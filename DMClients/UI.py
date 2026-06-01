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
import multiprocessing
import struct
import queue as queue_module
from datetime import datetime
from typing import Dict, List, Optional

_global_names = []
_global_dictionary = []
_show_advanced_logs = False

_main_loop: asyncio.AbstractEventLoop | None = None

def _async_timer(delay: float, callback, *args, loop=None):
    async def _wait():
        try:
            await asyncio.sleep(delay)
            callback(*args)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    target_loop = loop or _main_loop
    if target_loop is None:
        return None

    try:
        if threading.current_thread() is threading.main_thread():
            try:
                running_loop = asyncio.get_running_loop()
                if running_loop is target_loop:
                    return running_loop.create_task(_wait())
            except RuntimeError:
                pass
        task_box = []
        def _create():
            try:
                task_box.append(target_loop.create_task(_wait()))
            except Exception:
                pass
        target_loop.call_soon_threadsafe(_create)
        return task_box[0] if task_box else None
    except Exception:
        return None

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

# ========== SERVER PROCESS (Bridge + Control in separate process) ==========

_MAX_CLIENT_QUEUE = 2000

def _server_process(control_port, bridge_port, cmd_queue, event_queue, shared_state):
    if sys.platform == "win32":
        try:
            import winloop
            winloop.install()
        except ImportError:
            pass
    asyncio.run(_server_process_async(control_port, bridge_port, cmd_queue, event_queue, shared_state))


