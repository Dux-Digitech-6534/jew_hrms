# Copyright (c) 2026, JEWPL and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate

# Canonical leave types on this bench (see leave_details.py CL_TYPE/PL_TYPE - same
# names, kept as local constants here so this report has no import-time dependency
# on that doctype module).
CL_TYPE = "Casual Leave"
PL_TYPE = "Privilege Leave"

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

# Excel column -> Salary Component name, same 4 earning + 5 deduction columns the
# existing "Monthly Leave and Salary Summary" report already breaks out individually.
EARNING_COMPONENTS = ["Basic", "House Rent Allowance", "Special Allowance", "Tiffin Allowance"]
DEDUCTION_COMPONENTS = ["Provident Fund", "ESIC", "Professional Tax", "TDS", "Advance"]

EARNING_FIELD_MAP = {
	"Basic": "basic",
	"House Rent Allowance": "hra",
	"Special Allowance": "special_allowance",
	"Tiffin Allowance": "tiffin_allowance",
}
DEDUCTION_FIELD_MAP = {
	"Provident Fund": "pf",
	"ESIC": "esic",
	"Professional Tax": "p_tax",
	"TDS": "tds",
	"Advance": "advance",
}

# Attendance statuses that count as (fractional) presence for "Days Present".
# Half Day counts as 0.5 present - same weighting jew_hrms_mobile's own attendance/
# leave code already uses everywhere else (see LATE_EARLY_HALF_DAY_THRESHOLD /
# daily_wages_fraction_for_half_day precedent and leave_details.py's `0.5 if half_day`).
HALF_DAY_WEIGHT = 0.5


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are required."))
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
		{"label": _("Location"), "fieldname": "location", "fieldtype": "Link", "options": "Branch", "width": 90},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 200},
		{"label": _("Designation"), "fieldname": "designation", "fieldtype": "Data", "width": 130},
		{"label": _("Bank"), "fieldname": "bank", "fieldtype": "Data", "width": 100},
		{"label": _("Account Number"), "fieldname": "account_number", "fieldtype": "Data", "width": 140},
		{"label": _("IFSC"), "fieldname": "ifsc", "fieldtype": "Data", "width": 110},
		{"label": _("Days Present"), "fieldname": "days_present", "fieldtype": "Float", "width": 100},
		{"label": _("Working Days"), "fieldname": "working_days", "fieldtype": "Float", "width": 100},
		{"label": _("Days Paid"), "fieldname": "days_paid", "fieldtype": "Float", "width": 90},
		{"label": _("CL"), "fieldname": "cl_taken", "fieldtype": "Float", "width": 60},
		{"label": _("PL"), "fieldname": "pl_taken", "fieldtype": "Float", "width": 60},
		{"label": _("LWP Days"), "fieldname": "lwp_days", "fieldtype": "Float", "width": 90},
		{"label": _("Gross Salary"), "fieldname": "gross_salary", "fieldtype": "Currency", "width": 110},
		{"label": _("Basic Gross"), "fieldname": "basic_gross", "fieldtype": "Currency", "width": 100},
		{"label": _("Basic Earned"), "fieldname": "basic_earned", "fieldtype": "Currency", "width": 100},
		{"label": _("H.R.A Gross"), "fieldname": "hra_gross", "fieldtype": "Currency", "width": 100},
		{"label": _("H.R.A Earned"), "fieldname": "hra_earned", "fieldtype": "Currency", "width": 100},
		{"label": _("Special Allowance Gross"), "fieldname": "special_allowance_gross", "fieldtype": "Currency", "width": 130},
		{"label": _("Special Allowance Earned"), "fieldname": "special_allowance_earned", "fieldtype": "Currency", "width": 130},
		{"label": _("Tiffin Allowance Gross"), "fieldname": "tiffin_allowance_gross", "fieldtype": "Currency", "width": 130},
		{"label": _("Tiffin Allowance Earned"), "fieldname": "tiffin_allowance_earned", "fieldtype": "Currency", "width": 130},
		{"label": _("Total Salary"), "fieldname": "total_salary", "fieldtype": "Currency", "width": 110},
		{"label": _("P.F."), "fieldname": "pf", "fieldtype": "Currency", "width": 90},
		{"label": _("E.S.I.C"), "fieldname": "esic", "fieldtype": "Currency", "width": 90},
		{"label": _("P. Tax"), "fieldname": "p_tax", "fieldtype": "Currency", "width": 90},
		{"label": _("TDS"), "fieldname": "tds", "fieldtype": "Currency", "width": 90},
		{"label": _("Advance"), "fieldname": "advance", "fieldtype": "Currency", "width": 90},
		{"label": _("Total Deductions"), "fieldname": "total_deduction", "fieldtype": "Currency", "width": 120},
		{"label": _("Net Salary"), "fieldname": "net_pay", "fieldtype": "Currency", "width": 110},
		{"label": _("Remark"), "fieldname": "remark", "fieldtype": "Data", "width": 200},
		# Not in the source Excel layout, kept as extra HR-side context (mirrors how
		# the existing report keeps a few extra columns beyond the Excel's own set).
		{"label": _("Days Absent"), "fieldname": "days_absent", "fieldtype": "Float", "width": 90},
		{"label": _("Half Day Count"), "fieldname": "half_day_count", "fieldtype": "Float", "width": 100},
		{"label": _("Half Day (Late Coming)"), "fieldname": "late_half_days", "fieldtype": "Float", "width": 130},
		{"label": _("On Leave Days"), "fieldname": "on_leave_days", "fieldtype": "Float", "width": 100},
	]


