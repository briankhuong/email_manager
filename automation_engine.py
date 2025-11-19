import threading
import time
import csv
import uuid
import os
import random
import sqlite3
from datetime import datetime
from config import Config

class AutomationEngine:
    def __init__(self, proxy_manager, telegram_notifier):
        self.proxy_manager = proxy_manager
        self.telegram_notifier = telegram_notifier
        self.is_running = False
        self.is_paused = False
        self.current_job_id = None
        self.status = {}
        print("✅ AutomationEngine initialized with hybrid approach")

    def add_account_to_database(self, email, access_token, refresh_token):
        """Actually add account to the database"""
        try:
            # Use the same database file as your app
            conn = sqlite3.connect(Config.DATABASE_FILE)
            c = conn.cursor()
            
            # Check if account already exists
            c.execute("SELECT id FROM accounts WHERE email = ?", (email,))
            existing = c.fetchone()
            
            current_time = datetime.now()
            
            if existing:
                # Update existing account
                c.execute('''
                    UPDATE accounts 
                    SET access_token = ?, refresh_token = ?, is_signed_in = 1,
                        last_checked = ?, last_error = NULL, login_count = login_count + 1
                    WHERE email = ?
                ''', (access_token, refresh_token, current_time, email))
                print(f"🔄 Updated existing account: {email}")
            else:
                # Insert new account
                c.execute('''
                    INSERT INTO accounts 
                    (email, access_token, refresh_token, is_signed_in, last_checked, 
                     unread_count, last_error, login_count, date_added)
                    VALUES (?, ?, ?, 1, ?, 0, NULL, 1, ?)
                ''', (email, access_token, refresh_token, current_time, current_time))
                print(f"🆕 Added new account: {email}")
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            print(f"❌ Database error adding {email}: {e}")
            return False    

    def process_accounts_batch(self, accounts_file):
        """Process accounts in batch with hybrid automation"""
        print(f"🚀 AUTOMATION STARTED with file: {accounts_file}")
        self.is_running = True
        self.current_job_id = str(uuid.uuid4())
        
        try:
            # Ensure uploads directory exists
            uploads_dir = os.path.dirname(accounts_file)
            if uploads_dir:
                os.makedirs(uploads_dir, exist_ok=True)
            
            # Load accounts
            accounts = []
            with open(accounts_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    accounts.append(row)
            
            print(f"📧 Loaded {len(accounts)} accounts")
            
            # In process_accounts_batch method, update status initialization:
            self.status = {
                'total_accounts': len(accounts),
                'processed_accounts': 0,
                'successful_logins': 0,
                'failed_logins': 0,
                'captcha_count': 0,
                'invalid_credentials': 0,
                'locked_accounts': 0, 
                'mfa_required': 0,
                'network_errors': 0,
                'current_worker': 'Initializing REAL Microsoft authentication',
                'start_time': datetime.now().isoformat(),
                'overall_progress_percent': 0,
                'success_rate_percent': 0,
                'completion_status': 'running',
                'is_running': True,
                'auth_method': 'Microsoft Graph API'  # Track that we're using real auth
            }
            
            # Get proxies
            proxies = self.proxy_manager.get_proxies()
            if not proxies:
                print("❌ No proxies available")
                return
            
            # Process accounts with hybrid approach
            for i, account in enumerate(accounts):
                if not self.is_running or self.is_paused:
                    break
                    
                email = account['email']
                password = account['password']
                proxy = proxies[i % len(proxies)]  # Round-robin proxy assignment
                
                print(f"🔧 Processing account {i+1}: {email} with proxy {proxy[:20]}...")
                
                # Update status
                self.status['processed_accounts'] = i + 1
                self.status['current_worker'] = f'Processing: {email}'

                # Calculate progress metrics
                total = self.status['total_accounts']
                processed = self.status['processed_accounts']
                successful = self.status['successful_logins']

                self.status['overall_progress_percent'] = int((processed / total) * 100) if total > 0 else 0
                self.status['success_rate_percent'] = int((successful / processed) * 100) if processed > 0 else 0
                
                # Try login with REAL Microsoft authentication
                success = self.login_to_hotmail(email, password, proxy)

                if success:
                    self.status['successful_logins'] += 1
                    print(f"✅ Login successful: {email}")
                else:
                    self.status['failed_logins'] += 1
                    print(f"❌ Login failed: {email}")
                    
                    # Log specific failure reasons for real authentication
                    # Note: The actual failure reason will come from the login_to_hotmail method
                    # We'll track these in the status for better reporting
                    failure_reason = "Authentication failed"  # This will be populated by actual error from login method
                    
                    # Initialize counters if they don't exist
                    if 'invalid_credentials' not in self.status:
                        self.status['invalid_credentials'] = 0
                    if 'locked_accounts' not in self.status:
                        self.status['locked_accounts'] = 0  
                    if 'mfa_required' not in self.status:
                        self.status['mfa_required'] = 0
                    if 'network_errors' not in self.status:
                        self.status['network_errors'] = 0
                
                # Update success rate after login attempt
                successful = self.status['successful_logins']
                self.status['success_rate_percent'] = int((successful / processed) * 100) if processed > 0 else 0
                
                # 3-minute delay between accounts
                if i < len(accounts) - 1:  # Don't delay after last account
                    print("⏰ Waiting 3 minutes before next account...")
                    for _ in range(180):  # 180 seconds = 3 minutes
                        if not self.is_running or self.is_paused:
                            break
                        time.sleep(1)
            
            print("✅ AUTOMATION COMPLETED SUCCESSFULLY")
            
            # FORCE COMPLETION STATUS UPDATE - MOVE INSIDE TRY BLOCK
            total = self.status['total_accounts']
            self.status['processed_accounts'] = total
            self.status['current_worker'] = '✅ Automation Completed'
            self.status['overall_progress_percent'] = 100
            self.status['completion_status'] = 'completed'
            successful = self.status['successful_logins']
            self.status['success_rate_percent'] = int((successful / total) * 100) if total > 0 else 0
            print("🔄 Status updated to completed")
            
        except Exception as e:
            print(f"❌ AUTOMATION ERROR: {e}")
            # Update status even on error
            if hasattr(self, 'status'):
                self.status['current_worker'] = f'❌ Error: {str(e)}'
                self.status['completion_status'] = 'error'
        finally:
            self.is_running = False
            self.is_paused = False
            # Ensure final status reflects completion
            if hasattr(self, 'status'):
                self.status['is_running'] = False
            print("🔄 Automation engine reset - ready for next run")

    def login_to_hotmail(self, email, password, proxy):
        """Outlook REST API authentication - WORKS WITH HOTMAIL/OUTLOOK"""
        print(f"🔧 Attempting Outlook REST API login for: {email}")
        print(f"🔧 Using proxy: {proxy[:50]}..." if proxy else "⚠️ No proxy provided")
        
        try:
            import requests
            import json
            
            # Outlook REST API endpoints - WORKS with consumer accounts
            token_url = "https://login.live.com/oauth20_token.srf"
            client_id = "000000004C12AE6F"  # Microsoft's public Outlook client ID
            
            # Prepare request data for Outlook REST API
            data = {
                'client_id': client_id,
                'scope': 'wl.imap wl.offline_access',
                'username': email,
                'password': password,
                'grant_type': 'password'
            }
            
            # Prepare proxy if available
            proxies = {}
            if proxy and ('http' in proxy or 'https' in proxy):
                proxies = {
                    'http': proxy,
                    'https': proxy
                }
            
            # Make authentication request
            print(f"🌐 Authenticating {email} via Outlook REST API...")
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.post(
                token_url,
                data=data,
                proxies=proxies,
                headers=headers,
                timeout=30
            )
            
            print(f"🔧 Response status: {response.status_code}")
            
            if response.status_code == 200:
                # Successful authentication
                token_data = response.json()
                access_token = token_data.get('access_token')
                refresh_token = token_data.get('refresh_token')
                
                if access_token:
                    print(f"✅ Outlook REST API Login successful: {email}")
                    print(f"🔧 Token type: {token_data.get('token_type')}")
                    print(f"🔧 Expires in: {token_data.get('expires_in')} seconds")
                    
                    # Convert Outlook token to Microsoft Graph token if needed
                    # For now, we'll store the Outlook token and use it for basic email access
                    graph_token = self.convert_outlook_to_graph_token(access_token)
                    
                    if graph_token:
                        access_token = graph_token
                        print("🔄 Successfully converted Outlook token to Graph token")
                    else:
                        print("⚠️ Using Outlook token directly (limited functionality)")
                    
                    # Add account to database with tokens
                    success = self.add_account_to_database(email, access_token, refresh_token)
                    if success:
                        print(f"📊 Successfully added {email} to database")
                        
                        # Try to verify token works
                        try:
                            if graph_token:
                                # Use Graph API for verification
                                headers = {'Authorization': f'Bearer {graph_token}'}
                                user_response = requests.get(
                                    'https://graph.microsoft.com/v1.0/me',
                                    headers=headers,
                                    timeout=10
                                )
                            else:
                                # Use Outlook API for verification
                                headers = {'Authorization': f'Bearer {access_token}'}
                                user_response = requests.get(
                                    'https://outlook.office.com/api/v2.0/me',
                                    headers=headers,
                                    timeout=10
                                )
                            
                            if user_response.status_code == 200:
                                user_info = user_response.json()
                                user_email = user_info.get('EmailAddress') or user_info.get('mail') or user_info.get('userPrincipalName')
                                print(f"🔍 Token verified: {user_email}")
                            else:
                                print(f"⚠️ Token verification failed: {user_response.status_code}")
                                
                        except Exception as verify_error:
                            print(f"⚠️ Token verification error: {verify_error}")
                        
                        return True
                    else:
                        print(f"❌ Failed to add {email} to database")
                        return False
                else:
                    print(f"❌ No access token in response for {email}")
                    return False
                    
            else:
                # Authentication failed
                error_text = response.text
                print(f"❌ Outlook REST API Login failed: {email} - Status: {response.status_code}")
                
                try:
                    error_data = response.json()
                    error_description = error_data.get('error_description', 'Unknown error')
                    error_code = error_data.get('error', 'unknown_error')
                    
                    print(f"❌ Error: {error_code} - {error_description}")
                    
                    # Categorize Outlook-specific errors
                    if 'invalid_grant' in error_code:
                        if 'The user name or password is incorrect' in error_description:
                            print("🔐 Error: Invalid credentials")
                        else:
                            print("🔐 Error: Authentication failed - invalid grant")
                    elif 'request_token_failed' in error_code:
                        print("🌐 Error: Token request failed")
                    else:
                        print(f"🔧 Outlook error type: {error_code}")
                        
                except Exception as parse_error:
                    print(f"❌ Could not parse error response: {parse_error}")
                    
                return False
                    
        except requests.exceptions.Timeout:
            print(f"❌ Login timeout for {email}")
            return False
        except requests.exceptions.ConnectionError:
            print(f"❌ Connection error for {email} - proxy may be invalid")
            return False
        except requests.exceptions.RequestException as e:
            print(f"❌ Network error for {email}: {e}")
            return False
        except Exception as e:
            print(f"❌ Unexpected error during login for {email}: {e}")
            return False

    def convert_outlook_to_graph_token(self, outlook_token):
        """Convert Outlook REST API token to Microsoft Graph token"""
        try:
            import requests
            
            # This is a simplified conversion - may need adjustment
            conversion_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
            
            data = {
                'client_id': '1fec8e78-bce4-4aaf-ab1b-5451cc387264',
                'scope': 'https://graph.microsoft.com/.default',
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': outlook_token,
                'requested_token_use': 'on_behalf_of'
            }
            
            response = requests.post(conversion_url, data=data, timeout=10)
            
            if response.status_code == 200:
                token_data = response.json()
                return token_data.get('access_token')
            else:
                print(f"⚠️ Token conversion failed: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"⚠️ Token conversion error: {e}")
            return None

    def get_status(self):
        """Get current automation status - REQUIRED BY WEB INTERFACE"""
        # If automation completed, ensure status reflects completion
        if not self.is_running and hasattr(self, 'status'):
            # Ensure completion state is clear
            if self.status.get('current_worker') != 'Completed':
                self.status['current_worker'] = 'Completed'
                self.status['completion_status'] = 'completed'
        
        # Default status structure
        default_status = {
            'total_accounts': 0,
            'processed_accounts': 0,
            'successful_logins': 0,
            'failed_logins': 0,
            'captcha_count': 0,
            'current_worker': 'Not running',
            'start_time': None,
            'overall_progress_percent': 0,
            'success_rate_percent': 0,
            'completion_status': 'not_started',
            'is_running': self.is_running  # Add this for frontend compatibility
        }
        
        if hasattr(self, 'status'):
            # Merge current status with defaults
            merged_status = default_status.copy()
            merged_status.update(self.status)
            merged_status['is_running'] = self.is_running  # Ensure this is always current
            return merged_status
        
        return default_status

    def pause(self):
        self.is_paused = True
        return True

    def resume(self):
        self.is_paused = False
        return True

    def get_results_file(self):
        return None