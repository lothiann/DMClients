import os
import sys
import flet as ft
import threading
import time
import asyncio
import subprocess
import psutil
import glob
import random
import socket
import string
import re
import concurrent.futures
import math
import json
import shutil
import ast
from datetime import datetime
from typing import Dict, List, Optional

_global_names = []
_global_dictionary = []

# ========== HELPER FUNCTIONS ==========
class DotDict:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

def kill_process_tree(pid: int):
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            child.kill()
        parent.kill()
    except psutil.NoSuchProcess:
        pass

def random_char() -> str:
    chars = string.ascii_letters + string.digits + "._-"
    return random.choice(chars)

def replace_placeholders(cmd: str, client_index: int) -> str:
    cmd = cmd.replace("{i}", str(client_index))
    while "{r}" in cmd:
        cmd = cmd.replace("{r}", random_char(), 1)
    def replace_ri(match):
        max_val = int(match.group(1))
        return str(random.randint(0, max_val))
    cmd = re.sub(r'{ri-(\d+)}', replace_ri, cmd)
    while "{n}" in cmd:
        if _global_names:
            cmd = cmd.replace("{n}", random.choice(_global_names), 1)
        else:
            cmd = cmd.replace("{n}", "lol", 1)
    while "{d}" in cmd:
        if _global_dictionary:
            cmd = cmd.replace("{d}", random.choice(_global_dictionary), 1)
        else:
            cmd = cmd.replace("{d}", "lol", 1)
    while "{c}" in cmd:
        ch = chr(random.randint(0x4E00, 0x9FFF))
        cmd = cmd.replace("{c}", ch, 1)
    return cmd

# ========== BRIDGE RECEIVER ==========
class BridgeReceiver:
    def __init__(self, host='127.0.0.1', port=5556):
        self.host = host
        self.port = port
        self.running = False
        self.server_socket: Optional[socket.socket] = None
        self.clients: Dict[int, socket.socket] = {}
        self.socket_to_idx: Dict[socket.socket, int] = {}
        self.client_ports: Dict[int, int] = {}  # client_idx -> порт клиента
        self.players: Dict[int, dict] = {}
        self.client_local_ids: Dict[int, int] = {}
        self.client_token: Dict[str, int] = {}
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.log_callback = None
        self.token_callback = None
        self.next_client_idx = 1

        self.server_name = ""
        self.server_map = ""
        self.server_gametype = ""
        self.server_num_players = 0
        self.server_max_players = 0

    def set_log_callback(self, callback):
        self.log_callback = callback

    def set_token_callback(self, callback):
        self.token_callback = callback

    def _log(self, msg: str):
        if self.log_callback:
            self.log_callback(f"[Bridge] {msg}")

    def start(self) -> bool:
        if self.running:
            return False
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(32)
            self.server_socket.settimeout(1.0)
            self.running = True
            self.thread = threading.Thread(target=self._accept_loop, daemon=True)
            self.thread.start()
            self._log(f"Server listening on {self.host}:{self.port}")
            return True
        except Exception as e:
            self._log(f"Failed to start server: {e}")
            return False

    def stop(self):
        self.running = False
        with self.lock:
            for sock in list(self.clients.values()):
                try: sock.close()
                except: pass
            self.clients.clear()
            self.socket_to_idx.clear()
            self.client_local_ids.clear()
            self.client_token.clear()
            self.client_ports.clear()
        if self.server_socket:
            try: self.server_socket.close()
            except: pass
            self.server_socket = None
        if self.thread:
            self.thread.join(timeout=2)
        self._log("Server stopped")

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_port = conn.getpeername()[1]
                with self.lock:
                    client_idx = self.next_client_idx
                    self.next_client_idx += 1
                    self.clients[client_idx] = conn
                    self.socket_to_idx[conn] = client_idx
                    self.client_ports[client_idx] = client_port
                self._log(f"Client #{client_idx} connected ({addr[0]}:{client_port})")
                threading.Thread(target=self._read_loop, args=(conn, client_idx), daemon=True).start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self._log(f"Accept error: {e}")
                break

    def _read_loop(self, client_socket: socket.socket, client_idx: int):
        buffer = ""
        while self.running:
            try:
                data = client_socket.recv(262144)
                if not data:
                    break
                buffer += data.decode('utf-8', errors='replace')
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if line:
                        self._parse_line(line, client_idx)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self._log(f"Read error (client {client_idx}): {e}")
                break
        with self.lock:
            if client_idx in self.clients:
                del self.clients[client_idx]
                for sock, idx in list(self.socket_to_idx.items()):
                    if idx == client_idx:
                        del self.socket_to_idx[sock]
                        break
            if client_idx in self.client_local_ids:
                del self.client_local_ids[client_idx]
            for token, idx in list(self.client_token.items()):
                if idx == client_idx:
                    del self.client_token[token]
                    break
            self.client_ports.pop(client_idx, None)
        try: client_socket.close()
        except: pass
        self._log(f"Client #{client_idx} disconnected")

    def _parse_line(self, line: str, client_idx: int):
        if line.startswith("TOKEN "):
            token = line[6:].strip()
            with self.lock:
                for t, idx in list(self.client_token.items()):
                    if idx == client_idx:
                        del self.client_token[t]
                        break
                self.client_token[token] = client_idx
            self._log(f"Client #{client_idx} token: {token[:8]}...")
            if self.token_callback:
                self.token_callback(token, client_idx)
                if self.app:
                    self.app.sync_clients_by_pid()
            return

        if line.startswith("SERVER "):
            parts = line.split('"')
            if len(parts) >= 7:
                self.server_name = parts[1]
                self.server_map = parts[3]
                self.server_gametype = parts[5]
                numbers = parts[6].strip().split()
                if len(numbers) >= 2:
                    self.server_num_players = int(numbers[0])
                    self.server_max_players = int(numbers[1])
            with self.lock:
                self.players.clear()
            return

        if not line.startswith("PLAYER "):
            return

        parts = line.split('"')
        if len(parts) < 3:
            return

        try:
            left_part = parts[0].strip().split()
            if len(left_part) < 2:
                return
            pid = int(left_part[1])
            name = parts[1]
            right_part = parts[2].strip().split()
            if len(right_part) < 8:
                return

            x = float(right_part[0])
            y = float(right_part[1])
            team = int(right_part[2])
            weapon = int(right_part[3])
            health = int(right_part[4])
            armor = int(right_part[5])
            frozen = int(right_part[6])
            is_local = int(right_part[7]) == 1

            direction = 0
            jumped = 0
            hook_state = 0
            angle = 0
            attack_tick = 0
            target_x = 0
            target_y = 0

            if len(right_part) >= 15:
                direction = int(right_part[8])
                jumped = int(right_part[9])
                hook_state = int(right_part[10])
                angle = int(right_part[11])
                attack_tick = int(right_part[12])
                target_x = int(right_part[13])
                target_y = int(right_part[14])

            with self.lock:
                self.players[pid] = {
                    'x': x, 'y': y, 'is_local': is_local, 'frozen': frozen,
                    'name': name, 'weapon': weapon, 'health': health, 'team': team,
                    'armor': armor, 'direction': direction, 'jumped': jumped,
                    'hook_state': hook_state, 'angle': angle, 'attack_tick': attack_tick,
                    'target_x': target_x, 'target_y': target_y,
                }
                if is_local:
                    self.client_local_ids[client_idx] = pid
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"[Bridge] Parse error: {e} on line: {line}")

    def get_local_id(self, client_idx: Optional[int] = None) -> Optional[int]:
        with self.lock:
            if client_idx is not None:
                return self.client_local_ids.get(client_idx)
            for pid, data in self.players.items():
                if data.get('is_local'):
                    return pid
        return None

    def get_player_pos(self, player_id: int) -> Optional[dict]:
        with self.lock:
            data = self.players.get(player_id)
            if data:
                return {'x': data['x'], 'y': data['y'], 'is_local': data['is_local'], 'frozen': data['frozen']}
            return None

    def get_player_state(self, player_id: int) -> Optional[dict]:
        with self.lock:
            data = self.players.get(player_id)
            if data:
                return data.copy()
            return None

    def get_all_players(self) -> Dict[int, dict]:
        with self.lock:
            return {pid: data.copy() for pid, data in self.players.items()}

    def get_server_info(self) -> dict:
        with self.lock:
            return {
                'name': self.server_name,
                'map': self.server_map,
                'gametype': self.server_gametype,
                'num_players': self.server_num_players,
                'max_players': self.server_max_players
            }

# ========== TCP CONTROL SERVER ==========
class ControlServer:
    def __init__(self, host='127.0.0.1', port=5555):
        self.host = host
        self.port = port
        self.socket = None
        self.clients: Dict[int, socket.socket] = {}
        self.client_ports: Dict[int, int] = {}  # cid -> порт клиента
        self.next_id = 1
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.log_callback = None
        self.token_callback = None
        self._reader_threads: Dict[int, threading.Thread] = {}

    def set_log_callback(self, callback):
        self.log_callback = callback

    def set_token_callback(self, callback):
        self.token_callback = callback

    def _log(self, msg: str):
        if self.log_callback:
            self.log_callback(f"[ControlServer] {msg}")

    def start(self) -> bool:
        if self.running:
            return False
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(32)
            self.socket.settimeout(1.0)
            self.running = True
            self.thread = threading.Thread(target=self._accept_loop, daemon=True)
            self.thread.start()
            self._log(f"Server started on {self.host}:{self.port}")
            return True
        except Exception as e:
            self._log(f"Failed to start server: {e}")
            return False

    def stop(self):
        self.running = False
        if self.socket:
            try: self.socket.close()
            except: pass
        with self.lock:
            for sock in self.clients.values():
                try: sock.close()
                except: pass
            self.clients.clear()
            self.client_ports.clear()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def _read_first_token(self, sock) -> Optional[str]:
        try:
            sock.settimeout(2.0)
            data = b""
            while b'\n' not in data:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                data += chunk
            line = data.decode('utf-8', errors='replace').split('\n')[0]
            if line.startswith("TOKEN "):
                return line[6:].strip()
        except Exception:
            pass
        finally:
            sock.settimeout(1.0)
        return None

    def _reader_loop(self, sock, cid):
        buffer = ""
        while self.running:
            try:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data.decode('utf-8', errors='replace')
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if line.startswith("TOKEN "):
                        token = line[6:].strip()
                        self._log(f"Client #{cid} new token: {token[:8]}...")
                        if self.token_callback:
                            self.token_callback(cid, token)
                            if self.app:
                                self.app.sync_clients_by_pid()
            except socket.timeout:
                continue
            except Exception:
                break
        with self.lock:
            if cid in self.clients:
                del self.clients[cid]
            self.client_ports.pop(cid, None)
            self._reader_threads.pop(cid, None)
        try: sock.close()
        except: pass
        self._log(f"Client #{cid} disconnected")

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.socket.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)

                client_port = conn.getpeername()[1]
                token = self._read_first_token(conn)

                with self.lock:
                    cid = self.next_id
                    self.next_id += 1
                    self.clients[cid] = conn
                    self.client_ports[cid] = client_port

                if token:
                    self._log(f"Client #{cid} connected ({addr[0]}:{client_port}) token={token[:8]}...")
                    if self.token_callback:
                        self.token_callback(cid, token)
                else:
                    self._log(f"Client #{cid} connected ({addr[0]}:{client_port}) — no token")

                conn.settimeout(1.0)
                reader_thread = threading.Thread(target=self._reader_loop, args=(conn, cid), daemon=True)
                reader_thread.start()
                self._reader_threads[cid] = reader_thread

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self._log(f"Accept error: {e}")
                break

    def send_command(self, client_ids: List[int], command: str) -> Dict[int, bool]:
        results = {}
        with self.lock:
            tasks = []
            for cid in client_ids:
                sock = self.clients.get(cid)
                if not sock:
                    results[cid] = False
                    continue
                final_cmd = replace_placeholders(command, cid)
                tasks.append((cid, sock, final_cmd))

        def send_to_one(cid, sock, data):
            try:
                sock.sendall((data + "\n").encode('utf-8'))
                return cid, True
            except Exception:
                try: sock.close()
                except: pass
                with self.lock:
                    if cid in self.clients:
                        del self.clients[cid]
                        self.client_ports.pop(cid, None)
                return cid, False

        if tasks:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 32)) as executor:
                futures = [executor.submit(send_to_one, cid, sock, cmd) for cid, sock, cmd in tasks]
                for future in concurrent.futures.as_completed(futures):
                    cid, ok = future.result()
                    results[cid] = ok
        return results

    def get_online_clients(self) -> List[int]:
        with self.lock:
            return list(self.clients.keys())

    def remove_client(self, client_id: int):
        with self.lock:
            if client_id in self.clients:
                try: self.clients[client_id].close()
                except: pass
                del self.clients[client_id]
                self.client_ports.pop(client_id, None)

    def check_alive(self, client_id: int) -> bool:
        with self.lock:
            sock = self.clients.get(client_id)
            if not sock:
                return False
            try:
                sock.sendall(b'')
                return True
            except:
                try: sock.close()
                except: pass
                del self.clients[client_id]
                self.client_ports.pop(client_id, None)
                return False

