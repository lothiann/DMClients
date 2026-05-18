# DMClients
**DMClients (DDNet Multi Clients)** — Python utility for managing a large number of DDNet game clients

[![TikTok](https://img.shields.io/badge/TikTok-@DMClients-000000?style=for-the-badge&logo=tiktok)](https://tiktok.com/@DMClients)
[![Telegram](https://img.shields.io/badge/Telegram-@DMClients-26A5E4?style=for-the-badge&logo=telegram)](https://t.me/DMClients)

---

## Table of Contents
- [Overview](#overview)
- [Navigation](#️functions)
  - **Tabs:**
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
      - [Macros & Rules](#macros--rules)
      - [Code Execute](#code-execute)
    - [Tab](#tab)
    - [Servers](#servers)
      - [Server Hop](#server-hop)
    - [Settings](#settings)
- [Custom Commands](#️custom-commands)
- [Placeholders](#placeholders)
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
| Auto aim | Aim at target automatically |
| Hook | Hook the target |
| Fire | Fire at the target |
| Move | Allow movement |
| Stand | Stand still |
| Attack main | Also attack the main player |
| Kill on freeze | Send `kill` when the client is frozen |
| Rescue frozen | Hook frozen teammates within rescue radius |
| Rescue all | Rescue all frozen (not just teammates) |
| Auto hammer | Automatically switch to and use hammer |
| Stand on X only | Maintain only X-axis position |
| Fire dist | Distance to start firing |
| Hook dist | Distance to start hooking |
| Hook delay | Delay between hook attempts (ms) |
| Target dist | Maximum distance to consider a target |
| Rescue radius | Radius to search for frozen players to rescue |

All settings are debounced and sent together via `c_atk_set`, `c_atk_dists`, `c_atk_hook_delay`, `c_main`, `c_targets`, `c_bots`, `c_target_all`.

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
| Weapon | Weapon name + ID |
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
| Button | Description |
|--------|-------------|
| Optimal Proxies | Runs `optimal_proxies_new.py` to find the best proxies for your setup |
| Fast Proxies | Downloads `fastproxies.json` from GitHub, tests each key via HTTP + DNS through a temporary Xray instance, picks the best N and saves to `Settings/proxies.json` |
| Start Proxies | Starts `ports_proxies.py` + `ProxiFyre.exe` — stops them if already running |
| Start / Stop all clients | Launches or stops all client instances with a small delay between each |
| Clear logs | Clears the console log |
| Sync clients | Sends `c_sync` to selected clients |

#### Options
| Option | Description |
|--------|-------------|
| Adding ";" in commands | Wraps every command as `; <cmd>;` before sending |
| Show Proxy logs | Show output of `ports_proxies.py` in console |
| Show ProxiFyre logs | Show output of `ProxiFyre.exe` in console |
| Advanced logs | Show low-level sync and token messages |
| Try to fix player loading | Sends a series of `zoom-` commands to force client to reload player list |
| Timeout reconnect | Toggles fast reconnect (`conn_timeout 5; cl_reconnect_timeout 1`) |
| Generate timeout code | Sends `cl_timeout_code` with a random 14-character string |

#### Client count
- **Clients** — total number of client instances (`HDDNet1.exe` … `HDDNetN.exe`)
- **Clients per proxy** — how many clients share one proxy
- **Apply** — copies `HDDNet1.exe` for the new count, regenerates `ProxiFyre/app-config.json`

#### Proxies table
Shows all entries from `Settings/proxies.json`. Each row displays the port and a preview of the proxy key, with a **Replace** button that pops the next key from `Settings/spare_proxies.json`.

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

## Bypassing Bans

DMClients supports proxies to bypass IP bans and per-IP connection limits.

Each group of clients (configured via **Clients per proxy**) is routed through its own SOCKS5 proxy on a local port (`10801`, `10802`, …). The proxy configuration is auto-generated at `ProxiFyre/app-config.json`.
The utility can also automatically select the best proxies for you. ([Settings](#settings))

**You can select them manually.**
1. Get v2ray proxy keys (VLESS, Shadowsocks, Trojan, etc.)
2. Place them in `Settings/proxies.json`
3. Click **Start Proxies** — this starts `ports_proxies.py` (Xray tunnels) and `ProxiFyre.exe` (traffic routing)

> **Note:** ProxiFyre requires the WinSock NDIS API driver. See [FAQ](#-frequently-asked-questions-faq) if it starts with `? ...` errors.

---

## Frequently Asked Questions (FAQ)

**Q: ProxiFyre starts with logs beginning with `? ....`**  
A: Download and install the driver:  
https://github.com/wiresock/ndisapi/releases

**Q: Proxies are not being selected / Finds very few proxies**  
A1: Try disabling all utilities that affect the network.  
A2: Open `optimal_proxies_new.py` in a text editor and increase the value of `GAME_BASE_TIMEOUT`.

**Q: Proxies are selected in a very short time / Errors during selection**  
A: Make sure you extracted the archive and that `xray.exe` is in the same folder as the scripts.

---

## Third-party Components

### Xray-core (xray.exe)
- **Project:** https://github.com/XTLS/Xray-core
- **License:** MPL-2.0
- **Binary version:** v26.3.27
- **SHA256:** `15C2D007954AC53BA69B80EC91242786B3C0B71D52649165B4CA1D5CC96EF8F1`

### ProxiFyre
- **Project:** https://github.com/username/ProxiFyre
- **License:** AGPL-3.0
- **Binary version:** v2.2.0
- **SHA256:** `0CF61A431D02711DDD7F3D5DCA545DF8CE5F4B808AB34B713978AC272E1719D9`

---

**Enjoy! =D**
