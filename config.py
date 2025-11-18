import os
from dotenv import load_dotenv

# Only load .env file if it exists (for local development)
# This won't affect Railway since Railway uses actual environment variables
if os.path.exists('.env'):
    load_dotenv()

class Config:
    # Secret key for session management
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DATABASE_FILE = 'accounts.db'
    
    # Microsoft Graph API Configuration
    CLIENT_ID = os.environ.get('CLIENT_ID')
    CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
    AUTHORITY = "https://login.microsoftonline.com/common"
    SCOPE = ["https://graph.microsoft.com/Mail.Read", "https://graph.microsoft.com/Mail.ReadWrite", "https://graph.microsoft.com/User.Read"]
    
    # Use Railway provided URL
    REDIRECT_URI = os.environ.get('REDIRECT_URI', 'https://web-production-ce43f.up.railway.app')
    
    # Telegram configuration (optional)
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')