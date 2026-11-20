"""Face registration status mail for a staff group, sent every 2 hours from the frappe user's crontab.

  python report.py 6j          -> MPUSIP 6J Chhatarpur (60 staff)
  python report.py 7c          -> Satna 7C (21 staff)
  add --dry                    -> build only, write preview_<group>.html, send nothing
  add --test                   -> subject starts with "TEST - "
To stop: remove the crontab lines tagged face_status_report (crontab -u frappe -e).
"""
import datetime
import os
import sys

DIR = "/home/frappe/face_status_report"
TO = ["ajaymishra2711@gmail.com", "abhijeet.jha@thesvsgroup.org"]
SITE = "jewipl.duxdigitech.in"
DRY = "--dry" in sys.argv
TEST = "--test" in sys.argv

GROUPS = {
	"6j": {
		"title": "MPUSIP 6J Chhatarpur",
		"short": "6J Chhatarpur",
		"staff": ["HR-EMP-00068", "HR-EMP-00089", "HR-EMP-00178", "HR-EMP-00182", "HR-EMP-00189", "HR-EMP-00205", "HR-EMP-00216", "HR-EMP-00220",
		          "HR-EMP-00238", "HR-EMP-00254", "HR-EMP-00288", "HR-EMP-00291", "HR-EMP-00295", "HR-EMP-00298", "HR-EMP-00305", "HR-EMP-00306",
		          "HR-EMP-00308", "HR-EMP-00324", "HR-EMP-00339", "HR-EMP-00379", "HR-EMP-00402", "HR-EMP-00494", "HR-EMP-00498", "HR-EMP-00595",
		          "HR-EMP-00626", "HR-EMP-00671", "HR-EMP-00677", "HR-EMP-00678", "HR-EMP-00683", "HR-EMP-00684", "HR-EMP-00688", "HR-EMP-00690",
		          "HR-EMP-00691", "HR-EMP-00697", "HR-EMP-00698", "HR-EMP-00699", "HR-EMP-00700", "HR-EMP-00702", "HR-EMP-00709", "HR-EMP-00728",
		          "HR-EMP-00733", "HR-EMP-00776", "HR-EMP-00784", "HR-EMP-00785", "HR-EMP-00804", "HR-EMP-00805", "HR-EMP-00806", "HR-EMP-00808",
		          "HR-EMP-00809", "HR-EMP-00810", "HR-EMP-00816", "HR-EMP-00817", "HR-EMP-00818", "HR-EMP-00819", "HR-EMP-00820", "HR-EMP-00821",
		          "HR-EMP-00822", "HR-EMP-00823", "HR-EMP-00824", "HR-EMP-00825"],
	},
	"7c": {
		"title": "Satna 7C",
		"short": "Satna 7C",
		"staff": ["HR-EMP-00737", "HR-EMP-00797", "HR-EMP-00214", "HR-EMP-00484", "HR-EMP-00338", "HR-EMP-00382", "HR-EMP-00451", "HR-EMP-00171",
		          "HR-EMP-00180", "HR-EMP-00738", "HR-EMP-00739", "HR-EMP-00275", "HR-EMP-00750", "HR-EMP-00828", "HR-EMP-00829", "HR-EMP-00751",
		          "HR-EMP-00759", "HR-EMP-00830", "HR-EMP-00752", "HR-EMP-00831", "HR-EMP-00832"],
	},
}

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args or args[0] not in GROUPS:
	print("usage: report.py <%s> [--dry] [--test]" % "|".join(GROUPS))
	sys.exit(2)
KEY = args[0]
G = GROUPS[KEY]
STAFF = G["staff"]


def log(*a):
	print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), "[%s]" % KEY, *a, flush=True)


import frappe  # noqa: E402
from frappe.utils import escape_html, format_datetime, now_datetime  # noqa: E402

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
from jew_hrms_mobile import face_approval  # noqa: E402


def fmt(dt):
	return format_datetime(dt, "dd-MM-yyyy hh:mm a") if dt else ""


