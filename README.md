# DMClients

**DMClients (DDNet Multi Clients)** - Python utility for managing a large number of DDNet game clients

[![TikTok](https://img.shields.io/badge/TikTok-@DMClients-000000?style=for-the-sauce&logo=tiktok)](https://tiktok.com/@DMClients)
[![Telegram](https://img.shields.io/badge/Telegram-@DMClients-26A5E4?style=for-the-sauce&logo=telegram)](https://t.me/DMClients)

---

## 🌐 Bypassing Bans

This utility supports proxies. You can use a proxy hosting service and **ProxiFyre** to bypass IP bans and connection limits.

> ~~**Note:** You will need VLESS, Shadowsocks, Hysteria2, or similar keys to use ProxiFyre.~~

---

## 🧑‍💻 Custom commands

`c_input <action> <time>`:
action - hook, fire, left, right, jump; 
time - time in ms

`c_stop`:
stops c_input

`c_aim <dx> <dy>`:
shifts the sight coordinates by dx, dy

`c_oaim <x> <y>`:
sets the sight coordinates to x, y

`c_sleep`/`c_wake`:
makes a VERY slow render

## 🖋️ Placeholders

`{i}` - customer number (1..N)

`{r}` - random character (a-z, A-Z, 0-9, _, ., -)

`{ri-N}` is a random number from 0 to N (for example {ri-100})

`{n}` is a random nickname from names.json

`{d}` is a random phrase from the dictionary.json

`{c}` - random Chinese character (Unicode 4E00–9FFF)

---

## Third-party components

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

## ❓ Frequently Asked Questions (FAQ)

**Q: ProxiFyre starts with logs beginning with "? .... "**

A: Download and install the driver:  
https://github.com/wiresock/ndisapi/releases

**Q: Proxies are not being selected / Finds few proxies**

A1: Try disabling all utilities that affect the network.  
A2: Try opening the script in a text editor and changing the value of the `GAME_BASE_TIMEOUT` variable to a number greater than.

**Q: Proxies are selected in a very short time / Errors**

A: Make sure that you have extracted the archive and there is a script next to it `xray.exe`.

**Q: Scripts are not running / with error**

A1: Make sure that you have Python version 3.14.3 (and later, if the libraries support it) and make sure that you have all the necessary libraries installed.  
A2: Make sure that you have 1 Python installed, and you run through `C:/Windows/py.exe`.

---

**Enjoy! =D**
