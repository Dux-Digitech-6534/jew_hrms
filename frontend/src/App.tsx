import { Component, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MapContainer, Marker, TileLayer, Circle, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { API, call, cleanErrorMessage, logout, uploadFile } from "./api";
import { IconSprite, Ic, BrandMark } from "./icons";

type Caps = {
  can_mark_attendance: boolean;
  can_apply_leave: boolean;
  can_apply_late_early?: boolean;
  can_request_regularization?: boolean;
  can_apply_on_duty?: boolean;
  can_approve_on_duty?: boolean;
  can_register_face: boolean;
  can_self_register_face?: boolean;
  can_approve_face?: boolean;
  can_manage_locations: boolean;
  can_approve_leave: boolean;
  can_approve_late_early?: boolean;
  can_approve_late_early_hr?: boolean;
  can_decide_cancellations?: boolean;
  can_view_admin: boolean;
  can_manage_leave_policy?: boolean;
  can_manage_shift_policy?: boolean;
  can_manage_regularization?: boolean;
  can_view_approvers?: boolean;
  can_manage_masters?: boolean;
  can_apply_leave_for_others?: boolean;
  is_admin: boolean;
  is_hr?: boolean;
  is_owner?: boolean;
  is_dept_head?: boolean;
  approval_level?: string;
};

type View =
  | "dashboard" | "attendance" | "history" | "leave" | "lateEarly" | "regularizationRequest" | "admin" | "face" | "selfFace" | "faceApproval" | "location"
  | "leaveApproval" | "lateEarlyApproval" | "lateEarlyApprovalHr" | "cancellationApproval" | "leavePolicy" | "shiftPolicy" | "regularization" | "employees" | "approvers" | "onDuty" | "onDutyApproval"
  | "notifications" | "profile" | "settings";

const DENY: Caps = {
  can_mark_attendance: false, can_apply_leave: false, can_apply_late_early: false, can_request_regularization: false, can_register_face: false,
  can_manage_locations: false, can_approve_leave: false, can_approve_late_early: false, can_view_admin: false,
  can_manage_leave_policy: false, can_manage_shift_policy: false, can_manage_regularization: false,
  is_admin: false
};

const REMEMBER_LOGIN_KEY = "jew_hrms_remember_login";
const ADMIN_ACCESS_ROLES = ["JEW HRMS Admin", "JEW HRMS HR", "JEW HRMS Owner"];
const ADMIN_VIEWS: ReadonlySet<View> = new Set<View>(["admin", "face", "location", "leaveApproval", "lateEarlyApproval", "lateEarlyApprovalHr", "cancellationApproval", "leavePolicy", "shiftPolicy", "regularization", "employees", "approvers", "onDutyApproval"]);

function canAccessAdmin(userRoles: unknown) {
  return Array.isArray(userRoles) && userRoles.some((role) => ADMIN_ACCESS_ROLES.includes(String(role).trim()));
}

function applyAdminRoleVisibility(userCaps: Partial<Caps>, userRoles: unknown): Caps {
  const isDeptHead = Boolean(userCaps.is_dept_head);
  // A department head holds no admin ROLE but the backend flags them; grant them the
  // (backend-scoped) admin panel too. Full admins still come through the role check.
  const adminAllowed = canAccessAdmin(userRoles) || isDeptHead;
  return {
    ...DENY,
    ...userCaps,
    can_view_admin: adminAllowed,
    can_register_face: adminAllowed && Boolean(userCaps.can_register_face),
    can_manage_locations: adminAllowed && Boolean(userCaps.can_manage_locations),
    can_manage_masters: adminAllowed && Boolean(userCaps.can_manage_masters),
    can_apply_leave_for_others: adminAllowed && Boolean(userCaps.can_apply_leave_for_others),
    can_approve_leave: adminAllowed && Boolean(userCaps.can_approve_leave),
    can_approve_late_early: adminAllowed && Boolean(userCaps.can_approve_late_early),
    can_manage_leave_policy: adminAllowed && Boolean(userCaps.can_manage_leave_policy),
    can_manage_shift_policy: adminAllowed && Boolean(userCaps.can_manage_shift_policy),
    can_manage_regularization: adminAllowed && Boolean(userCaps.can_manage_regularization),
    is_admin: adminAllowed && Boolean(userCaps.is_admin),
    is_hr: adminAllowed && Boolean(userCaps.is_hr),
    is_owner: adminAllowed && Boolean(userCaps.is_owner),
    is_dept_head: isDeptHead
  };
}

function capacitorPlugin(name: string) {
  return (window as any).Capacitor?.Plugins?.[name];
}

// Install ID of this app / browser (kept in localStorage). It is sent with every punch so HR can see which
// phone a punch came from (Punch audit page). It identifies the install, nothing about the person.
function getDeviceId(): string {
  try {
    let id = localStorage.getItem("jew_hrms_device_id") || "";
    if (!/^D-[0-9A-F]{8}$/.test(id)) {
      const bytes = new Uint8Array(4);
      if (window.crypto?.getRandomValues) window.crypto.getRandomValues(bytes);
      else for (let i = 0; i < 4; i += 1) bytes[i] = Math.floor(Math.random() * 256);
      id = "D-" + Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
      localStorage.setItem("jew_hrms_device_id", id);
    }
    return id;
  } catch { return ""; }
}

function formatTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value).slice(11, 16) : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatWorkingHours(start?: string, end?: string) {
  if (!start || !end) return "-";
  const inDate = new Date(start);
  const outDate = new Date(end);
  if (Number.isNaN(inDate.getTime()) || Number.isNaN(outDate.getTime())) return "-";
  const minutes = Math.max(0, Math.round((outDate.getTime() - inDate.getTime()) / 60000));
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function hasActiveFrappeUserCookie() {
  return document.cookie.split(";").some((part) => {
    const [key, value] = part.trim().split("=");
    return key === "user_id" && decodeURIComponent(value || "") !== "Guest";
  });
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

// Current time in India (IST) regardless of device timezone.
function istNow() {
  return new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
}
function greeting() {
  const h = istNow().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  if (h < 21) return "Good evening";
  return "Good night";
}
// Open a link outside the app: in the Capacitor APK a navigation to another host is handed to Android
// (ACTION_VIEW - WhatsApp's own app links take over); in a normal browser it opens a new tab.
function openExternal(url: string) {
  const native = !!(window as any).Capacitor?.isNativePlatform?.();
  if (native) { window.location.href = url; return; }
  const w = window.open(url, "_blank", "noopener");
  if (!w) window.location.href = url;
}
// Copy to the clipboard: the synchronous way first (stays inside the tap gesture), then the async API.
async function copyText(text: string): Promise<boolean> {
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
    document.body.appendChild(ta); ta.focus(); ta.select(); ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy"); document.body.removeChild(ta);
    if (ok) return true;
  } catch { /* fall through */ }
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}
function WhatsAppSheet({ wa, flash, onClose, heading }: { wa: { text: string; group_url: string }; flash: (title: string, msg: string) => void; onClose: () => void; heading?: string }) {
  const shareUrl = "https://wa.me/?text=" + encodeURIComponent(wa.text);
  const toGroup = async () => {
    const copied = await copyText(wa.text);
    if (copied) { flash("Message copied", "Paste it in the group and tap Send."); openExternal(wa.group_url); return; }
    // Could not reach the clipboard: open WhatsApp's share screen instead, where the text is already filled in.
    flash("Opening WhatsApp", "Choose the leave group and tap Send.");
    openExternal(shareUrl);
  };
  const share = () => openExternal(shareUrl);
  return createPortal(
    <div className="sheet-layer" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label="Send to WhatsApp group">
        <div className="sheet-head"><strong>{heading || "Send to WhatsApp group"}</strong><button className="ibtn" type="button" aria-label="Close" onClick={onClose}><Ic name="close" /></button></div>
        <div className="sheet-body">
          <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 8 }}>Post this in the leave group so everyone knows.</div>
          <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 13, lineHeight: 1.55, padding: "11px 13px", borderRadius: 12, border: "1px solid var(--hair)", background: "var(--bg2, rgba(0,0,0,.03))", userSelect: "text" }}>{wa.text}</div>
          <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
            <button className="btn ok" type="button" onClick={toGroup}><Ic name="copy" /> Copy message &amp; open group</button>
            <button className="btn ghost" type="button" onClick={share}><Ic name="send" /> Share on WhatsApp instead</button>
            <button className="btn ghost" type="button" onClick={onClose}>Skip</button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 10 }}>The message is copied and the group opens - paste it there and tap Send.</div>
        </div>
      </div>
    </div>, document.body);
}
// "Send to WhatsApp group" for On Duty / Late-Early / Regularization: same sheet as leave requests.
function useRequestWhatsApp(kind: string, flash: (title: string, msg: string) => void) {
  const [waShare, setWaShare] = useState<any>(null);
  const [waBusy, setWaBusy] = useState<string | null>(null);
  const openWa = async (name: string, kindOverride?: string) => {
    if (waBusy) return;
    setWaBusy(name);
    try {
      const r: any = await call(API.getRequestWhatsapp, { kind: kindOverride || kind, name });
      if (r?.whatsapp?.text) setWaShare(r.whatsapp);
    } catch (error) { flash("WhatsApp", cleanErrorMessage(error)); } finally { setWaBusy(null); }
  };
  return { waShare, setWaShare, openWa, waBusy };
}
function WaButton({ onClick, busy, label }: { onClick: () => void; busy?: boolean; label?: string }) {
  return <button className="ibtn" type="button" aria-label={label || "Send to WhatsApp group"} title={label || "Send to WhatsApp group"} disabled={busy}
    style={{ flex: "none", color: "#15916A" }} onClick={(e) => { e.stopPropagation(); onClick(); }}><Ic name="send" /></button>;
}
// Display dates as dd/mm/yyyy across the app.
function fmtDMY(value?: string) {
  if (!value) return "";
  const str = String(value);
  const iso = str.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) return `${iso[3]}/${iso[2]}/${iso[1]}`;
  const d = new Date(str);
  if (Number.isNaN(d.getTime())) return str;
  return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
}

// Convert a native <input type="time"> value ("HH:MM", 24-hour) into a
// friendly 12-hour label for display in explanations/emails.
function fmtTime12(value?: string) {
  if (!value) return "";
  const m = String(value).match(/^(\d{1,2}):(\d{2})/);
  if (!m) return value;
  let h = parseInt(m[1], 10);
  const min = m[2];
  const suffix = h >= 12 ? "PM" : "AM";
  h = h % 12;
  if (h === 0) h = 12;
  return `${h}:${min} ${suffix}`;
}

// Collapse options that share a display label (the site has duplicate leave
// types like "CL" and "Casual Leave" both showing as "Casual Leave").
function dedupeByLabel(options: any[]) {
  const seen = new Set<string>();
  const out: any[] = [];
  for (const o of options) {
    const key = String(o.label ?? o.value ?? "").trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(o);
  }
  return out;
}

// Short-code leave types that duplicate a full-name type on this site
// (e.g. "CL" == "Casual Leave"). Hidden from pickers/balances so each leave
// shows once. Consolidating these in Frappe is the proper long-term fix.
const HIDDEN_LEAVE_TYPES = new Set(["CL", "SL", "PL", "LWP"]);

// Casual Leave is capped at CASUAL_LEAVE_MONTHLY_CAP days per calendar month
// (enforced server-side too — see api.py _check_casual_monthly_cap). A single
// request longer than the cap can never succeed, so it's disabled client-side
// rather than letting the employee hit a submit-time error.
const CASUAL_LEAVE_MONTHLY_CAP = 2;

// Build the leave-type picker options, mirroring the Desk Leave Application's
// leave_type link field (ALL leave types are selectable). The backend
// (get_leave_dashboard) returns `selectable_types` already ordered
// allocated-first, each annotated with the live remaining balance, an
// `unlimited` (is_lwp) flag, or `has_allocation:false` for types the employee
// isn't allocated. That annotation becomes the option sub-label. If the list is
// unavailable (older backend), fall back to the full Leave Type list minus the
// duplicate short-code records. `requestedDays` (from the From/To picked so
// far) disables Casual Leave once the request would exceed the monthly cap.
function buildLeaveTypeOptions(selectable: any[], allTypes: any[], requestedDays?: number) {
  const src = (selectable && selectable.length)
    ? selectable
    : (allTypes || [])
        .filter((t: any) => !HIDDEN_LEAVE_TYPES.has(t.name))
        .map((t: any) => ({ leave_type: t.name, leave_type_name: t.leave_type_name || t.name, is_lwp: t.is_lwp, unlimited: !!t.is_lwp, balance: null, has_allocation: undefined }));
  return src.map((t: any) => {
    const n = Number(t.balance);
    const isCasual = String(t.leave_type_name || t.leave_type || "").toLowerCase() === "casual leave";
    const overCasualCap = isCasual && Number(requestedDays || 0) > CASUAL_LEAVE_MONTHLY_CAP;
    const base = t.unlimited
      ? "No balance limit"
      : (t.balance != null && !Number.isNaN(n)
          ? `${n} day${n === 1 ? "" : "s"} left`
          : (t.has_allocation === false ? "No allocation — ask HR" : ""));
    const disabled = !!t.disabled || overCasualCap;
    const desc = overCasualCap ? `Max ${CASUAL_LEAVE_MONTHLY_CAP} days at a time` : (disabled ? `${base || "0 days left"} · not available` : base);
    return { value: t.leave_type, label: t.leave_type_name || t.leave_type, description: desc, disabled };
  });
}

// Primary tab views: show the bottom tab bar (and use tab-height padding).
// All other views are sub-screens: hide the tab bar + show a back button.
const TAB_VIEWS: ReadonlySet<View> = new Set<View>(["dashboard", "attendance", "history", "leave", "admin", "profile"]);

const navMeta: Record<View, { title: string; sub: string; nav?: string; back?: boolean }> = {
  dashboard: { title: "JEW HRMS", sub: "Jain Engineering Works", nav: "dashboard" },
  attendance: { title: "Attendance", sub: "Mark in / out", nav: "attendance" },
  history: { title: "History", sub: "My records", nav: "attendance", back: true },
  leave: { title: "Leave", sub: "Apply & track", nav: "leave" },
  lateEarly: { title: "Late / Early", sub: "Apply & track", nav: "attendance", back: true },
  onDuty: { title: "On Duty", sub: "Apply & track", nav: "attendance", back: true },
  onDutyApproval: { title: "On Duty approval", sub: "Approve/reject requests", nav: "admin", back: true },
  regularizationRequest: { title: "Regularization", sub: "Request & track", nav: "attendance", back: true },
  admin: { title: "Admin", sub: "Manager access", nav: "admin" },
  face: { title: "Face register", sub: "Enroll template", nav: "admin", back: true },
  selfFace: { title: "My face", sub: "Register for approval", nav: "profile", back: true },
  faceApproval: { title: "Face approvals", sub: "Approve employee photos", nav: "admin", back: true },
  location: { title: "Geofence", sub: "Work sites", nav: "admin", back: true },
  leaveApproval: { title: "Leave approval", sub: "Pending requests", nav: "admin", back: true },
  lateEarlyApproval: { title: "Late/Early approval", sub: "Pending requests", nav: "admin", back: true },
  lateEarlyApprovalHr: { title: "Late/Early — HR approval", sub: "Final sign-off", nav: "admin", back: true },
  cancellationApproval: { title: "Cancellations", sub: "Approve/reject requests", nav: "admin", back: true },
  leavePolicy: { title: "Leave policy", sub: "Leave types", nav: "admin", back: true },
  shiftPolicy: { title: "Shift policy", sub: "Attendance rules", nav: "admin", back: true },
  regularization: { title: "Regularization", sub: "Pending review", nav: "admin", back: true },
  employees: { title: "Employees", sub: "Directory", nav: "admin", back: true },
  approvers: { title: "Approvers", sub: "Who approves what", nav: "admin", back: true },
  notifications: { title: "Notifications", sub: "Alerts & reminders", nav: "dashboard", back: true },
  profile: { title: "Profile", sub: "My details", nav: "profile" },
  settings: { title: "Settings", sub: "Preferences", nav: "profile", back: true }
};

function toastText(code?: string) {
  const map: Record<string, string> = {
    face_not_registered: "Face is not registered for this employee.",
    face_not_matched: "Face did not match the registered template.",
    outside_location: "You are outside the assigned attendance location.",
    already_marked: "Attendance is already marked or the sequence is invalid.",
    permission_denied: "You do not have permission for this action.",
    success: "Done."
  };
  return map[code || ""] || "Action completed.";
}

function useActionRunner(flash: (title: string, msg: string) => void) {
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const runAction = async <T,>(
    key: string,
    action: () => Promise<T>,
    options: { successTitle?: string; successMessage?: string; errorTitle?: string } = {}
  ): Promise<T | null> => {
    if (busyAction) return null;
    setBusyAction(key);
    try {
      const result: any = await action();
      if (options.successTitle) flash(options.successTitle, options.successMessage || result?.message || "Done.");
      return result;
    } catch (error) {
      flash(options.errorTitle || "Action failed", cleanErrorMessage(error));
      return null;
    } finally {
      setBusyAction(null);
    }
  };
  return { busyAction, runAction, isBusy: (key: string) => busyAction === key, isAnyBusy: Boolean(busyAction) };
}

