"""Project.custom_whatsapp_group_url: the WhatsApp group the app opens for this project's employees when they post a
leave / late-early / on duty / regularization message. Empty = the company group (site config / LEAVE_WHATSAPP_GROUP_URL)."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Project": [
				{
					"fieldname": "custom_whatsapp_group_url",
					"label": "WhatsApp Group Link",
					"fieldtype": "Data",
					"options": "URL",
					"insert_after": "project_name",
					"description": "Leave, Late/Early, On Duty and Regularization messages of employees on this project open this group. Leave empty to use the company group.",
				}
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="Project")
