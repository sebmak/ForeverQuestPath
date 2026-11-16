--------------------------------------------------------------------------------
-- Core.lua
-- Rules:
--   1. Central namespace setup, SavedVariables initialization, 5-weight profile
--      presets (including Skinning & Leatherworking), maxLevelOffset (-3..+5,
--      default +2), multi-race/class/faction character helpers, shared
--      IsPlayerDeadOrGhost, IsPlayerInGroup, and GetPlayerInstanceState /
--      IsPlayerInDungeonInstance state helpers, formatting, event
--      bus, throttled GPS location ticker, trailing-edge event coalescing
--      (deferring loot/skill/subzone/group bursts off frame 0 without dropping
--      distinct event categories), PLAYER_DEAD / PLAYER_ALIVE / PLAYER_UNGHOST
--      dead/ghost location gating & resurrection resume, change-gated
--      GROUP_ROSTER_UPDATE / PARTY_MEMBERS_CHANGED / RAID_ROSTER_UPDATE group
--      tracking (supporting solo elite skipping via toggles.skipEliteSolo,
--      dungeon quest deferral via toggles.skipDungeons, automatic in-dungeon
--      quest routing when zoned inside a dungeon/raid instance, and
--      optimal-path unaccepted available quest discovery via State/Scorer),
--      TAXIMAP_OPENED change-gated flight-master recording, /fqp gate toggle,
--      /fqp elite toggle, /fqp skipdungeons (/fqp dungeons skip|allow|toggle),
--      /fqp gps stats telemetry, quest hover info tooltips & click-to-open
--      Quest Log (via UI/QuestInfo.lua), anti-shuffling Scorer/State routing
--      stability (continent/zone cohesion, single-use virtual hearthstone
--      lookahead, and target hysteresis), and slash commands.
--   2. Must tolerate missing optional addons (TomTom, OpenMap, Questie,
--      ForeverLevelingBiS) and missing/partial GeneratedData safely.
--   3. Keep all Lua compatible with WoW Forever 1.60.1 (Lua 5.1 semantics)
--      and headless unit-test harnesses.
--------------------------------------------------------------------------------

local addonName, ns = ...

if type(ns) ~= "table" then
    ns = {}
end
addonName = addonName or "ForeverQuestPath"

ns.name = addonName
ns.version = "1.6.0"
ns.PREFIX = "|cff33d9ffForeverQuestPath|r: "

_G.ForeverQuestPath = ns

local unpackFn = unpack or table.unpack

local ACTION_META = {
    ACCEPT       = { label = "Accept",       hex = "|cff44ee77", rgb = { 0.27, 0.93, 0.47 } },
    COMPLETE     = { label = "Complete",     hex = "|cffffcc33", rgb = { 1.00, 0.80, 0.20 } },
    TURN_IN      = { label = "Turn In",      hex = "|cff33ffcc", rgb = { 0.20, 1.00, 0.80 } },
    TRAVEL       = { label = "Travel",       hex = "|cff66b2ff", rgb = { 0.40, 0.70, 1.00 } },
    DUNGEON_PREP = { label = "Dungeon Prep", hex = "|cffff944d", rgb = { 1.00, 0.58, 0.30 } },
    DUNGEON_RUN  = { label = "Dungeon Run",  hex = "|cffc080ff", rgb = { 0.75, 0.50, 1.00 } },
}

local DEFAULT_DB = {
    minimapAngle = 215,
    showMinimap = true,
    hudLocked = false,
    hudShown = true,
    hudScale = 1.0,
    hudPos = nil,
    windowPos = nil,
    autoWaypoint = true,
    useTomTom = true,
    useNativePin = true,
    openMapOnPin = false,
}

local DEFAULT_CHAR_DB = {
    activeProfile = "balanced",
    customWeights = false,
    weights = nil,
    toggles = {
        preferDungeonUnlocks = true,
        prioritizeBags = true,
        prioritizeRogueGear = true,
        prioritizeClassGear = true,
        autoDetectChar = true,
        autoDetectProfessions = true,
        hasSkinning = false,
        hasLeatherworking = false,
        prioritizeSkinningLW = true,
        autoAdvance = true,
        locationReorder = true,
        dynamicRescore = false,
        consultQuestie = true,
        allowRedQuests = false,
        skipEliteSolo = true,
        skipDungeons = true,
    },
    overrideRace = nil,
    overrideClass = nil,
    overrideFaction = nil,
    maxLevelOffset = 2,
    stepIndex = 1,
    skippedSteps = {},
    deferredSteps = {},
    completedCustomSteps = {},
    history = {},
}

local PRESET_FALLBACK_WEIGHTS = {
    balanced      = { xp = 45, travel = 25, dungeon = 20, reward = 10, skinning = 15 },
    skinning_lw   = { xp = 35, travel = 20, dungeon = 10, reward = 15, skinning = 40 },
    dungeon_focus = { xp = 30, travel = 15, dungeon = 45, reward = 10, skinning = 10 },
    treasure_bags = { xp = 25, travel = 15, dungeon = 10, reward = 50, skinning = 15 },
    fast_xp       = { xp = 55, travel = 35, dungeon = 5,  reward = 5,  skinning = 10 },
}

