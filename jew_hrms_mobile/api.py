import json
import math
import traceback
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.auth import LoginManager
from frappe.utils import add_days, add_to_date, cint, date_diff, flt, formatdate, get_datetime, get_first_day, get_last_day, get_system_timezone, getdate, now_datetime, today
from frappe.utils.file_manager import save_file

from time import perf_counter as _perf

from jew_hrms_mobile import face_approval as _face_approval
from jew_hrms_mobile.face_service import (
	decode_image,
	get_face_engine_status as face_engine_status,
	register_face,
	serialize_encoding,
	verify_encoding,
	verify_face,
)


ADMIN_ROLES = {"Administrator", "System Manager", "HR Manager", "HR User", "JEW HRMS Admin", "JEW HRMS Owner"}
OWNER_ROLES = {"Administrator", "System Manager", "JEW HRMS Owner"}
HR_ROLES = {"HR Manager", "HR User", "Leave Approver", "JEW HRMS HR"}
APPROVER_ROLES = ADMIN_ROLES | HR_ROLES
# Access to the JEW HRMS mobile ADMIN panel is limited to these app-specific
# roles only — deliberately NOT the broad ERP roles (System Manager, HR Manager,
# HR User, Leave Approver), which belong to the wider ERP and must not grant
# mobile-admin access.
JEW_ADMIN_ROLES = {"JEW HRMS Admin", "JEW HRMS HR", "JEW HRMS Owner"}
EMPLOYEE_ROLES = {"Employee", "JEW HRMS Employee"}
REQUIRED_LEAVE_TYPES = {
	"CL": "Casual Leave",
	"PL": "Privilege / Paid Leave",
	"SL": "Sick Leave",
	"LWP": "Leave Without Pay",
}
APPROVAL_STATUSES = {
	"draft": "Draft",
	"pending_dept_head": "Pending Department Head Approval",
	"pending_md": "Pending MD Approval",
	"pending_hr": "Pending HR Approval",
	"pending_admin": "Pending Admin Approval",  # legacy alias (older leaves), treated as HR-final
	"approved": "Approved",
	"rejected": "Rejected",
	"rejected_dept_head": "Rejected by Department Head",
	"rejected_md": "Rejected by MD",
	"rejected_hr": "Rejected by HR",
	"cancelled": "Cancelled",
}
# current_approval_level field values
LEAVE_LEVELS = {
	"not_started": "Not Started", "dept_head": "Department Head", "md": "MD",
	"hr": "HR", "completed": "Completed", "rejected": "Rejected", "cancelled": "Cancelled",
}
# Dept Head / MD approvals are NEVER auto-approved or auto-forwarded. While a leave waits at one of those two
# stages the approver is e-mailed a reminder every LEAVE_REMINDER_EVERY_DAYS days until they approve or reject.
LEAVE_REMINDER_EVERY_DAYS = 3
# leave_email_action.py (www) imports this; email-link approval is disabled (app/Desk only).
EMAIL_ACTION_TOKEN_VALIDITY_DAYS = 3
CANCEL_ACTION_TOKEN_VALIDITY_DAYS = 7  # HR may take a few days to act on a cancellation request
# Casual Leave is capped at this many days per calendar month per employee.
CASUAL_LEAVE_MONTHLY_CAP = 2
CASUAL_LEAVE_TYPES = ("Casual Leave", "CL")
# Once an employee's combined Late Coming + Early Going count (auto-detected +
# self-service, approved/pending) reaches this many in a calendar month, an
# Approved self-service decision marks that day Half Day automatically.
LATE_EARLY_HALF_DAY_THRESHOLD = 5
# Self-service requests are hard-capped at this many PER TYPE per month (2 Late
# Coming AND, separately, 2 Early Going) — a 3rd request of either type is
# rejected outright at apply time, not just left for the Dept Head to decide.
# This is independent of LATE_EARLY_HALF_DAY_THRESHOLD above: real auto-detected
# late-arrivals (a separate system, not this apply_late_early request flow)
# still count toward that combined threshold and can still push a day to Half
# Day even once an employee has used up their 2+2 self-service requests.
LATE_EARLY_MONTHLY_REQUEST_CAP = 2
# Separate, stricter daily-lateness rule ("Late Arrival"): ANY check-in more than
# this many minutes after the employee's own shift start counts as one late day
# this month - independent of JEW Shift Attendance Policy.late_coming_grace_minutes
# (which is a much larger grace governing the formal "Late Coming" self-service/
# auto-detected allowance above). LATE_ARRIVAL_MONTHLY_ALLOWANCE days are fine;
# the (allowance + 1)th such day this month auto-marks that day Half Day, no HR
# review needed (see _evaluate_attendance_policy / mark_attendance).
LATE_ARRIVAL_GRACE_MINUTES = 15
LATE_ARRIVAL_MONTHLY_ALLOWANCE = 5
# Half Day is NOT charged for every single late day past the allowance - only on
# the 1st day past it (the 6th late day overall), then again every
# LATE_ARRIVAL_HALF_DAY_INTERVAL late days after that (9th, 12th, 15th, ...). Days
# in between (7th, 8th, 10th, 11th, ...) are recorded but stay "Warn Only" - no
# Half Day. Total Half Days for N late days (N >= 6) works out to
# 1 + (N - 6) // LATE_ARRIVAL_HALF_DAY_INTERVAL.
LATE_ARRIVAL_HALF_DAY_INTERVAL = 3
# An employee may back-date a leave application by up to this many days (e.g. they
# forgot to apply, or are applying the same morning for today) - beyond this
# window it's still blocked, same as before. See _initial_leave_stage for the
# same-day/backdated -> straight-to-HR routing this enables.
LEAVE_BACKDATE_ALLOWED_DAYS = 7
# Every leave application gets a ready-to-post WhatsApp message (see _leave_whatsapp_payload). The app copies it and
# opens this group; the employee pastes and taps Send. Override per site with site_config key
# "jew_hrms_leave_whatsapp_group_url" (set it to "" to switch the WhatsApp step off).
LEAVE_WHATSAPP_GROUP_URL = "https://chat.whatsapp.com/H8CdtNkbaje89BBsoWutIl"
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


def _can_access_admin(user=None):
	"""True only for the JEW HRMS mobile admin roles (not broad ERP roles)."""
	return _has_any(JEW_ADMIN_ROLES, user)


def _require_login():
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)


def _require_admin():
	_require_login()
	if not _can_access_admin():
		frappe.throw(_("Permission denied"), frappe.PermissionError)


def _require_approver():
	_require_login()
	if not _can_access_admin():
		frappe.throw(_("Permission denied"), frappe.PermissionError)


def _require_employee_directory_access():
	_require_login()
	if not _can_access_admin():
		return _fail("You do not have permission.", "permission_denied")
	return None


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


def _employee_companies_for_user(user=None):
	"""Distinct companies across all Active Employee records matching this user
	(via user_id/prefered_email/company_email/personal_email, or Employee.name ==
	user). Used to guard against ever misattributing attendance to the wrong
	company if one login somehow ends up linked to employees in more than one."""
	user = user or frappe.session.user
	fields = _employee_fields()
	names = set()
	for fieldname in ("user_id", "prefered_email", "company_email", "personal_email"):
		if fieldname not in fields:
			continue
		for n in frappe.get_all("Employee", filters={fieldname: user, "status": "Active"}, pluck="name"):
			names.add(n)
	if frappe.db.get_value("Employee", user, "status") == "Active":
		names.add(user)
	companies = set()
	for n in names:
		c = frappe.db.get_value("Employee", n, "company")
		if c:
			companies.add(c)
	return companies


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


def _verify_face(employee, face_image, encoding=None):
	profile = _active_face(employee)
	if not profile:
		return False, {"code": "face_not_registered", "message": "Face is not registered."}
	if encoding is not None:
		# active live scan already produced the encoding of the frame it chose - just compare
		result = verify_encoding(employee, encoding, profile.face_encoding or profile.face_template or "[]")
		return bool(result.get("ok")), result
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
	nearest = None         # nearest centre overall (used for the "outside" message)
	nearest_inside = None  # nearest location whose OWN radius contains the point
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
		if item["allowed"] and (nearest_inside is None or item["distance_meter"] < nearest_inside["distance_meter"]):
			nearest_inside = item
	# Being inside ANY assigned geofence is enough. Judging only the single nearest centre
	# wrongly rejects a point that lies inside a larger-radius site whenever a smaller-radius
	# site's centre happens to be closer (overlapping Office / Indore office / Palda sites).
	if nearest_inside:
		return {"allowed": True, "code": "success", "nearest_location": nearest_inside}
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
	resolved = _today_regularization_resolved(employee)
	if resolved:
		return None, "already_resolved", "Today's attendance was already resolved by HR ({0}).".format(resolved.status)
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


# Issue types whose resolution FABRICATES a check-in/check-out that never really
# happened (Missing Mark ...) - only these should stop the app from offering a
# real Check In/Check Out for today (see _today_regularization_resolved below).
# Late Coming / Early Going / Late Arrival / the self-service "Late Coming
# Check-in" / "Early Going Check-out" types are the OPPOSITE case: they only
# ever get raised or auto-applied AFTER a real check-in already happened today
# (that real punch is how the lateness was detected in the first place), so an
# employee who is auto-marked Half Day for arriving late this morning must
# still be able to check out normally this evening - resolving one of these
# never means "the day is finalized, stop offering Check In/Check Out".
MISSING_PUNCH_ISSUE_TYPES = ("Missing Mark Out", "Missing Mark In & Out", "Missing Mark In (Half Day)", "Missing Mark Out (Half Day)")


def _today_regularization_resolved(employee):
	"""If HR has already decided TODAY's Missing-Mark regularization for this
	employee (any outcome except still-Pending or Cancelled), the day is
	finalized - the app should stop offering Check In/Check Out for it, rather
	than letting the employee mark a fresh, later check-in that a
	resolved-but-empty "Missing Mark In" day would otherwise still show
	(can_mark_in only looks at whether an Employee Checkin row exists yet, not
	whether HR already settled the day). Scoped to MISSING_PUNCH_ISSUE_TYPES
	only - see the constant's comment for why Late Coming/Early Going/Late
	Arrival must never trigger this."""
	if not frappe.db.exists("DocType", "JEW Attendance Regularization"):
		return None
	return frappe.db.get_value(
		"JEW Attendance Regularization",
		{
			"employee": employee, "attendance_date": today(),
			"issue_type": ["in", MISSING_PUNCH_ISSUE_TYPES],
			"status": ["not in", ["Pending", "Cancelled"]],
		},
		["name", "status"], as_dict=True,
	)


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
	regularization = _today_regularization_resolved(employee.name)
	can_mark_in = bool(face and locations and not checkin)
	can_mark_out = bool(face and locations and checkin and not checkout)
	status = "completed" if completed else ("in" if checkin else "not_marked")
	status_label = "Attendance completed" if completed else (f"Marked In at {checkin_label}" if checkin_label else "Not marked today")
	if regularization:
		can_mark_in = False
		can_mark_out = False
		status = "regularized"
		status_label = "Today's attendance was already resolved by HR ({0})".format(regularization.status)
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
		"status": status,
		"status_label": status_label,
		"can_mark_in": can_mark_in,
		"can_mark_out": can_mark_out,
		"face_registered": bool(face),
		"location_assigned": bool(locations),
		"location_status": location_state,
		"face_status": {"registered": bool(face), "last_updated_on": face.last_updated_on if face else None, **_face_approval.status_extra(employee.name)},
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
		"attendance_login_grace_minute",
		"attendance_logout_grace_minute",
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


def _get_festival_exemption(attendance_date):
	"""Active JEW Festival Exemption record for this date, if any (HR-managed via
	Desk - see JEW Festival Exemption). None of the normal Early Going grace/
	monthly-count machinery applies on an exempt date; see _evaluate_attendance_policy."""
	if not frappe.db.exists("DocType", "JEW Festival Exemption"):
		return None
	name = frappe.db.get_value("JEW Festival Exemption", {"exemption_date": attendance_date, "is_active": 1}, "name")
	return frappe.get_doc("JEW Festival Exemption", name) if name else None


def _festival_cutoff_time(festival, employee):
	"""IT department gets its own (earlier) allowed time if one is configured;
	everyone else uses the festival's default allowed time."""
	dept = str(frappe.db.get_value("Employee", employee, "department") or "")
	is_it = dept.split(" - ")[0].strip().upper() == "IT"
	if is_it and festival.get("it_department_allowed_time"):
		return _parse_time(festival.it_department_allowed_time, time(17, 0))
	return _parse_time(festival.default_allowed_time, time(17, 0))


# Half-day On Duty + office for the other half: an APPROVED half-day Attendance Request (custom_half_day_type = which half is On Duty)
# means the employee is out on duty for one half. If they are ALSO in the office for the other half (IN and OUT punched at least
# this many hours apart) the day is Present - HRMS alone would leave it a Half Day with the other half Absent. The late rule is
# waived for a first-half duty (they arrive at midday by design), the early-going rule for a second-half duty.
OD_HALF_OFFICE_MIN_HOURS = 2.0


def _approved_od_half_request(employee, attendance_date):
	"""(request name, 'First Half' | 'Second Half' | '') of the approved half-day On Duty / WFH request on this date, else None."""
	fields = ["name"] + (["custom_half_day_type"] if frappe.get_meta("Attendance Request").has_field("custom_half_day_type") else [])
	rows = frappe.get_all("Attendance Request", filters={"employee": employee, "docstatus": 1, "half_day": 1, "half_day_date": getdate(attendance_date)},
	                      fields=fields, limit_page_length=1)
	if not rows:
		return None
	return rows[0].name, (rows[0].get("custom_half_day_type") or "")


def _office_span_hours(in_dt, out_dt):
	if not (in_dt and out_dt):
		return 0.0
	return max(0.0, (get_datetime(out_dt) - get_datetime(in_dt)).total_seconds() / 3600.0)


def _finalize_od_half_present(employee, attendance_date, in_time=None, out_time=None, policy_result=None):
	"""Half-day On Duty approved + the employee was in the office for the other half -> Present. Only ever upgrades the Half Day
	that this very On Duty request created or changed (HRMS marks it Half Day with the other half Absent without looking at the
	punches); never touches any other attendance. Best-effort, callers wrap it in try/except."""
	attendance_date = getdate(attendance_date)
	req = _approved_od_half_request(employee, attendance_date)
	if not req:
		return None
	att = frappe.db.get_value("Attendance", {"employee": employee, "attendance_date": attendance_date, "docstatus": 1, "status": "Half Day"},
	                          ["name", "attendance_request"], as_dict=True)
	if not att or (att.attendance_request and att.attendance_request != req[0]):
		return None
	if not (in_time and out_time):
		p = frappe.db.sql("select min(case when log_type = 'IN' then time end), max(case when log_type = 'OUT' then time end) "
		                  "from `tabEmployee Checkin` where employee = %s and date(time) = %s", (employee, attendance_date))
		in_time, out_time = (p[0][0], p[0][1]) if p else (None, None)
	span = _office_span_hours(in_time, out_time)
	if span < OD_HALF_OFFICE_MIN_HOURS:
		return None
	frappe.db.set_value("Attendance", att.name, {"status": "Present", "half_day_status": None, "late_entry": 0, "early_exit": 0, "attendance_request": req[0],
	                                             "in_time": get_datetime(in_time), "out_time": get_datetime(out_time), "working_hours": round(span, 2)})
	frappe.get_doc("Attendance", att.name).add_comment("Info", "Half-day On Duty ({0}) + office for the other half: status changed from Half Day to Present.".format(req[1] or "half day"))
	# the late / early / short-hours records of this very day no longer count towards the monthly limits (HR-approved ones are left alone)
	if frappe.db.exists("DocType", "JEW Attendance Regularization"):
		waive = ["Short Hours"] + (["Late Coming", "Late Arrival"] if req[1] == "First Half" else ["Early Going"] if req[1] == "Second Half" else [])
		for r in frappe.get_all("JEW Attendance Regularization", filters={"employee": employee, "attendance_date": attendance_date, "issue_type": ["in", waive],
		                                                                  "status": ["in", ["Pending", "Marked Late Coming", "Marked Half Day", "Marked Early Going"]]}, fields=["name", "remarks"]):
			frappe.db.set_value("JEW Attendance Regularization", r.name, {"status": "Rejected", "remarks": ((r.remarks or "") + " [Waived: half-day On Duty + office]").strip()}, update_modified=False)
	return att.name


def _parse_od_clock(value):
	"""'9:00 AM' / '09:00' / '14:30:00' -> datetime.time, else None."""
	text = str(value or "").strip().upper().replace(".", "")
	for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M:%S", "%H:%M"):
		try:
			return datetime.strptime(text, fmt).time()
		except ValueError:
			pass
	return None


def _od_time_from_text(explanation):
	"""The window request_on_duty folds into the explanation as "(Time: 10:00 AM to 12:00 PM)" -> (from time, to time), else None."""
	m = __import__("re").search(r"[(]Time:[ ]*(.+?)[ ]+to[ ]+(.+?)[)][ ]*$", str(explanation or ""))
	if not m:
		return None
	a, b = _parse_od_clock(m.group(1)), _parse_od_clock(m.group(2))
	return (a, b) if a and b and a < b else None


def _approved_od_time_window(employee, attendance_date):
	"""(request name, from time, to time) of an APPROVED full-day-type "Particular Time" On Duty request covering this date, else None.
	The time window is the one already stored in the request's explanation text - no extra fields."""
	d = getdate(attendance_date)
	for r in frappe.get_all("Attendance Request", filters={"employee": employee, "docstatus": 1, "half_day": 0, "from_date": ["<=", d], "to_date": [">=", d]},
	                        fields=["name", "explanation"]):
		w = _od_time_from_text(r.explanation)
		if w:
			return r.name, w[0], w[1]
	return None


_WAIVABLE_ISSUES = ("Late Coming", "Late Arrival", "Early Going", "Short Hours")


def _apply_time_od_attendance(doc):
	"""A "Particular Time" On Duty request was just approved: HRMS made EVERY day of it Present. Each day is put right here, because the
	duty hours only count as time worked (see _evaluate_attendance_policy):
	  - the duty window covers the whole shift  -> stays Present (a whole-day duty)
	  - IN and OUT punched                      -> status from the policy (Present / Half Day); late / early / short-hours records that no longer apply are waived
	  - otherwise (no punches yet / no OUT)     -> the Present HRMS just made is cancelled; the normal flow decides when the employee punches (nightly job marks Absent if never)
	Best-effort; the caller wraps it in try/except."""
	emp = doc.employee
	shift = frappe.db.get_value("Employee", emp, "default_shift")
	for i in range(date_diff(doc.to_date, doc.from_date) + 1):
		d = getdate(add_days(doc.from_date, i))
		att = frappe.db.get_value("Attendance", {"employee": emp, "attendance_date": d, "docstatus": 1, "attendance_request": doc.name}, ["name", "status"], as_dict=True)
		if not att:
			continue
		p = frappe.db.sql("select min(case when log_type = 'IN' then time end), max(case when log_type = 'OUT' then time end) from `tabEmployee Checkin` "
		                  "where employee = %s and date(time) = %s", (emp, d))
		in_t, out_t = (p[0][0], p[0][1]) if p else (None, None)
		pr = _evaluate_attendance_policy(emp, d, in_t, out_t, shift)
		if pr.get("od_full_cover"):
			continue
		if in_t and out_t:
			status, le, ee = _attendance_status_from_policy(pr)
			if not status:
				continue
			frappe.db.set_value("Attendance", att.name, {"status": status, "half_day_status": "Absent" if status == "Half Day" else None, "late_entry": le, "early_exit": ee,
			                                             "in_time": get_datetime(in_t), "out_time": get_datetime(out_t), "working_hours": pr.get("working_hours") or 0})
			frappe.get_doc("Attendance", att.name).add_comment("Info", "Particular-time On Duty: duty hours + office punches evaluated by the shift policy -> {0}.".format(status))
			if frappe.db.exists("DocType", "JEW Attendance Regularization"):
				still = {i.get("issue_type") for i in (pr.get("issues") or [])}
				waive = [t for t in _WAIVABLE_ISSUES if t not in still]
				for r in frappe.get_all("JEW Attendance Regularization", filters={"employee": emp, "attendance_date": d, "issue_type": ["in", waive],
				                                                                  "status": ["in", ["Pending", "Marked Late Coming", "Marked Half Day", "Marked Early Going"]]}, fields=["name", "remarks"]):
					frappe.db.set_value("JEW Attendance Regularization", r.name, {"status": "Rejected", "remarks": ((r.remarks or "") + " [Waived: particular-time On Duty]").strip()}, update_modified=False)
		else:
			frappe.get_doc("Attendance", att.name).cancel()


def _evaluate_attendance_policy(employee, attendance_date=None, in_time=None, out_time=None, shift=None):
	policy = _get_active_policy(employee=employee, shift=shift)
	if not policy:
		return {"ok": False, "message": "Shift policy is not configured. Please contact HR/Admin.", "issues": [], "status": "Shift Policy Missing"}
	attendance_date = getdate(attendance_date or today())
	issues = []
	policy_shift_start = _parse_time(policy.shift_start_time, time(9, 30))
	policy_shift_end = _parse_time(policy.shift_end_time, time(18, 30))
	# Particular-time On Duty (approved): a window at the start of the shift moves the effective start to the end of the window, a window at the end
	# moves the effective end to its start, a window covering the whole shift is a whole-day duty; the duty hours always count as time worked.
	od_time_adj, od_hours, od_full_cover = None, 0.0, False
	try:
		od_tw = _approved_od_time_window(employee, attendance_date)
	except Exception:
		od_tw = None
	if od_tw:
		lg, og = int(policy.get("attendance_login_grace_minute") or 0), int(policy.get("attendance_logout_grace_minute") or 0)
		s_dt, e_dt = datetime.combine(attendance_date, policy_shift_start), datetime.combine(attendance_date, policy_shift_end)
		w1, w2 = datetime.combine(attendance_date, od_tw[1]), datetime.combine(attendance_date, od_tw[2])
		od_hours = max(0.0, (min(w2, e_dt) - max(w1, s_dt)).total_seconds() / 3600.0)
		morning = w1 <= s_dt + timedelta(minutes=lg) and w2 > s_dt
		evening = w2 >= e_dt - timedelta(minutes=og) and w1 < e_dt
		if morning and evening:
			od_full_cover = True
		elif morning:
			policy_shift_start = min(w2, e_dt).time()
		elif evening:
			policy_shift_end = max(w1, s_dt).time()
		od_time_adj = {"from": od_tw[1].strftime("%H:%M"), "to": od_tw[2].strftime("%H:%M")}
	in_dt = get_datetime(in_time) if in_time else None
	out_dt = get_datetime(out_time) if out_time else None
	working_hours = 0
	if in_dt and out_dt:
		working_minutes = _minutes_between(in_dt, out_dt) - int(policy.break_minutes or 0)
		working_hours = max(0, round(working_minutes / 60, 2))
	if od_hours and in_dt and out_dt:
		working_hours = round(working_hours + od_hours, 2)
	full_day_minimum_hours = float(policy.full_day_minimum_hours or 8)
	if in_dt:
		allowed_in = datetime.combine(attendance_date, policy_shift_start) + timedelta(minutes=int(policy.late_coming_grace_minutes or 0))
		if in_dt > allowed_in:
			# Arriving past the Late Coming threshold is only excused if the employee
			# has an APPROVED self-service "JEW Late Early Application" (Late Coming)
			# covering this exact date - that application can itself only be used up
			# to LATE_EARLY_MONTHLY_REQUEST_CAP times a month (enforced at apply time).
			# Arriving this late with NO such approval on file is not a "warning" - it
			# goes straight to Half Day, every time.
			has_approved_late_coming = bool(frappe.db.exists("JEW Late Early Application", {
				"employee": employee, "application_type": "Late Coming",
				"application_date": attendance_date, "status": "Approved",
			}))
			issues.append({
				"issue_type": "Late Coming", "count": 0, "limit": 0,
				"action": "Warn Only" if has_approved_late_coming else "Mark Half Day",
			})
		# "Late Arrival": the tighter, always-on daily-lateness count described above -
		# fires independently of the "Late Coming" threshold/application check and is
		# evaluated every day the employee is even slightly late, not only once the
		# formal Late Coming grace is exceeded.
		strict_allowed_in = datetime.combine(attendance_date, policy_shift_start) + timedelta(minutes=LATE_ARRIVAL_GRACE_MINUTES)
		if in_dt > strict_allowed_in:
			la_count = _issue_count(employee, "Late Arrival", attendance_date) + 1
			la_is_half_day_trigger = (
				la_count > LATE_ARRIVAL_MONTHLY_ALLOWANCE
				and (la_count - LATE_ARRIVAL_MONTHLY_ALLOWANCE - 1) % LATE_ARRIVAL_HALF_DAY_INTERVAL == 0
			)
			hours_complete = bool(out_dt) and working_hours >= full_day_minimum_hours
			la_action = "Mark Half Day" if (la_is_half_day_trigger and not hours_complete) else "Warn Only"
			issues.append({"issue_type": "Late Arrival", "count": la_count, "limit": LATE_ARRIVAL_MONTHLY_ALLOWANCE, "action": la_action})
	if out_dt:
		festival = _get_festival_exemption(attendance_date)
		if festival:
			# Festival day (HR-configured, e.g. Ganesh Chaturthi): checking out at or
			# after the festival's allowed time is fully exempt - no Early Going flag,
			# no monthly count at all. Checking out earlier than that skips the normal
			# grace/monthly-allowance machinery entirely and goes straight to Half Day.
			cutoff_dt = datetime.combine(attendance_date, _festival_cutoff_time(festival, employee))
			if out_dt < cutoff_dt:
				issues.append({
					"issue_type": "Early Going", "count": 0, "limit": 0, "action": "Mark Half Day",
					"festival_exemption": festival.festival_name,
				})
		else:
			allowed_out = datetime.combine(attendance_date, policy_shift_end) - timedelta(minutes=int(policy.early_going_grace_minutes or 0))
			if out_dt < allowed_out:
				# Same rule as Late Coming above: excused only by an APPROVED self-service
				# Early Going application for this exact date; otherwise straight to Half
				# Day, no free warnings.
				has_approved_early_going = bool(frappe.db.exists("JEW Late Early Application", {
					"employee": employee, "application_type": "Early Going",
					"application_date": attendance_date, "status": "Approved",
				}))
				issues.append({
					"issue_type": "Early Going", "count": 0, "limit": 0,
					"action": "Warn Only" if has_approved_early_going else "Mark Half Day",
				})
	if in_dt and not out_dt:
		issues.append({"issue_type": "Missing Mark Out", "count": 1, "limit": 0, "action": "Regularization Required"})
	if in_dt and out_dt:
		full_day = full_day_minimum_hours
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
	# Attendance login/logout grace window — a SEPARATE, tighter grace from the late/early
	# flag grace above. An employee who marks IN within login grace of shift start AND marks
	# OUT within logout grace of shift end is Present regardless of net worked hours (the paid
	# break makes a full attended shift dip below Full Day Minimum Hours by design, so hours
	# must not force a Half Day). Needs both punches; IN-only days are handled elsewhere.
	login_grace = int(policy.get("attendance_login_grace_minute") or 0)
	logout_grace = int(policy.get("attendance_logout_grace_minute") or 0)
	within_grace_window = False
	if in_dt and out_dt:
		latest_login = datetime.combine(attendance_date, policy_shift_start) + timedelta(minutes=login_grace)
		earliest_logout = datetime.combine(attendance_date, policy_shift_end) - timedelta(minutes=logout_grace)
		within_grace_window = in_dt <= latest_login and out_dt >= earliest_logout
	if od_full_cover:
		issues = [i for i in issues if i.get("issue_type") not in _WAIVABLE_ISSUES]
		within_grace_window = True
	od_half, od_office_ok = "", False
	try:
		od_req = _approved_od_half_request(employee, attendance_date)
	except Exception:
		od_req = None
	if od_req:
		od_half = od_req[1] or "On Duty"
		od_office_ok = _office_span_hours(in_dt, out_dt) >= OD_HALF_OFFICE_MIN_HOURS
		skip = ("Late Coming", "Late Arrival") if od_req[1] == "First Half" else ("Early Going",) if od_req[1] == "Second Half" else ()
		issues = [i for i in issues if i.get("issue_type") not in skip and not (od_office_ok and i.get("issue_type") == "Short Hours")]
	status = "Present" if not issues else "Regularization Pending"
	blocked = any(issue.get("action") == "Block Attendance" for issue in issues)
	return {"ok": not blocked, "policy": _policy_payload(policy), "issues": issues, "status": status, "working_hours": working_hours, "within_grace_window": within_grace_window,
	        "od_half": od_half, "od_office_ok": od_office_ok, "od_time": od_time_adj, "od_full_cover": od_full_cover}


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
	# Raised by the policy engine / admin tools, NOT an employee's own request - even
	# though mark_attendance runs this inside the employee's own session. The doctype's
	# before_insert skips its self-service rules (issue-type whitelist, mandatory reason,
	# one-per-day, HR email) when this flag is set.
	doc.flags.system_generated = True
	doc.save(ignore_permissions=True)
	_safe_insert_notification(employee, "Regularization pending", f"{issue_type} needs HR review.", "Warning", "JEW Attendance Regularization", doc.name)
	return doc.name


def _attendance_status_from_policy(policy_result):
	"""Grace-driven attendance status. Returns (status, late_entry, early_exit);
	status is None when the day should be left for HR (a Block Attendance issue).

	Rules (all thresholds come from the JEW Shift Attendance Policy so HR can tune them):
	  - Inside the login/logout grace window (marked IN within Attendance Login Grace of
	    shift start AND OUT within Attendance Logout Grace of shift end)
	                                                    -> Present, regardless of hours.
	  - Late in / early out beyond the login/logout window but within the monthly allowance
	    (Max Late/Early ... Per Month)                 -> Present, flagged late/early if
	    beyond Late Coming / Early Going Grace.
	  - Monthly allowance exceeded (Action After ... = Mark Half Day / Mark LWP)
	                                                    -> Half Day (flagged).
	  - Beyond grace AND worked less than Half Day Minimum Hours -> Half Day.
	  - Short hours alone (shift net span below Full Day Minimum Hours) does NOT
	    downgrade a punctual day — that is a shift-config artifact (break time), not
	    the employee being late.

	Note the two independent graces: Attendance Login/Logout Grace decides Present here;
	Late Coming / Early Going Grace only sets the late_entry/early_exit flags and feeds the
	monthly Mark-Half-Day limit."""
	if not policy_result or policy_result.get("status") == "Shift Policy Missing":
		return "Present", 0, 0  # no policy configured -> a completed checkout counts as Present
	if not policy_result.get("ok"):
		return None, 0, 0  # a "Block Attendance" issue -> leave for regularization / HR
	if policy_result.get("od_half") and policy_result.get("od_office_ok"):
		return "Present", 0, 0      # half On Duty + office for the other half = a full day
	issues = policy_result.get("issues") or []
	late = any(i.get("issue_type") == "Late Coming" for i in issues)
	early = any(i.get("issue_type") == "Early Going" for i in issues)
	le, ee = (1 if late else 0), (1 if early else 0)
	wh = float(policy_result.get("working_hours") or 0)
	half = float((policy_result.get("policy") or {}).get("half_day_minimum_hours") or 4)
	short = next((i for i in issues if i.get("issue_type") == "Short Hours"), None)
	very_short = bool(short and str(short.get("attendance_status") or "").startswith("Absent"))
	limit_half = any(
		i.get("action") in ("Mark Half Day", "Mark LWP") and i.get("issue_type") in ("Late Coming", "Early Going")
		for i in issues
	)
	if policy_result.get("within_grace_window"):
		return "Present", le, ee    # IN/OUT inside the login/logout grace window -> Present, hours ignored
	if limit_half:
		return "Half Day", le, ee   # exceeded the allowed late/early count for the month
	if (late or early) and (very_short or wh < half):
		return "Half Day", le, ee   # beyond grace AND worked less than half a day
	return "Present", le, ee        # outside the window but within the monthly allowance


