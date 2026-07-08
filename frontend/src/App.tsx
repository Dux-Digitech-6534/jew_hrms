import {
  ArrowLeft,
  Bell,
  CalendarDays,
  Camera,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Eye,
  EyeOff,
  HelpCircle,
  Home,
  LogOut,
  Menu,
  MapPin,
  Moon,
  RefreshCw,
  Settings as SettingsIcon,
  ShieldCheck,
  Sun,
  UserRound,
  Users,
  X,
  XCircle
} from "lucide-react";
import { Component, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MapContainer, Marker, TileLayer, Circle, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { API, call, cleanErrorMessage, logout } from "./api";
import { assets } from "./assets";

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
  | "dashboard"
  | "attendance"
  | "history"
  | "leave"
  | "admin"
  | "face"
  | "location"
  | "leaveApproval"
  | "leavePolicy"
  | "shiftPolicy"
  | "regularization"
  | "employees"
  | "notifications"
  | "profile"
  | "settings";

const DENY: Caps = {
  can_mark_attendance: false,
  can_apply_leave: false,
  can_register_face: false,
  can_manage_locations: false,
  can_approve_leave: false,
  can_view_admin: false,
  can_manage_leave_policy: false,
  can_manage_shift_policy: false,
  can_manage_regularization: false,
  is_admin: false
};

const REMEMBER_LOGIN_KEY = "jew_hrms_remember_login";
const ADMIN_ACCESS_ROLES = [
  "JEW HRMS Admin",
  "JEW HRMS HR",
  "JEW HRMS Owner"
];
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
  const hrs = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return `${hrs}h ${mins}m`;
}

function hasActiveFrappeUserCookie() {
  return document.cookie.split(";").some((part) => {
    const [key, value] = part.trim().split("=");
    return key === "user_id" && decodeURIComponent(value || "") !== "Guest";
  });
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="shell center">
          <div className="card accent card-pad app-error">
            <XCircle size={24} />
            <h2>Unable to load JEW HRMS.</h2>
            <p>Please refresh or contact admin.</p>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const navMeta: Record<View, { title: string; sub: string; nav?: string }> = {
  dashboard: { title: "JEW HRMS", sub: "Employee Dashboard", nav: "dashboard" },
  attendance: { title: "Attendance", sub: "Face + Location", nav: "attendance" },
  history: { title: "Attendance", sub: "Monthly Records", nav: "attendance" },
  leave: { title: "Leave", sub: "Employee Self Service", nav: "leave" },
  admin: { title: "Admin Panel", sub: "HR Controls", nav: "admin" },
  face: { title: "Face Register", sub: "Admin Module", nav: "admin" },
  location: { title: "Location", sub: "Geofence Setup", nav: "admin" },
  leaveApproval: { title: "Leave Approval", sub: "Approver View", nav: "admin" },
  leavePolicy: { title: "Leave Policy", sub: "HR Configuration", nav: "admin" },
  shiftPolicy: { title: "Shift Policy", sub: "Attendance Rules", nav: "admin" },
  regularization: { title: "Regularization", sub: "Pending Review", nav: "admin" },
  employees: { title: "Employees", sub: "Admin Overview", nav: "admin" },
  notifications: { title: "Notifications", sub: "Alerts", nav: "dashboard" },
  profile: { title: "Profile", sub: "Employee Details", nav: "profile" },
  settings: { title: "Settings", sub: "App Info", nav: "profile" }
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
      if (options.successTitle) {
        flash(options.successTitle, options.successMessage || result?.message || "Done.");
      }
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
  useMapEvents({
    click: (event: any) => onPick(event.latlng.lat, event.latlng.lng)
  });
  return null;
}

function MapRecenter({ center }: { center: [number, number] }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, Math.max(map.getZoom(), 15), { animate: true });
  }, [center[0], center[1], map]);
  return null;
}

