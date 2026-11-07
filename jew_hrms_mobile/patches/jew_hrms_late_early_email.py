import frappe


EMAIL_TEMPLATES = [
	{
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
<p style="margin: 20px 0;">
<a href="{{ app_link }}" style="display:inline-block; padding:10px 24px; background:#1a52b3; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600;">Open JEW HRMS App</a>
</p>
<p>Please open the app to Approve or Reject this request.</p>
<p>Regards,<br>HRMS Team<br>{{ company_name }}</p>""",
	},
	{
		"name": "Late Early - Employee Notified After Decision",
		"subject": "Your {{ application_type }} Request Has Been {{ decision }} – {{ application_id }}",
		"response": """<p>Dear {{ employee_name }},</p>
<p>Your {{ application_type }} request has been <strong>{{ decision }}</strong> by your Department Head.</p>
<p>
Request Type: {{ application_type }}<br>
Date: {{ application_date }}<br>
Expected Time: {{ expected_time }}<br>
Reason: {{ reason }}<br>
Decision: {{ decision }}<br>
{% if remarks %}Remarks: {{ remarks }}<br>{% endif %}
</p>
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
