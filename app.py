# app.py
from flask import Flask, render_template, request, redirect, session, flash, g, url_for,jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
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

def update_current_symptom(fields: dict):
    """อัปเดตข้อมูลใน case_logs ของ session ปัจจุบัน"""

    sid = session.get("symptom_id")
    if not sid:
        return None

    # แปลงกรณีส่ง medicine_name → ให้หาว่า medicine_id อะไร
    if "medicine" in fields and "medicine_id" not in fields:
        med_name = fields.pop("medicine")
        q = supabase.table("medicines").select("medicine_id").eq("medicine_name", med_name).execute()
        if q.data:
            fields["medicine_id"] = q.data[0]["medicine_id"]

    # เขียนลง DB
    res = supabase.table("case_logs").update(fields).eq("symptom_id", sid).execute()
    return res.data



# ===== Dashboard JSON =====
def _range_params(key: str):
    key = (key or "month").lower()
    now = datetime.now(timezone.utc)
    if key == "day":
        start, bucket = now - timedelta(days=1), "hour"
    elif key == "week":
        start, bucket = now - timedelta(days=7), "day"
    elif key == "year":
        start, bucket = now - timedelta(days=365), "month"
    else:  # month (default 30 วัน)
        start, bucket = now - timedelta(days=30), "day"
    return key, start.isoformat(), now.isoformat(), bucket


def _label(dt: datetime, bucket: str) -> str:
    if bucket == "hour":  return dt.strftime("%d %b %H:00")
    if bucket == "month": return dt.strftime("%b %Y")
    return dt.strftime("%d %b")

# ---------- API สำหรับแดชบอร์ด ----------
@app.route('/dash/data')
@require_admin
def dash_data():
    # resolve range -> start_ts
    range_map = {'day': 1, 'week': 7, 'month': 30, 'year': 365}
    rng = range_map.get(request.args.get('range', 'month'), 30)
    start_ts = (datetime.now(timezone.utc) - timedelta(days=rng)).isoformat()

    out = {}

    # 1) Users summary (วิวมาตรฐาน)
    try:
        ua = supabase.from_('v_active_users').select('total_users,dau_7d,mau_30d').limit(1).execute().data
        out['users'] = ua[0] if ua else {'total_users': 0, 'dau_7d': 0, 'mau_30d': 0}
    except Exception:
        out['users'] = {'total_users': 0, 'dau_7d': 0, 'mau_30d': 0}

    # 2) Stock (ให้ FE ใช้เติม select dropdown ด้วย)
    try:
        stock = supabase.from_('v_medicines_stock')\
            .select('medicine_id,slot,medicine_name,current_stock,low_stock_thr')\
            .execute().data or []
        out['stock'] = stock
    except Exception:
        out['stock'] = []

    # 3) Dispense series — กรองด้วย event_ts (timestamp) ไม่ใช่ label (string)
    try:
        rows = supabase.from_('mv_dispense_events')\
            .select('label,count,event_ts')\
            .gte('event_ts', start_ts)\
            .order('event_ts')\
            .execute().data or []
        out['dispense_series'] = [{'label': r['label'], 'count': r.get('count', 0)} for r in rows]
    except Exception:
        out['dispense_series'] = []

    # 4) Top meds (ใช้วิว enriched + นับจาก medicine_name)
    try:
        top_rows = supabase.from_('v_case_logs_enriched')\
            .select('medicine_name,created_at')\
            .gte('created_at', start_ts)\
            .execute().data or []
        cnt = Counter([r['medicine_name'] for r in top_rows if r.get('medicine_name')])
        out['top_meds'] = [{'med': m, 'cnt': c} for m, c in cnt.most_common(10)]
    except Exception:
        out['top_meds'] = []

    # 5) Recent users (แสดง 5-6 รายล่าสุด)
    try:
        recent = supabase.from_('v_case_logs_enriched')\
            .select('created_at,username,symptom,medicine_name,accept_medicine,dispense_status')\
            .order('created_at', desc=True).limit(6)\
            .execute().data or []
        out['recent_users'] = [{
            'created_at': r['created_at'],
            'username': r.get('username'),
            'symptom': r.get('symptom'),
            'recommended_medicine': r.get('medicine_name'),
            'accept_medicine': r.get('accept_medicine'),
            'dispense_status': r.get('dispense_status') or '-'
        } for r in recent]
    except Exception:
        out['recent_users'] = []

    return jsonify(out)

    
    
