import React from "react";
import ReactDOM from "react-dom/client";
import { FrappeProvider } from "frappe-react-sdk";
import { BrowserRouter } from "react-router-dom";
import App, { ErrorBoundary } from "./App";
import "./styles.css";

function showRootFallback() {
  const root = document.getElementById("root");
  if (root && !root.textContent?.trim()) {
    root.innerHTML = '<div class="shell center"><div class="card accent card-pad app-error"><h2>Unable to load JEW HRMS.</h2><p>Please refresh or contact admin.</p></div></div>';
  }
}

window.addEventListener("error", () => window.setTimeout(showRootFallback, 0));
window.addEventListener("unhandledrejection", () => window.setTimeout(showRootFallback, 0));

try {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <ErrorBoundary>
        <FrappeProvider>
          <BrowserRouter basename="/jew-hrms">
            <App />
          </BrowserRouter>
        </FrappeProvider>
      </ErrorBoundary>
    </React.StrictMode>
  );
  window.setTimeout(showRootFallback, 3000);
} catch {
  showRootFallback();
}