def _ensure_attendance(employee, attendance_date, in_time=None, out_time=None, policy_result=None, shift=None):
	"""Create + submit an Attendance for a completed (checked-out) day when one
	does not already exist. Best-effort: never raises, so it can never block the
	checkout. Days already marked (On Leave / holiday / existing attendance) are
	skipped — HRMS's own validation is caught and treated as a skip."""
	attendance_date = getdate(attendance_date)
	if frappe.db.exists("Attendance", {"employee": employee, "attendance_date": attendance_date, "docstatus": ["<", 2]}):
		try:
			_finalize_od_half_present(employee, attendance_date, in_time, out_time, policy_result)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "JEW HRMS OD half finalize failed")
		return {"skipped": "already_exists"}
	status, late_entry, early_exit = _attendance_status_from_policy(policy_result)
	if not status:
		return {"skipped": "hr_review"}
	emp = frappe.db.get_value("Employee", employee, ["company", "status"], as_dict=True) or frappe._dict()
	if emp.get("status") and emp.status != "Active":
		return {"skipped": "inactive"}
	try:
		doc = frappe.new_doc("Attendance")
		doc.employee = employee
		if emp.get("company"):
			doc.company = emp.company
		doc.attendance_date = attendance_date
		doc.status = status
		if late_entry and doc.meta.has_field("late_entry"):
			doc.late_entry = 1
		if early_exit and doc.meta.has_field("early_exit"):
			doc.early_exit = 1
		if doc.meta.has_field("working_hours"):
			doc.working_hours = (policy_result or {}).get("working_hours") or 0
		if in_time and doc.meta.has_field("in_time"):
			doc.in_time = in_time
		if out_time and doc.meta.has_field("out_time"):
			doc.out_time = out_time
		if shift and doc.meta.has_field("shift"):
			doc.shift = shift
		doc.insert(ignore_permissions=True)
		doc.submit()
		return {"attendance": doc.name, "status": status}
	except Exception as exc:
		# Leave/holiday overlap, duplicate, etc. -> skip quietly (Frappe rolls the
		# failed insert back to its savepoint, leaving the checkin/regularization intact).
		return {"skipped": "not_marked", "reason": str(exc)[:140]}


def _company_holidays(company, cache):
	if company in cache:
		return cache[company]
	hl = frappe.db.get_value("Company", company, "default_holiday_list")
	dates = set(str(h.holiday_date) for h in frappe.get_all("Holiday", filters={"parent": hl}, fields=["holiday_date"])) if hl else set()
	cache[company] = dates
	return dates


def _mark_absent_unmarked(window_start, today_d, summary):
	"""Mark Absent for app-users (employees who have ever checked in) on PAST working
	days in the window where they neither checked in nor already have attendance/leave.
	Working days come from the company's default Holiday List, so weekly-offs and
	holidays are never marked Absent. Scoped to app-users only — employees who never
	use the app are left untouched."""
	first = {}
	for c in frappe.get_all("Employee Checkin", fields=["employee", "time"], order_by="time"):
		first.setdefault(c.employee, getdate(c.time))
	if not first:
		return
	onleave = set()
	for la in frappe.get_all("Leave Application",
	                         filters={"employee": ["in", list(first.keys())], "docstatus": 1, "to_date": [">=", str(window_start)]},
	                         fields=["employee", "from_date", "to_date", "status"]):
		js = frappe.db.get_value("Leave Application", {"employee": la.employee, "from_date": la.from_date}, "jew_hrms_approval_status")
		if not (la.status == "Approved" or js == "Approved"):
			continue
		d, end = getdate(la.from_date), getdate(la.to_date)
		while d <= end:
			onleave.add((la.employee, str(d))); d = add_to_date(d, days=1)
	hol_cache = {}
	for emp, fdate in first.items():
		if (frappe.db.get_value("Employee", emp, "status") or "Active") != "Active":
			continue
		company = frappe.db.get_value("Employee", emp, "company")
		hols = _company_holidays(company, hol_cache)
		d = max(getdate(window_start), fdate)
		while d < today_d:  # never today
			ds = str(d)
			# Never mark Absent for a day the employee actually checked in on - an IN
			# with no OUT is a missed check-out (handled by the Missing Mark Out
			# regularization), NOT an absence. Leave such days unmarked for HR.
			has_checkin = frappe.db.exists("Employee Checkin", {"employee": emp, "time": ["between", [ds + " 00:00:00", ds + " 23:59:59"]]})
			if ds in hols or (emp, ds) in onleave or has_checkin or frappe.db.exists("Attendance", {"employee": emp, "attendance_date": ds, "docstatus": ["<", 2]}):
				d = add_to_date(d, days=1); continue
			try:
				doc = frappe.new_doc("Attendance"); doc.employee = emp; doc.company = company
				doc.attendance_date = ds; doc.status = "Absent"
				doc.insert(ignore_permissions=True); doc.submit()
				summary["absent"] = summary.get("absent", 0) + 1
			except Exception:
				pass
			d = add_to_date(d, days=1)


def process_missing_attendance(days_back=30):
	"""Daily reconciliation of past days that have check-ins but no Attendance.

	- IN + OUT  -> mark per the shift policy (safety net if the inline check-out mark was missed).
	- IN only   -> mark Present + raise a 'Missing Mark Out' regularization for HR
	               (employee was verified on-site at check-in; HR can adjust if needed).

	Idempotent (days already marked are skipped) and never touches today (its
	check-out may still arrive). Scheduled via hooks `daily`; also safe to run manually."""
	today_d = getdate(today())
	window_start = add_to_date(today_d, days=-int(days_back or 30))
	checkins = frappe.get_all(
		"Employee Checkin",
		filters={"time": [">=", window_start]},
		fields=["employee", "employee_name", "log_type", "time", "shift"],
		order_by="time",
	)
	days = {}
	for c in checkins:
		d = getdate(c.time)
		if d >= today_d:
			continue  # don't finalise today; its check-out may still come
		key = (c.employee, str(d))
		e = days.setdefault(key, {"in": None, "out": None, "shift": c.shift})
		if c.log_type == "IN" and not e["in"]:
			e["in"] = c.time
		if c.log_type == "OUT":
			e["out"] = c.time
		if c.shift:
			e["shift"] = c.shift
	summary = {"present": 0, "half_day": 0, "in_only_present": 0, "skipped": 0, "absent": 0}
	for (emp, d), v in days.items():
		if not v["in"]:
			continue
		if frappe.db.exists("Attendance", {"employee": emp, "attendance_date": d, "docstatus": ["<", 2]}):
			summary["skipped"] += 1
			continue
		shift = v["shift"] or frappe.db.get_value("Employee", emp, "default_shift")
		if v["out"]:
			pr = _evaluate_attendance_policy(emp, d, v["in"], v["out"], shift)
			res = _ensure_attendance(emp, d, v["in"], v["out"], pr, shift)
			if res.get("status") == "Present":
				summary["present"] += 1
			elif res.get("status") == "Half Day":
				summary["half_day"] += 1
			else:
				summary["skipped"] += 1
		else:
			# IN only: do NOT auto-mark Present - just raise the "Missing Mark
			# Out" regularization and leave Attendance for HR to decide once
			# they confirm the actual check-out time (_create_regularization
			# is idempotent, so re-running this nightly job just updates the
			# same Pending record instead of creating duplicates).
			# Employee raises their OWN Missing Mark Out regularization (self-service);
			# the nightly job no longer auto-creates it (per HR instruction). The day
			# stays unmarked (never Absent - mark_absent_unmarked skips check-in days)
			# until the employee files their request and HR approves it.
			summary["in_only_skipped"] = summary.get("in_only_skipped", 0) + 1
	# app-users' unmarked working days -> Absent (safe scope: holidays/leave/existing skipped)
	_mark_absent_unmarked(window_start, today_d, summary)
	frappe.db.commit()
	_attendance_logger().info("process_missing_attendance " + json.dumps(summary))
	return summary


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
	is_dept_head = _is_any_dept_head()
	has_employee = bool(employee)
	return {
		"can_mark_attendance": has_employee,
		"can_apply_leave": has_employee,
		"can_apply_late_early": has_employee,
		"can_request_regularization": has_employee,
		"can_apply_on_duty": has_employee,
		"can_approve_on_duty": _can_access_admin(),
		"can_register_face": is_admin or is_dept_head,
		"can_self_register_face": bool(employee) and _face_approval.self_register_enabled(employee.name),
		"can_approve_face": _face_approval.can_approve(),
		"can_manage_locations": is_admin or is_dept_head,
		"can_manage_masters": is_admin or is_dept_head,
		"can_apply_leave_for_others": _can_access_admin(),
		"can_approve_leave": (is_approver and _can_access_admin()) or is_dept_head,
		"can_approve_late_early": is_dept_head,
		"can_approve_late_early_hr": _is_workflow_hr(),
		"can_decide_cancellations": _is_workflow_hr(),
		"can_view_admin": _can_access_admin() or is_dept_head,
		"can_manage_leave_policy": is_admin or is_hr,
		"can_manage_shift_policy": is_admin or is_hr,
		"can_manage_regularization": is_admin or is_hr,
		"can_view_approvers": _can_access_admin(),
		"is_admin": is_admin,
		"is_hr": is_hr,
		"is_owner": is_owner,
		"is_dept_head": is_dept_head,
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
	try:
		# NOTE: a Leave Allocation's "unused_leaves" field is the carry-forward amount
		# (leaves brought over from the previous period, usually 0) - NOT the live
		# remaining balance. Reading it made the dashboard show "0 days" even when the
		# employee still had leaves left. _employee_selectable_leave_types computes the
		# real remaining per type (allocated minus taken, via get_leave_balance_on) and
		# returns it in unused_leaves/balance, matching the Leave screen.
		leave_balance, _sel = _employee_selectable_leave_types(employee.name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS dashboard leave balance failed")
		leave_balance = []
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
def liveness_start(type=None, employee=None):
	"""Called by the attendance screen before it films the live-scan burst. On a site that does not use
	active liveness it answers {"enabled": False} and the app takes its normal single-selfie path. On a site
	with jew_hrms_liveness_mode = "active" it returns a fresh RANDOM challenge (see liveness_active.py)."""
	try:
		from jew_hrms_mobile import liveness_active, liveness_service
		if not liveness_service.active_enabled():
			return _ok({"enabled": False})
		scope = "att"
		if employee:
			# face REGISTRATION: an admin / dept head films the person being enrolled, so the challenge is
			# bound to THAT employee (not to the logged-in admin) and to the separate "reg" scope.
			_require_login()
			if not frappe.db.exists("Employee", employee) or not (_can_manage_employee(employee) or _face_approval.is_self_registrant(employee)):
				return _fail("You do not have permission for this employee.", "permission_denied")
			subject, scope = employee, "reg"
		else:
			subject = _get_current_employee().name
		locked = liveness_active.lockout_message(subject, scope)
		if locked:
			return _ok({"enabled": True, "locked": True, "message": locked})
		return _ok({"enabled": True, "locked": False, **liveness_active.new_challenge(subject, scope)})
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS liveness_start failed")
		# the server still enforces active liveness inside mark_attendance, so this cannot let a punch through
		return _ok({"enabled": False})


@frappe.whitelist()
def mark_attendance(type=None, image_data=None, latitude=None, longitude=None, accuracy=None, face_image=None, timestamp=None, liveness_challenge=None, liveness_frames=None, device_id=None, client_timings=None):
	_t_start = _perf()
	_face_ms = None
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

		companies = _employee_companies_for_user(frappe.session.user)
		if len(companies) > 1:
			_log_attendance_debug("multi_company_conflict", {**context, "companies": sorted(companies)})
			return _fail(
				"Your account is linked to Employee records in more than one company. Please contact HR to resolve this before marking attendance.",
				"multi_company_conflict",
			)

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

		# ACTIVE liveness (only sites with jew_hrms_liveness_mode = "active"): the server verifies the filmed
		# challenge burst and picks the frontal frame used for the face match itself - whatever image the
		# client sent as face_image is ignored, so a photo cannot be slipped in next to a live burst.
		from jew_hrms_mobile import liveness_active, liveness_service
		live_encoding = None
		if liveness_service.active_enabled():
			live_ok, live_res = liveness_active.verify_active(employee.name, liveness_challenge, liveness_frames)
			context["liveness_code"] = live_res.get("code")
			if not live_ok:
				_log_attendance_debug("liveness_failed", context)
				return _fail(live_res.get("message"), live_res.get("code", "liveness_failed"))
			face_image, image_data, live_encoding = live_res["frame"], None, live_res.get("encoding")

		image_payload = face_image or image_data
		if not image_payload:
			_log_attendance_debug("face_image_missing", context)
			return _fail("No face detected. Please keep your face inside the frame.", "no_face_detected")

		_t_face = _perf()
		matched, face_result = _verify_face(employee.name, image_payload, encoding=live_encoding)
		_face_ms = (_perf() - _t_face) * 1000
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

		# Punch audit: photo / device / IP / clock drift kept for HR review (page "Punch audit").
		# Best effort - it can never block or fail a punch (record_punch swallows its own errors).
		try:
			from jew_hrms_mobile import punch_audit
			punch_audit.record_punch(doc, employee, log_type, image_payload, face_result, geo, accuracy, timestamp, device_id,
											 timings=client_timings, server_ms=(_perf() - _t_start) * 1000, face_ms=_face_ms)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "JEW HRMS punch audit hook failed")

		status = _attendance_status_payload(employee)
		policy_result = None
		if log_type == "OUT":
			try:
				policy_result = _evaluate_attendance_policy(employee.name, today(), status.get("in_time"), status.get("out_time"), employee.get("default_shift"))
				# Issue types whose monthly ALLOWANCE (not just the over-limit case) needs a
				# persisted record even when the day itself needed no HR review - otherwise
				# _issue_count never sees the "fine, within allowance" days and the running
				# count can never actually reach the limit that triggers Mark Half Day.
				AUTO_RESOLVED_STATUS = {"Late Coming": "Marked Late Coming", "Early Going": "Marked Early Going", "Late Arrival": "Marked Late Coming"}
				for issue in policy_result.get("issues", []):
					if issue.get("festival_exemption"):
						continue
					issue_type = issue.get("issue_type")
					action = issue.get("action")
					# Marked IN within login grace and OUT within logout grace already makes
					# the day Present regardless of net hours (see _attendance_status_from_policy) -
					# a "Short Hours" issue on that same punctual day is purely the shift's own
					# break-time arithmetic, not the employee falling short, so it must not
					# raise a regularization for HR to review either.
					if issue_type == "Short Hours" and policy_result.get("within_grace_window"):
						continue
					if action == "Warn Only":
						if issue_type in AUTO_RESOLVED_STATUS:
							reg_name = _create_regularization(
								employee.name, today(), issue_type, status.get("in_time"), status.get("out_time"),
								policy_result.get("working_hours"), action, linked_employee_checkin=doc.name,
							)
							if reg_name:
								frappe.db.set_value("JEW Attendance Regularization", reg_name, {
									"status": AUTO_RESOLVED_STATUS[issue_type], "approved_by": "Administrator",
									"remarks": "Auto-resolved: within this month's allowance.",
								}, update_modified=False)
						continue
					if issue_type == "Late Arrival":
						# Allowance exceeded on a day with COMPLETE check-in/out data (nothing
						# missing to investigate) - a pure rule-based deduction, so it applies
						# immediately rather than sitting in HR's Pending queue like Missing
						# Mark In/Out genuinely needs to.
						reg_name = _create_regularization(
							employee.name, today(), issue_type, status.get("in_time"), status.get("out_time"),
							policy_result.get("working_hours"), action, linked_employee_checkin=doc.name,
						)
						if reg_name:
							reg_doc = frappe.get_doc("JEW Attendance Regularization", reg_name)
							reg_doc.status = "Marked Half Day"
							reg_doc.approved_by = "Administrator"
							reg_doc.remarks = "Auto-applied: {0}th late arrival this month (more than {1} min after shift start).".format(issue.get("count"), LATE_ARRIVAL_GRACE_MINUTES)
							reg_doc.save(ignore_permissions=True)
							_sync_attendance_for_regularization_decision(reg_doc, "Half Day", False, False)
						continue
					_create_regularization(
						employee.name, today(), issue_type, status.get("in_time"), status.get("out_time"),
						policy_result.get("working_hours"), action, linked_employee_checkin=doc.name,
					)
			except Exception:
				# The Employee Checkin is already committed above, so a failure while raising/
				# resolving policy regularizations must NEVER turn a successful check-out into an
				# error for the employee (the app would stay on the verify screen and keep
				# offering Mark Out). Log it for admin and carry on; _ensure_attendance below and
				# the nightly process_missing_attendance still reconcile the day.
				frappe.db.rollback()
				context["exception"] = traceback.format_exc()
				_log_attendance_debug("post_checkout_policy_failed", context)
				frappe.log_error(context["exception"], "JEW HRMS post-checkout policy failed")
			# Auto-mark Attendance for the completed day (Present / Half Day per policy).
			# Best-effort: any failure here must never block the checkout itself.
			try:
				context["attendance_result"] = _ensure_attendance(
					employee.name, today(), status.get("in_time"), status.get("out_time"),
					policy_result, employee.get("default_shift"))
				_log_attendance_debug("attendance_marked", context)
			except Exception:
				context["attendance_result"] = {"skipped": "error"}
		message = "Marked In successfully." if log_type == "IN" else "Marked Out successfully."
		return _ok({"employee_checkin": doc.name, "log_type": log_type, "time": doc.time, "face": face_result, "geofence": geo, "policy_result": policy_result, "attendance": context.get("attendance_result"), "attendance_status": status, **status}, message, "success")
	except Exception:
		frappe.db.rollback()
		context["exception"] = traceback.format_exc()
		_log_attendance_debug("mark_attendance_exception", context)
		frappe.log_error(context["exception"], "JEW HRMS mark_attendance failed")
		return _fail("Unable to create Employee Checkin. Please contact admin.", "employee_checkin_failed")


@frappe.whitelist()
def get_attendance_history(from_date=None, to_date=None, limit=500):
	employee = _get_current_employee()
	to_date = getdate(to_date or today())
	# Default to the CURRENT MONTH (1st → today) when no explicit range is given;
	# the mobile app passes an explicit from/to when the user picks a period.
	from_date = getdate(from_date) if from_date else to_date.replace(day=1)
	checkins = frappe.get_list(
		"Employee Checkin",
		filters={"employee": employee.name, "time": ["between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"]]},
		fields=["name", "time", "log_type"],
		order_by="time desc",
		limit_page_length=int(limit or 500),
		ignore_permissions=True,
	)
	attendance = frappe.get_list(
		"Attendance",
		filters={"employee": employee.name, "attendance_date": ["between", [from_date, to_date]]},
		fields=["name", "attendance_date", "status", "working_hours", "shift", "late_entry", "early_exit"],
		order_by="attendance_date desc",
		ignore_permissions=True,
	)
	# A day marked Present/Half Day/Absent via a regularization decision looks
	# identical to a normal day otherwise - attach the ORIGINAL issue (Missing Mark
	# Out, Late Coming, ...) so the app can show e.g. "Present (Missing Mark Out)"
	# instead of a plain "Present" that hides what actually happened that day.
	att_names = [a["name"] for a in attendance if a.get("name")]
	issue_by_attendance = {}
	if att_names:
		regs = frappe.get_all(
			"JEW Attendance Regularization",
			filters={"linked_attendance": ["in", att_names]},
			fields=["linked_attendance", "issue_type"],
		)
		issue_by_attendance = {r.linked_attendance: r.issue_type for r in regs}
	for a in attendance:
		a["regularization_issue_type"] = issue_by_attendance.get(a.get("name"))
	return _ok({"checkins": checkins, "attendance": attendance})


@frappe.whitelist()
def get_face_status(employee=None):
	doc = _require_employee(employee) if employee else _get_current_employee()
	face = _active_face(doc.name)
	return {"employee": doc.name, "registered": bool(face), "last_updated_on": face.last_updated_on if face else None, **_face_approval.status_extra(doc.name)}


@frappe.whitelist()
def get_face_engine_status():
	_require_login()
	return face_engine_status()


@frappe.whitelist()
def register_employee_face(employee=None, face_image=None, liveness_challenge=None, liveness_frames=None):
	_require_login()
	if not employee or not frappe.db.exists("Employee", employee):
		return _fail("Employee is required.", "validation_error")
	if _face_approval.is_self_registrant(employee) and not _can_access_admin():
		return _face_approval.submit_self_face(employee, face_image, liveness_challenge, liveness_frames)
	if not _can_manage_employee(employee):
		return _fail("You do not have permission for this employee.", "permission_denied")
	# ACTIVE liveness (sites with jew_hrms_liveness_mode = "active"): the person being enrolled must pass the
	# live scan, and the server picks the frontal frame that becomes the template - so a photo (or a screen)
	# cannot be enrolled as someone's face.
	from jew_hrms_mobile import liveness_active, liveness_service
	if liveness_service.active_enabled():
		live_ok, live_res = liveness_active.verify_active(employee, liveness_challenge, liveness_frames, scope="reg")
		if not live_ok:
			return _fail(live_res.get("message"), live_res.get("code", "liveness_failed"))
		face_image = live_res["frame"]
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
def update_employee_face(employee=None, face_image=None, liveness_challenge=None, liveness_frames=None):
	return register_employee_face(employee=employee, face_image=face_image, liveness_challenge=liveness_challenge, liveness_frames=liveness_frames)


@frappe.whitelist()
def get_locations(include_inactive=0):
	_require_login()
	if not (_can_access_admin() or _is_any_dept_head()):
		frappe.throw(_("Permission denied"), frappe.PermissionError)
	# We gate access ourselves above; dept heads hold no role-perm on the custom
	# location doctype, so read it with ignore_permissions (get_all also skips perms).
	# Retired (inactive) locations - e.g. duplicates merged into one - are hidden from the
	# admin screen by default; pass include_inactive=1 to list them too.
	filters = {} if _bool(include_inactive) else {"is_active": 1}
	return _ok({"locations": frappe.get_all("JEW Attendance Location", filters=filters, fields=["name", "location_name", "latitude", "longitude", "default_radius_meter", "is_active"], order_by="modified desc", ignore_permissions=True)})


@frappe.whitelist()
def save_location(name=None, location_name=None, latitude=None, longitude=None, default_radius_meter=100, is_active=1):
	_require_login()
	if not (_can_access_admin() or _is_any_dept_head()):
		frappe.throw(_("Permission denied"), frappe.PermissionError)
	doc = frappe.get_doc("JEW Attendance Location", name) if name else frappe.new_doc("JEW Attendance Location")
	doc.location_name = location_name
	doc.latitude = latitude
	doc.longitude = longitude
	doc.default_radius_meter = int(default_radius_meter or 100)
	doc.is_active = int(is_active or 0)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return _ok({"location": doc.name}, "Location saved")


@frappe.whitelist()
def delete_location(name=None):
	_require_login()
	if not (_can_access_admin() or _is_any_dept_head()):
		frappe.throw(_("Permission denied"), frappe.PermissionError)
	if not name:
		return _fail("Location is required.", "validation_error")
	frappe.delete_doc("JEW Attendance Location", name, ignore_permissions=True)
	frappe.db.commit()
	return _ok(message="Location deleted")


def _resolve_attendance_location(location):
	if not location:
		return None
	location = str(location).strip()
	if frappe.db.exists("JEW Attendance Location", location):
		return location
	if location.endswith(")") and "(" in location:
		maybe_name = location.rsplit("(", 1)[1].rstrip(")").strip()
		if frappe.db.exists("JEW Attendance Location", maybe_name):
			return maybe_name
	return frappe.db.get_value("JEW Attendance Location", {"location_name": location}, "name")


@frappe.whitelist()
def assign_employee_location(employee=None, location=None, radius_meter=None):
	_require_login()
	if not employee or not frappe.db.exists("Employee", employee):
		return _fail("Employee is required.", "validation_error")
	if not _can_manage_employee(employee):
		return _fail("You do not have permission to assign locations for this employee.", "permission_denied")
	if not location:
		return _fail("Location is required.", "validation_error")
	location_name = _resolve_attendance_location(location)
	if not location_name:
		return _fail("Selected location not found.", "not_found")
	name = frappe.db.get_value("JEW Employee Location Assignment", {"employee": employee, "location": location_name}, "name")
	doc = frappe.get_doc("JEW Employee Location Assignment", name) if name else frappe.new_doc("JEW Employee Location Assignment")
	doc.employee = employee
	doc.location = location_name
	doc.radius_meter = int(radius_meter or frappe.db.get_value("JEW Attendance Location", location_name, "default_radius_meter") or 100)
	doc.is_active = 1
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return _ok({"assignment": doc.name, "employee": employee, "location": location_name}, "Location assigned successfully.")


@frappe.whitelist()
def remove_employee_location(assignment=None, employee=None, location=None):
	_require_login()
	name = assignment or frappe.db.get_value("JEW Employee Location Assignment", {"employee": employee, "location": location}, "name")
	if not name:
		return _fail("Assignment is required.", "validation_error")
	target_emp = employee or frappe.db.get_value("JEW Employee Location Assignment", name, "employee")
	if not _can_manage_employee(target_emp):
		return _fail("You do not have permission for this employee.", "permission_denied")
	frappe.db.set_value("JEW Employee Location Assignment", name, "is_active", 0)
	frappe.db.commit()
	return _ok(message="Location assignment removed")


@frappe.whitelist()
def get_employee_locations(employee=None):
	doc = _require_employee(employee) if employee else _get_current_employee()
	return _ok({"employee": doc.name, "locations": _assigned_locations(doc.name)})


def _desk_allowed_leave_types():
	"""Leave Type names allowed by the Desk Leave Application's `leave_type`
	link_filters Property Setter, so the mobile picker mirrors the Desk EXACTLY
	(currently Casual Leave / Privilege Leave / Leave Without Pay). Returns a set
	of names, or None if no such filter is configured (then all types show)."""
	try:
		val = frappe.db.get_value(
			"Property Setter",
			{"doc_type": "Leave Application", "field_name": "leave_type", "property": "link_filters"},
			"value",
		)
		if not val:
			return None
		allowed = set()
		for f in json.loads(val):
			# f like ["Leave Type", "name", "in", ["Casual Leave", ...]]
			if len(f) >= 4 and str(f[1]) == "name" and str(f[2]).lower() == "in" and isinstance(f[3], (list, tuple)):
				allowed.update(f[3])
		return allowed or None
	except Exception:
		return None


def _employee_selectable_leave_types(employee):
	"""Leave Types an employee can pick, mirroring the Desk Leave Application's
	leave_type link field (restricted to the Desk's link_filters when configured).
	Each row is annotated for the mobile picker:
	  * allocated types carry the live remaining balance (get_leave_balance_on),
	  * unlimited (is_lwp) types are flagged (no allocation needed), and
	  * the rest are flagged has_allocation=False (selectable, but the backend will
	    reject on submit with a clear "allocation not found" message).
	Ordered allocated-first, then unlimited, then the rest. `balances` (used for the
	summary cards) holds only the allocated types. Returns (balances, selectable)."""
	day = getdate(today())
	allowed = _desk_allowed_leave_types()
	try:
		from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on
	except Exception:
		get_leave_balance_on = None
	allocations = frappe.get_list(
		"Leave Allocation",
		filters={"employee": employee, "docstatus": 1, "from_date": ["<=", day], "to_date": [">=", day]},
		fields=["leave_type", "total_leaves_allocated"],
		ignore_permissions=True,
	)
	alloc_totals = {}
	for a in allocations:
		alloc_totals[a.leave_type] = flt(alloc_totals.get(a.leave_type, 0)) + flt(a.total_leaves_allocated)
	balances = []
	selectable = []
	for t in frappe.get_all("Leave Type", fields=["name", "leave_type_name", "is_lwp"], order_by="name asc"):
		if allowed is not None and t.name not in allowed:
			continue
		lt = t.name
		is_lwp = int(t.is_lwp or 0)
		has_alloc = lt in alloc_totals
		# Use each type's own name (NOT the REQUIRED_LEAVE_TYPES display remap) so
		# the duplicate short-code records (CL / PL) stay visually distinct from the
		# full-name types instead of masquerading as them with the same label.
		lt_name = t.leave_type_name or lt
		allocated = flt(alloc_totals[lt]) if has_alloc else None
		balance = None
		if has_alloc:
			balance = allocated
			if get_leave_balance_on:
				try:
					balance = flt(get_leave_balance_on(employee, lt, day))
				except Exception:
					balance = allocated
			balances.append({"leave_type": lt, "leave_type_name": lt_name, "total_leaves_allocated": allocated, "unused_leaves": balance, "balance": balance})
		selectable.append({
			"leave_type": lt, "leave_type_name": lt_name, "is_lwp": is_lwp,
			"allocated": allocated, "balance": balance,
			"unlimited": bool(is_lwp), "has_allocation": has_alloc,
			# Allocated type with 0 (or negative) balance left -> not selectable in the app.
			"disabled": bool(has_alloc and not is_lwp and (balance is None or balance <= 0)),
		})
	rank = lambda r: (0 if r["has_allocation"] else 1 if r["unlimited"] else 2, r["leave_type_name"])
	selectable.sort(key=rank)
	return balances, selectable


@frappe.whitelist()
def get_leave_dashboard():
	employee = _get_current_employee()
	balances, selectable = _employee_selectable_leave_types(employee.name)
	recent = get_my_leaves(limit=5).get("leaves", [])
	return _ok({"balances": balances, "selectable_types": selectable, "recent": recent})