def _waive_late_half_days(row):
	"""Report-view only: with the Pay Late-Coming Half Days filter ticked, treat each
	auto Late-Coming half day as a FULL paid day - add its unpaid 0.5 back into Days Paid /
	Days Present (capped by this employee LWP), re-prorate attendance-linked earnings
	(Basic / HRA / Special Allowance; Tiffin is always full) at the same per-day rate, and
	refresh Total Salary and Net. Never changes any stored Salary Slip."""
	late_hd = flt(row.get("late_half_days"))
	old_paid = flt(row.get("days_paid"))
	addback = min(0.5 * late_hd, flt(row.get("lwp_days")))
	if late_hd <= 0 or old_paid <= 0 or addback <= 0:
		return
	ratio = (old_paid + addback) / old_paid
	row["days_paid"] = old_paid + addback
	row["days_present"] = flt(row.get("days_present")) + addback
	row["lwp_days"] = flt(row.get("lwp_days")) - addback
	for f in ("basic_earned", "hra_earned", "special_allowance_earned"):
		row[f] = flt(row.get(f)) * ratio
	row["total_salary"] = (flt(row.get("basic_earned")) + flt(row.get("hra_earned"))
		+ flt(row.get("special_allowance_earned")) + flt(row.get("tiffin_allowance_earned")))
	row["net_pay"] = flt(row["total_salary"]) - flt(row.get("total_deduction"))


