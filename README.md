# CST8917 Final Project (Expense Approval Workflow)
**Name:** Kelvin Ngabo  
**Student Number:** 041196196
**Course:** CST8917 (Serverless Applications)  
**Date:** April 2026  

---

## Version A Summary (Azure Durable Functions)

Version A implements the expense approval pipeline using the Azure Durable Functions Python v2 programming model. The orchestrator coordinates a chain of activity functions: validation, amount check, manager notification, and employee notification. For expenses of $100 or more, the Human Interaction pattern is implemented using `wait_for_external_event()` racing against `create_timer()` via `task_any()`. If no manager decision arrives within the timeout period, the expense is automatically escalated.

**Key design decisions:**
- A single `DFApp` instance is used for all triggers and activities, as required by `azure-functions-durable` v1.5.0
- The timeout is configurable via `MANAGER_TIMEOUT_HOURS` which is set to a small value locally for testing
- The manager decision endpoint checks orchestration status before raising the event to prevent race conditions
- All email notifications are simulated via `logging.info()` & in production these would be replaced with SendGrid or Azure Communication Services calls

**Challenges:**
- `azure-durable-functions` (old package name) is no longer published to PyPI & the correct package is now `azure-functions-durable`
- Python 3.13 is not supported & Python 3.11 is required
- The Azure Functions Core Tools on macOS ARM64 requires packages installed via `--target=".python_packages/lib/site-packages"` to be discovered correctly

---

## Version B Summary (Logic Apps + Service Bus)

Version B implements the same workflow using Azure Logic Apps (Consumption, Stateful) and Azure Service Bus. An HTTP-triggered Azure Function handles validation, called by the Logic App via an HTTP action. Incoming expense requests are sent to a Service Bus queue (`expense-requests`). Outcomes are published to a Service Bus topic (`expense-outcomes`) with filtered subscriptions (`sub-approved`, `sub-rejected`, `sub-escalated`) using SQL filters on the `outcome` message property.

**Approach for manager approval:**
Logic Apps does not natively support the Human Interaction pattern the way Durable Functions does. The chosen approach uses the built-in **"Send approval email"** (`ApiConnectionWebhook`) action from the Outlook.com connector. This action sends an email with Approve/Reject buttons to the manager and pauses the workflow until a response is received. The `SelectedOption` output is then evaluated in a downstream Condition action to route the workflow accordingly. This is a webhook-based callback pattern & the Logic App run remains in a "Waiting" state until the manager clicks a button, at which point the webhook fires and the run resumes.

**Challenges:**
- Service Bus messages arrive base64-encoded & all expressions referencing message content must wrap `triggerBody()?['ContentData']` with `base64ToString()`
- The trigger uses `splitOn: @triggerBody()` which means `triggerBody()` refers to a single message object, not an array & the `?[0]?` array accessor pattern does not apply
- When a required field like `employee_email` is missing, expressions that reference it in the `To` field of email actions fail & a fallback address is needed for edge cases

---

## Comparison Analysis

### Development Experience

Durable Functions required more upfront code but provided a much clearer development experience overall. The entire workflow was expressed in a single Python file with explicit control flow & reading the orchestrator function made it immediately obvious what the workflow did and in what order. Debugging was straightforward: the `func start` terminal printed structured logs for every activity execution, and the Durable Task Hub stored the full execution history in Azure Storage.

Logic Apps offered a faster start thanks to the visual designer & connections to Service Bus and Outlook were established through point-and-click without writing any code. However, the visual experience became a liability during debugging. When an action failed, the error messages in the run history were often vague (e.g., "InvalidTemplate. Unable to process template language expressions") with no indication of which expression was at fault. Resolving the base64 encoding issue and the `triggerBody()?[0]?` array accessor problem required switching to the Code view and manually inspecting the JSON definition — at which point the visual abstraction provided no advantage. The development confidence gap between the two approaches was significant: with Durable Functions, the logic was immediately verifiable by reading the code; with Logic Apps, confidence only came after a successful end-to-end test run.

### Testability

Durable Functions was substantially easier to test locally. The entire application ran on `localhost:7071` with Azurite emulating Azure Storage. All six test scenarios could be executed with `curl` commands in seconds, and the logs provided immediate feedback. Writing automated tests is also feasible & the activity functions are pure Python functions that can be unit tested independently of the orchestration runtime.

Logic Apps cannot be tested locally in any meaningful way. Every test required sending a real message to the Azure Service Bus queue and waiting up to 30 seconds for the polling trigger to fire. There is no local emulator for Logic Apps. Automated testing is not supported & there is no way to write a unit test for a Logic App workflow. The only testing mechanism is end-to-end execution in Azure, which makes iteration slow and expensive during development.

### Error Handling

Durable Functions provides fine-grained control over error handling. Activity functions can raise exceptions that propagate to the orchestrator, where they can be caught with standard Python `try/except` blocks. Retries can be configured per activity using `RetryPolicy`. The orchestrator's replay mechanism ensures that completed activities are not re-executed on failure, making partial failures safe to recover from.

Logic Apps handles errors through run-after conditions & each action can be configured to run after a previous action succeeds, fails, is skipped, or times out. However, configuring these conditions requires navigating the designer or editing the JSON definition directly. There is no equivalent to Python exception handling & error recovery logic must be expressed as additional branches in the workflow, which quickly makes the visual designer cluttered. The approval email webhook also has a fixed timeout of 30 days with no built-in escalation path & implementing a custom timeout required additional workarounds not available natively.

