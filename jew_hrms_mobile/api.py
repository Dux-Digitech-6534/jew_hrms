import json
import math
import traceback
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.auth import LoginManager
from frappe.utils import add_days, add_to_date, date_diff, get_datetime, get_system_timezone, getdate, now_datetime, today
from frappe.utils.file_manager import save_file

from jew_hrms_mobile.face_service import (
	decode_image,
	get_face_engine_status as face_engine_status,
	register_face,
	serialize_encoding,
	verify_face,
)


ADMIN_ROLES = {"Administrator", "System Manager", "HR Manager", "HR User", "JEW HRMS Admin", "JEW HRMS Owner"}
OWNER_ROLES = {"Administrator", "System Manager", "JEW HRMS Owner"}
HR_ROLES = {"HR Manager", "HR User", "Leave Approver", "JEW HRMS HR"}
APPROVER_ROLES = ADMIN_ROLES | HR_ROLES
EMPLOYEE_ROLES = {"Employee", "JEW HRMS Employee"}
REQUIRED_LEAVE_TYPES = {
	"CL": "Casual Leave",
	"PL": "Privilege / Paid Leave",
	"SL": "Sick Leave",
	"LWP": "Leave Without Pay",
}
APPROVAL_STATUSES = {
	"draft": "Draft",
	"pending_hr": "Pending HR Approval",
	"pending_admin": "Pending Admin Approval",
	"approved": "Approved",
	"rejected": "Rejected",
	"cancelled": "Cancelled",
}
POLICY_ACTIONS = {"Warn Only", "Regularization Required", "Mark Half Day", "Mark LWP", "Block Attendance"}


def _ok(data=None, message="success", code="success"):
	out = {"ok": True, "success": True, "message": message, "code": code, "error": None}
	if data is not None:
		out.update(data)
		out["data"] = data
	return out


def _fail(message, code="error", data=None):
	out = {"ok": False, "success": False, "message": message, "code": code, "error": code}
	if data is not None:
		out.update(data)
		out["data"] = data
	return out


def _attendance_logger():
	return frappe.logger("jew_hrms_mobile.attendance", allow_site=True)


def _log_attendance_debug(stage, context):
	safe = dict(context or {})
	safe["stage"] = stage
	_attendance_logger().info(json.dumps(safe, default=str, sort_keys=True))


def _roles(user=None):
	return set(frappe.get_roles(user or frappe.session.user))


def _has_any(roles, user=None):
	return bool(_roles(user).intersection(set(roles)))


def _is_admin(user=None):
	return _has_any(ADMIN_ROLES, user)


def _is_owner(user=None):
	return _has_any(OWNER_ROLES, user)


def _is_hr(user=None):
	return _has_any(HR_ROLES, user)


def _is_approver(user=None):
	return _has_any(APPROVER_ROLES, user)


def _require_login():
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)


def _require_admin():
	_require_login()
	if not _is_admin():
		frappe.throw(_("Permission denied"), frappe.PermissionError)


def _require_approver():
	_require_login()
	if not _is_approver():
		frappe.throw(_("Permission denied"), frappe.PermissionError)


def _require_owner():
	_require_login()
	if not _is_owner():
		frappe.throw(_("Permission denied"), frappe.PermissionError)


def _employee_fields():
	return {df.fieldname for df in frappe.get_meta("Employee").fields}


def _get_employee_for_user(user=None):
	user = user or frappe.session.user
	fields = _employee_fields()
	for fieldname in ("user_id", "prefered_email", "company_email", "personal_email"):
		if fieldname not in fields:
			continue
		name = frappe.db.get_value("Employee", {fieldname: user}, "name")
		if name:
			return frappe.get_doc("Employee", name)
	if frappe.db.exists("Employee", user):
		return frappe.get_doc("Employee", user)
	return None


def _get_current_employee():
	_require_login()
	employee = _get_employee_for_user()
	if not employee:
		frappe.throw(_("No Employee is mapped with this user. Please contact HR."), frappe.PermissionError)
	return employee


def _require_employee(employee=None):
	current = _get_current_employee()
	if employee and employee != current.name and not _is_admin():
		frappe.throw(_("Not permitted for this employee"), frappe.PermissionError)
	return frappe.get_doc("Employee", employee) if employee and employee != current.name else current


def _employee_payload(employee):
	fields = _employee_fields()
	def val(fieldname):
		return employee.get(fieldname) if fieldname in fields else None

	return {
		"employee": employee.name,
		"employee_id": employee.name,
		"employee_name": employee.employee_name or employee.name,
		"user_id": val("user_id"),
		"company": val("company"),
		"department": val("department"),
		"designation": val("designation"),
		"branch": val("branch"),
		"default_shift": val("default_shift"),
		"holiday_list": val("holiday_list"),
		"status": val("status") or "Active",
		"image": val("image"),
		"cell_number": val("cell_number"),
		"personal_email": val("personal_email"),
		"company_email": val("company_email"),
	}


def _haversine_m(lat1, lon1, lat2, lon2):
	lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
	radius_m = 6371000
	dlat = math.radians(lat2 - lat1)
	dlon = math.radians(lon2 - lon1)
	a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
	return 2 * radius_m * math.asin(math.sqrt(a))


def _active_face(employee):
	name = frappe.db.get_value("JEW Employee Face", {"employee": employee, "is_active": 1}, "name", order_by="modified desc")
	return frappe.get_doc("JEW Employee Face", name) if name else None


def _save_face_image(employee, face_image, attached_to_name=None):
	file_doc = save_file(
		f"{employee}-face.jpg",
		decode_image(face_image),
		"JEW Employee Face",
		attached_to_name or employee,
		is_private=1,
	)
	return file_doc.file_url


def _verify_face(employee, face_image):
	profile = _active_face(employee)
	if not profile:
		return False, {"code": "face_not_registered", "message": "Face is not registered."}
	if not face_image:
		return False, {"code": "no_face_detected", "message": "Start scan and keep your face inside the frame."}
	result = verify_face(employee, face_image, profile.face_encoding or profile.face_template or "[]")
	return bool(result.get("ok")), result


def _assigned_locations(employee):
	assignments = frappe.get_list(
		"JEW Employee Location Assignment",
		filters={"employee": employee, "is_active": 1},
		fields=["name", "location", "radius_meter"],
		ignore_permissions=True,
	)
	locations = []
	for assignment in assignments:
		location = frappe.db.get_value(
			"JEW Attendance Location",
			{"name": assignment.location, "is_active": 1},
			["name", "location_name", "latitude", "longitude", "default_radius_meter"],
			as_dict=True,
		)
		if not location:
			continue
		location.radius_meter = assignment.radius_meter or location.default_radius_meter or 100
		location.assignment = assignment.name
		locations.append(location)
	return locations


