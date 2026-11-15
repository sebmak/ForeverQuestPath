"""Core data models for the ForeverQuestPath simulator and route optimizer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# Rules: Metadata describing a playable WoW Forever race, its faction alignment,
# WoW bitmask bit value, allowed classes, and Level 1 starting zone and coordinates.
@dataclass(frozen=True)
class RaceInfo:
    race_id: int
    key: str
    label: str
    faction: str
    bitmask: int
    start_zone_id: int
    start_x: float
    start_y: float
    allowed_classes: Tuple[str, ...] = ()


# Rules: Metadata describing a playable WoW Forever class, its WoW class ID,
# UI label, and bitmask value.
@dataclass(frozen=True)
class ClassInfo:
    class_id: int
    key: str
    label: str
    bitmask: int


DEFAULT_RACES: Dict[str, RaceInfo] = {
    "Human": RaceInfo(1, "Human", "Human", "Alliance", 1, 12, 48.2, 42.9, ("WARRIOR", "PALADIN", "ROGUE", "PRIEST", "MAGE", "WARLOCK")),
    "Orc": RaceInfo(2, "Orc", "Orc", "Horde", 2, 14, 43.3, 68.5, ("WARRIOR", "HUNTER", "ROGUE", "SHAMAN", "WARLOCK")),
    "Dwarf": RaceInfo(3, "Dwarf", "Dwarf", "Alliance", 4, 1, 29.0, 67.5, ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST")),
    "NightElf": RaceInfo(4, "NightElf", "Night Elf", "Alliance", 8, 141, 58.7, 44.2, ("WARRIOR", "HUNTER", "ROGUE", "PRIEST", "DRUID")),
    "Scourge": RaceInfo(5, "Scourge", "Undead", "Horde", 16, 85, 30.2, 71.7, ("WARRIOR", "ROGUE", "PRIEST", "MAGE", "WARLOCK")),
    "Tauren": RaceInfo(6, "Tauren", "Tauren", "Horde", 32, 215, 44.9, 77.1, ("WARRIOR", "HUNTER", "SHAMAN", "DRUID")),
    "Gnome": RaceInfo(7, "Gnome", "Gnome", "Alliance", 64, 1, 29.0, 67.5, ("WARRIOR", "ROGUE", "MAGE", "WARLOCK")),
    "Troll": RaceInfo(8, "Troll", "Troll", "Horde", 128, 14, 43.3, 68.5, ("WARRIOR", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE")),
}

DEFAULT_CLASSES: Dict[str, ClassInfo] = {
    "WARRIOR": ClassInfo(1, "WARRIOR", "Warrior", 1),
    "PALADIN": ClassInfo(2, "PALADIN", "Paladin", 2),
    "HUNTER": ClassInfo(3, "HUNTER", "Hunter", 4),
    "ROGUE": ClassInfo(4, "ROGUE", "Rogue", 8),
    "PRIEST": ClassInfo(5, "PRIEST", "Priest", 16),
    "SHAMAN": ClassInfo(7, "SHAMAN", "Shaman", 64),
    "MAGE": ClassInfo(8, "MAGE", "Mage", 128),
    "WARLOCK": ClassInfo(9, "WARLOCK", "Warlock", 256),
    "DRUID": ClassInfo(11, "DRUID", "Druid", 1024),
}

DEFAULT_FACTION_FLIGHTS: Dict[str, Set[int]] = {
    "Alliance": {11, 1, 38, 1537, 1519, 12, 40, 44, 10},
    "Horde": {14, 1637, 17, 215, 1638, 85, 1497, 130},
}


def canonical_race_key(race: str, races: Optional[Dict[str, RaceInfo]] = None) -> str:
    """Normalize display/alias race names (e.g. 'Undead', 'Night Elf') to canonical RaceInfo keys."""
    lookup = races or DEFAULT_RACES
    cleaned = (race or "").strip()
    if cleaned in lookup:
        return cleaned
    lowered = cleaned.lower().replace(" ", "")
    aliases = {
        "undead": "Scourge",
        "scourge": "Scourge",
        "nightelf": "NightElf",
        "highelf": "HighElf",
        "bloodelf": "BloodElf",
    }
    if lowered in aliases and aliases[lowered] in lookup:
        return aliases[lowered]
    for key, info in lookup.items():
        if key.lower().replace(" ", "") == lowered or info.label.lower().replace(" ", "") == lowered:
            return key
    return cleaned or "Gnome"


def canonical_class_key(char_class: str, classes: Optional[Dict[str, ClassInfo]] = None) -> str:
    """Normalize class strings to canonical uppercase classFile keys (e.g. 'ROGUE')."""
    lookup = classes or DEFAULT_CLASSES
    cleaned = (char_class or "").strip().upper().replace(" ", "")
    if cleaned in lookup:
        return cleaned
    for key, info in lookup.items():
        if info.label.upper().replace(" ", "") == cleaned:
            return key
    return cleaned or "ROGUE"


# Rules: Represents a 2D map coordinate within a zone using 0..100 percentage space;
# zone_id must be a positive WoW Forever zone ID and x, y must lie within [0, 100].
@dataclass(frozen=True)
class Coord:
    zone_id: int
    x: float
    y: float
    label: str = ""


# Rules: Immutable descriptor for a notable quest item reward (bag, gear upgrade, or special item).
# slots > 0 only when kind == "bag"; class_scores / rogue_score are normalized to [0, 100].
@dataclass(frozen=True)
class SpecialReward:
    item_id: int
    name: str
    kind: str
    slots: int = 0
    quality: int = 1
    item_level: int = 0
    is_choice: bool = False
    rogue_score: float = 0.0
    class_scores: Dict[str, float] = field(default_factory=dict)

    def score_for_class(self, char_class: str) -> float:
        key = canonical_class_key(char_class)
        if key in self.class_scores:
            return float(self.class_scores[key])
        if key == "ROGUE":
            return float(self.rogue_score)
        return 0.0


# Rules: Normalized representation of a single WoW Forever quest node in the quest DAG.
# All prerequisite and follow-up IDs reference integer quest IDs; missing coords stay None and are
# resolved at runtime via zone hubs without mutating the source quest record; class_scores holds
# normalized [0..100] per-class gear upgrade ratings and profession/skinning attributes record synergy.
@dataclass
class Quest:
    quest_id: int
    name: str
    zone_id: int
    zone_name: str
    min_level: int
    quest_level: int
    xp: int
    gold_copper: int
    bag_slots: int
    rogue_score: float
    special_rewards: List[SpecialReward] = field(default_factory=list)
    prereq_ids: List[int] = field(default_factory=list)
    next_quest_ids: List[int] = field(default_factory=list)
    exclusive_to: List[int] = field(default_factory=list)
    is_breadcrumb: bool = False
    chain_id: Optional[int] = None
    chain_step: Optional[int] = None
    starter_npc_id: Optional[int] = None
    starter_kind: str = "npc"
    starter_name: Optional[str] = None
    starter_coords: Optional[Coord] = None
    turnin_npc_id: Optional[int] = None
    turnin_kind: str = "npc"
    turnin_name: Optional[str] = None
    turnin_coords: Optional[Coord] = None
    objective_coords: List[Coord] = field(default_factory=list)
    is_dungeon: bool = False
    dungeon_id: Optional[str] = None
    is_group: bool = False
    faction_mask: int = 3
    race_mask: int = 0
    class_mask: int = 0
    eligible_alliance_gnome_rogue: bool = True
    quest_kind: Optional[str] = None
    objective_count: Optional[int] = None
    source_provenance: List[str] = field(default_factory=list)
    cluster_id: str = ""
    class_scores: Dict[str, float] = field(default_factory=dict)
    is_leveling_quest: bool = True
    required_skill_id: Optional[int] = None
    required_min_skill: int = 0
    is_leatherworking: bool = False
    is_skinning: bool = False
    lw_rewards: List[Dict[str, Any]] = field(default_factory=list)
    skinning_score: float = 0.0
    beast_target_count: int = 0
    beast_types: List[str] = field(default_factory=list)

    @property
    def faction_label(self) -> str:
        if self.faction_mask == 1:
            return "Alliance"
        if self.faction_mask == 2:
            return "Horde"
        return "Both"

    def effective_quest_level(self, player_level: int) -> int:
        return player_level if self.quest_level <= 0 else self.quest_level

    def score_for_class(self, char_class: str) -> float:
        key = canonical_class_key(char_class)
        if key in self.class_scores:
            return float(self.class_scores[key])
        if key == "ROGUE":
            return float(self.rogue_score)
        return 0.0

    def max_class_score(self) -> float:
        best = float(self.rogue_score)
        for val in self.class_scores.values():
            if float(val) > best:
                best = float(val)
        return best


# Rules: Represents an ordered prerequisite chain leading into one or more dungeon quests.
# quest_ids must be ordered from earliest prerequisite to final dungeon quest.
@dataclass(frozen=True)
class UnlockChain:
    chain_name: str
    quest_ids: List[int]
    min_level: int
    travel_tip: str = ""


# Rules: Represents a WoW Forever dungeon or wing with its entrance location, level window,
# associated quest IDs, and prerequisite unlock chains.
@dataclass
class Dungeon:
    dungeon_id: str
    zone_id: int
    name: str
    zone_name: str
    entrance_zone_id: int
    entrance_zone_name: str
    entrance_coords: Optional[Coord]
    min_level: int
    max_level: int
    recommended_level: int
    faction: str
    quest_ids: List[int] = field(default_factory=list)
    unlock_chains: List[UnlockChain] = field(default_factory=list)


# Rules: Represents a geographic zone hub node in the travel graph.
# Coordinates are in 0..100 zone space; faction is Alliance/Horde/Both and ui_map_id is optional for client pins.
@dataclass
class ZoneNode:
    zone_id: int
    name: str
    continent: str
    region: int
    min_level: int
    max_level: int
    hub_name: str
    hub_coords: Coord
    flight_node_id: Optional[int] = None
    has_boat: bool = False
    has_tram: bool = False
    ui_map_id: Optional[int] = None
    faction: str = "Both"


# Rules: Directed travel edge between two zone IDs with travel mode and estimated seconds.
# faction must be "Alliance", "Horde", or "Both".
@dataclass(frozen=True)
class TravelEdge:
    from_zone_id: int
    to_zone_id: int
    mode: str
    seconds: int
    cost_copper: int = 0
    faction: str = "Both"
    note: str = ""


# Rules: Container holding all loaded and validated quest, dungeon, travel, nav-graph, race/class metadata, and XP curve data.
# Acts as shared read-only reference input to simulation and optimization strategies.
@dataclass
class Dataset:
    quests: Dict[int, Quest]
    dungeons: Dict[str, Dungeon]
    dungeon_by_zone: Dict[int, Dungeon]
    zones: Dict[int, ZoneNode]
    edges: List[TravelEdge]
    xp_curve: Dict[int, int]
    races: Dict[str, RaceInfo] = field(default_factory=lambda: dict(DEFAULT_RACES))
    classes: Dict[str, ClassInfo] = field(default_factory=lambda: dict(DEFAULT_CLASSES))
    zone_uimap_ids: Dict[int, int] = field(default_factory=dict)
    missing_prereqs: Dict[int, List[int]] = field(default_factory=dict)
    broken_cycles_removed: List[Tuple[int, int]] = field(default_factory=list)
    nav_graph: Optional[Any] = None


# Rules: Defines an optimization profile and integer 0..100 UI-aligned objective weights.
# Higher weights emphasize the corresponding dimension during route selection and Lua export.
@dataclass(frozen=True)
class ProfileConfig:
    profile_id: str
    label: str
    description: str
    weights: Dict[str, int]
    dungeon_bonus: float = 1.0
    reward_bonus: float = 1.0
    travel_penalty_scale: float = 1.0
    xp_rate_scale: float = 1.0
    skinning_bonus: float = 1.0


# Rules: Mutable simulation state representing the simulated character at a point in a run.
# Parameterized by faction, race, char_class, profession toggles, and max_level_offset (default +2);
# maintains quest log cap (20), completed/excluded sets, active quests, hearth cooldown, and totals.
@dataclass
class CharacterState:
    level: int = 24
    xp: int = 0
    zone_id: int = 11
    x: float = 10.8
    y: float = 59.6
    faction: str = "Alliance"
    race: str = "Gnome"
    char_class: str = "ROGUE"
    skinning: bool = True
    leatherworking: bool = True
    max_level_offset: int = 2
    hearth_zone_id: int = 11
    hearth_cooldown_remaining: float = 0.0
    elapsed_seconds: float = 0.0
    travel_seconds: float = 0.0
    quest_seconds: float = 0.0
    dungeon_seconds: float = 0.0
    total_xp_earned: int = 0
    quest_xp_earned: int = 0
    mob_xp_earned: int = 0
    total_gold_copper: int = 0
    bags_earned: int = 0
    max_bag_slots_earned: int = 0
    rogue_upgrades: int = 0
    rogue_score_total: float = 0.0
    class_upgrades: int = 0
    class_score_total: float = 0.0
    lw_quests_completed: int = 0
    skinning_quests_completed: int = 0
    beast_quests_completed: int = 0
    total_skinning_score: float = 0.0
    completed_quests: Set[int] = field(default_factory=set)
    excluded_quests: Set[int] = field(default_factory=set)
    active_quests: Set[int] = field(default_factory=set)
    cleared_objectives: Set[int] = field(default_factory=set)
    completed_dungeons: Set[str] = field(default_factory=set)
    discovered_flights: Set[int] = field(default_factory=lambda: {11, 1, 38, 1537, 1519, 12, 40, 44, 10})
    level_timestamps: Dict[int, float] = field(default_factory=dict)

    @classmethod
    def for_character(
        cls,
        dataset: Optional[Dataset] = None,
        faction: Optional[str] = None,
        race: str = "Gnome",
        char_class: str = "ROGUE",
        level: int = 1,
        zone_id: Optional[int] = None,
        skinning: bool = True,
        leatherworking: bool = True,
        max_level_offset: int = 2,
    ) -> "CharacterState":
        races = dataset.races if dataset else DEFAULT_RACES
        classes = dataset.classes if dataset else DEFAULT_CLASSES
        r_key = canonical_race_key(race, races)
        c_key = canonical_class_key(char_class, classes)
        r_info = races.get(r_key)
        resolved_faction = faction or (r_info.faction if r_info else "Alliance")
        resolved_zone = zone_id if zone_id is not None else (r_info.start_zone_id if r_info else (14 if resolved_faction == "Horde" else 1))
        if dataset and resolved_zone in dataset.zones:
            hub = dataset.zones[resolved_zone].hub_coords
            sx, sy = hub.x, hub.y
            if r_info and resolved_zone == r_info.start_zone_id and level <= 2:
                sx, sy = r_info.start_x, r_info.start_y
        elif r_info and resolved_zone == r_info.start_zone_id:
            sx, sy = r_info.start_x, r_info.start_y
        else:
            sx, sy = (10.8, 59.6) if resolved_zone == 11 else (50.0, 50.0)

        if level >= 20:
            flights = set(DEFAULT_FACTION_FLIGHTS.get(resolved_faction, {resolved_zone}))
            flights.add(resolved_zone)
        else:
            flights = {resolved_zone}

        return cls(
            level=level,
            xp=0,
            zone_id=resolved_zone,
            x=sx,
            y=sy,
            faction=resolved_faction,
            race=r_key,
            char_class=c_key,
            skinning=skinning,
            leatherworking=leatherworking,
            max_level_offset=max_level_offset,
            hearth_zone_id=resolved_zone,
            discovered_flights=flights,
        )

    def max_quest_level(self, offset_override: Optional[int] = None) -> int:
        offset = self.max_level_offset if offset_override is None else offset_override
        return self.level + offset

    def within_level_offset(self, quest: Quest, offset_override: Optional[int] = None) -> bool:
        return quest.effective_quest_level(self.level) <= self.max_quest_level(offset_override)

    def clone(self) -> "CharacterState":
        return CharacterState(
            level=self.level,
            xp=self.xp,
            zone_id=self.zone_id,
            x=self.x,
            y=self.y,
            faction=self.faction,
            race=self.race,
            char_class=self.char_class,
            skinning=self.skinning,
            leatherworking=self.leatherworking,
            max_level_offset=self.max_level_offset,
            hearth_zone_id=self.hearth_zone_id,
            hearth_cooldown_remaining=self.hearth_cooldown_remaining,
            elapsed_seconds=self.elapsed_seconds,
            travel_seconds=self.travel_seconds,
            quest_seconds=self.quest_seconds,
            dungeon_seconds=self.dungeon_seconds,
            total_xp_earned=self.total_xp_earned,
            quest_xp_earned=self.quest_xp_earned,
            mob_xp_earned=self.mob_xp_earned,
            total_gold_copper=self.total_gold_copper,
            bags_earned=self.bags_earned,
            max_bag_slots_earned=self.max_bag_slots_earned,
            rogue_upgrades=self.rogue_upgrades,
            rogue_score_total=self.rogue_score_total,
            class_upgrades=self.class_upgrades,
            class_score_total=self.class_score_total,
            lw_quests_completed=self.lw_quests_completed,
            skinning_quests_completed=self.skinning_quests_completed,
            beast_quests_completed=self.beast_quests_completed,
            total_skinning_score=self.total_skinning_score,
            completed_quests=set(self.completed_quests),
            excluded_quests=set(self.excluded_quests),
            active_quests=set(self.active_quests),
            cleared_objectives=set(self.cleared_objectives),
            completed_dungeons=set(self.completed_dungeons),
            discovered_flights=set(self.discovered_flights),
            level_timestamps=dict(self.level_timestamps),
        )


# Rules: Single executable/displayable step in a simulated leveling route.
# Action must be one of ACCEPT, COMPLETE, TURN_IN, TRAVEL, DUNGEON_PREP, DUNGEON_RUN.
@dataclass
class RouteStep:
    index: int
    action: str
    title: str
    zone_id: int
    zone_name: str
    x: float
    y: float
    quest_id: Optional[int] = None
    quest_ids: List[int] = field(default_factory=list)
    dungeon_id: Optional[str] = None
    npc_id: Optional[int] = None
    npc_name: str = ""
    travel_note: str = ""
    why: str = ""
    expected_level: int = 24
    expected_xp_pct: float = 0.0
    est_minutes: float = 1.0
    travel_seconds: float = 0.0
    xp_gain: int = 0
    gold_copper: int = 0
    bag_slots: int = 0
    rogue_score: float = 0.0
    class_score: float = 0.0
    unlocks_dungeon: Optional[str] = None
    unlocks_dungeon_id: Optional[str] = None
    is_leatherworking: bool = False
    is_skinning: bool = False
    skinning_score: float = 0.0
    required_skill_id: Optional[int] = None
    required_min_skill: int = 0


# Rules: Aggregated summary metrics for evaluating and comparing simulated leveling runs.
# All time metrics are in seconds/hours and ratios are derived deterministically from steps/state.
@dataclass
class SimulationMetrics:
    strategy_name: str
    profile_id: str
    start_level: int
    end_level: int
    end_xp_pct: float
    total_steps: int
    quests_completed: int
    dungeon_quests_completed: int
    dungeons_scheduled: List[str]
    total_time_hours: float
    travel_time_hours: float
    travel_share_pct: float
    avg_xp_per_hour: float
    total_xp_earned: int
    quest_xp_earned: int
    mob_xp_earned: int
    total_gold_copper: int
    bags_earned: int
    max_bag_slots_earned: int
    rogue_upgrades: int
    avg_rogue_score: float
    prereq_violations: int
    min_level_violations: int
    level_timestamps_hours: Dict[int, float] = field(default_factory=dict)
    lw_quests_completed: int = 0
    skinning_quests_completed: int = 0
    beast_quests_completed: int = 0
    total_skinning_score: float = 0.0
    faction: str = "Alliance"
    race: str = "Gnome"
    char_class: str = "ROGUE"
    start_zone_id: int = 11
    class_upgrades: int = 0
    avg_class_score: float = 0.0