export default function App() {
  const [theme, setTheme] = useState(localStorage.getItem("jew-theme") || "light");
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null);
  const [view, setView] = useState<View>(() => (location.pathname.includes("/m") ? "dashboard" : "dashboard"));
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
  }, [theme]);

  const flash = (title: string, msg: string) => {
    setToast({ title, msg });
    window.setTimeout(() => setToast(null), 3200);
  };

  const resetSessionState = () => {
    window.dispatchEvent(new Event("jew-hrms-stop-camera"));
    window.dispatchEvent(new Event("jew-hrms-close-attendance-verify"));
    setSession(null);
    setDashboard(null);
    setProfile(null);
    setHistory(null);
    setLeaveData({});
    setAdminData({});
    setSelectedEmployee("");
    setNotifications([]);
    setCaps(DENY);
    setView("dashboard");
    setViewStack([]);
    setMenuOpen(false);
    setRefreshing(false);
    setCameraActive(false);
  };

  const loadBase = async (force = false) => {
    if (!force && !hasActiveFrappeUserCookie()) {
      resetSessionState();
      setLoggedIn(false);
      return;
    }
    try {
      const user = await call(API.getSessionUser);
      const c = await call<Caps>(API.capabilities);
      setSession(user);
      setCaps(applyAdminRoleVisibility(c, user?.roles));
      setLoggedIn(true);
      try {
        const dash = await call(API.getDashboard);
        setDashboard(dash);
      } catch {
        setDashboard(null);
      }
    } catch {
      resetSessionState();
      setLoggedIn(false);
    }
  };

  const handleLogout = async () => {
    try {
      await logout();
      resetSessionState();
      setLoggedIn(false);
      flash("Logged out", "You have returned to JEW HRMS login.");
    } catch (error) {
      flash("Logout failed", cleanErrorMessage(error));
    }
  };

  useEffect(() => {
    loadBase();
  }, []);

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
    if (next === "profile") {
      const p = await call(API.getEmployeeProfile);
      setProfile(p.employee_profile);
    }
    if (next === "notifications") {
      const n = await call(API.getNotifications);
      setNotifications(n.notifications || []);
    }
    if (next === "admin") {
      const dash = await call(API.getDashboard);
      setDashboard(dash);
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
      if (view !== "dashboard") {
        setView("dashboard");
        setViewStack([]);
      }
      return;
    }
    if (next === "face" && !caps.can_register_face) {
      flash("Not permitted", "You do not have access to face registration.");
      return;
    }
    if (next === "location" && !caps.can_manage_locations) {
      flash("Not permitted", "You do not have access to location setup.");
      return;
    }
    if (next === "employees" && !caps.can_view_admin) {
      flash("Not permitted", "You do not have access to employee list.");
      return;
    }
    if (next === "leaveApproval" && !caps.can_approve_leave) {
      flash("Not permitted", "You do not have approval permission.");
      return;
    }
    if (next === "leavePolicy" && !caps.can_manage_leave_policy) {
      flash("Not permitted", "You do not have access to leave policy.");
      return;
    }
    if (next === "shiftPolicy" && !caps.can_manage_shift_policy) {
      flash("Not permitted", "You do not have access to shift policy.");
      return;
    }
    if (next === "regularization" && !caps.can_manage_regularization) {
      flash("Not permitted", "You do not have access to regularization.");
      return;
    }
    setMenuOpen(false);
    if (!options.replace && next !== view) {
      setViewStack((stack) => [...stack, view].slice(-12));
    }
    setView(next);
    try {
      await loadViewData(next);
    } catch (error: any) {
      if (!options.silent) flash("Unable to load", cleanErrorMessage(error));
    }
  };

  const goBack = () => {
    if (cameraActive) {
      window.dispatchEvent(new Event("jew-hrms-stop-camera"));
      window.dispatchEvent(new Event("jew-hrms-close-attendance-verify"));
      setCameraActive(false);
      return;
    }
    if (menuOpen) {
      setMenuOpen(false);
      return;
    }
    if (viewStack.length) {
      const previous = viewStack[viewStack.length - 1];
      setViewStack((stack) => stack.slice(0, -1));
      open(previous, { replace: true, silent: true });
      return;
    }
    if (view !== "dashboard") {
      open("dashboard", { replace: true, silent: true });
      return;
    }
    const now = Date.now();
    if (now - lastBackRef.current < 2000) {
      capacitorPlugin("App")?.exitApp?.();
      return;
    }
    lastBackRef.current = now;
    flash("Exit", "Press back again to exit.");
  };

  const refreshCurrent = async () => {
    setRefreshing(true);
    try {
      await loadViewData(view);
    } catch (error) {
      flash("Unable to refresh", cleanErrorMessage(error));
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    const AppPlugin = capacitorPlugin("App");
    let handle: any;
    AppPlugin?.addListener?.("backButton", () => goBack())?.then?.((listener: any) => {
      handle = listener;
    });
    return () => {
      handle?.remove?.();
    };
  }, [cameraActive, menuOpen, view, viewStack]);

  useEffect(() => {
    if (loggedIn && ADMIN_VIEWS.has(view) && !caps.can_view_admin) {
      setView("dashboard");
      setViewStack([]);
      flash("Not permitted", "You do not have access to this admin screen.");
    }
  }, [caps.can_view_admin, loggedIn, view]);

  const navItems = useMemo(() => {
    const items = [
      { id: "dashboard", label: "Home", icon: <Home size={19} />, view: "dashboard" as View },
      ...(caps.can_mark_attendance ? [{ id: "attendance", label: "Attendance", icon: <Clock3 size={19} />, view: "attendance" as View }] : []),
      ...(caps.can_view_admin ? [{ id: "admin", label: "Admin", icon: <ShieldCheck size={19} />, view: "admin" as View }] : []),
      { id: "profile", label: "Profile", icon: <UserRound size={19} />, view: "profile" as View }
    ];
    return items;
  }, [caps.can_mark_attendance, caps.can_view_admin]);

  if (loggedIn === null) {
    return <div className="shell center"><div className="loader">Loading JEW HRMS</div></div>;
  }

  if (!loggedIn) {
    return <Login onDone={() => loadBase(true)} flash={flash} />;
  }

  const meta = navMeta[view];
  return (
    <ErrorBoundary>
    <div className="shell">
      <header className="topbar">
        <button className="icon-btn" onClick={goBack} aria-label="Back"><ArrowLeft size={18} /></button>
        <div className="brand">
          <picture><source srcSet={assets.logoWhite} media="(prefers-color-scheme: dark)" /><img className="logo" src={theme === "dark" ? assets.logoWhite : assets.logo} alt="DUX Digitech" /></picture>
          <div className="brand-title"><strong>{meta.title}</strong><span>{meta.sub}</span></div>
        </div>
        <div className="top-actions">
          <button className="icon-btn" onClick={() => setMenuOpen(true)} aria-label="Menu"><Menu size={19} /></button>
          <button className="icon-btn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button>
        </div>
      </header>

      <PullToRefresh onRefresh={refreshCurrent} refreshing={refreshing}>
      <main className="page active">
        {view === "dashboard" && <Dashboard data={dashboard} open={open} caps={caps} flash={flash} />}
        {view === "attendance" && <Attendance flash={flash} open={open} initialStatus={adminData.attendanceStatus} onCameraActiveChange={setCameraActive} onMarked={async () => { setDashboard(await call(API.getDashboard)); const status = await call(API.getTodayAttendanceStatus); setAdminData((prev: any) => ({ ...prev, attendanceStatus: status })); }} />}
        {view === "history" && <History data={history} />}
        {view === "leave" && <Leave data={leaveData} caps={caps} flash={flash} reload={() => open("leave")} />}
        {view === "admin" && (caps.can_view_admin ? <Admin open={open} caps={caps} /> : <NotPermitted />)}
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

      <nav className="bottom-nav" style={{ gridTemplateColumns: `repeat(${navItems.length},1fr)` }}>
        {navItems.map((item) => {
          return <button key={item.id} className={meta.nav === item.id ? "active" : ""} onClick={() => open(item.view)}><span className="nav-ico">{item.icon}</span><span>{item.label}</span></button>;
        })}
      </nav>
      <MenuDrawer open={menuOpen} caps={caps} active={view} openView={open} onClose={() => setMenuOpen(false)} onLogout={handleLogout} />
      {toast && <div className="toast-stack"><div className="toast"><CheckCircle2 size={18} /><div><b>{toast.title}</b><span>{toast.msg}</span></div></div></div>}
    </div>
    </ErrorBoundary>
  );
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
      if (saved?.username && saved?.password) {
        setUsername(saved.username);
        setPassword(saved.password);
        setRememberPassword(true);
      }
    } catch {
      localStorage.removeItem(REMEMBER_LOGIN_KEY);
    }
  }, []);

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await call(API.login, { email: username, password });
      if (rememberPassword) {
        localStorage.setItem(REMEMBER_LOGIN_KEY, JSON.stringify({ username, password }));
      } else {
        localStorage.removeItem(REMEMBER_LOGIN_KEY);
      }
      flash("Login successful", "Welcome to Jain Engineering Works HRMS");
      await onDone();
    } catch (error: any) {
      flash("Login failed", cleanErrorMessage(error) || "Invalid credentials");
    } finally {
      setBusy(false);
    }
  };

  const handleLoginKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" && !busy) {
      event.preventDefault();
      void submit();
    }
  };

  return (
    <div className="shell">
      <div className="login-wrap">
        <div className="login-hero"><div className="eyebrow">JAIN ENGINEERING WORKS</div><h1>JEW <span className="grad">HRMS</span></h1><p>Attendance, leave, face verification and employee self service in one secure mobile app.</p></div>
        <div className="card accent login-card login-panel" role="group" aria-label="JEW HRMS login" onKeyDown={handleLoginKeyDown}>
          <div className="field">
            <label htmlFor="login-usr">Employee ID / Email</label>
            <input
              id="login-usr"
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              type="text"
              name="usr"
              autoComplete="username"
              inputMode="text"
              placeholder="Employee ID or email"
              required
            />
          </div>
          <div className="field">
            <label htmlFor="login-pwd">Password</label>
            <div className="password-input">
              <input
                id="login-pwd"
                className="input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                type={showPassword ? "text" : "password"}
                name="pwd"
                autoComplete="current-password"
                required
              />
              <button className="password-toggle" type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"}>
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </div>
          <div className="login-options">
            <label className="remember-check">
              <input
                type="checkbox"
                checked={rememberPassword}
                onChange={(event) => {
                  const checked = event.target.checked;
                  setRememberPassword(checked);
                  if (!checked) localStorage.removeItem(REMEMBER_LOGIN_KEY);
                }}
              />
              <span>Keep me signed in</span>
            </label>
          </div>
          <button className="btn btn-primary btn-wide" type="button" onClick={() => void submit()} disabled={busy}>{busy ? "Signing in..." : "Sign In"}</button>
          <div className="module-strip"><div>Face</div><div>Location</div><div>Leave</div></div>
        </div>
        <div className="footer">Powered by DUX Digitech</div>
      </div>
    </div>
  );
}

function Hero({ eyebrow, title, grad, text }: { eyebrow: string; title: string; grad: string; text: string }) {
  return <div className="hero"><div className="eyebrow">{eyebrow}</div><h1>{title}<br /><span className="grad">{grad}</span></h1><p>{text}</p></div>;
}

function NotPermitted() {
  return <div className="card accent card-pad"><Empty text="Not permitted." /></div>;
}

function PullToRefresh({ children, onRefresh, refreshing }: { children: ReactNode; onRefresh: () => Promise<void>; refreshing: boolean }) {
  const startY = useRef(0);
  const pulling = useRef(false);
  const [distance, setDistance] = useState(0);

  const begin = (event: React.TouchEvent) => {
    if (window.scrollY > 2 || refreshing) return;
    startY.current = event.touches[0].clientY;
    pulling.current = true;
  };
  const move = (event: React.TouchEvent) => {
    if (!pulling.current) return;
    const delta = event.touches[0].clientY - startY.current;
    if (delta > 0) setDistance(Math.min(72, delta / 2));
  };
  const end = async () => {
    if (!pulling.current) return;
    const shouldRefresh = distance > 48;
    pulling.current = false;
    setDistance(0);
    if (shouldRefresh) await onRefresh();
  };

  return (
    <div className="ptr-wrap" onTouchStart={begin} onTouchMove={move} onTouchEnd={end} onTouchCancel={end}>
      <div className={`ptr-indicator ${refreshing || distance > 8 ? "show" : ""}`} style={{ transform: `translate(-50%, ${refreshing ? 10 : distance - 38}px)` }}>
        <RefreshCw size={16} className={refreshing ? "spin" : ""} />
      </div>
      {children}
    </div>
  );
}

