"""Salon Desk local interface."""
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from uuid import uuid4

import streamlit as st
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agents import LocalModel
from store import SalonStore, today
from workflow import build, initial


ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".salon-data"
DATA.mkdir(exist_ok=True)
store = SalonStore(DATA / "salon.sqlite")
model = LocalModel()

st.set_page_config(page_title="Salon Desk", page_icon="✂", layout="centered")
st.markdown("""
<style>
  .block-container {max-width: 900px; padding-top: 4rem;}
  h1 {letter-spacing: -0.04em;}
  div[data-testid="stMetric"] {background: #edf2ea; border-radius: 12px; padding: 14px;}
  button[kind="primary"] {border-radius: 9px;}
</style>
""", unsafe_allow_html=True)
st.caption("SALON DESK · YOUR APPOINTMENT CONCIERGE")
st.title("Book your salon appointment")
st.write("Six AI specialists work together to understand your request, find an appointment, recover from problems, "
         "and prepare your confirmation.")
st.caption("Sample salon services and schedules · Real local AI and database operations · Times shown in Pacific time")

book_tab, calendar_tab, activity_tab, project_tab = st.tabs([
    "Book an appointment", "Appointments", "Agent activity", "About the project",
])

with st.sidebar:
    st.subheader("Salon menu")
    for service in store.catalog()["services"]:
        st.write(f"**{service['name']}**")
        st.caption(f"{service['minutes']} minutes · USD {service['cents'] / 100:.2f}")
    with st.expander("Demonstrate recovery"):
        st.caption("These controls inject labeled failures into the local sample calendar.")
        calendar_fault = st.checkbox("Calendar lookup fails once")
        conflict_fault = st.checkbox("Slot is taken after approval")
        response_fault = st.checkbox("Booking response is interrupted")

with book_tab:
    with st.form("new_request"):
        customer = st.text_input("Your name", placeholder="Pooja", max_chars=80)
        selected_day = st.date_input("Appointment date", value=today() + timedelta(days=1),
                                     min_value=today(), max_value=today() + timedelta(days=30))
        note = st.text_area("How can we help?",
                            "I'd like a haircut at 2 PM, preferably with Maya.", max_chars=700)
        submitted = st.form_submit_button("Find my appointment", type="primary")
    st.caption("A booking is created only after you confirm the exact service, stylist, time, and price.")

run_id = st.query_params.get("request", "")
config = {"configurable": {"thread_id": run_id}, "recursion_limit": 60}


def advance(graph, value, configuration):
    with book_tab, st.status("Your salon team is working…", expanded=True) as progress:
        for update in graph.stream(value, configuration, stream_mode="updates"):
            node = next(iter(update))
            labels = {
                "intake": "Intake agent understood your request",
                "match_service": "Service matching agent checked price, duration and qualified stylists",
                "calendar": "Calendar tool checked availability",
                "schedule": "Scheduling agent selected an appointment",
                "review": "Review agent checked the details",
                "book": "Booking tool checked and saved the result",
                "recover": "Recovery agent selected the next safe action",
                "recover_booking": "Booking tool checked whether the appointment was already saved",
                "confirm": "Confirmation agent checked the saved receipt",
            }
            if node in labels:
                st.write(labels[node])
        progress.update(label="Ready. Review the result below.", state="complete", expanded=False)


def proposal_details(proposal):
    start = datetime.fromisoformat(proposal["start"])
    end = datetime.fromisoformat(proposal["end"])
    st.subheader(f"{proposal['service']} with {proposal['stylist']}")
    left, middle, right = st.columns(3)
    left.metric("Date", start.strftime("%b %d"))
    middle.metric("Time", start.strftime("%I:%M %p").lstrip("0"))
    right.metric("Price", f"USD {proposal['cents'] / 100:.2f}")
    st.caption(f"{start.strftime('%A, %B %d, %Y')} · {start.strftime('%I:%M %p')} to "
               f"{end.strftime('%I:%M %p')} Pacific · {proposal['minutes']} minutes")


