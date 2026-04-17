"""
Texas Hold'em Poker - 10-player 50-hand simulation test
"""
import asyncio
import json
import uuid
import random
import time
import httpx
import websockets
from collections import defaultdict

BASE_URL = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"

stats = {
    "hands_completed": 0,
    "hands_deadlock": 0,
    "chip_violations": 0,
    "crashes": 0,
    "no_winner": 0,
    "errors": [],
}

NUM_PLAYERS = 10
NUM_HANDS = 50

class PlayerBot:
    def __init__(self, player_id, name, room_id):
        self.player_id = player_id
        self.name = name
        self.room_id = room_id
        self.ws = None
        self.last_state = None
        self.action_count = 0

    async def connect(self):
        url = f"{WS_BASE}/ws/{self.room_id}/{self.player_id}"
        self.ws = await websockets.connect(url)

    async def send(self, msg):
        await self.ws.send(json.dumps(msg))

    async def recv_with_timeout(self, timeout=2):
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            return json.loads(raw)
        except asyncio.TimeoutError:
            return None

    async def close(self):
        if self.ws:
            await self.ws.close()

    def choose_action(self, state):
        cur_id = state.get("current_player_id")
        if cur_id != self.player_id:
            return None

        my_info = next((p for p in state["players"] if p["id"] == self.player_id), None)
        if not my_info:
            return None

        current_bet = state.get("current_bet", 0)
        my_bet = my_info.get("bet", 0)
        my_chips = my_info.get("chips", 0)
        can_check = my_bet >= current_bet

        r = random.random()
        if r < 0.30:
            return {"action": "fold"}
        elif r < 0.50:
            if can_check:
                return {"action": "check"}
            else:
                return {"action": "call"}
        elif r < 0.70:
            if can_check:
                return {"action": "check"}
            else:
                return {"action": "call"}
        elif r < 0.90:
            raise_to = current_bet * 2 + random.randint(0, 20) * 10
            raise_to = max(raise_to, current_bet + 20)
            return {"action": "raise", "amount": raise_to}
        else:
            return {"action": "allin"}


async def run_game(room_id, initial_chips):
    """Monitor one game hand, return (completed, chip_conserved, winner_recorded)"""
    # Create bots
    bots = []
    for i in range(NUM_PLAYERS):
        pid = f"player_{room_id}_{i}"
        bot = PlayerBot(pid, f"玩家{i+1}", room_id)
        bots.append(bot)

    # Connect all bots
    for bot in bots:
        await bot.connect()
        await asyncio.sleep(0.05)

    # Set name and ready
    for bot in bots:
        await bot.send({"action": "set_name", "name": bot.name})
        await asyncio.sleep(0.02)
    for bot in bots:
        await bot.send({"action": "ready"})
        await asyncio.sleep(0.02)

    return bots