function MenuDrawer({ open, caps, active, openView, onClose, onLogout }: any) {
  if (!open) return null;
  const normal = [
    { view: "dashboard", label: "Dashboard", icon: <Home size={18} /> },
    caps.can_mark_attendance && { view: "attendance", label: "Attendance", icon: <Clock3 size={18} /> },
    caps.can_mark_attendance && { view: "history", label: "Attendance History", icon: <CalendarDays size={18} /> },
    caps.can_apply_leave && { view: "leave", label: "Leave Apply", icon: <CalendarDays size={18} /> },
    { view: "notifications", label: "Notifications", icon: <Bell size={18} /> },
    { view: "profile", label: "Profile", icon: <UserRound size={18} /> },
    { view: "settings", label: "Settings", icon: <SettingsIcon size={18} /> }
  ].filter(Boolean);
  const admin = [
    caps.can_view_admin && { view: "admin", label: "Admin Panel", icon: <ShieldCheck size={18} /> },
    caps.can_register_face && { view: "face", label: "Face Register", icon: <Camera size={18} /> },
    caps.can_manage_locations && { view: "location", label: "Location / Geofence", icon: <MapPin size={18} /> },
    caps.can_approve_leave && { view: "leaveApproval", label: "Leave Approval", icon: <CheckCircle2 size={18} /> },
    caps.can_view_admin && { view: "employees", label: "Employee List", icon: <Users size={18} /> },
    caps.can_manage_leave_policy && { view: "leavePolicy", label: "Leave Type / Leave Policy", icon: <CalendarDays size={18} /> },
    caps.can_manage_shift_policy && { view: "shiftPolicy", label: "Shift & Attendance Policy", icon: <Clock3 size={18} /> },
    caps.can_manage_regularization && { view: "regularization", label: "Regularization Pending", icon: <RefreshCw size={18} /> }
  ].filter(Boolean);
  return (
    <div className="drawer-layer" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="drawer-head">
          <img className="drawer-logo" src={assets.logo} alt="DUX Digitech" />
          <button className="icon-btn" type="button" onClick={onClose} aria-label="Close menu"><X size={18} /></button>
        </div>
        <div className="drawer-list">
          {normal.map((item: any) => <button className={active === item.view ? "active" : ""} key={`${item.view}-${item.label}`} onClick={() => openView(item.view)}>{item.icon}<span>{item.label}</span></button>)}
        </div>
        {admin.length ? <div className="drawer-section"><b>Admin</b><div className="drawer-list">{admin.map((item: any) => <button className={active === item.view ? "active" : ""} key={`${item.view}-${item.label}`} onClick={() => openView(item.view)}>{item.icon}<span>{item.label}</span></button>)}</div></div> : null}
        <button className="btn btn-danger btn-wide drawer-logout" type="button" onClick={onLogout}><LogOut size={16} /> Logout</button>
      </aside>
    </div>
  );
}

function LiveCamera({
  image,
  onCapture,
  flash,
  title = "Keep your face inside the frame",
  captureRef,
  disabled = false,
  disabledMessage = "Please select employee first.",
  showControls = true,
  autoStopAfterCapture = true,
  hideWhenIdle = false,
  onActiveChange
}: any) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [status, setStatus] = useState("Keep your face inside the frame");

  const setCameraActive = (value: boolean) => {
    setActive(value);
    onActiveChange?.(value);
  };

  const stop = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setStarting(false);
    setStatus("Keep your face inside the frame");
    setCameraActive(false);
  };

  useEffect(() => {
    const stopFromApp = () => stop();
    window.addEventListener("jew-hrms-stop-camera", stopFromApp);
    return () => {
      window.removeEventListener("jew-hrms-stop-camera", stopFromApp);
      stop();
    };
  }, []);

  const start = async () => {
    if (disabled) {
      flash("Employee required", disabledMessage);
      return false;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      flash("Camera failed", "Camera is not available on this device.");
      return false;
    }
    setStarting(true);
    setStatus("Starting camera...");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: "user",
          width: { ideal: 720 },
          height: { ideal: 720 }
        },
        audio: false
      });
      streamRef.current = stream;
      setCameraActive(true);
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const video = videoRef.current;
      if (!video) throw new Error("camera_video_not_ready");
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;
      await new Promise<void>((resolve) => {
        if (video.readyState >= 2) return resolve();
        video.onloadedmetadata = () => resolve();
      });
      await video.play();
      setStatus("Face ready - keep your face inside the frame");
      return true;
    } catch (error: any) {
      const name = error?.name || "";
      const message = name === "NotAllowedError" || name === "PermissionDeniedError"
        ? "Camera permission denied. Please allow camera access."
        : "Camera is not available on this device.";
      stop();
      flash("Camera failed", message);
      return false;
    } finally {
      setStarting(false);
    }
  };

  const waitForVideo = async () => {
    const video = videoRef.current;
    if (!video) return false;
    for (let i = 0; i < 20; i += 1) {
      if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) return true;
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    return false;
  };

  const capture = (stopAfter = autoStopAfterCapture) => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !streamRef.current || video.videoWidth === 0 || video.videoHeight === 0) {
      flash("Camera", disabled ? disabledMessage : "Start camera before capturing face.");
      return "";
    }
    canvas.width = video.videoWidth || 720;
    canvas.height = video.videoHeight || 960;
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
    const nextImage = canvas.toDataURL("image/jpeg", 0.9);
    onCapture(nextImage);
    setStatus("Face captured. Save template or retake.");
    if (stopAfter) stop();
    return nextImage;
  };

  const startAndCapture = async () => {
    if (!streamRef.current) {
      const started = await start();
      if (!started) return "";
    }
    setStatus("Verifying face...");
    const ready = await waitForVideo();
    if (!ready) {
      flash("Camera", "Camera is still starting. Please try again.");
      return "";
    }
    return capture(true);
  };

  useEffect(() => {
    if (!captureRef) return;
    captureRef.current = startAndCapture;
    return () => {
      captureRef.current = null;
    };
  }, [active, captureRef, disabled]);

  if (hideWhenIdle && !active && !starting && !image) {
    return <canvas ref={canvasRef} hidden />;
  }

  return (
    <div>
      <div className="camera-box live">
        {active ? <><video ref={videoRef} autoPlay playsInline muted disablePictureInPicture /><div className="face-guide"><span /></div><div className="scan-status"><span className="status ok">{status}</span></div></> : image ? <img src={image} alt="Captured face" /> : <div><div className="camera-circle"><Camera size={36} /></div><b>{title}</b><span>{disabled ? disabledMessage : "Only one face allowed - Good lighting required"}</span></div>}
        <canvas ref={canvasRef} hidden />
      </div>
      <div className="pills mt"><span className={`status ${active ? "ok" : image ? "pending" : "draft"}`}>{active ? status : image ? "Face captured" : disabled ? "Select employee first" : "Camera ready"}</span></div>
      {showControls && <div className="btn-row mt">
        <button className="btn" type="button" disabled={starting || disabled} onClick={active ? stop : start}>{active ? "Stop Camera" : starting ? "Starting..." : "Start Camera"}</button>
        <button className="btn btn-cyan" type="button" disabled={!active || starting} onClick={() => capture()}>Capture Face</button>
        {image && <button className="btn" type="button" onClick={() => { onCapture(""); if (!disabled) start(); }}>Retake</button>}
      </div>}
    </div>
  );
}

function Dashboard({ data, open, caps, flash }: any) {
  const emp = data?.employee || {};
  const todayData = data?.today || {};
  const guardedOpen = (allowed: boolean, next: View, message: string) => {
    if (!allowed) {
      flash("Not configured", message);
      return;
    }
    open(next);
  };
  return <>
    <Hero eyebrow="TODAY OVERVIEW" title="Good morning," grad={emp.employee_name || "Employee"} text="Face and location checks protect every attendance punch." />
    <div className="metrics">
      <Metric label="Status" value={todayData.status || "Not Marked"} sub={todayData.date || today()} />
      <Metric label="Shift" value={data?.shift || "General"} sub="Assigned shift" />
      <Metric label="Face" value={data?.face_status?.registered ? "Ready" : "Pending"} sub="Server verified" />
      <Metric label="Locations" value={data?.location_status?.assigned || 0} sub="Assigned geofences" />
    </div>
    <div className="card accent card-pad btn-row"><button className="btn btn-primary" onClick={() => guardedOpen(caps.can_mark_attendance, "attendance", "Employee mapping is not configured. Please contact HR.")}>Mark Attendance</button><button className="btn" onClick={() => guardedOpen(caps.can_apply_leave, "leave", "Leave access is not configured. Please contact HR.")}>Apply Leave</button></div>
    <div className="list mt">
      <Action icon={<CalendarDays size={19} />} title="Attendance History" sub="Check monthly records" onClick={() => open("history")} />
      <Action icon={<CalendarDays size={19} />} title="Leave Management" sub="Apply and track leave" onClick={() => open("leave")} />
      <Action icon={<Bell size={19} />} title="Notifications" sub="Alerts and reminders" onClick={() => open("notifications")} />
    </div>
  </>;
}

