import azure.functions as func
import json
import logging

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

VALID_CATEGORIES = {"travel", "meals", "supplies", "equipment", "software", "other"}

@app.route(route="validate", methods=["POST"])
def validate_expense(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP-triggered validation function called by the Logic App.

    Accepts JSON expense request, returns:
    - 200 with {"valid": true} if valid
    - 200 with {"valid": false, "reason": "..."} if invalid
    """
    try:
        expense = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"valid": False, "reason": "Invalid JSON body."}),
            mimetype="application/json",
            status_code=200
        )

    required_fields = ["employee_name", "employee_email", "amount",
                       "category", "description", "manager_email"]

    for field in required_fields:
        if not expense.get(field):
            return func.HttpResponse(
                json.dumps({"valid": False, "reason": f"Missing required field: '{field}'."}),
                mimetype="application/json",
                status_code=200
            )

    category = str(expense.get("category", "")).lower().strip()
    if category not in VALID_CATEGORIES:
        return func.HttpResponse(
            json.dumps({
                "valid": False,
                "reason": f"Invalid category '{category}'. Valid: {', '.join(sorted(VALID_CATEGORIES))}."
            }),
            mimetype="application/json",
            status_code=200
        )

    try:
        amount = float(expense["amount"])
        if amount <= 0:
            return func.HttpResponse(
                json.dumps({"valid": False, "reason": "Amount must be greater than zero."}),
                mimetype="application/json",
                status_code=200
            )
    except (ValueError, TypeError):
        return func.HttpResponse(
            json.dumps({"valid": False, "reason": "Amount must be a valid number."}),
            mimetype="application/json",
            status_code=200
        )

    logging.info(f"Expense validated for {expense.get('employee_name')}.")
    return func.HttpResponse(
        json.dumps({"valid": True, "reason": "Validation passed."}),
        mimetype="application/json",
        status_code=200
    )
