import frappe


EMAIL_TEMPLATES = [
	{
		# Adds one-click Approve/Reject/View buttons (same scheme as the Leave
		# Application emails) - previously this only had a generic "Open JEW HRMS
		# App" link.
		"name": "Late Early - Dept Head Approval Request",
		"subject": "{{ application_type }} Request Pending Your Approval – {{ employee_name }}",
		"response": """<p>Dear {{ department_head_name }},</p>
<p>{{ employee_name }} has submitted a {{ application_type }} request and it is awaiting your approval.</p>
<p>
Employee Name: {{ employee_name }}<br>
Employee ID: {{ employee_id }}<br>
Department: {{ department }}<br>
Request Type: {{ application_type }}<br>
Date: {{ application_date }}<br>
Expected Time: {{ expected_time }}<br>
Reason: {{ reason }}<br>
Current Status: Pending Department Head Approval
</p>
{% if approve_link %}
<p style="margin: 20px 0;">
<a href="{{ approve_link }}" style="display:inline-block; padding:10px 24px; background:#1a7f45; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Approve</a>
<a href="{{ reject_link }}" style="display:inline-block; padding:10px 24px; background:#b3341f; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Reject</a>
<a href="{{ app_link }}" style="display:inline-block; padding:10px 24px; background:#1a52b3; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600;">View</a>
</p>
{% endif %}
<p>Once approved, the request will be forwarded to HR for final sign-off.</p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Late Early - HR Approval Request",
		"subject": "{{ application_type }} Request Pending HR Approval – {{ employee_name }}",
		"response": """<p>Dear HR,</p>
<p>{{ employee_name }}'s {{ application_type }} request has been approved by their Department Head and now needs your final sign-off.</p>
<p>
Employee Name: {{ employee_name }}<br>
Employee ID: {{ employee_id }}<br>
Department: {{ department }}<br>
Request Type: {{ application_type }}<br>
Date: {{ application_date }}<br>
Expected Time: {{ expected_time }}<br>
Reason: {{ reason }}<br>
Current Status: Pending HR Approval
</p>
{% if approve_link %}
<p style="margin: 20px 0;">
<a href="{{ approve_link }}" style="display:inline-block; padding:10px 24px; background:#1a7f45; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Approve</a>
<a href="{{ reject_link }}" style="display:inline-block; padding:10px 24px; background:#b3341f; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600; margin-right:10px;">Reject</a>
<a href="{{ app_link }}" style="display:inline-block; padding:10px 24px; background:#1a52b3; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600;">View</a>
</p>
{% endif %}
<p>Please give the final Approve or Reject decision.</p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
]


def execute():
	ensure_email_templates()


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
