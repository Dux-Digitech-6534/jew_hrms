"""Punch audit - the evidence behind every attendance punch.

mark_attendance calls record_punch() right after the Employee Checkin is committed. It stores, per punch: a
small private photo, the face distance, the matched geofence + distance, the device (an install id sent by the
app + model/OS/browser parsed from the User-Agent), the IP address and the phone-clock drift. The "Punch audit"
desk page (page/jew_punch_audit) reads it through the whitelisted functions below.

Design rules
  * record_punch NEVER raises and NEVER blocks a punch: any failure is logged and swallowed.
  * Flags are COMPUTED at read time from the stored rows (nothing about a flag is persisted), so the rules can
    change without touching data. They are signals for HR, never verdicts, and nothing is blocked by them.
  * Punches made before capture started (and HR-entered manual punches) still appear: time, location and a
    GPS-pattern check come from the Employee Checkin itself; photo / device / IP / drift are simply empty.
  * Photos are private files served only through get_punch_photo (role-checked). They are deleted after the
    retention period (default 90 days, set from the page); the audit row itself is kept.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.desk.utils import provide_binary_file
from frappe.utils import add_days, cint, flt, get_datetime, get_url, getdate, now_datetime, today
from frappe.utils.file_manager import save_file
from frappe.utils.xlsxutils import make_xlsx

DOCTYPE = "JEW Punch Audit"
AUDIT_ROLES = {"System Manager", "JEW HRMS Admin", "JEW HRMS HR", "JEW HRMS Owner"}

DRIFT_SECONDS = 120            # phone clock this far from the server is flagged
DRIFT_HIGH_SECONDS = 600
FLAT_SD = 0.006                # face distance std-dev below this over the last PATTERN_WINDOW punches = "flat"
CENTRE_METRES = 3              # median distance from the site centre below this = "GPS at centre"
PATTERN_WINDOW = 6
PATTERN_DAYS = 30
PRIMARY_DEVICE_DAYS = 60
PRIMARY_MIN_ROWS = 5           # an employee needs this many captured punches before "new device" can fire
MAX_RANGE_DAYS = 92
PHOTO_MAX_SIDE = 640           # longer side of the stored photo; a details strip is added below it
PHOTO_QUALITY = 80
RETENTION_KEY = "jew_punch_audit_retention_days"
DEFAULT_RETENTION_DAYS = 90
RETENTION_CHOICES = (30, 60, 90, 180, 365)
MOBILE_DEVICE_ID = "JEW HRMS Mobile"


# ----------------------------------------------------------------------------- helpers
def _api():
	from jew_hrms_mobile import api

	return api


def _require_access():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)
	if user == "Administrator":
		return
	if not (set(frappe.get_roles(user)) & AUDIT_ROLES):
		frappe.throw(_("You do not have permission to view the punch audit."), frappe.PermissionError)


def _clean_device_id(value):
	value = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:64]
	return value or None


def _parse_ua(ua):
	"""(model, os, browser) from a User-Agent string. Best effort - any piece may be None."""
	ua = ua or ""
	model = os_ver = browser = None
	m = re.search(r"Android\s+([\d.]+)", ua)
	if m:
		os_ver = "Android " + m.group(1)
		mm = re.search(r"Android\s+[\d.]+;\s*([^;)]+?)(?:\s+Build/|[;)])", ua)
		if mm:
			model = mm.group(1).strip()
			if model.upper() == "K":      # Chrome's reduced User-Agent hides the real model behind "K"
				model = "Android phone"
	else:
		mi = re.search(r"\((iPhone|iPad)[^)]*?OS\s+([\d_]+)", ua)
		if mi:
			model = mi.group(1)
			os_ver = "iOS " + mi.group(2).replace("_", ".")
		elif "Windows NT" in ua:
			os_ver, model = "Windows", "PC"
		elif "Mac OS X" in ua:
			os_ver, model = "macOS", "Mac"
		elif "Linux" in ua:
			os_ver, model = "Linux", "PC"
	mb = re.search(r"(Edg|Chrome|CriOS|Firefox|FxiOS)/(\d+)", ua)
	if mb:
		name = {"Edg": "Edge", "CriOS": "Chrome", "FxiOS": "Firefox"}.get(mb.group(1), mb.group(1))
		browser = ("WebView / " if "; wv)" in ua else "") + name + " " + mb.group(2)
	elif "Safari/" in ua and "Version/" in ua:
		mv = re.search(r"Version/([\d.]+)", ua)
		browser = "Safari " + (mv.group(1) if mv else "")
	return model, os_ver, browser


def _open_image(payload):
	"""Base64 / data-URL image -> RGB PIL image scaled to at most PHOTO_MAX_SIDE, or None."""
	if not payload:
		return None
	try:
		from PIL import Image

		s = str(payload).strip()
		if s.startswith("data:") and "," in s:
			s = s.split(",", 1)[1]
		img = Image.open(io.BytesIO(base64.b64decode(s)))
		img.load()
		img = img.convert("RGB")
		img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
		return img
	except Exception:
		return None


_STAMP_FONTS = {
	False: ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
	        "LiberationSans-Regular.ttf", "DejaVuSans.ttf"),
	True: ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
	       "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"),
}


def _stamp_font(bold, size):
	from PIL import ImageFont

	for path in _STAMP_FONTS[bool(bold)]:
		try:
			return ImageFont.truetype(path, size)
		except Exception:
			continue
	return None


def _fit_text(draw, text, font, width):
	text = str(text or "")
	if draw.textlength(text, font=font) <= width:
		return text
	while text and draw.textlength(text + "…", font=font) > width:
		text = text[:-1]
	return text.rstrip() + "…"


def _stamp_image(img, info):
	"""Add a details strip BELOW the photo (so no part of the face is covered): employee, ID, department, punch type,
	date + time, matched location + distance and GPS. Returns a new image, or None when no font is available."""
	from PIL import Image, ImageDraw

	W, H = img.size
	fs = max(12, int(round(W / 32.0)))
	f_name, f_bold, f_reg, f_tag = _stamp_font(True, fs + 4), _stamp_font(True, fs), _stamp_font(False, fs), _stamp_font(True, max(11, fs - 1))
	if not (f_name and f_bold and f_reg and f_tag):
		return None
	BG, WHITE, SOFT, TEAL, IRIS = (18, 19, 31), (244, 245, 250), (172, 178, 202), (45, 212, 191), (99, 102, 241)
	pad, line_h = int(fs * 0.85), int(fs * 1.42)
	rows = [(info.get("l2"), f_reg, SOFT), (info.get("l3"), f_bold, TEAL), (info.get("l4"), f_reg, WHITE), (info.get("l5"), f_reg, SOFT)]
	rows = [r for r in rows if r[0]]
	strip_h = 3 + pad + int((fs + 4) * 1.5) + len(rows) * line_h + pad
	canvas = Image.new("RGB", (W, H + strip_h), BG)
	canvas.paste(img, (0, 0))
	d = ImageDraw.Draw(canvas)
	d.rectangle([0, H, W, H + 2], fill=IRIS)
	y = H + 3 + pad
	tag = str(info.get("tag") or "").upper()
	tag_w = 0
	if tag:
		tw = int(d.textlength(tag, font=f_tag))
		tag_w, th = tw + int(fs * 1.2), int(fs * 1.55)
		x0 = W - pad - tag_w
		d.rounded_rectangle([x0, y, x0 + tag_w, y + th], radius=th // 2, fill=TEAL if tag == "IN" else IRIS)
		d.text((x0 + (tag_w - tw) // 2, y + th // 2), tag, font=f_tag, fill=(10, 12, 24) if tag == "IN" else WHITE, anchor="lm")
	d.text((pad, y), _fit_text(d, info.get("name"), f_name, W - 2 * pad - tag_w - fs), font=f_name, fill=WHITE)
	y += int((fs + 4) * 1.5)
	for text, font, colour in rows:
		d.text((pad, y), _fit_text(d, text, font, W - 2 * pad), font=font, fill=colour)
		y += line_h
	return canvas


def _stamp_info(employee, log_type, checkin, near, accuracy):
	"""The lines printed on the stored photo. Best effort: returns None if anything about it fails."""
	try:
		dept = re.sub(r"\s+-\s+[A-Za-z0-9]{2,8}$", "", str(employee.get("department") or "")).strip()
		t = get_datetime(checkin.time)
		loc = (near.get("location_name") or near.get("name") or "").strip()
		if loc and near.get("distance_meter") is not None:
			loc += "  ·  %.1f m of %d m" % (flt(near.get("distance_meter")), round(flt(near.get("radius_meter"))))
		lat, lon = flt(checkin.get("latitude")), flt(checkin.get("longitude"))
		gps = ""
		if lat and lon:
			gps = "GPS %.5f, %.5f" % (lat, lon) + ("  ±%d m" % round(flt(accuracy)) if flt(accuracy) else "")
		return {
			"name": employee.employee_name or employee.name,
			"tag": log_type,
			"l2": "  ·  ".join(x for x in (employee.name, dept, str(employee.get("designation") or "").strip()) if x),
			"l3": t.strftime("%a %d %b %Y  ·  %H:%M:%S"),
			"l4": loc,
			"l5": gps,
		}
	except Exception:
		return None


def _photo_jpeg(payload, info=None):
	"""Base64 / data-URL image -> JPEG bytes. With `info` the employee / time / location details strip is added below
	the photo; if stamping fails for any reason the plain photo is stored instead."""
	img = _open_image(payload)
	if img is None:
		return None
	try:
		if info:
			stamped = _stamp_image(img, info)
			if stamped is not None:
				img = stamped
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS punch audit stamp failed")
	try:
		out = io.BytesIO()
		img.save(out, format="JPEG", quality=PHOTO_QUALITY, optimize=True)
		return out.getvalue()
	except Exception:
		return None


def _resize_jpeg(payload):
	return _photo_jpeg(payload)


def _source_of(device_id):
	if device_id == MOBILE_DEVICE_ID:
		return "Mobile"
	if not device_id or str(device_id).lower().startswith("manual"):
		return "HR Manual"
	return str(device_id)


def _face_limit():
	try:
		from jew_hrms_mobile.face_service import _face_tolerance

		return float(_face_tolerance())
	except Exception:
		return 0.55


def _retention_days():
	return cint(frappe.db.get_default(RETENTION_KEY)) or DEFAULT_RETENTION_DAYS


# ----------------------------------------------------------------------------- capture
def _speed_fields(timings, server_ms, face_ms):
	"""Scan-speed evidence for a punch: stage timings sent by the app (JSON text, whitelisted numeric keys only) plus the server-side milliseconds. Never raises."""
	out = {}
	try:
		data = json.loads(timings) if isinstance(timings, str) and timings else (timings if isinstance(timings, dict) else {})
		for field, key in (("cam_ms", "cam"), ("cap_ms", "cap"), ("gps_wait_ms", "gps"), ("lead_ms", "lead"), ("scan_try", "try")):
			v = data.get(key)
			if isinstance(v, (int, float)) and not isinstance(v, bool):
				out[field] = int(max(0, min(600000, v)))
		if data.get("pw") in (0, 1):
			out["gps_prewarmed"] = int(data["pw"])
	except Exception:
		pass
	for field, v in (("server_ms", server_ms), ("face_ms", face_ms)):
		try:
			if v is not None:
				out[field] = int(max(0, min(600000, float(v))))
		except Exception:
			pass
	return out


def record_punch(checkin, employee, log_type, image_payload, face_result, geo, accuracy, timestamp, device_id=None, timings=None, server_ms=None, face_ms=None):
	"""Store the evidence for a punch that has just been committed. Never raises."""
	try:
		api = _api()
		ua = frappe.get_request_header("User-Agent") or ""
		model, os_ver, browser = _parse_ua(ua)
		server_t = now_datetime()
		try:
			client_t = api._checkin_datetime(timestamp) if timestamp else server_t
		except Exception:
			client_t = server_t
		drift = int(round((client_t - server_t).total_seconds()))
		near = (geo or {}).get("nearest_location") or {}
		live = (face_result or {}).get("liveness") or {}
		ip = (getattr(frappe.local, "request_ip", None) or "").split(",")[0].strip() or None
		live_mode = None
		try:
			from jew_hrms_mobile import liveness_service

			live_mode = liveness_service.get_mode()
		except Exception:
			pass
		row = frappe.get_doc({
			"doctype": DOCTYPE,
			"employee": employee.name,
			"employee_name": employee.employee_name,
			"checkin": checkin.name,
			"log_type": log_type,
			"punch_time": checkin.time,
			"source": "Mobile",
			"server_time": server_t,
			"client_time": client_t,
			"clock_drift_seconds": drift,
			"location_name": (near.get("location_name") or near.get("name") or "").strip() or None,
			"distance_m": flt(near.get("distance_meter")),
			"radius_m": flt(near.get("radius_meter")),
			"gps_accuracy": flt(accuracy),
			"face_distance": flt((face_result or {}).get("distance")),
			"face_threshold": flt((face_result or {}).get("threshold")),
			"liveness_score": flt(live.get("score")) if live else 0,
			"liveness_mode": live_mode,
			"device_id": _clean_device_id(device_id),
			"device_model": model,
			"os_version": os_ver,
			"browser": browser,
			"ip_address": ip,
			"user_agent": ua[:500] or None,
			**_speed_fields(timings, server_ms, face_ms),
		})
		row.insert(ignore_permissions=True)
		jpg = _photo_jpeg(image_payload, _stamp_info(employee, log_type, checkin, near, accuracy))
		if jpg:
			f = save_file("punch-%s.jpg" % row.name, jpg, DOCTYPE, row.name, is_private=1)
			frappe.db.set_value(DOCTYPE, row.name, "punch_photo", f.file_url, update_modified=False)
		frappe.db.commit()
		return row.name
	except Exception:
		try:
			frappe.db.rollback()
		except Exception:
			pass
		frappe.log_error(frappe.get_traceback(), "JEW HRMS punch audit record failed")
		return None


# ----------------------------------------------------------------------------- reading
def _resolve_range(rng, from_date, to_date):
	t = getdate(today())
	if rng == "today":
		return t, t
	if rng == "yesterday":
		y = getdate(add_days(t, -1))
		return y, y
	if rng == "30d":
		return getdate(add_days(t, -29)), t
	if rng == "month":
		return t.replace(day=1), t
	if rng == "custom" and from_date and to_date:
		f, e = getdate(from_date), getdate(to_date)
		if f > e:
			f, e = e, f
		if (e - f).days > MAX_RANGE_DAYS:
			f = getdate(add_days(e, -MAX_RANGE_DAYS))
		return f, e
	return getdate(add_days(t, -6)), t    # "7d" / default


def _fetch_rows(from_date, to_date):
	"""Employee Checkin LEFT JOIN JEW Punch Audit for [from_date, to_date] inclusive, enriched with place data."""
	rows = frappe.db.sql(
		"""
		select c.name as checkin, c.employee, c.employee_name, c.log_type, c.time, c.device_id as c_device, c.owner as c_owner,
		       c.latitude, c.longitude, e.department,
		       a.name as audit, a.source as a_source, a.location_name, a.distance_m, a.radius_m, a.gps_accuracy,
		       a.face_distance, a.face_threshold, a.liveness_score, a.liveness_mode,
		       a.device_id, a.device_model, a.os_version, a.browser, a.ip_address,
		       a.clock_drift_seconds, a.punch_photo, a.reviewed, a.review_note, a.reviewed_by, a.reviewed_on
		from `tabEmployee Checkin` c
		left join `tabJEW Punch Audit` a on a.checkin = c.name
		left join `tabEmployee` e on e.name = c.employee
		where c.time >= %(f)s and c.time < %(t)s
		order by c.time desc
		""",
		{"f": "%s 00:00:00" % from_date, "t": "%s 00:00:00" % add_days(to_date, 1)},
		as_dict=True,
	)
	api = _api()
	seen, out, loc_cache = set(), [], {}

	def locs_for(emp):
		if emp not in loc_cache:
			try:
				loc_cache[emp] = api._assigned_locations(emp) or []
			except Exception:
				loc_cache[emp] = []
		return loc_cache[emp]

	for r in rows:
		if r.checkin in seen:
			continue
		seen.add(r.checkin)
		has_audit = bool(r.audit)
		src = r.a_source if has_audit and r.a_source else _source_of(r.c_device)
		loc, dist, radius = (r.location_name or None), (flt(r.distance_m) if has_audit else None), (flt(r.radius_m) if has_audit else None)
		if has_audit and not loc:
			dist = radius = None
		if not has_audit and src != "HR Manual" and flt(r.latitude) and flt(r.longitude):
			best = None
			for l in locs_for(r.employee):
				try:
					d = api._haversine_m(flt(r.latitude), flt(r.longitude), flt(l.latitude), flt(l.longitude))
				except Exception:
					continue
				if best is None or d < best[1]:
					best = (str(l.location_name or "").strip(), d, flt(l.radius_meter))
			if best:
				loc, dist, radius = best[0], round(best[1], 1), best[2]
		t = get_datetime(r.time)
		out.append({
			"id": r.checkin, "audit": r.audit, "employee": r.employee, "name": r.employee_name, "dept": r.department or "",
			"date": t.strftime("%Y-%m-%d"), "t": t.hour * 60 + t.minute, "sec": t.second, "type": r.log_type,
			"source": src, "added_by": (r.c_owner if src == "HR Manual" else None),
			"loc": loc, "dist": dist, "radius": radius, "acc": (flt(r.gps_accuracy) or None) if has_audit else None,
			"face": (flt(r.face_distance) or None) if has_audit else None, "live": (flt(r.liveness_score) or None) if has_audit else None,
			"live_mode": r.liveness_mode if has_audit else None,
			"dev": r.device_id if has_audit else None, "model": r.device_model if has_audit else None,
			"os": r.os_version if has_audit else None, "browser": r.browser if has_audit else None,
			"ip": r.ip_address if has_audit else None,
			"drift": int(r.clock_drift_seconds or 0) if has_audit else None,
			"photo": bool(r.punch_photo), "reviewed": bool(r.reviewed), "note": r.review_note or "",
			"lat": flt(r.latitude) or None, "lon": flt(r.longitude) or None,
		})
	return out


def _primary_devices():
	"""employee -> the device they normally punch from (most used in the last PRIMARY_DEVICE_DAYS), once they
	have at least PRIMARY_MIN_ROWS captured punches. Used for the "New device" flag."""
	rows = frappe.db.sql(
		"""select employee, device_id, count(*) n from `tabJEW Punch Audit`
		where device_id is not null and device_id <> '' and punch_time >= %s group by employee, device_id""",
		add_days(today(), -PRIMARY_DEVICE_DAYS),
		as_dict=True,
	)
	per = defaultdict(list)
	for r in rows:
		per[r.employee].append((r.n, r.device_id))
	out = {}
	for emp, lst in per.items():
		if sum(n for n, _d in lst) >= PRIMARY_MIN_ROWS:
			out[emp] = max(lst)[1]
	return out


def _compute_flags(rows):
	"""checkin id -> list of {k, ...} for every row in `rows` (rows must be newest-first)."""
	flags = defaultdict(list)
	shared = defaultdict(set)
	for r in rows:
		if r["dev"] and r["source"] == "Mobile":
			shared[(r["date"], r["dev"])].add(r["employee"])
	primary = _primary_devices()
	cutoff = str(add_days(today(), -PATTERN_DAYS))
	flat, centre = set(), set()
	by_emp = defaultdict(list)
	for r in rows:
		if r["date"] >= cutoff:
			by_emp[r["employee"]].append(r)
	for _emp, lst in by_emp.items():
		faces = [r for r in lst if r["face"]][:PATTERN_WINDOW]
		if len(faces) == PATTERN_WINDOW and statistics.pstdev([r["face"] for r in faces]) < FLAT_SD:
			flat.update(r["id"] for r in faces)
		pts = [r for r in lst if r["source"] == "Mobile" and r["dist"] is not None][:PATTERN_WINDOW]
		if len(pts) == PATTERN_WINDOW and statistics.median([r["dist"] for r in pts]) < CENTRE_METRES:
			centre.update(r["id"] for r in pts)
	for r in rows:
		f = flags[r["id"]]
		if r["source"] == "HR Manual":
			f.append({"k": "MANUAL", "sev": 0})
			continue
		s = shared.get((r["date"], r["dev"])) if r["dev"] and r["source"] == "Mobile" else None
		if s and len(s) > 1:
			f.append({"k": "SHARED", "n": len(s), "sev": 3})
		if r["dev"] and primary.get(r["employee"]) and r["dev"] != primary[r["employee"]]:
			f.append({"k": "NEWDEV", "sev": 2})
		if r["drift"] is not None and abs(r["drift"]) >= DRIFT_SECONDS:
			f.append({"k": "DRIFT", "v": r["drift"], "sev": 3 if abs(r["drift"]) >= DRIFT_HIGH_SECONDS else 2})
		if r["id"] in flat:
			f.append({"k": "FLAT", "sev": 2})
		if r["id"] in centre:
			f.append({"k": "CENTRE", "sev": 2})
	return flags


def _punch_rows(rng, from_date, to_date):
	f, t = _resolve_range(rng, from_date, to_date)
	wide_from = min(f, getdate(add_days(today(), -PATTERN_DAYS)))
	rows = _fetch_rows(wide_from, t)
	flags = _compute_flags(rows)
	lo = str(f)
	out = []
	for r in rows:
		if r["date"] < lo:
			continue
		r["flags"] = flags.get(r["id"], [])
		r["flagged"] = any(x.get("sev", 0) >= 2 for x in r["flags"])
		out.append(r)
	return f, t, out


@frappe.whitelist()
def get_punch_audit(range=None, from_date=None, to_date=None):
	"""All punches in the range with their evidence + computed flags. The page filters client-side."""
	_require_access()
	f, t, rows = _punch_rows(range, from_date, to_date)
	since = frappe.db.sql("select min(punch_time) from `tabJEW Punch Audit`")[0][0]
	try:
		from jew_hrms_mobile import liveness_service

		live_mode = liveness_service.get_mode()
	except Exception:
		live_mode = "off"
	return {
		"punches": rows,
		"range": {"from": str(f), "to": str(t)},
		"generated_at": str(now_datetime()),
		"face_limit": _face_limit(),
		"retention_days": _retention_days(),
		"liveness_mode": live_mode,
		"capture_since": str(get_datetime(since).date()) if since else None,
	}


@frappe.whitelist()
def get_punch_detail(checkin=None):
	"""Extra detail for the drawer: map geometry, device history, registered-face availability."""
	_require_access()
	c = frappe.db.get_value("Employee Checkin", checkin, ["name", "employee", "time", "latitude", "longitude"], as_dict=True)
	if not c:
		frappe.throw(_("Punch not found"), frappe.DoesNotExistError)
	a = frappe.db.get_value(DOCTYPE, {"checkin": checkin}, ["name", "location_name", "device_id", "reviewed_by", "reviewed_on"], as_dict=True)
	out = {"checkin": checkin, "audit": a.name if a else None, "angle": None, "centre": None,
	       "device_history": [], "primary_device": None, "registered_face": False,
	       "reviewed_by": a.reviewed_by if a else None, "reviewed_on": str(a.reviewed_on) if a and a.reviewed_on else None}
	loc_name = (a.location_name if a else None)
	centre = None
	if loc_name:
		centre = frappe.db.get_value("JEW Attendance Location", {"location_name": ["like", loc_name.strip() + "%"], "is_active": 1}, ["latitude", "longitude"], as_dict=True)
	if centre and flt(c.latitude) and flt(c.longitude):
		lat0, lon0 = flt(centre.latitude), flt(centre.longitude)
		dx = (flt(c.longitude) - lon0) * math.cos(math.radians(lat0))
		dy = flt(c.latitude) - lat0
		out["angle"] = math.atan2(-dy, dx)
		out["centre"] = {"lat": lat0, "lon": lon0}
	if a and a.device_id:
		since = add_days(today(), -30)
		hist = frappe.db.sql(
			"""select employee, employee_name, count(*) n, max(punch_time) last from `tabJEW Punch Audit`
			where device_id = %s and punch_time >= %s group by employee, employee_name order by n desc""",
			(a.device_id, since), as_dict=True,
		)
		primary = _primary_devices()
		out["device_history"] = [{"employee": h.employee, "name": h.employee_name, "n": h.n, "last": str(get_datetime(h.last).date()),
		                          "usual": primary.get(h.employee) == a.device_id} for h in hist]
	out["primary_device"] = _primary_devices().get(c.employee)
	out["registered_face"] = bool(frappe.db.get_value("JEW Employee Face", {"employee": c.employee, "is_active": 1, "face_image": ["is", "set"]}, "name"))
	return out


def _read_private_file(url):
	if not url:
		return None
	path = os.path.realpath(frappe.get_site_path(*[p for p in str(url).split("/") if p]))
	base = os.path.realpath(frappe.get_site_path())
	if not path.startswith(base + os.sep) or not os.path.isfile(path):
		return None
	with open(path, "rb") as fh:
		return fh.read()


@frappe.whitelist()
def get_punch_photo(name=None, kind="punch", employee=None):
	"""The punch photo (JEW Punch Audit row) or the employee's registered face, as an image response."""
	_require_access()
	if kind == "face":
		emp = employee or (frappe.db.get_value(DOCTYPE, name, "employee") if name else None)
		url = frappe.db.get_value("JEW Employee Face", {"employee": emp, "is_active": 1}, "face_image") if emp else None
	else:
		url = frappe.db.get_value(DOCTYPE, name, "punch_photo") if name else None
	content = _read_private_file(url)
	if not content:
		frappe.throw(_("Photo not available"), frappe.DoesNotExistError)
	frappe.local.response.filename = "punch.jpg"
	frappe.local.response.filecontent = content
	frappe.local.response.type = "binary"


