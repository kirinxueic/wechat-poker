"""
Concurrent 10-player simulation - each player runs in own task
"""
import asyncio
import json
import uuid
import random
import time
import httpx
import websockets

BASE_URL = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"
NUM_PLAYERS = 10
NUM_HANDS = 50

# Global stats
results = {
    "hands": 0,
    "chip_violations": [],
    "deadlocks": 0,
    "errors": [],
    "winners_missing": 0,
}

class SharedState:
    def __init__(self):
        self.phase = "waiting"
        self.current_player_id = None
        self.players = {}
        self.pot = 0
        self.current_bet = 0
        self.winners = []
        self.lock = asyncio.Lock()

shared = SharedState()
hand_count = 0
hand_event = asyncio.Event()
start_chips_per_hand = {}

async def player_task(player_id, name, room_id, is_host=False):
    """Each player runs as independent coroutine"""
    global hand_count
    url = f"{WS_BASE}/ws/{room_id}/{player_id}"
    
    try:
        async with websockets.connect(url, ping_interval=None) as ws:
            # Set name
            await ws.send(json.dumps({"action": "set_name", "name": name}))
            await asyncio.sleep(0.1)
            # Ready
            await ws.send(json.dumps({"action": "ready"}))
            
            last_acted_hand = -1
            local_hand = 0
            
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    msg = json.loads(raw)
                except asyncio.TimeoutError:
                    # Deadlock detection
                    results["deadlocks"] += 1
                    results["errors"].append(f"Player {name} timeout (deadlock?) at hand {hand_count}")
                    await ws.send(json.dumps({"action": "ready"}))
                    continue
                except Exception as e:
                    results["errors"].append(f"Player {name} recv error: {e}")
                    break

                mtype = msg.get("type")

                if mtype == "game_state":
                    data = msg["data"]
                    phase = data.get("phase")
                    cur = data.get("current_player_id")
                    
                    # Track hand transitions
                    if phase == "showdown":
                        # Record end state
                        async with shared.lock:
                            key = f"end_{hand_count}"
                            if key not in start_chips_per_hand:
                                start_chips_per_hand[key] = {
                                    "end_chips": sum(p["chips"] for p in data["players"]),
                                    "pot": data.get("pot", 0),
                                    "winners": data.get("winners", []),
                                    "hand": hand_count,
                                }
                    
                    if phase == "preflop":
                        # Maybe new hand started
                        async with shared.lock:
                            key = f"start_{hand_count}"
                            if key not in start_chips_per_hand:
                                total = sum(p["chips"] + p["bet"] for p in data["players"])
                                total += data.get("pot", 0)
                                start_chips_per_hand[key] = total

                    # Act if it's our turn
                    if cur == player_id and phase not in ("waiting", "showdown"):
                        my_info = next((p for p in data["players"] if p["id"] == player_id), None)
                        if my_info and not my_info.get("folded") and not my_info.get("all_in"):
                            current_bet = data.get("current_bet", 0)
                            my_bet = my_info.get("bet", 0)
                            my_chips = my_info.get("chips", 0)
                            can_check = my_bet >= current_bet

                            r = random.random()
                            if r < 0.30:
                                action = {"action": "fold"}
                            elif r < 0.50:
                                action = {"action": "check"} if can_check else {"action": "call"}
                            elif r < 0.70:
                                action = {"action": "check"} if can_check else {"action": "call"}
                            elif r < 0.90:
                                raise_to = max(current_bet * 2, current_bet + 20)
                                raise_to = min(raise_to, my_chips + my_bet)
                                action = {"action": "raise", "amount": raise_to}
                            else:
                                action = {"action": "allin"}

                            try:
                                await asyncio.sleep(random.uniform(0.05, 0.2))
                                await ws.send(json.dumps(action))
                            except Exception as e:
                                results["errors"].append(f"Send error {name}: {e}")

                elif mtype == "showdown":
                    # Hand finished
                    async with shared.lock:
                        hand_count += 1
                        results["hands"] += 1
                        if not msg.get("winners"):
                            results["winners_missing"] += 1

                    # Send ready for next hand
                    await asyncio.sleep(5.5)  # wait for auto restart
                    await ws.send(json.dumps({"action": "ready"}))

                elif mtype == "error":
                    results["errors"].append(f"Server error to {name}: {msg.get('msg','')}")

    except Exception as e:
        results["errors"].append(f"Player {name} fatal: {e}")


