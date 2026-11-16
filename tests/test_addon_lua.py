"""Independent Lua/API verification tests for ForeverQuestPath addon and WoW deployment."""

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path("/Users/sebastianmakinson/Documents/ForeverQuestPath")
ADDON_SRC = PROJECT_ROOT / "addon"
ADDON_DST = Path("/Applications/World of Warcraft/_forever_/Interface/AddOns/ForeverQuestPath")
TOC_PATH = ADDON_SRC / "ForeverQuestPath.toc"
WOW_MOCK_LUA = PROJECT_ROOT / "tests" / "wow_mock.lua"


def _run_luajit(script: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["luajit", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


# Rules for TestForeverQuestPathAddon:
# - Validates ForeverQuestPath.toc metadata and Lua 5.1 syntax across every .toc-listed file.
# - Executes the full addon under tests/wow_mock.lua simulating WoW Forever 16001.
# - Tests character progression across Lv1-60 races/classes/factions: Lv1 Human Paladin (Elwynn),
#   Lv12 Night Elf Druid (Darkshore + class quests), Lv1 Orc Warrior (Durotar + Horde routes),
#   Lv1 Scourge/Undead Mage (Tirisfal), HighOrderSkyborne (2^32) vs WindshaperSkyborne (2^33)
#   on Zephras Isle, and Lv24 Alliance Gnome Rogue Wetlands catch-up with completed flags.
# - Tests live event advancement, 5-profile switching (including skinning_lw), 5-axis weight sliders,
#   configurable Max Quest Lv offset (-3..+5, default +2) slider & /fqp maxlevel command, deferral
#   & automatic rewind/unlock of quest 464 (War Banners Lv28) on slider raise or PLAYER_LEVEL_UP
#   (Lv26), dungeon chain data, runtime eligibility guards (minLevel, prereqIds, questLevel >
#   playerLevel + maxLevelOffset, race/class/faction >31-bit Skyborne masks via HasBitMask,
#   requiredSkill / wrong_profession / skill_rank_too_low, optional QuestieDB.IsDoable /
#   QueryQuestSingle via pcall, and blocked-step diagnostics), viewport pooling & <20ms cached tab-switch
#   performance (0 CreateFrame calls on switch, bounded IsQuestFlaggedCompleted & QuestieDB checks),
#   Skinning/LW auto-detection, /fqp char & raceBtn/classBtn/autoCharBtn overrides, <Class> BiS labels/rewards,
# - Tests completed-but-not-turned-in quest detection (isComplete=1 / C_QuestLog.ReadyForTurnIn /
#   IsComplete), nearby/same-hub immediate batching (immune to maxLevelOffset=-2), QuestieDB NPC
#   turn-in resolution + pcall guard, deferred anti-detour zone/hub alignment (e.g. Stormwind 1519
#   from eastern Wetlands), unvisited-zone single TRAVEL + grouped TURN_IN batching (Loch Modan 38),
#   Scorer.GetDynamicSteps ready turn-in prioritization, [Ready Turn-In] / [READY] HUD & Window
#   display, near-realtime C_Timer.NewTicker (1.5s) GPS position polling, <5u move & >8u hysteresis
#   intra-zone walking reorder (Wetlands), UnitOnTaxi flyover & unengaged capital-zone guards,
#   Duskwood/Redridge off-route Friend-Meetup chain pivot (zero cross-zone starter leaks, nextQuestIds
#   follow-ups, Skip/StepBack/MarkComplete gpsHistory), [Friend Chain]/[GPS Reorder] HUD & Window
#   sync, "Auto-Reorder (GPS)" checkbox & /fqp gps|/fqp reorder toggles, 26-tick <20ms / 0 CreateFrame
#   performance, proactive full-chain step expansion ahead of quest turn-in (Wetlands 279->281->284
#   ->285->286->98189, 276->277->275 with MaxLv gating, Duskwood 56->57 excluding Lv30 58, Redridge 20)
#   across GetUpcomingSteps / Window routeRows (GPS on & off) / Scorer.GetDynamicSteps, [Chain X/Y]
#   progress badges & TurnIn prediction stability, Phase 7 terrain/gate-aware Lua Nav routing (CSR load,
#   Stonetalon internal detour + mounted scaling, Dun Algaz & Talondeep tunnel gate hops, Hearthstone
#   shortcut vs cooldown, TAXIMAP_OPENED FP penalty removal, multi-hop Waypoint pinning & zone-crossing
#   handoff + /fqp gate toggle, non-mutating HUD ETA/travelNote, FindNearestEligibleQuests, zero-GC
#   bidir Dijkstra <0.8ms & 30 GPS_MOVED ticks <2.0ms/tick, nil NavData fallback), Phase 8 idle/
#   stationary CPU gating (0 Nav solves / 0 STATE_UPDATED / <0.05ms per idle tick, unchanged
#   QUEST_LOG_UPDATE / ZONE_CHANGED_INDOORS / ZONE_CHANGED / SKILL_LINES_CHANGED / CHAT_MSG_SKILL /
#   repeat TAXIMAP_OPENED filtering, indoor nil flicker & micro-jitter suppression, >=160yd large-move
#   & cooldown-gated cell-crossing triggers, real objective/skill/taxi updates, and /fqp gps stats),
#   Phase 9 loot/skinning & partial-progress CPU gating (100 unchanged QUEST_LOG_UPDATE bursts w/
#   20 active & 2000 completed quests -> 0 GetAllCompletedQuestIDs/GetQuestsCompleted/GetQuestObjectives
#   & 0 Nav/STATE_UPDATED, partial 1/10..9/10 drops <0.1ms/loot w/ 0 generation/Nav churn vs 10/10
#   ready TURN_IN flip, trailing 0.20s Frame-0 burst coalescing keeping concurrent quest+skill categories,
#   and 1-scan stale QUEST_ACCEPTED expiry), Phase 10 dead/ghost quest-location gating (PLAYER_DEAD +
#   Release Spirit PLAYER_ALIVE while ghost + 300+yd cross-zone graveyard ticks/events/TAXIMAP_OPENED/
#   /fqp gps & Waypoint skipping -> 0 location/Nav/step updates & frozen quest location/order, /reload
#   while already ghost suppressing catch-up/TRAVEL/reorder, corpse rez 0 Dijkstra recomputes, and
#   Spirit-Healer rez immediate resume + deadLocationSkipped stats), deep requirements audit (/reload
#   while dead in dynamic/custom mode via deadBootLocation, MaxLevelOffset custom -1 preservation
#   across Allow Red +5 checkbox toggles, off-DB QuestieDB ready turn-ins in dynamic mode, and
#   deep requirements audit (/reload while dead in dynamic/custom mode via deadBootLocation,
#   MaxLevelOffset custom -1 preservation across Allow Red +5 checkbox toggles, off-DB QuestieDB
#   ready turn-ins in dynamic mode, and 1500-completed-quest allocation & latency budgets),
#   skipEliteSolo option (enabled by default, open-world elite deferral in deferredSteps +
#   HasEliteDeferredPrereq, excluding dungeons and ready elite turn-ins, live group/raid roster
#   boolean-change gating via GROUP_ROSTER_UPDATE / PARTY_MEMBERS_CHANGED / RAID_ROSTER_UPDATE,
#   /fqp elite command, and synchronized Route + Settings "Skip Elite (Solo)" checkboxes),
#   skipDungeons option (enabled by default, dungeon instance quest deferral in deferredSteps +
#   HasEliteDeferredPrereq, preserving completed ready turn-ins & outdoor unlock/attunement chains
#   plus Dungeons & BiS tab visibility, Nav/Scorer/GPS/Window filtering, HUD [Dungeon] badge and
#   'No Dung'/'Dung On' mode indicator, /fqp skipdungeons & /fqp dungeons skip|allow|toggle commands,
#   synchronized Route + Settings "Skip Dungeons" checkboxes, and cursor rewind to do all
#   deferred dungeon quests at once when turned off), quest hover tooltips (QuestieDB
#   objectivesText, GetQuestLogQuestText narrative, live C_QuestLog.GetQuestObjectives,
#   static objective coords, DUNGEON_RUN batch details, rewards, and blocked reasons across HUD
#   and Route/Dungeons/Rewards/Diag tabs), click-to-open Quest Log (modern QuestMapFrame_OpenToQuestDetails,
#   standalone QuestLogFrame + QuestLog_SetSelection, unaccepted quest-giver pin fallback,
#   Shift-click chat links, Right-Click waypoint/jump, InCombatLockdown, and Forever controller
#   InputUtil.IsGamepadUIEnabled guards with 0 frame/region allocations), optimal-path available
#   unaccepted quest discovery (unrouted side quests like Wetlands Q463 & Redridge Q122/Q124,
#   behind-gap route quests like Q288/Q472/Q299, same-hub pickup priority over distant newly
#   accepted objectives like Q98197, corridor pickups like Q469 along active travel, Skip/StepBack,
#   MaxLv -3 gating, Dynamic Re-Scorer nearby ACCEPT momentum, and 0 frame/region allocations),
#   AddonCompartment, waypoints, and sync.
class TestForeverQuestPathAddon(unittest.TestCase):
    """Lua 5.1 / WoW Forever 16001 harness tests for ForeverQuestPath addon."""

    def test_toc_metadata_and_lua_syntax(self) -> None:
        self.assertTrue(TOC_PATH.exists(), f"Missing TOC file: {TOC_PATH}")
        toc_text = TOC_PATH.read_text(encoding="utf-8")
        self.assertIn("## Interface: 16001", toc_text)
        self.assertIn("ForeverQuestPathDB", toc_text)
        self.assertIn("ForeverQuestPathCharDB", toc_text)
        for dep in ("TomTom", "OpenMap", "Questie", "ForeverLevelingBiS"):
            self.assertIn(dep, toc_text, f".toc OptionalDeps should include {dep}")

        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
local meta, files = Mock.ParseTOC({str(TOC_PATH)!r})
assert(#files >= 4, "Expected at least 4 Lua files in .toc")
for _, rel in ipairs(files) do
    local p = {str(ADDON_SRC)!r} .. "/" .. rel
    local chunk, err = loadfile(p)
    assert(chunk, "Syntax error in " .. p .. ": " .. tostring(err))
end
print("SYNTAX_OK:" .. #files)
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"TOC/syntax check failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("SYNTAX_OK:", out)

    def test_full_addon_load_and_progression_simulation(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})

-- Seed Lv24 Alliance Gnome Rogue in Wetlands with a couple early quests already done
Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.108,
    y = 0.596,
}})

