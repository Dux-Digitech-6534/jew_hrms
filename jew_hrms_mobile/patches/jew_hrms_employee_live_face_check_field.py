"""Employee.custom_live_face_check: switch the live face scan (turn head / blink) on for ONE employee's Mark In /
Mark Out while the rest of the site keeps the normal selfie. Read by liveness_service.active_for(); switched by HR on
the Employee form, or by HR / the employee's Dept Head on the Punch audit page, "Live check" tab."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Employee": [
				{
					"fieldname": "custom_live_face_check",
					"label": "Live face check",
					"fieldtype": "Check",
					"default": "0",
					"insert_after": "custom_face_approver_email",
					"description": "When ticked, this employee must turn their head (live scan) in the app before Mark In and Mark Out. "
					"HR or the employee's Dept Head can also switch it on the Punch audit page, Live check tab.",
				}
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="Employee")