def _geofence_result(employee, latitude, longitude):
	if latitude in (None, "") or longitude in (None, ""):
		return {"allowed": False, "code": "outside_location", "message": "Location is required."}
	locations = _assigned_locations(employee)
	if not locations:
		return {"allowed": False, "code": "location_not_assigned", "message": "Location not assigned. Please contact admin."}
	nearest = None
	for location in locations:
		distance = _haversine_m(latitude, longitude, location.latitude, location.longitude)
		item = {
			"name": location.name,
			"location_name": location.location_name,
			"distance_meter": round(distance, 2),
			"radius_meter": location.radius_meter,
			"allowed": distance <= float(location.radius_meter),
		}
		if nearest is None or item["distance_meter"] < nearest["distance_meter"]:
			nearest = item
	if nearest and nearest["allowed"]:
		return {"allowed": True, "code": "success", "nearest_location": nearest}
	return {"allowed": False, "code": "outside_location", "message": "Outside assigned geofence.", "nearest_location": nearest}


def _today_checkins(employee):
	return frappe.get_list(
		"Employee Checkin",
		filters={"employee": employee, "time": ["between", [f"{today()} 00:00:00", f"{today()} 23:59:59"]]},
		fields=["name", "log_type", "time"],
		order_by="time asc",
		ignore_permissions=True,
	)


def _checkin_datetime(timestamp=None):
	if not timestamp:
		return now_datetime()
	dt = get_datetime(timestamp)
	if getattr(dt, "tzinfo", None):
		dt = dt.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	return dt


def _bool(value):
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	return bool(value)


