"""ACTIVE liveness: challenge-response, verified entirely on the SERVER.

A photo, a gallery picture or a still on another phone cannot turn its head or blink on demand. The
attendance screen therefore asks the person to do 1-3 RANDOM actions (turn left / turn right / blink) while
the app films a short burst of frames; the server checks the burst with dlib face landmarks.

Layers that make this hard to beat (all enforced here, none trusted from the client):
  1. Random challenge  - steps are drawn server-side per attempt, so a pre-recorded video of "turn left" is
                          useless when this attempt asks "blink then turn right". Every challenge contains at
                          least one head TURN (a still picture can never turn, whatever noise it has).
  2. Single-use token  - bound to the employee and scope, expires in CHALLENGE_TTL seconds, deleted on first
                          use even if the attempt fails -> a captured burst cannot be replayed.
  3. Server-side check - head-turn is measured as nose-position ratio between the jaw edges (a FLAT picture,
                          even if tilted or moved, does not change it like a real 3-D head does); blink is
                          eye-aspect-ratio drop and recovery. The client only supplies raw frames.
  4. Face-swap defence - the frontal frame used for the face MATCH is picked by the server from the burst, and
                          a late frontal frame must be the same person (stops "photo for the first second,
                          live friend for the action").
  5. One face only, steady tracking, enough frames, bounded payload.
  6. Lock-out          - repeated failures lock the employee out for a few minutes (brute-force a photo).
  7. Passive MiniFASNet score is still computed and logged on the chosen frame (extra evidence, never blocks).
  8. Fail-CLOSED       - in active mode an internal error means "try again", never "attendance granted".

SPEED: the face detector is the expensive part (~45 ms/frame on the full frame). After the first frame it is
run only on a crop around the previous face box (~12 ms) with a full-frame re-check every 8th frame, which
also catches a second person walking in. Frames are analysed one at a time and dropped (no 90 images in RAM),
and on success the face encoding of the chosen frame is handed back so the registered-face match does not
have to decode / detect / encode the same picture again.

Site config (site_config.json of THAT site only), mode must be "active" (liveness_service.get_mode):
  jew_hrms_liveness_steps           1-3, default 2     number of random actions
  jew_hrms_liveness_yaw_delta       default 0.10       nose-ratio change needed for a turn
  jew_hrms_liveness_turn_mirror     default false      flip left/right if the camera frames are mirrored
  jew_hrms_liveness_blink_drop      default 0.75       blink = eye-aspect-ratio below this x baseline
  jew_hrms_liveness_bind_tolerance  default 0.50       max face distance between first and last frontal frame
  jew_hrms_liveness_max_failures    default 6          failures before lock-out (0 = never lock)
  jew_hrms_liveness_lockout_seconds default 600
"""
import base64
import random
import statistics
import time

import frappe

from jew_hrms_mobile import liveness_service as base

STEP_TYPES = ("turn_left", "turn_right", "blink")
TURN_TYPES = ("turn_left", "turn_right")
CHALLENGE_TTL = 60
MIN_FRAMES = 10
MAX_FRAMES = 90
MAX_FRAME_BYTES = 400_000
MAX_SIDE = 480
FULL_CHECK_EVERY = 8

MESSAGES = {
	"liveness_required": "Live face scan is required. Please refresh the app and try again.",
	"liveness_expired": "The live-scan session expired. Please try again.",
	"liveness_error": "The live check could not run. Please try again.",
	"too_few_frames": "Could not keep track of your face. Keep your face inside the frame, in good light, and try again.",
	"face_not_steady": "Could not keep track of your face. Keep your face inside the frame, in good light, and try again.",
	"multiple_faces": "Only one face should be in the frame.",
	"not_facing_camera": "Please look straight at the camera when the scan starts.",
	"eyes_not_visible": "Your eyes are not clearly visible. Face the light, remove cap/glasses if possible, and try again.",
	"no_final_frontal": "Please look straight at the camera again at the end of the scan.",
	"face_changed": "The face changed during the scan. Please try again.",
	"not_live": "This looks like a photo or a screen, not a live face. Please use your real face in good light and try again.",
	"step_failed": "Live check failed. Please follow the on-screen instruction (turn your head slowly / blink) and try again.",
}


