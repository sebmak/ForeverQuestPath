"""
Scores quest reward items for all 9 playable classes in World of Warcraft: Forever
using ForeverLevelingBiS ModelWeights & proficiencies, and tags special rewards
(bags with verified slot counts, multi-class gear/weapon upgrades, mounts, etc.).
"""

from __future__ import annotations

from typing import Any

# Canonical WoW Forever class ordering & metadata matching ForeverLevelingBiS/Data/Tables.lua.
ALL_CLASS_KEYS: tuple[str, ...] = (
    "WARRIOR",
    "PALADIN",
    "HUNTER",
    "ROGUE",
    "PRIEST",
    "SHAMAN",
    "MAGE",
    "WARLOCK",
    "DRUID",
)

# Verified bag slot counts (WoW Classic + WoW Forever custom bags from QuestieDB & Wowhead Forever)
VERIFIED_BAG_SLOTS: dict[int, int] = {
    918: 10,     # Deviate Hide Pack
    1537: 4,     # Old Blanchy's Feed Pouch
    1729: 10,    # Gunnysack of the Night Watch (Duskwood: The Night Watch)
    2082: 8,     # Wizbang's Gunnysack
    3343: 8,     # Captain Sander's Booty Bag
    3352: 10,    # Ooze-covered Bag (Wetlands: Digging Through the Ooze)
    9587: 14,    # Thawpelt Sack (Uldaman/IF: The Platinum Discs)
    10959: 16,   # Demon Hide Sack (Blasted Lands: You Are Rakh'likh, Demon)
    11324: 12,   # Explorer's Knapsack (STV/Swamp: Cortello's Riddle)
    11845: 6,    # Handmade Leather Bag
    19291: 14,   # Darkmoon Storage Box (Darkmoon Faire)
    22243: 20,   # Small Soul Pouch (Warlock soul bag)
    22244: 24,   # Box of Souls (Warlock soul bag)
    22679: 18,   # Supply Bag (Argent Dawn)
    221498: 12,  # Old Toolbox (Stonetalon: Stepping Stones / Cozy Sleeping Bag chain)
    256935: 6,   # Simple Leather Satchel
    270017: 10,  # Hunting Satchel (Loch Modan: Vyrin's Revenge)
    276891: 12,  # Spidersilk Pouch
    277200: 6,   # Scorched Leather Pouch
    277332: 8,   # Scuffed Shipping Crate
    277977: 6,   # Canvas Latchbag (Dun Morogh: Grund and Gozwin)
    279936: 6,   # Book Bag
    279939: 6,   # Collecting Basket
    280088: 6,   # Soft Saber Sack
    282286: 6,   # Shipwright's Satchel (Reagent bag, Darkshore: Making Do)
}

# Known WoW Classic/Forever quest mount reward itemIds
KNOWN_MOUNT_ITEM_IDS: set[int] = {
    2414, 5655, 5656, 5864, 5872, 5873, 8563, 8595, 13321, 13322, 13328, 13329, 18766, 18767, 18772, 18773,
    18774, 18776, 18777, 18778, 18785, 18786, 18787, 18788, 18789, 18790, 18791, 18793, 18794, 18795,
    18796, 18797, 18798, 18902, 269681, 269683, 274930,
}

# Rogue class ID = 4 -> class mask bit = 2^(4-1) = 8
ROGUE_CLASS_ID = 4
ROGUE_CLASS_MASK = 8

# 1H weapon subclasses vs ranged subclasses usable by Rogue (from FLB Tables.lua WeaponProficiencies[4])
ROGUE_MELEE_WEAPON_SUBCLASSES = {0, 4, 7, 13, 15}  # 1H Axe, 1H Mace, 1H Sword, Fist, Dagger
ROGUE_RANGED_WEAPON_SUBCLASSES = {2, 3, 16, 18}    # Bow, Gun, Thrown, Crossbow

# Jewelry/cloak/trinket subclasses in class=4
ACCESSORY_SLOTS = {2, 11, 12, 16}  # Neck, Ring, Trinket, Back