local addonName, ns, meta, files = Mock.LoadAddonTOC({str(TOC_PATH)!r})
assert(#Mock.errors == 0, "Unexpected errors during load: " .. table.concat(Mock.errors, "; "))

Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
assert(type(_G.ForeverQuestPathDB) == "table", "ForeverQuestPathDB not initialized")
assert(type(_G.ForeverQuestPathCharDB) == "table", "ForeverQuestPathCharDB not initialized")

-- Verify initial step & Wetlands map->zone resolution for Lv24 Wetlands
assert(ns.State.ResolveZoneId(1437, "Wetlands") == 11,
    "Expected UiMapID 1437 (Wetlands) to resolve to zoneId 11, got " .. tostring(ns.State.ResolveZoneId(1437, "Wetlands")))
ns.State.Refresh()
local step1 = ns.State.GetCurrentStep()
assert(step1, "Expected an initial current step at Lv24 in Wetlands")

-- Regression checks for quests 98230/464 and live Lv24 eligibility guards (tracking simulated lookahead turn-ins)
local function assertLv24StepSafe(s, contextLabel, simDone)
    if not s then return end
    local act = s.action or "ACCEPT"
    local qid = s.questId
    if qid and (act == "ACCEPT" or (act == "DUNGEON_PREP" and s.prepMode == "accept")) then
        assert(qid ~= 98230, contextLabel .. " recommended quest 98230 (Lv 29) at Lv24!")
        assert(qid ~= 464, contextLabel .. " recommended quest 464 (War Banners Lv 28) at Lv24 under default +2!")
        local q = ns.GetData().quests[qid]
        if q then
            assert(24 >= (q.minLevel or 1), contextLabel .. " recommended quest " .. qid .. " below minLevel")
            if q.questLevel then
                assert(q.questLevel <= 26, contextLabel .. " recommended quest " .. qid .. " with questLevel > 26 (24 + 2 default) at Lv24")
            end
            assert(ns.State.ArePrereqsMet(qid, simDone), contextLabel .. " recommended quest " .. qid .. " with incomplete prereqs")
        end
    end
    if simDone and (act == "TURN_IN" or act == "DUNGEON_RUN") then
        if qid then simDone[qid] = true end
        for _, sqid in ipairs(s.questIds or {{}}) do
            simDone[sqid] = true
        end
    end
end

assertLv24StepSafe(step1, "Initial GetCurrentStep", nil)
local upcomingInit = ns.State.GetUpcomingSteps(10)
local upSimDone = {{}}
for _, us in ipairs(upcomingInit) do
    assertLv24StepSafe(us, "Initial GetUpcomingSteps", upSimDone)
end
local dynInit = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 12)
assert(#dynInit > 0, "Expected dynamic steps at Lv24")
local dynSimDone = {{}}
for _, ds in ipairs(dynInit) do
    assertLv24StepSafe(ds, "Initial Scorer.GetDynamicSteps", dynSimDone)
end

-- Unit-test State.GetQuestBlockReason, Scorer filtering, prereq resolution, and QuestieDB pcall guard
local dataRef = ns.GetData()
local lv24Player = {{ level = 24, faction = "Alliance", raceId = 7, classId = 4, zoneId = 11, zoneName = "Wetlands", x = 10.6, y = 55.6 }}
local block98230 = ns.State.GetQuestBlockReason(98230, "ACCEPT", lv24Player, nil, nil, {{ consultQuestie = false }})
assert(block98230 ~= nil, "Expected quest 98230 to be blocked at Lv24 by State.GetQuestBlockReason")

dataRef.quests[999901] = {{
    id = 999901, name = "Synthetic TooHighMin", side = 1, factionMask = 1,
    minLevel = 25, questLevel = 25, xp = 999999, zoneId = 11, zoneName = "Wetlands",
    starter = {{ zoneId = 11, x = 10.6, y = 55.6 }}, turnin = {{ zoneId = 11, x = 10.6, y = 55.6 }},
    prereqIds = {{}},
}}
dataRef.quests[999902] = {{
    id = 999902, name = "Synthetic RedNonDungeon", side = 1, factionMask = 1,
    minLevel = 22, questLevel = 29, isDungeon = false, xp = 999999, zoneId = 11, zoneName = "Wetlands",
    starter = {{ zoneId = 11, x = 10.6, y = 55.6 }}, turnin = {{ zoneId = 11, x = 10.6, y = 55.6 }},
    prereqIds = {{}},
}}
dataRef.quests[999904] = {{
    id = 999904, name = "Synthetic PreReqRoot", side = 1, factionMask = 1,
    minLevel = 22, questLevel = 23, xp = 1800, zoneId = 11, zoneName = "Wetlands",
    starter = {{ zoneId = 11, x = 10.7, y = 55.7 }}, turnin = {{ zoneId = 11, x = 10.7, y = 55.7 }},
    prereqIds = {{}},
}}
dataRef.quests[999903] = {{
    id = 999903, name = "Synthetic LockedFollowup", side = 1, factionMask = 1,
    minLevel = 22, questLevel = 24, xp = 999999, zoneId = 11, zoneName = "Wetlands",
    starter = {{ zoneId = 11, x = 10.8, y = 55.8 }}, turnin = {{ zoneId = 11, x = 10.8, y = 55.8 }},
    prereqIds = {{ 999904 }},
}}

assert(ns.State.GetQuestBlockReason(999901, "ACCEPT", lv24Player, nil, nil, {{ consultQuestie = false }}) == "min_level",
    "Expected min_level block when minLevel > playerLevel")
assert(ns.State.GetQuestBlockReason(999902, "ACCEPT", lv24Player, nil, nil, {{ consultQuestie = false }}) == "quest_level_too_high",
    "Expected quest_level_too_high block for non-dungeon questLevel > playerLevel + 4")
assert(ns.State.GetQuestBlockReason(999903, "ACCEPT", lv24Player, nil, nil, {{ consultQuestie = false }}) == "missing_prereq",
    "Expected missing_prereq block for uncompleted prereqIds")

local dynGuard = ns.Scorer.GetDynamicSteps(lv24Player, {{ xp = 100, travel = 0, dungeon = 0, reward = 0 }}, {{ consultQuestie = false }}, 5)
for _, ds in ipairs(dynGuard) do
    assert(ds.questId ~= 999901 and ds.questId ~= 999902 and ds.questId ~= 999903 and ds.questId ~= 98230,
        "Scorer.GetDynamicSteps picked blocked quest " .. tostring(ds.questId))
end

-- Test route cursor auto-skip on 98230 and actionable prereq insertion for 999903 -> 999904
dataRef.profiles.__test_guard_profile = {{
    name = "TestGuard",
    steps = {{
        {{ index = 1, action = "ACCEPT", questId = 98230, title = "This Land Was Their Land", zoneId = 11, zoneName = "Wetlands" }},
        {{ index = 2, action = "ACCEPT", questId = 999903, title = "Synthetic LockedFollowup", zoneId = 11, zoneName = "Wetlands" }},
    }},
}}
ns.State.SetProfile("__test_guard_profile")
local guardStep, guardMeta = ns.State.GetCurrentStep()
assert(guardStep and guardStep.questId == 999904 and guardMeta and guardMeta.resolvedPrereq == true,
    "Expected GetCurrentStep to skip 98230 and resolve actionable prereq 999904, got " .. tostring(guardStep and guardStep.questId))
local blockedLog = ns.State.GetBlockedSteps()
assert(#blockedLog >= 1 and blockedLog[1].questId == 98230,
    "Expected blocked-step diagnostic entry for skipped quest 98230")

-- If prereq root itself requires Lv 30, locked chain step must be skipped cleanly
dataRef.quests[999904].minLevel = 30
ns.State.ResetProgress()
local guardBlockedAll, guardMetaAll = ns.State.GetCurrentStep()
assert((not guardBlockedAll) or (guardBlockedAll.questId ~= 98230 and guardBlockedAll.questId ~= 999903 and guardBlockedAll.questId ~= 999904),
    "Expected both locked followup and Lv30 prereq to be skipped cleanly")

-- Test optional QuestieDB.IsDoable / QueryQuestSingle guard and pcall safety against crashing QuestieDB
dataRef.quests[999904].minLevel = 22
_G.QuestieDB = {{
    QueryQuestSingle = function(qid, key)
        if qid == 999904 and key == "name" then return "Synthetic PreReqRoot" end
        return nil
    end,
    IsDoable = function(qid)
        return qid ~= 999904
    end,
}}
assert(ns.State.GetQuestBlockReason(999904, "ACCEPT", lv24Player, nil, nil, {{ consultQuestie = true }}) == "questie_not_doable",
    "Expected questie_not_doable when QuestieDB.IsDoable returns false")
dataRef.quests[999904].xp = 999999
local dynQuestie = ns.Scorer.GetDynamicSteps(lv24Player, {{ xp = 100, travel = 0, dungeon = 0, reward = 0 }}, {{ consultQuestie = true }}, 5)
assert(dynQuestie[1] and dynQuestie[1].questId ~= 999904, "Expected Scorer current step (step 1) to filter quest rejected by QuestieDB.IsDoable")
local rankedStep1 = ns.Scorer.RankAvailableCandidates({{
    player = lv24Player, level = 24, xpPct = 0, zoneId = 11, zoneName = "Wetlands", x = 10.6, y = 55.6,
    completed = {{}}, active = {{}}, stepCounter = 1,
}}, {{ xp = 100, travel = 0, dungeon = 0, reward = 0 }}, {{ consultQuestie = true }}, dataRef)
for _, cand in ipairs(rankedStep1) do
    assert(cand.quest.id ~= 999904, "Expected step-1 RankAvailableCandidates to exclude quest rejected by QuestieDB.IsDoable")
end
_G.QuestieDB.IsDoable = function() error("simulated QuestieDB internal error") end
local safeCode = ns.State.GetQuestBlockReason(999904, "ACCEPT", lv24Player, nil, nil, {{ consultQuestie = true }})
assert(safeCode == nil, "Expected throwing QuestieDB.IsDoable to be caught cleanly by pcall")

-- Restore clean addon state before standard progression checks
_G.QuestieDB = nil
dataRef.quests[999901] = nil
dataRef.quests[999902] = nil
dataRef.quests[999903] = nil
dataRef.quests[999904] = nil
dataRef.profiles.__test_guard_profile = nil
ns.State.SetProfile("balanced")
ns.State.ResetProgress()
ns.State.Refresh()
step1 = ns.State.GetCurrentStep()

-- Flag first step's quest(s) completed -> verify dynamic advancement
local preCompleted = {{}}
if step1.questId then
    Mock.CompleteQuest(step1.questId)
    table.insert(preCompleted, step1.questId)
end
for _, qid in ipairs(step1.questIds or {{}}) do
    Mock.CompleteQuest(qid)
    table.insert(preCompleted, qid)
end
Mock.FireEvent("QUEST_LOG_UPDATE")
local step2 = ns.State.GetCurrentStep()
assert(step2, "Expected next step after completing first step")
assert(step2.index ~= step1.index or step2.questId ~= step1.questId or step2.action ~= step1.action,
    "Step did not advance after pre-completed quest flags")

-- Simulate live accept -> complete objective -> turn in -> level up -> zone change
local targetQid = step2.questId
if targetQid then
    local qmeta = ns.GeneratedData and ns.GeneratedData.quests and ns.GeneratedData.quests[targetQid]
    local qname = (qmeta and qmeta.name) or step2.title or ("Quest " .. tostring(targetQid))
    local qlvl = (qmeta and qmeta.questLevel) or 25
    Mock.AcceptQuest(targetQid, qname, qlvl, false)
    Mock.SetQuestObjectivesComplete(targetQid, true)
    Mock.TurnInQuest(targetQid, (qmeta and qmeta.xp) or 1950, (qmeta and qmeta.goldCopper) or 1700)
end
Mock.LevelUp(25)
Mock.ChangeZone(1437, 0.380, 0.512)
local step3 = ns.State.GetCurrentStep()
assert(step3, "Expected valid step after turn-in and level up")

-- Profile switching across all 5 profiles
for _, prof in ipairs({{ "skinning_lw", "dungeon_focus", "treasure_bags", "fast_xp", "balanced" }}) do
    ns.State.SetProfile(prof)
    local cur = ns.State.GetCurrentStep()
    assert(cur, "No step returned after switching to profile " .. prof)
    assert(_G.ForeverQuestPathCharDB.activeProfile == prof, "activeProfile did not update to " .. prof)
end

-- Weight sliders & dynamic re-scoring (both Scorer API and State.SetCustomWeights + UI tabs)
if ns.Scorer and ns.Scorer.GetDynamicSteps then
    local weightsA = {{ xp = 90, travel = 5, dungeon = 5, reward = 0, skinning = 0 }}
    local weightsB = {{ xp = 5, travel = 5, dungeon = 85, reward = 5, skinning = 0 }}
    local rankedA = ns.Scorer.GetDynamicSteps(ns.State.GetSnapshot and ns.State.GetSnapshot() or nil, weightsA, 10)
    local rankedB = ns.Scorer.GetDynamicSteps(ns.State.GetSnapshot and ns.State.GetSnapshot() or nil, weightsB, 10)
    assert(type(rankedA) == "table" and #rankedA > 0, "Scorer returned no dynamic steps for XP weights")
    assert(type(rankedB) == "table" and #rankedB > 0, "Scorer returned no dynamic steps for Dungeon weights")
    if ns.State.SetCustomWeights then
        ns.State.SetCustomWeights(weightsB, true)
        local dynStep = ns.State.GetCurrentStep()
        assert(dynStep, "Expected dynamic current step after SetCustomWeights")
        ns.State.SetProfile("balanced")
    end
end

-- Exercise UI Window tabs (route, dungeons, rewards, diag) and verify Dungeon Chain UI snapshot data
if ns.Window then
    ns.Window.ShowTab("route")
    ns.Window.ShowTab("dungeons")
    ns.Window.ShowTab("rewards")
    ns.Window.ShowTab("diag")
    if ns.Window.GetSnapshot then
        local wsnap = ns.Window.GetSnapshot()
        assert(type(wsnap) == "table", "Window.GetSnapshot failed")
        assert(type(wsnap.dungeons) == "table" and #wsnap.dungeons > 0, "Expected dungeon UI entries near Lv25")
        local hasUnlockData = false
        for _, d in ipairs(wsnap.dungeons) do
            local chains = d.chains or d.unlockChains
            if chains and #chains > 0 then
                hasUnlockData = true
                break
            end
        end
        assert(hasUnlockData, "Expected at least one dungeon in Window snapshot to carry unlock chain UI data")
        assert(type(wsnap.specialRewards) == "table", "Expected specialRewards array in Window snapshot")
    end
    ns.Window.SetShown(false)
end

-- Waypoint integration with TomTom & native pin
if ns.Waypoint and ns.Waypoint.SyncToCurrentStep then
    ns.Waypoint.SyncToCurrentStep(true)
    assert(#Mock.state.tomtomWaypoints > 0 or Mock.state.userWaypoint ~= nil,
        "Expected TomTom or native user waypoint to be set")
    if Mock.state.userWaypoint then
        local px, py = Mock.state.userWaypoint.position:GetXY()
        assert(px >= 0 and px <= 1 and py >= 0 and py <= 1,
            "Native waypoint coords must be normalized 0..1")
    end
end

-- Slash command + AddonCompartment toggle
assert(Mock.RunSlash("/fqp", ""), "/fqp slash command failed")
if meta.AddonCompartmentFunc then
    assert(Mock.ClickAddonCompartment(addonName, "LeftButton"), "AddonCompartment click failed")
end

-- Manual skip / back / reset
local beforeSkip = ns.State.GetCurrentStep()
ns.State.SkipCurrentStep()
local afterSkip = ns.State.GetCurrentStep()
assert(afterSkip, "SkipCurrentStep left no step")
if ns.State.StepBack then
    ns.State.StepBack()
end
ns.State.ResetProgress()

assert(#Mock.errors == 0, "Lua error handler captured errors: " .. table.concat(Mock.errors, "; "))
print("ADDON_SIM_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Addon simulation failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("ADDON_SIM_OK", out)

    def test_native_waypoint_fallback_without_optional_addons(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = false, openmap = false }})

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

if ns.Waypoint and ns.Waypoint.SyncToCurrentStep then
    ns.Waypoint.SyncToCurrentStep(true)
    assert(Mock.state.userWaypoint ~= nil, "Expected native C_Map userWaypoint fallback without TomTom/OpenMap")
    assert(Mock.state.superTrackedUserWaypoint == true, "Expected C_SuperTrack.SetSuperTrackedUserWaypoint(true)")
    local px, py = Mock.state.userWaypoint.position:GetXY()
    assert(px >= 0 and px <= 1 and py >= 0 and py <= 1, "Waypoint coords must be 0..1")
end
assert(#Mock.errors == 0, "Unexpected errors without optional addons: " .. table.concat(Mock.errors, "; "))
print("FALLBACK_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Fallback waypoint test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("FALLBACK_OK", out)

    def test_tab_switch_performance_and_pooling(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab", realm = "Forever", level = 24,
    raceName = "Gnome", raceFile = "Gnome", raceId = 7,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1437, x = 0.108, y = 0.596,
}})

local questieQueryCalls = 0
local questieDoableCalls = 0
_G.QuestieDB = {{
    QueryQuestSingle = function(qid, key)
        questieQueryCalls = questieQueryCalls + 1
        return nil
    end,
    IsDoable = function(qid)
        questieDoableCalls = questieDoableCalls + 1
        return true
    end,
}}

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

-- Verify pooled initialization did NOT allocate thousands of per-step frames upfront
local initMetrics = Mock.GetMetrics()
assert(initMetrics.createFrameCalls <= 250,
    "Expected pooled frame initialization <= 250 CreateFrame calls, got " .. tostring(initMetrics.createFrameCalls))

-- Warm open window
ns.Window.ShowTab("route")

-- Helper to locate a button by visible label inside ForeverQuestPathWindow tree
local function findButtonByText(root, targetText, depth)
    if not root or (depth or 0) > 6 then return nil end
    if root.label and root.label.GetText and root.label:GetText() == targetText then
        return root
    end
    for _, ch in ipairs(root._children or {{}}) do
        local hit = findButtonByText(ch, targetText, (depth or 0) + 1)
        if hit then return hit end
    end
    return nil
end

local win = _G.ForeverQuestPathWindow
assert(win, "Expected ForeverQuestPathWindow global frame")

local tabs = {{ "route", "dungeons", "rewards", "diag" }}
local function runTabRotation(label, maxPerSwitchMs)
    Mock.ResetMetrics()
    questieQueryCalls = 0
    questieDoableCalls = 0
    local maxMs = 0
    for _, tkey in ipairs(tabs) do
        local t0 = os.clock()
        ns.Window.ShowTab(tkey)
        local ms = (os.clock() - t0) * 1000.0
        if ms > maxMs then maxMs = ms end
        assert(ms <= maxPerSwitchMs,
            string.format("%s ShowTab('%s') took %.2fms (> %.2fms limit)", label, tkey, ms, maxPerSwitchMs))
    end
    local m = Mock.GetMetrics()
    assert(m.createFrameCalls == 0,
        string.format("%s allocated %d frames on tab switch (expected 0)", label, m.createFrameCalls))
    assert(m.createRegionCalls == 0,
        string.format("%s allocated %d regions on tab switch (expected 0)", label, m.createRegionCalls))
    assert(m.isQuestFlaggedCompletedCalls <= 30,
        string.format("%s called IsQuestFlaggedCompleted %d times (expected <= 30)", label, m.isQuestFlaggedCompletedCalls))
    assert(questieDoableCalls <= 30,
        string.format("%s called QuestieDB.IsDoable %d times (expected <= 30)", label, questieDoableCalls))
    return maxMs
end

-- Pass 1 (initial generation warmup), Pass 2 (same generation cache, <20ms), Pass 3 (fresh generation post-QUEST_LOG_UPDATE)
local ms1 = runTabRotation("PASS1", 50.0)
local ms2 = runTabRotation("PASS2_CACHED", 20.0)
Mock.FireEvent("QUEST_LOG_UPDATE")
local ms3 = runTabRotation("PASS3_POST_QLU", 50.0)

-- Bind all 2,700+ route steps via "All Steps" button, scroll, and click "Skin/LW" filter with zero allocations
Mock.ResetMetrics()
questieDoableCalls = 0
ns.Window.ShowTab("route")
local allBtn = findButtonByText(win, "All Steps")
assert(allBtn and allBtn:GetScript("OnClick"), "Expected 'All Steps' filter button in Window")
local tAll0 = os.clock()
allBtn:GetScript("OnClick")(allBtn)
local allMs = (os.clock() - tAll0) * 1000.0
assert(allMs <= 80.0, string.format("'All Steps' full-route render took %.2fms (> 80ms limit)", allMs))

ns.Window.ShowTab("dungeons")
local skinFilterBtn = findButtonByText(win, "Skin/LW")
assert(skinFilterBtn and skinFilterBtn:GetScript("OnClick"), "Expected 'Skin/LW' rewards filter button in Window")
local tSkin0 = os.clock()
skinFilterBtn:GetScript("OnClick")(skinFilterBtn)
local skinMs = (os.clock() - tSkin0) * 1000.0
assert(skinMs <= 50.0, string.format("'Skin/LW' rewards filter took %.2fms (> 50ms limit)", skinMs))

local mFilters = Mock.GetMetrics()
assert(mFilters.createFrameCalls == 0, "Filter clicks allocated frames: " .. tostring(mFilters.createFrameCalls))
assert(mFilters.createRegionCalls == 0, "Filter clicks allocated regions: " .. tostring(mFilters.createRegionCalls))
assert(questieDoableCalls <= 30, "Filter clicks called QuestieDB.IsDoable " .. tostring(questieDoableCalls) .. " times")

-- Verify live dynamic re-scoring stays bounded on QuestieDB.IsDoable
questieDoableCalls = 0
local dynSteps = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 12)
assert(#dynSteps > 0, "Expected dynamic steps")
assert(questieDoableCalls <= 30, "Scorer.GetDynamicSteps exceeded bounded QuestieDB.IsDoable cap: " .. tostring(questieDoableCalls))

assert(#Mock.errors == 0, "Unexpected Lua errors during perf test: " .. table.concat(Mock.errors, "; "))
print(string.format("TAB_PERF_OK initFrames=%d p1=%.2fms p2=%.2fms p3=%.2fms all=%.2fms skinFilter=%.2fms doable=%d",
    initMetrics.createFrameCalls, ms1, ms2, ms3, allMs, skinMs, questieDoableCalls))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Tab-switch performance/pooling test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("TAB_PERF_OK", out)

    def test_skinning_and_leatherworking_addon_integration(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab", realm = "Forever", level = 24,
    raceName = "Gnome", raceFile = "Gnome", raceId = 7,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1437, x = 0.108, y = 0.596,
}})

-- 1. Auto-detect Skinning (185) + Leatherworking (165) via GetNumSkillLines / GetSkillLineInfo
Mock.SetSkills({{
    {{ name = "Professions", isHeader = true }},
    {{ name = "Leatherworking", rank = 165, maxRank = 225 }},
    {{ name = "Skinning", rank = 185, maxRank = 225 }},
}}, false)

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

local prof = ns.State.DetectProfessions(true)
assert(prof.hasSkinning == true and prof.skinningRank == 185, "Expected Skinning 185 detected")
assert(prof.hasLeatherworking == true and prof.leatherworkingRank == 165, "Expected Leatherworking 165 detected")

-- 2. Empty skills via SKILL_LINES_CHANGED must clear stale profession flags
Mock.SetSkills({{}}, true)
prof = ns.State.DetectProfessions()
assert(prof.hasSkinning == false, "Expected empty skill list to clear hasSkinning")
assert(prof.hasLeatherworking == false, "Expected empty skill list to clear hasLeatherworking")

-- 3. Re-learn Skinning only (rank 205), keep LW missing
Mock.SetSkills({{
    {{ name = "Professions", isHeader = true }},
    {{ name = "Skinning", rank = 205, maxRank = 225 }},
}}, true)
prof = ns.State.DetectProfessions()
assert(prof.hasSkinning == true and prof.skinningRank == 205, "Expected Skinning 205 re-detected")
assert(prof.hasLeatherworking == false, "Expected hasLeatherworking == false when LW absent")

-- 4. Profession gating in State.GetQuestBlockReason (1582 Moonglow Vest req [165, 90], 91751 Skinning [393, 1])
local pMissingLW = {{ level = 24, faction = "Alliance", raceId = 7, classId = 4, zoneId = 11, x = 10.6, y = 55.6,
    professions = {{ hasSkinning = true, skinningRank = 205, hasLeatherworking = false, skills = {{ [393] = 205 }} }} }}
local codeNoLW = ns.State.GetQuestBlockReason(1582, "ACCEPT", pMissingLW, nil, nil, {{ consultQuestie = false }})
assert(codeNoLW == "missing_profession", "Expected 1582 blocked by missing_profession, got " .. tostring(codeNoLW))

local pLowLW = {{ level = 24, faction = "Alliance", raceId = 7, classId = 4, zoneId = 11, x = 10.6, y = 55.6,
    professions = {{ hasSkinning = false, hasLeatherworking = true, leatherworkingRank = 50, skills = {{ [165] = 50 }} }} }}
local codeLowLW = ns.State.GetQuestBlockReason(1582, "ACCEPT", pLowLW, nil, nil, {{ consultQuestie = false }})
assert(codeLowLW == "skill_rank_too_low", "Expected 1582 blocked by skill_rank_too_low at LW 50, got " .. tostring(codeLowLW))

local pGoodLW = {{ level = 24, faction = "Alliance", raceId = 7, classId = 4, zoneId = 11, x = 10.6, y = 55.6,
    professions = {{ hasSkinning = true, skinningRank = 205, hasLeatherworking = true, leatherworkingRank = 165, skills = {{ [393] = 205, [165] = 165 }} }} }}
local codeOkLW = ns.State.GetQuestBlockReason(1582, "ACCEPT", pGoodLW, nil, nil, {{ consultQuestie = false }})
assert(codeOkLW == nil, "Expected 1582 unblocked with LW 165, got " .. tostring(codeOkLW))
local codeOkSkin = ns.State.GetQuestBlockReason(91751, "ACCEPT", pGoodLW, nil, nil, {{ consultQuestie = false }})
assert(codeOkSkin == nil, "Expected 91751 unblocked with Skinning 205, got " .. tostring(codeOkSkin))

-- Excluded crafting profession (164 Blacksmithing) must report wrong_profession
ns.GetData().quests[999950] = {{
    id = 999950, name = "Synthetic Blacksmith Quest", side = 1, factionMask = 1,
    minLevel = 20, questLevel = 24, xp = 1800, zoneId = 11,
    requiredSkill = {{ skillId = 164, minSkill = 100 }}, prereqIds = {{}},
}}
local codeBS = ns.State.GetQuestBlockReason(999950, "ACCEPT", pGoodLW, nil, nil, {{ consultQuestie = false }})
assert(codeBS == "wrong_profession", "Expected Blacksmithing 164 quest blocked with wrong_profession, got " .. tostring(codeBS))
ns.GetData().quests[999950] = nil

-- 5. Manual toggle override when autoDetectProfessions = false
ns.charDB.toggles.autoDetectProfessions = false
ns.charDB.toggles.hasLeatherworking = true
ns.State.Refresh("TOGGLE_hasLeatherworking")
assert(ns.State.DetectProfessions(true).hasLeatherworking == true, "Expected manual hasLeatherworking=true override when autoDetectProfessions=false")
ns.charDB.toggles.autoDetectProfessions = true
ns.State.Refresh("TOGGLE_autoDetectProfessions")

-- 6. /fqp profile skin -> skinning_lw preset + skinning weights + Special Rewards filter
assert(Mock.RunSlash("/fqp", "profile skin"), "/fqp profile skin slash command failed")
assert(_G.ForeverQuestPathCharDB.activeProfile == "skinning_lw",
    "Expected /fqp profile skin to activate skinning_lw, got " .. tostring(_G.ForeverQuestPathCharDB.activeProfile))
assert((ns.GetActiveWeights().skinning or 0) > 0, "Expected skinning_lw active weights.skinning > 0")

local skinRewards = ns.Scorer.GetSpecialRewards(pGoodLW, "skinning_lw", 25, true)
assert(type(skinRewards) == "table" and #skinRewards > 0, "Expected non-empty skinning_lw special rewards list")
local sawLWorSkinReward = false
for _, rw in ipairs(skinRewards) do
    if rw.isLeatherworking or rw.isSkinning or (rw.skinningScore or 0) > 0 or (rw.lwRewards and #rw.lwRewards > 0) then
        sawLWorSkinReward = true
        break
    end
end
assert(sawLWorSkinReward, "Expected Skin/LW special rewards entries to carry LW/Skinning metadata")

-- 7. Verify HUD ViewModel formats [LW] / [Skin] badges cleanly when viewing skinning_lw route steps
local hudVM = ns.HUD and ns.HUD.GetViewModel and ns.HUD.GetViewModel()
assert(hudVM and hudVM.current, "Expected HUD ViewModel current step on skinning_lw")
local foundFormattedBadge = false
local steps = ns.GetData().profiles.skinning_lw.steps
for _, st in ipairs(steps) do
    if st.badges and (st.badges.isLeatherworking or st.badges.isSkinning or (st.badges.skinningScore or 0) >= 30) then
        assert(type(st.badges.skinningScore or 0) == "number")
        foundFormattedBadge = true
        break
    end
end
assert(foundFormattedBadge, "Expected LW/Skinning badge step in skinning_lw profile")

assert(#Mock.errors == 0, "Unexpected Lua errors in LW/Skinning integration test: " .. table.concat(Mock.errors, "; "))
print("SKIN_LW_ADDON_OK rewards=" .. tostring(#skinRewards))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Skinning/LW addon integration test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("SKIN_LW_ADDON_OK", out)

    def test_multirace_multiclass_and_lv1_to_23_routes(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter("Human", "Paladin", "Alliance", 1, 1429)

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

local data = ns.GetData()

-- 1. Verify >31-bit Skyborne raceMasks in ns.HasBitMask + synthetic race/class eligibility gating
local bit32 = 2 ^ 32
local bit33 = 2 ^ 33
assert(ns.HasBitMask(bit32, bit32) == true, "Expected ns.HasBitMask(2^32, 2^32) == true")
assert(ns.HasBitMask(bit32, bit33) == false, "Expected ns.HasBitMask(2^32, 2^33) == false")
assert(ns.HasBitMask(bit33, bit33) == true, "Expected ns.HasBitMask(2^33, 2^33) == true")
assert(ns.HasBitMask(bit32 + bit33, bit32) == true, "Expected composite >31-bit mask check == true")

data.quests[999961] = {{
    id = 999961, name = "Synthetic HighOrderSkyborne Only", side = 1, factionMask = 1,
    raceMask = bit32, classMask = 0, minLevel = 1, questLevel = 1, xp = 250, zoneId = 16593,
    prereqIds = {{}},
}}
data.quests[999962] = {{
    id = 999962, name = "Synthetic Druid Only", side = 3, factionMask = 3,
    raceMask = 0, classMask = 1024, minLevel = 1, questLevel = 1, xp = 250, zoneId = 148,
    prereqIds = {{}},
}}

local function assertStepEligibleForPlayer(step, player, ctx)
    assert(step ~= nil, ctx .. ": expected non-nil step")
    local pSide = (player.faction == "Horde") and 2 or 1
    if step.questId and data.quests[step.questId] then
        local q = data.quests[step.questId]
        assert(ns.State.IsEligibleForPlayer(step.questId, player),
            ctx .. ": step quest " .. tostring(step.questId) .. " ineligible for " .. tostring(player.faction) .. "/" .. tostring(player.raceFile) .. "/" .. tostring(player.classFile))
        if q.side and q.side ~= 3 then
            assert(q.side == pSide, ctx .. ": wrong-faction quest " .. tostring(step.questId) .. " side=" .. tostring(q.side))
        end
        if (step.action or "ACCEPT") == "ACCEPT" and not q.isDungeon then
            assert((q.minLevel or 1) <= (player.level or 1),
                ctx .. ": recommended quest " .. tostring(step.questId) .. " minLevel > player level " .. tostring(player.level))
        end
    end
end

-- 2. Lv 1 Human Paladin (Elwynn Forest = 12)
local pHumPal = ns.State.GetPlayerInfo()
assert(pHumPal.faction == "Alliance" and pHumPal.raceFile == "Human" and pHumPal.classFile == "PALADIN",
    "Expected Lv1 Alliance Human PALADIN, got " .. tostring(pHumPal.faction) .. "/" .. tostring(pHumPal.raceFile) .. "/" .. tostring(pHumPal.classFile))
assert(pHumPal.level == 1 and pHumPal.zoneId == 12,
    "Expected Lv1 Elwynn (12), got lvl=" .. tostring(pHumPal.level) .. " z=" .. tostring(pHumPal.zoneId))
assert(pHumPal.raceBitmask == 1 and pHumPal.classBitmask == 2, "Unexpected Human/Paladin bitmasks")
assert(ns.State.GetQuestBlockReason(999961, "ACCEPT", pHumPal, nil, nil, {{ consultQuestie = false }}) == "ineligible_character",
    "Expected Skyborne race-locked quest blocked for Human")
assert(ns.State.GetQuestBlockReason(999962, "ACCEPT", pHumPal, nil, nil, {{ consultQuestie = false }}) == "ineligible_character",
    "Expected Druid class-locked quest blocked for Paladin")
assert(ns.State.GetQuestBlockReason(97279, "ACCEPT", pHumPal, nil, nil, {{ consultQuestie = false }}) == "ineligible_character",
    "Expected Horde Durotar quest 97279 blocked for Alliance Human")

local stHum = ns.State.GetCurrentStep()
assertStepEligibleForPlayer(stHum, pHumPal, "HumPal_Lv1_Current")
assert(stHum.zoneId == 12, "Expected Human Paladin starter step in Elwynn (12), got " .. tostring(stHum.zoneId))
local upHum = ns.State.GetUpcomingSteps(10)
assert(#upHum >= 5, "Expected >= 5 upcoming steps for Lv1 Human Paladin")
for i = 1, #upHum do
    assertStepEligibleForPlayer(upHum[i], pHumPal, "HumPal_Upcoming_" .. i)
end
local dynHum = ns.Scorer.GetDynamicSteps(pHumPal, ns.GetActiveWeights(), ns.charDB.toggles, 8)
assert(#dynHum > 0, "Expected dynamic steps for Lv1 Human Paladin")
for i = 1, #dynHum do
    assertStepEligibleForPlayer(dynHum[i], pHumPal, "HumPal_Dyn_" .. i)
end
local hudHum = ns.HUD.GetViewModel()
assert(hudHum and hudHum.playerLine and hudHum.playerLine:find("Human Paladin", 1, true),
    "Expected HUD playerLine to display Human Paladin, got " .. tostring(hudHum and hudHum.playerLine))
local palRewards = ns.Scorer.GetSpecialRewards(pHumPal, "bis", 20, true)
assert(#palRewards > 0, "Expected Paladin BiS rewards for class filter")

-- 3. Lv 12 Night Elf Druid (Darkshore = 148) + Druid class quest eligibility
Mock.SetCharacter("Night Elf", "Druid", "Alliance", 12, 1439, true)
local pNEDruid = ns.State.GetPlayerInfo()
assert(pNEDruid.faction == "Alliance" and pNEDruid.raceFile == "NightElf" and pNEDruid.classFile == "DRUID",
    "Expected NightElf DRUID, got " .. tostring(pNEDruid.raceFile) .. "/" .. tostring(pNEDruid.classFile))
assert(pNEDruid.level == 12 and pNEDruid.zoneId == 148 and pNEDruid.classBitmask == 1024, "Unexpected NE Druid state")
assert(ns.State.GetQuestBlockReason(999962, "ACCEPT", pNEDruid, nil, nil, {{ consultQuestie = false }}) == nil,
    "Expected Druid class-locked synthetic quest to be eligible for Druid")
assert(ns.State.GetQuestBlockReason(5925, "ACCEPT", pNEDruid, nil, nil, {{ consultQuestie = false }}) == nil,
    "Expected Lv10 Druid class quest 5925 (Heeding the Call) to be eligible at Lv12")
local stNEDruid = ns.State.GetCurrentStep()
assertStepEligibleForPlayer(stNEDruid, pNEDruid, "NEDruid_Lv12_Current")
for i, s in ipairs(ns.State.GetUpcomingSteps(10)) do
    assertStepEligibleForPlayer(s, pNEDruid, "NEDruid_Upcoming_" .. i)
end

-- 4. Lv 1 Orc Warrior (Durotar = 14) + Horde route composition
Mock.SetCharacter("Orc", "Warrior", "Horde", 1, 1411, true)
local pOrcWar = ns.State.GetPlayerInfo()
assert(pOrcWar.faction == "Horde" and pOrcWar.raceFile == "Orc" and pOrcWar.classFile == "WARRIOR",
    "Expected Horde Orc WARRIOR, got " .. tostring(pOrcWar.faction) .. "/" .. tostring(pOrcWar.raceFile) .. "/" .. tostring(pOrcWar.classFile))
assert(pOrcWar.zoneId == 14 and pOrcWar.startZoneId == 14, "Expected Durotar (14)")
assert(ns.State.GetQuestBlockReason(783, "ACCEPT", pOrcWar, nil, nil, {{ consultQuestie = false }}) == "ineligible_character",
    "Expected Alliance quest 783 blocked for Horde Orc")
local orcSteps = ns.State.GetActiveProfileSteps()
assert(type(orcSteps) == "table" and #orcSteps > 200, "Expected composed Horde starter+spine route (>200 steps), got " .. tostring(orcSteps and #orcSteps))
local stOrc = ns.State.GetCurrentStep()
assertStepEligibleForPlayer(stOrc, pOrcWar, "OrcWar_Lv1_Current")
assert(stOrc.zoneId == 14, "Expected Orc Warrior starter step in Durotar (14), got " .. tostring(stOrc.zoneId))
for i, s in ipairs(ns.State.GetUpcomingSteps(12)) do
    assertStepEligibleForPlayer(s, pOrcWar, "OrcWar_Upcoming_" .. i)
end
local warRewards = ns.Scorer.GetSpecialRewards(pOrcWar, "class", 20, true)
assert(#warRewards > 0, "Expected Warrior BiS rewards")

-- 5. Lv 1 Undead / Scourge Mage (Tirisfal Glades = 85)
Mock.SetCharacter("Undead", "Mage", "Horde", 1, 1420, true)
local pUndMage = ns.State.GetPlayerInfo()
assert(pUndMage.faction == "Horde" and pUndMage.raceFile == "Scourge" and pUndMage.classFile == "MAGE",
    "Expected Undead normalized to Horde Scourge MAGE, got " .. tostring(pUndMage.faction) .. "/" .. tostring(pUndMage.raceFile) .. "/" .. tostring(pUndMage.classFile))
assert(pUndMage.zoneId == 85 and pUndMage.startZoneId == 85, "Expected Tirisfal Glades (85)")
local stUnd = ns.State.GetCurrentStep()
assertStepEligibleForPlayer(stUnd, pUndMage, "UndMage_Lv1_Current")
assert(stUnd.zoneId == 85, "Expected Scourge Mage starter step in Tirisfal (85), got " .. tostring(stUnd.zoneId))
for i, s in ipairs(ns.State.GetUpcomingSteps(10)) do
    assertStepEligibleForPlayer(s, pUndMage, "UndMage_Upcoming_" .. i)
end

-- 6. WoW Forever HighOrderSkyborne (Alliance, 2^32) vs WindshaperSkyborne (Horde, 2^33) on Zephras Isle (16593)
Mock.SetCharacter("HighOrderSkyborne", "Priest", "Alliance", 1, 2521, true)
local pSkyA = ns.State.GetPlayerInfo()
assert(pSkyA.faction == "Alliance" and pSkyA.raceFile == "HighOrderSkyborne" and pSkyA.raceBitmask == bit32,
    "Expected HighOrderSkyborne raceBitmask=2^32, got " .. tostring(pSkyA.raceBitmask))
assert(ns.State.GetQuestBlockReason(999961, "ACCEPT", pSkyA, nil, nil, {{ consultQuestie = false }}) == nil,
    "Expected synthetic 2^32 race-locked quest to be eligible for HighOrderSkyborne")
local stSkyA = ns.State.GetCurrentStep()
assertStepEligibleForPlayer(stSkyA, pSkyA, "HighOrderSkyborne_Current")
local stepsSkyA = ns.State.GetActiveProfileSteps()
for i, s in ipairs(ns.State.GetUpcomingSteps(10)) do
    assertStepEligibleForPlayer(s, pSkyA, "HighOrderSkyborne_Upcoming_" .. i)
end

Mock.SetCharacter("WindshaperSkyborne", "Shaman", "Horde", 1, 2521, true)
local pSkyH = ns.State.GetPlayerInfo()
assert(pSkyH.faction == "Horde" and pSkyH.raceFile == "WindshaperSkyborne" and pSkyH.raceBitmask == bit33,
    "Expected WindshaperSkyborne raceBitmask=2^33, got " .. tostring(pSkyH.raceBitmask))
assert(ns.State.GetQuestBlockReason(999961, "ACCEPT", pSkyH, nil, nil, {{ consultQuestie = false }}) == "ineligible_character",
    "Expected 2^32 HighOrderSkyborne quest blocked for 2^33 WindshaperSkyborne")
local stSkyH = ns.State.GetCurrentStep()
assertStepEligibleForPlayer(stSkyH, pSkyH, "WindshaperSkyborne_Current")
local stepsSkyH = ns.State.GetActiveProfileSteps()
-- Both Skyborne factions share neutral Zephras Isle Lv1 opener (92460), then diverge into Alliance vs Horde routes
assert(stSkyA.questId == 92460 and stSkyH.questId == 92460,
    string.format("Expected shared Skyborne Zephras Isle opener 92460, got A=%s H=%s", tostring(stSkyA.questId), tostring(stSkyH.questId)))
local skyDiverged = (#stepsSkyA ~= #stepsSkyH)
for i = 1, math.min(#stepsSkyA, #stepsSkyH) do
    if stepsSkyA[i].questId ~= stepsSkyH[i].questId then
        skyDiverged = true
        break
    end
end
assert(skyDiverged, "Expected Alliance vs Horde Skyborne composed routes to diverge after shared Zephras Isle intro")

-- 7. Lv 24 Alliance Gnome Rogue catch-up in Wetlands (11): pre-24 starter & orphaned Barrens WC steps skipped; local WetlandsScorer active; 98230/464 blocked
Mock.SetCharacter("Gnome", "Rogue", "Alliance", 24, 1437, true)
local pGn24 = ns.State.GetPlayerInfo()
assert(pGn24.faction == "Alliance" and pGn24.raceFile == "Gnome" and pGn24.classFile == "ROGUE" and pGn24.level == 24 and pGn24.zoneId == 11)
local stGn24 = ns.State.GetCurrentStep()
assertStepEligibleForPlayer(stGn24, pGn24, "GnRogue24_Catchup")
assert(stGn24.questId ~= 1487 and stGn24.zoneId ~= 17,
    "Lv24 Gnome Rogue in Wetlands must not land on orphaned Barrens Wailing Caverns TURN_IN (step " .. tostring(stGn24.index) .. " " .. tostring(stGn24.action) .. " " .. tostring(stGn24.questId) .. ")")
assert((stGn24.expectedLevel or 0) >= 22, "Expected Lv24 catch-up expectedLevel >= 22, got " .. tostring(stGn24.expectedLevel))
assert(stGn24.questId ~= 98230 and stGn24.questId ~= 464, "Lv24 catch-up must never land on Lv29 quest 98230 or Lv28 quest 464")
local dynGn24 = ns.Scorer.GetDynamicSteps(pGn24, ns.GetActiveWeights(), ns.charDB.toggles, 5)
assert(#dynGn24 > 0 and dynGn24[1].zoneId == 11,
    "Expected Scorer.GetDynamicSteps for Lv24 Rogue in Wetlands to prioritize Wetlands (11), got zone " .. tostring(dynGn24[1] and dynGn24[1].zoneId))

-- 8. Slash /fqp char + Window raceBtn/classBtn/autoCharBtn UI controls with 0 frame/region leaks
ns.Window.ShowTab("route")
assert(Mock.RunSlash("/fqp", "char Human Paladin"), "/fqp char Human Paladin failed")
local pOver = ns.State.GetPlayerInfo()
assert(pOver.isCharOverride == true and pOver.raceFile == "Human" and pOver.classFile == "PALADIN" and pOver.faction == "Alliance",
    "Expected /fqp char Human Paladin override")
local hudOver = ns.HUD.GetViewModel()
assert(hudOver.playerLine:find("Human Paladin*", 1, true), "Expected override marker '*' in HUD playerLine")

assert(Mock.RunSlash("/fqp", "char Orc Warrior"), "/fqp char Orc Warrior failed")
local pOverH = ns.State.GetPlayerInfo()
assert(pOverH.isCharOverride == true and pOverH.raceFile == "Orc" and pOverH.classFile == "WARRIOR" and pOverH.faction == "Horde",
    "Expected /fqp char Orc Warrior override")

assert(Mock.RunSlash("/fqp", "char auto"), "/fqp char auto failed")
local pAuto = ns.State.GetPlayerInfo()
assert(pAuto.isCharOverride == false and pAuto.raceFile == "Gnome" and pAuto.classFile == "ROGUE",
    "Expected /fqp char auto to restore live Gnome Rogue")

local function findButtonByPrefix(root, prefix, depth)
    if not root or (depth or 0) > 6 then return nil end
    if root.label and root.label.GetText then
        local txt = root.label:GetText()
        if type(txt) == "string" and txt:sub(1, #prefix) == prefix then
            return root, txt
        end
    end
    for _, ch in ipairs(root._children or {{}}) do
        local hit, hitTxt = findButtonByPrefix(ch, prefix, (depth or 0) + 1)
        if hit then return hit, hitTxt end
    end
    return nil
end

local win = _G.ForeverQuestPathWindow
local raceBtn, raceLbl = findButtonByPrefix(win, "Race: ")
local classBtn = findButtonByPrefix(win, "Class: ")
local autoCharBtn = findButtonByPrefix(win, "Char: ") or findButtonByPrefix(win, "Auto Char")
assert(raceBtn and raceBtn:GetScript("OnClick"), "Expected Window raceBtn")
assert(classBtn and classBtn:GetScript("OnClick"), "Expected Window classBtn")
assert(autoCharBtn and autoCharBtn:GetScript("OnClick"), "Expected Window autoCharBtn")

Mock.ResetMetrics()
raceBtn:GetScript("OnClick")(raceBtn)
local afterRace = ns.State.GetPlayerInfo()
assert(afterRace.isCharOverride == true and afterRace.raceFile ~= "Gnome", "Expected raceBtn click to cycle race")
local _, newRaceLbl = findButtonByPrefix(win, "Race: ")
assert(newRaceLbl ~= raceLbl, "Expected raceBtn label to update on click")

classBtn:GetScript("OnClick")(classBtn)
local afterClass = ns.State.GetPlayerInfo()
assert(afterClass.classFile ~= afterRace.classFile, "Expected classBtn click to cycle class")
ns.Window.ShowTab("dungeons")
local classCheckBtn = findButtonByPrefix(win, afterClass.className .. " BiS")
assert(classCheckBtn ~= nil, "Expected Window label to reflect active class '" .. tostring(afterClass.className) .. " BiS'")
ns.Window.ShowTab("route")

autoCharBtn:GetScript("OnClick")(autoCharBtn)
local afterAuto = ns.State.GetPlayerInfo()
assert(afterAuto.isCharOverride == false and afterAuto.raceFile == "Gnome" and afterAuto.classFile == "ROGUE",
    "Expected autoCharBtn click to restore live character detection")

local mCharUi = Mock.GetMetrics()
assert(mCharUi.createFrameCalls == 0, "Character override UI clicks must not allocate frames, got " .. tostring(mCharUi.createFrameCalls))
assert(mCharUi.createRegionCalls == 0, "Character override UI clicks must not allocate regions, got " .. tostring(mCharUi.createRegionCalls))

data.quests[999961] = nil
data.quests[999962] = nil
assert(#Mock.errors == 0, "Unexpected Lua errors in multi-race/class test: " .. table.concat(Mock.errors, "; "))
print(string.format("MULTICHAR_ADDON_OK palRewards=%d warRewards=%d orcSteps=%d", #palRewards, #warRewards, #orcSteps))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Multi-race/class/faction addon test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("MULTICHAR_ADDON_OK", out)

    def test_max_level_offset_slider_and_464_deferral(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter("Gnome", "Rogue", "Alliance", 24, 1437)

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

local data = ns.GetData()
assert(data.meta and data.meta.defaultMaxLevelOffset == 2,
    "Expected GeneratedData.meta.defaultMaxLevelOffset == 2")
assert(ns.State.GetMaxLevelOffset() == 2,
    "Expected default State.GetMaxLevelOffset() == 2, got " .. tostring(ns.State.GetMaxLevelOffset()))
assert(ns.State.GetEffectiveMaxLevelOffset() == 2,
    "Expected default State.GetEffectiveMaxLevelOffset() == 2")

-- 1. Quest 464 (War Banners, Wetlands, minLevel=23, questLevel=28) MUST be blocked at Lv24 under +2
local p24 = ns.State.GetPlayerInfo()
assert(p24.level == 24 and p24.zoneId == 11)
local blk464Default = ns.State.GetQuestBlockReason(464, "ACCEPT", p24, nil, nil, {{ consultQuestie = false }})
assert(blk464Default == "quest_level_too_high",
    "Expected quest 464 (Lv28) blocked with quest_level_too_high at Lv24/+2, got " .. tostring(blk464Default))

local curInit = ns.State.GetCurrentStep()
assert(curInit and curInit.questId ~= 464, "Lv24/+2 GetCurrentStep must not suggest 464")
for _, us in ipairs(ns.State.GetUpcomingSteps(20)) do
    assert(us.questId ~= 464, "Lv24/+2 GetUpcomingSteps must not suggest 464")
end
for _, ds in ipairs(ns.Scorer.GetDynamicSteps(p24, ns.GetActiveWeights(), ns.charDB.toggles, 20)) do
    assert(ds.questId ~= 464, "Lv24/+2 Scorer.GetDynamicSteps must not suggest 464")
    if ds.questId and data.quests[ds.questId] and data.quests[ds.questId].questLevel then
        assert(data.quests[ds.questId].questLevel <= 26,
            "Lv24/+2 dynamic step exceeded Lv26 cap: qid=" .. tostring(ds.questId))
    end
end

-- Confirm exported balanced 1->60 profile schedules quest 464 at expectedLevel >= 26
local balFound464 = nil
for _, st in ipairs(data.profiles.balanced.steps or {{}}) do
    if st.questId == 464 and (st.action or "ACCEPT") == "ACCEPT" then
        balFound464 = st
        break
    end
end
assert(balFound464 and (balFound464.expectedLevel or 0) >= 26,
    "Expected balanced route to defer ACCEPT 464 until expectedLevel >= 26, got " .. tostring(balFound464 and balFound464.expectedLevel))

-- 2. Sweep all offsets -2, -1, 0, +1, +2, +3, +4, +5 (plus clamping -3..+5)
assert(ns.State.SetMaxLevelOffset(-10) == -3, "Expected clamp lower bound -3")
assert(ns.State.SetMaxLevelOffset(99) == 5, "Expected clamp upper bound +5")

for off = -2, 5 do
    local applied = ns.State.SetMaxLevelOffset(off)
    assert(applied == off and ns.State.GetMaxLevelOffset() == off, "SetMaxLevelOffset failed for " .. tostring(off))
    local p = ns.State.GetPlayerInfo()
    local blk464 = ns.State.GetQuestBlockReason(464, "ACCEPT", p, nil, nil, {{ consultQuestie = false }})
    if off < 4 then
        assert(blk464 == "quest_level_too_high",
            string.format("Expected 464 (Lv28) blocked at Lv24/+%d, got %s", off, tostring(blk464)))
    else
        assert(blk464 == nil,
            string.format("Expected 464 (Lv28) eligible at Lv24/+%d, got %s", off, tostring(blk464)))
    end
    local dyn = ns.Scorer.GetDynamicSteps(p, ns.GetActiveWeights(), ns.charDB.toggles, 10)
    for _, ds in ipairs(dyn) do
        local q = ds.questId and data.quests[ds.questId] or nil
        if q and q.questLevel and (ds.action or "ACCEPT") == "ACCEPT" then
            assert(q.questLevel <= 24 + off,
                string.format("Dynamic step qid=%d qLvl=%d exceeded 24+(%d)=%d", ds.questId, q.questLevel, off, 24 + off))
        end
    end
end

-- 3. UI Max Quest Lv Slider & /fqp maxlevel slash commands with 0 frame allocations
ns.State.SetMaxLevelOffset(2)
ns.Window.ShowTab("route")
local win = _G.ForeverQuestPathWindow

local function findSliderByTitle(root, titleText, depth)
    if not root or (depth or 0) > 6 then return nil end
    if root.slider and root.valText then
        for _, reg in ipairs(root._regions or {{}}) do
            if reg.GetText and reg:GetText() == titleText then
                return root
            end
        end
    end
    for _, ch in ipairs(root._children or {{}}) do
        local hit = findSliderByTitle(ch, titleText, (depth or 0) + 1)
        if hit then return hit end
    end
    return nil
end

local maxLvHolder = findSliderByTitle(win, "Max Quest Lv")
assert(maxLvHolder and maxLvHolder.slider and maxLvHolder.valText, "Expected 'Max Quest Lv' UI slider in Window")
local mn, mx = maxLvHolder.slider:GetMinMaxValues()
assert(mn == -3 and mx == 5, string.format("Expected slider min/max -3..5, got %s..%s", tostring(mn), tostring(mx)))
assert(maxLvHolder.valText:GetText() == "+2 (<=26)",
    "Expected initial slider readout '+2 (<=26)', got " .. tostring(maxLvHolder.valText:GetText()))

Mock.ResetMetrics()
maxLvHolder.slider:SetValue(4)
assert(ns.State.GetMaxLevelOffset() == 4, "Slider SetValue(4) must set offset=4")
assert(maxLvHolder.valText:GetText() == "+4 (<=28)",
    "Expected slider readout '+4 (<=28)', got " .. tostring(maxLvHolder.valText:GetText()))
local hud4 = ns.HUD.GetViewModel()
assert(hud4 and hud4.modeLabel and hud4.modeLabel:find("MaxLv +4 (<=28)", 1, true),
    "Expected HUD modeLabel 'MaxLv +4 (<=28)', got " .. tostring(hud4 and hud4.modeLabel))

maxLvHolder.slider:SetValue(-1)
assert(ns.State.GetMaxLevelOffset() == -1, "Slider SetValue(-1) must set offset=-1")
assert(maxLvHolder.valText:GetText() == "-1 (<=23)",
    "Expected slider readout '-1 (<=23)', got " .. tostring(maxLvHolder.valText:GetText()))

assert(Mock.RunSlash("/fqp", "maxlevel +4"), "/fqp maxlevel +4 failed")
assert(ns.State.GetMaxLevelOffset() == 4, "Expected /fqp maxlevel +4 -> 4")
assert(Mock.RunSlash("/fqp", "maxlv -2"), "/fqp maxlv -2 failed")
assert(ns.State.GetMaxLevelOffset() == -2, "Expected /fqp maxlv -2 -> -2")
assert(Mock.RunSlash("/fqp", "maxlevel reset"), "/fqp maxlevel reset failed")
assert(ns.State.GetMaxLevelOffset() == 2, "Expected /fqp maxlevel reset -> 2")

local mSlider = Mock.GetMetrics()
assert(mSlider.createFrameCalls == 0, "Max Quest Lv slider / slash changes allocated frames: " .. tostring(mSlider.createFrameCalls))
assert(mSlider.createRegionCalls == 0, "Max Quest Lv slider / slash changes allocated regions: " .. tostring(mSlider.createRegionCalls))

-- 4. Route step deferral & automatic rewind without /fqp reset (slider +4 -> +2 -> Mock.LevelUp(26))
data.profiles.__test_defer464 = {{
    name = "TestDefer464",
    steps = {{
        {{ index = 1, action = "ACCEPT", questId = 463, title = "Accept: The Greenwarden", zoneId = 11, zoneName = "Wetlands", expectedLevel = 24, x = 56.4, y = 40.4 }},
        {{ index = 2, action = "ACCEPT", questId = 464, title = "Accept: War Banners", zoneId = 11, zoneName = "Wetlands", expectedLevel = 24, x = 11.6, y = 58.0 }},
        {{ index = 3, action = "ACCEPT", questId = 469, title = "Accept: Daily Delivery", zoneId = 11, zoneName = "Wetlands", expectedLevel = 24, x = 50.0, y = 37.8 }},
    }},
}}
Mock.CompleteQuest(463)
ns.State.SetMaxLevelOffset(2)
ns.State.SetProfile("__test_defer464")

local dStep1 = ns.State.GetCurrentStep()
assert(dStep1 and dStep1.questId == 469,
    "Expected Lv24/+2 to defer 464 and advance to step 3 (469), got " .. tostring(dStep1 and dStep1.questId))
assert(ns.charDB.deferredSteps.__test_defer464 and ns.charDB.deferredSteps.__test_defer464[2] == true,
    "Expected step 2 (464) recorded in charDB.deferredSteps")
assert(not (ns.charDB.skippedSteps and ns.charDB.skippedSteps["__test_defer464:2"]),
    "Deferred step 464 must NOT be marked permanently skipped")

-- Raising slider to +4 immediately rewinds cursor back to 464 without /fqp reset
maxLvHolder.slider:SetValue(4)
local dStepPlus4 = ns.State.GetCurrentStep()
assert(dStepPlus4 and dStepPlus4.questId == 464,
    "Expected raising Max Quest Lv to +4 to automatically rewind back to 464, got " .. tostring(dStepPlus4 and dStepPlus4.questId))

-- Lowering back to +2 re-defers 464 cleanly to 469
maxLvHolder.slider:SetValue(2)
local dStepBack2 = ns.State.GetCurrentStep()
assert(dStepBack2 and dStepBack2.questId == 469,
    "Expected lowering back to +2 to re-defer 464 to 469, got " .. tostring(dStepBack2 and dStepBack2.questId))

-- Leveling up to Lv 26 while staying at +2 automatically unlocks & rewinds to 464 (zero /fqp reset!)
Mock.LevelUp(26)
local dStepLv26 = ns.State.GetCurrentStep()
assert(ns.State.GetPlayerInfo().level == 26, "Expected Mock.LevelUp(26) to set player level=26")
assert(dStepLv26 and dStepLv26.questId == 464,
    "Expected leveling to Lv26 at +2 to automatically rewind & surface deferred quest 464, got " .. tostring(dStepLv26 and dStepLv26.questId))
assert(ns.State.GetQuestBlockReason(464, "ACCEPT", ns.State.GetPlayerInfo(), nil, nil, {{ consultQuestie = false }}) == nil,
    "Expected 464 eligible at Lv26/+2")

data.profiles.__test_defer464 = nil
assert(#Mock.errors == 0, "Unexpected Lua errors in maxLevelOffset test: " .. table.concat(Mock.errors, "; "))
print("MAX_LEVEL_OFFSET_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Max level offset slider / 464 deferral test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("MAX_LEVEL_OFFSET_OK", out)

    def test_ready_turnin_optimal_placement(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})
-- 1. Nearby / Same-Hub Batching in Menethil Harbor (11, x=10.8, y=59.6)
Mock.SetCharacter({{
    name = "Sprocketstab", realm = "Forever", level = 24,
    raceName = "Gnome", raceFile = "Gnome", raceId = 7,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1437, x = 0.108, y = 0.596,
}})

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

-- Verify off-DB ready quest resolves turn-in via QuestieDB finishedBy NPC spawns + pcall safety
_G.QuestieDB = {{
    QueryQuestSingle = function(qid, field)
        if qid == 999971 then
            if field == "name" then return "Off-DB Menethil Bounty" end
            if field == "finishedBy" then return {{ {{ 99911 }}, nil }} end
            if field == "zoneOrSort" then return 11 end
            if field == "level" then return 25 end
        end
        return nil
    end,
    QueryNPCSingle = function(nid, field)
        if nid == 99911 then
            if field == "name" then return "Quartermaster Harlan" end
            if field == "spawns" then return {{ [11] = {{ {{ 10.6, 58.9 }} }} }} end
        end
        return nil
    end,
}}

local offResolved = ns.State.ResolveQuestTurnin(999971)
assert(offResolved and offResolved.zoneId == 11 and offResolved.npcName == "Quartermaster Harlan",
    "Expected QuestieDB finishedBy NPC turn-in resolution for off-DB quest")

_G.QuestieDB.QueryQuestSingle = function() error("simulated QuestieDB error") end
local safeOff = ns.State.ResolveQuestTurnin(999971)
assert(type(safeOff) == "table", "Expected pcall-guarded QuestieDB failure not to error")
_G.QuestieDB = nil

-- Add 3 ready-to-turn-in quests right in Menethil Harbor (incl. 288 Lv27 while MaxLv=-2!)
ns.State.SetMaxLevelOffset(-2)
Mock.AcceptQuest(484, "Young Crocolisk Skins", 22, false)
assert(ns.State.GetReadyTurnInCount() == 0, "Incomplete quest 484 must not count as ready turn-in")
Mock.SetQuestObjectivesComplete(484, true)
assert(C_QuestLog.ReadyForTurnIn(484) == true and C_QuestLog.IsComplete(484) == true and select(6, GetQuestLogTitle(1)) == 1)
Mock.AcceptQuest(279, "Claws from the Deep", 22, true)
Mock.AcceptQuest(288, "The Third Fleet", 27, true)

assert(ns.State.GetReadyTurnInCount() == 3, "Expected 3 ready turn-ins, got " .. tostring(ns.State.GetReadyTurnInCount()))

local curMen, curMenMeta = ns.State.GetCurrentStep()
assert(curMen and curMen.action == "TURN_IN" and curMen.isReadyTurnIn == true and curMen.zoneId == 11,
    "Expected immediate nearby Menethil ready turn-in step, got " .. tostring(curMen and curMen.title))
assert(curMenMeta and curMenMeta.source == "ready_turnin",
    "Expected meta.source == 'ready_turnin', got " .. tostring(curMenMeta and curMenMeta.source))

local hudMen = ns.HUD.GetViewModel()
assert(hudMen and hudMen.modeLabel and hudMen.modeLabel:find("Ready Turn-Ins: 3", 1, true),
    "Expected HUD modeLabel to include 'Ready Turn-Ins: 3', got " .. tostring(hudMen and hudMen.modeLabel))
assert(hudMen.current and hudMen.current.actionText and hudMen.current.actionText:find("Ready Turn-In", 1, true),
    "Expected HUD actionText '[Ready Turn-In]', got " .. tostring(hudMen.current and hudMen.current.actionText))
assert(hudMen.current.badgesLine and hudMen.current.badgesLine:find("[Ready Turn-In]", 1, true),
    "Expected HUD badgesLine '[Ready Turn-In]', got " .. tostring(hudMen.current and hudMen.current.badgesLine))

-- Confirm even high-level ready quest 288 (Lv27) is placed & NOT blocked at MaxLv=-2
local upMen = ns.State.GetUpcomingSteps(10)
local saw484, saw279, saw288 = (curMen.questId == 484), (curMen.questId == 279), (curMen.questId == 288)
for _, s in ipairs(upMen) do
    if s.questId == 484 and s.action == "TURN_IN" and s.isReadyTurnIn then saw484 = true end
    if s.questId == 279 and s.action == "TURN_IN" and s.isReadyTurnIn then saw279 = true end
    if s.questId == 288 and s.action == "TURN_IN" and s.isReadyTurnIn then saw288 = true end
end
assert(saw484 and saw279 and saw288, "Expected all 3 nearby Menethil ready turn-ins (incl. Lv27 288 under MaxLv=-2) placed immediately")

-- Turning in one Menethil quest decrements count cleanly
local curQid = curMen.questId
Mock.TurnInQuest(curQid, 1400, 350)
assert(ns.State.GetReadyTurnInCount() == 2, "Expected 2 ready turn-ins after turning in " .. tostring(curQid))

-- 2. Deferred / Zone-Aligned Anti-Detour + Multi-Turn-In Unvisited-Zone Batching
Mock.Reset({{ tomtom = true, openmap = true }})
-- Place Lv 24 Gnome Rogue in eastern Wetlands (56.4, 40.4) at Greenwarden — far from Stormwind (1519) & Loch Modan (38)
Mock.SetCharacter({{
    name = "Sprocketstab", realm = "Forever", level = 24,
    raceName = "Gnome", raceFile = "Gnome", raceId = 7,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1437, x = 0.564, y = 0.404,
}})
addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
ns.State.SetMaxLevelOffset(2)

-- Stormwind City (1519, revisited inside 140-step route window): 1338 & 343
Mock.AcceptQuest(1338, "Stormpike's Order", 14, true)
Mock.AcceptQuest(343, "Speaking of Fortitude", 24, true)
-- Unvisited Loch Modan (38, NOT revisited in Wetlands->Hillsbrad->Stormwind horizon): 416 & 1339
Mock.AcceptQuest(416, "Rat Catching", 11, true)
Mock.AcceptQuest(1339, "Mountaineer Stormpike's Task", 15, true)
assert(ns.State.GetReadyTurnInCount() == 4)

-- Anti-detour: first step MUST stay local in Wetlands (11), NOT warp cross-continent to Stormwind or Loch Modan
local stEast, stEastMeta = ns.State.GetCurrentStep()
assert(stEast and stEast.zoneId == 11 and stEast.questId ~= 1338 and stEast.questId ~= 343 and stEast.questId ~= 416 and stEast.questId ~= 1339,
    string.format("Anti-detour failed: expected local Wetlands (11) step before Stormwind/Loch Modan turn-ins, got zone=%s title=%s",
        tostring(stEast and stEast.zoneId), tostring(stEast and stEast.title)))

-- Scan upcoming horizon: both Loch Modan ready turn-ins batched under a single TRAVEL(38) AFTER local Wetlands cluster,
-- and both Stormwind ready turn-ins aligned cleanly into the upcoming Stormwind (1519) hub!
local upLong = ns.State.GetUpcomingSteps(140)
local firstNonWetlandsIdx = nil
local lochTravelIdx = nil
local lochTurninIdxs = {{}}
local swTurninIdxs = {{}}
for i, s in ipairs(upLong) do
    if (not firstNonWetlandsIdx) and s.zoneId and s.zoneId ~= 11 then
        firstNonWetlandsIdx = i
    end
    if s.action == "TRAVEL" and s.zoneId == 38 and s.isReadyTurnIn then
        lochTravelIdx = i
    end
    if s.action == "TURN_IN" and s.isReadyTurnIn then
        if s.questId == 416 or s.questId == 1339 then
            lochTurninIdxs[s.questId] = i
        elseif s.questId == 1338 or s.questId == 343 then
            swTurninIdxs[s.questId] = i
        end
    end
end
assert(lochTravelIdx ~= nil, "Expected batched 'Travel to Loch Modan (2 Ready Turn-Ins)' step in upcoming list")
assert(lochTurninIdxs[416] and lochTurninIdxs[1339], "Expected both Loch Modan ready turn-ins (416 & 1339) in upcoming list")
assert(lochTurninIdxs[416] > lochTravelIdx and lochTurninIdxs[1339] > lochTravelIdx,
    "Expected Loch Modan ready turn-ins right after Loch Modan batch TRAVEL step")
assert(math.abs(lochTurninIdxs[416] - lochTurninIdxs[1339]) <= 2,
    "Expected multiple Loch Modan ready turn-ins grouped together cleanly")
assert(firstNonWetlandsIdx and lochTravelIdx >= firstNonWetlandsIdx,
    "Expected unvisited Loch Modan batch placed AFTER local Wetlands cluster finishes")
assert(swTurninIdxs[1338] and swTurninIdxs[343], "Expected both Stormwind ready turn-ins (1338 & 343) spliced into Stormwind hub")
assert(swTurninIdxs[1338] > (firstNonWetlandsIdx or 0) and swTurninIdxs[343] > (firstNonWetlandsIdx or 0),
    "Expected Stormwind ready turn-ins deferred until after local Wetlands cluster")

-- Also confirm Scorer.GetDynamicSteps surfaces ready turn-ins cleanly (incl. Lv43 quest 1448 at Lv24!)
Mock.AcceptQuest(1448, "In Search of The Temple", 43, true)
local dynReady = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 20)
local dynSawReady = false
for _, ds in ipairs(dynReady) do
    if ds.isReadyTurnIn == true and ds.action == "TURN_IN" then
        dynSawReady = true
        assert(ds.why and ds.why:find("Ready Turn-In", 1, true), "Expected Scorer dynamic ready turn-in why tag")
        break
    end
end
assert(dynSawReady, "Expected Scorer.GetDynamicSteps to surface ready turn-ins")

-- 3. UI Window Ready Turn-Ins snapshot + 0 allocations + <20ms cached tab switching
ns.Window.ShowTab("route")
local snap = ns.Window.GetSnapshot()
assert(snap and snap.readyTurnInCount == 5, "Expected Window.GetSnapshot().readyTurnInCount == 5, got " .. tostring(snap and snap.readyTurnInCount))

local win = _G.ForeverQuestPathWindow
local sawReadySummary = false
local function scanFontStrings(root, depth)
    if not root or (depth or 0) > 6 then return end
    for _, reg in ipairs(root._regions or {{}}) do
        if reg.GetText then
            local t = reg:GetText()
            if type(t) == "string" and t:find("Ready Turn-Ins: ", 1, true) then
                sawReadySummary = true
                return
            end
        end
    end
    for _, ch in ipairs(root._children or {{}}) do
        scanFontStrings(ch, (depth or 0) + 1)
    end
end
scanFontStrings(win)
assert(sawReadySummary, "Expected Window Route summary to show 'Ready Turn-Ins: N'")

Mock.ResetMetrics()
local tabs = {{ "dungeons", "rewards", "diag", "route" }}
local maxSwitchMs = 0
for _, tk in ipairs(tabs) do
    local t0 = os.clock()
    ns.Window.ShowTab(tk)
    local ms = (os.clock() - t0) * 1000.0
    if ms > maxSwitchMs then maxSwitchMs = ms end
    assert(ms < 20.0, string.format("Tab switch '%s' with ready turn-ins took %.2fms (>= 20ms)", tk, ms))
end
local mPerf = Mock.GetMetrics()
assert(mPerf.createFrameCalls == 0, "Expected 0 CreateFrame calls on tab switches with ready turn-ins, got " .. tostring(mPerf.createFrameCalls))
assert(mPerf.createRegionCalls == 0, "Expected 0 CreateRegion calls on tab switches with ready turn-ins, got " .. tostring(mPerf.createRegionCalls))

assert(#Mock.errors == 0, "Unexpected Lua errors in ready turn-in test: " .. table.concat(Mock.errors, "; "))
print(string.format("READY_TURNIN_OK maxSwitchMs=%.2fms readyCount=%d", maxSwitchMs, ns.State.GetReadyTurnInCount()))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Ready turn-in optimal placement test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("READY_TURNIN_OK", out)

    def test_realtime_location_reorder_and_friend_meetup_pivot(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})

-- 1. Intra-zone real-time walking reorder + <5u threshold & >8u hysteresis in Wetlands (1437)
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab", realm = "Forever", level = 24,
    raceName = "Gnome", raceFile = "Gnome", raceId = 7,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1437, x = 0.564, y = 0.404,
}})
local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

-- Clear earlier Menethil opener steps so remaining Wetlands loop spans West/Central/East coords
for _, qid in ipairs({{ 463, 472, 305, 281, 284, 285, 286, 98189, 98221, 98197 }}) do
    Mock.CompleteQuest(qid)
end
Mock.AcceptQuest(279, "Claws from the Deep", 22, false)
Mock.AcceptQuest(470, "Digging Through the Ooze", 24, false)
Mock.AcceptQuest(98282, "Alchemical Hazards", 24, false)
Mock.AcceptQuest(484, "Young Crocolisk Skins", 22, false)
ns.State.CheckLivePosition(true)

local sEast, mEast = ns.State.GetCurrentStep()
assert(sEast and sEast.action == "COMPLETE" and sEast.questId == 470,
    "Expected eastern Wetlands (56.4, 40.4) to prioritize nearby COMPLETE 470 (57.4, 35.4), got " .. tostring(sEast and sEast.title))
assert(mEast and mEast.source == "gps_reorder" and sEast.isGpsReordered == true,
    "Expected source=='gps_reorder' and isGpsReordered==true, got " .. tostring(mEast and mEast.source))

-- Tiny move < 5 units (1.1, -2.4 => distSq=6.97 < 25) via timer tick must NOT thrash target
Mock.SetPlayerPosition(1437, 57.5, 38.0)
local firedSmall = Mock.AdvanceTimers(2.0)
assert(firedSmall >= 1, "Expected 1.5s GPS C_Timer.NewTicker to fire")
local sTiny = ns.State.GetCurrentStep()
assert(sTiny and sTiny.action == "COMPLETE" and sTiny.questId == 470,
    "Expected <5u movement not to change COMPLETE 470 target")

-- Midpoint move >= 5 units where saving <= 8 units (at 51.5, 48.5: dist470=14.37, dist98282=10.54, save=3.83 <= 8) keeps hysteresis lock
Mock.SetPlayerPosition(1437, 51.5, 48.5)
Mock.AdvanceTimers(2.0)
local sHyst = ns.State.GetCurrentStep()
assert(sHyst and sHyst.action == "COMPLETE" and sHyst.questId == 470,
    "Expected <=8u hysteresis to hold COMPLETE 470, got " .. tostring(sHyst and sHyst.title))

-- Walk onto 98282 at (49.8, 58.9): saving > 8 units immediately reorders to COMPLETE 98282
Mock.SetPlayerPosition(1437, 49.8, 58.9)
Mock.AdvanceTimers(2.0)
local sSwitched, mSwitched = ns.State.GetCurrentStep()
assert(sSwitched and sSwitched.action == "COMPLETE" and sSwitched.questId == 98282,
    "Expected >8u saving at (49.8, 58.9) to reorder to COMPLETE 98282, got " .. tostring(sSwitched and sSwitched.title))
assert(mSwitched and mSwitched.source == "gps_reorder", "Expected source=='gps_reorder' after walk switch")

-- Verify intra-zone window never promotes over-level quest 464 (Lv28 at MaxLv=+2 -> <=26)
for _, st in ipairs(ns.State.GetUpcomingSteps(15)) do
    assert(st.questId ~= 464, "GPS intra-zone reorder must never violate Max Quest Lv (+2) gate on quest 464")
end

-- 2. UnitOnTaxi(true) flyover guard & non-engaged CAPITAL_ZONES guard
Mock.SetOnTaxi(true)
Mock.SetPlayerPosition(1424, 50.0, 50.0, "Hillsbrad Foothills", true)
Mock.AdvanceTimers(3.0)
local sTaxi1, mTaxi1 = ns.State.GetCurrentStep()
assert(mTaxi1.source ~= "friend_pivot" and sTaxi1.zoneId ~= 267,
    "Expected UnitOnTaxi(true) over Hillsbrad not to pivot route")

Mock.SetPlayerPosition(1431, 73.5, 46.8, "Duskwood", true)
Mock.AdvanceTimers(3.0)
local sTaxi2, mTaxi2 = ns.State.GetCurrentStep()
assert(mTaxi2.source ~= "friend_pivot" and sTaxi2.zoneId ~= 10,
    "Expected UnitOnTaxi(true) flyover Duskwood not to trigger friend_pivot")

Mock.SetOnTaxi(false)
Mock.SetPlayerPosition(1455, 40.0, 55.0, "Ironforge", true)
Mock.AdvanceTimers(2.0)
local sCap, mCap = ns.State.GetCurrentStep()
assert(mCap.source ~= "friend_pivot" and sCap.zoneId ~= 1537,
    "Expected unengaged CAPITAL_ZONES (Ironforge 1537) not to trigger friend_pivot")

-- 3. Off-route Friend-Meetup Chain Adaptation in Duskwood (1431 -> zoneId 10)
Mock.SetPlayerPosition(1431, 73.5, 46.8, "Duskwood", true)
Mock.AcceptQuest(56, "The Night Watch", 24, false)
Mock.AdvanceTimers(2.0)

local sDusk, mDusk = ns.State.GetCurrentStep()
assert(sDusk and sDusk.action == "COMPLETE" and sDusk.questId == 56,
    "Expected friend-accepted quest 56 prioritized first (COMPLETE 56), got " .. tostring(sDusk and sDusk.title))
assert(mDusk and mDusk.source == "friend_pivot",
    "Expected meta.source=='friend_pivot' in Duskwood, got " .. tostring(mDusk and mDusk.source))

local duskUp = ns.State.GetUpcomingSteps(12)
local saw56Turnin, saw57Accept = false, false
for i, st in ipairs(duskUp) do
    if st.action ~= "TRAVEL" then
        assert(st.zoneId == 10, string.format(
            "Expected Duskwood friend_pivot upcoming[%d] (%s q=%s) in zoneId=10 (no cross-zone leak like 96137/377/174), got zoneId=%s",
            i, tostring(st.action), tostring(st.questId), tostring(st.zoneId)))
    end
    assert(st.questId ~= 96137, "Must never inject Alterac starter quest 96137 into Duskwood friend loop")
    if st.questId == 56 and st.action == "TURN_IN" then saw56Turnin = true end
    if st.questId == 57 and st.action == "ACCEPT" then saw57Accept = true end
    assert(st.isFriendChain == true and st.isGpsReordered == true,
        "Expected friend_pivot steps tagged with isFriendChain & isGpsReordered")
end
assert(saw56Turnin and saw57Accept, "Expected Duskwood friend chain progression TURN_IN 56 -> ACCEPT 57")

-- HUD badges & modeLabel + Window Route tab & snapshot synchronization
local hudDusk = ns.HUD.GetViewModel()
assert(hudDusk and hudDusk.modeLabel and hudDusk.modeLabel:find("Friend Chain", 1, true),
    "Expected HUD modeLabel to include 'Friend Chain', got " .. tostring(hudDusk and hudDusk.modeLabel))
assert(hudDusk.current and hudDusk.current.badgesLine
    and hudDusk.current.badgesLine:find("[Friend Chain]", 1, true)
    and hudDusk.current.badgesLine:find("[GPS Reorder]", 1, true),
    "Expected HUD badgesLine '[Friend Chain]' and '[GPS Reorder]', got " .. tostring(hudDusk.current and hudDusk.current.badgesLine))

ns.Window.ShowTab("route")
local snapDusk = ns.Window.GetSnapshot()
assert(snapDusk.locationReorder == true and snapDusk.locationPlanMode == "friend_pivot",
    "Expected Window snapshot locationReorder=true & locationPlanMode='friend_pivot'")

-- SkipCurrentStep / StepBack / MarkCurrentStepComplete GPS history navigation
local beforeTitle = sDusk.title
ns.State.SkipCurrentStep()
local afterSkip = ns.State.GetCurrentStep()
assert(afterSkip and afterSkip.title ~= beforeTitle, "Expected SkipCurrentStep in friend_pivot to advance past " .. tostring(beforeTitle))
ns.State.StepBack()
local afterBack = ns.State.GetCurrentStep()
assert(afterBack and afterBack.title == beforeTitle, "Expected StepBack to restore GPS history step " .. tostring(beforeTitle))
ns.State.MarkCurrentStepComplete()
local afterMark = ns.State.GetCurrentStep()
assert(afterMark and afterMark.title ~= beforeTitle, "Expected MarkCurrentStepComplete in friend_pivot to advance step")

-- 4. Second off-route Friend-Meetup Chain Adaptation in Redridge (1433 -> zoneId 44)
Mock.SetPlayerPosition(1433, 28.4, 49.0, "Redridge Mountains", true)
Mock.AcceptQuest(20, "Blackrock Menace", 21, false)
Mock.AdvanceTimers(2.0)

local sRed, mRed = ns.State.GetCurrentStep()
assert(sRed and sRed.action == "COMPLETE" and sRed.questId == 20 and mRed.source == "friend_pivot",
    "Expected Redridge friend_pivot starting on COMPLETE 20, got " .. tostring(sRed and sRed.title) .. " src=" .. tostring(mRed and mRed.source))
local redUp = ns.State.GetUpcomingSteps(10)
local saw20Turnin = false
for i, st in ipairs(redUp) do
    if st.action ~= "TRAVEL" then
        assert(st.zoneId == 44, string.format(
            "Expected Redridge friend_pivot upcoming[%d] (%s q=%s) in zoneId=44 (no Elwynn 118 or Stormwind 386 leak), got zoneId=%s",
            i, tostring(st.action), tostring(st.questId), tostring(st.zoneId)))
    end
    if st.questId == 20 and st.action == "TURN_IN" then saw20Turnin = true end
end
assert(saw20Turnin, "Expected Redridge TURN_IN 20 follow-up in upcoming steps")

-- 5. Slash commands (/fqp gps, /fqp reorder) & "Auto-Reorder (GPS)" checkbox toggle
Mock.RunSlash("/fqp", "gps off")
assert(ns.Window.GetSnapshot().locationReorder == false, "Expected /fqp gps off to disable locationReorder")
local _, mOff = ns.State.GetCurrentStep()
assert(mOff.source == "route", "Expected /fqp gps off to fall back to route, got " .. tostring(mOff.source))
local hudOff = ns.HUD.GetViewModel()
assert(hudOff.modeLabel and hudOff.modeLabel:find("GPS Off", 1, true),
    "Expected HUD modeLabel 'GPS Off' when locationReorder=false, got " .. tostring(hudOff.modeLabel))

Mock.RunSlash("/fqp", "reorder on")
assert(ns.Window.GetSnapshot().locationReorder == true, "Expected /fqp reorder on to enable locationReorder")
local _, mOn = ns.State.GetCurrentStep()
assert(mOn.source == "friend_pivot", "Expected /fqp reorder on to resume friend_pivot, got " .. tostring(mOn.source))

local win = Mock.FindFrameByName("ForeverQuestPathWindow")
local gpsChk = nil
local function findGpsCheck(frame)
    if frame and frame.label and frame.label.GetText then
        local txt = frame.label:GetText()
        if txt and txt:find("Auto-Reorder (GPS)", 1, true) then
            gpsChk = frame
            return
        end
    end
    for _, ch in ipairs((frame and frame._children) or {{}}) do
        findGpsCheck(ch)
        if gpsChk then return end
    end
end
findGpsCheck(win)
assert(gpsChk ~= nil, "Expected 'Auto-Reorder (GPS)' checkbox in Window")
assert(gpsChk.checked == true, "Expected 'Auto-Reorder (GPS)' checkbox initially checked when enabled")
gpsChk:Click()
assert(ns.Window.GetSnapshot().locationReorder == false and select(2, ns.State.GetCurrentStep()).source == "route",
    "Expected clicking 'Auto-Reorder (GPS)' checkbox off to disable GPS reorder")
gpsChk:Click()
assert(ns.Window.GetSnapshot().locationReorder == true and select(2, ns.State.GetCurrentStep()).source == "friend_pivot",
    "Expected clicking 'Auto-Reorder (GPS)' checkbox back on to restore friend_pivot")

-- 6. 26+ live GPS ticks + cross-zone pivots & cached tab-switch performance / zero allocations
Mock.ResetMetrics()
local tStart = os.clock()
for i = 1, 26 do
    local targetMap = (i % 2 == 0) and 1437 or 1431
    local px = 20.0 + (i % 7) * 9.5
    local py = 25.0 + (i % 5) * 9.0
    Mock.SetPlayerPosition(targetMap, px, py, nil, false)
    Mock.AdvanceTimers(1.6)
end
local avgTickMs = ((os.clock() - tStart) * 1000.0) / 26.0
assert(avgTickMs < 20.0, string.format("Average GPS tick + pivot took %.2fms (>= 20ms)", avgTickMs))

local tabs = {{ "dungeons", "rewards", "diag", "route" }}
local maxTabMs = 0
for _, tk in ipairs(tabs) do
    local t0 = os.clock()
    ns.Window.ShowTab(tk)
    local ms = (os.clock() - t0) * 1000.0
    if ms > maxTabMs then maxTabMs = ms end
    assert(ms < 20.0, string.format("Tab switch '%s' after GPS pivots took %.2fms (>= 20ms)", tk, ms))
end

local perf = Mock.GetMetrics()
assert(perf.createFrameCalls == 0, "Expected 0 CreateFrame calls across 26 GPS ticks + tab switches, got " .. tostring(perf.createFrameCalls))
assert(perf.createRegionCalls == 0, "Expected 0 CreateRegion calls across 26 GPS ticks + tab switches, got " .. tostring(perf.createRegionCalls))
assert(#Mock.errors == 0, "Unexpected Lua errors in Phase 5 GPS test: " .. table.concat(Mock.errors, "; "))
print(string.format("REALTIME_GPS_OK avgTickMs=%.2fms maxTabMs=%.2fms createFrame=%d", avgTickMs, maxTabMs, perf.createFrameCalls))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Realtime GPS reorder & friend-meetup pivot test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("REALTIME_GPS_OK", out)

    def test_proactive_full_chain_expansion_and_progress(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})

-- 1. Lv 24 Alliance Gnome Rogue in Menethil Harbor actively on 279 (Claws from the Deep -> 281 -> 284 -> 285 -> 286 -> 98189)
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab", realm = "Forever", level = 24,
    raceName = "Gnome", raceFile = "Gnome", raceId = 7,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1437, x = 0.083, y = 0.586,
}})
local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

Mock.CompleteQuest(463)
Mock.AcceptQuest(279, "Claws from the Deep", 22, true)
Mock.AdvanceTimers(2.0)

-- BEFORE turning in 279, State.GetCurrentStep + GetUpcomingSteps(15) must proactively expand the full 6-quest chain
local c0 = ns.State.GetCurrentStep()
assert(c0 and c0.action == "TURN_IN" and c0.questId == 279,
    "Expected current step TURN_IN 279 before turn-in, got " .. tostring(c0 and c0.title))
assert(c0.chainPos == 1 and c0.chainTotal == 6 and c0.chainRemainingQuests == 5 and c0.chainRemainingSteps == 10,
    string.format("Expected 279 chain metadata 1/6 (5q/10s left), got %s/%s (%s/%s)",
        tostring(c0.chainPos), tostring(c0.chainTotal), tostring(c0.chainRemainingQuests), tostring(c0.chainRemainingSteps)))
assert(c0.badges and c0.badges.chain and c0.badges.chain.total == 6, "Expected badges.chain populated on current step")

local expectedChain = {{ 281, 284, 285, 286, 98189 }}
local function verifyExpandedChain(steps, label, expect279First)
    local seen = {{}}
    local order = {{}}
    for _, item in ipairs(steps or {{}}) do
        local st = item.step or item
        if st and st.questId then
            seen[st.questId] = seen[st.questId] or {{ actions = {{}}, step = st }}
            seen[st.questId].actions[#seen[st.questId].actions + 1] = st.action
            if #order == 0 or order[#order] ~= st.questId then
                order[#order + 1] = st.questId
            end
        end
    end
    local idxStart = 1
    if expect279First then
        assert(seen[279] and seen[279].step.chainPos == 1 and seen[279].step.chainTotal == 6,
            label .. ": missing 279 (1/6)")
    end
    for k, qid in ipairs(expectedChain) do
        local entry = seen[qid]
        assert(entry ~= nil, label .. ": expected proactive follow-up quest " .. tostring(qid) .. " before 279 turn-in")
        assert(entry.step.chainPos == k + 1 and entry.step.chainTotal == 6,
            string.format("%s: expected quest %d chainPos=%d/6, got %s/%s",
                label, qid, k + 1, tostring(entry.step.chainPos), tostring(entry.step.chainTotal)))
        assert(entry.actions[1] == "ACCEPT" and entry.actions[#entry.actions] == "TURN_IN",
            string.format("%s: expected ACCEPT..TURN_IN lifecycle for quest %d", label, qid))
    end
    -- Strict topological chain order check (281 before 284 before 285 before 286 before 98189)
    local pos = {{}}
    for i, qid in ipairs(order) do
        if not pos[qid] then pos[qid] = i end
    end
    for i = 1, #expectedChain - 1 do
        assert(pos[expectedChain[i]] < pos[expectedChain[i + 1]],
            string.format("%s: expected quest %d before %d", label, expectedChain[i], expectedChain[i + 1]))
    end
end

local upBefore = ns.State.GetUpcomingSteps(15)
verifyExpandedChain(upBefore, "State.GetUpcomingSteps(15)", false)

-- Verify Window Route tab (GPS on & GPS off) proactively shows full chain without missing_prereq hiding
ns.Window.ShowTab("route")
local snapGpsOn = ns.Window.GetSnapshot()
verifyExpandedChain(snapGpsOn.routeRows, "Window.routeRows(GPS=on)", true)

-- Scan rendered Window FontStrings to ensure [Chain X/Y] displays in Route tab rows
local win = Mock.FindFrameByName("ForeverQuestPathWindow")
local sawRouteChainText = false
local function scanText(frame)
    for _, reg in ipairs((frame and frame._regions) or {{}}) do
        if reg and reg.GetText then
            local txt = reg:GetText()
            if txt and (txt:find("[Chain 1/6]", 1, true) or txt:find("[Chain 2/6]", 1, true)) then
                sawRouteChainText = true
                return
            end
        end
    end
    for _, ch in ipairs((frame and frame._children) or {{}}) do
        scanText(ch)
        if sawRouteChainText then return end
    end
end
scanText(win)
assert(sawRouteChainText, "Expected Window Route tab rows to render '[Chain 1/6]' / '[Chain 2/6]' indicator")

Mock.RunSlash("/fqp", "gps off")
ns.Window.ShowTab("route")
local snapGpsOff = ns.Window.GetSnapshot()
verifyExpandedChain(snapGpsOff.routeRows, "Window.routeRows(GPS=off)", true)
Mock.RunSlash("/fqp", "gps on")

-- Verify Scorer.GetDynamicSteps also proactively expands the full 6-quest chain ahead of turn-in
local dynSteps = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.charDB.weights, ns.charDB.toggles, 18)
verifyExpandedChain(dynSteps, "Scorer.GetDynamicSteps(18)", true)

-- Verify HUD Chain progress indicators
local hud0 = ns.HUD.GetViewModel()
assert(hud0 and hud0.current and hud0.current.badgesLine
    and hud0.current.badgesLine:find("[Chain 1/6", 1, true)
    and hud0.current.badgesLine:find("5q/10s left]", 1, true),
    "Expected HUD badgesLine '[Chain 1/6 · 5q/10s left]', got " .. tostring(hud0 and hud0.current and hud0.current.badgesLine))
assert(hud0.current.whyText and hud0.current.whyText:find("Chain 1/6", 1, true),
    "Expected HUD whyText to include 'Chain 1/6', got " .. tostring(hud0 and hud0.current and hud0.current.whyText))
assert(hud0.upcoming and hud0.upcoming[1] and hud0.upcoming[1].left and hud0.upcoming[1].left:find("[Chain 2/6]", 1, true),
    "Expected HUD upcoming[1] to include '[Chain 2/6]', got " .. tostring(hud0.upcoming and hud0.upcoming[1] and hud0.upcoming[1].left))

-- Turning in 279 must immediately advance to the exact predicted next step (ACCEPT 281, Chain 2/6) with zero surprise jump!
local predictedNext = upBefore[1]
Mock.TurnInQuest(279, 1400, 0)
local cAfter = ns.State.GetCurrentStep()
assert(cAfter and cAfter.action == predictedNext.action and cAfter.questId == predictedNext.questId and cAfter.questId == 281,
    string.format("Expected post-turn-in step to match predicted up[1] (%s %s), got (%s %s)",
        tostring(predictedNext.action), tostring(predictedNext.questId), tostring(cAfter and cAfter.action), tostring(cAfter and cAfter.questId)))
assert(cAfter.chainPos == 2 and cAfter.chainTotal == 6,
    string.format("Expected post-turn-in 281 at Chain 2/6, got %s/%s", tostring(cAfter.chainPos), tostring(cAfter.chainTotal)))

-- 2. Second Wetlands chain 276 -> 277 -> 275 + Max Quest Lv gating check
Mock.SetPlayerPosition(1437, 56.4, 40.4, "Wetlands", false)
Mock.AcceptQuest(276, "Tramping Paws", 21, false)
Mock.AdvanceTimers(2.0)

local c276 = ns.State.GetCurrentStep()
assert(c276 and c276.questId == 276 and c276.chainPos == 1 and c276.chainTotal == 3,
    "Expected 276 (Tramping Paws) at Chain 1/3 under MaxLv=+2")
local up276 = ns.State.GetUpcomingSteps(10)
local saw277, saw275 = false, false
for _, st in ipairs(up276) do
    if st.questId == 277 and st.chainPos == 2 and st.chainTotal == 3 then saw277 = true end
    if st.questId == 275 and st.chainPos == 3 and st.chainTotal == 3 then saw275 = true end
end
assert(saw277 and saw275, "Expected 276 chain to proactively expand 277 (2/3) and 275 (3/3) at MaxLv=+2 (<=26)")

-- Lowering MaxLv offset to 0 (cap Lv24) must cleanly exclude Lv26 quest 275 while preserving 276->277 (1/2 -> 2/2)
ns.State.SetMaxLevelOffset(0)
Mock.AdvanceTimers(2.0)
local up276Cap = ns.State.GetUpcomingSteps(12)
local saw276Cap, saw277Cap, saw275Cap = false, false, false
for _, st in ipairs(up276Cap) do
    if st.questId == 276 then
        saw276Cap = true
        assert(st.chainPos == 1 and st.chainTotal == 2, "Expected 276 capped at Chain 1/2 under MaxLv=0, got " .. tostring(st.chainTotal))
    end
    if st.questId == 277 then
        saw277Cap = true
        assert(st.chainPos == 2 and st.chainTotal == 2, "Expected 277 capped at Chain 2/2 under MaxLv=0, got " .. tostring(st.chainTotal))
    end
    if st.questId == 275 then saw275Cap = true end
end
assert(saw276Cap and saw277Cap and (not saw275Cap),
    "Expected MaxLv=0 (<=24) to expand 276->277 (1/2->2/2) while excluding Lv26 quest 275")
ns.State.SetMaxLevelOffset(2)

-- 3. Off-route Friend-Meetup Chain Expansion: Duskwood (56 -> 57, excluding Lv30 58) & Redridge (20 chain)
Mock.SetPlayerPosition(1431, 73.5, 46.8, "Duskwood", true)
Mock.AcceptQuest(56, "The Night Watch", 24, false)
Mock.AdvanceTimers(2.0)

local cDusk, mDusk = ns.State.GetCurrentStep()
assert(cDusk and cDusk.questId == 56 and mDusk.source == "friend_pivot" and cDusk.chainPos == 1 and cDusk.chainTotal == 2,
    string.format("Expected Duskwood friend chain 56 at Chain 1/2 (src=%s c=%s/%s)", tostring(mDusk and mDusk.source), tostring(cDusk and cDusk.chainPos), tostring(cDusk and cDusk.chainTotal)))
local saw57Dusk, saw58Dusk = false, false
for _, st in ipairs(ns.State.GetUpcomingSteps(12)) do
    if st.questId == 57 and st.action == "ACCEPT" and st.chainPos == 2 and st.chainTotal == 2 then saw57Dusk = true end
    if st.questId == 58 then saw58Dusk = true end
    if st.action ~= "TRAVEL" then
        assert(st.zoneId == 10, "Duskwood friend chain must stay in zoneId=10, got " .. tostring(st.zoneId))
    end
end
assert(saw57Dusk and (not saw58Dusk), "Expected Duskwood 56 to proactively expand Lv26 57 (2/2) while excluding Lv30 58")

Mock.SetPlayerPosition(1433, 28.4, 49.0, "Redridge Mountains", true)
Mock.AcceptQuest(20, "Blackrock Menace", 21, false)
Mock.AdvanceTimers(2.0)
local cRed, mRed = ns.State.GetCurrentStep()
assert(cRed and cRed.questId == 20 and mRed.source == "friend_pivot",
    "Expected Redridge friend chain pivot on COMPLETE 20")
local saw20Turnin, saw98387Solo, sawEliteRedSolo = false, false, false
for _, st in ipairs(ns.State.GetUpcomingSteps(25)) do
    if st.questId == 20 and st.action == "TURN_IN" then saw20Turnin = true end
    if st.questId == 98387 then saw98387Solo = true end
    if st.questId == 19 or st.questId == 115 then sawEliteRedSolo = true end
    if st.action ~= "TRAVEL" then
        assert(st.zoneId == 44 or st.questId == 281 or st.questId == 284 or st.questId == 285 or st.questId == 286 or st.questId == 98189,
            "Unexpected zone in Redridge upcoming list: " .. tostring(st.zoneId))
    end
end
assert(saw20Turnin and saw98387Solo and (not sawEliteRedSolo),
    "Expected Redridge solo friend chain to expand TURN_IN 20 + 98387 while excluding elite follow-ups 19/115")

Mock.SetGroup(true, 2, false, true)
Mock.AdvanceTimers(0.5)
local sawEliteRedGroup = false
for _, st in ipairs(ns.State.GetUpcomingSteps(25)) do
    if st.questId == 19 or st.questId == 115 then sawEliteRedGroup = true end
end
assert(sawEliteRedGroup, "Expected Redridge elite follow-ups 19/115 to expand proactively once grouped")
Mock.SetGroup(false, 0, false, true)
Mock.AdvanceTimers(0.5)

-- 4. Allocation-free cached performance guard (< 20ms tab switches, 0 CreateFrame/CreateRegion calls)
Mock.ResetMetrics()
local tabs = {{ "dungeons", "rewards", "diag", "route" }}
local maxTabMs = 0
for _, tk in ipairs(tabs) do
    local t0 = os.clock()
    ns.Window.ShowTab(tk)
    local ms = (os.clock() - t0) * 1000.0
    if ms > maxTabMs then maxTabMs = ms end
    assert(ms < 20.0, string.format("Tab switch '%s' with proactive chain badges took %.2fms (>= 20ms)", tk, ms))
end
local mPerf = Mock.GetMetrics()
assert(mPerf.createFrameCalls == 0, "Expected 0 CreateFrame calls on tab switches, got " .. tostring(mPerf.createFrameCalls))
assert(mPerf.createRegionCalls == 0, "Expected 0 CreateRegion calls on tab switches, got " .. tostring(mPerf.createRegionCalls))
assert(#Mock.errors == 0, "Unexpected Lua errors in proactive chain test: " .. table.concat(Mock.errors, "; "))
print(string.format("PROACTIVE_CHAIN_OK maxTabMs=%.2fms frames=%d", maxTabMs, mPerf.createFrameCalls))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Proactive full-chain expansion test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("PROACTIVE_CHAIN_OK", out)

    def test_terrain_gate_nav_routing_and_zero_gc(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})

Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.108,
    y = 0.596,
}})

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
assert(#Mock.errors == 0, "Unexpected errors loading addon with NavData: " .. table.concat(Mock.errors, "; "))

Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

-- 1. NavData + CSR availability
assert(ns.Nav and ns.Nav.IsAvailable(), "Expected ns.Nav.IsAvailable() == true")
assert(type(ns.NavData) == "table" and (ns.NavData.nodeCount or 0) >= 1091,
    "Expected NavData.nodeCount >= 1091, got " .. tostring(ns.NavData and ns.NavData.nodeCount))

local p = ns.State.GetPlayerInfo()
local data = ns.GetData()

-- 2. Stonetalon Mountains ridge detour (33,76) <-> (56,76) & mounted speed scaling
local rDetour = ns.Nav.QueryPath(406, 33, 76, 406, 56, 76, p, {{ unmountedOnly = true, forceRecalc = true }})
local detourSec = rDetour.totalSec
local detourKind = rDetour.hopKind
local detourLabel = rDetour.hopLabel
local detourGateZ = rDetour.gateZoneId
assert(rDetour.reachable and detourSec > 200.0,
    string.format("Expected Stonetalon ridge detour > 200s, got %s (reachable=%s)", tostring(detourSec), tostring(rDetour.reachable)))
assert(detourKind == "detour" and tostring(detourLabel):find("Stonetalon", 1, true),
    "Expected Stonetalon detour hopKind/label, got " .. tostring(detourKind) .. " / " .. tostring(detourLabel))
assert(detourGateZ == 406, "Expected detour gateZoneId 406, got " .. tostring(detourGateZ))

Mock.SetMounted(true)
local rMounted = ns.Nav.QueryPath(406, 33, 76, 406, 56, 76, p, {{ forceRecalc = true }})
local mountedSec = rMounted.totalSec
assert(rMounted.reachable and mountedSec < detourSec * 0.72,
    string.format("Expected mounted Stonetalon detour < 72%% of unmounted (%.2f vs %.2f)", mountedSec, detourSec))
Mock.SetMounted(false)

-- 3. Hearthstone O(1) shortcut vs cooldown fallback (Ashenvale -> Menethil Harbor)
Mock.SetBindLocation("Menethil Harbor")
local rHearth = ns.Nav.QueryPath(331, 36, 50, 11, 10, 59, p, {{ forceRecalc = true }})
local secHearth, usedHearth, kindHearth = rHearth.totalSec, rHearth.usedHearth, rHearth.hopKind
assert(rHearth.reachable and usedHearth == true and kindHearth == "hearth" and secHearth < 90.0,
    string.format("Expected Hearthstone shortcut <90s, got %.1fs usedHearth=%s kind=%s", secHearth, tostring(usedHearth), tostring(kindHearth)))

Mock.SetHearthCooldown(Mock.state.now, 1800)
local rCooldown = ns.Nav.QueryPath(331, 36, 50, 11, 10, 59, p, {{ forceRecalc = true }})
local secCooldown, usedCooldown = rCooldown.totalSec, rCooldown.usedHearth
assert(rCooldown.reachable and usedCooldown == false and secCooldown > 300.0,
    string.format("Expected Hearth on cooldown to avoid Hearthstone (>300s), got %.1fs usedHearth=%s", secCooldown, tostring(usedCooldown)))

-- 4. Dun Algaz tunnel gate route: Wetlands (11, 53, 72) -> Loch Modan (38, 26, 18)
local rAlgaz = ns.Nav.QueryPath(11, 53, 72, 38, 26, 18, p, {{ forceRecalc = true, allowHearth = false }})
assert(rAlgaz.reachable and rAlgaz.hopKind == "gate" and rAlgaz.gateZoneId == 11 and rAlgaz.gateName == "Dun Algaz tunnels",
    string.format("Expected Dun Algaz tunnels gate hop from Wetlands, got kind=%s zone=%s name=%s",
        tostring(rAlgaz.hopKind), tostring(rAlgaz.gateZoneId), tostring(rAlgaz.gateName)))

-- 5. TAXIMAP_OPENED FP discovery removes +45s/hop undiscovered flight penalty
Mock.OpenTaxiMap({{ {{ name = "Menethil Harbor, Wetlands", status = "CURRENT" }} }})
local rFpUnknown = ns.Nav.QueryPath(11, 10, 59, 40, 56, 52, p, {{ forceRecalc = true, allowHearth = false }})
local fpUnknownSec = rFpUnknown.totalSec
assert(rFpUnknown.reachable and fpUnknownSec >= 550.0,
    "Expected undiscovered multi-hop FP penalty on Menethil->Westfall (>=550s), got " .. tostring(fpUnknownSec))

Mock.OpenTaxiMap({{
    {{ name = "Menethil Harbor, Wetlands", status = "CURRENT" }},
    {{ name = "Thelsamar, Loch Modan", status = "REACHABLE" }},
    {{ name = "Ironforge, Dun Morogh", status = "REACHABLE" }},
    {{ name = "Stormwind, Elwynn Forest", status = "REACHABLE" }},
    {{ name = "Sentinel Hill, Westfall", status = "REACHABLE" }},
}})
local rFpKnown = ns.Nav.QueryPath(11, 10, 59, 40, 56, 52, p, {{ forceRecalc = true, allowHearth = false }})
local fpKnownSec = rFpKnown.totalSec
assert(rFpKnown.reachable and fpKnownSec < fpUnknownSec - 100.0,
    string.format("Expected discovering FPs via TAXIMAP_OPENED to remove penalties (%.1fs -> %.1fs)", fpUnknownSec, fpKnownSec))

-- 6. Multi-hop gate Waypoint pinning, zone-crossing auto-handoff & /fqp gate [off|on]
Mock.SetPlayerPosition(1440, 0.36, 0.50, "Ashenvale", true)
Mock.AdvanceTimers(2.5)
local stepST = {{
    questId = 999001,
    action = "TRAVEL",
    title = "Go to Stonetalon",
    zoneId = 406,
    zoneName = "Stonetalon Mountains",
    x = 46,
    y = 60,
    npcName = "Scout",
}}
local wpGate = ns.Waypoint.SetForStep(stepST, {{ announce = false }})
assert(wpGate.ok and wpGate.isGateHop == true and wpGate.gateName == "Talondeep Path tunnel" and wpGate.uiMapId == 1440,
    string.format("Expected Talondeep Path tunnel gate hop in Ashenvale (1440), got ok=%s gate=%s map=%s",
        tostring(wpGate.ok), tostring(wpGate.gateName), tostring(wpGate.uiMapId)))

-- Cross zone boundary into Stonetalon Mountains (1442) -> auto-handoff to final destination
Mock.SetPlayerPosition(1442, 0.78, 0.43, "Stonetalon Mountains", true)
Mock.AdvanceTimers(2.5)
local wpFinal = ns.Waypoint.SetForStep(stepST, {{ announce = false }})
assert(wpFinal.ok and not wpFinal.isGateHop and wpFinal.uiMapId == 1442
    and math.abs(wpFinal.x * 100 - 46.0) < 0.1 and math.abs(wpFinal.y * 100 - 60.0) < 0.1,
    string.format("Expected final Stonetalon pin (46,60) after zone crossing, got isGate=%s map=%s (%.2f,%.2f)",
        tostring(wpFinal.isGateHop), tostring(wpFinal.uiMapId), (wpFinal.x or 0) * 100, (wpFinal.y or 0) * 100))

-- /fqp gate off disables gate waypoints; /fqp gate on restores them
SlashCmdList.FOREVERQUESTPATH("gate off")
assert(ns.db.navGateWaypoints == false, "Expected /fqp gate off to set ns.db.navGateWaypoints=false")
Mock.SetPlayerPosition(1440, 0.36, 0.50, "Ashenvale", true)
Mock.AdvanceTimers(2.5)
local wpGateOff = ns.Waypoint.SetForStep(stepST, {{ announce = false }})
assert(wpGateOff.ok and not wpGateOff.isGateHop and wpGateOff.uiMapId == 1442,
    "Expected /fqp gate off to pin final destination directly, got map=" .. tostring(wpGateOff.uiMapId))

SlashCmdList.FOREVERQUESTPATH("gate on")
assert(ns.db.navGateWaypoints == true, "Expected /fqp gate on to set ns.db.navGateWaypoints=true")
local wpGateOn = ns.Waypoint.SetForStep(stepST, {{ announce = false }})
assert(wpGateOn.ok and wpGateOn.isGateHop == true and wpGateOn.uiMapId == 1440,
    "Expected /fqp gate on to restore Talondeep gate hop")

-- 7. HUD.GetViewModel non-mutating ETA & travelNote overlay
SlashCmdList.FOREVERQUESTPATH("gps off")
Mock.SetPlayerPosition(1432, 0.26, 0.18, "Loch Modan", true)
Mock.AdvanceTimers(2.5)
local curStep = ns.State.GetCurrentStep()
assert(curStep ~= nil, "Expected current step for HUD non-mutation test")
local origTravelNote = curStep.travelNote
local origEstMinutes = curStep.estMinutes
local vm = ns.HUD.GetViewModel()
assert(vm and vm.current and type(vm.current.whereLine) == "string" and vm.current.whereLine:find("~", 1, true),
    "Expected HUD viewModel whereLine to contain Nav ETA")
assert(type(vm.current.travelNote) == "string" and vm.current.travelNote ~= "",
    "Expected HUD viewModel travelNote populated across zones")
assert(curStep.travelNote == origTravelNote and curStep.estMinutes == origEstMinutes,
    "HUD.GetViewModel must not mutate underlying step.travelNote or step.estMinutes")

-- 8. Outward FindNearestEligibleQuests sorted ordering + zero-GC performance budgets
local nearPool, nearCount = ns.Nav.FindNearestEligibleQuests(331, 36, 50, data, ns.State.GetPlayerInfo(), {{ maxResults = 8, maxSec = 1800 }})
assert(nearCount >= 2 and type(nearPool) == "table", "Expected >=2 nearest eligible quests from Ashenvale, got " .. tostring(nearCount))
for i = 2, nearCount do
    assert(nearPool[i - 1].travelSec <= nearPool[i].travelSec + 1e-6,
        string.format("FindNearestEligibleQuests must be sorted ascending (idx %d: %.2f > %.2f)",
            i, nearPool[i - 1].travelSec, nearPool[i].travelSec))
end

local tB0 = os.clock()
for i = 1, 20 do
    ns.Nav.QueryPath(331, 30 + (i % 15), 40 + (i % 20), 406, 56, 76, p, {{ forceRecalc = true }})
end
local bidirAvgMs = ((os.clock() - tB0) * 1000.0) / 20.0
assert(bidirAvgMs < 0.8, string.format("Expected uncached Bidir Dijkstra < 0.8ms avg, got %.4fms", bidirAvgMs))

for _, tk in ipairs({{ "route", "dungeons", "rewards", "decisions" }}) do ns.Window.ShowTab(tk) end
Mock.ResetMetrics()
local tG0 = os.clock()
for i = 1, 30 do
    Mock.SetPlayerPosition(1442, 0.33 + (i % 10) * 0.005, 0.76 - (i % 6) * 0.003, "Stonetalon Mountains", false)
    Mock.AdvanceTimers(2.5)
end
local gpsAvgMs = ((os.clock() - tG0) * 1000.0) / 30.0
assert(gpsAvgMs < 2.0, string.format("Expected 30 GPS_MOVED ticks < 2.0ms/tick, got %.4fms", gpsAvgMs))

local maxTabMs = 0
for _, tk in ipairs({{ "route", "dungeons", "rewards", "decisions" }}) do
    local t0 = os.clock()
    ns.Window.ShowTab(tk)
    local ms = (os.clock() - t0) * 1000.0
    if ms > maxTabMs then maxTabMs = ms end
    assert(ms < 20.0, string.format("Tab '%s' took %.2fms (>=20ms)", tk, ms))
end
local mPerf = Mock.GetMetrics()
assert(mPerf.createFrameCalls == 0, "Expected 0 CreateFrame calls during Nav/GPS/tabs, got " .. tostring(mPerf.createFrameCalls))
assert(mPerf.createRegionCalls == 0, "Expected 0 CreateRegion calls during Nav/GPS/tabs, got " .. tostring(mPerf.createRegionCalls))

-- 9. Graceful fallback when ns.NavData is nil
ns.NavData = nil
ns.Nav.Reset()
assert(ns.Nav.IsAvailable() == false, "Expected Nav.IsAvailable() == false when NavData == nil")
local fallbackSec = ns.Nav.GetTravelSeconds(406, 33, 76, 406, 56, 76, p)
assert(fallbackSec == nil, "Expected nil travelSeconds when NavData == nil, got " .. tostring(fallbackSec))
local fallbackVm = ns.HUD.GetViewModel()
assert(fallbackVm and fallbackVm.current ~= nil, "Expected HUD.GetViewModel() to work smoothly without NavData")
assert(#Mock.errors == 0, "Unexpected Lua errors in Phase 7 Nav test: " .. table.concat(Mock.errors, "; "))

print(string.format("PHASE7_NAV_OK bidirAvgMs=%.4f gpsAvgMs=%.4f maxTabMs=%.3f frames=%d fpDelta=%.1f",
    bidirAvgMs, gpsAvgMs, maxTabMs, mPerf.createFrameCalls, fpUnknownSec - fpKnownSec))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Phase 7 terrain/gate Nav test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("PHASE7_NAV_OK", out)

    def test_idle_stationary_cpu_and_significant_location_gating(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})

Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.108,
    y = 0.596,
}})

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
assert(#Mock.errors == 0, "Unexpected load errors: " .. table.concat(Mock.errors, "; "))

Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.2)
ns.Window.ShowTab("route")
Mock.AdvanceTimers(0.2)
Mock.OpenTaxiMap({{ {{ name = "Menethil Harbor, Wetlands", status = "CURRENT" }} }})
Mock.AdvanceTimers(0.2)

local stateUpdatedCount = 0
ns.RegisterCallback("STATE_UPDATED", function()
    stateUpdatedCount = stateUpdatedCount + 1
end)

ns.State.ResetIdlePerfStats()
ns.Nav.ResetStats()
Mock.ResetMetrics()
stateUpdatedCount = 0

-- 1. 200 stationary 1.5s ticker ticks -> 0 Nav solves, 0 STATE_UPDATED, <0.05ms per tick
local tIdle0 = os.clock()
for _ = 1, 200 do
    Mock.AdvanceTimers(1.5)
end
local idleMs = (os.clock() - tIdle0) * 1000.0
local idlePerTickMs = idleMs / 200.0
assert(idlePerTickMs < 0.05,
    string.format("Expected stationary idle tick < 0.05ms, got %.5fms", idlePerTickMs))

-- 2. Noisy unchanged WoW events -> filtered cleanly with 0 Nav solves & 0 STATE_UPDATED
local tNoisy0 = os.clock()
for _ = 1, 50 do
    Mock.FireEvent("QUEST_LOG_UPDATE")
    Mock.FireEvent("ZONE_CHANGED_INDOORS")
    Mock.FireEvent("ZONE_CHANGED")
    Mock.FireEvent("SKILL_LINES_CHANGED")
    Mock.FireEvent("CHAT_MSG_SKILL", "Your skill in Defense has increased to 120.")
    Mock.FireEvent("TAXIMAP_OPENED")
    Mock.AdvanceTimers(0.1)
end
local noisyMs = (os.clock() - tNoisy0) * 1000.0

-- 3. Indoor nil position flicker & sub-threshold micro-jitter (+/- 0.4 coords) -> suppressed
local tFlicker0 = os.clock()
for i = 1, 25 do
    Mock.state.character.x = nil
    Mock.state.character.y = nil
    Mock.FireEvent("ZONE_CHANGED_INDOORS")
    Mock.AdvanceTimers(1.5)
    Mock.state.character.x = 0.108 + ((i % 3) - 1) * 0.004
    Mock.state.character.y = 0.596 + ((i % 2) - 0.5) * 0.004
    Mock.FireEvent("ZONE_CHANGED_INDOORS")
    Mock.AdvanceTimers(1.5)
end
local flickerMs = (os.clock() - tFlicker0) * 1000.0

local stIdle = ns.State.GetIdlePerfStats()
local nvIdle = ns.Nav.GetStats()
local mIdle = Mock.GetMetrics()
assert(stateUpdatedCount == 0, "Expected 0 STATE_UPDATED during idle/noisy/flicker phases, got " .. tostring(stateUpdatedCount))
assert(stIdle.refreshExecuted == 0, "Expected 0 executed refreshes during idle/noisy/flicker, got " .. tostring(stIdle.refreshExecuted))
assert(stIdle.refreshSkipped >= 250, "Expected >=250 skipped noisy refreshes, got " .. tostring(stIdle.refreshSkipped))
assert(stIdle.gpsTickTriggers == 0, "Expected 0 gpsTickTriggers while idle/jittering, got " .. tostring(stIdle.gpsTickTriggers))
assert(nvIdle.queryCalls == 0 and nvIdle.bidirSolves == 0 and nvIdle.singleFieldCalls == 0
    and nvIdle.nearestCalls == 0 and nvIdle.floodSolves == 0,
    string.format("Expected 0 Nav solves during idle/noisy/flicker, got qp=%d sf=%d near=%d flood=%d",
        nvIdle.queryCalls, nvIdle.singleFieldCalls, nvIdle.nearestCalls, nvIdle.floodSolves))
assert(mIdle.createFrameCalls == 0 and mIdle.createRegionCalls == 0,
    "Expected 0 frame/region allocations during idle/noisy/flicker")

-- 4a. Genuine large move (>= 160yd) triggers immediately
Mock.SetPlayerPosition(1437, 0.28, 0.46, "Wetlands", false)
Mock.AdvanceTimers(1.5)
local trigLarge = ns.State.GetIdlePerfStats().gpsTickTriggers
assert(trigLarge == 1 and stateUpdatedCount == 1,
    string.format("Expected large move (>=160yd) to trigger immediately (trig=%d upd=%d)", trigLarge, stateUpdatedCount))

-- 4b. Cooldown-gated moderate cell crossing in Redridge (34->39.1 ~114yd, >=95yd & <160yd, crosses cell at 35)
Mock.SetPlayerPosition(1433, 0.25, 0.50, "Redridge Mountains", true)
Mock.AdvanceTimers(0.2)
ns.State.ResetIdlePerfStats()
stateUpdatedCount = 0

Mock.SetPlayerPosition(1433, 0.34, 0.50, "Redridge Mountains", false) -- large jump (~200.7yd) arms cooldown
Mock.AdvanceTimers(1.5)
local trigArm = ns.State.GetIdlePerfStats().gpsTickTriggers
assert(trigArm == 1, "Expected large Redridge move to trigger & start cooldown, got " .. tostring(trigArm))

Mock.SetPlayerPosition(1433, 0.391, 0.50, "Redridge Mountains", false) -- cell crossing (~114yd) inside 4.5s cooldown -> gated
Mock.AdvanceTimers(1.5)
local trigCooldown = ns.State.GetIdlePerfStats().gpsTickTriggers
assert(trigCooldown == 1, "Expected cell crossing inside 4.5s cooldown to stay gated, got " .. tostring(trigCooldown))

Mock.AdvanceTimers(4.0) -- past 4.5s cooldown
Mock.SetPlayerPosition(1433, 0.392, 0.50, "Redridge Mountains", false)
Mock.AdvanceTimers(1.5)
local trigAfterCooldown = ns.State.GetIdlePerfStats().gpsTickTriggers
assert(trigAfterCooldown == 2,
    "Expected cumulative cell crossing after 4.5s cooldown to trigger, got " .. tostring(trigAfterCooldown))

-- 4c. Real quest objective update -> QUEST_LOG_UPDATE must execute
Mock.AcceptQuest(279, "Claws from the Deep", 22, false)
Mock.AdvanceTimers(0.2)
local updBeforeObj = stateUpdatedCount
Mock.SetQuestObjectivesComplete(279, true)
Mock.FireEvent("QUEST_LOG_UPDATE")
Mock.AdvanceTimers(0.2)
assert(stateUpdatedCount > updBeforeObj, "Expected QUEST_LOG_UPDATE after objective change to trigger STATE_UPDATED")

-- 4d. Real skill rank-up -> SKILL_LINES_CHANGED must execute
local updBeforeSkill = stateUpdatedCount
Mock.SetSkills({{ {{ name = "Skinning", rank = 150, maxRank = 225 }} }}, true)
Mock.AdvanceTimers(0.2)
assert(stateUpdatedCount > updBeforeSkill, "Expected real skill change on SKILL_LINES_CHANGED to trigger STATE_UPDATED")

-- 4e. Real new taxi node discovery -> TAXIMAP_OPENED must execute
local updBeforeTaxi = stateUpdatedCount
Mock.OpenTaxiMap({{
    {{ name = "Menethil Harbor, Wetlands", status = "CURRENT" }},
    {{ name = "Thelsamar, Loch Modan", status = "REACHABLE" }},
}})
Mock.AdvanceTimers(0.2)
assert(stateUpdatedCount > updBeforeTaxi, "Expected new flight node on TAXIMAP_OPENED to trigger STATE_UPDATED")

-- 4f. /fqp gps stats outputs GPS gating & Nav Dijkstra counters
local chatBefore = #Mock.state.chatMessages
Mock.RunSlash("/fqp", "gps stats")
local chatAfter = #Mock.state.chatMessages
assert(chatAfter >= chatBefore + 2, "Expected /fqp gps stats to print 2 summary lines")
local stat1 = tostring(Mock.state.chatMessages[chatBefore + 1] or "")
local stat2 = tostring(Mock.state.chatMessages[chatBefore + 2] or "")
assert(stat1:find("GPS gating", 1, true) and stat1:find("triggers", 1, true),
    "Unexpected first /fqp gps stats line: " .. stat1)
assert(stat2:find("Nav Dijkstra", 1, true) and stat2:find("QueryPath", 1, true),
    "Unexpected second /fqp gps stats line: " .. stat2)

assert(#Mock.errors == 0, "Unexpected Lua errors in Phase 8 idle gating test: " .. table.concat(Mock.errors, "; "))
print(string.format("PHASE8_IDLE_GATING_OK idlePerTickMs=%.5f noisyMs=%.2f flickerMs=%.2f skipped=%d trig=%d",
    idlePerTickMs, noisyMs, flickerMs, stIdle.refreshSkipped, trigAfterCooldown))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Phase 8 idle/stationary gating test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("PHASE8_IDLE_GATING_OK", out)

    def test_loot_and_quest_progress_cpu_gating(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})

Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.083,
    y = 0.586,
}})

-- Seed 2,000 completed quests ahead of login
for i = 1, 2000 do
    Mock.state.completedQuests[900000 + i] = true
end

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
assert(#Mock.errors == 0, "Unexpected load errors: " .. table.concat(Mock.errors, "; "))

Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)
ns.Window.ShowTab("route")
Mock.AdvanceTimers(0.25)

-- Populate 20 active quests in log (real 279 + 19 fillers)
Mock.CompleteQuest(463)
Mock.AcceptQuest(279, "Claws from the Deep", 22, false)
for qk = 1, 19 do
    Mock.AcceptQuest(950000 + qk, "Active Quest " .. qk, 22, false)
end
Mock.AdvanceTimers(0.30)

local stateUpdatedCount = 0
ns.RegisterCallback("STATE_UPDATED", function()
    stateUpdatedCount = stateUpdatedCount + 1
end)

ns.State.ResetIdlePerfStats()
ns.Nav.ResetStats()
Mock.ResetMetrics()
stateUpdatedCount = 0

-- 1. 100 unchanged loot QUEST_LOG_UPDATE bursts (20 active, 2000 completed) -> 0 bulk/obj APIs, 0 Nav solves, 0 STATE_UPDATED
local tLoot0 = os.clock()
for _ = 1, 100 do
    Mock.FireEvent("QUEST_LOG_UPDATE")
    Mock.AdvanceTimers(0.25)
end
local lootMs = (os.clock() - tLoot0) * 1000.0
local mLoot = Mock.GetMetrics()
local stLoot = ns.State.GetIdlePerfStats()
local nvLoot = ns.Nav.GetStats()
assert(stateUpdatedCount == 0, "Expected 0 STATE_UPDATED on unchanged loot bursts, got " .. tostring(stateUpdatedCount))
assert(stLoot.refreshExecuted == 0 and stLoot.refreshSkipped >= 100,
    string.format("Expected 0 executed / >=100 skipped loot refreshes, got %d/%d", stLoot.refreshExecuted, stLoot.refreshSkipped))
assert(mLoot.getAllCompletedQuestIDsCalls == 0 and mLoot.getQuestsCompletedCalls == 0 and mLoot.getQuestObjectivesCalls == 0,
    string.format("Expected 0 GetAllCompletedQuestIDs/GetQuestsCompleted/GetQuestObjectives on loot QLU, got %d/%d/%d",
        mLoot.getAllCompletedQuestIDsCalls, mLoot.getQuestsCompletedCalls, mLoot.getQuestObjectivesCalls))
assert(nvLoot.queryCalls == 0 and nvLoot.bidirSolves == 0 and nvLoot.singleFieldCalls == 0
    and nvLoot.nearestCalls == 0 and nvLoot.floodSolves == 0,
    "Expected 0 Nav solves during unchanged loot bursts")
assert(mLoot.createFrameCalls == 0 and mLoot.createRegionCalls == 0,
    "Expected 0 frame/region allocations during unchanged loot bursts")

-- 2a. Partial quest item progress 1/10..9/10 while isComplete == false -> <0.1ms/loot, 0 Nav solves, 0 generation/STATE_UPDATED churn
local genBeforePartial = ns.State.GetQuestStateGeneration()
local tPart0 = os.clock()
for cnt = 1, 9 do
    Mock.SetQuestObjectiveProgress(279, 1, cnt, 10, false)
    Mock.AdvanceTimers(0.25)
end
local partMs = (os.clock() - tPart0) * 1000.0
local partPerMs = partMs / 9.0
local genAfterPartial = ns.State.GetQuestStateGeneration()
local mPart = Mock.GetMetrics()
local nvPart = ns.Nav.GetStats()
assert(partPerMs < 0.1, string.format("Expected partial loot < 0.1ms/drop, got %.4fms", partPerMs))
assert(stateUpdatedCount == 0, "Expected 0 STATE_UPDATED during 1/10..9/10 partial progress, got " .. tostring(stateUpdatedCount))
assert(genAfterPartial == genBeforePartial,
    string.format("Expected questStateGeneration intact during partial progress (%d vs %d)", genAfterPartial, genBeforePartial))
assert(mPart.getAllCompletedQuestIDsCalls == 0 and mPart.getQuestObjectivesCalls == 0,
    "Expected 0 GetAllCompletedQuestIDs/GetQuestObjectives during partial progress")
assert(nvPart.queryCalls == 0 and nvPart.singleFieldCalls == 0 and nvPart.nearestCalls == 0 and nvPart.floodSolves == 0,
    "Expected 0 Nav solves during partial progress")

-- 2b. 10/10 completion flip (isComplete false -> true) -> immediate STATE_UPDATED & ready TURN_IN promotion
Mock.SetQuestObjectiveProgress(279, 1, 10, 10, true)
Mock.AdvanceTimers(0.25)
local stepDone = ns.State.GetCurrentStep()
assert(stateUpdatedCount == 1, "Expected 1 STATE_UPDATED on 10/10 completion flip, got " .. tostring(stateUpdatedCount))
assert(ns.State.GetQuestStateGeneration() > genAfterPartial, "Expected questStateGeneration bump on 10/10 completion")
assert(stepDone and stepDone.action == "TURN_IN" and stepDone.questId == 279,
    string.format("Expected quest 279 promoted to TURN_IN on 10/10, got %s %s",
        tostring(stepDone and stepDone.action), tostring(stepDone and stepDone.questId)))

-- 3a. True async Frame-0 loot+skinning burst via 0.20s trailing-edge coalescing
local origAfter = _G.C_Timer.After
local pendingAfter = {{}}
_G.C_Timer.After = function(delay, cb)
    table.insert(pendingAfter, {{ delay = delay, cb = cb }})
end
local function flushAsyncAfter()
    while #pendingAfter > 0 do
        local batch = pendingAfter
        pendingAfter = {{}}
        for _, item in ipairs(batch) do
            if type(item.cb) == "function" then item.cb() end
        end
    end
end

-- Reset 279 incomplete so we can test unchanged burst AND concurrent completion+skill rank-up burst
Mock.SetQuestObjectiveProgress(279, 1, 9, 10, false)
flushAsyncAfter()
ns.State.ResetIdlePerfStats()
ns.Nav.ResetStats()
stateUpdatedCount = 0

local preBurstCalls = ns.State.GetIdlePerfStats().refreshCalls
local tF0 = os.clock()
for _ = 1, 8 do
    Mock.FireEvent("QUEST_LOG_UPDATE")
end
Mock.FireEvent("CHAT_MSG_SKILL", "Your skill in Skinning has increased to 185.")
Mock.FireEvent("SKILL_LINES_CHANGED")
local f0Ms = (os.clock() - tF0) * 1000.0
local postF0Calls = ns.State.GetIdlePerfStats().refreshCalls
assert(postF0Calls == preBurstCalls,
    string.format("Expected 0 State.Refresh executions on Frame 0 during burst, got %d", postF0Calls - preBurstCalls))
assert(#pendingAfter == 1 and math.abs((pendingAfter[1].delay or 0) - 0.20) < 1e-6,
    string.format("Expected 1 coalesced 0.20s timer queued on Frame 0, got count=%d delay=%s",
        #pendingAfter, tostring(pendingAfter[1] and pendingAfter[1].delay)))
assert(f0Ms < 0.5, string.format("Expected Frame-0 burst queueing < 0.5ms, got %.4fms", f0Ms))

flushAsyncAfter()
local postFlushStats = ns.State.GetIdlePerfStats()
local postFlushNav = ns.Nav.GetStats()
assert(postFlushStats.refreshExecuted == 0 and stateUpdatedCount == 0,
    "Expected coalesced unchanged loot+skinning flush to skip cleanly with 0 STATE_UPDATED")
assert(postFlushNav.queryCalls == 0 and postFlushNav.singleFieldCalls == 0,
    "Expected 0 Nav solves on coalesced unchanged loot+skinning flush")

-- 3b. Concurrent real completion + real skill rank-up inside the SAME Frame-0 burst must NOT lose SKILL_LINES_CHANGED (#130)
Mock.SetQuestObjectiveProgress(279, 1, 10, 10, true) -- queues QUEST_LOG_UPDATE (not flushed yet)
Mock.SetSkills({{ {{ name = "Skinning", rank = 205, maxRank = 225 }} }}, true) -- queues SKILL_LINES_CHANGED
assert(stateUpdatedCount == 0 and ns.State.GetPlayerInfo().skinningRank == 185,
    "Expected both Frame-0 updates deferred until 0.20s coalesced flush")
assert(#pendingAfter == 1, "Expected single coalesced trailing timer for combined burst")
flushAsyncAfter()
assert(stateUpdatedCount >= 1 and ns.State.GetPlayerInfo().skinningRank == 205,
    string.format("Expected concurrent burst flush to apply both quest completion and Skinning 205, got upd=%d skinRank=%s",
        stateUpdatedCount, tostring(ns.State.GetPlayerInfo().skinningRank)))
local stepCombo = ns.State.GetCurrentStep()
assert(stepCombo and stepCombo.action == "TURN_IN" and stepCombo.questId == 279,
    "Expected quest 279 promoted after combined burst flush")

_G.C_Timer.After = origAfter

-- 3c. Stale/unlogged QUEST_ACCEPTED expires cleanly after 1 scan
ns.State.ResetIdlePerfStats()
Mock.FireEvent("QUEST_ACCEPTED", 987654)
local execAccept = ns.State.GetIdlePerfStats().refreshExecuted
assert(ns.State.GetActiveQuests()[987654] ~= nil, "Expected pending entry immediately after QUEST_ACCEPTED")
Mock.FireEvent("QUEST_LOG_UPDATE") -- scan 1 expires stale 987654 since scans > 1
local execAfterScan1 = ns.State.GetIdlePerfStats().refreshExecuted
assert(ns.State.GetActiveQuests()[987654] == nil, "Expected stale unlogged QUEST_ACCEPTED to expire after 1 scan")
Mock.FireEvent("QUEST_LOG_UPDATE") -- subsequent QLU must skip cleanly
local execAfterScan2 = ns.State.GetIdlePerfStats().refreshExecuted
local skipAfterScan2 = ns.State.GetIdlePerfStats().refreshSkipped
assert(execAfterScan2 == execAfterScan1 and skipAfterScan2 >= 1 and execAccept >= 1,
    string.format("Expected subsequent QLU after stale expiry to skip (exec1=%d exec2=%d skip=%d)",
        execAfterScan1, execAfterScan2, skipAfterScan2))

local mEnd = Mock.GetMetrics()
assert(mEnd.createFrameCalls == 0 and mEnd.createRegionCalls == 0,
    "Expected 0 frame/region allocations across Phase 9 loot regression test")
assert(#Mock.errors == 0, "Unexpected Lua errors in Phase 9 loot test: " .. table.concat(Mock.errors, "; "))

print(string.format("PHASE9_LOOT_OK loot100Ms=%.2f partPerMs=%.4f f0Ms=%.4f skinRank=%d",
    lootMs, partPerMs, f0Ms, ns.State.GetPlayerInfo().skinningRank))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Phase 9 loot & quest-progress CPU gating test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("PHASE9_LOOT_OK", out)

    def test_dead_ghost_quest_location_gating(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})

Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.564,
    y = 0.404,
}})
Mock.SetPlayerPosition(1437, 0.564, 0.404, "Greenwarden's Grove", false)

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
assert(#Mock.errors == 0, "Unexpected load errors: " .. table.concat(Mock.errors, "; "))

Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.CompleteQuest(463)
Mock.AcceptQuest(276, "Tramping Paws", 21, false)
Mock.AdvanceTimers(0.25)
Mock.SetHearthCooldown(Mock.state.now, 1800)

local stepAlive, metaAlive = ns.State.GetCurrentStep()
local pAlive = ns.State.GetPlayerInfo()
assert(stepAlive and stepAlive.questId == 276 and metaAlive.source == "gps_reorder",
    string.format("Expected alive step 276 (gps_reorder) at Greenwarden's Grove, got %s (%s)",
        tostring(stepAlive and stepAlive.questId), tostring(metaAlive and metaAlive.source)))
assert(pAlive.isDead == false and pAlive.zoneId == 11 and pAlive.subZoneName == "Greenwarden's Grove")
local wpAliveBefore = Mock.state.userWaypoint
assert(wpAliveBefore and wpAliveBefore.uiMapID == 1437, "Expected initial alive waypoint pinned in Wetlands")

local stateUpd = 0
local stepChg = 0
ns.RegisterCallback("STATE_UPDATED", function() stateUpd = stateUpd + 1 end)
ns.RegisterCallback("STEP_CHANGED", function() stepChg = stepChg + 1 end)

ns.Nav.ResetStats()
ns.State.ResetIdlePerfStats()
Mock.ResetMetrics()
stateUpd, stepChg = 0, 0

-- 1. Die at quest spot & release spirit (PLAYER_ALIVE while UnitIsGhost == true must stay frozen)
Mock.SetDead(true, false, true)
assert(ns.IsPlayerDeadOrGhost() == true)
Mock.ReleaseSpirit(1437, 0.145, 0.432, "Baradin Bay")
assert(UnitIsGhost("player") == true and ns.IsPlayerDeadOrGhost() == true)

-- 2. Move ghost 300+ yd across graveyard zones & subzones with 1.5s ticks, events, taxi & /fqp gps
local ghostHops = {{
    {{ 1437, 0.50, 0.78, "Dun Algaz" }},
    {{ 1432, 0.25, 0.50, "Thelsamar" }},
    {{ 1431, 0.20, 0.45, "Raven Hill" }},
}}
for _, gh in ipairs(ghostHops) do
    Mock.SetPlayerPosition(gh[1], gh[2], gh[3], gh[4], false)
    Mock.FireEvent("ZONE_CHANGED_NEW_AREA")
    Mock.FireEvent("ZONE_CHANGED")
    Mock.FireEvent("ZONE_CHANGED_INDOORS")
    for _ = 1, 5 do
        Mock.AdvanceTimers(1.5)
    end
end
Mock.OpenTaxiMap({{ {{ name = "Darkshire, Duskwood", status = "CURRENT" }} }})
Mock.RunSlash("/fqp", "gps")

local wpDeadAuto = ns.Waypoint.SetForStep(stepAlive, {{ skipIfSame = true }})
assert(wpDeadAuto and wpDeadAuto.ok == false and wpDeadAuto.reason == "player_dead",
    "Expected automatic SetForStep while dead/ghost to skip with reason='player_dead'")
-- Manual /fqp pin while dead pins quest target directly without graveyard Nav hops
Mock.RunSlash("/fqp", "pin")

local pGhost = ns.State.GetPlayerInfo()
local stepGhost, metaGhost = ns.State.GetCurrentStep()
local nvGhost = ns.Nav.GetStats()
local stGhost = ns.State.GetIdlePerfStats()
local mGhost = Mock.GetMetrics()
assert(pGhost.isDead == true and pGhost.zoneId == 11 and pGhost.subZoneName == "Greenwarden's Grove"
    and math.abs(pGhost.x - 56.4) < 0.05 and math.abs(pGhost.y - 40.4) < 0.05,
    "Expected player location frozen at Greenwarden's Grove (56.4, 40.4) while ghost")
assert(stepGhost and stepGhost.questId == 276 and metaGhost.source == "gps_reorder",
    "Expected quest order/step frozen at Wetlands 276 while wandering Duskwood graveyard")
assert(stateUpd == 0 and stepChg == 0 and stGhost.gpsTickTriggers == 0 and stGhost.deadLocationSkipped >= 16,
    string.format("Expected 0 STATE_UPDATED/STEP_CHANGED/gpsTriggers & >=16 dead skips, got upd=%d chg=%d trig=%d deadSkip=%d",
        stateUpd, stepChg, stGhost.gpsTickTriggers, stGhost.deadLocationSkipped))
assert(nvGhost.queryCalls == 0 and nvGhost.bidirSolves == 0 and nvGhost.singleFieldCalls == 0
    and nvGhost.nearestCalls == 0 and nvGhost.floodSolves == 0,
    "Expected 0 Nav/Dijkstra solves while dead/ghost (including /fqp gps and /fqp pin)")
assert(mGhost.createFrameCalls == 0 and mGhost.createRegionCalls == 0)

-- Check /fqp gps stats mentions dead skipped
local chatBefore = #Mock.state.chatMessages
Mock.RunSlash("/fqp", "gps stats")
local statLine = tostring(Mock.state.chatMessages[chatBefore + 1] or "")
assert(statLine:find("dead skipped", 1, true), "Expected /fqp gps stats to include dead skipped count, got: " .. statLine)

-- 3. Corpse resurrection at original death coords -> 0 Dijkstra recompute
ns.Nav.ResetStats()
ns.State.ResetIdlePerfStats()
stateUpd = 0
Mock.Resurrect(1437, 0.564, 0.404, "Greenwarden's Grove")
local pCorpse = ns.State.GetPlayerInfo()
local nvCorpse = ns.Nav.GetStats()
local stCorpse = ns.State.GetIdlePerfStats()
assert(pCorpse.isDead == false and ns.IsPlayerDeadOrGhost() == false and pCorpse.subZoneName == "Greenwarden's Grove")
assert(stCorpse.gpsTickTriggers == 0 and stateUpd == 0,
    "Expected corpse rez at same coords to skip redundant GPS trigger & STATE_UPDATED")
assert(nvCorpse.bidirSolves == 0 and nvCorpse.floodSolves == 0 and nvCorpse.queryCalls == 0
    and nvCorpse.singleFieldCalls == 0 and nvCorpse.nearestCalls == 0,
    "Expected 0 Dijkstra recomputes on corpse rez at unchanged coords")

-- 4. Spirit-Healer resurrection far away (>160yd) -> immediate quest-location resume bypassing cooldown
Mock.SetDead(true, false, true)
Mock.ReleaseSpirit(1437, 0.145, 0.432, "Baradin Bay")
ns.State.ResetIdlePerfStats()
stateUpd = 0
Mock.Resurrect(1437, 0.108, 0.596, "Menethil Harbor")
local pHealer = ns.State.GetPlayerInfo()
local stHealer = ns.State.GetIdlePerfStats()
assert(pHealer.isDead == false and pHealer.subZoneName == "Menethil Harbor"
    and math.abs(pHealer.x - 10.8) < 0.05 and math.abs(pHealer.y - 59.6) < 0.05,
    "Expected Spirit-Healer rez to unfreeze location to Menethil Harbor (10.8, 59.6)")
assert(stHealer.gpsTickTriggers == 1 and stateUpd == 1,
    string.format("Expected Spirit-Healer rez far away to resume immediately (trig=%d upd=%d)",
        stHealer.gpsTickTriggers, stateUpd))

-- 5. /reload while ALREADY ghost in adjacent zone Duskwood (1431) -> no graveyard catch-up, TRAVEL satisfaction, or reorder
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1431,
    x = 0.20,
    y = 0.45,
}})
Mock.SetDead(false, true, false) -- already ghost prior to /reload

local addonName2, ns2 = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName2)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)

local pReload = ns2.State.GetPlayerInfo()
local stepReload, metaReload = ns2.State.GetCurrentStep()
local nvReload = ns2.Nav.GetStats()
assert(pReload.isDead == true and ns2.IsPlayerDeadOrGhost() == true)
assert(stepReload and stepReload.zoneId == 11 and stepReload.questId == 294 and metaReload.source == "route",
    string.format("Expected /reload while dead in Duskwood to stay on Wetlands route step 294 (not jump/reorder), got z=%s qid=%s src=%s",
        tostring(stepReload and stepReload.zoneId), tostring(stepReload and stepReload.questId), tostring(metaReload and metaReload.source)))
assert(ns2.State.IsStepSatisfied({{ action = "TRAVEL", zoneId = 10, x = 20, y = 45, title = "Go to Duskwood" }}) == false,
    "Expected TRAVEL step in graveyard zone NOT marked satisfied while dead after /reload")
assert(nvReload.queryCalls == 0 and nvReload.bidirSolves == 0 and nvReload.floodSolves == 0,
    "Expected 0 Nav location/hop solves while dead on /reload")

-- Spirit-Healer rez in Duskwood after /reload resumes location reordering immediately
Mock.Resurrect(1431, 0.20, 0.45, "Raven Hill")
local stepResumed, metaResumed = ns2.State.GetCurrentStep()
assert(ns2.State.GetPlayerInfo().isDead == false and metaResumed.source == "friend_pivot" and stepResumed.zoneId == 10,
    string.format("Expected rez in Duskwood after dead /reload to resume friend_pivot in Duskwood (10), got z=%s src=%s",
        tostring(stepResumed and stepResumed.zoneId), tostring(metaResumed and metaResumed.source)))

assert(#Mock.errors == 0, "Unexpected Lua errors in Phase 10 dead/ghost test: " .. table.concat(Mock.errors, "; "))
print(string.format("PHASE10_DEAD_GHOST_OK deadSkip=%d corpseBidir=%d healerTrig=%d resumedZ=%d",
    stGhost.deadLocationSkipped, nvCorpse.bidirSolves, stHealer.gpsTickTriggers, stepResumed.zoneId))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Phase 10 dead/ghost quest-location gating test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("PHASE10_DEAD_GHOST_OK", out)

    def test_deep_requirements_audit_and_performance_budgets(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})

-- 1. Fix #1: /reload while ALREADY dead/ghost in dynamic/custom mode freezes zone (Arathi 45) across Hillsbrad walk & level-up until Resurrect
do
    _G.ForeverQuestPathDB = nil
    _G.ForeverQuestPathCharDB = nil
    Mock.Reset({{ tomtom = true, openmap = true }})
    Mock.SetCharacter({{
        name = "GhostRepro",
        realm = "Forever",
        level = 24,
        raceName = "Gnome",
        raceFile = "Gnome",
        raceId = 7,
        className = "Rogue",
        classFile = "ROGUE",
        classId = 4,
        faction = "Alliance",
        mapId = 1417,
        x = 0.45,
        y = 0.45,
    }})
    Mock.SetDead(false, true, false)

    local addonA, nsA = Mock.LoadAddonTOC({str(TOC_PATH)!r})
    Mock.FireEvent("ADDON_LOADED", addonA)
    Mock.FireEvent("PLAYER_LOGIN")
    Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
    Mock.AdvanceTimers(0.25)

    nsA.State.SetProfile("custom")
    local pBootDead = nsA.State.GetPlayerInfo()
    assert(pBootDead.isDead == true and pBootDead.zoneId == 45,
        "Expected dead boot in Arathi to freeze player zoneId=45, got " .. tostring(pBootDead.zoneId))

    Mock.SetPlayerPosition(1424, 0.50, 0.50, "Southshore", true)
    Mock.AdvanceTimers(1.6)
    Mock.LevelUp(25)
    local pStillDead = nsA.State.GetPlayerInfo()
    assert(pStillDead.isDead == true and pStillDead.zoneId == 45,
        "Expected /reload-while-dead location to remain frozen at Arathi (45) after walking to Hillsbrad, got " .. tostring(pStillDead.zoneId))
    local stepDead = nsA.State.GetCurrentStep()
    assert(stepDead ~= nil and stepDead.zoneId ~= 267,
        "Expected custom/dynamic step while dead NOT to re-anchor to Hillsbrad (267), got " .. tostring(stepDead and stepDead.zoneId))

    Mock.Resurrect(1424, 0.50, 0.50, "Southshore")
    local pRezzed = nsA.State.GetPlayerInfo()
    assert(pRezzed.isDead == false and pRezzed.zoneId == 267,
        "Expected Resurrect in Hillsbrad to unfreeze location to zoneId=267, got " .. tostring(pRezzed.zoneId))
end

-- 2. Fix #2: MaxLevelOffset (-1) preserved across Allow Red (+5) Quests checkbox ON -> OFF
do
    _G.ForeverQuestPathDB = nil
    _G.ForeverQuestPathCharDB = nil
    Mock.Reset({{ tomtom = true, openmap = true }})
    Mock.SetCharacter({{
        name = "Sprocketstab",
        realm = "Forever",
        level = 24,
        raceName = "Gnome",
        raceFile = "Gnome",
        raceId = 7,
        className = "Rogue",
        classFile = "ROGUE",
        classId = 4,
        faction = "Alliance",
        mapId = 1437,
        x = 0.11,
        y = 0.59,
    }})
    local addonB, nsB = Mock.LoadAddonTOC({str(TOC_PATH)!r})
    Mock.FireEvent("ADDON_LOADED", addonB)
    Mock.FireEvent("PLAYER_LOGIN")
    Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
    Mock.AdvanceTimers(0.25)

    nsB.State.SetMaxLevelOffset(-1)
    assert(nsB.State.GetMaxLevelOffset() == -1, "Expected initial custom MaxLevelOffset -1")
    nsB.Window.ShowTab("diag")
    local winB = Mock.FindFrameByName("ForeverQuestPathWindow")
    local redChk = nil
    local function findRedCheck(frame)
        if frame and frame.label and frame.label.GetText then
            local txt = frame.label:GetText()
            if txt and txt:find("Allow Red (+5) Quests", 1, true) then
                redChk = frame
                return
            end
        end
        for _, ch in ipairs((frame and frame._children) or {{}}) do
            findRedCheck(ch)
            if redChk then return end
        end
    end
    findRedCheck(winB)
    assert(redChk ~= nil, "Expected 'Allow Red (+5) Quests' checkbox in Window")
    assert(redChk.checked == false, "Expected Allow Red checkbox unchecked at -1 offset")
    redChk:Click()
    assert(nsB.State.GetMaxLevelOffset() == 5 and nsB.charDB.toggles.allowRedQuests == true,
        "Expected checking Allow Red to set MaxLevelOffset=+5, got " .. tostring(nsB.State.GetMaxLevelOffset()))
    redChk:Click()
    assert(nsB.State.GetMaxLevelOffset() == -1 and nsB.charDB.toggles.allowRedQuests == false and redChk.checked == false,
        "Expected unchecking Allow Red to restore custom -1 offset, got " .. tostring(nsB.State.GetMaxLevelOffset()))

    -- 3. Off-DB Ready Turn-In via QuestieDB in Dynamic Mode
    _G.QuestieDB = {{
        QueryQuestSingle = function(qid, field)
            if qid == 999991 then
                if field == "name" then return "Lost Shipment" end
                if field == "finishedBy" then return {{ {{ 99912 }}, nil }} end
                if field == "zoneOrSort" then return 11 end
                if field == "level" then return 24 end
            end
            return nil
        end,
        QueryNPCSingle = function(nid, field)
            if nid == 99912 then
                if field == "name" then return "Quartermaster Brent" end
                if field == "spawns" then return {{ [11] = {{ {{ 11.0, 59.5 }} }} }} end
            end
            return nil
        end,
    }}
    Mock.AcceptQuest(999991, "Lost Shipment", 24, true)
    local dynOffSteps = nsB.Scorer.GetDynamicSteps(nsB.State.GetPlayerInfo(), nsB.GetActiveWeights(), nsB.charDB.toggles, 12)
    local sawOffReady = false
    for _, st in ipairs(dynOffSteps or {{}}) do
        if st.questId == 999991 and st.action == "TURN_IN" and st.isReadyTurnIn then
            sawOffReady = true
            break
        end
    end
    assert(sawOffReady, "Expected QuestieDB off-DB ready quest 999991 TURN_IN in Scorer.GetDynamicSteps")
    _G.QuestieDB = nil
end

collectgarbage("collect")

-- 4. Performance & Allocation Budgets (1,500 completed quests + Window open)
_G.ForeverQuestPathDB = nil
_G.ForeverQuestPathCharDB = nil
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "BenchGnome",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.107,
    y = 0.595,
}})
for qid = 700001, 701500 do
    Mock.state.completedQuests[qid] = true
end

local addonC, nsC = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonC)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)

nsC.Window.ShowTab("route")
nsC.State.GetPlayerInfo()
nsC.State.GetCurrentStep()
nsC.Window.Refresh()

collectgarbage("collect")
collectgarbage("stop")
local memBase0 = collectgarbage("count")
for i = 1, 1000 do
    local pos = _G.C_Map.GetPlayerMapPosition(1437, "player")
    assert(pos ~= nil)
end
local mockPosKb = collectgarbage("count") - memBase0
collectgarbage("restart")

collectgarbage("collect")
collectgarbage("stop")
local memStart = collectgarbage("count")
local tGp0 = os.clock()
for i = 1, 1000 do
    local p = nsC.State.GetPlayerInfo()
    assert(p.zoneId == 11)
end
local gpMs = (os.clock() - tGp0) * 1000.0
local gpRawKb = collectgarbage("count") - memStart
local gpKb = math.max(0.0, gpRawKb - mockPosKb)
collectgarbage("restart")
assert(gpMs < 5.0, string.format("GetPlayerInfo x1000 budget exceeded: %.2fms >= 5.0ms", gpMs))
assert(gpKb < 64.0, string.format("GetPlayerInfo x1000 addon alloc budget exceeded: %.1fKB (raw=%.1fKB, mock=%.1fKB) >= 64.0KB", gpKb, gpRawKb, mockPosKb))

Mock.AcceptQuest(279, "Claws from the Deep", 22, true)
Mock.AcceptQuest(288, "The Third Fleet", 27, true)
Mock.AcceptQuest(484, "Young Crocolisk Skins", 22, true)
nsC.Window.Refresh()

local tGps0 = os.clock()
for i = 1, 20 do
    local nx = 0.11 + (i % 5) * 0.05
    local ny = 0.58 - math.floor(i / 5) * 0.04
    Mock.SetPlayerPosition(1437, nx, ny, "Wetlands", false)
    nsC.State.Refresh("GPS_MOVED")
end
local gpsAvgMs = ((os.clock() - tGps0) * 1000.0) / 20.0
assert(gpsAvgMs < 3.5, string.format("Ready GPS_MOVED budget exceeded: %.2fms/tick >= 3.5ms", gpsAvgMs))

local tPair0 = os.clock()
for k = 1, 20 do
    local bQid = 880000 + k
    Mock.AcceptQuest(bQid, "Bench Quest " .. tostring(k), 24, true)
    Mock.TurnInQuest(bQid, 1000, 100)
end
local pairAvgMs = ((os.clock() - tPair0) * 1000.0) / 20.0
assert(pairAvgMs < 10.0, string.format("Accept+TurnIn pair budget exceeded: %.2fms/pair >= 10.0ms", pairAvgMs))

local tMax0 = os.clock()
for off = -3, 5 do
    nsC.State.SetMaxLevelOffset(off)
end
local maxAvgMs = ((os.clock() - tMax0) * 1000.0) / 9.0
assert(maxAvgMs < 6.0, string.format("MaxLevelOffset sweep budget exceeded: %.2fms/step >= 6.0ms", maxAvgMs))

nsC.State.SetProfile("custom")
nsC.State.SetMaxLevelOffset(2)
local tDyn0 = os.clock()
local lastP = nil
for k = 1, 10 do
    Mock.SetPlayerPosition(1437, 0.12 + k * 0.04, 0.55 - (k % 4) * 0.04, "Wetlands", false)
    lastP = nsC.State.GetPlayerInfo()
    local dSteps = nsC.Scorer.GetDynamicSteps(lastP, nsC.GetActiveWeights(), nsC.charDB.toggles, 20)
    assert(dSteps and #dSteps > 0, "Expected dynamic steps during cell sweep")
end
local dynAvgMs = ((os.clock() - tDyn0) * 1000.0) / 10.0
assert(dynAvgMs < 15.0, string.format("Scorer.GetDynamicSteps live cell budget exceeded: %.2fms/refresh >= 15.0ms", dynAvgMs))

local tDynRep0 = os.clock()
local repSteps = nsC.Scorer.GetDynamicSteps(lastP, nsC.GetActiveWeights(), nsC.charDB.toggles, 20)
local dynRepMs = (os.clock() - tDynRep0) * 1000.0
assert(repSteps and #repSteps > 0 and dynRepMs < 0.5,
    string.format("Scorer.GetDynamicSteps cached repeat budget exceeded: %.3fms >= 0.5ms", dynRepMs))

assert(#Mock.errors == 0, "Unexpected Lua errors in audit & perf test: " .. table.concat(Mock.errors, "; "))
print(string.format("DEEP_AUDIT_PERF_OK gp=%.2fms/%.1fKB gps=%.2fms pair=%.2fms max=%.2fms dyn=%.2fms rep=%.3fms",
    gpMs, gpKb, gpsAvgMs, pairAvgMs, maxAvgMs, dynAvgMs, dynRepMs))
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Deep audit & performance test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("DEEP_AUDIT_PERF_OK", out)

    def test_skip_elite_quests_unless_grouped(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})

local function findStepIndex(steps, questId, action)
    for i, st in ipairs(steps or {{}}) do
        if st.questId == questId and (action == nil or (st.action or "ACCEPT") == action) then
            return i
        end
    end
    return nil
end

local function stepInList(list, questId, action)
    for _, st in ipairs(list or {{}}) do
        if st and st.questId == questId and (action == nil or (st.action or "ACCEPT") == action) then
            return true, st
        end
    end
    return false, nil
end

-- ============================================================================
-- Part A: Default solo skipEliteSolo, Elwynn Q176 deferral, Nav/Scorer/Window
-- ============================================================================
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Lightshield",
    realm = "Forever",
    level = 11,
    raceName = "Human",
    raceFile = "Human",
    raceId = 1,
    className = "Paladin",
    classFile = "PALADIN",
    classId = 2,
    faction = "Alliance",
    mapId = 1429,
    x = 0.246,
    y = 0.747,
}})

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