def _num(key, default, cast=float):
	try:
		return cast(frappe.conf.get(key, default))
	except Exception:
		return default


def _challenge_key(token):
	return "jew_liveness_challenge:%s" % token


def _fail_key(employee, scope="att"):
	return "jew_liveness_fail:%s:%s" % (scope, employee)


def _ttl_for(scope):
	# registration: the admin films, then reviews and presses Save - give that a few minutes
	return 300 if scope == "reg" else CHALLENGE_TTL


# ---------------------------------------------------------------- lock-out
# scope "att" = the employee's own attendance punches, "reg" = face registration of that employee by an admin
# (separate counters, so enrolment mistakes never lock an employee out of marking attendance).
def lockout_message(employee, scope="att"):
	limit = _num("jew_hrms_liveness_max_failures", 6, int)
	if limit <= 0:
		return None
	try:
		count = int(frappe.cache.get_value(_fail_key(employee, scope)) or 0)
	except Exception:
		return None
	if count >= limit:
		return "Too many failed live-scan attempts. Please wait a few minutes and try again, or contact HR."
	return None


def _record_failure(employee, scope="att"):
	try:
		key = _fail_key(employee, scope)
		count = int(frappe.cache.get_value(key) or 0) + 1
		frappe.cache.set_value(key, count, expires_in_sec=_num("jew_hrms_liveness_lockout_seconds", 600, int))
	except Exception:
		pass


def _clear_failures(employee, scope="att"):
	try:
		frappe.cache.delete_value(_fail_key(employee, scope))
	except Exception:
		pass


# ---------------------------------------------------------------- challenge
def new_challenge(employee, scope="att"):
	count = max(1, min(3, _num("jew_hrms_liveness_steps", 2, int)))
	rng = random.SystemRandom()
	steps = rng.sample(STEP_TYPES, count)
	if not any(s in TURN_TYPES for s in steps):
		# a lone blink could in theory be faked by landmark noise on a still picture - always include a turn
		steps[rng.randrange(len(steps))] = rng.choice(TURN_TYPES)
	token = frappe.generate_hash(length=32)
	ttl = _ttl_for(scope)
	frappe.cache.set_value(
		_challenge_key(token), {"employee": employee, "steps": steps, "at": time.time(), "scope": scope}, expires_in_sec=ttl
	)
	return {"token": token, "steps": steps, "ttl": ttl}


def _consume(token, employee, scope="att"):
	"""Single use: the token is deleted the moment it is read, whatever the outcome of the attempt.
	A token issued for one scope (attendance / registration) is useless in the other."""
	if not token:
		return None
	key = _challenge_key(str(token))
	data = frappe.cache.get_value(key)
	if not data:
		return None
	frappe.cache.delete_value(key)
	if data.get("employee") != employee or (data.get("scope") or "att") != scope:
		return None
	if time.time() - float(data.get("at") or 0) > _ttl_for(scope) + 5:
		return None
	return list(data.get("steps") or [])