@frappe.whitelist()
def get_leave_types():
	# The Leave Type list is non-sensitive and every logged-in employee needs it to apply.
	# Read it with ignore_permissions: users created without the Employee role (Website User,
	# roles: All/Guest) otherwise get "Insufficient Permission for Leave Type" and the app
	# showed an empty leave picker.
	_require_login()
	leave_types = frappe.get_list("Leave Type", fields=["name", "leave_type_name", "is_lwp"], order_by="name asc", ignore_permissions=True)
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
	"""True if an active (non-Rejected/Cancelled) Leave Application already covers
	any day in [from_dt, to_dt] for this employee. Checks BOTH the standard
	`status` field and the custom `jew_hrms_approval_status` field as terminal-
	state defense in depth: a record can end up with status still "Open" while
	jew_hrms_approval_status already says Cancelled/Rejected-by-X (e.g. from a
	direct Desk edit that only touched one field) - sync_leave_terminal_status()
	now prevents new desyncs going forward, but this guard also makes the
	overlap check itself robust against any that already exist or slip through."""
	terminal_approval_statuses = [
		APPROVAL_STATUSES["cancelled"],
		APPROVAL_STATUSES["rejected"],
		APPROVAL_STATUSES["rejected_dept_head"],
		APPROVAL_STATUSES["rejected_md"],
		APPROVAL_STATUSES["rejected_hr"],
	]
	filters = {
		"employee": employee,
		"from_date": ["<=", to_dt],
		"to_date": [">=", from_dt],
		"status": ["not in", ["Rejected", "Cancelled"]],
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	for row in frappe.get_all("Leave Application", filters=filters, fields=["name", "jew_hrms_approval_status"]):
		if row.get("jew_hrms_approval_status") not in terminal_approval_statuses:
			return True
	return False


def _has_leave_allocation(employee, leave_type, from_dt, to_dt):
	if str(leave_type).upper() == "LWP":
		return True
	leave_type_doc = frappe.get_doc("Leave Type", leave_type)
	if leave_type_doc.get("is_lwp"):
		return True
	return bool(frappe.db.exists("Leave Allocation", {"employee": employee, "leave_type": leave_type, "docstatus": 1, "from_date": ["<=", from_dt], "to_date": [">=", to_dt]}))


def _check_casual_monthly_cap(employee, leave_type, from_dt, to_dt, is_half_day, exclude=None):
	"""Casual Leave is limited to CASUAL_LEAVE_MONTHLY_CAP days per calendar month
	(of from_dt). Counts existing non-rejected/cancelled CL in that month. Returns a
	_fail(...) dict if the cap would be exceeded, else None."""
	if str(leave_type) not in CASUAL_LEAVE_TYPES:
		return None
	this_days = 0.5 if is_half_day else (date_diff(to_dt, from_dt) + 1)
	mstart = frappe.utils.get_first_day(from_dt)
	mend = frappe.utils.get_last_day(from_dt)
	filters = {
		"employee": employee,
		"leave_type": ["in", list(CASUAL_LEAVE_TYPES)],
		"from_date": ["between", [mstart, mend]],
		"status": ["not in", ["Rejected", "Cancelled"]],
		"docstatus": ["<", 2],
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	used = 0.0
	for r in frappe.get_all("Leave Application", filters=filters, fields=["total_leave_days", "half_day", "from_date", "to_date"], ignore_permissions=True):
		if r.get("total_leave_days"):
			used += flt(r.total_leave_days)
		elif r.get("half_day"):
			used += 0.5
		else:
			used += (date_diff(r.to_date, r.from_date) + 1)
	if used + this_days > CASUAL_LEAVE_MONTHLY_CAP:
		return _fail(
			"Casual Leave is limited to {0} days per month. You already have {1} day(s) of Casual Leave this month; this {2}-day request would exceed that.".format(
				CASUAL_LEAVE_MONTHLY_CAP, (int(used) if used == int(used) else used), (int(this_days) if this_days == int(this_days) else this_days)),
			"casual_monthly_cap_exceeded")
	return None


def sync_leave_terminal_status(doc, method=None):
	"""validate hook backstop: keep the standard `status` field in sync with the
	custom jew_hrms_approval_status whenever the latter has already reached a
	terminal Cancelled/Rejected variant. This has to be unbypassable because Desk
	lets HR edit either field directly on the form (not only via cancel_leave()/
	reject_leave()), and _leave_overlap_exists()/_check_casual_monthly_cap() key
	off the standard `status` field only - a desync there (approval_status says
	Cancelled/Rejected but status still says Open) silently makes a resolved
	leave keep blocking new applications for the same dates forever."""
	if not doc.meta.has_field("jew_hrms_approval_status"):
		return
	appr = doc.get("jew_hrms_approval_status")
	if not appr:
		return
	if appr == APPROVAL_STATUSES["cancelled"] and doc.status != "Cancelled":
		doc.status = "Cancelled"
	elif appr in (
		APPROVAL_STATUSES["rejected"],
		APPROVAL_STATUSES["rejected_dept_head"],
		APPROVAL_STATUSES["rejected_md"],
		APPROVAL_STATUSES["rejected_hr"],
	) and doc.status != "Rejected":
		doc.status = "Rejected"


def validate_leave_application_cl_cap(doc, method=None):
	"""Enforce the Casual Leave monthly cap on EVERY Leave Application save —
	whether created via Desk directly or via the mobile app's apply_leave() API.
	apply_leave() already checks this before insert for a clean API error; this
	hook is the universal, unbypassable backstop (registered as `validate` in
	hooks.py, so it also re-runs on submit and on any later edit)."""
	if str(doc.get("leave_type")) not in CASUAL_LEAVE_TYPES:
		return
	error = _check_casual_monthly_cap(
		doc.employee, doc.leave_type, doc.from_date, doc.to_date, bool(doc.get("half_day")), exclude=doc.name
	)
	if error:
		frappe.throw(error.get("message") or "Casual Leave monthly cap exceeded.")


def _system_manager_emails():
	"""Enabled emails of everyone holding the System Manager role - the people who
	should hear about infrastructure-level problems (like an SMTP daily-limit
	error), as opposed to _leave_approver_emails() which is scoped to the HRMS
	approval roles only."""
	users = frappe.get_all("Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent")
	emails = []
	for u in set(users):
		if u in ("Administrator", "Guest"):
			continue
		info = frappe.db.get_value("User", u, ["email", "enabled"], as_dict=True)
		if info and info.enabled and info.email and "@" in info.email:
			emails.append(info.email)
	return sorted(set(emails))


def notify_admins_of_failed_emails():
	"""Scheduled every 15 minutes: alert System Managers the moment any outgoing
	email finally fails for good (retry limit exhausted - e.g. Gmail's daily
	sending-limit error). By default Frappe's own EmailQueue.notify_failed_email()
	only notifies whoever originally triggered that one email (its `owner`), so
	unless that happens to be a System Manager the failure can sit unnoticed for
	hours (as happened with the 366-email Gmail-quota backlog found this
	session). Email Queue status updates go through frappe.db.set_value directly
	(bypassing doc events), so a doc_event hook can't catch this - polling the
	jew_hrms_admin_notified marker is the reliable way. One digest notification
	per run (not one per failed email) so a burst of failures doesn't spam."""
	if not frappe.get_meta("Email Queue").has_field("jew_hrms_admin_notified"):
		return
	failed = frappe.get_all(
		"Email Queue",
		filters={"status": "Error", "jew_hrms_admin_notified": 0},
		fields=["name", "reference_doctype", "reference_name"],
		limit_page_length=0,
	)
	if not failed:
		return
	recipients = _system_manager_emails()
	if recipients:
		sample = failed[0]
		ref = "{0} {1}".format(sample.reference_doctype, sample.reference_name) if sample.reference_doctype else "no linked document"
		subject = "{0} email(s) failed to send (e.g. {1}) - check Email Queue for details.".format(len(failed), ref)
		try:
			from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
			enqueue_create_notification(recipients, {
				"type": "Alert",
				"document_type": "Email Queue",
				"document_name": sample.name,
				"subject": subject,
			})
		except Exception:
			frappe.log_error(frappe.get_traceback(), "JEW HRMS admin email-failure notification failed")
	for row in failed:
		frappe.db.set_value("Email Queue", row.name, "jew_hrms_admin_notified", 1)
	frappe.db.commit()


def _leave_approver_emails():
	"""Enabled emails of the leave approvers (JEW HRMS HR / Admin / Owner)."""
	users = set()
	for role in ("JEW HRMS HR", "JEW HRMS Admin", "JEW HRMS Owner"):
		for u in frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"):
			users.add(u)
	emails = []
	for u in users:
		if u in ("Administrator", "Guest"):
			continue
		info = frappe.db.get_value("User", u, ["email", "enabled"], as_dict=True)
		if info and info.enabled and info.email and "@" in info.email:
			emails.append(info.email)
	return sorted(set(emails))


def _send_leave_apply_mail(doc, employee_doc):
	"""Email the approvers when a leave is applied. Best-effort — never breaks apply."""
	try:
		recipients = _leave_approver_emails()
		if not recipients:
			return
		link = frappe.utils.get_url() + "/jew-hrms/m"
		days = "0.5 (Half Day)" if int(doc.get("half_day") or 0) else str(frappe.utils.date_diff(doc.to_date, doc.from_date) + 1)
		subject = "Leave request: {0} ({1})".format(employee_doc.employee_name, doc.leave_type)
		message = (
			"<p>A new leave request needs your approval.</p>"
			"<table cellpadding='6' style='border-collapse:collapse'>"
			"<tr><td><b>Employee</b></td><td>{emp}</td></tr>"
			"<tr><td><b>Leave type</b></td><td>{lt}</td></tr>"
			"<tr><td><b>From</b></td><td>{fr}</td></tr>"
			"<tr><td><b>To</b></td><td>{to}</td></tr>"
			"<tr><td><b>Days</b></td><td>{days}</td></tr>"
			"<tr><td><b>Reason</b></td><td>{reason}</td></tr>"
			"</table>"
			"<p><a href='{link}'>Open the JEW HRMS app to approve / reject</a></p>"
		).format(
			emp=frappe.utils.escape_html(employee_doc.employee_name or ""), lt=frappe.utils.escape_html(doc.leave_type or ""),
			fr=_fmt_dmy(doc.from_date), to=_fmt_dmy(doc.to_date),
			days=days, reason=frappe.utils.escape_html(doc.get("description") or "-"), link=link,
		)
		frappe.sendmail(recipients=recipients, subject=subject, message=message,
		                reference_doctype="Leave Application", reference_name=doc.name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS leave apply mail failed")


# ── Multi-stage leave approval workflow ─────────────────────────────────────
# Flow: apply → Department Head → MD → HR (final) → Approved. The Department Head
# stage is skipped when the employee's `custom_reports_direct_to_owner` is set or
# no `custom_dept_head_email` is configured. MD stage waits for the MD (reminder mail every 3 days, no auto-advance). Emails are TARGETED (applicant + the one next approver),
# never blasted to every approver-role holder.

def _employee_dept_head_email(employee_doc):
	email = (employee_doc.get("custom_dept_head_email") or "").strip()
	return email if email and "@" in email else None


def _leave_skips_md(doc):
	"""Same-day / backdated leaves bypass the MD stage (Dept Head -> HR). Detected by
	the application being made on or after the leave's own start date
	(from_date <= creation). A future-dated leave (applied in advance) still follows
	the full Dept Head -> MD -> HR chain."""
	try:
		created = getdate(doc.get("creation")) if doc.get("creation") else getdate(today())
		fd = doc.get("from_date")
		return bool(fd and getdate(fd) <= created)
	except Exception:
		return False


LEAVE_DH_MD_ROLE = "JEW HRMS Dept Head MD"


def _approver_is_md_like(user):
	"""True when this approver is ALSO an MD: either a JEW HRMS Owner (the global MDs), or a holder of the
	"JEW HRMS Dept Head MD" role - people who are the MD of their own team but are NOT the MD of everyone
	(so they must not receive / approve every other employee's MD stage). A Department Head who is MD
	approves ONCE: their Dept Head approval sends the leave straight to HR, the MD stage is bypassed."""
	if not user:
		return False
	emails = {e.lower() for e in set(_md_emails()) | set(_role_emails(LEAVE_DH_MD_ROLE))}
	if str(user).lower() in emails:
		return True
	uemail = (frappe.db.get_value("User", user, "email") or "").lower()
	return bool(uemail) and uemail in emails


def _initial_leave_stage(employee_doc, from_date=None):
	"""(status, level) for a brand-new leave: Department Head unless the employee
	reports direct to owner or has no dept head configured (then straight to MD).
	EXCEPTION: a same-day or backdated application (from_date <= today, e.g. applied
	the same morning, or a day late because the employee forgot) skips Dept
	Head/MD entirely and goes straight to HR - by the time the leave day has
	already started or passed, routing it through the normal multi-stage chain
	first doesn't reflect reality; HR handles these directly.
	ALSO skipped when the employee's configured Department Head IS the MD
	(custom_dept_head_email matches an MD/JEW HRMS Owner user's email) - without
	this, the MD got two separate emails and had to click Approve twice (once as
	'Department Head', once as 'MD') for the exact same leave before it reached
	HR. Treated exactly like custom_reports_direct_to_owner: straight to a single
	MD stage, which already auto-advances straight to HR once approved."""
	dept_head_email = _employee_dept_head_email(employee_doc)
	if from_date and getdate(from_date) <= getdate(today()):
		# Same-day / backdated: Department Head approves first, then straight to HR
		# (MD stage skipped - see _leave_skips_md). No Dept Head, or a Dept Head who is
		# already an HR approver, goes straight to HR so nobody approves the same leave twice.
		if dept_head_email and dept_head_email.strip().lower() not in {e.lower() for e in _hr_emails()}:
			return APPROVAL_STATUSES["pending_dept_head"], LEAVE_LEVELS["dept_head"]
		return APPROVAL_STATUSES["pending_hr"], LEAVE_LEVELS["hr"]
	skip = _bool(employee_doc.get("custom_reports_direct_to_owner"))
	if dept_head_email and dept_head_email.strip().lower() in {e.lower() for e in _md_emails()}:
		skip = True
	if not skip and dept_head_email:
		return APPROVAL_STATUSES["pending_dept_head"], LEAVE_LEVELS["dept_head"]
	return APPROVAL_STATUSES["pending_md"], LEAVE_LEVELS["md"]


def _role_emails(*role_names):
	users = set()
	for role in role_names:
		for u in frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"):
			users.add(u)
	emails = []
	for u in users:
		if u in ("Administrator", "Guest"):
			continue
		info = frappe.db.get_value("User", u, ["email", "enabled"], as_dict=True)
		if info and info.enabled and info.email and "@" in info.email:
			emails.append(info.email)
	return sorted(set(emails))


def _md_emails():
	return _role_emails("JEW HRMS Owner")


def _hr_emails():
	return _role_emails("JEW HRMS HR")


def _applicant_email(employee_doc):
	for f in ("user_id", "personal_email", "company_email", "prefered_email"):
		v = employee_doc.get(f)
		if v and "@" in v:
			return v
	return None


def _stage_approver_emails(status, employee_doc):
	"""The single next-approver recipient list for a pending status."""
	if status == APPROVAL_STATUSES["pending_dept_head"]:
		e = _employee_dept_head_email(employee_doc)
		return [e] if e else _md_emails()
	if status == APPROVAL_STATUSES["pending_md"]:
		return _md_emails()
	if status in (APPROVAL_STATUSES["pending_hr"], APPROVAL_STATUSES["pending_admin"]):
		return _hr_emails()
	return []


def _is_dept_head_of(user, employee_doc):
	dh = _employee_dept_head_email(employee_doc)
	if not dh:
		return False
	if (user or "").lower() == dh.lower():
		return True
	uemail = frappe.db.get_value("User", user, "email")
	return bool(uemail and uemail.lower() == dh.lower())


def _can_manage_employee(employee, user=None):
	"""True if the current user may manage this employee (face / location / etc):
	a full JEW HRMS admin/HR/MD (any employee), or a department head — for their own
	team AND for their own employee record (self face-register / self location assign).
	A plain employee (not a dept head / admin) can NOT self-register — face templates
	stay admin/dept-head controlled."""
	u = user or frappe.session.user
	if _can_access_admin(u):
		return True
	if not employee:
		return False
	if _is_any_dept_head(u):
		own = _get_employee_for_user(u)
		if own and own.name == employee:
			return True
		try:
			emp_doc = frappe.get_doc("Employee", employee)
		except Exception:
			return False
		return _is_dept_head_of(u, emp_doc)
	return False


def _user_display_name(user):
	"""Also imported by www/leave_email_action.py."""
	if not user:
		return "-"
	return frappe.db.get_value("User", user, "full_name") or user


def _fmt_dmy(date_value):
	"""DD/MM/YYYY, everywhere (emails, notification text, email-action pages) -
	frappe.utils.formatdate() renders using the site's configured date format,
	which on this bench is dd-mm-yyyy (dashes) - not the DD/MM/YYYY (slashes) used
	throughout the mobile app's own UI. Also imported by
	www/late_early_email_action.py and www/leave_email_action.py."""
	if not date_value:
		return "-"
	return frappe.utils.getdate(date_value).strftime("%d/%m/%Y")


def _stage_user_for_role(role):
	for u in frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"):
		if u not in ("Administrator", "Guest"):
			return u
	return None


def _stage_approver_user(status, employee_doc):
	"""The single approver USER for a pending stage (dept head = configured email;
	MD = JEW HRMS Owner holder; HR = JEW HRMS HR holder)."""
	if status == APPROVAL_STATUSES["pending_dept_head"]:
		return _employee_dept_head_email(employee_doc)
	if status == APPROVAL_STATUSES["pending_md"]:
		return _stage_user_for_role("JEW HRMS Owner")
	if status in (APPROVAL_STATUSES["pending_hr"], APPROVAL_STATUSES["pending_admin"]):
		return _stage_user_for_role("JEW HRMS HR")
	return None


def _generate_email_action_token(doc, user):
	"""Issue a single-use, expiring, stage-locked email-action token bound to the
	doc's CURRENT stage + the intended approver. Overwrites any previous token (so an
	old-stage link can no longer be used). Persisted without firing hooks."""
	token = frappe.generate_hash(length=48)
	frappe.db.set_value("Leave Application", doc.name, {
		"email_action_token": token,
		"email_action_token_user": user or "",
		"email_action_token_stage": doc.get("jew_hrms_approval_status"),
		"email_action_token_expiry": add_to_date(now_datetime(), days=EMAIL_ACTION_TOKEN_VALIDITY_DAYS),
	}, update_modified=False)
	return token


def _validate_email_action_token(token, action):
	"""Validate an email-action token. Returns (doc, None) or (None, error_message).
	For approve/reject the token must still match the doc's CURRENT stage and the doc
	must not be finalised; view is lenient. Imported by www/leave_email_action.py."""
	if not token:
		return None, "Invalid or missing action link."
	name = frappe.db.get_value("Leave Application", {"email_action_token": token}, "name")
	if not name:
		return None, "This link is no longer valid — it may have already been used or superseded."
	doc = frappe.get_doc("Leave Application", name)
	expiry = doc.get("email_action_token_expiry")
	if expiry and get_datetime(expiry) < now_datetime():
		return None, "This action link has expired."
	if action in ("approve", "reject"):
		if doc.docstatus != 0 or str(doc.status) in ("Approved", "Rejected", "Cancelled"):
			return None, "This leave application has already been finalised."
		stage = doc.get("email_action_token_stage")
		if stage and stage != doc.get("jew_hrms_approval_status"):
			return None, "This leave application has already moved to the next stage."
	return doc, None


def _recent_leave_days_count(employee, before_date, window_days=15, exclude_name=None):
	"""Total APPROVED leave days this employee took in the `window_days` days
	immediately before `before_date` (the 15 days strictly before the new
	application's start date - `before_date` itself is not counted). Shown to
	approvers (Dept Head/MD/HR) alongside a new request so they can see at a
	glance whether this employee has been on leave a lot very recently.
	Overlap with the window is computed per-day, not just "any overlap counts",
	so a leave that only partly falls inside the window is counted correctly."""
	before_date = getdate(before_date)
	window_start = before_date - timedelta(days=window_days)
	window_end = before_date - timedelta(days=1)
	if window_end < window_start:
		return 0
	rows = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee, "docstatus": 1, "status": "Approved",
			"from_date": ["<=", window_end], "to_date": [">=", window_start],
		},
		fields=["name", "from_date", "to_date", "half_day", "half_day_date"],
	)
	total = 0.0
	for r in rows:
		if exclude_name and r.name == exclude_name:
			continue
		if r.half_day:
			hd_date = getdate(r.half_day_date or r.from_date)
			if window_start <= hd_date <= window_end:
				total += 0.5
			continue
		overlap_start = max(getdate(r.from_date), window_start)
		overlap_end = min(getdate(r.to_date), window_end)
		if overlap_end >= overlap_start:
			total += (overlap_end - overlap_start).days + 1
	return total


def _leave_email_context(doc, employee_doc):
	"""Common Jinja context for the Leave Email Templates (patches/jew_hrms_leave_md_workflow.py)."""
	if int(doc.get("half_day") or 0):
		total_days = "0.5"
	else:
		total_days = doc.get("total_leave_days") or (frappe.utils.date_diff(doc.to_date, doc.from_date) + 1)
	dh_email = _employee_dept_head_email(employee_doc)
	return {
		"employee_name": employee_doc.employee_name or employee_doc.name,
		"employee_id": employee_doc.name,
		"department": employee_doc.get("department") or "-",
		"leave_type": doc.leave_type,
		"from_date": _fmt_dmy(doc.from_date),
		"to_date": _fmt_dmy(doc.to_date),
		"total_leave_days": total_days,
		"leave_reason": doc.get("description") or "-",
		"leave_application_id": doc.name,
		"company_name": doc.get("company") or employee_doc.get("company") or "Jain Engineering Works (India) Private Limited",
		"department_head_name": _user_display_name(dh_email) if dh_email else "-",
		"md_name": _user_display_name(_stage_user_for_role("JEW HRMS Owner")),
		"hr_approver_name": _user_display_name(_stage_user_for_role("JEW HRMS HR")),
		"department_head_approval_date": frappe.utils.format_datetime(doc.get("dept_head_approved_on")) if doc.get("dept_head_approved_on") else "-",
		"leave_application_link": frappe.utils.get_url() + "/jew-hrms/m",
		"approve_link": "", "reject_link": "", "attachment_view_link": "",
		"has_attachment": bool(doc.get("attachment")),
		# Stage-aware wording for the leave e-mail templates (the old templates always claimed "forwarded to the MD /
		# Pending MD Approval" even when the leave really went straight to HR, or when the Department Head IS the MD).
		"current_status": doc.get("jew_hrms_approval_status") or "",
		"forwarded_clause": (
			" and forwarded to the Managing Director for the second level of approval"
			if doc.get("jew_hrms_approval_status") == APPROVAL_STATUSES["pending_md"]
			else " and forwarded to HR for final approval"
			if doc.get("jew_hrms_approval_status") in (APPROVAL_STATUSES["pending_hr"], APPROVAL_STATUSES["pending_admin"])
			else ""
		),
		"hr_approved_text": (
			"approved by the Department Head and Managing Director"
			if doc.get("dept_head_approved_by") and doc.get("owner_approved_by")
			else "approved by the Managing Director (the employee's Department Head is also the MD, so a single approval was enough)"
			if doc.get("owner_approved_by")
			else "approved by the Department Head (a separate MD approval was not required)"
			if doc.get("dept_head_approved_by")
			else "approved"
		),
		"md_intro": (
			"has been approved by the Department Head and is now awaiting your approval"
			if doc.get("dept_head_approved_by")
			else "has been submitted. The employee's Department Head is also an MD (or the employee reports directly to the MD), "
			     "so there is no separate Department Head approval - it is now awaiting your approval"
		),
		"recent_leave_count": _recent_leave_days_count(employee_doc.name, doc.creation, exclude_name=doc.name),
		"recent_leave_window_date": _fmt_dmy(doc.creation),
	}


def _hrms_sender_email():
	"""The dedicated 'HRMS' Email Account's address (hrmsjewipl@gmail.com) - every
	HRMS mobile-app email (Leave / Late-Early / Regularization / Cancellation) must
	always go from THIS account specifically, regardless of whatever Email Account
	is marked Default Outgoing site-wide (other apps - fuel approval, PO/GRN
	approval, etc. - rely on that site default for their own mail and must not be
	affected by HRMS's choice of sender). Falls back to None (site default) only if
	the HRMS account has been renamed/removed, so mail never silently stops."""
	return frappe.db.get_value("Email Account", "HRMS", "email_id", cache=True)


def _send_template_email(recipients, template_name, ctx, doc, attachments=None):
	"""Render an Email Template by name with ctx and send it, tagged against whatever
	doctype `doc` actually is (Leave Application, JEW Late Early Application, ...).
	`attachments`, when given, is passed straight through to frappe.sendmail (e.g.
	[{"fid": file_doc_name}]) so the approver's email carries the file itself
	(a medical certificate, say) instead of only a link into the app. Best-effort."""
	recipients = sorted({r for r in (recipients or []) if r and "@" in r})
	if not recipients:
		return
	try:
		tmpl = frappe.db.get_value("Email Template", template_name, ["subject", "response"], as_dict=True)
		if not tmpl:
			return
		subject = frappe.render_template(tmpl.subject, ctx)
		message = frappe.render_template(tmpl.response, ctx)
		frappe.sendmail(recipients=recipients, subject=subject, message=message,
		                sender=_hrms_sender_email(),
		                reference_doctype=doc.doctype, reference_name=doc.name,
		                attachments=attachments or None)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS template email failed ({0})".format(template_name))


def _desk_bell_notify(recipients, doc, message):
	"""Push a real Frappe 'Notification Log' entry (Desk's own top-right bell) to
	each recipient - independent of email delivery, so an SMTP hiccup never leaves
	an approver with zero signal. Factored out of _notify_approvers_inapp so every
	HRMS approval-request path (Late/Early stage requests, Attendance
	Regularization requests - not just Leave stage requests and cancellations)
	can show up in ERP's own Notification bell, not only via email. Best-effort."""
	recipients = sorted({r for r in (recipients or []) if r and "@" in r})
	if not recipients:
		return
	try:
		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
		enqueue_create_notification(recipients, {
			"type": "Alert", "document_type": doc.doctype, "document_name": doc.name, "subject": message,
		})
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS Desk bell notification failed")


def _notify_approvers_inapp(recipients, doc, employee_doc, status):
	"""Notify each stage approver in-app two ways, independent of whether the email
	in _send_stage_request_email actually got delivered (SMTP/quota failures must
	never leave an approver with zero signal that a leave is waiting on them):
	  1. The custom "JEW HRMS Notification" bell inside the mobile app (unchanged).
	  2. A real Frappe "Notification Log" entry, so it also shows in Desk's own
	     top-right bell for anyone who works from Desk rather than the mobile app.
	Both are synchronous inserts (not routed through the email queue), so a Gmail
	daily-limit error or any other SMTP failure has no effect on either. Best-effort."""
	stage_label = {
		APPROVAL_STATUSES["pending_dept_head"]: "Department Head",
		APPROVAL_STATUSES["pending_md"]: "MD",
		APPROVAL_STATUSES["pending_hr"]: "HR",
		APPROVAL_STATUSES["pending_admin"]: "HR",
	}.get(status, "")
	msg = "{0} applied for {1} ({2} to {3}) — pending your {4} approval.".format(
		employee_doc.employee_name or employee_doc.name, doc.leave_type,
		_fmt_dmy(doc.from_date), _fmt_dmy(doc.to_date), stage_label).replace("  ", " ").strip()

	desk_user_emails = sorted({r for r in (recipients or []) if r and "@" in r})
	if desk_user_emails:
		try:
			from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
			enqueue_create_notification(desk_user_emails, {
				"type": "Alert",
				"document_type": doc.doctype,
				"document_name": doc.name,
				"subject": msg,
			})
		except Exception:
			frappe.log_error(frappe.get_traceback(), "JEW HRMS Desk bell notification failed")

	seen = set()
	for r in (recipients or []):
		if not r:
			continue
		emp = frappe.db.get_value("Employee", {"user_id": r}, "name")
		if not emp:
			u = frappe.db.get_value("User", {"email": r}, "name")
			if u:
				emp = frappe.db.get_value("Employee", {"user_id": u}, "name")
		if emp and emp not in seen:
			seen.add(emp)
			_safe_insert_notification(emp, "Leave approval needed", msg, "Info", "Leave Application", doc.name)


def _leave_attachment_for_email(doc):
	"""Leave Application.attachment is a private-file URL - resolve it to the
	File doctype's own name so it can be passed to frappe.sendmail(attachments=...)
	and actually ride along in the approver's email, not just be a link back into
	the app that only the applicant has permission to open."""
	if not doc.get("attachment"):
		return None
	file_name = frappe.db.get_value("File", {"file_url": doc.attachment}, "name")
	return [{"fid": file_name}] if file_name else None


def _send_stage_request_email(doc, employee_doc, status, ctx):
	"""Email the approver for `status` with one-click Approve/Reject/View token links,
	and drop an in-app (bell) notification for approvers who are employees."""
	recipients = _stage_approver_emails(status, employee_doc)
	if not recipients:
		return
	token = _generate_email_action_token(doc, _stage_approver_user(status, employee_doc))
	site = frappe.utils.get_url()
	c = dict(ctx)
	c["approve_link"] = "{0}/leave_email_action?token={1}&action=approve".format(site, token)
	c["reject_link"] = "{0}/leave_email_action?token={1}&action=reject".format(site, token)
	c["leave_application_link"] = "{0}/leave_email_action?token={1}&action=view".format(site, token)
	if doc.get("attachment"):
		c["attachment_view_link"] = "{0}/api/method/jew_hrms_mobile.api.view_leave_attachment?token={1}".format(site, token)
	tmpl = {
		APPROVAL_STATUSES["pending_dept_head"]: "Leave - Dept Head Approval Request",
		APPROVAL_STATUSES["pending_md"]: "Leave - MD Approval Request",
		APPROVAL_STATUSES["pending_hr"]: "Leave - HR Final Approval Request",
		APPROVAL_STATUSES["pending_admin"]: "Leave - HR Final Approval Request",
	}.get(status)
	if tmpl:
		_send_template_email(recipients, tmpl, c, doc, attachments=_leave_attachment_for_email(doc))
	_notify_approvers_inapp(recipients, doc, employee_doc, status)


@frappe.whitelist(allow_guest=True)
def view_leave_attachment(token=None):
	"""Guest-accessible, token-gated (same one-click scheme as the email
	Approve/Reject/View links) endpoint so a Dept Head/MD/HR reading the
	approval-request email can open the attachment straight from the email on
	any device, no login needed. This is the reliable fallback for whenever the
	raw MIME attachment itself doesn't make it into the inbox (mail gateway
	strips attachments, size limits, or the attachment was added later via an
	edit that never re-sent the email) - the link always reflects the leave's
	CURRENT attachment, not a snapshot from when the email was sent."""
	doc, error = _validate_email_action_token(token, "view")
	if not doc:
		frappe.throw(error or _("This link is not valid."), frappe.DoesNotExistError)
	if not doc.get("attachment"):
		frappe.throw(_("This leave application has no attachment."), frappe.DoesNotExistError)
	file_row = frappe.db.get_value("File", {"file_url": doc.attachment}, ["name", "file_name"], as_dict=True)
	if not file_row:
		frappe.throw(_("Attachment file not found."), frappe.DoesNotExistError)
	file_doc = frappe.get_doc("File", file_row.name)
	frappe.local.response["filename"] = file_doc.file_name
	frappe.local.response["filecontent"] = file_doc.get_content()
	frappe.local.response["type"] = "download"
	frappe.local.response["display_content_as"] = "inline"


def _notify_stage_advance(doc, employee_doc, level_label, approver_name, next_status):
	"""After a stage is approved: notify the applicant + email the next approver (with buttons)."""
	ctx = _leave_email_context(doc, employee_doc)
	appl = _applicant_email(employee_doc)
	if appl:
		if level_label == "Department Head":
			_send_template_email([appl], "Leave - Employee Notified After Dept Head Approval", ctx, doc)
		elif level_label == "MD":
			_send_template_email([appl], "Leave - Employee Notified After MD Approval", ctx, doc)
	_send_stage_request_email(doc, employee_doc, next_status, ctx)
	_safe_insert_notification(doc.employee, "Leave approved by {0}".format(level_label),
	                          "Now {0}.".format(next_status), "Info", "Leave Application", doc.name)


def _notify_final_approval(doc, employee_doc, approver_name):
	ctx = _leave_email_context(doc, employee_doc)
	appl = _applicant_email(employee_doc)
	if appl:
		_send_template_email([appl], "Leave - Employee Final Approval", ctx, doc)
	_safe_insert_notification(doc.employee, "Leave approved", "Your leave request is approved.", "Info", "Leave Application", doc.name)


def _notify_rejection(doc, employee_doc, level_label, remarks):
	ctx = _leave_email_context(doc, employee_doc)
	ctx.update({
		"rejection_level": level_label,
		"rejected_by_name": _user_display_name(doc.get("rejected_by") or frappe.session.user),
		"rejection_date": frappe.utils.format_datetime(doc.get("rejection_date")) if doc.get("rejection_date") else frappe.utils.format_datetime(now_datetime()),
		"rejection_reason": remarks or "-",
		"rejection_status": doc.get("jew_hrms_approval_status") or "Rejected",
	})
	appl = _applicant_email(employee_doc)
	if appl:
		_send_template_email([appl], "Leave - Employee Rejection", ctx, doc)
	_safe_insert_notification(doc.employee, "Leave rejected", remarks, "Warning", "Leave Application", doc.name)


@frappe.whitelist(allow_guest=True)
def confirm_leave_email_action(token=None, action=None, remarks=None, csrf_token=None):
	"""POST target of the leave_email_action page (one-click email Approve/Reject).
	Runs as the token's intended approver, reusing approve_leave/reject_leave (so the
	full stage machine + next-stage emails fire). Redirects back to a result page."""
	from urllib.parse import urlencode
	def _redirect(**params):
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = "/leave_email_action?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})
	doc, error = _validate_email_action_token(token, action)
	if not doc:
		return _redirect(done=1, ok=0, msg=error or "This link is not valid.")
	if action == "reject" and not (remarks and str(remarks).strip()):
		return _redirect(action="reject", token=token, missing_remarks=1)
	acting_user = doc.get("email_action_token_user") or frappe.session.user
	orig_user = frappe.session.user
	try:
		frappe.set_user(acting_user)
		if action == "approve":
			res = approve_leave(name=doc.name)
		elif action == "reject":
			res = reject_leave(name=doc.name, remarks=remarks)
		else:
			res = {"ok": False, "message": "Unknown action."}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS confirm_leave_email_action failed")
		res = {"ok": False, "message": "Could not complete this action."}
	finally:
		frappe.set_user(orig_user)
	ok = bool(res.get("ok"))
	return _redirect(done=1, ok=1 if ok else 0, msg=res.get("message") or ("Done." if ok else "Could not complete this action."))


def set_initial_leave_approval_stage(doc, method=None):
	"""before_insert hook (hooks.py): stamp the initial approval stage (Department
	Head, or MD when dept-head is skipped) on a new Leave Application. Idempotent —
	apply_leave pre-sets it for mobile leaves; this covers Desk-created ones too.
	Best-effort: never blocks the insert."""
	try:
		if not doc.meta.has_field("jew_hrms_approval_status"):
			return
		current = doc.get("jew_hrms_approval_status")
		if not current or current == APPROVAL_STATUSES.get("draft"):
			emp = frappe.get_doc("Employee", doc.employee) if doc.get("employee") else None
			status, level = _initial_leave_stage(emp, doc.get("from_date")) if emp else (APPROVAL_STATUSES["pending_md"], LEAVE_LEVELS["md"])
			doc.jew_hrms_approval_status = status
			_set_if_has(doc, "current_approval_level", level)
			if status == APPROVAL_STATUSES["pending_md"]:
				_set_if_has(doc, "owner_stage_started_on", now_datetime())
		if not doc.get("status"):
			doc.status = "Open"
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS set_initial_leave_approval_stage failed")


def send_initial_leave_stage_email(doc, method=None):
	"""after_insert hook (hooks.py): email the APPLICANT (submission confirmation) +
	the first-stage approver (Dept Head or MD) with one-click Approve/Reject buttons,
	using the Leave Email Templates. Also drops a Desk bell notification for the
	applicant, since the email above goes through the async Email Queue and can
	silently fail later (e.g. an SMTP daily-limit error) with no way to know that at
	insert time - the applicant should still see, right in Desk, that the leave
	really was submitted, independent of whatever happens to the email. Best-effort
	— never breaks insert."""
	try:
		if not doc.get("employee"):
			return
		employee_doc = frappe.get_doc("Employee", doc.employee)
		status = _leave_approval_status(doc)
		ctx = _leave_email_context(doc, employee_doc)
		appl = _applicant_email(employee_doc)
		if appl:
			_send_template_email([appl], "Leave - Employee Submission Confirmation", ctx, doc)
			try:
				from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
				enqueue_create_notification([appl], {
					"type": "Alert",
					"document_type": doc.doctype,
					"document_name": doc.name,
					"subject": "Your {0} application ({1} to {2}) has been submitted.".format(
						doc.leave_type, _fmt_dmy(doc.from_date), _fmt_dmy(doc.to_date)),
				})
			except Exception:
				frappe.log_error(frappe.get_traceback(), "JEW HRMS applicant Desk bell notification failed")
		_send_stage_request_email(doc, employee_doc, status, ctx)
		_notify_hr_leave_applied(doc, employee_doc, status)
		_notify_dept_head_leave_applied(doc, employee_doc, status)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS send_initial_leave_stage_email failed")


def sync_approved_leave_to_salary_slip(doc, method=None):
	"""on_submit hook (hooks.py). Payroll/leave reconciliation is handled by standard
	HRMS; this stub exists so the hook resolves and leave submission/approval is not
	blocked. Extend here if a custom Salary Slip sync is required."""
	return


def _leave_stage_start(doc, status):
	"""When the leave reached its CURRENT approval stage (older leaves without the stamp fall back)."""
	if status == APPROVAL_STATUSES["pending_dept_head"]:
		return doc.get("dept_head_stage_started_on") or doc.get("creation")
	return doc.get("owner_stage_started_on") or doc.get("modified") or doc.get("creation")


def _last_leave_reminder(doc_name, since):
	"""Creation time of the latest approval reminder logged on this leave during the CURRENT stage."""
	rows = frappe.get_all(
		"Comment",
		filters={"reference_doctype": "Leave Application", "reference_name": doc_name, "comment_type": "Info",
		         "content": ["like", "Approval reminder%"], "creation": [">=", since]},
		fields=["creation"], order_by="creation desc", limit=1,
	)
	return rows[0].creation if rows else None


def _send_leave_reminder_email(doc, employee_doc, status, days_pending, recipients):
	"""Reminder to the approver(s) of the stage the leave is waiting at, with fresh one-click
	Approve / Reject / View links (issuing them retires the links of the earlier mail)."""
	token = _generate_email_action_token(doc, _stage_approver_user(status, employee_doc))
	site = frappe.utils.get_url()
	approve = "{0}/leave_email_action?token={1}&action=approve".format(site, token)
	reject = "{0}/leave_email_action?token={1}&action=reject".format(site, token)
	view = "{0}/leave_email_action?token={1}&action=view".format(site, token)
	ctx = _leave_email_context(doc, employee_doc)
	stage_label = "Department Head" if status == APPROVAL_STATUSES["pending_dept_head"] else "MD"
	esc = frappe.utils.escape_html
	rows = [
		("Employee", "{0} ({1})".format(ctx["employee_name"], ctx["employee_id"])),
		("Leave type", ctx["leave_type"]),
		("Leave period", "{0} to {1}".format(ctx["from_date"], ctx["to_date"])),
		("Total leave days", ctx["total_leave_days"]),
		("Reason", ctx["leave_reason"]),
		("Waiting for", "{0} approval - {1} day(s)".format(stage_label, days_pending)),
	]
	table = "".join("<tr><td style=\"padding:3px 14px 3px 0;color:#64707a;\">{0}</td><td><strong>{1}</strong></td></tr>".format(esc(k), esc(str(v))) for k, v in rows)
	attach = ""
	if doc.get("attachment"):
		attach = "<p><a href=\"{0}/api/method/jew_hrms_mobile.api.view_leave_attachment?token={1}\">View attachment</a></p>".format(site, token)
	message = (
		"<p>Hello,</p>"
		"<p><strong>Reminder:</strong> the leave application below is still waiting for your <strong>{stage}</strong> approval.</p>"
		"<table style=\"border-collapse:collapse;\">{table}</table>{attach}"
		"<p style=\"margin-top:18px;\">"
		"<a href=\"{approve}\" style=\"background:#1a7f45;color:#fff;padding:11px 22px;border-radius:6px;text-decoration:none;font-weight:600;margin-right:10px;\">Approve</a>"
		"<a href=\"{reject}\" style=\"background:#b3341f;color:#fff;padding:11px 22px;border-radius:6px;text-decoration:none;font-weight:600;\">Reject</a></p>"
		"<p style=\"color:#64707a;font-size:12.5px;margin-top:14px;\"><a href=\"{view}\">View details</a> - this application is NOT approved "
		"automatically. You will get this reminder every {every} days until you approve or reject it.</p>"
	).format(stage=stage_label, table=table, attach=attach, approve=approve, reject=reject, view=view, every=LEAVE_REMINDER_EVERY_DAYS)
	frappe.sendmail(
		recipients=sorted({r for r in recipients if r and "@" in r}),
		subject="Reminder ({0} day(s) pending): leave approval needed - {1} ({2} to {3})".format(days_pending, ctx["employee_name"], ctx["from_date"], ctx["to_date"]),
		message=message, sender=_hrms_sender_email(), reference_doctype=doc.doctype, reference_name=doc.name,
	)


def send_leave_approval_reminders(dry_run=False):
	"""Leaves waiting at the Department Head or MD stage are never approved or forwarded automatically. The
	approver of the stage the leave is waiting at gets a reminder every LEAVE_REMINDER_EVERY_DAYS days (counted from
	the later of the stage start and the previous reminder) until they approve or reject. Leaves whose period is
	already over are skipped. Every reminder is logged as an 'Approval reminder' comment on the leave (that comment
	is also what keeps the schedule idempotent). Returns the list of reminders sent / due (dry_run sends nothing)."""
	today_d = getdate(today())
	out = []
	for status in (APPROVAL_STATUSES["pending_dept_head"], APPROVAL_STATUSES["pending_md"]):
		names = frappe.get_all("Leave Application", filters={"jew_hrms_approval_status": status, "docstatus": 0}, pluck="name")
		for nm in names:
			try:
				doc = frappe.get_doc("Leave Application", nm)
				if doc.get("to_date") and getdate(doc.to_date) < today_d:
					continue
				start = get_datetime(_leave_stage_start(doc, status))
				last = _last_leave_reminder(nm, start)
				base = get_datetime(last) if last else start
				if date_diff(today_d, base.date()) < LEAVE_REMINDER_EVERY_DAYS:
					continue
				employee_doc = frappe.get_doc("Employee", doc.employee)
				recipients = _stage_approver_emails(status, employee_doc)
				if not recipients:
					continue
				days_pending = max(date_diff(today_d, start.date()), 0)
				out.append({"leave": nm, "employee": employee_doc.employee_name, "stage": status, "days_pending": days_pending, "recipients": list(recipients), "last_reminder": str(last) if last else None})
				if dry_run:
					continue
				_send_leave_reminder_email(doc, employee_doc, status, days_pending, recipients)
				_notify_approvers_inapp(recipients, doc, employee_doc, status)
				doc.add_comment("Info", "Approval reminder sent to {0} ({1}, {2} day(s) pending)".format(", ".join(recipients), "Department Head" if status == APPROVAL_STATUSES["pending_dept_head"] else "MD", days_pending))
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(frappe.get_traceback(), "JEW HRMS leave approval reminder failed ({0})".format(nm))
	return out


def auto_approve_stale_owner_leaves():
	"""Daily scheduler hook (hooks.py) - kept under this name only because the scheduled job is registered
	with it. It NO LONGER approves or forwards anything: a leave waits at the Department Head / MD stage until
	that person acts, and send_leave_approval_reminders() mails them a reminder every 3 days meanwhile."""
	try:
		send_leave_approval_reminders()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS leave approval reminders failed")


@frappe.whitelist()
def _leave_is_pre_approval(doc):
	"""True while a Leave Application is still Open - a draft (docstatus 0) that is
	pending any approval stage (Department Head, MD or HR) and has not yet reached
	a final state. This is the window during which the applicant may edit their own
	request. Editing stays allowed right through every pending stage, even after an
	earlier stage (Dept Head/MD) has already approved, up until the leave is finally
	Approved / Rejected / Cancelled - at which point it can no longer be edited."""
	if doc.docstatus != 0:
		return False
	status = doc.get("jew_hrms_approval_status") or doc.status
	return status in (
		APPROVAL_STATUSES["pending_dept_head"],
		APPROVAL_STATUSES["pending_md"],
		APPROVAL_STATUSES["pending_hr"],
		APPROVAL_STATUSES["pending_admin"],
	)


def _leave_edit_snapshot(doc):
	"""The editable parts of a Leave Application, in comparable form (used to tell HR what an edit changed)."""
	half = int(doc.get("half_day") or 0)
	return {
		"leave_type": doc.get("leave_type") or "",
		"from_date": getdate(doc.get("from_date")) if doc.get("from_date") else None,
		"to_date": getdate(doc.get("to_date")) if doc.get("to_date") else None,
		"half_day": half,
		"half_day_date": getdate(doc.get("half_day_date")) if (half and doc.get("half_day_date")) else None,
		"half_day_type": (doc.get("custom_half_day_type") or "") if half else "",
		"reason": (doc.get("description") or "").strip(),
		"attachment": doc.get("attachment") or "",
		"total_leave_days": flt(doc.get("total_leave_days") or 0),
	}


def _leave_edit_changes(before, after):
	"""[(label, before_text, after_text)] for every field that really changed - [] when nothing did."""
	def day(v):
		return _fmt_dmy(v) if v else "-"

	def half(snap):
		if not snap["half_day"]:
			return "No"
		bits = [snap["half_day_type"], day(snap["half_day_date"])]
		return "Yes ({0})".format(", ".join(b for b in bits if b and b != "-"))

	def att(snap):
		return (snap["attachment"].rsplit("/", 1)[-1] or "None") if snap["attachment"] else "None"

	rows = []
	if before["leave_type"] != after["leave_type"]:
		rows.append(("Leave type", before["leave_type"] or "-", after["leave_type"] or "-"))
	if before["from_date"] != after["from_date"]:
		rows.append(("From date", day(before["from_date"]), day(after["from_date"])))
	if before["to_date"] != after["to_date"]:
		rows.append(("To date", day(before["to_date"]), day(after["to_date"])))
	if (before["half_day"], before["half_day_date"], before["half_day_type"]) != (after["half_day"], after["half_day_date"], after["half_day_type"]):
		rows.append(("Half day", half(before), half(after)))
	if before["reason"] != after["reason"]:
		rows.append(("Reason", before["reason"] or "-", after["reason"] or "-"))
	if before["attachment"] != after["attachment"]:
		rows.append(("Attachment", att(before), att(after)))
	if rows and before["total_leave_days"] != after["total_leave_days"]:
		rows.append(("Total leave days", "{0:g}".format(before["total_leave_days"]), "{0:g}".format(after["total_leave_days"])))
	return rows


def _notify_hr_leave_updated(doc, before):
	"""E-mail + bell to HR when an EMPLOYEE edits their own leave application, listing exactly what changed
	(before -> after) and the stage the leave is at. Information only; silent when nothing really changed.
	Best-effort - the caller wraps it so an e-mail problem can never undo the edit."""
	changes = _leave_edit_changes(before, _leave_edit_snapshot(doc))
	if not changes:
		return
	recipients = _hr_emails()
	if not recipients:
		return
	employee_doc = frappe.get_doc("Employee", doc.employee)
	emp_name = employee_doc.employee_name or employee_doc.name
	esc = frappe.utils.escape_html
	status = _leave_approval_status(doc) or "-"
	date_range = _fmt_dmy(doc.from_date) if doc.from_date == doc.to_date else "{0} to {1}".format(_fmt_dmy(doc.from_date), _fmt_dmy(doc.to_date))
	head = "<tr><th align=\"left\" style=\"padding:4px 14px 4px 0;\">Field</th><th align=\"left\" style=\"padding:4px 14px 4px 0;\">Before</th><th align=\"left\">Now</th></tr>"
	body_rows = "".join(
		"<tr><td style=\"padding:3px 14px 3px 0;color:#64707a;\">{0}</td><td style=\"padding:3px 14px 3px 0;\">{1}</td><td><strong>{2}</strong></td></tr>".format(esc(k), esc(str(b)), esc(str(a)))
		for k, b, a in changes
	)
	message = (
		"<p><strong>{emp}</strong> ({eid}) has <strong>edited</strong> their leave application "
		"<strong>{name}</strong>.</p><table style=\"border-collapse:collapse;\">{head}{rows}</table>"
		"<p style=\"margin-top:12px;\"><strong>Leave type:</strong> {lt}<br><strong>Leave period:</strong> {dates}<br>"
		"<strong>Current status:</strong> {status}</p>"
		"<p style=\"color:#64707a;font-size:12.5px;margin-top:14px;\">Information only - no action needed here. The approval "
		"stages and their approvers are unchanged.</p>"
	).format(emp=esc(emp_name), eid=esc(employee_doc.name), name=esc(doc.name), head=head, rows=body_rows,
	         lt=esc(doc.leave_type or "-"), dates=date_range, status=esc(status))
	frappe.sendmail(
		recipients=recipients,
		subject="Leave application updated: {0} ({1})".format(emp_name, date_range),
		message=message, sender=_hrms_sender_email(), reference_doctype=doc.doctype, reference_name=doc.name,
	)
	_desk_bell_notify(recipients, doc, "{0} edited their leave {1}: {2}".format(emp_name, doc.name, ", ".join(c[0] for c in changes)))


@frappe.whitelist()
def update_leave(name=None, leave_type=None, from_date=None, to_date=None, half_day=0, half_day_date=None, half_day_type=None, reason=None, attachment=None):
	"""Let the applicant correct their own Leave Application (wrong date, leave
	type, forgot the attachment, etc.) while it is still awaiting the FIRST
	approval stage - once a Department Head/MD has acted, editing is blocked and
	the employee must cancel and re-apply instead, so re-running the same
	validations apply_leave() does keeps the two paths consistent."""
	employee = _get_current_employee()
	is_admin = _is_admin()
	if not name or not frappe.db.exists("Leave Application", name):
		return _fail("Leave request not found.", "not_found")
	doc = frappe.get_doc("Leave Application", name)
	if employee and doc.employee != employee.name and not is_admin:
		return _fail("You do not have permission.", "permission_denied")
	if not _leave_is_pre_approval(doc) and not is_admin:
		return _fail("This leave has already been reviewed by an approver and can no longer be edited. Please cancel it and apply again.", "not_editable")
	if not all([leave_type, from_date, to_date]):
		return _fail("Leave type, from date and to date are required.", "validation_error")
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
	is_half_day = _bool(half_day)
	if is_half_day and not half_dt:
		return _fail("Half Day Date is required.", "validation_error")
	if half_dt and (half_dt < from_dt or half_dt > to_dt):
		return _fail("Half Day Date must be between From Date and To Date.", "validation_error")
	if is_half_day and from_dt != to_dt:
		return _fail("Half day can only be applied on a single-day leave (From and To must be the same day).", "half_day_multi_day")
	if _leave_overlap_exists(doc.employee, from_dt, to_dt, exclude=doc.name):
		return _fail("Leave dates overlap with an existing leave.", "leave_overlap")
	if not _has_leave_allocation(doc.employee, leave_type, from_dt, to_dt):
		return _fail("Leave allocation not found. Please contact HR.", "leave_allocation_not_found")
	cl_cap_error = _check_casual_monthly_cap(doc.employee, leave_type, from_dt, to_dt, is_half_day, exclude=doc.name)
	if cl_cap_error:
		return cl_cap_error
	before_snapshot = _leave_edit_snapshot(doc)
	doc.leave_type = leave_type
	doc.from_date = from_dt
	doc.to_date = to_dt
	doc.half_day = int(is_half_day)
	if is_half_day:
		_set_if_has(doc, "half_day_date", half_dt)
		_set_if_has(doc, "total_leave_days", 0.5)
		_set_if_has(doc, "custom_half_day_type", half_day_type or "First Half")
	else:
		_set_if_has(doc, "half_day_date", None)
	if reason is not None:
		doc.description = reason
	if attachment is not None and doc.meta.has_field("attachment"):
		doc.attachment = attachment
	doc.save(ignore_permissions=True)
	_link_attachment_to_leave(doc)
	frappe.db.commit()
	# Tell HR what the EMPLOYEE just changed (not when HR/admin edits someone else's leave). Best-effort.
	try:
		if employee and employee.name == doc.employee:
			_notify_hr_leave_updated(doc, before_snapshot)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS leave-edit HR notice failed")
	return _ok({"name": doc.name}, "Leave request updated.")


def _link_attachment_to_leave(doc):
	"""The attachment is uploaded (upload_file) BEFORE the Leave Application exists,
	so the resulting private File record has no attached_to_doctype/name - and
	Frappe's File.has_permission() only allows the file's owner (or, when linked,
	whoever can read attached_to_doctype+name) to view a private file. Without
	this, only the applicant who uploaded it could ever open their own medical
	certificate - the Department Head/MD/HR who actually need to review it would
	get a permission error. Linking the File to this Leave Application makes its
	read access follow leave_application_has_permission (self/dept head/MD/HR),
	the same rule that already governs who can see the leave record itself."""
	if not doc.get("attachment"):
		return
	file_name = frappe.db.get_value("File", {"file_url": doc.attachment}, "name")
	if not file_name:
		return
	frappe.db.set_value("File", file_name, "attached_to_doctype", "Leave Application")
	frappe.db.set_value("File", file_name, "attached_to_name", doc.name)
	frappe.db.set_value("File", file_name, "attached_to_field", "attachment")


CANCEL_REQUEST_STATUSES = {"pending_hr": "Pending HR Approval", "approved": "Approved", "rejected": "Rejected"}


def _hr_role_emails():
	"""Enabled emails of everyone holding the JEW HRMS HR role specifically - the
	ONLY role allowed to decide a cancellation request (self-service cancellation
	of an already-approved Leave/Late-Coming/Early-Going application always needs
	an explicit HR sign-off, never the employee or any other approver)."""
	users = frappe.get_all("Has Role", filters={"role": "JEW HRMS HR", "parenttype": "User"}, pluck="parent")
	emails = []
	for u in set(users):
		if u in ("Administrator", "Guest"):
			continue
		info = frappe.db.get_value("User", u, ["email", "enabled"], as_dict=True)
		if info and info.enabled and info.email and "@" in info.email:
			emails.append(info.email)
	return sorted(set(emails))


def _cancellation_context(doc, employee_doc, application_type, from_dt, to_dt, days, reason, decision=None, remarks=None):
	return {
		"employee_name": employee_doc.employee_name or employee_doc.name,
		"employee_id": employee_doc.name,
		"application_type": application_type,
		"from_date": _fmt_dmy(from_dt) if from_dt else "-",
		"to_date": _fmt_dmy(to_dt) if to_dt else "-",
		"days": days if days is not None else "-",
		"reason": reason or "-",
		"decision": decision or "",
		"remarks": remarks or "-",
		"reference_id": doc.name,
		"company_name": doc.get("company") or employee_doc.get("company") or "Jain Engineering Works (India) Private Limited",
	}


def _notify_hr_of_cancellation_request(doc, employee_doc, application_type, from_dt, to_dt, days, reason):
	"""Email + Desk bell + in-app notification to JEW HRMS HR the moment an
	employee requests cancellation of an already-approved application. Best-effort
	- mirrors the resilience pattern used for leave-stage-request notifications
	elsewhere in this file (never let an SMTP hiccup leave HR with zero signal).
	Also issues a one-click action token so HR can Approve/Reject straight from
	the email (cancellation_email_action page) instead of having to open the app."""
	recipients = _hr_role_emails()
	token = frappe.generate_hash(length=48)
	frappe.db.set_value(doc.doctype, doc.name, "jew_cancel_action_token", token, update_modified=False)
	frappe.db.set_value(doc.doctype, doc.name, "jew_cancel_action_token_expiry", add_to_date(now_datetime(), days=CANCEL_ACTION_TOKEN_VALIDITY_DAYS), update_modified=False)
	site = frappe.utils.get_url()
	ctx = _cancellation_context(doc, employee_doc, application_type, from_dt, to_dt, days, reason)
	ctx["approve_link"] = "{0}/cancellation_email_action?token={1}&action=approve".format(site, token)
	ctx["reject_link"] = "{0}/cancellation_email_action?token={1}&action=reject".format(site, token)
	_send_template_email(recipients, "JEW HRMS - Cancellation Request", ctx, doc)
	msg = "{0} requested cancellation of {1} ({2} to {3}, {4} day(s)) - reason: {5}".format(
		employee_doc.employee_name or employee_doc.name, application_type, ctx["from_date"], ctx["to_date"], days, reason or "-")
	if recipients:
		try:
			from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
			enqueue_create_notification(recipients, {
				"type": "Alert", "document_type": doc.doctype, "document_name": doc.name, "subject": msg,
			})
		except Exception:
			frappe.log_error(frappe.get_traceback(), "JEW HRMS cancellation-request Desk notification failed")
	seen = set()
	for r in recipients:
		emp = frappe.db.get_value("Employee", {"user_id": r}, "name")
		if emp and emp not in seen:
			seen.add(emp)
			_safe_insert_notification(emp, "Cancellation request needs your approval", msg, "Info", doc.doctype, doc.name)


def _notify_cancellation_decision(doc, employee_doc, application_type, decision, remarks, from_dt=None, to_dt=None, days=None, reason=None):
	"""Email + in-app notification to the APPLICANT once HR approves/rejects
	their cancellation request."""
	ctx = _cancellation_context(doc, employee_doc, application_type, from_dt, to_dt, days, reason, decision, remarks)
	appl = _applicant_email(employee_doc)
	if appl:
		_send_template_email([appl], "JEW HRMS - Cancellation Decision", ctx, doc)
	_safe_insert_notification(
		doc.employee, "Cancellation request {0}".format(decision.lower()), remarks or decision,
		"Info" if decision == "Approved" else "Warning", doc.doctype, doc.name,
	)


def _validate_cancellation_action_token(token):
	"""Look up a one-click cancellation-decision token across BOTH doctypes that
	can carry one (Leave Application, JEW Late Early Application). Returns
	(doctype, doc, None) or (None, None, error_message)."""
	if not token:
		return None, None, "Invalid or missing action link."
	doctype = "Leave Application"
	name = frappe.db.get_value(doctype, {"jew_cancel_action_token": token}, "name")
	if not name:
		doctype = "JEW Late Early Application"
		name = frappe.db.get_value(doctype, {"jew_cancel_action_token": token}, "name")
	if not name:
		return None, None, "This link is no longer valid — it may have already been used or expired."
	doc = frappe.get_doc(doctype, name)
	expiry = doc.get("jew_cancel_action_token_expiry")
	if expiry and get_datetime(expiry) < now_datetime():
		return None, None, "This action link has expired."
	if doc.get("jew_cancel_status") != CANCEL_REQUEST_STATUSES["pending_hr"]:
		return None, None, "This cancellation request has already been decided."
	return doctype, doc, None


@frappe.whitelist(allow_guest=True)
def confirm_cancellation_email_action(token=None, action=None, remarks=None, csrf_token=None):
	"""POST target of the cancellation_email_action page (one-click HR
	Approve/Reject on a cancellation request straight from the email). Runs as
	an actual JEW HRMS HR user (so the normal decide_* functions' own strict
	HR-only check is satisfied honestly, not bypassed) - reuses whichever
	decide_leave_cancellation/decide_late_early_cancellation applies, so the
	same balance-restoring/partial-split logic runs regardless of whether HR
	acted from the email or from the app."""
	from urllib.parse import urlencode
	def _redirect(**params):
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = "/cancellation_email_action?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})
	doctype, doc, error = _validate_cancellation_action_token(token)
	if not doc:
		return _redirect(done=1, ok=0, msg=error or "This link is not valid.")
	if action not in ("approve", "reject"):
		return _redirect(done=1, ok=0, msg="Unknown action.")
	if action == "reject" and not (remarks and str(remarks).strip()):
		return _redirect(action="reject", token=token, missing_remarks=1)
	hr_emails = _hr_role_emails()
	hr_user = frappe.db.get_value("User", {"email": hr_emails[0]}, "name") if hr_emails else None
	if not hr_user:
		return _redirect(done=1, ok=0, msg="No HR user is configured to action this request.")
	orig_user = frappe.session.user
	try:
		frappe.set_user(hr_user)
		if doctype == "Leave Application":
			res = decide_leave_cancellation(name=doc.name, action=action, remarks=remarks)
		else:
			res = decide_late_early_cancellation(name=doc.name, action=action, remarks=remarks)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS confirm_cancellation_email_action failed")
		res = {"ok": False, "message": "Could not complete this action."}
	finally:
		frappe.set_user(orig_user)
	ok = bool(res.get("ok"))
	return _redirect(done=1, ok=1 if ok else 0, msg=res.get("message") or ("Done." if ok else "Could not complete this action."))


@frappe.whitelist()
def request_leave_cancellation(name=None, from_date=None, to_date=None, reason=None):
	"""Employee-initiated cancellation REQUEST for an already-APPROVED Leave
	Application - this does NOT cancel anything by itself, only HR can decide it
	(decide_leave_cancellation). Supports a partial date range within the
	original leave (e.g. cancel just 2 of 5 approved days) - decide_leave_cancellation
	splits the remaining days back out into their own approved leave so the
	employee doesn't lose days they never asked to cancel."""
	employee = _get_current_employee()
	if not name or not frappe.db.exists("Leave Application", name):
		return _fail("Leave request not found.", "not_found")
	doc = frappe.get_doc("Leave Application", name)
	if doc.employee != employee.name and not _is_admin():
		return _fail("You do not have permission.", "permission_denied")
	if doc.docstatus != 1 or str(doc.status) != "Approved":
		return _fail("Only an approved leave can have a cancellation requested.", "invalid_status")
	if doc.get("jew_cancel_status") == CANCEL_REQUEST_STATUSES["pending_hr"]:
		return _fail("A cancellation request is already pending for this leave.", "already_pending")
	if not from_date or not to_date or not reason:
		return _fail("From date, to date and reason are required.", "validation_error")
	try:
		from_dt = getdate(from_date)
		to_dt = getdate(to_date)
	except Exception:
		return _fail("Invalid dates.", "validation_error")
	if from_dt > to_dt:
		return _fail("From date cannot be after To date.", "validation_error")
	if from_dt < doc.from_date or to_dt > doc.to_date:
		return _fail(
			"Cancellation dates must be within the approved leave period ({0} to {1}).".format(_fmt_dmy(doc.from_date), _fmt_dmy(doc.to_date)),
			"validation_error",
		)
	if bool(doc.get("half_day")):
		if not (from_dt == to_dt == doc.from_date):
			return _fail("This is a half-day leave; cancellation must cover that same single day.", "validation_error")
		cancel_days = 0.5
	else:
		cancel_days = date_diff(to_dt, from_dt) + 1
	doc.jew_cancel_status = CANCEL_REQUEST_STATUSES["pending_hr"]
	doc.jew_cancel_from_date = from_dt
	doc.jew_cancel_to_date = to_dt
	doc.jew_cancel_days = cancel_days
	doc.jew_cancel_reason = reason
	doc.jew_cancel_requested_on = now_datetime()
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	_notify_hr_of_cancellation_request(doc, employee, doc.leave_type, from_dt, to_dt, cancel_days, reason)
	return _ok({"name": doc.name, "whatsapp": _safe_request_whatsapp("leave_cancel_request", doc)}, "Cancellation request sent to HR.")


@frappe.whitelist()
def get_pending_cancellations():
	"""HR-only combined queue: cancellation requests awaiting decision, across
	both Leave Applications and Late/Early Applications."""
	if not _is_workflow_hr():
		# STRICT - the requirement is explicitly "only HR may cancel", not any admin
		# role, so this deliberately does not fall back to _is_admin() the way most
		# other permission checks in this file do.
		return _fail("You do not have permission.", "permission_denied")
	leaves = frappe.get_list(
		"Leave Application",
		filters={"jew_cancel_status": CANCEL_REQUEST_STATUSES["pending_hr"]},
		fields=["name", "employee", "employee_name", "leave_type", "from_date", "to_date",
		        "jew_cancel_from_date", "jew_cancel_to_date", "jew_cancel_days", "jew_cancel_reason", "jew_cancel_requested_on"],
		order_by="jew_cancel_requested_on asc",
		limit_page_length=200,
		ignore_permissions=True,
	)
	for r in leaves:
		r["kind"] = "Leave"
	late_early = frappe.get_list(
		"JEW Late Early Application",
		filters={"jew_cancel_status": CANCEL_REQUEST_STATUSES["pending_hr"]},
		fields=["name", "employee", "employee_name", "application_type", "application_date",
		        "jew_cancel_reason", "jew_cancel_requested_on"],
		order_by="jew_cancel_requested_on asc",
		limit_page_length=200,
		ignore_permissions=True,
	)
	for r in late_early:
		r["kind"] = "Late/Early"
	return _ok({"leaves": leaves, "late_early": late_early})


@frappe.whitelist()
def decide_leave_cancellation(name=None, action=None, remarks=None):
	"""HR-only decision on a pending Leave cancellation request.
	Reject: just marks the request Rejected, leave stays exactly as approved.
	Approve: cancels the original Leave Application (HRMS's own on_cancel
	reverses its leave-ledger entries, restoring the FULL balance), then - if the
	cancelled range was only PART of the original period - re-files the
	remaining, non-cancelled day(s) as a fresh already-Approved Leave Application
	(or two, if the cancelled range was in the middle), so only the actually
	cancelled days are given back and the rest of the employee's leave stands."""
	if not _is_workflow_hr():
		# STRICT - the requirement is explicitly "only HR may cancel", not any admin
		# role, so this deliberately does not fall back to _is_admin() the way most
		# other permission checks in this file do.
		return _fail("You do not have permission.", "permission_denied")
	if not name or not frappe.db.exists("Leave Application", name):
		return _fail("Leave request not found.", "not_found")
	if action not in ("approve", "reject"):
		return _fail("A valid action is required.", "validation_error")
	doc = frappe.get_doc("Leave Application", name)
	if doc.get("jew_cancel_status") != CANCEL_REQUEST_STATUSES["pending_hr"]:
		return _fail("No pending cancellation request on this leave.", "invalid_status")
	employee_doc = frappe.get_doc("Employee", doc.employee)
	cancel_from = getdate(doc.jew_cancel_from_date)
	cancel_to = getdate(doc.jew_cancel_to_date)
	cancel_days = doc.jew_cancel_days
	reason = doc.jew_cancel_reason

	if action == "reject":
		if not remarks:
			return _fail("Remarks are required to reject.", "validation_error")
		doc.jew_cancel_status = CANCEL_REQUEST_STATUSES["rejected"]
		doc.jew_cancel_decided_by = frappe.session.user
		doc.jew_cancel_decided_on = now_datetime()
		doc.jew_cancel_remarks = remarks
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		_notify_cancellation_decision(doc, employee_doc, doc.leave_type, "Rejected", remarks, cancel_from, cancel_to, cancel_days, reason)
		return _ok(message="Cancellation request rejected.")

	original_from, original_to = doc.from_date, doc.to_date
	full_cancel = (cancel_from == original_from and cancel_to == original_to)
	original_leave_type = doc.leave_type
	original_employee = doc.employee
	remainder_names = []
	try:
		if doc.docstatus == 1:
			doc.cancel()
		else:
			doc.status = "Cancelled"
			doc.save(ignore_permissions=True)
		if not full_cancel:
			chunks = []
			if original_from < cancel_from:
				chunks.append((original_from, add_days(cancel_from, -1)))
			if original_to > cancel_to:
				chunks.append((add_days(cancel_to, 1), original_to))
			prev_name = doc.name
			for c_from, c_to in chunks:
				remainder = frappe.new_doc("Leave Application")
				remainder.employee = original_employee
				remainder.employee_name = employee_doc.employee_name
				if remainder.meta.has_field("company"):
					remainder.company = employee_doc.get("company")
				remainder.leave_type = original_leave_type
				remainder.from_date = c_from
				remainder.to_date = c_to
				remainder.half_day = 0
				remainder.description = doc.get("description")
				remainder.status = "Open"
				if remainder.meta.has_field("jew_hrms_approval_status"):
					remainder.jew_hrms_approval_status = APPROVAL_STATUSES["approved"]
					_set_if_has(remainder, "current_approval_level", LEAVE_LEVELS["completed"])
				_set_if_has(remainder, "jew_cancel_split_from", doc.name)
				# Name it the way Frappe's own "Amend" does (original-1, then
				# original-2 for a second split chunk, chained off the first
				# remainder so the two don't collide on the same "-1") so it
				# stays visibly traceable back to the cancelled leave it came
				# from, instead of an unrelated next-in-sequence HR-LAP-... id.
				if remainder.meta.has_field("amended_from"):
					remainder.amended_from = prev_name
				# jew_cancel_split_from points at `doc`, which is ALREADY cancelled by
				# this point - Frappe's default link validation refuses to link to a
				# cancelled document, so this insert needs to skip that check.
				remainder.flags.ignore_links = True
				remainder.insert(ignore_permissions=True)
				prev_name = remainder.name
				remainder.reload()
				remainder.status = "Approved"
				if remainder.docstatus == 0:
					remainder.submit()
				remainder_names.append(remainder.name)
		frappe.db.set_value("Leave Application", doc.name, "jew_cancel_status", CANCEL_REQUEST_STATUSES["approved"])
		frappe.db.set_value("Leave Application", doc.name, "jew_cancel_decided_by", frappe.session.user)
		frappe.db.set_value("Leave Application", doc.name, "jew_cancel_decided_on", now_datetime())
		frappe.db.set_value("Leave Application", doc.name, "jew_cancel_remarks", remarks or "")
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "JEW HRMS decide_leave_cancellation failed")
		return _fail("Unable to process cancellation. Please try again or contact IT.", "setup_incomplete")
	_notify_cancellation_decision(doc, employee_doc, original_leave_type, "Approved", remarks, cancel_from, cancel_to, cancel_days, reason)
	return _ok({"remainder_leaves": remainder_names}, "Cancellation approved.")


