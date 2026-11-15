"""Load and validate WoW Forever JSON datasets into normalized simulation models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    ClassInfo,
    Coord,
    DEFAULT_CLASSES,
    DEFAULT_RACES,
    Dataset,
    Dungeon,
    Quest,
    RaceInfo,
    SpecialReward,
    TravelEdge,
    UnlockChain,
    ZoneNode,
    canonical_class_key,
    canonical_race_key,
)
from .nav import NavGraph
from .xp import DEFAULT_XP_CURVE


def _parse_coord(raw: Optional[dict], default_zone_id: int = 0) -> Optional[Coord]:
    if not raw or not isinstance(raw, dict):
        return None
    x = raw.get("x")
    y = raw.get("y")
    z = raw.get("zoneId", default_zone_id)
    if x is None or y is None or z is None:
        return None
    return Coord(
        zone_id=int(z),
        x=max(0.0, min(100.0, float(x))),
        y=max(0.0, min(100.0, float(y))),
        label=str(raw.get("label", "") or ""),
    )


# 165=Leatherworking, 393=Skinning; 129=First Aid, 185=Cooking, 356=Fishing, 762=Riding (secondary/utility)
ALLOWED_PROFESSION_SKILLS = {165, 393, 129, 185, 356, 762}


def _parse_required_skill(raw: Any) -> Tuple[Optional[int], int]:
    if isinstance(raw, dict) and raw.get("skillId") is not None:
        return int(raw["skillId"]), max(1, int(raw.get("minSkill") or 1))
    return None, 0


def _normalize_lw_rewards(raw: Any) -> List[Dict[str, Any]]:
    rewards: List[Dict[str, Any]] = []
    for entry in raw or []:
        if isinstance(entry, dict):
            rewards.append(
                {
                    "itemId": int(entry.get("itemId", 0) or 0),
                    "name": str(entry.get("name", "") or ""),
                    "kind": str(entry.get("kind", "pattern") or "pattern"),
                    "quality": int(entry.get("quality", 1) or 1),
                }
            )
        elif isinstance(entry, int):
            rewards.append({"itemId": int(entry), "name": f"Item {entry}", "kind": "pattern", "quality": 1})
        elif isinstance(entry, str) and entry:
            rewards.append({"itemId": 0, "name": entry, "kind": "pattern", "quality": 1})
    return rewards


def _parse_class_scores(raw: Any, rogue_fallback: float = 0.0) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if not k:
                continue
            val = round(max(0.0, float(v or 0.0)), 2)
            if val > 0.0:
                scores[canonical_class_key(str(k))] = val
    if rogue_fallback > 0.0 and "ROGUE" not in scores:
        scores["ROGUE"] = round(max(0.0, float(rogue_fallback)), 2)
    return scores


def _merge_races_metadata(raw_sources: List[Any]) -> Dict[str, RaceInfo]:
    races: Dict[str, RaceInfo] = dict(DEFAULT_RACES)
    for src in raw_sources:
        if not src:
            continue
        entries = list(src.items()) if isinstance(src, dict) else [(None, item) for item in src]
        for k, rdata in entries:
            if not isinstance(rdata, dict):
                continue
            key = str(rdata.get("key") or rdata.get("raceFile") or k or rdata.get("name") or "").strip()
            if not key:
                continue
            existing = races.get(canonical_race_key(key, races))
            sc = rdata.get("startCoords") or {}
            race_id = int(rdata.get("id", rdata.get("raceId", existing.race_id if existing else 0)) or 0)
            raw_bit = rdata.get("bitmask") or rdata.get("raceBit")
            if raw_bit is not None and int(raw_bit) > 0:
                bitmask = int(raw_bit)
            elif existing and existing.bitmask > 0:
                bitmask = existing.bitmask
            else:
                bitmask = (1 << (race_id - 1)) if race_id > 0 else 0
            start_zone = int(rdata.get("startZoneId", existing.start_zone_id if existing else 1) or 1)
            sx = float(sc.get("x", rdata.get("startX", existing.start_x if existing else 50.0)) or 50.0)
            sy = float(sc.get("y", rdata.get("startY", existing.start_y if existing else 50.0)) or 50.0)
            raw_allowed = rdata.get("allowedClasses") or rdata.get("classes")
            allowed_classes = tuple(
                canonical_class_key(str(c))
                for c in raw_allowed
                if c
            ) if raw_allowed else (existing.allowed_classes if existing else ())
            races[key] = RaceInfo(
                race_id=race_id,
                key=key,
                label=str(rdata.get("label") or rdata.get("name") or (existing.label if existing else key)),
                faction=str(rdata.get("faction") or (existing.faction if existing else "Alliance")),
                bitmask=bitmask,
                start_zone_id=start_zone,
                start_x=sx,
                start_y=sy,
                allowed_classes=allowed_classes,
            )
    return races


def _merge_classes_metadata(raw_sources: List[Any]) -> Dict[str, ClassInfo]:
    classes: Dict[str, ClassInfo] = dict(DEFAULT_CLASSES)
    for src in raw_sources:
        if not src:
            continue
        entries = list(src.items()) if isinstance(src, dict) else [(None, item) for item in src]
        for k, cdata in entries:
            if not isinstance(cdata, dict):
                continue
            key = str(cdata.get("key") or cdata.get("classFile") or k or cdata.get("name") or "").strip().upper()
            if not key:
                continue
            existing = classes.get(canonical_class_key(key, classes))
            cid = int(cdata.get("id", cdata.get("classId", existing.class_id if existing else 0)) or 0)
            raw_bit = cdata.get("bitmask") or cdata.get("classBit")
            if raw_bit is not None and int(raw_bit) > 0:
                bitmask = int(raw_bit)
            elif existing and existing.bitmask > 0:
                bitmask = existing.bitmask
            else:
                bitmask = (1 << (cid - 1)) if cid > 0 else 0
            classes[key] = ClassInfo(
                class_id=cid,
                key=key,
                label=str(cdata.get("label") or cdata.get("name") or (existing.label if existing else key.title())),
                bitmask=bitmask,
            )
    return classes


def _is_alliance_gnome_rogue_eligible(raw: dict) -> bool:
    req_skill_id, _ = _parse_required_skill(raw.get("requiredSkill"))
    if req_skill_id is not None and req_skill_id not in ALLOWED_PROFESSION_SKILLS:
        return False
    if "eligibleAllianceGnomeRogue" in raw:
        return bool(raw["eligibleAllianceGnomeRogue"])
    faction_mask = int(raw.get("factionMask", 3) or 3)
    if faction_mask not in (0, 1, 3):
        return False
    race_mask = int(raw.get("raceMask", 0) or 0)
    # Gnome raceId=7 -> bit 6 (64)
    if race_mask != 0 and (race_mask & (1 << 6)) == 0:
        return False
    class_mask = int(raw.get("classMask", 0) or 0)
    # Rogue classId=4 -> bit 3 (8)
    if class_mask != 0 and (class_mask & (1 << 3)) == 0:
        return False
    return True


def _break_cycles(quests: Dict[int, Quest]) -> List[Tuple[int, int]]:
    """Detect and break any rare prerequisite cycles via DFS back-edge removal."""
    state: Dict[int, int] = {}  # 0/absent=unvisited, 1=visiting, 2=done
    removed: List[Tuple[int, int]] = []

    def dfs(qid: int) -> None:
        state[qid] = 1
        q = quests[qid]
        kept_prereqs: List[int] = []
        for pid in q.prereq_ids:
            if pid not in quests or pid == qid:
                continue
            if state.get(pid, 0) == 1:
                removed.append((pid, qid))
                continue
            kept_prereqs.append(pid)
            if state.get(pid, 0) == 0:
                dfs(pid)
        q.prereq_ids = kept_prereqs
        state[qid] = 2

    for qid in sorted(quests.keys()):
        if state.get(qid, 0) == 0:
            dfs(qid)
    return removed


# Rules: Reads data/quests.json, data/dungeons.json, data/travel_graph.json, and optional data/nav_graph.json
# from disk, normalizes all Lv 1-60 multi-race/multi-class/faction schemas, repairs dangling/cyclic
# prerequisite references, and builds the immutable Dataset.
class DatasetLoader:
    data_dir: Path

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)

    def load(self) -> Dataset:
        quests_raw = self._read_json(self.data_dir / "quests.json")
        dungeons_raw = self._read_json(self.data_dir / "dungeons.json")
        travel_raw = self._read_json(self.data_dir / "travel_graph.json")
        meta_path = self.data_dir / "meta.json"
        meta_raw = self._read_json(meta_path) if meta_path.exists() else {}

        q_meta = quests_raw.get("meta") if isinstance(quests_raw.get("meta"), dict) else {}
        races = _merge_races_metadata([
            travel_raw.get("races"),
            q_meta.get("races"),
            quests_raw.get("races"),
            meta_raw.get("races"),
        ])
        classes = _merge_classes_metadata([
            travel_raw.get("classes"),
            q_meta.get("classes"),
            quests_raw.get("classes"),
            meta_raw.get("classes"),
        ])

        zones: Dict[int, ZoneNode] = {}
        zone_uimap_ids: Dict[int, int] = {}
        for zkey, zdata in travel_raw.get("nodes", {}).items():
            zid = int(zdata.get("zoneId", zkey))
            hub_coords = _parse_coord(zdata.get("hubCoords"), default_zone_id=zid) or Coord(zid, 50.0, 50.0)
            ui_map_id = int(zdata["uiMapId"]) if zdata.get("uiMapId") is not None else None
            if ui_map_id is not None:
                zone_uimap_ids[zid] = ui_map_id
            zones[zid] = ZoneNode(
                zone_id=zid,
                name=str(zdata.get("name", f"Zone {zid}")),
                continent=str(zdata.get("continent", "Eastern Kingdoms")),
                region=int(zdata.get("region") if zdata.get("region") is not None else 0),
                min_level=int(zdata.get("minLevel") or 1),
                max_level=int(zdata.get("maxLevel") or 60),
                hub_name=str(zdata.get("hubName", zdata.get("name", f"Zone {zid}"))),
                hub_coords=hub_coords,
                flight_node_id=zdata.get("flightNodeId"),
                has_boat=bool(zdata.get("hasBoat", False)),
                has_tram=bool(zdata.get("hasTram", False)),
                ui_map_id=ui_map_id,
                faction=str(zdata.get("faction", "Both") or "Both"),
            )

        edges: List[TravelEdge] = []
        for edata in travel_raw.get("edges", []):
            edges.append(
                TravelEdge(
                    from_zone_id=int(edata["fromZoneId"]),
                    to_zone_id=int(edata["toZoneId"]),
                    mode=str(edata.get("mode", "run")),
                    seconds=max(1, int(edata.get("seconds", 60))),
                    cost_copper=int(edata.get("costCopper", 0)),
                    faction=str(edata.get("faction", "Both")),
                    note=str(edata.get("note", "")),
                )
            )

        xp_curve: Dict[int, int] = dict(DEFAULT_XP_CURVE)
        for lvl_str, val in (travel_raw.get("xpCurve") or {}).items():
            lvl = int(lvl_str)
            if 1 <= lvl < 60 and int(val) > 0:
                xp_curve[lvl] = int(val)

        dungeons: Dict[str, Dungeon] = {}
        dungeon_by_zone: Dict[int, Dungeon] = {}
        quest_to_dungeon: Dict[int, str] = {}
        for dkey, ddata in dungeons_raw.get("dungeons", {}).items():
            did = str(ddata.get("dungeonId", dkey))
            dzid = int(ddata["zoneId"])
            ent_zid = int(ddata.get("entranceZoneId", dzid))
            ent_coords = _parse_coord(ddata.get("entranceCoords"), default_zone_id=ent_zid)
            chains: List[UnlockChain] = []
            for cdata in ddata.get("unlockChains", []):
                c_qids = [int(x) for x in cdata.get("questIds", [])]
                chains.append(
                    UnlockChain(
                        chain_name=str(cdata.get("chainName", f"{ddata.get('name', did)} Chain")),
                        quest_ids=c_qids,
                        min_level=int(cdata.get("minLevel", ddata.get("minLevel", 1))),
                        travel_tip=str(cdata.get("travelTip", "")),
                    )
                )
            dqids = [int(x) for x in ddata.get("questIds", [])]
            for qid in dqids:
                quest_to_dungeon[qid] = did
            dungeon = Dungeon(
                dungeon_id=did,
                zone_id=dzid,
                name=str(ddata.get("name", did)),
                zone_name=str(ddata.get("zoneName", ddata.get("name", did))),
                entrance_zone_id=ent_zid,
                entrance_zone_name=str(ddata.get("entranceZoneName", "")),
                entrance_coords=ent_coords,
                min_level=int(ddata.get("minLevel", 1)),
                max_level=int(ddata.get("maxLevel", 60)),
                recommended_level=int(ddata.get("recommendedLevel", (int(ddata.get("minLevel", 1)) + int(ddata.get("maxLevel", 60))) // 2)),
                faction=str(ddata.get("faction", "Both")),
                quest_ids=dqids,
                unlock_chains=chains,
            )
            dungeons[did] = dungeon
            dungeon_by_zone[dzid] = dungeon

        quests: Dict[int, Quest] = {}
        for qkey, qdata in quests_raw.get("quests", {}).items():
            qid = int(qdata.get("questId", qkey))
            zid = int(qdata.get("zoneId", 0))
            specials: List[SpecialReward] = []
            for sr in qdata.get("specialRewards", []) or []:
                sr_rogue = float(sr.get("rogueScore", 0.0) or 0.0)
                sr_class_scores = _parse_class_scores(sr.get("classScores"), rogue_fallback=sr_rogue)
                specials.append(
                    SpecialReward(
                        item_id=int(sr.get("itemId", 0)),
                        name=str(sr.get("name", "")),
                        kind=str(sr.get("kind", "item")),
                        slots=int(sr.get("slots", 0) or 0),
                        quality=int(sr.get("quality", 1) or 1),
                        item_level=int(sr.get("itemLevel", 0) or 0),
                        is_choice=bool(sr.get("isChoice", False)),
                        rogue_score=sr_rogue,
                        class_scores=sr_class_scores,
                    )
                )

            obj_coords: List[Coord] = []
            for oc in qdata.get("objectiveCoords", []) or []:
                parsed = _parse_coord(oc, default_zone_id=zid)
                if parsed:
                    obj_coords.append(parsed)

            dungeon_id = qdata.get("dungeonId") or quest_to_dungeon.get(qid)
            is_dungeon = bool(qdata.get("isDungeon", False) or dungeon_id is not None or zid in dungeon_by_zone)
            if is_dungeon and not dungeon_id and zid in dungeon_by_zone:
                dungeon_id = dungeon_by_zone[zid].dungeon_id

            req_skill_id, req_min_skill = _parse_required_skill(qdata.get("requiredSkill"))
            lw_rewards = _normalize_lw_rewards(qdata.get("lwRewards"))
            is_lw = bool(qdata.get("isLeatherworking", False) or req_skill_id == 165)
            is_skin = bool(qdata.get("isSkinning", False) or req_skill_id == 393)
            beast_types = [str(x) for x in (qdata.get("beastTypes") or []) if x]

            raw_rogue = max(0.0, float(qdata.get("rogueScore", 0.0) or 0.0))
            class_scores = _parse_class_scores(qdata.get("classScores"), rogue_fallback=raw_rogue)
            if raw_rogue == 0.0 and "ROGUE" in class_scores:
                raw_rogue = class_scores["ROGUE"]

            quests[qid] = Quest(
                quest_id=qid,
                name=str(qdata.get("name", f"Quest {qid}")),
                zone_id=zid,
                zone_name=str(qdata.get("zoneName", zones[zid].name if zid in zones else f"Zone {zid}")),
                min_level=max(1, int(qdata.get("minLevel", 1) or 1)),
                quest_level=max(1, int(qdata.get("questLevel", qdata.get("minLevel", 1)) or 1)),
                xp=max(0, int(qdata.get("xp", 0) or 0)),
                gold_copper=max(0, int(qdata.get("goldCopper", 0) or 0)),
                bag_slots=max(0, int(qdata.get("bagSlots", 0) or 0)),
                rogue_score=raw_rogue,
                special_rewards=specials,
                prereq_ids=[int(x) for x in qdata.get("prereqIds", []) or []],
                next_quest_ids=[int(x) for x in qdata.get("nextQuestIds", []) or []],
                exclusive_to=[int(x) for x in qdata.get("exclusiveTo", []) or []],
                is_breadcrumb=bool(qdata.get("isBreadcrumb", False)),
                chain_id=qdata.get("chainId"),
                chain_step=qdata.get("chainStep"),
                starter_npc_id=qdata.get("starterNpcId"),
                starter_kind=str(qdata.get("starterKind", "npc") or "npc"),
                starter_name=qdata.get("starterName"),
                starter_coords=_parse_coord(qdata.get("starterCoords"), default_zone_id=zid),
                turnin_npc_id=qdata.get("turninNpcId"),
                turnin_kind=str(qdata.get("turninKind", "npc") or "npc"),
                turnin_name=qdata.get("turninName"),
                turnin_coords=_parse_coord(qdata.get("turninCoords"), default_zone_id=zid),
                objective_coords=obj_coords,
                is_dungeon=is_dungeon,
                dungeon_id=str(dungeon_id) if dungeon_id else None,
                is_group=bool(qdata.get("isGroup", False)),
                faction_mask=int(qdata.get("factionMask", qdata.get("side", 3)) or 3),
                race_mask=int(qdata.get("raceMask", 0) or 0),
                class_mask=int(qdata.get("classMask", 0) or 0),
                eligible_alliance_gnome_rogue=_is_alliance_gnome_rogue_eligible(qdata),
                quest_kind=qdata.get("questKind"),
                objective_count=qdata.get("objectiveCount"),
                source_provenance=[str(x) for x in qdata.get("sourceProvenance", []) or []],
                class_scores=class_scores,
                is_leveling_quest=bool(qdata.get("isLevelingQuest", True)),
                required_skill_id=req_skill_id,
                required_min_skill=req_min_skill,
                is_leatherworking=is_lw,
                is_skinning=is_skin,
                lw_rewards=lw_rewards,
                skinning_score=max(0.0, min(100.0, float(qdata.get("skinningScore", 0.0) or 0.0))),
                beast_target_count=max(0, int(qdata.get("beastTargetCount", 0) or 0)),
                beast_types=beast_types,
            )

        # Normalize prerequisite edges & record any missing prereq references
        missing_prereqs: Dict[int, List[int]] = {}
        for qid, q in quests.items():
            valid_prereqs: List[int] = []
            for pid in q.prereq_ids:
                if pid in quests:
                    if pid not in valid_prereqs:
                        valid_prereqs.append(pid)
                else:
                    missing_prereqs.setdefault(qid, []).append(pid)
            q.prereq_ids = valid_prereqs

        broken_cycles = _break_cycles(quests)

        # Rebuild next_quest_ids symmetrically for loaded quests
        next_sets: Dict[int, Set[int]] = {qid: set(nid for nid in q.next_quest_ids if nid in quests and nid != qid) for qid, q in quests.items()}
        for qid, q in quests.items():
            for pid in q.prereq_ids:
                if pid in next_sets:
                    next_sets[pid].add(qid)
        for qid, q in quests.items():
            q.next_quest_ids = sorted(next_sets[qid])

        nav_path = self.data_dir / "nav_graph.json"
        nav_graph = (
            NavGraph.from_json(nav_path, zone_names={zid: z.name for zid, z in zones.items()})
            if nav_path.exists()
            else None
        )

        return Dataset(
            quests=quests,
            dungeons=dungeons,
            dungeon_by_zone=dungeon_by_zone,
            zones=zones,
            edges=edges,
            xp_curve=xp_curve,
            races=races,
            classes=classes,
            zone_uimap_ids=zone_uimap_ids,
            missing_prereqs=missing_prereqs,
            broken_cycles_removed=broken_cycles,
            nav_graph=nav_graph,
        )

    @staticmethod
    def _read_json(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
