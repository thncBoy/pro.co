from flask import Flask, render_template, request, redirect, session, flash
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-super-secret-key-change-this-in-production')

app.config['PERMANENT_SESSION_LIFETIME'] = 3600
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True

def get_db():
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='pro7'
        )
        return conn
    except mysql.connector.Error as err:
        flash(f"Error connecting to database: {err}", "error")
        return None

def require_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please login to continue", "error")
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def home():
    return redirect('/login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        if conn is None:
            return render_template('register.html')
        cursor = conn.cursor()
        try:
            hashed_password = generate_password_hash(password)
            cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_password))
            conn.commit()
            flash("Registration successful! Please login.", "success")
            return redirect('/login')
        except mysql.connector.errors.IntegrityError:
            flash("Username already exists", "error")
        except mysql.connector.Error as err:
            flash(f"Database error: {err}", "error")
        finally:
            cursor.close()
            conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db()
        if conn is None:
            flash("Database connection error.", "error")
            return render_template('login.html')

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            if user and check_password_hash(user['password'], password):
                session['username'] = user['username']
                session['user_id'] = user['id']
                log_user_action(user['id'], "login")
                flash("Login successful!", "success")
                return redirect('/dashboard')
            else:
                flash("Incorrect username or password", "error")
        except mysql.connector.Error as err:
            flash(f"Database error: {err}", "error")
        finally:
            cursor.close()
            conn.close()
    return render_template('login.html')

