#!/usr/bin/env python3
"""
Rupiyo Telegram Bot
────────────────────────────────────────────────────────────
Usage:
    export BOT_TOKEN="YOUR_BOT_TOKEN"
    export JSONBIN_MASTER_KEY="your_jsonbin_master_key"
    export JSONBIN_INDEX_BIN="<index_bin_id>"
    python3 rupiyo_bot.py

Install:
    pip install "python-telegram-bot==20.7" pycryptodome requests

JSONBin setup (one-time):
    1. Create account at https://jsonbin.io
    2. Copy your Master Key from Account > API Keys
    3. Create one empty bin manually (or let the bot auto-create it on first run)
       with content: {}  — this becomes JSONBIN_INDEX_BIN
    4. Set both env vars and start the bot
"""

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
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

import signal
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

log = logging.getLogger(__name__)

DEBUG     = os.environ.get("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    format="%(asctime)s  %(levelname).1s  %(message)s",
    datefmt="%H:%M:%S",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# ── ANSI helpers ─────────────────────────────────────────────────────────────
_ANSI = {
    "reset":  "\033[0m",  "bold":   "\033[1m",  "dim":    "\033[2m",
    "purple": "\033[35m", "cyan":   "\033[36m",  "green":  "\033[32m",
    "yellow": "\033[33m", "white":  "\033[97m",
}

def _c(color: str, text: str) -> str:
    return f"{_ANSI.get(color, '')}{text}{_ANSI['reset']}"

def print_banner(port: int, jsonbin: bool) -> None:
    """Compact colored startup banner printed directly to stdout (no log prefix)."""
    W              = 38
    TL, TR, BL, BR = "\u2554", "\u2557", "\u255a", "\u255d"
    H,  V          = "\u2550", "\u2551"
    border = _c("purple", "{}{}{}")
    top    = border.format(TL, H * W, TR)
    bot    = border.format(BL, H * W, BR)
    empty  = _c("purple", V) + " " * W + _c("purple", V)

    def row(content: str) -> str:
        # strip ANSI to measure visible length
        import re as _re
        visible = _re.sub(r"\033\[[0-9;]*m", "", content)
        pad = W - len(visible) - 2
        return _c("purple", V) + "  " + content + " " * max(pad, 0) + _c("purple", V)

    storage = _c("green", "JSONBin \u2713") if jsonbin else _c("yellow", "Local files")
    print("", top, empty,
          row(_c("bold",   "\U0001f680  RUPIYO BOT") + "  " + _c("dim", "ptb 20.7")),
          empty,
          row(_c("cyan", "Port   ") + _c("white", f":{port}")),
          row(_c("cyan", "Storage") + "  " + storage),
          row(_c("cyan", "Mode   ") + _c("white", "Polling")),
          empty, bot, "",
          sep="\n", flush=True)

# ── Credentials ──────────────────────────────────────────────────────────────
BOT_TOKEN          = "8348185586:AAHDEltpiYbJcWpcOh1IEOI5c-V4-bhSnDY"
JSONBIN_MASTER_KEY = "$2a$10$qK/rwakuFj4A0fyaBQ63he4BIJUK1BExJZQXl87Ua4271u2a806oK"
JSONBIN_INDEX_BIN  = "69ae82b0ae596e708f6fac50"
PORT               = int(os.environ.get("PORT", 8080))  # Render injects PORT

# ── Admin config ─────────────────────────────────────────────────────────────
ADMIN_IDS: set = {6824349902,8050679624}           # Add more admin IDs here
_whitelist: set = set(ADMIN_IDS)        # Whitelisted users (admins always included)
_bot_users: dict = {}                   # uid → {"name": str, "username": str} all who /start'd
PER_PAGE           = 10   # offers per page
START_TIME         = time.time()                         # for /status uptime

# ═══════════════════════════════════════════════════════════════════════════════
#  ACCESS CONTROL
# ═══════════════════════════════════════════════════════════════════════════════

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def is_allowed(uid: int) -> bool:
    return uid in _whitelist

def record_user(user) -> None:
    """Track every user who interacts with the bot (in-memory + persisted)."""
    if not user:
        return
    uid      = user.id
    name     = user.full_name or "Unknown"
    username = user.username or ""
    if _bot_users.get(uid) == {"name": name, "username": username}:
        return  # no change — skip write
    _bot_users[uid] = {"name": name, "username": username}
    if JSONBinStorage.is_enabled():
        threading.Thread(
            target=JSONBinStorage.save_meta,
            args=(uid, name, username, _whitelist),
            daemon=True,
        ).start()

# ═══════════════════════════════════════════════════════════════════════════════
#  BACKEND  (identical logic from rupiyo.py CLI script)
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
            val = float(amount)
            # Show as integer if whole, otherwise 2 decimal places (no trailing zeros)
            if val == int(val):
                return f"₹{int(val)}"
            return f"₹{val:.2f}".rstrip('0').rstrip('.')
        except (ValueError, TypeError):
            return "₹0"

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

# ── Friendly error messages ──────────────────────────────────────────────────
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
    """Map a raw server error string to a user-friendly message."""
    if not raw:
        return "❌ Something went wrong. Please try again."
    lower = raw.lower()
    for key, msg in _ERROR_MAP.items():
        if key in lower:
            return msg
    return "❌ Something went wrong. Please try again."

# ═══════════════════════════════════════════════════════════════════════════════
#  JSONBIN STORAGE  (remote session store — one bin per telegram user)
# ═══════════════════════════════════════════════════════════════════════════════

class JSONBinStorage:
    BASE    = "https://api.jsonbin.io/v3"
    HEADERS = lambda: {
        "Content-Type":  "application/json",
        "X-Master-Key":  JSONBIN_MASTER_KEY,
        "X-Bin-Private": "true",
    }

    # In-process cache: verified bin IDs — no HTTP probe on the hot path
    _bin_cache:  Dict[str, str] = {}
    # Serialises all index writes to prevent concurrent read-modify-write races
    _write_lock = threading.Lock()

    # ── Index bin ─────────────────────────────────────────────────────────────
    @staticmethod
    def _read_index() -> Dict:
        if not JSONBIN_MASTER_KEY or not JSONBIN_INDEX_BIN:
            return {}
        try:
            r = requests.get(
                f"{JSONBinStorage.BASE}/b/{JSONBIN_INDEX_BIN}/latest",
                headers=JSONBinStorage.HEADERS(), timeout=10,
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
                headers=JSONBinStorage.HEADERS(),
                json=data, timeout=10,
            )
            return r.status_code == 200
        except Exception:
            return False

    # ── Per-user bin ──────────────────────────────────────────────────────────
    @staticmethod
    def _get_or_create_user_bin(tg_uid: int) -> Optional[str]:
        """Return bin id for this user.
        Uses in-process cache after first successful probe — no HTTP on hot path.
        Falls back to probing index bin, then creates a new bin only if needed.
        """
        key = str(tg_uid)

        # Fast path: already verified this session
        if key in JSONBinStorage._bin_cache:
            return JSONBinStorage._bin_cache[key]

        index = JSONBinStorage._read_index()

        if key in index:
            candidate = index[key]
            try:
                probe = requests.get(
                    f"{JSONBinStorage.BASE}/b/{candidate}/latest",
                    headers=JSONBinStorage.HEADERS(), timeout=10,
                )
                if probe.status_code == 200:
                    JSONBinStorage._bin_cache[key] = candidate
                    return candidate
                log.warning(f"JSONBin: bin {candidate} inaccessible ({probe.status_code}) — recreating")
            except Exception as e:
                log.warning(f"JSONBin: probe failed for {candidate}: {e}")

        # Create a new bin
        try:
            r = requests.post(
                f"{JSONBinStorage.BASE}/b",
                headers={**JSONBinStorage.HEADERS(), "X-Bin-Name": f"rupiyo_{tg_uid}"},
                json={"_init": True}, timeout=10,
            )
            if r.status_code == 200:
                bin_id     = r.json()["metadata"]["id"]
                index[key] = bin_id
                JSONBinStorage._write_index(index)
                JSONBinStorage._bin_cache[key] = bin_id
                log.info(f"JSONBin: created bin {bin_id} for UID {tg_uid}")
                return bin_id
        except Exception as e:
            log.warning(f"JSONBin create bin failed: {e}")
        return None

    @staticmethod
    def read_user_sessions(tg_uid: int) -> Dict:
        bin_id = JSONBinStorage._get_or_create_user_bin(tg_uid)
        if not bin_id:
            return {}
        try:
            r = requests.get(
                f"{JSONBinStorage.BASE}/b/{bin_id}/latest",
                headers=JSONBinStorage.HEADERS(), timeout=10,
            )
            return r.json().get("record", {}) if r.status_code == 200 else {}
        except Exception:
            return {}

    @staticmethod
    def write_user_sessions(tg_uid: int, sessions: Dict) -> bool:
        """Overwrite sessions for this user. Retries once on transient failure."""
        bin_id = JSONBinStorage._get_or_create_user_bin(tg_uid)
        if not bin_id:
            return False
        for attempt in range(2):
            try:
                r = requests.put(
                    f"{JSONBinStorage.BASE}/b/{bin_id}",
                    headers=JSONBinStorage.HEADERS(),
                    json=sessions, timeout=10,
                )
                if r.status_code == 200:
                    return True
                log.warning(f"JSONBin write attempt {attempt+1} failed: HTTP {r.status_code}")
            except Exception as e:
                log.warning(f"JSONBin write attempt {attempt+1} error: {e}")
        return False

    @staticmethod
    def load_meta() -> Dict:
        """Load _users and _whitelist from index bin."""
        index = JSONBinStorage._read_index()
        return {
            "users":     index.get("_users", {}),
            "whitelist": index.get("_whitelist", []),
        }

    @staticmethod
    def save_meta(uid: int, name: str, username: str, whitelist: set) -> None:
        """Persist user record and full whitelist atomically in one write."""
        with JSONBinStorage._write_lock:
            try:
                index = JSONBinStorage._read_index()
                users = index.get("_users", {})
                key   = str(uid)
                if users.get(key) == {"name": name, "username": username}:
                    return  # no change — skip write
                users[key] = {"name": name, "username": username}
                index["_users"]     = users
                index["_whitelist"] = [str(i) for i in whitelist]
                JSONBinStorage._write_index(index)
            except Exception as e:
                log.debug(f"save_meta failed: {e}")

    @staticmethod
    def save_whitelist(whitelist: set) -> None:
        """Persist just the whitelist (called after /berserk whitelist)."""
        with JSONBinStorage._write_lock:
            try:
                index = JSONBinStorage._read_index()
                index["_whitelist"] = [str(i) for i in whitelist]
                JSONBinStorage._write_index(index)
            except Exception as e:
                log.warning(f"save_whitelist failed: {e}")

    @staticmethod
    def is_enabled() -> bool:
        return bool(JSONBIN_MASTER_KEY and JSONBIN_INDEX_BIN)

class SessionManager:
    @staticmethod
    def get_session_path(filename: str) -> str:
        return os.path.join(Config.SESSION_DIR, filename)

    @staticmethod
    def _parse_session_dict(data: Dict, ref: str) -> Optional[Dict]:
        """Parse a session data dict into a session record. ref = filepath or phone key."""
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
                'timestamp':    (
                    datetime.fromisoformat(ts_str).strftime('%-d %b')
                    if ts_str else 'Unknown'
                ),
                'datetime':     ts,
                'data':         data,
                'status':       status,
            }
        except Exception:
            return None

    @staticmethod
    def find_saved_sessions(tg_uid: Optional[int] = None) -> List[Dict]:
        sessions_by_phone: Dict = {}

        if JSONBinStorage.is_enabled() and tg_uid:
            remote = JSONBinStorage.read_user_sessions(tg_uid)
            stale  = []
            for phone_key, data in remote.items():
                if phone_key.startswith("_"):
                    continue
                if not isinstance(data, dict):
                    continue
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
            remote       = JSONBinStorage.read_user_sessions(tg_uid)
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

        self.fcm_token:     Optional[str]  = None
        self.auth_token:    Optional[str]  = None
        self.refresh_token: Optional[str]  = None
        self.user_id:       Optional[str]  = None
        self.user_phone:    Optional[str]  = None
        self.user_name:     Optional[str]  = None
        self.v1_profile_id: Optional[str]  = None
        self.wid:           Optional[str]  = None
        self.balance:       float          = 0.0

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
    def send_otp(phone: str, x_profile: str, device: DeviceIdentity,
                 x_token: str) -> Tuple[int, str]:
        try:
            headers  = device.get_headers({
                'Content-Type': 'application/json',
                'x-token':      x_token,
                'x-profile':    x_profile,
            })
            payload  = {"phone_number": phone, "is_retry": False, "retry_method": ""}
            response = RupiyoAPI._request('POST', Config.API_ENDPOINTS['send_otp'],
                                     json=payload, headers=headers, timeout=30)
            return response.status_code, response.text
        except Exception as e:
            return 0, str(e)

    @staticmethod
    def login_with_otp(phone: str, otp: str, x_profile: str,
                       device: DeviceIdentity, v2_profile_id: str,
                       x_token: str) -> Tuple[int, str, bool, Optional[str]]:
        try:
            headers = device.get_headers({
                'Content-Type': 'application/json',
                'x-token':      x_token,
                'x-profile':    v2_profile_id,
            })
            payload = {
                "phone_number":    phone,
                "otp":             int(otp),
                "gaid":            device.ga_id,
                "app_instance_id": Utils.generate_app_instance_id(),
            }
            response    = RupiyoAPI._request('POST', Config.API_ENDPOINTS['login'],
                                        json=payload, headers=headers, timeout=30)
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
                        ban_message = data.get('error', 'Account blocked, contact support')
                except ValueError:
                    pass

            return response.status_code, response.text, is_new_user, ban_message
        except Exception as e:
            return 0, str(e), False, None

    @staticmethod
    def signup_new_user(device: DeviceIdentity, phone: str, otp: str,
                        full_name: str, referral_code: str, x_profile: str,
                        v2_profile_id: str, x_token: str) -> Tuple[int, str, Optional[str]]:
        try:
            headers = device.get_headers({
                'x-environment': 'clone',
                'x-profile':     v2_profile_id,
                'content-type':  'application/json; charset=UTF-8',
            })
            payload = {
                "phone_number":    phone,
                "otp":             int(otp),
                "full_name":       full_name,
                "email":           "",
                "referral_code":   referral_code if referral_code else "",
                "utm_source":      "utm_source=google-play&utm_medium=organic",
                "gaid":            device.ga_id,
                "app_instance_id": Utils.generate_app_instance_id(),
            }
            response    = RupiyoAPI._request('POST', Config.API_ENDPOINTS['signup'],
                                        json=payload, headers=headers, timeout=30)
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
                        ban_message = data.get('error', 'Account blocked, contact support')
                except ValueError:
                    pass

            return response.status_code, response.text, ban_message
        except Exception as e:
            return 0, str(e), None

    @staticmethod
    def refresh_token(refresh_token: str) -> Tuple[bool, Union[Dict, str], Optional[str]]:
        try:
            headers = {
                'User-Agent':        ('Mozilla/5.0 (Linux; Android 15; 23076PC4BI Build/AQ3A.240912.001; wv)'
                                         ' AppleWebKit/537.36'),
                'Content-Type':      'application/json',
                'Accept':            'application/json, text/plain, */*',
                'x-pn':              'com.rupiyo.realmoney.rewardsapp',
                'Origin':            'https://rupiyo.app',
                'Referer':           'https://rupiyo.app/',
                'x-requested-with':  'com.rupiyo.realmoney.rewardsapp',
            }
            response = RupiyoAPI._request('POST', Config.API_ENDPOINTS['refresh_token'],
                                     json={'refresh_token': refresh_token},
                                     headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()
                return True, {
                    'access_token':  data.get('access_token'),
                    'refresh_token': data.get('refresh_token', refresh_token),
                }, None

            ban_message = None
            if response.status_code == 403:
                try:
                    data = response.json()
                    if data.get('error_code') == 1004 or "account blocked" in data.get('error', '').lower():
                        ban_message = data.get('error', 'Account blocked, contact support')
                        return False, "Account banned", ban_message
                except ValueError:
                    pass

            return False, f"HTTP {response.status_code}", None
        except Exception as e:
            return False, str(e), None

    @staticmethod
    def update_fcm_token(device: DeviceIdentity) -> bool:
        try:
            if not device.fcm_token:
                device.fcm_token = Utils.generate_fcm_token()
            response = RupiyoAPI._request('PUT',
                Config.API_ENDPOINTS['fcm_token_update'],
                json={'fcm_token': device.fcm_token},
                headers=device.get_headers({'content-type': 'application/json; charset=UTF-8'}),
                timeout=30,
            )
            return response.status_code == 200
        except Exception as e:
            Utils.debug_print(f"FCM update error: {e}")
            return False

    @staticmethod
    def send_ongoing_signal(device: DeviceIdentity, offer_id: str, reward_id: int) -> Tuple[bool, str]:
        try:
            encrypted_data = Encryption.encrypt_signal_data(device, offer_id, reward_id)
            if not encrypted_data:
                return False, "Encryption failed"
            headers  = device.get_headers({
                'Host':           'api.rupiyo.app',
                'content-type':   'text/plain; charset=utf-8',
                'content-length': str(len(encrypted_data)),
            })
            response = RupiyoAPI._request('POST', Config.API_ENDPOINTS['v1_user_offer_signal'],
                                     headers=headers, data=encrypted_data, timeout=30)
            if response.status_code == 200:
                return True, "Signal sent"
            return False, f"HTTP {response.status_code}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_user_profile(device: DeviceIdentity) -> Tuple[bool, Optional[Dict]]:
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['v1_user_profile'],
                                    headers=device.get_headers(), timeout=30)
            if response.status_code == 200:
                return True, response.json()
            return False, None
        except Exception:
            return False, None

    @staticmethod
    def get_wallet_id(device: DeviceIdentity) -> Tuple[bool, Optional[str]]:
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['v1_user_wallet'],
                                    headers=device.get_headers(), timeout=30)
            if response.status_code == 200:
                return True, response.json().get('wid')
            return False, None
        except Exception:
            return False, None

    @staticmethod
    def get_wallet_balance(device: DeviceIdentity) -> Tuple[bool, float]:
        if not device.wid:
            return False, 0.0
        try:
            url      = Config.API_ENDPOINTS['v2_wallet_balance'].format(device.wid)
            response = RupiyoAPI._request('GET', url, headers=device.get_headers(), timeout=30)
            if response.status_code == 200:
                data = response.json()
                bal  = data.get('balance', {})
                if isinstance(bal, dict):
                    return True, bal.get('amount', 0.0)
                return True, float(bal)
            return False, 0.0
        except Exception:
            return False, 0.0

    @staticmethod
    def refresh_wallet(device: DeviceIdentity) -> bool:
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
    def get_offers(device: DeviceIdentity, list_type: str, page: int = 1) -> Optional[List]:
        try:
            response = RupiyoAPI._request('GET',
                Config.API_ENDPOINTS['v6_offer_list'],
                headers=device.get_headers(),
                params={'list_type': list_type, 'page': page},
                timeout=30,
            )
            if response.status_code == 200:
                return response.json().get('results', [])
            return None
        except Exception:
            return None

    @staticmethod
    def get_all_offers(device: DeviceIdentity, list_type: str) -> List:
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
    def _filter_expired(device: DeviceIdentity, offers: List) -> List:
        live = []
        for offer in offers:
            try:
                details = RupiyoAPI.get_offer_details(device, offer.get('oid'))
                if not details:
                    live.append(offer); continue
                rewards = details.get('postback_reward', [])
                if not rewards:
                    live.append(offer); continue
                ok, url = RupiyoAPI.get_reward_cta(device, rewards[0].get('reward_id'))
                if ok and urlparse(url).hostname and urlparse(url).hostname.endswith('epicplay.in'):
                    Utils.debug_print(f"Filtered expired: {offer.get('title')}")
                    continue
                live.append(offer)
            except Exception:
                live.append(offer)
        return live

    @staticmethod
    def get_offer_details(device: DeviceIdentity, offer_id: str) -> Optional[Dict]:
        try:
            response = RupiyoAPI._request('GET',
                Config.API_ENDPOINTS['v6_offer_details'],
                headers=device.get_headers(),
                params={'offer_id': offer_id},
                timeout=30,
            )
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    @staticmethod
    def get_reward_cta(device: DeviceIdentity, reward_id: int) -> Tuple[bool, str]:
        try:
            response = RupiyoAPI._request('GET',
                Config.API_ENDPOINTS['v1_offer_reward_cta'],
                headers=device.get_headers(),
                params={'reward_id': reward_id, 'ga_id': device.ga_id},
                timeout=30,
            )
            if response.status_code == 200:
                return True, response.json().get('url', '')
            return False, response.text
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_payout_store_info(device: DeviceIdentity) -> Optional[Dict]:
        try:
            response = RupiyoAPI._request('GET', Config.API_ENDPOINTS['payout_store_info'],
                                    headers=device.get_headers(), timeout=30)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    @staticmethod
    def get_payout_packs(device: DeviceIdentity, method: str = "UPI") -> List:
        server_packs: List = []
        try:
            response = RupiyoAPI._request('GET',
                Config.API_ENDPOINTS['payout_pack_list'],
                headers=device.get_headers(),
                params={'page': 1, 'payment_method': method, 'q': ''},
                timeout=30,
            )
            if response.status_code == 200:
                server_packs = response.json().get('packs', [])
        except Exception as e:
            log.debug(f"Server packs fetch: {e}")

        seen_ids = {p.get('pack_id') for p in server_packs}
        merged   = list(server_packs)
        for p in Config.KNOWN_PACKS:
            if p['pack_id'] not in seen_ids:
                merged.append(p)
        merged.sort(key=lambda p: p.get('payout', {}).get('amount', 0))
        return merged

    @staticmethod
    def purchase_payout(device: DeviceIdentity, pack_id: int,
                        payment_address: str) -> Tuple[bool, str]:
        try:
            url      = Config.API_ENDPOINTS['purchase_payout'].format(device.wid)
            response = RupiyoAPI._request('POST', url,
                json={
                    'pack_id':              pack_id,
                    'payment_address':      payment_address,
                    'additional_vpa_params': {},
                },
                headers=device.get_headers({'Content-Type': 'application/json'}),
                timeout=30,
            )
            if response.status_code == 200:
                return True, response.text
            return False, response.text
        except Exception as e:
            return False, str(e)

