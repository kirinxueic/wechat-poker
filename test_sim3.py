"""
Quick simulation: detect bugs by running hands and capturing all messages
"""
import asyncio
import json
import uuid
import random
import httpx
import websockets
import time

BASE = "http://localhost:8000"
WS = "ws://localhost:8000"

async def run_hand_test(num_players=5, num_hands=10):
    """Run hands and return results"""
    async with httpx.AsyncClient() as c:
        host_id = str(uuid.uuid4())[:8]
        r = await c.post(f"{BASE}/api/rooms", json={"host_id": host_id, "host_name": "P0"})
        room_id = r.json()["room_id"]

    player_ids = [host_id] + [str(uuid.uuid4())[:8] for _ in range(num_players-1)]
    
    # Shared state
    state = {"hands": 0, "errors": [], "chip_violations": [], "phase": "waiting",
             "start_total": None, "deadlocks": 0}
    
    queues = {pid: asyncio.Queue() for pid in player_ids}
    
    async def ws_reader(pid, ws):
        """Read messages and put in queue"""
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=45)
                msg = json.loads(raw)
                await queues[pid].put(msg)
        except asyncio.TimeoutError:
            await queues[pid].put({"type": "_timeout"})
        except Exception as e:
            await queues[pid].put({"type": "_error", "err": str(e)})

    async def player_loop(pid, name, ws):
        """Handle player actions"""
        last_hand = -1
        
        while state["hands"] < num_hands:
            try:
                msg = await asyncio.wait_for(queues[pid].get(), timeout=20)
            except asyncio.TimeoutError:
                state["deadlocks"] += 1
                state["errors"].append(f"{name}: queue timeout (deadlock?) at hand {state['hands']}")
                # Force fold
                try:
                    await ws.send(json.dumps({"action":"fold"}))
                except:
                    pass
                continue
            
            mtype = msg.get("type")
            
            if mtype == "_timeout" or mtype == "_error":
                state["errors"].append(f"{name}: {mtype} {msg.get('err','')}")
                break
            
            if mtype == "showdown":
                if state["hands"] == last_hand:
                    state["hands"] += 1
                    last_hand = state["hands"]
                    winners = msg.get("winners", [])
                    if not winners:
                        state["errors"].append(f"Hand {state['hands']}: no winners in showdown msg")
                    # Schedule ready after delay
                    await asyncio.sleep(5.5)
                    await ws.send(json.dumps({"action": "ready"}))
            
            elif mtype == "game_state":
                data = msg["data"]
                phase = data.get("phase")
                cur = data.get("current_player_id")
                
                if phase == "preflop" and state["start_total"] is None:
                    # Capture starting chips
                    t = sum(p["chips"] + p["bet"] for p in data["players"]) + data.get("pot", 0)
                    state["start_total"] = t
                
                if phase == "showdown" and state["start_total"] is not None:
                    # Check conservation
                    t = sum(p["chips"] for p in data["players"]) + data.get("pot", 0)
                    diff = abs(t - state["start_total"])
                    if diff > num_players:
                        state["chip_violations"].append(
                            f"Hand {state['hands']+1}: start={state['start_total']} end={t} diff={diff}")
                    state["start_total"] = None
                    winners = data.get("winners", [])
                    if not winners:
                        state["errors"].append(f"Hand {state['hands']+1}: no winners in game_state showdown")
                
                # Act if our turn
                if cur == pid and phase not in ("waiting", "showdown"):
                    my = next((p for p in data["players"] if p["id"] == pid), None)
                    if my and not my.get("folded") and not my.get("all_in"):
                        cb = data.get("current_bet", 0)
                        mb = my.get("bet", 0)
                        mc = my.get("chips", 0)
                        can_check = mb >= cb
                        
                        await asyncio.sleep(random.uniform(0.05, 0.15))
                        r = random.random()
                        try:
                            if r < 0.30:
                                await ws.send(json.dumps({"action":"fold"}))
                            elif r < 0.60:
                                await ws.send(json.dumps({"action":"check" if can_check else "call"}))
                            elif r < 0.85:
                                rt = max(cb*2, cb+20)
                                rt = min(rt, mc+mb)
                                await ws.send(json.dumps({"action":"raise","amount":rt}))
                            else:
                                await ws.send(json.dumps({"action":"allin"}))
                        except Exception as e:
                            state["errors"].append(f"{name} send: {e}")

    # Connect all
    wss = {}
    readers = []
    for pid in player_ids:
        ws = await websockets.connect(f"{WS}/ws/{room_id}/{pid}", ping_interval=None)
        wss[pid] = ws
    
    for i, pid in enumerate(player_ids):
        ws = wss[pid]
        await ws.send(json.dumps({"action":"set_name","name":f"P{i}"}))
        await asyncio.sleep(0.05)
    
    for pid in player_ids:
        ws = wss[pid]
        readers.append(asyncio.create_task(ws_reader(pid, ws)))
    
    await asyncio.sleep(0.5)
    for pid in player_ids:
        await wss[pid].send(json.dumps({"action":"ready"}))
        await asyncio.sleep(0.05)

    # Run player loops
    loops = [asyncio.create_task(player_loop(pid, f"P{i}", wss[pid])) 
             for i, pid in enumerate(player_ids)]
    
    # Wait for completion
    deadline = time.time() + 300
    while state["hands"] < num_hands and time.time() < deadline:
        await asyncio.sleep(2)
        print(f"  Hands: {state['hands']}/{num_hands}", end="\r")
    
    print()
    for t in loops + readers:
        t.cancel()
    await asyncio.gather(*loops, *readers, return_exceptions=True)
    for ws in wss.values():
        try:
            await ws.close()
        except:
            pass
    
    return state


async def main():
    print("=" * 60)
    print("Texas Hold'em QA - Dynamic Simulation")
    print("=" * 60)
    
    print("\n[Phase 1] Quick 5-player 10-hand smoke test...")
    s1 = await run_hand_test(5, 10)
    print(f"  Hands: {s1['hands']}/10, Violations: {len(s1['chip_violations'])}, Deadlocks: {s1['deadlocks']}")
    if s1["errors"]:
        for e in s1["errors"][:5]:
            print(f"  ERR: {e}")
    
    print("\n[Phase 2] Full 10-player 50-hand test...")
    s2 = await run_hand_test(10, 50)
    
    print("\n" + "=" * 60)
    print("FULL RESULTS")
    print("=" * 60)
    print(f"Hands completed:    {s2['hands']}/50")
    print(f"Chip violations:    {len(s2['chip_violations'])}")
    print(f"Deadlocks:          {s2['deadlocks']}")
    print(f"Other errors:       {len(s2['errors'])}")
    print()
    if s2["chip_violations"]:
        print("Chip Violations:")
        for v in s2["chip_violations"]:
            print(f"  {v}")
    if s2["errors"]:
        print(f"Errors ({len(s2['errors'])} total, showing first 20):")
        for e in s2["errors"][:20]:
            print(f"  - {e}")
    print("=" * 60)
    return s2


if __name__ == "__main__":
    r = asyncio.run(main())
