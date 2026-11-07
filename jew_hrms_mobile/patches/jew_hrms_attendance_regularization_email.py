import frappe


EMAIL_TEMPLATES = [
	{
		"name": "Attendance Regularization - HR Request",
		"subject": "Attendance Regularization Request Pending Review – {{ employee_name }}",
		"response": """<p>Dear HR,</p>
<p>{{ employee_name }} has submitted an attendance regularization request and it is awaiting your review.</p>
<p>
Employee Name: {{ employee_name }}<br>
Employee ID: {{ employee_id }}<br>
Department: {{ department }}<br>
Issue Type: {{ issue_type }}<br>
Date: {{ attendance_date }}<br>
Reason: {{ reason }}<br>
Current Status: Pending HR Review
</p>
<p style="margin: 20px 0;">
<a href="{{ app_link }}" style="display:inline-block; padding:10px 24px; background:#1a52b3; color:#ffffff; text-decoration:none; border-radius:5px; font-weight:600;">Open JEW HRMS App</a>
</p>
<p>Please open the app to Approve or Reject this request.</p>
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
