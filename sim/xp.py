"""XP curve, quest XP color decay, mob-kill XP, and level progression math for WoW Forever."""

from __future__ import annotations

import math
from typing import Dict, Tuple

from .models import CharacterState, Quest

# Verified Classic / WoW Forever 1..59 XP-to-next-level table (total 4,084,700 XP to reach 60).
DEFAULT_XP_CURVE: Dict[int, int] = {
    1: 400,
    2: 900,
    3: 1400,
    4: 2100,
    5: 2800,
    6: 3600,
    7: 4500,
    8: 5400,
    9: 6500,
    10: 7600,
    11: 8800,
    12: 10100,
    13: 11400,
    14: 12900,
    15: 14400,
    16: 16000,
    17: 17700,
    18: 19400,
    19: 21300,
    20: 23200,
    21: 25200,
    22: 27300,
    23: 29400,
    24: 31700,
    25: 34000,
    26: 36400,
    27: 38900,
    28: 41400,
    29: 44300,
    30: 47400,
    31: 50800,
    32: 54500,
    33: 58600,
    34: 62800,
    35: 67100,
    36: 71600,
    37: 76100,
    38: 80800,
    39: 85700,
    40: 90700,
    41: 95800,
    42: 101000,
    43: 106300,
    44: 111800,
    45: 117500,
    46: 123200,
    47: 129100,
    48: 135100,
    49: 141200,
    50: 147500,
    51: 153900,
    52: 160400,
    53: 167100,
    54: 173900,
    55: 180800,
    56: 187900,
    57: 195000,
    58: 202300,
    59: 209800,
}

MAX_LEVEL = 60


