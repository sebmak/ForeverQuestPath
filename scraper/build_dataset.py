"""
Builds verified, normalized JSON datasets (`quests.json`, `dungeons.json`,
`travel_graph.json`, `schema.md`) for World of Warcraft: Forever (1.60.1)
from local QuestieDB, ForeverLevelingBiS, and OpenMap databases.
"""

from __future__ import annotations

import json
import math
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cbor_reader import QuestieDBData, QuestieDBReader
from fetch_npc_types import DEFAULT_CACHE_PATH as NPC_CREATURE_TYPES_CACHE_PATH, NpcCreatureTypeFetcher
from lua_parser import parse_lua_table_at
from scoring import ALL_CLASS_KEYS, MultiClassItemScorer, RogueItemScorer, VERIFIED_BAG_SLOTS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ADDONS_ROOT = Path("/Applications/World of Warcraft/_forever_/Interface/AddOns")
VENDOR_QUESTIEDB_DIR = PROJECT_ROOT / "scraper" / "vendor" / "QuestieDB"
QUESTIEDB_DIR = (
    VENDOR_QUESTIEDB_DIR
    if (VENDOR_QUESTIEDB_DIR / "QuestieDB_Forever.toc").exists()
    else ADDONS_ROOT / "QuestieDB"
)
FLB_DATA_DIR = ADDONS_ROOT / "ForeverLevelingBiS/Data"
OPENMAP_DATA_DIR = ADDONS_ROOT / "OpenMap/Data/Forever"

# Alliance Gnome (bit 64 = 1<<6) and Rogue (bit 8 = 1<<3) masks in WoW Classic/Forever.
GNOME_RACE_BIT = 1 << 6
ROGUE_CLASS_BIT = 1 << 3

# Full WoW Forever 10-race & 9-class masks (High Order Skyborne = 1<<32, Windshaper Skyborne = 1<<33).
PLAYABLE_ALLIANCE_CLASSIC_MASK = 1 | 4 | 8 | 64
PLAYABLE_HORDE_CLASSIC_MASK = 2 | 16 | 32 | 128
SKYBORNE_ALLIANCE_BIT = 1 << 32
SKYBORNE_HORDE_BIT = 1 << 33
ALL_ALLIANCE_RACE_MASK = PLAYABLE_ALLIANCE_CLASSIC_MASK | SKYBORNE_ALLIANCE_BIT
ALL_HORDE_RACE_MASK = PLAYABLE_HORDE_CLASSIC_MASK | SKYBORNE_HORDE_BIT
ALL_PLAYABLE_RACE_MASK = ALL_ALLIANCE_RACE_MASK | ALL_HORDE_RACE_MASK
ALL_PLAYABLE_CLASS_MASK = 1 | 2 | 4 | 8 | 16 | 64 | 128 | 256 | 1024

