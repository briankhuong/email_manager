import threading
import time
import csv
import uuid
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

class AutomationEngine:
    def __init__(self, proxy_manager, telegram_notifier):
        self.proxy_manager = proxy_manager
        self.telegram_notifier = telegram_notifier
        self.is_running = False
        self.is_paused = False
        self.current_job_id = None
        self.status = {}
        print("✅ AutomationEngine initialized with Selenium")

    def process_accounts_batch(self, accounts_file):
        """Process accounts in batch with Selenium automation"""
        print(f"🚀 AUTOMATION STARTED with file: {accounts_file}")
        self.is_running = True
        self.current_job_id = str(uuid.uuid4())
        
        try:
            # Load accounts
            accounts = []
            with open(accounts_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    accounts.append(row)
            
            print(f"📧 Loaded {len(accounts)} accounts")
            
            # Update status
            self.status = {
                'total_accounts': len(accounts),
                'processed_accounts': 0,
                'successful_logins': 0,
                'failed_logins': 0,
                'captcha_count': 0,
                'current_worker': 'Initializing browser automation',
                'start_time': datetime.now().isoformat()
            }
            
            # Get proxies
            proxies = self.proxy_manager.get_proxies()
            if not proxies:
                print("❌ No proxies available")
                return
            
            # Process accounts with Selenium
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
                
                # Try login with Selenium
                success = self.login_to_hotmail(email, password, proxy)
                
                if success:
                    self.status['successful_logins'] += 1
                    print(f"✅ Login successful: {email}")
                else:
                    self.status['failed_logins'] += 1
                    print(f"❌ Login failed: {email}")
                
                # 3-minute delay between accounts
                if i < len(accounts) - 1:  # Don't delay after last account
                    print("⏰ Waiting 3 minutes before next account...")
                    for _ in range(180):  # 180 seconds = 3 minutes
                        if not self.is_running or self.is_paused:
                            break
                        time.sleep(1)
            
            print("✅ AUTOMATION COMPLETED SUCCESSFULLY")
            
        except Exception as e:
            print(f"❌ AUTOMATION ERROR: {e}")
        finally:
            self.is_running = False

    def login_to_hotmail(self, email, password, proxy):
        """Login to Hotmail using Selenium with Railway-optimized setup"""
        driver = None
        try:
            # Railway-optimized Chrome options
            chrome_options = Options()
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-software-rasterizer')
            chrome_options.add_argument('--remote-debugging-port=9222')
            
            # Add proxy if available and valid
            if proxy and ('http' in proxy or 'socks' in proxy):
                chrome_options.add_argument(f'--proxy-server={proxy}')
                print(f"🔧 Using proxy: {proxy[:50]}...")
            else:
                print("⚠️ No valid proxy provided, using direct connection")
            
            # Initialize driver with error handling
            try:
                from selenium.webdriver.chrome.service import Service
                from webdriver_manager.chrome import ChromeDriverManager
                
                # Use webdriver-manager for automatic ChromeDriver management
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=chrome_options)
                
            except Exception as e:
                print(f"⚠️ ChromeDriver manager failed: {e}, trying direct...")
                # Fallback to direct ChromeDriver
                driver = webdriver.Chrome(options=chrome_options)
            
            driver.implicitly_wait(15)
            
            # Navigate to Hotmail login
            print(f"🌐 Navigating to Hotmail login for {email}")
            driver.get("https://outlook.live.com/owa/")
            
            # Wait for login page to load
            wait = WebDriverWait(driver, 15)
            
            try:
                # Enter email
                email_field = wait.until(EC.element_to_be_clickable((By.NAME, "loginfmt")))
                email_field.clear()
                email_field.send_keys(email)
                print("✅ Email entered")
                
                # Click next
                next_button = driver.find_element(By.ID, "idSIButton9")
                next_button.click()
                print("✅ Next button clicked")
                
                # Wait for password field
                time.sleep(5)
                
                # Check if we need to handle "Use another account"
                try:
                    use_another_account = driver.find_elements(By.ID, "otherTileText")
                    if use_another_account:
                        use_another_account[0].click()
                        time.sleep(3)
                except:
                    pass
                
                # Enter password
                password_field = wait.until(EC.element_to_be_clickable((By.NAME, "passwd")))
                password_field.clear()
                password_field.send_keys(password)
                print("✅ Password entered")
                
                # Click sign in
                signin_button = driver.find_element(By.ID, "idSIButton9")
                signin_button.click()
                print("✅ Sign in button clicked")
                
                # Wait for login result with longer timeout
                time.sleep(8)
                
                # Check if login was successful
                current_url = driver.current_url
                page_title = driver.title.lower()
                page_source = driver.page_source.lower()
                
                success_indicators = [
                    "mail" in current_url,
                    "inbox" in current_url, 
                    "outlook" in page_title,
                    "inbox" in page_title,
                    "messages" in page_source
                ]
                
                if any(success_indicators):
                    print(f"✅ Successfully logged in: {email}")
                    return True
                else:
                    print(f"❌ Login failed - URL: {current_url}, Title: {page_title}")
                    # Save screenshot for debugging
                    try:
                        driver.save_screenshot(f"debug_{email.split('@')[0]}.png")
                        print("📸 Screenshot saved for debugging")
                    except:
                        pass
                    return False
                    
            except Exception as form_error:
                print(f"❌ Form interaction error: {form_error}")
                return False
                
        except Exception as e:
            print(f"❌ Selenium setup error for {email}: {e}")
            return False
        finally:
            if driver:
                try:
                    driver.quit()
                    print("✅ Browser closed")
                except:
                    pass

    def get_status(self):
        return self.status

    def pause(self):
        self.is_paused = True
        return True

    def resume(self):
        self.is_paused = False
        return True

    def get_results_file(self):
        return None