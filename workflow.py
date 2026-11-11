"""LangGraph orchestration with durable review and transactional calendar tools."""
from copy import deepcopy
from datetime import datetime
import sqlite3
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents import (
    ConfirmationResult, IntakeResult, ModelUnavailable, RecoveryResult,
    ReviewResult, ScheduleResult, ServiceMatchResult,
)
from store import BookingConflict, DetailsChanged, TIMEZONE


class State(TypedDict, total=False):
    request_id: str
    customer: str
    day: str
    note: str
    intake: dict
    candidates: list
    proposal: dict
    approval: str
    booking: dict
    events: list
    status: str
    error: str
    retry_target: str
    calendar_attempts: int
    fault_calendar: bool
    fault_conflict: bool
    fault_response: bool
    summary: str
    service_match: dict
    recovery_kind: str
    recovery_counts: dict
    booking_attempts: int
    confirmation: dict
    clarification_question: str


def initial(request_id, customer, day, note, calendar=False, conflict=False, response=False):
    return {
        "request_id": request_id, "customer": customer, "day": day, "note": note,
        "intake": {}, "candidates": [], "proposal": {}, "approval": "", "booking": {},
        "events": [], "status": "working", "error": "", "retry_target": "",
        "calendar_attempts": 0, "fault_calendar": calendar, "fault_conflict": conflict,
        "fault_response": response, "summary": "", "service_match": {},
        "recovery_kind": "", "recovery_counts": {}, "booking_attempts": 0,
        "confirmation": {}, "clarification_question": "",
    }


def add_event(state, actor, action, details):
    state["events"].append({
        "time": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "actor": actor, "action": action, "details": details,
    })


def request_recovery(state, kind, message):
    state.update(recovery_kind=kind, error=message, status="recovering")


def ask(state, model, role, context, response_type, retry_target):
    for attempt in range(2):
        try:
            answer, usage = model.ask(role, context, response_type)
            add_event(state, role, "AI response", {"result": answer, **usage})
            return answer
        except ModelUnavailable as error:
            add_event(state, role, "AI call failed", {"attempt": attempt + 1, "message": str(error)})
    state.update(error="Local AI is unavailable after two attempts.",
                 retry_target=retry_target, status="needs_help")
    return None


