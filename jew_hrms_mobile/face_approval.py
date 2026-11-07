"""Self face registration WITH approval (JEW HRMS mobile app).

An employee flagged with Employee.custom_face_self_register = 1 films their own face in the app. The template is stored
INACTIVE with approval_status "Pending Approval" - a punch is only ever verified against the ACTIVE template, so an
unapproved face can never be used. The approver (Employee.custom_face_approver_email, else the department head) gets an
email with the photo and one-click Approve / Reject links (same token scheme as the Leave / Late-Early emails) and can also
decide inside the app (Face approvals). Approving swaps it in as the active template; rejecting keeps the old one.
"""
import base64

import frappe
from frappe.utils import add_to_date, escape_html, get_datetime, get_url, now_datetime

TOKEN_VALIDITY_DAYS = 3
PENDING = "Pending Approval"
DOCTYPE = "JEW Employee Face"


def _api():
    from jew_hrms_mobile import api
    return api


def _has_flag_field():
    try:
        return frappe.get_meta("Employee").has_field("custom_face_self_register")
    except Exception:
        return False


def self_register_enabled(employee):
    """True if this employee may register their own face (needs approval)."""
    if not employee or not _has_flag_field():
        return False
    return bool(frappe.db.get_value("Employee", employee, "custom_face_self_register"))


def is_self_registrant(employee):
    """The logged-in user IS this employee and is allowed to self-register."""
    try:
        own = _api()._get_employee_for_user()
    except Exception:
        own = None
    return bool(own and own.name == employee and self_register_enabled(employee))


def approver_email(employee):
    """Who approves this employee's face: the explicit Face Approver Email, else the Dept Head Email."""
    if not frappe.get_meta("Employee").has_field("custom_face_approver_email"):
        return None
    row = frappe.db.get_value("Employee", employee, ["custom_face_approver_email", "custom_dept_head_email"], as_dict=True) or {}
    for value in (row.get("custom_face_approver_email"), row.get("custom_dept_head_email")):
        value = (value or "").strip()
        if value and "@" in value:
            return value
    return None


def _user_email(user=None):
    u = user or frappe.session.user
    return (frappe.db.get_value("User", u, "email") or u or "").strip().lower()


def can_approve(user=None):
    """Admin roles, or anyone named as a Face Approver on at least one active employee."""
    u = user or frappe.session.user
    if not u or u == "Guest":
        return False
    try:
        if _api()._can_access_admin(u):
            return True
    except Exception:
        pass
    if not frappe.get_meta("Employee").has_field("custom_face_approver_email"):
        return False
    email = _user_email(u)
    return bool(email and frappe.db.exists("Employee", {"status": "Active", "custom_face_approver_email": email}))


def _can_decide(doc, user=None):
    u = user or frappe.session.user
    try:
        if _api()._can_access_admin(u):
            return True
    except Exception:
        pass
    return bool(doc.get("approver_email") and _user_email(u) == (doc.approver_email or "").strip().lower())


def status_extra(employee):
    """Extra keys for the face_status payload: waiting-for-approval / last rejection. Never raises."""
    out = {"pending": False}
    try:
        row = frappe.db.get_value(DOCTYPE, {"employee": employee, "approval_status": PENDING}, ["name", "registered_on"], as_dict=True, order_by="registered_on desc")
        if row:
            out.update({"pending": True, "pending_on": row.registered_on, "pending_name": row.name})
        else:
            rej = frappe.db.get_value(DOCTYPE, {"employee": employee, "approval_status": "Rejected"}, ["decision_remarks", "decided_on"], as_dict=True, order_by="decided_on desc")
            latest_ok = frappe.db.get_value(DOCTYPE, {"employee": employee, "is_active": 1}, "registered_on")
            if rej and rej.decided_on and (not latest_ok or get_datetime(rej.decided_on) > get_datetime(latest_ok)):
                out.update({"rejected": True, "rejected_remarks": rej.decision_remarks or "", "rejected_on": rej.decided_on})
    except Exception:
        pass
    return out


