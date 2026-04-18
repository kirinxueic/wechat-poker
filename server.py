"""
Texas Hold'em Poker - FastAPI + WebSocket Server
"""
import json
import uuid
import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from contextlib import asynccontextmanager

from game_engine import PokerGame, GamePhase

# ─── Database ───────────────────────────────────────────────────────────────

DB_PATH = Path("poker.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            total_chips INTEGER DEFAULT 1000,
            games_played INTEGER DEFAULT 0,
            games_won INTEGER DEFAULT 0,
            chips_won INTEGER DEFAULT 0,
            chips_lost INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS game_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT,
            winner_id TEXT,
            winner_name TEXT,
            chips_won INTEGER,
            hand_name TEXT,
            played_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chip_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT,
            hand_num INTEGER DEFAULT 0,
            snapshot_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def upsert_player(player_id: str, name: str, starting_chips: int = 1000):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO players (id, name, total_chips, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            last_seen=excluded.last_seen
    """, (player_id, name, starting_chips, datetime.now().isoformat()))
    conn.commit()
    c.execute("SELECT total_chips FROM players WHERE id=?", (player_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else starting_chips

def get_player_chips(player_id: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT total_chips FROM players WHERE id=?", (player_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 1000

def save_player_chips(player_id: str, chips: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE players SET total_chips=?, last_seen=? WHERE id=?",
              (chips, datetime.now().isoformat(), player_id))
    conn.commit()
    conn.close()

def update_player_stats(player_id: str, chips_delta: int, won: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE players SET
            total_chips = total_chips + ?,
            games_played = games_played + 1,
            games_won = games_won + ?,
            chips_won = chips_won + ?,
            chips_lost = chips_lost + ?
        WHERE id=?
    """, (
        chips_delta,
        1 if won else 0,
        max(0, chips_delta),
        max(0, -chips_delta),
        player_id
    ))
    conn.commit()
    conn.close()

def get_leaderboard(limit=20):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT name, total_chips, games_played, games_won, chips_won
        FROM players
        ORDER BY total_chips DESC
        LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    return [
        {"name": r[0], "chips": r[1], "games": r[2], "wins": r[3], "won": r[4]}
        for r in rows
    ]

def record_game(room_id: str, winner_id: str, winner_name: str, chips_won: int, hand_name: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO game_history (room_id, winner_id, winner_name, chips_won, hand_name)
        VALUES (?, ?, ?, ?, ?)
    """, (room_id, winner_id, winner_name, chips_won, hand_name))
    conn.commit()
    conn.close()

def save_chip_snapshot(room_id: str, hand_num: int, players_data: list):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO chip_snapshots (room_id, hand_num, snapshot_json, created_at)
        VALUES (?, ?, ?, ?)
    """, (room_id, hand_num, json.dumps(players_data, ensure_ascii=False),
          datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_chip_snapshots(room_id: str, limit=20):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT hand_num, snapshot_json, created_at FROM chip_snapshots
        WHERE room_id=? ORDER BY id DESC LIMIT ?
    """, (room_id, limit))
    rows = c.fetchall()
    conn.close()
    return [{"hand": r[0], "players": json.loads(r[1]), "time": r[2]} for r in rows]

# ─── Room ───────────────────────────────────────────────────────────────────

class Room:
    def __init__(self, room_id: str, host_id: str, small_blind=10, big_blind=20):
        self.room_id = room_id
        self.host_id = host_id
        self.game = PokerGame(room_id, small_blind, big_blind)
        self.connections: Dict[str, WebSocket] = {}
        self.player_names: Dict[str, str] = {}
        self.game_started = False
        self.hand_count = 0
        self.action_timers: Dict[str, asyncio.Task] = {}  # action timeout timers

    async def broadcast(self, message: dict, exclude_id: str = None):
        for pid, ws in list(self.connections.items()):
            if pid != exclude_id:
                try:
                    await ws.send_json(message)
                except:
                    pass

    async def send_state(self):
        for pid, ws in list(self.connections.items()):
            try:
                state = self.game.get_state(viewer_id=pid)
                await ws.send_json({"type": "game_state", "data": state})
            except:
                pass

    async def send_to(self, player_id: str, message: dict):
        ws = self.connections.get(player_id)
        if ws:
            try:
                await ws.send_json(message)
            except:
                pass

    def cancel_action_timer(self):
        for task in self.action_timers.values():
            task.cancel()
        self.action_timers.clear()

rooms: Dict[str, Room] = {}

# ─── App ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan, title="Texas Hold'em Poker")

static_dir = Path("static")
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.get("/game/{room_id}")
async def game_page(room_id: str):
    return FileResponse("static/game.html")

@app.get("/snapshot/{room_id}")
async def snapshot_page(room_id: str):
    return FileResponse("static/snapshot.html")

@app.post("/api/rooms")
async def create_room(body: dict):
    room_id = body.get("room_id") or str(uuid.uuid4())[:8].upper()
    host_id = body.get("host_id", str(uuid.uuid4()))
    host_name = body.get("host_name", "玩家")
    small_blind = body.get("small_blind", 10)
    big_blind = body.get("big_blind", 20)

    if room_id in rooms:
        raise HTTPException(400, "房间已存在")

    room = Room(room_id, host_id, small_blind, big_blind)
    rooms[room_id] = room

    chips = upsert_player(host_id, host_name)
    room.game.add_player(host_id, host_name, chips)
    room.player_names[host_id] = host_name

    return {"room_id": room_id, "host_id": host_id}

@app.get("/api/leaderboard")
async def leaderboard():
    return get_leaderboard()

@app.get("/api/rooms/{room_id}")
async def get_room(room_id: str):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "房间不存在")
    return {
        "room_id": room_id,
        "player_count": len(room.connections),
        "game_started": room.game_started,
        "phase": room.game.phase.value,
    }

@app.get("/api/rooms/{room_id}/snapshot")
async def room_snapshot(room_id: str):
    snapshots = get_chip_snapshots(room_id)
    # Also include current in-memory chips
    room = rooms.get(room_id)
    current = []
    if room:
        current = [{"name": p.name, "chips": p.chips, "sitting_out": p.sitting_out}
                   for p in room.game.players]
    return {"snapshots": snapshots, "current": current}

# ─── WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws/{room_id}/{player_id}")
async def websocket_endpoint(ws: WebSocket, room_id: str, player_id: str):
    await ws.accept()

    room = rooms.get(room_id)
    if not room:
        await ws.send_json({"type": "error", "msg": "房间不存在"})
        await ws.close()
        return

    # Reconnect or new join
    is_reconnect = player_id in room.player_names
    room.connections[player_id] = ws

    player = room.game.get_player(player_id)
    if not player:
        # Brand new player
        name = room.player_names.get(player_id, f"玩家{len(room.game.players)+1}")
        chips = get_player_chips(player_id)
        if chips < room.game.big_blind * 2:
            chips = 1000
        room.game.add_player(player_id, name, chips)
        player = room.game.get_player(player_id)
    else:
        # Reconnect: restore chips from DB (already in player object since we keep game state)
        # If game is in progress, sit them out so they don't disrupt current hand
        if room.game.phase not in (GamePhase.WAITING, GamePhase.SHOWDOWN):
            player.sitting_out = True

    join_msg = "重新连接" if is_reconnect else "进入房间"
    await room.broadcast({
        "type": "player_joined",
        "player_id": player_id,
        "name": player.name if player else "Unknown",
        "msg": f"{player.name if player else 'Unknown'} {join_msg}"
    })
    await room.send_state()

    try:
        while True:
            data = await ws.receive_json()
            await handle_message(room, player_id, data)
    except WebSocketDisconnect:
        # Save chips to DB on disconnect
        p = room.game.get_player(player_id)
        if p:
            save_player_chips(player_id, p.chips)
        del room.connections[player_id]
        await room.broadcast({
            "type": "player_left",
            "player_id": player_id,
            "msg": f"{room.player_names.get(player_id, '玩家')} 断线"
        })
        await room.send_state()


async def handle_message(room: Room, player_id: str, data: dict):
    action = data.get("action")
    game = room.game

    if action == "set_name":
        name = data.get("name", "玩家")[:12]
        room.player_names[player_id] = name
        chips = upsert_player(player_id, name)
        p = game.get_player(player_id)
        if p:
            p.name = name
        else:
            game.add_player(player_id, name, chips)
        await room.send_state()

    elif action == "ready":
        p = game.get_player(player_id)
        if p and not p.sitting_out:
            game.set_ready(player_id)
            await room.broadcast({"type": "system", "msg": f"{room.player_names.get(player_id,'玩家')} 准备好了"})
            if game.can_start() and not room.game_started:
                await asyncio.sleep(1)
                await start_hand(room)

    elif action == "start":
        if player_id == room.host_id:
            await start_hand(room)

    elif action == "stand_up":
        p = game.get_player(player_id)
        if p and not p.sitting_out:
            p.sitting_out = True
            p.is_ready = False
            await room.broadcast({"type": "system", "msg": f"🚶 {p.name} 暂时离席"})
            await room.send_state()

    elif action == "sit_down":
        p = game.get_player(player_id)
        if p and p.sitting_out:
            p.sitting_out = False
            await room.broadcast({"type": "system", "msg": f"👋 {p.name} 回到座位"})
            await room.send_state()

    elif action == "leave_room":
        # Graceful leave: save chips, remove from game
        p = game.get_player(player_id)
        name = room.player_names.get(player_id, "玩家")
        if p:
            save_player_chips(player_id, p.chips)
            game.remove_player(player_id)
        ws = room.connections.pop(player_id, None)
        # Transfer host if needed
        if player_id == room.host_id and room.connections:
            room.host_id = next(iter(room.connections))
        await room.broadcast({"type": "player_left", "player_id": player_id,
                               "msg": f"🚪 {name} 离开了房间"})
        await room.send_state()
        if ws:
            try:
                await ws.close()
            except:
                pass

    elif action == "fold":
        room.cancel_action_timer()
        if game.action_fold(player_id):
            await after_action(room)

    elif action == "check":
        room.cancel_action_timer()
        if game.action_check(player_id):
            await after_action(room)

    elif action == "call":
        room.cancel_action_timer()
        if game.action_call(player_id):
            await after_action(room)

    elif action == "raise":
        room.cancel_action_timer()
        amount = int(data.get("amount", game.current_bet * 2))
        if game.action_raise(player_id, amount):
            await after_action(room)

    elif action == "allin":
        room.cancel_action_timer()
        p = game.get_player(player_id)
        cur = game.current_player()
        if p and cur and p.id == cur.id:
            total = p.chips + p.bet
            if total > game.current_bet:
                game.action_raise(player_id, total)
            else:
                game.action_call(player_id)
            await after_action(room)


async def start_action_timer(room: Room, player_id: str, timeout: int = 30):
    """Auto-fold if player doesn't act within timeout seconds."""
    await asyncio.sleep(timeout)
    p = room.game.get_player(player_id)
    cur = room.game.current_player()
    if p and cur and p.id == cur.id and room.game.phase not in (GamePhase.WAITING, GamePhase.SHOWDOWN):
        # Auto check if possible, else fold
        if not room.game.action_check(player_id):
            room.game.action_fold(player_id)
        await room.broadcast({"type": "system", "msg": f"⏰ {p.name} 超时自动行动"})
        await after_action(room)


async def start_hand(room: Room):
    room.game_started = True
    if room.game.start_hand():
        room.hand_count += 1
        await room.broadcast({"type": "system", "msg": f"🃏 第{room.hand_count}局开始！"})
        await room.send_state()
        # Start action timer for first player
        cur = room.game.current_player()
        if cur:
            task = asyncio.create_task(start_action_timer(room, cur.id))
            room.action_timers[cur.id] = task
    else:
        await room.broadcast({"type": "error", "msg": "玩家不足，无法开始"})


async def after_action(room: Room):
    game = room.game
    await room.send_state()

    # Start timer for next player
    room.cancel_action_timer()
    if game.phase not in (GamePhase.WAITING, GamePhase.SHOWDOWN):
        cur = game.current_player()
        if cur:
            task = asyncio.create_task(start_action_timer(room, cur.id))
            room.action_timers[cur.id] = task

    if game.phase == GamePhase.SHOWDOWN:
        room.cancel_action_timer()
        winner_ids = {w["id"] for w in game.winners}

        for w in game.winners:
            update_player_stats(w["id"], w["gain"], True)
            record_game(game.room_id, w["id"], w["name"], w["gain"], w.get("hand", ""))

        # Save chip snapshot after each hand
        snapshot_data = [
            {"name": p.name, "chips": p.chips, "id": p.id}
            for p in game.players
        ]
        save_chip_snapshot(room.room_id, room.hand_count, snapshot_data)
        # Also persist chips to DB
        for p in game.players:
            save_player_chips(p.id, p.chips)

        await room.broadcast({
            "type": "showdown",
            "winners": game.winners,
            "history": game.hand_history[-15:],
        })

        await asyncio.sleep(5)

        # Rebuy busted players
        for p in list(game.players):
            if p.chips == 0 and not p.sitting_out:
                p.chips = 1000
                await room.broadcast({"type": "system", "msg": f"💰 {p.name} 补充筹码至1000"})

        room.game_started = False
        for p in game.players:
            p.is_ready = False
        await room.send_state()
        await room.broadcast({"type": "system", "msg": "⏳ 等待玩家准备下一局..."})
        await asyncio.sleep(1)
        await room.send_state()
