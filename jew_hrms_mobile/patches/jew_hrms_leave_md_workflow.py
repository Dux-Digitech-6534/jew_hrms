"""Patch: three-level Leave Application approval workflow hardening.

Closes gaps against the earlier ad-hoc console-script setup done this session:
- Adds "Pending MD Approval" + per-level rejection statuses to the existing
  jew_hrms_approval_status Select (old values kept for in-flight/historical docs).
- Adds approval-remarks fields (previously accepted by approve_leave but silently
  dropped) and rejection audit fields (rejected_by/rejection_level/rejection_date).
- Adds a computed "Current Approval Level" field.
- Relabels the existing owner_* fields to "MD ..." in the UI without renaming the
  underlying fieldname (same person/stage - avoids touching in-flight records).
- Adds HR Settings fields so the MD/HR approver is a configurable user, not a
  hardcoded email in api.py - seeded with the values api.py used to hardcode, so
  behavior is unchanged for existing users.
- Creates the 8 Email Templates the workflow now sends (see api.py's
  _send_leave_stage_email call sites for how these are rendered/used).
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


LEAVE_APPROVAL_STATUS_OPTIONS = (
	"Draft\nPending Department Head Approval\nPending Owner Approval\nPending MD Approval\n"
	"Pending HR Approval\nPending Admin Approval\nApproved\nRejected\n"
	"Rejected by Department Head\nRejected by MD\nRejected by HR\nCancelled"
)

CURRENT_MD_USER = "aditya.surana@jewonline.in"
CURRENT_HR_USER = "kavita.kochle@thesvsgroup.org"

EMAIL_TEMPLATES = [
	{
		"name": "Leave - Dept Head Approval Request",
		"subject": "Leave Application Pending Department Head Approval – {{ employee_name }}",
		"response": """<p>Dear {{ department_head_name }},</p>
<p>A leave application has been submitted and is awaiting your review and approval.</p>
<p>
Employee Name: {{ employee_name }}<br>
Employee ID: {{ employee_id }}<br>
Department: {{ department }}<br>
Leave Type: {{ leave_type }}<br>
Leave Period: {{ from_date }} to {{ to_date }}<br>
Total Leave Days: {{ total_leave_days }}<br>
Reason: {{ leave_reason }}<br>
Current Status: Pending Department Head Approval
</p>
{% if approve_link %}
<p style="margin: 20px 0;">
<a href="{{ approve_link }}" style="display:inline-block; padding:10px 24px; background:#1a7f45; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Approve</a>
<a href="{{ reject_link }}" style="display:inline-block; padding:10px 24px; background:#b3341f; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Reject</a>
<a href="{{ leave_application_link }}" style="display:inline-block; padding:10px 24px; background:#1a52b3; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600;">View Application</a>
</p>
{% endif %}
<p>Once approved, the application will be forwarded to the Managing Director.</p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Leave - MD Approval Request",
		"subject": "Leave Application Pending MD Approval – {{ employee_name }}",
		"response": """<p>Dear {{ md_name }},</p>
<p>The following leave application has been approved by the Department Head and is now awaiting your approval.</p>
<p>
Employee Name: {{ employee_name }}<br>
Employee ID: {{ employee_id }}<br>
Department: {{ department }}<br>
Leave Type: {{ leave_type }}<br>
Leave Period: {{ from_date }} to {{ to_date }}<br>
Total Leave Days: {{ total_leave_days }}<br>
Reason: {{ leave_reason }}<br>
Department Head: {{ department_head_name }}<br>
Department Head Approval Date: {{ department_head_approval_date }}<br>
Current Status: Pending MD Approval
</p>
{% if approve_link %}
<p style="margin: 20px 0;">
<a href="{{ approve_link }}" style="display:inline-block; padding:10px 24px; background:#1a7f45; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Approve</a>
<a href="{{ reject_link }}" style="display:inline-block; padding:10px 24px; background:#b3341f; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Reject</a>
<a href="{{ leave_application_link }}" style="display:inline-block; padding:10px 24px; background:#1a52b3; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600;">View Application</a>
</p>
{% endif %}
<p>Once approved, the application will be forwarded to HR for final approval.</p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Leave - HR Final Approval Request",
		"subject": "Leave Application Pending Final HR Approval – {{ employee_name }}",
		"response": """<p>Dear {{ hr_approver_name }},</p>
