"""
Texas Hold'em Poker - FastAPI + WebSocket Server
"""
import json
import uuid
import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Set, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

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
            total_chips INTEGER DEFAULT 0,
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
    # Get current chips
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

# ─── Room Manager ───────────────────────────────────────────────────────────

class Room:
    def __init__(self, room_id: str, host_id: str, small_blind=10, big_blind=20):
        self.room_id = room_id
        self.host_id = host_id
        self.game = PokerGame(room_id, small_blind, big_blind)
        self.connections: Dict[str, WebSocket] = {}  # player_id -> websocket
        self.player_names: Dict[str, str] = {}
        self.game_started = False

    async def broadcast(self, message: dict, exclude_id: str = None):
        for pid, ws in list(self.connections.items()):
            if pid != exclude_id:
                try:
                    await ws.send_json(message)
                except:
                    pass

    async def send_state(self):
        """Send personalized game state to each player."""
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


rooms: Dict[str, Room] = {}

# ─── App ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan, title="Texas Hold'em Poker")

# Serve static files
static_dir = Path("static")
static_dir.mkdir(exist_ok=True)

@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.get("/game/{room_id}")
async def game_page(room_id: str):
    return FileResponse("static/game.html")

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

# ─── WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws/{room_id}/{player_id}")
async def websocket_endpoint(ws: WebSocket, room_id: str, player_id: str):
    await ws.accept()

    room = rooms.get(room_id)
    if not room:
        await ws.send_json({"type": "error", "msg": "房间不存在"})
        await ws.close()
        return

    # Register connection
    room.connections[player_id] = ws
    player = room.game.get_player(player_id)
    if not player:
        # New player joining via link
        name = room.player_names.get(player_id, f"玩家{len(room.connections)}")
        chips = get_player_chips(player_id)
        if chips < room.game.big_blind * 2:
            chips = 1000  # Rebuy
        room.game.add_player(player_id, name, chips)
        player = room.game.get_player(player_id)

    await room.broadcast({
        "type": "player_joined",
        "player_id": player_id,
        "name": player.name if player else "Unknown",
        "msg": f"{player.name if player else 'Unknown'} 进入房间"
    })
    await room.send_state()

    try:
        while True:
            data = await ws.receive_json()
            await handle_message(room, player_id, data)
    except WebSocketDisconnect:
        del room.connections[player_id]
        await room.broadcast({
            "type": "player_left",
            "player_id": player_id,
            "msg": f"{room.player_names.get(player_id, '玩家')} 离开房间"
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
        game.set_ready(player_id)
        await room.broadcast({"type": "system", "msg": f"{room.player_names.get(player_id,'玩家')} 准备好了"})
        if game.can_start() and not room.game_started:
            await asyncio.sleep(1)
            await start_hand(room)

    elif action == "start":
        if player_id == room.host_id:
            await start_hand(room)

    elif action == "fold":
        if game.action_fold(player_id):
            await after_action(room)

    elif action == "check":
        if game.action_check(player_id):
            await after_action(room)

    elif action == "call":
        if game.action_call(player_id):
            await after_action(room)

    elif action == "raise":
        amount = int(data.get("amount", game.current_bet * 2))
        if game.action_raise(player_id, amount):
            await after_action(room)

    elif action == "allin":
        p = game.get_player(player_id)
        if p:
            game.action_raise(player_id, p.chips + p.bet)
            await after_action(room)


async def start_hand(room: Room):
    room.game_started = True
    if room.game.start_hand():
        await room.broadcast({"type": "system", "msg": "🃏 新一局开始！"})
        await room.send_state()
    else:
        await room.broadcast({"type": "error", "msg": "玩家不足，无法开始"})


async def after_action(room: Room):
    game = room.game
    await room.send_state()

    if game.phase == GamePhase.SHOWDOWN:
        # Update stats
        winner_ids = {w["id"] for w in game.winners}
        player_chips_before = {p.id: p.chips - sum(w["gain"] for w in game.winners if w["id"] == p.id) for p in game.players}

        for w in game.winners:
            update_player_stats(w["id"], w["gain"], True)
            record_game(game.room_id, w["id"], w["name"], w["gain"], w.get("hand", ""))

        await room.broadcast({
            "type": "showdown",
            "winners": game.winners,
            "history": game.hand_history[-15:],
        })

        # Auto restart after delay
        await asyncio.sleep(5)
        # Remove busted players
        for p in list(game.players):
            if p.chips == 0:
                # Give them chips to rebuy
                chips = 1000
                p.chips = chips

        room.game_started = False
        # Prompt ready again
        for p in game.players:
            p.is_ready = False
        await room.send_state()
        await room.broadcast({"type": "system", "msg": "⏳ 等待玩家准备下一局..."})
