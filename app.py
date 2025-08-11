from flask import Flask, render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os

from connDB import supabase
from iot_client import iot_dispense        # ✅ เอาเฉพาะ client ฟังก์ชันพอ
from iot_routes import iot_bp              # ✅ นำเข้า blueprint อย่างเดียว

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-super-secret-key-change-this-in-production')

# ✅ กันเผื่อถูก import ซ้ำ/รันซ้ำ
if 'iot' not in app.blueprints:
    app.register_blueprint(iot_bp)  

def require_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please login to continue", "error")
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

def log_user_action(user_id, action):
    # เก็บเฉพาะ login/logout ตามที่ตกลง
    if action in ("login", "logout"):
        supabase.table('user_logs').insert({'user_id': user_id, 'action': action}).execute()

def update_current_symptom(update_dict):
    symptom_id = session.get('symptom_id')
    if symptom_id:
        supabase.table('symptoms').update(update_dict).eq('id', symptom_id).execute()

# ==============================
# Static medicine meta (รูป/วิธีใช้/คำเตือน/คำแนะนำ)
# ==============================
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
            "doctor_advice": "เลี่ยงอาหารมัน เผ็ด เปรี้ยวจัด งดนอนทันทีหลังอาหาร 2–3 ชม.",
            "warning": "หญิงตั้งครรภ์/ให้นม และผู้ป่วยไต ควรปรึกษาแพทย์ก่อนใช้ หากปวดท้องรุนแรง ถ่ายดำ อาเจียนเป็นเลือด ให้พบแพทย์ทันที"
        }
    }
    # fallback
    return medicine_db.get(medicine, {
        "image": "default.png",
        "description": "ยาเพื่อบรรเทาอาการเบื้องต้น",
        "usage": "-",
        "doctor_advice": "-",
        "warning": "-"
    })

# map ชื่อยา -> ช่องจ่าย (ปรับให้ตรงกับฮาร์ดแวร์)
SLOT_BY_MEDICINE = {
    "พาราเซตามอล 500mg": 1,
    "เกลือแร่ ORS": 2,
    "กาวิสคอน": 3
}

# ==============================
# Routes: Auth + Dashboard
# ==============================
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

        hashed_password = generate_password_hash(password)
        exist = supabase.table('users').select('id').eq('username', username).execute()
        if exist.data:
            flash("Username already exists", "error")
            return render_template('register.html')

        res = supabase.table('users').insert({'username': username, 'password': hashed_password}).execute()
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
                session['user_id'] = user['id']
                log_user_action(user['id'], "login")
                flash("Login successful!", "success")
                return redirect('/dashboard')
        flash("Incorrect username or password", "error")
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_user_action(session['user_id'], "logout")
    session.clear()
    flash("Logout successful!", "success")
    return redirect('/login')

@app.route('/dashboard')
@require_login
def dashboard():
    result = supabase.table('symptom_types').select('*').execute()
    symptoms = result.data if result.data else []
    return render_template('first.html', symptoms=symptoms)

# ==============================
# Routes: Flow คัดกรองอาการ
# ==============================
@app.route('/select_symptom', methods=['POST'])
@require_login
def select_symptom():
    symptom_type_id = int(request.form.get('symptom_id'))
    session['symptom_type_id'] = symptom_type_id
    user_id = session['user_id']

    # insert symptom ใหม่ และเก็บ id ไว้ใน session
    res = supabase.table('symptoms').insert({
        'user_id': user_id,
        'symptom_type_id': symptom_type_id
    }).execute()
    if res.data and len(res.data) > 0:
        session['symptom_id'] = res.data[0]['id']

    # ดึงกติกา flow
    q = supabase.table('symptom_types').select(
        'name,skip_severity,ask_has_fever,suggested_medicine'
    ).eq('id', symptom_type_id).execute()
    if not q.data:
        flash("ไม่พบอาการนี้ในระบบ", "error")
        return redirect('/dashboard')

    row = q.data[0]
    name = row.get('name', '')
    skip_severity = row.get('skip_severity', False)
    ask_has_fever = row.get('ask_has_fever', True)
    med = row.get('suggested_medicine', '')

    # อาการที่ “จ่ายได้เลย” (ไม่ถามตั้งครรภ์/แพ้พารา ฯลฯ)
    if name == "อ่อนเพลียจากอาการท้องร่วง/ท้องเสีย":
        update_current_symptom({"severity_note": f"แนะนำยา: {med}"})
        session['medicine'] = med
        return redirect('/recommend_medicine')

    # อาการ “มีไข้” → ข้ามถามไข้ ไปถามปวดกล้ามเนื้อทันที
    elif name == "มีไข้":
        session['has_fever'] = True
        update_current_symptom({"has_fever": True})
        return redirect('/question_fever')

    # อาการที่ skip severity → ไปถามตั้งครรภ์
    elif skip_severity:
        return redirect('/question_pregnant')

    # อาการที่ต้องถามไข้ก่อน
    elif ask_has_fever:
        return redirect('/question_has_fever')

    # ที่เหลือ → ไปให้คะแนนความรุนแรง
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
        q = supabase.table('symptom_types').select('name').eq('id', symptom_type_id).execute()
        name = q.data[0]['name'] if q.data else ""

        # กรณี “ปวดกล้ามเนื้อ” ถ้ามีไข้ → จบที่แนะนำพบแพทย์
        if name == "ปวดกล้ามเนื้อ":
            if has_fever:
                update_current_symptom({"severity_note": "แนะนำพบแพทย์ (ปวดกล้ามเนื้อ+ไข้)"})
                session['medicine'] = None
                return render_template(
                    'advise_doctor.html',
                    reason=("เนื่องจากคุณมีอาการปวดกล้ามเนื้อร่วมกับไข้ "
                            "ซึ่งอาจเป็นสัญญาณของไข้หวัดใหญ่หรือโรคที่ต้องดูแลโดยแพทย์ "
                            "ขอแนะนำให้ไปพบแพทย์เพื่อรับการวินิจฉัยและรักษาที่เหมาะสม")
                )
            else:
                return redirect('/severity')

        # ปวดหัว ถ้ามีไข้ → ถามปวดกล้ามเนื้อ, ไม่มีก็ไปให้คะแนน
        elif name in ["ปวดหัว"]:
            if has_fever:
                return redirect('/question_fever')
            else:
                return redirect('/severity')

        # อื่น ๆ
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
            return render_template(
                'advise_doctor.html',
                reason=("เนื่องจากคุณมีอาการปวดกล้ามเนื้อร่วมกับไข้ "
                        "ซึ่งอาจเป็นสัญญาณของไข้หวัดใหญ่หรือโรคที่ต้องดูแลโดยแพทย์ "
                        "ขอแนะนำให้ไปพบแพทย์เพื่อรับการวินิจฉัยและรักษาที่เหมาะสม")
            )
        else:
            # ถ้าอาการเดิมคือ “มีไข้” (id=8 ในข้อมูลเดิมของคุณ) → ไปถามตั้งครรภ์
            if symptom_type_id == 8:
                return redirect('/question_pregnant')
            # อื่น ๆ → ไปให้คะแนน
            else:
                return redirect('/severity')

    return render_template('fever_muscle.html')

