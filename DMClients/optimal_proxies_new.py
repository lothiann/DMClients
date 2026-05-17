import os
import sys
import requests
import base64
import json
import subprocess
import time
import re
import socket
import threading
import queue
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
import socks as pysocks
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn
from rich.console import Console

if getattr(sys, 'frozen', False):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    sys.stderr.reconfigure(encoding='utf-8')

# --- Настройки ---
XRAY_PATH = "xray.exe"
TEST_URL = "http://ifconfig.me/ip"
TOP_N = 14
START_PORT = 19000
TIMEOUT = 10
PORTS_FILE = "ports_proxies.py"
MAX_WORKERS = 100
KEY_FILTER = ["rbc.ru"]

GAME_BASE_TIMEOUT = 25
GAME_EXTEND_TIMEOUT = 10

SUB_URLS = [
    "https://raw.githubusercontent.com/RKPchannel/RKP_bypass_configs/refs/heads/main/configs/url_work.txt",
    "https://raw.githubusercontent.com/Ai123999/WhiteKeys/refs/heads/main/WhiteKeys",
    "https://gistpad.com/raw/miata-vpn-free-vless-keys-reverse-engineer-s-basement",
    "https://raw.githubusercontent.com/pyatovsergey0105-maker/-/refs/heads/main/Whie_spiksik",
    "https://github.com/KiryaScript/white-lists/raw/refs/heads/main/githubmirror/28.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/WHITE-SNI-RU-all.txt",
    "https://raw.githubusercontent.com/clowovx/clowovxVPN/refs/heads/main/clowovxVPN",
    "https://raw.githubusercontent.com/whoahaow/rjsxrd/refs/heads/main/githubmirror/bypass/bypass-all.txt",
    "https://raw.githubusercontent.com/Ai123999/WhiteKeys/refs/heads/main/WhiteKeys",
    "https://raw.githubusercontent.com/ebrasha/free-v2ray-public-list/refs/heads/main/V2Ray-Config-By-EbraSha-All-Type.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile.txt",

    "https://tinyurl.com/SemqkaVLESS",
    "https://raw.githubusercontent.com/cinev505/VlessTrogan-vpn-key/refs/heads/main/WhiteList-VPN-Vless",
    "https://raw.githubusercontent.com/Reallyza/ReallyzaVpn/refs/heads/main/ALL%20CONF-WH%2BWIFI",
    "https://github.com/Reallyza/ReallyzaVpn/blob/main/ALL%20CONF-WH%2BWIFI",
    "https://raw.githubusercontent.com/v0id9/vpn-configs/refs/heads/main/vpn.txt"
]

PROTOCOLS = ("vless://", "vmess://", "ss://", "trojan://", "hysteria://", "hysteria2://")

TARGET_SERVER = "45.141.57.22:8390"
PROXIFYRE_PATH = r"proxifyre/proxifyre.exe"
DDNET_PATH = r"ddnets-19.9-win64/hddnet1.exe"
GAME_TEST_PORT = 10801

