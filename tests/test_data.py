"""Independent validation tests for data/*.json against WoW Forever ground truth."""

import heapq
import json
import math
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path("/Users/sebastianmakinson/Documents/ForeverQuestPath")
DATA_DIR = PROJECT_ROOT / "data"
ADDONS_DIR = Path("/Applications/World of Warcraft/_forever_/Interface/AddOns")
FLB_QUESTS_LUA = ADDONS_DIR / "ForeverLevelingBiS" / "Data" / "Quests.lua"
QUESTIE_DUNGEONS_LUA = ADDONS_DIR / "QuestieDB" / "support" / "Forever" / "Zones" / "dungeons.lua"

GNOME_RACE_BIT = 64
ROGUE_CLASS_BIT = 8
ALLIANCE_FACTION_MASK = 1


def _load_json(name: str) -> dict:
    path = DATA_DIR / name
    assert path.exists(), f"Missing required data file: {path}"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_flb_spot_checks() -> dict[int, dict]:
    """Parse select spot-check fields from ForeverLevelingBiS/Data/Quests.lua."""
    if not FLB_QUESTS_LUA.exists():
        return {}
    spot: dict[int, dict] = {}
    pattern = re.compile(r"^\[(\d+)\]=\{(.*)\}$")
    with FLB_QUESTS_LUA.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            m = pattern.match(line)
            if not m:
                continue
            qid = int(m.group(1))
            body = m.group(2)
            entry: dict = {}
            name_m = re.search(r'name="((?:[^"\\]|\\.)*)"', body)
            if name_m:
                entry["name"] = name_m.group(1)
            for field in ("level", "reqLevel", "side", "zoneId", "reqRace", "reqClass"):
                fm = re.search(rf"\b{field}=(\d+)", body)
                if fm:
                    entry[field] = int(fm.group(1))
            xp_m = re.search(r"\bxp=(\d+)", body)
            if xp_m:
                entry["xp"] = int(xp_m.group(1))
            spot[qid] = entry
    return spot


