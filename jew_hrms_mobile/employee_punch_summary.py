from __future__ import annotations

import math

import frappe
from frappe import _
from frappe.desk.utils import provide_binary_file
from frappe.query_builder import functions as fn
from frappe.utils import add_days, cint, getdate
from frappe.utils.xlsxutils import make_xlsx


DOCTYPE = "Employee Checkin"
ALLOWED_PAGE_LENGTHS = {25, 50, 100}
SORT_FIELDS = {
	"employee": "employee",
	"employee_name": "employee_name",
	"in_count": "in_count",
	"out_count": "out_count",
	"total_punches": "total_punches",
	"punch_days": "punch_days",
}


def _check_permission() -> None:
	if not frappe.has_permission(DOCTYPE, "read"):
		frappe.throw(_("You do not have permission to read Employee Checkin."), frappe.PermissionError)


def _validate_dates(from_date: str, to_date: str):
	if not from_date or not to_date:
		frappe.throw(_("From Date and To Date are required."))

	start_date = getdate(from_date)
	end_date = getdate(to_date)
	if start_date > end_date:
		frappe.throw(_("From Date cannot be after To Date."))

	return start_date, end_date


def _query_filters(from_date, to_date, employee: str | None):
	filters = [
		["time", ">=", f"{from_date} 00:00:00"],
		["time", "<", f"{add_days(to_date, 1)} 00:00:00"],
		["log_type", "in", ["IN", "OUT"]],
	]
	if employee:
		filters.append(["employee", "=", employee.strip()])
	return filters


def _search_filters(search: str | None):
	term = (search or "").strip()
	if not term:
		return None
	pattern = f"%{term}%"
	return [["employee", "like", pattern], ["employee_name", "like", pattern]]


def _summary_rows(
	from_date: str,
	to_date: str,
	employee: str | None = None,
	search: str | None = None,
	sort_by: str = "employee_name",
	sort_order: str = "asc",
) -> list[dict]:
	start_date, end_date = _validate_dates(from_date, to_date)
	filters = _query_filters(start_date, end_date, employee)
	or_filters = _search_filters(search)

	checkin = frappe.qb.DocType(DOCTYPE)
	count_query = frappe.qb.get_query(
		DOCTYPE,
		fields=[
			checkin.employee,
			checkin.log_type,
			fn.Max(checkin.employee_name).as_("employee_name"),
			fn.Count(checkin.name).as_("punch_count"),
		],
		filters=filters,
		or_filters=or_filters,
		ignore_permissions=False,
	).groupby(checkin.employee, checkin.log_type)

	day_query = frappe.qb.get_query(
		DOCTYPE,
		fields=[checkin.employee, fn.Date(checkin.time).as_("punch_date")],
		filters=filters,
		or_filters=or_filters,
		ignore_permissions=False,
	).groupby(checkin.employee, fn.Date(checkin.time))

	rows_by_employee: dict[str, dict] = {}
	for item in count_query.run(as_dict=True):
		row = rows_by_employee.setdefault(
			item.employee,
			{
				"employee": item.employee,
				"employee_name": item.employee_name or item.employee,
				"in_count": 0,
				"out_count": 0,
				"total_punches": 0,
				"punch_days": 0,
			},
		)
		if item.employee_name:
			row["employee_name"] = item.employee_name
		if item.log_type == "IN":
			row["in_count"] = cint(item.punch_count)
		elif item.log_type == "OUT":
			row["out_count"] = cint(item.punch_count)

	for item in day_query.run(as_dict=True):
		if item.employee in rows_by_employee:
			rows_by_employee[item.employee]["punch_days"] += 1

	rows = list(rows_by_employee.values())
	for row in rows:
		row["total_punches"] = row["in_count"] + row["out_count"]
		row["punch_summary"] = f'{row["total_punches"]} punches across {row["punch_days"]} days'

	sort_key = SORT_FIELDS.get(sort_by, "employee_name")
	reverse = str(sort_order).lower() == "desc"
	rows.sort(key=lambda row: str(row["employee"]).casefold())
	rows.sort(
		key=lambda row: (
			str(row[sort_key]).casefold() if sort_key in {"employee", "employee_name"} else row[sort_key]
		),
		reverse=reverse,
	)
	return rows


def _totals(rows: list[dict]) -> dict:
	return {
		"total_employees": len(rows),
		"total_in": sum(row["in_count"] for row in rows),
		"total_out": sum(row["out_count"] for row in rows),
		"total_punches": sum(row["total_punches"] for row in rows),
	}


@frappe.whitelist()
def get_employee_punch_summary(
	from_date: str,
	to_date: str,
	employee: str | None = None,
	search: str | None = None,
	sort_by: str = "employee_name",
	sort_order: str = "asc",
	page: int = 1,
	page_length: int = 25,
):
	_check_permission()
	page = max(cint(page), 1)
	page_length = cint(page_length)
	if page_length not in ALLOWED_PAGE_LENGTHS:
		frappe.throw(_("Rows per page must be 25, 50, or 100."))
	if sort_by not in SORT_FIELDS:
		frappe.throw(_("Invalid sort field."))
	if str(sort_order).lower() not in {"asc", "desc"}:
		frappe.throw(_("Invalid sort order."))

	rows = _summary_rows(from_date, to_date, employee, search, sort_by, sort_order)
	total_rows = len(rows)
	total_pages = max(math.ceil(total_rows / page_length), 1)
	page = min(page, total_pages)
	start = (page - 1) * page_length

	return {
		"rows": rows[start : start + page_length],
		"totals": _totals(rows),
		"pagination": {
			"page": page,
			"page_length": page_length,
			"total_rows": total_rows,
			"total_pages": total_pages,
			"start": start + 1 if total_rows else 0,
			"end": min(start + page_length, total_rows),
		},
	}


@frappe.whitelist()
def download_employee_punch_summary(
	from_date: str,
	to_date: str,
	employee: str | None = None,
	search: str | None = None,
	sort_by: str = "employee_name",
	sort_order: str = "asc",
):
	_check_permission()
	if sort_by not in SORT_FIELDS:
		frappe.throw(_("Invalid sort field."))
	if str(sort_order).lower() not in {"asc", "desc"}:
		frappe.throw(_("Invalid sort order."))

	rows = _summary_rows(from_date, to_date, employee, search, sort_by, sort_order)
	data = [[
		_("S.No."),
		_("Employee ID"),
		_("Employee Name"),
		_("Check IN Count"),
		_("Check OUT Count"),
		_("Total Punches"),
		_("Punch Days"),
		_("Punch Summary"),
	]]
	for index, row in enumerate(rows, 1):
		data.append([
			index,
			row["employee"],
			row["employee_name"],
			row["in_count"],
			row["out_count"],
			row["total_punches"],
			row["punch_days"],
			row["punch_summary"],
		])

	filename = f"Employee Punch Summary {getdate(from_date)} to {getdate(to_date)}"
	xlsx_file = make_xlsx(
		data,
		"Employee Punch Summary",
		column_widths=[8, 18, 38, 18, 19, 16, 14, 38],
		header_index=0,
	)
	provide_binary_file(filename, "xlsx", xlsx_file.getvalue())