def user_name(email):
	if not email:
		return ""
	return frappe.db.get_value("User", email, "full_name") or frappe.db.get_value("User", {"email": email}, "full_name") or email


emps = {e.name: e for e in frappe.get_all("Employee", filters={"name": ["in", STAFF]}, fields=["name", "employee_name", "designation", "status"])}
faces = frappe.get_all("JEW Employee Face", filters={"employee": ["in", STAFF]},
                       fields=["name", "employee", "approval_status", "is_active", "approver_email", "decided_by", "decided_on",
                               "registered_on", "decision_remarks"], order_by="registered_on asc")
by_emp = {}
for f in faces:
	by_emp.setdefault(f.employee, []).append(f)

approved, pending, rejected, missing = [], [], [], []
for emp_id in STAFF:
	e = emps.get(emp_id) or frappe._dict(name=emp_id, employee_name=emp_id, designation="")
	rows = by_emp.get(emp_id, [])
	active = [r for r in rows if r.is_active]
	pend = [r for r in rows if r.approval_status == "Pending Approval"]
	rej = [r for r in rows if r.approval_status == "Rejected"]
	if active:
		approved.append((e, active[-1], pend[-1] if pend else None))
	elif pend:
		pending.append((e, pend[-1]))
	elif rej:
		rejected.append((e, rej[-1]))
	else:
		missing.append(e)

# who approves this group (from the app's own rule: Face Approver, else Dept Head)
appr_count = {}
for emp_id in STAFF:
	a = face_approval.approver_email(emp_id) or ""
	appr_count[a] = appr_count.get(a, 0) + 1
appr_txt = ", ".join("%s (%d)" % (user_name(a) if a else "NO APPROVER", n) for a, n in sorted(appr_count.items(), key=lambda x: -x[1]))

stamp = now_datetime()
when = fmt(stamp)
today = stamp.date()
approved_today = sum(1 for _e, a, _p in approved if a.decided_on and a.decided_on.date() == today)

TH = "padding:6px 8px;border:1px solid #d0d7de;background:#f3f4f6;text-align:left;font-size:13px"
TD = "padding:6px 8px;border:1px solid #d0d7de;font-size:13px;vertical-align:top"


def table(head, body_rows):
	if not body_rows:
		return "<p style='color:#57606a;font-size:13px;margin:4px 0 16px'>None</p>"
	h = "".join("<th style='%s'>%s</th>" % (TH, x) for x in head)
	b = "".join("<tr>%s</tr>" % "".join("<td style='%s'>%s</td>" % (TD, escape_html(str(c or ""))) for c in r) for r in body_rows)
	return "<table style='border-collapse:collapse;width:100%%;margin:4px 0 18px'><tr>%s</tr>%s</table>" % (h, b)


def box(label, n, color):
	return ("<td style='padding:10px 14px;border:1px solid #d0d7de;text-align:center'>"
	        "<div style='font-size:24px;font-weight:700;color:%s'>%d</div><div style='font-size:12px;color:#57606a'>%s</div></td>" % (color, n, label))


summary = (box("Approved", len(approved), "#1a7f37") + box("Pending approval", len(pending), "#9a6700")
           + (box("Rejected", len(rejected), "#cf222e") if rejected else "") + box("Not registered", len(missing), "#57606a")
           + box("Total staff", len(STAFF), "#24292f"))

html = ["<div style='font-family:Arial,Helvetica,sans-serif;color:#24292f;max-width:820px'>"]
html.append("<h2 style='margin:0 0 4px;font-size:18px'>Face registration status - %s (%d staff)</h2>" % (escape_html(G["title"]), len(STAFF)))
html.append("<p style='margin:0 0 12px;color:#57606a;font-size:13px'>As of %s &middot; Face approver: %s</p>" % (when, escape_html(appr_txt)))
html.append("<table style='border-collapse:collapse;margin:0 0 8px'><tr>%s</tr></table>" % summary)
html.append("<p style='margin:0 0 18px;font-size:13px;color:#57606a'>Approved today: %d</p>" % approved_today)
html.append("<h3 style='font-size:15px;margin:0'>Pending approval (%d)</h3>" % len(pending))
html.append(table(["#", "Employee", "ID", "Designation", "Registered on", "Waiting with"],
                  [(i + 1, e.employee_name, e.name, e.designation, fmt(p.registered_on), user_name(p.approver_email))
                   for i, (e, p) in enumerate(sorted(pending, key=lambda x: x[1].registered_on))]))
