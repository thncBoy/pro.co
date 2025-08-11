from flask import Flask, render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import requests
import time
from connDB import supabase

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-super-secret-key-change-this-in-production')


DISPENSER_URL = os.environ.get("DISPENSER_URL", "http://172.20.10.4")  # <-- IP ESP32
DISPENSE_TIMEOUT = 60  # วินาที สูงสุดที่รอการจ่ายยาเสร็จ

def require_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please login to continue", "error")
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

def log_user_action(user_id, action):
    if action in ("login", "logout"):
        supabase.table('user_logs').insert({'user_id': user_id, 'action': action}).execute()

def update_current_symptom(update_dict):
    symptom_id = session.get('symptom_id')
    if symptom_id:
        supabase.table('symptoms').update(update_dict).eq('id', symptom_id).execute()

@app.route('/')
def home():
    return redirect('/login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password)
        exist = supabase.table('users').select('id').eq('username', username).execute()
        if exist.data:
            flash("Username already exists", "error")
            return render_template('register.html')
        res = supabase.table('users').insert({'username': username, 'password': hashed_password}).execute()
        if res.data:
            flash("Registration successful! Please login.", "success")
            return redirect('/login')
        else:
            flash("Error: Cannot register user", "error")
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
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

@app.route('/select_symptom', methods=['POST'])
@require_login
def select_symptom():
    symptom_type_id = int(request.form.get('symptom_id'))
    session['symptom_type_id'] = symptom_type_id
    user_id = session['user_id']

    res = supabase.table('symptoms').insert({'user_id': user_id, 'symptom_type_id': symptom_type_id}).execute()
    if res.data and len(res.data) > 0:
        session['symptom_id'] = res.data[0]['id']

    # ดึงข้อมูลอาการ
    q = supabase.table('symptom_types').select('name,skip_severity,ask_has_fever,suggested_medicine').eq('id', symptom_type_id).execute()
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
        return redirect('/severity')  # ข้ามการถามไข้

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

        if name == "ปวดกล้ามเนื้อ":
            if has_fever:
                update_current_symptom({"severity_note": "แนะนำพบแพทย์ (ปวดกล้ามเนื้อ+ไข้)"})
                session['medicine'] = None  # ไม่แนะนำยา
                return render_template('advise_doctor.html', reason="เนื่องจากคุณมีอาการปวดกล้ามเนื้อร่วมกับไข้ ซึ่งอาจเป็นสัญญาณของไข้หวัดใหญ่หรือโรคที่ต้องดูแลโดยแพทย์ ขอแนะนำให้ไปพบแพทย์เพื่อรับการวินิจฉัยและรักษาที่เหมาะสม")
            else:
                return redirect('/severity')
        elif name in ["ปวดหัว"]:
            if has_fever:
                return redirect('/question_fever')
                #if
            else:
                return redirect('/severity')
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
            session['medicine'] = None  # ไม่แนะนำยา
            return render_template('advise_doctor.html', reason="เนื่องจากคุณมีอาการปวดกล้ามเนื้อร่วมกับไข้ ซึ่งอาจเป็นสัญญาณของไข้หวัดใหญ่หรือโรคที่ต้องดูแลโดยแพทย์ ขอแนะนำให้ไปพบแพทย์เพื่อรับการวินิจฉัยและรักษาที่เหมาะสม")
        else: 
            if symptom_type_id == 8 : 
                return redirect('/question_pregnant')
            else : 
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
        session['medicine'] = None  # ไม่แนะนำยา
        return render_template('advise_doctor.html', reason="อาการของคุณอยู่ในระดับค่อนข้างรุนแรง โปรดพบแพทย์หรือเภสัชใกล้บ้านท่าน")
    else:
        return redirect('/question_pregnant')

@app.route('/question_pregnant', methods=['GET', 'POST'])
@require_login
def question_pregnant():
    if request.method == 'POST':
        pregnant = request.form.get('pregnant') == 'yes'
        session['is_pregnant'] = pregnant
        severity = session.get('severity')
        symptom_type_id = session.get('symptom_type_id')
        q = supabase.table('symptom_types').select('name', 'suggested_medicine').eq('id', symptom_type_id).execute()
        name = q.data[0]['name'] if q.data else ""
        med = q.data[0]['suggested_medicine'] if q.data else ""
        if pregnant:
            update_current_symptom({"severity_note": "อยู่ระหว่างตั้งครรภ์", "is_pregnant": True})
            session['medicine'] = None  # ไม่แนะนำยา
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
            session['medicine'] = None  # ไม่แนะนำยา
            return render_template("advise_doctor.html", reason="เนื่องจากคุณแพ้ยาพาราเซตามอล โปรดพบแพทย์หรือเภสัชใกล้บ้านท่านเพื่อรับการรักษาเฉพาะทาง")
        else:
            q = supabase.table('symptom_types').select('suggested_medicine').eq('id', symptom_type_id).execute()
            if q.data:
                medicine = q.data[0]['suggested_medicine']
            else:
                medicine = "ไทลินอล 500mg"
            update_current_symptom({
                "severity": severity,
                "severity_note": f"แนะนำยา: {medicine}",
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": allergy
            })
            session['medicine'] = medicine
            return redirect('/recommend_medicine')
    return render_template("allergy.html")

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

SLOT_BY_MEDICINE = {
    "พาราเซตามอล 500mg": 1,   # ช่อง 1 
    "เกลือแร่ ORS": 2,           # ช่อง 2 
    "กาวิสคอน": 3              # ช่อง 3 
}

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
    # 1) บันทึกการกดรับยา
    update_current_symptom({"accept_medicine":"รับยา"})

    # 2) ดึงชื่อยาและ map ไป slot
    medicine = session.get('medicine')
    if not medicine or medicine not in SLOT_BY_MEDICINE:
        flash("ไม่พบข้อมูลช่องสำหรับยาที่เลือก", "error")
        return redirect('/dashboard')

    slot = SLOT_BY_MEDICINE[medicine]

    # 3) ดึงข้อความคำแนะนำแพทย์เพื่อแสดงระหว่างรอ
    info = get_medicine_info(medicine) if medicine else {"doctor_advice": "-"}
    doctor_advice = info.get("doctor_advice", "-")

    # 4) สั่งให้ ESP32 จ่ายยา
    try:
        resp = iot_dispense(slot)
        if not resp.get("ok", False):
            flash(f"สั่งจ่ายยาไม่สำเร็จ: {resp}", "error")
            return redirect('/dashboard')
    except Exception as e:
        flash(f"เชื่อมต่อเครื่องจ่ายยาไม่ได้: {e}", "error")
        return redirect('/dashboard')

    # 5) แสดงหน้า loading (มีคำแนะนำแพทย์)
    #    — และใช้ JS ในหน้า loading คอย polling สถานะเพื่อไปหน้าถัดไป
    return render_template("loading_dispense.html",
    medicine=medicine,
    doctor_advice=doctor_advice)

@app.route('/goodbye')
def goodbye():
    update_current_symptom({"accept_medicine" : "ไม่รับยา"})
    session.clear()
    return render_template("goodbye.html") 


# โค้ดฟังก์ชั่นฝั่งควบคุมบอร์ด

def iot_dispense(slot: int):
    """สั่ง ESP32 ให้จ่ายยา slot ที่ระบุ"""
    r = requests.get(f"{DISPENSER_URL}/dispense", params={"slot": slot}, timeout=5)
    r.raise_for_status()
    return r.json()

def iot_get_status():
    """ถามสถานะจาก ESP32 ว่ากำลังยุ่งอยู่ไหม"""
    r = requests.get(f"{DISPENSER_URL}/status", timeout=3)
    r.raise_for_status()
    return r.json()

def wait_until_done(max_seconds=DISPENSE_TIMEOUT, interval=0.5):
    """รอจน ESP32 จ่ายยาเสร็จ (busy=false) หรือครบเวลาที่กำหนด"""
    t0 = time.time()
    while time.time() - t0 < max_seconds:
        try:
            st = iot_get_status()
            if not st.get("busy", False):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False

if __name__ == '__main__':
    app.run(debug=True)
