"""
Texas Hold'em Poker Game Engine
"""
import random
from enum import Enum
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass, field


class Suit(Enum):
    SPADES = "♠"
    HEARTS = "♥"
    DIAMONDS = "♦"
    CLUBS = "♣"


class Rank(Enum):
    TWO = (2, "2")
    THREE = (3, "3")
    FOUR = (4, "4")
    FIVE = (5, "5")
    SIX = (6, "6")
    SEVEN = (7, "7")
    EIGHT = (8, "8")
    NINE = (9, "9")
    TEN = (10, "10")
    JACK = (11, "J")
    QUEEN = (12, "Q")
    KING = (13, "K")
    ACE = (14, "A")

    def __init__(self, value, symbol):
        self.rank_value = value
        self.symbol = symbol


@dataclass
class Card:
    rank: Rank
    suit: Suit

    def __str__(self):
        return f"{self.rank.symbol}{self.suit.value}"

    def to_dict(self):
        return {"rank": self.rank.symbol, "suit": self.suit.value, "str": str(self)}


class HandRank(Enum):
    HIGH_CARD = (1, "高牌")
    ONE_PAIR = (2, "一对")
    TWO_PAIR = (3, "两对")
    THREE_OF_A_KIND = (4, "三条")
    STRAIGHT = (5, "顺子")
    FLUSH = (6, "同花")
    FULL_HOUSE = (7, "葫芦")
    FOUR_OF_A_KIND = (8, "四条")
    STRAIGHT_FLUSH = (9, "同花顺")
    ROYAL_FLUSH = (10, "皇家同花顺")

    def __init__(self, value, name_cn):
        self.rank_value = value
        self.name_cn = name_cn


class GamePhase(Enum):
    WAITING = "waiting"
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


@dataclass
class Player:
    id: str
    name: str
    chips: int = 1000
    hole_cards: List[Card] = field(default_factory=list)
    bet: int = 0
    total_bet: int = 0
    folded: bool = False
    all_in: bool = False
    is_ready: bool = False
    avatar: str = ""

    def to_dict(self, show_cards=False):
        return {
            "id": self.id,
            "name": self.name,
            "chips": self.chips,
            "bet": self.bet,
            "total_bet": self.total_bet,
            "folded": self.folded,
            "all_in": self.all_in,
            "is_ready": self.is_ready,
            "avatar": self.avatar,
            "cards": [c.to_dict() for c in self.hole_cards] if show_cards else (
                [{"hidden": True} for _ in self.hole_cards]
            ),
            "card_count": len(self.hole_cards),
        }


class Deck:
    def __init__(self):
        self.cards = [Card(rank, suit) for suit in Suit for rank in Rank]
        self.shuffle()

    def shuffle(self):
        random.shuffle(self.cards)

    def deal(self) -> Card:
        return self.cards.pop()


def evaluate_hand(cards: List[Card]) -> Tuple[HandRank, List[int]]:
    """Evaluate best 5-card hand from up to 7 cards."""
    from itertools import combinations
    best = None
    for combo in combinations(cards, min(5, len(cards))):
        score = _score_five(list(combo))
        if best is None or score > best:
            best = score
    return best


def _score_five(cards: List[Card]) -> Tuple[HandRank, List[int]]:
    ranks = sorted([c.rank.rank_value for c in cards], reverse=True)
    suits = [c.suit for c in cards]
    is_flush = len(set(suits)) == 1
    is_straight = (len(set(ranks)) == 5 and ranks[0] - ranks[4] == 4)
    # Ace-low straight
    if set(ranks) == {14, 2, 3, 4, 5}:
        is_straight = True
        ranks = [5, 4, 3, 2, 1]

    from collections import Counter
    counts = Counter(ranks)
    freq = sorted(counts.values(), reverse=True)
    groups = sorted(counts.keys(), key=lambda r: (counts[r], r), reverse=True)

    if is_straight and is_flush:
        if ranks[0] == 14:
            return (HandRank.ROYAL_FLUSH, ranks)
        return (HandRank.STRAIGHT_FLUSH, ranks)
    if freq[0] == 4:
        return (HandRank.FOUR_OF_A_KIND, groups)
    if freq[0] == 3 and freq[1] == 2:
        return (HandRank.FULL_HOUSE, groups)
    if is_flush:
        return (HandRank.FLUSH, ranks)
    if is_straight:
        return (HandRank.STRAIGHT, ranks)
    if freq[0] == 3:
        return (HandRank.THREE_OF_A_KIND, groups)
    if freq[0] == 2 and freq[1] == 2:
        return (HandRank.TWO_PAIR, groups)
    if freq[0] == 2:
        return (HandRank.ONE_PAIR, groups)
    return (HandRank.HIGH_CARD, ranks)