def get_data(filters):
	from_date = getdate(filters["from_date"])
	to_date = getdate(filters["to_date"])
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	total_days = date_diff(to_date, from_date) + 1

	company = filters.get("company")
	branch = filters.get("branch")
	pay_late_half = cint(filters.get("pay_late_half_days"))

	emp_filters = {"status": "Active"}
	if company:
		emp_filters["company"] = company
	if branch:
		emp_filters["branch"] = branch

	employees = frappe.get_all(
		"Employee",
		filters=emp_filters,
		fields=[
			"name", "employee_name", "branch", "company", "designation",
			"bank_name", "bank_ac_no", "ifsc_code", "holiday_list",
		],
	)
	emp_ids = [e.name for e in employees]
	if not emp_ids:
		return []

	# Scope Attendance / Leave Application / Salary Structure Assignment lookups to
	# THESE employees (already correctly filtered by their Employee-master company
	# and branch above) rather than re-filtering those child queries by the
	# `company` field stored on each individual Attendance/Leave row. That
	# denormalized field can be wrong/stale on a transactional row even though the
	# Employee master is correct - e.g. a couple of Attendance rows created by an
	# automated marking run can end up tagged with a different company than the
	# employee's real one for a given day. Filtering by employee id instead of by
	# that row-level company avoids silently dropping otherwise-valid rows.
	att_by_emp = _get_attendance_summary(from_date, to_date, emp_ids)
	leave_by_emp = _get_leave_summary(from_date, to_date, emp_ids)
	ssa_emps = _get_ssa_employees(to_date, emp_ids)
	late_half_day_by_emp = _get_late_half_day_counts(from_date, to_date, emp_ids)

	holiday_list_cache = {}
	data = []
	for emp in employees:
		# If a Salary Slip already exists for this employee/period, it is the single
		# source of truth from here on - HR may have hand-corrected its numbers
		# (e.g. a real Additional Salary deduction that shouldn't apply, a Salary
		# Structure component that shouldn't count) after this report first
		# generated them, and this report's own live re-simulation must never
		# silently diverge from that correction. Without this, this report and
		# "Monthly Leave and Salary Summary" (which is Salary-Slip-based) could show
		# two different numbers for the same employee/period indefinitely.
		existing_slip_row = _row_from_existing_salary_slip(emp, from_date, to_date)
		if existing_slip_row is not None:
			existing_slip_row["late_half_days"] = late_half_day_by_emp.get(emp.name, 0)
			if pay_late_half:
				_waive_late_half_days(existing_slip_row)
			data.append(existing_slip_row)
			continue

		if emp.name not in att_by_emp and emp.name not in ssa_emps:
			# Scope: only employees with SOME real activity (attendance or an active
			# salary structure assignment) in the period - not every Active employee
			# that has ever existed.
			continue

		# Working days for THIS employee = total calendar days in the range, minus
		# every day that is a holiday/weekly-off per their applicable Holiday List -
		# full stop, regardless of whether a stray Attendance/Leave row happens to
		# exist on that date (a marking-run mistake stamping a Sunday, say). Two
		# employees on the same Holiday List must get the same Working Days; making
		# it depend on incidental attendance markings (as an earlier version of this
		# report did) let it drift employee-to-employee for no real reason. Holiday
		# dates are correspondingly excluded from the Present/Absent/Half Day/On
		# Leave/CL/PL counts below too, so nothing is double-counted.
		emp_holidays = _get_employee_holidays_in_range(
			emp.name, emp.company, emp.holiday_list, from_date, to_date, holiday_list_cache
		)
		days = compute_period_days(
			emp_holidays, att_by_emp.get(emp.name, []), leave_by_emp.get(emp.name, []),
			from_date, to_date, total_days,
		)
		# birthday half-day policy (no-op unless the employee has a birthday credit to use)
		from jew_hrms_mobile.birthday_policy import apply_birthday_credit
		days = apply_birthday_credit(days, emp.name, from_date, to_date)
		working_days = days["working_days"]
		present = days["present"]
		absent = days["absent"]
		half_day_count = days["half_day_count"]
		on_leave_days = days["on_leave_days"]
		cl_taken = days["cl_taken"]
		pl_taken = days["pl_taken"]
		days_paid = days["days_paid"]
		lwp_days = days["lwp_days"]

		row = {
			"employee_name": emp.employee_name,
			"location": emp.branch,
			"company": emp.company,
			"designation": emp.designation,
			"bank": emp.bank_name,
			"account_number": emp.bank_ac_no,
			"ifsc": emp.ifsc_code,
			"days_present": present,
			"days_absent": absent,
			"half_day_count": half_day_count,
			"on_leave_days": on_leave_days,
			"late_half_days": late_half_day_by_emp.get(emp.name, 0),
			"working_days": working_days,
			"days_paid": days_paid,
			"cl_taken": cl_taken,
			"pl_taken": pl_taken,
			"lwp_days": lwp_days,
			"remark": "",
		}

		if days.get("birthday_credit"):
			row["remark"] = _("Birthday half-day credit: {0} day paid").format(days["birthday_credit"])

		# The in-memory Salary Slip simulation runs HRMS's own Salary Slip engine, which
		# queues client-side popups via frappe.msgprint/frappe.throw for things this
		# report already surfaces itself via the "remark" column (missing Salary
		# Structure, missing Date of Joining) or that are pure internal-implementation
		# noise for a throwaway simulated doc (e.g. "Added tax components from the
		# Salary Component master..."). None of that should leak to the user as a
		# popup - across 20-30 employees per run it would mean a wall of dialogs.
		# Truncate back to the pre-call length in `finally` so this is suppressed
		# whether the simulation succeeds or raises.
		message_log_len = len(frappe.local.message_log)
		try:
			sim = _simulate_gross_components(emp.name, emp.company, from_date, to_date)
		except Exception:
			frappe.log_error(
				title="Attendance Based Salary Sheet: salary simulation failed",
				message=frappe.get_traceback(),
			)
			sim = None
		finally:
			del frappe.local.message_log[message_log_len:]

		if sim is None:
			row["remark"] = _("No active Salary Structure found for this period")

		earnings = sim["earnings"] if sim else {}
		deductions = sim["deductions"] if sim else {}

		gross_salary = 0.0
		total_salary = 0.0
		for component in EARNING_COMPONENTS:
			field = EARNING_FIELD_MAP[component]
			gross_amt = flt(earnings.get(component, 0))
			if component == "Tiffin Allowance":
				# Verified against real Salary Structure / historical Salary Slip data:
				# Tiffin Allowance is paid in full regardless of attendance in this
				# company's actual practice, even though the Salary Component master
				# has "depends_on_payment_days" ticked for it (which would otherwise
				# prorate it like Basic/HRA/Special Allowance).
				earned = gross_amt
			else:
				earned = (gross_amt * days_paid / working_days) if working_days else 0.0
			row[f"{field}_gross"] = gross_amt
			row[f"{field}_earned"] = earned
			gross_salary += gross_amt
			total_salary += earned
		row["gross_salary"] = gross_salary
		row["total_salary"] = total_salary

		total_deduction = 0.0
		for component in DEDUCTION_COMPONENTS:
			field = DEDUCTION_FIELD_MAP[component]
			amt = flt(deductions.get(component, 0))
			row[field] = amt
			total_deduction += amt
		row["total_deduction"] = total_deduction
		row["net_pay"] = total_salary - total_deduction

		if pay_late_half:
			_waive_late_half_days(row)
		data.append(row)

	data.sort(key=lambda r: (r["company"] or "", r["employee_name"] or ""))
	return data