<p>The following leave application has been approved by the Department Head and Managing Director. It is now awaiting final HR approval.</p>
<p>
Employee Name: {{ employee_name }}<br>
Employee ID: {{ employee_id }}<br>
Department: {{ department }}<br>
Leave Type: {{ leave_type }}<br>
Leave Period: {{ from_date }} to {{ to_date }}<br>
Total Leave Days: {{ total_leave_days }}<br>
Reason: {{ leave_reason }}<br>
Department Head: Approved by {{ department_head_name }}<br>
Managing Director: Approved by {{ md_name }}<br>
Current Status: Pending HR Approval
</p>
{% if approve_link %}
<p style="margin: 20px 0;">
<a href="{{ approve_link }}" style="display:inline-block; padding:10px 24px; background:#1a7f45; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Approve</a>
<a href="{{ reject_link }}" style="display:inline-block; padding:10px 24px; background:#b3341f; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Reject</a>
<a href="{{ leave_application_link }}" style="display:inline-block; padding:10px 24px; background:#1a52b3; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600;">View Application</a>
</p>
{% endif %}
<p>After your approval, the Leave Application will be marked as finally approved.</p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Leave - Employee Submission Confirmation",
		"subject": "Your Leave Application Has Been Submitted – {{ leave_application_id }}",
		"response": """<p>Dear {{ employee_name }},</p>
<p>Your leave application has been submitted successfully and sent to your Department Head for the first level of approval.</p>
<p>
Leave Application ID: {{ leave_application_id }}<br>
Leave Type: {{ leave_type }}<br>
Leave Period: {{ from_date }} to {{ to_date }}<br>
Total Leave Days: {{ total_leave_days }}<br>
Current Status: Pending Department Head Approval
</p>
<p>You will receive further notifications as your application moves through the approval process.</p>
<p>View Application:<br>
<a href="{{ leave_application_link }}">{{ leave_application_link }}</a></p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Leave - Employee Notified After Dept Head Approval",
		"subject": "Your Leave Application Has Been Approved by the Department Head – {{ leave_application_id }}",
		"response": """<p>Dear {{ employee_name }},</p>
<p>Your leave application has been approved by your Department Head and forwarded to the Managing Director for the second level of approval.</p>
<p>
Leave Application ID: {{ leave_application_id }}<br>
Leave Type: {{ leave_type }}<br>
Leave Period: {{ from_date }} to {{ to_date }}<br>
Approved By: {{ department_head_name }}<br>
Current Status: Pending MD Approval
</p>
<p>View Application:<br>
<a href="{{ leave_application_link }}">{{ leave_application_link }}</a></p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Leave - Employee Notified After MD Approval",
		"subject": "Your Leave Application Has Been Approved by the Managing Director – {{ leave_application_id }}",
		"response": """<p>Dear {{ employee_name }},</p>
<p>Your leave application has been approved by the Managing Director and forwarded to HR for final approval.</p>
<p>
Leave Application ID: {{ leave_application_id }}<br>
Leave Type: {{ leave_type }}<br>
Leave Period: {{ from_date }} to {{ to_date }}<br>
Approved By: {{ md_name }}<br>
Current Status: Pending HR Approval
</p>
<p>View Application:<br>
<a href="{{ leave_application_link }}">{{ leave_application_link }}</a></p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Leave - Employee Final Approval",
		"subject": "Your Leave Application Has Been Finally Approved – {{ leave_application_id }}",
		"response": """<p>Dear {{ employee_name }},</p>
