import json
from pathlib import Path

import frappe
from frappe.sessions import get_csrf_token


no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.csrf_token = get_csrf_token()
	context.css_files = []
	context.js_files = []
	manifest_path = Path(frappe.get_app_path("jew_hrms_mobile")) / "public" / "frontend" / ".vite" / "manifest.json"
	if manifest_path.exists():
		manifest = json.loads(manifest_path.read_text())
		entry = manifest.get("index.html") or next((item for item in manifest.values() if item.get("isEntry")), None)
		if entry:
			for css in entry.get("css", []):
				context.css_files.append(f"/assets/jew_hrms_mobile/frontend/{css}")
			context.js_files.append(f"/assets/jew_hrms_mobile/frontend/{entry['file']}")