local PRESET_LABELS = {
    balanced      = "Balanced",
    skinning_lw   = "Skinning & Leatherworking",
    dungeon_focus = "Dungeon Focus",
    treasure_bags = "Gold & Bags",
    fast_xp       = "Fast XP & Low Travel",
    custom        = "Custom",
}

local REFRESH_COALESCE_SEC = 0.20
local callbacks = {}
local pendingRefresh = false
local dirtyEnterWorld = false
local dirtyQuestLog = false
local dirtySkills = false
local dirtyTaxi = false
local dirtyZone = false
local dirtyZoneReason = nil
local dirtyGroup = false
local initialized = false
local locationTicker = nil

--------------------------------------------------------------------------------
-- Logging & Formatting
--------------------------------------------------------------------------------

function ns.Print(msg)
    local text = ns.PREFIX .. tostring(msg)
    if DEFAULT_CHAT_FRAME and DEFAULT_CHAT_FRAME.AddMessage then
        DEFAULT_CHAT_FRAME:AddMessage(text)
    elseif print then
        print(text)
    end
end

function ns.Printf(fmt, ...)
    ns.Print(string.format(fmt, ...))
end

function ns.SafeCall(fn, ...)
    if type(fn) ~= "function" then
        return false
    end
    return pcall(fn, ...)
end

function ns.IsPlayerDeadOrGhost()
    if UnitIsDeadOrGhost and UnitIsDeadOrGhost("player") then
        return true
    end
    if UnitIsDead and UnitIsDead("player") then
        return true
    end
    if UnitIsGhost and UnitIsGhost("player") then
        return true
    end
    return false
end

function ns.IsPlayerInGroup()
    if IsInGroup and IsInGroup() then
        return true
    end
    if IsInRaid and IsInRaid() then
        return true
    end
    if GetNumGroupMembers and (GetNumGroupMembers() or 0) > 1 then
        return true
    end
    if GetNumPartyMembers and (GetNumPartyMembers() or 0) > 0 then
        return true
    end
    if GetNumRaidMembers and (GetNumRaidMembers() or 0) > 0 then
        return true
    end
    return false
end

function ns.GetPlayerInstanceState()
    if not IsInInstance then
        return false, nil, nil, "none"
    end
    local inInst, instType = IsInInstance()
    if not inInst or (instType ~= "party" and instType ~= "raid") then
        return false, nil, nil, instType or "none"
    end
    local instName, instMapId = nil, nil
    if GetInstanceInfo then
        local name, _, _, _, _, _, _, mapId = GetInstanceInfo()
        if type(name) == "string" and name ~= "" then
            instName = name
        end
        instMapId = tonumber(mapId)
        if instMapId and instMapId <= 0 then
            instMapId = nil
        end
    end
    return true, instName, instMapId, instType
end

function ns.IsPlayerInDungeonInstance()
    local inDungeon = ns.GetPlayerInstanceState()
    return inDungeon == true
end

function ns.CopyTable(src)
    if type(src) ~= "table" then
        return src
    end
    local out = {}
    for k, v in pairs(src) do
        if type(v) == "table" then
            out[k] = ns.CopyTable(v)
        else
            out[k] = v
        end
    end
    return out
end

function ns.MergeDefaults(target, defaults)
    if type(target) ~= "table" then
        target = {}
    end
    for k, v in pairs(defaults) do
        if target[k] == nil then
            if type(v) == "table" then
                target[k] = ns.CopyTable(v)
            else
                target[k] = v
            end
        elseif type(v) == "table" and type(target[k]) == "table" then
            ns.MergeDefaults(target[k], v)
        end
    end
    return target
end

function ns.HasBit(mask, index)
    if not mask or mask <= 0 or not index or index < 1 then
        return true
    end
    if bit and bit.band and index <= 31 then
        return bit.band(mask, 2 ^ (index - 1)) ~= 0
    end
    local divisor = 2 ^ (index - 1)
    return math.floor(mask / divisor) % 2 == 1
end

function ns.HasBitMask(mask, bitValue)
    if not mask or mask <= 0 or not bitValue or bitValue <= 0 then
        return true
    end
    if bit and bit.band and bitValue < 2147483648 and mask < 2147483648 then
        return bit.band(mask, bitValue) ~= 0
    end
    return math.floor(mask / bitValue) % 2 == 1
end

function ns.GetQuestClassScore(q, classFile)
    if type(q) ~= "table" then return 0 end
    local cf = classFile and string.upper(tostring(classFile)) or "ROGUE"
    local cs = q.classScores
    if type(cs) == "table" then
        local v = tonumber(cs[cf])
        if v ~= nil then return v end
        for k, val in pairs(cs) do
            if string.upper(tostring(k)) == cf then
                return tonumber(val) or 0
            end
        end
    end
    if cf == "ROGUE" or classFile == nil then
        return tonumber(q.rogueScore) or 0
    end
    return 0
