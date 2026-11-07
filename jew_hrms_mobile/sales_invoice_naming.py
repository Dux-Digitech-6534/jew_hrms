import frappe

CREDIT_NOTE_NAMING_SERIES = "JEW/CN/26-27/.##"


def set_credit_note_naming_series(doc, method=None):
	"""Force Sales Invoice returns/credit notes onto their own numbering series
	(JEW/CN/26-27/..) instead of sharing the regular Sales Invoice series
	(JEW/IND/26-27/..). Kept short (no 'IND' segment) so the generated name
	stays within GST's 16-character transaction name limit. Runs on
	before_naming, before the default naming_series based name is generated,
	so setting doc.naming_series here is picked up by frappe's standard
	naming logic.
	"""
	if doc.get("is_return") and doc.get("naming_series") != CREDIT_NOTE_NAMING_SERIES:
		doc.naming_series = CREDIT_NOTE_NAMING_SERIES
