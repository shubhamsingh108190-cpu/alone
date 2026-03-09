#!/usr/bin/env python3
"""
Rupiyo Telegram Bot — with Admin Panel
────────────────────────────────────────────────────────────
.env variables required:
  BOT_TOKEN            = your telegram bot token
  JSONBIN_MASTER_KEY   = $2a$10$...
  JSONBIN_INDEX_BIN    = <index bin id>
  ADMIN_IDS            = 123456789,987654321   (comma-separated Telegram user IDs)
  PORT                 = 8000  (optional, for Render health-check server)

Render deployment:
  Build command: pip install "python-telegram-bot==20.7" pycryptodome requests python-dotenv
  Start command: python rupiyo_bot.py
"""

DEBUG = False

import asyncio
import threading
import base64
import glob
import hashlib
import json
import logging
import os
import random
import string
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

# ── Load .env file FIRST ──────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

import requests
from Crypto.Cipher import AES
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s · %(levelname)s · %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ── Credentials ───────────────────────────────────────────────────────────────
BOT_TOKEN          = os.environ.get("BOT_TOKEN")
JSONBIN_MASTER_KEY = os.environ.get("JSONBIN_MASTER_KEY")
JSONBIN_INDEX_BIN  = os.environ.get("JSONBIN_INDEX_BIN")
PORT               = int(os.environ.get("PORT", 8000))
PER_PAGE           = 10

# ── Admin IDs: comma-separated list in ADMIN_IDS env var ─────────────────────
def _parse_admin_ids() -> set:
    raw = os.environ.get("ADMIN_IDS", "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids

ADMIN_IDS: set = _parse_admin_ids()


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ═══════════════════════════════════════════════════════════════════════════════
#  RENDER HEALTH-CHECK SERVER
# ═══════════════════════════════════════════════════════════════════════════════

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, fmt, *args):
        pass  # suppress access logs


def _start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"🌐 Health-check server running on port {PORT}")


# ═══════════════════════════════════════════════════════════════════════════════
#  BACKEND CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

