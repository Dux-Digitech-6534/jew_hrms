import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	ensure_roles()
	ensure_leave_types()
	ensure_leave_custom_fields()
	ensure_default_shift_policy()


def ensure_roles():
	for role in ("JEW HRMS Employee", "JEW HRMS HR", "JEW HRMS Admin", "JEW HRMS Owner"):
		if not frappe.db.exists("Role", role):
			doc = frappe.new_doc("Role")
			doc.role_name = role
			doc.desk_access = 1
			doc.insert(ignore_permissions=True)


def ensure_leave_types():
	for name, label, is_lwp in (
		("CL", "Casual Leave", 0),
		("PL", "Privilege / Paid Leave", 0),
		("SL", "Sick Leave", 0),
		("LWP", "Leave Without Pay", 1),
	):
		doc = frappe.get_doc("Leave Type", name) if frappe.db.exists("Leave Type", name) else frappe.new_doc("Leave Type")
		if doc.is_new():
			doc.leave_type_name = name
		elif doc.get("leave_type_name") in (None, "", name):
			doc.leave_type_name = label
		if doc.meta.has_field("is_lwp"):
			doc.is_lwp = is_lwp
		if doc.meta.has_field("description") and not doc.get("description"):
			doc.description = label
		doc.save(ignore_permissions=True)


def ensure_leave_custom_fields():
	create_custom_fields({
		"Leave Application": [
			{
				"fieldname": "jew_hrms_approval_status",
				"label": "JEW HRMS Approval Status",
				"fieldtype": "Select",
				"options": "Draft\nPending HR Approval\nPending Admin Approval\nApproved\nRejected\nCancelled",
				"insert_after": "status",
				"default": "Draft",
			},
			{"fieldname": "hr_approved_by", "label": "HR Approved By", "fieldtype": "Link", "options": "User", "insert_after": "jew_hrms_approval_status"},
			{"fieldname": "hr_approved_on", "label": "HR Approved On", "fieldtype": "Datetime", "insert_after": "hr_approved_by"},
			{"fieldname": "final_approved_by", "label": "Final Approved By", "fieldtype": "Link", "options": "User", "insert_after": "hr_approved_on"},
			{"fieldname": "final_approved_on", "label": "Final Approved On", "fieldtype": "Datetime", "insert_after": "final_approved_by"},
			{"fieldname": "reject_reason", "label": "Reject Reason", "fieldtype": "Small Text", "insert_after": "final_approved_on"},
			{
				"fieldname": "custom_half_day_type",
				"label": "Half Day Type",
				"fieldtype": "Select",
				"options": "First Half\nSecond Half",
				"insert_after": "half_day_date",
			},
		]
	}, update=True)


def ensure_default_shift_policy():
	if not frappe.db.exists("DocType", "JEW Shift Attendance Policy"):
		return
	if frappe.db.exists("JEW Shift Attendance Policy", {"is_active": 1}):
		return
	doc = frappe.new_doc("JEW Shift Attendance Policy")
	doc.shift_name = "General"
	doc.shift_start_time = "09:30:00"
	doc.shift_end_time = "18:30:00"
	doc.break_minutes = 60
	doc.full_day_minimum_hours = 8
	doc.half_day_minimum_hours = 4
	doc.late_coming_grace_minutes = 10
	doc.early_going_grace_minutes = 10
	doc.max_late_coming_allowed_per_month = 0
	doc.max_early_going_allowed_per_month = 0
	doc.max_short_hours_allowed_per_month = 0
	doc.action_after_late_limit = "Regularization Required"
	doc.action_after_early_limit = "Regularization Required"
	doc.action_after_short_hours_limit = "Regularization Required"
	doc.is_active = 1
	doc.insert(ignore_permissions=True)