async def monitor_hands(room_id, bots, num_hands):
    """Run the game and monitor hands"""
    hands_done = 0
    action_rounds = 0
    last_phase = None
    last_action_time = time.time()
    MAX_IDLE = 15  # seconds without state change = deadlock

    all_states = {bot.player_id: None for bot in bots}
    hand_start_chips = None
    hand_violations = 0
    hand_no_winner = 0
    hand_deadlock = 0
    errors = []

    # Map ws -> bot
    ws_to_bot = {bot.ws: bot for bot in bots}

    async def get_any_message(timeout=0.5):
        """Get next message from any websocket"""
        futs = {asyncio.ensure_future(bot.ws.recv()): bot for bot in bots}
        done, pending = await asyncio.wait(
            list(futs.keys()), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for f in pending:
            f.cancel()
        if done:
            fut = list(done)[0]
            bot = futs[fut]
            try:
                raw = fut.result()
                return bot, json.loads(raw)
            except Exception as e:
                return None, None
        return None, None

    start_time = time.time()
    consecutive_idle = 0

    while hands_done < num_hands:
        bot, msg = await get_any_message(timeout=0.3)
        
        if time.time() - start_time > 300:  # 5 min overall timeout
            errors.append(f"Overall timeout after {hands_done} hands")
            break

        if msg is None:
            consecutive_idle += 1
            # Check if any bot needs to act
            for b in bots:
                state = all_states.get(b.player_id)
                if state and state.get("current_player_id") == b.player_id:
                    phase = state.get("phase", "")
                    if phase not in ("waiting", "showdown"):
                        action = b.choose_action(state)
                        if action:
                            try:
                                await b.send(action)
                                last_action_time = time.time()
                                consecutive_idle = 0
                            except Exception as e:
                                errors.append(f"Send error: {e}")

            if time.time() - last_action_time > MAX_IDLE:
                hand_deadlock += 1
                errors.append(f"Deadlock after hand {hands_done}: no activity for {MAX_IDLE}s")
                # Try to nudge all players
                for b in bots:
                    state = all_states.get(b.player_id)
                    if state and state.get("current_player_id") == b.player_id:
                        try:
                            await b.send({"action": "fold"})
                        except:
                            pass
                last_action_time = time.time()
                consecutive_idle = 0
                if hand_deadlock >= 5:
                    errors.append("Too many deadlocks, stopping")
                    break
            continue

        consecutive_idle = 0

        if msg.get("type") == "game_state":
            data = msg["data"]
            all_states[bot.player_id] = data
            phase = data.get("phase")

            # Track hand start chips
            if phase == "preflop" and last_phase in ("waiting", "showdown", None):
                hand_start_chips = sum(p["chips"] + p["bet"] for p in data["players"])
                # Also add pot
                hand_start_chips += data.get("pot", 0)

            # Showdown - check conservation
            if phase == "showdown" and last_phase != "showdown":
                hands_done += 1
                stats["hands_completed"] += 1

                # Check chip conservation
                end_chips = sum(p["chips"] for p in data["players"])
                end_chips += data.get("pot", 0)  # should be 0 at showdown
                
                if hand_start_chips is not None:
                    diff = abs(end_chips - hand_start_chips)
                    if diff > NUM_PLAYERS:  # allow small rounding
                        hand_violations += 1
                        stats["chip_violations"] += 1
                        errors.append(f"Hand {hands_done}: chip violation! start={hand_start_chips} end={end_chips} diff={diff}")

                # Check winners recorded
                winners = data.get("winners", [])
                if not winners:
                    hand_no_winner += 1
                    stats["no_winner"] += 1
                    errors.append(f"Hand {hands_done}: no winner recorded!")

                last_action_time = time.time()

                # Send ready for next hand
                for b in bots:
                    try:
                        await b.send({"action": "ready"})
                    except:
                        pass

            last_phase = phase

            # Act if it's our turn
            if phase not in ("waiting", "showdown"):
                action = bot.choose_action(data)
                if action:
                    try:
                        await bot.send(action)
                        last_action_time = time.time()
                    except Exception as e:
                        errors.append(f"Action error: {e}")

        elif msg.get("type") == "showdown":
            pass  # handled via game_state

        elif msg.get("type") == "error":
            errors.append(f"Server error: {msg.get('msg', '')}")

    return hands_done, hand_violations, hand_no_winner, hand_deadlock, errors


async def main():
    print("=" * 60)
    print("Texas Hold'em Poker - QA Simulation")
    print(f"Players: {NUM_PLAYERS}, Hands: {NUM_HANDS}")
    print("=" * 60)

    # Create room
    async with httpx.AsyncClient() as client:
        host_id = str(uuid.uuid4())
        resp = await client.post(f"{BASE_URL}/api/rooms", json={
            "host_id": host_id,
            "host_name": "Host",
        })
        if resp.status_code != 200:
            print(f"Failed to create room: {resp.text}")
            return
        room_data = resp.json()
        room_id = room_data["room_id"]
        print(f"Room created: {room_id}")

    # Connect players (skip host since they're already added)
    bots = []
    # First bot is the host
    host_bot = PlayerBot(host_id, "Host", room_id)
    bots.append(host_bot)
    for i in range(1, NUM_PLAYERS):
        pid = str(uuid.uuid4())
        bot = PlayerBot(pid, f"玩家{i+1}", room_id)
        bots.append(bot)

    print(f"Connecting {NUM_PLAYERS} players...")
    for bot in bots:
        try:
            await bot.connect()
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Connection failed for {bot.name}: {e}")
            stats["crashes"] += 1

    # Send names
    for bot in bots:
        try:
            await bot.send({"action": "set_name", "name": bot.name})
            await asyncio.sleep(0.05)
        except:
            pass

    # Send ready
    for bot in bots:
        try:
            await bot.send({"action": "ready"})
            await asyncio.sleep(0.05)
        except:
            pass

    print("All players ready, monitoring game...")

    # Monitor
    hands_done, violations, no_winner, deadlocks, errors = await monitor_hands(room_id, bots, NUM_HANDS)

    # Close connections
    for bot in bots:
        try:
            await bot.close()
        except:
            pass

    # Print report
    print("\n" + "=" * 60)
    print("SIMULATION RESULTS")
    print("=" * 60)
    print(f"Hands completed:    {hands_done}/{NUM_HANDS}")
    print(f"Chip violations:    {violations}")
    print(f"No winner recorded: {no_winner}")
    print(f"Deadlocks:          {deadlocks}")
    print(f"Crashes:            {stats['crashes']}")
    print()
    if errors:
        print("ERRORS (first 20):")
        for e in errors[:20]:
            print(f"  - {e}")
    else:
        print("No errors detected!")
    
    print("=" * 60)
    return hands_done, violations, no_winner, deadlocks, errors


if __name__ == "__main__":
    asyncio.run(main())