# Rules: Encapsulates WoW Forever quest difficulty colors, quest XP decay, mob kill XP,
# and level-up rollover mechanics without mutating external reference tables.
class XPModel:
    xp_curve: Dict[int, int]
    max_level: int

    def __init__(self, xp_curve: Dict[int, int] | None = None, max_level: int = MAX_LEVEL) -> None:
        self.xp_curve = dict(DEFAULT_XP_CURVE)
        if xp_curve:
            for lvl, amount in xp_curve.items():
                int_lvl = int(lvl)
                if 1 <= int_lvl < max_level and int(amount) > 0:
                    self.xp_curve[int_lvl] = int(amount)
        self.max_level = max_level

    def xp_for_level(self, level: int) -> int:
        if level >= self.max_level:
            return 0
        clamped = max(1, min(self.max_level - 1, int(level)))
        return self.xp_curve[clamped]

    @staticmethod
    def quest_green_range(player_level: int) -> int:
        """Classic/Forever green-quest cutoff distance below player level."""
        if player_level <= 5:
            return 4
        if player_level <= 9:
            return 5
        if player_level <= 19:
            return 6
        if player_level <= 29:
            return 7
        if player_level <= 39:
            return 8
        return 9

    def quest_color(self, quest_level: int, player_level: int) -> str:
        effective_q_level = player_level if quest_level <= 0 else quest_level
        diff = effective_q_level - player_level
        if diff >= 5:
            return "red"
        if diff >= 3:
            return "orange"
        if diff >= -2:
            return "yellow"
        if -diff <= self.quest_green_range(player_level):
            return "green"
        return "grey"

    def adjusted_quest_xp(self, base_xp: int, quest_level: int, player_level: int) -> int:
        """Matches Questie/cmangos WoW Forever quest XP decay and Blizzard rounding."""
        if player_level >= self.max_level or base_xp <= 0:
            return 0
        effective_q_level = player_level if quest_level <= 0 else quest_level
        xp_multiplier = 2 * (effective_q_level - player_level) + 20
        xp_multiplier = max(1, min(10, xp_multiplier))
        scaled = base_xp * xp_multiplier / 10.0

        if scaled <= 100:
            rounded = 5 * math.floor((scaled + 2) / 5)
        elif scaled <= 500:
            rounded = 10 * math.floor((scaled + 5) / 10)
        elif scaled <= 1000:
            rounded = 25 * math.floor((scaled + 12) / 25)
        else:
            rounded = 50 * math.floor((scaled + 25) / 50)
        return max(0, int(rounded))

    @staticmethod
    def base_mob_xp(mob_level: int, player_level: int) -> int:
        """Classic/Forever per-mob solo kill XP formula with grey mob cutoff."""
        if player_level >= MAX_LEVEL:
            return 0
        gray_level = player_level - XPModel.quest_green_range(player_level) - 1
        if mob_level <= gray_level:
            return 0
        raw = 45 + 5 * mob_level
        if mob_level < player_level:
            zd = 5 + (player_level // 10) * 2
            factor = max(0.0, 1.0 - (player_level - mob_level) / zd)
            return max(0, int(round(raw * factor)))
        if mob_level > player_level:
            bonus = min(4, mob_level - player_level) * 0.05
            return int(round(raw * (1.0 + bonus)))
        return raw

    @staticmethod
    def is_talk_or_breadcrumb(quest: Quest) -> bool:
        if quest.is_breadcrumb:
            return True
        if quest.quest_kind in ("talk", "delivery", "breadcrumb"):
            return True
        # Breadcrumb/delivery quests typically have small base XP relative to level and no objective coords
        expected_full = 450 + quest.quest_level * 85
        if not quest.objective_coords and quest.xp <= expected_full * 0.42 and not quest.is_dungeon:
            return True
        return False

    def estimate_quest_mob_kills_and_xp(self, quest: Quest, player_level: int) -> Tuple[int, int]:
        """Estimate mob kills and solo/party kill XP earned while clearing quest objectives."""
        if player_level >= self.max_level:
            return 0, 0
        if self.is_talk_or_breadcrumb(quest):
            return 0, 0

        q_lvl = quest.quest_level if quest.quest_level > 0 else player_level
        per_mob = self.base_mob_xp(q_lvl, player_level)
        if quest.is_dungeon:
            # Dungeon quests happen inside a 5-man run where group XP modifier yields ~0.55x mob XP per kill
            kills = 12 if not quest.objective_count else max(6, min(20, quest.objective_count))
            return kills, int(round(kills * per_mob * 0.55))

        if quest.objective_count and quest.objective_count > 0:
            kills = max(4, min(24, int(round(quest.objective_count * 1.35))))
        elif quest.is_group:
            kills = 10
        else:
            # Standard kill/collect quest: ~8-15 mobs cleared including aggro along the way
            obj_spots = max(1, len(quest.objective_coords))
            kills = min(18, 7 + obj_spots * 2)

        return kills, kills * per_mob

    def estimate_dungeon_trash_xp(self, recommended_level: int, player_level: int, is_repeat: bool = False) -> int:
        """Estimate boss + trash mob kill XP from a full 5-player dungeon clear."""
        if player_level >= self.max_level:
            return 0
        per_mob = self.base_mob_xp(recommended_level, player_level)
        # Forever shifted XP toward quests, with moderate boss/trash kill XP (~45-65 effective mob kills at 5-man rate)
        effective_mobs = 55 if not is_repeat else 35
        return int(round(effective_mobs * per_mob * 0.55))

    def apply_xp(self, state: CharacterState, xp_gain: int, source: str = "quest") -> int:
        """Apply XP to character state, handle level-ups, and record level timestamps."""
        if xp_gain <= 0 or state.level >= self.max_level:
            return 0

        state.xp += xp_gain
        state.total_xp_earned += xp_gain
        if source == "quest":
            state.quest_xp_earned += xp_gain
        else:
            state.mob_xp_earned += xp_gain

        while state.level < self.max_level:
            needed = self.xp_for_level(state.level)
            if needed <= 0 or state.xp < needed:
                break
            state.xp -= needed
            state.level += 1
            if state.level not in state.level_timestamps:
                state.level_timestamps[state.level] = state.elapsed_seconds
            if state.level >= self.max_level:
                state.xp = 0
                break
        return xp_gain

    def xp_pct(self, state: CharacterState) -> float:
        needed = self.xp_for_level(state.level)
        if needed <= 0:
            return 100.0
        return round((state.xp / needed) * 100.0, 1)
