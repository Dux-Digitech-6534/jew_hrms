import frappe
from frappe.model.document import Document


class JEWShiftAttendancePolicy(Document):
	def validate(self):
		if not self.shift_name:
			frappe.throw("Shift name is required.")
