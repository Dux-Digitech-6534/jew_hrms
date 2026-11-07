// Copyright (c) 2026, JEWPL and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Attendance Based Salary Sheet"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "branch",
			label: __("Location"),
			fieldtype: "Link",
			options: "Branch",
		},
		{
			fieldname: "pay_late_half_days",
			label: __("Pay Late-Coming Half Days (do not deduct)"),
			fieldtype: "Check",
			default: 0,
		},
	],
};