class Config:
    SESSION_DIR = os.path.expanduser("~/Rupiyo")
    DEBUG       = DEBUG

    API_ENDPOINTS = {
        'send_otp':            "https://api.rupiyo.app/v1.1/user/otp",
        'login':               "https://api.rupiyo.app/v1/user/login",
        'signup':              "https://api.rupiyo.app/v1/user/signup",
        'refresh_token':       "https://api.rupiyo.app/v1/user/token/refresh",
        'v2_profile_init':     "https://api.rupiyo.app/v2/basket/profile/init",
        'v1_basket_init':      "https://api.rupiyo.app/v1/basket/profile/init",
        'v1_user_wallet':      "https://api.rupiyo.app/v1/user/wallet",
        'v1_user_profile':     "https://api.rupiyo.app/v1/user/profile/me",
        'v2_wallet_balance':   "https://api.rupiyo.app/v2/wallet/{}/balance",
        'v6_offer_list':       "https://api.rupiyo.app/v6/offer/user/list",
        'v6_offer_details':    "https://api.rupiyo.app/v6/offer/user/details",
        'v1_offer_reward_cta': "https://api.rupiyo.app/v1/offer/reward/cta",
        'v1_user_offer_signal':"https://api.rupiyo.app/v1/user_offer/signal/ongoing",
        'v1_telemetry_sync':   "https://api.rupiyo.app/v1/basket/telemetry/sync",
        'payout_store_info':   "https://api.rupiyo.app/v2/transaction/payout_store/info",
        'payout_pack_list':    "https://api.rupiyo.app/v2/transaction/payout_store/payout_pack/list",
        'purchase_payout':     "https://api.rupiyo.app/v2/wallet/payout_pack/wallet/{}/purchase",
        'fcm_token_update':    "https://api.rupiyo.app/v1/user/profile/fcm_token/update",
    }

    DEVICE_MODELS = [
        "Xiaomi-23076PC4BI",
        "Samsung-SM-S918B",
        "Google-Pixel-8-Pro",
        "OnePlus-PJD110",
    ]

    POPULAR_APPS = [
        {"package": "com.whatsapp",                        "days_range": (30,  180)},
        {"package": "com.instagram.android",               "days_range": (30,  180)},
        {"package": "com.google.android.youtube",          "days_range": (90,  400)},
        {"package": "com.google.android.gm",               "days_range": (200, 600)},
        {"package": "com.spotify.music",                   "days_range": (90,  400)},
        {"package": "com.netflix.mediaclient",             "days_range": (90,  400)},
        {"package": "com.amazon.mShop.android.shopping",   "days_range": (20,  200)},
        {"package": "com.google.android.apps.maps",        "days_range": (200, 600)},
        {"package": "com.ubercab",                         "days_range": (20,  200)},
        {"package": "com.swiggy.android",                  "days_range": (20,  200)},
        {"package": "com.facebook.katana",                 "days_range": (30,  180)},
        {"package": "com.zhiliaoapp.musically",            "days_range": (40,  250)},
        {"package": "com.google.android.apps.photos",      "days_range": (200, 600)},
        {"package": "com.microsoft.office.word",           "days_range": (100, 300)},
        {"package": "com.rupiyo.realmoney.rewardsapp",     "days_range": (1,   7)},
    ]

    MAX_WORKERS = 5

    KNOWN_PACKS = [
        {'pack_id': 6,     'payout': {'amount': 5},   'name': 'Rs. 5 Instant UPI Credit'},
        {'pack_id': 7,     'payout': {'amount': 30},  'name': 'Rs. 30 Instant UPI Credit'},
        {'pack_id': 87046, 'payout': {'amount': 50},  'name': 'Instant UPI Payout'},
        {'pack_id': 16,    'payout': {'amount': 100}, 'name': 'Rs. 100 Instant UPI Credit'},
        {'pack_id': 81,    'payout': {'amount': 250}, 'name': 'Rs. 250 Instant UPI Credit'},
        {'pack_id': 83,    'payout': {'amount': 500}, 'name': 'Rs. 500 Instant UPI Credit'},
    ]

    @classmethod
    def initialize(cls):
        os.makedirs(cls.SESSION_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILS
# ═══════════════════════════════════════════════════════════════════════════════

class Utils:
    @staticmethod
    def generate_uuid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def generate_random_hex(length: int) -> str:
        return ''.join(random.choices(string.hexdigits[:16], k=length)).lower()

    @staticmethod
    def generate_android_id() -> str:
        return Utils.generate_random_hex(16)

    @staticmethod
    def generate_drm_id() -> str:
        return Utils.generate_random_hex(64)

    @staticmethod
    def generate_x_token() -> str:
        chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
        return '03AFcWeA' + ''.join(random.choices(chars, k=593))

    @staticmethod
    def generate_app_instance_id() -> str:
        return Utils.generate_random_hex(32)

    @staticmethod
    def generate_fcm_token() -> str:
        chars  = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
        prefix = ''.join(random.choices(chars, k=22))
        suffix = ''.join(random.choices(chars, k=122))
        return f"{prefix}:APA91b{suffix}"

    @staticmethod
    def format_currency(amount: Union[int, float, str]) -> str:
        try:
            return f"₹{float(amount):.2f}"
        except (ValueError, TypeError):
            return "₹0.00"

    @staticmethod
    def format_time_remaining(seconds: Union[int, float, str]) -> str:
        try:
            seconds = int(seconds)
            hours   = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        except (ValueError, TypeError):
            return "Unknown"

    @staticmethod
    def get_ist_time() -> datetime:
        return datetime.now() + timedelta(hours=5, minutes=30)

    @staticmethod
    def mask_phone(phone: str) -> str:
        if not phone or len(phone) < 4:
            return "****"
        return "*" * (len(phone) - 2) + phone[-2:]

    @staticmethod
    def validate_upi_id(upi_id: str) -> Tuple[bool, str]:
        if not upi_id or '@' not in upi_id:
            return False, "Invalid UPI ID"
        parts = upi_id.split('@')
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return False, "Invalid UPI ID format"
        return True, "Valid"

    @staticmethod
    def decode_jwt_exp(token: str) -> Optional[int]:
        try:
            payload_b64 = token.split('.')[1]
            padding     = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += '=' * padding
            payload = json.loads(base64.b64decode(payload_b64).decode('utf-8'))
            return payload.get('exp')
        except Exception:
            return None

    @staticmethod
    def is_token_valid(token: str, buffer_seconds: int = 300) -> bool:
        if not token:
            return False
        exp = Utils.decode_jwt_exp(token)
        if exp is None:
            return False
        return time.time() < (exp - buffer_seconds)

    @staticmethod
    def debug_print(msg: str):
        if Config.DEBUG:
            log.debug(msg)


_ERROR_MAP = {
    "account blocked":       "🚫 Your account has been blocked. Please contact Rupiyo support.",
    "account banned":        "🚫 Your account has been banned. Please contact Rupiyo support.",
    "incorrect otp":         "❌ Wrong OTP. Please try again.",
    "invalid otp":           "❌ Wrong OTP. Please try again.",
    "otp expired":           "❌ OTP has expired. Please request a new one.",
    "token expired":         "❌ Session expired. Please log in again.",
    "user not found":        "❌ No account found with this number.",
    "phone already exists":  "⚠️ This number is already registered.",
    "invalid phone":         "❌ Invalid phone number.",
    "referral not found":    "⚠️ Referral code not found — account created without it.",
    "insufficient balance":  "❌ Insufficient balance for this withdrawal.",
    "invalid vpa":           "❌ Invalid UPI ID. Please check and try again.",
    "network error":         "❌ Network error. Please try again.",
}

def friendly_error(raw: str) -> str:
    if not raw:
        return "❌ Something went wrong. Please try again."
    lower = raw.lower()
    for key, msg in _ERROR_MAP.items():
        if key in lower:
            return msg
    return "❌ Something went wrong. Please try again."


# ═══════════════════════════════════════════════════════════════════════════════
#  JSONBIN STORAGE
# ═══════════════════════════════════════════════════════════════════════════════

class JSONBinStorage:
    BASE = "https://api.jsonbin.io/v3"

    @staticmethod
    def _headers():
        return {
            "Content-Type":  "application/json",
            "X-Master-Key":  JSONBIN_MASTER_KEY,
            "X-Bin-Private": "true",
        }

    @staticmethod
    def _read_index() -> Dict:
        if not JSONBIN_MASTER_KEY or not JSONBIN_INDEX_BIN:
            return {}
        try:
            r = requests.get(
                f"{JSONBinStorage.BASE}/b/{JSONBIN_INDEX_BIN}/latest",
                headers=JSONBinStorage._headers(), timeout=10,
            )
            return r.json().get("record", {}) if r.status_code == 200 else {}
        except Exception:
            return {}

    @staticmethod
    def _write_index(data: Dict) -> bool:
        if not JSONBIN_MASTER_KEY or not JSONBIN_INDEX_BIN:
            return False
        try:
            r = requests.put(
                f"{JSONBinStorage.BASE}/b/{JSONBIN_INDEX_BIN}",
                headers=JSONBinStorage._headers(),
                json=data, timeout=10,
            )
            return r.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _get_or_create_user_bin(tg_uid: int) -> Optional[str]:
        index = JSONBinStorage._read_index()
        key   = str(tg_uid)
        if key in index:
            return index[key]
        try:
            r = requests.post(
                f"{JSONBinStorage.BASE}/b",
                headers={**JSONBinStorage._headers(), "X-Bin-Name": f"rupiyo_{tg_uid}"},
                json={"_init": True}, timeout=10,
            )
            if r.status_code == 200:
                bin_id     = r.json()["metadata"]["id"]
                index[key] = bin_id
                JSONBinStorage._write_index(index)
                return bin_id
        except Exception:
            pass
        return None

    @staticmethod
    def read_user_sessions(tg_uid: int) -> Dict:
        bin_id = JSONBinStorage._get_or_create_user_bin(tg_uid)
        if not bin_id:
            return {}
        try:
            r = requests.get(
                f"{JSONBinStorage.BASE}/b/{bin_id}/latest",
                headers=JSONBinStorage._headers(), timeout=10,
            )
            return r.json().get("record", {}) if r.status_code == 200 else {}
        except Exception:
            return {}

    @staticmethod
    def write_user_sessions(tg_uid: int, sessions: Dict) -> bool:
        bin_id = JSONBinStorage._get_or_create_user_bin(tg_uid)
        if not bin_id:
            return False
        try:
            r = requests.put(
                f"{JSONBinStorage.BASE}/b/{bin_id}",
                headers=JSONBinStorage._headers(),
                json=sessions, timeout=10,
            )
            return r.status_code == 200
        except Exception:
            return False

    @staticmethod
    def is_enabled() -> bool:
        return bool(JSONBIN_MASTER_KEY and JSONBIN_INDEX_BIN)


# ═══════════════════════════════════════════════════════════════════════════════
#  SESSION MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class SessionManager:
    @staticmethod
    def get_session_path(filename: str) -> str:
        return os.path.join(Config.SESSION_DIR, filename)

    @staticmethod
    def _parse_session_dict(data: Dict, ref: str) -> Optional[Dict]:
        try:
            phone  = data.get('session_info', {}).get('phone', 'Unknown')
            ts_str = data.get('session_info', {}).get('timestamp', '')
            try:
                ts = datetime.fromisoformat(ts_str) if ts_str else datetime.min
            except (ValueError, TypeError):
                ts = datetime.min
            has_refresh    = data.get('tokens', {}).get('refresh_token') is not None
            has_v1_profile = data.get('wallet', {}).get('v1_profile_id') is not None
            if not has_refresh:
                status = "invalid"
            elif not has_v1_profile:
                status = "recoverable"
            else:
                status = "complete"
            return {
                'file':         ref,
                'phone':        phone,
                'masked_phone': Utils.mask_phone(phone),
                'user_name':    data.get('session_info', {}).get('user_name', 'User'),
                'timestamp':    ts_str[:10] if ts_str else 'Unknown',
                'datetime':     ts,
                'data':         data,
                'status':       status,
            }
        except Exception:
            return None

    @staticmethod
    def find_saved_sessions(tg_uid: Optional[int] = None) -> List[Dict]:
        sessions_by_phone: Dict[str, Dict] = {}
        if tg_uid and JSONBinStorage.is_enabled():
            remote = JSONBinStorage.read_user_sessions(tg_uid)
            stale  = []
            for phone_key, data in remote.items():
                rec = SessionManager._parse_session_dict(data, f"remote:{tg_uid}:{phone_key}")
                if rec is None or rec['status'] == 'invalid':
                    stale.append(phone_key)
                    continue
                sessions_by_phone[phone_key] = rec
            if stale:
                for k in stale:
                    remote.pop(k, None)
                JSONBinStorage.write_user_sessions(tg_uid, remote)
        else:
            session_files = glob.glob(os.path.join(Config.SESSION_DIR, "rupiyo_session_*.json"))
            to_delete     = []
            for file in session_files:
                try:
                    with open(file, 'r') as f:
                        data = json.load(f)
                    rec = SessionManager._parse_session_dict(data, file)
                    if rec is None or rec['status'] == 'invalid':
                        to_delete.append(file)
                        continue
                    phone = rec['phone']
                    if phone not in sessions_by_phone or rec['datetime'] > sessions_by_phone[phone]['datetime']:
                        if phone in sessions_by_phone:
                            to_delete.append(sessions_by_phone[phone]['file'])
                        sessions_by_phone[phone] = rec
                    else:
                        to_delete.append(file)
                except Exception:
                    to_delete.append(file)
            for file in to_delete:
                try:
                    os.remove(file)
                except OSError:
                    pass

        sessions = list(sessions_by_phone.values())
        sessions.sort(key=lambda x: x['datetime'], reverse=True)
        return sessions

    @staticmethod
    def _build_session_data(device: 'DeviceIdentity') -> Dict:
        data = {
            "tokens": {
                "access_token":  device.auth_token,
                "refresh_token": device.refresh_token,
            },
            "wallet": {
                "v1_profile_id": device.v1_profile_id,
                "wid":           device.wid,
            },
            "session_info": {
                "user_id":   device.user_id,
                "phone":     device.user_phone,
                "user_name": device.user_name,
                "fcm_token": device.fcm_token,
                "timestamp": datetime.now().isoformat(),
            },
        }
        return {k: v for k, v in data.items() if v is not None}

    @staticmethod
    def save_session(device: 'DeviceIdentity', tg_uid: Optional[int] = None) -> str:
        data  = SessionManager._build_session_data(device)
        phone = device.user_phone or "unknown"
        ref   = f"remote:{tg_uid}:{phone}"

        if JSONBinStorage.is_enabled() and tg_uid:
            remote        = JSONBinStorage.read_user_sessions(tg_uid)
            remote[phone] = data
            JSONBinStorage.write_user_sessions(tg_uid, remote)
        else:
            masked_phone = Utils.mask_phone(phone)
            timestamp    = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename     = f"rupiyo_session_{masked_phone}_{timestamp}.json"
            ref          = SessionManager.get_session_path(filename)
            for old_file in glob.glob(os.path.join(Config.SESSION_DIR, "rupiyo_session_*.json")):
                try:
                    with open(old_file, 'r') as _f:
                        _d = json.load(_f)
                    if _d.get('session_info', {}).get('phone') == phone:
                        os.remove(old_file)
                except (OSError, json.JSONDecodeError):
                    pass
            with open(ref, 'w') as f:
                json.dump(data, f, indent=2)
        return ref

    @staticmethod
    def delete_session(ref: str) -> bool:
        if ref.startswith("remote:"):
            _, tg_uid_str, phone = ref.split(":", 2)
            try:
                tg_uid = int(tg_uid_str)
                remote = JSONBinStorage.read_user_sessions(tg_uid)
                remote.pop(phone, None)
                return JSONBinStorage.write_user_sessions(tg_uid, remote)
            except Exception:
                return False
        else:
            try:
                os.remove(ref)
                return True
            except OSError:
                return False

    @staticmethod
    def update_session(ref: str, device: 'DeviceIdentity') -> bool:
        data = SessionManager._build_session_data(device)
        if ref.startswith("remote:"):
            _, tg_uid_str, phone = ref.split(":", 2)
            try:
                tg_uid        = int(tg_uid_str)
                remote        = JSONBinStorage.read_user_sessions(tg_uid)
                remote[phone] = data
                return JSONBinStorage.write_user_sessions(tg_uid, remote)
            except Exception:
                return False
        else:
            try:
                with open(ref, 'w') as f:
                    json.dump(data, f, indent=2)
                return True
            except Exception:
                return False


# ═══════════════════════════════════════════════════════════════════════════════
#  DEVICE IDENTITY
# ═══════════════════════════════════════════════════════════════════════════════

class DeviceIdentity:
    def __init__(self):
        self.device_id    = Utils.generate_uuid()
        self.device_model = random.choice(Config.DEVICE_MODELS)
        self.android_id   = Utils.generate_android_id()
        self.drm_id       = Utils.generate_drm_id()
        self.app_set_id   = Utils.generate_uuid()
        self.package_name = "com.rupiyo.realmoney.rewardsapp"
        self.appn         = "71"
        self.x_sid        = Utils.generate_uuid()
        self.ga_id        = Utils.generate_uuid()
        self.adv_id       = Utils.generate_uuid()

        self.fcm_token:     Optional[str] = None
        self.auth_token:    Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.user_id:       Optional[str] = None
        self.user_phone:    Optional[str] = None
        self.user_name:     Optional[str] = None
        self.v1_profile_id: Optional[str] = None
        self.wid:           Optional[str] = None
        self.balance:       float         = 0.0

    def load_from_session(self, session_data: Dict):
        tokens             = session_data.get('tokens', {})
        self.auth_token    = tokens.get('access_token')
        self.refresh_token = tokens.get('refresh_token')
        wallet             = session_data.get('wallet', {})
        self.v1_profile_id = wallet.get('v1_profile_id')
        self.wid           = wallet.get('wid')
        info               = session_data.get('session_info', {})
        self.user_id       = info.get('user_id')
        self.user_phone    = info.get('phone')
        self.user_name     = info.get('user_name', 'User')
        self.fcm_token     = info.get('fcm_token')

    def get_headers(self, additional: Optional[Dict] = None) -> Dict[str, str]:
        headers = {
            'User-Agent':     'okhttp/4.12.0',
            'Accept-Encoding':'gzip',
            'appn':           '71',
            'x-language':     'ENGLISH',
            'x-pn':           self.package_name,
            'x-platform':     'android',
            'x-app-id':       'rupiyo',
            'x-device-id':    self.device_id,
            'x-device-model': self.device_model,
        }
        if self.auth_token:
            headers['auth-token'] = self.auth_token
        if self.v1_profile_id:
            headers['x-profile'] = self.v1_profile_id
        if additional:
            headers.update(additional)
        return headers

    def needs_healing(self) -> bool:
        return self.refresh_token is not None and self.v1_profile_id is None

    def heal_from_profile(self, profile_data: Dict):
        if not self.user_id   and profile_data.get('user_id'):
            self.user_id   = profile_data.get('user_id')
        if not self.user_name and profile_data.get('full_name'):
            self.user_name = profile_data.get('full_name')
        if not self.user_phone and profile_data.get('phone_number'):
            self.user_phone = profile_data.get('phone_number')


# ═══════════════════════════════════════════════════════════════════════════════
#  ENCRYPTION
# ═══════════════════════════════════════════════════════════════════════════════

class Encryption:
    @staticmethod
    def double_md5_key(user_id: str, user_phone: str) -> bytes:
        key_input = f"{user_id}{user_phone}"
        md5_once  = hashlib.md5(key_input.encode()).hexdigest()
        md5_twice = hashlib.md5(md5_once.encode()).hexdigest()
        return md5_twice.encode('utf-8')

    @staticmethod
    def encrypt_signal_data(device: DeviceIdentity, offer_id: str, reward_id: int) -> Optional[str]:
        try:
            data       = f'{{"offer_id":{offer_id},"reward_id":{reward_id}}}'
            json_bytes = data.encode('utf-8')
            key_bytes  = Encryption.double_md5_key(device.user_id, device.user_phone)
            iv         = os.urandom(16)
            cipher     = AES.new(key_bytes, AES.MODE_CFB, iv=iv, segment_size=128)
            encrypted  = cipher.encrypt(json_bytes)
            b64        = base64.b64encode(iv + encrypted).decode('ascii')
            return b64.replace('+', '-').replace('/', '_').rstrip('=')
        except Exception as e:
            Utils.debug_print(f"Encryption error: {e}")
            return None


# ═══════════════════════════════════════════════════════════════════════════════
#  RUPIYO API
# ═══════════════════════════════════════════════════════════════════════════════

class RupiyoAPI:
    @staticmethod
    def _request(method: str, url: str, retries: int = 2, **kwargs):
        last_exc = None
        for attempt in range(retries + 1):
            try:
                return requests.request(method, url, **kwargs)
            except requests.RequestException as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise last_exc

    @staticmethod
    def send_otp(phone, x_profile, device, x_token):
        try:
            headers  = device.get_headers({'Content-Type': 'application/json', 'x-token': x_token, 'x-profile': x_profile})
            payload  = {"phone_number": phone, "is_retry": False, "retry_method": ""}
            response = RupiyoAPI._request('POST', Config.API_ENDPOINTS['send_otp'], json=payload, headers=headers, timeout=30)
            return response.status_code, response.text
        except Exception as e:
            return 0, str(e)

    @staticmethod
    def login_with_otp(phone, otp, x_profile, device, v2_profile_id, x_token):
        try:
            headers     = device.get_headers({'Content-Type': 'application/json', 'x-token': x_token, 'x-profile': v2_profile_id})
            payload     = {"phone_number": phone, "otp": int(otp), "gaid": device.ga_id, "app_instance_id": Utils.generate_app_instance_id()}
            response    = RupiyoAPI._request('POST', Config.API_ENDPOINTS['login'], json=payload, headers=headers, timeout=30)
            is_new_user = False
            ban_message = None
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data.get('new_user') is True:
                        is_new_user = True
                    else:
                        device.auth_token    = data.get('tokens', {}).get('access_token')
                        device.refresh_token = data.get('tokens', {}).get('refresh_token')
                        device.user_id       = data.get('user', {}).get('user_id')
                        device.user_name     = data.get('user', {}).get('full_name', 'User')
                        device.wid           = data.get('wid')
                except Exception as e:
                    Utils.debug_print(f"Login parse error: {e}")
            elif response.status_code == 403:
                try:
                    data = response.json()
                    if data.get('error_code') == 1004 or "account blocked" in data.get('error', '').lower():
                        ban_message = data.get('error', 'Account blocked')
                except Exception:
                    pass
            return response.status_code, response.text, is_new_user, ban_message
        except Exception as e:
            return 0, str(e), False, None

    @staticmethod
    def signup_new_user(device, phone, otp, full_name, referral_code, x_profile, v2_profile_id, x_token):
        try:
            headers  = device.get_headers({'x-environment': 'clone', 'x-profile': v2_profile_id, 'content-type': 'application/json; charset=UTF-8'})
            payload  = {"phone_number": phone, "otp": int(otp), "full_name": full_name, "email": "", "referral_code": referral_code if referral_code else "", "utm_source": "utm_source=google-play&utm_medium=organic", "gaid": device.ga_id, "app_instance_id": Utils.generate_app_instance_id()}
            response = RupiyoAPI._request('POST', Config.API_ENDPOINTS['signup'], json=payload, headers=headers, timeout=30)
            ban_message = None
            if response.status_code == 200:
                try:
                    data                 = response.json()
                    device.auth_token    = data.get('tokens', {}).get('access_token')
                    device.refresh_token = data.get('tokens', {}).get('refresh_token')
                    device.user_id       = data.get('user', {}).get('user_id')
                    device.user_name     = data.get('user', {}).get('full_name', full_name)
                    device.wid           = data.get('wid')
                except Exception as e:
                    Utils.debug_print(f"Signup parse error: {e}")
            elif response.status_code == 403:
                try:
                    data = response.json()
                    if data.get('error_code') == 1004 or "account blocked" in data.get('error', '').lower():
                        ban_message = data.get('error', 'Account blocked')
                except Exception:
                    pass
            return response.status_code, response.text, ban_message
        except Exception as e:
            return 0, str(e), None

    @staticmethod
    def refresh_token(refresh_token):
        try:
            headers  = {'User-Agent': 'Mozilla/5.0 (Linux; Android 15; 23076PC4BI Build/AQ3A.240912.001; wv) AppleWebKit/537.36', 'Content-Type': 'application/json', 'Accept': 'application/json, text/plain, */*', 'x-pn': 'com.rupiyo.realmoney.rewardsapp', 'Origin': 'https://rupiyo.app', 'Referer': 'https://rupiyo.app/', 'x-requested-with': 'com.rupiyo.realmoney.rewardsapp'}
            response = RupiyoAPI._request('POST', Config.API_ENDPOINTS['refresh_token'], json={'refresh_token': refresh_token}, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return True, {'access_token': data.get('access_token'), 'refresh_token': data.get('refresh_token', refresh_token)}, None
            ban_message = None
            if response.status_code == 403:
                try:
                    data = response.json()
                    if data.get('error_code') == 1004 or "account blocked" in data.get('error', '').lower():
                        ban_message = data.get('error', 'Account blocked')
                        return False, "Account banned", ban_message
                except Exception:
                    pass
            return False, f"HTTP {response.status_code}", None
        except Exception as e:
            return False, str(e), None

    @staticmethod
    def update_fcm_token(device):
        try:
            if not device.fcm_token:
                device.fcm_token = Utils.generate_fcm_token()
            response = RupiyoAPI._request('PUT', Config.API_ENDPOINTS['fcm_token_update'], json={'fcm_token': device.fcm_token}, headers=device.get_headers({'content-type': 'application/json; charset=UTF-8'}), timeout=30)
            return response.status_code == 200
        except Exception:
            return False

    @staticmethod
    def send_ongoing_signal(device, offer_id, reward_id):
        try:
            encrypted_data = Encryption.encrypt_signal_data(device, offer_id, reward_id)
            if not encrypted_data:
                return False, "Encryption failed"
            headers  = device.get_headers({'Host': 'api.rupiyo.app', 'content-type': 'text/plain; charset=utf-8', 'content-length': str(len(encrypted_data))})
            response = RupiyoAPI._request('POST', Config.API_ENDPOINTS['v1_user_offer_signal'], headers=headers, data=encrypted_data, timeout=30)
            return (True, "Signal sent") if response.status_code == 200 else (False, f"HTTP {response.status_code}")
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_user_profile(device):
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['v1_user_profile'], headers=device.get_headers(), timeout=30)
            return (True, response.json()) if response.status_code == 200 else (False, None)
        except Exception:
            return False, None

    @staticmethod
    def get_wallet_id(device):
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['v1_user_wallet'], headers=device.get_headers(), timeout=30)
            return (True, response.json().get('wid')) if response.status_code == 200 else (False, None)
        except Exception:
            return False, None

    @staticmethod
    def get_wallet_balance(device):
        if not device.wid:
            return False, 0.0
        try:
            url      = Config.API_ENDPOINTS['v2_wallet_balance'].format(device.wid)
            response = RupiyoAPI._request('GET', url, headers=device.get_headers(), timeout=30)
            if response.status_code == 200:
                data = response.json()
                bal  = data.get('balance', {})
                return True, bal.get('amount', 0.0) if isinstance(bal, dict) else float(bal)
            return False, 0.0
        except Exception:
            return False, 0.0

    @staticmethod
    def refresh_wallet(device):
        if not device.wid:
            ok, wid = RupiyoAPI.get_wallet_id(device)
            if ok and wid:
                device.wid = wid
        if device.wid:
            ok, bal = RupiyoAPI.get_wallet_balance(device)
            if ok:
                device.balance = bal
                return True
        return False

    @staticmethod
    def get_offers(device, list_type, page=1):
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['v6_offer_list'], headers=device.get_headers(), params={'list_type': list_type, 'page': page}, timeout=30)
            return response.json().get('results', []) if response.status_code == 200 else None
        except Exception:
            return None

    @staticmethod
    def get_all_offers(device, list_type):
        all_offers = []
        page       = 1
        while True:
            offers = RupiyoAPI.get_offers(device, list_type, page)
            if not offers:
                break
            all_offers.extend(offers)
            page += 1
            time.sleep(0.1)
        return all_offers

    @staticmethod
    def get_offer_details(device, offer_id):
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['v6_offer_details'], headers=device.get_headers(), params={'offer_id': offer_id}, timeout=30)
            return response.json() if response.status_code == 200 else None
        except Exception:
            return None

    @staticmethod
    def get_reward_cta(device, reward_id):
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['v1_offer_reward_cta'], headers=device.get_headers(), params={'reward_id': reward_id, 'ga_id': device.ga_id}, timeout=30)
            return (True, response.json().get('url', '')) if response.status_code == 200 else (False, response.text)
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_payout_store_info(device):
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['payout_store_info'], headers=device.get_headers(), timeout=30)
            return response.json() if response.status_code == 200 else None
        except Exception:
            return None

    @staticmethod
    def get_payout_packs(device, method="UPI"):
        server_packs = []
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['payout_pack_list'], headers=device.get_headers(), params={'page': 1, 'payment_method': method, 'q': ''}, timeout=30)
            if response.status_code == 200:
                server_packs = response.json().get('packs', [])
        except Exception:
            pass
        seen_ids = {p.get('pack_id') for p in server_packs}
        merged   = list(server_packs)
        for p in Config.KNOWN_PACKS:
            if p['pack_id'] not in seen_ids:
                merged.append(p)
        merged.sort(key=lambda p: p.get('payout', {}).get('amount', 0))
        return merged

    @staticmethod
    def purchase_payout(device, pack_id, payment_address):
        try:
            url      = Config.API_ENDPOINTS['purchase_payout'].format(device.wid)
            response = RupiyoAPI._request('POST', url, json={'pack_id': pack_id, 'payment_address': payment_address, 'additional_vpa_params': {}}, headers=device.get_headers({'Content-Type': 'application/json'}), timeout=30)
            return (True, response.text) if response.status_code == 200 else (False, response.text)
        except Exception as e:
            return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