assert(ns.charDB and ns.charDB.toggles and ns.charDB.toggles.skipEliteSolo == true,
    "Expected skipEliteSolo to default to true")
local pSolo = ns.State.GetPlayerInfo()
assert(pSolo and pSolo.inGroup == false, "Expected player.inGroup == false while solo")
assert(ns.State.ShouldSkipEliteNow(pSolo, ns.charDB.toggles) == true,
    "Expected ShouldSkipEliteNow() == true while solo with default toggle")
local vmSolo = ns.HUD.GetViewModel()
assert(vmSolo and vmSolo.modeLabel and vmSolo.modeLabel:find("No Elite", 1, true),
    "Expected HUD modeLabel to include 'No Elite' when solo & skipEliteSolo=true, got " .. tostring(vmSolo and vmSolo.modeLabel))

-- Q176 (WANTED: Hogger) is open-world elite
assert(ns.State.IsQuestElite(176) == true, "Expected Q176 (WANTED: Hogger) to be classified as open-world elite")
local bCode176, bReason176 = ns.State.GetQuestBlockReason(176)
assert(bCode176 == "elite_solo", "Expected GetQuestBlockReason(176) == 'elite_solo', got " .. tostring(bCode176))

-- Dungeon quests must NEVER be classified as open-world elite or blocked by elite_solo
assert(ns.State.IsQuestElite(166) == false, "Expected dungeon quest 166 not to be classified as open-world elite")
assert(ns.State.IsQuestElite(96394) == false, "Expected dungeon quest 96394 not to be classified as open-world elite")

