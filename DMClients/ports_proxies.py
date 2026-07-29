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
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    sys.stderr.reconfigure(encoding='utf-8')

PROXIES = []
checked_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Settings", "checked_proxies.json")
if os.path.exists(checked_path):
    with open(checked_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # checked_proxies.json is a plain array of key strings (same format as spare_proxies.json).
    if not isinstance(data, list):
        print("ERROR: checked_proxies.json must be an array of proxy key strings")
        sys.exit(1)
    PROXIES = [str(k) for k in data]
else:
    print("=" * 60)
    print("ERROR: checked_proxies.json not found")
    print(f"   Looked at: {checked_path}")
    print("   Run 'Check Proxy' in the UI first to populate it.")
    print("=" * 60)
    sys.exit(1)

if not PROXIES:
    print("=" * 60)
    print("ERROR: checked_proxies.json is empty")
    print("   Run 'Check Proxy' in the UI to select working proxies.")
    print("=" * 60)
    sys.exit(1)

XRAY_PATH = "xray.exe"
TEST_URL = "https://www.google.com/generate_204"
log_enabled = True

# Ports are allocated statically: first proxy -> 10801, second -> 10802, ...
PROXY_BASE_PORT = 10801

processes = []
process_lock = threading.Lock()

def parse_key(key: str) -> dict | None:
    """Parse a proxy key into an xray outbound config dict.

    Uses python_v2ray.XrayConfigBuilder for vless/vmess/trojan/ss/socks so
    that streamSettings are built correctly for every transport (TCP
    headerType, REALITY, WS, gRPC, KCP, ...). Falls back to manual
    construction for hysteria/hysteria2/wireguard (not supported by
    XrayConfigBuilder at the time of writing).
    """
    try:
        from python_v2ray.config_parser import parse_uri, XrayConfigBuilder

        k = key
        if k.startswith("socks5://"):
            k = "socks://" + k[len("socks5://"):]
        elif k.startswith("shadowsocks://"):
            k = "ss://" + k[len("shadowsocks://"):]
        elif k.startswith("hy://"):
            k = "hysteria://" + k[len("hy://"):]
        elif k.startswith("hy2://"):
            k = "hysteria2://" + k[len("hy2://"):]
        # vmess://base64(json) is parsed by python_v2ray directly — no preprocessing.

        p = parse_uri(k)
        if p is None:
            return None

        server = getattr(p, "address", None) or getattr(p, "server", None)
        if not server:
            return None

        # ----- Protocols supported by XrayConfigBuilder -----
        if p.protocol in ("vless", "mvless", "vmess", "trojan", "ss", "shadowsocks", "socks"):
            builder = XrayConfigBuilder()
            outbound = builder.build_outbound_from_params(p)
            if outbound:
                outbound.pop("tag", None)  # we manage tags ourselves
                return outbound
            # fall through to manual fallback below

        # ----- Hysteria / Hysteria2 (manual, not supported by XrayConfigBuilder) -----
        if p.protocol in ("hysteria", "hysteria2"):
            out = {
                "protocol": p.protocol,
                "settings": {"servers": [{"address": server, "port": p.port,
                    "password": getattr(p, "hy2_password", getattr(p, "password", ""))}]},
                "streamSettings": {"network": "udp", "security": "tls",
                    "tlsSettings": {"serverName": getattr(p, "sni", server),
                                    "allowInsecure": False}}
            }
            if getattr(p, "fp", ""):
                out["streamSettings"]["tlsSettings"]["fingerprint"] = p.fp
            if getattr(p, "alpn", ""):
                out["streamSettings"]["tlsSettings"]["alpn"] = [a.strip() for a in p.alpn.split(",") if a.strip()]
            if p.protocol == "hysteria2" and getattr(p, "hy2_obfs", ""):
                out["settings"]["servers"][0]["obfs"] = {
                    "type": p.hy2_obfs,
                    "password": getattr(p, "hy2_obfs_password", "")
                }
            return out

        # ----- WireGuard (manual) -----
        if p.protocol == "wireguard":
            reserved = []
            if getattr(p, "wg_reserved", ""):
                reserved = [int(i.strip()) for i in p.wg_reserved.split(",") if i.strip()]
            return {
                "protocol": "wireguard",
                "settings": {
                    "secretKey": getattr(p, "wg_secret_key", ""),
                    "address": getattr(p, "wg_address", "172.16.0.2/32").split(","),
                    "peers": [{"publicKey": getattr(p, "pbk", ""),
                               "endpoint": f"{server}:{p.port}"}],
                    "mtu": getattr(p, "wg_mtu", 1420),
                    "reserved": reserved,
                }
            }

        return None
    except Exception as e:
        print(f"Parse error: {e}")
        return None


def start_proxy(key: str, port: int):

    outbound = parse_key(key)
    if outbound is None:
        print(f"\n⚠️ Skipping port {port} — failed to parse key")
        return None

    server = outbound.get("settings", {}).get("vnext", [{}])[0].get("address", 
             outbound.get("settings", {}).get("servers", [{}])[0].get("address", "unknown"))
    proto = outbound.get("protocol", "?")

    print(f"\nStarting proxy on port {port}")
    print(f"   Type: {proto}")
    print(f"   Address: {server}")

    inbound = {
        "port": port,
        "listen": "127.0.0.1",
        "protocol": "socks",
        "settings": {"auth": "noauth", "udp": True},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}
    }

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
                with open(checked_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    conn.sendall(b"ERROR\n")
                    conn.close()
                    return
                new_proxies = [str(k) for k in data]
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
                for idx, key in enumerate(new_proxies):
                    port = PROXY_BASE_PORT + idx
                    proc = start_proxy(key, port)
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

for idx, key in enumerate(PROXIES):
    port = PROXY_BASE_PORT + idx
    proc = start_proxy(key, port)
    if proc:
        processes.append(proc)
    time.sleep(2)

print(f"\n✅ STARTED: {len(processes)}/{len(PROXIES)}")
print("Ports:", [PROXY_BASE_PORT + i for i in range(len(PROXIES))])

log_enabled = False
print("\n📡 Checking proxies...")
for idx, _ in enumerate(PROXIES):
    port = PROXY_BASE_PORT + idx
    result = check_ping(port)
    print(f"   Port {port}: {result}")
log_enabled = True

print("\nCtrl+C to stop")
print("="*60)

try:
    while True:
        time.sleep(1)
        for i, proc in enumerate(processes):
            if proc.poll() is not None:
                print(f"❌ Proxy {PROXY_BASE_PORT + i} died")
except KeyboardInterrupt:
    print("\n⏏️  Stopping...")
    for proc in processes:
        proc.terminate()
    time.sleep(1)
    for proc in processes:
        proc.kill()
    print("✅ Done")