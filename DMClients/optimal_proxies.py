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

import threading
import queue
import atexit
import random
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

# When True, proxies whose exit IP is in bproxies.json are SKIPPED during
# the selection loop. When False, banned IPs are still RECORDED (ban_ip()
# is always called when a proxy gets banned in-game) but not filtered out —
# useful for servers without IP-based protection.
USE_BANNED_FILTER = True

# Comma-separated list of DDNet servers to test proxies against.
# A random one is picked at startup (see main()).
TARGET_SERVERS = ["45.141.57.22:8390", "46.174.54.240:8406", "46.174.54.240:8451", "46.174.54.240:8360"]
# Backward-compat: legacy code reads TARGET_SERVER as a single string.
TARGET_SERVER = TARGET_SERVERS[0] if TARGET_SERVERS else "45.141.57.22:8390"
DDNET_PATH = r"DDNet-19.9-win64/DDNet.exe"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

PROTOCOLS = ("vless://", "vmess://", "ss://", "shadowsocks://", "trojan://", "hysteria://", "hysteria2://", "hy://", "hy2://", "socks5://", "socks://")

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
    # Working:
    "https://raw.githubusercontent.com/whoahaow/rjsxrd/refs/heads/main/githubmirror/bypass/bypass-all.txt",
    "https://raw.githubusercontent.com/Ai123999/WhiteKeys/refs/heads/main/WhiteKeys",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    "https://raw.githubusercontent.com/ebrasha/free-v2ray-public-list/refs/heads/main/V2Ray-Config-By-EbraSha.txt",
    "https://openproxylist.com/v2ray/rawlist/subscribe",
    "https://raw.githubusercontent.com/RKPchannel/RKP_bypass_configs/refs/heads/main/whitelist.txt",
    "https://raw.githubusercontent.com/RKPchannel/RKP_bypass_configs/refs/heads/main/blacklist.txt",

    "https://raw.githubusercontent.com/ShatakVPN/ConfigForge-V2Ray/refs/heads/main/configs/all.txt",
    "https://raw.githubusercontent.com/v0id9/vpn-configs/refs/heads/main/vpn.txt",

    # 300–1500:
    "https://raw.githubusercontent.com/Reallyza/ReallyzaVpn/refs/heads/main/ALL%20CONF-WH%2BWIFI",
    "https://raw.githubusercontent.com/KiryaScript/white-lists/refs/heads/main/githubmirror/28.txt",
    "https://raw.githubusercontent.com/pornnewbee/free-vless-VPN/refs/heads/main/vless.txt",
    "https://raw.githubusercontent.com/zieng2/wl/main/vless_lite.txt",
    "https://raw.githubusercontent.com/SoliSpirit/SolVPN/refs/heads/main/all_configs.txt",

    # 100–300:
    "https://raw.githubusercontent.com/roosterkid/openproxylist/refs/heads/main/V2RAY_RAW.txt",

    # 10–100:
    "https://gist.githubusercontent.com/DestroyST6767/f00837ad379aa3272183fdaabcfd50da/raw",
    "https://raw.githubusercontent.com/cinev505/VlessTrogan-vpn-key/refs/heads/main/WhiteList-VPN-Vless",
    "https://raw.githubusercontent.com/pyatovsergey0105-maker/-/refs/heads/main/Whie_spiksik",

    # <10:
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/WHITE-SNI-RU-all.txt",
    "https://tinyurl.com/SemqkaVLESS",
    "https://gistpad.com/raw/miata-vpn-free-vless-keys-reverse-engineer-s-basement",
    "https://raw.githubusercontent.com/RKPchannel/RKP_bypass_configs/refs/heads/main/configs/url_work.txt",
    "https://raw.githubusercontent.com/clowovx/clowovxVPN/refs/heads/main/clowovxVPN",
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
    """Kill xray.exe, DDNet.exe — taskkill in parallel."""
    procs = []
    for name in ("xray.exe", "DDNet.exe"):
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
    """Allocate a port for thread with given index."""
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


