// Seamless OTA auto-update for the thin-shell app.
//
// The app loads the hosted web build (jewipl.duxdigitech.in/jew-hrms/m). The host
// page is no-cache and references HASHED asset bundles, so reloading always pulls the
// latest deployed frontend, and every backend rule/workflow/leave-approval change is
// already read live from the API. The only gap was that a running app kept the old
// frontend until a full close+reopen. This checks on resume / focus / periodically
// whether a newer build was deployed and reloads once — so bench + web changes reach
// the app automatically, no force-close or APK reinstall. It never reloads while the
// attendance camera is live (that would interrupt a punch).

const loadedJs = (() => {
  const s = document.querySelector('script[type="module"][src]') as HTMLScriptElement | null;
  const m = s && s.src.match(/(index-[A-Za-z0-9_-]+\.js)/);
  return m ? m[1] : "";
})();

function punchInProgress(): boolean {
  const v = document.querySelector("video") as HTMLVideoElement | null;
  return !!(v && v.srcObject); // a live camera stream = attendance verify open
}

async function checkAndReload() {
  if (!loadedJs || punchInProgress()) return;
  try {
    const res = await fetch(location.pathname + "?_=" + Date.now(), { cache: "no-store" });
    if (!res.ok) return;
    const html = await res.text();
    const m = html.match(/(index-[A-Za-z0-9_-]+\.js)/);
    if (m && m[1] && m[1] !== loadedJs) location.reload();
  } catch {
    /* offline / ignore */
  }
}

export function initAutoUpdate() {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") checkAndReload();
  });
  window.addEventListener("focus", checkAndReload);
  window.setInterval(checkAndReload, 5 * 60 * 1000); // also every 5 min while open
}