@app.route('/select_symptom', methods=['POST'])
@require_login
def select_symptom():
    symptom_type_id = request.form.get('symptom_id')
    session['symptom_type_id'] = int(symptom_type_id)

    user_id = session['user_id']

    # บันทึกอาการที่เลือกลง symptoms
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO symptoms (user_id, symptom_type_id) VALUES (%s, %s)", (user_id, symptom_type_id))
    conn.commit()

    # ✅ log_question: ผู้ใช้เลือกอาการอะไร
    cursor.execute("SELECT name, ask_has_fever, has_fever_logic, skip_severity FROM symptom_types WHERE id = %s", (symptom_type_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        flash("ไม่พบอาการนี้ในระบบ", "error")
        return redirect('/dashboard')

    symptom_name, ask_has_fever, has_fever_logic, skip_severity = row
    cursor.close()
    conn.close()

    # ✅ Flow: ถามมีไข้ → ถามปวดกล้ามเนื้อ → ข้าม severity → ถามตั้งครรภ์
    if ask_has_fever:
        return redirect('/question_has_fever')
    elif has_fever_logic:
        return redirect('/question_fever')
    elif skip_severity:
        return redirect('/question_pregnant')
    else:
        return redirect('/severity')

@app.route('/dashboard')
@require_login
def dashboard():
    conn = get_db()
    if conn is None:
        return redirect('/login')
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM symptom_types")
        symptoms = cursor.fetchall()
        return render_template('first.html', symptoms=symptoms)
    except mysql.connector.Error as err:
        flash(f"Database error: {err}", "error")
        return redirect('/login')
    finally:
        cursor.close()
        conn.close()

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_user_action(session['user_id'], "logout")
    session.clear()
    flash("Logout successful!", "success")
    return redirect('/login')

#ถามว่าปวดกล้ามเนื้อหรือไม่
@app.route('/question_fever', methods=['GET', 'POST'])
@require_login
def question_fever():
    if request.method == 'POST':
        muscle_pain = request.form.get('muscle_pain') == 'yes'
        session['muscle_pain'] = muscle_pain
        # ✅ บันทึกค่าลงตาราง symptoms
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE symptoms 
            SET has_fever = %s 
            WHERE id = (
                SELECT id FROM (
                    SELECT id FROM symptoms 
                    WHERE user_id = %s 
                    ORDER BY id DESC LIMIT 1
                ) AS latest
            )
        """, (int(muscle_pain), session['user_id']))
        conn.commit()
        cursor.close()
        conn.close()

        if muscle_pain:
            return render_template('advise_doctor.html', reason="คุณมีอาการปวดเมื่อยกล้ามเนื้อร่วมกับไข้ อาจเสี่ยงเป็นไข้หวัดใหญ่ โปรดพบแพทย์")
        else:
            return redirect('/question_pregnant')

    return render_template('fever_muscle.html')

# ✅ ฟังก์ชัน log_user_action คงเดิม

def log_user_action(user_id, action):
    conn = get_db()
    if conn is None:
        return
    cursor = conn.cursor()
    cursor.execute("INSERT INTO user_logs (user_id, action) VALUES (%s, %s)", (user_id, action))
    conn.commit()
    cursor.close()
    conn.close()
    print(f"[log_user_action] user_id: {user_id}, action: {action}")

# ✅ คุณสามารถนำวิธีแก้จาก /submit_severity ไปใช้ในฟังก์ชันอื่นๆ ที่มีการ update ด้วย ORDER BY ได้เลย

@app.route('/submit_severity', methods=['POST'])
def submit_severity():
    if 'user_id' not in session:
        return redirect('/login')

    severity = int(request.form.get('severity'))
    note = request.form.get('note')
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')

    # บันทึกลง DB
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE symptoms 
        SET severity = %s, severity_note = %s 
        WHERE id = (
            SELECT id FROM (
                SELECT id FROM symptoms 
                WHERE user_id = %s 
                ORDER BY id DESC LIMIT 1
            ) AS temp
        )
    """, (severity, note, user_id))
    conn.commit()
    cursor.close()
    conn.close()

    session['severity'] = severity  # 👈 สำคัญ
    

    return redirect('/check_condition')  # 👈 ส่งผู้ใช้ไปต่อ

#ระดับความปวดมากกว่า 5 จะแนะนำพบแพทย์
@app.route('/check_condition', methods=['GET', 'POST'])
def check_condition():
    if request.method == 'POST':
        severity = int(request.form.get('severity'))
        session['severity'] = severity
    else:
        severity = session.get('severity')

    if severity is None:
        return redirect('/severity')

    if severity >= 5:
        return render_template('advise_doctor.html', reason="อาการรุนแรง")
    else:
        return redirect('/question_pregnant')

#ถามคำถามว่าตั้งครรภ์หรือไม่
@app.route('/question_pregnant', methods=['GET', 'POST'])
@require_login
def question_pregnant():
    if request.method == 'POST':
        pregnant = request.form.get('pregnant')  # yes / no
        is_pregnant = pregnant == 'yes'
        session['is_pregnant'] = is_pregnant
        user_id = session.get('user_id')
        severity = session.get('severity')
        allergy = session.get('paracetamol_allergy', False)

        if severity is None:
            return redirect('/severity')  # เผื่อ session หาย

        reason = ""
        medicine = ""

        if is_pregnant:
            medicine = "แนะนำพบแพทย์"
            reason = "อยู่ระหว่างตั้งครรภ์"
            severity_note = f"{medicine} ({reason})"

            # ✅ อัปเดต DB ด้วย subquery ปลอดภัย
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM (
                    SELECT id FROM symptoms 
                    WHERE user_id = %s 
                    ORDER BY id DESC LIMIT 1
                ) AS temp
            """, (user_id,))
            result = cursor.fetchone()

            if result:
                symptom_id = result[0]
                cursor.execute("""
                    UPDATE symptoms
                    SET severity = %s,
                        severity_note = %s,
                        is_pregnant = %s,
                        paracetamol_allergy = %s
                    WHERE id = %s
                """, (
                    severity,
                    severity_note,
                    is_pregnant,
                    allergy,
                    symptom_id
                ))
                conn.commit()

            cursor.close()
            conn.close()

            log_user_action(user_id, "ตั้งครรภ์: ใช่")

            return render_template('advise_doctor.html', reason=reason)

        else:
            log_user_action(user_id, "ตั้งครรภ์: ไม่ใช่")
            return redirect('/question_allergy')

    return render_template('pregnant.html')


#ถามคำถามว่าแพ้ยามั้ย
@app.route('/question_allergy', methods=['GET', 'POST'])
@require_login
def question_allergy():
    if request.method == 'POST':
        # 🟢 ดึงค่าจากฟอร์ม
        allergy = request.form.get('allergy')  # "yes" หรือ "no"
        is_allergy = allergy == 'yes'
        session['paracetamol_allergy'] = is_allergy
        user_id = session.get('user_id')
        severity = session.get('severity')
        is_pregnant = session.get('is_pregnant', False)
        symptom_type_id = session.get('symptom_type_id')

        # 🔍 เตรียมตัวแปร
        reason = ""
        medicine = ""

        # 🔁 เช็คเงื่อนไขแพ้/ตั้งครรภ์
        if is_allergy:
            medicine = "แนะนำพบแพทย์"
            reason = "แพ้ยาพาราเซตามอล"
        elif is_pregnant:
            medicine = "แนะนำพบแพทย์"
            reason = "อยู่ระหว่างตั้งครรภ์"
        else:
            # ✅ ดึงยาจาก symptom_types
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT suggested_medicine FROM symptom_types WHERE id = %s", (symptom_type_id,))
            result = cursor.fetchone()
            if result:
                medicine = result[0]
                reason = ""
            else:
                medicine = "พาราเซตามอล 500mg"
                reason = "ไม่พบข้อมูลยาในระบบ"
            cursor.close()
            conn.close()

        # ✅ สร้างข้อความสรุป
        severity_note = f"แนะนำยา: {medicine}"

        # ✅ บันทึกลงตาราง symptoms
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM symptoms 
            WHERE user_id = %s 
            ORDER BY id DESC LIMIT 1
        """, (user_id,))
        result = cursor.fetchone()

        if result:
            symptom_id = result[0]
            cursor.execute("""
                UPDATE symptoms
                SET severity = %s,
                    severity_note = %s,
                    is_pregnant = %s,
                    paracetamol_allergy = %s
                WHERE id = %s
            """, (
                severity,
                severity_note,
                is_pregnant,
                is_allergy,
                symptom_id
            ))
            conn.commit()

        cursor.close()
        conn.close()

        # ✅ ตอบผู้ใช้
        if medicine == "แนะนำพบแพทย์":
            return render_template("advise_doctor.html", reason=reason)
        else:
            return render_template("recommend_result.html", medicine=medicine)

    return render_template("allergy.html")