def _parse_time(value, default=None):
	if value in (None, ""):
		return default
	if isinstance(value, time):
		return value
	if isinstance(value, timedelta):
		seconds = int(value.total_seconds())
		return time(seconds // 3600 % 24, seconds // 60 % 60, seconds % 60)
	if isinstance(value, datetime):
		return value.time().replace(microsecond=0)
	text = str(value).strip()
	for fmt in ("%H:%M:%S", "%H:%M"):
		try:
			return datetime.strptime(text, fmt).time()
		except ValueError:
			pass
	return default


def _minutes_between(start, end):
	if not start or not end:
		return 0
	start_dt = get_datetime(start)
	end_dt = get_datetime(end)
	if end_dt < start_dt:
		end_dt += timedelta(days=1)
	return round((end_dt - start_dt).total_seconds() / 60, 2)


def _set_if_has(doc, fieldname, value):
	if doc.meta.has_field(fieldname):
		doc.set(fieldname, value)


def _approval_field_exists(fieldname="jew_hrms_approval_status"):
	return frappe.get_meta("Leave Application").has_field(fieldname)


def _leave_approval_status(doc):
	if doc.meta.has_field("jew_hrms_approval_status") and doc.get("jew_hrms_approval_status"):
		return doc.get("jew_hrms_approval_status")
	if doc.status == "Approved":
		return APPROVAL_STATUSES["approved"]
	if doc.status == "Rejected":
		return APPROVAL_STATUSES["rejected"]
	if doc.status == "Cancelled":
		return APPROVAL_STATUSES["cancelled"]
	return APPROVAL_STATUSES["pending_hr"]


def _safe_insert_notification(employee=None, title=None, message=None, notification_type="Info", ref_doctype=None, ref_name=None):
	if not frappe.db.exists("DocType", "JEW HRMS Notification"):
		return None
	try:
		doc = frappe.new_doc("JEW HRMS Notification")
		doc.employee = employee
		doc.title = title or "JEW HRMS"
		doc.message = message or ""
		doc.notification_type = notification_type
		doc.reference_doctype = ref_doctype
		doc.reference_name = ref_name
		doc.is_read = 0
		doc.insert(ignore_permissions=True)
		return doc.name
	except Exception:
		frappe.log_error(traceback.format_exc(), "JEW HRMS notification failed")
		return None


def _month_range(date_value=None):
	base = getdate(date_value or today())
	start = base.replace(day=1)
	next_month = start.replace(year=start.year + (1 if start.month == 12 else 0), month=1 if start.month == 12 else start.month + 1)
	return start, add_days(next_month, -1)


def _attendance_sequence(employee, requested=None):
	requested = (requested or "").upper().strip()
	if requested and requested not in ("IN", "OUT"):
		return None, "validation_error", "Invalid attendance type."
	rows = _today_checkins(employee)
	has_in = any(row.log_type == "IN" for row in rows)
	has_out = any(row.log_type == "OUT" for row in rows)
	if requested:
		if requested == "IN" and has_in:
			return None, "already_marked_in", "Mark In is already completed for today."
		if requested == "OUT" and not has_in:
			return None, "mark_in_required", "Please mark in before marking out."
		if requested == "OUT" and has_out:
			return None, "already_marked_out", "Mark Out is already completed for today."
		return requested, None, None
	if not has_in:
		return "IN", None, None
	if not has_out:
		return "OUT", None, None
	return None, "already_marked_out", "Attendance is already completed for today."


def _attendance_status_payload(employee):
	checkins = _today_checkins(employee.name)
	checkin = next((row.time for row in checkins if row.log_type == "IN"), None)
	checkout = next((row.time for row in reversed(checkins) if row.log_type == "OUT"), None)
	checkin_label = get_datetime(checkin).strftime("%H:%M") if checkin else None
	checkout_label = get_datetime(checkout).strftime("%H:%M") if checkout else None
	face = _active_face(employee.name)
	locations = _assigned_locations(employee.name)
	completed = bool(checkin and checkout)
	location_state = "not_assigned" if not locations else "unknown"
	return {
		"employee": employee.name,
		"employee_id": employee.name,
		"employee_name": employee.employee_name or employee.name,
		"date": today(),
		"checkins": checkins,
		"checkin": checkin,
		"checkout": checkout,
		"in_time": checkin,
		"out_time": checkout,
		"status": "completed" if completed else ("in" if checkin else "not_marked"),
		"status_label": "Attendance completed" if completed else (f"Marked In at {checkin_label}" if checkin_label else "Not marked today"),
		"can_mark_in": bool(face and locations and not checkin),
		"can_mark_out": bool(face and locations and checkin and not checkout),
		"face_registered": bool(face),
		"location_assigned": bool(locations),
		"location_status": location_state,
		"face_status": {"registered": bool(face), "last_updated_on": face.last_updated_on if face else None},
		"location_details": {"assigned": len(locations), "ready": bool(locations), "status": location_state},
		"in_time_label": checkin_label,
		"out_time_label": checkout_label,
	}


def _get_active_policy(employee=None, shift=None, required=False):
	if not frappe.db.exists("DocType", "JEW Shift Attendance Policy"):
		if required:
			frappe.throw(_("Shift policy is not configured. Please contact HR/Admin."))
		return None
	filters = {"is_active": 1}
	if shift:
		name = frappe.db.get_value("JEW Shift Attendance Policy", {**filters, "shift_name": shift}, "name")
		if name:
			return frappe.get_doc("JEW Shift Attendance Policy", name)
	name = frappe.db.get_value("JEW Shift Attendance Policy", filters, "name", order_by="modified desc")
	if name:
		return frappe.get_doc("JEW Shift Attendance Policy", name)
	if required:
		frappe.throw(_("Shift policy is not configured. Please contact HR/Admin."))
	return None


def _policy_payload(doc):
	if not doc:
		return None
	fields = [
		"name",
		"shift_name",
		"shift_start_time",
		"shift_end_time",
		"break_minutes",
		"full_day_minimum_hours",
		"half_day_minimum_hours",
		"late_coming_grace_minutes",
		"early_going_grace_minutes",
		"max_late_coming_allowed_per_month",
		"max_early_going_allowed_per_month",
		"max_short_hours_allowed_per_month",
		"action_after_late_limit",
		"action_after_early_limit",
		"action_after_short_hours_limit",
		"is_active",
	]
	return {field: doc.get(field) for field in fields}


def _issue_count(employee, issue_type, date_value=None):
	if not frappe.db.exists("DocType", "JEW Attendance Regularization"):
		return 0
	start, end = _month_range(date_value)
	return frappe.db.count(
		"JEW Attendance Regularization",
		{"employee": employee, "issue_type": issue_type, "attendance_date": ["between", [start, end]], "status": ["!=", "Rejected"]},
	)


def _evaluate_attendance_policy(employee, attendance_date=None, in_time=None, out_time=None, shift=None):
	policy = _get_active_policy(employee=employee, shift=shift)
	if not policy:
		return {"ok": False, "message": "Shift policy is not configured. Please contact HR/Admin.", "issues": [], "status": "Shift Policy Missing"}
	attendance_date = getdate(attendance_date or today())
	issues = []
	policy_shift_start = _parse_time(policy.shift_start_time, time(9, 30))
	policy_shift_end = _parse_time(policy.shift_end_time, time(18, 30))
	in_dt = get_datetime(in_time) if in_time else None
	out_dt = get_datetime(out_time) if out_time else None
	if in_dt:
		allowed_in = datetime.combine(attendance_date, policy_shift_start) + timedelta(minutes=int(policy.late_coming_grace_minutes or 0))
		if in_dt > allowed_in:
			count = _issue_count(employee, "Late Coming", attendance_date) + 1
			limit = int(policy.max_late_coming_allowed_per_month or 0)
			action = policy.action_after_late_limit or "Regularization Required"
			issues.append({"issue_type": "Late Coming", "count": count, "limit": limit, "action": action if not limit or count > limit else "Warn Only"})
	if out_dt:
		allowed_out = datetime.combine(attendance_date, policy_shift_end) - timedelta(minutes=int(policy.early_going_grace_minutes or 0))
		if out_dt < allowed_out:
			count = _issue_count(employee, "Early Going", attendance_date) + 1
			limit = int(policy.max_early_going_allowed_per_month or 0)
			action = policy.action_after_early_limit or "Regularization Required"
			issues.append({"issue_type": "Early Going", "count": count, "limit": limit, "action": action if not limit or count > limit else "Warn Only"})
	if in_dt and not out_dt:
		issues.append({"issue_type": "Missing Mark Out", "count": 1, "limit": 0, "action": "Regularization Required"})
	working_hours = 0
	if in_dt and out_dt:
		working_minutes = _minutes_between(in_dt, out_dt) - int(policy.break_minutes or 0)
		working_hours = max(0, round(working_minutes / 60, 2))
		full_day = float(policy.full_day_minimum_hours or 8)
		half_day = float(policy.half_day_minimum_hours or 4)
		if working_hours < full_day:
			count = _issue_count(employee, "Short Hours", attendance_date) + 1
			limit = int(policy.max_short_hours_allowed_per_month or 0)
			action = policy.action_after_short_hours_limit or "Regularization Required"
			issues.append({
				"issue_type": "Short Hours",
				"count": count,
				"limit": limit,
				"action": action if not limit or count > limit else "Warn Only",
				"working_hours": working_hours,
				"attendance_status": "Half Day / Short Hours" if working_hours >= half_day else "Absent / HR Review",
			})
	status = "Present" if not issues else "Regularization Pending"
	blocked = any(issue.get("action") == "Block Attendance" for issue in issues)
	return {"ok": not blocked, "policy": _policy_payload(policy), "issues": issues, "status": status, "working_hours": working_hours}


def _create_regularization(employee, attendance_date=None, issue_type=None, in_time=None, out_time=None, working_hours=None, policy_action=None, remarks=None, linked_attendance=None, linked_employee_checkin=None):
	if not frappe.db.exists("DocType", "JEW Attendance Regularization"):
		return None
	if not employee or not issue_type:
		return None
	attendance_date = getdate(attendance_date or today())
	existing = frappe.db.get_value(
		"JEW Attendance Regularization",
		{"employee": employee, "attendance_date": attendance_date, "issue_type": issue_type, "status": "Pending"},
		"name",
	)
	doc = frappe.get_doc("JEW Attendance Regularization", existing) if existing else frappe.new_doc("JEW Attendance Regularization")
	doc.employee = employee
	doc.attendance_date = attendance_date
	doc.issue_type = issue_type
	doc.in_time = in_time
	doc.out_time = out_time
	doc.working_hours = working_hours
	doc.policy_action = policy_action or "Regularization Required"
	doc.status = "Pending"
	doc.requested_by = frappe.session.user
	doc.created_by = frappe.session.user
	doc.remarks = remarks
	doc.linked_attendance = linked_attendance
	doc.linked_employee_checkin = linked_employee_checkin
	doc.save(ignore_permissions=True)
	_safe_insert_notification(employee, "Regularization pending", f"{issue_type} needs HR review.", "Warning", "JEW Attendance Regularization", doc.name)
	return doc.name


@frappe.whitelist(allow_guest=True)
def login(email=None, password=None):
	if not email or not password:
		return _fail("Email and password are required", "validation_error")
	try:
		manager = LoginManager()
		manager.authenticate(user=email, pwd=password)
		manager.post_login()
		return _ok({"user": frappe.session.user, "roles": list(_roles())}, "Login successful")
	except Exception:
		frappe.local.response.http_status_code = 401
		return _fail("Invalid login", "permission_denied")


@frappe.whitelist()
def get_session_user():
	_require_login()
	employee = _get_employee_for_user()
	return _ok({"user": frappe.session.user, "roles": list(_roles()), "employee": _employee_payload(employee) if employee else None})


@frappe.whitelist()
def capabilities():
	_require_login()
	employee = _get_employee_for_user()
	is_admin = _is_admin()
	is_approver = _is_approver()
	is_owner = _is_owner()
	is_hr = _is_hr()
	has_employee = bool(employee)
	return {
		"can_mark_attendance": has_employee,
		"can_apply_leave": has_employee,
		"can_register_face": is_admin,
		"can_manage_locations": is_admin,
		"can_approve_leave": is_approver,
		"can_view_admin": is_admin or is_approver,
		"can_manage_leave_policy": is_admin or is_hr,
		"can_manage_shift_policy": is_admin or is_hr,
		"can_manage_regularization": is_admin or is_hr,
		"is_admin": is_admin,
		"is_hr": is_hr,
		"is_owner": is_owner,
		"approval_level": "owner" if is_owner else ("hr" if is_hr else "employee"),
	}


@frappe.whitelist()
def get_employee_profile(employee=None):
	doc = _require_employee(employee)
	payload = _employee_payload(doc)
	payload["face_status"] = get_face_status(doc.name)
	payload["locations"] = get_employee_locations(doc.name).get("locations", [])
	return _ok({"employee_profile": payload})


@frappe.whitelist()
def get_dashboard():
	employee = _get_current_employee()
	status = _attendance_status_payload(employee)
	leave_balance = []
	if frappe.db.exists("DocType", "Leave Allocation"):
		leave_balance = frappe.get_list(
			"Leave Allocation",
			filters={"employee": employee.name, "docstatus": 1, "to_date": [">=", today()]},
			fields=["leave_type", "total_leaves_allocated", "unused_leaves"],
			limit_page_length=10,
			ignore_permissions=True,
		)
	return _ok({
		"employee": _employee_payload(employee),
		"today": {
			"date": today(),
			"checkin": status["checkin"],
			"checkout": status["checkout"],
			"status": "Completed" if status["status"] == "completed" else ("In" if status["status"] == "in" else "Not Marked"),
		},
		"attendance_status": status,
		"shift": employee.get("default_shift"),
		"leave_balance": leave_balance,
		"face_status": status["face_status"],
		"location_status": status["location_details"],
		"location_state": status["location_status"],
	})


@frappe.whitelist()
def get_today_attendance_status():
	employee = _get_current_employee()
	return _ok(_attendance_status_payload(employee))


@frappe.whitelist()
def mark_attendance(type=None, image_data=None, latitude=None, longitude=None, accuracy=None, face_image=None, timestamp=None):
	context = {
		"user": frappe.session.user,
		"requested_type": (type or "").upper().strip(),
		"latitude": latitude,
		"longitude": longitude,
		"accuracy": accuracy,
		"has_face_image": bool(face_image or image_data),
	}
	try:
		employee = _get_current_employee()
		context.update({"employee": employee.name, "employee_name": employee.employee_name})
	except Exception:
		context["exception"] = traceback.format_exc()
		_log_attendance_debug("employee_resolution_failed", context)
		return _fail("Employee not linked to current user.", "employee_not_linked")

	try:
		if employee.get("status") and employee.status != "Active":
			_log_attendance_debug("inactive_employee", context)
			return _fail("Employee is inactive.", "permission_denied")

		log_type, sequence_code, sequence_message = _attendance_sequence(employee.name, type)
		context["resolved_log_type"] = log_type
		if not log_type:
			_log_attendance_debug("attendance_sequence_blocked", context)
			return _fail(sequence_message, sequence_code)

		face_profile = _active_face(employee.name)
		context["face_template_exists"] = bool(face_profile)
		if not face_profile:
			_log_attendance_debug("face_template_missing", context)
			return _fail("Face is not registered.", "face_not_registered")

		image_payload = face_image or image_data
		if not image_payload:
			_log_attendance_debug("face_image_missing", context)
			return _fail("No face detected. Please keep your face inside the frame.", "no_face_detected")

		matched, face_result = _verify_face(employee.name, image_payload)
		context.update({
			"face_detected_count": face_result.get("face_detected_count"),
			"face_distance": face_result.get("distance"),
			"threshold": face_result.get("threshold"),
			"matched": bool(matched),
			"face_code": face_result.get("code"),
		})
		if not matched:
			_log_attendance_debug("face_verification_failed", context)
			return _fail(face_result.get("message", "Face did not match. Please scan again with a clear face image."), face_result.get("code", "face_not_matched"), {"face": face_result})

		locations = _assigned_locations(employee.name)
		context["assigned_locations_count"] = len(locations)
		if not locations:
			_log_attendance_debug("location_not_assigned", context)
			return _fail("Location not assigned. Please contact admin.", "location_not_assigned")

		geo = _geofence_result(employee.name, latitude, longitude)
		nearest = geo.get("nearest_location") or {}
		context.update({
			"nearest_location": nearest.get("location_name") or nearest.get("name"),
			"distance_meter": nearest.get("distance_meter"),
			"radius_meter": nearest.get("radius_meter"),
			"geofence_allowed": bool(geo.get("allowed")),
			"geofence_code": geo.get("code"),
		})
		if not geo.get("allowed"):
			_log_attendance_debug("geofence_failed", context)
			return _fail(geo.get("message", "You are outside assigned location."), geo.get("code", "outside_location"), {"geofence": geo})

		checkin_time = _checkin_datetime(timestamp)
		duplicate = frappe.db.exists(
			"Employee Checkin",
			{"employee": employee.name, "log_type": log_type, "time": ["between", [add_to_date(checkin_time, minutes=-2), add_to_date(checkin_time, minutes=2)]]},
		)
		if duplicate:
			_log_attendance_debug("duplicate_checkin_blocked", context)
			return _fail(f"Mark {log_type.title()} already exists.", f"already_marked_{log_type.lower()}")

		try:
			doc = frappe.new_doc("Employee Checkin")
			doc.employee = employee.name
			doc.employee_name = employee.employee_name
			doc.log_type = log_type
			doc.time = checkin_time
			if doc.meta.has_field("device_id"):
				doc.device_id = "JEW HRMS Mobile"
			for fieldname, value in {
				"custom_latitude": latitude,
				"latitude": latitude,
				"custom_longitude": longitude,
				"longitude": longitude,
				"custom_accuracy": accuracy,
				"accuracy": accuracy,
			}.items():
				if value not in (None, "") and doc.meta.has_field(fieldname):
					doc.set(fieldname, value)
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			context["employee_checkin"] = doc.name
			_log_attendance_debug("employee_checkin_created", context)
		except Exception:
			frappe.db.rollback()
			context["exception"] = traceback.format_exc()
			_log_attendance_debug("employee_checkin_exception", context)
			frappe.log_error(context["exception"], "JEW HRMS mark_attendance Employee Checkin failed")
			return _fail("Unable to create Employee Checkin. Please contact admin.", "employee_checkin_failed")

		status = _attendance_status_payload(employee)
		policy_result = None
		if log_type == "OUT":
			policy_result = _evaluate_attendance_policy(employee.name, today(), status.get("in_time"), status.get("out_time"), employee.get("default_shift"))
			for issue in policy_result.get("issues", []):
				if issue.get("action") != "Warn Only":
					_create_regularization(
						employee.name,
						today(),
						issue.get("issue_type"),
						status.get("in_time"),
						status.get("out_time"),
						policy_result.get("working_hours"),
						issue.get("action"),
						linked_employee_checkin=doc.name,
					)
		message = "Marked In successfully." if log_type == "IN" else "Marked Out successfully."
		return _ok({"employee_checkin": doc.name, "log_type": log_type, "time": doc.time, "face": face_result, "geofence": geo, "policy_result": policy_result, "attendance_status": status, **status}, message, "success")
	except Exception:
		frappe.db.rollback()
		context["exception"] = traceback.format_exc()
		_log_attendance_debug("mark_attendance_exception", context)
		frappe.log_error(context["exception"], "JEW HRMS mark_attendance failed")
		return _fail("Unable to create Employee Checkin. Please contact admin.", "employee_checkin_failed")


@frappe.whitelist()
def get_attendance_history(from_date=None, to_date=None, limit=60):
	employee = _get_current_employee()
	to_date = getdate(to_date or today())
	from_date = getdate(from_date or add_days(to_date, -30))
	checkins = frappe.get_list(
		"Employee Checkin",
		filters={"employee": employee.name, "time": ["between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"]]},
		fields=["name", "time", "log_type"],
		order_by="time desc",
		limit_page_length=int(limit or 60),
		ignore_permissions=True,
	)
	attendance = frappe.get_list(
		"Attendance",
		filters={"employee": employee.name, "attendance_date": ["between", [from_date, to_date]]},
		fields=["name", "attendance_date", "status", "working_hours", "shift"],
		order_by="attendance_date desc",
		ignore_permissions=True,
	)
	return _ok({"checkins": checkins, "attendance": attendance})