# Rules for RogueItemScorer: scores gear & classifies special rewards solely from ForeverLevelingBiS
# weight brackets, proficiency tables, and QuestieDB metadata; never fabricates item stats.
class RogueItemScorer:
    """Computes Rogue item utility scores (0..100) and builds normalized specialRewards entries."""

    def __init__(
        self,
        flb_items: dict[int, dict[str, Any]],
        qdb_items: dict[int, dict[Any, Any]],
        rogue_weights: dict[str, Any],
    ) -> None:
        self.flb_items = flb_items
        self.qdb_items = qdb_items
        # Blend combat (70%) + assassination (30%) leveling brackets for robust 22-60 Rogue scoring
        self.combat_brackets: list[dict[str, Any]] = rogue_weights["combat"]["brackets"]
        self.assn_brackets: list[dict[str, Any]] = rogue_weights["assassination"]["brackets"]

    def _weights_for_level(self, level: int) -> dict[str, float]:
        lvl = max(1, min(60, int(level or 24)))
        c_stats = self.combat_brackets[-1]["stats"]
        a_stats = self.assn_brackets[-1]["stats"]
        for b in self.combat_brackets:
            if lvl <= int(b["upTo"]):
                c_stats = b["stats"]
                break
        for b in self.assn_brackets:
            if lvl <= int(b["upTo"]):
                a_stats = b["stats"]
                break
        keys = set(c_stats.keys()) | set(a_stats.keys())
        return {k: 0.7 * float(c_stats.get(k, 0.0)) + 0.3 * float(a_stats.get(k, 0.0)) for k in keys}

    def is_rogue_usable(self, item_id: int) -> bool:
        """Checks FLB item class/subclass/classMask restrictions for a Rogue."""
        it = self.flb_items.get(item_id)
        if not it:
            return False
        cmask = it.get("classMask")
        if cmask and (int(cmask) & ROGUE_CLASS_MASK) == 0:
            return False
        cls = it.get("class")
        sub = it.get("subclass")
        slot = it.get("slot")
        if cls == 2:
            if sub == 20:  # Fishing pole
                return False
            if sub in ROGUE_MELEE_WEAPON_SUBCLASSES and slot in (13, 21, 22):
                return True
            if sub in ROGUE_RANGED_WEAPON_SUBCLASSES and slot in (15, 25, 26):
                return True
            return False
        if cls == 4:
            if slot in ACCESSORY_SLOTS:
                return True
            # Leather armor (subclass == 2) only; reject cloth/mail/plate/shields/relics/offhand frills
            if sub == 2 and slot in (1, 3, 5, 6, 7, 8, 9, 10, 20):
                return True
            return False
        return False

    def score_item_raw(self, item_id: int, quest_level: int = 30) -> float:
        """Computes raw stat+DPS point value for a Rogue-usable item."""
        if not self.is_rogue_usable(item_id):
            return 0.0
        it = self.flb_items[item_id]
        eval_lvl = max(int(it.get("reqLevel") or 0), int(quest_level or 24))
        w = self._weights_for_level(eval_lvl)
        stats: dict[str, Any] = it.get("stats") or {}
        score = 0.0
        for skey, sval in stats.items():
            if not isinstance(sval, (int, float)):
                continue
            if skey in ("armor", "armorbonus"):
                score += float(sval) * float(w.get("armor", 0.01))
            elif skey == "healthrgn":
                score += float(sval) * 0.4
            elif skey in ("firres", "frores", "natres", "shares", "arcres"):
                score += float(sval) * 0.15
            elif skey == "dodgePct":
                score += float(sval) * 1.2
            else:
                score += float(sval) * float(w.get(skey, 0.0))

        # Exclude pure caster/tank/healer leather pieces that provide zero physical offensive stats
        off_keys = ("agi", "str", "atkpwr", "critPct", "hitPct", "exprtng", "feratkpwr", "rgdatkpwr")
        has_off = any((stats.get(k) or 0) > 0 for k in off_keys)
        slot = it.get("slot")
        cls = it.get("class")

        if cls == 2 and isinstance(it.get("weapon"), dict):
            wp = it["weapon"]
            dps = float(wp.get("dps") or 0.0)
            if slot in (13, 21):  # Main-hand / One-hand
                # Reward slower MH swords/maces/fists for Sinister Strike burst; daggers good for Backstab/Ambush
                speed = float(wp.get("speed") or 2.0)
                sub = it.get("subclass")
                speed_bonus = 1.0 + (0.06 * max(0.0, speed - 2.2) if sub != 15 else 0.0)
                score += dps * float(w.get("dps", 9.0)) * speed_bonus
                has_off = True
            elif slot == 22:  # Off-hand
                score += dps * float(w.get("ohDps", 3.0))
                has_off = True
            elif slot in (15, 25, 26):
                has_off = has_off or (stats.get("sta", 0) > 0)

        if cls == 4 and not has_off:
            # Pure caster leather (int/spi/splpwr) has near-zero Rogue value; keep small stamina/armor only if high sta
            sta = float(stats.get("sta") or 0)
            if any((stats.get(k) or 0) > 0 for k in ("int", "splpwr", "spldmg", "splheal", "manargn")) and sta <= 4:
                return 0.0

        # Quality & trinket on-use bonus when item is e.g. blue/purple quest trinket without passive stats
        if slot == 12 and score == 0.0 and int(it.get("quality") or 0) >= 2:
            score = 8.0 * (1.0 + 0.02 * eval_lvl)

        return max(0.0, score)

    def score_item_normalized(self, item_id: int, quest_level: int = 30) -> float:
        """Normalizes raw Rogue item score into 0.0..100.0 for easy comparison across quest levels."""
        raw = self.score_item_raw(item_id, quest_level=quest_level)
        if raw <= 0.0:
            return 0.0
        it = self.flb_items[item_id]
        cls = it.get("class")
        slot = it.get("slot")
        ilvl = max(15, int(it.get("itemLevel") or quest_level or 25))
        # Expected strong green/blue quest reward ceiling at `ilvl`
        if cls == 2 and slot in (13, 21):
            ceiling = (ilvl * 0.72) * 9.5 + ilvl * 0.65
        elif cls == 2 and slot == 22:
            ceiling = (ilvl * 0.72) * 3.8 + ilvl * 0.65
        elif cls == 2 and slot in (15, 25, 26):
            ceiling = ilvl * 0.7
        elif slot in ACCESSORY_SLOTS:
            ceiling = ilvl * 1.1
        else:
            # Slot multiplier for armor budget (chest/legs ~1.4x, wrists/waist ~0.85x)
            slot_mult = {1: 1.35, 3: 1.05, 5: 1.4, 20: 1.4, 7: 1.35, 8: 1.0, 10: 1.0, 6: 0.95, 9: 0.8}.get(slot, 1.0)
            ceiling = ilvl * 1.45 * slot_mult
        norm = min(100.0, (raw / max(1.0, ceiling)) * 82.0)
        if int(it.get("quality") or 0) >= 3:
            norm = min(100.0, norm + 8.0)
        return round(norm, 2)

    def build_reward_entry(self, item_id: int, is_choice: bool, quest_level: int) -> dict[str, Any] | None:
        """Builds a normalized specialRewards object if the item is a bag, Rogue gear/weapon, or mount."""
        flb_it = self.flb_items.get(item_id)
        qdb_it = self.qdb_items.get(item_id)
        name = (flb_it or {}).get("name") or (qdb_it or {}).get(1)
        if not name:
            return None

        cls = (flb_it or {}).get("class") or (qdb_it or {}).get(12)
        sub = (flb_it or {}).get("subclass") or (qdb_it or {}).get(13)
        quality = int((flb_it or {}).get("quality") or 1)
        ilvl = int((flb_it or {}).get("itemLevel") or (qdb_it or {}).get(9) or 0)

        # 1. Container bags (class == 1)
        if cls == 1 or item_id in VERIFIED_BAG_SLOTS:
            # Ignore specialized soul/herb/reagent bags if not general bag? Still record actual slot count!
            slots = VERIFIED_BAG_SLOTS.get(item_id, 0)
            return {
                "itemId": item_id,
                "name": str(name),
                "kind": "bag",
                "slots": int(slots),
                "quality": quality,
                "itemLevel": ilvl,
                "isChoice": is_choice,
                "rogueScore": 0.0,
            }

        # 2. Mounts
        if item_id in KNOWN_MOUNT_ITEM_IDS or (qdb_it and qdb_it.get(16) and "mount" in str(name).lower()):
            return {
                "itemId": item_id,
                "name": str(name),
                "kind": "mount",
                "slots": 0,
                "quality": quality,
                "itemLevel": ilvl,
                "isChoice": is_choice,
                "rogueScore": 0.0,
            }

        # 3. Rogue-usable weapon or leather/accessory gear
        rscore = self.score_item_normalized(item_id, quest_level=quest_level)
        if rscore > 0.0:
            kind = "weapon" if cls == 2 else "gear"
            return {
                "itemId": item_id,
                "name": str(name),
                "kind": kind,
                "slots": 0,
                "quality": quality,
                "itemLevel": ilvl,
                "isChoice": is_choice,
                "rogueScore": rscore,
            }

        return None