#  PROFILE MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ProfileManager:
    @staticmethod
    def init_v2_profile(device):
        try:
            data       = {"device_model": device.device_model, "android_id": device.android_id, "ga_id": device.ga_id, "adv_id": device.adv_id, "gsf_id": "", "drm_id": device.drm_id, "app_set_id": device.app_set_id, "package_id": device.package_name}
            json_bytes = json.dumps(data, separators=(',', ':')).encode('utf-8')
            key_str    = f"#{device.x_sid}_4#"
            key_bytes  = hashlib.md5(key_str.encode('utf-8')).hexdigest().encode('utf-8')
            iv         = os.urandom(16)
            cipher     = AES.new(key_bytes, AES.MODE_CFB, iv=iv, segment_size=128)
            encrypted  = cipher.encrypt(json_bytes)
            b64        = base64.b64encode(iv + encrypted).decode('ascii').replace('+', '-').replace('/', '_').rstrip('=')
            boundary   = str(uuid.uuid4())
            body       = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="basket"\r\nContent-Type: multipart/form-data\r\nContent-Length: {len(b64)}\r\n\r\n{b64}\r\n--{boundary}--\r\n'
            headers    = {'User-Agent': 'okhttp/4.12.0', 'x-sid': device.x_sid, 'x-profile': '', 'appn': device.appn, 'x-language': 'ENGLISH', 'x-device-id': device.device_id, 'x-device-model': device.device_model, 'x-pn': device.package_name, 'x-platform': 'android', 'x-app-id': 'rupiyo', 'Content-Type': f'multipart/form-data; boundary={boundary}'}
            response   = requests.post(Config.API_ENDPOINTS['v2_profile_init'], headers=headers, data=body, timeout=30)
            return response.json().get('profile_id') if response.status_code == 200 else None
        except Exception:
            return None

    @staticmethod
    def _build_basket_payload(device):
        app_list = []
        for app in Config.POPULAR_APPS:
            days         = random.randint(app['days_range'][0], app['days_range'][1])
            install_time = str(int((datetime.now().timestamp() * 1000) - (days * 86400000)))
            app_list.append({'package_name': app['package'], 'install_time': install_time})
        payload    = {"usage_data": [], "current_installed_apps": app_list, "device_model": device.device_model, "android_id": device.android_id, "app_set_id": device.app_set_id, "gsf_id": "", "drm_id": device.drm_id, "p_id": device.package_name, "adv_id": ""}
        json_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        key_bytes  = Encryption.double_md5_key(device.user_id, device.user_phone)
        iv         = os.urandom(16)
        cipher     = AES.new(key_bytes, AES.MODE_CFB, iv=iv, segment_size=128)
        encrypted  = cipher.encrypt(json_bytes)
        b64        = base64.b64encode(iv + encrypted).decode('ascii')
        return b64.replace('+', '-').replace('/', '_').replace('=', '')

    @staticmethod
    def _basket_headers(device):
        headers = {"auth-token": device.auth_token, "appn": device.appn, "x-language": "ENGLISH", "x-device-id": device.device_id, "x-device-model": device.device_model, "x-pn": device.package_name, "x-platform": "android", "x-app-id": "rupiyo", "User-Agent": "okhttp/4.12.0"}
        if device.v1_profile_id:
            headers["x-profile"] = device.v1_profile_id
        return headers

    @staticmethod
    def init_v1_profile(device):
        try:
            b64      = ProfileManager._build_basket_payload(device)
            headers  = ProfileManager._basket_headers(device)
            files    = {'file': ('basket', b64, 'multipart/form-data')}
            response = requests.post(Config.API_ENDPOINTS['v1_basket_init'], headers=headers, files=files, timeout=30)
            if response.status_code == 200:
                device.v1_profile_id = response.json().get('profile_id')
                return True, device.v1_profile_id
            return False, response.text
        except Exception as e:
            return False, str(e)

    @staticmethod
    def sync_telemetry(device):
        try:
            b64      = ProfileManager._build_basket_payload(device)
            headers  = ProfileManager._basket_headers(device)
            files    = {'file': ('encrypted_payload.txt', b64, 'multipart/form-data')}
            response = requests.post(Config.API_ENDPOINTS['v1_telemetry_sync'], headers=headers, files=files, timeout=30)
            return response.status_code == 200
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════════════
#  SELF HEALER
# ═══════════════════════════════════════════════════════════════════════════════