def wait_for_port(port: int, timeout: float = 5.0) -> bool:
    """Wait until port starts accepting TCP connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)
    return False


# ─── Subscription loading (PARALLEL) ──────────────────────────────────────────

def _try_base64_decode(text: str) -> str | None:
    """Try to base64-decode text with multiple strategies. Returns decoded string or None."""
    text = text.strip()
    if not text:
        return None
    # Strategy 1: Standard base64 with padding
    try:
        padded = text + "=" * (-len(text) % 4)
        return base64.b64decode(padded).decode("utf-8", errors="ignore")
    except Exception:
        pass
    # Strategy 2: URL-safe base64 with padding
    try:
        padded = text + "=" * (-len(text) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
    except Exception:
        pass
    # Strategy 3: Try without any padding
    try:
        return base64.b64decode(text).decode("utf-8", errors="ignore")
    except Exception:
        pass
    return None


def _extract_keys_from_lines(text: str) -> list[str]:
    """Extract proxy keys from text content (line by line), cleaning noise."""
    keys = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Remove BOM, brackets
        line = line.lstrip('\ufeff')
        line = line.replace("[", "").replace("]", "")
        # Find protocol prefix — skip any noise before it (emoji, spaces, etc.)
        for proto in PROTOCOLS:
            idx = line.find(proto)
            if idx >= 0:
                keys.append(line[idx:])
                break
    return keys


def _clean_key(key: str) -> str | None:
    """Clean a single key: strip noise, extract protocol part. Returns None if not a valid key."""
    key = key.strip()
    key = key.lstrip('\ufeff')
    # Remove surrounding quotes
    if len(key) >= 2 and ((key.startswith('"') and key.endswith('"')) or
                           (key.startswith("'") and key.endswith("'"))):
        key = key[1:-1].strip()
    # Trim everything before the protocol prefix
    for proto in PROTOCOLS:
        idx = key.find(proto)
        if idx >= 0:
            return key[idx:]
    return None


def fetch_one_subscription(url: str) -> list[str]:
    """Fetch keys from a single subscription with robust multi-strategy extraction."""
    if url.startswith(PROTOCOLS):
        return [url]
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        content = r.text.strip()
    except Exception:
        return []

    content = content.lstrip('\ufeff')
    if not content:
        return []

    keys: list[str] = []
    seen_keys: set[str] = set()

    def _add_key(k: str):
        cleaned = _clean_key(k)
        if cleaned and cleaned not in seen_keys:
            seen_keys.add(cleaned)
            keys.append(cleaned)

    # Strategy 1: Try base64 decode entire content
    decoded = _try_base64_decode(content)
    if decoded:
        for k in _extract_keys_from_lines(decoded):
            _add_key(k)

    # Strategy 2: Plain text extraction (always try — catches keys that base64 missed)
    for k in _extract_keys_from_lines(content):
        _add_key(k)

    # Strategy 3: Line-by-line base64 decode (for mixed content)
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        is_key = any(line.startswith(p) for p in PROTOCOLS)
        if is_key:
            continue
        decoded_line = _try_base64_decode(line)
        if decoded_line:
            for k in _extract_keys_from_lines(decoded_line):
                _add_key(k)

    return keys


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
    """UDP check via SOCKS5 proxy (STUN Binding Request to Google STUN).
    Returns latency (ms) or None."""
    try:
        import socks as pysocks
        start = time.monotonic()
        # AF_INET socket — PySocks builds the SOCKS5 UDP request header with
        # ATYP=4 (IPv6) automatically when the destination is an IPv6 string.
        s = pysocks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
        s.set_proxy(pysocks.SOCKS5, "127.0.0.1", proxy_port)
        s.settimeout(UDP_PING_TIMEOUT)

        # STUN Binding Request (RFC 5389):
        #   0x0001 = Binding Request, 0x2112 = magic cookie, 12 random bytes
        stun_packet = b'\x00\x01\x00\x00\x21\x12\xa4\x42' + os.urandom(12)

        s.sendto(stun_packet, ("2001:4860:4864:5:8000::1", 19302))
        s.recv(128)
        s.close()
        return round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return None


# ─── Key parsing ──────────────────────────────────────────────────────────────

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
                outbound.pop("tag", None)
                return outbound

        # ----- Hysteria / Hysteria2 (manual) -----
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
        console.print(f"[red]Parse error: {e}[/red]")
        return None


def _xray_config(port: int, outbound: dict) -> dict:
    """Xray config — identical structure to test.py (proven to work)."""
    return {
        "log": {"loglevel": "none"},
        "dns": {"servers": ["8.8.8.8", "1.1.1.1"], "tag": "dns-module"},
        "inbounds": [{
            "port": port,
            "listen": "127.0.0.1",
            "protocol": "mixed",
            "sniffing": {
                "enabled": True,
                "destOverride": ["http", "tls"],
                "routeOnly": False
            },
            "settings": {"auth": "noauth", "udp": True}
        }],
        "outbounds": [
            outbound,
            {"tag": "direct", "protocol": "freedom", "settings": {}}
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "inboundTag": "dns-module", "outboundTag": "direct"},
                {"type": "field", "port": "53", "outboundTag": "direct"}
            ]
        }
    }


def test_proxy(key: str, port: int, outbound: dict | None = None) -> tuple[float, str | None, float | None] | None:
    """Launch xray, test HTTP via socks5h + background UDP check.
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

        if not wait_for_port(port, timeout=5.0):
            return None

        # socks5h — DNS резолвинг на стороне xray, не на клиенте
        proxies = {
            "http":  f"socks5h://127.0.0.1:{port}",
            "https": f"socks5h://127.0.0.1:{port}"
        }
        headers = {"User-Agent": USER_AGENT}

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
                        r = requests.get(url, proxies=proxies, headers=headers,
                                         timeout=IP_CHECK_TIMEOUT)
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
    """xray + DDNet.exe (with c_proxy) → test connection to server."""
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

    xray_proc = game_proc = None
    proxy_proc = None  # kept for finally-block compatibility (no longer used)
    _disconnect_sent = False  # track so we don't send disconnect twice

    def _send_disconnect():
        """Send 'disconnect' to the DDNet client via bridge so it leaves the
        server cleanly before we kill the process. Idempotent."""
        nonlocal _disconnect_sent
        if _disconnect_sent:
            return
        try:
            with _bridge_lock:
                if _bridge_clients:
                    bridge_send("disconnect\n")
                    _disconnect_sent = True
        except Exception:
            pass

    try:
        # ── xray.exe ──
        xray_proc = subprocess.Popen(
            [XRAY_PATH, "-c", str(cfg)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not wait_for_port(GAME_TEST_PORT, timeout=6.0):
            console.print("    [red]❌ Xray failed[/red]")
            return False

        # ── DDNet.exe (no ProxiFyre — client routes via c_proxy command) ──
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

            if not sent and not exited:
                with _bridge_lock:
                    has_client = bool(_bridge_clients)
                if has_client:
                    time.sleep(2)
                    # Route all client traffic through the SOCKS5 proxy started above.
                    bridge_send(f"c_proxy 1 127.0.0.1:{GAME_TEST_PORT}\n")
                    time.sleep(0.5)
                    # Pick a random target server for THIS connect attempt.
                    target = random.choice(TARGET_SERVERS) if TARGET_SERVERS else TARGET_SERVER
                    console.print(f"    [dim]🎲 Target: {target}[/dim]")
                    sent = bridge_send(f"player_name testbot; player_clan \"\"; player_skin default; connect {target}\n")

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
                if any(x in low for x in ("entering game", "map loaded", "welcome", "got pong from current server", "i motd", "chat/server")):
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
        # Send 'disconnect' to DDNet client via bridge so it leaves the server
        # cleanly, then wait 0.5s before killing the process.
        if game_proc is not None and game_proc.poll() is None:
            _send_disconnect()
            time.sleep(0.5)
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
    """Save keys to proxies.json in the new format (settings + proxies list).

    This is the "optimal proxies" pool — the candidate list that the UI's
    "Check Proxy" button will later filter down into checked_proxies.json.
    Ports are implied: 10801 + index. Existing settings are preserved."""
    data = {}
    try:
        if PROXIES_JSON.exists():
            with open(PROXIES_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}
    if "settings" not in data or not isinstance(data["settings"], dict):
        data["settings"] = {}
    data["proxies"] = list(keys)
    try:
        PROXIES_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
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
    global TARGET_SERVERS, TARGET_SERVER, TCP_PING_TIMEOUT, TLS_PING_TIMEOUT, UDP_PING_TIMEOUT, IP_CHECK_TIMEOUT, MAX_WORKERS, USE_BANNED_FILTER

    use_spare = False
    spare_count = SPARE_COUNT
    top_n = TOP_N
    use_ddnet_test = True

    for arg in sys.argv[1:]:
        if arg.startswith("--target-servers="):
            # Comma-separated list of servers. A random one is picked before
            # every connect attempt in _test_proxy_in_game (not just once here).
            raw = arg.split("=", 1)[1]
            TARGET_SERVERS = [s.strip() for s in raw.split(",") if s.strip()]
            if TARGET_SERVERS:
                TARGET_SERVER = TARGET_SERVERS[0]  # placeholder; overwritten per-test
        elif arg.startswith("--target-server="):
            # Backward-compat: single server from old --target-server=...
            TARGET_SERVER = arg.split("=", 1)[1]
            TARGET_SERVERS = [TARGET_SERVER]
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
        elif arg.startswith("--threads="):
            try:
                MAX_WORKERS = int(arg.split("=", 1)[1])
                if MAX_WORKERS < 1:
                    MAX_WORKERS = 1
            except ValueError:
                pass
        elif arg.startswith("--skip-ddnet"):
            use_ddnet_test = False
        elif arg.startswith("--banned-filter="):
            USE_BANNED_FILTER = arg.split("=", 1)[1].lower() in ("1", "true", "yes", "on")
        elif arg.startswith("--top-n="):
            try:
                top_n = int(arg.split("=", 1)[1])
            except ValueError:
                pass

    console.print(f"[cyan]📡 Target servers: {TARGET_SERVERS}[/cyan]")
    console.print(f"[cyan]🎯 Top: {top_n} | Spare: {spare_count if use_spare else 'off'}[/cyan]")
    console.print(f"[cyan]🔧 Threads: {MAX_WORKERS} | DDNet test: {'on' if use_ddnet_test else 'off'}[/cyan]")
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

    threading.Thread(target=start_bridge, daemon=True).start()

    try:
        # 1. LOAD SUBSCRIPTIONS (parallel)
        console.print("\n[bold]📥 Fetching subscriptions (parallel)...[/bold]")
        all_keys = fetch_all_subscriptions(SUB_URLS)

        if not all_keys:
            console.print("[red]❌ No keys fetched[/red]")
            return

        console.print(f"\n[cyan]📊 Total: {len(all_keys)} raw keys[/cyan]")

        # 2. DEDUPLICATION + FILTERING
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

        # 3. PROXY TESTING (ThreadPoolExecutor, polling)
        results: list[tuple[float, str | None, float | None, str]] = []
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

            outbound = parse_key(key)
            if outbound is None:
                done = counter.inc()
                progress.update(task_id, advance=1)
                return

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

            ip = resolve_host(host)

            stream = outbound.get("streamSettings", {})
            security = stream.get("security", "")
            needs_tls = security in ("tls", "reality")
            sni = None
            if needs_tls:
                tls_key = "tlsSettings" if security == "tls" else "realitySettings"
                sni = stream.get(tls_key, {}).get("serverName") or host

            protocol = outbound.get("protocol", "")
            is_udp = protocol in ("hysteria", "hysteria2")

            tcp = None
            tls = None
            ping_parts: list[str] = []

            if not is_udp:
                tcp = tcp_ping(ip, port_srv)
                if needs_tls and tcp is not None:
                    tls = tls_ping(ip, port_srv, sni)

                if tcp is None or (needs_tls and tls is None):
                    done = counter.inc()
                    with results_lock:
                        pv = key_preview(key)[:45]
                        console.print(f"  ❌ [{done}/{total}] {pv}")
                        progress.update(task_id, advance=1)
                    return

                ping_parts.append(f"TCP:{tcp}ms")
                if needs_tls and tls is not None:
                    ping_parts.append(f"TLS:{tls}ms")

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
                    time.sleep(0.08)
        except KeyboardInterrupt:
            _shutdown.set()
        finally:
            ex.shutdown(wait=False)
            kill_all()

        if _shutdown.is_set():
            console.print("\n[yellow]⚠️ Interrupted during testing[/yellow]")
            if not results:
                return

        console.print(f"\n[green]✅ Working: {len(results)}[/green]")

        if not results:
            console.print("[red]❌ No working proxies[/red]")
            return

        # 4. DEDUPLICATION BY EXIT IP
        best: dict[str, tuple[float, str | None, float | None, str]] = {}
        for ping, exit_ip, udp_latency, key in results:
            dedup_key = exit_ip or resolve_host(key_identity(key).split(":")[0])
            if dedup_key not in best or ping < best[dedup_key][0]:
                best[dedup_key] = (ping, exit_ip, udp_latency, key)
        results = sorted(best.values(), key=lambda x: x[0])
        console.print(f"[green]✅ {len(results)} unique exit IPs[/green]")

        # 5. LOAD BAN LIST + FILTERING
        # banned_ips is always loaded (so ban_ip() can still record new bans
        # during the in-game test), but the filter step is gated by USE_BANNED_FILTER.
        banned_ips = load_banned_ips()

        if USE_BANNED_FILTER:
            results_filtered: list[tuple[float, str | None, float | None, str]] = []
            for ping, exit_ip, udp_latency, key in results:
                if exit_ip and exit_ip in banned_ips:
                    continue
                results_filtered.append((ping, exit_ip, udp_latency, key))

            skipped = len(results) - len(results_filtered)
            if skipped:
                console.print(f"[yellow]🚫 Banned IPs filtered: {skipped}[/yellow]")
            results = results_filtered

            if not results:
                console.print("[red]❌ No proxies left after ban filter[/red]")
                return
        else:
            console.print("[cyan]🚫 Banned filter disabled[/cyan]")

        # 6. DDNET VALIDATION
        if use_ddnet_test:
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
        else:
            console.print(f"\n[yellow]⏭️ DDNet test skipped — saving top {top_n} by ping[/yellow]")
            confirmed = [key for _, _, _, key in results[:top_n]]
            save_proxies(confirmed)

        # 7. SPARE PROXIES
        if use_spare and use_ddnet_test:
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