bridge_clients = []
bridge_lock = threading.Lock()
dns_cache = {}
dns_lock = threading.Lock()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(SCRIPT_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

console = Console()

SPARE_COUNT = 5
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPARE_FILE = os.path.join(SCRIPT_DIR, "Settings", "spare_proxies.json")

def kill_all():
    for proc in ["xray.exe", "proxifyre.exe", "hddnet1.exe"]:
        os.system(f"taskkill /F /IM {proc} >nul 2>&1")
    time.sleep(0.5)

def key_preview(key: str) -> str:
    return key.split("#")[0][:60]

def key_identity(key: str) -> str:
    match = re.match(r"[^:]+://[^@]+@([^?#]+)", key)
    return match.group(1) if match else key

def resolve_host(host):
    with dns_lock:
        if host in dns_cache:
            return dns_cache[host]
    try:
        ip = socket.gethostbyname(host) if not re.match(r"^\d", host) else host
    except:
        ip = host
    with dns_lock:
        dns_cache[host] = ip
    return ip

def fetch_subscription(url: str) -> list[str]:
    if url.startswith(PROTOCOLS):
        return [url]
    print(f"📥 Fetching subscription...", end=" ")
    try:
        r = requests.get(url, timeout=10)
        content = r.text.strip()
        try:
            decoded = base64.b64decode(content + "==").decode("utf-8")
            raw_keys = [k.strip() for k in decoded.splitlines() if k.strip()]
            if any(k.startswith(PROTOCOLS) for k in raw_keys):
                keys = [k.replace('[', '').replace(']', '') for k in raw_keys]
                console.print(f"[green]Found {len(keys)} keys[/green]")
                return keys
        except:
            pass
        raw_keys = [k.strip() for k in content.splitlines() if k.strip()]
        keys = [k.replace('[', '').replace(']', '') for k in raw_keys]
        console.print(f"[green]Found {len(keys)} keys[/green]")
        return keys
    except:
        console.print("[red]Failed[/red]")
        return []

def parse_key(key: str) -> dict | None:
    try:
        from python_v2ray.config_parser import parse_uri
        parsed = parse_uri(key)
        server = getattr(parsed, 'address', None) or getattr(parsed, 'server', None) or getattr(parsed, 'host', None)
        if not server:
            return None
        if parsed.protocol in ['vless', 'vmess']:
            outbound = {
                "protocol": parsed.protocol,
                "settings": {"vnext": [{"address": server, "port": parsed.port,
                                        "users": [{"id": getattr(parsed, 'id', getattr(parsed, 'uuid', '')),
                                                   "encryption": getattr(parsed, 'encryption', 'none'),
                                                   "flow": getattr(parsed, 'flow', '')}]}]},
                "streamSettings": {"network": getattr(parsed, 'network', 'tcp'),
                                   "security": getattr(parsed, 'security', 'none')}
            }
            if getattr(parsed, 'security', '') == 'reality':
                outbound['streamSettings']['realitySettings'] = {
                    "serverName": getattr(parsed, 'sni', ''),
                    "fingerprint": 'chrome',
                    "publicKey": getattr(parsed, 'pbk', ''),
                    "shortId": getattr(parsed, 'sid', ''),
                    "spiderX": "/"
                }
            return outbound
        elif parsed.protocol in ['shadowsocks', 'ss']:
            return {
                "protocol": "shadowsocks",
                "settings": {"servers": [{"address": server, "port": parsed.port,
                                          "method": getattr(parsed, 'method', 'chacha20-ietf-poly1305'),
                                          "password": getattr(parsed, 'password', '')}]}
            }
        elif parsed.protocol == 'trojan':
            return {
                "protocol": "trojan",
                "settings": {"servers": [{"address": server, "port": parsed.port,
                                          "password": getattr(parsed, 'password', getattr(parsed, 'uuid', ''))}]},
                "streamSettings": {"security": "tls",
                                   "tlsSettings": {"serverName": getattr(parsed, 'sni', server),
                                                   "allowInsecure": True}}
            }
    except:
        return None

def test_proxy(key: str, port: int) -> float | None:
    cfg_file = os.path.join(TEMP_DIR, f"_test_{port}.json")
    outbound = parse_key(key)
    if not outbound:
        return None

    config = {
        "log": {"loglevel": "none"},
        "inbounds": [{"port": port, "listen": "127.0.0.1",
                      "protocol": "socks", "settings": {"auth": "noauth", "udp": True}}],
        "outbounds": [outbound]
    }

    for attempt in range(2):
        try:
            with open(cfg_file, "w") as f:
                json.dump(config, f)

            proc = subprocess.Popen([XRAY_PATH, "-c", cfg_file],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                time.sleep(1)
                proxies = {"http": f"socks5://127.0.0.1:{port}",
                           "https": f"socks5://127.0.0.1:{port}"}
                start = time.time()
                r = requests.get(TEST_URL, proxies=proxies, timeout=TIMEOUT)
                ping = (time.time() - start) * 1000
                if r.status_code not in (200, 204):
                    return None

                s = pysocks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
                s.set_proxy(pysocks.SOCKS5, "127.0.0.1", port)
                s.settimeout(TIMEOUT)
                s.sendto(b'\xaa\xbb\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x06google\x03com\x00\x00\x01\x00\x01',
                         ("8.8.8.8", 53))
                s.recv(512)
                s.close()

                return round(ping, 1)

            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                    socket.timeout) as e:
                if attempt == 0:
                    continue
                else:
                    return None
            except Exception:
                return None
            finally:
                proc.terminate()
                proc.wait()
                if os.path.exists(cfg_file):
                    os.remove(cfg_file)

        except Exception:
            return None

    return None

def run_visual_test(key: str, idx_info: str) -> bool:
    console.print(f"\n[cyan]🧪 {idx_info} Testing in DDNet: {key_preview(key)}[/cyan]")
    kill_all()
    outbound = parse_key(key)
    if not outbound:
        return False

    cfg_file = os.path.join(TEMP_DIR, "visual_test_config.json")
    with open(cfg_file, "w") as f:
        json.dump({"inbounds": [{"port": GAME_TEST_PORT, "protocol": "socks", "settings": {"udp": True}}],
                   "outbounds": [outbound]}, f)

    subprocess.Popen([XRAY_PATH, "-c", cfg_file], creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.Popen([PROXIFYRE_PATH], creationflags=subprocess.CREATE_NO_WINDOW)
    game_proc = subprocess.Popen([DDNET_PATH], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8',
                                 startupinfo=subprocess.STARTUPINFO(dwFlags=1, wShowWindow=0))

    q = queue.Queue()
    def read_stdout(out, q):
        try:
            for line in iter(out.readline, ''):
                q.put(line)
        except:
            pass
        finally:
            out.close()
    threading.Thread(target=read_stdout, args=(game_proc.stdout, q), daemon=True).start()

    timeout = GAME_BASE_TIMEOUT
    start_t = time.time()
    sent = False
    success = False

    try:
        while time.time() - start_t < timeout:
            with bridge_lock:
                if bridge_clients and not sent:
                    time.sleep(5)
                    try:
                        bridge_clients[0].sendall(f"player_name testbot; connect {TARGET_SERVER}\n".encode())
                        sent = True
                    except:
                        bridge_clients.clear()
            while not q.empty():
                line = q.get_nowait().strip()
                if line:
                    if "E datafile: failed to open file 'maps/" in line:
                        timeout += GAME_EXTEND_TIMEOUT
                        console.print(f"    [yellow]⏰ Map loading. [/yellow]")
                    if any(x in line.lower() for x in ["entering game", "map loaded", "welcome to"]):
                        console.print("    [green]✅ Connection confirmed![/green]")
                        success = True
                        return True
                    if any(x in line.lower() for x in ["vpn detected", "banned", "disconnected", "wrong password"]):
                        console.print("    [red]❌ Rejected by server.[/red]")
                        return False
            time.sleep(0.02)
    finally:
        kill_all()
        if os.path.exists(cfg_file):
            os.remove(cfg_file)
    if not success:
        console.print("    [red]❌ Timeout.[/red]")
    return success

def update_ports_proxies(best_keys: list[str]):
    proxies = [{"port": 10801 + i, "key": k} for i, k in enumerate(best_keys)]
    json_path = os.path.join(SCRIPT_DIR, "Settings", "proxies.json")
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(proxies, f, indent=2, ensure_ascii=False)
    console.print(f"\n[green]✅ {json_path} updated with {len(best_keys)} confirmed keys[/green]")

def save_spare_proxies(spare_keys: list[str]):
    """Сохранить запасные ключи (только ключи, без портов) в Settings/spare_proxies.json"""
    os.makedirs(os.path.dirname(SPARE_FILE), exist_ok=True)
    with open(SPARE_FILE, "w", encoding="utf-8") as f:
        json.dump(spare_keys, f, indent=2, ensure_ascii=False)
    console.print(f"[green]✅ {len(spare_keys)} spare proxies saved to {SPARE_FILE}[/green]")

def handle_bridge():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 5555))
    server.listen(10)
    while True:
        try:
            conn, _ = server.accept()
            with bridge_lock:
                bridge_clients.append(conn)
        except:
            break