def _image_data_url(face_name):
    file_name = frappe.db.get_value("File", {"attached_to_doctype": DOCTYPE, "attached_to_name": face_name}, "name", order_by="creation desc")
    if not file_name:
        return None
    try:
        content = frappe.get_doc("File", file_name).get_content()
        if isinstance(content, str):
            content = content.encode("latin-1", "ignore")
        return "data:image/jpeg;base64," + base64.b64encode(content).decode()
    except Exception:
        return None


# --------------------------------------------------------------------------- employee side
@frappe.whitelist()
def register_my_face(face_image=None, liveness_challenge=None, liveness_frames=None):
    api = _api()
    api._require_login()
    employee = api._get_current_employee()
    return submit_self_face(employee.name, face_image, liveness_challenge, liveness_frames)


def submit_self_face(employee, face_image, liveness_challenge=None, liveness_frames=None):
    api = _api()
    if not self_register_enabled(employee):
        return api._fail("Face self-registration is not enabled for you. Please contact admin.", "permission_denied")
    approver = approver_email(employee)
    if not approver:
        return api._fail("No approver is set for your face approval. Please contact admin.", "no_approver")
    from jew_hrms_mobile import liveness_active, liveness_service
    from jew_hrms_mobile.face_service import register_face, serialize_encoding
    if liveness_service.active_enabled():
        live_ok, live_res = liveness_active.verify_active(employee, liveness_challenge, liveness_frames, scope="reg")
        if not live_ok:
            return api._fail(live_res.get("message"), live_res.get("code", "liveness_failed"))
        face_image = live_res["frame"]
    if not face_image:
        return api._fail("Capture your face before sending it for approval.", "no_face_detected")
    face_result = register_face(employee, face_image)
    if not face_result.get("ok"):
        return api._fail(face_result.get("message", "Face template could not be created."), face_result.get("code", "face_not_matched"), {"face": face_result})
    for old in frappe.get_all(DOCTYPE, filters={"employee": employee, "approval_status": PENDING}, pluck="name"):
        frappe.db.set_value(DOCTYPE, old, {"approval_status": "Replaced", "email_action_token": ""}, update_modified=False)
    doc = frappe.new_doc(DOCTYPE)
    doc.employee = employee
    doc.user = frappe.db.get_value("Employee", employee, "user_id")
    doc.face_encoding = serialize_encoding(face_result["encoding"])
    doc.face_template = doc.face_encoding
    doc.is_active = 0
    doc.approval_status = PENDING
    doc.self_registered = 1
    doc.approver_email = approver
    doc.registered_by = frappe.session.user
    doc.registered_on = now_datetime()
    doc.last_updated_on = now_datetime()
    doc.insert(ignore_permissions=True)
    doc.face_image = api._save_face_image(employee, face_image, doc.name)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    _send_request_email(doc)
    approver_name = api._user_display_name(frappe.db.get_value("User", {"email": approver}, "name") or approver)
    return api._ok({"face": doc.name, "employee": employee, "approval_status": PENDING, "approver": approver_name},
                   "Sent to {0} for approval. You can mark attendance once it is approved.".format(approver_name))


def _employee_label(emp):
    return emp.employee_name or emp.name


