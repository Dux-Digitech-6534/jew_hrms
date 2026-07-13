import calendar

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate

# Canonical leave types on this bench (full names; short codes CL/PL/SL are stale duplicates).
CL_TYPE = "Casual Leave"
PL_TYPE = "Privilege Leave"
# The employee-applied unpaid type that already books the whole leave as LWP.
LWP_BASELINE_TYPE = "Leave Request"

ALLOWED_ROLES = {"JEW HRMS Admin", "JEW HRMS HR", "JEW HRMS Owner"}
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def _require_roles():
	if frappe.session.user == "Administrator":
		return
	if not (ALLOWED_ROLES & set(frappe.get_roles())):
		frappe.throw(_("You are not permitted to use Leave Details."), frappe.PermissionError)


def _month_bounds(month, year):
	m = MONTHS.index(month) + 1
	year = cint(year)
	last = calendar.monthrange(year, m)[1]
	return getdate(f"{year}-{m:02d}-01"), getdate(f"{year}-{m:02d}-{last:02d}")


def _balance(employee, leave_type, as_on):
	fn = frappe.get_attr("hrms.hr.doctype.leave_application.leave_application.get_leave_balance_on")
	return flt(fn(employee, leave_type, getdate(as_on)) or 0)


def _allotted(employee, leave_type, start, end):
	rows = frappe.get_all(
		"Leave Allocation",
		filters={"employee": employee, "leave_type": leave_type, "docstatus": 1,
		         "from_date": ["<=", end], "to_date": [">=", start]},
		fields=["total_leaves_allocated"],
	)
	return flt(sum(flt(r.total_leaves_allocated) for r in rows))


def _make_lle(doc, on_date, leave_type, leaves, is_lwp=0):
	lle = frappe.new_doc("Leave Ledger Entry")
	lle.employee = doc.employee
	lle.employee_name = frappe.db.get_value("Employee", doc.employee, "employee_name")
	lle.leave_type = leave_type
	lle.transaction_type = "Leave Details"
	lle.transaction_name = doc.name
	lle.company = doc.company
	lle.leaves = leaves
	lle.from_date = on_date
	lle.to_date = on_date
	lle.is_lwp = cint(is_lwp)
	lle.insert(ignore_permissions=True)
	lle.submit()
	return lle.name


class LeaveDetails(Document):
	def validate(self):
		_require_roles()
		for row in self.leaves:
			ticked = [f for f in ("pl", "cl", "lwp") if cint(row.get(f))]
			if len(ticked) == 0:
				frappe.throw(_("Select PL, CL or LWP for every leave day (row {0}, {1}).").format(row.idx, row.date))
			if len(ticked) > 1:
				frappe.throw(_("Choose only ONE of PL / CL / LWP per day (row {0}, {1}).").format(row.idx, row.date))

	def on_submit(self):
		_require_roles()
		# idempotency guard: never create ledger entries twice for this doc
		if frappe.get_all("Leave Ledger Entry",
		                  filters={"transaction_type": "Leave Details", "transaction_name": self.name, "docstatus": 1},
		                  limit=1):
			return
		_, end = _month_bounds(self.month, self.year)
		need_cl = sum((0.5 if cint(r.half_day) else 1) for r in self.leaves if cint(r.cl))
		need_pl = sum((0.5 if cint(r.half_day) else 1) for r in self.leaves if cint(r.pl))
		if need_cl and _balance(self.employee, CL_TYPE, end) < need_cl:
			frappe.throw(_("Not enough Casual Leave balance: {0} day(s) needed, {1} available.").format(need_cl, _balance(self.employee, CL_TYPE, end)))
		if need_pl and _balance(self.employee, PL_TYPE, end) < need_pl:
			frappe.throw(_("Not enough Privilege Leave balance: {0} day(s) needed, {1} available.").format(need_pl, _balance(self.employee, PL_TYPE, end)))
		for r in self.leaves:
			qty = 0.5 if cint(r.half_day) else 1
			if cint(r.cl):
				_make_lle(self, r.date, CL_TYPE, -qty, is_lwp=0)
				_make_lle(self, r.date, LWP_BASELINE_TYPE, qty, is_lwp=1)   # remove this day from the LWP tally
			elif cint(r.pl):
				_make_lle(self, r.date, PL_TYPE, -qty, is_lwp=0)
				_make_lle(self, r.date, LWP_BASELINE_TYPE, qty, is_lwp=1)
			# lwp: no entry — the day stays unpaid via the original Leave Request

	def on_cancel(self):
		_require_roles()
		for name in frappe.get_all("Leave Ledger Entry",
		                           filters={"transaction_type": "Leave Details", "transaction_name": self.name, "docstatus": 1},
		                           pluck="name"):
			frappe.get_doc("Leave Ledger Entry", name).cancel()


@frappe.whitelist()
def fetch_leave_details(company=None, month=None, year=None, employee=None):
	"""Balances + day-wise approved-leave rows for the selected employee/month/year."""
	_require_roles()
	if not (company and month and year and employee):
		return {}
	start, end = _month_bounds(month, year)
	result = {
		"allotted_cl": _allotted(employee, CL_TYPE, start, end),
		"allotted_pl": _allotted(employee, PL_TYPE, start, end),
		"remaining_cl": _balance(employee, CL_TYPE, end),
		"remaining_pl": _balance(employee, PL_TYPE, end),
		"leaves": [],
	}
	apps = frappe.get_all(
		"Leave Application",
		filters=[["employee", "=", employee], ["docstatus", "=", 1],
		         ["from_date", "<=", end], ["to_date", ">=", start]],
		fields=["name", "leave_type", "from_date", "to_date", "half_day", "half_day_date", "status"],
		order_by="from_date asc",
	)
	for a in apps:
		js = frappe.db.get_value("Leave Application", a.name, "jew_hrms_approval_status")
		if not (a.status == "Approved" or js == "Approved"):
			continue
		day = max(getdate(a.from_date), start)
		last = min(getdate(a.to_date), end)
		while day <= last:
			is_half = cint(a.half_day) and getdate(a.half_day_date or a.from_date) == day
			result["leaves"].append({
				"date": str(day),
				"leave_application": a.name,
				"leave_type": (a.leave_type or "") + (" (Half Day)" if is_half else ""),
				"half_day": 1 if is_half else 0,
			})
			day = add_days(day, 1)
	return result
