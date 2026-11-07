import math

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import add_days, get_datetime, getdate, today


EMPLOYEE_CHECKIN_LOCATION_FIELD = "custom_attendance_location"
EMPLOYEE_BRANCH_FIELD = "custom_employee_branch"
ATTENDANCE_IN_LOCATION_FIELD = "custom_in_location"
ATTENDANCE_OUT_LOCATION_FIELD = "custom_out_location"


def ensure_custom_fields():
	create_custom_fields(
		{
			"Employee Checkin": [
				{
					"fieldname": EMPLOYEE_CHECKIN_LOCATION_FIELD,
					"label": "Attendance Location",
					"fieldtype": "Data",
					"insert_after": "device_id",
					"read_only": 1,
					"in_list_view": 1,
				},
				{
					"fieldname": EMPLOYEE_BRANCH_FIELD,
					"label": "Employee Branch",
					"fieldtype": "Link",
					"options": "Branch",
					"insert_after": EMPLOYEE_CHECKIN_LOCATION_FIELD,
					"fetch_from": "employee.branch",
					"fetch_if_empty": 1,
					"read_only": 1,
					"in_list_view": 1,
				},
			],
			"Attendance": [
				{
					"fieldname": EMPLOYEE_BRANCH_FIELD,
					"label": "Employee Branch",
					"fieldtype": "Link",
					"options": "Branch",
					"insert_after": "department",
					"fetch_from": "employee.branch",
					"fetch_if_empty": 1,
					"read_only": 1,
					"in_list_view": 1,
				},
				{
					"fieldname": EMPLOYEE_CHECKIN_LOCATION_FIELD,
					"label": "Attendance Location",
					"fieldtype": "Data",
					"insert_after": "shift",
					"read_only": 1,
					"in_list_view": 1,
				},
				{
					"fieldname": ATTENDANCE_IN_LOCATION_FIELD,
					"label": "IN Location",
					"fieldtype": "Data",
					"insert_after": EMPLOYEE_CHECKIN_LOCATION_FIELD,
					"read_only": 1,
				},
				{
					"fieldname": ATTENDANCE_OUT_LOCATION_FIELD,
					"label": "OUT Location",
					"fieldtype": "Data",
					"insert_after": ATTENDANCE_IN_LOCATION_FIELD,
					"read_only": 1,
				},
			],
		},
		update=True,
	)
	frappe.clear_cache(doctype="Employee Checkin")
	frappe.clear_cache(doctype="Attendance")


def _haversine_m(latitude, longitude, target_latitude, target_longitude):
	latitude = math.radians(float(latitude))
	longitude = math.radians(float(longitude))
	target_latitude = math.radians(float(target_latitude))
	target_longitude = math.radians(float(target_longitude))
	delta_latitude = target_latitude - latitude
	delta_longitude = target_longitude - longitude
	a = (
		math.sin(delta_latitude / 2) ** 2
		+ math.cos(latitude) * math.cos(target_latitude) * math.sin(delta_longitude / 2) ** 2
	)
	return 6371000 * 2 * math.asin(math.sqrt(a))


def resolve_checkin_location(employee, latitude, longitude):
	if not employee or latitude in (None, "") or longitude in (None, ""):
		return None

	locations = frappe.db.sql(
		"""
		select
			location.name,
			location.location_name,
			location.latitude,
			location.longitude,
			coalesce(assignment.radius_meter, location.default_radius_meter, 100) as radius_meter
		from `tabJEW Employee Location Assignment` assignment
		inner join `tabJEW Attendance Location` location on location.name = assignment.location
		where assignment.employee = %(employee)s
			and assignment.is_active = 1
			and location.is_active = 1
		""",
		{"employee": employee},
		as_dict=True,
	)
	nearest = None
	for location in locations:
		if location.latitude in (None, "") or location.longitude in (None, ""):
			continue
		distance = _haversine_m(latitude, longitude, location.latitude, location.longitude)
		candidate = {
			"name": location.name,
			"location_name": location.location_name or location.name,
			"distance_meter": round(distance, 2),
			"radius_meter": float(location.radius_meter or 100),
		}
		if nearest is None or candidate["distance_meter"] < nearest["distance_meter"]:
			nearest = candidate

	if nearest and nearest["distance_meter"] <= nearest["radius_meter"]:
		return nearest
	return None


def populate_employee_checkin_fields(doc, method=None):
	if not doc.get("employee"):
		return
	if doc.meta.has_field(EMPLOYEE_BRANCH_FIELD):
		doc.set(EMPLOYEE_BRANCH_FIELD, frappe.db.get_value("Employee", doc.employee, "branch"))
	if doc.meta.has_field(EMPLOYEE_CHECKIN_LOCATION_FIELD):
		location = resolve_checkin_location(doc.employee, doc.get("latitude"), doc.get("longitude"))
		if location:
			doc.set(EMPLOYEE_CHECKIN_LOCATION_FIELD, location["location_name"])


