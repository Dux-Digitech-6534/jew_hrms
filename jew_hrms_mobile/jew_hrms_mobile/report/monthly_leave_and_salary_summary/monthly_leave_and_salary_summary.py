# Copyright (c) 2026, JEWPL and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from jew_hrms_mobile.jew_hrms_mobile.doctype.leave_details.leave_details import (
	CL_TYPE,
	PL_TYPE,
	_balance,
	_month_bounds,
)

# Excel column -> Salary Component name, for the 4 earning + 5 deduction columns
# the source "Indore Staff Salary Sheet" always broke out individually. Deduction
# components not actually used in any Salary Structure here (ESIC/TDS/Advance)
# still get their own column - they just show 0, matching the sheet's structure
# even where this org has nothing to deduct under that head.
EARNING_COMPONENTS = ["Basic", "House Rent Allowance", "Special Allowance", "Tiffin Allowance"]
DEDUCTION_COMPONENTS = ["Provident Fund", "ESIC", "Professional Tax", "TDS", "Advance"]


def execute(filters=None):
	filters = filters or {}
	data = get_data(filters)
	columns = get_columns()
	return columns, data


def get_columns():
	columns = [
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
		{"label": _("Location"), "fieldname": "location", "fieldtype": "Data", "width": 90},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 200},
		{"label": _("Designation"), "fieldname": "designation", "fieldtype": "Data", "width": 130},
		{"label": _("Bank"), "fieldname": "bank", "fieldtype": "Data", "width": 100},
		{"label": _("Account Number"), "fieldname": "account_number", "fieldtype": "Data", "width": 140},
		{"label": _("IFSC"), "fieldname": "ifsc", "fieldtype": "Data", "width": 110},
		{"label": _("Days Present"), "fieldname": "days_present", "fieldtype": "Float", "width": 100},
		{"label": _("Days Paid"), "fieldname": "days_paid", "fieldtype": "Float", "width": 90},
		{"label": _("CL"), "fieldname": "cl_taken", "fieldtype": "Float", "width": 60},
		{"label": _("PL"), "fieldname": "pl_taken", "fieldtype": "Float", "width": 60},
		{"label": _("LWP Days"), "fieldname": "lwp_days", "fieldtype": "Float", "width": 90},
		{"label": _("Absent Days"), "fieldname": "days_absent", "fieldtype": "Float", "width": 90},
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
		{"label": _("Remark"), "fieldname": "remark", "fieldtype": "Data", "width": 120},
		# Kept from the original version of this report - not in the source Excel,
		# but useful HR-side context that shouldn't be lost.
		{"label": _("Month"), "fieldname": "month", "fieldtype": "Data", "width": 90},
		{"label": _("Year"), "fieldname": "year", "fieldtype": "Data", "width": 70},
		{"label": _("CL Balance"), "fieldname": "cl_balance", "fieldtype": "Float", "width": 90},
		{"label": _("PL Balance"), "fieldname": "pl_balance", "fieldtype": "Float", "width": 90},
		{"label": _("Salary Slip"), "fieldname": "salary_slip", "fieldtype": "Link", "options": "Salary Slip", "width": 170},
		{"label": _("Slip Status"), "fieldname": "slip_status", "fieldtype": "Data", "width": 90},
	]
	return columns


MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def _canonical_days(employee, company, holiday_list, start, end, holiday_cache):
	from jew_hrms_mobile.jew_hrms_mobile.report.attendance_based_salary_sheet import (
		attendance_based_salary_sheet as _abs,
	)
	total_days = frappe.utils.date_diff(end, start) + 1
	emp_holidays = _abs._get_employee_holidays_in_range(employee, company, holiday_list, start, end, holiday_cache)
	att = _abs._get_attendance_summary(start, end, [employee]).get(employee, [])
	lv = _abs._get_leave_summary(start, end, [employee]).get(employee, [])
	days = _abs.compute_period_days(emp_holidays, att, lv, start, end, total_days)
	from jew_hrms_mobile.birthday_policy import apply_birthday_credit
	return apply_birthday_credit(days, employee, start, end)