function Metric({ label, value, sub }: any) {
  return <div className="card metric"><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="metric-sub">{sub}</div></div>;
}

function Action({ icon, title, sub, onClick }: any) {
  return <button className="list-item" onClick={onClick}><div className="tile-icon">{icon}</div><div><strong>{title}</strong><span>{sub}</span></div><ChevronRight size={18} /></button>;
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

  useEffect(() => {
    setStatus(initialStatus || null);
  }, [initialStatus]);

  useEffect(() => {
    if (!status) {
      call(API.getTodayAttendanceStatus).then(setStatus).catch((error) => flash("Unable to load", cleanErrorMessage(error)));
    }
  }, []);

  useEffect(() => {
    const tick = () => setNowText(new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, []);

  const requestPosition = (highAccuracy: boolean, timeout: number) => new Promise<GeolocationCoordinates>((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve(pos.coords),
      (err) => reject(err),
      { enableHighAccuracy: highAccuracy, timeout, maximumAge: 30000 }
    );
  });

  const getCurrentLocation = async (): Promise<GeolocationCoordinates> => {
    if (!navigator.geolocation) {
      throw new Error("Location is not available on this device.");
    }
    try {
      return await requestPosition(true, 15000);
    } catch (err: any) {
      // GeolocationPositionError.PERMISSION_DENIED === 1: the OS/browser blocked
      // location for this app. This is a device-location issue, NOT an account
      // permission problem, so keep the wording specific to location.
      if (err && err.code === 1) {
        throw new Error("Location is turned off for this app. Please enable location/GPS and allow access, then try again.");
      }
      // POSITION_UNAVAILABLE (2) / TIMEOUT (3): retry once with coarse accuracy.
      try {
        return await requestPosition(false, 20000);
      } catch {
        throw new Error("Could not get your location. Please turn on GPS/location and try again in an open area.");
      }
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
    stopVerifyCamera();
    autoSubmitRef.current = false;
    setVerifyType(null);
    setSubmitting(false);
    setCoords(null);
    setGpsStatus("Captured on submit");
    setCameraStatus("Starting camera...");
    setFaceStatus("Looking for face...");
  };

  useEffect(() => {
    const close = () => closeVerify();
    window.addEventListener("jew-hrms-close-attendance-verify", close);
    return () => {
      window.removeEventListener("jew-hrms-close-attendance-verify", close);
      stopVerifyCamera();
    };
  }, []);

  const startVerifyCamera = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Camera is not available on this device.");
    }
    setCameraStatus("Starting camera...");
    setFaceStatus("Looking for face...");
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: "user",
        width: { ideal: 720 },
        height: { ideal: 720 }
      },
      audio: false
    });
    streamRef.current = stream;
    onCameraActiveChange?.(true);
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) throw new Error("Camera is not ready.");
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    await new Promise<void>((resolve) => {
      if (video.readyState >= 2) return resolve();
      video.onloadedmetadata = () => resolve();
    });
    await video.play();
    for (let i = 0; i < 20; i += 1) {
      if (video.videoWidth > 0 && video.videoHeight > 0) break;
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    if (!video.videoWidth || !video.videoHeight) throw new Error("Camera is still starting. Please try again.");
    setCameraReady(true);
    setCameraStatus("Camera ready");
    setFaceStatus("Ready to capture");
  };

  useEffect(() => {
    if (!verifyType) return;
    let cancelled = false;
    autoSubmitRef.current = false;
    stopVerifyCamera();
    setCoords(null);
    setGpsStatus("Captured on submit");
    startVerifyCamera().catch((error) => {
      if (!cancelled) {
        setCameraStatus("Camera unavailable");
        setFaceStatus("Camera permission required");
        flash("Camera failed", cleanErrorMessage(error));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [verifyType]);

  // Single-tap flow: once the camera is ready, automatically capture the face,
  // grab GPS and submit — no second "Confirm" press. The ref + submitting guard
  // keep this to exactly one auto-submission per Mark In/Out.
  useEffect(() => {
    if (verifyType && cameraReady && !submitting && !autoSubmitRef.current) {
      autoSubmitRef.current = true;
      void confirmVerify();
    }
  }, [verifyType, cameraReady]);

  const captureCurrentFrame = () => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !streamRef.current || !video.videoWidth || !video.videoHeight) {
      throw new Error("Camera is still starting. Please try again.");
    }
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.9);
  };

  const openVerify = (type: "IN" | "OUT") => {
    if (type === "IN" && !status?.can_mark_in) {
      flash("Attendance", !faceReady ? "Face not registered. Please contact admin." : !locationReady ? "Location not assigned. Please contact admin." : "Mark In is not available now.");
      return;
    }
    if (type === "OUT" && !status?.can_mark_out) {
      flash("Attendance", !faceReady ? "Face not registered. Please contact admin." : !locationReady ? "Location not assigned. Please contact admin." : "Mark Out is not available now.");
      return;
    }
    setVerifyType(type);
  };

  const confirmVerify = async () => {
    if (!verifyType || submitting) return;
    setSubmitting(true);
    try {
      setFaceStatus("Face captured");
      const capturedImage = captureCurrentFrame();
      if (!capturedImage) {
        flash("Face required", "Please keep your full face inside the frame.");
        return;
      }
      setGpsStatus("Capturing GPS...");
      const currentCoords = await getCurrentLocation();
      setCoords(currentCoords);
      setGpsStatus("Captured");
      setFaceStatus("Verifying face");
      const result = await call(API.markAttendance, {
        type: verifyType,
        face_image: capturedImage,
        latitude: currentCoords.latitude,
        longitude: currentCoords.longitude,
        accuracy: currentCoords.accuracy,
        timestamp: new Date().toISOString()
      });
      await onMarked?.();
      if (result?.attendance_status) setStatus(result.attendance_status);
      flash("Attendance", result.message || toastText(result.code));
      closeVerify();
    } catch (error) {
      setFaceStatus(cleanErrorMessage(error));
      flash("Attendance failed", cleanErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const faceReady = status?.face_registered ?? status?.face_status?.registered ?? true;
  const locationReady = status?.location_assigned ?? ((status?.location_details?.assigned || status?.location_status?.assigned || 0) > 0);
  const nextType: "IN" | "OUT" = status?.status === "in" ? "OUT" : "IN";
  const canUseAction = nextType === "IN" ? Boolean(status?.can_mark_in) : Boolean(status?.can_mark_out);
  const actionLabel = nextType === "IN" ? "Mark In" : "Mark Out";
  const statusMessage = !faceReady
    ? "Face not registered. Please contact admin."
    : !locationReady
      ? "Location not assigned. Please contact admin."
      : "Ready for secure face and location attendance.";
  const employeeName = status?.employee_name || status?.employee || "Employee";
  const employeeId = status?.employee || status?.employee_id || "";
  const initials = employeeName.split(" ").filter(Boolean).slice(0, 2).map((part: string) => part[0]).join("").toUpperCase() || "JE";
  if (verifyType) {
    const actionName = verifyType === "IN" ? "Mark In" : "Mark Out";
    const confirmLabel = submitting ? "Verifying..." : !cameraReady ? "Starting camera..." : `Retry ${actionName}`;
    return (
      <>
        <div className="verify-stack">
          <div className="verify-title">
            <div className="eyebrow">ATTENDANCE VERIFY</div>
            <h1>Verify face and <span className="grad">mark attendance</span></h1>
          </div>
          <div className="verify-employee-card">
            <div className="avatar verify-avatar">{initials}</div>
            <div><strong>{employeeName}</strong><span>{employeeId}</span><b>IST {nowText}</b></div>
            <span className="verify-badge">{verifyType}</span>
          </div>
          <div className="verify-mini-card"><Clock3 size={18} /><div><b>Time</b><span>IST {nowText}</span></div></div>
          <div className="verify-mini-card"><MapPin size={18} /><div><b>GPS</b><span>{coords ? `${coords.latitude.toFixed(5)}, ${coords.longitude.toFixed(5)} (${Math.round(coords.accuracy || 0)} m)` : gpsStatus}</span></div></div>
          <div className="verify-camera">
            <video ref={videoRef} autoPlay muted playsInline />
            <canvas ref={canvasRef} hidden />
            <div className="dux-face-frame"><span /></div>
            <div className="face-quality-card"><ShieldCheck size={18} /><span>{faceStatus}</span></div>
          </div>
          <div className="verify-ready-card"><ShieldCheck size={20} /><div><b>Ready for ERPNext verification</b><span>Face and location verify and submit automatically.</span></div></div>
          <button className="btn btn-primary btn-wide verify-confirm" type="button" disabled={!cameraReady || submitting} onClick={() => { autoSubmitRef.current = true; void confirmVerify(); }}>
            {confirmLabel}
          </button>
          <div className="faint verify-camera-status">{cameraStatus}</div>
        </div>
      </>
    );
  }
  if (status?.status === "completed") {
    return <>
      <Hero eyebrow="MARK IN / OUT" title="Verify face and" grad="mark attendance" text="Attendance submits only after server face match and geofence validation." />
      <div className="card accent card-pad complete-card">
        <CheckCircle2 size={24} />
        <div>
          <h2>Attendance completed</h2>
          <div className="complete-grid">
            <span>In Time</span><b>{status.in_time_label || formatTime(status.in_time)}</b>
            <span>Out Time</span><b>{status.out_time_label || formatTime(status.out_time)}</b>
            <span>Working Hours</span><b>{status.working_hours || formatWorkingHours(status.in_time, status.out_time)}</b>
          </div>
        </div>
      </div>
    </>;
  }
  return <>
    <Hero eyebrow="MARK IN / OUT" title="Verify face and" grad="mark attendance" text="Attendance submits only after server face match and geofence validation." />
    <div className="card accent card-pad">
      <div className={`notice ${status?.status === "completed" ? "ok-notice" : ""}`}><CheckCircle2 size={18} /><div><b>{status?.status_label || "Loading today status"}</b><span>{statusMessage}</span></div></div>
      <div className="btn-row mt"><button className={`btn btn-wide ${nextType === "IN" ? "btn-primary" : "btn-cyan"}`} type="button" disabled={!canUseAction} onClick={() => openVerify(nextType)}>{actionLabel}</button></div>
    </div>
  </>;
}

function History({ data }: any) {
  return <><Hero eyebrow="ATTENDANCE HISTORY" title="Monthly" grad="records" text="Checkins and HRMS attendance records from the server." /><div className="card"><div className="card-head"><h2>Recent Checkins</h2></div><div className="card-pad list">{(data?.checkins || []).length ? data.checkins.map((row: any) => <div className="list-item" key={row.name}><div><strong>{row.log_type}</strong><span>{String(row.time)}</span></div><span className="status ok">Synced</span></div>) : <Empty text="No attendance records found." />}</div></div></>;
}

function Leave({ data, caps, flash, reload }: any) {
  const [form, setForm] = useState({ employee: "", leave_type: "", from_date: "", to_date: "", reason: "", half_day: false, half_day_date: "", half_day_type: "First Half" });
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const canSelectEmployee = Boolean(caps?.can_view_admin);
  const submit = async () => {
    if (!form.leave_type || !form.from_date || !form.to_date) {
      flash("Required fields", "Leave Type, From Date and To Date are required.");
      return;
    }
    if (form.from_date > form.to_date) {
      flash("Invalid dates", "From Date cannot be after To Date.");
      return;
    }
    if (form.half_day && !form.half_day_date) {
      flash("Required fields", "Half Day Date is required.");
      return;
    }
    const result = await runAction("submit-leave", async () => {
      const payload = canSelectEmployee ? form : { ...form, employee: undefined };
      const response = await call(API.applyLeave, payload);
      await reload();
      return response;
    }, { successTitle: "Leave submitted", errorTitle: "Leave failed" });
    if (result) setForm({ employee: "", leave_type: "", from_date: "", to_date: "", reason: "", half_day: false, half_day_date: "", half_day_type: "First Half" });
  };
  const cancelLeave = async (name: string) => {
    await runAction(`cancel-${name}`, async () => {
      await call(API.cancelLeave, { name });
      reload();
    }, { successTitle: "Leave cancelled", successMessage: "Leave request updated.", errorTitle: "Cancel failed" });
  };
  return <><Hero eyebrow="LEAVE MANAGEMENT" title="Apply and track" grad="leave requests" text="Submit leave applications and view approval status from HRMS." /><div className="card accent card-pad"><div className="grid-2">{canSelectEmployee && <Select label="Employee" value={form.employee} onChange={(v: string) => setForm({ ...form, employee: v })} options={(data.employees || []).map((e: any) => ({ value: e.name, label: e.employee_name || e.name }))} />}<Select label="Leave Type" value={form.leave_type} onChange={(v: string) => setForm({ ...form, leave_type: v })} options={(data.types || []).map((t: any) => ({ value: t.name, label: t.leave_type_name || t.name }))} /><Field label="From Date" type="date" value={form.from_date} onChange={(v: string) => setForm({ ...form, from_date: v })} /><Field label="To Date" type="date" value={form.to_date} onChange={(v: string) => setForm({ ...form, to_date: v })} /><label className="check"><input type="checkbox" checked={form.half_day} onChange={(e) => setForm({ ...form, half_day: e.target.checked })} /> Half day</label>{form.half_day && <Field label="Half Day Date" type="date" value={form.half_day_date} onChange={(v: string) => setForm({ ...form, half_day_date: v })} />}{form.half_day && <Select label="Half Day Type" value={form.half_day_type} onChange={(v: string) => setForm({ ...form, half_day_type: v })} options={["First Half", "Second Half"]} />}</div><div className="field"><label>Reason</label><textarea className="textarea" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></div><button className="btn btn-primary btn-wide" type="button" disabled={isAnyBusy} onClick={submit}>{isBusy("submit-leave") ? "Submitting..." : "Submit Leave"}</button></div><div className="card mt"><div className="card-head"><h2>My Leaves</h2></div><div className="card-pad list">{(data.leaves || []).length ? data.leaves.map((l: any) => { const status = l.approval_status || l.jew_hrms_approval_status || l.status; return <div className="list-item stack" key={l.name}><div><strong>{l.leave_type}</strong><span>{l.from_date} to {l.to_date}{l.half_day ? " | Half Day" : ""}</span></div><div className="btn-row"><span className={`status ${status === "Approved" ? "ok" : status === "Rejected" || status === "Cancelled" ? "err" : "pending"}`}>{status}</span>{!["Approved", "Rejected", "Cancelled"].includes(status) && <button className="btn btn-danger" type="button" disabled={isAnyBusy} onClick={() => cancelLeave(l.name)}>{isBusy(`cancel-${l.name}`) ? "Cancelling..." : "Cancel"}</button>}</div></div>; }) : <Empty text="No leave requests yet." />}</div></div></>;
}

function Admin({ open, caps }: any) {
  return <><Hero eyebrow="ADMIN ACCESS" title="Manage face," grad="locations and leave" text="Role-based admin tools for HR and authorized approvers only." /><div className="card accent"><div className="card-head"><h2>Admin Modules</h2><span className="status ok">Allowed</span></div><div className="card-pad list">{caps.can_register_face && <Action icon={<Camera size={19} />} title="Face Register / Update" sub="Select employee and capture face" onClick={() => open("face")} />}{caps.can_manage_locations && <Action icon={<MapPin size={19} />} title="Location & Geofence" sub="Create locations and assign radius" onClick={() => open("location")} />}{caps.can_approve_leave && <Action icon={<CheckCircle2 size={19} />} title="Leave Approval" sub="Approve or reject requests" onClick={() => open("leaveApproval")} />}{caps.can_manage_leave_policy && <Action icon={<CalendarDays size={19} />} title="Leave Type / Leave Policy" sub="Configure CL, PL, SL and LWP" onClick={() => open("leavePolicy")} />}{caps.can_manage_shift_policy && <Action icon={<Clock3 size={19} />} title="Shift & Attendance Policy" sub="Late, early and short-hours rules" onClick={() => open("shiftPolicy")} />}{caps.can_manage_regularization && <Action icon={<RefreshCw size={19} />} title="Regularization Pending" sub="Resolve missing out and policy exceptions" onClick={() => open("regularization")} />}{caps.can_view_admin && <Action icon={<Users size={19} />} title="Employee List" sub="Face/location status overview" onClick={() => open("employees")} />}</div></div></>;
}

function FaceAdmin({ employees, flash, selectedEmployee, onCameraActiveChange }: any) {
  const [employee, setEmployee] = useState(selectedEmployee || "");
  const [image, setImage] = useState("");
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  useEffect(() => {
    if (selectedEmployee) setEmployee(selectedEmployee);
  }, [selectedEmployee]);
  const selected = (employees || []).find((item: any) => item.name === employee);
  const save = async () => {
    if (!employee || !image) {
      flash("Required fields", "Select employee and capture face before saving.");
      return;
    }
    await runAction("save-face", async () => {
      await call(API.registerEmployeeFace, { employee, face_image: image });
      setImage("");
    }, { successTitle: "Face registered", successMessage: "Server template saved for employee.", errorTitle: "Face failed" });
  };
  return <><Hero eyebrow="FACE ENROLLMENT" title="Register employee" grad="face template" text="Only authorized admin users can capture or update employee face data." /><div className="card accent card-pad"><Select label="Employee" value={employee} onChange={(value: string) => { setEmployee(value); setImage(""); window.dispatchEvent(new Event("jew-hrms-stop-camera")); }} options={(employees || []).map((e: any) => ({ value: e.name, label: e.label || e.employee_name || e.name, description: e.description || e.name }))} />{selected && <div className="notice"><UserRound size={18} /><div><b>{selected.employee_name || selected.name}</b><span>{selected.name} - {selected.face_registered ? `Registered${selected.face_last_updated_on ? ` - ${selected.face_last_updated_on}` : ""}` : "Not Registered"}</span></div></div>}<div className="mt"><LiveCamera image={image} onCapture={setImage} flash={flash} title="Keep your face inside the frame" disabled={!employee} disabledMessage="Please select employee first." onActiveChange={onCameraActiveChange} /></div><button className="btn btn-primary btn-wide mt" type="button" disabled={isAnyBusy || !employee || !image} onClick={save}>{isBusy("save-face") ? "Saving..." : "Save Face Template"}</button>{!(employees || []).length && <div className="notice mt"><HelpCircle size={18} /><div><b>No employees loaded</b><span>Open this screen from Admin after employee list loads.</span></div></div>}</div></>;
}

function LocationAdmin({ data, setData, flash, reload }: any) {
  const [form, setForm] = useState({ location_name: "", latitude: 21.1458, longitude: 79.0882, default_radius_meter: 100 });
  const [mapCenter, setMapCenter] = useState<[number, number]>([21.1458, 79.0882]);
  const [employee, setEmployee] = useState("");
  const [location, setLocation] = useState("");
  const [employeeLocations, setEmployeeLocations] = useState<any[]>([]);
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const employeeOptions = (data.employees || []).map((e: any) => ({ value: e.name, label: e.label || e.employee_name || e.name, description: e.description || e.name }));
  const locationOptions = (data.locations || []).map((l: any) => ({ value: l.name, label: l.location_name || l.name, description: l.name }));
  const loadEmployeeLocations = async (employeeName: string) => {
    setEmployee(employeeName);
    setEmployeeLocations([]);
    if (!employeeName) return;
    const res = await runAction(`load-locations-${employeeName}`, () => call(API.getEmployeeLocations, { employee: employeeName }), { errorTitle: "Assignments failed" });
    if (res) setEmployeeLocations(res.locations || []);
  };
  const useCurrentLocation = async () => {
    await runAction("use-current-location", async () => {
      if (!navigator.geolocation) throw new Error("Location is not available on this device.");
      const position = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, () => reject(new Error("Could not get your location. Please turn on GPS/location and try again.")), {
          enableHighAccuracy: true,
          timeout: 15000,
          maximumAge: 0
        });
      });
      const latitude = Number(position.coords.latitude.toFixed(7));
      const longitude = Number(position.coords.longitude.toFixed(7));
      setForm((current) => ({ ...current, latitude, longitude }));
      setMapCenter([latitude, longitude]);
      return position;
    }, { successTitle: "Location", successMessage: "Current location captured.", errorTitle: "Location failed" });
  };
  const save = async () => {
    if (!form.location_name || form.latitude === null || form.longitude === null || !form.default_radius_meter) {
      flash("Required fields", "Location name, latitude, longitude and radius are required.");
      return;
    }
    await runAction("save-location", async () => {
      await call(API.saveLocation, form);
      reload();
    }, { successTitle: "Location saved", successMessage: "Geofence updated.", errorTitle: "Location failed" });
  };
  const assign = async () => {
    if (!employee || !location) {
      flash("Required fields", "Select employee and location before assigning.");
      return;
    }
    await runAction("assign-location", async () => {
      const selectedLocation = (data.locations || []).find((item: any) => item.name === location);
      const res = await call(API.assignEmployeeLocation, { employee, location, radius_meter: selectedLocation?.default_radius_meter });
      setEmployeeLocations((current) => {
        const nextAssignment = {
          assignment: res.assignment,
          name: selectedLocation?.name || location,
          location_name: selectedLocation?.location_name || location,
          radius_meter: Number(selectedLocation?.default_radius_meter || res.radius_meter || 100)
        };
        return [nextAssignment, ...current.filter((item) => item.name !== location && item.location !== location)];
      });
    }, { successTitle: "Assigned", successMessage: "Location assigned successfully.", errorTitle: "Assign failed" });
  };
  const deleteLocation = async (name: string) => {
    await runAction(`delete-location-${name}`, async () => {
      await call(API.deleteLocation, { name });
      if (location === name) setLocation("");
      await reload();
    }, { successTitle: "Location deleted", successMessage: "Geofence removed.", errorTitle: "Delete failed" });
  };
  const removeAssignment = async (assignment: string) => {
    await runAction(`remove-assignment-${assignment}`, async () => {
      await call(API.removeEmployeeLocation, { assignment });
      if (employee) {
        const res = await call(API.getEmployeeLocations, { employee });
        setEmployeeLocations(res.locations || []);
      }
      await reload();
    }, { successTitle: "Assignment removed", successMessage: "Employee location assignment removed.", errorTitle: "Remove failed" });
  };
  return <><Hero eyebrow="GEOFENCE" title="Assign employee" grad="locations" text="Create allowed attendance locations with latitude, longitude and radius." /><div className="card accent card-pad"><div className="map-box real-map"><MapContainer center={mapCenter} zoom={13} scrollWheelZoom={false}><MapRecenter center={mapCenter} /><TileLayer attribution="OpenStreetMap" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" /><PickLocation onPick={(lat, lng) => { setForm({ ...form, latitude: lat, longitude: lng }); setMapCenter([lat, lng]); }} /><Marker position={[form.latitude, form.longitude]} /><Circle center={[form.latitude, form.longitude]} radius={form.default_radius_meter} /></MapContainer></div><Field label="Location Name" value={form.location_name} onChange={(v: string) => setForm({ ...form, location_name: v })} /><div className="grid-2"><Field label="Latitude" type="number" value={form.latitude} onChange={(v: string) => { const latitude = Number(v); setForm({ ...form, latitude }); setMapCenter([latitude, form.longitude]); }} /><Field label="Longitude" type="number" value={form.longitude} onChange={(v: string) => { const longitude = Number(v); setForm({ ...form, longitude }); setMapCenter([form.latitude, longitude]); }} /></div><Field label="Radius Meter" type="number" value={form.default_radius_meter} onChange={(v: string) => setForm({ ...form, default_radius_meter: Number(v) })} /><button className="btn btn-cyan btn-wide" type="button" disabled={isAnyBusy} onClick={useCurrentLocation}>{isBusy("use-current-location") ? "Capturing..." : "Use Current Location"}</button><button className="btn btn-primary btn-wide mt" type="button" disabled={isAnyBusy || !form.location_name || !form.latitude || !form.longitude || !form.default_radius_meter} onClick={save}>{isBusy("save-location") ? "Saving..." : "Save Location"}</button></div><div className="card mt card-pad"><div className="grid-2"><Select label="Employee" value={employee} onChange={loadEmployeeLocations} options={employeeOptions} /><Select label="Location" value={location} onChange={setLocation} options={locationOptions} /></div><button className="btn btn-cyan btn-wide" type="button" disabled={isAnyBusy || !employee || !location} onClick={assign}>{isBusy("assign-location") ? "Assigning..." : "Assign Location"}</button></div><div className="card mt"><div className="card-head"><h2>Saved Locations</h2></div><div className="card-pad list">{(data.locations || []).length ? data.locations.map((item: any) => <div className="list-item stack" key={item.name}><div><strong>{item.location_name || item.name}</strong><span>{item.latitude}, {item.longitude} | {item.default_radius_meter} m</span></div><button className="btn btn-danger" type="button" disabled={isAnyBusy} onClick={() => deleteLocation(item.name)}>{isBusy(`delete-location-${item.name}`) ? "Deleting..." : "Delete Location"}</button></div>) : <Empty text="No saved locations." />}</div></div><div className="card mt"><div className="card-head"><h2>Employee Assignments</h2></div><div className="card-pad list">{employee ? (employeeLocations.length ? employeeLocations.map((item: any) => <div className="list-item stack" key={item.assignment || item.name}><div><strong>{item.location_name || item.name}</strong><span>{item.radius_meter} m radius</span></div><button className="btn btn-danger" type="button" disabled={isAnyBusy} onClick={() => removeAssignment(item.assignment)}>{isBusy(`remove-assignment-${item.assignment}`) ? "Removing..." : "Remove Assignment"}</button></div>) : <Empty text="No active assignments for selected employee." />) : <Empty text="Select employee to view assignments." />}</div></div></>;
}