class PokerGame:
    def __init__(self, room_id: str, small_blind: int = 10, big_blind: int = 20):
        self.room_id = room_id
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.players: List[Player] = []
        self.deck: Optional[Deck] = None
        self.community_cards: List[Card] = []
        self.phase = GamePhase.WAITING
        self.pot = 0
        self.side_pots: List[Dict] = []
        self.current_bet = 0
        self.dealer_idx = 0
        self.current_player_idx = 0
        self.last_raiser_idx = -1
        self.hand_history: List[str] = []
        self.winners: List[Dict] = []

    def add_player(self, player_id: str, name: str, chips: int = 1000) -> bool:
        if len(self.players) >= 10:
            return False
        if any(p.id == player_id for p in self.players):
            return False
        avatar = f"https://api.dicebear.com/7.x/avataaars/svg?seed={player_id}"
        self.players.append(Player(player_id, name, chips, avatar=avatar))
        return True

    def remove_player(self, player_id: str):
        self.players = [p for p in self.players if p.id != player_id]

    def get_player(self, player_id: str) -> Optional[Player]:
        return next((p for p in self.players if p.id == player_id), None)

    def set_ready(self, player_id: str):
        p = self.get_player(player_id)
        if p:
            p.is_ready = True

    def can_start(self) -> bool:
        active = [p for p in self.players if p.is_ready]
        return len(active) >= 2

    def start_hand(self) -> bool:
        active = [p for p in self.players if p.chips > 0]
        if len(active) < 2:
            return False

        # Reset
        for p in self.players:
            p.hole_cards = []
            p.bet = 0
            p.total_bet = 0
            p.folded = p.chips == 0
            p.all_in = False

        self.deck = Deck()
        self.community_cards = []
        self.pot = 0
        self.side_pots = []
        self.current_bet = 0
        self.winners = []
        self.hand_history = []
        self.phase = GamePhase.PREFLOP

        # Move dealer button
        active_ids = [p.id for p in self.players if not p.folded]
        self.dealer_idx = (self.dealer_idx + 1) % len(self.players)
        while self.players[self.dealer_idx].chips == 0:
            self.dealer_idx = (self.dealer_idx + 1) % len(self.players)

        # Post blinds
        active = [p for p in self.players if not p.folded]
        n = len(active)
        dealer_pos = active.index(self.players[self.dealer_idx]) if self.players[self.dealer_idx] in active else 0

        sb_pos = (dealer_pos + 1) % n if n > 2 else dealer_pos
        bb_pos = (dealer_pos + 2) % n if n > 2 else (dealer_pos + 1) % n

        sb_player = active[sb_pos]
        bb_player = active[bb_pos]

        self._post_blind(sb_player, self.small_blind)
        self._post_blind(bb_player, self.big_blind)
        self.current_bet = self.big_blind

        # Deal 2 hole cards each
        for _ in range(2):
            for p in active:
                p.hole_cards.append(self.deck.deal())

        # First to act preflop is after BB
        self.current_player_idx = self.players.index(active[(bb_pos + 1) % n])
        self.last_raiser_idx = self.players.index(bb_player)

        self.hand_history.append(f"新一局开始 | 庄家:{self.players[self.dealer_idx].name} 小盲:{sb_player.name}({self.small_blind}) 大盲:{bb_player.name}({self.big_blind})")
        return True

    def _post_blind(self, player: Player, amount: int):
        amount = min(amount, player.chips)
        player.chips -= amount
        player.bet += amount
        player.total_bet += amount
        self.pot += amount
        if player.chips == 0:
            player.all_in = True

    def _next_active_player(self):
        n = len(self.players)
        idx = (self.current_player_idx + 1) % n
        while idx != self.current_player_idx:
            p = self.players[idx]
            if not p.folded and not p.all_in:
                self.current_player_idx = idx
                return
            idx = (idx + 1) % n

    def current_player(self) -> Optional[Player]:
        if 0 <= self.current_player_idx < len(self.players):
            return self.players[self.current_player_idx]
        return None

    def action_fold(self, player_id: str) -> bool:
        p = self.get_player(player_id)
        if not p or p.id != self.current_player().id:
            return False
        p.folded = True
        self.hand_history.append(f"{p.name} 弃牌")
        self._advance()
        return True

    def action_call(self, player_id: str) -> bool:
        p = self.get_player(player_id)
        if not p or p.id != self.current_player().id:
            return False
        to_call = min(self.current_bet - p.bet, p.chips)
        p.chips -= to_call
        p.bet += to_call
        p.total_bet += to_call
        self.pot += to_call
        if p.chips == 0:
            p.all_in = True
        self.hand_history.append(f"{p.name} 跟注 {to_call}")
        self._advance()
        return True

    def action_check(self, player_id: str) -> bool:
        p = self.get_player(player_id)
        if not p or p.id != self.current_player().id:
            return False
        if p.bet < self.current_bet:
            return False
        self.hand_history.append(f"{p.name} 过牌")
        self._advance()
        return True

    def action_raise(self, player_id: str, amount: int) -> bool:
        p = self.get_player(player_id)
        if not p or p.id != self.current_player().id:
            return False
        total_raise = min(amount, p.chips + p.bet)
        if total_raise <= self.current_bet:
            return False
        add = total_raise - p.bet
        p.chips -= add
        p.bet = total_raise
        p.total_bet += add
        self.pot += add
        self.current_bet = total_raise
        if p.chips == 0:
            p.all_in = True
        self.last_raiser_idx = self.players.index(p)
        self.hand_history.append(f"{p.name} 加注至 {total_raise}")
        self._advance()
        return True

    def _advance(self):
        active = [p for p in self.players if not p.folded and not p.all_in]
        all_active = [p for p in self.players if not p.folded]

        # Check if only one player left
        if len(all_active) == 1:
            self._award_pot(all_active)
            self.phase = GamePhase.SHOWDOWN
            return

        # Check if betting round is over
        n = len(self.players)
        next_idx = (self.current_player_idx + 1) % n
        while next_idx != self.current_player_idx:
            p = self.players[next_idx]
            if not p.folded and not p.all_in:
                # Has this player matched the current bet?
                if p.bet < self.current_bet or next_idx == self.last_raiser_idx:
                    if p.bet < self.current_bet:
                        self.current_player_idx = next_idx
                        return
                    # We've gone around
                    break
            next_idx = (next_idx + 1) % n

        # Check if everyone matched or no active players
        unmatched = [p for p in active if p.bet < self.current_bet]
        # Find next player who needs to act
        found = False
        idx = (self.current_player_idx + 1) % n
        start = idx
        while True:
            p = self.players[idx]
            if not p.folded and not p.all_in:
                if p.bet < self.current_bet:
                    self.current_player_idx = idx
                    found = True
                    break
                if idx == self.last_raiser_idx:
                    break
            idx = (idx + 1) % n
            if idx == start:
                break

        if not found:
            self._next_phase()

    def _next_phase(self):
        # Reset bets
        for p in self.players:
            p.bet = 0
        self.current_bet = 0

        if self.phase == GamePhase.PREFLOP:
            self.phase = GamePhase.FLOP
            for _ in range(3):
                self.community_cards.append(self.deck.deal())
        elif self.phase == GamePhase.FLOP:
            self.phase = GamePhase.TURN
            self.community_cards.append(self.deck.deal())
        elif self.phase == GamePhase.TURN:
            self.phase = GamePhase.RIVER
            self.community_cards.append(self.deck.deal())
        elif self.phase == GamePhase.RIVER:
            self.phase = GamePhase.SHOWDOWN
            self._showdown()
            return

        # Set first to act (first active after dealer)
        active = [p for p in self.players if not p.folded and not p.all_in]
        if len(active) <= 1:
            # Run out remaining cards and showdown
            while len(self.community_cards) < 5:
                self.community_cards.append(self.deck.deal())
            self.phase = GamePhase.SHOWDOWN
            self._showdown()
            return

        n = len(self.players)
        idx = (self.dealer_idx + 1) % n
        while self.players[idx].folded:
            idx = (idx + 1) % n
        self.current_player_idx = idx
        self.last_raiser_idx = idx

    def _showdown(self):
        active = [p for p in self.players if not p.folded]
        if len(active) == 1:
            self._award_pot(active)
            return

        # Evaluate hands
        results = []
        for p in active:
            all_cards = p.hole_cards + self.community_cards
            score = evaluate_hand(all_cards)
            results.append((p, score))
            self.hand_history.append(f"{p.name}: {score[0].name_cn}")

        # Sort by hand strength
        results.sort(key=lambda x: (x[1][0].rank_value, x[1][1]), reverse=True)
        self._award_pot([r[0] for r in results], results)

    def _award_pot(self, ranked_players: List[Player], results=None):
        # Simple pot split (no side pots for now)
        if not ranked_players:
            return

        best_score = results[0][1] if results else None
        winners = []
        if results:
            winners = [r[0] for r in results if r[1] == best_score]
        else:
            winners = ranked_players[:1]

        split = self.pot // len(winners)
        remainder = self.pot % len(winners)

        for i, w in enumerate(winners):
            gain = split + (remainder if i == 0 else 0)
            w.chips += gain
            self.winners.append({
                "id": w.id,
                "name": w.name,
                "gain": gain,
                "hand": results[0][1][0].name_cn if results else "最后存活",
                "chips": w.chips,
            })
            self.hand_history.append(f"🏆 {w.name} 赢得 {gain} 筹码" + (f" ({results[0][1][0].name_cn})" if results else ""))

        self.pot = 0

    def get_state(self, viewer_id: str = None) -> dict:
        active = [p for p in self.players if not p.folded]
        cur = self.current_player()
        return {
            "room_id": self.room_id,
            "phase": self.phase.value,
            "pot": self.pot,
            "current_bet": self.current_bet,
            "community_cards": [c.to_dict() for c in self.community_cards],
            "players": [
                p.to_dict(show_cards=(self.phase == GamePhase.SHOWDOWN or p.id == viewer_id))
                for p in self.players
            ],
            "current_player_id": cur.id if cur else None,
            "dealer_idx": self.dealer_idx,
            "winners": self.winners,
            "hand_history": self.hand_history[-10:],
            "small_blind": self.small_blind,
            "big_blind": self.big_blind,
        }