def get_attendance_locations(employee, attendance_date):
	if not employee or not attendance_date:
		return {"in_location": None, "out_location": None, "attendance_location": None}
	day_start = get_datetime(f"{getdate(attendance_date)} 00:00:00")
	day_end = add_days(day_start, 1)
	checkins = frappe.get_all(
		"Employee Checkin",
		filters=[
			["Employee Checkin", "employee", "=", employee],
			["Employee Checkin", "time", ">=", day_start],
			["Employee Checkin", "time", "<", day_end],
			["Employee Checkin", "docstatus", "<", 2],
		],
		fields=["log_type", "time", EMPLOYEE_CHECKIN_LOCATION_FIELD],
		order_by="time asc",
		limit_page_length=0,
	)
	in_location = next(
		(row.get(EMPLOYEE_CHECKIN_LOCATION_FIELD) for row in checkins if row.log_type == "IN" and row.get(EMPLOYEE_CHECKIN_LOCATION_FIELD)),
		None,
	)
	out_location = next(
		(row.get(EMPLOYEE_CHECKIN_LOCATION_FIELD) for row in reversed(checkins) if row.log_type == "OUT" and row.get(EMPLOYEE_CHECKIN_LOCATION_FIELD)),
		None,
	)
	if in_location and out_location and in_location != out_location:
		attendance_location = f"IN: {in_location} | OUT: {out_location}"
	else:
		attendance_location = out_location or in_location
	return {
		"in_location": in_location,
		"out_location": out_location,
		"attendance_location": attendance_location,
	}


def populate_attendance_fields(doc, method=None):
	if not doc.get("employee"):
		return
	if doc.meta.has_field(EMPLOYEE_BRANCH_FIELD):
		doc.set(EMPLOYEE_BRANCH_FIELD, frappe.db.get_value("Employee", doc.employee, "branch"))
	locations = get_attendance_locations(doc.employee, doc.get("attendance_date"))
	if doc.meta.has_field(EMPLOYEE_CHECKIN_LOCATION_FIELD):
		doc.set(EMPLOYEE_CHECKIN_LOCATION_FIELD, locations["attendance_location"])
	if doc.meta.has_field(ATTENDANCE_IN_LOCATION_FIELD):
		doc.set(ATTENDANCE_IN_LOCATION_FIELD, locations["in_location"])
	if doc.meta.has_field(ATTENDANCE_OUT_LOCATION_FIELD):
		doc.set(ATTENDANCE_OUT_LOCATION_FIELD, locations["out_location"])


def self_test_tracking_hooks(checkin_name):
	source = frappe.get_doc("Employee Checkin", checkin_name)
	checkin = frappe.new_doc("Employee Checkin")
	checkin.employee = source.employee
	checkin.employee_name = source.employee_name
	checkin.log_type = source.log_type
	checkin.time = source.time
	checkin.device_id = source.device_id
	checkin.latitude = source.latitude
	checkin.longitude = source.longitude
	checkin.run_method("before_validate")

	attendance = frappe.new_doc("Attendance")
	attendance.employee = source.employee
	attendance.attendance_date = getdate(source.time)
	attendance.run_method("before_validate")
	return {
		"employee_checkin": {
			"log_type": checkin.log_type,
			"attendance_location": checkin.get(EMPLOYEE_CHECKIN_LOCATION_FIELD),
			"employee_branch": checkin.get(EMPLOYEE_BRANCH_FIELD),
		},
		"attendance": {
			"attendance_location": attendance.get(EMPLOYEE_CHECKIN_LOCATION_FIELD),
			"in_location": attendance.get(ATTENDANCE_IN_LOCATION_FIELD),
			"out_location": attendance.get(ATTENDANCE_OUT_LOCATION_FIELD),
			"employee_branch": attendance.get(EMPLOYEE_BRANCH_FIELD),
		},
	}


@frappe.whitelist()
def backfill_tracking_fields(from_date=None, to_date=None):
	ensure_custom_fields()
	to_date = getdate(to_date or today())
	from_date = getdate(from_date) if from_date else to_date.replace(day=1)
	day_start = get_datetime(f"{from_date} 00:00:00")
	day_end = add_days(get_datetime(f"{to_date} 00:00:00"), 1)

	checkin_updated = 0
	checkins = frappe.get_all(
		"Employee Checkin",
		filters=[
			["Employee Checkin", "time", ">=", day_start],
			["Employee Checkin", "time", "<", day_end],
			["Employee Checkin", "docstatus", "<", 2],
		],
		fields=["name"],
		limit_page_length=0,
	)
	for row in checkins:
		doc = frappe.get_doc("Employee Checkin", row.name)
		populate_employee_checkin_fields(doc)
		values = {
			EMPLOYEE_CHECKIN_LOCATION_FIELD: doc.get(EMPLOYEE_CHECKIN_LOCATION_FIELD),
			EMPLOYEE_BRANCH_FIELD: doc.get(EMPLOYEE_BRANCH_FIELD),
		}
		frappe.db.set_value("Employee Checkin", doc.name, values, update_modified=False)
		checkin_updated += 1

	attendance_updated = 0
	attendance_rows = frappe.get_all(
		"Attendance",
		filters=[
			["Attendance", "attendance_date", ">=", from_date],
			["Attendance", "attendance_date", "<=", to_date],
			["Attendance", "docstatus", "<", 2],
		],
		fields=["name"],
		limit_page_length=0,
	)
	for row in attendance_rows:
		doc = frappe.get_doc("Attendance", row.name)
		populate_attendance_fields(doc)
		values = {
			EMPLOYEE_BRANCH_FIELD: doc.get(EMPLOYEE_BRANCH_FIELD),
			EMPLOYEE_CHECKIN_LOCATION_FIELD: doc.get(EMPLOYEE_CHECKIN_LOCATION_FIELD),
			ATTENDANCE_IN_LOCATION_FIELD: doc.get(ATTENDANCE_IN_LOCATION_FIELD),
			ATTENDANCE_OUT_LOCATION_FIELD: doc.get(ATTENDANCE_OUT_LOCATION_FIELD),
		}
		frappe.db.set_value("Attendance", doc.name, values, update_modified=False)
		attendance_updated += 1

	frappe.db.commit()
	return {
		"from_date": str(from_date),
		"to_date": str(to_date),
		"checkins_updated": checkin_updated,
		"attendance_updated": attendance_updated,
	}