function LeavePolicy({ types, flash, reload }: any) {
  const [form, setForm] = useState({ name: "", leave_type_name: "", is_lwp: false });
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const save = async () => {
    if (!form.name && !form.leave_type_name) {
      flash("Required fields", "Leave type is required.");
      return;
    }
    await runAction("save-leave-type", async () => {
      await call(API.saveLeaveType, form);
      await reload();
      setForm({ name: "", leave_type_name: "", is_lwp: false });
    }, { successTitle: "Leave type saved", errorTitle: "Save failed" });
  };
  return <><Hero eyebrow="LEAVE POLICY" title="Configure" grad="leave types" text="HR/Admin managed leave types used by employee leave apply." /><div className="card accent card-pad"><div className="grid-2"><Field label="Leave Type Code" value={form.name} onChange={(v: string) => setForm({ ...form, name: v.toUpperCase() })} /><Field label="Leave Type Name" value={form.leave_type_name} onChange={(v: string) => setForm({ ...form, leave_type_name: v })} /><label className="check"><input type="checkbox" checked={form.is_lwp} onChange={(e) => setForm({ ...form, is_lwp: e.target.checked })} /> LWP</label></div><button className="btn btn-primary btn-wide" type="button" disabled={isAnyBusy} onClick={save}>{isBusy("save-leave-type") ? "Saving..." : "Save Leave Type"}</button></div><div className="card mt"><div className="card-head"><h2>Configured Leave Types</h2></div><div className="card-pad list">{types.length ? types.map((t: any) => <div className="list-item" key={t.name}><div><strong>{t.name}</strong><span>{t.leave_type_name || t.name}</span></div><span className={`status ${t.is_lwp ? "pending" : "ok"}`}>{t.is_lwp ? "LWP" : "Paid"}</span></div>) : <Empty text="No leave types configured." />}</div></div></>;
}

