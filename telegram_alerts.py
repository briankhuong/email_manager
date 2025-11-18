import requests
import os
import json

class TelegramNotifier:
    def __init__(self):
        self.bot_token = None
        self.chat_id = None
        self.load_config()
    
    def load_config(self):
        """Load Telegram configuration"""
        config_file = 'telegram_config.json'
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = json.load(f)
                self.bot_token = config.get('bot_token')
                self.chat_id = config.get('chat_id')
    
    def setup(self, bot_token, chat_id):
        """Setup Telegram bot"""
        self.bot_token = bot_token
        self.chat_id = chat_id
        
        # Save configuration
        config = {
            'bot_token': bot_token,
            'chat_id': chat_id
        }
        
        with open('telegram_config.json', 'w') as f:
            json.dump(config, f)
        
        # Test configuration
        return self.send_alert("✅ Telegram notifications configured successfully!")
    
    def send_alert(self, message):
        """Send alert via Telegram"""
        if not self.bot_token or not self.chat_id:
            print(f"Telegram alert (not sent - not configured): {message}")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"Failed to send Telegram alert: {e}")
            return False
    
    def send_captcha_alert(self, email, proxy, worker_id):
        """Send captcha alert"""
        message = (
            f"🚨 <b>CAPTCHA DETECTED</b>\n"
            f"Account: {email}\n"
            f"Worker: {worker_id}\n"
            f"Proxy: {proxy}\n"
            f"Action required: Please solve captcha manually"
        )
        return self.send_alert(message)
    
    def send_progress_update(self, processed, total, success, failures):
        """Send progress update"""
        percentage = (processed / total) * 100 if total > 0 else 0
        message = (
            f"📊 <b>Automation Progress</b>\n"
            f"Processed: {processed}/{total} ({percentage:.1f}%)\n"
            f"Success: {success} | Failed: {failures}\n"
            f"Success rate: {(success/processed*100):.1f}%" if processed > 0 else ""
        )
        return self.send_alert(message)
    
    def is_configured(self):
        """Check if Telegram is configured"""
        return bool(self.bot_token and self.chat_id)