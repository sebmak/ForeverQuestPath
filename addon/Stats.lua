--------------------------------------------------------------------------------
-- Stats.lua
-- Rules:
--   1. Tracks persistent and per-session leveling journey analytics for
--      ForeverQuestPath: tracked time, /played server sync (TIME_PLAYED_MSG),
--      XP gained & XP/hr (split between Quest Turn-Ins and Combat/Other XP),
--      average time per level, fastest level, ETA to next level and Level 60,
--      distance traveled on foot/mount in yards (filtered against <6yd indoor
--      jitter, >450yd teleports/hearths, dead/ghost state, and taxi flights),
--      flights taken, hearthstone jumps, zones & dungeons visited, gold earned/
--      spent/net, and deaths/deathless streaks.
--   2. All module variables and reusable scratch tables live at the top of the
--      file; OnTickerTick() performs zero C_Map calls, zero table allocations,
--      and never fires STATE_UPDATED so idle CPU stays under budget.
--   3. Uses anti-double-count baseline synchronization for UnitXP/GetMoney
--      across QUEST_TURNED_IN, PLAYER_XP_UPDATE, PLAYER_MONEY, and
--      PLAYER_LEVEL_UP events regardless of client vs mock event ordering.
--------------------------------------------------------------------------------

local _, ns = ...

local Stats = {}
ns.Stats = Stats

local MIN_MOVE_STEP_YARDS = 6.0
local MAX_GROUND_STEP_YARDS = 450.0
local HEARTH_JUMP_MIN_YARDS = 900.0
local YARDS_PER_KM = 1093.6133
local YARDS_PER_MILE = 1760.0

local DEFAULT_STATS_DB = {
    version = 1,
    startLevel = nil,
    startXp = nil,
    trackedElapsedSec = 0,
    currentLevelElapsedSec = 0,
    totalTimePlayedSec = nil,
    levelTimePlayedSec = nil,
    playedSyncClientTime = nil,
    xpGainedTotal = 0,
    xpFromQuests = 0,
    questsCompleted = 0,
    questsAccepted = 0,
    levelsGained = 0,
    distanceYards = 0,
    flightCount = 0,
    flightTimeSec = 0,
    hearthCount = 0,
    goldEarnedCopper = 0,
    goldSpentCopper = 0,
    netCopper = 0,
    deaths = 0,
    deathlessStreakSec = 0,
    bestDeathlessStreakSec = 0,
    dungeonsVisitedCount = 0,
    dungeonsVisited = {},
    zonesVisitedCount = 0,
    zones = {},
    levels = {},
}

local session = {
    baselineReady = false,
    startedAt = 0,
    elapsedSec = 0,
    lastTickSec = 0,
    lastClockRateSec = 0,
    xpGained = 0,
    xpFromQuests = 0,
    questsCompleted = 0,
    questsAccepted = 0,
    levelsGained = 0,
    distanceYards = 0,
    flightCount = 0,
    flightTimeSec = 0,
    hearthCount = 0,
    goldEarnedCopper = 0,
    goldSpentCopper = 0,
    netCopper = 0,
    deaths = 0,
    lastLevel = nil,
    lastXp = 0,
    lastXpMax = 1,
    lastMoney = 0,
    lastOnTaxi = false,
    lastInInst = false,
    lastInstKey = nil,
    lastZoneId = nil,
    lastZoneName = nil,
    lastAnchorZoneId = nil,
    lastAnchorX = nil,
    lastAnchorY = nil,
    pendingQuestXp = 0,
    pendingQuestMoney = 0,
    lastPlayedReqAt = 0,
}

local viewRowsScratch = {}
local sortedZoneScratch = {}
local sortedLevelScratch = {}
local dungeonNamesScratch = {}

local function EnsureDB()
    if not ns.charDB then return nil end
    if type(ns.charDB.stats) ~= "table" then
        ns.charDB.stats = ns.CopyTable(DEFAULT_STATS_DB)
    else
        ns.MergeDefaults(ns.charDB.stats, DEFAULT_STATS_DB)
    end
    return ns.charDB.stats
end

local function EnsureLevelRecord(db, lv, zoneName)
    if not (db and lv) then return nil end
    local rec = db.levels[lv]
    if type(rec) ~= "table" then
        rec = {
            level = lv,
            durationSec = 0,
            xpGained = 0,
            quests = 0,
            distanceYards = 0,
            deaths = 0,
            goldEarnedCopper = 0,
            netCopper = 0,
            startZone = zoneName,
            endZone = zoneName,
            completed = false,
        }
        db.levels[lv] = rec
    elseif zoneName and zoneName ~= "" then
        rec.endZone = zoneName
        if not rec.startZone or rec.startZone == "" then
            rec.startZone = zoneName
        end
    end
    return rec
end

local function EnsureZoneRecord(db, zoneId, zoneName, markVisit)
    if not (db and zoneId) then return nil end
    local rec = db.zones[zoneId]
    if type(rec) ~= "table" then
        rec = {
            zoneId = zoneId,
            zoneName = zoneName or ("Zone " .. tostring(zoneId)),
            visits = 0,
            distanceYards = 0,
            timeSec = 0,
            quests = 0,
            xpGained = 0,
            deaths = 0,
        }
        db.zones[zoneId] = rec
        db.zonesVisitedCount = (db.zonesVisitedCount or 0) + 1
    elseif zoneName and zoneName ~= "" then
        rec.zoneName = zoneName
    end
    if markVisit then
        rec.visits = (rec.visits or 0) + 1
    end
    return rec
end

