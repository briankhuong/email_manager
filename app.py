from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import sqlite3
import time
from datetime import datetime
import os
import requests
from config import Config
import msal
import json

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

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
            last_error TEXT
        )
    ''')
    conn.commit()
    conn.close()

def encrypt_token(token):
    """Store token as plain text - no encryption to avoid JWT corruption"""
    return token

def decrypt_token(encrypted_token):
    """Return token as-is - no decryption needed"""
    return encrypted_token

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

def get_user_info(access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        response = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers, timeout=10)
        print(f"User info request - Status: {response.status_code}")
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"User info error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"User info exception: {str(e)}")
        return None

def get_unread_emails_count(access_token):
    try:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Use the correct endpoint for unread messages
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
            print(f"Found {unread_count} unread emails")
            return unread_count, None
        else:
            error_msg = f"Graph API error: {response.status_code} - {response.text}"
            print(f"Error: {error_msg}")
            return 0, error_msg
            
    except Exception as e:
        error_msg = f"Exception: {str(e)}"
        print(f"Exception: {error_msg}")
        return 0, error_msg

@app.route('/')
def callback():
    """Main route that handles both OAuth callback and dashboard display"""
    # Handle OAuth callback
    if 'error' in request.args:
        flash(f"Authentication error: {request.args['error']}", 'error')
        return redirect(url_for('dashboard'))
    
    if 'code' in request.args:
        result = get_token_from_code(request.args['code'])
        
        if 'access_token' in result:
            access_token = result['access_token']
            
            # Debug: Check if we got a valid access token
            print(f"Access token received: {access_token[:50]}...")
            
            user_info = get_user_info(access_token)
            
            if user_info:
                email = user_info.get('mail') or user_info.get('userPrincipalName')
                print(f"User info retrieved successfully: {email}")
                
                conn = sqlite3.connect(app.config['DATABASE_FILE'])
                c = conn.cursor()
                
                # Check if account already exists
                c.execute("SELECT id FROM accounts WHERE email = ?", (email,))
                existing = c.fetchone()
                
                # Store tokens as plain text (no encryption)
                access_token_plain = access_token
                refresh_token_plain = result.get('refresh_token')
                
                # AUTO-CHECK FOR NEW EMAILS IMMEDIATELY
                print(f"Checking for unread emails for {email}...")
                unread_count, error = get_unread_emails_count(access_token)
                current_time = datetime.now()
                
                if existing:
                    # Update tokens and check emails for existing account
                    c.execute('''
                        UPDATE accounts 
                        SET access_token = ?, refresh_token = ?, is_signed_in = 1, 
                            last_checked = ?, unread_count = ?, last_error = ?
                        WHERE email = ?
                    ''', (access_token_plain, refresh_token_plain, 
                          current_time, unread_count, error, email))
                    print(f"Updated existing account: {email}")
                else:
                    # Insert new account with email check
                    c.execute('''
                        INSERT INTO accounts (email, access_token, refresh_token, is_signed_in, last_checked, unread_count, last_error)
                        VALUES (?, ?, ?, 1, ?, ?, ?)
                    ''', (email, access_token_plain, refresh_token_plain, 
                          current_time, unread_count, error))
                    print(f"Added new account: {email}")
                
                conn.commit()
                conn.close()
                
                if unread_count > 0:
                    flash(f'Successfully added {email} - {unread_count} new emails found!', 'success')
                else:
                    flash(f'Successfully added {email} - no new emails', 'success')
            else:
                error_msg = "Failed to get user information - access token may be invalid"
                print(error_msg)
                flash(error_msg, 'error')
                
                # Debug: Try to see what the actual error is
                headers = {'Authorization': f'Bearer {access_token}'}
                response = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers)
                print(f"User info API response: {response.status_code} - {response.text}")
        else:
            error_description = result.get("error_description", "Unknown error")
            print(f"Failed to get access token: {error_description}")
            flash(f'Failed to get access token: {error_description}', 'error')
    
    # Show the dashboard
    return dashboard()

@app.route('/dashboard')
def dashboard():
    """Display the accounts dashboard"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    
    c.execute('''
        SELECT id, email, is_signed_in, unread_count, last_checked, last_error 
        FROM accounts 
        ORDER BY unread_count DESC, email ASC
    ''')
    
    accounts = []
    for row in c.fetchall():
        accounts.append({
            'id': row[0],
            'email': row[1],
            'is_signed_in': bool(row[2]),
            'unread_count': row[3],
            'last_checked': row[4],
            'last_error': row[5]
        })
    
    conn.close()
    return render_template('index.html', accounts=accounts)

@app.route('/login')
def login():
    """Redirect to Microsoft login"""
    auth_url = get_msal_app().get_authorization_request_url(
        scopes=app.config['SCOPE'],
        redirect_uri=app.config['REDIRECT_URI']
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
    
    email, access_token = row  # Token is already plain text
    
    if not access_token:
        flash('Not signed in or token expired', 'error')
        return redirect(url_for('dashboard'))
    
    # Get unread emails
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        # Get unread emails with more details
        url = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages'
        params = {
            '$filter': 'isRead eq false',
            '$select': 'id,subject,from,receivedDateTime,bodyPreview,hasAttachments',
            '$orderby': 'receivedDateTime DESC',
            '$top': 50
        }
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            emails_data = response.json()
            emails = emails_data.get('value', [])
            
            # Format email data for display
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
    
    email, access_token = row  # Token is already plain text
    
    if not access_token:
        flash('Not signed in or token expired', 'error')
        return redirect(url_for('dashboard'))
    
    # Get full email content
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
        
        if response.status_code == 200:
            email_data = response.json()
            
            # Format email data
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
def mark_as_read(account_id, message_id):
    """Mark a specific email as read and redirect to view it"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute("SELECT access_token FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        flash('Account not found', 'error')
        return redirect(url_for('dashboard'))
    
    access_token = row[0]  # Token is already plain text
    
    if not access_token:
        flash('Not signed in or token expired', 'error')
        return redirect(url_for('dashboard'))
    
    # Mark email as read
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
        
        if response.status_code in [200, 204]:
            # Successfully marked as read, now redirect to view the email
            return redirect(url_for('view_email', account_id=account_id, message_id=message_id))
        else:
            flash(f'Error marking email as read: {response.status_code}', 'error')
            # Even if marking as read fails, still try to view the email
            return redirect(url_for('view_email', account_id=account_id, message_id=message_id))
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        # Even if there's an error, try to view the email
        return redirect(url_for('view_email', account_id=account_id, message_id=message_id))

@app.route('/debug-token/<int:account_id>')
def debug_token(account_id):
    """Debug token for a specific account"""
    conn = sqlite3.connect(app.config['DATABASE_FILE'])
    c = conn.cursor()
    c.execute("SELECT email, access_token, refresh_token FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return "Account not found", 404
    
    email, access_token, refresh_token = row  # Tokens are already plain text
    
    return {
        'email': email,
        'access_token_length': len(access_token) if access_token else 0,
        'access_token_preview': access_token[:50] + '...' if access_token else None,
        'refresh_token_length': len(refresh_token) if refresh_token else 0,
        'access_token_has_dots': '.' in access_token if access_token else False,
        'refresh_token_has_dots': '.' in refresh_token if refresh_token else False,
        'access_token_starts_with_eyJ': access_token.startswith('eyJ') if access_token else False
    }

@app.route('/debug-app')
def debug_app():
    """Debug Azure app configuration"""
    return {
        'client_id': app.config['CLIENT_ID'],
        'client_id_set': bool(app.config['CLIENT_ID']),
        'client_secret_set': bool(app.config['CLIENT_SECRET']),
        'redirect_uri': app.config['REDIRECT_URI'],
        'scope': app.config['SCOPE']
    }

if __name__ == '__main__':
    init_db()
    
    # Get port from environment variable (Railway provides this)
    port = int(os.environ.get('PORT', 5001))
    # Run with debug=False in production
    app.run(host='0.0.0.0', port=port, debug=False)