# Rules for MultiClassItemScorer: scores gear & classifies special rewards across all 9 classes
# using ForeverLevelingBiS ModelWeights, WeaponProficiencies, and ArmorProficiencies; never fabricates item stats.
class MultiClassItemScorer:
    """Computes normalized 0..100 item scores across all 9 WoW Forever classes and builds specialRewards entries."""

    CLASS_SPECS: dict[str, dict[str, Any]] = {
        "WARRIOR": {
            "id": 1,
            "bit": 1,
            "weapons": {0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 13, 15, 16, 18},
            "ranged": {2, 3, 16, 18},
            "max_armor": 4,
            "can_shield": True,
            "held_offhand": False,
            "relic_subclass": None,
            "profiles": [
                ("melee", "physical", [("arms", 0.65), ("fury", 0.35)]),
                ("tank", "tank", [("protection", 1.0)]),
            ],
        },
        "PALADIN": {
            "id": 2,
            "bit": 2,
            "weapons": {0, 1, 4, 5, 6, 7, 8},
            "ranged": set(),
            "max_armor": 4,
            "can_shield": True,
            "held_offhand": True,
            "relic_subclass": 7,
            "profiles": [
                ("ret", "physical", [("retribution", 1.0)]),
                ("prot", "tank", [("protection", 1.0)]),
                ("holy", "healer", [("holy", 1.0)]),
            ],
        },
        "HUNTER": {
            "id": 3,
            "bit": 4,
            "weapons": {0, 1, 2, 3, 6, 7, 8, 10, 13, 15, 16, 18},
            "ranged": {2, 3, 18},
            "max_armor": 3,
            "can_shield": False,
            "held_offhand": False,
            "relic_subclass": None,
            "profiles": [
                ("ranged", "hunter", [("beastmastery", 0.60), ("marksmanship", 0.30), ("survival", 0.10)]),
            ],
        },
        "PRIEST": {
            "id": 5,
            "bit": 16,
            "weapons": {4, 10, 15, 19},
            "ranged": {19},
            "max_armor": 1,
            "can_shield": False,
            "held_offhand": True,
            "relic_subclass": None,
            "profiles": [
                ("shadow", "caster", [("shadow", 1.0)]),
                ("heal", "healer", [("holy", 0.60), ("discipline", 0.40)]),
            ],
        },
        "SHAMAN": {
            "id": 7,
            "bit": 64,
            "weapons": {0, 1, 4, 5, 10, 13, 15},
            "ranged": set(),
            "max_armor": 3,
            "can_shield": True,
            "held_offhand": True,
            "relic_subclass": 9,
            "profiles": [
                ("enh", "physical", [("enhancement", 1.0)]),
                ("ele", "caster", [("elemental", 1.0)]),
                ("resto", "healer", [("restoration", 1.0)]),
            ],
        },
        "MAGE": {
            "id": 8,
            "bit": 128,
            "weapons": {7, 10, 15, 19},
            "ranged": {19},
            "max_armor": 1,
            "can_shield": False,
            "held_offhand": True,
            "relic_subclass": None,
            "profiles": [
                ("caster", "caster", [("frost", 0.50), ("fire", 0.30), ("arcane", 0.20)]),
            ],
        },
        "WARLOCK": {
            "id": 9,
            "bit": 256,
            "weapons": {7, 10, 15, 19},
            "ranged": {19},
            "max_armor": 1,
            "can_shield": False,
            "held_offhand": True,
            "relic_subclass": None,
            "profiles": [
                ("caster", "caster", [("affliction", 0.55), ("demonology", 0.25), ("destruction", 0.20)]),
            ],
        },
        "DRUID": {
            "id": 11,
            "bit": 1024,
            "weapons": {4, 5, 6, 10, 13, 15},
            "ranged": set(),
            "max_armor": 2,
            "can_shield": False,
            "held_offhand": True,
            "relic_subclass": 8,
            "profiles": [
                ("feral", "feral", [("feral", 1.0)]),
                ("bear", "tank", [("bear", 1.0)]),
                ("bal", "caster", [("balance", 1.0)]),
                ("resto", "healer", [("restoration", 1.0)]),
            ],
        },
    }
    MELEE_SLOTS: set[int] = {13, 17, 21, 22}
    RANGED_SLOTS: set[int] = {15, 25, 26}
    ARMOR_SLOTS: set[int] = {1, 3, 5, 6, 7, 8, 9, 10, 20}
    TWO_HAND_SUBCLASSES: set[int] = {1, 5, 6, 8, 10}
    SLOT_BUDGET_MULT: dict[int, float] = {
        1: 1.35, 3: 1.05, 5: 1.40, 20: 1.40, 7: 1.35, 8: 1.00, 10: 1.00, 6: 0.95, 9: 0.80,
    }
    RESIST_KEYS: set[str] = {"firres", "frores", "natres", "shares", "arcres"}

    def __init__(
        self,
        flb_items: dict[int, dict[str, Any]],
        qdb_items: dict[int, dict[Any, Any]],
        model_weights: dict[str, Any],
    ) -> None:
        self.flb_items = flb_items
        self.qdb_items = qdb_items
        self.model_weights = model_weights
        self.rogue_scorer = RogueItemScorer(flb_items, qdb_items, model_weights["ROGUE"])

    def _weights_for_spec(self, cls_key: str, spec: str, level: int) -> dict[str, float]:
        brackets = self.model_weights[cls_key][spec]["brackets"]
        chosen = brackets[-1]["stats"]
        for br in brackets:
            if level <= int(br["upTo"]):
                chosen = br["stats"]
                break
        return {str(k): float(v) for k, v in chosen.items() if isinstance(v, (int, float))}

    def _blended_weights(self, cls_key: str, spec_mix: list[tuple[str, float]], level: int) -> dict[str, float]:
        out: dict[str, float] = {}
        for spec, weight in spec_mix:
            sw = self._weights_for_spec(cls_key, spec, level)
            for k, v in sw.items():
                out[k] = out.get(k, 0.0) + weight * v
        return out

    @staticmethod
    def _effective_max_armor(cfg: dict[str, Any], eval_lvl: int) -> int:
        cap = int(cfg["max_armor"])
        if cap >= 3 and eval_lvl < 40:
            return cap - 1
        return cap

    def _is_role_compatible_armor(self, cfg: dict[str, Any], role: str, sc: int, eval_lvl: int) -> float:
        """Returns armor proficiency factor (0.0 if unusable, 0.82..1.0 if wearable)."""
        eff_max = self._effective_max_armor(cfg, eval_lvl)
        if sc > eff_max:
            return 0.0
        if sc == eff_max:
            return 1.0
        if role in ("caster", "healer"):
            return 0.90 if sc == 1 or sc == eff_max - 1 else 0.78
        # Physical melee/tank/hunter classes should not roll heavily on Cloth or outdated low-tier armor
        if sc == 1:
            return 0.0
        if eff_max - sc == 1 and eval_lvl < 30:
            return 0.90
        return 0.0

    @staticmethod
    def _has_relevant_role_stats(role: str, stats: dict[str, Any], it: dict[str, Any]) -> bool:
        if it.get("class") == 2 and isinstance(it.get("weapon"), dict):
            return True
        if role in ("physical", "feral", "hunter"):
            off = ("agi", "str", "atkpwr", "rgdatkpwr", "feratkpwr", "critPct", "hitPct", "exprtng")
            if any((stats.get(k) or 0) > 0 for k in off):
                return True
            return role == "hunter" and (stats.get("int") or 0) > 0 and (stats.get("sta") or 0) > 0
        if role == "tank":
            tank = ("sta", "str", "agi", "defrtng", "dodgePct", "parryPct", "block", "blockPct")
            return any((stats.get(k) or 0) > 0 for k in tank) and int(it.get("subclass") or 0) != 1
        # Caster / Healer
        spell = ("int", "spi", "splpwr", "spldmg", "splheal", "manargn", "holsplpwr", "firsplpwr", "frosplpwr", "shasplpwr", "natsplpwr", "arcsplpwr")
        return any((stats.get(k) or 0) > 0 for k in spell)

    def _score_profile(self, cls_key: str, cfg: dict[str, Any], role: str, spec_mix: list[tuple[str, float]], it: dict[str, Any], eval_lvl: int) -> float:
        stats: dict[str, Any] = it.get("stats") or {}
        cls = int(it.get("class") or 0)
        sub = int(it.get("subclass") or 0)
        slot = int(it.get("slot") or 0)
        armor_mult = 1.0

        if cls == 2:
            if sub == 20:
                return 0.0
            if slot in self.RANGED_SLOTS:
                if sub not in cfg["ranged"]:
                    return 0.0
            elif slot in self.MELEE_SLOTS:
                if sub not in cfg["weapons"]:
                    return 0.0
                if slot == 22 and cls_key not in ("WARRIOR", "HUNTER"):
                    return 0.0
                # Pure casters should only score caster-statted 1H/2H weapons (not physical beatsticks)
                if role in ("caster", "healer") and not any(
                    (stats.get(k) or 0) > 0
                    for k in ("int", "spi", "splpwr", "spldmg", "splheal", "manargn", "holsplpwr", "firsplpwr", "frosplpwr", "shasplpwr", "natsplpwr", "arcsplpwr")
                ):
                    return 0.0
                # Physical melee classes (Warrior/Paladin) should not value caster staves/daggers
                if role in ("physical", "tank") and sub in (10, 15):
                    sp_val = (stats.get("splpwr") or 0) + (stats.get("spldmg") or 0) + (stats.get("splheal") or 0)
                    melee_val = (stats.get("str") or 0) + (stats.get("agi") or 0) + (stats.get("atkpwr") or 0) * 0.5
                    if sp_val > melee_val or (melee_val == 0 and (stats.get("int") or 0) > 0):
                        return 0.0
            else:
                return 0.0
        elif cls == 4:
            if slot in ACCESSORY_SLOTS:
                armor_mult = 1.0
            elif sub == 6:
                if not cfg["can_shield"]:
                    return 0.0
                armor_mult = 1.0
            elif slot == 23 and sub == 0:
                if not cfg["held_offhand"]:
                    return 0.0
                armor_mult = 1.0
            elif slot == 28 or sub in (7, 8, 9):
                if cfg["relic_subclass"] != sub:
                    return 0.0
                return 65.0 + min(25.0, int(it.get("quality") or 2) * 8.0)
            elif slot in self.ARMOR_SLOTS and 1 <= sub <= 4:
                armor_mult = self._is_role_compatible_armor(cfg, role, sub, eval_lvl)
                if armor_mult <= 0.0:
                    return 0.0
            else:
                return 0.0
        else:
            return 0.0

        if slot == 12 and not stats and int(it.get("quality") or 0) >= 2:
            # Utility / on-use green+ quest trinkets benefit every class equally
            return 28.0 if int(it.get("quality") or 0) >= 3 else 18.0

        if not self._has_relevant_role_stats(role, stats, it):
            return 0.0

        w = self._blended_weights(cls_key, spec_mix, eval_lvl)
        raw = 0.0
        for skey, sval in stats.items():
            if not isinstance(sval, (int, float)):
                continue
            if skey in ("armor", "armorbonus"):
                armor_w = w.get("armor", 0.015 if role == "tank" else 0.004)
                raw += float(sval) * float(armor_w)
            elif skey == "healthrgn":
                raw += float(sval) * (0.25 if role == "tank" else 0.08)
            elif skey in self.RESIST_KEYS:
                raw += float(sval) * 0.05
            elif skey == "dodgePct":
                raw += float(sval) * max(w.get("dodgePct", 0.0), 3.0 if role == "tank" else 0.4)
            else:
                raw += float(sval) * float(w.get(skey, 0.0))

        # Suppress minor off-role noise on physical armor pieces
        if role == "physical" and (stats.get("int") or 0) > 0 and cls_key == "WARRIOR":
            raw -= float(stats.get("int", 0)) * 0.5

        if cls == 2 and isinstance(it.get("weapon"), dict):
            wp = it["weapon"]
            dps = float(wp.get("dps") or 0.0)
            if slot in self.RANGED_SLOTS:
                rdps_w = float(w.get("rangedDps", 0.0))
                raw += dps * (0.35 * rdps_w if sub == 19 else rdps_w)
            elif slot in (13, 17, 21):
                dps_w = float(w.get("dps", 0.0))
                if role == "hunter":
                    # Hunters treat melee weapons primarily as stat-sticks
                    raw += dps * (0.18 * dps_w)
                elif role == "feral":
                    # Druids scale mostly from Feral AP & stats in forms, not white weapon DPS
                    raw += dps * (0.15 * dps_w)
                elif role in ("caster", "healer"):
                    raw += dps * (0.12 * dps_w)
                else:
                    speed = float(wp.get("speed") or 2.2)
                    speed_bonus = 1.0 + (0.05 * max(0.0, speed - (2.8 if slot == 17 else 2.2)))
                    raw += dps * dps_w * speed_bonus
            elif slot == 22:
                raw += dps * float(w.get("ohDps", 0.5 * w.get("dps", 0.0)))

        if raw <= 0.0:
            return 0.0

        # Compute spec-appropriate ceiling at item level so all 9 classes land on a balanced 0..100 scale
        ilvl = max(12, int(it.get("itemLevel") or eval_lvl or 20))
        if role in ("caster", "healer"):
            sp_w = max(w.get("splpwr", 1.0), w.get("spldmg", 0.0), w.get("splheal", 0.0))
            int_w = w.get("int", 0.0)
            spi_w = w.get("spi", 0.0)
            ramp = 0.42 + 0.50 * min(1.0, max(0.0, (eval_lvl - 10) / 36.0))
            unit = max(int_w * 0.80 + spi_w * 0.30, sp_w * ramp, 0.25)
        elif role == "tank":
            unit = max(w.get("sta", 1.0) * 0.72, w.get("str", 0.0) * 0.55 + w.get("agi", 0.0) * 0.45, 1.0)
        elif role == "hunter":
            unit = max(0.68 * w.get("agi", 0.0) + 0.30 * w.get("sta", 0.0), 1.6 * w.get("rgdatkpwr", 0.0), 1.0)
        else:
            unit = max(
                0.78 * max(w.get("str", 0.0), w.get("agi", 0.0)) + 0.35 * w.get("sta", 0.0),
                1.8 * w.get("atkpwr", 0.0),
                1.0,
            )

        if cls == 2:
            if slot in self.RANGED_SLOTS and sub != 19:
                ceiling = (ilvl * 0.65) * max(w.get("rangedDps", 0.0), 1.0) + ilvl * 0.50 * unit
            elif slot in self.RANGED_SLOTS and sub == 19:
                ceiling = (ilvl * 0.65) * 0.35 * max(w.get("rangedDps", 0.0), 0.5) + ilvl * 0.45 * unit
            elif slot == 17 and role not in ("hunter", "feral", "caster", "healer"):
                ceiling = (ilvl * 0.84) * max(w.get("dps", 0.0), 1.0) + ilvl * 0.65 * unit
            elif slot in (13, 21) and role not in ("hunter", "feral", "caster", "healer"):
                ceiling = (ilvl * 0.68) * max(w.get("dps", 0.0), 1.0) + ilvl * 0.45 * unit
            elif slot == 22 and role not in ("hunter", "feral", "caster", "healer"):
                ceiling = (ilvl * 0.65) * max(w.get("ohDps", 0.5 * w.get("dps", 0.0)), 1.0) + ilvl * 0.40 * unit
            elif role in ("caster", "healer"):
                sp_w = max(w.get("splpwr", 1.0), w.get("spldmg", 0.0), w.get("splheal", 0.0))
                slot_f = 1.15 if slot == 17 else 0.82
                ceiling = max(ilvl * slot_f * sp_w, ilvl * 0.90 * slot_f * unit)
            else:
                slot_f = 1.30 if slot == 17 else 0.90
                ceiling = ilvl * 0.78 * slot_f * unit
        elif sub == 6:
            ceiling = ilvl * 0.85 * unit + ilvl * 18.0 * w.get("armor", 0.015)
        elif slot in ACCESSORY_SLOTS or slot == 23:
            ceiling = ilvl * 0.58 * unit
        else:
            slot_mult = self.SLOT_BUDGET_MULT.get(slot, 1.0)
            ceiling = ilvl * 0.72 * slot_mult * unit

        norm = min(100.0, (raw / max(1.0, ceiling)) * 82.0 * armor_mult)
        if int(it.get("quality") or 0) >= 3:
            norm = min(100.0, norm + 8.0)
        return round(norm, 2)

    def score_item_all_classes(self, item_id: int, quest_level: int = 30) -> dict[str, float]:
        """Returns normalized 0..100 score map across all 9 WoW Forever classes."""
        scores: dict[str, float] = {cls_key: 0.0 for cls_key in ALL_CLASS_KEYS}
        scores["ROGUE"] = self.rogue_scorer.score_item_normalized(item_id, quest_level=quest_level)
        it = self.flb_items.get(item_id)
        if not it:
            return scores
        cmask = it.get("classMask")
        eval_lvl = max(1, min(60, max(int(it.get("reqLevel") or 0), int(quest_level or 20))))

        for cls_key, cfg in self.CLASS_SPECS.items():
            if cmask and (int(cmask) & int(cfg["bit"])) == 0:
                continue
            best = 0.0
            for idx, (_pname, role, spec_mix) in enumerate(cfg["profiles"]):
                val = self._score_profile(cls_key, cfg, role, spec_mix, it, eval_lvl)
                # Slightly favor the class's primary leveling profile over pure off-spec healer/tank
                if idx > 0 and role in ("healer", "tank"):
                    val = round(val * 0.92, 2)
                if val > best:
                    best = val
            scores[cls_key] = best
        return scores

    def build_reward_entry(self, item_id: int, is_choice: bool, quest_level: int) -> dict[str, Any] | None:
        """Builds a normalized multi-class specialRewards object for bags, mounts, weapons, and gear."""
        flb_it = self.flb_items.get(item_id)
        qdb_it = self.qdb_items.get(item_id)
        name = (flb_it or {}).get("name") or (qdb_it or {}).get(1)
        if not name:
            return None

        cls = (flb_it or {}).get("class") or (qdb_it or {}).get(12)
        quality = int((flb_it or {}).get("quality") or 1)
        ilvl = int((flb_it or {}).get("itemLevel") or (qdb_it or {}).get(9) or 0)
        zero_scores = {cls_key: 0.0 for cls_key in ALL_CLASS_KEYS}

        if cls == 1 or item_id in VERIFIED_BAG_SLOTS:
            slots = VERIFIED_BAG_SLOTS.get(item_id, 0)
            return {
                "itemId": item_id,
                "name": str(name),
                "kind": "bag",
                "slots": int(slots),
                "quality": quality,
                "itemLevel": ilvl,
                "isChoice": is_choice,
                "rogueScore": 0.0,
                "classScores": zero_scores,
            }

        if item_id in KNOWN_MOUNT_ITEM_IDS or (qdb_it and qdb_it.get(16) and "mount" in str(name).lower()):
            return {
                "itemId": item_id,
                "name": str(name),
                "kind": "mount",
                "slots": 0,
                "quality": quality,
                "itemLevel": ilvl,
                "isChoice": is_choice,
                "rogueScore": 0.0,
                "classScores": zero_scores,
            }

        class_scores = self.score_item_all_classes(item_id, quest_level=quest_level)
        max_score = max(class_scores.values(), default=0.0)
        if max_score > 0.0:
            kind = "weapon" if cls == 2 else "gear"
            return {
                "itemId": item_id,
                "name": str(name),
                "kind": kind,
                "slots": 0,
                "quality": quality,
                "itemLevel": ilvl,
                "isChoice": is_choice,
                "rogueScore": float(class_scores["ROGUE"]),
                "classScores": class_scores,
            }

        return None