@frappe.whitelist()
def get_face_status(employee=None):
	doc = _require_employee(employee) if employee else _get_current_employee()
	face = _active_face(doc.name)
	return {"employee": doc.name, "registered": bool(face), "last_updated_on": face.last_updated_on if face else None}


@frappe.whitelist()
def get_face_engine_status():
	_require_login()
	return face_engine_status()


@frappe.whitelist()
def register_employee_face(employee=None, face_image=None):
	_require_admin()
	if not employee or not frappe.db.exists("Employee", employee):
		return _fail("Employee is required.", "validation_error")
	if not face_image:
		return _fail("Capture face before saving template.", "no_face_detected")
	face_result = register_face(employee, face_image)
	if not face_result.get("ok"):
		return _fail(face_result.get("message", "Face template could not be created."), face_result.get("code", "face_not_matched"), {"face": face_result})
	for old in frappe.get_list("JEW Employee Face", filters={"employee": employee, "is_active": 1}, pluck="name", ignore_permissions=True):
		frappe.db.set_value("JEW Employee Face", old, "is_active", 0)
	doc = frappe.new_doc("JEW Employee Face")
	doc.employee = employee
	doc.user = frappe.db.get_value("Employee", employee, "user_id")
	doc.face_encoding = serialize_encoding(face_result["encoding"])
	doc.face_template = doc.face_encoding
	doc.is_active = 1
	doc.registered_by = frappe.session.user
	doc.registered_on = now_datetime()
	doc.last_updated_on = now_datetime()
	doc.insert(ignore_permissions=True)
	doc.face_image = _save_face_image(employee, face_image, doc.name)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return _ok({"face": doc.name, "employee": employee}, "Face registered")


