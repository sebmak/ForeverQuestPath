"""Independent verification tests for sim/, routes/, and addon/Data/GeneratedData.lua."""

import json
import subprocess
import sys
import unittest
from collections import deque
from pathlib import Path

PROJECT_ROOT = Path("/Users/sebastianmakinson/Documents/ForeverQuestPath")
DATA_DIR = PROJECT_ROOT / "data"
ROUTES_DIR = PROJECT_ROOT / "routes"
SIM_DIR = PROJECT_ROOT / "sim"
GENERATED_LUA = PROJECT_ROOT / "addon" / "Data" / "GeneratedData.lua"

PROFILES = ("balanced", "skinning_lw", "dungeon_focus", "treasure_bags", "fast_xp")


def _load_json(path: Path) -> dict:
    assert path.exists(), f"Missing expected file: {path}"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _export_generated_lua_to_json() -> dict:
    """Execute addon/Data/GeneratedData.lua under LuaJIT and serialize ns.GeneratedData to JSON."""
    assert GENERATED_LUA.exists(), f"Missing {GENERATED_LUA}"
    lua_script = r"""
local path = __GENERATED_LUA_PATH__
local chunk, err = loadfile(path)
assert(chunk, "Lua syntax error in GeneratedData.lua: " .. tostring(err))
local ns = {}
chunk("ForeverQuestPath", ns)
local gd = assert(ns.GeneratedData, "ns.GeneratedData missing after loading GeneratedData.lua")

local function escape_str(s)
    s = s:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t")
    return '"' .. s .. '"'
end

local function is_array(t)
    local count = 0
    local max_i = 0
    for k, _ in pairs(t) do
        if type(k) ~= "number" or k < 1 or k % 1 ~= 0 then
            return false
        end
        count = count + 1
        if k > max_i then max_i = k end
    end
    return count == max_i and count > 0
end

local function to_json(v)
    local tv = type(v)
    if tv == "nil" then return "null"
    elseif tv == "boolean" then return v and "true" or "false"
    elseif tv == "number" then
        if v ~= v or v == math.huge or v == -math.huge then return "null" end
        return tostring(v)
    elseif tv == "string" then return escape_str(v)
    elseif tv == "table" then
        if is_array(v) then
            local parts = {}
            for i = 1, #v do parts[#parts + 1] = to_json(v[i]) end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            local parts = {}
            local keys = {}
            for k in pairs(v) do keys[#keys + 1] = k end
            table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
            for _, k in ipairs(keys) do
                parts[#parts + 1] = escape_str(tostring(k)) .. ":" .. to_json(v[k])
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    end
    error("Unsupported value type: " .. tv)
end

io.write(to_json(gd))
""".replace("__GENERATED_LUA_PATH__", repr(str(GENERATED_LUA)))
    proc = subprocess.run(
        ["luajit", "-e", lua_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"LuaJIT failed on GeneratedData.lua:\n{proc.stderr}"
    return json.loads(proc.stdout)


# Rules for TestSimulationAndRoutes:
# - Independently validates routes/sim_metrics.json (strategyComparison, profileComparison across
#   all 5 profiles including skinning_lw, level24Benchmarks, and 8 multi-race/class archetypes),
#   routes/simulation_report.md, addon/Data/GeneratedData.lua (meta.races/classes, 1->60
#   profiles, starterRoutes 1->24, hordeRoutes 20->60, indexes, load time < 250ms), AND
#   addon/Data/NavData.lua CSR tables (forward/reverse CSR exactness, zoneCells, questAnchors,
#   load time < 150ms).
# - Directly exercises sim/ core modules (DatasetLoader, XPModel decay/colors, QuestDAG
#   gating, TravelModel shortest paths, NavGraph Bidirectional Dijkstra, Stonetalon/tunnel barriers,
#   O(1) Hearthstone, FP discovery penalties, Lv40+ mount scaling, and nearest_eligible_quests
#   frontier cutoff equivalence) AND runs live 1->60 multi-race/multi-class/multi-faction
#   + 24->60 Wetlands simulations, enforcing topological prerequisite order, minLevel, default
#   +2 max_level_offset ceiling (`questLevel <= expectedLevel + 2`, keeping quest 98230 Lv29 and
#   quest 464 War Banners Lv28 deferred until Lv26+), faction/race/class bitmasks,
#   zero deprecated IDs, faction-aware travel reachability, smooth 1->23->60 leveling, and
#   optimization gains vs naive_greedy baseline.
class TestSimulationAndRoutes(unittest.TestCase):
    """Independent validation of route optimizer, XP/travel simulation, and Lua export."""

    quests: dict[int, dict]
    dungeons: dict[str, dict]
    travel_graph: dict
    sim_metrics: dict
    generated_data: dict

    @classmethod
    def setUpClass(cls) -> None:
        quests_doc = _load_json(DATA_DIR / "quests.json")
        cls.quests = {int(k): v for k, v in quests_doc["quests"].items()}
        cls.dungeons = _load_json(DATA_DIR / "dungeons.json")["dungeons"]
        cls.travel_graph = _load_json(DATA_DIR / "travel_graph.json")
        cls.sim_metrics = _load_json(ROUTES_DIR / "sim_metrics.json")
        cls.generated_data = _export_generated_lua_to_json()

    def test_report_and_lua_export_structure(self) -> None:
        report_path = ROUTES_DIR / "simulation_report.md"
        self.assertTrue(report_path.exists(), "Missing routes/simulation_report.md")
        report_text = report_path.read_text(encoding="utf-8")
        self.assertGreater(len(report_text), 500, "routes/simulation_report.md looks too short")
        for prof in PROFILES:
            self.assertIn(prof, report_text, f"simulation_report.md does not mention profile {prof}")

        gd = self.generated_data
        for key in ("meta", "xpCurve", "zones", "dungeons", "quests", "profiles", "starterRoutes", "hordeRoutes", "indexes"):
            self.assertIn(key, gd, f"GeneratedData.lua missing top-level key '{key}'")
        self.assertEqual(gd["meta"]["targetCharacter"]["startLevel"], 1)
        self.assertEqual(gd["meta"]["targetCharacter"]["startZoneId"], 1)
        self.assertEqual(gd["meta"]["targetCharacter"]["faction"], "Alliance")
        self.assertEqual(gd["meta"]["targetCharacter"]["race"], "Gnome")
        self.assertEqual(gd["meta"]["targetCharacter"]["class"], "ROGUE")
        self.assertEqual(gd["meta"].get("defaultMaxLevelOffset"), 2)
        self.assertEqual(gd["meta"].get("maxLevelOffsetRange"), [-3, 5])

        # Verify LuaJIT load + execution time stays crisp (< 250ms)
        lua_bench = f"""
local t0 = os.clock()
local chunk = assert(loadfile({str(GENERATED_LUA)!r}))
local ns = {{}}
chunk("ForeverQuestPath", ns)
local elapsed_ms = (os.clock() - t0) * 1000.0
assert(ns.GeneratedData and ns.GeneratedData.quests, "GeneratedData missing")
print(string.format("LOAD_MS=%.2f", elapsed_ms))
"""
        proc = subprocess.run(["luajit", "-e", lua_bench], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"GeneratedData.lua bench failed:\n{proc.stderr}")
        load_ms = float(proc.stdout.strip().split("LOAD_MS=")[1].splitlines()[0])
        self.assertLess(load_ms, 250.0, f"GeneratedData.lua load too slow: {load_ms:.2f}ms")

        # Validate meta.races (10) & meta.classes (9) & meta.zoneUiMapIds
        races_meta = gd["meta"].get("races", {})
        classes_meta = gd["meta"].get("classes", {})
        for req_race in ("Human", "Orc", "Dwarf", "NightElf", "Scourge", "Tauren", "Gnome", "Troll", "HighOrderSkyborne", "WindshaperSkyborne"):
            self.assertIn(req_race, races_meta, f"GeneratedData.meta.races missing {req_race}")
        for req_cls in ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID"):
            self.assertIn(req_cls, classes_meta, f"GeneratedData.meta.classes missing {req_cls}")
        self.assertGreaterEqual(len(gd["meta"].get("zoneUiMapIds", {})), 40)

        for prof in PROFILES:
            self.assertIn(prof, gd["profiles"], f"GeneratedData.lua missing profile '{prof}'")
            p = gd["profiles"][prof]
            self.assertGreater(len(p["steps"]), 200, f"Profile '{prof}' 1->60 has too few steps")
            for wkey in ("xp", "travel", "dungeon", "reward", "skinning"):
                val = p["weights"][wkey]
                self.assertIsInstance(val, int, f"Weight {wkey} should be int 0..100")
                self.assertGreaterEqual(val, 0)
                self.assertLessEqual(val, 100)
            summary = p.get("summary", {})
            for skey in ("lwQuestsCompleted", "skinningQuestsCompleted", "beastQuestsCompleted", "totalSkinningScore"):
                self.assertIn(skey, summary, f"Profile '{prof}' summary missing '{skey}'")

        # Validate starterRoutes (1->24 across all race starting zones) & hordeRoutes (20->60)
        starter_routes = gd["starterRoutes"]
        for sr_key in ("1", "12", "14", "85", "141", "215", "16593_Alliance", "16593_Horde"):
            self.assertIn(sr_key, starter_routes, f"GeneratedData.starterRoutes missing '{sr_key}'")
            sr = starter_routes[sr_key]
            self.assertEqual(int(sr.get("minLevel", 0)), 1, f"starterRoutes[{sr_key}] minLevel should be 1")
            self.assertGreaterEqual(int(sr.get("maxLevel", 0)), 23, f"starterRoutes[{sr_key}] maxLevel < 23")
            self.assertGreater(len(sr.get("steps", [])), 50, f"starterRoutes[{sr_key}] too few steps")
        self.assertIn("balanced", gd["hordeRoutes"], "GeneratedData.hordeRoutes missing 'balanced'")
        self.assertGreater(len(gd["hordeRoutes"]["balanced"].get("steps", [])), 200, "hordeRoutes.balanced too short")

        # Validate data.indexes structure & quest cross-references
        indexes = gd["indexes"]
        required_index_lists = (
            "specialRewardIds",
            "bagQuestIds",
            "goldQuestIds",
            "rogueQuestIds",
            "classRewardIds",
            "lwQuestIds",
            "skinningQuestIds",
            "dungeonQuestIds",
            "eligibleIds",
        )
        lua_qids = {int(k) for k in gd["quests"].keys()}
        for idx_key in required_index_lists:
            self.assertIn(idx_key, indexes, f"GeneratedData.lua indexes missing '{idx_key}'")
            vals = indexes[idx_key]
            self.assertIsInstance(vals, list, f"indexes.{idx_key} must be a list")
            self.assertGreater(len(vals), 0, f"indexes.{idx_key} must be non-empty")
            for qid in vals:
                self.assertIn(int(qid), lua_qids, f"indexes.{idx_key} contains unknown questId {qid}")
        self.assertIn("byZone", indexes, "GeneratedData.lua indexes missing 'byZone'")
        self.assertIn("11", indexes["byZone"], "indexes.byZone missing Wetlands ('11')")
        self.assertIn("12", indexes["byZone"], "indexes.byZone missing Elwynn ('12')")
        self.assertIn("14", indexes["byZone"], "indexes.byZone missing Durotar ('14')")
        self.assertIn("byFaction", indexes, "GeneratedData.lua indexes missing 'byFaction'")
        by_fac = indexes["byFaction"]
        if isinstance(by_fac, list):
            self.assertEqual(len(by_fac), 3, "indexes.byFaction should have 3 entries ([1]=Alliance,[2]=Horde,[3]=Neutral)")
            for fidx, flist in enumerate(by_fac, start=1):
                self.assertGreater(len(flist), 100, f"indexes.byFaction[{fidx}] too small")
        else:
            for fk in ("1", "2", "3"):
                self.assertGreater(len(by_fac.get(fk, [])), 100, f"indexes.byFaction[{fk}] too small")
        self.assertIn("classQuests", indexes, "GeneratedData.lua indexes missing 'classQuests'")
        for ckey in ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID"):
            self.assertGreater(len(indexes["classQuests"].get(ckey, [])), 0, f"indexes.classQuests missing {ckey}")
        lw_idx_set = {int(q) for q in indexes["lwQuestIds"]}
        for expected_lw in (1582, 2847, 2853, 5141, 5143, 5144):
            self.assertIn(expected_lw, lw_idx_set, f"indexes.lwQuestIds missing LW quest {expected_lw}")
        skin_idx_set = {int(q) for q in indexes["skinningQuestIds"]}
        for expected_skin in (294, 484, 5885, 91751, 98215, 4182):
            self.assertIn(expected_skin, skin_idx_set, f"indexes.skinningQuestIds missing skinnable quest {expected_skin}")

        # Verify Lua quest export carries LW/Skinning fields and step badges carry profession flags
        for sample_qid, attr_check in (("1582", "isLeatherworking"), ("5885", "isSkinning")):
            self.assertIn(sample_qid, gd["quests"], f"GeneratedData.lua quests missing {sample_qid}")
            lq = gd["quests"][sample_qid]
            self.assertTrue(lq.get(attr_check), f"Lua quest {sample_qid} missing {attr_check}=true")
            self.assertIsNotNone(lq.get("requiredSkill"), f"Lua quest {sample_qid} missing requiredSkill")
            self.assertIn("skinningValue", lq.get("features", {}), f"Lua quest {sample_qid} missing features.skinningValue")
        q294 = gd["quests"]["294"]
        self.assertGreater(float(q294.get("skinningScore", 0)), 0.0)
        self.assertGreater(int(q294.get("beastTargetCount", 0)), 0)
        self.assertIn("Beast", q294.get("beastTypes", []))
        self.assertTrue(
            any(
                isinstance(st.get("badges"), dict)
                and (st["badges"].get("isLeatherworking") or st["badges"].get("isSkinning") or float(st["badges"].get("skinningScore") or 0) > 0)
                for st in gd["profiles"]["skinning_lw"]["steps"]
            ),
            "skinning_lw steps should expose LW/Skinning badge fields",
        )

    def _build_reachability_map(self, faction: str = "Alliance") -> dict[int, set[int]]:
        adj: dict[int, set[int]] = {}
        for z_str in self.travel_graph.get("nodes", {}):
            adj.setdefault(int(z_str), set()).add(int(z_str))
        allowed_factions = (faction, "Both")
        for edge in self.travel_graph.get("edges", []):
            if edge.get("faction", "Both") not in allowed_factions:
                continue
            fz = int(edge["fromZoneId"])
            tz = int(edge["toZoneId"])
            adj.setdefault(fz, set()).add(tz)
            if edge.get("bidirectional", True):
                adj.setdefault(tz, set()).add(fz)
        # Dungeon instances are directly connected to their entrance zone
        for d in self.dungeons.values():
            dz = int(d["zoneId"])
            ez = int(d["entranceZoneId"])
            adj.setdefault(ez, set()).add(dz)
            adj.setdefault(dz, set()).add(ez)
        return adj

    def _is_reachable(self, adj: dict[int, set[int]], src: int, dst: int) -> bool:
        if src == dst:
            return True
        visited = {src}
        q = deque([src])
        while q:
            cur = q.popleft()
            if cur == dst:
                return True
            for nxt in adj.get(cur, ()):
                if nxt not in visited:
                    visited.add(nxt)
                    q.append(nxt)
        return False

    def _validate_route_steps(
        self,
        steps: list[dict],
        label: str,
        faction: str,
        race_bit: int,
        class_bit: int,
        start_level: int,
        start_zones: set[int],
        min_end_level: int,
        require_wetlands_visit: bool = False,
    ) -> None:
        self.assertGreater(len(steps), 0, f"{label} has no steps")
        adj = self._build_reachability_map(faction)
        f_bit = 1 if faction == "Alliance" else 2

        first_step = steps[0]
        if first_step["action"] != "TRAVEL":
            self.assertIn(int(first_step["zoneId"]), start_zones, f"{label}: first non-TRAVEL step zone {first_step['zoneId']} not in {start_zones}")
        self.assertGreaterEqual(int(first_step["expectedLevel"]), start_level, f"{label}: start level < {start_level}")

        completed: set[int] = set()
        accepted: set[int] = set()
        prev_level = start_level
        prev_zone = int(first_step["zoneId"])
        wetlands_steps = 0

        for idx, step in enumerate(steps):
            action = step["action"]
            lvl = int(step["expectedLevel"])
            zone_id = int(step["zoneId"])
            if zone_id == 11:
                wetlands_steps += 1

            self.assertGreaterEqual(lvl, prev_level, f"{label} step {idx + 1} ({step['title']}): level regressed {prev_level} -> {lvl}")
            prev_level = lvl

            if prev_zone != zone_id:
                self.assertTrue(
                    self._is_reachable(adj, prev_zone, zone_id),
                    f"{label} step {idx + 1}: unreachable {faction} zone transition {prev_zone} -> {zone_id}",
                )
            prev_zone = zone_id

            step_qids: list[int] = []
            if step.get("questId"):
                step_qids.append(int(step["questId"]))
            for qx in step.get("questIds") or []:
                if int(qx) not in step_qids:
                    step_qids.append(int(qx))

            for qid in step_qids:
                self.assertIn(qid, self.quests, f"{label} step {idx + 1} references unknown quest {qid}")
                q = self.quests[qid]
                self.assertTrue(q["isLevelingQuest"], f"{label} step {idx + 1} schedules non-leveling quest {qid} ({q['name']})")
                self.assertNotIn(qid, {785, 3366, 3911, 94898}, f"{label} step {idx + 1} schedules deprecated quest {qid}")
                if q["factionMask"] not in (0, 3):
                    self.assertNotEqual(int(q["factionMask"]) & f_bit, 0, f"{label} step {idx + 1} wrong faction for {qid}")
                if q.get("raceMask", 0) != 0 and race_bit:
                    self.assertNotEqual(int(q["raceMask"]) & race_bit, 0, f"{label} step {idx + 1} wrong race for {qid}")
                if q.get("classMask", 0) != 0 and class_bit:
                    self.assertNotEqual(int(q["classMask"]) & class_bit, 0, f"{label} step {idx + 1} wrong class for {qid}")
                self.assertGreaterEqual(lvl, int(q["minLevel"]), f"{label} step {idx + 1} ({q['name']}): Lv {lvl} < minLevel {q['minLevel']}")
                q_lvl = int(q.get("questLevel") or q["minLevel"])
                if action in ("ACCEPT", "DUNGEON_PREP"):
                    if qid == 98230:
                        self.assertGreater(lvl, 24, f"{label} step {idx + 1}: quest 98230 scheduled at Lv <= 24!")
                    if qid == 464:
                        self.assertGreaterEqual(lvl, 26, f"{label} step {idx + 1}: quest 464 (War Banners Lv28) scheduled at Lv {lvl} (< 26)!")
                    if not q.get("isDungeon"):
                        self.assertLessEqual(q_lvl, lvl + 2, f"{label} step {idx + 1} ({qid} {q['name']}): questLevel {q_lvl} > Lv {lvl} + 2")

            if action in ("ACCEPT", "DUNGEON_PREP"):
                for qid in step_qids:
                    q = self.quests[qid]
                    for pid in q.get("prereqIds", []):
                        pid_int = int(pid)
                        if self.quests[pid_int].get("isBreadcrumb"):
                            continue
                        self.assertIn(pid_int, completed, f"{label} step {idx + 1}: ACCEPT {qid} ({q['name']}) before prereq {pid_int} turned in")
                    accepted.add(qid)
            elif action == "COMPLETE":
                for qid in step_qids:
                    self.assertIn(qid, accepted | completed, f"{label} step {idx + 1}: COMPLETE {qid} before ACCEPT")
            elif action == "TURN_IN":
                for qid in step_qids:
                    q = self.quests[qid]
                    for pid in q.get("prereqIds", []):
                        pid_int = int(pid)
                        if self.quests[pid_int].get("isBreadcrumb"):
                            continue
                        self.assertIn(pid_int, completed, f"{label} step {idx + 1}: TURN_IN {qid} before prereq {pid_int} turned in")
                    completed.add(qid)
            elif action == "DUNGEON_RUN":
                for qid in step_qids:
                    completed.add(qid)

        if require_wetlands_visit:
            self.assertGreaterEqual(wetlands_steps, 5, f"{label}: too few Wetlands Lv20+ steps ({wetlands_steps})")
        self.assertGreaterEqual(prev_level, min_end_level, f"{label}: route ended early at level {prev_level} < {min_end_level}")

    def test_routes_respect_prereqs_min_level_lifecycle_and_travel(self) -> None:
        races = self.generated_data["meta"]["races"]
        classes = self.generated_data["meta"]["classes"]
        gnome_bit = int(races["Gnome"]["bitmask"])
        rogue_bit = int(classes["ROGUE"]["bitmask"])

        # 1. Validate all 5 primary Lv 1->60 Alliance Gnome Rogue profiles (Dun Morogh -> Wetlands -> 60)
        for prof in PROFILES:
            self._validate_route_steps(
                self.generated_data["profiles"][prof]["steps"],
                label=f"profile:{prof}",
                faction="Alliance",
                race_bit=gnome_bit,
                class_bit=rogue_bit,
                start_level=1,
                start_zones={1},
                min_end_level=60,
                require_wetlands_visit=True,
            )

        # 2. Validate all 8 Lv 1->24 race starterRoutes
        for sr_key in ("1", "12", "14", "85", "141", "215", "16593_Alliance", "16593_Horde"):
            sr = self.generated_data["starterRoutes"][sr_key]
            fac = sr["faction"]
            rep_race = sr.get("representativeRace")
            rep_cls = sr.get("representativeClass")
            r_bit = int(races[rep_race]["bitmask"]) if rep_race in races else 0
            c_bit = int(classes[rep_cls]["bitmask"]) if rep_cls in classes else 0
            self._validate_route_steps(
                sr["steps"],
                label=f"starter:{sr_key}",
                faction=fac,
                race_bit=r_bit,
                class_bit=c_bit,
                start_level=1,
                start_zones={int(sr["zoneId"])},
                min_end_level=23,
                require_wetlands_visit=False,
            )

        # 3. Validate Lv 20->60 Horde spine route
        hr = self.generated_data["hordeRoutes"]["balanced"]
        self._validate_route_steps(
            hr["steps"],
            label="hordeRoutes:balanced",
            faction="Horde",
            race_bit=int(races["Orc"]["bitmask"]),
            class_bit=int(classes["WARRIOR"]["bitmask"]),
            start_level=20,
            start_zones={14, 17, 130, 85, 215, 1637, 1638, 1497},
            min_end_level=60,
            require_wetlands_visit=False,
        )

    def test_optimization_and_profile_differentiation(self) -> None:
        metrics = self.sim_metrics
        self.assertIn("strategyComparison", metrics, "sim_metrics.json must include strategyComparison")
        self.assertIn("profileComparison", metrics, "sim_metrics.json must include profileComparison")

        by_strategy = {entry["strategy_name"]: entry for entry in metrics["strategyComparison"]}
        self.assertIn("naive_greedy", by_strategy, "strategyComparison missing naive_greedy baseline")
        baseline = by_strategy["naive_greedy"]
        base_xp_hr = float(baseline["avg_xp_per_hour"])
        base_travel_share = float(baseline["travel_share_pct"])

        prof_cmp = metrics["profileComparison"]
        for p in PROFILES:
            self.assertIn(p, prof_cmp, f"profileComparison missing profile '{p}'")
            self.assertIn("metrics", prof_cmp[p], f"profileComparison['{p}'] missing 'metrics'")

        fast_m = prof_cmp["fast_xp"]["metrics"]
        bal_m = prof_cmp["balanced"]["metrics"]
        dng_m = prof_cmp["dungeon_focus"]["metrics"]
        tb_m = prof_cmp["treasure_bags"]["metrics"]
        skin_m = prof_cmp["skinning_lw"]["metrics"]

        self.assertGreater(float(fast_m["avg_xp_per_hour"]), base_xp_hr, "fast_xp optimizer should outperform naive_greedy XP/hr")
        self.assertGreater(float(bal_m["avg_xp_per_hour"]), base_xp_hr, "balanced optimizer should outperform naive_greedy XP/hr")
        self.assertGreater(float(skin_m["avg_xp_per_hour"]), base_xp_hr, "skinning_lw optimizer should outperform naive_greedy XP/hr")
        self.assertLess(float(bal_m["travel_share_pct"]), base_travel_share, "balanced optimizer should cut travel share vs naive_greedy")

        # skinning_lw profile differentiation: significantly more LW/Skinning & beast-skinning yield than fast_xp/balanced
        skin_lw_total = int(skin_m.get("lw_quests_completed", 0)) + int(skin_m.get("skinning_quests_completed", 0))
        fast_lw_total = int(fast_m.get("lw_quests_completed", 0)) + int(fast_m.get("skinning_quests_completed", 0))
        bal_lw_total = int(bal_m.get("lw_quests_completed", 0)) + int(bal_m.get("skinning_quests_completed", 0))
        self.assertGreater(int(skin_m.get("lw_quests_completed", 0)), int(fast_m.get("lw_quests_completed", 0)),
            "skinning_lw should complete more LW quests than fast_xp")
        self.assertGreater(skin_lw_total, bal_lw_total, "skinning_lw should complete more LW+Skinning quests than balanced")
        self.assertGreater(skin_lw_total, fast_lw_total, "skinning_lw should complete more LW+Skinning quests than fast_xp")
        self.assertGreater(int(skin_m.get("beast_quests_completed", 0)), int(fast_m.get("beast_quests_completed", 0)),
            "skinning_lw should complete more beast-skinning quests than fast_xp")
        self.assertGreater(float(skin_m.get("total_skinning_score", 0.0)), float(fast_m.get("total_skinning_score", 0.0)),
            "skinning_lw should achieve higher total_skinning_score than fast_xp")

        dng_count = len(dng_m.get("dungeons_scheduled", []))
        fast_dng_count = len(fast_m.get("dungeons_scheduled", []))
        self.assertGreaterEqual(dng_count, 3, "dungeon_focus should schedule >= 3 dungeons")
        self.assertGreaterEqual(dng_count, fast_dng_count, "dungeon_focus should schedule >= dungeons than fast_xp")

        tb_bags = int(tb_m.get("bags_earned", 0))
        tb_gold = int(tb_m.get("total_gold_copper", 0))
        tb_gear = int(tb_m.get("rogue_upgrades", 0))
        fast_bags = int(fast_m.get("bags_earned", 0))
        fast_gold = int(fast_m.get("total_gold_copper", 0))
        fast_gear = int(fast_m.get("rogue_upgrades", 0))
        self.assertTrue(
            (tb_bags > fast_bags or (tb_bags == fast_bags and tb_gold + tb_gear * 5000 > fast_gold + fast_gear * 5000)),
            f"treasure_bags (bags={tb_bags}, gold={tb_gold}, rogue={tb_gear}) should beat fast_xp (bags={fast_bags}, gold={fast_gold}, rogue={fast_gear})",
        )

        # Preserved Lv 24->60 Wetlands benchmarks & 8 multi-race/class 1->60 archetypes
        l24 = metrics.get("level24Benchmarks", {})
        self.assertIn("profileComparison", l24, "sim_metrics.json missing level24Benchmarks.profileComparison")
        for p in PROFILES:
            self.assertIn(p, l24["profileComparison"], f"level24Benchmarks missing {p}")
            self.assertEqual(int(l24["profileComparison"][p]["metrics"]["end_level"]), 60)

        archetypes = metrics.get("archetypes", {})
        for req_arch in (
            "Alliance_Gnome_ROGUE_1",
            "Alliance_Gnome_ROGUE_24",
            "Alliance_Human_PALADIN_1",
            "Alliance_NightElf_HUNTER_1",
            "Horde_Orc_WARRIOR_1",
            "Horde_Scourge_MAGE_1",
            "Alliance_HighOrderSkyborne_ROGUE_1",
            "Horde_WindshaperSkyborne_ROGUE_1",
        ):
            self.assertIn(req_arch, archetypes, f"sim_metrics.json missing archetype {req_arch}")
            am = archetypes[req_arch]
            self.assertEqual(int(am["endLevel"]), 60, f"Archetype {req_arch} failed to reach Lv 60")
            for vkey in ("prereqViolations", "minLevelViolations", "redViolations", "eligibilityViolations"):
                self.assertEqual(int(am[vkey]), 0, f"Archetype {req_arch} had {vkey}={am[vkey]}")

    def test_live_multiclass_multirace_1_to_60_simulations(self) -> None:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from sim.dag import QuestDAG  # noqa: E402
        from sim.dungeon import DungeonOptimizer  # noqa: E402
        from sim.loader import DatasetLoader  # noqa: E402
        from sim.models import CharacterState  # noqa: E402
        from sim.optimizer import HubClusterLookaheadStrategy  # noqa: E402
        from sim.scoring import MultiObjectiveScorer, PROFILES as SIM_PROFILES  # noqa: E402
        from sim.simulator import RouteSimulator  # noqa: E402
        from sim.travel import TravelModel  # noqa: E402
        from sim.xp import XPModel  # noqa: E402

        ds = DatasetLoader(DATA_DIR).load()
        dag = QuestDAG(ds)
        xp_model = XPModel(ds.xp_curve)
        travel = TravelModel(ds)
        dng_opt = DungeonOptimizer(ds, dag, xp_model, travel)
        scorer = MultiObjectiveScorer(ds, dag, xp_model, travel, dng_opt)
        simulator = RouteSimulator(ds, dag, xp_model, travel, dng_opt, scorer)
        opt = HubClusterLookaheadStrategy(ds, dag, xp_model, travel, dng_opt, scorer, simulator)
        cfg = SIM_PROFILES["balanced"]

        combos = [
            ("Alliance", "Gnome", "ROGUE", 1),
            ("Alliance", "Human", "PALADIN", 12),
            ("Horde", "Orc", "WARRIOR", 14),
            ("Horde", "Scourge", "MAGE", 85),
        ]
        for faction, race, cclass, start_zone in combos:
            init_state = CharacterState.for_character(ds, faction=faction, race=race, char_class=cclass, level=1)
            self.assertEqual(init_state.zone_id, start_zone)
            final_state, steps, met = opt.build_route(init_state, cfg, target_level=60)
            self.assertEqual(final_state.level, 60, f"Live {faction} {race} {cclass} stopped at Lv {final_state.level}")
            self.assertEqual(met.prereq_violations, 0)
            self.assertEqual(met.min_level_violations, 0)
            step_dicts = [
                {
                    "action": s.action,
                    "title": s.title,
                    "expectedLevel": s.expected_level,
                    "zoneId": s.zone_id,
                    "questId": s.quest_id,
                    "questIds": list(s.quest_ids),
                }
                for s in steps
            ]
            self._validate_route_steps(
                step_dicts,
                label=f"live:{faction}:{race}:{cclass}",
                faction=faction,
                race_bit=ds.races[race].bitmask,
                class_bit=ds.classes[cclass].bitmask,
                start_level=1,
                start_zones={start_zone},
                min_end_level=60,
                require_wetlands_visit=(faction == "Alliance" and race == "Gnome"),
            )

        # Also validate live Lv 24 Alliance Gnome Rogue in Wetlands (24->60) respects +2 ceiling (deferring 464 until Lv26+)
        lv24_state = CharacterState.for_character(
            ds, faction="Alliance", race="Gnome", char_class="ROGUE", level=24, zone_id=11, max_level_offset=2
        )
        final24, steps24, met24 = opt.build_route(lv24_state, cfg, target_level=60)
        self.assertEqual(final24.level, 60, f"Live 24->60 Wetlands run stopped at Lv {final24.level}")
        self.assertEqual(met24.prereq_violations, 0)
        self.assertEqual(met24.min_level_violations, 0)
        self._validate_route_steps(
            [
                {
                    "action": s.action,
                    "title": s.title,
                    "expectedLevel": s.expected_level,
                    "zoneId": s.zone_id,
                    "questId": s.quest_id,
                    "questIds": list(s.quest_ids),
                }
                for s in steps24
            ],
            label="live24:Alliance:Gnome:ROGUE",
            faction="Alliance",
            race_bit=ds.races["Gnome"].bitmask,
            class_bit=ds.classes["ROGUE"].bitmask,
            start_level=24,
            start_zones={11},
            min_end_level=60,
            require_wetlands_visit=True,
        )

    def test_sim_python_modules_import_and_xp_decay(self) -> None:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from sim.dag import QuestDAG  # noqa: E402
        from sim.loader import DatasetLoader  # noqa: E402
        from sim.models import CharacterState  # noqa: E402
        from sim.travel import TravelModel  # noqa: E402
        from sim.xp import XPModel  # noqa: E402

        xp_model = XPModel()
        self.assertEqual(xp_model.xp_for_level(24), 31700)
        self.assertEqual(xp_model.quest_color(24, 24), "yellow")
        self.assertEqual(xp_model.quest_color(24, 45), "grey")
        self.assertEqual(xp_model.adjusted_quest_xp(1000, 24, 24), 1000)
        self.assertLess(xp_model.adjusted_quest_xp(1000, 24, 45), 500)
        self.assertEqual(xp_model.adjusted_quest_xp(1000, 24, 60), 0)

        dataset = DatasetLoader(DATA_DIR).load()
        dag = QuestDAG(dataset)
        state = CharacterState()
        avail = dag.available_quests(state)
        self.assertGreater(len(avail), 15, "Expected >15 initial available quests at Lv24")
        # Quest 631 has minLevel 28 -> must not be acceptable at Lv24
        if 631 in dataset.quests:
            self.assertFalse(dag.can_accept(dataset.quests[631], state))
        # Quest 2989 requires prereq 2988 -> must not be acceptable before 2988 completes
        if 2989 in dataset.quests:
            high_state = CharacterState(level=45)
            self.assertFalse(dag.can_accept(dataset.quests[2989], high_state))

        travel = TravelModel(dataset)
        self.assertEqual(travel.resolve_zone_id(717), 1519)
        plan_sw = travel.shortest_zone_path(11, 1519)
        plan_ds = travel.shortest_zone_path(11, 148)
        self.assertGreater(plan_sw.total_seconds, 0.0)
        self.assertGreater(plan_ds.total_seconds, 0.0)
        self.assertLess(plan_sw.total_seconds, 3600.0)
        self.assertLess(plan_ds.total_seconds, 3600.0)

    def test_nav_graph_simulator_routing_and_lua_csr_export(self) -> None:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from sim.loader import DatasetLoader  # noqa: E402
        from sim.nav import NavGraph  # noqa: E402

        ds = DatasetLoader(DATA_DIR).load()
        nav: NavGraph | None = getattr(ds, "nav_graph", None)
        self.assertIsNotNone(nav, "DatasetLoader must attach NavGraph when data/nav_graph.json exists")
        assert nav is not None

        # 1. Stonetalon mountain ridge detours (33,76) <-> (56,76) cleanly (> 200s vs naive chord)
        clear, _ = nav.line_is_clear(406, 33.0, 76.0, 56.0, 76.0)
        self.assertFalse(clear, "Stonetalon mountain wall must block direct Charred Vale <-> Fold chord")
        cv_res = nav.shortest_path(406, 33.0, 76.0, 406, 56.0, 76.0, player_level=24, faction="Horde")
        self.assertIsNotNone(cv_res)
        assert cv_res is not None
        naive_sec = nav.chord_seconds(406, 33.0, 76.0, 56.0, 76.0, player_level=24)
        self.assertGreater(cv_res.total_seconds, max(200.0, naive_sec * 1.5))

        # Mounted scaling (Lv 40+) speeds up ground traversal without shrinking boat/flight fixed segments
        cv_mounted = nav.shortest_path(406, 33.0, 76.0, 406, 56.0, 76.0, player_level=40, faction="Horde")
        self.assertIsNotNone(cv_mounted)
        assert cv_mounted is not None
        self.assertLess(cv_mounted.total_seconds, cv_res.total_seconds * 0.72)

        # 2. Ground gates / tunnels (Talondeep Path, Dun Algaz tunnels, Thandol Span bridge)
        ash_st = nav.shortest_path(331, 36.0, 50.0, 406, 46.0, 60.0, player_level=24, faction="Alliance", discovered_flights=set())
        self.assertIsNotNone(ash_st)
        assert ash_st is not None
        ash_text = ash_st.note + " " + " ".join(str(st[4]) for st in ash_st.steps)
        self.assertIn("Talondeep", ash_text)
        self.assertIsNotNone(ash_st.next_hop)

        wet_loch = nav.shortest_path(11, 53.0, 72.0, 38, 26.0, 18.0, player_level=24, faction="Alliance", discovered_flights=set())
        self.assertIsNotNone(wet_loch)
        assert wet_loch is not None
        wet_text = wet_loch.note + " " + " ".join(str(st[4]) for st in wet_loch.steps)
        self.assertIn("Dun Algaz", wet_text)

        # 3. O(1) Hearthstone shortcut & undiscovered FP +45s penalty
        hearth_res = nav.shortest_path(440, 52.0, 28.0, 11, 9.5, 59.7, player_level=24, faction="Alliance", allow_hearth=True, hearth_zone_id=11)
        self.assertIsNotNone(hearth_res)
        assert hearth_res is not None
        self.assertTrue(hearth_res.used_hearth)
        self.assertLess(hearth_res.total_seconds, 90.0, f"Hearthstone to Wetlands should be fast (<90s), got {hearth_res.total_seconds}")

        fp_known = nav.shortest_path(141, 55.0, 60.0, 148, 37.0, 44.0, player_level=24, faction="Alliance", discovered_flights={148})
        fp_unknown = nav.shortest_path(141, 55.0, 60.0, 148, 37.0, 44.0, player_level=24, faction="Alliance", discovered_flights=set())
        self.assertIsNotNone(fp_known)
        self.assertIsNotNone(fp_unknown)
        assert fp_known is not None and fp_unknown is not None
        self.assertAlmostEqual(fp_unknown.total_seconds - fp_known.total_seconds, 45.0, places=2)

        # 4. Frontier nearest_eligible_quests matches exact per-quest shortest_path maximum
        pool = [
            q for q in ds.quests.values()
            if q.eligible_alliance_gnome_rogue and q.starter_coords and q.zone_id in (11, 38, 45, 10, 40)
        ][:40]
        self.assertGreater(len(pool), 15)
        max_xp = max(float(q.xp) for q in pool)
        score_fn = lambda q, d: float(q.xp) - 6.0 * d
        ub_fn = lambda min_d: max_xp - 6.0 * min_d
        picked_q, picked_score = nav.nearest_eligible_quests(
            11, 56.4, 40.4,
            pool, score_fn, ub_fn,
            lambda q: q.starter_coords,
            role="s", player_level=24, faction="Alliance", allow_hearth=False,
        )
        best_bf_q, best_bf_score = None, -1e9
        for q in pool:
            c = q.starter_coords
            res = nav.shortest_path(
                11, 56.4, 40.4, c.zone_id, c.x, c.y,
                player_level=24, faction="Alliance", allow_hearth=False,
                dst_anchors_override=nav.quest_role_anchors(q, "s", c),
            )
            if res:
                sc = score_fn(q, res.total_seconds)
                if sc > best_bf_score:
                    best_bf_score = sc
                    best_bf_q = q
        self.assertIsNotNone(picked_q)
        self.assertIsNotNone(best_bf_q)
        assert picked_q is not None and best_bf_q is not None
        self.assertEqual(picked_q.quest_id, best_bf_q.quest_id)
        self.assertAlmostEqual(picked_score, best_bf_score, places=2)

        # 5. Direct LuaJIT load & CSR structural validation of addon/Data/NavData.lua
        nav_lua = PROJECT_ROOT / "addon" / "Data" / "NavData.lua"
        self.assertTrue(nav_lua.exists(), f"Missing {nav_lua}")
        lua_check = f"""
local t0 = os.clock()
local chunk, err = loadfile({str(nav_lua)!r})
assert(chunk, "LuaJIT syntax error in NavData.lua: " .. tostring(err))
local ns = {{}}
chunk("ForeverQuestPath", ns)
local loadMs = (os.clock() - t0) * 1000.0
local nd = assert(ns.NavData, "ns.NavData missing")
assert(nd.version == 1 and nd.cellGrid == 20, "NavData version/cellGrid mismatch")
local N, E = nd.nodeCount, nd.edgeCount
assert(N > 500 and N <= 1500 and E > 1000 and E <= 6000)
assert(#nd.nodeZone == N and #nd.nodeX == N and #nd.nodeY == N and #nd.nodeKind == N and #nd.nodeComp == N)
assert(#nd.fHead == N + 1 and #nd.rHead == N + 1)
assert(#nd.fTo == E and #nd.eCost == E and #nd.eMode == E and #nd.eGround == E and #nd.eFaction == E)
assert(#nd.rFrom == E and #nd.rFwdIdx == E)
for u = 1, N do
    for re = nd.rHead[u], nd.rHead[u + 1] - 1 do
        local fwd = nd.rFwdIdx[re]
        assert(fwd >= 1 and fwd <= E, "Bad rFwdIdx")
        assert(nd.fTo[fwd] == u, "Reverse CSR mismatch: fTo[fwd] ~= u")
    end
end
local zCount, qCount, hCount = 0, 0, 0
for _ in pairs(nd.zoneCells) do zCount = zCount + 1 end
for _ in pairs(nd.questAnchors) do qCount = qCount + 1 end
for _ in pairs(nd.hearthNodes) do hCount = hCount + 1 end
assert(zCount == 50 and hCount == 50 and qCount >= 5500, string.format("Counts mismatch z=%d h=%d q=%d", zCount, hCount, qCount))
assert(loadMs < 150.0, string.format("NavData.lua load took %.2fms (>=150ms)", loadMs))
print(string.format("NAVDATA_CSR_OK N=%d E=%d loadMs=%.2fms quests=%d", N, E, loadMs, qCount))
"""
        proc = subprocess.run(["luajit", "-e", lua_check], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"NavData.lua CSR check failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
        self.assertIn("NAVDATA_CSR_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
