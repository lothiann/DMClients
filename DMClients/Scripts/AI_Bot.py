"""
Requirements:
pip install requests openai
"""

import re
import time
from openai import OpenAI
import threading
import flet as ft

OPENROUTER_API_KEY = "YOUR_OPENROUTER_API_KEY"

muted_ids = []

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

def add_ui_container():
    """Добавляет блок AI Bot во вкладку Actions, если его ещё нет."""
    if hasattr(app, '_ai_bot_ui_added') and app._ai_bot_ui_added:
        return

    indices_to_remove = []
    for i, control in enumerate(app.actions_container.content.controls):
        if hasattr(control, 'value') and control.value == "AI Bot":
            indices_to_remove.append(i)
        elif hasattr(control, 'content') and hasattr(control.content, 'controls'):
            if len(control.content.controls) > 0:
                first = control.content.controls[0]
                if hasattr(first, 'value') and first.value == "AI Bot History":
                    indices_to_remove.append(i)
    
    for i in sorted(indices_to_remove, reverse=True):
        app.actions_container.content.controls.pop(i)
    
    app._ai_bot_ui_added = False

    history_list = ft.ListView(
        auto_scroll=True,
        spacing=2,
        expand=True,
        height=None,
    )

    def reset_history_ui(e):
        with lock:
            chat_history.clear()
        history_list.controls.clear()
        history_list.update()
        log("🔄 Chat history reset")

    def terminate_ai_bot(e):
        global running_flag
        running_flag = False
        history_list.controls.clear()
        history_list.update()
        
        remove_indices = []
        for i, control in enumerate(app.actions_container.content.controls):
            if hasattr(control, 'value') and control.value == "AI Bot":
                remove_indices.append(i)
            elif control == container:
                remove_indices.append(i)
        
        for i in sorted(remove_indices, reverse=True):
            app.actions_container.content.controls.pop(i)
        
        app.actions_container.update()
        app.page.update()
        app._ai_bot_ui_added = False

    reset_btn = ft.FilledButton(
        "Reset history",
        icon=ft.Icons.DELETE,
        on_click=reset_history_ui,
        style=ft.ButtonStyle(bgcolor="#2a2a3a", color="white")
    )

    terminate_btn = ft.FilledButton(
        "Terminate",
        icon=ft.Icons.POWER_SETTINGS_NEW,
        on_click=terminate_ai_bot,
        style=ft.ButtonStyle(bgcolor="#c62828", color="white")
    )

    button_row = ft.Row([reset_btn, terminate_btn], spacing=10)

    container = ft.Container(
        content=ft.Column([history_list, button_row], spacing=10),
        padding=10,
        bgcolor="#1a1a24",
        border_radius=10,
        width=float("inf"),
    )

    app.actions_container.content.controls.append(ft.Text("AI Bot", size=16, weight="bold"))
    app.actions_container.content.controls.append(container)
    app.actions_container.update()
    app.page.update()

    app.ai_bot_history_list = history_list
    app._ai_bot_ui_added = True
    log("✅ AI Bot UI container added")

my_id = local_id(min(get_clients()))
if my_id is None:
    log(f"❌ Client {min(get_clients())} not connected to server")
else:
    bot_name = name(my_id)
    log(f"🤖 My name: {bot_name}")

    last_msg = ""
    chat_history = []
    lock = threading.Lock()

    threading.Timer(1.0, add_ui_container).start()

    def add_to_history(msg):
        with lock:
            timestamp = time.strftime("%H:%M:%S")
            formatted = f"[{timestamp}] {msg}"
            chat_history.append(formatted)
            log(f"📃 Added to history: \"{formatted}\"")

        if hasattr(app, 'ai_bot_history_list'):
            app.ai_bot_history_list.controls.append(ft.Text(formatted, size=12))
            app.ai_bot_history_list.update()

    def reset_history():
        with lock:
            chat_history.clear()
        if hasattr(app, 'ai_bot_history_list'):
            app.ai_bot_history_list.controls.clear()
            app.ai_bot_history_list.update()
        log("🔄 Chat history reset")
        send_to(min(get_clients()), "say История чата сброшена")

    def get_player_id_by_name(player_name):
        players = app.bridge_receiver.get_all_players()
        for pid, p in players.items():
            if p.get('name') == player_name:
                return pid
        return None

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