@frappe.whitelist()
def update_employee_face(employee=None, face_image=None):
	return register_employee_face(employee=employee, face_image=face_image)


@frappe.whitelist()
def get_locations():
	_require_admin()
	return _ok({"locations": frappe.get_list("JEW Attendance Location", fields=["name", "location_name", "latitude", "longitude", "default_radius_meter", "is_active"], order_by="modified desc")})


@frappe.whitelist()
def save_location(name=None, location_name=None, latitude=None, longitude=None, default_radius_meter=100, is_active=1):
	_require_admin()
	doc = frappe.get_doc("JEW Attendance Location", name) if name else frappe.new_doc("JEW Attendance Location")
	doc.location_name = location_name
	doc.latitude = latitude
	doc.longitude = longitude
	doc.default_radius_meter = int(default_radius_meter or 100)
	doc.is_active = int(is_active or 0)
	doc.save()
	frappe.db.commit()
	return _ok({"location": doc.name}, "Location saved")


@frappe.whitelist()
def delete_location(name=None):
	_require_admin()
	if not name:
		return _fail("Location is required.", "validation_error")
	frappe.delete_doc("JEW Attendance Location", name)
	frappe.db.commit()
	return _ok(message="Location deleted")


@frappe.whitelist()
def assign_employee_location(employee=None, location=None, radius_meter=None):
	_require_admin()
	if not employee or not location:
		return _fail("Employee and location are required.", "validation_error")
	name = frappe.db.get_value("JEW Employee Location Assignment", {"employee": employee, "location": location}, "name")
	doc = frappe.get_doc("JEW Employee Location Assignment", name) if name else frappe.new_doc("JEW Employee Location Assignment")
	doc.employee = employee
	doc.location = location
	doc.radius_meter = int(radius_meter or frappe.db.get_value("JEW Attendance Location", location, "default_radius_meter") or 100)
	doc.is_active = 1
	doc.save()
	frappe.db.commit()
	return _ok({"assignment": doc.name}, "Location assigned")


