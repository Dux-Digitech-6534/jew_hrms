frappe.ui.form.on("Leave Details", {
	onload(frm) {
		if (frm.is_new() && !frm.doc.year) {
			frm.set_value("year", new Date().getFullYear());
		}
		set_employee_query(frm);
	},
	refresh(frm) {
		set_employee_query(frm);
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Fetch Leaves"), () => fetch_leaves(frm));
		}
	},
	company(frm) {
		frm.set_value("employee", "");
		frm.clear_table("leaves");
		frm.refresh_field("leaves");
		["allotted_cl", "allotted_pl", "remaining_cl", "remaining_pl"].forEach((f) => frm.set_value(f, 0));
		set_employee_query(frm);
	},
	employee(frm) {
		if (frm.doc.employee) fetch_leaves(frm);
	},
	month(frm) {
		if (frm.doc.employee) fetch_leaves(frm);
	},
	year(frm) {
		if (frm.doc.employee) fetch_leaves(frm);
	},
});

function set_employee_query(frm) {
	frm.set_query("employee", () => ({
		filters: { company: frm.doc.company, status: "Active" },
	}));
}

function fetch_leaves(frm) {
	if (frm.doc.docstatus !== 0) return;
	if (!(frm.doc.company && frm.doc.month && frm.doc.year && frm.doc.employee)) return;
	frappe.call({
		method: "jew_hrms_mobile.jew_hrms_mobile.doctype.leave_details.leave_details.fetch_leave_details",
		args: { company: frm.doc.company, month: frm.doc.month, year: frm.doc.year, employee: frm.doc.employee },
		freeze: true,
		freeze_message: __("Fetching leaves..."),
		callback: (r) => {
			const d = r.message || {};
			["allotted_cl", "allotted_pl", "remaining_cl", "remaining_pl"].forEach((f) => frm.set_value(f, d[f] || 0));
			frm.clear_table("leaves");
			(d.leaves || []).forEach((row) => {
				const c = frm.add_child("leaves");
				c.date = row.date;
				c.leave_application = row.leave_application;
				c.leave_type = row.leave_type;
				c.half_day = row.half_day;
			});
			frm.refresh_field("leaves");
			frappe.show_alert({ message: __("{0} leave day(s) loaded", [(d.leaves || []).length]), indicator: "green" });
		},
	});
}

// PL / CL / LWP are mutually exclusive per row.
frappe.ui.form.on("Leave Details Item", {
	pl(frm, cdt, cdn) { exclusive(cdt, cdn, "pl"); },
	cl(frm, cdt, cdn) { exclusive(cdt, cdn, "cl"); },
	lwp(frm, cdt, cdn) { exclusive(cdt, cdn, "lwp"); },
});

function exclusive(cdt, cdn, picked) {
	const row = locals[cdt][cdn];
	if (row[picked]) {
		["pl", "cl", "lwp"].filter((f) => f !== picked).forEach((f) => frappe.model.set_value(cdt, cdn, f, 0));
	}
}