# ---------------------------------------------------------------- frame analysis
def _decode_frame(b64):
	import cv2
	import numpy as np

	text = str(b64)
	if text.startswith("data:") and "," in text:
		text = text.split(",", 1)[1]
	raw = base64.b64decode(text)
	if not raw or len(raw) > MAX_FRAME_BYTES:
		return None
	bgr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
	if bgr is None:
		return None
	longest = max(bgr.shape[:2])
	if longest > MAX_SIDE:
		scale = MAX_SIDE / float(longest)
		bgr = cv2.resize(bgr, (int(bgr.shape[1] * scale), int(bgr.shape[0] * scale)), interpolation=cv2.INTER_AREA)
	return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _dist(a, b):
	return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _locate(rgb, prev_nbox, force_full):
	"""Face boxes in this frame. After the first frame, look only in a crop around where the face just was
	(3-4x cheaper); fall back to the whole frame whenever the crop does not give exactly one face."""
	import face_recognition
	import numpy as np

	if prev_nbox is not None and not force_full:
		height, width = rgb.shape[:2]
		t, r, b, l = prev_nbox
		top, bottom, left, right = t * height, b * height, l * width, r * width
		margin = 0.6 * max(right - left, bottom - top)
		y1, y2 = int(max(0, top - margin)), int(min(height, bottom + margin))
		x1, x2 = int(max(0, left - margin)), int(min(width, right + margin))
		if y2 - y1 >= 80 and x2 - x1 >= 80:
			found = face_recognition.face_locations(np.ascontiguousarray(rgb[y1:y2, x1:x2]))
			if len(found) == 1:
				ft, fr, fb, fl = found[0]
				return [(ft + y1, fr + x1, fb + y1, fl + x1)], False
	return face_recognition.face_locations(rgb), True


def _frame_metrics(rgb, prev_nbox=None, force_full=True):
	"""{faces, yaw, ear, size, box, nbox} - yaw = nose position between the jaw edges (0.5 = straight on)."""
	import face_recognition

	locations, full = _locate(rgb, prev_nbox, force_full)
	if len(locations) != 1:
		return {"faces": len(locations), "full": full}
	landmarks = face_recognition.face_landmarks(rgb, locations)
	if not landmarks:
		return {"faces": 1}
	lm = landmarks[0]
	chin, nose = lm["chin"], lm["nose_tip"]
	jaw_left, jaw_right = chin[0][0], chin[-1][0]
	width = float(jaw_right - jaw_left)
	if width <= 4:
		return {"faces": 1}
	nose_x = sum(p[0] for p in nose) / float(len(nose))

	def ear(eye):
		return (_dist(eye[1], eye[5]) + _dist(eye[2], eye[4])) / (2.0 * _dist(eye[0], eye[3]) + 1e-6)

	top, right, bottom, left = locations[0]
	height_px, width_px = rgb.shape[:2]
	return {
		"faces": 1,
		"yaw": (nose_x - jaw_left) / width,
		"ear": (ear(lm["left_eye"]) + ear(lm["right_eye"])) / 2.0,
		"size": (right - left) / float(width_px),
		"box": locations[0],
		"full": full,
		"nbox": (top / float(height_px), right / float(width_px), bottom / float(height_px), left / float(width_px)),
	}


