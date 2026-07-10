import { Component, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MapContainer, Marker, TileLayer, Circle, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { API, call, cleanErrorMessage, logout } from "./api";
import { IconSprite, Ic, BrandMark } from "./icons";

type Caps = {
  can_mark_attendance: boolean;
  can_apply_leave: boolean;
  can_register_face: boolean;
  can_manage_locations: boolean;
  can_approve_leave: boolean;
  can_view_admin: boolean;
  can_manage_leave_policy?: boolean;
  can_manage_shift_policy?: boolean;
  can_manage_regularization?: boolean;
  is_admin: boolean;
  is_hr?: boolean;
  is_owner?: boolean;
  approval_level?: string;
};

type View =
  | "dashboard" | "attendance" | "history" | "leave" | "admin" | "face" | "location"
  | "leaveApproval" | "leavePolicy" | "shiftPolicy" | "regularization" | "employees"
  | "notifications" | "profile" | "settings";

const DENY: Caps = {
  can_mark_attendance: false, can_apply_leave: false, can_register_face: false,
  can_manage_locations: false, can_approve_leave: false, can_view_admin: false,
  can_manage_leave_policy: false, can_manage_shift_policy: false, can_manage_regularization: false,
  is_admin: false
};

const REMEMBER_LOGIN_KEY = "jew_hrms_remember_login";
const ADMIN_ACCESS_ROLES = ["JEW HRMS Admin", "JEW HRMS HR", "JEW HRMS Owner"];
const ADMIN_VIEWS: ReadonlySet<View> = new Set<View>(["admin", "face", "location", "leaveApproval", "leavePolicy", "shiftPolicy", "regularization", "employees"]);

function canAccessAdmin(userRoles: unknown) {
  return Array.isArray(userRoles) && userRoles.some((role) => ADMIN_ACCESS_ROLES.includes(String(role).trim()));
}

function applyAdminRoleVisibility(userCaps: Partial<Caps>, userRoles: unknown): Caps {
  const adminAllowed = canAccessAdmin(userRoles);
  return {
    ...DENY,
    ...userCaps,
    can_view_admin: adminAllowed,
    can_register_face: adminAllowed && Boolean(userCaps.can_register_face),
    can_manage_locations: adminAllowed && Boolean(userCaps.can_manage_locations),
    can_approve_leave: adminAllowed && Boolean(userCaps.can_approve_leave),
    can_manage_leave_policy: adminAllowed && Boolean(userCaps.can_manage_leave_policy),
    can_manage_shift_policy: adminAllowed && Boolean(userCaps.can_manage_shift_policy),
    can_manage_regularization: adminAllowed && Boolean(userCaps.can_manage_regularization),
    is_admin: adminAllowed && Boolean(userCaps.is_admin),
    is_hr: adminAllowed && Boolean(userCaps.is_hr),
    is_owner: adminAllowed && Boolean(userCaps.is_owner)
  };
}

function capacitorPlugin(name: string) {
  return (window as any).Capacitor?.Plugins?.[name];
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

// Primary tab views: show the bottom tab bar (and use tab-height padding).
// All other views are sub-screens: hide the tab bar + show a back button.
const TAB_VIEWS: ReadonlySet<View> = new Set<View>(["dashboard", "attendance", "history", "leave", "admin", "profile"]);

const navMeta: Record<View, { title: string; sub: string; nav?: string; back?: boolean }> = {
  dashboard: { title: "JEW HRMS", sub: "Jain Engineering Works", nav: "dashboard" },
  attendance: { title: "Attendance", sub: "Mark in / out", nav: "attendance" },
  history: { title: "History", sub: "My records", nav: "attendance", back: true },
  leave: { title: "Leave", sub: "Apply & track", nav: "leave" },
  admin: { title: "Admin", sub: "Manager access", nav: "admin" },
  face: { title: "Face register", sub: "Enroll template", nav: "admin", back: true },
  location: { title: "Geofence", sub: "Work sites", nav: "admin", back: true },
  leaveApproval: { title: "Leave approval", sub: "Pending requests", nav: "admin", back: true },
  leavePolicy: { title: "Leave policy", sub: "Leave types", nav: "admin", back: true },
  shiftPolicy: { title: "Shift policy", sub: "Attendance rules", nav: "admin", back: true },
  regularization: { title: "Regularization", sub: "Pending review", nav: "admin", back: true },
  employees: { title: "Employees", sub: "Directory", nav: "admin", back: true },
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
  const [adminData, setAdminData] = useState<any>({});
  const [selectedEmployee, setSelectedEmployee] = useState("");
  const [notifications, setNotifications] = useState<any[]>([]);
  const [toast, setToast] = useState<{ title: string; msg: string } | null>(null);
  const [cameraActive, setCameraActive] = useState(false);
  const lastBackRef = useRef(0);

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
    setLeaveData({}); setAdminData({}); setSelectedEmployee(""); setNotifications([]);
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
      const [types, dash, mine, employees] = await Promise.all(calls);
      setLeaveData({ types: types.leave_types, balances: dash.balances, leaves: mine.leaves, employees: employees?.employees || [] });
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
    if (next === "location" && caps.can_manage_locations) {
      const locations = await call(API.getLocations);
      const employees = await call(API.getEmployeeList);
      setAdminData((prev: any) => ({ ...prev, locations: locations.locations, employees: employees.employees }));
    }
    if (next === "employees" && caps.can_view_admin) {
      const employees = await call(API.getEmployeeList);
      setAdminData((prev: any) => ({ ...prev, employees: employees.employees }));
    }
    if (next === "leaveApproval" && caps.can_approve_leave) {
      const pending = await call(API.getPendingLeaves);
      setAdminData((prev: any) => ({ ...prev, pendingLeaves: pending.leaves }));
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
    if (next === "location" && !caps.can_manage_locations) { flash("Not permitted", "You do not have access to location setup."); return; }
    if (next === "employees" && !caps.can_view_admin) { flash("Not permitted", "You do not have access to employee list."); return; }
    if (next === "leaveApproval" && !caps.can_approve_leave) { flash("Not permitted", "You do not have approval permission."); return; }
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
            {view === "attendance" && <Attendance flash={flash} open={open} initialStatus={adminData.attendanceStatus} onCameraActiveChange={setCameraActive} onMarked={async () => { setDashboard(await call(API.getDashboard)); const status = await call(API.getTodayAttendanceStatus); setAdminData((prev: any) => ({ ...prev, attendanceStatus: status })); }} />}
            {view === "history" && <History data={history} />}
            {view === "leave" && <Leave data={leaveData} caps={caps} flash={flash} reload={() => open("leave")} />}
            {view === "admin" && (caps.can_view_admin ? <Admin open={open} caps={caps} data={adminData} /> : <NotPermitted />)}
            {view === "face" && (caps.can_view_admin && caps.can_register_face ? <FaceAdmin employees={adminData.employees || []} flash={flash} selectedEmployee={selectedEmployee} onCameraActiveChange={setCameraActive} /> : <NotPermitted />)}
            {view === "location" && (caps.can_view_admin && caps.can_manage_locations ? <LocationAdmin data={adminData} setData={setAdminData} flash={flash} reload={() => open("location")} /> : <NotPermitted />)}
            {view === "leaveApproval" && (caps.can_view_admin && caps.can_approve_leave ? <LeaveApproval leaves={adminData.pendingLeaves || []} flash={flash} reload={() => open("leaveApproval")} /> : <NotPermitted />)}
            {view === "leavePolicy" && (caps.can_view_admin && caps.can_manage_leave_policy ? <LeavePolicy types={adminData.leaveTypes || []} flash={flash} reload={() => open("leavePolicy")} /> : <NotPermitted />)}
            {view === "shiftPolicy" && (caps.can_view_admin && caps.can_manage_shift_policy ? <ShiftPolicy policies={adminData.shiftPolicies || []} flash={flash} reload={() => open("shiftPolicy")} /> : <NotPermitted />)}
            {view === "regularization" && (caps.can_view_admin && caps.can_manage_regularization ? <Regularization items={adminData.regularizations || []} flash={flash} reload={() => open("regularization")} /> : <NotPermitted />)}
            {view === "employees" && (caps.can_view_admin ? <Employees employees={adminData.employees || []} open={open} selectEmployee={setSelectedEmployee} /> : <NotPermitted />)}
            {view === "notifications" && <Notifications items={notifications} />}
            {view === "profile" && <Profile profile={profile} open={open} onLogout={handleLogout} />}
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
    { view: "notifications", label: "Notifications", icon: "bell" }
  ].filter(Boolean) as any[];
  const admin = [
    caps.can_view_admin && { view: "admin", label: "Admin panel", icon: "shield" },
    caps.can_approve_leave && { view: "leaveApproval", label: "Leave approval", icon: "check" },
    caps.can_view_admin && { view: "employees", label: "Employees", icon: "users" },
    caps.can_register_face && { view: "face", label: "Face register", icon: "camera" },
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
      <Mod icon="bell" cyan title="Notifications" sub="Alerts & reminders" onClick={() => open("notifications")} />
    </div>
  </>;
}

function Attendance({ flash, onMarked, initialStatus, onCameraActiveChange }: any) {
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

  const stopVerifyCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraReady(false);
    onCameraActiveChange?.(false);
  };
  const closeVerify = () => {
    stopVerifyCamera(); autoSubmitRef.current = false; setVerifyType(null); setSubmitting(false);
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
    const video = videoRef.current; const canvas = canvasRef.current;
    if (!video || !canvas || !streamRef.current || !video.videoWidth || !video.videoHeight) throw new Error("Camera is still starting. Please try again.");
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.9);
  };
  const openVerify = (type: "IN" | "OUT") => {
    if (type === "IN" && !status?.can_mark_in) { flash("Attendance", !faceReady ? "Face not registered. Please contact admin." : !locationReady ? "Location not assigned. Please contact admin." : "Mark In is not available now."); return; }
    if (type === "OUT" && !status?.can_mark_out) { flash("Attendance", !faceReady ? "Face not registered. Please contact admin." : !locationReady ? "Location not assigned. Please contact admin." : "Mark Out is not available now."); return; }
    setVerifyType(type);
  };
  const confirmVerify = async () => {
    if (!verifyType || submitting) return;
    setSubmitting(true);
    try {
      setFaceStatus("Face captured");
      const capturedImage = captureCurrentFrame();
      if (!capturedImage) { flash("Face required", "Please keep your full face inside the frame."); return; }
      setGpsStatus("Capturing GPS...");
      const currentCoords = await getCurrentLocation();
      setCoords(currentCoords); setGpsStatus("Captured"); setFaceStatus("Verifying face");
      const result = await call(API.markAttendance, { type: verifyType, face_image: capturedImage, latitude: currentCoords.latitude, longitude: currentCoords.longitude, accuracy: currentCoords.accuracy, timestamp: new Date().toISOString() });
      await onMarked?.();
      if (result?.attendance_status) setStatus(result.attendance_status);
      flash("Attendance", result.message || toastText(result.code));
      closeVerify();
    } catch (error) {
      setFaceStatus(cleanErrorMessage(error));
      flash("Attendance failed", cleanErrorMessage(error));
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
      <p className="sub" style={{ marginTop: 12 }}>{!faceReady ? "Face not registered. Please contact admin." : !locationReady ? "Location not assigned. Please contact admin." : "Center your face in the frame — verification is automatic."}</p>
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

function History({ data }: any) {
  const checkins = data?.checkins || [];
  const attendance = data?.attendance || [];
  const present = attendance.filter((a: any) => String(a.status).toLowerCase().includes("present")).length;
  return <>
    <div className="grid2">
      <Stat icon="check" label="Present" value={present || attendance.length || 0} small={attendance.length ? `/${attendance.length}` : ""} />
      <Stat icon="clock" label="Records" value={checkins.length} />
    </div>
    <div className="sec-lab">Recent checkins</div>
    <div className="card list" style={{ padding: "4px 15px" }}>
      {checkins.length ? checkins.map((row: any) => (
        <div className="row" key={row.name}><div className="date" style={{ width: 76 }}>{fmtDMY(row.time)}</div><div className="mid"><strong>{row.log_type}</strong><span>{String(row.time).slice(11, 16)}</span></div><Chip kind="ok">Synced</Chip></div>
      )) : <div className="empty"><h3>No records</h3><p>No attendance records found.</p></div>}
    </div>
  </>;
}

function Leave({ data, caps, flash, reload }: any) {
  const [form, setForm] = useState({ employee: "", leave_type: "", from_date: "", to_date: "", reason: "", half_day: false, half_day_date: "", half_day_type: "First Half" });
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const canSelectEmployee = Boolean(caps?.can_view_admin);
  const submit = async () => {
    if (!form.leave_type || !form.from_date || !form.to_date) { flash("Required fields", "Leave Type, From Date and To Date are required."); return; }
    if (form.from_date > form.to_date) { flash("Invalid dates", "From Date cannot be after To Date."); return; }
    if (form.half_day && !form.half_day_date) { flash("Required fields", "Half Day Date is required."); return; }
    const result = await runAction("submit-leave", async () => {
      const payload = canSelectEmployee ? form : { ...form, employee: undefined };
      const response = await call(API.applyLeave, payload);
      await reload();
      return response;
    }, { successTitle: "Leave submitted", errorTitle: "Leave failed" });
    if (result) setForm({ employee: "", leave_type: "", from_date: "", to_date: "", reason: "", half_day: false, half_day_date: "", half_day_type: "First Half" });
  };
  const cancelLeave = async (name: string) => {
    await runAction(`cancel-${name}`, async () => { await call(API.cancelLeave, { name }); reload(); }, { successTitle: "Leave cancelled", successMessage: "Leave request updated.", errorTitle: "Cancel failed" });
  };
  const balances = data.balances || [];
  return <>
    {balances.length > 0 && <div className="grid2">{dedupeByLabel(balances.filter((b: any) => !HIDDEN_LEAVE_TYPES.has(b.leave_type)).map((b: any) => ({ ...b, label: b.leave_type }))).slice(0, 2).map((b: any, i: number) => <Stat key={i} label={b.leave_type} value={Number(b.total_leaves_allocated ?? 0)} small=" days" />)}</div>}
    <div className="card" style={{ marginTop: balances.length ? 12 : 0 }}>
      {canSelectEmployee && <Select label="Employee" value={form.employee} onChange={(v: string) => setForm({ ...form, employee: v })} options={(data.employees || []).map((e: any) => ({ value: e.name, label: e.employee_name || e.name, description: e.description || e.name }))} />}
      <Select label="Leave type" value={form.leave_type} onChange={(v: string) => setForm({ ...form, leave_type: v })} options={dedupeByLabel((data.types || []).filter((t: any) => !HIDDEN_LEAVE_TYPES.has(t.name)).map((t: any) => ({ value: t.name, label: t.leave_type_name || t.name })))} />
      <div className="grid2" style={{ marginTop: 13 }}>
        <Field label="From" icon="calendar" type="date" value={form.from_date} onChange={(v: string) => setForm({ ...form, from_date: v })} />
        <Field label="To" icon="calendar" type="date" value={form.to_date} onChange={(v: string) => setForm({ ...form, to_date: v })} />
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 13, color: "var(--muted)", fontSize: 12.5, fontWeight: 600 }}>
        <input type="checkbox" style={{ accentColor: "var(--iris)" }} checked={form.half_day} onChange={(e) => setForm({ ...form, half_day: e.target.checked })} /> Half day
      </label>
      {form.half_day && <Field label="Half day date" icon="calendar" type="date" value={form.half_day_date} onChange={(v: string) => setForm({ ...form, half_day_date: v })} />}
      {form.half_day && <Select label="Half day type" value={form.half_day_type} onChange={(v: string) => setForm({ ...form, half_day_type: v })} options={["First Half", "Second Half"]} />}
      <Field label="Reason" value={form.reason} onChange={(v: string) => setForm({ ...form, reason: v })} placeholder="Add a short note (optional)" />
      <div style={{ marginTop: 16 }}><button className="btn" type="button" disabled={isAnyBusy} onClick={submit}><Ic name="check" /> {isBusy("submit-leave") ? "Submitting…" : "Submit leave request"}</button></div>
    </div>
    <div className="sec-lab">My requests</div>
    {(data.leaves || []).length ? data.leaves.map((l: any) => {
      const st = l.approval_status || l.jew_hrms_approval_status || l.status;
      const kind = statusChipKind(st);
      return <div className="qrow" key={l.name}>
        <div className="qa"><Ic name={kind === "ok" ? "check" : kind === "err" ? "close" : "clock"} /></div>
        <div className="qt"><strong>{l.leave_type}{l.half_day ? " · Half day" : ""}</strong><span>{fmtDMY(l.from_date)} → {fmtDMY(l.to_date)}</span></div>
        {["Approved", "Rejected", "Cancelled"].includes(st) ? <Chip kind={kind}>{st}</Chip>
          : <button className="btn danger sm" style={{ width: "auto", padding: "0 12px" }} type="button" disabled={isAnyBusy} onClick={() => cancelLeave(l.name)}>{isBusy(`cancel-${l.name}`) ? "…" : "Cancel"}</button>}
      </div>;
    }) : <div className="empty"><h3>No requests</h3><p>No leave requests yet.</p></div>}
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
      {caps.can_view_admin && <Mod icon="users" cyan title="Employees" sub="Face / location status" onClick={() => open("employees")} />}
      {caps.can_register_face && <Mod icon="camera" title="Face register" sub="Enroll employee templates" onClick={() => open("face")} />}
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
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  useEffect(() => { if (selectedEmployee) setEmployee(selectedEmployee); }, [selectedEmployee]);
  const selected = (employees || []).find((item: any) => item.name === employee);
  const save = async () => {
    if (!employee || !image) { flash("Required fields", "Select employee and capture face before saving."); return; }
    await runAction("save-face", async () => { await call(API.registerEmployeeFace, { employee, face_image: image }); setImage(""); }, { successTitle: "Face registered", successMessage: "Server template saved for employee.", errorTitle: "Face failed" });
  };
  return <>
    <Select label="Select employee" icon="user" value={employee} onChange={(value: string) => { setEmployee(value); setImage(""); window.dispatchEvent(new Event("jew-hrms-stop-camera")); }} options={(employees || []).map((e: any) => ({ value: e.name, label: e.label || e.employee_name || e.name, description: e.description || e.name }))} />
    {selected && <div className="qrow" style={{ marginTop: 12 }}><div className="qa"><Ic name="user" /></div><div className="qt"><strong>{selected.employee_name || selected.name}</strong><span>{selected.name} · {selected.face_registered ? "Registered" : "Not registered"}</span></div></div>}
    <div className="mt"><LiveCamera image={image} onCapture={setImage} flash={flash} disabled={!employee} disabledMessage="Please select employee first." onActiveChange={onCameraActiveChange} /></div>
    <button className="btn mt" type="button" disabled={isAnyBusy || !employee || !image} onClick={save}><Ic name="check" /> {isBusy("save-face") ? "Saving…" : "Save face template"}</button>
    {!(employees || []).length && <div className="empty"><h3>No employees loaded</h3><p>Open this screen from Admin after the employee list loads.</p></div>}
  </>;
}

function LiveCamera({ image, onCapture, flash, disabled = false, disabledMessage = "Please select employee first.", onActiveChange }: any) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
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
  const capture = () => {
    const video = videoRef.current; const canvas = canvasRef.current;
    if (!video || !canvas || !streamRef.current || video.videoWidth === 0) { flash("Camera", disabled ? disabledMessage : "Start camera before capturing face."); return; }
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
        <button className="btn" type="button" disabled={!active || starting} onClick={capture}><Ic name="check" /> Capture</button>
      </div>
      {image && <button className="btn ghost mt" type="button" onClick={() => { onCapture(""); if (!disabled) start(); }}>Retake</button>}
    </div>
  );
}

function LocationAdmin({ data, flash, reload }: any) {
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
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
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
    </div>
    <div className="card mt">
      <Select label="Employee" value={employee} onChange={loadEmployeeLocations} options={employeeOptions} />
      <Select label="Location" value={location} onChange={setLocation} options={locationOptions} />
      <button className="btn ghost mt" type="button" disabled={isAnyBusy || !employee || !location} onClick={assign}><Ic name="plus" /> {isBusy("assign-location") ? "Assigning…" : "Assign location"}</button>
    </div>
    <div className="sec-lab">Saved locations</div>
    <div className="list card" style={{ padding: "4px 15px" }}>
      {(data.locations || []).length ? data.locations.map((item: any) => (
        <div className="row" key={item.name}><div className="prow" style={{ border: 0, padding: 0, flex: 1 }}><div className="pi"><Ic name="building" /></div><div className="pt"><strong>{item.location_name || item.name}</strong><span>{item.latitude}, {item.longitude} · {item.default_radius_meter} m</span></div></div><button className="btn danger sm" style={{ width: "auto", padding: "0 12px" }} type="button" disabled={isAnyBusy} onClick={() => deleteLocation(item.name)}>{isBusy(`delete-location-${item.name}`) ? "…" : "Delete"}</button></div>
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

function Regularization({ items, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decide = async (name: string, action: string) => {
    const remarks = action === "Rejected" ? window.prompt("Reject reason") || "" : window.prompt("Remarks") || "";
    await runAction(`${action}-${name}`, async () => { await call(API.decideRegularization, { name, action, remarks }); await reload(); }, { successTitle: "Updated", successMessage: "Regularization updated.", errorTitle: "Update failed" });
  };
  return <>
    {items.length ? items.map((r: any) => (
      <div className="card" style={{ marginBottom: 11 }} key={r.name}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}><div className="qa" style={{ width: 38, height: 38, borderRadius: 11, background: "var(--iris-tint)", color: "var(--iris)", display: "grid", placeItems: "center" }}><Ic name="refresh" style={{ width: 17, height: 17 }} /></div><div style={{ flex: 1, minWidth: 0 }}><strong style={{ fontSize: 13 }}>{r.employee}</strong><div style={{ fontSize: 11, color: "var(--faint)" }}>{fmtDMY(r.attendance_date)} · {r.issue_type} · {r.policy_action || "Review"}</div></div></div>
        <div style={{ display: "flex", gap: 9, marginTop: 12 }}>
          <button className="btn ok sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(r.name, "Approved as Present")}>{isBusy(`Approved as Present-${r.name}`) ? "…" : "Present"}</button>
          <button className="btn ghost sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(r.name, "Marked Half Day")}>Half day</button>
          <button className="btn danger sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(r.name, "Rejected")}>Reject</button>
        </div>
      </div>
    )) : <div className="empty"><h3>All clear</h3><p>No pending regularization.</p></div>}
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
        <div style={{ display: "flex", gap: 9, marginTop: 12 }}>
          <button className="btn ok sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(l.name, true)}><Ic name="check" /> {isBusy(`approve-${l.name}`) ? "…" : st === "Pending Admin Approval" ? "Final approve" : "Approve"}</button>
          <button className="btn danger sm" style={{ flex: 1 }} type="button" disabled={isAnyBusy} onClick={() => decide(l.name, false)}><Ic name="close" /> {isBusy(`reject-${l.name}`) ? "…" : "Reject"}</button>
        </div>
      </div>;
    }) : <div className="empty"><h3>All clear</h3><p>No pending leave requests.</p></div>}
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

function Notifications({ items }: any) {
  return <>
    {items.length ? items.map((n: any, i: number) => (
      <div className="qrow" key={i}><div className="qa"><Ic name="bell" style={{ width: 17, height: 17 }} /></div><div className="qt"><strong>{n.title}</strong><span>{n.message}</span></div></div>
    )) : <div className="empty"><h3>No notifications</h3><p>You're all caught up.</p></div>}
  </>;
}

function Profile({ profile, open, onLogout }: any) {
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
      <div className="prow"><div className="pi"><Ic name="camera" /></div><div className="pt"><strong>Face</strong></div><span className="pv">{profile?.face_status?.registered ? "Ready" : "Pending"}</span></div>
      <div className="prow"><div className="pi"><Ic name="mail" /></div><div className="pt"><strong>User</strong><span>{profile?.user_id || "—"}</span></div></div>
    </div>
    <button className="btn ghost mt" type="button" onClick={() => open("settings")}><Ic name="gear" /> Settings</button>
    <button className="btn danger mt" type="button" disabled={loggingOut} onClick={doLogout}><Ic name="logout" /> {loggingOut ? "Logging out…" : "Log out"}</button>
  </>;
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

function Field({ label, value, onChange, type = "text", icon, placeholder }: any) {
  return <div className="field"><label>{label}</label><div className="inp">{icon && <Ic name={icon} />}<input type={type} value={value ?? ""} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} /></div></div>;
}

function Select({ label, value, onChange, options, icon }: any) {
  const normalized = useMemo(() => (options || []).map((option: any) => {
    const optionValue = typeof option === "string" ? option : option.value;
    const baseText = typeof option === "string" ? option : option.label;
    const description = typeof option === "string" ? "" : option.description;
    const text = description && description !== optionValue ? `${baseText} (${description})` : baseText;
    return { value: optionValue, label: baseText || optionValue, description, text };
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
            {filtered.length ? filtered.map((o: any) => <button className={`select-option ${o.value === value ? "active" : ""}`} type="button" key={o.value} onClick={() => choose(o.value)}><span>{o.label}</span>{o.description && <small>{o.description}</small>}</button>) : <div className="select-empty">No results found.</div>}
          </div>
        </div>
      </div>, document.body)}
  </div>;
}
