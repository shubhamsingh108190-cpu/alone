from flask import Flask, request, jsonify
import threading
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global bot instance
bot_instance = None
bot_thread = None

@app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>🎫 SHEIN Voucher Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                text-align: center;
                padding: 50px;
                margin: 0;
            }
            .container {
                max-width: 800px;
                margin: 0 auto;
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                padding: 40px;
                border-radius: 20px;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            }
            h1 {
                font-size: 3em;
                margin-bottom: 20px;
                color: white;
            }
            .status {
                background: rgba(0, 255, 0, 0.2);
                padding: 15px;
                border-radius: 10px;
                margin: 20px 0;
                font-size: 1.2em;
            }
            .stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin: 30px 0;
            }
            .stat-card {
                background: rgba(255, 255, 255, 0.15);
                padding: 20px;
                border-radius: 10px;
                text-align: center;
            }
            .stat-value {
                font-size: 2em;
                font-weight: bold;
                margin: 10px 0;
            }
            .button {
                display: inline-block;
                background: white;
                color: #667eea;
                padding: 15px 30px;
                margin: 10px;
                border-radius: 50px;
                text-decoration: none;
                font-weight: bold;
                font-size: 1.1em;
                transition: all 0.3s;
                border: none;
                cursor: pointer;
            }
            .button:hover {
                transform: translateY(-3px);
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.3);
            }
            .button.start {
                background: #4CAF50;
                color: white;
            }
            .button.stop {
                background: #f44336;
                color: white;
            }
            .telegram-link {
                margin-top: 30px;
                padding: 15px;
                background: #0088cc;
                border-radius: 10px;
                display: inline-block;
            }
            .telegram-link a {
                color: white;
                text-decoration: none;
                font-weight: bold;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎫 SHEIN Voucher Bot</h1>
            <p>⚡ Continuous Auto-Collection Bot</p>
            
            <div class="status" id="status">
                <span id="status-text">Loading...</span>
            </div>
            
            <div class="stats" id="stats">
                <!-- Stats will be loaded here -->
            </div>
            
            <div>
                <button class="button start" onclick="startBot()">🚀 Start Bot</button>
                <button class="button stop" onclick="stopBot()">⏹️ Stop Bot</button>
                <button class="button" onclick="refreshStats()">🔄 Refresh</button>
            </div>
            
            <div class="telegram-link">
                <p>📱 Use the bot on Telegram:</p>
                <a href="https://t.me/{your_bot_username}" target="_blank">Open Telegram Bot</a>
            </div>
            
            <div style="margin-top: 40px; font-size: 0.9em; opacity: 0.8;">
                <p>Bot runs 24/7 on Render • Auto-collection • Real-time vouchers</p>
            </div>
        </div>
        
        <script>
            function updateStatus() {
                fetch('/status')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('status-text').textContent = data.status;
                        document.getElementById('status').style.background = 
                            data.status.includes('Running') ? 'rgba(0, 255, 0, 0.2)' : 
                            data.status.includes('Stopped') ? 'rgba(255, 0, 0, 0.2)' : 
                            'rgba(255, 255, 0, 0.2)';
                        
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
                    });
            }
            
            function startBot() {
                fetch('/start', { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        alert(data.message);
                        updateStatus();
                    });
            }
            
            function stopBot() {
                fetch('/stop', { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        alert(data.message);
                        updateStatus();
                    });
            }
            
            function refreshStats() {
                updateStatus();
            }
            
            // Update every 10 seconds
            setInterval(updateStatus, 10000);
            
            // Initial load
            updateStatus();
        </script>
    </body>
    </html>
    """

@app.route('/status')
def status():
    """Get bot status"""
    try:
        if bot_instance:
            stats = {
                'status': 'Bot Running',
                'users': len(bot_instance.users) if hasattr(bot_instance, 'users') else 0,
                'vouchers': len(bot_instance.vouchers) if hasattr(bot_instance, 'vouchers') else 0,
                'numbers': len(bot_instance.numbers) if hasattr(bot_instance, 'numbers') else 0,
                'active': sum(1 for v in bot_instance.continuous_mode.values() if v) 
                         if hasattr(bot_instance, 'continuous_mode') else 0
            }
        else:
            stats = {
                'status': 'Bot Stopped',
                'users': 0,
                'vouchers': 0,
                'numbers': 0,
                'active': 0
            }
        
        return jsonify(stats)
    except Exception as e:
        return jsonify({'status': f'Error: {str(e)}', 'users': 0, 'vouchers': 0, 'numbers': 0, 'active': 0})

@app.route('/start', methods=['POST'])
def start_bot():
    """Start the bot"""
    global bot_instance, bot_thread
    
    try:
        from bot import SheinVoucherBot
        
        if bot_instance and bot_instance.is_running:
            return jsonify({'message': 'Bot is already running!'})
        
        # Create and start bot
        bot_instance = SheinVoucherBot()
        
        def run_bot():
            import asyncio
            asyncio.run(bot_instance.run())
        
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        
        logger.info("✅ Bot started via web interface")
        return jsonify({'message': 'Bot started successfully!'})
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        return jsonify({'message': f'Error starting bot: {str(e)}'}), 500

@app.route('/stop', methods=['POST'])
def stop_bot():
    """Stop the bot"""
    global bot_instance
    
    try:
        if bot_instance:
            bot_instance.is_running = False
            # Stop all continuous modes
            if hasattr(bot_instance, 'continuous_mode'):
                for user_id in list(bot_instance.continuous_mode.keys()):
                    bot_instance.continuous_mode[user_id] = False
                    if hasattr(bot_instance, 'stop_continuous'):
                        bot_instance.stop_continuous[user_id] = True
            
            bot_instance = None
            logger.info("⏹️ Bot stopped via web interface")
            return jsonify({'message': 'Bot stopped successfully!'})
        else:
            return jsonify({'message': 'Bot is not running!'})
            
    except Exception as e:
        logger.error(f"Error stopping bot: {e}")
        return jsonify({'message': f'Error stopping bot: {str(e)}'}), 500

@app.route('/health')
def health():
    """Health check endpoint for Render"""
    return jsonify({'status': 'healthy', 'service': 'shein-voucher-bot'})

@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram webhook endpoint"""
    # This would handle Telegram webhooks if using webhook method
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)