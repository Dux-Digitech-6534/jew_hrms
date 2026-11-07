import frappe

no_cache = 1


def get_context(context):
	context.no_cache = 1
	from jew_hrms_mobile.api import _validate_late_early_email_action_token, _user_display_name, EMAIL_ACTION_TOKEN_VALIDITY_DAYS

	# The link is opened from an email, so the visiting browser may already have an
	# unrelated logged-in Frappe session (dept head/HR also using the desk app on the
	# same phone). If it does, Frappe requires a matching CSRF token on this POST or
	# it rejects it before our code ever runs - so always emit the current token.
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.token_validity_days = EMAIL_ACTION_TOKEN_VALIDITY_DAYS
	form = frappe.form_dict
	context.done = bool(form.get("done"))
	context.ok = form.get("ok") == "1"
	context.message = form.get("msg") or ""
	context.missing_remarks = bool(form.get("missing_remarks"))
	context.token = form.get("token") or ""
	context.action = form.get("action") or ""
	context.application = None
	context.error = None

	if not context.done:
		doc, error = _validate_late_early_email_action_token(context.token, context.action)
		if not doc:
			context.error = error or "This link is not valid."
		else:
			context.application = {
				"name": doc.name,
				"employee_name": doc.employee_name,
				"application_type": doc.application_type,
				"application_date": frappe.utils.getdate(doc.application_date).strftime("%d/%m/%Y"),
				"expected_time": doc.expected_time,
				"reason": doc.reason or "-",
				"status": doc.status,
				"acting_as": _user_display_name(doc.get("email_action_token_user")),
			}
