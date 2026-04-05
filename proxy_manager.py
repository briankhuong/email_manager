import os
import random

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.current_proxies_file = None
        
    def load_proxies_from_file(self, file):
        """Load proxies from uploaded file - Supports IP:PORT:USER:PASS"""
        content = file.read().decode('utf-8')
        lines = content.strip().split('\n')
        
        self.proxies = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Split by colon to check format
            parts = line.split(':')
            
            if len(parts) == 4:
                # Format: IP:PORT:USER:PASS -> http://USER:PASS@IP:PORT
                # This is the ONLY format that allows authenticated requests
                formatted = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
                self.proxies.append(formatted)
            elif len(parts) == 2:
                # Format: IP:PORT -> http://IP:PORT
                self.proxies.append(f"http://{line}")
            else:
                # Fallback: just add http:// if it's missing
                if not line.startswith('http'):
                    self.proxies.append(f"http://{line}")
                else:
                    self.proxies.append(line)
        
        # Save the properly formatted proxies for persistence
        self.current_proxies_file = 'uploads/current_proxies.txt'
        os.makedirs('uploads', exist_ok=True)
        with open(self.current_proxies_file, 'w') as f:
            # Join with newlines so the file stays readable
            f.write('\n'.join(self.proxies))
        
        return len(self.proxies)
    
    def get_proxies(self):
        """Get list of available proxies"""
        if not self.proxies and os.path.exists('uploads/current_proxies.txt'):
            # Load from saved file
            with open('uploads/current_proxies.txt', 'r') as f:
                content = f.read()
                self.proxies = [line.strip() for line in content.split('\n') if line.strip() and not line.startswith('#')]
        
        return self.proxies.copy()
    
    def get_proxy_for_slot(self, slot_number):
        """Get proxy for a specific slot"""
        proxies = self.get_proxies()
        if not proxies:
            return None
        
        # Use modulo to assign proxy to slot
        proxy_index = (slot_number - 1) % len(proxies)
        return proxies[proxy_index]
    
    def get_random_proxy(self):
        """Get a random proxy"""
        proxies = self.get_proxies()
        if not proxies:
            return None
        return random.choice(proxies)
    
    def get_proxy_count(self):
        """Get number of available proxies"""
        return len(self.get_proxies())
    
    def validate_proxy(self, proxy):
        """Validate if proxy is working (basic check)"""
        # This would contain actual proxy validation logic
        # For now, just return True
        return True