@frappe.whitelist()
def remove_employee_location(assignment=None, employee=None, location=None):
	_require_admin()
	name = assignment or frappe.db.get_value("JEW Employee Location Assignment", {"employee": employee, "location": location}, "name")
	if not name:
		return _fail("Assignment is required.", "validation_error")
	frappe.db.set_value("JEW Employee Location Assignment", name, "is_active", 0)
	frappe.db.commit()
	return _ok(message="Location assignment removed")


@frappe.whitelist()
def get_employee_locations(employee=None):
	doc = _require_employee(employee) if employee else _get_current_employee()
	return _ok({"employee": doc.name, "locations": _assigned_locations(doc.name)})


@frappe.whitelist()
def get_leave_dashboard():
	employee = _get_current_employee()
	allocations = frappe.get_list("Leave Allocation", filters={"employee": employee.name, "docstatus": 1, "to_date": [">=", today()]}, fields=["leave_type", "total_leaves_allocated", "unused_leaves"], ignore_permissions=True)
	recent = get_my_leaves(limit=5).get("leaves", [])
	return _ok({"balances": allocations, "recent": recent})


@frappe.whitelist()
def get_leave_types():
	leave_types = frappe.get_list("Leave Type", fields=["name", "leave_type_name", "is_lwp"], order_by="name asc")
	if not leave_types:
		return _fail("Leave type is not configured. Please contact HR/Admin.", "leave_type_not_configured")
	for row in leave_types:
		if row.name in REQUIRED_LEAVE_TYPES:
			row.leave_type_name = REQUIRED_LEAVE_TYPES[row.name]
	return _ok({"leave_types": leave_types})


@frappe.whitelist()
def get_leave_policy():
	_require_admin()
	return get_leave_types()


@frappe.whitelist()
def save_leave_type(name=None, leave_type_name=None, is_lwp=0):
	_require_admin()
	if not name and not leave_type_name:
		return _fail("Leave type is required.", "validation_error")
	name = (name or leave_type_name or "").strip()
	doc = frappe.get_doc("Leave Type", name) if frappe.db.exists("Leave Type", name) else frappe.new_doc("Leave Type")
	if doc.is_new():
		doc.leave_type_name = name
	else:
		doc.leave_type_name = leave_type_name or doc.leave_type_name or name
	if doc.meta.has_field("is_lwp"):
		doc.is_lwp = int(_bool(is_lwp) or name.upper() == "LWP")
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return _ok({"leave_type": doc.name}, "Leave type saved")


