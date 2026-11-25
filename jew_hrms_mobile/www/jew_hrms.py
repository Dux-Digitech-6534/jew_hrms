import json
from pathlib import Path

import frappe
from frappe.sessions import get_csrf_token


no_cache = 1


def _user_live_scan():
	try:
		user = frappe.session.user
		if not user or user == "Guest":
			return False
		employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")
		from jew_hrms_mobile import liveness_service
		return bool(employee and liveness_service.employee_flag(employee))
	except Exception:
		return False


def get_context(context):
	context.no_cache = 1
	context.csrf_token = get_csrf_token()
	context.css_files = []
	context.js_files = []
	context.logo_url = ""
	context.logo_white_url = ""
	# live-scan (active liveness): the whole site (mode "active") or the logged-in employee's own live-check switch;
	# the page tells the app so everybody else keeps the normal selfie flow
	context.live_scan = frappe.conf.get("jew_hrms_liveness_mode") == "active" or _user_live_scan()
	manifest_path = Path(frappe.get_app_path("jew_hrms_mobile")) / "public" / "frontend" / ".vite" / "manifest.json"
	if manifest_path.exists():
		manifest = json.loads(manifest_path.read_text())
		logo = manifest.get("src/assets/dux-logo.png")
		logo_white = manifest.get("src/assets/dux-logo-white.png")
		if logo:
			context.logo_url = f"/assets/jew_hrms_mobile/frontend/{logo['file']}"
		if logo_white:
			context.logo_white_url = f"/assets/jew_hrms_mobile/frontend/{logo_white['file']}"
		entry = manifest.get("index.html") or next((item for item in manifest.values() if item.get("isEntry")), None)
		if entry:
			for css in entry.get("css", []):
				context.css_files.append(f"/assets/jew_hrms_mobile/frontend/{css}")
			context.js_files.append(f"/assets/jew_hrms_mobile/frontend/{entry['file']}")