<p>Your leave application has completed all three approval levels and has been finally approved by HR.</p>
<p>
Leave Application ID: {{ leave_application_id }}<br>
Leave Type: {{ leave_type }}<br>
Leave Period: {{ from_date }} to {{ to_date }}<br>
Total Leave Days: {{ total_leave_days }}<br>
Final Status: Approved
</p>
<p>Approval Summary:<br>
Department Head: Approved by {{ department_head_name }}<br>
Managing Director: Approved by {{ md_name }}<br>
HR: Approved by {{ hr_approver_name }}</p>
<p>Please complete any necessary work handover before beginning your leave.</p>
<p>View Application:<br>
<a href="{{ leave_application_link }}">{{ leave_application_link }}</a></p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Leave - Employee Rejection",
		"subject": "Your Leave Application Has Been Rejected – {{ leave_application_id }}",
		"response": """<p>Dear {{ employee_name }},</p>
<p>Your leave application has been rejected at the {{ rejection_level }} approval stage.</p>
<p>
Leave Application ID: {{ leave_application_id }}<br>
Leave Type: {{ leave_type }}<br>
Leave Period: {{ from_date }} to {{ to_date }}<br>
Rejected By: {{ rejected_by_name }}<br>
Rejection Date: {{ rejection_date }}<br>
Reason: {{ rejection_reason }}<br>
Final Status: {{ rejection_status }}
</p>
<p>View Application:<br>
<a href="{{ leave_application_link }}">{{ leave_application_link }}</a></p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
]


def execute():
	ensure_leave_approval_fields()
	ensure_hr_settings_approver_fields()
	ensure_email_templates()
	ensure_employee_role_permission()


def ensure_employee_role_permission():
	"""JEW HRMS Employee needs ordinary Read/Write/Create on Leave Application so
	employees can apply for and view their own leave - approval itself is never
	gated by this role grant (approve_leave/reject_leave check the actual
	Department Head/MD/HR identity independently), and every approval-audit
	field above is read_only=1 so a Write grant here can't be used to edit them
	directly from the desk form."""
	import frappe as _frappe
	if _frappe.db.exists("Custom DocPerm", {"parent": "Leave Application", "role": "JEW HRMS Employee", "permlevel": 0}):
		return
	_frappe.get_doc({
		"doctype": "Custom DocPerm",
		"parent": "Leave Application", "parenttype": "DocType", "parentfield": "permissions",
		"role": "JEW HRMS Employee", "permlevel": 0,
		"read": 1, "write": 1, "create": 1, "delete": 0, "submit": 0, "cancel": 0, "amend": 0,
	}).insert(ignore_permissions=True)


def ensure_leave_approval_fields():
	create_custom_fields({
		"Leave Application": [
			{
				"fieldname": "jew_hrms_approval_status",
				"label": "JEW HRMS Approval Status",
				"fieldtype": "Select",
				"options": LEAVE_APPROVAL_STATUS_OPTIONS,
				"insert_after": "status",
				"default": "Draft",
				"read_only": 1,
			},
			{"fieldname": "dept_head_approval_remarks", "label": "Department Head Approval Remarks", "fieldtype": "Small Text", "insert_after": "reject_reason", "read_only": 1},
			{"fieldname": "md_approval_remarks", "label": "MD Approval Remarks", "fieldtype": "Small Text", "insert_after": "dept_head_approval_remarks", "read_only": 1},
			{"fieldname": "hr_approval_remarks", "label": "HR Approval Remarks", "fieldtype": "Small Text", "insert_after": "md_approval_remarks", "read_only": 1},
			{"fieldname": "rejected_by", "label": "Rejected By", "fieldtype": "Link", "options": "User", "insert_after": "hr_approval_remarks", "read_only": 1},
			{"fieldname": "rejection_level", "label": "Rejection Level", "fieldtype": "Select", "options": "\nDepartment Head\nMD\nHR", "insert_after": "rejected_by", "read_only": 1},
			{"fieldname": "rejection_date", "label": "Rejection Date", "fieldtype": "Datetime", "insert_after": "rejection_level", "read_only": 1},
			{
				"fieldname": "current_approval_level", "label": "Current Approval Level", "fieldtype": "Select",
				"options": "\nNot Started\nDepartment Head\nMD\nHR\nCompleted\nRejected\nCancelled", "insert_after": "rejection_date", "read_only": 1,
			},
			{
				# Rendered by the "Leave Application Stage Approval Buttons" Client
				# Script - a real field (not frm.dashboard.add_section, which always
				# renders near the top) so it can sit at the very bottom of the form,
				# after every other field, right before Comments/Activity.
				"fieldname": "approval_timeline_html", "label": "Approval Timeline", "fieldtype": "HTML",
				"insert_after": "current_approval_level", "read_only": 1,
			},
			# One-click email Approve/Reject support - a single-use, expiring,
			# stage-locked token per (leave, recipient). Regenerated every time a
			# new stage begins; cleared the moment it's used. See
			# _generate_email_action_token / the leave_email_action www page.
			{"fieldname": "email_action_token", "label": "Email Action Token", "fieldtype": "Data", "insert_after": "approval_timeline_html", "hidden": 1, "no_copy": 1, "read_only": 1},
			{"fieldname": "email_action_token_user", "label": "Email Action Token User", "fieldtype": "Data", "insert_after": "email_action_token", "hidden": 1, "no_copy": 1, "read_only": 1},
			{"fieldname": "email_action_token_stage", "label": "Email Action Token Stage", "fieldtype": "Data", "insert_after": "email_action_token_user", "hidden": 1, "no_copy": 1, "read_only": 1},
			{"fieldname": "email_action_token_expiry", "label": "Email Action Token Expiry", "fieldtype": "Datetime", "insert_after": "email_action_token_stage", "hidden": 1, "no_copy": 1, "read_only": 1},
			# Relabel only (fieldtype unchanged, insert_after omitted so existing
			# position is preserved) - "Owner" internally is the same identity as "MD".
			{"fieldname": "owner_stage_started_on", "label": "MD Stage Started On", "fieldtype": "Datetime"},
			{"fieldname": "owner_approved_by", "label": "MD Approver", "fieldtype": "Data"},
			{"fieldname": "owner_approved_on", "label": "MD Approval Date", "fieldtype": "Datetime"},
			{"fieldname": "owner_auto_approved", "label": "Auto-Approved (MD did not act within 3 days)", "fieldtype": "Check"},
		]
	}, update=True)


def ensure_hr_settings_approver_fields():
	create_custom_fields({
		"HR Settings": [
			{
				"fieldname": "custom_leave_md_user",
				"label": "Leave Approval - MD User",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "send_leave_notification",
				"description": "The user who acts as Managing Director (2nd-level / MD stage) approver in the Leave Application approval workflow. Resolved dynamically - not hardcoded.",
			},
			{
				"fieldname": "custom_leave_hr_user",
				"label": "Leave Approval - HR User",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "custom_leave_md_user",
				"description": "The user who gives final HR approval (3rd-level / HR stage) in the Leave Application approval workflow. Resolved dynamically - not hardcoded.",
			},
		]
	}, update=True)

	settings = frappe.get_cached_doc("HR Settings")
	changed = False
	# Seed with the values api.py used to hardcode (TRUE_OWNER_EMAIL/TRUE_HR_EMAIL)
	# so behavior is unchanged for existing users after this patch runs.
	if not settings.get("custom_leave_md_user") and frappe.db.exists("User", CURRENT_MD_USER):
		settings.custom_leave_md_user = CURRENT_MD_USER
		changed = True
	if not settings.get("custom_leave_hr_user") and frappe.db.exists("User", CURRENT_HR_USER):
		settings.custom_leave_hr_user = CURRENT_HR_USER
		changed = True
	if changed:
		settings.save(ignore_permissions=True)


def ensure_email_templates():
	for tmpl in EMAIL_TEMPLATES:
		if frappe.db.exists("Email Template", tmpl["name"]):
			doc = frappe.get_doc("Email Template", tmpl["name"])
		else:
			doc = frappe.new_doc("Email Template")
			doc.name = tmpl["name"]
		doc.subject = tmpl["subject"]
		doc.response = tmpl["response"]
		doc.use_html = 1
		if doc.is_new():
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
