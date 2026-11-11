# Salon Desk requirements audit

The core implementation covers the technical scope of the supplied Week 3 handout. A maximum score cannot be established because the handout does not provide a numerical grading rubric. The submission still needs its final shared artifacts and measured evaluation evidence.

| Handout requirement | Evidence | Status |
| --- | --- | --- |
| Permitted use case | The handout explicitly allows a custom use case. Salon appointment coordination is the chosen workflow. | Covered |
| Agentic task completion | Six real local AI specialists handle intake, service matching, scheduling, booking review, recovery and confirmation. The graph branches for clarification, failures, approval and replanning. | Covered |
| Python implementation and orchestration | Python, LangGraph, Streamlit and Ollama are included in the source package. | Covered |
| Tools | Catalog reads, availability reads and transactional booking execute against SQLite. Repeated writes recover the existing request's booking. | Covered |
| Persistent state | SQLite checkpoints preserve pending review across a reopened connection. Calendar records persist separately. | Covered and tested |
| Human approval | A unique proposal ID binds approval to service, stylist, start, duration and price. Conflicts and price changes require fresh approval. | Covered and tested |
| Failure recovery | Injected calendar failure retries. A competing reservation causes replanning. An interrupted write response recovers the same booking. Model outages hand off to the user. | Covered and tested |
| Measurable success | Fourteen regression checks passed. The current actual local-model conflict scenario exercised all six roles, used nine successful AI responses and saved one customer booking in 121.84 seconds. The extended all-failures run used 11 responses and took 198.8 seconds. | Initial evidence ready |
| Agent one liner and framework | The documentation includes the goal, surface, control flow, tools, memory, limits, approval, failures and a target success measure. | Draft ready |
| Project documentation | Overview, sample data, development prompts, iterations and observations are in the documentation draft. | Must become a shared Google Doc |
| Video | A narration and screen-action script follows the reference recording's sequence. | Must record the working app in five minutes or less |
| Source code | Verified source ZIP includes configuration, README, test suite, documentation, script and actual-model sample outputs. | Must upload to GitHub and share the link |

## Improvements before recording and submission

1. Evaluate ten representative requests with the actual local model. Record expected outcome, observed outcome, duration and pass or fail. The three-minute ordinary-request and eight-out-of-ten statements in the documentation remain targets until measured.
2. Rehearse the recording on this computer. The revised conflict-only scenario took 121.84 seconds with automated test approvals, while the extended all-failures scenario took 198.8 seconds. The revised script uses one conflict and exercises all six roles. Add narration and normal customer pauses, then check that the recording stays below five minutes.
3. Show a fresh approval after the appointment changes and show the final customer booking count. The activity log distinguishes test failures and the simulated competing reservation from actual local AI and database work.
4. Copy the finalized documentation into Google Docs, upload the code to GitHub, record the video and check that reviewers can open all three links.

The salon, service prices and staff schedules are sample data. The local AI inference and booking transactions are real. This application creates appointments in its local sample calendar. The handout does not require a commercial salon integration, but this scope should be stated clearly during the recording.

The handout says tracks may use Nebius Token Factory. That wording is optional. No paid model, LangSmith account, voice interface or public hosting requirement is stated for this custom use case.

Reference handout https://docs.google.com/document/d/1ixizY2Bd-FUkT6nhYPNRVbtP-LFGqUWHP9tB9HgcH6Q/edit
