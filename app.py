# app.py
from flask import Flask, render_template, request, redirect, session, flash, g, url_for,jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from connDB import supabase
from iot_client import iot_dispense
from iot_routes import iot_bp

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-super-secret-key-change-this-in-production')

# ป้องกัน register blueprint ซ้ำ
if 'iot' not in app.blueprints:
    app.register_blueprint(iot_bp)

# ----------------------- Utilities -----------------------
def require_role(*roles):
    def deco(f):
        @wraps(f)
        def _wrap(*args, **kwargs):
            if 'user_id' not in session:
                flash("Please login to continue", "error")
                return redirect('/login')
            if session.get('role', 'user') not in roles:
                return ("Forbidden", 403)
            return f(*args, **kwargs)
        return _wrap
    return deco

require_admin = require_role('admin')


def require_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please login to continue", "error")
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def log_user_action(user_id, action):
    if action in ("login", "logout"):
        supabase.table('user_logs').insert({'user_id': user_id, 'action': action}).execute()

def update_current_symptom(update_dict: dict):
    """อัปเดตแถว symptoms ปัจจุบันด้วยคีย์ใหม่ symptom_id"""
    symptom_id = session.get('symptom_id')
    if symptom_id:
        supabase.table('symptoms').update(update_dict).eq('symptom_id', symptom_id).execute()


# ===== Dashboard JSON =====
@app.get("/dash/data")
@require_login
def dash_data():
    # 1) ผู้ใช้
    au = supabase.table("v_active_users").select("*").execute().data
    au = au[0] if au else {"total_users": 0, "dau_7d": 0, "mau_30d": 0}

    # 2) สต๊อก
    slots = supabase.table("v_low_stock").select("*").execute().data

    # 3) จ่ายรายวัน
    daily = supabase.table("v_dispense_daily").select("*").execute().data

    # 4) Top ยา
    top = supabase.table("v_top_medicines_30d").select("*").limit(10).execute().data

    # 5) สถานะตู้จาก ESP32
    try:
        from iot_client import iot_status  # ใช้ฟังก์ชันที่คุณมีอยู่แล้ว:contentReference[oaicite:9]{index=9}
        device = iot_status()              # proxy ได้จาก blueprint /iot ด้วยก็ได้:contentReference[oaicite:10]{index=10}
    except Exception as e:
        device = {"ok": False, "error": str(e)}

    recent = supabase.table("v_recent_users_min")\
            .select("username,recommended_medicine,accept_medicine,dispense_status")\
            .limit(5).execute().data

    return jsonify({
        "users": au,
        "slots": slots,
        "dispense_daily": daily,
        "top_meds": top,
        "recent_users": recent
    })

