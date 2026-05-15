"""
Requirements:
pip install requests openai
"""

import re
import time
from openai import OpenAI
import threading
import requests
import random

OPENROUTER_API_KEY = "YOUR_OPENROUTER_API_KEY"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

def get_players_list():
    players = app.bridge_receiver.get_all_players()
    return "\n".join([
        f"ID={pid}: {p['name']} (team={p['team']})"
        for pid, p in players.items() if p.get('name')
    ])

log("🤖 AI Bot started (async)")

my_id = local_id(min(get_clients()))
if my_id is None:
    log(f"❌ Client {min(get_clients())} not connected to server")
else:
    bot_name = name(my_id)
    log(f"🤖 My name: {bot_name}")
    
    last_msg = ""
    chat_history = []
    lock = threading.Lock()
    
    def add_to_history(msg):
        with lock:
            timestamp = time.strftime("%H:%M:%S")
            chat_history.append(f"[{timestamp}] {msg}")
            log(f"📃 Added to history: \"[{timestamp}] {msg}\"")
            if len(chat_history) > 100:
                chat_history.pop(0)
    
    def process_message(message_text):
        try:
            with lock:
                context = "\n".join(chat_history)
            
            players = get_players_list()
            
            prompt = f"""Игроки на сервере:
{players}

История чата:
{context}

Ты используешься как бот в DDRaceNetwork.
Строгая структура сообщений:
[ЧАСЫ:МИНУТЫ:СЕКУНДЫ] НИК: СООБЩЕНИЕ
Тебе тут могут писать игроки упомянув твой ник ({bot_name}: или просто {bot_name}), например:
[11:11:11] Nickname: {bot_name}: Hello!
Или:
[11:11:11] Nickname: Hello {bot_name}!
Ты должен ответить в таком формате:
НИК: СООБЩЕНИЕ
Таким образом упомянув игрока (тегнув)
Если что твой ник это не твоя модель, воспринимай себя как есть
Желательно отметить пользователя в начале сообщение: напиши его ник
Если просят атаковать (сделать war, варом, target, таргетом, целью) — напиши где либо в сообщении "{{target ID1,ID2}}"
Если просят остановить атаку — напиши где либо в сообщении "{{untarget}}"
Если просят выполнить/отправить команду - напиши где либо в сообщении "{{command COMMAND}}"
Если просят кикнуть кого либо - напиши где либо в сообщении "{{kick ID REASON}}"
Если просят проголосовать ЗА - напиши где либо в сообщении "{{vote yes}}"
Если просят проголосовать за НЕТ - напиши где либо в сообщении "{{vote no}}"
Отвечай строго только на том языке на котором тебе ответили (на последнее сообщение), например:
Ответили на русском - отвечаешь на русском,
Ответили на английском - отвечаешь на английском,
И так со всеми языками
Не забывай про эти все правила
Ответь коротко одним предложением на последнее сообщение: '{message_text}'"""
            
            response = client.chat.completions.create(
                model="openrouter/owl-alpha",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                timeout=30
            )
            answer = response.choices[0].message.content.strip()

            say_text = re.sub(r'\{.*?\}', '', answer).strip()
            
            send_to(min(get_clients()), f"say {say_text}")
            log(f"🤖 Answered: {answer}")
            
            if "{" in answer and "}" in answer:
                cmd = answer[answer.find("{")+1:answer.find("}")].strip()
    
                if cmd.startswith("target "):
                    targets = cmd.replace("attack ", "")
                    targets = ",".join([t.strip() for t in targets.split(",") if t.strip().isdigit()])
                    if targets:
                        app.attack_target_field.value = targets
                        app._send_attack_config()
                        log(f"🎯 Targets: {targets}")
    
                if cmd == "untarget":
                    app.attack_target_field.value = ""
                    app._send_attack_config()
                    log("🛑 Targets cleared")

                if cmd.startswith("command "):
                    command = cmd.replace("command ", "").strip()
                    if command:
                        if command.startswith(("exit", "disconnect", "restart", "connect")):
                            log(f"⚠️ Command blocked: {command}")
                        else:
                            log(f"⚠️ Sending command: {command}")
                            send(command)

                if cmd.startswith("kick "):
                    kick = cmd.replace("kick ", "").strip()
                    if kick:
                        log(f"🗳️ Callvote kick: {kick}")
                        send_to(min(get_clients()), f"callvote kick {kick}")

                if cmd == "vote yes":
                    log("✅ Vote YES")
                    send_to(min(get_clients()), "vote yes")

                if cmd == "vote no":
                    log("❌ Vote NO")
                    send_to(min(get_clients()), "vote no")

        except Exception as e:
            log(f"❌ OpenRouter error: {e}")
    
    while running():
        msg = get_log(min(get_clients()))
        if not msg or msg == last_msg:
            sleep(50)
            continue
        
        last_msg = msg

        match_sys = re.search(r'chat/server:\s*(.+)', msg)
        if match_sys:
            sys_text = match_sys.group(1).strip()
            add_to_history(sys_text)
            continue
        
        match = re.search(r'chat/all:\s*(.+)', msg)
        if match:
            message_text = match.group(1).strip()
            add_to_history(message_text)
            
            if message_text.startswith(f"{bot_name}:"): continue
            if bot_name.lower() not in message_text.lower(): continue
            
            log(f"📩 Got message: {message_text}")
            threading.Thread(target=process_message, args=(message_text,), daemon=True).start()
        
        sleep(500)