def compute_period_days(emp_holidays, attendance_rows, leave_rows, from_date, to_date, total_days):
	"""The single, canonical Present/Absent/Half Day/CL/PL/LWP/Working-Days formula for
	one employee's period - shared by this report's live-simulation branch AND by
	jew_hrms_mobile.api's monthly Salary Slip auto-generation, so the two can never
	drift apart. attendance_rows/leave_rows are this employee's own slices of
	_get_attendance_summary()/_get_leave_summary()'s per-employee lists."""
	# Monthly-salary basis: the WHOLE calendar month is payable and weekly-offs /
	# holidays are PAID days, so Working Days = every day in the period (30/31),
	# never minus the Holiday List. Only real Absent/Half-Day/unpaid days reduce pay
	# (see days_paid below). Changed from the earlier "total_days - holidays" (26-day)
	# basis per company payroll policy - applies to every company/site using this formula.
	working_days = total_days

	present = 0.0
	absent = 0.0
	half_day_count = 0.0
	on_leave_days = 0.0
	for att_date, status in attendance_rows:
		if att_date in emp_holidays:
			continue
		if status == "Present":
			present += 1
		elif status == "Half Day":
			present += HALF_DAY_WEIGHT
			half_day_count += 1
		elif status == "Absent":
			absent += 1
		elif status == "On Leave":
			on_leave_days += 1

	cl_taken = 0.0
	pl_taken = 0.0
	for a in leave_rows:
		start = max(getdate(a.from_date), from_date)
		end = min(getdate(a.to_date), to_date)
		if start > end:
			continue
		days = 0.0
		d = start
		while d <= end:
			if d not in emp_holidays:
				days += 1
			d = add_days(d, 1)
		half_day_date = getdate(a.half_day_date) if a.half_day_date else None
		if cint(a.half_day) and half_day_date and start <= half_day_date <= end and half_day_date not in emp_holidays:
			days -= 0.5
		if a.leave_type == CL_TYPE:
			cl_taken += days
		else:
			pl_taken += days

	# Holidays / weekly-offs are paid in full on a monthly salary, so every Holiday
	# List day in the period counts toward days_paid (they were skipped in the Present/
	# CL/PL counts above, so this adds them exactly once). Clamp so data anomalies can
	# never pay >100%.
	holiday_paid_days = len(emp_holidays)
	days_paid = present + cl_taken + pl_taken + holiday_paid_days
	if days_paid > working_days:
		days_paid = working_days
	lwp_days = working_days - days_paid
	return {
		"working_days": working_days, "present": present, "absent": absent,
		"half_day_count": half_day_count, "on_leave_days": on_leave_days,
		"cl_taken": cl_taken, "pl_taken": pl_taken, "days_paid": days_paid, "lwp_days": lwp_days,
	}