@frappe.whitelist()
def review_punch(checkin=None, reviewed=1, note=None):
	"""Mark a punch reviewed (or undo) and/or save an HR note. Creates a minimal audit row for older punches."""
	_require_access()
	c = frappe.db.get_value("Employee Checkin", checkin, ["name", "employee", "employee_name", "log_type", "time", "device_id"], as_dict=True)
	if not c:
		frappe.throw(_("Punch not found"), frappe.DoesNotExistError)
	name = frappe.db.get_value(DOCTYPE, {"checkin": checkin}, "name")
	if name:
		doc = frappe.get_doc(DOCTYPE, name)
	else:
		doc = frappe.get_doc({"doctype": DOCTYPE, "employee": c.employee, "employee_name": c.employee_name, "checkin": c.name,
		                      "log_type": c.log_type, "punch_time": c.time, "source": _source_of(c.device_id)})
		doc.insert(ignore_permissions=True)
	doc.reviewed = 1 if cint(reviewed) else 0
	doc.reviewed_by = frappe.session.user if doc.reviewed else None
	doc.reviewed_on = now_datetime() if doc.reviewed else None
	if note is not None:
		doc.review_note = (note or "")[:1000] or None
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"audit": doc.name, "reviewed": bool(doc.reviewed), "note": doc.review_note or ""}


