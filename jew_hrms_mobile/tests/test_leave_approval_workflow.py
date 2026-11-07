"""Automated coverage for the 3-level Leave Application approval workflow
(Dept Head -> MD -> HR) in jew_hrms_mobile/api.py.

Run with:
	bench --site <site> run-tests --app jew_hrms_mobile --module jew_hrms_mobile.tests.test_leave_approval_workflow

Uses real, already-configured Employee/User fixtures from this site rather than
creating new ones. NOTE: this site's test runner does NOT auto-rollback each
test's DB writes (confirmed empirically - `bench run-tests` behaves like a
normal request, not a wrapped-and-rolled-back transaction), so every test that
creates a Leave Application registers it in self._created_leaves and tearDown
explicitly cancels+deletes it. Do not rely on framework rollback here.
"""
import frappe
from frappe.tests.utils import FrappeTestCase

from jew_hrms_mobile import api as m


# Shubhangi Pawar - IT team, custom_dept_head_email -> Ajay Mishra (not the MD).
APPLICANT = "HR-EMP-00390"
DEPT_HEAD_USER = "drajaymishra.snipl@gmail.com"
# Durgesh Joshi - a real employee/user with no approval authority over APPLICANT
# at any stage (same dept-head bucket as Shubhangi, not an approver themselves).
UNAUTHORIZED_USER = "durgesh.joshi@thesvsgroup.org"
# Akshay Nandankar - custom_dept_head_email IS the MD (dept-head-is-MD skip case).
DEPT_HEAD_IS_MD_APPLICANT = "HR-EMP-00584"


_DAY_COUNTER = [1]


def _leave_dates():
	"""A fresh, non-overlapping date each call (deterministic counter, not random,
	so no two test methods can ever collide regardless of rollback timing between
	tests). Must fall inside the employees' current Leave Allocation period
	(FY 2026-27: 2026-04-01 to 2027-03-31) - Feb 2027 is safely mid-range and far
	from today's real activity."""
	day = _DAY_COUNTER[0]
	_DAY_COUNTER[0] = 1 if day >= 26 else day + 1
	return f"2027-02-{day:02d}", f"2027-02-{day:02d}"