class ProfileManager:
    @staticmethod
    def init_v2_profile(device: DeviceIdentity) -> Optional[str]:
        try:
            data = {
                "device_model": device.device_model,
                "android_id":   device.android_id,
                "ga_id":        device.ga_id,
                "adv_id":       device.adv_id,
                "gsf_id":       "",
                "drm_id":       device.drm_id,
                "app_set_id":   device.app_set_id,
                "package_id":   device.package_name,
            }
            json_bytes = json.dumps(data, separators=(',', ':')).encode('utf-8')
            key_str    = f"#{device.x_sid}_4#"
            key_bytes  = hashlib.md5(key_str.encode('utf-8')).hexdigest().encode('utf-8')
            iv         = os.urandom(16)
            cipher     = AES.new(key_bytes, AES.MODE_CFB, iv=iv, segment_size=128)
            encrypted  = cipher.encrypt(json_bytes)
            b64        = base64.b64encode(iv + encrypted).decode('ascii')
            b64        = b64.replace('+', '-').replace('/', '_').rstrip('=')

            boundary = str(uuid.uuid4())
            body     = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="basket"\r\n'
                f'Content-Type: multipart/form-data\r\n'
                f'Content-Length: {len(b64)}\r\n\r\n'
                f'{b64}\r\n'
                f'--{boundary}--\r\n'
            )
            headers = {
                'User-Agent':     'okhttp/4.12.0',
                'x-sid':          device.x_sid,
                'x-profile':      '',
                'appn':           device.appn,
                'x-language':     'ENGLISH',
                'x-device-id':    device.device_id,
                'x-device-model': device.device_model,
                'x-pn':           device.package_name,
                'x-platform':     'android',
                'x-app-id':       'rupiyo',
                'Content-Type':   f'multipart/form-data; boundary={boundary}',
            }
            response = requests.post(Config.API_ENDPOINTS['v2_profile_init'],
                                     headers=headers, data=body, timeout=30)
            if response.status_code == 200:
                return response.json().get('profile_id')
            return None
        except Exception:
            return None

    @staticmethod
    def _build_basket_payload(device: DeviceIdentity) -> str:
        app_list = []
        for app in Config.POPULAR_APPS:
            days         = random.randint(app['days_range'][0], app['days_range'][1])
            install_time = str(int((datetime.now().timestamp() * 1000) - (days * 86400000)))
            app_list.append({'package_name': app['package'], 'install_time': install_time})

        payload = {
            "usage_data":             [],
            "current_installed_apps": app_list,
            "device_model":           device.device_model,
            "android_id":             device.android_id,
            "app_set_id":             device.app_set_id,
            "gsf_id":                 "",
            "drm_id":                 device.drm_id,
            "p_id":                   device.package_name,
            "adv_id":                 "",
        }
        json_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        key_bytes  = Encryption.double_md5_key(device.user_id, device.user_phone)
        iv         = os.urandom(16)
        cipher     = AES.new(key_bytes, AES.MODE_CFB, iv=iv, segment_size=128)
        encrypted  = cipher.encrypt(json_bytes)
        b64        = base64.b64encode(iv + encrypted).decode('ascii')
        return b64.replace('+', '-').replace('/', '_').replace('=', '')

    @staticmethod
    def _basket_headers(device: DeviceIdentity) -> Dict:
        headers = {
            "auth-token":     device.auth_token,
            "appn":           device.appn,
            "x-language":     "ENGLISH",
            "x-device-id":    device.device_id,
            "x-device-model": device.device_model,
            "x-pn":           device.package_name,
            "x-platform":     "android",
            "x-app-id":       "rupiyo",
            "User-Agent":     "okhttp/4.12.0",
        }
        if device.v1_profile_id:
            headers["x-profile"] = device.v1_profile_id
        return headers

    @staticmethod
    def init_v1_profile(device: DeviceIdentity) -> Tuple[bool, Optional[str]]:
        try:
            b64      = ProfileManager._build_basket_payload(device)
            headers  = ProfileManager._basket_headers(device)
            files    = {'file': ('basket', b64, 'multipart/form-data')}
            response = requests.post(Config.API_ENDPOINTS['v1_basket_init'],
                                     headers=headers, files=files, timeout=30)
            if response.status_code == 200:
                device.v1_profile_id = response.json().get('profile_id')
                return True, device.v1_profile_id
            return False, response.text
        except Exception as e:
            return False, str(e)

    @staticmethod
    def sync_telemetry(device: DeviceIdentity) -> bool:
        try:
            b64      = ProfileManager._build_basket_payload(device)
            headers  = ProfileManager._basket_headers(device)
            files    = {'file': ('encrypted_payload.txt', b64, 'multipart/form-data')}
            response = requests.post(Config.API_ENDPOINTS['v1_telemetry_sync'],
                                     headers=headers, files=files, timeout=30)
            return response.status_code == 200
        except Exception as e:
            Utils.debug_print(f"Telemetry sync error: {e}")
            return False