class SelfHealer:
    @staticmethod
    def heal_session(device, session_file):
        if not device.refresh_token:
            return False
        ok, result, ban_message = RupiyoAPI.refresh_token(device.refresh_token)
        if not ok:
            if ban_message:
                SessionManager.delete_session(session_file)
            return False
        device.auth_token = result['access_token']
        if result['refresh_token'] != device.refresh_token:
            device.refresh_token = result['refresh_token']
        ok, profile = RupiyoAPI.get_user_profile(device)
        if ok and profile:
            device.heal_from_profile(profile)
        if not device.v1_profile_id:
            device.device_id    = Utils.generate_uuid()
            device.device_model = random.choice(Config.DEVICE_MODELS)
            device.android_id   = Utils.generate_android_id()
            device.drm_id       = Utils.generate_drm_id()
            device.app_set_id   = Utils.generate_uuid()
            ProfileManager.init_v1_profile(device)
        RupiyoAPI.refresh_wallet(device)
        ProfileManager.sync_telemetry(device)
        RupiyoAPI.update_fcm_token(device)
        SessionManager.update_session(session_file, device)
        return True

    @staticmethod
    def sync_profile_fields(device, session_file):
        try:
            ok, profile = RupiyoAPI.get_user_profile(device)
            if not ok or not profile:
                return
            changed      = False
            server_name  = profile.get('full_name') or profile.get('name')
            server_phone = profile.get('phone_number') or profile.get('phone')
            server_uid   = profile.get('user_id')
            if server_name  and server_name  != device.user_name:  device.user_name  = server_name;  changed = True
            if server_phone and server_phone != device.user_phone: device.user_phone = server_phone; changed = True
            if server_uid   and server_uid   != device.user_id:    device.user_id    = server_uid;   changed = True
            if changed:
                SessionManager.update_session(session_file, device)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  BOT STATE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BotState:
    device:             Optional[DeviceIdentity] = None
    session_file:       Optional[str]            = None
    offers:             List                     = field(default_factory=list)
    list_type:          str                      = 'active'
    list_title:         str                      = ''
    page:               int                      = 1
    current_offer:      Optional[Dict]           = None
    packs:              List                     = field(default_factory=list)
    selected_pack:      Optional[Dict]           = None
    inactivity_timeout: float                    = field(default_factory=lambda: random.uniform(3*60, 4*60))
    phone:              str                      = ''
    x_profile:          str                      = ''
    x_token:            str                      = ''
    v2_profile_id:      str                      = ''
    otp:                str                      = ''
    full_name:          str                      = ''
    awaiting:           Optional[str]            = None
    last_activity:      float                    = field(default_factory=time.time)
    # Admin panel state
    admin_awaiting:     Optional[str]            = None


_user_states: Dict[int, BotState] = {}
_tg_uid_map:  Dict[str, int]      = {}


def get_state(uid: int) -> BotState:
    if uid not in _user_states:
        _user_states[uid] = BotState()
    return _user_states[uid]

def touch(st: BotState):
    st.last_activity = time.time()

def is_session_alive(st: BotState) -> bool:
    if not st.device:
        return False
    return (time.time() - st.last_activity) < st.inactivity_timeout


_global_offers:      Dict[str, Optional[List]] = {'active': None, 'ongoing': None, 'completed': None}
_global_offers_ts:   float                     = 0.0
_global_offers_lock                            = threading.Lock()
CACHE_TTL                                      = 5 * 60