# เติมสต็อกยา
@app.post("/admin/refill")
@require_login
@require_admin
def admin_refill_api():
    data = request.get_json(silent=True) or {}
    try:
        slot = int(data.get("slot", 0))
        qty  = int(data.get("qty", 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="invalid payload"), 400

    if slot <= 0 or qty <= 0:
        return jsonify(ok=False, error="slot/qty invalid"), 400

    # ยืนยันว่ามี slot นี้จริง
    row = supabase.table("slots").select("slot").eq("slot", slot).single().execute()
    if not row.data:
        return jsonify(ok=False, error="slot not found"), 404

    # บันทึกลงเลดเจอร์ (ปล่อยให้คอลัมน์ ts ใช้ DEFAULT now() ฝั่ง DB)
    supabase.table("stock_ledger").insert({
    "slot": slot,
    "qty_change": qty,           # ควรเป็นค่าบวกสำหรับเติมสต็อก
    "reason": "refill",          # << ต้องเป็น reason ไม่ใช่ dispense
    "actor": session.get("username", "admin"),
    "note": "web refill"
    }).execute()

    # อ่านสต็อกล่าสุดตอบกลับ
    cur = supabase.table("slots").select("current_stock").eq("slot", slot).single().execute()
    new_stock = (cur.data or {}).get("current_stock")
    return jsonify(ok=True, new_stock=new_stock), 200


#ประวัติผู้ใช้
@app.get("/users/history")
@require_login
@require_admin
def users_history():
    rows = supabase.table("v_user_history")\
           .select("username,recommended_medicine,accept_medicine,dispense_status")\
           .limit(200).execute().data
    return render_template("user_history.html", rows=rows)

#---------------------------------------------------------------------------------------------------------#

# ----------------------- Medicine Meta -----------------------
def get_medicine_info(medicine):
    medicine_db = {
        "พาราเซตามอล 500mg": {
            "image": "พาราเซตามอล 500mg.jpg",
            "description": "ยาแก้ปวดลดไข้ เหมาะกับปวดศีรษะ ปวดเมื่อยตัว มีไข้",
            "usage": "รับประทานครั้งละ 1–2 เม็ด (500–1000 มก.) ทุก 4–6 ชม. เมื่อมีอาการ ไม่เกิน 8 เม็ด/วัน",
            "doctor_advice": "ดื่มน้ำมาก ๆ พักผ่อนเพียงพอ หากมีไข้/ปวดเกิน 3 วัน หรืออาการแย่ลง ให้พบแพทย์",
            "warning": "หลีกเลี่ยงการใช้ร่วมกับแอลกอฮอล์/ยาที่มีพาราเซตามอลซ้ำซ้อน ผู้ป่วยโรคตับควรปรึกษาแพทย์ก่อนใช้"
        },
        "เกลือแร่ ORS": {
            "image": "เกลือแร่ ORS.jpg",
            "description": "ทดแทนสารน้ำและเกลือแร่จากอาการท้องเสีย/อาเจียน",
            "usage": "ละลายผง 1 ซองในน้ำสะอาดตามปริมาณที่ระบุ จิบบ่อย ๆ ทีละน้อยจนดีขึ้น",
            "doctor_advice": "สังเกตอาการขาดน้ำ (ปากแห้ง ปัสสาวะน้อย หน้ามืด) หากไม่ดีขึ้นใน 24 ชม. ให้พบแพทย์",
            "warning": "ห้ามผสมนม/น้ำอัดลม ไม่ควรชงเข้ม/จางเกินไป ผู้ป่วยไต/หัวใจควรปรึกษาแพทย์ก่อน"
        },
        "กาวิสคอน": {
            "image": "กาวิสคอน.jpg",
            "description": "บรรเทากรดไหลย้อน/แสบร้อนกลางอก",
            "usage": "รับประทานครั้งละ 10–20 มล. หลังอาหารและก่อนนอน",
            "doctor_advice": "เลี่ยงอาหารมัน เผ็ด เปรี้ยวจัด งดนอนทันทีหลังอาหาร 2–3 ชม. หากอาการยังไม่ดีขึ้นให้พบแพทย์",
            "warning": "หญิงตั้งครรภ์/ให้นม และผู้ป่วยไต ควรปรึกษาแพทย์ก่อนใช้ หากปวดท้องรุนแรง ถ่ายดำ อาเจียนเป็นเลือด ให้พบแพทย์ทันที"
        }
    }
    return medicine_db.get(medicine, {
        "image": "default.png",
        "description": "ยาเพื่อบรรเทาอาการเบื้องต้น",
        "usage": "-",
        "doctor_advice": "-",
        "warning": "-"
    })

# map ชื่อยา -> ช่องจ่าย
SLOT_BY_MEDICINE = {
    "พาราเซตามอล 500mg": 1,
    "เกลือแร่ ORS": 2,
    "กาวิสคอน": 3
}

# ----------------------- Auth + Dashboard -----------------------
@app.route('/')
def home():
    return redirect('/login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if not username or not password:
            flash("กรุณากรอกชื่อผู้ใช้และรหัสผ่าน", "error")
            return render_template('register.html')

        hashed = generate_password_hash(password)
        exist = supabase.table('users').select('user_id').eq('username', username).execute()
        if exist.data:
            flash("Username already exists", "error")
            return render_template('register.html')

        res = supabase.table('users').insert({'username': username, 'password': hashed}).execute()
        if res.data:
            flash("Registration successful! Please login.", "success")
            return redirect('/login')
        flash("Error: Cannot register user", "error")
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        res = supabase.table('users').select('*').eq('username', username).execute()
        if res.data:
            user = res.data[0]
            if check_password_hash(user['password'], password):
                session['username'] = user['username']
                session['user_id'] = user['user_id']
                session['role'] = user.get('role','user')
                log_user_action(user['user_id'], "login")
                flash("Login successful!", "success")
                if session['role'] == 'admin':
                    return redirect('/dash')
                else:
                    return redirect('/dashboard')
        flash("Incorrect username or password", "error")
    return render_template('login.html')

# ---------- Back stack ----------
NAV_PAGES = {
    'dashboard', 'question_has_fever', 'question_fever', 'severity',
    'question_pregnant', 'question_allergy', 'recommend_medicine',
    'login', 'register',
}

@app.before_request
def _push_nav_stack():
    if request.method == 'GET' and request.endpoint in NAV_PAGES:
        stack = session.get('nav_stack', [])
        if not stack or stack[-1] != request.path:
            stack.append(request.path)
            if len(stack) > 20:
                stack = stack[-20:]
            session['nav_stack'] = stack
        g.back_url = stack[-2] if len(stack) > 1 else url_for('dashboard')
    else:
        g.back_url = url_for('dashboard')

@app.route('/back')
def back():
    stack = session.get('nav_stack', [])
    if len(stack) >= 2:
        stack.pop()
        target = stack.pop()
        session['nav_stack'] = stack + [target]
        return redirect(target)
    return redirect(url_for('dashboard'))

@app.context_processor
def inject_back_url():
    return {'back_url': getattr(g, 'back_url', url_for('dashboard'))}

@app.context_processor
def inject_flags():
    return {
        'is_admin': session.get('role') == 'admin',
        'user_role': session.get('role', 'user')
    }

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_user_action(session['user_id'], "logout")
    session.pop('nav_stack', None)
    session.clear()
    flash("Logout successful!", "success")
    return redirect('/login')

@app.route('/dashboard')
@require_login
def dashboard():
    session['nav_stack'] = ['/dashboard']
    result = supabase.table('symptom_types').select('*').execute()
    symptoms = result.data or []
    return render_template('first.html', symptoms=symptoms)

# ----------------------- Symptom Flow -----------------------
@app.route('/select_symptom', methods=['POST'])
@require_login
def select_symptom():
    raw = int(request.form.get('symptom_type_id'))
    if not raw:
        flash("กรุณาเลือกอาการ", "error")
        return redirect('/dashboard')
    symptom_type_id = int(raw)
    session['symptom_type_id'] = symptom_type_id
    user_id = session['user_id']

    res = supabase.table('symptoms').insert({
        'user_id': user_id,
        'symptom_type_id': symptom_type_id
    }).execute()
    if res.data:
        session['symptom_id'] = res.data[0]['symptom_id']

    q = supabase.table('symptom_types').select(
        'name,skip_severity,ask_has_fever,suggested_medicine'
    ).eq('symptom_type_id', symptom_type_id).execute()
    if not q.data:
        flash("ไม่พบอาการนี้ในระบบ", "error")
        return redirect('/dashboard')

    row = q.data[0]
    name = row.get('name', '')
    skip_severity = row.get('skip_severity', False)
    ask_has_fever = row.get('ask_has_fever', True)
    med = row.get('suggested_medicine', '')

    if name == "อ่อนเพลียจากอาการท้องร่วง/ท้องเสีย":
        update_current_symptom({"severity_note": f"แนะนำยา: {med}"})
        session['medicine'] = med
        return redirect('/recommend_medicine')
    elif name == "มีไข้":
        session['has_fever'] = True
        update_current_symptom({"has_fever": True})
        return redirect('/question_fever')
    elif skip_severity:
        return redirect('/question_pregnant')
    elif ask_has_fever:
        return redirect('/question_has_fever')
    else:
        return redirect('/severity')

@app.route('/question_has_fever', methods=['GET', 'POST'])
@require_login
def question_has_fever():
    if request.method == 'POST':
        has_fever = request.form.get('has_fever') == 'yes'
        session['has_fever'] = has_fever
        update_current_symptom({"has_fever": has_fever})

        symptom_type_id = session.get('symptom_type_id')
        q = supabase.table('symptom_types').select('name').eq('symptom_type_id', symptom_type_id).execute()
        name = q.data[0]['name'] if q.data else ""

        if name == "ปวดกล้ามเนื้อ":
            if has_fever:
                update_current_symptom({"severity_note": "แนะนำพบแพทย์ (ปวดกล้ามเนื้อ+ไข้)"})
                session['medicine'] = None
                return render_template('advise_doctor.html', reason=("เนื่องจากคุณมีอาการปวดกล้ามเนื้อร่วมกับไข้ "
                                                                     "ซึ่งอาจเป็นสัญญาณของไข้หวัดใหญ่หรือโรคที่ต้องดูแลโดยแพทย์ "
                                                                     "ขอแนะนำให้ไปพบแพทย์เพื่อรับการวินิจฉัยและรักษาที่เหมาะสม"))
            else:
                return redirect('/severity')
        elif name in ["ปวดหัว"]:
            return redirect('/question_fever' if has_fever else '/severity')
        else:
            return redirect('/severity')
    return render_template('question_has_fever.html')

@app.route('/question_fever', methods=['GET', 'POST'])
@require_login
def question_fever():
    symptom_type_id = session.get('symptom_type_id')
    if request.method == 'POST':
        muscle_pain = request.form.get('muscle_pain') == 'yes'
        session['muscle_pain'] = muscle_pain
        update_current_symptom({"muscle_pain": muscle_pain})

        if muscle_pain:
            update_current_symptom({"severity_note": "แนะนำพบแพทย์ (ปวดเมื่อยกล้ามเนื้อ+ไข้)"})
            session['medicine'] = None
            return render_template('advise_doctor.html',
                                   reason=("เนื่องจากคุณมีอาการปวดกล้ามเนื้อร่วมกับไข้ "
                                           "ซึ่งอาจเป็นสัญญาณของไข้หวัดใหญ่หรือโรคที่ต้องดูแลโดยแพทย์ "
                                           "ขอแนะนำให้ไปพบแพทย์เพื่อรับการวินิจฉัยและรักษาที่เหมาะสม"))
        else:
            return redirect('/question_pregnant' if symptom_type_id == 8 else '/severity')
    return render_template('fever_muscle.html')

@app.route('/severity')
@require_login
def severity():
    return render_template('severity.html')

@app.route('/submit_severity', methods=['POST'])
@require_login
def submit_severity():
    raw = int(request.form.get('severity'))
    try:
        severity = int(raw)
    except (TypeError, ValueError):
        flash("กรุณาเลือกระดับความปวด", "error")
        return redirect('/severity')
    severity = int(raw)
    note = request.form.get('note')
    update_current_symptom({"severity": severity, "severity_note": note})
    session['severity'] = severity

    if severity >= 5:
        update_current_symptom({"severity_note": "แนะนำพบแพทย์ (severity >= 5)"})
        session['medicine'] = None
        return render_template('advise_doctor.html', reason="อาการของคุณอยู่ในระดับค่อนข้างรุนแรง โปรดพบแพทย์หรือเภสัชใกล้บ้านท่าน")
    else:
        return redirect('/question_pregnant')

@app.route('/question_pregnant', methods=['GET', 'POST'])
@require_login
def question_pregnant():
    if request.method == 'POST':
        pregnant = request.form.get('pregnant') == 'yes'
        session['is_pregnant'] = pregnant

        symptom_type_id = session.get('symptom_type_id')
        q = supabase.table('symptom_types').select('name', 'suggested_medicine')\
             .eq('symptom_type_id', symptom_type_id).execute()
        name = q.data[0]['name'] if q.data else ""
        med = q.data[0]['suggested_medicine'] if q.data else ""

        if pregnant:
            update_current_symptom({"severity_note": "แนะนำให้พบแพทญ์เนื่องจากอยู่ระหว่างตั้งครรภ์", "is_pregnant": True})
            session['medicine'] = None
            return render_template('advise_doctor.html', reason="คุณอยู่ในระหว่างตั้งครรภ์ โปรดพบแพทย์หรือเภสัชใกล้บ้านท่านเพื่อรับการรักษาเฉพาะทาง")
        elif name == "กรดไหลย้อน":
            update_current_symptom({"severity_note": f"แนะนำยา: {med}"})
            session['medicine'] = med
            return redirect('/recommend_medicine')
        else:
            return redirect('/question_allergy')
    return render_template('pregnant.html')

@app.route('/question_allergy', methods=['GET', 'POST'])
@require_login
def question_allergy():
    if request.method == 'POST':
        allergy = request.form.get('allergy') == 'yes'
        session['paracetamol_allergy'] = allergy
        severity = session.get('severity')
        is_pregnant = session.get('is_pregnant', False)
        symptom_type_id = session.get('symptom_type_id')

        if allergy:
            update_current_symptom({
                "severity": severity,
                "severity_note": "แนะนำพบแพทย์ (แพ้พาราเซตามอล)",
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": allergy
            })
            session['medicine'] = None
            return render_template("advise_doctor.html", reason="เนื่องจากคุณแพ้ยาพาราเซตามอล โปรดพบแพทย์หรือเภสัชใกล้บ้านท่านเพื่อรับการรักษาเฉพาะทาง")
        else:
            q = supabase.table('symptom_types').select('suggested_medicine').eq('symptom_type_id', symptom_type_id).execute()
            medicine = q.data[0]['suggested_medicine'] if q.data else "พาราเซตามอล 500mg"
            update_current_symptom({
                "severity": severity,
                "severity_note": f"แนะนำยา: {medicine}",
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": allergy
            })
            session['medicine'] = medicine
            return redirect('/recommend_medicine')
    return render_template("allergy.html")

# ----------------------- Dispense / Finish -----------------------
@app.route('/recommend_medicine')
@require_login
def recommend_medicine():
    medicine = session.get('medicine')
    if not medicine:
        flash("ไม่สามารถแสดงผลการแนะนำยาได้", "error")
        return redirect('/dashboard')
    info = get_medicine_info(medicine)
    return render_template("recommend_result.html",
                           medicine=medicine,
                           image_name=info.get("image"),
                           description=info.get("description"),
                           usage=info.get("usage"),
                           warning=info.get("warning"))

@app.route('/dispense_success', methods=['GET'])
@require_login
def dispense_success():
    medicine = session.get('medicine', 'ยา')
    try:
        update_current_symptom({"dispense_status": "success"})
    except Exception:
        pass
    return render_template('dispense_success.html', medicine=medicine, redirect_ms=4000)

MAX_RETRY = 2
DEFAULT_MAX_WAIT_MS = 60000

@app.route('/dispense_loading', methods=['GET', 'POST'])
@require_login
def dispense_loading():
    if request.method == 'POST':
        update_current_symptom({"accept_medicine": "รับยา"})
        session['dispense_attempts'] = session.get('dispense_attempts', 0)

    medicine = session.get('medicine')
    if not medicine or medicine not in SLOT_BY_MEDICINE:
        flash("ไม่พบข้อมูลช่องสำหรับยาที่เลือก", "error")
        return redirect('/dashboard')

    slot = SLOT_BY_MEDICINE[medicine]
    info = get_medicine_info(medicine) if medicine else {"doctor_advice": "-"}
    doctor_advice = info.get("doctor_advice", "-")

    try:
        resp = iot_dispense(slot)
        if not resp.get("ok", False):
            flash(f"สั่งจ่ายยาไม่สำเร็จ: {resp}", "error")
            return redirect('/dispense_failed')
    except Exception as e:
        flash(f"เชื่อมต่อเครื่องจ่ายยาไม่ได้: {e}", "error")
        return redirect('/dispense_failed')

    return render_template("loading_dispense.html",
                           medicine=medicine,
                           doctor_advice=doctor_advice,
                           max_wait_ms=DEFAULT_MAX_WAIT_MS,
                           poll_every_ms=400)

@app.route('/decline_medicine', methods=['POST'])
@require_login
def decline_medicine():
    update_current_symptom({"accept_medicine": "ไม่รับยา"})
    session.pop('medicine', None)
    session.pop('symptom_id', None)
    return redirect('/goodbye')

@app.route('/goodbye')
def goodbye():
    session.clear()
    return render_template("goodbye.html")

@app.route('/dispense_success_cb', methods=['POST'])
def dispense_success_cb():
    data = request.get_json(silent=True) or {}
    try:
        if "symptom_id" in data:
            supabase.table('symptoms').update({"dispense_status": "success"})\
                    .eq('symptom_id', int(data["symptom_id"])).execute()
        else:
            update_current_symptom({"dispense_status": "success"})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.route('/dispense_failed')
@require_login
def dispense_failed():
    update_current_symptom({"dispense_status": "timeout"})
    attempts = session.get('dispense_attempts', 0)
    return render_template('dispense_failed.html', attempts=attempts, max_retry=MAX_RETRY)

@app.route('/dispense_retry', methods=['POST'])
@require_login
def dispense_retry():
    attempts = session.get('dispense_attempts', 0)
    if attempts >= MAX_RETRY:
        flash("พยายามจ่ายยาครบจำนวนครั้งแล้ว กรุณาติดต่อเจ้าหน้าที่", "warning")
        return redirect('/dispense_failed')
    session['dispense_attempts'] = attempts + 1
    update_current_symptom({"dispense_status": f"retry{session['dispense_attempts']}"})
    return redirect('/dispense_loading')

@app.route('/dispense_cancel', methods=['POST'])
@require_login
def dispense_cancel():
    update_current_symptom({"dispense_status": "cancel"})
    return redirect('/goodbye')


# ----------------------- Entrypoint -----------------------

@app.get("/dash")
@require_login
@require_admin
def dash_page():
    return render_template("dash.html")



if __name__ == '__main__':
    app.run(debug=True)