# Canonical playable WoW Forever races (verified against ForeverLevelingBiS/Data/Tables.lua & QuestieDB).
RACES: list[dict[str, Any]] = [
    {
        "id": 1, "name": "Human", "label": "Human", "raceBit": 1, "faction": "Alliance",
        "startZoneId": 12, "startCoords": {"zoneId": 12, "x": 48.17, "y": 42.94},
        "classes": ["WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "MAGE", "WARLOCK"],
    },
    {
        "id": 2, "name": "Orc", "label": "Orc", "raceBit": 2, "faction": "Horde",
        "startZoneId": 14, "startCoords": {"zoneId": 14, "x": 43.29, "y": 68.53},
        "classes": ["WARRIOR", "HUNTER", "ROGUE", "SHAMAN", "MAGE", "WARLOCK"],
    },
    {
        "id": 3, "name": "Dwarf", "label": "Dwarf", "raceBit": 4, "faction": "Alliance",
        "startZoneId": 1, "startCoords": {"zoneId": 1, "x": 29.93, "y": 71.2},
        "classes": ["WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN"],
    },
    {
        "id": 4, "name": "NightElf", "label": "Night Elf", "raceBit": 8, "faction": "Alliance",
        "startZoneId": 141, "startCoords": {"zoneId": 141, "x": 58.69, "y": 44.27},
        "classes": ["WARRIOR", "HUNTER", "ROGUE", "PRIEST", "DRUID"],
    },
    {
        "id": 5, "name": "Scourge", "label": "Undead", "raceBit": 16, "faction": "Horde",
        "startZoneId": 85, "startCoords": {"zoneId": 85, "x": 30.22, "y": 71.65},
        "classes": ["WARRIOR", "PALADIN", "ROGUE", "PRIEST", "MAGE", "WARLOCK"],
    },
    {
        "id": 6, "name": "Tauren", "label": "Tauren", "raceBit": 32, "faction": "Horde",
        "startZoneId": 215, "startCoords": {"zoneId": 215, "x": 44.47, "y": 77.5},
        "classes": ["WARRIOR", "HUNTER", "SHAMAN", "DRUID"],
    },
    {
        "id": 7, "name": "Gnome", "label": "Gnome", "raceBit": 64, "faction": "Alliance",
        "startZoneId": 1, "startCoords": {"zoneId": 1, "x": 29.93, "y": 71.2},
        "classes": ["WARRIOR", "ROGUE", "PRIEST", "MAGE", "WARLOCK"],
    },
    {
        "id": 8, "name": "Troll", "label": "Troll", "raceBit": 128, "faction": "Horde",
        "startZoneId": 14, "startCoords": {"zoneId": 14, "x": 43.29, "y": 68.53},
        "classes": ["WARRIOR", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK"],
    },
    {
        "id": 95, "name": "HighOrderSkyborne", "label": "High Order Skyborne",
        "raceBit": SKYBORNE_ALLIANCE_BIT, "faction": "Alliance",
        "startZoneId": 16593, "startCoords": {"zoneId": 16593, "x": 42.82, "y": 23.39},
        "classes": ["WARRIOR", "HUNTER", "ROGUE", "MAGE", "DRUID"],
    },
    {
        "id": 96, "name": "WindshaperSkyborne", "label": "Windshaper Skyborne",
        "raceBit": SKYBORNE_HORDE_BIT, "faction": "Horde",
        "startZoneId": 16593, "startCoords": {"zoneId": 16593, "x": 42.82, "y": 23.39},
        "classes": ["WARRIOR", "HUNTER", "ROGUE", "SHAMAN", "DRUID"],
    },
]

CLASSES: list[dict[str, Any]] = [
    {
        "id": cid, "key": key, "label": label, "classBit": bit,
        "races": [r["name"] for r in RACES if key in r["classes"]],
    }
    for cid, key, label, bit in (
        (1, "WARRIOR", "Warrior", 1),
        (2, "PALADIN", "Paladin", 2),
        (3, "HUNTER", "Hunter", 4),
        (4, "ROGUE", "Rogue", 8),
        (5, "PRIEST", "Priest", 16),
        (7, "SHAMAN", "Shaman", 64),
        (8, "MAGE", "Mage", 128),
        (9, "WARLOCK", "Warlock", 256),
        (11, "DRUID", "Druid", 1024),
    )
]

# World zone territorial faction ownership ("Alliance", "Horde", "Contested").
ZONE_FACTIONS: dict[int, str] = {
    **{zid: "Alliance" for zid in (1, 12, 38, 40, 141, 148, 1519, 1537, 1657)},
    **{zid: "Horde" for zid in (14, 17, 85, 130, 215, 1497, 1637, 1638)},
    **{
        zid: "Contested"
        for zid in (
            3, 4, 8, 10, 11, 15, 16, 28, 33, 36, 41, 44, 45, 46, 47, 51,
            139, 267, 331, 357, 361, 400, 405, 406, 440, 490, 493, 616, 618,
            1377, 16591, 16593, 16651,
        )
    },
}


def _expand_race_mask(mask: int) -> int:
    """Normalizes 0 (all playable races) and retains only playable race bits."""
    return ALL_PLAYABLE_RACE_MASK if mask == 0 else (mask & ALL_PLAYABLE_RACE_MASK)


def _effective_race_mask(faction_mask: int, race_mask: int) -> int:
    """Intersects normalized playable race bits with faction ownership bits."""
    expanded = _expand_race_mask(race_mask)
    allowed = 0
    if faction_mask & 1:
        allowed |= ALL_ALLIANCE_RACE_MASK
    if faction_mask & 2:
        allowed |= ALL_HORDE_RACE_MASK
    return expanded & allowed if allowed else expanded


# Quest flags and sortKeys excluded from regular 1-60 leveling eligibility.
DAILY_WEEKLY_MONTHLY_FLAGS = 4096 | 32768 | 65536
SEASONAL_OR_NON_LEVELING_SORTS = {
    -21, -22, -25, -41, -284, -364, -365, -366, -368, -369, -370, -374, -375, -376,
    -378, -402, -404,
}
CRAFT_ONLY_SORTS = {-24, -121, -181, -201, -264}
LEATHERWORKING_SKILL_ID = 165
SKINNING_SKILL_ID = 393
LEATHERWORKING_SORT_ID = -182
ALLOWED_SKILL_IDS = {129, LEATHERWORKING_SKILL_ID, 185, 356, SKINNING_SKILL_ID, 633}
NON_SKINNABLE_BEAST_FAMILIES = {"Spider", "Crab"}
NON_SKINNABLE_BEAST_NAME_RE = re.compile(
    r"\b(spider|tarantula|spinner|recluse|widow|crab|crawler|thresher|frenzy|shark|silithid|grub|maggot|wasp|stinger|swarmer|larva|beetle|roach)\b",
    re.IGNORECASE,
)
BG_ZONES = {2597, 3277, 3358, 16603, 16604, 16605, 16653}
PROTO_OR_TEST_ZONES = {
    16144, 16508, 16509, 16510, 16511, 16512, 16513, 16514, 16515, 16516,
    16517, 16518, 16519, 16520, 16521, 16522, 16523, 16524, 16532, 16537,
    16632, 16650, 16662, 16772, 17845,
}
RAID_ZONES = {2159, 2677, 2717, 1977, 3428, 3429, 3456, 16076, 16394}

# Exact Blizzard Classic / WoW Forever 1-60 XP-to-next-level curve.
XP_CURVE: dict[str, int] = {
    "1": 400, "2": 900, "3": 1400, "4": 2100, "5": 2800, "6": 3600,
    "7": 4500, "8": 5400, "9": 6500, "10": 7600, "11": 8800, "12": 10100,
    "13": 11400, "14": 12900, "15": 14400, "16": 16000, "17": 17700,
    "18": 19400, "19": 21300, "20": 23200, "21": 25200, "22": 27300,
    "23": 29400, "24": 31700, "25": 34000, "26": 36400, "27": 38900,
    "28": 41400, "29": 44300, "30": 47400, "31": 50800, "32": 54500,
    "33": 58600, "34": 62800, "35": 67100, "36": 71600, "37": 76100,
    "38": 80800, "39": 85700, "40": 90700, "41": 95800, "42": 101000,
    "43": 106300, "44": 111800, "45": 117500, "46": 123200, "47": 129100,
    "48": 135100, "49": 141200, "50": 147500, "51": 153900, "52": 160400,
    "53": 167100, "54": 173900, "55": 180800, "56": 187900, "57": 195000,
    "58": 202300, "59": 209800,
}

# Verified Blizzard/Forever world zones absent from ForeverLevelingBiS/Data/Zones.lua.
CUSTOM_WORLD_ZONES: list[dict[str, Any]] = [
    {"id": 616, "name": "Mount Hyjal", "region": 1, "minLevel": 54, "maxLevel": 60},
    {"id": 16651, "name": "Shen'dralas", "region": 1, "minLevel": 35, "maxLevel": 45},
]

# Verified WoW Forever Shen'dralas & Mount Hyjal quests (from QuestieDB + foreverchanges.pro).
FOREVER_SHENDRALAS_QIDS = {
    92841, 92844, 92846, 92847, 92873, 92893, 92894, 92914, 93016, 93034,
    93035, 93037, 93041, 93058, 93062, 93066, 93072, 93122, 93125, 93129,
    93188, 93341, 93362, 93394, 93450, 93546, 93568, 93574, 93594, 93601,
    93606, 93609, 93644, 96010, 96018, 96060, 99257, 99258,
}
FOREVER_HYJAL_QIDS = {
    90487, 90647, 90865, 91688, 91697, 91754, 91755, 91770, 91794, 91881,
    91883, 91923, 92008, 92009, 92011, 92016, 92017, 92064, 92100, 92101,
    92102, 92111, 92147, 92169, 92171, 92317, 92328, 92329, 92335, 92446,
    92449, 92500, 92521, 92533, 92591, 92592, 92593, 92594, 92609, 92702,
    92833, 92959, 92960, 93127, 93197, 93269, 93383, 93406, 93407, 93422,
    93444, 93510, 93547, 93584, 93615, 93747, 93759, 93773, 93825, 93826,
    93827, 93831, 93952, 94479, 94504, 94782, 95026, 95027, 95248, 95316,
    95317, 95318, 95319, 95320, 95378, 95379, 95380, 95568, 95569, 95611,
    95793, 96003, 96077, 96131, 96145, 96252, 96255, 96262, 96296, 96303,
    96321, 96559, 96616, 96796, 96830, 96931, 96959, 96979, 96983, 96992,
    96998, 96999, 97000, 97009, 97490,
}
EXPLICIT_WORLD_ZONE_OVERRIDES: dict[int, int] = {
    78823: 3,  # Terror of the Desert Skies -> Badlands (leads to Nesingwary STV)
}
# Forever breadcrumbs whose nextQuestInChain points into a standalone hub quest without breadcrumbForQuestId.
FOREVER_BREADCRUMB_TARGETS: dict[int, int] = {
    98461: 305,  # Unrequited Love (Darkshore -> Menethil Harbor In Search of The Excavation Team)
    95737: 95647,  # Seeking Caitlin (Ashenvale -> Menethil Harbor Lost in the Thicket Things)
}
# Verified Forever requiredLevel overrides where QuestieDB omitted or under-set requiredLevel.
FOREVER_MIN_LEVEL_OVERRIDES: dict[int, int] = {
    378: 25,    # The Fury Runs Deep (Stockade: ForeverChanges & FLB both require level 25; matches prereq 303)
    1132: 18,   # Fiora Longears (Wetlands -> Theramore: ForeverChanges level 20 from 18, matches target 1133)
    92066: 47,  # The Twin Bears (Blackmaw Hold, ForeverChanges)
    97327: 55,  # Blackmaw Pouches (Blackmaw Hold, ForeverChanges)
}
# Verified Forever questLevel overrides where live ForeverChanges updated quest levels beyond QuestieDB 1.1.0.
FOREVER_QUEST_LEVEL_OVERRIDES: dict[int, int] = {
    1132: 20,   # Fiora Longears (Wetlands -> Theramore, matches target 1133 Journey to Astranaar)
    86757: 32,  # Slowing the Orc Advance (Ashenvale, ForeverChanges level 32 from 27)
    91732: 10,  # Good Steel (Elwynn Forest, ForeverChanges level 10 from 7)
    96137: 24,  # Ira's Dagger (Duskwood, ForeverChanges level 24 from 18)
    96138: 28,  # Merrick's Bow (Duskwood, ForeverChanges level 28 from 18)
    96139: 22,  # The Valor Family (Duskwood, ForeverChanges level 22 from 18)
    97282: 11,  # Stormy Potential (Durotar, ForeverChanges level 11 from 7)
    98027: 29,  # Gleaming Crescent Necklace (Ashenvale, ForeverChanges level 29 from 25)
    98155: 31,  # Pristine Pesterhide Pelts (Thousand Needles, ForeverChanges level 31 from 27; matches 98156)
    99052: 12,  # Threat from Below (Durotar, ForeverChanges level 12 elite from 10)
    99152: 10,  # As Above, So Below (Tirisfal Glades, ForeverChanges level 10 from 7)
    99153: 10,  # The One That Got Away (Tirisfal Glades, ForeverChanges level 10 from 7)
}
# Verified Forever elite quests missing Elite/Group flags in QuestieDB/FLB.
FOREVER_ELITE_QIDS: set[int] = {
    115,    # Shadow Magic (Redridge Mountains, level 23 elite)
    169,    # WANTED: Gath'Ilzogg (Redridge Mountains, level 26 elite)
    271,    # Vyrin's Revenge (Loch Modan, level 20 elite)
    531,    # Vyrin's Revenge (Loch Modan, level 20 elite)
    731,    # The Absent Minded Prospector (Darkshore, level 20 elite)
    2078,   # Gyromast's Revenge (Darkshore, level 20 elite)
    87491,  # Spark of Freedom (Wetlands, level 32 elite)
    88756,  # Bring Back a Bang (Wetlands, level 32 elite)
}
# Verified custom WoW Forever non-Rogue class quests missing requiredClasses in QuestieDB.
FOREVER_CLASS_OVERRIDES: dict[int, int] = {
    79939: 2,   # Paladin: The Broken Hammer
    79940: 2,   # Paladin: A Lost Brother
    96438: 2,   # Paladin: Battle at the Mannoroc Coven
    96449: 2,   # Paladin: Aeonas's Secret Refuge
    96476: 2,   # Paladin: In Search of Aeonas
    95753: 64,  # Shaman: Challenge of the Desert Wolf
    93797: 128, # Mage: Boughs in the Wind
}
# 0-XP/non-leveling Mage book turn-ins, Sparklematic repeatables, PvP/SoD leftovers, cloth donations, and lvl-0 stubs.
NON_LEVELING_ZERO_XP_QIDS = {
    785, 3366, 3911, 5101,         # Deprecated/Beta/Old quests absent from QuestieDB 1.1.0
    2951, 4601, 4602, 4606,        # The Sparklematic 5200!
    7810, 7838,                    # Gurubashi Arena PvP trinket turn-ins (minLevel=1)
    9121, 9122, 9123,              # Naxxramas raid entry turn-ins
    9415, 9419,                    # Silithus Silithyst PvP turn-ins (requiredLevel=1 in Forever)
    78132, 78133, 78134,           # Ashenvale Dragonslayer world buff turn-ins
    80001,                         # Rekindle (Stonetalon 0-XP campfire flavor)
    *range(81730, 81748),          # Duskwood Mission I-XVIII (SoD Emerald Wardens leftover, 404 on Forever)
    81917, 81919,                  # Blasted Lands SoD leftovers (404 on Forever)
    83934, 84148, 84149, 84154,    # AQ/SoD War Effort leftovers (404 on Forever)
    92521, 93862, 96735,           # Level 0 / stub quests
    92811,                         # Economic Warfare (Desolace level 0 stub)
    93551,                         # Praise duplicate stub (404 on Forever)
    94559, 94898, 94901, 94902,    # Unused / Temp Disabled / [PH] Zephras stubs
    95817,                         # Rotgath Stonebeard (tradeskill commerce license step 2, follows gated 95816)
    96076,                         # In Demand crafting turn-in
    96339, 96383,                  # Legacy Reward cosmetic claims
    96379, 96602,                  # Unreleased/duplicate 0-XP stubs (404 on Forever)
    96912,                         # Field of Honor: Week 1 (level 60 duel PvP)
    97188,                         # Learning to Ride pointer
    97286,                         # Mage Research Access unlock
    97447,                         # An Earnest Proposition leftover (404 on Forever)
    98372, 99267,                  # An Unfortunate End (lost torch replacement)
    98462,                         # Shroud of the Winter Wilds duplicate stub (404 on Forever)
    98896, 98897, 98898, 98901, 98910, 98913,  # Darkspear Islands Clash PvP/war-effort events
    99145,                         # A Winter Veil Gift (Seasonal)
    *range(99191, 99201),          # Capital city Cloth Donation rep turn-ins (0 XP)
    # Level 1-23 / cross-faction non-leveling, mount swap, seasonal gift, & broken stubs:
    785,                           # A Strategic Alliance (deprecated FLB-only stub, no coords)
    909,                           # Baron Aquanis (deprecated FLB-only Horde duplicate, no coords)
    *range(7660, 7679),            # Epic/racial mount exchange quests (0 XP)
    8744, 8767, 8768, 8769, 8788, 8803,  # Feast of Winter Veil gift quests (0 XP)
    8846, 8851,                    # Ahn'Qiraj War Effort supply turn-ins (0 XP)
    60860, 60870,                  # Non-leveling seasonal/promo 0-XP stubs
    79963, 94825, 95771, 96476,    # Broken/stub 0-XP or unimplemented quests (96476 In Search of Aeonas no spawns)
    86747, 93161, 93925,           # 0-XP repeatable/unleveled FLB stubs lacking QuestieDB questLevel
    97171, 97446, 98287, 98471, 98476, 98890,  # Camping (-666) / Invasion (-368) / 0-XP event stubs
}
NON_LEVELING_BOOK_NPCS = {5497, 210995, 211022, 211033}

# Canonical subzone -> parent zone/dungeon mappings missing from subZoneToParentZone.lua.
SUBZONE_OVERRIDES: dict[int, int] = {
    133: 721,     # Gnomeregan subzone -> Gnomeregan
    978: 1176,    # Zul'Farrak subzone -> Zul'Farrak
    1417: 1477,   # Sunken Temple subzone -> The Temple of Atal'Hakkar
    1517: 1337,   # Uldaman subzone -> Uldaman
    1717: 491,    # Razorfen Kraul subzone -> Razorfen Kraul
    2657: 16651,  # Valley of Bones -> Shen'dralas
    16560: 36,    # City of Dalaran outdoor / Kirin Tor subzone -> Alterac Mountains
    16562: 267,   # Hillsbrad Dalaran subzone -> Hillsbrad Foothills
    16606: 14,    # Darkspear Islands -> Durotar
    16675: 16651, # Magram Front -> Shen'dralas
    16676: 16651, # Outcast Hideaway -> Shen'dralas
    16677: 16651, # Bristleback Retreat -> Shen'dralas
    16678: 16651, # Evenshade's Overlook -> Shen'dralas
    16742: 16651, # Forlorn Gardens -> Shen'dralas
    16848: 616,   # Elderwild -> Mount Hyjal
    16849: 616,   # Summit of Eternity -> Mount Hyjal
    16853: 616,   # Tainted Foothills -> Mount Hyjal
    16866: 616,   # Dae'gun -> Mount Hyjal
    16867: 616,   # Shrine of Aviana -> Mount Hyjal
    16868: 616,   # Mourning's Rest -> Mount Hyjal
    17050: 616,   # Felblood Scar -> Mount Hyjal
    17144: 616,   # Malorne's Retreat -> Mount Hyjal
    17145: 616,   # Cradle of Tranquility -> Mount Hyjal
    16876: 11,    # Whelgar's Excavation Site (Forever) -> Wetlands
    16941: 139,   # Craftsman's Writ group -> Eastern Plaguelands
    17732: 11,    # Excavation Site: Wetlands Exterior -> Wetlands
    17780: 16842, # Krol'dok Stronghold subzone -> Krol'dok Stronghold dungeon
    17803: 1584,  # Blackrock Depths (Forever) -> BRD
    17804: 1583,  # Blackrock Spire (Forever) -> BRS
    2257: 1519,   # Deeprun Tram -> Stormwind City
    25: 51,       # Blackrock Mountain -> Searing Gorge
}

# Verified flight node ID -> canonical world zone ID (OpenMap Data/Forever/FlightData.lua, all 69 non-BG nodes).
FLIGHT_NODE_TO_ZONE: dict[int, int] = {
    # Alliance (faction == 1)
    2: 1519,    # Stormwind, Elwynn
    4: 40,      # Sentinel Hill, Westfall
    5: 44,      # Lakeshire, Redridge
    6: 1537,    # Ironforge, Dun Morogh
    7: 11,      # Menethil Harbor, Wetlands
    8: 38,      # Thelsamar, Loch Modan
    12: 10,     # Darkshire, Duskwood
    14: 267,    # Southshore, Hillsbrad
    16: 45,     # Refuge Pointe, Arathi
    19: 33,     # Booty Bay, Stranglethorn
    26: 148,    # Auberdine, Darkshore
    27: 141,    # Rut'theran Village, Teldrassil
    28: 331,    # Astranaar, Ashenvale
    31: 357,    # Thalanaar, Feralas
    32: 15,     # Theramore, Dustwallow Marsh
    33: 406,    # Stonetalon Peak, Stonetalon Mountains
    37: 405,    # Nijel's Point, Desolace
    39: 440,    # Gadgetzan, Tanaris
    41: 357,    # Feathermoon, Feralas
    43: 47,     # Aerie Peak, The Hinterlands
    45: 4,      # Nethergarde Keep, Blasted Lands
    49: 493,    # Moonglade (Alliance)
    52: 618,    # Everlook, Winterspring
    62: 493,    # Nighthaven, Moonglade
    64: 16,     # Talrendis Point, Azshara
    65: 361,    # Talonbranch Glade, Felwood
    66: 28,     # Chillwind Camp, Western Plaguelands
    67: 139,    # Light's Hope Chapel, Eastern Plaguelands
    71: 46,     # Morgan's Vigil, Burning Steppes
    73: 1377,   # Cenarion Hold, Silithus
    74: 51,     # Thorium Point, Searing Gorge
    3276: 16591,  # Farholde Keep, Riverglades
    # Horde (faction == 2)
    10: 130,    # The Sepulcher, Silverpine Forest
    11: 1497,   # Undercity, Tirisfal
    13: 267,    # Tarren Mill, Hillsbrad
    17: 45,     # Hammerfall, Arathi
    18: 33,     # Booty Bay, Stranglethorn (Horde)
    20: 33,     # Grom'gol, Stranglethorn
    21: 3,      # Kargath, Badlands
    22: 1638,   # Thunder Bluff, Mulgore
    23: 1637,   # Orgrimmar, Durotar
    25: 17,     # Crossroads, The Barrens
    29: 406,    # Sun Rock Retreat, Stonetalon Mountains
    30: 400,    # Freewind Post, Thousand Needles
    38: 405,    # Shadowprey Village, Desolace
    40: 440,    # Gadgetzan, Tanaris (Horde)
    42: 357,    # Camp Mojache, Feralas
    44: 16,     # Valormok, Azshara
    48: 361,    # Bloodvenom Post, Felwood
    53: 618,    # Everlook, Winterspring (Horde)
    55: 15,     # Brackenwall Village, Dustwallow Marsh
    56: 8,      # Stonard, Swamp of Sorrows
    58: 331,    # Zoram'gar Outpost, Ashenvale
    61: 331,    # Splintertree Post, Ashenvale
    63: 493,    # Nighthaven, Moonglade (Horde)
    68: 139,    # Light's Hope Chapel, Eastern Plaguelands (Horde)
    69: 493,    # Moonglade (Horde)
    70: 46,     # Flame Crest, Burning Steppes
    72: 1377,   # Cenarion Hold, Silithus (Horde)
    75: 51,     # Thorium Point, Searing Gorge (Horde)
    76: 47,     # Revantusk Village, The Hinterlands
    77: 17,     # Camp Taurajo, The Barrens
    3203: 16591,  # Grimreach, Riverglades (Horde)
    # Neutral (faction == 3)
    79: 490,    # Marshal's Refuge, Un'Goro Crater
    80: 17,     # Ratchet, The Barrens
    84: 139,    # Plaguewood Tower, Eastern Plaguelands
    85: 139,    # Northpass Tower, Eastern Plaguelands
    86: 139,    # Eastwall Tower, Eastern Plaguelands
    87: 139,    # Crown Guard Tower, Eastern Plaguelands
}

FLIGHT_NODE_FACTIONS: dict[int, str] = {
    **{nid: "Alliance" for nid in (2, 4, 5, 6, 7, 8, 12, 14, 16, 19, 26, 27, 28, 31, 32, 33, 37, 39, 41, 43, 45, 49, 52, 62, 64, 65, 66, 67, 71, 73, 74, 3276)},
    **{nid: "Horde" for nid in (10, 11, 13, 17, 18, 20, 21, 22, 23, 25, 29, 30, 38, 40, 42, 44, 48, 53, 55, 56, 58, 61, 63, 68, 69, 70, 72, 75, 76, 77, 3203)},
    **{nid: "Both" for nid in (79, 80, 84, 85, 86, 87)},
}

# Verified zone hubs for world zones & capital cities lacking an Alliance flight master.
MANUAL_ZONE_HUBS: dict[int, tuple[str, float, float]] = {
    1: ("Kharanos Inn", 47.4, 52.5),
    3: ("Theldurin / New Kargath Road", 51.4, 76.9),
    8: ("Misty Reed / Harbor Approach", 46.0, 56.0),
    12: ("Goldshire Inn", 43.8, 65.8),
    14: ("Razor Hill", 52.0, 42.0),
    36: ("Dalaran Crater Outpost", 18.4, 65.8),
    85: ("Brill / Tirisfal Approach", 61.0, 52.0),
    130: ("Pyrewood / Dead Drop Approach", 47.1, 71.0),
    215: ("Bloodhoof Village", 47.0, 60.0),
    400: ("Mirage Raceway", 77.8, 77.3),
    1497: ("Undercity Trade Quarter", 63.0, 44.0),
    1637: ("Valley of Strength", 53.3, 66.5),
    1638: ("Thunder Bluff Lower Rise", 46.0, 59.0),
    1657: ("Craftsmen's Terrace, Darnassus", 67.4, 15.7),
    16593: ("Calmbreeze Haven Inn, Zephras Isle", 43.0, 43.2),
    16651: ("Adarien, Innkeeper (Shen'dralas)", 42.79, 70.48),
    41: ("Karazhan / Deadwind Pass Road", 47.0, 75.0),
}

# Land adjacency run edges (symmetric) between world zones (seconds = estimated mounted/foot transit).
LAND_ADJACENCIES: list[tuple[int, int, int, str]] = [
    # Eastern Kingdoms North -> South
    (139, 28, 180, "Eastern Plaguelands <-> Western Plaguelands"),
    (28, 85, 160, "Western Plaguelands <-> Tirisfal Glades"),
    (28, 36, 150, "Western Plaguelands <-> Alterac Mountains (Chillwind)"),
    (28, 267, 170, "Western Plaguelands <-> Hillsbrad Foothills (Chillwind/river)"),
    (28, 47, 210, "Western Plaguelands <-> The Hinterlands (Andorhal/Aerie approach)"),
    (85, 1497, 45, "Tirisfal Glades <-> Undercity"),
    (85, 130, 140, "Tirisfal Glades <-> Silverpine Forest"),
    (130, 267, 150, "Silverpine Forest <-> Hillsbrad Foothills"),
    (267, 36, 120, "Hillsbrad Foothills <-> Alterac Mountains"),
    (267, 47, 180, "Hillsbrad Foothills <-> The Hinterlands (Durnholde/Ravencrest pass)"),
    (267, 45, 150, "Hillsbrad Foothills <-> Arathi Highlands"),
    (45, 11, 150, "Arathi Highlands <-> Wetlands (Thandol Span)"),
    (11, 38, 180, "Wetlands <-> Loch Modan (Dun Algaz tunnel)"),
    (38, 1, 150, "Loch Modan <-> Dun Morogh (South Gate Pass)"),
    (1, 1537, 45, "Dun Morogh <-> Ironforge"),
    (38, 3, 120, "Loch Modan <-> Badlands (southern border pass)"),
    (38, 51, 180, "Loch Modan <-> Searing Gorge (locked Dark Iron gate / Stonewrought route)"),
    (3, 51, 130, "Badlands <-> Searing Gorge"),
    (3, 16591, 180, "Badlands <-> Riverglades (northern pass)"),
    (51, 46, 150, "Searing Gorge <-> Burning Steppes (Blackrock Mountain corridor)"),
    (46, 44, 150, "Burning Steppes <-> Redridge Mountains (Blackrock Pass)"),
    (46, 16591, 180, "Burning Steppes <-> Riverglades (eastern pass)"),
    (44, 16591, 160, "Redridge Mountains <-> Riverglades (eastern fork)"),
    (44, 12, 130, "Redridge Mountains <-> Elwynn Forest"),
    (44, 10, 120, "Redridge Mountains <-> Duskwood"),
    (12, 1519, 45, "Elwynn Forest <-> Stormwind City"),
    (12, 40, 130, "Elwynn Forest <-> Westfall"),
    (12, 10, 120, "Elwynn Forest <-> Duskwood"),
    (40, 10, 110, "Westfall <-> Duskwood"),
    (10, 33, 140, "Duskwood <-> Stranglethorn Vale"),
    (10, 41, 110, "Duskwood <-> Deadwind Pass"),
    (41, 8, 100, "Deadwind Pass <-> Swamp of Sorrows"),
    (8, 4, 110, "Swamp of Sorrows <-> Blasted Lands"),
    (8, 16591, 180, "Swamp of Sorrows <-> Riverglades (northeast coast)"),
    # Kalimdor North -> South
    (141, 1657, 45, "Teldrassil <-> Darnassus"),
    (148, 331, 180, "Darkshore <-> Ashenvale"),
    (331, 361, 160, "Ashenvale <-> Felwood"),
    (331, 16, 180, "Ashenvale <-> Azshara"),
    (331, 406, 160, "Ashenvale <-> Stonetalon Mountains (Talondeep)"),
    (331, 17, 170, "Ashenvale <-> The Barrens"),
    (331, 616, 180, "Ashenvale <-> Mount Hyjal (Nordrassil northern trail)"),
    (361, 493, 140, "Felwood <-> Moonglade (Timbermaw Hold)"),
    (361, 618, 160, "Felwood <-> Winterspring (Timbermaw Hold)"),
    (361, 616, 170, "Felwood <-> Mount Hyjal (Whisperwind / Timbermaw)"),
    (618, 616, 150, "Winterspring <-> Mount Hyjal (Darkwhisper Gorge)"),
    (406, 405, 160, "Stonetalon Mountains <-> Desolace"),
    (17, 406, 180, "The Barrens <-> Stonetalon Mountains"),
    (17, 14, 150, "The Barrens <-> Durotar"),
    (14, 1637, 45, "Durotar <-> Orgrimmar"),
    (17, 215, 150, "The Barrens <-> Mulgore"),
    (215, 1638, 60, "Mulgore <-> Thunder Bluff"),
    (17, 15, 160, "The Barrens <-> Dustwallow Marsh"),
    (17, 400, 170, "The Barrens <-> Thousand Needles (Great Lift)"),
    (405, 16651, 140, "Desolace <-> Shen'dralas (Valley of Bones)"),
    (405, 357, 170, "Desolace <-> Feralas"),
    (16651, 357, 160, "Shen'dralas <-> Feralas"),
    (357, 400, 180, "Feralas <-> Thousand Needles (Thalanaar)"),
    (400, 440, 140, "Thousand Needles <-> Tanaris (Shimmering Flats south ramp)"),
    (440, 490, 150, "Tanaris <-> Un'Goro Crater (west ramp)"),
    (490, 1377, 150, "Un'Goro Crater <-> Silithus (northwest ramp)"),
]

# Complete WoW Forever leveling dungeons (min-max, world entrances, unlock/attunement chains).
CANONICAL_DUNGEONS: list[dict[str, Any]] = [
    {
        "dungeonId": "hall-of-thanes", "zoneId": 16919, "name": "The Hall of Thanes",
        "entranceZoneId": 1537, "entranceCoords": (27.7, 47.9), "minLevel": 13, "maxLevel": 18,
        "recommendedLevel": 16, "faction": "Alliance",
        "unlockChains": [
            {"chainName": "Underground Map / Old Ironforge Incursion", "questIds": [96391, 96393], "minLevel": 9, "travelTip": "Golorn Stonehammer (Dun Morogh 77.0, 60.9) -> Magistrate Brassbeard (64.9, 58.5) -> Ironforge Old Ironforge gate (27.7, 47.9)"},
        ],
    },
    {
        "dungeonId": "ragefire-chasm", "zoneId": 2437, "name": "Ragefire Chasm",
        "entranceZoneId": 1637, "entranceCoords": (52.8, 49.0), "minLevel": 13, "maxLevel": 18,
        "recommendedLevel": 15, "faction": "Horde",
        "unlockChains": [
            {"chainName": "Hidden Enemies (Cleft of Shadow)", "questIds": [5726, 5727, 5728, 5729, 5730], "minLevel": 9, "travelTip": "Thrall (Grommash Hold 31.7, 37.8) -> Skull Rock / Neeru Fireblade (49.5, 50.6) -> Ragefire Chasm"},
        ],
    },
    {
        "dungeonId": "ruins-of-lordaeron", "zoneId": 16611, "name": "Ruins of Lordaeron",
        "entranceZoneId": 1497, "entranceCoords": (72.5, 11.4), "minLevel": 15, "maxLevel": 20,
        "recommendedLevel": 18, "faction": "Both",
        "unlockChains": [
            {"chainName": "Unending Torment (Horde)", "questIds": [97288, 97289, 97290, 97291, 97292], "minLevel": 15, "travelTip": "Undercity courtyard & War Quarter -> Ruins of Lordaeron entrance (72.5, 11.4)"},
            {"chainName": "Remember That I Love You (Alliance)", "questIds": [92415, 95161], "minLevel": 15, "travelTip": "Throne Room ruins (72.5, 11.4) -> Stormwind Cathedral / Keep"},
        ],
    },
    {
        "dungeonId": "wailing-caverns", "zoneId": 718, "name": "Wailing Caverns",
        "entranceZoneId": 17, "entranceCoords": (46.0, 35.8), "minLevel": 15, "maxLevel": 25,
        "recommendedLevel": 20, "faction": "Both",
        "unlockChains": [
            {"chainName": "Leaders of the Fang (Horde)", "questIds": [880, 1489, 1490, 914], "minLevel": 10, "travelTip": "Tonga Runetotem (Crossroads 52.3, 31.9) -> Thunder Bluff Elder Rise -> Naralex"},
        ],
    },
    {
        "dungeonId": "the-deadmines", "zoneId": 1581, "name": "The Deadmines",
        "entranceZoneId": 40, "entranceCoords": (42.5, 71.7), "minLevel": 18, "maxLevel": 23,
        "recommendedLevel": 20, "faction": "Alliance",
        "unlockChains": [
            {"chainName": "The Defias Brotherhood", "questIds": [65, 132, 135, 141, 142, 155, 166], "minLevel": 14, "travelTip": "Sentinel Hill -> Redridge/Stormwind -> Moonbrook"},
        ],
    },
    {
        "dungeonId": "shadowfang-keep", "zoneId": 209, "name": "Shadowfang Keep",
        "entranceZoneId": 130, "entranceCoords": (44.8, 67.8), "minLevel": 22, "maxLevel": 30,
        "recommendedLevel": 25, "faction": "Both", "unlockChains": [],
    },
    {
        "dungeonId": "the-stockade", "zoneId": 717, "name": "The Stockade",
        "entranceZoneId": 1519, "entranceCoords": (49.3, 68.4), "minLevel": 22, "maxLevel": 30,
        "recommendedLevel": 26, "faction": "Alliance",
        "unlockChains": [
            {"chainName": "Defias Brotherhood / Bazil Thredd", "questIds": [166, 373, 389, 391, 392, 393, 350, 2745, 2746], "minLevel": 22, "travelTip": "Finish Deadmines -> Stormwind Keep -> Lakeside/Stockade"},
            {"chainName": "The Fury Runs Deep (Lakeshire)", "questIds": [377, 378], "minLevel": 22, "travelTip": "Deputy Feldon/Magistrate Solomon in Redridge -> Stormwind"},
        ],
    },
    {
        "dungeonId": "excavation-site-wetlands", "zoneId": 16732, "name": "Excavation Site: Wetlands",
        "entranceZoneId": 11, "entranceCoords": (47.8, 56.2), "minLevel": 24, "maxLevel": 29,
        "recommendedLevel": 27, "faction": "Both",
        "unlockChains": [
            {"chainName": "Seeking Caitlin / Lost in the Thicket", "questIds": [95737, 95647, 95809], "minLevel": 24, "travelTip": "No key required. Llana (Ashenvale 35,48) -> Caitlin Grassman (Menethil 11.8,58.6)"},
            {"chainName": "Songblade Search", "questIds": [95772, 95795], "minLevel": 24, "travelTip": "Dorin Songblade (Redridge 25.7,46.6) -> Daewyn inside Excavation Site"},
            {"chainName": "Lost Relic Carry / Prehistoric Prism", "questIds": [95810, 98824], "minLevel": 24, "travelTip": "Interact with / loot relic inside Excavation Site -> Menethil/Greenwarden"},
        ],
    },
    {
        "dungeonId": "blackfathom-deeps", "zoneId": 719, "name": "Blackfathom Deeps",
        "entranceZoneId": 331, "entranceCoords": (14.6, 15.3), "minLevel": 24, "maxLevel": 32,
        "recommendedLevel": 27, "faction": "Both",
        "unlockChains": [
            {"chainName": "Researching the Corruption -> Blackfathom Villainy", "questIds": [3765, 1275, 1199, 1200], "minLevel": 20, "travelTip": "Auberdine / Astranaar -> Darnassus -> Zoram Strand"},
            {"chainName": "Trouble in the Deeps -> Allegiance to the Old Gods (Horde)", "questIds": [6562, 6564, 6565], "minLevel": 17, "travelTip": "Sun Rock Retreat / Zoram'gar Outpost -> Blackfathom Deeps"},
        ],
    },
    {
        "dungeonId": "city-of-dalaran", "zoneId": 16544, "name": "City of Dalaran",
        "entranceZoneId": 36, "entranceCoords": (8.5, 59.3), "minLevel": 28, "maxLevel": 33,
        "recommendedLevel": 31, "faction": "Both",
        "unlockChains": [
            {"chainName": "Dalaran Sewer Key (Alliance)", "questIds": [92432, 92458], "minLevel": 24, "travelTip": "Emissary Jacques (Southshore Town Hall 48.3,60.1) -> Image of Archmage Modera -> unlocks Dalaran Sewer Key"},
        ],
    },
    {
        "dungeonId": "scarlet-monastery", "zoneId": 796, "name": "Scarlet Monastery",
        "entranceZoneId": 85, "entranceCoords": (85.0, 32.0), "minLevel": 28, "maxLevel": 45,
        "recommendedLevel": 36, "faction": "Both",
        "unlockChains": [
            {"chainName": "The Scarlet Key & In the Name of the Light", "questIds": [1050, 1052, 1053], "minLevel": 33, "travelTip": "Loot The Scarlet Key from Doan's Strongbox (Library) for Armory/Cathedral; Southshore Raleigh Chain"},
        ],
    },
    {
        "dungeonId": "gnomeregan", "zoneId": 721, "name": "Gnomeregan",
        "entranceZoneId": 1, "entranceCoords": (24.5, 39.8), "minLevel": 29, "maxLevel": 38,
        "recommendedLevel": 32, "faction": "Both",
        "unlockChains": [
            {"chainName": "The Grand Betrayal & Workshop Key", "questIds": [2923, 2924, 2928, 2929], "minLevel": 24, "travelTip": "Tinkmaster Overspark (Ironforge); Workshop Key drops from Electrocutioner 6000 for backdoor"},
        ],
    },
    {
        "dungeonId": "razorfen-kraul", "zoneId": 491, "name": "Razorfen Kraul",
        "entranceZoneId": 17, "entranceCoords": (42.3, 90.2), "minLevel": 30, "maxLevel": 40,
        "recommendedLevel": 33, "faction": "Both", "unlockChains": [],
    },
    {
        "dungeonId": "the-drowned-city", "zoneId": 16550, "name": "The Drowned City",
        "entranceZoneId": 33, "entranceCoords": (21.3, 27.8), "minLevel": 30, "maxLevel": 40,
        "recommendedLevel": 37, "faction": "Both",
        "unlockChains": [
            {"chainName": "The Drowned City Underwater Portal", "questIds": [95787, 95788, 95789, 95791, 97130], "minLevel": 30, "travelTip": "No key required. Rebel Camp / Booty Bay -> underwater ruins west of Vile Reef (21.3, 27.8)"},
        ],
    },
    {
        "dungeonId": "krol-dok-stronghold", "zoneId": 16842, "name": "Krol'dok Stronghold",
        "entranceZoneId": 16591, "entranceCoords": (39.5, 39.1), "minLevel": 37, "maxLevel": 45,
        "recommendedLevel": 42, "faction": "Both",
        "unlockChains": [
            {"chainName": "Krol'dok Compound Key (Alliance)", "questIds": [94980, 94981], "minLevel": 38, "travelTip": "Loot mould inside Krol'dok Stronghold -> Farholde Keep (Riverglades) for Krol'dok Compound Key (item 267089)"},
            {"chainName": "Twilight Mementos Chain", "questIds": [94983, 94997, 94998], "minLevel": 38, "travelTip": "Farholde Keep <-> northern Riverglades"},
        ],
    },
    {
        "dungeonId": "razorfen-downs", "zoneId": 722, "name": "Razorfen Downs",
        "entranceZoneId": 17, "entranceCoords": (49.3, 93.8), "minLevel": 40, "maxLevel": 50,
        "recommendedLevel": 44, "faction": "Both", "unlockChains": [],
    },
    {
        "dungeonId": "uldaman", "zoneId": 1337, "name": "Uldaman",
        "entranceZoneId": 3, "entranceCoords": (43.5, 13.7), "minLevel": 42, "maxLevel": 52,
        "recommendedLevel": 45, "faction": "Both",
        "unlockChains": [
            {"chainName": "Agmond's Fate / Solution to Doom", "questIds": [704, 709, 722, 2240, 2398], "minLevel": 35, "travelTip": "Loch Modan -> Badlands north entrance"},
            {"chainName": "The Shattered Necklace / Platinum Discs", "questIds": [2198, 2199, 2200, 2201, 2204, 2278, 2279, 2439], "minLevel": 37, "travelTip": "Inside Uldaman -> Ironforge Hall of Explorers"},
        ],
    },
    {
        "dungeonId": "zul-farrak", "zoneId": 1176, "name": "Zul'Farrak",
        "entranceZoneId": 440, "entranceCoords": (38.7, 20.1), "minLevel": 44, "maxLevel": 54,
        "recommendedLevel": 47, "faction": "Both",
        "unlockChains": [
            {"chainName": "Mallet of Zul'Farrak (Gahz'rilla)", "questIds": [3527, 2770], "minLevel": 40, "travelTip": "Sacrifice at Jintha'alor altar (Hinterlands) -> Shimmering Flats -> Zul'Farrak"},
        ],
    },
    {
        "dungeonId": "maraudon", "zoneId": 2100, "name": "Maraudon",
        "entranceZoneId": 405, "entranceCoords": (29.2, 62.6), "minLevel": 46, "maxLevel": 55,
        "recommendedLevel": 49, "faction": "Both",
        "unlockChains": [
            {"chainName": "Scepter of Celebras", "questIds": [7067, 7044, 7046], "minLevel": 41, "travelTip": "Orange/Purple Maraudon wings -> craft Scepter with Celebras the Redeemed"},
        ],
    },
    {
        "dungeonId": "alcaz-prison", "zoneId": 2079, "name": "Alcaz Prison",
        "entranceZoneId": 15, "entranceCoords": (75.3, 14.1), "minLevel": 48, "maxLevel": 53,
        "recommendedLevel": 50, "faction": "Both",
        "unlockChains": [],
    },
    {
        "dungeonId": "the-temple-of-atal-hakkar", "zoneId": 1477, "name": "The Temple of Atal'Hakkar",
        "entranceZoneId": 8, "entranceCoords": (70.0, 53.7), "minLevel": 50, "maxLevel": 60,
        "recommendedLevel": 53, "faction": "Both", "unlockChains": [],
    },
    {
        "dungeonId": "blackrock-depths", "zoneId": 1584, "name": "Blackrock Depths",
        "entranceZoneId": 51, "entranceCoords": (34.8, 85.3), "minLevel": 52, "maxLevel": 60,
        "recommendedLevel": 55, "faction": "Both",
        "unlockChains": [
            {"chainName": "Shadowforge Key (Dark Iron Legacy)", "questIds": [3801, 3802], "minLevel": 48, "travelTip": "Die near BRD anvil -> speak to Franclorn Forgewright outside Blackrock chamber"},
            {"chainName": "Onyxia Alliance Attunement (Windsor)", "questIds": [4182, 4183, 4184, 4185, 4186, 4223, 4224, 4241, 4242, 4264, 4282, 4322, 6402, 6403, 6501, 6502], "minLevel": 50, "travelTip": "Morgan's Vigil (Burning Steppes) -> BRD Marshal Windsor -> Stormwind Keep -> UBRS"},
        ],
    },
    {
        "dungeonId": "blackmaw-hold", "zoneId": 1216, "name": "Blackmaw Hold",
        "entranceZoneId": 16, "entranceCoords": (38.4, 32.6), "minLevel": 55, "maxLevel": 60,
        "recommendedLevel": 58, "faction": "Both", "unlockChains": [],
    },
    {
        "dungeonId": "blackrock-spire", "zoneId": 1583, "name": "Blackrock Spire",
        "entranceZoneId": 46, "entranceCoords": (29.0, 38.0), "minLevel": 55, "maxLevel": 60,
        "recommendedLevel": 58, "faction": "Both",
        "unlockChains": [
            {"chainName": "Seal of Ascension (UBRS Key)", "questIds": [4742, 4743], "minLevel": 57, "travelTip": "LBRS bosses drop 3 gems -> Dustwallow Emberstrife -> UBRS"},
        ],
    },
    {
        "dungeonId": "dire-maul", "zoneId": 2557, "name": "Dire Maul",
        "entranceZoneId": 357, "entranceCoords": (59.0, 41.8), "minLevel": 58, "maxLevel": 60,
        "recommendedLevel": 59, "faction": "Both",
        "unlockChains": [
            {"chainName": "Crescent Key", "questIds": [7441], "minLevel": 56, "travelTip": "Kill Pusillin in Dire Maul East for Crescent Key to West/North"},
        ],
    },
    {
        "dungeonId": "scholomance", "zoneId": 2057, "name": "Scholomance",
        "entranceZoneId": 28, "entranceCoords": (69.6, 73.1), "minLevel": 58, "maxLevel": 60,
        "recommendedLevel": 59, "faction": "Both",
        "unlockChains": [
            {"chainName": "Skeleton Key (Alliance)", "questIds": [5533, 5537, 5538, 5801, 5803, 5505], "minLevel": 55, "travelTip": "Chillwind Camp Commander Ashlam -> WPL/Tanaris/Un'Goro -> Skeleton Key"},
        ],
    },
    {
        "dungeonId": "stratholme", "zoneId": 2017, "name": "Stratholme",
        "entranceZoneId": 139, "entranceCoords": (26.6, 8.9), "minLevel": 58, "maxLevel": 60,
        "recommendedLevel": 59, "faction": "Both",
        "unlockChains": [
            {"chainName": "Key to the City", "questIds": [5122, 5212, 5213, 5214, 5282], "minLevel": 55, "travelTip": "Kill Magistrate Barthilas inside Service Entrance -> drops Key to the City"},
        ],
    },
    {
        "dungeonId": "the-shapers-terrace", "zoneId": 16985, "name": "The Shaper's Terrace",
        "entranceZoneId": 490, "entranceCoords": (50.0, 12.0), "minLevel": 58, "maxLevel": 60,
        "recommendedLevel": 59, "faction": "Both", "unlockChains": [],
    },
]

CONTINENT_NAMES: dict[int, str] = {
    0: "Eastern Kingdoms",
    1: "Kalimdor",
    -1: "Zephras Isle",
}

BAD_NAME_PATTERNS = re.compile(
    r"^(Welcome!|test)$|\b(nyi|deprecated|unused|reuse|dummy|test|temp\s+disabled|zzold)\b|<test>|<txt>|\[PH\]|DND\s+FLAG",
    re.IGNORECASE,
)


def _sync_addon_questiedb_levels(qdb: QuestieDBData) -> int:
    """Overlays updated non-empty min/quest levels from the live installed QuestieDB 1.1.0+ addon."""
    addon_qdb_dir = ADDONS_ROOT / "QuestieDB"
    if QUESTIEDB_DIR == addon_qdb_dir or not (addon_qdb_dir / "QuestieDB_Forever.toc").exists():
        return 0
    addon_qdb = QuestieDBReader(addon_qdb_dir).load()
    updated = 0
    for qid, addon_row in addon_qdb.quests.items():
        base_row = qdb.quests.get(qid)
        if base_row is None:
            continue
        changed = False
        for key in (4, 5):
            val = addon_row.get(key)
            if val is not None and int(val) > 0 and base_row.get(key) != int(val):
                base_row[key] = int(val)
                changed = True
        if changed:
            updated += 1
    return updated


# Rules for ForeverDatasetBuilder: merges vendored QuestieDB Forever (with trace-verified
# quest relations and live QuestieDB 1.1.0+ level sync), ForeverChanges elite/level overrides,
# ForeverLevelingBiS, OpenMap (69 non-BG flights + boats/zeppelins/airships),
# QuestieQuestBlacklist, and cached Wowhead Forever NPC creature types across all 10 races
# and 9 classes cleanly without defaulting missing mid/high-level minLevel to 1, promoting
# optional breadcrumbs into hard prereqs, leaking deprecated FLB-only/seasonal/mount quests,
# or leaking cross-faction/cross-race/cross-class prerequisites; allows Leatherworking
# (165/-182) & Skinning (393) quests while excluding other crafting skills; computes
# classScores (9 classes), rogueScore (== classScores["ROGUE"]), skinningScore,
# beastTargetCount/beastTypes, and lwRewards; enforces [0..100] coords across all Lv 1-60
# isLevelingQuest routes, symmetric acyclic DAG prerequisites, and canonical zones.
class ForeverDatasetBuilder:
    """Extracts, normalizes, and writes WoW Forever JSON datasets and schema documentation."""

    def __init__(self) -> None:
        self.qdb: QuestieDBData = QuestieDBReader(QUESTIEDB_DIR).load()
        _sync_addon_questiedb_levels(self.qdb)
        for breadcrumb_qid, target_qid in FOREVER_BREADCRUMB_TARGETS.items():
            raw_bc = self.qdb.quests.get(breadcrumb_qid)
            if raw_bc is not None and not raw_bc.get(27):
                raw_bc[27] = target_qid
        self.flb_quests: dict[int, dict[str, Any]] = {}
        self.flb_chains: list[list[list[int]]] = []
        self.flb_items: dict[int, dict[str, Any]] = {}
        self.flb_zones: list[dict[str, Any]] = []
        self.flb_dungeons: list[dict[str, Any]] = []
        self.openmap_places: dict[str, Any] = {}
        self.openmap_flights: dict[str, Any] = {}
        self.model_weights: dict[str, Any] = {}
        self.rogue_weights: dict[str, Any] = {}
        self.scorer: RogueItemScorer | None = None
        self.multi_scorer: MultiClassItemScorer | None = None

        self.world_zone_map: dict[int, dict[str, Any]] = {}
        self.dungeon_by_zone: dict[int, dict[str, Any]] = {}
        self.dungeon_by_slug: dict[str, dict[str, Any]] = {}
        self.zone_names: dict[int, str] = {}
        self.zone_hubs: dict[int, dict[str, Any]] = {}
        self.quest_reward_items: dict[int, set[int]] = {}
        self.dungeon_quest_map: dict[int, str] = {}
        self.flb_chain_prev: dict[int, int] = {}
        self.questie_blacklist: set[int] = set()
        self.npc_creature_types: dict[int, dict[str, Any]] = {}

    def _load_addon_sources(self) -> None:
        bl_path = ADDONS_ROOT / "Questie/Database/Corrections/QuestieQuestBlacklist.lua"
        if bl_path.exists():
            bl_txt = bl_path.read_text(encoding="utf-8", errors="ignore")
            self.questie_blacklist = {int(q) for q in re.findall(r"\[(\d+)\]\s*=\s*true\b", bl_txt)}
        q_txt = (FLB_DATA_DIR / "Quests.lua").read_text(encoding="utf-8")
        self.flb_quests = {int(k): v for k, v in parse_lua_table_at(q_txt, r"ns\.Quests\s*=").items()}
        self.flb_chains = parse_lua_table_at(q_txt, r"ns\.QuestChains\s*=")
        self.flb_items = {
            int(k): v
            for k, v in parse_lua_table_at((FLB_DATA_DIR / "Items.lua").read_text(encoding="utf-8"), r"ns\.Items\s*=").items()
        }
        self.flb_zones = parse_lua_table_at((FLB_DATA_DIR / "Zones.lua").read_text(encoding="utf-8"), r"ns\.Zones\s*=")
        self.flb_dungeons = parse_lua_table_at((FLB_DATA_DIR / "Tables.lua").read_text(encoding="utf-8"), r"ns\.Dungeons\s*=")
        mw = parse_lua_table_at((FLB_DATA_DIR / "ModelWeights.lua").read_text(encoding="utf-8"), r"ns\.ModelWeights\s*=")
        self.model_weights = mw
        self.rogue_weights = mw["ROGUE"]
        self.scorer = RogueItemScorer(self.flb_items, self.qdb.items, self.rogue_weights)
        self.multi_scorer = MultiClassItemScorer(self.flb_items, self.qdb.items, self.model_weights)

        self.openmap_places = parse_lua_table_at((OPENMAP_DATA_DIR / "Places.lua").read_text(encoding="utf-8"), r"ns\.MapPlaces\s*=")
        self.openmap_flights = parse_lua_table_at((OPENMAP_DATA_DIR / "FlightData.lua").read_text(encoding="utf-8"), r"ns\.MapFlights\s*=")

        # Index world zones (FLB Zones + verified Forever zones like Mount Hyjal & Shen'dralas) and canonical dungeons.
        for z in list(self.flb_zones) + CUSTOM_WORLD_ZONES:
            zid = int(z["id"])
            self.zone_names[zid] = str(z["name"])
            if not z.get("dungeon") and not z.get("raid"):
                self.world_zone_map[zid] = z

        # Merge QuestieDB item questRewards reverse mapping so bags/special items are caught even if FLB missed them.
        for iid, item_row in self.qdb.items.items():
            qrewards = item_row.get(6)
            if isinstance(qrewards, list):
                for qid in qrewards:
                    if isinstance(qid, int):
                        self.quest_reward_items.setdefault(qid, set()).add(int(iid))

        # Populate dungeon lookups and explicit FLB ns.Dungeons quest assignments.
        for d in CANONICAL_DUNGEONS:
            dzid = int(d["zoneId"])
            self.zone_names.setdefault(dzid, d["name"])
            self.dungeon_by_zone[dzid] = d
            self.dungeon_by_slug[d["dungeonId"]] = d

        for flb_d in self.flb_dungeons:
            dzid = int(flb_d["zoneId"])
            for qentry in flb_d.get("quests") or []:
                if isinstance(qentry, dict) and isinstance(qentry.get("id"), int):
                    if dzid in self.dungeon_by_zone:
                        self.dungeon_quest_map[int(qentry["id"])] = self.dungeon_by_zone[dzid]["dungeonId"]

        # Map explicit Forever custom dungeon quests not in FLB ns.Dungeons.
        extra_dungeon_quests = {
            94968: "krol-dok-stronghold", 94972: "krol-dok-stronghold",
            94973: "krol-dok-stronghold", 94974: "krol-dok-stronghold",
            94975: "krol-dok-stronghold", 94980: "krol-dok-stronghold",
            94981: "krol-dok-stronghold", 94982: "krol-dok-stronghold",
            94983: "krol-dok-stronghold", 94984: "krol-dok-stronghold",
            94997: "krol-dok-stronghold", 94998: "krol-dok-stronghold",
            96771: "krol-dok-stronghold",
            95775: "the-drowned-city", 95776: "the-drowned-city",
            95787: "the-drowned-city", 95788: "the-drowned-city",
            95789: "the-drowned-city", 95791: "the-drowned-city",
            96774: "the-drowned-city", 97130: "the-drowned-city",
            95262: "alcaz-prison", 95263: "alcaz-prison",
            95309: "alcaz-prison", 95454: "alcaz-prison",
            95483: "alcaz-prison", 96332: "alcaz-prison",
            96334: "alcaz-prison", 96335: "alcaz-prison",
            96337: "alcaz-prison", 96369: "alcaz-prison",
            96376: "alcaz-prison", 96377: "alcaz-prison",
            96379: "alcaz-prison", 96381: "alcaz-prison",
            96772: "alcaz-prison",
            92066: "blackmaw-hold", 92069: "blackmaw-hold",
            92343: "blackmaw-hold", 92344: "blackmaw-hold",
            97173: "blackmaw-hold", 97198: "blackmaw-hold",
            97327: "blackmaw-hold",
            95924: "the-shapers-terrace", 95925: "the-shapers-terrace",
            95926: "the-shapers-terrace", 95928: "the-shapers-terrace",
            97049: "the-shapers-terrace",
        }
        for qid, slug in extra_dungeon_quests.items():
            self.dungeon_quest_map.setdefault(qid, slug)

        self._load_npc_creature_types()
        self._build_zone_hubs()

    def _load_npc_creature_types(self) -> None:
        """Loads cached Wowhead Forever NPC creature types, syncing automatically if missing."""
        if not NPC_CREATURE_TYPES_CACHE_PATH.exists():
            raw_npcs = NpcCreatureTypeFetcher(NPC_CREATURE_TYPES_CACHE_PATH).sync()
        else:
            raw_npcs = json.loads(NPC_CREATURE_TYPES_CACHE_PATH.read_text(encoding="utf-8")).get("npcs", {})
        self.npc_creature_types = {int(k): v for k, v in raw_npcs.items() if isinstance(v, dict)}

    def _build_zone_hubs(self) -> None:
        """Selects canonical Flight Master / Inn / Hub coordinates for every world zone."""
        fm_candidates: dict[int, tuple[int, str, float, float]] = {}
        for nid, npc in self.qdb.npcs.items():
            sub = str(npc.get(14) or "").lower()
            frac = str(npc.get(13) or "")
            if not any(t in sub for t in ("gryphon master", "hippogryph master", "flight master")):
                continue
            if frac and "A" not in frac:
                continue
            spawns = npc.get(7)
            if not isinstance(spawns, dict):
                continue
            for z_raw, pts in spawns.items():
                if not pts or not isinstance(pts, list):
                    continue
                pt = self._pick_valid_point(pts)
                if pt:
                    fm_candidates.setdefault(int(z_raw), (int(nid), str(npc.get(1) or "Flight Master"), pt[0], pt[1]))

        for zid, z in self.world_zone_map.items():
            if zid in MANUAL_ZONE_HUBS:
                name, x, y = MANUAL_ZONE_HUBS[zid]
                self.zone_hubs[zid] = {"hubName": name, "zoneId": zid, "x": round(x, 2), "y": round(y, 2)}
            elif zid in fm_candidates:
                _, name, x, y = fm_candidates[zid]
                self.zone_hubs[zid] = {"hubName": f"{name} ({z['name']})", "zoneId": zid, "x": round(x, 2), "y": round(y, 2)}
            else:
                self.zone_hubs[zid] = {"hubName": z["name"], "zoneId": zid, "x": 50.0, "y": 50.0}

    @staticmethod
    def _pick_valid_point(pts: list[Any]) -> tuple[float, float] | None:
        """Returns the first coordinate pair inside [0, 100] x [0, 100]."""
        for pt in pts:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                try:
                    x, y = float(pt[0]), float(pt[1])
                except (TypeError, ValueError):
                    continue
                if 0.0 <= x <= 100.0 and 0.0 <= y <= 100.0:
                    return (round(x, 2), round(y, 2))
        return None

    def _resolve_raw_zone(self, zid: int | None) -> int | None:
        """Maps subzones to their parent world zone or canonical dungeon zone."""
        if not isinstance(zid, int) or zid <= 0:
            return None
        cur = zid
        for _ in range(6):
            if cur in SUBZONE_OVERRIDES:
                cur = SUBZONE_OVERRIDES[cur]
                continue
            if cur in self.world_zone_map or cur in self.dungeon_by_zone or cur in RAID_ZONES or cur in BG_ZONES:
                return cur
            nxt = self.qdb.subzone_to_parent.get(cur)
            if nxt is None or nxt == cur:
                break
            cur = int(nxt)
        return cur

    def _to_world_zone(self, zid: int | None) -> int | None:
        """Resolves any zone ID (including dungeon IDs) to an open-world travel_graph zone ID."""
        resolved = self._resolve_raw_zone(zid)
        if resolved is None:
            return None
        if resolved in self.dungeon_by_zone:
            return int(self.dungeon_by_zone[resolved]["entranceZoneId"])
        if resolved in self.world_zone_map:
            return resolved
        return None

    def _extract_entity_ids(self, container: Any) -> dict[str, list[int]]:
        """Normalizes QuestieDB startedBy / finishedBy containers (both list and sparse int-key dicts)."""
        res: dict[str, list[int]] = {"npc": [], "object": [], "item": []}
        if isinstance(container, list):
            if len(container) >= 1 and isinstance(container[0], list):
                res["npc"] = [int(x) for x in container[0] if isinstance(x, int)]
            if len(container) >= 2 and isinstance(container[1], list):
                res["object"] = [int(x) for x in container[1] if isinstance(x, int)]
            if len(container) >= 3 and isinstance(container[2], list):
                res["item"] = [int(x) for x in container[2] if isinstance(x, int)]
        elif isinstance(container, dict):
            mapping = {1: "npc", 2: "object", 3: "item"}
            for k, kind in mapping.items():
                val = container.get(k) or container.get(str(k))
                if isinstance(val, list):
                    res[kind] = [int(x) for x in val if isinstance(x, int)]
        return res

    def _entity_coords(
        self,
        kind: str,
        eid: int,
        preferred_zone: int | None,
        dungeon_slug: str | None,
    ) -> dict[str, Any] | None:
        """Resolves valid [0..100] world coordinates for an NPC, Object, or Item drop."""
        if kind == "npc":
            ent = self.qdb.npcs.get(eid)
            spawns = ent.get(7) if isinstance(ent, dict) else None
            c = self._pick_spawn_coord(spawns, preferred_zone, dungeon_slug)
            if c is None and eid == 266847:  # Anastasia Miller in Stromgarde Keep (Arathi)
                return {"zoneId": 45, "x": 27.0, "y": 62.0}
            return c
        if kind == "object":
            ent = self.qdb.objects.get(eid)
            spawns = ent.get(4) if isinstance(ent, dict) else None
            return self._pick_spawn_coord(spawns, preferred_zone, dungeon_slug)
        if kind == "item":
            item = self.qdb.items.get(eid)
            if isinstance(item, dict):
                for nid in (item.get(2) or [])[:6]:
                    c = self._entity_coords("npc", int(nid), preferred_zone, dungeon_slug)
                    if c:
                        return c
                for oid in (item.get(3) or [])[:6]:
                    c = self._entity_coords("object", int(oid), preferred_zone, dungeon_slug)
                    if c:
                        return c
        return None

    def _pick_spawn_coord(
        self,
        spawns: Any,
        preferred_zone: int | None,
        dungeon_slug: str | None,
    ) -> dict[str, Any] | None:
        # LibCbor serializes single-zone [1] (Dun Morogh) Lua spawn tables as 1-based arrays.
        if isinstance(spawns, list):
            spawns = {idx + 1: pts for idx, pts in enumerate(spawns) if pts is not None}
        if not isinstance(spawns, dict):
            return None
        world_pref = self._to_world_zone(preferred_zone)
        candidates: list[tuple[int, float, float]] = []
        for raw_z, pts in spawns.items():
            try:
                rz = int(raw_z)
            except (TypeError, ValueError):
                continue
            if not isinstance(pts, list):
                continue
            pt = self._pick_valid_point(pts)
            if not pt:
                # Spawns inside instances often use [-1, -1]; substitute dungeon entrance world coords.
                if rz in self.dungeon_by_zone:
                    d = self.dungeon_by_zone[rz]
                    return {"zoneId": int(d["entranceZoneId"]), "x": float(d["entranceCoords"][0]), "y": float(d["entranceCoords"][1])}
                if rz == 2257:
                    # Deeprun Tram (Stormwind <-> Ironforge) uses [-1, -1] coords in QuestieDB.
                    return {"zoneId": 1519, "x": 63.0, "y": 9.5}
                continue
            wz = self._to_world_zone(rz)
            if wz is not None:
                candidates.append((wz, pt[0], pt[1]))
            elif rz in self.dungeon_by_zone:
                d = self.dungeon_by_zone[rz]
                candidates.append((int(d["entranceZoneId"]), float(d["entranceCoords"][0]), float(d["entranceCoords"][1])))
            elif rz == 2257:
                candidates.append((1519, 63.0, 9.5))

        if not candidates:
            return None
        for wz, x, y in candidates:
            if world_pref is not None and wz == world_pref:
                return {"zoneId": wz, "x": x, "y": y}
        if dungeon_slug and dungeon_slug in self.dungeon_by_slug:
            ez = int(self.dungeon_by_slug[dungeon_slug]["entranceZoneId"])
            for wz, x, y in candidates:
                if wz == ez:
                    return {"zoneId": wz, "x": x, "y": y}
        wz, x, y = candidates[0]
        return {"zoneId": wz, "x": x, "y": y}

    def _entity_name(self, kind: str, eid: int) -> str | None:
        if kind == "npc":
            ent = self.qdb.npcs.get(eid)
            return str(ent.get(1)) if ent and ent.get(1) else None
        if kind == "object":
            ent = self.qdb.objects.get(eid)
            return str(ent.get(1)) if ent and ent.get(1) else None
        if kind == "item":
            ent = self.qdb.items.get(eid) or self.flb_items.get(eid)
            if isinstance(ent, dict):
                return str(ent.get(1) or ent.get("name") or "") or None
        return None

    def _is_faction_friendly_npc(self, nid: int, prefer_faction: int = 1) -> bool:
        ent = self.qdb.npcs.get(nid)
        if not ent:
            return True
        frac = ent.get(13)
        if frac is None or frac == "":
            return True
        frac_s = str(frac)
        if prefer_faction == 2:
            return "H" in frac_s
        if prefer_faction == 3:
            return "A" in frac_s or "H" in frac_s
        return "A" in frac_s

    def _pick_primary_entity(
        self,
        ids_by_kind: dict[str, list[int]],
        preferred_zone: int | None,
        dungeon_slug: str | None,
        allow_item: bool = True,
        prefer_faction: int = 1,
    ) -> tuple[int | None, str, str | None, dict[str, Any] | None]:
        """Selects the best faction-friendly starter/finisher entity and its resolved coords."""
        order = ("npc", "object", "item") if allow_item else ("npc", "object")
        fallback: tuple[int | None, str, str | None, dict[str, Any] | None] | None = None
        for kind in order:
            ids = ids_by_kind.get(kind) or []
            if not ids:
                continue
            # Prefer faction-friendly NPCs first if multiple starters/finishers exist.
            if kind == "npc":
                friendly = [i for i in ids if self._is_faction_friendly_npc(i, prefer_faction)]
                ordered_ids = friendly + [i for i in ids if i not in friendly]
            else:
                ordered_ids = ids
            for eid in ordered_ids:
                name = self._entity_name(kind, eid)
                coord = self._entity_coords(kind, eid, preferred_zone, dungeon_slug)
                if coord is not None:
                    return eid, kind, name, coord
                if fallback is None:
                    fallback = (eid, kind, name, None)
        return fallback if fallback is not None else (None, "npc", None, None)

    def _extract_objectives(
        self,
        qdb_row: dict[Any, Any],
        world_zone: int | None,
        dungeon_slug: str | None,
    ) -> tuple[list[dict[str, Any]], int, str]:
        """Extracts objectiveCoords, objectiveCount, and questKind from QuestieDB rows."""
        coords: list[dict[str, Any]] = []
        seen_coord_keys: set[tuple[int, float, float, str]] = set()
        obj_count = 0
        has_kill = False
        has_collect = False

        def add_coord(c: dict[str, Any] | None, label: str) -> None:
            if not c or len(coords) >= 12:
                return
            key = (int(c["zoneId"]), float(c["x"]), float(c["y"]), label)
            if key not in seen_coord_keys:
                seen_coord_keys.add(key)
                coords.append({"zoneId": int(c["zoneId"]), "x": float(c["x"]), "y": float(c["y"]), "label": label})

        # 1) Direct triggerEnd coordinates
        trig = qdb_row.get(9)
        if isinstance(trig, list) and len(trig) >= 2:
            t_label = str(trig[0] or "Complete objective")
            t_spawns = trig[1]
            c = self._pick_spawn_coord(t_spawns, world_zone, dungeon_slug)
            if c:
                add_coord(c, t_label)
            obj_count += 1

        # 2) Positional objectives: {1=creature, 2=object, 3=item, 4=rep, 5=killCredit, 6=spell}
        raw_obj = qdb_row.get(10)
        groups: dict[int, list[Any]] = {}
        if isinstance(raw_obj, list):
            for idx, grp in enumerate(raw_obj, start=1):
                if isinstance(grp, list) and grp:
                    groups[idx] = grp
        elif isinstance(raw_obj, dict):
            for k, grp in raw_obj.items():
                if isinstance(grp, list) and grp:
                    groups[int(k)] = grp

        for gidx, entries in sorted(groups.items()):
            for entry in entries:
                if not isinstance(entry, (list, tuple)) or not entry:
                    continue
                eid = entry[0]
                if not isinstance(eid, int):
                    continue
                obj_count += 1
                lbl_override = str(entry[1]) if len(entry) >= 2 and entry[1] else None
                if gidx in (1, 5):  # creature or kill-credit
                    has_kill = True
                    npc_ids = [eid]
                    if gidx == 5 and isinstance(entry[0], list):
                        npc_ids = [int(x) for x in entry[0] if isinstance(x, int)]
                    for nid in npc_ids[:3]:
                        lbl = lbl_override or self._entity_name("npc", nid) or f"Target NPC {nid}"
                        add_coord(self._entity_coords("npc", nid, world_zone, dungeon_slug), lbl)
                elif gidx == 2:  # object
                    has_collect = True
                    lbl = lbl_override or self._entity_name("object", eid) or f"Object {eid}"
                    add_coord(self._entity_coords("object", eid, world_zone, dungeon_slug), lbl)
                elif gidx == 3:  # item
                    has_collect = True
                    lbl = lbl_override or self._entity_name("item", eid) or f"Item {eid}"
                    item = self.qdb.items.get(eid)
                    added = False
                    if isinstance(item, dict):
                        for nid in (item.get(2) or [])[:3]:
                            c = self._entity_coords("npc", int(nid), world_zone, dungeon_slug)
                            if c:
                                add_coord(c, f"{lbl} ({self._entity_name('npc', int(nid)) or 'drop'})")
                                added = True
                        for oid in (item.get(3) or [])[:3]:
                            c = self._entity_coords("object", int(oid), world_zone, dungeon_slug)
                            if c:
                                add_coord(c, f"{lbl} ({self._entity_name('object', int(oid)) or 'object'})")
                                added = True
                        for vid in (item.get(14) or [])[:1]:
                            if not added:
                                c = self._entity_coords("npc", int(vid), world_zone, dungeon_slug)
                                if c:
                                    add_coord(c, f"{lbl} (vendor)")
                                    added = True
                    if not added and qdb_row.get(11):
                        # fallback via sourceItemId if item drop absent
                        pass
                elif gidx == 6:  # spell / extra trigger
                    lbl = lbl_override or f"Objective {eid}"
                    obj_count = max(obj_count, 1)

        # 3) extraObjectives (key 29) if nothing found yet
        if not coords and isinstance(qdb_row.get(29), list):
            for ex in qdb_row[29]:
                if isinstance(ex, list) and len(ex) >= 3:
                    sp = ex[0]
                    lbl = str(ex[2] or "Objective")
                    if isinstance(sp, dict):
                        c = self._pick_spawn_coord(sp, world_zone, dungeon_slug)
                        add_coord(c, lbl)
                        obj_count = max(obj_count, 1)

        text_blob = " ".join(str(x) for x in (qdb_row.get(8) or []) if x).lower()
        is_escort = any(w in text_blob for w in ("escort ", "follow ", "safely to ", "protect ")) and not has_collect
        if is_escort:
            kind = "escort"
        elif has_collect:
            kind = "collect"
        elif has_kill:
            kind = "kill"
        elif obj_count > 0:
            kind = "kill"
        else:
            kind = "talk"

        return coords, obj_count, kind

    def _skinnable_creature_kind(self, npc_id: int) -> str | None:
        """Returns 'Beast' or 'Dragonkin' when target NPC is skinnable in WoW Classic/Forever."""
        meta = self.npc_creature_types.get(npc_id) or {}
        ctype = str(meta.get("type") or "")
        family = str(meta.get("family") or "")
        qdb_npc = self.qdb.npcs.get(npc_id) or {}
        npc_name = str(meta.get("name") or qdb_npc.get(1) or "")
        if ctype == "Beast":
            if family in NON_SKINNABLE_BEAST_FAMILIES or NON_SKINNABLE_BEAST_NAME_RE.search(npc_name):
                return None
            return "Beast"
        if ctype == "Dragonkin":
            return "Dragonkin"
        if "yeti" in npc_name.lower() and ctype == "Humanoid":
            return "Beast"
        return None

    @staticmethod
    def _parse_objective_qty(
        target_name: str,
        obj_texts: list[str],
        is_item: bool,
        drop_npc_count: int,
    ) -> int:
        """Extracts required kill/collect quantity from QuestieDB objective text."""
        blob = " ".join(str(x) for x in obj_texts if x)
        if not blob:
            return 5 if is_item else 1
        words = [w.rstrip("s") for w in re.findall(r"[A-Za-z]{4,}", target_name or "")]
        for word in reversed(words):
            m = re.search(rf"\b(\d+)\s+[^.;,]*?\b{re.escape(word)}", blob, re.IGNORECASE)
            if m:
                return max(1, min(100, int(m.group(1))))
        nums = [int(n) for n in re.findall(r"\b(\d+)\b", blob) if 1 <= int(n) <= 80]
        if len(nums) == 1:
            return nums[0]
        if not is_item or "'" in target_name or drop_npc_count <= 1:
            return 1
        return 5

    def _extract_skinning_info(self, qdb_row: dict[Any, Any]) -> tuple[float, int, list[str]]:
        """Scores skinnable Beast/Dragonkin target synergy (0..100), target count, and types."""
        obj_texts = [str(x) for x in (qdb_row.get(8) or []) if x]
        raw_obj = qdb_row.get(10)
        groups: dict[int, list[Any]] = {}
        if isinstance(raw_obj, list):
            for idx, grp in enumerate(raw_obj, start=1):
                if isinstance(grp, list) and grp:
                    groups[idx] = grp
        elif isinstance(raw_obj, dict):
            for k, grp in raw_obj.items():
                if isinstance(grp, list) and grp:
                    groups[int(k)] = grp

        total_objs = 0
        skinnable_objs = 0
        beast_types: set[str] = set()
        beast_target_count = 0
        effective_kills = 0.0

        for gidx, entries in sorted(groups.items()):
            for entry in entries:
                if not isinstance(entry, (list, tuple)) or not entry:
                    continue
                eid = entry[0]
                if gidx in (1, 5):
                    total_objs += 1
                    nids = (
                        [eid]
                        if isinstance(eid, int)
                        else ([int(x) for x in eid if isinstance(x, int)] if isinstance(eid, list) else [])
                    )
                    sk = [k for k in (self._skinnable_creature_kind(n) for n in nids) if k]
                    if sk:
                        skinnable_objs += 1
                        beast_types.update(sk)
                        tname = str(entry[1] or (self.qdb.npcs.get(nids[0]) or {}).get(1) or "")
                        qty = self._parse_objective_qty(tname, obj_texts, is_item=False, drop_npc_count=len(nids))
                        beast_target_count += qty
                        effective_kills += float(qty)
                elif gidx == 2:
                    total_objs += 1
                elif gidx == 3 and isinstance(eid, int):
                    total_objs += 1
                    it = self.qdb.items.get(eid) or {}
                    drop_nids = [int(x) for x in (it.get(2) or [])[:10] if isinstance(x, int)]
                    if drop_nids:
                        sk = [k for k in (self._skinnable_creature_kind(n) for n in drop_nids) if k]
                        if sk and (len(sk) * 2 >= len(drop_nids)):
                            skinnable_objs += 1
                            beast_types.update(sk)
                            iname = str(entry[1] or it.get(1) or "")
                            qty = self._parse_objective_qty(
                                iname, obj_texts, is_item=True, drop_npc_count=len(drop_nids)
                            )
                            beast_target_count += qty
                            effective_kills += float(qty) * 1.35

        if beast_target_count <= 0 or not beast_types:
            return 0.0, 0, []
        ratio = skinnable_objs / max(1, total_objs)
        vol = 22.0 + 63.0 * (1.0 - math.exp(-effective_kills / 10.0))
        dragon_bonus = 7.0 if "Dragonkin" in beast_types else 0.0
        score = min(100.0, vol + 10.0 * ratio + dragon_bonus)
        return round(score, 1), int(beast_target_count), sorted(beast_types)

    def _extract_lw_rewards(self, candidate_item_ids: list[int]) -> list[dict[str, Any]]:
        """Extracts Leatherworking pattern/recipe and skinning tool rewards for a quest."""
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for iid in candidate_item_ids:
            if iid in seen:
                continue
            seen.add(iid)
            flb_it = self.flb_items.get(iid) or {}
            qdb_it = self.qdb.items.get(iid) or {}
            name = str(flb_it.get("name") or qdb_it.get(1) or "")
            if not name:
                continue
            cls = flb_it.get("class") if flb_it.get("class") is not None else qdb_it.get(12)
            sub = flb_it.get("subclass") if flb_it.get("subclass") is not None else qdb_it.get(13)
            quality = int(flb_it.get("quality") or (2 if cls == 9 and sub == 1 else 1))
            stats = flb_it.get("stats") or {}

            kind: str | None = None
            if (cls == 9 and sub == 1) or (
                name.startswith("Pattern:")
                and sub != 2
                and any(
                    w in name.lower()
                    for w in ("leather", "scale", "hide", "deviate", "moonglow", "kodo", "suede", "armor kit")
                )
            ):
                kind = "lw_pattern"
            elif any("skin" in str(k).lower() or "leatherwork" in str(k).lower() for k in stats):
                kind = "skinning_tool"

            if kind:
                out.append({"itemId": int(iid), "name": name, "kind": kind, "quality": quality})
        return out

    def _merge_quest(self, qid: int) -> dict[str, Any]:
        """Builds the initial normalized quest record from QuestieDB + dynamic Alliance fixes + FLB."""
        base_q = dict(self.qdb.quests.get(qid) or {})
        fix_q = self.qdb.alliance_fixes.get(qid)
        flb_q = self.flb_quests.get(qid)
        prov: list[str] = []
        if base_q:
            prov.append("QuestieDB")
        if fix_q:
            base_q.update(fix_q)
            prov.append("QuestieDB-AllianceFix")
        if flb_q:
            prov.append("ForeverLevelingBiS")
        if qid in self.qdb.xp_db:
            prov.append("QuestXP")

        raw_name = base_q.get(1) or (flb_q.get("name") if flb_q else "")
        name = str(raw_name or f"Quest {qid}")
        raw_min = base_q.get(4)
        flb_min = int((flb_q.get("reqLevel") if flb_q else 0) or (flb_q.get("minLevel") if flb_q else 0) or 0)
        quest_level = int(base_q.get(5) or (flb_q.get("level") if flb_q else 0) or (raw_min or flb_min or 1))
        if quest_level <= 0:
            quest_level = max(int(raw_min or flb_min or 1), 1)
        if qid in FOREVER_QUEST_LEVEL_OVERRIDES:
            quest_level = FOREVER_QUEST_LEVEL_OVERRIDES[qid]
        if qid in FOREVER_MIN_LEVEL_OVERRIDES:
            min_level = FOREVER_MIN_LEVEL_OVERRIDES[qid]
        elif raw_min is not None and int(raw_min) > 0:
            min_level = int(raw_min)
        elif flb_min > 1 or (flb_min > 0 and quest_level < 12):
            min_level = flb_min
        elif qid in FOREVER_HYJAL_QIDS:
            min_level = max(54, quest_level - 5)
        elif quest_level >= 10:
            # Missing requiredLevel in QuestieDB must never default mid/high-level quests to 1.
            min_level = max(1, quest_level - 5)
        else:
            min_level = 1

        raw_zone = base_q.get(17)
        if not isinstance(raw_zone, int) or raw_zone == 0:
            raw_zone = flb_q.get("zoneId") if flb_q else None

        dungeon_slug = self.dungeon_quest_map.get(qid)
        resolved_zone = self._resolve_raw_zone(raw_zone)
        if dungeon_slug and dungeon_slug in self.dungeon_by_slug:
            resolved_zone = int(self.dungeon_by_slug[dungeon_slug]["zoneId"])
        elif qid in FOREVER_SHENDRALAS_QIDS and (resolved_zone is None or resolved_zone <= 0 or resolved_zone == 405):
            resolved_zone = 16651
            if "ForeverChanges" not in prov:
                prov.append("ForeverChanges")
        elif qid in FOREVER_HYJAL_QIDS and (resolved_zone is None or resolved_zone <= 0):
            resolved_zone = 616
            if "ForeverChanges" not in prov:
                prov.append("ForeverChanges")
        elif qid in EXPLICIT_WORLD_ZONE_OVERRIDES and (resolved_zone is None or resolved_zone <= 0):
            resolved_zone = EXPLICIT_WORLD_ZONE_OVERRIDES[qid]
            if "ForeverChanges" not in prov:
                prov.append("ForeverChanges")

        # Resolve negative quest sort keys (e.g. -162 Rogue) to a concrete NPC/world zone when available.
        sort_id = raw_zone if isinstance(raw_zone, int) and raw_zone < 0 else None

        starter_ids = self._extract_entity_ids(base_q.get(2))
        finisher_ids = self._extract_entity_ids(base_q.get(3))

        # Supplement starter from FLB if QuestieDB lacks it or FLB has direct coordinates.
        flb_start = flb_q.get("start") if isinstance(flb_q, dict) else None
        if isinstance(flb_start, dict):
            s_kind = str(flb_start.get("kind") or "npc")
            s_id = flb_start.get("id")
            if isinstance(s_id, int) and s_kind in starter_ids and s_id not in starter_ids[s_kind]:
                starter_ids[s_kind].append(s_id)

        flb_side_hint = flb_q.get("side") if flb_q else None
        raw_races_hint = base_q.get(6)
        if raw_races_hint is None and flb_q and flb_q.get("reqRace"):
            raw_races_hint = int(flb_q["reqRace"])
        hint_race_mask = int(raw_races_hint) if isinstance(raw_races_hint, int) else 0
        prefer_faction = 1
        if hint_race_mask != 0:
            ha = bool(hint_race_mask & ALL_ALLIANCE_RACE_MASK)
            hh = bool(hint_race_mask & ALL_HORDE_RACE_MASK)
            if hh and not ha:
                prefer_faction = 2
            elif ha and not hh:
                prefer_faction = 1
            elif flb_side_hint in (1, 2):
                prefer_faction = int(flb_side_hint)
        elif flb_side_hint in (1, 2):
            prefer_faction = int(flb_side_hint)
        elif dungeon_slug and dungeon_slug in self.dungeon_by_slug and self.dungeon_by_slug[dungeon_slug]["faction"] == "Horde":
            prefer_faction = 2

        starter_id, starter_kind, starter_name, starter_coord = self._pick_primary_entity(
            starter_ids, resolved_zone, dungeon_slug, allow_item=True, prefer_faction=prefer_faction
        )
        turnin_id, turnin_kind, turnin_name, turnin_coord = self._pick_primary_entity(
            finisher_ids, resolved_zone, dungeon_slug, allow_item=False, prefer_faction=prefer_faction
        )

        # Fallback starter coord/name/id from FLB start block when QuestieDB entity lacked world spawns.
        if isinstance(flb_start, dict):
            flb_sk = str(flb_start.get("kind") or "npc")
            if starter_id is None and isinstance(flb_start.get("id"), int):
                starter_id = int(flb_start["id"])
                starter_kind = flb_sk if flb_sk in ("npc", "object", "item") else "npc"
            if not starter_name and flb_start.get("name"):
                starter_name = str(flb_start["name"])
            if starter_coord is None and flb_start.get("x") is not None and flb_start.get("y") is not None:
                fz = self._to_world_zone(flb_start.get("zoneId"))
                x, y = float(flb_start["x"]), float(flb_start["y"])
                if fz is not None and 0.0 <= x <= 100.0 and 0.0 <= y <= 100.0:
                    starter_coord = {"zoneId": fz, "x": round(x, 2), "y": round(y, 2)}

        # Infer zoneId for negative sortKeys (-162 Rogue etc.) or missing zoneId using starter -> turnin -> FLB start.
        if resolved_zone is None or resolved_zone <= 0:
            if starter_coord:
                resolved_zone = int(starter_coord["zoneId"])
            elif turnin_coord:
                resolved_zone = int(turnin_coord["zoneId"])
            elif isinstance(flb_start, dict) and isinstance(flb_start.get("zoneId"), int):
                resolved_zone = self._resolve_raw_zone(int(flb_start["zoneId"]))
            elif dungeon_slug and dungeon_slug in self.dungeon_by_slug:
                resolved_zone = int(self.dungeon_by_slug[dungeon_slug]["zoneId"])

        if dungeon_slug is None and resolved_zone in self.dungeon_by_zone:
            dungeon_slug = self.dungeon_by_zone[resolved_zone]["dungeonId"]

        is_dungeon = bool(dungeon_slug is not None or (resolved_zone in self.dungeon_by_zone))
        if is_dungeon and dungeon_slug is None and resolved_zone in self.dungeon_by_zone:
            dungeon_slug = self.dungeon_by_zone[resolved_zone]["dungeonId"]

        # Map final zoneId and zoneName cleanly.
        if resolved_zone is None:
            resolved_zone = int(raw_zone) if isinstance(raw_zone, int) else 0
        zone_name = (
            self.zone_names.get(resolved_zone)
            or self.qdb.sort_names.get(resolved_zone)
            or (f"Zone {resolved_zone}" if resolved_zone else "Unknown")
        )

        # Fallback coords for dungeon quests and world quests so 24-45+ / Wetlands quests have valid plot coords.
        d_info = self.dungeon_by_slug.get(dungeon_slug) if dungeon_slug else None
        if d_info and resolved_zone == d_info["zoneId"]:
            ent_coord = {
                "zoneId": int(d_info["entranceZoneId"]),
                "x": float(d_info["entranceCoords"][0]),
                "y": float(d_info["entranceCoords"][1]),
            }
            if starter_coord is None:
                starter_coord = dict(ent_coord)
            if turnin_coord is None:
                turnin_coord = dict(ent_coord)

        world_z = self._to_world_zone(resolved_zone)
        obj_coords, obj_count, q_kind = self._extract_objectives(base_q, world_z, dungeon_slug)

        if starter_coord is None and turnin_coord is not None and flb_start is None and not base_q.get(2):
            starter_coord = dict(turnin_coord)
        if turnin_coord is None and starter_coord is not None:
            turnin_coord = dict(starter_coord)

        # XP & Gold
        xp_qdb = self.qdb.xp_db.get(qid, (0, 0))[1]
        xp_flb = int((flb_q.get("rewards") or {}).get("xp") or 0) if flb_q else 0
        xp = int(xp_qdb or xp_flb or 0)
        gold_copper = int((flb_q.get("rewards") or {}).get("money") or 0) if flb_q else 0

        # Rewards, Bags, Special Rewards & Multi-Class / Rogue Utility Scores
        rewards_tbl = (flb_q.get("rewards") or {}) if flb_q else {}
        choices = [int(i) for i in (rewards_tbl.get("choices") or []) if isinstance(i, int)]
        fixed = [int(i) for i in (rewards_tbl.get("items") or []) if isinstance(i, int)]
        seen_items = set(choices) | set(fixed)
        for extra_iid in sorted(self.quest_reward_items.get(qid, ())):
            if extra_iid not in seen_items and (
                extra_iid in VERIFIED_BAG_SLOTS
                or extra_iid in self.flb_items
                or (self.scorer and self.scorer.is_rogue_usable(extra_iid))
            ):
                fixed.append(extra_iid)
                seen_items.add(extra_iid)

        special_rewards: list[dict[str, Any]] = []
        assert self.multi_scorer is not None
        for iid in fixed:
            entry = self.multi_scorer.build_reward_entry(iid, is_choice=False, quest_level=quest_level)
            if entry:
                special_rewards.append(entry)
        for iid in choices:
            entry = self.multi_scorer.build_reward_entry(iid, is_choice=True, quest_level=quest_level)
            if entry:
                special_rewards.append(entry)

        bag_slots = max((int(r["slots"]) for r in special_rewards if r["kind"] == "bag"), default=0)
        class_scores: dict[str, float] = {
            cls_key: round(
                max((float(r["classScores"][cls_key]) for r in special_rewards), default=0.0),
                1,
            )
            for cls_key in ALL_CLASS_KEYS
        }
        rogue_score = class_scores["ROGUE"]

        # Faction / Race / Class / Eligibility
        flb_side = flb_q.get("side") if flb_q else None
        races = base_q.get(6)
        if races is None and flb_q and flb_q.get("reqRace"):
            races = int(flb_q["reqRace"])
        race_mask = int(races) if isinstance(races, int) else 0

        classes = base_q.get(7)
        if classes is None and flb_q and flb_q.get("reqClass"):
            classes = int(flb_q["reqClass"])
        class_mask = int(classes) if isinstance(classes, int) else 0
        if class_mask == 0 and qid in FOREVER_CLASS_OVERRIDES:
            class_mask = FOREVER_CLASS_OVERRIDES[qid]
            if "ForeverChanges" not in prov:
                prov.append("ForeverChanges")

        # Determine 1=Alliance, 2=Horde, 3=Both; intersect with raceMask faction bits when present.
        if flb_side in (1, 2, 3):
            faction_mask = int(flb_side)
        elif race_mask != 0:
            has_a = bool(race_mask & ALL_ALLIANCE_RACE_MASK)
            has_h = bool(race_mask & ALL_HORDE_RACE_MASK)
            faction_mask = (1 if has_a else 0) | (2 if has_h else 0) or 3
        else:
            all_starter_npcs = starter_ids["npc"] + finisher_ids["npc"]
            if all_starter_npcs:
                can_a = any("A" in str(self.qdb.npcs.get(n, {}).get(13) or "AH") for n in all_starter_npcs)
                can_h = any("H" in str(self.qdb.npcs.get(n, {}).get(13) or "AH") for n in all_starter_npcs)
                faction_mask = (1 if can_a else 0) | (2 if can_h else 0) or 3
            else:
                faction_mask = 3

        if race_mask != 0:
            race_has_a = bool(race_mask & ALL_ALLIANCE_RACE_MASK)
            race_has_h = bool(race_mask & ALL_HORDE_RACE_MASK)
            race_fac = (1 if race_has_a else 0) | (2 if race_has_h else 0)
            if race_fac:
                intersected_fac = faction_mask & race_fac
                faction_mask = intersected_fac if intersected_fac else race_fac

        qflags = int(base_q.get(23) or 0)
        sflags = int(base_q.get(24) or 0)
        is_breadcrumb = bool(base_q.get(27))
        if is_dungeon:
            q_kind = "dungeon"
        elif is_breadcrumb:
            q_kind = "breadcrumb"

        chain_tbl = flb_q.get("chain") if isinstance(flb_q, dict) else None
        chain_id = int(chain_tbl["id"]) if isinstance(chain_tbl, dict) and chain_tbl.get("id") else None
        chain_step = int(chain_tbl["step"]) if isinstance(chain_tbl, dict) and chain_tbl.get("step") else None

        is_group = bool(
            (flb_q and flb_q.get("type") in ("Group", "Elite", "Dungeon", "Raid"))
            or is_dungeon
            or (qflags & 64)
            or (qid in FOREVER_ELITE_QIDS)
        )

        # Alliance Gnome Rogue eligibility & Skinning/Leatherworking checks
        raw_req_skill = base_q.get(18)
        required_skill: dict[str, int] | None = None
        if isinstance(raw_req_skill, list) and len(raw_req_skill) >= 1 and isinstance(raw_req_skill[0], int):
            min_sk = int(raw_req_skill[1]) if len(raw_req_skill) >= 2 and isinstance(raw_req_skill[1], int) else 1
            required_skill = {"skillId": int(raw_req_skill[0]), "minSkill": min_sk}
        req_skill_id = required_skill["skillId"] if required_skill else None
        is_leatherworking = bool(req_skill_id == LEATHERWORKING_SKILL_ID or sort_id == LEATHERWORKING_SORT_ID)
        is_skinning = bool(req_skill_id == SKINNING_SKILL_ID)

        lw_candidate_ids = list(fixed) + [i for i in choices if i not in fixed]
        for extra_iid in sorted(self.quest_reward_items.get(qid, ())):
            if extra_iid not in lw_candidate_ids:
                lw_candidate_ids.append(extra_iid)
        lw_rewards = self._extract_lw_rewards(lw_candidate_ids)
        skinning_score, beast_target_count, beast_types = self._extract_skinning_info(base_q)

        is_primary_prof_gated = req_skill_id is not None and req_skill_id not in ALLOWED_SKILL_IDS
        req_rep = base_q.get(19)
        is_rep_grind_gated = isinstance(req_rep, list) and len(req_rep) >= 2 and int(req_rep[1]) > 0
        has_real_name = bool(raw_name)
        has_valid_zone = resolved_zone in self.world_zone_map or resolved_zone in self.dungeon_by_zone
        if raw_min is None and flb_min <= 0 and resolved_zone == 616:
            min_level = max(min_level, 54)

        eff_races = _effective_race_mask(faction_mask, race_mask)
        eff_classes = ALL_PLAYABLE_CLASS_MASK if class_mask == 0 else (class_mask & ALL_PLAYABLE_CLASS_MASK)
        is_flb_only_missing_coords = (not base_q) and starter_coord is None and turnin_coord is None

        is_leveling = True
        if not has_real_name or not has_valid_zone or is_flb_only_missing_coords:
            is_leveling = False
        elif eff_races == 0 or eff_classes == 0:
            is_leveling = False
        elif BAD_NAME_PATTERNS.search(name):
            is_leveling = False
        elif (qflags & DAILY_WEEKLY_MONTHLY_FLAGS) != 0:
            is_leveling = False
        elif (sflags & 1) != 0:
            is_leveling = False
        elif is_rep_grind_gated:
            is_leveling = False
        elif sort_id in SEASONAL_OR_NON_LEVELING_SORTS or sort_id in CRAFT_ONLY_SORTS or (sort_id == -666 and xp == 0):
            is_leveling = False
        elif is_primary_prof_gated or resolved_zone in BG_ZONES or resolved_zone in PROTO_OR_TEST_ZONES or resolved_zone in RAID_ZONES or raw_zone == 16941:
            is_leveling = False
        elif not base_q and qid in self.questie_blacklist:
            is_leveling = False
        elif qid in NON_LEVELING_ZERO_XP_QIDS:
            is_leveling = False
        elif xp == 0 and (starter_id in NON_LEVELING_BOOK_NPCS or turnin_id in NON_LEVELING_BOOK_NPCS):
            is_leveling = False

        eligible = is_leveling
        if faction_mask == 2:
            eligible = False
        elif race_mask != 0 and (race_mask & GNOME_RACE_BIT) == 0:
            eligible = False
        elif class_mask != 0 and (class_mask & ROGUE_CLASS_BIT) == 0:
            eligible = False
        elif d_info and d_info["faction"] == "Horde":
            eligible = False

        return {
            "questId": qid,
            "name": name,
            "zoneId": resolved_zone,
            "zoneName": zone_name,
            "minLevel": min_level,
            "questLevel": quest_level,
            "xp": xp,
            "goldCopper": gold_copper,
            "bagSlots": bag_slots,
            "specialRewards": special_rewards,
            "rogueScore": rogue_score,
            "classScores": class_scores,
            "requiredSkill": required_skill,
            "isLeatherworking": is_leatherworking,
            "isSkinning": is_skinning,
            "lwRewards": lw_rewards,
            "skinningScore": skinning_score,
            "beastTargetCount": beast_target_count,
            "beastTypes": beast_types,
            "prereqIds": [],
            "nextQuestIds": [],
            "exclusiveTo": [],
            "isBreadcrumb": is_breadcrumb,
            "questKind": q_kind,
            "objectiveCount": obj_count,
            "chainId": chain_id,
            "chainStep": chain_step,
            "starterNpcId": starter_id,
            "starterKind": starter_kind,
            "starterName": starter_name,
            "starterCoords": starter_coord,
            "turninNpcId": turnin_id,
            "turninKind": turnin_kind,
            "turninName": turnin_name,
            "turninCoords": turnin_coord,
            "objectiveCoords": obj_coords,
            "isDungeon": is_dungeon,
            "dungeonId": dungeon_slug,
            "isGroup": is_group,
            "factionMask": faction_mask,
            "raceMask": race_mask,
            "classMask": class_mask,
            "isLevelingQuest": is_leveling,
            "eligibleAllianceGnomeRogue": eligible,
            "sourceProvenance": prov,
            # Internal scratch metadata stripped before JSON write
            "_rawQdb": base_q,
        }

    def _build_and_validate_dag(self, quests: dict[int, dict[str, Any]]) -> None:
        """Builds symmetric, acyclic prereqIds/nextQuestIds and enforces prereq eligibility closure."""
        prereqs: dict[int, set[int]] = {qid: set() for qid in quests}
        exclusives: dict[int, set[int]] = {qid: set() for qid in quests}
        rejected_prereqs: dict[int, set[int]] = {qid: set() for qid in quests}

        def is_rfc_compatible(pq: dict[str, Any]) -> bool:
            """True when a quest passes Alliance + Gnome + Rogue race/faction/class masks."""
            if pq["factionMask"] == 2:
                return False
            if pq["raceMask"] != 0 and (pq["raceMask"] & GNOME_RACE_BIT) == 0:
                return False
            if pq["classMask"] != 0 and (pq["classMask"] & ROGUE_CLASS_BIT) == 0:
                return False
            return True

        def are_masks_compatible(tq: dict[str, Any], pq: dict[str, Any]) -> bool:
            """True when at least one race, class, and faction can complete both quests."""
            if (tq["factionMask"] & pq["factionMask"]) == 0:
                return False
            tr = _effective_race_mask(tq["factionMask"], tq["raceMask"])
            pr = _effective_race_mask(pq["factionMask"], pq["raceMask"])
            if (tr & pr) == 0:
                return False
            tc = tq["classMask"] or ALL_PLAYABLE_CLASS_MASK
            pc = pq["classMask"] or ALL_PLAYABLE_CLASS_MASK
            if (tc & pc) == 0:
                return False
            return True

        def accept_prereq(target_id: int, prereq_id: int) -> bool:
            """Adds prereq_id -> target_id unless rejected or jointly faction/race/class incompatible."""
            if prereq_id in rejected_prereqs[target_id]:
                return False
            tq = quests[target_id]
            pq = quests[prereq_id]
            if not are_masks_compatible(tq, pq):
                rejected_prereqs[target_id].add(prereq_id)
                return False
            # Also ensure adding prereq_id does not create an unsatisfiable AND-conjunction with existing prereqs.
            joint_fac = tq["factionMask"] & pq["factionMask"]
            joint_race = _effective_race_mask(tq["factionMask"], tq["raceMask"]) & _effective_race_mask(pq["factionMask"], pq["raceMask"])
            joint_cls = (tq["classMask"] or ALL_PLAYABLE_CLASS_MASK) & (pq["classMask"] or ALL_PLAYABLE_CLASS_MASK)
            for existing_p in prereqs[target_id]:
                ep = quests[existing_p]
                joint_fac &= ep["factionMask"]
                joint_race &= _effective_race_mask(ep["factionMask"], ep["raceMask"])
                joint_cls &= (ep["classMask"] or ALL_PLAYABLE_CLASS_MASK)
            if joint_fac == 0 or joint_race == 0 or joint_cls == 0:
                rejected_prereqs[target_id].add(prereq_id)
                return False
            prereqs[target_id].add(prereq_id)
            return True

        # 1a) QuestieDB direct prerequisites (12=preQuestGroup AND, 13=preQuestSingle OR, 25/34 parents)
        for qid, q in quests.items():
            raw = q["_rawQdb"]
            t_fac = q["factionMask"]
            t_race = _effective_race_mask(q["factionMask"], q["raceMask"])
            t_cls = q["classMask"] or ALL_PLAYABLE_CLASS_MASK
            for p_field in (12, 13):
                p_list = raw.get(p_field)
                if isinstance(p_list, list):
                    valid_ps = [int(p) for p in p_list if isinstance(p, int) and p in quests and p != qid]
                    if p_field == 13 and len(valid_ps) > 1:
                        if q["factionMask"] == 3 and q["isLevelingQuest"]:
                            # Neutral OR list (e.g., [Alliance_intro, Horde_intro]): keep neutral leveling alts;
                            # if alts split across factions with no neutral alt, do not make one faction mandatory.
                            neutral_ps = [
                                p for p in valid_ps
                                if quests[p]["factionMask"] == 3 and quests[p]["isLevelingQuest"] and are_masks_compatible(q, quests[p])
                            ]
                            if neutral_ps:
                                rejected_prereqs[qid].update(p for p in valid_ps if p not in neutral_ps)
                                valid_ps = neutral_ps
                            elif any(quests[p]["factionMask"] == 1 for p in valid_ps) and any(quests[p]["factionMask"] == 2 for p in valid_ps):
                                rejected_prereqs[qid].update(valid_ps)
                                valid_ps = []
                        elif q["eligibleAllianceGnomeRogue"]:
                            # preQuestSingle is an OR list: keep Alliance Gnome Rogue eligible alts when present.
                            compat = [p for p in valid_ps if quests[p]["eligibleAllianceGnomeRogue"]]
                            if compat:
                                rejected_prereqs[qid].update(p for p in valid_ps if p not in compat)
                                valid_ps = compat
                        elif q["isLevelingQuest"]:
                            compat = [p for p in valid_ps if quests[p]["isLevelingQuest"] and are_masks_compatible(q, quests[p])]
                            if compat and len(compat) < len(valid_ps):
                                rejected_prereqs[qid].update(p for p in valid_ps if p not in compat)
                                valid_ps = compat
                        # Check if OR list still holds mutually exclusive class/race branches (e.g., [792 non-Lock, 1499 Lock]):
                        # keep full-coverage OR alts if any exist, otherwise drop mutually exclusive OR branches.
                        if len(valid_ps) > 1 and q["isLevelingQuest"]:
                            broad = [
                                p for p in valid_ps
                                if (quests[p]["factionMask"] & t_fac) == t_fac
                                and (_effective_race_mask(quests[p]["factionMask"], quests[p]["raceMask"]) & t_race) == t_race
                                and ((quests[p]["classMask"] or ALL_PLAYABLE_CLASS_MASK) & t_cls) == t_cls
                            ]
                            if broad:
                                rejected_prereqs[qid].update(p for p in valid_ps if p not in broad)
                                valid_ps = broad
                            else:
                                c_fac, c_race, c_cls = t_fac, t_race, t_cls
                                for p in valid_ps:
                                    c_fac &= quests[p]["factionMask"]
                                    c_race &= _effective_race_mask(quests[p]["factionMask"], quests[p]["raceMask"])
                                    c_cls &= (quests[p]["classMask"] or ALL_PLAYABLE_CLASS_MASK)
                                if c_fac == 0 or c_race == 0 or c_cls == 0:
                                    rejected_prereqs[qid].update(valid_ps)
                                    valid_ps = []
                    elif p_field == 12 and len(valid_ps) > 1:
                        if q["eligibleAllianceGnomeRogue"]:
                            # Mixed racial/class branches inside preQuestGroup (e.g., High Elf variants):
                            # drop RFC-incompatible alts only when compatible steps remain (keeps raid gates).
                            compat = [p for p in valid_ps if is_rfc_compatible(quests[p])]
                            if compat and len(compat) < len(valid_ps):
                                rejected_prereqs[qid].update(p for p in valid_ps if p not in compat)
                                valid_ps = compat
                        elif q["isLevelingQuest"]:
                            compat = [p for p in valid_ps if are_masks_compatible(q, quests[p])]
                            if compat and len(compat) < len(valid_ps):
                                rejected_prereqs[qid].update(p for p in valid_ps if p not in compat)
                                valid_ps = compat
                    for p in valid_ps:
                        accept_prereq(qid, p)

            for field_id in (25, 34):
                parent_q = raw.get(field_id)
                if isinstance(parent_q, int) and parent_q in quests and parent_q != qid:
                    accept_prereq(qid, parent_q)

            ex = raw.get(16)
            if isinstance(ex, list):
                for e in ex:
                    if isinstance(e, int) and e in quests and e != qid:
                        exclusives[qid].add(e)
                        exclusives[e].add(qid)

            bcs = raw.get(28)
            if isinstance(bcs, list):
                for b in bcs:
                    if isinstance(b, int) and b in quests and b != qid:
                        exclusives[qid].add(b)
                        exclusives[b].add(qid)

            bc_for = raw.get(27)
            if isinstance(bc_for, int) and bc_for in quests and bc_for != qid:
                exclusives[qid].add(bc_for)
                exclusives[bc_for].add(qid)

        # 1b) QuestieDB reverse chain links (22=nextQuestInChain, 14=childQuests).
        # Collect per-target candidates first so shared neutral follow-ups drop Horde/wrong-class
        # pointers whenever an Alliance Gnome Rogue compatible branch exists, and never re-add
        # alternatives already rejected from preQuestSingle/preQuestGroup.
        inbound_candidates: dict[int, list[int]] = {qid: [] for qid in quests}
        for qid, q in quests.items():
            raw = q["_rawQdb"]
            nxt = raw.get(22)
            if isinstance(nxt, int) and nxt in quests and nxt != qid:
                inbound_candidates[nxt].append(qid)
            children = raw.get(14)
            if isinstance(children, list):
                for ch in children:
                    if isinstance(ch, int) and ch in quests and ch != qid:
                        inbound_candidates[ch].append(qid)

        for target_id, cand_ids in inbound_candidates.items():
            if not cand_ids:
                continue
            tq = quests[target_id]
            t_race = _effective_race_mask(tq["factionMask"], tq["raceMask"])
            t_cls = tq["classMask"] or ALL_PLAYABLE_CLASS_MASK
            has_compat_inbound = any(
                is_rfc_compatible(quests[p]) and p not in rejected_prereqs[target_id]
                for p in cand_ids
            )
            has_compat_existing = any(is_rfc_compatible(quests[p]) for p in prereqs[target_id])
            has_broad_inbound = any(
                quests[p]["isLevelingQuest"]
                and are_masks_compatible(tq, quests[p])
                and (_effective_race_mask(quests[p]["factionMask"], quests[p]["raceMask"]) & t_race) == t_race
                and ((quests[p]["classMask"] or ALL_PLAYABLE_CLASS_MASK) & t_cls) == t_cls
                and (tq["factionMask"] != 3 or quests[p]["factionMask"] == 3)
                and p not in rejected_prereqs[target_id]
                for p in cand_ids
            )
            for p_id in cand_ids:
                pq = quests[p_id]
                if pq["isBreadcrumb"]:
                    # Optional breadcrumbs lead players to a hub without gating the target quest.
                    continue
                if tq["factionMask"] == 3 and pq["factionMask"] != 3:
                    # Do not let a single-faction intro pointer lock out the opposite faction on a Neutral quest.
                    rejected_prereqs[target_id].add(p_id)
                    continue
                p_race = _effective_race_mask(pq["factionMask"], pq["raceMask"])
                p_cls = pq["classMask"] or ALL_PLAYABLE_CLASS_MASK
                is_narrower = ((p_race & t_race) != t_race) or ((p_cls & t_cls) != t_cls)
                if tq["isLevelingQuest"] and is_narrower and (has_broad_inbound or has_compat_existing):
                    rejected_prereqs[target_id].add(p_id)
                    continue
                if (
                    tq["eligibleAllianceGnomeRogue"]
                    and not is_rfc_compatible(pq)
                    and (has_compat_inbound or has_compat_existing)
                ):
                    rejected_prereqs[target_id].add(p_id)
                    continue
                accept_prereq(target_id, p_id)

        # 2) Fill Forever custom quest chains (>=70000) from FLB QuestChains where QuestieDB lacks prereqs,
        # without reversing level order or connecting optional breadcrumbs/Classic quests.
        for chain in self.flb_chains:
            if not isinstance(chain, list) or len(chain) < 2:
                continue
            for step_idx in range(1, len(chain)):
                prev_alts = [int(x) for x in chain[step_idx - 1] if isinstance(x, int) and int(x) in quests]
                curr_alts = [int(x) for x in chain[step_idx] if isinstance(x, int) and int(x) in quests]
                has_compat_prev = any(is_rfc_compatible(quests[p]) for p in prev_alts)
                for c_id in curr_alts:
                    if prereqs[c_id] or quests[c_id]["isBreadcrumb"]:
                        continue
                    cq = quests[c_id]
                    c_side = cq["factionMask"]
                    for p_id in prev_alts:
                        pq = quests[p_id]
                        if p_id == c_id or pq["isBreadcrumb"]:
                            continue
                        if (
                            cq["eligibleAllianceGnomeRogue"]
                            and has_compat_prev
                            and not is_rfc_compatible(pq)
                        ):
                            continue
                        if c_side == 3 and pq["factionMask"] != 3:
                            continue
                        if not are_masks_compatible(cq, pq):
                            continue
                        is_custom_forever = c_id >= 70000 and p_id >= 70000
                        non_inverted_lvl = pq["questLevel"] <= cq["questLevel"]
                        if is_custom_forever and non_inverted_lvl:
                            accept_prereq(c_id, p_id)

        # Explicitly verified Dalaran Sewer Key prereq link (An Alarming Request -> Heart of Disruption).
        if 92432 in quests and 92458 in quests:
            prereqs[92458].add(92432)

        # Explicitly verified Forever Rogue Syndicate chain progression (Alterac -> Stromgarde -> finish).
        for p_id, n_id in ((96781, 96782), (96782, 96783), (96783, 96785), (96785, 96786)):
            if p_id in quests and n_id in quests:
                prereqs[n_id].add(p_id)

        # 3) Break any cycles deterministically using DFS (prefer cutting edges where prereqLevel > targetLevel).
        edges_removed = 0
        state: dict[int, int] = {}  # 0=unvisited, 1=visiting, 2=done

        def break_cycles_from(start_node: int) -> None:
            nonlocal edges_removed
            stack: list[tuple[int, list[int]]] = [(start_node, sorted(prereqs[start_node]))]
            state[start_node] = 1
            while stack:
                node, neighbors = stack[-1]
                advanced = False
                while neighbors:
                    p = neighbors.pop()
                    st = state.get(p, 0)
                    if st == 1:
                        # Cycle found: drop p -> node
                        prereqs[node].discard(p)
                        edges_removed += 1
                    elif st == 0:
                        state[p] = 1
                        stack.append((p, sorted(prereqs[p])))
                        advanced = True
                        break
                if not advanced:
                    state[node] = 2
                    stack.pop()

        for qid in sorted(quests):
            if state.get(qid, 0) == 0:
                break_cycles_from(qid)

        # 3b) Strict prerequisite eligibility closure:
        # First close isLevelingQuest (pruning dead OR alternatives when a leveling OR alt remains),
        # then close eligibleAllianceGnomeRogue identically.
        for flag_key in ("isLevelingQuest", "eligibleAllianceGnomeRogue"):
            changed = True
            while changed:
                changed = False
                for qid, q in quests.items():
                    if not q[flag_key]:
                        continue
                    bad_ps = [p for p in prereqs[qid] if not quests[p][flag_key]]
                    if not bad_ps:
                        continue
                    raw = q["_rawQdb"]
                    or_only = set(raw.get(13) or []) - set(raw.get(12) or [])
                    kept_or = [p for p in (prereqs[qid] & or_only) if quests[p][flag_key]]
                    if kept_or and all(p in or_only for p in bad_ps):
                        for p in bad_ps:
                            prereqs[qid].discard(p)
                        changed = True
                    else:
                        q[flag_key] = False
                        if flag_key == "isLevelingQuest":
                            q["eligibleAllianceGnomeRogue"] = False
                        changed = True

        # 3c) Narrow any Neutral quest whose mandatory prereqs are all Alliance-only or Horde-only,
        # then prune any downstream prereq edges made cross-faction by that narrowing.
        fac_changed = True
        while fac_changed:
            fac_changed = False
            for qid, q in quests.items():
                if not q["isLevelingQuest"] or not prereqs[qid]:
                    continue
                new_fac = q["factionMask"]
                for p in prereqs[qid]:
                    new_fac &= quests[p]["factionMask"]
                if new_fac in (1, 2) and new_fac != q["factionMask"]:
                    q["factionMask"] = new_fac
                    if new_fac == 2:
                        q["eligibleAllianceGnomeRogue"] = False
                    fac_changed = True

        for qid, q in quests.items():
            if not prereqs[qid]:
                continue
            bad_after_narrow = [p for p in prereqs[qid] if not are_masks_compatible(q, quests[p])]
            for p in bad_after_narrow:
                prereqs[qid].discard(p)

        # 4) Build symmetric nextQuestIds and strip scratch fields.
        next_map: dict[int, set[int]] = {qid: set() for qid in quests}
        for qid, pset in prereqs.items():
            for p in pset:
                next_map[p].add(qid)

        for qid, q in quests.items():
            q["prereqIds"] = sorted(prereqs[qid])
            q["nextQuestIds"] = sorted(next_map[qid])
            q["exclusiveTo"] = sorted(exclusives[qid])
            q.pop("_rawQdb", None)

    def _fill_remaining_wetlands_and_core_coords(self, quests: dict[int, dict[str, Any]]) -> None:
        """
        Ensures every Lv 1-60 leveling quest (including Wetlands, Lv 1-45 starters/chains,
        and 22-60 Alliance Gnome Rogue routes) has valid starter and turnin coordinates,
        falling back to turnin/objective/zone hub coordinates only when QuestieDB/FLB lack spawns.
        """
        for qid, q in quests.items():
            if not (q["isLevelingQuest"] or q["eligibleAllianceGnomeRogue"]):
                continue
            in_focus = q["zoneId"] == 11 or 1 <= q["questLevel"] <= 60
            if not in_focus:
                continue
            wz = self._to_world_zone(q["zoneId"])
            hub = self.zone_hubs.get(wz) if wz else None
            fallback = {"zoneId": int(hub["zoneId"]), "x": float(hub["x"]), "y": float(hub["y"])} if hub else None
            if q["starterCoords"] is None:
                if q["turninCoords"] is not None:
                    q["starterCoords"] = dict(q["turninCoords"])
                elif q["objectiveCoords"]:
                    c0 = q["objectiveCoords"][0]
                    q["starterCoords"] = {"zoneId": int(c0["zoneId"]), "x": float(c0["x"]), "y": float(c0["y"])}
                elif fallback:
                    q["starterCoords"] = dict(fallback)
            if q["turninCoords"] is None:
                if q["starterCoords"] is not None:
                    q["turninCoords"] = dict(q["starterCoords"])
                elif fallback:
                    q["turninCoords"] = dict(fallback)

    def _build_dungeons(self, quests: dict[int, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Builds normalized dungeons.json entries including classic + WoW Forever custom dungeons."""
        out: dict[str, dict[str, Any]] = {}
        quests_by_slug: dict[str, list[int]] = {d["dungeonId"]: [] for d in CANONICAL_DUNGEONS}
        for qid, q in quests.items():
            if q["isDungeon"] and q["dungeonId"] in quests_by_slug:
                quests_by_slug[q["dungeonId"]].append(qid)

        for d in CANONICAL_DUNGEONS:
            slug = d["dungeonId"]
            ez = int(d["entranceZoneId"])
            ex, ey = d["entranceCoords"]
            qids = sorted(quests_by_slug[slug])
            out[slug] = {
                "dungeonId": slug,
                "zoneId": int(d["zoneId"]),
                "name": d["name"],
                "zoneName": self.zone_names.get(int(d["zoneId"]), d["name"]),
                "entranceZoneId": ez,
                "entranceZoneName": self.zone_names.get(ez, f"Zone {ez}"),
                "entranceCoords": {"zoneId": ez, "x": float(ex), "y": float(ey)},
                "minLevel": int(d["minLevel"]),
                "maxLevel": int(d["maxLevel"]),
                "recommendedLevel": int(d["recommendedLevel"]),
                "faction": d["faction"],
                "questIds": qids,
                "unlockChains": d["unlockChains"],
            }
        return out

    def _build_travel_graph(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Builds nodes (world zones & cities) and directed edges (flights, boats, zeppelins, airships, tram, run)."""
        zone_to_flight: dict[int, int] = {}
        zone_to_flight_ids: dict[int, list[int]] = {}
        for fnid in sorted(FLIGHT_NODE_TO_ZONE):
            zid = FLIGHT_NODE_TO_ZONE[fnid]
            zone_to_flight_ids.setdefault(zid, []).append(fnid)
            # Prefer Feathermoon (41) over Thalanaar (31) as primary Alliance Feralas flight node id
            if zid == 357 and fnid == 31:
                continue
            if FLIGHT_NODE_FACTIONS.get(fnid) != "Horde":
                zone_to_flight.setdefault(zid, fnid)
        for fnid in sorted(FLIGHT_NODE_TO_ZONE):
            zid = FLIGHT_NODE_TO_ZONE[fnid]
            zone_to_flight.setdefault(zid, fnid)

        boat_zones = {11, 15, 17, 33, 141, 148, 267, 357, 440, 1519, 16591}
        tram_zones = {1519, 1537}
        zeppelin_zones = {33, 85, 1637}
        airship_zones = {36, 215, 16593}

        nodes: dict[str, dict[str, Any]] = {}
        for zid, z in self.world_zone_map.items():
            if zid not in ZONE_FACTIONS:
                raise ValueError(f"World zone {zid} ({z.get('name')}) missing from ZONE_FACTIONS")
            hub = self.zone_hubs[zid]
            region = int(z.get("region") if z.get("region") is not None else 0)
            nodes[str(zid)] = {
                "zoneId": zid,
                "uiMapId": self.qdb.area_to_uimap.get(zid),
                "name": z["name"],
                "faction": ZONE_FACTIONS[zid],
                "continent": CONTINENT_NAMES.get(region, "Eastern Kingdoms"),
                "region": region,
                "minLevel": int(z["minLevel"]) if z.get("minLevel") is not None else None,
                "maxLevel": int(z["maxLevel"]) if z.get("maxLevel") is not None else None,
                "hubName": hub["hubName"],
                "hubCoords": {"zoneId": hub["zoneId"], "x": hub["x"], "y": hub["y"]},
                "flightNodeId": zone_to_flight.get(zid),
                "flightNodeIds": zone_to_flight_ids.get(zid, []),
                "hasBoat": zid in boat_zones,
                "hasTram": zid in tram_zones,
                "hasZeppelin": zid in zeppelin_zones,
                "hasAirship": zid in airship_zones,
            }

        # 1) Flight edges from OpenMap FlightData.lua at 30 yards/sec across Alliance, Horde, and Neutral networks.
        raw_nodes = self.openmap_flights.get("nodes") or {}
        best_flights: dict[tuple[int, int, str], tuple[int, int, float]] = {}

        def node_faction_bits(fnid: int) -> int:
            info = raw_nodes.get(fnid) or raw_nodes.get(str(fnid))
            if info and len(info) >= 5 and info[4] in (1, 2, 3):
                return int(info[4])
            fac_str = FLIGHT_NODE_FACTIONS.get(fnid, "Both")
            return {"Alliance": 1, "Horde": 2, "Both": 3}[fac_str]

        for path in self.openmap_flights.get("paths") or []:
            if not isinstance(path, list) or len(path) < 4:
                continue
            fn_from, fn_to, yards, cost = int(path[0]), int(path[1]), float(path[2]), int(path[3])
            if fn_from not in FLIGHT_NODE_TO_ZONE or fn_to not in FLIGHT_NODE_TO_ZONE:
                continue
            common_bits = node_faction_bits(fn_from) & node_faction_bits(fn_to)
            if common_bits == 0:
                continue
            edge_fac = {1: "Alliance", 2: "Horde", 3: "Both"}[common_bits]
            zf, zt = FLIGHT_NODE_TO_ZONE[fn_from], FLIGHT_NODE_TO_ZONE[fn_to]
            if zf == zt:
                continue
            secs = max(15, round(yards / 30.0))
            key = (zf, zt, edge_fac)
            prev = best_flights.get(key)
            if prev is None or secs < prev[0]:
                best_flights[key] = (secs, cost, yards)

        edges: list[dict[str, Any]] = []
        for (zf, zt, edge_fac), (secs, cost, yards) in sorted(best_flights.items()):
            edges.append({
                "fromZoneId": zf,
                "toZoneId": zt,
                "mode": "flight",
                "seconds": secs,
                "costCopper": cost,
                "faction": edge_fac,
                "note": f"{edge_fac} Flight {self.zone_names[zf]} -> {self.zone_names[zt]} ({int(yards)} yds)",
            })

        # 2) Deeprun Tram (Stormwind <-> Ironforge)
        for a, b in ((1519, 1537), (1537, 1519)):
            edges.append({
                "fromZoneId": a,
                "toZoneId": b,
                "mode": "tram",
                "seconds": 75,
                "costCopper": 0,
                "faction": "Alliance",
                "note": "Deeprun Tram (Stormwind <-> Ironforge)",
            })

        # 3) Boats (verified from OpenMap Places.lua transports)
        ship_routes = [
            (11, 15, 330, "Alliance", "Boat: Menethil Harbor <-> Theramore Isle"),
            (15, 11, 330, "Alliance", "Boat: Theramore Isle <-> Menethil Harbor"),
            (1519, 148, 300, "Alliance", "Boat: Stormwind Harbor <-> Auberdine"),
            (148, 1519, 300, "Alliance", "Boat: Auberdine <-> Stormwind Harbor"),
            (148, 141, 180, "Alliance", "Boat: Auberdine <-> Rut'theran Village"),
            (141, 148, 180, "Alliance", "Boat: Rut'theran Village <-> Auberdine"),
            (11, 267, 220, "Alliance", "WoW Forever Boat leg: Menethil Harbor -> Southshore"),
            (267, 148, 320, "Alliance", "WoW Forever Boat leg: Southshore -> Auberdine"),
            (148, 11, 315, "Alliance", "WoW Forever Boat leg: Auberdine -> Menethil Harbor"),
            (33, 17, 300, "Both", "Boat: Booty Bay <-> Ratchet"),
            (17, 33, 300, "Both", "Boat: Ratchet <-> Booty Bay"),
            (440, 16591, 300, "Both", "WoW Forever Boat: Steamwheedle Port (Tanaris) <-> Powderfuse Port (Riverglades)"),
            (16591, 440, 300, "Both", "WoW Forever Boat: Powderfuse Port (Riverglades) <-> Steamwheedle Port (Tanaris)"),
        ]
        for a, b, secs, fac, note in ship_routes:
            edges.append({
                "fromZoneId": a,
                "toZoneId": b,
                "mode": "boat",
                "seconds": secs,
                "costCopper": 0,
                "faction": fac,
                "note": note,
            })

        # 3b) Horde Zeppelins (Orgrimmar <-> Tirisfal Glades <-> Stranglethorn Vale)
        zeppelin_routes = [
            (1637, 85, 180, "Zeppelin: Orgrimmar <-> Brill / Undercity (Tirisfal Glades)"),
            (85, 1637, 180, "Zeppelin: Brill / Undercity (Tirisfal Glades) <-> Orgrimmar"),
            (1637, 33, 210, "Zeppelin: Orgrimmar <-> Grom'gol Base Camp (Stranglethorn)"),
            (33, 1637, 210, "Zeppelin: Grom'gol Base Camp (Stranglethorn) <-> Orgrimmar"),
            (85, 33, 210, "Zeppelin: Tirisfal Glades <-> Grom'gol Base Camp (Stranglethorn)"),
            (33, 85, 210, "Zeppelin: Grom'gol Base Camp (Stranglethorn) <-> Tirisfal Glades"),
        ]
        for a, b, secs, note in zeppelin_routes:
            edges.append({
                "fromZoneId": a,
                "toZoneId": b,
                "mode": "zeppelin",
                "seconds": secs,
                "costCopper": 0,
                "faction": "Horde",
                "note": note,
            })

        # 3c) WoW Forever Airships (Alterac Mountains <-> Zephras Isle, Mulgore <-> Zephras Isle)
        airship_routes = [
            (36, 16593, 180, "WoW Forever Airship: Alterac Mountains <-> Zephras Isle"),
            (16593, 36, 180, "WoW Forever Airship: Zephras Isle <-> Alterac Mountains"),
            (215, 16593, 180, "WoW Forever Airship: Mulgore <-> Zephras Isle"),
            (16593, 215, 180, "WoW Forever Airship: Zephras Isle <-> Mulgore"),
        ]
        for a, b, secs, note in airship_routes:
            edges.append({
                "fromZoneId": a,
                "toZoneId": b,
                "mode": "airship",
                "seconds": secs,
                "costCopper": 0,
                "faction": "Both",
                "note": note,
            })

        # 4) Land Adjacencies (bidirectional)
        for a, b, secs, note in LAND_ADJACENCIES:
            edges.append({"fromZoneId": a, "toZoneId": b, "mode": "run", "seconds": secs, "costCopper": 0, "faction": "Both", "note": note})
            edges.append({"fromZoneId": b, "toZoneId": a, "mode": "run", "seconds": secs, "costCopper": 0, "faction": "Both", "note": note})

        return nodes, edges

    def _write_schema_doc(
        self,
        quests: dict[int, dict[str, Any]],
        dungeons: dict[str, dict[str, Any]],
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        elig = [q for q in quests.values() if q["eligibleAllianceGnomeRogue"]]
        lvl = [q for q in quests.values() if q["isLevelingQuest"]]
        lvl_a = [q for q in lvl if q["factionMask"] == 1]
        lvl_h = [q for q in lvl if q["factionMask"] == 2]
        lvl_n = [q for q in lvl if q["factionMask"] == 3]
        elig_22_60 = [q for q in elig if 22 <= q["questLevel"] <= 60]
        wetlands = [q for q in elig if q["zoneId"] == 11 or q["dungeonId"] == "excavation-site-wetlands"]
        bags = sorted((q for q in lvl if q["bagSlots"] > 0), key=lambda q: (-q["bagSlots"], q["minLevel"], q["questId"]))
        top_gear = sorted((q for q in lvl if q["rogueScore"] > 0 and 1 <= q["questLevel"] <= 60), key=lambda q: (-q["rogueScore"], q["questLevel"]))
        missing_xp_24_45 = sum(1 for q in elig if 24 <= q["questLevel"] <= 45 and q["xp"] <= 0 and not q["isBreadcrumb"])
        missing_coords_core = sum(
            1 for q in lvl
            if (q["zoneId"] == 11 or 1 <= q["questLevel"] <= 45)
            and (q["starterCoords"] is None or q["turninCoords"] is None)
        )

        bag_lines = "\n".join(
            f"| {q['questId']} | {q['name']} | {q['zoneName']} | {q['minLevel']}–{q['questLevel']} | {q['bagSlots']} |"
            for q in bags[:15]
        )
        gear_lines = "\n".join(
            f"| {q['questId']} | {q['name']} | {q['zoneName']} | {q['questLevel']} | {q['rogueScore']} | "
            + ", ".join(r["name"] for r in q["specialRewards"] if r["rogueScore"] > 0)[:65] + " |"
            for q in top_gear[:15]
        )
        lw_skin_qs = sorted(
            (q for q in elig if q["isLeatherworking"] or q["isSkinning"] or q["lwRewards"]),
            key=lambda q: (q["questLevel"], q["questId"]),
        )
        beast_qs = sorted(
            (q for q in elig if q["skinningScore"] > 0 and 22 <= q["questLevel"] <= 60),
            key=lambda q: (-q["skinningScore"], q["questLevel"], q["questId"]),
        )
        lw_skin_count = sum(1 for q in elig if q["isLeatherworking"])
        skin_quest_count = sum(1 for q in elig if q["isSkinning"])
        skinnable_count = sum(1 for q in elig if q["skinningScore"] > 0)
        lw_lines = "\n".join(
            f"| {q['questId']} | {q['name']} | {q['zoneName']} | {q['minLevel']}–{q['questLevel']} | "
            + ("LW" if q["isLeatherworking"] else ("Skin" if q["isSkinning"] else "Pattern"))
            + f" ({q['requiredSkill']['minSkill'] if q['requiredSkill'] else 0}) | "
            + (", ".join(r["name"] for r in q["lwRewards"])[:55] or "—")
            + " |"
            for q in lw_skin_qs[:20]
        )
        beast_lines = "\n".join(
            f"| {q['questId']} | {q['name']} | {q['zoneName']} | {q['questLevel']} | {q['skinningScore']} | {q['beastTargetCount']} | {', '.join(q['beastTypes'])} |"
            for q in beast_qs[:15]
        )

        md = f"""# World of Warcraft: Forever (1.60.1) Quest, Dungeon & Travel Dataset Schema

Generated deterministically by `scraper/build_dataset.py` from local WoW Forever add-on stores (`QuestieDB_Forever.toc`, `QuestXP/xpDB-classic.lua`, `QuestXP/xpDB-traces.lua`, `ForeverLevelingBiS/Data/*.lua`, and `OpenMap/Data/Forever/*.lua`), cached Wowhead Forever (`dataEnv=16/4`) NPC creature type metadata (`scraper/cache/npc_creature_types.json`), and live `foreverchanges.pro` Forever quest/dungeon pages plus Classic/Forever XP curve and dungeon unlock metadata across all **10 races**, **9 classes**, and **Levels 1–60**.

## Data Coverage Summary

| Metric | Count |
| --- | ---: |
| Total quests in `data/quests.json` | {len(quests)} |
| Level 1–60 leveling quests (`isLevelingQuest`) | {len(lvl)} |
| Level 1–60 Alliance-only leveling quests (`factionMask == 1`) | {len(lvl_a)} |
| Level 1–60 Horde-only leveling quests (`factionMask == 2`) | {len(lvl_h)} |
| Level 1–60 Neutral leveling quests (`factionMask == 3`) | {len(lvl_n)} |
| Eligible Alliance Gnome Rogue quests (all levels) | {len(elig)} |
| Eligible Alliance Gnome Rogue quests (level 22–60) | {len(elig_22_60)} |
| Eligible Wetlands & Excavation Site quests | {len(wetlands)} |
| Eligible Leatherworking quests (`isLeatherworking`) | {lw_skin_count} |
| Eligible Skinning quests (`isSkinning`) | {skin_quest_count} |
| Eligible Skinnable Beast/Dragonkin quests (`skinningScore > 0`) | {skinnable_count} |
| Playable Races / Classes exported in `travel_graph.json` | {len(RACES)} / {len(CLASSES)} |
| Dungeons in `data/dungeons.json` | {len(dungeons)} |
| World/city nodes in `data/travel_graph.json` | {len(nodes)} |
| Travel edges in `data/travel_graph.json` | {len(edges)} |
| Missing coords on Wetlands or lvl 1–45 leveling quests | {missing_coords_core} |
| Non-breadcrumb lvl 24–45 eligible quests with 0 XP | {missing_xp_24_45} |

## File Schemas

### 1. `data/quests.json`
Top-level keys:
- `meta`: `{{source, build, generatedAt, totalQuests, levelingQuestCount, levelingAllianceCount, levelingHordeCount, levelingNeutralCount, allianceRogueAllCount, allianceRogue22to60Count}}`
- `quests`: map `"<questId>" -> QuestObject`

`QuestObject` fields:
- `questId` (`int`): Quest ID
- `name` (`str`): Quest title
- `zoneId` (`int`): Normalized WoW Forever zone/dungeon ID
- `zoneName` (`str`): Canonical zone/dungeon name
- `minLevel` (`int`): Required minimum character level (missing mid/high-level entries never fall back to `1`; uses Forever/QuestieDB/ForeverLevelingBiS or safe `questLevel - 5` / zone floor)
- `questLevel` (`int`): Quest difficulty/target level
- `xp` (`int`): Verified quest completion XP
- `goldCopper` (`int`): Money reward in copper
- `bagSlots` (`int`): Maximum general bag slot reward (`0` if none; reagent bags appear in `specialRewards` with `kind: "reagent_bag"`)
- `specialRewards` (`list`): `{{itemId, name, kind, slots, quality, itemLevel, isChoice, rogueScore, classScores}}` (`kind` in `"weapon"|"gear"|"bag"|"mount"`, `classScores` maps all 9 class keys to `0.0..100.0`)
- `rogueScore` (`float`): Max Rogue utility score `0..100` across `specialRewards` (`== classScores["ROGUE"]`)
- `classScores` (`dict[str, float]`): Max normalized `0.0..100.0` utility score across `specialRewards` for each of `WARRIOR`, `PALADIN`, `HUNTER`, `ROGUE`, `PRIEST`, `SHAMAN`, `MAGE`, `WARLOCK`, `DRUID`
- `requiredSkill` (`{{"skillId": int, "minSkill": int}}|null`): QuestieDB profession requirement (`165`=Leatherworking, `393`=Skinning, etc.)
- `isLeatherworking` (`bool`): True when `requiredSkill.skillId == 165` or Questie sortKey `-182` (Leatherworking)
- `isSkinning` (`bool`): True when `requiredSkill.skillId == 393` (Skinning)
- `lwRewards` (`list`): `{{itemId, name, kind, quality}}` for Leatherworking patterns/recipes (`kind: "lw_pattern"`) or skinning tools (`"skinning_tool"`)
- `skinningScore` (`float`): `0.0..100.0` skinnable Beast/Dragonkin target synergy from objective NPCs/drops (excludes Spiders/Crabs/non-skinnable beasts)
- `beastTargetCount` (`int`): Estimated skinnable Beast/Dragonkin target kill or skin/hide/scale collect quantity
- `beastTypes` (`list[str]`): Sorted distinct skinnable creature types (`"Beast"`, `"Dragonkin"`)
- `prereqIds` (`list[int]`): Verified prerequisite quest IDs (acyclic DAG, symmetric with `nextQuestIds`, free of cross-faction/cross-race/cross-class leaks)
- `nextQuestIds` (`list[int]`): Follow-up quests unlocked by this quest
- `exclusiveTo` (`list[int]`): Mutually exclusive quest/breadcrumb IDs
- `isBreadcrumb` (`bool`): True if optional breadcrumb leading into another quest
- `questKind` (`str`): `"talk"|"kill"|"collect"|"escort"|"dungeon"|"breadcrumb"`
- `objectiveCount` (`int`): Count of distinct objectives/triggers
- `chainId` (`int|null`), `chainStep` (`int|null`): ForeverLevelingBiS chain identifiers when part of a chain
- `starterNpcId` (`int|null`), `starterKind` (`"npc"|"object"|"item"`), `starterName` (`str|null`), `starterCoords` (`{{zoneId, x, y}}|null`)
- `turninNpcId` (`int|null`), `turninKind` (`"npc"|"object"`), `turninName` (`str|null`), `turninCoords` (`{{zoneId, x, y}}|null`)
- `objectiveCoords` (`list[{{zoneId, x, y, label}}]`)
- `isDungeon` (`bool`), `dungeonId` (`str|null`), `isGroup` (`bool`)
- `factionMask` (`int`: `1`=Alliance, `2`=Horde, `3`=Both), `raceMask` (`int`, supports >31-bit Skyborne bits `2^32`/`2^33`), `classMask` (`int`)
- `isLevelingQuest` (`bool`): True for non-seasonal, non-daily, non-repeatable, non-BG/raid/craft-gated Lv 1–60 leveling quests across all 10 races and 9 classes
- `eligibleAllianceGnomeRogue` (`bool`): Strict subset of `isLevelingQuest` where Alliance/Neutral, permits Gnome (`bit 64`) and Rogue (`bit 8`), and excludes Horde-only dungeons
- `sourceProvenance` (`list[str]`): Contributing data sources

### 2. `data/dungeons.json`
Top-level `{{meta, dungeons}}`, keyed by `dungeonId` slug:
- `dungeonId`, `zoneId`, `name`, `zoneName`, `entranceZoneId`, `entranceZoneName`, `entranceCoords` (`{{zoneId, x, y}}`), `minLevel`, `maxLevel`, `recommendedLevel`, `faction` (`"Alliance"|"Horde"|"Both"`), `questIds` (`list[int]`), `unlockChains` (`list[{{chainName, questIds, minLevel, travelTip}}]`).

### 3. `data/travel_graph.json`
Top-level `{{meta, xpCurve, races, classes, nodes, edges}}`:
- `xpCurve`: `"1".."59"` XP required to advance to level `L+1` (`4,084,700` total XP from 1 to 60).
- `races`: list of 10 canonical WoW Forever races (`{{id, name, label, raceBit, faction, startZoneId, startCoords, classes}}`, including Classic races and custom Skyborne races `95`/`96`).
- `classes`: list of 9 canonical WoW Forever classes (`{{id, key, label, classBit, races}}`).
- `nodes`: keyed by `"<zoneId>"` -> `{{zoneId, uiMapId, name, faction ("Alliance"|"Horde"|"Contested"), continent, region, minLevel, maxLevel, hubName, hubCoords, flightNodeId, flightNodeIds, hasBoat, hasTram, hasZeppelin, hasAirship}}`.
- `edges`: directed edges `{{fromZoneId, toZoneId, mode ("run"|"flight"|"boat"|"zeppelin"|"airship"|"tram"|"hearth"), seconds, costCopper, faction ("Alliance"|"Horde"|"Both"), note}}`. Covers all 69 non-BG OpenMap flight masters, Deeprun Tram, boats, Horde Zeppelins (`Orgrimmar <-> Tirisfal <-> STV`), and Forever Airships (`Alterac <-> Zephras Isle <-> Mulgore`).

## Sample Verified Bag, Gear & Profession Quests

### Verified Bag Quests
| QuestID | Name | Zone | Levels | Bag Slots |
| --- | --- | --- | --- | ---: |
{bag_lines}

### Top Rogue Upgrade Quests (Level 1–60)
| QuestID | Name | Zone | Quest Lvl | Rogue Score | Reward Items |
| --- | --- | --- | ---: | ---: | --- |
{gear_lines}

### Verified Leatherworking & Skinning Quests
| QuestID | Name | Zone | Levels | Profession (Min Skill) | LW Rewards |
| --- | --- | --- | --- | --- | --- |
{lw_lines}

### Top Skinnable Beast/Dragonkin Synergy Quests (Level 22–60)
| QuestID | Name | Zone | Quest Lvl | Skinning Score | Target Count | Beast Types |
| --- | --- | --- | ---: | ---: | ---: | --- |
{beast_lines}
"""
        (DATA_DIR / "schema.md").write_text(md, encoding="utf-8")

    def build(self) -> None:
        """Executes full dataset extraction, normalization, graph validation, and JSON emission."""
        self._load_addon_sources()
        all_qids = sorted(set(self.qdb.quests.keys()) | set(self.flb_quests.keys()))
        quests: dict[int, dict[str, Any]] = {}
        for qid in all_qids:
            quests[qid] = self._merge_quest(qid)

        self._build_and_validate_dag(quests)
        self._fill_remaining_wetlands_and_core_coords(quests)
        dungeons = self._build_dungeons(quests)
        nodes, edges = self._build_travel_graph()

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lvl_all = [q for q in quests.values() if q["isLevelingQuest"]]
        lvl_a = [q for q in lvl_all if q["factionMask"] == 1]
        lvl_h = [q for q in lvl_all if q["factionMask"] == 2]
        lvl_n = [q for q in lvl_all if q["factionMask"] == 3]
        elig_all = [q for q in quests.values() if q["eligibleAllianceGnomeRogue"]]
        elig_22_60 = [q for q in elig_all if 22 <= q["questLevel"] <= 60]

        quests_payload = {
            "meta": {
                "source": "WoW Forever 1.60.1 (Interface 16001): QuestieDB + ForeverLevelingBiS + OpenMap + QuestXP + ForeverChanges",
                "build": "1.60.1",
                "generatedAt": now_iso,
                "totalQuests": len(quests),
                "levelingQuestCount": len(lvl_all),
                "levelingAllianceCount": len(lvl_a),
                "levelingHordeCount": len(lvl_h),
                "levelingNeutralCount": len(lvl_n),
                "allianceRogueAllCount": len(elig_all),
                "allianceRogue22to60Count": len(elig_22_60),
            },
            "quests": {str(qid): quests[qid] for qid in sorted(quests)},
        }
        dungeons_payload = {
            "meta": {
                "source": "WoW Forever 1.60.1: ForeverLevelingBiS + OpenMap Places + QuestieDB",
                "build": "1.60.1",
                "generatedAt": now_iso,
                "totalDungeons": len(dungeons),
            },
            "dungeons": dungeons,
        }
        travel_payload = {
            "meta": {
                "source": "WoW Forever 1.60.1: OpenMap FlightData/Places (30 yds/s) + Classic/Forever XP Curve",
                "build": "1.60.1",
                "generatedAt": now_iso,
                "totalNodes": len(nodes),
                "totalEdges": len(edges),
                "totalRaces": len(RACES),
                "totalClasses": len(CLASSES),
            },
            "xpCurve": XP_CURVE,
            "races": RACES,
            "classes": CLASSES,
            "nodes": nodes,
            "edges": edges,
        }

        (DATA_DIR / "quests.json").write_text(json.dumps(quests_payload, indent=2, sort_keys=False), encoding="utf-8")
        (DATA_DIR / "dungeons.json").write_text(json.dumps(dungeons_payload, indent=2, sort_keys=False), encoding="utf-8")
        (DATA_DIR / "travel_graph.json").write_text(json.dumps(travel_payload, indent=2, sort_keys=False), encoding="utf-8")
        self._write_schema_doc(quests, dungeons, nodes, edges)

        print(
            f"Built dataset: quests={len(quests)} (lvl_all={len(lvl_all)} [A={len(lvl_a)}, H={len(lvl_h)}, N={len(lvl_n)}], "
            f"elig_all={len(elig_all)}, elig_22_60={len(elig_22_60)}), "
            f"dungeons={len(dungeons)}, travel_nodes={len(nodes)}, travel_edges={len(edges)}"
        )


def main() -> None:
    """Entrypoint to build all WoW Forever dataset files in data/."""
    ForeverDatasetBuilder().build()


if __name__ == "__main__":
    main()
