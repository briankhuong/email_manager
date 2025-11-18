import threading
import time
import random
from datetime import datetime
import os
import json
import csv
import uuid

class AutomationEngine:
    def __init__(self, proxy_manager, telegram_notifier):
        self.proxy_manager = proxy_manager
        self.telegram_notifier = telegram_notifier
        self.is_running = False
        self.is_paused = False
        self.current_job_id = None
        self.status = {
            'total_accounts': 0,
            'processed_accounts': 0,
            'successful_logins': 0,
            'failed_logins': 0,
            'captcha_count': 0,
            'start_time': None,
            'current_worker': None
        }
        self.workers = {}
        
    def process_accounts_batch(self, accounts_file):
        """Process accounts in parallel with proxies"""
        self.is_running = True
        self.is_paused = False
        self.current_job_id = str(uuid.uuid4())
        self.status = {
            'total_accounts': 0,
            'processed_accounts': 0,
            'successful_logins': 0,
            'failed_logins': 0,
            'captcha_count': 0,
            'start_time': datetime.now().isoformat(),
            'current_worker': 'Initializing...'
        }
        
        try:
            # Load accounts using CSV (no pandas dependency)
            accounts = []
            with open(accounts_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    accounts.append(row)
            
            self.status['total_accounts'] = len(accounts)
            
            # Get available proxies
            proxies = self.proxy_manager.get_proxies()
            if not proxies:
                self.telegram_notifier.send_alert("❌ No proxies available for automation")
                return
            
            # Distribute accounts across proxies
            accounts_per_proxy = len(accounts) // len(proxies)
            extra_accounts = len(accounts) % len(proxies)
            
            # Start worker threads
            threads = []
            account_index = 0
            
            for i, proxy in enumerate(proxies):
                worker_accounts = accounts_per_proxy
                if i < extra_accounts:
                    worker_accounts += 1
                
                if account_index < len(accounts):
                    worker_accounts_list = accounts[account_index:account_index + worker_accounts]
                    account_index += worker_accounts
                    
                    thread = threading.Thread(
                        target=self._worker_process,
                        args=(f"worker_{i}", proxy, worker_accounts_list)
                    )
                    thread.daemon = True
                    threads.append(thread)
                    thread.start()
                    
                    # Staggered start - 45 seconds between workers
                    time.sleep(45)
            
            # Wait for all threads to complete
            for thread in threads:
                thread.join()
                
        except Exception as e:
            self.telegram_notifier.send_alert(f"❌ Automation error: {str(e)}")
        finally:
            self.is_running = False
            self._generate_results_file()
            self.telegram_notifier.send_alert(
                f"✅ Automation completed!\n"
                f"Success: {self.status['successful_logins']}\n"
                f"Failed: {self.status['failed_logins']}\n"
                f"Captchas: {self.status['captcha_count']}"
            )
    
    def _worker_process(self, worker_id, proxy, accounts):
        """Process accounts for a single worker"""
        self.workers[worker_id] = {
            'status': 'running',
            'current_account': None,
            'processed': 0,
            'success': 0,
            'failures': 0
        }
        
        driver = None
        try:
            # Setup browser with proxy
            driver = self._setup_browser_with_proxy(proxy)
            
            for account in accounts:
                if not self.is_running or self.is_paused:
                    break
                    
                self.workers[worker_id]['current_account'] = account['email']
                self.status['current_worker'] = f"{worker_id}: {account['email']}"
                
                result = self._process_single_account(driver, account, proxy)
                
                self.workers[worker_id]['processed'] += 1
                self.status['processed_accounts'] += 1
                
                if result['success']:
                    self.workers[worker_id]['success'] += 1
                    self.status['successful_logins'] += 1
                else:
                    self.workers[worker_id]['failures'] += 1
                    self.status['failed_logins'] += 1
                    if result.get('captcha'):
                        self.status['captcha_count'] += 1
                
                # Delay between accounts (3 minutes ± 30 seconds)
                if account != accounts[-1]:  # Don't delay after last account
                    delay = 180 + random.randint(-30, 30)
                    time.sleep(delay)
                    
        except Exception as e:
            self.telegram_notifier.send_alert(f"❌ Worker {worker_id} error: {str(e)}")
        finally:
            if driver:
                driver.quit()
            self.workers[worker_id]['status'] = 'completed'
    
    def _setup_browser_with_proxy(self, proxy):
        """Setup Chrome browser with proxy"""
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        
        chrome_options = Options()
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Add proxy
        if proxy:
            chrome_options.add_argument(f'--proxy-server={proxy}')
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        return driver
    
    def _process_single_account(self, driver, account, proxy):
        """Process a single account login"""
        email = account['email']
        password = account['password']
        
        try:
            # Import selenium components inside method to avoid circular imports
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            # Navigate to the app
            driver.get("https://web-production-ce43f.up.railway.app")
            
            # Handle basic auth if needed (your app has password protection)
            # This would be handled automatically by the browser
            
            # Click Add Account
            add_account_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.LINK_TEXT, "Add Account"))
            )
            add_account_btn.click()
            
            # Microsoft login flow
            # Wait for email field
            email_field = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.ID, "i0116"))
            )
            email_field.clear()
            self._human_type(email_field, email)
            
            # Click next
            next_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "idSIButton9"))
            )
            next_btn.click()
            
            # Wait for password field
            password_field = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.ID, "i0118"))
            )
            time.sleep(2)  # Small delay
            password_field.clear()
            self._human_type(password_field, password)
            
            # Click sign in
            signin_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "idSIButton9"))
            )
            signin_btn.click()
            
            # Handle "Stay signed in" page
            time.sleep(3)
            try:
                no_btn = driver.find_element(By.ID, "idBtn_Back")
                no_btn.click()
            except:
                pass
            
            # Wait for redirect back to app
            WebDriverWait(driver, 30).until(
                EC.url_contains("web-production-ce43f.up.railway.app")
            )
            
            # Check for success
            if "dashboard" in driver.current_url:
                return {'success': True, 'captcha': False}
            else:
                return {'success': False, 'captcha': False}
                
        except Exception as e:
            # Check if it's a captcha
            error_msg = str(e).lower()
            if "captcha" in error_msg or "security" in error_msg or "verification" in error_msg:
                self.telegram_notifier.send_alert(
                    f"⚠️ CAPTCHA detected for {email}\n"
                    f"Proxy: {proxy}\n"
                    f"Worker: {self.status['current_worker']}\n"
                    f"Please solve manually or skip"
                )
                return {'success': False, 'captcha': True}
            else:
                return {'success': False, 'captcha': False}
    
    def _human_type(self, element, text):
        """Type like a human with random delays"""
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.1, 0.3))
    
    def pause(self):
        """Pause automation"""
        self.is_paused = True
        self.telegram_notifier.send_alert("⏸️ Automation paused")
    
    def resume(self):
        """Resume automation"""
        self.is_paused = False
        self.telegram_notifier.send_alert("▶️ Automation resumed")
    
    def get_status(self):
        """Get current automation status"""
        status = self.status.copy()
        status['is_running'] = self.is_running
        status['is_paused'] = self.is_paused
        status['workers'] = self.workers
        status['job_id'] = self.current_job_id
        
        # Calculate progress metrics
        if status['start_time'] and status['processed_accounts'] > 0:
            elapsed = (datetime.now() - datetime.fromisoformat(status['start_time'])).total_seconds()
            accounts_per_minute = status['processed_accounts'] / (elapsed / 60)
            status['speed'] = f"{accounts_per_minute:.1f} accounts/minute"
            
            if status['total_accounts'] > 0:
                remaining = status['total_accounts'] - status['processed_accounts']
                if accounts_per_minute > 0:
                    eta_minutes = remaining / accounts_per_minute
                    status['eta'] = f"{eta_minutes:.1f} minutes"
                
                percentage = (status['processed_accounts'] / status['total_accounts']) * 100
                status['percentage'] = f"{percentage:.1f}%"
        
        return status
    
    def _generate_results_file(self):
        """Generate results CSV file"""
        results_file = f"exports/results_{self.current_job_id}.csv"
        os.makedirs('exports', exist_ok=True)
        
        # Create detailed results
        with open(results_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Job Summary'])
            writer.writerow(['Job ID', self.current_job_id])
            writer.writerow(['Total Accounts', self.status['total_accounts']])
            writer.writerow(['Successful Logins', self.status['successful_logins']])
            writer.writerow(['Failed Logins', self.status['failed_logins']])
            writer.writerow(['Captcha Count', self.status['captcha_count']])
            writer.writerow(['Completion Time', datetime.now().isoformat()])
            writer.writerow([])
            writer.writerow(['Worker Details'])
            writer.writerow(['Worker', 'Processed', 'Success', 'Failures', 'Status'])
            
            for worker_id, worker_data in self.workers.items():
                writer.writerow([
                    worker_id,
                    worker_data['processed'],
                    worker_data['success'],
                    worker_data['failures'],
                    worker_data['status']
                ])
    
    def get_results_file(self):
        """Get the path to results file"""
        if self.current_job_id:
            return f"exports/results_{self.current_job_id}.csv"
        return None

    def skip_current_account(self, worker_id):
        """Skip current account for a specific worker (captcha handling)"""
        if worker_id in self.workers:
            self.workers[worker_id]['failures'] += 1
            self.workers[worker_id]['processed'] += 1
            self.status['failed_logins'] += 1
            self.status['captcha_count'] += 1
            self.status['processed_accounts'] += 1
            return True
        return False