class TestLeaveApprovalWorkflow(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.md_user = frappe.get_cached_doc("HR Settings").get("custom_leave_md_user")
		self.hr_user = frappe.get_cached_doc("HR Settings").get("custom_leave_hr_user")
		self.assertTrue(self.md_user, "HR Settings 'Leave Approval - MD User' must be configured for these tests")
		self.assertTrue(self.hr_user, "HR Settings 'Leave Approval - HR User' must be configured for these tests")
		self.applicant_user = frappe.db.get_value("Employee", APPLICANT, "user_id")
		self._created_leaves = []
		self._hr_settings_restore = {}

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self._created_leaves:
			try:
				if frappe.db.exists("Leave Application", name):
					if frappe.db.get_value("Leave Application", name, "docstatus") == 1:
						frappe.get_doc("Leave Application", name).cancel()
					frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True, delete_permanently=True)
				frappe.db.delete("Notification Log", {"document_name": name})
			except Exception:
				frappe.log_error(title="test_leave_approval_workflow cleanup failed", message=frappe.get_traceback())
		for field, value in self._hr_settings_restore.items():
			frappe.db.set_single_value("HR Settings", field, value)
		frappe.db.commit()

	def _new_leave(self, employee=None):
		from_date, to_date = _leave_dates()
		doc = frappe.get_doc({
			"doctype": "Leave Application",
			"employee": employee or APPLICANT,
			"leave_type": "Casual Leave",
			"from_date": from_date,
			"to_date": to_date,
			"posting_date": frappe.utils.today(),
			"status": "Open",
		})
		doc.insert(ignore_permissions=True)
		self._created_leaves.append(doc.name)
		return doc

	def _override_hr_setting(self, field, temp_value):
		"""Registers the CURRENT value for tearDown to restore, then sets temp_value."""
		if field not in self._hr_settings_restore:
			self._hr_settings_restore[field] = frappe.db.get_single_value("HR Settings", field)
		frappe.db.set_single_value("HR Settings", field, temp_value)

	def _notified(self, user, doc):
		"""Approver-facing notifications (Dept Head/MD/HR) go through the standard
		Frappe Notification Log - desk bell icon."""
		return bool(frappe.get_all("Notification Log", filters={"for_user": user, "document_name": doc.name}, limit=1))

	def _employee_notified_count(self, doc):
		"""Employee-facing notifications go through this app's own 'JEW HRMS
		Notification' doctype (the mobile app's feed) - a separate mechanism from
		Notification Log, keyed by Employee ID rather than user email."""
		return frappe.db.count("JEW HRMS Notification", {"employee": doc.employee, "reference_name": doc.name})

	def _wait_for_async(self):
		"""Notification Log writes happen via enqueue(after_commit=True) on a
		background RQ worker - give it a moment before asserting on them."""
		import time
		frappe.db.commit()
		time.sleep(2)

	def _sent_emails(self, action):
		"""Runs `action` with frappe.sendmail mocked and returns every recipient
		list passed to it. This site has no outgoing Email Account configured, so
		real Email Queue rows are never created even on a successful send attempt -
		mocking is what actually verifies the template rendered and the send was
		attempted for the right recipient, independent of that infra gap."""
		import unittest.mock as mock
		with mock.patch("jew_hrms_mobile.api.frappe.sendmail") as mock_send:
			action()
		return [r for c in mock_send.call_args_list for r in (c.kwargs.get("recipients") or [])]

	# --- 1-3: submission ---------------------------------------------------

	def test_01_employee_submits_leave(self):
		doc = self._new_leave()
		self.assertEqual(doc.jew_hrms_approval_status, "Pending Department Head Approval")
		self.assertEqual(doc.current_approval_level, "Department Head")

	def test_02_department_head_receives_notification_and_email(self):
		box = {}
		sent_to = self._sent_emails(lambda: box.update(doc=self._new_leave()))
		doc = box["doc"]
		self._wait_for_async()
		self.assertTrue(self._notified(DEPT_HEAD_USER, doc))
		self.assertIn(DEPT_HEAD_USER, sent_to)

	def test_03_employee_receives_submission_confirmation(self):
		box = {}
		sent_to = self._sent_emails(lambda: box.update(doc=self._new_leave()))
		doc = box["doc"]
		self.assertGreaterEqual(self._employee_notified_count(doc), 1)
		self.assertIn(self.applicant_user, sent_to)

	# --- 4-6: Dept Head approves --------------------------------------------

	def test_04_dept_head_approves(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER)
		result = m.approve_leave(name=doc.name)
		self.assertTrue(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Pending MD Approval")
		self.assertEqual(doc.current_approval_level, "MD")
		self.assertEqual(doc.dept_head_approved_by, DEPT_HEAD_USER)
		self.assertIsNotNone(doc.dept_head_approved_on)

	def test_05_md_notified_and_emailed_after_dept_head_approval(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER)
		sent_to = self._sent_emails(lambda: m.approve_leave(name=doc.name))
		frappe.set_user("Administrator")
		self._wait_for_async()
		self.assertTrue(self._notified(self.md_user, doc))
		self.assertIn(self.md_user, sent_to)

	def test_06_employee_notified_after_dept_head_approval(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER)
		m.approve_leave(name=doc.name)
		frappe.set_user("Administrator")
		self.assertGreaterEqual(self._employee_notified_count(doc), 1)

	# --- 7-9: MD approves ----------------------------------------------------

	def test_07_md_approves(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER)
		m.approve_leave(name=doc.name)
		frappe.set_user(self.md_user)
		result = m.approve_leave(name=doc.name)
		self.assertTrue(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Pending HR Approval")
		self.assertEqual(doc.current_approval_level, "HR")
		self.assertEqual(doc.owner_approved_by, self.md_user)

	def test_08_hr_notified_and_emailed_after_md_approval(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER); m.approve_leave(name=doc.name)
		frappe.set_user(self.md_user)
		sent_to = self._sent_emails(lambda: m.approve_leave(name=doc.name))
		frappe.set_user("Administrator")
		self._wait_for_async()
		self.assertTrue(self._notified(self.hr_user, doc))
		self.assertIn(self.hr_user, sent_to)

	def test_09_employee_notified_after_md_approval(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER); m.approve_leave(name=doc.name)
		frappe.set_user(self.md_user); m.approve_leave(name=doc.name)
		frappe.set_user("Administrator")
		# submission + dept-head-approved + md-approved
		self.assertGreaterEqual(self._employee_notified_count(doc), 3)

	# --- 10-11: HR approves (final) ------------------------------------------

	def test_10_hr_approves_is_final(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER); m.approve_leave(name=doc.name)
		frappe.set_user(self.md_user); m.approve_leave(name=doc.name)
		frappe.set_user(self.hr_user)
		result = m.approve_leave(name=doc.name)
		self.assertTrue(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Approved")
		self.assertEqual(doc.current_approval_level, "Completed")
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.hr_approved_by, self.hr_user)

	def test_11_employee_receives_final_approval_email(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER); m.approve_leave(name=doc.name)
		frappe.set_user(self.md_user); m.approve_leave(name=doc.name)
		frappe.set_user(self.hr_user)
		sent_to = self._sent_emails(lambda: m.approve_leave(name=doc.name))
		frappe.set_user("Administrator")
		self.assertIn(self.applicant_user, sent_to)

	# --- 12-14: rejection at each level ---------------------------------------

	def test_12_dept_head_rejects(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER)
		result = m.reject_leave(name=doc.name, remarks="test reject at dept head")
		self.assertTrue(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Rejected by Department Head")
		self.assertEqual(doc.rejection_level, "Department Head")
		self.assertEqual(doc.rejected_by, DEPT_HEAD_USER)
		self.assertEqual(doc.reject_reason, "test reject at dept head")
		# Rejecting must not notify the next-level approver.
		self.assertFalse(self._notified(self.md_user, doc))

	def test_13_md_rejects_after_dept_head_approval(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER); m.approve_leave(name=doc.name)
		frappe.set_user(self.md_user)
		result = m.reject_leave(name=doc.name, remarks="test reject at md")
		self.assertTrue(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Rejected by MD")
		self.assertEqual(doc.rejection_level, "MD")
		frappe.set_user("Administrator")
		self.assertFalse(self._notified(self.hr_user, doc))

	def test_14_hr_rejects_after_md_approval(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER); m.approve_leave(name=doc.name)
		frappe.set_user(self.md_user); m.approve_leave(name=doc.name)
		frappe.set_user(self.hr_user)
		result = m.reject_leave(name=doc.name, remarks="test reject at hr")
		self.assertTrue(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Rejected by HR")
		self.assertEqual(doc.rejection_level, "HR")
		# No leave balance/attendance side effect - rejected leaves never reach
		# docstatus=1, so sync_approved_leave_to_salary_slip (on_submit) never fires.
		self.assertEqual(doc.docstatus, 0)

	# --- 15-17: security -------------------------------------------------------

	def test_15_unauthorized_user_cannot_approve(self):
		"""UNAUTHORIZED_USER holds a broad ERP role (dev-team accounts carry System
		Manager for unrelated technical reasons - see api.py's ADMIN_ROLES comment),
		so they pass the coarse _require_leave_approver() gate but must still be
		rejected by the per-stage identity check (_is_department_head/_is_leave_owner/
		_is_leave_hr) - either a raised PermissionError or a failed result is an
		acceptable way to block them; what matters is the leave never advances."""
		doc = self._new_leave()
		frappe.set_user(UNAUTHORIZED_USER)
		blocked = False
		try:
			result = m.approve_leave(name=doc.name)
			blocked = not result.get("ok")
		except frappe.PermissionError:
			blocked = True
		frappe.set_user("Administrator")
		self.assertTrue(blocked, "Unauthorized user's approve_leave call should have been blocked")
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Pending Department Head Approval")

	def test_16_approver_cannot_approve_wrong_stage(self):
		doc = self._new_leave()
		# MD tries to approve before the Dept Head stage has been actioned.
		frappe.set_user(self.md_user)
		result = m.approve_leave(name=doc.name)
		self.assertFalse(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Pending Department Head Approval")

	def test_17_duplicate_approval_is_rejected(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER)
		first = m.approve_leave(name=doc.name)
		self.assertTrue(first["ok"])
		second = m.approve_leave(name=doc.name)
		self.assertFalse(second["ok"])
		self.assertEqual(second["code"], "permission_denied")

	# --- 18-19: missing approver configuration --------------------------------

	def test_18_missing_md_configuration_blocks_advance(self):
		doc = self._new_leave()
		self._override_hr_setting("custom_leave_md_user", "")
		frappe.set_user(DEPT_HEAD_USER)
		with self.assertRaises(frappe.ValidationError):
			m.approve_leave(name=doc.name)
		frappe.set_user("Administrator")
		doc.reload()
		# Must stay at its current stage - not half-advanced.
		self.assertEqual(doc.jew_hrms_approval_status, "Pending Department Head Approval")
		self.assertIsNone(doc.dept_head_approved_by)

	def test_19_missing_hr_configuration_blocks_advance(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER)
		m.approve_leave(name=doc.name)
		self._override_hr_setting("custom_leave_hr_user", "")
		frappe.set_user(self.md_user)
		with self.assertRaises(frappe.ValidationError):
			m.approve_leave(name=doc.name)
		frappe.set_user("Administrator")
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Pending MD Approval")
		self.assertIsNone(doc.owner_approved_by)

	# --- 20: email sending failure never blocks the workflow ------------------

	def test_20_email_failure_does_not_block_approval(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER)
		import unittest.mock as mock
		with mock.patch("jew_hrms_mobile.api.frappe.sendmail", side_effect=Exception("smtp down")):
			result = m.approve_leave(name=doc.name)
		self.assertTrue(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Pending MD Approval")

	# --- 21: leave balance / attendance sync only after final HR approval ----

	def test_21_salary_sync_hook_only_fires_on_final_submit(self):
		doc = self._new_leave()
		frappe.set_user(DEPT_HEAD_USER); m.approve_leave(name=doc.name)
		frappe.set_user(self.md_user); m.approve_leave(name=doc.name)
		doc.reload()
		self.assertEqual(doc.docstatus, 0)  # not yet submitted -> on_submit hook has not fired
		frappe.set_user(self.hr_user)
		m.approve_leave(name=doc.name)
		doc.reload()
		self.assertEqual(doc.docstatus, 1)  # HR's approval is what actually calls doc.submit()

	# --- 22: existing/historical Leave Applications keep working -------------

	def test_22_historical_leave_application_status_resolution_unaffected(self):
		"""A Leave Application saved before this change (generic 'Rejected'/'Pending
		Owner Approval') must still resolve/display correctly - these values were
		deliberately left in the Select options and MD_STAGE_STATUSES for exactly
		this reason."""
		doc = self._new_leave()
		# Simulate a pre-existing record sitting in the OLD "Pending Owner Approval"
		# stage (the name used before this change introduced "Pending MD Approval").
		frappe.db.set_value("Leave Application", doc.name, "jew_hrms_approval_status", "Pending Owner Approval", update_modified=False)
		doc.reload()
		frappe.set_user(self.md_user)
		result = m.can_i_approve_this_leave(name=doc.name)
		self.assertTrue(result["can_approve"])
		self.assertEqual(result["stage"], "md")
		approve_result = m.approve_leave(name=doc.name)
		self.assertTrue(approve_result["ok"])

	# --- dept-head-is-MD skip case (added this session, worth locking in) ----

	def test_dept_head_is_md_skips_md_stage(self):
		doc = self._new_leave(employee=DEPT_HEAD_IS_MD_APPLICANT)
		self.assertEqual(doc.jew_hrms_approval_status, "Pending Department Head Approval")
		frappe.set_user(self.md_user)  # this employee's configured Dept Head IS the MD
		result = m.approve_leave(name=doc.name)
		self.assertTrue(result["ok"])
		doc.reload()
		self.assertEqual(doc.jew_hrms_approval_status, "Pending HR Approval")
