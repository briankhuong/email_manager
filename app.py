from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
import sqlite3
import time
from datetime import datetime
import os
import requests
from config import Config
import msal
import json
import base64
from functools import wraps
import uuid
import csv
from io import StringIO
import glob

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

# Initialize managers
from proxy_manager import ProxyManager
from telegram_alerts import TelegramNotifier
from automation_engine import AutomationEngine

proxy_manager = ProxyManager()
telegram_notifier = TelegramNotifier()
automation_engine = AutomationEngine(proxy_manager, telegram_notifier)

def init_db():
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            access_token TEXT,
            refresh_token TEXT,
            is_signed_in BOOLEAN DEFAULT 1,
            last_checked DATETIME,
            unread_count INTEGER DEFAULT 0,
            last_error TEXT,
            date_added DATETIME DEFAULT CURRENT_TIMESTAMP,
            login_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            proxy_slot TEXT,
            account_status TEXT DEFAULT 'active',
            client_id TEXT
        )
    ''')
    conn.commit()
    conn.close()

def migrate_database():
    """Add new columns to existing database safely"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    
    # Check if new columns already exist
    c.execute("PRAGMA table_info(accounts)")
    columns = [column[1] for column in c.fetchall()]
    
    # Add missing columns
    new_columns = [
        'date_added',
        'login_count', 
        'failure_count',
        'proxy_slot',
        'account_status',
        'client_id'
    ]
    
    for column in new_columns:
        if column not in columns:
            print(f"Adding column: {column}")
            if column == 'date_added':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} DATETIME')
                c.execute(f'UPDATE accounts SET {column} = datetime("now") WHERE {column} IS NULL')
            elif column in ['login_count', 'failure_count']:
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} INTEGER DEFAULT 0')
            elif column == 'proxy_slot':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} TEXT')
            elif column == 'account_status':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} TEXT DEFAULT "active"')
            elif column == 'client_id':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} TEXT')
    
    conn.commit()
    conn.close()
    print("Database migration completed!")

# Initialize database and run migration
init_db()
migrate_database()

def get_msal_app():
    return msal.ConfidentialClientApplication(
        app.config['CLIENT_ID'],
        authority=app.config['AUTHORITY'],
        client_credential=app.config['CLIENT_SECRET'],
    )

def get_token_from_code(code):
    result = get_msal_app().acquire_token_by_authorization_code(
        code,
        scopes=app.config['SCOPE'],
        redirect_uri=app.config['REDIRECT_URI']
    )
    return result

def refresh_token(account_id):
    """Refresh access token - Dynamic Client ID Support"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    # Get client_id from DB
    c.execute("SELECT email, access_token, refresh_token, client_id FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return None
        
    # Catch all 4 values: email, access_token, refresh_token, and client_id
    email, access_token, refresh_token_value, client_id_from_db = row
    
    # Skip legacy/auth accounts that can't use Graph API
    if (access_token and 
        (access_token.startswith('legacy_auth_') or 
         access_token.startswith('imap_auth_') or
         access_token.startswith('hotmail_app_password_'))):
        print(f"⚠️ Skipping token refresh for legacy account: {email}")
        conn.close()
        return None
    
    if not refresh_token_value or refresh_token_value.startswith('legacy_'):
        conn.close()
        return None
    
    try:
        # Use stored client_id or fallback to config
        target_client_id = client_id_from_db if client_id_from_db else app.config['CLIENT_ID']
        
        # If the ID isn't your own, it's a Public Client (No Secret required)
        if client_id_from_db and client_id_from_db != app.config['CLIENT_ID']:
            app_instance = msal.PublicClientApplication(
                target_client_id,
                authority=app.config['AUTHORITY']
            )
        else:
            app_instance = get_msal_app()

# We need to filter out 'offline_access' because MSAL rejects it during the refresh step
        request_scopes = [s for s in app.config['SCOPE'] if s != 'offline_access']
        
        if client_id_from_db and client_id_from_db != app.config['CLIENT_ID']:
            # Use ONLY .default for seller accounts. Do NOT include offline_access here.
            request_scopes = ['https://graph.microsoft.com/.default']
            print(f"📡 Using compatibility scopes for seller account: {email}")

        result = app_instance.acquire_token_by_refresh_token(
            refresh_token_value,
            scopes=request_scopes
        )
        
        if 'access_token' in result:
            access_token = result['access_token']
            new_refresh_token = result.get('refresh_token', refresh_token_value)
            
            c.execute('''
                UPDATE accounts 
                SET access_token = ?, refresh_token = ?, last_error = NULL,
                    last_checked = datetime('now')
                WHERE id = ?
            ''', (access_token, new_refresh_token, account_id))
            conn.commit()
            conn.close()
            
            print(f"✅ Token refreshed successfully for {email}")
            return access_token
        else:
            error_msg = f"Token refresh failed: {result.get('error_description', 'Unknown error')}"
            print(f"❌ Token refresh error for {email}: {error_msg}")
            
            c.execute('''
                UPDATE accounts 
                SET access_token = NULL, refresh_token = NULL, 
                    is_signed_in = 0, last_error = ?
                WHERE id = ?
            ''', (error_msg, account_id))
            conn.commit()
            conn.close()
            return None
            
    except Exception as e:
        error_msg = f"Token refresh exception: {str(e)}"
        print(f"❌ Token refresh exception for {email}: {error_msg}")
        
        c.execute('''
            UPDATE accounts 
            SET last_error = ?
            WHERE id = ?
        ''', (error_msg, account_id))
        conn.commit()
        conn.close()
        return None

def get_user_info(access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        response = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            return None
    except Exception as e:
        return None

def get_unread_emails_count(access_token):
    """Uses folder metadata for 100% accuracy and speed"""
    try:
        headers = {'Authorization': f'Bearer {access_token}'}
        # We target the Inbox folder directly
        url = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox'
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # This is the official count maintained by Microsoft
            return data.get('unreadItemCount', 0), None
        elif response.status_code == 401:
            return 0, "UNAUTHORIZED"
        else:
            return 0, f"Error {response.status_code}"
    except Exception as e:
        return 0, str(e)

@app.route('/')
def callback():
    """Main route that handles OAuth callback and redirects to a clean URL"""
    # 1. Handle explicit errors from Microsoft (e.g., user cancelled login)
    if 'error' in request.args:
        flash(f"Authentication error: {request.args['error']}", 'error')
        return redirect(url_for('dashboard'))
    
    # 2. Handle the Authorization Code exchange
    if 'code' in request.args:
        # Swap the code for an access token
        result = get_token_from_code(request.args['code'])
        
        if 'access_token' in result:
            access_token = result['access_token']
            user_info = get_user_info(access_token)
            
            if user_info:
                email = user_info.get('mail') or user_info.get('userPrincipalName')
                
                conn = sqlite3.connect(app.config['DATABASE_FILE'])
                c = conn.cursor()
                
                c.execute("SELECT id FROM accounts WHERE email = ?", (email,))
                existing = c.fetchone()
                
                access_token_plain = access_token
                refresh_token_plain = result.get('refresh_token')
                
                unread_count, error = get_unread_emails_count(access_token)
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                if existing:
                    c.execute('''
                        UPDATE accounts 
                        SET access_token = ?, refresh_token = ?, is_signed_in = 1, 
                            last_checked = ?, unread_count = ?, last_error = ?
                        WHERE email = ?
                    ''', (access_token_plain, refresh_token_plain, 
                          current_time, unread_count, error, email))
                else:
                    c.execute('''
                        INSERT INTO accounts (email, access_token, refresh_token, is_signed_in, last_checked, unread_count, last_error)
                        VALUES (?, ?, ?, 1, ?, ?, ?)
                    ''', (email, access_token_plain, refresh_token_plain, 
                          current_time, unread_count, error))
                
                conn.commit()
                conn.close()
                
                # Flash success message
                if unread_count > 0:
                    flash(f'Successfully added {email} - {unread_count} new emails found!', 'success')
                else:
                    flash(f'Successfully added {email} - no new emails', 'success')
                
                # CRITICAL FIX: Redirect to dashboard to clear the '?code=' from the URL
                return redirect(url_for('dashboard'))
            else:
                flash("Failed to get user information - access token may be invalid", 'error')
                return redirect(url_for('dashboard'))
        else:
            # Handle expired or invalid code error gracefully
            error_description = result.get("error_description", "Unknown error")
            flash(f'Failed to get access token: {error_description}', 'error')
            return redirect(url_for('dashboard'))
    
    # 3. Default behavior: if no code in URL, just show the dashboard
    return dashboard()


def get_status_badge(unread_count, last_error, is_signed_in, access_token):
    """Get status badge for account - INCLUDES LEGACY INDICATOR"""
    if access_token and (access_token.startswith('legacy_auth_') or access_token.startswith('imap_auth_')):
        return '🟡'  # Yellow for legacy accounts
    
    if last_error:
        return '🔴'
    elif not is_signed_in:
        return '⚫'
    elif unread_count > 0:
        return '🟢'
    else:
        return '🔵'

@app.route('/dashboard')
def dashboard():
    """Display the accounts dashboard with compact list view"""
    status_filter = request.args.get('status', 'all')
    search_query = request.args.get('search', '')
    page = int(request.args.get('page', 1))
    per_page = 50
    
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    
    query = '''
        SELECT id, email, is_signed_in, unread_count, last_checked, last_error, 
               date_added, login_count, failure_count, account_status, access_token
        FROM accounts 
        WHERE 1=1
    '''
    params = []
    
    if search_query:
        query += ' AND email LIKE ?'
        params.append(f'%{search_query}%')
    
    if status_filter == 'active':
        query += ' AND is_signed_in = 1 AND last_error IS NULL'
    elif status_filter == 'failed':
        query += ' AND last_error IS NOT NULL'
    elif status_filter == 'inactive':
        query += ' AND is_signed_in = 0'
    
    query += ' ORDER BY unread_count DESC, email ASC'
    
    count_query = f'SELECT COUNT(*) FROM ({query})'
    c.execute(count_query, params)
    total_accounts = c.fetchone()[0]
    
    query += ' LIMIT ? OFFSET ?'
    params.extend([per_page, (page - 1) * per_page])
    
    c.execute(query, params)
    
    accounts = []
    for row in c.fetchall():
        accounts.append({
            'id': row[0],
            'email': row[1],
            'is_signed_in': bool(row[2]),
            'unread_count': row[3],
            'last_checked': row[4],
            'last_error': row[5],
            'date_added': row[6],
            'login_count': row[7],
            'failure_count': row[8],
            'account_status': row[9],
            'access_token': row[10],
            'status_badge': get_status_badge(row[3], row[5], row[2], row[10])  # FIXED: Added access_token parameter
        })
    
    c.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN is_signed_in = 1 AND last_error IS NULL THEN 1 ELSE 0 END) as active,
            SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN is_signed_in = 0 THEN 1 ELSE 0 END) as inactive,
            SUM(unread_count) as total_unread
        FROM accounts
    ''')
    stats = c.fetchone()
    
    conn.close()
    
    total_pages = (total_accounts + per_page - 1) // per_page
    
    return render_template('index.html', 
                         accounts=accounts, 
                         stats={
                             'total': stats[0],
                             'active': stats[1],
                             'failed': stats[2],
                             'inactive': stats[3],
                             'total_unread': stats[4]
                         },
                         current_page=page,
                         total_pages=total_pages,
                         status_filter=status_filter,
                         search_query=search_query)