def build(store, model, checkpointer):
    graph = StateGraph(State)

    def intake_agent(current):
        state = deepcopy(current)
        state["error"] = ""
        state["clarification_question"] = ""
        catalog = store.catalog()
        add_event(state, "Intake agent", "Read service catalog", catalog)
        result = ask(state, model, "Intake agent", {
            "request": state["note"], "selected_date": state["day"], "catalog": catalog,
        }, IntakeResult, "intake")
        if result:
            state["intake"] = result
            state["status"] = "clarify" if result["needs_clarification"] or result["service_id"] == "unknown" else "working"
        return state

    def service_matching_agent(current):
        state = deepcopy(current)
        catalog = store.catalog()
        options = [
            {**service, "eligible_stylist_ids": [
                stylist["id"] for stylist in catalog["stylists"]
                if service["id"] in stylist["services"].split(",")
            ]} for service in catalog["services"]
        ]
        context = {
            "customer_request": state["note"], "preferences": state["intake"],
            "catalog": {"services": catalog["services"]}, "service_options": options,
        }
        for attempt in range(2):
            result = ask(state, model, "Service matching agent", context, ServiceMatchResult, "match_service")
            if not result:
                return state
            if result["needs_clarification"] or result["service_id"] == "unknown":
                state.update(status="clarify", clarification_question=result["question"])
                return state
            option = next((row for row in options if row["id"] == result["service_id"]), None)
            if (option and result["price_cents"] == option["cents"]
                    and result["duration_minutes"] == option["minutes"]
                    and set(result["stylist_ids"]) == set(option["eligible_stylist_ids"])):
                state["service_match"] = result
                state["intake"]["service_id"] = result["service_id"]
                state["status"] = "working"
                add_event(state, "Catalog tool", "Validated service, price and stylist skills", result)
                return state
            add_event(state, "Catalog tool", "Rejected inconsistent service match", {"attempt": attempt + 1})
            context["validation_feedback"] = {
                "message": "The previous output was invalid. Copy duration, cents and eligible stylist IDs exactly.",
                "previous_output": result, "authoritative_options": options,
            }
        state.update(status="needs_help", error="The service match did not agree with the salon catalog.",
                     retry_target="match_service")
        return state

    def clarification(current):
        state = deepcopy(current)
        answer = interrupt({
            "kind": "clarification",
            "message": state.get("clarification_question") or state["intake"].get("question")
                       or "Would you like a haircut, blow-dry or hair coloring?",
        })
        if answer.get("action") == "cancel":
            return Command(update={"status": "cancelled"}, goto=END)
        return Command(update={"note": state["note"] + "\nClarification: " + str(answer.get("text", "")),
                               "status": "working"}, goto="intake")

    def calendar_tool(current):
        state = deepcopy(current)
        state["status"] = "working"
        state["approval"] = ""
        state["calendar_attempts"] += 1
        if state["fault_calendar"]:
            state["fault_calendar"] = False
            request_recovery(state, "calendar", "A temporary calendar lookup failed.")
            add_event(state, "Calendar tool", "Temporary failure",
                      {"simulated": True, "next": "Retry availability lookup"})
            return state
        try:
            state["candidates"] = store.availability(state["day"], state["intake"])
        except (OSError, sqlite3.OperationalError):
            request_recovery(state, "calendar", "The calendar is temporarily unavailable.")
            add_event(state, "Calendar tool", "Calendar unavailable", {"attempt": state["calendar_attempts"]})
            return state
        add_event(state, "Calendar tool", "Read available appointments", {
            "count": len(state["candidates"]), "selected_date": state["day"],
        })
        if not state["candidates"]:
            request_recovery(state, "no_slots", "No matching slots remain. Change the date or time preference.")
        return state

    def scheduling_agent(current):
        state = deepcopy(current)
        result = ask(state, model, "Scheduling agent", {
            "preferences": state["intake"], "available_slots": [
                {"slot_index": index, **slot} for index, slot in enumerate(state["candidates"][:3])
            ],
        }, ScheduleResult, "schedule")
        if not result:
            return state
        index = result["slot_index"]
        if not 0 <= index < len(state["candidates"]):
            state.update(status="needs_help", error="The scheduling agent chose an invalid slot.", retry_target="schedule")
            return state
        if index != 0:
            add_event(state, "Scheduling validator", "Used best-ranked available slot",
                      {"ai_slot_index": index, "selected_slot_index": 0,
                       "reason": "Prefer the requested stylist, closest time and earliest opening"})
            index = 0
            result["reason"] = "Best available match under the customer's stated preferences."
        state["proposal"] = {**state["candidates"][index], "id": uuid4().hex, "reason": result["reason"]}
        return state

    def review_agent(current):
        state = deepcopy(current)
        proposal = state["proposal"]
        record = {key: proposal[key] for key in state["candidates"][0]}
        if record not in state["candidates"]:
            state.update(status="needs_help", error="The proposal does not match an available slot.", retry_target="calendar")
            return state
        catalog = store.catalog()
        result = ask(state, model, "Booking review agent", {
            "proposal": record, "calendar_verified": True,
            "catalog_service": next(row for row in catalog["services"] if row["id"] == proposal["service_id"]),
            "catalog_stylist": next(row for row in catalog["stylists"] if row["id"] == proposal["stylist_id"]),
            "requested_time": state["intake"].get("requested_time", ""),
        }, ReviewResult, "review")
        if result:
            state["summary"] = result["explanation"]
            state["status"] = "awaiting_approval" if result["ready_for_approval"] else "needs_help"
            if not result["ready_for_approval"]:
                state.update(error=result["explanation"], retry_target="calendar")
        return state

    def approval(current):
        state = deepcopy(current)
        decision = interrupt({"kind": "approval", "proposal": state["proposal"], "message": state["summary"]})
        if decision.get("action") == "cancel":
            add_event(state, "Customer", "Declined proposal", {"proposal_id": state["proposal"]["id"]})
            state["status"] = "cancelled"
            return Command(update=state, goto=END)
        if decision.get("action") == "revise":
            state.update(note=state["note"] + "\nUpdated request: " + str(decision.get("text", "")),
                         day=str(decision.get("day", state["day"])), proposal={}, approval="", status="working",
                         recovery_counts={})
            add_event(state, "Customer", "Requested a change", {"note": decision.get("text", "")})
            return Command(update=state, goto="intake")
        if decision.get("action") != "approve" or decision.get("proposal_id") != state["proposal"]["id"]:
            state.update(status="needs_help", error="That approval belongs to an older proposal. Review the latest details.",
                         retry_target="review", approval="")
            return Command(update=state, goto="help")
        state["approval"] = state["proposal"]["id"]
        add_event(state, "Customer", "Approved exact proposal", {"proposal_id": state["approval"]})
        return Command(update=state, goto="book")

    def booking_tool(current):
        state = deepcopy(current)
        proposal = state["proposal"]
        state["booking_attempts"] = (state.get("booking_attempts") or 0) + 1
        try:
            if state["fault_conflict"]:
                state["fault_conflict"] = False
                store.book("competing:" + state["request_id"], "Another customer (simulated)", proposal,
                           proposal["id"], simulated=True)
                add_event(state, "Calendar tool", "Simulated competing reservation",
                          {"proposal_id": proposal["id"], "time": proposal["start"]})
            booking, recovered = store.book(state["request_id"], state["customer"], proposal, state["approval"])
            if state["fault_response"]:
                state["fault_response"] = False
                add_event(state, "Booking tool", "Simulated response interruption",
                          {"committed_booking_id": booking["id"], "next": "Retry with same request ID"})
                request_recovery(state, "booking_response", "The booking response was interrupted after the write.")
                return state
            state.update(booking=booking, status="booked")
            add_event(state, "Booking tool", "Recovered existing booking" if recovered else "Appointment saved",
                      {"booking_id": booking["id"], "duplicate_created": False})
        except (BookingConflict, DetailsChanged) as error:
            state.update(approval="", proposal={}, calendar_attempts=0, booking_attempts=0)
            request_recovery(state, "conflict", str(error))
            add_event(state, "Orchestrator", "Fresh availability and approval required", {"reason": str(error)})
        except sqlite3.OperationalError:
            request_recovery(state, "booking_database", "The booking database is busy. Check the existing request first.")
        return state

    def recovery_agent(current):
        state = deepcopy(current)
        kind = state.get("recovery_kind", "")
        counts = dict(state.get("recovery_counts") or {})
        state["recovery_counts"] = counts
        counts[kind] = counts.get(kind, 0) + 1
        if kind in ("booking_response", "booking_database"):
            allowed = ["recover_booking"]
        elif counts[kind] > 3 or (kind == "calendar" and state["calendar_attempts"] >= 3):
            allowed = ["escalate"]
        elif kind == "calendar":
            allowed = ["retry_calendar", "escalate"]
        elif kind == "conflict":
            allowed = ["replan", "clarify", "escalate"]
        else:
            allowed = ["clarify", "escalate"]
        result = ask(state, model, "Recovery agent", {
            "failure": kind, "message": state["error"], "allowed_actions": allowed,
            "attempt": counts[kind], "calendar_attempts": state["calendar_attempts"],
            "preferences": state["intake"], "approval_cleared": not state["approval"],
        }, RecoveryResult, "recover")
        if kind in ("booking_response", "booking_database"):
            # A committed write must be resolved even if AI is unavailable.
            if not result:
                add_event(state, "Orchestrator", "Resolve booking before human handoff",
                          {"request_id": state["request_id"]})
            state["status"] = "working"
            return Command(update=state, goto="recover_booking")
        if not result:
            return Command(update=state, goto="help")
        action = result["action"] if result["action"] in allowed else "escalate"
        if action in ("retry_calendar", "replan"):
            state.update(status="working", error="")
            if action == "replan":
                state.update(approval="", proposal={}, candidates=[], calendar_attempts=0)
            return Command(update=state, goto="calendar")
        state.update(status="needs_help", error=result["reason"] or state["error"],
                     retry_target="intake" if action == "clarify" else "recover")
        return Command(update=state, goto="help")

    def recover_booking_tool(current):
        state = deepcopy(current)
        try:
            booking = store.find_booking(state["request_id"])
        except sqlite3.OperationalError:
            state.update(status="needs_help", error="The saved booking could not be checked. Retry before making changes.",
                         retry_target="recover_booking")
            return state
        if booking:
            state.update(booking=booking, status="booked", error="")
            add_event(state, "Booking tool", "Recovered existing booking",
                      {"booking_id": booking["id"], "duplicate_created": False})
        elif (state.get("booking_attempts") or 0) < 2 and state["approval"] == state["proposal"].get("id"):
            state["status"] = "retry_booking"
            add_event(state, "Booking tool", "No committed booking found", {"next": "Retry the same approved request"})
        else:
            state.update(status="needs_help", error="No saved booking was found. Retry the approved request.",
                         retry_target="book")
        return state

    def confirmation_agent(current):
        state = deepcopy(current)
        try:
            booking = store.find_booking(state["request_id"])
        except sqlite3.OperationalError:
            booking = state.get("booking")
        if not booking:
            state.update(status="needs_help", error="A saved booking is needed before preparing confirmation.",
                         retry_target="confirm")
            return state
        state["booking"] = booking
        receipt = {
            "booking_reference": booking["id"], "service_id": booking["service_id"],
            "stylist_id": booking["stylist_id"], "start": booking["start"],
            "end": booking["end"], "price_cents": booking["cents"],
        }
        add_event(state, "Confirmation agent", "Read saved booking", {"booking_id": booking["id"]})
        result = ask(state, model, "Confirmation agent", {"saved_booking": receipt}, ConfirmationResult, "confirm")
        state["confirmation"] = {**receipt, "message": ""}
        if result and all(result[key] == value for key, value in receipt.items()):
            state["confirmation"]["message"] = result["message"]
            add_event(state, "Receipt validator", "Confirmed receipt matches saved booking", receipt)
        else:
            add_event(state, "Receipt validator", "Used authoritative saved receipt",
                      {"booking_id": booking["id"], "reason": "AI confirmation unavailable or inconsistent"})
        # Booking success remains true even if confirmation wording fails.
        state.update(status="booked", error="")
        return state

    def help_node(current):
        state = deepcopy(current)
        decision = interrupt({"kind": "help", "message": state["error"]})
        if decision.get("action") == "retry":
            target = state["retry_target"]
            state.update(error="", status="working", calendar_attempts=0)
            return Command(update=state, goto=target)
        if decision.get("action") == "revise":
            if state["retry_target"] in ("book", "recover_booking", "confirm"):
                state.update(error="Resolve the previous booking attempt before changing the request.")
                return Command(update=state, goto="help")
            state.update(note=state["note"] + "\nUpdated request: " + str(decision.get("text", "")),
                         day=str(decision.get("day", state["day"])), status="working", approval="", proposal={},
                         recovery_counts={})
            return Command(update=state, goto="intake")
        return Command(update={"status": "cancelled"}, goto=END)

    graph.add_node("intake", intake_agent)
    graph.add_node("match_service", service_matching_agent)
    graph.add_node("clarify", clarification)
    graph.add_node("calendar", calendar_tool)
    graph.add_node("schedule", scheduling_agent)
    graph.add_node("review", review_agent)
    graph.add_node("approval", approval)
    graph.add_node("book", booking_tool)
    graph.add_node("recover", recovery_agent)
    graph.add_node("recover_booking", recover_booking_tool)
    graph.add_node("confirm", confirmation_agent)
    graph.add_node("help", help_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: "help" if s["status"] == "needs_help" else
                                "clarify" if s["status"] == "clarify" else "match_service", ["help", "clarify", "match_service"])
    graph.add_conditional_edges("match_service", lambda s: "help" if s["status"] == "needs_help" else
                                "clarify" if s["status"] == "clarify" else "calendar", ["help", "clarify", "calendar"])
    graph.add_conditional_edges("calendar", lambda s: "recover" if s["status"] == "recovering" else "schedule",
                                ["recover", "schedule"])
    graph.add_conditional_edges("schedule", lambda s: "help" if s["status"] == "needs_help" else "review", ["help", "review"])
    graph.add_conditional_edges("review", lambda s: "help" if s["status"] == "needs_help" else "approval", ["help", "approval"])
    graph.add_conditional_edges("book", lambda s: "confirm" if s["status"] == "booked" else "recover",
                                ["confirm", "recover"])
    graph.add_conditional_edges("recover_booking", lambda s: "confirm" if s["status"] == "booked" else
                                "book" if s["status"] == "retry_booking" else "help", ["confirm", "book", "help"])
    graph.add_conditional_edges("confirm", lambda s: END if s["status"] == "booked" else "help", [END, "help"])
    return graph.compile(checkpointer=checkpointer)