@frappe.whitelist()
def request_late_early_cancellation(name=None, reason=None):
	"""Employee-initiated cancellation REQUEST for an already-Approved Late
	Coming / Early Going application - single-day record, so this is always an
	all-or-nothing request (no partial-day concept like Leave has)."""
	employee = _get_current_employee()
	if not name or not frappe.db.exists("JEW Late Early Application", name):
		return _fail("Application not found.", "not_found")
	doc = frappe.get_doc("JEW Late Early Application", name)
	if doc.employee != employee.name and not _is_admin():
		return _fail("You do not have permission.", "permission_denied")
	if doc.status != "Approved":
		return _fail("Only an approved application can have a cancellation requested.", "invalid_status")
	if doc.get("jew_cancel_status") == CANCEL_REQUEST_STATUSES["pending_hr"]:
		return _fail("A cancellation request is already pending for this application.", "already_pending")
	if not reason:
		return _fail("Reason is required.", "validation_error")
	doc.jew_cancel_status = CANCEL_REQUEST_STATUSES["pending_hr"]
	doc.jew_cancel_reason = reason
	doc.jew_cancel_requested_on = now_datetime()
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	_notify_hr_of_cancellation_request(doc, employee, doc.application_type, doc.application_date, doc.application_date, 1, reason)
	return _ok({"name": doc.name, "whatsapp": _safe_request_whatsapp("late_early_cancel_request", doc)}, "Cancellation request sent to HR.")


