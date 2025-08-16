# iot_client.py
import os
import requests

DISPENSER_URL = os.environ.get("DISPENSER_URL", "http://172.20.10.4")
STATUS_TIMEOUT = float(os.environ.get("ESP_STATUS_TIMEOUT", "2.0"))   # วินาที
DISPENSE_TIMEOUT = float(os.environ.get("ESP_DISPENSE_TIMEOUT", "6.0"))

def get_dispenser_url() -> str:
    """คืนค่า URL ปัจจุบันของเครื่องจ่ายยา"""
    return DISPENSER_URL

def iot_status() -> dict:
    """เรียก /status จาก ESP32"""
    url = f"{get_dispenser_url()}/status"
    r = requests.get(url, timeout=STATUS_TIMEOUT)
    r.raise_for_status()   
    return r.json()

def iot_dispense(slot: int) -> dict:
    """สั่งจ่ายยา /dispense?slot=<1..3>"""
    url = f"{get_dispenser_url()}/dispense"
    r = requests.get(url, params={"slot": slot}, timeout=DISPENSE_TIMEOUT)
    r.raise_for_status()
    return r.json()
