import frappe

no_cache = 1


def get_context(context):
    context.no_cache = 1
    from jew_hrms_mobile import face_approval
    from jew_hrms_mobile.api import _user_display_name

    # Opened from an email: the browser may hold an unrelated logged-in session, so always emit the current CSRF token for the POST.
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.token_validity_days = face_approval.TOKEN_VALIDITY_DAYS
    form = frappe.form_dict
    context.done = bool(form.get("done"))
    context.ok = form.get("ok") == "1"
    context.message = form.get("msg") or ""
    context.missing_remarks = bool(form.get("missing_remarks"))
    context.token = form.get("token") or ""
    context.action = form.get("action") or ""
    context.face = None
    context.error = None

    if not context.done:
        doc, error = face_approval.validate_token(context.token, context.action)
        if not doc:
            context.error = error or "This link is not valid."
        else:
            emp = frappe.db.get_value("Employee", doc.employee, ["employee_name", "department"], as_dict=True) or {}
            context.face = {
                "name": doc.name,
                "employee_name": emp.get("employee_name") or doc.employee,
                "employee": doc.employee,
                "department": emp.get("department") or "-",
                "requested_on": frappe.utils.get_datetime(doc.registered_on).strftime("%d/%m/%Y %I:%M %p"),
                "status": doc.approval_status,
                "image": face_approval._image_data_url(doc.name),
                "acting_as": _user_display_name(frappe.db.get_value("User", {"email": doc.approver_email}, "name") or doc.approver_email),
            }
