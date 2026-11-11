# Salon Desk project documentation

Week 3 Build Your AI Agent submission

## Overview and problem

Salon staff coordinate customer preferences, service durations and stylist availability through repeated conversations. Availability can change while a customer is deciding, and an interrupted booking response can leave staff unsure whether an appointment was saved.

Salon Desk turns a customer request into an appointment proposal, asks the customer to approve its exact details and creates the booking. If the slot becomes unavailable, it finds another option and asks for approval again. If a booking commits but its response is interrupted, it recovers the same booking.

This is an original custom use case under the handout's bring your own option. The implementation uses Python, Streamlit, LangGraph, SQLite and a local Ollama model.

## Agent one liner

My agent helps salon customers arrange an appointment in a web app, replacing the manual exchange of preferences and availability with six AI specialists and catalog, calendar, booking and receipt tools. It interprets a service request, matches it to the catalog, finds openings, reviews a proposal and handles recovery autonomously, pauses for customer approval before creating a booking and checks the saved receipt afterward. The evaluation target is a confirmed appointment in under three minutes on eight of ten representative ordinary requests.

The completion rate and three-minute threshold are evaluation targets. The ten-request evaluation remains pending. Recovery scenarios are assessed separately for correct approval and database behavior.

## Agent framework

| Field | Implementation |
| --- | --- |
| Goal | Turn a service request into a customer-approved appointment. |
| Surface | Local Streamlit web app on port 8502. |
| Steps | Interpret preferences, read the calendar, choose an opening, review details, request approval and save the booking. A conflict routes back to availability and a new approval. |
| Tools | Read the service and stylist catalog, read available appointments, create an approved booking and recover a saved booking by request ID. The first two read data. Booking creation writes to SQLite. |
| Memory | LangGraph SQLite checkpoints store the request, parsed preferences, candidate slots, current proposal, approval, events and outcome. Calendar records live in a separate SQLite database. Both persist across app restarts on the same computer. |
| Limits | The app creates local sample salon appointments. It does not collect payment details, send customer messages or contact a real salon. |
| Human review | The customer confirms the exact service, stylist, date, time and price. Changes require another approval. The customer can decline or revise a proposal. |
| Failure handling | Calendar reads retry, slot conflicts replan, missing preferences trigger clarification and model outages hand off to a human. Committed bookings are recovered by request ID. |
| Success | The customer receives one confirmed, nonoverlapping booking matching the latest approved proposal. Failure scenarios must not silently book a changed slot or create a duplicate. |

## Specialized agents and orchestration

The intake agent reads the service catalog and extracts the requested service, stylist and time preference. It asks for clarification if the requested service is unsupported or missing.

The service matching agent selects the catalog service and qualified stylists. Code validates its duration, price and stylist list against the catalog. An inconsistent match receives explicit validation feedback and one correction attempt before human handoff.

The scheduling agent chooses from real openings returned by the calendar tool. The slots already satisfy the requested service, time window, working hours and existing reservations.

The booking review agent checks the proposal against the catalog and available-slot record, then prepares an explanation for the customer. Its recommendation does not replace customer approval.

The recovery agent selects a bounded retry, a fresh plan, a saved-booking lookup, clarification or escalation. Its permitted actions depend on the failure. A changed proposal always requires fresh customer approval. Uncertain write outcomes must be checked even if AI is unavailable.

The confirmation agent reads the saved booking and prepares structured confirmation fields. The code checks those fields against the saved record. If its wording fails or a field differs, the confirmed appointment remains valid and the authoritative receipt is used.

The LangGraph orchestrator holds state and routes the six agents, tools, clarification, retries, approval, conflicts and human handoffs. Each specialist has a separate prompt, context and JSON schema, while all share the local model llama3.2:3b. Normal bookings use five specialists. The recovery agent is activated by a failure.

## Dataset and tools

