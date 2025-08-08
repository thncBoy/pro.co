from flask import Flask, render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from connDB import supabase

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-super-secret-key-change-this-in-production')

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

    if name == "อ่อนเพลีย":
        update_current_symptom({"severity_note": f"แนะนำยา: {med}"})
        session['medicine'] = med
        return redirect('/recommend_medicine')
    elif name == "ไข้ขึ้น":
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
            "description": "ไทลินอล 500 มก. (Tylenol) เป็นยาพาราเซตามอลสำหรับลดไข้และบรรเทาอาการปวดทั่วไป เช่น ปวดหัว ปวดกล้ามเนื้อ ปวดฟัน",
            "advice": "รับประทานครั้งละ 1-2 เม็ด ทุก 4-6 ชั่วโมงเมื่อมีอาการ ไม่ควรเกิน 8 เม็ดต่อวัน ห้ามใช้ในผู้ที่แพ้ยาพาราเซตามอล"
        },
        "เกลือแร่ ORS": {
            "image": "เกลือแร่ ORS.jpg",
            "description": "ORS หรือผงเกลือแร่ ใช้สำหรับชงดื่มเพื่อทดแทนการสูญเสียน้ำและเกลือแร่ในร่างกาย จากการท้องเสียหรืออาเจียน",
            "advice": "ละลายผง 1 ซองในน้ำสะอาด 1 แก้ว ดื่มทีละน้อยตลอดวันจนกว่าอาการจะดีขึ้น"
        },
        "กาวิสคอน": {
            "image": "กาวิสคอน.jpg",
            "description": "กาวิสคอน (Gaviscon) ใช้บรรเทาอาการกรดไหลย้อน แสบร้อนกลางอก ลดกรดในกระเพาะอาหาร",
            "advice": "รับประทานครั้งละ 10-20 มล. หลังอาหารและก่อนนอน"
        }
    }
    return medicine_db.get(medicine, {
        "image": "default.png",
        "description": "ยาเพื่อบรรเทาอาการเบื้องต้น",
        "advice": ""
    })

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
        image_name=info["image"],
        description=info["description"],
        advice=info["advice"]
    )

@app.route('/dispense_loading', methods=['POST'])
@require_login
def dispense_loading():
    update_current_symptom({"accept_medicine" : "รับยา"})
    # ในอนาคตที่นี่จะสั่งจ่ายยาและรอ ESP8266 ส่งสถานะกลับ
    return render_template("loading_dispense.html")  # สร้างหน้า loading_dispense.html ไว้แสดงสถานะกำลังจ่ายยา

@app.route('/goodbye')
def goodbye():
    update_current_symptom({"accept_medicine" : "ไม่รับยา"})
    session.clear()
    return render_template("goodbye.html")  # สร้างหน้า goodbye.html แสดงข้อความขอบคุณและแนะนำให้ออกจากระบบ

if __name__ == '__main__':
    app.run(debug=True)
