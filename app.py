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

# Simple password protection
APP_USERNAME = "lbasapp"
APP_PASSWORD = "Ngoc@123"

def check_auth(auth_header):
    """Check if authorized"""
    if not auth_header:
        return False
    try:
        auth_type, auth_string = auth_header.split(' ', 1)
        if auth_type.lower() != 'basic':
            return False
        decoded = base64.b64decode(auth_string).decode('utf-8')
        username, password = decoded.split(':', 1)
        return username == APP_USERNAME and password == APP_PASSWORD
    except:
        return False

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('authenticated'):
            return f(*args, **kwargs)
        auth_header = request.headers.get('Authorization')
        if check_auth(auth_header):
            session['authenticated'] = True
            return f(*args, **kwargs)
        return Response(
            'Email Manager - Login Required',
            401,
            {'WWW-Authenticate': 'Basic realm="Email Manager Login"'}
        )
    return decorated

@app.before_request
def require_login():
    if request.endpoint and ('static' in request.endpoint or request.endpoint == 'logout'):
        return
    return login_required(lambda: None)()

@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.pop('authenticated', None)
    return redirect('/')

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
            account_status TEXT DEFAULT 'active'
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
        'account_status'
    ]
    
    for column in new_columns:
        if column not in columns:
            print(f"Adding column: {column}")
            if column == 'date_added':
                # SQLite doesn't allow CURRENT_TIMESTAMP for new columns in existing tables
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} DATETIME')
                # Set default value for existing rows
                c.execute(f'UPDATE accounts SET {column} = datetime("now") WHERE {column} IS NULL')
            elif column in ['login_count', 'failure_count']:
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} INTEGER DEFAULT 0')
            elif column == 'proxy_slot':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} TEXT')
            elif column == 'account_status':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} TEXT DEFAULT "active"')
    
    conn.commit()
    conn.close()
    print("Database migration completed!")

# Initialize database and run migration
init_db()
migrate_database()