local stepsA, _, pkeyA = ns.State.GetActiveProfileSteps()
local idxAcc176 = findStepIndex(stepsA, 176, "ACCEPT")
local idxComp176 = findStepIndex(stepsA, 176, "COMPLETE")
local idxTurn176 = findStepIndex(stepsA, 176, "TURN_IN")
assert(idxAcc176 and idxComp176 and idxTurn176, "Expected Q176 ACCEPT/COMPLETE/TURN_IN steps in Human starter route")

-- Mark 433 complete so step 114 doesn't confuse cursor jumps
Mock.CompleteQuest(433)
ns.State.JumpToStep(idxAcc176)
local curAfterJump, _ = ns.State.GetCurrentStep()
assert(curAfterJump and curAfterJump.questId ~= 176,
    "Expected solo cursor to skip past ACCEPT 176, landed on " .. tostring(curAfterJump and curAfterJump.questId))
local defMapA = ns.charDB.deferredSteps and ns.charDB.deferredSteps[pkeyA]
local skipMapA = ns.charDB.skippedSteps and ns.charDB.skippedSteps[pkeyA]
assert(defMapA and defMapA[idxAcc176] == true, "Expected idxAcc176 recorded in deferredSteps while solo")
local kAcc176 = ns.State.GetStepKey(stepsA[idxAcc176], idxAcc176, pkeyA)
assert(not (skipMapA and (skipMapA[idxAcc176] or skipMapA[kAcc176])),
    "Expected deferred elite step NOT to be marked in skippedSteps")

