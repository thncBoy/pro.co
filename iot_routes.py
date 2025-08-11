# iot_routes.py
from flask import Blueprint, jsonify, request, session
from functools import wraps
from iot_client import iot_status, iot_dispense, get_dispenser_url

# สร้าง blueprint เดียว ชัดเจนด้วย url_prefix
iot_bp = Blueprint("iot", __name__, url_prefix="/iot")

def require_login_bp(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401
        return f(*args, **kwargs)
    return wrapper

@iot_bp.get("/status")
def iot_status_route():
    """Proxy ดูสถานะจาก ESP32"""
    try:
        st = iot_status()  # เรียก http://<ESP32-IP>/status
        return jsonify({"ok": True, "status": st})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

@iot_bp.get("/test")
@require_login_bp
def iot_test():
    """ทดสอบเชื่อมต่อ ESP32: เรียก /status แล้วคืนผล"""
    try:
        data = iot_status()
        return jsonify({"ok": True, "url": get_dispenser_url(), "status": data})
    except Exception as e:
        return jsonify({"ok": False, "url": get_dispenser_url(), "error": str(e)}), 502

@iot_bp.post("/manual-dispense")
@require_login_bp
def iot_manual_dispense():
    """สั่งจ่ายยาแบบ manual จากปุ่มทดสอบ: body/json { "slot": 1..3 }"""
    body = request.get_json(silent=True) or {}
    slot = int(body.get("slot", 0))
    if slot not in (1, 2, 3):
        return jsonify({"ok": False, "error": "slot must be 1..3"}), 400
    try:
        res = iot_dispense(slot)
        return jsonify({"ok": True, "response": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502