# ========== HDDNet CLIENT MANAGER ==========
class HDDNetClientManager:
    def __init__(self, log_callback):
        self.log_callback = log_callback
        self.processes: Dict[int, subprocess.Popen] = {}
        self.log_threads: Dict[int, threading.Thread] = {}
        self.log_flags: Dict[int, bool] = {}
        self.lock = threading.Lock()
        self.base_dir = os.path.join(os.path.dirname(__file__), "DDNets-19.9-win64")
        self.client_log: Dict[int, str] = {}

    def _get_exe_path(self, client_id: int) -> Optional[str]:
        exe_name = f"HDDNet{client_id}.exe"
        path = os.path.join(self.base_dir, exe_name)
        if os.path.exists(path):
            return path
        pattern = os.path.join(self.base_dir, "HDDNet*.exe")
        files = glob.glob(pattern)
        if files:
            self.log_callback(f"⚠️ {path} not found, using {files[0]}")
            return files[0]
        self.log_callback(f"❌ No HDDNet executable found in {self.base_dir}")
        return None

    def _log(self, msg: str):
        if self.log_callback:
            self.log_callback(msg)

    def launch(self, client_id: int, show_logs: bool = False) -> bool:
        exe_path = self._get_exe_path(client_id)
        if not exe_path:
            return False
        with self.lock:
            if client_id in self.processes and self.processes[client_id].poll() is None:
                self._log(f"Client #{client_id} already running")
                return False
        try:
            proc = subprocess.Popen(
                [exe_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            with self.lock:
                self.processes[client_id] = proc
                self.log_flags[client_id] = show_logs

            def reader():
                for line_bytes in iter(proc.stdout.readline, b''):
                    if line_bytes:
                        line = line_bytes.decode('utf-8', errors='replace').rstrip()
                        with self.lock:
                            self.client_log[client_id] = line
                            show = self.log_flags.get(client_id, False)
                        if show:
                            self._log(f"[Client #{client_id}] {line}")
                    else:
                        break
                with self.lock:
                    self.processes.pop(client_id, None)
                    self.log_threads.pop(client_id, None)
                    self.log_flags.pop(client_id, None)
                self._log(f"Client #{client_id} has terminated")

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            with self.lock:
                self.log_threads[client_id] = t
            self._log(f"✅ Client #{client_id} started (PID {proc.pid})")
            return True
        except Exception as e:
            self._log(f"❌ Failed to start client #{client_id}: {e}")
            return False

    def set_show_logs(self, client_id: int, show: bool):
        with self.lock:
            if client_id in self.log_flags:
                self.log_flags[client_id] = show

    def stop(self, client_id: int) -> bool:
        with self.lock:
            proc = self.processes.get(client_id)
            if not proc or proc.poll() is not None:
                return False
            kill_process_tree(proc.pid)
            self.processes.pop(client_id, None)
            self.log_threads.pop(client_id, None)
            self.log_flags.pop(client_id, None)
            self._log(f"🛑 Client #{client_id} stopped")
            return True

    def stop_all(self):
        for cid in list(self.processes.keys()):
            self.stop(cid)

    def is_running(self, client_id: int) -> bool:
        with self.lock:
            proc = self.processes.get(client_id)
            return proc is not None and proc.poll() is None

    def get_pid(self, client_id: int) -> Optional[int]:
        with self.lock:
            proc = self.processes.get(client_id)
            if proc and proc.poll() is None:
                return proc.pid
        return None

    def get_client_connection_info(self, client_id: int) -> Dict:
        pid = self.get_pid(client_id)
        if not pid:
            return {}
    
        result = {'pid': pid, 'control_port': None, 'bridge_port': None}
        try:
            proc = psutil.Process(pid)
            for conn in proc.net_connections():
                if conn.status == 'ESTABLISHED':
                    if conn.raddr.port == 5555:
                        result['control_port'] = conn.laddr.port
                    elif conn.raddr.port == 5556:
                        result['bridge_port'] = conn.laddr.port
        except psutil.NoSuchProcess:
            pass
        except Exception:
            pass
        return result

    def get_all_clients_connection_info(self) -> Dict[int, Dict]:
        result = {}
        with self.lock:
            client_ids = list(self.processes.keys())

        for client_id in client_ids:
            info = self.get_client_connection_info(client_id)
            if info and info['control_port'] and info['bridge_port']:
                result[client_id] = info

        return result

# ========== MAIN APPLICATION ==========
class DMClientsApp:
    class MacroManager:
        def __init__(self, app):
            self.app = app
            self._running = False
            self._active_clients: set = set()
            self._pending_timers: Dict[int, threading.Timer] = {}
            self._rule_threads: Dict[int, threading.Thread] = {}
            self._filepath = ""
            self._base_delay_ms = 0
            self._freeze_watcher_task = None
            self._last_loaded_file = None
            self._current_client_id = None

            self.file_field: ft.TextField = None
            self.delay_field: ft.TextField = None
            self.capture_id_field: ft.TextField = None
            self.save_as_field: ft.TextField = None
            self.record_btn: ft.FilledButton = None
            self.play_btn: ft.FilledButton = None
            self.save_btn: ft.FilledButton = None
            self.play_kill_cb: ft.Checkbox = None
            self.dont_kill_if_macros_cb: ft.Checkbox = None
            self.dont_block_if_macros_cb: ft.Checkbox = None
            self.code_editor = None
            self.editor_status = None

        @property
        def playing(self):
            return self._running

        @staticmethod
        def _calc_macro_duration(filepath: str) -> int:
            if not os.path.exists(filepath):
                return 0
            dur = 0
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith("sleep "):
                            try:
                                dur += int(line.split()[1])
                            except:
                                pass
            except Exception:
                pass
            return dur

        def _set_button_playing(self, playing: bool):
            if self.play_btn is None:
                return
            if playing:
                self.play_btn.content.value = "Stop"
                self.play_btn.icon = ft.Icons.STOP
            else:
                self.play_btn.content.value = "Play"
                self.play_btn.icon = ft.Icons.PLAY_ARROW
            self.play_btn.update()
            self._running = playing

        def _start_macro(self, cid: int):
            if not self._running:
                return
            if cid not in self.app.control_server.clients:
                self._active_clients.discard(cid)
                return

            self._active_clients.add(cid)

            if self.dont_block_if_macros_cb and self.dont_block_if_macros_cb.value:
                self.app.control_server.send_command([cid], "c_attack 0")
                self.app.control_server.send_command([cid], "c_input left 0")
                self.app.control_server.send_command([cid], "c_input right 0")
                self.app.control_server.send_command([cid], "c_input jump 0")
                self.app.control_server.send_command([cid], "c_input fire 0")
                self.app.control_server.send_command([cid], "c_input hook 0")
                self.app.control_server.send_command([cid], "+left; +right; +jump; +hook; +fire")

            ext = self._filepath.lower().rsplit('.', 1)[-1]

            if ext == 'inp':
                escaped_path = self._filepath.replace("\\", "\\\\")
                self.app.control_server.send_command([cid], f'c_macro_load "{escaped_path}"')
                self.app.control_server.send_command([cid], 'c_macro_play 1')
                duration = self._calc_macro_duration(self._filepath)
                if duration <= 0:
                    duration = 2000
                timer = threading.Timer((duration + 150) / 1000.0, lambda: self._on_macro_done(cid))
                timer.daemon = True
                timer.start()
                self._pending_timers[cid] = timer

            elif ext == 'rule':
                thread = threading.Thread(target=self._run_rule, args=(cid,), daemon=True)
                thread.start()
                self._rule_threads[cid] = thread

            self.app.add_log(f"▶️ Macro started for client #{cid}")

        def _stop_macros(self):
            self._running = False
            clients = list(self._active_clients)
            for cid in clients:
                self._cancel_client_macro(cid)
            self._active_clients.clear()
            self._pending_timers.clear()
            self._rule_threads.clear()
            self._set_button_playing(False)
            self.app.add_log("⏹️ Macros stopped")

        def _cancel_client_macro(self, cid: int):
            timer = self._pending_timers.pop(cid, None)
            if timer and timer.is_alive():
                timer.cancel()
            self._rule_threads.pop(cid, None)
            self.app.control_server.send_command([cid], "c_macro_play 0")
            if self.dont_block_if_macros_cb and self.dont_block_if_macros_cb.value:
                if self.app.attack_enable_switch.value:
                    self.app.control_server.send_command([cid], "c_attack 1")

        def _on_macro_done(self, cid: int):
            self.app._loop.call_soon_threadsafe(lambda: self._handle_client_macro_finished(cid))

        def _handle_client_macro_finished(self, cid: int):
            if cid not in self._active_clients:
                return
            self._pending_timers.pop(cid, None)
            self._rule_threads.pop(cid, None)
            self._active_clients.discard(cid)
            if self.dont_block_if_macros_cb and self.dont_block_if_macros_cb.value:
                if self.app.attack_enable_switch.value:
                    self.app.control_server.send_command([cid], "c_attack 1")
            self.app.add_log(f"⏹️ Macro finished for client #{cid}")
            if not self._active_clients:
                self._set_button_playing(False)
                self.app.add_log("🏁 All macros finished")

        def _run_rule(self, cid: int):
            try:
                self._current_client_id = cid
                max_duration = self._execute_rule_for_client(cid, self._filepath)
            except Exception as e:
                self.app.add_log(f"❌ Rule error on client #{cid}: {e}")
                max_duration = 0
            finally:
                self._current_client_id = None

            if not self._running:
                return
            if max_duration > 0:
                timer = threading.Timer((max_duration + 150) / 1000.0, lambda: self._on_macro_done(cid))
                timer.daemon = True
                timer.start()
                self._pending_timers[cid] = timer
            else:
                self._on_macro_done(cid)

        def _execute_rule_for_client(self, cid: int, rule_filepath: str) -> int:
            with open(rule_filepath, 'r', encoding='utf-8') as f:
                script_code = f.read()

            def get_state(pid=None):
                if pid is None:
                    pid = self._current_client_id
                bridge_cidx = self.app.control_to_bridge.get(pid)
                if bridge_cidx is None:
                    return None
                lid = self.app.bridge_receiver.get_local_id(bridge_cidx)
                if lid is None:
                    return None
                return self.app.bridge_receiver.get_player_state(lid)

            def weapon(pid=None):
                s = get_state(pid)
                return s.get('weapon', 0) if s else 0

            def health(pid=None):
                s = get_state(pid)
                return s.get('health', 0) if s else 0

            def armor(pid=None):
                s = get_state(pid)
                return s.get('armor', 0) if s else 0

            def frozen(pid=None):
                s = get_state(pid)
                return s.get('frozen', False) if s else False

            def team(pid=None):
                s = get_state(pid)
                return s.get('team', 0) if s else 0

            def ply_type(pid=None):
                if pid is None:
                    pid = self._current_client_id
                if pid in self.app.control_server.clients:
                    return 'player'
                return 'bot'

            def direction(pid=None):
                s = get_state(pid)
                return s.get('direction', 0) if s else 0

            def jumped(pid=None):
                s = get_state(pid)
                return s.get('jumped', 0) if s else 0

            def hook_state(pid=None):
                s = get_state(pid)
                return s.get('hook_state', 0) if s else 0

            def angle(pid=None):
                s = get_state(pid)
                return s.get('angle', 0) if s else 0

            def attack_tick(pid=None):
                s = get_state(pid)
                return s.get('attack_tick', 0) if s else 0

            def ply_name(pid=None):
                s = get_state(pid)
                return s.get('name', '') if s else ''

            def local_id(pid=None):
                if pid is None:
                    pid = self._current_client_id
                bridge_cidx = self.app.control_to_bridge.get(pid)
                if bridge_cidx is None:
                    return None
                return self.app.bridge_receiver.get_local_id(bridge_cidx)

            class _Pos:
                @staticmethod
                def x(pid=None):
                    s = get_state(pid)
                    return s.get('x', 0.0) if s else 0.0
                @staticmethod
                def y(pid=None):
                    s = get_state(pid)
                    return s.get('y', 0.0) if s else 0.0

            class _Aim:
                @staticmethod
                def x(pid=None):
                    s = get_state(pid)
                    return s.get('target_x', 0) if s else 0
                @staticmethod
                def y(pid=None):
                    s = get_state(pid)
                    return s.get('target_y', 0) if s else 0

            pos = _Pos()
            aim = _Aim()

            max_duration = [0]
            rule_dir = os.path.dirname(os.path.abspath(rule_filepath))

            def send(cmd):
                self.app.control_server.send_command([cid], cmd)

            def macros_play(filename):
                inp_path = os.path.join(rule_dir, filename)
                if not os.path.exists(inp_path):
                    self.app.add_log(f"❌ File not found: {inp_path}")
                    return
                escaped_path = inp_path.replace("\\", "\\\\")
                self.app.control_server.send_command([cid], f'c_macro_load "{escaped_path}"')
                self.app.control_server.send_command([cid], 'c_macro_play 1')
                dur = self._calc_macro_duration(inp_path)
                if dur > max_duration[0]:
                    max_duration[0] = dur

            def sleep_ms(ms):
                for _ in range(int(ms / 50)):
                    if not self._running:
                        break
                    time.sleep(0.05)
                time.sleep((ms % 50) / 1000.0)

            def log(msg):
                self.app.add_log(f"[Rule #{cid}] {msg}")

            class _Macros:
                @staticmethod
                def play(filename):
                    macros_play(filename)

            env = {
                'app': self.app,
                'pos': pos,
                'aim': aim,
                'weapon': weapon,
                'health': health,
                'armor': armor,
                'frozen': frozen,
                'team': team,
                'type': ply_type,
                'dir': direction,
                'jump': jumped,
                'hook': hook_state,
                'angle': angle,
                'attack': attack_tick,
                'name': ply_name,
                'local_id': local_id,
                'client_id': cid,
                'running': lambda: self._running,
                'send': send,
                'send_to': lambda target_cid, cmd: self.app.control_server.send_command([target_cid], cmd),
                'get_clients': lambda: self.app.control_server.get_online_clients(),
                'get_selected': lambda: self.app.get_selected_clients(),
                'get_log': lambda target_cid: self.app.client_manager.client_log.get(target_cid, ''),
                'log': log,
                'sleep': sleep_ms,
                'server_name': lambda: self.app.bridge_receiver.get_server_info().get('name', ''),
                'server_map': lambda: self.app.bridge_receiver.get_server_info().get('map', ''),
                'server_gametype': lambda: self.app.bridge_receiver.get_server_info().get('gametype', ''),
                'server_players': lambda: self.app.bridge_receiver.get_server_info().get('num_players', 0),
                'server_max_players': lambda: self.app.bridge_receiver.get_server_info().get('max_players', 0),
                'launch_client': lambda target_cid: self.app.client_manager.launch(target_cid, self.app.logs_checkboxes[target_cid-1].value if target_cid-1 < len(self.app.logs_checkboxes) else False),
                'stop_client': lambda target_cid: self.app.client_manager.stop(target_cid),
                'client_running': lambda target_cid: self.app.client_manager.is_running(target_cid),
                'threading': threading,
                'ft': ft,
                'macros': _Macros(),
            }

            try:
                exec(script_code, {}, env)
            except Exception as e:
                self.app.add_log(f"❌ Rule execution error on client #{cid}: {e}")

            return max_duration[0]

        def record(self, e):
            if not hasattr(self, 'macro_recording'):
                self.macro_recording = False
            if not self.macro_recording:
                capture_id = self.capture_id_field.value.strip()
                if capture_id:
                    self.app.send_action_command(f"c_macro_capture {capture_id}")
                self.app.send_action_command("c_macro_record 1")
                self.record_btn.content.value = "Stop"
                self.record_btn.icon = ft.Icons.STOP
                self.macro_recording = True
                self.app.add_log("🔴 Macro recording started")
            else:
                self.app.send_action_command("c_macro_record 0")
                self.record_btn.content.value = "Record"
                self.record_btn.icon = ft.Icons.RECORD_VOICE_OVER
                self.macro_recording = False
                self.app.add_log("⏹️ Macro recording stopped")
            self.record_btn.update()

        def save(self, e):
            script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Macros")
            os.makedirs(script_dir, exist_ok=True)
            save_as = self.save_as_field.value.strip()
            if save_as:
                filename = f"{save_as}.inp"
            elif self.file_field.value.strip() and os.path.exists(self.file_field.value.strip()):
                filename = os.path.basename(self.file_field.value.strip())
            else:
                filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".inp"
            filepath = os.path.join(script_dir, filename)
            escaped_path = filepath.replace("\\", "\\\\")
            self.app.send_action_command(f"c_macro_save \"{escaped_path}\"")
            self.app.add_log(f"💾 Macro saved: {filepath}")
            self.file_field.value = filepath
            self.file_field.update()

        def browse(self, e):
            import ctypes
            from ctypes import wintypes
            try: ctypes.windll.user32.SetProcessDPIAware()
            except: pass

            class OPENFILENAMEW(ctypes.Structure):
                _fields_ = [
                    ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
                    ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
                    ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
                    ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
                    ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                    ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                    ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
                    ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
                    ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
                    ("lpfnHook", wintypes.LPVOID), ("lpTemplateName", wintypes.LPCWSTR),
                    ("pvReserved", wintypes.LPVOID), ("dwReserved", wintypes.DWORD),
                    ("FlagsEx", wintypes.DWORD),
                ]

            OFN_FILEMUSTEXIST = 0x1000
            OFN_HIDEREADONLY = 0x4
            filter_str = "Macro & Rule files\0*.inp;*.rule\0All files\0*.*\0"
            title = "Select macro or rule file"
            file_buf = ctypes.create_unicode_buffer(260)
            file_buf.value = ""
            ofn = OPENFILENAMEW()
            ofn.lStructSize = ctypes.sizeof(ofn)
            ofn.lpstrInitialDir = os.path.join(os.path.dirname(__file__), "Macros")
            try:
                ofn.hwndOwner = self.app.page.window.hwnd
            except AttributeError:
                ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
            ofn.lpstrFilter = filter_str
            ofn.lpstrFile = ctypes.cast(file_buf, wintypes.LPWSTR)
            ofn.nMaxFile = 260
            ofn.lpstrTitle = title
            ofn.Flags = OFN_FILEMUSTEXIST | OFN_HIDEREADONLY
            if ofn.hwndOwner:
                ctypes.windll.user32.SetForegroundWindow(ofn.hwndOwner)
            if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
                filepath = file_buf.value
                self.file_field.value = filepath
                self.file_field.update()
                self.load_file_to_editor(filepath)

        def play(self, e):
            if self._running:
                self._stop_macros()
                return
            filepath = self.file_field.value.strip()
            delay_str = self.delay_field.value.strip()
            try:
                self._base_delay_ms = int(delay_str) if delay_str else 0
            except ValueError:
                self._base_delay_ms = 0
            selected = self.app.get_selected_clients()
            online = [cid for cid in selected if cid in self.app.control_server.clients]
            if not online:
                self.app.add_log("⚠️ No online clients selected")
                return
            if not filepath or not os.path.exists(filepath):
                self.app.add_log("❌ File not found")
                return
            ext = filepath.lower().rsplit('.', 1)[-1]
            if ext not in ('inp', 'rule'):
                self.app.add_log("❌ Unknown file type. Use .inp or .rule")
                return
            self._filepath = filepath
            self._active_clients.clear()
            self._pending_timers.clear()
            self._rule_threads.clear()
            self._set_button_playing(True)
            for cid in online:
                delay_ms = self._base_delay_ms * (cid - 1)
                if delay_ms == 0:
                    self._start_macro(cid)
                else:
                    timer = threading.Timer(delay_ms / 1000.0, self._start_macro, args=[cid])
                    timer.daemon = True
                    timer.start()
                    self._pending_timers[cid] = timer
            self.app.add_log(f"▶️ Macros playback started for {len(online)} clients")

        def on_play_kill_toggle(self, e):
            if self.play_kill_cb.value:
                if self._freeze_watcher_task is None or self._freeze_watcher_task.done():
                    self._freeze_watcher_task = self.app.page.run_task(self._freeze_watcher_loop)
                    self.app.add_log("🔄 Freeze watcher started")
            else:
                if self._freeze_watcher_task and not self._freeze_watcher_task.done():
                    self._freeze_watcher_task.cancel()
                    self._freeze_watcher_task = None
                    self.app.add_log("⏹️ Freeze watcher stopped")

        async def _freeze_watcher_loop(self):
            try:
                while self.play_kill_cb.value:
                    await asyncio.sleep(0.2)

                    try:
                        online_selected = [cid for cid in self.app.get_selected_clients() 
                                           if cid in self.app.control_server.clients]

                        for cid in online_selected:
                            bridge_cidx = self.app.control_to_bridge.get(cid)
                            if bridge_cidx is None:
                                continue

                            local_id = self.app.bridge_receiver.get_local_id(bridge_cidx)
                            if local_id is None:
                                continue

                            state = self.app.bridge_receiver.get_player_state(local_id)
                            if not state:
                                continue

                            if state.get('frozen'):
                                if self.dont_kill_if_macros_cb.value and cid in self._active_clients:
                                    continue

                                self.app.control_server.send_command([cid], "kill; say /kill")
                                self.app.add_log(f"❄️ Client #{cid} frozen – killed and restarting")

                                self._filepath = self.file_field.value.strip()
                                self._running = True 
                                self._set_button_playing(True) 

                                await asyncio.sleep(0.1)
                                self._cancel_client_macro(cid)
                                self._start_macro(cid)

                    except Exception as inner_ex:
                        self.app.add_log(f"⚠️ Freeze watcher inner error: {inner_ex}")

            except asyncio.CancelledError:
                pass

        def _on_code_change(self, e):
            if self.code_editor and self.code_editor.value:
                val = self.code_editor.value
                new_val = val.replace('\r\n', '\n').replace('\r', '\n')
                while '\n\n\n' in new_val:
                    new_val = new_val.replace('\n\n\n', '\n\n')
                if new_val != val:
                    self.code_editor.value = new_val
                    self.code_editor.update()

        def load_file_to_editor(self, filepath: str):
            try:
                if os.path.exists(filepath):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    content = content.replace('\r\n', '\n').replace('\r', '\n')
                    while '\n\n\n' in content:
                        content = content.replace('\n\n\n', '\n\n')
                    self.code_editor.value = content
                    self.editor_status.value = f"Loaded: {os.path.basename(filepath)} ({len(content)} chars)"
                    self._last_loaded_file = filepath
                else:
                    self.editor_status.value = "File not found"
            except Exception as e:
                self.editor_status.value = f"Error loading: {e}"
            self.editor_status.update()
            self.code_editor.update()

        def save_changes(self, e):
            filepath = self._last_loaded_file or self.file_field.value.strip()
            if not filepath:
                script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Macros")
                os.makedirs(script_dir, exist_ok=True)
                save_as = self.save_as_field.value.strip()
                if save_as:
                    if not save_as.endswith(('.inp', '.rule')):
                        filename = f"{save_as}.inp"
                    else:
                        filename = save_as
                else:
                    filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".inp"
                filepath = os.path.join(script_dir, filename)
            try:
                raw = self.code_editor.value
                content = raw.replace('\r\n', '\n').replace('\r', '\n')
                import re
                content = re.sub(r'\n{3,}', '\n\n', content)
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.file_field.value = filepath
                self.file_field.update()
                self._last_loaded_file = filepath
                self.editor_status.value = f"Saved: {os.path.basename(filepath)}"
                self.app.add_log(f"💾 Code saved: {filepath}")
            except Exception as e:
                self.editor_status.value = f"Save error: {e}"
            self.editor_status.update()

        def reload_file(self, e):
            filepath = self._last_loaded_file or self.file_field.value.strip()
            if filepath:
                self.load_file_to_editor(filepath)

        def build_ui(self) -> ft.Container:
            self.file_field = ft.TextField(label="Macro/Rule file", value="", width=300,
                                           bgcolor="#1e1e24", border_color="#33334d")
            self.delay_field = ft.TextField(label="Macro delay (ms)", value="0", width=100,
                                            bgcolor="#1e1e24", border_color="#33334d")
            self.capture_id_field = ft.TextField(label="Capture ID", value="", width=100,
                                                 bgcolor="#1e1e24", border_color="#33334d")
            self.save_as_field = ft.TextField(label="Save as", value="", width=200,
                                              bgcolor="#1e1e24", border_color="#33334d")
            self.record_btn = ft.FilledButton(content=ft.Text("Record"), icon=ft.Icons.RECORD_VOICE_OVER,
                                              on_click=self.record,
                                              style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
            self.play_btn = ft.FilledButton(content=ft.Text("Play"), icon=ft.Icons.PLAY_ARROW,
                                            on_click=self.play,
                                            style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
            self.save_btn = ft.FilledButton("Save", icon=ft.Icons.SAVE,
                                            on_click=self.save,
                                            style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
            self.play_kill_cb = ft.Checkbox(label="Play & Kill on freeze", value=False)
            self.dont_kill_if_macros_cb = ft.Checkbox(label="Don't kill if macros", value=False)
            self.dont_block_if_macros_cb = ft.Checkbox(label="Don't block if macros", value=False)
            self.play_kill_cb.on_change = self.on_play_kill_toggle

            self.code_editor = ft.TextField(
                multiline=True, min_lines=12, max_lines=20,
                text_style=ft.TextStyle(size=13, font_family="Consolas"),
                bgcolor="#121217", border_color="#33334d",
                focused_border_color="purpleaccent", cursor_color="#A855F7",
                selection_color="#A855F766",
                hint_text="// Select a macro/rule file to edit...", expand=True,
                on_change=self._on_code_change,
            )

            self.editor_status = ft.Text("", size=12, color="#888888")

            save_changes_btn = ft.FilledButton("Save Changes", icon=ft.Icons.SAVE_AS,
                                               on_click=self.save_changes,
                                               style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
            reload_btn = ft.FilledButton("Reload", icon=ft.Icons.REFRESH,
                                         on_click=self.reload_file,
                                         style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

            file_row = ft.Row([
                self.file_field,
                ft.FilledButton("Browse", icon=ft.Icons.FOLDER_OPEN,
                                on_click=self.browse,
                                style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white")),
            ], spacing=10)

            return ft.Container(
                content=ft.Column([
                    file_row,
                    ft.Row([self.delay_field, self.play_kill_cb, self.dont_kill_if_macros_cb, self.dont_block_if_macros_cb], spacing=15),
                    ft.Row([self.record_btn, self.play_btn, self.save_btn], spacing=15),
                    ft.Row([self.capture_id_field, self.save_as_field], spacing=10),
                    ft.Divider(height=1, color="#33334d"),
                    ft.Row([save_changes_btn, reload_btn, self.editor_status], spacing=10),
                    self.code_editor,
                ], spacing=10),
                padding=10, bgcolor="#1a1a24", border_radius=10
            )

    class CodeExecutor:
        def __init__(self, app):
            self.app = app
            self.file_field = None
            self.save_as_field = None
            self.save_btn = None
            self.reload_btn = None
            self.execute_btn = None
            self.code_editor = None
            self.editor_status = None
            self._last_loaded_file = None
            self._running = False

        def load_file_to_editor(self, filepath: str):
            try:
                if os.path.exists(filepath):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    self.code_editor.value = content
                    self._last_loaded_file = filepath
                    self.editor_status.value = f"Loaded: {os.path.basename(filepath)}"
                else:
                    self.editor_status.value = "File not found"
            except Exception as e:
                self.editor_status.value = f"Error: {e}"
            self.editor_status.update()
            self.code_editor.update()

        def browse(self, e):
            import ctypes
            from ctypes import wintypes
            try: ctypes.windll.user32.SetProcessDPIAware()
            except: pass

            class OPENFILENAMEW(ctypes.Structure):
                _fields_ = [
                    ("lStructSize", wintypes.DWORD),
                    ("hwndOwner", wintypes.HWND),
                    ("hInstance", wintypes.HINSTANCE),
                    ("lpstrFilter", wintypes.LPCWSTR),
                    ("lpstrCustomFilter", wintypes.LPWSTR),
                    ("nMaxCustFilter", wintypes.DWORD),
                    ("nFilterIndex", wintypes.DWORD),
                    ("lpstrFile", wintypes.LPWSTR),
                    ("nMaxFile", wintypes.DWORD),
                    ("lpstrFileTitle", wintypes.LPWSTR),
                    ("nMaxFileTitle", wintypes.DWORD),
                    ("lpstrInitialDir", wintypes.LPCWSTR),
                    ("lpstrTitle", wintypes.LPCWSTR),
                    ("Flags", wintypes.DWORD),
                    ("nFileOffset", wintypes.WORD),
                    ("nFileExtension", wintypes.WORD),
                    ("lpstrDefExt", wintypes.LPCWSTR),
                    ("lCustData", wintypes.LPARAM),
                    ("lpfnHook", wintypes.LPVOID),
                    ("lpTemplateName", wintypes.LPCWSTR),
                    ("pvReserved", wintypes.LPVOID),
                    ("dwReserved", wintypes.DWORD),
                    ("FlagsEx", wintypes.DWORD),
                ]

            OFN_FILEMUSTEXIST = 0x1000
            OFN_HIDEREADONLY = 0x4
            filter_str = "Python & Text files\0*.py;*.txt\0All files\0*.*\0"
            title = "Select Python or text file"
            file_buf = ctypes.create_unicode_buffer(260)
            file_buf.value = ""
            ofn = OPENFILENAMEW()
            ofn.lStructSize = ctypes.sizeof(ofn)
            ofn.lpstrInitialDir = os.path.join(os.path.dirname(__file__), "Scripts")
            try:
                ofn.hwndOwner = self.app.page.window.hwnd
            except AttributeError:
                ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
            ofn.lpstrFilter = filter_str
            ofn.lpstrFile = ctypes.cast(file_buf, wintypes.LPWSTR)
            ofn.nMaxFile = 260
            ofn.lpstrTitle = title
            ofn.Flags = OFN_FILEMUSTEXIST | OFN_HIDEREADONLY
            if ofn.hwndOwner:
                ctypes.windll.user32.SetForegroundWindow(ofn.hwndOwner)
            if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
                filepath = file_buf.value
                self.file_field.value = filepath
                self.file_field.update()
                self.load_file_to_editor(filepath)

        def save_changes(self, e):
            script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Scripts")
            os.makedirs(script_dir, exist_ok=True)

            save_as = self.save_as_field.value.strip()
            if save_as:
                if not save_as.endswith(('.py', '.txt')):
                    filename = f"{save_as}.py"
                else:
                    filename = save_as
            elif self.file_field.value.strip() and os.path.exists(self.file_field.value.strip()):
                filename = os.path.basename(self.file_field.value.strip())
            else:
                filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".py"

            filepath = os.path.join(script_dir, filename)

            try:
                raw = self.code_editor.value
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(raw)
                self.file_field.value = filepath
                self.file_field.update()
                self._last_loaded_file = filepath
                self.editor_status.value = f"Saved: {os.path.basename(filepath)}"
                self.app.add_log(f"💾 Code saved: {filepath}")
            except Exception as e:
                self.editor_status.value = f"Save error: {e}"
            self.editor_status.update()

        def reload_file(self, e):
            filepath = self._last_loaded_file or self.file_field.value.strip()
            if filepath:
                self.load_file_to_editor(filepath)

        def execute(self, e):
            if self._running:
                self._running = False
                self.execute_btn.content = ft.Text("Execute")
                self.execute_btn.icon = ft.Icons.PLAY_ARROW
                self.execute_btn.style = ft.ButtonStyle(bgcolor="#A855F7", color="white")
                self.execute_btn.update()
                self.editor_status.value = "Stopped"
                self.editor_status.update()
                return

            code = self.code_editor.value.strip()
            if not code:
                self.editor_status.value = "No code to execute"
                self.editor_status.update()
                return

            self._running = True
            self.execute_btn.content = ft.Text("Stop")
            self.execute_btn.icon = ft.Icons.STOP
            self.execute_btn.style = ft.ButtonStyle(bgcolor="#c62828", color="white")
            self.execute_btn.update()
            self.editor_status.value = "Executing..."
            self.editor_status.update()
            threading.Thread(target=self._run_code, args=(code,), daemon=True).start()

        def _on_code_change(self, e):
            if self.code_editor and self.code_editor.value:
                val = self.code_editor.value
                new_val = val.replace('\r\n', '\n').replace('\r', '\n')
                while '\n\n\n' in new_val:
                    new_val = new_val.replace('\n\n\n', '\n\n')
                if new_val != val:
                    self.code_editor.value = new_val
                    self.code_editor.update()

        def _run_code(self, code: str):
            try:
                app = self.app
                sys.path.append(os.path.dirname(os.path.abspath(__file__)))
                if self._last_loaded_file:
                    sys.path.append(os.path.dirname(os.path.abspath(self._last_loaded_file)))
                sys.path.append(os.path.expandvars(r'%localappdata%\Programs\Python\Python314\Lib\site-packages'))

                class _Pos:
                    @staticmethod
                    def x(pid):
                        s = app.bridge_receiver.get_player_state(pid)
                        return s.get('x', 0.0) if s else 0.0
                    @staticmethod
                    def y(pid):
                        s = app.bridge_receiver.get_player_state(pid)
                        return s.get('y', 0.0) if s else 0.0

                pos = _Pos()

                class _Aim:
                    @staticmethod
                    def x(pid):
                        s = app.bridge_receiver.get_player_state(pid)
                        return s.get('target_x', 0) if s else 0
                    @staticmethod
                    def y(pid):
                        s = app.bridge_receiver.get_player_state(pid)
                        return s.get('target_y', 0) if s else 0

                aim = _Aim()

                env = {
                    'app': app,
                    'pos': pos,
                    'weapon': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('weapon', 0),
                    'health': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('health', 0),
                    'frozen': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('frozen', False),
                    'type': lambda pid: 'bot' if pid in {app.bridge_receiver.get_local_id(cid) for cid in app.control_server.get_online_clients() if app.bridge_receiver.get_local_id(cid)} else 'player',
                    'dir': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('direction', 0),
                    'jump': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('jumped', 0),
                    'hook': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('hook_state', 0),
                    'angle': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('angle', 0),
                    'attack': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('attack_tick', 0),
                    'aim': aim,
                    'get_log': lambda cid: app.client_manager.client_log.get(cid, ''),
                    'local_id': lambda cid: app.bridge_receiver.get_local_id(app.control_to_bridge.get(cid)),
                    'name': lambda pid: (app.bridge_receiver.get_player_state(pid) or {}).get('name', ''),
                    'running': lambda: self._running,
                    'send': lambda cmd: app.send_action_command(cmd),
                    'send_to': lambda cid, cmd: app.control_server.send_command([cid], cmd),
                    'get_clients': lambda: app.control_server.get_online_clients(),
                    'get_selected': lambda: app.get_selected_clients(),
                    'log': lambda msg: app.add_log(f"[Code] {msg}"),
                    'sleep': lambda ms: time.sleep(ms / 1000),
                    'server_name': lambda: app.bridge_receiver.get_server_info().get('name', ''),
                    'server_map': lambda: app.bridge_receiver.get_server_info().get('map', ''),
                    'server_gametype': lambda: app.bridge_receiver.get_server_info().get('gametype', ''),
                    'server_players': lambda: app.bridge_receiver.get_server_info().get('num_players', 0),
                    'server_max_players': lambda: app.bridge_receiver.get_server_info().get('max_players', 0),
                    'launch_client': lambda cid: app.client_manager.launch(cid, app.logs_checkboxes[cid-1].value if cid-1 < len(app.logs_checkboxes) else False),
                    'stop_client': lambda cid: app.client_manager.stop(cid),
                    'client_running': lambda cid: app.client_manager.is_running(cid),
                    'threading': threading,
                    'ft': ft,
                }
                exec(code, env)
                self.app.add_log("✅ Code executed successfully")
                self.editor_status.value = "Done"
                self.editor_status.update()
            except Exception as ex:
                self.app.add_log(f"❌ Code execution error: {ex}")
                self.editor_status.value = f"Error: {ex}"
                self.editor_status.update()
            finally:
                self._running = False
                self.execute_btn.content = ft.Text("Execute")
                self.execute_btn.icon = ft.Icons.PLAY_ARROW
                self.execute_btn.style = ft.ButtonStyle(bgcolor="#A855F7", color="white")
                self.execute_btn.update()

        def build_ui(self) -> ft.Container:
            self.file_field = ft.TextField(label="Code file", value="", width=300,
                                           bgcolor="#1e1e24", border_color="#33334d")
            self.save_as_field = ft.TextField(label="Save as", value="", width=200,
                                              bgcolor="#1e1e24", border_color="#33334d")
            self.save_btn = ft.FilledButton("Save", icon=ft.Icons.SAVE,
                                            on_click=self.save_changes,
                                            style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
            self.reload_btn = ft.FilledButton("Reload", icon=ft.Icons.REFRESH,
                                              on_click=self.reload_file,
                                              style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
            self.execute_btn = ft.FilledButton("Execute", icon=ft.Icons.PLAY_ARROW,
                                               on_click=self.execute,
                                               style=ft.ButtonStyle(bgcolor="#A855F7", color="white"))
            self.editor_status = ft.Text("", size=12, color="#888888")
            self.code_editor = ft.TextField(
                multiline=True,
                min_lines=12,
                max_lines=20,
                text_style=ft.TextStyle(size=13, font_family="Consolas"),
                bgcolor="#121217",
                border_color="#33334d",
                focused_border_color="purpleaccent",
                cursor_color="#A855F7",
                selection_color="#A855F766",
                hint_text="// Write Python code here...",
                expand=True,
                on_change=self._on_code_change,
            )

            file_row = ft.Row([
                self.file_field,
                ft.FilledButton("Browse", icon=ft.Icons.FOLDER_OPEN,
                                on_click=self.browse,
                                style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white")),
            ], spacing=10)

            btn_row = ft.Row([self.save_btn, self.reload_btn, self.execute_btn, self.editor_status], spacing=10)

            return ft.Container(
                content=ft.Column([
                    file_row,
                    ft.Row([self.save_as_field], spacing=10),
                    btn_row,
                    self.code_editor,
                ], spacing=10),
                padding=10, bgcolor="#1a1a24", border_radius=10
            )

    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "DMClients UI"
        self.page.window_width = 1400
        self.page.window_height = 800
        self.page.theme_mode = "dark"
        self.page.bgcolor = "#121217"
        self.page.window.opacity = 0.9
        self.page.padding = 0
        self.page.spacing = 0

        try:
            p = psutil.Process(os.getpid())
            p.nice(psutil.REALTIME_PRIORITY_CLASS)
        except:
            pass

        self._loop = asyncio.get_event_loop()
        self._last_scroll_time = 0
        self._log_update_timer = 0
        self.MAX_LOG_LINES = 2000

        self.control_server = ControlServer()
        self.client_manager = HDDNetClientManager(self.add_log)
        self.control_server.set_log_callback(self.add_log)

        self.bridge_receiver = BridgeReceiver()
        self.bridge_receiver.set_log_callback(self.add_log)
    
        self.control_server.start()
        self.bridge_receiver.start()
    
        self.bridge_receiver.app = self
        self.control_server.app = self
        self.sync_clients_by_pid()

        self.control_to_bridge: Dict[int, int] = {}
        self._pending_control_tokens: Dict[str, int] = {}
        self._pending_bridge_tokens: Dict[str, int] = {}

        def on_control_token(cid, token):
            if token in self.bridge_receiver.client_token:
                bridge_cidx = self.bridge_receiver.client_token[token]
                self.control_to_bridge[cid] = bridge_cidx
                self.add_log(f"✅ Synced: Control #{cid} ↔ Bridge #{bridge_cidx}")
                self.sync_clients_by_pid()
            else:
                self._pending_control_tokens[token] = cid

        def on_bridge_token(token, bridge_cidx):
            if token in self._pending_control_tokens:
                cid = self._pending_control_tokens.pop(token)
                self.control_to_bridge[cid] = bridge_cidx
                self.add_log(f"✅ Synced: Control #{cid} ↔ Bridge #{bridge_cidx}")
                self.sync_clients_by_pid()
            else:
                self._pending_bridge_tokens[token] = bridge_cidx

        self.control_server.set_token_callback(on_control_token)
        self.bridge_receiver.set_token_callback(on_bridge_token)

        self.NUM_CLIENTS = 28
        self.clients_per_proxy = 2

        self.send_checkboxes: List[ft.Checkbox] = []
        self.logs_checkboxes: List[ft.Checkbox] = []
        self.connect_buttons: List[ft.Button] = []
        self.mem_texts: List[ft.Text] = []
        self.cpu_texts: List[ft.Text] = []

        self.players_table = None
        self.server_info_table = None
        self.server_info_cells = None
        self.servers_table = None
        self._community_mapping = {}
        self._sorted_server_rows = []
        self._sort_descending = True
        self._current_community_filter = None
        self._community_names = {}
        self._hop_running = False
        self._hop_task = None
        self._hop_selected_communities = []

        self.add_semicolons = True
        self.show_proxy_logs = True
        self.show_proxifyre_logs = False

        self.prev_attack_tick: Dict[int, int] = {}
        self._config_timer: Optional[threading.Timer] = None

        self.names: List[str] = []
        self.dictionary: List[str] = []
        self._load_dictionaries()

        self.spare_proxies: List[dict] = []
        self._load_spare_proxies()

        self.macro_mgr = self.MacroManager(self)
        self.code_executor = self.CodeExecutor(self)

        self.recording = False
        self.playing = False
        self.macro_lines = []
        self.macro_play_task = None
        self.kill_on_freeze_task = None
        self.random_aim_task = None

        self.optimal_proxies_proc = None
        self.ports_proxies_proc = None
        self.proxifyre_proc = None

        self._build_ui()
        self._start_monitoring()
        self.page.run_task(self.monitor_loop)
        self.page.run_task(self.players_tab_loop)

        psutil.Process(os.getpid()).nice(psutil.REALTIME_PRIORITY_CLASS)
        for c in psutil.Process(os.getpid()).children():
            if c.name().lower() == 'flet.exe':
                c.nice(psutil.REALTIME_PRIORITY_CLASS)

    def add_log(self, text: str):
        def _add():
            self.log_box.controls.append(ft.Text(text, size=14, selectable=True))
            if len(self.log_box.controls) > self.MAX_LOG_LINES:
                self.log_box.controls = self.log_box.controls[-self.MAX_LOG_LINES:]
            if not self.console_container.visible:
                return
            self.page.update()
            async def scroll():
                await asyncio.sleep(0.05)
                try:
                    await self.log_box.scroll_to(offset=-1, duration=0)
                except Exception:
                    pass
            asyncio.ensure_future(scroll(), loop=self._loop)
        self._loop.call_soon_threadsafe(_add)

    def _load_dictionaries(self):
        base = os.path.dirname(os.path.abspath(__file__))
        names_path = os.path.join(base, "Settings", "names.json")
        dict_path = os.path.join(base, "Settings", "dictionary.json")
        global _global_names, _global_dictionary

        try:
            with open(names_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.names = [str(x) for x in data]
                elif isinstance(data, dict) and 'words' in data:
                    self.names = [str(x) for x in data['words']]
        except Exception:
            self.names = []
        _global_names = self.names

        try:
            with open(dict_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.dictionary = [str(x) for x in data]
                elif isinstance(data, dict) and 'words' in data:
                    self.dictionary = [str(x) for x in data['words']]
        except Exception:
            self.dictionary = []
        _global_dictionary = self.dictionary

    def get_selected_clients(self) -> List[int]:
        return [i + 1 for i, cb in enumerate(self.send_checkboxes) if cb.value]

    def send_command_to_clients(self, command: str):
        selected = self.get_selected_clients()
    
        if not selected:
            self.add_log("⚠️ No clients selected")
            return
    
        real_cids = []
        for client_id in selected:
            cid = None
            for c, bridge_cidx in self.control_to_bridge.items():
                if bridge_cidx == client_id:
                    cid = c
                    break
        
            if cid is not None and cid in self.control_server.clients:
                real_cids.append(cid)
    
        if not real_cids:
            self.add_log("⚠️ No online clients selected")
            return
    
        if self.add_semicolons:
            command = f"; {command};"
    
        results = self.control_server.send_command(real_cids, command)
        success_count = sum(1 for ok in results.values() if ok)
        total = len(real_cids)
    
        if success_count == total:
            self.add_log(f"✅ Command sent to all {total} online clients: {command}")
        elif success_count > 0:
            self.add_log(f"⚠️ Command partially sent ({success_count}/{total} online): {command}")
        else:
            self.add_log(f"❌ Failed to send command to any online client: {command}")

    def send_action_command(self, command: str):
        self.send_command_to_clients(command)

    def sync_clients_by_pid(self):
        bridge_by_port = {}
        with self.bridge_receiver.lock:
            for cidx, port in self.bridge_receiver.client_ports.items():
                bridge_by_port[port] = cidx

        control_by_port = {}
        with self.control_server.lock:
            for cid, port in self.control_server.client_ports.items():
                control_by_port[port] = cid

        clients_info = self.client_manager.get_all_clients_connection_info()

        already_logged = getattr(self, '_synced_client_ids', set())

        synced = 0
        for client_id, info in clients_info.items():
            if client_id in already_logged:
                continue

            control_port = info.get('control_port')
            bridge_port  = info.get('bridge_port')

            cid         = control_by_port.get(control_port) if control_port else None
            bridge_cidx = bridge_by_port.get(bridge_port)   if bridge_port  else None

            if cid is None or bridge_cidx is None:
                continue

            self.control_to_bridge[cid] = bridge_cidx
            already_logged.add(client_id)
            self._synced_client_ids = already_logged
            synced += 1
            self.add_log(f"🔗 Synced: Client #{client_id} → Control #{cid} ↔ Bridge #{bridge_cidx}")

        return synced

    def _start_monitoring(self):
        def monitor_loop():
            last_update = 0
            while True:
                time.sleep(2)
                now = time.time()
                need_update = False
                for i in range(1, self.NUM_CLIENTS + 1):
                    running = self.client_manager.is_running(i)
                    try:
                        btn = self.connect_buttons[i - 1]
                    except IndexError:
                        continue
                    current_text = btn.content.value if btn.content else "Connect"
                    if running and current_text != "Disconnect":
                        btn.content = ft.Text("Disconnect")
                        btn.icon = ft.Icons.STOP
                        need_update = True
                    elif not running and current_text != "Connect":
                        btn.content = ft.Text("Connect")
                        btn.icon = ft.Icons.PLAY_ARROW
                        need_update = True
                for cid in self.control_server.get_online_clients():
                    if not self.control_server.check_alive(cid):
                        self.add_log(f"Client #{cid} disconnected from control server")
                        need_update = True
                if need_update and now - last_update > 0.5:
                    last_update = now
                    self._loop.call_soon_threadsafe(self.page.update)
        threading.Thread(target=monitor_loop, daemon=True).start()

    async def monitor_loop(self):
        cpu_count = psutil.cpu_count(logical=True)
        warned_uss = False
        prev_cpu = {}
        prev_time = {}
        while True:
            try:
                current_load = await asyncio.to_thread(psutil.cpu_percent, interval=0.1)
                interval = 5 if current_load > 70 else 3
            except:
                interval = 3
            await asyncio.sleep(interval)

            if not self.clients_container.visible:
                continue

            tasks = []
            for i in range(1, self.NUM_CLIENTS + 1):
                pid = self.client_manager.get_pid(i)
                if pid:
                    tasks.append(self._get_process_stats(i, pid, warned_uss, prev_cpu, prev_time, cpu_count))
                else:
                    self.mem_texts[i - 1].value = "0 MB"
                    self.cpu_texts[i - 1].value = "0%"
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.update_clients_stats()
            self.page.update()

    async def _get_process_stats(self, idx, pid, warned_uss, prev_cpu, prev_time, cpu_count):
        try:
            proc = await asyncio.to_thread(psutil.Process, pid)
            try:
                mem_info = await asyncio.to_thread(proc.memory_full_info)
                mem_mb = mem_info.uss / (1024 * 1024)
            except Exception:
                if not warned_uss:
                    self.add_log("⚠️ USS requires admin rights, using RSS.")
                    warned_uss = True
                mem_info = await asyncio.to_thread(proc.memory_info)
                mem_mb = mem_info.rss / (1024 * 1024)
            cpu_times = await asyncio.to_thread(proc.cpu_times)
            now = time.perf_counter()
            if pid in prev_cpu:
                user_diff = cpu_times.user - prev_cpu[pid].user
                system_diff = cpu_times.system - prev_cpu[pid].system
                time_diff = now - prev_time[pid]
                cpu_percent = (user_diff + system_diff) / time_diff * 100 / cpu_count if time_diff > 0 else 0.0
            else:
                cpu_percent = 0.0
            prev_cpu[pid] = cpu_times
            prev_time[pid] = now
            self.mem_texts[idx - 1].value = f"{mem_mb:.1f} MB"
            self.cpu_texts[idx - 1].value = f"{cpu_percent:.1f}%"
        except Exception:
            self.mem_texts[idx - 1].value = "0 MB"
            self.cpu_texts[idx - 1].value = "0%"

    def update_clients_stats(self):
        if not hasattr(self, 'clients_table_header') or self.clients_table_header is None:
            return
        total_mem = 0
        total_cpu = 0
        running_count = 0
        for i in range(1, self.NUM_CLIENTS + 1):
            if self.client_manager.is_running(i):
                running_count += 1
                try:
                    mem_str = self.mem_texts[i - 1].value.replace(" MB", "")
                    total_mem += float(mem_str) if mem_str != "0 MB" else 0
                except:
                    pass
                try:
                    cpu_str = self.cpu_texts[i - 1].value.replace("%", "")
                    total_cpu += float(cpu_str) if cpu_str != "0%" else 0
                except:
                    pass
        stats_text = f"[{running_count}/{self.NUM_CLIENTS}] | MEM total: {total_mem:.1f} MB | CPU total: {total_cpu:.1f}%"
        self.clients_table_header.controls[1].value = stats_text
        self.clients_table_header.update()

    def _apply_client_count(self, e):
        try:
            new_count = int(self.num_clients_field.value.strip())
            if new_count < 1 or new_count > 128:
                self.add_log("⚠️ Client count must be between 1 and 128")
                return
        except ValueError:
            self.add_log("⚠️ Invalid number")
            return

        try:
            cpp = int(self.clients_per_proxy_field.value.strip())
            if cpp < 1:
                raise ValueError
            self.clients_per_proxy = cpp
        except ValueError:
            self.add_log("⚠️ Clients per proxy must be a positive integer")
            return

        if new_count == self.NUM_CLIENTS:
            self._generate_proxifyre_config(new_count, cpp)
            self.add_log(f"🔄 Config regenerated with {cpp} clients per proxy (count unchanged)")
            return

        self.client_manager.stop_all()
        time.sleep(0.5)

        base_dir = os.path.join(os.path.dirname(__file__), "DDNets-19.9-win64")
        template = os.path.join(base_dir, "HDDNet1.exe")
        if not os.path.isfile(template):
            self.add_log("❌ HDDNet1.exe not found")
            return

        for f in glob.glob(os.path.join(base_dir, "HDDNet*.exe")):
            if os.path.basename(f) != "HDDNet1.exe":
                os.remove(f)

        for i in range(2, new_count + 1):
            dest = os.path.join(base_dir, f"HDDNet{i}.exe")
            shutil.copy(template, dest)

        top_n = (new_count + cpp - 1) // cpp
        script_path = os.path.join(os.path.dirname(__file__), "optimal_proxies_new.py")
        if os.path.isfile(script_path):
            with open(script_path, 'r', encoding='utf-8') as f:
                content = f.read()
            content = re.sub(r"TOP_N = \d+", f"TOP_N = {top_n}", content)
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(content)
            self.add_log(f"🔧 TOP_N set to {top_n} (based on {new_count} clients, {cpp} per proxy)")

        self._generate_proxifyre_config(new_count, cpp)
        self.NUM_CLIENTS = new_count
        self.clients_per_proxy = cpp
        self._rebuild_clients_table()
        self.add_log(f"🔄 Applied: {new_count} clients, {cpp} clients per proxy")

    def _generate_proxifyre_config(self, num_clients: int, clients_per_proxy: int = None):
        if clients_per_proxy is None:
            clients_per_proxy = self.clients_per_proxy
        proxy_count = (num_clients + clients_per_proxy - 1) // clients_per_proxy
        proxies = []
        for proxy_idx in range(1, proxy_count + 1):
            start_id = (proxy_idx - 1) * clients_per_proxy + 1
            end_id = min(proxy_idx * clients_per_proxy, num_clients)
            app_names = []
            for cid in range(start_id, end_id + 1):
                app_names.append(f"ddnet{cid}.exe")
                app_names.append(f"hddnet{cid}.exe")
            port = 10800 + proxy_idx
            proxies.append({
                "appNames": app_names,
                "socks5ProxyEndpoint": f"127.0.0.1:{port}",
                "supportedProtocols": ["TCP", "UDP"]
            })
        config = {"logLevel": "Error", "proxies": proxies}
        config_path = os.path.join(os.path.dirname(__file__), "ProxiFyre", "app-config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        self.add_log(f"📄 app-config.json regenerated: {proxy_count} proxies, {clients_per_proxy} clients each")

    def _load_spare_proxies(self):
        spare_path = os.path.join(os.path.dirname(__file__), "Settings", "spare_proxies.json")
        if os.path.exists(spare_path):
            try:
                with open(spare_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.spare_proxies = [str(item) for item in data]
                else:
                    self.spare_proxies = []
            except Exception:
                self.spare_proxies = []
        else:
            self.spare_proxies = []

    def _save_spare_proxies(self):
        spare_path = os.path.join(os.path.dirname(__file__), "Settings", "spare_proxies.json")
        os.makedirs(os.path.dirname(spare_path), exist_ok=True)
        with open(spare_path, 'w', encoding='utf-8') as f:
            json.dump(self.spare_proxies, f, indent=2)

    def _replace_proxy(self, port: int):
        if not self.spare_proxies:
            self.add_log("⚠️ No spare proxies available")
            return
        new_key = self.spare_proxies.pop(0)
        self._save_spare_proxies()
        json_path = os.path.join(os.path.dirname(sys.executable), "Settings", "proxies.json")
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for item in data:
                if item["port"] == port:
                    item["key"] = new_key
                    break
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.add_log(f"✅ Key for port {port} replaced")
            if hasattr(self, 'spare_label'):
                self.spare_label.value = f"Spare proxies: {len(self.spare_proxies)}"
                self.spare_label.update()
            self._refresh_proxies_table()
        except Exception as e:
            self.add_log(f"❌ Error: {e}")

    def _build_ui(self):
        self.page.input_field = ft.TextField(
            hint_text="Command...",
            expand=True,
            bgcolor="#1e1e24",
            border_color="#33334d",
            focused_border_color="purpleaccent",
            autofocus=True,
            shift_enter=True,
        )
        send_btn = ft.IconButton(ft.Icons.SEND, icon_color="purpleaccent",
                                  on_click=lambda e: self.on_send_click())
        self.page.input_field.on_submit = lambda e: self.on_send_click()

        self.log_box = ft.ListView(expand=True, spacing=5, auto_scroll=False)

        self.console_container = ft.Container(
            content=ft.Column([
                ft.Container(content=self.log_box, expand=True, padding=20),
                ft.Container(
                    content=ft.Row([self.page.input_field, send_btn], spacing=10),
                    padding=ft.Padding.only(left=20, right=20, bottom=20)
                )
            ], expand=True, spacing=0),
            expand=True, visible=True
        )

        self.clients_table = self._build_clients_table()
        self.clients_table_header = ft.Row([
            ft.Text("Clients Management", size=20, weight="bold", color="#A855F7"),
            ft.Text("", size=14, color="#888888")
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        clients_view = ft.Container(
            content=ft.Column([
                self.clients_table_header,
                ft.Container(content=self.clients_table, expand=True, padding=10),
            ], expand=True, scroll=ft.ScrollMode.AUTO),
            padding=20, expand=True,
        )
        self.clients_container = ft.Container(content=clients_view, expand=True, visible=False)

        self._build_tab_ui()
        self._build_actions_ui()
        self._build_servers_ui()
        self._build_settings_ui()

        self.nav_rail = ft.NavigationRail(
            selected_index=0,
            label_type="all",
            min_width=80,
            bgcolor="#18181f",
            indicator_color="purpleaccent",
            indicator_shape=ft.RoundedRectangleBorder(radius=15),
            destinations=[
                ft.NavigationRailDestination(icon=ft.Icons.TERMINAL, label="Console"),
                ft.NavigationRailDestination(icon=ft.Icons.GROUP, label="Clients"),
                ft.NavigationRailDestination(icon=ft.Icons.GESTURE, label="Actions"),
                ft.NavigationRailDestination(icon=ft.Icons.TABLE_CHART, label="Tab"),
                ft.NavigationRailDestination(icon=ft.Icons.DNS, label="Servers"),
                ft.NavigationRailDestination(icon=ft.Icons.SETTINGS, label="Settings"),
            ],
            on_change=self.on_nav_change,
        )

        main_content = ft.Stack(controls=[
            self.console_container, self.clients_container, self.tab_container,
            self.actions_container, self.servers_container, self.settings_container
        ], expand=True)

        self.page.add(
            ft.Row([
                self.nav_rail,
                ft.VerticalDivider(width=1, color="#252533"),
                main_content,
            ], expand=True, spacing=0)
        )

    def _build_clients_table(self) -> ft.DataTable:
        columns = [
            ft.DataColumn(ft.Text("Client", weight="bold")),
            ft.DataColumn(ft.Text("MEM (MB)", weight="bold")),
            ft.DataColumn(ft.Text("CPU (%)", weight="bold")),
            ft.DataColumn(ft.Text("Send commands", weight="bold")),
            ft.DataColumn(ft.Text("Show logs", weight="bold")),
            ft.DataColumn(ft.Text("Action", weight="bold")),
        ]
        rows = []
        self.send_checkboxes.clear()
        self.logs_checkboxes.clear()
        self.connect_buttons.clear()
        self.mem_texts.clear()
        self.cpu_texts.clear()

        for i in range(1, self.NUM_CLIENTS + 1):
            send_cb = ft.Checkbox(value=True)
            logs_cb = ft.Checkbox(value=False)
            def on_logs_change(e, cid=i):
                self.client_manager.set_show_logs(cid, e.control.value)
            logs_cb.on_change = on_logs_change
            self.send_checkboxes.append(send_cb)
            self.logs_checkboxes.append(logs_cb)

            mem_text = ft.Text("0 MB")
            cpu_text = ft.Text("0%")
            self.mem_texts.append(mem_text)
            self.cpu_texts.append(cpu_text)

            def make_handler(cid):
                def handler(e):
                    self.toggle_client(cid, e.control)
                return handler

            connect_btn = ft.Button(
                content=ft.Text("Connect"),
                icon=ft.Icons.PLAY_ARROW,
                width=200,
                bgcolor="#2a2a3a",
                color="white",
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                on_click=make_handler(i)
            )
            self.connect_buttons.append(connect_btn)

            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(f"Client #{i}", weight="bold")),
                ft.DataCell(mem_text),
                ft.DataCell(cpu_text),
                ft.DataCell(send_cb),
                ft.DataCell(logs_cb),
                ft.DataCell(connect_btn),
            ]))

        return ft.DataTable(
            columns=columns,
            rows=rows,
            heading_row_color="#1e1e24",
            divider_thickness=1,
            border=ft.Border.all(1, "#2a2a3a"),
            border_radius=10,
            horizontal_lines=ft.BorderSide(1, "#2a2a3a"),
            vertical_lines=ft.BorderSide(1, "#2a2a3a"),
            column_spacing=20,
            width=float("inf"),
        )

    def _build_tab_ui(self):
        server_info_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Name", weight="bold")),
                ft.DataColumn(ft.Text("Map", weight="bold")),
                ft.DataColumn(ft.Text("Type", weight="bold")),
                ft.DataColumn(ft.Text("Players", weight="bold")),
                ft.DataColumn(ft.Text("Max players", weight="bold")),
            ],
            rows=[
                ft.DataRow(cells=[
                    ft.DataCell(ft.Text("")),
                    ft.DataCell(ft.Text("")),
                    ft.DataCell(ft.Text("")),
                    ft.DataCell(ft.Text("")),
                    ft.DataCell(ft.Text("")),
                ])
            ],
            heading_row_color="#1e1e24",
            divider_thickness=1,
            border=ft.Border.all(1, "#2a2a3a"),
            border_radius=10,
            horizontal_lines=ft.BorderSide(1, "#2a2a3a"),
            vertical_lines=ft.BorderSide(1, "#2a2a3a"),
            column_spacing=20,
            width=float("inf"),
        )
        self.server_info_table = server_info_table
        self.server_info_cells = server_info_table.rows[0].cells

        players_columns = [
            ft.DataColumn(ft.Text("Player", weight="bold")),
            ft.DataColumn(ft.Text("ID", weight="bold")),
            ft.DataColumn(ft.Text("Pos X", weight="bold")),
            ft.DataColumn(ft.Text("Pos Y", weight="bold")),
            ft.DataColumn(ft.Text("Weapon", weight="bold")),
            ft.DataColumn(ft.Text("Health", weight="bold")),
            ft.DataColumn(ft.Text("Frozen", weight="bold")),
            ft.DataColumn(ft.Text("Type", weight="bold")),
            ft.DataColumn(ft.Text("Dir", weight="bold")),
            ft.DataColumn(ft.Text("Jump", weight="bold")),
            ft.DataColumn(ft.Text("Hook", weight="bold")),
            ft.DataColumn(ft.Text("Angle", weight="bold")),
            ft.DataColumn(ft.Text("Attack", weight="bold")),
            ft.DataColumn(ft.Text("Aim", weight="bold")),
        ]
        self.players_table = ft.DataTable(
            columns=players_columns,
            rows=[],
            heading_row_color="#1e1e24",
            divider_thickness=1,
            border=ft.Border.all(1, "#2a2a3a"),
            border_radius=10,
            horizontal_lines=ft.BorderSide(1, "#2a2a3a"),
            vertical_lines=ft.BorderSide(1, "#2a2a3a"),
            column_spacing=20,
            width=float("inf"),
        )

        tab_view = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Server Info", size=20, weight="bold", color="#A855F7"),
                    ft.Container(content=server_info_table, padding=10),
                    ft.Text("Players Info", size=20, weight="bold", color="#A855F7"),
                    ft.Container(content=self.players_table, expand=True, padding=10),
                ],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            alignment=ft.Alignment(-1, -1),
            padding=20,
            expand=True,
        )
        self.tab_container = ft.Container(content=tab_view, expand=True, visible=False)

    def _build_actions_ui(self):
        player_name_field = ft.TextField(label="Player name", hint_text="Enter name", expand=True,
                                         bgcolor="#1e1e24", border_color="#33334d")
        player_name_field.on_submit = self.on_player_name_submit
        player_name_send = ft.FilledButton("Set name", icon=ft.Icons.PERSON,
                                           on_click=self.on_player_name_submit,
                                           style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        player_skin_field = ft.TextField(label="Player skin", hint_text="skin ID", expand=True,
                                         bgcolor="#1e1e24", border_color="#33334d")
        player_skin_field.on_submit = self.on_player_skin_submit
        player_skin_send = ft.FilledButton("Set skin", icon=ft.Icons.FACE,
                                           on_click=self.on_player_skin_submit,
                                           style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        dummy_name_field = ft.TextField(label="Dummy name", hint_text="Enter dummy name", expand=True,
                                        bgcolor="#1e1e24", border_color="#33334d")
        dummy_name_field.on_submit = self.on_dummy_name_submit
        dummy_name_send = ft.FilledButton("Set dummy name", icon=ft.Icons.SMART_TOY,
                                          on_click=self.on_dummy_name_submit,
                                          style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        dummy_skin_field = ft.TextField(label="Dummy skin", hint_text="skin ID", expand=True,
                                        bgcolor="#1e1e24", border_color="#33334d")
        dummy_skin_field.on_submit = self.on_dummy_skin_submit
        dummy_skin_send = ft.FilledButton("Set skin", icon=ft.Icons.FACE,
                                          on_click=self.on_dummy_skin_submit,
                                          style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        left_container = ft.Container(
            content=ft.Column([
                ft.Row([player_name_field, player_name_send], spacing=10),
                ft.Row([player_skin_field, player_skin_send], spacing=10),
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10, expand=True
        )
        right_container = ft.Container(
            content=ft.Column([
                ft.Row([dummy_name_field, dummy_name_send], spacing=10),
                ft.Row([dummy_skin_field, dummy_skin_send], spacing=10),
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10, expand=True
        )

        connect_server_field = ft.TextField(label="Connect to", hint_text="server:port", expand=True,
                                            bgcolor="#1e1e24", border_color="#33334d")
        self.connect_button = ft.FilledButton(content=ft.Text("Connect"), icon=ft.Icons.LINK,
                                              on_click=self.on_connect_click,
                                              style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
        self.dummy_button = ft.FilledButton(content=ft.Text("Connect dummy"), icon=ft.Icons.SMART_TOY,
                                            on_click=self.on_dummy_connect_click,
                                            style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        self.callvote_input = ft.TextField(label="Player name or ID", hint_text="Enter name or ID", expand=True,
                                           bgcolor="#1e1e24", border_color="#33334d")
        self.callvote_mode_cb = ft.Checkbox(label="Name", value=True)
        def on_mode_change(e):
            if self.callvote_mode_cb.value:
                self.callvote_mode_cb.label = "Name"
                self.callvote_input.hint_text = "Enter player name"
            else:
                self.callvote_mode_cb.label = "ID"
                self.callvote_input.hint_text = "Enter player ID"
            self.callvote_mode_cb.update()
            self.callvote_input.update()
        self.callvote_mode_cb.on_change = on_mode_change
        callvote_btn = ft.FilledButton("Callvote", icon=ft.Icons.GAVEL, on_click=self.on_callvote_click,
                                       style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
        vote_yes_btn = ft.FilledButton("Vote YES (F3)", icon=ft.Icons.CHECK_CIRCLE, on_click=self.on_vote_yes,
                                       style=ft.ButtonStyle(bgcolor="#2e7d32", color="white"))
        vote_no_btn = ft.FilledButton("Vote NO (F4)", icon=ft.Icons.CANCEL, on_click=self.on_vote_no,
                                      style=ft.ButtonStyle(bgcolor="#c62828", color="white"))

        say_field = ft.TextField(label="Say", hint_text="Enter message", expand=True,
                                 bgcolor="#1e1e24", border_color="#33334d")
        say_field.on_submit = self.on_say_submit
        say_send = ft.FilledButton("Send", icon=ft.Icons.SEND, on_click=self.on_say_submit,
                                   style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        self.cron_command = ft.TextField(label="Spam command", hint_text="command to spam", expand=True, bgcolor="#1e1e24", border_color="#33334d")
        self.cron_delay = ft.TextField(label="Interval (ms)", value="1000", width=120, bgcolor="#1e1e24", border_color="#33334d")
        self.cron_switch = ft.Switch(label="Enable spam send", value=False, on_change=self.on_cron_toggle)

        self.left_cb = ft.Checkbox(label="Left", on_change=lambda e: self.on_input_checkbox_change(e, "left"))
        self.right_cb = ft.Checkbox(label="Right", on_change=lambda e: self.on_input_checkbox_change(e, "right"))
        self.jump_cb = ft.Checkbox(label="Jump", on_change=lambda e: self.on_input_checkbox_change(e, "jump"))
        fire_cb = ft.Checkbox(label="Fire", on_change=lambda e: self.on_input_checkbox_change(e, "fire"))
        hook_cb = ft.Checkbox(label="Hook", on_change=lambda e: self.on_input_checkbox_change(e, "hook"))

        kill_btn = ft.FilledButton("Kill", on_click=self.on_kill_click,
                                   style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
        self.dcm_btn = ft.FilledButton(content=ft.Text("Enable copy moves"),
                                       on_click=self.on_copy_moves_click,
                                       style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        weapon_slider = ft.Slider(min=1, max=5, value=1, divisions=4, label="{value}", expand=True)
        weapon_value = ft.Text("1", size=14)
        def on_weapon_change(e):
            val = int(e.control.value)
            weapon_value.value = str(val)
            weapon_value.update()
            self.send_action_command(f"+weapon{val}")
        weapon_slider.on_change = on_weapon_change

        self.aim_x_slider = ft.Slider(min=-1000, max=1000, value=0, divisions=100, label="{value}", expand=True)
        self.aim_y_slider = ft.Slider(min=-1000, max=1000, value=0, divisions=100, label="{value}", expand=True)
        self.aim_x_value = ft.Text("0", size=14)
        self.aim_y_value = ft.Text("0", size=14)
        def update_x_label(e):
            self.aim_x_value.value = str(int(e.control.value))
            self.aim_x_value.update()
            self.on_aim_slider_change(e, "x")
        def update_y_label(e):
            self.aim_y_value.value = str(int(e.control.value))
            self.aim_y_value.update()
            self.on_aim_slider_change(e, "y")
        self.aim_x_slider.on_change = update_x_label
        self.aim_y_slider.on_change = update_y_label

        self.random_aim_checkbox = ft.Checkbox(label="Random aim", value=False, on_change=self.on_random_aim_toggle)
        self.random_aim_interval = ft.TextField(label="Interval (ms)", value="100", width=120,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.random_for_all_checkbox = ft.Checkbox(label="Random for all", value=False, on_change=self.on_random_for_all_change)

        self.attack_enable_switch = ft.Switch(value=False, on_change=self.on_attack_toggle)
        self.main_id_field = ft.TextField(label="Main ID", value="", width=100,
                                          bgcolor="#1e1e24", border_color="#33334d")
        self.attack_target_field = ft.TextField(label="Target IDs", value="", width=250,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.auto_aim_cb = ft.Checkbox(label="Auto aim", value=False)
        self.hook_target_cb = ft.Checkbox(label="Hook", value=False)
        self.fire_target_cb = ft.Checkbox(label="Fire", value=False)
        self.fire_distance_field = ft.TextField(label="Fire dist", value="65", width=100,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.hook_distance_field = ft.TextField(label="Hook dist", value="400", width=100,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.target_distance_field = ft.TextField(label="Target dist", value="300", width=100,
                                                  bgcolor="#1e1e24", border_color="#33334d")
        self.hook_delay_field = ft.TextField(label="Hook delay (ms)", value="1000", width=100,
                                             bgcolor="#1e1e24", border_color="#33334d")
        self.rescue_frozen_cb = ft.Checkbox(label="Rescue frozen", value=True)
        self.rescue_radius_field = ft.TextField(label="Rescue radius", value="500", width=120,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.kill_on_freeze_cb = ft.Checkbox(label="Kill on freeze", value=False)
        self.attack_main_cb = ft.Checkbox(label="Attack main", value=False)
        self.move_cb = ft.Checkbox(label="Move", value=True)
        self.stand_cb = ft.Checkbox(label="Stand", value=False)
        self.rescue_all_cb = ft.Checkbox(label="Rescue all", value=False)
        self.all_target_cb = ft.Checkbox(label="All target", value=False, on_change=self.on_all_target_change)

        self.auto_hammer_cb = ft.Checkbox(label="Auto hammer", value=False)
        self.stand_on_x_cb = ft.Checkbox(label="Stand on X only", value=False, on_change=self.on_stand_on_x_change)

        self.copy_id_field = ft.TextField(label="Copy from ID", width=100, value="",
                                          bgcolor="#1e1e24", border_color="#33334d")
        self.copy_moves_cb = ft.Checkbox(label="Copy moves", value=False, on_change=self.on_copy_moves_checkbox_change)
        self.delay_field = ft.TextField(label="Delay (ms)", value="0", width=100,
                                        bgcolor="#1e1e24", border_color="#33334d")
        self.delay_checkbox = ft.Checkbox(label="Enable client delay [Experimental]", value=False)

        for control in (self.main_id_field, self.attack_target_field, self.fire_distance_field,
                        self.hook_distance_field, self.target_distance_field, self.hook_delay_field,
                        self.rescue_radius_field,
                        self.auto_aim_cb, self.hook_target_cb, self.fire_target_cb,
                        self.move_cb, self.stand_cb, self.rescue_frozen_cb, self.rescue_all_cb,
                        self.kill_on_freeze_cb, self.attack_main_cb, self.auto_hammer_cb,
                        self.stand_on_x_cb):
            control.on_change = lambda e: self._schedule_attack_config_update()
        self.delay_field.on_change = lambda e: self._send_client_delay()
        self.delay_checkbox.on_change = lambda e: self._send_client_delay()

        attack_container = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("Enable:", size=14), self.attack_enable_switch,
                    self.main_id_field, self.attack_target_field,
                ], spacing=10),
                ft.Row([self.auto_aim_cb, self.hook_target_cb, self.fire_target_cb, self.move_cb, self.stand_cb], spacing=15),
                ft.Row([self.attack_main_cb, self.kill_on_freeze_cb, self.all_target_cb, self.auto_hammer_cb, self.stand_on_x_cb], spacing=15),
                ft.Row([self.fire_distance_field, self.hook_distance_field, self.target_distance_field, self.hook_delay_field], spacing=10),
                ft.Row([self.rescue_frozen_cb, self.rescue_radius_field, self.rescue_all_cb], spacing=10),
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10
        )

        macros_section = self.macro_mgr.build_ui()

        self.input_fields = {
            "player_name": player_name_field,
            "dummy_name": dummy_name_field,
            "player_skin": player_skin_field,
            "dummy_skin": dummy_skin_field,
            "connect_server": connect_server_field,
            "say_text": say_field,
            "aim_x": self.aim_x_slider,
            "aim_y": self.aim_y_slider,
        }

        content = ft.Column([
            ft.Text("Actions", size=24, weight="bold", color="#A855F7"),
            ft.Row([left_container, right_container], spacing=20),
            ft.Container(
                content=ft.Row([
                    ft.Row([connect_server_field, self.connect_button], spacing=10, expand=True),
                    self.dummy_button
                ], spacing=20, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=10, bgcolor="#1a1a24", border_radius=10
            ),
            ft.Container(
                content=ft.Row([
                    self.callvote_input, self.callvote_mode_cb, callvote_btn, vote_yes_btn, vote_no_btn,
                ], spacing=10),
                padding=10, bgcolor="#1a1a24", border_radius=10
            ),
            ft.Container(
                content=ft.Row([say_field, say_send], spacing=10),
                padding=10, bgcolor="#1a1a24", border_radius=10
            ),
            ft.Container(
                content=ft.Row([self.cron_command, self.cron_delay, self.cron_switch], spacing=10),
                padding=10,
                bgcolor="#1a1a24",
                border_radius=10,
                margin=ft.Margin.only(bottom=10)
            ),
            ft.Text("Input controls", size=16, weight="bold"),
            ft.Container(
                content=ft.Column([
                    ft.Row([self.left_cb, self.right_cb, self.jump_cb, fire_cb, hook_cb], spacing=20, wrap=False),
                    ft.Row([kill_btn, self.dcm_btn], spacing=20, wrap=False),
                    ft.Row([ft.Text("Weapon:", size=14), weapon_slider, weapon_value], spacing=10),
                    ft.Row([
                        ft.Text("Copy from ID:", size=14),
                        self.copy_id_field, self.copy_moves_cb,
                        self.delay_field, self.delay_checkbox,
                    ], spacing=10),
                ], spacing=10),
                padding=10, bgcolor="#1a1a24", border_radius=10, expand=True,
            ),
            ft.Text("Aim", size=16, weight="bold"),
            ft.Container(
                content=ft.Column([
                    ft.Row([ft.Text("X:", size=14), self.aim_x_slider, self.aim_x_value], spacing=10),
                    ft.Row([ft.Text("Y:", size=14), self.aim_y_slider, self.aim_y_value], spacing=10),
                    ft.Row([self.random_aim_checkbox, self.random_aim_interval, self.random_for_all_checkbox], spacing=10),
                ], spacing=10),
                padding=10, bgcolor="#1a1a24", border_radius=10
            ),
            ft.Text("Block", size=16, weight="bold"),
            attack_container,
            ft.Text("Macros [Experimental]", size=16, weight="bold"),
            macros_section,
            ft.Text("Code Execute [Experimental]", size=16, weight="bold"),
            self.code_executor.build_ui(),
        ], spacing=10, scroll=ft.ScrollMode.AUTO)

        self.actions_container = ft.Container(content=content, expand=True, padding=20,
                                              alignment=ft.Alignment.TOP_CENTER, visible=False)

    def _build_servers_ui(self):
        self.servers_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Name", weight="bold")),
                ft.DataColumn(ft.Text("Map", weight="bold")),
                ft.DataColumn(ft.Text("Players", weight="bold")),
                ft.DataColumn(ft.Text("Type", weight="bold")),
                ft.DataColumn(ft.Text("Address", weight="bold")),
                ft.DataColumn(ft.Text("Connect", weight="bold")),
            ],
            rows=[],
            heading_row_color="#1e1e24",
            divider_thickness=1,
            border=ft.Border.all(1, "#2a2a3a"),
            border_radius=10,
            horizontal_lines=ft.BorderSide(1, "#2a2a3a"),
            vertical_lines=ft.BorderSide(1, "#2a2a3a"),
            column_spacing=20,
            width=float("inf"),
        )

        refresh_btn = ft.FilledButton(
            "Refresh", icon=ft.Icons.REFRESH,
            on_click=lambda e: self.page.run_task(self._refresh_servers),
            style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white")
        )

        sort_btn = ft.FilledButton(
            "Sort ↕", icon=ft.Icons.SORT,
            on_click=lambda e: self._on_sort_players(),
            style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white")
        )

        self.hide_full_cb = ft.Checkbox(label="Hide full", value=False, on_change=lambda e: self._apply_filter_and_sort())

        self.community_segments = ft.SegmentedButton(
            on_change=self._on_community_filter_change,
            selected=["all"],
            allow_multiple_selection=False,
            allow_empty_selection=False,
            segments=[ft.Segment(value="all", label=ft.Text("All", size=12))],
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(size=12),
            ),
        )

        self.hop_enabled = ft.Switch(label="Enable Server Hop", value=False)
        self.hop_if_players_cb = ft.Checkbox(label="If players >", value=False)
        self.hop_players_count = ft.TextField(
            value="5", width=80,
            bgcolor="#1e1e24", border_color="#33334d",
        )
        self.hop_skip_full_cb = ft.Checkbox(label="Skip full", value=True)
        self.hop_random_all_cb = ft.Checkbox(label="Random for all", value=False)
        self.hop_precommands = ft.TextField(
            label="Precommands", value="", expand=True,
            bgcolor="#1e1e24", border_color="#33334d",
        )
        self.hop_say = ft.TextField(
            label="Say", value="", width=200,
            bgcolor="#1e1e24", border_color="#33334d",
        )
        self.hop_frequency = ft.TextField(
            label="Frequency (ms)", value="5000", width=120,
            bgcolor="#1e1e24", border_color="#33334d",
        )
        self.hop_community_filter = ft.SegmentedButton(
            on_change=self._on_hop_community_filter_change,
            selected=[],
            allow_multiple_selection=True,
            allow_empty_selection=True,
            segments=[ft.Segment(value="none", label=ft.Text("None", size=12))],
            style=ft.ButtonStyle(text_style=ft.TextStyle(size=12)),
            visible=False,
            margin=ft.Margin(top=5, left=0, right=0, bottom=0),
        )
        self.hop_status = ft.Text("", size=12, color="#888888")

        async def on_hop_toggle(e):
            if self.hop_enabled.value:
                self._start_server_hop()
            else:
                self._stop_server_hop()

        self.hop_enabled.on_change = on_hop_toggle

        hop_container = ft.Container(
            content=ft.Column([
                ft.Row([
                    self.hop_enabled,
                    self.hop_status,
                ], spacing=10),
                ft.Row([
                    self.hop_if_players_cb,
                    self.hop_players_count,
                    self.hop_skip_full_cb,
                    self.hop_random_all_cb,
                ], spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    self.hop_precommands,
                    self.hop_say,
                    self.hop_frequency,
                ], spacing=10),
                self.hop_community_filter,
            ], spacing=5),
            padding=10, bgcolor="#1a1a24", border_radius=10
        )

        self._progress_bar = ft.ProgressBar(width=float("inf"), visible=False)

        servers_view = ft.Container(
            content=ft.Column([
                ft.Text("Servers", size=24, weight="bold", color="#A855F7"),
                hop_container,
                ft.Row([refresh_btn, sort_btn, self.hide_full_cb], spacing=10),
                self.community_segments,
                self._progress_bar,
                ft.Container(content=self.servers_table, expand=True, padding=10),
            ], expand=True, scroll=ft.ScrollMode.AUTO),
            padding=20, expand=True,
            alignment=ft.Alignment(-1, -1),
        )
        self.servers_container = ft.Container(content=servers_view, expand=True, visible=False)

    def _build_settings_ui(self):
        semicolon_switch = ft.Switch(label='Adding ";" in commands', value=True, on_change=self.on_semicolon_switch_change)
        proxy_logs_cb = ft.Checkbox(label="Show Proxy logs", value=self.show_proxy_logs, on_change=self.on_proxy_logs_change)
        proxifyre_logs_cb = ft.Checkbox(label="Show ProxiFyre logs", value=self.show_proxifyre_logs, on_change=self.on_proxifyre_logs_change)

        block1 = ft.Container(
            content=ft.Row([
                ft.FilledButton("Optimal Proxies", icon=ft.Icons.SEARCH, on_click=lambda e: self.run_optimal_proxies(),
                                style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white", shape=ft.RoundedRectangleBorder(radius=8))),
                ft.FilledButton("Fast proxies", icon=ft.Icons.FLASH_ON, on_click=self.fast_proxies,
                        style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white", shape=ft.RoundedRectangleBorder(radius=8))),
                ft.FilledButton("Start Proxies", icon=ft.Icons.PLAY_ARROW, on_click=self.toggle_proxies,
                                style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white", shape=ft.RoundedRectangleBorder(radius=8)))
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10
        )
        self.all_clients_btn = ft.FilledButton(
            content=ft.Text("Start all clients"), icon=ft.Icons.PLAY_ARROW,
            on_click=self.toggle_all_clients,
            style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white", shape=ft.RoundedRectangleBorder(radius=8))
        )
        block2 = ft.Container(content=self.all_clients_btn, padding=10, bgcolor="#1a1a24", border_radius=10)
        block3 = ft.Container(
            content=ft.FilledButton("Clear logs", icon=ft.Icons.DELETE, on_click=lambda e: self.clear_logs(),
                                    style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white", shape=ft.RoundedRectangleBorder(radius=8))),
            padding=10, bgcolor="#1a1a24", border_radius=10
        )
        block4 = ft.Container(
            content=ft.FilledButton("Sync clients", icon=ft.Icons.SYNC, on_click=self.sync_clients,
                                    style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white", shape=ft.RoundedRectangleBorder(radius=8))),
            padding=10, bgcolor="#1a1a24", border_radius=10
        )
        top_row = ft.Row([block1, block2, block3, block4], alignment=ft.MainAxisAlignment.START, spacing=10)

        self.fix_players_switch = ft.Switch(label="Try to fix player loading", value=False, on_change=self.on_fix_players_toggle)
        self.timeout_reconnect_switch = ft.Switch(label="Timeout reconnect", value=False, on_change=self.on_timeout_reconnect_toggle)
        self.num_clients_field = ft.TextField(label="Clients", value=str(self.NUM_CLIENTS), width=100,
                                              bgcolor="#1e1e24", border_color="#33334d")
        self.clients_per_proxy_field = ft.TextField(label="Clients per proxy", value=str(self.clients_per_proxy), width=120,
                                                    bgcolor="#1e1e24", border_color="#33334d")
        self.apply_clients_btn = ft.FilledButton("Apply", icon=ft.Icons.CHECK, on_click=self._apply_client_count,
                                                 style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
        self.spare_proxies_switch = ft.Switch(value=False, label="Use spare proxies")
        self.spare_count_field = ft.TextField(label="Spare count", value="5", width=80,
                                              bgcolor="#1e1e24", border_color="#33334d")
        self.target_server_field = ft.TextField(label="Target server", value="", width=200,
                                                bgcolor="#1e1e24", border_color="#33334d")

        try:
            with open("optimal_proxies_new.py", "r", encoding="utf-8") as f:
                content = f.read()
            import re
            match = re.search(r'TARGET_SERVER\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                self.target_server_field.value = match.group(1)
                self.target_server_field.update()
        except:
            pass

        bottom_row = ft.Container(
            content=ft.Column([
                ft.Row([semicolon_switch,
                        self.fix_players_switch,
                        self.timeout_reconnect_switch], spacing=10),
                ft.Row([proxy_logs_cb, proxifyre_logs_cb], spacing=20),
                ft.Row([
                    ft.Text("Set client count:", size=14),
                    self.num_clients_field, self.clients_per_proxy_field, self.apply_clients_btn,
                ], spacing=10),
                ft.Row([self.spare_proxies_switch, self.spare_count_field, self.target_server_field], spacing=10),
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10
        )

        proxy_settings = self._build_proxy_settings()

        settings_view = ft.Container(
            content=ft.Column([
                ft.Text("Settings", size=24, weight="bold", color="#A855F7"),
                top_row,
                bottom_row,
                ft.Text("Proxies", size=16, weight="bold"),
                proxy_settings,
            ], spacing=10, scroll=ft.ScrollMode.AUTO),
            padding=20, expand=True,
            alignment=ft.Alignment(-1, -1),
        )
        self.settings_container = ft.Container(content=settings_view, expand=True, visible=False)

    def _build_proxy_settings(self):
        self.proxy_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Port", weight="bold")),
                ft.DataColumn(ft.Text("Proxy", weight="bold")),
                ft.DataColumn(ft.Text("Action", weight="bold")),
            ],
            rows=[],
            heading_row_color="#1e1e24",
            divider_thickness=1,
            border=ft.Border.all(1, "#2a2a3a"),
            border_radius=10,
            horizontal_lines=ft.BorderSide(1, "#2a2a3a"),
            vertical_lines=ft.BorderSide(1, "#2a2a3a"),
            column_spacing=20,
            width=float("inf"),
        )
        self.spare_label = ft.Text(f"Spare proxies: {len(self.spare_proxies)}", size=14)
        refresh_btn = ft.FilledButton("Refresh", icon=ft.Icons.REFRESH,
                                      on_click=self._refresh_proxies_table,
                                      style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
        top_bar = ft.Row([refresh_btn, self.spare_label], spacing=20, alignment=ft.MainAxisAlignment.START)
        return ft.Container(
            content=ft.Column([
                top_bar,
                ft.Container(content=self.proxy_table, expand=True),
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10, expand=True
        )

    def _rebuild_clients_table(self):
        self.clients_table = self._build_clients_table()
        clients_view = ft.Container(
            content=ft.Column([
                self.clients_table_header,
                ft.Container(content=self.clients_table, expand=True, padding=10),
            ], expand=True, scroll=ft.ScrollMode.AUTO),
            padding=20, expand=True,
        )
        self.clients_container.content = clients_view
        self.clients_container.update()

    def _refresh_proxies_table(self, e=None):
        if not self.settings_container.visible:
            return
        self._load_spare_proxies()
        if hasattr(self, 'spare_label'):
            self.spare_label.value = f"Spare proxies: {len(self.spare_proxies)}"
            self.spare_label.update()
        json_path = os.path.join(os.path.dirname(__file__), "Settings", "proxies.json")
        if not os.path.exists(json_path):
            return
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            rows = []
            for item in data:
                port = item["port"]
                key = item["key"]
                key_preview = key.split("#")[0][:50]
                replace_btn = ft.FilledButton("Replace", icon=ft.Icons.REFRESH,
                                              on_click=lambda e, p=port: self._replace_proxy(p),
                                              style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
                rows.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(str(port))),
                    ft.DataCell(ft.Text(key_preview)),
                    ft.DataCell(replace_btn),
                ]))
            self.proxy_table.rows = rows
            self.proxy_table.update()
        except Exception as e:
            self.add_log(f"❌ Error updating proxy table: {e}")

    def on_send_click(self):
        if self.page.input_field.value:
            cmd = self.page.input_field.value.strip()
            if cmd:
                self.add_log(f"> {cmd}")
                self.send_command_to_clients(cmd)
                self.page.input_field.value = ""
                self.page.update()
                self.page.run_task(self.page.input_field.focus)

    def on_player_name_submit(self, e):
        name = self.input_fields.get("player_name")
        if name and name.value.strip():
            self.send_action_command(f"player_name {name.value.strip()}")

    def on_dummy_name_submit(self, e):
        name = self.input_fields.get("dummy_name")
        if name and name.value.strip():
            self.send_action_command(f"dummy_name {name.value.strip()}")

    def on_player_skin_submit(self, e):
        skin = self.input_fields.get("player_skin")
        if skin and skin.value.strip():
            self.send_action_command(f"player_skin {skin.value.strip()}")

    def on_dummy_skin_submit(self, e):
        skin = self.input_fields.get("dummy_skin")
        if skin and skin.value.strip():
            self.send_action_command(f"dummy_skin {skin.value.strip()}")

    def on_connect_click(self, e):
        if self.connect_button.content.value == "Disconnect":
            self.send_action_command("disconnect")
            self.connect_button.content = ft.Text("Connect")
        else:
            server = self.input_fields.get("connect_server")
            if server and server.value.strip():
                self.send_action_command(f"connect {server.value.strip()}")
                self.connect_button.content = ft.Text("Disconnect")
            else:
                self.add_log("⚠️ Enter server address")
        self.connect_button.update()

    def on_dummy_connect_click(self, e):
        if self.dummy_button.content.value == "Connect dummy":
            self.send_action_command("dummy_connect")
            self.dummy_button.content = ft.Text("Disconnect dummy")
        else:
            self.send_action_command("dummy_disconnect")
            self.dummy_button.content = ft.Text("Connect dummy")
        self.dummy_button.update()

    def on_kill_click(self, e):
        self.send_action_command("kill; say /kill")
        self.add_log("🔪 Kill command sent")

    def on_copy_moves_click(self, e):
        if "Enable" in self.dcm_btn.content.value:
            self.send_action_command("cl_dummy_copy_moves 1")
            self.dcm_btn.content = ft.Text("Disable copy moves")
        else:
            self.send_action_command("cl_dummy_copy_moves 0")
            self.dcm_btn.content = ft.Text("Enable copy moves")
        self.dcm_btn.update()

    def on_vote_yes(self, e):
        self.send_action_command("vote yes")

    def on_vote_no(self, e):
        self.send_action_command("vote no")

    def on_callvote_click(self, e):
        target = self.callvote_input.value.strip()
        if not target:
            self.add_log("⚠️ Enter player name or ID")
            return
        if self.callvote_mode_cb.value:
            found_id = self.find_player_id_by_name(target)
            if found_id is not None:
                self.send_action_command(f"callvote kick {found_id}")
                self.add_log(f"🗳️ Callvote kick {found_id} (name: {target})")
            else:
                self.add_log(f"❌ Player '{target}' not found")
        else:
            self.send_action_command(f"callvote kick {target}")
            self.add_log(f"🗳️ Callvote kick {target}")
        self.callvote_input.value = ""
        self.callvote_input.update()

    def find_player_id_by_name(self, name: str) -> Optional[int]:
        players = self.bridge_receiver.get_all_players()
        for pid, data in players.items():
            if data.get('name', '').lower() == name.lower():
                return pid
        return None

    def on_say_submit(self, e):
        say = self.input_fields.get("say_text")
        if say and say.value.strip():
            self.send_action_command(f"say {say.value.strip()}")
            say.value = ""
            say.update()

    async def on_cron_toggle(self, e):
        if self.cron_switch.value:
            if not hasattr(self, '_cron_task') or self._cron_task is None or self._cron_task.done():
                self._cron_task = asyncio.create_task(self._cron_loop())
                self.add_log(f"⏱️ Spam command started: '{self.cron_command.value}' every {self.cron_delay.value} ms")
        else:
            if hasattr(self, '_cron_task') and self._cron_task and not self._cron_task.done():
                self._cron_task.cancel()
                self.add_log("⏱️ Spam command stopped")

    async def _cron_loop(self):
        while self.cron_switch.value:
            cmd = self.cron_command.value.strip()
            if cmd:
                self.send_action_command(cmd)
            try:
                delay = int(self.cron_delay.value.strip())
                if delay < 10:
                    delay = 10
            except:
                delay = 1000
            await asyncio.sleep(delay / 1000.0)

    def on_input_checkbox_change(self, e, input_name: str):
        if e.control.value:
            self.send_action_command(f"c_input {input_name} 100000000")
        else:
            self.send_action_command(f"c_input {input_name} 20")

    def on_aim_slider_change(self, e, axis: str):
        x_val = int(self.aim_x_slider.value)
        y_val = int(self.aim_y_slider.value)
        self.send_action_command(f"c_oaim {x_val} {y_val}")

    async def on_random_aim_toggle(self, e):
        if self.random_aim_checkbox.value:
            if self.random_aim_task is None or self.random_aim_task.done():
                self.random_aim_task = asyncio.create_task(self.random_aim_loop())
        else:
            if self.random_aim_task and not self.random_aim_task.done():
                self.random_aim_task.cancel()
                self.random_aim_task = None
                selected = self.get_selected_clients()
                for cid in selected:
                    self.control_server.send_command([cid], "c_random_aim 0")

    def on_random_for_all_change(self, e):
        if not self.random_for_all_checkbox.value:
            selected = self.get_selected_clients()
            for cid in selected:
                self.control_server.send_command([cid], "c_random_aim 0")

    async def random_aim_loop(self):
        while self.random_aim_checkbox.value:
            try:
                interval_ms = int(self.random_aim_interval.value.strip() or 100)
            except ValueError:
                interval_ms = 100

            if self.random_for_all_checkbox.value:
                selected = self.get_selected_clients()
                for cid in selected:
                    self.control_server.send_command([cid], f"c_random_aim 1 {interval_ms}")
            else:
                x = random.randint(-1000, 1000)
                y = random.randint(-1000, 1000)
                self.send_action_command(f"c_oaim {x} {y}")
                self.aim_x_slider.value = x
                self.aim_y_slider.value = y
                self.aim_x_value.value = str(x)
                self.aim_y_value.value = str(y)
                self.aim_x_slider.update()
                self.aim_y_slider.update()
                self.aim_x_value.update()
                self.aim_y_value.update()

            await asyncio.sleep(interval_ms / 1000.0)

    def on_copy_moves_checkbox_change(self, e):
        if self.copy_moves_cb.value:
            copy_id = self.copy_id_field.value.strip()
            if not copy_id:
                self.send_action_command("c_copy_moves -1")
                return
            self.send_action_command(f"c_copy_moves {copy_id}")
        else:
            self.send_action_command("c_copy_moves -1")

    def _get_auto_bots_ids(self) -> str:
        selected = self.get_selected_clients()
        bot_ids = set()
        main_id = self.main_id_field.value.strip()
        main_id_int = int(main_id) if main_id and main_id != "-1" else None
        for cid in selected:
            lid = self.bridge_receiver.get_local_id(cid)
            if lid is not None and lid != main_id_int:
                bot_ids.add(lid)
        result = ','.join(str(pid) for pid in bot_ids)
        return result if result else "-1"

    def _send_attack_config(self):
        if not self.attack_enable_switch.value:
            return
        main_id = self.main_id_field.value.strip()
        targets_str = self.attack_target_field.value.strip()
        bots_str = self._get_auto_bots_ids()
        all_target = self.all_target_cb.value

        if not main_id:
            main_id = "-1"
        if not targets_str:
            targets_str = "-1"

        auto_aim   = 1 if self.auto_aim_cb.value else 0
        auto_fire  = 1 if self.fire_target_cb.value else 0
        auto_hook  = 1 if self.hook_target_cb.value else 0
        move       = 1 if self.move_cb.value else 0
        stand      = 1 if self.stand_cb.value else 0
        rescue     = 1 if self.rescue_frozen_cb.value else 0
        rescue_all = 1 if self.rescue_all_cb.value else 0
        kill_frz   = 1 if self.kill_on_freeze_cb.value else 0
        attack_main= 1 if self.attack_main_cb.value else 0
        auto_hammer= 1 if self.auto_hammer_cb.value else 0

        fire_dist     = self.fire_distance_field.value.strip() or "80"
        hook_dist     = self.hook_distance_field.value.strip() or "400"
        rescue_radius = self.rescue_radius_field.value.strip() or "500"
        target_dist   = self.target_distance_field.value.strip() or "300"
        hook_delay    = self.hook_delay_field.value.strip() or "1000"

        self.send_action_command(f"c_main {main_id}")
        self.send_action_command(f"c_targets {targets_str}")
        if bots_str:
            self.send_action_command(f"c_bots {bots_str}")
        self.send_action_command(f"c_target_all {1 if all_target else 0}")
        self.send_action_command(f"c_atk_set {auto_aim} {auto_fire} {auto_hook} {move} {stand} {rescue} {rescue_all} {kill_frz} {attack_main} {auto_hammer}")
        self.send_action_command(f"c_atk_dists {fire_dist} {hook_dist} {rescue_radius} {target_dist}")
        self.send_action_command(f"c_atk_hook_delay {hook_delay}")
        self._send_client_delay()
        self.send_action_command(f"c_stand_on_x {1 if self.stand_on_x_cb.value else 0}")

    def _send_client_delay(self):
        selected = self.get_selected_clients()
        if not selected:
            return
        enabled = self.delay_checkbox.value
        try:
            base_delay = int(self.delay_field.value.strip())
        except ValueError:
            base_delay = 0
        if not enabled or base_delay == 0:
            for cid in selected:
                self.control_server.send_command([cid], "c_client_delay 0")
        else:
            for cid in selected:
                delay = base_delay * cid
                self.control_server.send_command([cid], f"c_client_delay {delay}")

    def _schedule_attack_config_update(self, *args):
        if not self.attack_enable_switch.value:
            return
        if self._config_timer:
            self._config_timer.cancel()
        self._config_timer = threading.Timer(0.3, self._send_attack_config)
        self._config_timer.start()

    async def on_attack_toggle(self, e):
        if self.attack_enable_switch.value:
            self._send_attack_config()
            self.send_action_command("c_attack 1")
            self.add_log("⚔️ Attack mode enabled on selected clients")
        else:
            self.send_action_command("c_attack 0")
            self.send_action_command("+left;+right;+jump;+fire;c_input left 0;c_input right 0;c_input fire 0")
            self.add_log("🕊️ Attack mode disabled")
        self.page.update()

    def on_all_target_change(self, e):
        if self.all_target_cb.value:
            self.attack_target_field.label = "Untarged IDs"
        else:
            self.attack_target_field.label = "Target IDs"
        self.attack_target_field.update()
        self._schedule_attack_config_update()

    def on_stand_on_x_change(self, e):
        self._schedule_attack_config_update()

    def on_fix_players_toggle(self, e):
        if self.fix_players_switch.value:
            self.send_action_command("zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-; zoom-")
            self.add_log("🔧 Player loading fix applied")
        else:
            self.send_action_command("zoom")
            self.add_log("🔧 Player loading fix reverted")

    def on_timeout_reconnect_toggle(self, e):
        if self.timeout_reconnect_switch.value:
            self.send_action_command("conn_timeout 5; cl_reconnect_timeout 1")
            self.add_log("🔁 Fast reconnect enabled")
        else:
            self.send_action_command("conn_timeout 100; cl_reconnect_timeout 120")
            self.add_log("🔁 Fast reconnect disabled")

    def toggle_client(self, client_id: int, button: ft.Button):
        if self.client_manager.is_running(client_id):
            self.client_manager.stop(client_id)
            button.content = ft.Text("Connect")
            button.icon = ft.Icons.PLAY_ARROW
            self.control_server.remove_client(client_id)
        else:
            show_logs = self.logs_checkboxes[client_id - 1].value
            success = self.client_manager.launch(client_id, show_logs)
            if success:
                button.content = ft.Text("Disconnect")
                button.icon = ft.Icons.STOP
            else:
                button.content = ft.Text("Connect")
                button.icon = ft.Icons.PLAY_ARROW
        button.update()
        self.page.update()

    def toggle_all_clients(self, e):
        running_count = sum(1 for i in range(1, self.NUM_CLIENTS + 1) if self.client_manager.is_running(i))
        if running_count == self.NUM_CLIENTS:
            self.client_manager.stop_all()
            for i in range(1, self.NUM_CLIENTS + 1):
                self.control_server.remove_client(i)
            for btn in self.connect_buttons:
                btn.content = ft.Text("Connect")
                btn.icon = ft.Icons.PLAY_ARROW
                btn.update()
            self.all_clients_btn.content = ft.Text("Start all clients")
            self.all_clients_btn.icon = ft.Icons.PLAY_ARROW
            self.add_log(f"🛑 All {self.NUM_CLIENTS} HDDNet clients stopped")
        else:
            def launch_with_delay():
                started = 0
                for i in range(1, self.NUM_CLIENTS + 1):
                    if not self.client_manager.is_running(i):
                        show_logs = self.logs_checkboxes[i - 1].value if i - 1 < len(self.logs_checkboxes) else True
                        if self.client_manager.launch(i, show_logs):
                            self.connect_buttons[i - 1].content = ft.Text("Disconnect")
                            self.connect_buttons[i - 1].icon = ft.Icons.STOP
                            self._loop.call_soon_threadsafe(self.connect_buttons[i - 1].update)
                            self._loop.call_soon_threadsafe(self.page.update)
                            started += 1
                        time.sleep(0.1)
                new_running = sum(1 for i in range(1, self.NUM_CLIENTS + 1) if self.client_manager.is_running(i))
                if new_running == self.NUM_CLIENTS:
                    self._loop.call_soon_threadsafe(lambda: setattr(self.all_clients_btn, 'content', ft.Text("Stop all clients")))
                    self._loop.call_soon_threadsafe(lambda: setattr(self.all_clients_btn, 'icon', ft.Icons.STOP))
                self._loop.call_soon_threadsafe(self.update_clients_stats)
                self._loop.call_soon_threadsafe(self.page.update)
                self.add_log(f"🚀 Started {started} HDDNet clients (now: {new_running}/{self.NUM_CLIENTS})")

            threading.Thread(target=launch_with_delay, daemon=True).start()
            self.add_log(f"🚀 Starting {self.NUM_CLIENTS} HDDNet clients with 100ms delay...")

        self.all_clients_btn.update()
        self.update_clients_stats()
        self.page.update()

    async def players_tab_loop(self):
        weapon_names = {0: "Hammer", 1: "Pistol", 2: "Shotgun", 3: "Rocket", 4: "Laser", 5: "Ninja"}
        while True:
            await asyncio.sleep(0.5)

            if not self.tab_container.visible:
                continue
            if not hasattr(self, 'players_table') or self.players_table is None:
                continue

            our_bot_ids = set()
            for cid in range(1, self.NUM_CLIENTS + 1):
                lid = self.bridge_receiver.get_local_id(cid)
                if lid is not None:
                    our_bot_ids.add(lid)

            players = self.bridge_receiver.get_all_players()
            rows = []
            for pid, data in players.items():
                player_type = "Bot" if pid in our_bot_ids else "Player"
                weapon = data.get('weapon', -1)
                weapon_str = weapon_names.get(weapon, f"Unknown ({weapon})")
                weapon_display = f"{weapon_str} ({weapon})"
                target_x = data.get('target_x', 0)
                target_y = data.get('target_y', 0)
                aim_str = f"{target_x},{target_y}"
                rows.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(data.get('name', '')[:20])),
                    ft.DataCell(ft.Text(str(pid))),
                    ft.DataCell(ft.Text(f"{data['x']:.1f}")),
                    ft.DataCell(ft.Text(f"{data['y']:.1f}")),
                    ft.DataCell(ft.Text(weapon_display)),
                    ft.DataCell(ft.Text(str(data.get('health', 0)))),
                    ft.DataCell(ft.Text("True" if data.get('frozen') else "False")),
                    ft.DataCell(ft.Text(player_type)),
                    ft.DataCell(ft.Text(str(data.get('direction', 0)))),
                    ft.DataCell(ft.Text(str(data.get('jumped', 0)))),
                    ft.DataCell(ft.Text(str(data.get('hook_state', 0)))),
                    ft.DataCell(ft.Text(str(data.get('angle', 0)))),
                    ft.DataCell(ft.Text(str(data.get('attack_tick', 0)))),
                    ft.DataCell(ft.Text(aim_str)),
                ]))
            if self.players_table:
                self.players_table.rows = rows
                self.players_table.update()

    def on_semicolon_switch_change(self, e):
        self.add_semicolons = e.control.value

    def on_proxy_logs_change(self, e):
        self.show_proxy_logs = e.control.value

    def on_proxifyre_logs_change(self, e):
        self.show_proxifyre_logs = e.control.value

    def on_nav_change(self, e):
        idx = e.control.selected_index
        self.console_container.visible = False
        self.clients_container.visible = False
        self.tab_container.visible = False
        self.actions_container.visible = False
        self.servers_container.visible = False
        self.settings_container.visible = False
        if idx == 0:
            self.console_container.visible = True
            self.page.run_task(self.page.input_field.focus)
        elif idx == 1:
            self.clients_container.visible = True
        elif idx == 2:
            self.actions_container.visible = True
        elif idx == 3:
            self.tab_container.visible = True
        elif idx == 4:
            self.servers_container.visible = True
        else:
            self.settings_container.visible = True
        self.page.update()

    def run_optimal_proxies(self):
        self.switch_to_console()
        self.add_log("🔍 Starting proxy selection (optimal_proxies_new.py)...")
        if hasattr(self, 'optimal_proxies_proc') and self.optimal_proxies_proc and self.optimal_proxies_proc.poll() is None:
            self.add_log("⚠️ Proxy selection already running")
            return
        script_path = os.path.join(os.path.dirname(__file__), "optimal_proxies_new.py")
        if not os.path.exists(script_path):
            self.add_log(f"❌ File {script_path} not found")
            return
        was_running = self.control_server.running
        if was_running:
            self.control_server.stop()
            self.add_log("⏸️ TCP Control Server temporarily stopped for proxy selection")
        was_bridge_running = self.bridge_receiver.running
        if was_bridge_running:
            self.bridge_receiver.stop()
            self.add_log("⏸️ Bridge Receiver temporarily stopped for proxy selection")
        cmd = [sys.executable, "-u", script_path]
        target = self.target_server_field.value.strip()
        if target:
            cmd.append(f"--target-server={target}")
        if self.spare_proxies_switch.value:
            count = self.spare_count_field.value.strip() or "5"
            cmd.append(f"--spare-proxies={count}")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, encoding='utf-8', env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.optimal_proxies_proc = proc
        def read_output():
            try:
                for line in iter(proc.stdout.readline, ''):
                    if line:
                        self.add_log(f"[OptimalProxies] {line.rstrip()}")
            except Exception as ex:
                self.add_log(f"[OptimalProxies] Read error: {ex}")
            finally:
                proc.stdout.close()
                proc.wait()
                self.add_log(f"[OptimalProxies] Finished (code {proc.returncode})")
                if getattr(self, 'optimal_proxies_proc', None) == proc:
                    self.optimal_proxies_proc = None
                self._refresh_proxies_table()
                self._load_spare_proxies()
                if was_running:
                    self.control_server.start()
                    self.add_log("▶️ TCP Control Server restarted")
                if was_bridge_running:
                    self.bridge_receiver.start()
                    self.add_log("▶️ Bridge Receiver restarted")
        threading.Thread(target=read_output, daemon=True).start()

    def fast_proxies(self, e):
        import random
        import socks as pysocks
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from python_v2ray.config_parser import parse_uri
        import requests
        import threading

        temp_dir = os.path.join(os.path.dirname(__file__), "temp")
        os.makedirs(temp_dir, exist_ok=True)

        def key_preview(key: str) -> str:
            return key.split("#")[0][:60]

        def parse_key_to_config(key: str) -> dict | None:
            try:
                parsed = parse_uri(key)
                server = getattr(parsed, 'address', None) or getattr(parsed, 'server', None) or getattr(parsed, 'host', None)
                if not server:
                    return None
                if parsed.protocol in ('vless', 'vmess'):
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
                elif parsed.protocol in ('shadowsocks', 'ss'):
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
            return None

        def test_single_key(key: str, port: int) -> tuple | None:
            outbound = parse_key_to_config(key)
            if not outbound:
                return None

            config = {
                "log": {"loglevel": "error"},
                "inbounds": [{"port": port, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": True}}],
                "outbounds": [outbound]
            }

            cfg_file = os.path.join(temp_dir, f"_fast_{port}.json")
            proc = None
            try:
                with open(cfg_file, "w") as f:
                    json.dump(config, f)
                proc = subprocess.Popen(["xray.exe", "-c", cfg_file],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(0.8)
                proxies = {"http": f"socks5://127.0.0.1:{port}", "https": f"socks5://127.0.0.1:{port}"}
                start = time.time()
                resp = requests.get("http://ifconfig.me/ip", proxies=proxies, timeout=5)
                if resp.status_code not in (200, 204):
                    return None
                s = pysocks.socksocket()
                s.set_proxy(pysocks.SOCKS5, "127.0.0.1", port)
                s.settimeout(5)
                s.connect(("8.8.8.8", 53))
                s.send(b'\xaa\xbb\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x06google\x03com\x00\x00\x01\x00\x01')
                s.recv(512)
                s.close()
                ping = (time.time() - start) * 1000
                return (ping, key)
            except Exception:
                return None
            finally:
                if proc:
                    proc.terminate()
                    time.sleep(0.1)
                if os.path.exists(cfg_file):
                    os.remove(cfg_file)

        def run_fast_proxies():
            FAST_URL = "https://raw.githubusercontent.com/lothiann/DMClients/refs/heads/main/fastproxies.json"
            MAX_WORKERS = 20

            N = (self.NUM_CLIENTS + self.clients_per_proxy - 1) // self.clients_per_proxy
            self.add_log(f"⚡ Fast proxies: need {N} proxies for {self.NUM_CLIENTS} clients, {self.clients_per_proxy} per proxy")

            try:
                r = requests.get(FAST_URL, timeout=10)
                r.raise_for_status()
                data = r.json()
                if isinstance(data, list):
                    if len(data) > 0 and isinstance(data[0], dict) and "key" in data[0]:
                        raw_keys = [item["key"] for item in data]
                    else:
                        raw_keys = [str(item) for item in data]
                else:
                    self.add_log("❌ Unexpected JSON format")
                    return
                self.add_log(f"📥 Loaded {len(raw_keys)} keys from fastproxies.json")
            except Exception as ex:
                self.add_log(f"❌ Failed to load fast proxies list: {ex}")
                return

            if not raw_keys:
                self.add_log("❌ No keys found")
                return

            self.add_log("⏳ Testing proxies (HTTP + DNS)...")
            working = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {}
                port = 30000
                for key in raw_keys:
                    port += 1
                    futures[executor.submit(test_single_key, key, port)] = key

                completed = 0
                for future in as_completed(futures):
                    completed += 1
                    result = future.result()
                    if result:
                        working.append(result)
                    if completed % 50 == 0:
                        self.add_log(f"   Tested {completed}/{len(raw_keys)}, working: {len(working)}")

            self.add_log(f"✅ Fast test finished: {len(working)} working proxies")
            if not working:
                self.add_log("❌ No working proxies found")
                return

            random.shuffle(working)
            selected = working[:N] if len(working) >= N else working
            if len(selected) < N:
                self.add_log(f"⚠️ Only {len(selected)} working proxies, need {N}")

            proxies_list = []
            for idx, (ping, key) in enumerate(selected):
                proxies_list.append({
                    "port": 10801 + idx,
                    "key": key
                })
                self.add_log(f"   Selected: {key_preview(key)} ({ping:.0f}ms) -> port {10801 + idx}")

            settings_dir = os.path.join(os.path.dirname(__file__), "Settings")
            os.makedirs(settings_dir, exist_ok=True)
            json_path = os.path.join(settings_dir, "proxies.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(proxies_list, f, indent=2, ensure_ascii=False)

            self.add_log(f"💾 Saved {len(proxies_list)} fast proxies to {json_path}")
            self._refresh_proxies_table()

        threading.Thread(target=run_fast_proxies, daemon=True).start()

    def toggle_proxies(self, e):
        btn = e.control
        ports_running = getattr(self, 'ports_proxies_proc', None) and self.ports_proxies_proc.poll() is None
        proxifyre_running = getattr(self, 'proxifyre_proc', None) and self.proxifyre_proc.poll() is None
        if ports_running or proxifyre_running:
            self.add_log("🛑 Stopping proxy services...")
            if getattr(self, 'ports_proxies_proc', None):
                kill_process_tree(self.ports_proxies_proc.pid)
                self.ports_proxies_proc = None
            if getattr(self, 'proxifyre_proc', None):
                kill_process_tree(self.proxifyre_proc.pid)
                self.proxifyre_proc = None
            btn.content = ft.Text("Start proxies")
            btn.icon = ft.Icons.PLAY_ARROW
            btn.update()
            self.add_log("✅ Proxy services stopped")
        else:
            self.switch_to_console()
            self.add_log("🚀 Starting proxy services...")
            ports_script = os.path.join(os.path.dirname(__file__), "ports_proxies.py")
            if not os.path.exists(ports_script):
                self.add_log(f"❌ File {ports_script} not found")
                return
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            proc_ports = subprocess.Popen(
                [sys.executable, "-u", ports_script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding='utf-8', env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            self.ports_proxies_proc = proc_ports
            def read_ports():
                try:
                    for line in iter(proc_ports.stdout.readline, ''):
                        if line:
                            if self.show_proxy_logs:
                                self.add_log(f"[PortsProxies] {line.rstrip()}")
                except Exception as ex:
                    self.add_log(f"[PortsProxies] Read error: {ex}")
                finally:
                    proc_ports.stdout.close()
                    proc_ports.wait()
                    self.add_log(f"[PortsProxies] Finished (code {proc_ports.returncode})")
                    if getattr(self, 'ports_proxies_proc', None) == proc_ports:
                        self.ports_proxies_proc = None
            threading.Thread(target=read_ports, daemon=True).start()
            proxifyre_path = os.path.join(os.path.dirname(__file__), "ProxiFyre", "ProxiFyre.exe")
            if os.path.exists(proxifyre_path):
                try:
                    proc_prox = subprocess.Popen(
                        [proxifyre_path],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, encoding='utf-8',
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                    )
                    self.proxifyre_proc = proc_prox
                    self.add_log(f"[ProxiFyre] Started (PID {proc_prox.pid})")
                    def read_proxifyre():
                        try:
                            for line in iter(proc_prox.stdout.readline, ''):
                                if line:
                                    if self.show_proxifyre_logs:
                                        self.add_log(f"[ProxiFyre] {line.rstrip()}")
                        except Exception as ex:
                            self.add_log(f"[ProxiFyre] Read error: {ex}")
                        finally:
                            proc_prox.stdout.close()
                            proc_prox.wait()
                            self.add_log(f"[ProxiFyre] Finished (code {proc_prox.returncode})")
                            if getattr(self, 'proxifyre_proc', None) == proc_prox:
                                self.proxifyre_proc = None
                    threading.Thread(target=read_proxifyre, daemon=True).start()
                except Exception as ex:
                    self.add_log(f"[ProxiFyre] Start error: {ex}")
            else:
                self.add_log(f"❌ {proxifyre_path} not found")
            btn.content = ft.Text("Stop proxies")
            btn.icon = ft.Icons.STOP
            btn.update()

    def clear_logs(self):
        self.log_box.controls.clear()
        self.page.update()
        self.add_log("🧹 Logs cleared")

    def sync_clients(self, e=None):
        self.send_action_command("c_sync")
        self.add_log("🔄 Sync command sent to selected clients")

    def switch_to_console(self):
        self.console_container.visible = True
        self.clients_container.visible = False
        self.tab_container.visible = False
        self.settings_container.visible = False
        self.actions_container.visible = False
        self.servers_container.visible = False
        self.nav_rail.selected_index = 0
        self.page.update()
        self.page.run_task(self.page.input_field.focus)

    async def _refresh_servers(self):
        import urllib.request
        import time
        import ssl
        import json
        from concurrent.futures import ThreadPoolExecutor

        if not hasattr(self, '_progress_bar'):
            self._progress_bar = ft.ProgressBar(width=float("inf"), visible=False)
        self._progress_bar.visible = True
        self._progress_bar.update()

        urls = [
            "https://master1.ddnet.org/ddnet/15/servers.json",
            "https://master2.ddnet.org/ddnet/15/servers.json",
            "https://master3.ddnet.org/ddnet/15/servers.json",
            "https://master4.ddnet.org/ddnet/15/servers.json",
        ]

        def check_performance(url):
            try:
                ctx = ssl._create_unverified_context()
                start = time.perf_counter()
                req = urllib.request.Request(url, headers={'User-Agent': 'DMClients/1.0'})
                with urllib.request.urlopen(req, timeout=3.0, context=ctx) as resp:
                    chunk = resp.read(100)
                    if not chunk:
                        return (url, 999.0)
                    total_duration = time.perf_counter() - start
                    return (url, total_duration)
            except:
                return (url, 999.0)

        def fetch():
            self.add_log("📡 Testing masters (Ping + Speed 100B)...")
            with ThreadPoolExecutor(max_workers=len(urls)) as executor:
                results = list(executor.map(check_performance, urls))
            results.sort(key=lambda x: x[1])
            valid_results = [r for r in results if r[1] < 999.0]
            if not valid_results:
                return None
            fastest_url, best_time = valid_results[0]
            self.add_log(f"⚡ Best performance: {fastest_url.split('/')[2]} ({int(best_time*1000)}ms)")
            try:
                ctx = ssl._create_unverified_context()
                req = urllib.request.Request(fastest_url, headers={'User-Agent': 'DMClients/1.0'})
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except Exception as e:
                self.add_log(f"⚠️ Failed to download: {e}")
                return None

        try:
            data = await asyncio.wait_for(asyncio.to_thread(fetch), timeout=30)
        except asyncio.TimeoutError:
            data = None

        if not data:
            self.add_log("❌ Failed to fetch servers (No response from any master)")
            self._progress_bar.visible = False
            self._progress_bar.update()
            return

        communities_list = data.get("communities", [])
        self._community_names = {"all": "All"}
    
        new_segments = [ft.Segment(value="all", label=ft.Text("All", size=12))]
        new_segments.append(ft.Segment(value="none", label=ft.Text("None", size=12)))
        for c in communities_list:
            if "id" in c:
                cid = c["id"]
                cname = c.get("name", cid)
                self._community_names[cid] = cname
                new_segments.append(ft.Segment(value=cid, label=ft.Text(cname, size=12)))
        self.community_segments.segments = new_segments
        self.community_segments.update()
    
        hop_segments = [ft.Segment(value="none", label=ft.Text("None", size=12))]
        for c in communities_list:
            if "id" in c:
                cid = c["id"]
                cname = c.get("name", cid)
                hop_segments.append(ft.Segment(value=cid, label=ft.Text(cname, size=12)))
        self.hop_community_filter.segments = hop_segments
        self.hop_community_filter.visible = True
        self.hop_community_filter.update()

        servers = data.get("servers", [])
        if servers:
            asyncio.create_task(self._load_servers_async(servers))
        else:
            self._progress_bar.visible = False
            self._progress_bar.update()

    async def _load_servers_async(self, servers):
        rows = []
        total = len(servers)
        chunk_size = 100
        delay = 0.5

        for i, srv in enumerate(servers):
            info = srv.get("info", {})
            name = info.get("name", "?")[:20]
            map_name = info.get("map", {}).get("name", "?")[:30]
            game_type = info.get("game_type", "?")

            clients = info.get("clients", [])
            players = len(clients)
            max_players = info.get("max_players", 0)

            addresses = srv.get("addresses", [])
            if not addresses:
                continue

            addr_str = addresses[0]
            match = re.search(r'://(.+)', addr_str)
            full_ip = match.group(1).strip('[]') if match else addr_str
            display_ip = full_ip[:15]

            community = srv.get("community", "none")

            def connect_click(e, ip=full_ip):
                self.send_action_command(f"connect {ip}")

            connect_btn = ft.FilledButton(
                "Connect", icon=ft.Icons.LINK,
                on_click=connect_click,
                style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white")
            )

            row_obj = ft.DataRow(cells=[
                ft.DataCell(ft.Text(name, size=14)),
                ft.DataCell(ft.Text(map_name, size=14)),
                ft.DataCell(ft.Text(f"{players}/{max_players}", size=14)),
                ft.DataCell(ft.Text(game_type, size=14)),
                ft.DataCell(ft.Text(display_ip, size=12)),
                ft.DataCell(connect_btn),
            ])

            rows.append((players, max_players, row_obj, community, full_ip))

            if (i + 1) % chunk_size == 0 or (i + 1) == total:
                self._sorted_server_rows = rows
                self._apply_filter_and_sort()
                progress = (i + 1) / total
                self._progress_bar.value = progress
                self._progress_bar.update()
                await asyncio.sleep(delay)

        self._sorted_server_rows = rows
        self._apply_filter_and_sort()
        self._progress_bar.value = None
        self._progress_bar.visible = False
        self._progress_bar.update()
        self.add_log(f"✅ All {len(rows)} servers loaded")

    def _apply_filter_and_sort(self):
        if not hasattr(self, '_sorted_server_rows') or not self._sorted_server_rows:
            return
        rows = list(self._sorted_server_rows)
        if self._current_community_filter:
            rows = [r for r in rows if r[3] == self._current_community_filter]
        if hasattr(self, 'hide_full_cb') and self.hide_full_cb.value:
            rows = [r for r in rows if r[0] < r[1]]
        rows.sort(key=lambda r: r[0], reverse=self._sort_descending)
        self.servers_table.rows = [r[2] for r in rows]
        self.servers_table.update()

    def _on_hop_community_filter_change(self, e):
        selected = e.control.selected
        self._hop_selected_communities = selected if selected else []

    def _on_community_filter_change(self, e):
        selected = e.control.selected
        if selected:
            community_id = selected[0]
            self._current_community_filter = None if community_id == "all" else community_id
            self._apply_filter_and_sort()

    def _on_sort_players(self, e=None):
        self._sort_descending = not self._sort_descending
        self._apply_filter_and_sort()
        direction = "↓" if self._sort_descending else "↑"
        self.add_log(f"🔃 Sorted by players {direction}")

    def _start_server_hop(self):
        self._hop_running = True
        self.hop_status.value = "Running..."
        self.hop_status.color = "#4CAF50"
        self.hop_status.update()
        self._hop_task = asyncio.create_task(self._server_hop_loop())

    def _stop_server_hop(self):
        self._hop_running = False
        if hasattr(self, '_hop_task') and self._hop_task and not self._hop_task.done():
            self._hop_task.cancel()
            self.add_log("🛑 Server hop stopped")
        self.hop_status.value = "Stopped"
        self.hop_status.color = "#888888"
        self.hop_status.update()

    async def _server_hop_loop(self):
        while self._hop_running:
            try:
                servers = self._sorted_server_rows
                if not servers:
                    await asyncio.sleep(1)
                    continue

                filtered = list(servers)
            
                if hasattr(self, '_hop_selected_communities') and self._hop_selected_communities:
                    filtered = [s for s in filtered if s[3] in self._hop_selected_communities]
            
                if self.hop_if_players_cb.value:
                    try:
                        min_players = int(self.hop_players_count.value)
                    except:
                        min_players = 0
                    filtered = [s for s in filtered if s[0] > min_players]

                if self.hop_skip_full_cb.value:
                    filtered = [s for s in filtered if s[0] < s[1]]

                if not filtered:
                    self.hop_status.value = "No servers found"
                    self.hop_status.update()
                    await asyncio.sleep(1)
                    continue

                if self.hop_random_all_cb.value:
                    selected_clients = self.get_selected_clients()
                    if not selected_clients:
                        self.add_log("❌ No clients selected for hop")
                        await asyncio.sleep(1)
                        continue
            
                    try:
                        freq = int(self.hop_frequency.value)
                    except:
                        freq = 5000
            
                    await asyncio.sleep(freq / 1000.0)
            
                    for cid in selected_clients:
                        chosen = random.choice(filtered)
                        ip_port = chosen[4]
                        asyncio.create_task(self._hop_to_server(cid, ip_port))
            
                    self.hop_status.value = f"Running"
                    self.hop_status.update()
                else:
                    chosen = random.choice(filtered)
                    ip_port = chosen[4]
                    await self._hop_to_server(None, ip_port)
                    self.hop_status.value = f"Running (Last: {ip_port[:15]}...)"
                    self.hop_status.update()
            
                    try:
                        freq = int(self.hop_frequency.value)
                    except:
                        freq = 5000
                    await asyncio.sleep(freq / 1000.0)

            except Exception as e:
                self.add_log(f"❌ Server hop error: {e}")
                await asyncio.sleep(1)

    async def _hop_to_server(self, cid, ip_port):
        precommands = self.hop_precommands.value.strip()
        if precommands:
            for cmd in precommands.split(";"):
                cmd = cmd.strip()
                if cmd:
                    if cid is not None:
                        self.control_server.send_command([cid], cmd)
                    else:
                        self.send_action_command(cmd)

        if cid is not None:
            self.control_server.send_command([cid], f"connect {ip_port}")
        else:
            self.send_action_command(f"connect {ip_port}")

        self.add_log(f"🔗 Server hop: connect {ip_port}")

        say_text = self.hop_say.value.strip()
        if say_text:
            try:
                freq = int(self.hop_frequency.value)
            except:
                freq = 5000
            await asyncio.sleep(max(0, (freq - 500) / 1000.0))
            if cid is not None:
                self.control_server.send_command([cid], f"say {say_text}")
            else:
                self.send_action_command(f"say {say_text}")

def main(page: ft.Page):
    app = DMClientsApp(page)
    def on_close(e):
        app.control_server.stop()
        app.client_manager.stop_all()
        app.bridge_receiver.stop()
        os.system("taskkill /F /IM xray.exe 2>nul")
        os.system("taskkill /F /IM proxifyre.exe 2>nul")
        if getattr(app, 'optimal_proxies_proc', None):
            kill_process_tree(app.optimal_proxies_proc.pid)
        if getattr(app, 'ports_proxies_proc', None):
            kill_process_tree(app.ports_proxies_proc.pid)
        if getattr(app, 'proxifyre_proc', None):
            kill_process_tree(app.proxifyre_proc.pid)
        os._exit(0)
    page.on_close = on_close

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import winloop
            winloop.install()
            print("[OK] Winloop activated")
        except ImportError:
            print("[WARN] Winloop not installed")

    try:
        ft.run(main)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        os.system("taskkill /F /IM xray.exe 2>nul")
        os.system("taskkill /F /IM proxifyre.exe 2>nul")
        os.system("taskkill /F /IM HDDNet*.exe 2>nul")
        os.system("taskkill /F /IM flet.exe 2>nul")
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            if 'python' in proc.info['name'].lower():
                try:
                    proc.kill()
                except:
                    pass
        os._exit(0)