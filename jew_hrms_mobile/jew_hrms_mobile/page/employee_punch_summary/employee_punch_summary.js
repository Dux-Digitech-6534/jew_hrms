frappe.pages["employee-punch-summary"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Employee Punch Summary"),
		single_column: true,
	});
	wrapper.employee_punch_summary = new EmployeePunchSummaryPage(page, wrapper);
};

frappe.pages["employee-punch-summary"].on_page_show = function (wrapper) {
	if (wrapper.employee_punch_summary && !wrapper.employee_punch_summary.loaded) {
		wrapper.employee_punch_summary.refresh();
	}
};

class EmployeePunchSummaryPage {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.loaded = false;
		this.ready = false;
		this.loading = false;
		this.request_id = 0;
		this.state = {
			page: 1,
			page_length: 25,
			sort_by: "employee_name",
			sort_order: "asc",
			employee: "",
		};
		this.employee_search_id = 0;
		this.inject_styles();
		this.build();
		this.bind_events();
		this.set_defaults();
		this.ready = true;
	}

	icon(name, size = 18) {
		const paths = {
			refresh: '<path d="M20 11a8.1 8.1 0 1 1-2.37-5.73L20 7.64"/><path d="M20 3v4.64h-4.64"/>',
			download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
			search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
			calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
			users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
			in: '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5"/><path d="M15 12H3"/>',
			out: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
			activity: '<path d="M3 12h4l2.5-7 5 14 2.5-7h4"/>',
			chevron_left: '<path d="m15 18-6-6 6-6"/>',
			chevron_right: '<path d="m9 18 6-6-6-6"/>',
			sort: '<path d="m8 9 4-4 4 4"/><path d="m16 15-4 4-4-4"/>',
		};
		return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name] || ""}</svg>`;
	}

	inject_styles() {
		if (document.getElementById("employee-punch-summary-styles")) return;
		const style = document.createElement("style");
		style.id = "employee-punch-summary-styles";
		style.textContent = `
			.employee-punch-summary-page {
				--eps-iris: #5c4de6;
				--eps-iris-soft: #f0eeff;
				--eps-cyan: #0e9c8b;
				--eps-cyan-soft: #e6f7f4;
				--eps-bg: #f3f4f7;
				--eps-surface: #ffffff;
				--eps-text: #171821;
				--eps-muted: #6f7280;
				--eps-border: #e7e9ee;
				--eps-shadow: 0 8px 28px rgba(25, 28, 43, 0.06);
				background: var(--eps-bg);
				color: var(--eps-text);
				font-family: Geist, Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
				min-height: calc(100vh - 120px);
				padding: 24px;
			}
			[data-theme="dark"] .employee-punch-summary-page {
				--eps-bg: #111218;
				--eps-surface: #1a1c24;
				--eps-text: #f5f5f7;
				--eps-muted: #a4a8b4;
				--eps-border: #2a2d38;
				--eps-iris-soft: rgba(92, 77, 230, 0.18);
				--eps-cyan-soft: rgba(14, 156, 139, 0.16);
				--eps-shadow: 0 8px 28px rgba(0, 0, 0, 0.18);
			}
			.employee-punch-summary-page * { box-sizing: border-box; }
			.eps-shell { max-width: 1480px; margin: 0 auto; }
			.eps-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
			.eps-brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
			.eps-brand-mark { width: 42px; height: 42px; padding: 8px; background: var(--eps-surface); border: 1px solid var(--eps-border); border-radius: 12px; object-fit: contain; box-shadow: var(--eps-shadow); }
			.eps-eyebrow { margin: 0 0 4px; color: var(--eps-iris); font-size: 10px; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }
			.eps-title { margin: 0; color: var(--eps-text); font-size: 22px; font-weight: 690; line-height: 1.25; letter-spacing: -.02em; }
			.eps-subtitle { margin: 5px 0 0; color: var(--eps-muted); font-size: 12px; }
			.eps-actions { display: flex; align-items: center; gap: 8px; }
			.eps-btn { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 36px; padding: 7px 14px; border: 1px solid var(--eps-border); border-radius: 999px; background: var(--eps-surface); color: var(--eps-text); font-size: 12px; font-weight: 630; cursor: pointer; transition: border-color .15s ease, background .15s ease, transform .15s ease; }
			.eps-btn:hover:not(:disabled) { border-color: var(--eps-iris); background: var(--eps-iris-soft); transform: translateY(-1px); }
			.eps-btn-primary { border-color: var(--eps-iris); background: var(--eps-iris); color: #fff; }
			.eps-btn-primary:hover:not(:disabled) { background: #4f40d4; color: #fff; }
			.eps-btn:disabled { cursor: not-allowed; opacity: .55; }
			.eps-btn .is-spinning { animation: eps-spin .8s linear infinite; }
			@keyframes eps-spin { to { transform: rotate(360deg); } }
			.eps-card { position: relative; border: 1px solid var(--eps-border); border-radius: 16px; background: var(--eps-surface); box-shadow: var(--eps-shadow); overflow: hidden; }
			.eps-accent::before { content: ""; position: absolute; inset: 0 0 auto; height: 2px; background: linear-gradient(90deg, var(--eps-iris), #796cf0 48%, var(--eps-cyan)); }
			.eps-filters { display: grid; grid-template-columns: minmax(170px, .8fr) minmax(170px, .8fr) minmax(280px, 1.4fr); gap: 12px; padding: 17px 18px 14px; margin-bottom: 16px; overflow: visible; z-index: 5; }
			.eps-filter .control-label, .eps-custom-label { display: block; color: var(--eps-muted); font-size: 10.5px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; margin-bottom: 6px; }
			.eps-filter .form-control { min-height: 36px; border-color: var(--eps-border); border-radius: 9px; background: var(--eps-surface); color: var(--eps-text); font-size: 12px; box-shadow: none; }
			.eps-filter .form-control:focus { border-color: var(--eps-iris); box-shadow: 0 0 0 3px rgba(92, 77, 230, .11); }
			.eps-filter .help-box { display: none; }
			.eps-custom-input { position: relative; }
			.eps-custom-input > svg { position: absolute; left: 11px; top: 9px; z-index: 1; color: var(--eps-muted); pointer-events: none; }
			.eps-date-input { width: 100%; min-height: 36px; padding: 7px 10px 7px 37px; border: 1px solid var(--eps-border); border-radius: 9px; outline: 0; background: var(--eps-surface); color: var(--eps-text); font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px; font-weight: 570; color-scheme: light; }
			[data-theme="dark"] .eps-date-input { color-scheme: dark; }
			.eps-date-input:hover { border-color: #cfd2dc; }
			.eps-date-input:focus { border-color: var(--eps-iris); box-shadow: 0 0 0 3px rgba(92, 77, 230, .11); }
			.eps-required-mark { color: #e5484d; }
			.eps-employee-picker { position: relative; min-width: 0; }
			.eps-employee-input { width: 100%; min-height: 36px; padding: 7px 64px 7px 37px; border: 1px solid var(--eps-border); border-radius: 9px; outline: 0; background: var(--eps-surface); color: var(--eps-text); font-size: 12px; }
			.eps-employee-input:focus { border-color: var(--eps-iris); box-shadow: 0 0 0 3px rgba(92, 77, 230, .11); }
			.eps-employee-actions { position: absolute; top: 5px; right: 6px; display: flex; align-items: center; gap: 2px; }
			.eps-field-icon-btn { display: none; place-items: center; width: 26px; height: 26px; padding: 0; border: 0; border-radius: 7px; background: transparent; color: var(--eps-muted); cursor: pointer; }
			.eps-field-icon-btn:hover { background: var(--eps-iris-soft); color: var(--eps-iris); }
			.eps-employee-dropdown { position: absolute; top: calc(100% + 6px); left: 0; z-index: 50; display: none; width: max(100%, 310px); max-height: 265px; margin: 0; padding: 6px; overflow-x: hidden; overflow-y: auto; border: 1px solid var(--eps-border); border-radius: 11px; background: var(--eps-surface); box-shadow: 0 14px 38px rgba(25, 28, 43, .16); list-style: none; }
			.eps-employee-dropdown.is-open { display: block; }
			.eps-employee-option { width: 100%; padding: 9px 10px; border: 0; border-radius: 8px; background: transparent; color: var(--eps-text); text-align: left; cursor: pointer; }
			.eps-employee-option:hover, .eps-employee-option:focus { outline: 0; background: var(--eps-iris-soft); }
			.eps-option-name { display: block; overflow: hidden; font-size: 12px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
			.eps-option-id { display: block; margin-top: 2px; color: var(--eps-muted); font-family: "JetBrains Mono", Consolas, monospace; font-size: 10.5px; }
			.eps-option-status { padding: 13px 10px; color: var(--eps-muted); font-size: 11.5px; text-align: center; }
			.eps-search-label { display: block; color: var(--eps-muted); font-size: 10.5px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; margin-bottom: 6px; }
			.eps-search-wrap { position: relative; }
			.eps-search-wrap svg { position: absolute; left: 11px; top: 9px; color: var(--eps-muted); pointer-events: none; }
			.eps-search { width: 100%; min-height: 36px; padding: 7px 34px 7px 36px; border: 1px solid var(--eps-border); border-radius: 9px; outline: 0; background: var(--eps-surface); color: var(--eps-text); font-size: 12px; }
			.eps-search:focus { border-color: var(--eps-iris); box-shadow: 0 0 0 3px rgba(92, 77, 230, .11); }
			.eps-clear-search { position: absolute; right: 7px; top: 5px; width: 26px; height: 26px; padding: 0; border: 0; border-radius: 50%; background: transparent; color: var(--eps-muted); font-size: 18px; cursor: pointer; display: none; }
			.eps-clear-search:hover { background: var(--eps-iris-soft); color: var(--eps-iris); }
			.eps-meta { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding-top: 1px; color: var(--eps-muted); font-size: 11px; }
			.eps-required { color: var(--eps-iris); font-weight: 600; }
			.eps-cards { position: relative; z-index: 1; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
			.eps-stat { padding: 18px; min-height: 112px; }
			.eps-stat-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
			.eps-stat-label { margin: 0; color: var(--eps-muted); font-size: 10.5px; font-weight: 720; letter-spacing: .07em; text-transform: uppercase; }
			.eps-stat-icon { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; color: var(--eps-iris); background: var(--eps-iris-soft); }
			.eps-stat-cyan .eps-stat-icon { color: var(--eps-cyan); background: var(--eps-cyan-soft); }
			.eps-stat-value { margin-top: 12px; font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace; color: var(--eps-text); font-size: 25px; font-weight: 650; line-height: 1; letter-spacing: -.04em; }
			.eps-table-card { min-height: 330px; }
			.eps-table-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 57px; padding: 12px 16px; border-bottom: 1px solid var(--eps-border); }
			.eps-table-title { margin: 0; color: var(--eps-text); font-size: 13px; font-weight: 680; }
			.eps-range { margin-top: 3px; color: var(--eps-muted); font-size: 11px; }
			.eps-rows-control { display: flex; align-items: center; gap: 8px; color: var(--eps-muted); font-size: 11px; }
			.eps-page-length { height: 32px; padding: 4px 28px 4px 10px; border: 1px solid var(--eps-border); border-radius: 8px; background: var(--eps-surface); color: var(--eps-text); font-size: 11px; outline: none; }
			.eps-table-scroll { overflow: auto; min-height: 210px; }
			.eps-table { width: 100%; min-width: 1050px; border-collapse: separate; border-spacing: 0; }
			.eps-table th { position: sticky; top: 0; z-index: 1; padding: 11px 16px; border-bottom: 1px solid var(--eps-border); background: var(--eps-surface); color: var(--eps-muted); font-size: 10.5px; font-weight: 720; letter-spacing: .055em; text-align: left; text-transform: uppercase; white-space: nowrap; }
			.eps-table th.eps-number, .eps-table td.eps-number { text-align: right; }
			.eps-sort-button { display: inline-flex; align-items: center; gap: 4px; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; letter-spacing: inherit; text-transform: inherit; cursor: pointer; }
			.eps-sort-button:hover, .eps-sort-button.is-active { color: var(--eps-iris); }
			.eps-sort-button.is-desc svg { transform: rotate(180deg); }
			.eps-table td { padding: 12px 16px; border-bottom: 1px solid var(--eps-border); color: var(--eps-text); font-size: 12px; vertical-align: middle; white-space: nowrap; }
			.eps-table tbody tr:last-child td { border-bottom: 0; }
			.eps-table tbody tr:hover td { background: var(--eps-iris-soft); }
			.eps-employee-link { color: var(--eps-iris); font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px; font-weight: 620; }
			.eps-employee-link:hover { color: #4f40d4; text-decoration: underline; }
			.eps-name { font-weight: 610; }
			.eps-count { font-family: "JetBrains Mono", Consolas, monospace; font-variant-numeric: tabular-nums; }
			.eps-pill { display: inline-flex; align-items: center; padding: 4px 9px; border-radius: 999px; background: var(--eps-cyan-soft); color: var(--eps-cyan); font-size: 11px; font-weight: 650; }
			.eps-empty, .eps-error { display: grid; place-items: center; align-content: center; min-height: 260px; padding: 35px; text-align: center; color: var(--eps-muted); }
			.eps-empty-icon { display: grid; place-items: center; width: 52px; height: 52px; margin-bottom: 12px; border-radius: 16px; background: var(--eps-iris-soft); color: var(--eps-iris); }
			.eps-empty h3, .eps-error h3 { margin: 0 0 5px; color: var(--eps-text); font-size: 14px; }
			.eps-empty p, .eps-error p { margin: 0; font-size: 12px; }
			.eps-skeleton-row td { padding: 15px 16px; }
			.eps-skeleton { height: 10px; border-radius: 999px; background: linear-gradient(90deg, var(--eps-border) 25%, var(--eps-bg) 50%, var(--eps-border) 75%); background-size: 200% 100%; animation: eps-shimmer 1.15s infinite; }
			@keyframes eps-shimmer { to { background-position: -200% 0; } }
			.eps-pagination { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 57px; padding: 11px 16px; border-top: 1px solid var(--eps-border); }
			.eps-pagination-info { color: var(--eps-muted); font-size: 11px; }
			.eps-pagination-actions { display: flex; align-items: center; gap: 7px; }
			.eps-page-indicator { min-width: 76px; color: var(--eps-muted); font-size: 11px; text-align: center; }
			.eps-icon-btn { display: grid; place-items: center; width: 32px; height: 32px; padding: 0; border: 1px solid var(--eps-border); border-radius: 9px; background: var(--eps-surface); color: var(--eps-text); cursor: pointer; }
			.eps-icon-btn:hover:not(:disabled) { border-color: var(--eps-iris); color: var(--eps-iris); background: var(--eps-iris-soft); }
			.eps-icon-btn:disabled { opacity: .4; cursor: not-allowed; }
			@media (max-width: 1000px) { .eps-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); } .eps-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
			@media (max-width: 620px) { .employee-punch-summary-page { padding: 14px; } .eps-heading { flex-direction: column; } .eps-actions { width: 100%; } .eps-actions .eps-btn { flex: 1; } .eps-filters, .eps-cards { grid-template-columns: 1fr; } .eps-table-toolbar, .eps-pagination { align-items: flex-start; flex-direction: column; } .eps-meta { align-items: flex-start; flex-direction: column; } }
		`;
		document.head.appendChild(style);
	}

	build() {
		this.page.main.html(`
			<div class="employee-punch-summary-page">
				<div class="eps-shell">
					<header class="eps-heading">
						<div class="eps-brand">
							<img class="eps-brand-mark" src="/assets/jew_hrms_mobile/dux-logo.png" alt="DUX" />
							<div>
								<p class="eps-eyebrow">${__("People Analytics")}</p>
								<h1 class="eps-title">${__("Employee Punch Summary")}</h1>
								<p class="eps-subtitle">${__("Daily attendance punch totals, grouped employee-wise.")}</p>
							</div>
						</div>
						<div class="eps-actions">
							<button class="eps-btn eps-refresh" type="button">${this.icon("refresh", 16)}<span>${__("Refresh")}</span></button>
							<button class="eps-btn eps-btn-primary eps-export" type="button">${this.icon("download", 16)}<span>${__("Export Excel")}</span></button>
						</div>
					</header>

					<section class="eps-card eps-accent eps-filters" aria-label="${__("Filters")}">
						<div class="eps-filter">
							<label class="eps-custom-label" for="eps-from-date">${__("From Date")} <span class="eps-required-mark">*</span></label>
							<div class="eps-custom-input">${this.icon("calendar", 17)}<input id="eps-from-date" class="eps-date-input eps-from-date-input" type="date" /></div>
						</div>
						<div class="eps-filter">
							<label class="eps-custom-label" for="eps-to-date">${__("To Date")} <span class="eps-required-mark">*</span></label>
							<div class="eps-custom-input">${this.icon("calendar", 17)}<input id="eps-to-date" class="eps-date-input eps-to-date-input" type="date" /></div>
						</div>
						<div class="eps-filter eps-employee-picker">
							<label class="eps-custom-label" for="eps-employee-select">${__("Employee Select")}</label>
							<div class="eps-search-wrap">
								${this.icon("search", 17)}
								<input id="eps-employee-select" class="eps-employee-input" type="text" autocomplete="off" placeholder="${__("Search and select employee")}" aria-autocomplete="list" aria-expanded="false" />
								<div class="eps-employee-actions">
									<button class="eps-field-icon-btn eps-open-employee" type="button" title="${__("Open Employee")}">${this.icon("chevron_right", 15)}</button>
									<button class="eps-field-icon-btn eps-clear-employee" type="button" title="${__("Clear Employee")}">&times;</button>
								</div>
							</div>
							<ul class="eps-employee-dropdown" role="listbox"></ul>
						</div>
						<div class="eps-meta">
							<span class="eps-required">${__("From Date and To Date are required")}</span>
							<span>${__("To Date includes the complete selected day")}</span>
						</div>
					</section>

					<section class="eps-cards" aria-label="${__("Summary totals")}">
						${this.stat_card("total_employees", __("Total Employees"), "users")}
						${this.stat_card("total_in", __("Total IN"), "in", true)}
						${this.stat_card("total_out", __("Total OUT"), "out")}
						${this.stat_card("total_punches", __("Total Punches"), "activity", true)}
					</section>

					<section class="eps-card eps-accent eps-table-card">
						<div class="eps-table-toolbar">
							<div><h2 class="eps-table-title">${__("Employee-wise summary")}</h2><div class="eps-range">${__("Waiting for data")}</div></div>
						<label class="eps-rows-control">${__("Rows per page")}
							<select class="eps-page-length"><option>25</option><option>50</option><option>100</option></select>
						</label>
						</div>
						<div class="eps-content"></div>
						<div class="eps-pagination">
							<div class="eps-pagination-info">${__("No rows")}</div>
							<div class="eps-pagination-actions">
								<button class="eps-icon-btn eps-prev" type="button" aria-label="${__("Previous page")}">${this.icon("chevron_left", 17)}</button>
								<span class="eps-page-indicator">${__("Page 1 of 1")}</span>
								<button class="eps-icon-btn eps-next" type="button" aria-label="${__("Next page")}">${this.icon("chevron_right", 17)}</button>
							</div>
						</div>
					</section>
				</div>
			</div>
		`);

		this.$root = this.page.main.find(".employee-punch-summary-page");
	}

	stat_card(key, label, icon, cyan = false) {
		return `<article class="eps-card eps-accent eps-stat ${cyan ? "eps-stat-cyan" : ""}"><div class="eps-stat-top"><p class="eps-stat-label">${label}</p><span class="eps-stat-icon">${this.icon(icon, 18)}</span></div><div class="eps-stat-value" data-stat="${key}">0</div></article>`;
	}

	set_defaults() {
		const today = frappe.datetime.get_today();
		const month_start = `${today.slice(0, 7)}-01`;
		this.$root.find(".eps-from-date-input").val(month_start);
		this.$root.find(".eps-to-date-input").val(today).attr("min", month_start);
		this.$root.find(".eps-page-length").val(String(this.state.page_length));
	}

	bind_events() {
		this.$root.on("change", ".eps-from-date-input", (event) => {
			const from_date = event.currentTarget.value;
			const $to_date = this.$root.find(".eps-to-date-input");
			$to_date.attr("min", from_date || null);
			if (from_date && $to_date.val() && $to_date.val() < from_date) $to_date.val(from_date);
			this.filter_changed();
		});
		this.$root.on("change", ".eps-to-date-input", () => this.filter_changed());
		this.$root.on("focus", ".eps-employee-input", () => this.search_employees());
		this.$root.on("input", ".eps-employee-input", frappe.utils.debounce(() => {
			this.state.employee = "";
			this.toggle_employee_actions(false);
			this.search_employees();
		}, 250));
		this.$root.on("keydown", ".eps-employee-input", (event) => {
			if (event.key === "Escape") this.hide_employee_dropdown();
		});
		this.$root.on("click", ".eps-employee-option", (event) => {
			const employee = decodeURIComponent(event.currentTarget.dataset.employee);
			const label = decodeURIComponent(event.currentTarget.dataset.label);
			this.state.employee = employee;
			this.$root.find(".eps-employee-input").val(label);
			this.toggle_employee_actions(true);
			this.hide_employee_dropdown();
			this.filter_changed();
		});
		this.$root.on("click", ".eps-clear-employee", () => {
			this.state.employee = "";
			this.$root.find(".eps-employee-input").val("").trigger("focus");
			this.toggle_employee_actions(false);
			this.filter_changed();
		});
		this.$root.on("click", ".eps-open-employee", () => {
			if (this.state.employee) frappe.set_route("Form", "Employee", this.state.employee);
		});
		this.$root.on("click", (event) => {
			if (!$(event.target).closest(".eps-employee-picker").length) this.hide_employee_dropdown();
		});
		this.$root.on("click", ".eps-refresh", () => this.refresh());
		this.$root.on("click", ".eps-export", () => this.export_excel());
		this.$root.on("click", ".eps-prev", () => this.change_page(this.state.page - 1));
		this.$root.on("click", ".eps-next", () => this.change_page(this.state.page + 1));
		this.$root.on("change", ".eps-page-length", (event) => {
			this.state.page_length = Number(event.currentTarget.value);
			this.state.page = 1;
			this.refresh();
		});
		this.$root.on("click", ".eps-sort-button", (event) => {
			const field = event.currentTarget.dataset.sort;
			if (this.state.sort_by === field) {
				this.state.sort_order = this.state.sort_order === "asc" ? "desc" : "asc";
			} else {
				this.state.sort_by = field;
				this.state.sort_order = "asc";
			}
			this.state.page = 1;
			this.refresh();
		});
	}

	filter_changed() {
		if (!this.ready) return;
		this.state.page = 1;
		this.refresh();
	}

	args() {
		return {
			from_date: this.$root.find(".eps-from-date-input").val(),
			to_date: this.$root.find(".eps-to-date-input").val(),
			employee: this.state.employee,
			search: "",
			sort_by: this.state.sort_by,
			sort_order: this.state.sort_order,
			page: this.state.page,
			page_length: this.state.page_length,
		};
	}

	async search_employees() {
		const current_search = ++this.employee_search_id;
		const $dropdown = this.$root.find(".eps-employee-dropdown");
		const term = this.state.employee ? "" : this.$root.find(".eps-employee-input").val().trim();
		$dropdown.html(`<li class="eps-option-status">${__("Searching employees...")}</li>`).addClass("is-open");
		this.$root.find(".eps-employee-input").attr("aria-expanded", "true");
		try {
			const response = await frappe.call({
				method: "frappe.desk.search.search_link",
				args: { doctype: "Employee", txt: term, page_length: 20 },
			});
			if (current_search !== this.employee_search_id) return;
			this.render_employee_options(response.message || []);
		} catch (error) {
			if (current_search !== this.employee_search_id) return;
			$dropdown.html(`<li class="eps-option-status">${__("Employees could not be loaded")}</li>`);
		}
	}

	render_employee_options(items) {
		const $dropdown = this.$root.find(".eps-employee-dropdown");
		if (!items.length) {
			$dropdown.html(`<li class="eps-option-status">${__("No employees found")}</li>`).addClass("is-open");
			return;
		}
		const escape = frappe.utils.escape_html;
		const html = items.map((item) => {
			const plain_description = $("<div>").html(item.description || "").text().trim();
			const employee_name = item.label || plain_description || item.value;
			const display_label = employee_name === item.value ? item.value : `${employee_name} · ${item.value}`;
			return `<li role="option"><button class="eps-employee-option" type="button" data-employee="${encodeURIComponent(item.value)}" data-label="${encodeURIComponent(display_label)}"><span class="eps-option-name">${escape(employee_name)}</span><span class="eps-option-id">${escape(item.value)}</span></button></li>`;
		}).join("");
		$dropdown.html(html).addClass("is-open");
	}

	hide_employee_dropdown() {
		this.$root.find(".eps-employee-dropdown").removeClass("is-open");
		this.$root.find(".eps-employee-input").attr("aria-expanded", "false");
	}

	toggle_employee_actions(show) {
		this.$root.find(".eps-open-employee, .eps-clear-employee").css("display", show ? "grid" : "none");
	}

	validate() {
		const args = this.args();
		if (!args.from_date || !args.to_date) {
			frappe.show_alert({ message: __("Please select both From Date and To Date."), indicator: "orange" });
			return false;
		}
		if (args.from_date > args.to_date) {
			frappe.show_alert({ message: __("From Date cannot be after To Date."), indicator: "orange" });
			return false;
		}
		return true;
	}

	async refresh() {
		if (!this.validate()) return;
		const current_request = ++this.request_id;
		this.set_loading(true);
		this.render_loading();
		try {
			const response = await frappe.call({
				method: "jew_hrms_mobile.employee_punch_summary.get_employee_punch_summary",
				args: this.args(),
			});
			if (current_request !== this.request_id) return;
			this.loaded = true;
			this.state.page = response.message.pagination.page;
			this.set_loading(false);
			this.render(response.message);
		} catch (error) {
			if (current_request !== this.request_id) return;
			this.set_loading(false);
			this.render_error(error);
		} finally {
			if (current_request === this.request_id) this.set_loading(false);
		}
	}

	set_loading(loading) {
		this.loading = loading;
		this.$root.find(".eps-btn, .eps-icon-btn, .eps-page-length").prop("disabled", loading);
		this.$root.find(".eps-refresh svg").toggleClass("is-spinning", loading);
	}

	render_loading() {
		const cells = Array.from({ length: 8 }, (_, index) => `<td><div class="eps-skeleton" style="width:${index === 2 || index === 7 ? 78 : 42}%"></div></td>`).join("");
		const rows = Array.from({ length: 6 }, () => `<tr class="eps-skeleton-row">${cells}</tr>`).join("");
		this.$root.find(".eps-content").html(`<div class="eps-table-scroll"><table class="eps-table"><tbody>${rows}</tbody></table></div>`);
	}

	render(data) {
		Object.entries(data.totals).forEach(([key, value]) => {
			this.$root.find(`[data-stat="${key}"]`).text(Number(value || 0).toLocaleString());
		});
		this.render_table(data.rows, data.pagination);
		this.render_pagination(data.pagination);
	}

	sort_header(label, field, number = false) {
		const active = this.state.sort_by === field;
		return `<th class="${number ? "eps-number" : ""}"><button type="button" class="eps-sort-button ${active ? "is-active" : ""} ${active && this.state.sort_order === "desc" ? "is-desc" : ""}" data-sort="${field}">${label}${this.icon("sort", 13)}</button></th>`;
	}

	render_table(rows, pagination) {
		if (!rows.length) {
			this.$root.find(".eps-content").html(`<div class="eps-empty"><div class="eps-empty-icon">${this.icon("search", 22)}</div><h3>${__("No punch data found")}</h3><p>${__("Try changing the date range, employee, or search text.")}</p></div>`);
			this.$root.find(".eps-range").text(__("No matching employees"));
			return;
		}

		const escape = frappe.utils.escape_html;
		const body = rows.map((row, index) => {
			const serial = pagination.start + index;
			const employee_route = `/app/employee/${encodeURIComponent(row.employee)}`;
			return `<tr>
				<td class="eps-number eps-count">${serial}</td>
				<td><a class="eps-employee-link" href="${employee_route}">${escape(row.employee)}</a></td>
				<td class="eps-name">${escape(row.employee_name)}</td>
				<td class="eps-number eps-count">${row.in_count}</td>
				<td class="eps-number eps-count">${row.out_count}</td>
				<td class="eps-number eps-count">${row.total_punches}</td>
				<td class="eps-number eps-count">${row.punch_days}</td>
				<td><span class="eps-pill">${escape(row.punch_summary)}</span></td>
			</tr>`;
		}).join("");

		this.$root.find(".eps-content").html(`<div class="eps-table-scroll"><table class="eps-table">
			<thead><tr>
				<th class="eps-number">${__("S.No.")}</th>
				${this.sort_header(__("Employee ID"), "employee")}
				${this.sort_header(__("Employee Name"), "employee_name")}
				${this.sort_header(__("Check IN Count"), "in_count", true)}
				${this.sort_header(__("Check OUT Count"), "out_count", true)}
				${this.sort_header(__("Total Punches"), "total_punches", true)}
				${this.sort_header(__("Punch Days"), "punch_days", true)}
				<th>${__("Punch Summary")}</th>
			</tr></thead><tbody>${body}</tbody></table></div>`);
		this.$root.find(".eps-range").text(`${pagination.total_rows} ${__("employees in the filtered range")}`);
	}

	render_pagination(pagination) {
		const has_rows = pagination.total_rows > 0;
		this.$root.find(".eps-pagination-info").text(has_rows ? `${__("Showing")} ${pagination.start}–${pagination.end} ${__("of")} ${pagination.total_rows}` : __("No rows"));
		this.$root.find(".eps-page-indicator").text(`${__("Page")} ${pagination.page} ${__("of")} ${pagination.total_pages}`);
		this.$root.find(".eps-prev").prop("disabled", this.loading || pagination.page <= 1);
		this.$root.find(".eps-next").prop("disabled", this.loading || pagination.page >= pagination.total_pages);
	}

	render_error(error) {
		const message = error?.message || __("The summary could not be loaded. Please try again.");
		this.$root.find(".eps-content").html(`<div class="eps-error"><div class="eps-empty-icon">${this.icon("activity", 22)}</div><h3>${__("Unable to load punch summary")}</h3><p>${frappe.utils.escape_html(message)}</p></div>`);
		this.$root.find(".eps-range").text(__("Load failed"));
	}

	change_page(page) {
		if (this.loading || page < 1) return;
		this.state.page = page;
		this.refresh();
	}

	export_excel() {
		if (!this.validate() || this.loading) return;
		const args = this.args();
		delete args.page;
		delete args.page_length;
		const query = new URLSearchParams(args).toString();
		const url = `/api/method/jew_hrms_mobile.employee_punch_summary.download_employee_punch_summary?${query}`;
		window.location.assign(url);
	}
}
