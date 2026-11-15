"""Serialize simulation routes, quest DAG, dungeons, travel zones, and condensed NavGraph into GeneratedData.lua and NavData.lua."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .models import Coord, Dataset, Dungeon, ProfileConfig, Quest, RouteStep, SimulationMetrics
from .scoring import MultiObjectiveScorer
from .travel import TravelModel

AREA_TO_UIMAP_FALLBACK: Dict[int, int] = {
    1: 1426,
    3: 1418,
    4: 1419,
    8: 1435,
    10: 1431,
    11: 1437,
    12: 1429,
    14: 1411,
    15: 1445,
    16: 1447,
    17: 1413,
    28: 1422,
    33: 1434,
    36: 1416,
    38: 1432,
    40: 1436,
    41: 1430,
    44: 1433,
    45: 1417,
    46: 1428,
    47: 1425,
    51: 1427,
    85: 1420,
    130: 1421,
    139: 1423,
    141: 1438,
    148: 1439,
    215: 1412,
    267: 1424,
    331: 1440,
    357: 1444,
    361: 1448,
    400: 1441,
    405: 1443,
    406: 1442,
    440: 1446,
    490: 1449,
    493: 1450,
    618: 1452,
    1377: 1451,
    1497: 1458,
    1519: 1453,
    1537: 1455,
    1637: 1454,
    1638: 1456,
    1657: 1457,
    16591: 2548,
    16593: 2521,
    16732: 1437,
}

_LUA_IDENT_BAD = set(" -.:/'\"")


def _lua_escape(val: str) -> str:
    return (
        val.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _is_ident(key: str) -> bool:
    if not key or key[0].isdigit():
        return False
    return all(ch.isalnum() or ch == "_" for ch in key)


def _is_flat(value: Any) -> bool:
    if isinstance(value, (int, float, str, bool)) or value is None:
        return True
    if isinstance(value, (list, tuple)):
        return len(value) <= 10 and all(isinstance(x, (int, float, str, bool)) or x is None for x in value)
    if isinstance(value, Mapping):
        return len(value) <= 10 and all(
            isinstance(v, (int, float, str, bool)) or v is None
            for v in value.values()
        )
    return False


def to_lua(value: Any, indent: int = 0, inline: bool = False, compact: bool = False) -> str:
    """Recursively serialize Python primitives, lists, and dicts into valid Lua 5.1 source."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, str):
        return f'"{_lua_escape(value)}"'
    sep = "," if compact else ", "
    eq = "=" if compact else " = "
    if isinstance(value, (list, tuple)):
        if not value:
            return "{}"
        if compact or inline or (all(isinstance(x, (int, float, str, bool)) for x in value) and len(value) <= 16):
            inner = sep.join(to_lua(x, inline=True, compact=compact) for x in value)
            return "{" + inner + "}"
        pad = " " * (indent + 2)
        end_pad = " " * indent
        if all(isinstance(x, int) and not isinstance(x, bool) for x in value):
            chunks = [", ".join(str(x) for x in value[i : i + 20]) for i in range(0, len(value), 20)]
            return "{\n" + ",\n".join(f"{pad}{c}" for c in chunks) + f"\n{end_pad}}}"
        items = [f"{pad}{to_lua(x, indent + 2, inline=_is_flat(x))}" for x in value]
        return "{\n" + ",\n".join(items) + f"\n{end_pad}}}"
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        parts: List[str] = []
        if compact or inline or (_is_flat(value) and indent >= 2):
            for k, v in value.items():
                if v is None:
                    continue
                if isinstance(k, int):
                    key_expr = f"[{k}]"
                elif isinstance(k, str) and _is_ident(k):
                    key_expr = k
                else:
                    key_expr = f"[{to_lua(str(k), compact=compact)}]"
                parts.append(f"{key_expr}{eq}{to_lua(v, inline=True, compact=compact)}")
            if not parts:
                return "{}"
            return "{" + sep.join(parts) + "}" if compact else "{ " + ", ".join(parts) + " }"
        pad = " " * (indent + 2)
        end_pad = " " * indent
        for k, v in value.items():
            if v is None:
                continue
            if isinstance(k, int):
                key_expr = f"[{k}]"
            elif isinstance(k, str) and _is_ident(k):
                key_expr = k
            else:
                key_expr = f"[{to_lua(str(k))}]"
            parts.append(f"{pad}{key_expr} = {to_lua(v, indent + 2, inline=_is_flat(v))}")
        if not parts:
            return "{}"
        return "{\n" + ",\n".join(parts) + f"\n{end_pad}}}"
    raise TypeError(f"Unsupported type for Lua serialization: {type(value)}")


def _coord_dict(c: Coord | None) -> Dict[str, Any] | None:
    if c is None:
        return None
    return {
        "zoneId": c.zone_id,
        "x": round(c.x, 1),
        "y": round(c.y, 1),
    }