# เติมสต็อกยา
@app.post("/admin/refill")
@require_login
@require_admin
def admin_refill():
    if request.is_json:                           # เรียกผ่าน fetch()
        payload = request.get_json(silent=True) or {}
        med_id = int(payload.get("medicine_id", 0))
        qty    = int(payload.get("qty", 0))
        if med_id <= 0 or qty <= 0:
            return jsonify(ok=False, error="invalid payload"), 400
        supabase.rpc("refill_stock", {"p_medicine_id": med_id, "p_qty": qty}).execute()
        return jsonify(ok=True)

    # ---- fallback: submit จาก <form> ปกติ ----
    med_id = int(request.form.get("medicine_id", 0))
    qty    = int(request.form.get("qty", 0))
    if med_id <= 0 or qty <= 0:
        flash("ข้อมูลไม่ถูกต้อง", "error")
        return redirect(url_for("dash_page"))
    supabase.rpc("refill_stock", {"p_medicine_id": med_id, "p_qty": qty}).execute()
    flash("เติมยาสำเร็จ!", "success")
    return redirect(url_for("dash_page"))

#ประวัติผู้ใช้
@app.get("/users/history")
@require_login
@require_admin
def users_history():
    try:
        rows = supabase.rpc("rpc_user_history", {"p_limit": 200}).execute().data or []
    except Exception:
        rows = []
    return render_template("user_history.html", rows=rows)

#---------------------------------------------------------------------------------------------------------#