class SelfHealer:
    @staticmethod
    def heal_session(device: DeviceIdentity, session_file: str) -> bool:
        """Silent self-healer for bot use — no stdout prints."""
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
    def sync_profile_fields(device: 'DeviceIdentity', session_file: str):
        """Fetch live profile from server and correct any stale/wrong fields silently."""
        try:
            ok, profile = RupiyoAPI.get_user_profile(device)
            if not ok or not profile:
                return
            changed = False
            server_name  = profile.get('full_name') or profile.get('name')
            server_phone = profile.get('phone_number') or profile.get('phone')
            server_uid   = profile.get('user_id')
            if server_name and server_name != device.user_name:
                device.user_name  = server_name
                changed = True
            if server_phone and server_phone != device.user_phone:
                device.user_phone = server_phone
                changed = True
            if server_uid and server_uid != device.user_id:
                device.user_id    = server_uid
                changed = True
            if changed:
                SessionManager.update_session(session_file, device)
        except Exception as e:
            log.warning(f"JSONBin create bin failed: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
#  BOT STATE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BotState:
    """Per-user conversation state."""
    device:       Optional[DeviceIdentity] = None
    session_file: Optional[str]            = None
    offers:     List = field(default_factory=list)
    list_type:  str  = 'active'
    list_title: str  = ''
    page:       int  = 1
    current_offer: Optional[Dict] = None
    packs:         List           = field(default_factory=list)
    selected_pack: Optional[Dict] = None
    inactivity_timeout: float = field(default_factory=lambda: random.uniform(3*60, 4*60))
    phone:         str = ''
    x_profile:     str = ''
    x_token:       str = ''
    v2_profile_id: str = ''
    otp:           str = ''
    full_name:     str = ''
    awaiting:      Optional[str] = None
    last_activity: float          = field(default_factory=time.time)

_user_states: Dict[int, BotState] = {}
_tg_uid_map: Dict[str, int] = {}  # rupiyo user_id → telegram uid

def get_state(uid: int) -> BotState:
    if uid not in _user_states:
        _user_states[uid] = BotState()
    return _user_states[uid]

INACTIVITY_TIMEOUT_MIN = 3 * 60  # 3 minutes
INACTIVITY_TIMEOUT_MAX = 4 * 60  # 4 minutes

def touch(st: BotState):
    st.last_activity = time.time()

def is_session_alive(st: BotState) -> bool:
    if not st.device:
        return False
    return (time.time() - st.last_activity) < st.inactivity_timeout

# ─── Per-user offer cache ─────────────────────────────────────────────────────
# Each Telegram user has their own cache keyed by uid.
# Offers differ per account (eligibility, expiry, ongoing status, etc.) so a
# shared global cache would serve the wrong data to most users.
_user_offer_cache: Dict[int, Dict] = {}   # uid → {'active': [...], '_ts': float, ...}
_user_offer_cache_lock = threading.Lock()
CACHE_TTL = 5 * 60  # 5 minutes

def get_cached_offers(uid: int, list_type: str) -> Optional[List]:
    with _user_offer_cache_lock:
        user_cache = _user_offer_cache.get(uid)
        if not user_cache:
            return None
        ts = user_cache.get('_ts', 0.0)
        if ts and (time.time() - ts) < CACHE_TTL:
            return user_cache.get(list_type)
    return None

def set_cached_offers(uid: int, list_type: str, offers: List):
    with _user_offer_cache_lock:
        if uid not in _user_offer_cache:
            _user_offer_cache[uid] = {}
        _user_offer_cache[uid][list_type] = offers
        _user_offer_cache[uid]['_ts'] = time.time()

def invalidate_user_cache(uid: int):
    """Drop all cached offers for a user (e.g. on logout / session expiry)."""
    with _user_offer_cache_lock:
        _user_offer_cache.pop(uid, None)

def start_prefetch(st: BotState, uid: int):
    """Trigger a per-user cache refresh if stale, using this session's device."""
    if not st.device:
        return
    if get_cached_offers(uid, 'active') is not None:
        return  # cache is still fresh for this user
    device = st.device
    def _fetch():
        for ltype in ('active', 'ongoing', 'completed'):
            try:
                offers = RupiyoAPI.get_all_offers(device, ltype)
                set_cached_offers(uid, ltype, offers)
            except Exception as e:
                log.debug(f"Prefetch uid={uid} {ltype}: {e}")
    threading.Thread(target=_fetch, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def run_sync(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

async def safe_edit(query, text: str, markup=None, pm: str = ParseMode.HTML):
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=pm,
                                      disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning(f"safe_edit error: {e}")

def trunc(s: str, n: int = 38) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"

async def update_commands(context, sessions: List, uid: int = 0):
    """Set BotFather slash commands dynamically based on saved accounts."""
    cmds = [BotCommand("start", "🚀 Main menu / Account list")]
    for i, s in enumerate(sessions, 1):
        name  = s.get('user_name', 'Account')
        phone = s.get('masked_phone', '****')
        cmds.append(BotCommand(str(i), f"Login as {name} ({phone})"))
    if uid and is_admin(uid):
        cmds.append(BotCommand("berserk", "🔧 Admin panel"))
    try:
        from telegram import BotCommandScopeChat
        if uid:
            await context.bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))
        else:
            await context.bot.set_my_commands(cmds)
    except Exception as e:
        log.warning(f"set_my_commands failed: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════════════════════════════════════

def mk_login_keyboard(sessions: List) -> InlineKeyboardMarkup:
    rows = []
    for i, s in enumerate(sessions):
        icon  = "✅" if s['status'] == 'complete' else "🔄" if s['status'] == 'recoverable' else "❌"
        label = f"{icon} {s['user_name']}  ·  {s['masked_phone']}  ({s['timestamp']})"
        rows.append([InlineKeyboardButton(label, callback_data=f"acc:{i}")])
    rows.append([InlineKeyboardButton("➕  Add Account", callback_data="new_acct")])
    return InlineKeyboardMarkup(rows)

def mk_dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍  Search Offers",    callback_data="menu:search"),
         InlineKeyboardButton("⏳  Ongoing Offers",   callback_data="menu:ongoing")],
        [InlineKeyboardButton("✅  Completed Offers", callback_data="menu:completed"),
         InlineKeyboardButton("💸  Withdraw",         callback_data="menu:withdraw")],
        [InlineKeyboardButton("🔄  Refresh Balance",  callback_data="refresh_bal"),
         InlineKeyboardButton("🔙  Switch Account",   callback_data="back_login")],
    ])