def evaluate_steps(metrics, steps, yaw_delta=0.10, mirror=False, blink_drop=0.75, blink_recover=0.88, hold=1, min_valid_ratio=0.0):
	"""Pure function (unit-testable): did this per-frame metric series perform `steps` in order?
	Returns (ok, reason, info). info carries the indices the caller needs for frame selection."""
	if len(metrics) < MIN_FRAMES:
		return False, "too_few_frames", {}
	valid = [(i, m) for i, m in enumerate(metrics) if m and m.get("faces") == 1 and "yaw" in m]
	if sum(1 for m in metrics if m and m.get("faces", 0) > 1) >= 2:
		return False, "multiple_faces", {}
	if len(valid) < max(MIN_FRAMES * 0.8, max(0.6, min_valid_ratio) * len(metrics)):
		return False, "face_not_steady", {}
	baseline = valid[:4]
	yaw_b = statistics.median(m["yaw"] for _, m in baseline)
	if abs(yaw_b - 0.5) > 0.16:
		return False, "not_facing_camera", {}
	# Eye baseline = the typical OPEN-eye value over the whole scan (frames where the head is near straight).
	# The first few frames are noisy while the person settles in, and a blink is only a handful of outliers,
	# so the median of the whole series is far steadier than the first four frames.
	straight = [m["ear"] for _, m in valid if abs(m["yaw"] - yaw_b) <= 0.08] or [m["ear"] for _, m in baseline]
	ear_b = statistics.median(straight)
	if ear_b < 0.12:
		return False, "eyes_not_visible", {}
	pos = baseline[-1][0]
	for step in steps:
		later = [(i, m) for i, m in valid if i > pos]
		if step in ("turn_left", "turn_right"):
			# raw (un-mirrored) frame: the user's LEFT is the image's right, so a left turn raises the ratio
			want = 1.0 if step == "turn_left" else -1.0
			if mirror:
				want = -want
			vmap = dict(valid)

			def _turned(j):
				mj = vmap.get(j)
				return mj is not None and (mj["yaw"] - yaw_b) * want >= yaw_delta

			hit = next((i for i, m in later if _turned(i) and all(_turned(i + k) for k in range(1, max(1, int(hold))))), None)
			if hit is None:
				return False, "step_failed", {"step": step}
			pos = hit
		elif step == "blink":
			low = next((i for i, m in later if m["ear"] < blink_drop * ear_b), None)
			if low is None:
				return False, "step_failed", {"step": step}
			recovered = next((i for i, m in valid if i > low and m["ear"] >= blink_recover * ear_b), None)
			if recovered is None:
				return False, "step_failed", {"step": step}
			pos = recovered
		else:
			return False, "step_failed", {"step": step}
	# the person must be facing the camera again at the end (needed for the face-swap check): in the last
	# quarter of the scan, after the final step, and clearly NOT still turned (a turned head is 0.10+ away)
	tail_from = len(metrics) - max(4, int(len(metrics) * 0.25))
	back_tol = max(0.05, yaw_delta * 0.6)
	finals = [
		(i, m) for i, m in valid
		if i >= max(tail_from, pos) and (abs(m["yaw"] - 0.5) <= 0.10 or abs(m["yaw"] - yaw_b) <= back_tol)
		and abs(m["yaw"] - yaw_b) < yaw_delta
	]
	if not finals:
		return False, "no_final_frontal", {}
	first_idx = min(baseline, key=lambda im: abs(im[1]["yaw"] - 0.5))[0]
	last_idx = min(finals, key=lambda im: abs(im[1]["yaw"] - 0.5))[0]
	return True, "ok", {"first_idx": first_idx, "last_idx": last_idx, "yaw_b": yaw_b, "ear_b": ear_b}


def _encoding(rgb, box):
	import face_recognition

	encodings = face_recognition.face_encodings(rgb, known_face_locations=[box])
	return encodings[0] if encodings else None


def _trace(metrics, key):
	return "[" + ",".join("-" if not m or key not in m else ("%.2f" % m[key]) for m in metrics) + "]"


def _result(ok, code, message=None, **extra):
	out = {"code": code, "message": message or MESSAGES.get(code, MESSAGES["liveness_error"])}
	out.update(extra)
	return ok, out