@app.route('/batch_upload', methods=['GET', 'POST'])
def batch_upload():
    """Batch upload interface for proxies and accounts"""
    if request.method == 'POST':
        if 'proxies_file' in request.files:
            proxies_file = request.files['proxies_file']
            if proxies_file.filename != '':
                try:
                    proxy_manager.load_proxies_from_file(proxies_file)
                    flash('Proxies uploaded successfully!', 'success')
                except Exception as e:
                    flash(f'Error uploading proxies: {str(e)}', 'error')
        
        if 'accounts_file' in request.files:
            accounts_file = request.files['accounts_file']
            if accounts_file.filename != '':
                try:
                    content = accounts_file.read().decode('utf-8')
                    lines = content.strip().split('\n')
                    accounts = []
                    
                    for line in lines:
                        parts = line.strip().split('|')
                        if len(parts) >= 2:
                            accounts.append({
                                'email': parts[0].strip(),
                                'password': parts[1].strip(),
                                'refresh_token': parts[2].strip() if len(parts) > 2 else '',
                                'client_id': parts[3].strip() if len(parts) > 3 else ''
                            })
                    
                    if not accounts:
                        flash('No valid accounts found in file.', 'error')
                    else:
                        upload_id = str(uuid.uuid4())
                        upload_file = f'uploads/accounts_{upload_id}.csv'
                        os.makedirs('uploads', exist_ok=True)
                        
                        with open(upload_file, 'w', newline='') as f:
                            writer = csv.DictWriter(f, fieldnames=['email', 'password', 'refresh_token', 'client_id'])
                            writer.writeheader()
                            writer.writerows(accounts)
                        
                        session['current_upload'] = upload_file
                        session['upload_count'] = len(accounts)
                        flash(f'Uploaded {len(accounts)} accounts successfully!', 'success')
                except Exception as e:
                    flash(f'Error processing file: {str(e)}', 'error')
    
    return render_template('batch_upload.html')
