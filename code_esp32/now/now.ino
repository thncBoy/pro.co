#include <WiFi.h>
#include <WebServer.h>
#include <AccelStepper.h>

/* ===================== Wi-Fi ===================== */
const char* WIFI_SSID = "THNC";
const char* WIFI_PASS = "77777777";

/* ===================== Mapping มอเตอร์ ===================== */
/* ช่อง 1 (พารา) */
const int M1_IN1 = 14;
const int M1_IN2 = 26;
const int M1_IN3 = 27;
const int M1_IN4 = 25;

/* ช่อง 2 (ORS) */
const int M2_IN1 = 18;
const int M2_IN2 = 19;
const int M2_IN3 = 21;
const int M2_IN4 = 22;

/* ช่อง 3 (กาวิสคอน) */
const int M3_IN1 = 32;
const int M3_IN2 = 33;
const int M3_IN3 = 23;
const int M3_IN4 = 5;

/* โหมดขับ: HALF4WIRE = ลื่น, FULL4WIRE = แรงค้างเฟส (ถ้าอยากแรงสุด ลอง FULL4WIRE + ลดสปีดลง) */
AccelStepper motor1(AccelStepper::FULL4WIRE, M1_IN1, M1_IN3, M1_IN2, M1_IN4);
AccelStepper motor2(AccelStepper::FULL4WIRE, M2_IN1, M2_IN3, M2_IN2, M2_IN4);
AccelStepper motor3(AccelStepper::FULL4WIRE, M3_IN1, M3_IN3, M3_IN2, M3_IN4);

const long STEPS_PER_REV = 2048;          // base ที่เข้ากับ FULL4WIRE
long SLOT_STEPS[3] = {
  (long)(STEPS_PER_REV * 1.3),  
  (long)(STEPS_PER_REV * 1.25),  
  (long)(STEPS_PER_REV * 1.7)     
};

/* โปรไฟล์มอเตอร์ (เริ่มกลาง ๆ แล้วค่อยจูน) */
const float MAX_SPEED = 900; // steps/s  (ถ้าหลุดสเต็ป ลดลงเช่น 900, 800)
const float ACCEL     = 700; // steps/s^2

/* ===================== Ultrasonic (HC-SR04) ===================== */
/* TRIG=GPIO4 (output), ECHO=GPIO34 (input only, ต้องแบ่งแรงดันจาก 5V → 3.3V) */
const int TRIG_PIN = 4;
const int ECHO_PIN = 34;

/* ===================== Server ===================== */
WebServer server(80);

/* ===================== สถานะการทำงาน ===================== */
bool dispensed = false; // เจอยาในถาดหรือยัง
float distance_cm = 999.0;

/* post-check: รอเซ็นเซอร์หลังหมุนจบ เพื่อให้สิ่งของตกถึงถาดก่อนสรุปผล */
bool postCheckActive = false;
unsigned long postCheckUntilMs = 0;
// =================> 🕒 แก้ไขตรงนี้ <=================
const unsigned long POST_CHECK_MS = 2500;   // 3s (รอ 3 วินาทีก่อนสรุปผล)

/* เกณฑ์ตรวจจับอัลตร้าโซนิก */
const float   DETECT_THRESHOLD_CM = 12.5; 
const uint8_t DETECT_STABLE_COUNT = 2;
const unsigned long SENSE_INTERVAL_MS = 50; // โพลล์ทุก ~20ms
unsigned long lastSenseMs = 0;
uint8_t detect_ok_count = 0;

/* ช่องที่กำลังทำงานอยู่ (0 = ว่าง, 1..3) */
int activeSlot = 0;

/* =============== Helper =============== */
void releaseCoils(int slot) {
  // ปล่อยคอยล์ของมอเตอร์ที่ระบุ (ลดกินไฟ/ความร้อนหลังจบ)
  if (slot == 1) {
    digitalWrite(M1_IN1, LOW); digitalWrite(M1_IN2, LOW);
    digitalWrite(M1_IN3, LOW); digitalWrite(M1_IN4, LOW);
  } else if (slot == 2) {
    digitalWrite(M2_IN1, LOW); digitalWrite(M2_IN2, LOW);
    digitalWrite(M2_IN3, LOW); digitalWrite(M2_IN4, LOW);
  } else if (slot == 3) {
    digitalWrite(M3_IN1, LOW); digitalWrite(M3_IN2, LOW);
    digitalWrite(M3_IN3, LOW); digitalWrite(M3_IN4, LOW);
  }
}

AccelStepper& motorForSlot(int slot) {
  if (slot == 1) return motor1;
  if (slot == 2) return motor2;
  return motor3; // slot == 3
}

/* อ่านระยะ (มี timeout) */
float readUltrasonic() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long dur = pulseIn(ECHO_PIN, HIGH, 15000UL); // ~15ms
  if (dur == 0) return 999.0; // ไม่มี echo
  return dur * 0.017f;         // cm
}

/* อัปเดตสถานะตรวจจับ (เรียกบ่อยใน loop) */
void updateDispenseDetect() {
  distance_cm = readUltrasonic();
  bool hit = (distance_cm > 0 && distance_cm < DETECT_THRESHOLD_CM);
  if (hit) {
    if (detect_ok_count < 255) detect_ok_count++;
  } else {
    detect_ok_count = 0;
  }
  dispensed = (detect_ok_count >= DETECT_STABLE_COUNT);
}

