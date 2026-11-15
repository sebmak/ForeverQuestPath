"""Self-tests verifying the headless WoW Forever 16001 Lua mock harness."""

import subprocess
import unittest
from pathlib import Path

WOW_MOCK_LUA = Path("/Users/sebastianmakinson/Documents/ForeverQuestPath/tests/wow_mock.lua")


# Rules for TestWoWForeverMockHarness:
# - Exercises the standalone wow_mock.lua environment under LuaJIT 2.1 (Lua 5.1 semantics).
# - Verifies BuildInfo (16001 / 1.60.1), character unit APIs, positional & table SetCharacter
#   race/class/faction/level/zoneId switching (plus clean reset), skill-line profession APIs
#   (GetNumSkillLines/GetSkillLineInfo), C_QuestLog stateful transitions (incl. ReadyForTurnIn /
#   IsComplete / GetQuestLogTitle isComplete=1), C_Map coordinate pinning & SetPlayerPosition,
#   C_Map coordinate pinning & SetPlayerPosition, UnitOnTaxi / SetOnTaxi flyover guards,
#   IsMounted / SetMounted, GetBindLocation / SetBindLocation, GetItemCooldown / SetHearthCooldown,
#   NumTaxiNodes / TaxiNodeName / TaxiNodeGetType / OpenTaxiMap, C_Timer.NewTicker / AdvanceTimers,
#   UnitIsDead / UnitIsGhost / UnitIsDeadOrGhost / SetDead / ReleaseSpirit / Resurrect,
#   TomTom/OpenMap toggle modes, Frame/Event dispatching, SetQuestObjectiveProgress, and call/frame metrics
#   (incl. getAllCompletedQuestIDsCalls, getQuestObjectivesCalls, getQuestsCompletedCalls).
class TestWoWForeverMockHarness(unittest.TestCase):
    """Sanity checks for tests/wow_mock.lua."""

    def test_mock_apis_and_events(self) -> None:
        lua_code = f"""
local Mock = dofile({str(WOW_MOCK_LUA)!r})
Mock.Reset({{ tomtom = true, openmap = true }})

local ver, build, _, iface = GetBuildInfo()
assert(ver == "1.60.1" and iface == 16001)
assert(UnitLevel("player") == 24)
assert(select(2, UnitRace("player")) == "Gnome")
assert(select(2, UnitClass("player")) == "ROGUE")
assert(UnitFactionGroup("player") == "Alliance")
assert(BreakUpLargeNumbers(1234567) == "1,234,567")

local fired = {{}}
local f = CreateFrame("Frame", "TestEventFrame", UIParent, "BackdropTemplate")
f:SetBackdrop({{ bgFile = "test" }})
f:RegisterEvent("QUEST_ACCEPTED")
f:RegisterEvent("QUEST_TURNED_IN")
f:SetScript("OnEvent", function(_, ev, arg1)
    table.insert(fired, ev .. ":" .. tostring(arg1))
end)

Mock.AcceptQuest(95646, "Horrors in the Highland", 31, false, "Dungeon")
assert(C_QuestLog.IsOnQuest(95646) == true)
assert(C_QuestLog.IsComplete(95646) == false)
assert(C_QuestLog.ReadyForTurnIn(95646) == false)
assert(select(6, GetQuestLogTitle(1)) == nil)
Mock.SetQuestObjectivesComplete(95646, true)
assert(C_QuestLog.IsComplete(95646) == true)
assert(C_QuestLog.ReadyForTurnIn(95646) == true)
assert(select(6, GetQuestLogTitle(1)) == 1)
Mock.TurnInQuest(95646, 6150, 1200)
assert(C_QuestLog.IsQuestFlaggedCompleted(95646) == true)
assert(C_QuestLog.IsOnQuest(95646) == false)
assert(C_QuestLog.ReadyForTurnIn(95646) == false)
assert(fired[1] == "QUEST_ACCEPTED:95646")
assert(fired[2] == "QUEST_TURNED_IN:95646")

local pt = UiMapPoint.CreateFromCoordinates(1437, 0.562, 0.404)
C_Map.SetUserWaypoint(pt)
C_SuperTrack.SetSuperTrackedUserWaypoint(true)
assert(C_Map.HasUserWaypoint() == true)
assert(C_SuperTrack.IsSuperTrackedUserWaypoint() == true)

assert(GetNumSkillLines() == 3)
local sName, sHdr, _, sRank, _, _, sMax = GetSkillLineInfo(2)
assert(sName == "Leatherworking" and not sHdr and sRank == 165 and sMax == 225)
Mock.SetSkills({{ {{ name = "Skinning", rank = 200, maxRank = 225 }} }}, false)
assert(GetNumSkillLines() == 1 and select(1, GetSkillLineInfo(1)) == "Skinning")

local mBefore = Mock.GetMetrics()
assert(mBefore.createFrameCalls >= 1 and mBefore.isQuestFlaggedCompletedCalls >= 1)
Mock.ResetMetrics()
local mAfter = Mock.GetMetrics()
assert(mAfter.createFrameCalls == 0 and mAfter.isQuestFlaggedCompletedCalls == 0)

-- Multi-race/class/faction/level/zoneId positional switching & clean reset
Mock.SetCharacter("Human", "Paladin", "Alliance", 1, 12)
assert(UnitLevel("player") == 1)
assert(select(1, UnitRace("player")) == "Human" and select(3, UnitRace("player")) == 1)
assert(select(2, UnitClass("player")) == "PALADIN" and select(3, UnitClass("player")) == 2)
assert(UnitFactionGroup("player") == "Alliance")
assert(C_Map.GetBestMapForUnit("player") == 1429 and GetRealZoneText() == "Elwynn Forest")

Mock.SetCharacter("Orc", "Warrior", "Horde", 1, 14)
assert(select(1, UnitRace("player")) == "Orc" and select(3, UnitRace("player")) == 2)
assert(select(2, UnitClass("player")) == "WARRIOR" and select(3, UnitClass("player")) == 1)
assert(UnitFactionGroup("player") == "Horde")
assert(C_Map.GetBestMapForUnit("player") == 1411 and GetRealZoneText() == "Durotar")

Mock.SetCharacter("Night Elf", "Druid", "Alliance", 12, 1439)
assert(select(2, UnitRace("player")) == "NightElf" and select(3, UnitRace("player")) == 4)
assert(select(2, UnitClass("player")) == "DRUID" and select(3, UnitClass("player")) == 11)
assert(C_Map.GetBestMapForUnit("player") == 1439 and GetRealZoneText() == "Darkshore")

Mock.Reset()
assert(UnitLevel("player") == 24 and select(2, UnitRace("player")) == "Gnome" and C_Map.GetBestMapForUnit("player") == 1437)
assert(UnitOnTaxi("player") == false)
Mock.SetOnTaxi(true)
assert(UnitOnTaxi("player") == true)
Mock.SetOnTaxi(false)
assert(UnitOnTaxi("player") == false)

Mock.SetPlayerPosition(1431, 74.5, 46.2)
assert(C_Map.GetBestMapForUnit("player") == 1431)
local posObj = C_Map.GetPlayerMapPosition(1431, "player")
local px, py = posObj:GetXY()
assert(math.abs(px - 0.745) < 1e-6 and math.abs(py - 0.462) < 1e-6)

local tickCount = 0
local ticker = C_Timer.NewTicker(1.5, function() tickCount = tickCount + 1 end)
assert(Mock.AdvanceTimers(3.0) == 2 and tickCount == 2)
ticker:Cancel()
assert(Mock.AdvanceTimers(3.0) == 0 and tickCount == 2)

assert(IsMounted() == false)
Mock.SetMounted(true)
assert(IsMounted() == true)
Mock.SetBindLocation("Thelsamar, Loch Modan")
assert(GetBindLocation() == "Thelsamar, Loch Modan")
local hs, hd = C_Container.GetItemCooldown(6948)
assert(hs == 0 and hd == 0)
Mock.SetHearthCooldown(10, 1800)
local hs2, hd2 = GetItemCooldown(6948)
assert(hs2 == 10 and hd2 == 1800)

Mock.OpenTaxiMap({{ "Menethil Harbor, Wetlands", "Thelsamar, Loch Modan" }})
assert(NumTaxiNodes() == 2 and TaxiNodeName(2) == "Thelsamar, Loch Modan" and TaxiNodeGetType(2) == "REACHABLE")

Mock.ResetMetrics()
Mock.AcceptQuest(279, "Claws from the Deep", 22, false)
assert(Mock.SetQuestObjectiveProgress(279, 1, 3, 10, false) == true)
assert(C_QuestLog.IsComplete(279) == false)
local objs = C_QuestLog.GetQuestObjectives(279)
assert(objs[1].numFulfilled == 3 and objs[1].numRequired == 10 and objs[1].finished == false)
assert(Mock.SetQuestObjectiveProgress(279, 1, 10, 10, true) == true)
assert(C_QuestLog.IsComplete(279) == true)
local allDoneIds = C_QuestLog.GetAllCompletedQuestIDs()
local legacyDone = GetQuestsCompleted()
assert(type(allDoneIds) == "table" and type(legacyDone) == "table")
local mCalls = Mock.GetMetrics()
assert(mCalls.getQuestObjectivesCalls == 1 and mCalls.getAllCompletedQuestIDsCalls == 1 and mCalls.getQuestsCompletedCalls == 1)

assert(UnitIsDead("player") == false and UnitIsGhost("player") == false and UnitIsDeadOrGhost("player") == false)
Mock.SetDead(true, false, true)
assert(UnitIsDead("player") == true and UnitIsGhost("player") == false and UnitIsDeadOrGhost("player") == true)
Mock.ReleaseSpirit(1437, 0.145, 0.432, "Baradin Bay")
assert(UnitIsDead("player") == false and UnitIsGhost("player") == true and UnitIsDeadOrGhost("player") == true)
assert(GetSubZoneText() == "Baradin Bay")
Mock.Resurrect(1437, 0.502, 0.484, "Menethil Harbor")
assert(UnitIsDead("player") == false and UnitIsGhost("player") == false and UnitIsDeadOrGhost("player") == false)
assert(GetSubZoneText() == "Menethil Harbor")

print("MOCK_HARNESS_OK")
"""
        proc = subprocess.run(["luajit", "-e", lua_code], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}")
        self.assertIn("MOCK_HARNESS_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
