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

# **ปรับตรงนี้: update symptoms ด้วย symptom_id จาก session**
def update_current_symptom(update_dict):
    symptom_id = session.get('symptom_id')
    if symptom_id:
        supabase.table('symptoms').update(update_dict).eq('id', symptom_id).execute()
    else:
        print("No symptom_id in session, cannot update symptom efficiently")

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

    # Insert symptoms แล้วเก็บ symptom_id ไว้ใน session
    res = supabase.table('symptoms').insert({'user_id': user_id, 'symptom_type_id': symptom_type_id}).execute()
    if res.data and len(res.data) > 0:
        session['symptom_id'] = res.data[0]['id']

    # ดึงรายละเอียดอาการจากฐานข้อมูล
    q = supabase.table('symptom_types').select('name,skip_severity').eq('id', symptom_type_id).execute()
    if not q.data:
        flash("ไม่พบอาการนี้ในระบบ", "error")
        return redirect('/dashboard')

    row = q.data[0]
    name = row.get('name', '')
    skip_severity = row.get('skip_severity', False)

    # === FLOW SPLIT ===
    if name in ["ปวดกล้ามเนื้อ", "ปวดหัว", "ไข้ขึ้น"]:
        # ถามไข้ก่อน
        return redirect('/question_has_fever')
    elif skip_severity:
        # กรดไหลย้อน, อ่อนเพลีย, ปวดตึง
        return redirect('/question_pregnant')
    else:
        # อื่น ๆ (ปวดท้อง, ประจำเดือน, ฟันผุ/ปวดฟัน)
        return redirect('/severity')
    
@app.route('/question_has_fever', methods=['GET', 'POST'])
@require_login
def question_has_fever():
    if request.method == 'POST':
        has_fever = request.form.get('has_fever') == 'yes'
        session['has_fever'] = has_fever
        update_current_symptom({"has_fever": has_fever})

        # เช็คชื่ออาการ
        symptom_type_id = session.get('symptom_type_id')
        q = supabase.table('symptom_types').select('name').eq('id', symptom_type_id).execute()
        name = q.data[0]['name'] if q.data else ""

        if name == "ปวดกล้ามเนื้อ":
            # ไม่ถามปวดกล้ามเนื้อซ้ำ ไปถาม severity ต่อเลย
            return redirect('/severity')
        elif name in ["ปวดหัว", "ไข้ขึ้น"]:
            if has_fever:
                # ถามปวดกล้ามเนื้อ
                return redirect('/question_fever')
            else:
                return redirect('/severity')
        else:
            return redirect('/severity')
    return render_template('question_has_fever.html')

@app.route('/question_fever', methods=['GET', 'POST'])
@require_login
def question_fever():
    if request.method == 'POST':
        muscle_pain = request.form.get('muscle_pain') == 'yes'
        session['muscle_pain'] = muscle_pain
        update_current_symptom({"muscle_pain": muscle_pain})
        if muscle_pain:
            # แนะนำพบแพทย์ (case อาการหนัก)
            update_current_symptom({"severity_note": "แนะนำพบแพทย์ (ปวดกล้ามเนื้อ+ไข้)"})
            return render_template('advise_doctor.html', reason="คุณมีอาการปวดเมื่อยกล้ามเนื้อร่วมกับไข้ อาจเสี่ยงเป็นไข้หวัดใหญ่ โปรดพบแพทย์")
        else:
            return redirect('/question_pregnant')
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
        return render_template('advise_doctor.html', reason="อาการรุนแรง")
    else:
        return redirect('/question_pregnant')

@app.route('/question_pregnant', methods=['GET', 'POST'])
@require_login
def question_pregnant():
    if request.method == 'POST':
        pregnant = request.form.get('pregnant') == 'yes'
        session['is_pregnant'] = pregnant
        severity = session.get('severity')
        allergy = session.get('paracetamol_allergy', False)
        if pregnant:
            update_current_symptom({
                "severity": severity,
                "severity_note": "แนะนำพบแพทย์ (ตั้งครรภ์)",
                "is_pregnant": pregnant,
                "paracetamol_allergy": allergy
            })
            return render_template('advise_doctor.html', reason="อยู่ระหว่างตั้งครรภ์")
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
            return render_template("advise_doctor.html", reason="แพ้ยาพาราเซตามอล")
        else:
            # แนะนำยาตามฐานข้อมูล
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
            return render_template("recommend_result.html", medicine=medicine)
    return render_template("allergy.html")

def update_current_symptom(update_dict):
    """
    update_dict: dict ของ field ที่ต้องการอัปเดต
    ใช้ symptom_id ที่เก็บใน session เพื่อ update row ในตาราง symptoms
    """
    symptom_id = session.get('symptom_id')
    if symptom_id:
        supabase.table('symptoms').update(update_dict).eq('id', symptom_id).execute()
    else:
        print("No symptom_id in session, cannot update symptom efficiently")


@app.route('/recommend_medicine')
@require_login
def recommend_medicine():
    symptom_type_id = session.get('symptom_type_id')
    severity = session.get('severity', 0)
    is_pregnant = session.get('is_pregnant', False)
    allergy = session.get('paracetamol_allergy', False)
    has_fever = session.get('has_fever', False)
    extra = ""
    q = supabase.table('symptom_types').select('suggested_medicine').eq('id', symptom_type_id).execute()
    if allergy or is_pregnant:
        medicine = "แนะนำพบแพทย์"
        update_current_symptom({
            "severity": severity,
            "severity_note": "แนะนำพบแพทย์ (แพ้ยา/ตั้งครรภ์)",
            "is_pregnant": is_pregnant,
            "paracetamol_allergy": allergy,
            "has_fever": has_fever
        })
    elif q.data:
        medicine = q.data[0]['suggested_medicine']
        update_current_symptom({
            "severity": severity,
            "severity_note": medicine,
            "is_pregnant": is_pregnant,
            "paracetamol_allergy": allergy,
            "has_fever": has_fever
        })
    else:
        medicine = "พาราเซตามอล 500mg"
        update_current_symptom({
            "severity": severity,
            "severity_note": medicine,
            "is_pregnant": is_pregnant,
            "paracetamol_allergy": allergy,
            "has_fever": has_fever
        })
    if allergy:
        extra = "คุณแพ้ยาพาราเซตามอล ควรพบแพทย์เพื่อรับคำแนะนำเพิ่มเติม"
    elif is_pregnant:
        extra = "คุณอยู่ระหว่างตั้งครรภ์ โปรดปรึกษาแพทย์ก่อนใช้ยา"
    return render_template('recommend_result.html', medicine=medicine, extra=extra)

if __name__ == '__main__':
    app.run(debug=True)
