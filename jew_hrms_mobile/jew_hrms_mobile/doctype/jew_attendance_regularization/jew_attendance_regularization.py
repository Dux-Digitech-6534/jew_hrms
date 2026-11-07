import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today

SELF_SERVICE_ISSUE_TYPES = ("Missing Mark In", "Missing Mark Out", "Missing Mark In & Out", "Missing Mark In (Half Day)", "Missing Mark Out (Half Day)", "Late Coming Check-in", "Early Going Check-out")
SELF_SERVICE_ACTIVE_STATUSES = ("Pending", "Checked In", "Approved as Check-Out", "Approved as Present", "Marked Half Day", "Marked LWP")


class JEWAttendanceRegularization(Document):
	def before_insert(self):
		# Records raised by the policy engine (api._create_regularization) are never
		# self-service, even when mark_attendance creates them inside the employee's OWN
		# session: they use issue types outside SELF_SERVICE_ISSUE_TYPES (Late Coming /
		# Early Going / Short Hours / Late Arrival), carry no employee "reason", and must
		# not page HR. Without this, every check-out that raised a policy issue failed.
		if self.flags.get("system_generated"):
			self.flags.is_self_service = False
			return
		self.flags.is_self_service = self._resolve_self_service()
		if self.flags.is_self_service:
			self._validate_self_service()

	def _resolve_self_service(self):
		"""True whenever the logged-in user is raising a request for their OWN
		Employee record — via Desk's native 'New' form or the mobile app's
		request_attendance_regularization — regardless of whether that user also
		holds an HR/Admin/dept-head role (an HR person requesting for THEMSELVES is
		still self-service and should alert HR same as anyone else). False when an
		Admin/HR/dept-head creates a record for a DIFFERENT employee (entering it on
		their behalf — they already know about it, no alert needed) and always False
		for the scheduled auto-detection engine (_evaluate_attendance_policy /
		process_missing_attendance), which runs as Administrator/system and has no
		Employee mapping of its own."""
		user = frappe.session.user
		if user in ("Administrator", "Guest"):
			return False
		from jew_hrms_mobile.api import _get_employee_for_user
		employee = _get_employee_for_user(user)
		return bool(employee and employee.name == self.employee)

	def _validate_self_service(self):
		"""Same rules regardless of entry point (Desk or mobile): only the three
		self-raisable issue types, a reason, no future dates, one active request per
		day — and never let the requester set their own approval state."""
		if self.issue_type not in SELF_SERVICE_ISSUE_TYPES:
			frappe.throw(_("Issue type must be Missing Mark In, Missing Mark Out, Missing Mark In & Out, Missing Mark In (Half Day), Missing Mark Out (Half Day), Late Coming Check-in, or Early Going Check-out."))
		if not self.remarks:
			frappe.throw(_("Reason is required."))
		attendance_date = getdate(self.attendance_date)
		if attendance_date > getdate(today()):
			frappe.throw(_("Attendance Date cannot be in the future."))
		if frappe.db.exists("JEW Attendance Regularization", {
			"employee": self.employee, "attendance_date": attendance_date,
			"status": ["in", list(SELF_SERVICE_ACTIVE_STATUSES)],
		}):
			frappe.throw(_("You already have a regularization request for this date."))
		self.attendance_date = attendance_date
		self.status = "Pending"
		self.policy_action = self.policy_action or "Regularization Required"
		self.requested_by = frappe.session.user
		self.created_by = frappe.session.user
		self.approved_by = None

	def after_insert(self):
		# Alert HR only when the EMPLOYEE THEMSELVES formally applies/requests a
		# regularization (self-service) - a missed check-in/out that the scheduled
		# auto-detection engine silently flags should NOT by itself page HR; the
		# affected employee still gets their own in-app notice (see
		# _create_regularization in api.py) and can choose to formally apply if they
		# want HR to review it. Only that deliberate self-service application should
		# reach HR - not every auto-flagged miss.
		if self.flags.is_self_service:
			from jew_hrms_mobile.api import _send_regularization_request_email
			employee_doc = frappe.get_doc("Employee", self.employee)
			_send_regularization_request_email(self, employee_doc)
