from flask import Flask, render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from connDB import supabase

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-super-secret-key-change-this-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = 3600
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True

def require_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please login to continue", "error")
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

def log_user_action(user_id, action):
    # เก็บเฉพาะ login/logout
    if action in ("login", "logout"):
        supabase.table('user_logs').insert({'user_id': user_id, 'action': action}).execute()

def update_latest_symptom(user_id, update_dict):
    q = supabase.table('symptoms').select('id').eq('user_id', user_id).order('id', desc=True).limit(1).execute()
    if q.data:
        symptom_id = q.data[0]['id']
        supabase.table('symptoms').update(update_dict).eq('id', symptom_id).execute()

@app.route('/test_connect')
def test_connect():
    result = supabase.table('users').select('*').limit(5).execute()
    return str(result.data)

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
    supabase.table('symptoms').insert({'user_id': user_id, 'symptom_type_id': symptom_type_id}).execute()
    q = supabase.table('symptom_types').select('name,ask_has_fever,has_fever_logic,skip_severity').eq('id', symptom_type_id).execute()
    if not q.data:
        flash("ไม่พบอาการนี้ในระบบ", "error")
        return redirect('/dashboard')
    row = q.data[0]
    ask_has_fever = row.get('ask_has_fever', False)
    has_fever_logic = row.get('has_fever_logic', False)
    skip_severity = row.get('skip_severity', False)
    if ask_has_fever:
        return redirect('/question_has_fever')
    elif has_fever_logic:
        return redirect('/question_fever')
    elif skip_severity:
        return redirect('/question_pregnant')
    else:
        return redirect('/severity')

@app.route('/question_has_fever', methods=['GET', 'POST'])
@require_login
def question_has_fever():
    if request.method == 'POST':
        has_fever = request.form.get('has_fever') == 'yes'
        session['has_fever'] = has_fever
        update_latest_symptom(session['user_id'], {"has_fever": has_fever})
        if has_fever:
            return redirect('/question_fever')
        else:
            return redirect('/severity')
    return render_template('question_has_fever.html')

@app.route('/question_fever', methods=['GET', 'POST'])
@require_login
def question_fever():
    if request.method == 'POST':
        muscle_pain = request.form.get('muscle_pain') == 'yes'
        session['muscle_pain'] = muscle_pain
        update_latest_symptom(session['user_id'], {"has_fever": muscle_pain})
        if muscle_pain:
            # **เพิ่มการบันทึก "แนะนำพบแพทย์"**
            update_latest_symptom(session['user_id'], {
                "severity_note": "แนะนำพบแพทย์ (ปวดเมื่อยกล้ามเนื้อ+ไข้)"
            })
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
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')
    update_latest_symptom(user_id, {"severity": severity, "severity_note": note})
    session['severity'] = severity
    return redirect('/check_condition')

@app.route('/check_condition', methods=['GET', 'POST'])
@require_login
def check_condition():
    if request.method == 'POST':
        severity = int(request.form.get('severity'))
        session['severity'] = severity
    else:
        severity = session.get('severity')
    if severity is None:
        return redirect('/severity')
    if severity >= 5:
        # **บันทึก "แนะนำพบแพทย์"**
        update_latest_symptom(session['user_id'], {
            "severity": severity,
            "severity_note": "แนะนำพบแพทย์ (severity >= 5)"
        })
        return render_template('advise_doctor.html', reason="อาการรุนแรง")
    else:
        return redirect('/question_pregnant')

@app.route('/question_pregnant', methods=['GET', 'POST'])
@require_login
def question_pregnant():
    if request.method == 'POST':
        pregnant = request.form.get('pregnant')
        is_pregnant = pregnant == 'yes'
        session['is_pregnant'] = is_pregnant
        user_id = session.get('user_id')
        severity = session.get('severity')
        allergy = session.get('paracetamol_allergy', False)
        if severity is None:
            return redirect('/severity')
        if is_pregnant:
            reason = "อยู่ระหว่างตั้งครรภ์"
            # **บันทึก "แนะนำพบแพทย์"**
            update_latest_symptom(user_id, {
                "severity": severity,
                "severity_note": f"แนะนำพบแพทย์ ({reason})",
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": allergy
            })
            log_user_action(user_id, "ตั้งครรภ์: ใช่")
            return render_template('advise_doctor.html', reason=reason)
        else:
            log_user_action(user_id, "ตั้งครรภ์: ไม่ใช่")
            return redirect('/question_allergy')
    return render_template('pregnant.html')

@app.route('/question_allergy', methods=['GET', 'POST'])
@require_login
def question_allergy():
    if request.method == 'POST':
        allergy = request.form.get('allergy')
        is_allergy = allergy == 'yes'
        session['paracetamol_allergy'] = is_allergy
        user_id = session.get('user_id')
        severity = session.get('severity')
        is_pregnant = session.get('is_pregnant', False)
        symptom_type_id = session.get('symptom_type_id')
        if is_allergy:
            reason = "แพ้ยาพาราเซตามอล"
            # **บันทึก "แนะนำพบแพทย์"**
            update_latest_symptom(user_id, {
                "severity": severity,
                "severity_note": f"แนะนำพบแพทย์ ({reason})",
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": is_allergy
            })
            return render_template("advise_doctor.html", reason=reason)
        elif is_pregnant:
            reason = "อยู่ระหว่างตั้งครรภ์"
            # **บันทึก "แนะนำพบแพทย์"**
            update_latest_symptom(user_id, {
                "severity": severity,
                "severity_note": f"แนะนำพบแพทย์ ({reason})",
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": is_allergy
            })
            return render_template("advise_doctor.html", reason=reason)
        else:
            q = supabase.table('symptom_types').select('suggested_medicine').eq('id', symptom_type_id).execute()
            if q.data:
                medicine = q.data[0]['suggested_medicine']
            else:
                medicine = "พาราเซตามอล 500mg"
            severity_note = f"แนะนำยา: {medicine}"
            update_latest_symptom(user_id, {
                "severity": severity,
                "severity_note": severity_note,
                "is_pregnant": is_pregnant,
                "paracetamol_allergy": is_allergy
            })
            return render_template("recommend_result.html", medicine=medicine)
    return render_template("allergy.html")

@app.route('/recommend_medicine')
@require_login
def recommend_medicine():
    user_id = session.get('user_id')
    symptom_type_id = session.get('symptom_type_id')
    severity = session.get('severity', 0)
    is_pregnant = session.get('is_pregnant', False)
    allergy = session.get('paracetamol_allergy', False)
    has_fever = session.get('has_fever', False)
    extra = ""
    q = supabase.table('symptom_types').select('suggested_medicine').eq('id', symptom_type_id).execute()
    if allergy or is_pregnant:
        medicine = "แนะนำพบแพทย์"
        # **บันทึก "แนะนำพบแพทย์"**
        update_latest_symptom(user_id, {
            "severity": severity,
            "severity_note": f"แนะนำพบแพทย์ (แพ้ยา/ตั้งครรภ์)",
            "is_pregnant": is_pregnant,
            "paracetamol_allergy": allergy,
            "has_fever": has_fever
        })
    elif q.data:
        medicine = q.data[0]['suggested_medicine']
        update_latest_symptom(user_id, {
            "severity": severity,
            "severity_note": medicine,
            "is_pregnant": is_pregnant,
            "paracetamol_allergy": allergy,
            "has_fever": has_fever
        })
    else:
        medicine = "พาราเซตามอล 500mg"
        update_latest_symptom(user_id, {
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