local function AdvanceClockDelta(dt)
    if dt <= 0 then return end
    local db = EnsureDB()
    session.elapsedSec = session.elapsedSec + dt
    local onTaxi = (UnitOnTaxi and UnitOnTaxi("player")) and true or false
    if onTaxi and not session.lastOnTaxi then
        session.flightCount = session.flightCount + 1
        if db then
            db.flightCount = (db.flightCount or 0) + 1
        end
    end
    session.lastOnTaxi = onTaxi
    if onTaxi then
        session.flightTimeSec = session.flightTimeSec + dt
        if db then
            db.flightTimeSec = (db.flightTimeSec or 0) + dt
        end
    end

    if not db then return end
    db.trackedElapsedSec = (db.trackedElapsedSec or 0) + dt
    db.currentLevelElapsedSec = (db.currentLevelElapsedSec or 0) + dt
    db.deathlessStreakSec = (db.deathlessStreakSec or 0) + dt
    if db.deathlessStreakSec > (db.bestDeathlessStreakSec or 0) then
        db.bestDeathlessStreakSec = db.deathlessStreakSec
    end

    local curLv = session.lastLevel or (UnitLevel and UnitLevel("player")) or 24
    local lRec = EnsureLevelRecord(db, curLv, session.lastZoneName)
    if lRec then
        lRec.durationSec = (lRec.durationSec or 0) + dt
    end

    if session.lastZoneId then
        local zRec = EnsureZoneRecord(db, session.lastZoneId, session.lastZoneName, false)
        if zRec then
            zRec.timeSec = (zRec.timeSec or 0) + dt
        end
    end
    if session.lastInInst and session.lastInstKey and db.dungeonsVisited[session.lastInstKey] then
        local dRec = db.dungeonsVisited[session.lastInstKey]
        dRec.timeSec = (dRec.timeSec or 0) + dt
    end
end

local function FlushClock()
    if not session.baselineReady then return end
    local now = GetTime and tonumber(GetTime()) or nil
    if not now then return end
    local dt = now - (session.lastTickSec or now)
    if dt > 0 then
        session.lastTickSec = now
        AdvanceClockDelta(dt)
    end
end

local function EstablishBaseline()
    local db = EnsureDB()
    local now = GetTime and tonumber(GetTime()) or 0
    local lv = (UnitLevel and tonumber(UnitLevel("player"))) or 24
    local xp = (UnitXP and tonumber(UnitXP("player"))) or 0
    local xpMax = math.max(1, (UnitXPMax and tonumber(UnitXPMax("player"))) or 1)
    local money = (GetMoney and tonumber(GetMoney())) or 0

    session.baselineReady = true
    session.startedAt = now
    session.lastTickSec = now
    session.lastLevel = lv
    session.lastXp = xp
    session.lastXpMax = xpMax
    session.lastMoney = money
    session.lastOnTaxi = (UnitOnTaxi and UnitOnTaxi("player")) and true or false
    session.pendingQuestXp = 0
    session.pendingQuestMoney = 0

    if db then
        if not db.startLevel then db.startLevel = lv end
        if db.startXp == nil then db.startXp = xp end
        EnsureLevelRecord(db, lv, session.lastZoneName)
    end
end

local function CreditXp(db, curLv, dx, questCredit)
    if dx <= 0 and questCredit <= 0 then return end
    local totalAdd = math.max(0, dx)
    local qAdd = math.min(totalAdd, math.max(0, questCredit))
    if totalAdd > 0 then
        session.xpGained = session.xpGained + totalAdd
        if db then
            db.xpGainedTotal = (db.xpGainedTotal or 0) + totalAdd
            local lRec = EnsureLevelRecord(db, curLv, session.lastZoneName)
            if lRec then
                lRec.xpGained = (lRec.xpGained or 0) + totalAdd
            end
            if session.lastZoneId then
                local zRec = EnsureZoneRecord(db, session.lastZoneId, session.lastZoneName, false)
                if zRec then
                    zRec.xpGained = (zRec.xpGained or 0) + totalAdd
                end
            end
        end
    end
    if qAdd > 0 then
        session.xpFromQuests = session.xpFromQuests + qAdd
        if db then
            db.xpFromQuests = (db.xpFromQuests or 0) + qAdd
        end
    end
end

local function CreditMoney(db, curLv, dm)
    if dm == 0 then return end
    if dm > 0 then
        session.goldEarnedCopper = session.goldEarnedCopper + dm
        if db then
            db.goldEarnedCopper = (db.goldEarnedCopper or 0) + dm
            local lRec = EnsureLevelRecord(db, curLv, session.lastZoneName)
            if lRec then
                lRec.goldEarnedCopper = (lRec.goldEarnedCopper or 0) + dm
            end
        end
    else
        local spent = -dm
        session.goldSpentCopper = session.goldSpentCopper + spent
        if db then
            db.goldSpentCopper = (db.goldSpentCopper or 0) + spent
        end
    end
    session.netCopper = session.netCopper + dm
    if db then
        db.netCopper = (db.netCopper or 0) + dm
        local lRec = EnsureLevelRecord(db, curLv, session.lastZoneName)
        if lRec then
            lRec.netCopper = (lRec.netCopper or 0) + dm
        end
    end
end

