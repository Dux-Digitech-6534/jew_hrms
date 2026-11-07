"""Birthday half-day policy - the company gives every employee a PAID HALF DAY for their birthday.

How it works (one credit of 0.5 day per birthday, expires at the end of the NEXT month):
  * The credit goes to the FIRST day on/after the birthday (within the birthday month and the month after)
    on which the employee takes leave or loses pay. So:
      - leave on the birthday itself      -> 0.5 day of that day is on the house,
      - came to work on the birthday      -> the credit is "banked" for the first leave / absence in the
                                             birthday month or the next month.
  * What "on the house" means depends on what that first day is:
      - an approved paid leave (CL / PL / Sick ...): 0.5 day is given back to the leave BALANCE (the Leave Ledger
        Entry of that application is reduced by 0.5) and the application is tagged
        (jew_birthday_credit_date / jew_birthday_credit_days),
      - an unpaid day (LWP leave, Absent, no attendance) or a Half Day attendance: payroll counts 0.5 more paid day
        (up to what was actually lost) - done in apply_birthday_credit(), called wherever the canonical
        compute_period_days() is used.
  * Employees whose DOB is missing or 1 January (a default value on this site) get nothing; birthdays before
    the policy start date (site_config jew_hrms_birthday_policy_start, default 2026-11-01) are not touched.

Switch: site_config jew_hrms_birthday_policy = "on" enables it for that site (default off - the bench is shared with a trial site).
Everything is best-effort: any error leaves leave balances and payroll exactly as they were.
"""
import frappe
from frappe.utils import add_days, add_months, flt, get_last_day, getdate, today

CREDIT_DAYS = 0.5
DEFAULT_POLICY_START = "2026-11-01"
# the leave types the payroll formula already pays (see attendance_based_salary_sheet.compute_period_days)
PAID_PAYROLL_LEAVE_TYPES = ("Casual Leave", "Privilege Leave")
TAG_DATE_FIELD = "jew_birthday_credit_date"
TAG_DAYS_FIELD = "jew_birthday_credit_days"


# ------------------------------------------------------------------ switches / small helpers
def enabled():
	return str(frappe.conf.get("jew_hrms_birthday_policy", "off")).strip().lower() == "on"


def policy_start(override=None):
	return getdate(override or frappe.conf.get("jew_hrms_birthday_policy_start") or DEFAULT_POLICY_START)


def _birthday_in_year(dob, year):
	try:
		return dob.replace(year=year)
	except ValueError:  # 29 February in a non-leap year
		return dob.replace(year=year, day=28)


def window_end(birthday):
	"""Last day the credit can be used: the end of the month AFTER the birthday month."""
	return get_last_day(add_months(birthday, 1))


def eligible_birthdays(info, from_date, to_date, start=None):
	"""Birthdays of this employee whose usage window [birthday, window_end] overlaps [from_date, to_date].
	info: dict-like with date_of_birth, date_of_joining, status, relieving_date."""
	dob = info.get("date_of_birth")
	if not dob:
		return []
	dob = getdate(dob)
	if dob.month == 1 and dob.day == 1:  # default DOB on this site - not a real birthday
		return []
	if (info.get("status") or "Active") != "Active":
		return []
	begin = policy_start(start)
	out = []
	for year in range(from_date.year - 1, to_date.year + 1):
		b = _birthday_in_year(dob, year)
		if b < begin or b > to_date or window_end(b) < from_date:
			continue
		if info.get("date_of_joining") and getdate(info["date_of_joining"]) > b:
			continue
		if info.get("relieving_date") and getdate(info["relieving_date"]) < b:
			continue
		out.append(b)
	return out