@app.route('/severity')
@require_login
def severity():
    return render_template('severity.html')

@app.route('/submit_severity', methods=['POST'])
@require_login
def submit_severity():
    severity = int(request.form.get('severity'))
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
        q = supabase.table('symptom_types').select('name', 'suggested_medicine').eq('id', symptom_type_id).execute()
        name = q.data[0]['name'] if q.data else ""
        med = q.data[0]['suggested_medicine'] if q.data else ""

        if pregnant:
            update_current_symptom({"severity_note": "อยู่ระหว่างตั้งครรภ์", "is_pregnant": True})
            session['medicine'] = None
            return render_template('advise_doctor.html', reason="คุณอยู่ในระหว่างตั้งครรภ์ โปรดพบแพทย์หรือเภสัชใกล้บ้านท่านเพื่อรับการรักษาเฉพาะทาง")

        # กรดไหลย้อน (skip ask_allergy)
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
            q = supabase.table('symptom_types').select('suggested_medicine').eq('id', symptom_type_id).execute()
            if q.data:
                medicine = q.data[0]['suggested_medicine']
            else:
                medicine = "พาราเซตามอล 500mg"
            update_current_symptom({
                "severity": severity,
                "severity_note": f"แนะนำยา: {medicine}",
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": allergy
            })
            session['medicine'] = medicine
            return redirect('/recommend_medicine')
    return render_template("allergy.html")

# ==============================
# Routes: สรุปยา / จ่ายยา / จบการใช้งาน
# ==============================
@app.route('/recommend_medicine')
@require_login
def recommend_medicine():
    medicine = session.get('medicine')
    if not medicine:
        flash("ไม่สามารถแสดงผลการแนะนำยาได้", "error")
        return redirect('/dashboard')

    info = get_medicine_info(medicine)
    return render_template(
        "recommend_result.html",
        medicine=medicine,
        image_name=info.get("image"),
        description=info.get("description"),
        usage=info.get("usage"),
        warning=info.get("warning")
    )

@app.route('/dispense_loading', methods=['POST'])
@require_login
def dispense_loading():
    update_current_symptom({"accept_medicine": "รับยา"})
    medicine = session.get('medicine')
    if not medicine:
        flash("ไม่พบรายการยา", "error")
        return redirect('/dashboard')

    slot = SLOT_BY_MEDICINE.get(medicine)
    if not slot:
        flash("ไม่พบช่องจ่ายที่ตรงกับยา", "error")
        return redirect('/dashboard')

    # ส่งคำสั่งไป ESP32
    try:
        resp = iot_dispense(slot)
        if not resp.get("ok"):
            flash(f"สั่งจ่ายยาไม่สำเร็จ: {resp}", "error")
            return redirect('/dashboard')
    except Exception as e:
        flash(f"เชื่อมต่อเครื่องจ่ายยาไม่ได้: {e}", "error")
        return redirect('/dashboard')

    # บอกหน้าโหลดดิ้งว่า 'ต้องรอเซ็นเซอร์ยืนยัน'
    sensor_required = os.environ.get("USE_ULTRASONIC", "1") == "1"

    info = get_medicine_info(medicine)
    return render_template(
        "loading_dispense.html",
        medicine=medicine,
        doctor_advice=info.get("doctor_advice", "-"),
        sensor_required=sensor_required
    )

# ปุ่ม “ไม่รับยา”
@app.route('/decline_medicine', methods=['POST'])
@require_login
def decline_medicine():
    update_current_symptom({"accept_medicine": "ไม่รับยา"})
    # เคลียร์บริบทเคสนี้ (ถ้าต้องการ)
    session.pop('medicine', None)
    session.pop('symptom_id', None)
    return redirect('/goodbye')

# จบการใช้งาน (อย่าแก้สถานะรับยาที่นี่)
@app.route('/goodbye')
def goodbye():
    session.clear()
    return render_template("goodbye.html")

# ==============================
# Entrypoint
# ==============================
if __name__ == '__main__':
    app.run(debug=True)