def _leave_overlap_exists(employee, from_dt, to_dt, exclude=None):
	filters = {
		"employee": employee,
		"from_date": ["<=", to_dt],
		"to_date": [">=", from_dt],
		"status": ["not in", ["Rejected", "Cancelled"]],
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	return bool(frappe.db.exists("Leave Application", filters))


def _has_leave_allocation(employee, leave_type, from_dt, to_dt):
	if str(leave_type).upper() == "LWP":
		return True
	leave_type_doc = frappe.get_doc("Leave Type", leave_type)
	if leave_type_doc.get("is_lwp"):
		return True
	return bool(frappe.db.exists("Leave Allocation", {"employee": employee, "leave_type": leave_type, "docstatus": 1, "from_date": ["<=", from_dt], "to_date": [">=", to_dt]}))


@frappe.whitelist()
def apply_leave(employee=None, leave_type=None, from_date=None, to_date=None, half_day=0, half_day_date=None, half_day_type=None, reason=None, attachment=None):
	is_admin = _is_admin()
	current_employee = None if employee and is_admin else _get_current_employee()
	if employee and current_employee and employee != current_employee.name and not is_admin:
		return _fail("You do not have permission.", "permission_denied")
	if employee and is_admin:
		employee_doc = frappe.get_doc("Employee", employee)
	elif current_employee:
		employee_doc = current_employee
	else:
		return _fail("Employee is required.", "validation_error")
	if not all([leave_type, from_date, to_date]):
		return _fail("Leave type, from date and to date are required.", "validation_error")
	if employee_doc.get("status") and employee_doc.status != "Active":
		return _fail("Employee is inactive. Please contact HR.", "permission_denied")
	if not frappe.db.exists("Leave Type", leave_type):
		return _fail("Leave type is not configured. Please contact HR/Admin.", "leave_type_not_configured")
	try:
		from_dt = getdate(from_date)
		to_dt = getdate(to_date)
		half_dt = getdate(half_day_date) if half_day_date else None
	except Exception:
		return _fail("Invalid leave dates.", "validation_error")
	if from_dt > to_dt:
		return _fail("From date cannot be after To date.", "validation_error")
	if not is_admin and from_dt < getdate(today()):
		return _fail("Past date leave is not allowed for employees. Please contact HR.", "past_date_not_allowed")
	is_half_day = _bool(half_day)
	if is_half_day and not half_dt:
		return _fail("Half Day Date is required.", "validation_error")
	if half_dt and (half_dt < from_dt or half_dt > to_dt):
		return _fail("Half Day Date must be between From Date and To Date.", "validation_error")
	if _leave_overlap_exists(employee_doc.name, from_dt, to_dt):
		return _fail("Leave dates overlap with an existing leave.", "leave_overlap")
	if not _has_leave_allocation(employee_doc.name, leave_type, from_dt, to_dt):
		return _fail("Leave allocation not found. Please contact HR.", "leave_allocation_not_found")
	try:
		doc = frappe.new_doc("Leave Application")
		doc.employee = employee_doc.name
		doc.employee_name = employee_doc.employee_name
		if doc.meta.has_field("company"):
			doc.company = employee_doc.get("company")
		doc.leave_type = leave_type
		doc.from_date = from_dt
		doc.to_date = to_dt
		doc.half_day = int(is_half_day)
		if is_half_day:
			_set_if_has(doc, "half_day_date", half_dt)
			_set_if_has(doc, "total_leave_days", 0.5)
			_set_if_has(doc, "custom_half_day_type", half_day_type or "First Half")
		doc.description = reason
		doc.status = "Open"
		if doc.meta.has_field("jew_hrms_approval_status"):
			doc.jew_hrms_approval_status = APPROVAL_STATUSES["pending_admin"] if _is_owner() else APPROVAL_STATUSES["pending_hr"]
		doc.insert(ignore_permissions=True)
		if attachment and doc.meta.has_field("attachment"):
			doc.attachment = attachment
			doc.save(ignore_permissions=True)
		_safe_insert_notification(employee_doc.name, "Leave submitted", f"{leave_type} leave is pending approval.", "Info", "Leave Application", doc.name)
		message = "Leave request submitted successfully."
		frappe.db.commit()
		return _ok({"name": doc.name, "leave_application": doc.name, "docstatus": doc.docstatus, "approval_status": _leave_approval_status(doc)}, message)
	except frappe.PermissionError:
		frappe.db.rollback()
		return _fail("You do not have permission.", "permission_denied")
	except Exception as exc:
		frappe.db.rollback()
		text = str(exc)
		if "Leave Allocation" in text or "allocation" in text.lower():
			return _fail("Leave allocation not found. Please contact HR.", "leave_allocation_not_found")
		if "Leave Type" in text:
			return _fail("Leave type is not configured.", "leave_type_not_configured")
		if "Employee" in text and "not" in text.lower():
			return _fail("Employee not linked. Please contact HR.", "employee_not_linked")
		frappe.log_error(traceback.format_exc(), "JEW HRMS apply_leave failed")
		return _fail("Unable to submit leave. Please contact HR.", "setup_incomplete")


@frappe.whitelist()
def get_my_leaves(limit=50):
	employee = _get_current_employee()
	fields = ["name", "leave_type", "from_date", "to_date", "status", "description", "half_day"]
	if _approval_field_exists():
		fields.extend(["jew_hrms_approval_status", "reject_reason"])
	leaves = frappe.get_list("Leave Application", filters={"employee": employee.name}, fields=fields, order_by="from_date desc", limit_page_length=int(limit or 50), ignore_permissions=True)
	for leave in leaves:
		leave.approval_status = leave.get("jew_hrms_approval_status") or leave.status
	return _ok({"leaves": leaves})


@frappe.whitelist()
def cancel_leave(name=None):
	employee = _get_current_employee()
	doc = frappe.get_doc("Leave Application", name)
	if doc.employee != employee.name and not _is_admin():
		frappe.throw(_("Permission denied"), frappe.PermissionError)
	if doc.docstatus == 1:
		doc.cancel()
	else:
		doc.status = "Cancelled"
		_set_if_has(doc, "jew_hrms_approval_status", APPROVAL_STATUSES["cancelled"])
		doc.save()
	frappe.db.commit()
	return _ok(message="Leave cancelled")


@frappe.whitelist()
def get_pending_leaves():
	_require_approver()
	if _is_owner():
		filters = {"jew_hrms_approval_status": APPROVAL_STATUSES["pending_admin"]} if _approval_field_exists() else {"status": "Open"}
	elif _is_hr():
		filters = {"jew_hrms_approval_status": APPROVAL_STATUSES["pending_hr"]} if _approval_field_exists() else {"status": "Open"}
	else:
		filters = {"status": "Open"}
	fields = ["name", "employee", "employee_name", "leave_type", "from_date", "to_date", "description", "status", "half_day"]
	if _approval_field_exists():
		fields.extend(["jew_hrms_approval_status", "reject_reason"])
	leaves = frappe.get_list("Leave Application", filters=filters, fields=fields, order_by="from_date asc", limit_page_length=100)
	for leave in leaves:
		leave.approval_status = leave.get("jew_hrms_approval_status") or leave.status
	return _ok({"leaves": leaves})


@frappe.whitelist()
def approve_leave(name=None, remarks=None):
	_require_approver()
	doc = frappe.get_doc("Leave Application", name)
	current_status = _leave_approval_status(doc)
	if current_status == APPROVAL_STATUSES["pending_hr"] and _is_hr():
		_set_if_has(doc, "jew_hrms_approval_status", APPROVAL_STATUSES["pending_admin"])
		_set_if_has(doc, "hr_approved_by", frappe.session.user)
		_set_if_has(doc, "hr_approved_on", now_datetime())
		doc.status = "Open"
		doc.save(ignore_permissions=True)
		_safe_insert_notification(doc.employee, "Leave approved by HR", "Pending admin final approval.", "Info", "Leave Application", doc.name)
		frappe.db.commit()
		return _ok({"approval_status": APPROVAL_STATUSES["pending_admin"]}, "Leave approved by HR")
	if current_status in (APPROVAL_STATUSES["pending_admin"], APPROVAL_STATUSES["pending_hr"]) and _is_owner():
		_set_if_has(doc, "jew_hrms_approval_status", APPROVAL_STATUSES["approved"])
		_set_if_has(doc, "final_approved_by", frappe.session.user)
		_set_if_has(doc, "final_approved_on", now_datetime())
		doc.status = "Approved"
		try:
			if doc.docstatus == 0:
				doc.submit()
			else:
				doc.save(ignore_permissions=True)
		except Exception:
			doc.save(ignore_permissions=True)
		_safe_insert_notification(doc.employee, "Leave approved by Admin", "Leave request approved.", "Info", "Leave Application", doc.name)
		frappe.db.commit()
		return _ok({"approval_status": APPROVAL_STATUSES["approved"]}, "Leave approved")
	return _fail("You do not have permission.", "permission_denied")


@frappe.whitelist()
def reject_leave(name=None, remarks=None):
	_require_approver()
	if not remarks:
		return _fail("Reject Reason required.", "reject_reason_required")
	doc = frappe.get_doc("Leave Application", name)
	doc.status = "Rejected"
	_set_if_has(doc, "jew_hrms_approval_status", APPROVAL_STATUSES["rejected"])
	_set_if_has(doc, "reject_reason", remarks)
	if remarks:
		doc.description = ((doc.description or "") + f"\n\nRejection remarks: {remarks}").strip()
	doc.save(ignore_permissions=True)
	_safe_insert_notification(doc.employee, "Leave rejected", remarks, "Warning", "Leave Application", doc.name)
	frappe.db.commit()
	return _ok(message="Leave rejected")


@frappe.whitelist()
def get_notifications():
	_require_login()
	employee = _get_employee_for_user()
	items = []
	if employee and frappe.db.exists("DocType", "JEW HRMS Notification"):
		items.extend(frappe.get_list(
			"JEW HRMS Notification",
			filters={"employee": employee.name},
			fields=["name", "notification_type as type", "title", "message", "creation", "is_read", "reference_doctype", "reference_name"],
			order_by="creation desc",
			limit_page_length=50,
			ignore_permissions=True,
		))
	if employee and not _active_face(employee.name):
		items.append({"type": "warning", "title": "Face not registered", "message": "Ask HR to register your face before marking attendance."})
	if employee and not _assigned_locations(employee.name):
		items.append({"type": "warning", "title": "Location not assigned", "message": "Ask HR to assign an attendance geofence."})
	return _ok({"notifications": items})


@frappe.whitelist()
def get_shift_policies():
	_require_admin()
	if not frappe.db.exists("DocType", "JEW Shift Attendance Policy"):
		return _ok({"policies": []})
	policies = frappe.get_list(
		"JEW Shift Attendance Policy",
		fields=["name", "shift_name", "shift_start_time", "shift_end_time", "full_day_minimum_hours", "half_day_minimum_hours", "late_coming_grace_minutes", "early_going_grace_minutes", "is_active"],
		order_by="modified desc",
		ignore_permissions=True,
	)
	return _ok({"policies": policies})


@frappe.whitelist()
def save_shift_policy(**kwargs):
	_require_admin()
	if not frappe.db.exists("DocType", "JEW Shift Attendance Policy"):
		return _fail("Shift policy is not configured. Please contact HR/Admin.", "shift_policy_missing")
	name = kwargs.get("name")
	doc = frappe.get_doc("JEW Shift Attendance Policy", name) if name else frappe.new_doc("JEW Shift Attendance Policy")
	for field in (
		"shift_name",
		"shift_start_time",
		"shift_end_time",
		"break_minutes",
		"full_day_minimum_hours",
		"half_day_minimum_hours",
		"late_coming_grace_minutes",
		"early_going_grace_minutes",
		"max_late_coming_allowed_per_month",
		"max_early_going_allowed_per_month",
		"max_short_hours_allowed_per_month",
		"action_after_late_limit",
		"action_after_early_limit",
		"action_after_short_hours_limit",
		"is_active",
	):
		if field in kwargs:
			doc.set(field, kwargs.get(field))
	if not doc.shift_name:
		return _fail("Shift name is required.", "validation_error")
	for field in ("action_after_late_limit", "action_after_early_limit", "action_after_short_hours_limit"):
		if doc.get(field) and doc.get(field) not in POLICY_ACTIONS:
			return _fail("Invalid policy action.", "validation_error")
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return _ok({"policy": _policy_payload(doc)}, "Shift policy saved")


@frappe.whitelist()
def evaluate_attendance_policy(employee=None, attendance_date=None, in_time=None, out_time=None):
	if employee and _is_approver():
		doc = frappe.get_doc("Employee", employee)
	else:
		doc = _require_employee(employee) if employee else _get_current_employee()
	result = _evaluate_attendance_policy(doc.name, attendance_date, in_time, out_time, doc.get("default_shift"))
	return _ok({"result": result})


@frappe.whitelist()
def mark_missing_out_regularization(employee=None, attendance_date=None):
	_require_admin()
	employee_doc = frappe.get_doc("Employee", employee) if employee else _get_current_employee()
	attendance_date = getdate(attendance_date or today())
	checkins = frappe.get_list(
		"Employee Checkin",
		filters={"employee": employee_doc.name, "time": ["between", [f"{attendance_date} 00:00:00", f"{attendance_date} 23:59:59"]]},
		fields=["name", "log_type", "time"],
		order_by="time asc",
		ignore_permissions=True,
	)
	in_time = next((row.time for row in checkins if row.log_type == "IN"), None)
	out_time = next((row.time for row in reversed(checkins) if row.log_type == "OUT"), None)
	if in_time and not out_time:
		name = _create_regularization(employee_doc.name, attendance_date, "Missing Mark Out", in_time, None, None, "Regularization Required")
		frappe.db.commit()
		return _ok({"regularization": name}, "Regularization pending")
	return _ok({"regularization": None}, "No missing mark out found")


@frappe.whitelist()
def get_regularizations(status="Pending"):
	_require_approver()
	if not frappe.db.exists("DocType", "JEW Attendance Regularization"):
		return _ok({"regularizations": []})
	filters = {}
	if status:
		filters["status"] = status
	rows = frappe.get_list(
		"JEW Attendance Regularization",
		filters=filters,
		fields=["name", "employee", "attendance_date", "issue_type", "in_time", "out_time", "working_hours", "policy_action", "status", "remarks"],
		order_by="attendance_date desc, modified desc",
		limit_page_length=100,
		ignore_permissions=True,
	)
	return _ok({"regularizations": rows})


@frappe.whitelist()
def decide_regularization(name=None, action=None, remarks=None, manual_out_time=None):
	_require_admin()
	if not name or not action:
		return _fail("Regularization and action are required.", "validation_error")
	if action == "Marked LWP" and not _is_owner():
		return _fail("You do not have permission.", "permission_denied")
	allowed = {"Approved as Present", "Marked Half Day", "Marked LWP", "Rejected"}
	if action not in allowed:
		return _fail("Invalid regularization action.", "validation_error")
	doc = frappe.get_doc("JEW Attendance Regularization", name)
	if action == "Rejected" and not remarks:
		return _fail("Reject Reason required.", "reject_reason_required")
	if manual_out_time:
		doc.out_time = get_datetime(manual_out_time)
	doc.status = action
	doc.approved_by = frappe.session.user
	doc.remarks = remarks
	doc.save(ignore_permissions=True)
	_safe_insert_notification(doc.employee, "Regularization updated", f"{doc.issue_type}: {action}", "Info", "JEW Attendance Regularization", doc.name)
	frappe.db.commit()
	return _ok({"regularization": doc.name, "status": doc.status}, "Regularization updated")


@frappe.whitelist()
def get_employee_list():
	_require_approver()
	employees = frappe.get_list("Employee", filters={"status": "Active"}, fields=["name", "employee_name", "department", "designation", "user_id"], order_by="employee_name asc", limit_page_length=200)
	for employee in employees:
		face = _active_face(employee.name)
		employee.face_registered = bool(face)
		employee.face_last_updated_on = face.last_updated_on if face else None
		employee.location_count = len(_assigned_locations(employee.name))
		employee.value = employee.name
		employee.label = employee.employee_name or employee.name
		employee.description = " - ".join([part for part in [employee.name, employee.department, employee.designation] if part])
	return _ok({"employees": employees})