function ShiftPolicy({ policies, flash, reload }: any) {
  const [form, setForm] = useState<any>({ shift_name: "General", shift_start_time: "09:30:00", shift_end_time: "18:30:00", break_minutes: 60, full_day_minimum_hours: 8, half_day_minimum_hours: 4, late_coming_grace_minutes: 10, early_going_grace_minutes: 10, max_late_coming_allowed_per_month: 0, max_early_going_allowed_per_month: 0, max_short_hours_allowed_per_month: 0, action_after_late_limit: "Regularization Required", action_after_early_limit: "Regularization Required", action_after_short_hours_limit: "Regularization Required", is_active: 1 });
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const actionOptions = ["Warn Only", "Regularization Required", "Mark Half Day", "Mark LWP", "Block Attendance"];
  const edit = (policy: any) => setForm({ ...form, ...policy });
  const save = async () => {
    await runAction("save-shift-policy", async () => {
      await call(API.saveShiftPolicy, form);
      await reload();
    }, { successTitle: "Policy saved", errorTitle: "Policy failed" });
  };
  return <><Hero eyebrow="SHIFT POLICY" title="Late, early and" grad="short-hours rules" text="Configurable attendance policy for HR/Admin." /><div className="card accent card-pad"><div className="grid-2"><Field label="Shift Name" value={form.shift_name} onChange={(v: string) => setForm({ ...form, shift_name: v })} /><Field label="Shift Start" type="time" value={String(form.shift_start_time || "").slice(0, 5)} onChange={(v: string) => setForm({ ...form, shift_start_time: v })} /><Field label="Shift End" type="time" value={String(form.shift_end_time || "").slice(0, 5)} onChange={(v: string) => setForm({ ...form, shift_end_time: v })} /><Field label="Break Minutes" type="number" value={form.break_minutes} onChange={(v: string) => setForm({ ...form, break_minutes: Number(v) })} /><Field label="Full Day Hours" type="number" value={form.full_day_minimum_hours} onChange={(v: string) => setForm({ ...form, full_day_minimum_hours: Number(v) })} /><Field label="Half Day Hours" type="number" value={form.half_day_minimum_hours} onChange={(v: string) => setForm({ ...form, half_day_minimum_hours: Number(v) })} /><Field label="Late Grace Min" type="number" value={form.late_coming_grace_minutes} onChange={(v: string) => setForm({ ...form, late_coming_grace_minutes: Number(v) })} /><Field label="Early Grace Min" type="number" value={form.early_going_grace_minutes} onChange={(v: string) => setForm({ ...form, early_going_grace_minutes: Number(v) })} /><Field label="Max Late / Month" type="number" value={form.max_late_coming_allowed_per_month} onChange={(v: string) => setForm({ ...form, max_late_coming_allowed_per_month: Number(v) })} /><Field label="Max Early / Month" type="number" value={form.max_early_going_allowed_per_month} onChange={(v: string) => setForm({ ...form, max_early_going_allowed_per_month: Number(v) })} /><Field label="Max Short / Month" type="number" value={form.max_short_hours_allowed_per_month} onChange={(v: string) => setForm({ ...form, max_short_hours_allowed_per_month: Number(v) })} /><Select label="Late Action" value={form.action_after_late_limit} onChange={(v: string) => setForm({ ...form, action_after_late_limit: v })} options={actionOptions} /><Select label="Early Action" value={form.action_after_early_limit} onChange={(v: string) => setForm({ ...form, action_after_early_limit: v })} options={actionOptions} /><Select label="Short Hours Action" value={form.action_after_short_hours_limit} onChange={(v: string) => setForm({ ...form, action_after_short_hours_limit: v })} options={actionOptions} /><label className="check"><input type="checkbox" checked={Boolean(Number(form.is_active))} onChange={(e) => setForm({ ...form, is_active: e.target.checked ? 1 : 0 })} /> Active</label></div><button className="btn btn-primary btn-wide" type="button" disabled={isAnyBusy} onClick={save}>{isBusy("save-shift-policy") ? "Saving..." : "Save Policy"}</button></div><div className="card mt"><div className="card-head"><h2>Policies</h2></div><div className="card-pad list">{policies.length ? policies.map((p: any) => <button className="list-item" key={p.name} type="button" onClick={() => edit(p)}><div><strong>{p.shift_name}</strong><span>{String(p.shift_start_time).slice(0, 5)} to {String(p.shift_end_time).slice(0, 5)}</span></div><span className={`status ${p.is_active ? "ok" : "pending"}`}>{p.is_active ? "Active" : "Inactive"}</span></button>) : <Empty text="No shift policy configured." />}</div></div></>;
}