local function SyncLiveXpAndMoney(sourceTag, questXpHint, questMoneyHint)
    if not session.baselineReady then
        EstablishBaseline()
    end
    local db = EnsureDB()
    local curLv = (UnitLevel and tonumber(UnitLevel("player"))) or session.lastLevel or 24
    local curXp = (UnitXP and tonumber(UnitXP("player"))) or 0
    local curXpMax = math.max(1, (UnitXPMax and tonumber(UnitXPMax("player"))) or session.lastXpMax or 1)
    local curMoney = (GetMoney and tonumber(GetMoney())) or session.lastMoney or 0

    local qXp = tonumber(questXpHint) or 0
    local qMon = tonumber(questMoneyHint) or 0

    local xpDelta = 0
    if curLv == session.lastLevel then
        if curXp > session.lastXp then
            xpDelta = curXp - session.lastXp
        end
    elseif curLv > session.lastLevel then
        local oldLv = session.lastLevel
        local lvlGap = curLv - oldLv
        if lvlGap == 1 and session.pendingQuestXp > 0 then
            xpDelta = math.max(curXp, session.pendingQuestXp)
            session.pendingQuestXp = 0
        elseif lvlGap == 1 and qXp > 0 then
            xpDelta = math.max(curXp, qXp)
        elseif lvlGap == 1 and sourceTag == "PLAYER_XP_UPDATE" then
            xpDelta = math.max(0, (session.lastXpMax - session.lastXp) + curXp)
        elseif lvlGap == 1 and curXp > 0 and (session.lastXpMax - session.lastXp) <= math.max(5000, session.lastXpMax * 0.25) then
            xpDelta = math.max(0, (session.lastXpMax - session.lastXp) + curXp)
        else
            xpDelta = math.max(0, curXp)
        end

        if db then
            local prevRec = EnsureLevelRecord(db, oldLv, session.lastZoneName)
            if prevRec then
                prevRec.completed = true
            end
            db.levelsGained = (db.levelsGained or 0) + lvlGap
            db.currentLevelElapsedSec = 0
            EnsureLevelRecord(db, curLv, session.lastZoneName)
        end
        session.levelsGained = session.levelsGained + lvlGap
    end

    local qCredit = 0
    if qXp > 0 then
        if xpDelta > 0 then
            qCredit = math.min(xpDelta, qXp)
        else
            session.pendingQuestXp = session.pendingQuestXp + qXp
        end
    elseif xpDelta > 0 and session.pendingQuestXp > 0 then
        qCredit = math.min(xpDelta, session.pendingQuestXp)
        session.pendingQuestXp = math.max(0, session.pendingQuestXp - qCredit)
    end

    CreditXp(db, curLv, xpDelta, qCredit)

    local moneyDelta = curMoney - (session.lastMoney or curMoney)
    CreditMoney(db, curLv, moneyDelta)
    if qMon > 0 and moneyDelta <= 0 then
        session.pendingQuestMoney = session.pendingQuestMoney + qMon
    else
        session.pendingQuestMoney = 0
    end

    session.lastLevel = curLv
    session.lastXp = curXp
    session.lastXpMax = curXpMax
    session.lastMoney = curMoney
end

function Stats.Initialize()
    EnsureDB()
    EstablishBaseline()
end

function Stats.OnEnteringWorld()
    FlushClock()
    EstablishBaseline()
end

function Stats.OnTickerTick()
    if not session.baselineReady then return end
    local now = GetTime and tonumber(GetTime()) or nil
    if not now then return end
    local dt = now - (session.lastTickSec or now)
    if dt > 0 then
        session.lastTickSec = now
        AdvanceClockDelta(dt)
    end
    if ns.Window and ns.Window.RefreshStatsIfShown then
        ns.Window.RefreshStatsIfShown()
    end
end

function Stats.RecordMovement(zoneId, px, py, zoneName, inInst, instName, isDead, onTaxi)
    if not session.baselineReady then
        EstablishBaseline()
    end
    FlushClock()
    if isDead then return end
    local db = EnsureDB()

    if inInst then
        local iKey = tostring(zoneId or instName or "instance")
        if (not session.lastInInst) or (session.lastInstKey ~= iKey) then
            if db then
                local dRec = db.dungeonsVisited[iKey]
                if not dRec then
                    dRec = {
                        key = iKey,
                        name = instName or zoneName or "Dungeon",
                        visits = 0,
                        timeSec = 0,
                        deaths = 0,
                    }
                    db.dungeonsVisited[iKey] = dRec
                    db.dungeonsVisitedCount = (db.dungeonsVisitedCount or 0) + 1
                elseif instName and instName ~= "" then
                    dRec.name = instName
                end
                dRec.visits = (dRec.visits or 0) + 1
            end
        end
        session.lastInInst = true
        session.lastInstKey = iKey
    else
        session.lastInInst = false
        session.lastInstKey = nil
    end

    if zoneId then
        local zChanged = (session.lastZoneId ~= nil and session.lastZoneId ~= zoneId)
        local markVisit = (session.lastZoneId == nil) or zChanged
        session.lastZoneId = zoneId
        if zoneName and zoneName ~= "" then
            session.lastZoneName = zoneName
        end
        if db then
            EnsureZoneRecord(db, zoneId, zoneName, markVisit)
            local lRec = session.lastLevel and EnsureLevelRecord(db, session.lastLevel, zoneName)
            if lRec and zoneName and zoneName ~= "" then
                lRec.endZone = zoneName
            end
        end
    end

    if onTaxi or (UnitOnTaxi and UnitOnTaxi("player")) then
        if zoneId and px and py then
            session.lastAnchorZoneId = zoneId
            session.lastAnchorX = px
            session.lastAnchorY = py
        end
        return
    end

    if (not zoneId) or (px == nil) or (py == nil) then
        return
    end

    if session.lastAnchorZoneId == zoneId and session.lastAnchorX ~= nil and session.lastAnchorY ~= nil then
        local yards
        if ns.Nav and ns.Nav.ZoneCoordsToYards and ns.NavData then
            yards = ns.Nav.ZoneCoordsToYards(ns.NavData, zoneId, session.lastAnchorX, session.lastAnchorY, px, py)
        else
            local dx = px - session.lastAnchorX
            local dy = py - session.lastAnchorY
            yards = math.sqrt(dx * dx + dy * dy) * 35.0
        end
        if yards < MIN_MOVE_STEP_YARDS then
            return
        elseif yards <= MAX_GROUND_STEP_YARDS then
            session.distanceYards = session.distanceYards + yards
            if db then
                db.distanceYards = (db.distanceYards or 0) + yards
                local curLv = session.lastLevel or (UnitLevel and tonumber(UnitLevel("player"))) or 24
                local lRec = EnsureLevelRecord(db, curLv, zoneName)
                if lRec then
                    lRec.distanceYards = (lRec.distanceYards or 0) + yards
                end
                local zRec = EnsureZoneRecord(db, zoneId, zoneName, false)
                if zRec then
                    zRec.distanceYards = (zRec.distanceYards or 0) + yards
                end
            end
            session.lastAnchorX = px
            session.lastAnchorY = py
            return
        elseif yards >= HEARTH_JUMP_MIN_YARDS then
            session.hearthCount = session.hearthCount + 1
            if db then
                db.hearthCount = (db.hearthCount or 0) + 1
            end
        end
    end

    session.lastAnchorZoneId = zoneId
    session.lastAnchorX = px
    session.lastAnchorY = py
