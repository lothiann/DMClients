# DMClients

**DMClients (DDNet Multi Clients)** - Python utility for managing a large number of DDNet game clients

[![TikTok](https://img.shields.io/badge/TikTok-@DMClients-000000?style=for-the-sauce&logo=tiktok)](https://tiktok.com/@DMClients)
[![Telegram](https://img.shields.io/badge/Telegram-@DMClients-26A5E4?style=for-the-sauce&logo=telegram)](https://t.me/DMClients)

---

## 📦 Installation

To run the script, you need to install the required Python libraries.

**Core Libraries (Required):**

```bash
pip install requests PySocks rich python-v2ray
```

**UI Libraries (Required only if you plan to use the graphical interface):**

```bash
pip install flet psutil
```

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