# ----------------------- Medicine Meta -----------------------
def get_medicine_info(medicine):
    medicine_db = {
        "พาราเซตามอล 500mg": {
            "image": "พาราเซตามอล 500mg.jpg",
            "description": "ยาแก้ปวดลดไข้ เหมาะกับปวดศีรษะ ปวดเมื่อยตัว มีไข้",
            "usage": "รับประทานครั้งละ 1–2 เม็ด (500–1000 มก.) ทุก 4–6 ชม. เมื่อมีอาการ ไม่เกิน 8 เม็ด/วัน",
            "doctor_advice": "ดื่มน้ำมาก ๆ พักผ่อนเพียงพอ หากมีไข้/ปวดเกิน 3 วัน หรืออาการแย่ลงแนะนำให้พบแพทย์ทันที",
            "warning": "หลีกเลี่ยงการใช้ร่วมกับแอลกอฮอล์/ยาที่มีพาราเซตามอลซ้ำซ้อน ผู้ป่วยโรคตับควรปรึกษาแพทย์ก่อนใช้",
            "audio": "paracetamol.mp3"
        },
        "เกลือแร่ ORS": {
            "image": "เกลือแร่ ORS.jpg",
            "description": "ทดแทนสารน้ำและเกลือแร่จากอาการท้องเสีย/อาเจียน",
            "usage": "ละลายผง 1 ซองในน้ำสะอาดตามปริมาณที่ระบุ จิบบ่อย ๆ ทีละน้อยจนดีขึ้น",
            "doctor_advice": "ละลายน้ำแล้วค่อยๆจิบทีละน้อยจนหมด ห้ามดื่มทีเดียวหมด หากไม่ดีขึ้นใน 24 ชม. ให้พบแพทย์ทันที",
            "warning": "ห้ามผสมนม/น้ำอัดลม ไม่ควรชงเข้ม/จางเกินไป ผู้ป่วยไต/หัวใจควรปรึกษาแพทย์ก่อน",
            "audio": "ors.mp3"
        },
        "กาวิสคอน": {
            "image": "กาวิสคอน.jpg",
            "description": "บรรเทากรดไหลย้อน/แสบร้อนกลางอก",
            "usage": "รับประทานครั้งละ 10–20 มล. หลังอาหารและก่อนนอน",
            "doctor_advice": "เลี่ยงอาหารมัน เผ็ด เปรี้ยวจัด งดนอนทันทีหลังอาหาร 2–3 ชม. หากอาการยังไม่ดีขึ้นให้พบแพทย์ทันที",
            "warning": "หญิงตั้งครรภ์/ให้นม และผู้ป่วยไต ควรปรึกษาแพทย์ก่อนใช้ หากปวดท้องรุนแรง ถ่ายดำ อาเจียนเป็นเลือด ให้พบแพทย์ทันที",
            "audio": "gaviscon.mp3"
        }
    }
    return medicine_db.get(medicine, {
        "image": "default.png",
        "description": "ยาเพื่อบรรเทาอาการเบื้องต้น",
        "usage": "-",
        "doctor_advice": "-",
        "warning": "-",
        "audio": "default.mp3"
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
        confirm  = request.form['confirm_password']

        # ตรวจสอบช่องว่าง
        if not username or not password or not confirm:
            flash("กรุณากรอกข้อมูลให้ครบถ้วน", "error")
            return render_template('register.html')

        # ตรวจสอบว่ารหัสผ่านตรงกัน
        if password != confirm:
            flash("รหัสผ่านและการยืนยันรหัสผ่านไม่ตรงกัน", "error")
            return render_template('register.html')

        # เช็กซ้ำ username
        exist = supabase.table('users').select('user_id').eq('username', username).execute()
        if exist.data:
            flash("Username already exists", "error")
            return render_template('register.html')

        # บันทึก
        hashed = generate_password_hash(password)
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
    raw = request.form.get('symptom_type_id')
    if not raw:
        flash("กรุณาเลือกอาการ", "error")
        return redirect('/dashboard')

    symptom_type_id = int(raw)
    session['symptom_type_id'] = symptom_type_id
    user_id = session['user_id']

    # สร้าง case_logs entry
    res = supabase.table('case_logs').insert({
        'user_id': user_id,
        'symptom_type_id': symptom_type_id
    }).execute()

    if res.data:
        session['symptom_id'] = res.data[0]['symptom_id']

    # ดึงข้อมูลอาการ + ยาที่ผูกไว้
    q = supabase.table('symptom_types').select(
        'name, skip_severity, ask_has_fever, medicine_id'
    ).eq('symptom_type_id', symptom_type_id).execute()

    if not q.data:
        flash("ไม่พบอาการนี้ในระบบ", "error")
        return redirect('/dashboard')

    row = q.data[0]
    name = row.get('name', '')
    skip_severity = row.get('skip_severity', False)
    ask_has_fever = row.get('ask_has_fever', True)
    med_id = row.get('medicine_id')

    # ถ้ามีการ map ยาใน symptom_types ให้ query ชื่อยา
    med_name = None
    if med_id:
        m = supabase.table('medicines').select('medicine_name').eq('medicine_id', med_id).execute()
        if m.data:
            med_name = m.data[0]['medicine_name']

    # Logic
    if name == "อ่อนเพลียจากอาการท้องร่วง/ท้องเสีย":
        if med_id:
            update_current_symptom({"medicine_id": med_id})
            session['medicine'] = med_name
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
        q = supabase.table('symptom_types').select('name, medicine_id').eq('symptom_type_id', symptom_type_id).execute()
        row = q.data[0] if q.data else {}
        name = row.get('name', "")
        med_id = row.get('medicine_id')

        # ปวดกล้ามเนื้อ + ไข้
        if name == "ปวดกล้ามเนื้อ":
            if has_fever:
                update_current_symptom({"dispense_status": "cancel"})  # ไม่จ่ายยา
                session['medicine'] = None
                return render_template('advise_doctor.html', reason=(
                    "เนื่องจากคุณมีอาการปวดกล้ามเนื้อร่วมกับไข้ "
                    "ซึ่งอาจเป็นสัญญาณของไข้หวัดใหญ่หรือโรคที่ต้องดูแลโดยแพทย์ "
                    "ขอแนะนำให้ไปพบแพทย์เพื่อรับการวินิจฉัยและรักษาที่เหมาะสม"
                ))
            else:
                return redirect('/severity')

        elif name == "ปวดหัว":
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
            update_current_symptom({"dispense_status": "cancel", "severity_note": "แนะนำพบแพทย์ (ปวดกล้ามเนื้อ+ไข้)"})
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
    raw = request.form.get('severity')
    try:
        severity = int(raw)
    except (TypeError, ValueError):
        flash("กรุณาเลือกระดับความปวด", "error")
        return redirect('/severity')

    note = request.form.get('note')
    update_current_symptom({"severity": severity, "severity_note": note})
    session['severity'] = severity

    if severity >= 5:
        update_current_symptom({"dispense_status": "cancel", "severity_note": "แนะนำพบแพทย์ (severity >= 5)"})
        session['medicine'] = None
        return render_template('advise_doctor.html',
                               reason="อาการของคุณอยู่ในระดับค่อนข้างรุนแรง โปรดพบแพทย์หรือเภสัชใกล้บ้านท่าน")
    else:
        return redirect('/question_pregnant')


@app.route('/question_pregnant', methods=['GET', 'POST'])
@require_login
def question_pregnant():
    if request.method == 'POST':
        pregnant = request.form.get('pregnant') == 'yes'
        session['is_pregnant'] = pregnant

        symptom_type_id = session.get('symptom_type_id')
        q = supabase.table('symptom_types').select('name, medicine_id')\
             .eq('symptom_type_id', symptom_type_id).execute()
        row = q.data[0] if q.data else {}
        name = row.get("name", "")
        med_id = row.get("medicine_id")

        # ดึงชื่อยา
        med_name = None
        if med_id:
            m = supabase.table("medicines").select("medicine_name").eq("medicine_id", med_id).execute()
            if m.data:
                med_name = m.data[0]["medicine_name"]

        if pregnant:
            update_current_symptom({"is_pregnant": True, "dispense_status": "cancel",
                                    "severity_note": "แนะนำพบแพทย์ (ตั้งครรภ์)"})
            session['medicine'] = None
            return render_template('advise_doctor.html',
                                   reason="คุณอยู่ในระหว่างตั้งครรภ์ โปรดพบแพทย์หรือเภสัชใกล้บ้านท่านเพื่อรับการรักษาเฉพาะทาง")
        elif name == "กรดไหลย้อน" and med_id:
            update_current_symptom({"medicine_id": med_id})
            session['medicine'] = med_name
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
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": allergy,
                "dispense_status": "cancel",
                "severity_note": "แนะนำพบแพทย์ (แพ้พาราเซตามอล)"
            })
            session['medicine'] = None
            return render_template("advise_doctor.html",
                                   reason="เนื่องจากคุณแพ้ยาพาราเซตามอล โปรดพบแพทย์หรือเภสัชใกล้บ้านท่านเพื่อรับการรักษาเฉพาะทาง")
        else:
            # กรณีไม่แพ้ ให้เลือกยาตาม symptom_types
            q = supabase.table('symptom_types').select('medicine_id').eq('symptom_type_id', symptom_type_id).execute()
            med_id = q.data[0]['medicine_id'] if q.data else None

            med_name = None
            if med_id:
                m = supabase.table("medicines").select("medicine_name").eq("medicine_id", med_id).execute()
                if m.data:
                    med_name = m.data[0]["medicine_name"]

            if med_id:
                update_current_symptom({
                    "severity": severity,
                    "is_pregnant": is_pregnant,
                    "paracetamol_allergy": allergy,
                    "medicine_id": med_id
                })
                session['medicine'] = med_name
                return redirect('/recommend_medicine')
            else:
                update_current_symptom({
                    "severity": severity,
                    "is_pregnant": is_pregnant,
                    "paracetamol_allergy": allergy,
                    "dispense_status": "cancel",
                    "severity_note": "ไม่พบยาที่เหมาะสม"
                })
                session['medicine'] = None
                return render_template("advise_doctor.html", reason="ไม่พบยาที่เหมาะสม โปรดปรึกษาแพทย์")
    return render_template("allergy.html")