function PickLocation({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({ click: (event: any) => onPick(event.latlng.lat, event.latlng.lng) });
  return null;
}

function MapRecenter({ center }: { center: [number, number] }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, Math.max(map.getZoom(), 15), { animate: true });
  }, [center[0], center[1], map]);
  return null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) {
      return (
        <div className="shell center"><IconSprite />
          <div className="app-error"><Ic name="close" style={{ width: 26, height: 26, color: "var(--err)" }} /><h2>Unable to load JEW HRMS.</h2><p>Please refresh or contact admin.</p></div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const [theme, setTheme] = useState(localStorage.getItem("jew-theme") || "light");
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null);
  const [view, setView] = useState<View>("dashboard");
  const [viewStack, setViewStack] = useState<View[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [caps, setCaps] = useState<Caps>(DENY);
  const [session, setSession] = useState<any>(null);
  const [dashboard, setDashboard] = useState<any>(null);
  const [profile, setProfile] = useState<any>(null);
  const [history, setHistory] = useState<any>(null);
  const [leaveData, setLeaveData] = useState<any>({});
  const [lateEarlyData, setLateEarlyData] = useState<any>({});
  const [onDutyData, setOnDutyData] = useState<any>({});
  const [myRegularizations, setMyRegularizations] = useState<any>({});
  const [adminData, setAdminData] = useState<any>({});
  const [selectedEmployee, setSelectedEmployee] = useState("");
  const [notifications, setNotifications] = useState<any[]>([]);
  const [toast, setToast] = useState<{ title: string; msg: string } | null>(null);
  const [cameraActive, setCameraActive] = useState(false);
  const lastBackRef = useRef(0);
  const deepLinkHandledRef = useRef(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("jew-theme", theme);
    const StatusBar = capacitorPlugin("StatusBar");
    StatusBar?.setOverlaysWebView?.({ overlay: false }).catch?.(() => undefined);
    StatusBar?.setBackgroundColor?.({ color: theme === "dark" ? "#0A0D13" : "#F3F4F7" }).catch?.(() => undefined);
    StatusBar?.setStyle?.({ style: theme === "dark" ? "LIGHT" : "DARK" }).catch?.(() => undefined);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0A0D13" : "#5C4DE6");
  }, [theme]);

  const flash = (title: string, msg: string) => {
    setToast({ title, msg });
    window.setTimeout(() => setToast(null), 3200);
  };

  const resetSessionState = () => {
    window.dispatchEvent(new Event("jew-hrms-stop-camera"));
    window.dispatchEvent(new Event("jew-hrms-close-attendance-verify"));
    setSession(null); setDashboard(null); setProfile(null); setHistory(null);
    setLeaveData({}); setLateEarlyData({}); setOnDutyData({}); setMyRegularizations({}); setAdminData({}); setSelectedEmployee(""); setNotifications([]);
    setCaps(DENY); setView("dashboard"); setViewStack([]); setMenuOpen(false);
    setRefreshing(false); setCameraActive(false);
  };

  const loadBase = async (force = false) => {
    if (!force && !hasActiveFrappeUserCookie()) {
      resetSessionState(); setLoggedIn(false); return;
    }
    try {
      const user = await call(API.getSessionUser);
      const c = await call<Caps>(API.capabilities);
      setSession(user);
      setCaps(applyAdminRoleVisibility(c, user?.roles));
      setLoggedIn(true);
      try { setDashboard(await call(API.getDashboard)); } catch { setDashboard(null); }
    } catch {
      resetSessionState(); setLoggedIn(false);
    }
  };

  const handleLogout = async () => {
    try {
      await logout(); resetSessionState(); setLoggedIn(false);
      flash("Logged out", "You have returned to JEW HRMS login.");
    } catch (error) {
      flash("Logout failed", cleanErrorMessage(error));
    }
  };

  useEffect(() => { loadBase(); }, []);

  const loadViewData = async (next: View) => {
    if (ADMIN_VIEWS.has(next) && !caps.can_view_admin) return;
    if (next === "dashboard") setDashboard(await call(API.getDashboard));
    if (next === "attendance") {
      const status = await call(API.getTodayAttendanceStatus);
      setAdminData((prev: any) => ({ ...prev, attendanceStatus: status }));
    }
    if (next === "history") setHistory(await call(API.getAttendanceHistory));
    if (next === "leave") {
      const calls: Promise<any>[] = [call(API.getLeaveTypes), call(API.getLeaveDashboard), call(API.getMyLeaves)];
      if (caps.can_view_admin) calls.push(call(API.getEmployeeList));
      // Load each part independently - one failing call must not blank out the whole leave
      // form (it used to: a single rejected request left the Leave type picker empty).
      const settled = await Promise.allSettled(calls);
      if (settled.every((r) => r.status === "rejected")) throw (settled[0] as PromiseRejectedResult).reason;
      const val = (r: any) => (r && r.status === "fulfilled" ? r.value : {});
      const [types, dash, mine, employees] = settled.map(val);
      setLeaveData({ types: types.leave_types || [], selectable: dash.selectable_types || [], balances: dash.balances || [], leaves: mine.leaves || [], employees: employees?.employees || [] });
    }
    if (next === "lateEarly") {
      const mine = await call(API.getMyLateEarly);
      setLateEarlyData({ applications: mine.applications || [] });
    }
    if (next === "onDuty") {
      const mine = await call(API.getMyOnDuty);
      setOnDutyData({ requests: mine.requests || [] });
    }
    if (next === "onDutyApproval" && caps.can_approve_on_duty) {
      const pending = await call(API.getPendingOnDuty);
      setAdminData((prev: any) => ({ ...prev, pendingOnDuty: pending.requests || [] }));
    }
    if (next === "regularizationRequest") {
      const mine = await call(API.getMyRegularizations);
      setMyRegularizations({ regularizations: mine.regularizations || [] });
    }
    if (next === "profile") setProfile((await call(API.getEmployeeProfile)).employee_profile);
    if (next === "notifications") setNotifications((await call(API.getNotifications)).notifications || []);
    if (next === "admin") {
      setDashboard(await call(API.getDashboard));
      try {
        const ol = await call(API.getOnLeaveToday);
        setAdminData((prev: any) => ({ ...prev, onLeave: ol.on_leave || [], onLeaveCount: ol.count ?? (ol.on_leave || []).length }));
      } catch {
        // Endpoint may not be live until the bench is restarted; fail soft.
        setAdminData((prev: any) => ({ ...prev, onLeave: [], onLeaveCount: 0 }));
      }
    }
    if (next === "face" && caps.can_register_face) {
      const employees = await call(API.getEmployeeList);
      setAdminData((prev: any) => ({ ...prev, employees: employees.employees }));
    }
    if (next === "faceApproval" && caps.can_approve_face) {
      const fa = await call(API.getPendingFaceApprovals);
      setAdminData((prev: any) => ({ ...prev, faceApprovals: fa.requests || [] }));
    }
    if (next === "location" && caps.can_manage_locations) {
      const locations = await call(API.getLocations);
      const employees = await call(API.getEmployeeList);
      setAdminData((prev: any) => ({ ...prev, locations: locations.locations, employees: employees.employees }));
    }
    if (next === "employees" && caps.can_view_admin) {
      const employees = await call(API.getEmployeeList);
      setAdminData((prev: any) => ({ ...prev, employees: employees.employees }));
    }
    if (next === "approvers" && caps.can_view_approvers) {
      const overview = await call(API.getApproversOverview);
      setAdminData((prev: any) => ({ ...prev, approvers: overview }));
    }
    if (next === "leaveApproval" && caps.can_approve_leave) {
      const pending = await call(API.getPendingLeaves);
      setAdminData((prev: any) => ({ ...prev, pendingLeaves: pending.leaves }));
    }
    if (next === "lateEarlyApproval" && caps.can_approve_late_early) {
      const pending = await call(API.getPendingLateEarly);
      setAdminData((prev: any) => ({ ...prev, pendingLateEarly: pending.applications }));
    }
    if (next === "lateEarlyApprovalHr" && caps.can_approve_late_early_hr) {
      const pending = await call(API.getPendingLateEarlyHr);
      setAdminData((prev: any) => ({ ...prev, pendingLateEarlyHr: pending.applications }));
    }
    if (next === "cancellationApproval" && caps.can_decide_cancellations) {
      const pending = await call(API.getPendingCancellations);
      setAdminData((prev: any) => ({ ...prev, pendingCancelLeaves: pending.leaves, pendingCancelLateEarly: pending.late_early }));
    }
    if (next === "leavePolicy" && caps.can_manage_leave_policy) {
      const policy = await call(API.getLeavePolicy);
      setAdminData((prev: any) => ({ ...prev, leaveTypes: policy.leave_types }));
    }
    if (next === "shiftPolicy" && caps.can_manage_shift_policy) {
      const policies = await call(API.getShiftPolicies);
      setAdminData((prev: any) => ({ ...prev, shiftPolicies: policies.policies }));
    }
    if (next === "regularization" && caps.can_manage_regularization) {
      const regs = await call(API.getRegularizations);
      setAdminData((prev: any) => ({ ...prev, regularizations: regs.regularizations }));
    }
  };

  const open = async (next: View, options: { replace?: boolean; silent?: boolean } = {}) => {
    if (ADMIN_VIEWS.has(next) && !caps.can_view_admin) {
      flash("Not permitted", "You do not have access to this admin screen.");
      if (view !== "dashboard") { setView("dashboard"); setViewStack([]); }
      return;
    }
    if (next === "face" && !caps.can_register_face) { flash("Not permitted", "You do not have access to face registration."); return; }
    if (next === "selfFace" && !caps.can_self_register_face) { flash("Not permitted", "Face self-registration is not enabled for you."); return; }
    if (next === "faceApproval" && !caps.can_approve_face) { flash("Not permitted", "You do not have access to face approvals."); return; }
    if (next === "location" && !caps.can_manage_locations) { flash("Not permitted", "You do not have access to location setup."); return; }
    if (next === "employees" && !caps.can_view_admin) { flash("Not permitted", "You do not have access to employee list."); return; }
    if (next === "approvers" && !caps.can_view_approvers) { flash("Not permitted", "You do not have access to this admin screen."); return; }
    if (next === "leaveApproval" && !caps.can_approve_leave) { flash("Not permitted", "You do not have approval permission."); return; }
    if (next === "lateEarlyApproval" && !caps.can_approve_late_early) { flash("Not permitted", "You do not have approval permission."); return; }
    if (next === "lateEarlyApprovalHr" && !caps.can_approve_late_early_hr) { flash("Not permitted", "You do not have approval permission."); return; }
    if (next === "cancellationApproval" && !caps.can_decide_cancellations) { flash("Not permitted", "You do not have approval permission."); return; }
    if (next === "leavePolicy" && !caps.can_manage_leave_policy) { flash("Not permitted", "You do not have access to leave policy."); return; }
    if (next === "shiftPolicy" && !caps.can_manage_shift_policy) { flash("Not permitted", "You do not have access to shift policy."); return; }
    if (next === "regularization" && !caps.can_manage_regularization) { flash("Not permitted", "You do not have access to regularization."); return; }
    setMenuOpen(false);
    if (!options.replace && next !== view) setViewStack((stack) => [...stack, view].slice(-12));
    setView(next);
    try { await loadViewData(next); }
    catch (error: any) { if (!options.silent) flash("Unable to load", cleanErrorMessage(error)); }
  };

  const goBack = () => {
    if (cameraActive) {
      window.dispatchEvent(new Event("jew-hrms-stop-camera"));
      window.dispatchEvent(new Event("jew-hrms-close-attendance-verify"));
      setCameraActive(false); return;
    }
    if (menuOpen) { setMenuOpen(false); return; }
    if (viewStack.length) {
      const previous = viewStack[viewStack.length - 1];
      setViewStack((stack) => stack.slice(0, -1));
      open(previous, { replace: true, silent: true }); return;
    }
    if (view !== "dashboard") { open("dashboard", { replace: true, silent: true }); return; }
    const now = Date.now();
    if (now - lastBackRef.current < 2000) { capacitorPlugin("App")?.exitApp?.(); return; }
    lastBackRef.current = now;
    flash("Exit", "Press back again to exit.");
  };

  const refreshCurrent = async () => {
    setRefreshing(true);
    try { await loadViewData(view); }
    catch (error) { flash("Unable to refresh", cleanErrorMessage(error)); }
    finally { setRefreshing(false); }
  };

  useEffect(() => {
    const AppPlugin = capacitorPlugin("App");
    let handle: any;
    AppPlugin?.addListener?.("backButton", () => goBack())?.then?.((listener: any) => { handle = listener; });
    return () => { handle?.remove?.(); };
  }, [cameraActive, menuOpen, view, viewStack]);

  useEffect(() => {
    if (loggedIn && ADMIN_VIEWS.has(view) && !caps.can_view_admin) {
      setView("dashboard"); setViewStack([]);
      flash("Not permitted", "You do not have access to this admin screen.");
    }
  }, [caps.can_view_admin, loggedIn, view]);

  // Deep link from a notification email (e.g. "?view=regularization&name=abc123") -
  // land straight on that ONE request, not the dashboard and not the whole pending
  // queue. Runs once, after caps are actually loaded (a stale `caps` read right after
  // login would wrongly look like "not permitted").
  useEffect(() => {
    if (!loggedIn || deepLinkHandledRef.current) return;
    deepLinkHandledRef.current = true;
    const params = new URLSearchParams(window.location.search);
    const target = params.get("view") as View | null;
    const targetName = params.get("name");
    if (!target || target === "dashboard") return;
    window.history.replaceState(null, "", window.location.pathname);
    if (target === "regularization" && targetName) {
      if (!caps.can_manage_regularization) { flash("Not permitted", "You do not have access to this admin screen."); return; }
      (async () => {
        try {
          const detail = await call(API.getRegularizationDetail, { name: targetName });
          setAdminData((prev: any) => ({ ...prev, regularizations: [detail.regularization] }));
          setMenuOpen(false);
          setViewStack((stack) => [...stack, view].slice(-12));
          setView(target);
        } catch (error: any) { flash("Unable to load", cleanErrorMessage(error)); }
      })();
      return;
    }
    open(target, { replace: true });
  }, [loggedIn, caps]);

  const navItems = useMemo(() => {
    return [
      { id: "dashboard", label: "Home", icon: "home", view: "dashboard" as View, show: true },
      { id: "attendance", label: "Attend", icon: "clock", view: "attendance" as View, show: caps.can_mark_attendance },
      { id: "leave", label: "Leave", icon: "leaf", view: "leave" as View, show: caps.can_apply_leave },
      { id: "admin", label: "Admin", icon: "shield", view: "admin" as View, show: caps.can_view_admin },
      { id: "profile", label: "Profile", icon: "user", view: "profile" as View, show: true }
    ].filter((i) => i.show);
  }, [caps.can_mark_attendance, caps.can_apply_leave, caps.can_view_admin]);

  if (loggedIn === null) {
    return <div className="shell center"><IconSprite /><div className="loader">Loading JEW HRMS…</div></div>;
  }
  if (!loggedIn) {
    return <><IconSprite /><Login onDone={() => loadBase(true)} flash={flash} /></>;
  }

  const meta = navMeta[view];
  const showTabs = TAB_VIEWS.has(view);
  const canBack = !showTabs || Boolean(meta.back);

  return (
    <ErrorBoundary>
      <IconSprite />
      <div className="shell">
        <header className="topbar">
          {canBack
            ? <button className="back" onClick={goBack} aria-label="Back"><Ic name="chevron" /></button>
            : <button className="ibtn plain" onClick={() => setMenuOpen(true)} aria-label="Menu"><Ic name="menu" style={{ width: 22, height: 22 }} /></button>}
          <div className="brand">
            <span className="mark"><BrandMark /></span>
            <span className="brand-t"><strong>{meta.title}</strong><span>{meta.sub}</span></span>
          </div>
          <div className="top-actions">
            <button className="ibtn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Theme"><Ic name={theme === "dark" ? "sun" : "moon"} /></button>
            <button className="ibtn" onClick={() => open("notifications")} aria-label="Notifications"><Ic name="bell" /><span className="dot" /></button>
          </div>
        </header>

        <PullToRefresh onRefresh={refreshCurrent} refreshing={refreshing}>
          <main className={`page ${showTabs ? "" : "noTab"}`}>
            {view === "dashboard" && <Dashboard data={dashboard} open={open} caps={caps} flash={flash} session={session} />}
            {view === "attendance" && <Attendance flash={flash} open={open} caps={caps} initialStatus={adminData.attendanceStatus} onCameraActiveChange={setCameraActive} onMarked={async () => { const [dash, status] = await Promise.all([call(API.getDashboard), call(API.getTodayAttendanceStatus)]); setDashboard(dash); setAdminData((prev: any) => ({ ...prev, attendanceStatus: status })); }} />}
            {view === "history" && <History data={history} />}
            {view === "leave" && <Leave data={leaveData} caps={caps} flash={flash} me={session?.employee} reload={() => open("leave")} />}
            {view === "lateEarly" && <LateEarly data={lateEarlyData} flash={flash} reload={() => open("lateEarly")} />}
            {view === "onDuty" && <OnDuty data={onDutyData} flash={flash} reload={() => open("onDuty")} />}
            {view === "onDutyApproval" && (caps.can_view_admin && caps.can_approve_on_duty ? <OnDutyApproval requests={adminData.pendingOnDuty || []} flash={flash} reload={() => open("onDutyApproval")} /> : <NotPermitted />)}
            {view === "regularizationRequest" && <RegularizationRequest data={myRegularizations} flash={flash} reload={() => open("regularizationRequest")} />}
            {view === "admin" && (caps.can_view_admin ? <Admin open={open} caps={caps} data={adminData} /> : <NotPermitted />)}
            {view === "face" && (caps.can_view_admin && caps.can_register_face ? <FaceAdmin employees={adminData.employees || []} flash={flash} selectedEmployee={selectedEmployee} onCameraActiveChange={setCameraActive} /> : <NotPermitted />)}
            {view === "selfFace" && (caps.can_self_register_face ? <SelfFace flash={flash} me={session?.employee} onCameraActiveChange={setCameraActive} /> : <NotPermitted />)}
            {view === "faceApproval" && (caps.can_approve_face ? <FaceApproval requests={adminData.faceApprovals || []} flash={flash} reload={() => open("faceApproval")} /> : <NotPermitted />)}
            {view === "location" && (caps.can_view_admin && caps.can_manage_locations ? <LocationAdmin data={adminData} setData={setAdminData} flash={flash} reload={() => open("location")} caps={caps} /> : <NotPermitted />)}
            {view === "leaveApproval" && (caps.can_view_admin && caps.can_approve_leave ? <LeaveApproval leaves={adminData.pendingLeaves || []} flash={flash} reload={() => open("leaveApproval")} /> : <NotPermitted />)}
            {view === "lateEarlyApproval" && (caps.can_view_admin && caps.can_approve_late_early ? <LateEarlyApproval items={adminData.pendingLateEarly || []} flash={flash} reload={() => open("lateEarlyApproval")} /> : <NotPermitted />)}
            {view === "lateEarlyApprovalHr" && (caps.can_view_admin && caps.can_approve_late_early_hr ? <LateEarlyApproval items={adminData.pendingLateEarlyHr || []} flash={flash} reload={() => open("lateEarlyApprovalHr")} /> : <NotPermitted />)}
            {view === "cancellationApproval" && (caps.can_view_admin && caps.can_decide_cancellations ? <CancellationApproval leaves={adminData.pendingCancelLeaves || []} lateEarly={adminData.pendingCancelLateEarly || []} flash={flash} reload={() => open("cancellationApproval")} /> : <NotPermitted />)}
            {view === "leavePolicy" && (caps.can_view_admin && caps.can_manage_leave_policy ? <LeavePolicy types={adminData.leaveTypes || []} flash={flash} reload={() => open("leavePolicy")} /> : <NotPermitted />)}
            {view === "shiftPolicy" && (caps.can_view_admin && caps.can_manage_shift_policy ? <ShiftPolicy policies={adminData.shiftPolicies || []} flash={flash} reload={() => open("shiftPolicy")} /> : <NotPermitted />)}
            {view === "regularization" && (caps.can_view_admin && caps.can_manage_regularization ? <Regularization items={adminData.regularizations || []} flash={flash} reload={() => open("regularization")} /> : <NotPermitted />)}
            {view === "employees" && (caps.can_view_admin ? <Employees employees={adminData.employees || []} open={open} selectEmployee={setSelectedEmployee} /> : <NotPermitted />)}
            {view === "approvers" && (caps.can_view_approvers ? <Approvers data={adminData.approvers || {}} /> : <NotPermitted />)}
            {view === "notifications" && <Notifications items={notifications} />}
            {view === "profile" && <Profile profile={profile} open={open} caps={caps} onLogout={handleLogout} />}
            {view === "settings" && <Settings theme={theme} setTheme={setTheme} onLogout={handleLogout} />}
          </main>
        </PullToRefresh>

        {showTabs && <nav className="tabbar">
          {navItems.map((item) => (
            <button key={item.id} className={`tab ${meta.nav === item.id ? "on" : ""}`} onClick={() => open(item.view)}>
              <Ic name={item.icon} style={{ width: 22, height: 22 }} /><span>{item.label}</span>
            </button>
          ))}
        </nav>}

        <MenuDrawer open={menuOpen} caps={caps} active={view} session={session} openView={open} onClose={() => setMenuOpen(false)} onLogout={handleLogout} />
        {toast && <div className="toast-stack"><div className="toast"><Ic name="check" /><div><b>{toast.title}</b><span>{toast.msg}</span></div></div></div>}
      </div>
    </ErrorBoundary>
  );
}

function empInitials(name?: string) {
  return (name || "").split(" ").filter(Boolean).slice(0, 2).map((p) => p[0]).join("").toUpperCase() || "JE";
}

function Login({ onDone, flash }: { onDone: () => void; flash: (title: string, msg: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberPassword, setRememberPassword] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(REMEMBER_LOGIN_KEY) || "null");
      if (saved?.username && saved?.password) { setUsername(saved.username); setPassword(saved.password); setRememberPassword(true); }
    } catch { localStorage.removeItem(REMEMBER_LOGIN_KEY); }
  }, []);

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await call(API.login, { email: username, password });
      if (rememberPassword) localStorage.setItem(REMEMBER_LOGIN_KEY, JSON.stringify({ username, password }));
      else localStorage.removeItem(REMEMBER_LOGIN_KEY);
      flash("Login successful", "Welcome to Jain Engineering Works HRMS");
      await onDone();
    } catch (error: any) {
      flash("Login failed", cleanErrorMessage(error) || "Invalid credentials");
    } finally { setBusy(false); }
  };

  const onKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" && !busy) { event.preventDefault(); void submit(); }
  };

  return (
    <div className="shell">
      <div className="login-wrap" onKeyDown={onKey}>
        <div className="login-mark"><BrandMark /></div>
        <p className="eyebrow" style={{ textAlign: "center" }}>JEWIPL Employee app</p>
        <h1 className="h1" style={{ textAlign: "center" }}>Welcome <span className="g">back</span></h1>
        <p className="sub" style={{ textAlign: "center" }}>Sign in to mark attendance and manage your leave.</p>
        <div className="field">
          <label>Employee ID or email</label>
          <div className="inp"><Ic name="user" /><input value={username} onChange={(e) => setUsername(e.target.value)} type="text" name="usr" autoComplete="username" placeholder="EMP-00148 or email" /></div>
        </div>
        <div className="field">
          <label>Password</label>
          <div className="inp">
            <input value={password} onChange={(e) => setPassword(e.target.value)} type={showPassword ? "text" : "password"} name="pwd" autoComplete="current-password" placeholder="••••••••" />
            <button type="button" className="pwtoggle" onClick={() => setShowPassword((v) => !v)} aria-label={showPassword ? "Hide password" : "Show password"}><Ic name={showPassword ? "eyeoff" : "eye"} /></button>
          </div>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 8, margin: "12px 2px 0", color: "var(--muted)", fontSize: 13, fontWeight: 600 }}>
          <input type="checkbox" style={{ width: 16, height: 16, accentColor: "var(--iris)" }} checked={rememberPassword} onChange={(e) => { const c = e.target.checked; setRememberPassword(c); if (!c) localStorage.removeItem(REMEMBER_LOGIN_KEY); }} />
          <span>Keep me signed in</span>
        </label>
        <div style={{ marginTop: 18 }}><button className="btn" type="button" disabled={busy} onClick={() => void submit()}><Ic name="check" /> {busy ? "Signing in…" : "Sign in"}</button></div>
        <div style={{ marginTop: 22, textAlign: "center", color: "var(--faint)", fontSize: 10.5 }}>Powered by <b style={{ color: "var(--muted)" }}>DUX Digitech</b></div>
      </div>
    </div>
  );
}

function NotPermitted() {
  return <div className="empty"><h3>Not permitted</h3><p>You do not have access to this screen.</p></div>;
}