@frappe.whitelist()
def get_settings():
	_require_access()
	return {"retention_days": _retention_days(), "choices": list(RETENTION_CHOICES)}


@frappe.whitelist()
def save_settings(retention_days=None):
	_require_access()
	days = cint(retention_days)
	if days not in RETENTION_CHOICES:
		frappe.throw(_("Retention must be one of: {0}").format(", ".join(str(x) for x in RETENTION_CHOICES)))
	frappe.db.set_default(RETENTION_KEY, str(days))
	frappe.db.commit()
	return {"retention_days": days}


# ----------------------------------------------------------------------------- filters + export
FLAG_TEXT = {"SHARED": "Shared device", "NEWDEV": "New device", "DRIFT": "Clock drift", "FLAT": "Flat face score",
             "CENTRE": "GPS at centre", "MANUAL": "HR manual"}


def _apply_filters(rows, f):
	f = f or {}
	q = str(f.get("q") or "").strip().lower()
	dv = str(f.get("dev") or "").strip().lower()
	kpi = f.get("kpi")
	out = []
	for r in rows:
		if q and not (q in str(r["name"] or "").lower() or q in str(r["employee"] or "").lower()):
			continue
		if f.get("dept") and r["dept"] != f["dept"]:
			continue
		if f.get("loc") and (r["loc"] or "") != f["loc"]:
			continue
		if f.get("type") and r["type"] != f["type"]:
			continue
		if f.get("src") and r["source"] != f["src"]:
			continue
		if dv and not (dv in str(r["dev"] or "").lower() or dv in str(r["ip"] or "").lower()):
			continue
		if (f.get("only_flag") or kpi == "flag") and not r["flagged"]:
			continue
		if f.get("only_photo") and not r["photo"]:
			continue
		if kpi == "shared" and not any(x["k"] == "SHARED" for x in r["flags"]):
			continue
		if kpi == "drift" and not any(x["k"] == "DRIFT" for x in r["flags"]):
			continue
		out.append(r)
	return out