with SqliteSaver.from_conn_string(str(DATA / "workflow.sqlite")) as saver:
    graph = build(store, model, saver)
    if submitted:
        if not customer.strip() or not note.strip():
            with book_tab:
                st.error("Enter your name and the service you would like.")
        else:
            run_id = uuid4().hex
            st.query_params["request"] = run_id
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 60}
            advance(graph, initial(run_id, customer.strip(), selected_day.isoformat(), note.strip(),
                                   calendar_fault, conflict_fault, response_fault), config)

    state = {}
    pending = None
    if re.fullmatch(r"[0-9a-f]{32}", run_id):
        snapshot = graph.get_state(config)
        state = snapshot.values
        for task in snapshot.tasks:
            for pause in task.interrupts:
                pending = pause.value
    with book_tab:
        if pending and pending["kind"] == "approval":
            if any(event["action"] == "Fresh availability and approval required" for event in state["events"]):
                st.warning("The previous appointment became unavailable. Here is a new option. "
                           "Please confirm these updated details.")
            st.caption("PROPOSED APPOINTMENT · WAITING FOR YOUR APPROVAL")
            proposal_details(state["proposal"])
            st.write(state["summary"])
            if state["intake"].get("requested_time") and state["proposal"]["start"][11:16] != state["intake"]["requested_time"]:
                st.info(f"You requested {state['intake']['requested_time']}. This is an alternative time.")
            confirm_column, decline_column = st.columns(2)
            confirm = confirm_column.button("Confirm this appointment", type="primary")
            decline = decline_column.button("Decline appointment")
            if confirm or decline:
                advance(graph, Command(resume={
                    "action": "approve" if confirm else "cancel",
                    "proposal_id": state["proposal"]["id"],
                }), config)
                st.rerun()
        elif pending and pending["kind"] == "clarification":
            st.info(pending["message"])
            with st.form("clarification"):
                answer = st.text_input("Tell us a little more", max_chars=400)
                answered = st.form_submit_button("Continue")
            if answered and answer.strip():
                advance(graph, Command(resume={"text": answer.strip()}), config)
                st.rerun()
        elif pending and pending["kind"] == "help":
            st.warning(pending["message"])
            retry_column, stop_column = st.columns(2)
            retry = retry_column.button("Try again")
            stop = stop_column.button("Stop this request")
            if retry or stop:
                advance(graph, Command(resume={"action": "retry" if retry else "cancel"}), config)
                st.rerun()

        if (pending and pending["kind"] in ("approval", "help")
                and (pending["kind"] == "approval"
                     or state.get("retry_target") not in ("book", "recover_booking", "confirm"))):
            with st.expander("Change my request"):
                with st.form("revision"):
                    revised_day = st.date_input("New appointment date", value=max(today(), date.fromisoformat(state["day"])),
                                               min_value=today(), max_value=today() + timedelta(days=30))
                    revision = st.text_input("What would you like to change?", placeholder="Try a morning appointment instead.",
                                             max_chars=400)
                    revise = st.form_submit_button("Find a different appointment")
                if revise and revision.strip():
                    advance(graph, Command(resume={"action": "revise", "text": revision.strip(),
                                                   "day": revised_day.isoformat()}), config)
                    st.rerun()

        if state.get("status") == "booked":
            booking = state["booking"]
            catalog = store.catalog()
            service = next(row for row in catalog["services"] if row["id"] == booking["service_id"])
            stylist = next(row for row in catalog["stylists"] if row["id"] == booking["stylist_id"])
            st.success(f"You're booked, {booking['customer']}!")
            if (state.get("confirmation") or {}).get("message"):
                st.write(state["confirmation"]["message"])
            proposal_details({
                **booking, "service": service["name"], "stylist": stylist["name"],
                "minutes": int((datetime.fromisoformat(booking["end"]) - datetime.fromisoformat(booking["start"])).total_seconds() / 60),
            })
            st.caption(f"Booking reference {booking['id'][:10]} · Saved in this sample salon on your computer")
            receipt = {
                "booking_reference": booking["id"], "customer": booking["customer"], "service": service["name"],
                "stylist": stylist["name"], "start_pacific": booking["start"], "end_pacific": booking["end"],
                "price_usd": booking["cents"] / 100, "sample_salon": True,
            }
            st.download_button("Download confirmation", json.dumps(receipt, indent=2),
                               "salon-booking-confirmation.json", "application/json")
        elif state.get("status") == "cancelled":
            st.info("This request is closed.")

    with calendar_tab:
        st.subheader("Appointments saved on this computer")
        appointments = store.appointments()
        if not appointments:
            st.info("Your confirmed appointments will appear here.")
        for booking in appointments:
            title = "Simulated competing reservation" if booking["simulated"] else booking["customer"]
            with st.container(border=True):
                st.write(f"**{title} · {booking['service']} with {booking['stylist']}**")
                st.caption(f"{booking['start'].replace('T', ' ')} Pacific · USD {booking['cents'] / 100:.2f}")
                st.caption(f"Reference {booking['id'][:10]}")

    with activity_tab:
        st.subheader("What the team did")
        st.caption("AI responses, tool calls, approvals and recovery events are stored with this request.")
        roles = [
            "Intake agent", "Service matching agent", "Scheduling agent",
            "Booking review agent", "Recovery agent", "Confirmation agent",
        ]
        completed = {event["actor"] for event in state.get("events", []) if event["action"] == "AI response"}
        model_calls = [event for event in state.get("events", []) if event["action"] == "AI response"]
        team_column, calls_column, recovery_column = st.columns(3)
        team_column.metric("AI specialists", 6)
        calls_column.metric("Successful AI calls", len(model_calls))
        recovery_column.metric("Recovery decisions", sum(event["actor"] == "Recovery agent" for event in model_calls))
        for role in roles:
            status = "Responded" if role in completed else "On standby"
            st.write(f"**{role}** · {status}")
        st.caption("The recovery agent is called when a problem occurs. Completed historical requests may show fewer roles.")
        if not state:
            st.info("Start an appointment request to see the agents work.")
        for event in state.get("events", []):
            with st.expander(f"{event['actor']} · {event['action']}"):
                st.caption(event["time"])
                st.code(json.dumps(event["details"], indent=2), language="json")
        if state:
            st.download_button("Download activity log", json.dumps(state["events"], indent=2),
                               "salon-agent-activity.json", "application/json")

with project_tab:
    st.subheader("Six specialists coordinate the booking")
    st.write("Salon staff coordinate service duration, stylist skills and changing availability. "
             "Salon Desk assigns six AI specialists to turn a customer request into a confirmed appointment.")
    st.markdown("""
1. **Intake agent** understands the customer's preferences and asks for missing information.
2. **Service matching agent** checks the catalog, service duration, price and qualified stylists.
3. **Scheduling agent** chooses from actual calendar openings.
4. **Booking review agent** checks the proposed details before asking the customer.
5. **Recovery agent** chooses a retry, a fresh plan, clarification or a human handoff.
6. **Confirmation agent** reads the saved booking and prepares a checked receipt.

**LangGraph orchestrator** coordinates the six agents, tools, persistent state and customer approval.
""")
    st.write("The local Ollama model powers every specialist. SQLite checks for overlaps in a transaction "
             "and reuses the existing booking when the same request is retried.")
    st.caption("This project uses sample salon data and creates local bookings. It does not contact a real salon.")