function PullToRefresh({ children, onRefresh, refreshing }: { children: ReactNode; onRefresh: () => Promise<void>; refreshing: boolean }) {
  const startY = useRef(0);
  const pulling = useRef(false);
  const [distance, setDistance] = useState(0);
  const begin = (event: React.TouchEvent) => {
    if (window.scrollY > 2 || refreshing) return;
    startY.current = event.touches[0].clientY; pulling.current = true;
  };
  const move = (event: React.TouchEvent) => {
    if (!pulling.current) return;
    const delta = event.touches[0].clientY - startY.current;
    if (delta > 0) setDistance(Math.min(72, delta / 2));
  };
  const end = async () => {
    if (!pulling.current) return;
    const should = distance > 48; pulling.current = false; setDistance(0);
    if (should) await onRefresh();
  };
  return (
    <div className="ptr-wrap" onTouchStart={begin} onTouchMove={move} onTouchEnd={end} onTouchCancel={end}>
      <div className={`ptr-indicator ${refreshing || distance > 8 ? "show" : ""}`} style={{ transform: `translate(-50%, ${refreshing ? 10 : distance - 38}px)` }}>
        <Ic name="refresh" className={refreshing ? "ic spin" : "ic"} style={{ width: 16, height: 16 }} />
      </div>
      {children}
    </div>
  );
}

function MenuDrawer({ open, caps, active, session, openView, onClose, onLogout }: any) {
  const name = session?.employee?.employee_name || session?.user || "Employee";
  const emp = session?.employee?.employee || session?.employee?.name || "";
  const normal = [
    { view: "dashboard", label: "Dashboard", icon: "home" },
    caps.can_mark_attendance && { view: "attendance", label: "Mark attendance", icon: "fp" },
    caps.can_mark_attendance && { view: "history", label: "Attendance history", icon: "clock" },
    caps.can_apply_leave && { view: "leave", label: "Leave", icon: "leaf" },
    caps.can_apply_late_early && { view: "lateEarly", label: "Late / Early", icon: "clock" },
    caps.can_apply_on_duty && { view: "onDuty", label: "On Duty", icon: "briefcase" },
    caps.can_request_regularization && { view: "regularizationRequest", label: "Regularization", icon: "refresh" },
    caps.can_self_register_face && { view: "selfFace", label: "Register my face", icon: "camera" },
    { view: "notifications", label: "Notifications", icon: "bell" }
  ].filter(Boolean) as any[];
  const admin = [
    caps.can_view_admin && { view: "admin", label: "Admin panel", icon: "shield" },
    caps.can_approve_leave && { view: "leaveApproval", label: "Leave approval", icon: "check" },
    caps.can_approve_late_early && { view: "lateEarlyApproval", label: "Late/Early approval", icon: "check" },
    caps.can_approve_late_early_hr && { view: "lateEarlyApprovalHr", label: "Late/Early — HR approval", icon: "check" },
    caps.can_approve_on_duty && { view: "onDutyApproval", label: "On Duty approval", icon: "check" },
    caps.can_decide_cancellations && { view: "cancellationApproval", label: "Cancellations", icon: "close" },
    caps.can_view_admin && { view: "employees", label: "Employees", icon: "users" },
    caps.can_view_approvers && { view: "approvers", label: "Approvers", icon: "check" },
    caps.can_register_face && { view: "face", label: "Face register", icon: "camera" },
    caps.can_approve_face && { view: "faceApproval", label: "Face approvals", icon: "check" },
    caps.can_manage_locations && { view: "location", label: "Locations / geofence", icon: "pin" },
    caps.can_manage_leave_policy && { view: "leavePolicy", label: "Leave policy", icon: "leaf" },
    caps.can_manage_shift_policy && { view: "shiftPolicy", label: "Shift policy", icon: "clock" },
    caps.can_manage_regularization && { view: "regularization", label: "Regularization", icon: "refresh" }
  ].filter(Boolean) as any[];
  return (
    <>
      <div className={`scrim ${open ? "show" : ""}`} onClick={onClose} />
      <aside className={`drawer ${open ? "open" : ""}`}>
        <div className="drawer-head">
          <span className="avatar" style={{ width: 44, height: 44, borderRadius: 13, fontSize: 16 }}>{empInitials(name)}</span>
          <div><strong style={{ fontSize: 14, letterSpacing: "-.02em" }}>{name}</strong><div style={{ fontSize: 11, color: "var(--faint)" }}>{emp}</div></div>
        </div>
        {normal.map((item) => <button key={item.view} className={`dlink ${active === item.view ? "on" : ""}`} onClick={() => openView(item.view)}><Ic name={item.icon} /> {item.label}</button>)}
        {admin.length > 0 && <><div className="dsec">Admin</div>{admin.map((item) => <button key={item.view} className={`dlink ${active === item.view ? "on" : ""}`} onClick={() => openView(item.view)}><Ic name={item.icon} /> {item.label}</button>)}</>}
        <div className="dsec">Account</div>
        <button className={`dlink ${active === "profile" ? "on" : ""}`} onClick={() => openView("profile")}><Ic name="user" /> Profile</button>
        <button className={`dlink ${active === "settings" ? "on" : ""}`} onClick={() => openView("settings")}><Ic name="gear" /> Settings</button>
        <button className="dlink" onClick={onLogout}><Ic name="logout" /> Log out</button>
      </aside>
    </>
  );
}

/* ---------- shared presentational helpers ---------- */
function Stat({ icon, label, value, small, ink }: any) {
  return <div className="stat"><div className="lab">{icon && <Ic name={icon} />} {label}</div><div className={`val ${ink ? "ink" : ""}`}>{value}{small && <small>{small}</small>}</div></div>;
}
function Mod({ icon, cyan, title, sub, onClick }: any) {
  return <button className="mod" type="button" onClick={onClick}><span className={`mi ${cyan ? "cy" : ""}`}><Ic name={icon} /></span><span className="mt"><strong>{title}</strong><span>{sub}</span></span><span className="chev"><Ic name="chevron" /></span></button>;
}
function Chip({ kind = "info", icon, children }: any) {
  return <span className={`chip ${kind}`}>{icon && <Ic name={icon} />}{children}</span>;
}
function statusChipKind(status?: string) {
  const s = String(status || "").toLowerCase();
  if (s.includes("approv")) return "ok";
  if (s.includes("reject") || s.includes("cancel")) return "err";
  return "pend";
}

function Dashboard({ data, open, caps, flash, session }: any) {
  const emp = data?.employee || session?.employee || {};
  const t = data?.today || {};
  const status = data?.attendance_status || {};
  const name = emp.employee_name || "Employee";
  const guarded = (allowed: boolean, next: View, message: string) => { if (!allowed) { flash("Not configured", message); return; } open(next); };
  const checkedIn = status.status === "in" || t.status === "In";
  const inLabel = status.in_time_label || formatTime(status.in_time);
  const leaveBal = (data?.leave_balance || []).reduce((a: number, b: any) => a + Number(b.unused_leaves || 0), 0);
  return <>
    <p className="eyebrow">Today overview</p>
    <h1 className="h1">{greeting()},<br /><span className="g">{name}</span></h1>
    <p className="sub"><span className="num">{fmtDMY(t.date || today())}</span> · {data?.shift || "General shift"}</p>

    <div className="card accent" style={{ marginTop: 15, display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ flex: 1 }}>
        {checkedIn ? <Chip kind="ok" icon="check">Checked in</Chip> : <Chip kind="pend" icon="clock">Not marked</Chip>}
        <div style={{ marginTop: 9, fontSize: 11.5, color: "var(--muted)" }}>{checkedIn && inLabel ? <>Since <span className="num cy">{inLabel}</span></> : "No check-in yet today"}</div>
      </div>
      <div style={{ textAlign: "right" }}>
        <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: "var(--cyan)", letterSpacing: "-.02em" }}>{status.working_hours || (status.in_time && status.out_time ? formatWorkingHours(status.in_time, status.out_time) : "—")}</div>
        <div style={{ fontSize: 10, color: "var(--faint)", textTransform: "uppercase", letterSpacing: ".08em" }}>worked today</div>
      </div>
    </div>

    <div className="grid2" style={{ marginTop: 12 }}>
      <Stat icon="calendar" label="Status" value={<span style={{ fontSize: 17 }}>{t.status || "Not Marked"}</span>} />
      <Stat icon="leaf" label="Leave bal." value={leaveBal || 0} small=" days" />
    </div>

    <div className="sec-lab">Quick actions</div>
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {caps.can_mark_attendance && <Mod icon="fp" title="Mark attendance" sub="Face + geofence verified" onClick={() => guarded(caps.can_mark_attendance, "attendance", "Employee mapping is not configured. Please contact HR.")} />}
      <Mod icon="clock" cyan title="Attendance history" sub="Timeline & monthly summary" onClick={() => open("history")} />
      {caps.can_apply_leave && <Mod icon="leaf" title="Apply for leave" sub="Submit & track requests" onClick={() => guarded(caps.can_apply_leave, "leave", "Leave access is not configured. Please contact HR.")} />}
      {caps.can_apply_on_duty && <Mod icon="briefcase" title="On Duty" sub="Apply & track requests" onClick={() => guarded(caps.can_apply_on_duty, "onDuty", "On Duty access is not configured. Please contact HR.")} />}
      {caps.can_apply_late_early && <Mod icon="clock" cyan title="Late / Early request" sub="Apply for late coming or early going" onClick={() => guarded(caps.can_apply_late_early, "lateEarly", "Employee mapping is not configured. Please contact HR.")} />}
      {caps.can_request_regularization && <Mod icon="refresh" title="Regularization request" sub="Missed check-in/out? Request here" onClick={() => guarded(caps.can_request_regularization, "regularizationRequest", "Employee mapping is not configured. Please contact HR.")} />}
      <Mod icon="bell" cyan title="Notifications" sub="Alerts & reminders" onClick={() => open("notifications")} />
    </div>
  </>;
}

function Attendance({ flash, onMarked, initialStatus, onCameraActiveChange, open, caps }: any) {
  const [status, setStatus] = useState<any>(initialStatus || null);
  const [verifyType, setVerifyType] = useState<"IN" | "OUT" | null>(null);
  const [coords, setCoords] = useState<GeolocationCoordinates | null>(null);
  const [gpsStatus, setGpsStatus] = useState("Captured on submit");
  const [cameraStatus, setCameraStatus] = useState("Starting camera...");
  const [faceStatus, setFaceStatus] = useState("Looking for face...");
  const [submitting, setSubmitting] = useState(false);
  const [nowText, setNowText] = useState("");
  const [cameraReady, setCameraReady] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const autoSubmitRef = useRef(false);
  // Speed: GPS fix started before the user taps Mark In/Out, and stage timings sent with the punch (stored in Punch audit).
  const prewarmRef = useRef<{ p: Promise<GeolocationCoordinates>; at: number } | null>(null);
  const tapRef = useRef(0); const camMsRef = useRef(0); const triesRef = useRef(0);

  useEffect(() => { setStatus(initialStatus || null); }, [initialStatus]);
  useEffect(() => {
    if (!status) call(API.getTodayAttendanceStatus).then(setStatus).catch((error) => flash("Unable to load", cleanErrorMessage(error)));
  }, []);
  useEffect(() => {
    const tick = () => setNowText(new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, []);

  const requestPosition = (highAccuracy: boolean, timeout: number) => new Promise<GeolocationCoordinates>((resolve, reject) => {
    navigator.geolocation.getCurrentPosition((pos) => resolve(pos.coords), (err) => reject(err), { enableHighAccuracy: highAccuracy, timeout, maximumAge: 30000 });
  });
  const getCurrentLocation = async (): Promise<GeolocationCoordinates> => {
    if (!navigator.geolocation) throw new Error("Location is not available on this device.");
    try { return await requestPosition(true, 15000); }
    catch (err: any) {
      if (err && err.code === 1) throw new Error("Location is turned off for this app. Please enable location/GPS and allow access, then try again.");
      try { return await requestPosition(false, 20000); }
      catch { throw new Error("Could not get your location. Please turn on GPS/location and try again in an open area."); }
    }
  };
  // The GPS fix is the slowest part on a cold phone, so it starts as soon as Mark In / Mark Out is possible (screen opened) and the
  // punch re-uses it while it is fresh - the verify screen then never waits for GPS after the photo.
  const prewarmFresh = () => { const pre = prewarmRef.current; return pre && Date.now() - pre.at < 45000 ? pre : null; };
  const startLocation = (): Promise<GeolocationCoordinates> => {
    const pre = prewarmFresh();
    return pre ? pre.p.catch(() => getCurrentLocation()) : getCurrentLocation();
  };
  useEffect(() => {
    if (verifyType || !navigator.geolocation) return;
    if (!status?.can_mark_in && !status?.can_mark_out) return;
    const pre = prewarmRef.current;
    if (pre && Date.now() - pre.at < 20000) return;
    const p = getCurrentLocation(); p.catch(() => { /* retried when the punch starts */ });
    prewarmRef.current = { p, at: Date.now() };
  }, [status?.can_mark_in, status?.can_mark_out, verifyType]);

  const stopVerifyCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraReady(false);
    onCameraActiveChange?.(false);
  };
  const closeVerify = () => {
    stopVerifyCamera(); autoSubmitRef.current = false; triesRef.current = 0; setVerifyType(null); setSubmitting(false);
    setCoords(null); setGpsStatus("Captured on submit"); setCameraStatus("Starting camera..."); setFaceStatus("Looking for face...");
  };
  useEffect(() => {
    const close = () => closeVerify();
    window.addEventListener("jew-hrms-close-attendance-verify", close);
    return () => { window.removeEventListener("jew-hrms-close-attendance-verify", close); stopVerifyCamera(); };
  }, []);

  const startVerifyCamera = async () => {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera is not available on this device.");
    setCameraStatus("Starting camera..."); setFaceStatus("Looking for face...");
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 720 }, height: { ideal: 720 } }, audio: false });
    streamRef.current = stream; onCameraActiveChange?.(true);
    const video = videoRef.current; const canvas = canvasRef.current;
    if (!video || !canvas) throw new Error("Camera is not ready.");
    video.srcObject = stream; video.muted = true; video.playsInline = true;
    await new Promise<void>((resolve) => { if (video.readyState >= 2) return resolve(); video.onloadedmetadata = () => resolve(); });
    await video.play();
    for (let i = 0; i < 20; i += 1) { if (video.videoWidth > 0 && video.videoHeight > 0) break; await new Promise((resolve) => window.setTimeout(resolve, 100)); }
    if (!video.videoWidth || !video.videoHeight) throw new Error("Camera is still starting. Please try again.");
    setCameraReady(true); setCameraStatus("Camera ready"); setFaceStatus("Ready to capture");
    camMsRef.current = Math.round(performance.now() - tapRef.current);
  };

  useEffect(() => {
    if (!verifyType) return;
    let cancelled = false;
    autoSubmitRef.current = false;
    stopVerifyCamera(); setCoords(null); setGpsStatus("Captured on submit");
    startVerifyCamera().catch((error) => {
      if (!cancelled) { setCameraStatus("Camera unavailable"); setFaceStatus("Camera permission required"); flash("Camera failed", cleanErrorMessage(error)); }
    });
    return () => { cancelled = true; };
  }, [verifyType]);

  useEffect(() => {
    if (verifyType && cameraReady && !submitting && !autoSubmitRef.current) { autoSubmitRef.current = true; void confirmVerify(); }
  }, [verifyType, cameraReady]);

  const captureCurrentFrame = () => {
    const video = videoRef.current;
    if (!video || !streamRef.current || !video.videoWidth || !video.videoHeight) throw new Error("Camera is still starting. Please try again.");
    const frame = regGrabFrame(video, 560, 0.85);
    if (!frame) throw new Error("Camera is still starting. Please try again.");
    return frame;
  };
  const openVerify = (type: "IN" | "OUT") => {
    if (type === "IN" && !status?.can_mark_in) { flash("Attendance", !faceReady ? "Face not registered. Please contact admin." : !locationReady ? "Location not assigned. Please contact admin." : "Mark In is not available now."); return; }
    if (type === "OUT" && !status?.can_mark_out) { flash("Attendance", !faceReady ? "Face not registered. Please contact admin." : !locationReady ? "Location not assigned. Please contact admin." : "Mark Out is not available now."); return; }
    tapRef.current = performance.now(); camMsRef.current = 0; triesRef.current = 0;
    setVerifyType(type);
  };
  const runActiveLiveness = async (challenge: any): Promise<string[]> => {
    const frames = await liveFilmScan(() => (streamRef.current ? videoRef.current : null), challenge.steps || [], setFaceStatus, 480);
    setFaceStatus("Verifying live scan...");
    return frames;
  };
  const confirmVerify = async () => {
    if (!verifyType || submitting) return;
    setSubmitting(true);
    // GPS runs in parallel with the photo (or was already pre-warmed) on every site - the punch never waits for it afterwards.
    const liveOn = isLiveScanSite();
    triesRef.current += 1;
    const tConfirm = performance.now();
    const usedPre = Boolean(prewarmFresh());
    const locPromise = startLocation();
    locPromise.catch(() => { /* surfaced where it is awaited */ });
    try {
      // Ask the server whether this site needs the active live scan. Any failure here (or "enabled: false")
      // simply keeps the normal single-selfie flow below - the server still enforces its own rules.
      let challenge: any = null;
      if (liveOn) { try { challenge = await call(API.livenessStart, { type: verifyType }); } catch { challenge = null; } }
      let liveFrames: string[] | null = null;
      if (challenge?.enabled) {
        if (challenge.locked) throw new Error(challenge.message || "Too many failed attempts. Please try again later.");
        liveFrames = await runActiveLiveness(challenge);
      }
      setFaceStatus("Face captured");
      const tCap = performance.now();
      const capturedImage = liveFrames ? liveFrames[0] : captureCurrentFrame();
      const capMs = Math.round(performance.now() - tCap);
      if (!capturedImage) { flash("Face required", "Please keep your full face inside the frame."); return; }
      setGpsStatus("Capturing GPS...");
      const tGps = performance.now();
      const currentCoords = await locPromise;
      const gpsMs = Math.round(performance.now() - tGps);
      setCoords(currentCoords); setGpsStatus("Captured"); setFaceStatus("Verifying face");
      const firstTry = triesRef.current === 1;
      const clientTimings = JSON.stringify({ v: 1, try: triesRef.current, cam: firstTry ? camMsRef.current : 0, cap: capMs, gps: gpsMs, pw: usedPre ? 1 : 0, lead: Math.round(performance.now() - (firstTry ? tapRef.current : tConfirm)) });
      const result = await call(API.markAttendance, { type: verifyType, face_image: capturedImage, latitude: currentCoords.latitude, longitude: currentCoords.longitude, accuracy: currentCoords.accuracy, timestamp: new Date().toISOString(), device_id: getDeviceId(), client_timings: clientTimings, ...(liveFrames ? { liveness_challenge: challenge.token, liveness_frames: liveFrames } : {}) });
      // The punch is saved server-side at this point: a hiccup refreshing the dashboard
      // must not leave the user stuck on the verify screen.
      if (result?.attendance_status) setStatus(result.attendance_status);
      // The punch is saved - close at once and refresh the dashboard in the background (status is already set above).
      flash("Attendance", result.message || toastText(result.code));
      closeVerify();
      void Promise.resolve(onMarked?.()).catch(() => { /* non-fatal - status refreshes when the tab reopens */ });
    } catch (error) {
      setFaceStatus(cleanErrorMessage(error));
      // The server may have recorded the punch before the error came back (e.g. a step
      // after the check-in failed). Resync, so the screen reflects reality instead of
      // offering the same Mark In / Mark Out again.
      let done = false;
      let doneMsg = verifyType === "OUT" ? "Mark Out recorded." : "Mark In recorded.";
      try {
        const fresh: any = await call(API.getTodayAttendanceStatus);
        setStatus(fresh);
        // Leave the verify screen (and never offer the same Mark In / Mark Out
        // again) once today is settled - already punched, fully completed, or
        // HR-resolved. Only a genuinely still-open action stays for a retry.
        if (verifyType === "OUT") {
          done = fresh?.status === "completed" || fresh?.status === "regularized";
        } else {
          done = fresh?.status === "in" || fresh?.status === "completed" || fresh?.status === "regularized";
        }
        if (fresh?.status === "completed") doneMsg = "Attendance already completed for today.";
        else if (fresh?.status === "regularized") doneMsg = "Today’s attendance was already resolved by HR.";
      } catch {
        // Could not refresh (offline). Fall back to the error text: an "already
        // marked / completed / resolved" error still means the day is settled.
        const m = cleanErrorMessage(error).toLowerCase();
        done = m.includes("already") && (m.includes("complet") || m.includes("marked") || m.includes("resolved"));
        doneMsg = cleanErrorMessage(error);
      }
      if (done) {
        try { await onMarked?.(); } catch { /* non-fatal */ }
        flash("Attendance", doneMsg);
        closeVerify();
      } else {
        flash("Attendance failed", cleanErrorMessage(error));
      }
    } finally { setSubmitting(false); }
  };

  const faceReady = status?.face_registered ?? status?.face_status?.registered ?? true;
  const locationReady = status?.location_assigned ?? ((status?.location_details?.assigned || status?.location_status?.assigned || 0) > 0);
  const nextType: "IN" | "OUT" = status?.status === "in" ? "OUT" : "IN";
  const canUseAction = nextType === "IN" ? Boolean(status?.can_mark_in) : Boolean(status?.can_mark_out);
  const inLabel = status?.in_time_label || formatTime(status?.in_time);
  const outLabel = status?.out_time_label || formatTime(status?.out_time);
  const completed = status?.status === "completed";

  if (verifyType) {
    return (
      <div style={{ textAlign: "center" }}>
        <p className="eyebrow">Attendance verify</p>
        <h1 className="h1">Verify &amp; <span className="g">mark {verifyType === "IN" ? "in" : "out"}</span></h1>
        <div className="facebox" style={{ marginTop: 18, height: 300 }}>
          <span className="brk b1" /><span className="brk b2" /><span className="brk b3" /><span className="brk b4" />
          <video ref={videoRef} autoPlay muted playsInline />
          <canvas ref={canvasRef} hidden />
          <div className="scan"><span className="chip ok"><Ic name="camera" /> {faceStatus}</span></div>
        </div>
        <p className="sub" style={{ marginTop: 12 }}>Center your face in the frame — verification &amp; submit are automatic.</p>
        <p className="sub" style={{ marginTop: 6, fontSize: 11.5 }}>Your face photo, device and location are recorded to verify attendance.</p>
        <div style={{ marginTop: 12, display: "flex", gap: 9, justifyContent: "center", flexWrap: "wrap" }}>
          <Chip kind="info" icon="pin">{coords ? `${coords.latitude.toFixed(4)}, ${coords.longitude.toFixed(4)}` : gpsStatus}</Chip>
          <Chip kind="data" icon="clock">IST {nowText}</Chip>
        </div>
        <div style={{ marginTop: 16 }}>
          <button className="btn ok" type="button" disabled={!cameraReady || submitting} onClick={() => { autoSubmitRef.current = true; void confirmVerify(); }}>
            <Ic name="check" /> {submitting ? "Verifying…" : !cameraReady ? "Starting camera…" : `Retry mark ${verifyType === "IN" ? "in" : "out"}`}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ textAlign: "center" }}>
      <div className="ring" style={{ background: completed ? "conic-gradient(var(--ok) 0deg 360deg)" : status?.status === "in" ? "conic-gradient(var(--iris) 0deg 246deg,var(--surface-3) 246deg 360deg)" : "conic-gradient(var(--surface-3) 0deg 360deg)" }}>
        <div className="in">
          <div className="k">{completed ? "Out at" : status?.status === "in" ? "In at" : "Today"}</div>
          <div className="v">{completed ? (outLabel || "--:--") : status?.status === "in" ? (inLabel || "--:--") : "--:--"}</div>
          <div className="s">{completed ? (status?.working_hours || formatWorkingHours(status?.in_time, status?.out_time)) : status?.status_label || "Not marked yet"}</div>
        </div>
      </div>
      <div className="facebox" style={{ marginTop: 18 }}>
        <span className="brk b1" /><span className="brk b2" /><span className="brk b3" /><span className="brk b4" />
        <Ic name="camera" />
      </div>
      <p className="sub" style={{ marginTop: 12 }}>{!faceReady ? (status?.face_status?.pending ? "Your face is waiting for approval." : caps?.can_self_register_face ? "Face not registered. Register your face below." : "Face not registered. Please contact admin.") : !locationReady ? "Location not assigned. Please contact admin." : "Center your face in the frame — verification is automatic."}</p>
      {faceReady && locationReady && <p className="sub" style={{ marginTop: 4, fontSize: 11.5 }}>Your face photo, device and location are recorded to verify attendance.</p>}
      {!faceReady && caps?.can_self_register_face && <div style={{ marginTop: 12 }}><button className="btn" type="button" onClick={() => open?.("selfFace")}><Ic name="camera" /> {status?.face_status?.pending ? "Send a new face photo" : "Register my face"}</button></div>}
      {!completed && <div style={{ marginTop: 16 }}>
        <button className={`btn ${nextType === "IN" ? "ok" : ""}`} type="button" disabled={!canUseAction} onClick={() => openVerify(nextType)}><Ic name="check" /> Verify &amp; mark {nextType === "IN" ? "in" : "out"}</button>
      </div>}
      {completed && <div style={{ marginTop: 14 }}><Chip kind="ok" icon="check">Attendance completed</Chip></div>}
      <div style={{ marginTop: 12, display: "flex", gap: 9, justifyContent: "center", flexWrap: "wrap" }}>
        <Chip kind={locationReady ? "ok" : "pend"} icon="pin">{locationReady ? "Geofence OK" : "No location"}</Chip>
        <Chip kind={faceReady ? "info" : "pend"} icon="camera">{faceReady ? "Face ready" : "Face pending"}</Chip>
      </div>
    </div>
  );
}