def get_cached_offers(list_type):
    with _global_offers_lock:
        if _global_offers_ts and (time.time() - _global_offers_ts) < CACHE_TTL:
            return _global_offers.get(list_type)
    return None


def set_cached_offers(list_type, offers):
    global _global_offers_ts
    with _global_offers_lock:
        _global_offers[list_type] = offers
        _global_offers_ts = time.time()


def _get_any_active_device():
    for st in _user_states.values():
        if st.device and st.device.auth_token:
            return st.device
    return None


def start_prefetch(st: BotState):
    if not st.device or get_cached_offers('active') is not None:
        return
    device = st.device
    def _fetch():
        for ltype in ('active', 'ongoing', 'completed'):
            try:
                set_cached_offers(ltype, RupiyoAPI.get_all_offers(device, ltype))
            except Exception:
                pass
    threading.Thread(target=_fetch, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def run_sync(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

async def safe_edit(query, text, markup=None, pm=ParseMode.HTML):
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=pm, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning(f"safe_edit error: {e}")

def trunc(s, n=38):
    return s if len(s) <= n else s[:n-1] + "…"

async def update_commands(context, sessions, uid: Optional[int] = None):
    cmds = [BotCommand("menu", "📋 Main menu / Account list")]
    for i, s in enumerate(sessions, 1):
        cmds.append(BotCommand(str(i), f"Login as {s.get('user_name','Account')} ({s.get('masked_phone','****')})"))
    if uid and is_admin(uid):
        cmds.append(BotCommand("admin", "🛡️ Admin Panel"))
    try:
        await context.bot.set_my_commands(cmds)
    except Exception as e:
        log.warning(f"set_my_commands failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════════════════════════════════════

def mk_login_keyboard(sessions):
    rows = []
    for i, s in enumerate(sessions):
        icon  = "✅" if s['status'] == 'complete' else "🔄" if s['status'] == 'recoverable' else "❌"
        rows.append([InlineKeyboardButton(f"{icon} {s['user_name']}  ·  {s['masked_phone']}  ({s['timestamp']})", callback_data=f"acc:{i}")])
    rows.append([InlineKeyboardButton("➕  Add Account", callback_data="new_acct")])
    return InlineKeyboardMarkup(rows)

def mk_dashboard_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍  Search Offers",    callback_data="menu:search"),
         InlineKeyboardButton("⏳  Ongoing Offers",   callback_data="menu:ongoing")],
        [InlineKeyboardButton("✅  Completed Offers", callback_data="menu:completed"),
         InlineKeyboardButton("💸  Withdraw",         callback_data="menu:withdraw")],
        [InlineKeyboardButton("🔄  Refresh Balance",  callback_data="refresh_bal"),
         InlineKeyboardButton("🔙  Switch Account",   callback_data="back_login")],
    ])

def mk_offer_list_keyboard(st):
    total = len(st.offers)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    start = (st.page - 1) * PER_PAGE
    end   = min(start + PER_PAGE, total)
    rows  = []
    for i, o in enumerate(st.offers[start:end]):
        rows.append([InlineKeyboardButton(f"📌  {trunc(o.get('title','Untitled'),32)}  ₹{o.get('payout',{}).get('amnt',0)}", callback_data=f"sel:{start+i}")])
    nav = []
    if st.page > 1:   nav.append(InlineKeyboardButton("◀", callback_data="page:prev"))
    nav.append(InlineKeyboardButton(f"  {st.page}/{pages}  ", callback_data="noop"))
    if st.page < pages: nav.append(InlineKeyboardButton("▶", callback_data="page:next"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("🔄  Refresh", callback_data="refresh_list"), InlineKeyboardButton("🏠  Dashboard", callback_data="back_dash")])
    return InlineKeyboardMarkup(rows)

def mk_offer_detail_keyboard(list_type):
    rows = []
    if list_type in ('active', 'ongoing'):
        rows.append([InlineKeyboardButton("🔗  Get Link", callback_data="get_link")])
    rows.append([InlineKeyboardButton("🔙  Back to List", callback_data="back_list"), InlineKeyboardButton("🏠  Dashboard", callback_data="back_dash")])
    return InlineKeyboardMarkup(rows)

def mk_pack_keyboard(packs):
    rows = [[InlineKeyboardButton(f"💰  ₹{p.get('payout',{}).get('amount',0)}", callback_data=f"pack:{p.get('pack_id')}:{p.get('payout',{}).get('amount',0)}")] for p in packs]
    rows.append([InlineKeyboardButton("🔙  Dashboard", callback_data="back_dash")])
    return InlineKeyboardMarkup(rows)

def mk_confirm_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅  Confirm", callback_data="confirm:yes"), InlineKeyboardButton("❌  Cancel", callback_data="confirm:no")]])

def mk_back_dash():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠  Dashboard", callback_data="back_dash")]])

def mk_back_list():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back to List", callback_data="back_list"), InlineKeyboardButton("🏠  Dashboard", callback_data="back_dash")]])


# ── Admin Panel Keyboards ─────────────────────────────────────────────────────

def mk_admin_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥  All Users",          callback_data="adm:users")],
        [InlineKeyboardButton("📊  Bot Statistics",     callback_data="adm:stats")],
        [InlineKeyboardButton("📢  Broadcast Message",  callback_data="adm:broadcast")],
        [InlineKeyboardButton("🆔  Admin IDs List",     callback_data="adm:admins")],
        [InlineKeyboardButton("❌  Close Panel",        callback_data="adm:close")],
    ])

def mk_admin_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Admin Panel", callback_data="adm:back")]])


# ═══════════════════════════════════════════════════════════════════════════════
#  TEXT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_login_text(sessions, name=""):
    greeting = f"👋 Welcome back, <b>{name}</b>!" if name else "🚀 <b>RUPIYO</b>"
    return f"{greeting}\n\nChoose an option to continue:" if sessions else f"{greeting}\n\nNo saved accounts yet. Add one to get started."

def build_dashboard_text(device):
    uid = f"<code>{device.user_id}</code>" if device.user_id else "—"
    return (f"🎯 <b>RUPIYO DASHBOARD</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤  Hello! <b>{device.user_name}</b>\n"
            f"💰  Balance: <b>{Utils.format_currency(device.balance)}</b>\n"
            f"🆔  User ID: {uid}\n━━━━━━━━━━━━━━━━━━━━━━━━")

def build_offer_list_text(st):
    total = len(st.offers)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return f"<b>{st.list_title}</b>\nPage {st.page}/{pages}  ·  {total} offer{'s' if total != 1 else ''} total\n\nTap an offer to view details:"

