# Example script

TARGET = 0

def move(cid, dir):
    send_to(cid, dir)

while running():
    target = app.bridge_receiver.get_player_state(TARGET)
    if not target:
        sleep(100)
        continue
    
    tx = target['x']
    ty = target['y']
    
    for cid in get_clients():
        x = pos.x(cid)
        y = pos.y(cid)
        
        if x == 0.0 and y == 0.0:
            continue
        
        dist = ((tx - x)**2 + (ty - y)**2) ** 0.5
        
        if dist <= 100:
            threading.Thread(target=move, args=(cid, "c_input left 0;c_input right 0"), daemon=True).start()
            log(f"Client #{cid} reached target")
        elif x < tx:
            threading.Thread(target=move, args=(cid, "c_input right 100000000"), daemon=True).start()
        else:
            threading.Thread(target=move, args=(cid, "c_input left 100000000"), daemon=True).start()
    
    sleep(50)