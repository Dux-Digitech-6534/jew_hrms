import frappe

no_cache = 1


def get_context(context):
	context.no_cache = 1
	from jew_hrms_mobile.api import _validate_on_duty_email_action_token, _user_display_name, EMAIL_ACTION_TOKEN_VALIDITY_DAYS

	# The link is opened from an email, so the visiting browser may already have an
	# unrelated logged-in Frappe session on the same phone. If it does, Frappe
	# requires a matching CSRF token on this POST or it rejects it before our code
	# ever runs - so always emit the current token.
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.token_validity_days = EMAIL_ACTION_TOKEN_VALIDITY_DAYS
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
		doc, error = _validate_on_duty_email_action_token(context.token, context.action)
		if not doc:
			context.error = error or "This link is not valid."
		else:
			date_range = frappe.utils.getdate(doc.from_date).strftime("%d/%m/%Y")
			if doc.from_date != doc.to_date:
				date_range += " to " + frappe.utils.getdate(doc.to_date).strftime("%d/%m/%Y")
			context.request = {
				"name": doc.name,
				"employee_name": doc.employee_name,
				"date_range": date_range,
				"explanation": doc.explanation or "-",
				"status": doc.jew_od_status,
				"acting_as": _user_display_name(doc.get("jew_email_action_token_user")),
			}