def _send_request_email(doc):
    """Email the approver (photo attached + one-click Approve / Reject / View links). Best-effort."""
    try:
        api = _api()
        emp = frappe.get_doc("Employee", doc.employee)
        token = frappe.generate_hash(length=48)
        frappe.db.set_value(DOCTYPE, doc.name, {"email_action_token": token, "email_action_token_expiry": add_to_date(now_datetime(), days=TOKEN_VALIDITY_DAYS)}, update_modified=False)
        site = get_url()
        link = lambda action: "{0}/face_email_action?token={1}&action={2}".format(site, token, action)
        who = escape_html(_employee_label(emp))
        btn = "display:inline-block;padding:10px 24px;color:#ffffff;text-decoration:none;border-radius:5px;font-weight:600;margin-right:10px;background:{0}"
        had = bool(frappe.db.exists(DOCTYPE, {"employee": doc.employee, "is_active": 1}))
        body = (
            "<p>Dear {approver},</p>"
            "<p>{who} has registered {what} face in the JEW HRMS app and it needs your approval. "
            "The photo is attached - please check that it clearly shows {who}'s own face.</p>"
            "<p>Employee Name: {who}<br>Employee ID: {eid}<br>Department: {dept}<br>Requested on: {on}<br>Status: Pending approval"
            "{had_note}</p>"
            '<p style="margin:20px 0"><a href="{a}" style="{ba}" rel="noopener noreferrer">Approve</a>'
            '<a href="{r}" style="{br}" rel="noopener noreferrer">Reject</a>'
            '<a href="{v}" style="{bv}" rel="noopener noreferrer">View photo</a></p>'
            "<p>Until you approve, {who} cannot use this face for attendance"
            "{keep_note}. The link works once and expires in {days} days.</p>"
            "<p>Regards,<br>HRMS Team</p>"
        ).format(
            approver=escape_html(api._user_display_name(frappe.db.get_value("User", {"email": doc.approver_email}, "name") or doc.approver_email)),
            who=who, what="a new" if had else "their", eid=escape_html(emp.name), dept=escape_html(emp.get("department") or "-"),
            on=escape_html(api._fmt_dmy(doc.registered_on) + " " + get_datetime(doc.registered_on).strftime("%I:%M %p")),
            had_note="<br>Replaces: the face registered earlier (it stays active until you approve)" if had else "",
            a=link("approve"), r=link("reject"), v=link("view"),
            ba=btn.format("#1a7f45"), br=btn.format("#b3341f"), bv=btn.format("#1a52b3"),
            keep_note=", and the earlier face keeps working" if had else "", days=TOKEN_VALIDITY_DAYS,
        )
        file_name = frappe.db.get_value("File", {"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name}, "name", order_by="creation desc")
        frappe.sendmail(recipients=[doc.approver_email], subject="Face registration approval needed - {0}".format(_employee_label(emp)), message=body,
                        sender=api._hrms_sender_email(), reference_doctype=DOCTYPE, reference_name=doc.name,
                        attachments=[{"fid": file_name}] if file_name else None)
        api._desk_bell_notify([doc.approver_email], doc, "{0} registered a face - pending your approval.".format(_employee_label(emp)))
        frappe.db.commit()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "JEW HRMS face approval email failed")


# --------------------------------------------------------------------------- approver side
@frappe.whitelist()
def get_pending_face_approvals():
    api = _api()
    api._require_login()
    if not can_approve():
        return api._fail("You do not have permission to approve faces.", "permission_denied")
    filters = {"approval_status": PENDING}
    if not api._can_access_admin():
        filters["approver_email"] = _user_email()
    rows = frappe.get_all(DOCTYPE, filters=filters, fields=["name", "employee", "registered_on", "approver_email"], order_by="registered_on desc", limit=50, ignore_permissions=True)
    out = []
    for r in rows:
        emp = frappe.db.get_value("Employee", r.employee, ["employee_name", "department", "designation"], as_dict=True) or {}
        out.append({"name": r.name, "employee": r.employee, "employee_name": emp.get("employee_name") or r.employee, "department": emp.get("department"),
                    "designation": emp.get("designation"), "registered_on": r.registered_on, "image": _image_data_url(r.name),
                    "had_face": bool(frappe.db.exists(DOCTYPE, {"employee": r.employee, "is_active": 1}))})
    return api._ok({"requests": out, "count": len(out)})


@frappe.whitelist()
def decide_face_approval(name=None, decision=None, remarks=None):
    api = _api()
    api._require_login()
    if not name or not frappe.db.exists(DOCTYPE, name):
        return api._fail("Request not found.", "not_found")
    doc = frappe.get_doc(DOCTYPE, name)
    if not _can_decide(doc):
        return api._fail("You do not have permission to decide this request.", "permission_denied")
    return apply_decision(doc, decision, remarks, frappe.session.user)


def apply_decision(doc, decision, remarks, user):
    """Approve (swap in as the active template) or reject. Shared by the app and the email links."""
    api = _api()
    if decision not in ("Approved", "Rejected"):
        return api._fail("Unknown decision.", "validation_error")
    if doc.approval_status != PENDING:
        return api._fail("This request has already been decided.", "already_decided")
    remarks = (remarks or "").strip()
    if decision == "Rejected" and not remarks:
        return api._fail("A reason is required to reject.", "validation_error")
    now = now_datetime()
    updates = {"decided_by": user, "decided_on": now, "decision_remarks": remarks, "email_action_token": ""}
    if decision == "Approved":
        for old in frappe.get_all(DOCTYPE, filters={"employee": doc.employee, "is_active": 1, "name": ["!=", doc.name]}, pluck="name"):
            frappe.db.set_value(DOCTYPE, old, "is_active", 0)
        updates.update({"is_active": 1, "approval_status": "Approved", "last_updated_on": now})
    else:
        updates["approval_status"] = "Rejected"
    frappe.db.set_value(DOCTYPE, doc.name, updates)
    frappe.db.commit()
    _send_decision_email(doc, decision, remarks)
    if decision == "Approved":
        return api._ok({"name": doc.name, "approval_status": "Approved"}, "Face approved. The employee can now mark attendance with it.")
    return api._ok({"name": doc.name, "approval_status": "Rejected"}, "Face rejected. The employee has been told to register again.")


def _send_decision_email(doc, decision, remarks):
    try:
        api = _api()
        emp = frappe.get_doc("Employee", doc.employee)
        to = api._applicant_email(emp)
        label = _employee_label(emp)
        if decision == "Approved":
            subject, text = "Your face registration is approved", "Your face has been approved. You can now mark attendance in the JEW HRMS app."
        else:
            subject = "Your face registration was rejected"
            text = "Your face registration was rejected{0}. Please register your face again in the JEW HRMS app (Profile > Register my face).".format(
                " - reason: " + escape_html(remarks) if remarks else "")
        if to:
            frappe.sendmail(recipients=[to], subject=subject, message="<p>Dear {0},</p><p>{1}</p><p>Regards,<br>HRMS Team</p>".format(escape_html(label), text),
                            sender=api._hrms_sender_email(), reference_doctype=DOCTYPE, reference_name=doc.name)
        api._desk_bell_notify([e for e in [to] if e], doc, subject)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "JEW HRMS face decision email failed")


