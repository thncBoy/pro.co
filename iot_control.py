import RPi.GPIO as GPIO
import time

motor_map = {
    "พาราเซตอมอล" : 17,
    "กาวิสคอน" : 18,
    "ผงเกลือแร่" : 27
}

def dispense_medicine(medicine_name):
    if medicine_name not in motor_map:
        print(f"ไม่พบยา {medicine_name} ในระบบ")
        return
    
    pin = motor_map[medicine_name]
    



