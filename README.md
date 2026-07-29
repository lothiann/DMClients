# DMClients
**DMClients (DDNet Multi Clients)** — Python utility for managing a large number of DDNet game clients

[![TikTok](https://img.shields.io/badge/TikTok-@DMClients-000000?style=for-the-badge&logo=tiktok)](https://tiktok.com/@DMClients)
[![Telegram](https://img.shields.io/badge/Telegram-@DMClients-26A5E4?style=for-the-badge&logo=telegram)](https://t.me/DMClients)

---

## Table of Contents
- [Overview](#overview)
- [Functions](#functions)
  - [Console](#console)
  - [Clients](#clients)
  - [Actions](#actions)
    - [Player / Dummy settings](#player--dummy-settings)
    - [Server connection](#server-connection)
    - [Callvote](#callvote)
    - [Say / Spam](#say--spam)
    - [Input controls](#input-controls)
    - [Aim](#aim)
    - [Block](#block)
    - [Pathfinder (Experimental)](#pathfinder-experimental)
    - [Macros & Rules](#macros--rules)
    - [Code Execute](#code-execute)
  - [Tab](#tab)
  - [Servers](#servers)
    - [Server Hop](#server-hop)
  - [Settings](#settings)
- [Custom Commands](#custom-commands)
- [Placeholders](#placeholders)
- [Files](#files)
- [Bypassing Bans](#bypassing-bans)
- [FAQ](#frequently-asked-questions-faq)
- [Third-party Components](#third-party-components)

---

## Overview

DMClients lets you launch, monitor, and control dozens of DDNet clients simultaneously from a single window. It communicates with each client over two local TCP servers:
- **Control Server** (port `5555`) — sends commands to clients
- **Bridge Receiver** (port `5556`) — receives real-time game state (positions, health, weapons, etc.)

At the moment, this is the best bot utility according to these criteria:
1. **100% Free**
2. **100% Open-Source**
3. **A LOT of features**
4. You can write your **own function** using [Code Execution](#code-execute)

### What is this for?

This is needed for raids, advertising, entertainment, and even server stability testing.

---

## Functions

### Console
The main command terminal. Here you can type any command and send it to all **selected** clients at once.

- Type a command in the input field at the bottom and press **Enter** or click the send button
- The log area above shows all events, errors, and responses from the app
- Logs auto-scroll to the bottom; scrolling up pauses auto-scroll
- Up to **2000 lines** are kept in the log buffer
- Logs are **batched and flushed every 100 ms** to avoid UI freezes under heavy load (e.g. when hundreds of lines arrive per second during proxy testing)

---

### Clients
The client management table. Each row represents one DDNet client instance (`HDDNet1.exe`, `HDDNet2.exe`, etc.).

| Column | Description |
|--------|-------------|
| **Client** | Client number |
| **MEM (MB)** | Current memory usage (USS if admin, RSS otherwise) |
| **CPU (%)** | Current CPU usage |
| **Send commands** | Checkbox — whether this client receives commands |
| **Show logs** | Checkbox — whether this client's stdout is shown in the console |
| **Action** | Connect / Disconnect button |

**Header stats bar** shows:
- How many clients are running out of total (`[running/total]`)
- Total memory and CPU across all running clients

---

### Actions
A control panel for sending common commands and configuring automation.

#### Player / Dummy settings
| Field | Command sent |
|-------|-------------|
| Player name | `player_name <name>` |
| Player skin | `player_skin <skin>` |
| Dummy name | `dummy_name <name>` |
| Dummy skin | `dummy_skin <skin>` |

#### Server connection
- **Connect to** — enter `host:port` and click **Connect** to send `connect <address>` to selected clients. Click again to **Disconnect**.
- **Connect dummy / Disconnect dummy** — sends `dummy_connect` / `dummy_disconnect`.

#### Callvote
- Enter a player **name** (look up by name in bridge data) or **ID** directly.
- Toggle the **Name / ID** checkbox to switch mode.
- **Vote YES (F3)** / **Vote NO (F4)** — sends `vote yes` / `vote no`.

#### Say / Spam
- **Say** — sends `say <message>` to selected clients.
- **Spam command** — repeatedly sends a command at a configurable interval (ms). Toggle with the switch.

#### Input controls
Manually hold inputs on clients:

| Control | Command |
|---------|---------|
| Left | `c_input left 100000000` |
| Right | `c_input right 100000000` |
| Jump | `c_input jump 100000000` |
| Fire | `c_input fire 100000000` |
| Hook | `c_input hook 100000000` |

Unchecking a box sends `c_input <action> 20` (releases).

- **Kill** — sends `kill; say /kill`
- **Enable / Disable copy moves** — toggles `cl_dummy_copy_moves 1/0`
- **Weapon slider** — sends `+weapon<1-5>` on change
- **Copy from ID** + **Copy moves checkbox** — sends `c_copy_moves <id>` / `c_copy_moves -1`
- **Client delay** — sends `c_client_delay <ms * client_id>` to each client individually (experimental)

#### Aim
- **X / Y sliders** (−1000 to +1000) — sends `c_oaim <x> <y>` on change
- **Random aim** — randomly changes aim at a set interval (ms):
  - Without *Random for all*: moves sliders locally and sends `c_oaim`
  - With *Random for all*: sends `c_random_aim 1 <interval>` to each client individually (handled natively by the client)

#### Block
Configure automated targeting and movement for all selected clients.

| Setting | Description |
|---------|-------------|
| Enable | Master toggle — sends `c_attack 1/0` |
| Main ID | Your main player ID (`c_main <id>`) |
| Target IDs | Comma-separated IDs to attack; or untargeted IDs if **All target** is on |
| Rescue IDs | Specific IDs to rescue (or unrescue if Rescue all is on) |
| Target Coords | Auto-target players in zones: `x1,y1-x2,y2; x3,y3-x4,y4` |
| Auto aim | Aim at target automatically |
| Hook | Hook the target |
| Fire | Fire at the target |
| Move | Allow movement |
| Stand | Stand still within Stand dist radius |
| Pathfinder | Use A* pathfinding to navigate around obstacles |
| Attack main | Also attack the main player |
| Kill on freeze | Auto respawn (`kill; say /kill`) when frozen |
| Rescue frozen | Hook frozen teammates within rescue radius |
| Rescue all | Rescue all frozen players (not just teammates) |
| Smart Detect | Find frozen players without line of sight |
| Smart Rescue | Use pathfinder to reach frozen players in rescue radius |
| Auto hammer | Automatically switch to hammer when attacking |
| Stand on X only | Maintain only X-axis position (experimental) |
| All target | Target all players (Target IDs become exclusion list) |
| Fire dist | Distance to start firing (default: 65) |
| Hook dist | Distance to start hooking (default: 400) |
| Target dist | Max distance to consider a target (default: 300) |
| Rescue radius | Radius to search for frozen players (default: 500) |
| Hook delay | Delay between hook attempts (ms, default: 1000) |
| Main dist | Radius to go to main (`inf` = unlimited) |
| Stand dist | Don't move if within N units of target/main (default: 64) |

All settings are debounced and sent together via `c_atk_set`, `c_atk_dists`, `c_atk_hook_delay`, `c_main`, `c_targets`, `c_bots`, `c_target_all`.

#### Pathfinder (Experimental)

A* pathfinding to navigate around walls and obstacles.

| Setting | Description |
|---------|-------------|
| Simulate Players | Players treated as walls (OFF = intersect character jump bypass) |
| Fix Snap | Slightly changes bot behavior when needing to jump |
| SPS | 0 = Players as walls, 1 = Players as pushable obstacles |
| Pf Hook (Experimental) | Hook onto hookable blocks while pathfinding |
| Avoid Freeze | Repel from nearby freeze tiles |
| Rays | Number of rays in raycast (12–90, default: 24) |
| Ray Dist | Max raycast distance (1–128, default: 6) |
| Go X / Y | Target coordinates for pathfinder |
| Go Switch | Enable pathfinder movement to target coordinates |

When destination reached, switch automatically turns off.

---

## Macros & Rules

Macros are stored in the `Macros/` folder. Two file types are supported:

### `.inp` — Recorded input macros
Binary/text macro files recorded by the client. Played via:
```
c_macro_load "<path>"
c_macro_play 1
```
Duration is calculated by summing all `sleep <ms>` lines in the file.

### `.rule` — Python scripting
A Python script executed per-client with access to game state. Each client runs its own thread.

**Available variables and functions in `.rule` files:**

| Name | Type | Description |
|------|------|-------------|
| `client_id` | `int` | Control server ID of the current client |
| `pos.x(*pid)` / `pos.y()` | `float` | Current player world position |
| `aim.x(*pid)` / `aim.y()` | `int` | Current aim target coordinates |
| `weapon(*pid)` | `int` | Current weapon (0=Hammer … 5=Ninja) |
| `health(*pid)` | `int` | Current HP |
| `armor(*pid)` | `int` | Current armor |
| `frozen(*pid)` | `bool` | Whether the player is frozen |
| `team(*pid)` | `int` | Current team |
| `dir(*pid)` | `int` | Movement direction |
| `jump(*pid)` | `int` | Jump state |
| `hook(*pid)` | `int` | Hook state |
| `angle(*pid)` | `int` | Aim angle |
| `attack(*pid)` | `int` | Attack tick |
| `name(*pid)` | `str` | Player name |
| `local_id()` | `int` | In-game player ID of this client |
| `type(*pid)` | `str` | `'player'` or `'bot'` |
| `running()` | `bool` | Whether the macro is still active |
| `send(cmd)` | func | Send a command to this client |
| `send_to(cid, cmd)` | func | Send a command to a specific control client |
| `get_clients()` | func | List of all online control client IDs |
| `get_selected()` | func | List of currently selected client IDs |
| `get_log(cid)` | func | Last log line from `HDDNetN.exe` stdout |
| `sleep(ms)` | func | Sleep for N milliseconds (interruptible) |
| `log(msg)` | func | Print a message to the console |
| `macros.play(filename)` | func | Play a `.inp` file relative to the rule's directory |
| `server_name()` | func | Current server name |
| `server_map()` | func | Current map name |
| `server_gametype()` | func | Current game type |
| `server_players()` | func | Current player count |
| `server_max_players()` | func | Max player count |
| `launch_client(cid)` | func | Launch a client by ID |
| `stop_client(cid)` | func | Stop a client by ID |
| `client_running(cid)` | func | Check if a client is running |
| `threading` | module | Python `threading` module |
| `ft` | module | Flet UI module |
| `app` | object | Full app instance (advanced use) |

**Example `.rule` file:**
```python
while running():
    if frozen():
        send("kill; say /kill")
        sleep(500)
        macros.play("respawn.inp")
    sleep(100)
```

### Macro UI options

| Option | Description |
|--------|-------------|
| Macro delay (ms) | Stagger start time between clients: client N starts after `delay × (N−1)` ms |
| Record / Stop | Records inputs via `c_macro_record 1/0` |
| Play / Stop | Plays the selected file on all selected online clients |
| Save | Saves the recorded macro via `c_macro_save "<path>"` |
| Capture ID | Passed to `c_macro_capture <id>` before recording |
| Save as | Custom filename (without extension) |
| Play & Kill on freeze | Watches for frozen clients every 200ms; kills and restarts the macro |
| Don't kill if macros | Skip killing a client if its macro is still active |
| Don't block if macros | Sends `+left; +right; +jump; +hook; +fire` at macro start to unblock inputs |

The built-in **code editor** lets you view and edit `.inp` / `.rule` files directly in the app. Changes can be saved with **Save Changes** or reloaded from disk with **Reload**.

---

## Code Execute

A full Python REPL embedded in the app (experimental). Write and run any Python code with access to the same environment as `.rule` files, plus:

| Name | Description |
|------|-------------|
| `send(cmd)` | Sends to **all selected** clients (via `send_action_command`) |
| `send_to(cid, cmd)` | Send to a specific client |
| `get_clients()` | All online client IDs |
| `get_selected()` | Selected client IDs |
| `local_id(cid)` | In-game ID for a control client |
| `pos.x(pid)` / `pos.y(pid)` | Position of any player by in-game ID |
| `aim.x(pid)` / `aim.y(pid)` | Aim coordinates of any player |
| `weapon(pid)`, `health(pid)`, `frozen(pid)` | Player state |
| `log(msg)` | Print to console |
| `sleep(ms)` | Sleep in milliseconds |
| `running()` | Returns `False` when Stop is pressed |

The executor runs in a background thread. Press **Execute** to run, **Stop** to interrupt. Supports loading `.py` / `.txt` files via **Browse**.

---

### Tab
Live overview of all players currently visible to the bridge.

**Server Info table:**

| Name | Map | Type | Players | Max players |
|------|-----|------|---------|-------------|

**Players table** — updates every 0.5 seconds:

| Column | Description |
|--------|-------------|
| Player | Name (truncated to 20 chars) |
| ID | In-game player ID |
| Pos X / Y | World coordinates |
| Weapon | Weapon name (ID) |
| Health | Current HP |
| Frozen | True / False |
| Type | `Bot` (your client) or `Player` (other) |
| Dir | Movement direction |
| Jump | Jump state |
| Hook | Hook state |
| Angle | Aim angle |
| Attack | Attack tick |
| Aim | Target X, Y |

> **Note:** All coordinate types are given in units (to get tile coordinates, divide the coordinates by 32).

---

### Servers
Browse and connect to DDNet servers fetched from the official master servers.

- **Refresh** — fetches servers from all 4 DDNet masters, picks the fastest one, and loads all servers in chunks
- **Sort ↕** — toggles sort by player count (descending / ascending)
- **Hide full** — hides servers where `players >= max_players`
- **Community filter** — filter by community (DDRaceNetwork, KoG, Blockworlds, etc.)
- Each row has a **Connect** button that sends `connect <ip:port>` to selected clients

#### Server Hop
Automatically cycles through servers at a set interval.

| Setting | Description |
|---------|-------------|
| Enable | Starts / stops the hop loop |
| If players > N | Only hop to servers with more than N players |
| Skip full | Skip servers that are at max capacity |
| Random for all | Each selected client connects to a different random server |
| Precommands | Commands sent before `connect` (semicolon-separated) |
| Say | Message sent near the end of the interval after connecting |
| Frequency (ms) | How often to hop |
| Community filter | Limit hops to selected communities (multi-select) |

---

### Settings

#### Quick actions
Buttons are split across two rows.

**Row 1 — proxy buttons + clear logs:**

| Button | Description |
|--------|-------------|
| Optimal Proxies | Runs `optimal_proxies.py` — fetches subscriptions, dedupes, runs TCP/TLS/UDP prefilter, then in-game validation against a random target server. Saves the best **Proxy limit** keys to `Settings/proxies.json` (the pool). |
| Fast proxies | Downloads `fastproxies.json` from GitHub, tests each key via HTTP + DNS through a temporary Xray instance, picks the best **Proxy limit** keys and saves them to `Settings/proxies.json` (the pool). |
| Check Proxy | Tests every key in `Settings/proxies.json` via HTTP-through-SOCKS5, sorts by ping, saves the best `ceil(Clients / Clients per proxy)` keys as a plain array to `Settings/checked_proxies.json`. **Start Proxies reads from this file**, so this button must be pressed before launching proxies. |
| Start Proxies | Starts `ports_proxies.py` — reads `Settings/checked_proxies.json` and launches local Xray SOCKS5 tunnels (one per key on sequential ports starting from 10801). Toggles to Stop if already running. |
| Clear logs | Clears the console log. |

**Row 2 — clients + sync:**

| Button | Description |
|--------|-------------|
| Start / Stop all clients | Launches (or stops) all client instances with a small delay between each. The button state reflects the actual running count: if any client is not running it shows **Start**, otherwise **Stop**. |
| Sync clients | Sends `c_sync` to selected clients. |

#### Options
| Option | Description |
|--------|-------------|
| Adding ";" in commands | Wraps every command as `; <cmd>;` before sending |
| Show Proxy logs | Show output of `ports_proxies.py` in console |
| Advanced logs | Show low-level sync and token messages |
| Try to fix player loading | Sends a series of `zoom-` commands to force client to reload player list |
| Timeout reconnect | Toggles fast reconnect (`conn_timeout 5; cl_reconnect_timeout 1`) |
| Generate timeout code | Sends `cl_timeout_code` with a random 14-character string |

#### Client count
- **Clients** — total number of client instances (`HDDNet1.exe` … `HDDNetN.exe`)
- **Clients per proxy** — how many clients share one proxy
- **Proxy limit** — how many keys to keep in the `proxies.json` pool (used as `--top-n=` for Optimal/Fast proxies)
- **Apply** — copies `HDDNet1.exe` for the new count; persists `num_clients` / `clients_per_proxy` / `proxy_limit` to `Settings/proxies.json` under the `settings` block

#### Proxy testing options
- **Use spare proxies** + **Spare count** — also pick `Spare count` extra working keys into `Settings/spare_proxies.json` after the main run
- **Target servers** — comma-separated list of DDNet servers (e.g. `1.2.3.4:8305,5.6.7.8:8305`). A random one is picked **before each `connect` attempt** in the in-game validation step, so different proxies are tested against different servers.
- **Timeout (ms)** — TCP/TLS/UDP/IP-check timeout for `optimal_proxies.py`
- **Threads** — `MAX_WORKERS` for `optimal_proxies.py`
- **Test in DDNet** — when ON, validates every candidate proxy by actually connecting to a DDNet server through it. When OFF, picks the top **Proxy limit** keys by ping only (much faster but less reliable).
- **Banned Filter** — when ON, proxies whose exit IP is in `Settings/bproxies.json` are **skipped** during selection. When OFF, banned IPs are still **recorded** (so the list keeps growing) but not filtered out — useful for servers without IP-based protection. Passed via `--banned-filter=true|false`.

#### Proxies table
Shows all keys currently in `Settings/proxies.json` (the pool). Each row displays the port (10801 + index), a preview of the key, a **Replace** button that pops the next key from `Settings/spare_proxies.json`, and a **Ping** column populated by Check Proxy / Ping.

---

## Custom Commands

Commands sent through the Control Server to clients:

| Command | Description |
|---------|-------------|
| `c_input <action> <time>` | Hold an input for `time` ms. Actions: `hook`, `fire`, `left`, `right`, `jump` |
| `c_stop` | Stops any active `c_input` |
| `c_aim <dx> <dy>` | Shift aim by dx, dy relative to current position |
| `c_oaim <x> <y>` | Set aim to absolute coordinates x, y |
| `c_sleep` / `c_wake` | Toggle very slow render mode (reduces CPU usage) |
| `c_attack <0/1>` | Enable / disable auto-attack mode |
| `c_main <id>` | Set the main player ID |
| `c_targets <ids>` | Set target player IDs (comma-separated) |
| `c_bots <ids>` | Set bot player IDs (comma-separated) |
| `c_target_all <0/1>` | Target all players (ids become exclusions) |
| `c_atk_set <...>` | Set attack flags (aim, fire, hook, move, stand, rescue, rescue_all, kill_frz, atk_main, hammer) |
| `c_atk_dists <fire> <hook> <rescue> <target>` | Set attack distances |
| `c_atk_hook_delay <ms>` | Set hook delay in ms |
| `c_atk_pathfinder <0/1>` | Enable/disable pathfinding in attack mode |
| `c_atk_pathfinder_rays <n>` | Set number of pathfinder rays |
| `c_atk_pathfinder_rays_dist <n>` | Set pathfinder ray distance |
| `c_atk_pathfinder_snap <0/1>` | Toggle snap behavior |
| `c_atk_pathfinder_sps <0/1>` | Toggle SPS mode |
| `c_pathfinder_go <0/1> [x y]` | Move to coordinates using pathfinder |
| `c_rescue_ids <ids>` | Set specific IDs to rescue/unrescue |
| `c_stand_on_x <0/1>` | Hold X position only |
| `c_copy_moves <id>` | Mirror inputs from player with given in-game ID (-1 to disable) |
| `c_client_delay <ms>` | Delay all inputs by N ms (experimental) |
| `c_stand_on_x <0/1>` | Hold X position only |
| `c_random_aim <0/1> [interval]` | Enable random aim natively in the client |
| `c_macro_load "<path>"` | Load a `.inp` macro file |
| `c_macro_play <0/1>` | Start / stop macro playback |
| `c_macro_record <0/1>` | Start / stop macro recording |
| `c_macro_save "<path>"` | Save the current recorded macro |
| `c_macro_capture <id>` | Set capture ID before recording |
| `c_sync` | Force client to resync state |
| `c_proxy <0/1> [host:port]` | Route this client's traffic through the given SOCKS5 proxy (or drop it when `0`) |

---

## Placeholders

Placeholders can be used in any command sent through the UI or macros:

| Placeholder | Description |
|-------------|-------------|
| `{i}` | Client number (1 … N) |
| `{r}` | One random character from `a-z A-Z 0-9 _ . -` |
| `{ri-N}` | Random integer from 0 to N (e.g. `{ri-100}`) |
| `{n}` | Random name from `Settings/names.json` |
| `{d}` | Random word/phrase from `Settings/dictionary.json` |
| `{c}` | Random CJK character (Unicode U+4E00 – U+9FFF) |

---

## Files

Project file/folder structure:

| Path | Description |
|------|-------------|
| `UI.py/.exe` | Main application — Flet GUI, Control/Bridge servers, client management, all tabs and actions |
| `compile.py` | PyInstaller build script — patches scripts for frozen exe mode, generates `.spec`, compiles all scripts into a single `Out/DMClients/` folder |
| `optimal_proxies.py/.exe` | Proxy tester — fetches subscriptions, deduplicates, TCP/TLS/UDP prefilter, Xray connectivity test, in-game validation against a random target server. Outputs `Settings/proxies.json` (the pool). |
| `ports_proxies.py/.exe` | Proxy starter — reads `Settings/checked_proxies.json`, launches local Xray SOCKS5 tunnels (one per proxy key on sequential ports starting from 10801) |
| `xray.exe` | Xray-core binary — handles VLESS/Shadowsocks/Trojan/VMess/Hysteria/Hysteria2/WireGuard proxy connections |
| `DDNet-19.9-win64/` | DDNet client binaries — contains `HDDNet1.exe`, `HDDNet2.exe`, etc. (renamed DDNet builds with Bridge DLL injected) |
| `Settings/` | Configuration storage |
| `Settings/proxies.json` | **Pool** of proxy keys (output of Optimal/Fast proxies). Format: `{"settings": {...}, "proxies": ["key1", "key2", ...]}` — the `settings` block stores `num_clients`, `clients_per_proxy`, `proxy_limit` |
| `Settings/checked_proxies.json` | **Selected** proxies (output of Check Proxy). Plain array of key strings, e.g. `["vless://...", "vmess://..."]`. Read by `ports_proxies.py` at startup. |
| `Settings/spare_proxies.json` | Spare proxy keys — plain array of strings; the UI "Replace" button pops keys from here into `proxies.json` |
| `Settings/bproxies.json` | Banned exit IPs — populated automatically when a proxy gets banned in-game; filtered out by Optimal Proxies when **Banned Filter** is ON |
| `Settings/subscriptions.json` | List of subscription URLs / direct keys consumed by Optimal Proxies |
| `Settings/names.json` | Name list — random names picked by the `{n}` placeholder |
| `Settings/dictionary.json` | Dictionary list — random words picked by the `{d}` placeholder |
| `Macros/` | Macro storage — `.inp` (recorded input) and `.rule` (Python script) files for the Macros & Rules system |
| `Scripts/` | Script storage — `.py` / `.txt` files loadable by the Code Execute tab |
| `Temp/` | Temporary files — working directory for proxy testing, intermediate Xray configs, etc. |
| `Out/` | Build output — generated by `compile.py`, contains the compiled `DMClients/` distribution folder |

---

## Bypassing Bans

DMClients supports proxies to bypass IP bans and per-IP connection limits.

Each group of clients (configured via **Clients per proxy**) is routed through its own SOCKS5 proxy on a local port (`10801`, `10802`, …). The DDNet client receives a `c_proxy 1 127.0.0.1:<port>` command that pushes all of its traffic through the corresponding Xray tunnel — no system-level traffic interception is needed.

### Proxy pipeline

```
   Optimal Proxies / Fast proxies
                 │
                 ▼
       Settings/proxies.json      ← pool of Proxy limit keys
                 │
                 ▼  Check Proxy (HTTP ping + sort by latency)
       Settings/checked_proxies.json   ← best ceil(Clients/CPP) keys
                 │
                 ▼  Start Proxies
       ports_proxies.py  →  Xray SOCKS5 tunnels on 10801, 10802, …
                 │
                 ▼  c_proxy 1 127.0.0.1:<port>  (broadcast to clients)
            DDNet clients
```

- **Optimal Proxies / Fast proxies** — populate `proxies.json` (the pool, size = **Proxy limit**).
- **Check Proxy** — tests every key in the pool, picks the best `ceil(Clients / Clients per proxy)` keys, writes them to `checked_proxies.json` (a plain array of strings).
- **Start Proxies** — reads `checked_proxies.json` and starts one Xray SOCKS5 tunnel per key. Exits with an error if the file is missing or empty (run **Check Proxy** first).
- Each DDNet client is mapped to a port via `10801 + ((client_id − 1) // Clients per proxy)`.

**You can also select proxies manually:**
1. Get v2ray proxy keys (VLESS, Shadowsocks, Trojan, etc.)
2. Place them as a plain array in `Settings/checked_proxies.json`:
   ```json
   [
     "vless://uuid@1.2.3.4:443?...",
     "vmess://eyJ...",
     "trojan://password@5.6.7.8:443?..."
   ]
   ```
3. Click **Start Proxies** — Xray tunnels come up on ports 10801, 10802, …

> After Optimal Proxies finishes the in-game validation step, the bot sends a `disconnect` command and waits 0.5 s before killing the DDNet process — this prevents false "vpn detected" / "disconnected" logs when rapidly switching between proxies.

---

## Frequently Asked Questions (FAQ)

**Q: Where can I get player IDs?**
A1: In the "Tab" tab, find the "ID" column in the table
A2: In the DDNet client, go to Settings -> HUD -> Show Client ID

**Q: Proxy subscriptions are not downloaded during proxy testing / proxy pings are not possible**
A1: Make sure you are not blocked by your provider
A2: Enable the DPI bypass tool

**Q: When will there be a DDNet client?**
A: In plans

**Q: How can I create my own function?**
A: Use [Code Execute](#code-execute)

**Q: Where can I get commands?**
A: ddnet.org/settingscommands/#client-commands

**Q: When sending a command, the log "⚠️ No online clients selected" appears.**
A1: Make sure the clients are connected and selected in the "Clients" tab
A2: Otherwise, restart the UI and terminate all "HDDNet*.exe" processes

**Q: Start Proxies exits with `ERROR: checked_proxies.json not found`**
A: **Check Proxy** must be pressed at least once before **Start Proxies**. It tests every key in `proxies.json` and writes the best ones to `checked_proxies.json` (which `ports_proxies.py` reads at startup).

**Q: How can I insert my own proxies manually?**
A: Place them as a plain array of v2ray key strings in `Settings/checked_proxies.json`:
```json
[
  "vless://uuid@1.2.3.4:443?security=reality&...",
  "vmess://eyJ2Ijoi...",
  "trojan://password@5.6.7.8:443?sni=...",
  "ss://YWVzLTI1Ni1nY206cGFzcw@9.10.11.12:8388"
]
```
Then click **Start Proxies**. Ports are assigned sequentially: first key → 10801, second → 10802, …

**Q: How can I add my proxies / subscription to the proxy selection?**
A: In `Settings/subscriptions.json`, paste your URLs or keys in the following JSON format:
```json
[
  "http://...",
  "https://...",
  "vless://...",
  "vmess://...",
  "trojan://..."
]
```

**Q: The UI freezes for ~30 seconds when many proxies are connected and clients are running**
A: This should be fixed in recent versions — logs are now batched and flushed every 100 ms instead of triggering a UI redraw per line. If you still see freezes, check the console for what's producing the log flood (most likely `ports_proxies.py` stdout or bridge_receiver polling) and toggle **Show Proxy logs** off.

---

## Third-party Components

### Xray-core (xray.exe)
- **Project:** https://github.com/XTLS/Xray-core
- **License:** MPL-2.0
- **Binary version:** v26.3.27
- **SHA256:** `15C2D007954AC53BA69B80EC91242786B3C0B71D52649165B4CA1D5CC96EF8F1`

### python-v2ray
- **Project:** https://github.com/arshiacomplus/python_v2ray
- **License:** GPL-3.0 license
- **Used for:** parsing v2ray share links (VLESS, VMess, Trojan, Shadowsocks, SOCKS, WireGuard, Hysteria, Hysteria2) into Xray outbound JSON configs

### Flet
- **Project:** https://github.com/flet-dev/flet
- **License:** Apache-2.0
- **Used for:** the entire UI framework

---

**Enjoy! =D**
