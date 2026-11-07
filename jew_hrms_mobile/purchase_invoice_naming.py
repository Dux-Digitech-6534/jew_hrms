import frappe

DEBIT_NOTE_NAMING_SERIES = "PINV-DN-.YY.-"


def set_debit_note_naming_series(doc, method=None):
	"""Force Purchase Invoice returns/debit notes onto their own numbering series
	(PINV-DN-..) instead of sharing the regular Purchase Invoice series
	(PINV-..). Runs on before_naming, before the default naming_series based
	name is generated, so setting doc.naming_series here is picked up by
	frappe's standard naming logic.
	"""
	if doc.get("is_return") and doc.get("naming_series") != DEBIT_NOTE_NAMING_SERIES:
		doc.naming_series = DEBIT_NOTE_NAMING_SERIES
