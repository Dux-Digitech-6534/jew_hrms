import frappe
from frappe.model.document import Document


class JEWPunchAudit(Document):
	"""One row of evidence per attendance punch (photo, face distance, device, IP, clock drift).

	Rows are written by jew_hrms_mobile.punch_audit.record_punch from mark_attendance and read by the
	Punch audit page. They are system-generated: the doctype is hidden from manual creation (in_create)."""
	pass
