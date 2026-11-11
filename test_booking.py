"""Regression tests for approval, concurrency, durable state and recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agents import ModelUnavailable
from store import BookingConflict, DetailsChanged, SalonStore, today
from workflow import build, initial


class TestModel:
    def ask(self, role, context, response_type):
        if role == "Intake agent":
            result = {
                "service_id": "haircut", "stylist_id": "maya", "period": "afternoon",
                "requested_time": "14:00", "needs_clarification": False, "question": "",
            }
        elif role == "Scheduling agent":
            result = {"slot_index": 0, "reason": "Closest available option with the preferred stylist."}
        elif role == "Service matching agent":
            result = {
                "service_id": "haircut", "stylist_ids": ["maya", "lily"],
                "duration_minutes": 45, "price_cents": 4000,
                "needs_clarification": False, "question": "",
            }
            service = next(row for row in context["catalog"]["services"] if row["id"] == "haircut")
            result["price_cents"] = service["cents"]
        elif role == "Recovery agent":
            result = {"action": context["allowed_actions"][0], "reason": "Use the safe allowed recovery action."}
        elif role == "Confirmation agent":
            result = {**context["saved_booking"], "message": "Your appointment is confirmed."}
        else:
            result = {"ready_for_approval": True, "explanation": "Please confirm these appointment details."}
        return result, {"model": "test fixture", "seconds": 0, "input_tokens": 0, "output_tokens": 0}


class UnavailableModel:
    def ask(self, *args):
        raise ModelUnavailable("Simulated local model outage")


class ConfirmationUnavailableModel(TestModel):
    def ask(self, role, *args):
        if role == "Confirmation agent":
            raise ModelUnavailable("Confirmation wording unavailable")
        return super().ask(role, *args)


class InvalidPriceModel(TestModel):
    def ask(self, role, *args):
        result, usage = super().ask(role, *args)
        if role == "Service matching agent":
            result["price_cents"] = 1
        return result, usage


class CorrectedStylistModel(TestModel):
    def ask(self, role, context, response_type):
        result, usage = super().ask(role, context, response_type)
        if role == "Service matching agent" and "validation_feedback" not in context:
            result["stylist_ids"].append("zoe")
        return result, usage


class WorseSlotModel(TestModel):
    def ask(self, role, *args):
        result, usage = super().ask(role, *args)
        if role == "Scheduling agent":
            result["slot_index"] = 1
        return result, usage


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SalonStore(Path(self.folder.name) / "salon.sqlite")
        self.workflow_path = str(Path(self.folder.name) / "workflow.sqlite")
        self.config = {"configurable": {"thread_id": "test-request"}, "recursion_limit": 60}
        self.day = (today() + timedelta(days=1)).isoformat()

    def run_graph(self, value, model=None):
        with SqliteSaver.from_conn_string(self.workflow_path) as saver:
            graph = build(self.store, model or TestModel(), saver)
            result = graph.invoke(value, self.config)
            return result, graph.get_state(self.config).next

    def start(self, **faults):
        return self.run_graph(initial("test-request", "Alex", self.day, "Haircut at 2 PM with Maya", **faults))

    def approve(self, state):
        return self.run_graph(Command(resume={"action": "approve", "proposal_id": state["proposal"]["id"]}))

    def test_approval_is_required_and_checkpoint_survives_reopen(self):
        state, pending = self.start()
        self.assertEqual(pending, ("approval",))
        self.assertEqual(self.store.appointments(), [])
        approved, pending = self.approve(state)
        self.assertEqual(approved["status"], "booked")
        self.assertEqual(pending, ())
        self.assertEqual(len(self.store.appointments()), 1)

    def test_conflict_requires_fresh_approval_and_response_retry_is_idempotent(self):
        state, _ = self.start(calendar=True, conflict=True, response=True)
        original_id = state["proposal"]["id"]
        alternative, pending = self.approve(state)
        self.assertEqual(pending, ("approval",))
        self.assertNotEqual(alternative["proposal"]["id"], original_id)
        self.assertEqual(alternative["approval"], "")
        self.assertNotEqual(alternative["proposal"]["start"], state["proposal"]["start"])
        self.assertFalse(any(not row["simulated"] for row in self.store.appointments()))
        booked, _ = self.approve(alternative)
        self.assertEqual(booked["status"], "booked")
        self.assertEqual(sum(not row["simulated"] for row in self.store.appointments()), 1)
        self.assertTrue(any(e["action"] == "Recovered existing booking" for e in booked["events"]))

    def test_old_approval_cannot_book_new_proposal(self):
        state, _ = self.start(conflict=True)
        alternative, _ = self.approve(state)
        result, pending = self.run_graph(Command(resume={
            "action": "approve", "proposal_id": state["proposal"]["id"],
        }))
        self.assertEqual(pending, ("help",))
        self.assertFalse(any(not row["simulated"] for row in self.store.appointments()))

    def test_decline_creates_no_booking(self):
        self.start()
        result, pending = self.run_graph(Command(resume={"action": "cancel"}))
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.store.appointments(), [])

    def test_model_failure_hands_off_without_booking(self):
        result, pending = self.run_graph(initial("test-request", "Alex", self.day, "Haircut"), UnavailableModel())
        self.assertEqual(pending, ("help",))
        self.assertEqual(self.store.appointments(), [])
        self.assertEqual(sum(e["action"] == "AI call failed" for e in result["events"]), 2)

    def test_price_change_requires_reapproval(self):
        state, _ = self.start()
        with self.store.connect() as db:
            db.execute("UPDATE services SET cents=4500 WHERE id='haircut'")
        result, pending = self.approve(state)
        self.assertEqual(pending, ("approval",))
        self.assertEqual(result["proposal"]["cents"], 4500)
        self.assertEqual(result["approval"], "")
        self.assertEqual(self.store.appointments(), [])

    def test_concurrent_customers_cannot_double_book(self):
        state, _ = self.start()
        proposal = state["proposal"]
        def reserve(request_id):
            try:
                return self.store.book(request_id, request_id, proposal, proposal["id"])[0]["id"]
            except BookingConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, ["customer-a", "customer-b"]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.store.appointments()), 1)

    def test_retry_after_commit_returns_same_booking(self):
        state, _ = self.start()
        proposal = state["proposal"]
        original, _ = self.store.book("retry", "Alex", proposal, proposal["id"])
        recovered, reused = self.store.book("retry", "Alex", proposal, proposal["id"])
        self.assertEqual(original["id"], recovered["id"])
        self.assertTrue(reused)
        self.assertEqual(len(self.store.appointments()), 1)

    def test_six_roles_participate_in_recovery_workflow(self):
        state, _ = self.start(calendar=True, conflict=True, response=True)
        alternative, _ = self.approve(state)
        booked, _ = self.approve(alternative)
        roles = {e["actor"] for e in booked["events"] if e["action"] == "AI response"}
        self.assertEqual(roles, {
            "Intake agent", "Service matching agent", "Scheduling agent",
            "Booking review agent", "Recovery agent", "Confirmation agent",
        })
        self.assertEqual(booked["confirmation"]["booking_reference"], booked["booking"]["id"])

    def test_service_agent_cannot_invent_price(self):
        result, pending = self.run_graph(
            initial("test-request", "Alex", self.day, "Haircut"), InvalidPriceModel(),
        )
        self.assertEqual(pending, ("help",))
        self.assertEqual(self.store.appointments(), [])

    def test_service_agent_repairs_an_unqualified_stylist(self):
        result, pending = self.run_graph(
            initial("test-request", "Alex", self.day, "Haircut"), CorrectedStylistModel(),
        )
        self.assertEqual(pending, ("approval",))
        self.assertEqual(set(result["service_match"]["stylist_ids"]), {"maya", "lily"})
        self.assertTrue(any(e["action"] == "Rejected inconsistent service match" for e in result["events"]))
        self.assertEqual(self.store.appointments(), [])

    def test_requested_slot_is_not_displaced_by_a_worse_ai_choice(self):
        result, pending = self.run_graph(
            initial("test-request", "Alex", self.day, "Haircut at 2 PM with Maya"), WorseSlotModel(),
        )
        self.assertEqual(pending, ("approval",))
        self.assertEqual(result["proposal"]["start"][11:16], "14:00")
        self.assertEqual(result["proposal"]["stylist_id"], "maya")
        self.assertTrue(any(e["actor"] == "Scheduling validator" for e in result["events"]))

    def test_confirmation_failure_preserves_committed_booking(self):
        state, _ = self.start()
        result, pending = self.run_graph(Command(resume={
            "action": "approve", "proposal_id": state["proposal"]["id"],
        }), ConfirmationUnavailableModel())
        self.assertEqual(result["status"], "booked")
        self.assertEqual(pending, ())
        self.assertEqual(len(self.store.appointments()), 1)
        self.assertEqual(result["confirmation"]["booking_reference"], result["booking"]["id"])

    def test_old_approval_checkpoint_resumes_without_new_state_fields(self):
        state, _ = self.start()
        with SqliteSaver.from_conn_string(self.workflow_path) as saver:
            graph = build(self.store, TestModel(), saver)
            # Existing checkpoints may have None for newly introduced channels.
            graph.update_state(self.config, {"recovery_counts": None, "booking_attempts": None})
        result, pending = self.approve(state)
        self.assertEqual(result["status"], "booked")
        self.assertEqual(pending, ())


if __name__ == "__main__":
    unittest.main()
