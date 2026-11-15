"""
Validates data/quests.json, data/dungeons.json, and data/travel_graph.json against
the locked schema, DAG acyclicity, coordinate bounds, graph reachability, and
WoW Forever domain spot-checks.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

REQUIRED_QUEST_FIELDS = {
    "questId", "name", "zoneId", "zoneName", "minLevel", "questLevel", "xp",
    "goldCopper", "bagSlots", "specialRewards", "rogueScore", "requiredSkill",
    "isLeatherworking", "isSkinning", "lwRewards", "skinningScore",
    "beastTargetCount", "beastTypes", "prereqIds",
    "nextQuestIds", "exclusiveTo", "isBreadcrumb", "questKind", "objectiveCount",
    "chainId", "chainStep", "starterNpcId", "starterKind", "starterName",
    "starterCoords", "turninNpcId", "turninKind", "turninName", "turninCoords",
    "objectiveCoords", "isDungeon", "dungeonId", "isGroup", "factionMask",
    "raceMask", "classMask", "eligibleAllianceGnomeRogue", "sourceProvenance",
}

REQUIRED_DUNGEON_FIELDS = {
    "dungeonId", "zoneId", "name", "zoneName", "entranceZoneId",
    "entranceZoneName", "entranceCoords", "minLevel", "maxLevel",
    "recommendedLevel", "faction", "questIds", "unlockChains",
}

VALID_QUEST_KINDS = {"talk", "kill", "collect", "escort", "dungeon", "breadcrumb"}
VALID_MODES = {"run", "flight", "boat", "tram", "zeppelin", "airship", "hearth"}
ALLOWED_SKILL_IDS = {129, 165, 185, 356, 393, 633}
DISALLOWED_CRAFT_SKILL_IDS = {164, 171, 182, 186, 197, 202, 333}


# Rules for DatasetValidator: asserts strict schema conformance (including 9-class classScores,
# isLevelingQuest, and 10-race/9-class travel metadata), symmetric acyclic quest DAGs, strict
# eligible/leveling prereq closure, zero cross-faction/cross-race/cross-class/joint-unsatisfiable
# prereqs, valid [0..100] coordinates on known travel_graph zones (including Lv 1-60 starters,
# Deeprun Tram, and Wetlands), Alliance/Horde/Skyborne travel reachability across flights, boats,
# zeppelins, airships, and tram, minLevel non-fallback integrity, Leatherworking (165) & Skinning
# (393) inclusion, skinningScore/beastTypes consistency, and seasonal/stub exclusions.
class DatasetValidator:
    """Runs structural, graph, coordinate, and domain checks on generated JSON files."""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.data_dir = data_dir
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def _err(self, msg: str) -> None:
        self.errors.append(msg)

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def _check_coord(self, c: dict[str, Any] | None, ctx: str, world_nodes: set[int]) -> None:
        if c is None:
            return
        if not isinstance(c, dict):
            self._err(f"{ctx}: coord is not dict")
            return
        zid, x, y = c.get("zoneId"), c.get("x"), c.get("y")
        if not isinstance(zid, int) or zid not in world_nodes:
            self._err(f"{ctx}: coord zoneId={zid} missing from travel_graph.nodes")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            self._err(f"{ctx}: non-numeric x/y ({x}, {y})")
        elif not (0.0 <= float(x) <= 100.0 and 0.0 <= float(y) <= 100.0):
            self._err(f"{ctx}: out-of-range coords ({x}, {y})")

    def validate(self) -> int:
        """Executes all validation checks and prints a detailed diagnostic report."""
        q_data = json.loads((self.data_dir / "quests.json").read_text(encoding="utf-8"))
        d_data = json.loads((self.data_dir / "dungeons.json").read_text(encoding="utf-8"))
        t_data = json.loads((self.data_dir / "travel_graph.json").read_text(encoding="utf-8"))

        quests: dict[int, dict[str, Any]] = {int(k): v for k, v in q_data["quests"].items()}
        dungeons: dict[str, dict[str, Any]] = d_data["dungeons"]
        nodes: dict[int, dict[str, Any]] = {int(k): v for k, v in t_data["nodes"].items()}
        edges: list[dict[str, Any]] = t_data["edges"]
        world_node_ids = set(nodes.keys())

        # 1. Travel graph checks
        xp_curve = t_data.get("xpCurve") or {}
        if len(xp_curve) != 59 or sum(int(v) for v in xp_curve.values()) != 4084700:
            self._err(f"xpCurve mismatch: len={len(xp_curve)}, sum={sum(int(v) for v in xp_curve.values())}")
        for zid, n in nodes.items():
            self._check_coord(n.get("hubCoords"), f"node {zid}", world_node_ids)
        adj: dict[int, set[int]] = {zid: set() for zid in world_node_ids}
        for e in edges:
            fz, tz = e.get("fromZoneId"), e.get("toZoneId")
            if fz not in world_node_ids or tz not in world_node_ids:
                self._err(f"travel edge points to unknown zone: {fz}->{tz}")
            if e.get("mode") not in VALID_MODES:
                self._err(f"invalid edge mode: {e.get('mode')}")
            if (e.get("seconds") or 0) <= 0:
                self._err(f"non-positive edge seconds: {e}")
            if fz in adj and isinstance(tz, int):
                adj[fz].add(tz)

        # Validate node metadata (faction, flightNodeIds, hasZeppelin, hasAirship) + races & classes catalogs
        all_class_keys = ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID")
        for zid_str, node in nodes.items():
            if node.get("faction") not in ("Alliance", "Horde", "Contested"):
                self._err(f"travel node {zid_str} invalid faction={node.get('faction')}")
            if not isinstance(node.get("flightNodeIds"), list):
                self._err(f"travel node {zid_str} missing list flightNodeIds")
            if not isinstance(node.get("hasZeppelin"), bool) or not isinstance(node.get("hasAirship"), bool):
                self._err(f"travel node {zid_str} missing bool hasZeppelin/hasAirship")

        races_meta = t_data.get("races") or []
        classes_meta = t_data.get("classes") or []
        if len(races_meta) != 10:
            self._err(f"travel_graph.races expected 10 races, got {len(races_meta)}")
        if len(classes_meta) != 9 or [c.get("key") for c in classes_meta] != list(all_class_keys):
            self._err(f"travel_graph.classes mismatch: {[c.get('key') for c in classes_meta]}")
        for r in races_meta:
            if r.get("startZoneId") not in world_node_ids or r.get("faction") not in ("Alliance", "Horde"):
                self._err(f"invalid race metadata: {r}")
            self._check_coord(r.get("startCoords"), f"race {r.get('name')}", world_node_ids)

        # Verify Zeppelins (Orgrimmar<->Tirisfal<->STV) & Airships (Alterac/Mulgore<->Zephras)
        zep_pairs = {(e["fromZoneId"], e["toZoneId"]) for e in edges if e.get("mode") == "zeppelin"}
        for a, b in ((1637, 85), (85, 1637), (1637, 33), (33, 1637), (85, 33), (33, 85)):
            if (a, b) not in zep_pairs:
                self._err(f"missing zeppelin edge {a}->{b}")
        air_pairs = {(e["fromZoneId"], e["toZoneId"]) for e in edges if e.get("mode") == "airship"}
        for a, b in ((36, 16593), (16593, 36), (215, 16593), (16593, 215)):
            if (a, b) not in air_pairs:
                self._err(f"missing airship edge {a}->{b}")

        # BFS reachability: full graph from Wetlands (11), plus faction-filtered BFS for Alliance/Horde/Skyborne
        def bfs_reach(start: int, allowed_factions: set[str] | None) -> set[int]:
            seen = {start}
            q_nodes: deque[int] = deque([start])
            while q_nodes:
                u = q_nodes.popleft()
                for e in edges:
                    if e.get("fromZoneId") == u and (allowed_factions is None or e.get("faction") in allowed_factions):
                        v = e.get("toZoneId")
                        if isinstance(v, int) and v not in seen:
                            seen.add(v)
                            q_nodes.append(v)
            return seen

        unreachable = sorted(world_node_ids - bfs_reach(11, None))
        if unreachable:
            self._err(f"Zones unreachable from Wetlands (11): {unreachable}")
        if sorted(world_node_ids - bfs_reach(1519, {"Alliance", "Both"})):
            self._err("Alliance cannot reach all zones from Stormwind (1519)")
        if sorted(world_node_ids - bfs_reach(16593, {"Alliance", "Both"})):
            self._err("High Order Skyborne (Alliance) cannot reach all zones from Zephras (16593)")
        horde_unreach = sorted(world_node_ids - bfs_reach(1637, {"Horde", "Both"}) - {141, 1657})
        if horde_unreach:
            self._err(f"Horde unreachable non-Teldrassil zones from Orgrimmar (1637): {horde_unreach}")
        sky_h_unreach = sorted(world_node_ids - bfs_reach(16593, {"Horde", "Both"}) - {141, 1657})
        if sky_h_unreach:
            self._err(f"Windshaper Skyborne (Horde) unreachable non-Teldrassil zones from Zephras (16593): {sky_h_unreach}")

        # 2. Dungeon checks
        for slug, d in dungeons.items():
            missing = REQUIRED_DUNGEON_FIELDS - set(d.keys())
            if missing:
                self._err(f"dungeon {slug} missing keys: {sorted(missing)}")
            self._check_coord(d.get("entranceCoords"), f"dungeon {slug}", world_node_ids)
            for qid in d.get("questIds") or []:
                if qid not in quests:
                    self._err(f"dungeon {slug} references missing quest {qid}")
            for ch in d.get("unlockChains") or []:
                for qid in ch.get("questIds") or []:
                    if qid not in quests:
                        self._err(f"dungeon {slug} unlockChain references missing quest {qid}")

        for req_slug in (
            "hall-of-thanes", "ragefire-chasm", "ruins-of-lordaeron", "wailing-caverns",
            "the-deadmines", "shadowfang-keep", "excavation-site-wetlands", "the-stockade",
            "blackfathom-deeps", "city-of-dalaran", "gnomeregan", "the-drowned-city",
            "krol-dok-stronghold", "uldaman", "zul-farrak", "alcaz-prison",
            "blackmaw-hold", "the-shapers-terrace",
        ):
            if req_slug not in dungeons:
                self._err(f"Expected dungeon {req_slug} missing from dungeons.json")
        for unlock_slug in ("hall-of-thanes", "ragefire-chasm", "ruins-of-lordaeron", "wailing-caverns"):
            if not (dungeons.get(unlock_slug, {}).get("unlockChains") or []):
                self._err(f"Expected non-empty unlockChains on {unlock_slug}")

        # 3. Quest schema, coordinates, symmetry, and DAG checks
        all_a_race = 1 | 4 | 8 | 64 | (1 << 32)
        all_h_race = 2 | 16 | 32 | 128 | (1 << 33)
        all_race = all_a_race | all_h_race
        all_cls = 1 | 2 | 4 | 8 | 16 | 64 | 128 | 256 | 1024

        def eff_race(q_obj: dict[str, Any]) -> int:
            m = all_race if q_obj["raceMask"] == 0 else (q_obj["raceMask"] & all_race)
            allow = (all_a_race if (q_obj["factionMask"] & 1) else 0) | (all_h_race if (q_obj["factionMask"] & 2) else 0)
            return m & allow if allow else m

        valid_quest_zones = world_node_ids | {int(d["zoneId"]) for d in dungeons.values()}
        indegree: dict[int, int] = {qid: 0 for qid in quests}
        for qid, q in quests.items():
            missing = REQUIRED_QUEST_FIELDS - set(q.keys())
            if missing:
                self._err(f"quest {qid} missing keys: {sorted(missing)}")
            if not isinstance(q.get("isLevelingQuest"), bool):
                self._err(f"quest {qid} missing bool isLevelingQuest")
            if q["eligibleAllianceGnomeRogue"] and not q.get("isLevelingQuest"):
                self._err(f"quest {qid} eligibleAllianceGnomeRogue=True must have isLevelingQuest=True")
            c_scores = q.get("classScores")
            if not isinstance(c_scores, dict) or set(c_scores.keys()) != set(all_class_keys):
                self._err(f"quest {qid} missing 9-key classScores")
            else:
                for ck, cv in c_scores.items():
                    if not isinstance(cv, (int, float)) or not (0.0 <= float(cv) <= 100.0):
                        self._err(f"quest {qid} invalid classScores[{ck}]={cv}")
                if round(float(c_scores["ROGUE"]), 1) != round(float(q["rogueScore"]), 1):
                    self._err(f"quest {qid} rogueScore={q['rogueScore']} != classScores['ROGUE']={c_scores['ROGUE']}")
            for sr in q.get("specialRewards") or []:
                sr_cs = sr.get("classScores")
                if not isinstance(sr_cs, dict) or set(sr_cs.keys()) != set(all_class_keys):
                    self._err(f"quest {qid} specialReward {sr.get('itemId')} missing 9-key classScores")
            if q["questKind"] not in VALID_QUEST_KINDS:
                self._err(f"quest {qid} invalid questKind={q['questKind']}")
            if q["isDungeon"] and q["dungeonId"] not in dungeons:
                self._err(f"quest {qid} unknown dungeonId={q['dungeonId']}")
            if (q["isLevelingQuest"] or q["eligibleAllianceGnomeRogue"]) and q["zoneId"] not in valid_quest_zones:
                self._err(f"leveling quest {qid} ({q['name']}) has unmapped zoneId={q['zoneId']} ({q['zoneName']})")

            rs = q.get("requiredSkill")
            if rs is not None:
                if not isinstance(rs, dict) or not isinstance(rs.get("skillId"), int) or not isinstance(rs.get("minSkill"), int):
                    self._err(f"quest {qid} malformed requiredSkill={rs}")
                elif (q.get("isLevelingQuest") or q["eligibleAllianceGnomeRogue"]) and rs["skillId"] in DISALLOWED_CRAFT_SKILL_IDS:
                    self._err(f"leveling quest {qid} ({q['name']}) uses disallowed craft skillId={rs['skillId']}")
            if not isinstance(q.get("isLeatherworking"), bool) or not isinstance(q.get("isSkinning"), bool):
                self._err(f"quest {qid} non-bool isLeatherworking/isSkinning")
            if not isinstance(q.get("lwRewards"), list):
                self._err(f"quest {qid} lwRewards must be list")
            s_score = q.get("skinningScore")
            b_cnt = q.get("beastTargetCount")
            b_types = q.get("beastTypes")
            if not isinstance(s_score, (int, float)) or not (0.0 <= float(s_score) <= 100.0):
                self._err(f"quest {qid} invalid skinningScore={s_score}")
            if not isinstance(b_cnt, int) or b_cnt < 0:
                self._err(f"quest {qid} invalid beastTargetCount={b_cnt}")
            if not isinstance(b_types, list) or not all(t in ("Beast", "Dragonkin") for t in b_types):
                self._err(f"quest {qid} invalid beastTypes={b_types}")
            elif (float(s_score) > 0.0) != (b_cnt > 0) or (b_cnt > 0) != bool(b_types):
                self._err(f"quest {qid} inconsistent skinningScore={s_score}, beastTargetCount={b_cnt}, beastTypes={b_types}")

            self._check_coord(q.get("starterCoords"), f"quest {qid} starter", world_node_ids)
            self._check_coord(q.get("turninCoords"), f"quest {qid} turnin", world_node_ids)
            for oc in q.get("objectiveCoords") or []:
                self._check_coord(oc, f"quest {qid} obj", world_node_ids)

            if (q.get("isLevelingQuest") or q["eligibleAllianceGnomeRogue"]) and (q["zoneId"] == 11 or 1 <= q["questLevel"] <= 60):
                if q["starterCoords"] is None or q["turninCoords"] is None:
                    self._err(f"leveling core quest {qid} ({q['name']}) missing starter/turnin coords")

            joint_fac = q["factionMask"]
            joint_race = eff_race(q)
            joint_cls = q["classMask"] or all_cls
            for p in q["prereqIds"]:
                if p not in quests:
                    self._err(f"quest {qid} dangling prereq {p}")
                else:
                    pq = quests[p]
                    if qid not in pq["nextQuestIds"]:
                        self._err(f"asymmetric edge {p} -> {qid}")
                    if q["eligibleAllianceGnomeRogue"] and not pq["eligibleAllianceGnomeRogue"]:
                        self._err(
                            f"eligible quest {qid} ({q['name']}) requires ineligible prereq {p} ({pq['name']})"
                        )
                    if q.get("isLevelingQuest"):
                        if not pq.get("isLevelingQuest"):
                            self._err(f"leveling quest {qid} ({q['name']}) requires non-leveling prereq {p} ({pq['name']})")
                        if q["factionMask"] == 3 and pq["factionMask"] in (1, 2):
                            self._err(f"neutral quest {qid} ({q['name']}) requires single-faction prereq {p} ({pq['name']}, fac={pq['factionMask']})")
                        joint_fac &= pq["factionMask"]
                        joint_race &= eff_race(pq)
                        joint_cls &= (pq["classMask"] or all_cls)
                indegree[qid] += 1
            if q.get("isLevelingQuest") and q["prereqIds"] and (joint_fac == 0 or joint_race == 0 or joint_cls == 0):
                self._err(f"leveling quest {qid} ({q['name']}) has unsatisfiable joint prereq masks (fac={joint_fac}, race={joint_race}, cls={joint_cls})")

            for n in q["nextQuestIds"]:
                if n not in quests:
                    self._err(f"quest {qid} dangling nextQuest {n}")
                elif qid not in quests[n]["prereqIds"]:
                    self._err(f"asymmetric edge {qid} -> {n}")

            for e in q["exclusiveTo"]:
                if e not in quests:
                    self._err(f"quest {qid} dangling exclusiveTo {e}")

        # Kahn's cycle check
        kahn_q: deque[int] = deque(qid for qid, deg in indegree.items() if deg == 0)
        popped = 0
        while kahn_q:
            u = kahn_q.popleft()
            popped += 1
            for v in quests[u]["nextQuestIds"]:
                indegree[v] -= 1
                if indegree[v] == 0:
                    kahn_q.append(v)
        if popped != len(quests):
            cyc = [qid for qid, deg in indegree.items() if deg > 0]
            self._err(f"Quest DAG cycle detected involving {len(cyc)} quests: {cyc[:15]}")

        # 4. Domain spot-checks & anomaly reporting
        spot_checks = [
            (275, "Wetlands Blisterpaw / Claws"),
            (286, "Wetlands Return the Statuette"),
            (465, "Nek'rosh's Gambit"),
            (470, "Digging Through the Ooze"),
            (98189, "Forever Wetlands chain quest"),
            (95646, "Excavation Site: Horrors in the Highland"),
            (95795, "Excavation Site: Fallen in the Fen"),
            (98815, "Excavation Site: Highland Hides"),
            (92432, "Dalaran key chain: An Alarming Request"),
            (92458, "Dalaran: Heart of Disruption"),
            (95787, "Drowned City: Take Two With Water"),
            (94980, "Krol'dok Compound Key start"),
            (95262, "Alcaz Prison: The Secret Prison"),
            (93450, "Shen'dralas: Phantasmal Problems"),
            (93269, "Mount Hyjal: Top of the Post"),
            (96830, "Mount Hyjal: Farsight Guided"),
            (96779, "Forever Rogue: Syndicate Sabotage"),
            (96785, "Forever Rogue: Ain't No Telling"),
            (96786, "Forever Rogue: The Syndicate Sting"),
            (78676, "Forever Rogue: The Eye of Bhossca"),
            (79192, "Bag quest: 12-slot Old Toolbox"),
            (531, "Bag quest: 10-slot Hunting Satchel"),
            (58, "Bag quest: 10-slot Night Watch"),
            (2439, "Bag quest: 14-slot Thawpelt Sack"),
            (626, "Bag quest: 12-slot Explorer's Knapsack"),
            (3628, "Bag quest: 16-slot Demon Hide Sack"),
        ]
        # Verify known non-Rogue custom Forever quests are excluded from eligibleAllianceGnomeRogue
        for non_rogue_qid in (96438, 79939, 95753, 93797):
            nrq = quests.get(non_rogue_qid)
            if nrq and nrq["eligibleAllianceGnomeRogue"]:
                self._err(f"Non-Rogue custom quest {non_rogue_qid} ({nrq['name']}) leaked into eligibleAllianceGnomeRogue")

        # Verify trace-verified Wetlands prerequisite chains & Lv 24 opening quest integrity
        expected_prereqs = {
            98230: [474, 98223],
            465: [464, 98221],
            98072: [471],
            98291: [98245, 98246],
            98297: [98291],
            95646: [275],
            98815: [484],
        }
        for qid, exp_pre in expected_prereqs.items():
            q = quests.get(qid)
            if not q:
                self._err(f"Missing Wetlands regression quest {qid}")
            elif sorted(q.get("prereqIds", [])) != sorted(exp_pre):
                self._err(f"Quest {qid} ({q['name']}) prereqIds={q.get('prereqIds')}, expected {exp_pre}")

        wetlands_lv24_openers = (276, 279, 294, 305, 455, 463, 464, 469, 470, 484, 95647, 98197, 98221, 98223, 98246, 98282)
        for qid in wetlands_lv24_openers:
            q = quests.get(qid)
            if not q:
                self._err(f"Missing Wetlands Lv24 opener {qid}")
                continue
            if not q["eligibleAllianceGnomeRogue"]:
                self._err(f"Wetlands Lv24 opener {qid} ({q['name']}) must be eligible")
            if q["minLevel"] > 24:
                self._err(f"Wetlands Lv24 opener {qid} ({q['name']}) minLevel={q['minLevel']} > 24")
            if q["prereqIds"]:
                self._err(f"Wetlands Lv24 opener {qid} ({q['name']}) has unexpected prereqIds={q['prereqIds']}")

        # Verify no mid/high-level quest silently fell back to minLevel <= 1
        verified_lvl1_class_qids = {78261, 78270, 78307}
        for qid, q in quests.items():
            if q["eligibleAllianceGnomeRogue"] and q["questLevel"] >= 12 and q["minLevel"] <= 1 and qid not in verified_lvl1_class_qids:
                self._err(f"Quest {qid} ({q['name']}) questLevel={q['questLevel']} has unverified minLevel={q['minLevel']}")

        # Verify high requiredMinRep / SoD / deprecated FLB-only / broken stub / Welcome / Winter Veil / Mount swap exclusions
        for gated_qid in (785, 909, 3366, 3911, 5101, 5507, 81730, 94898, 95816, 95817, 96476, 99204):
            gq = quests.get(gated_qid)
            if gq and (gq["eligibleAllianceGnomeRogue"] or gq.get("isLevelingQuest")):
                self._err(f"Rep/SoD/stub quest {gated_qid} ({gq['name']}) leaked into leveling/eligible flags")

        for non_lvl_qid in (5805, 5841, 5842, 5843, 5844, 5847, 7660, 7661, 8744, 8767, 8768, 8788, 8803, 60860, 86747, 92454, 93161, 93925, 98287, 98471):
            nq = quests.get(non_lvl_qid)
            if nq and nq.get("isLevelingQuest"):
                self._err(f"Welcome/Mount/WinterVeil/stub quest {non_lvl_qid} ({nq['name']}) leaked into isLevelingQuest")

        # Verify Lv 1-23 starter quests, Deeprun Tram coords, and early Horde/Alliance dungeons
        for starter_qid in (783, 179, 456, 4641, 747, 363, 92464, 5730, 914, 166, 6565, 96393, 97292):
            sq = quests.get(starter_qid)
            if not sq or not sq.get("isLevelingQuest") or sq["starterCoords"] is None:
                self._err(f"Expected Lv 1-23 starter/dungeon quest {starter_qid} missing or lacking isLevelingQuest/coords")
        for tram_qid in (6661, 6662):
            tq = quests.get(tram_qid)
            if not tq or not tq.get("isLevelingQuest") or tq["starterCoords"] is None or tq["starterCoords"]["zoneId"] != 1519:
                self._err(f"Deeprun Tram quest {tram_qid} missing Stormwind (1519) starterCoords")
        q92464 = quests.get(92464)
        if not q92464 or q92464["factionMask"] != 1:
            self._err(f"Skyborne Alliance starter 92464 expected factionMask=1, got {q92464 and q92464['factionMask']}")

        # Verify Leatherworking (165) & Skinning (393) profession quests are included cleanly,
        # while Blacksmithing/Alchemy/Tailoring/Engineering/Herbalism stay excluded.
        for lw_qid in (1582, 2847, 2848, 2849, 2850, 2851, 2852, 2853, 5141, 5143, 5144, 96031):
            lq = quests.get(lw_qid)
            if not lq or not lq["eligibleAllianceGnomeRogue"] or not lq["isLeatherworking"]:
                self._err(f"Expected eligible Leatherworking quest {lw_qid} missing or flagged ineligible")
        for sk_qid in (5885, 91751, 96056):
            sq = quests.get(sk_qid)
            if not sq or not sq["eligibleAllianceGnomeRogue"] or not sq["isSkinning"]:
                self._err(f"Expected eligible Skinning quest {sk_qid} missing or flagged ineligible")
        for other_craft_qid in (1559, 1578, 1581, 3379, 4104, 4105, 4107, 96044, 96045, 96058, 96059):
            oq = quests.get(other_craft_qid)
            if oq and oq["eligibleAllianceGnomeRogue"]:
                self._err(f"Non-LW/Skinning crafting quest {other_craft_qid} ({oq['name']}) leaked into eligibleAllianceGnomeRogue")
        q1487 = quests.get(1487)
        if not q1487 or not any(r["itemId"] == 6476 for r in q1487.get("lwRewards", [])):
            self._err("Expected Deviate Eradication (1487) to expose Pattern: Deviate Scale Belt (6476) in lwRewards")

        # Verify skinningScore > 0 for beast/dragonkin quests and == 0 for non-beast/spider quests
        for beast_qid in (185, 190, 194, 294, 471, 484, 91751, 98072, 98297, 98815):
            bq = quests.get(beast_qid)
            if not bq or bq["skinningScore"] <= 0 or bq["beastTargetCount"] <= 0 or not bq["beastTypes"]:
                self._err(f"Beast/Dragonkin quest {beast_qid} missing positive skinningScore/beastTypes")
        for non_beast_qid in (275, 276, 279, 470, 98282):
            nbq = quests.get(non_beast_qid)
            if not nbq or nbq["skinningScore"] != 0 or nbq["beastTargetCount"] != 0 or nbq["beastTypes"]:
                self._err(f"Non-skinnable quest {non_beast_qid} ({nbq['name'] if nbq else '?'}) has non-zero skinningScore={nbq['skinningScore'] if nbq else None}")

        # Verify Forever 1.60.1 / QuestieDB 1.1.0 audited Wetlands & Lv 20-33 levels and elite flags
        expected_levels_and_group = {
            464: (23, 28, False),     # War Banners (Alliance Wetlands)
            303: (25, 30, True),      # The Dark Iron War (QuestieDB 1.1.0 minLevel=25, elite)
            378: (25, 27, True),      # The Fury Runs Deep (Stockade minLevel=25)
            1132: (18, 20, False),    # Fiora Longears (Wetlands -> Theramore breadcrumb)
            87491: (27, 32, True),    # Spark of Freedom (QuestieDB 1.1.0 level 32, elite)
            88756: (27, 32, True),    # Bring Back a Bang (QuestieDB 1.1.0 level 32, elite)
            98027: (25, 29, False),   # Gleaming Crescent Necklace (minLevel=25, level=29)
            98155: (27, 31, False),   # Pristine Pesterhide Pelts (minLevel=27, level=31)
            98156: (27, 31, False),   # Packaged Pristine Pelts (QuestieDB 1.1.0 27/31)
            115: (18, 23, True),      # Shadow Magic (elite)
            169: (15, 26, True),      # WANTED: Gath'Ilzogg (elite)
            271: (15, 20, True),      # Vyrin's Revenge (elite)
            531: (15, 20, True),      # Vyrin's Revenge (elite)
            731: (15, 20, True),      # The Absent Minded Prospector (elite)
            2078: (14, 20, True),     # Gyromast's Revenge (elite)
        }
        for aq_id, (exp_min, exp_lvl, exp_grp) in expected_levels_and_group.items():
            aq = quests.get(aq_id)
            actual = (aq["minLevel"], aq["questLevel"], aq["isGroup"]) if aq else None
            if actual != (exp_min, exp_lvl, exp_grp):
                self._err(f"Quest {aq_id} expected (min,lvl,grp)={(exp_min, exp_lvl, exp_grp)}, got {actual}")

        print("=== Domain Spot Checks ===")
        for qid, label in spot_checks:
            q = quests.get(qid)
            if not q:
                self._err(f"Spot-check quest {qid} ({label}) missing")
                continue
            print(
                f"  {qid} [{q['zoneId']} {q['zoneName']}] {q['name']!r}: "
                f"lvl={q['minLevel']}-{q['questLevel']} xp={q['xp']} copper={q['goldCopper']} "
                f"bag={q['bagSlots']} rogue={q['rogueScore']} elig={q['eligibleAllianceGnomeRogue']} "
                f"pre={q['prereqIds']} next={q['nextQuestIds']} sc={q['starterCoords']} tc={q['turninCoords']}"
            )

        elig = [q for q in quests.values() if q["eligibleAllianceGnomeRogue"]]
        lvl = [q for q in quests.values() if q.get("isLevelingQuest")]
        zero_xp_core = [
            q for q in elig if 24 <= q["questLevel"] <= 45 and q["xp"] == 0 and not q["isBreadcrumb"]
        ]
        print(f"\n=== Summary Diagnostics ===")
        print(
            f"Total quests: {len(quests)}, Leveling 1-60: {len(lvl)} "
            f"(A={sum(1 for q in lvl if q['factionMask'] == 1)}, H={sum(1 for q in lvl if q['factionMask'] == 2)}, "
            f"N={sum(1 for q in lvl if q['factionMask'] == 3)}, Lv1-23={sum(1 for q in lvl if q['questLevel'] <= 23)}), "
            f"Eligible Alliance Gnome Rogue: {len(elig)} (22-60: {sum(1 for q in elig if 22 <= q['questLevel'] <= 60)})"
        )
        print(f"Dungeons: {len(dungeons)}, Travel nodes: {len(nodes)}, Travel edges: {len(edges)}")
        print(f"Non-breadcrumb eligible lvl 24-45 quests with 0 XP: {len(zero_xp_core)}")
        for q in zero_xp_core[:25]:
            print(f"  0-XP: {q['questId']} [{q['zoneId']} {q['zoneName']}] {q['name']!r} lvl={q['questLevel']} sflags={q['questKind']} prov={q['sourceProvenance']}")

        no_obj_core = [
            q for q in elig
            if 24 <= q["questLevel"] <= 45 and q["questKind"] in ("kill", "collect") and not q["objectiveCoords"]
        ]
        print(f"Eligible lvl 24-45 kill/collect quests missing objectiveCoords: {len(no_obj_core)}")
        for q in no_obj_core[:15]:
            print(f"  no-obj-coord: {q['questId']} [{q['zoneId']} {q['zoneName']}] {q['name']!r} kind={q['questKind']}")

        if self.warnings:
            print(f"\nWARNINGS ({len(self.warnings)}):")
            for w in self.warnings[:20]:
                print(" ", w)
        if self.errors:
            print(f"\nERRORS ({len(self.errors)}):")
            for e in self.errors[:30]:
                print(" ", e)
            return 1

        print("\nVALIDATION PASSED WITH ZERO ERRORS.")
        return 0


def main() -> int:
    """Entrypoint for dataset validation."""
    return DatasetValidator().validate()


if __name__ == "__main__":
    sys.exit(main())