def _row_from_existing_salary_slip(emp, from_date, to_date):
	"""If a Salary Slip already exists for (emp, from_date..to_date), build this
	report's row entirely from its stored fields instead of re-simulating from
	Attendance/Leave Application - see the call site for why. Returns None if no
	such slip exists, so the caller falls back to the normal live computation."""
	slip_name = frappe.db.exists(
		"Salary Slip",
		{"employee": emp.name, "start_date": from_date, "end_date": to_date, "docstatus": ["!=", 2]},
	)
	if not slip_name:
		return None

	slip = frappe.db.get_value(
		"Salary Slip", slip_name,
		["present_days", "total_working_days", "payment_days", "leave_without_pay", "absent_days",
		 "gross_pay", "total_deduction", "net_pay"],
		as_dict=True,
	)
	details = frappe.get_all(
		"Salary Detail",
		filters={"parent": slip_name, "parentfield": ["in", ["earnings", "deductions"]]},
		fields=["salary_component", "amount"],
	)
	detail_by_component = {d.salary_component: flt(d.amount) for d in details}

	ld = frappe.db.get_value(
		"Leave Details",
		{"employee": emp.name, "month": MONTHS[from_date.month - 1], "year": from_date.year},
		["cl_days", "pl_days"],
		as_dict=True,
	)

	row = {
		"employee_name": emp.employee_name,
		"location": emp.branch,
		"company": emp.company,
		"designation": emp.designation,
		"bank": emp.bank_name,
		"account_number": emp.bank_ac_no,
		"ifsc": emp.ifsc_code,
		"days_present": flt(slip.present_days),
		"days_absent": flt(slip.absent_days),
		"half_day_count": 0.0,
		"on_leave_days": 0.0,
		"working_days": flt(slip.total_working_days),
		"days_paid": flt(slip.payment_days),
		"cl_taken": flt(ld.cl_days) if ld else 0.0,
		"pl_taken": flt(ld.pl_days) if ld else 0.0,
		"lwp_days": flt(slip.leave_without_pay),
		"remark": "",
		"gross_salary": flt(slip.gross_pay),
		"total_salary": flt(slip.gross_pay),
		"total_deduction": flt(slip.total_deduction),
		"net_pay": flt(slip.net_pay),
	}
	for component in EARNING_COMPONENTS:
		field = EARNING_FIELD_MAP[component]
		amt = detail_by_component.get(component, 0)
		row[f"{field}_gross"] = amt
		row[f"{field}_earned"] = amt
	for component in DEDUCTION_COMPONENTS:
		row[DEDUCTION_FIELD_MAP[component]] = detail_by_component.get(component, 0)
	return row


def _get_attendance_summary(from_date, to_date, employees):
	"""Returns raw (date, status) rows grouped by employee, for the given employee
	ids only - deliberately NOT filtered by the `company` field stored on the
	Attendance row itself (see the comment in get_data for why that denormalized
	field can't be trusted as a filter here). Left un-aggregated (not summed into
	status counts) so get_data can drop any row that falls on that specific
	employee's holiday date before counting - see the Working Days comment there."""
	if not employees:
		return {}
	filters = {
		"attendance_date": ["between", [from_date, to_date]],
		"docstatus": 1,
		"employee": ["in", employees],
	}
	rows = frappe.get_all("Attendance", filters=filters, fields=["employee", "status", "attendance_date"])
	by_emp = {}
	for r in rows:
		by_emp.setdefault(r.employee, []).append((getdate(r.attendance_date), r.status))
	return by_emp


def _get_leave_summary(from_date, to_date, employees):
	"""Approved CL/PL Leave Applications overlapping [from_date, to_date], grouped by
	employee - the "final approval" check mirrors jew_hrms_mobile/api.py's own
	(status == Approved OR the custom jew_hrms_approval_status == Approved, to cover
	both legacy and current-workflow records). Left un-clamped and un-summed (not
	reduced to a day count here) so get_data can clamp each application to the
	period AND drop its holiday-overlapping days once that employee's holiday set
	is known - see the Working Days comment there.

	Scoped to the given employee ids (not the Leave Application's own `company`
	field) for the same reason as _get_attendance_summary."""
	if not employees:
		return {}
	filters = [
		["docstatus", "=", 1],
		["employee", "in", employees],
		["leave_type", "in", [CL_TYPE, PL_TYPE]],
		["from_date", "<=", to_date],
		["to_date", ">=", from_date],
	]
	apps = frappe.get_all(
		"Leave Application",
		filters=filters,
		fields=["employee", "leave_type", "from_date", "to_date", "half_day", "half_day_date", "status", "jew_hrms_approval_status"],
	)
	by_emp = {}
	for a in apps:
		if not (a.status == "Approved" or a.jew_hrms_approval_status == "Approved"):
			continue
		by_emp.setdefault(a.employee, []).append(a)
	return by_emp