# --------------------------------------------------------------------------- one-click email links
def validate_token(token, action):
    """(doc, None) or (None, error). approve/reject need a still-pending request; view is lenient."""
    if not token:
        return None, "Invalid or missing action link."
    name = frappe.db.get_value(DOCTYPE, {"email_action_token": token}, "name")
    if not name:
        return None, "This link is no longer valid - it may have already been used or superseded."
    doc = frappe.get_doc(DOCTYPE, name)
    expiry = doc.get("email_action_token_expiry")
    if expiry and get_datetime(expiry) < now_datetime():
        return None, "This action link has expired."
    if action in ("approve", "reject") and doc.approval_status != PENDING:
        return None, "This request has already been decided."
    return doc, None


@frappe.whitelist(allow_guest=True)
def confirm_face_email_action(token=None, action=None, remarks=None, csrf_token=None):
    """POST target of the face_email_action page: runs the decision as the token's approver."""
    from urllib.parse import urlencode

    def _redirect(**params):
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/face_email_action?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})

    doc, error = validate_token(token, action)
    if not doc:
        return _redirect(done=1, ok=0, msg=error or "This link is not valid.")
    if action == "reject" and not (remarks and str(remarks).strip()):
        return _redirect(action="reject", token=token, missing_remarks=1)
    acting_user = frappe.db.get_value("User", {"email": doc.approver_email}, "name") or doc.approver_email
    orig_user = frappe.session.user
    try:
        frappe.set_user(acting_user)
        res = apply_decision(doc, "Approved" if action == "approve" else "Rejected", remarks, acting_user) if action in ("approve", "reject") else {"ok": False, "message": "Unknown action."}
    except Exception:
        frappe.log_error(frappe.get_traceback(), "JEW HRMS confirm_face_email_action failed")
        res = {"ok": False, "message": "Could not complete this action."}
    finally:
        frappe.set_user(orig_user)
    ok = bool(res.get("ok"))
    return _redirect(done=1, ok=1 if ok else 0, msg=res.get("message") or ("Done." if ok else "Could not complete this action."))