# Примечания:
    1. Отвечай строго только на том языке на котором тебе ответили (на последнее сообщение), например:
        1.1. Ответили на русском - отвечаешь на русском,
        1.2. Ответили на английском - отвечаешь на английском,
        1.3. И так со всеми языками.
    2. Не забывай про эти все правила.
    3. Всегда добавляй таргеты/антаргеты в текущий список, не заменяй полностью если не просят, только добавляй. Ты должен взять текущий список таргетов/антаргетов и добавить туда нужные ID. НЕ ЗАМЕНЯТЬ ПОЛНОСТЬЮ. Функция targets полностью сбрасывает таргетов/антаргетов на нужные.
    4. Про команду targets: она заменяет указанные ID в текущий список целей. Если в "Текущих настройках" указан "Список таргетов" (белый список), ты атакуешь только тех, кто в списке. Если "Список антаргетов" (чёрный список), ты атакуешь всех, кроме тех, кто в списке.
    5. Про команду rescues: она добавляет указанные ID в текущий список спасения. Если "Список спасения" (белый список), ты спасаешь только указанных. Если "Список не спасения" (чёрный список), ты спасаешь всех, кроме указанных.
    6. Когда ты перестаёшь атаковать игрока (сделать unwar, тимом, untarget, антаргетом), обязательно добавь этого игрока в список спасения.
    7. Строго следуй правилам и синтаксису. Убедись что твоё сообщение точно следует тому что указано в этой инструкции. Выполняй строго функции как сказано в инструкции если попросили. Если не напишешь вызов функции - она просто не выполнится.
    8. В выходном сообщении (в чате) вызов функции СТРОГО удаляется, например:
        8.1. Было: okay. {{targets 1,2,3}} ; Стало: okay.
        8.2. Было: Targets removed. {{untargets}} ; Стало: Targets removed
    9. Пиши сообщение сторого в 1 строчку. Любой перенос строки ломает сообщение
    10. Поддерживается более одного вызова функции за раз в сообщении. Например:
        11.1. {{command echo 1}}{{command echo 2}}{{command echo 3}}
    11. Формат функций: "Что просят", "Вызов функции с включением", "Вызов функции с выключением" (противоположным действием), *"Значение по умолчанию" | "Что делает"
   
