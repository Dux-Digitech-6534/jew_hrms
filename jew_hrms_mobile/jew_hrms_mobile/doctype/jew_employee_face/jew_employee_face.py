import frappe
from frappe.model.document import Document


class JEWEmployeeFace(Document):
	def validate(self):
		if self.is_active:
			for name in frappe.get_all("JEW Employee Face", filters={"employee": self.employee, "is_active": 1, "name": ["!=", self.name]}, pluck="name"):
				frappe.db.set_value("JEW Employee Face", name, "is_active", 0)
