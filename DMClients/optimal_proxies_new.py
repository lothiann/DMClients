import os
import sys
import signal
import requests
import base64
import json
import subprocess
import time
import re
import socket
import ssl
import struct
import threading
import queue
import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ─── Settings ─────────────────────────────────────────────────────────────────

XRAY_PATH = "xray.exe"
TOP_N = 14
START_PORT = 19000
MAX_WORKERS = 100
KEY_FILTER = ["rbc.ru"]

TCP_PING_TIMEOUT = 5.0
TLS_PING_TIMEOUT = 5.0
UDP_PING_TIMEOUT = 5.0
IP_CHECK_URLS = [
    "http://ipconfig.me/ip",
    "http://icanhazip.com",
    "http://api.ipify.org",
]
IP_CHECK_TIMEOUT = 5

GAME_BASE_TIMEOUT = 20
GAME_EXTEND_TIMEOUT = 10
GAME_TEST_PORT = 10801

SPARE_COUNT = 0

TARGET_SERVER = "45.141.57.22:8390"
PROXIFYRE_PATH = r"proxifyre/proxifyre.exe"
DDNET_PATH = r"ddnets-19.9-win64/hddnet1.exe"

PROTOCOLS = ("vless://", "vmess://", "ss://", "trojan://", "hysteria://", "hysteria2://", "socks5://", "socks://")

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
TEMP_DIR = SCRIPT_DIR / "Temp"
SETTINGS_DIR = SCRIPT_DIR / "Settings"
PROXIES_JSON = SETTINGS_DIR / "proxies.json"
BPROXIES_JSON = SETTINGS_DIR / "bproxies.json"
SPARE_JSON = SETTINGS_DIR / "spare_proxies.json"
SUBS_JSON = SETTINGS_DIR / "subscriptions.json"

TEMP_DIR.mkdir(exist_ok=True)
SETTINGS_DIR.mkdir(exist_ok=True)

# ─── Global state ─────────────────────────────────────────────────────────────

# Shutdown flag — threads check it and exit
_shutdown = threading.Event()

# Port manager: just a set of used ports
_ports_used: set[int] = set()
_ports_lock = threading.Lock()

# DNS cache
_dns_cache: dict[str, str] = {}
_dns_lock = threading.Lock()

# Bridge for DDNet
_bridge_clients: list[socket.socket] = []
_bridge_lock = threading.RLock()  # RLock — safe for nested acquire
_bridge_server: socket.socket | None = None

# Rich
from rich.console import Console
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn

console = Console()

# Frozen (PyInstaller)
if getattr(sys, "frozen", False):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

# ─── Subscriptions ────────────────────────────────────────────────────────────

_DEFAULT_SUBS = [
    "https://raw.githubusercontent.com/RKPchannel/RKP_bypass_configs/refs/heads/main/configs/url_work.txt",
    "https://raw.githubusercontent.com/Ai123999/WhiteKeys/refs/heads/main/WhiteKeys",
    "https://gistpad.com/raw/miata-vpn-free-vless-keys-reverse-engineer-s-basement",
    "https://raw.githubusercontent.com/pyatovsergey0105-maker/-/refs/heads/main/Whie_spiksik",
    "https://github.com/KiryaScript/white-lists/raw/refs/heads/main/githubmirror/28.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/WHITE-SNI-RU-all.txt",
    "https://raw.githubusercontent.com/clowovx/clowovxVPN/refs/heads/main/clowovxVPN",
    "https://raw.githubusercontent.com/whoahaow/rjsxrd/refs/heads/main/githubmirror/bypass/bypass-all.txt",
    "https://raw.githubusercontent.com/Ai123999/WhiteKeys/refs/heads/main/WhiteKeys",
    # "https://raw.githubusercontent.com/ebrasha/free-v2ray-public-list/refs/heads/main/V2Ray-Config-By-EbraSha-All-Type.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    "https://tinyurl.com/SemqkaVLESS",
    "https://raw.githubusercontent.com/cinev505/VlessTrogan-vpn-key/refs/heads/main/WhiteList-VPN-Vless",
    "https://raw.githubusercontent.com/Reallyza/ReallyzaVpn/refs/heads/main/ALL%20CONF-WH%2BWIFI",
    "https://github.com/Reallyza/ReallyzaVpn/blob/main/ALL%20CONF-WH%2BWIFI",
    "https://raw.githubusercontent.com/v0id9/vpn-configs/refs/heads/main/vpn.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Config/refs/heads/main/Splitted-By-Protocol/vmess.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Config/refs/heads/main/Splitted-By-Protocol/vless.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Config/refs/heads/main/Splitted-By-Protocol/trojan.txt",
    "https://raw.githubusercontent.com/barry-far/V2ray-Config/refs/heads/main/Splitted-By-Protocol/ss.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/refs/heads/main/V2RAY_RAW.txt",
    "https://raw.githubusercontent.com/ShatakVPN/ConfigForge-V2Ray/refs/heads/main/configs/all.txt",

    "https://gist.githubusercontent.com/DestroyST6767/f00837ad379aa3272183fdaabcfd50da/raw",
    "https://raw.githubusercontent.com/Reallyza/ReallyzaVpn/refs/heads/main/ALL%20CONF-WH%2BWIFI",
    "https://raw.githubusercontent.com/zieng2/wl/main/vless_lite.txt",
    "https://raw.githubusercontent.com/ProxyScrape/free-proxy-list/refs/heads/main/proxies/protocols/socks5/data.txt",
    "https://raw.githubusercontent.com/ShatakVPN/ConfigForge-V2Ray/refs/heads/main/configs/all.txt",
    "https://raw.githubusercontent.com/pornnewbee/free-vless-VPN/refs/heads/main/vless.txt",
    "https://raw.githubusercontent.com/SoliSpirit/SolVPN/refs/heads/main/Protocols/shadowsocks.txt",
    "https://raw.githubusercontent.com/SoliSpirit/SolVPN/refs/heads/main/Protocols/trojan.txt",
    "https://raw.githubusercontent.com/SoliSpirit/SolVPN/refs/heads/main/Protocols/vless.txt",
    "https://raw.githubusercontent.com/SoliSpirit/SolVPN/refs/heads/main/Protocols/vmess.txt",
]