if rejected:
	html.append("<h3 style='font-size:15px;margin:0'>Rejected - must register again (%d)</h3>" % len(rejected))
	html.append(table(["#", "Employee", "ID", "Designation", "Rejected on", "Reason"],
	                  [(i + 1, e.employee_name, e.name, e.designation, fmt(r.decided_on), r.decision_remarks) for i, (e, r) in enumerate(rejected)]))
html.append("<h3 style='font-size:15px;margin:0'>Not registered yet (%d)</h3>" % len(missing))
html.append(table(["#", "Employee", "ID", "Designation"],
                  [(i + 1, e.employee_name, e.name, e.designation) for i, e in enumerate(sorted(missing, key=lambda x: x.employee_name or ""))]))
html.append("<h3 style='font-size:15px;margin:0'>Approved (%d)</h3>" % len(approved))
html.append(table(["#", "Employee", "ID", "Designation", "Approved on", "Approved by"],
                  [(i + 1, e.employee_name + (" (new face pending)" if p else ""), e.name, e.designation, fmt(a.decided_on or a.registered_on),
                    user_name(a.decided_by) or "HR / admin")
                   for i, (e, a, p) in enumerate(sorted(approved, key=lambda x: str(x[1].decided_on or x[1].registered_on), reverse=True))]))
html.append("<p style='font-size:12px;color:#8c959f;margin-top:8px'>Automatic report from JEW HRMS, sent every hour 9 AM - 5 PM and every 2 hours 5 PM - 9 AM. "
            "Attached Excel: today's Mark IN / OUT time of every employee + last 7 days.</p></div>")

# --------------------------------------------------------------------------- Mark IN / OUT (Excel attachment)
from frappe.utils import add_days, getdate  # noqa: E402

DAYS = [add_days(today, -i) for i in range(6, -1, -1)]  # oldest .. today
loc_name = {l.name: l.location_name for l in frappe.get_all("JEW Attendance Location", fields=["name", "location_name"])}
assigned = {}
for a in frappe.get_all("JEW Employee Location Assignment", filters={"employee": ["in", STAFF], "is_active": 1}, fields=["employee", "location"]):
	n = loc_name.get(a.location)
	if n and n not in assigned.setdefault(a.employee, []):
		assigned[a.employee].append(n)
loc_optional = {e.name for e in frappe.get_all("Employee", filters={"name": ["in", STAFF], "jew_location_optional": 1}, fields=["name"])}

punches = {}
for c in frappe.get_all("Employee Checkin", filters={"employee": ["in", STAFF], "time": [">=", "%s 00:00:00" % DAYS[0]]},
                        fields=["employee", "log_type", "time", "custom_attendance_location"], order_by="time asc"):
	punches.setdefault((c.employee, getdate(c.time)), []).append(c)

leave_days = set()
for la in frappe.get_all("Leave Application", filters={"employee": ["in", STAFF], "docstatus": 1, "status": "Approved",
                                                        "from_date": ["<=", today], "to_date": [">=", DAYS[0]]},
                         fields=["employee", "from_date", "to_date"]):
	for d in DAYS:
		if getdate(la.from_date) <= d <= getdate(la.to_date):
			leave_days.add((la.employee, d))


def day_punch(emp_id, d):
	rows = punches.get((emp_id, d), [])
	ins = [r for r in rows if (r.log_type or "").upper() == "IN"]
	outs = [r for r in rows if (r.log_type or "").upper() == "OUT"]
	first_in = ins[0] if ins else None
	last_out = outs[-1] if outs else None
	if first_in and last_out and last_out.time < first_in.time:
		last_out = None
	return first_in, last_out