# ------------------------------------------------------------------ the pure rule (unit-tested)
def compute_events(first_day, window_last, last_att_day, holidays, att, leaves, nonlwp_types):
	"""Chronological list of days that could use the credit.
	  att:    {date: attendance status}
	  leaves: APPROVED leave applications [{name, leave_type, from_date, to_date, half_day, half_day_date}]
	  A "leave" event = a day covered by an approved NON-LWP leave (may lie in the future).
	  A "payroll" event = a working day (not after last_att_day, we do not know the future) on which pay is lost:
	  Absent / no attendance / Half Day, after counting approved Casual/Privilege leave. Holidays never count.
	Each event: {date, kind, amount (<= CREDIT_DAYS), leave}."""
	per_day = {}
	for a in leaves:
		d, end = max(getdate(a["from_date"]), first_day), min(getdate(a["to_date"]), window_last)
		while d <= end:
			if d not in holidays:
				half = a.get("half_day") and (not a.get("half_day_date") or getdate(a["half_day_date"]) == d)
				per_day.setdefault(d, []).append((a["leave_type"], 0.5 if half else 1.0, a["name"]))
			d = add_days(d, 1)
	events = []
	d = first_day
	while d <= window_last:
		if d not in holidays:
			items = per_day.get(d, [])
			nonlwp = [i for i in items if i[0] in nonlwp_types]
			if nonlwp:
				events.append({"date": d, "kind": "leave", "amount": min(CREDIT_DAYS, sum(i[1] for i in nonlwp)), "leave": nonlwp[0][2]})
			elif d <= last_att_day:
				paid_leave = min(1.0, sum(i[1] for i in items if i[0] in PAID_PAYROLL_LEAVE_TYPES))
				present = {"Present": 1.0, "Half Day": 0.5}.get(att.get(d), 0.0)
				loss = 1.0 - min(1.0, present + paid_leave)
				if loss > 1e-9:
					events.append({"date": d, "kind": "payroll", "amount": min(CREDIT_DAYS, loss), "leave": None})
		d = add_days(d, 1)
	return events


# ------------------------------------------------------------------ data access
def _emp_info(employee):
	return frappe.get_cached_value(
		"Employee", employee, ["date_of_birth", "date_of_joining", "status", "relieving_date", "company", "holiday_list"], as_dict=True
	) or {}


def _approved_leaves(employee, from_date, to_date):
	rows = frappe.get_all(
		"Leave Application",
		filters={"employee": employee, "docstatus": 1, "from_date": ["<=", to_date], "to_date": [">=", from_date]},
		fields=["name", "leave_type", "from_date", "to_date", "half_day", "half_day_date", "status", "jew_hrms_approval_status"],
	)
	return [r for r in rows if r.status == "Approved" or r.jew_hrms_approval_status == "Approved"]


def events_for(employee, birthday, info=None):
	"""Events inside this birthday's window for this employee (earliest first)."""
	from jew_hrms_mobile.jew_hrms_mobile.report.attendance_based_salary_sheet import attendance_based_salary_sheet as _abs

	info = info or _emp_info(employee)
	last = window_end(birthday)
	holidays = _abs._get_employee_holidays_in_range(employee, info.get("company"), info.get("holiday_list"), birthday, last, {})
	last_att = min(getdate(today()), last)
	att = {}
	if last_att >= birthday:
		att = {getdate(r.attendance_date): r.status for r in frappe.get_all(
			"Attendance", filters={"employee": employee, "docstatus": 1, "attendance_date": ["between", [birthday, last_att]]},
			fields=["attendance_date", "status"])}
	leaves = _approved_leaves(employee, birthday, last)
	# only the leave types payroll actually pays (Casual/Privilege) can take the credit as a balance refund;
	# Sick / Comp-off / LWP days are unpaid in payroll, so they take it as a paid day instead
	nonlwp = {t.name for t in frappe.get_all("Leave Type", filters={"is_lwp": 0}, fields=["name"])} & set(PAID_PAYROLL_LEAVE_TYPES)
	return compute_events(birthday, last, last_att, holidays, att, leaves, nonlwp)


def _tag_fields_exist():
	return frappe.get_meta("Leave Application").has_field(TAG_DATE_FIELD)


def tagged_leave_exists(employee, birthday):
	"""True when an (active) leave application already took this birthday's credit as a balance refund."""
	if not _tag_fields_exist():
		return False
	return bool(frappe.db.exists("Leave Application", {"employee": employee, "docstatus": 1, TAG_DATE_FIELD: birthday}))


