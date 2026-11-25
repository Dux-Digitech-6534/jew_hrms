"""Passive face liveness / anti-spoofing: catches a PHOTO or a PHONE SCREEN held up to the camera.

The face matcher (face_service.py, dlib) only answers "is this the right person?". It cannot tell a
live person from a picture of them. This module adds that second check on the SAME single selfie
(no extra capture step, no change to the mobile app flow).

Model: MiniFASNetV2-SE, INT8 ONNX (~600 KB, Apache-2.0), from github.com/facenox/face-antispoof-onnx
  input  "input"  float32 [N,3,128,128]  RGB, CHW, scaled to 0..1
  output "output" float32 [N,2]          two logits (real / spoof)
  crop: square box = 1.5x the detected face box, reflect-101 padding past the image edge, resized to 128.

Control - PER SITE, in that site's site_config.json (never in common_site_config.json, which would
apply to every site on the bench):
  jew_hrms_liveness_mode            "off" (default) | "shadow" | "enforce"
        off     nothing runs - behaviour is byte-for-byte the old one
        shadow  the score is computed and LOGGED, attendance is never blocked
        enforce a clearly-not-live face is rejected with code "liveness_failed"
  jew_hrms_liveness_threshold       float, default 0.0; live when (real_logit - spoof_logit) >= threshold
  jew_hrms_liveness_real_index      0 or 1, default 0 - which output column is "real"
  jew_hrms_liveness_enforce_employees   optional list of Employee ids; in enforce mode only these
        employees can be blocked, everyone else is treated as shadow (staged rollout)

EVERYTHING HERE FAILS OPEN: a missing model, a runtime error, a bad crop - none of them can ever block
an attendance punch. The only thing that blocks is a successfully computed "not live" score in enforce mode.

Scores are appended to logs/jew_hrms_liveness.log (one line per punch attempt) so a threshold can be tuned
from real data before enforcing.
"""
import logging
import logging.handlers
import os
import threading
import time

import frappe

MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "best_model_quantized.onnx")
INPUT_SIZE = 128
BBOX_EXPANSION = 1.5
DEFAULT_THRESHOLD = 0.0

_state = {"session": None, "input_name": None, "failed": False}
_lock = threading.Lock()


def _log():
	"""Our OWN file logger at INFO level. frappe.logger() is ERROR-level unless developer_mode is on,
	so INFO score lines written through it are silently dropped - hence this dedicated handler.
	One bench-level file: <bench>/logs/jew_hrms_liveness.log (every line carries the site name)."""
	lg = logging.getLogger("jew_hrms_liveness_scores")
	if not lg.handlers:
		try:
			path = os.path.join(frappe.utils.get_bench_path(), "logs", "jew_hrms_liveness.log")
			handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5)
			handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
			lg.addHandler(handler)
			lg.setLevel(logging.INFO)
			lg.propagate = False
		except Exception:
			lg.addHandler(logging.NullHandler())
	return lg


def get_mode():
	mode = str(frappe.conf.get("jew_hrms_liveness_mode") or "off").strip().lower()
	return mode if mode in ("shadow", "enforce", "active") else "off"


def active_enabled():
	"""True when this site requires the ACTIVE (challenge-response) live scan. In this mode the passive
	single-image score above is still computed and logged, but never blocks (see evaluate / _enforced_for)."""
	return get_mode() == "active"


# Per-employee switch (Employee.custom_live_face_check): HR, or the employee's Dept Head from the Punch audit page
# ("Live check" tab), turns the live scan on for ONE person while the rest of the site stays on the normal selfie.
EMPLOYEE_FLAG = "custom_live_face_check"


def employee_flag(employee):
	"""True when the live face check is switched on for this one employee."""
	if not employee:
		return False
	try:
		if not frappe.db.has_column("Employee", EMPLOYEE_FLAG):
			return False
		return bool(frappe.db.get_value("Employee", employee, EMPLOYEE_FLAG))
	except Exception:
		return False


def active_for(employee):
	"""ACTIVE live scan for this employee's Mark In / Mark Out: the whole site is on "active", or this employee's
	own switch is on. Face REGISTRATION keeps using the site-wide active_enabled() only."""
	return active_enabled() or employee_flag(employee)


def _threshold():
	try:
		return float(frappe.conf.get("jew_hrms_liveness_threshold", DEFAULT_THRESHOLD))
	except Exception:
		return DEFAULT_THRESHOLD


def _real_index():
	try:
		return 1 if int(frappe.conf.get("jew_hrms_liveness_real_index", 0)) == 1 else 0
	except Exception:
		return 0


def _enforced_for(employee):
	if get_mode() != "enforce":
		return False
	only = frappe.conf.get("jew_hrms_liveness_enforce_employees")
	if isinstance(only, (list, tuple, set)) and only:
		return employee in set(only)
	return True


