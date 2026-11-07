from jew_hrms_mobile.attendance_fields import backfill_tracking_fields, ensure_custom_fields


def execute():
	ensure_custom_fields()
	backfill_tracking_fields()