@app.route('/start_automation', methods=['POST'])

def start_automation():
    """Start the automation process"""
    if 'current_upload' not in session:
        return jsonify({'error': 'No accounts uploaded'}), 400
    
    upload_file = session['current_upload']
    
    try:
        import threading
        thread = threading.Thread(
            target=automation_engine.process_accounts_batch,
            args=(upload_file,)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'message': f'Automation started for {session["upload_count"]} accounts',
            'job_id': automation_engine.current_job_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/automation_status')

def automation_status():
    """Get current automation status"""
    status = automation_engine.get_status()
    
    # Ensure status includes is_running for frontend compatibility
    if 'is_running' not in status:
        status['is_running'] = automation_engine.is_running
    
    # Add timestamp to prevent caching
    status['timestamp'] = datetime.now().isoformat()
    
    return jsonify(status)

@app.route('/pause_automation', methods=['POST'])

def pause_automation():
    """Pause automation"""
    automation_engine.pause()
    return jsonify({'success': True, 'message': 'Automation paused'})

@app.route('/resume_automation', methods=['POST'])

def resume_automation():
    """Resume automation"""
    automation_engine.resume()
    return jsonify({'success': True, 'message': 'Automation resumed'})

@app.route('/download_results')

def download_results():
    """Download processing results"""
    results_file = automation_engine.get_results_file()
    if results_file and os.path.exists(results_file):
        return redirect(f'/static/{results_file}')
    else:
        flash('No results available yet', 'error')
        return redirect(url_for('dashboard'))

@app.route('/bulk_action', methods=['POST'])

def bulk_action():
    """Perform bulk actions on selected accounts"""
    account_ids = request.json.get('account_ids', [])
    action = request.json.get('action', '')
    
    if not account_ids:
        return jsonify({'error': 'No accounts selected'}), 400
    
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    
    try:
        if action == 'refresh':
            c.execute(f'''
                UPDATE accounts 
                SET last_checked = NULL 
                WHERE id IN ({','.join(['?']*len(account_ids))})
            ''', account_ids)
            flash(f'Refresh scheduled for {len(account_ids)} accounts', 'success')
            
        elif action == 'sign_out':
            c.execute(f'''
                UPDATE accounts 
                SET is_signed_in = 0 
                WHERE id IN ({','.join(['?']*len(account_ids))})
            ''', account_ids)
            flash(f'Signed out {len(account_ids)} accounts', 'success')
            
        elif action == 'sign_in':
            c.execute(f'''
                UPDATE accounts 
                SET is_signed_in = 1 
                WHERE id IN ({','.join(['?']*len(account_ids))})
            ''', account_ids)
            flash(f'Signed in {len(account_ids)} accounts', 'success')
            
        elif action == 'delete':
            c.execute(f'''
                DELETE FROM accounts 
                WHERE id IN ({','.join(['?']*len(account_ids))})
            ''', account_ids)
            flash(f'Deleted {len(account_ids)} accounts', 'success')
        
        conn.commit()
        
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
    
    return jsonify({'success': True})

@app.route('/login')

def login():
    """Redirect to Microsoft login"""
    auth_url = get_msal_app().get_authorization_request_url(
        scopes=app.config['SCOPE'],
        redirect_uri=app.config['REDIRECT_URI'],
        prompt='select_account'  # <--- ADD THIS EXACT LINE
    )
    return redirect(auth_url)

@app.route('/add_account')

def add_account():
    """Redirect to Microsoft login to add an account"""
    return redirect(url_for('login'))

@app.route('/sign_out_all')

def sign_out_all():
    """Sign out all accounts"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute('UPDATE accounts SET is_signed_in = 0')
    conn.commit()
    conn.close()
    flash('All accounts have been signed out', 'success')
    return redirect(url_for('dashboard'))

@app.route('/sign_out/<int:account_id>')

def sign_out(account_id):
    """Sign out a specific account"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute('UPDATE accounts SET is_signed_in = 0 WHERE id = ?', (account_id,))
    conn.commit()
    conn.close()
    flash('Account signed out successfully', 'success')
    return redirect(url_for('dashboard'))

@app.route('/sign_in/<int:account_id>')

def sign_in(account_id):
    """Sign in a specific account"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute('UPDATE accounts SET is_signed_in = 1 WHERE id = ?', (account_id,))
    conn.commit()
    conn.close()
    flash('Account signed in successfully', 'success')
    return redirect(url_for('dashboard'))

@app.route('/delete_account/<int:account_id>')

def delete_account(account_id):
    """Delete a specific account"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute('DELETE FROM accounts WHERE id = ?', (account_id,))
    conn.commit()
    conn.close()
    flash('Account deleted successfully', 'success')
    return redirect(url_for('dashboard'))

@app.route('/view_emails/<int:account_id>')

def view_emails(account_id): # CHANGED NAME to avoid conflicts
    """View emails - WITH DEBUGGING"""
    print(f"🔍 DEBUG: view_emails_function called with account_id: {account_id}")
    
    try:
        # Test if functions are callable
        print(f"🔍 DEBUG: Testing redirect function: {type(redirect)}")
        print(f"🔍 DEBUG: Testing url_for function: {type(url_for)}")
        print(f"🔍 DEBUG: Testing flash function: {type(flash)}")
        
        conn = sqlite3.connect(app.config['DATABASE_FILE'])
        c = conn.cursor()
        c.execute("SELECT email, access_token, refresh_token FROM accounts WHERE id = ?", (account_id,))
        row = c.fetchone()
        conn.close()
        
        if not row:
            print("❌ DEBUG: Account not found")
            flash('Account not found', 'error')
            return redirect(url_for('dashboard'))
        
# Rename refresh_token to refresh_token_val to avoid overwriting the function name
        email, access_token, refresh_token_val = row
        print(f"🔍 DEBUG: Found account - Email: {email}")
        
        # Check if it's a legacy auth account
        if access_token and (access_token.startswith('legacy_auth_') or access_token.startswith('imap_auth_')):
            print(f"⚠️ DEBUG: Legacy account - {email}")
            flash(f'This account uses legacy authentication. Use an email client to access emails for {email}.', 'info')
            return redirect(url_for('dashboard'))
        
        if not access_token:
            print("❌ DEBUG: No access token")
            flash('Not signed in or token expired', 'error')
            return redirect(url_for('dashboard'))
        
        print(f"🔍 DEBUG: Making Graph API request...")
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        url = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages'
        params = {
            '$filter': 'isRead eq false',
            '$select': 'id,subject,from,receivedDateTime,bodyPreview,hasAttachments',
            '$orderby': 'receivedDateTime DESC',
            '$top': 50
        }
        
        response = requests.get(url, headers=headers, params=params)
        print(f"🔍 DEBUG: Graph API response: {response.status_code}")
        
        # AUTO-FIX: If token is expired or was a "dummy" token from batch upload
        if response.status_code == 401:
            print(f"🔄 Token expired or dummy found for {email}. Attempting auto-refresh...")
            access_token = refresh_token(account_id)
            if access_token:
                headers['Authorization'] = f'Bearer {access_token}'
                response = requests.get(url, headers=headers, params=params)
                print(f"✅ Auto-refresh successful. New response: {response.status_code}")

        if response.status_code == 200:
            emails_data = response.json()
            emails = emails_data.get('value', [])
            print(f"🔍 DEBUG: Found {len(emails)} emails")
            
            formatted_emails = []
            for email_msg in emails:
                sender = email_msg.get('from', {}).get('emailAddress', {})
                formatted_emails.append({
                    'id': email_msg.get('id'),
                    'subject': email_msg.get('subject', 'No Subject'),
                    'from_name': sender.get('name', 'Unknown'),
                    'from_email': sender.get('address', ''),
                    'received': email_msg.get('receivedDateTime', ''),
                    'preview': email_msg.get('bodyPreview', '')[:200] + '...' if email_msg.get('bodyPreview') else 'No preview',
                    'has_attachments': email_msg.get('hasAttachments', False)
                })
            
            return render_template('emails.html', 
                                 account_email=email,
                                 account_id=account_id,
                                 emails=formatted_emails,
                                 unread_count=len(emails))
        else:
            error_msg = f'Error: {response.status_code}'
            print(f"❌ DEBUG: {error_msg}")
            flash(error_msg, 'error')
            return redirect(url_for('dashboard'))
            
    except Exception as e:
        error_msg = f'Error: {str(e)}'
        print(f"❌ DEBUG: Exception: {e}")
        import traceback
        print(f"❌ DEBUG: Traceback: {traceback.format_exc()}")
        flash(error_msg, 'error')
        return redirect(url_for('dashboard'))
@app.route('/view_email/<int:account_id>/<message_id>')

def view_email(account_id, message_id):
    """Bridge that fetches data from Microsoft and sends it to email_detail.html"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute("SELECT email, access_token FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        flash('Account not found', 'error')
        return redirect(url_for('dashboard'))

    email_address, access_token = row
    headers = {'Authorization': f'Bearer {access_token}'}

    # 1. Fetch the specific email content from Microsoft Graph
    url = f'https://graph.microsoft.com/v1.0/me/messages/{message_id}'
    response = requests.get(url, headers=headers)

    # 2. Handle token expiration automatically
    if response.status_code == 401:
        access_token = refresh_token(account_id)
        if access_token:
            headers['Authorization'] = f'Bearer {access_token}'
            response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        
        # 3. Format data to match your template's variable names
        formatted_email = {
            'subject': data.get('subject', 'No Subject'),
            'from_name': data.get('from', {}).get('emailAddress', {}).get('name'),
            'from_email': data.get('from', {}).get('emailAddress', {}).get('address'),
            'to_recipients': [r.get('emailAddress', {}).get('address') for r in data.get('toRecipients', [])],
            'received': data.get('receivedDateTime', ''),
            'body': data.get('body', {}).get('content', ''),
            'body_type': data.get('body', {}).get('contentType', 'html').lower(),
            'has_attachments': data.get('hasAttachments', False),
            'attachments': [] # Basic view; actual file data requires a separate /attachments call
        }

        return render_template('email_detail.html', 
                             email=formatted_email, 
                             account_email=email_address, 
                             account_id=account_id)
    else:
        flash(f"Error {response.status_code}: Could not fetch email content.", "error")
        return redirect(url_for('view_emails', account_id=account_id))

@app.route('/mark_as_read/<int:account_id>/<message_id>')

def mark_as_read(account_id, message_id):
    """Mark a specific email as read and redirect to view it"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute("SELECT email, access_token FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        flash('Account not found', 'error')
        return redirect(url_for('dashboard'))
    
    email, access_token = row
    
    if not access_token:
        flash('Not signed in or token expired', 'error')
        return redirect(url_for('dashboard'))
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        url = f'https://graph.microsoft.com/v1.0/me/messages/{message_id}'
        data = {
            'isRead': True
        }
        
        response = requests.patch(url, headers=headers, json=data)
        
        if response.status_code == 401:
            new_access_token = refresh_token(account_id)
            if new_access_token:
                headers['Authorization'] = f'Bearer {new_access_token}'
                response = requests.patch(url, headers=headers, json=data)
        
        if response.status_code in [200, 204]:
            return redirect(url_for('view_email', account_id=account_id, message_id=message_id))
        else:
            flash(f'Error marking email as read: {response.status_code}', 'error')
            return redirect(url_for('view_email', account_id=account_id, message_id=message_id))
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('view_email', account_id=account_id, message_id=message_id))

