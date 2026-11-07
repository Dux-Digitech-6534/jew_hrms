import frappe


# Employee-facing email subjects previously showed the raw internal record ID
# (e.g. "knvl40gruo") - meaningless to a non-technical employee. Swap it for the
# request/leave date(s), which is actually useful context. Only the subject
# changes here - the email body content is left untouched.
SUBJECT_UPDATES = {
	"Late Early - Employee Notified After Decision":
		"Your {{ application_type }} Request Has Been {{ decision }} – {{ application_date }}",
	"Leave - Employee Submission Confirmation":
		"Your Leave Application Has Been Submitted – {{ from_date }} to {{ to_date }}",
	"Leave - Employee Notified After Dept Head Approval":
		"Your Leave Application Has Been Approved by the Department Head – {{ from_date }} to {{ to_date }}",
	"Leave - Employee Notified After MD Approval":
		"Your Leave Application Has Been Approved by the Managing Director – {{ from_date }} to {{ to_date }}",
	"Leave - Employee Final Approval":
		"Your Leave Application Has Been Finally Approved – {{ from_date }} to {{ to_date }}",
	"Leave - Employee Rejection":
		"Your Leave Application Has Been Rejected – {{ from_date }} to {{ to_date }}",
}


def execute():
	for name, subject in SUBJECT_UPDATES.items():
		if frappe.db.exists("Email Template", name):
			frappe.db.set_value("Email Template", name, "subject", subject, update_modified=False)