-- Route tab remaining filter hides/blocks Q176 while solo and unaccepted
ns.Window.ShowTab("route")
local snapSolo = ns.Window.GetSnapshot()
assert(snapSolo.skipEliteSolo == true and snapSolo.inGroup == false, "Window snapshot mismatch for skipEliteSolo/inGroup")
for _, row in ipairs(snapSolo.routeRows or {{}}) do
    local st = row and row.step
    if st and st.questId == 176 and (not row.isBlocked) then
        error("Expected Window remaining routeRows not to show unblocked Q176 while solo")
    end
end

-- Nav.FindNearestEligibleQuests & Scorer.GetDynamicSteps exclude unaccepted Q176 while solo
local nearSolo, nearSoloN = ns.Nav.FindNearestEligibleQuests(12, 0.246, 0.747, ns.GetData(), ns.State.GetPlayerInfo(), {{
    maxSec = 1800,
    maxResults = 60,
    noCap = true,
}})
assert((nearSoloN or 0) > 0, "Expected Nav nearest eligible quests in Elwynn Forest")
for i = 1, nearSoloN do
    assert(nearSolo[i].questId ~= 176, "Expected Nav.FindNearestEligibleQuests to exclude Q176 while solo")
end
local dynSolo = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 30)
local has176DynSolo = stepInList(dynSolo, 176, nil)
assert(not has176DynSolo, "Expected Scorer.GetDynamicSteps to exclude Q176 while solo")

-- ============================================================================
-- Part B: Party & Raid auto-unlock, Re-deferral on leave, Roster churn CPU guard
-- ============================================================================
Mock.SetGroup(true, 2, false, true)
Mock.AdvanceTimers(0.5)
local pParty = ns.State.GetPlayerInfo()
assert(pParty.inGroup == true, "Expected player.inGroup == true in party")
assert(ns.State.ShouldSkipEliteNow(pParty, ns.charDB.toggles) == false, "Expected ShouldSkipEliteNow == false when grouped")
local curParty, metaParty = ns.State.GetCurrentStep()
assert(metaParty and metaParty.stepIndex == idxAcc176,
    "Expected joining party to rewind route cursor stepIndex to idxAcc176 (" .. tostring(idxAcc176) .. "), got " .. tostring(metaParty and metaParty.stepIndex))
assert(curParty and curParty.questId == 176 and curParty.action == "ACCEPT",
    "Expected joining party at (24.6, 74.7) to surface ACCEPT 176, got " .. tostring(curParty and curParty.questId) .. " " .. tostring(curParty and curParty.action))
local vmParty = ns.HUD.GetViewModel()
assert(vmParty.modeLabel:find("Grouped", 1, true), "Expected HUD modeLabel 'Grouped' in party, got " .. tostring(vmParty.modeLabel))
assert(vmParty.current and vmParty.current.badgesLine and vmParty.current.badgesLine:find("[Elite]", 1, true),
    "Expected HUD badgesLine '[Elite]' for Q176, got " .. tostring(vmParty.current and vmParty.current.badgesLine))
local dynParty = ns.Scorer.GetDynamicSteps(pParty, ns.GetActiveWeights(), ns.charDB.toggles, 40)
assert(stepInList(dynParty, 176, "ACCEPT"), "Expected Scorer.GetDynamicSteps to include ACCEPT 176 when grouped")

-- Roster churn while remaining grouped (member count 2 -> 3 -> 4 -> 5) must trigger 0 refreshes
ns.State.ResetIdlePerfStats()
local statsChurnBefore = ns.State.GetIdlePerfStats()
local genChurnBefore = ns.State.GetQuestStateGeneration()
for i = 1, 20 do
    Mock.SetGroup(true, 2 + (i % 4), false, true)
    Mock.AdvanceTimers(0.3)
end
local statsChurnAfter = ns.State.GetIdlePerfStats()
assert(statsChurnAfter.refreshExecuted == statsChurnBefore.refreshExecuted,
    string.format("Expected 0 refreshes on inGroup-unchanged roster churn, got %d",
        statsChurnAfter.refreshExecuted - statsChurnBefore.refreshExecuted))
assert(ns.State.GetQuestStateGeneration() == genChurnBefore,
    "Expected quest generation unchanged across group roster noise")

-- Leave party -> incomplete elite is re-deferred and cursor skips past ACCEPT 176 again
Mock.SetGroup(false, 0, false, true)
Mock.AdvanceTimers(0.5)
local curLeft, _ = ns.State.GetCurrentStep()
assert(curLeft and curLeft.questId ~= 176, "Expected leaving group to re-defer Q176")

-- Join raid -> inGroup=true again and cursor rewinds to ACCEPT 176
Mock.SetGroup(true, 10, true, true)
Mock.AdvanceTimers(0.5)
local pRaid = ns.State.GetPlayerInfo()
assert(pRaid.inGroup == true, "Expected player.inGroup == true in raid")
local curRaid, _ = ns.State.GetCurrentStep()
assert(curRaid and curRaid.questId == 176 and curRaid.action == "ACCEPT",
    "Expected joining raid to unlock and rewind to ACCEPT 176")
Mock.SetGroup(false, 0, false, true)
Mock.AdvanceTimers(0.5)

-- ============================================================================
-- Part C: Active incomplete elite skipped vs Ready elite TURN_IN preserved solo
-- ============================================================================
Mock.AcceptQuest(176, "WANTED: \\"Hogger\\"", 11, false, "Elite")
Mock.AcceptQuest(999901, "Custom Off-DB Elite Hunt", 11, false, "Elite")
Mock.AdvanceTimers(0.5)
assert(ns.State.IsQuestElite(999901) == true, "Expected off-DB QuestLog 'Elite' tag quest 999901 to be classified elite")
local curIncElite, _ = ns.State.GetCurrentStep()
assert(not (curIncElite and (curIncElite.questId == 176 or curIncElite.questId == 999901)),
    "Expected incomplete active elite quests not to be recommended for COMPLETE while solo")
local dynInc = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 40)
assert(not stepInList(dynInc, 176, "COMPLETE"), "Expected Scorer to omit incomplete COMPLETE 176 while solo")
assert(not stepInList(dynInc, 999901, "COMPLETE"), "Expected Scorer to omit incomplete off-DB COMPLETE 999901 while solo")

-- Complete Q176 objectives while STILL solo + skipEliteSolo=true -> ready TURN_IN 176 MUST NOT be skipped!
Mock.SetQuestObjectivesComplete(176, true)
Mock.AdvanceTimers(0.5)
local readyList = ns.State.GetReadyTurnInQuests()
assert(stepInList(readyList, 176, nil), "Expected completed elite Q176 in GetReadyTurnInQuests while solo")
Mock.SetPlayerPosition(1429, 0.421, 0.659, "Goldshire", false)
Mock.AdvanceTimers(2.0)
local curReadySolo, metaReadySolo = ns.State.GetCurrentStep()
assert(curReadySolo and curReadySolo.questId == 176 and curReadySolo.action == "TURN_IN",
    "Expected ready elite TURN_IN 176 to be recommended even while solo, got " .. tostring(curReadySolo and curReadySolo.questId) .. " " .. tostring(curReadySolo and curReadySolo.action))
local vmReady = ns.HUD.GetViewModel()
assert(vmReady.current and vmReady.current.actionText:find("Ready Turn-In", 1, true),
    "Expected HUD actionText '[Ready Turn-In]' for completed solo elite quest")