@app.route('/telegram_settings', methods=['GET', 'POST'])

def telegram_settings():
    """Configure Telegram notifications"""
    if request.method == 'POST':
        bot_token = request.form.get('bot_token')
        chat_id = request.form.get('chat_id')
        
        try:
            telegram_notifier.setup(bot_token, chat_id)
            flash('Telegram settings saved successfully!', 'success')
        except Exception as e:
            flash(f'Error saving Telegram settings: {str(e)}', 'error')
    
    return render_template('telegram_settings.html')

@app.route('/debug-automation')

def debug_automation():
    """Debug automation engine status"""
    import inspect
    from automation_engine import AutomationEngine
    
    result = {
        'automation_engine_methods': [],
        'has_process_accounts_batch': False,
        'automation_engine_file_exists': os.path.exists('automation_engine.py')
    }
    
    try:
        methods = [method for method in dir(AutomationEngine) if not method.startswith('_')]
        result['automation_engine_methods'] = methods
        result['has_process_accounts_batch'] = 'process_accounts_batch' in methods
        
        if os.path.exists('automation_engine.py'):
            with open('automation_engine.py', 'r') as f:
                content = f.read()
                result['file_has_method'] = 'def process_accounts_batch' in content
                result['file_size'] = len(content)
    except Exception as e:
        result['error'] = str(e)
    
    return jsonify(result)