def mk_offer_list_keyboard(st: BotState) -> InlineKeyboardMarkup:
    total  = len(st.offers)
    pages  = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    start  = (st.page - 1) * PER_PAGE
    end    = min(start + PER_PAGE, total)

    rows = []
    for i, o in enumerate(st.offers[start:end]):
        idx   = start + i
        title = trunc(o.get('title', 'Untitled'), 32)
        amt   = o.get('payout', {}).get('amnt', 0)
        rows.append([InlineKeyboardButton(
            f"📌  {title}  {Utils.format_currency(amt)}",
            callback_data=f"sel:{idx}",
        )])

    nav = []
    if st.page > 1:
        nav.append(InlineKeyboardButton("◀", callback_data="page:prev"))
    nav.append(InlineKeyboardButton(f"  {st.page}/{pages}  ", callback_data="noop"))
    if st.page < pages:
        nav.append(InlineKeyboardButton("▶", callback_data="page:next"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton("🔄  Refresh",   callback_data="refresh_list"),
        InlineKeyboardButton("🏠  Dashboard", callback_data="back_dash"),
    ])
    return InlineKeyboardMarkup(rows)

def mk_offer_detail_keyboard(list_type: str, expired: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if not expired and list_type in ('active', 'ongoing'):
        rows.append([InlineKeyboardButton("🔗  Get Link", callback_data="get_link")])
    rows.append([
        InlineKeyboardButton("🔙  Back to List", callback_data="back_list"),
        InlineKeyboardButton("🏠  Dashboard",    callback_data="back_dash"),
    ])
    return InlineKeyboardMarkup(rows)

def mk_pack_keyboard(packs: List) -> InlineKeyboardMarkup:
    rows = []
    for p in packs:
        pid    = p.get('pack_id')
        amount = p.get('payout', {}).get('amount', 0)
        rows.append([InlineKeyboardButton(f"💰  ₹{amount}", callback_data=f"pack:{pid}:{amount}")])
    rows.append([InlineKeyboardButton("🔙  Dashboard", callback_data="back_dash")])
    return InlineKeyboardMarkup(rows)

def mk_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅  Confirm", callback_data="confirm:yes"),
        InlineKeyboardButton("❌  Cancel",  callback_data="confirm:no"),
    ]])

def mk_back_dash() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠  Dashboard", callback_data="back_dash")]])

def mk_back_list() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙  Back to List", callback_data="back_list"),
        InlineKeyboardButton("🏠  Dashboard",    callback_data="back_dash"),
    ]])

# ═══════════════════════════════════════════════════════════════════════════════
#  TEXT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_login_text(sessions: List, name: str = "") -> str:
    greeting = f"👋 Welcome back, <b>{name}</b>!" if name else "🚀 <b>RUPIYO</b>"
    if sessions:
        return f"{greeting}\n\nChoose an option to continue:"
    return f"{greeting}\n\nNo saved accounts yet. Add one to get started."