### Human Interaction Pattern

Durable Functions implements the Human Interaction pattern natively and elegantly. The `wait_for_external_event()` / `create_timer()` / `task_any()` combination is purpose-built for this use case. The orchestration pauses deterministically, the timer fires exactly when configured, and the manager decision HTTP endpoint raises the event cleanly. The pattern is well-documented and the code expressing it is readable.

Logic Apps does not have a native equivalent. The chosen workaround ,the Outlook.com "Send approval email" webhook action, works but is tightly coupled to a specific email connector. It cannot be triggered programmatically, does not support custom timeouts shorter than the platform maximum, and is not portable to non-email approval scenarios. The Logic App run remains in a "Waiting" state indefinitely until the manager responds, with no built-in escalation. Implementing escalation would require a separate scheduled Logic App or Azure Function to detect stalled runs and intervene which is a significant additional complexity that Durable Functions handles in three lines of code.

### Observability

Both approaches provide run history in the Azure portal, but the quality of observability differs considerably. Durable Functions exposes a rich REST API for querying orchestration state, including instance status, input, output, and execution history. The Application Insights integration captures structured telemetry for every activity execution. Locally, the `func start` terminal provides real-time log output that makes it easy to follow the workflow step by step.

Logic Apps run history is visually appealing & the coloured step diagram makes it easy to see at a glance which branch was taken. However, error messages are often generic and do not pinpoint the failing expression. There is no equivalent to the Durable Functions status query API & monitoring Logic App runs programmatically requires the Azure Monitor or Logic Apps Management API, which adds integration complexity. For this project, the Logic Apps run history was sufficient for capturing screenshots but would be inadequate for production monitoring of high-volume workflows.

### Cost

**Assumptions:** 8-hour working day, 22 working days/month. Auto-approve rate ~40%, manager approval rate ~60%. Average Logic App run: 6 actions. Average Durable Functions orchestration: 5 activity executions.

**At ~100 expenses/day (~2,200/month):**

Durable Functions (Consumption plan): ~2,200 orchestrations × 5 activities = 11,000 executions. Well within the 1,000,000 free executions/month. Storage costs negligible at this scale. **Estimated cost: ~$0/month.**

Logic Apps (Consumption): ~2,200 runs × 6 actions = 13,200 action executions at $0.000025/action. **Estimated cost: ~$0.33/month.** Service Bus Standard: ~$10/month base. **Total: ~$10.33/month.**

**At ~10,000 expenses/day (~220,000/month):**

Durable Functions: ~220,000 orchestrations × 5 activities = 1,100,000 executions. Exceeds free tier by 100,000 executions at $0.20/million. Storage for history tables: ~$2-5/month. **Estimated cost: ~$2-5/month.**

Logic Apps: ~220,000 runs × 6 actions = 1,320,000 action executions at $0.000025/action = $33/month. Service Bus Standard: ~$10/month. **Total: ~$43/month.**

At scale, Durable Functions is significantly more cost-efficient, primarily because the generous free tier covers most student and small business workloads, and the per-execution cost beyond that tier is lower than Logic Apps action pricing.

---

## Recommendation

For a production team building this expense approval pipeline, I would choose **Azure Durable Functions**.

The primary reason is testability. The inability to run Logic Apps locally is a serious liability in a team environment: it slows iteration, makes debugging expensive, and prevents automated testing. With Durable Functions, the entire workflow runs locally in seconds, unit tests can be written for individual activities, and the feedback loop is immediate.

The second reason is the Human Interaction pattern. Durable Functions implements it natively with a clean, readable API. Logic Apps requires coupling to a specific email connector with limited timeout control and no built-in escalation & an acceptable workaround for a prototype but fragile in production.

The third reason is cost at scale. At 10,000 expenses/day, Durable Functions costs roughly one tenth of the equivalent Logic Apps workflow.

**When I would choose Logic Apps instead:** If the team had no Python developers and needed to build the workflow quickly without writing code, Logic Apps would be the pragmatic choice. It is also the better option when the workflow primarily orchestrates existing Azure services (Service Bus, Blob Storage, SharePoint) through pre-built connectors, rather than executing custom business logic. For integration-heavy workflows where the connectors do most of the work, the visual designer's strength, rapid connector configuration, outweighs its debugging weaknesses.

---

## References

- Microsoft. (2024). *Durable Functions overview*. https://learn.microsoft.com/en-us/azure/azure-functions/durable/durable-functions-overview
- Microsoft. (2024). *Azure Logic Apps overview*. https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-overview
- Microsoft. (2024). *Azure Service Bus documentation*. https://learn.microsoft.com/en-us/azure/service-bus-messaging/
- Microsoft. (2024). *Azure Functions Python developer guide*. https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python
- Microsoft. (2024). *Human interaction pattern in Durable Functions*. https://learn.microsoft.com/en-us/azure/azure-functions/durable/durable-functions-overview?tabs=python#human
- Microsoft. (2024). *Azure pricing calculator*. https://azure.microsoft.com/en-us/pricing/calculator/
- PyPI. (2024). *azure-functions-durable*. https://pypi.org/project/azure-functions-durable/

