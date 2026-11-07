import calendar

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, get_link_to_form, getdate

# Canonical leave types on this bench (full names; short codes CL/PL/SL are stale duplicates).
CL_TYPE = "Casual Leave"
PL_TYPE = "Privilege Leave"
LWP_TYPE = "Leave Without Pay"

# Marker stored in the auto-created Leave Application's `description` field so on_cancel
# can find and reverse exactly the ones this doc created (Leave Application has no free
# transaction_name-style link field the way Leave Ledger Entry does).
LWP_MARKER = "AUTO:LeaveDetails:{0}"

# Ledger transaction_type used for the deduction rows.
# HRMS `get_leaves_for_period` only counts consumption from three transaction types;
# of those, "Leave Encashment" is the only one that trusts the raw `leaves` value verbatim
# (the "Leave Application" branch recomputes days from the date range instead). Using it
# means a -1 / -0.5 entry reduces the balance by exactly that amount, half-days included.
# transaction_name carries this doc's name purely as a reversal marker.
TXN_TYPE = "Leave Encashment"

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
	lle.transaction_type = TXN_TYPE
	lle.transaction_name = doc.name
	lle.company = doc.company
	lle.leaves = leaves
	lle.from_date = on_date
	lle.to_date = on_date
	lle.is_lwp = cint(is_lwp)
	lle.flags.ignore_links = True  # transaction_name points at this Leave Details, not a real Leave Encashment
	lle.insert(ignore_permissions=True)
	lle.submit()
	return lle.name


def _create_lwp_leave_applications(doc):
	"""One Approved, submitted Leave Application per LWP-flagged day, tagged with a
	marker so on_cancel can find and cancel exactly these (and only these)."""
	created = []
	for r in doc.leaves:
		if not cint(r.lwp):
			continue
		la = frappe.new_doc("Leave Application")
		la.employee = doc.employee
		la.company = doc.company
		la.leave_type = LWP_TYPE
		la.from_date = r.date
		la.to_date = r.date
		la.half_day = cint(r.half_day)
		if cint(r.half_day):
			la.half_day_date = r.date
		la.posting_date = r.date
		la.status = "Approved"
		la.description = LWP_MARKER.format(doc.name)
		la.flags.ignore_permissions = True
		la.insert(ignore_permissions=True)
		la.submit()
		created.append(la.name)
	return created


def _cancel_lwp_leave_applications(doc):
	names = frappe.get_all(
		"Leave Application",
		filters={"employee": doc.employee, "description": LWP_MARKER.format(doc.name), "docstatus": 1},
		pluck="name",
	)
	for name in names:
		la = frappe.get_doc("Leave Application", name)
		la.cancel()
	return names


def _sync_salary_slip(doc, start, end):
	"""Create (or recompute, if still Draft) the employee's Salary Slip for this month so
	the LWP days just recorded actually reduce pay. A Submitted slip is left untouched —
	that's a financial document; HR must deliberately cancel & amend it."""
	existing = frappe.get_all(
		"Salary Slip",
		filters={"employee": doc.employee, "start_date": start, "end_date": end, "docstatus": ["!=", 2]},
		fields=["name", "docstatus"],
		limit=1,
	)
	if existing and existing[0].docstatus == 1:
		frappe.msgprint(
			_("Salary Slip {0} for this period is already Submitted. Leave was recorded, "
			  "but the slip was NOT changed — cancel and amend it if this month's pay needs correcting.")
			.format(get_link_to_form("Salary Slip", existing[0].name)),
			indicator="orange", alert=True,
		)
		return None

	try:
		currency = frappe.get_cached_value("Company", doc.company, "default_currency") or "INR"
		if existing:
			slip = frappe.get_doc("Salary Slip", existing[0].name)
			slip.get_emp_and_working_day_details()
			slip.save()
		else:
			slip = frappe.new_doc("Salary Slip")
			slip.employee = doc.employee
			slip.company = doc.company
			slip.currency = currency
			slip.posting_date = end
			slip.start_date = start
			slip.end_date = end
			slip.insert()

		# Informational only (Payment Days/pay are unaffected): CL/PL stay fully paid,
		# matching how this company's salary sheets already treat them.
		cl_pl = flt(doc.cl_days) + flt(doc.pl_days)
		present = flt(slip.total_working_days) - cl_pl - flt(doc.lwp_days)
		frappe.db.set_value("Salary Slip", slip.name, {
			"cl_pl_days": cl_pl,
			"present_days": present,
			"absent_days": flt(slip.total_working_days) - present,
		})
		return slip.name
	except Exception:
		frappe.log_error(title="Leave Details: Salary Slip sync failed", message=frappe.get_traceback())
		frappe.msgprint(
			_("Leave was recorded, but the Salary Slip for this period could not be created/updated "
			  "automatically ({0}). Check the Error Log and prepare it manually.").format(doc.employee),
			indicator="orange", alert=True,
		)
		return None