def build_admin_stats_text() -> str:
    total_users    = len(_user_states)
    active_users   = sum(1 for st in _user_states.values() if st.device and is_session_alive(st))
    logged_in      = sum(1 for st in _user_states.values() if st.device)
    cache_active   = "✅ Cached" if get_cached_offers('active') is not None else "❌ Empty"
    cache_ongoing  = "✅ Cached" if get_cached_offers('ongoing') is not None else "❌ Empty"
    cache_complete = "✅ Cached" if get_cached_offers('completed') is not None else "❌ Empty"
    return (
        "📊 <b>Bot Statistics</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥  Total sessions loaded: <b>{total_users}</b>\n"
        f"🔐  Users logged in: <b>{logged_in}</b>\n"
        f"🟢  Active (not timed out): <b>{active_users}</b>\n"
        f"🛡️  Admin count: <b>{len(ADMIN_IDS)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Offer Cache:</b>\n"
        f"  Active:    {cache_active}\n"
        f"  Ongoing:   {cache_ongoing}\n"
        f"  Completed: {cache_complete}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_admin_users_text() -> str:
    if not _user_states:
        return "👥 <b>No active users</b> in this session."
    lines = ["👥 <b>Active User Sessions</b>\n━━━━━━━━━━━━━━━━━━━━━━━━"]
    for uid, st in list(_user_states.items()):
        admin_tag = " 🛡️" if is_admin(uid) else ""
        if st.device:
            alive = "🟢" if is_session_alive(st) else "🔴"
            lines.append(
                f"{alive} <b>{st.device.user_name}</b>{admin_tag}\n"
                f"   TG ID: <code>{uid}</code>\n"
                f"   Phone: {Utils.mask_phone(st.device.user_phone or '')}\n"
                f"   Balance: {Utils.format_currency(st.device.balance)}"
            )
        else:
            lines.append(f"⚪ <i>No device</i>{admin_tag}  TG: <code>{uid}</code>")
    return "\n\n".join(lines)

def build_admin_id_list_text() -> str:
    if not ADMIN_IDS:
        return "🛡️ <b>No admins configured.</b>\n\nSet ADMIN_IDS in your .env file."
    lines = ["🛡️ <b>Configured Admin IDs</b>\n━━━━━━━━━━━━━━━━━━━━━━━━"]
    for aid in sorted(ADMIN_IDS):
        lines.append(f"  • <code>{aid}</code>")
    lines.append("\n<i>To add/remove admins, update ADMIN_IDS in your .env and restart.</i>")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  SHOW DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

async def show_dashboard(query, context, device, edit=True):
    uid = query.from_user.id
    st  = get_state(uid)
    if device.user_id:
        _tg_uid_map[device.user_id] = uid
    await run_sync(RupiyoAPI.refresh_wallet, device)
    start_prefetch(st)
    if edit:
        await safe_edit(query, build_dashboard_text(device), mk_dashboard_keyboard())
    else:
        await query.message.reply_text(build_dashboard_text(device), reply_markup=mk_dashboard_keyboard(), parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════════════════════════════════════════
#  ADMIN PANEL HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_admin(update, context):
    """Entry point for /admin command — admin-only."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("🚫 <b>Access Denied.</b>\n\nThis panel is only available to administrators.", parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(
        "🛡️ <b>ADMIN PANEL</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\nWelcome, Administrator.\nSelect an option below:",
        reply_markup=mk_admin_main_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def cb_admin(update, context):
    """Handles all admin:* callback buttons."""
    query  = update.callback_query
    await query.answer()
    uid    = update.effective_user.id

    if not is_admin(uid):
        await query.answer("🚫 Access Denied.", show_alert=True)
        return

    action = query.data.split(':')[1]

    if action == 'back':
        await safe_edit(
            query,
            "🛡️ <b>ADMIN PANEL</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\nWelcome, Administrator.\nSelect an option below:",
            markup=mk_admin_main_keyboard(),
        )

    elif action == 'close':
        try:
            await query.message.delete()
        except Exception:
            await safe_edit(query, "✅ Admin panel closed.")

    elif action == 'stats':
        await safe_edit(query, build_admin_stats_text(), markup=mk_admin_back())

    elif action == 'users':
        text = build_admin_users_text()
        # Telegram message limit safety
        if len(text) > 4000:
            text = text[:3990] + "\n…<i>(truncated)</i>"
        await safe_edit(query, text, markup=mk_admin_back())

    elif action == 'admins':
        await safe_edit(query, build_admin_id_list_text(), markup=mk_admin_back())

    elif action == 'broadcast':
        st = get_state(uid)
        st.admin_awaiting = 'broadcast'
        await safe_edit(
            query,
            "📢 <b>Broadcast Message</b>\n\nSend the message you want to broadcast to all active bot users.\n\n<i>Send /cancel to abort.</i>",
            markup=mk_admin_back(),
        )

    elif action == 'broadcast_confirm':
        # Triggered after preview — send the broadcast
        msg_text = context.user_data.get('broadcast_text', '')
        if not msg_text:
            await safe_edit(query, "❌ No message to broadcast.", markup=mk_admin_back()); return
        sent, failed = 0, 0
        for tg_uid in list(_user_states.keys()):
            try:
                await context.bot.send_message(tg_uid, f"📢 <b>Admin Broadcast</b>\n\n{msg_text}", parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
        context.user_data.pop('broadcast_text', None)
        await safe_edit(query, f"✅ <b>Broadcast Sent</b>\n\n✔ Delivered: <b>{sent}</b>\n✖ Failed: <b>{failed}</b>", markup=mk_admin_back())

    elif action == 'broadcast_cancel':
        context.user_data.pop('broadcast_text', None)
        st = get_state(uid)
        st.admin_awaiting = None
        await safe_edit(
            query,
            "🛡️ <b>ADMIN PANEL</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\nBroadcast cancelled.\nSelect an option below:",
            markup=mk_admin_main_keyboard(),
        )


async def _handle_admin_broadcast_input(update, context, st, text):
    """Called from on_text when admin is in broadcast-input mode."""
    if text.strip().lower() == '/cancel':
        st.admin_awaiting = None
        await update.message.reply_text("❌ Broadcast cancelled.")
        return
    context.user_data['broadcast_text'] = text
    st.admin_awaiting = None
    preview_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅  Send to All Users", callback_data="adm:broadcast_confirm")],
        [InlineKeyboardButton("❌  Cancel",            callback_data="adm:broadcast_cancel")],
    ])
    await update.message.reply_text(
        f"📢 <b>Preview</b>\n\n{text}\n\n━━━━━━━━━━━━━━━━━━━━━━━━\nSend this to <b>{len(_user_states)}</b> user(s)?",
        reply_markup=preview_kb,
        parse_mode=ParseMode.HTML,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  REGULAR CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_select_account(update, context):
    query    = update.callback_query
    await query.answer()
    uid      = update.effective_user.id
    st       = get_state(uid)
    sessions = context.user_data.get('sessions') or await run_sync(SessionManager.find_saved_sessions)
    idx      = int(query.data.split(':')[1])
    if idx >= len(sessions):
        await safe_edit(query, "❌ Session not found. Send /start to refresh.")
        return
    sess = sessions[idx]
    await safe_edit(query, f"🔄 Logging in as <b>{sess['user_name']}</b>…", pm=ParseMode.HTML)
    device, status = await run_sync(_perform_relogin, sess)
    if status.startswith("banned:"):
        await safe_edit(query, friendly_error(status[7:]), pm=ParseMode.HTML)
        return
    if status != "ok" or not device:
        await safe_edit(query, "❌ Login failed. Send /start to try again.")
        return
    st.device       = device
    st.session_file = sess['file']
    st.awaiting     = None
    await show_dashboard(query, context, device, edit=True)

async def cb_new_account(update, context):
    query = update.callback_query
    await query.answer()
    get_state(update.effective_user.id).awaiting = 'phone'
    await safe_edit(query, "🆕 <b>Add Account</b>\n\n📞 Send your phone number (10 digits, without country code):", pm=ParseMode.HTML)

async def on_text(update, context):
    uid  = update.effective_user.id
    st   = get_state(uid)
    text = update.message.text.strip()
    touch(st)
    # Admin broadcast input takes priority
    if st.admin_awaiting == 'broadcast' and is_admin(uid):
        await _handle_admin_broadcast_input(update, context, st, text)
        return
    if   st.awaiting == 'phone':    await _handle_phone(update, context, st, text)
    elif st.awaiting == 'otp':      await _handle_otp(update, context, st, text)
    elif st.awaiting == 'name':     await _handle_name(update, context, st, text)
    elif st.awaiting == 'referral': await _handle_referral(update, context, st, text)
    elif st.awaiting == 'keyword':  await _handle_keyword(update, context, st, text)
    elif st.awaiting == 'upi':      await _handle_upi(update, context, st, text)

async def _handle_phone(update, context, st, text):
    phone = text
    if phone.startswith('+91'):   phone = phone[3:]
    elif phone.startswith('91') and len(phone) == 12: phone = phone[2:]
    if not phone.isdigit() or len(phone) != 10:
        await update.message.reply_text("❌ Invalid phone number. Send 10 digits (without country code):"); return
    st.phone = phone
    device   = DeviceIdentity()
    device.user_phone = phone
    st.device = device
    def setup():
        v2_id    = ProfileManager.init_v2_profile(device) or Utils.generate_uuid()
        x_profile = Utils.generate_uuid()
        x_token  = Utils.generate_x_token()
        status, resp = RupiyoAPI.send_otp(phone, x_profile, device, x_token)
        return v2_id, x_profile, x_token, status, resp
    v2_id, x_profile, x_token, status, resp = await run_sync(setup)
    if status != 200:
        err = ""
        try: err = json.loads(resp).get("error", "")
        except Exception: pass
        await update.message.reply_text(friendly_error(err))
        st.awaiting = None; return
    st.v2_profile_id = v2_id
    st.x_profile     = x_profile
    st.x_token       = x_token
    st.awaiting      = 'otp'
    await update.message.reply_text("✅ OTP sent!\n\n🔑 Enter the 6-digit OTP:")

async def _handle_otp(update, context, st, text):
    otp = text.strip()
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text("❌ OTP must be exactly 6 digits. Try again:"); return
    device = st.device
    status, resp, is_new_user, ban_message = await run_sync(lambda: RupiyoAPI.login_with_otp(st.phone, otp, st.x_profile, device, st.v2_profile_id, st.x_token))
    if status != 200:
        err = ban_message or ""
        try:
            if not err: err = json.loads(resp).get("error", "")
        except Exception: pass
        await update.message.reply_text(friendly_error(err))
        st.awaiting = None; return
    if is_new_user:
        st.otp = otp; st.awaiting = 'name'
        await update.message.reply_text("📝 <b>New account!</b>\n\n👤 Enter your full name:", parse_mode=ParseMode.HTML); return
    await _post_login_setup(update, context, st, device)

async def _handle_name(update, context, st, text):
    st.full_name = text.strip() or "User"
    st.awaiting  = 'referral'
    await update.message.reply_text("🎫 Enter referral code (or send <b>–</b> to skip):", parse_mode=ParseMode.HTML)

async def _handle_referral(update, context, st, text):
    referral = '' if text.strip() in ('-', '–', '—') or not text.strip() else text.strip()
    device   = st.device
    status, resp, ban_message = await run_sync(lambda: RupiyoAPI.signup_new_user(device, st.phone, st.otp, st.full_name, referral, st.x_profile, st.v2_profile_id, st.x_token))
    if status != 200:
        err = ban_message or ""
        try:
            if not err: err = json.loads(resp).get("error", "")
        except Exception: pass
        await update.message.reply_text(friendly_error(err))
        st.awaiting = None; return
    await _post_login_setup(update, context, st, device)

async def _post_login_setup(update, context, st, device):
    uid_for_save = update.effective_user.id
    def setup():
        ProfileManager.init_v1_profile(device)
        ProfileManager.sync_telemetry(device)
        RupiyoAPI.update_fcm_token(device)
        RupiyoAPI.refresh_wallet(device)
        return SessionManager.save_session(device, tg_uid=uid_for_save)
    session_file    = await run_sync(setup)
    st.device       = device
    st.session_file = session_file
    st.awaiting     = None
    if device.user_id:
        _tg_uid_map[device.user_id] = update.effective_user.id
    start_prefetch(st)
    fresh_sessions = await run_sync(SessionManager.find_saved_sessions, update.effective_user.id)
    await update_commands(context, fresh_sessions, uid=update.effective_user.id)
    await update.message.reply_text(f"✅ Welcome, <b>{device.user_name}</b>!\n\n{build_dashboard_text(device)}", reply_markup=mk_dashboard_keyboard(), parse_mode=ParseMode.HTML)

async def cb_refresh_balance(update, context):
    query = update.callback_query
    await query.answer("🔄 Refreshing…")
    st    = get_state(update.effective_user.id)
    if st.device:
        await show_dashboard(query, context, st.device, edit=True)

async def cb_menu(update, context):
    query  = update.callback_query
    await query.answer()
    uid    = update.effective_user.id
    st     = get_state(uid)
    action = query.data.split(':')[1]
    if not st.device:
        await safe_edit(query, "❌ Session expired. Use /menu to start a new session."); return
    touch(st)
    if action == 'search':
        st.awaiting = 'keyword'
        await safe_edit(query, "🔍 <b>Search Offers</b>\n\nSend a keyword to search:", pm=ParseMode.HTML)
    elif action in ('ongoing', 'completed'):
        label  = "⏳ Ongoing Offers" if action == 'ongoing' else "✅ Completed Offers"
        offers = get_cached_offers(action)
        if offers is None:
            await safe_edit(query, f"<b>{label}</b>\n\n📡 Fetching…", pm=ParseMode.HTML)
            offers = await run_sync(RupiyoAPI.get_all_offers, st.device, action)
            if offers is not None: set_cached_offers(action, offers)
        if not offers:
            await safe_edit(query, f"📭 No {action} offers found.", markup=mk_back_dash(), pm=ParseMode.HTML); return
        st.offers = offers; st.list_type = action; st.list_title = label; st.page = 1
        await safe_edit(query, build_offer_list_text(st), markup=mk_offer_list_keyboard(st), pm=ParseMode.HTML)
    elif action == 'withdraw':
        await _show_withdraw(query, st)

async def _handle_keyword(update, context, st, text):
    if not text.strip():
        await update.message.reply_text("❌ No keyword entered. Try again:"); return
    st.awaiting = None
    keyword     = text.strip()
    await update.message.reply_text(f"🔍 Searching for <b>{keyword}</b>…", parse_mode=ParseMode.HTML)
    all_active = get_cached_offers('active')
    if all_active is None:
        await update.message.reply_text("📡 Loading offers…")
        all_active = await run_sync(RupiyoAPI.get_all_offers, st.device, 'active')
        if all_active: set_cached_offers('active', all_active)
    filtered = [o for o in (all_active or []) if keyword.lower() in o.get('title', '').lower()]
    if not filtered:
        await update.message.reply_text(f"📭 No offers found for <b>{keyword}</b>", reply_markup=mk_back_dash(), parse_mode=ParseMode.HTML); return
    st.offers = filtered; st.list_type = 'active'; st.list_title = f"🔍 Search: '{keyword}'"; st.page = 1
    await update.message.reply_text(build_offer_list_text(st), reply_markup=mk_offer_list_keyboard(st), parse_mode=ParseMode.HTML)

async def cb_page(update, context):
    query  = update.callback_query
    await query.answer()
    st     = get_state(update.effective_user.id)
    touch(st)
    action = query.data.split(':')[1]
    pages  = max(1, (len(st.offers) + PER_PAGE - 1) // PER_PAGE)
    if action == 'prev' and st.page > 1:   st.page -= 1
    elif action == 'next' and st.page < pages: st.page += 1
    await safe_edit(query, build_offer_list_text(st), markup=mk_offer_list_keyboard(st), pm=ParseMode.HTML)

async def cb_refresh_list(update, context):
    query = update.callback_query
    await query.answer("🔄 Refreshing…")
    st    = get_state(update.effective_user.id)
    await safe_edit(query, "📡 Refreshing…", pm=ParseMode.HTML)
    if 'Search:' in st.list_title and "'" in st.list_title:
        keyword    = st.list_title.split("'")[1]
        all_active = await run_sync(RupiyoAPI.get_all_offers, st.device, 'active')
        if all_active: set_cached_offers('active', all_active)
        fresh = [o for o in (all_active or []) if keyword.lower() in o.get('title', '').lower()]
    else:
        fresh = await run_sync(RupiyoAPI.get_all_offers, st.device, st.list_type)
        if fresh: set_cached_offers(st.list_type, fresh)
    if not fresh:
        await safe_edit(query, f"📭 No {st.list_type} offers found.", markup=mk_back_dash(), pm=ParseMode.HTML); return
    st.offers = fresh
    st.page   = min(st.page, max(1, (len(fresh) + PER_PAGE - 1) // PER_PAGE))
    await safe_edit(query, build_offer_list_text(st), markup=mk_offer_list_keyboard(st), pm=ParseMode.HTML)

async def cb_select_offer(update, context):
    query = update.callback_query
    await query.answer()
    st    = get_state(update.effective_user.id)
    touch(st)
    idx   = int(query.data.split(':')[1])
    if idx >= len(st.offers):
        await safe_edit(query, "❌ Offer not found. Refresh the list.", markup=mk_back_dash()); return
    offer            = st.offers[idx]
    st.current_offer = offer
    text = (f"📌 <b>{offer.get('title','Untitled')}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰  Reward: <b>{Utils.format_currency(offer.get('payout',{}).get('amnt',0))}</b>\n"
            f"🆔  ID: <code>{offer.get('oid','N/A')}</code>\n"
            f"📊  Type: {offer.get('type','N/A')}\n"
            f"👍  Upvotes: {offer.get('metrics',{}).get('upvotes',0)}\n━━━━━━━━━━━━━━━━━━━━━━━━")
    await safe_edit(query, text, markup=mk_offer_detail_keyboard(st.list_type), pm=ParseMode.HTML)

async def cb_get_link(update, context):
    query = update.callback_query
    await query.answer("🔗 Fetching link…")
    st    = get_state(update.effective_user.id)
    touch(st)
    if not st.current_offer:
        await safe_edit(query, "❌ No offer selected.", markup=mk_back_dash()); return
    offer       = st.current_offer
    offer_id    = offer.get('oid')
    offer_title = offer.get('title', '')
    await safe_edit(query, f"🔄 Getting link for <b>{trunc(offer_title, 40)}</b>…", pm=ParseMode.HTML)
    def fetch_link():
        details = RupiyoAPI.get_offer_details(st.device, offer_id)
        if not details: return None, "Failed to get offer details", []
        rewards = details.get('postback_reward', [])
        if not rewards: return None, "No rewards found", []
        reward_id = rewards[0].get('reward_id')
        ok, url   = RupiyoAPI.get_reward_cta(st.device, reward_id)
        if not ok: return None, url, []
        if (urlparse(url).hostname or '').endswith('epicplay.in'): return None, "EXPIRED", []
        if st.list_type == 'active':
            RupiyoAPI.send_ongoing_signal(st.device, offer_id, reward_id)
        chain = []; final_url = unquote(url)
        try:
            resp = requests.get(url, timeout=10, allow_redirects=True)
            for i, r in enumerate(resp.history):
                chain.append({'step': i + 1, 'url': unquote(r.url)})
            final_url = unquote(resp.url)
            while chain and ('play.google.com' in chain[-1]['url'] or chain[-1]['url'].startswith('market://')):
                chain = chain[:-1]
            if 'play.google.com' in final_url or final_url.startswith('market://'):
                final_url = chain[-1]['url'] if chain else unquote(url)
        except Exception: pass
        return final_url, None, chain
    final_url, err, chain = await run_sync(fetch_link)
    if final_url is None:
        msg = "⚠️ <b>This Offer Is Expired</b>" if err == "EXPIRED" else f"❌ {err}"
        await safe_edit(query, msg, markup=mk_offer_detail_keyboard(st.list_type), pm=ParseMode.HTML); return
    steps_lines = "".join(f"  ↪️ Step {s['step']}: <code>{trunc(s['url'],60)}</code>\n" for s in chain)
    text = f"🔗 <b>Offer Link</b>\n📌 {trunc(offer_title,40)}\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    if steps_lines: text += f"🔄 Redirect chain:\n{steps_lines}\n"
    text += f'✅ <b>Final URL:</b>\n<a href="{final_url}">{trunc(final_url,55)}</a>'
    await safe_edit(query, text, markup=mk_back_list(), pm=ParseMode.HTML)

async def _show_withdraw(query, st):
    await safe_edit(query, "💸 <b>Withdrawal</b>\n\n📡 Checking store…", pm=ParseMode.HTML)
    def fetch():
        store = RupiyoAPI.get_payout_store_info(st.device)
        RupiyoAPI.refresh_wallet(st.device)
        return store, RupiyoAPI.get_payout_packs(st.device)
    store, packs = await run_sync(fetch)
    if not store:
        await safe_edit(query, "❌ Failed to fetch store info.", markup=mk_back_dash(), pm=ParseMode.HTML); return
    status  = store.get('store_status', {}).get('status', 'UNKNOWN')
    unlocks = store.get('store_status', {}).get('unlocks_in_sec', 0)
    if status != 'OPEN':
        msg = f"⏰ <b>Store is {status}</b>\n\n"
        if unlocks > 0:
            now = Utils.get_ist_time()
            msg += f"Opens in: <b>{Utils.format_time_remaining(unlocks)}</b>\nat {(now + timedelta(seconds=unlocks)).strftime('%I:%M %p IST')}"
        await safe_edit(query, msg, markup=mk_back_dash(), pm=ParseMode.HTML); return
    if not packs:
        await safe_edit(query, "❌ No withdrawal options available.", markup=mk_back_dash(), pm=ParseMode.HTML); return
    st.packs = packs
    await safe_edit(query, f"💸 <b>Withdrawal</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n💰  Balance: <b>{Utils.format_currency(st.device.balance)}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\nSelect withdrawal amount:", markup=mk_pack_keyboard(packs), pm=ParseMode.HTML)

async def cb_select_pack(update, context):
    query   = update.callback_query
    await query.answer()
    st      = get_state(update.effective_user.id)
    touch(st)
    parts   = query.data.split(':')
    pack_id = int(parts[1])
    amount  = float(parts[2])
    selected = next((p for p in st.packs if p.get('pack_id') == pack_id), {'pack_id': pack_id, 'payout': {'amount': amount}})
    if st.device.balance < amount:
        await safe_edit(query, f"❌ <b>Not Enough Balance</b>\n\nHave: <b>{Utils.format_currency(st.device.balance)}</b>\nNeed: ₹{amount}", markup=mk_back_dash(), pm=ParseMode.HTML); return
    st.selected_pack = selected
    st.awaiting      = 'upi'
    await safe_edit(query, f"💸 Withdrawing <b>₹{amount}</b>\n\n📱 Send your UPI ID:\n<i>e.g. name@okhdfcbank</i>", pm=ParseMode.HTML)

async def _handle_upi(update, context, st, text):
    upi     = text.strip().lower().replace(" ", "")
    ok, msg = Utils.validate_upi_id(upi)
    if not ok:
        await update.message.reply_text(f"❌ {msg}\n\nTry again:"); return
    amount              = st.selected_pack.get('payout', {}).get('amount', 0)
    st.awaiting         = None
    context.user_data['upi'] = upi
    await update.message.reply_text(f"📋 <b>Withdrawal Summary</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n💰  Amount: <b>₹{amount}</b>\n📱  UPI: <code>{upi}</code>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\nConfirm withdrawal?", reply_markup=mk_confirm_keyboard(), parse_mode=ParseMode.HTML)

async def cb_confirm(update, context):
    query  = update.callback_query
    await query.answer()
    st     = get_state(update.effective_user.id)
    touch(st)
    action = query.data.split(':')[1]
    if action == 'no':
        await show_dashboard(query, context, st.device, edit=True); return
    pack    = st.selected_pack
    pack_id = pack.get('pack_id')
    amount  = pack.get('payout', {}).get('amount', 0)
    upi     = context.user_data.get('upi', '')
    await safe_edit(query, "🔄 Processing withdrawal…", pm=ParseMode.HTML)
    ok, result = await run_sync(RupiyoAPI.purchase_payout, st.device, pack_id, upi)
    if ok:
        await run_sync(RupiyoAPI.refresh_wallet, st.device)
        await safe_edit(query, f"✅ <b>Withdrawal Successful!</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n💰  ₹{amount} → <code>{upi}</code>\n💳  New Balance: <b>{Utils.format_currency(st.device.balance)}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━", markup=mk_back_dash(), pm=ParseMode.HTML)
    else:
        await safe_edit(query, f"❌ <b>Withdrawal Failed</b>\n\n<code>{result[:300]}</code>", markup=mk_back_dash(), pm=ParseMode.HTML)

async def cb_back_login(update, context):
    query    = update.callback_query
    await query.answer()
    uid      = update.effective_user.id
    st       = get_state(uid)
    st.awaiting = None
    sessions = await run_sync(SessionManager.find_saved_sessions, update.effective_user.id)
    context.user_data['sessions'] = sessions
    await safe_edit(query, build_login_text(sessions, st.device.user_name if st.device else ""), markup=mk_login_keyboard(sessions), pm=ParseMode.HTML)

async def cb_back_dash(update, context):
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    st    = get_state(uid)
    st.awaiting = None
    if not is_session_alive(st):
        sessions     = await run_sync(SessionManager.find_saved_sessions)
        phone_to_idx = {s['phone']: i for i, s in enumerate(sessions, 1)}
        phone        = st.device.user_phone if st.device else ""
        idx          = phone_to_idx.get(phone)
        cmd          = f"/{idx}" if idx else "/menu"
        name         = st.device.user_name if st.device else "your account"
        _user_states.pop(uid, None)
        await safe_edit(query, f"🔒 <b>Session Expired</b>\n\nYour session for <b>{name}</b> has expired due to inactivity.\n\nUse <b>{cmd}</b> to start a new session.", pm=ParseMode.HTML); return
    touch(st)
    await show_dashboard(query, context, st.device, edit=True)

async def cb_back_list(update, context):
    query = update.callback_query
    await query.answer()
    st    = get_state(update.effective_user.id)
    if not st.offers:
        await show_dashboard(query, context, st.device, edit=True); return
    await safe_edit(query, build_offer_list_text(st), markup=mk_offer_list_keyboard(st), pm=ParseMode.HTML)

async def cb_noop(update, context):
    await update.callback_query.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  RELOGIN + COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

def _perform_relogin(sess):
    if sess['file'].startswith('remote:'):
        data = sess['data']
    else:
        with open(sess['file'], 'r') as f:
            data = json.load(f)
    device = DeviceIdentity()
    device.load_from_session(data)
    if not device.refresh_token:
        return None, "no_refresh"
    if not Utils.is_token_valid(device.auth_token):
        ok, result, ban_message = RupiyoAPI.refresh_token(device.refresh_token)
        if not ok:
            if ban_message:
                SessionManager.delete_session(sess['file'])
                return None, f"banned:{ban_message}"
            if device.needs_healing():
                if SelfHealer.heal_session(device, sess['file']):
                    return device, "ok"
            return None, "failed"
        device.auth_token = result['access_token']
        if result['refresh_token'] != device.refresh_token:
            device.refresh_token = result['refresh_token']
    ProfileManager.sync_telemetry(device)
    RupiyoAPI.update_fcm_token(device)
    RupiyoAPI.refresh_wallet(device)
    SessionManager.update_session(sess['file'], device)
    def _bg():
        if device.needs_healing(): SelfHealer.heal_session(device, sess['file'])
        else: SelfHealer.sync_profile_fields(device, sess['file'])
    threading.Thread(target=_bg, daemon=True).start()
    return device, "ok"

async def cmd_menu(update, context):
    uid      = update.effective_user.id
    st       = get_state(uid)
    st.awaiting = None
    sessions = await run_sync(SessionManager.find_saved_sessions, update.effective_user.id)
    context.user_data['sessions'] = sessions
    await update_commands(context, sessions, uid=uid)
    await update.message.reply_text(build_login_text(sessions, st.device.user_name if st.device else ""), reply_markup=mk_login_keyboard(sessions), parse_mode=ParseMode.HTML)

async def cmd_login_n(update, context):
    uid  = update.effective_user.id
    st   = get_state(uid)
    cmd  = update.message.text.strip().lstrip('/')
    if not cmd.isdigit(): return
    n    = int(cmd)
    sessions = await run_sync(SessionManager.find_saved_sessions, update.effective_user.id)
    if n < 1 or n > len(sessions):
        await update.message.reply_text(f"❌ No account #{n}. You have {len(sessions)} saved account(s)."); return
    sess = sessions[n - 1]
    await update.message.reply_text(f"🔄 Logging in as <b>{sess['user_name']}</b>…", parse_mode=ParseMode.HTML)
    device, status = await run_sync(_perform_relogin, sess)
    if status.startswith("banned:"):
        await update.message.reply_text(friendly_error(status[7:])); return
    if status != "ok" or not device:
        await update.message.reply_text("❌ Login failed. Try /menu."); return
    st.device = device; st.session_file = sess['file']; st.awaiting = None
    start_prefetch(st)
    await update.message.reply_text(f"✅ Logged in!\n\n{build_dashboard_text(device)}", reply_markup=mk_dashboard_keyboard(), parse_mode=ParseMode.HTML)

async def global_cache_refresh_job(context):
    device = _get_any_active_device()
    if not device: return
    def _refresh():
        for ltype in ('active', 'ongoing', 'completed'):
            try: set_cached_offers(ltype, RupiyoAPI.get_all_offers(device, ltype))
            except Exception: pass
    threading.Thread(target=_refresh, daemon=True).start()

async def session_cleanup_job(context):
    sessions_on_disk = SessionManager.find_saved_sessions()
    phone_to_index   = {s['phone']: i for i, s in enumerate(sessions_on_disk, 1)}
    for uid, st in list(_user_states.items()):
        if not st.device: continue
        if (time.time() - st.last_activity) < st.inactivity_timeout: continue
        name  = st.device.user_name or "your account"
        phone = st.device.user_phone or ""
        idx   = phone_to_index.get(phone)
        cmd   = f"/{idx}" if idx else "/menu"
        _user_states.pop(uid, None)
        try:
            await context.bot.send_message(uid, f"🔒 <b>Session Expired</b>\n\nYour session for <b>{name}</b> has expired due to inactivity.\n\nUse <b>{cmd}</b> to start a new session.", parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning(f"Could not notify uid={uid}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    missing = [v for v in ("BOT_TOKEN", "JSONBIN_MASTER_KEY", "JSONBIN_INDEX_BIN") if not os.environ.get(v)]
    if missing:
        log.error(f"❌ Missing required variables in .env: {', '.join(missing)}")
        log.error("Create a .env file with BOT_TOKEN, JSONBIN_MASTER_KEY, JSONBIN_INDEX_BIN")
        return

    if not ADMIN_IDS:
        log.warning("⚠️  No ADMIN_IDS configured. Admin panel will be inaccessible. Set ADMIN_IDS in .env.")
    else:
        log.info(f"🛡️  Admin IDs loaded: {sorted(ADMIN_IDS)}")

    Config.initialize()

    # Start Render health-check server
    _start_health_server()

    async def post_init(app):
        sessions = SessionManager.find_saved_sessions()
        cmds     = [BotCommand("menu", "📋 Main menu / Account list")]
        for i, s in enumerate(sessions, 1):
            cmds.append(BotCommand(str(i), f"Login as {s.get('user_name','Account')} ({s.get('masked_phone','****')})"))
        await app.bot.set_my_commands(cmds)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.job_queue.run_repeating(session_cleanup_job,      interval=60,  first=30)
    app.job_queue.run_repeating(global_cache_refresh_job, interval=300, first=90)

    # Regular commands
    app.add_handler(CommandHandler("start", cmd_menu))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(CommandHandler("admin", cmd_admin))
    for _i in range(1, 21):
        app.add_handler(CommandHandler(str(_i), cmd_login_n))

    # Admin callbacks (must come before generic handlers)
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^adm:"))

    # Regular callbacks
    app.add_handler(CallbackQueryHandler(cb_select_account,  pattern=r"^acc:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_new_account,     pattern=r"^new_acct$"))
    app.add_handler(CallbackQueryHandler(cb_refresh_balance, pattern=r"^refresh_bal$"))
    app.add_handler(CallbackQueryHandler(cb_menu,            pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(cb_back_login,      pattern=r"^back_login$"))
    app.add_handler(CallbackQueryHandler(cb_page,            pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(cb_refresh_list,    pattern=r"^refresh_list$"))
    app.add_handler(CallbackQueryHandler(cb_select_offer,    pattern=r"^sel:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_get_link,        pattern=r"^get_link$"))
    app.add_handler(CallbackQueryHandler(cb_back_list,       pattern=r"^back_list$"))
    app.add_handler(CallbackQueryHandler(cb_select_pack,     pattern=r"^pack:"))
    app.add_handler(CallbackQueryHandler(cb_confirm,         pattern=r"^confirm:"))
    app.add_handler(CallbackQueryHandler(cb_back_dash,       pattern=r"^back_dash$"))
    app.add_handler(CallbackQueryHandler(cb_noop,            pattern=r"^noop$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("🚀 Rupiyo Bot started. Polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