@frappe.whitelist()
def decide_late_early_cancellation(name=None, action=None, remarks=None):
	"""HR-only decision on a pending Late/Early cancellation request."""
	if not _is_workflow_hr():
		# STRICT - the requirement is explicitly "only HR may cancel", not any admin
		# role, so this deliberately does not fall back to _is_admin() the way most
		# other permission checks in this file do.
		return _fail("You do not have permission.", "permission_denied")
	if not name or not frappe.db.exists("JEW Late Early Application", name):
		return _fail("Application not found.", "not_found")
	if action not in ("approve", "reject"):
		return _fail("A valid action is required.", "validation_error")
	doc = frappe.get_doc("JEW Late Early Application", name)
	if doc.get("jew_cancel_status") != CANCEL_REQUEST_STATUSES["pending_hr"]:
		return _fail("No pending cancellation request on this application.", "invalid_status")
	employee_doc = frappe.get_doc("Employee", doc.employee)
	reason = doc.jew_cancel_reason
	if action == "reject":
		if not remarks:
			return _fail("Remarks are required to reject.", "validation_error")
		doc.jew_cancel_status = CANCEL_REQUEST_STATUSES["rejected"]
		doc.jew_cancel_decided_by = frappe.session.user
		doc.jew_cancel_decided_on = now_datetime()
		doc.jew_cancel_remarks = remarks
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		_notify_cancellation_decision(doc, employee_doc, doc.application_type, "Rejected", remarks, doc.application_date, doc.application_date, 1, reason)
		return _ok(message="Cancellation request rejected.")
	doc.status = "Cancelled"
	doc.jew_cancel_status = CANCEL_REQUEST_STATUSES["approved"]
	doc.jew_cancel_decided_by = frappe.session.user
	doc.jew_cancel_decided_on = now_datetime()
	doc.jew_cancel_remarks = remarks or ""
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	_notify_cancellation_decision(doc, employee_doc, doc.application_type, "Approved", remarks, doc.application_date, doc.application_date, 1, reason)
	return _ok(message="Cancellation approved.")


@frappe.whitelist()
def apply_leave(employee=None, leave_type=None, from_date=None, to_date=None, half_day=0, half_day_date=None, half_day_type=None, reason=None, attachment=None):
	# This whole pre-check block runs BEFORE the try/except further down, so any
	# PermissionError raised in here (e.g. by _get_current_employee() if the
	# session/employee lookup misbehaves for some real request) used to propagate
	# fully unhandled: Frappe treats PermissionError as an "expected" exception
	# and does NOT write it to Error Log by default, so this was a completely
	# silent failure mode - the user saw a generic "You do not have permission"
	# with literally no trace of it anywhere. Wrapping it here is what finally
	# makes the next real occurrence diagnosable.
	try:
		is_admin = _is_admin()
		current_employee = None if employee and is_admin else _get_current_employee()
	except frappe.PermissionError:
		frappe.log_error(
			"apply_leave pre-check PermissionError: employee={0}, user={1}\n{2}".format(
				employee, frappe.session.user, traceback.format_exc()),
			"JEW HRMS apply_leave PermissionError (pre-checks)",
		)
		return _fail("You do not have permission.", "permission_denied")
	if employee and current_employee and employee != current_employee.name and not is_admin:
		# Same diagnostic gap as the PermissionError branch below: this generic
		# message has hit real users on the live app with no way to tell WHICH of
		# the two return points in this function actually fired. Log the values
		# so the next occurrence is finally traceable.
		frappe.log_error(
			"apply_leave permission_denied (early check): called_employee={0}, current_employee={1}, is_admin={2}, user={3}".format(
				employee, current_employee.name, is_admin, frappe.session.user),
			"JEW HRMS apply_leave permission_denied (early check)",
		)
		return _fail("You do not have permission.", "permission_denied")
	try:
		if employee and is_admin:
			employee_doc = frappe.get_doc("Employee", employee)
		elif current_employee:
			employee_doc = current_employee
		else:
			return _fail("Employee is required.", "validation_error")
	except frappe.PermissionError:
		frappe.log_error(
			"apply_leave employee_doc PermissionError: employee={0}, user={1}\n{2}".format(
				employee, frappe.session.user, traceback.format_exc()),
			"JEW HRMS apply_leave PermissionError (employee lookup)",
		)
		return _fail("You do not have permission.", "permission_denied")
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
	if not is_admin and from_dt < add_days(getdate(today()), -LEAVE_BACKDATE_ALLOWED_DAYS):
		return _fail(
			"Leave cannot be back-dated more than {0} days. Please contact HR.".format(LEAVE_BACKDATE_ALLOWED_DAYS),
			"past_date_not_allowed",
		)
	is_half_day = _bool(half_day)
	if is_half_day and not half_dt:
		return _fail("Half Day Date is required.", "validation_error")
	if half_dt and (half_dt < from_dt or half_dt > to_dt):
		return _fail("Half Day Date must be between From Date and To Date.", "validation_error")
	if is_half_day and from_dt != to_dt:
		return _fail("Half day can only be applied on a single-day leave (From and To must be the same day).", "half_day_multi_day")
	if _leave_overlap_exists(employee_doc.name, from_dt, to_dt):
		return _fail("Leave dates overlap with an existing leave.", "leave_overlap")
	if not _has_leave_allocation(employee_doc.name, leave_type, from_dt, to_dt):
		return _fail("Leave allocation not found. Please contact HR.", "leave_allocation_not_found")
	cl_cap_error = _check_casual_monthly_cap(employee_doc.name, leave_type, from_dt, to_dt, is_half_day)
	if cl_cap_error:
		return cl_cap_error
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
		if attachment and doc.meta.has_field("attachment"):
			# Set BEFORE insert, not after: the after_insert hook
			# (send_initial_leave_stage_email) fires the very first approver email
			# during doc.insert() below, and it needs doc.attachment already
			# populated to attach the file to that email.
			doc.attachment = attachment
		if doc.meta.has_field("jew_hrms_approval_status"):
			init_status, init_level = _initial_leave_stage(employee_doc, from_dt)
			doc.jew_hrms_approval_status = init_status
			_set_if_has(doc, "current_approval_level", init_level)
			if init_status == APPROVAL_STATUSES["pending_md"]:
				_set_if_has(doc, "owner_stage_started_on", now_datetime())
		doc.insert(ignore_permissions=True)
		if doc.get("attachment"):
			_link_attachment_to_leave(doc)
		_safe_insert_notification(employee_doc.name, "Leave submitted", f"{leave_type} leave is pending approval.", "Info", "Leave Application", doc.name)
		# Approver email is sent by the after_insert hook (send_initial_leave_stage_email)
		# during doc.insert() above — no explicit call here (avoids a duplicate email).
		message = "Leave request submitted successfully."
		frappe.db.commit()
		# WhatsApp text for the group post. Built AFTER the commit and fully isolated: whatever happens here the
		# leave stays submitted and the response stays a success (an error would hit the generic handler below).
		whatsapp = None
		try:
			whatsapp = _leave_whatsapp_payload(doc, employee_doc)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "JEW HRMS leave WhatsApp text failed")
		return _ok({"name": doc.name, "leave_application": doc.name, "docstatus": doc.docstatus, "approval_status": _leave_approval_status(doc), "whatsapp": whatsapp}, message)
	except frappe.PermissionError:
		frappe.db.rollback()
		# This has hit multiple real employees on the live app while an identical
		# console replay always succeeds - meaning something in this insert/hook
		# chain occasionally raises a genuine PermissionError only under real
		# request conditions. Log the full traceback so the NEXT occurrence is
		# finally diagnosable instead of silently returning this generic message.
		frappe.log_error(traceback.format_exc(), "JEW HRMS apply_leave PermissionError")
		return _fail("You do not have permission.", "permission_denied")
	except frappe.ValidationError as exc:
		# HRMS raises clean, user-facing messages here (insufficient balance,
		# holiday/overlap conflicts, etc.). Surface them instead of the generic
		# fallback so employees know exactly why the leave was rejected.
		frappe.db.rollback()
		clean = frappe.utils.strip_html(str(exc) or "").strip()
		low = clean.lower()
		if "allocation" in low:
			return _fail("Leave allocation not found. Please contact HR.", "leave_allocation_not_found")
		if "balance" in low or "not enough" in low:
			return _fail(clean or "Insufficient leave balance for this leave type.", "insufficient_balance")
		return _fail(clean or "Unable to submit leave. Please contact HR.", "validation_error")
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


def _leave_whatsapp_payload(doc, employee_doc=None):
	"""{"text", "group_url"} for posting a leave request in the WhatsApp group, or None when the step is switched off.
	The text is plain WhatsApp markup (*bold*, new lines). No approval status on purpose - it is posted right after
	applying, when it is always "pending"."""
	group_url = frappe.conf.get("jew_hrms_leave_whatsapp_group_url", LEAVE_WHATSAPP_GROUP_URL)
	if not group_url:
		return None
	emp = employee_doc or frappe.get_doc("Employee", doc.employee)

	def _d(value):
		return getdate(value).strftime("%d %b %Y")

	from_dt, to_dt = getdate(doc.from_date), getdate(doc.to_date)
	if doc.get("half_day"):
		half = str(doc.get("custom_half_day_type") or "").strip()
		span = "%s (Half day%s)" % (_d(from_dt), " - " + half if half else "")
	else:
		days = flt(doc.get("total_leave_days") or 0) or (date_diff(to_dt, from_dt) + 1)
		count = "%g day%s" % (days, "" if days == 1 else "s")
		span = "%s (%s)" % (_d(from_dt), count) if from_dt == to_dt else "%s to %s (%s)" % (_d(from_dt), _d(to_dt), count)
	dept = str(emp.get("department") or "").strip()
	if " - " in dept:                       # drop the company suffix of the department ("Accounts - AIL")
		head, tail = dept.rsplit(" - ", 1)
		if len(tail) <= 8:
			dept = head
	reason = " ".join(frappe.utils.strip_html(str(doc.get("description") or "")).split())
	if len(reason) > 300:
		reason = reason[:297].rstrip() + "..."
	lines = ["*Leave Application*", "Employee: %s (%s)" % (emp.get("employee_name") or emp.name, emp.name)]
	if dept:
		lines.append("Department: %s" % dept)
	lines += [
		"Leave type: %s" % doc.leave_type,
		"Dates: %s" % span,
		"Reason: %s" % (reason or "-"),
		"Applied on: %s" % get_datetime(doc.creation).strftime("%d %b %Y, %I:%M %p"),
	]
	return {"text": "\n".join(lines), "group_url": group_url}


@frappe.whitelist()
def get_leave_whatsapp(name=None):
	"""The WhatsApp text for an existing leave request - lets the app offer "Send to WhatsApp group" again later."""
	try:
		me = _get_current_employee().name
	except frappe.PermissionError:
		me = None
	if not name or not frappe.db.exists("Leave Application", name):
		return _fail("This leave request no longer exists.", "not_found")
	doc = frappe.get_doc("Leave Application", name)
	if doc.employee != me and not _is_admin():
		return _fail("You do not have permission.", "permission_denied")
	payload = _leave_whatsapp_payload(doc)
	if not payload:
		return _fail("WhatsApp sharing is switched off.", "disabled")
	return _ok({"whatsapp": payload})


_WA_REQUEST_DOCTYPES = {"late_early": "JEW Late Early Application", "on_duty": "Attendance Request", "regularization": "JEW Attendance Regularization",
						"leave_cancelled": "Leave Application", "leave_cancel_request": "Leave Application",
						"late_early_cancelled": "JEW Late Early Application", "late_early_cancel_request": "JEW Late Early Application"}


def _wa_time(value):
	try:
		return frappe.utils.get_time(str(value)).strftime("%I:%M %p").lstrip("0")
	except Exception:
		return str(value or "")


def _wa_clean(text, limit=300):
	text = " ".join(frappe.utils.strip_html(str(text or "")).split())
	return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _request_whatsapp_payload(kind, doc, employee_doc=None):
	"""{"text", "group_url"} for posting an On Duty / Late-Early / Regularization request in the same WhatsApp group
	as leave requests (see _leave_whatsapp_payload). Same format rules: WhatsApp *bold* title, no approval status."""
	group_url = frappe.conf.get("jew_hrms_leave_whatsapp_group_url", LEAVE_WHATSAPP_GROUP_URL)
	if not group_url:
		return None
	emp = employee_doc or frappe.get_doc("Employee", doc.employee)

	def _d(value):
		return getdate(value).strftime("%d %b %Y")

	def _span(from_dt, to_dt, days=None, half=False, half_type=""):
		from_dt, to_dt = getdate(from_dt), getdate(to_dt)
		if half:
			return "%s (Half day%s)" % (_d(from_dt), " - " + half_type if half_type else "")
		days = flt(days or 0) or (date_diff(to_dt, from_dt) + 1)
		count = "%g day%s" % (days, "" if days == 1 else "s")
		return "%s (%s)" % (_d(from_dt), count) if from_dt == to_dt else "%s to %s (%s)" % (_d(from_dt), _d(to_dt), count)

	footer_label, footer_dt = "Applied on", doc.creation
	if kind == "late_early":
		title = "*%s Request*" % (doc.get("application_type") or "Late / Early")
		rows = [("Date", _d(doc.application_date)), ("Expected time", _wa_time(doc.expected_time)), ("Reason", _wa_clean(doc.reason))]
	elif kind == "on_duty":
		title = "*On Duty Request*"
		from_dt, to_dt = getdate(doc.from_date), getdate(doc.to_date)
		days = date_diff(to_dt, from_dt) + 1
		span = _d(from_dt) if from_dt == to_dt else "%s to %s" % (_d(from_dt), _d(to_dt))
		if doc.get("half_day") and doc.get("half_day_date"):
			part = str(doc.get("custom_half_day_type") or "").strip()
			span += (" (Half day on %s" % _d(doc.half_day_date) if from_dt != to_dt else " (Half day") + (" · %s On Duty)" % part if part else ")")
		else:
			span += " (%d day%s)" % (days, "" if days == 1 else "s")
		reason, time_text = str(doc.get("explanation") or ""), ""
		reason = __import__("re").sub(r"^\[[^\]]*On Duty[^\]]*\]\s*", "", reason)
		m = __import__("re").search(r"\(Time:\s*(.+?)\)\s*$", reason)          # request_on_duty folds the time window into the text
		if m:
			time_text, reason = m.group(1).strip(), reason[: m.start()]
		rows = [("Dates", span)] + ([("Time", time_text)] if time_text else []) + [("Reason", _wa_clean(reason))]
	elif kind == "regularization":
		title = "*Attendance Regularization*"
		rows = [("Date", _d(doc.attendance_date)), ("Issue", doc.get("issue_type")), ("Reason", _wa_clean(doc.get("remarks")))]
	elif kind in ("leave_cancelled", "leave_cancel_request"):
		half = bool(doc.get("half_day"))
		half_type = str(doc.get("custom_half_day_type") or "").strip()
		leave_span = _span(doc.from_date, doc.to_date, doc.get("total_leave_days"), half, half_type)
		if kind == "leave_cancelled":
			title = "*Leave Cancelled*"
			rows = [("Leave type", doc.leave_type), ("Dates", leave_span)]
			footer_label, footer_dt = "Cancelled on", doc.modified
		else:
			title = "*Leave Cancellation Request*"
			c_from, c_to = doc.get("jew_cancel_from_date") or doc.from_date, doc.get("jew_cancel_to_date") or doc.to_date
			rows = [("Leave type", doc.leave_type), ("Leave dates", leave_span), ("Cancel dates", _span(c_from, c_to, doc.get("jew_cancel_days"), half, half_type)),
					("Reason", _wa_clean(doc.get("jew_cancel_reason")))]
			footer_label, footer_dt = "Requested on", doc.get("jew_cancel_requested_on") or doc.modified
	elif kind in ("late_early_cancelled", "late_early_cancel_request"):
		kind_name = doc.get("application_type") or "Late / Early"
		base = [("Date", _d(doc.application_date)), ("Expected time", _wa_time(doc.expected_time))]
		if kind == "late_early_cancelled":
			title = "*%s Cancelled*" % kind_name
			rows = base
			footer_label, footer_dt = "Cancelled on", doc.modified
		else:
			title = "*%s Cancellation Request*" % kind_name
			rows = base + [("Reason", _wa_clean(doc.get("jew_cancel_reason")))]
			footer_label, footer_dt = "Requested on", doc.get("jew_cancel_requested_on") or doc.modified
	else:
		return None
	dept = str(emp.get("department") or "").strip()
	if " - " in dept:                       # drop the company suffix of the department ("Accounts - AIL")
		head, tail = dept.rsplit(" - ", 1)
		if len(tail) <= 8:
			dept = head
	lines = [title, "Employee: %s (%s)" % (emp.get("employee_name") or emp.name, emp.name)]
	if dept:
		lines.append("Department: %s" % dept)
	lines += ["%s: %s" % (label, value or "-") for label, value in rows]
	lines.append("%s: %s" % (footer_label, get_datetime(footer_dt).strftime("%d %b %Y, %I:%M %p")))
	return {"text": "\n".join(lines), "group_url": group_url}