async def monitor_task(num_hands):
    """Monitor chip conservation after each hand"""
    last_hand = 0
    while results["hands"] < num_hands:
        await asyncio.sleep(1)
        cur = results["hands"]
        if cur > last_hand:
            # Check conservation for completed hands
            for h in range(last_hand, cur):
                sk = f"start_{h}"
                ek = f"end_{h}"
                if sk in start_chips_per_hand and ek in start_chips_per_hand:
                    start_total = start_chips_per_hand[sk]
                    end_data = start_chips_per_hand[ek]
                    end_total = end_data["end_chips"] + end_data["pot"]
                    diff = abs(end_total - start_total)
                    if diff > NUM_PLAYERS:
                        results["chip_violations"].append(
                            f"Hand {h}: start={start_total} end={end_total} diff={diff}"
                        )
                if not start_chips_per_hand.get(ek, {}).get("winners"):
                    results["winners_missing"] += 0  # already tracked
            last_hand = cur
        
        # Timeout guard
        if results["deadlocks"] > 10:
            results["errors"].append("Too many deadlocks, aborting")
            break


async def main():
    print("=" * 60)
    print(f"QA Test: {NUM_PLAYERS} players, {NUM_HANDS} hands")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        host_id = str(uuid.uuid4())
        resp = await client.post(f"{BASE_URL}/api/rooms", json={
            "host_id": host_id,
            "host_name": "Host",
        })
        if resp.status_code != 200:
            print(f"Room creation failed: {resp.text}")
            return
        room_data = resp.json()
        room_id = room_data["room_id"]
        print(f"Room: {room_id}")

    # Create player tasks
    tasks = []
    player_ids = [host_id] + [str(uuid.uuid4()) for _ in range(NUM_PLAYERS - 1)]
    names = ["Host"] + [f"玩家{i}" for i in range(1, NUM_PLAYERS)]

    for i, (pid, name) in enumerate(zip(player_ids, names)):
        t = asyncio.create_task(player_task(pid, name, room_id, is_host=(i==0)))
        tasks.append(t)
        await asyncio.sleep(0.1)

    # Monitor task
    monitor = asyncio.create_task(monitor_task(NUM_HANDS))

    # Wait for hands to complete
    timeout = 300  # 5 minutes
    start = time.time()
    while results["hands"] < NUM_HANDS:
        await asyncio.sleep(2)
        elapsed = time.time() - start
        print(f"  Progress: {results['hands']}/{NUM_HANDS} hands, {elapsed:.0f}s elapsed")
        if elapsed > timeout:
            results["errors"].append(f"Overall timeout: only {results['hands']} hands completed")
            break

    # Cancel all tasks
    for t in tasks + [monitor]:
        t.cancel()
    await asyncio.gather(*tasks, monitor, return_exceptions=True)

    # Print report
    print()
    print("=" * 60)
    print("SIMULATION RESULTS")
    print("=" * 60)
    print(f"Hands completed:      {results['hands']}/{NUM_HANDS}")
    print(f"Chip violations:      {len(results['chip_violations'])}")
    print(f"No winner recorded:   {results['winners_missing']}")
    print(f"Deadlocks/Timeouts:   {results['deadlocks']}")
    print()
    if results["chip_violations"]:
        print("Chip violations:")
        for v in results["chip_violations"][:10]:
            print(f"  {v}")
    if results["errors"]:
        print(f"Errors (first 20 of {len(results['errors'])}):")
        for e in results["errors"][:20]:
            print(f"  - {e}")
    else:
        print("No errors!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
