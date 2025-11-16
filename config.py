import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    DATABASE_FILE = 'accounts.db'
    
    # Microsoft Graph API Configuration
    CLIENT_ID = os.environ.get('CLIENT_ID')
    CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
    AUTHORITY = "https://login.microsoftonline.com/common"
    SCOPE = ["https://graph.microsoft.com/Mail.Read", "https://graph.microsoft.com/Mail.ReadWrite", "https://graph.microsoft.com/User.Read"]
    # Railway will provide the REDIRECT_URI via environment variable
    REDIRECT_URI = os.environ.get('REDIRECT_URI', 'http://localhost:5001')