def _safe_request_whatsapp(kind, doc, employee_doc=None):
	"""Never raises - a WhatsApp text problem must not turn a saved request into an error."""
	try:
		return _request_whatsapp_payload(kind, doc, employee_doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS request WhatsApp text failed")
		return None


@frappe.whitelist()
def get_request_whatsapp(kind=None, name=None):
	"""WhatsApp text for an existing On Duty / Late-Early / Regularization request (owner or admin)."""
	doctype = _WA_REQUEST_DOCTYPES.get(kind)
	if not doctype:
		return _fail("Unknown request type.", "validation_error")
	try:
		me = _get_current_employee().name
	except frappe.PermissionError:
		me = None
	if not name or not frappe.db.exists(doctype, name):
		return _fail("This request no longer exists.", "not_found")
	doc = frappe.get_doc(doctype, name)
	if doc.employee != me and not _is_admin():
		return _fail("You do not have permission.", "permission_denied")
	payload = _request_whatsapp_payload(kind, doc)
	if not payload:
		return _fail("WhatsApp sharing is switched off.", "disabled")
	return _ok({"whatsapp": payload})


@frappe.whitelist()
def get_my_leaves(limit=50):
	employee = _get_current_employee()
	fields = ["name", "leave_type", "from_date", "to_date", "status", "description", "half_day", "half_day_date", "docstatus"]
	if _approval_field_exists():
		fields.extend(["jew_hrms_approval_status", "reject_reason", "dept_head_approved_by", "owner_approved_by"])
	if frappe.get_meta("Leave Application").has_field("attachment"):
		fields.append("attachment")
	if frappe.get_meta("Leave Application").has_field("custom_half_day_type"):
		fields.append("custom_half_day_type")
	if frappe.get_meta("Leave Application").has_field("jew_cancel_status"):
		fields.extend(["jew_cancel_status", "jew_cancel_from_date", "jew_cancel_to_date", "jew_cancel_days", "jew_cancel_remarks"])
	leaves = frappe.get_list("Leave Application", filters={"employee": employee.name}, fields=fields, order_by="from_date desc", limit_page_length=int(limit or 50), ignore_permissions=True)
	for leave in leaves:
		leave.approval_status = leave.get("jew_hrms_approval_status") or leave.status
		leave.editable = _leave_is_pre_approval(leave)
	return _ok({"leaves": leaves})


@frappe.whitelist()
def cancel_leave(name=None):
	employee = _get_current_employee()
	if not name or not frappe.db.exists("Leave Application", name):
		# Confirmed root cause of a confusing "Cancel failed" for real users: the
		# app's "My requests" list is only fetched when the Leave tab opens, so if
		# the record is deleted elsewhere (e.g. via Desk) while that list is still
		# on screen, tapping its stale Cancel button used to crash here with a raw
		# DoesNotExistError instead of a clear message.
		return _fail("This leave request no longer exists. Pull to refresh and try again.", "not_found")
	doc = frappe.get_doc("Leave Application", name)
	if doc.employee != employee.name and not _is_admin():
		frappe.throw(_("Permission denied"), frappe.PermissionError)
	# The explicit self/admin check above is the real gate - same as every other
	# whitelisted function in this file, skip the framework's own has_permission
	# check (which for this doctype means our custom leave_application_has_permission
	# hook) so a mismatch there can never surface as a confusing "Cancel failed"
	# for an employee cancelling their own leave.
	doc.flags.ignore_permissions = True
	if doc.docstatus == 1:
		doc.cancel()
	else:
		doc.status = "Cancelled"
		_set_if_has(doc, "jew_hrms_approval_status", APPROVAL_STATUSES["cancelled"])
		doc.save(ignore_permissions=True)
	frappe.db.commit()
	return _ok({"whatsapp": _safe_request_whatsapp("leave_cancelled", doc)}, "Leave cancelled")


@frappe.whitelist()
def get_pending_leaves():
	"""Pending leaves the CURRENT user may actually act on (strict — see
	_leave_stage_actor): a dept head sees only their own employees' dept-head-stage
	leaves; MD (Aditya) sees MD-stage; HR (Kavita) sees HR-stage."""
	_require_login()
	user = frappe.session.user
	fields = ["name", "employee", "employee_name", "leave_type", "from_date", "to_date",
	          "description", "status", "half_day", "jew_hrms_approval_status", "current_approval_level", "reject_reason", "creation"]
	pending = [APPROVAL_STATUSES["pending_dept_head"], APPROVAL_STATUSES["pending_md"],
	           APPROVAL_STATUSES["pending_hr"], APPROVAL_STATUSES["pending_admin"]]
	rows = frappe.get_all("Leave Application", filters={"jew_hrms_approval_status": ["in", pending], "docstatus": 0},
	                      fields=fields, order_by="from_date asc", limit_page_length=0, ignore_permissions=True)
	result = []
	for r in rows:
		emp = frappe.get_doc("Employee", r.employee) if r.get("employee") else None
		if _leave_stage_actor(r.get("jew_hrms_approval_status"), user, emp):
			r["approval_status"] = r.get("jew_hrms_approval_status") or r.status
			# Same "leave taken in last 15 days" figure shown in the approval emails and
			# on the Desk form - shown in the app too when this pending-leaves screen opens.
			r["recent_leave_count"] = _recent_leave_days_count(r.employee, r.get("creation"), exclude_name=r.name)
			result.append(r)
	return _ok({"leaves": result})


def _is_workflow_md(user=None):
	"""MD approver = holds the JEW HRMS Owner role ONLY (tight — not the broad
	OWNER_ROLES which also matches System Manager). Trimmed to a single MD."""
	return "JEW HRMS Owner" in _roles(user)


def _is_workflow_hr(user=None):
	"""HR approver = holds the JEW HRMS HR role ONLY (tight — not the broad HR_ROLES
	which also matches HR Manager / HR User / Leave Approver)."""
	return "JEW HRMS HR" in _roles(user)


def _is_any_dept_head(user=None):
	"""True if this user is the configured department head (custom_dept_head_email) of
	any active employee — used to grant leave-approval access in the app to dept heads
	who hold no JEW HRMS approver role."""
	u = user or frappe.session.user
	if not u or u in ("Guest",):
		return False
	email = frappe.db.get_value("User", u, "email") or u
	return bool(
		frappe.db.exists("Employee", {"status": "Active", "custom_dept_head_email": email})
		or (email != u and frappe.db.exists("Employee", {"status": "Active", "custom_dept_head_email": u}))
	)


def _can_view_all_leaves(user=None):
	"""Full company-wide Leave Application visibility: Admin/System Manager, any HR
	role, or MD (JEW HRMS Owner). Department Heads are handled separately (team-only,
	not company-wide) by leave_application_permission_query / _has_permission below."""
	return _is_admin(user) or _is_hr(user) or _is_workflow_md(user)


def _own_and_team_employees(user=None):
	"""This user's own Employee name plus their department-head team, as a set.
	Shared building block for every 'self + my team, unless Admin/HR/MD' permission
	check across Leave Application / Leave Details."""
	employee = _get_employee_for_user(user)
	names = set(_dept_head_team(user))
	if employee:
		names.add(employee.name)
	return names


def _employee_scoped_permission_query(doctype, user=None):
	"""Generic Desk/report-view row filter, keyed off a doctype's `employee` link
	field: a plain employee sees only their own rows; a Department Head additionally
	sees their own team's; Admin/HR/MD see everyone (no filter)."""
	user = user or frappe.session.user
	if _can_view_all_leaves(user):
		return ""
	names = _own_and_team_employees(user)
	if not names:
		# No Employee record and not a dept head for anyone — see nothing.
		return "1=0"
	quoted = ", ".join(frappe.db.escape(n) for n in names)
	return f"`tab{doctype}`.employee in ({quoted})"


def _employee_scoped_has_permission(doc, user=None):
	"""Generic single-document counterpart of _employee_scoped_permission_query —
	guards direct-link/API access to one record (the list filter alone doesn't stop
	someone opening another employee's record by name/URL)."""
	user = user or frappe.session.user
	if _can_view_all_leaves(user):
		return True
	return doc.employee in _own_and_team_employees(user)


def leave_application_permission_query(user=None):
	"""Desk/report-view row filter for Leave Application: a plain employee sees only
	their own leave records; a Department Head additionally sees their own team's;
	Admin/HR/MD see everyone (no filter). Registered as `permission_query_conditions`
	in hooks.py — this is what actually stops one employee from browsing another's
	leave applications in Desk list/report views (previously unrestricted)."""
	return _employee_scoped_permission_query("Leave Application", user)


def leave_application_has_permission(doc, user=None, permission_type=None):
	"""Single-document counterpart of leave_application_permission_query — guards
	direct-link/API access to one Leave Application (the list filter alone doesn't
	stop someone opening another employee's record by name/URL)."""
	return _employee_scoped_has_permission(doc, user)


def leave_details_permission_query(user=None):
	"""Same self/team/everyone visibility rule as Leave Application, applied to the
	Leave Details admin doctype (monthly CL/PL/LWP booking). Leave Details' own
	create/submit/cancel stay gated to Admin/HR/MD by _require_roles() in
	leave_details.py regardless of this — this hook only governs who can *see* a
	record (the Employee DocPerm row added for read/report access)."""
	return _employee_scoped_permission_query("Leave Details", user)


def leave_details_has_permission(doc, user=None, permission_type=None):
	"""Single-document counterpart of leave_details_permission_query."""
	return _employee_scoped_has_permission(doc, user)


def _leave_stage_actor(status, user, employee_doc):
	"""Return (level_label, is_final) if `user` may act on `status`, else None. STRICT:
	dept-head stage → only that employee's configured dept head; MD stage → only the
	JEW HRMS Owner (Aditya); HR stage → only the JEW HRMS HR (Kavita). A super-admin can
	still edit the doc directly in Desk if a stage is ever stuck."""
	if status == APPROVAL_STATUSES["pending_dept_head"] and employee_doc and _is_dept_head_of(user, employee_doc):
		return "Department Head", False
	if status == APPROVAL_STATUSES["pending_md"] and _is_workflow_md(user):
		return "MD", False
	if status in (APPROVAL_STATUSES["pending_hr"], APPROVAL_STATUSES["pending_admin"]) and _is_workflow_hr(user):
		return "HR", True
	return None


@frappe.whitelist()
def get_recent_leave_info(name=None):
	"""Desk-side counterpart of the 'recent_leave_count' shown in the Dept
	Head/MD/HR approval-request emails - so an approver deciding straight from
	Desk (not the email) sees the same number: how many days of APPROVED leave
	this employee already took in the 15 days before the day this application
	was ACTUALLY APPLIED (doc.creation) - not before the leave's own from_date,
	which may be weeks in the future. Never throws for a non-approver; the
	Client Script just won't show the line if this comes back empty."""
	if not name or not frappe.db.exists("Leave Application", name):
		return {"recent_leave_count": None}
	doc = frappe.get_doc("Leave Application", name)
	if not doc.get("employee") or not doc.get("creation"):
		return {"recent_leave_count": None}
	count = _recent_leave_days_count(doc.employee, doc.creation, exclude_name=doc.name)
	return {
		"recent_leave_count": count,
		"window_days": 15,
		"before_date": _fmt_dmy(doc.creation),
	}


@frappe.whitelist()
def can_i_approve_this_leave(name=None):
	"""Desk 'Leave Application Stage Approval Buttons' client script uses this to show
	Approve/Reject buttons. Returns {can_approve, stage}. NEVER throws for a
	non-approver (that would surface as a Desk error popup)."""
	try:
		if not name or not frappe.db.exists("Leave Application", name):
			return {"can_approve": False}
		doc = frappe.get_doc("Leave Application", name)
		if doc.docstatus != 0:
			return {"can_approve": False}
		status = _leave_approval_status(doc)
		employee_doc = frappe.get_doc("Employee", doc.employee) if doc.get("employee") else None
		actor = _leave_stage_actor(status, frappe.session.user, employee_doc)
		return {"can_approve": bool(actor), "stage": status}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS can_i_approve_this_leave failed")
		return {"can_approve": False}


@frappe.whitelist()
def approve_leave(name=None, remarks=None):
	"""Advance a leave one stage: Dept Head → MD → HR (final) → Approved. The acting
	user must be authorised for the CURRENT stage. Emails the applicant (who approved)
	and the next approver only."""
	_require_login()
	doc = frappe.get_doc("Leave Application", name)
	status = _leave_approval_status(doc)
	if doc.docstatus == 2 or status in (APPROVAL_STATUSES["approved"], APPROVAL_STATUSES["cancelled"]) or str(doc.status) in ("Approved", "Cancelled", "Rejected"):
		return _fail("This leave is already finalised.", "already_finalised")
	employee_doc = frappe.get_doc("Employee", doc.employee)
	user = frappe.session.user
	actor = _leave_stage_actor(status, user, employee_doc)
	if not actor:
		return _fail("You cannot approve this leave at its current stage.", "permission_denied")
	level_label, is_final = actor
	approver_name = _user_display_name(user)
	if level_label == "Department Head":
		_set_if_has(doc, "dept_head_approved_by", user)
		_set_if_has(doc, "dept_head_approved_on", now_datetime())
		if remarks:
			_set_if_has(doc, "dept_head_approval_remarks", remarks)
		# Same-day / backdated leaves skip MD (Dept Head -> HR). So does a leave approved by a Department
		# Head who is ALSO an MD (_approver_is_md_like): one approval, then straight to HR.
		if _leave_skips_md(doc) or _approver_is_md_like(user):
			next_status, next_level, next_label = APPROVAL_STATUSES["pending_hr"], LEAVE_LEVELS["hr"], "HR"
		else:
			next_status, next_level, next_label = APPROVAL_STATUSES["pending_md"], LEAVE_LEVELS["md"], "MD"
		doc.jew_hrms_approval_status = next_status
		_set_if_has(doc, "current_approval_level", next_level)
		_set_if_has(doc, "owner_stage_started_on", now_datetime())
		doc.status = "Open"
		doc.save(ignore_permissions=True)
		_notify_stage_advance(doc, employee_doc, "Department Head", approver_name, next_status)
		frappe.db.commit()
		return _ok({"approval_status": next_status}, "Approved by Department Head — pending {0}.".format(next_label))
	if level_label == "MD":
		_set_if_has(doc, "owner_approved_by", user)
		_set_if_has(doc, "owner_approved_on", now_datetime())
		if remarks:
			_set_if_has(doc, "md_approval_remarks", remarks)
		doc.jew_hrms_approval_status = APPROVAL_STATUSES["pending_hr"]
		_set_if_has(doc, "current_approval_level", LEAVE_LEVELS["hr"])
		doc.status = "Open"
		doc.save(ignore_permissions=True)
		_notify_stage_advance(doc, employee_doc, "MD", approver_name, APPROVAL_STATUSES["pending_hr"])
		frappe.db.commit()
		return _ok({"approval_status": APPROVAL_STATUSES["pending_hr"]}, "Approved by MD — pending HR.")
	# HR — final
	_set_if_has(doc, "hr_approved_by", user)
	_set_if_has(doc, "hr_approved_on", now_datetime())
	_set_if_has(doc, "final_approved_by", user)
	_set_if_has(doc, "final_approved_on", now_datetime())
	if remarks:
		_set_if_has(doc, "hr_approval_remarks", remarks)
	doc.jew_hrms_approval_status = APPROVAL_STATUSES["approved"]
	_set_if_has(doc, "current_approval_level", LEAVE_LEVELS["completed"])
	doc.status = "Approved"
	try:
		if doc.docstatus == 0:
			doc.submit()
		else:
			doc.save(ignore_permissions=True)
	except Exception:
		doc.save(ignore_permissions=True)
	_notify_final_approval(doc, employee_doc, approver_name)
	frappe.db.commit()
	return _ok({"approval_status": APPROVAL_STATUSES["approved"]}, "Leave approved (final).")


@frappe.whitelist()
def reject_leave(name=None, remarks=None):
	"""Reject at the current stage. Records rejecting user/level/date + reason, emails
	the applicant with the reason. Authorised like approve_leave."""
	_require_login()
	if not remarks:
		return _fail("Reject Reason required.", "reject_reason_required")
	doc = frappe.get_doc("Leave Application", name)
	status = _leave_approval_status(doc)
	if doc.docstatus == 2 or status in (APPROVAL_STATUSES["approved"], APPROVAL_STATUSES["cancelled"]) or str(doc.status) in ("Approved", "Cancelled", "Rejected"):
		return _fail("This leave is already finalised.", "already_finalised")
	employee_doc = frappe.get_doc("Employee", doc.employee)
	user = frappe.session.user
	actor = _leave_stage_actor(status, user, employee_doc)
	if not actor:
		return _fail("You cannot reject this leave at its current stage.", "permission_denied")
	level_label = actor[0]
	rejected_status = {
		"Department Head": APPROVAL_STATUSES["rejected_dept_head"],
		"MD": APPROVAL_STATUSES["rejected_md"],
		"HR": APPROVAL_STATUSES["rejected_hr"],
	}.get(level_label, APPROVAL_STATUSES["rejected"])
	doc.status = "Rejected"
	_set_if_has(doc, "jew_hrms_approval_status", rejected_status)
	_set_if_has(doc, "current_approval_level", LEAVE_LEVELS["rejected"])
	_set_if_has(doc, "reject_reason", remarks)
	_set_if_has(doc, "rejected_by", user)
	_set_if_has(doc, "rejection_level", level_label)
	_set_if_has(doc, "rejection_date", now_datetime())
	# Stock HRMS's own list view indicator shows "Draft" instead of the real status
	# whenever a submittable doc claims Approved/Rejected but is still docstatus=0 -
	# submit it (same as approve_leave's final branch) so Desk actually shows
	# "Rejected", not "Draft". Safe: HRMS's on_submit only deducts leave balance /
	# touches Attendance when status == "Approved", and explicitly supports
	# submitting a "Rejected" leave (its own on_submit only rejects Open/Cancelled).
	try:
		if doc.docstatus == 0:
			doc.submit()
		else:
			doc.save(ignore_permissions=True)
	except Exception:
		doc.save(ignore_permissions=True)
	_notify_rejection(doc, employee_doc, level_label, remarks)
	frappe.db.commit()
	return _ok(message="Leave rejected")


@frappe.whitelist()
def get_on_leave_today(on_date=None):
	"""Employees on approved leave for the given day (default today).
	Admin / HR / Owner see everyone; a department head sees only their own team.
	Used by the mobile Admin dashboard."""
	_require_login()
	is_admin_access = _can_access_admin()
	is_dept_head = _is_any_dept_head()
	if not (is_admin_access or is_dept_head):
		frappe.throw(_("Permission denied"), frappe.PermissionError)
	day = getdate(on_date or today())
	rows = frappe.get_all(
		"Leave Application",
		filters={"from_date": ["<=", day], "to_date": [">=", day], "docstatus": ["<", 2]},
		fields=["name", "employee", "employee_name", "leave_type", "from_date", "to_date", "half_day", "status"],
		order_by="from_date asc",
		limit_page_length=0,
		ignore_permissions=True,
	)
	result = []
	for r in rows:
		approved = str(r.get("status") or "").lower() == "approved"
		if not approved:
			approved = str(frappe.db.get_value("Leave Application", r.name, "jew_hrms_approval_status") or "").lower() == "approved"
		if not approved:
			continue
		if not is_admin_access and not _can_manage_employee(r.employee):
			continue
		result.append({
			"employee": r.employee,
			"employee_name": r.employee_name or r.employee,
			"department": frappe.db.get_value("Employee", r.employee, "department"),
			"leave_type": r.leave_type,
			"from_date": str(r.from_date),
			"to_date": str(r.to_date),
			"half_day": r.half_day,
		})
	return _ok({"on_leave": result, "count": len(result), "date": str(day)})


# HR categorises a whole leave as CL / PL / LWP in one click. Re-books the
# Leave Application under the chosen paid type (deducting that balance), or
# leaves it unpaid for LWP. Admin/HR/Owner only.
LEAVE_CATEGORY_MAP = {
	"CL": "Casual Leave", "Casual Leave": "Casual Leave",
	"PL": "Privilege Leave", "Privilege Leave": "Privilege Leave",
	"LWP": "Leave Without Pay", "Leave Without Pay": "Leave Without Pay",
}


@frappe.whitelist()
def categorise_leave(leave=None, category=None):
	_require_admin()
	if not leave or not category:
		return _fail("Leave and category are required.", "validation_error")
	target = LEAVE_CATEGORY_MAP.get(str(category).strip())
	if not target or not frappe.db.exists("Leave Type", target):
		return _fail("Unknown or missing leave type for this category.", "validation_error")
	src = frappe.get_doc("Leave Application", leave)
	if src.leave_type == target:
		return _ok({"leave_application": src.name, "leave_type": target}, f"Already marked as {target}.")
	payload = {
		"employee": src.employee, "leave_type": target,
		"from_date": src.from_date, "to_date": src.to_date,
		"half_day": src.half_day, "half_day_date": src.get("half_day_date"),
		"description": src.description,
	}
	try:
		if src.docstatus == 1:
			src.cancel()
		elif src.docstatus == 0:
			frappe.delete_doc("Leave Application", src.name, ignore_permissions=True)
		new = frappe.new_doc("Leave Application")
		new.update({k: v for k, v in payload.items() if v not in (None, "")})
		new.status = "Approved"
		_set_if_has(new, "jew_hrms_approval_status", APPROVAL_STATUSES["approved"])
		new.insert(ignore_permissions=True)
		new.submit()
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		return _fail(f"Could not mark as {target}. The employee may not have enough {target} balance.", "categorise_failed")
	return _ok({"leave_application": new.name, "leave_type": target}, f"Leave marked as {target}.")


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
		fields=["name", "shift_name", "shift_start_time", "shift_end_time", "full_day_minimum_hours", "half_day_minimum_hours", "late_coming_grace_minutes", "early_going_grace_minutes", "attendance_login_grace_minute", "attendance_logout_grace_minute", "is_active"],
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
		"attendance_login_grace_minute",
		"attendance_logout_grace_minute",
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
	# JEW Attendance Regularization has no employee_name field of its own (unlike Leave
	# Application) - look it up so HR sees who a request is for, not just the raw ID.
	names = {r["employee"] for r in rows if r.get("employee")}
	name_map = {}
	if names:
		name_map = {
			e.name: e.employee_name
			for e in frappe.get_all("Employee", filters={"name": ["in", list(names)]}, fields=["name", "employee_name"])
		}
	for r in rows:
		r["employee_name"] = name_map.get(r.get("employee")) or r.get("employee")
	return _ok({"regularizations": rows})


@frappe.whitelist()
def get_regularization_detail(name=None):
	"""Single-record fetch for deep-linking straight from the HR notification email
	to the ONE request it's about, instead of landing on the full pending queue."""
	_require_approver()
	if not name or not frappe.db.exists("JEW Attendance Regularization", name):
		return _fail("Regularization request not found.", "not_found")
	doc = frappe.get_doc("JEW Attendance Regularization", name)
	row = {
		"name": doc.name, "employee": doc.employee, "attendance_date": doc.attendance_date,
		"issue_type": doc.issue_type, "in_time": doc.in_time, "out_time": doc.out_time,
		"working_hours": doc.working_hours, "policy_action": doc.policy_action,
		"status": doc.status, "remarks": doc.remarks,
	}
	row["employee_name"] = frappe.db.get_value("Employee", doc.employee, "employee_name") or doc.employee
	return _ok({"regularization": row})


REGULARIZATION_ATTENDANCE_OUTCOME = {
	# action -> (Attendance.status, late_entry, early_exit). "Rejected" and "Marked LWP"
	# both land on Absent - the excuse wasn't accepted (or was, but unpaid), so the
	# missing check-in/out stands as an unpaid absence either way. "Checked In" maps
	# to None - it only fabricates the missing punch, it never touches Attendance;
	# the employee's own real Check-Out later runs the same policy evaluation as any
	# normal day and finalizes Attendance then, same as everyone else.
	"Checked In": (None, False, False),
	"Approved as Check-Out": ("Present", False, False),
	"Approved as Present": ("Present", False, False),
	"Marked Half Day": ("Half Day", False, False),
	"Marked LWP": ("Absent", False, False),
	"Marked Late Coming": ("Present", True, False),
	"Marked Early Going": ("Present", False, True),
	"Rejected": ("Absent", False, False),
}


def _sync_attendance_for_regularization_decision(doc, status, late_entry=False, early_exit=False):
	"""Make the day's real Attendance record match what HR just decided on this
	regularization request - deciding the REQUEST alone doesn't fix the actual
	(payroll-relevant) attendance unless this also runs. Creates the Attendance if
	none exists yet (the usual case for a self-raised request); updates it in place
	if one already exists (e.g. the auto-detection engine's optimistic Present mark).
	Best-effort - never raises, so it can never block the decision itself."""
	try:
		attendance_date = getdate(doc.attendance_date)
		meta = frappe.get_meta("Attendance")
		existing = frappe.db.get_value(
			"Attendance", {"employee": doc.employee, "attendance_date": attendance_date, "docstatus": ["<", 2]}, "name"
		)
		if existing:
			updates = {"status": status}
			if meta.has_field("late_entry"):
				updates["late_entry"] = 1 if late_entry else 0
			if meta.has_field("early_exit"):
				updates["early_exit"] = 1 if early_exit else 0
			frappe.db.set_value("Attendance", existing, updates, update_modified=False)
			return existing
		emp = frappe.db.get_value("Employee", doc.employee, ["company", "status"], as_dict=True) or frappe._dict()
		if emp.get("status") and emp.status != "Active":
			return None
		att = frappe.new_doc("Attendance")
		att.employee = doc.employee
		if emp.get("company"):
			att.company = emp.company
		att.attendance_date = attendance_date
		att.status = status
		if late_entry and meta.has_field("late_entry"):
			att.late_entry = 1
		if early_exit and meta.has_field("early_exit"):
			att.early_exit = 1
		att.insert(ignore_permissions=True)
		att.submit()
		return att.name
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS regularization decision -> attendance sync failed")
		return None


@frappe.whitelist()
def _has_real_checkin(employee, attendance_date, log_type):
	return bool(frappe.db.exists("Employee Checkin", {
		"employee": employee,
		"time": ["between", [f"{attendance_date} 00:00:00", f"{attendance_date} 23:59:59"]],
		"log_type": log_type,
	}))


def _create_missing_checkin(employee, when, log_type):
	checkin = frappe.new_doc("Employee Checkin")
	checkin.employee = employee
	checkin.log_type = log_type
	checkin.time = get_datetime(when)
	checkin.insert(ignore_permissions=True)
	return checkin.name


# Actions that mean HR actually accepted the employee was AT WORK (as opposed to
# Rejected, or Marked LWP - an unpaid absence) - only these should ever fabricate a
# real Employee Checkin punch for a day the employee genuinely missed one.
PRESENCE_ACCEPTING_ACTIONS = {"Checked In", "Approved as Check-Out", "Approved as Present", "Marked Half Day", "Marked Late Coming", "Marked Early Going"}


@frappe.whitelist()
def decide_regularization(name=None, action=None, remarks=None, manual_in_time=None, manual_out_time=None):
	_require_admin()
	if not name or not action:
		return _fail("Regularization and action are required.", "validation_error")
	if action == "Marked LWP" and not _is_owner():
		return _fail("You do not have permission.", "permission_denied")
	if action not in REGULARIZATION_ATTENDANCE_OUTCOME:
		return _fail("Invalid regularization action.", "validation_error")
	doc = frappe.get_doc("JEW Attendance Regularization", name)
	if action == "Rejected" and not remarks:
		return _fail("Reject Reason required.", "reject_reason_required")

	# Deciding "Present" (or Half Day/Late/Early) for a Missing Mark In/Out issue
	# must actually create the real Employee Checkin punch that was missing - not
	# just flip the summary Attendance status to Present with no underlying
	# check-in/check-out event on record. Only do this once (skip if a real punch
	# already exists for that side, e.g. a "Missing Mark Out" case already has its
	# real IN checkin from earlier in the day).
	if action in PRESENCE_ACCEPTING_ACTIONS:
		needs_in = doc.issue_type in ("Missing Mark In", "Missing Mark In & Out", "Missing Mark In (Half Day)") and not _has_real_checkin(doc.employee, doc.attendance_date, "IN")
		needs_out = doc.issue_type in ("Missing Mark Out", "Missing Mark In & Out", "Missing Mark Out (Half Day)") and not _has_real_checkin(doc.employee, doc.attendance_date, "OUT")

		# For the dedicated self-service "Missing Mark ..." issue types, auto-derive
		# the missing punch's time from the employee's own shift policy instead of
		# requiring HR to type an exact time - there's nothing to verify here (the day
		# is being accepted as worked), so the shift's own official start/end time is
		# the correct, unambiguous value. manual_in_time/manual_out_time, if the
		# caller DID pass them, still take priority over this auto-fill.
		if (needs_in and not manual_in_time) or (needs_out and not manual_out_time):
			checkin_shift = frappe.db.get_value("Employee Checkin", {
				"employee": doc.employee,
				"time": ["between", [f"{doc.attendance_date} 00:00:00", f"{doc.attendance_date} 23:59:59"]],
			}, "shift")
			shift_policy = _get_active_policy(employee=doc.employee, shift=checkin_shift)
			if needs_in and not manual_in_time and shift_policy:
				manual_in_time = f"{doc.attendance_date} {frappe.utils.get_time(shift_policy.shift_start_time)}"
			if needs_out and not manual_out_time and shift_policy:
				manual_out_time = f"{doc.attendance_date} {frappe.utils.get_time(shift_policy.shift_end_time)}"

		if needs_in and not manual_in_time:
			return _fail("Please provide the actual Check-In time.", "manual_in_time_required")
		if needs_out and not manual_out_time:
			return _fail("Please provide the actual Check-Out time.", "manual_out_time_required")
		if needs_in:
			_create_missing_checkin(doc.employee, manual_in_time, "IN")
		if needs_out:
			_create_missing_checkin(doc.employee, manual_out_time, "OUT")

	if manual_in_time:
		doc.in_time = get_datetime(manual_in_time)
	if manual_out_time:
		doc.out_time = get_datetime(manual_out_time)
	doc.status = action
	doc.approved_by = frappe.session.user
	doc.remarks = remarks
	doc.save(ignore_permissions=True)
	att_status, late_entry, early_exit = REGULARIZATION_ATTENDANCE_OUTCOME[action]
	if att_status:
		attendance_name = _sync_attendance_for_regularization_decision(doc, att_status, late_entry, early_exit)
		if attendance_name and not doc.linked_attendance:
			frappe.db.set_value("JEW Attendance Regularization", doc.name, "linked_attendance", attendance_name, update_modified=False)
	_safe_insert_notification(doc.employee, "Regularization updated", f"{doc.issue_type}: {action}", "Info", "JEW Attendance Regularization", doc.name)
	frappe.db.commit()
	return _ok({"regularization": doc.name, "status": doc.status}, "Regularization updated")


def _regularization_request_email_context(doc, employee_doc):
	site = frappe.utils.get_url() + "/jew-hrms/m"
	return {
		"employee_name": employee_doc.employee_name or employee_doc.name,
		"employee_id": employee_doc.name,
		"department": employee_doc.get("department") or "-",
		"issue_type": doc.issue_type,
		"attendance_date": _fmt_dmy(doc.attendance_date),
		"reason": doc.remarks or "",
		"request_id": doc.name,
		"company_name": employee_doc.get("company") or "Jain Engineering Works (India) Private Limited",
		"app_link": "{0}?view=regularization&name={1}".format(site, doc.name),
	}


def _send_regularization_request_email(doc, employee_doc):
	"""Email HR that a self-service Attendance Regularization request is pending
	their review. Best-effort — never breaks the request call."""
	recipients = _hr_emails()
	if not recipients:
		return
	ctx = _regularization_request_email_context(doc, employee_doc)
	_send_template_email(recipients, "Attendance Regularization - HR Request", ctx, doc)
	msg = "{0}: {1} on {2} needs your review.".format(
		employee_doc.employee_name or employee_doc.name, doc.issue_type, _fmt_dmy(doc.attendance_date))
	_desk_bell_notify(recipients, doc, msg)


@frappe.whitelist()
def request_attendance_regularization(attendance_date=None, issue_type=None, reason=None):
	"""Employee self-service: raise a regularization request for a day they missed
	check-in / check-out. Lands in the same HR 'Regularization' queue as system-flagged
	issues (get_regularizations / decide_regularization). Validation (issue type,
	reason required, no future dates, one active request per day), forcing the
	initial Pending status, and the HR email are all handled by the doctype's own
	before_insert/after_insert (see jew_attendance_regularization.py) — the SAME path
	Desk's native 'New JEW Attendance Regularization' form goes through, so a request
	raised from Desk behaves identically to one raised from the app."""
	employee = _get_current_employee()
	if not attendance_date or not issue_type:
		return _fail("Attendance date and issue type are required.", "validation_error")
	doc = frappe.new_doc("JEW Attendance Regularization")
	doc.employee = employee.name
	doc.attendance_date = attendance_date
	doc.issue_type = issue_type
	doc.remarks = reason
	try:
		doc.insert(ignore_permissions=True)
	except frappe.ValidationError as exc:
		return _fail(str(exc), "validation_error")
	frappe.db.commit()
	return _ok({"name": doc.name}, "Regularization request submitted")


@frappe.whitelist()
def get_my_regularizations():
	"""Only requests the employee actually raised themselves - "Late Arrival" /
	plain "Late Coming" / plain "Early Going" (and the other purely-automatic
	issue types) are the system's own silent policy checks, never something an
	employee applied for, so they don't belong on this screen even though they
	live in the same doctype. See SELF_SERVICE_ISSUE_TYPES for the exact list of
	issue types an employee can actually self-raise.

	"Missing Mark Out" needs a second check on top of that: it's the one
	self-raisable type process_missing_attendance's own IN-only safety net ALSO
	auto-creates on its own (as "Administrator", since that's a scheduled job) -
	excluding requested_by == "Administrator" catches that case. This is more
	reliable than matching the auto-creation's remarks text, because HR deciding
	the request overwrites remarks with their own note (often blank with the
	single-click Approve flow) but never touches requested_by."""
	from jew_hrms_mobile.jew_hrms_mobile.doctype.jew_attendance_regularization.jew_attendance_regularization import SELF_SERVICE_ISSUE_TYPES
	employee = _get_current_employee()
	rows = frappe.get_list(
		"JEW Attendance Regularization",
		filters={
			"employee": employee.name,
			"issue_type": ["in", list(SELF_SERVICE_ISSUE_TYPES)],
			"requested_by": ["!=", "Administrator"],
		},
		fields=["name", "attendance_date", "issue_type", "in_time", "out_time", "policy_action", "status", "remarks"],
		order_by="attendance_date desc, modified desc",
		limit_page_length=100,
		ignore_permissions=True,
	)
	return _ok({"regularizations": rows})


# ---------------------------------------------------------------------------
# Monthly Salary Slip auto-generation. Reuses the "Attendance Based Salary
# Sheet" report's own, already-validated-against-real-data functions
# (compute_period_days / _simulate_gross_components / holiday & SSA lookups)
# instead of re-deriving the Present/Absent/CL/PL/LWP and gross-vs-earned
# formula a second time here - the report and this generator must never be
# able to silently disagree with each other about what a given employee's pay
# should be for a given month.
# ---------------------------------------------------------------------------

def _abs_report_module():
	from jew_hrms_mobile.jew_hrms_mobile.report.attendance_based_salary_sheet import attendance_based_salary_sheet
	return attendance_based_salary_sheet


def _default_salary_slip_period():
	"""The month that just ended - i.e. assumes "today" is its last day, which is
	true whenever this runs from the scheduled month-end job. Also used as the
	default for the manual preview/generate calls when no explicit dates are
	passed."""
	last_day = get_last_day(today())
	first_day = get_first_day(today())
	return first_day, last_day


def _plan_monthly_salary_slips(from_date, to_date):
	"""Per-employee plan for (from_date, to_date): either the day-counts a new
	Salary Slip would be built from, or a skip_reason explaining why none will
	be created for them. Purely read-only - makes no database changes, safe to
	call any time as a dry run."""
	abs_report = _abs_report_module()
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "company", "holiday_list"],
	)
	plan = []
	emp_ids = [e.name for e in employees]
	if not emp_ids:
		return plan

	att_by_emp = abs_report._get_attendance_summary(from_date, to_date, emp_ids)
	leave_by_emp = abs_report._get_leave_summary(from_date, to_date, emp_ids)
	ssa_emps = abs_report._get_ssa_employees(to_date, emp_ids)
	total_days = date_diff(to_date, from_date) + 1
	holiday_cache = {}

	for emp in employees:
		existing = frappe.db.exists("Salary Slip", {
			"employee": emp.name, "start_date": from_date, "end_date": to_date, "docstatus": ["!=", 2],
		})
		if existing:
			plan.append({
				"employee": emp.name, "employee_name": emp.employee_name, "company": emp.company,
				"skip_reason": "Salary Slip {0} already exists for this period".format(existing),
			})
			continue
		if emp.name not in ssa_emps:
			plan.append({
				"employee": emp.name, "employee_name": emp.employee_name, "company": emp.company,
				"skip_reason": "No active Salary Structure Assignment",
			})
			continue
		if not att_by_emp.get(emp.name) and not leave_by_emp.get(emp.name):
			plan.append({
				"employee": emp.name, "employee_name": emp.employee_name, "company": emp.company,
				"skip_reason": "No Attendance or Leave data found in this app for this period - likely tracked elsewhere; create this Salary Slip manually",
			})
			continue
		emp_holidays = abs_report._get_employee_holidays_in_range(
			emp.name, emp.company, emp.holiday_list, from_date, to_date, holiday_cache
		)
		days = abs_report.compute_period_days(
			emp_holidays, att_by_emp.get(emp.name, []), leave_by_emp.get(emp.name, []),
			from_date, to_date, total_days,
		)
		# birthday half-day policy (no-op unless the employee has a birthday credit to use)
		from jew_hrms_mobile.birthday_policy import apply_birthday_credit
		days = apply_birthday_credit(days, emp.name, from_date, to_date)
		# Real Attendance/Leave rows exist for this employee, but every single one
		# came back Absent/nothing-paid - possible (resigned-but-still-Active,
		# unrecorded long absence), but unusual enough that a zero-pay Salary Slip
		# should never go out the door unattended. Same "ask HR first" principle as
		# the no-data guard above, just for this narrower case.
		if days["days_paid"] == 0:
			plan.append({
				"employee": emp.name, "employee_name": emp.employee_name, "company": emp.company,
				"skip_reason": "Fully absent for the whole period (0 paid days) - please verify and create manually",
			})
			continue
		plan.append({
			"employee": emp.name, "employee_name": emp.employee_name, "company": emp.company,
			"skip_reason": None, **days,
		})
	return plan


def _create_monthly_salary_slip(abs_report, emp_row, from_date, to_date, days):
	"""Create + submit one real Salary Slip for (employee, from_date..to_date).
	ERPNext's own Salary Slip controller recalculates gross/earned/deduction
	amounts from REAL attendance during insert()/submit() - using its own
	payment-days proration, which (confirmed against this company's real
	historical data - see _simulate_gross_components) does not match how pay
	is actually meant to work here (e.g. it would zero out fixed deductions
	like Professional Tax at low payment-days, and it would prorate Tiffin
	Allowance which should always be paid in full). So: let it insert/submit
	normally first (that's what attaches the correct Salary Structure
	components as real rows), THEN raw-overwrite the resulting amounts and the
	present/absent/leave display fields to the report's own validated formula -
	exactly the same two-step pattern leave_details._sync_salary_slip already
	uses for the display fields alone, just extended to the pay amounts too.
	No GL Entries exist for Salary Slip on this site (verified), so patching
	after submit creates no accounting mismatch. Returns (slip_name, error)."""
	sim = abs_report._simulate_gross_components(emp_row.name, emp_row.company, from_date, to_date)
	if sim is None:
		return None, "No active Salary Structure found for this period"

	slip = frappe.new_doc("Salary Slip")
	slip.employee = emp_row.name
	slip.company = emp_row.company
	slip.posting_date = to_date
	slip.start_date = from_date
	slip.end_date = to_date
	slip.insert(ignore_permissions=True)
	slip.submit()

	working_days = flt(days["working_days"]) or (date_diff(to_date, from_date) + 1)
	days_paid = flt(days["days_paid"])
	cl_pl_days = flt(days["cl_taken"]) + flt(days["pl_taken"])

	detail_rows = frappe.get_all(
		"Salary Detail", filters={"parent": slip.name, "parentfield": "earnings"},
		fields=["name", "salary_component"],
	)
	gross_pay = 0.0
	for d in detail_rows:
		gross_amt = flt(sim["earnings"].get(d.salary_component, 0))
		if d.salary_component == "Tiffin Allowance":
			earned = gross_amt
		else:
			earned = flt(gross_amt * days_paid / working_days) if working_days else 0.0
		frappe.db.set_value("Salary Detail", d.name, "amount", earned, update_modified=False)
		gross_pay += earned

	deduction_rows = frappe.get_all(
		"Salary Detail", filters={"parent": slip.name, "parentfield": "deductions"},
		fields=["name", "salary_component"],
	)
	total_deduction = 0.0
	for d in deduction_rows:
		amt = flt(sim["deductions"].get(d.salary_component, 0))
		frappe.db.set_value("Salary Detail", d.name, "amount", amt, update_modified=False)
		total_deduction += amt

	net_pay = gross_pay - total_deduction
	frappe.db.set_value("Salary Slip", slip.name, {
		"present_days": flt(days["present"]),
		"absent_days": flt(days["absent"]),
		"cl_pl_days": cl_pl_days,
		"leave_without_pay": flt(days["lwp_days"]),
		"total_working_days": working_days,
		"payment_days": days_paid,
		"gross_pay": gross_pay,
		"total_deduction": total_deduction,
		"net_pay": net_pay,
		"rounded_total": round(net_pay),
	}, update_modified=False)
	return slip.name, None


@frappe.whitelist()
def preview_monthly_salary_slip_generation(from_date=None, to_date=None):
	"""Dry run for generate_monthly_salary_slips() - read-only, makes no
	database changes. Lets HR sanity-check the plan (who gets a slip, who is
	skipped and why, what their day-counts would be) before the real,
	submitting run happens, whether that's this month's scheduled run or a
	one-off manual call for a past period."""
	_require_admin()
	from_date = getdate(from_date) if from_date else None
	to_date = getdate(to_date) if to_date else None
	if not from_date or not to_date:
		from_date, to_date = _default_salary_slip_period()
	plan = _plan_monthly_salary_slips(from_date, to_date)
	return _ok({"from_date": str(from_date), "to_date": str(to_date), "plan": plan})


@frappe.whitelist()
def generate_monthly_salary_slips(from_date=None, to_date=None):
	"""Create + submit a Salary Slip for every Active employee (any company)
	with an active Salary Structure Assignment covering the period and no
	Salary Slip already on file for it - the real, writing counterpart to
	preview_monthly_salary_slip_generation(). Safe to re-run: employees who
	already have a slip for the period are skipped, never duplicated. Called
	both by HR (manually, from the admin panel) and by the month-end scheduled
	job (see hooks.py's "cron" entry)."""
	_require_admin()
	from_date = getdate(from_date) if from_date else None
	to_date = getdate(to_date) if to_date else None
	if not from_date or not to_date:
		from_date, to_date = _default_salary_slip_period()

	abs_report = _abs_report_module()
	plan = _plan_monthly_salary_slips(from_date, to_date)
	created, skipped, failed = [], [], []
	for row in plan:
		if row["skip_reason"]:
			skipped.append({"employee": row["employee"], "employee_name": row["employee_name"], "reason": row["skip_reason"]})
			continue
		emp_row = frappe._dict({"name": row["employee"], "company": row["company"]})
		try:
			slip_name, error = _create_monthly_salary_slip(abs_report, emp_row, from_date, to_date, row)
		except Exception:
			frappe.log_error(title="Monthly Salary Slip auto-generation failed", message=frappe.get_traceback())
			error = "Unexpected error - see Error Log"
			slip_name = None
		if slip_name:
			created.append({"employee": row["employee"], "employee_name": row["employee_name"], "salary_slip": slip_name})
		else:
			failed.append({"employee": row["employee"], "employee_name": row["employee_name"], "reason": error})
	frappe.db.commit()
	summary = {"from_date": str(from_date), "to_date": str(to_date), "created": created, "skipped": skipped, "failed": failed}
	_attendance_logger().info(json.dumps({"stage": "monthly_salary_slip_generation", **summary}, default=str))
	_email_monthly_salary_slip_summary(from_date, to_date, created, skipped, failed)
	return _ok(summary, "Generated {0} Salary Slip(s), skipped {1}, failed {2}.".format(len(created), len(skipped), len(failed)))


