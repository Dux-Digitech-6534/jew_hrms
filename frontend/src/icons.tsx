import type { CSSProperties } from "react";

// Inline SVG icon sprite — matches the design mockup (jew-hrms-app.html).
// Rendered once near the app root; icons are referenced via <Ic name="..." />.
export function IconSprite() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        <g id="clock"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></g>
        <g id="calendar"><rect x="3.5" y="5" width="17" height="16" rx="2" /><path d="M3.5 10h17M8 3v4M16 3v4" /></g>
        <g id="user"><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></g>
        <g id="users"><circle cx="9" cy="8" r="3.4" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0" /><path d="M16 5.2a3.4 3.4 0 0 1 0 6.6" /><path d="M17.5 14.2A6.5 6.5 0 0 1 21.5 20" /></g>
        <g id="check"><path d="M4 12.5 9 17.5 20 6.5" /></g>
        <g id="plus"><path d="M12 5v14" /><path d="M5 12h14" /></g>
        <g id="chevron"><path d="m15 18-6-6 6-6" /></g>
        <g id="building"><path d="M5 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16" /><path d="M15 9h3a1 1 0 0 1 1 1v11" /><path d="M9 8h2M9 12h2M9 16h2" /><path d="M3 21h18" /></g>
        <g id="search"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></g>
        <g id="close"><path d="M6 6l12 12M18 6 6 18" /></g>
        <g id="download"><path d="M12 4v11" /><path d="m7 11 5 5 5-5" /><path d="M5 20h14" /></g>
        <g id="home"><path d="M4 11 12 4l8 7" /><path d="M6 10v9h12v-9" /></g>
        <g id="leaf"><path d="M4 20c0-8 6-14 16-14 0 10-6 16-16 14z" /><path d="M5 19c4-3 8-5 12-6" /></g>
        <g id="shield"><path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6z" /></g>
        <g id="bell"><path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6z" /><path d="M10 20a2 2 0 0 0 4 0" /></g>
        <g id="camera"><rect x="3" y="7.5" width="18" height="12.5" rx="2.4" /><path d="M8 7.5 9.4 4.5h5.2L16 7.5" /><circle cx="12" cy="13.5" r="3.2" /></g>
        <g id="pin"><path d="M12 21s7-6.2 7-11a7 7 0 0 0-14 0c0 4.8 7 11 7 11z" /><circle cx="12" cy="10" r="2.6" /></g>
        <g id="gear"><circle cx="12" cy="12" r="3" /><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5 5l2.1 2.1M16.9 16.9 19 19M19 5l-2.1 2.1M7.1 16.9 5 19" /></g>
        <g id="logout"><path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3" /><path d="M20 12H9" /><path d="m13 8-4 4 4 4" /></g>
        <g id="fp"><path d="M12 11v4a3 3 0 0 1-1.2 2.4" /><path d="M8.6 6.6a6 6 0 0 1 9.4 4.9v2.2" /><path d="M6 9.5A6 6 0 0 1 12 5.5" /><path d="M9 12v3a5.6 5.6 0 0 1-.5 2.3" /><path d="M15 11v4" /></g>
        <g id="signal"><path d="M4 20h.01M9 16v4M14 11v9M19 6v14" strokeWidth={3} /></g>
        <g id="mail"><rect x="3" y="6" width="18" height="12" rx="2" /><path d="m4 8 8 5 8-5" /></g>
        <g id="sun"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" /></g>
        <g id="moon"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></g>
        <g id="menu"><path d="M4 7h16M4 12h16M4 17h16" /></g>
        <g id="phone"><path d="M6 3h4l2 5-2.5 1.5a11 11 0 0 0 5 5L16 12l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 4 5a2 2 0 0 1 2-2z" /></g>
        <g id="briefcase"><rect x="3" y="7.5" width="18" height="12.5" rx="2" /><path d="M8 7.5V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v1.5" /><path d="M3 12.5h18" /></g>
        <g id="eye"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></g>
        <g id="eyeoff"><path d="M3 3l18 18" /><path d="M10.6 10.6a3 3 0 0 0 4.2 4.2" /><path d="M9.4 5.2A9.9 9.9 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.3 4.1M6.1 6.1A17 17 0 0 0 2 12s3.5 7 10 7a9.9 9.9 0 0 0 3.3-.6" /></g>
        <g id="refresh"><path d="M20 11a8 8 0 1 0-.5 4" /><path d="M20 5v6h-6" /></g>
        {/* brand: ink + spark J */}
        <g id="jmark"><path d="M15.5 4.5v11.5a5 5 0 0 1-8.4 3.7" stroke="#fff" strokeWidth={2.1} /><path d="M7 6l1.1 3.3L11.4 10.4l-3.3 1.1L7 14.8l-1.1-3.3L2.6 10.4l3.3-1.1z" fill="#0E9C8B" stroke="#0E9C8B" strokeWidth={1.4} /></g>
      </defs>
    </svg>
  );
}

// JEW brand mark (from jew-mark.svg) — J curve in currentColor + cyan sparks.
// Rendered inside the dark .mark / .login-mark tile (currentColor = white).
export function BrandMark() {
  return (
    <svg viewBox="0 0 512 512" aria-hidden="true">
      <defs>
        <linearGradient id="jewSpark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0CB4A0" /><stop offset="1" stopColor="#0E9C8B" />
        </linearGradient>
      </defs>
      <path d="M330 118 V296 a94 94 0 0 1 -158 69" fill="none" stroke="currentColor" strokeWidth={46} strokeLinecap="round" strokeLinejoin="round" />
      <path d="M156 120 l24 66 66 24 -66 24 -24 66 -24 -66 -66 -24 66 -24 z" fill="url(#jewSpark)" />
      <path d="M360 340 l13 34 34 13 -34 13 -13 34 -13 -34 -34 -13 34 -13 z" fill="url(#jewSpark)" opacity="0.9" />
    </svg>
  );
}

export function Ic({ name, style, className = "ic" }: { name: string; style?: CSSProperties; className?: string }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" aria-hidden="true">
      <use href={`#${name}`} />
    </svg>
  );
}
