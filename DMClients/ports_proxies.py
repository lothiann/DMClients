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
if os.path.exists(proxies_path := os.path.join(os.path.dirname(os.path.abspath(__file__)), "Settings", "proxies.json")):
    with open(proxies_path, 'r', encoding='utf-8') as f:
        PROXIES = json.load(f)

XRAY_PATH = "xray.exe"
TEST_URL = "https://www.google.com/generate_204"
log_enabled = True

processes = []
process_lock = threading.Lock()

if sys.platform == 'win32':
    driver_path = r"C:\Windows\System32\drivers\ndisrd.sys"
    if not os.path.exists(driver_path):
        print("="*60)
        print("⚠️  WARNING: ndisrd.sys driver not found!")
        print("="*60)
        print("This tool requires Windows Packet Filter driver")
        print("Download: https://github.com/wiresock/ndisapi/releases/")
        print("="*60)
        time.sleep(3)

def _decode_vmess_base64(key: str) -> str:
    """Try to decode vmess://base64(json) into a standard vmess URI
    that python_v2ray can parse. Returns original key if decoding fails."""
    if not key.startswith("vmess://"):
        return key
    payload = key[len("vmess://"):]
    if not payload:
        return key
    try:
        # Fix base64 padding + handle URL-safe variants
        import base64
        padded = payload + "=" * (-len(payload) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
        obj = json.loads(decoded)
        if not isinstance(obj, dict):
            return key
        # Reconstruct a standard vmess:// URI from the JSON fields
        v = obj.get("v", "2")
        add = obj.get("add", "")
        port = obj.get("port", "")
        uid = obj.get("id", "")
        aid = obj.get("aid", 0)
        net = obj.get("net", "tcp")
        ttype = obj.get("type", "none")
        host = obj.get("host", "")
        path = obj.get("path", "")
        tls = obj.get("tls", "")
        sni = obj.get("sni", "")
        alpn = obj.get("alpn", "")
        fp = obj.get("fp", "")
        pbk = obj.get("pbk", "")
        sid = obj.get("sid", "")
        flow = obj.get("flow", "")
        scy = obj.get("scy", "auto")

        if not add or not port or not uid:
            return key

        params = []
        params.append(f"type={net}")
        if ttype and ttype != "none":
            params.append(f"headerType={ttype}")
        if host:
            params.append(f"host={host}")
        if path:
            params.append(f"path={path}")
        if tls:
            params.append(f"security={tls}")
        else:
            params.append("security=none")
        if sni:
            params.append(f"sni={sni}")
        if alpn:
            params.append(f"alpn={alpn}")
        if fp:
            params.append(f"fp={fp}")
        if pbk:
            params.append(f"pbk={pbk}")
        if sid:
            params.append(f"sid={sid}")
        if flow:
            params.append(f"flow={flow}")
        if scy and scy != "auto":
            params.append(f"encryption={scy}")

        query = "&".join(params)
        remark = obj.get("ps", "")
        fragment = f"#{remark}" if remark else ""

        return f"vmess://{uid}@{add}:{port}?{query}{fragment}"
    except Exception:
        return key


def parse_key(key: str) -> dict | None:
    try:
        from python_v2ray.config_parser import parse_uri

        if key.startswith("socks5://"):
            key = "socks://" + key[len("socks5://"):]
        elif key.startswith("shadowsocks://"):
            key = "ss://" + key[len("shadowsocks://"):]
        elif key.startswith("hy://"):
            key = "hysteria://" + key[len("hy://"):]
        elif key.startswith("hy2://"):
            key = "hysteria2://" + key[len("hy2://"):]

        # vmess://base64(json) — try decoding before parse_uri
        if key.startswith("vmess://"):
            key = _decode_vmess_base64(key)

        p = parse_uri(key)
        server = getattr(p, "address", None) or getattr(p, "server", None) or getattr(p, "host", None)
        if not server:
            return None

        # ---------- VLESS / VMESS ----------
        if p.protocol in ("vless", "vmess"):
            ob: dict = {
                "protocol": p.protocol,
                "settings": {"vnext": [{"address": server, "port": p.port,
                    "users": [{"id": getattr(p, "id", getattr(p, "uuid", "")),
                               "encryption": getattr(p, "encryption", "none"),
                               "flow": getattr(p, "flow", "")}]}]},
                "streamSettings": {"network": getattr(p, "network", "tcp"),
                                   "security": getattr(p, "security", "none")}
            }
            network = ob["streamSettings"]["network"]
            security = ob["streamSettings"]["security"]

            # WebSocket
            if network == "ws":
                ws = {}
                if hasattr(p, "path"):
                    ws["path"] = p.path
                if hasattr(p, "host"):
                    ws["headers"] = {"Host": p.host}
                ob["streamSettings"]["wsSettings"] = ws

            # gRPC
            elif network == "grpc":
                grpc = {}
                if hasattr(p, "serviceName"):
                    grpc["serviceName"] = p.serviceName
                if hasattr(p, "mode"):
                    grpc["multiMode"] = p.mode == "multi"
                ob["streamSettings"]["grpcSettings"] = grpc

            # HTTP/2
            elif network == "http":
                http = {}
                if hasattr(p, "host"):
                    http["host"] = [p.host] if isinstance(p.host, str) else p.host
                if hasattr(p, "path"):
                    http["path"] = p.path
                ob["streamSettings"]["httpSettings"] = http

            # mKCP
            elif network == "kcp":
                kcp = {"congestion": True}
                if hasattr(p, "header"):
                    kcp["header"] = {"type": p.header}
                elif hasattr(p, "headerType"):
                    kcp["header"] = {"type": p.headerType}
                if hasattr(p, "seed"):
                    kcp["seed"] = p.seed
                ob["streamSettings"]["kcpSettings"] = kcp

            # QUIC
            elif network == "quic":
                quic = {}
                if hasattr(p, "header"):
                    quic["header"] = {"type": p.header}
                elif hasattr(p, "headerType"):
                    quic["header"] = {"type": p.headerType}
                if hasattr(p, "quicSecurity"):
                    quic["security"] = p.quicSecurity
                if hasattr(p, "key"):
                    quic["key"] = p.key
                ob["streamSettings"]["quicSettings"] = quic

            # TLS / Reality
            if security == "tls":
                tls_cfg = {
                    "serverName": getattr(p, "sni", server),
                    "allowInsecure": getattr(p, "allowInsecure", False)
                }
                if hasattr(p, "fingerprint"):
                    tls_cfg["fingerprint"] = p.fingerprint
                if hasattr(p, "alpn"):
                    alpn_val = p.alpn
                    if isinstance(alpn_val, str):
                        alpn_val = [a.strip() for a in alpn_val.split(",") if a.strip()]
                    tls_cfg["alpn"] = alpn_val
                ob["streamSettings"]["tlsSettings"] = tls_cfg
            elif security == "reality":
                reality_cfg = {
                    "serverName": getattr(p, "sni", ""),
                    "fingerprint": getattr(p, "fingerprint", "chrome"),
                    "publicKey": getattr(p, "pbk", ""),
                    "shortId": getattr(p, "sid", ""),
                    "spiderX": getattr(p, "spiderX", "/")
                }
                if hasattr(p, "alpn"):
                    alpn_val = p.alpn
                    if isinstance(alpn_val, str):
                        alpn_val = [a.strip() for a in alpn_val.split(",") if a.strip()]
                    reality_cfg["alpn"] = alpn_val
                ob["streamSettings"]["realitySettings"] = reality_cfg
            return ob

        # ---------- Shadowsocks / SS ----------
        if p.protocol in ("shadowsocks", "ss"):
            out = {
                "protocol": "shadowsocks",
                "settings": {"servers": [{"address": server, "port": p.port,
                    "method": getattr(p, "method", "chacha20-ietf-poly1305"),
                    "password": getattr(p, "password", "")}]}
            }
            if hasattr(p, "plugin"):
                out["settings"]["servers"][0]["plugin"] = p.plugin
                if hasattr(p, "pluginOpts"):
                    out["settings"]["servers"][0]["pluginOpts"] = p.pluginOpts
            ss_network = getattr(p, "network", None)
            if ss_network and ss_network != "tcp":
                out["streamSettings"] = {"network": ss_network}
                if ss_network == "ws":
                    ws = {}
                    if hasattr(p, "path"):
                        ws["path"] = p.path
                    if hasattr(p, "host"):
                        ws["headers"] = {"Host": p.host}
                    out["streamSettings"]["wsSettings"] = ws
            return out

        # ---------- Trojan ----------
        if p.protocol == "trojan":
            network = getattr(p, "network", "tcp")
            security = getattr(p, "security", "tls")
            out = {
                "protocol": "trojan",
                "settings": {"servers": [{"address": server, "port": p.port,
                    "password": getattr(p, "password", getattr(p, "uuid", ""))}]},
                "streamSettings": {"network": network, "security": security}
            }
            if security in ("tls", "reality"):
                if security == "tls":
                    tls_cfg = {
                        "serverName": getattr(p, "sni", server),
                        "allowInsecure": getattr(p, "allowInsecure", False)
                    }
                    if hasattr(p, "fingerprint"):
                        tls_cfg["fingerprint"] = p.fingerprint
                    if hasattr(p, "alpn"):
                        alpn_val = p.alpn
                        if isinstance(alpn_val, str):
                            alpn_val = [a.strip() for a in alpn_val.split(",") if a.strip()]
                        tls_cfg["alpn"] = alpn_val
                    out["streamSettings"]["tlsSettings"] = tls_cfg
                elif security == "reality":
                    out["streamSettings"]["realitySettings"] = {
                        "serverName": getattr(p, "sni", ""),
                        "fingerprint": getattr(p, "fingerprint", "chrome"),
                        "publicKey": getattr(p, "pbk", ""),
                        "shortId": getattr(p, "sid", ""),
                        "spiderX": getattr(p, "spiderX", "/")
                    }
            if network == "ws":
                ws = {}
                if hasattr(p, "path"):
                    ws["path"] = p.path
                if hasattr(p, "host"):
                    ws["headers"] = {"Host": p.host}
                out["streamSettings"]["wsSettings"] = ws
            elif network == "grpc":
                grpc = {}
                if hasattr(p, "serviceName"):
                    grpc["serviceName"] = p.serviceName
                if hasattr(p, "mode"):
                    grpc["multiMode"] = p.mode == "multi"
                out["streamSettings"]["grpcSettings"] = grpc
            elif network == "kcp":
                kcp = {"congestion": True}
                if hasattr(p, "header"):
                    kcp["header"] = {"type": p.header}
                elif hasattr(p, "headerType"):
                    kcp["header"] = {"type": p.headerType}
                if hasattr(p, "seed"):
                    kcp["seed"] = p.seed
                out["streamSettings"]["kcpSettings"] = kcp
            return out

        # ---------- Hysteria / Hysteria2 ----------
        if p.protocol in ("hysteria", "hysteria2"):
            out = {
                "protocol": p.protocol,
                "settings": {"servers": [{"address": server, "port": p.port,
                    "password": getattr(p, "password", getattr(p, "auth", ""))}]},
                "streamSettings": {"network": "udp", "security": "tls",
                    "tlsSettings": {"serverName": getattr(p, "sni", server),
                                    "allowInsecure": getattr(p, "allowInsecure", False)}}
            }
            if hasattr(p, "fingerprint"):
                out["streamSettings"]["tlsSettings"]["fingerprint"] = p.fingerprint
            if hasattr(p, "alpn"):
                alpn_val = p.alpn
                if isinstance(alpn_val, str):
                    alpn_val = [a.strip() for a in alpn_val.split(",") if a.strip()]
                out["streamSettings"]["tlsSettings"]["alpn"] = alpn_val

            if p.protocol == "hysteria":
                if hasattr(p, "up"):
                    out["settings"]["servers"][0]["up_mbps"] = p.up
                if hasattr(p, "down"):
                    out["settings"]["servers"][0]["down_mbps"] = p.down
                if hasattr(p, "obfs"):
                    out["settings"]["servers"][0]["obfs"] = p.obfs

            if p.protocol == "hysteria2":
                if hasattr(p, "obfs"):
                    out["settings"]["servers"][0]["obfs"] = {
                        "type": getattr(p, "obfs", "salamander")
                    }
                    if hasattr(p, "obfs_password"):
                        out["settings"]["servers"][0]["obfs"]["password"] = p.obfs_password
                    elif hasattr(p, "obfsPassword"):
                        out["settings"]["servers"][0]["obfs"]["password"] = p.obfsPassword
                if hasattr(p, "congestion_control_type"):
                    out["settings"]["servers"][0]["congestion_control_type"] = p.congestion_control_type
                elif hasattr(p, "congestion"):
                    out["settings"]["servers"][0]["congestion_control_type"] = p.congestion
                if hasattr(p, "up"):
                    out["settings"]["servers"][0]["up_mbps"] = p.up
                if hasattr(p, "down"):
                    out["settings"]["servers"][0]["down_mbps"] = p.down
            return out

        # ---------- SOCKS ----------
        if p.protocol == "socks":
            users = []
            user = getattr(p, "id", "")
            passwd = getattr(p, "password", "")
            if user:
                users.append({"user": user, "pass": passwd})
            return {"protocol": "socks",
                    "settings": {"servers": [{"address": server, "port": p.port,
                                               "users": users}]}}
    except Exception as e:
        print(f"Parse error: {e}")
        pass
    return None


def start_proxy(proxy_config):
    port = proxy_config["port"]
    key = proxy_config["key"]

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