The sample salon catalog contains a 45-minute haircut at USD 40, a 30-minute wash and blow-dry at USD 30 and 90-minute hair coloring at USD 95. Three fictional stylists have distinct supported services. Openings are generated between 10 AM and 6 PM Pacific time, with appointment duration and overlap checks.

The schedules and prices are sample data. Local AI inference, checkpointing, approval processing and booking transactions execute in the app. The three failure controls explicitly inject a calendar lookup failure, a competing reservation or an interrupted booking response.

The model was already installed through Ollama on the development computer. The app needs no external API keys.

## Prompts and AI assisted development

The project evolved through a conversation with Codex. Relevant user instructions included asking for a salon use case with multiple AI agents, approval and failure handling, choosing the salon booking assistant and requesting implementation according to the Week 3 handout.

The intake prompt asks the model to produce structured preferences. The service matching prompt binds a choice to explicit catalog options. Scheduling selects an actual candidate slot, and review checks the proposed details. Recovery selects from allowed actions for the observed failure. Confirmation copies fields from the saved booking and drafts a short customer message.

The exact prompts are included in agents.py. Codex helped implement the graph, local model calls, SQLite transactions, interface and regression checks.

## Iterations and observations

The earlier competitor research direction was replaced with appointment booking after the owner requested a more concrete real-life workflow.

The first salon implementation separated intake, scheduling and review. A real local model run extracted a 2 PM haircut preference, chose Maya and reached pending customer approval.

Booking approval is tied to a proposal ID. The write tool rechecks price, duration, stylist eligibility and calendar overlap inside a transaction. This addresses the gap between an earlier availability lookup and the final booking.

Failure controls make recovery repeatable. A conflicting appointment produces an alternative with a new proposal ID. A repeated write uses the same request ID and returns the existing booking.

The visible Agent activity tab preserves AI outputs, model timing and token counts, tool results, approvals and recovery events. It provides evidence of the work performed during the demo.

## Verification and remaining submission steps

Fourteen regression checks passed. They cover approval and restart persistence, a combined calendar failure and booking conflict, interrupted-response recovery, rejection of stale approval, declined requests, model failure handoff, changed-price reapproval and concurrent booking integrity. Additional cases cover participation by all six roles, service-price validation, correction of an unqualified stylist, preservation of the best requested slot, confirmation failure after a committed booking and compatibility with older approval checkpoints.

The regression tests use a deterministic model fixture to test state and transactions. A separate end-to-end test used the actual local model and reached the approval step for a USD 40 haircut with Maya at 2 PM.

The original three-agent baseline completed all three injected failures in an isolated sample calendar, using five real AI calls and 128 seconds. That is historical baseline evidence. The current six-agent sample confirmation and activity files record the role list, call count and measured runtime for the expanded workflow.

The six-agent extended recovery run exercised every role, corrected an invalid catalog match, required fresh approval after a conflict and resolved an interrupted write response. It used 11 successful AI responses and took 198.8 seconds with automated test approvals. Exactly one customer booking and one labeled simulated competing reservation were stored in the isolated test calendar.

After limiting scheduling context to the best three candidates and review context to the relevant catalog records, the current conflict-only run completed in 121.84 seconds. It used nine successful AI responses across all six roles, proposed 2 PM, required fresh approval for 3 PM and saved one customer appointment. This measurement uses automated test approvals and excludes narration. It is a single observed run, not the pending ten-request evaluation.

Local-model wording and choices can be imperfect. The service matcher is checked against explicit qualified-stylist lists and receives one repair attempt. The scheduling validator preserves the best-ranked opening under the stated preference policy. Receipt fields are checked against the saved record. These checks keep booking facts grounded in the tools.

Add the final browser walkthrough observations, measured completion time, GitHub repository link and video link before submission. Copy this document into Google Docs and share it with the access requested by the course. Record the working app in five minutes or less, including the use of Codex and a recovery scenario.

Assignment handout https://docs.google.com/document/d/1ixizY2Bd-FUkT6nhYPNRVbtP-LFGqUWHP9tB9HgcH6Q/edit
