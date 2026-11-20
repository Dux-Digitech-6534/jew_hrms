"""Employee.custom_project: the Project (site) an employee works on. Used by the Punch Audit page's Project filter."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Employee": [
				{
					"fieldname": "custom_project",
					"label": "Project",
					"fieldtype": "Link",
					"options": "Project",
					"insert_after": "designation",
					"in_standard_filter": 1,
					"description": "Project / site this employee works on (Punch Audit can filter by it)",
				}
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="Employee")