assert(vmReady.current.badgesLine and vmReady.current.badgesLine:find("[Elite]", 1, true),
    "Expected HUD badgesLine '[Elite]' on ready elite turn-in")
local dynReady = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 30)
assert(stepInList(dynReady, 176, "TURN_IN"), "Expected Scorer.GetDynamicSteps to preserve ready TURN_IN 176 while solo")

-- ============================================================================
-- Part D: Slash commands (/fqp elite), both UI checkboxes, 0 Frame leaks
-- ============================================================================
-- Clean 176 complete so cursor tests cleanly reflect ACCEPT 176 gating
Mock.TurnInQuest(176, 850, 400)
Mock.AdvanceTimers(0.5)

-- Jump to Lv30 Wetlands scenario in same harness using Mock.Reset for pristine Q631->Q632 prereq chain + /fqp elite
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 30,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.50,
    y = 0.25,
}})
local addonName2, ns2 = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName2)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

-- Verify Lv30 dungeon quest Q96394 is NEVER blocked with elite_solo while solo;
-- under default skipDungeons=true it is skipped with 'dungeon_skipped', and when skipDungeons=false it is unblocked even while solo
local dc96394 = ns2.State.GetQuestBlockReason(96394)
assert(dc96394 == "dungeon_skipped", "Dungeon quest 96394 should be blocked with 'dungeon_skipped' under default skipDungeons=true, got " .. tostring(dc96394))
local stepsB, _, pkeyB = ns2.State.GetActiveProfileSteps()
local idxDungRun = findStepIndex(stepsB, 96394, "DUNGEON_RUN")
if idxDungRun then
    local dbCode = ns2.State.IsStepBlocked(stepsB[idxDungRun], stepsB, idxDungRun, ns2.State.GetPlayerInfo(), pkeyB, nil, nil, ns2.charDB.toggles)
    assert(dbCode == "dungeon_skipped", "DUNGEON_RUN step should be blocked with 'dungeon_skipped', got " .. tostring(dbCode))
end
ns2.charDB.toggles.skipDungeons = false
ns2.State.Refresh("TOGGLE_skipDungeons")
local dc96394Allow = ns2.State.GetQuestBlockReason(96394)
assert(dc96394Allow == nil, "Expected dungeon quest 96394 unblocked while solo when skipDungeons=false, got " .. tostring(dc96394Allow))
if idxDungRun then
    local dbCodeAllow = ns2.State.IsStepBlocked(stepsB[idxDungRun], stepsB, idxDungRun, ns2.State.GetPlayerInfo(), pkeyB, nil, nil, ns2.charDB.toggles)
    assert(dbCodeAllow == nil, "Expected DUNGEON_RUN step unblocked while solo when skipDungeons=false, got " .. tostring(dbCodeAllow))
end
ns2.charDB.toggles.skipDungeons = true
ns2.State.Refresh("TOGGLE_skipDungeons")

-- Test elite chain prereq deferral: Q631 (elite, 986) -> Q632 (non-elite, prereq 631, 988)
local idxAcc631 = findStepIndex(stepsB, 631, "ACCEPT")
local idxTurn631 = findStepIndex(stepsB, 631, "TURN_IN")
local idxAcc632 = findStepIndex(stepsB, 632, "ACCEPT")
local idxTurn632 = findStepIndex(stepsB, 632, "TURN_IN")
assert(idxAcc631 and idxTurn631 and idxAcc632 and idxTurn632, "Expected Q631/Q632 chain steps in balanced route")

ns2.State.JumpToStep(idxAcc631)
local curChainSolo, _ = ns2.State.GetCurrentStep()
assert(curChainSolo and curChainSolo.questId ~= 631 and curChainSolo.questId ~= 632,
    "Expected solo cursor jumping to elite Q631 to also skip prereq-locked follow-up Q632, landed on " .. tostring(curChainSolo and curChainSolo.questId))
local defB = ns2.charDB.deferredSteps and ns2.charDB.deferredSteps[pkeyB]
local skipB = ns2.charDB.skippedSteps and ns2.charDB.skippedSteps[pkeyB]
assert(defB and defB[idxAcc631] == true and defB[idxTurn631] == true and defB[idxAcc632] == true and defB[idxTurn632] == true,
    "Expected Q631 and prereq-locked follow-up Q632 steps all recorded in deferredSteps")
for _, checkIdx in ipairs({{ idxAcc631, idxTurn631, idxAcc632, idxTurn632 }}) do
    local kC = ns2.State.GetStepKey(stepsB[checkIdx], checkIdx, pkeyB)
    assert(not (skipB and (skipB[checkIdx] or skipB[kC])), "Expected deferred elite/prereq step not to be in skippedSteps")
end

-- /fqp elite off while STILL solo unlocks deferred Q631 immediately and rewinds cursor!
Mock.RunSlash("/fqp", "elite off")
assert(ns2.Window.GetSnapshot().skipEliteSolo == false, "Expected /fqp elite off to set skipEliteSolo=false")
local vmOff = ns2.HUD.GetViewModel()
assert(vmOff.modeLabel:find("Elite All", 1, true), "Expected HUD modeLabel 'Elite All' when solo & skipEliteSolo=false, got " .. tostring(vmOff.modeLabel))
local curEliteOff, _ = ns2.State.GetCurrentStep()
assert(curEliteOff and curEliteOff.questId == 631 and curEliteOff.action == "ACCEPT",
    "Expected /fqp elite off while solo to unlock & rewind cursor to ACCEPT 631, got " .. tostring(curEliteOff and curEliteOff.questId))

-- /fqp elite on re-defers Q631 + Q632
Mock.RunSlash("/fqp", "elite on")
assert(ns2.Window.GetSnapshot().skipEliteSolo == true, "Expected /fqp elite on to set skipEliteSolo=true")
local curEliteOn, _ = ns2.State.GetCurrentStep()
assert(curEliteOn and curEliteOn.questId ~= 631 and curEliteOn.questId ~= 632,
    "Expected /fqp elite on while solo to re-defer Q631 & Q632")

-- Find both "Skip Elite (Solo)" checkboxes (Route tab + Settings/Diag tab) and verify sync + 0 allocations
local win2 = Mock.FindFrameByName("ForeverQuestPathWindow")
local eliteChks = {{}}
local function collectEliteChecks(frame)
    if frame and frame.label and frame.label.GetText then
        local txt = frame.label:GetText()
        if txt and txt:find("Skip Elite (Solo)", 1, true) then
            eliteChks[#eliteChks + 1] = frame
        end
    end
    for _, ch in ipairs((frame and frame._children) or {{}}) do
        collectEliteChecks(ch)
    end
end
ns2.Window.ShowTab("route")
ns2.Window.ShowTab("diag")
collectEliteChecks(win2)
assert(#eliteChks == 2, "Expected 2 'Skip Elite (Solo)' checkboxes (Route + Diag tabs), found " .. tostring(#eliteChks))
assert(eliteChks[1].checked == true and eliteChks[2].checked == true,
    "Expected both Skip Elite (Solo) checkboxes initially checked")

Mock.ResetMetrics()
ns2.Window.ShowTab("route")
eliteChks[1]:Click()
assert(ns2.Window.GetSnapshot().skipEliteSolo == false, "Clicking Route Skip Elite checkbox should disable skipEliteSolo")
assert(eliteChks[1].checked == false and eliteChks[2].checked == false,
    "Both Skip Elite (Solo) checkboxes should sync to false after Route tab click")
assert(ns2.State.GetCurrentStep().questId == 631, "Disabling via Route checkbox should rewind cursor to Q631")

ns2.Window.ShowTab("diag")
eliteChks[2]:Click()
assert(ns2.Window.GetSnapshot().skipEliteSolo == true, "Clicking Settings/Diag Skip Elite checkbox should re-enable skipEliteSolo")
assert(eliteChks[1].checked == true and eliteChks[2].checked == true,
    "Both Skip Elite (Solo) checkboxes should sync to true after Diag tab click")
assert(ns2.State.GetCurrentStep().questId ~= 631, "Re-enabling via Diag checkbox should re-defer Q631")

Mock.RunSlash("/fqp", "elite toggle")
assert(ns2.Window.GetSnapshot().skipEliteSolo == false, "Expected /fqp elite toggle to flip skipEliteSolo to false")
Mock.RunSlash("/fqp", "elite toggle")
assert(ns2.Window.GetSnapshot().skipEliteSolo == true, "Expected /fqp elite toggle to flip skipEliteSolo back to true")

local m2 = Mock.GetMetrics()
assert(m2.createFrameCalls == 0, "Expected 0 CreateFrame calls during Skip Elite UI/slash/group transitions, got " .. tostring(m2.createFrameCalls))
assert(m2.createRegionCalls == 0, "Expected 0 CreateRegion calls during Skip Elite UI/slash/group transitions, got " .. tostring(m2.createRegionCalls))
assert(#Mock.errors == 0, "Unexpected Lua errors in skip-elite test: " .. table.concat(Mock.errors, "; "))
print("SKIP_ELITE_UNLESS_GROUPED_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Skip elite unless grouped test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("SKIP_ELITE_UNLESS_GROUPED_OK", out)

    def test_skip_dungeon_quests_option(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})

local function findStepIndex(steps, questId, action)
    for i, st in ipairs(steps or {{}}) do
        if st.questId == questId and (action == nil or (st.action or "ACCEPT") == action) then
            return i
        end
    end
    return nil
end

local function stepInList(list, questId, action)
    for _, st in ipairs(list or {{}}) do
        if st and st.questId == questId and (action == nil or (st.action or "ACCEPT") == action) then
            return true, st
        end
    end
    return false, nil
end

-- ============================================================================
-- Part A: Default skipDungeons=true, Ironforge/Westfall routing, Nav & Scorer
-- ============================================================================
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Stonevault",
    realm = "Forever",
    level = 15,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1455, -- Ironforge (1537), Hall of Thanes steps [296..302]
    x = 0.50,
    y = 0.50,
}})
-- Complete prereqs for Lv15 Ironforge/Westfall checks & outdoor unlock Q92747
Mock.CompleteQuest(92745)
Mock.CompleteQuest(155)
Mock.SetGroup(true, 5, false, true) -- Even while grouped, skipDungeons skips quests inside dungeons!

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

assert(ns.charDB and ns.charDB.toggles and ns.charDB.toggles.skipDungeons == true,
    "Expected skipDungeons to default to true")
assert(ns.State.ShouldSkipDungeonsNow(ns.charDB.toggles) == true,
    "Expected ShouldSkipDungeonsNow() == true with default toggle")

-- IsQuestDungeon classification & outdoor dungeon-unlock preservation
assert(ns.State.IsQuestDungeon(166) == true, "Expected Deadmines quest Q166 to be classified as dungeon quest")
assert(ns.State.IsQuestDungeon(96394) == true, "Expected Hall of Thanes quest Q96394 to be classified as dungeon quest")
assert(ns.State.IsQuestDungeon(92747) == false, "Expected outdoor unlock quest Q92747 not to be classified as dungeon quest")
assert(ns.State.IsQuestDungeon(176) == false, "Expected open-world elite Q176 not to be classified as dungeon quest")

local b92747 = ns.State.GetQuestBlockReason(92747)
assert(b92747 == nil, "Outdoor unlock quest Q92747 should NOT be skipped by skipDungeons, got " .. tostring(b92747))
local b96394, r96394 = ns.State.GetQuestBlockReason(96394, "DUNGEON_PREP")
assert(b96394 == "dungeon_skipped", "Expected dungeon quest Q96394 blocked with 'dungeon_skipped', got " .. tostring(b96394))
assert(type(r96394) == "string" and r96394 ~= "", "Expected non-empty reason for dungeon_skipped")
local b214 = ns.State.GetQuestBlockReason(214, "ACCEPT")
assert(b214 == "dungeon_skipped", "Expected Q214 (Lv17) blocked with 'dungeon_skipped' at Lv15, got " .. tostring(b214))
local lv20Player = {{ level = 20, faction = "Alliance", raceId = 7, classId = 4 }}
local b166 = ns.State.GetQuestBlockReason(166, "ACCEPT", lv20Player)
assert(b166 == "dungeon_skipped", "Expected Q166 blocked with 'dungeon_skipped' at Lv20, got " .. tostring(b166))

local vmInit = ns.HUD.GetViewModel()
assert(vmInit and vmInit.skipDungeons == true, "Expected HUD viewmodel skipDungeons == true")
assert(vmInit.modeLabel and vmInit.modeLabel:find("No Dung", 1, true),
    "Expected HUD modeLabel to include 'No Dung', got " .. tostring(vmInit.modeLabel))

-- Verify cursor skips DUNGEON_PREP (296) -> DUNGEON_RUN (299) -> TURN_IN (302) and records in deferredSteps
local stepsA, _, pkeyA = ns.State.GetActiveProfileSteps()
local idxPrep96394 = findStepIndex(stepsA, 96394, "DUNGEON_PREP")
local idxRun96394 = findStepIndex(stepsA, 96394, "DUNGEON_RUN")
local idxTurn96394 = findStepIndex(stepsA, 96394, "TURN_IN")
assert(idxPrep96394 and idxRun96394 and idxTurn96394, "Expected Hall of Thanes Q96394 PREP/RUN/TURN_IN in balanced route")

ns.State.JumpToStep(idxPrep96394)
local curSkip, _ = ns.State.GetCurrentStep()
assert(curSkip and curSkip.questId ~= 96394 and curSkip.action ~= "DUNGEON_PREP" and curSkip.action ~= "DUNGEON_RUN",
    "Expected cursor to skip Hall of Thanes dungeon batch, landed on " .. tostring(curSkip and curSkip.action) .. ":" .. tostring(curSkip and curSkip.questId))
local defA = ns.charDB.deferredSteps and ns.charDB.deferredSteps[pkeyA]
local skipA = ns.charDB.skippedSteps and ns.charDB.skippedSteps[pkeyA]
assert(defA and defA[idxPrep96394] == true and defA[idxRun96394] == true and defA[idxTurn96394] == true,
    "Expected skipped dungeon steps recorded in deferredSteps")
for _, checkIdx in ipairs({{ idxPrep96394, idxRun96394, idxTurn96394 }}) do
    local skKey = ns.State.GetStepKey(stepsA[checkIdx], checkIdx, pkeyA)
    assert(not (skipA and (skipA[checkIdx] or skipA[skKey])), "Deferred dungeon steps must NOT be written into skippedSteps")
end

-- GetUpcomingSteps, Window.routeRows, Nav.FindNearestEligibleQuests, Scorer.GetDynamicSteps all skip 96394
local upcomingSkip = ns.State.GetUpcomingSteps(12)
for _, us in ipairs(upcomingSkip) do
    assert(us.action ~= "DUNGEON_PREP" and us.action ~= "DUNGEON_RUN" and us.questId ~= 96394,
        "Expected GetUpcomingSteps to exclude dungeon steps while skipDungeons=true")
end

ns.Window.ShowTab("route")
local snapSkip = ns.Window.GetSnapshot()
assert(snapSkip.skipDungeons == true, "Expected Window snapshot skipDungeons == true")
assert(type(snapSkip.dungeons) == "table" and #snapSkip.dungeons > 0, "Expected Dungeons & BiS tab data still populated")
for _, row in ipairs(snapSkip.routeRows or {{}}) do
    local st = row and row.step
    if st and (st.questId == 96394 or st.action == "DUNGEON_PREP" or st.action == "DUNGEON_RUN") and (not row.isBlocked) then
        error("Expected Window remaining routeRows not to show unblocked dungeon step: " .. tostring(st.action) .. ":" .. tostring(st.questId))
    end
end

local nearSkip, nearSkipN = ns.Nav.FindNearestEligibleQuests(1537, 0.50, 0.50, ns.GetData(), ns.State.GetPlayerInfo(), {{
    maxSec = 1800,
    maxResults = 60,
    noCap = true,
}})
for i = 1, (nearSkipN or 0) do
    local r = nearSkip[i]
    assert(r.qid ~= 96394 and r.qid ~= 96395 and r.qid ~= 96403,
        "Expected Nav.FindNearestEligibleQuests to exclude dungeon quests while skipDungeons=true, got " .. tostring(r.qid))
end
local dynSkip = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 30)
assert(not stepInList(dynSkip, 96394, nil), "Expected Scorer.GetDynamicSteps to exclude unaccepted Q96394")

-- Toggling /fqp skipdungeons off rewinds cursor to the deferred Hall of Thanes DUNGEON_PREP 96394 batch
Mock.RunSlash("/fqp", "skipdungeons off")
local vmDungAllowA = ns.HUD.GetViewModel()
assert(vmDungAllowA.modeLabel and vmDungAllowA.modeLabel:find("Dung On", 1, true),
    "Expected HUD modeLabel 'Dung On' in Part A, got " .. tostring(vmDungAllowA.modeLabel))
local curRewindA, _ = ns.State.GetCurrentStep()
assert(curRewindA and curRewindA.questId == 96394 and curRewindA.action == "DUNGEON_PREP",
    "Expected /fqp skipdungeons off to rewind to DUNGEON_PREP 96394, got "
    .. tostring(curRewindA and curRewindA.action) .. ":" .. tostring(curRewindA and curRewindA.questId))
Mock.RunSlash("/fqp", "skipdungeons on")
local curReDeferA, _ = ns.State.GetCurrentStep()
assert(curReDeferA and curReDeferA.questId ~= 96394, "Expected /fqp skipdungeons on to re-defer Q96394")

-- ============================================================================
-- Part B: Incomplete active dungeon quests skipped, Ready turn-ins ALWAYS preserved
-- ============================================================================
-- Accept Q96394 + an off-DB "Dungeon"-tagged quest + test QuestieDB questFlags=32 classification
Mock.AcceptQuest(96394, "The Restless Dead", 15, false, "Dungeon")
Mock.AcceptQuest(999921, "Custom Instance Contract", 15, false, "Dungeon")
_G.QuestieDB = {{
    QueryQuestSingle = function(qid, field)
        if qid == 999922 then
            if field == "name" then return "Questie Flagged Instance Quest"
            elseif field == "requiredLevel" then return 10
            elseif field == "questLevel" then return 15
            elseif field == "questFlags" then return 32
            end
        end
        return nil
    end,
    IsDoable = function() return true end,
}}
assert(ns.State.IsQuestDungeon(999921) == true, "Expected off-DB Dungeon-tagged quest 999921 detected as dungeon")
assert(ns.State.IsQuestDungeon(999922) == true, "Expected QuestieDB questFlags bit-32 quest 999922 detected as dungeon")
assert(ns.State.GetQuestBlockReason(96394, "COMPLETE") == "dungeon_skipped",
    "Expected incomplete active dungeon quest 96394 blocked with 'dungeon_skipped'")
assert(ns.State.GetQuestBlockReason(999921, "COMPLETE") == "dungeon_skipped",
    "Expected incomplete off-DB Dungeon quest 999921 blocked with 'dungeon_skipped'")
assert(ns.State.GetQuestBlockReason(999922, "ACCEPT") == "dungeon_skipped",
    "Expected QuestieDB flag-32 quest 999922 blocked with 'dungeon_skipped'")
_G.QuestieDB = nil

-- Nav & Scorer must exclude incomplete active dungeon quest Q96394
local nearInc, nearIncN = ns.Nav.FindNearestEligibleQuests(1537, 0.50, 0.50, ns.GetData(), ns.State.GetPlayerInfo(), {{
    maxSec = 1800,
    maxResults = 60,
    noCap = true,
}})
for i = 1, (nearIncN or 0) do
    assert(nearInc[i].qid ~= 96394, "Expected Nav to exclude incomplete active dungeon quest 96394")
end
local dynInc = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 25)
assert(not stepInList(dynInc, 96394, "COMPLETE"), "Expected Scorer not to suggest COMPLETE 96394 while skipDungeons=true")

-- Mark Q96394 and off-DB Q999921 COMPLETE (ready for turn-in) -> MUST surface immediately even while skipDungeons=true!
Mock.TurnInQuest(999921, 0, 0) -- remove 999921 incomplete first
Mock.SetQuestObjectivesComplete(96394, true)
assert(ns.State.GetQuestBlockReason(96394, "TURN_IN") == nil,
    "Completed ready dungeon quest 96394 turn-in must NEVER be blocked")
local curReadyDung, metaReadyDung = ns.State.GetCurrentStep()
assert(curReadyDung and curReadyDung.isReadyTurnIn == true and curReadyDung.questId == 96394 and curReadyDung.action == "TURN_IN",
    "Expected ready dungeon quest Q96394 to surface immediately as [Ready Turn-In], got " .. tostring(curReadyDung and curReadyDung.questId))
local vmReadyDung = ns.HUD.GetViewModel()
assert(vmReadyDung and vmReadyDung.current and vmReadyDung.current.actionText:find("Ready Turn-In", 1, true),
    "Expected HUD actionText '[Ready Turn-In]' for completed dungeon quest")
assert(vmReadyDung.current.badgesLine and vmReadyDung.current.badgesLine:find("[Dungeon]", 1, true),
    "Expected HUD badgesLine '[Dungeon]' on ready dungeon TURN_IN step")
local dynReadyDung = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), ns.GetActiveWeights(), ns.charDB.toggles, 25)
assert(stepInList(dynReadyDung, 96394, "TURN_IN"), "Expected Scorer to preserve ready TURN_IN 96394 while skipDungeons=true")

-- Clean up 96394 into completed log for rewind test below
Mock.TurnInQuest(96394, 1000, 100)

-- ============================================================================
-- Part C: Slash commands (/fqp skipdungeons, /fqp dungeons allow|skip|toggle),
--         checkboxes in Route + Settings tabs, prereq cascade, and cursor rewind
-- ============================================================================
-- Test bare '/fqp dungeons' still opens the Dungeons tab
ns.Window.ShowTab("route")
Mock.RunSlash("/fqp", "dungeons")
assert(ns.Window.GetSnapshot().activeTab == "dungeons", "Bare '/fqp dungeons' should still open Dungeons tab")

-- Jump to Lv42 Badlands scenario to verify dungeon -> outdoor prereq cascade Q722 -> Q723 -> Q724 + rewind on toggle off
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "DungRunner",
    realm = "Forever",
    level = 42,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1418, -- Badlands
    x = 0.42,
    y = 0.52,
}})
Mock.CompleteQuest(721)
local addon2, ns2 = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addon2)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

local steps2, _, pkey2 = ns2.State.GetActiveProfileSteps()
local idxAcc722 = findStepIndex(steps2, 722, "ACCEPT")
local idxAcc723 = findStepIndex(steps2, 723, "ACCEPT")
assert(idxAcc722 and idxAcc723, "Expected Q722 (dungeon prereq) and Q723 (outdoor followup) in balanced route")

-- Jump ONCE to Q722: skips dungeon Q722 and prereq-locked outdoor follow-up Q723, recording both in deferredSteps
ns2.State.JumpToStep(idxAcc722)
local curAfterJump2, _ = ns2.State.GetCurrentStep()
assert(curAfterJump2 and curAfterJump2.questId ~= 722 and curAfterJump2.questId ~= 723,
    "Expected Q722 and outdoor prereq-locked followup Q723 to defer cleanly when skipDungeons=true, landed on "
    .. tostring(curAfterJump2 and curAfterJump2.questId))
local def2 = ns2.charDB.deferredSteps and ns2.charDB.deferredSteps[pkey2]
assert(def2 and def2[idxAcc722] == true and def2[idxAcc723] == true,
    "Expected both Q722 dungeon step and prereq-locked Q723 in deferredSteps")

-- /fqp skipdungeons off unlocks & rewinds cursor back to idxAcc722 so user can run all deferred dungeons together!
Mock.RunSlash("/fqp", "skipdungeons off")
assert(ns2.Window.GetSnapshot().skipDungeons == false, "Expected /fqp skipdungeons off -> false")
local vmAllow = ns2.HUD.GetViewModel()
assert(vmAllow.modeLabel:find("Dung On", 1, true), "Expected HUD modeLabel 'Dung On', got " .. tostring(vmAllow.modeLabel))
local curRewound, _ = ns2.State.GetCurrentStep()
assert(curRewound and curRewound.questId == 722 and curRewound.action == "ACCEPT",
    "Expected toggling skipDungeons OFF to rewind cursor to deferred ACCEPT 722, got "
    .. tostring(curRewound and curRewound.action) .. ":" .. tostring(curRewound and curRewound.questId))

-- /fqp dungeons skip & /fqp dungeons allow & /fqp skipdungeons toggle work consistently
Mock.RunSlash("/fqp", "dungeons skip")
assert(ns2.Window.GetSnapshot().skipDungeons == true, "Expected /fqp dungeons skip -> true")
assert(ns2.State.GetCurrentStep().questId ~= 722, "Expected /fqp dungeons skip to re-defer Q722")
Mock.RunSlash("/fqp", "dungeons allow")
assert(ns2.Window.GetSnapshot().skipDungeons == false, "Expected /fqp dungeons allow -> false")
assert(ns2.State.GetCurrentStep().questId == 722, "Expected /fqp dungeons allow to rewind to Q722")
Mock.RunSlash("/fqp", "skipdungeons on")
assert(ns2.Window.GetSnapshot().skipDungeons == true, "Expected /fqp skipdungeons on -> true")