# ----------------------- Dispense / Finish -----------------------
@app.route('/recommend_medicine')
@require_login
def recommend_medicine():
    medicine = session.get('medicine')
    if not medicine:
        flash("ไม่สามารถแสดงผลการแนะนำยาได้", "error")
        return redirect('/dashboard')

    # map medicine_name -> medicine_id
    med = supabase.table("medicines").select("medicine_id")\
        .eq("medicine_name", medicine).execute().data
    if med:
        med_id = med[0]['medicine_id']
        session['medicine_id'] = med_id
        update_current_symptom({"medicine_id": med_id})

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
    medicine = session.get('medicine')
    med_id   = session.get('medicine_id')
    if not medicine or not med_id:
        flash("ไม่พบข้อมูลยา", "error")
        return redirect('/dashboard')

    try:
        # อัปเดตสถานะ success + บันทึกยา
        update_current_symptom({"dispense_status": "success", "medicine_id": med_id})

        # เรียก RPC ให้ stock -1
        supabase.rpc("decrement_stock", {"p_medicine_id": med_id, "p_qty": 1}).execute()

    except Exception as e:
        app.logger.error(f"Error updating stock for {medicine}: {e}")

    return render_template('dispense_success.html',
                           medicine=medicine,
                           redirect_ms=4000)

MAX_RETRY = 2
DEFAULT_MAX_WAIT_MS = 60000