def load_subs() -> list[str]:
    """Load subscription list from subscriptions.json. If missing — create from defaults."""
    if SUBS_JSON.exists():
        try:
            data = json.loads(SUBS_JSON.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    save_subs(_DEFAULT_SUBS)
    return _DEFAULT_SUBS[:]

def save_subs(urls: list[str]):
    """Save subscription list to subscriptions.json."""
    try:
        SUBS_JSON.write_text(json.dumps(urls, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        console.print(f"[red]❌ Save subscriptions failed: {e}[/red]")

SUB_URLS = load_subs()

# ─── Process management ───────────────────────────────────────────────────────

def kill_all():
    """Kill xray.exe, proxifyre.exe, hddnet1.exe — taskkill in parallel."""
    procs = []
    for name in ("xray.exe", "proxifyre.exe", "hddnet1.exe"):
        try:
            p = subprocess.Popen(
                ["taskkill", "/F", "/IM", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            procs.append(p)
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    time.sleep(0.15)


def _kill_proc(proc, timeout=1.5):
    """terminate → wait → kill for a single Popen object."""
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass
    except Exception:
        pass


def _emergency_cleanup():
    """atexit / finally: kill everything that's left."""
    kill_all()


atexit.register(_emergency_cleanup)


# ─── Port manager ─────────────────────────────────────────────────────────────

def port_alloc(index: int) -> int:
    """Allocate a port for thread with given index. No is_port_free — just
    allocate a unique port from the range. If port is occupied (rare) — xray
    will fail on startup, wait_for_port won't succeed, test will fail."""
    base = START_PORT + index * 2
    with _ports_lock:
        port = base
        while port in _ports_used and port < 65000:
            port += 2
        _ports_used.add(port)
    return port


def port_release(port: int):
    """Release a port. No waiting — just remove from the set."""
    with _ports_lock:
        _ports_used.discard(port)


# ─── Utilities ────────────────────────────────────────────────────────────────

def key_preview(key: str) -> str:
    return key.split("#")[0][:60]


def key_identity(key: str) -> str:
    m = re.match(r"[^:]+://[^@]+@([^?#]+)", key)
    return m.group(1) if m else key


def resolve_host(host: str) -> str:
    with _dns_lock:
        if host in _dns_cache:
            return _dns_cache[host]
    try:
        ip = socket.gethostbyname(host) if not re.match(r"^\d", host) else host
    except Exception:
        ip = host
    with _dns_lock:
        _dns_cache[host] = ip
    return ip


def wait_for_port(port: int, timeout: float = 4.0) -> bool:
    """Wait until port starts accepting TCP connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)
    return False


# ─── Subscription loading (PARALLEL) ──────────────────────────────────────────

def fetch_one_subscription(url: str) -> list[str]:
    """Fetch keys from a single subscription."""
    if url.startswith(PROTOCOLS):
        return [url]
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        content = r.text.strip()
    except Exception:
        return []

    keys: list[str] = []
    # base64
    try:
        padded = content + "=" * (-len(content) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
        raw = [l.strip() for l in decoded.splitlines() if l.strip()]
        if any(l.startswith(PROTOCOLS) for l in raw):
            keys = [l.replace("[", "").replace("]", "") for l in raw]
    except Exception:
        pass

    # Plain text
    if not keys:
        raw = [l.strip() for l in content.splitlines() if l.strip()]
        keys = [l.replace("[", "").replace("]", "") for l in raw]

    # Extract protocol part from lines that have prefixes before the key
    # e.g. "🇪🇸 vless://..." → "vless://..."
    cleaned = []
    for k in keys:
        for proto in PROTOCOLS:
            idx = k.find(proto)
            if idx >= 0:
                cleaned.append(k[idx:])
                break
    return cleaned


def fetch_all_subscriptions(urls: list[str]) -> list[str]:
    """Fetch ALL subscriptions in parallel (up to 10 threads)."""
    all_keys: list[str] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        future_map = {ex.submit(fetch_one_subscription, url): url for url in urls}
        for future in as_completed(future_map, timeout=60):
            if _shutdown.is_set():
                break
            url = future_map[future]
            try:
                keys = future.result()
                if keys:
                    console.print(f"  [green]✓[/green] {url[:65]}  [green]{len(keys)} keys[/green]")
                    all_keys.extend(keys)
                else:
                    console.print(f"  [dim]·[/dim] {url[:65]}  [dim]0[/dim]")
            except Exception:
                console.print(f"  [red]✗[/red] {url[:65]}")
    return all_keys


# ─── TCP + TLS + UDP ping (prefilter) ─────────────────────────────────────────

def tcp_ping(ip: str, port: int, timeout: float = TCP_PING_TIMEOUT) -> float | None:
    """Fast TCP connect to ip:port. Returns latency (ms) or None if unreachable."""
    try:
        start = time.monotonic()
        with socket.create_connection((ip, port), timeout=timeout):
            return round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return None


def tls_ping(ip: str, port: int, sni: str | None = None,
             timeout: float = TLS_PING_TIMEOUT) -> float | None:
    """TLS handshake to ip:port. Returns latency (ms) or None if unreachable."""
    try:
        start = time.monotonic()
        sock = socket.create_connection((ip, port), timeout=timeout)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with ctx.wrap_socket(sock, server_hostname=sni or ip):
            return round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return None


def test_proxy_udp_dns(proxy_port: int) -> float | None:
    """Fast UDP check via SOCKS5 proxy (DNS request to 8.8.8.8). Returns latency (ms) or None."""
    tcp_sock = udp_sock = None
    try:
        start = time.monotonic()
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.settimeout(UDP_PING_TIMEOUT)
        tcp_sock.connect(("127.0.0.1", proxy_port))
        tcp_sock.sendall(b"\x05\x01\x00")
        if tcp_sock.recv(2) != b"\x05\x00": return None
        
        tcp_sock.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
        resp = tcp_sock.recv(10)
        if len(resp) < 10 or resp[1] != 0x00: return None
        
        udp_relay_port = struct.unpack("!H", resp[8:10])[0]
        dns_payload = struct.pack(">HHHHHH", 0xDEAD, 0x0100, 1, 0, 0, 0)
        packet = b"\x00\x00\x00\x01" + socket.inet_aton("8.8.8.8") + struct.pack("!H", 53) + dns_payload
        
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.settimeout(UDP_PING_TIMEOUT)
        udp_sock.sendto(packet, ("127.0.0.1", udp_relay_port))
        
        data, _ = udp_sock.recvfrom(1024)
        if len(data) > 10 and struct.unpack(">H", data[10:12])[0] == 0xDEAD:
            return round((time.monotonic() - start) * 1000, 1)
    except Exception:
        pass
    finally:
        for s in (tcp_sock, udp_sock):
            if s:
                try: s.close()
                except: pass
    return None


# ─── Key parsing ──────────────────────────────────────────────────────────────

def parse_key(key: str) -> dict | None:
    """URI → Xray outbound config."""
    try:
        from python_v2ray.config_parser import parse_uri

        if key.startswith("socks5://"):
            key = "socks://" + key[len("socks5://"):]

        p = parse_uri(key)
        server = getattr(p, "address", None) or getattr(p, "server", None) or getattr(p, "host", None)
        if not server:
            return None

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
            sec = getattr(p, "security", "")
            if sec == "reality":
                ob["streamSettings"]["realitySettings"] = {
                    "serverName": getattr(p, "sni", ""), "fingerprint": "chrome",
                    "publicKey": getattr(p, "pbk", ""), "shortId": getattr(p, "sid", ""),
                    "spiderX": "/"}
            elif sec == "tls":
                ob["streamSettings"]["tlsSettings"] = {
                    "serverName": getattr(p, "sni", server), "allowInsecure": True}
            return ob

        if p.protocol in ("shadowsocks", "ss"):
            return {"protocol": "shadowsocks",
                    "settings": {"servers": [{"address": server, "port": p.port,
                        "method": getattr(p, "method", "chacha20-ietf-poly1305"),
                        "password": getattr(p, "password", "")}]}}

        if p.protocol == "trojan":
            return {"protocol": "trojan",
                    "settings": {"servers": [{"address": server, "port": p.port,
                        "password": getattr(p, "password", getattr(p, "uuid", ""))}]},
                    "streamSettings": {"security": "tls",
                        "tlsSettings": {"serverName": getattr(p, "sni", server),
                                        "allowInsecure": True}}}

        if p.protocol in ("hysteria", "hysteria2"):
            return {"protocol": p.protocol,
                    "settings": {"servers": [{"address": server, "port": p.port,
                        "password": getattr(p, "password", getattr(p, "auth", ""))}]},
                    "streamSettings": {"network": "tcp", "security": "tls",
                        "tlsSettings": {"serverName": getattr(p, "sni", server),
                                        "allowInsecure": True}}}

        if p.protocol == "socks":
            users = []
            user = getattr(p, "id", "")
            passwd = getattr(p, "password", "")
            if user:
                users.append({"user": user, "pass": passwd})
            return {"protocol": "socks",
                    "settings": {"servers": [{"address": server, "port": p.port,
                                               "users": users}]}}
    except Exception:
        pass
    return None


# ─── Proxy test ───────────────────────────────────────────────────────────────

def _xray_config(port: int, outbound: dict) -> dict:
    return {
        "log": {"loglevel": "none"},
        "inbounds": [{"port": port, "listen": "127.0.0.1",
                      "protocol": "socks", "settings": {"auth": "noauth", "udp": True}}],
        "outbounds": [outbound],
    }


def test_proxy(key: str, port: int, outbound: dict | None = None) -> tuple[float, str | None, float | None] | None:
    """Launch xray, test HTTP via socks5 + background UDP check.
    Returns (xray_ping_ms, exit_ip, udp_latency_ms) or None."""
    if _shutdown.is_set():
        return None

    if outbound is None:
        outbound = parse_key(key)
        if outbound is None:
            return None

    cfg = TEMP_DIR / f"_t{port}.json"
    try:
        cfg.write_text(json.dumps(_xray_config(port, outbound)), encoding="utf-8")
    except Exception:
        return None

    proc = None
    try:
        proc = subprocess.Popen(
            [XRAY_PATH, "-c", str(cfg)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if not wait_for_port(port, timeout=4.0):
            return None

        proxies = {"http": f"socks5://127.0.0.1:{port}",
                   "https": f"socks5://127.0.0.1:{port}"}
        
        # ── Get exit IP (also serves as XRAY ping) + Background UDP check ──
        exit_ip = None
        xray_ping = None
        udp_latency = None
        
        udp_res = [None]
        udp_done = threading.Event()

        def _bg_udp():
            udp_res[0] = test_proxy_udp_dns(port)
            udp_done.set()

        threading.Thread(target=_bg_udp, daemon=True).start()

        if not _shutdown.is_set():
            start = time.monotonic()
            try:
                # Race all IP check URLs — use whichever responds first
                _ip_result: list[str | None] = [None]
                _ip_done = threading.Event()

                def _check_one(url):
                    if _ip_done.is_set():
                        return
                    try:
                        r = requests.get(url, proxies=proxies, timeout=IP_CHECK_TIMEOUT)
                        if r.status_code == 200:
                            ip = r.text.strip()
                            if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                                _ip_result[0] = ip
                                _ip_done.set()
                    except Exception:
                        pass

                for url in IP_CHECK_URLS:
                    threading.Thread(target=_check_one, args=(url,), daemon=True).start()

                _ip_done.wait(timeout=IP_CHECK_TIMEOUT)
                xray_ping = round((time.monotonic() - start) * 1000, 1)
                exit_ip = _ip_result[0]
                if exit_ip is None:
                    return None
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, OSError):
                return None
            except Exception:
                pass

        # Wait for UDP check to complete (max UDP_PING_TIMEOUT + 1s margin)
        if xray_ping is not None and not udp_done.is_set():
            udp_done.wait(timeout=UDP_PING_TIMEOUT + 1.0)
            
        udp_latency = udp_res[0]

        if exit_ip is not None and xray_ping is not None:
            return (xray_ping, exit_ip, udp_latency)
        
        return None

    except Exception:
        return None
    finally:
        if proc is not None:
            _kill_proc(proc)
        try:
            cfg.unlink(missing_ok=True)
        except Exception:
            pass


# ─── Bridge for DDNet ─────────────────────────────────────────────────────────

def start_bridge():
    """TCP server on 127.0.0.1:5555 for bridge connections."""
    global _bridge_server
    _bridge_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _bridge_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _bridge_server.bind(("127.0.0.1", 5555))
    _bridge_server.listen(10)
    _bridge_server.settimeout(1.0)

    while not _shutdown.is_set():
        try:
            conn, _ = _bridge_server.accept()
            conn.settimeout(5.0)
            with _bridge_lock:
                _bridge_clients.append(conn)
        except socket.timeout:
            continue
        except OSError:
            break


def stop_bridge():
    """Stop bridge and close all client connections."""
    global _bridge_server
    with _bridge_lock:
        for c in _bridge_clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except Exception:
                pass
        _bridge_clients.clear()
    if _bridge_server:
        try:
            _bridge_server.close()
        except Exception:
            pass
        _bridge_server = None


def bridge_send(cmd: str) -> bool:
    """Send command via bridge. Does NOT hold lock during I/O."""
    with _bridge_lock:
        if not _bridge_clients:
            return False
        target = _bridge_clients[0]
    try:
        target.sendall(cmd.encode())
        return True
    except Exception:
        with _bridge_lock:
            for c in _bridge_clients:
                try:
                    c.close()
                except Exception:
                    pass
            _bridge_clients.clear()
        return False


def bridge_clear():
    """Close all bridge clients."""
    with _bridge_lock:
        for c in _bridge_clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except Exception:
                pass
        _bridge_clients.clear()

# ─── Ban list (bproxies.json) ─────────────────────────────────────────────────

def load_banned_ips() -> set[str]:
    """Load ban list of exit IPs from bproxies.json."""
    if BPROXIES_JSON.exists():
        try:
            data = json.loads(BPROXIES_JSON.read_text(encoding="utf-8"))
            return set(data) if isinstance(data, list) else set()
        except Exception:
            return set()
    return set()


def save_banned_ips(banned: set[str]):
    """Save ban list of exit IPs to bproxies.json."""
    try:
        BPROXIES_JSON.write_text(json.dumps(sorted(banned), indent=2), encoding="utf-8")
    except Exception:
        pass


def ban_ip(ip: str, banned: set[str]):
    """Add IP to ban list and immediately save to disk."""
    banned.add(ip)
    save_banned_ips(banned)
    console.print(f"    [dim]🚫 IP {ip} → bproxies.json[/dim]")

# ─── DDNet visual test ────────────────────────────────────────────────────────

def run_visual_test(key: str, idx_info: str, exit_ip: str | None = None, banned_ips: set[str] | None = None) -> bool:
    """xray + proxifyre + hddnet1 → test connection to server."""
    console.print(f"\n[cyan]🧪 {idx_info} DDNet: {key_preview(key)}[/cyan]")

    kill_all()
    time.sleep(0.15)

    if _shutdown.is_set():
        return False

    outbound = parse_key(key)
    if not outbound:
        console.print("    [red]❌ Parse failed[/red]")
        return False

    cfg = TEMP_DIR / "visual_test.json"
    try:
        cfg.write_text(json.dumps(_xray_config(GAME_TEST_PORT, outbound)), encoding="utf-8")
    except Exception:
        return False

    xray_proc = proxy_proc = game_proc = None

    try:
        # ── xray.exe ──
        xray_proc = subprocess.Popen(
            [XRAY_PATH, "-c", str(cfg)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not wait_for_port(GAME_TEST_PORT, timeout=6.0):
            console.print("    [red]❌ Xray failed[/red]")
            return False

        # ── proxifyre.exe ──
        proxy_proc = subprocess.Popen(
            [PROXIFYRE_PATH],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(0.5)

        # ── hddnet1.exe (DDNet) ──
        si = subprocess.STARTUPINFO(dwFlags=1, wShowWindow=0)
        game_proc = subprocess.Popen(
            [DDNET_PATH],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            startupinfo=si,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        # Read DDNet stdout
        q: queue.Queue[str | None] = queue.Queue()

        def _reader(out, q):
            try:
                for line in iter(out.readline, ""):
                    q.put(line)
            except Exception:
                pass
            finally:
                q.put(None)
                try:
                    out.close()
                except Exception:
                    pass

        threading.Thread(target=_reader, args=(game_proc.stdout, q), daemon=True).start()

        # Main loop
        deadline = time.monotonic() + GAME_BASE_TIMEOUT
        sent = False
        success = False
        exited = False

        while time.monotonic() < deadline:
            if _shutdown.is_set():
                break

            # Send bridge command
            if not sent and not exited:
                with _bridge_lock:
                    has_client = bool(_bridge_clients)
                if has_client:
                    time.sleep(2)
                    sent = bridge_send(f"player_name testbot; player_clan \"\"; player_skin default; connect {TARGET_SERVER}\n")

            # Process stdout
            while not q.empty():
                item = q.get_nowait()
                if item is None:
                    exited = True
                    break
                line = item.strip()
                if not line:
                    continue

                if "E datafile: failed to open file 'maps/" in line:
                    deadline += GAME_EXTEND_TIMEOUT
                    console.print("    [yellow]⏰ Map loading...[/yellow]")

                low = line.lower()
                if any(x in low for x in ("entering game", "map loaded", "welcome", "got pong from current server")):
                    console.print("    [green]✅ Connected[/green]")
                    return True

                if any(x in low for x in ("vpn detected", "banned", "disconnected", "wrong password")):
                    console.print("    [red]❌ Banned[/red]")
                    if exit_ip and banned_ips is not None:
                        ban_ip(exit_ip, banned_ips)
                    return False

            if exited:
                break
            time.sleep(0.05)

        if not success:
            console.print("    [red]❌ Timeout[/red]")
        return success

    except Exception as exc:
        console.print(f"    [red]❌ Error: {exc}[/red]")
        return False
    finally:
        for p in (game_proc, proxy_proc, xray_proc):
            if p is not None:
                _kill_proc(p)
        kill_all()
        bridge_clear()
        try:
            cfg.unlink(missing_ok=True)
        except Exception:
            pass


# ─── Save results ─────────────────────────────────────────────────────────────

def save_proxies(keys: list[str]):
    proxies = [{"port": 10801 + i, "key": k} for i, k in enumerate(keys)]
    try:
        PROXIES_JSON.write_text(json.dumps(proxies, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"\n[green]✅ {PROXIES_JSON} — {len(keys)} keys[/green]")
    except Exception as e:
        console.print(f"[red]❌ Save failed: {e}[/red]")


def save_spare(keys: list[str]):
    try:
        SPARE_JSON.write_text(json.dumps(keys, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]✅ {len(keys)} spare → {SPARE_JSON}[/green]")
    except Exception as e:
        console.print(f"[red]❌ Save failed: {e}[/red]")


# ─── Thread-safe counter ──────────────────────────────────────────────────────

class Counter:
    def __init__(self, n=0):
        self._v = n
        self._lock = threading.Lock()

    def inc(self) -> int:
        with self._lock:
            self._v += 1
            return self._v


# ─── Main function ────────────────────────────────────────────────────────────

def main():
    global TARGET_SERVER, TCP_PING_TIMEOUT, TLS_PING_TIMEOUT, UDP_PING_TIMEOUT, IP_CHECK_TIMEOUT

    # Arguments
    use_spare = False
    spare_count = SPARE_COUNT
    top_n = TOP_N

    for arg in sys.argv[1:]:
        if arg.startswith("--target-server="):
            TARGET_SERVER = arg.split("=", 1)[1]
        elif arg.startswith("--spare-proxies"):
            use_spare = True
            if "=" in arg:
                try:
                    spare_count = int(arg.split("=", 1)[1])
                except ValueError:
                    pass
        elif arg.startswith("--timeout="):
            try:
                t = int(arg.split("=", 1)[1]) / 1000.0
                TCP_PING_TIMEOUT = t
                TLS_PING_TIMEOUT = t
                UDP_PING_TIMEOUT = t
                IP_CHECK_TIMEOUT = t
            except ValueError:
                pass
        elif arg.startswith("--top-n="):
            try:
                top_n = int(arg.split("=", 1)[1])
            except ValueError:
                pass

    console.print(f"[cyan]📡 Target: {TARGET_SERVER}[/cyan]")
    console.print(f"[cyan]🎯 Top: {top_n} | Spare: {spare_count if use_spare else 'off'}[/cyan]")
    if any(a.startswith("--timeout=") for a in sys.argv[1:]):
        console.print(f"[cyan]⌛ Timeout: {TCP_PING_TIMEOUT}s[/cyan]")

    _ctrl_c_count = 0

    def _on_signal(signum, frame):
        nonlocal _ctrl_c_count
        _ctrl_c_count += 1
        _shutdown.set()
        if _ctrl_c_count >= 2:
            kill_all()
            os._exit(0)
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _on_signal)
    try:
        signal.signal(signal.SIGTERM, _on_signal)
    except (OSError, AttributeError):
        pass

    # Bridge
    threading.Thread(target=start_bridge, daemon=True).start()

    try:
        # ══════════════════════════════════════════════════════════════════
        # 1. LOAD SUBSCRIPTIONS (parallel)
        # ══════════════════════════════════════════════════════════════════
        console.print("\n[bold]📥 Fetching subscriptions (parallel)...[/bold]")
        all_keys = fetch_all_subscriptions(SUB_URLS)

        if not all_keys:
            console.print("[red]❌ No keys fetched[/red]")
            return

        console.print(f"\n[cyan]📊 Total: {len(all_keys)} raw keys[/cyan]")

        # ══════════════════════════════════════════════════════════════════
        # 2. DEDUPLICATION + FILTERING (fast, in-memory)
        # ══════════════════════════════════════════════════════════════════
        seen: set[str] = set()
        deduped: list[str] = []
        for k in all_keys:
            ident = key_identity(k)
            if ident not in seen:
                seen.add(ident)
                deduped.append(k)

        keys = [k for k in deduped if not any(f in k.lower() for f in KEY_FILTER)]
        console.print(f"[green]🔍 {len(keys)} unique keys to test[/green]")

        if not keys:
            console.print("[red]❌ No keys to test[/red]")
            return

        # ══════════════════════════════════════════════════════════════════
        # 3. PROXY TESTING (ThreadPoolExecutor, polling)
        # ══════════════════════════════════════════════════════════════════
        results: list[tuple[float, str | None, float | None, str]] = []  # (ping, exit_ip, udp_latency, key)
        results_lock = threading.Lock()
        counter = Counter(0)
        total = len(keys)

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]Testing:[/] {task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        )
        task_id = progress.add_task("", total=total)

        def _test_one(i: int, key: str):
            """Test a single key: TCP/TLS ping → xray test."""
            if _shutdown.is_set():
                progress.update(task_id, advance=1)
                return

            # ── Parse key once ──
            outbound = parse_key(key)
            if outbound is None:
                done = counter.inc()
                progress.update(task_id, advance=1)
                return

            # ── Extract address/port from outbound config ──
            settings = outbound.get("settings", {})
            vnext = settings.get("vnext")
            if vnext:
                host = vnext[0].get("address", "")
                port_srv = vnext[0].get("port", 0)
            else:
                servers = settings.get("servers", [])
                host = servers[0].get("address", "") if servers else ""
                port_srv = servers[0].get("port", 0) if servers else 0

            if not host or not port_srv:
                progress.update(task_id, advance=1)
                return

            # ── DNS resolve ──
            ip = resolve_host(host)

            # ── Determine if TLS is needed ──
            stream = outbound.get("streamSettings", {})
            security = stream.get("security", "")
            needs_tls = security in ("tls", "reality")
            sni = None
            if needs_tls:
                tls_key = "tlsSettings" if security == "tls" else "realitySettings"
                sni = stream.get(tls_key, {}).get("serverName") or host

            # ── TCP ping ──
            tcp = tcp_ping(ip, port_srv)

            # ── TLS ping (if protocol requires it) ──
            tls = None
            if needs_tls and tcp is not None:
                tls = tls_ping(ip, port_srv, sni)

            # ── Dead server? → skip ──
            if tcp is None or (needs_tls and tls is None):
                done = counter.inc()
                with results_lock:
                    pv = key_preview(key)[:45]
                    console.print(f"  ❌ [{done}/{total}] {pv}")
                    progress.update(task_id, advance=1)
                return

            # ── Ping passed → full xray test ──
            port = port_alloc(i)
            try:
                res = test_proxy(key, port, outbound)
            finally:
                port_release(port)

            done = counter.inc()
            with results_lock:
                if _shutdown.is_set():
                    return
                pv = key_preview(key)[:45]
                ping_parts = [f"TCP:{tcp}ms"]
                if needs_tls and tls is not None:
                    ping_parts.append(f"TLS:{tls}ms")
                if res is not None:
                    ping_ms, exit_ip, udp_latency = res
                    if exit_ip is not None and udp_latency is not None:
                        ping_parts.append(f"UDP:{udp_latency}ms")
                        ping_parts.append(f"XRAY:{ping_ms}ms")
                        console.print(f"  ✅ [{done}/{total}] {pv}  {'  '.join(ping_parts)}  IP:{exit_ip}")
                        results.append((ping_ms, exit_ip, udp_latency, key))
                    else:
                        console.print(f"  ⚠️ [{done}/{total}] {pv}  {'  '.join(ping_parts)}")
                else:
                    console.print(f"  ⚠️ [{done}/{total}] {pv}  {'  '.join(ping_parts)}")
                progress.update(task_id, advance=1)

        # Run tests
        ex = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        futures = [ex.submit(_test_one, i, k) for i, k in enumerate(keys)]
        completed = 0
        _total_futures = len(futures)

        try:
            with Live(progress, console=console, refresh_per_second=12):
                while completed < _total_futures:
                    if _shutdown.is_set():
                        break
                    still_running = []
                    for f in futures:
                        if f.done():
                            completed += 1
                            try:
                                f.result()
                            except Exception:
                                pass
                        else:
                            still_running.append(f)
                    futures = still_running
                    time.sleep(0.08)  # polling ~12 times/sec — fast enough
        except KeyboardInterrupt:
            _shutdown.set()
        finally:
            # Don't wait for threads — kill everything
            ex.shutdown(wait=False)
            kill_all()

        if _shutdown.is_set():
            console.print("\n[yellow]⚠️ Interrupted during testing[/yellow]")
            # Still show what we managed to find
            if not results:
                return

        console.print(f"\n[green]✅ Working: {len(results)}[/green]")

        if not results:
            console.print("[red]❌ No working proxies[/red]")
            return

        # ══════════════════════════════════════════════════════════════════
        # 4. DEDUPLICATION BY EXIT IP
        # ══════════════════════════════════════════════════════════════════
        best: dict[str, tuple[float, str | None, float | None, str]] = {}
        for ping, exit_ip, udp_latency, key in results:
            dedup_key = exit_ip or resolve_host(key_identity(key).split(":")[0])
            if dedup_key not in best or ping < best[dedup_key][0]:
                best[dedup_key] = (ping, exit_ip, udp_latency, key)
        results = sorted(best.values(), key=lambda x: x[0])
        console.print(f"[green]✅ {len(results)} unique exit IPs[/green]")

        # ══════════════════════════════════════════════════════════════════
        # 5. LOAD BAN LIST + FILTERING
        # ══════════════════════════════════════════════════════════════════
        banned_ips = load_banned_ips()

        results_filtered: list[tuple[float, str | None, float | None, str]] = []
        for ping, exit_ip, udp_latency, key in results:
            if exit_ip and exit_ip in banned_ips:
                continue
            results_filtered.append((ping, exit_ip, udp_latency, key))

        skipped = len(results) - len(results_filtered)
        if skipped:
            console.print(f"[yellow]🚫 Banned IPs: {skipped}[/yellow]")
        results = results_filtered

        if not results:
            console.print("[red]❌ No proxies left after ban filter[/red]")
            return

        # ══════════════════════════════════════════════════════════════════
        # 6. DDNET VALIDATION
        # ══════════════════════════════════════════════════════════════════
        console.print(f"\n[cyan]🎮 DDNet validation (need {top_n})...[/cyan]")
        confirmed: list[str] = []
        tested: set[int] = set()

        for idx, (ping, exit_ip, udp_latency, key) in enumerate(results):
            if _shutdown.is_set():
                break
            if len(confirmed) >= top_n:
                break
            tested.add(idx)
            if run_visual_test(key, f"#{len(confirmed) + 1}", exit_ip=exit_ip, banned_ips=banned_ips):
                confirmed.append(key)

        if not confirmed:
            console.print("[red]❌ No confirmed proxies[/red]")
            return

        save_proxies(confirmed)

        # ══════════════════════════════════════════════════════════════════
        # 7. SPARE PROXIES
        # ══════════════════════════════════════════════════════════════════
        if use_spare:
            console.print(f"\n[cyan]📦 Spare ({spare_count})...[/cyan]")
            spare: list[str] = []
            for idx, (ping, exit_ip, udp_latency, key) in enumerate(results):
                if _shutdown.is_set():
                    break
                if len(spare) >= spare_count:
                    break
                if idx in tested:
                    continue
                if run_visual_test(key, f"spare #{len(spare) + 1}", exit_ip=exit_ip, banned_ips=banned_ips):
                    spare.append(key)
            if spare:
                save_spare(spare)
            else:
                console.print("[yellow]⚠️ No spare proxies[/yellow]")

        console.print("\n[bold green]🎉 Done![/bold green]")

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ Interrupted by user[/yellow]")
    finally:
        _shutdown.set()
        kill_all()
        stop_bridge()


if __name__ == "__main__":
    main()
