# Example script
import urllib.request
import json
import random

try:
    with urllib.request.urlopen("https://master1.ddnet.org/ddnet/15/servers.json", timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))

    servers = data.get("servers", [])
    communities = {c["id"]: c.get("name", c["id"]) for c in data.get("communities", [])}

    srv = random.choice(servers)
    info = srv.get("info", {})
    addr = srv.get("addresses", [""])[0].split("://")[-1].strip("[]")

    log(f"{communities.get(srv.get('community'), 'none')} | {info.get('name', '?')} | {info.get('map', {}).get('name', '?')} | {len(info.get('clients', []))}/{info.get('max_players', 0)} | {addr}")

except Exception as e:
    log(f"Error: {e}")