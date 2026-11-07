import frappe

no_cache = 1


def get_context(context):
	context.no_cache = 1
	from jew_hrms_mobile.api import _validate_cancellation_action_token, _fmt_dmy, CANCEL_ACTION_TOKEN_VALIDITY_DAYS

	# Same reasoning as leave_email_action.py: emit the current CSRF token in
	# case the visiting browser already has an unrelated logged-in Desk session.
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.token_validity_days = CANCEL_ACTION_TOKEN_VALIDITY_DAYS
	form = frappe.form_dict
	context.done = bool(form.get("done"))
	context.ok = form.get("ok") == "1"
	context.message = form.get("msg") or ""
	context.missing_remarks = bool(form.get("missing_remarks"))
	context.token = form.get("token") or ""
	context.action = form.get("action") or ""
	context.request = None
	context.error = None

	if not context.done:
		doctype, doc, error = _validate_cancellation_action_token(context.token)
		if not doc:
			context.error = error or "This link is not valid."
		else:
			if doctype == "Leave Application":
				context.request = {
					"employee_name": doc.employee_name,
					"type_label": doc.leave_type,
					"period": "{0} to {1}".format(_fmt_dmy(doc.jew_cancel_from_date), _fmt_dmy(doc.jew_cancel_to_date)),
					"days": doc.jew_cancel_days,
					"reason": doc.jew_cancel_reason,
				}
			else:
				context.request = {
					"employee_name": doc.employee_name,
					"type_label": doc.application_type,
					"period": _fmt_dmy(doc.application_date),
					"days": 1,
					"reason": doc.jew_cancel_reason,
				}