# Функции:
    # Block:
        Если просят сделать кого то таргетом/антаргетом (Target IDs)	— напиши "{{targets ID1,ID2}}".     очистить  - "{{untargets}}".                           - | Эта команда ЗАМЕНЯЕТ (не добавляет) текущий список ID ПОЛНОСТЬЮ. Строго следуй синтаксису. Если "Список таргетов" (Выключен All Target), (белый список), ты атакуешь только тех, кто в списке.    Если "Список антаргетов" (Включен All Target), (чёрный список), ты атакуешь всех, кроме тех, кто в списке.
        Если просят спасать/не спасать кого то (Rescue IDs)	            — напиши "{{rescues ID1,ID2}}".     очистить  - "{{unrescues}}".                           - | Эта команда ЗАМЕНЯЕТ (не добавляет) текущий список ID ПОЛНОСТЬЮ. Строго следуй синтаксису. Если "Список спасения" (Выключен Rescue All), (белый список), спасаешь только указанных.               Если "Список не спасения" (Включен Rescue All), (чёрный список), спасаешь всех, кроме указанных.
        Если просят включить атаку (Attack)	                            — напиши "{{attack 1}}",            выключить — "{{attack 0}}".            (по умолчанию: 0) | Включает всю систему Block
        Если просят установить главного (Main ID)	                    — напиши "{{main ID}}",             выключить - "{{main }}".                               - | За кем следовать (Главный игрок)
        Если просят включить автонаведение (Auto aim)	                — напиши "{{auto_aim 1}}",          выключить — "{{auto_aim 0}}".          (по умолчанию: 1) | Включает доступ к aim
        Если просят включить автоогонь (Auto fire)	                    — напиши "{{auto_fire 1}}",         выключить — "{{auto_fire 0}}".         (по умолчанию: 1) | Включает доступ к fire
        Если просят включить автохук (Auto hook)	                    — напиши "{{auto_hook 1}}",         выключить — "{{auto_hook 0}}".         (по умолчанию: 1) | Включает доступ к hook
        Если просят включить автомолот (Auto hammer)	                — напиши "{{auto_hammer 1}}",       выключить — "{{auto_hammer 0}}".       (по умолчанию: 1) | Автоматически меняет оружие на hammer
        Если просят включить движение (Move)	                        — напиши "{{move 1}}",              выключить — "{{move 0}}".              (по умолчанию: 1) | Включает доступ к Move
        Если просят включить стояние (Stand)	                        — напиши "{{stand 1}}",             выключить — "{{stand 0}}".             (по умолчанию: 1) | Не двигается когда достиг цели в радиусе Stand dist
        Если просят включить спасение замороженных (Rescue frozen)	    — напиши "{{rescue_frozen 1}}",     выключить — "{{rescue_frozen 0}}".     (по умолчанию: 1) | Включает спасение Main, Других ботов, если список спасения (Включен Rescue All) то и указанных игроков (Rescue IDs)
        Если просят включить спасение всех (Rescue all)	                — напиши "{{rescue_all 1}}",        выключить — "{{rescue_all 0}}".        (по умолчанию: 0) | Включает спасение абсолютно всех кроме Target'ов, указанных игроков (Rescue IDs), делает "Список спасения" в "Список не спасения"
        Если просят включить килл при заморозке (Kill on freeze)	    — напиши "{{kill_freeze 1}}",       выключить — "{{kill_freeze 0}}".       (по умолчанию: 0) | Делает авто респавн когда падает во фриз
        Если просят включить атаку главного (Attack main)	            — напиши "{{attack_main 1}}",       выключить — "{{attack_main 0}}".       (по умолчанию: 0) | Делает Main как таргетом, но все равно спасает, для развлечения
        Если просят включить поиск пути (Pathfinder)	                — напиши "{{pathfinder 1}}",        выключить — "{{pathfinder 0}}".        (по умолчанию: 1) | Включает доступ к алгоритмам поиска пути, что в разы улучшает всю логику ходьбы
        Если просят включить режим всех целей (All target)	            — напиши "{{target_all 1}}",        выключить — "{{target_all 0}}".        (по умолчанию: 0) | Делает всех таргетами, делает "Список таргетов" в "Список антаргетов". Инвертирует логику таргетов
        Если просят включить симуляцию игроков (Simulate players)	    — напиши "{{simulate_players 1}}",  выключить — "{{simulate_players 0}}".  (по умолчанию: 1) | Симулирует игроков для поиска пути (Pathfinder)
        Если просят включить избегание фриза (Avoid freeze)	            — напиши "{{avoid_freeze 1}}",      выключить — "{{avoid_freeze 0}}".      (по умолчанию: 1) | При поиске пути (Pathfinder) старается не идти в сторону фриза
        Если просят включить хук по пути (Pf hook)	                    — напиши "{{pf_hook 1}}",           выключить — "{{pf_hook 0}}".           (по умолчанию: 0) | Использует хук во время ходьбы по построенному пути через поиск пути (Pathfinder)
        Если просят включить умное обнаружение (Smart detect)	        — напиши "{{smart_detect 1}}",      выключить — "{{smart_detect 0}}".      (по умолчанию: 1) | Умно обнаруживает кого нужно спасти используя поиск пути (Pathfinder)
        Если просят включить умное спасение (Smart rescue)	            — напиши "{{smart_rescue 1}}",      выключить — "{{smart_rescue 0}}".      (по умолчанию: 1) | Умно спасает используя поиск пути (Pathfinder)
        # Dists:
            Если просят изменить дистанцию огня (Fire dist)	            — напиши "{{fire_dist число}}"                                             (по умолчанию 65) | В какой области разрешено использовать fire
            Если просят изменить дистанцию хука (Hook dist)	            — напиши "{{hook_dist число}}"                                            (по умолчанию 400) | В какой области разрешено использовать hook
            Если просят изменить радиус спасения (Rescue radius)	    — напиши "{{rescue_radius число}}"                                        (по умолчанию 500) | В какой области разрешено искать тех, кто упал во фриз
            Если просят изменить дистанцию цели (Target dist)	        — напиши "{{target_dist число}}"                                          (по умолчанию 300) | В какой области разрешено искать таргетов
            Если просят изменить дистанцию главного (Main dist)	        — напиши "{{main_dist число}}"                                            (по умолчанию inf) | В какой области разрешено идти к main
            Если просят изменить дистанцию стояния (Stand dist)	        — напиши "{{stand_dist число}}"                                            (по умолчанию 64) | В какой области разрешено использовать stand (становиться)
            Если просят изменить задержку хука (Hook delay (ms))	    — напиши "{{hook_delay число}}"                                          (по умолчанию 1000) | Сколько держать хук в миллисекундах

    # Default:
        Если просят выполнить команду (Command)	                        — напиши "{{command COMMAND}}"                                                             - | Исполняет какую либо команду DDNet
        Если просят кикнуть (Kick)	                                    — напиши "{{kick ID REASON}}"                                                              - | Делает голосование за кик какого либо игрока
        Если просят проголосовать ЗА (Vote yes)	                        — напиши "{{vote yes}}"                                                                    - | Голосовать ЗА
        Если просят проголосовать ПРОТИВ (Vote no)	                    — напиши "{{vote no}}"                                                                     - | Голосовать ПРОТИВ

    # Chat:
        Если просят замутить (Mute)	                                    — напиши "{{mute NAME}}"                                                                   - | Больше не показывает сообщения от определённого игрока
        Если просят размутить (Unmute)	                                — напиши "{{unmute NAME}}"                                                                 - | Показывает сообщения от определённого игрока (снимает мут (Mute))

    # Advanced:
        Если просят копировать движения (Copy moves)	                — напиши "{{copy_moves 1 ID}}", выключить — "{{copy_moves 0}}"                             - | Копирует движения у определённого игрока
        Если просят включить случайный аим (Random aim)	                — напиши "{{random_aim 1 MS RFA}}", выключить — "{{random_aim 0}}"   (по умолчанию: 0 100 1) | Делает случайный аим с нужной частотой
        ^-> MS - Частота в миллисекундах (по умолчанию 100)
        ^-> RFA (Random For All) - Рандомно для каждого клиента (по умолчанию 1), выключить - 0

