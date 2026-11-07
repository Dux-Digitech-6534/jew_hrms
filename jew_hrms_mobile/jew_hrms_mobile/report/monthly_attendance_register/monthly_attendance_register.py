# Copyright (c) 2026, JEWPL and contributors
# For license information, please see license.txt

import calendar

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, today

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

# date.weekday(): Monday=0 ... Sunday=6
DAY_ABBR = ["M", "T", "W", "Th", "F", "St", "S"]
WEEKDAY_NAMES = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}

STATUS_CODE = {"Present": "P", "Work From Home": "WFH", "Half Day": "HD", "On Leave": "L", "Absent": "A"}

def execute(filters=None):
	filters = frappe._dict(filters or {})
	today_d = getdate(today())
	month = MONTHS.index(filters.month) + 1 if filters.get("month") in MONTHS else today_d.month
	year = cint(filters.get("year")) or today_d.year
	days_in_month = calendar.monthrange(year, month)[1]
	first = getdate("{0}-{1:02d}-01".format(year, month))
	last = getdate("{0}-{1:02d}-{2:02d}".format(year, month, days_in_month))
	return get_columns(first, days_in_month), get_data(filters, first, last, days_in_month, today_d)


def get_columns(first, days_in_month):
	columns = [
		{"label": _("Emp. Code"), "fieldname": "employee", "fieldtype": "Data", "width": 110},
		{"label": _("Emp. Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 170},
		{"label": "", "fieldname": "row_type", "fieldtype": "Data", "width": 70},
	]
	for n in range(1, days_in_month + 1):
		d = first.replace(day=n)
		columns.append({
			"label": "{0} {1}".format(n, DAY_ABBR[d.weekday()]),
			"fieldname": "d{0}".format(n),
			"fieldtype": "Data",
			"width": 62,
		})
	return columns


def _hhmm(dt):
	return dt.strftime("%H:%M") if dt else ""


def get_data(filters, first, last, days_in_month, today_d):
	emp_filters = {}
	if filters.get("company"):
		emp_filters["company"] = filters.company
	if filters.get("branch"):
		emp_filters["branch"] = filters.branch
	if filters.get("employee"):
		emp_filters["name"] = filters.employee
	employees = frappe.get_all(
		"Employee",
		filters=emp_filters,
		or_filters=[["status", "=", "Active"], ["relieving_date", ">=", first]],
		fields=["name", "employee_name", "company", "holiday_list", "date_of_joining", "relieving_date"],
		order_by="company asc, employee_name asc",
		limit_page_length=0,
	)
	if not employees:
		return []
	names = [e.name for e in employees]

	def holiday_list_for(emp):
		return emp.holiday_list or frappe.get_cached_value("Company", emp.company, "default_holiday_list")

	hl_names = {holiday_list_for(e) for e in employees} - {None}
	holidays = {}
	if hl_names:
		for h in frappe.get_all(
			"Holiday",
			filters={"parent": ["in", list(hl_names)], "holiday_date": ["between", [first, last]]},
			fields=["parent", "holiday_date", "weekly_off", "description"],
			limit_page_length=0,
		):
			weekly = cint(h.weekly_off) or (h.description or "").strip().lower() in WEEKDAY_NAMES
			holidays.setdefault(h.parent, {})[getdate(h.holiday_date)] = bool(weekly)

	att = {}
	for a in frappe.get_all(
		"Attendance",
		filters={"employee": ["in", names], "attendance_date": ["between", [first, last]], "docstatus": 1},
		fields=["employee", "attendance_date", "status", "late_entry", "in_time", "out_time", "working_hours"],
		limit_page_length=0,
	):
		att[(a.employee, getdate(a.attendance_date))] = a

	punches = {}
	for c in frappe.get_all(
		"Employee Checkin",
		filters={"employee": ["in", names], "time": ["between", ["{0} 00:00:00".format(first), "{0} 23:59:59".format(last)]]},
		fields=["employee", "log_type", "time"],
		order_by="time asc",
		limit_page_length=0,
	):
		t = get_datetime(c.time)
		slot = punches.setdefault((c.employee, t.date()), {"in": None, "out": None})
		if c.log_type == "IN" and slot["in"] is None:
			slot["in"] = t
		elif c.log_type == "OUT":
			slot["out"] = t

	data = []
	for emp in employees:
		hl = holidays.get(holiday_list_for(emp), {})
		joined = getdate(emp.date_of_joining) if emp.date_of_joining else None
		relieved = getdate(emp.relieving_date) if emp.relieving_date else None
		r_status = {"employee": emp.name, "employee_name": emp.employee_name, "row_type": "Status"}
		r_in = {"row_type": "InTime"}
		r_out = {"row_type": "OutTime"}
		r_total = {"row_type": "Total"}

		for n in range(1, days_in_month + 1):
			d = first.replace(day=n)
			if d > today_d or (joined and d < joined) or (relieved and d > relieved):
				continue
			fn = "d{0}".format(n)
			a = att.get((emp.name, d))
			p = punches.get((emp.name, d), {})
			in_t = p.get("in") or (get_datetime(a.in_time) if a and a.in_time else None)
			out_t = p.get("out") or (get_datetime(a.out_time) if a and a.out_time else None)
			is_hol = d in hl

			if a:
				status = STATUS_CODE.get(a.status, a.status)
				if is_hol and status == "P":
					status = "WOP"
			elif is_hol:
				status = "WOP" if in_t else ("WO" if hl.get(d) else "H")
			elif in_t or out_t:
				status = "PD"
			else:
				continue

			total = ""
			if in_t and out_t and out_t > in_t:
				secs = int((out_t - in_t).total_seconds())
				total = "{0:02d}:{1:02d}".format(secs // 3600, (secs % 3600) // 60)
			elif a and flt(a.working_hours) > 0:
				mins = int(round(flt(a.working_hours) * 60))
				total = "{0:02d}:{1:02d}".format(mins // 60, mins % 60)
			elif status in ("P", "WFH", "HD", "WOP", "A", "PD"):
				total = "00:00"

			r_status[fn] = status
			r_in[fn] = _hhmm(in_t)
			r_out[fn] = _hhmm(out_t)
			r_total[fn] = total

		data.extend([r_status, r_in, r_out, r_total])
	return data
