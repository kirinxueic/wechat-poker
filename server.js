// ============================================================
// 德州扑克服务端  server.js
// Node.js + Socket.io  支持多人联机、完整规则、边池计算
// ============================================================
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const path = require('path');

const app = express();
const server = http.createServer(app);
const io = new Server(server, { cors: { origin: '*' } });

app.use(express.static(path.join(__dirname, 'public')));

// ─────────────────────────────────────────────
// 牌组工具
// ─────────────────────────────────────────────
const SUITS = ['♠', '♥', '♦', '♣'];
const RANKS = ['2','3','4','5','6','7','8','9','10','J','Q','K','A'];
const RANK_VAL = Object.fromEntries(RANKS.map((r,i) => [r, i+2]));

function makeDeck() {
  const deck = [];
  for (const s of SUITS) for (const r of RANKS) deck.push({ r, s });
  return deck;
}
function shuffle(deck) {
  for (let i = deck.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

// ─────────────────────────────────────────────
// 牌型评估  (返回值越大越好)
// ─────────────────────────────────────────────
function cardVal(r) { return RANK_VAL[r]; }

function evaluateHand(cards7) {
  // 从7张中选最优5张
  const combos = choose5(cards7);
  let best = null;
  for (const c of combos) {
    const s = scoreHand(c);
    if (!best || compareScore(s, best.score) > 0) best = { score: s, cards: c };
  }
  return best;
}

function choose5(cards) {
  const result = [];
  for (let i=0;i<cards.length-4;i++)
  for (let j=i+1;j<cards.length-3;j++)
  for (let k=j+1;k<cards.length-2;k++)
  for (let l=k+1;l<cards.length-1;l++)
  for (let m=l+1;m<cards.length;m++)
    result.push([cards[i],cards[j],cards[k],cards[l],cards[m]]);
  return result;
}

function scoreHand(cards) {
  const vals = cards.map(c => cardVal(c.r)).sort((a,b) => b-a);
  const suits = cards.map(c => c.s);
  const counts = {};
  for (const v of vals) counts[v] = (counts[v]||0)+1;
  const groups = Object.values(counts).sort((a,b) => b-a);
  const flush = suits.every(s => s === suits[0]);
  const straight = isStraight(vals);
  const straightVal = straight ? getStraightHigh(vals) : 0;

  // 皇家/同花顺
  if (flush && straight) {
    return [8, straightVal, ...vals];
  }
  // 四条
  if (groups[0] === 4) {
    const quad = Number(Object.entries(counts).find(([,c])=>c===4)[0]);
    const kick = vals.filter(v=>v!==quad)[0];
    return [7, quad, kick];
  }
  // 葫芦
  if (groups[0] === 3 && groups[1] === 2) {
    const trip = Number(Object.entries(counts).find(([,c])=>c===3)[0]);
    const pair = Number(Object.entries(counts).find(([,c])=>c===2)[0]);
    return [6, trip, pair];
  }
  // 同花
  if (flush) return [5, ...vals];
  // 顺子
  if (straight) return [4, straightVal, ...vals];
  // 三条
  if (groups[0] === 3) {
    const trip = Number(Object.entries(counts).find(([,c])=>c===3)[0]);
    const kicks = vals.filter(v=>v!==trip);
    return [3, trip, ...kicks];
  }
  // 两对
  if (groups[0] === 2 && groups[1] === 2) {
    const pairs = Object.entries(counts).filter(([,c])=>c===2).map(([v])=>Number(v)).sort((a,b)=>b-a);
    const kick = vals.filter(v=>v!==pairs[0]&&v!==pairs[1])[0];
    return [2, pairs[0], pairs[1], kick];
  }
  // 一对
  if (groups[0] === 2) {
    const pair = Number(Object.entries(counts).find(([,c])=>c===2)[0]);
    const kicks = vals.filter(v=>v!==pair);
    return [1, pair, ...kicks];
  }
  // 高牌
  return [0, ...vals];
}

function isStraight(sortedVals) {
  if (getStraightHigh(sortedVals) > 0) return true;
  return false;
}
function getStraightHigh(sortedVals) {
  const uniq = [...new Set(sortedVals)];
  // A-2-3-4-5 低顺
  if (uniq.includes(14)&&uniq.includes(2)&&uniq.includes(3)&&uniq.includes(4)&&uniq.includes(5)) return 5;
  for (let i=0;i<uniq.length-4;i++) {
    if (uniq[i]-uniq[i+4]===4 &&
        uniq[i+1]-uniq[i+2]===1 &&
        uniq[i+2]-uniq[i+3]===1 &&
        uniq[i+3]-uniq[i+4]===1) return uniq[i];
  }
  return 0;
}
function compareScore(a, b) {
  for (let i=0;i<Math.max(a.length,b.length);i++) {
    const av = a[i]||0, bv = b[i]||0;
    if (av !== bv) return av - bv;
  }
  return 0;
}

const HAND_NAMES = ['高牌','一对','两对','三条','顺子','同花','葫芦','四条','同花顺'];

// ─────────────────────────────────────────────
// 边池计算
// ─────────────────────────────────────────────
function calcPots(players) {
  // players: [{id, totalBet, folded}]
  const bets = players.map(p => ({ id: p.id, bet: p.totalBet, folded: p.folded }));
  const pots = [];
  let remaining = bets.filter(b => b.bet > 0);

  while (remaining.length > 0) {
    const minBet = Math.min(...remaining.map(b => b.bet));
    const eligible = remaining.filter(b => !b.folded).map(b => b.id);
    const amount = minBet * remaining.length;
    pots.push({ amount, eligible });
    remaining.forEach(b => b.bet -= minBet);
    remaining = remaining.filter(b => b.bet > 0);
  }
  return pots;
}

// ─────────────────────────────────────────────
// 游戏状态机
// ─────────────────────────────────────────────
const PHASES = ['waiting', 'preflop', 'flop', 'turn', 'river', 'showdown'];
const SMALL_BLIND = 50, BIG_BLIND = 100;

class PokerRoom {
  constructor(id) {
    this.id = id;
    this.players = [];     // {id, name, chips, cards, totalBet, bet, folded, allIn, connected}
    this.deck = [];
    this.community = [];
    this.phase = 'waiting';
    this.pot = 0;
    this.currentBet = 0;
    this.dealerIdx = -1;
    this.actionIdx = -1;
    this.actionsThisRound = 0;
    this.sidePots = [];
    this.lastWinners = [];
    this.actionTimer = null;
    this.bettingRoundComplete = false;
    this._broadcastFn = null;
  }

  addPlayer(id, name) {
    if (this.players.find(p=>p.id===id)) return { ok: false, reason: 'already_joined' };
    if (this.players.length >= 9) return { ok: false, reason: 'room_full' };
    if (this.players.find(p=>p.name===name && p.connected)) return { ok: false, reason: 'name_taken' };
    this.players.push({
      id, name, chips: 2000,
      cards: [], totalBet: 0, bet: 0,
      folded: false, allIn: false, connected: true, seatIdx: this.players.length
    });
    // 第一个加入的玩家成为房主
    if (this.players.length === 1) this.hostId = id;
    return { ok: true };
  }

  removePlayer(id) {
    const idx = this.players.findIndex(p=>p.id===id);
    if (idx !== -1) this.players.splice(idx, 1);
    if (this.players.length === 0) {
      this.phase = 'waiting';
      this.hostId = null;
    } else if (this.hostId === id) {
      // 房主离开，转让给第一个在线玩家
      const next = this.players.find(p=>p.connected);
      this.hostId = next ? next.id : this.players[0].id;
    }
  }

  activePlayers() {
    return this.players.filter(p => !p.folded && !p.allIn && p.connected);
  }

  playersInHand() {
    return this.players.filter(p => !p.folded);
  }

  canStart() {
    return this.players.filter(p=>p.connected).length >= 2 && this.phase === 'waiting';
  }

  startGame() {
    if (!this.canStart()) return false;
    // 重置
    this.deck = shuffle(makeDeck());
    this.community = [];
    this.pot = 0;
    this.currentBet = 0;
    this.sidePots = [];
    this.lastWinners = [];
    this.phase = 'preflop';
    const active = this.players.filter(p=>p.connected && p.chips > 0);
    if (active.length < 2) return false;

    for (const p of this.players) {
      p.cards = []; p.totalBet = 0; p.bet = 0;
      p.folded = false; p.allIn = false;
    }

    // dealer
    this.dealerIdx = (this.dealerIdx + 1) % active.length;
    const dealerP = active[this.dealerIdx];
    dealerP.isDealer = true;

    // 发手牌
    for (const p of active) {
      p.cards = [this.deck.pop(), this.deck.pop()];
    }

    // 盲注
    const sbIdx = (this.dealerIdx + 1) % active.length;
    const bbIdx = (this.dealerIdx + 2) % active.length;
    this._postBlind(active[sbIdx], SMALL_BLIND);
    this._postBlind(active[bbIdx], BIG_BLIND);
    this.currentBet = BIG_BLIND;

    // 第一个行动者在bb之后
    this.actionIdx = (bbIdx + 1) % active.length;
    this.actionsThisRound = 0;
    this.bettingRoundComplete = false;

    return true;
  }

  _postBlind(player, amount) {
    const actual = Math.min(amount, player.chips);
    player.chips -= actual;
    player.bet = actual;
    player.totalBet = actual;
    this.pot += actual;
    if (player.chips === 0) player.allIn = true;
  }

  currentPlayer() {
    const active = this.players.filter(p=>p.connected && !p.folded && !p.allIn);
    if (active.length === 0) return null;
    return active[this.actionIdx % active.length];
  }

  processAction(playerId, action, amount) {
    const active = this.players.filter(p=>p.connected && !p.folded && !p.allIn);
    if (active.length === 0) return false;
    const p = active[this.actionIdx % active.length];
    if (!p || p.id !== playerId) return false;

    if (action === 'fold') {
      p.folded = true;
    } else if (action === 'check') {
      if (p.bet < this.currentBet) return false; // can't check
    } else if (action === 'call') {
      const toCall = Math.min(this.currentBet - p.bet, p.chips);
      p.chips -= toCall;
      p.bet += toCall;
      p.totalBet += toCall;
      this.pot += toCall;
      if (p.chips === 0) p.allIn = true;
    } else if (action === 'raise' || action === 'allin') {
      const raiseAmount = action === 'allin' ? p.chips : Math.min(amount, p.chips);
      const actual = Math.max(raiseAmount, this.currentBet - p.bet); // at least call
      p.chips -= actual;
      p.bet += actual;
      p.totalBet += actual;
      this.pot += actual;
      if (p.bet > this.currentBet) {
        this.currentBet = p.bet;
        this.actionsThisRound = 0; // reset - everyone must act again
      }
      if (p.chips === 0) p.allIn = true;
    }

    this.actionsThisRound++;

    // 移到下一个玩家
    const newActive = this.players.filter(p=>p.connected && !p.folded && !p.allIn);
    if (newActive.length === 0 || this._roundOver(newActive)) {
      this._nextPhase();
    } else {
      this.actionIdx = (this.actionIdx + 1) % newActive.length;
    }
    return true;
  }

  _roundOver(active) {
    // 所有人都下注到currentBet且至少行动过一次
    const allCalled = active.every(p => p.bet >= this.currentBet);
    return allCalled && this.actionsThisRound >= active.length;
  }

  _nextPhase() {
    const inHand = this.playersInHand();
    // 只剩一人
    if (inHand.length === 1) {
      this._awardPot(inHand);
      this.phase = 'showdown';
      return;
    }
    // 重置下注
    for (const p of this.players) p.bet = 0;
    this.currentBet = 0;
    this.actionsThisRound = 0;

    const phaseOrder = ['preflop','flop','turn','river','showdown'];
    const next = phaseOrder[phaseOrder.indexOf(this.phase)+1];

    if (next === 'flop') {
      this.deck.pop(); // burn
      this.community.push(this.deck.pop(), this.deck.pop(), this.deck.pop());
    } else if (next === 'turn' || next === 'river') {
      this.deck.pop();
      this.community.push(this.deck.pop());
    } else if (next === 'showdown') {
      this._showdown();
      this.phase = 'showdown';
      return;
    }

    this.phase = next;
    // 从dealer左边第一个active玩家开始
    const active = this.players.filter(p=>p.connected && !p.folded && !p.allIn);
    this.actionIdx = 0;

    // 如果所有未弃牌玩家都已全押（无人可行动），自动推进发完剩余公共牌
    const inHandNow = this.players.filter(p => !p.folded && p.connected);
    const allAllIn = inHandNow.length > 1 && active.length === 0;
    if (allAllIn) {
      setTimeout(() => {
        this._nextPhase();
        if (this._broadcastFn) this._broadcastFn(this);
      }, 1200);
    }
  }

  _showdown() {
    const inHand = this.playersInHand();
    // 评估每人牌型
    for (const p of inHand) {
      const all7 = [...p.cards, ...this.community];
      const res = evaluateHand(all7);
      p.handScore = res.score;
      p.bestHand = res.cards;
      p.handName = HAND_NAMES[res.score[0]];
    }
    // 边池分配
    const pots = calcPots(this.players.map(p=>({id:p.id, totalBet:p.totalBet, folded:p.folded})));
    this.lastWinners = [];

    for (const pot of pots) {
      const contenders = inHand.filter(p => pot.eligible.includes(p.id));
      if (contenders.length === 0) continue;
      let best = contenders[0];
      for (const p of contenders.slice(1)) {
        if (compareScore(p.handScore, best.handScore) > 0) best = p;
      }
      // 并列赢家
      const winners = contenders.filter(p => compareScore(p.handScore, best.handScore) === 0);
      const share = Math.floor(pot.amount / winners.length);
      const rem = pot.amount - share * winners.length;
      winners[0].chips += rem; // 余数给第一位
      for (const w of winners) {
        w.chips += share;
        this.lastWinners.push({ id: w.id, name: w.name, amount: share + (w===winners[0]?rem:0), handName: w.handName });
      }
    }

    // 淘汰筹码为0的玩家
    this.players = this.players.filter(p => p.chips > 0 || !p.connected === false);
  }

  _awardPot(winners) {
    const share = Math.floor(this.pot / winners.length);
    this.lastWinners = [];
    for (const w of winners) {
      w.chips += share;
      this.lastWinners.push({ id: w.id, name: w.name, amount: share, handName: '对手弃牌' });
    }
    this.pot = 0;
  }

  stateFor(playerId) {
    return {
      phase: this.phase,
      pot: this.pot,
      community: this.community,
      currentBet: this.currentBet,
      dealerIdx: this.dealerIdx,
      currentPlayerId: this.currentPlayer()?.id || null,
      lastWinners: this.lastWinners,
      hostId: this.hostId || null,
      players: this.players.map(p => ({
        id: p.id, name: p.name, chips: p.chips,
        bet: p.bet, totalBet: p.totalBet,
        folded: p.folded, allIn: p.allIn,
        connected: p.connected, seatIdx: p.seatIdx,
        isDealer: p.isDealer || false,
        handName: p.handName || null,
        // 只给自己看手牌，摊牌阶段全看
        cards: (p.id === playerId || this.phase === 'showdown') ? p.cards : (p.cards.length ? ['?','?'] : []),
      }))
    };
  }
}

// ─────────────────────────────────────────────
// 房间管理
// ─────────────────────────────────────────────
const rooms = {};

function getOrCreateRoom(roomId) {
  if (!rooms[roomId]) rooms[roomId] = new PokerRoom(roomId);
  return rooms[roomId];
}

function broadcastState(room) {
  room._broadcastFn = broadcastState;
  for (const p of room.players) {
    io.to(p.id).emit('gameState', room.stateFor(p.id));
  }
  // 发给旁观者
  io.to(`room:${room.id}`).emit('gameState', room.stateFor(null));
}

// ─────────────────────────────────────────────
// Socket 事件
// ─────────────────────────────────────────────
io.on('connection', (socket) => {
  console.log('连接:', socket.id);

  socket.on('joinRoom', ({ roomId, name }) => {
    const room = getOrCreateRoom(roomId);
    socket.join(`room:${roomId}`);
    socket.roomId = roomId;
    socket.playerName = name;

    const result = room.addPlayer(socket.id, name);
    if (result.ok) {
      console.log(`${name} 加入房间 ${roomId}`);
      io.to(`room:${roomId}`).emit('playerJoined', { id: socket.id, name });
      broadcastState(room);
    } else {
      const msg = result.reason === 'name_taken' ? `昵称"${name}"已被使用，请换一个` :
                  result.reason === 'room_full'   ? '房间已满（最多9人）' : '加入失败';
      socket.emit('joinError', msg);
    }
  });

  socket.on('leaveRoom', () => {
    const room = rooms[socket.roomId];
    if (!room) return;
    const p = room.players.find(p=>p.id===socket.id);
    if (p) {
      // 游戏进行中自动fold
      if (room.phase !== 'waiting' && room.phase !== 'showdown') {
        const cur = room.currentPlayer();
        if (cur && cur.id === socket.id) room.processAction(socket.id, 'fold');
      }
      room.removePlayer(socket.id);
      io.to(`room:${socket.roomId}`).emit('playerLeft', { id: socket.id, name: p.name });
      broadcastState(room);
      console.log(`${p.name} 离开房间 ${socket.roomId}`);
    }
    socket.leave(`room:${socket.roomId}`);
    socket.roomId = null;
    socket.playerName = null;
    socket.emit('leftRoom');
  });

  socket.on('startGame', () => {
    const room = rooms[socket.roomId];
    if (!room) return;
    if (room.startGame()) {
      console.log(`房间 ${socket.roomId} 开局`);
      broadcastState(room);
    } else {
      socket.emit('error', '无法开始，需要至少2名玩家');
    }
  });

  socket.on('action', ({ action, amount }) => {
    const room = rooms[socket.roomId];
    if (!room) return;
    const ok = room.processAction(socket.id, action, amount);
    if (ok) {
      console.log(`${socket.playerName} -> ${action} ${amount||''}`);
      broadcastState(room);

      // 摊牌后等待3秒自动开下一局
      if (room.phase === 'showdown') {
        setTimeout(() => {
          if (room.players.filter(p=>p.connected).length >= 2) {
            room.phase = 'waiting';
            room.startGame();
            broadcastState(room);
          } else {
            room.phase = 'waiting';
            broadcastState(room);
          }
        }, 4000);
      }
    } else {
      socket.emit('error', '非法操作');
    }
  });

  socket.on('disconnect', () => {
    const room = rooms[socket.roomId];
    if (room) {
      const p = room.players.find(p=>p.id===socket.id);
      if (p) {
        p.connected = false;
        io.to(`room:${socket.roomId}`).emit('playerLeft', { id: socket.id, name: p.name });
        // 如果是当前行动玩家，自动fold
        const cur = room.currentPlayer();
        if (cur && cur.id === socket.id) {
          room.processAction(socket.id, 'fold');
        }
        broadcastState(room);
      }
    }
    console.log('断线:', socket.id);
  });
});

const PORT = 3000;
server.listen(PORT, () => console.log(`德州扑克服务器运行于 http://localhost:${PORT}`));