-- Locate both "Skip Dungeons" checkboxes (Route tab + Settings/Diag tab) and verify sync + 0 allocations
local win2 = Mock.FindFrameByName("ForeverQuestPathWindow")
local dungChks = {{}}
local function collectDungChecks(frame)
    if frame and frame.label and frame.label.GetText then
        local txt = frame.label:GetText()
        if txt and txt:find("Skip Dungeons", 1, true) then
            dungChks[#dungChks + 1] = frame
        end
    end
    for _, ch in ipairs((frame and frame._children) or {{}}) do
        collectDungChecks(ch)
    end
end
ns2.Window.ShowTab("route")
ns2.Window.ShowTab("diag")
collectDungChecks(win2)
assert(#dungChks == 2, "Expected 2 'Skip Dungeons' checkboxes (Route + Diag tabs), found " .. tostring(#dungChks))
assert(dungChks[1].checked == true and dungChks[2].checked == true,
    "Expected both Skip Dungeons checkboxes initially checked")

Mock.ResetMetrics()
ns2.Window.ShowTab("route")
dungChks[1]:Click()
assert(ns2.Window.GetSnapshot().skipDungeons == false, "Clicking Route Skip Dungeons checkbox should set false")
assert(dungChks[1].checked == false and dungChks[2].checked == false, "Both Skip Dungeons checkboxes must sync to false")
assert(ns2.State.GetCurrentStep().questId == 722, "Unchecking Route Skip Dungeons should rewind cursor to Q722")

ns2.Window.ShowTab("diag")
dungChks[2]:Click()
assert(ns2.Window.GetSnapshot().skipDungeons == true, "Clicking Diag Skip Dungeons checkbox should set true")
assert(dungChks[1].checked == true and dungChks[2].checked == true, "Both Skip Dungeons checkboxes must sync to true")
assert(ns2.State.GetCurrentStep().questId ~= 722, "Checking Diag Skip Dungeons should re-defer Q722")

Mock.RunSlash("/fqp", "skipdungeons toggle")
assert(ns2.Window.GetSnapshot().skipDungeons == false, "Expected /fqp skipdungeons toggle -> false")
Mock.RunSlash("/fqp", "dungeons toggle")
assert(ns2.Window.GetSnapshot().skipDungeons == true, "Expected /fqp dungeons toggle -> true")

local m2 = Mock.GetMetrics()
assert(m2.createFrameCalls == 0, "Expected 0 CreateFrame calls during Skip Dungeons transitions, got " .. tostring(m2.createFrameCalls))
assert(m2.createRegionCalls == 0, "Expected 0 CreateRegion calls during Skip Dungeons transitions, got " .. tostring(m2.createRegionCalls))
assert(#Mock.errors == 0, "Unexpected Lua errors in skip-dungeons test: " .. table.concat(Mock.errors, "; "))
print("SKIP_DUNGEON_QUESTS_OPTION_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Skip dungeon quests test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("SKIP_DUNGEON_QUESTS_OPTION_OK", out)

    def test_quest_hover_tooltip_and_click_open_quest_log(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})

Mock.SetCharacter({{
    name = "TooltipRogue",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437, -- Wetlands
    x = 0.108,
    y = 0.596,
}})

_G.QuestieDB = {{
    QueryQuestSingle = function(qid, field)
        if qid == 279 then
            if field == "name" then return "Claws from the Deep"
            elseif field == "requiredLevel" then return 20
            elseif field == "questLevel" then return 22
            elseif field == "objectivesText" then return {{ "Slay Bluegill Murlocs and bring Gobbler's Head to Karl Boran." }}
            elseif field == "triggerEnd" then return {{ "Return Gobbler's Head to Menethil Harbor." }}
            end
        elseif qid == 470 then
            if field == "name" then return "Digging Through the Ooze"
            elseif field == "requiredLevel" then return 19
            elseif field == "questLevel" then return 24
            elseif field == "objectivesText" then return {{ "Recover Sida's Bag from oozes in central Wetlands." }}
            end
        elseif qid == 484 then
            if field == "name" then return "Young Crocolisk Skins"
            elseif field == "requiredLevel" then return 18
            elseif field == "questLevel" then return 22
            elseif field == "objectivesText" then return {{ "Collect 4 Young Crocolisk Skins." }}
            end
        end
        return nil
    end,
    IsDoable = function() return true end,
}}

-- Accept Q279 with live objective progress (3/8) and active quest log narrative text
Mock.AcceptQuest(279, "Claws from the Deep", 22, false)
Mock.SetQuestObjectiveProgress(279, 1, 3, 8, false)
Mock.SetQuestLogText(279,
    "Karl Boran asked you to thin out the Bluegill Murlocs along the coast.",
    "Collect Gobbler's Head and slay Bluegill Murlocs.")

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

assert(ns.version == "1.6.0", "Expected ns.version == 1.6.0, got " .. tostring(ns.version))
assert(type(ns.QuestInfo) == "table", "Expected ns.QuestInfo module to be loaded")

ns.HUD.SetShown(true)
ns.Window.SetShown(true)
ns.Window.ShowTab("route")

local function tooltipToString()
    local parts = {{}}
    for _, ln in ipairs(_G.GameTooltip._lines or {{}}) do
        if ln.text then parts[#parts + 1] = tostring(ln.text) end
        if ln.left then parts[#parts + 1] = tostring(ln.left) .. " " .. tostring(ln.right or "") end
    end
    return table.concat(parts, "\\n")
end

local function findButtons(root, pred, out)
    out = out or {{}}
    if root and pred(root) then
        out[#out + 1] = root
    end
    for _, ch in ipairs((root and root._children) or {{}}) do
        findButtons(ch, pred, out)
    end
    return out
end

Mock.ResetMetrics()

-- 1. Hovering HUD card displays current quest info & respects smart left/right anchoring
local hudFrame = Mock.FindFrameByName("ForeverQuestPathHUD")
assert(hudFrame and hudFrame.card, "Expected ForeverQuestPathHUD.card")
assert(hudFrame.card:GetObjectType() == "Button", "Expected HUD.card to be a clickable Button")

local enterCard = hudFrame.card:GetScript("OnEnter")
local leaveCard = hudFrame.card:GetScript("OnLeave")
assert(enterCard and leaveCard, "Expected OnEnter/OnLeave scripts on HUD.card")
enterCard(hudFrame.card)
assert(_G.GameTooltip._shown == true and _G.GameTooltip:IsOwned(hudFrame.card), "Expected GameTooltip shown & owned by HUD.card")
assert(_G.GameTooltip._anchor == "ANCHOR_RIGHT", "Expected ANCHOR_RIGHT when center x=400 < UIParent/2, got " .. tostring(_G.GameTooltip._anchor))
local cardTip = tooltipToString()
local curStep = ns.State.GetCurrentStep()
local curQ = curStep and curStep.questId and ns.GetData().quests[curStep.questId]
local expectedCurName = (curQ and curQ.name) or (curStep and curStep.title) or ""
assert(expectedCurName ~= "" and cardTip:find(expectedCurName, 1, true),
    "Expected HUD tooltip to include current quest name '" .. tostring(expectedCurName) .. "', got:\\n" .. cardTip)

-- Simulate HUD positioned on right half of screen -> ANCHOR_LEFT
local origCardCenter = hudFrame.card.GetCenter
hudFrame.card.GetCenter = function() return 1500, 300 end
enterCard(hudFrame.card)
assert(_G.GameTooltip._anchor == "ANCHOR_LEFT", "Expected ANCHOR_LEFT when center x=1500 > UIParent/2, got " .. tostring(_G.GameTooltip._anchor))
hudFrame.card.GetCenter = origCardCenter

leaveCard(hudFrame.card)
assert(_G.GameTooltip._shown == false, "Expected GameTooltip hidden on HUD.card OnLeave")

-- Hover HUD upcoming row
local upRow1 = nil
for _, ch in ipairs(hudFrame._children or {{}}) do
    if ch.GetObjectType and ch:GetObjectType() == "Button" and ch.step and ch ~= hudFrame.card then
        upRow1 = ch
        break
    end
end
assert(upRow1 and upRow1:GetScript("OnEnter"), "Expected HUD upcoming row with OnEnter script")
upRow1:GetScript("OnEnter")(upRow1)
assert(_G.GameTooltip._shown == true and _G.GameTooltip:IsOwned(upRow1), "Expected GameTooltip owned by HUD upcoming row")
upRow1:GetScript("OnLeave")(upRow1)
assert(_G.GameTooltip._shown == false, "Expected GameTooltip hidden on HUD upcoming row OnLeave")

-- 2. Locate Route rows for active Q279 and unaccepted Q470/Q484
local win = Mock.FindFrameByName("ForeverQuestPathWindow")
local routeBtns = findButtons(win, function(f) return f.step ~= nil and f.pin ~= nil end)
assert(#routeBtns > 0, "Expected pooled Route rows")

local row279 = nil
local rowUnaccepted = nil
for _, r in ipairs(routeBtns) do
    if r:IsShown() and r.step then
        if r.step.questId == 279 and not row279 then
            row279 = r
        elseif r.step.action == "ACCEPT" and r.step.questId and not rowUnaccepted then
            rowUnaccepted = r
        end
    end
end
if not row279 then
    -- Switch Route filter to "All Steps" if Q279 sits beyond viewport pool
    local allBtns = findButtons(win, function(f) return f.label and f.label.GetText and f.label:GetText() == "All Steps" end)
    if allBtns[1] then
        allBtns[1]:Click("LeftButton")
    end
    for _, r in ipairs(routeBtns) do
        if r:IsShown() and r.step then
            if r.step.questId == 279 and not row279 then row279 = r end
            if r.step.action == "ACCEPT" and r.step.questId and not rowUnaccepted then rowUnaccepted = r end
        end
    end
end
assert(row279, "Expected visible Route row for active quest 279")
assert(rowUnaccepted, "Expected visible Route row for an unaccepted ACCEPT step")

-- Hover active Q279 with QuestieDB present: title, Lv 22, QuestieDB instructions, narrative, and live 3/8 objective progress
row279:GetScript("OnEnter")(row279)
local tip279 = tooltipToString()
assert(tip279:find("Claws from the Deep", 1, true), "Expected Q279 title in tooltip")
assert(tip279:find("Lv 22", 1, true), "Expected Lv 22 in Q279 tooltip")
assert(tip279:find("Slay Bluegill Murlocs and bring Gobbler's Head to Karl Boran.", 1, true),
    "Expected QuestieDB objectivesText instructions in Q279 tooltip, got:\\n" .. tip279)
assert(tip279:find("Karl Boran asked you to thin out the Bluegill Murlocs", 1, true),
    "Expected GetQuestLogQuestText narrative in Q279 tooltip, got:\\n" .. tip279)
assert(tip279:find("3/8", 1, true), "Expected live objective progress 3/8 in Q279 tooltip, got:\\n" .. tip279)
assert(tip279:find("Click: Open in Quest Log", 1, true), "Expected Click hint in Q279 tooltip")
row279:GetScript("OnLeave")(row279)
assert(_G.GameTooltip._shown == false, "Expected tooltip hidden on Route row OnLeave")

-- Hover active Q279 WITHOUT QuestieDB -> GetQuestLogQuestText objectivesText + description fallback must still appear
local savedQDB = _G.QuestieDB
_G.QuestieDB = nil
row279:GetScript("OnEnter")(row279)
local tip279NoQdb = tooltipToString()
assert(tip279NoQdb:find("Collect Gobbler's Head and slay Bluegill Murlocs.", 1, true),
    "Expected GetQuestLogQuestText objectivesText fallback when QuestieDB absent, got:\\n" .. tip279NoQdb)
assert(tip279NoQdb:find("Karl Boran asked you to thin out the Bluegill Murlocs", 1, true),
    "Expected GetQuestLogQuestText description fallback when QuestieDB absent")
row279:GetScript("OnLeave")(row279)
_G.QuestieDB = savedQDB

-- Hover unaccepted quest tooltip via Q470 (GeneratedData objectives + bag reward) and DUNGEON_RUN batch step
ns.QuestInfo.ShowTooltip(rowUnaccepted, {{ questId = 470, rightClickHint = "Right-Click: Pin Waypoint" }})
local tip470 = tooltipToString()
assert(tip470:find("Digging Through the Ooze", 1, true), "Expected Q470 title in tooltip")
assert(tip470:find("Recover Sida's Bag", 1, true), "Expected QuestieDB objectivesText for unaccepted Q470")
assert(tip470:find("Sida's Bag", 1, true) and tip470:find("44.3, 24.7", 1, true), "Expected static objective coords in Q470 tooltip")
assert(tip470:find("10%-Slot Bag") and tip470:find("Ooze%-covered Bag"), "Expected bag & item reward info in Q470 tooltip")
assert(tip470:find("Available %(Not in Quest Log%)"), "Expected unaccepted status label in Q470 tooltip")
ns.QuestInfo.HideTooltip(rowUnaccepted)

ns.QuestInfo.ShowTooltip(rowUnaccepted, {{
    step = {{ action = "DUNGEON_RUN", title = "Deadmines Batch", isDungeon = true, questIds = {{ 279, 470, 484 }} }},
}})
local tipBatch = tooltipToString()
assert(tipBatch:find("Dungeon Batch %(3 Quests%)"), "Expected batch header in DUNGEON_RUN tooltip, got:\\n" .. tipBatch)
assert(tipBatch:find("Claws from the Deep", 1, true) and tipBatch:find("Digging Through the Ooze", 1, true) and tipBatch:find("Young Crocolisk Skins", 1, true),
    "Expected all 3 batch quests in DUNGEON_RUN tooltip")
ns.QuestInfo.HideTooltip(rowUnaccepted)

-- 3. Left-click active Q279 opens Quest Log via QuestMapFrame_OpenToQuestDetails
Mock.state.openedQuestDetailsID = nil
Mock.state.selectedQuestID = nil
_G.WorldMapFrame:Hide()
_G.WorldMapFrame.QuestLog:Hide()
row279:Click("LeftButton")
assert(Mock.state.openedQuestDetailsID == 279, "Expected QuestMapFrame_OpenToQuestDetails(279), got " .. tostring(Mock.state.openedQuestDetailsID))
assert(C_QuestLog.GetSelectedQuest() == 279, "Expected C_QuestLog.GetSelectedQuest() == 279")
assert(_G.WorldMapFrame:IsShown() == true and _G.WorldMapFrame.QuestLog:IsShown() == true,
    "Expected WorldMapFrame and WorldMapFrame.QuestLog shown")

-- Left-click HUD upcoming row & HUD card holding an active quest
if upRow1 and upRow1.step and upRow1.step.questId then
    Mock.AcceptQuest(upRow1.step.questId, upRow1.step.title or "Upcoming Q", 24, false)
    ns.State.Refresh("QUEST_LOG_UPDATE")
end
for _ = 1, 10 do
    local sNow = ns.State.GetCurrentStep()
    local qNow = ns.QuestInfo.ResolvePrimaryQuestId(nil, sNow)
    if (not qNow) or ns.QuestInfo.FindQuestLogIndex(qNow) then
        break
    end
    Mock.AcceptQuest(qNow, sNow.title or ("Quest " .. tostring(qNow)), 24, false)
    ns.State.Refresh("QUEST_LOG_UPDATE")
end
ns.HUD.Refresh()

Mock.state.openedQuestDetailsID = nil
_G.WorldMapFrame:Hide()
_G.WorldMapFrame.QuestLog:Hide()
local cardQid = ns.QuestInfo.ResolvePrimaryQuestId(nil, ns.State.GetCurrentStep())
assert(cardQid and ns.QuestInfo.FindQuestLogIndex(cardQid),
    "Expected current HUD step quest to be in Quest Log, got " .. tostring(cardQid))
hudFrame.card:Click("LeftButton")
assert(Mock.state.openedQuestDetailsID == cardQid,
    "Expected Left-Click on HUD.card to open Quest Log for " .. tostring(cardQid) .. ", got " .. tostring(Mock.state.openedQuestDetailsID))

-- Left-click HUD upcoming row holding an active quest
local up279Row = nil
for _, ch in ipairs(hudFrame._children or {{}}) do
    if ch.GetObjectType and ch:GetObjectType() == "Button" and ch.step and ch ~= hudFrame.card then
        if ns.QuestInfo.FindQuestLogIndex(ns.QuestInfo.ResolvePrimaryQuestId(nil, ch.step)) then
            up279Row = ch
            break
        end
    end
end
if not up279Row then
    upRow1.step = {{ action = "COMPLETE", questId = 279, title = "Claws from the Deep", zoneId = 11, zoneName = "Wetlands", x = 12.2, y = 39.1 }}
    up279Row = upRow1
end
Mock.state.openedQuestDetailsID = nil
up279Row:Click("LeftButton")
assert(Mock.state.openedQuestDetailsID == ns.QuestInfo.ResolvePrimaryQuestId(nil, up279Row.step),
    "Expected Left-Click on HUD upcoming row to open Quest Log")

-- 4. Standalone QuestLogFrame + QuestLog_SetSelection path (use non-CreateFrame table to keep zero-alloc check exact)
local function findVisibleRouteRow(pred)
    local allBtns = findButtons(win, function(f) return f.label and f.label.GetText and f.label:GetText() == "All Steps" end)
    for _, r in ipairs(routeBtns) do
        if r:IsShown() and r.step and pred(r) then return r end
    end
    if allBtns[1] then
        allBtns[1]:Click("LeftButton")
    end
    for _, r in ipairs(routeBtns) do
        if r:IsShown() and r.step and pred(r) then return r end
    end
    return nil
end

row279 = findVisibleRouteRow(function(r) return r.step.questId == 279 end)
assert(row279, "Expected visible Route row for Q279 before standalone QuestLogFrame check")

Mock.state.openedQuestDetailsID = nil
_G.QuestLogFrame = {{
    _shown = false,
    IsShown = function(s) return s._shown end,
    Show = function(s) s._shown = true end,
    Hide = function(s) s._shown = false end,
}}
_G.QuestLogListScrollFrame = {{ ScrollBar = {{ GetValueStep = function() return 16 end, SetValue = function(self, v) _G.__scrollVal = v end }} }}
_G.ShowUIPanel = function(f) if f and f.Show then f:Show() end end
_G.QuestLog_SetSelection = function(idx) _G.__qlSelIdx = idx end
_G.QuestLog_UpdateQuestDetails = function() _G.__qlDetailsUpdated = true end
_G.QuestLog_Update = function() _G.__qlListUpdated = true end

row279:Click("LeftButton")
assert(_G.__qlSelIdx == C_QuestLog.GetLogIndexForQuestID(279),
    "Expected QuestLog_SetSelection(logIndex for 279), got " .. tostring(_G.__qlSelIdx))
assert(_G.QuestLogFrame:IsShown() == true, "Expected QuestLogFrame:Show() via ShowUIPanel")
assert(_G.__qlDetailsUpdated == true and _G.__qlListUpdated == true, "Expected QuestLog_UpdateQuestDetails & QuestLog_Update calls")
assert(Mock.state.openedQuestDetailsID == nil, "Standalone QuestLogFrame path must not invoke QuestMapFrame_OpenToQuestDetails")
_G.QuestLogFrame = nil
_G.QuestLogListScrollFrame = nil
_G.ShowUIPanel = nil
_G.QuestLog_SetSelection = nil
_G.QuestLog_UpdateQuestDetails = nil
_G.QuestLog_Update = nil
_G.__scrollVal = nil
_G.__qlSelIdx = nil
_G.__qlDetailsUpdated = nil
_G.__qlListUpdated = nil

-- 5. Dungeons & Special Rewards & Diagnostics tabs hover & click
ns.Window.ShowTab("dungeons")
local dungBtns = findButtons(win, function(f) return (f.dungeonInfo ~= nil or f.questId ~= nil) and f.pin ~= nil end)
local headerRow, questRow, rewardRow = nil, nil, nil
for _, b in ipairs(dungBtns) do
    if b:IsShown() then
        if b.dungeonInfo and not headerRow then headerRow = b
        elseif b.questId and b.top and not rewardRow then rewardRow = b
        elseif b.questId and b.left and not questRow then questRow = b
        end
    end
end
assert(headerRow and questRow and rewardRow, "Expected visible Dungeon header, Dungeon quest, and Reward rows")

headerRow:GetScript("OnEnter")(headerRow)
assert(_G.GameTooltip._shown == true and _G.GameTooltip:IsOwned(headerRow), "Expected tooltip on Dungeon header")
headerRow:GetScript("OnLeave")(headerRow)

Mock.AcceptQuest(questRow.questId, "Dungeon Quest", 24, false)
questRow:GetScript("OnEnter")(questRow)
assert(_G.GameTooltip._shown == true and _G.GameTooltip:IsOwned(questRow), "Expected tooltip on Dungeon quest row")
questRow:GetScript("OnLeave")(questRow)
Mock.state.openedQuestDetailsID = nil
questRow:Click("LeftButton")
assert(Mock.state.openedQuestDetailsID == questRow.questId, "Expected clicking active Dungeon row to open Quest Log to " .. tostring(questRow.questId))

rewardRow:GetScript("OnEnter")(rewardRow)
assert(_G.GameTooltip._shown == true and _G.GameTooltip:IsOwned(rewardRow), "Expected tooltip on Reward row")
rewardRow:GetScript("OnLeave")(rewardRow)
if rewardRow.pinTarget then
    Mock.state.tomtomWaypoints = {{}}
    rewardRow:Click("RightButton")
    assert(#Mock.state.tomtomWaypoints >= 1, "Expected Right-Click on Reward row to pin waypoint")
end

-- Switch to Settings/Diag tab: verify blocked & candidate rows hover & click cleanly
ns.State.SetMaxLevelOffset(-3)
ns.Window.ShowTab("diag")
local diagRows = findButtons(win, function(f) return f.blockReason ~= nil or (f.step ~= nil and f.pin == nil and f.right ~= nil) end)
local blockedDiagRow, candDiagRow = nil, nil
for _, drow in ipairs(diagRows) do
    if drow:IsShown() then
        if drow.blockReason and not blockedDiagRow then blockedDiagRow = drow
        elseif (not drow.blockReason) and drow.step and not candDiagRow then candDiagRow = drow
        end
    end
end
assert(blockedDiagRow and candDiagRow, "Expected visible Blocked and Candidate rows on Diag tab")
blockedDiagRow:GetScript("OnEnter")(blockedDiagRow)
assert(_G.GameTooltip._shown == true and tooltipToString():find("Locked:", 1, true), "Expected 'Locked:' reason in blocked Diag row tooltip")
blockedDiagRow:GetScript("OnLeave")(blockedDiagRow)
candDiagRow:GetScript("OnEnter")(candDiagRow)
assert(_G.GameTooltip._shown == true and tooltipToString():find("Why:", 1, true), "Expected 'Why:' in candidate Diag row tooltip")
candDiagRow:GetScript("OnLeave")(candDiagRow)
ns.State.SetMaxLevelOffset(2)

-- 6. Unaccepted quest Left-Click opens Quest Log cleanly + pins quest giver without calling QuestMapFrame_ShowQuestDetails
ns.Window.ShowTab("route")
rowUnaccepted = findVisibleRouteRow(function(r)
    return r.step.action == "ACCEPT" and r.step.questId and not ns.QuestInfo.FindQuestLogIndex(r.step.questId)
end)
assert(rowUnaccepted and rowUnaccepted.step.questId, "Expected visible unaccepted ACCEPT row")
Mock.state.openedQuestDetailsID = nil
_G.WorldMapFrame:Hide()
_G.WorldMapFrame.QuestLog:Hide()
Mock.state.tomtomWaypoints = {{}}
Mock.state.chatMessages = {{}}
rowUnaccepted:Click("LeftButton")
assert(Mock.state.openedQuestDetailsID == nil, "Unaccepted quest must not invoke QuestMapFrame_ShowQuestDetails")
assert(_G.WorldMapFrame:IsShown() == true and _G.WorldMapFrame.QuestLog:IsShown() == true,
    "Expected WorldMapFrame & QuestLog opened for unaccepted quest click")
assert(#Mock.state.tomtomWaypoints >= 1, "Expected quest giver waypoint pinned for unaccepted quest click")
local foundNotice = false
for _, msg in ipairs(Mock.state.chatMessages) do
    if tostring(msg):find("not in your Quest Log yet", 1, true) then foundNotice = true break end
end
assert(foundNotice, "Expected helpful chat notice when clicking unaccepted quest")

-- 7. Right-Click on Route row jumps to step; Right-Click on HUD card pins waypoint
if not rowUnaccepted.isDynamic and rowUnaccepted.stepIdx then
    rowUnaccepted:Click("RightButton")
    assert(ns.State.GetCurrentStepIndex() == rowUnaccepted.stepIdx,
        "Expected Right-Click on Route row to jump cursor to stepIdx " .. tostring(rowUnaccepted.stepIdx))
end
Mock.state.tomtomWaypoints = {{}}
Mock.state.openedQuestDetailsID = nil
hudFrame.card:Click("RightButton")
assert(Mock.state.openedQuestDetailsID == nil and #Mock.state.tomtomWaypoints >= 1,
    "Expected Right-Click on HUD.card to pin waypoint without changing Quest Log")

row279 = findVisibleRouteRow(function(r) return r.step.questId == 279 end)
assert(row279, "Expected visible Route row for Q279 before chat-link & guard checks")

-- 8. Shift-Click inserts quest link into active ChatEdit box
_G.IsShiftKeyDown = function() return true end
_G.ChatEdit_GetActiveWindow = function() return {{}} end
_G.ChatEdit_InsertLink = function(txt) _G.__insertedLink = txt end
row279:Click("LeftButton")
assert(_G.__insertedLink and _G.__insertedLink:find("Claws from the Deep", 1, true) and _G.__insertedLink:find("(279)", 1, true),
    "Expected Shift-Click to insert [Claws from the Deep (279)], got " .. tostring(_G.__insertedLink))
_G.IsShiftKeyDown = function() return false end
_G.ChatEdit_GetActiveWindow = nil
_G.ChatEdit_InsertLink = nil
_G.__insertedLink = nil

-- 9. Combat lockdown & Forever controller guards
Mock.state.openedQuestDetailsID = nil
Mock.state.selectedQuestID = nil
_G.WorldMapFrame:Hide()
_G.WorldMapFrame.QuestLog:Hide()
Mock.state.character.inCombat = true
Mock.state.chatMessages = {{}}
row279:Click("LeftButton")
assert(Mock.state.openedQuestDetailsID == nil and _G.WorldMapFrame:IsShown() == false,
    "InCombatLockdown must prevent opening WorldMapFrame / QuestMapFrame")
assert(C_QuestLog.GetSelectedQuest() == 279, "InCombatLockdown may still pre-select quest 279 safely")
Mock.state.character.inCombat = false

Mock.state.openedQuestDetailsID = nil
_G.InputUtil = {{ IsGamepadUIEnabled = function() return true end }}
Mock.state.chatMessages = {{}}
row279:Click("LeftButton")
assert(Mock.state.openedQuestDetailsID == nil, "Controller mode must block opening Quest Log")
local foundPadMsg = false
for _, msg in ipairs(Mock.state.chatMessages) do
    if tostring(msg):find("controller mode", 1, true) then foundPadMsg = true break end
end
assert(foundPadMsg, "Expected controller-mode warning message")
_G.InputUtil = nil

-- 10. Zero frame / region allocations across all hover & click operations!
local mAlloc = Mock.GetMetrics()
assert(mAlloc.createFrameCalls == 0, "Expected 0 CreateFrame calls on hover/click, got " .. tostring(mAlloc.createFrameCalls))
assert(mAlloc.createRegionCalls == 0, "Expected 0 CreateRegion calls on hover/click, got " .. tostring(mAlloc.createRegionCalls))
assert(#Mock.errors == 0, "Unexpected Lua errors in tooltip/quest-log test: " .. table.concat(Mock.errors, "; "))
print("QUEST_HOVER_CLICK_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Quest hover/click test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("QUEST_HOVER_CLICK_OK", out)

    def test_optimal_path_unaccepted_available_quest_suggestions(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})

-- 1A. Unrouted optimal-path quest discovery in Redridge (Q124 A Baying of Gnolls, prereq 119 done) + Skip/StepBack
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Redknight",
    realm = "Forever",
    level = 20,
    raceName = "Human",
    raceFile = "Human",
    raceId = 1,
    className = "Paladin",
    classFile = "PALADIN",
    classId = 2,
    faction = "Alliance",
    mapId = 1433,
    x = 0.259,
    y = 0.473,
}})
Mock.CompleteQuest(119)
local addonRed, nsRed = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonRed)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)

Mock.ResetMetrics()
local stepsRed = nsRed.State.GetActiveProfileSteps()
local redCursor = nil
for idx, st in ipairs(stepsRed) do
    if st.zoneId == 44 and st.action == "ACCEPT" then
        redCursor = idx
        break
    end
end
assert(redCursor ~= nil, "Expected Redridge route step in balanced profile")
nsRed.State.JumpToStep(redCursor)
nsRed.State.CheckLivePosition(true)

local foundRedOpt = false
local curRed, metaRed = nsRed.State.GetCurrentStep()
if curRed and curRed.questId == 124 and curRed.action == "ACCEPT" then
    foundRedOpt = true
end
for _, st in ipairs(nsRed.State.GetUpcomingSteps(45)) do
    if st.questId == 124 and st.action == "ACCEPT" then
        foundRedOpt = true
        break
    end
end
assert(foundRedOpt, "Expected unrouted Redridge optimal-path ACCEPT 124 (A Baying of Gnolls) near Lakeshire (25.9, 47.3)")

-- 1B. Cross-zone quest exclusion (Q1132 Dustwallow turn-in never suggested in Wetlands) & Skip/StepBack on injected step
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 25,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.109,
    y = 0.596,
}})
for _, qid in ipairs({{ 279, 281, 284, 285, 286, 98189, 484, 470, 98282, 305, 98197, 463 }}) do
    Mock.CompleteQuest(qid)
end
local addonGap, nsGap = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonGap)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)

Mock.ResetMetrics()
local curGap, metaGap = nsGap.State.GetCurrentStep()
assert(curGap and curGap.action == "ACCEPT" and curGap.questId == 288,
    "Expected behind-gap Wetlands quest ACCEPT 288 as next step at (10.9, 59.6), got "
    .. tostring(curGap and curGap.action) .. " " .. tostring(curGap and curGap.questId))
assert(metaGap and metaGap.source == "gps_reorder",
    "Expected gps_reorder source for Q288 suggestion, got " .. tostring(metaGap and metaGap.source))

local saw288, saw472 = false, false
if curGap.questId == 288 then saw288 = true end
for _, st in ipairs(nsGap.State.GetUpcomingSteps(40)) do
    assert(st.questId ~= 1132, "Must NEVER suggest cross-zone Dustwallow turn-in quest Q1132 in Wetlands loop")
    if st.action == "ACCEPT" then
        if st.questId == 288 then saw288 = true end
        if st.questId == 472 then saw472 = true end
    end
end
assert(saw288 and saw472, string.format(
    "Expected behind-gap Lv25 Menethil quests ACCEPT 288 & ACCEPT 472, got saw288=%s saw472=%s",
    tostring(saw288), tostring(saw472)))

-- SkipCurrentStep / StepBack cleanly skips and restores injected optimal-path ACCEPT 288 step
nsGap.State.SkipCurrentStep()
local afterSkip288 = nsGap.State.GetCurrentStep()
assert((not afterSkip288) or afterSkip288.questId ~= 288, "Expected SkipCurrentStep to advance past Q288")
nsGap.State.StepBack()
local afterBack288 = nsGap.State.GetCurrentStep()
assert(afterBack288 and afterBack288.action == "ACCEPT" and afterBack288.questId == 288,
    "Expected StepBack to restore ACCEPT 288, got " .. tostring(afterBack288 and afterBack288.questId))

-- Behind-gap Excavation Site quest Q299 at (38.2, 50.9)
Mock.SetPlayerPosition(1437, 38.2, 50.9, "Wetlands", false)
nsGap.State.CheckLivePosition(true)
local saw299 = false
local curExc = nsGap.State.GetCurrentStep()
if curExc and curExc.action == "ACCEPT" and curExc.questId == 299 then saw299 = true end
for _, st in ipairs(nsGap.State.GetUpcomingSteps(30)) do
    if st.action == "ACCEPT" and st.questId == 299 then saw299 = true end
end
assert(saw299, "Expected behind-gap Lv25 Excavation Site quest ACCEPT 299 near (38.2, 50.9)")

-- 2. Same-hub pickup priority before distant recentAcceptedQuests objective (Q98197 Spoils of War)
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.101,
    y = 0.569,
}})
local addonHub, nsHub = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonHub)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)

Mock.AcceptQuest(98197, "Spoils of War", 22, false)
Mock.AdvanceTimers(0.25)

local sHub = nsHub.State.GetCurrentStep()
assert(sHub and sHub.action == "ACCEPT" and sHub.zoneId == 11,
    "Expected same-hub ACCEPT step before distant COMPLETE 98197, got "
    .. tostring(sHub and sHub.action) .. " " .. tostring(sHub and sHub.questId))

local upHub = nsHub.State.GetUpcomingSteps(25)
local complete98197Pos = nil
local complete279Pos = nil
local hubAcceptSet = {{}}
hubAcceptSet[sHub.questId] = 0
for i, st in ipairs(upHub) do
    if st.questId == 98197 and st.action == "COMPLETE" and not complete98197Pos then
        complete98197Pos = i
    elseif st.questId == 279 and st.action == "COMPLETE" and not complete279Pos then
        complete279Pos = i
    elseif st.action == "ACCEPT" and st.x and st.y and st.x <= 15.0 and st.y >= 50.0 and not hubAcceptSet[st.questId] then
        hubAcceptSet[st.questId] = i
    end
end
assert(complete98197Pos ~= nil and complete279Pos ~= nil, "Expected COMPLETE 98197 and COMPLETE 279 in upcoming steps")
assert(hubAcceptSet[484] and hubAcceptSet[484] < complete98197Pos,
    "Expected west-pier ACCEPT 484 before COMPLETE 98197")
assert(hubAcceptSet[279] and hubAcceptSet[279] < complete98197Pos,
    "Expected west-pier ACCEPT 279 before COMPLETE 98197")
for _, rq in ipairs({{ 484, 279, 470, 98282, 305 }}) do
    assert(hubAcceptSet[rq] and hubAcceptSet[rq] < complete279Pos,
        string.format("Expected Menethil ACCEPT %d (pos=%s) before leaving town for COMPLETE 279 (pos=%d)",
            rq, tostring(hubAcceptSet[rq]), complete279Pos))
end

-- Accepting eastern Wetlands quest Q470 at (11.8, 58.0) must pick up remaining hub quests before COMPLETE 470
Mock.SetPlayerPosition(1437, 11.8, 58.0, "Wetlands", false)
Mock.AcceptQuest(470, "Digging Through the Ooze", 24, false)
Mock.AdvanceTimers(0.25)
local sHub470 = nsHub.State.GetCurrentStep()
assert(sHub470 and sHub470.action == "ACCEPT" and sHub470.questId == 98282,
    "Expected adjacent hub ACCEPT 98282 after accepting Q470 at (11.8, 58.0), got "
    .. tostring(sHub470 and sHub470.action) .. " " .. tostring(sHub470 and sHub470.questId))
local upHub470 = nsHub.State.GetUpcomingSteps(20)
local complete470Pos, saw305Before470 = nil, false
for i, st in ipairs(upHub470) do
    if st.questId == 470 and st.action == "COMPLETE" and not complete470Pos then
        complete470Pos = i
    elseif st.action == "ACCEPT" and st.questId == 305 and not complete470Pos then
        saw305Before470 = true
    end
end
assert(complete470Pos ~= nil and saw305Before470,
    "Expected Menethil ACCEPT 305 before leaving town for distant COMPLETE 470")

-- 3. Corridor pickup mid-travel + MaxLv -3 gating + Dynamic Re-Scorer + 0 UI allocations
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sprocketstab",
    realm = "Forever",
    level = 24,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1437,
    x = 0.485,
    y = 0.390,
}})
for _, qid in ipairs({{ 463, 472, 305, 281, 284, 285, 286, 98189, 98221, 98197, 279, 470, 98282, 484 }}) do
    Mock.CompleteQuest(qid)
end
local addonCor, nsCor = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonCor)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)

Mock.ResetMetrics()
Mock.AcceptQuest(276, "Tramping Paws", 21, false)
Mock.AdvanceTimers(0.25)

local curCor = nsCor.State.GetCurrentStep()
assert(curCor and curCor.action == "ACCEPT" and curCor.questId == 469,
    "Expected corridor pickup ACCEPT 469 (49.9, 39.4) before distant COMPLETE 276 (59.9, 73.2), got "
    .. tostring(curCor and curCor.action) .. " " .. tostring(curCor and curCor.questId))

-- MaxLevelOffset = -3 (cap Lv 21) allows Lv 21 Q469 & Q276 while excluding Lv 22+ quests like Q294 (Lv25)
nsCor.State.SetMaxLevelOffset(-3)
for _, st in ipairs(nsCor.State.GetUpcomingSteps(25)) do
    if st.questId then
        local qd = nsCor.GetData().quests[st.questId]
        local qlv = qd and tonumber(qd.questLevel) or 0
        if qlv > 0 and not nsCor.State.GetQuestLogEntry(st.questId) then
            assert(qlv <= 21, string.format("MaxLv=-3 must exclude unaccepted Lv%d quest %d", qlv, st.questId))
        end
    end
end
nsCor.State.SetMaxLevelOffset(2)

-- Dynamic Re-Scorer boosts same-zone nearby ACCEPT within ~25 yds when player only holds distant active quest
Mock.SetPlayerPosition(1437, 10.8, 59.6, "Wetlands", false)
nsCor.State.CheckLivePosition(true)
local dynSteps = nsCor.Scorer.GetDynamicSteps(nsCor.State.GetPlayerInfo(), nsCor.GetActiveWeights(), nsCor.charDB.toggles, 10)
assert(dynSteps and dynSteps[1] and dynSteps[1].action == "ACCEPT" and dynSteps[1].zoneId == 11,
    "Expected Scorer.GetDynamicSteps at Menethil (10.8, 59.6) to suggest nearby ACCEPT before eastern COMPLETE 276, got "
    .. tostring(dynSteps and dynSteps[1] and dynSteps[1].action) .. " " .. tostring(dynSteps and dynSteps[1] and dynSteps[1].questId))

local mOpt = Mock.GetMetrics()
assert(mOpt.createFrameCalls == 0, "Expected 0 CreateFrame calls during optimal-path discovery, got " .. tostring(mOpt.createFrameCalls))
assert(mOpt.createRegionCalls == 0, "Expected 0 CreateRegion calls during optimal-path discovery, got " .. tostring(mOpt.createRegionCalls))
assert(#Mock.errors == 0, "Unexpected Lua errors in optimal-path test: " .. table.concat(Mock.errors, "; "))
print("OPTIMAL_PATH_UNACCEPTED_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Optimal-path unaccepted quest test failed:\\nSTDOUT: {out}\\nSTDERR: {err}")
        self.assertIn("OPTIMAL_PATH_UNACCEPTED_OK", out)

    def test_in_dungeon_quest_suggestions(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})

local function hasStep(list, qid, action)
    for _, st in ipairs(list or {{}}) do
        if st and (qid == nil or st.questId == qid) and (action == nil or st.action == action) then
            return true
        end
    end
    return false
end

local function indexOfStep(list, qid, action)
    for idx, st in ipairs(list or {{}}) do
        if st and (qid == nil or st.questId == qid) and (action == nil or st.action == action) then
            return idx
        end
    end
    return nil
end

-- ============================================================================
-- 1. Deadmines (instMapId=36) while skipDungeons=true & skipEliteSolo=true
-- ============================================================================
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Dungrunner",
    realm = "Forever",
    level = 22,
    raceName = "Human",
    raceFile = "Human",
    raceId = 1,
    className = "Paladin",
    classFile = "PALADIN",
    classId = 2,
    faction = "Alliance",
    mapId = 1437, -- Wetlands
    x = 0.118,
    y = 0.586,
}})
Mock.CompleteQuest(155) -- prereq for Q214 / Q166
Mock.AcceptQuest(214, "Red Silk Bandanas", 17, false, "Dungeon")
Mock.AcceptQuest(166, "The Defias Brotherhood", 22, false, "Dungeon")

local addonDm, nsDm = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonDm)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)

assert(nsDm.charDB.toggles.skipDungeons == true, "Expected skipDungeons default true")
assert(nsDm.charDB.toggles.skipEliteSolo == true, "Expected skipEliteSolo default true")
local pOut = nsDm.State.GetPlayerInfo()
assert(pOut.inInstance == false and pOut.activeDungeonId == nil, "Expected outdoors inInstance=false")
local curOut, metaOut = nsDm.State.GetCurrentStep()
assert(curOut and metaOut.source ~= "in_dungeon" and curOut.questId ~= 214 and curOut.questId ~= 166,
    "Expected skipDungeons=true outdoors to defer incomplete Q214/Q166, got " .. tostring(curOut and curOut.questId))

-- Open HUD & Window so pooled frames also render in dungeon
nsDm.HUD.SetShown(true)
nsDm.Window.SetShown(true)
nsDm.Window.ShowTab("route")

-- Seed an outdoor TomTom waypoint, then zone into The Deadmines (36)
assert(#Mock.state.tomtomWaypoints >= 1, "Expected outdoor waypoint before zoning into dungeon")
Mock.SetInstance(true, 36, "Deadmines", "party", true)
Mock.AdvanceTimers(0.25)

local pIn = nsDm.State.GetPlayerInfo()
assert(pIn.inInstance == true, "Expected player.inInstance == true inside Deadmines")
assert(pIn.activeDungeonId == "the-deadmines", "Expected activeDungeonId == 'the-deadmines', got " .. tostring(pIn.activeDungeonId))
assert(pIn.zoneId == 1581, "Expected zoneId == 1581 inside The Deadmines, got " .. tostring(pIn.zoneId))

local curDm, metaDm = nsDm.State.GetCurrentStep()
assert(curDm and curDm.action == "COMPLETE" and curDm.questId == 214,
    "Expected COMPLETE 214 inside Deadmines, got " .. tostring(curDm and curDm.action) .. " " .. tostring(curDm and curDm.questId))
assert(metaDm and metaDm.source == "in_dungeon", "Expected meta.source == 'in_dungeon', got " .. tostring(metaDm and metaDm.source))
assert(#Mock.state.tomtomWaypoints == 0, "Expected stale outdoor TomTom waypoint cleared on inside-dungeon target")

local upDm = nsDm.State.GetUpcomingSteps(12)
assert(hasStep(upDm, 166, "COMPLETE"), "Expected COMPLETE 166 in upcoming Deadmines steps")
local idxTurn214 = indexOfStep(upDm, 214, "TURN_IN")
local idxTurn166 = indexOfStep(upDm, 166, "TURN_IN")
assert(idxTurn214 and idxTurn166, "Expected post-run outdoor TURN_IN 214 and 166 after objectives")

local vmDm = nsDm.HUD.GetViewModel()
assert(vmDm and vmDm.modeLabel and vmDm.modeLabel:find("In Dungeon", 1, true) and vmDm.modeLabel:find("Dung Active", 1, true),
    "Expected HUD modeLabel to indicate In Dungeon & Dung Active, got " .. tostring(vmDm and vmDm.modeLabel))
assert(vmDm.current and vmDm.current.badgesLine:find("[In Dungeon]", 1, true),
    "Expected HUD badgesLine '[In Dungeon]', got " .. tostring(vmDm.current and vmDm.current.badgesLine))
assert(vmDm.current.whereLine and vmDm.current.whereLine:find("Inside Dungeon", 1, true),
    "Expected HUD whereLine 'Inside Dungeon', got " .. tostring(vmDm.current and vmDm.current.whereLine))

local devDm = nsDm.State.GetDeviationStatus()
assert(devDm and devDm.deviated == false and devDm.gpsMode == "in_dungeon",
    "Expected DeviationStatus non-deviated in_dungeon mode")

local snapDm = nsDm.Window.GetSnapshot()
assert(snapDm and snapDm.routeSummary and snapDm.routeSummary:find("In Dungeon: The Deadmines", 1, true),
    "Expected Window routeSummary 'In Dungeon: The Deadmines', got " .. tostring(snapDm and snapDm.routeSummary))
local saw214Row, saw166Row = false, false
for _, r in ipairs(snapDm.routeRows or {{}}) do
    if r.step and r.step.questId == 214 and r.step.action == "COMPLETE" then
        assert(r.isCurrent == true and r.step.isInDungeon == true, "Expected Q214 highlighted as current in-dungeon row")
        saw214Row = true
    elseif r.step and r.step.questId == 166 and r.step.action == "COMPLETE" then
        saw166Row = true
    end
end
assert(saw214Row and saw166Row, "Expected both Deadmines active quest rows in Window Current Zone routeRows")

-- ============================================================================
-- 2. Gnomeregan (90): Ready internal turn-in #1, outdoor turn-ins after run,
--    and resuming open-world routing on exit
-- ============================================================================
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Gnomecleanser",
    realm = "Forever",
    level = 30,
    raceName = "Gnome",
    raceFile = "Gnome",
    raceId = 7,
    className = "Rogue",
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",
    mapId = 1455, -- Ironforge
    x = 0.68,
    y = 0.49,
}})
Mock.CompleteQuest(2904)
Mock.CompleteQuest(2945)
Mock.CompleteQuest(2947)
Mock.AcceptQuest(2951, "The Sparklematic 5200!", 30, true, "Dungeon")
Mock.AcceptQuest(2924, "Essential Artificials", 30, true, "Dungeon")
Mock.AcceptQuest(2929, "The Grand Betrayal", 35, false, "Dungeon")