end

function Stats.OnQuestAccepted(qid)
    if not session.baselineReady then EstablishBaseline() end
    FlushClock()
    local db = EnsureDB()
    session.questsAccepted = session.questsAccepted + 1
    if db then
        db.questsAccepted = (db.questsAccepted or 0) + 1
    end
end

function Stats.OnQuestTurnedIn(qid, xpReward, moneyReward)
    FlushClock()
    SyncLiveXpAndMoney("QUEST_TURNED_IN", xpReward, moneyReward)
    local db = EnsureDB()
    session.questsCompleted = session.questsCompleted + 1
    if db then
        db.questsCompleted = (db.questsCompleted or 0) + 1
        local curLv = session.lastLevel or 24
        local lRec = EnsureLevelRecord(db, curLv, session.lastZoneName)
        if lRec then
            lRec.quests = (lRec.quests or 0) + 1
        end
        if session.lastZoneId then
            local zRec = EnsureZoneRecord(db, session.lastZoneId, session.lastZoneName, false)
            if zRec then
                zRec.quests = (zRec.quests or 0) + 1
            end
        end
    end
end

function Stats.OnXpUpdate()
    FlushClock()
    SyncLiveXpAndMoney("PLAYER_XP_UPDATE", 0, 0)
end

function Stats.OnMoneyChanged()
    FlushClock()
    SyncLiveXpAndMoney("PLAYER_MONEY", 0, 0)
end

function Stats.OnLevelUp(newLevel)
    FlushClock()
    local targetLv = tonumber(newLevel) or (UnitLevel and tonumber(UnitLevel("player")))
    if targetLv and session.lastLevel and targetLv > session.lastLevel then
        local db = EnsureDB()
        local prevRec = db and EnsureLevelRecord(db, session.lastLevel, session.lastZoneName) or nil
        if prevRec then
            prevRec.completed = true
        end
    end
    SyncLiveXpAndMoney("PLAYER_LEVEL_UP", 0, 0)
    local db = EnsureDB()
    if db and targetLv then
        EnsureLevelRecord(db, targetLv, session.lastZoneName)
    end
end

function Stats.OnPlayerDead()
    if not session.baselineReady then EstablishBaseline() end
    FlushClock()
    local db = EnsureDB()
    session.deaths = session.deaths + 1
    if db then
        db.deaths = (db.deaths or 0) + 1
        db.deathlessStreakSec = 0
        local curLv = session.lastLevel or (UnitLevel and tonumber(UnitLevel("player"))) or 24
        local lRec = EnsureLevelRecord(db, curLv, session.lastZoneName)
        if lRec then
            lRec.deaths = (lRec.deaths or 0) + 1
        end
        if session.lastZoneId then
            local zRec = EnsureZoneRecord(db, session.lastZoneId, session.lastZoneName, false)
            if zRec then
                zRec.deaths = (zRec.deaths or 0) + 1
            end
        end
        if session.lastInInst and session.lastInstKey and db.dungeonsVisited[session.lastInstKey] then
            local dRec = db.dungeonsVisited[session.lastInstKey]
            dRec.deaths = (dRec.deaths or 0) + 1
        end
    end
end

function Stats.OnTimePlayedMsg(totalSec, levelSec)
    local t = tonumber(totalSec)
    local l = tonumber(levelSec)
    if not t then return end
    local db = EnsureDB()
    local now = GetTime and tonumber(GetTime()) or 0
    if db then
        db.totalTimePlayedSec = t
        db.levelTimePlayedSec = l
        db.playedSyncClientTime = now
        if l and l > (db.currentLevelElapsedSec or 0) then
            db.currentLevelElapsedSec = l
        end
    end
end

function Stats.RequestTimePlayedSync()
    if RequestTimePlayed then
        local now = GetTime and tonumber(GetTime()) or 0
        if (now - (session.lastPlayedReqAt or 0)) >= 2.0 or session.lastPlayedReqAt == 0 then
            session.lastPlayedReqAt = now
            pcall(RequestTimePlayed)
            return true
        end
    end
    return false
