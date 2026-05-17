import os
import sys
import subprocess
import json
import time
import threading
import requests
import socket
import io
from python_v2ray.config_parser import parse_uri

if getattr(sys, 'frozen', False):
    sys.stdout.reconfigure(line_buffering=True)

PROXIES = []
if os.path.exists(proxies_path := os.path.join(os.path.dirname(os.path.abspath(__file__)), "Settings", "proxies.json")):
    with open(proxies_path, 'r', encoding='utf-8') as f:
        PROXIES = json.load(f)

XRAY_PATH = "xray.exe"
TEST_URL = "http://ifconfig.me/ip"
log_enabled = True

processes = []
process_lock = threading.Lock()

def start_proxy(proxy_config):
    from python_v2ray.config_parser import parse_uri

    parsed = parse_uri(proxy_config["key"])
    port = proxy_config["port"]

    server = getattr(parsed, 'address', None) or getattr(parsed, 'server', None) or getattr(parsed, 'host', 'unknown')

    print(f"\nStarting proxy on port {port}")
    print(f"   Type: {parsed.protocol}")
    print(f"   Address: {server}:{parsed.port}")

    inbound = {
        "port": port,
        "listen": "127.0.0.1",
        "protocol": "socks",
        "settings": {"auth": "noauth", "udp": True},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}
    }

    if parsed.protocol in ['vless', 'vmess']:
        outbound = {
            "protocol": parsed.protocol,
            "settings": {
                "vnext": [{
                    "address": server,
                    "port": parsed.port,
                    "users": [{
                        "id": getattr(parsed, 'id', getattr(parsed, 'uuid', '')),
                        "encryption": getattr(parsed, 'encryption', 'none'),
                        "flow": getattr(parsed, 'flow', '')
                    }]
                }]
            },
            "streamSettings": {
                "network": getattr(parsed, 'network', 'tcp'),
                "security": getattr(parsed, 'security', 'none'),
            }
        }
        if getattr(parsed, 'security', '') == 'reality':
            outbound['streamSettings']['realitySettings'] = {
                "serverName": getattr(parsed, 'sni', ''),
                "fingerprint": getattr(parsed, 'fingerprint', 'chrome'),
                "publicKey": getattr(parsed, 'pbk', ''),
                "shortId": getattr(parsed, 'sid', ''),
                "spiderX": "/"
            }

    elif parsed.protocol in ['shadowsocks', 'ss']:
        outbound = {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [{
                    "address": server,
                    "port": parsed.port,
                    "method": getattr(parsed, 'method', 'chacha20-ietf-poly1305'),
                    "password": getattr(parsed, 'password', ''),
                }]
            }
        }

    elif parsed.protocol == 'trojan':
        outbound = {
            "protocol": "trojan",
            "settings": {
                "servers": [{
                    "address": server,
                    "port": parsed.port,
                    "password": getattr(parsed, 'password', getattr(parsed, 'uuid', ''))
                }]
            },
            "streamSettings": {
                "network": getattr(parsed, 'network', 'tcp'),
                "security": getattr(parsed, 'security', 'tls'),
                "tlsSettings": {
                    "serverName": getattr(parsed, 'sni', server),
                    "allowInsecure": True
                }
            }
        }

    else:
        print(f"⚠️ Protocol {parsed.protocol} is not supported")
        return None

    config = {
        "log": {"loglevel": "info"},
        "inbounds": [inbound],
        "outbounds": [outbound]
    }

    temp_dir = os.path.join(os.path.dirname(__file__), "Temp")
    os.makedirs(temp_dir, exist_ok=True)
    config_file = os.path.join(temp_dir, f"xray_config_{port}.json")
    with open(config_file, "w", encoding='utf-8') as f:
        json.dump(config, f, indent=2)

    print(f"   ✅ Config: {config_file}")

    proc = subprocess.Popen(
        [XRAY_PATH, "-c", config_file],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW
    )

    def log_output(pipe, prefix):
        for line in iter(pipe.readline, b''):
            if line and log_enabled:
                print(f"[{prefix}:{port}] {line.decode().strip()}")

    threading.Thread(target=log_output, args=(proc.stdout, "XRAY"), daemon=True).start()
    threading.Thread(target=log_output, args=(proc.stderr, "ERR"), daemon=True).start()

    return proc

def check_ping(port: int) -> str:
    try:
        proxies_dict = {"http": f"socks5://127.0.0.1:{port}", "https": f"socks5://127.0.0.1:{port}"}
        start = time.time()
        r = requests.get(TEST_URL, proxies=proxies_dict, timeout=5)
        ping = round((time.time() - start) * 1000, 1)
        return f"✅ {ping}ms" if r.status_code in (200, 204) else "❌ bad status"
    except Exception:
        return "❌ unreachable"

COMMAND_PORT = 5557

def handle_command(conn):
    try:
        data = conn.recv(1024).decode().strip()
        if data.startswith("replace "):
            port = int(data.split()[1])
            print(f"Replace proxy on port {port}")
            try:
                with open(__file__, "r", encoding="utf-8") as f:
                    content = f.read()
                local_vars = {}
                exec(content, {}, local_vars)
                new_proxies = local_vars.get("PROXIES", PROXIES)
            except Exception as e:
                conn.sendall(b"ERROR\n")
                conn.close()
                return
            with process_lock:
                for proc in processes:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait()
                processes.clear()
                for proxy in new_proxies:
                    proc = start_proxy(proxy)
                    if proc:
                        processes.append(proc)
                    time.sleep(2)
            conn.sendall(b"OK\n")
        else:
            conn.sendall(b"UNKNOWN\n")
    except Exception as e:
        conn.sendall(b"ERROR\n")
    finally:
        conn.close()

def command_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", COMMAND_PORT))
    server.listen(5)
    print(f"Command server listening on port {COMMAND_PORT}")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_command, args=(conn,), daemon=True).start()

print("Killing old xray processes...")
os.system("taskkill /F /IM xray.exe 2>nul")
time.sleep(1)

threading.Thread(target=command_server, daemon=True).start()

print("="*60)
print("STARTING PROXIES")
print("="*60)

for proxy in PROXIES:
    proc = start_proxy(proxy)
    if proc:
        processes.append(proc)
    time.sleep(2)

print(f"\n✅ STARTED: {len(processes)}/{len(PROXIES)}")
print("Ports:", [p['port'] for p in PROXIES])

log_enabled = False
print("\n📡 Checking proxies...")
for p in PROXIES:
    result = check_ping(p['port'])
    print(f"   Port {p['port']}: {result}")
log_enabled = True

print("\nCtrl+C to stop")
print("="*60)

try:
    while True:
        time.sleep(1)
        for i, proc in enumerate(processes):
            if proc.poll() is not None:
                print(f"❌ Proxy {PROXIES[i]['port']} died")
except KeyboardInterrupt:
    print("\n⏏️  Stopping...")
    for proc in processes:
        proc.terminate()
    time.sleep(1)
    for proc in processes:
        proc.kill()
    print("✅ Done")