@app.route('/debug-cache')

def debug_cache():
    """Debug cache status"""
    return jsonify({
        'automation_engine_status': automation_engine.get_status(),
        'is_running': automation_engine.is_running,
        'is_paused': automation_engine.is_paused,
        'current_job_id': automation_engine.current_job_id,
        'session_data': {
            'current_upload': session.get('current_upload'),
            'upload_count': session.get('upload_count')
        }
    })

@app.route('/debug-status')

def debug_status():
    """Debug automation status in detail"""
    status = automation_engine.get_status()
    return jsonify({
        'automation_status': status,
        'engine_state': {
            'is_running': automation_engine.is_running,
            'is_paused': automation_engine.is_paused,
            'current_job_id': automation_engine.current_job_id
        },
        'session_state': {
            'current_upload': session.get('current_upload'),
            'upload_count': session.get('upload_count')
        },
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/automation/start', methods=['POST'])

def api_start_automation():
    """API endpoint for starting automation - ALIAS FOR /start_automation"""
    return start_automation()

@app.route('/reset-automation')

def reset_automation():
    """Reset automation engine status"""
    automation_engine.is_running = False
    automation_engine.is_paused = False
    automation_engine.status = {}
    session.pop('current_upload', None)
    session.pop('upload_count', None)
    flash('Automation status reset successfully!', 'success')
    return redirect(url_for('batch_upload'))

@app.route('/debug-routes')

def debug_routes():
    """Debug all routes to find conflicts"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods),
            'rule': str(rule)
        })
    
    # Check for duplicate endpoints
    endpoints = {}
    for route in routes:
        endpoint = route['endpoint']
        if endpoint in endpoints:
            endpoints[endpoint].append(route)
        else:
            endpoints[endpoint] = [route]
    
    duplicates = {k: v for k, v in endpoints.items() if len(v) > 1}
    
    return jsonify({
        'all_routes': routes,
        'duplicate_endpoints': duplicates,
        'view_emails_function': str(view_emails) if 'view_emails' in globals() else 'NOT FOUND'
    })

@app.route('/nuclear-reset')

def nuclear_reset():
    """COMPLETE reset of automation state"""
    automation_engine.is_running = False
    automation_engine.is_paused = False
    automation_engine.current_job_id = None
    automation_engine.status = {}
    
    session.pop('current_upload', None)
    session.pop('upload_count', None)
    
    # Clear any file locks
    for file in glob.glob('uploads/accounts_*.csv'):
        try:
            os.remove(file)
        except:
            pass
    
    flash('🚀 COMPLETE SYSTEM RESET - Cache cleared!', 'success')
    return redirect(url_for('batch_upload'))

# --- MOVE THIS BLOCK UP ---
@app.route('/refresh_account/<int:account_id>')
def refresh_account(account_id):
    """Manual sync for a specific account"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    # Fetch the token and email needed for the sync
    c.execute("SELECT access_token, email FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return redirect(url_for('dashboard'))

    access_token, email = row
    count, error = get_unread_emails_count(access_token)
    
    if error == "UNAUTHORIZED":
        # This will use your new compatibility logic in refresh_token()
        access_token = refresh_token(account_id)
        if access_token:
            count, error = get_unread_emails_count(access_token)

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('''
        UPDATE accounts 
        SET unread_count = ?, last_checked = ?, last_error = ? 
        WHERE id = ?
    ''', (count, current_time, error, account_id))
    
    conn.commit()
    conn.close()
    flash(f'Account {email} synced successfully', 'success')
    return redirect(url_for('dashboard'))

# --- THIS SHOULD BE THE VERY LAST THING IN THE FILE ---
if __name__ == '__main__':
    os.makedirs('uploads', exist_ok=True)
    os.makedirs('exports', exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    
    init_db()
    
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)