end

function Stats.ResetSession()
    local now = GetTime and tonumber(GetTime()) or 0
    local lv = (UnitLevel and tonumber(UnitLevel("player"))) or session.lastLevel or 24
    local xp = (UnitXP and tonumber(UnitXP("player"))) or 0
    local xpMax = math.max(1, (UnitXPMax and tonumber(UnitXPMax("player"))) or 1)
    local money = (GetMoney and tonumber(GetMoney())) or 0
    session.startedAt = now
    session.lastTickSec = now
    session.elapsedSec = 0
    session.xpGained = 0
    session.xpFromQuests = 0
    session.questsCompleted = 0
    session.questsAccepted = 0
    session.levelsGained = 0
    session.distanceYards = 0
    session.flightCount = 0
    session.flightTimeSec = 0
    session.hearthCount = 0
    session.goldEarnedCopper = 0
    session.goldSpentCopper = 0
    session.netCopper = 0
    session.deaths = 0
    session.lastLevel = lv
    session.lastXp = xp
    session.lastXpMax = xpMax
    session.lastMoney = money
    session.pendingQuestXp = 0
    session.pendingQuestMoney = 0
    if ns.Window and ns.Window.RefreshStatsIfShown then
        ns.Window.RefreshStatsIfShown(true)
    end
end

function Stats.ResetAll()
    if ns.charDB then
        ns.charDB.stats = ns.CopyTable(DEFAULT_STATS_DB)
    end
    session.lastAnchorZoneId = nil
    session.lastAnchorX = nil
    session.lastAnchorY = nil
    Stats.ResetSession()
    EstablishBaseline()
    if ns.Window and ns.Window.RefreshStatsIfShown then
        ns.Window.RefreshStatsIfShown(true)
    end
end

function Stats.FormatDuration(seconds)
    local s = math.max(0, math.floor((tonumber(seconds) or 0) + 0.5))
    if s < 60 then
        return string.format("%ds", s)
    end
    local mins = math.floor(s / 60)
    local remS = s % 60
    if mins < 60 then
        return string.format("%dm %ds", mins, remS)
    end
    local hrs = math.floor(mins / 60)
    local remM = mins % 60
    if hrs < 24 then
        return string.format("%dh %dm", hrs, remM)
    end
    local days = math.floor(hrs / 24)
    local remH = hrs % 24
    return string.format("%dd %dh %dm", days, remH, remM)
end

function Stats.FormatDistance(yards)
    local y = math.max(0, tonumber(yards) or 0)
    local roundedY = math.floor(y + 0.5)
    local km = y / YARDS_PER_KM
    local mi = y / YARDS_PER_MILE
    if km >= 1.0 or mi >= 0.5 then
        return string.format("%s yds (%.2f km / %.2f mi)", ns.FormatNumber(roundedY), km, mi)
    end
    return string.format("%s yds (%.2f km)", ns.FormatNumber(roundedY), km)
end

local function ComputeRemainingXpTo60(curLv, curXp, curXpMax)
    if curLv >= 60 then
        return 0
    end
    local data = ns.GetData and ns.GetData() or nil
    local curve = data and data.xpCurve
    local remFirst = math.max(0, (curXpMax or 1) - (curXp or 0))
    local sum = remFirst
    for lv = curLv + 1, 59 do
        local c = curve and (tonumber(curve[lv]) or tonumber(curve[tostring(lv)])) or nil
        if not c then
            c = (ns.Scorer and ns.Scorer.XpToLevel and ns.Scorer.XpToLevel(lv, data)) or (lv * 2200)
        end
        sum = sum + c
    end
    return sum
end

