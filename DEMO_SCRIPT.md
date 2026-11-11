# Salon Desk demo script

Target length is four to five minutes. Record the local app and your voice. Open http://127.0.0.1:8502/ and the project documentation before recording. The local Ollama model should be running. There are no API keys to enter. Rehearse the actual model runtime before recording.

The measured conflict-only scenario took 121.84 seconds with automated test approvals and included all six roles. Allow additional time for customer review, clicks and narration. You can explain the problem and architecture while the model processes the request.

## First thirty seconds

Show the booking form.

“Hi, I’m Pooja. My project is Salon Desk, a multi-agent booking assistant built in Python.

“Salon staff spend time checking service durations, stylist schedules and customer preferences. Availability can change before a customer confirms, causing another round of messages. Salon Desk finds a suitable appointment, asks for approval and handles those changes.”

## Thirty to sixty seconds

Show the About the project tab and README architecture diagram.

“Six agents handle intake, service matching, scheduling, booking review, recovery and confirmation. LangGraph coordinates their work and saves progress in SQLite.

“The salon data is fictional, while the local AI calls and booking database operations run for real.”

## One to two minutes

Before starting, open Demonstrate recovery and select Slot is taken after approval. This one scenario exercises all six roles while keeping the main recording short. Enter a sample customer name, choose an unused future date, and request a haircut at 2 PM, preferably with Maya.

“I’m requesting a haircut at 2 PM with Maya. I’ve enabled a labeled booking-conflict scenario so we can test recovery.

“The intake and service agents interpret the request and check the catalog. Scheduling finds an opening, and the review agent checks the proposal. No appointment for this customer has been created yet.”

Show the proposed service, stylist, date, time and USD price.

## Two to three minutes

Click Confirm this appointment.

“This approval applies to the exact appointment on screen. Here another customer's reservation takes that slot before my booking is written.

“The booking tool detects the overlap. The recovery agent chooses a fresh plan. Scheduling and review prepare a new proposal, and the earlier approval is cleared. The app asks me to confirm the new time.”

Show the changed time and the fresh approval controls. Do not claim a particular replacement time before checking what the app actually offers.

## Three to three and a half minutes

Confirm the new proposal.

“The appointment now commits. The confirmation agent reads the saved booking and prepares the receipt. Its fields are checked against the database record. There is one appointment for this customer.”

Open Appointments. Distinguish the labeled simulated competing reservation from the customer's confirmed appointment. Download the confirmation.

## Final thirty seconds

Open Agent activity and expand a real AI response and a recovery event.

“The activity tab shows all six specialist roles, their structured outputs, model timing, tool operations and approvals. Pending approvals and confirmed bookings survive reopening the same request URL.

“I used Codex to implement the workflow and test conflicts, concurrent requests, changed prices, stale approvals and duplicate prevention.”

Finish by showing the source repository and the project documentation.

## Recording checks

Use a future date without existing appointments so the 2 PM example is available at the beginning. For another recording, select a different future date instead of deleting calendar records. Keep the selected date visible because tomorrow changes.

The course submission needs a Google Doc, a video of five minutes or less and a GitHub link. Upload the source ZIP contents and include an actual sample confirmation and activity log.

The separate all-failures verification log also covers the temporary calendar outage and interrupted booking response. Those can be shown in an extended walkthrough or discussed using the saved verification evidence.