function attendanceStatusLabel(a: any) {
  // Present is Present, full stop - no "(Missing Mark Out)" / "(Early Going)" /
  // etc. suffix, regardless of how the day got resolved. Only non-Present
  // statuses (Half Day, Absent) still show what caused them, since that's the
  // useful part there.
  if (String(a.status || "").toLowerCase() === "present") return a.status;
  const tags: string[] = [];
  if (a.regularization_issue_type) tags.push(a.regularization_issue_type);
  else {
    if (a.late_entry) tags.push("Late Coming");
    if (a.early_exit) tags.push("Early Going");
  }
  return tags.length ? `${a.status} (${tags.join(", ")})` : a.status;
}

function attendanceChipKind(status: string) {
  const s = String(status || "").toLowerCase();
  if (s.includes("present")) return "ok";
  if (s.includes("absent")) return "err";
  return "pend";
}

function History({ data }: any) {
  const checkins = data?.checkins || [];
  const attendance = data?.attendance || [];
  const present = attendance.filter((a: any) => String(a.status).toLowerCase().includes("present")).length;
  const attendanceByDate: Record<string, any> = {};
  attendance.forEach((a: any) => { attendanceByDate[String(a.attendance_date)] = a; });
  return <>
    <div className="grid2">
      <Stat icon="check" label="Present" value={present || attendance.length || 0} small={attendance.length ? `/${attendance.length}` : ""} />
      <Stat icon="clock" label="Records" value={checkins.length} />
    </div>
    <div className="sec-lab">Recent checkins</div>
    <div className="card list" style={{ padding: "4px 15px" }}>
      {checkins.length ? checkins.map((row: any) => {
        const a = attendanceByDate[String(row.time).slice(0, 10)];
        return <div className="row" key={row.name}><div className="date" style={{ width: 76 }}>{fmtDMY(row.time)}</div><div className="mid"><strong>{row.log_type}</strong><span>{String(row.time).slice(11, 16)}</span></div><Chip kind={a ? attendanceChipKind(a.status) : "pend"}>{a ? attendanceStatusLabel(a) : "Pending"}</Chip></div>;
      }) : <div className="empty"><h3>No records</h3><p>No attendance records found.</p></div>}
    </div>
  </>;
}