def _get_late_half_day_counts(from_date, to_date, employees):
	"""Count, per employee, how many days in [from_date, to_date] were marked Half
	Day specifically by the auto "Late Coming" / "Late Arrival" rule (JEW Attendance
	Regularization, status Marked Half Day) - distinct from half_day_count above,
	which includes Half Days from ANY cause (Short Hours, HR manual decision, etc.)."""
	if not employees or not frappe.db.exists("DocType", "JEW Attendance Regularization"):
		return {}
	rows = frappe.get_all(
		"JEW Attendance Regularization",
		filters={
			"employee": ["in", employees],
			"attendance_date": ["between", [from_date, to_date]],
			"issue_type": ["in", ["Late Coming", "Late Arrival"]],
			"status": "Marked Half Day",
		},
		fields=["employee"],
	)
	counts = {}
	for r in rows:
		counts[r.employee] = counts.get(r.employee, 0) + 1
	return counts


def _get_ssa_employees(to_date, employees):
	if not employees:
		return set()
	filters = {"docstatus": 1, "from_date": ["<=", to_date], "employee": ["in", employees]}
	return set(frappe.get_all("Salary Structure Assignment", filters=filters, pluck="employee"))


def _get_employee_holidays_in_range(employee, company, employee_holiday_list, from_date, to_date, cache):
	"""Resolve this employee's applicable Holiday List - their own Employee.holiday_list,
	falling back to their Company's default_holiday_list - the same resolution
	erpnext.setup.doctype.employee.employee.get_holiday_list_for_employee uses, and
	the same employee-then-company fallback jew_hrms_mobile.api._company_holidays /
	_mark_absent_unmarked already rely on elsewhere in this app for "is this day a
	working day for this employee". Returns the set of that Holiday List's holiday
	dates (which, once "Get Weekly Off Dates" has been used on the Holiday List,
	already includes every weekly-off day - not just named/statutory holidays)
	that fall within [from_date, to_date].

	Cached per holiday_list name for the life of one report run (from_date/to_date
	are fixed for the whole call, so a single cache key per list is enough)."""
	holiday_list = employee_holiday_list or frappe.get_cached_value("Company", company, "default_holiday_list")
	if not holiday_list:
		return set()
	if holiday_list not in cache:
		dates = frappe.get_all(
			"Holiday",
			filters={"parent": holiday_list, "holiday_date": ["between", [from_date, to_date]]},
			pluck="holiday_date",
		)
		cache[holiday_list] = {getdate(d) for d in dates}
	return cache[holiday_list]


def _simulate_gross_components(employee, company, from_date, to_date):
	"""Build an UNSAVED, in-memory Salary Slip for (employee, from_date..to_date) and
	read back each earning/deduction component's full, undiluted amount as configured
	on the employee's applicable Salary Structure - exactly what a real Salary Slip
	would pull, evaluated via the same hrms Salary Slip controller methods a real
	Payroll Entry / Salary Slip uses (get_emp_and_working_day_details, pull_sal_struct,
	calculate_net_pay), just with payment_days forced equal to total_working_days so
	the components come back at their full ("Gross") configured value rather than
	proportioned by this employee's real attendance (this report applies its own
	attendance-derived proration afterwards, in Python, per the exact CL/PL/LWP-based
	formula HR uses - not ERPNext's own payment-days proration, which - confirmed
	against real historical Salary Slip data on this bench - would otherwise reduce
	even fixed statutory deductions like Professional Tax to 0 for an employee with
	no payment days, which is not how this company's payroll actually works).

	NEVER calls .insert()/.save()/.submit() - purely an in-memory calculation, zero
	DB side effects. Returns None if no active Salary Structure Assignment applies
	to this employee for this period.
	"""
	slip = frappe.new_doc("Salary Slip")
	slip.employee = employee
	slip.company = company
	slip.posting_date = to_date
	slip.start_date = from_date
	slip.end_date = to_date

	slip.get_emp_and_working_day_details()
	if not slip.salary_structure:
		return None

	total_days = date_diff(to_date, from_date) + 1
	slip.total_working_days = total_days
	slip.payment_days = total_days
	slip.leave_without_pay = 0
	slip.absent_days = 0

	slip.set_salary_structure_assignment()
	slip.calculate_net_pay(skip_tax_breakup_computation=True)

	earnings = {d.salary_component: flt(d.amount) for d in slip.earnings}
	deductions = {d.salary_component: flt(d.amount) for d in slip.deductions}
	return {"earnings": earnings, "deductions": deductions}