# Rules: Compiles multi-race/class/faction Dataset (Lv 1-60), per-quest classScores and features,
# meta race/class/uiMapId registries, fast data.indexes lookup tables, primary 1-60 profiles,
# starterRoutes (1-24 per starter zone/faction), and hordeRoutes into addon/Data/GeneratedData.lua,
# plus standalone CSR navigation arrays into addon/Data/NavData.lua.
class LuaExporter:
    _STEP_ACTION_PREFIXES: Mapping[str, str] = {
        "ACCEPT": "Accept: ",
        "COMPLETE": "Complete: ",
        "TURN_IN": "Turn In: ",
    }

    dataset: Dataset
    travel_model: TravelModel
    scorer: MultiObjectiveScorer

    def __init__(
        self,
        dataset: Dataset,
        travel_model: TravelModel,
        scorer: MultiObjectiveScorer,
    ) -> None:
        self.dataset = dataset
        self.travel_model = travel_model
        self.scorer = scorer

    @staticmethod
    def _compact_class_scores(scores: Mapping[str, float]) -> Dict[str, float] | None:
        compact = {
            k: round(float(v), 1)
            for k, v in sorted(scores.items())
            if round(float(v), 1) > 0.0
        }
        return compact if compact else None

    @staticmethod
    def _serialize_step(st: RouteStep) -> Dict[str, Any]:
        badges_raw: Dict[str, Any] = {
            "xp": st.xp_gain or None,
            "goldCopper": st.gold_copper or None,
            "bagSlots": st.bag_slots or None,
            "rogueScore": round(st.rogue_score, 1) if round(st.rogue_score, 1) > 0.0 else None,
            "classScore": (
                round(st.class_score if st.class_score > 0.0 else st.rogue_score, 1)
                if round(st.class_score if st.class_score > 0.0 else st.rogue_score, 1) > 0.0
                else None
            ),
            "unlocksDungeon": st.unlocks_dungeon,
            "isLeatherworking": True if st.is_leatherworking else None,
            "isSkinning": True if st.is_skinning else None,
            "skinningScore": round(st.skinning_score, 1) if round(st.skinning_score, 1) > 0.0 else None,
            "requiredSkill": (
                {"skillId": st.required_skill_id, "minSkill": st.required_min_skill}
                if st.required_skill_id is not None
                else None
            ),
        }
        badges = {k: v for k, v in badges_raw.items() if v is not None}
        qids_out = None
        if st.quest_ids and (len(st.quest_ids) > 1 or st.quest_ids[0] != st.quest_id):
            qids_out = list(st.quest_ids)
        tn = st.travel_note or None
        if tn and (
            tn.startswith("Pick up at (")
            or tn.startswith("Turn in at (")
            or tn.startswith("Clear objective at (")
        ):
            tn = None
        return {
            "index": st.index,
            "action": st.action,
            "prepMode": "accept" if st.action == "DUNGEON_PREP" else None,
            "questId": st.quest_id,
            "questIds": qids_out,
            "dungeonId": st.dungeon_id,
            "title": st.title,
            "zoneId": st.zone_id,
            "zoneName": st.zone_name,
            "x": round(st.x, 1),
            "y": round(st.y, 1),
            "npcId": st.npc_id,
            "npcName": st.npc_name or None,
            "travelNote": tn,
            "why": st.why,
            "expectedLevel": st.expected_level,
            "expectedXpPct": round(st.expected_xp_pct, 1),
            "estMinutes": round(st.est_minutes, 1),
            "badges": badges if badges else None,
        }

    @staticmethod
    def _serialize_summary(metrics: SimulationMetrics) -> Dict[str, Any]:
        return {
            "startLevel": metrics.start_level,
            "endLevel": metrics.end_level,
            "totalSteps": metrics.total_steps,
            "totalTimeHours": metrics.total_time_hours,
            "avgXpPerHour": round(metrics.avg_xp_per_hour, 1),
            "dungeonsScheduled": len(metrics.dungeons_scheduled),
            "totalGoldCopper": metrics.total_gold_copper,
            "bagsEarned": metrics.bags_earned,
            "rogueUpgrades": metrics.rogue_upgrades,
            "classUpgrades": metrics.class_upgrades,
            "lwQuestsCompleted": metrics.lw_quests_completed,
            "skinningQuestsCompleted": metrics.skinning_quests_completed,
            "beastQuestsCompleted": metrics.beast_quests_completed,
            "totalSkinningScore": round(metrics.total_skinning_score, 1),
        }

    def build_payload(
        self,
        profile_results: Mapping[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]],
        start_level: int = 1,
        start_zone_id: int = 1,
        starter_routes: Mapping[str, Tuple[str, str, int, str, str, List[RouteStep], SimulationMetrics]] | None = None,
        horde_routes: Mapping[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]] | None = None,
        target_race: str = "Gnome",
        target_class: str = "ROGUE",
        target_faction: str = "Alliance",
        horde_profiles: Sequence[str] | None = ("balanced",),
        default_max_level_offset: int = 2,
    ) -> Dict[str, Any]:
        # Pre-populate cluster IDs across all leveling quests with usable profession skills
        allowed_skills = {165, 393, 129, 185, 356, 762}
        exported_quests = [
            q
            for q in self.dataset.quests.values()
            if q.is_leveling_quest and (q.required_skill_id is None or q.required_skill_id in allowed_skills)
        ]
        self.travel_model.cluster_quests_by_subzone(exported_quests)

        zones_lua: Dict[int, Dict[str, Any]] = {}
        zone_uimaps: Dict[int, int] = dict(AREA_TO_UIMAP_FALLBACK)
        for zid in sorted(self.dataset.zones):
            z = self.dataset.zones[zid]
            uimap = z.ui_map_id or AREA_TO_UIMAP_FALLBACK.get(zid)
            if uimap is not None:
                zone_uimaps[zid] = int(uimap)
            zones_lua[zid] = {
                "id": z.zone_id,
                "name": z.name,
                "region": z.region,
                "faction": z.faction,
                "minLevel": z.min_level,
                "maxLevel": z.max_level,
                "hub": z.hub_name,
                "coords": {"x": round(z.hub_coords.x, 1), "y": round(z.hub_coords.y, 1)},
                "uiMapId": uimap,
            }

        races_lua: Dict[str, Dict[str, Any]] = {}
        for rkey in sorted(self.dataset.races):
            r = self.dataset.races[rkey]
            races_lua[r.key] = {
                "id": r.race_id,
                "key": r.key,
                "label": r.label,
                "faction": r.faction,
                "bitmask": r.bitmask,
                "allowedClasses": list(r.allowed_classes) if r.allowed_classes else None,
                "startZoneId": r.start_zone_id,
                "startCoords": {"x": round(r.start_x, 1), "y": round(r.start_y, 1)},
            }

        classes_lua: Dict[str, Dict[str, Any]] = {}
        for ckey, c in sorted(self.dataset.classes.items(), key=lambda item: item[1].class_id):
            classes_lua[c.key] = {
                "id": c.class_id,
                "key": c.key,
                "label": c.label,
                "bitmask": c.bitmask,
            }

        dungeons_lua: Dict[str, Dict[str, Any]] = {}
        for did in sorted(self.dataset.dungeons):
            d = self.dataset.dungeons[did]
            chains_lua = [
                {
                    "chainName": ch.chain_name,
                    "questIds": list(ch.quest_ids),
                    "minLevel": ch.min_level,
                    "travelTip": ch.travel_tip,
                }
                for ch in d.unlock_chains
            ]
            dungeons_lua[did] = {
                "id": d.dungeon_id,
                "zoneId": d.zone_id,
                "name": d.name,
                "entranceZoneId": d.entrance_zone_id,
                "entranceZoneName": d.entrance_zone_name or self.travel_model.zone_name(d.entrance_zone_id),
                "entranceCoords": _coord_dict(d.entrance_coords or self.travel_model.dungeon_entrance_coord(d)),
                "minLevel": d.min_level,
                "maxLevel": d.max_level,
                "recommendedLevel": d.recommended_level,
                "faction": d.faction,
                "questIds": list(d.quest_ids),
                "unlockChains": chains_lua,
            }

        # Export all Lv 1-60 leveling quests + any referenced prereq/route/dungeon quests
        referenced_ids: set[int] = {q.quest_id for q in exported_quests}
        for q in list(exported_quests):
            referenced_ids.update(q.prereq_ids)
        for d in self.dataset.dungeons.values():
            referenced_ids.update(d.quest_ids)
            for ch in d.unlock_chains:
                referenced_ids.update(ch.quest_ids)
        for _, steps, _ in profile_results.values():
            for st in steps:
                if st.quest_id:
                    referenced_ids.add(st.quest_id)
                referenced_ids.update(st.quest_ids)
        if starter_routes:
            for _, _, _, _, _, steps, _ in starter_routes.values():
                for st in steps:
                    if st.quest_id:
                        referenced_ids.add(st.quest_id)
                    referenced_ids.update(st.quest_ids)
        if horde_routes:
            for hkey, (_, steps, _) in horde_routes.items():
                if horde_profiles is not None and hkey not in horde_profiles:
                    continue
                for st in steps:
                    if st.quest_id:
                        referenced_ids.add(st.quest_id)
                    referenced_ids.update(st.quest_ids)

        quests_lua: Dict[int, Dict[str, Any]] = {}
        for qid in sorted(referenced_ids):
            q = self.dataset.quests.get(qid)
            if not q:
                continue
            sc = self.travel_model.quest_starter_coord(q)
            tc = self.travel_model.quest_turnin_coord(q)
            objs = [
                {
                    "zoneId": oc.zone_id,
                    "x": round(oc.x, 1),
                    "y": round(oc.y, 1),
                    "label": oc.label or q.name,
                }
                for oc in q.objective_coords
            ]
            specials = [
                {
                    "itemId": sr.item_id,
                    "name": sr.name,
                    "kind": sr.kind,
                    "slots": sr.slots,
                    "quality": sr.quality,
                    "rogueScore": round(sr.rogue_score, 1),
                    "classScores": self._compact_class_scores(sr.class_scores),
                }
                for sr in q.special_rewards
            ]
            raw_features = self.scorer.compute_quest_features(q, target_class=target_class)
            compact_features = {"skinningValue": raw_features.get("skinningValue", 0)}
            quests_lua[qid] = {
                "id": q.quest_id,
                "name": q.name,
                "zoneId": q.zone_id,
                "zoneName": q.zone_name,
                "minLevel": q.min_level,
                "questLevel": q.quest_level,
                "xp": q.xp,
                "goldCopper": q.gold_copper or None,
                "bagSlots": q.bag_slots or None,
                "rogueScore": round(q.rogue_score, 1) if round(q.rogue_score, 1) > 0.0 else None,
                "classScores": self._compact_class_scores(q.class_scores),
                "specialRewards": specials or None,
                "prereqIds": list(q.prereq_ids) or None,
                "nextQuestIds": list(q.next_quest_ids) or None,
                "exclusiveTo": list(q.exclusive_to) or None,
                "isBreadcrumb": True if q.is_breadcrumb else None,
                "isDungeon": True if q.is_dungeon else None,
                "dungeonId": q.dungeon_id,
                "isGroup": True if q.is_group else None,
                "faction": q.faction_label,
                "side": q.faction_mask,
                "factionMask": q.faction_mask,
                "raceMask": q.race_mask,
                "classMask": q.class_mask or None,
                "requiredSkill": (
                    {"skillId": q.required_skill_id, "minSkill": q.required_min_skill}
                    if q.required_skill_id is not None
                    else None
                ),
                "isLeatherworking": True if q.is_leatherworking else None,
                "isSkinning": True if q.is_skinning else None,
                "lwRewards": list(q.lw_rewards) or None,
                "skinningScore": round(q.skinning_score, 1) if round(q.skinning_score, 1) > 0.0 else None,
                "beastTargetCount": q.beast_target_count or None,
                "beastTypes": list(q.beast_types) or None,
                "starter": {
                    "id": q.starter_npc_id,
                    "name": q.starter_name or "Quest Giver",
                    "kind": q.starter_kind,
                    "zoneId": sc.zone_id,
                    "x": round(sc.x, 1),
                    "y": round(sc.y, 1),
                },
                "turnin": {
                    "id": q.turnin_npc_id,
                    "name": q.turnin_name or "Turn-in NPC",
                    "kind": q.turnin_kind,
                    "zoneId": tc.zone_id,
                    "x": round(tc.x, 1),
                    "y": round(tc.y, 1),
                },
                "objectives": objs or None,
                "features": compact_features,
            }

        profiles_lua: Dict[str, Dict[str, Any]] = {}
        profile_order: List[str] = []
        for pkey, (cfg, steps, metrics) in profile_results.items():
            profile_order.append(pkey)
            profiles_lua[pkey] = {
                "id": cfg.profile_id,
                "label": cfg.label,
                "description": cfg.description,
                "weights": dict(cfg.weights),
                "summary": self._serialize_summary(metrics),
                "steps": [self._serialize_step(st) for st in steps],
            }

        starter_routes_lua: Dict[str | int, Dict[str, Any]] = {}
        if starter_routes:
            for rkey, (label, faction, zid, rep_race, rep_class, steps, metrics) in starter_routes.items():
                key_out: str | int = int(rkey) if str(rkey).isdigit() else str(rkey)
                starter_routes_lua[key_out] = {
                    "label": label,
                    "faction": faction,
                    "zoneId": zid,
                    "representativeRace": rep_race,
                    "representativeClass": rep_class,
                    "minLevel": metrics.start_level,
                    "maxLevel": metrics.end_level,
                    "summary": self._serialize_summary(metrics),
                    "steps": [self._serialize_step(st) for st in steps],
                }

        horde_routes_lua: Dict[str, Dict[str, Any]] = {}
        if horde_routes:
            for pkey, (cfg, steps, metrics) in horde_routes.items():
                if horde_profiles is not None and pkey not in horde_profiles:
                    continue
                horde_routes_lua[pkey] = {
                    "id": cfg.profile_id,
                    "label": cfg.label,
                    "description": cfg.description,
                    "faction": "Horde",
                    "representativeRace": metrics.race,
                    "representativeClass": metrics.char_class,
                    "weights": dict(cfg.weights),
                    "summary": self._serialize_summary(metrics),
                    "steps": [self._serialize_step(st) for st in steps],
                }

        eligible_sorted = sorted(exported_quests, key=lambda q: (q.min_level, q.quest_level, q.quest_id))
        eligible_ids = [q.quest_id for q in eligible_sorted]
        bag_quest_ids = [
            q.quest_id
            for q in sorted(
                (q for q in exported_quests if q.bag_slots > 0),
                key=lambda q: (-q.bag_slots, q.min_level, q.quest_id),
            )
        ]
        gold_quest_ids = [
            q.quest_id
            for q in sorted(
                (q for q in exported_quests if q.gold_copper >= 4000),
                key=lambda q: (-q.gold_copper, q.min_level, q.quest_id),
            )
        ]
        rogue_quest_ids = [
            q.quest_id
            for q in sorted(
                (q for q in exported_quests if q.rogue_score >= 30.0),
                key=lambda q: (-q.rogue_score, q.min_level, q.quest_id),
            )
        ]
        class_reward_ids = [
            q.quest_id
            for q in sorted(
                (q for q in exported_quests if q.max_class_score() >= 30.0),
                key=lambda q: (-q.max_class_score(), q.min_level, q.quest_id),
            )
        ]
        lw_quest_ids = [
            q.quest_id
            for q in sorted(
                (q for q in exported_quests if q.is_leatherworking or q.lw_rewards),
                key=lambda q: (q.min_level, q.quest_level, q.quest_id),
            )
        ]
        skinning_quest_ids = [
            q.quest_id
            for q in sorted(
                (q for q in exported_quests if q.is_skinning or q.skinning_score >= 30.0),
                key=lambda q: (-(1 if q.is_skinning else 0), -q.skinning_score, q.min_level, q.quest_id),
            )[:350]
        ]
        dungeon_quest_ids = [
            q.quest_id
            for q in eligible_sorted
            if q.is_dungeon
        ]

        def _special_rank_score(q: Any) -> float:
            bag_s = (60.0 + q.bag_slots * 4.0) if q.bag_slots > 0 else 0.0
            gear_s = q.max_class_score() * 1.1 if q.max_class_score() >= 30.0 else 0.0
            gold_s = min(60.0, q.gold_copper / 300.0) if q.gold_copper >= 4000 else 0.0
            lw_s = 75.0 if (q.is_leatherworking or q.lw_rewards) else 0.0
            skin_s = (70.0 if q.is_skinning else 0.0) + (q.skinning_score * 0.75 if q.skinning_score >= 50.0 else 0.0)
            return bag_s + gear_s + gold_s + lw_s + skin_s

        special_candidates = [
            q
            for q in exported_quests
            if (
                q.bag_slots > 0
                or q.gold_copper >= 4000
                or q.max_class_score() >= 30.0
                or q.is_leatherworking
                or bool(q.lw_rewards)
                or q.is_skinning
                or q.skinning_score >= 50.0
            )
        ]
        special_reward_ids = [
            q.quest_id
            for q in sorted(special_candidates, key=lambda q: (-_special_rank_score(q), q.min_level, q.quest_id))
        ]

        by_zone: Dict[int, List[int]] = {}
        by_faction: Dict[int, List[int]] = {1: [], 2: [], 3: []}
        for q in eligible_sorted:
            if q.faction_mask in (0, 1, 3):
                by_faction[1].append(q.quest_id)
            if q.faction_mask in (0, 2, 3):
                by_faction[2].append(q.quest_id)
            if q.faction_mask in (0, 3):
                by_faction[3].append(q.quest_id)
            sc_z = self.travel_model.resolve_zone_id(self.travel_model.quest_starter_coord(q).zone_id)
            by_zone.setdefault(sc_z, []).append(q.quest_id)
            if q.zone_id > 0:
                q_z = self.travel_model.resolve_zone_id(q.zone_id)
                if q_z != sc_z:
                    by_zone.setdefault(q_z, []).append(q.quest_id)

        class_quests: Dict[str, List[int]] = {}
        for ckey, cinfo in sorted(self.dataset.classes.items(), key=lambda item: item[1].class_id):
            c_list = [
                q.quest_id
                for q in eligible_sorted
                if q.class_mask != 0 and (q.class_mask & cinfo.bitmask) != 0
            ]
            if c_list:
                class_quests[ckey] = c_list

        indexes_lua = {
            "eligibleIds": eligible_ids,
            "specialRewardIds": special_reward_ids,
            "bagQuestIds": bag_quest_ids,
            "goldQuestIds": gold_quest_ids,
            "rogueQuestIds": rogue_quest_ids,
            "classRewardIds": class_reward_ids,
            "lwQuestIds": lw_quest_ids,
            "skinningQuestIds": skinning_quest_ids,
            "dungeonQuestIds": dungeon_quest_ids,
            "byZone": dict(sorted(by_zone.items())),
            "byFaction": by_faction,
            "classQuests": class_quests,
        }

        return {
            "meta": {
                "version": "1.0.0",
                "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "defaultMaxLevelOffset": int(default_max_level_offset),
                "maxLevelOffsetRange": [-3, 5],
                "targetCharacter": {
                    "faction": target_faction,
                    "race": target_race,
                    "class": target_class,
                    "skinning": True,
                    "leatherworking": True,
                    "startLevel": start_level,
                    "startZoneId": start_zone_id,
                },
                "profiles": profile_order,
                "races": races_lua,
                "classes": classes_lua,
                "zoneUiMapIds": dict(sorted(zone_uimaps.items())),
            },
            "xpCurve": dict(sorted(self.dataset.xp_curve.items())),
            "zones": zones_lua,
            "dungeons": dungeons_lua,
            "quests": quests_lua,
            "indexes": indexes_lua,
            "profiles": profiles_lua,
            "starterRoutes": starter_routes_lua,
            "hordeRoutes": horde_routes_lua,
        }

    @classmethod
    def _compact_step_for_lua(
        cls,
        st: Mapping[str, Any],
        zone_names: Mapping[int, str],
        quest_names: Mapping[int, str],
    ) -> Dict[str, Any]:
        out = dict(st)
        zid = out.get("zoneId")
        if zid in zone_names and out.get("zoneName") == zone_names[zid]:
            out.pop("zoneName", None)
        if out.get("expectedXpPct") == 0:
            out.pop("expectedXpPct", None)
        qid = out.get("questId")
        pfx = cls._STEP_ACTION_PREFIXES.get(str(out.get("action", "")))
        if pfx and qid in quest_names and out.get("title") == pfx + quest_names[qid]:
            out.pop("title", None)
        return out

    @classmethod
    def _append_route_block(
        cls,
        lines: List[str],
        assign_target: str,
        route_meta: Mapping[str, Any],
        steps: Sequence[Mapping[str, Any]],
        zone_names: Mapping[int, str],
        quest_names: Mapping[int, str],
        step_chunk_size: int = 1200,
    ) -> None:
        lines.append(";(function()")
        lines.append(f"  local route={to_lua(route_meta, compact=True)}")
        lines.append("  local steps={}")
        for start in range(0, len(steps), step_chunk_size):
            chunk = steps[start : start + step_chunk_size]
            lines.append("  ;(function(s)")
            for offset, st in enumerate(chunk, start=start + 1):
                c_st = cls._compact_step_for_lua(st, zone_names, quest_names)
                lines.append(f"  s[{offset}]={to_lua(c_st, inline=True, compact=True)}")
            lines.append("  end)(steps)")
        lines.append("  route.steps=_H(steps)")
        lines.append(f"  {assign_target}=route")
        lines.append("end)()")
        lines.append("")

    def write_lua(
        self,
        out_path: str | Path,
        profile_results: Mapping[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]],
        start_level: int = 1,
        start_zone_id: int = 1,
        starter_routes: Mapping[str, Tuple[str, str, int, str, str, List[RouteStep], SimulationMetrics]] | None = None,
        horde_routes: Mapping[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]] | None = None,
        target_race: str = "Gnome",
        target_class: str = "ROGUE",
        target_faction: str = "Alliance",
        horde_profiles: Sequence[str] | None = ("balanced",),
        default_max_level_offset: int = 2,
    ) -> Path:
        payload = self.build_payload(
            profile_results,
            start_level=start_level,
            start_zone_id=start_zone_id,
            starter_routes=starter_routes,
            horde_routes=horde_routes,
            target_race=target_race,
            target_class=target_class,
            target_faction=target_faction,
            horde_profiles=horde_profiles,
            default_max_level_offset=default_max_level_offset,
        )
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        zone_names: Dict[int, str] = {int(zid): str(zv["name"]) for zid, zv in payload["zones"].items()}
        quest_names: Dict[int, str] = {int(qid): str(qv["name"]) for qid, qv in payload["quests"].items()}
        side_names: Dict[int, str] = {0: "Both", 1: "Alliance", 2: "Horde", 3: "Both"}

        # Split quests and route steps into scoped IIFEs so LuaJIT stays well under the 65536 constant limit
        lines = [
            "-- AUTO-GENERATED by ForeverQuestPath/sim/lua_exporter.py. Do not edit manually.",
            "local _, ns = ...",
            "",
            "local data = {",
            f"  meta = {to_lua(payload['meta'], indent=2)},",
            f"  xpCurve = {to_lua(payload['xpCurve'], indent=2)},",
            "  zones = {},",
            "  dungeons = {},",
            "  quests = {},",
            "  indexes = {},",
            "  profiles = {},",
            "  starterRoutes = {},",
            "  hordeRoutes = {},",
            "}",
            "",
            "local zones = data.zones",
        ]
        for zid, zval in payload["zones"].items():
            lines.append(f"zones[{zid}]={to_lua(zval, compact=True)}")
        lines.append("")
        lines.append("local dungeons = data.dungeons")
        for did, dval in payload["dungeons"].items():
            lines.append(f"dungeons[{to_lua(did)}]={to_lua(dval, compact=True)}")
        lines.append("")
        lines.append("local quests = data.quests")
        quest_items = list(payload["quests"].items())
        quest_chunk_size = 250
        for start in range(0, len(quest_items), quest_chunk_size):
            lines.append(";(function(quests)")
            for qid, qval in quest_items[start : start + quest_chunk_size]:
                q_emit = dict(qval)
                if q_emit.get("zoneId") in zone_names and q_emit.get("zoneName") == zone_names[q_emit["zoneId"]]:
                    q_emit.pop("zoneName", None)
                if q_emit.get("faction") == side_names.get(int(q_emit.get("side", 3))):
                    q_emit.pop("faction", None)
                if q_emit.get("turnin") == q_emit.get("starter"):
                    q_emit.pop("turnin", None)
                lines.append(f"quests[{qid}]={to_lua(q_emit, compact=True)}")
            lines.append("end)(quests)")
        lines.extend([
            "local _ZN={}",
            "for zid,z in pairs(zones) do _ZN[zid]=z.name end",
            'local _FN={[0]="Both",[1]="Alliance",[2]="Horde",[3]="Both"}',
            "for _,q in pairs(quests) do",
            '  if not q.zoneName then q.zoneName=_ZN[q.zoneId] or "" end',
            '  if not q.faction then q.faction=_FN[q.side or 3] or "Both" end',
            "  if not q.turnin then q.turnin=q.starter end",
            "end",
            'local _PFX={ACCEPT="Accept: ",COMPLETE="Complete: ",TURN_IN="Turn In: "}',
            "local function _H(s)",
            "  for i=1,#s do",
            "    local t=s[i]",
            '    if not t.zoneName then t.zoneName=_ZN[t.zoneId] or "" end',
            '    if not t.title and t.questId and quests[t.questId] then t.title=(_PFX[t.action] or "")..quests[t.questId].name end',
            "    if not t.expectedXpPct then t.expectedXpPct=0 end",
            "  end",
            "  return s",
            "end",
            "",
        ])
        lines.append("local indexes = data.indexes")
        for ikey, ival in payload["indexes"].items():
            if ikey == "byZone":
                lines.append("indexes.byZone = {}")
                for bz_id, bz_qids in ival.items():
                    lines.append(f"indexes.byZone[{bz_id}]={to_lua(bz_qids, compact=True)}")
            elif ikey == "byFaction":
                lines.append("indexes.byFaction = {}")
                for bf_id, bf_qids in ival.items():
                    lines.append(f"indexes.byFaction[{bf_id}]={to_lua(bf_qids, compact=True)}")
            elif ikey == "classQuests":
                lines.append("indexes.classQuests = {}")
                for cq_cls, cq_ids in ival.items():
                    lines.append(f"indexes.classQuests[{to_lua(cq_cls)}]={to_lua(cq_ids, compact=True)}")
            else:
                lines.append(f"indexes.{ikey}={to_lua(ival, compact=True)}")
        lines.append("")

        for pkey, pval in payload["profiles"].items():
            p_meta = {k: v for k, v in pval.items() if k != "steps"}
            self._append_route_block(lines, f"data.profiles[{to_lua(pkey)}]", p_meta, pval["steps"], zone_names, quest_names)

        for skey, sval in payload["starterRoutes"].items():
            s_meta = {k: v for k, v in sval.items() if k != "steps"}
            k_expr = str(skey) if isinstance(skey, int) else to_lua(str(skey))
            self._append_route_block(lines, f"data.starterRoutes[{k_expr}]", s_meta, sval["steps"], zone_names, quest_names)
        if "16593_Alliance" in payload["starterRoutes"] and 16593 not in payload["starterRoutes"]:
            lines.append('data.starterRoutes[16593] = data.starterRoutes["16593_Alliance"]')
            lines.append("")

        for hkey, hval in payload["hordeRoutes"].items():
            h_meta = {k: v for k, v in hval.items() if k != "steps"}
            self._append_route_block(lines, f"data.hordeRoutes[{to_lua(hkey)}]", h_meta, hval["steps"], zone_names, quest_names)

        lines.append("ns.GeneratedData = data")
        lines.append("")
        out.write_text("\n".join(lines), encoding="utf-8")
        return out

    @staticmethod
    def _num_array_lua(vals: Sequence[int | float]) -> str:
        return "{" + ",".join(str(v) for v in vals) + "}"

    def write_nav_lua(self, out_path: str | Path) -> Path | None:
        nav = self.dataset.nav_graph
        if nav is None:
            return None

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        n_count = nav.node_count
        e_count = nav.edge_count

        lines: List[str] = [
            "-- AUTO-GENERATED by ForeverQuestPath/sim/lua_exporter.py. Do not edit manually.",
            "local _, ns = ...",
            "",
            "local nav = {",
            f"  version = {int(nav.version)},",
            f"  cellGrid = {int(nav.grid_width)},",
            f"  unmountedYdsPerSec = {round(float(nav.unmounted_yps), 3)},",
            f"  mountedYdsPerSec = {round(float(nav.mounted_yps), 3)},",
            f"  nodeCount = {n_count},",
            f"  edgeCount = {e_count},",
            f"  nodeZone = {self._num_array_lua(nav.node_zone[1:])},",
            f"  nodeX = {self._num_array_lua([round(v, 2) for v in nav.node_x[1:]])},",
            f"  nodeY = {self._num_array_lua([round(v, 2) for v in nav.node_y[1:]])},",
            f"  nodeKind = {self._num_array_lua(nav.node_kind[1:])},",
            f"  nodeComp = {self._num_array_lua(nav.node_comp[1:])},",
            "  nodeName = {},",
            "  nodeFpId = {},",
            f"  hearthNodes = {to_lua({int(z): int(nid) for z, nid in sorted(nav.hearth_nodes.items())}, compact=True)},",
            "  zoneBounds = {},",
            f"  fHead = {self._num_array_lua(nav.f_head[1:])},",
            f"  fTo = {self._num_array_lua(nav.f_to[1:])},",
            f"  eCost = {self._num_array_lua([round(v, 2) for v in nav.e_cost[1:]])},",
            f"  eMode = {self._num_array_lua(nav.e_mode[1:])},",
            f"  eGround = {self._num_array_lua(nav.e_ground[1:])},",
            f"  eFaction = {self._num_array_lua(nav.e_faction[1:])},",
            "  edgeNotes = {},",
            f"  rHead = {self._num_array_lua(nav.r_head[1:])},",
            f"  rFrom = {self._num_array_lua(nav.r_from[1:])},",
            f"  rFwdIdx = {self._num_array_lua(nav.r_fwd_idx[1:])},",
            "  zoneCells = {},",
            "  questAnchors = {},",
            "}",
            "",
        ]

        # Sparse node names & flight IDs
        named_nodes = [(nid, nav.node_name[nid]) for nid in range(1, n_count + 1) if nav.node_name[nid]]
        for start in range(0, len(named_nodes), 250):
            lines.append(";(function(t)")
            for nid, name in named_nodes[start : start + 250]:
                lines.append(f"t[{nid}]={to_lua(name)}")
            lines.append("end)(nav.nodeName)")
        if nav.node_fp_id:
            lines.append(";(function(t)")
            for nid, fpid in sorted(nav.node_fp_id.items()):
                lines.append(f"t[{nid}]={int(fpid)}")
            lines.append("end)(nav.nodeFpId)")

        # Zone worldBounds for exact chord yard scaling in Lua
        lines.append(";(function(zb)")
        for zid, (cont, lwy, rwy, twx, bwx) in sorted(nav.zone_bounds.items()):
            lines.append(f"zb[{zid}]={{{int(cont)},{round(lwy,2)},{round(rwy,2)},{round(twx,2)},{round(bwx,2)}}}")
        lines.append("end)(nav.zoneBounds)")

        # Sparse edge notes (gates, flights, boats, zeppelins, airships, tram)
        noted_edges = sorted(nav.edge_notes.items())
        for start in range(0, len(noted_edges), 250):
            lines.append(";(function(t)")
            for eidx, note in noted_edges[start : start + 250]:
                lines.append(f"t[{eidx}]={to_lua(note)}")
            lines.append("end)(nav.edgeNotes)")
        lines.append("")

        # Zone spatial cells: one IIFE per zone with flat numeric arrays {compId, n1, w1, ...}
        lines.append("local zoneCells = nav.zoneCells")
        for zid, cells in sorted(nav.zone_cells.items()):
            lines.append(";(function(zc)")
            lines.append("local z={}")
            for cidx, (_, comp_id, anchors) in sorted(cells.items()):
                arr: List[int | float] = [int(comp_id)]
                for anc_nid, anc_w in anchors:
                    arr.append(int(anc_nid))
                    arr.append(round(float(anc_w), 1))
                lines.append(f"z[{cidx}]={self._num_array_lua(arr)}")
            lines.append(f"zc[{zid}]=z")
            lines.append("end)(zoneCells)")
        lines.append("")

        # Quest anchors: 250 per IIFE; t omitted (nil) when turn-in == starter
        lines.append("local qa = nav.questAnchors")
        qa_items = sorted(nav.quest_anchors.items())
        for start in range(0, len(qa_items), 250):
            lines.append(";(function(qa)")
            for qid, (s_anc, o_anc, t_anc) in qa_items[start : start + 250]:
                s_flat: List[int | float] = []
                for nid, w in s_anc:
                    s_flat.extend((int(nid), round(float(w), 1)))
                o_flat: List[int | float] = []
                for nid, w in o_anc:
                    o_flat.extend((int(nid), round(float(w), 1)))
                if t_anc is not None:
                    t_flat: List[int | float] = []
                    for nid, w in t_anc:
                        t_flat.extend((int(nid), round(float(w), 1)))
                    lines.append(
                        f"qa[{qid}]={{s={self._num_array_lua(s_flat)},o={self._num_array_lua(o_flat)},t={self._num_array_lua(t_flat)}}}"
                    )
                else:
                    lines.append(
                        f"qa[{qid}]={{s={self._num_array_lua(s_flat)},o={self._num_array_lua(o_flat)}}}"
                    )
            lines.append("end)(qa)")
        lines.append("")
        lines.append("ns.NavData = nav")
        lines.append("")
        out.write_text("\n".join(lines), encoding="utf-8")
        return out
