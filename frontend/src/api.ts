export const API = {
  login: "jew_hrms_mobile.api.login",
  getSessionUser: "jew_hrms_mobile.api.get_session_user",
  capabilities: "jew_hrms_mobile.api.capabilities",
  getEmployeeProfile: "jew_hrms_mobile.api.get_employee_profile",
  getDashboard: "jew_hrms_mobile.api.get_dashboard",
  getTodayAttendanceStatus: "jew_hrms_mobile.api.get_today_attendance_status",
  markAttendance: "jew_hrms_mobile.api.mark_attendance",
  getAttendanceHistory: "jew_hrms_mobile.api.get_attendance_history",
  getFaceStatus: "jew_hrms_mobile.api.get_face_status",
  registerEmployeeFace: "jew_hrms_mobile.api.register_employee_face",
  updateEmployeeFace: "jew_hrms_mobile.api.update_employee_face",
  getLocations: "jew_hrms_mobile.api.get_locations",
  saveLocation: "jew_hrms_mobile.api.save_location",
  deleteLocation: "jew_hrms_mobile.api.delete_location",
  assignEmployeeLocation: "jew_hrms_mobile.api.assign_employee_location",
  removeEmployeeLocation: "jew_hrms_mobile.api.remove_employee_location",
  getEmployeeLocations: "jew_hrms_mobile.api.get_employee_locations",
  getLeaveDashboard: "jew_hrms_mobile.api.get_leave_dashboard",
  getLeaveTypes: "jew_hrms_mobile.api.get_leave_types",
  applyLeave: "jew_hrms_mobile.api.apply_leave",
  getMyLeaves: "jew_hrms_mobile.api.get_my_leaves",
  cancelLeave: "jew_hrms_mobile.api.cancel_leave",
  getPendingLeaves: "jew_hrms_mobile.api.get_pending_leaves",
  approveLeave: "jew_hrms_mobile.api.approve_leave",
  rejectLeave: "jew_hrms_mobile.api.reject_leave",
  getNotifications: "jew_hrms_mobile.api.get_notifications",
  getEmployeeList: "jew_hrms_mobile.api.get_employee_list",
  getLeavePolicy: "jew_hrms_mobile.api.get_leave_policy",
  saveLeaveType: "jew_hrms_mobile.api.save_leave_type",
  getShiftPolicies: "jew_hrms_mobile.api.get_shift_policies",
  saveShiftPolicy: "jew_hrms_mobile.api.save_shift_policy",
  getRegularizations: "jew_hrms_mobile.api.get_regularizations",
  decideRegularization: "jew_hrms_mobile.api.decide_regularization"
} as const;

type Params = Record<string, unknown>;

const FALLBACK_ERROR = "Something went wrong. Please try again.";

declare global {
  interface Window {
    frappe?: { csrf_token?: string };
  }
}

export async function call<T = any>(method: string, params: Params = {}): Promise<T> {
  const form = new FormData();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) form.append(key, typeof value === "object" ? JSON.stringify(value) : String(value));
  });

  const response = await fetch(`/api/method/${method}`, {
    method: "POST",
    credentials: "include",
    headers: window.frappe?.csrf_token ? { "X-Frappe-CSRF-Token": window.frappe.csrf_token } : {},
    body: form
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(cleanErrorMessage(data) || response.statusText || FALLBACK_ERROR);
  }
  const payload = data.message ?? data;
  if (payload && typeof payload === "object" && (payload.ok === false || payload.success === false)) {
    throw new Error(cleanErrorMessage(payload));
  }
  return payload;
}

export async function logout() {
  const response = await fetch("/api/method/logout", {
    method: "POST",
    credentials: "include",
    headers: window.frappe?.csrf_token ? { "X-Frappe-CSRF-Token": window.frappe.csrf_token } : {}
  });
  if (!response.ok) {
    await fetch("/?cmd=web_logout", { credentials: "include" }).catch(() => undefined);
  }
}

export function cleanErrorMessage(error: unknown): string {
  const raw = normalizeError(error);
  if (!raw) return FALLBACK_ERROR;
  if (raw.includes("No Employee is mapped") || raw.includes("Employee is mapped")) {
    return "Employee not linked to current user.";
  }
  if (raw.includes("TypeError") && raw.includes("mark_attendance")) {
    return "Unable to create Employee Checkin. Please contact admin.";
  }
  if (raw.toLowerCase().includes("permission")) {
    return "You do not have permission.";
  }
  if (raw.includes("Traceback") || raw.includes("_server_messages")) return FALLBACK_ERROR;
  return raw.replace(/^frappe\.exceptions\.[A-Za-z]+:\s*/, "").trim() || FALLBACK_ERROR;
}

function normalizeError(error: unknown): string {
  if (!error) return "";
  if (typeof error === "string") return parseServerMessages(error);
  if (error instanceof Error) return parseServerMessages(error.message);
  if (typeof error === "object") {
    const value = error as any;
    return parseServerMessages(value.message || value._server_messages || value.exception || value.error || "");
  }
  return String(error);
}

function parseServerMessages(value: unknown): string {
  if (!value) return "";
  if (typeof value !== "string") {
    if (typeof value === "object") return parseServerMessages((value as any).message || (value as any).title || "");
    return String(value);
  }
  let text = value.trim();
  for (let i = 0; i < 2; i += 1) {
    if (!text || !["[", "{"].includes(text[0])) break;
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) {
        text = parsed.map((item) => parseServerMessages(item)).filter(Boolean).join(" ");
      } else {
        text = parseServerMessages(parsed.message || parsed.title || parsed._server_messages || "");
      }
    } catch {
      break;
    }
  }
  return text.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}