def build_dashboard_text(device: DeviceIdentity) -> str:
    uid = f"<code>{device.user_id}</code>" if device.user_id else "—"
    return (
        "🎯 <b>RUPIYO DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤  Hello! <b>{device.user_name}</b>\n"
        f"💰  Balance: <b>{Utils.format_currency(device.balance)}</b>\n"
        f"🆔  User ID: {uid}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )

def build_offer_list_text(st: BotState) -> str:
    total = len(st.offers)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return (
        f"<b>{st.list_title}</b>\n"
        f"Page {st.page}/{pages}  ·  {total} offer{'s' if total != 1 else ''} total\n\n"
        "Tap an offer to view details:"
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  /start  +  SHOW DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

async def show_dashboard(query, context, device: DeviceIdentity, edit: bool = True):
    uid = query.from_user.id
    st  = get_state(uid)
    if device.user_id:
        _tg_uid_map[device.user_id] = uid
    await run_sync(RupiyoAPI.refresh_wallet, device)
    start_prefetch(st, uid)
    text   = build_dashboard_text(device)
    markup = mk_dashboard_keyboard()
    if edit:
        await safe_edit(query, text, markup)
    else:
        await query.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

# ═══════════════════════════════════════════════════════════════════════════════
#  LOGIN FLOW
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_select_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    user     = update.effective_user
    uid      = user.id
    record_user(user)
    st       = get_state(uid)
    sessions = await run_sync(SessionManager.find_saved_sessions, uid)
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

async def cb_new_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    st    = get_state(uid)
    st.awaiting = 'phone'

    await safe_edit(
        query,
        "🆕 <b>Add Account</b>\n\n"
        "📞 Send your phone number (10 digits, without country code):",
        pm=ParseMode.HTML,
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  TEXT MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = user.id
    record_user(user)

    if not is_allowed(uid):
        return  # silently ignore

    st   = get_state(uid)
    text = update.message.text.strip()
    touch(st)

    if   st.awaiting == 'phone':    await _handle_phone(update, context, st, text)
    elif st.awaiting == 'otp':      await _handle_otp(update, context, st, text)
    elif st.awaiting == 'name':     await _handle_name(update, context, st, text)
    elif st.awaiting == 'referral': await _handle_referral(update, context, st, text)
    elif st.awaiting == 'keyword':  await _handle_keyword(update, context, st, text)
    elif st.awaiting == 'upi':      await _handle_upi(update, context, st, text)

async def _handle_phone(update, context, st: BotState, text: str):
    phone = text
    if phone.startswith('+91'):
        phone = phone[3:]
    elif phone.startswith('91') and len(phone) == 12:
        phone = phone[2:]
    if not phone.isdigit() or len(phone) != 10:
        await update.message.reply_text("❌ Invalid phone number. Send 10 digits (without country code):")
        return

    st.phone  = phone
    device    = DeviceIdentity()
    device.user_phone = phone
    st.device = device

    def setup():
        v2_id     = ProfileManager.init_v2_profile(device) or Utils.generate_uuid()
        x_profile = Utils.generate_uuid()
        x_token   = Utils.generate_x_token()
        status, resp = RupiyoAPI.send_otp(phone, x_profile, device, x_token)
        return v2_id, x_profile, x_token, status, resp

    v2_id, x_profile, x_token, status, resp = await run_sync(setup)

    if status != 200:
        err = ""
        try: err = json.loads(resp).get("error", "")
        except ValueError: pass
        await update.message.reply_text(friendly_error(err))
        st.awaiting = None
        return

    st.v2_profile_id = v2_id
    st.x_profile     = x_profile
    st.x_token       = x_token
    st.awaiting      = 'otp'

    await update.message.reply_text("✅ OTP sent!\n\n🔑 Enter the 6-digit OTP:")

async def _handle_otp(update, context, st: BotState, text: str):
    otp = text.strip()
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text("❌ OTP must be exactly 6 digits. Try again:")
        return

    device = st.device

    def do_login():
        return RupiyoAPI.login_with_otp(
            st.phone, otp, st.x_profile, device,
            st.v2_profile_id, st.x_token,
        )

    status, resp, is_new_user, ban_message = await run_sync(do_login)

    if status != 200:
        err = ban_message or ""
        try:
            if not err: err = json.loads(resp).get("error", "")
        except ValueError: pass
        await update.message.reply_text(friendly_error(err))
        st.awaiting = None
        return

    if is_new_user:
        st.otp      = otp
        st.awaiting = 'name'
        await update.message.reply_text(
            "📝 <b>New account!</b>\n\n👤 Enter your full name:", parse_mode=ParseMode.HTML)
        return

    await _post_login_setup(update, context, st, device)

async def _handle_name(update, context, st: BotState, text: str):
    st.full_name = text.strip() or "User"
    st.awaiting  = 'referral'
    await update.message.reply_text(
        "🎫 Enter referral code (or send <b>–</b> to skip):", parse_mode=ParseMode.HTML)

async def _handle_referral(update, context, st: BotState, text: str):
    referral = '' if text.strip() in ('-', '–', '—') or not text.strip() else text.strip()
    device   = st.device

    def do_signup():
        return RupiyoAPI.signup_new_user(
            device, st.phone, st.otp, st.full_name, referral,
            st.x_profile, st.v2_profile_id, st.x_token,
        )

    status, resp, ban_message = await run_sync(do_signup)

    if status != 200:
        err = ban_message or ""
        try:
            if not err: err = json.loads(resp).get("error", "")
        except ValueError: pass
        await update.message.reply_text(friendly_error(err))
        st.awaiting = None
        return

    await _post_login_setup(update, context, st, device)

async def _post_login_setup(update, context, st: BotState, device: DeviceIdentity):
    uid_for_save = update.effective_user.id
    def setup():
        ProfileManager.init_v1_profile(device)
        ProfileManager.sync_telemetry(device)
        RupiyoAPI.update_fcm_token(device)
        RupiyoAPI.refresh_wallet(device)
        return SessionManager.save_session(device, tg_uid=uid_for_save)

    session_file = await run_sync(setup)

    st.device       = device
    st.session_file = session_file
    st.awaiting     = None

    if device.user_id:
        _tg_uid_map[device.user_id] = update.effective_user.id
    start_prefetch(st, uid_for_save)
    fresh_sessions = await run_sync(SessionManager.find_saved_sessions, update.effective_user.id)
    await update_commands(context, fresh_sessions, uid=uid_for_save)
    await update.message.reply_text(
        f"✅ Welcome, <b>{device.user_name}</b>!\n\n{build_dashboard_text(device)}",
        reply_markup=mk_dashboard_keyboard(),
        parse_mode=ParseMode.HTML,
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_refresh_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Refreshing…")
    uid   = update.effective_user.id
    st    = get_state(uid)
    if not st.device:
        return
    await show_dashboard(query, context, st.device, edit=True)

async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    uid    = update.effective_user.id
    st     = get_state(uid)
    action = query.data.split(':')[1]

    if not st.device:
        await safe_edit(query, "❌ Session expired. Use /menu to start a new session.")
        return
    touch(st)

    if action == 'search':
        st.awaiting = 'keyword'
        await safe_edit(
            query,
            "🔍 <b>Search Offers</b>\n\nSend a keyword to search:",
            pm=ParseMode.HTML,
        )

    elif action in ('ongoing', 'completed'):
        label  = "⏳ Ongoing Offers" if action == 'ongoing' else "✅ Completed Offers"
        offers = get_cached_offers(uid, action)
        if offers is None:
            await safe_edit(query, f"<b>{label}</b>\n\n📡 Fetching…", pm=ParseMode.HTML)
            offers = await run_sync(RupiyoAPI.get_all_offers, st.device, action)
            if offers is not None:
                set_cached_offers(uid, action, offers)
        if not offers:
            await safe_edit(query, f"📭 No {action} offers found.", markup=mk_back_dash(), pm=ParseMode.HTML)
            return
        st.offers     = offers
        st.list_type  = action
        st.list_title = label
        st.page       = 1
        await safe_edit(query, build_offer_list_text(st), markup=mk_offer_list_keyboard(st), pm=ParseMode.HTML)

    elif action == 'withdraw':
        await _show_withdraw(query, st)

# ═══════════════════════════════════════════════════════════════════════════════
#  OFFER LIST  (search keyword handler + pagination + select + link)
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_keyword(update, context, st: BotState, text: str):
    if not text.strip():
        await update.message.reply_text("❌ No keyword entered. Try again:")
        return
    st.awaiting = None
    keyword     = text.strip()

    await update.message.reply_text(f"🔍 Searching for <b>{keyword}</b>…", parse_mode=ParseMode.HTML)

    uid        = update.effective_user.id
    all_active = get_cached_offers(uid, 'active')
    if all_active is None:
        await update.message.reply_text("📡 Loading offers…")
        all_active = await run_sync(RupiyoAPI.get_all_offers, st.device, 'active')
        if all_active:
            set_cached_offers(uid, 'active', all_active)
    filtered   = [o for o in (all_active or []) if keyword.lower() in o.get('title', '').lower()]

    if not filtered:
        await update.message.reply_text(
            f"📭 No offers found for <b>{keyword}</b>",
            reply_markup=mk_back_dash(),
            parse_mode=ParseMode.HTML,
        )
        return

    st.offers     = filtered
    st.list_type  = 'active'
    st.list_title = f"🔍 Search: '{keyword}'"
    st.page       = 1

    await update.message.reply_text(
        build_offer_list_text(st),
        reply_markup=mk_offer_list_keyboard(st),
        parse_mode=ParseMode.HTML,
    )

async def cb_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    uid    = update.effective_user.id
    st     = get_state(uid)
    touch(st)
    action = query.data.split(':')[1]
    pages  = max(1, (len(st.offers) + PER_PAGE - 1) // PER_PAGE)

    if action == 'prev' and st.page > 1:
        st.page -= 1
    elif action == 'next' and st.page < pages:
        st.page += 1

    await safe_edit(query, build_offer_list_text(st), markup=mk_offer_list_keyboard(st), pm=ParseMode.HTML)

async def cb_refresh_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Refreshing…")
    uid   = update.effective_user.id
    st    = get_state(uid)

    await safe_edit(query, "📡 Refreshing…", pm=ParseMode.HTML)

    if 'Search:' in st.list_title and "'" in st.list_title:
        keyword    = st.list_title.split("'")[1]
        all_active = await run_sync(RupiyoAPI.get_all_offers, st.device, 'active')
        if all_active:
            set_cached_offers(uid, 'active', all_active)
        fresh = [o for o in (all_active or []) if keyword.lower() in o.get('title', '').lower()]
    else:
        fresh = await run_sync(RupiyoAPI.get_all_offers, st.device, st.list_type)
        if fresh:
            set_cached_offers(uid, st.list_type, fresh)

    if not fresh:
        await safe_edit(query, f"📭 No {st.list_type} offers found.", markup=mk_back_dash(), pm=ParseMode.HTML)
        return

    st.offers = fresh
    pages     = max(1, (len(fresh) + PER_PAGE - 1) // PER_PAGE)
    st.page   = min(st.page, pages)
    await safe_edit(query, build_offer_list_text(st), markup=mk_offer_list_keyboard(st), pm=ParseMode.HTML)

async def cb_select_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    st    = get_state(uid)
    touch(st)
    idx   = int(query.data.split(':')[1])

    if idx >= len(st.offers):
        await safe_edit(query, "❌ Offer not found. Refresh the list.", markup=mk_back_dash())
        return

    offer          = st.offers[idx]
    st.current_offer = offer

    title  = offer.get('title', 'Untitled')
    amt    = offer.get('payout', {}).get('amnt', 0)
    oid    = offer.get('oid', 'N/A')
    otype  = offer.get('type', 'N/A')
    votes  = offer.get('metrics', {}).get('upvotes', 0)

    text = (
        f"📌 <b>{title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰  Reward: <b>{Utils.format_currency(amt)}</b>\n"
        f"🆔  ID: <code>{oid}</code>\n"
        f"📊  Type: {otype}\n"
        f"👍  Upvotes: {votes}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await safe_edit(query, text, markup=mk_offer_detail_keyboard(st.list_type), pm=ParseMode.HTML)

async def cb_get_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔗 Fetching link…")
    uid   = update.effective_user.id
    st    = get_state(uid)
    touch(st)

    if not st.current_offer:
        await safe_edit(query, "❌ No offer selected.", markup=mk_back_dash())
        return

    offer       = st.current_offer
    offer_id    = offer.get('oid')
    offer_title = offer.get('title', '')

    await safe_edit(query, f"🔄 Getting link for <b>{trunc(offer_title, 40)}</b>…", pm=ParseMode.HTML)

    def fetch_link():
        details = RupiyoAPI.get_offer_details(st.device, offer_id)
        if not details:
            return None, "Failed to get offer details", []

        rewards = details.get('postback_reward', [])
        if not rewards:
            return None, "No rewards found", []

        reward    = rewards[0]
        reward_id = reward.get('reward_id')

        ok, url = RupiyoAPI.get_reward_cta(st.device, reward_id)
        if not ok:
            return None, url, []

        parsed_host = urlparse(url).hostname or ''
        if parsed_host.endswith('epicplay.in'):
            return None, "EXPIRED", []

        if st.list_type == 'active':
            RupiyoAPI.send_ongoing_signal(st.device, offer_id, reward_id)

        chain_     = []
        final_url_ = unquote(url)
        try:
            resp = requests.get(url, timeout=10, allow_redirects=True)
            for i, r in enumerate(resp.history):
                chain_.append({'step': i + 1, 'url': unquote(r.url)})
            final_url_ = unquote(resp.url)
            while chain_ and ('play.google.com' in chain_[-1]['url'] or chain_[-1]['url'].startswith('market://')):
                chain_ = chain_[:-1]
            if 'play.google.com' in final_url_ or final_url_.startswith('market://'):
                final_url_ = chain_[-1]['url'] if chain_ else unquote(url)
        except Exception as e:
            log.debug(f"Redirect chain: {e}")

        return final_url_, None, chain_

    final_url, err, chain = await run_sync(fetch_link)

    if final_url is None:
        if err == "EXPIRED":
            await safe_edit(
                query,
                "⚠️ <b>This Offer Has Expired</b>\n\nThis offer is no longer available.",
                markup=mk_offer_detail_keyboard(st.list_type, expired=True),
                pm=ParseMode.HTML,
            )
        else:
            await safe_edit(query, f"❌ {err}",
                            markup=mk_offer_detail_keyboard(st.list_type), pm=ParseMode.HTML)
        return

    steps_lines = ""
    for step in chain:
        short = trunc(step['url'], 60)
        steps_lines += f"  ↪️ Step {step['step']}: <code>{short}</code>\n"

    text = (
        "🔗 <b>Offer Link</b>\n"
        f"📌 {trunc(offer_title, 40)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if steps_lines:
        text += f"🔄 Redirect chain:\n{steps_lines}\n"
    text += (
        "✅ <b>Final URL:</b>\n"
        f'<a href="{final_url}">{trunc(final_url, 55)}</a>'
    )

    await safe_edit(query, text, markup=mk_back_list(), pm=ParseMode.HTML)

# ═══════════════════════════════════════════════════════════════════════════════
#  WITHDRAWAL FLOW
# ═══════════════════════════════════════════════════════════════════════════════

async def _show_withdraw(query, st: BotState):
    await safe_edit(query, "💸 <b>Withdrawal</b>\n\n📡 Checking store…", pm=ParseMode.HTML)

    def fetch():
        store = RupiyoAPI.get_payout_store_info(st.device)
        RupiyoAPI.refresh_wallet(st.device)
        packs = RupiyoAPI.get_payout_packs(st.device)
        return store, packs

    store, packs = await run_sync(fetch)

    if not store:
        await safe_edit(query, "❌ Failed to fetch store info.", markup=mk_back_dash(), pm=ParseMode.HTML)
        return

    status  = store.get('store_status', {}).get('status', 'UNKNOWN')
    unlocks = store.get('store_status', {}).get('unlocks_in_sec', 0)

    if status != 'OPEN':
        msg = f"⏰ <b>Store is {status}</b>\n\n"
        if unlocks > 0:
            now   = Utils.get_ist_time()
            opens = now + timedelta(seconds=unlocks)
            msg  += (
                f"Opens in: <b>{Utils.format_time_remaining(unlocks)}</b>\n"
                f"at {opens.strftime('%I:%M %p IST')}"
            )
        await safe_edit(query, msg, markup=mk_back_dash(), pm=ParseMode.HTML)
        return

    if not packs:
        await safe_edit(query, "❌ No withdrawal options available.", markup=mk_back_dash(), pm=ParseMode.HTML)
        return

    st.packs = packs
    text = (
        "💸 <b>Withdrawal</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰  Balance: <b>{Utils.format_currency(st.device.balance)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select withdrawal amount:"
    )
    await safe_edit(query, text, markup=mk_pack_keyboard(packs), pm=ParseMode.HTML)

async def cb_select_pack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    uid    = update.effective_user.id
    st     = get_state(uid)
    touch(st)
    parts  = query.data.split(':')
    pack_id = int(parts[1])
    amount  = float(parts[2])

    selected = next((p for p in st.packs if p.get('pack_id') == pack_id), None)
    if not selected:
        selected = {'pack_id': pack_id, 'payout': {'amount': amount}}

    if st.device.balance < amount:
        await safe_edit(
            query,
            "❌ <b>Not Enough Balance</b>\n\n"
            f"Have: <b>{Utils.format_currency(st.device.balance)}</b>\n"
            f"Need: ₹{amount}",
            markup=mk_back_dash(),
            pm=ParseMode.HTML,
        )
        return

    st.selected_pack = selected
    st.awaiting      = 'upi'

    await safe_edit(
        query,
        f"💸 Withdrawing <b>₹{amount}</b>\n\n"
        "📱 Send your UPI ID:\n<i>e.g. name@okhdfcbank</i>",
        pm=ParseMode.HTML,
    )

async def _handle_upi(update, context, st: BotState, text: str):
    upi      = text.strip().lower().replace(" ", "")
    ok, msg  = Utils.validate_upi_id(upi)
    if not ok:
        await update.message.reply_text(f"❌ {msg}\n\nTry again:")
        return

    pack   = st.selected_pack
    amount = pack.get('payout', {}).get('amount', 0)
    st.awaiting = None
    context.user_data['upi'] = upi

    text = (
        "📋 <b>Withdrawal Summary</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰  Amount: <b>₹{amount}</b>\n"
        f"📱  UPI: <code>{upi}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Confirm withdrawal?"
    )
    await update.message.reply_text(text, reply_markup=mk_confirm_keyboard(), parse_mode=ParseMode.HTML)

async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    uid    = update.effective_user.id
    st     = get_state(uid)
    touch(st)
    action = query.data.split(':')[1]

    if action == 'no':
        await show_dashboard(query, context, st.device, edit=True)
        return

    pack    = st.selected_pack
    pack_id = pack.get('pack_id')
    amount  = pack.get('payout', {}).get('amount', 0)
    upi     = context.user_data.get('upi', '')

    await safe_edit(query, "🔄 Processing withdrawal…", pm=ParseMode.HTML)

    ok, result = await run_sync(RupiyoAPI.purchase_payout, st.device, pack_id, upi)

    if ok:
        await run_sync(RupiyoAPI.refresh_wallet, st.device)
        await safe_edit(
            query,
            "✅ <b>Withdrawal Successful!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰  ₹{amount} → <code>{upi}</code>\n"
            f"💳  New Balance: <b>{Utils.format_currency(st.device.balance)}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            markup=mk_back_dash(),
            pm=ParseMode.HTML,
        )
    else:
        await safe_edit(
            query,
            f"❌ <b>Withdrawal Failed</b>\n\n<code>{result[:300]}</code>",
            markup=mk_back_dash(),
            pm=ParseMode.HTML,
        )

# ═══════════════════════════════════════════════════════════════════════════════
#  BACK / NAVIGATION CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_back_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    uid      = update.effective_user.id
    st       = get_state(uid)
    st.awaiting = None
    sessions = await run_sync(SessionManager.find_saved_sessions, update.effective_user.id)
    name = st.device.user_name if st.device else ""
    await safe_edit(query, build_login_text(sessions, name), markup=mk_login_keyboard(sessions), pm=ParseMode.HTML)

async def cb_back_dash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    st    = get_state(uid)
    st.awaiting = None
    if not is_session_alive(st):
        sessions     = await run_sync(SessionManager.find_saved_sessions, uid)
        phone_to_idx = {s['phone']: i for i, s in enumerate(sessions, 1)}
        phone        = st.device.user_phone if st.device else ""
        idx          = phone_to_idx.get(phone)
        cmd          = f"/{idx}" if idx else "/menu"
        name         = st.device.user_name if st.device else "your account"
        _user_states.pop(uid, None)
        await safe_edit(
            query,
            "🔒 <b>Session Expired</b>\n\n"
            f"Your session for <b>{name}</b> has expired due to inactivity.\n\n"
            f"Use <b>{cmd}</b> to start a new session.",
            pm=ParseMode.HTML,
        )
        return
    touch(st)
    await show_dashboard(query, context, st.device, edit=True)

async def cb_back_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    st    = get_state(uid)
    if not st.offers:
        await show_dashboard(query, context, st.device, edit=True)
        return
    await safe_edit(query, build_offer_list_text(st), markup=mk_offer_list_keyboard(st), pm=ParseMode.HTML)

async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _perform_relogin(sess: Dict) -> Tuple[Optional['DeviceIdentity'], str]:
    """
    Shared relogin logic used by both cb_select_account and cmd_login_n.
    Returns (device, status) where status is 'ok', 'no_refresh', 'failed', or 'banned:<msg>'.
    """
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
        if device.needs_healing():
            SelfHealer.heal_session(device, sess['file'])
        else:
            SelfHealer.sync_profile_fields(device, sess['file'])
    threading.Thread(target=_bg, daemon=True).start()

    return device, "ok"

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main menu (account selection screen). Primary entry point."""
    user = update.effective_user
    uid  = user.id
    record_user(user)

    if not is_allowed(uid):
        await update.message.reply_text("⛔ You are not allowed to use this bot.")
        return

    st       = get_state(uid)
    st.awaiting = None
    sessions = await run_sync(SessionManager.find_saved_sessions, update.effective_user.id)
    name = st.device.user_name if st.device else ""
    await update_commands(context, sessions, uid=uid)
    await update.message.reply_text(
        build_login_text(sessions, name),
        reply_markup=mk_login_keyboard(sessions),
        parse_mode=ParseMode.HTML,
    )

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deprecated — /menu is replaced by /start. Kept so old shortcuts don't hard-break."""
    await update.message.reply_text(
        "ℹ️ <b>/menu is deprecated.</b> Please use /start instead.",
        parse_mode=ParseMode.HTML,
    )
    await cmd_start(update, context)

async def cmd_login_n(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /1, /2, /3 … to instantly login as that account."""
    uid      = update.effective_user.id
    st       = get_state(uid)
    cmd      = update.message.text.strip().lstrip('/')
    if not cmd.isdigit():
        return
    n        = int(cmd)
    sessions = await run_sync(SessionManager.find_saved_sessions, update.effective_user.id)

    if n < 1 or n > len(sessions):
        await update.message.reply_text(
            f"❌ No account #{n}. You have {len(sessions)} saved account(s).")
        return

    sess = sessions[n - 1]
    await update.message.reply_text(
        f"🔄 Logging in as <b>{sess['user_name']}</b>…", parse_mode=ParseMode.HTML)

    device, status = await run_sync(_perform_relogin, sess)

    if status.startswith("banned:"):
        await update.message.reply_text(friendly_error(status[7:]))
        return
    if status != "ok" or not device:
        await update.message.reply_text("❌ Login failed. Try /start.")
        return

    st.device       = device
    st.session_file = sess['file']
    st.awaiting     = None
    start_prefetch(st, uid)

    await update.message.reply_text(
        f"✅ Logged in!\n\n{build_dashboard_text(device)}",
        reply_markup=mk_dashboard_keyboard(),
        parse_mode=ParseMode.HTML,
    )

async def global_cache_refresh_job(context):
    """Refresh each active user's offer cache individually every 5 minutes.
    Each account has its own offer catalogue so caches must never be shared."""
    active_users = [
        (uid, st) for uid, st in _user_states.items()
        if st.device and st.device.auth_token
    ]
    if not active_users:
        log.info("Cache refresh skipped — no active sessions")
        return

    def _refresh_for_user(uid: int, device: DeviceIdentity):
        for ltype in ('active', 'ongoing', 'completed'):
            try:
                offers = RupiyoAPI.get_all_offers(device, ltype)
                set_cached_offers(uid, ltype, offers)
            except Exception as e:
                log.debug(f"Cache refresh uid={uid} {ltype}: {e}")
        log.info(f"Offer cache refreshed for uid={uid}")

    for uid, st in active_users:
        threading.Thread(
            target=_refresh_for_user,
            args=(uid, st.device),
            daemon=True,
        ).start()

async def session_cleanup_job(context):
    """
    Runs every minute.
    Clears in-memory state for users inactive past their timeout and sends a notification.
    The remote/disk session is kept intact — /N will still work for fast re-login.
    """
    for uid, st in list(_user_states.items()):
        if not st.device:
            continue
        idle_secs = time.time() - st.last_activity
        if idle_secs < st.inactivity_timeout:
            continue

        name  = st.device.user_name or "your account"
        phone = st.device.user_phone or ""

        # Look up the account index scoped to this specific user
        try:
            user_sessions  = SessionManager.find_saved_sessions(uid)
            phone_to_index = {s['phone']: i for i, s in enumerate(user_sessions, 1)}
            idx = phone_to_index.get(phone)
        except Exception:
            idx = None
        cmd = f"/{idx}" if idx else "/menu"

        log.info(f"Cleanup: session expired for uid={uid} ({name}) after {idle_secs:.0f}s idle")

        _user_states.pop(uid, None)
        invalidate_user_cache(uid)   # clear stale per-user offer cache

        msg = (
            "🔒 <b>Session Expired</b>\n\n"
            f"Your session for <b>{name}</b> has expired due to inactivity.\n\n"
            f"Use <b>{cmd}</b> to start a new session."
        )
        try:
            await context.bot.send_message(uid, msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning(f"Could not notify uid={uid}: {e}")

    # Evict truly dead entries: no device and no activity for 2 hours
    dead_cutoff = time.time() - 7200
    dead = [uid for uid, st in _user_states.items()
            if not st.device and st.last_activity < dead_cutoff]
    for uid in dead:
        _user_states.pop(uid, None)
        invalidate_user_cache(uid)
    if dead:
        log.info(f"Evicted {len(dead)} stale state entries")

# ═══════════════════════════════════════════════════════════════════════════════
#  RENDER KEEP-ALIVE  —  health server so Render accepts the deployment
# ═══════════════════════════════════════════════════════════════════════════════

async def health_handler(request: "aiohttp.web.Request") -> "aiohttp.web.Response":
    from aiohttp.web import Response
    uptime = int(time.time() - START_TIME)
    body   = json.dumps({"status": "ok", "uptime_seconds": uptime})
    return Response(text=body, content_type="application/json", status=200)

async def start_web_server() -> None:
    """Bind an HTTP server on PORT so Render marks the service as live.
    On Termux / local runs, if the port is busy we just skip the health server.
    """
    from aiohttp import web
    app = web.Application()
    app.router.add_get("/",       health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    try:
        await site.start()
        log.info(f"Health server on :{PORT}")
    except OSError as e:
        if e.errno == 98:   # Address already in use
            log.warning(f"Port {PORT} busy — health server skipped (kill old process with: pkill -f r.py)")
        else:
            raise

# ═══════════════════════════════════════════════════════════════════════════════
#  ADMIN — /berserk
# ═══════════════════════════════════════════════════════════════════════════════

_BERSERK_HELP = (
    "🔧 <b>Berserk Admin Commands</b>\n\n"
    "• <code>/berserk users</code> — Show all users\n"
    "• <code>/berserk whitelist</code> &lt;id/username&gt; — Allow user\n"
    "• <code>/berserk stats</code> — Bot statistics\n"
    "• <code>/berserk broadcast</code> &lt;message&gt; — Message all users\n\n"
    "🆔 Your ID: <code>{uid}</code>\n"
    "👑 Admin Status: ✅ Verified"
)

async def _berserk_users(update: Update) -> None:
    if not _bot_users:
        await update.message.reply_text("👥 No users have started the bot yet.")
        return
    lines = [f"👥 <b>All Users</b> ({len(_bot_users)})\n"]
    for u_id, info in _bot_users.items():
        name     = info["name"]
        username = info["username"]
        wl       = "✅" if u_id in _whitelist else "🚫"
        adm      = " 👑" if u_id in ADMIN_IDS else ""
        mention  = f'<a href="tg://user?id={u_id}">{name}</a>'
        handle   = f"📛 @{username}" if username else f"📛 <a href=\"tg://user?id={u_id}\">Direct link</a>"
        lines.append(f"{mention}{adm} {wl}\n🆔 ID: <code>{u_id}</code>\n{handle}\n")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True)


async def _berserk_whitelist(update: Update, args: List) -> None:
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/berserk whitelist &lt;userid or @username&gt;</code>",
            parse_mode=ParseMode.HTML)
        return
    target = args[1].lstrip("@")
    if target.isdigit():
        t_id   = int(target)
        t_name = _bot_users.get(t_id, {}).get("name", str(t_id))
    else:
        found = [(uid2, info) for uid2, info in _bot_users.items()
                 if info["username"].lower() == target.lower()]
        if not found:
            await update.message.reply_text(
                f"❌ User @{target} not found.\nThey must have started the bot first.",
                parse_mode=ParseMode.HTML)
            return
        t_id, info = found[0]
        t_name = info["name"]
    _whitelist.add(t_id)
    if JSONBinStorage.is_enabled():
        threading.Thread(target=JSONBinStorage.save_whitelist, args=(_whitelist,), daemon=True).start()
    await update.message.reply_text(
        f"✅ <b>{t_name}</b> (<code>{t_id}</code>) has been whitelisted.",
        parse_mode=ParseMode.HTML)


async def _berserk_stats(update: Update) -> None:
    uptime_s    = int(time.time() - START_TIME)
    h, rem      = divmod(uptime_s, 3600)
    m, s_       = divmod(rem, 60)
    total       = len(_bot_users)
    wl_users    = len(_whitelist - ADMIN_IDS)
    active_sess = sum(1 for st_ in _user_states.values() if is_session_alive(st_))
    await update.message.reply_text(
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total users: <code>{total}</code>\n"
        f"✅ Whitelisted (non-admin): <code>{wl_users}</code>\n"
        f"🚫 Not whitelisted: <code>{max(0, total - wl_users - len(ADMIN_IDS))}</code>\n"
        f"🟢 Active sessions: <code>{active_sess}</code>\n"
        f"⏱ Uptime: <code>{h:02d}:{m:02d}:{s_:02d}</code>",
        parse_mode=ParseMode.HTML,
    )


async def _berserk_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, args: List) -> None:
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/berserk broadcast &lt;your message&gt;</code>",
            parse_mode=ParseMode.HTML)
        return
    broadcast = f"📩 <b>Message from Admin</b>\n\n{' '.join(args[1:])}"
    sent = failed = 0
    for u_id in list(_whitelist):
        if u_id in ADMIN_IDS:
            continue
        try:
            await context.bot.send_message(u_id, broadcast, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(
        f"📣 Broadcast done\n✅ Sent: <code>{sent}</code>  ❌ Failed: <code>{failed}</code>",
        parse_mode=ParseMode.HTML)


async def cmd_berserk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    uid  = user.id
    if not is_admin(uid):
        return
    args = context.args or []
    sub  = args[0].lower() if args else ""
    dispatch = {
        "users":     lambda: _berserk_users(update),
        "whitelist": lambda: _berserk_whitelist(update, args),
        "stats":     lambda: _berserk_stats(update),
        "broadcast": lambda: _berserk_broadcast(update, context, args),
    }
    if sub in dispatch:
        await dispatch[sub]()
    else:
        await update.message.reply_text(_BERSERK_HELP.format(uid=uid), parse_mode=ParseMode.HTML)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot uptime and active session count — useful for Render monitoring."""
    uptime_s  = int(time.time() - START_TIME)
    hours, r  = divmod(uptime_s, 3600)
    mins, sec = divmod(r, 60)
    active    = sum(1 for st in _user_states.values() if is_session_alive(st))
    uid       = update.effective_user.id
    cache_state = "fresh" if get_cached_offers(uid, "active") is not None else "empty"
    text = (
        "🤖 <b>Rupiyo Bot Status</b>\n\n"
        f"⏱ Uptime: <code>{hours:02d}:{mins:02d}:{sec:02d}</code>\n"
        f"👤 Active sessions: <code>{active}</code>\n"
        f"🗄 Cache: <code>{cache_state}</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def _notify_admins_error(context, report: str) -> None:
    """DM every admin the structured error report. Silently ignores delivery failures."""
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id, report[:4096],
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as notify_err:
            log.warning(f"Could not notify admin {admin_id}: {notify_err}")


def _build_error_report(update: object, err: Exception) -> str:
    """Return a structured, HTML-formatted error report for the admin."""
    import traceback, html

    tb_lines  = traceback.format_exception(type(err), err, err.__traceback__)
    tb_full   = "".join(tb_lines).strip()
    if len(tb_full) > 2800:
        tb_full = "(truncated)\n" + tb_full[-2800:]
    tb_escaped = html.escape(tb_full)

    user_line = data_line = text_line = chat_line = "—"

    if update and hasattr(update, "effective_user") and update.effective_user:
        u = update.effective_user
        user_line = html.escape(
            f"{u.full_name} (id={u.id}, @{u.username or 'none'})")
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        c = update.effective_chat
        chat_line = html.escape(f"{c.type} id={c.id}")
    if update and hasattr(update, "effective_message") and update.effective_message:
        m = update.effective_message
        if m.text:
            text_line = html.escape(m.text[:200])
    if update and hasattr(update, "callback_query") and update.callback_query:
        data_line = html.escape(str(update.callback_query.data or "")[:200])

    ist_now  = Utils.get_ist_time().strftime("%d %b %Y  %H:%M:%S IST")
    err_name = html.escape(type(err).__name__)
    err_msg  = html.escape(str(err)[:300])

    rows = [
        "🚨 <b>Unhandled Exception</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 <b>Time:</b>     <code>{ist_now}</code>",
        f"👤 <b>User:</b>     {user_line}",
        f"💬 <b>Chat:</b>     {chat_line}",
        f"✉️  <b>Message:</b>  <code>{text_line}</code>",
        f"🔘 <b>CB Data:</b>  <code>{data_line}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"❗ <b>Error:</b>    <code>{err_name}: {err_msg}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📋 <b>Traceback:</b>",
        f"<pre>{tb_escaped}</pre>",
    ]
    return "\n".join(rows)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Known transient errors (rate-limit, network blip, bot blocked) are handled
    quietly.  Every real unexpected error is:
      1. Logged locally.
      2. Sent as a full structured report to every admin via DM.
      3. Answered to the user with a polite support notice (no internal details).
    """
    from telegram.error import (
        NetworkError, TimedOut, RetryAfter, Forbidden,
        BadRequest as TBadRequest,
    )
    err = context.error

    # ── known / expected — handle quietly ────────────────────────────────────
    if isinstance(err, RetryAfter):
        log.warning(f"Rate-limited — retry after {err.retry_after}s")
        await asyncio.sleep(err.retry_after)
        return

    if isinstance(err, TBadRequest):
        log.warning(f"Telegram BadRequest (bad HTML/params): {err}")
        return

    if isinstance(err, (TimedOut, NetworkError)):
        log.debug(f"Transient network error (auto-retry): {err}")
        return

    if isinstance(err, Forbidden):
        if update and hasattr(update, "effective_user") and update.effective_user:
            uid = update.effective_user.id
            _user_states.pop(uid, None)
            invalidate_user_cache(uid)
            log.info(f"User {uid} blocked the bot — state cleared")
        return

    # ── unexpected error ──────────────────────────────────────────────────────
    log.exception("Unhandled exception", exc_info=err)

    report = _build_error_report(update, err)
    await _notify_admins_error(context, report)

    user_msg = (
        "⚠️ <b>Something went wrong</b>\n\n"
        "An unexpected error occurred while processing your request.\n"
        "Please contact support so we can look into it and fix it.\n\n"
        "<i>Our team has already been notified automatically.</i>"
    )
    try:
        if update and hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.answer(
                "⚠️ An error occurred. Please contact support.",
                show_alert=True,
            )
            await safe_edit(update.callback_query, user_msg, pm=ParseMode.HTML)
        elif update and hasattr(update, "effective_message") and update.effective_message:
            await update.effective_message.reply_text(
                user_msg, parse_mode=ParseMode.HTML)
    except Exception as reply_err:
        log.warning(f"Could not send error notice to user: {reply_err}")


def _schedule_jobs(app) -> None:
    app.job_queue.run_repeating(session_cleanup_job,      interval=60,  first=30)
    app.job_queue.run_repeating(global_cache_refresh_job, interval=300, first=90)


def _register_handlers(app) -> None:
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("menu",   cmd_menu))   # deprecated alias
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("berserk", cmd_berserk))
    for _i in range(1, 21):
        app.add_handler(CommandHandler(str(_i), cmd_login_n))

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
    app.add_error_handler(error_handler)


def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        log.error("Set the BOT_TOKEN environment variable before running.")
        return

    Config.initialize()

    async def post_init(app):
        if JSONBinStorage.is_enabled():
            try:
                meta = JSONBinStorage.load_meta()
                for uid_str, info in meta["users"].items():
                    try:
                        _bot_users[int(uid_str)] = info
                    except (ValueError, TypeError):
                        pass
                for wl_str in meta["whitelist"]:
                    try:
                        _whitelist.add(int(wl_str))
                    except (ValueError, TypeError):
                        pass
                _whitelist.update(ADMIN_IDS)  # admins always whitelisted
                log.info(f"Loaded {len(_bot_users)} users, {len(_whitelist)} whitelisted")
            except Exception as e:
                log.warning(f"Could not load persisted users: {e}")

        await start_web_server()

        # Set only generic base commands globally — account shortcuts (/1, /2, …)
        # are personal and must only ever be set per-chat via BotCommandScopeChat.
        base_cmds = [
            BotCommand("start",  "🚀 Main menu / account list"),
            BotCommand("status", "📊 Bot uptime and session info"),
        ]
        await app.bot.set_my_commands(base_cmds)

        # Set admin-scoped commands (includes /berserk) only for each admin chat
        admin_cmds = base_cmds + [BotCommand("berserk", "🔧 Admin panel")]
        from telegram import BotCommandScopeChat
        async def _set_admin_cmds(admin_id):
            try:
                await app.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception as e:
                log.warning(f"Could not set admin commands for {admin_id}: {e}")
        await asyncio.gather(*[_set_admin_cmds(aid) for aid in ADMIN_IDS])

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .post_init(post_init)
        .build()
    )

    _schedule_jobs(app)
    _register_handlers(app)

    print_banner(PORT, bool(JSONBIN_MASTER_KEY))

    # Graceful shutdown on SIGTERM (Render sends this on every redeploy)
    def _handle_sigterm(*_):
        log.info("SIGTERM received — shutting down cleanly")
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
    except KeyboardInterrupt:
        log.info("Bot stopped")

if __name__ == "__main__":
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