def _sync_leave_display_fields(doc, start, end):
	"""No LWP this month, so pay is unaffected - but if a Draft Salary Slip already
	exists for this period, refresh its Present/CL+PL Days (display only)."""
	slip = frappe.get_all(
		"Salary Slip",
		filters={"employee": doc.employee, "start_date": start, "end_date": end, "docstatus": 0},
		fields=["name", "total_working_days"],
		limit=1,
	)
	if not slip:
		return
	cl_pl = flt(doc.cl_days) + flt(doc.pl_days)
	present = flt(slip[0].total_working_days) - cl_pl
	frappe.db.set_value("Salary Slip", slip[0].name, {
		"cl_pl_days": cl_pl,
		"present_days": present,
		"absent_days": flt(slip[0].total_working_days) - present,
	})


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
		                  filters={"transaction_type": TXN_TYPE, "transaction_name": self.name, "docstatus": 1},
		                  limit=1):
			return
		start, end = _month_bounds(self.month, self.year)
		need_cl = sum((0.5 if cint(r.half_day) else 1) for r in self.leaves if cint(r.cl))
		need_pl = sum((0.5 if cint(r.half_day) else 1) for r in self.leaves if cint(r.pl))
		need_lwp = sum((0.5 if cint(r.half_day) else 1) for r in self.leaves if cint(r.lwp))
		if need_cl and _balance(self.employee, CL_TYPE, end) < need_cl:
			frappe.throw(_("Not enough Casual Leave balance: {0} day(s) needed, {1} available.").format(need_cl, _balance(self.employee, CL_TYPE, end)))
		if need_pl and _balance(self.employee, PL_TYPE, end) < need_pl:
			frappe.throw(_("Not enough Privilege Leave balance: {0} day(s) needed, {1} available.").format(need_pl, _balance(self.employee, PL_TYPE, end)))
		# Consume CL / PL from the balance (delta = one negative ledger entry per day).
		for r in self.leaves:
			qty = 0.5 if cint(r.half_day) else 1
			if cint(r.cl):
				_make_lle(self, r.date, CL_TYPE, -qty, is_lwp=0)
			elif cint(r.pl):
				_make_lle(self, r.date, PL_TYPE, -qty, is_lwp=0)
		self.db_set("cl_days", need_cl, update_modified=False)
		self.db_set("pl_days", need_pl, update_modified=False)
		self.db_set("lwp_days", need_lwp, update_modified=False)

		# LWP days: booked as real (Approved, submitted) Leave Applications so Frappe's
		# own payroll engine (Payroll based on = Leave) picks them up automatically when
		# the Salary Slip is built/rebuilt - no manual salary editing needed afterward.
		if need_lwp:
			_create_lwp_leave_applications(self)
			_sync_salary_slip(self, start, end)
		else:
			# No LWP this month, so pay itself doesn't change - but if a Salary Slip already
			# exists (still Draft) for this period, keep its Present/CL+PL display in sync.
			_sync_leave_display_fields(self, start, end)

	def on_cancel(self):
		_require_roles()
		# Reverse the deduction: drop every ledger entry this doc created so the
		# CL/PL balance recomputes to its pre-submit value.
		frappe.db.delete("Leave Ledger Entry",
		                 {"transaction_type": TXN_TYPE, "transaction_name": self.name})
		_cancel_lwp_leave_applications(self)
		self.db_set("cl_days", 0, update_modified=False)
		self.db_set("pl_days", 0, update_modified=False)
		self.db_set("lwp_days", 0, update_modified=False)
		start, end = _month_bounds(self.month, self.year)
		slip = frappe.get_all("Salary Slip",
		                      filters={"employee": self.employee, "start_date": start, "end_date": end, "docstatus": 0},
		                      limit=1)
		if slip:
			frappe.msgprint(
				_("Leave Details cancelled. Salary Slip {0} (still Draft) was NOT recalculated automatically - "
				  "reopen it and click 'Get Emp & Working Days Details' if this month's pay needs to reflect the reversal.")
				.format(get_link_to_form("Salary Slip", slip[0].name)),
				indicator="orange", alert=True,
			)


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
