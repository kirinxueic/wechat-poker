"""
Final 10-player 50-hand QA simulation
"""
import asyncio
import json
import uuid
import random
import httpx
import websockets
import time
import sys

BASE = "http://localhost:8000"
WS = "ws://localhost:8000"
NUM_PLAYERS = 10
NUM_HANDS = 50

results = {
    "hands": 0,
    "chip_violations": [],
    "deadlocks": 0,
    "errors": [],
    "winners_missing": 0,
    "server_crashes": 0,
}

# Shared across all player tasks
start_chips = {}
end_state = {}
lock = asyncio.Lock()
hand_ready_count = {}  # hand_num -> count of players who sent ready


async def player_task(pid, name, room_id):
    global results
    url = f"{WS}/ws/{room_id}/{pid}"
    
    try:
        async with websockets.connect(url, ping_interval=None, close_timeout=5) as ws:
            await ws.send(json.dumps({"action": "set_name", "name": name}))
            await asyncio.sleep(0.05)
            await ws.send(json.dumps({"action": "ready"}))
            
            local_phase = "waiting"
            
            while results["hands"] < NUM_HANDS:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=25)
                    msg = json.loads(raw)
                except asyncio.TimeoutError:
                    async with lock:
                        results["deadlocks"] += 1
                        results["errors"].append(f"{name}: recv timeout at hand {results['hands']}")
                    # Try fold then ready
                    try:
                        await ws.send(json.dumps({"action": "fold"}))
                        await asyncio.sleep(0.1)
                        await ws.send(json.dumps({"action": "ready"}))
                    except:
                        pass
                    continue
                except Exception as e:
                    async with lock:
                        results["errors"].append(f"{name} conn error: {e}")
                    break
                
                mtype = msg.get("type")
                
                if mtype == "showdown":
                    winners = msg.get("winners", [])
                    async with lock:
                        if not winners:
                            results["winners_missing"] += 1
                    # Wait for auto restart then send ready
                    await asyncio.sleep(5.8)
                    try:
                        await ws.send(json.dumps({"action": "ready"}))
                    except:
                        pass
                
                elif mtype == "game_state":
                    data = msg["data"]
                    phase = data.get("phase")
                    cur = data.get("current_player_id")
                    
                    # Chip tracking
                    async with lock:
                        hand_num = results["hands"]
                        if phase == "preflop" and local_phase in ("waiting", "showdown"):
                            key = f"start_{hand_num}"
                            if key not in start_chips:
                                total = sum(p["chips"] + p["bet"] for p in data["players"]) + data.get("pot", 0)
                                start_chips[key] = total
                        
                        if phase == "showdown" and local_phase != "showdown":
                            key = f"end_{hand_num}"
                            if key not in end_state:
                                total = sum(p["chips"] for p in data["players"]) + data.get("pot", 0)
                                end_state[key] = {
                                    "total": total,
                                    "winners": data.get("winners", []),
                                }
                                # Check conservation
                                sk = f"start_{hand_num}"
                                if sk in start_chips:
                                    diff = abs(total - start_chips[sk])
                                    if diff > NUM_PLAYERS:
                                        results["chip_violations"].append(
                                            f"Hand {hand_num+1}: start={start_chips[sk]} end={total} diff={diff}"
                                        )
                                results["hands"] += 1
                    
                    local_phase = phase
                    
                    # Act if our turn
                    if cur == pid and phase not in ("waiting", "showdown"):
                        my = next((p for p in data["players"] if p["id"] == pid), None)
                        if my and not my.get("folded") and not my.get("all_in"):
                            cb = data.get("current_bet", 0)
                            mb = my.get("bet", 0)
                            mc = my.get("chips", 0)
                            can_check = mb >= cb
                            
                            await asyncio.sleep(random.uniform(0.03, 0.12))
                            r = random.random()
                            try:
                                if r < 0.30:
                                    await ws.send(json.dumps({"action": "fold"}))
                                elif r < 0.60:
                                    await ws.send(json.dumps({"action": "check" if can_check else "call"}))
                                elif r < 0.85:
                                    rt = max(cb * 2, cb + 20)
                                    rt = min(rt, mc + mb)
                                    if rt <= cb:
                                        await ws.send(json.dumps({"action": "call"}))
                                    else:
                                        await ws.send(json.dumps({"action": "raise", "amount": rt}))
                                else:
                                    await ws.send(json.dumps({"action": "allin"}))
                            except Exception as e:
                                async with lock:
                                    results["errors"].append(f"{name} send: {e}")
    
    except Exception as e:
        async with lock:
            results["errors"].append(f"{name} fatal: {e}")
            results["server_crashes"] += 1


async def main():
    print("=" * 60)
    print(f"QA Simulation: {NUM_PLAYERS} players, {NUM_HANDS} hands")
    print("=" * 60, flush=True)
    
    async with httpx.AsyncClient() as c:
        host_id = str(uuid.uuid4())[:8]
        r = await c.post(f"{BASE}/api/rooms", json={"host_id": host_id, "host_name": "P0"})
        if r.status_code != 200:
            print("Room creation failed:", r.text)
            return
        room_id = r.json()["room_id"]
        print(f"Room: {room_id}", flush=True)
    
    player_ids = [host_id] + [str(uuid.uuid4())[:8] for _ in range(NUM_PLAYERS - 1)]
    names = [f"P{i}" for i in range(NUM_PLAYERS)]
    
    tasks = []
    for pid, name in zip(player_ids, names):
        t = asyncio.create_task(player_task(pid, name, room_id))
        tasks.append(t)
        await asyncio.sleep(0.12)  # stagger connections
    
    # Progress monitor
    deadline = time.time() + 400
    prev_hands = 0
    stall_start = time.time()
    
    while results["hands"] < NUM_HANDS:
        await asyncio.sleep(3)
        cur = results["hands"]
        print(f"  Progress: {cur}/{NUM_HANDS} hands, errors={len(results['errors'])}", flush=True)
        
        if cur > prev_hands:
            prev_hands = cur
            stall_start = time.time()
        elif time.time() - stall_start > 45:
            print("  STALLED: no progress for 45s", flush=True)
            results["errors"].append(f"Stalled at hand {cur}")
            break
        
        if time.time() > deadline:
            results["errors"].append(f"Deadline: only {cur} hands done")
            break
    
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    
    print()
    print("=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    print(f"Hands completed:     {results['hands']}/{NUM_HANDS}")
    print(f"Chip violations:     {len(results['chip_violations'])}")
    print(f"Winners missing:     {results['winners_missing']}")
    print(f"Deadlocks/Timeouts:  {results['deadlocks']}")
    print(f"Fatal errors:        {results['server_crashes']}")
    print(f"Total errors:        {len(results['errors'])}")
    if results["chip_violations"]:
        print("\nChip violations:")
        for v in results["chip_violations"]:
            print(f"  {v}")
    if results["errors"]:
        print(f"\nErrors (showing first 20):")
        for e in results["errors"][:20]:
            print(f"  - {e}")
    print("=" * 60)
    
    pass_rate = results["hands"] / NUM_HANDS * 100
    print(f"\nPass rate: {pass_rate:.1f}%  |  Issues: chip_violations={len(results['chip_violations'])}, deadlocks={results['deadlocks']}")


if __name__ == "__main__":
    asyncio.run(main())