def _flag_label(x):
	if x["k"] == "SHARED":
		return "Shared device (%s employees)" % x.get("n")
	if x["k"] == "DRIFT":
		return "Clock %+d min" % round(x["v"] / 60)
	return FLAG_TEXT.get(x["k"], x["k"])


@frappe.whitelist()
def export_punch_audit(range=None, from_date=None, to_date=None, filters=None):
	_require_access()
	f, t, rows = _punch_rows(range, from_date, to_date)
	try:
		flt_args = json.loads(filters) if filters else {}
	except Exception:
		flt_args = {}
	rows = _apply_filters(rows, flt_args)
	data = [["Date", "Time", "Employee ID", "Employee", "Department", "Punch", "Source", "Added by", "Location", "Distance (m)", "Radius (m)",
	         "Face distance", "Device ID", "Device model", "OS", "Browser", "IP address", "Clock drift (s)", "Flags", "Reviewed", "HR note", "Photo link"]]
	for r in rows:
		data.append([
			r["date"], "%02d:%02d:%02d" % (r["t"] // 60, r["t"] % 60, r["sec"]), r["employee"], r["name"], r["dept"], r["type"], r["source"], r["added_by"] or "",
			r["loc"] or "", r["dist"] if r["dist"] is not None else "", r["radius"] if r["radius"] is not None else "",
			r["face"] if r["face"] is not None else "", r["dev"] or "", r["model"] or "", r["os"] or "", r["browser"] or "", r["ip"] or "",
			r["drift"] if r["drift"] is not None else "", ", ".join(_flag_label(x) for x in r["flags"]), "Yes" if r["reviewed"] else "", r["note"],
			(get_url() + "/api/method/jew_hrms_mobile.punch_audit.get_punch_photo?name=%s&kind=punch" % r["audit"]) if r["photo"] and r["audit"] else "",
		])
	xlsx = make_xlsx(data, "Punch audit", column_widths=[11, 9, 14, 24, 18, 7, 11, 22, 18, 11, 10, 11, 14, 20, 12, 18, 16, 11, 34, 9, 30, 60], header_index=0)
	provide_binary_file("Punch audit %s to %s" % (f, t), "xlsx", xlsx.getvalue())


# ----------------------------------------------------------------------------- retention
def purge_old_photos(limit=500):
	"""Delete punch photos older than the retention period (keeps the audit rows). Safe to call daily."""
	try:
		cutoff = add_days(now_datetime(), -_retention_days())
		rows = frappe.get_all(DOCTYPE, filters={"punch_photo": ["is", "set"], "punch_time": ["<", cutoff]},
		                      fields=["name", "punch_photo"], limit_page_length=limit)
		n = 0
		for r in rows:
			for fname in frappe.get_all("File", filters={"file_url": r.punch_photo, "attached_to_doctype": DOCTYPE, "attached_to_name": r.name}, pluck="name"):
				frappe.delete_doc("File", fname, force=1, ignore_permissions=True)
			frappe.db.set_value(DOCTYPE, r.name, "punch_photo", None, update_modified=False)
			n += 1
		if n:
			frappe.db.commit()
		return n
	except Exception:
		frappe.log_error(frappe.get_traceback(), "JEW HRMS punch audit purge failed")
		return 0


def daily_purge(batch=500, max_batches=40):
	"""Scheduler entry for the daily "delete old punch photos" job (hooks.py `daily` + its own Scheduled Job Type).
	Runs purge_old_photos in batches until nothing is left to delete, so lowering the retention days clears the whole
	backlog in one run instead of 500 a day. Returns how many photos were deleted."""
	total = 0
	for _ in range(max_batches):
		deleted = purge_old_photos(batch)
		total += deleted
		if deleted < batch:
			break
	return total


# ----------------------------------------------------------------------------- not punched
NP_MARKED_STATUSES = ("Present", "Work From Home", "Half Day")


def _days_between(f, t):
	out, d, end = [], getdate(f), getdate(t)
	while d <= end:
		out.append(d)
		d = getdate(add_days(d, 1))
	return out


def _take(rem, days):
	"""Remove `days` from the not-yet-accounted-for set `rem`; return how many it actually covered."""
	hit = rem & days
	rem.difference_update(hit)
	return len(hit)


def _not_punched_rows(f, t):
	"""Active employees with NO Employee Checkin in [f, t], each with a note on why: approved leave, on duty / WFH,
	HR-marked attendance, holiday, or marked absent. `covered` = every day of the range is accounted for by leave /
	duty / HR-marked attendance / holiday, so nobody needs chasing. Read-only."""
	days = _days_between(f, t)
	day_set = set(days)
	arg = {"f": "%s 00:00:00" % f, "t": str(t), "t1": "%s 00:00:00" % add_days(t, 1), "d0": str(f)}
	emps = frappe.db.sql(
		"""
		select e.name, e.employee_name, e.department, e.designation, e.company, e.holiday_list, e.date_of_joining,
		       ifnull(e.user_id, '') as user_id
		from `tabEmployee` e
		where e.status = 'Active'
		  and (e.date_of_joining is null or e.date_of_joining <= %(t)s)
		  and not exists (select 1 from `tabEmployee Checkin` c where c.employee = e.name and c.time >= %(f)s and c.time < %(t1)s)
		order by e.employee_name
		""",
		arg, as_dict=True,
	)
	out = {"days": len(days), "employees": [], "active_total": cint(frappe.db.count("Employee", {"status": "Active"}))}
	if not emps:
		return out
	arg["n"] = tuple(e.name for e in emps)
	today_d = getdate(today())

	last = {}
	for r in frappe.db.sql("select employee, max(time) from `tabEmployee Checkin` where employee in %(n)s group by employee", arg):
		last[r[0]] = getdate(r[1])

	leave, lhalf, od, marked, absent = (defaultdict(set) for _ in range(5))
	for la in frappe.db.sql(
		"""select employee, from_date, to_date, half_day, half_day_date from `tabLeave Application`
		where docstatus = 1 and status = 'Approved' and employee in %(n)s and from_date <= %(t)s and to_date >= %(d0)s""",
		arg, as_dict=True,
	):
		a, b = getdate(la.from_date), getdate(la.to_date)
		full = set(_days_between(max(a, f), min(b, t)))
		if cint(la.half_day):
			hd = getdate(la.half_day_date) if la.half_day_date else (a if a == b else None)
			if hd and hd in full:       # a half-day leave does not cover the day: the person should still punch
				full.discard(hd)
				lhalf[la.employee].add(hd)
		leave[la.employee] |= full
	for ar in frappe.db.sql(
		"""select employee, from_date, to_date from `tabAttendance Request`
		where docstatus = 1 and employee in %(n)s and from_date <= %(t)s and to_date >= %(d0)s""",
		arg, as_dict=True,
	):
		od[ar.employee] |= set(_days_between(max(getdate(ar.from_date), f), min(getdate(ar.to_date), t)))
	for a in frappe.db.sql(
		"""select employee, attendance_date, status from `tabAttendance`
		where docstatus = 1 and employee in %(n)s and attendance_date between %(d0)s and %(t)s""",
		arg, as_dict=True,
	):
		d = getdate(a.attendance_date)
		if a.status == "On Leave":
			leave[a.employee].add(d)
		elif a.status in NP_MARKED_STATUSES:
			marked[a.employee].add(d)
		elif a.status == "Absent":
			absent[a.employee].add(d)

	hol = defaultdict(set)
	for h in frappe.db.sql("select parent, holiday_date from `tabHoliday` where parenttype = 'Holiday List' and holiday_date between %s and %s", (str(f), str(t))):
		hol[h[0]].add(getdate(h[1]))
	company_hl = dict(frappe.db.sql("select name, default_holiday_list from `tabCompany`"))

	for e in emps:
		rem = set(day_set)
		if e.date_of_joining:
			rem.difference_update({d for d in rem if d < getdate(e.date_of_joining)})
		n_leave = _take(rem, leave[e.name])
		n_od = _take(rem, od[e.name])
		n_marked = _take(rem, marked[e.name])
		n_hol = _take(rem, hol.get(e.holiday_list or company_hl.get(e.company), set()))
		ld = last.get(e.name)
		out["employees"].append({
			"id": e.name, "name": str(e.employee_name or e.name).strip(), "dept": e.department or "", "desig": e.designation or "", "company": e.company or "",
			"login": 1 if e.user_id else 0, "last": str(ld) if ld else None, "ago": (today_d - ld).days if ld else None,
			"leave": n_leave, "half": len(lhalf[e.name] & day_set), "od": n_od, "marked": n_marked, "hol": n_hol,
			"absent": len(absent[e.name] & day_set), "covered": 0 if rem else 1,
		})
	return out


@frappe.whitelist()
def get_not_punched(range=None, from_date=None, to_date=None):
	"""Active employees with no punch at all in the range (e.g. range=today: who has not punched yet today)."""
	_require_access()
	f, t = _resolve_range(range, from_date, to_date)
	res = _not_punched_rows(f, t)
	res.update({"range": {"from": str(f), "to": str(t)}, "generated_at": str(now_datetime())})
	return res


def _np_filter(rows, f):
	f = f or {}
	q = str(f.get("q") or "").strip().lower()
	out = []
	for r in rows:
		if q and not (q in str(r["name"] or "").lower() or q in str(r["id"] or "").lower()):
			continue
		if f.get("dept") and r["dept"] != f["dept"]:
			continue
		if cint(f.get("only_login")) and not r["login"]:
			continue
		if cint(f.get("hide_covered")) and r["covered"]:
			continue
		out.append(r)
	return out


@frappe.whitelist()
def export_not_punched(range=None, from_date=None, to_date=None, filters=None):
	_require_access()
	f, t = _resolve_range(range, from_date, to_date)
	try:
		flt_args = json.loads(filters) if filters else {}
	except Exception:
		flt_args = {}
	rows = _np_filter(_not_punched_rows(f, t)["employees"], flt_args)
	rows.sort(key=lambda r: (r["covered"], r["dept"] or "~", str(r["name"]).lower()))
	data = [["Employee ID", "Employee", "Department", "Designation", "Company", "App login", "Last punch", "Days since last punch",
	         "Leave days", "On duty days", "Attendance marked days", "Holiday days", "Marked absent days", "Status"]]
	for r in rows:
		data.append([
			r["id"], r["name"], r["dept"], r["desig"], r["company"], "Yes" if r["login"] else "No", r["last"] or "Never punched",
			r["ago"] if r["ago"] is not None else "", r["leave"] or "", r["od"] or "", r["marked"] or "", r["hol"] or "", r["absent"] or "",
			"Accounted for (leave / duty / marked / holiday)" if r["covered"] else "No punch",
		])
	xlsx = make_xlsx(data, "Not punched", column_widths=[14, 26, 20, 22, 22, 10, 14, 12, 10, 12, 14, 12, 14, 36], header_index=0)
	provide_binary_file("Not punched %s to %s" % (f, t), "xlsx", xlsx.getvalue())


# ----------------------------------------------------------------------------- exceptions (late / early / regularization / on duty / leave, employee-wise and day by day)
EX_REG_ISSUES = ("Missing Mark In", "Missing Mark Out", "Missing Mark In & Out", "Short Hours")
LEAVE_STAGE = {"Pending Department Head Approval": "Waiting for Dept Head", "Pending MD Approval": "Waiting for MD", "Pending HR Approval": "Waiting for HR"}
LE_STAGE = {"Pending": "Waiting for Dept Head", "Pending HR": "Waiting for HR"}
OD_STAGE = {"Pending": "Pending approval", "Pending Dept Head": "Waiting for Dept Head"}
MANUAL_DEVICES = ("", "Manual (HR)")


def _hhmm(dt):
	return get_datetime(dt).strftime("%H:%M") if dt else None


def _tstr(v):
	"""Time / timedelta -> 'HH:MM'."""
	try:
		p = str(v).split(":")
		return "%02d:%02d" % (int(p[0]), int(p[1]))
	except Exception:
		return ""


def _exceptions(f, t):
	"""Everything HR needs about attendance exceptions in [f, t], read-only: per employee counters + one small cell per day
	(status code, IN/OUT, late/early by punch time and whether the system counted it, leave / late-early / on-duty /
	regularization items with their approval stage) + the list of items still waiting for an approval."""
	from datetime import datetime, time as dtime

	api = _api()
	days = _days_between(f, t)
	today_d = getdate(today())
	arg = {"f": "%s 00:00:00" % f, "t1": "%s 00:00:00" % add_days(t, 1), "d0": str(f), "d1": str(t)}
	cells = defaultdict(dict)          # emp -> {date: cell}
	pend_names = defaultdict(set)
	pending = []

	def cell(emp, d):
		return cells[emp].setdefault(str(d), {})

	def age(creation):
		return max(0, (today_d - getdate(creation)).days) if creation else 0

	def add_pending(emp, kind, what, frm, to, stage, creation, name):
		if name in pend_names[emp]:
			return
		pend_names[emp].add(name)
		pending.append({"emp": emp, "kind": kind, "what": what, "from": str(frm), "to": str(to), "stage": stage, "age": age(creation), "name": name})

	def short(s):
		return str(s or "").replace("Department Head", "Dept Head")

	punches = defaultdict(lambda: defaultdict(list))
	for p in frappe.db.sql("select employee, log_type, time, ifnull(device_id, '') as dev from `tabEmployee Checkin` where time >= %(f)s and time < %(t1)s order by time", arg, as_dict=True):
		punches[p.employee][str(getdate(p.time))].append(p)
	att = {}
	for a in frappe.db.sql("select employee, attendance_date, status from `tabAttendance` where docstatus = 1 and attendance_date between %(d0)s and %(d1)s", arg, as_dict=True):
		att[(a.employee, str(a.attendance_date))] = a.status

	for la in frappe.db.sql(
		"""select name, employee, leave_type, from_date, to_date, half_day, half_day_date, docstatus, status, ifnull(jew_hrms_approval_status, '') as stage,
		          ifnull(jew_cancel_status, '') as cancel, jew_cancel_requested_on as cancel_on, creation
		   from `tabLeave Application` where docstatus < 2 and from_date <= %(d1)s and to_date >= %(d0)s""", arg, as_dict=True):
		if la.docstatus == 1 and la.status == "Rejected":
			k, st = "rej", short(la.stage) or "Rejected"
		elif la.docstatus == 1 and la.status == "Approved":
			k, st = "ok", "Approved"
		elif la.docstatus == 0 and la.status == "Open":
			k, st = "pend", LEAVE_STAGE.get(la.stage, "Pending")
		else:
			continue
		cx = k == "ok" and la.cancel.startswith("Pending")
		a, b = getdate(la.from_date), getdate(la.to_date)
		hd = getdate(la.half_day_date) if cint(la.half_day) and la.half_day_date else (a if cint(la.half_day) and a == b else None)
		for d in _days_between(max(a, f), min(b, t)):
			cell(la.employee, d).setdefault("lv", []).append({"t": la.leave_type, "st": st, "k": k, "h": 1 if hd == d else 0, "cx": 1 if cx else 0, "age": age(la.creation) if k == "pend" else 0})
		if k == "pend":
			add_pending(la.employee, "Leave", la.leave_type + (" (half day)" if hd else ""), a, b, st, la.creation, la.name)
		if cx:
			add_pending(la.employee, "Leave cancellation", la.leave_type, a, b, "Waiting for HR", la.cancel_on or la.creation, la.name + ":cx")

	for le in frappe.db.sql(
		"""select name, employee, application_type, application_date, expected_time, status, ifnull(jew_cancel_status, '') as cancel,
		          jew_cancel_requested_on as cancel_on, creation
		   from `tabJEW Late Early Application` where application_date between %(d0)s and %(d1)s and status <> 'Cancelled'""", arg, as_dict=True):
		k = "ok" if le.status == "Approved" else "rej" if le.status == "Rejected" else "pend"
		st = "Approved" if k == "ok" else "Rejected" if k == "rej" else LE_STAGE.get(le.status, "Pending")
		cx = le.cancel.startswith("Pending")
		x = _tstr(le.expected_time) if le.expected_time else ""
		cell(le.employee, le.application_date).setdefault("le", []).append({"t": le.application_type, "x": x, "st": st, "k": k, "cx": 1 if cx else 0, "age": age(le.creation) if k == "pend" else 0})
		what = "%s%s" % (le.application_type, " " + x if x else "")
		if k == "pend":
			add_pending(le.employee, "Late/Early", what, le.application_date, le.application_date, st, le.creation, le.name)
		if cx:
			add_pending(le.employee, "Late/Early cancellation", what, le.application_date, le.application_date, "Waiting for HR", le.cancel_on or le.creation, le.name + ":cx")

	for ar in frappe.db.sql(
		"""select name, employee, from_date, to_date, reason, docstatus, ifnull(jew_od_status, '') as s, creation
		   from `tabAttendance Request` where docstatus < 2 and from_date <= %(d1)s and to_date >= %(d0)s""", arg, as_dict=True):
		if ar.docstatus == 1:
			k, st = "ok", "Approved"
		elif ar.s == "Rejected":
			k, st = "rej", "Rejected"
		else:
			k, st = "pend", OD_STAGE.get(ar.s, "Pending approval")
		label = ar.reason or "On duty"
		a, b = getdate(ar.from_date), getdate(ar.to_date)
		for d in _days_between(max(a, f), min(b, t)):
			cell(ar.employee, d).setdefault("od", []).append({"t": label, "st": st, "k": k, "age": age(ar.creation) if k == "pend" else 0})
		if k == "pend":
			add_pending(ar.employee, "On duty", label, a, b, st, ar.creation, ar.name)

	for r in frappe.db.sql("select name, employee, attendance_date, issue_type, status, creation from `tabJEW Attendance Regularization` where attendance_date between %(d0)s and %(d1)s", arg, as_dict=True):
		c = cell(r.employee, r.attendance_date)
		rejected = r.status == "Rejected"
		if r.issue_type == "Late Arrival":
			if not rejected:
				c["ls"] = 1                          # the system counted this late day
				if r.status == "Marked Half Day":
					c["lah"] = 1
			continue
		if r.issue_type in ("Late Coming", "Late Coming Check-in") and not rejected:
			c["lc"] = 1
		if r.issue_type == "Early Going" and not rejected:
			c["es"] = 1
		c.setdefault("rg", []).append({"i": r.issue_type, "st": r.status, "k": "pend" if r.status == "Pending" else "rej" if rejected else "ok"})
		if r.status == "Pending":
			add_pending(r.employee, "Regularization", r.issue_type, r.attendance_date, r.attendance_date, "Waiting for HR", r.creation, r.name)

	emps = frappe.db.sql(
		"""select name, employee_name, department, designation, company, holiday_list, default_shift, date_of_joining, ifnull(user_id, '') as user_id
		   from `tabEmployee` where status = 'Active'""", as_dict=True)
	hol = defaultdict(set)
	for h in frappe.db.sql("select parent, holiday_date from `tabHoliday` where parenttype = 'Holiday List' and holiday_date between %s and %s", (str(f), str(t))):
		hol[h[0]].add(str(getdate(h[1])))
	company_hl = dict(frappe.db.sql("select name, default_holiday_list from `tabCompany`"))
	pols = {}

	def policy_for(e):
		key = e.default_shift or ""
		if key not in pols:
			try:
				pols[key] = api._get_active_policy(employee=e.name, shift=e.default_shift or None)
			except Exception:
				pols[key] = None
		return pols[key]

	emp_out, cells_out, popset = [], {}, set()
	for e in emps:
		if not (e.user_id or e.name in cells or e.name in punches):
			continue
		popset.add(e.name)
		pol = policy_for(e)
		start = end = None
		grace_out = 0
		if pol:
			start = api._parse_time(pol.shift_start_time, dtime(9, 30))
			end = api._parse_time(pol.shift_end_time, dtime(18, 30))
			grace_out = int(pol.early_going_grace_minutes or 0)
		holset = hol.get(e.holiday_list or company_hl.get(e.company), set())
		joined = getdate(e.date_of_joining) if e.date_of_joining else None
		ecells = cells.get(e.name, {})
		mine = punches.get(e.name, {})
		for d in days:
			ds = str(d)
			if joined and d < joined:
				continue
			c = ecells.get(ds) or {}
			day_p = mine.get(ds, [])
			ins = [p for p in day_p if p.log_type == "IN"]
			outs = [p for p in day_p if p.log_type == "OUT"]
			if ins:
				c["in"] = _hhmm(min(p.time for p in ins))
			if outs:
				c["out"] = _hhmm(max(p.time for p in outs))
			real_in = [p.time for p in ins if p.dev not in MANUAL_DEVICES]
			real_out = [p.time for p in outs if p.dev not in MANUAL_DEVICES]
			if start and real_in:
				mins = (min(real_in) - datetime.combine(d, start)).total_seconds() / 60.0
				if mins > api.LATE_ARRIVAL_GRACE_MINUTES:
					c["late"] = int(mins)
			if end and real_out:
				mins = (datetime.combine(d, end) - max(real_out)).total_seconds() / 60.0
				if mins > grace_out:
					c["early"] = int(mins)
			lv, od = c.get("lv", []), c.get("od", [])
			astat = att.get((e.name, ds))
			inc = bool(ins) and not outs and d < today_d
			if inc:
				c["i"] = 1
				if not astat and not any(x["k"] == "ok" for x in lv + od):
					c["na"] = 1
			code = ""
			if any(x["k"] == "ok" and not x["h"] for x in lv):
				code = "L"
			elif any(x["k"] == "ok" for x in od):
				code = "OD"
			elif astat in ("Present", "Work From Home"):
				code = "P"
			elif astat == "Half Day":
				code = "HD"
			elif astat == "On Leave" or any(x["k"] == "ok" for x in lv):
				code = "L"
			elif any(x["k"] == "pend" for x in lv):
				code = "L?"
			elif any(x["k"] == "pend" for x in od):
				code = "OD?"
			elif astat == "Absent":
				code = "A"
			elif day_p:
				code = "I" if inc else "P"
			elif ds in holset:
				code = "H"
			if code:
				c["s"] = code
			if c:
				ecells[ds] = c
		cnt = {"late_sys": 0, "late_punch": 0, "early_sys": 0, "early_punch": 0, "reg": 0, "reg_p": 0, "od_days": 0, "leave_days": 0, "leave_p": 0, "inc": 0, "half": 0,
		       "pend": len(pend_names.get(e.name, ()))}
		for ds, c in ecells.items():
			cnt["late_sys"] += 1 if c.get("ls") else 0
			cnt["late_punch"] += 1 if c.get("late") else 0
			cnt["early_sys"] += 1 if c.get("es") else 0
			cnt["early_punch"] += 1 if c.get("early") else 0
			cnt["inc"] += 1 if c.get("i") else 0
			cnt["half"] += 1 if c.get("s") == "HD" else 0
			for x in c.get("rg", []):
				if x["i"] in EX_REG_ISSUES and x["k"] != "rej":
					cnt["reg"] += 1
				if x["k"] == "pend":
					cnt["reg_p"] += 1
			if ds not in holset:
				for x in c.get("lv", []):
					amt = 0.5 if x["h"] else 1
					if x["k"] == "ok":
						cnt["leave_days"] += amt
					elif x["k"] == "pend":
						cnt["leave_p"] += amt
				cnt["od_days"] += sum(1 for x in c.get("od", []) if x["k"] == "ok")
		emp_out.append({"id": e.name, "name": str(e.employee_name or e.name).strip(), "dept": e.department or "", "desig": e.designation or "",
		                "login": 1 if e.user_id else 0, "c": cnt})
		cells_out[e.name] = {ds: c for ds, c in ecells.items() if c}
	return {"days": [str(d) for d in days], "today": str(today_d), "employees": emp_out, "cells": cells_out,
	        "pending": sorted([p for p in pending if p["emp"] in popset], key=lambda p: -p["age"])}


@frappe.whitelist()
def get_exceptions(range=None, from_date=None, to_date=None):
	"""Late / early / regularization / on duty / leave for every app user, employee-wise and day by day (read-only)."""
	_require_access()
	f, t = _resolve_range(range, from_date, to_date)
	res = _exceptions(f, t)
	res.update({"range": {"from": str(f), "to": str(t)}, "generated_at": str(now_datetime())})
	return res


def _ex_match(c, chip):
	return (chip in (None, "", "all")
	        or (chip == "late" and (c["late_sys"] or c["late_punch"]))
	        or (chip == "early" and (c["early_sys"] or c["early_punch"]))
	        or (chip == "reg" and (c["reg"] or c["reg_p"]))
	        or (chip == "od" and c["od_days"])
	        or (chip == "leave" and (c["leave_days"] or c["leave_p"]))
	        or (chip == "inc" and c["inc"])
	        or (chip == "pend" and c["pend"]))


@frappe.whitelist()
def export_exceptions(range=None, from_date=None, to_date=None, filters=None):
	_require_access()
	f, t = _resolve_range(range, from_date, to_date)
	try:
		fl = json.loads(filters) if filters else {}
	except Exception:
		fl = {}
	res = _exceptions(f, t)
	q = str(fl.get("q") or "").strip().lower()
	rows = [e for e in res["employees"]
	        if (not q or q in e["name"].lower() or q in e["id"].lower()) and (not fl.get("dept") or e["dept"] == fl["dept"]) and _ex_match(e["c"], fl.get("chip"))]
	rows.sort(key=lambda e: e["name"].lower())
	grid = len(res["days"]) <= 31
	head = ["Employee ID", "Employee", "Department", "Designation", "App login", "Late (system)", "Late (punch time)", "Early (system)", "Early (punch time)",
	        "Regularization", "Pending regularization", "On duty days", "Leave days", "Pending leave days", "Incomplete days", "Half days", "Pending approvals"]
	if grid:
		head += [d[5:] for d in res["days"]]
	data = [head]
	for e in rows:
		c = e["c"]
		line = [e["id"], e["name"], e["dept"], e["desig"], "Yes" if e["login"] else "No", c["late_sys"], c["late_punch"], c["early_sys"], c["early_punch"],
		        c["reg"], c["reg_p"], c["od_days"], c["leave_days"], c["leave_p"], c["inc"], c["half"], c["pend"]]
		if grid:
			mine = res["cells"].get(e["id"], {})
			for d in res["days"]:
				x = mine.get(d) or {}
				line.append((x.get("s") or "") + (" late" if x.get("late") else ""))
		data.append(line)
	widths = [14, 26, 20, 22, 10, 11, 12, 11, 12, 13, 14, 12, 10, 14, 13, 9, 14] + ([9] * (len(head) - 17))
	xlsx = make_xlsx(data, "Exceptions", column_widths=widths, header_index=0)
	provide_binary_file("Exceptions %s to %s" % (f, t), "xlsx", xlsx.getvalue())