def verify_active(employee, token, frames_json, scope="att"):
	"""-> (ok, result). On success result["frame"] is the server-chosen frontal frame (base64 JPEG) and
	result["encoding"] its face encoding (list) - the caller uses them for the registered-face MATCH (scope
	"att") or the face to ENROL (scope "reg"). On failure result has code + user message."""
	started = time.perf_counter()
	site = getattr(frappe.local, "site", "?")
	locked = lockout_message(employee, scope)
	if locked:
		return _result(False, "liveness_locked", locked)
	if not token or not frames_json:
		return _result(False, "liveness_required")
	steps = _consume(token, employee, scope)
	if not steps:
		_record_failure(employee, scope)
		return _result(False, "liveness_expired")
	try:
		frames = frappe.parse_json(frames_json) if isinstance(frames_json, str) else frames_json
		if not isinstance(frames, list) or len(frames) < MIN_FRAMES or len(frames) > MAX_FRAMES:
			_record_failure(employee, scope)
			base._log().info("site=%s emp=%s ACTIVE scope=%s steps=%s FAIL=bad_frame_count n=%s", site, employee, scope, steps, len(frames) if isinstance(frames, list) else "?")
			return _result(False, "too_few_frames")
		# one frame at a time - only the metrics are kept, never the decoded pictures
		metrics, previous = [], None
		for n, frame in enumerate(frames):
			rgb = _decode_frame(frame)
			if rgb is None:
				metrics.append(None)
				previous = None
				continue
			m = _frame_metrics(rgb, previous, force_full=(n % FULL_CHECK_EVERY == 0))
			metrics.append(m)
			previous = m.get("nbox") if m.get("faces") == 1 else None
		t_frames = (time.perf_counter() - started) * 1000.0
		ok, reason, info = evaluate_steps(
			metrics, steps,
			yaw_delta=_num("jew_hrms_liveness_yaw_delta", 0.10),
			mirror=bool(frappe.conf.get("jew_hrms_liveness_turn_mirror")),
			blink_drop=_num("jew_hrms_liveness_blink_drop", 0.75),
			hold=_num("jew_hrms_liveness_turn_hold", 1, int),
			min_valid_ratio=_num("jew_hrms_liveness_min_valid_ratio", 0.0),
		)
		enc_first, passive = None, None
		full_checks = sum(1 for m in metrics if m and m.get("full"))
		if ok:
			import numpy as np

			first_rgb = _decode_frame(frames[info["first_idx"]])
			last_rgb = _decode_frame(frames[info["last_idx"]])
			enc_first = _encoding(first_rgb, metrics[info["first_idx"]]["box"])
			enc_last = _encoding(last_rgb, metrics[info["last_idx"]]["box"])
			if enc_first is None or enc_last is None:
				ok, reason = False, "face_not_steady"
			else:
				info["bind_dist"] = float(np.linalg.norm(enc_first - enc_last))
				if info["bind_dist"] > _num("jew_hrms_liveness_bind_tolerance", 0.50):
					ok, reason = False, "face_changed"
			if ok and _num("jew_hrms_liveness_passive", 1, int):
				# extra evidence only (never blocks in active mode): the single-image MiniFASNet score.
				# Set site_config jew_hrms_liveness_passive = 0 to skip it (saves ~0.4 s per scan); default keeps it.
				passive = base.evaluate(first_rgb, metrics[info["first_idx"]]["box"], employee, force=True)
				pmin = frappe.conf.get("jew_hrms_liveness_passive_min")
				if pmin is not None and passive and passive.get("score") is not None and passive["score"] < float(pmin):
					ok, reason = False, "not_live"
		base._log().info(
			"site=%s emp=%s ACTIVE scope=%s steps=%s ok=%s reason=%s frames=%s valid=%s bind=%s passive=%s ms=%.0f t_frames=%.0f t_enc_passive=%.0f full_detects=%s yaw=%s ear=%s",
			site, employee, scope, steps, ok, reason, len(metrics), sum(1 for m in metrics if m and "yaw" in m),
			("%.3f" % info["bind_dist"]) if "bind_dist" in info else "-", (passive or {}).get("score", "-"),
			(time.perf_counter() - started) * 1000.0, t_frames, (time.perf_counter() - started) * 1000.0 - t_frames,
			full_checks, _trace(metrics, "yaw"), _trace(metrics, "ear"),
		)
		if not ok:
			_record_failure(employee, scope)
			return _result(False, "liveness_failed", MESSAGES.get(reason, MESSAGES["step_failed"]), reason=reason)
		_clear_failures(employee, scope)
		return _result(True, "success", "Live scan passed.", frame=frames[info["first_idx"]], encoding=[float(v) for v in enc_first])
	except Exception:
		base._log().error("site=%s emp=%s ACTIVE liveness analysis crashed (fail-closed)", site, employee, exc_info=True)
		return _result(False, "liveness_error")