/* =============== REST =============== */
void handleWhoami() {
  server.send(200, "text/plain", WiFi.localIP().toString());
}

void handleStatus() {
  long msLeft = 0;
  if (postCheckActive) {
    long diff = (long)(postCheckUntilMs - millis());
    msLeft = diff > 0 ? diff : 0;
  }

  // busy=true เมื่อ: (กำลังหมุน) หรือ (อยู่ในช่วง post-check)
  bool isRunning = false;
  if (activeSlot != 0) {
    AccelStepper& m = motorForSlot(activeSlot);
    isRunning = (m.distanceToGo() != 0);
  }
  bool busy = isRunning || postCheckActive;

  String res = String("{\"busy\":") + (busy ? "true" : "false")
             + ",\"dispensed\":" + (dispensed ? "true" : "false")
             + ",\"distance_cm\":" + String(distance_cm, 1)
             + ",\"post_check_ms_left\":" + String(msLeft)
             + "}";
  server.send(200, "application/json", res);
}

void handleDispense() {
  // กันคีย์ซ้ำ
  bool isRunning = false;
  if (activeSlot != 0) {
    AccelStepper& cur = motorForSlot(activeSlot);
    isRunning = (cur.distanceToGo() != 0);
  }
  if (isRunning || postCheckActive) {
    server.send(409, "application/json", "{\"ok\":false,\"error\":\"busy\"}");
    return;
  }

  if (!server.hasArg("slot")) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing slot\"}");
    return;
  }
  int slot = server.arg("slot").toInt();
  if (slot < 1 || slot > 3) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"slot out of range\"}");
    return;
  }

  AccelStepper& m = motorForSlot(slot);
  m.setMaxSpeed(MAX_SPEED);
  m.setAcceleration(ACCEL);
  m.setMinPulseWidth(150);  // ทำพัลส์กว้างขึ้น ช่วยเสถียรขึ้นกับ ULN2003

  // รีเซ็ตผลตรวจจับ
  detect_ok_count = 0;
  dispensed = false;

  long addSteps = SLOT_STEPS[slot - 1];
  m.moveTo(m.currentPosition() + addSteps);
  activeSlot = slot;
  postCheckActive = false;
  String res = String("{\"ok\":true,\"slot\":") + slot + ",\"steps\":" + addSteps + "}";
  server.send(200, "application/json", res);
}

/* =============== Setup / Loop =============== */
void setup() {
  Serial.begin(115200);

  // ตั้งขาออกคอยล์ทั้งหมด (และปล่อยคอยล์ไว้ก่อน)
  pinMode(M1_IN1, OUTPUT); pinMode(M1_IN2, OUTPUT);
  pinMode(M1_IN3, OUTPUT); pinMode(M1_IN4, OUTPUT);
  pinMode(M2_IN1, OUTPUT); pinMode(M2_IN2, OUTPUT); pinMode(M2_IN3, OUTPUT); pinMode(M2_IN4, OUTPUT);
  pinMode(M3_IN1, OUTPUT); pinMode(M3_IN2, OUTPUT); pinMode(M3_IN3, OUTPUT); pinMode(M3_IN4, OUTPUT);
  releaseCoils(1); releaseCoils(2); releaseCoils(3);

  // Ultrasonic
  pinMode(TRIG_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_PIN, INPUT);
  // ⚠️ ใช้ตัวต้านทานแบ่งแรงดัน 5V → 3.3V

  // Wi-Fi
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting WiFi");
  while (WiFi.status() != WL_CONNECTED) { Serial.print("."); delay(400); }
  Serial.println(); Serial.print("IP: "); Serial.println(WiFi.localIP());

  // Routes
  server.on("/whoami",   handleWhoami);
  server.on("/status",   handleStatus);
  server.on("/dispense", handleDispense);
  server.begin();
  Serial.println("HTTP server started");
}

void loop() {
  server.handleClient();

  // โพลล์เซ็นเซอร์ถี่ ๆ
  unsigned long now = millis();
  if (now - lastSenseMs >= SENSE_INTERVAL_MS) {
    lastSenseMs = now;
    updateDispenseDetect();
  }

  // ขับเฉพาะมอเตอร์ที่ active
  if (activeSlot != 0) {
    AccelStepper& m = motorForSlot(activeSlot);
    if (m.distanceToGo() != 0) {
      m.run();
    } else if (!postCheckActive) {
      // หมุนจบ → ค้างเฟสสั้น ๆ ให้กลไกนิ่ง แล้วปล่อยคอยล์
      delay(200);
      releaseCoils(activeSlot);
      // เข้าสู่ช่วง post-check ให้เซ็นเซอร์มีเวลาจับ
      postCheckActive = true;
      postCheckUntilMs = millis() + POST_CHECK_MS;
      detect_ok_count = 0;   // เริ่มนับใหม่ในช่วงนี้
      dispensed = false;
    }
  }

  // จบ post-check เมื่อเจอยา หรือครบเวลา
  if (postCheckActive) {
    if (dispensed || millis() > postCheckUntilMs) {
      postCheckActive = false;
      activeSlot = 0;        // พร้อมรับคำสั่งรอบใหม่
    }
  }
}