end

function ns.FormatMoney(copper)
    copper = tonumber(copper) or 0
    if copper <= 0 then
        return "0c"
    end
    local g = math.floor(copper / 10000)
    local s = math.floor(copper / 100) % 100
    local c = copper % 100
    local parts = {}
    if g > 0 then
        parts[#parts + 1] = string.format("|cffffd700%dg|r", g)
    end
    if s > 0 or (g > 0 and c > 0) then
        parts[#parts + 1] = string.format("|cffc7c7cf%ds|r", s)
    end
    if c > 0 or #parts == 0 then
        parts[#parts + 1] = string.format("|cffeda55f%dc|r", c)
    end
    return table.concat(parts, " ")
end

function ns.FormatMoneyPlain(copper)
    copper = tonumber(copper) or 0
    if copper <= 0 then
        return "0c"
    end
    local g = math.floor(copper / 10000)
    local s = math.floor(copper / 100) % 100
    local c = copper % 100
    local parts = {}
    if g > 0 then parts[#parts + 1] = g .. "g" end
    if s > 0 then parts[#parts + 1] = s .. "s" end
    if c > 0 or #parts == 0 then parts[#parts + 1] = c .. "c" end
    return table.concat(parts, " ")
end

function ns.FormatNumber(n)
    n = math.floor(tonumber(n) or 0)
    if BreakUpLargeNumbers then
        return BreakUpLargeNumbers(n)
    end
    local s = tostring(n)
    local formatted = s
    while true do
        local nextStr, k = formatted:gsub("^(-?%d+)(%d%d%d)", "%1,%2")
        formatted = nextStr
        if k == 0 then break end
    end
    return formatted
end

function ns.FormatCoords(x, y)
    if type(x) ~= "number" or type(y) ~= "number" then
        return "?.?, ?.?"
    end
    return string.format("%.1f, %.1f", x, y)
end

function ns.GetActionMeta(action)
    return ACTION_META[action or "ACCEPT"] or { label = tostring(action or "Step"), hex = "|cffffffff", rgb = { 1, 1, 1 } }
end

function ns.GetPresetLabel(key)
    local data = ns.GetData()
    local prof = data.profiles and data.profiles[key]
    if prof and prof.label then
        return prof.label
    end
    return PRESET_LABELS[key] or tostring(key)
end

function ns.GetPresetWeights(key)
    local data = ns.GetData()
    local prof = data.profiles and data.profiles[key]
    local fb = PRESET_FALLBACK_WEIGHTS[key] or PRESET_FALLBACK_WEIGHTS.balanced
    if prof and type(prof.weights) == "table" then
        return {
            xp = tonumber(prof.weights.xp) or fb.xp,
            travel = tonumber(prof.weights.travel) or fb.travel,
            dungeon = tonumber(prof.weights.dungeon) or fb.dungeon,
            reward = tonumber(prof.weights.reward) or fb.reward,
            skinning = tonumber(prof.weights.skinning) or fb.skinning or 0,
        }
    end
    return ns.CopyTable(fb)
end

function ns.GetActiveWeights()
    local cdb = ns.charDB
    if cdb and cdb.customWeights and type(cdb.weights) == "table" then
        return cdb.weights
    end
    local profileKey = (cdb and cdb.activeProfile) or "balanced"
    if profileKey == "custom" and cdb and type(cdb.weights) == "table" then
        return cdb.weights
    end
    return ns.GetPresetWeights(profileKey)
end

function ns.GetData()
    local d = ns.GeneratedData
    if type(d) == "table" and d._fqpNormalized then
        return d
    end
    if type(d) ~= "table" then
        d = {
            meta = {},
            xpCurve = {},
            zones = {},
            dungeons = {},
            quests = {},
            profiles = {},
            starterRoutes = {},
            hordeRoutes = {},
            indexes = {},
        }
        ns.GeneratedData = d
    end
    d.meta = d.meta or {}
    d.xpCurve = d.xpCurve or {}
    d.zones = d.zones or {}
    d.dungeons = d.dungeons or {}
    d.quests = d.quests or {}
    d.profiles = d.profiles or {}
    d.starterRoutes = d.starterRoutes or {}
    d.hordeRoutes = d.hordeRoutes or {}
    d.indexes = d.indexes or {}
    d._fqpNormalized = true
    return d
end

--------------------------------------------------------------------------------
-- Internal Event Bus
--------------------------------------------------------------------------------

function ns.RegisterCallback(eventName, fn)
    callbacks[eventName] = callbacks[eventName] or {}
    table.insert(callbacks[eventName], fn)
end

function ns.FireCallback(eventName, ...)
    local list = callbacks[eventName]
    if not list then return end
    for i = 1, #list do
        local ok, err = pcall(list[i], ...)
        if not ok and geterrorhandler then
            geterrorhandler()(err)
        end
    end
end

--------------------------------------------------------------------------------
-- Initialization & WoW Event Dispatch
--------------------------------------------------------------------------------

function ns.InitSavedVars()
    ForeverQuestPathDB = ns.MergeDefaults(ForeverQuestPathDB, DEFAULT_DB)
    ForeverQuestPathCharDB = ns.MergeDefaults(ForeverQuestPathCharDB, DEFAULT_CHAR_DB)
    ns.db = ForeverQuestPathDB
    ns.charDB = ForeverQuestPathCharDB
    if not ns.charDB.weights then
        ns.charDB.weights = ns.GetPresetWeights(ns.charDB.activeProfile)
    elseif ns.charDB.weights.skinning == nil then
        local pw = ns.GetPresetWeights(ns.charDB.activeProfile)
        ns.charDB.weights.skinning = pw.skinning or 0
    end
    if ns.charDB.maxLevelOffset == nil then
        local data = ns.GetData and ns.GetData() or nil
        local defOff = data and data.meta and tonumber(data.meta.defaultMaxLevelOffset)
        ns.charDB.maxLevelOffset = defOff or 2
    end
    if type(ns.charDB.deferredSteps) ~= "table" then
        ns.charDB.deferredSteps = {}
    end
end

function ns.Bootstrap()
    if initialized then return end
    initialized = true
    ns.InitSavedVars()
    if ns.State and ns.State.Initialize then
        ns.State.Initialize()
    end
    if ns.Scorer and ns.Scorer.Initialize then
        ns.Scorer.Initialize()
    end
    if ns.Waypoint and ns.Waypoint.Initialize then
        ns.Waypoint.Initialize()
    end
    if ns.HUD and ns.HUD.Initialize then
        ns.HUD.Initialize()
    end
    if ns.Window and ns.Window.Initialize then
        ns.Window.Initialize()
    end
    if ns.Minimap and ns.Minimap.Initialize then
        ns.Minimap.Initialize()
    end
    if (not locationTicker) and C_Timer and C_Timer.NewTicker then
        locationTicker = C_Timer.NewTicker(1.5, function()
            if initialized and ns.State and ns.State.CheckLivePosition then
                ns.State.CheckLivePosition(false)
            end
        end)
    end
end

local function MarkDirtyRefreshReason(reason)
    if reason == "PLAYER_ENTERING_WORLD" then
        dirtyEnterWorld = true
    elseif reason == "QUEST_LOG_UPDATE" then
        dirtyQuestLog = true
    elseif reason == "SKILL_LINES_CHANGED" or reason == "CHAT_MSG_SKILL" then
        dirtySkills = true
    elseif reason == "TAXIMAP_OPENED" then
        dirtyTaxi = true
    elseif reason == "ZONE_CHANGED" or reason == "ZONE_CHANGED_INDOORS" then
        dirtyZone = true
        dirtyZoneReason = reason
    elseif reason == "GROUP_ROSTER_UPDATE" or reason == "PARTY_MEMBERS_CHANGED" or reason == "RAID_ROSTER_UPDATE" then
        dirtyGroup = true
    end
end

local function FlushPendingRefresh()
    local runEnter = dirtyEnterWorld
    local runQuest = dirtyQuestLog
    local runSkills = dirtySkills
    local runTaxi = dirtyTaxi
    local runZone = dirtyZone
    local zoneReason = dirtyZoneReason
    local runGroup = dirtyGroup
    pendingRefresh = false
    dirtyEnterWorld = false
    dirtyQuestLog = false
    dirtySkills = false
    dirtyTaxi = false
    dirtyZone = false
    dirtyZoneReason = nil
    dirtyGroup = false

    if not (ns.State and ns.State.Refresh) then
        return
    end
    local getStats = ns.State.GetIdlePerfStats
    local beforeExec = getStats and getStats().refreshExecuted or 0
    if runEnter then
        ns.State.Refresh("PLAYER_ENTERING_WORLD")
        local nowExec = getStats and getStats().refreshExecuted or 0
        if nowExec > beforeExec then
            runQuest = false
            runSkills = false
            runZone = false
            runGroup = false
        end
        beforeExec = nowExec
    end
    if runQuest then
        ns.State.Refresh("QUEST_LOG_UPDATE")
        local nowExec = getStats and getStats().refreshExecuted or 0
        if nowExec > beforeExec then
            runZone = false
        end
        beforeExec = nowExec
    end
    if runSkills then
        ns.State.Refresh("SKILL_LINES_CHANGED")
        local nowExec = getStats and getStats().refreshExecuted or 0
        if nowExec > beforeExec then
            runZone = false
        end
    end
    if runGroup then
        ns.State.Refresh("GROUP_ROSTER_UPDATE")
    end
    local isDeadOrGhost = ns.IsPlayerDeadOrGhost()
    local isOnTaxi = (UnitOnTaxi and UnitOnTaxi("player")) and true or false
    if runTaxi and (not isDeadOrGhost) and (not isOnTaxi) then
        ns.State.Refresh("TAXIMAP_OPENED")
    end
    if runZone and zoneReason and (not isDeadOrGhost) and (not isOnTaxi) then
        ns.State.Refresh(zoneReason)
    end
end

local function ScheduleRefresh(reason, immediate)
    if immediate then
        if ns.State and ns.State.Refresh then
            ns.State.Refresh(reason)
        end
        if not pendingRefresh then
            pendingRefresh = true
            if C_Timer and C_Timer.After then
                C_Timer.After(REFRESH_COALESCE_SEC, FlushPendingRefresh)
            else
                FlushPendingRefresh()
            end
        end
        return
    end
    MarkDirtyRefreshReason(reason)
    if pendingRefresh then
        return
    end
    pendingRefresh = true
    if C_Timer and C_Timer.After then
        C_Timer.After(REFRESH_COALESCE_SEC, FlushPendingRefresh)
    else
        FlushPendingRefresh()
    end
end

function ns.HandleSlashCommand(rawMsg)
    ns.Bootstrap()
    local msg = (rawMsg or ""):match("^%s*(.-)%s*$")
    local cmd, rest = msg:match("^(%S+)%s*(.-)$")
    cmd = cmd and cmd:lower() or ""
    rest = rest and rest:lower() or ""

    if cmd == "" or cmd == "show" or cmd == "toggle" or cmd == "config" then
        if ns.Window and ns.Window.Toggle then
            ns.Window.Toggle()
        end
    elseif cmd == "hud" then
        if ns.HUD and ns.HUD.Toggle then
            ns.HUD.Toggle()
        end
    elseif cmd == "next" or cmd == "sync" then
        if ns.State and ns.State.Refresh then
            ns.State.Refresh("MANUAL_SYNC")
        end
        if ns.Waypoint and ns.Waypoint.SyncToCurrentStep then
            ns.Waypoint.SyncToCurrentStep(true)
        end
    elseif cmd == "skip" then
        if ns.State and ns.State.SkipCurrentStep then
            ns.State.SkipCurrentStep()
        end
    elseif cmd == "back" then
        if ns.State and ns.State.StepBack then
            ns.State.StepBack()
        end
    elseif cmd == "reset" then
        if ns.State and ns.State.ResetProgress then
            ns.State.ResetProgress()
        end
        ns.Print("Route state reset and resynced to character.")
    elseif cmd == "pin" or cmd == "wp" or cmd == "waypoint" then
        if ns.Waypoint and ns.Waypoint.SyncToCurrentStep then
            ns.Waypoint.SyncToCurrentStep(true)
        end
    elseif cmd == "reorder" or cmd == "gps" then
        if ns.charDB and ns.charDB.toggles then
            local t = ns.charDB.toggles
            if rest == "on" or rest == "enable" then
                t.locationReorder = true
                if ns.State and ns.State.CheckLivePosition then
                    ns.State.CheckLivePosition(true)
                elseif ns.State and ns.State.Refresh then
                    ns.State.Refresh("TOGGLE_locationReorder")
                end
                ns.Print("Auto-Reorder (GPS) enabled and resynced.")
            elseif rest == "off" or rest == "disable" then
                t.locationReorder = false
                if ns.State and ns.State.Refresh then
                    ns.State.Refresh("TOGGLE_locationReorder")
                end
                ns.Print("Auto-Reorder (GPS) disabled.")
            elseif rest == "stats" or rest == "perf" then
                local st = ns.State and ns.State.GetIdlePerfStats and ns.State.GetIdlePerfStats() or nil
                local nv = ns.Nav and ns.Nav.GetStats and ns.Nav.GetStats() or nil
                if st then
                    ns.Printf("GPS gating (>=%.0fyd / >=%.0fyd / %.1fs): %d checks, %d triggers (%d dead skipped); Refreshes: %d exec / %d skipped idle.",
                        st.moveYardsThreshold or 95, st.largeMoveYards or 160, st.cooldownSec or 4.5,
                        st.gpsTickChecks or 0, st.gpsTickTriggers or 0, st.deadLocationSkipped or 0,
                        st.refreshExecuted or 0, st.refreshSkipped or 0)
                end
                if nv then
                    ns.Printf("Nav Dijkstra: %d QueryPath (%d Bidir solves), %d SingleFld + %d Nearest (%d Flood solves).",
                        nv.queryCalls or 0, nv.bidirSolves or 0, nv.singleFieldCalls or 0, nv.nearestCalls or 0, nv.floodSolves or 0)
                end
            elseif rest == "toggle" then
                t.locationReorder = (t.locationReorder == false)
                if t.locationReorder and ns.State and ns.State.CheckLivePosition then
                    ns.State.CheckLivePosition(true)
                elseif ns.State and ns.State.Refresh then
                    ns.State.Refresh("TOGGLE_locationReorder")
                end
                ns.Printf("Auto-Reorder (GPS) %s.", (t.locationReorder ~= false) and "enabled" or "disabled")
            else
                if t.locationReorder == false then
                    t.locationReorder = true
                end
                if ns.State and ns.State.CheckLivePosition then
                    ns.State.CheckLivePosition(true)
                elseif ns.State and ns.State.Refresh then
                    ns.State.Refresh("GPS_SYNC")
                end
                ns.Print("Auto-Reorder (GPS) synced to current location (use '/fqp gps off' to disable).")
            end
        end
    elseif cmd == "profile" or cmd == "mode" then
        local mapAlias = {
            balanced = "balanced",
            skinning = "skinning_lw",
            skin = "skinning_lw",
            lw = "skinning_lw",
            leatherworking = "skinning_lw",
            skinning_lw = "skinning_lw",
            dungeon = "dungeon_focus",
            dungeon_focus = "dungeon_focus",
            dungeons = "dungeon_focus",
            bags = "treasure_bags",
            gold = "treasure_bags",
            treasure = "treasure_bags",
            treasure_bags = "treasure_bags",
            fast = "fast_xp",
            fast_xp = "fast_xp",
            xp = "fast_xp",
        }
        local target = mapAlias[rest]
        if target and ns.State and ns.State.SetProfile then
            ns.State.SetProfile(target)
            ns.Printf("Strategy switched to %s.", ns.GetPresetLabel(target))
        else
            ns.Print("Profiles: balanced, skinning_lw, dungeon_focus, treasure_bags, fast_xp")
        end
    elseif cmd == "char" or cmd == "race" or cmd == "class" then
        if not (ns.State and ns.State.SetCharacterOverride) then
            return
        end
        if rest == "" or rest == "auto" or rest == "reset" then
            ns.State.ClearCharacterOverride()
            local p = ns.State.GetPlayerInfo()
            ns.Printf("Character set to Auto-Detect (%s %s %s).", p.faction, p.raceName, p.className)
            return
        end
        if cmd == "race" then
            local ok, errMsg = ns.State.SetCharacterOverride(rest, nil)
            if ok then
                local p = ns.State.GetPlayerInfo()
                ns.Printf("Character override: %s %s %s.", p.faction, p.raceName, p.className)
            else
                ns.Print(errMsg or ("Unknown race: " .. rest))
            end
            return
        elseif cmd == "class" then
            local ok, errMsg = ns.State.SetCharacterOverride(nil, rest)
            if ok then
                local p = ns.State.GetPlayerInfo()
                ns.Printf("Character override: %s %s %s.", p.faction, p.raceName, p.className)
            else
                ns.Print(errMsg or ("Unknown class: " .. rest))
            end
            return
        end
        local tokens = {}
        for tok in rest:gmatch("%S+") do
            tokens[#tokens + 1] = tok
        end
        local raceTok, classTok = nil, nil
        -- Try whole string or multi-word race + class (e.g. "night elf hunter", "high elf paladin", "orc warrior")
        if #tokens >= 2 then
            local lastTok = tokens[#tokens]
            if ns.State.ResolveClass(lastTok) then
                classTok = lastTok
                raceTok = table.concat(tokens, " ", 1, #tokens - 1)
            elseif ns.State.ResolveClass(tokens[1]) then
                classTok = tokens[1]
                raceTok = table.concat(tokens, " ", 2, #tokens)
            else
                raceTok = rest
            end
        else
            if ns.State.ResolveClass(tokens[1]) then
                classTok = tokens[1]
            else
                raceTok = tokens[1]
            end
        end
        local ok, errMsg = ns.State.SetCharacterOverride(raceTok, classTok)
        if ok then
            local p = ns.State.GetPlayerInfo()
            ns.Printf("Character override: %s %s %s.", p.faction, p.raceName, p.className)
        else
            ns.Print(errMsg or ("Invalid race/class: " .. rest .. " (use '/fqp char auto' to reset)"))
        end
    elseif cmd == "maxlevel" or cmd == "maxlv" then
        if not (ns.State and ns.State.SetMaxLevelOffset and ns.State.GetMaxLevelOffset) then
            return
        end
        if rest == "" then
            local p = ns.State.GetPlayerInfo and ns.State.GetPlayerInfo() or {}
            local off = ns.State.GetEffectiveMaxLevelOffset and ns.State.GetEffectiveMaxLevelOffset() or ns.State.GetMaxLevelOffset()
            local pLv = tonumber(p.level) or 24
            local sign = (off >= 0) and "+" or ""
            ns.Printf("Max Quest Level Offset: %s%d (<= Lv %d; range -3 to +5). Usage: /fqp maxlevel <-3..+5|reset>", sign, off, pLv + off)
            return
        end
        local targetOff
        if rest == "reset" or rest == "default" then
            local data = ns.GetData and ns.GetData() or nil
            targetOff = (data and data.meta and tonumber(data.meta.defaultMaxLevelOffset)) or 2
        else
            local cleaned = rest:gsub("^%s*%+", ""):match("^%s*(.-)%s*$")
            targetOff = tonumber(cleaned)
        end
        if targetOff == nil then
            ns.Print("Usage: /fqp maxlevel <-3..+5|reset> (e.g. /fqp maxlevel +2, /fqp maxlevel -1)")
            return
        end
        local applied = ns.State.SetMaxLevelOffset(targetOff)
        local p = ns.State.GetPlayerInfo and ns.State.GetPlayerInfo() or {}
        local pLv = tonumber(p.level) or 24
        local sign = (applied >= 0) and "+" or ""
        ns.Printf("Max Quest Level Offset set to %s%d (<= Lv %d).", sign, applied, pLv + applied)
    elseif cmd == "dungeons" then
        if (rest == "skip" or rest == "allow" or rest == "show" or rest == "include" or rest == "toggle" or rest == "on" or rest == "off" or rest == "hide")
            and ns.charDB and ns.charDB.toggles then
            local toggles = ns.charDB.toggles
            local newVal
            if rest == "skip" or rest == "on" or rest == "hide" then
                newVal = true
            elseif rest == "allow" or rest == "show" or rest == "include" or rest == "off" then
                newVal = false
            else
                newVal = not (toggles.skipDungeons ~= false)
            end
            toggles.skipDungeons = newVal
            if ns.State and ns.State.Refresh then
                ns.State.Refresh("TOGGLE_skipDungeons")
            end
            ns.Print("Skip Dungeons: " .. (newVal and "ON (dungeon quests deferred)" or "OFF (dungeon quests active)"))
        elseif ns.Window and ns.Window.ShowTab then
            ns.Window.ShowTab("dungeons")
        end
    elseif cmd == "skipdungeons" or cmd == "skipdungeon" or cmd == "nodungeons" then
        if ns.charDB and ns.charDB.toggles then
            local toggles = ns.charDB.toggles
            local newVal
            if rest == "on" or rest == "skip" or rest == "1" or rest == "true" then
                newVal = true
            elseif rest == "off" or rest == "allow" or rest == "show" or rest == "0" or rest == "false" then
                newVal = false
            else
                newVal = not (toggles.skipDungeons ~= false)
            end
            toggles.skipDungeons = newVal
            if ns.State and ns.State.Refresh then
                ns.State.Refresh("TOGGLE_skipDungeons")
            end
            ns.Print("Skip Dungeons: " .. (newVal and "ON (dungeon quests deferred)" or "OFF (dungeon quests active)"))
        end
    elseif cmd == "rewards" then
        if ns.Window and ns.Window.ShowTab then
            ns.Window.ShowTab("rewards")
        end
    elseif cmd == "gate" or cmd == "gates" or cmd == "nav" then
        if ns.db then
            if rest == "on" or rest == "enable" then
                ns.db.navGateWaypoints = true
            elseif rest == "off" or rest == "disable" then
                ns.db.navGateWaypoints = false
            else
                ns.db.navGateWaypoints = (ns.db.navGateWaypoints == false)
            end
            if ns.Waypoint and ns.Waypoint.SyncToCurrentStep then
                ns.Waypoint.SyncToCurrentStep(false)
            end
            ns.Printf("Gate/transport intermediate waypoints %s.", (ns.db.navGateWaypoints ~= false) and "enabled" or "disabled")
        end
    elseif cmd == "elite" or cmd == "elites" or cmd == "group" then
        if ns.charDB and ns.charDB.toggles then
            local toggles = ns.charDB.toggles
            local newVal
            if rest == "on" or rest == "skip" or rest == "solo" or rest == "1" or rest == "true" then
                newVal = true
            elseif rest == "off" or rest == "allow" or rest == "show" or rest == "0" or rest == "false" then
                newVal = false
            else
                newVal = not (toggles.skipEliteSolo ~= false)
            end
            toggles.skipEliteSolo = newVal
            if ns.State and ns.State.Refresh then
                ns.State.Refresh("TOGGLE_skipEliteSolo")
            end
            local inGroup = ns.IsPlayerInGroup and ns.IsPlayerInGroup() or false
            ns.Print("Skip Elite (Solo): " .. (newVal and "ON" or "OFF")
                .. (inGroup and " (currently IN GROUP - elite quests included)" or " (currently SOLO)"))
        end
    else
        ns.Print("Commands: /fqp (window), /fqp hud, /fqp pin, /fqp elite [on|off|toggle], /fqp skipdungeons [on|off|toggle], /fqp gate [on|off], /fqp gps [on|off|toggle|stats], /fqp skip, /fqp back, /fqp reset, /fqp dungeons [skip|allow|toggle], /fqp rewards, /fqp maxlevel <-3..+5>, /fqp char <auto|race class>, /fqp profile <balanced|skinning_lw|dungeon_focus|treasure_bags|fast_xp>")
    end
end

local function RegisterSlashCommands()
    _G.SLASH_FOREVERQUESTPATH1 = "/fqp"
    _G.SLASH_FOREVERQUESTPATH2 = "/foreverquestpath"
    if SlashCmdList then
        SlashCmdList.FOREVERQUESTPATH = ns.HandleSlashCommand
    end
end

RegisterSlashCommands()

if CreateFrame then
    local eventFrame = CreateFrame("Frame", "ForeverQuestPathEventFrame")
    ns.eventFrame = eventFrame
    local events = {
        "ADDON_LOADED",
        "PLAYER_LOGIN",
        "PLAYER_ENTERING_WORLD",
        "QUEST_ACCEPTED",
        "QUEST_TURNED_IN",
        "QUEST_REMOVED",
        "QUEST_LOG_UPDATE",
        "PLAYER_LEVEL_UP",
        "ZONE_CHANGED_NEW_AREA",
        "ZONE_CHANGED",
        "ZONE_CHANGED_INDOORS",
        "SKILL_LINES_CHANGED",
        "CHAT_MSG_SKILL",
        "TAXIMAP_OPENED",
        "PLAYER_DEAD",
        "PLAYER_ALIVE",
        "PLAYER_UNGHOST",
        "GROUP_ROSTER_UPDATE",
        "PARTY_MEMBERS_CHANGED",
        "RAID_ROSTER_UPDATE",
    }
    for _, ev in ipairs(events) do
        pcall(eventFrame.RegisterEvent, eventFrame, ev)
    end

    eventFrame:SetScript("OnEvent", function(_, event, arg1, arg2)
        if event == "ADDON_LOADED" then
            if arg1 == addonName then
                ns.InitSavedVars()
            end
        elseif event == "PLAYER_LOGIN" then
            ns.Bootstrap()
            if ns.State and ns.State.Refresh then
                ns.State.Refresh("PLAYER_LOGIN")
            end
            local data = ns.GetData()
            local pCount = tonumber(data.meta and data.meta.questCount)
            if not pCount then
                pCount = 0
                for _ in pairs(data.quests) do pCount = pCount + 1 end
            end
            ns.Printf("Loaded (%d quests indexed). Type /fqp for route & dungeon tracker.", pCount)
        elseif event == "PLAYER_ENTERING_WORLD" then
            ns.Bootstrap()
            ScheduleRefresh(event, true)
        elseif event == "QUEST_TURNED_IN" or event == "QUEST_ACCEPTED" or event == "QUEST_REMOVED"
            or event == "PLAYER_LEVEL_UP" then
            if initialized then
                if ns.State and ns.State.Refresh then
                    ns.State.Refresh(event, arg1, arg2)
                end
            end
        elseif event == "ZONE_CHANGED_NEW_AREA" then
            if initialized and (not ns.IsPlayerDeadOrGhost()) and not (UnitOnTaxi and UnitOnTaxi("player")) then
                if ns.State and ns.State.Refresh then
                    ns.State.Refresh(event, arg1, arg2)
                end
            end
        elseif event == "ZONE_CHANGED" or event == "ZONE_CHANGED_INDOORS" then
            if initialized and (not ns.IsPlayerDeadOrGhost()) and not (UnitOnTaxi and UnitOnTaxi("player")) then
                ScheduleRefresh(event, false)
            end
        elseif event == "QUEST_LOG_UPDATE" or event == "SKILL_LINES_CHANGED" or event == "CHAT_MSG_SKILL" then
            if initialized then
                ScheduleRefresh(event, false)
            end
        elseif event == "GROUP_ROSTER_UPDATE" or event == "PARTY_MEMBERS_CHANGED" or event == "RAID_ROSTER_UPDATE" then
            if initialized then
                if not (ns.State and ns.State.HasGroupStatusChanged) or ns.State.HasGroupStatusChanged() then
                    ScheduleRefresh(event, false)
                end
            end
        elseif event == "TAXIMAP_OPENED" then
            if initialized and (not ns.IsPlayerDeadOrGhost()) and not (UnitOnTaxi and UnitOnTaxi("player"))
                and ns.Nav and ns.Nav.RecordTaxiNodes then
                local _, changed = ns.Nav.RecordTaxiNodes()
                if changed then
                    ScheduleRefresh(event, false)
                end
            end
        elseif event == "PLAYER_DEAD" then
            dirtyZone = false
            dirtyZoneReason = nil
            dirtyTaxi = false
            if initialized and ns.State and ns.State.OnPlayerDead then
                ns.State.OnPlayerDead()
            end
        elseif event == "PLAYER_ALIVE" or event == "PLAYER_UNGHOST" then
            if initialized and (not ns.IsPlayerDeadOrGhost()) and not (UnitOnTaxi and UnitOnTaxi("player")) then
                if ns.State and ns.State.OnPlayerResurrected then
                    ns.State.OnPlayerResurrected()
                end
            end
        end
    end)
end