# Списки:
    Список {"антаргетов (Атакуются все кроме указанных)" if app.all_target_cb.value else "таргетов (Атакуются только указанные)"}: {app.attack_target_field.value.strip() or "нет"}
    Список {"не спасения (Спасаются все кроме указанных)" if app.rescue_all_cb.value else "спасения (Спасаются только указанные)"}: {app.rescue_ids_field.value.strip() or "нет"}

# Текущие настройки:
    attack: {app.attack_enable_switch.value}
    main: {app.main_id_field.value.strip() or "нету"}
    rescue_frozen: {app.rescue_frozen_cb.value}
    rescue_radius: {app.rescue_radius_field.value}
    smart_detect: {app.smart_detect_cb.value}
    smart_rescue: {app.smart_rescue_cb.value}
    auto_aim: {app.auto_aim_cb.value}
    auto_fire: {app.fire_target_cb.value}
    auto_hook: {app.hook_target_cb.value}
    auto_hammer: {app.auto_hammer_cb.value}
    move: {app.move_cb.value}
    stand: {app.stand_cb.value}
    fire_dist: {app.fire_distance_field.value}
    hook_dist: {app.hook_distance_field.value}
    hook_delay: {app.hook_delay_field.value}
    kill_freeze: {app.kill_on_freeze_cb.value}
    attack_main: {app.attack_main_cb.value}
    pathfinder: {app.pathfinder_cb.value}
    simulate_players: {app.simulate_players_cb.value}
    avoid_freeze: {app.avoid_freeze_cb.value}
    pf_hook: {app.pf_hook_cb.value}
    rays: {int(app.pathfinder_rays_slider.value)}
    rays_dist: {int(app.pathfinder_rays_dist_slider.value)}
    random_aim: {app.random_aim_checkbox.value}
    random_aim_interval: {app.random_aim_interval.value}
    copy_moves: {app.copy_moves_cb.value}
    copy_id: {app.copy_id_field.value or "нету"}
    client_delay: {app.delay_field.value} ({"on" if app.delay_checkbox.value else "off"})
    target_all: {app.all_target_cb.value}
    rescue_all: {app.rescue_all_cb.value}"""

            message = f"""Ответь коротко одним предложением на последнее сообщение: '{message_text}'"""

            response = client.chat.completions.create(
                model="openrouter/owl-alpha",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": message}],
                max_tokens=500,
                timeout=30
            )
            answer = response.choices[0].message.content.strip()

            say_text = re.sub(r'\{.*?\}', '', answer).strip()

            send_to(min(get_clients()), f"say {say_text}")
            log(f"🤖 Answered: {answer}")

            for cmd in re.findall(r'\{\{(.*?)\}\}', answer):
                cmd = cmd.strip()
    
                if cmd.startswith("targets "):
                    ids = cmd.replace("targets ", "").strip()
                    ids = ",".join([t.strip() for t in ids.split(",") if t.strip().isdigit()])
                    if ids:
                        app.attack_target_field.value = ids
                        app._send_attack_config()
                        app.page.update()
                        log(f"🎯 Targets: {ids}")
    
                elif cmd == "untargets":
                    app.attack_target_field.value = ""
                    app._send_attack_config()
                    app.page.update()
                    log("🛑 Targets cleared")
    
                elif cmd.startswith("rescues "):
                    ids = cmd.replace("rescues ", "").strip()
                    ids = ",".join([t.strip() for t in ids.split(",") if t.strip().isdigit()])
                    if ids:
                        app.rescue_ids_field.value = ids
                        app._send_attack_config()
                        app.page.update()
                        log(f"🆘 Rescues: {ids}")

                elif cmd == "unrescues":
                    app.rescue_ids_field.value = ""
                    app._send_attack_config()
                    app.page.update()
                    log(f"🆘 Rescues cleared")
    
                elif cmd in ["attack on", "attack 1", "attack enable"]:
                    app.attack_enable_switch.value = True
                    app.on_attack_toggle(None)
                    app.page.update()
                    log("⚔️ Attack mode enabled")
    
                elif cmd in ["attack off", "attack 0", "attack disable"]:
                    app.attack_enable_switch.value = False
                    app.on_attack_toggle(None)
                    app.page.update()
                    log("🕊️ Attack mode disabled")
    
                elif cmd.startswith("main "):
                    main_id = cmd.replace("main ", "").strip()
                    app.main_id_field.value = main_id
                    app._send_attack_config()
                    app.page.update()
                    log(f"👑 Main ID set to: '{main_id}'")
    
                elif cmd in ["auto_aim on", "auto_aim 1", "auto_aim enable"]:
                    app.auto_aim_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🎯 Auto aim enabled")
    
                elif cmd in ["auto_aim off", "auto_aim 0", "auto_aim disable"]:
                    app.auto_aim_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🎯 Auto aim disabled")
    
                elif cmd in ["auto_fire on", "auto_fire 1", "auto_fire enable"]:
                    app.fire_target_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🔥 Auto fire enabled")
    
                elif cmd in ["auto_fire off", "auto_fire 0", "auto_fire disable"]:
                    app.fire_target_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🔥 Auto fire disabled")
    
                elif cmd in ["auto_hook on", "auto_hook 1", "auto_hook enable"]:
                    app.hook_target_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🪝 Auto hook enabled")
    
                elif cmd in ["auto_hook off", "auto_hook 0", "auto_hook disable"]:
                    app.hook_target_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🪝 Auto hook disabled")
    
                elif cmd in ["auto_hammer on", "auto_hammer 1", "auto_hammer enable"]:
                    app.auto_hammer_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🔨 Auto hammer enabled")
    
                elif cmd in ["auto_hammer off", "auto_hammer 0", "auto_hammer disable"]:
                    app.auto_hammer_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🔨 Auto hammer disabled")
    
                elif cmd in ["move on", "move 1", "move enable"]:
                    app.move_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🚶 Movement enabled")
    
                elif cmd in ["move off", "move 0", "move disable"]:
                    app.move_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🚶 Movement disabled")
    
                elif cmd in ["stand on", "stand 1", "stand enable"]:
                    app.stand_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🧍 Standing enabled")
    
                elif cmd in ["stand off", "stand 0", "stand disable"]:
                    app.stand_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🧍 Standing disabled")
    
                elif cmd in ["rescue_frozen on", "rescue_frozen 1", "rescue_frozen enable"]:
                    app.rescue_frozen_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🆘 Rescue frozen enabled")
    
                elif cmd in ["rescue_frozen off", "rescue_frozen 0", "rescue_frozen disable"]:
                    app.rescue_frozen_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🆘 Rescue frozen disabled")
    
                elif cmd in ["rescue_all on", "rescue_all 1", "rescue_all enable"]:
                    app.rescue_all_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🌍 Rescue all enabled")
    
                elif cmd in ["rescue_all off", "rescue_all 0", "rescue_all disable"]:
                    app.rescue_all_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🌍 Rescue all disabled")
    
                elif cmd in ["kill_freeze on", "kill_freeze 1", "kill_freeze enable"]:
                    app.kill_on_freeze_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("💀 Kill on freeze enabled")
    
                elif cmd in ["kill_freeze off", "kill_freeze 0", "kill_freeze disable"]:
                    app.kill_on_freeze_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("💀 Kill on freeze disabled")
    
                elif cmd in ["attack_main on", "attack_main 1", "attack_main enable"]:
                    app.attack_main_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🎯 Attack main enabled")
    
                elif cmd in ["attack_main off", "attack_main 0", "attack_main disable"]:
                    app.attack_main_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🎯 Attack main disabled")
    
                elif cmd in ["pathfinder on", "pathfinder 1", "pathfinder enable"]:
                    app.pathfinder_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🗺️ Pathfinder enabled")
    
                elif cmd in ["pathfinder off", "pathfinder 0", "pathfinder disable"]:
                    app.pathfinder_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🗺️ Pathfinder disabled")
    
                elif cmd in ["target_all on", "target_all 1", "target_all enable"]:
                    app.all_target_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🎯 Target all mode enabled (blacklist)")
    
                elif cmd in ["target_all off", "target_all 0", "target_all disable"]:
                    app.all_target_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🎯 Target all mode disabled (whitelist)")
    
                elif cmd in ["simulate_players on", "simulate_players 1", "simulate_players enable"]:
                    app.simulate_players_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("👥 Simulate players enabled")
    
                elif cmd in ["simulate_players off", "simulate_players 0", "simulate_players disable"]:
                    app.simulate_players_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("👥 Simulate players disabled")
    
                elif cmd in ["avoid_freeze on", "avoid_freeze 1", "avoid_freeze enable"]:
                    app.avoid_freeze_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("❄️ Avoid freeze enabled")
    
                elif cmd in ["avoid_freeze off", "avoid_freeze 0", "avoid_freeze disable"]:
                    app.avoid_freeze_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("❄️ Avoid freeze disabled")
    
                elif cmd in ["pf_hook on", "pf_hook 1", "pf_hook enable"]:
                    app.pf_hook_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🪝 Pathfinder hook enabled")
    
                elif cmd in ["pf_hook off", "pf_hook 0", "pf_hook disable"]:
                    app.pf_hook_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🪝 Pathfinder hook disabled")
    
                elif cmd in ["smart_detect on", "smart_detect 1", "smart_detect enable"]:
                    app.smart_detect_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🔍 Smart detect enabled")
    
                elif cmd in ["smart_detect off", "smart_detect 0", "smart_detect disable"]:
                    app.smart_detect_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🔍 Smart detect disabled")
    
                elif cmd in ["smart_rescue on", "smart_rescue 1", "smart_rescue enable"]:
                    app.smart_rescue_cb.value = True
                    app._send_attack_config()
                    app.page.update()
                    log("🧠 Smart rescue enabled")
    
                elif cmd in ["smart_rescue off", "smart_rescue 0", "smart_rescue disable"]:
                    app.smart_rescue_cb.value = False
                    app._send_attack_config()
                    app.page.update()
                    log("🧠 Smart rescue disabled")
    
                elif cmd.startswith("fire_dist "):
                    val = cmd.replace("fire_dist ", "").strip()
                    if val.isdigit() or val == "inf":
                        app.fire_distance_field.value = val
                        app._send_attack_config()
                        app.page.update()
                        log(f"🔥 Fire distance: {val}")
    
                elif cmd.startswith("hook_dist "):
                    val = cmd.replace("hook_dist ", "").strip()
                    if val.isdigit() or val == "inf":
                        app.hook_distance_field.value = val
                        app._send_attack_config()
                        app.page.update()
                        log(f"🪝 Hook distance: {val}")
    
                elif cmd.startswith("rescue_radius "):
                    val = cmd.replace("rescue_radius ", "").strip()
                    if val.isdigit():
                        app.rescue_radius_field.value = val
                        app._send_attack_config()
                        app.page.update()
                        log(f"🆘 Rescue radius: {val}")
    
                elif cmd.startswith("target_dist "):
                    val = cmd.replace("target_dist ", "").strip()
                    if val.isdigit() or val == "inf":
                        app.target_distance_field.value = val
                        app._send_attack_config()
                        app.page.update()
                        log(f"🎯 Target distance: {val}")
    
                elif cmd.startswith("main_dist "):
                    val = cmd.replace("main_dist ", "").strip()
                    if val.isdigit() or val == "inf":
                        app.main_dist_field.value = val
                        app._send_attack_config()
                        app.page.update()
                        log(f"👑 Main distance: {val}")
    
                elif cmd.startswith("stand_dist "):
                    val = cmd.replace("stand_dist ", "").strip()
                    if val.isdigit():
                        app.stand_dist_field.value = val
                        app._send_attack_config()
                        app.page.update()
                        log(f"🧍 Stand distance: {val}")
    
                elif cmd.startswith("hook_delay "):
                    val = cmd.replace("hook_delay ", "").strip()
                    if val.isdigit():
                        app.hook_delay_field.value = val
                        app._send_attack_config()
                        app.page.update()
                        log(f"⏱️ Hook delay: {val}")
    
                elif cmd in ["random_aim on", "random_aim 1", "random_aim enable"]:
                    app.random_aim_checkbox.value = True
                    app.page.update()
                    log("🎲 Random aim enabled")
    
                elif cmd.startswith("random_aim "):
                    parts = cmd.replace("random_aim ", "").strip().split()
                    val = parts[0] if parts else "0"
                    if val == "1":
                        app.random_aim_checkbox.value = True
                        if len(parts) >= 2 and parts[1].isdigit():
                            app.random_aim_interval.value = parts[1]
                        if len(parts) >= 3:
                            app.random_for_all_checkbox.value = parts[2] == "1"
                        log(f"🎲 Random aim enabled (interval={app.random_aim_interval.value}, all={app.random_for_all_checkbox.value})")
                    else:
                        app.random_aim_checkbox.value = False
                        if app.random_aim_task:
                            app.random_aim_task.cancel()
                        log("🎲 Random aim disabled")
                    app.page.update()
    
                elif cmd.startswith("copy_moves "):
                    parts = cmd.replace("copy_moves ", "").strip().split()
                    if parts[0] == "1":
                        app.copy_moves_cb.value = True
                        if len(parts) > 1 and parts[1].isdigit():
                            app.copy_id_field.value = parts[1]
                            app.send_action_command(f"c_copy_moves {parts[1]}")
                        log(f"📋 Copy moves enabled (ID: {app.copy_id_field.value})")
                    else:
                        app.copy_moves_cb.value = False
                        app.copy_id_field.value = ""
                        log("📋 Copy moves disabled")
                    app.page.update()
    
                elif cmd.startswith("command "):
                    command = cmd.replace("command ", "").strip()
                    if command:
                        blocked = ("exit", "disconnect", "restart", "connect", "player_name")
                        if any(command.startswith(b) for b in blocked):
                            log(f"⚠️ Command blocked: {command}")
                        else:
                            log(f"⚠️ Sending command: {command}")
                            send(command)
    
                elif cmd.startswith("kick "):
                    kick = cmd.replace("kick ", "").strip()
                    if kick:
                        log(f"🗳️ Callvote kick: {kick}")
                        send_to(min(get_clients()), f"callvote kick {kick}")
    
                elif cmd == "vote yes":
                    log("✅ Vote YES")
                    send_to(min(get_clients()), "vote yes")
    
                elif cmd == "vote no":
                    log("❌ Vote NO")
                    send_to(min(get_clients()), "vote no")
    
                elif cmd.startswith("mute "):
                    target_name = cmd.replace("mute ", "").strip()
                    target_id = get_player_id_by_name(target_name)
                    if target_id and target_id not in muted_ids:
                        muted_ids.append(target_id)
                        log(f"🔇 {target_name} (ID {target_id}) muted")
                    elif target_id:
                        log(f"⚠️ {target_name} already muted")
                    else:
                        log(f"❌ Player {target_name} not found")
    
                elif cmd.startswith("unmute "):
                    target_name = cmd.replace("unmute ", "").strip()
                    target_id = get_player_id_by_name(target_name)
                    if target_id and target_id in muted_ids:
                        muted_ids.remove(target_id)
                        log(f"🔊 {target_name} (ID {target_id}) unmuted")
                    elif target_id:
                        log(f"⚠️ {target_name} not muted")
                    else:
                        log(f"❌ Player {target_name} not found")

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

            sender_name = message_text.split(":", 1)[0].strip()
            sender_id = get_player_id_by_name(sender_name)

            if sender_id and sender_id in muted_ids:
                log(f"🚫 Ignored muted player: {sender_name} (ID {sender_id})")
                continue

            if message_text.startswith(f"{bot_name}:"):
                continue
            if bot_name.lower() not in message_text.lower():
                continue

            log(f"📩 Got message: {message_text}")
            threading.Thread(target=process_message, args=(message_text,), daemon=True).start()

        match = re.search(r'chat/team:\s*(.+)', msg)
        if match:
            message_text = match.group(1).strip()
            add_to_history(message_text)

            if message_text.startswith(f"{bot_name}:"):
                continue
            if bot_name.lower() not in message_text.lower():
                continue

            log(f"📩 Got message: {message_text}")
            threading.Thread(target=process_message, args=(message_text,), daemon=True).start()

        sleep(500)