import base64
import json
import os
import tempfile
import traceback

import frappe
from frappe import _


DEFAULT_FACE_TOLERANCE = 0.55


def _result(ok, code="success", message="success", **data):
	out = {"ok": ok, "success": ok, "code": code, "message": message}
	out.update(data)
	return out


def _clean_base64_image(image_data):
	if not image_data:
		return None
	image_data = str(image_data).strip()
	if image_data.startswith("data:image") and "," in image_data:
		return image_data.split(",", 1)[1].strip()
	return image_data


def decode_base64_image(image_data):
	cleaned = _clean_base64_image(image_data)
	if not cleaned:
		frappe.throw(_("Face image is required"))
	try:
		return base64.b64decode(cleaned, validate=True)
	except Exception:
		frappe.throw(_("Invalid face image"))


def decode_image(image_data):
	return decode_base64_image(image_data)


def _temp_image(image_bytes):
	tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
	try:
		tmp.write(image_bytes)
		return tmp.name
	finally:
		tmp.close()


def _face_recognition():
	try:
		import face_recognition
	except Exception:
		return None
	return face_recognition


def _face_tolerance():
	try:
		return float(frappe.conf.get("jew_hrms_face_tolerance", DEFAULT_FACE_TOLERANCE))
	except Exception:
		return DEFAULT_FACE_TOLERANCE


def _load_image(image_data):
	engine = _face_recognition()
	if not engine:
		return None, None, _result(False, "face_engine_not_configured", "Face verification engine is not configured on server.")
	path = None
	try:
		try:
			image_bytes = decode_base64_image(image_data)
		except Exception:
			return None, None, _result(False, "no_face_detected", "No usable face image was received. Start scan and keep your face inside the frame.")
		path = _temp_image(image_bytes)
		try:
			image = engine.load_image_file(path)
		except Exception:
			frappe.logger("jew_hrms_mobile").error("Face image load failed\n%s", traceback.format_exc())
			return None, None, _result(False, "no_face_detected", "No usable face image was received. Please keep your face inside the frame.")
		return engine, image, None
	finally:
		if path and os.path.exists(path):
			os.remove(path)


def get_face_locations(image):
	engine = _face_recognition()
	if not engine:
		return []
	return engine.face_locations(image)


def serialize_encoding(encoding):
	return json.dumps([round(float(v), 8) for v in encoding])


def deserialize_encoding(encoding_text):
	return json.loads(encoding_text) if isinstance(encoding_text, str) else encoding_text


def extract_face_encoding(image_data):
	engine, image, error = _load_image(image_data)
	if error:
		return error
	locations = get_face_locations(image)
	if not locations:
		return _result(False, "no_face_detected", "No face detected. Please keep your face inside the frame.", face_detected_count=0)
	if len(locations) > 1:
		return _result(False, "multiple_faces_detected", "Multiple faces detected. Only one face is allowed.", face_detected_count=len(locations))
	height, width = image.shape[:2]
	top, right, bottom, left = locations[0]
	margin_x = width * 0.03
	margin_y = height * 0.03
	if left <= margin_x or top <= margin_y or right >= width - margin_x or bottom >= height - margin_y:
		return _result(
			False,
			"poor_face_position",
			"Please keep your full face inside the frame.",
			face_detected_count=1,
			face_box={"top": int(top), "right": int(right), "bottom": int(bottom), "left": int(left), "image_width": int(width), "image_height": int(height)},
		)
	encodings = engine.face_encodings(image, known_face_locations=locations)
	if not encodings:
		return _result(False, "no_face_detected", "No face detected. Please keep your face inside the frame.", face_detected_count=1)
	encoding = [round(float(v), 8) for v in encodings[0]]
	return _result(True, "success", "Face template created.", encoding=encoding, face_detected_count=1)


def compare_face(saved_encoding, current_encoding):
	engine = _face_recognition()
	if not engine:
		return _result(False, "face_engine_not_configured", "Face verification engine is not configured on server.")
	try:
		saved = deserialize_encoding(saved_encoding)
		if not saved or not current_encoding:
			return _result(False, "face_not_matched", "Face template is invalid.")
		import numpy as np
		saved = np.array(saved, dtype="float64")
		current = np.array(current_encoding, dtype="float64")
		tolerance = _face_tolerance()
		matches = engine.compare_faces([saved], current, tolerance=tolerance)
		distance = float(engine.face_distance([saved], current)[0])
	except Exception:
		frappe.logger("jew_hrms_mobile").error("Face comparison failed\n%s", traceback.format_exc())
		return _result(False, "face_not_matched", "Face verification failed.")
	matched = bool(matches and matches[0])
	frappe.logger("jew_hrms_mobile").info("Face match distance for employee verification: %.4f threshold %.4f matched %s", distance, tolerance, matched)
	return _result(
		matched,
		"success" if matched else "face_not_matched",
		"Face matched." if matched else "Face did not match. Please scan again with a clear face image.",
		distance=round(distance, 4),
		threshold=tolerance,
		matched=matched,
	)


def register_face(employee, image_data):
	extracted = extract_face_encoding(image_data)
	if not extracted.get("ok"):
		return extracted
	return _result(True, "success", "Face registered successfully.", employee=employee, encoding=extracted["encoding"])


def verify_face(employee, image_data, saved_encoding):
	extracted = extract_face_encoding(image_data)
	if not extracted.get("ok"):
		return extracted
	compare = compare_face(saved_encoding, extracted["encoding"])
	compare["employee"] = employee
	return compare


def get_face_engine_status():
	if not _face_recognition():
		return _result(False, "face_engine_not_configured", "Face verification engine is not configured on server.")
	return _result(True, "success", "Face verification engine is ready.")