#ถามคำถามว่ามีไข้หรือไม่
@app.route('/question_has_fever', methods=['GET', 'POST'])
def question_has_fever():
    if request.method == 'POST':
        has_fever = request.form.get('has_fever') == 'yes'
        session['has_fever'] = has_fever
        if has_fever:
            return redirect('/question_fever')
        else:
            # บันทึกลง symptoms
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE symptoms 
                SET has_fever = %s 
                WHERE user_id = %s 
                ORDER BY id DESC LIMIT 1
            """, (has_fever, session['user_id']))
            conn.commit()
            cursor.close()
            conn.close()

            return redirect('/severity')
    return render_template('question_has_fever.html')

#สรุปและแนะนำยา
@app.route('/recommend_medicine')
@require_login
def recommend_medicine():
    user_id = session.get('user_id')
    symptom_type_id = session.get('symptom_type_id')
    severity = session.get('severity',0)
    is_pregnant = session.get('is_pregnant', False)
    allergy = session.get('paracetamol_allergy', False)
    has_fever = session.get('has_fever', False)

    # 🔍 DEBUG: แสดงค่าจาก session
    print("user_id:", user_id)
    print("symptom_type_id (from session):", symptom_type_id)

    conn = get_db()
    cursor = conn.cursor()

    # ✅ ดึง suggested_medicine
    try:
        cursor.execute("SELECT suggested_medicine FROM symptom_types WHERE id = %s", (symptom_type_id,))
        result = cursor.fetchone()

        # 🔍 DEBUG: แสดงค่าที่ดึงได้
        print("SQL Result:", result)

    except Exception as e:
        print("เกิดข้อผิดพลาดตอน SELECT:", e)
        result = None

    # ✅ เงื่อนไขการเลือกยา
    if allergy or is_pregnant:
        medicine = "แนะนำพบแพทย์"
        print("สาเหตุ: แพ้ยา / ตั้งครรภ์")
    elif result:
        medicine = result[0]
        print("ยาที่แนะนำจาก DB:", medicine)
    else:
        medicine = "พาราเซตามอล 500mg"
        print("ไม่พบอาการใน DB, fallback:", medicine)

    # ✅ UPDATE symptoms ล่าสุด
    cursor.execute("""
        UPDATE symptoms
        SET severity = %s,
            severity_note = %s,
            is_pregnant = %s,
            paracetamol_allergy = %s,
            has_fever = %s
        WHERE id = (
            SELECT id FROM (
                SELECT id FROM symptoms WHERE user_id = %s ORDER BY id DESC LIMIT 1
            ) AS latest
        )
    """, (severity,medicine, int(is_pregnant), int(allergy), int(has_fever), user_id))

    conn.commit()
    cursor.close()
    conn.close()
    # ข้อความคำแนะนำเพิ่มเติม
    if allergy:
        extra = "คุณแพ้ยาพาราเซตามอล ควรพบแพทย์เพื่อรับคำแนะนำเพิ่มเติม"
    elif is_pregnant:
        extra = "คุณอยู่ระหว่างตั้งครรภ์ โปรดปรึกษาแพทย์ก่อนใช้ยา"

    return render_template('recommend_result.html', medicine=medicine, extra=extra)


@app.route('/severity')
def severity():
    return render_template('severity.html')

if __name__ == '__main__':
    app.run(debug=True)

 