# ------------------------------------------------------------------ payroll side
def payroll_credit(employee, from_date, to_date, start=None):
	"""(credit_days, details) to ADD to days_paid for this employee in [from_date, to_date]."""
	info = _emp_info(employee)
	total, details = 0.0, []
	for b in eligible_birthdays(info, getdate(from_date), getdate(to_date), start):
		if tagged_leave_exists(employee, b):
			continue  # the credit was already given as leave balance
		events = events_for(employee, b, info)
		first = events[0] if events else None
		if first and first["kind"] == "payroll" and getdate(from_date) <= first["date"] <= getdate(to_date):
			total += first["amount"]
			details.append({"birthday": str(b), "day": str(first["date"]), "amount": first["amount"]})
	return total, details


def apply_birthday_credit(days, employee, from_date, to_date):
	"""Add the birthday credit to the dict returned by compute_period_days(). Fail-safe: on any problem the
	original numbers are returned untouched."""
	try:
		if not enabled():
			return days
		info = _emp_info(employee)
		if not info.get("date_of_birth"):
			return days
		credit, details = payroll_credit(employee, from_date, to_date)
		credit = min(flt(credit), max(flt(days.get("lwp_days")), 0.0))
		if credit > 0:
			days = dict(days)
			days["days_paid"] = flt(days["days_paid"]) + credit
			days["lwp_days"] = flt(days["lwp_days"]) - credit
			days["birthday_credit"] = credit
		return days
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS birthday payroll credit failed ({0})".format(employee))
		return days


# ------------------------------------------------------------------ leave side (balance refund)
def on_leave_submit(doc, method=None):
	"""Leave Application on_submit hook (after the standard one). If this leave contains the first day that can use
	a birthday credit, give 0.5 day back to the leave balance and tag the application. Best-effort."""
	try:
		if not enabled() or not _tag_fields_exist() or doc.get(TAG_DAYS_FIELD):
			return
		if doc.leave_type not in PAID_PAYROLL_LEAVE_TYPES or frappe.db.get_value("Leave Type", doc.leave_type, "is_lwp"):
			return
		info = _emp_info(doc.employee)
		for b in eligible_birthdays(info, getdate(doc.from_date), getdate(doc.to_date)):
			if tagged_leave_exists(doc.employee, b):
				continue
			events = events_for(doc.employee, b, info)
			first = events[0] if events else None
			if not (first and first["kind"] == "leave" and first["leave"] == doc.name):
				continue
			rows = frappe.get_all(
				"Leave Ledger Entry",
				filters={"transaction_type": "Leave Application", "transaction_name": doc.name, "docstatus": 1},
				fields=["name", "leaves"], order_by="from_date asc, creation asc", limit=1,
			)
			if not rows or flt(rows[0].leaves) >= 0:
				return
			amount = min(first["amount"], -flt(rows[0].leaves))
			frappe.db.set_value("Leave Ledger Entry", rows[0].name, "leaves", flt(rows[0].leaves) + amount, update_modified=False)
			frappe.db.set_value("Leave Application", doc.name, {TAG_DATE_FIELD: b, TAG_DAYS_FIELD: amount}, update_modified=False)
			doc.add_comment("Info", "Birthday half-day credit: {0} day given back to the leave balance (birthday {1}).".format(amount, b.strftime("%d/%m/%Y")))
			return
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS birthday leave credit failed ({0})".format(getattr(doc, "name", "?")))


def ensure_custom_fields():
	"""One-off: the two read-only tag fields on Leave Application."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields({"Leave Application": [
		{"fieldname": TAG_DATE_FIELD, "label": "Birthday Credit For (Birthday)", "fieldtype": "Date", "read_only": 1, "insert_after": "description", "no_copy": 1},
		{"fieldname": TAG_DAYS_FIELD, "label": "Birthday Credit Given (days)", "fieldtype": "Float", "read_only": 1, "insert_after": TAG_DATE_FIELD, "no_copy": 1, "default": "0"},
	]}, update=True)
