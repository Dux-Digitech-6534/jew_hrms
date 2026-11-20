/* Punch audit — DUX design system page.
   Evidence behind every attendance punch: photo, face distance, device, IP, phone-clock drift.
   Data: jew_hrms_mobile.punch_audit (get_punch_audit / get_punch_detail / get_punch_photo / review_punch /
   export_punch_audit / get_settings / save_settings). The UI lives in a Shadow DOM so Frappe's global
   styles (.btn, .card, .toast ...) cannot leak in and the DUX tokens cannot leak out. */

frappe.pages["punch-audit"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Punch audit"), single_column: true });
	wrapper.jew_punch_audit = jewPunchAuditBuild(page);
};
frappe.pages["punch-audit"].on_page_show = function (wrapper) {
	if (wrapper.jew_punch_audit) wrapper.jew_punch_audit.onShow();
};

function jewPunchAuditBuild(page) {
	"use strict";
	const API = "jew_hrms_mobile.punch_audit.";
	const CSS = "\n/* ============================================================\n   DUX Digitech design tokens — copied verbatim from the design\n   system (tokens/colors.css, typography.css, spacing.css).\n   ============================================================ */\n:host{\n  --iris:#5C4DE6; --iris-soft:#6D5EF6; --iris-deep:#4A3CD0; --iris-tint:rgba(92,77,230,.10);\n  --cyan:#0E9C8B; --cyan-soft:#0CB4A0; --cyan-tint:rgba(14,156,139,.12);\n  --brand-grad:linear-gradient(90deg,var(--iris) 0%,#4F9FD8 55%,var(--cyan) 100%);\n  --canvas:#F3F4F7; --surface-1:#FFFFFF; --surface-2:#FFFFFF; --surface-3:#F0F1F5;\n  --card-bg:#FFFFFF; --pill-bg:#F5F6F9; --th-bg:#FAFAFC; --meta-bg:#FAFAFC; --hdr-bg:rgba(255,255,255,.82);\n  --hairline:#E7E9EE; --hairline-2:#DCDFE6;\n  --fg-1:#1A2030; --fg-2:#586273; --fg-3:#8A93A3; --fg-4:#AAB2BF;\n  --ok:#15916A; --ok-bg:rgba(21,145,106,.10);\n  --pending:#B0741A; --pending-bg:rgba(176,116,26,.11);\n  --err:#C8475E; --err-bg:rgba(200,71,94,.09);\n  --row-hover:rgba(92,77,230,.045); --avatar-bg:#F4F4FC; --scrollthumb:#D4D8E0;\n  --ambient:radial-gradient(120% 64% at 50% -16%,rgba(92,77,230,.06),transparent 60%),radial-gradient(80% 50% at 88% 6%,rgba(14,156,139,.045),transparent 55%);\n  --bg-app:var(--canvas); --bg-surface:var(--surface-1); --bg-card:var(--card-bg); --bg-sunken:var(--surface-3);\n  --text-primary:var(--fg-1); --text-secondary:var(--fg-2); --text-muted:var(--fg-3); --text-faint:var(--fg-4);\n  --text-brand:var(--iris); --text-data:var(--cyan); --text-on-brand:#FFFFFF;\n  --border-subtle:var(--hairline); --border-strong:var(--hairline-2);\n  --brand-primary:var(--iris); --brand-accent:var(--cyan); --focus-ring:var(--iris);\n  --mark-light-display:block; --mark-white-display:none;\n\n  --font-ui:'Geist','Geist Variable',system-ui,-apple-system,'Segoe UI',sans-serif;\n  --font-mono:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,monospace;\n  --fs-display:26px; --fs-h1:22px; --fs-h2:18px; --fs-h3:16px; --fs-body:14.5px; --fs-sm:13px; --fs-meta:12.5px; --fs-label:11px; --fs-micro:10.5px;\n  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:20px; --sp-6:24px; --sp-8:32px;\n  --r-sm:8px; --r-md:12px; --r-lg:16px; --r-xl:22px; --r-pill:999px;\n  --shadow-card:0 6px 26px rgba(22,28,46,.07); --shadow-pop:0 10px 34px rgba(22,28,46,.10);\n  --inset-hi:inset 0 1px 0 rgba(255,255,255,.6); --shadow-brand:0 6px 18px rgba(109,94,246,.40);\n  --spring:cubic-bezier(.22,1,.36,1); --ease:cubic-bezier(.4,0,.2,1);\n  --dur-fast:.18s; --dur-base:.25s; --dur-slow:.45s;\n}\n:host([data-theme=\"dark\"]){\n  --iris:#6D5EF6; --iris-soft:#8A7DF8; --iris-deep:#5B4DE0; --iris-tint:rgba(109,94,246,.14);\n  --cyan:#2DD4BF; --cyan-soft:#5CE6D5; --cyan-tint:rgba(45,212,191,.14);\n  --canvas:#0A0D13; --surface-1:#11151E; --surface-2:#181D29; --surface-3:#1E2433;\n  --card-bg:linear-gradient(180deg,#181D29,#161A25); --pill-bg:#1E2433; --th-bg:#141926; --meta-bg:rgba(255,255,255,.008); --hdr-bg:rgba(17,21,30,.80);\n  --hairline:#232938; --hairline-2:#2C3344;\n  --fg-1:#E8EAF1; --fg-2:#9AA3B7; --fg-3:#5E6678; --fg-4:#424A5C;\n  --ok:#34D39A; --ok-bg:rgba(52,211,154,.10);\n  --pending:#E0A85C; --pending-bg:rgba(224,168,92,.11);\n  --err:#E8738A; --err-bg:rgba(232,115,138,.10);\n  --row-hover:rgba(109,94,246,.06); --avatar-bg:rgba(255,255,255,.035); --scrollthumb:#2C3344;\n  --ambient:radial-gradient(120% 70% at 50% -12%,rgba(109,94,246,.16),transparent 60%),radial-gradient(90% 60% at 88% 8%,rgba(45,212,191,.07),transparent 55%);\n  --mark-light-display:none; --mark-white-display:block;\n  --shadow-card:0 18px 50px rgba(0,0,0,.42); --shadow-pop:0 14px 40px rgba(0,0,0,.40); --inset-hi:inset 0 1px 0 rgba(255,255,255,.045);\n}\n\n/* ============================================================ base */\n*{box-sizing:border-box}\n:host{display:block;position:relative;font-family:var(--font-ui);background:var(--bg-app);color:var(--text-primary);font-size:var(--fs-body);line-height:1.6;-webkit-font-smoothing:antialiased;min-height:calc(100vh - 100px)}\nbutton,input,select,textarea{font:inherit;color:inherit}\nbutton{cursor:pointer}\nsvg{display:block;flex:0 0 auto}\n::-webkit-scrollbar{width:10px;height:10px}\n::-webkit-scrollbar-thumb{background:var(--scrollthumb);border-radius:999px;border:3px solid var(--canvas)}\n.ambient{position:absolute;inset:0;pointer-events:none;background:var(--ambient);z-index:0}\n.mono{font-family:var(--font-mono);font-variant-numeric:tabular-nums}\n.num{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-weight:600;color:var(--cyan)}\n.lbl{font-size:var(--fs-label);letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted);font-weight:600}\n.turn{animation:duxSettle .5s var(--spring) both}\n@keyframes duxSettle{from{transform:translateY(12px)}to{transform:none}}\n@keyframes duxFloaty{0%,100%{transform:translateY(0) rotate(-1deg)}50%{transform:translateY(-8px) rotate(1deg)}}\n@media (prefers-reduced-motion:reduce){.turn,.mascot{animation:none!important}}\n\n/* ============================================================ header */\n.hdr{position:sticky;top:0;z-index:50;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 22px;background:var(--hdr-bg);backdrop-filter:blur(16px) saturate(130%);-webkit-backdrop-filter:blur(16px) saturate(130%);border-bottom:1px solid var(--border-subtle)}\n.hdr .l,.hdr .r{display:flex;align-items:center;gap:12px;min-width:0}\n.hdr .r{gap:9px}\n.hdr .vr{width:1px;height:24px;background:var(--border-strong)}\n.hdr .nm{display:flex;align-items:baseline;gap:7px}\n.hdr .nm b{font-size:16px;font-weight:600;letter-spacing:-.02em}\n.hdr .nm small{font-size:12px;color:var(--text-muted);font-weight:450}\n.brandav{position:relative;display:grid;place-items:center;width:32px;height:32px;border-radius:9px;background:var(--avatar-bg);border:1px solid var(--border-strong);box-shadow:var(--inset-hi);overflow:hidden;flex:0 0 auto}\n.brandav img{width:62%;height:auto;object-fit:contain}\n.brandav img.k{display:var(--mark-light-display,block)}\n.brandav img.w{position:absolute;display:var(--mark-white-display,none)}\n.brandav::after{content:\"\";position:absolute;inset:0;border-radius:inherit;padding:1px;background:var(--brand-grad);opacity:.45;pointer-events:none;-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude}\n.mockbar{position:relative;z-index:2;text-align:center;padding:7px 14px;background:var(--cyan-tint);border-bottom:1px solid var(--border-subtle);font-size:var(--fs-label);letter-spacing:.08em;text-transform:uppercase;font-weight:600;color:var(--cyan)}\n.mockbar span{color:var(--text-secondary);letter-spacing:0;text-transform:none;font-weight:450;font-size:12px}\n\n/* ============================================================ controls (DS Button / IconButton) */\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;height:38px;padding:8px 16px;font-size:13px;font-weight:500;line-height:1;border-radius:var(--r-pill);white-space:nowrap;cursor:pointer;transition:all var(--dur-base) var(--spring);background:var(--surface-1);color:var(--text-primary);border:1px solid var(--border-strong);box-shadow:var(--inset-hi)}\n.btn:hover{transform:translateY(-1px);border-color:var(--iris);color:var(--iris)}\n.btn svg{width:15px;height:15px}\n.btn.sm{height:30px;padding:6px 12px;font-size:12px;gap:6px}\n.btn.sm svg{width:13px;height:13px}\n.btn.primary{color:var(--text-on-brand);background:linear-gradient(150deg,var(--iris),var(--iris-deep));border-color:transparent;box-shadow:var(--shadow-brand),inset 0 1px 0 rgba(255,255,255,.22)}\n.btn.primary:hover{color:#fff;border-color:transparent}\n.btn.ghost{background:transparent;border-color:transparent;box-shadow:none;color:var(--text-secondary)}\n.btn.ghost:hover{border-color:transparent;color:var(--iris)}\n.btn.danger{color:var(--err);background:var(--err-bg);border-color:var(--err-bg);box-shadow:none}\n.btn.danger:hover{color:var(--err);border-color:var(--err)}\n.ibtn{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;border:1px solid var(--border-strong);background:var(--surface-1);color:var(--text-secondary);transition:all var(--dur-base) var(--spring)}\n.ibtn:hover{transform:translateY(-1px);color:var(--iris);border-color:var(--iris)}\n.ibtn svg{width:16px;height:16px}\n\n/* ============================================================ card / chips (DS Card, Badge, StatusTag, FilterPill, FilterBar) */\n.card{position:relative;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:var(--r-lg);box-shadow:var(--shadow-card),var(--inset-hi);overflow:hidden}\n.card.accent::before,.fbar::before{content:\"\";position:absolute;left:0;right:0;top:0;height:2px;background:var(--brand-grad);opacity:.6;z-index:1}\n.badge{display:inline-flex;align-items:center;gap:5px;font-size:var(--fs-micro);font-weight:600;line-height:1;padding:4px 9px;border-radius:var(--r-pill);color:var(--text-secondary);background:var(--bg-sunken)}\n.badge.mono{font-family:var(--font-mono);font-variant-numeric:tabular-nums}\n.badge.iris{color:var(--iris);background:var(--iris-tint)} .badge.cyan{color:var(--cyan);background:var(--cyan-tint)}\n.badge.ok{color:var(--ok);background:var(--ok-bg)} .badge.pending{color:var(--pending);background:var(--pending-bg)} .badge.err{color:var(--err);background:var(--err-bg)}\n.badge svg{width:11px;height:11px}\n.tag{display:inline-flex;align-items:center;gap:6px;font-size:var(--fs-micro);font-weight:500;line-height:1;padding:3px 9px;border-radius:var(--r-pill);white-space:nowrap}\n.tag::before{content:\"\";width:5px;height:5px;border-radius:50%;background:currentColor}\n.tag.ok{color:var(--ok);background:var(--ok-bg)} .tag.pending{color:var(--pending);background:var(--pending-bg)} .tag.err{color:var(--err);background:var(--err-bg)} .tag.draft{color:var(--text-secondary);background:var(--bg-sunken)}\n.tag.phase{background:transparent;border:1px dashed var(--border-strong);color:var(--text-muted)}\n.fpill{display:inline-flex;align-items:center;gap:7px;padding:6px 11px 6px 9px;border-radius:var(--r-sm);font-size:var(--fs-meta);background:var(--pill-bg);border:1px solid var(--border-strong);color:var(--text-primary)}\n.fpill .tile{width:22px;height:22px;border-radius:6px;display:grid;place-items:center;flex:0 0 auto}\n.fpill .tile svg{width:13px;height:13px}\n.fpill .k{color:var(--text-muted)}\n.fpill .v{font-weight:500}\n.fpill .v.n{font-family:var(--font-mono);font-variant-numeric:tabular-nums;color:var(--cyan)}\n.t-iris{color:var(--iris);background:var(--iris-tint)} .t-cyan{color:var(--cyan);background:var(--cyan-tint)} .t-pending{color:var(--pending);background:var(--pending-bg)} .t-ok{color:var(--ok);background:var(--ok-bg)} .t-mut{color:var(--text-secondary);background:var(--bg-sunken)}\n.fbar{position:relative;padding:14px 16px 13px;border-bottom:1px solid var(--border-subtle)}\n.fbar .hd{display:flex;align-items:center;gap:7px;margin-bottom:11px;font-size:var(--fs-label);letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted);font-weight:600}\n.fbar .hd svg{width:13px;height:13px}\n.fbar .pills{display:flex;flex-wrap:wrap;gap:8px}\n.meta{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:11px 16px;border-bottom:1px solid var(--border-subtle);background:var(--meta-bg);font-size:var(--fs-meta)}\n.meta .a{color:var(--text-secondary)} .meta .a b{color:var(--text-primary);font-family:var(--font-mono)} .meta .b{color:var(--text-muted)}\n\n/* ============================================================ layout */\n.wrap{position:relative;z-index:2;max-width:1280px;margin:0 auto;padding:28px 24px 56px}\n.intro{margin-bottom:22px}\n.intro h1{font-size:var(--fs-display);font-weight:600;letter-spacing:-.025em;margin:6px 0 8px;line-height:1.2}\n.intro h1 em{font-style:normal;background:var(--brand-grad);-webkit-background-clip:text;background-clip:text;color:transparent}\n.intro p{margin:0;font-size:var(--fs-body);line-height:1.6;color:var(--text-secondary);max-width:64ch}\n.eyebrow{font-size:var(--fs-label);letter-spacing:.1em;text-transform:uppercase;color:var(--iris);font-weight:600}\n.controls{padding:16px;display:grid;gap:14px;margin-bottom:16px}\n.crow{display:flex;gap:12px;align-items:center;flex-wrap:wrap}\n.crow .sp{flex:1}\n.rng{display:inline-flex;gap:6px;flex-wrap:wrap}\n.rng button{height:30px;padding:6px 12px;font-size:12px;font-weight:500;line-height:1;border-radius:var(--r-pill);background:var(--surface-1);border:1px solid var(--border-strong);color:var(--text-secondary);box-shadow:var(--inset-hi);transition:all var(--dur-base) var(--spring)}\n.rng button:hover{transform:translateY(-1px);border-color:var(--iris);color:var(--iris)}\n.rng button.on{background:var(--iris-tint);border-color:var(--iris);color:var(--iris)}\n.dates{display:none;align-items:center;gap:8px;font-size:var(--fs-meta);color:var(--text-muted)}\n.dates.show{display:flex}\n.dates input{height:34px;border:1px solid var(--border-strong);background:var(--surface-1);border-radius:var(--r-sm);padding:0 10px;font-family:var(--font-mono);font-size:12px;color:var(--text-primary);box-shadow:var(--inset-hi)}\n.dates input:focus{outline:none;border-color:var(--iris);box-shadow:0 0 0 3px var(--iris-tint)}\n.fgrid{display:grid;grid-template-columns:1.7fr repeat(5,1fr);gap:12px}\n.fld{display:grid;gap:6px;min-width:0}\n.in{display:flex;align-items:center;gap:9px;padding:0 13px;height:42px;border-radius:var(--r-md);background:var(--surface-1);border:1px solid var(--border-strong);box-shadow:var(--inset-hi);min-width:0;position:relative;transition:border-color var(--dur-base) var(--ease),box-shadow var(--dur-base) var(--ease)}\n.in:focus-within{border-color:var(--iris);box-shadow:0 0 0 3px var(--iris-tint)}\n.in>svg{width:16px;height:16px;color:var(--text-muted)}\n.in:focus-within>svg{color:var(--iris)}\n.in input,.in select{flex:1 1 auto;border:0;outline:0;background:transparent;font-size:var(--fs-body);color:var(--text-primary);min-width:0;height:100%;appearance:none;-webkit-appearance:none}\n.in select{cursor:pointer;padding-right:18px}\n.in select option{background:var(--surface-1);color:var(--text-primary)}\n.in .chev{position:absolute;right:12px;top:50%;width:14px;height:14px;margin-top:-7px;transform:rotate(-90deg);color:var(--text-muted);pointer-events:none}\n.sw{display:inline-flex;align-items:center;gap:9px;font-size:var(--fs-sm);font-weight:500;color:var(--text-secondary);cursor:pointer;user-select:none;background:none;border:0;padding:0}\n.sw i{width:36px;height:21px;border-radius:99px;background:var(--border-strong);position:relative;transition:background var(--dur-base) var(--ease);flex:0 0 auto}\n.sw i::after{content:\"\";position:absolute;top:3px;left:3px;width:15px;height:15px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:left var(--dur-base) var(--spring)}\n.sw.on{color:var(--iris)} .sw.on i{background:var(--iris)} .sw.on i::after{left:18px}\n\n.lead{margin:0 0 14px;font-size:var(--fs-body);line-height:1.6;max-width:68ch}\n.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:18px}\n.kpi{text-align:left;padding:14px 16px;display:grid;gap:3px;cursor:default;transition:all var(--dur-base) var(--spring);width:100%}\n.kpi .v{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:26px;font-weight:600;letter-spacing:-.02em;color:var(--cyan);line-height:1.2}\n.kpi .v.err{color:var(--err)} .kpi .v.pending{color:var(--pending)} .kpi .v.ok{color:var(--ok)}\n.kpi .s{font-size:var(--fs-meta);color:var(--text-muted)}\nbutton.kpi{font:inherit;color:inherit}\n.kpi.click{cursor:pointer}\n.kpi.click:hover{transform:translateY(-1px);border-color:var(--iris)}\n.kpi.active{border-color:var(--iris);box-shadow:0 0 0 3px var(--iris-tint),var(--shadow-card)}\n.tabs{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}\n.tab{height:36px;padding:0 14px;border-radius:var(--r-pill);border:1px solid var(--border-strong);background:var(--surface-1);box-shadow:var(--inset-hi);font-size:var(--fs-sm);font-weight:500;color:var(--text-secondary);display:inline-flex;align-items:center;gap:8px;transition:all var(--dur-base) var(--spring)}\n.tab:hover{transform:translateY(-1px);border-color:var(--iris);color:var(--iris)}\n.tab.on{background:var(--iris-tint);border-color:var(--iris);color:var(--iris)}\n.tab svg{width:14px;height:14px}\n.tabs .sp{flex:1}\n.tabs .help{display:inline-flex;align-items:center;gap:6px;border:0;background:none;color:var(--iris);font-size:var(--fs-meta);font-weight:500}\n.tabs .help svg{width:14px;height:14px}\n.foot{margin:12px 2px 0;font-size:var(--fs-meta);color:var(--text-muted)}\n\n/* ============================================================ tables (DS DataTable) */\n.tscroll{overflow-x:auto}\ntable{width:100%;border-collapse:collapse;font-size:var(--fs-sm)}\nth{text-align:left;font-weight:600;font-size:var(--fs-micro);letter-spacing:.07em;text-transform:uppercase;color:var(--text-muted);padding:11px 16px;border-bottom:1px solid var(--border-subtle);white-space:nowrap;background:var(--th-bg)}\ntd{padding:12px 16px;border-bottom:1px solid var(--border-subtle);color:var(--text-primary);vertical-align:middle}\ntbody tr:last-child td{border-bottom:0}\ntable.pt{min-width:1120px}\ntable.pt tbody tr{cursor:pointer;transition:background .18s var(--ease)}\ntable.pt tbody tr:hover{background:var(--row-hover)}\ntable.pt tbody tr.rev{opacity:.55}\ntable.pt td{white-space:nowrap}\ntable.pt td.c-flags{white-space:normal}\ntable.dt{min-width:900px}\n.thumb{width:44px;height:44px;border-radius:var(--r-md);overflow:hidden;border:1px solid var(--border-strong);background:var(--avatar-bg);box-shadow:var(--inset-hi)}\n.thumb.none{display:grid;place-items:center;text-align:center;font-size:9.5px;line-height:1.15;color:var(--text-muted);font-weight:500}\n.emp b{display:block;font-weight:500;font-size:var(--fs-sm)}\n.emp span{font-size:12px;color:var(--text-secondary)}\n.id{font-family:var(--font-mono);font-variant-numeric:tabular-nums;color:var(--cyan);font-weight:600}\n.sub{font-size:12px;color:var(--text-muted)}\n.tm{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-weight:600;font-size:13px;display:block;margin-top:5px}\n.dtm{font-family:var(--font-mono);font-size:12px;color:var(--text-secondary)}\n.fv{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-weight:600;color:var(--cyan);font-size:13px}\n.bar{height:5px;border-radius:99px;background:var(--bg-sunken);border:1px solid var(--border-subtle);position:relative;margin-top:6px;min-width:90px}\n.bar i{display:block;height:100%;border-radius:99px;background:var(--ok)}\n.bar i.m{background:var(--pending)}\n.bar::after{content:\"\";position:absolute;top:-3px;bottom:-3px;width:2px;border-radius:2px;background:var(--err);left:calc(var(--th) * 1%)}\n.clk{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:12.5px;font-weight:500}\n.clk.bad{color:var(--err)} .clk.warn{color:var(--pending)} .clk.good{color:var(--text-muted)}\n.flags{display:flex;flex-wrap:wrap;gap:5px}\n.empty{padding:34px 18px 38px;text-align:center;color:var(--text-secondary);display:grid;justify-items:center;gap:6px}\n.empty img{width:88px;height:auto;filter:drop-shadow(0 10px 22px rgba(20,28,46,.2));animation:duxFloaty 5s var(--ease) infinite}\n.empty h3{margin:6px 0 0;font-size:var(--fs-h3);font-weight:600;letter-spacing:-.02em;color:var(--text-primary)}\n.empty p{margin:0;font-size:var(--fs-sm);max-width:44ch}\n\n.egrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px;padding:16px}\n.ecard{padding:14px;border-radius:var(--r-md);border:1px solid var(--border-subtle);background:var(--pill-bg);display:grid;gap:11px;cursor:pointer;transition:all var(--dur-base) var(--spring)}\n.ecard:hover{transform:translateY(-1px);border-color:var(--iris)}\n.ecard .hd{display:flex;gap:11px;align-items:center}\n.ecard .st{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}\n.ecard .st div{background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:10px;padding:7px 10px}\n.ecard .st b{display:block;font-family:var(--font-mono);font-size:15px;color:var(--cyan)}\n.ecard .st span{font-size:var(--fs-micro);color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;font-weight:600}\n\n/* legend + settings */\n.legend{display:none;margin-bottom:14px}\n.legend.show{display:block}\n.legend .box{padding:16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}\n.rule{display:grid;gap:7px;padding:13px;border:1px solid var(--border-subtle);border-radius:var(--r-md);background:var(--pill-bg);justify-items:start}\n.rule b{font-size:var(--fs-sm);font-weight:600}\n.rule p{margin:0;font-size:var(--fs-meta);color:var(--text-secondary);line-height:1.5}\n.rule.ph{border-style:dashed;background:transparent}\n.settings{margin-top:20px;padding:18px 16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px;align-items:start}\n.settings h3{margin:0 0 4px;font-size:var(--fs-sm);font-weight:600}\n.settings p{margin:0 0 10px;font-size:var(--fs-meta);color:var(--text-secondary);line-height:1.5}\n\n/* ============================================================ drawer */\n.scrim{position:fixed;inset:0;background:rgba(10,13,19,.45);z-index:1035;opacity:0;pointer-events:none;transition:opacity var(--dur-base) var(--ease)}\n.scrim.on{opacity:1;pointer-events:auto}\n.drawer{position:fixed;top:0;right:0;bottom:0;width:min(480px,100%);background:var(--bg-card);z-index:1040;box-shadow:var(--shadow-pop);transform:translateX(104%);transition:transform .35s var(--spring);display:flex;flex-direction:column;border-left:1px solid var(--border-subtle);border-radius:var(--r-xl) 0 0 var(--r-xl);overflow:hidden}\n.drawer.on{transform:none}\n.drawer::before{content:\"\";position:absolute;left:0;right:0;top:0;height:2px;background:var(--brand-grad);opacity:.6;z-index:2}\n.dhead{display:flex;align-items:center;gap:12px;padding:18px 18px 14px;border-bottom:1px solid var(--border-subtle)}\n.dhead h2{margin:0;font-size:var(--fs-h3);font-weight:600;letter-spacing:-.02em;line-height:1.3}\n.dhead .sub{margin-top:2px}\n.dbody{overflow-y:auto;padding:16px 18px 22px;display:grid;gap:18px;align-content:start}\n.photos{display:grid;grid-template-columns:1fr 1fr;gap:10px}\n.pbox{border:1px solid var(--border-strong);border-radius:var(--r-md);overflow:hidden;background:var(--avatar-bg);box-shadow:var(--inset-hi)}\n.pbox .im{aspect-ratio:1/1.02}\n.pbox .cap{padding:8px 11px;display:flex;justify-content:space-between;gap:8px;border-top:1px solid var(--border-subtle);background:var(--bg-surface)}\n.facts{display:grid;grid-template-columns:1fr 1fr;gap:8px}\n.fact{background:var(--pill-bg);border:1px solid var(--border-subtle);border-radius:10px;padding:9px 11px;min-width:0}\n.fact .lbl{font-size:var(--fs-micro)}\n.fact b{display:block;margin-top:3px;font-size:var(--fs-sm);font-weight:500;overflow-wrap:anywhere}\n.fact.wide{grid-column:1/-1}\n.sec{margin-bottom:8px}\n.issue{display:flex;gap:11px;padding:11px 12px;border-radius:var(--r-md);border:1px solid var(--border-subtle);margin-bottom:8px;background:var(--pill-bg)}\n.issue .ic{width:26px;height:26px;border-radius:7px;display:grid;place-items:center;flex:0 0 auto}\n.issue .ic svg{width:14px;height:14px}\n.issue b{display:block;font-size:var(--fs-sm);font-weight:600;line-height:1.35}\n.issue p{margin:2px 0 0;font-size:var(--fs-meta);color:var(--text-secondary);line-height:1.5}\n.mapbox{border:1px solid var(--border-strong);border-radius:var(--r-md);overflow:hidden;background:var(--pill-bg)}\n.devuse{display:grid;gap:6px}\n.devuse div{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:var(--fs-meta);padding:9px 12px;background:var(--pill-bg);border:1px solid var(--border-subtle);border-radius:10px}\n.devuse div.me{border-color:var(--iris);background:var(--iris-tint)}\ntextarea.note{width:100%;min-height:68px;border:1px solid var(--border-strong);background:var(--surface-1);border-radius:var(--r-md);padding:10px 12px;resize:vertical;font-size:var(--fs-sm);box-shadow:var(--inset-hi);color:var(--text-primary)}\ntextarea.note:focus{outline:none;border-color:var(--iris);box-shadow:0 0 0 3px var(--iris-tint)}\n.dfoot{padding:12px 18px 16px;border-top:1px solid var(--border-subtle);display:flex;gap:8px;flex-wrap:wrap;background:var(--bg-surface)}\n.toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,30px);background:var(--text-primary);color:var(--bg-surface);padding:10px 16px;border-radius:var(--r-pill);font-weight:500;font-size:var(--fs-sm);z-index:1060;opacity:0;pointer-events:none;transition:all var(--dur-base) var(--spring);box-shadow:var(--shadow-pop);max-width:90vw}\n.toast.on{opacity:1;transform:translate(-50%,0)}\n\n/* ============================================================ responsive */\n@media(max-width:1180px){.kpis{grid-template-columns:repeat(3,1fr)}.fgrid{grid-template-columns:repeat(3,1fr)}.fgrid .fld:first-child{grid-column:1/-1}}\n@media(max-width:820px){\n  .hdr{padding:12px 14px}.hdr .nm small{display:none}.hdr .btn span{display:none}.hdr .btn{padding:0 11px}\n  .wrap{padding:20px 14px 48px}\n  .intro h1{font-size:22px}\n  .kpis{grid-template-columns:repeat(2,1fr);gap:10px}.kpi .v{font-size:23px}\n  .fgrid{grid-template-columns:repeat(2,1fr)}\n  table.pt{min-width:0}table.pt thead{display:none}table.pt,table.pt tbody{display:block}\n  table.pt tbody tr{display:grid;grid-template-columns:44px 1fr 1fr auto;grid-template-areas:\"photo emp emp punch\" \"photo loc loc loc\" \"face face face face\" \"dev dev ip clk\" \"flags flags flags flags\";gap:9px 12px;padding:14px;border-bottom:1px solid var(--border-subtle)}\n  table.pt tbody tr:last-child{border-bottom:0}\n  table.pt td{display:block;padding:0;border:0;white-space:normal}\n  td.c-photo{grid-area:photo}td.c-emp{grid-area:emp}td.c-punch{grid-area:punch;text-align:right}td.c-loc{grid-area:loc}td.c-face{grid-area:face}td.c-dev{grid-area:dev}td.c-ip{grid-area:ip}td.c-clk{grid-area:clk;text-align:right}td.c-flags{grid-area:flags}\n  table.dt{min-width:760px}\n  .drawer{width:100%;top:auto;height:92vh;border-left:0;border-top:1px solid var(--border-subtle);border-radius:var(--r-xl) var(--r-xl) 0 0;transform:translateY(104%)}\n  .drawer.on{transform:none}\n}\n@media(max-width:430px){.fgrid{grid-template-columns:1fr}}\n\n/* ---- additions for live data ---- */\n.thumb img{width:100%;height:100%;object-fit:cover;object-position:50% 22%;display:block}\n.thumb.ini{display:grid;place-items:center;font-weight:600;font-size:13px;color:var(--iris);background:var(--iris-tint);letter-spacing:.02em}\n.pbox .im{overflow:hidden}\n.pbox .im img{width:100%;height:100%;object-fit:cover;display:block}\n.pbox .im .ph{height:100%;display:grid;place-items:center;text-align:center;color:var(--text-muted);font-size:12px;padding:10px;background:var(--pill-bg)}\n.issue .ic.hi{color:var(--err);background:var(--err-bg)}\n.issue .ic.t-pending,.issue .ic.t-ok{}\n.shim{background:linear-gradient(90deg,var(--fg-3) 30%,var(--fg-1) 50%,var(--fg-3) 70%);background-size:200% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:duxShim 2.2s linear infinite}\n@keyframes duxShim{to{background-position:-200% 0}}\n@media (prefers-reduced-motion:reduce){.shim{animation:none}}\n.capnote{display:flex;align-items:flex-start;gap:8px;margin:0 0 14px;padding:9px 12px;border-radius:var(--r-md);background:var(--iris-tint);color:var(--text-secondary);font-size:var(--fs-meta);line-height:1.5;max-width:78ch}\n.capnote svg{margin-top:2px;color:var(--iris)}\n.msel{height:32px;width:178px;border-radius:var(--r-pill);padding:0 12px}\n.msel.on{border-color:var(--iris);background:var(--iris-tint)}\n.msel.on select{color:var(--iris);font-weight:500}\n.msel select{font-size:var(--fs-sm)}\ntable.mt{min-width:980px}\ntable.mt td{white-space:nowrap}\ntable.mt tbody tr{cursor:pointer;transition:background .18s var(--ease)}\ntable.mt tbody tr:hover{background:var(--row-hover)}\ntable.mt tbody tr.tot{cursor:default;background:var(--th-bg)}\ntable.mt tbody tr.tot:hover{background:var(--th-bg)}\ntable.mt tbody tr.tot td{border-top:1px solid var(--border-strong)}\n.empH{display:flex;align-items:center;gap:12px;padding:14px 16px;border-bottom:1px solid var(--border-subtle);flex-wrap:wrap}\n.empH .sp{flex:1}\n.empH .thumb{flex:none}\n.dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,350px),1fr));gap:14px;padding:16px}\n.dcard{border:1px solid var(--border-subtle);border-radius:var(--r-lg);background:var(--surface-1);overflow:hidden;min-width:0}\n.dcard.fl{border-color:var(--err)}\n.dcard .dh{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border-subtle);background:var(--th-bg);flex-wrap:wrap}\n.dcard .dh b{font-weight:600;font-size:var(--fs-sm)}\n.dcard .dh .sub{margin-left:auto;font-size:12px;color:var(--text-secondary)}\n.dpair{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px}\n.dpair + .dpair{border-top:1px solid var(--border-subtle)}\n.dhalf{display:grid;gap:6px;align-content:start;min-width:0;cursor:pointer;border-radius:var(--r-md);outline-offset:3px}\n.dhalf .ph{position:relative;aspect-ratio:1/1;border-radius:var(--r-md);overflow:hidden;background:var(--pill-bg);display:grid;place-items:center;text-align:center;color:var(--text-muted);font-size:12px;line-height:1.35;padding:10px;border:1px solid var(--border-subtle);transition:border-color .18s var(--ease)}\n.dhalf .ph span{position:relative}\n.dhalf .ph .badge{position:absolute;top:7px;left:7px;z-index:2}\n.dhalf .ph img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}\n.dhalf:hover .ph,.dhalf:focus-visible .ph{border-color:var(--iris)}\n.dhalf.none{cursor:default}\n.dhalf.none .ph{border-style:dashed;background:transparent}\n.dhalf .tm2{font-family:var(--font-mono);font-weight:600;font-size:16px;color:var(--text-primary)}\n.dhalf .mt2{font-size:12px;color:var(--text-secondary);line-height:1.4;overflow-wrap:anywhere}\n.dhalf .flags{display:flex;flex-wrap:wrap;gap:4px}\n.photos{align-items:start}\n.pbox .im.has{aspect-ratio:auto;background:#12131f}\n.pbox .im.has img{width:100%;height:auto;display:block;object-fit:contain;cursor:zoom-in}\n.dhalf .ph.has{aspect-ratio:auto;display:block;padding:0;background:#12131f;cursor:zoom-in}\n.dhalf .ph.has img{position:static;width:100%;height:auto;display:block}\n.dhalf .ph.has span{display:none}\n.lbx{position:fixed;inset:0;z-index:1070;display:none;place-items:center;background:rgba(8,9,16,.88);padding:20px;cursor:zoom-out}\n.lbx.on{display:grid}\n.lbx img{max-width:min(94vw,860px);max-height:92vh;object-fit:contain;border-radius:var(--r-md);box-shadow:var(--shadow-pop);background:#12131f}\n.lbx .ibtn{position:absolute;top:14px;right:14px;color:#fff;border-color:rgba(255,255,255,.35)}\n.mstrip{display:flex;gap:2px;align-items:center}\n.mstrip i{display:block;width:8px;height:18px;border-radius:2px;background:var(--border-subtle)}\n.mstrip i.on{background:var(--cyan)}\n.mstrip i.fl{background:var(--err)}\n.mstrip i.off{opacity:.45}\n.mstrip i.today{outline:1.5px solid var(--iris);outline-offset:1px}\n.npbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:11px 16px;border-bottom:1px solid var(--border-subtle)}\n.npbar .sp{flex:1}\ntable.np{min-width:880px}\ntable.np td{white-space:nowrap;vertical-align:top}\ntable.np td.chips{white-space:normal}\ntable.np td.chips .tag{margin:0 6px 4px 0}\ntable.np tbody tr.cov{opacity:.7}\n.xbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:11px 16px;border-bottom:1px solid var(--border-subtle)}\n.xbar .sp{flex:1}\n.xchip{height:30px;padding:0 12px;border-radius:var(--r-pill);border:1px solid var(--border-strong);background:var(--surface-1);font-size:var(--fs-sm);font-weight:500;color:var(--text-secondary);display:inline-flex;align-items:center;gap:6px;cursor:pointer;transition:all .18s var(--ease)}\n.xchip:hover{border-color:var(--iris);color:var(--iris)}\n.xchip.on{background:var(--iris-tint);border-color:var(--iris);color:var(--iris)}\n.xchip .n{font-family:var(--font-mono);font-size:11px;opacity:.8}\n.xseg{display:inline-flex;border:1px solid var(--border-strong);border-radius:var(--r-pill);overflow:hidden}\n.xseg button{height:30px;padding:0 14px;border:0;background:var(--surface-1);font-size:var(--fs-sm);color:var(--text-secondary);cursor:pointer}\n.xseg button.on{background:var(--iris-tint);color:var(--iris);font-weight:500}\ntable.xt{min-width:980px}\ntable.xt td{white-space:nowrap;vertical-align:top}\ntable.xt td.chips{white-space:normal}\ntable.xt td.chips .tag{margin:0 6px 4px 0}\ntable.xt tbody tr[data-ex]{cursor:pointer;transition:background .18s var(--ease)}\ntable.xt tbody tr[data-ex]:hover{background:var(--row-hover)}\ntable.xt tbody tr.tot{cursor:default;background:var(--th-bg)}\ntable.xt tbody tr.tot:hover{background:var(--th-bg)}\n.xgrid{border-collapse:separate;border-spacing:0;font-size:12px;width:100%}\n.xgrid th,.xgrid td{padding:5px 2px;text-align:center;border-bottom:1px solid var(--border-subtle)}\n.xgrid th{font-weight:500;color:var(--text-muted);font-size:11px;line-height:1.25}\n.xgrid th.sun{color:var(--err)}\n.xgrid td.nm,.xgrid th.nm{position:sticky;left:0;z-index:1;background:var(--surface-1);text-align:left;padding:6px 12px;min-width:190px;white-space:nowrap}\n.xgrid tbody tr:hover td{background:var(--row-hover)}\n.xc{position:relative;display:inline-grid;place-items:center;min-width:26px;height:24px;padding:0 4px;border-radius:6px;font-family:var(--font-mono);font-size:11px;font-weight:600;cursor:pointer;color:var(--text-muted);background:var(--bg-sunken)}\n.xc.c-P{color:var(--ok);background:var(--ok-bg)}\n.xc.c-HD{color:var(--pending);background:var(--pending-bg)}\n.xc.c-A{color:var(--err);background:var(--err-bg)}\n.xc.c-L{color:var(--iris);background:var(--iris-tint)}\n.xc.c-LQ{color:var(--iris);background:transparent;border:1px dashed var(--iris)}\n.xc.c-OD{color:var(--cyan);background:var(--iris-tint)}\n.xc.c-ODQ{color:var(--cyan);background:transparent;border:1px dashed var(--cyan)}\n.xc.c-H{color:var(--text-muted);background:var(--bg-sunken)}\n.xc.c-I{color:var(--err);background:transparent;border:1px dashed var(--err)}\n.xc .xd{position:absolute;top:-3px;right:-3px;width:8px;height:8px;border-radius:50%;background:var(--err);border:1px solid var(--surface-1)}\n.xc .xd.miss{background:var(--surface-1);border:2px solid var(--err)}\n.xleg{display:flex;gap:6px 16px;flex-wrap:wrap;padding:12px 16px;font-size:12px;color:var(--text-muted);border-top:1px solid var(--border-subtle)}\n.xleg>span{display:inline-flex;align-items:center;gap:6px}\n.xleg .xc{cursor:default}\n.xcal{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr));gap:12px;padding:16px}\n.xday{border:1px solid var(--border-subtle);border-radius:var(--r-lg);background:var(--surface-1);padding:10px 12px;display:grid;gap:6px;align-content:start;min-width:0}\n.xday.off{background:var(--bg-sunken);opacity:.85}\n.xday.fl{border-color:var(--err)}\n.xday .hd{display:flex;align-items:center;gap:8px}\n.xday .hd b{font-size:var(--fs-sm);font-weight:600}\n.xday .tm{font-family:var(--font-mono);font-size:12px;color:var(--text-secondary)}\n.xday .tg{display:flex;flex-wrap:wrap;gap:4px}\n.kpis.k4{grid-template-columns:repeat(4,1fr)}\ntable.xt{min-width:720px}\n@media(max-width:1180px){.kpis.k4{grid-template-columns:repeat(2,1fr)}}\n";
	const HTML = "\n<div class=\"ambient\" aria-hidden=\"true\"></div>\n<header class=\"hdr\">\n  <div class=\"l\">\n    <button class=\"ibtn\" id=\"backBtn\" title=\"Back\" aria-label=\"Back\"><span data-i=\"chevron\"></span></button>\n    <span class=\"brandav\"><img class=\"k\" data-mark=\"k\" alt=\"DUX\"><img class=\"w\" data-mark=\"w\" alt=\"\"></span>\n    <span class=\"vr\"></span>\n    <div class=\"nm\"><b>DUX</b><small>Punch audit</small></div>\n  </div>\n  <div class=\"r\">\n    <button class=\"ibtn\" id=\"themeBtn\" title=\"Toggle theme\" aria-label=\"Toggle theme\"><span id=\"themeIc\"></span></button>\n    <button class=\"btn\" id=\"refreshBtn\"><span data-i=\"refresh\"></span><span>Refresh</span></button>\n    <button class=\"btn primary\" id=\"exportBtn\"><span data-i=\"download\"></span><span>Export to Excel</span></button>\n  </div>\n</header>\n<main class=\"wrap\">\n  <section class=\"intro\">\n    <div class=\"eyebrow\">HR review</div>\n    <h1>Review every punch with its <em>proof</em></h1>\n    <p>See the photo, device, IP address and phone clock behind each attendance punch. Flags point things out for a closer look — nothing is blocked automatically.</p>\n  </section>\n  <div class=\"card controls\">\n    <div class=\"crow\">\n      <div class=\"rng\" id=\"rangeSeg\">\n        <button data-r=\"today\">Today</button><button data-r=\"yesterday\">Yesterday</button><button data-r=\"7d\">Last 7 days</button><button data-r=\"month\" class=\"on\">This month</button><button data-r=\"30d\">Last 30 days</button><button data-r=\"custom\">Custom</button>\n      </div>\n      <div class=\"in msel\" id=\"monBox\"><span data-i=\"calendar\"></span><select id=\"fMonth\" aria-label=\"Month\"></select><span class=\"chev\" data-i=\"chevron\"></span></div>\n      <div class=\"dates show\" id=\"dates\"><input type=\"date\" id=\"dFrom\" aria-label=\"From date\"><span>to</span><input type=\"date\" id=\"dTo\" aria-label=\"To date\"></div>\n      <div class=\"rng\" id=\"rpSeg\" style=\"display:none\"><button data-p=\"month\" class=\"on\">Month-wise</button><button data-p=\"year\">Year-wise</button></div>\n      <div class=\"in msel on\" id=\"rpMonBox\" style=\"display:none\"><span data-i=\"calendar\"></span><select id=\"rpMonth\" aria-label=\"Report month\"></select><span class=\"chev\" data-i=\"chevron\"></span></div>\n      <div class=\"in msel on\" id=\"rpYearBox\" style=\"display:none\"><span data-i=\"calendar\"></span><select id=\"rpYear\" aria-label=\"Report year\"></select><span class=\"chev\" data-i=\"chevron\"></span></div>\n      <div class=\"sp\"></div>\n      <button class=\"sw\" id=\"photoToggle\" type=\"button\"><i></i>With photo only</button>\n      <button class=\"sw\" id=\"flagToggle\" type=\"button\"><i></i>Flagged only</button>\n      <button class=\"btn ghost sm\" id=\"resetBtn\">Reset filters</button>\n    </div>\n    <div class=\"fgrid\">\n      <div class=\"fld\"><span class=\"lbl\">Employee</span><div class=\"in\"><span data-i=\"search\"></span><input id=\"fQ\" placeholder=\"Name or employee ID\"></div></div>\n      <div class=\"fld\"><span class=\"lbl\">Department</span><div class=\"in\"><select id=\"fDept\"></select><span class=\"chev\" data-i=\"chevron\"></span></div></div>\n      <div class=\"fld pa-only\"><span class=\"lbl\">Location</span><div class=\"in\"><select id=\"fLoc\"></select><span class=\"chev\" data-i=\"chevron\"></span></div></div>\n      <div class=\"fld pa-only\"><span class=\"lbl\">Punch type</span><div class=\"in\"><select id=\"fType\"><option value=\"\">All types</option><option>IN</option><option>OUT</option></select><span class=\"chev\" data-i=\"chevron\"></span></div></div>\n      <div class=\"fld pa-only\"><span class=\"lbl\">Source</span><div class=\"in\"><select id=\"fSrc\"></select><span class=\"chev\" data-i=\"chevron\"></span></div></div>\n      <div class=\"fld pa-only\"><span class=\"lbl\">Device or IP</span><div class=\"in\"><span data-i=\"phone\"></span><input id=\"fDev\" placeholder=\"D-3F9A1C2E or 103.172…\"></div></div>\n    </div>\n  </div>\n  <p class=\"lead\" id=\"lead\"></p>\n  <div class=\"capnote\" id=\"capnote\"></div>\n  <div class=\"kpis\" id=\"kpis\"></div>\n  <div class=\"card accent legend\" id=\"legend\"><div class=\"box\" id=\"legendBox\"></div></div>\n  <div class=\"tabs\" id=\"tabs\"></div>\n  <div id=\"view\"></div>\n  <p class=\"foot\">DUX can make mistakes — flags are signals, not verdicts. Review each one before acting on it.</p>\n  <div class=\"card settings\">\n    <div><h3>Photo retention</h3><p>Punch photos are deleted automatically after this period. The punch record itself is kept.</p>\n      <div class=\"in\" style=\"max-width:200px\"><select id=\"retention\"><option value=\"30\">30 days</option><option value=\"60\">60 days</option><option value=\"90\">90 days</option><option value=\"180\">180 days</option><option value=\"365\">365 days</option></select><span class=\"chev\" data-i=\"chevron\"></span></div></div>\n    <div><h3>Liveness check</h3><p>Set for the whole site by IT. When it is on, a punch that fails the blink / head-turn challenge is rejected before it is saved.</p>\n      <span class=\"badge iris\" id=\"liveMode\">—</span></div>\n    <div><h3>Who can view</h3><p>HR, MD and Admin see every employee. A department head (the leave approver set as Dept Head Email) sees only their own team, and the teams of those people.</p>\n      <div style=\"display:flex;gap:6px;flex-wrap:wrap\"><span class=\"badge iris\">JEW HRMS HR</span><span class=\"badge iris\">JEW HRMS Owner</span><span class=\"badge iris\">JEW HRMS Admin</span><span class=\"badge\">System Manager</span><span class=\"badge\">Department heads · own team</span></div></div>\n    <div><h3>Privacy</h3><p>Photos are private files, served only to signed-in authorised users. Employees are told in the app that their photo, device and location are recorded.</p></div>\n  </div>\n</main>\n<div class=\"scrim\" id=\"scrim\"></div>\n<aside class=\"drawer\" id=\"drawer\" aria-hidden=\"true\"></aside>\n<div class=\"lbx\" id=\"lbx\" aria-hidden=\"true\"><button class=\"ibtn\" id=\"lbxClose\" aria-label=\"Close photo\"><span data-i=\"close\"></span></button><img id=\"lbxImg\" alt=\"Punch photo\"></div>\n<div class=\"toast\" id=\"toast\" role=\"status\"></div>\n";
	const MARK_L = "data:image/png;base64,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";
	const MARK_W = "data:image/png;base64,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";
	const MASCOT = "data:image/png;base64,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";

	// Attendance report + Leave & on duty tabs (month-wise / year-wise register)
	const CSS_RP = ".rgrid{border-collapse:separate;border-spacing:0;font-size:12px;width:100%}"
		+ ".rgrid th,.rgrid td{padding:5px 3px;text-align:center;border-bottom:1px solid var(--border-subtle);vertical-align:top}"
		+ ".rgrid th{font-weight:500;color:var(--text-muted);font-size:11px;line-height:1.25}.rgrid th.sun{color:var(--err)}"
		+ ".rgrid td.nm,.rgrid th.nm{position:sticky;left:0;z-index:1;background:var(--surface-1);text-align:left;padding:6px 12px;min-width:200px;white-space:nowrap}"
		+ ".rgrid tbody tr{cursor:pointer}.rgrid tbody tr:hover td{background:var(--row-hover)}"
		+ ".rgrid td.tt{font-family:var(--font-mono);font-size:12px;font-weight:600;color:var(--text-primary);padding:8px 6px}"
		+ ".rio{display:block;font-family:var(--font-mono);font-size:10px;line-height:1.35;color:var(--text-secondary);white-space:nowrap;margin-top:2px}.rio.miss{color:var(--err)}"
		+ ".xc.c-WO,.xc.c-H{color:var(--text-muted);background:var(--bg-sunken)}"
		+ ".rpm{display:grid;gap:1px;justify-items:center}.rpm b{font-family:var(--font-mono);font-size:12.5px;color:var(--text-primary)}.rpm span{font-size:10.5px;color:var(--text-muted);white-space:nowrap}"
		+ "table.xt tbody tr[data-rp]{cursor:pointer}table.xt tbody tr[data-rp]:hover{background:var(--row-hover)}"
		+ "table.xt td.chips .tag{margin:0 6px 4px 0}"
		+ ".rpnote{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--text-muted)}";

	// ---- shell: hide Frappe's own page header, mount the Shadow DOM host
	try { page.wrapper.find(".page-head").hide(); } catch (e) { /* ignore */ }
	if (!document.getElementById("jew-punch-audit-fonts")) {
		const l = document.createElement("link");
		l.id = "jew-punch-audit-fonts"; l.rel = "stylesheet";
		l.href = "https://fonts.googleapis.com/css2?family=Geist:wght@300;400;450;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
		document.head.appendChild(l);
	}
	const host = document.createElement("div");
	host.className = "jew-punch-audit-host";
	host.style.cssText = "display:block";
	$(host).appendTo(page.main);
	const root = host.attachShadow({ mode: "open" });
	root.innerHTML = "<style>" + CSS + CSS_RP + "</style>" + HTML;
	const $$ = (id) => root.getElementById(id);
	const qa = (s) => Array.from(root.querySelectorAll(s));

	// ---- DUX thin-stroke icon set (24x24, 1.7 stroke) - DS glyphs + a few added in the same style
	const PATHS = {
		filter: '<path d="M3 5h18l-7 8v6l-4-2v-4z"/>',
		clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
		building: '<path d="M5 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16"/><path d="M15 9h3a1 1 0 0 1 1 1v11"/><path d="M9 8h2M9 12h2M9 16h2"/><path d="M3 21h18"/>',
		calendar: '<rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
		box: '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
		search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
		moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
		sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>',
		chevron: '<path d="m15 18-6-6 6-6"/>',
		layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
		refresh: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 4v4h-4"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 20v-4h4"/>',
		check: '<path d="M4 12.5 9 17.5 20 6.5"/>',
		close: '<path d="M6 6l12 12M18 6 6 18"/>',
		download: '<path d="M12 4v11"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/>',
		user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
		alert: '<path d="M12 3.5 21.5 20h-19z"/><path d="M12 10v4.5M12 17.2v.1"/>',
		phone: '<rect x="7" y="2.8" width="10" height="18.4" rx="2.4"/><path d="M10.8 18h2.4"/>',
		pin: '<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.4"/>',
		camera: '<path d="M3.5 8.5h3.2l1.6-2.5h7.4l1.6 2.5h3.2v11h-17z"/><circle cx="12" cy="13.5" r="3.6"/>',
		info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.8v.1"/>',
		userx: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/><path d="M17 8.5l4 4M21 8.5l-4 4"/>',
		report: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5"/><path d="M9 13h7M9 17h5"/>',
		leave: '<rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/><path d="m9 15 2 2 4-4"/>'
	};
	const ic = (n, s) => '<svg viewBox="0 0 24 24" width="' + (s || 16) + '" height="' + (s || 16) + '" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' + (PATHS[n] || PATHS.info) + '</svg>';
	const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
	const pad = (n) => String(n).padStart(2, "0");
	const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
	const parseD = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ""); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(); };
	const fmtDs = (d) => d.getDate() + " " + MON[d.getMonth()];
	const isoOf = (d) => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
	const fmtT = (m) => pad(Math.floor(m / 60)) + ":" + pad(m % 60);
	const ini = (n) => String(n || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
	const MONF = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
	const todayStr = () => isoOf(new Date());
	const curMonth = () => todayStr().slice(0, 7);
	const monthLabel = (ym) => { const m = /^(\d{4})-(\d{2})$/.exec(ym || ""); return m ? MONF[+m[2] - 1] + " " + m[1] : ""; };
	const yesterdayStr = () => { const d = new Date(); d.setDate(d.getDate() - 1); return isoOf(d); };

	// static icons in the shell
	qa("[data-i]").forEach((el) => { const chev = el.classList.contains("chev"); el.innerHTML = ic(el.dataset.i, chev ? 14 : 16); if (!chev) el.style.display = "grid"; });
	root.querySelectorAll("img[data-mark]").forEach((im) => { im.src = im.dataset.mark === "k" ? MARK_L : MARK_W; });

	// ---- state
	let DATA = { punches: [], face_limit: 0.55, retention_days: 90, liveness_mode: "off", capture_since: null, range: {} };
	let NPD = null, EXD = null, exLoading = false, ROWS = [], EMP = {}, loading = true, loadError = null, loadedAt = 0, seq = 0;
	let RPD = null, rpLoading = false; const rpCache = {};
	const S = { range: "month", month: curMonth(), from: "", to: "", q: "", dept: "", loc: "", type: "", src: "", dev: "", onlyFlag: false, onlyPhoto: false, tab: "mon", kpi: null, anim: true, empView: null, npLogin: true, npHide: false, exChip: "all", exView: "tbl", exEmp: null,
		rp: "month", rpMonth: curMonth(), rpYear: String(new Date().getFullYear()), rpView: "reg", rpEmp: null, lvChip: "all", lvSt: "all" };
	// Deep link (DUX portal cards): /desk/punch-audit?tab=rep (Attendance report) or ?tab=lv (Leave & on duty), optional &period=year
	const TABS = ["mon", "np", "ex", "rep", "lv", "sus", "all", "dev", "emp"];
	let urlTabSeen = "";
	function applyUrlTab() {
		let p; try { p = new URLSearchParams(window.location.search); } catch (e) { return false; }
		const ro = (window.frappe && frappe.route_options) || {}, t = p.get("tab") || ro.tab || "", per = p.get("period") || ro.period || "";
		const key = t + "|" + per;
		if (!TABS.includes(t) || key === urlTabSeen) return false;
		urlTabSeen = key; S.tab = t; S.kpi = null; S.empView = null; S.exEmp = null; S.rpEmp = null; S.anim = true;
		if (per === "year" || per === "month") S.rp = per;
		return true;
	}
	applyUrlTab();
	const cache = {};
	const TH = () => DATA.face_limit || 0.55;

	// ---- theme (follows the desk, with a local override)
	let localTheme = null;
	try { localTheme = localStorage.getItem("pa-theme"); } catch (e) { /* ignore */ }
	function applyTheme() {
		const t = localTheme || (document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light");
		host.setAttribute("data-theme", t);
		$$("themeIc").innerHTML = ic(t === "dark" ? "sun" : "moon", 16);
	}
	new MutationObserver(() => { if (!localTheme) applyTheme(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

	// ---- data
	function rangeArgs() {
		const a = { range: S.range };
		if (S.range === "month" && S.month !== curMonth()) { a.range = "custom"; a.from_date = S.from; a.to_date = S.to; }
		if (S.range === "custom") { a.from_date = S.from; a.to_date = S.to; }
		return a;
	}
	function callApi(method, args) {
		return new Promise((resolve) => {
			frappe.call({ method: API + method, args: args || {}, callback: (r) => resolve({ ok: true, data: r.message }), error: () => resolve({ ok: false }) });
		});
	}
	function prepare() {
		ROWS = DATA.punches || [];
		EMP = {};
		ROWS.forEach((p) => {
			p.sev = Math.max(0, ...p.flags.map((f) => f.sev || 0));
			p.d = parseD(p.date);
			if (!EMP[p.employee]) EMP[p.employee] = { id: p.employee, name: p.name || p.employee, dept: p.dept || "" };
		});
	}
	function fillSelects() {
		const opt = (el, first, vals, cur) => { el.innerHTML = '<option value="">' + first + '</option>' + vals.map((v) => '<option' + (v === cur ? " selected" : "") + '>' + esc(v) + '</option>').join(""); };
		const uniq = (k) => Array.from(new Set(ROWS.map((p) => p[k]).filter(Boolean))).sort();
		opt($$("fDept"), "All departments", Array.from(new Set(uniq("dept").concat(((NPD && NPD.employees) || []).map((e) => e.dept).filter(Boolean)).concat(((RPD && RPD.employees) || []).map((e) => e.dept).filter(Boolean)))).sort(), S.dept);
		opt($$("fLoc"), "All locations", uniq("loc"), S.loc);
		opt($$("fSrc"), "All sources", uniq("source"), S.src);
	}
	async function loadData(force) {
		const key = JSON.stringify(rangeArgs());
		if (!force && cache[key] && Date.now() - cache[key].at < 60000) { DATA = cache[key].data; NPD = cache[key].np; EXD = cache[key].ex || null; if (!EXD) startEx(key); prepare(); syncSettings(); loading = false; loadError = null; fillSelects(); S.anim = true; render(); return; }
		const my = ++seq; loading = true; loadError = null; render();
		const [r, nr] = await Promise.all([callApi("get_punch_audit", rangeArgs()), callApi("get_not_punched", rangeArgs())]);
		if (my !== seq) return;
		loading = false;
		if (!r.ok || !r.data) { loadError = "Could not load punches. You may not have access to this page, or the server is busy."; render(); return; }
		DATA = r.data; NPD = nr.ok && nr.data ? nr.data : null; cache[key] = { at: Date.now(), data: DATA, np: NPD }; EXD = null; startEx(key); loadedAt = Date.now();
		prepare(); syncSettings(); fillSelects(); S.anim = true; render();
		if (!RPD && !rpLoading) loadReport();
	}
	// Attendance report (month-wise / year-wise) - one call feeds both the "Attendance report" and "Leave & on duty" tabs
	const rpArgs = () => S.rp === "year" ? { period: "year", year: S.rpYear } : { period: "month", month: S.rpMonth };
	const rpKey = () => JSON.stringify(rpArgs());
	async function loadReport(force) {
		const key = rpKey();
		if (!force && rpCache[key] && Date.now() - rpCache[key].at < 60000) { RPD = rpCache[key].data; rpLoading = false; fillSelects(); S.anim = true; render(); return; }
		rpLoading = true; RPD = null; render();
		const r = await callApi("get_attendance_report", rpArgs());
		if (key !== rpKey()) return;
		rpLoading = false; RPD = r.ok && r.data ? r.data : null;
		if (RPD) rpCache[key] = { at: Date.now(), data: RPD };
		fillSelects(); S.anim = true; render();
	}

	// ---- filtering
	function baseList() {
		const q = S.q.trim().toLowerCase(), dv = S.dev.trim().toLowerCase();
		return ROWS.filter((p) => {
			if (q && !((p.name || "").toLowerCase().includes(q) || (p.employee || "").toLowerCase().includes(q))) return false;
			if (S.dept && p.dept !== S.dept) return false;
			if (S.loc && (p.loc || "") !== S.loc) return false;
			if (S.type && p.type !== S.type) return false;
			if (S.onlyPhoto && !p.photo) return false;
			if (S.src && p.source !== S.src) return false;
			if (dv && !((p.dev || "").toLowerCase().includes(dv) || (p.ip || "").toLowerCase().includes(dv))) return false;
			return true;
		});
	}
	// Why a punch has no photo: made before capture started / deleted after the retention period / never saved.
	const retDays = () => DATA.retention_days || 90;
	const ageDays = (p) => Math.floor((Date.now() - parseD(p.date).getTime()) / 864e5);
	const inCaptureEra = (p) => !!DATA.capture_since && p.date >= String(DATA.capture_since).slice(0, 10);
	function noPhotoText(p, short) {
		if (p.source === "HR Manual") return short ? "HR-entered, no photo" : "HR-entered punch, no photo";
		if (inCaptureEra(p)) return ageDays(p) > retDays() ? (short ? "Photo deleted after " + retDays() + " days" : "Photo deleted after the " + retDays() + "-day retention period.") : (short ? "No photo saved" : "No photo was saved for this punch.");
		return short ? "No photo stored" : "No photo stored. This punch was made before photo capture started.";
	}
	const isShared = (p) => p.flags.some((f) => f.k === "SHARED");
	const isDrift = (p) => p.flags.some((f) => f.k === "DRIFT");
	const clockTxt = (p) => p.drift == null ? "—" : Math.abs(p.drift) >= 120 ? (p.drift > 0 ? "+" : "") + Math.round(p.drift / 60) + " min" : (p.drift > 0 ? "+" : "") + p.drift + " s";
	const dayLabel = (p) => p.date === todayStr() ? "Today" : p.date === yesterdayStr() ? "Yesterday" : fmtDs(p.d);

	// ---- DS markup helpers
	const pill = (icon, label, value, tint, num) => '<span class="fpill"><span class="tile t-' + (tint || "mut") + '">' + ic(icon, 13) + '</span>' + (label ? '<span class="k">' + label + ':</span>' : "") + '<span class="v' + (num ? " n" : "") + '">' + esc(value) + '</span></span>';
	const tag = (t, label) => '<span class="tag ' + t + '">' + label + '</span>';
	function flagTag(f) {
		if (f.k === "MANUAL") return tag("draft", "HR manual");
		if (f.k === "SHARED") return tag("err", "Shared device · " + f.n + " employees");
		if (f.k === "NEWDEV") return tag("pending", "New device");
		if (f.k === "DRIFT") { const m = Math.round(f.v / 60); return tag(Math.abs(f.v) >= 600 ? "err" : "pending", "Clock " + (m > 0 ? "+" : "") + m + " min"); }
		if (f.k === "FLAT") return tag("pending", "Flat face score");
		return tag("pending", "GPS at centre");
	}
	const photoUrl = (p) => "/api/method/" + API + "get_punch_photo?name=" + encodeURIComponent(p.audit) + "&kind=punch";
	const faceUrl = (p) => "/api/method/" + API + "get_punch_photo?kind=face&employee=" + encodeURIComponent(p.employee);
	const thumb = (p) => p.photo ? '<div class="thumb"><img src="' + photoUrl(p) + '" loading="lazy" alt=""></div>' : '<div class="thumb ini">' + esc(ini(p.name)) + '</div>';

	// ---- views
	function filterPills() {
		const out = [];
		const rl = { today: "Today", yesterday: "Yesterday", "7d": "Last 7 days", month: "This month", "30d": "Last 30 days" };
		if (S.range === "custom") out.push(pill("calendar", "Date", fmtDs(parseD(S.from)) + " – " + fmtDs(parseD(S.to)), "cyan", true));
		else if (S.range === "month" && S.month !== curMonth()) out.push(pill("calendar", "Month", monthLabel(S.month), "cyan"));
		else out.push(pill("calendar", "Date", (rl[S.range] || "") + " · " + fmtDs(parseD(S.from)) + (S.from !== S.to ? " – " + fmtDs(parseD(S.to)) : ""), "cyan"));
		if (S.empView && S.tab === "mon") out.push(pill("user", "Employee", (EMP[S.empView] ? EMP[S.empView].name : S.empView), "iris"));
		if (S.q) out.push(pill("user", "Employee", S.q, "iris"));
		if (S.dept) out.push(pill("building", "Department", S.dept, "iris"));
		if (S.loc) out.push(pill("pin", "Location", S.loc, "iris"));
		if (S.type) out.push(pill("clock", "Punch type", S.type, "pending"));
		if (S.src) out.push(pill("layers", "Source", S.src, "iris"));
		if (S.dev) out.push(pill("phone", "Device or IP", S.dev, "iris", true));
		if (S.onlyFlag) out.push(pill("alert", "Show", "Flagged only", "pending"));
		if (S.onlyPhoto) out.push(pill("camera", "Show", "With photo only", "iris"));
		if (S.kpi) out.push(pill("filter", "Focus", { flag: "Flagged punches", shared: "Shared devices", drift: "Clock drift" }[S.kpi] || "", "pending"));
		if (DATA.viewer && DATA.viewer.scope === "team") out.push(pill("user", "Scope", "Your team · " + DATA.viewer.team_size + " employees", "iris"));
		else if (out.length === 1) out.push(pill("box", "Scope", "All employees and sources", "iris"));
		return out.join("");
	}
	function kpiBlock(list) {
		const emps = new Set(list.map((p) => p.employee)).size, flagged = list.filter((p) => p.flagged).length;
		const sharedDev = new Set(list.filter(isShared).map((p) => p.date + "|" + p.dev)).size, drift = list.filter(isDrift).length;
		const fl = list.filter((p) => p.face != null), avg = fl.length ? fl.reduce((a, p) => a + p.face, 0) / fl.length : 0;
		const K = [
			{ k: "all", l: "Punches", v: list.length, s: emps + (emps === 1 ? " employee" : " employees"), c: "" },
			{ k: "emp", l: "Employees", v: emps, s: "with at least one punch", c: "" },
			{ k: "flag", l: "Flagged", v: flagged, s: list.length ? Math.round(flagged / list.length * 100) + "% of punches" : "—", c: "err", click: 1 },
			{ k: "shared", l: "Shared devices", v: sharedDev, s: "one phone, several people", c: "err", click: 1 },
			{ k: "drift", l: "Clock drift", v: drift, s: "phone clock off by 2 min+", c: "pending", click: 1 },
			{ k: "face", l: "Avg face distance", v: avg ? avg.toFixed(2) : "—", s: avg ? "limit " + TH().toFixed(2) + " · lower is closer" : "captured on new punches", c: "ok" }
		];
		$$("kpis").innerHTML = K.map((x) => '<' + (x.click ? 'button type="button"' : "div") + ' class="card kpi' + (x.click ? " click" : "") + (S.kpi === x.k ? " active" : "") + '" data-k="' + x.k + '"><span class="lbl">' + x.l + '</span><span class="v ' + x.c + '">' + x.v + '</span><span class="s">' + x.s + '</span></' + (x.click ? "button" : "div") + '>').join("");
		let lead = list.length ? 'Found <span class="num">' + list.length + '</span> ' + (list.length === 1 ? "punch" : "punches") + ' from <span class="num">' + emps + '</span> ' + (emps === 1 ? "employee" : "employees") + '. <span class="num">' + flagged + '</span> ' + (flagged === 1 ? "is" : "are") + ' flagged for a closer look. The filters that produced this are shown below.' : (loading ? "Reading punch data…" : "No punches matched those filters. You can widen the date range or clear a filter.");
		$$("lead").innerHTML = lead;
		const note = $$("capnote");
		if (!DATA.capture_since) note.innerHTML = ic("info", 14) + '<span>Capture has just been switched on. Photos, devices and IP addresses appear for punches made from now on; earlier punches show time and location only.</span>';
		else note.innerHTML = ic("info", 14) + '<span>Photos, devices and IP addresses are captured from <b class="mono">' + esc(fmtDs(parseD(DATA.capture_since)) + " " + parseD(DATA.capture_since).getFullYear()) + '</b>. Earlier punches and HR-entered ones show time and location only.</span>';
	}
	function row(p) {
		const photo = thumb(p);
		const face = p.face == null ? '<span class="sub">—</span>' : '<span class="fv">' + p.face.toFixed(3) + '</span><div class="bar" style="--th:' + (TH() / 0.7 * 100) + '"><i class="' + (p.face > 0.4 ? "m" : "") + '" style="width:' + Math.min(100, p.face / 0.7 * 100) + '%"></i></div>';
		const dev = p.dev ? '<span class="id" style="font-size:12.5px">' + esc(p.dev) + '</span><div class="sub">' + esc(p.model || "Unknown phone") + '</div>' : '<span class="sub">—</span><div class="sub">' + (p.source === "HR Manual" ? "Added by HR" : "Not captured") + '</div>';
		const ip = p.ip ? '<span class="mono" style="font-size:12.5px;color:var(--text-secondary)">' + esc(p.ip) + '</span>' : '<span class="sub">—</span>';
		const clk = p.drift == null ? '<span class="clk good">—</span>' : '<span class="clk ' + (Math.abs(p.drift) >= 600 ? "bad" : Math.abs(p.drift) >= 120 ? "warn" : "good") + '">' + clockTxt(p) + '</span>';
		const flags = p.flags.length ? p.flags.map(flagTag).join("") : tag("ok", "Clean");
		const locCell = p.loc ? '<div style="font-weight:500">' + esc(p.loc) + '</div><div class="mono sub">' + (p.dist != null ? p.dist + " m of " + p.radius + " m" : "") + '</div>' : '<span class="sub">—</span>' + (p.added_by ? '<div class="sub">by ' + esc(p.added_by.split("@")[0]) + '</div>' : "");
		return '<tr data-id="' + esc(p.id) + '" tabindex="0"' + (p.reviewed ? ' class="rev"' : "") + '>'
			+ '<td class="c-photo">' + photo + '</td>'
			+ '<td class="c-emp emp"><b>' + esc(p.name) + '</b><span><span class="id" style="font-size:12px">' + esc(p.employee) + '</span> · ' + esc(p.dept) + '</span></td>'
			+ '<td class="c-punch"><span class="badge mono ' + (p.type === "IN" ? "cyan" : "iris") + '">' + esc(p.type) + '</span><span class="tm">' + fmtT(p.t) + '</span><span class="dtm">' + dayLabel(p) + ' · ' + esc(p.source) + '</span></td>'
			+ '<td class="c-loc loc">' + locCell + '</td>'
			+ '<td class="c-face">' + face + '</td><td class="c-dev">' + dev + '</td><td class="c-ip">' + ip + '</td><td class="c-clk">' + clk + '</td>'
			+ '<td class="c-flags"><div class="flags">' + flags + (p.reviewed ? tag("draft", "Reviewed") : "") + '</div></td></tr>';
	}
	const shell = (list, body) => '<div class="card accent' + (S.anim ? " turn" : "") + '"><div class="fbar"><div class="hd">' + ic("filter", 13) + 'Showing results for</div><div class="pills">' + filterPills() + '</div></div>' + body + '</div>';
	const emptyBody = (title, msg) => '<div class="empty"><img class="mascot" src="' + MASCOT + '" alt=""><h3>' + title + '</h3><p>' + msg + '</p></div>';
	function punchTable(list, flaggedMode) {
		const meta = '<div class="meta"><span class="a"><b>' + list.length + '</b> ' + (list.length === 1 ? "punch" : "punches") + (flaggedMode ? " flagged" : " found") + '</span><span class="b">Face distance limit <b class="num" style="font-weight:600">' + TH().toFixed(2) + '</b> · lower is a closer match</span></div>';
		if (!list.length) return shell(list, meta + emptyBody(flaggedMode ? "Nothing flagged in this view" : "No punches found", "Try widening the date range or clearing a filter."));
		return shell(list, meta + '<div class="tscroll"><table class="pt"><thead><tr><th></th><th>Employee</th><th>Punch</th><th>Location</th><th>Face distance</th><th>Device</th><th>IP address</th><th>Clock</th><th>Flags</th></tr></thead><tbody>' + list.map(row).join("") + '</tbody></table></div>');
	}
	function deviceView(list) {
		const m = {};
		list.forEach((p) => {
			if (!p.dev) return;
			const o = m[p.dev] = m[p.dev] || { id: p.dev, model: p.model, os: p.os, emps: new Set(), n: 0, first: "9999", last: "0000", shared: false, newdev: false };
			o.emps.add(p.employee); o.n++; if (p.date < o.first) o.first = p.date; if (p.date > o.last) o.last = p.date;
			if (isShared(p)) o.shared = true; if (p.flags.some((f) => f.k === "NEWDEV")) o.newdev = true;
		});
		const arr = Object.values(m).sort((a, b) => (b.shared - a.shared) || (b.newdev - a.newdev) || b.emps.size - a.emps.size);
		const meta = '<div class="meta"><span class="a"><b>' + arr.length + '</b> devices</span><span class="b">Devices are identified by an install ID the app creates on each phone</span></div>';
		if (!arr.length) return shell(list, meta + emptyBody("No devices in this view", "Device data is captured on new punches, so older dates will not list any."));
		return shell(list, meta + '<div class="tscroll"><table class="dt"><thead><tr><th>Device</th><th>Employees</th><th>First seen</th><th>Last seen</th><th>Punches</th><th>Status</th></tr></thead><tbody>'
			+ arr.map((o) => {
				const names = Array.from(o.emps).map((id) => (EMP[id] ? EMP[id].name : id).split(" ")[0]).join(", ");
				const st = o.shared ? tag("err", "Shared") : o.newdev ? tag("pending", "New") : tag("ok", "OK");
				return '<tr><td><span class="id">' + esc(o.id) + '</span><div class="sub">' + esc((o.model || "Unknown phone") + (o.os ? " · " + o.os : "")) + '</div></td>'
					+ '<td><span class="num">' + o.emps.size + '</span> <span class="sub">· ' + esc(names) + '</span></td>'
					+ '<td class="dtm">' + fmtDs(parseD(o.first)) + '</td><td class="dtm">' + fmtDs(parseD(o.last)) + '</td><td><span class="num">' + o.n + '</span></td><td>' + st + '</td></tr>';
			}).join("") + '</tbody></table></div>');
	}
	const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
	const durTxt = (m) => m <= 0 ? "" : Math.floor(m / 60) + " h " + pad(m % 60) + " m";
	function dayHalf(p, kind, date) {
		const bd = '<span class="badge mono ' + (kind === "IN" ? "cyan" : "iris") + '">' + kind + '</span>';
		if (!p) return '<div class="dhalf none"><div class="ph">' + bd + '<span>' + (kind === "OUT" && date === todayStr() ? "No OUT yet" : "No " + kind + " punch") + '</span></div></div>';
		const note = noPhotoText(p, true);
		const img = p.photo ? '<img data-zoom="1" src="' + photoUrl(p) + '" loading="lazy" alt="' + kind + ' photo of ' + esc(p.name) + '" onerror="this.parentNode.classList.remove(&quot;has&quot;);this.remove()">' : "";
		const loc = p.loc ? esc(p.loc) + (p.dist != null ? " · " + p.dist + " m" : "") : (p.added_by ? "by " + esc(p.added_by.split("@")[0]) : "No location");
		const face = p.face != null ? '<div class="mt2">Face distance <span class="num">' + p.face.toFixed(3) + '</span></div>' : "";
		const fl = p.flags.length || p.reviewed ? '<div class="flags">' + p.flags.map(flagTag).join("") + (p.reviewed ? tag("draft", "Reviewed") : "") + '</div>' : "";
		return '<div class="dhalf" data-id="' + esc(p.id) + '" tabindex="0" role="button" aria-label="' + kind + ' punch at ' + fmtT(p.t) + ', open details"><div class="ph' + (p.photo ? " has" : "") + '">' + bd + img + '<span>' + (p.photo ? "Photo unavailable" : note) + '</span></div>'
			+ '<div class="tm2">' + fmtT(p.t) + '</div><div class="mt2">' + loc + '</div>' + face + fl + '</div>';
	}
	function dayCardsView(list) {
		const e = EMP[S.empView] || { id: S.empView, name: S.empView, dept: "" };
		const mine = list.filter((p) => p.employee === S.empView), byDay = {};
		mine.forEach((p) => { (byDay[p.date] = byDay[p.date] || []).push(p); });
		const dates = Object.keys(byDay).sort().reverse();
		const title = S.range === "month" ? monthLabel(S.month) : fmtDs(parseD(S.from)) + " – " + fmtDs(parseD(S.to));
		const withPhoto = mine.filter((p) => p.photo).length;
		const head = '<div class="empH"><button type="button" class="btn ghost sm" data-v="back">' + ic("chevron", 13) + 'Month summary</button><div class="thumb ini">' + esc(ini(e.name)) + '</div>'
			+ '<div class="emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span>' + (e.dept ? ' · ' + esc(e.dept) : "") + '</span></div><div class="sp"></div>'
			+ '<span class="sub">' + dates.length + (dates.length === 1 ? " day" : " days") + ' · ' + mine.length + (mine.length === 1 ? " punch" : " punches") + ' · ' + withPhoto + (withPhoto === 1 ? " photo" : " photos") + '</span>'
			+ '<button type="button" class="btn sm" data-v="table">' + ic("layers", 13) + 'All punches table</button></div>';
		const meta = '<div class="meta"><span class="a"><b>' + esc(title) + '</b> · IN and OUT side by side</span><span class="b">Select a photo to enlarge it, or the time to see the full evidence</span></div>';
		if (!dates.length) return shell(list, head + meta + emptyBody("No punches for " + esc(e.name), "There are no punches in " + esc(title) + ". Pick another month or clear a filter."));
		const cards = dates.map((d) => {
			const L = byDay[d].slice().sort((a, b) => a.t - b.t), pairs = [];
			L.forEach((p) => { const last = pairs[pairs.length - 1]; if (p.type === "OUT" && last && last.i && !last.o) last.o = p; else if (p.type === "OUT") pairs.push({ i: null, o: p }); else pairs.push({ i: p, o: null }); });
			const dt = parseD(d), nfl = L.filter((p) => p.flagged).length;
			let mins = 0; pairs.forEach((x) => { if (x.i && x.o) mins += x.o.t - x.i.t; });
			return '<div class="dcard' + (nfl ? " fl" : "") + '"><div class="dh"><b>' + DOW[dt.getDay()] + " " + fmtDs(dt) + '</b>' + (d === todayStr() ? tag("ok", "Today") : "") + (nfl ? tag("err", nfl + (nfl === 1 ? " flag" : " flags")) : "") + '<span class="sub mono">' + durTxt(mins) + '</span></div>'
				+ pairs.map((x) => '<div class="dpair">' + dayHalf(x.i, "IN", d) + dayHalf(x.o, "OUT", d) + '</div>').join("") + '</div>';
		}).join("");
		return shell(list, head + meta + '<div class="dgrid">' + cards + '</div>');
	}
	function dayList() {
		if (!S.from || !S.to) return null;
		const a = parseD(S.from), b = parseD(S.to), out = [];
		if ((b - a) / 864e5 > 30) return null;
		for (const d = new Date(a); d <= b; d.setDate(d.getDate() + 1)) out.push(isoOf(d));
		return out;
	}
	function dayStrip(o, days) {
		return '<div class="mstrip">' + days.map((d) => {
			const x = o.day[d], dt = parseD(d);
			return '<i class="' + (x ? (x.fl ? "fl" : "on") : (dt.getDay() === 0 ? "off" : "")) + (d === todayStr() ? " today" : "") + '" title="' + esc(fmtDs(dt) + " · " + (x ? x.n + (x.n === 1 ? " punch" : " punches") : "no punches")) + '"></i>';
		}).join("") + '</div>';
	}
	function monthView(list) {
		const days = dayList(), m = {};
		list.forEach((p) => {
			const o = m[p.employee] = m[p.employee] || { id: p.employee, n: 0, inn: 0, out: 0, man: 0, fl: 0, ph: 0, dev: new Set(), faces: [], day: {} };
			o.n++; if (p.photo) o.ph++; if (p.type === "IN") o.inn++; else o.out++;
			if (p.source === "HR Manual") o.man++;
			if (p.flagged) o.fl++;
			if (p.dev) o.dev.add(p.dev);
			if (p.face != null) o.faces.push(p.face);
			const d = o.day[p.date] = o.day[p.date] || { n: 0, fl: 0 }; d.n++; if (p.flagged) d.fl++;
		});
		const arr = Object.values(m).sort((a, b) => b.fl - a.fl || EMP[a.id].name.localeCompare(EMP[b.id].name));
		const title = S.range === "month" ? monthLabel(S.month) : fmtDs(parseD(S.from)) + " – " + fmtDs(parseD(S.to));
		const meta = '<div class="meta"><span class="a"><b>' + arr.length + '</b> ' + (arr.length === 1 ? "employee" : "employees") + ' · ' + esc(title) + '</span><span class="b">Select an employee to see their IN and OUT photos day by day</span></div>';
		if (!arr.length) return shell(list, meta + emptyBody("No punches in " + esc(title), "Pick another month or clear a filter."));
		const T = { n: 0, inn: 0, out: 0, man: 0, fl: 0, days: 0, ph: 0 };
		const body = arr.map((o) => {
			const e = EMP[o.id], nd = Object.keys(o.day).length, avg = o.faces.length ? (o.faces.reduce((a, b) => a + b, 0) / o.faces.length).toFixed(2) : "";
			T.n += o.n; T.ph += o.ph; T.inn += o.inn; T.out += o.out; T.man += o.man; T.fl += o.fl; T.days += nd;
			return '<tr class="mrow" data-emp="' + esc(e.id) + '" tabindex="0"><td class="emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span> · ' + esc(e.dept) + '</span></td>'
				+ '<td><span class="num">' + nd + '</span>' + (days ? '<span class="sub"> / ' + days.length + '</span>' : "") + '</td>'
				+ '<td><span class="num">' + o.n + '</span></td><td><span class="num">' + o.inn + '</span></td><td><span class="num">' + o.out + '</span></td>'
				+ '<td>' + (o.ph ? '<span class="num">' + o.ph + '</span><span class="sub"> / ' + o.n + '</span>' : '<span class="sub">—</span>') + '</td>'
				+ '<td>' + (o.man ? '<span class="num">' + o.man + '</span>' : '<span class="sub">—</span>') + '</td>'
				+ '<td>' + (o.dev.size ? '<span class="num">' + o.dev.size + '</span>' : '<span class="sub">—</span>') + '</td>'
				+ '<td>' + (avg ? '<span class="num">' + avg + '</span>' : '<span class="sub">—</span>') + '</td>'
				+ '<td>' + (o.fl ? tag("err", o.fl + (o.fl === 1 ? " flag" : " flags")) : tag("ok", "Clean")) + '</td>'
				+ (days ? '<td>' + dayStrip(o, days) + '</td>' : "") + '</tr>';
		}).join("");
		const tot = '<tr class="tot"><td><b>Total</b><span class="sub"> · ' + arr.length + (arr.length === 1 ? " employee" : " employees") + '</span></td><td><span class="num">' + T.days + '</span><span class="sub"> days</span></td><td><span class="num">' + T.n + '</span></td><td><span class="num">' + T.inn + '</span></td><td><span class="num">' + T.out + '</span></td><td><span class="num">' + T.ph + '</span></td><td><span class="num">' + T.man + '</span></td><td></td><td></td><td>' + (T.fl ? tag("err", T.fl + " flagged") : tag("ok", "Clean")) + '</td>' + (days ? '<td></td>' : "") + '</tr>';
		return shell(list, meta + '<div class="tscroll"><table class="mt"><thead><tr><th>Employee</th><th>Days punched</th><th>Punches</th><th>IN</th><th>OUT</th><th>Photos</th><th>HR manual</th><th>Devices</th><th>Avg face</th><th>Flags</th>' + (days ? '<th>Daily activity</th>' : "") + '</tr></thead><tbody>' + body + tot + '</tbody></table></div>');
	}
	function spark(emp) {
		const L = ROWS.filter((p) => p.employee === emp && p.face != null).sort((a, b) => (a.date + fmtT(a.t)).localeCompare(b.date + fmtT(b.t))).slice(-14);
		if (!L.length) return '<span class="sub">No face data yet</span>';
		const w = 8, g = 3, H = 34, W = L.length * (w + g), thy = Math.max(0, H - (TH() / 0.7 * H));
		return '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">' + L.map((p, i) => { const h = Math.max(3, p.face / 0.7 * H); return '<rect x="' + (i * (w + g)) + '" y="' + (H - h) + '" width="' + w + '" height="' + h + '" rx="2" fill="' + (p.face > 0.4 ? "var(--pending)" : "var(--ok)") + '"/>'; }).join("") + '<line x1="0" x2="' + W + '" y1="' + thy + '" y2="' + thy + '" stroke="var(--err)" stroke-width="1" stroke-dasharray="3 3"/></svg>';
	}
	function employeeView(list) {
		const m = {};
		list.forEach((p) => { const o = m[p.employee] = m[p.employee] || { id: p.employee, n: 0, dev: new Set(), fl: 0, faces: [] }; o.n++; if (p.dev) o.dev.add(p.dev); if (p.flagged) o.fl++; if (p.face != null) o.faces.push(p.face); });
		const arr = Object.values(m).sort((a, b) => b.fl - a.fl || b.n - a.n);
		const meta = '<div class="meta"><span class="a"><b>' + arr.length + '</b> employees</span><span class="b">Select an employee to see their IN and OUT photos day by day</span></div>';
		if (!arr.length) return shell(list, meta + emptyBody("No employees in this view", "Try widening the date range."));
		return shell(list, meta + '<div class="egrid">' + arr.map((o) => {
			const e = EMP[o.id], avg = o.faces.length ? (o.faces.reduce((a, b) => a + b, 0) / o.faces.length).toFixed(2) : "—";
			return '<div class="ecard" data-emp="' + esc(e.id) + '"><div class="hd"><div class="thumb ini">' + esc(ini(e.name)) + '</div><div class="emp" style="flex:1;min-width:0"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span> · ' + esc(e.dept) + '</span></div>' + (o.fl ? tag("err", o.fl + (o.fl === 1 ? " flag" : " flags")) : tag("ok", "Clean")) + '</div>'
				+ '<div class="st"><div><b>' + o.n + '</b><span>Punches</span></div><div><b>' + o.dev.size + '</b><span>Devices</span></div><div><b>' + avg + '</b><span>Avg face</span></div></div>'
				+ '<div><div class="lbl" style="margin-bottom:5px;font-size:10.5px">Face distance trend · red line is the limit</div>' + spark(o.id) + '</div></div>';
		}).join("") + '</div>');
	}
	// ---- Not punched tab: active employees with NO punch in the selected range (server: get_not_punched)
	const agoTxt = (n) => n == null ? "" : n <= 0 ? "today" : n === 1 ? "yesterday" : n + " days ago";
	const npRange = () => (NPD && NPD.range) || { from: S.from, to: S.to };
	const npIsToday = () => { const r = npRange(); return r.from === todayStr() && r.to === todayStr(); };
	const npWhen = () => { const r = npRange(); return r.from === r.to ? (r.from === todayStr() ? "today" : "on " + fmtDs(parseD(r.from))) : "between " + fmtDs(parseD(r.from)) + " and " + fmtDs(parseD(r.to)); };
	function npStats() {
		const q = S.q.trim().toLowerCase();
		const base = ((NPD && NPD.employees) || []).filter((e) => {
			if (q && !((e.name || "").toLowerCase().includes(q) || (e.id || "").toLowerCase().includes(q))) return false;
			if (S.dept && e.dept !== S.dept) return false;
			return true;
		});
		const pool = S.npLogin ? base.filter((e) => e.login) : base;
		const missing = pool.filter((e) => !e.covered), covered = pool.filter((e) => e.covered);
		return { base, pool, missing, covered, shown: S.npHide ? missing : pool };
	}
	function npKpiBlock() {
		const st = npStats(), b = st.base;
		const never = st.missing.filter((e) => !e.last).length, stale = st.missing.filter((e) => e.last && e.ago >= 7).length;
		const qq = S.q.trim().toLowerCase();
		const punched = new Set(ROWS.filter((p) => (!qq || (p.name || "").toLowerCase().includes(qq) || (p.employee || "").toLowerCase().includes(qq)) && (!S.dept || p.dept === S.dept)).map((p) => p.employee)).size;
		const K = [
			{ l: "Not punched", v: st.missing.length, s: S.npLogin ? "app users with no punch" : "active employees with no punch", c: "err" },
			{ l: "Accounted for", v: st.covered.length, s: "leave, on duty, marked or holiday", c: "pending" },
			{ l: "Never punched", v: never, s: "have not used the app yet", c: "" },
			{ l: "Silent 7+ days", v: stale, s: "last punch a week or more ago", c: "pending" },
			{ l: "Punched", v: punched, s: "at least one punch in the period", c: "ok" },
			{ l: "No app login", v: b.filter((e) => !e.login).length, s: "cannot punch from the app", c: "" }
		];
		$$("kpis").innerHTML = K.map((x) => '<div class="card kpi"><span class="lbl">' + x.l + '</span><span class="v ' + x.c + '">' + x.v + '</span><span class="s">' + x.s + '</span></div>').join("");
		const n = st.missing.length, cv = st.covered.length;
		$$("lead").innerHTML = NPD
			? '<span class="num">' + n + '</span> ' + (S.npLogin ? (n === 1 ? "app user has" : "app users have") : (n === 1 ? "employee has" : "employees have")) + ' no punch ' + npWhen() + '.' + (cv ? ' <span class="num">' + cv + '</span> more ' + (cv === 1 ? "is" : "are") + ' on leave, on duty, marked by HR or on a holiday.' : "") + ' The filters that produced this are shown below.'
			: "The not-punched list could not be loaded.";
	}
	function npRow(e) {
		const multi = (NPD.days || 1) > 1, today = npIsToday(), nd = (n) => multi ? " · " + n + (n === 1 ? " day" : " days") : "";
		const chips = [];
		if (!e.covered) chips.push(tag("err", today ? "Not punched yet" : "No punch"));
		if (e.leave) chips.push(tag("pending", "On leave" + nd(e.leave)));
		if (e.half) chips.push(tag("pending", "Half-day leave"));
		if (e.od) chips.push(tag("ok", "On duty" + nd(e.od)));
		if (e.marked) chips.push(tag("ok", "Attendance marked" + nd(e.marked)));
		if (e.hol && (!multi || e.covered)) chips.push(tag("draft", "Holiday" + nd(e.hol)));
		if (e.absent) chips.push(tag("err", "Marked absent" + nd(e.absent)));
		const lastD = e.last ? parseD(e.last) : null;
		return '<tr' + (e.covered ? ' class="cov"' : "") + '><td class="emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span> · ' + (e.dept ? esc(e.dept) : "No department") + '</span></td>'
			+ '<td>' + (e.desig ? esc(e.desig) : '<span class="sub">—</span>') + '</td>'
			+ '<td>' + (e.login ? tag("ok", "Has login") : tag("draft", "No login")) + '</td>'
			+ '<td>' + (lastD ? '<span class="dtm">' + fmtDs(lastD) + " " + lastD.getFullYear() + '</span><div class="sub">' + agoTxt(e.ago) + '</div>' : '<span class="sub">Never punched</span>') + '</td>'
			+ '<td class="chips">' + (chips.join("") || '<span class="sub">—</span>') + '</td></tr>';
	}
	function notPunchedView() {
		const r = npRange();
		const pills = [pill("calendar", "Date", r.from === r.to ? fmtDs(parseD(r.from)) + (npIsToday() ? " (today)" : "") : fmtDs(parseD(r.from)) + " – " + fmtDs(parseD(r.to)), "cyan", true)];
		if (S.q) pills.push(pill("user", "Employee", S.q, "iris"));
		if (S.dept) pills.push(pill("building", "Department", S.dept, "iris"));
		pills.push(pill("user", "Scope", S.npLogin ? "App users only" : "All active employees", "iris"));
		const wrap = (body) => '<div class="card accent' + (S.anim ? " turn" : "") + '"><div class="fbar"><div class="hd">' + ic("filter", 13) + 'Showing results for</div><div class="pills">' + pills.join("") + '</div></div>' + body + '</div>';
		if (!NPD) return wrap(emptyBody("Could not load this list", "The not-punched list is unavailable right now. Press Refresh to try again."));
		const st = npStats();
		const rows = st.shown.slice().sort((a, b) => (a.covered - b.covered) || ((a.dept ? 0 : 1) - (b.dept ? 0 : 1)) || (a.dept || "").localeCompare(b.dept || "") || a.name.localeCompare(b.name));
		const bar = '<div class="npbar"><button class="sw' + (S.npLogin ? " on" : "") + '" type="button" data-np="login"><i></i>App users only</button>'
			+ '<button class="sw' + (S.npHide ? " on" : "") + '" type="button" data-np="hide"><i></i>Hide leave, duty and holidays</button><span class="sp"></span>'
			+ (npIsToday() ? "" : '<button class="btn sm" type="button" data-np="today">' + ic("clock", 13) + 'Check today</button>') + '</div>';
		const asOf = NPD.generated_at ? String(NPD.generated_at).slice(11, 16) : "";
		const meta = '<div class="meta"><span class="a"><b>' + st.missing.length + '</b> not punched' + (st.covered.length ? ' · <b>' + st.covered.length + '</b> accounted for' : "") + '</span><span class="b">'
			+ (npIsToday() ? "As of " + esc(asOf) + " · people who have not punched yet today" : "People with no punch at all in this period") + '</span></div>';
		if (!rows.length) return wrap(bar + meta + emptyBody(st.pool.length ? "Nobody is missing" : "No one to show", st.pool.length ? "Everyone here has punched, or is on leave, on duty or on a holiday." : "Try turning off “App users only” or clearing a filter."));
		return wrap(bar + meta + '<div class="tscroll"><table class="dt np"><thead><tr><th>Employee</th><th>Designation</th><th>App login</th><th>Last punch</th><th>In this period</th></tr></thead><tbody>' + rows.map(npRow).join("") + '</tbody></table></div>');
	}
	// ---- Exceptions tab: late / early / regularization / on duty / leave, employee-wise and day by day (server: get_exceptions)
	const EX_CHIPS = [["all", "All"], ["late", "Late"], ["early", "Early"], ["reg", "Regularization"], ["od", "On duty"], ["leave", "Leave"], ["inc", "Incomplete"], ["pend", "Pending approvals"]];
	const EX_CODE = { P: "Present", HD: "Half day", A: "Absent", L: "On leave", "L?": "Leave applied", OD: "On duty", "OD?": "On duty requested", H: "Holiday", I: "IN without OUT" };
	const exMatch = (e, k) => { const c = e.c; return k === "all" || (k === "late" && (c.late_sys || c.late_punch)) || (k === "early" && (c.early_sys || c.early_punch)) || (k === "reg" && (c.reg || c.reg_p)) || (k === "od" && c.od_days) || (k === "leave" && (c.leave_days || c.leave_p)) || (k === "inc" && c.inc) || (k === "pend" && c.pend); };
	const nfx = (n) => n % 1 ? n.toFixed(1) : String(n);
	const exOneDay = () => !!EXD && EXD.days.length === 1;
	const exCellOf = (id, d) => (EXD && EXD.cells[id] && EXD.cells[id][d]) || null;
	function exBase() {
		const q = S.q.trim().toLowerCase();
		return ((EXD && EXD.employees) || []).filter((e) => (!q || (e.name || "").toLowerCase().includes(q) || (e.id || "").toLowerCase().includes(q)) && (!S.dept || e.dept === S.dept));
	}
	const exList = () => exBase().filter((e) => exMatch(e, S.exChip));
	const kTag = (k) => k === "ok" ? "ok" : k === "rej" ? "err" : "pending";
	const ageTxt = (x) => x.k === "pend" && x.age ? " · " + x.age + (x.age === 1 ? " day" : " days") : "";
	function exChips(c, d) {
		const out = [], soon = d === todayStr() && !c.out && !c.ls;
		if (c.late) out.push(tag(c.ls ? "err" : "pending", "Late " + c.late + " min" + (c.ls ? " · counted" : soon ? " · counted when OUT is marked" : " · not counted")));
		if (c.lah) out.push(tag("err", "Half Day for lateness"));
		if (c.lc) out.push(tag("pending", "Late Coming (past grace)"));
		if (c.early) out.push(tag(c.es ? "err" : "pending", "Early " + c.early + " min" + (c.es ? " · counted" : " · not counted")));
		if (c.i) out.push(tag("err", "IN without OUT" + (c.na ? " · no attendance" : "")));
		(c.lv || []).forEach((x) => out.push(tag(kTag(x.k), esc(x.t) + (x.h ? " (half day)" : "") + " · " + esc(x.st) + ageTxt(x)) + (x.cx ? tag("pending", "Cancellation requested") : "")));
		(c.le || []).forEach((x) => out.push(tag(kTag(x.k), esc(x.t) + (x.x ? " " + esc(x.x) : "") + " · " + esc(x.st) + ageTxt(x)) + (x.cx ? tag("pending", "Cancellation requested") : "")));
		(c.od || []).forEach((x) => out.push(tag(kTag(x.k), esc(x.t) + " · " + esc(x.st) + ageTxt(x))));
		(c.rg || []).forEach((x) => out.push(tag(kTag(x.k), esc(x.i) + " · " + esc(x.st))));
		return out;
	}
	function exStatus(e, d) {
		const c = exCellOf(e.id, d) || {}, code = c.s || "", isToday = d === todayStr();
		let label = EX_CODE[code] || (d > todayStr() ? "—" : isToday ? "Not punched yet" : "No punch"), t = code === "P" ? "ok" : (code === "A" || code === "I" || !code) ? "err" : code === "H" ? "draft" : "pending";
		if (code === "L?") label += " · " + esc(((c.lv || []).find((x) => x.k === "pend") || {}).st || "pending");
		if (code === "OD?") label += " · " + esc(((c.od || []).find((x) => x.k === "pend") || {}).st || "pending");
		if (code === "I" && isToday) label = "IN, no OUT yet";
		return tag(t, label);
	}
	const exEmpCell = (e) => '<td class="emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span> · ' + (e.dept ? esc(e.dept) : "No department") + '</span></td>';
	const exNum = (n, warn) => n ? '<span class="num"' + (warn ? ' style="color:var(--pending)"' : "") + '>' + nfx(n) + '</span>' : '<span class="sub">—</span>';
	function exKpiBlock() {
		const b = exBase(); let K;
		if (exOneDay()) {
			const d = EXD.days[0], codes = b.map((e) => (exCellOf(e.id, d) || {}).s || "");
			K = [{ l: "Present", v: codes.filter((x) => x === "P" || x === "HD").length, s: "punched or marked", c: "ok" },
				{ l: "Late", v: b.filter((e) => (exCellOf(e.id, d) || {}).late).length, s: "came after grace time", c: "pending" },
				{ l: "On leave", v: codes.filter((x) => x === "L").length, s: "approved leave", c: "" },
				{ l: "No punch", v: codes.filter((x) => !x).length, s: d === todayStr() ? "not punched yet" : "no record", c: "err" }];
		} else {
			const sum = (k) => b.reduce((a, e) => a + (e.c[k] || 0), 0);
			K = [{ l: "Late days", v: sum("late_punch"), s: "by punch time", c: "err" }, { l: "Early days", v: sum("early_punch"), s: "by punch time", c: "pending" },
				{ l: "Incomplete days", v: sum("inc"), s: "IN without OUT", c: "err" }, { l: "Pending approvals", v: sum("pend"), s: "waiting on someone", c: "pending" }];
		}
		$$("kpis").classList.add("k4");
		$$("kpis").innerHTML = K.map((x) => '<div class="card kpi"><span class="lbl">' + x.l + '</span><span class="v ' + x.c + '">' + x.v + '</span><span class="s">' + x.s + '</span></div>').join("");
		const r = EXD.days; const when = r.length === 1 ? (r[0] === todayStr() ? "today" : "on " + fmtDs(parseD(r[0]))) : "between " + fmtDs(parseD(r[0])) + " and " + fmtDs(parseD(r[r.length - 1]));
		$$("lead").innerHTML = 'Late, early, leave and pending requests for <span class="num">' + b.length + '</span> ' + (b.length === 1 ? "employee" : "employees") + ' ' + when + '.';
	}
	function exTableMulti(list) {
		const cnt = (n, sys) => n ? '<span class="num">' + n + '</span>' + (sys < n ? '<span class="sub"> · system ' + sys + '</span>' : "") : '<span class="sub">—</span>';
		const rows = list.slice().sort((a, b) => ((b.c.late_punch + b.c.early_punch + b.c.inc + b.c.pend) - (a.c.late_punch + a.c.early_punch + a.c.inc + a.c.pend)) || a.name.localeCompare(b.name)).map((e) => {
			const c = e.c;
			return '<tr data-ex="emp:' + esc(e.id) + '" tabindex="0">' + exEmpCell(e) + '<td>' + cnt(c.late_punch, c.late_sys) + '</td><td>' + cnt(c.early_punch, c.early_sys) + '</td>'
				+ '<td>' + (c.leave_days || c.leave_p ? (c.leave_days ? '<span class="num">' + nfx(c.leave_days) + '</span>' : "") + (c.leave_p ? ' ' + tag("pending", nfx(c.leave_p) + " pending") : "") : '<span class="sub">—</span>') + '</td>'
				+ '<td>' + exNum(c.inc, 1) + '</td><td>' + (c.pend ? tag("pending", c.pend + (c.pend === 1 ? " request" : " requests")) : '<span class="sub">—</span>') + '</td></tr>';
		}).join("");
		return '<div class="tscroll"><table class="dt xt"><thead><tr><th>Employee</th><th>Late days</th><th>Early days</th><th>Leave days</th><th>Incomplete days</th><th>Pending</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
	}
	function exTableDay(list) {
		const d = EXD.days[0];
		const rows = list.slice().sort((a, b) => a.name.localeCompare(b.name)).map((e) => {
			const c = exCellOf(e.id, d) || {};
			const det = exChips(c, d).join("");
			return '<tr data-ex="emp:' + esc(e.id) + '" tabindex="0">' + exEmpCell(e) + '<td>' + exStatus(e, d) + '</td><td><span class="dtm">' + (c.in || "—") + '</span></td><td><span class="dtm">' + (c.out || "—") + '</span></td><td class="chips">' + (det || '<span class="sub">—</span>') + '</td></tr>';
		}).join("");
		return '<div class="tscroll"><table class="dt xt"><thead><tr><th>Employee</th><th>Status</th><th>IN</th><th>OUT</th><th>Details</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
	}
	function exGrid(list) {
		const days = EXD.days, td = todayStr();
		const head = '<tr><th class="nm">Employee</th>' + days.map((d) => { const dt = parseD(d); return '<th class="' + (dt.getDay() === 0 ? "sun" : "") + '" title="' + esc(DOW[dt.getDay()] + " " + fmtDs(dt)) + '">' + dt.getDate() + '<br>' + DOW[dt.getDay()][0] + '</th>'; }).join("") + '</tr>';
		const rows = list.slice().sort((a, b) => a.name.localeCompare(b.name)).map((e) => '<tr><td class="nm emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span></span></td>' + days.map((d) => {
			const c = exCellOf(e.id, d); if (!c || (!c.s && !c.late)) return '<td></td>';
			const code = c.s || "", tip = fmtDs(parseD(d)) + " · " + (EX_CODE[code] || "") + (c.in ? " · IN " + c.in : "") + (c.out ? " · OUT " + c.out : "") + (c.late ? " · late " + c.late + " min" + (c.ls ? " (counted)" : " (not counted)") : "");
			return '<td><span class="xc c-' + code.replace("?", "Q") + '" data-ex="emp:' + esc(e.id) + '" title="' + esc(tip) + '">' + (code || "·") + (c.late ? '<i class="xd' + (c.ls || (d === td && !c.out) ? "" : " miss") + '"></i>' : "") + '</span></td>';
		}).join("") + '</tr>').join("");
		const leg = '<div class="xleg"><span><span class="xc c-P">P</span> Present</span><span><span class="xc c-A">A</span> Absent</span><span><span class="xc c-L">L</span> Leave</span><span><span class="xc c-OD">OD</span> On duty</span><span><span class="xc c-H">H</span> Holiday</span><span><span class="xc c-I">I</span> IN without OUT</span><span>Dashed box = request pending · Dot = late</span></div>';
		return '<div class="tscroll"><table class="xgrid"><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div>' + leg;
	}
	function exPending() {
		const byId = {}; ((EXD && EXD.employees) || []).forEach((e) => { byId[e.id] = e; });
		const q = S.q.trim().toLowerCase();
		const items = EXD.pending.filter((p) => { const e = byId[p.emp]; if (!e) return false; if (q && !((e.name || "").toLowerCase().includes(q) || e.id.toLowerCase().includes(q))) return false; return !S.dept || e.dept === S.dept; });
		if (!items.length) return emptyBody("Nothing is waiting", "No leave, late/early, on-duty or regularization request is waiting for an approval in this view.");
		const rows = items.map((p) => { const e = byId[p.emp]; const dt = p.from === p.to ? fmtDs(parseD(p.from)) : fmtDs(parseD(p.from)) + " – " + fmtDs(parseD(p.to));
			return '<tr data-ex="emp:' + esc(e.id) + '" tabindex="0">' + exEmpCell(e) + '<td><b style="font-weight:500">' + esc(p.kind) + '</b><div class="sub">' + esc(p.what) + '</div></td><td><span class="dtm">' + dt + '</span></td><td>' + tag("pending", esc(p.stage)) + '</td><td>' + (p.age ? '<span class="num"' + (p.age >= 7 ? ' style="color:var(--err)"' : "") + '>' + p.age + '</span> <span class="sub">' + (p.age === 1 ? "day" : "days") + '</span>' : '<span class="sub">today</span>') + '</td></tr>'; }).join("");
		return '<div class="tscroll"><table class="dt xt"><thead><tr><th>Employee</th><th>Request</th><th>Dates</th><th>Waiting for</th><th>Waiting since</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
	}
	function exCalendar(e) {
		const c = e.c, td = todayStr();
		const head = '<div class="empH"><button type="button" class="btn ghost sm" data-ex="back">' + ic("chevron", 13) + 'Exceptions</button><div class="thumb ini">' + esc(ini(e.name)) + '</div>'
			+ '<div class="emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span>' + (e.dept ? ' · ' + esc(e.dept) : "") + '</span></div><div class="sp"></div>'
			+ '<span class="sub">Late ' + c.late_punch + ' · Early ' + c.early_punch + ' · Leave ' + nfx(c.leave_days) + ' d · Incomplete ' + c.inc + (c.pend ? ' · ' + c.pend + ' pending' : "") + '</span></div>';
		const boxes = EXD.days.filter((d) => d <= td || exCellOf(e.id, d)).map((d) => {
			const x = exCellOf(e.id, d) || {}, dt = parseD(d), code = x.s || "", off = code === "H" || (!code && dt.getDay() === 0);
			const times = x.in || x.out ? '<div class="tm">IN ' + (x.in || "—") + ' · OUT ' + (x.out || "—") + '</div>' : "";
			const bad = x.i || (x.late && x.ls) || x.lah;
			return '<div class="xday' + (off ? " off" : "") + (bad ? " fl" : "") + '"><div class="hd"><b>' + DOW[dt.getDay()] + " " + fmtDs(dt) + '</b>' + (d === td ? tag("ok", "Today") : "") + '<span class="sp" style="flex:1"></span>' + (code ? '<span class="xc c-' + code.replace("?", "Q") + '" style="cursor:default">' + code + '</span>' : "") + '</div>'
				+ (code ? '<div class="sub">' + esc(EX_CODE[code] || "") + '</div>' : (d < td ? '<div class="sub">No record</div>' : "")) + times + '<div class="tg">' + exChips(x, d).join("") + '</div></div>';
		}).join("");
		const title = EXD.days.length === 1 ? fmtDs(parseD(EXD.days[0])) : fmtDs(parseD(EXD.days[0])) + " – " + fmtDs(parseD(EXD.days[EXD.days.length - 1]));
		return head + '<div class="meta"><span class="a"><b>' + esc(title) + '</b> · day by day</span><span class="b">Status, IN / OUT, late and early, and every request with its approval stage</span></div><div class="xcal">' + boxes + '</div>';
	}
	function exView() {
		if (!EXD) return '<div class="card accent turn"><div class="empty"><img class="mascot" src="' + MASCOT + '" alt=""><h3>' + (exLoading ? '<span class="shim">Reading attendance exceptions…</span>' : "Could not load exceptions") + '</h3><p>' + (exLoading ? "Collecting leave, late/early, on duty and regularization for the selected dates." : "Press Refresh to try again.") + '</p></div></div>';
		const r = EXD.days, one = r.length === 1;
		const pills = [pill("calendar", "Date", one ? fmtDs(parseD(r[0])) + (r[0] === todayStr() ? " (today)" : "") : fmtDs(parseD(r[0])) + " – " + fmtDs(parseD(r[r.length - 1])), "cyan", true)];
		if (S.q) pills.push(pill("user", "Employee", S.q, "iris"));
		if (S.dept) pills.push(pill("building", "Department", S.dept, "iris"));
		if (S.exChip !== "all") pills.push(pill("filter", "Show", (EX_CHIPS.find((x) => x[0] === S.exChip) || [])[1], "pending"));
		const wrap = (body) => '<div class="card accent' + (S.anim ? " turn" : "") + '"><div class="fbar"><div class="hd">' + ic("filter", 13) + 'Showing results for</div><div class="pills">' + pills.join("") + '</div></div>' + body + '</div>';
		const emp = S.exEmp ? ((EXD.employees || []).find((e) => e.id === S.exEmp)) : null;
		if (emp) return wrap(exCalendar(emp));
		const base = exBase(), list = exList(), canGrid = r.length > 1 && r.length <= 31, mode = S.exView === "grid" && canGrid ? "grid" : "tbl";
		const bar = '<div class="xbar">' + EX_CHIPS.map((x) => '<button type="button" class="xchip' + (S.exChip === x[0] ? " on" : "") + '" data-ex="chip:' + x[0] + '">' + x[1] + ' <span class="n">' + base.filter((e) => exMatch(e, x[0])).length + '</span></button>').join("")
			+ '<span class="sp"></span>' + (canGrid ? '<span class="xseg"><button type="button" class="' + (mode === "tbl" ? "on" : "") + '" data-ex="view:tbl">Summary</button><button type="button" class="' + (mode === "grid" ? "on" : "") + '" data-ex="view:grid">Day-wise</button></span>' : "")
			+ (one && r[0] === todayStr() ? "" : '<button class="btn sm" type="button" data-np="today">' + ic("clock", 13) + 'Check today</button>') + '</div>';
		const meta = '<div class="meta"><span class="a"><b>' + (S.exChip === "pend" ? EXD.pending.length : list.length) + '</b> ' + (S.exChip === "pend" ? "requests waiting" : list.length === 1 ? "employee" : "employees") + '</span><span class="b">' + (one ? "Status for the day · select an employee for the month" : mode === "grid" ? "Each box is one day · select a box for that employee’s calendar" : "Select an employee for the day-by-day calendar") + '</span></div>';
		let body;
		if (S.exChip === "pend") body = exPending();
		else if (!list.length) body = emptyBody("Nothing in this view", "Try another filter chip, a wider date range, or clear a filter.");
		else body = one ? exTableDay(list) : mode === "grid" ? exGrid(list) : exTableMulti(list);
		return wrap(bar + meta + body);
	}
	function startEx(key) {
		exLoading = true; const args = rangeArgs();
		callApi("get_exceptions", args).then((r) => {
			if (key !== JSON.stringify(rangeArgs())) return;
			exLoading = false; EXD = r.ok && r.data ? r.data : null;
			if (cache[key]) cache[key].ex = EXD;
			render();
		});
	}
	// ---- Attendance report tab (Mark IN / OUT, month-wise or year-wise) + Leave & on duty tab (server: get_attendance_report)
	const RP_CODE = { P: "Present", HD: "Half day", A: "Absent", L: "Leave", OD: "On duty", H: "Holiday", WO: "Week off" };
	const isRp = () => S.tab === "rep" || S.tab === "lv";
	const hrs = (m) => m ? Math.floor(m / 60) + ":" + pad(m % 60) : "";
	const pdays = (c) => (c.p || 0) + (c.od || 0) + (c.hd || 0) / 2;
	const rpLabel = () => RPD ? RPD.label : (S.rp === "year" ? S.rpYear : monthLabel(S.rpMonth));
	function rpBase() {
		const q = S.q.trim().toLowerCase();
		return ((RPD && RPD.employees) || []).filter((e) => (!q || (e.name || "").toLowerCase().includes(q) || (e.id || "").toLowerCase().includes(q)) && (!S.dept || e.dept === S.dept));
	}
	const rpEmpOf = (id) => ((RPD && RPD.employees) || []).find((e) => e.id === id) || null;
	const rpChip = (code, x) => '<span class="xc c-' + (code || "none") + '" style="cursor:inherit">' + (code || "·") + (x && x.late ? '<i class="xd"></i>' : "") + '</span>';
	function rpNote(x) {
		const b = [];
		if (x.lt) b.push(x.lt);
		if (x.s === "HD" && x.hr) b.push(x.hr);
		if (x.od) b.push(x.od);
		if (x.nr) b.push("No punch, no leave, no on duty");
		if (x.i) b.push("IN without OUT");
		if (x.lp) b.push("Leave applied (" + x.lp + ") · pending");
		if (x.odp) b.push("On duty requested · pending");
		return b;
	}
	const rpTip = (d, x) => fmtDs(parseD(d)) + " · " + (RP_CODE[x.s] || "No record") + (x.in ? " · IN " + x.in : "") + (x.out ? " · OUT " + x.out : "") + (x.m ? " · " + hrs(x.m) + " h" : "")
		+ (x.late ? " · late " + x.late + " min" : "") + (x.early ? " · early " + x.early + " min" : "") + (rpNote(x).length ? " · " + rpNote(x).join(" · ") : "");
	function scopePill() {
		const v = (RPD && RPD.viewer) || DATA.viewer;
		return v && v.scope === "team" ? pill("user", "Scope", "Your team · " + v.team_size + " employees", "iris") : pill("box", "Scope", "All employees", "iris");
	}
	function rpPills(extra) {
		const out = [pill("calendar", S.rp === "year" ? "Year" : "Month", rpLabel(), "cyan")];
		if (S.q) out.push(pill("user", "Employee", S.q, "iris"));
		if (S.dept) out.push(pill("building", "Department", S.dept, "iris"));
		(extra || []).forEach((x) => out.push(x));
		out.push(scopePill());
		return out.join("");
	}
	const rpWrap = (pills, body) => '<div class="card accent' + (S.anim ? " turn" : "") + '"><div class="fbar"><div class="hd">' + ic("filter", 13) + 'Showing results for</div><div class="pills">' + pills + '</div></div>' + body + '</div>';
	const rpWaiting = (what) => '<div class="card accent turn"><div class="empty"><img class="mascot" src="' + MASCOT + '" alt=""><h3>' + (rpLoading ? '<span class="shim">Building the ' + what + '…</span>' : "Could not load the " + what) + '</h3><p>'
		+ (rpLoading ? "Collecting mark in / out, leave, half day and on duty for " + esc(rpLabel()) + "." : "You may not have access to this report, or the server is busy. Press Refresh to try again.") + '</p></div></div>';
	function rpRegister(list) {
		const days = RPD.days, td = todayStr();
		const head = '<tr><th class="nm">Employee</th>' + days.map((d) => { const dt = parseD(d); return '<th class="' + (dt.getDay() === 0 ? "sun" : "") + '" title="' + esc(DOW[dt.getDay()] + " " + fmtDs(dt)) + '">' + dt.getDate() + '<br>' + DOW[dt.getDay()].slice(0, 2) + '</th>'; }).join("")
			+ '<th>P</th><th>HD</th><th>L</th><th>OD</th><th>A</th></tr>';
		const rows = list.map((e) => {
			const cells = RPD.cells[e.id] || {}, c = e.c;
			return '<tr data-rp="emp:' + esc(e.id) + '" tabindex="0"><td class="nm emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span>' + (e.dept ? ' · ' + esc(e.dept) : "") + '</span></td>'
				+ days.map((d) => {
					const x = cells[d]; if (!x || (!x.s && !x.in)) return '<td></td>';
					return '<td title="' + esc(rpTip(d, x)) + '">' + rpChip(x.s, x) + (x.in || x.out ? '<span class="rio">' + (x.in || "—") + '</span><span class="rio' + (x.i ? " miss" : "") + '">' + (x.out || (d === td ? "…" : "—")) + '</span>' : "") + '</td>';
				}).join("")
				+ '<td class="tt">' + c.p + '</td><td class="tt">' + c.hd + '</td><td class="tt">' + nfx(c.l) + '</td><td class="tt">' + c.od + '</td><td class="tt">' + c.a + '</td></tr>';
		}).join("");
		const leg = '<div class="xleg">' + [["P", "Present"], ["HD", "Half day"], ["L", "Leave"], ["OD", "On duty"], ["A", "Absent / no record"], ["H", "Holiday"], ["WO", "Week off"]].map((x) => '<span>' + rpChip(x[0]) + ' ' + x[1] + '</span>').join("")
			+ '<span>Under each box: Mark IN, then Mark OUT · red dot = late</span></div>';
		return '<div class="tscroll"><table class="rgrid"><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div>' + leg;
	}
	function rpYearGrid(list) {
		const ms = RPD.months, cur = RPD.today.slice(0, 7);
		const sub = (c) => [c.l ? "L " + nfx(c.l) : "", c.a ? "A " + c.a : ""].filter(Boolean).join(" · ");
		const head = '<tr><th class="nm">Employee</th>' + ms.map((m) => '<th>' + MON[+m.slice(5, 7) - 1] + '</th>').join("") + '<th>Total</th></tr>';
		const rows = list.map((e) => {
			const mo = RPD.mo[e.id] || {};
			return '<tr data-rp="emp:' + esc(e.id) + '" tabindex="0"><td class="nm emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span>' + (e.dept ? ' · ' + esc(e.dept) : "") + '</span></td>'
				+ ms.map((m) => {
					const c = mo[m]; if (!c || m > cur || !(c.p || c.hd || c.od || c.l || c.a)) return '<td></td>';
					return '<td title="' + esc(monthLabel(m) + " · Present " + c.p + " · Half day " + c.hd + " · On duty " + c.od + " · Leave " + nfx(c.l) + " · Absent " + c.a + " · Late " + c.late) + '"><div class="rpm"><b>' + nfx(pdays(c)) + '</b>' + (sub(c) ? '<span>' + sub(c) + '</span>' : "") + '</div></td>';
				}).join("")
				+ '<td><div class="rpm"><b>' + nfx(pdays(e.c)) + '</b><span>' + sub(e.c) + '</span></div></td></tr>';
		}).join("");
		return '<div class="tscroll"><table class="rgrid"><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div>'
			+ '<div class="xleg"><span>Big number = present days (Present + On duty + ½ Half day)</span><span>L = leave days · A = absent days</span><span>Select an employee for the month-by-month detail</span></div>';
	}
	function rpSummary(list) {
		const T = { p: 0, hd: 0, od: 0, l: 0, a: 0, off: 0, late: 0, early: 0, mins: 0 };
		const n = (v, col) => v ? '<span class="num"' + (col ? ' style="color:var(--' + col + ')"' : "") + '>' + nfx(v) + '</span>' : '<span class="sub">—</span>';
		const rows = list.map((e) => {
			const c = e.c; Object.keys(T).forEach((k) => { T[k] += c[k] || 0; });
			return '<tr data-rp="emp:' + esc(e.id) + '" tabindex="0">' + rpEmpCell(e) + '<td>' + n(c.p) + '</td><td>' + n(c.hd, "pending") + '</td><td>' + n(c.od) + '</td><td>' + n(c.l) + '</td><td>' + n(c.a, "err") + '</td><td>' + n(c.off) + '</td>'
				+ '<td><b class="num">' + nfx(pdays(c)) + '</b></td><td>' + n(c.late, "err") + '</td><td>' + n(c.early, "pending") + '</td><td><span class="dtm">' + (hrs(c.mins) || "—") + '</span></td><td><span class="dtm">' + (c.md ? hrs(Math.round(c.mins / c.md)) : "—") + '</span></td></tr>';
		}).join("");
		const tot = '<tr class="tot"><td><b>Total</b><span class="sub"> · ' + list.length + (list.length === 1 ? " employee" : " employees") + '</span></td><td>' + n(T.p) + '</td><td>' + n(T.hd) + '</td><td>' + n(T.od) + '</td><td>' + n(T.l) + '</td><td>' + n(T.a) + '</td><td>' + n(T.off) + '</td>'
			+ '<td><b class="num">' + nfx(T.p + T.od + T.hd / 2) + '</b></td><td>' + n(T.late) + '</td><td>' + n(T.early) + '</td><td><span class="dtm">' + (hrs(T.mins) || "—") + '</span></td><td></td></tr>';
		return '<div class="tscroll"><table class="dt xt"><thead><tr><th>Employee</th><th>Present</th><th>Half day</th><th>On duty</th><th>Leave</th><th>Absent</th><th>Holiday / off</th><th>Present days</th><th>Late</th><th>Early</th><th>Total hours</th><th>Avg / day</th></tr></thead><tbody>' + rows + tot + '</tbody></table></div>'
			+ '<div class="xleg"><span>Present days = Present + On duty + ½ Half day</span><span>Absent includes past days with no punch, no leave and no on duty</span><span>Days before an employee’s first punch or attendance are not counted</span><span>Days are counted up to today</span></div>';
	}
	const rpEmpCell = (e) => '<td class="emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span> · ' + (e.dept ? esc(e.dept) : "No department")
		+ (e.since ? "" : ' · <span style="color:var(--pending)">no attendance yet</span>') + '</span></td>';
	function rpEmpDetail(e) {
		const c = e.c, year = RPD.period === "year";
		const head = '<div class="empH"><button type="button" class="btn ghost sm" data-rp="back">' + ic("chevron", 13) + (S.tab === "lv" ? "Leave & on duty" : "Attendance report") + '</button><div class="thumb ini">' + esc(ini(e.name)) + '</div>'
			+ '<div class="emp"><b>' + esc(e.name) + '</b><span><span class="id" style="font-size:12px">' + esc(e.id) + '</span>' + (e.dept ? ' · ' + esc(e.dept) : "") + (e.desig ? ' · ' + esc(e.desig) : "")
			+ (e.since ? ' · tracked from ' + esc(fmtDs(parseD(e.since)) + " " + parseD(e.since).getFullYear()) : ' · no punch or attendance yet') + '</span></div><div class="sp"></div>'
			+ '<span class="sub">Present ' + c.p + ' · Half day ' + c.hd + ' · On duty ' + c.od + ' · Leave ' + nfx(c.l) + ' · Absent ' + c.a + ' · Late ' + c.late + '</span>'
			+ '<button type="button" class="btn sm" data-rp="xls">' + ic("download", 13) + 'Excel for ' + esc(e.name.split(" ")[0]) + '</button></div>';
		const dash = '<span class="sub">—</span>';
		let body;
		if (year) {
			const mo = RPD.mo[e.id] || {}, cur = RPD.today.slice(0, 7), z = { p: 0, hd: 0, od: 0, l: 0, a: 0, off: 0, late: 0, early: 0, mins: 0 };
			body = '<div class="tscroll"><table class="dt xt"><thead><tr><th>Month</th><th>Present</th><th>Half day</th><th>On duty</th><th>Leave</th><th>Absent</th><th>Holiday / off</th><th>Present days</th><th>Late</th><th>Early</th><th>Total hours</th></tr></thead><tbody>'
				+ RPD.months.filter((m) => m <= cur).map((m) => {
					const x = mo[m] || z, nn = (v) => v ? '<span class="num">' + nfx(v) + '</span>' : dash;
					return '<tr data-rp="month:' + m + '" tabindex="0"><td><b style="font-weight:500">' + monthLabel(m) + '</b><div class="sub">open day by day</div></td><td>' + nn(x.p) + '</td><td>' + nn(x.hd) + '</td><td>' + nn(x.od) + '</td><td>' + nn(x.l) + '</td><td>' + nn(x.a) + '</td><td>' + nn(x.off) + '</td>'
						+ '<td><b class="num">' + nfx(pdays(x)) + '</b></td><td>' + nn(x.late) + '</td><td>' + nn(x.early) + '</td><td><span class="dtm">' + (hrs(x.mins) || "—") + '</span></td></tr>';
				}).join("") + '</tbody></table></div>';
		} else {
			const cells = RPD.cells[e.id] || {}, td = todayStr();
			body = '<div class="tscroll"><table class="dt xt"><thead><tr><th>Date</th><th>Status</th><th>Mark IN</th><th>Mark OUT</th><th>Hours</th><th>Late</th><th>Early going</th><th>Leave / half day / on duty</th></tr></thead><tbody>'
				+ RPD.days.filter((d) => d <= td || cells[d]).map((d) => {
					const x = cells[d] || {}, dt = parseD(d), notes = rpNote(x);
					return '<tr><td><span class="dtm">' + DOW[dt.getDay()] + " " + fmtDs(dt) + '</span></td><td>' + (x.s ? rpChip(x.s, x) + ' <span class="sub">' + RP_CODE[x.s] + '</span>' : '<span class="sub">' + (d === td ? "Not punched yet" : "—") + '</span>') + '</td>'
						+ '<td><span class="dtm">' + (x.in || "—") + '</span></td><td><span class="dtm"' + (x.i ? ' style="color:var(--err)"' : "") + '>' + (x.out || (x.in && d === td ? "not yet" : "—")) + '</span></td><td><span class="dtm">' + (hrs(x.m) || "—") + '</span></td>'
						+ '<td>' + (x.late ? tag("err", x.late + " min") : dash) + '</td><td>' + (x.early ? tag("pending", x.early + " min") : dash) + '</td><td class="chips">' + (notes.map((s) => tag("draft", esc(s))).join("") || dash) + '</td></tr>';
				}).join("") + '</tbody></table></div>';
		}
		const recs = (RPD.records || []).filter((r) => r.emp === e.id);
		const rl = recs.length ? '<div class="meta"><span class="a"><b>' + recs.length + '</b> leave, half day and on duty ' + (recs.length === 1 ? "entry" : "entries") + '</span><span class="b">' + esc(RPD.label) + '</span></div>' + lvTable(recs, true) : "";
		return head + '<div class="meta"><span class="a"><b>' + esc(RPD.label) + '</b> · ' + (year ? "month by month" : "day by day") + '</span><span class="b">' + (year ? "Select a month to open it day by day" : "Mark IN = first IN punch of the day · Mark OUT = last OUT punch") + '</span></div>' + body + rl;
	}
	function repView() {
		if (!RPD) return rpWaiting("attendance report");
		const year = RPD.period === "year", emp = S.rpEmp ? rpEmpOf(S.rpEmp) : null;
		if (emp) return rpWrap(rpPills([pill("user", "Employee", emp.name, "iris")]), rpEmpDetail(emp));
		const list = rpBase().slice().sort((a, b) => a.name.localeCompare(b.name)), view = S.rpView === "sum" ? "sum" : "reg";
		const bar = '<div class="xbar"><span class="xseg"><button type="button" class="' + (view === "reg" ? "on" : "") + '" data-rp="view:reg">' + (year ? "Month-wise" : "Mark in / out") + '</button><button type="button" class="' + (view === "sum" ? "on" : "") + '" data-rp="view:sum">Summary</button></span>'
			+ '<span class="sp"></span><span class="rpnote">' + ic("info", 13) + (year ? "Present days per month · select an employee for details" : "Status, Mark IN and Mark OUT for every day · select an employee for day by day") + '</span></div>';
		const upto = RPD.today < RPD.to ? RPD.today : RPD.to;
		const meta = '<div class="meta"><span class="a"><b>' + list.length + '</b> ' + (list.length === 1 ? "employee" : "employees") + ' · ' + esc(RPD.label) + '</span><span class="b">Counted up to ' + esc(fmtDs(parseD(upto)) + " " + parseD(upto).getFullYear()) + ' · Export to Excel gives the full workbook</span></div>';
		const body = !list.length ? emptyBody("No employees in this view", "Clear the employee or department filter, or pick another period.") : view === "sum" ? rpSummary(list) : year ? rpYearGrid(list) : rpRegister(list);
		return rpWrap(rpPills(), bar + meta + body);
	}
	// Leave & on duty: leave applications, half days (half-day leave / duty, or a half day marked on attendance) and on-duty requests
	const LV_CHIPS = [["all", "All"], ["leave", "Leave"], ["half", "Half day"], ["od", "On duty"]];
	const LV_ST = [["all", "Any status"], ["ok", "Approved"], ["pend", "Pending"], ["rej", "Rejected"]];
	const lvMatch = (r, chip) => chip === "all" || (chip === "leave" && r.kind === "Leave") || (chip === "od" && r.kind === "On duty") || (chip === "half" && !!r.half);
	function lvBase() {
		if (!RPD) return [];
		const ok = new Set(rpBase().map((e) => e.id));
		return (RPD.records || []).filter((r) => ok.has(r.emp));
	}
	const lvList = () => lvBase().filter((r) => lvMatch(r, S.lvChip) && (S.lvSt === "all" || r.k === S.lvSt));
	function lvTable(recs, compact) {
		const byId = {}; ((RPD && RPD.employees) || []).forEach((e) => { byId[e.id] = e; });
		const rows = recs.map((r) => {
			const e = byId[r.emp] || { id: r.emp, name: r.emp, dept: "" }, a = parseD(r.from), b = parseD(r.to);
			const dt = r.from === r.to ? DOW[a.getDay()] + " " + fmtDs(a) + " " + a.getFullYear() : fmtDs(a) + " – " + fmtDs(b) + " " + b.getFullYear();
			const kind = r.kind === "Leave" ? tag("draft", "Leave") : r.kind === "On duty" ? tag("ok", "On duty") : tag("pending", "Half day");
			const half = r.half && r.kind !== "Half day" ? tag("pending", "Half day" + (r.htype ? " · " + esc(r.htype) : "") + (r.hdate && r.from !== r.to ? " · " + fmtDs(parseD(r.hdate)) : "")) : "";
			return '<tr' + (compact ? "" : ' data-rp="emp:' + esc(r.emp) + '" tabindex="0"') + '>' + (compact ? "" : exEmpCell(e)) + '<td>' + kind + '<div class="sub" style="margin-top:5px">' + esc(r.type) + '</div></td>'
				+ '<td><span class="dtm">' + dt + '</span>' + (half ? '<div style="margin-top:5px">' + half + '</div>' : "") + '</td><td><span class="num">' + nfx(r.days) + '</span></td>'
				+ '<td class="chips">' + tag(kTag(r.k), esc(r.st)) + (r.cx ? tag("pending", "Cancellation requested") : "") + '</td><td style="white-space:normal;min-width:220px;max-width:380px"><span class="sub">' + (esc(r.why) || "—") + '</span></td></tr>';
		}).join("");
		return '<div class="tscroll"><table class="dt xt"><thead><tr>' + (compact ? "" : "<th>Employee</th>") + '<th>Type</th><th>Dates</th><th>Days</th><th>Status</th><th>Reason / details</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
	}
	function lvView() {
		if (!RPD) return rpWaiting("leave and on-duty list");
		const emp = S.rpEmp ? rpEmpOf(S.rpEmp) : null;
		if (emp) return rpWrap(rpPills([pill("user", "Employee", emp.name, "iris")]), rpEmpDetail(emp));
		const base = lvBase(), list = lvList(), extra = [];
		if (S.lvChip !== "all") extra.push(pill("filter", "Show", (LV_CHIPS.find((x) => x[0] === S.lvChip) || [])[1], "pending"));
		if (S.lvSt !== "all") extra.push(pill("filter", "Status", (LV_ST.find((x) => x[0] === S.lvSt) || [])[1], "pending"));
		const bar = '<div class="xbar">' + LV_CHIPS.map((x) => '<button type="button" class="xchip' + (S.lvChip === x[0] ? " on" : "") + '" data-lv="chip:' + x[0] + '">' + x[1] + ' <span class="n">' + base.filter((r) => lvMatch(r, x[0]) && (S.lvSt === "all" || r.k === S.lvSt)).length + '</span></button>').join("")
			+ '<span class="sp"></span><span class="xseg">' + LV_ST.map((x) => '<button type="button" class="' + (S.lvSt === x[0] ? "on" : "") + '" data-lv="st:' + x[0] + '">' + x[1] + '</button>').join("") + '</span></div>';
		const meta = '<div class="meta"><span class="a"><b>' + list.length + '</b> ' + (list.length === 1 ? "entry" : "entries") + ' · ' + esc(RPD.label) + '</span><span class="b">Leave applications, half days and on-duty requests · select a row for that employee’s days</span></div>';
		return rpWrap(rpPills(extra), bar + meta + (list.length ? lvTable(list, false) : emptyBody("Nothing in this view", "No leave, half day or on duty matches these filters. Try another chip, status or period.")));
	}
	function rpKpiBlock() {
		const b = rpBase(), sum = (k) => b.reduce((a, e) => a + (e.c[k] || 0), 0), team = RPD.viewer && RPD.viewer.scope === "team";
		const K = [{ l: "Employees", v: b.length, s: team ? "in your team" : "in this report", c: "" },
			{ l: "Present days", v: nfx(b.reduce((a, e) => a + pdays(e.c), 0)), s: "present + on duty + ½ half day", c: "ok" },
			{ l: "Half days", v: sum("hd"), s: "half-day attendance", c: "pending" },
			{ l: "Leave days", v: nfx(sum("l")), s: "approved leave taken", c: "" },
			{ l: "On duty days", v: sum("od"), s: "approved on duty", c: "" },
			{ l: "Absent days", v: sum("a"), s: "incl. no punch / no record", c: "err" }];
		$$("kpis").innerHTML = K.map((x) => '<div class="card kpi"><span class="lbl">' + x.l + '</span><span class="v ' + x.c + '">' + x.v + '</span><span class="s">' + x.s + '</span></div>').join("");
		$$("lead").innerHTML = 'Mark in / out, leave, half day and on duty for <span class="num">' + b.length + '</span> ' + (b.length === 1 ? "employee" : "employees") + (team ? " in your team" : "") + ' · ' + esc(RPD.label) + (RPD.period === "year" ? ", month by month." : ", day by day.");
	}
	function lvKpiBlock() {
		const b = lvBase(), ok = b.filter((r) => r.k === "ok"), lv = ok.filter((r) => r.kind === "Leave"), od = ok.filter((r) => r.kind === "On duty");
		const K = [{ l: "Leave days", v: nfx(lv.reduce((a, r) => a + r.days, 0)), s: lv.length + (lv.length === 1 ? " approved application" : " approved applications"), c: "" },
			{ l: "Half days", v: ok.filter((r) => r.half).length, s: "half-day leave, duty or attendance", c: "pending" },
			{ l: "On duty days", v: nfx(od.reduce((a, r) => a + r.days, 0)), s: od.length + (od.length === 1 ? " approved request" : " approved requests"), c: "ok" },
			{ l: "Pending", v: b.filter((r) => r.k === "pend").length, s: "waiting for an approval", c: "pending" }];
		$$("kpis").classList.add("k4");
		$$("kpis").innerHTML = K.map((x) => '<div class="card kpi"><span class="lbl">' + x.l + '</span><span class="v ' + x.c + '">' + x.v + '</span><span class="s">' + x.s + '</span></div>').join("");
		const ne = new Set(b.map((r) => r.emp)).size;
		$$("lead").innerHTML = 'Leave, half day and on duty for <span class="num">' + ne + '</span> ' + (ne === 1 ? "employee" : "employees") + ' · ' + esc(RPD.label) + '.';
	}
	function syncRpControls() {
		const rp = isRp();
		["rangeSeg", "monBox", "dates", "photoToggle", "flagToggle"].forEach((id) => { $$(id).style.display = rp ? "none" : ""; });
		qa(".fld.pa-only").forEach((el) => { el.style.display = rp ? "none" : ""; });
		$$("rpSeg").style.display = rp ? "" : "none";
		$$("rpMonBox").style.display = rp && S.rp === "month" ? "" : "none";
		$$("rpYearBox").style.display = rp && S.rp === "year" ? "" : "none";
		Array.from($$("rpSeg").children).forEach((x) => x.classList.toggle("on", x.dataset.p === S.rp));
		if ($$("rpMonth").value !== S.rpMonth) $$("rpMonth").value = S.rpMonth;
		if ($$("rpYear").value !== S.rpYear) $$("rpYear").value = S.rpYear;
		$$("capnote").style.display = rp ? "none" : "";
	}
	function render() {
		const list = baseList(); kpiBlock(list); $$("kpis").classList.remove("k4"); if (S.tab === "np" && !loading && !loadError) npKpiBlock(); else if (S.tab === "ex" && !loading && !loadError && EXD) exKpiBlock();
		else if (S.tab === "rep" && RPD) rpKpiBlock(); else if (S.tab === "lv" && RPD) lvKpiBlock(); else if (isRp()) { $$("kpis").innerHTML = ""; $$("lead").innerHTML = rpLoading ? "Building the report for " + esc(rpLabel()) + "…" : ""; }
		syncRpControls();
		const flaggedList = list.filter((p) => p.flagged);
		let tabList = S.tab === "sus" ? flaggedList.slice().sort((a, b) => b.sev - a.sev) : (S.onlyFlag ? flaggedList : list);
		if (S.kpi === "flag") tabList = flaggedList; if (S.kpi === "shared") tabList = list.filter(isShared); if (S.kpi === "drift") tabList = list.filter(isDrift);
		const devCount = new Set(list.filter((p) => p.dev).map((p) => p.dev)).size, empCount = new Set(list.map((p) => p.employee)).size, npBadge = NPD ? npStats().missing.length : "…", exBadge = EXD ? exBase().filter((e) => e.c.late_punch || e.c.early_punch || e.c.inc || e.c.pend).length : "…";
		$$("tabs").innerHTML =
			'<button class="tab' + (S.tab === "mon" ? " on" : "") + '" data-t="mon">' + ic("calendar", 14) + 'Month summary <span class="badge mono">' + empCount + '</span></button>'
			+ '<button class="tab' + (S.tab === "np" ? " on" : "") + '" data-t="np">' + ic("userx", 14) + 'Not punched <span class="badge mono">' + npBadge + '</span></button>'
			+ '<button class="tab' + (S.tab === "ex" ? " on" : "") + '" data-t="ex">' + ic("layers", 14) + 'Exceptions <span class="badge mono">' + exBadge + '</span></button>'
			+ '<button class="tab' + (S.tab === "rep" ? " on" : "") + '" data-t="rep">' + ic("report", 14) + 'Attendance report <span class="badge mono">' + (RPD ? rpBase().length : "…") + '</span></button>'
			+ '<button class="tab' + (S.tab === "lv" ? " on" : "") + '" data-t="lv">' + ic("leave", 14) + 'Leave &amp; on duty <span class="badge mono">' + (RPD ? lvBase().length : "…") + '</span></button>'
			+ '<button class="tab' + (S.tab === "sus" ? " on" : "") + '" data-t="sus">' + ic("alert", 14) + 'Flagged <span class="badge mono">' + flaggedList.length + '</span></button>'
			+ '<button class="tab' + (S.tab === "all" ? " on" : "") + '" data-t="all">All punches <span class="badge mono">' + list.length + '</span></button>'
			+ '<button class="tab' + (S.tab === "dev" ? " on" : "") + '" data-t="dev">' + ic("phone", 14) + 'Devices <span class="badge mono">' + devCount + '</span></button>'
			+ '<button class="tab' + (S.tab === "emp" ? " on" : "") + '" data-t="emp">' + ic("user", 14) + 'Employees <span class="badge mono">' + empCount + '</span></button>'
			+ '<span class="sp"></span><button class="help" id="legendBtn">' + ic("info", 14) + 'How flags work</button>';
		const v = $$("view");
		if (isRp()) { v.innerHTML = S.tab === "rep" ? repView() : lvView(); S.anim = false; return; }
		if (loading) { v.innerHTML = '<div class="card accent turn"><div class="empty"><img class="mascot" src="' + MASCOT + '" alt=""><h3><span class="shim">Reading punch data…</span></h3><p>Collecting punches, photos and devices for the selected dates.</p></div></div>'; S.anim = false; return; }
		if (loadError) { v.innerHTML = '<div class="card accent"><div class="empty"><h3>Could not load punches</h3><p>' + esc(loadError) + '</p></div></div>'; return; }
		if (S.tab === "np") v.innerHTML = notPunchedView(); else if (S.tab === "ex") v.innerHTML = exView(); else if (S.tab === "mon") { const ml = S.kpi ? tabList : (S.onlyFlag ? flaggedList : list); v.innerHTML = S.empView ? dayCardsView(ml) : monthView(ml); } else if (S.tab === "dev") v.innerHTML = deviceView(list); else if (S.tab === "emp") v.innerHTML = employeeView(list); else v.innerHTML = punchTable(tabList, S.tab === "sus" || S.kpi === "flag");
		S.anim = false;
	}
	function buildLegend() {
		const R = [["err", "Shared device", "One phone, several employees", "Punches from different people came from the same device on the same day. This is the “someone else’s photo on my phone” case."],
			["pending", "New device", "Not their usual phone", "The punch came from a device that is not the one this employee normally uses (their most-used phone over the last 60 days)."],
			["pending", "Clock drift", "Phone clock is off", "The app sends the phone’s own time. A gap of 2 minutes or more can mean the phone’s clock was changed."],
			["pending", "Flat face score", "Same distance every time", "A live face gives slightly different distances day to day. An almost constant value over the last 6 punches can mean the same photo is being shown."],
			["pending", "GPS at centre", "Always the exact centre", "Real GPS drifts by a few metres. Landing on the centre of the site on the last 6 punches can point to a mock-location app."]];
		$$("legendBox").innerHTML = R.map((r) => '<div class="rule">' + tag(r[0], r[1]) + '<b>' + r[2] + '</b><p>' + r[3] + '</p></div>').join("")
			+ '<div class="rule ph">' + tag("phase", "Liveness") + '<b>Liveness check</b><p>When active liveness is switched on for the site, a punch that fails the blink / head-turn challenge is rejected before it is saved. Its score, when available, appears in the punch details.</p></div>';
	}

	// ---- drawer
	function mapSvg(p, angle) {
		const r = 60, cx = 110, cy = 76, k = Math.min(1, (p.dist || 0) / (p.radius || 1)), a = angle == null ? 0.8 : angle, px = cx + Math.cos(a) * k * r, py = cy + Math.sin(a) * k * r;
		return '<svg viewBox="0 0 220 152" width="100%"><defs><pattern id="gr" width="22" height="22" patternUnits="userSpaceOnUse"><path d="M22 0H0V22" fill="none" stroke="var(--border-subtle)" stroke-width="1"/></pattern></defs><rect width="220" height="152" fill="url(#gr)"/><circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="var(--iris-tint)" stroke="var(--iris)" stroke-width="1.4" stroke-dasharray="5 4"/><circle cx="' + cx + '" cy="' + cy + '" r="3.5" fill="var(--iris)"/><line x1="' + cx + '" y1="' + cy + '" x2="' + px + '" y2="' + py + '" stroke="var(--text-muted)" stroke-width="1.2"/><circle cx="' + px + '" cy="' + py + '" r="6" fill="var(--cyan)" stroke="var(--bg-surface)" stroke-width="2"/><text x="10" y="144" font-family="JetBrains Mono,monospace" font-size="9.5" fill="var(--text-muted)">' + esc(p.loc) + ' · radius ' + p.radius + ' m</text><text x="210" y="15" text-anchor="end" font-family="JetBrains Mono,monospace" font-size="11" font-weight="600" fill="var(--cyan)">' + p.dist + ' m</text></svg>';
	}
	function issueText(f, p, d) {
		const T = {
			SHARED: ["One phone, " + f.n + " employees", "Other employees punched from this same device on the same day (see device history below). Possible buddy punching with someone else’s photo."],
			NEWDEV: ["Not this employee’s usual phone", d && d.primary_device ? esc(p.name) + " normally punches from " + esc(d.primary_device) + ". This one is different." : "This is a different phone from the one this employee normally uses."],
			DRIFT: ["Phone clock is " + Math.abs(Math.round(f.v / 60)) + " minutes " + (f.v > 0 ? "ahead of" : "behind") + " the server", "The app sends the phone’s own time. A gap this large can mean the phone’s clock was changed."],
			FLAT: ["Face distance is almost identical every time" + (p.face != null ? " (" + p.face.toFixed(3) + ")" : ""), "A live face gives slightly different distances day to day. A near-constant value over the last 6 punches can mean the same photo is being shown."],
			CENTRE: ["GPS sits at the centre of the site" + (p.dist != null ? " (" + p.dist + " m)" : ""), "Real GPS drifts by a few metres. Landing on the centre on each of the last 6 punches can point to a mock-location app."]
		};
		return T[f.k] || ["Flag", ""];
	}
	let openId = null;
	function drawerHtml(p, d) {
		const hasDev = !!p.dev, captured = !!p.audit && (p.photo || p.dev || p.face != null);
		const issues = p.flags.filter((f) => f.k !== "MANUAL").map((f) => { const t = issueText(f, p, d); const hi = (f.sev || 0) >= 3; return '<div class="issue"><span class="ic ' + (hi ? "hi" : "t-pending") + '">' + ic("alert", 14) + '</span><div><b>' + t[0] + '</b><p>' + t[1] + '</p></div></div>'; }).join("")
			|| '<div class="issue"><span class="ic t-ok">' + ic("check", 14) + '</span><div><b>Nothing unusual</b><p>No flag was raised for this punch.</p></div></div>';
		const punchPhoto = p.photo ? '<img data-zoom="1" src="' + photoUrl(p) + '" alt="Punch photo">' : '<div class="ph"><span>' + noPhotoText(p) + '</span></div>';
		const facePhoto = d && d.registered_face ? '<img src="' + faceUrl(p) + '" alt="Registered face">' : '<div class="ph"><span>' + (d ? "No registered face" : "Loading…") + '</span></div>';
		const hist = (d && d.device_history && d.device_history.length) ? d.device_history.map((h) => '<div class="' + (h.employee === p.employee ? "me" : "") + '"><span><b style="font-weight:500">' + esc(h.name || h.employee) + '</b> <span class="sub">' + (h.usual ? "· usual phone" : "· not their usual phone") + '</span></span><span class="mono sub">' + h.n + ' punches · last ' + esc(fmtDs(parseD(h.last))) + '</span></div>').join("") : '<div><span class="sub">' + (hasDev ? (d ? "No other history for this device." : "Loading…") : "This punch has no device record.") + '</span></div>';
		const detail = '<div class="facts">'
			+ (p.face != null ? '<div class="fact wide"><span class="lbl">Face distance · lower is closer</span><b><span class="fv" style="font-size:15px">' + p.face.toFixed(3) + '</span> <span class="sub">of limit ' + TH().toFixed(2) + '</span></b><div class="bar" style="--th:' + (TH() / 0.7 * 100) + '"><i class="' + (p.face > 0.4 ? "m" : "") + '" style="width:' + Math.min(100, p.face / 0.7 * 100) + '%"></i></div></div>' : "")
			+ '<div class="fact"><span class="lbl">Punch time</span><b class="mono">' + fmtT(p.t) + ':' + pad(p.sec || 0) + '</b></div>'
			+ '<div class="fact"><span class="lbl">Phone clock</span><b class="mono">' + (p.drift == null ? "—" : '<span class="clk ' + (Math.abs(p.drift) >= 120 ? "bad" : "good") + '">' + clockTxt(p) + '</span> vs server') + '</b></div>'
			+ '<div class="fact"><span class="lbl">Device</span><b>' + (hasDev ? '<span class="id">' + esc(p.dev) + '</span><br><span class="sub">' + esc(p.model || "Unknown phone") + '</span>' : "—") + '</b></div>'
			+ '<div class="fact"><span class="lbl">OS · browser</span><b>' + (p.os || p.browser ? esc([p.os, p.browser].filter(Boolean).join(" · ")) : "—") + '</b></div>'
			+ '<div class="fact"><span class="lbl">IP address</span><b class="mono">' + esc(p.ip || "—") + '</b></div>'
			+ '<div class="fact"><span class="lbl">Source</span><b>' + esc(p.source) + (p.added_by ? '<br><span class="sub">by ' + esc(p.added_by) + '</span>' : "") + '</b></div>'
			+ (p.live != null ? '<div class="fact"><span class="lbl">Liveness score</span><b class="mono">' + p.live.toFixed(2) + '</b><span class="sub">' + esc(DATA.liveness_mode || "off") + ' mode</span></div>' : "")
			+ (p.acc != null ? '<div class="fact"><span class="lbl">GPS accuracy</span><b class="mono">' + p.acc + ' m</b></div>' : "")
			+ '</div>';
		return '<div class="dhead"><div class="thumb' + (p.photo ? "" : " ini") + '">' + (p.photo ? '<img src="' + photoUrl(p) + '" alt="">' : esc(ini(p.name))) + '</div><div style="flex:1;min-width:0"><div class="lbl" style="margin-bottom:2px">Punch details</div><h2>' + esc(p.name) + '</h2><div class="sub"><span class="id" style="font-size:12px">' + esc(p.employee) + '</span> · ' + esc(p.dept) + ' · ' + esc(p.type) + ' <span class="mono">' + fmtT(p.t) + '</span> · ' + dayLabel(p) + '</div></div><button class="ibtn" data-close aria-label="Close">' + ic("close", 16) + '</button></div>'
			+ '<div class="dbody">'
			+ '<div><div class="lbl sec">Photo comparison</div><div class="photos"><div class="pbox"><div class="im' + (p.photo ? " has" : "") + '">' + punchPhoto + '</div><div class="cap"><span class="lbl">Punch photo</span><span class="mono sub">' + fmtT(p.t) + '</span></div></div><div class="pbox"><div class="im">' + facePhoto + '</div><div class="cap"><span class="lbl">Registered face</span></div></div></div></div>'
			+ '<div><div class="lbl sec">Why it was flagged</div>' + issues + '</div>'
			+ '<div><div class="lbl sec">Details</div>' + detail + '</div>'
			+ (p.loc && p.dist != null ? '<div><div class="lbl sec">Geofence · ' + esc(p.loc) + '</div><div class="mapbox">' + mapSvg(p, d ? d.angle : null) + '</div></div>' : "")
			+ (hasDev ? '<div><div class="lbl sec">Device history · 30 days</div><div class="devuse">' + hist + '</div></div>' : "")
			+ '<div><div class="lbl sec">HR note</div><textarea class="note" id="noteBox" placeholder="What did you find? e.g. spoke to the employee, phone was replaced…">' + esc(p.note || "") + '</textarea>' + (d && d.reviewed_by ? '<div class="sub" style="margin-top:6px">Reviewed by ' + esc(d.reviewed_by) + (d.reviewed_on ? " · " + esc(d.reviewed_on.slice(0, 16)) : "") + '</div>' : "") + '</div></div>'
			+ '<div class="dfoot"><button class="btn primary" data-act="reviewed">' + ic("check", 15) + (p.reviewed ? "Reviewed — undo" : "Mark reviewed") + '</button><button class="btn" data-act="note">Save note</button></div>';
	}
	async function openDrawer(id) {
		const p = ROWS.find((x) => x.id === id); if (!p) return; openId = id;
		const dr = $$("drawer");
		dr.innerHTML = drawerHtml(p, null);
		dr.classList.add("on"); $$("scrim").classList.add("on"); dr.setAttribute("aria-hidden", "false");
		const r = await callApi("get_punch_detail", { checkin: id });
		if (openId !== id) return;
		const keep = root.getElementById("noteBox"); const typed = keep ? keep.value : null;
		dr.innerHTML = drawerHtml(p, r.ok ? r.data : { device_history: [], registered_face: false });
		if (typed != null && typed !== (p.note || "")) { const nb = root.getElementById("noteBox"); if (nb) nb.value = typed; }
	}
	function openLightbox(src) { $$("lbxImg").src = src; $$("lbx").classList.add("on"); $$("lbx").setAttribute("aria-hidden", "false"); }
	function closeLightbox() { $$("lbx").classList.remove("on"); $$("lbx").setAttribute("aria-hidden", "true"); $$("lbxImg").removeAttribute("src"); }
	function closeDrawer() { openId = null; const dr = $$("drawer"); dr.classList.remove("on"); $$("scrim").classList.remove("on"); dr.setAttribute("aria-hidden", "true"); }
	let tt; function toast(m) { const t = $$("toast"); t.textContent = m; t.classList.add("on"); clearTimeout(tt); tt = setTimeout(() => t.classList.remove("on"), 2400); }

	// ---- wiring
	const rangeDates = (r) => {
		const t = new Date(), addD = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
		if (r === "today") return [isoOf(t), isoOf(t)];
		if (r === "yesterday") { const y = addD(t, -1); return [isoOf(y), isoOf(y)]; }
		if (r === "7d") return [isoOf(addD(t, -6)), isoOf(t)];
		if (r === "30d") return [isoOf(addD(t, -29)), isoOf(t)];
		const ym = /^(\d{4})-(\d{2})$/.exec(S.month || curMonth()), y = +ym[1], mo = +ym[2] - 1;   // "month"
		const first = new Date(y, mo, 1), last = new Date(y, mo + 1, 0);
		return [isoOf(first), isoOf(last > t ? t : last)];
	};
	function setRange(r) {
		S.range = r; if (r === "month" && !S.month) S.month = curMonth();
		Array.from($$("rangeSeg").children).forEach((x) => x.classList.toggle("on", x.dataset.r === r));
		if (r !== "custom") { const d = rangeDates(r); $$("dFrom").value = d[0]; $$("dTo").value = d[1]; S.from = d[0]; S.to = d[1]; }
		syncMonthSel();
	}
	function syncMonthSel() {
		const sel = $$("fMonth"), cur = S.range === "month" && S.month !== curMonth();
		sel.value = cur ? S.month : "";
		$$("monBox").classList.toggle("on", cur);
		Array.from($$("rangeSeg").children).forEach((x) => { if (x.dataset.r === "month") x.classList.toggle("on", S.range === "month" && S.month === curMonth()); });
	}
	{
		const sel = $$("fMonth"), t = new Date(); let h = '<option value="">Pick a month</option>';
		for (let i = 0; i < 12; i++) { const d = new Date(t.getFullYear(), t.getMonth() - i, 1), ym = d.getFullYear() + "-" + pad(d.getMonth() + 1); h += '<option value="' + ym + '">' + monthLabel(ym) + "</option>"; }
		sel.innerHTML = h;
	}
	setRange("month");
	$$("rangeSeg").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; if (b.dataset.r === "month") S.month = curMonth(); setRange(b.dataset.r); if (S.range !== "custom") loadData(); });
	$$("fMonth").addEventListener("change", (e) => { if (!e.target.value) { syncMonthSel(); return; } S.month = e.target.value; setRange("month"); loadData(); });
	const dateChanged = () => {
		let f = $$("dFrom").value, e = $$("dTo").value; if (!f || !e) return;
		if (f > e) { const x = f; f = e; e = x; $$("dFrom").value = f; $$("dTo").value = e; }
		S.from = f; S.to = e; S.range = "custom";
		Array.from($$("rangeSeg").children).forEach((x) => x.classList.toggle("on", x.dataset.r === "custom"));
		syncMonthSel();
		loadData();
	};
	$$("dFrom").addEventListener("change", dateChanged); $$("dTo").addEventListener("change", dateChanged);
	[["fQ", "q"], ["fDev", "dev"]].forEach(([id, k]) => $$(id).addEventListener("input", (e) => { S[k] = e.target.value; render(); }));
	[["fDept", "dept"], ["fLoc", "loc"], ["fType", "type"], ["fSrc", "src"]].forEach(([id, k]) => $$(id).addEventListener("change", (e) => { S[k] = e.target.value; render(); }));
	$$("flagToggle").addEventListener("click", () => { S.onlyFlag = !S.onlyFlag; $$("flagToggle").classList.toggle("on", S.onlyFlag); render(); });
	$$("photoToggle").addEventListener("click", () => { S.onlyPhoto = !S.onlyPhoto; $$("photoToggle").classList.toggle("on", S.onlyPhoto); render(); });
	$$("resetBtn").addEventListener("click", () => { const rangeChanged = S.range !== "month" || S.month !== curMonth(); S.month = curMonth(); Object.assign(S, { q: "", dept: "", loc: "", type: "", src: "", dev: "", onlyFlag: false, onlyPhoto: false, kpi: null, anim: true, empView: null, npLogin: true, npHide: false, exChip: "all", exView: "tbl", exEmp: null, rpEmp: null, lvChip: "all", lvSt: "all" }); ["fQ", "fDev", "fDept", "fLoc", "fType", "fSrc"].forEach((i) => { $$(i).value = ""; }); $$("flagToggle").classList.remove("on"); $$("photoToggle").classList.remove("on"); setRange("month"); if (rangeChanged) loadData(); else render(); toast("Filters reset"); });
	$$("kpis").addEventListener("click", (ev) => { const b = ev.target.closest(".kpi.click"); if (!b) return; S.kpi = S.kpi === b.dataset.k ? null : b.dataset.k; if (S.kpi && S.tab !== "all" && S.tab !== "sus") S.tab = "sus"; S.anim = true; render(); });
	$$("tabs").addEventListener("click", (ev) => { const b = ev.target.closest(".tab"); if (b) { S.tab = b.dataset.t; S.kpi = null; S.empView = null; S.exEmp = null; S.rpEmp = null; S.anim = true; render(); if (isRp() && !RPD && !rpLoading) loadReport(); return; } if (ev.target.closest("#legendBtn")) $$("legend").classList.toggle("show"); });
	// report period: Month-wise / Year-wise + month / year pickers (only shown on the two report tabs)
	{
		const t = new Date(); let h = "", y = "";
		for (let i = 0; i < 24; i++) { const d = new Date(t.getFullYear(), t.getMonth() - i, 1), ym = d.getFullYear() + "-" + pad(d.getMonth() + 1); h += '<option value="' + ym + '">' + monthLabel(ym) + "</option>"; }
		for (let i = 0; i < 4; i++) y += '<option value="' + (t.getFullYear() - i) + '">' + (t.getFullYear() - i) + "</option>";
		$$("rpMonth").innerHTML = h; $$("rpYear").innerHTML = y;
	}
	$$("rpSeg").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b || b.dataset.p === S.rp) return; S.rp = b.dataset.p; S.anim = true; loadReport(); });
	$$("rpMonth").addEventListener("change", (e) => { S.rpMonth = e.target.value; S.anim = true; loadReport(); });
	$$("rpYear").addEventListener("change", (e) => { S.rpYear = e.target.value; S.anim = true; loadReport(); });
	const rpExport = (only) => {
		const b = rpArgs(); b.view = S.tab === "lv" ? "lv" : "att"; b.filters = JSON.stringify({ q: S.q, dept: S.dept, chip: S.lvChip, st: S.lvSt, emp: only || "" });
		window.open("/api/method/" + API + "export_attendance_report?" + new URLSearchParams(b).toString(), "_blank");
		toast("Preparing the Excel file…");
	};
	$$("view").addEventListener("click", (ev) => {
		const rb = ev.target.closest("[data-rp]");
		if (rb) {
			const i = rb.dataset.rp.indexOf(":"), a = i < 0 ? rb.dataset.rp : rb.dataset.rp.slice(0, i), v2 = i < 0 ? "" : rb.dataset.rp.slice(i + 1);
			if (a === "xls") { rpExport(S.rpEmp); return; }
			if (a === "month") { S.rp = "month"; S.rpMonth = v2; S.anim = true; loadReport(); $$("tabs").scrollIntoView({ behavior: "smooth", block: "start" }); return; }
			if (a === "view") S.rpView = v2; else if (a === "back") S.rpEmp = null; else if (a === "emp") S.rpEmp = v2;
			S.anim = false; render(); if (a === "emp" || a === "back") $$("tabs").scrollIntoView({ behavior: "smooth", block: "start" }); return;
		}
		const lb = ev.target.closest("[data-lv]"); if (lb) { const p = lb.dataset.lv.split(":"); if (p[0] === "chip") S.lvChip = p[1]; else if (p[0] === "st") S.lvSt = p[1]; S.anim = false; render(); return; }
		const xb = ev.target.closest("[data-ex]"); if (xb) { const i = xb.dataset.ex.indexOf(":"), a = i < 0 ? xb.dataset.ex : xb.dataset.ex.slice(0, i), v2 = i < 0 ? "" : xb.dataset.ex.slice(i + 1); if (a === "chip") S.exChip = v2; else if (a === "view") S.exView = v2; else if (a === "back") S.exEmp = null; else if (a === "emp") { S.exEmp = v2; if (exOneDay()) { S.month = curMonth(); setRange("month"); loadData(); return; } } S.anim = false; render(); if (a === "emp" || a === "back") $$("tabs").scrollIntoView({ behavior: "smooth", block: "start" }); return; }
		const nb = ev.target.closest("[data-np]"); if (nb) { const k = nb.dataset.np; if (k === "today") { setRange("today"); loadData(); return; } if (k === "login") S.npLogin = !S.npLogin; else if (k === "hide") S.npHide = !S.npHide; S.anim = false; render(); return; }
		const r = ev.target.closest("tbody tr[data-id]"); if (r) { openDrawer(r.dataset.id); return; }
		const vb = ev.target.closest("[data-v]");
		if (vb) {
			if (vb.dataset.v === "table") { const e = EMP[S.empView]; S.q = e ? e.name : S.empView; $$("fQ").value = S.q; S.tab = "all"; }
			S.empView = null; S.anim = true; render(); $$("tabs").scrollIntoView({ behavior: "smooth", block: "start" }); return;
		}
		const zi = ev.target.closest("img[data-zoom]"); if (zi) { openLightbox(zi.src); return; }
		const hf = ev.target.closest(".dhalf[data-id]"); if (hf) { openDrawer(hf.dataset.id); return; }
		const ec = ev.target.closest(".ecard, tr.mrow"); if (ec) { S.empView = ec.dataset.emp; S.tab = "mon"; S.kpi = null; S.anim = true; render(); $$("tabs").scrollIntoView({ behavior: "smooth", block: "start" }); }
	});
	$$("view").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { const rr = ev.target.closest("tr[data-rp]"); if (rr) { rr.click(); return; } const r = ev.target.closest("tbody tr[data-id]"); if (r) openDrawer(r.dataset.id); const m = ev.target.closest("tr.mrow"); if (m) m.click(); const xr = ev.target.closest("[data-ex]"); if (xr) xr.click(); const h = ev.target.closest(".dhalf[data-id]"); if (h) openDrawer(h.dataset.id); } });
	$$("scrim").addEventListener("click", closeDrawer);
	$$("lbx").addEventListener("click", closeLightbox);
	document.addEventListener("keydown", (ev) => { if (ev.key !== "Escape") return; if ($$("lbx").classList.contains("on")) closeLightbox(); else if (openId) closeDrawer(); });
	$$("drawer").addEventListener("click", async (ev) => {
		if (ev.target.closest("[data-close]")) { closeDrawer(); return; }
		const zi = ev.target.closest("img[data-zoom]"); if (zi) { openLightbox(zi.src); return; }
		const a = ev.target.closest("[data-act]"); if (!a) return; const p = ROWS.find((x) => x.id === openId); if (!p) return;
		const note = (root.getElementById("noteBox") || {}).value;
		if (a.dataset.act === "reviewed") {
			const r = await callApi("review_punch", { checkin: p.id, reviewed: p.reviewed ? 0 : 1, note: note });
			if (r.ok) { p.reviewed = r.data.reviewed; p.note = r.data.note; p.audit = r.data.audit || p.audit; toast(p.reviewed ? "Marked as reviewed" : "Review removed"); openDrawer(p.id); render(); } else toast("Could not save the review");
		}
		if (a.dataset.act === "note") {
			const r = await callApi("review_punch", { checkin: p.id, reviewed: p.reviewed ? 1 : 0, note: note || "" });
			if (r.ok) { p.note = r.data.note; p.audit = r.data.audit || p.audit; toast("Note saved"); } else toast("Could not save the note");
		}
	});
	$$("exportBtn").addEventListener("click", () => {
		if (isRp()) { rpExport(S.rpEmp); return; }
		const a = rangeArgs(); a.filters = JSON.stringify({ q: S.q, dept: S.dept, loc: S.loc, type: S.type, src: S.src, dev: S.dev, only_flag: S.onlyFlag ? 1 : 0, only_photo: S.onlyPhoto ? 1 : 0, kpi: S.kpi || "" });
		if (S.tab === "ex") { const b = rangeArgs(); b.filters = JSON.stringify({ q: S.q, dept: S.dept, chip: S.exChip }); window.open("/api/method/" + API + "export_exceptions?" + new URLSearchParams(b).toString(), "_blank"); } else if (S.tab === "np") { const b = rangeArgs(); b.filters = JSON.stringify({ q: S.q, dept: S.dept, only_login: S.npLogin ? 1 : 0, hide_covered: S.npHide ? 1 : 0 }); window.open("/api/method/" + API + "export_not_punched?" + new URLSearchParams(b).toString(), "_blank"); } else window.open("/api/method/" + API + "export_punch_audit?" + new URLSearchParams(a).toString(), "_blank");
		toast("Preparing the Excel file…");
	});
	$$("refreshBtn").addEventListener("click", () => { if (isRp()) loadReport(true); else loadData(true); toast("Refreshed"); });
	$$("backBtn").addEventListener("click", () => { if (window.history.length > 1) window.history.back(); else frappe.set_route(""); });
	$$("themeBtn").addEventListener("click", () => { const cur = host.getAttribute("data-theme"); localTheme = cur === "dark" ? "light" : "dark"; try { localStorage.setItem("pa-theme", localTheme); } catch (e) { /* ignore */ } applyTheme(); });
	$$("retention").addEventListener("change", async (e) => { const days = parseInt(e.target.value, 10); const r = await callApi("save_settings", { retention_days: days }); if (r.ok) { DATA.retention_days = days; toast("Photos will be kept for " + days + " days"); } else toast("Could not save the setting"); });

	function syncSettings() {
		const sel = $$("retention"); sel.value = String(DATA.retention_days || 90);
		sel.disabled = !!(DATA.viewer && DATA.viewer.scope === "team");   // only HR / Admin may change it (server enforces too)
		const mode = DATA.liveness_mode || "off";
		$$("liveMode").textContent = { off: "Off", shadow: "Shadow (score logged only)", enforce: "Enforce", active: "Active (blink / head-turn)" }[mode] || mode;
	}
	buildLegend(); applyTheme();
	if (isRp()) loadReport();
	loadData(true);
	return { onShow() {
		if (applyUrlTab()) { render(); if (isRp() && !rpLoading && (!RPD || RPD.period !== S.rp)) loadReport(); }
		if (Date.now() - loadedAt > 120000) loadData(true);
	} };
}
