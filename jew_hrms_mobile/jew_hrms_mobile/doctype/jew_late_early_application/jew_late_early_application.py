import frappe
from frappe.utils import getdate, today
from frappe.model.document import Document


class JEWLateEarlyApplication(Document):
	def validate(self):
		# Only block this on the INITIAL submission - by the time HR decides a
		# cancellation request (or anyone else edits the doc later), the
		# application date has very often already passed, which is expected and
		# fine; re-checking "not in the past" on every later save made the record
		# permanently un-editable in Desk once its date arrived, blocking even the
		# Approve/Reject Cancellation buttons.
		if self.is_new() and getdate(self.application_date) < getdate(today()):
			frappe.throw("Application Date cannot be in the past.")