function Regularization({ items, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decide = async (name: string, action: string) => {
    const remarks = action === "Rejected" ? window.prompt("Reject reason") || "" : window.prompt("Remarks") || "";
    await runAction(`${action}-${name}`, async () => {
      await call(API.decideRegularization, { name, action, remarks });
      await reload();
    }, { successTitle: "Updated", successMessage: "Regularization updated.", errorTitle: "Update failed" });
  };
  return <><Hero eyebrow="REGULARIZATION" title="Pending" grad="attendance review" text="Review missing mark out, late, early and short-hours cases." /><div className="card"><div className="card-pad list">{items.length ? items.map((r: any) => <div className="list-item stack" key={r.name}><div><strong>{r.employee}</strong><span>{r.attendance_date} | {r.issue_type} | {r.policy_action || "Review"}</span></div><div className="btn-row"><button className="btn btn-cyan" type="button" disabled={isAnyBusy} onClick={() => decide(r.name, "Approved as Present")}>{isBusy(`Approved as Present-${r.name}`) ? "Saving..." : "Present"}</button><button className="btn" type="button" disabled={isAnyBusy} onClick={() => decide(r.name, "Marked Half Day")}>Half Day</button><button className="btn btn-danger" type="button" disabled={isAnyBusy} onClick={() => decide(r.name, "Rejected")}>Reject</button></div></div>) : <Empty text="No pending regularization." />}</div></div></>;
}

function LeaveApproval({ leaves, flash, reload }: any) {
  const { runAction, isBusy, isAnyBusy } = useActionRunner(flash);
  const decide = async (name: string, approve: boolean) => {
    const remarks = approve ? "" : window.prompt("Rejection remarks") || "";
    if (!approve && !remarks.trim()) {
      flash("Reject Reason required", "Reject Reason required.");
      return;
    }
    await runAction(`${approve ? "approve" : "reject"}-${name}`, async () => {
      await call(approve ? API.approveLeave : API.rejectLeave, { name, remarks });
      reload();
    }, { successTitle: approve ? "Approved" : "Rejected", successMessage: "Leave request updated.", errorTitle: approve ? "Approve failed" : "Reject failed" });
  };
  return <><Hero eyebrow="APPROVAL QUEUE" title="Review pending" grad="leave requests" text="Approve or reject employee leave requests with remarks." /><div className="card"><div className="card-pad list">{leaves.length ? leaves.map((l: any) => { const status = l.approval_status || l.jew_hrms_approval_status || l.status; return <div className="list-item stack" key={l.name}><div><strong>{l.employee_name || l.employee}</strong><span>{l.leave_type} | {l.from_date} to {l.to_date}{l.half_day ? " | Half Day" : ""}</span></div><div className="btn-row"><span className="status pending">{status}</span><button className="btn btn-cyan" type="button" disabled={isAnyBusy} onClick={() => decide(l.name, true)}>{isBusy(`approve-${l.name}`) ? "Approving..." : status === "Pending Admin Approval" ? "Final Approve" : "Approve"}</button><button className="btn btn-danger" type="button" disabled={isAnyBusy} onClick={() => decide(l.name, false)}>{isBusy(`reject-${l.name}`) ? "Rejecting..." : "Reject"}</button></div></div>; }) : <Empty text="No pending leave requests." />}</div></div></>;
}

function Employees({ employees, open, selectEmployee }: any) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState<any>(null);
  const filtered = (employees || []).filter((employee: any) => {
    const haystack = `${employee.name} ${employee.employee_name || ""} ${employee.department || ""} ${employee.designation || ""}`.toLowerCase();
    return haystack.includes(query.toLowerCase());
  });
  const openFace = (employee: any) => {
    selectEmployee(employee.name);
    open("face");
  };
  return <><Hero eyebrow="EMPLOYEES" title="Admin" grad="overview" text="Face and location setup status for active employees." /><div className="card accent card-pad"><div className="field"><label>Search</label><input className="input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search employee name, ID, department" /></div></div><div className="card mt"><div className="card-pad list">{filtered.length ? filtered.map((e: any) => <div className="list-item stack" key={e.name} onClick={() => setActive(active?.name === e.name ? null : e)}><div className="profile-line"><div className="avatar">{(e.employee_name || e.name).slice(0, 1)}</div><div><strong>{e.employee_name || e.name}</strong><span>{e.department || e.designation || e.name}</span></div></div><div className="pills"><span className={`status ${e.face_registered ? "ok" : "pending"}`}>Face</span><span className={`status ${e.location_count ? "ok" : "pending"}`}>Location</span></div>{active?.name === e.name && <div className="notice"><UserRound size={18} /><div><b>{e.name}</b><span>{e.designation || "Employee"} {e.user_id ? `| ${e.user_id}` : ""}</span><button className="btn btn-primary mt" type="button" onClick={(event) => { event.stopPropagation(); openFace(e); }}>Face Register</button></div></div>}</div>) : <Empty text="No employee data loaded." />}</div></div></>;
}

