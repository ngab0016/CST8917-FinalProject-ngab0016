import azure.functions as func
import azure.durable_functions as df
import logging
import json
from datetime import timedelta

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
df_app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ---------------------------------------------------------------------------
# VALID CATEGORIES
# ---------------------------------------------------------------------------
VALID_CATEGORIES = {"travel", "meals", "supplies", "equipment", "software", "other"}
AUTO_APPROVE_THRESHOLD = 100.0
MANAGER_TIMEOUT_HOURS = 24  # change to a smaller value (e.g. 0.003 ≈ 10s) for local testing


# ===========================================================================
# CLIENT FUNCTION — Receives an expense submission via HTTP POST
# ===========================================================================
@df_app.route(route="expenses/submit", methods=["POST"])
@df_app.durable_client_input(client_name="client")
async def http_start(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """
    HTTP client trigger.  Accepts a JSON expense request and starts the orchestrator.

    Expected JSON body:
    {
        "employee_name": "Jane Doe",
        "employee_email": "jane@example.com",
        "amount": 150.00,
        "category": "travel",
        "description": "Flight to conference",
        "manager_email": "manager@example.com"
    }
    """
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body.", status_code=400)

    instance_id = await client.start_new("expense_orchestrator", client_input=body)
    logging.info(f"Started orchestration with ID = '{instance_id}'.")

    # Return the instance ID and management URLs so the caller can poll status
    return client.create_check_status_response(req, instance_id)


# ===========================================================================
# ORCHESTRATOR — Coordinates the approval workflow
# ===========================================================================
@df_app.orchestration_trigger(context_name="context")
def expense_orchestrator(context: df.DurableOrchestrationContext):
    """
    Orchestrates the expense approval pipeline:
      1. Validate the request
      2. Auto-approve if amount < $100
      3. Otherwise wait for manager decision (with timeout → escalate)
      4. Notify the employee of the outcome
    """
    expense = context.get_input()

    # ── Step 1: Validate ────────────────────────────────────────────────────
    validation_result = yield context.call_activity("validate_expense", expense)

    if not validation_result["valid"]:
        yield context.call_activity("notify_employee", {
            "expense": expense,
            "outcome": "rejected",
            "reason": validation_result["reason"],
        })
        return {"status": "rejected", "reason": validation_result["reason"]}

    # ── Step 2: Amount check ─────────────────────────────────────────────────
    amount = float(expense.get("amount", 0))

    if amount < AUTO_APPROVE_THRESHOLD:
        yield context.call_activity("notify_employee", {
            "expense": expense,
            "outcome": "approved",
            "reason": "Auto-approved: amount under $100.",
        })
        return {"status": "approved", "reason": "Auto-approved: amount under $100."}

    # ── Step 3: Human Interaction Pattern ───────────────────────────────────
    # Send approval request email to manager
    yield context.call_activity("notify_manager", {
        "expense": expense,
        "instance_id": context.instance_id,
    })

    # Race: wait for external event OR timer expiry
    approval_event = context.wait_for_external_event("ManagerDecision")
    timeout_event  = context.create_timer(
        context.current_utc_datetime + timedelta(hours=MANAGER_TIMEOUT_HOURS)
    )

    winner = yield context.task_any([approval_event, timeout_event])

    if winner == approval_event:
        # Cancel the timer to avoid unnecessary delay
        timeout_event.cancel()
        decision = approval_event.result
        outcome = decision.get("action", "rejected")   # "approved" or "rejected"
        reason  = decision.get("reason", "Manager decision received.")
    else:
        # Timer fired — escalate
        outcome = "escalated"
        reason  = "No manager response received within the timeout period."

    # ── Step 4: Notify employee ──────────────────────────────────────────────
    yield context.call_activity("notify_employee", {
        "expense": expense,
        "outcome": outcome,
        "reason": reason,
    })

    return {"status": outcome, "reason": reason}


# ===========================================================================
# ACTIVITY: validate_expense
# ===========================================================================
@df_app.activity_trigger(input_name="expense")
def validate_expense(expense: dict) -> dict:
    """
    Validates required fields and category.
    Returns {"valid": bool, "reason": str}
    """
    required_fields = ["employee_name", "employee_email", "amount", "category", "description", "manager_email"]

    for field in required_fields:
        if not expense.get(field):
            return {"valid": False, "reason": f"Missing required field: '{field}'."}

    category = str(expense.get("category", "")).lower().strip()
    if category not in VALID_CATEGORIES:
        return {
            "valid": False,
            "reason": f"Invalid category '{category}'. Valid categories: {', '.join(sorted(VALID_CATEGORIES))}."
        }

    try:
        amount = float(expense["amount"])
        if amount <= 0:
            return {"valid": False, "reason": "Amount must be greater than zero."}
    except (ValueError, TypeError):
        return {"valid": False, "reason": "Amount must be a valid number."}

    logging.info(f"Expense validated successfully for {expense.get('employee_name')}.")
    return {"valid": True, "reason": "Validation passed."}


# ===========================================================================
# ACTIVITY: notify_manager
# ===========================================================================
@df_app.activity_trigger(input_name="payload")
def notify_manager(payload: dict) -> str:
    """
    Simulates sending an approval request email to the manager.
    In production, replace the log statement with an actual email send
    (e.g. SendGrid, Azure Communication Services).
    """
    expense     = payload["expense"]
    instance_id = payload["instance_id"]

    approve_url = f"http://localhost:7071/api/expenses/decision/{instance_id}?action=approved"
    reject_url  = f"http://localhost:7071/api/expenses/decision/{instance_id}?action=rejected"

    logging.info(
        f"[EMAIL → MANAGER] Approval required for {expense.get('employee_name')} "
        f"— ${expense.get('amount')} ({expense.get('category')}).\n"
        f"  Approve : {approve_url}\n"
        f"  Reject  : {reject_url}"
    )
    return "Manager notified."


# ===========================================================================
# ACTIVITY: notify_employee
# ===========================================================================
@df_app.activity_trigger(input_name="payload")
def notify_employee(payload: dict) -> str:
    """
    Simulates sending an outcome email to the employee.
    Replace the log statement with a real email send in production.
    """
    expense = payload["expense"]
    outcome = payload["outcome"]
    reason  = payload["reason"]

    logging.info(
        f"[EMAIL → EMPLOYEE] Hi {expense.get('employee_name')}, "
        f"your expense of ${expense.get('amount')} ({expense.get('category')}) "
        f"has been {outcome.upper()}. Reason: {reason}"
    )
    return f"Employee notified: {outcome}."


# ===========================================================================
# MANAGER DECISION ENDPOINT — Simulates manager approving or rejecting
# ===========================================================================
@df_app.route(route="expenses/decision/{instance_id}", methods=["POST"])
@df_app.durable_client_input(client_name="client")
async def manager_decision(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """
    HTTP endpoint the manager calls to approve or reject an expense.

    Route param : instance_id — the orchestration instance to signal
    Query param : action      — "approved" or "rejected"
    Optional JSON body:
    { "reason": "Looks good." }
    """
    instance_id = req.route_params.get("instance_id")
    action      = req.params.get("action", "").lower()

    if action not in ("approved", "rejected"):
        return func.HttpResponse(
            "Query param 'action' must be 'approved' or 'rejected'.",
            status_code=400
        )

    try:
        body   = req.get_json()
        reason = body.get("reason", f"Manager {action} the expense.")
    except ValueError:
        reason = f"Manager {action} the expense."

    # Check the orchestration is still running
    status = await client.get_status(instance_id)
    if status is None:
        return func.HttpResponse(f"Orchestration '{instance_id}' not found.", status_code=404)
    if status.runtime_status.value not in ("Running", "Pending"):
        return func.HttpResponse(
            f"Orchestration '{instance_id}' is no longer awaiting a decision (status: {status.runtime_status.value}).",
            status_code=409
        )

    await client.raise_event(instance_id, "ManagerDecision", {"action": action, "reason": reason})

    logging.info(f"Manager decision '{action}' sent to orchestration '{instance_id}'.")
    return func.HttpResponse(
        json.dumps({"message": f"Decision '{action}' recorded.", "instance_id": instance_id}),
        mimetype="application/json",
        status_code=200
    )