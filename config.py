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
    # offline_access is REQUIRED to get a refresh_token back from Microsoft
    SCOPE = [
        "https://graph.microsoft.com/Mail.Read", 
        "https://graph.microsoft.com/User.Read", 
        "offline_access"
    ]
    # Use Railway provided URL
    # This ensures that if you are running locally, it uses localhost. 
    # If you are on Railway, it uses the environment variable.
    REDIRECT_URI = os.environ.get('REDIRECT_URI', 'http://localhost:5001')
    # Telegram configuration (optional)
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')