# ==================== EXISTING FUNCTIONALITY (PRESERVED) ====================

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
    """Refresh access token using refresh token - FIXED VERSION"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute("SELECT email, refresh_token FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    
    if not row or not row[1]:
        conn.close()
        return None
        
    email, refresh_token_value = row
    
    if not refresh_token_value:
        conn.close()
        return None
    
    try:
        # Use the confidential client app correctly
        app_instance = get_msal_app()
        
        # Use refresh token to get new tokens
        result = app_instance.acquire_token_by_refresh_token(
            refresh_token_value,
            scopes=app.config['SCOPE']
        )
        
        if 'access_token' in result:
            access_token = result['access_token']
            # Get new refresh token if provided, otherwise keep old one
            new_refresh_token = result.get('refresh_token', refresh_token_value)
            
            # Update tokens in database
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
            # Token refresh failed - clear invalid tokens
            error_msg = f"Token refresh failed: {result.get('error_description', 'Unknown error')}"
            print(f"❌ Token refresh error for {email}: {error_msg}")
            
            # Clear invalid tokens and mark as signed out
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
        
        # Update error in database
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
    try:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        url = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages'
        params = {
            '$filter': 'isRead eq false',
            '$count': 'true',
            '$select': 'id,subject,receivedDateTime,from',
            '$top': 50
        }
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            unread_count = len(data.get('value', []))
            return unread_count, None
        else:
            error_msg = f"Graph API error: {response.status_code} - {response.text}"
            return 0, error_msg
            
    except Exception as e:
        error_msg = f"Exception: {str(e)}"
        return 0, error_msg

@app.route('/')
def callback():
    """Main route that handles both OAuth callback and dashboard display"""
    if 'error' in request.args:
        flash(f"Authentication error: {request.args['error']}", 'error')
        return redirect(url_for('dashboard'))
    
    if 'code' in request.args:
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
                current_time = datetime.now()
                
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
                
                if unread_count > 0:
                    flash(f'Successfully added {email} - {unread_count} new emails found!', 'success')
                else:
                    flash(f'Successfully added {email} - no new emails', 'success')
            else:
                flash("Failed to get user information - access token may be invalid", 'error')
        else:
            error_description = result.get("error_description", "Unknown error")
            flash(f'Failed to get access token: {error_description}', 'error')
    
    return dashboard()

# ==================== ENHANCED DASHBOARD WITH COMPACT VIEW ====================

def get_status_badge(unread_count, last_error, is_signed_in):
    """Get status badge for account"""
    if last_error:
        return '🔴'
    elif not is_signed_in:
        return '⚫'
    elif unread_count > 0:
        return '🟢'
    else:
        return '🔵'

@app.route('/dashboard')
@login_required
def dashboard():
    """Display the accounts dashboard with compact list view"""
    # Get filter parameters
    status_filter = request.args.get('status', 'all')
    search_query = request.args.get('search', '')
    page = int(request.args.get('page', 1))
    per_page = 50
    
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    
    # Build query with filters
    query = '''
        SELECT id, email, is_signed_in, unread_count, last_checked, last_error, 
               date_added, login_count, failure_count, account_status
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
    
    # Get total count for pagination
    count_query = f'SELECT COUNT(*) FROM ({query})'
    c.execute(count_query, params)
    total_accounts = c.fetchone()[0]
    
    # Add pagination
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
            'status_badge': get_status_badge(row[3], row[5], row[2])
        })
    
    # Get dashboard stats
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

# ==================== NEW BATCH PROCESSING ROUTES ====================

@app.route('/batch_upload', methods=['GET', 'POST'])
@login_required
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
                    # Use CSV for compatibility (no pandas dependency)
                    content = accounts_file.read().decode('utf-8')
                    csv_reader = csv.DictReader(StringIO(content))
                    accounts = list(csv_reader)
                    
                    # Validate columns
                    if not accounts or 'email' not in accounts[0] or 'password' not in accounts[0]:
                        flash('File must contain "email" and "password" columns', 'error')
                    else:
                        # Save uploaded accounts
                        upload_id = str(uuid.uuid4())
                        upload_file = f'uploads/accounts_{upload_id}.csv'
                        os.makedirs('uploads', exist_ok=True)
                        
                        # Write back to CSV
                        with open(upload_file, 'w', newline='') as f:
                            writer = csv.DictWriter(f, fieldnames=['email', 'password'])
                            writer.writeheader()
                            writer.writerows(accounts)
                        
                        session['current_upload'] = upload_file
                        session['upload_count'] = len(accounts)
                        flash(f'Accounts uploaded successfully! {len(accounts)} accounts ready for processing.', 'success')
                except Exception as e:
                    flash(f'Error uploading accounts: {str(e)}', 'error')
    
    return render_template('batch_upload.html')

@app.route('/start_automation', methods=['POST'])
@login_required
def start_automation():
    """Start the automation process"""
    if 'current_upload' not in session:
        return jsonify({'error': 'No accounts uploaded'}), 400
    
    upload_file = session['current_upload']
    
    try:
        import threading
        # Start automation in background thread
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
@login_required
def automation_status():
    """Get current automation status"""
    return jsonify(automation_engine.get_status())

@app.route('/pause_automation', methods=['POST'])
@login_required
def pause_automation():
    """Pause automation"""
    automation_engine.pause()
    return jsonify({'success': True, 'message': 'Automation paused'})

@app.route('/resume_automation', methods=['POST'])
@login_required
def resume_automation():
    """Resume automation"""
    automation_engine.resume()
    return jsonify({'success': True, 'message': 'Automation resumed'})

@app.route('/download_results')
@login_required
def download_results():
    """Download processing results"""
    results_file = automation_engine.get_results_file()
    if results_file and os.path.exists(results_file):
        return redirect(f'/static/{results_file}')
    else:
        flash('No results available yet', 'error')
        return redirect(url_for('dashboard'))

# ==================== BULK ACCOUNT MANAGEMENT ====================

@app.route('/bulk_action', methods=['POST'])
@login_required
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
            # Update last_checked to force refresh
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

# ==================== EXISTING ROUTES (PRESERVED) ====================

@app.route('/login')
@login_required
def login():
    """Redirect to Microsoft login"""
    auth_url = get_msal_app().get_authorization_request_url(
        scopes=app.config['SCOPE'],
        redirect_uri=app.config['REDIRECT_URI']
    )
    return redirect(auth_url)

@app.route('/add_account')
@login_required
def add_account():
    """Redirect to Microsoft login to add an account"""
    return redirect(url_for('login'))

@app.route('/sign_out_all')
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
def view_emails(account_id):
    """View unread emails for a specific account"""
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
        url = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages'
        params = {
            '$filter': 'isRead eq false',
            '$select': 'id,subject,from,receivedDateTime,bodyPreview,hasAttachments',
            '$orderby': 'receivedDateTime DESC',
            '$top': 50
        }
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 401:
            new_access_token = refresh_token(account_id)
            if new_access_token:
                headers['Authorization'] = f'Bearer {new_access_token}'
                response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            emails_data = response.json()
            emails = emails_data.get('value', [])
            
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
            flash(f'Error fetching emails: {response.status_code}', 'error')
            return redirect(url_for('dashboard'))
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/view_email/<int:account_id>/<message_id>')
@login_required
def view_email(account_id, message_id):
    """View full email content"""
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
        params = {
            '$select': 'subject,from,toRecipients,receivedDateTime,body,hasAttachments,attachments'
        }
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 401:
            new_access_token = refresh_token(account_id)
            if new_access_token:
                headers['Authorization'] = f'Bearer {new_access_token}'
                response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            email_data = response.json()
            
            sender = email_data.get('from', {}).get('emailAddress', {})
            to_recipients = email_data.get('toRecipients', [])
            body = email_data.get('body', {})
            body_content = body.get('content', 'No content available')
            
            formatted_email = {
                'subject': email_data.get('subject', 'No Subject'),
                'from_name': sender.get('name', 'Unknown'),
                'from_email': sender.get('address', ''),
                'to_recipients': [recipient.get('emailAddress', {}).get('address', '') for recipient in to_recipients],
                'received': email_data.get('receivedDateTime', ''),
                'body': body_content,
                'body_type': body.get('contentType', 'text'),
                'has_attachments': email_data.get('hasAttachments', False),
                'attachments': email_data.get('attachments', [])
            }
            
            return render_template('email_detail.html', 
                                 account_email=email,
                                 account_id=account_id,
                                 email=formatted_email,
                                 message_id=message_id)
        else:
            flash(f'Error fetching email: {response.status_code}', 'error')
            return redirect(url_for('view_emails', account_id=account_id))
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('view_emails', account_id=account_id))

@app.route('/mark_as_read/<int:account_id>/<message_id>')
@login_required
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

# ==================== NEW TELEGRAM SETTINGS ROUTE ====================

@app.route('/telegram_settings', methods=['GET', 'POST'])
@login_required
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

@app.route('/debug-files')
@login_required
def debug_files():
    """Emergency file check - CRITICAL"""
    import os
    import hashlib
    
    result = {
        'files_exist': {},
        'file_sizes': {},
        'file_content_samples': {},
        'current_directory': os.getcwd(),
        'directory_listing': []
    }
    
    # Check ALL critical files
    files_to_check = [
        'automation_engine.py',
        'app.py', 
        'proxy_manager.py',
        'telegram_alerts.py',
        'config.py'
    ]
    
    for file in files_to_check:
        exists = os.path.exists(file)
        result['files_exist'][file] = exists
        
        if exists:
            result['file_sizes'][file] = os.path.getsize(file)
            try:
                with open(file, 'r') as f:
                    content = f.read(500)  # First 500 chars
                    result['file_content_samples'][file] = content
                    
                    # Check for specific methods
                    if file == 'automation_engine.py':
                        result['has_process_method'] = 'def process_accounts_batch' in content
                        result['has_init_method'] = 'def __init__' in content
            except Exception as e:
                result['file_content_samples'][file] = f"Error: {e}"
        else:
            result['file_content_samples'][file] = "FILE NOT FOUND"
    
    # List directory contents
    try:
        result['directory_listing'] = os.listdir('.')
    except Exception as e:
        result['directory_listing'] = f"Error: {e}"
    
    return jsonify(result)


@app.route('/debug-import')
@login_required
def debug_import():
    """Debug import issues"""
    import sys
    result = {'errors': []}
    
    try:
        # Try to import and instantiate
        from automation_engine import AutomationEngine
        result['import_success'] = True
    except Exception as e:
        result['import_success'] = False
        result['import_error'] = str(e)
        result['errors'].append(f"Import failed: {e}")
    
    try:
        # Try to check methods if import worked
        if result.get('import_success'):
            methods = [m for m in dir(AutomationEngine) if not m.startswith('_')]
            result['methods'] = methods
            result['has_process_method'] = 'process_accounts_batch' in methods
    except Exception as e:
        result['methods_error'] = str(e)
        result['errors'].append(f"Methods check failed: {e}")
    
    return jsonify(result)

# KEEP THE EXISTING debug-automation route below this
@app.route('/debug-automation')
@login_required
def debug_automation():


@app.route('/debug-automation')
@login_required
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
        # Check methods
        methods = [method for method in dir(AutomationEngine) if not method.startswith('_')]
        result['automation_engine_methods'] = methods
        result['has_process_accounts_batch'] = 'process_accounts_batch' in methods
        
        # Check file content
        if os.path.exists('automation_engine.py'):
            with open('automation_engine.py', 'r') as f:
                content = f.read()
                result['file_has_method'] = 'def process_accounts_batch' in content
                result['file_size'] = len(content)
    except Exception as e:
        result['error'] = str(e)
    
    return jsonify(result)

if __name__ == '__main__':
    # Create necessary directories
    os.makedirs('uploads', exist_ok=True)
    os.makedirs('exports', exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    
    init_db()
    
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)