def hhmm(r):
	return r.time.strftime("%I:%M %p") if r else ""


def punch_loc(r):
	if not r or not r.custom_attendance_location:
		return ""
	return loc_name.get(r.custom_attendance_location, r.custom_attendance_location)


face_state = {}
for e, a, p in approved:
	face_state[e.name] = "Approved" + (" (new face pending)" if p else "")
for e, p in pending:
	face_state[e.name] = "Pending approval"
for e, r in rejected:
	face_state[e.name] = "Rejected"
for e in missing:
	face_state[e.name] = "Not registered"

STATUS_ORDER = {"Not marked": 0, "Only IN (no OUT)": 1, "On leave": 2, "IN & OUT": 3}
today_rows = []
for emp_id in STAFF:
	e = emps.get(emp_id) or frappe._dict(name=emp_id, employee_name=emp_id, designation="")
	fi, lo = day_punch(emp_id, today)
	if fi and lo:
		st = "IN & OUT"
	elif fi:
		st = "Only IN (no OUT)"
	elif (emp_id, today) in leave_days:
		st = "On leave"
	else:
		st = "Not marked"
	hrs = ""
	if fi and lo:
		mins = int((lo.time - fi.time).total_seconds() // 60)
		hrs = "%d:%02d" % (mins // 60, mins % 60)
	today_rows.append([e.employee_name, e.name, e.designation or "", "Not required" if emp_id in loc_optional else ", ".join(assigned.get(emp_id, [])) or "NOT ASSIGNED",
	                   face_state.get(emp_id, ""), hhmm(fi), punch_loc(fi), hhmm(lo), punch_loc(lo), hrs, st])
today_rows.sort(key=lambda r: (STATUS_ORDER.get(r[-1], 9), r[0] or ""))
att = {k: sum(1 for r in today_rows if r[-1] == k) for k in STATUS_ORDER}

week_rows = []
for emp_id in STAFF:
	e = emps.get(emp_id) or frappe._dict(name=emp_id, employee_name=emp_id, designation="")
	cells, marked = [], 0
	for d in DAYS:
		fi, lo = day_punch(emp_id, d)
		if fi:
			marked += 1
			cells.append("%s - %s" % (hhmm(fi), hhmm(lo) if lo else "no OUT"))
		elif (emp_id, d) in leave_days:
			cells.append("Leave")
		else:
			cells.append("")
	week_rows.append([e.employee_name, e.name, e.designation or ""] + cells + [marked])
week_rows.sort(key=lambda r: (r[-1], r[0] or ""))


def build_xlsx():
	from io import BytesIO

	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
	from openpyxl.utils import get_column_letter

	thin = Side(style="thin", color="D0D7DE")
	border = Border(left=thin, right=thin, top=thin, bottom=thin)
	head_fill = PatternFill("solid", fgColor="1F4E78")
	fills = {"IN & OUT": "D1F2D9", "Only IN (no OUT)": "FFF1C2", "On leave": "E5E7EB", "Not marked": "FAD4D4"}

	def sheet(ws, title_text, head, rows, status_col=None, widths=None):
		ws.append([title_text])
		ws["A1"].font = Font(bold=True, size=13)
		ws.append([])
		ws.append(head)
		for c in ws[3]:
			c.font = Font(bold=True, color="FFFFFF")
			c.fill = head_fill
			c.alignment = Alignment(vertical="center", wrap_text=True)
			c.border = border
		for r in rows:
			ws.append(r)
			for c in ws[ws.max_row]:
				c.border = border
				c.alignment = Alignment(vertical="top")
			if status_col is not None:
				f = fills.get(r[status_col])
				if f:
					for c in ws[ws.max_row]:
						c.fill = PatternFill("solid", fgColor=f)
		ws.freeze_panes = "C4"
		ws.auto_filter.ref = "A3:%s%d" % (get_column_letter(len(head)), max(3, ws.max_row))
		for i, w in enumerate(widths or [], start=1):
			ws.column_dimensions[get_column_letter(i)].width = w

	wb = Workbook()
	ws = wb.active
	ws.title = "Today %s" % today.strftime("%d-%m")
	sheet(ws, "%s - Mark IN / OUT on %s (as of %s)   |   IN & OUT: %d   Only IN: %d   On leave: %d   Not marked: %d" % (
		G["title"], today.strftime("%d-%m-%Y"), stamp.strftime("%I:%M %p"), att["IN & OUT"], att["Only IN (no OUT)"], att["On leave"], att["Not marked"]),
		["Employee", "Emp ID", "Designation", "Assigned location", "Face", "IN time", "IN at", "OUT time", "OUT at", "Hours", "Status"],
		today_rows, status_col=10, widths=[30, 14, 24, 26, 22, 10, 22, 10, 22, 8, 18])
	ws2 = wb.create_sheet("Last 7 days")
	sheet(ws2, "%s - first IN and last OUT per day (blank = not marked)" % G["title"],
	      ["Employee", "Emp ID", "Designation"] + [d.strftime("%a %d-%m") for d in DAYS] + ["Days marked"],
	      week_rows, widths=[30, 14, 24] + [20] * len(DAYS) + [12])
	buf = BytesIO()
	wb.save(buf)
	return buf.getvalue()


xlsx = build_xlsx()
xlsx_name = "Attendance_%s_%s.xlsx" % (G["short"].replace(" ", "_"), stamp.strftime("%d-%m-%Y_%H%M"))
att_html = ("<p style='margin:0 0 6px;font-size:14px'><b>Today's Mark IN / OUT:</b> IN &amp; OUT <b>%d</b> &middot; Only IN <b>%d</b> &middot; "
            "On leave <b>%d</b> &middot; Not marked <b style='color:#cf222e'>%d</b> &nbsp;<span style='color:#57606a;font-size:12px'>(details in the attached Excel)</span></p>"
            % (att["IN & OUT"], att["Only IN (no OUT)"], att["On leave"], att["Not marked"]))
body_marker = "<h3 style='font-size:15px;margin:0'>Pending approval"
html_str = "".join(html)
html = [html_str.replace(body_marker, att_html + body_marker, 1)]
body = "".join(html)
subject = "%sFace registration status - %s - %s (Approved %d, Pending %d, Not registered %d)" % (
	"TEST - " if TEST else "", G["short"], when, len(approved), len(pending), len(missing))

log("summary: approved=%d pending=%d rejected=%d not_registered=%d approved_today=%d" % (
	len(approved), len(pending), len(rejected), len(missing), approved_today))
log("today IN/OUT: %s | excel %s (%d bytes)" % (att, xlsx_name, len(xlsx)))
if DRY:
	path = os.path.join(DIR, "preview_%s.html" % KEY)
	with open(path, "w") as fh:
		fh.write("<html><head><meta charset='utf-8'><title>preview</title></head><body>%s</body></html>" % body)
	with open(os.path.join(DIR, "preview_%s.xlsx" % KEY), "wb") as fh:
		fh.write(xlsx)
	log("DRY RUN - subject:", subject, "| preview:", path)
else:
	frappe.sendmail(recipients=TO, subject=subject, message=body, now=True, add_unsubscribe_link=0,
	                attachments=[{"fname": xlsx_name, "fcontent": xlsx}])
	frappe.db.commit()
	for rcpt in TO:
		q = frappe.db.sql("""select q.status, r.status rstatus, q.error from `tabEmail Queue` q join `tabEmail Queue Recipient` r on r.parent = q.name
			where r.recipient = %s order by q.creation desc limit 1""", rcpt, as_dict=True)
		log("sent to", rcpt, "| queue:", q[0].status if q else "?", "| recipient:", q[0].rstatus if q else "?", (q[0].error or "")[:200] if q else "")
frappe.destroy()