function Stats.GetViewModel(viewMode, playerHint)
    FlushClock()
    local db = EnsureDB() or DEFAULT_STATS_DB
    local p = playerHint or (ns.State and ns.State.GetPlayerInfo and ns.State.GetPlayerInfo()) or {}
    local curLv = tonumber(p.level) or session.lastLevel or (UnitLevel and tonumber(UnitLevel("player"))) or 24
    local curXp = (UnitXP and tonumber(UnitXP("player"))) or session.lastXp or 0
    local curXpMax = math.max(1, (UnitXPMax and tonumber(UnitXPMax("player"))) or session.lastXpMax or 1)
    local curXpPct = math.floor((curXp / curXpMax) * 100 + 0.5)

    local sElapsed = math.max(0, session.elapsedSec or 0)
    local tElapsed = math.max(0, db.trackedElapsedSec or 0)
    local now = GetTime and tonumber(GetTime()) or 0

    local totalPlayedLive = nil
    local levelPlayedLive = nil
    if db.totalTimePlayedSec then
        local sinceSync = math.max(0, now - (db.playedSyncClientTime or now))
        totalPlayedLive = db.totalTimePlayedSec + sinceSync
        if db.levelTimePlayedSec then
            levelPlayedLive = db.levelTimePlayedSec + sinceSync
        end
    end

    local curLevelElapsed = levelPlayedLive or (db.currentLevelElapsedSec or 0)
    if curLevelElapsed <= 0 and db.levels and db.levels[curLv] then
        curLevelElapsed = db.levels[curLv].durationSec or 0
    end

    local sessXpHr = (sElapsed >= 1) and ((session.xpGained / sElapsed) * 3600.0) or 0
    local totalXpHr = (tElapsed >= 1) and (((db.xpGainedTotal or 0) / tElapsed) * 3600.0) or 0
    local effectiveXpPerSec = ((tElapsed >= 1 and (db.xpGainedTotal or 0) > 0) and ((db.xpGainedTotal or 0) / tElapsed))
        or ((sElapsed >= 1 and session.xpGained > 0) and (session.xpGained / sElapsed))
        or 0

    -- Average time per level across completed tracked levels, falling back to /played or live XP pace
    local completedSum = 0
    local completedCount = 0
    local fastestLv = nil
    local fastestSec = nil
    for lv, lRec in pairs(db.levels or {}) do
        if type(lRec) == "table" and lRec.completed and (lRec.durationSec or 0) > 0 and lv < curLv then
            local dur = lRec.durationSec
            completedSum = completedSum + dur
            completedCount = completedCount + 1
            if (not fastestSec) or dur < fastestSec then
                fastestSec = dur
                fastestLv = tonumber(lv)
            end
        end
    end

    local avgLevelSec = 0
    local avgLevelSource = "none"
    if completedCount > 0 then
        avgLevelSec = completedSum / completedCount
        avgLevelSource = "completed"
    elseif totalPlayedLive and curLv > 1 then
        avgLevelSec = totalPlayedLive / (curLv - 1)
        avgLevelSource = "played"
    elseif effectiveXpPerSec > 0 then
        avgLevelSec = curXpMax / effectiveXpPerSec
        avgLevelSource = "projected"
    end

    local remCurXp = math.max(0, curXpMax - curXp)
    local etaNextSec = (effectiveXpPerSec > 0 and curLv < 60) and (remCurXp / effectiveXpPerSec) or nil
    local remXpTo60 = ComputeRemainingXpTo60(curLv, curXp, curXpMax)
    local eta60Sec = (effectiveXpPerSec > 0 and curLv < 60) and (remXpTo60 / effectiveXpPerSec) or nil

    local totalYds = db.distanceYards or 0
    local sessYds = session.distanceYards or 0
    local avgYdsPerMin = (tElapsed >= 1) and ((totalYds / tElapsed) * 60.0) or 0

    local qXpPct = ((db.xpGainedTotal or 0) > 0) and math.floor(((db.xpFromQuests or 0) / db.xpGainedTotal) * 100 + 0.5) or 0
    local otherXpTotal = math.max(0, (db.xpGainedTotal or 0) - (db.xpFromQuests or 0))
    local sessOtherXp = math.max(0, (session.xpGained or 0) - (session.xpFromQuests or 0))
    local avgXpPerQuest = ((db.questsCompleted or 0) > 0) and math.floor(((db.xpFromQuests or 0) / db.questsCompleted) + 0.5) or 0
    local goldPerHrCopper = (tElapsed >= 1) and math.floor(((db.netCopper or 0) / tElapsed) * 3600.0) or 0

    local avgLevelText
    if avgLevelSource == "completed" then
        avgLevelText = string.format("%s (%d completed %s)", Stats.FormatDuration(avgLevelSec), completedCount, completedCount == 1 and "lvl" or "lvls")
    elseif avgLevelSource == "played" then
        avgLevelText = string.format("%s (from /played)", Stats.FormatDuration(avgLevelSec))
    elseif avgLevelSource == "projected" then
        avgLevelText = string.format("%s (est. current pace)", Stats.FormatDuration(avgLevelSec))
    else
        avgLevelText = "-- (gain XP to measure)"
    end

    local topZoneName, topZoneYds = "--", 0
    for _, zRec in pairs(db.zones or {}) do
        if type(zRec) == "table" and (zRec.distanceYards or 0) >= topZoneYds then
            topZoneYds = zRec.distanceYards or 0
            topZoneName = zRec.zoneName or topZoneName
        end
    end

    local paceSummary = string.format(
        "|cff33d9ffPace & XP|r  Lv %d (%d%%)  |  Avg Time/Level: |cffffffff%s|r  |  XP/hr: |cffc080ff%s|r (Session: %s)\n"
        .. "Tracked Time: |cffffffff%s|r (Session: %s)  |  Current Level: |cffffffff%s|r  |  ETA Next Lv: |cff44ee77%s|r  |  ETA 60: |cff33ffcc%s|r",
        curLv,
        curXpPct,
        avgLevelText,
        ns.FormatNumber(math.floor(totalXpHr + 0.5)),
        ns.FormatNumber(math.floor(sessXpHr + 0.5)),
        Stats.FormatDuration(tElapsed),
        Stats.FormatDuration(sElapsed),
        Stats.FormatDuration(curLevelElapsed),
        etaNextSec and Stats.FormatDuration(etaNextSec) or "--",
        (curLv >= 60) and "Max" or (eta60Sec and Stats.FormatDuration(eta60Sec) or "--")
    )

    local travelSummary = string.format(
        "|cff66b2ffTravel & World|r  Distance: |cffffffff%s|r  |  Session: |cffffffff%s|r\n"
        .. "Avg Ground Speed: |cffffffff%.0f yd/min|r  |  Zones: |cffffffff%d|r (Top: %s)  |  Dungeons: |cffffffff%d|r  |  Flights: |cffffffff%d|r (%s)  |  Hearths: |cffffffff%d|r",
        Stats.FormatDistance(totalYds),
        Stats.FormatDistance(sessYds),
        avgYdsPerMin,
        db.zonesVisitedCount or 0,
        topZoneName,
        db.dungeonsVisitedCount or 0,
        db.flightCount or 0,
        Stats.FormatDuration(db.flightTimeSec or 0),
        db.hearthCount or 0
    )

    local netSign = ((db.netCopper or 0) < 0) and "|cffff6644-|r" or ""
    local questEconSummary = string.format(
        "|cff33ff99Quests, Gold & Survival|r  Quests Done: |cffffffff%d|r (Session: %d)  |  Quest XP: |cffc080ff%s|r (%d%%) vs Combat/Explore: |cffffffff%s|r  |  Avg XP/Q: |cffffffff%s|r\n"
        .. "Gold Earned: %s  |  Spent: %s  |  Net: %s%s (%s/hr)  |  Deaths: |cffff6644%d|r  |  Deathless Streak: |cff44ee77%s|r",
        db.questsCompleted or 0,
        session.questsCompleted or 0,
        ns.FormatNumber(db.xpFromQuests or 0),
        qXpPct,
        ns.FormatNumber(otherXpTotal),
        ns.FormatNumber(avgXpPerQuest),
        ns.FormatMoney(db.goldEarnedCopper or 0),
        ns.FormatMoney(db.goldSpentCopper or 0),
        netSign,
        ns.FormatMoney(math.abs(db.netCopper or 0)),
        ns.FormatMoney(math.max(0, goldPerHrCopper)),
        db.deaths or 0,
        Stats.FormatDuration(db.deathlessStreakSec or 0)
    )

    local mode = viewMode or "levels"
    local rowCount = 0
    if mode == "zones" then
        local zc = 0
        for _, zRec in pairs(db.zones or {}) do
            if type(zRec) == "table" then
                zc = zc + 1
                sortedZoneScratch[zc] = zRec
            end
        end
        for i = zc + 1, #sortedZoneScratch do sortedZoneScratch[i] = nil end
        table.sort(sortedZoneScratch, function(a, b)
            if (a.distanceYards or 0) ~= (b.distanceYards or 0) then
                return (a.distanceYards or 0) > (b.distanceYards or 0)
            end
            if (a.quests or 0) ~= (b.quests or 0) then
                return (a.quests or 0) > (b.quests or 0)
            end
            return (a.zoneId or 0) < (b.zoneId or 0)
        end)
        for i = 1, zc do
            local z = sortedZoneScratch[i]
            rowCount = rowCount + 1
            viewRowsScratch[rowCount] = {
                top = string.format("|cff66b2ff%s|r  |cff888888(%d %s · %s)|r",
                    z.zoneName or ("Zone " .. tostring(z.zoneId)),
                    z.visits or 1,
                    (z.visits or 1) == 1 and "visit" or "visits",
                    Stats.FormatDuration(z.timeSec or 0)),
                bot = string.format("Distance: %s · Quests: %d · XP: %s · Deaths: %d",
                    Stats.FormatDistance(z.distanceYards or 0),
                    z.quests or 0,
                    ns.FormatNumber(z.xpGained or 0),
                    z.deaths or 0),
            }
        end
    elseif mode == "milestones" then
        local dCount = 0
        for _, dRec in pairs(db.dungeonsVisited or {}) do
            if type(dRec) == "table" then
                dCount = dCount + 1
                dungeonNamesScratch[dCount] = string.format("%s (%dx)", dRec.name or dRec.key, dRec.visits or 1)
            end
        end
        for i = dCount + 1, #dungeonNamesScratch do dungeonNamesScratch[i] = nil end
        table.sort(dungeonNamesScratch)
        local dungeonListTxt = (dCount > 0) and table.concat(dungeonNamesScratch, ", ", 1, dCount) or "None yet"

        local rows = {
            {
                top = "|cff33d9ffCurrent Session Snapshot|r",
                bot = string.format("Time: %s · XP: %s (%s/hr) · Quests: %d · Distance: %s · Net: %s",
                    Stats.FormatDuration(sElapsed),
                    ns.FormatNumber(session.xpGained or 0),
                    ns.FormatNumber(math.floor(sessXpHr + 0.5)),
                    session.questsCompleted or 0,
                    Stats.FormatDistance(sessYds),
                    ns.FormatMoney(math.max(0, session.netCopper or 0))),
            },
            {
                top = "|cffc080ffXP Source Breakdown (Session & Journey)|r",
                bot = string.format("Journey: Quest %s (%d%%) / Combat & Explore %s · Session: Quest %s / Other %s",
                    ns.FormatNumber(db.xpFromQuests or 0),
                    qXpPct,
                    ns.FormatNumber(otherXpTotal),
                    ns.FormatNumber(session.xpFromQuests or 0),
                    ns.FormatNumber(sessOtherXp)),
            },
            {
                top = "|cff44ee77Leveling Pace & Projection to 60|r",
                bot = string.format("Completed Levels: %d · Fastest: %s · Avg/Level: %s · Rem XP to 60: %s",
                    completedCount,
                    fastestLv and string.format("Lv %d (%s)", fastestLv, Stats.FormatDuration(fastestSec)) or "--",
                    avgLevelText,
                    ns.FormatNumber(remXpTo60)),
            },
            {
                top = "|cff66b2ffServer /played Time & Survival Records|r",
                bot = string.format("/played Total: %s · /played This Level: %s · Deaths: %d · Best Deathless Streak: %s",
                    totalPlayedLive and Stats.FormatDuration(totalPlayedLive) or "Not synced (click Sync /played)",
                    levelPlayedLive and Stats.FormatDuration(levelPlayedLive) or "--",
                    db.deaths or 0,
                    Stats.FormatDuration(db.bestDeathlessStreakSec or 0)),
            },
            {
                top = string.format("|cffff944dDungeons Visited (%d) & Transport|r", dCount),
                bot = string.format("%s · Flights: %d (%s) · Hearths: %d",
                    dungeonListTxt,
                    db.flightCount or 0,
                    Stats.FormatDuration(db.flightTimeSec or 0),
                    db.hearthCount or 0),
            },
        }
        for i = 1, #rows do
            rowCount = rowCount + 1
            viewRowsScratch[rowCount] = rows[i]
        end
    else
        local lc = 0
        for lvKey, lRec in pairs(db.levels or {}) do
            if type(lRec) == "table" then
                lc = lc + 1
                sortedLevelScratch[lc] = lRec
                lRec.level = lRec.level or tonumber(lvKey) or 0
            end
        end
        for i = lc + 1, #sortedLevelScratch do sortedLevelScratch[i] = nil end
        table.sort(sortedLevelScratch, function(a, b)
            return (a.level or 0) > (b.level or 0)
        end)
        for i = 1, lc do
            local lr = sortedLevelScratch[i]
            local lvNum = lr.level or 0
            local dur = lr.durationSec or 0
            local lXpHr = (dur >= 1) and (((lr.xpGained or 0) / dur) * 3600.0) or 0
            local statusTag = (lvNum == curLv and not lr.completed) and "|cff33ff99[LIVE]|r "
                or (lr.completed and "|cff66aa66[DONE]|r " or "|cff888888[PARTIAL]|r ")
            local zLabel = lr.endZone or lr.startZone or (p and p.zoneName) or "?"
            rowCount = rowCount + 1
            viewRowsScratch[rowCount] = {
                top = string.format("%sLevel %d  |  Time: %s  |  XP/hr: %s  |  %s",
                    statusTag,
                    lvNum,
                    Stats.FormatDuration(dur),
                    ns.FormatNumber(math.floor(lXpHr + 0.5)),
                    zLabel),
                bot = string.format("XP: %s · Quests: %d · Distance: %s · Deaths: %d",
                    ns.FormatNumber(lr.xpGained or 0),
                    lr.quests or 0,
                    Stats.FormatDistance(lr.distanceYards or 0),
                    lr.deaths or 0),
            }
        end
    end

    for i = rowCount + 1, #viewRowsScratch do
        viewRowsScratch[i] = nil
    end

    return {
        viewMode = mode,
        level = curLv,
        xp = curXp,
        xpMax = curXpMax,
        xpPct = curXpPct,
        sessionElapsedSec = sElapsed,
        trackedElapsedSec = tElapsed,
        currentLevelElapsedSec = curLevelElapsed,
        totalTimePlayedSec = totalPlayedLive,
        levelTimePlayedSec = levelPlayedLive,
        sessionXpGained = session.xpGained or 0,
        sessionXpFromQuests = session.xpFromQuests or 0,
        sessionXpPerHour = sessXpHr,
        totalXpGained = db.xpGainedTotal or 0,
        totalXpFromQuests = db.xpFromQuests or 0,
        otherXpTotal = otherXpTotal,
        questXpSharePct = qXpPct,
        avgXpPerHour = totalXpHr,
        avgTimePerLevelSec = avgLevelSec,
        avgTimePerLevelSource = avgLevelSource,
        completedLevelsCount = completedCount,
        fastestLevel = fastestLv,
        fastestLevelSec = fastestSec,
        etaNextLevelSec = etaNextSec,
        etaLevel60Sec = eta60Sec,
        remainingXpTo60 = remXpTo60,
        sessionDistanceYards = sessYds,
        totalDistanceYards = totalYds,
        totalDistanceKm = totalYds / YARDS_PER_KM,
        totalDistanceMiles = totalYds / YARDS_PER_MILE,
        avgYardsPerMin = avgYdsPerMin,
        questsCompleted = db.questsCompleted or 0,
        sessionQuestsCompleted = session.questsCompleted or 0,
        questsAccepted = db.questsAccepted or 0,
        avgXpPerQuest = avgXpPerQuest,
        levelsGained = db.levelsGained or 0,
        sessionLevelsGained = session.levelsGained or 0,
        zonesVisitedCount = db.zonesVisitedCount or 0,
        dungeonsVisitedCount = db.dungeonsVisitedCount or 0,
        flightCount = db.flightCount or 0,
        flightTimeSec = db.flightTimeSec or 0,
        hearthCount = db.hearthCount or 0,
        goldEarnedCopper = db.goldEarnedCopper or 0,
        goldSpentCopper = db.goldSpentCopper or 0,
        netCopper = db.netCopper or 0,
        sessionNetCopper = session.netCopper or 0,
        deaths = db.deaths or 0,
        deathlessStreakSec = db.deathlessStreakSec or 0,
        bestDeathlessStreakSec = db.bestDeathlessStreakSec or 0,
        paceSummary = paceSummary,
        travelSummary = travelSummary,
        questEconSummary = questEconSummary,
        rows = viewRowsScratch,
    }
end

function Stats.PrintChatSummary()
    local vm = Stats.GetViewModel("levels")
    ns.Printf("Journey Stats (Lv %d): Avg/Lvl %s | XP/hr %s | Distance %s | Quests %d | Deaths %d",
        vm.level,
        (vm.avgTimePerLevelSec > 0) and Stats.FormatDuration(vm.avgTimePerLevelSec) or "--",
        ns.FormatNumber(math.floor((vm.avgXpPerHour or 0) + 0.5)),
        Stats.FormatDistance(vm.totalDistanceYards),
        vm.questsCompleted,
        vm.deaths)
end