def _email_monthly_salary_slip_summary(from_date, to_date, created, skipped, failed):
	"""Best-effort plain summary email to HR after every generation run (scheduled
	or manual) - the two skip categories HR actually needs to act on (no SSA, no
	Attendance/Leave data in this app) are broken out by name, not just counted,
	since those are exactly the employees who now need a Salary Slip made by
	hand this month."""
	recipients = _hr_emails()
	if not recipients:
		return
	no_data = [s for s in skipped if "No Attendance or Leave data" in s["reason"]]
	no_ssa = [s for s in skipped if "No active Salary Structure" in s["reason"]]
	fully_absent = [s for s in skipped if "Fully absent for the whole period" in s["reason"]]
	already_existed = [s for s in skipped if s not in no_data and s not in no_ssa and s not in fully_absent]
	lines = [
		"Monthly Salary Slip auto-generation for {0} to {1}:".format(from_date, to_date),
		"",
		"Created: {0}".format(len(created)),
		"Already existed (skipped): {0}".format(len(already_existed)),
		"No Salary Structure Assignment (skipped): {0}".format(len(no_ssa)),
		"No Attendance/Leave data in this app - please create these manually ({0}):".format(len(no_data)),
	]
	for s in no_data:
		lines.append("  - {0} ({1})".format(s["employee_name"] or s["employee"], s["employee"]))
	if no_ssa:
		lines.append("No Salary Structure Assignment - please set one up ({0}):".format(len(no_ssa)))
		for s in no_ssa:
			lines.append("  - {0} ({1})".format(s["employee_name"] or s["employee"], s["employee"]))
	if fully_absent:
		lines.append("Fully absent the whole period (0 paid days) - please verify ({0}):".format(len(fully_absent)))
		for s in fully_absent:
			lines.append("  - {0} ({1})".format(s["employee_name"] or s["employee"], s["employee"]))
	if failed:
		lines.append("Failed with an error ({0}):".format(len(failed)))
		for f in failed:
			lines.append("  - {0} ({1}): {2}".format(f["employee_name"] or f["employee"], f["employee"], f["reason"]))
	message = "<br>".join(frappe.utils.escape_html(line) for line in lines)
	try:
		frappe.sendmail(recipients=recipients, subject="Monthly Salary Slip generation - {0} to {1}".format(from_date, to_date),
		                message=message, sender=_hrms_sender_email())
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS monthly salary slip summary email failed")


def run_month_end_salary_slip_generation():
	"""Scheduled entry point (see hooks.py) - fires daily at night, but only
	actually generates anything on the LAST day of the month (i.e. when
	tomorrow rolls into a new month), since Frappe's scheduler has no native
	"last day of month" cron trigger."""
	if getdate(add_days(today(), 1)).day != 1:
		return
	from_date, to_date = _default_salary_slip_period()
	generate_monthly_salary_slips(from_date=from_date, to_date=to_date)


def send_work_anniversary_hr_notice():
	"""Daily. Replaces HRMS's own Work Anniversary Reminder (switched off in HR
	Settings), which emails EVERY employee of the company - this tells HR only
	who completed a work anniversary today. Scoped to the JEWPL site because
	scheduler hooks run on every site that has this app installed."""
	if frappe.local.site != "jewipl.duxdigitech.in":
		return
	recipients = _hr_emails()
	if not recipients:
		return
	from hrms.controllers.employee_reminders import get_employees_having_an_event_today
	by_company = get_employees_having_an_event_today("work_anniversary")
	rows = []
	for company, persons in (by_company or {}).items():
		for p in persons:
			years = getdate().year - p["date_of_joining"].year
			rows.append("<li><strong>{0}</strong> - {1} ({2} year{3})</li>".format(
				frappe.utils.escape_html(p["name"]), frappe.utils.escape_html(company or ""), years, "" if years == 1 else "s"))
	if not rows:
		return
	message = "<p>Work anniversary today:</p><ul>{0}</ul>".format("".join(rows))
	try:
		frappe.sendmail(recipients=recipients, subject="Work Anniversary Reminder (HR)", message=message, sender=_hrms_sender_email())
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS work anniversary HR notice failed")


# ---------------------------------------------------------------------------
# Statutory deductions (Professional Tax / PF / TDS) set on the Employee record
# itself (see the "Statutory Deductions" section added to the Employee form) -
# kept in sync with the employee's Salary Structure automatically, so HR never
# has to separately go edit the Salary Structure after checking a box here.
# ---------------------------------------------------------------------------

PROFESSIONAL_TAX_FORMULA = "0 if gross_pay <= 18750 else (125 if gross_pay <= 25000 else (167 if gross_pay <= 33333 else (212 if getdate(posting_date).month == 3 else 208)))"


def _employee_current_salary_structure(employee):
	"""(gross_salary, salary_structure_name) from the employee's latest active
	Salary Structure Assignment, or (0, None) if they don't have one yet."""
	ssa = frappe.db.get_value(
		"Salary Structure Assignment", {"employee": employee, "docstatus": 1},
		["salary_structure"], order_by="from_date desc",
	)
	if not ssa:
		return 0.0, None
	earnings = frappe.get_all("Salary Detail", filters={"parent": ssa, "parentfield": "earnings"}, fields=["amount"])
	gross = sum(flt(e.amount) for e in earnings)
	return gross, ssa


def _evaluate_professional_tax(gross_pay):
	from frappe.utils import getdate
	return eval(PROFESSIONAL_TAX_FORMULA, {"getdate": getdate, "gross_pay": flt(gross_pay), "posting_date": today()})


def _sync_salary_structure_deduction(structure_name, component, applicable, amount=None, formula=None):
	"""Add/update/remove ONE deduction row on a (possibly already-submitted)
	Salary Structure to match applicable/amount/formula - the same
	allow_on_submit-based update path already used for the Professional Tax
	slab rollout, generalized to any component."""
	existing_name = frappe.db.get_value(
		"Salary Detail", {"parent": structure_name, "parentfield": "deductions", "salary_component": component}, "name"
	)
	if not applicable:
		if existing_name:
			frappe.delete_doc("Salary Detail", existing_name, ignore_permissions=True, force=True)
		return
	doc = frappe.get_doc("Salary Structure", structure_name)
	row = next((d for d in doc.deductions if d.salary_component == component), None)
	if not row:
		row = doc.append("deductions", {"salary_component": component})
	if formula:
		row.amount_based_on_formula = 1
		row.formula = formula
		row.amount = 0
	else:
		row.amount_based_on_formula = 0
		row.formula = None
		row.amount = flt(amount)
	doc.save(ignore_permissions=True)


def sync_employee_statutory_deductions(doc, method=None):
	"""Employee on_update hook (see hooks.py) - and also called directly from
	the Salary Structure Assignment on_submit hook, for a brand new employee who
	had these boxes checked before any Salary Structure existed yet to sync
	into. Best-effort: a failure here must never block saving the Employee."""
	try:
		gross, structure = _employee_current_salary_structure(doc.name)
		if not structure:
			# No Salary Structure Assignment yet (a brand new employee, most likely) -
			# fall back to their own CTC field so the estimate is still useful right
			# when HR is creating the record, not just after payroll setup later.
			gross = flt(doc.get("ctc"))
		ptax_amount = _evaluate_professional_tax(gross) if doc.get("professional_tax_applicable") else 0
		frappe.db.set_value("Employee", doc.name, "professional_tax_amount", ptax_amount, update_modified=False)

		if not structure:
			return
		_sync_salary_structure_deduction(structure, "Professional Tax", bool(doc.get("professional_tax_applicable")), formula=PROFESSIONAL_TAX_FORMULA)
		_sync_salary_structure_deduction(structure, "Provident Fund", bool(doc.get("pf_applicable")), amount=doc.get("pf_amount"))
		_sync_salary_structure_deduction(structure, "TDS", bool(doc.get("tds_applicable")), amount=doc.get("tds_amount"))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS: sync_employee_statutory_deductions failed")