async def _server_process_async(control_port, bridge_port, cmd_queue, event_queue, shared_state):
    control_clients = {}  # cid -> ControlProc
    control_client_ports = {}  # cid -> port
    control_writer_tasks = {}  # cid -> asyncio.Task
    bridge_clients = {}  # cidx -> BridgeProc
    bridge_client_ports = {}  # cidx -> port
    bridge_local_ids = {}  # cidx -> pid
    bridge_token_map = {}  # token -> cidx
    players = {}  # pid -> data
    server_info = {"name": "", "map": "", "gametype": "", "num_players": 0, "max_players": 0}
    _sync_dirty = [False]  # flag: True when state needs pushing

    next_control_id = [1]
    next_bridge_idx = [1]

    class ControlProc(asyncio.Protocol):
        def __init__(self):
            self.transport = None
            self.buffer = ""
            self.cid = None
            self.write_queue = asyncio.Queue(maxsize=_MAX_CLIENT_QUEUE)

        def connection_made(self, transport):
            nonlocal next_control_id
            self.transport = transport
            transport.set_write_buffer_limits(high=262144)
            client_port = transport.get_extra_info('peername')[1]
            self.cid = next_control_id[0]
            next_control_id[0] += 1
            control_clients[self.cid] = self
            control_client_ports[self.cid] = client_port
            control_writer_tasks[self.cid] = asyncio.create_task(self._writer_loop())
            _mark_dirty()
            try:
                event_queue.put_nowait(('log', f"[ControlServer] Client #{self.cid} connected (port {client_port})"))
            except Exception:
                pass

        def data_received(self, data):
            self.buffer += data.decode('utf-8', errors='replace')
            while '\n' in self.buffer:
                line, self.buffer = self.buffer.split('\n', 1)
                line = line.strip()
                if line.startswith("TOKEN "):
                    token = line[6:].strip()
                    try:
                        event_queue.put_nowait(('token_control', self.cid, token))
                    except Exception:
                        pass

        def connection_lost(self, exc):
            control_clients.pop(self.cid, None)
            control_client_ports.pop(self.cid, None)
            writer = control_writer_tasks.pop(self.cid, None)
            if writer and not writer.done():
                writer.cancel()
            _mark_dirty()
            try:
                event_queue.put_nowait(('log', f"[ControlServer] Client #{self.cid} disconnected"))
            except Exception:
                pass

        def enqueue_send(self, data: str):
            try:
                self.write_queue.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    event_queue.put_nowait(('log', f"[ControlServer] Client #{self.cid} write queue full — dropping"))
                except Exception:
                    pass
                if self.transport and not self.transport.is_closing():
                    self.transport.close()

        async def _writer_loop(self):
            try:
                while True:
                    data = await self.write_queue.get()
                    if self.transport and not self.transport.is_closing():
                        try:
                            self.transport.write((data + "\n").encode('utf-8'))
                        except Exception:
                            pass
                    else:
                        while not self.write_queue.empty():
                            try:
                                self.write_queue.get_nowait()
                            except Exception:
                                break
                        return
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    class BridgeProc(asyncio.Protocol):
        def __init__(self):
            self.transport = None
            self.buffer = ""
            self.cidx = None

        def connection_made(self, transport):
            self.transport = transport
            client_port = transport.get_extra_info('peername')[1]
            self.cidx = next_bridge_idx[0]
            next_bridge_idx[0] += 1
            bridge_clients[self.cidx] = self
            bridge_client_ports[self.cidx] = client_port
            _mark_dirty()
            try:
                event_queue.put_nowait(('log', f"[Bridge] Client #{self.cidx} connected (port {client_port})"))
            except Exception:
                pass

        def data_received(self, data):
            self.buffer += data.decode('utf-8', errors='replace')
            while '\n' in self.buffer:
                line, self.buffer = self.buffer.split('\n', 1)
                line = line.strip()
                if line:
                    _parse_bridge_line(line, self.cidx)

        def connection_lost(self, exc):
            bridge_clients.pop(self.cidx, None)
            bridge_client_ports.pop(self.cidx, None)
            bridge_local_ids.pop(self.cidx, None)
            for token, idx in list(bridge_token_map.items()):
                if idx == self.cidx:
                    del bridge_token_map[token]
                    break
            _mark_dirty()
            try:
                event_queue.put_nowait(('log', f"[Bridge] Client #{self.cidx} disconnected"))
            except Exception:
                pass

        def send(self, data: str):
            if self.transport and not self.transport.is_closing():
                self.transport.write((data + "\n").encode('utf-8'))

    def _parse_bridge_line(line: str, cidx: int):
        if line.startswith("TOKEN "):
            token = line[6:].strip()
            for t, idx in list(bridge_token_map.items()):
                if idx == cidx:
                    del bridge_token_map[t]
                    break
            bridge_token_map[token] = cidx
            _mark_dirty()
            try:
                event_queue.put_nowait(('token_bridge', token, cidx))
            except Exception:
                pass
            return

        if line.startswith("SERVER "):
            parts = line.split('"')
            if len(parts) >= 7:
                server_info["name"] = parts[1]
                server_info["map"] = parts[3]
                server_info["gametype"] = parts[5]
                numbers = parts[6].strip().split()
                if len(numbers) >= 2:
                    server_info["num_players"] = int(numbers[0])
                    server_info["max_players"] = int(numbers[1])
            players.clear()
            _mark_dirty()
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

            direction = jumped = hook_state = angle = attack_tick = target_x = target_y = 0
            if len(right_part) >= 15:
                direction = int(right_part[8])
                jumped = int(right_part[9])
                hook_state = int(right_part[10])
                angle = int(right_part[11])
                attack_tick = int(right_part[12])
                target_x = int(right_part[13])
                target_y = int(right_part[14])

            players[pid] = {
                'x': x, 'y': y, 'is_local': is_local, 'frozen': frozen,
                'name': name, 'weapon': weapon, 'health': health, 'team': team,
                'armor': armor, 'direction': direction, 'jumped': jumped,
                'hook_state': hook_state, 'angle': angle, 'attack_tick': attack_tick,
                'target_x': target_x, 'target_y': target_y,
            }
            if is_local:
                bridge_local_ids[cidx] = pid
            _mark_dirty()
        except Exception:
            pass

    def _mark_dirty():
        _sync_dirty[0] = True

    def _sync_shared_state():
        try:
            shared_state['control_clients'] = list(control_clients.keys())
            shared_state['control_client_ports'] = dict(control_client_ports)
            shared_state['bridge_clients'] = list(bridge_clients.keys())
            shared_state['bridge_client_ports'] = dict(bridge_client_ports)
            shared_state['bridge_local_ids'] = dict(bridge_local_ids)
            shared_state['bridge_token_map'] = dict(bridge_token_map)
            shared_state['players'] = dict(players)
            shared_state['server_info'] = dict(server_info)
        except Exception:
            pass

    async def _periodic_sync():
        while True:
            await asyncio.sleep(0.05)
            if _sync_dirty[0]:
                _sync_dirty[0] = False
                await asyncio.to_thread(_sync_shared_state)

    def _random_char() -> str:
        import random, string
        return random.choice(string.ascii_letters + string.digits + "._-")

    def _replace_placeholders(cmd: str, client_index: int) -> str:
        online = sorted(control_clients.keys())
        rank = online.index(client_index) + 1 if client_index in online else client_index
        cmd = cmd.replace("{i}", str(rank))

        while "{r}" in cmd:
            cmd = cmd.replace("{r}", _random_char(), 1)

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

    # --- Start servers ---
    loop = asyncio.get_running_loop()

    control_server = await loop.create_server(ControlProc, '127.0.0.1', control_port)
    bridge_server = await loop.create_server(BridgeProc, '127.0.0.1', bridge_port)

    try:
        event_queue.put_nowait(('log', f"[ServerProcess] Control on :{control_port}, Bridge on :{bridge_port}"))
    except Exception:
        pass

    # --- Command polling (non-blocking per-client dispatch) ---
    async def _poll_commands():
        while True:
            try:
                for _ in range(100):
                    try:
                        msg = cmd_queue.get_nowait()
                    except Exception:
                        break

                    if msg[0] == 'send':
                        _, cid_list, command = msg
                        for cid in cid_list:
                            proto = control_clients.get(cid)
                            if proto:
                                final_cmd = _replace_placeholders(command, cid)
                                proto.enqueue_send(final_cmd)
                    elif msg[0] == 'remove':
                        _, cid = msg
                        proto = control_clients.pop(cid, None)
                        control_client_ports.pop(cid, None)
                        writer = control_writer_tasks.pop(cid, None)
                        if writer and not writer.done():
                            writer.cancel()
                        if proto and proto.transport:
                            try:
                                proto.transport.close()
                            except Exception:
                                pass
                        _mark_dirty()
                    elif msg[0] == 'stop':
                        return
            except Exception:
                pass
            await asyncio.sleep(0.002)

    poll_task = asyncio.create_task(_poll_commands())
    sync_task = asyncio.create_task(_periodic_sync())

    try:
        await asyncio.gather(
            control_server.serve_forever(),
            bridge_server.serve_forever(),
            poll_task,
            sync_task,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        for task in control_writer_tasks.values():
            if not task.done():
                task.cancel()
        sync_task.cancel()
        control_server.close()
        bridge_server.close()
        await control_server.wait_closed()
        await bridge_server.wait_closed()


# ========== BRIDGE RECEIVER (proxy to server process) ==========
class BridgeReceiver:
    def __init__(self, host='127.0.0.1', port=5556):
        self.host = host
        self.port = port
        self.running = False
        self.lock = threading.Lock()
        self.log_callback = None
        self.token_callback = None
        self.app = None
        self._shared_state = None  # Set by ServerProcessManager

    def set_log_callback(self, callback):
        self.log_callback = callback

    def set_token_callback(self, callback):
        self.token_callback = callback

    def _log(self, msg: str):
        if self.log_callback:
            self.log_callback(f"[Bridge] {msg}")

    def start(self) -> bool:
        self.running = True
        return True

    def stop(self):
        self.running = False

    def _get_state(self, key, default=None):
        if self._shared_state is None:
            return default
        try:
            return self._shared_state.get(key, default)
        except Exception:
            return default

    @property
    def client_token(self) -> dict:
        return self._get_state('bridge_token_map', {})

    @property
    def client_ports(self) -> dict:
        return self._get_state('bridge_client_ports', {})

    @property
    def client_local_ids(self) -> dict:
        return self._get_state('bridge_local_ids', {})

    @property
    def players(self) -> dict:
        return self._get_state('players', {})

    def get_local_id(self, client_idx=None):
        local_ids = self._get_state('bridge_local_ids', {})
        if client_idx is not None:
            return local_ids.get(client_idx)
        for pid, data in self._get_state('players', {}).items():
            if data.get('is_local'):
                return pid
        return None

    def get_player_pos(self, player_id: int):
        data = self._get_state('players', {}).get(player_id)
        if data:
            return {'x': data['x'], 'y': data['y'], 'is_local': data['is_local'], 'frozen': data['frozen']}
        return None

    def get_player_state(self, player_id: int):
        data = self._get_state('players', {}).get(player_id)
        return data.copy() if data else None

    def get_all_players(self) -> Dict[int, dict]:
        result = self._get_state('players', {})
        return {pid: data.copy() for pid, data in result.items()} if result else {}

    def get_server_info(self) -> dict:
        return self._get_state('server_info', {
            'name': '', 'map': '', 'gametype': '', 'num_players': 0, 'max_players': 0
        })


# ========== CONTROL SERVER (proxy to server process) ==========
class ControlServer:
    def __init__(self, host='127.0.0.1', port=5555):
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.running = False
        self.log_callback = None
        self.token_callback = None
        self.app = None
        self._cmd_queue = None  # Set by ServerProcessManager
        self._shared_state = None  # Set by ServerProcessManager

    def set_log_callback(self, callback):
        self.log_callback = callback

    def set_token_callback(self, callback):
        self.token_callback = callback

    def _log(self, msg: str):
        if self.log_callback:
            self.log_callback(f"[ControlServer] {msg}")

    def start(self) -> bool:
        self.running = True
        return True

    def stop(self):
        self.running = False

    def random_char(self) -> str:
        import random, string
        return random.choice(string.ascii_letters + string.digits + "._-")

    def replace_placeholders(self, cmd: str, client_index: int) -> str:
        # Placeholder replacement is done in the server process now
        return cmd

    def send_command(self, client_ids: List[int], command: str) -> Dict[int, bool]:
        if self._cmd_queue is None:
            return {cid: False for cid in client_ids}
        
        online = self._get_state('control_clients', [])
        results = {}
        for cid in client_ids:
            if cid in online:
                results[cid] = True
            else:
                results[cid] = False
        
        try:
            self._cmd_queue.put_nowait(('send', list(client_ids), command))
        except Exception:
            return {cid: False for cid in client_ids}
        
        return results

    def get_online_clients(self) -> List[int]:
        return self._get_state('control_clients', [])

    def remove_client(self, client_id: int):
        if self._cmd_queue is not None:
            try:
                self._cmd_queue.put_nowait(('remove', client_id))
            except Exception:
                pass

    def check_alive(self, client_id: int) -> bool:
        return client_id in self._get_state('control_clients', [])

    def _get_state(self, key, default=None):
        if self._shared_state is None:
            return default
        try:
            return self._shared_state.get(key, default)
        except Exception:
            return default

    @property
    def clients(self) -> dict:
        return {cid: True for cid in self._get_state('control_clients', [])}

    @property
    def client_ports(self) -> dict:
        return self._get_state('control_client_ports', {})


# ========== SERVER PROCESS MANAGER ==========
class ServerProcessManager:
    def __init__(self, control_port=5555, bridge_port=5556):
        self.control_port = control_port
        self.bridge_port = bridge_port
        self._manager = None
        self._shared_state = None
        self._cmd_queue = None
        self._event_queue = None
        self._process = None
        self._event_thread = None
        self._running = False
        self.log_callback = None
        self.token_control_callback = None
        self.token_bridge_callback = None

    def set_callbacks(self, log_callback, token_control_callback, token_bridge_callback):
        self.log_callback = log_callback
        self.token_control_callback = token_control_callback
        self.token_bridge_callback = token_bridge_callback

    def start(self, control_server, bridge_receiver):
        self._manager = multiprocessing.Manager()
        self._shared_state = self._manager.dict()
        self._cmd_queue = self._manager.Queue()
        self._event_queue = self._manager.Queue()

        # Initialize shared state
        self._shared_state['control_clients'] = []
        self._shared_state['control_client_ports'] = {}
        self._shared_state['bridge_clients'] = []
        self._shared_state['bridge_client_ports'] = {}
        self._shared_state['bridge_local_ids'] = {}
        self._shared_state['bridge_token_map'] = {}
        self._shared_state['players'] = {}
        self._shared_state['server_info'] = {"name": "", "map": "", "gametype": "", "num_players": 0, "max_players": 0}

        self._process = multiprocessing.Process(
            target=_server_process,
            args=(self.control_port, self.bridge_port, self._cmd_queue, self._event_queue, self._shared_state),
            daemon=True,
        )
        self._process.start()
        self._running = True

        # Wire up proxy objects
        control_server._cmd_queue = self._cmd_queue
        control_server._shared_state = self._shared_state
        bridge_receiver._shared_state = self._shared_state

        # Start event polling thread
        self._event_thread = threading.Thread(target=self._poll_events, daemon=True)
        self._event_thread.start()

    def _poll_events(self):
        while self._running:
            try:
                for _ in range(50):  # Batch process up to 50 events
                    try:
                        event = self._event_queue.get_nowait()
                    except Exception:
                        break
                    
                    event_type = event[0]
                    if event_type == 'log' and self.log_callback:
                        self.log_callback(event[1])
                    elif event_type == 'token_control' and self.token_control_callback:
                        cid, token = event[1], event[2]
                        if _main_loop:
                            _main_loop.call_soon_threadsafe(self.token_control_callback, cid, token)
                    elif event_type == 'token_bridge' and self.token_bridge_callback:
                        token, cidx = event[1], event[2]
                        if _main_loop:
                            _main_loop.call_soon_threadsafe(self.token_bridge_callback, token, cidx)
            except Exception:
                pass
            time.sleep(0.01)  # 100 Hz polling

    def stop(self):
        self._running = False
        if self._cmd_queue is not None:
            try:
                self._cmd_queue.put_nowait(('stop',))
            except Exception:
                pass
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=3)
        if self._manager:
            try:
                self._manager.shutdown()
            except Exception:
                pass

    def pause(self, control_server, bridge_receiver):
        if not self._running:
            return
        self._running = False
        if self._cmd_queue is not None:
            try:
                self._cmd_queue.put_nowait(('stop',))
            except Exception:
                pass
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=3)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=2)
        self._process = None
        # Give OS time to fully release ports (TIME_WAIT on Windows)
        time.sleep(1.0)
        if self._manager:
            try:
                self._manager.shutdown()
            except Exception:
                pass
        self._manager = None
        self._shared_state = None
        self._cmd_queue = None
        self._event_queue = None
        control_server.running = False
        bridge_receiver.running = False

    def resume(self, control_server, bridge_receiver):
        if self._running:
            return
        self.start(control_server, bridge_receiver)
        control_server.running = True
        bridge_receiver.running = True

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

            self._apply_efficiency_mode(proc.pid)

            def reader():
                for line_bytes in iter(proc.stdout.readline, b''):
                    if line_bytes:
                        try:
                            line = line_bytes.decode('utf-8').rstrip()
                        except UnicodeDecodeError:
                            line = line_bytes.decode('cp1251', errors='replace').rstrip()
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

    def _apply_efficiency_mode(self, pid: int):
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
            PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
            ProcessPowerThrottling = 4
            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_SET_INFORMATION = 0x0200
            IDLE_PRIORITY_CLASS = 0x00000040

            class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
                _fields_ = [
                    ("Version", wintypes.ULONG),
                    ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            hProcess = kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_SET_INFORMATION,
                False,
                pid
            )
            if not hProcess:
                return

            kernel32.SetPriorityClass(hProcess, IDLE_PRIORITY_CLASS)

            state = PROCESS_POWER_THROTTLING_STATE()
            state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION
            state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
            state.StateMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED

            kernel32.SetProcessInformation(
                hProcess,
                ProcessPowerThrottling,
                ctypes.byref(state),
                ctypes.sizeof(state)
            )
            kernel32.CloseHandle(hProcess)
            if _show_advanced_logs:
                self._log(f"✅ Client #{self._get_client_id_by_pid(pid)} → Efficiency Mode + IDLE priority")
        except Exception as e:
            self._log(f"⚠️ Efficiency mode failed: {e}")

    def _get_client_id_by_pid(self, pid: int) -> str:
        with self.lock:
            for cid, proc in self.processes.items():
                if proc.pid == pid:
                    return str(cid)
        return "?"

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
            for conn in proc.net_connections(kind='tcp'):          # только TCP
                if conn.status != 'ESTABLISHED' or not conn.raddr:
                    continue
                if conn.raddr.port == 5555 and result['control_port'] is None:   # берём первое, не перезаписываем
                    result['control_port'] = conn.laddr.port
                elif conn.raddr.port == 5556 and result['bridge_port'] is None:
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
            self._pending_timers: Dict[int, asyncio.Task] = {}
            self._rule_threads: Dict[int, asyncio.Task] = {}
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
                timer = _async_timer((duration + 150) / 1000.0, lambda: self._on_macro_done(cid))
                self._pending_timers[cid] = timer

            elif ext == 'rule':
                try:
                    thread = asyncio.create_task(asyncio.to_thread(self._run_rule, cid))
                    self._rule_threads[cid] = thread
                except RuntimeError:
                    self._active_clients.discard(cid)

            self.app.add_log(f"▶️ Macro started for client #{cid}")

        def _stop_macros(self):
            self._running = False
            clients = list(self._active_clients)
            # Cancel all timers and rule tasks before clearing
            for cid in clients:
                timer = self._pending_timers.pop(cid, None)
                if timer and isinstance(timer, asyncio.Task) and not timer.done():
                    timer.cancel()
                rule_task = self._rule_threads.pop(cid, None)
                if rule_task and isinstance(rule_task, asyncio.Task) and not rule_task.done():
                    rule_task.cancel()
                self.app.control_server.send_command([cid], "c_macro_play 0")
            self._active_clients.clear()
            self._pending_timers.clear()
            self._rule_threads.clear()
            self._set_button_playing(False)
            self.app.add_log("⏹️ Macros stopped")

        def _cancel_client_macro(self, cid: int):
            timer = self._pending_timers.pop(cid, None)
            if timer and isinstance(timer, asyncio.Task) and not timer.done():
                timer.cancel()
            rule_task = self._rule_threads.pop(cid, None)
            if rule_task and isinstance(rule_task, asyncio.Task) and not rule_task.done():
                rule_task.cancel()
            self.app.control_server.send_command([cid], "c_macro_play 0")
            if self.dont_block_if_macros_cb and self.dont_block_if_macros_cb.value:
                if self.app.attack_enable_switch.value:
                    self.app.control_server.send_command([cid], "c_attack 1")

        def _on_macro_done(self, cid: int):
            # _on_macro_done is called from _async_timer callback which already
            # runs on the event loop, so we can call directly
            try:
                self._handle_client_macro_finished(cid)
            except Exception:
                pass

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
                # _async_timer is now thread-safe — works from worker threads
                timer = _async_timer((max_duration + 150) / 1000.0, lambda: self._on_macro_done(cid))
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
                'get_selected_control': lambda: self.app.get_selected_control_cids(),
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
            online = self.app.get_selected_control_cids()
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
                    timer = _async_timer(delay_ms / 1000.0, self._start_macro, cid)
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
                        selected_ddnet = self.app.get_selected_clients()

                        for ddnet_id in selected_ddnet:
                            cid = self.app.client_to_control.get(ddnet_id)
                            if cid is None or cid not in self.app.control_server.clients:
                                continue
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
                    ft.Text("Documentation: ", selectable=True, spans=[ft.TextSpan("https://github.com/lothiann/DMClients#macros--rules", url="https://github.com/lothiann/DMClients#macros--rules", style=ft.TextStyle(color="#A855F7", size=14))]),
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
            try:
                asyncio.create_task(asyncio.to_thread(self._run_code, code))
            except RuntimeError:
                self._running = False
                self.editor_status.value = "Error: no event loop"
                self.editor_status.update()

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
                    'get_selected_control': lambda: app.get_selected_control_cids(),
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
                    ft.Text("Documentation: ", selectable=True, spans=[ft.TextSpan("https://github.com/lothiann/DMClients#code-execute", url="https://github.com/lothiann/DMClients#code-execute", style=ft.TextStyle(color="#A855F7", size=14))]),
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

        self._loop = asyncio.get_running_loop()
        global _main_loop
        _main_loop = self._loop
        self._last_scroll_time = 0
        self._log_update_timer = 0
        self._auto_scroll = True
        self.MAX_LOG_LINES = 2000
        self.show_advanced_logs = False

        self.control_server = ControlServer()
        self.client_manager = HDDNetClientManager(self.add_log)
        self.control_server.set_log_callback(self.add_log)

        self.bridge_receiver = BridgeReceiver()
        self.bridge_receiver.set_log_callback(self.add_log)

        # Server Process Manager — runs Bridge + Control in a separate process
        self._server_mgr = ServerProcessManager()

        self.control_server.start()
        self.bridge_receiver.start()

        self.bridge_receiver.app = self
        self.control_server.app = self

        self.control_to_bridge: Dict[int, int] = {}
        self.client_to_control: Dict[int, int] = {}
        self._pending_control_tokens: Dict[str, int] = {}
        self._pending_bridge_tokens: Dict[str, int] = {}

        def on_control_token(cid, token):
            if token in self.bridge_receiver.client_token:
                bridge_cidx = self.bridge_receiver.client_token[token]
                self.control_to_bridge[cid] = bridge_cidx
                if _show_advanced_logs:
                    self.add_log(f"✅ Synced: Control #{cid} ↔ Bridge #{bridge_cidx}")
                self.sync_clients_by_pid()
                _async_timer(2.0, self.sync_clients_by_pid)
            else:
                self._pending_control_tokens[token] = cid

        def on_bridge_token(token, bridge_cidx):
            if token in self._pending_control_tokens:
                cid = self._pending_control_tokens.pop(token)
                self.control_to_bridge[cid] = bridge_cidx
                if _show_advanced_logs:
                    self.add_log(f"✅ Synced: Control #{cid} ↔ Bridge #{bridge_cidx}")
                self.sync_clients_by_pid()
                _async_timer(2.0, self.sync_clients_by_pid)
            else:
                self._pending_bridge_tokens[token] = bridge_cidx

        self.control_server.set_token_callback(on_control_token)
        self.bridge_receiver.set_token_callback(on_bridge_token)

        # Start server process (after token callbacks are set)
        self._server_mgr.set_callbacks(self.add_log, on_control_token, on_bridge_token)
        self._server_mgr.start(self.control_server, self.bridge_receiver)

        self.sync_clients_by_pid()

        self._detect_config()

        self.send_checkboxes: List[ft.Checkbox] = []
        self.logs_checkboxes: List[ft.Checkbox] = []
        self.header_logs_cb = None
        self.header_cmd_cb = None
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
        self._config_timer: Optional[asyncio.Task] = None

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
        async def _do():
            try:
                lines = self._log_text.value.split('\n') if self._log_text.value else []
                lines.append(text)
                if len(lines) > self.MAX_LOG_LINES:
                    lines = lines[-self.MAX_LOG_LINES:]

                self._log_text.value = '\n'.join(lines)

                if not self.console_container.visible:
                    return

                self.page.update()

                if self._auto_scroll:
                    await asyncio.sleep(0.02)
                    try:
                        await self.log_box.scroll_to(offset=-1, duration=0)
                    except Exception:
                        pass
            except Exception:
                pass  # Prevent log errors from crashing

        if self._loop is None or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(_do()))
        except RuntimeError:
            pass

    def _on_scroll(self, e: ft.OnScrollEvent):
        if e.pixels >= e.max_scroll_extent - 30:
            self._auto_scroll = True
        else:
            self._auto_scroll = False

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

    def get_selected_control_cids(self) -> List[int]:
        result = []
        for ddnet_id in self.get_selected_clients():
            cid = self.client_to_control.get(ddnet_id)
            if cid is not None and cid in self.control_server.clients:
                result.append(cid)
        return result

    def send_command_to_clients(self, command: str):
        real_cids = self.get_selected_control_cids()
    
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
        if not hasattr(self, 'control_to_bridge'):
            return
        online_cids = set(self.control_server.clients.keys())
        for cid in list(self.control_to_bridge):
            if cid not in online_cids:
                del self.control_to_bridge[cid]
        for ddnet_id in list(self.client_to_control):
            if self.client_to_control[ddnet_id] not in online_cids:
                del self.client_to_control[ddnet_id]

        bridge_by_port = {}
        with self.bridge_receiver.lock:
            for cidx, port in self.bridge_receiver.client_ports.items():
                bridge_by_port[port] = cidx

        control_by_port = {}
        with self.control_server.lock:
            for cid, port in self.control_server.client_ports.items():
                control_by_port[port] = cid

        clients_info = self.client_manager.get_all_clients_connection_info()

        synced = 0
        for client_id, info in clients_info.items():
            control_port = info.get('control_port')
            bridge_port  = info.get('bridge_port')

            cid         = control_by_port.get(control_port) if control_port else None
            bridge_cidx = bridge_by_port.get(bridge_port)   if bridge_port  else None

            if cid is None or bridge_cidx is None:
                continue

            is_new = client_id not in self.client_to_control
            self.client_to_control[client_id] = cid

            if cid not in self.control_to_bridge:
                self.control_to_bridge[cid] = bridge_cidx

            if is_new:
                synced += 1
                if _show_advanced_logs:
                    self.add_log(f"🔗 Synced: Client #{client_id} → Control #{cid} ↔ Bridge #{bridge_cidx}")

        return synced

    def _start_monitoring(self):
        pass  # The old threaded monitor_loop has been removed to avoid race conditions.
                 # The async monitor_loop (below) handles all monitoring.

    async def monitor_loop(self):
        cpu_count = psutil.cpu_count(logical=True)
        warned_uss = False
        prev_cpu = {}
        prev_time = {}
        while True:
            try:
                current_load = await asyncio.to_thread(psutil.cpu_percent, interval=0.1)
                interval = 5 if current_load > 70 else 3
            except Exception:
                interval = 3
            await asyncio.sleep(interval)

            if not self.clients_container.visible:
                # Still check button states even when tab is hidden
                self._update_connect_buttons()
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

            # Update connect/disconnect buttons
            self._update_connect_buttons()

            self.update_clients_stats()
            self.page.update()

    def _update_connect_buttons(self):
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
        if need_update:
            self.sync_clients_by_pid()

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

        self._generate_proxifyre_config(new_count, cpp)
        self.NUM_CLIENTS = new_count
        self.clients_per_proxy = cpp
        self._rebuild_clients_table()
        self.add_log(f"🔄 Applied: {new_count} clients, {cpp} clients per proxy")

    def _detect_config(self):
        base_dir = os.path.join(os.path.dirname(__file__), "DDNets-19.9-win64")
        files = glob.glob(os.path.join(base_dir, "HDDNet*.exe"))
    
        max_id = 0
        for f in files:
            match = re.search(r'HDDNet(\d+)\.exe$', os.path.basename(f))
            if match:
                max_id = max(max_id, int(match.group(1)))
        self.NUM_CLIENTS = max_id if max_id > 0 else 28
    
        config_path = os.path.join(os.path.dirname(__file__), "ProxiFyre", "app-config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                proxies = config.get("proxies", [])
                if proxies and "appNames" in proxies[0]:
                    app_count = len(proxies[0]["appNames"])
                    self.clients_per_proxy = app_count // 2
                else:
                    self.clients_per_proxy = 2
            except:
                self.clients_per_proxy = 2
        else:
            self.clients_per_proxy = 2

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

    def _on_header_logs_changed(self, e):
        new_val = True if e.control.value is not False else False
        for i, cb in enumerate(self.logs_checkboxes):
            cb.value = new_val
            cb.update()
            self.client_manager.set_show_logs(i + 1, new_val)
        self.header_logs_cb.value = new_val
        self.header_logs_cb.update()

    def _on_header_cmd_changed(self, e):
        new_val = True if e.control.value is not False else False
        for cb in self.cmd_checkboxes:
            cb.value = new_val
            cb.update()
        self.header_cmd_cb.value = new_val
        self.header_cmd_cb.update()

    def _sync_header_cmd(self):
        if not self.header_cmd_cb or not self.cmd_checkboxes:
            return
        values = [cb.value for cb in self.cmd_checkboxes]
        if all(values):
            self.header_cmd_cb.value = True
        else:
            self.header_cmd_cb.value = False
        try:
            self.header_cmd_cb.update()
        except Exception:
            pass

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

        self._log_text = ft.Text("", size=14, selectable=True)
        self.log_box = ft.ListView(
            controls=[self._log_text],
            expand=True,
            auto_scroll=False,
            on_scroll=self._on_scroll,
        )

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
        self.header_logs_cb = ft.Checkbox(
            label="Show logs", 
            value=False, 
            on_change=self._on_header_logs_changed, 
            label_style=ft.TextStyle(weight="bold")
        )
        self.header_cmd_cb = ft.Checkbox(
            label="Send commands", 
            value=True, 
            on_change=self._on_header_cmd_changed, 
            label_style=ft.TextStyle(weight="bold")
        )

        columns = [
            ft.DataColumn(ft.Text("Client", weight="bold")),
            ft.DataColumn(ft.Text("MEM (MB)", weight="bold")),
            ft.DataColumn(ft.Text("CPU (%)", weight="bold")),
            ft.DataColumn(self.header_cmd_cb),
            ft.DataColumn(self.header_logs_cb),
            ft.DataColumn(ft.Text("Action", weight="bold")),
        ]
        
        rows = []
        self.send_checkboxes.clear()
        self.logs_checkboxes.clear()
        self.connect_buttons.clear()
        self.mem_texts.clear()
        self.cpu_texts.clear()

        for i in range(1, self.NUM_CLIENTS + 1):
            send_cb = ft.Checkbox(value=True, on_change=lambda e: self._sync_header_cmd())
            logs_cb = ft.Checkbox(value=False, on_change=lambda e, cid=i: (self.client_manager.set_show_logs(cid, e.control.value), self._sync_header_logs()))
            
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

    def _on_header_logs_changed(self, e):
        if not self.logs_checkboxes:
            return
        all_on = all(cb.value for cb in self.logs_checkboxes)
        new_val = not all_on
        
        for i, cb in enumerate(self.logs_checkboxes):
            cb.value = new_val
            self.client_manager.set_show_logs(i + 1, new_val)
            cb.update()
            
        self.header_logs_cb.value = new_val
        self.header_logs_cb.update()

    def _on_header_cmd_changed(self, e):
        if not self.send_checkboxes:
            return
        all_on = all(cb.value for cb in self.send_checkboxes)
        new_val = not all_on
        
        for cb in self.send_checkboxes:
            cb.value = new_val
            cb.update()
            
        self.header_cmd_cb.value = new_val
        self.header_cmd_cb.update()

    def _sync_header_logs(self):
        if not self.header_logs_cb or not self.logs_checkboxes:
            return
        self.header_logs_cb.value = all(cb.value for cb in self.logs_checkboxes)
        self.header_logs_cb.update()

    def _sync_header_cmd(self):
        if not self.header_cmd_cb or not self.send_checkboxes:
            return
        self.header_cmd_cb.value = all(cb.value for cb in self.send_checkboxes)
        self.header_cmd_cb.update()

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
            ft.DataColumn(ft.Text("Target", weight="bold")),
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
        player_name_field = ft.TextField(label="Player name", hint_text="Enter name", expand=True, tooltip="player_name ...",
                                         bgcolor="#1e1e24", border_color="#33334d", value="Bot {i}")
        player_name_field.on_submit = self.on_player_name_submit
        player_name_send = ft.FilledButton("Set name", icon=ft.Icons.PERSON,
                                           on_click=self.on_player_name_submit,
                                           style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        player_clan_field = ft.TextField(label="Player clan", hint_text="Enter clan", expand=True, tooltip="player_clan ...",
                                         bgcolor="#1e1e24", border_color="#33334d", value="DMClients")
        player_clan_field.on_submit = self.on_player_clan_submit
        player_clan_send = ft.FilledButton("Set clan", icon=ft.Icons.GROUP,
                                           on_click=self.on_player_clan_submit,
                                           style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        player_skin_field = ft.TextField(label="Player skin", hint_text="skin ID", expand=True, tooltip="player_skin ...",
                                         bgcolor="#1e1e24", border_color="#33334d", value="itsabot")
        player_skin_field.on_submit = self.on_player_skin_submit
        player_skin_send = ft.FilledButton("Set skin", icon=ft.Icons.FACE,
                                           on_click=self.on_player_skin_submit,
                                           style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        dummy_name_field = ft.TextField(label="Dummy name", hint_text="Enter dummy name", expand=True, tooltip="dummy_name ...",
                                        bgcolor="#1e1e24", border_color="#33334d")
        dummy_name_field.on_submit = self.on_dummy_name_submit
        dummy_name_send = ft.FilledButton("Set dummy name", icon=ft.Icons.SMART_TOY,
                                          on_click=self.on_dummy_name_submit,
                                          style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        dummy_clan_field = ft.TextField(label="Dummy clan", hint_text="Enter dummy clan", expand=True, tooltip="dummy_clan ...",
                                        bgcolor="#1e1e24", border_color="#33334d")
        dummy_clan_field.on_submit = self.on_dummy_clan_submit
        dummy_clan_send = ft.FilledButton("Set dummy clan", icon=ft.Icons.GROUP,
                                          on_click=self.on_dummy_clan_submit,
                                          style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        dummy_skin_field = ft.TextField(label="Dummy skin", hint_text="skin ID", expand=True, tooltip="dummy_skin ...",
                                        bgcolor="#1e1e24", border_color="#33334d")
        dummy_skin_field.on_submit = self.on_dummy_skin_submit
        dummy_skin_send = ft.FilledButton("Set skin", icon=ft.Icons.FACE,
                                          on_click=self.on_dummy_skin_submit,
                                          style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))

        left_container = ft.Container(
            content=ft.Column([
                ft.Row([player_name_field, player_name_send], spacing=10),
                ft.Row([player_clan_field, player_clan_send], spacing=10),
                ft.Row([player_skin_field, player_skin_send], spacing=10),
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10, expand=True
        )
        right_container = ft.Container(
            content=ft.Column([
                ft.Row([dummy_name_field, dummy_name_send], spacing=10),
                ft.Row([dummy_clan_field, dummy_clan_send], spacing=10),
                ft.Row([dummy_skin_field, dummy_skin_send], spacing=10),
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10, expand=True
        )

        connect_server_field = ft.TextField(label="Connect to", hint_text="server:port", expand=True,
                                            bgcolor="#1e1e24", border_color="#33334d", value="localhost:8303")
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

        self.cron_command = ft.TextField(label="Spam command", hint_text="command to spam", expand=True, bgcolor="#1e1e24", border_color="#33334d", value="say {c}{c}{c} The best DDNet bot utility -> t.me/DMClients {c}{c}{c}")
        self.cron_delay = ft.TextField(label="Interval (ms)", value="5000", width=120, bgcolor="#1e1e24", border_color="#33334d", tooltip="For command \"say ...\" recommended 5000 ms")
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

        self.random_aim_checkbox = ft.Checkbox(label="Random aim", value=False, tooltip="Generates random crosshair coordinates every N ms", on_change=self.on_random_aim_toggle)
        self.random_aim_interval = ft.TextField(label="Interval (ms)", value="100", width=120,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.random_for_all_checkbox = ft.Checkbox(label="Random for all", value=False, tooltip="Each bot gets its own random coordinates (works on the client side)", on_change=self.on_random_for_all_change)

        self.attack_enable_switch = ft.Switch(value=False, on_change=self.on_attack_toggle)
        self.main_id_field = ft.TextField(label="Main ID", value="", tooltip="Which player should bots follow? (team)", width=100,
                                          bgcolor="#1e1e24", border_color="#33334d")
        self.attack_target_field = ft.TextField(label="Target IDs", value="", tooltip="Which players should bots attack? (wars)", width=250,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.auto_aim_cb = ft.Checkbox(label="Auto aim", tooltip="Enables aiming", value=True)
        self.hook_target_cb = ft.Checkbox(label="Hook", tooltip="Enables hooking", value=True)
        self.fire_target_cb = ft.Checkbox(label="Fire", tooltip="Enables firing", value=True)
        self.fire_distance_field = ft.TextField(label="Fire dist", tooltip="Radius within which bots can fire", value="65", width=100,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.hook_distance_field = ft.TextField(label="Hook dist", tooltip="Radius within which bots can hook", value="400", width=100,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.target_distance_field = ft.TextField(label="Target dist", value="300", tooltip="Range to attack a target (war)", width=100,
                                                  bgcolor="#1e1e24", border_color="#33334d")
        self.hook_delay_field = ft.TextField(label="Hook delay (ms)", value="1000", width=100,
                                             bgcolor="#1e1e24", border_color="#33334d")
        self.rescue_frozen_cb = ft.Checkbox(label="Rescue frozen", tooltip="Bots can rescue their teammates and the main within N radius", value=True)
        self.rescue_radius_field = ft.TextField(label="Rescue radius", value="500", width=120,
                                                bgcolor="#1e1e24", border_color="#33334d")
        self.kill_on_freeze_cb = ft.Checkbox(label="Kill on freeze", tooltip="Auto respawn if frozen", value=False)
        self.attack_main_cb = ft.Checkbox(label="Attack main", tooltip="Bots attack the main (for fun)", value=False)
        self.move_cb = ft.Checkbox(label="Move", tooltip="Enables moving (left, right, jump)", value=True)
        self.stand_cb = ft.Checkbox(label="Stand", tooltip="Don't move if already within N radius of the main or target", value=True)
        self.pathfinder_cb = ft.Checkbox(label="Pathfinder", value=True, on_change=self.on_pathfinder_change, tooltip="Using Pathfinder")
        self.rescue_all_cb = ft.Checkbox(label="Rescue all", tooltip="Bots can rescue everyone except targets (wars)", value=False, on_change=self.on_rescue_all_change)
        self.smart_detect_cb = ft.Checkbox(label="Smart Detect", tooltip="Find frozen players without line of sight", value=True)
        self.smart_rescue_cb = ft.Checkbox(label="Smart Rescue", tooltip="If someone frozen in rescue radius - find gradient to them via pathfinder", value=True)
        self.all_target_cb = ft.Checkbox(label="All target", value=False, on_change=self.on_all_target_change)

        self.auto_hammer_cb = ft.Checkbox(label="Auto hammer", tooltip="Automatically switches to hammer when attacking", value=True)
        self.stand_on_x_cb = ft.Checkbox(label="Stand on X only [Experimental]", value=False, on_change=self.on_stand_on_x_change)

        self.main_dist_field = ft.TextField(label="Main dist", value="inf", tooltip="Radius within which bots go to main (inf = unlimited)", width=100,
                                            bgcolor="#1e1e24", border_color="#33334d")
        self.stand_dist_field = ft.TextField(label="Stand dist", value="64", tooltip="Don't move if already within N radius of target/main", width=100,
                                             bgcolor="#1e1e24", border_color="#33334d")
        self.rescue_ids_field = ft.TextField(label="Rescue IDs", value="", tooltip="Specific IDs to rescue (or unrescue if Rescue all is on)", width=250,
                                              bgcolor="#1e1e24", border_color="#33334d")
        self.target_coords_field = ft.TextField(label="Target Coords", value="", width=400,
                                                 hint_text="x1,y1-x2,y2; x3,y3-x4,y4",
                                                 tooltip="Auto-target players in these zones (x1,y1-x2,y2; ...)",
                                                 bgcolor="#1e1e24", border_color="#33334d")

        self.copy_id_field = ft.TextField(label="Copy from ID", width=100, value="",
                                          bgcolor="#1e1e24", border_color="#33334d")
        self.copy_moves_cb = ft.Checkbox(label="Copy moves", value=False, on_change=self.on_copy_moves_checkbox_change)
        self.delay_field = ft.TextField(label="Delay (ms)", value="0", width=100,
                                        bgcolor="#1e1e24", border_color="#33334d")
        self.delay_checkbox = ft.Checkbox(label="Enable client delay [Experimental]", value=False)

        for control in (self.main_id_field, self.attack_target_field,
                        self.auto_aim_cb, self.hook_target_cb, self.fire_target_cb, self.move_cb, self.stand_cb, self.pathfinder_cb,
                        self.attack_main_cb, self.kill_on_freeze_cb, self.auto_hammer_cb, self.stand_on_x_cb,
                        self.fire_distance_field, self.hook_distance_field, self.target_distance_field, self.hook_delay_field, self.main_dist_field, self.stand_dist_field,
                        self.rescue_frozen_cb, self.rescue_radius_field, self.smart_detect_cb, self.smart_rescue_cb, self.rescue_ids_field,
                        self.target_coords_field):
            control.on_change = lambda e: self._schedule_attack_config_update()
        self.delay_field.on_change = lambda e: self._send_client_delay()
        self.delay_checkbox.on_change = lambda e: self._send_client_delay()

        attack_container = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("Enable:", size=14), self.attack_enable_switch,
                    self.main_id_field, self.attack_target_field,
                ], spacing=10),
                ft.Row([self.auto_aim_cb, self.hook_target_cb, self.fire_target_cb, self.move_cb, self.stand_cb, self.pathfinder_cb], spacing=15),
                ft.Row([self.attack_main_cb, self.kill_on_freeze_cb, self.all_target_cb, self.auto_hammer_cb, self.stand_on_x_cb], spacing=15),
                ft.Row([self.fire_distance_field, self.hook_distance_field, self.target_distance_field, self.hook_delay_field, self.main_dist_field, self.stand_dist_field], spacing=10),
                ft.Row([self.rescue_frozen_cb, self.rescue_radius_field, self.rescue_all_cb, self.smart_detect_cb, self.smart_rescue_cb, self.rescue_ids_field], spacing=10),
                ft.Row([self.target_coords_field], spacing=10),
            ], spacing=10),
            padding=10, bgcolor="#1a1a24", border_radius=10
        )

        self.simulate_players_cb = ft.Checkbox(label="Simulate Players", value=True, tooltip="A* treats players as walls; OFF = IntersectCharacter jump bypass")
        self.pathfinder_rays_slider = ft.Slider(min=12, max=90, value=24, expand=True,
                                                on_change=self.on_pathfinder_rays_change, tooltip="Number of rays in pathfinder raycast")
        self.pathfinder_rays_dist_slider = ft.Slider(min=1, max=128, value=6, expand=True,
                                                      on_change=self.on_pathfinder_rays_dist_change, tooltip="Max raycast distance for pathfinder")
        self.pathfinder_snap_cb = ft.Checkbox(label="Fix Snap", value=False, on_change=self.on_pathfinder_snap_change, tooltip="Slightly changes the bot's behavior when it needs to use a jump")
        self.pathfinder_sps_cb = ft.Checkbox(label="SPS", value=True, tooltip="0 = Players as walls (default), 1 = Players as pushable obstacles", on_change=self.on_pathfinder_sps_change)
        self.pf_hook_cb = ft.Checkbox(label="Pf Hook [Experimental]", value=False, tooltip="Hook onto hookable blocks while pathfinding (WARNING: EXPERIMENTAL)")
        self.avoid_freeze_cb = ft.Checkbox(label="Avoid Freeze", value=True, tooltip="Repel from nearby freeze tiles")
        self.pathfinder_rays_value = ft.Text("24", size=14)
        self.pathfinder_rays_dist_value = ft.Text("6", size=14)
        self.pathfinder_go_x = ft.TextField(label="X", expand=True, bgcolor="#1e1e24", border_color="#33334d")
        self.pathfinder_go_y = ft.TextField(label="Y", expand=True, bgcolor="#1e1e24", border_color="#33334d")
        self.pathfinder_go_switch = ft.Switch(label="Go", value=False, on_change=self.on_pathfinder_go_toggle)

        pathfinder_go_container = ft.Container(
            content=ft.Column([
                ft.Row([
                    self.simulate_players_cb, 
                    self.pathfinder_snap_cb, 
                    self.pathfinder_sps_cb, 
                    self.avoid_freeze_cb, 
                    self.pf_hook_cb,
                ], spacing=10),
                ft.Row([ft.Text("Rays:", size=14), self.pathfinder_rays_slider, self.pathfinder_rays_value, ft.Text("Dist:", size=14), self.pathfinder_rays_dist_slider, self.pathfinder_rays_dist_value], spacing=10),
                ft.Row([
                    self.pathfinder_go_x,
                    self.pathfinder_go_y,
                    self.pathfinder_go_switch,
                ], spacing=10),
            ], spacing=5),
            padding=10, bgcolor="#1a1a24"
        )

        macros_section = self.macro_mgr.build_ui()

        self.input_fields = {
            "player_name": player_name_field,
            "player_clan": player_clan_field,
            "dummy_name": dummy_name_field,
            "dummy_clan": dummy_clan_field,
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
            ft.Text("Input controls", size=16, weight="bold", tooltip="Manual bot input: movement, weapon, kill, copy moves"),
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
            ft.Text("Aim", size=16, weight="bold", tooltip="Crosshair position control and random aim settings"),
            ft.Container(
                content=ft.Column([
                    ft.Row([ft.Text("X:", size=14), self.aim_x_slider, self.aim_x_value], spacing=10),
                    ft.Row([ft.Text("Y:", size=14), self.aim_y_slider, self.aim_y_value], spacing=10),
                    ft.Row([self.random_aim_checkbox, self.random_aim_interval, self.random_for_all_checkbox], spacing=10),
                ], spacing=10),
                padding=10, bgcolor="#1a1a24", border_radius=10
            ),
            ft.Text("Block", size=16, weight="bold", tooltip="Bot behavior: follow main, attack targets, rescue frozen teammates, auto-aim/hook/fire"),
            attack_container,
            ft.Text("Pathfinder [Experimental]", size=16, weight="bold", tooltip="A* pathfinding to navigate around walls"),
            pathfinder_go_container,
            ft.Text("Macros", size=16, weight="bold", tooltip="Record and playback input macros (.inp) or execute rule scripts (.rule) on clients"),
            macros_section,
            ft.Text("Code Execute", size=16, weight="bold", tooltip="Run custom Python code with access to app state, send commands, and control clients dynamically"),
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
        self.advanced_logs_cb = ft.Checkbox(
            label="Advanced logs",
            value=False,
            on_change=lambda e: globals().__setitem__('_show_advanced_logs', e.control.value)
        )

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
        self.generate_timeout_code_btn = ft.FilledButton("Generate timeout code", icon=ft.Icons.KEY, on_click=self.on_generate_timeout_code, style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
        self.num_clients_field = ft.TextField(label="Clients", value=str(self.NUM_CLIENTS), width=100,
                                              bgcolor="#1e1e24", border_color="#33334d")
        self.clients_per_proxy_field = ft.TextField(label="Clients per proxy", value=str(self.clients_per_proxy), width=120,
                                                    bgcolor="#1e1e24", border_color="#33334d")
        self.apply_clients_btn = ft.FilledButton("Apply", icon=ft.Icons.CHECK, on_click=self._apply_client_count,
                                                 style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
        self.spare_proxies_switch = ft.Switch(value=False, label="Use spare proxies")
        self.spare_count_field = ft.TextField(label="Spare count", value="5", width=80,
                                              bgcolor="#1e1e24", border_color="#33334d")
        self.target_server_field = ft.TextField(label="Target server", value="45.141.57.22:8390", width=200,
                                                bgcolor="#1e1e24", border_color="#33334d", tooltip="The server on which to check the proxy")
        self.timeout_field = ft.TextField(label="Timeout (ms)", value="5000", width=120,
                                          bgcolor="#1e1e24", border_color="#33334d", tooltip="Ping/IP check timeout in ms")

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
                        self.timeout_reconnect_switch,
                        self.generate_timeout_code_btn], spacing=10),
                ft.Row([proxy_logs_cb, proxifyre_logs_cb, self.advanced_logs_cb], spacing=20),
                ft.Row([
                    ft.Text("Set client count:", size=14),
                    self.num_clients_field, self.clients_per_proxy_field, self.apply_clients_btn,
                ], spacing=10),
                ft.Row([self.spare_proxies_switch, self.spare_count_field, self.target_server_field, self.timeout_field], spacing=10),
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
                ft.Text("Github: ", selectable=True, spans=[ft.TextSpan("https://github.com/lothiann/DMClients", url="https://github.com/lothiann/DMClients", style=ft.TextStyle(color="#A855F7", size=14))]),
                ft.Text("Telegram: ", selectable=True, spans=[ft.TextSpan("https://t.me/DMClients", url="https://t.me/DMClients", style=ft.TextStyle(color="#A855F7", size=14))]),
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
                ft.DataColumn(ft.Text("Ping", weight="bold")),
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
        ping_btn = ft.FilledButton("Ping proxies", icon=ft.Icons.SPEED,
                                    on_click=self._ping_proxies,
                                    style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
        top_bar = ft.Row([refresh_btn, ping_btn, self.spare_label], spacing=20, alignment=ft.MainAxisAlignment.START)
        self._refresh_proxies_table()
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
        self._load_spare_proxies()
        rows = []
        json_path = os.path.join(os.path.dirname(__file__), "Settings", "proxies.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item in data:
                    port = item["port"]
                    key = item["key"]
                    key_preview = key.split("#")[0][:50]
                    replace_btn = ft.FilledButton("Replace", icon=ft.Icons.REFRESH,
                                                  on_click=lambda e, p=port: self._replace_proxy(p), width=200,
                                                  style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
                    rows.append(ft.DataRow(cells=[
                        ft.DataCell(ft.Text(str(port))),
                        ft.DataCell(ft.Text(key_preview)),
                        ft.DataCell(replace_btn),
                        ft.DataCell(ft.Text("—")),
                    ]))
            except Exception as e:
                if hasattr(self, '_log_text'):
                    self.add_log(f"❌ Error updating proxy table: {e}")
        self.proxy_table.rows = rows
        if hasattr(self, 'spare_label'):
            self.spare_label.value = f"Spare proxies: {len(self.spare_proxies)}"
        try:
            self.proxy_table.update()
            if hasattr(self, 'spare_label'):
                self.spare_label.update()
        except Exception:
            pass

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

    def on_player_clan_submit(self, e):
        clan = self.input_fields.get("player_clan")
        if clan and clan.value.strip():
            self.send_action_command(f"player_clan {clan.value.strip()}")

    def on_dummy_clan_submit(self, e):
        clan = self.input_fields.get("dummy_clan")
        if clan and clan.value.strip():
            self.send_action_command(f"dummy_clan {clan.value.strip()}")

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
                try:
                    self._cron_task = asyncio.create_task(self._cron_loop())
                except RuntimeError:
                    pass
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
                try:
                    self.random_aim_task = asyncio.create_task(self.random_aim_loop())
                except RuntimeError:
                    pass
        else:
            if self.random_aim_task and not self.random_aim_task.done():
                self.random_aim_task.cancel()
                self.random_aim_task = None
                for cid in self.get_selected_control_cids():
                    self.control_server.send_command([cid], "c_random_aim 0")

    def on_random_for_all_change(self, e):
        if not self.random_for_all_checkbox.value:
            for cid in self.get_selected_control_cids():
                self.control_server.send_command([cid], "c_random_aim 0")

    async def random_aim_loop(self):
        while self.random_aim_checkbox.value:
            try:
                interval_ms = int(self.random_aim_interval.value.strip() or 100)
            except ValueError:
                interval_ms = 100

            if self.random_for_all_checkbox.value:
                for cid in self.get_selected_control_cids():
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
        for ddnet_id in selected:
            control_cid = self.client_to_control.get(ddnet_id)
            bridge_cidx = self.control_to_bridge.get(control_cid) if control_cid else None
            lid = self.bridge_receiver.get_local_id(bridge_cidx) if bridge_cidx is not None else None
            if lid is not None and lid != main_id_int:
                bot_ids.add(lid)
        result = ','.join(str(pid) for pid in bot_ids)
        return result if result else "-1"

    def _send_attack_config(self):
        main_id = self.main_id_field.value.strip()
        targets_str = self.attack_target_field.value.strip()
        bots_str = self._get_auto_bots_ids()
        all_target = self.all_target_cb.value

        if not main_id:
            main_id = "-1"
        if not targets_str:
            targets_str = "-1"

        zone_ids = self._compute_zone_target_ids()
        if zone_ids:
            manual_ids = set()
            if targets_str != "-1":
                for part in targets_str.split(','):
                    part = part.strip()
                    if part:
                        try:
                            manual_ids.add(int(part))
                        except ValueError:
                            pass
            merged = manual_ids | zone_ids
            targets_str = ','.join(str(pid) for pid in sorted(merged))

        auto_aim      = 1 if self.auto_aim_cb.value else 0
        auto_fire     = 1 if self.fire_target_cb.value else 0
        auto_hook     = 1 if self.hook_target_cb.value else 0
        move          = 1 if self.move_cb.value else 0
        stand         = 1 if self.stand_cb.value else 0
        rescue        = 1 if self.rescue_frozen_cb.value else 0
        rescue_all    = 1 if self.rescue_all_cb.value else 0
        kill_frz      = 1 if self.kill_on_freeze_cb.value else 0
        attack_main   = 1 if self.attack_main_cb.value else 0
        auto_hammer   = 1 if self.auto_hammer_cb.value else 0
        smart_detect  = 1 if self.smart_detect_cb.value else 0
        smart_rescue  = 1 if self.smart_rescue_cb.value else 0
        sim_players   = 1 if self.simulate_players_cb.value else 0
        avoid_freeze  = 1 if self.avoid_freeze_cb.value else 0
        pf_hook       = 1 if self.pf_hook_cb.value else 0

        fire_dist     = self.fire_distance_field.value.strip() or "80"
        hook_dist     = self.hook_distance_field.value.strip() or "400"
        rescue_radius = self.rescue_radius_field.value.strip() or "500"
        target_dist   = self.target_distance_field.value.strip() or "300"
        hook_delay    = self.hook_delay_field.value.strip() or "1000"
        main_dist     = self.main_dist_field.value.strip() or "inf"
        stand_dist    = self.stand_dist_field.value.strip() or "64"
        rescue_ids    = self.rescue_ids_field.value.strip() or "-1"

        self.send_action_command(f"c_main {main_id}")
        self.send_action_command(f"c_targets {targets_str}")
        if bots_str:
            self.send_action_command(f"c_bots {bots_str}")
        self.send_action_command(f"c_target_all {1 if all_target else 0}")
        self.send_action_command(f"c_atk_set {auto_aim} {auto_fire} {auto_hook} {move} {stand} {rescue} {rescue_all} {smart_detect} {smart_rescue} {kill_frz} {attack_main} {auto_hammer} {sim_players} {avoid_freeze} {pf_hook}")
        self.send_action_command(f"c_atk_dists {fire_dist} {hook_dist} {rescue_radius} {target_dist} {main_dist} {stand_dist}")
        self.send_action_command(f"c_atk_hook_delay {hook_delay}")
        self.send_action_command(f"c_rescue_ids {rescue_ids}")
        self._send_client_delay()
        self.send_action_command(f"c_stand_on_x {1 if self.stand_on_x_cb.value else 0}")
        self.send_action_command(f"c_atk_pathfinder {1 if self.pathfinder_cb.value else 0}")
        self.send_action_command(f"c_atk_pathfinder_rays {int(self.pathfinder_rays_slider.value)}")
        self.send_action_command(f"c_atk_pathfinder_rays_dist {int(self.pathfinder_rays_dist_slider.value)}")
        self.send_action_command(f"c_atk_pathfinder_snap {1 if self.pathfinder_snap_cb.value else 0}")
        self.send_action_command(f"c_atk_pathfinder_sps {1 if self.pathfinder_sps_cb.value else 0}")

    def _send_client_delay(self):
        selected = self.get_selected_control_cids()
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
        self._config_timer = _async_timer(0.3, self._send_attack_config)

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
    
        if hasattr(self, 'players_table') and self.players_table:
            target_column = self.players_table.columns[-1]
            if self.all_target_cb.value:
                target_column.label = ft.Text("Untarget", weight="bold")
            else:
                target_column.label = ft.Text("Target", weight="bold")
            self.players_table.update()

    def on_rescue_all_change(self, e):
        if self.rescue_all_cb.value:
            self.rescue_ids_field.label = "Unrescue IDs"
        else:
            self.rescue_ids_field.label = "Rescue IDs"
        self.rescue_ids_field.update()
        self._schedule_attack_config_update()

    def _compute_zone_target_ids(self):
        coords_text = self.target_coords_field.value.strip()
        if not coords_text:
            return set()

        zones = []
        for zone_str in coords_text.split(';'):
            zone_str = zone_str.strip()
            if not zone_str:
                continue
            try:
                range_part = zone_str.split('-')
                if len(range_part) != 2:
                    continue
                x1y1 = range_part[0].strip().split(',')
                x2y2 = range_part[1].strip().split(',')
                if len(x1y1) != 2 or len(x2y2) != 2:
                    continue
                x1, y1 = float(x1y1[0]), float(x1y1[1])
                x2, y2 = float(x2y2[0]), float(x2y2[1])
                zones.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
            except (ValueError, IndexError):
                continue

        if not zones:
            return set()

        all_players = self.bridge_receiver.get_all_players()
        zone_ids = set()
        for pid, data in all_players.items():
            if data.get('is_local'):
                continue
            px, py = data.get('x', 0), data.get('y', 0)
            for (zx1, zy1, zx2, zy2) in zones:
                if zx1 <= px <= zx2 and zy1 <= py <= zy2:
                    zone_ids.add(pid)
                    break
        return zone_ids

    def on_stand_on_x_change(self, e):
        self._schedule_attack_config_update()

    def on_pathfinder_change(self, e):
        self._schedule_attack_config_update()

    def on_pathfinder_rays_change(self, e):
        self.pathfinder_rays_value.value = str(int(e.control.value))
        self.pathfinder_rays_value.update()
        self._schedule_attack_config_update()

    def on_pathfinder_rays_dist_change(self, e):
        self.pathfinder_rays_dist_value.value = str(int(e.control.value))
        self.pathfinder_rays_dist_value.update()
        self._schedule_attack_config_update()

    def on_pathfinder_snap_change(self, e):
        self._schedule_attack_config_update()

    def on_pathfinder_sps_change(self, e):
        self._schedule_attack_config_update()

    def on_pathfinder_go_toggle(self, e):
        if self.pathfinder_go_switch.value:
            try:
                x = int(self.pathfinder_go_x.value.strip())
                y = int(self.pathfinder_go_y.value.strip())
                self._send_attack_config()
                self.send_action_command(f"c_pathfinder_go 1 {x} {y}")
            except:
                self.send_action_command("c_pathfinder_go 1")
            try:
                asyncio.create_task(self.monitor_pathfinder_go_status())
            except RuntimeError:
                pass
        else:
            self.send_action_command("c_pathfinder_go 0")

    async def monitor_pathfinder_go_status(self):
        while self.pathfinder_go_switch.value:
            await asyncio.sleep(0.5)
            for ddnet_id in self.get_selected_clients():
                if "Reached destination" in self.client_manager.client_log.get(ddnet_id, ""):
                    self.pathfinder_go_switch.value = False
                    self.pathfinder_go_switch.update()
                    self.send_action_command("c_pathfinder_go 0")
                    self.add_log(f"✅ Client #{ddnet_id} reached destination")
                    break

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

    def on_generate_timeout_code(self, e):
        cmd = "cl_timeout_code {r}{r}{r}{r}{r}{r}{r}{r}{r}{r}{r}{r}{r}{r}"
        self.send_action_command(cmd)
        self.add_log("🔑 Timeout code generated and sent")

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
                time.sleep(0.1)
            self.all_clients_btn.content = ft.Text("Start all clients")
            self.all_clients_btn.icon = ft.Icons.PLAY_ARROW
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

            try:
                asyncio.create_task(asyncio.to_thread(launch_with_delay))
            except RuntimeError:
                self.add_log("❌ Failed to start clients: no event loop")

        self.all_clients_btn.update()
        self.update_clients_stats()
        self.page.update()

    async def players_tab_loop(self):
        weapon_names = {0: "Hammer", 1: "Pistol", 2: "Shotgun", 3: "Rocket", 4: "Laser", 5: "Ninja"}
    
        if not hasattr(self, 'target_checkboxes'):
            self.target_checkboxes = {}
    
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
        
            current_targets = set()
            targets_str = self.attack_target_field.value.strip()
            if targets_str and targets_str != "-1":
                for part in targets_str.split(','):
                    try:
                        current_targets.add(int(part.strip()))
                    except:
                        pass
        
            for pid, data in players.items():
                player_type = "Bot" if pid in our_bot_ids else "Player"
                weapon = data.get('weapon', -1)
                weapon_str = weapon_names.get(weapon, f"Unknown ({weapon})")
                weapon_display = f"{weapon_str} ({weapon})"
                target_x = data.get('target_x', 0)
                target_y = data.get('target_y', 0)
                aim_str = f"{target_x},{target_y}"
            
                def make_checkbox(p_id, current_val):
                    def on_change(e):
                        if self.all_target_cb.value:
                            untarget_ids = set()
                            current = self.attack_target_field.value.strip()
                            if current and current != "-1":
                                for part in current.split(','):
                                    try:
                                        untarget_ids.add(int(part.strip()))
                                    except:
                                        pass
                            if e.control.value:
                                untarget_ids.add(p_id)
                            else:
                                untarget_ids.discard(p_id)
                            if untarget_ids:
                                new_value = ','.join(str(i) for i in sorted(untarget_ids))
                            else:
                                new_value = "-1"
                            self.attack_target_field.value = new_value
                        else:
                            target_ids = set()
                            current = self.attack_target_field.value.strip()
                            if current:
                                for part in current.split(','):
                                    try:
                                        target_ids.add(int(part.strip()))
                                    except:
                                        pass
                            if e.control.value:
                                target_ids.add(p_id)
                            else:
                                target_ids.discard(p_id)
                            if target_ids:
                                new_value = ','.join(str(i) for i in sorted(target_ids))
                            else:
                                new_value = ""
                            self.attack_target_field.value = new_value
        
                        self.attack_target_field.update()
                        self._schedule_attack_config_update()
    
                    return on_change
            
                cb = ft.Checkbox(
                    value=pid in current_targets,
                    on_change=make_checkbox(pid, pid in current_targets)
                )
                self.target_checkboxes[pid] = cb
            
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
                    ft.DataCell(cb),
                ]))
        
            if self.players_table:
                self.players_table.rows = rows
                self.players_table.update()
        
            target_column = self.players_table.columns[-1]
            if self.all_target_cb.value:
                target_column.label = ft.Text("Untarget", weight="bold")
            else:
                target_column.label = ft.Text("Target", weight="bold")
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
            try:
                asyncio.create_task(self.log_box.scroll_to(offset=-1, duration=0))
            except RuntimeError:
                pass
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
        was_running = self._server_mgr._running
        if was_running:
            self._server_mgr.pause(self.control_server, self.bridge_receiver)
            self.add_log("⏸️ Servers paused for proxy selection (ports freed)")
        cmd = [sys.executable, "-u", script_path]
        target = self.target_server_field.value.strip()
        count = self.spare_count_field.value.strip() or "5"
        timeout = self.timeout_field.value.strip() or "5000"
        if target:
            cmd.append(f"--target-server={target}")
        if self.spare_proxies_switch.value:
            cmd.append(f"--spare-proxies={count}")
        if timeout:
            cmd.append(f"--timeout={timeout}")
        top_n = (self.NUM_CLIENTS + self.clients_per_proxy - 1) // self.clients_per_proxy
        cmd.append(f"--top-n={top_n}")
        if self.spare_proxies_switch.value:
            count = self.spare_count_field.value.strip() or "5"
            cmd.append(f"--spare-proxies={count}")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["COLUMNS"] = "200"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, encoding='utf-8', env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, 
                                errors='replace')
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

                def resume_servers():
                    if was_running:
                        self._server_mgr.resume(self.control_server, self.bridge_receiver)
                        self.add_log("▶️ Servers resumed")
                
                self._loop.call_soon_threadsafe(resume_servers)
        try:
            asyncio.create_task(asyncio.to_thread(read_output))
        except RuntimeError:
            self.add_log("❌ Failed to start output reader")

    # ========== PROXY HELPERS ==========
    def _parse_key_to_config(self, key: str) -> dict | None:
        try:
            from python_v2ray.config_parser import parse_uri

            k = key
            if k.startswith("socks5://"):
                k = "socks://" + k[len("socks5://"):]

            parsed = parse_uri(k)
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
                sec = getattr(parsed, 'security', '')
                if sec == 'reality':
                    outbound['streamSettings']['realitySettings'] = {
                        "serverName": getattr(parsed, 'sni', ''),
                        "fingerprint": 'chrome',
                        "publicKey": getattr(parsed, 'pbk', ''),
                        "shortId": getattr(parsed, 'sid', ''),
                        "spiderX": "/"
                    }
                elif sec == 'tls':
                    outbound['streamSettings']['tlsSettings'] = {
                        "serverName": getattr(parsed, 'sni', server),
                        "allowInsecure": True
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

            elif parsed.protocol in ('hysteria', 'hysteria2'):
                return {
                    "protocol": parsed.protocol,
                    "settings": {"servers": [{"address": server, "port": parsed.port,
                                              "password": getattr(parsed, 'password', getattr(parsed, 'auth', ''))}]},
                    "streamSettings": {"network": "tcp", "security": "tls",
                                       "tlsSettings": {"serverName": getattr(parsed, 'sni', server),
                                                       "allowInsecure": True}}
                }

            elif parsed.protocol == 'socks':
                user = getattr(parsed, 'id', '') or ''
                pwd = getattr(parsed, 'password', '') or ''
                srv = {"address": server, "port": parsed.port}
                if user:
                    srv["users"] = [{"user": user, "pass": pwd}]
                return {
                    "protocol": "socks",
                    "settings": {"servers": [srv]}
                }
        except Exception:
            return None
        return None

    def _test_proxy_xray(self, key: str, port: int, test_dns: bool = False) -> float | tuple | None:
        import requests
        import socks as pysocks

        outbound = self._parse_key_to_config(key)
        if not outbound:
            return None

        temp_dir = os.path.join(os.path.dirname(__file__), "Temp")
        os.makedirs(temp_dir, exist_ok=True)

        config = {
            "log": {"loglevel": "error"},
            "inbounds": [{"port": port, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": True}}],
            "outbounds": [outbound]
        }

        prefix = "_dns" if test_dns else "_test"
        cfg_file = os.path.join(temp_dir, f"{prefix}_{port}.json")
        proc = None
        try:
            with open(cfg_file, "w") as f:
                json.dump(config, f)
            proc = subprocess.Popen(["xray.exe", "-c", cfg_file],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW,
                                    errors='replace')
            time.sleep(0.8)
            proxies = {"http": f"socks5://127.0.0.1:{port}", "https": f"socks5://127.0.0.1:{port}"}
            start = time.time()
            resp = requests.get("http://ifconfig.me/ip", proxies=proxies, timeout=5)
            if resp.status_code not in (200, 204):
                return None

            if test_dns:
                s = pysocks.socksocket()
                s.set_proxy(pysocks.SOCKS5, "127.0.0.1", port)
                s.settimeout(5)
                s.connect(("8.8.8.8", 53))
                s.send(b'\xaa\xbb\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x06google\x03com\x00\x00\x01\x00\x01')
                s.recv(512)
                s.close()

            ping = (time.time() - start) * 1000
            return (ping, key) if test_dns else round(ping, 1)
        except Exception:
            return None
        finally:
            if proc:
                proc.terminate()
                time.sleep(0.1)
            if os.path.exists(cfg_file):
                os.remove(cfg_file)

    def _batch_test_proxies(self, items: list, max_workers: int = 20, test_dns: bool = False,
                            progress_callback=None) -> dict:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for key, port in items:
                futures[executor.submit(self._test_proxy_xray, key, port, test_dns)] = port

            completed = 0
            for future in as_completed(futures):
                port = futures[future]
                result = future.result()
                if result is not None:
                    results[port] = result
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(items), len(results))

        return results

    @staticmethod
    def _key_preview(key: str) -> str:
        return key.split("#")[0][:60]

    @staticmethod
    def _ping_text(ping_ms: float | None) -> ft.Text:
        if ping_ms is None:
            return ft.Text("—", color="#F44336")
        if ping_ms < 500:
            return ft.Text(f"{ping_ms:.0f}ms", color="#4CAF50")
        elif ping_ms < 1500:
            return ft.Text(f"{ping_ms:.0f}ms", color="#FFC107")
        else:
            return ft.Text(f"{ping_ms:.0f}ms", color="#F44336")

    def _build_proxy_rows(self, data: list, ping_results: dict = None):
        rows = []
        for item in data:
            port = item["port"]
            key = item["key"]
            key_preview = key.split("#")[0][:50]
            replace_btn = ft.FilledButton("Replace", icon=ft.Icons.REFRESH,
                                          on_click=lambda e, p=port: self._replace_proxy(p), width=200,
                                          style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white"))
            if ping_results is not None:
                ping_ms = ping_results.get(port)
                ping_cell = ft.DataCell(self._ping_text(ping_ms))
            else:
                ping_cell = ft.DataCell(ft.Text("—"))

            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(str(port))),
                ft.DataCell(ft.Text(key_preview)),
                ft.DataCell(replace_btn),
                ping_cell,
            ]))
        return rows

    # ========== FAST PROXIES ==========
    def fast_proxies(self, e):
        e.control.disabled = True
        e.control.update()

        import random
        import requests

        def run_fast_proxies():
            FAST_URL = "https://raw.githubusercontent.com/lothiann/DMClients/refs/heads/main/fastproxies.json"
            N = (self.NUM_CLIENTS + self.clients_per_proxy - 1) // self.clients_per_proxy
            self.add_log(f"⚡ Fast proxies: need {N} proxies for {self.NUM_CLIENTS} clients, {self.clients_per_proxy} per proxy")

            # --- Load keys ---
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

            # --- Test all keys ---
            self.add_log("⏳ Testing proxies (HTTP + DNS)...")
            items = [(key, 30000 + i + 1) for i, key in enumerate(raw_keys)]

            def on_progress(completed, total, working):
                if completed % 50 == 0:
                    self.add_log(f"   Tested {completed}/{total}, working: {working}")

            results = self._batch_test_proxies(items, max_workers=20, test_dns=True, progress_callback=on_progress)

            working = list(results.values())
            self.add_log(f"✅ Fast test finished: {len(working)} working proxies")

            if not working:
                self.add_log("❌ No working proxies found")
                return

            # --- Select and save ---
            random.shuffle(working)
            selected = working[:N] if len(working) >= N else working
            if len(selected) < N:
                self.add_log(f"⚠️ Only {len(selected)} working proxies, need {N}")

            proxies_list = []
            for idx, (ping, key) in enumerate(selected):
                proxies_list.append({"port": 10801 + idx, "key": key})
                self.add_log(f"   Selected: {self._key_preview(key)} ({ping:.0f}ms) -> port {10801 + idx}")

            settings_dir = os.path.join(os.path.dirname(__file__), "Settings")
            os.makedirs(settings_dir, exist_ok=True)
            json_path = os.path.join(settings_dir, "proxies.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(proxies_list, f, indent=2, ensure_ascii=False)

            self.add_log(f"💾 Saved {len(proxies_list)} fast proxies to {json_path}")
            self._refresh_proxies_table()

        def run_and_unlock():
            try:
                run_fast_proxies()
            finally:
                e.control.disabled = False
                self._loop.call_soon_threadsafe(e.control.update)
        try:
            asyncio.create_task(asyncio.to_thread(run_and_unlock))
        except RuntimeError:
            pass

    # ========== PING PROXIES ==========
    def _ping_proxies(self, e=None):
        if e:
            e.control.disabled = True
            e.control.update()

        json_path = os.path.join(os.path.dirname(__file__), "Settings", "proxies.json")
        if not os.path.exists(json_path):
            if e:
                e.control.disabled = False
                e.control.update()
            self.add_log("❌ proxies.json not found")
            return

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as ex:
            if e:
                e.control.disabled = False
                e.control.update()
            self.add_log(f"❌ Error reading proxies.json: {ex}")
            return

        if not data:
            if e:
                e.control.disabled = False
                e.control.update()
            self.add_log("❌ No proxies to ping")
            return

        def _run_ping():
            items = [(item["key"], 40000 + i + 1) for i, item in enumerate(data)]
            results = self._batch_test_proxies(items, max_workers=20, test_dns=False)

            # Build port -> ping_ms mapping (results key is test_port, need real port)
            port_to_data = {i + 1: item for i, item in enumerate(data)}
            ping_by_port = {}
            for i, item in enumerate(data):
                test_port = 40000 + i + 1
                ping_by_port[item["port"]] = results.get(test_port)

            self.proxy_table.rows = self._build_proxy_rows(data, ping_by_port)
            try:
                self.proxy_table.update()
            except Exception:
                pass

        def run_and_unlock():
            try:
                _run_ping()
            finally:
                if e:
                    e.control.disabled = False
                    self._loop.call_soon_threadsafe(e.control.update)
        try:
            asyncio.create_task(asyncio.to_thread(run_and_unlock))
        except RuntimeError:
            pass

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
            env["PYTHONUTF8"] = "1"
            env["COLUMNS"] = "200"
            proc_ports = subprocess.Popen(
                [sys.executable, "-u", ports_script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding='utf-8', env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                errors='replace'
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
            try:
                asyncio.create_task(asyncio.to_thread(read_ports))
            except RuntimeError:
                pass
            proxifyre_path = os.path.join(os.path.dirname(__file__), "ProxiFyre", "ProxiFyre.exe")
            if os.path.exists(proxifyre_path):
                try:
                    proc_prox = subprocess.Popen(
                        [proxifyre_path],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, encoding='utf-8',
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, 
                        errors='replace'
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
                    try:
                        asyncio.create_task(asyncio.to_thread(read_proxifyre))
                    except RuntimeError:
                        pass
                except Exception as ex:
                    self.add_log(f"[ProxiFyre] Start error: {ex}")
            else:
                self.add_log(f"❌ {proxifyre_path} not found")
            btn.content = ft.Text("Stop proxies")
            btn.icon = ft.Icons.STOP
            btn.update()

    def clear_logs(self):
        self._log_text.value = ""
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
            try:
                asyncio.create_task(self._load_servers_async(servers))
            except RuntimeError:
                self.add_log("❌ Failed to load servers")
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
        try:
            self._hop_task = asyncio.create_task(self._server_hop_loop())
        except RuntimeError:
            self.add_log("❌ Failed to start server hop")

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
                    selected_clients = self.get_selected_control_cids()
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
                        try:
                            asyncio.create_task(self._hop_to_server(cid, ip_port))
                        except RuntimeError:
                            pass
            
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
        if hasattr(app, '_server_mgr'):
            app._server_mgr.stop()
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
    multiprocessing.freeze_support()
    if sys.platform == "win32":
        try:
            import winloop
            winloop.install()
        except ImportError:
            pass
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