local addonGn, nsGn = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonGn)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.AdvanceTimers(0.25)

Mock.SetInstance(true, 90, "Gnomeregan", "party", true)
Mock.AdvanceTimers(0.25)

local curGn, metaGn = nsGn.State.GetCurrentStep()
assert(metaGn and metaGn.source == "in_dungeon", "Expected in_dungeon source inside Gnomeregan")
assert(curGn and curGn.action == "TURN_IN" and curGn.questId == 2951 and curGn.isReadyTurnIn == true and curGn.isInsideDungeonTarget == true,
    "Expected internal ready TURN_IN 2951 as #1 step in Gnomeregan, got " .. tostring(curGn and curGn.action) .. " " .. tostring(curGn and curGn.questId))

local upGn = nsGn.State.GetUpcomingSteps(12)
local idxComp2929 = indexOfStep(upGn, 2929, "COMPLETE")
local idxOut2924 = indexOfStep(upGn, 2924, "TURN_IN")
assert(idxComp2929 and idxOut2924 and idxComp2929 < idxOut2924,
    string.format("Expected COMPLETE 2929 (pos=%s) BEFORE outdoor TURN_IN 2924 (pos=%s)", tostring(idxComp2929), tostring(idxOut2924)))

-- Complete internal turn-in 2951 and finish objectives of 2929 -> outdoor Ironforge turn-ins become #1
Mock.TurnInQuest(2951, 1000, 0)
Mock.SetQuestObjectivesComplete(2929, true)
Mock.AdvanceTimers(0.25)

local curGnDone = nsGn.State.GetCurrentStep()
assert(curGnDone and curGnDone.action == "TURN_IN" and (curGnDone.questId == 2924 or curGnDone.questId == 2929)
    and curGnDone.zoneId == 1537 and curGnDone.x ~= nil and curGnDone.isInsideDungeonTarget == false,
    "Expected Ironforge outdoor TURN_IN (2924/2929) after finishing dungeon objectives, got "
    .. tostring(curGnDone and curGnDone.action) .. " " .. tostring(curGnDone and curGnDone.questId))

-- Turn in 2924 & 2929, re-accept an incomplete Gnomeregan quest, then zone out -> open world defers dungeon quests again
Mock.TurnInQuest(2924, 1500, 0)
Mock.TurnInQuest(2929, 1500, 0)
Mock.AcceptQuest(2928, "Gyrodrillmatic Excavationators", 30, false, "Dungeon")
Mock.SetInstance(false, nil, nil, "none", false)
Mock.SetPlayerPosition(1437, 11.8, 58.6, "Menethil Harbor", true)
Mock.FireEvent("PLAYER_ENTERING_WORLD", false, false)
Mock.AdvanceTimers(0.25)

local pExit = nsGn.State.GetPlayerInfo()
assert(pExit.inInstance == false and pExit.activeDungeonId == nil, "Expected inInstance=false after zoning out")
local curExit, metaExit = nsGn.State.GetCurrentStep()
assert(curExit and metaExit.source ~= "in_dungeon" and curExit.questId ~= 2928,
    "Expected zoning out with skipDungeons=true to resume open-world routing and defer Q2928")

-- ============================================================================
-- 3. Forever Custom Dungeon (Excavation Site: Wetlands 2998) over-offset
--    internal quests (Lv 31 at Lv 26, maxLevelOffset=+2) & internal follow-up
-- ============================================================================
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Dwarfdelver",
    realm = "Forever",
    level = 26,
    raceName = "Dwarf",
    raceFile = "Dwarf",
    raceId = 3,
    className = "Warrior",
    classFile = "WARRIOR",
    classId = 1,
    faction = "Alliance",
    mapId = 1437,
    x = 0.478,
    y = 0.562,
}})
Mock.AcceptQuest(95647, "Lost in the Thicket Things", 31, true, "Dungeon")
Mock.SetQuestObjectivesComplete(95647, true)

local addonEx, nsEx = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonEx)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
Mock.SetInstance(true, 2998, "Excavation Site: Wetlands", "party", true)
Mock.AdvanceTimers(0.25)

assert(nsEx.State.GetPlayerInfo().activeDungeonId == "excavation-site-wetlands",
    "Expected activeDungeonId == 'excavation-site-wetlands'")
local curEx = nsEx.State.GetCurrentStep()
assert(curEx and curEx.action == "TURN_IN" and curEx.questId == 95647 and curEx.isInsideDungeonTarget == true,
    "Expected ready internal TURN_IN 95647 as #1 step in Excavation Site")
local upEx = nsEx.State.GetUpcomingSteps(15)
assert(hasStep(upEx, 95809, "ACCEPT"), "Expected unlocked Lv31 internal ACCEPT 95809 (prereq 95647) despite Lv26 +2 offset")
assert(hasStep(upEx, 95810, "ACCEPT"), "Expected internal Lv31 ACCEPT 95810 despite Lv26 +2 offset")
assert(hasStep(upEx, 95810, "COMPLETE"), "Expected simulated COMPLETE 95810 inside Excavation Site")

-- ============================================================================
-- 4. Wing name resolution (Scarlet Monastery - Library) & instMapId=209
--    (Zul'Farrak, NOT Shadowfang Keep) with 0 active quests
-- ============================================================================
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Sandseeker",
    realm = "Forever",
    level = 45,
    raceName = "Human",
    raceFile = "Human",
    raceId = 1,
    className = "Paladin",
    classFile = "PALADIN",
    classId = 2,
    faction = "Alliance",
    mapId = 1446, -- Tanaris
    x = 0.51,
    y = 0.28,
}})
local addonZf, nsZf = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonZf)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

Mock.SetInstance(true, nil, "Scarlet Monastery - Library", "party", true)
Mock.AdvanceTimers(0.25)
assert(nsZf.State.GetPlayerInfo().activeDungeonId == "scarlet-monastery",
    "Expected wing name 'Scarlet Monastery - Library' -> 'scarlet-monastery', got " .. tostring(nsZf.State.GetPlayerInfo().activeDungeonId))

Mock.SetInstance(true, 209, "Zul'Farrak", "party", true)
Mock.AdvanceTimers(0.25)
local pZf = nsZf.State.GetPlayerInfo()
assert(pZf.activeDungeonId == "zul-farrak" and pZf.zoneId == 1176,
    "Expected instMapId=209 -> 'zul-farrak' (NOT shadowfang-keep!), got " .. tostring(pZf.activeDungeonId))
local curZf, metaZf = nsZf.State.GetCurrentStep()
assert(curZf and metaZf.source == "in_dungeon" and curZf.action == "ACCEPT" and curZf.questId ~= nil,
    "Expected shareable/outdoor-starter Zul'Farrak ACCEPT step on 0-quest entry")

-- ============================================================================
-- 5. Dungeon wipe corpse-run freeze, Spirit Healer outdoor resurrection,
--    SkipCurrentStep / StepBack, Dynamic Re-Scorer, and 0 idle GPS triggers
-- ============================================================================
Mock.Reset({{ tomtom = true, openmap = true }})
Mock.SetCharacter({{
    name = "Wipeguard",
    realm = "Forever",
    level = 22,
    raceName = "Human",
    raceFile = "Human",
    raceId = 1,
    className = "Paladin",
    classFile = "PALADIN",
    classId = 2,
    faction = "Alliance",
    mapId = 1437,
    x = 0.118,
    y = 0.586,
}})
Mock.CompleteQuest(155)
Mock.AcceptQuest(214, "Red Silk Bandanas", 17, false, "Dungeon")
Mock.AcceptQuest(166, "The Defias Brotherhood", 22, false, "Dungeon")

local addonWp, nsWp = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonWp)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
nsWp.HUD.SetShown(true)
nsWp.Window.SetShown(true)
Mock.SetInstance(true, 36, "Deadmines", "party", true)
Mock.AdvanceTimers(0.25)

local curAlive = nsWp.State.GetCurrentStep()
assert(curAlive and curAlive.questId == 214 and curAlive.action == "COMPLETE", "Expected COMPLETE 214 while alive in Deadmines")

-- Wipe inside Deadmines -> Release Spirit to outdoor Westfall graveyard
Mock.SetDead(true, false, true)
Mock.SetInstance(false, nil, nil, "none", false)
Mock.ReleaseSpirit(1436, 56.6, 52.6, "Sentinel Hill")
Mock.FireEvent("QUEST_LOG_UPDATE")
Mock.AdvanceTimers(5.0)
local curGhost, metaGhost = nsWp.State.GetCurrentStep()
assert(metaGhost and metaGhost.source == "in_dungeon" and curGhost and curGhost.questId == 214,
    "Expected corpse-run ghost outside portal to stay frozen on Deadmines COMPLETE 214")

-- Resurrect at outdoor Spirit Healer -> exits in_dungeon mode & resumes open-world
Mock.Resurrect(1436, 56.6, 52.6, "Sentinel Hill")
Mock.AdvanceTimers(0.25)
local pRez = nsWp.State.GetPlayerInfo()
assert(pRez.inInstance == false and pRez.activeDungeonId == nil, "Expected inInstance cleared on outdoor resurrection")
local curRez, metaRez = nsWp.State.GetCurrentStep()
assert(curRez and metaRez.source ~= "in_dungeon" and curRez.questId ~= 214 and curRez.questId ~= 166,
    "Expected outdoor resurrection to resume open-world routing with skipDungeons=true")

-- Re-enter Deadmines -> test SkipCurrentStep / StepBack & Scorer.GetDynamicSteps
Mock.SetInstance(true, 36, "Deadmines", "party", true)
Mock.AdvanceTimers(0.25)
nsWp.State.SkipCurrentStep()
local curAfterSkip = nsWp.State.GetCurrentStep()
assert(curAfterSkip and curAfterSkip.questId == 166 and curAfterSkip.action == "COMPLETE",
    "Expected SkipCurrentStep inside Deadmines to advance to COMPLETE 166, got " .. tostring(curAfterSkip and curAfterSkip.questId))
nsWp.State.StepBack()
local curAfterBack = nsWp.State.GetCurrentStep()
assert(curAfterBack and curAfterBack.questId == 214 and curAfterBack.action == "COMPLETE",
    "Expected StepBack inside Deadmines to restore COMPLETE 214")

local dynDung = nsWp.Scorer.GetDynamicSteps(nsWp.State.GetPlayerInfo(), nsWp.GetActiveWeights(), nsWp.charDB.toggles, 10)
assert(dynDung and dynDung[1] and dynDung[1].questId == 214 and dynDung[1].action == "COMPLETE",
    "Expected Scorer.GetDynamicSteps to prioritize Deadmines COMPLETE 214 even while skipDungeons=true")

-- Idle 30s inside dungeon -> 0 GPS tick triggers & 0 UI allocations
nsWp.State.ResetIdlePerfStats()
Mock.ResetMetrics()
Mock.AdvanceTimers(30.0)
local idleSt = nsWp.State.GetIdlePerfStats()
local mDung = Mock.GetMetrics()
assert(idleSt.gpsTickTriggers == 0, "Expected 0 idle gpsTickTriggers inside dungeon, got " .. tostring(idleSt.gpsTickTriggers))
assert(mDung.createFrameCalls == 0, "Expected 0 CreateFrame calls inside dungeon, got " .. tostring(mDung.createFrameCalls))
assert(mDung.createRegionCalls == 0, "Expected 0 CreateRegion calls inside dungeon, got " .. tostring(mDung.createRegionCalls))
assert(#Mock.errors == 0, "Unexpected Lua errors in in-dungeon test: " .. table.concat(Mock.errors, "; "))
print("IN_DUNGEON_QUEST_SUGGESTIONS_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"In-dungeon quest suggestions test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("IN_DUNGEON_QUEST_SUGGESTIONS_OK", out)

    def test_dynamic_and_route_anti_shuffling_stability(self) -> None:
        lua_script = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset()

-- Part A: Real GeneratedData Stonetalon/Kalimdor vs Ironforge stability (skinning_lw + dynamicRescore)
Mock.SetCharacter({{
    name = "Houdro", realm = "Forever", level = 28,
    raceName = "NightElf", raceFile = "NightElf", raceId = 4,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1440, x = 0.423, y = 0.710,
}})
Mock.SetBindLocation("Ironforge")
Mock.SetHearthCooldown(0, 0)
Mock.SetSkills({{
    {{ name = "Skinning", rank = 225, maxRank = 225 }},
    {{ name = "Leatherworking", rank = 165, maxRank = 225 }},
}}, false)

local addonName, ns = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)

ns.State.SetProfile("skinning_lw")
ns.charDB.toggles.dynamicRescore = true
ns.charDB.toggles.preferDungeonUnlocks = false
ns.charDB.toggles.skipEliteSolo = true
ns.charDB.toggles.skipDungeons = true
ns.charDB.toggles.locationReorder = true
ns.charDB.knownFlightMasters = ns.charDB.knownFlightMasters or {{}}
for _, fm in ipairs({{ "astranaar", "stonetalon peak", "thalanaar", "auberdine", "ironforge", "menethil harbor" }}) do
    ns.charDB.knownFlightMasters[fm] = true
end
ns.Scorer.InvalidateCaches()
ns.State.Refresh("MANUAL_SYNC")

local data = ns.GetData()
local function stepContinent(st)
    local zid = st and st.zoneId
    local z = zid and data.zones and data.zones[zid]
    return z and z.region
end

local beforeSteps = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), nil, ns.charDB.toggles, 6)
assert(#beforeSteps >= 2, "Expected dynamic steps before entering Stonetalon")

Mock.SetPlayerPosition(1442, 0.364, 0.072, "Stonetalon Peak", true)
Mock.AdvanceTimers(3.0)
local stStonetalon1 = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), nil, ns.charDB.toggles, 6)
assert(#stStonetalon1 >= 2, "Expected dynamic steps upon entering Stonetalon")

-- While Kalimdor work remains in the lookahead queue, no Eastern Kingdoms ACCEPT/COMPLETE should jump ahead
local hasKalimdor = false
for idx = 1, #stStonetalon1 do
    if stepContinent(stStonetalon1[idx]) == 1 then
        hasKalimdor = true
    end
end
assert(hasKalimdor, "Expected Kalimdor steps when inside Stonetalon Mountains")
local seenKalimdorAhead = false
for idx = #stStonetalon1, 1, -1 do
    local s = stStonetalon1[idx]
    local reg = stepContinent(s)
    if reg == 1 and (s.action == "ACCEPT" or s.action == "COMPLETE" or s.action == "TURN_IN") then
        seenKalimdorAhead = true
    elseif reg == 0 and (s.action == "ACCEPT" or s.action == "COMPLETE") and seenKalimdorAhead then
        error("Cross-continent EK " .. tostring(s.action) .. " Q" .. tostring(s.questId) .. " jumped ahead of Kalimdor work")
    end
end

-- Synthetic multi-continent test with Hearthstone bound in Ironforge while player enters Stonetalon
data.quests[995001] = {{
    id = 995001, title = "Stonetalon Skinning Patrol", level = 27, minLevel = 24,
    zoneId = 406, zoneName = "Stonetalon Mountains", factionMask = 1, xp = 2400,
    starter = {{ zoneId = 406, x = 37.0, y = 8.0, npcName = "Kalimdor Scout" }},
    objectives = {{ zoneId = 406, x = 42.0, y = 18.0, text = "Skin Stonetalon Beasts" }},
    turnin = {{ zoneId = 406, x = 37.0, y = 8.0, npcName = "Kalimdor Scout" }},
}}
data.quests[995002] = {{
    id = 995002, title = "Needles Hide Delivery", level = 28, minLevel = 25,
    zoneId = 400, zoneName = "Thousand Needles", factionMask = 1, xp = 2600,
    starter = {{ zoneId = 400, x = 89.5, y = 41.2, npcName = "Thalanaar Outrunner" }},
    objectives = {{ zoneId = 400, x = 80.0, y = 45.0, text = "Gather Smooth Hides" }},
    turnin = {{ zoneId = 400, x = 89.5, y = 41.2, npcName = "Thalanaar Outrunner" }},
}}
data.quests[995003] = {{
    id = 995003, title = "Ironforge Artisan Contract", level = 28, minLevel = 24,
    zoneId = 1537, zoneName = "Ironforge", factionMask = 1, xp = 4800,
    hasLeatherworkingReward = true,
    starter = {{ zoneId = 1537, x = 40.0, y = 50.0, npcName = "Ironforge Tanner" }},
    objectives = {{ zoneId = 1537, x = 42.0, y = 52.0, text = "Craft Thick Armor" }},
    turnin = {{ zoneId = 1537, x = 40.0, y = 50.0, npcName = "Ironforge Tanner" }},
}}
ns.Nav.Reset()
ns.Scorer.InvalidateCaches()
ns.Nav.EnsureEligibleIndex(data, ns.State.GetPlayerInfo())
Mock.AcceptQuest(995003, "Ironforge Artisan Contract", 28, false)
ns.State.Refresh("MANUAL_SYNC")

local synSt = ns.Scorer.GetDynamicSteps(ns.State.GetPlayerInfo(), nil, ns.charDB.toggles, 6)
local pos995001, pos995002, pos995003 = nil, nil, nil
for idx, s in ipairs(synSt) do
    if s.questId == 995001 and not pos995001 then pos995001 = idx end
    if s.questId == 995002 and not pos995002 then pos995002 = idx end
    if s.questId == 995003 and not pos995003 then pos995003 = idx end
end
assert(pos995001 ~= nil, "Expected Stonetalon quest 995001 in dynamic queue")
if pos995003 then
    assert(pos995001 < pos995003, "Expected Stonetalon quest ahead of Ironforge 995003")
    if pos995002 then
        assert(pos995002 < pos995003, "Expected Kalimdor Thousand Needles 995002 ahead of cross-continent Ironforge 995003")
    end
end

-- Clean slate for Part B: Single-Use Virtual Hearthstone Lookahead
Mock.Reset()
Mock.SetCharacter({{
    name = "Hearthcheck", realm = "Forever", level = 26,
    raceName = "Human", raceFile = "Human", raceId = 1,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1436, x = 0.560, y = 0.520,
}})
Mock.SetBindLocation("Ironforge")
Mock.SetHearthCooldown(0, 0)

local addonName2, ns2 = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName2)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
ns2.charDB.toggles.dynamicRescore = true
ns2.charDB.knownFlightMasters = ns2.charDB.knownFlightMasters or {{}}
for _, fm in ipairs({{ "sentinel hill", "stormwind", "ironforge", "menethil harbor" }}) do
    ns2.charDB.knownFlightMasters[fm] = true
end

local data2 = ns2.GetData()
data2.quests = {{
    [995010] = {{
        id = 995010, title = "IF Turnin Alpha", level = 26, minLevel = 20,
        zoneId = 1537, zoneName = "Ironforge", factionMask = 1, xp = 4800,
        starter = {{ zoneId = 1537, x = 40.0, y = 50.0, npcName = "IF Captain" }},
        objectives = {{}},
        turnin = {{ zoneId = 1537, x = 40.0, y = 50.0, npcName = "IF Captain" }},
    }},
    [995011] = {{
        id = 995011, title = "IF Turnin Beta", level = 26, minLevel = 20,
        zoneId = 1537, zoneName = "Ironforge", factionMask = 1, xp = 4800,
        starter = {{ zoneId = 1537, x = 41.0, y = 51.0, npcName = "IF Smith" }},
        objectives = {{}},
        turnin = {{ zoneId = 1537, x = 41.0, y = 51.0, npcName = "IF Smith" }},
    }},
    [995012] = {{
        id = 995012, title = "Stormwind Royal Report", level = 26, minLevel = 20,
        zoneId = 1519, zoneName = "Stormwind City", factionMask = 1, xp = 4800,
        starter = {{ zoneId = 1519, x = 55.0, y = 60.0, npcName = "SW Marshal" }},
        objectives = {{}},
        turnin = {{ zoneId = 1519, x = 55.0, y = 60.0, npcName = "SW Marshal" }},
    }},
    [995013] = {{
        id = 995013, title = "Ironforge Late Followup", level = 26, minLevel = 20,
        zoneId = 1537, zoneName = "Ironforge", factionMask = 1, xp = 1600,
        prereqIds = {{ 995012 }},
        starter = {{ zoneId = 1537, x = 40.0, y = 50.0, npcName = "IF Clerk" }},
        objectives = {{ zoneId = 1537, x = 42.0, y = 52.0, text = "Inspect Ironforge Ledger" }},
        turnin = {{ zoneId = 1537, x = 40.0, y = 50.0, npcName = "IF Clerk" }},
    }},
    [995014] = {{
        id = 995014, title = "Stormwind Neighbor Turnin", level = 26, minLevel = 20,
        zoneId = 1519, zoneName = "Stormwind City", factionMask = 1, xp = 1400,
        starter = {{ zoneId = 1519, x = 55.5, y = 60.5, npcName = "SW Aide" }},
        objectives = {{}},
        turnin = {{ zoneId = 1519, x = 55.5, y = 60.5, npcName = "SW Aide" }},
    }},
}}
ns2.Nav.Reset()
ns2.Scorer.InvalidateCaches()
local p2 = ns2.State.GetPlayerInfo()
ns2.Nav.EnsureEligibleIndex(data2, p2)

-- Direct check of vState.hearthAvailable=true vs false pricing on RankAvailableCandidates
Mock.AcceptQuest(995010, "IF Turnin Alpha", 26, true)
Mock.AcceptQuest(995011, "IF Turnin Beta", 26, true)
Mock.AcceptQuest(995012, "Stormwind Royal Report", 26, true)
Mock.AcceptQuest(995014, "Stormwind Neighbor Turnin", 26, true)
ns2.State.Refresh("MANUAL_SYNC")

local pathWithHearth = ns2.Nav.QueryPath(40, 56, 52, 1537, 40, 50, p2, {{ questId = 995010, role = "t" }})
local pathNoHearth = ns2.Nav.QueryPath(40, 56, 52, 1537, 40, 50, p2, {{ questId = 995010, role = "t", allowHearth = false }})
assert(pathWithHearth and pathWithHearth.usedHearth == true, "Expected ready Hearthstone to be used for IF turnin when allowed")
assert(pathNoHearth and (not pathNoHearth.usedHearth), "Expected allowHearth=false to block Hearthstone")
assert((pathNoHearth.totalSec or 0) > (pathWithHearth.totalSec or 0) * 2,
    "Expected travel without Hearthstone to be significantly longer than 12s Hearthstone")

local vRankBase = {{
    player = p2, level = 26, xpPct = 0, zoneId = 40, zoneName = "Westfall", x = 56, y = 52,
    completed = {{}}, active = {{
        [995010] = {{ questId = 995010, isComplete = true }},
        [995012] = {{ questId = 995012, isComplete = true }},
    }},
    chainFollowups = {{}}, inChainQuests = {{}}, newlyUnlocked = {{}},
    stepCounter = 2, isLiveState = false, maxCandidates = 30,
    seedQids = {{ 995010, 995012 }}, hearthAvailable = true,
}}
local function findRankPos(list, qid)
    for i, item in ipairs(list) do
        if item.quest and item.quest.id == qid then return i end
    end
    return nil
end
local rankWithH = ns2.Scorer.RankAvailableCandidates(vRankBase, nil, ns2.charDB.toggles, data2)
vRankBase.hearthAvailable = false
local rankNoH = ns2.Scorer.RankAvailableCandidates(vRankBase, nil, ns2.charDB.toggles, data2)
local pWithH_10, pWithH_12 = findRankPos(rankWithH, 995010), findRankPos(rankWithH, 995012)
local pNoH_10, pNoH_12 = findRankPos(rankNoH, 995010), findRankPos(rankNoH, 995012)
assert(pWithH_10 and pWithH_12 and pWithH_10 < pWithH_12,
    string.format("Expected Hearth-enabled Ironforge 995010 (@%s) ahead of Stormwind 995012 (@%s)", tostring(pWithH_10), tostring(pWithH_12)))
assert(pNoH_10 and pNoH_12 and pNoH_12 < pNoH_10,
    string.format("Expected hearthAvailable=false Stormwind 995012 (@%s) ahead of Ironforge 995010 (@%s)", tostring(pNoH_12), tostring(pNoH_10)))

-- Verify full multi-step simulation consumes Hearthstone once so Stormwind local 995014 stays ahead of second IF Hearth 995013
data2.quests[995010].xp = 6200
data2.quests[995011].xp = 6200
data2.quests[995012].nextQuestIds = {{ 995013 }}
ns2.Nav.Reset()
ns2.Scorer.InvalidateCaches()
ns2.Nav.EnsureEligibleIndex(data2, ns2.State.GetPlayerInfo())
local hsSteps = ns2.Scorer.GetDynamicSteps(ns2.State.GetPlayerInfo(), nil, ns2.charDB.toggles, 8)
local idx995010, idx995012, idx995013, idx995014 = nil, nil, nil, nil
local debugHs = {{}}
for idx, s in ipairs(hsSteps) do
    debugHs[#debugHs + 1] = string.format("%d:%s:%s@%s", idx, tostring(s.action), tostring(s.questId), tostring(s.zoneId))
    if s.questId == 995010 and not idx995010 then idx995010 = idx end
    if s.questId == 995012 and not idx995012 then idx995012 = idx end
    if s.questId == 995013 and not idx995013 then idx995013 = idx end
    if s.questId == 995014 and not idx995014 then idx995014 = idx end
end
assert(idx995010 and idx995012 and idx995014, "Expected IF & Stormwind turn-ins in multi-step plan: " .. table.concat(debugHs, ", "))
assert(idx995010 < idx995012 and idx995012 < idx995014 and ((not idx995013) or idx995014 < idx995013),
    "Expected consumed virtual Hearthstone to keep Stormwind neighbor 995014 ahead of returning to Ironforge 995013")

-- Clean slate for Part C: Walking Target & Queue Sticky Hysteresis + Instant Invalidation
Mock.Reset()
Mock.SetCharacter({{
    name = "Roadrunner", realm = "Forever", level = 24,
    raceName = "Gnome", raceFile = "Gnome", raceId = 7,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1437, x = 0.280, y = 0.500,
}})
Mock.SetHearthCooldown(1, 1800)

local addonName3, ns3 = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName3)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
ns3.charDB.toggles.dynamicRescore = true
local data3 = ns3.GetData()
data3.quests = {{
    [995020] = {{
        id = 995020, title = "West Road Alpha", level = 24, minLevel = 20,
        zoneId = 11, zoneName = "Wetlands", factionMask = 1, xp = 5500,
        starter = {{ zoneId = 11, x = 20.0, y = 50.0, npcName = "West Marshal" }},
        objectives = {{ zoneId = 11, x = 20.0, y = 50.0, text = "Hold West Post" }},
        turnin = {{ zoneId = 11, x = 20.0, y = 50.0, npcName = "West Marshal" }},
    }},
    [995021] = {{
        id = 995021, title = "East Road Beta", level = 24, minLevel = 20,
        zoneId = 11, zoneName = "Wetlands", factionMask = 1, xp = 5500,
        starter = {{ zoneId = 11, x = 42.0, y = 50.0, npcName = "East Marshal" }},
        objectives = {{ zoneId = 11, x = 42.0, y = 50.0, text = "Hold East Post" }},
        turnin = {{ zoneId = 11, x = 42.0, y = 50.0, npcName = "East Marshal" }},
    }},
}}
ns3.Nav.Reset()
ns3.Scorer.InvalidateCaches()
ns3.Nav.EnsureEligibleIndex(data3, ns3.State.GetPlayerInfo())
Mock.AcceptQuest(995020, "West Road Alpha", 24, false)
Mock.AcceptQuest(995021, "East Road Beta", 24, false)
ns3.State.Refresh("MANUAL_SYNC")

local w1 = ns3.Scorer.GetDynamicSteps(ns3.State.GetPlayerInfo(), nil, ns3.charDB.toggles, 4)
assert(w1[1] and w1[1].questId == 995020 and w1[1].action == "COMPLETE",
    "Expected closer 995020 COMPLETE committed initially, got " .. tostring(w1[1] and w1[1].questId))
assert(w1[2] and w1[2].questId == 995020 and w1[2].action == "TURN_IN",
    "Expected 995020 TURN_IN queued second, got " .. tostring(w1[2] and w1[2].questId))
assert(w1[3] and w1[3].questId == 995021 and w1[3].action == "COMPLETE",
    "Expected 995021 COMPLETE queued third, got " .. tostring(w1[3] and w1[3].questId))

-- Repeat call at identical position hits dynamicStepsCache (postKey match)
local w1Repeat = ns3.Scorer.GetDynamicSteps(ns3.State.GetPlayerInfo(), nil, ns3.charDB.toggles, 4)
assert(w1Repeat == w1, "Expected repeat GetDynamicSteps call at identical coordinates to hit dynamicStepsCache")

-- Walk slightly past midpoint toward 995021 (32.5, 50.0): margin < 6.0 sticky bonus and both > 0.12m away
Mock.SetPlayerPosition(1437, 0.325, 0.500, "Wetlands", true)
Mock.AdvanceTimers(3.0)
local w2 = ns3.Scorer.GetDynamicSteps(ns3.State.GetPlayerInfo(), nil, ns3.charDB.toggles, 4)
assert(w2[1] and w2[1].questId == 995020 and w2[1].action == "COMPLETE",
    "Expected sticky hysteresis to hold committed target 995020 while walking past midpoint")
assert(w2[2] and w2[2].questId == 995020 and w2[2].action == "TURN_IN" and w2[3] and w2[3].questId == 995021,
    "Expected sticky hysteresis to preserve queue order while walking")

-- Turning in 995020 changes commitStateSig and immediately advances target to 995021
Mock.TurnInQuest(995020, 5500, 0)
ns3.State.Refresh("QUEST_TURNED_IN")
local w3 = ns3.Scorer.GetDynamicSteps(ns3.State.GetPlayerInfo(), nil, ns3.charDB.toggles, 4)
assert(w3[1] and w3[1].questId == 995021, "Expected immediate target advance to 995021 after turning in 995020")

-- Part D: Continent & RouteDistCoord travel-aware SortReadyZones in State.lua
Mock.Reset()
Mock.SetCharacter({{
    name = "ReadySorter", realm = "Forever", level = 28,
    raceName = "NightElf", raceFile = "NightElf", raceId = 4,
    className = "Rogue", classFile = "ROGUE", classId = 4,
    faction = "Alliance", mapId = 1442, x = 0.364, y = 0.072,
}})
local addonName4, ns4 = Mock.LoadAddonTOC({str(TOC_PATH)!r})
Mock.FireEvent("ADDON_LOADED", addonName4)
Mock.FireEvent("PLAYER_LOGIN")
Mock.FireEvent("PLAYER_ENTERING_WORLD", true, false)
local data4 = ns4.GetData()
data4.quests[995031] = {{
    id = 995031, title = "Westfall Ready 40", level = 18, minLevel = 15,
    zoneId = 40, zoneName = "Westfall", factionMask = 1, xp = 1200,
    turnin = {{ zoneId = 40, x = 56.3, y = 47.5, npcName = "Gryan Stoutmantle" }},
}}
data4.quests[995032] = {{
    id = 995032, title = "Ashenvale Ready 331", level = 25, minLevel = 20,
    zoneId = 331, zoneName = "Ashenvale", factionMask = 1, xp = 1500,
    turnin = {{ zoneId = 331, x = 34.4, y = 48.0, npcName = "Raene Wolfrunner" }},
}}
data4.quests[995033] = {{
    id = 995033, title = "Needles Ready 400", level = 28, minLevel = 24,
    zoneId = 400, zoneName = "Thousand Needles", factionMask = 1, xp = 1500,
    turnin = {{ zoneId = 400, x = 89.5, y = 41.2, npcName = "Thalanaar Warden" }},
}}
Mock.AcceptQuest(995031, "Westfall Ready 40", 18, true)
Mock.AcceptQuest(995032, "Ashenvale Ready 331", 25, true)
Mock.AcceptQuest(995033, "Needles Ready 400", 28, true)
ns4.State.Refresh("MANUAL_SYNC")

local p4 = ns4.State.GetPlayerInfo()
local dummyRouteFixed = {{
    {{ index = 1, questId = 995030, questTitle = "Local Anchor", zoneId = 406, x = 36.4, y = 7.2, action = "COMPLETE" }},
}}
local plan = ns4.State.GetReadyTurnInPlan(dummyRouteFixed, 1, p4, "primary", ns4.charDB.toggles)
local batchZones = {{}}
for _, bStep in ipairs((plan.routeAfter and plan.routeAfter[1]) or plan.immediate or {{}}) do
    if bStep.action == "TURN_IN" and bStep.zoneId then
        batchZones[#batchZones + 1] = bStep.zoneId
    end
end
assert(#batchZones == 3, "Expected 3 batched out-of-zone turn-ins, got " .. tostring(#batchZones))
local zPos = {{}}
for i, zid in ipairs(batchZones) do zPos[zid] = i end
assert(zPos[331] and zPos[400] and zPos[40], "Expected zones 331, 400, 40 in batched plan")
assert(zPos[331] < zPos[40] and zPos[400] < zPos[40],
    string.format("Expected same-continent Kalimdor zones (331@%d, 400@%d) strictly before EK Westfall (40@%d despite 40 < 331)", zPos[331], zPos[400], zPos[40]))
assert(zPos[331] < zPos[400],
    string.format("Expected closer Ashenvale (331@%d) before Thousand Needles (400@%d) from Stonetalon", zPos[331], zPos[400]))

assert(#Mock.errors == 0, "Unexpected Lua errors in anti-shuffling test: " .. table.concat(Mock.errors, "; "))
print("ANTI_SHUFFLING_STABILITY_OK")
"""
        rc, out, err = _run_luajit(lua_script)
        self.assertEqual(rc, 0, f"Anti-shuffling stability test failed:\nSTDOUT: {out}\nSTDERR: {err}")
        self.assertIn("ANTI_SHUFFLING_STABILITY_OK", out)

    def test_deployed_addon_matches_source_directory(self) -> None:
        self.assertTrue(ADDON_DST.exists(), f"Installed addon directory missing: {ADDON_DST}")

        def _collect_hashes(root: Path) -> dict[str, str]:
            out: dict[str, str] = {}
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    rel = p.relative_to(root).as_posix()
                    if rel.startswith(".") or rel.endswith(".DS_Store"):
                        continue
                    out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            return out

        src_files = _collect_hashes(ADDON_SRC)
        dst_files = _collect_hashes(ADDON_DST)
        self.assertGreater(len(src_files), 0, "No files found in addon/")
        self.assertEqual(set(src_files.keys()), set(dst_files.keys()), "Source and installed AddOns file lists differ")
        for rel, digest in src_files.items():
            self.assertEqual(digest, dst_files[rel], f"File content mismatch between addon/{rel} and AddOns/{rel}")


if __name__ == "__main__":
    unittest.main()