@app.route('/dispense_loading', methods=['GET', 'POST'])
@require_login
def dispense_loading():
    # 1) บันทึกการยอมรับรับยา
    if request.method == 'POST':
        update_current_symptom({"accept_medicine": "รับยา"})
        session['dispense_attempts'] = session.get('dispense_attempts', 0)

    # 2) ตรวจข้อมูลยา + ช่อง
    medicine = session.get('medicine')
    if not medicine or medicine not in SLOT_BY_MEDICINE:
        flash("ไม่พบข้อมูลช่องสำหรับยาที่เลือก", "error")
        return redirect('/dashboard')

    slot = SLOT_BY_MEDICINE[medicine]
    info = get_medicine_info(medicine) if medicine else {"doctor_advice": "-"}
    doctor_advice = info.get("doctor_advice", "-")

    # 3) เตรียมไฟล์เสียงจาก static/sounds
    #    - ให้ get_medicine_info คืน key เช่น {"audio_file": "paracetamol.mp3"}
    #    - ถ้าไม่มีให้ fallback เป็น default.mp3 (คุณต้องมีไฟล์นี้ใน static/sounds/)
    audio_file = (info.get("audio") or "default.mp3").strip()
    audio_url  = url_for('static', filename=f'sounds/{audio_file}')

    # 4) สร้าง request_id กันสั่งซ้ำ (ใช้ symptom_id ปัจจุบัน)
    request_id = str(session.get('symptom_id', ''))

    # 5) Trigger เครื่องจ่ายยาแบบไม่บล็อก (อย่าทำให้หน้าเว็บค้าง)
    try:
        # ถ้า iot_client.iot_dispense() รองรับ query เพิ่มเติม (เช่น request_id) ให้ส่งเลย:
        # iot_dispense(slot, request_id=request_id)
        #
        # แต่ถ้ายังรับได้แค่ slot ให้ใช้ fallback call ด้านล่างแทน:
        from iot_client import get_dispenser_url
        import requests
        base = get_dispenser_url().rstrip('/')
        # /dispense?slot=<n>&request_id=<id>
        resp = requests.get(f"{base}/dispense", params={"slot": slot, "request_id": request_id}, timeout=4.0)
        resp.raise_for_status()
        # ไม่ต้องตรวจผล ณ ตอนนี้ ให้หน้า loading ไปโพล /iot/status เอง
    except Exception as e:
        app.logger.warning(f"IoT trigger error: {e} (slot={slot}, req={request_id})")
        # ไม่ทำให้หน้าโหลดล้ม — ให้หน้า loading โพล /iot/status ตามปกติ

    # 6) ส่งค่าที่หน้า loading ต้องใช้สำหรับโพล/นับถอยหลัง/เสียง
    MAX_WAIT_MS   = 60000   # 60 วินาที (ปรับได้)
    POLL_EVERY_MS = 800     # โพลทุก 0.8 วินาที

    return render_template(
        "loading_dispense.html",
        medicine=medicine,
        doctor_advice=doctor_advice,
        max_wait_ms=MAX_WAIT_MS,
        poll_every_ms=POLL_EVERY_MS,
        audio_url=audio_url,  
    )
  
@app.route('/decline_medicine', methods=['POST'])
@require_login
def decline_medicine():
    # ผู้ใช้ปฏิเสธการรับยา → บันทึกลงเคส แล้วค่อยเคลียร์ session บางส่วน
    update_current_symptom({"accept_medicine": "ไม่รับยา", "dispense_status": "cancel"})
    # เคลียร์ตัวเลือกยาหน้านี้ (เก็บ symptom_id ไว้จนกว่าจะถึง goodbye เพื่อความชัวร์)
    session.pop('medicine', None)
    session.pop('medicine_id', None)
    return redirect('/goodbye')


@app.route('/goodbye')
def goodbye():
    # ออกจาก flow แล้วค่อยล้าง session ทั้งหมด
    session.clear()
    return render_template("goodbye.html")


@app.route('/dispense_success_cb', methods=['POST'])
def dispense_success_cb():
    """
    webhook/callback จาก ESP32 (ถ้าใช้): แจ้งว่าสำเร็จ
    - อัปเดตที่ case_logs (schema ใหม่)
    - ถ้าไม่มี symptom_id ใน payload ให้ใช้เคสปัจจุบันใน session
    """
    data = request.get_json(silent=True) or {}
    try:
        if "symptom_id" in data:
            sid = int(data["symptom_id"])
            supabase.table('case_logs').update({"dispense_status": "success"}) \
                   .eq('symptom_id', sid).execute()
        else:
            update_current_symptom({"dispense_status": "success"})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.route('/dispense_failed')
@require_login
def dispense_failed():
    # โหมดล้มเหลว/หมดเวลา
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
    # ผู้ใช้กดยกเลิกกลางคัน
    update_current_symptom({"dispense_status": "cancel"})
    return redirect('/goodbye')


# ----------------------- Entrypoint -----------------------
@app.get("/dash")
@require_login
@require_admin
def dash_page():
    return render_template("dash.html")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