# Rules for TestForeverQuestData:
# - Validates data/quests.json, data/dungeons.json, data/travel_graph.json, data/nav_graph.json,
#   and data/schema.md.
# - Independently checks DAG acyclicity, zero dangling prerequisite IDs, coordinate bounds,
#   XP > 0 on leveling quests, full Lv 1-60 multi-faction/multi-race/multi-class coverage
#   (all 10 Forever races incl. Skyborne, 9 classes, classScores, Lv 1-23 zones & dungeons,
#   Horde/Alliance travel edges), prerequisite-eligibility consistency (no cross-faction,
#   cross-race, or cross-class AND-prereq deadlocks), Leatherworking (165/-182) & Skinning (393)
#   inclusion vs other crafting skill exclusion, lwRewards/skinningScore/beastTargetCount/beastTypes,
#   quest 98230, quest 464 (War Banners Lv28), audited Lv20-33 minLevel/questLevel/isGroup fixes,
#   minLevel fallback sanity, multimodal terrain/gate NavGraph schema, 20x20 cell component
#   consistency, questAnchors completeness, Stonetalon mountain detour, ground tunnels/bridges,
#   faction reachability, and opening Wetlands Lv24 verification against QuestieDB
#   (including corrections/), foreverchanges.pro, and ForeverLevelingBiS.
class TestForeverQuestData(unittest.TestCase):
    """Independent validation of scraped/normalized WoW Forever quest, dungeon, and travel data."""

    quests_doc: dict
    quests: dict[int, dict]
    dungeons_doc: dict
    dungeons: dict[str, dict]
    travel_doc: dict
    flb_quests: dict[int, dict]

    @classmethod
    def setUpClass(cls) -> None:
        cls.quests_doc = _load_json("quests.json")
        cls.dungeons_doc = _load_json("dungeons.json")
        cls.travel_doc = _load_json("travel_graph.json")
        raw_quests = cls.quests_doc.get("quests", {})
        cls.quests = {int(k): v for k, v in raw_quests.items()}
        cls.dungeons = cls.dungeons_doc.get("dungeons", {})
        cls.flb_quests = _parse_flb_spot_checks()

    def test_required_files_and_schema_doc(self) -> None:
        schema_path = DATA_DIR / "schema.md"
        self.assertTrue(schema_path.exists(), "Missing data/schema.md")
        self.assertGreater(schema_path.stat().st_size, 200, "data/schema.md appears empty")
        self.assertGreater(len(self.quests), 100, "Expected substantial quest dataset in data/quests.json")
        self.assertGreater(len(self.dungeons), 8, "Expected full WoW Forever dungeon list in data/dungeons.json")

    def test_quest_required_fields_and_types(self) -> None:
        required_keys = {
            "questId",
            "name",
            "zoneId",
            "zoneName",
            "minLevel",
            "questLevel",
            "xp",
            "goldCopper",
            "bagSlots",
            "specialRewards",
            "rogueScore",
            "classScores",
            "isLevelingQuest",
            "requiredSkill",
            "isLeatherworking",
            "isSkinning",
            "lwRewards",
            "skinningScore",
            "beastTargetCount",
            "beastTypes",
            "prereqIds",
            "nextQuestIds",
            "starterCoords",
            "turninCoords",
            "objectiveCoords",
            "isDungeon",
            "dungeonId",
            "isGroup",
            "factionMask",
            "classMask",
            "eligibleAllianceGnomeRogue",
            "sourceProvenance",
        }
        for qid, q in self.quests.items():
            missing = required_keys - set(q.keys())
            self.assertFalse(missing, f"Quest {qid} missing keys: {sorted(missing)}")
            self.assertEqual(int(q["questId"]), qid, f"Quest key/id mismatch for {qid}")
            self.assertIsInstance(q["name"], str)
            self.assertTrue(q["name"].strip(), f"Quest {qid} has empty name")
            self.assertIsInstance(q["bagSlots"], int)
            self.assertGreaterEqual(q["bagSlots"], 0)
            self.assertIsInstance(q["goldCopper"], (int, float))
            self.assertGreaterEqual(q["goldCopper"], 0)
            self.assertGreaterEqual(q["rogueScore"], 0)
            self.assertLessEqual(q["rogueScore"], 100)
            self.assertIsInstance(q["isLevelingQuest"], bool, f"Quest {qid} isLevelingQuest must be bool")
            cscores = q["classScores"]
            self.assertIsInstance(cscores, dict, f"Quest {qid} classScores must be dict")
            for ckey in ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID"):
                self.assertIn(ckey, cscores, f"Quest {qid} classScores missing {ckey}")
                self.assertGreaterEqual(float(cscores[ckey]), 0.0)
                self.assertLessEqual(float(cscores[ckey]), 100.0)
            self.assertAlmostEqual(float(cscores["ROGUE"]), float(q["rogueScore"]), places=2)
            rs = q["requiredSkill"]
            if rs is not None:
                self.assertIsInstance(rs, dict, f"Quest {qid} requiredSkill must be dict or None")
                self.assertIn("skillId", rs)
                self.assertIn("minSkill", rs)
            self.assertIsInstance(q["isLeatherworking"], bool, f"Quest {qid} isLeatherworking must be bool")
            self.assertIsInstance(q["isSkinning"], bool, f"Quest {qid} isSkinning must be bool")
            self.assertIsInstance(q["lwRewards"], list, f"Quest {qid} lwRewards must be list")
            self.assertGreaterEqual(q["skinningScore"], 0.0)
            self.assertLessEqual(q["skinningScore"], 100.0)
            self.assertIsInstance(q["beastTargetCount"], int)
            self.assertGreaterEqual(q["beastTargetCount"], 0)
            self.assertIsInstance(q["beastTypes"], list)
            for bt in q["beastTypes"]:
                self.assertIn(bt, ("Beast", "Dragonkin"), f"Unexpected beastType {bt!r} on quest {qid}")
            self.assertIsInstance(q["sourceProvenance"], list)
            self.assertGreater(len(q["sourceProvenance"]), 0, f"Quest {qid} missing sourceProvenance")

    def test_no_missing_prereqs_or_next_quest_ids(self) -> None:
        for qid, q in self.quests.items():
            for pid in q.get("prereqIds", []):
                self.assertIn(int(pid), self.quests, f"Quest {qid} references missing prereqId {pid}")
                self.assertNotEqual(int(pid), qid, f"Quest {qid} lists itself as prereqId")
            for nid in q.get("nextQuestIds", []):
                self.assertIn(int(nid), self.quests, f"Quest {qid} references missing nextQuestId {nid}")
                self.assertNotEqual(int(nid), qid, f"Quest {qid} lists itself as nextQuestId")

    def test_prerequisite_dag_is_acyclic(self) -> None:
        # 0 = unvisited, 1 = visiting, 2 = visited
        state: dict[int, int] = {}
        stack: list[int] = []

        def dfs(node: int) -> None:
            st = state.get(node, 0)
            if st == 1:
                cycle_start = stack.index(node)
                cycle = stack[cycle_start:] + [node]
                self.fail(f"Prerequisite cycle detected: {cycle}")
            if st == 2:
                return
            state[node] = 1
            stack.append(node)
            for pid in self.quests[node].get("prereqIds", []):
                dfs(int(pid))
            stack.pop()
            state[node] = 2

        for qid in self.quests:
            if state.get(qid, 0) == 0:
                dfs(qid)

    def test_coordinates_valid_and_wetlands_24_45_complete(self) -> None:
        def check_coord(coord: dict, label: str) -> None:
            self.assertIsInstance(coord, dict, f"{label} coord must be dict")
            self.assertIn("zoneId", coord, f"{label} coord missing zoneId")
            self.assertIn("x", coord, f"{label} coord missing x")
            self.assertIn("y", coord, f"{label} coord missing y")
            self.assertGreaterEqual(coord["x"], 0.0, f"{label} x out of range: {coord}")
            self.assertLessEqual(coord["x"], 100.0, f"{label} x out of range: {coord}")
            self.assertGreaterEqual(coord["y"], 0.0, f"{label} y out of range: {coord}")
            self.assertLessEqual(coord["y"], 100.0, f"{label} y out of range: {coord}")

        wetlands_eligible = 0
        core_24_45_eligible = 0
        for qid, q in self.quests.items():
            if q.get("starterCoords") is not None:
                check_coord(q["starterCoords"], f"Quest {qid} starter")
            if q.get("turninCoords") is not None:
                check_coord(q["turninCoords"], f"Quest {qid} turnin")
            for idx, oc in enumerate(q.get("objectiveCoords", [])):
                check_coord(oc, f"Quest {qid} objective[{idx}]")

            if q["eligibleAllianceGnomeRogue"]:
                if q["zoneId"] == 11:
                    wetlands_eligible += 1
                    self.assertIsNotNone(q["starterCoords"], f"Wetlands quest {qid} ({q['name']}) missing starterCoords")
                    self.assertIsNotNone(q["turninCoords"], f"Wetlands quest {qid} ({q['name']}) missing turninCoords")
                if 24 <= q["questLevel"] <= 45 and not q.get("isDungeon"):
                    core_24_45_eligible += 1
                    has_any_coords = (
                        q["starterCoords"] is not None
                        and q["turninCoords"] is not None
                    )
                    self.assertTrue(has_any_coords, f"Core 24-45 quest {qid} ({q['name']}) missing starter/turnin coords")

        self.assertGreaterEqual(wetlands_eligible, 15, "Expected >= 15 eligible Wetlands quests")
        self.assertGreaterEqual(core_24_45_eligible, 80, "Expected >= 80 eligible 24-45 open-world quests")

    def test_xp_and_alliance_gnome_rogue_eligibility(self) -> None:
        eligible_xp_positive = 0
        eligible_total = 0
        for qid, q in self.quests.items():
            self.assertGreaterEqual(q["xp"], 0, f"Quest {qid} has negative XP")
            if q["eligibleAllianceGnomeRogue"]:
                eligible_total += 1
                if q["xp"] > 0:
                    eligible_xp_positive += 1
                # Must permit Alliance (1 or 3; never strictly Horde-only 2)
                self.assertIn(q["factionMask"], (1, 3), f"Eligible quest {qid} ({q['name']}) has non-Alliance factionMask={q['factionMask']}")
                # Class mask: 0 means all classes, otherwise Rogue bit (8) must be set
                cmask = int(q["classMask"])
                if cmask != 0:
                    self.assertNotEqual(cmask & ROGUE_CLASS_BIT, 0, f"Eligible quest {qid} ({q['name']}) excludes Rogue (classMask={cmask})")
                # Race mask if present: 0 means all races, otherwise Gnome bit (64) must be set
                rmask = int(q.get("raceMask", 0))
                if rmask != 0:
                    self.assertNotEqual(rmask & GNOME_RACE_BIT, 0, f"Eligible quest {qid} ({q['name']}) excludes Gnome (raceMask={rmask})")

        self.assertGreater(eligible_total, 150, "Expected > 150 eligible Alliance Gnome Rogue quests (22-60)")
        self.assertGreater(
            eligible_xp_positive / eligible_total,
            0.90,
            f"Too many eligible quests have 0 XP: {eligible_xp_positive}/{eligible_total}",
        )

        # Explicitly verify race/class exclusions if those quests were scraped
        for excluded_qid, reason in [
            (98166, "Your First Horse is Human-only (reqRace=1)"),
            (29, "Trial of the Lake is Druid-only"),
            (26, "A Lesson to Learn is Druid-only"),
        ]:
            if excluded_qid in self.quests:
                self.assertFalse(
                    self.quests[excluded_qid]["eligibleAllianceGnomeRogue"],
                    f"Quest {excluded_qid} should NOT be eligible ({reason})",
                )

    def test_spot_checks_against_forever_leveling_bis(self) -> None:
        # Core Wetlands & Forever quests verified in ForeverLevelingBiS/Data/Quests.lua
        expected_quests = {
            279: ("Claws from the Deep", 11, 20, 22),
            294: ("Ormer's Revenge", 11, 22, 24),
            304: ("A Grim Task", 11, 26, 34),
            464: ("War Banners", 11, 23, 28),
            474: ("Defeat Nek'rosh", 11, 23, 32),
            # QuestieDB_Forever enforces minLevel=28 (vs Era ForeverLevelingBiS reqLevel=26)
            631: ("The Thandol Span", 11, 28, 31),
            95646: ("Horrors in the Highland", None, 24, 31),
            95647: ("Lost in the Thicket Things", None, 24, 31),
            98824: ("Prehistoric Prism", None, 24, 31),
        }
        for qid, (exp_name, exp_zone, exp_min, exp_lvl) in expected_quests.items():
            self.assertIn(qid, self.quests, f"Missing key spot-check quest {qid} ({exp_name})")
            q = self.quests[qid]
            self.assertIn(exp_name.lower(), q["name"].lower(), f"Quest {qid} name mismatch: {q['name']}")
            self.assertEqual(q["minLevel"], exp_min, f"Quest {qid} minLevel mismatch")
            self.assertEqual(q["questLevel"], exp_lvl, f"Quest {qid} questLevel mismatch")
            if exp_zone is not None:
                self.assertEqual(q["zoneId"], exp_zone, f"Quest {qid} zoneId mismatch")
            if qid in self.flb_quests and "xp" in self.flb_quests[qid]:
                self.assertEqual(q["xp"], self.flb_quests[qid]["xp"], f"Quest {qid} XP differs from ForeverLevelingBiS")
            self.assertTrue(q["eligibleAllianceGnomeRogue"], f"Quest {qid} should be eligible for Alliance Gnome Rogue")

    def test_dungeons_and_unlock_chains_wow_forever(self) -> None:
        by_zone = {d["zoneId"]: d for d in self.dungeons.values()}
        expected_dungeon_zones = {
            717: "The Stockade",
            16732: "Excavation Site: Wetlands",
            719: "Blackfathom Deeps",
            16544: "City of Dalaran",
            721: "Gnomeregan",
            796: "Scarlet Monastery",
            491: "Razorfen Kraul",
            16842: "Krol'dok Stronghold",
            722: "Razorfen Downs",
            1337: "Uldaman",
            1176: "Zul'Farrak",
        }
        for dz, dname in expected_dungeon_zones.items():
            self.assertIn(dz, by_zone, f"Missing WoW Forever dungeon zoneId={dz} ({dname}) in dungeons.json")
            d = by_zone[dz]
            self.assertGreater(len(d.get("questIds", [])), 0, f"Dungeon {dname} has empty questIds")
            for qid in d["questIds"]:
                self.assertIn(int(qid), self.quests, f"Dungeon {dname} references missing questId {qid}")
            for chain in d.get("unlockChains", []):
                c_qids = [int(x) for x in chain.get("questIds", [])]
                self.assertGreater(len(c_qids), 0, f"Dungeon {dname} has empty unlockChain")
                for qid in c_qids:
                    self.assertIn(qid, self.quests, f"Dungeon {dname} unlockChain references missing questId {qid}")

        # Verify Excavation Site: Wetlands includes real Forever dungeon quests
        excav = by_zone[16732]
        excav_qids = set(int(x) for x in excav["questIds"])
        for expected_qid in (95646, 95647, 95772, 95795, 95809, 98815, 98824):
            self.assertIn(expected_qid, excav_qids, f"Excavation Site: Wetlands missing quest {expected_qid}")

    def test_bag_and_gold_rewards_present(self) -> None:
        bag_quests = [q for q in self.quests.values() if q["eligibleAllianceGnomeRogue"] and q["bagSlots"] > 0]
        high_gold_quests = [q for q in self.quests.values() if q["eligibleAllianceGnomeRogue"] and q["goldCopper"] >= 5000]
        rogue_upgrade_quests = [q for q in self.quests.values() if q["eligibleAllianceGnomeRogue"] and q["rogueScore"] >= 40]
        self.assertGreaterEqual(len(bag_quests), 3, "Expected >= 3 Alliance-eligible bag-reward quests in 22-60")
        for bq in bag_quests:
            self.assertGreaterEqual(bq["bagSlots"], 4, f"Unexpected bagSlots={bq['bagSlots']} on quest {bq['questId']}")
        large_bag_quests = [q for q in bag_quests if q["bagSlots"] >= 10]
        self.assertGreaterEqual(len(large_bag_quests), 3, "Expected >= 3 Alliance-eligible >=10-slot bag quests")
        self.assertGreaterEqual(len(high_gold_quests), 10, "Expected >= 10 quests rewarding >= 50s (5000 copper)")
        self.assertGreaterEqual(len(rogue_upgrade_quests), 10, "Expected >= 10 high-value Rogue gear quests")

    def test_eligible_quests_have_no_ineligible_prereqs(self) -> None:
        """No eligible quest should be blocked by Horde/wrong-class/wrong-race/ineligible AND-prereqs."""
        violations = []
        for qid, q in sorted(self.quests.items()):
            if not q["eligibleAllianceGnomeRogue"]:
                continue
            bad_prereqs = []
            for pid in q.get("prereqIds", []):
                p = self.quests.get(int(pid))
                if p is not None and not p["eligibleAllianceGnomeRogue"]:
                    bad_prereqs.append((int(pid), p["factionMask"], p["classMask"], p["raceMask"], p["name"]))
            if bad_prereqs:
                violations.append((qid, q["name"], q["prereqIds"], bad_prereqs))
        self.assertFalse(
            violations,
            f"Found {len(violations)} eligible Alliance Gnome Rogue quests with ineligible prereqIds: "
            f"{violations[:10]}",
        )

    def test_travel_graph_and_xp_curve(self) -> None:
        xp_curve = self.travel_doc.get("xpCurve", {})
        for lvl in range(24, 60):
            self.assertIn(str(lvl), xp_curve, f"xpCurve missing level {lvl}")
            self.assertGreater(xp_curve[str(lvl)], xp_curve[str(lvl - 1)], f"xpCurve not increasing at level {lvl}")

        nodes = self.travel_doc.get("nodes", {})
        for required_zone in ("11", "1537", "1519", "38", "45", "10", "44", "148", "331", "440"):
            self.assertIn(required_zone, nodes, f"travel_graph missing key Alliance zone node {required_zone}")

        edges = self.travel_doc.get("edges", [])
        self.assertGreater(len(edges), 20, "travel_graph has too few edges")
        # Verify Wetlands (11) is connected via Menethil boat/flight/run
        wetlands_modes = {
            e["mode"]
            for e in edges
            if str(e["fromZoneId"]) == "11" or str(e["toZoneId"]) == "11"
        }
        self.assertIn("boat", wetlands_modes, "Wetlands (Menethil Harbor) must have boat travel edges")
        self.assertIn("flight", wetlands_modes, "Wetlands (Menethil Harbor) must have flight path edges")


    def test_wetlands_lv24_openers_and_98230_and_min_level_integrity(self) -> None:
        # 1. Quest 98230 ("This Land Was Their Land") is Lv 29 (minLevel=22) AND gated behind [474, 98223]
        #    in official QuestieDB Forever 1.1.0, so it is NEVER acceptable as an opener at Lv 24.
        self.assertIn(98230, self.quests)
        q98230 = self.quests[98230]
        self.assertEqual(q98230["name"], "This Land Was Their Land")
        self.assertEqual(int(q98230["minLevel"]), 22)
        self.assertEqual(int(q98230["questLevel"]), 29)
        self.assertEqual(int(q98230["zoneId"]), 11)
        self.assertEqual(sorted(q98230.get("prereqIds", [])), [474, 98223])

        # Verify restored Wetlands/Excavation Site prerequisite chains from QuestieDB 1.1.0
        expected_wetlands_prereqs = {
            465: [464, 98221],
            98072: [471],
            98240: [98072],
            98245: [98240],
            98291: [98245, 98246],
            98297: [98291],
            95646: [275],
            98815: [484],
        }
        for qid, expected_pre in expected_wetlands_prereqs.items():
            self.assertEqual(
                sorted(self.quests[qid].get("prereqIds", [])),
                sorted(expected_pre),
                f"Quest {qid} ({self.quests[qid]['name']}) prereqIds mismatch",
            )

        # 2. True Lv 24 Wetlands opening quests (verified in QuestieDB Forever 1.1.0 & ForeverLevelingBiS)
        #    must all be eligible at Lv 24 in Wetlands (11) with zero false/hidden prerequisites.
        lv24_wetlands_openers = {
            463: ("The Greenwarden", 20, 21),
            279: ("Claws from the Deep", 20, 22),
            469: ("Daily Delivery", 18, 21),
            470: ("Digging Through the Ooze", 19, 24),
            305: ("In Search of The Excavation Team", 21, 24),
            98197: ("Spoils of War", 18, 22),
            98282: ("Alchemical Hazards", 20, 22),
            294: ("Ormer's Revenge", 22, 24),
            484: ("Young Crocolisk Skins", 18, 22),
            276: ("Tramping Paws", 20, 21),
            464: ("War Banners", 23, 28),
            98221: ("From the Ashes", 23, 28),
            98223: ("Old Habits", 23, 31),
            98246: ("Trying Times", 21, 29),
        }
        for qid, (expected_name, min_lvl, q_lvl) in lv24_wetlands_openers.items():
            q = self.quests[qid]
            self.assertEqual(q["name"], expected_name)
            self.assertTrue(q["eligibleAllianceGnomeRogue"], f"Opener {qid} must be eligible")
            self.assertEqual(int(q["zoneId"]), 11)
            self.assertEqual(int((q.get("starterCoords") or {}).get("zoneId") or 0), 11)
            self.assertLessEqual(int(q["minLevel"]), 24, f"Opener {qid} minLevel > 24")
            self.assertGreaterEqual(int(q["minLevel"]), min_lvl, f"Opener {qid} minLevel < QuestieDB {min_lvl}")
            self.assertEqual(int(q["questLevel"]), q_lvl, f"Opener {qid} questLevel mismatch")
            self.assertEqual(q.get("prereqIds", []), [], f"Opener {qid} should have 0 false prereqs: {q.get('prereqIds')}")

        # 3. No quest with questLevel >= 20 should silently fall back to minLevel <= 5
        #    (except 78261/78270/78307, verified in QuestieDB 1.1.0 & foreverchanges.pro as real requiredLevel=1 Rogue SFK quests).
        verified_lvl1_rogue_sfk_quests = {78261, 78270, 78307}
        fallback_min_leaks = [
            (q["questId"], q["name"], q["minLevel"], q["questLevel"])
            for q in self.quests.values()
            if q.get("eligibleAllianceGnomeRogue")
            and int(q["questId"]) not in verified_lvl1_rogue_sfk_quests
            and int(q.get("questLevel", 0)) >= 20
            and int(q.get("minLevel", 0)) <= 5
        ]
        self.assertEqual(
            fallback_min_leaks,
            [],
            f"Found quests >= Lv20 with suspicious minLevel <= 5 fallback: {fallback_min_leaks[:10]}",
        )

        # 4. Deprecated/beta/temp-disabled ForeverLevelingBiS-only quests absent from QuestieDB 1.1.0
        #    and blacklisted in QuestieQuestBlacklist.lua (e.g. 3366 duplicate of 6981, 785 BETA, 3911 OLD, 94898 Temp Disabled)
        #    must never be marked eligible for routes.
        deprecated_flb_only = {785, 3366, 3911, 94898}
        leaked_deprecated = [
            (qid, self.quests[qid]["name"])
            for qid in sorted(deprecated_flb_only)
            if qid in self.quests and self.quests[qid].get("eligibleAllianceGnomeRogue")
        ]
        self.assertEqual(
            leaked_deprecated,
            [],
            f"Deprecated/blacklisted/disabled ForeverLevelingBiS-only quests marked eligible: {leaked_deprecated}",
        )

        # 5. Audited Lv 20-33 Wetlands/Stormwind level & group fixes (#67: quest 464 War Banners +
        #    QuestieDB 1.1.0 / foreverchanges.pro overrides and elite flags)
        self.assertFalse(bool(self.quests[464].get("isGroup", False)), "Quest 464 (War Banners) is solo, not group")
        audited_levels = {
            303: (25, 30, True),    # The Dark Iron War
            378: (25, 27, True),    # The Fury Runs Deep
            98027: (25, 29, False), # Gleaming Crescent Necklace
            98155: (27, 31, False), # Pristine Pesterhide Pelts
            98156: (27, 31, False), # Packaged Pristine Pelts
            87491: (27, 32, True),  # Spark of Freedom
            88756: (27, 32, True),  # Bring Back a Bang
        }
        for qid, (min_lvl, q_lvl, exp_group) in audited_levels.items():
            q = self.quests[qid]
            self.assertEqual(int(q["minLevel"]), min_lvl, f"Audited quest {qid} minLevel mismatch")
            self.assertEqual(int(q["questLevel"]), q_lvl, f"Audited quest {qid} questLevel mismatch")
            self.assertEqual(bool(q.get("isGroup", False)), exp_group, f"Audited quest {qid} isGroup mismatch")
        for elite_qid in (115, 169, 271, 531, 731, 2078):
            self.assertTrue(bool(self.quests[elite_qid].get("isGroup", False)), f"Quest {elite_qid} must be flagged isGroup=True")

    def test_skinning_and_leatherworking_fields_and_profession_filtering(self) -> None:
        schema_text = (DATA_DIR / "schema.md").read_text(encoding="utf-8")
        for field_name in (
            "requiredSkill",
            "isLeatherworking",
            "isSkinning",
            "lwRewards",
            "skinningScore",
            "beastTargetCount",
            "beastTypes",
        ):
            self.assertIn(field_name, schema_text, f"data/schema.md must document '{field_name}'")

        # Alliance Leatherworking (165) quests must be eligible with pattern metadata where applicable
        expected_lw_qids = (1582, 2847, 2848, 2849, 2850, 2851, 2852, 2853, 5141, 5143, 5144, 7497)
        for qid in expected_lw_qids:
            self.assertIn(qid, self.quests, f"Missing Leatherworking quest {qid}")
            q = self.quests[qid]
            self.assertTrue(q["eligibleAllianceGnomeRogue"], f"LW quest {qid} ({q['name']}) should be eligible")
            self.assertTrue(q["isLeatherworking"], f"LW quest {qid} should have isLeatherworking=True")
            self.assertEqual((q["requiredSkill"] or {}).get("skillId"), 165, f"LW quest {qid} skillId mismatch")
        for pattern_qid in (1487, 1582, 2848, 2849, 2850, 2851, 2852, 2853):
            self.assertTrue(
                any(r.get("kind") == "lw_pattern" for r in self.quests[pattern_qid].get("lwRewards", [])),
                f"Quest {pattern_qid} expected lw_pattern reward in lwRewards",
            )

        # Alliance Skinning (393) quests must be eligible; Horde-only 4106 must stay ineligible
        for qid in (5885, 91751):
            self.assertIn(qid, self.quests, f"Missing Skinning quest {qid}")
            q = self.quests[qid]
            self.assertTrue(q["eligibleAllianceGnomeRogue"], f"Skinning quest {qid} should be eligible")
            self.assertTrue(q["isSkinning"], f"Skinning quest {qid} should have isSkinning=True")
            self.assertEqual((q["requiredSkill"] or {}).get("skillId"), 393, f"Skinning quest {qid} skillId mismatch")
        if 4106 in self.quests:
            self.assertFalse(
                self.quests[4106]["eligibleAllianceGnomeRogue"],
                "Horde Salve via Skinning (4106) must remain ineligible",
            )

        # Other primary crafting/gathering skills must stay excluded for Alliance Gnome Rogue
        excluded_skill_ids = {164, 171, 182, 186, 197, 202, 333}
        excluded_leaks = [
            (qid, q["name"], q["requiredSkill"])
            for qid, q in sorted(self.quests.items())
            if q["eligibleAllianceGnomeRogue"]
            and isinstance(q.get("requiredSkill"), dict)
            and q["requiredSkill"].get("skillId") in excluded_skill_ids
        ]
        self.assertEqual(excluded_leaks, [], f"Non-LW/Skinning profession quests leaked into eligible set: {excluded_leaks}")

        # Skinnable Beast / Dragonkin quests vs non-beast quests spot-checks
        beast_spot_checks = (484, 294, 295, 296, 98815, 185, 186, 190, 194, 98215, 4182, 1487, 5141, 91751)
        for qid in beast_spot_checks:
            q = self.quests[qid]
            self.assertGreater(q["skinningScore"], 0.0, f"Quest {qid} ({q['name']}) should have skinningScore > 0")
            self.assertGreater(q["beastTargetCount"], 0, f"Quest {qid} ({q['name']}) should have beastTargetCount > 0")
            self.assertTrue(q["beastTypes"], f"Quest {qid} ({q['name']}) should list beastTypes")
        self.assertIn("Dragonkin", self.quests[4182]["beastTypes"])
        self.assertIn("Dragonkin", self.quests[98215]["beastTypes"])

        non_beast_spot_checks = (463, 469, 388, 93)
        for qid in non_beast_spot_checks:
            q = self.quests[qid]
            self.assertEqual(q["skinningScore"], 0.0, f"Non-beast quest {qid} ({q['name']}) should have skinningScore == 0")
            self.assertEqual(q["beastTargetCount"], 0, f"Non-beast quest {qid} ({q['name']}) should have beastTargetCount == 0")
            self.assertEqual(q["beastTypes"], [], f"Non-beast quest {qid} ({q['name']}) should have empty beastTypes")

    def test_lv1_to_23_multifaction_multirace_multiclass_coverage(self) -> None:
        # 1. Verify all 10 WoW Forever races & 9 classes in travel_graph.json
        races = self.travel_doc.get("races", [])
        classes = self.travel_doc.get("classes", [])
        self.assertIsInstance(races, list)
        self.assertIsInstance(classes, list)
        by_race = {r["name"]: r for r in races}
        for req_race in (
            "Human", "Orc", "Dwarf", "NightElf", "Scourge",
            "Tauren", "Gnome", "Troll", "HighOrderSkyborne", "WindshaperSkyborne",
        ):
            self.assertIn(req_race, by_race, f"travel_graph.races missing {req_race}")
            r = by_race[req_race]
            self.assertIn(r["faction"], ("Alliance", "Horde"))
            self.assertGreater(int(r["raceBit"]), 0)
            self.assertGreater(int(r["startZoneId"]), 0)
            self.assertIsInstance(r.get("startCoords"), dict)
            self.assertTrue(r.get("classes"), f"Race {req_race} missing allowed classes")

        by_class = {c["key"]: c for c in classes}
        expected_classes = {
            "WARRIOR": 1, "PALADIN": 2, "HUNTER": 4, "ROGUE": 8,
            "PRIEST": 16, "SHAMAN": 64, "MAGE": 128, "WARLOCK": 256, "DRUID": 1024,
        }
        for ckey, cbit in expected_classes.items():
            self.assertIn(ckey, by_class, f"travel_graph.classes missing {ckey}")
            self.assertEqual(int(by_class[ckey]["classBit"]), cbit)
            self.assertTrue(by_class[ckey].get("races"), f"Class {ckey} missing races")

        # 2. Verify Horde & Alliance Lv 1-23 starting + early zone travel coverage
        edges = self.travel_doc.get("edges", [])
        modes = {e["mode"] for e in edges}
        for req_mode in ("flight", "boat", "tram", "zeppelin", "airship", "run"):
            self.assertIn(req_mode, modes, f"travel_graph missing travel mode '{req_mode}'")
        nodes = self.travel_doc.get("nodes", {})
        for z in ("12", "1", "141", "16593", "14", "215", "85", "40", "38", "148", "17", "130", "1637", "1638", "1497", "1519", "1537", "1657"):
            self.assertIn(z, nodes, f"travel_graph missing Lv1-23/capital zone node {z}")

        # 3. Verify Lv 1-23 leveling quest counts across all Alliance & Horde starting/early zones
        lv1_23_by_zone: dict[int, int] = {}
        missing_coords_1_45 = []
        prereq_mask_deadlocks = []
        for qid, q in sorted(self.quests.items()):
            if not q["isLevelingQuest"]:
                continue
            if 1 <= q["questLevel"] <= 23:
                lv1_23_by_zone[q["zoneId"]] = lv1_23_by_zone.get(q["zoneId"], 0) + 1
            if not q.get("isDungeon") and 1 <= q["questLevel"] <= 45:
                if q.get("starterCoords") is None or q.get("turninCoords") is None:
                    missing_coords_1_45.append((qid, q["name"], q["zoneId"], q["questLevel"]))
            for pid in q.get("prereqIds", []):
                p = self.quests.get(int(pid))
                if not p or not p["isLevelingQuest"]:
                    prereq_mask_deadlocks.append(("non_leveling_prereq", qid, int(pid)))
                elif (int(q["factionMask"]) & int(p["factionMask"])) == 0:
                    prereq_mask_deadlocks.append(("cross_faction", qid, q["factionMask"], int(pid), p["factionMask"]))
                elif q.get("classMask") and p.get("classMask") and (int(q["classMask"]) & int(p["classMask"])) == 0:
                    prereq_mask_deadlocks.append(("cross_class", qid, q["classMask"], int(pid), p["classMask"]))
                elif q.get("raceMask") and p.get("raceMask") and (int(q["raceMask"]) & int(p["raceMask"])) == 0:
                    prereq_mask_deadlocks.append(("cross_race", qid, q["raceMask"], int(pid), p["raceMask"]))

        for zid, zname in (
            (12, "Elwynn Forest"), (1, "Dun Morogh"), (141, "Teldrassil"), (16593, "Zephras Isle"),
            (14, "Durotar"), (215, "Mulgore"), (85, "Tirisfal Glades"), (40, "Westfall"),
            (38, "Loch Modan"), (148, "Darkshore"), (17, "The Barrens"), (130, "Silverpine Forest"),
        ):
            self.assertGreaterEqual(lv1_23_by_zone.get(zid, 0), 25, f"Too few Lv1-23 leveling quests in {zname} ({zid})")

        self.assertEqual(missing_coords_1_45, [], f"Missing coords on Lv1-45 leveling quests: {missing_coords_1_45[:10]}")
        self.assertEqual(prereq_mask_deadlocks, [], f"Found cross-faction/race/class prereq deadlocks: {prereq_mask_deadlocks[:10]}")

        # 4. Verify early Alliance, Horde, and Forever custom dungeons + unlockChains
        for dslug, expect_chains in (
            ("ragefire-chasm", True),
            ("the-deadmines", True),
            ("wailing-caverns", True),
            ("shadowfang-keep", False),
            ("hall-of-thanes", True),
            ("ruins-of-lordaeron", True),
            ("blackfathom-deeps", True),
            ("the-stockade", True),
        ):
            self.assertIn(dslug, self.dungeons, f"Missing early dungeon '{dslug}'")
            d = self.dungeons[dslug]
            self.assertGreater(len(d.get("questIds", [])), 0, f"Dungeon {dslug} has no questIds")
            if expect_chains:
                self.assertGreater(len(d.get("unlockChains", [])), 0, f"Dungeon {dslug} missing unlockChains")

        # 5. Verify multi-class gear & class-quest differentiation
        self.assertIn(3636, self.quests)
        cs3636 = self.quests[3636]["classScores"]
        self.assertGreater(cs3636["WARRIOR"], 50.0)
        self.assertGreater(cs3636["PALADIN"], 50.0)
        self.assertGreater(cs3636["PRIEST"], 40.0)
        self.assertIn(78699, self.quests)
        self.assertGreater(self.quests[78699]["classScores"]["ROGUE"], 50.0)
        # Class quests across multiple classMasks must exist as isLevelingQuest
        seen_class_bits = {int(q["classMask"]) for q in self.quests.values() if q["isLevelingQuest"] and int(q["classMask"]) > 0}
        for cbit in (1, 2, 4, 8, 16, 64, 128, 256, 1024):
            self.assertIn(cbit, seen_class_bits, f"Expected class-specific leveling quests for classBit={cbit}")

    def test_nav_graph_integrity_and_terrain_gates(self) -> None:
        nav = _load_json("nav_graph.json")
        meta = nav["meta"]
        self.assertGreater(meta["nodeCount"], 500)
        self.assertLessEqual(meta["nodeCount"], 1500)
        self.assertLessEqual(meta["edgeCount"], 6000)
        self.assertEqual(meta["gridWidth"], 20)
        self.assertEqual(meta["gridHeight"], 20)

        nodes = {int(n["id"]): n for n in nav["nodes"]}
        self.assertEqual(len(nodes), meta["nodeCount"])
        self.assertEqual(len(nav["edges"]), meta["edgeCount"])
        self.assertEqual(len(nav["zoneGrids"]), 50)
        self.assertEqual(set(nav["hearthNodes"].keys()), set(nav["zoneGrids"].keys()))

        for zstr, nid in nav["hearthNodes"].items():
            self.assertIn(int(nid), nodes, f"Hearth node {nid} for zone {zstr} missing")
            self.assertEqual(int(nodes[int(nid)]["zoneId"]), int(zstr))

        valid_modes = {"walk", "gate", "flight", "boat", "zeppelin", "airship", "tram"}
        valid_factions = {"Alliance", "Horde", "Both"}
        cross_land_pairs: set[tuple[int, int]] = set()
        for e in nav["edges"]:
            u_id, v_id = int(e["fromId"]), int(e["toId"])
            self.assertIn(u_id, nodes)
            self.assertIn(v_id, nodes)
            sec = float(e["seconds"])
            self.assertTrue(math.isfinite(sec) and sec > 0.0, f"Invalid edge seconds on {e}")
            self.assertIn(e["mode"], valid_modes)
            self.assertIn(e["faction"], valid_factions)
            if e["mode"] in ("walk", "gate") and nodes[u_id]["zoneId"] != nodes[v_id]["zoneId"]:
                cross_land_pairs.add((int(nodes[u_id]["zoneId"]), int(nodes[v_id]["zoneId"])))

        # Verify impossible mountain-wall border jumps do NOT exist
        for forbidden_pair in ((1519, 46), (46, 1519), (38, 12), (12, 38), (11, 1), (1, 11)):
            self.assertNotIn(forbidden_pair, cross_land_pairs, f"Forbidden border shortcut edge: {forbidden_pair}")

        # Verify every active 20x20 cell anchor belongs to the same zone & terrain componentId
        for zstr, zg in nav["zoneGrids"].items():
            zid = int(zstr)
            self.assertEqual(zg["width"], 20)
            self.assertEqual(zg["height"], 20)
            self.assertEqual(len(zg["worldBounds"]), 5)
            for cell_key, cell in zg["cells"].items():
                c_idx = int(cell_key)
                self.assertGreaterEqual(c_idx, 1)
                self.assertLessEqual(c_idx, 400)
                self.assertIn(cell["terrain"], ("road", "walkable", "swim"))
                self.assertGreaterEqual(len(cell["anchors"]), 1)
                self.assertLessEqual(len(cell["anchors"]), 3)
                for nid, walk_sec in cell["anchors"]:
                    n = nodes[int(nid)]
                    self.assertEqual(int(n["zoneId"]), zid)
                    self.assertEqual(int(n["componentId"]), int(cell["componentId"]))
                    self.assertTrue(math.isfinite(float(walk_sec)) and float(walk_sec) >= 0.0)

        # Verify questAnchors covers every quest with coordinates
        qa = nav["questAnchors"]
        for qid, q in self.quests.items():
            has_coords = bool(q.get("starterCoords") or q.get("turninCoords") or q.get("objectiveCoords"))
            if has_coords:
                self.assertIn(str(qid), qa, f"Quest {qid} has coordinates but is missing from questAnchors")
        for qstr, entry in qa.items():
            for role in ("s", "o", "t"):
                for nid, sec in (entry.get(role) or []):
                    self.assertIn(int(nid), nodes, f"questAnchors[{qstr}].{role} references missing node {nid}")
                    self.assertGreaterEqual(float(sec), 0.0)

        # Verify Stonetalon Mountains (406) Charred Vale <-> Fold barrier blocks straight-line cut at y~75
        zg406 = nav["zoneGrids"]["406"]["cells"]
        blocked_between = [cx for cx in range(7, 15) if str(15 * 20 + cx + 1) not in zg406]
        self.assertGreater(len(blocked_between), 0, "Expected blocked mountain ridge cells between Charred Vale and The Fold")

        # Independent Dijkstra over nav_graph.json (ground-only vs full multimodal, Alliance vs Horde)
        def _cell_anchors(zid: int, x: float, y: float) -> list[list[float]]:
            zg = nav["zoneGrids"][str(zid)]
            cx = min(19, max(0, int(x * 20.0 / 100.0)))
            cy = min(19, max(0, int(y * 20.0 / 100.0)))
            cell = zg["cells"].get(str(cy * 20 + cx + 1))
            if cell:
                return cell["anchors"]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    c2 = zg["cells"].get(str(min(19, max(0, cy + dy)) * 20 + min(19, max(0, cx + dx)) + 1))
                    if c2:
                        return c2["anchors"]
            self.fail(f"No walkable cell near ({zid}, {x}, {y})")
            return []

        def _shortest(z1: int, x1: float, y1: float, z2: int, x2: float, y2: float, *, faction: str, allow_flight: bool, mounted: bool = False):
            adj: dict[int, list[tuple[int, float, str, str]]] = {}
            scale = float(meta["unmountedYdsPerSec"]) / float(meta["mountedYdsPerSec"]) if mounted else 1.0
            for e in nav["edges"]:
                if e["faction"] not in ("Both", faction):
                    continue
                if (not allow_flight) and e["mode"] == "flight":
                    continue
                w = float(e["seconds"]) * (scale if e.get("groundScaled") else 1.0)
                adj.setdefault(int(e["fromId"]), []).append((int(e["toId"]), w, e["mode"], e.get("note", "")))
            src = _cell_anchors(z1, x1, y1)
            dst = dict((int(nid), float(sec) * scale) for nid, sec in _cell_anchors(z2, x2, y2))
            dist: dict[int, float] = {}
            prev: dict[int, tuple[int, str, str]] = {}
            pq: list[tuple[float, int]] = []
            for nid, w in src:
                nid_i, w_f = int(nid), float(w) * scale
                dist[nid_i] = w_f
                heapq.heappush(pq, (w_f, nid_i))
            best_sec, best_node = 1e18, None
            while pq:
                d, u = heapq.heappop(pq)
                if d > dist.get(u, 1e18) or d >= best_sec:
                    continue
                if u in dst and d + dst[u] < best_sec:
                    best_sec = d + dst[u]
                    best_node = u
                for v, w, mode, note in adj.get(u, ()):
                    nd = d + w
                    if nd < dist.get(v, 1e18):
                        dist[v] = nd
                        prev[v] = (u, mode, note)
                        heapq.heappush(pq, (nd, v))
            hops = []
            cur = best_node
            while cur in prev:
                pu, mode, note = prev[cur]
                if mode != "walk":
                    hops.append((int(nodes[pu]["zoneId"]), int(nodes[cur]["zoneId"]), mode, note))
                cur = pu
            hops.reverse()
            return best_sec, hops

        cv_sec, _ = _shortest(406, 33.0, 76.0, 406, 56.0, 76.0, faction="Horde", allow_flight=False, mounted=False)
        self.assertGreater(cv_sec, 200.0, f"Charred Vale -> Fold must detour around mountain barrier (>200s), got {cv_sec:.1f}s")
        cv_mounted, _ = _shortest(406, 33.0, 76.0, 406, 56.0, 76.0, faction="Horde", allow_flight=False, mounted=True)
        self.assertLess(cv_mounted, cv_sec * 0.75, "Mounted speed must scale down ground paths cleanly")

        for z1, x1, y1, z2, x2, y2, fac, expected_substr in (
            (11, 9.5, 59.7, 38, 35.0, 49.0, "Alliance", "Dun Algaz"),
            (11, 9.5, 59.7, 45, 45.0, 46.0, "Alliance", "Thandol Span"),
            (331, 36.0, 50.0, 406, 46.0, 60.0, "Alliance", "Talondeep"),
            (17, 52.0, 30.0, 406, 46.0, 60.0, "Horde", "Stonetalon"),
            (361, 50.0, 50.0, 618, 60.0, 40.0, "Alliance", "Timbermaw"),
        ):
            sec, hops = _shortest(z1, x1, y1, z2, x2, y2, faction=fac, allow_flight=False)
            self.assertLess(sec, 2000.0)
            self.assertTrue(
                any(expected_substr in note for _, _, _, note in hops),
                f"Expected ground route ({z1}->{z2}, {fac}) to traverse '{expected_substr}', got {hops}",
            )

        # Faction reachability check across all 50 zones
        def _reachable_zones(start_zid: int, faction: str) -> set[int]:
            adj: dict[int, list[int]] = {}
            for e in nav["edges"]:
                if e["faction"] in ("Both", faction):
                    adj.setdefault(int(e["fromId"]), []).append(int(e["toId"]))
            start_nid = int(nav["hearthNodes"][str(start_zid)])
            seen = {start_nid}
            stack = [start_nid]
            while stack:
                u = stack.pop()
                for v in adj.get(u, ()):
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)
            return {int(nodes[nid]["zoneId"]) for nid in seen}

        ally_zones = _reachable_zones(11, "Alliance")
        horde_zones = _reachable_zones(17, "Horde")
        self.assertEqual(len(ally_zones), 50, f"Alliance missing zones: {set(map(int, nav['zoneGrids'])) - ally_zones}")
        self.assertGreaterEqual(len(horde_zones), 48)
        self.assertIn(16593, horde_zones, "Horde must reach Zephras Isle via Mulgore/Alterac airship")
        self.assertNotIn(141, horde_zones, "Horde must not reach Alliance-only Teldrassil")
        self.assertNotIn(1657, horde_zones, "Horde must not reach Alliance-only Darnassus")


if __name__ == "__main__":
    unittest.main()
