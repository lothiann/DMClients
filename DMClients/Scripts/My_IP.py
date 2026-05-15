# Example script

import urllib.request

try:
    with urllib.request.urlopen("http://ifconfig.me/ip", timeout=5) as response:
        ip = response.read().decode('utf-8').strip()
        log(f"My IP: {ip}")
except Exception as e:
    log(f"Error: {e}")