def sync_statutory_deductions_on_ssa_submit(doc, method=None):
	"""Salary Structure Assignment on_submit hook - a brand new employee may
	have had Professional Tax/PF/TDS checked on their Employee record before
	any Salary Structure existed to sync those into; this catches that case the
	moment their first Salary Structure Assignment is submitted."""
	try:
		employee_doc = frappe.get_doc("Employee", doc.employee)
		sync_employee_statutory_deductions(employee_doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS: sync_statutory_deductions_on_ssa_submit failed")


# ---------------------------------------------------------------------------
# On Duty (OD) - self-service on top of ERPNext/HRMS's own standard "Attendance
# Request" doctype (reason="On Duty"), which already knows how to turn an
# approved date range into real Attendance records on submit - no attendance-
# marking logic to reinvent here, only the Pending -> HR decision -> Submit/stay-
# Draft workflow layered on top (see jew_od_status / jew_od_remarks, the two
# custom fields added for this).
# ---------------------------------------------------------------------------

@frappe.whitelist()
def request_on_duty(from_date=None, to_date=None, explanation=None, full_day=1, half_day_date=None, from_time=None, to_time=None, half_day_part=None):
	"""Employee self-service: raise an On Duty request for a date range. Lands as
	a Draft "Attendance Request" (reason=On Duty, jew_od_status=Pending) - nothing
	happens to Attendance until HR approves (decide_on_duty), which submits it and
	lets HRMS's own on_submit create the day(s)' Attendance: every day in the
	range becomes Present, EXCEPT half_day_date (when full_day is unchecked),
	which becomes Half Day instead - see hrms's own Attendance Request logic.
	`time` has no native field on this doctype (On Duty has no time-of-day
	concept in HRMS - only whole-day or one Half Day date) - folded into the
	explanation text purely so HR sees it."""
	employee = _get_current_employee()
	if not from_date or not to_date:
		return _fail("From date and to date are required.", "validation_error")
	from_dt, to_dt = getdate(from_date), getdate(to_date)
	if from_dt > to_dt:
		return _fail("From date cannot be after To date.", "validation_error")
	is_full_day = cint(full_day)
	half_day_dt = None
	half_part = ""
	if not is_full_day:
		if not half_day_date:
			return _fail("Half Day date is required when Full Day is unchecked.", "validation_error")
		half_day_dt = getdate(half_day_date)
		if not (from_dt <= half_day_dt <= to_dt):
			return _fail("Half Day date must be within the From/To range.", "validation_error")
		half_part = {"first half": "First Half", "second half": "Second Half"}.get(str(half_day_part or "First Half").strip().lower(), "")
		if not half_part:
			return _fail("Choose which half is On Duty: First Half or Second Half.", "validation_error")
	if frappe.db.exists("Attendance Request", {
		"employee": employee.name, "docstatus": ["<", 2],
		"to_date": [">=", from_dt], "from_date": ["<=", to_dt],
	}):
		return _fail("You already have an On Duty / Work From Home request overlapping these dates.", "duplicate_request")
	doc = frappe.new_doc("Attendance Request")
	doc.employee = employee.name
	doc.company = employee.get("company")
	doc.from_date = from_dt
	doc.to_date = to_dt
	doc.reason = "On Duty"
	doc.half_day = 0 if is_full_day else 1
	if half_day_dt:
		doc.half_day_date = half_day_dt
		_set_if_has(doc, "custom_half_day_type", half_part)
	time_note = ""
	if from_time and to_time:
		time_note = " (Time: {0} to {1})".format(from_time, to_time)
	elif from_time or to_time:
		time_note = " (Time: {0})".format(from_time or to_time)
	half_note = "[{0} On Duty, other half in office] ".format(half_part) if half_part else ""
	doc.explanation = "{0}{1}{2}".format(half_note, explanation or "", time_note)
	if from_time and to_time and is_full_day:
		t1, t2 = _parse_od_clock(from_time), _parse_od_clock(to_time)
		if not (t1 and t2 and t1 < t2):
			return _fail("Pick a valid From time and To time (From must be earlier than To).", "validation_error")
	stage_status, _od_recipients = _od_initial_stage(employee)
	doc.jew_od_status = stage_status
	if half_day_dt and from_dt == to_dt and frappe.db.exists("Attendance", {"employee": employee.name, "attendance_date": half_day_dt, "docstatus": 1, "status": "Half Day"}):
		doc.flags.ignore_validate = True    # HRMS refuses a half-day request on a day already marked Half Day; the office half still has to be counted
	try:
		doc.insert(ignore_permissions=True)
	except frappe.ValidationError as exc:
		return _fail(str(exc), "validation_error")
	frappe.db.commit()
	_pending_with = "Department Head" if stage_status == "Pending Dept Head" else "approver"
	_safe_insert_notification(employee.name, "On Duty request submitted", f"On Duty from {_fmt_dmy(from_dt)} to {_fmt_dmy(to_dt)} pending {_pending_with} review.", "Info", "Attendance Request", doc.name)
	_send_on_duty_request_email(doc, employee)
	return _ok({"name": doc.name, "whatsapp": _safe_request_whatsapp("on_duty", doc, employee)}, "On Duty request submitted")


def _generate_on_duty_email_action_token(doc, user):
	"""Same single-use, expiring, stage-locked email-action token scheme as the
	Leave / Late-Early email actions (_generate_late_early_email_action_token),
	against Attendance Request's own jew_email_action_token* fields."""
	token = frappe.generate_hash(length=48)
	frappe.db.set_value("Attendance Request", doc.name, {
		"jew_email_action_token": token,
		"jew_email_action_token_user": user or "",
		"jew_email_action_token_stage": doc.jew_od_status,
		"jew_email_action_token_expiry": add_to_date(now_datetime(), days=EMAIL_ACTION_TOKEN_VALIDITY_DAYS),
	}, update_modified=False)
	return token


def _validate_on_duty_email_action_token(token, action):
	if not token:
		return None, "Invalid or missing action link."
	name = frappe.db.get_value("Attendance Request", {"jew_email_action_token": token}, "name")
	if not name:
		return None, "This link is no longer valid - it may have already been used or superseded."
	doc = frappe.get_doc("Attendance Request", name)
	expiry = doc.get("jew_email_action_token_expiry")
	if expiry and get_datetime(expiry) < now_datetime():
		return None, "This action link has expired."
	if action in ("approve", "reject"):
		if doc.jew_od_status not in ("Pending Dept Head", "Pending"):
			return None, "This request has already been decided."
		stage = doc.get("jew_email_action_token_stage")
		if stage and stage != doc.jew_od_status:
			return None, "This request has already been decided."
	return doc, None


# On Duty from these employees routes to ONE approver (sunil.surana) instead of the
# HR pool, per HR instruction. That approver already has admin/HR rights, so
# decide_on_duty permits their one-click email approval and attendance gets marked.
OD_SPECIAL_APPROVER_EMAIL = "sunil.surana@jewonline.in"
OD_SPECIAL_APPROVER_EMPLOYEES = {"HR-EMP-00732", "HR-EMP-00581", "HR-EMP-00130", "HR-EMP-00592", "HR-EMP-00620"}


def _od_final_approver_emails(employee_doc):
	"""Who gives the FINAL On Duty approval: sunil.surana for the special field
	staff above, otherwise the HR pool."""
	if employee_doc.name in OD_SPECIAL_APPROVER_EMPLOYEES:
		return [OD_SPECIAL_APPROVER_EMAIL]
	return _hr_emails()


def _od_initial_stage(employee_doc):
	"""(status, recipients) for a brand-new On Duty request. The Department Head
	approves first, then it goes to the final approver (HR, or sunil.surana for the
	special field staff). If the employee has no Department Head, or their Department
	Head IS the final approver, the Dept Head stage is skipped so the same person is
	never asked to approve the one request twice."""
	finals = _od_final_approver_emails(employee_doc) or []
	dh = _employee_dept_head_email(employee_doc)
	if dh and dh.lower() not in [e.lower() for e in finals]:
		return "Pending Dept Head", [dh]
	return "Pending", finals


def _od_stage_recipients(doc, employee_doc):
	"""Approver email(s) for the On Duty request's CURRENT stage."""
	if doc.jew_od_status == "Pending Dept Head":
		dh = _employee_dept_head_email(employee_doc)
		return [dh] if dh else []
	return _od_final_approver_emails(employee_doc)


def _send_on_duty_request_email(doc, employee_doc):
	"""Email HR that an On Duty request is pending, with one-click Approve/Reject/
	View token links (same scheme as the Leave/Late-Early email actions) - opens
	/on_duty_email_action, which posts to confirm_on_duty_email_action."""
	recipients = _od_stage_recipients(doc, employee_doc)
	if not recipients:
		return
	hr_user = recipients[0]
	token = _generate_on_duty_email_action_token(doc, hr_user)
	site = frappe.utils.get_url()
	approve_link = "{0}/on_duty_email_action?token={1}&action=approve".format(site, token)
	reject_link = "{0}/on_duty_email_action?token={1}&action=reject".format(site, token)
	view_link = "{0}/on_duty_email_action?token={1}&action=view".format(site, token)
	date_range = _fmt_dmy(doc.from_date) if doc.from_date == doc.to_date else "{0} to {1}".format(_fmt_dmy(doc.from_date), _fmt_dmy(doc.to_date))
	message = """
		<p><strong>{employee}</strong> has requested On Duty from {dates}.</p>
		<p><strong>Reason:</strong> {reason}</p>
		<p style="margin-top:20px;">
			<a href="{approve}" style="background:#1a7f45;color:#fff;padding:11px 22px;border-radius:6px;text-decoration:none;font-weight:600;margin-right:10px;">Approve</a>
			<a href="{reject}" style="background:#b3341f;color:#fff;padding:11px 22px;border-radius:6px;text-decoration:none;font-weight:600;">Reject</a>
		</p>
		<p style="color:#64707a;font-size:12.5px;margin-top:16px;"><a href="{view}">View details</a> - this link works once and expires after {days} days.</p>
	""".format(employee=employee_doc.employee_name or employee_doc.name, dates=date_range, reason=doc.explanation or "-",
	           approve=approve_link, reject=reject_link, view=view_link, days=EMAIL_ACTION_TOKEN_VALIDITY_DAYS)
	try:
		frappe.sendmail(recipients=recipients, subject="On Duty request - {0}".format(employee_doc.employee_name or employee_doc.name),
		                message=message, sender=_hrms_sender_email(), reference_doctype=doc.doctype, reference_name=doc.name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS on duty request email failed")
	_desk_bell_notify(recipients, doc, "{0}: On Duty requested from {1} - pending your review.".format(employee_doc.employee_name or employee_doc.name, date_range))


@frappe.whitelist(allow_guest=True)
def confirm_on_duty_email_action(token=None, action=None, remarks=None, csrf_token=None):
	"""POST target of the on_duty_email_action page (one-click email Approve/
	Reject) - same scheme as confirm_late_early_email_action. Runs as the
	token's intended approver, reusing decide_on_duty. Redirects to a result
	page."""
	from urllib.parse import urlencode
	def _redirect(**params):
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = "/on_duty_email_action?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})
	doc, error = _validate_on_duty_email_action_token(token, action)
	if not doc:
		return _redirect(done=1, ok=0, msg=error or "This link is not valid.")
	if action == "reject" and not (remarks and str(remarks).strip()):
		return _redirect(action="reject", token=token, missing_remarks=1)
	acting_user = doc.get("jew_email_action_token_user") or frappe.session.user
	orig_user = frappe.session.user
	try:
		frappe.set_user(acting_user)
		# Token already authenticated this stage's intended approver (who may be a
		# Department Head without admin rights), so use the internal decider rather
		# than the admin-only whitelisted decide_on_duty.
		if action == "approve":
			res = _decide_on_duty(name=doc.name, action="Approved", remarks=remarks, acting_user=acting_user)
		elif action == "reject":
			res = _decide_on_duty(name=doc.name, action="Rejected", remarks=remarks, acting_user=acting_user)
		else:
			res = {"ok": False, "message": "Unknown action."}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS confirm_on_duty_email_action failed")
		res = {"ok": False, "message": "Could not complete this action."}
	finally:
		frappe.set_user(orig_user)
	ok = bool(res.get("ok"))
	return _redirect(done=1, ok=1 if ok else 0, msg=res.get("message") or ("Done." if ok else "Could not complete this action."))


@frappe.whitelist()
def get_my_on_duty():
	employee = _get_current_employee()
	rows = frappe.get_list(
		"Attendance Request", filters={"employee": employee.name},
		fields=["name", "from_date", "to_date", "explanation", "jew_od_status", "jew_od_remarks", "docstatus", "half_day", "half_day_date", "custom_half_day_type"],
		order_by="from_date desc, modified desc", limit_page_length=100, ignore_permissions=True,
	)
	return _ok({"requests": rows})


@frappe.whitelist()
def get_pending_on_duty():
	_require_admin()
	rows = frappe.get_list(
		"Attendance Request", filters={"jew_od_status": ["in", ["Pending Dept Head", "Pending"]], "docstatus": 0},
		fields=["name", "employee", "employee_name", "from_date", "to_date", "explanation", "half_day", "half_day_date", "custom_half_day_type"],
		order_by="from_date asc, modified desc", limit_page_length=100, ignore_permissions=True,
	)
	for r in rows:
		r["employee_name"] = r.get("employee_name") or frappe.db.get_value("Employee", r["employee"], "employee_name")
	return _ok({"requests": rows})


@frappe.whitelist()
def decide_on_duty(name=None, action=None, remarks=None):
	"""App pending-OD screen action (admin/HR). Email one-click actions by a stage
	approver go through _decide_on_duty directly (see confirm_on_duty_email_action)."""
	_require_admin()
	return _decide_on_duty(name=name, action=action, remarks=remarks, acting_user=frappe.session.user, via_admin=True)


def _decide_on_duty(name=None, action=None, remarks=None, acting_user=None, via_admin=False):
	"""Core On Duty decision for the TWO-stage flow: Department Head -> final
	approver (HR, or sunil.surana for the special field staff). Approving at the Dept
	Head stage forwards to the final approver (no attendance yet); approving at the
	final stage submits the Attendance Request so HRMS marks the day(s) Present/Half
	Day. Rejecting at either stage ends it. When via_admin is False (email one-click
	link) acting_user must be the current stage's approver."""
	if not name or not frappe.db.exists("Attendance Request", name):
		return _fail("On Duty request not found.", "not_found")
	if action not in ("Approved", "Rejected"):
		return _fail("A valid action is required.", "validation_error")
	if action == "Rejected" and not remarks:
		return _fail("Remarks are required to reject.", "validation_error")
	doc = frappe.get_doc("Attendance Request", name)
	status = doc.jew_od_status
	if cint(doc.get("half_day")) and doc.get("half_day_date") and getdate(doc.from_date) == getdate(doc.to_date) \
			and frappe.db.exists("Attendance", {"employee": doc.employee, "attendance_date": doc.half_day_date, "docstatus": 1, "status": "Half Day"}):
		doc.flags.ignore_validate = True    # day already marked Half Day: HRMS validation would refuse every save; _finalize_od_half_present counts the office half
	if status not in ("Pending Dept Head", "Pending"):
		return _fail("This request has already been decided.", "invalid_status")
	employee_doc = frappe.get_doc("Employee", doc.employee)
	if not via_admin:
		allowed = [e.lower() for e in (_od_stage_recipients(doc, employee_doc) or [])]
		if not acting_user or acting_user.lower() not in allowed:
			return _fail("You are not the approver for this request.", "permission_denied")
	date_range = "{0} to {1}".format(_fmt_dmy(doc.from_date), _fmt_dmy(doc.to_date))
	if action == "Rejected":
		doc.jew_od_status = "Rejected"
		doc.jew_od_remarks = remarks
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		_safe_insert_notification(doc.employee, "On Duty request updated", f"On Duty {date_range}: Rejected", "Info", "Attendance Request", doc.name)
		return _ok({"name": doc.name, "status": "Rejected"}, "On Duty request rejected.")
	if status == "Pending Dept Head":
		# Department Head approved -> forward to the final approver (HR / Sunil).
		doc.jew_od_status = "Pending"
		if remarks:
			doc.jew_od_remarks = remarks
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		try:
			_send_on_duty_request_email(doc, employee_doc)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "JEW HRMS OD forward email failed")
		_safe_insert_notification(doc.employee, "On Duty forwarded", f"On Duty {date_range}: Department Head approved; pending final approval.", "Info", "Attendance Request", doc.name)
		return _ok({"name": doc.name, "status": "Pending"}, "Approved by Department Head; forwarded for final approval.")
	doc.jew_od_status = "Approved"
	doc.jew_od_remarks = remarks
	doc.save(ignore_permissions=True)
	try:
		doc.flags.ignore_permissions = True
		doc.submit()
	except Exception as exc:
		frappe.db.rollback()
		return _fail(str(exc)[:200], "submit_failed")
	try:
		if cint(doc.get("half_day")) and doc.get("half_day_date"):
			_finalize_od_half_present(doc.employee, doc.half_day_date)
		elif _od_time_from_text(doc.get("explanation")):
			_apply_time_od_attendance(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS OD half finalize failed")
	frappe.db.commit()
	_safe_insert_notification(doc.employee, "On Duty request updated", f"On Duty {date_range}: Approved", "Info", "Attendance Request", doc.name)
	return _ok({"name": doc.name, "status": "Approved"}, "On Duty request approved.")


def _employee_option(employee):
	description = " · ".join([part for part in [employee.name, employee.department, employee.designation] if part])
	return {
		"value": employee.name,
		"label": employee.employee_name or employee.name,
		"description": description,
	}


@frappe.whitelist()
def get_approvers_overview():
	"""Admin-only: who is currently configured as an approver for Leave Application
	and Late/Early — MD (JEW HRMS Owner role), HR (JEW HRMS HR role), and each
	Department Head (custom_dept_head_email on Employee) with their team. Exists so
	Admin/HR can audit this themselves in the app instead of it only being visible
	via a backend script - a wrong role assignment (e.g. someone holding JEW HRMS
	Owner/HR by mistake) shows up here immediately."""
	_require_admin()

	def _users_for_role(role):
		rows = []
		for u in frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"):
			if u in ("Administrator", "Guest"):
				continue
			info = frappe.db.get_value("User", u, ["full_name", "email", "enabled"], as_dict=True)
			if info and info.enabled:
				rows.append({"user": u, "name": info.full_name or u, "email": info.email or u})
		return sorted(rows, key=lambda r: r["name"] or "")

	md_users = _users_for_role("JEW HRMS Owner")
	hr_users = _users_for_role("JEW HRMS HR")

	dept_head_rows = frappe.get_all(
		"Employee",
		# NOTE: "not in" with a NULL in the list would poison every row's comparison
		# to NULL/false in SQL, matching nothing - "!=" doesn't have that issue and
		# naturally excludes NULL rows too.
		filters={"status": "Active", "custom_dept_head_email": ["!=", ""]},
		fields=["name", "employee_name", "custom_dept_head_email"],
	)
	grouped = {}
	for e in dept_head_rows:
		grouped.setdefault(e.custom_dept_head_email, []).append({"employee": e.name, "employee_name": e.employee_name or e.name})
	dept_heads = []
	for email, team in grouped.items():
		dept_heads.append({
			"email": email,
			"name": _user_display_name(email) or email,
			"team_count": len(team),
			"team": sorted(team, key=lambda t: t["employee_name"] or ""),
		})
	dept_heads.sort(key=lambda d: d["name"] or "")

	return _ok({"md": md_users, "hr": hr_users, "dept_heads": dept_heads})


@frappe.whitelist()
def get_employee_list(search=None, limit=0):
	_require_login()
	is_admin_access = _can_access_admin()
	is_dept_head = _is_any_dept_head()
	if not (is_admin_access or is_dept_head):
		return _fail("You do not have permission.", "permission_denied")
	# The mobile app's employee pickers (Assign Location, admin Employee search, ...)
	# fetch this list ONCE with no `search` and then filter client-side as the user
	# types - they never re-call with a `search` term. The old default/cap of
	# 500/1000 silently cut off anyone past that point in employee_name order (664
	# active employees today and growing), so an employee like Roshni Jain simply
	# never existed in the picker's data no matter what was typed - not a
	# permission issue. Admin-only + a rare screen-load call (not per-keystroke),
	# so fetching everyone is cheap; only cap when a caller explicitly asks for less.
	limit = min(int(limit), 5000) if limit else 0
	filters = {"status": "Active"}
	if not is_admin_access:
		# Department head (no admin role): restrict directory to their own team PLUS
		# their own employee record (so they can self face-register / self-assign).
		u = frappe.session.user
		uemail = frappe.db.get_value("User", u, "email") or u
		team = frappe.get_all("Employee", filters={"status": "Active", "custom_dept_head_email": ["in", list({uemail, u})]}, pluck="name")
		own = _get_employee_for_user(u)
		if own and own.name not in team:
			team.append(own.name)
		filters["name"] = ["in", team or ["__none__"]]
	or_filters = None
	if search:
		search_text = f"%{str(search).strip()}%"
		or_filters = [
			["Employee", "name", "like", search_text],
			["Employee", "employee_name", "like", search_text],
			["Employee", "user_id", "like", search_text],
			["Employee", "department", "like", search_text],
			["Employee", "designation", "like", search_text],
		]
	employees = frappe.get_list(
		"Employee",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "employee_name", "department", "designation", "user_id"],
		order_by="employee_name asc",
		limit_page_length=limit or 0,
		ignore_permissions=True,
	)
	for employee in employees:
		face = _active_face(employee.name)
		employee.face_registered = bool(face)
		employee.face_last_updated_on = face.last_updated_on if face else None
		employee.location_count = len(_assigned_locations(employee.name))
		employee.value = employee.name
		employee.label = employee.employee_name or employee.name
		employee.description = " · ".join([part for part in [employee.name, employee.department, employee.designation] if part])
	return {"ok": True, "success": True, "message": "success", "code": "success", "error": None, "employees": employees, "data": [_employee_option(employee) for employee in employees], "count": len(employees), "roles": list(_roles())}


LATE_EARLY_ACTIVE_STATUSES = ("Pending", "Pending HR", "Approved")


def _late_early_application_count(employee, application_type, date_value=None, exclude=None):
	"""How many SELF-SERVICE Late Coming / Early Going requests (this type only,
	Pending / Pending HR / Approved) the employee has already raised this month —
	deliberately excludes the separate auto-detection engine's rows, since the
	LATE_EARLY_MONTHLY_REQUEST_CAP is about how many times an employee has asked,
	not how many times the system flagged them."""
	start, end = _month_range(date_value)
	filters = {
		"employee": employee,
		"application_type": application_type,
		"application_date": ["between", [start, end]],
		"status": ["in", list(LATE_EARLY_ACTIVE_STATUSES)],
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	return frappe.db.count("JEW Late Early Application", filters)


def _late_early_count(employee, application_type, date_value=None, exclude=None):
	"""Combined monthly usage for Late Coming / Early Going: auto-detected
	JEW Attendance Regularization rows PLUS self-service JEW Late Early Application
	rows (Pending / Pending HR / Approved — an application still counts toward the
	monthly allowance while it's anywhere in the two-stage approval, not just once
	fully Approved) — both systems share ONE monthly allowance, set on
	JEW Shift Attendance Policy.max_late_coming_allowed_per_month / max_early_going_allowed_per_month."""
	count = _issue_count(employee, application_type, date_value)
	if frappe.db.exists("DocType", "JEW Late Early Application"):
		start, end = _month_range(date_value)
		filters = {
			"employee": employee,
			"application_type": application_type,
			"application_date": ["between", [start, end]],
			"status": ["in", list(LATE_EARLY_ACTIVE_STATUSES)],
		}
		if exclude:
			filters["name"] = ["!=", exclude]
		count += frappe.db.count("JEW Late Early Application", filters)
	return count


def _late_early_email_context(doc, employee_doc, decision=None, view=None):
	dh_email = _employee_dept_head_email(employee_doc)
	site = frappe.utils.get_url() + "/jew-hrms/m"
	return {
		"employee_name": employee_doc.employee_name or employee_doc.name,
		"employee_id": employee_doc.name,
		"department": employee_doc.get("department") or "-",
		"application_type": doc.application_type,
		"application_date": _fmt_dmy(doc.application_date),
		"expected_time": doc.expected_time,
		"reason": doc.reason,
		"application_id": doc.name,
		"department_head_name": _user_display_name(dh_email) if dh_email else "-",
		"decision": decision or doc.status,
		"remarks": doc.remarks or "",
		"company_name": employee_doc.get("company") or "Jain Engineering Works (India) Private Limited",
		"app_link": "{0}?view={1}".format(site, view) if view else site,
	}


def _generate_late_early_email_action_token(doc, user):
	"""Issue a single-use, expiring, stage-locked email-action token bound to the
	doc's CURRENT status + the intended approver — same scheme as the Leave
	Application one (_generate_email_action_token), just against this doctype.
	Overwrites any previous token (so an old-stage link can no longer be used)."""
	token = frappe.generate_hash(length=48)
	frappe.db.set_value("JEW Late Early Application", doc.name, {
		"email_action_token": token,
		"email_action_token_user": user or "",
		"email_action_token_stage": doc.status,
		"email_action_token_expiry": add_to_date(now_datetime(), days=EMAIL_ACTION_TOKEN_VALIDITY_DAYS),
	}, update_modified=False)
	return token


def _validate_late_early_email_action_token(token, action):
	"""Validate a Late/Early email-action token. Returns (doc, None) or (None, error).
	For approve/reject the token must still match the doc's CURRENT status (Pending
	or Pending HR) and the doc must not be finalised; view is lenient."""
	if not token:
		return None, "Invalid or missing action link."
	name = frappe.db.get_value("JEW Late Early Application", {"email_action_token": token}, "name")
	if not name:
		return None, "This link is no longer valid — it may have already been used or superseded."
	doc = frappe.get_doc("JEW Late Early Application", name)
	expiry = doc.get("email_action_token_expiry")
	if expiry and get_datetime(expiry) < now_datetime():
		return None, "This action link has expired."
	if action in ("approve", "reject"):
		if doc.status not in ("Pending", "Pending HR"):
			return None, "This application has already been finalised."
		stage = doc.get("email_action_token_stage")
		if stage and stage != doc.status:
			return None, "This application has already moved to the next stage."
	return doc, None


def _send_late_early_request_email(doc, employee_doc):
	"""Email the employee's Department Head that a Late/Early request is pending
	their approval, with one-click Approve/Reject/View token links (same scheme as
	the Leave Application emails). Best-effort — never breaks the apply call."""
	dh_email = _employee_dept_head_email(employee_doc)
	if not dh_email:
		return
	token = _generate_late_early_email_action_token(doc, dh_email)
	site = frappe.utils.get_url()
	ctx = _late_early_email_context(doc, employee_doc, view="lateEarlyApproval")
	ctx["approve_link"] = "{0}/late_early_email_action?token={1}&action=approve".format(site, token)
	ctx["reject_link"] = "{0}/late_early_email_action?token={1}&action=reject".format(site, token)
	ctx["app_link"] = "{0}/late_early_email_action?token={1}&action=view".format(site, token)
	_send_template_email([dh_email], "Late Early - Dept Head Approval Request", ctx, doc)
	msg = "{0} applied for {1} on {2} - pending your Department Head approval.".format(
		employee_doc.employee_name or employee_doc.name, doc.application_type, _fmt_dmy(doc.application_date))
	_desk_bell_notify([dh_email], doc, msg)


def _send_late_early_hr_request_email(doc, employee_doc):
	"""Email HR that a Department Head has approved a Late/Early request and it now
	needs HR's sign-off (the second, final approval stage), with one-click
	Approve/Reject/View token links. Best-effort."""
	hr_user = _stage_user_for_role("JEW HRMS HR")
	recipients = _hr_emails()
	if not recipients:
		return
	token = _generate_late_early_email_action_token(doc, hr_user)
	site = frappe.utils.get_url()
	ctx = _late_early_email_context(doc, employee_doc, decision="Pending HR", view="lateEarlyApprovalHr")
	ctx["approve_link"] = "{0}/late_early_email_action?token={1}&action=approve".format(site, token)
	ctx["reject_link"] = "{0}/late_early_email_action?token={1}&action=reject".format(site, token)
	ctx["app_link"] = "{0}/late_early_email_action?token={1}&action=view".format(site, token)
	_send_template_email(recipients, "Late Early - HR Approval Request", ctx, doc)
	msg = "{0} applied for {1} on {2} - pending your HR approval.".format(
		employee_doc.employee_name or employee_doc.name, doc.application_type, _fmt_dmy(doc.application_date))
	_desk_bell_notify(recipients, doc, msg)


def _send_late_early_decision_email(doc, employee_doc, decision):
	"""Email the employee once the application is FINALLY decided (HR-approved or
	rejected at either stage). Best-effort. The link opens the app straight to the
	employee's own Late/Early requests list."""
	appl = _applicant_email(employee_doc)
	if not appl:
		return
	ctx = _late_early_email_context(doc, employee_doc, decision=decision, view="lateEarly")
	_send_template_email([appl], "Late Early - Employee Notified After Decision", ctx, doc)


def _send_late_early_applied_email(doc, employee_doc):
	"""Confirmation e-mail to the APPLICANT the moment a Late Coming / Early Going request is submitted
	(until now only the Department Head was mailed, so the employee heard nothing). Information only - no
	action links. If the employee has no Department Head configured, say so, because then the request has
	not reached any approver. Best-effort: never breaks the application insert."""
	try:
		applicant = _applicant_email(employee_doc)
		if not applicant:
			return
		dh_email = _employee_dept_head_email(employee_doc)
		try:
			hh, mm = str(doc.expected_time).split(":")[:2]
			time_label = "{0:02d}:{1:02d}".format(int(hh), int(mm))
		except Exception:
			time_label = str(doc.expected_time or "-")
		emp_name = employee_doc.employee_name or employee_doc.name
		if dh_email:
			status_line = (
				"It has been sent to your Department Head (<strong>{0}</strong>) for approval, and then to HR. "
				"You will get another email once it is decided."
			).format(frappe.utils.escape_html(dh_email))
		else:
			status_line = (
				"<strong>No Department Head is set for you, so this request has NOT reached an approver.</strong> "
				"Please contact HR."
			)
		message = (
			"<p>Hello {emp},</p>"
			"<p>Your <strong>{kind}</strong> request for <strong>{day}</strong> (expected time <strong>{tm}</strong>) "
			"has been submitted.</p><p><strong>Reason:</strong> {reason}</p><p>{status}</p>"
			"<p style=\"color:#64707a;font-size:12.5px;margin-top:14px;\">This is an information-only message.</p>"
		).format(
			emp=frappe.utils.escape_html(emp_name), kind=frappe.utils.escape_html(doc.application_type or "-"),
			day=_fmt_dmy(doc.application_date), tm=time_label,
			reason=frappe.utils.escape_html(doc.get("reason") or "-"), status=status_line,
		)
		frappe.sendmail(
			recipients=[applicant],
			subject="{0} request submitted ({1})".format(doc.application_type, _fmt_dmy(doc.application_date)),
			message=message,
			sender=_hrms_sender_email(),
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS late/early applicant confirmation failed")


@frappe.whitelist()
def apply_late_early(application_type=None, application_date=None, expected_time=None, reason=None):
	employee = _get_current_employee()
	if application_type not in ("Late Coming", "Early Going"):
		return _fail("Application type must be Late Coming or Early Going.", "validation_error")
	if not application_date or not expected_time or not reason:
		return _fail("Application date, expected time and reason are required.", "validation_error")
	application_date = getdate(application_date)
	if application_date < getdate(today()):
		return _fail("Application Date cannot be in the past.", "past_date_not_allowed")
	policy = _get_active_policy(employee=employee.name, shift=employee.get("default_shift"))
	if not policy:
		return _fail("Shift policy is not configured. Please contact HR/Admin.", "shift_policy_missing")
	# Hard cap: at most LATE_EARLY_MONTHLY_REQUEST_CAP self-service requests of THIS
	# type per month — reject the request outright rather than letting it go to the
	# Department Head. (Separately, LATE_EARLY_HALF_DAY_THRESHOLD still applies at
	# decision time against the combined auto-detected + self-service count.)
	existing_count = _late_early_application_count(employee.name, application_type, application_date)
	if existing_count >= LATE_EARLY_MONTHLY_REQUEST_CAP:
		return _fail(
			"You have already used {0} {1} requests this month.".format(LATE_EARLY_MONTHLY_REQUEST_CAP, application_type),
			"monthly_cap_exceeded",
		)
	if frappe.db.exists("JEW Late Early Application", {
		"employee": employee.name,
		"application_date": application_date,
		"application_type": application_type,
		"status": ["in", list(LATE_EARLY_ACTIVE_STATUSES)],
	}):
		return _fail("You already have a {0} application for this date.".format(application_type), "duplicate_application")
	doc = frappe.get_doc({
		"doctype": "JEW Late Early Application",
		"employee": employee.name,
		"application_type": application_type,
		"application_date": application_date,
		"expected_time": expected_time,
		"shift_name": policy.shift_name,
		"reason": reason,
		"status": "Pending",
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	_safe_insert_notification(employee.name, "Late/Early application submitted", f"{application_type} on {application_date} pending your Department Head's review.", "Info", "JEW Late Early Application", doc.name)
	_send_late_early_request_email(doc, employee)
	_send_late_early_applied_email(doc, employee)
	return _ok({"name": doc.name, "whatsapp": _safe_request_whatsapp("late_early", doc, employee)}, "Application submitted")


@frappe.whitelist()
def get_my_late_early(month=None):
	employee = _get_current_employee()
	fields = ["name", "application_type", "application_date", "expected_time", "reason", "status", "remarks"]
	if frappe.get_meta("JEW Late Early Application").has_field("jew_cancel_status"):
		fields.extend(["jew_cancel_status", "jew_cancel_remarks"])
	rows = frappe.get_list(
		"JEW Late Early Application",
		filters={"employee": employee.name},
		fields=fields,
		order_by="application_date desc, modified desc",
		limit_page_length=100,
		ignore_permissions=True,
	)
	return _ok({"applications": rows})


@frappe.whitelist()
def cancel_late_early(name=None):
	employee = _get_current_employee()
	doc = frappe.get_doc("JEW Late Early Application", name)
	if doc.employee != employee.name and not _is_admin():
		return _fail("You do not have permission.", "permission_denied")
	if doc.status != "Pending":
		return _fail("Only a Pending application can be cancelled.", "invalid_status")
	doc.status = "Cancelled"
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return _ok({"whatsapp": _safe_request_whatsapp("late_early_cancelled", doc)}, "Application cancelled")


def _dept_head_team(user=None):
	"""Active employee names whose configured Department Head is `user`."""
	u = user or frappe.session.user
	uemail = frappe.db.get_value("User", u, "email") or u
	return frappe.get_all("Employee", filters={"status": "Active", "custom_dept_head_email": ["in", list({uemail, u})]}, pluck="name")


@frappe.whitelist()
def get_pending_late_early():
	"""Late/Early approval is STRICT Department Head only — each dept head sees only
	their own team's pending requests, not a general HR/Admin view."""
	_require_login()
	if not _is_any_dept_head():
		return _fail("You do not have permission.", "permission_denied")
	team = _dept_head_team()
	if not team:
		return _ok({"applications": []})
	rows = frappe.get_list(
		"JEW Late Early Application",
		filters={"status": "Pending", "employee": ["in", team]},
		fields=["name", "employee", "employee_name", "application_type", "application_date", "expected_time", "shift_name", "reason", "status"],
		order_by="application_date asc, modified desc",
		limit_page_length=100,
		ignore_permissions=True,
	)
	return _ok({"applications": rows})


@frappe.whitelist()
def get_pending_late_early_hr():
	"""Second, final approval stage: once the employee's Department Head has
	approved, the application moves to 'Pending HR' and only HR (JEW HRMS HR) can
	see/decide it here — this is a company-wide queue, not scoped to any one team,
	since HR is the single final sign-off for every Late/Early request."""
	_require_login()
	if not _is_workflow_hr():
		return _fail("You do not have permission.", "permission_denied")
	rows = frappe.get_list(
		"JEW Late Early Application",
		filters={"status": "Pending HR"},
		fields=["name", "employee", "employee_name", "application_type", "application_date", "expected_time", "shift_name", "reason", "status", "dept_head_approved_by", "dept_head_approved_on"],
		order_by="application_date asc, modified desc",
		limit_page_length=100,
		ignore_permissions=True,
	)
	return _ok({"applications": rows})


def _late_early_combined_count(employee, date_value=None, exclude=None):
	"""Combined Late Coming + Early Going usage for the month (both count toward
	the same LATE_EARLY_HALF_DAY_THRESHOLD, per 'X Early Going or Late Coming')."""
	return (
		_late_early_count(employee, "Late Coming", date_value, exclude)
		+ _late_early_count(employee, "Early Going", date_value, exclude)
	)


def _mark_half_day_for_late_early(employee, attendance_date):
	"""Best-effort: mark `attendance_date` as Half Day once the monthly Late/Early
	threshold is crossed. Never raises — an Attendance-marking failure must not
	block the approval itself."""
	try:
		attendance_date = getdate(attendance_date)
		existing = frappe.db.get_value(
			"Attendance", {"employee": employee, "attendance_date": attendance_date, "docstatus": ["<", 2]}, "name"
		)
		if existing:
			frappe.db.set_value("Attendance", existing, "status", "Half Day", update_modified=False)
			return
		emp = frappe.db.get_value("Employee", employee, ["company", "status"], as_dict=True) or frappe._dict()
		if emp.get("status") and emp.status != "Active":
			return
		doc = frappe.new_doc("Attendance")
		doc.employee = employee
		if emp.get("company"):
			doc.company = emp.company
		doc.attendance_date = attendance_date
		doc.status = "Half Day"
		doc.insert(ignore_permissions=True)
		doc.submit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS late/early half-day mark failed")


@frappe.whitelist()
def decide_late_early(name=None, decision=None, remarks=None):
	"""Two-stage approval: Department Head decides first (Pending -> Pending HR /
	Rejected), then HR gives the final sign-off (Pending HR -> Approved / Rejected).
	Which stage a caller may act on is determined by the application's CURRENT
	status, not by who they are — so this one endpoint serves both the Dept Head's
	and HR's decide actions; the app just points each screen at it."""
	_require_login()
	if not name or decision not in ("Approved", "Rejected"):
		return _fail("Application and a valid decision are required.", "validation_error")
	doc = frappe.get_doc("JEW Late Early Application", name)
	employee_doc = frappe.get_doc("Employee", doc.employee)

	if doc.status == "Pending":
		if not _is_dept_head_of(frappe.session.user, employee_doc):
			return _fail("You do not have permission.", "permission_denied")
		if decision == "Rejected" and not remarks:
			return _fail("Reject Reason required.", "reject_reason_required")
		doc.dept_head_approved_by = frappe.session.user
		doc.dept_head_approved_on = get_datetime(now_datetime())
		doc.reviewed_by = frappe.session.user
		doc.reviewed_on = doc.dept_head_approved_on
		doc.remarks = remarks
		if decision == "Approved":
			doc.status = "Pending HR"
			doc.save(ignore_permissions=True)
			_safe_insert_notification(doc.employee, "Late/Early application updated", f"{doc.application_type} on {doc.application_date}: approved by Department Head, pending HR.", "Info", "JEW Late Early Application", doc.name)
			_send_late_early_hr_request_email(doc, employee_doc)
			frappe.db.commit()
			return _ok({"name": doc.name, "status": doc.status}, "Sent to HR for final approval")
		doc.status = "Rejected"
		doc.save(ignore_permissions=True)
		_safe_insert_notification(doc.employee, "Late/Early application updated", f"{doc.application_type} on {doc.application_date}: Rejected", "Info", "JEW Late Early Application", doc.name)
		_send_late_early_decision_email(doc, employee_doc, "Rejected")
		frappe.db.commit()
		return _ok({"name": doc.name, "status": doc.status}, "Application updated")

	if doc.status == "Pending HR":
		if not _is_workflow_hr():
			return _fail("You do not have permission.", "permission_denied")
		if decision == "Rejected" and not remarks:
			return _fail("Reject Reason required.", "reject_reason_required")
		doc.hr_approved_by = frappe.session.user
		doc.hr_approved_on = get_datetime(now_datetime())
		doc.reviewed_by = frappe.session.user
		doc.reviewed_on = doc.hr_approved_on
		doc.remarks = remarks
		doc.status = decision
		doc.save(ignore_permissions=True)
		if decision == "Approved" and _late_early_combined_count(doc.employee, doc.application_date) >= LATE_EARLY_HALF_DAY_THRESHOLD:
			_mark_half_day_for_late_early(doc.employee, doc.application_date)
		_safe_insert_notification(doc.employee, "Late/Early application updated", f"{doc.application_type} on {doc.application_date}: {decision}", "Info", "JEW Late Early Application", doc.name)
		_send_late_early_decision_email(doc, employee_doc, decision)
		frappe.db.commit()
		return _ok({"name": doc.name, "status": doc.status}, "Application updated")

	return _fail("This application has already been decided.", "invalid_status")


@frappe.whitelist(allow_guest=True)
def confirm_late_early_email_action(token=None, action=None, remarks=None, csrf_token=None):
	"""POST target of the late_early_email_action page (one-click email
	Approve/Reject) — same scheme as confirm_leave_email_action. Runs as the
	token's intended approver, reusing decide_late_early (so the same two-stage
	permission checks + next-stage email apply as an in-app decision). Redirects
	back to a result page."""
	from urllib.parse import urlencode
	def _redirect(**params):
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = "/late_early_email_action?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})
	doc, error = _validate_late_early_email_action_token(token, action)
	if not doc:
		return _redirect(done=1, ok=0, msg=error or "This link is not valid.")
	if action == "reject" and not (remarks and str(remarks).strip()):
		return _redirect(action="reject", token=token, missing_remarks=1)
	acting_user = doc.get("email_action_token_user") or frappe.session.user
	orig_user = frappe.session.user
	try:
		frappe.set_user(acting_user)
		if action == "approve":
			res = decide_late_early(name=doc.name, decision="Approved", remarks=remarks)
		elif action == "reject":
			res = decide_late_early(name=doc.name, decision="Rejected", remarks=remarks)
		else:
			res = {"ok": False, "message": "Unknown action."}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS confirm_late_early_email_action failed")
		res = {"ok": False, "message": "Could not complete this action."}
	finally:
		frappe.set_user(orig_user)
	ok = bool(res.get("ok"))
	return _redirect(done=1, ok=1 if ok else 0, msg=res.get("message") or ("Done." if ok else "Could not complete this action."))


@frappe.whitelist()
def hr_update_salary_slip_amounts(salary_slip=None, earnings=None, deductions=None):
	"""HR tool (desk + mobile): directly set earning/deduction amounts on a Salary
	Slip - including a submitted one - WITHOUT triggering ERPNext's auto
	recalculation, so a manual HR correction sticks (ERPNext would otherwise
	recompute everything from the Salary Structure + attendance on a normal save).
	Uses the same raw, no-GL post-submit patch the monthly generator already relies
	on. earnings / deductions are {component: amount} maps (JSON string or dict).
	HR roles only; every change is recorded on the slip's timeline."""
	roles = set(_roles())
	allowed = {"HR Manager", "HR User", "System Manager", "JEW HRMS HR", "JEW HRMS Admin", "JEW HRMS Owner"}
	if not (roles & allowed):
		frappe.throw(_("You are not permitted to edit salary amounts."))
	if not salary_slip or not frappe.db.exists("Salary Slip", salary_slip):
		frappe.throw(_("Salary Slip not found."))
	earnings = json.loads(earnings) if isinstance(earnings, str) else (earnings or {})
	deductions = json.loads(deductions) if isinstance(deductions, str) else (deductions or {})
	docstatus = frappe.db.get_value("Salary Slip", salary_slip, "docstatus")

	def _apply(parentfield, data):
		existing = {
			d.salary_component: d.name
			for d in frappe.get_all(
				"Salary Detail",
				filters={"parent": salary_slip, "parentfield": parentfield},
				fields=["name", "salary_component"],
			)
		}
		changes = []
		for comp, amt in (data or {}).items():
			amt = flt(amt)
			if comp in existing:
				old = flt(frappe.db.get_value("Salary Detail", existing[comp], "amount"))
				if old != amt:
					frappe.db.set_value("Salary Detail", existing[comp], "amount", amt, update_modified=False)
					changes.append("%s: %s -> %s" % (comp, old, amt))
			elif amt:
				ab = frappe.db.get_value("Salary Component", comp, "salary_component_abbr")
				if not ab:
					continue
				frappe.get_doc({
					"doctype": "Salary Detail", "parenttype": "Salary Slip", "parent": salary_slip,
					"parentfield": parentfield, "salary_component": comp, "abbr": ab,
					"amount": amt, "docstatus": docstatus,
				}).insert(ignore_permissions=True)
				changes.append("%s: (new) %s" % (comp, amt))
		return changes

	changes = _apply("earnings", earnings) + _apply("deductions", deductions)
	rows = frappe.get_all("Salary Detail", filters={"parent": salary_slip}, fields=["parentfield", "amount"])
	gross = sum(flt(r.amount) for r in rows if r.parentfield == "earnings")
	ded = sum(flt(r.amount) for r in rows if r.parentfield == "deductions")
	net = gross - ded
	frappe.db.set_value("Salary Slip", salary_slip, {
		"gross_pay": gross, "total_deduction": ded, "net_pay": net, "rounded_total": round(net),
	}, update_modified=False)
	if changes:
		frappe.get_doc("Salary Slip", salary_slip).add_comment(
			"Info", _("HR edited salary amounts ({0}): {1}").format(frappe.session.user, "; ".join(changes))
		)
	frappe.db.commit()
	return _ok({"gross_pay": gross, "total_deduction": ded, "net_pay": net}, "Salary amounts updated.")


# ---------------------------------------------------------------------------
# Daily Staff Attendance Report - emailed to HR (+ extra recipients) each morning
# at 10:00 (Asia/Kolkata, via hooks cron) for the PREVIOUS day. Table format mirrors
# the manual "Daily Attendance Report - STAFF" sheet: SN, E.Code, Name, Shift,
# In Time, Out Time, Status. Fixed staff list (HR tells us when it changes).
# ---------------------------------------------------------------------------

DAILY_STAFF_ATTENDANCE_EMPLOYEES = [
	"HR-EMP-00588",  # Varun Misar
	"HR-EMP-00585",  # Mayur Ganwir
	"HR-EMP-00590",  # Mayur Kaushal
	"HR-EMP-00396",  # Prakash Shukla
	"HR-EMP-00527",  # Prashant Pandey
	"HR-EMP-00302",  # Mayank Gangwal
	"HR-EMP-00130",  # Dharmendra Tiwari
	"HR-EMP-00391",  # Durgesh Joshi
	"HR-EMP-00591",  # Vishnu Dangi
	"HR-EMP-00587",  # Roshni Jain
	"HR-EMP-00584",  # Akshay Nandankar
	"HR-EMP-00133",  # Pinkesh Chouriya
	"HR-EMP-00535",  # Ajay Mishra
	"HR-EMP-00592",  # Gajendra Vyas
	"HR-EMP-00393",  # Rishabh Shriwas
	"HR-EMP-00377",  # Abhijeet Jha
	"HR-EMP-00389",  # Nandkishor Kochkar
	"HR-EMP-00397",  # Kavita Kochle
	"HR-EMP-00394",  # Suraj Malviya
	"HR-EMP-00390",  # Shubhangi Pawar
	"HR-EMP-00386",  # Poojashree Sahu
	"HR-EMP-00583",  # Vinayak Badodiya
	"HR-EMP-00732",  # Ankit Meena
	"HR-EMP-00581",  # Mahesh Rawat
	"HR-EMP-00620",  # Ekansh Gome
	"HR-EMP-00769",  # Prashant Nagar
]

# Extra recipients beyond everyone holding the "JEW HRMS HR" role (_hr_emails()).
DAILY_STAFF_ATTENDANCE_EXTRA_RECIPIENTS = [
	"sunil.surana@jewonline.in",
	"kavita.kochle@thesvsgroup.org",
]


def _hhmm(t):
	return str(t)[11:16] if t else ""


def _daily_staff_attendance_rows(date_value):
	d = getdate(date_value)
	start = "{0} 00:00:00".format(d)
	end = "{0} 23:59:59".format(d)
	rows = []
	sn = 0
	for emp in DAILY_STAFF_ATTENDANCE_EMPLOYEES:
		info = frappe.db.get_value(
			"Employee", emp,
			["employee_name", "default_shift", "attendance_device_id", "status"],
			as_dict=True,
		)
		if not info:
			continue
		sn += 1
		att = frappe.db.get_value(
			"Attendance", {"employee": emp, "attendance_date": d, "docstatus": 1},
			["status", "shift", "in_time", "out_time"], as_dict=True,
		)
		status = att.status if att else "Absent"
		shift = (att.shift if (att and att.shift) else info.default_shift) or ""
		intime = _hhmm(att.in_time) if (att and att.in_time) else ""
		outtime = _hhmm(att.out_time) if (att and att.out_time) else ""
		if not intime:
			ci = frappe.get_all(
				"Employee Checkin",
				filters={"employee": emp, "time": ["between", [start, end]]},
				fields=["log_type", "time"], order_by="time",
			)
			if ci:
				ins = [c.time for c in ci if (c.log_type or "").upper().startswith("IN")] or [c.time for c in ci]
				outs = [c.time for c in ci if (c.log_type or "").upper().startswith("OUT")]
				if ins:
					intime = _hhmm(min(ins))
				if outs:
					outtime = _hhmm(max(outs))
				elif ci:
					outtime = _hhmm(max(c.time for c in ci))
				if (not att) and ci:
					status = "Present"
		if status in ("Absent", "On Leave"):
			intime = ""
			outtime = ""
		rows.append({
			"sn": sn,
			"ecode": emp,
			"name": info.employee_name or emp,
			"shift": shift,
			"in": intime or "-",
			"out": outtime or "-",
			"status": status,
		})
	return rows


def _daily_staff_attendance_html(date_value):
	d = getdate(date_value)
	rows = _daily_staff_attendance_rows(d)
	present = sum(1 for r in rows if r["status"] == "Present")
	absent = sum(1 for r in rows if r["status"] == "Absent")
	th = "padding:6px 9px;border:1px solid #444;background:#eef2f7;text-align:left;font-size:12px;white-space:nowrap"
	tdp = "padding:5px 9px;border:1px solid #444;font-size:12px"
	heads = ["SN", "Employee ID", "Name", "Shift", "In Time", "Out Time", "Status"]
	header = "".join("<th style=\"{0}\">{1}</th>".format(th, h) for h in heads)
	body = []
	for r in rows:
		if r["status"] == "Present":
			color = "#1a7f37"
		elif r["status"] == "Absent":
			color = "#b00020"
		else:
			color = "#b5830a"
		cells = "".join(
			"<td style=\"{0}\">{1}</td>".format(tdp, v)
			for v in [r["sn"], r["ecode"], r["name"], r["shift"], r["in"], r["out"]]
		)
		cells += "<td style=\"{0};color:{1};font-weight:bold\">{2}</td>".format(tdp, color, r["status"])
		body.append("<tr>{0}</tr>".format(cells))
	return (
		"<div style=\"font-family:Arial,sans-serif\">"
		"<h3 style=\"margin:0 0 4px\">Daily Attendance Report - STAFF</h3>"
		"<p style=\"margin:0 0 10px;color:#444\"><b>{date}</b> &nbsp;|&nbsp; "
		"Present: <b>{present}</b> &nbsp; Absent: <b>{absent}</b> &nbsp; Total: <b>{total}</b></p>"
		"<table style=\"border-collapse:collapse\"><thead><tr>{header}</tr></thead>"
		"<tbody>{body}</tbody></table>"
		"<p style=\"margin-top:10px;color:#888;font-size:11px\">Auto-generated by HRMS.</p></div>"
	).format(
		date=formatdate(d), present=present, absent=absent, total=len(rows),
		header=header, body="".join(body),
	)


@frappe.whitelist()
def send_daily_staff_attendance(for_date=None, test_recipient=None):
	"""Email the STAFF daily-attendance table (SN/E.Code/Name/Shift/In/Out/Status).
	Scheduled daily at 10:00 Asia/Kolkata (hooks cron) and then reports YESTERDAY.
	for_date re-sends a specific day; test_recipient sends only to that one address
	(preview). Recipients otherwise = everyone with the JEW HRMS HR role plus
	DAILY_STAFF_ATTENDANCE_EXTRA_RECIPIENTS. JEWPL site only (scheduler runs per-site)."""
	if frappe.local.site != "jewipl.duxdigitech.in":
		return
	# 2026-10-10 only: that day was sent manually to a one-off list (sunil excluded),
	# so skip the scheduled auto-run that single day.
	if not for_date and getdate(today()) == getdate("2026-10-10"):
		return
	d = getdate(for_date) if for_date else add_days(getdate(today()), -1)
	html = _daily_staff_attendance_html(d)
	if test_recipient:
		recipients = [test_recipient]
	else:
		recipients = list(dict.fromkeys(list(DAILY_STAFF_ATTENDANCE_EXTRA_RECIPIENTS)))
	recipients = [r for r in recipients if r]
	if not recipients:
		frappe.log_error("No recipients for daily staff attendance", "JEW HRMS daily attendance")
		return
	try:
		frappe.sendmail(
			recipients=recipients,
			subject="Daily Attendance Report - STAFF ({0})".format(formatdate(d)),
			message=html,
			sender=_hrms_sender_email(),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS daily attendance email failed")
	return _ok({"sent_to": recipients, "date": str(d)}, "Daily staff attendance sent.")


def send_birthday_hr_notice():
	"""Daily. Emails ONLY HR (everyone holding the JEW HRMS HR role) the list of
	employees whose birthday is today - no other employee is notified. Mirrors
	send_work_anniversary_hr_notice. JEWPL site only (scheduler hooks run on every
	site that has this app installed)."""
	if frappe.local.site != "jewipl.duxdigitech.in":
		return
	recipients = ["kavita.kochle@thesvsgroup.org"]
	from hrms.controllers.employee_reminders import get_employees_having_an_event_today
	by_company = get_employees_having_an_event_today("birthday")
	rows = []
	for company, persons in (by_company or {}).items():
		for p in persons:
			name = frappe.utils.escape_html(p.get("name") or p.get("employee_name") or "")
			suffix = " - {0}".format(frappe.utils.escape_html(company)) if company else ""
			rows.append("<li><strong>{0}</strong>{1}</li>".format(name, suffix))
	if not rows:
		return
	message = "<p>Birthday today - please wish:</p><ul>{0}</ul>".format("".join(rows))
	try:
		frappe.sendmail(
			recipients=recipients,
			subject="Birthday Reminder (HR)",
			message=message,
			sender=_hrms_sender_email(),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS birthday HR notice failed")


def _notify_hr_leave_applied(doc, employee_doc, status):
	"""Information-only email to HR whenever a leave is applied, so HR always knows
	who requested leave even while it is still pending the Dept Head / MD. Skipped
	when HR is already the current approver (they then get the approval request
	itself). Best-effort - never breaks the leave insert."""
	try:
		if status in (APPROVAL_STATUSES["pending_hr"], APPROVAL_STATUSES["pending_admin"]):
			return
		recipients = _hr_emails()
		if not recipients:
			return
		if doc.from_date == doc.to_date:
			date_range = _fmt_dmy(doc.from_date)
		else:
			date_range = "{0} to {1}".format(_fmt_dmy(doc.from_date), _fmt_dmy(doc.to_date))
		stage_label = {
			APPROVAL_STATUSES["pending_dept_head"]: "Department Head",
			APPROVAL_STATUSES["pending_md"]: "MD",
		}.get(status, "the approver")
		emp_name = employee_doc.employee_name or employee_doc.name
		message = (
			"<p><strong>{emp}</strong> has applied for <strong>{lt}</strong> leave "
			"from <strong>{dates}</strong>.</p>"
			"<p><strong>Reason:</strong> {reason}</p>"
			"<p style=\"color:#64707a;font-size:12.5px;margin-top:14px;\">"
			"Currently pending {stage} approval. This is an information-only notice to HR "
			"(no action needed here).</p>"
		).format(
			emp=frappe.utils.escape_html(emp_name),
			lt=frappe.utils.escape_html(doc.leave_type or "-"),
			dates=date_range,
			reason=frappe.utils.escape_html(doc.get("description") or doc.get("reason") or "-"),
			stage=stage_label,
		)
		frappe.sendmail(
			recipients=recipients,
			subject="Leave applied: {0} ({1})".format(emp_name, date_range),
			message=message,
			sender=_hrms_sender_email(),
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS HR leave-applied notice failed")


def _notify_dept_head_leave_applied(doc, employee_doc, status):
	"""Information-only email to the employee's Department Head when a leave skips
	straight to HR (same-day / backdated application) - so the Dept Head still knows
	their report applied, even though they are not the approver for that case. For
	normal future-dated leaves the Dept Head is the first approver and already gets
	the approval request, so this only fires for the HR-direct case. Best-effort."""
	try:
		if status not in (APPROVAL_STATUSES["pending_hr"], APPROVAL_STATUSES["pending_admin"]):
			return
		dh = _employee_dept_head_email(employee_doc)
		if not dh:
			return
		if doc.from_date == doc.to_date:
			date_range = _fmt_dmy(doc.from_date)
		else:
			date_range = "{0} to {1}".format(_fmt_dmy(doc.from_date), _fmt_dmy(doc.to_date))
		emp_name = employee_doc.employee_name or employee_doc.name
		message = (
			"<p><strong>{emp}</strong> has applied for <strong>{lt}</strong> leave "
			"from <strong>{dates}</strong> (same-day / backdated).</p>"
			"<p><strong>Reason:</strong> {reason}</p>"
			"<p style=\"color:#64707a;font-size:12.5px;margin-top:14px;\">"
			"This request is going directly to HR for approval. This is an "
			"information-only notice to you as Department Head (no action needed here).</p>"
		).format(
			emp=frappe.utils.escape_html(emp_name),
			lt=frappe.utils.escape_html(doc.leave_type or "-"),
			dates=date_range,
			reason=frappe.utils.escape_html(doc.get("description") or doc.get("reason") or "-"),
		)
		frappe.sendmail(
			recipients=[dh],
			subject="Leave applied (to HR): {0} ({1})".format(emp_name, date_range),
			message=message,
			sender=_hrms_sender_email(),
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS dept-head leave-applied notice failed")