function Notifications({ items }: any) {
  return <><Hero eyebrow="NOTIFICATIONS" title="Alerts and" grad="reminders" text="In-app alerts now; Firebase push can be added later." /><div className="list">{items.length ? items.map((n: any, i: number) => <div className="notice" key={i}><Bell size={18} /><div><b>{n.title}</b><span>{n.message}</span></div></div>) : <Empty text="No notifications." />}</div></>;
}

function Profile({ profile, open, onLogout }: any) {
  const [loggingOut, setLoggingOut] = useState(false);
  const doLogout = async () => {
    setLoggingOut(true);
    try {
      await onLogout();
    } finally {
      setLoggingOut(false);
    }
  };
  return <><Hero eyebrow="MY PROFILE" title="Employee" grad="profile" text="Your HRMS identity, shift, face status and assigned locations." /><div className="card accent card-pad"><div className="profile-line"><UserRound size={42} /><div><h2>{profile?.employee_name || "Employee"}</h2><p>{profile?.designation || profile?.department || profile?.employee}</p></div></div><div className="pills mt"><span className="pill">{profile?.status || "Active"}</span><span className="pill">{profile?.default_shift || "General Shift"}</span><span className="pill">{profile?.face_status?.registered ? "Face Ready" : "Face Pending"}</span></div><button className="btn btn-danger btn-wide mt" type="button" disabled={loggingOut} onClick={doLogout}><LogOut size={16} /> {loggingOut ? "Logging out..." : "Logout"}</button><button className="btn btn-wide mt" type="button" onClick={() => open("settings")}><HelpCircle size={16} /> Settings / Help</button></div></>;
}

function Settings({ theme, setTheme, onLogout }: any) {
  const [loggingOut, setLoggingOut] = useState(false);
  const doLogout = async () => {
    setLoggingOut(true);
    try {
      await onLogout();
    } finally {
      setLoggingOut(false);
    }
  };
  return <><Hero eyebrow="SETTINGS" title="App" grad="support" text="Thin APK mode, hosted by the JEW Frappe site." /><div className="card card-pad list"><div className="notice"><ShieldCheck size={18} /><div><b>Thin APK</b><span>Loads https://jewipl.duxdigitech.in/jew-hrms/m</span></div></div><div className="notice"><Camera size={18} /><div><b>Permissions</b><span>Camera and location are used only for attendance.</span></div></div><button className="btn btn-wide" type="button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? "Light Mode" : "Dark Mode"}</button><button className="btn btn-danger btn-wide" type="button" disabled={loggingOut} onClick={doLogout}>{loggingOut ? "Logging out..." : "Logout"}</button><div className="notice"><HelpCircle size={18} /><div><b>Help / Support</b><span>Please contact HR or DUX Digitech support for app access, face registration, and location assignment.</span></div></div></div></>;
}

function Field({ label, value, onChange, type = "text" }: any) {
  return <div className="field"><label>{label}</label><input className="input" type={type} value={value ?? ""} onChange={(e) => onChange(e.target.value)} /></div>;
}

function Select({ label, value, onChange, options }: any) {
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
  const selected = normalized.find((option: any) => option.value === value);
  const filtered = normalized.filter((option: any) => {
    const haystack = `${option.label} ${option.description || ""} ${option.value}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  });

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => searchRef.current?.focus(), 50);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = (nextValue: string) => {
    onChange(nextValue);
    setOpen(false);
    setQuery("");
  };

  return <div className="field searchable-field"><label>{label}</label><button className="select select-trigger" type="button" onClick={() => setOpen(true)}><span>{selected?.text || "Select"}</span><ChevronRight size={16} /></button>{open && createPortal(
    <div className="select-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
      <div className="select-sheet" role="dialog" aria-modal="true" aria-label={label}>
        <div className="select-sheet-head"><strong>{label}</strong><button className="icon-btn" type="button" aria-label="Close" onClick={() => setOpen(false)}><X size={18} /></button></div>
        <input ref={searchRef} className="input select-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${label.toLowerCase()}`} />
        <div className="select-options">
          <button className={`select-option ${!value ? "active" : ""}`} type="button" onClick={() => choose("")}>Select</button>
          {filtered.length ? filtered.map((option: any) => <button className={`select-option ${option.value === value ? "active" : ""}`} type="button" key={option.value} onClick={() => choose(option.value)}><span>{option.label}</span>{option.description && <small>{option.description}</small>}</button>) : <div className="select-empty">No results found.</div>}
        </div>
      </div>
    </div>,
    document.body
  )}</div>;
}

function Empty({ text }: { text: string }) {
  return <div className="empty"><XCircle size={20} /><span>{text}</span></div>;
}

function today() {
  return new Date().toISOString().slice(0, 10);
}
