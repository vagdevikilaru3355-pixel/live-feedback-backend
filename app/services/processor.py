# app/services/processor.py
from typing import Dict, Any, Optional
import math

def safe_get(d: Dict, *keys, default=None):
    v = d
    for k in keys:
        if not isinstance(v, dict) or k not in v:
            return default
        v = v[k]
    return v

def score_attention(features: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map raw features to an attention score and short label:
    - faceDetected: bool
    - leftEyeOpen/rightEyeOpen: bool
    - mouthOpen: bool
    - raw: {leftEyeDist, rightEyeDist, lipDist}
    Returns a dict: { label, score (0..1), reason }
    """
    if not features or not features.get("faceDetected"):
        return {"label": "not-visible", "score": 0.0, "reason": "face not detected"}

    left = safe_get(features, "raw", "leftEyeDist", default=0.0) or 0.0
    right = safe_get(features, "raw", "rightEyeDist", default=0.0) or 0.0
    lip = safe_get(features, "raw", "lipDist", default=0.0) or 0.0

    left_open = bool(features.get("leftEyeOpen"))
    right_open = bool(features.get("rightEyeOpen"))
    mouth_open = bool(features.get("mouthOpen"))

    # start with neutral score
    score = 0.6

    # penalize if both eyes closed
    if not left_open and not right_open:
        score -= 0.5
        label = "eyes-closed"
        reason = "both eyes closed"
        return {"label": label, "score": max(0.0, score), "reason": reason}

    # slight penalty when one eye closed
    if not left_open or not right_open:
        score -= 0.25

    # if eye distances are very small -> likely looking away/squint
    if left < 0.004 and right < 0.004:
        score -= 0.35
        label = "looking-away"
        reason = "eye landmarks smaller than expected (possible looking away)"
        return {"label": label, "score": max(0.0, score), "reason": reason}

    # mouth open large -> speaking or laughing (this doesn't reduce attention necessarily)
    if lip > 0.05:
        label = "speaking-or-laughing"
        # if mouth open but eyes open, treat as active (not distracted)
        score = min(1.0, score + 0.15)
        reason = "mouth open (speaking or laughing)"
        return {"label": label, "score": score, "reason": reason}

    # otherwise if eyes are open and sizes normal -> attentive
    if left_open and right_open:
        label = "looking-straight"
        score = min(1.0, score + 0.3)
        reason = "eyes open and landmarks within expected range"

    else:
        # fallback generic attentive
        label = "attentive"
        reason = "partial attention detected"

    return {"label": label, "score": max(0.0, score), "reason": reason}

def generate_feedback(student_id: str, features: Dict[str, Any], derived: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build a human friendly feedback message and structured record.
    Returns:
      {
        student_id,
        ts,
        feedback: { label, score, reason, message },
        features,
        derived
      }
    """
    res = score_attention(features)
    label = res["label"]
    score = res["score"]
    reason = res["reason"]

    # Compose a short natural-language message
    if label == "not-visible":
        message = f"{student_id} is not visible (face not detected)."
    elif label == "eyes-closed":
        message = f"{student_id} seems to have eyes closed — possibly drowsy or looking down."
    elif label == "looking-away":
        message = f"{student_id} appears distracted / looking away."
    elif label == "speaking-or-laughing":
        message = f"{student_id} may be speaking or laughing."
    elif label == "looking-straight":
        message = f"{student_id} appears to be looking at the screen."
    else:
        message = f"{student_id} status: {label}."

    # If derived events exist, prioritize them for the message
    if derived and isinstance(derived.get("events"), list) and derived["events"]:
        # choose highest-priority derived
        ev = derived["events"][0]
        # small mapping to nicer text
        map_ev = {
            "no-face": "is not visible",
            "eyes-closed": "has eyes closed",
            "one-eye-closed": "has one eye closed",
            "mouth-open": "has mouth open",
            "looking-away": "appears distracted / looking away",
            "speaking-or-laughing": "may be speaking or laughing"
        }
        ev_text = map_ev.get(ev, ev)
        message = f"{student_id} {ev_text}."

    return {
        "student_id": student_id,
        "feedback": {
            "label": label,
            "score": score,
            "reason": reason,
            "message": message
        },
        "features": features,
        "derived": derived or {}
    }