def main():
    global TARGET_SERVER, use_spare, spare_count
    use_spare = False
    spare_count = SPARE_COUNT
    for arg in sys.argv:
        if arg.startswith("--target-server="):
            TARGET_SERVER = arg.split("=", 1)[1]
        elif arg.startswith("--spare-proxies"):
            use_spare = True
            if "=" in arg:
                try:
                    spare_count = int(arg.split("=", 1)[1])
                except:
                    pass

    console.print(f"[cyan]📡 Using target server: {TARGET_SERVER}[/cyan]")
    sys.stdout.flush()

    threading.Thread(target=handle_bridge, daemon=True).start()

    all_raw_keys = []
    for url in SUB_URLS:
        all_raw_keys += fetch_subscription(url)
    if not all_raw_keys:
        console.print("[red]❌ No keys fetched[/red]")
        return

    console.print(f"\n[cyan]📊 Total raw keys collected: {len(all_raw_keys)}[/cyan]")
    console.print("[cyan]🎯 Processing deduplication...[/cyan]")

    identity_seen = set()
    first_pass = []
    total = len(all_raw_keys)
    progress_identity = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Identity deduplication:[/] {task.percentage:>3.0f}%"),
        BarColumn(bar_width=None),
        TextColumn("[yellow]{task.completed}/{task.total}"),
        console=console
    )
    task_id = progress_identity.add_task("", total=total)
    with Live(progress_identity, console=console, refresh_per_second=10):
        for k in all_raw_keys:
            identity = key_identity(k)
            if identity not in identity_seen:
                identity_seen.add(identity)
                first_pass.append(k)
            progress_identity.update(task_id, advance=1)

    keys = [k for k in first_pass if not any(f in k for f in KEY_FILTER)]
    print("")
    console.print(f"[green]🔍 Testing {len(keys)} unique endpoints (after identity dedup & filter)...[/green]")

    results = []
    results_lock = threading.Lock()
    total_tests = len(keys)
    completed = 0
    test_progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Test proxy:[/] {task.percentage:>3.0f}%"),
        BarColumn(bar_width=None),
        TextColumn("[yellow]{task.completed}/{task.total}"),
        console=console
    )
    task_test = test_progress.add_task("", total=total_tests)

    with Live(test_progress, console=console, refresh_per_second=10):
        def test_one(args):
            nonlocal completed
            i, key = args
            res = test_proxy(key, START_PORT + i)
            with results_lock:
                completed += 1
                preview = key_preview(key)
                if "://" in preview:
                    proto, rest = preview.split("://", 1)
                    proto += "://"
                else:
                    proto, rest = "", preview
                if res is None:
                    console.print(f"  ❌ [{completed}/{total_tests}] {proto}[white]{rest}[/white]")
                else:
                    console.print(f"  ✅ [{completed}/{total_tests}] {res}ms {proto}[green]{rest}[/green]")
                    results.append((res, key))
                test_progress.update(task_test, advance=1)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            list(ex.map(test_one, enumerate(keys)))

    console.print(f"\n[green]✅ Working proxies: {len(results)}[/green]")

    if not results:
        console.print("[red]❌ No working proxies after test[/red]")
        input("\nPress any key to exit . . .")
        return

    best_by_ip = {}
    for ping, key in results:
        identity = key_identity(key)
        host = identity.split(':')[0]
        ip = resolve_host(host)
        if ip not in best_by_ip or ping < best_by_ip[ip][0]:
            best_by_ip[ip] = (ping, key)
    results = list(best_by_ip.values())
    console.print(f"[green]✅ After IP deduplication: {len(results)} unique IPs[/green]")

    results.sort(key=lambda x: x[0])
    confirmed = []
    spare_confirmed = []
    console.print(f"\n[cyan]🎮 Starting DDNet validation...[/cyan]")

    idx = 0
    for ping, key in results:
        if len(confirmed) >= TOP_N:
            break
        if run_visual_test(key, f"#{len(confirmed)+1}"):
            confirmed.append(key)
        idx += 1

    if not confirmed:
        console.print("[red]❌ No working proxies confirmed[/red]")
        return

    update_ports_proxies(confirmed)

    if use_spare:
        for ping, key in results[idx:]:
            if len(spare_confirmed) >= spare_count:
                break
            if run_visual_test(key, f"spare #{len(spare_confirmed)+1}"):
                spare_confirmed.append(key)
        if spare_confirmed:
            save_spare_proxies(spare_confirmed)
        else:
            console.print("[yellow]⚠️ No spare proxies found[/yellow]")

if __name__ == "__main__":
    main()