def _get_session():
	"""Lazy, once per worker process. Returns (session, input_name) or (None, None)."""
	if _state["session"] is not None:
		return _state["session"], _state["input_name"]
	if _state["failed"]:
		return None, None
	with _lock:
		if _state["session"] is not None:
			return _state["session"], _state["input_name"]
		try:
			import onnxruntime as ort
			if not os.path.exists(MODEL_FILE):
				raise FileNotFoundError(MODEL_FILE)
			opts = ort.SessionOptions()
			# One thread: this runs inside web workers next to other requests, so don't fan out.
			opts.intra_op_num_threads = 1
			opts.inter_op_num_threads = 1
			session = ort.InferenceSession(MODEL_FILE, sess_options=opts, providers=["CPUExecutionProvider"])
			_state["session"] = session
			_state["input_name"] = session.get_inputs()[0].name
		except Exception:
			_state["failed"] = True
			_log().error("site=%s liveness model load failed (fail-open)", getattr(frappe.local, "site", "?"), exc_info=True)
			return None, None
	return _state["session"], _state["input_name"]


def _preprocess(image, face_box):
	"""image: uint8 RGB ndarray (H,W,3). face_box: (top, right, bottom, left) as face_recognition returns."""
	import cv2
	import numpy as np

	top, right, bottom, left = [float(v) for v in face_box]
	w, h = right - left, bottom - top
	if w <= 1 or h <= 1:
		return None
	cx, cy = left + w / 2.0, top + h / 2.0
	side = max(int(round(max(w, h) * BBOX_EXPANSION)), 2)
	x1, y1 = int(round(cx - side / 2.0)), int(round(cy - side / 2.0))
	x2, y2 = x1 + side, y1 + side
	height, width = image.shape[:2]
	pad_l, pad_t = max(0, -x1), max(0, -y1)
	pad_r, pad_b = max(0, x2 - width), max(0, y2 - height)
	img = np.ascontiguousarray(image)
	if pad_l or pad_t or pad_r or pad_b:
		img = cv2.copyMakeBorder(img, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_REFLECT_101)
	crop = img[y1 + pad_t:y2 + pad_t, x1 + pad_l:x2 + pad_l]
	if crop.size == 0:
		return None
	interp = cv2.INTER_LANCZOS4 if crop.shape[0] < INPUT_SIZE else cv2.INTER_AREA
	crop = cv2.resize(crop, (INPUT_SIZE, INPUT_SIZE), interpolation=interp)
	arr = crop.astype("float32") / 255.0
	return np.transpose(arr, (2, 0, 1))[None, ...]


def evaluate(image, face_box, employee=None, force=False):
	"""Score one selfie. Returns None when liveness is off or unavailable (caller then does nothing),
	otherwise a plain-JSON dict:
	  {"mode", "enforce", "is_real", "score", "real_logit", "spoof_logit", "threshold", "ms"}
	force=True (the active live scan of a per-employee switch) scores even when the site mode is "off"."""
	mode = get_mode()
	if mode == "off" and not force:
		return None
	try:
		session, input_name = _get_session()
		if session is None:
			return None
		started = time.perf_counter()
		tensor = _preprocess(image, face_box)
		if tensor is None:
			return None
		logits = session.run(None, {input_name: tensor})[0][0]
		ri = _real_index()
		real_logit, spoof_logit = float(logits[ri]), float(logits[1 - ri])
		score = real_logit - spoof_logit
		threshold = _threshold()
		return {
			"mode": mode,
			"enforce": _enforced_for(employee),
			"is_real": bool(score >= threshold),
			"score": round(score, 4),
			"real_logit": round(real_logit, 4),
			"spoof_logit": round(spoof_logit, 4),
			"threshold": threshold,
			"ms": round((time.perf_counter() - started) * 1000.0, 1),
		}
	except Exception:
		_log().error("site=%s emp=%s liveness evaluate failed (fail-open)", getattr(frappe.local, "site", "?"), employee, exc_info=True)
		return None


def log_attempt(employee, live, matched=None, distance=None, blocked=False):
	"""One line per punch attempt - the data used to tune the threshold."""
	if not live:
		return
	try:
		_log().info(
			"site=%s emp=%s mode=%s score=%s real=%s spoof=%s thr=%s is_real=%s enforce=%s blocked=%s matched=%s dist=%s ms=%s",
			getattr(frappe.local, "site", "?"), employee, live.get("mode"), live.get("score"), live.get("real_logit"),
			live.get("spoof_logit"), live.get("threshold"), live.get("is_real"), live.get("enforce"), blocked,
			matched, distance, live.get("ms"),
		)
	except Exception:
		pass