def get_data(filters):
	from_date = frappe.utils.getdate(filters["from_date"]) if filters.get("from_date") else None
	to_date = frappe.utils.getdate(filters["to_date"]) if filters.get("to_date") else None

	common = {}
	if filters.get("company"):
		common["company"] = filters["company"]
	if filters.get("employee"):
		common["employee"] = filters["employee"]

	# Salary Slips are the primary source - they exist for every payroll period run so
	# far, whether or not HR has also filed a "Leave Details" for that month.
	slip_filters = dict(common, docstatus=["!=", 2])
	if from_date:
		slip_filters["end_date"] = [">=", from_date]
	if to_date:
		slip_filters["start_date"] = ["<=", to_date]
	slips = frappe.get_all(
		"Salary Slip",
		filters=slip_filters,
		fields=["name", "employee", "employee_name", "company", "start_date", "end_date",
		        "docstatus", "gross_pay", "total_deduction", "net_pay", "payment_days",
		        "present_days"],
	)
	slip_by_key = {(s.employee, s.start_date, s.end_date): s for s in slips}

	# Every earning/deduction row for these slips in one query, instead of one query
	# per slip - grouped below by (parent, salary_component).
	slip_names = [s.name for s in slips]
	detail_by_slip = {}
	if slip_names:
		details = frappe.get_all(
			"Salary Detail",
			filters={"parent": ["in", slip_names], "parentfield": ["in", ["earnings", "deductions"]]},
			fields=["parent", "salary_component", "amount", "default_amount"],
		)
		for d in details:
			detail_by_slip.setdefault(d.parent, {})[d.salary_component] = d

	# Leave Details: same (employee, period) keying, via the month-name/year it stores.
	ld_filters = dict(common, docstatus=1)
	leave_details = frappe.get_all(
		"Leave Details",
		filters=ld_filters,
		fields=["name", "company", "employee", "month", "year", "cl_days", "pl_days", "lwp_days"],
	)
	ld_by_key = {}
	for ld in leave_details:
		start, end = _month_bounds(ld.month, ld.year)
		if from_date and end < from_date:
			continue
		if to_date and start > to_date:
			continue
		ld_by_key[(ld.employee, start, end)] = ld

	keys = set(slip_by_key) | set(ld_by_key)

	employee_master = {}
	holiday_cache = {}
	data = []
	for employee, start, end in keys:
		s = slip_by_key.get((employee, start, end))
		ld = ld_by_key.get((employee, start, end))

		if employee not in employee_master:
			employee_master[employee] = frappe.db.get_value(
				"Employee", employee,
				["employee_name", "branch", "designation", "bank_name", "bank_ac_no", "ifsc_code", "company", "holiday_list"],
				as_dict=True,
			) or frappe._dict()

		emp = employee_master[employee]
		days_absent = 0.0
		if s:
			_cd = _canonical_days(employee, emp.get("company"), emp.get("holiday_list"), start, end, holiday_cache)
			days_absent = _cd["absent"]
		if ld:
			cl_taken = frappe.utils.flt(ld.cl_days)
			pl_taken = frappe.utils.flt(ld.pl_days)
			lwp_days = frappe.utils.flt(ld.lwp_days)
		elif s:
			cl_taken = _cd["cl_taken"]
			pl_taken = _cd["pl_taken"]
			lwp_days = _cd["lwp_days"]
		else:
			cl_taken = pl_taken = lwp_days = 0.0
		row = {
			"employee_name": emp.employee_name,
			"location": emp.branch,
			"company": (s.company if s else ld.company),
			"designation": emp.designation,
			"bank": emp.bank_name,
			"account_number": emp.bank_ac_no,
			"ifsc": emp.ifsc_code,
			"month": MONTHS[start.month - 1],
			"year": start.year,
			"cl_taken": cl_taken,
			"pl_taken": pl_taken,
			"lwp_days": lwp_days,
			"days_absent": days_absent,
			"cl_balance": _balance(employee, CL_TYPE, end),
			"pl_balance": _balance(employee, PL_TYPE, end),
			"remark": "",
		}

		components = detail_by_slip.get(s.name, {}) if s else {}
		gross_salary = 0
		earning_field_map = {
			"Basic": "basic", "House Rent Allowance": "hra",
			"Special Allowance": "special_allowance", "Tiffin Allowance": "tiffin_allowance",
		}
		for component in EARNING_COMPONENTS:
			field_prefix = earning_field_map[component]
			detail = components.get(component)
			gross = detail.default_amount if detail else 0
			earned = detail.amount if detail else 0
			row[f"{field_prefix}_gross"] = gross
			row[f"{field_prefix}_earned"] = earned
			gross_salary += gross or 0
		row["gross_salary"] = gross_salary

		deduction_field_map = {
			"Provident Fund": "pf", "ESIC": "esic", "Professional Tax": "p_tax",
			"TDS": "tds", "Advance": "advance",
		}
		for component in DEDUCTION_COMPONENTS:
			field_name = deduction_field_map[component]
			detail = components.get(component)
			row[field_name] = detail.amount if detail else 0

		if s:
			row["salary_slip"] = s.name
			row["slip_status"] = {0: _("Draft"), 1: _("Submitted")}.get(s.docstatus, "")
			row["days_present"] = s.present_days
			row["days_paid"] = s.payment_days
			row["total_salary"] = s.gross_pay
			row["total_deduction"] = s.total_deduction
			row["net_pay"] = s.net_pay
		else:
			row["salary_slip"] = None
			row["slip_status"] = _("No Slip")
			row["days_present"] = 0
			row["days_paid"] = 0
			row["total_salary"] = 0
			row["total_deduction"] = 0
			row["net_pay"] = 0

		data.append(row)

	data.sort(key=lambda r: (r["year"], MONTHS.index(r["month"]), r["employee_name"] or ""), reverse=True)
	return data
