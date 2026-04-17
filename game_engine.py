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
    has_acted: bool = False  # tracks if player acted in current betting round

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
    if len(cards) < 2:
        return (HandRank.HIGH_CARD, [0])
    best = None
    for combo in combinations(cards, min(5, len(cards))):
        score = _score_five(list(combo))
        if best is None or (score[0].rank_value, score[1]) > (best[0].rank_value, best[1]):
            best = score
    return best


def _score_five(cards: List[Card]) -> Tuple[HandRank, List[int]]:
    ranks = sorted([c.rank.rank_value for c in cards], reverse=True)
    suits = [c.suit for c in cards]
    is_flush = len(set(suits)) == 1
    is_straight = (len(set(ranks)) == 5 and ranks[0] - ranks[4] == 4)
    # Ace-low straight: A-2-3-4-5
    is_ace_low_straight = set(ranks) == {14, 2, 3, 4, 5}
    if is_ace_low_straight:
        is_straight = True
        ranks = [5, 4, 3, 2, 1]  # treat ace as 1 for ordering

    from collections import Counter
    counts = Counter(ranks)
    freq = sorted(counts.values(), reverse=True)
    groups = sorted(counts.keys(), key=lambda r: (counts[r], r), reverse=True)

    if is_straight and is_flush:
        # Royal flush: A-K-Q-J-10 (ranks[0]==14 before ace-low adjustment)
        if not is_ace_low_straight and ranks[0] == 14:
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
            p.has_acted = False

        self.deck = Deck()
        self.community_cards = []
        self.pot = 0
        self.side_pots = []
        self.current_bet = 0
        self.winners = []
        self.hand_history = []
        self.phase = GamePhase.PREFLOP

        # Move dealer button - advance to next player with chips
        n = len(self.players)
        self.dealer_idx = (self.dealer_idx + 1) % n
        attempts = 0
        while self.players[self.dealer_idx].chips == 0:
            self.dealer_idx = (self.dealer_idx + 1) % n
            attempts += 1
            if attempts >= n:
                return False  # no valid dealer

        # Post blinds
        active = [p for p in self.players if not p.folded]
        n_active = len(active)
        dealer_pos = active.index(self.players[self.dealer_idx])

        sb_pos = (dealer_pos + 1) % n_active if n_active > 2 else dealer_pos
        bb_pos = (dealer_pos + 2) % n_active if n_active > 2 else (dealer_pos + 1) % n_active

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
        # BB gets option (last_raiser_idx = bb index in players list)
        utg_pos = (bb_pos + 1) % n_active
        self.current_player_idx = self.players.index(active[utg_pos])
        self.last_raiser_idx = self.players.index(bb_player)

        self.hand_history.append(
            f"新一局开始 | 庄家:{self.players[self.dealer_idx].name} "
            f"小盲:{sb_player.name}({self.small_blind}) 大盲:{bb_player.name}({self.big_blind})"
        )
        return True

    def _post_blind(self, player: Player, amount: int):
        amount = min(amount, player.chips)
        player.chips -= amount
        player.bet += amount
        player.total_bet += amount
        self.pot += amount
        if player.chips == 0:
            player.all_in = True

    def current_player(self) -> Optional[Player]:
        if 0 <= self.current_player_idx < len(self.players):
            return self.players[self.current_player_idx]
        return None

    def _is_valid_actor(self, player_id: str) -> Optional["Player"]:
        """Return player if they are the current actor, else None."""
        p = self.get_player(player_id)
        if not p:
            return None
        cur = self.current_player()
        if not cur or p.id != cur.id:
            return None
        if p.folded or p.all_in:
            return None
        if self.phase in (GamePhase.WAITING, GamePhase.SHOWDOWN):
            return None
        return p

    def action_fold(self, player_id: str) -> bool:
        p = self._is_valid_actor(player_id)
        if not p:
            return False
        p.folded = True
        p.has_acted = True
        self.hand_history.append(f"{p.name} 弃牌")
        self._advance()
        return True

    def action_call(self, player_id: str) -> bool:
        p = self._is_valid_actor(player_id)
        if not p:
            return False
        to_call = min(self.current_bet - p.bet, p.chips)
        p.chips -= to_call
        p.bet += to_call
        p.total_bet += to_call
        self.pot += to_call
        if p.chips == 0:
            p.all_in = True
        p.has_acted = True
        self.hand_history.append(f"{p.name} 跟注 {to_call}")
        self._advance()
        return True

    def action_check(self, player_id: str) -> bool:
        p = self._is_valid_actor(player_id)
        if not p:
            return False
        if p.bet < self.current_bet:
            return False
        p.has_acted = True
        self.hand_history.append(f"{p.name} 过牌")
        self._advance()
        return True

    def action_raise(self, player_id: str, amount: int) -> bool:
        p = self._is_valid_actor(player_id)
        if not p:
            return False
        total_raise = min(amount, p.chips + p.bet)
        if total_raise <= self.current_bet and p.chips > 0:
            # Can't raise (not enough to beat current_bet), treat as all-in call if going all-in
            if total_raise == p.chips + p.bet:  # they're actually going all-in
                return self.action_call(player_id)
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
        p.has_acted = True
        # Reset has_acted for others who need to respond to the raise
        for other in self.players:
            if other.id != p.id and not other.folded and not other.all_in:
                other.has_acted = False
        self.hand_history.append(f"{p.name} 加注至 {total_raise}")
        self._advance()
        return True

    def _advance(self):
        """Determine next player to act, or advance to next phase."""
        # Players who can still act (not folded, not all-in, has cards)
        can_act = [p for p in self.players if not p.folded and not p.all_in and len(p.hole_cards) >= 2]
        # Players still in the hand (not folded, has cards)
        in_hand = [p for p in self.players if not p.folded and len(p.hole_cards) >= 2]

        # If 0 or 1 players remain in hand, end the hand
        if len(in_hand) <= 1:
            if len(in_hand) == 1:
                self._award_pot(in_hand)
            else:
                all_players = [p for p in self.players]
                if all_players:
                    self._award_pot([max(all_players, key=lambda x: x.total_bet)])
            self.phase = GamePhase.SHOWDOWN
            return

        # If no one can act (everyone remaining is all-in), go to next phase
        if len(can_act) == 0:
            self._next_phase()
            return

        # Find next player who still needs to act
        # A player needs to act if:
        # - they haven't acted yet this round (has_acted=False), OR
        # - they have acted but bet < current_bet (shouldn't happen normally, edge case)
        n = len(self.players)
        idx = (self.current_player_idx + 1) % n

        for _ in range(n):
            p = self.players[idx]
            if not p.folded and not p.all_in and len(p.hole_cards) >= 2:
                if not p.has_acted or p.bet < self.current_bet:
                    self.current_player_idx = idx
                    return
            idx = (idx + 1) % n

        # No one needs to act - round is over
        self._next_phase()

    def _next_phase(self):
        # Reset bets for new street
        for p in self.players:
            p.bet = 0
            p.has_acted = False
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
        else:
            # Already at showdown or unknown phase
            self.phase = GamePhase.SHOWDOWN
            self._showdown()
            return

        # Set first to act (first active non-all-in player after dealer)
        can_act = [p for p in self.players if not p.folded and not p.all_in and len(p.hole_cards) >= 2]
        if len(can_act) == 0:
            # Everyone remaining is all-in, run out the board
            while len(self.community_cards) < 5:
                self.community_cards.append(self.deck.deal())
            self.phase = GamePhase.SHOWDOWN
            self._showdown()
            return

        n = len(self.players)
        idx = (self.dealer_idx + 1) % n
        attempts = 0
        while self.players[idx].folded or self.players[idx].all_in or len(self.players[idx].hole_cards) < 2:
            idx = (idx + 1) % n
            attempts += 1
            if attempts >= n:
                # No one can act
                self.phase = GamePhase.SHOWDOWN
                self._showdown()
                return

        self.current_player_idx = idx
        # last_raiser_idx = first to act (so when we come full circle back, round ends)
        self.last_raiser_idx = idx

    def _showdown(self):
        active = [p for p in self.players if not p.folded and len(p.hole_cards) >= 2]
        if len(active) == 0:
            # Fallback: anyone not folded
            active = [p for p in self.players if not p.folded]
        if len(active) == 0:
            # Edge case: everyone folded (shouldn't normally happen)
            return
        if len(active) == 1:
            self._award_pot(active)
            return

        # Calculate side pots for all-in situations
        self._calculate_and_award_side_pots(active)

    def _calculate_and_award_side_pots(self, active: List["Player"]):
        """Award pot(s) considering all-in side pots."""
        # Evaluate hands
        hand_scores = {}
        for p in active:
            all_cards = p.hole_cards + self.community_cards
            score = evaluate_hand(all_cards)
            hand_scores[p.id] = score
            self.hand_history.append(f"{p.name}: {score[0].name_cn}")

        # Build side pots from total_bet amounts
        # Sort by total_bet to find contribution levels
        all_in_players = sorted(
            [p for p in self.players if p.all_in or p.total_bet > 0],
            key=lambda x: x.total_bet
        )

        # Gather all players who contributed (including folded)
        contributors = [p for p in self.players if p.total_bet > 0]
        if not contributors:
            return

        # Create pots: for each level of commitment, calculate eligible players and pot size
        pots = []
        prev_level = 0
        processed_levels = set()

        # All unique total_bet levels from all-in players
        levels = sorted(set(p.total_bet for p in self.players if p.all_in and p.total_bet > 0))

        remaining_contributors = list(self.players)  # use all players for pot calculation

        if not levels:
            # No all-ins, simple case: all active players eligible for main pot
            pots.append((self.pot, active))
        else:
            pot_remaining = self.pot
            prev = 0
            for level in levels:
                amount_per_player = level - prev
                # Count players who contributed at least this level
                eligible_contrib = [p for p in self.players if p.total_bet >= level]
                pot_size = amount_per_player * len(eligible_contrib)
                pot_size = min(pot_size, pot_remaining)
                # Eligible to win this pot: active players who contributed at least this level
                eligible_win = [p for p in active if p.total_bet >= level]
                if eligible_win and pot_size > 0:
                    pots.append((pot_size, eligible_win))
                pot_remaining -= pot_size
                prev = level

            # Remaining pot (beyond all-in levels): only non-all-in active players eligible
            if pot_remaining > 0:
                top_eligible = [p for p in active if not p.all_in]
                if not top_eligible:
                    top_eligible = active  # fallback
                pots.append((pot_remaining, top_eligible))

        # Award each pot to best hand among eligible players
        self.winners = []
        for pot_amount, eligible in pots:
            if pot_amount <= 0 or not eligible:
                continue
            best_score = max(
                (hand_scores[p.id] for p in eligible),
                key=lambda s: (s[0].rank_value, s[1])
            )
            pot_winners = [p for p in eligible if (hand_scores[p.id][0].rank_value, hand_scores[p.id][1]) == (best_score[0].rank_value, best_score[1])]
            split = pot_amount // len(pot_winners)
            remainder = pot_amount % len(pot_winners)
            for i, w in enumerate(pot_winners):
                gain = split + (remainder if i == 0 else 0)
                w.chips += gain
                self.winners.append({
                    "id": w.id,
                    "name": w.name,
                    "gain": gain,
                    "hand": best_score[0].name_cn,
                    "chips": w.chips,
                })
                self.hand_history.append(
                    f"🏆 {w.name} 赢得 {gain} 筹码 ({best_score[0].name_cn})"
                )

        self.pot = 0

    def _award_pot(self, ranked_players: List["Player"], results=None):
        """Simple pot award (used when only 1 player remains)."""
        if not ranked_players:
            return
        w = ranked_players[0]
        w.chips += self.pot
        self.winners.append({
            "id": w.id,
            "name": w.name,
            "gain": self.pot,
            "hand": "最后存活",
            "chips": w.chips,
        })
        self.hand_history.append(f"🏆 {w.name} 赢得 {self.pot} 筹码")
        self.pot = 0

    def get_state(self, viewer_id: str = None) -> dict:
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