function Leave({ data, caps, flash, reload, me }: any) {
  const [form, setForm] = useState({ employee: "", leave_type: "", from_date: "", to_date: "", reason: "", half_day: false, half_day_date: "", half_day_type: "First Half", attachment: "" });
  const [attachmentName, setAttachmentName] = useState("");
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const onAttachmentChange = async (e: any) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setAttachmentBusy(true);
    try {
      const url = await uploadFile(file);
      setForm((f: any) => ({ ...f, attachment: url }));
      setAttachmentName(file.name);
    } catch (err) {
      flash("Upload failed", cleanErrorMessage(err));
    } finally {
      setAttachmentBusy(false);
    }
  };
  const clearAttachment = () => { setForm((f: any) => ({ ...f, attachment: "" })); setAttachmentName(""); };
  const [detail, setDetail] = useState<any>(null);
  const [waShare, setWaShare] = useState<any>(null);
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const canSelectEmployee = Boolean(caps?.can_apply_leave_for_others);
  // Employees can back-date up to LEAVE_BACKDATE_ALLOWED_DAYS (forgot to apply, or
  // applying same-morning for today) - backend enforces the same limit, and routes
  // any such application straight to HR instead of Dept Head/MD. Admins may
  // back-date further on behalf of others. "To" can never be before "From".
  const LEAVE_BACKDATE_ALLOWED_DAYS = 7;
  const toISODate = (d: Date) => { const p = (n: number) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`; };
  const todayStr = toISODate(new Date());
  const earliestBackdateStr = (() => { const d = new Date(); d.setDate(d.getDate() - LEAVE_BACKDATE_ALLOWED_DAYS); return toISODate(d); })();
  const minFrom = canSelectEmployee ? undefined : earliestBackdateStr;
  const isUrgent = !!(form.from_date && form.from_date <= todayStr);
  // Setting From also drags To forward if To is now earlier than From.
  const setFromDate = (v: string) => setForm((f: any) => ({ ...f, from_date: v, to_date: f.to_date && v && f.to_date < v ? v : f.to_date }));
  // Default the employee to the logged-in user. Admins can change it; a plain
  // employee sees their own (fixed) and the payload always resolves to them.
  useEffect(() => {
    if (me?.employee) setForm((f: any) => (f.employee ? f : { ...f, employee: me.employee }));
  }, [me?.employee]);
  // Half day is only valid for a single-day leave (From === To). Force it off otherwise.
  const singleDay = !!(form.from_date && form.to_date && form.from_date === form.to_date);
  useEffect(() => { if (!singleDay && form.half_day) setForm((f: any) => ({ ...f, half_day: false, half_day_date: "" })); }, [singleDay]);
  // Requested day count, mirroring the backend's own calc (api.py _check_casual_monthly_cap):
  // half day = 0.5, otherwise inclusive day span. Casual Leave is disabled beyond the cap.
  const requestedDays = form.half_day && singleDay
    ? 0.5
    : (form.from_date && form.to_date ? Math.round((new Date(form.to_date).getTime() - new Date(form.from_date).getTime()) / 86400000) + 1 : 0);
  // If Casual Leave is selected and the range grows past the cap (e.g. extending
  // "To" after picking CL for a short leave), clear the now-invalid selection.
  useEffect(() => {
    const selected = (data.selectable || []).find((t: any) => t.leave_type === form.leave_type);
    const isCasual = String(selected?.leave_type_name || selected?.leave_type || "").toLowerCase() === "casual leave";
    if (isCasual && requestedDays > CASUAL_LEAVE_MONTHLY_CAP) setForm((f: any) => ({ ...f, leave_type: "" }));
  }, [requestedDays]);
  const submit = async () => {
    const missing: string[] = [];
    if (!form.leave_type) missing.push("Leave type");
    if (!form.from_date) missing.push("From date");
    if (!form.to_date) missing.push("To date");
    if (form.half_day && !form.half_day_date) missing.push("Half day date");
    if (missing.length) { flash("Required fields", `${missing.join(", ")} ${missing.length > 1 ? "are" : "is"} required.`); return; }
    if (form.from_date > form.to_date) { flash("Invalid dates", "From Date cannot be after To Date."); return; }
    if (form.half_day && !singleDay) { flash("Half day", "Half day can only be applied on a single-day leave (From and To same day)."); return; }
    const result = await runAction("submit-leave", async () => {
      const base = canSelectEmployee ? form : { ...form, employee: undefined };
      const payload = { ...base, half_day_date: form.half_day ? form.from_date : "" };
      const response = await call(API.applyLeave, payload);
      await reload();
      return response;
    }, { successTitle: "Leave submitted", errorTitle: "Leave failed" });
    if (result) { setForm({ employee: me?.employee || "", leave_type: "", from_date: "", to_date: "", reason: "", half_day: false, half_day_date: "", half_day_type: "First Half", attachment: "" }); setAttachmentName(""); if ((result as any).whatsapp?.text) setWaShare((result as any).whatsapp); }
  };
  const cancelLeave = async (name: string) => {
    // reload() runs even on failure (e.g. "this request no longer exists") so a
    // stale row referencing an already-deleted record clears itself from the
    // list instead of staying stuck there for the user to keep tapping.
    const res: any = await runAction(`cancel-${name}`, async () => await call(API.cancelLeave, { name }), { successTitle: "Leave cancelled", successMessage: "Leave request updated.", errorTitle: "Cancel failed" });
    reload();
    if (res?.whatsapp?.text) setWaShare({ ...res.whatsapp, title: "Leave cancelled" });
  };
  const balances = data.balances || [];
  const selectable = data.selectable || [];
  // Live balance note for the currently-selected leave type.
  const selectedType = selectable.find((t: any) => t.leave_type === form.leave_type);
  const balanceHint = !form.leave_type
    ? ""
    : selectedType
      ? (selectedType.unlimited
          ? "This leave type has no balance limit."
          : selectedType.has_allocation === false
            ? "You have no allocation for this leave type — it may be rejected. Contact HR."
            : `Balance: ${Number(selectedType.balance ?? 0)} day${Number(selectedType.balance ?? 0) === 1 ? "" : "s"} remaining.`)
      : "";
  return <>
    {balances.length > 0 && <div className="grid2">{dedupeByLabel(balances.map((b: any) => ({ ...b, label: b.leave_type_name || b.leave_type }))).slice(0, 2).map((b: any, i: number) => <Stat key={i} label={b.leave_type_name || b.leave_type} value={Number(b.balance ?? b.total_leaves_allocated ?? 0)} small=" left" />)}</div>}
    <div className="card" style={{ marginTop: balances.length ? 12 : 0 }}>
      {canSelectEmployee
        ? <Select label="Employee" value={form.employee} onChange={(v: string) => setForm({ ...form, employee: v })} options={(data.employees || []).map((e: any) => ({ value: e.name, label: e.employee_name || e.name, description: e.description || e.name }))} />
        : <div className="field"><label>Employee</label><div className="inp" style={{ opacity: 0.9 }}><Ic name="user" /><span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{me?.employee_name || me?.employee || "You"}</span></div></div>}
      <Select label="Leave type" value={form.leave_type} onChange={(v: string) => setForm({ ...form, leave_type: v })} options={buildLeaveTypeOptions(data.selectable, data.types, requestedDays)} />
      <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 4 }}>You can take up to {CASUAL_LEAVE_MONTHLY_CAP} Casual Leave days and unlimited Privilege Leave (balance permitting) per month.</div>
      {balanceHint && <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 4 }}>{balanceHint}</div>}
      <div className="grid2" style={{ marginTop: 13 }}>
        <Field label="From" icon="calendar" type="date" value={form.from_date} min={minFrom} onChange={setFromDate} />
        <Field label="To" icon="calendar" type="date" value={form.to_date} min={form.from_date || minFrom} onChange={(v: string) => setForm({ ...form, to_date: v })} />
      </div>
      {null}
      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 13, color: "var(--muted)", fontSize: 12.5, fontWeight: 600, opacity: singleDay ? 1 : 0.5 }}>
        <input type="checkbox" style={{ accentColor: "var(--iris)" }} disabled={!singleDay} checked={form.half_day} onChange={(e) => setForm({ ...form, half_day: e.target.checked, half_day_date: e.target.checked ? form.from_date : "" })} /> Half day
      </label>
      {!singleDay && <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 4 }}>Half day only for a single-day leave (From and To same day).</div>}
      {form.half_day && singleDay && <Select label="Half day type" value={form.half_day_type} onChange={(v: string) => setForm({ ...form, half_day_type: v })} options={["First Half", "Second Half"]} />}
      <Field label="Reason" value={form.reason} onChange={(v: string) => setForm({ ...form, reason: v })} placeholder="Add a short note (optional)" />
      <div className="field">
        <label>Attachment (optional)</label>
        <div className="inp" style={{ justifyContent: "space-between" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0, cursor: attachmentBusy ? "default" : "pointer" }}>
            <Ic name="paperclip" />
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: attachmentName ? "inherit" : "var(--faint)" }}>
              {attachmentBusy ? "Uploading…" : (attachmentName || "Attach medical certificate, etc.")}
            </span>
            <input type="file" accept="image/*,.pdf" style={{ display: "none" }} onChange={onAttachmentChange} disabled={attachmentBusy} />
          </label>
          {form.attachment && !attachmentBusy && <button type="button" className="ibtn" aria-label="Remove attachment" onClick={clearAttachment}><Ic name="close" /></button>}
        </div>
      </div>
      <div style={{ marginTop: 16 }}><button className="btn" type="button" disabled={isAnyBusy} onClick={submit}><Ic name="check" /> {isBusy("submit-leave") ? "Submitting…" : "Submit leave request"}</button></div>
    </div>
    <div className="sec-lab">My requests</div>
    {(data.leaves || []).length ? data.leaves.map((l: any) => {
      const st = l.approval_status || l.jew_hrms_approval_status || l.status;
      const kind = statusChipKind(st);
      return <div className="qrow" key={l.name} role="button" tabIndex={0} style={{ cursor: "pointer" }} onClick={() => setDetail(l)} onKeyDown={(e) => { if (e.key === "Enter") setDetail(l); }}>
        <div className="qa"><Ic name={kind === "ok" ? "check" : kind === "err" ? "close" : "clock"} /></div>
        <div className="qt"><strong>{l.leave_type}{l.half_day ? " · Half day" : ""}</strong><span>{fmtDMY(l.from_date)} → {fmtDMY(l.to_date)}</span></div>
        {/approved|reject|cancel/i.test(st) ? <Chip kind={kind}>{st}</Chip>
          : <button className="btn danger sm" style={{ width: "auto", padding: "0 12px" }} type="button" disabled={isAnyBusy} onClick={(e) => { e.stopPropagation(); cancelLeave(l.name); }}>{isBusy(`cancel-${l.name}`) ? "…" : "Cancel"}</button>}
      </div>;
    }) : <div className="empty"><h3>No requests</h3><p>No leave requests yet.</p></div>}
    {waShare && <WhatsAppSheet wa={waShare} flash={flash} heading={waShare.title || "Leave submitted"} onClose={() => setWaShare(null)} />}
    {detail && <LeaveDetailSheet leave={detail} data={data} flash={flash} onClose={() => setDetail(null)}
      onCancelLeave={async (name: string) => { await cancelLeave(name); setDetail(null); }}
      onSaved={async (w?: any) => { setDetail(null); await reload(); if (w?.text) setWaShare(w); }} />}
  </>;
}

function LeaveDetailSheet({ leave, data, flash, onClose, onCancelLeave, onSaved }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const [editMode, setEditMode] = useState(false);
  const [form, setForm] = useState<any>({
    leave_type: leave.leave_type || "", from_date: leave.from_date || "", to_date: leave.to_date || "",
    half_day: !!leave.half_day, half_day_date: leave.half_day_date || leave.from_date || "",
    half_day_type: leave.custom_half_day_type || "First Half", reason: leave.description || "", attachment: leave.attachment || ""
  });
  const [attachmentName, setAttachmentName] = useState(leave.attachment ? String(leave.attachment).split("/").pop() : "");
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewType, setPreviewType] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [waShare, setWaShare] = useState<any>(null);
  const openWhatsApp = async (kind?: string) => {
    const r: any = await runAction("leave-whatsapp", async () => kind ? call(API.getRequestWhatsapp, { kind, name: leave.name }) : call(API.getLeaveWhatsapp, { name: leave.name }), { errorTitle: "WhatsApp" });
    if (r?.whatsapp?.text) setWaShare(r.whatsapp);
  };
  const [cancelReqMode, setCancelReqMode] = useState(false);
  const [cancelForm, setCancelForm] = useState({ from_date: leave.from_date || "", to_date: leave.to_date || "", reason: "" });
  const isCancelPending = leave.jew_cancel_status === "Pending HR Approval";
  const closePreview = () => { if (previewUrl) URL.revokeObjectURL(previewUrl); setPreviewUrl(null); setPreviewType(""); };
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);
  const viewAttachment = async () => {
    if (!leave.attachment || previewBusy) return;
    setPreviewBusy(true);
    try {
      const res = await fetch(leave.attachment, { credentials: "include" });
      if (!res.ok) throw new Error("Unable to load attachment.");
      const blob = await res.blob();
      setPreviewUrl(URL.createObjectURL(blob));
      setPreviewType(blob.type || "");
    } catch (err) {
      flash("Attachment failed", cleanErrorMessage(err));
    } finally {
      setPreviewBusy(false);
    }
  };
  const singleDay = !!(form.from_date && form.to_date && form.from_date === form.to_date);
  useEffect(() => { if (!singleDay && form.half_day) setForm((f: any) => ({ ...f, half_day: false, half_day_date: "" })); }, [singleDay]);
  const requestedDays = form.half_day && singleDay
    ? 0.5
    : (form.from_date && form.to_date ? Math.round((new Date(form.to_date).getTime() - new Date(form.from_date).getTime()) / 86400000) + 1 : 0);
  const onAttachmentChange = async (e: any) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setAttachmentBusy(true);
    try {
      const url = await uploadFile(file);
      setForm((f: any) => ({ ...f, attachment: url }));
      setAttachmentName(file.name);
    } catch (err) {
      flash("Upload failed", cleanErrorMessage(err));
    } finally {
      setAttachmentBusy(false);
    }
  };
  const st = leave.approval_status || leave.jew_hrms_approval_status || leave.status;
  const kind = statusChipKind(st);
  const save = async () => {
    if (!form.leave_type || !form.from_date || !form.to_date) { flash("Required fields", "Leave type, From date and To date are required."); return; }
    if (form.from_date > form.to_date) { flash("Invalid dates", "From Date cannot be after To Date."); return; }
    const result = await runAction("save-leave-edit", async () => {
      const payload = { name: leave.name, ...form, half_day_date: form.half_day ? form.from_date : "" };
      return await call(API.updateLeave, payload);
    }, { successTitle: "Leave updated", errorTitle: "Update failed" });
    if (result) await onSaved();
  };
  const requestCancellation = async () => {
    if (!cancelForm.from_date || !cancelForm.to_date) { flash("Required fields", "From date and To date are required."); return; }
    if (cancelForm.from_date > cancelForm.to_date) { flash("Invalid dates", "From Date cannot be after To Date."); return; }
    if (!cancelForm.reason.trim()) { flash("Reason required", "Please enter a reason for the cancellation."); return; }
    const result = await runAction("request-cancel", async () => {
      return await call(API.requestLeaveCancellation, { name: leave.name, from_date: cancelForm.from_date, to_date: cancelForm.to_date, reason: cancelForm.reason });
    }, { successTitle: "Cancellation requested", successMessage: "Sent to HR for approval.", errorTitle: "Request failed" });
    if (result) await onSaved((result as any).whatsapp?.text ? { ...(result as any).whatsapp, title: "Cancellation request sent" } : undefined);
  };
  return <>
    {waShare && <WhatsAppSheet wa={waShare} flash={flash} onClose={() => setWaShare(null)} />}
    {createPortal(
    <div className="sheet-layer" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !isAnyBusy) onClose(); }}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label="Leave request">
        <div className="sheet-head"><strong>{editMode ? "Edit leave request" : cancelReqMode ? "Request cancellation" : "Leave request"}</strong><button className="ibtn" type="button" aria-label="Close" disabled={isAnyBusy} onClick={onClose}><Ic name="close" /></button></div>
        <div className="sheet-body">
          {!editMode && !cancelReqMode ? <>
            <div className="sheet-row"><span>Leave type</span><span>{leave.leave_type}</span></div>
            <div className="sheet-row"><span>Dates</span><span>{fmtDMY(leave.from_date)} → {fmtDMY(leave.to_date)}{leave.half_day ? " · Half day" : ""}</span></div>
            <div className="sheet-row"><span>Status</span><span><Chip kind={kind}>{st}</Chip></span></div>
            {leave.description && <div className="sheet-row"><span>Reason</span><span>{leave.description}</span></div>}
            {leave.reject_reason && <div className="sheet-row"><span>Reject reason</span><span>{leave.reject_reason}</span></div>}
            {leave.attachment && <div className="sheet-row"><span>Attachment</span><span><button type="button" onClick={viewAttachment} disabled={previewBusy} style={{ background: "none", border: "none", padding: 0, color: "var(--iris)", fontWeight: 600, fontSize: "inherit", cursor: previewBusy ? "default" : "pointer" }}>{previewBusy ? "Loading…" : "View file"}</button></span></div>}
            {isCancelPending && <div className="sheet-row"><span>Cancellation</span><span><Chip kind="pend">Pending HR Approval</Chip></span></div>}
            {leave.jew_cancel_status === "Rejected" && leave.jew_cancel_remarks && <div className="sheet-row"><span>Cancellation rejected</span><span>{leave.jew_cancel_remarks}</span></div>}
            <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
              {leave.editable && <button className="btn" type="button" style={{ flex: 1 }} onClick={() => setEditMode(true)}><Ic name="pen" /> Edit</button>}
              {/approved/i.test(st) && !isCancelPending && <button className="btn ghost" type="button" style={{ flex: 1 }} onClick={() => { setCancelForm({ from_date: leave.from_date, to_date: leave.to_date, reason: "" }); setCancelReqMode(true); }}>Request cancellation</button>}
              {!/approved|reject|cancel/i.test(st) && <button className="btn danger" type="button" style={{ flex: 1 }} disabled={isAnyBusy} onClick={() => onCancelLeave(leave.name)}>Cancel request</button>}
            </div>
            {!/reject/i.test(st) && <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
              {/cancel/i.test(st)
                ? <button className="btn ghost" type="button" disabled={isAnyBusy} onClick={() => openWhatsApp("leave_cancelled")}><Ic name="send" /> {isBusy("leave-whatsapp") ? "Preparing…" : "Send cancelled message to WhatsApp"}</button>
                : <>
                    <button className="btn ghost" type="button" disabled={isAnyBusy} onClick={() => openWhatsApp()}><Ic name="send" /> {isBusy("leave-whatsapp") ? "Preparing…" : "Send to WhatsApp group"}</button>
                    {isCancelPending && <button className="btn ghost" type="button" disabled={isAnyBusy} onClick={() => openWhatsApp("leave_cancel_request")}><Ic name="send" /> Send cancellation request to WhatsApp</button>}
                  </>}
            </div>}
          </> : editMode ? <>
            <Select label="Leave type" value={form.leave_type} onChange={(v: string) => setForm({ ...form, leave_type: v })} options={buildLeaveTypeOptions(data.selectable, data.types, requestedDays)} />
            <div className="grid2" style={{ marginTop: 13 }}>
              <Field label="From" icon="calendar" type="date" value={form.from_date} onChange={(v: string) => setForm((f: any) => ({ ...f, from_date: v, to_date: f.to_date && v && f.to_date < v ? v : f.to_date }))} />
              <Field label="To" icon="calendar" type="date" value={form.to_date} min={form.from_date} onChange={(v: string) => setForm({ ...form, to_date: v })} />
            </div>
            <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 13, color: "var(--muted)", fontSize: 12.5, fontWeight: 600, opacity: singleDay ? 1 : 0.5 }}>
              <input type="checkbox" style={{ accentColor: "var(--iris)" }} disabled={!singleDay} checked={form.half_day} onChange={(e) => setForm({ ...form, half_day: e.target.checked, half_day_date: e.target.checked ? form.from_date : "" })} /> Half day
            </label>
            {form.half_day && singleDay && <Select label="Half day type" value={form.half_day_type} onChange={(v: string) => setForm({ ...form, half_day_type: v })} options={["First Half", "Second Half"]} />}
            <Field label="Reason" value={form.reason} onChange={(v: string) => setForm({ ...form, reason: v })} placeholder="Add a short note (optional)" />
            <div className="field">
              <label>Attachment (optional)</label>
              <div className="inp" style={{ justifyContent: "space-between" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0, cursor: attachmentBusy ? "default" : "pointer" }}>
                  <Ic name="paperclip" />
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: attachmentName ? "inherit" : "var(--faint)" }}>
                    {attachmentBusy ? "Uploading…" : (attachmentName || "Attach medical certificate, etc.")}
                  </span>
                  <input type="file" accept="image/*,.pdf" style={{ display: "none" }} onChange={onAttachmentChange} disabled={attachmentBusy} />
                </label>
                {form.attachment && !attachmentBusy && <button type="button" className="ibtn" aria-label="Remove attachment" onClick={() => { setForm((f: any) => ({ ...f, attachment: "" })); setAttachmentName(""); }}><Ic name="close" /></button>}
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
              <button className="btn ghost" type="button" style={{ flex: 1 }} disabled={isAnyBusy} onClick={() => setEditMode(false)}>Discard</button>
              <button className="btn" type="button" style={{ flex: 1 }} disabled={isAnyBusy || attachmentBusy} onClick={save}><Ic name="check" /> {isBusy("save-leave-edit") ? "Saving…" : "Save changes"}</button>
            </div>
          </> : <>
            <div style={{ fontSize: 11.5, color: "var(--muted)", marginBottom: 4 }}>Approved period: {fmtDMY(leave.from_date)} → {fmtDMY(leave.to_date)}. Cancel the whole leave, or just part of it.</div>
            <div className="grid2">
              <Field label="Cancel from" icon="calendar" type="date" value={cancelForm.from_date} min={leave.from_date} max={leave.to_date} onChange={(v: string) => setCancelForm((f: any) => ({ ...f, from_date: v, to_date: f.to_date && v && f.to_date < v ? v : f.to_date }))} />
              <Field label="Cancel to" icon="calendar" type="date" value={cancelForm.to_date} min={cancelForm.from_date || leave.from_date} max={leave.to_date} onChange={(v: string) => setCancelForm({ ...cancelForm, to_date: v })} />
            </div>
            <Field label="Reason" value={cancelForm.reason} onChange={(v: string) => setCancelForm({ ...cancelForm, reason: v })} placeholder="Why are you cancelling?" />
            <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
              <button className="btn ghost" type="button" style={{ flex: 1 }} disabled={isAnyBusy} onClick={() => setCancelReqMode(false)}>Discard</button>
              <button className="btn" type="button" style={{ flex: 1 }} disabled={isAnyBusy} onClick={requestCancellation}><Ic name="check" /> {isBusy("request-cancel") ? "Sending…" : "Send to HR"}</button>
            </div>
          </>}
        </div>
      </div>
    </div>, document.body)}
    {previewUrl && createPortal(
      <div className="sheet-layer" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) closePreview(); }}>
        <div className="sheet" role="dialog" aria-modal="true" aria-label="Attachment preview">
          <div className="sheet-head"><strong>Attachment</strong><button className="ibtn" type="button" aria-label="Close" onClick={closePreview}><Ic name="close" /></button></div>
          <div className="sheet-body" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
            {previewType.startsWith("image/")
              ? <img src={previewUrl} alt="Attachment" style={{ maxWidth: "100%", borderRadius: 12 }} />
              : <iframe src={previewUrl} title="Attachment" style={{ width: "100%", height: "60dvh", border: "none", borderRadius: 12, background: "#fff" }} />}
            <a href={previewUrl} download="attachment" className="btn ghost sm" style={{ width: "auto", padding: "0 16px" }}>Download</a>
          </div>
        </div>
      </div>, document.body)}
  </>;
}

function TypeToggle({ label, value, onChange, options }: any) {
  // A plain checkbox-style pair of buttons for a 2-option choice — the searchable
  // dropdown modal used elsewhere is overkill when there are only two options to
  // pick from; tap either one directly.
  return <div className="field">
    <label>{label}</label>
    <div style={{ display: "flex", gap: 9 }}>
      {options.map((o: any) => {
        const val = typeof o === "string" ? o : o.value;
        const optLabel = typeof o === "string" ? o : o.label;
        const disabled = typeof o === "string" ? false : !!o.disabled;
        const note = typeof o === "string" ? "" : o.description;
        const selected = value === val;
        return <button key={val} type="button" disabled={disabled}
          className={`btn sm ${selected ? "" : "ghost"}`}
          style={{ flex: 1, opacity: disabled ? 0.45 : 1 }}
          onClick={() => { if (!disabled) onChange(val); }}>
          {optLabel}{note ? ` (${note})` : ""}
        </button>;
      })}
    </div>
  </div>;
}

function LateEarly({ data, flash, reload }: any) {
  const [form, setForm] = useState({ application_type: "Late Coming", application_date: "", expected_time: "", reason: "" });
  const [justSubmitted, setJustSubmitted] = useState<any>(null);
  const wa = useRequestWhatsApp("late_early", flash);
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const submit = async () => {
    if (!form.application_date || !form.expected_time || !form.reason) { flash("Required fields", "Date, expected time and reason are required."); return; }
    setJustSubmitted(null);
    const submittedForm = form;
    const result = await runAction("submit-late-early", async () => {
      const response = await call(API.applyLateEarly, form);
      await reload();
      return response;
    }, { successTitle: "Application submitted", errorTitle: "Application failed" });
    if (result) {
      setForm({ application_type: "Late Coming", application_date: "", expected_time: "", reason: "" });
      setJustSubmitted(submittedForm);
      if ((result as any).whatsapp?.text) wa.setWaShare({ ...(result as any).whatsapp, title: "Request submitted" });
    }
  };
  const cancelApplication = async (name: string) => {
    const res: any = await runAction(`cancel-${name}`, async () => { const r = await call(API.cancelLateEarly, { name }); reload(); return r; }, { successTitle: "Cancelled", successMessage: "Application cancelled.", errorTitle: "Cancel failed" });
    if (res?.whatsapp?.text) wa.setWaShare({ ...res.whatsapp, title: "Application cancelled" });
  };
  const requestCancellation = async (name: string) => {
    const reason = window.prompt("Reason for cancellation") || "";
    if (!reason.trim()) { flash("Reason required", "Please enter a reason for the cancellation."); return; }
    const res: any = await runAction(`requestcancel-${name}`, async () => { const r = await call(API.requestLateEarlyCancellation, { name, reason }); reload(); return r; }, { successTitle: "Cancellation requested", successMessage: "Sent to HR for approval.", errorTitle: "Request failed" });
    if (res?.whatsapp?.text) wa.setWaShare({ ...res.whatsapp, title: "Cancellation request sent" });
  };
  const applications = data.applications || [];
  const thisMonth = today().slice(0, 7);
  const activeThisMonth = (type: string) => applications.filter((a: any) => a.application_type === type && ["Pending", "Pending HR", "Approved"].includes(a.status) && String(a.application_date || "").startsWith(thisMonth)).length;
  const usedLateComing = activeThisMonth("Late Coming");
  const usedEarlyGoing = activeThisMonth("Early Going");
  const LATE_EARLY_MONTHLY_CAP = 2;
  // If the selected type just hit its cap (e.g. a request was submitted while this
  // screen was open), clear the selection rather than leave a disabled type chosen.
  useEffect(() => {
    const capped = (form.application_type === "Late Coming" && usedLateComing >= LATE_EARLY_MONTHLY_CAP)
      || (form.application_type === "Early Going" && usedEarlyGoing >= LATE_EARLY_MONTHLY_CAP);
    if (capped) setForm((f: any) => ({ ...f, application_type: "" }));
  }, [usedLateComing, usedEarlyGoing]);
  return <>
    {justSubmitted && <div className="card" style={{ marginBottom: 12, borderColor: "var(--ok, #1a7f45)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div className="qa" style={{ width: 34, height: 34, borderRadius: 10, background: "var(--ok-bg, #eaf6ee)", color: "var(--ok, #1a7f45)", display: "grid", placeItems: "center" }}><Ic name="check" style={{ width: 16, height: 16 }} /></div>
        <div style={{ flex: 1 }}>
          <strong style={{ fontSize: 13.5 }}>Submitted</strong>
          <div style={{ fontSize: 11.5, color: "var(--faint)" }}>{justSubmitted.application_type} · {fmtDMY(justSubmitted.application_date)} · pending Department Head review</div>
        </div>
      </div>
    </div>}
    <div className="card accent" style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ flex: 1 }}><Chip kind={usedLateComing >= LATE_EARLY_MONTHLY_CAP ? "err" : "pend"} icon="clock">Late Coming: {usedLateComing} of {LATE_EARLY_MONTHLY_CAP}</Chip></div>
      <div style={{ flex: 1 }}><Chip kind={usedEarlyGoing >= LATE_EARLY_MONTHLY_CAP ? "err" : "pend"} icon="clock">Early Going: {usedEarlyGoing} of {LATE_EARLY_MONTHLY_CAP}</Chip></div>
    </div>
    <div className="card" style={{ marginTop: 12 }}>
      <TypeToggle label="Type" value={form.application_type} onChange={(v: string) => setForm({ ...form, application_type: v })} options={[
        { value: "Late Coming", label: "Late Coming", disabled: usedLateComing >= LATE_EARLY_MONTHLY_CAP, description: usedLateComing >= LATE_EARLY_MONTHLY_CAP ? "Limit reached" : "" },
        { value: "Early Going", label: "Early Going", disabled: usedEarlyGoing >= LATE_EARLY_MONTHLY_CAP, description: usedEarlyGoing >= LATE_EARLY_MONTHLY_CAP ? "Limit reached" : "" },
      ]} />
      <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 4 }}>You can request up to {LATE_EARLY_MONTHLY_CAP} Late Coming and {LATE_EARLY_MONTHLY_CAP} Early Going per month.</div>
      <div className="grid2" style={{ marginTop: 13 }}>
        <Field label="Date" icon="calendar" type="date" value={form.application_date} onChange={(v: string) => setForm({ ...form, application_date: v })} />
        <Field label="Expected time" icon="clock" type="time" value={form.expected_time} onChange={(v: string) => setForm({ ...form, expected_time: v })} />
      </div>
      <Field label="Reason" value={form.reason} onChange={(v: string) => setForm({ ...form, reason: v })} placeholder="Why will you be late / need to leave early" />
      <div style={{ marginTop: 16 }}><button className="btn" type="button" disabled={isAnyBusy} onClick={submit}><Ic name="check" /> {isBusy("submit-late-early") ? "Submitting…" : "Submit application"}</button></div>
    </div>
    <div className="sec-lab">My requests</div>
    {applications.length ? applications.map((a: any) => {
      const kind = statusChipKind(a.status);
      return <div className="qrow" key={a.name}>
        <div className="qa"><Ic name={kind === "ok" ? "check" : kind === "err" ? "close" : "clock"} /></div>
        <div className="qt"><strong>{a.application_type}</strong><span>{fmtDMY(a.application_date)} · {a.expected_time}</span></div>
        {!/reject/i.test(String(a.status)) && (() => {
          const cancelled = /cancel/i.test(String(a.status)), pending = a.status === "Approved" && a.jew_cancel_status === "Pending HR Approval";
          const k = cancelled ? "late_early_cancelled" : pending ? "late_early_cancel_request" : "late_early";
          return <WaButton busy={wa.waBusy === a.name} label={cancelled ? "Send cancelled message to WhatsApp group" : pending ? "Send cancellation request to WhatsApp group" : undefined} onClick={() => wa.openWa(a.name, k)} />;
        })()}
        {a.status === "Pending" ? <button className="btn danger sm" style={{ width: "auto", padding: "0 12px" }} type="button" disabled={isAnyBusy} onClick={() => cancelApplication(a.name)}>{isBusy(`cancel-${a.name}`) ? "…" : "Cancel"}</button>
          : a.status === "Approved" && a.jew_cancel_status === "Pending HR Approval" ? <Chip kind="pend">Cancel Pending</Chip>
          : a.status === "Approved" ? <button className="btn ghost sm" style={{ width: "auto", padding: "0 12px" }} type="button" disabled={isAnyBusy} onClick={() => requestCancellation(a.name)}>{isBusy(`requestcancel-${a.name}`) ? "…" : "Request cancel"}</button>
          : <Chip kind={kind}>{a.status}</Chip>}
      </div>;
    }) : <div className="empty"><h3>No requests</h3><p>No Late Coming / Early Going requests yet.</p></div>}
    {wa.waShare && <WhatsAppSheet wa={wa.waShare} flash={flash} heading={wa.waShare.title} onClose={() => wa.setWaShare(null)} />}
  </>;
}

function OnDuty({ data, flash, reload }: any) {
  const DEFAULT_FORM = { from_date: "", to_date: "", explanation: "", from_time: "", to_time: "", od_mode: "full", half_day_date: "", half_day_type: "First Half" };
  const [form, setForm] = useState(DEFAULT_FORM);
  const wa = useRequestWhatsApp("on_duty", flash);
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const setFromDate = (v: string) => setForm((f: any) => ({ ...f, from_date: v, to_date: f.to_date && f.to_date < v ? v : f.to_date, half_day_date: f.half_day_date && (f.half_day_date < v || (f.to_date && f.half_day_date > f.to_date)) ? v : f.half_day_date }));
  const submit = async () => {
    if (!form.from_date || !form.to_date || !form.explanation) { flash("Required fields", "From date, to date and explanation are required."); return; }
    if (form.od_mode === "half" && !form.half_day_date) { flash("Half Day date required", "Please pick which date is the Half Day."); return; }
    if (form.od_mode === "time" && (!form.from_time || !form.to_time)) { flash("Time required", "Please pick From time and To time."); return; }
    const result = await runAction("submit-on-duty", async () => {
      const response = await call(API.requestOnDuty, {
        from_date: form.from_date,
        to_date: form.to_date,
        explanation: form.explanation,
        from_time: form.od_mode === "time" ? fmtTime12(form.from_time) : undefined,
        to_time: form.od_mode === "time" ? fmtTime12(form.to_time) : undefined,
        full_day: form.od_mode === "half" ? 0 : 1,
        half_day_date: form.od_mode === "half" ? form.half_day_date : undefined,
        half_day_part: form.od_mode === "half" ? form.half_day_type : undefined,
      });
      await reload();
      return response;
    }, { successTitle: "Request submitted", successMessage: "HR has been notified.", errorTitle: "Request failed" });
    if (result) { setForm(DEFAULT_FORM); if ((result as any).whatsapp?.text) wa.setWaShare({ ...(result as any).whatsapp, title: "Request submitted" }); }
  };
  const requests = data.requests || [];
  const OD_MODES = [
    { key: "full", label: "Full Day" },
    { key: "time", label: "Particular Time" },
    { key: "half", label: "Half Day" },
  ];
  return <>
    <div className="card">
      <div className="grid2">
        <Field label="From date" icon="calendar" type="date" value={form.from_date} onChange={setFromDate} />
        <Field label="To date" icon="calendar" type="date" value={form.to_date} onChange={(v: string) => setForm((f: any) => ({ ...f, to_date: v }))} />
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 13 }}>
        {OD_MODES.map((m) => (
          <button key={m.key} type="button" onClick={() => setForm((f: any) => ({ ...f, od_mode: m.key }))}
            style={{
              flex: 1, height: 40, borderRadius: 12, fontSize: 12.5, fontWeight: 600, cursor: "pointer",
              border: form.od_mode === m.key ? "1px solid var(--iris)" : "1px solid var(--hair2)",
              background: form.od_mode === m.key ? "var(--iris)" : "var(--surface)",
              color: form.od_mode === m.key ? "#fff" : "var(--iris)",
            }}>
            {m.label}
          </button>
        ))}
      </div>
      {form.od_mode === "time" && <div className="grid2" style={{ marginTop: 13 }}>
        <Field label="From time" icon="clock" type="time" value={form.from_time} onChange={(v: string) => setForm({ ...form, from_time: v })} />
        <Field label="To time" icon="clock" type="time" value={form.to_time} onChange={(v: string) => setForm({ ...form, to_time: v })} />
      </div>}
      {form.od_mode === "time" && <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 4 }}>These duty hours count as time worked. Come to the office for the rest of the day and punch IN and OUT: late / early is waived for the duty hours, and the day is Present when office + duty hours complete your shift (otherwise Half Day).</div>}
      {form.od_mode === "half" && <div style={{ marginTop: 13 }}>
        <Field label="Half Day date" icon="calendar" type="date" value={form.half_day_date} onChange={(v: string) => setForm({ ...form, half_day_date: v })} />
        <Select label="Which half are you On Duty?" value={form.half_day_type} onChange={(v: string) => setForm({ ...form, half_day_type: v })} options={["First Half", "Second Half"]} />
        <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 4 }}>Only this one date (within the range above) is a half day. The other half you work from the office: punch IN and OUT as usual and the day counts as Present (late / early rules are waived for the duty half). Without an office punch it stays a Half Day.</div>
      </div>}
      <Field label="Explanation" value={form.explanation} onChange={(v: string) => setForm({ ...form, explanation: v })} placeholder="Where / why (e.g. client site visit)" />
      <div style={{ marginTop: 16 }}><button className="btn" type="button" disabled={isAnyBusy} onClick={submit}><Ic name="check" /> {isBusy("submit-on-duty") ? "Submitting…" : "Submit request"}</button></div>
    </div>
    <div className="sec-lab">My requests</div>
    {requests.length ? requests.map((r: any) => {
      const kind = statusChipKind(r.jew_od_status);
      return <div className="qrow" key={r.name}>
        <div className="qa"><Ic name={kind === "ok" ? "check" : kind === "err" ? "close" : "briefcase"} /></div>
        <div className="qt"><strong>On Duty</strong><span>{fmtDMY(r.from_date)}{r.from_date !== r.to_date ? ` \u2013 ${fmtDMY(r.to_date)}` : ""}{r.half_day ? ` \u00b7 Half day${r.custom_half_day_type ? ` (${r.custom_half_day_type} On Duty)` : ""}` : ""}</span></div>
        {!/cancel|reject/i.test(String(r.jew_od_status)) && <WaButton busy={wa.waBusy === r.name} onClick={() => wa.openWa(r.name)} />}
        <Chip kind={kind}>{r.jew_od_status}</Chip>
      </div>;
    }) : <div className="empty"><h3>No requests</h3><p>No On Duty requests yet.</p></div>}
    {wa.waShare && <WhatsAppSheet wa={wa.waShare} flash={flash} heading={wa.waShare.title} onClose={() => wa.setWaShare(null)} />}
  </>;
}

function OnDutyApproval({ requests, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decide = async (r: any, action: string) => {
    let remarks = "";
    if (action === "Rejected") {
      remarks = window.prompt("Reject reason") || "";
      if (!remarks.trim()) { flash("Reject Reason required", "Reject Reason required."); return; }
    }
    await runAction(`${action}-${r.name}`, async () => { await call(API.decideOnDuty, { name: r.name, action, remarks }); await reload(); }, { successTitle: "Updated", successMessage: "On Duty request updated.", errorTitle: "Update failed" });
  };
  return <>
    {requests.length ? requests.map((r: any) => (
      <div className="card" style={{ marginBottom: 11 }} key={r.name}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <div className="qa" style={{ width: 38, height: 38, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center" }}><Ic name="briefcase" style={{ width: 17, height: 17 }} /></div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <strong style={{ fontSize: 13 }}>{r.employee_name || r.employee}</strong>
            <div style={{ fontSize: 11, color: "var(--faint)" }}>{fmtDMY(r.from_date)}{r.from_date !== r.to_date ? ` \u2013 ${fmtDMY(r.to_date)}` : ""} \u00b7 {r.explanation || "-"}</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 9, marginTop: 12, flexWrap: "wrap" }}>
          <button className="btn ok sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, "Approved")}>{isBusy(`Approved-${r.name}`) ? "…" : "Approve"}</button>
          <button className="btn danger sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, "Rejected")}>Reject</button>
        </div>
      </div>
    )) : <div className="empty"><h3>All clear</h3><p>No pending On Duty requests.</p></div>}
  </>;
}

const MISSING_PUNCH_STATUS_LABEL: Record<string, string> = {
  "Missing Mark In": "Check-In Approved",
  "Missing Mark Out": "Check-Out Approved",
  "Missing Mark In & Out": "Check-In & Out Approved",
};
function regularizationStatusLabel(issueType: string, status: string): string {
  if (status === "Approved as Present" && MISSING_PUNCH_STATUS_LABEL[issueType]) return MISSING_PUNCH_STATUS_LABEL[issueType];
  return status;
}

function RegularizationRequest({ data, flash, reload }: any) {
  const [form, setForm] = useState({ attendance_date: "", issue_type: "Missing Mark Out", reason: "" });
  const [expanded, setExpanded] = useState<string | null>(null);
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const submit = async () => {
    if (!form.attendance_date || !form.reason) { flash("Required fields", "Date and reason are required."); return; }
    const result = await runAction("submit-regularization", async () => {
      const response = await call(API.requestAttendanceRegularization, form);
      await reload();
      return response;
    }, { successTitle: "Request submitted", successMessage: "HR has been notified.", errorTitle: "Request failed" });
    if (result) setForm({ attendance_date: "", issue_type: "Missing Mark Out", reason: "" });
  };
  const requests = data.regularizations || [];
  return <>
    <div className="card">
      <Select label="Regularization type" value={form.issue_type} onChange={(v: string) => setForm({ ...form, issue_type: v })} options={["Missing Mark In", "Missing Mark Out", "Missing Mark In & Out", "Missing Mark In (Half Day)", "Missing Mark Out (Half Day)", "Late Coming Check-in", "Early Going Check-out"]} />
      <div style={{ marginTop: 13 }}><Field label="Date" icon="calendar" type="date" value={form.attendance_date} onChange={(v: string) => setForm({ ...form, attendance_date: v })} /></div>
      <Field label="Reason" value={form.reason} onChange={(v: string) => setForm({ ...form, reason: v })} placeholder="Explain the reason for HR" />
      <div style={{ marginTop: 16 }}><button className="btn" type="button" disabled={isAnyBusy} onClick={submit}><Ic name="check" /> {isBusy("submit-regularization") ? "Submitting…" : "Submit request"}</button></div>
    </div>
    <div className="sec-lab">My requests</div>
    {requests.length ? requests.map((r: any) => {
      const kind = statusChipKind(r.status);
      const isOpen = expanded === r.name;
      return <div key={r.name}>
        <div className="qrow" role="button" tabIndex={0} style={{ cursor: "pointer" }} onClick={() => setExpanded(isOpen ? null : r.name)}>
          <div className="qa"><Ic name={kind === "ok" ? "check" : kind === "err" ? "close" : "clock"} /></div>
          <div className="qt"><strong>{r.issue_type}</strong><span>{fmtDMY(r.attendance_date)}</span></div>
          <Chip kind={kind}>{regularizationStatusLabel(r.issue_type, r.status)}</Chip>
        </div>
        {isOpen && <div className="card" style={{ marginTop: -4, marginBottom: 11, fontSize: 12.5 }}>
          <div><strong>Date:</strong> {fmtDMY(r.attendance_date)}</div>
          <div style={{ marginTop: 6 }}><strong>Issue:</strong> {r.issue_type}</div>
          {r.in_time && <div style={{ marginTop: 6 }}><strong>In time:</strong> {r.in_time}</div>}
          {r.out_time && <div style={{ marginTop: 6 }}><strong>Out time:</strong> {r.out_time}</div>}
          <div style={{ marginTop: 6 }}><strong>{r.status === "Pending" ? "Your reason:" : "HR remarks:"}</strong> {r.remarks || "—"}</div>
        </div>}
      </div>;
    }) : <div className="empty"><h3>No requests</h3><p>No attendance regularization requests yet.</p></div>}
  </>;
}

function Admin({ open, caps, data }: any) {
  const onLeave = data?.onLeave || [];
  const onLeaveCount = data?.onLeaveCount ?? onLeave.length;
  return <>
    <div className="sec-lab">On leave today <span className="num" style={{ color: "var(--faint)" }}>{onLeaveCount}</span></div>
    {onLeave.length ? <div className="list card" style={{ padding: "4px 15px" }}>
      {onLeave.map((e: any, i: number) => (
        <div className="row" key={e.employee || i}>
          <div className="qa" style={{ width: 36, height: 36, borderRadius: 11, background: "var(--pend-bg)", color: "var(--pend)", display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 12, flex: "0 0 auto" }}>{empInitials(e.employee_name)}</div>
          <div className="mid"><strong>{e.employee_name}</strong><span>{[e.department, e.leave_type].filter(Boolean).join(" · ")}{e.half_day ? " · ½ day" : ""}</span></div>
          <Chip kind="pend">{fmtDMY(e.from_date)}{e.to_date && e.to_date !== e.from_date ? ` – ${fmtDMY(e.to_date)}` : ""}</Chip>
        </div>
      ))}
    </div> : <div className="empty"><h3>Nobody on leave</h3><p>No approved leave for today.</p></div>}
    <div className="sec-lab">Admin modules</div>
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {caps.can_approve_leave && <Mod icon="check" title="Leave approval" sub="Approve or reject requests" onClick={() => open("leaveApproval")} />}
      {caps.can_approve_late_early && <Mod icon="check" cyan title="Late/Early approval" sub="Approve or reject requests" onClick={() => open("lateEarlyApproval")} />}
      {caps.can_approve_late_early_hr && <Mod icon="check" cyan title="Late/Early — HR approval" sub="Final sign-off after Dept Head" onClick={() => open("lateEarlyApprovalHr")} />}
      {caps.can_decide_cancellations && <Mod icon="close" title="Cancellations" sub="Approve/reject cancellation requests" onClick={() => open("cancellationApproval")} />}
      {caps.can_view_admin && <Mod icon="users" cyan title="Employees" sub="Face / location status" onClick={() => open("employees")} />}
      {caps.can_view_approvers && <Mod icon="check" title="Approvers" sub="MD, HR & Department Heads" onClick={() => open("approvers")} />}
      {caps.can_register_face && <Mod icon="camera" title="Face register" sub="Enroll employee templates" onClick={() => open("face")} />}
      {caps.can_approve_face && <Mod icon="check" cyan title="Face approvals" sub="Approve employees' face photos" onClick={() => open("faceApproval")} />}
      {caps.can_manage_locations && <Mod icon="pin" cyan title="Locations / geofence" sub="Assign work sites" onClick={() => open("location")} />}
      {caps.can_manage_leave_policy && <Mod icon="leaf" title="Leave policy" sub="Configure leave types" onClick={() => open("leavePolicy")} />}
      {caps.can_manage_shift_policy && <Mod icon="clock" cyan title="Shift policy" sub="Late / early / short rules" onClick={() => open("shiftPolicy")} />}
      {caps.can_manage_regularization && <Mod icon="refresh" title="Regularization" sub="Resolve pending cases" onClick={() => open("regularization")} />}
    </div>
  </>;
}

function FaceAdmin({ employees, flash, selectedEmployee, onCameraActiveChange }: any) {
  const [employee, setEmployee] = useState(selectedEmployee || "");
  const [image, setImage] = useState("");
  const [live, setLive] = useState<{ token: string; frames: string[] } | null>(null);
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  useEffect(() => { if (selectedEmployee) setEmployee(selectedEmployee); }, [selectedEmployee]);
  const selected = (employees || []).find((item: any) => item.name === employee);
  const save = async () => {
    if (!employee || !image) { flash("Required fields", "Select employee and capture face before saving."); return; }
    await runAction("save-face", async () => { await call(API.registerEmployeeFace, { employee, face_image: image, ...(live ? { liveness_challenge: live.token, liveness_frames: live.frames } : {}) }); setImage(""); setLive(null); }, { successTitle: "Face registered", successMessage: "Server template saved for employee.", errorTitle: "Face failed" });
  };
  return <>
    <Select label="Select employee" icon="user" value={employee} onChange={(value: string) => { setEmployee(value); setImage(""); setLive(null); window.dispatchEvent(new Event("jew-hrms-stop-camera")); }} options={(employees || []).map((e: any) => ({ value: e.name, label: e.label || e.employee_name || e.name, description: e.description || e.name }))} />
    {selected && <div className="qrow" style={{ marginTop: 12 }}><div className="qa"><Ic name="user" /></div><div className="qt"><strong>{selected.employee_name || selected.name}</strong><span>{selected.name} · {selected.face_registered ? "Registered" : "Not registered"}</span></div></div>}
    <div className="mt"><LiveCamera image={image} onCapture={(value: string) => { setImage(value); if (!value) setLive(null); }} flash={flash} disabled={!employee} disabledMessage="Please select employee first." onActiveChange={onCameraActiveChange} liveEmployee={employee} onLive={setLive} /></div>
    <button className="btn mt" type="button" disabled={isAnyBusy || !employee || !image} onClick={save}><Ic name="check" /> {isBusy("save-face") ? "Saving…" : "Save face template"}</button>
    {!(employees || []).length && <div className="empty"><h3>No employees loaded</h3><p>Open this screen from Admin after the employee list loads.</p></div>}
  </>;
}

// The server puts window.JEW_LIVENESS_ACTIVE = true into the page ONLY for a site whose site_config has
// jew_hrms_liveness_mode = "active". Every other site (e.g. the live jewipl HRMS) never sees any of the
// live-scan code paths and behaves exactly like the app did before the live scan existed.
const isLiveScanSite = (): boolean => typeof window !== "undefined" && (window as any).JEW_LIVENESS_ACTIVE === true;

const REG_LIVE_PROMPTS: Record<string, string> = {
  turn_left: "Turn your head to your LEFT",
  turn_right: "Turn your head to your RIGHT",
  blink: "Blink your eyes now",
};
const regSleepMs = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));
const regGrabFrame = (video: HTMLVideoElement, maxSide: number, quality: number): string | null => {
  if (!video.videoWidth || !video.videoHeight) return null;
  const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", quality);
};

const liveFilmScan = async (getVideo: () => HTMLVideoElement | null, steps: string[], setText: (text: string) => void, matchSide: number): Promise<string[]> => {
  // Longer windows so a normal person can react (2.8s per action), a visible countdown, and DENSE sampling
  // (~80ms) only while a blink is asked - a blink is over in ~150ms and falls between sparse frames. Turns,
  // the straight-ahead start/end need far fewer frames (250ms), which keeps the upload and the server work small.
  const frames: string[] = [];
  const phase = async (label: string, ms: number, gapMs: number, side: number, quality: number) => {
    const end = Date.now() + ms;
    let shown = "";
    while (Date.now() < end) {
      const video = getVideo();
      if (!video) throw new Error("Camera stopped. Please try again.");
      const started = Date.now();
      const frame = regGrabFrame(video, side, quality);
      if (frame && frames.length < 85) frames.push(frame);
      const text = label + " (" + Math.max(1, Math.ceil((end - Date.now()) / 1000)) + ")";
      if (text !== shown) { setText(text); shown = text; }
      await regSleepMs(Math.max(5, gapMs - (Date.now() - started)));
    }
  };
  await phase("Look straight at the camera", 1000, 250, matchSide, 0.8);
  for (const step of steps) {
    const blink = step === "blink";
    await phase(REG_LIVE_PROMPTS[step] || "Follow the instruction", 2400, blink ? 80 : 250, 320, blink ? 0.6 : 0.65);
  }
  await phase("Look straight again", 1000, 250, 320, 0.65);
  return frames;
};

function LiveCamera({ image, onCapture, flash, disabled = false, disabledMessage = "Please select employee first.", onActiveChange, liveEmployee, onLive }: any) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [statusText, setStatusText] = useState("Camera ready");
  const setCameraActive = (value: boolean) => { setActive(value); onActiveChange?.(value); };
  const stop = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setStarting(false); setCameraActive(false);
  };
  useEffect(() => {
    const stopFromApp = () => stop();
    window.addEventListener("jew-hrms-stop-camera", stopFromApp);
    return () => { window.removeEventListener("jew-hrms-stop-camera", stopFromApp); stop(); };
  }, []);
  const start = async () => {
    if (disabled) { flash("Employee required", disabledMessage); return; }
    if (!navigator.mediaDevices?.getUserMedia) { flash("Camera failed", "Camera is not available on this device."); return; }
    setStarting(true); setStatusText("Starting camera…");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 720 }, height: { ideal: 720 } }, audio: false });
      streamRef.current = stream; setCameraActive(true);
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const video = videoRef.current;
      if (!video) throw new Error("camera_video_not_ready");
      video.srcObject = stream; video.muted = true; video.playsInline = true;
      await new Promise<void>((resolve) => { if (video.readyState >= 2) return resolve(); video.onloadedmetadata = () => resolve(); });
      await video.play();
      setStatusText("Keep your face inside the frame");
    } catch (error: any) {
      const name = error?.name || "";
      stop();
      flash("Camera failed", name === "NotAllowedError" || name === "PermissionDeniedError" ? "Camera permission denied. Please allow camera access." : "Camera is not available on this device.");
    } finally { setStarting(false); }
  };
  const capture = async () => {
    const video = videoRef.current; const canvas = canvasRef.current;
    if (!video || !canvas || !streamRef.current || video.videoWidth === 0) { flash("Camera", disabled ? disabledMessage : "Start camera before capturing face."); return; }
    // ACTIVE live scan when the server requires it for face registration on this site. Any failure to ask
    // (or "enabled: false") keeps the normal single-photo capture below, so other sites are unaffected.
    if (liveEmployee && isLiveScanSite()) {
      let challenge: any = null;
      try { challenge = await call(API.livenessStart, { employee: liveEmployee }); } catch { challenge = null; }
      if (challenge?.enabled) {
        if (challenge.locked) { flash("Live scan", challenge.message || "Too many failed attempts. Please try again later."); return; }
        setScanning(true);
        try {
          const frames = await liveFilmScan(() => (streamRef.current ? videoRef.current : null), challenge.steps || [], setStatusText, 640);
          onLive?.({ token: challenge.token, frames });
          onCapture(frames[0]);
          setStatusText("Live scan captured. Save or retake."); stop();
        } catch (error: any) {
          flash("Live scan", error?.message || "Live scan failed. Please try again.");
          setStatusText("Keep your face inside the frame");
        } finally { setScanning(false); }
        return;
      }
    }
    onLive?.(null);
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
    onCapture(canvas.toDataURL("image/jpeg", 0.9));
    setStatusText("Face captured. Save or retake."); stop();
  };
  return (
    <div>
      <div className="facebox" style={{ height: 230 }}>
        <span className="brk b1" /><span className="brk b2" /><span className="brk b3" /><span className="brk b4" />
        {active ? <video ref={videoRef} autoPlay muted playsInline /> : image ? <img src={image} alt="Captured face" /> : <Ic name="camera" />}
        <canvas ref={canvasRef} hidden />
        <div className="scan"><span className={`chip ${active ? "ok" : image ? "pend" : "info"}`}>{active ? statusText : image ? "Face captured" : disabled ? "Select employee first" : "Camera ready"}</span></div>
      </div>
      <div style={{ marginTop: 14, display: "flex", gap: 9 }}>
        <button className="btn ghost" type="button" disabled={starting || disabled} onClick={active ? stop : start}><Ic name="camera" /> {active ? "Stop" : starting ? "…" : "Start"}</button>
        <button className="btn" type="button" disabled={!active || starting || scanning} onClick={capture}><Ic name="check" /> {scanning ? "Scanning…" : "Capture"}</button>
      </div>
      {image && <button className="btn ghost mt" type="button" onClick={() => { onCapture(""); if (!disabled) start(); }}>Retake</button>}
    </div>
  );
}

function LocationAdmin({ data, flash, reload, caps }: any) {
  const [form, setForm] = useState({ location_name: "", latitude: 21.1458, longitude: 79.0882, default_radius_meter: 100 });
  const [mapCenter, setMapCenter] = useState<[number, number]>([21.1458, 79.0882]);
  const [employee, setEmployee] = useState("");
  const [location, setLocation] = useState("");
  const [employeeLocations, setEmployeeLocations] = useState<any[]>([]);
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const employeeOptions = (data.employees || []).map((e: any) => ({ value: e.name, label: e.label || e.employee_name || e.name, description: e.description || e.name }));
  const locationOptions = (data.locations || []).map((l: any) => ({ value: l.name, label: l.location_name || l.name, description: l.name }));
  const loadEmployeeLocations = async (employeeName: string) => {
    setEmployee(employeeName); setEmployeeLocations([]);
    if (!employeeName) return;
    const res = await runAction(`load-locations-${employeeName}`, () => call(API.getEmployeeLocations, { employee: employeeName }), { errorTitle: "Assignments failed" });
    if (res) setEmployeeLocations(res.locations || []);
  };
  const useCurrentLocation = async () => {
    await runAction("use-current-location", async () => {
      if (!navigator.geolocation) throw new Error("Location is not available on this device.");
      const position = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, () => reject(new Error("Could not get your location. Please turn on GPS/location and try again.")), { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
      });
      const latitude = Number(position.coords.latitude.toFixed(7));
      const longitude = Number(position.coords.longitude.toFixed(7));
      setForm((current) => ({ ...current, latitude, longitude })); setMapCenter([latitude, longitude]);
      return position;
    }, { successTitle: "Location", successMessage: "Current location captured.", errorTitle: "Location failed" });
  };
  const save = async () => {
    if (!form.location_name || form.latitude === null || form.longitude === null || !form.default_radius_meter) { flash("Required fields", "Location name, latitude, longitude and radius are required."); return; }
    await runAction("save-location", async () => { await call(API.saveLocation, form); reload(); }, { successTitle: "Location saved", successMessage: "Geofence updated.", errorTitle: "Location failed" });
  };
  const assign = async () => {
    if (!employee || !location) { flash("Required fields", "Select employee and location before assigning."); return; }
    await runAction("assign-location", async () => {
      const selectedLocation = (data.locations || []).find((item: any) => item.name === location);
      const res = await call(API.assignEmployeeLocation, { employee, location, radius_meter: selectedLocation?.default_radius_meter });
      setEmployeeLocations((current) => [{ assignment: res.assignment, name: selectedLocation?.name || location, location_name: selectedLocation?.location_name || location, radius_meter: Number(selectedLocation?.default_radius_meter || res.radius_meter || 100) }, ...current.filter((item) => item.name !== location && item.location !== location)]);
    }, { successTitle: "Assigned", successMessage: "Location assigned successfully.", errorTitle: "Assign failed" });
  };
  const deleteLocation = async (name: string) => {
    await runAction(`delete-location-${name}`, async () => { await call(API.deleteLocation, { name }); if (location === name) setLocation(""); await reload(); }, { successTitle: "Location deleted", successMessage: "Geofence removed.", errorTitle: "Delete failed" });
  };
  const removeAssignment = async (assignment: string) => {
    await runAction(`remove-assignment-${assignment}`, async () => { await call(API.removeEmployeeLocation, { assignment }); if (employee) { const res = await call(API.getEmployeeLocations, { employee }); setEmployeeLocations(res.locations || []); } await reload(); }, { successTitle: "Assignment removed", successMessage: "Employee location assignment removed.", errorTitle: "Remove failed" });
  };
  return <>
    {caps?.can_manage_masters && <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div className="map-box"><MapContainer center={mapCenter} zoom={13} scrollWheelZoom={false}><MapRecenter center={mapCenter} /><TileLayer attribution="OpenStreetMap" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" /><PickLocation onPick={(lat, lng) => { setForm({ ...form, latitude: lat, longitude: lng }); setMapCenter([lat, lng]); }} /><Marker position={[form.latitude, form.longitude]} /><Circle center={[form.latitude, form.longitude]} radius={form.default_radius_meter} /></MapContainer></div>
      <div style={{ padding: 15 }}>
        <Field label="Location name" value={form.location_name} onChange={(v: string) => setForm({ ...form, location_name: v })} />
        <div className="grid2" style={{ marginTop: 13 }}>
          <Field label="Latitude" type="number" value={String(form.latitude)} onChange={(v: string) => { const latitude = Number(v); setForm({ ...form, latitude }); setMapCenter([latitude, form.longitude]); }} />
          <Field label="Longitude" type="number" value={String(form.longitude)} onChange={(v: string) => { const longitude = Number(v); setForm({ ...form, longitude }); setMapCenter([form.latitude, longitude]); }} />
        </div>
        <Field label="Radius (m)" type="number" value={String(form.default_radius_meter)} onChange={(v: string) => setForm({ ...form, default_radius_meter: Number(v) })} />
        <button className="btn ghost mt" type="button" disabled={isAnyBusy} onClick={useCurrentLocation}><Ic name="pin" /> {isBusy("use-current-location") ? "Capturing…" : "Use current location"}</button>
        <button className="btn mt" type="button" disabled={isAnyBusy || !form.location_name} onClick={save}><Ic name="check" /> {isBusy("save-location") ? "Saving…" : "Save location"}</button>
      </div>
    </div>}
    <div className="card mt">
      <Select label="Employee" value={employee} onChange={loadEmployeeLocations} options={employeeOptions} />
      <Select label="Location" value={location} onChange={setLocation} options={locationOptions} />
      <button className="btn ghost mt" type="button" disabled={isAnyBusy || !employee || !location} onClick={assign}><Ic name="plus" /> {isBusy("assign-location") ? "Assigning…" : "Assign location"}</button>
    </div>
    <div className="sec-lab">Saved locations</div>
    <div className="list card" style={{ padding: "4px 15px" }}>
      {(data.locations || []).length ? data.locations.map((item: any) => (
        <div className="row" key={item.name}><div className="prow" style={{ border: 0, padding: 0, flex: 1 }}><div className="pi"><Ic name="building" /></div><div className="pt"><strong>{item.location_name || item.name}</strong><span>{item.latitude}, {item.longitude} · {item.default_radius_meter} m</span></div></div>{caps?.can_manage_masters && <button className="btn danger sm" style={{ width: "auto", padding: "0 12px" }} type="button" disabled={isAnyBusy} onClick={() => deleteLocation(item.name)}>{isBusy(`delete-location-${item.name}`) ? "…" : "Delete"}</button>}</div>
      )) : <div className="empty"><h3>No locations</h3><p>No saved locations yet.</p></div>}
    </div>
    <div className="sec-lab">Employee assignments</div>
    <div className="list card" style={{ padding: "4px 15px" }}>
      {employee ? (employeeLocations.length ? employeeLocations.map((item: any) => (
        <div className="row" key={item.assignment || item.name}><div className="prow" style={{ border: 0, padding: 0, flex: 1 }}><div className="pi"><Ic name="pin" /></div><div className="pt"><strong>{item.location_name || item.name}</strong><span>{item.radius_meter} m radius</span></div></div><button className="btn danger sm" style={{ width: "auto", padding: "0 12px" }} type="button" disabled={isAnyBusy} onClick={() => removeAssignment(item.assignment)}>{isBusy(`remove-assignment-${item.assignment}`) ? "…" : "Remove"}</button></div>
      )) : <div className="empty"><h3>No assignments</h3><p>No active assignments for selected employee.</p></div>) : <div className="empty"><h3>Select employee</h3><p>Select an employee to view assignments.</p></div>}
    </div>
  </>;
}

function LeavePolicy({ types, flash, reload }: any) {
  const [form, setForm] = useState({ name: "", leave_type_name: "", is_lwp: false });
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const save = async () => {
    if (!form.name && !form.leave_type_name) { flash("Required fields", "Leave type is required."); return; }
    await runAction("save-leave-type", async () => { await call(API.saveLeaveType, form); await reload(); setForm({ name: "", leave_type_name: "", is_lwp: false }); }, { successTitle: "Leave type saved", errorTitle: "Save failed" });
  };
  return <>
    <div className="card">
      <div className="grid2">
        <Field label="Type code" value={form.name} onChange={(v: string) => setForm({ ...form, name: v.toUpperCase() })} />
        <Field label="Type name" value={form.leave_type_name} onChange={(v: string) => setForm({ ...form, leave_type_name: v })} />
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 13, color: "var(--muted)", fontSize: 12.5, fontWeight: 600 }}><input type="checkbox" style={{ accentColor: "var(--iris)" }} checked={form.is_lwp} onChange={(e) => setForm({ ...form, is_lwp: e.target.checked })} /> LWP (unpaid)</label>
      <button className="btn mt" type="button" disabled={isAnyBusy} onClick={save}><Ic name="check" /> {isBusy("save-leave-type") ? "Saving…" : "Save leave type"}</button>
    </div>
    <div className="sec-lab">Configured leave types</div>
    <div className="list card" style={{ padding: "4px 15px" }}>
      {types.length ? types.map((t: any) => <div className="row" key={t.name}><div className="mid"><strong>{t.name}</strong><span>{t.leave_type_name || t.name}</span></div><Chip kind={t.is_lwp ? "pend" : "ok"}>{t.is_lwp ? "LWP" : "Paid"}</Chip></div>) : <div className="empty"><h3>None</h3><p>No leave types configured.</p></div>}
    </div>
  </>;
}

function ShiftPolicy({ policies, flash, reload }: any) {
  const [form, setForm] = useState<any>({ shift_name: "General", shift_start_time: "09:30:00", shift_end_time: "18:30:00", break_minutes: 60, full_day_minimum_hours: 8, half_day_minimum_hours: 4, late_coming_grace_minutes: 10, early_going_grace_minutes: 10, max_late_coming_allowed_per_month: 0, max_early_going_allowed_per_month: 0, max_short_hours_allowed_per_month: 0, action_after_late_limit: "Regularization Required", action_after_early_limit: "Regularization Required", action_after_short_hours_limit: "Regularization Required", is_active: 1 });
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const actionOptions = ["Warn Only", "Regularization Required", "Mark Half Day", "Mark LWP", "Block Attendance"];
  const save = async () => { await runAction("save-shift-policy", async () => { await call(API.saveShiftPolicy, form); await reload(); }, { successTitle: "Policy saved", errorTitle: "Policy failed" }); };
  return <>
    <div className="card">
      <div className="grid2">
        <Field label="Shift name" value={form.shift_name} onChange={(v: string) => setForm({ ...form, shift_name: v })} />
        <Field label="Start" type="time" value={String(form.shift_start_time || "").slice(0, 5)} onChange={(v: string) => setForm({ ...form, shift_start_time: v })} />
        <Field label="End" type="time" value={String(form.shift_end_time || "").slice(0, 5)} onChange={(v: string) => setForm({ ...form, shift_end_time: v })} />
        <Field label="Break (min)" type="number" value={String(form.break_minutes)} onChange={(v: string) => setForm({ ...form, break_minutes: Number(v) })} />
        <Field label="Full day hrs" type="number" value={String(form.full_day_minimum_hours)} onChange={(v: string) => setForm({ ...form, full_day_minimum_hours: Number(v) })} />
        <Field label="Half day hrs" type="number" value={String(form.half_day_minimum_hours)} onChange={(v: string) => setForm({ ...form, half_day_minimum_hours: Number(v) })} />
        <Field label="Late grace" type="number" value={String(form.late_coming_grace_minutes)} onChange={(v: string) => setForm({ ...form, late_coming_grace_minutes: Number(v) })} />
        <Field label="Early grace" type="number" value={String(form.early_going_grace_minutes)} onChange={(v: string) => setForm({ ...form, early_going_grace_minutes: Number(v) })} />
      </div>
      <Select label="Late action" value={form.action_after_late_limit} onChange={(v: string) => setForm({ ...form, action_after_late_limit: v })} options={actionOptions} />
      <Select label="Early action" value={form.action_after_early_limit} onChange={(v: string) => setForm({ ...form, action_after_early_limit: v })} options={actionOptions} />
      <Select label="Short hours action" value={form.action_after_short_hours_limit} onChange={(v: string) => setForm({ ...form, action_after_short_hours_limit: v })} options={actionOptions} />
      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 13, color: "var(--muted)", fontSize: 12.5, fontWeight: 600 }}><input type="checkbox" style={{ accentColor: "var(--iris)" }} checked={Boolean(Number(form.is_active))} onChange={(e) => setForm({ ...form, is_active: e.target.checked ? 1 : 0 })} /> Active</label>
      <button className="btn mt" type="button" disabled={isAnyBusy} onClick={save}><Ic name="check" /> {isBusy("save-shift-policy") ? "Saving…" : "Save policy"}</button>
    </div>
    <div className="sec-lab">Policies</div>
    <div className="list card" style={{ padding: "4px 15px" }}>
      {policies.length ? policies.map((p: any) => <button className="row" style={{ width: "100%", textAlign: "left" }} key={p.name} type="button" onClick={() => setForm({ ...form, ...p })}><div className="mid"><strong>{p.shift_name}</strong><span>{String(p.shift_start_time).slice(0, 5)} → {String(p.shift_end_time).slice(0, 5)}</span></div><Chip kind={p.is_active ? "ok" : "pend"}>{p.is_active ? "Active" : "Inactive"}</Chip></button>) : <div className="empty"><h3>None</h3><p>No shift policy configured.</p></div>}
    </div>
  </>;
}

// Missing Mark In/Out (self-service) issue types: HR just accepts or rejects the
// employee's claim - the actual Check-In/Check-Out punch is auto-filled server
// side from the employee's own shift start/end time, no manual time entry needed.
// Each maps to exactly one button label + the resulting regularization action.
const MISSING_PUNCH_APPROVE_ACTION: Record<string, { label: string; action: string }> = {
  "Missing Mark In": { label: "Approve Check-In", action: "Checked In" },
  "Missing Mark Out": { label: "Approve Check-Out", action: "Approved as Check-Out" },
  "Missing Mark In & Out": { label: "Approve Check-In & Out", action: "Approved as Present" },
  "Missing Mark In (Half Day)": { label: "Approve Check-In (Half Day)", action: "Marked Half Day" },
  "Missing Mark Out (Half Day)": { label: "Approve Check-Out (Half Day)", action: "Marked Half Day" },
  "Late Coming Check-in": { label: "Approve Late Coming", action: "Marked Late Coming" },
  "Early Going Check-out": { label: "Approve Early Going", action: "Marked Early Going" },
};

function Regularization({ items, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decide = async (r: any, action: string) => {
    const name = r.name;
    // Approve is a single click, no remarks needed - only Reject asks for a reason.
    let remarks = "";
    if (action === "Rejected") {
      remarks = window.prompt("Reject reason") || "";
      if (!remarks.trim()) { flash("Reject Reason required", "Reject Reason required."); return; }
    }
    await runAction(`${action}-${name}`, async () => { await call(API.decideRegularization, { name, action, remarks }); await reload(); }, { successTitle: "Updated", successMessage: "Regularization updated.", errorTitle: "Update failed" });
  };
  return <>
    {items.length ? items.map((r: any) => {
      const missingPunch = MISSING_PUNCH_APPROVE_ACTION[r.issue_type];
      return <div className="card" style={{ marginBottom: 11 }} key={r.name}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}><div className="qa" style={{ width: 38, height: 38, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center" }}><Ic name="refresh" style={{ width: 17, height: 17 }} /></div><div style={{ flex: 1, minWidth: 0 }}><strong style={{ fontSize: 13 }}>{r.employee_name || r.employee}</strong><div style={{ fontSize: 11, color: "var(--faint)" }}>{fmtDMY(r.attendance_date)} · {r.issue_type} · {r.policy_action || "Review"}</div></div></div>
        <div style={{ display: "flex", gap: 9, marginTop: 12, flexWrap: "wrap" }}>
          {missingPunch ? <>
            <button className="btn ok sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, missingPunch.action)}>{isBusy(`${missingPunch.action}-${r.name}`) ? "…" : missingPunch.label}</button>
            <button className="btn danger sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, "Rejected")}>Reject</button>
          </> : <>
            <button className="btn ok sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, "Approved as Present")}>{isBusy(`Approved as Present-${r.name}`) ? "…" : "Present"}</button>
            <button className="btn ghost sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, "Marked Half Day")}>Half day</button>
            <button className="btn ghost sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, "Marked Late Coming")}>Late coming</button>
            <button className="btn ghost sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, "Marked Early Going")}>Early going</button>
            <button className="btn danger sm" style={{ flex: 1, minWidth: 88 }} type="button" disabled={isAnyBusy} onClick={() => decide(r, "Rejected")}>Reject</button>
          </>}
        </div>
      </div>;
    }) : <div className="empty"><h3>All clear</h3><p>No pending regularization.</p></div>}
  </>;
}

function LeaveApproval({ leaves, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decide = async (name: string, approve: boolean) => {
    const remarks = approve ? "" : window.prompt("Rejection remarks") || "";
    if (!approve && !remarks.trim()) { flash("Reject Reason required", "Reject Reason required."); return; }
    await runAction(`${approve ? "approve" : "reject"}-${name}`, async () => { await call(approve ? API.approveLeave : API.rejectLeave, { name, remarks }); reload(); }, { successTitle: approve ? "Approved" : "Rejected", successMessage: "Leave request updated.", errorTitle: approve ? "Approve failed" : "Reject failed" });
  };
  return <>
    {leaves.length ? leaves.map((l: any) => {
      const st = l.approval_status || l.jew_hrms_approval_status || l.status;
      const nm = l.employee_name || l.employee;
      return <div className="card" style={{ marginBottom: 11 }} key={l.name}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <div className="qa" style={{ width: 38, height: 38, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 13 }}>{empInitials(nm)}</div>
          <div style={{ flex: 1, minWidth: 0 }}><strong style={{ fontSize: 13 }}>{nm}</strong><div style={{ fontSize: 11, color: "var(--faint)" }}>{l.leave_type} · <span className="num cy">{fmtDMY(l.from_date)} → {fmtDMY(l.to_date)}</span>{l.half_day ? " · Half day" : ""}</div></div>
          <Chip kind="pend">{st}</Chip>
        </div>
        {(l.recent_leave_count !== undefined && l.recent_leave_count !== null) && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>Leave taken in last 15 days: <b>{l.recent_leave_count} day(s)</b></div>}
        <div style={{ display: "flex", gap: 9, marginTop: 12 }}>
          <button className="btn ok sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(l.name, true)}><Ic name="check" /> {isBusy(`approve-${l.name}`) ? "…" : st === "Pending Admin Approval" ? "Final approve" : "Approve"}</button>
          <button className="btn danger sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(l.name, false)}><Ic name="close" /> {isBusy(`reject-${l.name}`) ? "…" : "Reject"}</button>
        </div>
      </div>;
    }) : <div className="empty"><h3>All clear</h3><p>No pending leave requests.</p></div>}
  </>;
}

function LateEarlyApproval({ items, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decide = async (name: string, approve: boolean) => {
    const remarks = approve ? "" : window.prompt("Rejection remarks") || "";
    if (!approve && !remarks.trim()) { flash("Reject Reason required", "Reject Reason required."); return; }
    await runAction(`${approve ? "approve" : "reject"}-${name}`, async () => { await call(API.decideLateEarly, { name, decision: approve ? "Approved" : "Rejected", remarks }); reload(); }, { successTitle: approve ? "Approved" : "Rejected", successMessage: "Application updated.", errorTitle: approve ? "Approve failed" : "Reject failed" });
  };
  return <>
    {items.length ? items.map((a: any) => {
      const nm = a.employee_name || a.employee;
      return <div className="card" style={{ marginBottom: 11 }} key={a.name}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <div className="qa" style={{ width: 38, height: 38, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 13 }}>{empInitials(nm)}</div>
          <div style={{ flex: 1, minWidth: 0 }}><strong style={{ fontSize: 13 }}>{nm}</strong><div style={{ fontSize: 11, color: "var(--faint)" }}>{a.application_type} · <span className="num cy">{fmtDMY(a.application_date)}</span> · {a.expected_time}</div>{a.reason && <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 3 }}>{a.reason}</div>}</div>
          <Chip kind="pend">{a.status || "Pending"}</Chip>
        </div>
        <div style={{ display: "flex", gap: 9, marginTop: 12 }}>
          <button className="btn ok sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(a.name, true)}><Ic name="check" /> {isBusy(`approve-${a.name}`) ? "…" : "Approve"}</button>
          <button className="btn danger sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(a.name, false)}><Ic name="close" /> {isBusy(`reject-${a.name}`) ? "…" : "Reject"}</button>
        </div>
      </div>;
    }) : <div className="empty"><h3>All clear</h3><p>No pending Late Coming / Early Going requests.</p></div>}
  </>;
}

function CancellationApproval({ leaves, lateEarly, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decideLeave = async (name: string, approve: boolean) => {
    const remarks = approve ? (window.prompt("Remarks (optional)") || "") : (window.prompt("Rejection remarks") || "");
    if (!approve && !remarks.trim()) { flash("Reject Reason required", "Reject Reason required."); return; }
    await runAction(`leavecancel-${approve ? "approve" : "reject"}-${name}`, async () => { await call(API.decideLeaveCancellation, { name, action: approve ? "approve" : "reject", remarks }); reload(); }, { successTitle: approve ? "Approved" : "Rejected", successMessage: "Cancellation updated.", errorTitle: approve ? "Approve failed" : "Reject failed" });
  };
  const decideLateEarly = async (name: string, approve: boolean) => {
    const remarks = approve ? (window.prompt("Remarks (optional)") || "") : (window.prompt("Rejection remarks") || "");
    if (!approve && !remarks.trim()) { flash("Reject Reason required", "Reject Reason required."); return; }
    await runAction(`lecancel-${approve ? "approve" : "reject"}-${name}`, async () => { await call(API.decideLateEarlyCancellation, { name, action: approve ? "approve" : "reject", remarks }); reload(); }, { successTitle: approve ? "Approved" : "Rejected", successMessage: "Cancellation updated.", errorTitle: approve ? "Approve failed" : "Reject failed" });
  };
  const empty = !leaves.length && !lateEarly.length;
  return <>
    {leaves.length > 0 && <div className="sec-lab">Leave cancellation requests</div>}
    {leaves.map((l: any) => {
      const nm = l.employee_name || l.employee;
      return <div className="card" style={{ marginBottom: 11 }} key={l.name}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <div className="qa" style={{ width: 38, height: 38, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 13 }}>{empInitials(nm)}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <strong style={{ fontSize: 13 }}>{nm}</strong>
            <div style={{ fontSize: 11, color: "var(--faint)" }}>{l.leave_type} · original {fmtDMY(l.from_date)} → {fmtDMY(l.to_date)}</div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>Cancel: <span className="num cy">{fmtDMY(l.jew_cancel_from_date)} → {fmtDMY(l.jew_cancel_to_date)}</span> ({l.jew_cancel_days} day{Number(l.jew_cancel_days) === 1 ? "" : "s"})</div>
            {l.jew_cancel_reason && <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 3 }}>{l.jew_cancel_reason}</div>}
          </div>
          <Chip kind="pend">Leave</Chip>
        </div>
        <div style={{ display: "flex", gap: 9, marginTop: 12 }}>
          <button className="btn ok sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decideLeave(l.name, true)}><Ic name="check" /> {isBusy(`leavecancel-approve-${l.name}`) ? "…" : "Approve"}</button>
          <button className="btn danger sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decideLeave(l.name, false)}><Ic name="close" /> {isBusy(`leavecancel-reject-${l.name}`) ? "…" : "Reject"}</button>
        </div>
      </div>;
    })}
    {lateEarly.length > 0 && <div className="sec-lab">Late / Early cancellation requests</div>}
    {lateEarly.map((a: any) => {
      const nm = a.employee_name || a.employee;
      return <div className="card" style={{ marginBottom: 11 }} key={a.name}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <div className="qa" style={{ width: 38, height: 38, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 13 }}>{empInitials(nm)}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <strong style={{ fontSize: 13 }}>{nm}</strong>
            <div style={{ fontSize: 11, color: "var(--faint)" }}>{a.application_type} · <span className="num cy">{fmtDMY(a.application_date)}</span></div>
            {a.jew_cancel_reason && <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 3 }}>{a.jew_cancel_reason}</div>}
          </div>
          <Chip kind="pend">Late/Early</Chip>
        </div>
        <div style={{ display: "flex", gap: 9, marginTop: 12 }}>
          <button className="btn ok sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decideLateEarly(a.name, true)}><Ic name="check" /> {isBusy(`lecancel-approve-${a.name}`) ? "…" : "Approve"}</button>
          <button className="btn danger sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decideLateEarly(a.name, false)}><Ic name="close" /> {isBusy(`lecancel-reject-${a.name}`) ? "…" : "Reject"}</button>
        </div>
      </div>;
    })}
    {empty && <div className="empty"><h3>All clear</h3><p>No pending cancellation requests.</p></div>}
  </>;
}

function Employees({ employees, open, selectEmployee }: any) {
  const [query, setQuery] = useState("");
  const filtered = (employees || []).filter((employee: any) => `${employee.name} ${employee.employee_name || ""} ${employee.department || ""} ${employee.designation || ""}`.toLowerCase().includes(query.toLowerCase()));
  const openFace = (employee: any) => { selectEmployee(employee.name); open("face"); };
  return <>
    <div className="inp" style={{ marginBottom: 4 }}><Ic name="search" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name or ID" /></div>
    <div className="sec-lab">All employees <span className="num" style={{ color: "var(--faint)" }}>{filtered.length}</span></div>
    <div className="list card" style={{ padding: "4px 15px" }}>
      {filtered.length ? filtered.map((e: any) => (
        <div className="row" key={e.name} onClick={() => openFace(e)} style={{ cursor: "pointer" }}>
          <div className="qa" style={{ width: 36, height: 36, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 12 }}>{empInitials(e.employee_name || e.name)}</div>
          <div className="mid"><strong>{e.employee_name || e.name}</strong><span>{e.department || e.designation || e.name}</span></div>
          <div style={{ display: "flex", gap: 6 }}><Chip kind={e.face_registered ? "ok" : "pend"}>Face</Chip><Chip kind={e.location_count ? "ok" : "pend"}>Loc</Chip></div>
        </div>
      )) : <div className="empty"><h3>No employees</h3><p>No employee data loaded.</p></div>}
    </div>
  </>;
}

function Approvers({ data }: any) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const md = data.md || [];
  const hr = data.hr || [];
  const deptHeads = data.dept_heads || [];

  const personRow = (p: any, key: string) => (
    <div className="row" key={key}>
      <div className="qa" style={{ width: 36, height: 36, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 12 }}>{empInitials(p.name)}</div>
      <div className="mid"><strong>{p.name}</strong><span>{p.email}</span></div>
    </div>
  );

  return <>
    <div className="sec-lab">MD <span className="num" style={{ color: "var(--faint)" }}>{md.length}</span></div>
    <div className="list card" style={{ padding: "4px 15px", marginBottom: 14 }}>
      {md.length ? md.map((p: any) => personRow(p, `md-${p.user}`)) : <div className="empty"><h3>None configured</h3><p>No one holds the MD (JEW HRMS Owner) role.</p></div>}
    </div>

    <div className="sec-lab">HR <span className="num" style={{ color: "var(--faint)" }}>{hr.length}</span></div>
    <div className="list card" style={{ padding: "4px 15px", marginBottom: 14 }}>
      {hr.length ? hr.map((p: any) => personRow(p, `hr-${p.user}`)) : <div className="empty"><h3>None configured</h3><p>No one holds the HR (JEW HRMS HR) role.</p></div>}
    </div>

    <div className="sec-lab">Department Heads <span className="num" style={{ color: "var(--faint)" }}>{deptHeads.length}</span></div>
    {deptHeads.length ? deptHeads.map((d: any) => {
      const isOpen = expanded === d.email;
      return <div key={d.email} style={{ marginBottom: 11 }}>
        <div className="card" style={{ cursor: "pointer" }} onClick={() => setExpanded(isOpen ? null : d.email)}>
          <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
            <div className="qa" style={{ width: 36, height: 36, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 12 }}>{empInitials(d.name)}</div>
            <div style={{ flex: 1, minWidth: 0 }}><strong style={{ fontSize: 13 }}>{d.name}</strong><div style={{ fontSize: 11, color: "var(--faint)" }}>{d.email}</div></div>
            <Chip kind="pend">{d.team_count} {d.team_count === 1 ? "employee" : "employees"}</Chip>
          </div>
          {isOpen && <div className="rows" style={{ marginTop: 12, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
            {d.team.map((t: any) => <div key={t.employee} style={{ fontSize: 12.5, padding: "5px 0" }}>{t.employee_name}</div>)}
          </div>}
        </div>
      </div>;
    }) : <div className="empty"><h3>None configured</h3><p>No employee has a Department Head assigned.</p></div>}
  </>;
}

function Notifications({ items }: any) {
  return <>
    {items.length ? items.map((n: any, i: number) => (
      <div className="qrow" key={i}><div className="qa"><Ic name="bell" style={{ width: 17, height: 17 }} /></div><div className="qt"><strong>{n.title}</strong><span>{n.message}</span></div></div>
    )) : <div className="empty"><h3>No notifications</h3><p>You're all caught up.</p></div>}
  </>;
}

function Profile({ profile, open, onLogout, caps }: any) {
  const [loggingOut, setLoggingOut] = useState(false);
  const doLogout = async () => { setLoggingOut(true); try { await onLogout(); } finally { setLoggingOut(false); } };
  const name = profile?.employee_name || "Employee";
  return <>
    <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
      <div className="avatar">{empInitials(name)}</div>
      <div><strong style={{ fontSize: 17, letterSpacing: "-.02em" }}>{name}</strong>
        <div style={{ fontSize: 11.5, color: "var(--muted)" }}>{profile?.designation || profile?.department || "Employee"}</div>
        <span className="chip data" style={{ marginTop: 7 }}><Ic name="user" /> {profile?.employee || profile?.employee_id || ""}</span>
      </div>
    </div>
    <div className="card" style={{ marginTop: 16, padding: "4px 15px" }}>
      <div className="prow"><div className="pi"><Ic name="building" /></div><div className="pt"><strong>Department</strong><span>{profile?.department || "—"}</span></div></div>
      <div className="prow"><div className="pi"><Ic name="clock" /></div><div className="pt"><strong>Shift</strong></div><span className="pv">{profile?.default_shift || "General"}</span></div>
      <div className="prow"><div className="pi"><Ic name="user" /></div><div className="pt"><strong>Status</strong></div><span className="pv">{profile?.status || "Active"}</span></div>
      <div className="prow"><div className="pi"><Ic name="camera" /></div><div className="pt"><strong>Face</strong></div><span className="pv">{profile?.face_status?.pending ? "Waiting for approval" : profile?.face_status?.registered ? "Ready" : "Pending"}</span></div>
      <div className="prow"><div className="pi"><Ic name="mail" /></div><div className="pt"><strong>User</strong><span>{profile?.user_id || "—"}</span></div></div>
    </div>
    <button className="btn ghost mt" type="button" onClick={() => open("settings")}><Ic name="gear" /> Settings</button>
    <button className="btn danger mt" type="button" disabled={loggingOut} onClick={doLogout}><Ic name="logout" /> {loggingOut ? "Logging out…" : "Log out"}</button>
  </>;
}

function SelfFace({ flash, me, onCameraActiveChange }: any) {
  const [image, setImage] = useState("");
  const [live, setLive] = useState<{ token: string; frames: string[] } | null>(null);
  const [fs, setFs] = useState<any>(null);
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const empId = me?.employee || me?.name || "";
  const loadStatus = async () => { try { setFs(await call(API.getFaceStatus, {})); } catch { /* status stays unknown */ } };
  useEffect(() => { void loadStatus(); }, []);
  const submit = async () => {
    if (!image) { flash("Capture first", "Take your face photo before sending it for approval."); return; }
    const res: any = await runAction("self-face", async () => call(API.registerMyFace, { face_image: image, ...(live ? { liveness_challenge: live.token, liveness_frames: live.frames } : {}) }), { successTitle: "Sent for approval", errorTitle: "Could not send" });
    if (res) { setImage(""); setLive(null); void loadStatus(); }
  };
  const pending = Boolean(fs?.pending);
  return <>
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <strong>My face</strong>
        <Chip kind={fs?.registered ? "ok" : "pend"} icon="camera">{fs?.registered ? "Active face registered" : "No active face yet"}</Chip>
        {pending && <Chip kind="pend" icon="clock">Waiting for approval</Chip>}
      </div>
      <p className="sub" style={{ marginTop: 10, fontSize: 12.5 }}>
        {pending ? "Your last photo is with your approver. You can send a new one - it replaces the pending request." : "Take a clear photo of your own face. Your approver checks it, and you can use it for attendance once approved."}
        {fs?.registered && !pending ? " Your current face keeps working until the new one is approved." : ""}
      </p>
      {fs?.rejected && <div className="warn" style={{ marginTop: 8, fontSize: 12.5 }}>Your last photo was rejected{fs.rejected_remarks ? `: ${fs.rejected_remarks}` : "."} Please register again.</div>}
    </div>
    <LiveCamera image={image} onCapture={(value: string) => { setImage(value); if (!value) setLive(null); }} flash={flash} onActiveChange={onCameraActiveChange} liveEmployee={empId} onLive={setLive} />
    <button className="btn mt" type="button" disabled={isAnyBusy || !image} onClick={submit}><Ic name="check" /> {isBusy("self-face") ? "Sending…" : "Send for approval"}</button>
  </>;
}

function FaceApproval({ requests, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decide = async (r: any, decision: "Approved" | "Rejected") => {
    let remarks = "";
    if (decision === "Rejected") {
      remarks = window.prompt("Reason for rejection") || "";
      if (!remarks.trim()) { flash("Reason required", "Please enter a reason for the rejection."); return; }
    }
    await runAction(`face-${r.name}`, async () => { await call(API.decideFaceApproval, { name: r.name, decision, remarks }); await reload(); },
      { successTitle: decision === "Approved" ? "Face approved" : "Face rejected", successMessage: decision === "Approved" ? "The employee can now use this face for attendance." : "The employee was told to register again.", errorTitle: "Action failed" });
  };
  if (!requests.length) return <div className="empty"><h3>No face requests</h3><p>Nothing is waiting for your approval.</p></div>;
  return <>{requests.map((r: any) => <div className="card" key={r.name} style={{ marginBottom: 12 }}>
    {r.image ? <img src={r.image} alt="Face photo" style={{ width: "100%", maxHeight: 280, objectFit: "cover", borderRadius: 12 }} /> : <div className="sub">Photo not available.</div>}
    <div style={{ marginTop: 10 }}><strong>{r.employee_name}</strong>
      <div style={{ fontSize: 11.5, color: "var(--muted)" }}>{r.employee}{r.department ? ` · ${r.department}` : ""}</div>
      <div style={{ fontSize: 11.5, color: "var(--muted)" }}>{r.had_face ? "Replaces the current face (stays active until you approve)" : "First face for this employee"}</div></div>
    <div style={{ display: "flex", gap: 9, marginTop: 12 }}>
      <button className="btn ok" type="button" disabled={isAnyBusy} onClick={() => decide(r, "Approved")}><Ic name="check" /> {isBusy(`face-${r.name}`) ? "Saving…" : "Approve"}</button>
      <button className="btn ghost" type="button" disabled={isAnyBusy} onClick={() => decide(r, "Rejected")}><Ic name="close" /> Reject</button>
    </div>
  </div>)}</>;
}

function Settings({ theme, setTheme, onLogout }: any) {
  const [loggingOut, setLoggingOut] = useState(false);
  const doLogout = async () => { setLoggingOut(true); try { await onLogout(); } finally { setLoggingOut(false); } };
  const dark = theme === "dark";
  return <>
    <div className="sec-lab">Appearance</div>
    <div className="card" style={{ padding: "4px 15px" }}>
      <div className="prow"><div className="pi"><Ic name={dark ? "moon" : "sun"} /></div><div className="pt"><strong>Dark theme</strong><span>Instrument mode for low light</span></div><button className={`switch ${dark ? "on" : ""}`} onClick={() => setTheme(dark ? "light" : "dark")} /></div>
    </div>
    <div className="sec-lab">Attendance</div>
    <div className="card" style={{ padding: "4px 15px" }}>
      <div className="prow"><div className="pi"><Ic name="camera" /></div><div className="pt"><strong>Face verification</strong><span>Required to mark attendance</span></div><Chip kind="ok">On</Chip></div>
      <div className="prow"><div className="pi"><Ic name="pin" /></div><div className="pt"><strong>Geofence check</strong><span>Must be on-site to punch</span></div><Chip kind="ok">On</Chip></div>
    </div>
    <div className="sec-lab">About</div>
    <div className="card" style={{ padding: "4px 15px" }}>
      <div className="prow"><div className="pt"><strong>Server</strong></div><span className="pv" style={{ color: "var(--muted)" }}>jewipl.duxdigitech.in</span></div>
      <div className="prow"><div className="pt"><strong>Mode</strong></div><span className="pv">Thin APK</span></div>
    </div>
    <button className="btn danger mt" type="button" disabled={loggingOut} onClick={doLogout}><Ic name="logout" /> {loggingOut ? "Logging out…" : "Log out"}</button>
    <div className="empty"><p>Built by <b style={{ color: "var(--muted)" }}>DUX Digitech</b> · Where business gets digital</p></div>
  </>;
}

const CAL_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const CAL_DOW = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const calPad = (n: number) => String(n).padStart(2, "0");
const calISO = (y: number, m: number, d: number) => `${y}-${calPad(m + 1)}-${calPad(d)}`;
function calParse(v?: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v || "");
  return m ? { y: Number(m[1]), m: Number(m[2]) - 1, d: Number(m[3]) } : null;
}

// In-app calendar (bottom sheet, same look as Select). A native <input type="date"> is
// device/WebView dependent: some phones show the field focused but never open the
// picker, and the OS locale decides the displayed format. This works the same on every
// device, always shows DD/MM/YYYY, and honours min / max (ISO yyyy-mm-dd strings).
function DateField({ label, value, onChange, icon, placeholder, min, max }: any) {
  const [open, setOpen] = useState(false);
  const now = new Date();
  const todayISO = calISO(now.getFullYear(), now.getMonth(), now.getDate());
  const sel = calParse(value);
  const [view, setView] = useState({ y: now.getFullYear(), m: now.getMonth() });
  const openSheet = () => {
    // Open on the selected month; else the nearest month inside the allowed range.
    const anchor = sel || (min && min > todayISO ? calParse(min) : null) || (max && max < todayISO ? calParse(max) : null);
    setView(anchor ? { y: anchor.y, m: anchor.m } : { y: now.getFullYear(), m: now.getMonth() });
    setOpen(true);
  };
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const shift = (months: number) => setView((v) => { const t = v.y * 12 + v.m + months; return { y: Math.floor(t / 12), m: ((t % 12) + 12) % 12 }; });
  const blocked = (iso: string) => Boolean((min && iso < min) || (max && iso > max));
  const pick = (iso: string) => { onChange(iso); setOpen(false); };
  const lead = new Date(view.y, view.m, 1).getDay();
  const total = new Date(view.y, view.m + 1, 0).getDate();
  const cells: (number | null)[] = [...Array(lead).fill(null), ...Array.from({ length: total }, (_, i) => i + 1)];
  return <div className="field">
    <label>{label}</label>
    <button className="inp trigger" type="button" onClick={openSheet}>
      {icon && <Ic name={icon} />}
      <span className="tval" style={{ color: value ? "inherit" : "var(--faint)" }}>{value ? fmtDMY(value) : (placeholder || "DD/MM/YYYY")}</span>
    </button>
    {open && createPortal(
      <div className="select-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
        <div className="date-sheet" role="dialog" aria-modal="true" aria-label={label}>
          <div className="select-sheet-head"><strong>{label}</strong><button className="ibtn" type="button" aria-label="Close" onClick={() => setOpen(false)}><Ic name="close" /></button></div>
          <div className="cal-nav">
            <button type="button" aria-label="Previous year" onClick={() => shift(-12)}>«</button>
            <button type="button" aria-label="Previous month" onClick={() => shift(-1)}>‹</button>
            <strong>{CAL_MONTHS[view.m]} {view.y}</strong>
            <button type="button" aria-label="Next month" onClick={() => shift(1)}>›</button>
            <button type="button" aria-label="Next year" onClick={() => shift(12)}>»</button>
          </div>
          <div className="cal-grid">
            {CAL_DOW.map((d) => <div className="cal-dow" key={d}>{d}</div>)}
            {cells.map((d, i) => {
              if (d === null) return <span key={`b${i}`} />;
              const iso = calISO(view.y, view.m, d);
              const cls = `cal-day${iso === value ? " sel" : ""}${iso === todayISO ? " today" : ""}`;
              return <button type="button" className={cls} key={iso} disabled={blocked(iso)} onClick={() => pick(iso)}>{d}</button>;
            })}
          </div>
          <div className="cal-foot">
            <button className="btn ghost" type="button" disabled={blocked(todayISO)} onClick={() => pick(todayISO)}>Today</button>
            {value ? <button className="btn ghost" type="button" onClick={() => pick("")}>Clear</button> : null}
          </div>
        </div>
      </div>, document.body)}
  </div>;
}

function Field({ label, value, onChange, type = "text", icon, placeholder, min, max }: any) {
  if (type === "date") return <DateField label={label} value={value} onChange={onChange} icon={icon} placeholder={placeholder} min={min} max={max} />;
  return <div className="field"><label>{label}</label><div className="inp">{icon && <Ic name={icon} />}<input type={type} value={value ?? ""} placeholder={placeholder} min={min} max={max} onChange={(e) => onChange(e.target.value)} /></div></div>;
}

function Select({ label, value, onChange, options, icon }: any) {
  const normalized = useMemo(() => (options || []).map((option: any) => {
    const optionValue = typeof option === "string" ? option : option.value;
    const baseText = typeof option === "string" ? option : option.label;
    const description = typeof option === "string" ? "" : option.description;
    const disabled = typeof option === "string" ? false : !!option.disabled;
    const text = description && description !== optionValue ? `${baseText} (${description})` : baseText;
    return { value: optionValue, label: baseText || optionValue, description, disabled, text };
  }), [options]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement | null>(null);
  const selected = normalized.find((o: any) => o.value === value);
  const filtered = normalized.filter((o: any) => `${o.label} ${o.description || ""} ${o.value}`.toLowerCase().includes(query.trim().toLowerCase()));
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => searchRef.current?.focus(), 50);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => { window.clearTimeout(timer); window.removeEventListener("keydown", onKey); };
  }, [open]);
  const choose = (nextValue: string) => { onChange(nextValue); setOpen(false); setQuery(""); };
  return <div className="field">
    <label>{label}</label>
    <button className="inp trigger" type="button" onClick={() => setOpen(true)}>{icon && <Ic name={icon} />}<span className="tval">{selected?.label || "Select"}</span><span className="chev"><Ic name="chevron" style={{ transform: "rotate(-90deg)" }} /></span></button>
    {open && createPortal(
      <div className="select-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
        <div className="select-sheet" role="dialog" aria-modal="true" aria-label={label}>
          <div className="select-sheet-head"><strong>{label}</strong><button className="ibtn" type="button" aria-label="Close" onClick={() => setOpen(false)}><Ic name="close" /></button></div>
          <div className="inp"><Ic name="search" /><input ref={searchRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`Search ${label.toLowerCase()}`} /></div>
          <div className="select-options">
            <button className={`select-option ${!value ? "active" : ""}`} type="button" onClick={() => choose("")}><span>Select</span></button>
            {filtered.length ? filtered.map((o: any) => <button className={`select-option ${o.value === value ? "active" : ""}`} type="button" key={o.value} disabled={o.disabled} style={o.disabled ? { opacity: 0.45, cursor: "not-allowed" } : undefined} onClick={() => { if (!o.disabled) choose(o.value); }}><span>{o.label}</span>{o.description && <small>{o.description}</small>}</button>) : <div className="select-empty">No results found.</div>}
          </div>
        </div>
      </div>, document.body)}
  </div>;
}
