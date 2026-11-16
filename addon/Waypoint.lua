--------------------------------------------------------------------------------
-- Waypoint.lua
-- Rules:
--   1. Multi-provider waypoint integration for ForeverQuestPath:
--      TomTom, OpenMap (via native UserWaypoint + OpenMapAPI), and Blizzard
--      C_Map.SetUserWaypoint + C_SuperTrack.
--   2. Resolves Wowhead/Forever AreaIDs & dungeon zoneIds to client UiMapIDs
--      using GeneratedData, built-in Forever mappings, C_Map tree, and Questie.
--   3. Never taints WorldMapFrame during combat lockdown.
--   4. When ns.Nav resolves a multi-hop gate/tunnel/pass/flight/boat/tram or
--      terrain detour (and navGateWaypoints ~= false), pins the next reachable
--      hop/gate automatically and advances to the final target on movement.
--   5. While ns.IsPlayerDeadOrGhost() is true, skips automatic autoWaypoint
--      updates and graveyard Nav hop resolution (while manual /fqp pin pins
--      the target directly without computing graveyard hops).
--   6. While inside a dungeon instance on an inside-dungeon step target
--      (isInsideDungeonTarget == true), automatic autoWaypoint clears stale
--      outdoor arrows rather than plotting an outdoor entrance coordinate.
--------------------------------------------------------------------------------

local _, ns = ...

local Waypoint = {}
ns.Waypoint = Waypoint

local mapByName = nil
local lastTomTomUid = nil
local lastTargetSignature = nil

local function BuildClientMapIndex()
    mapByName = {}
    if not (C_Map and C_Map.GetMapInfo and C_Map.GetMapChildrenInfo) then
        return
    end
    local root = (C_Map.GetFallbackWorldMapID and C_Map.GetFallbackWorldMapID())
        or (C_Map.GetBestMapForUnit and C_Map.GetBestMapForUnit("player"))
    if not root then
        return
    end
    local info = C_Map.GetMapInfo(root)
    while info and info.parentMapID and info.parentMapID > 0 do
        root = info.parentMapID
        info = C_Map.GetMapInfo(root)
    end

    local canPin = C_Map.CanSetUserWaypointOnMap
    local children = C_Map.GetMapChildrenInfo(root, nil, true) or {}
    for _, m in ipairs(children) do
        local key = m.name and string.lower(m.name)
        if key and key ~= "" then
            local prev = mapByName[key]
            if not prev or (canPin and not canPin(prev) and canPin(m.mapID)) then
                mapByName[key] = m.mapID
            end
        end
    end
end

function Waypoint.ResolveUiMapId(zoneId, zoneName)
    local data = ns.GetData()
    if zoneId then
        local z = data.zones and data.zones[zoneId]
        if z and z.uiMapId then
            return z.uiMapId
        end
        local areaTable = ns.State and ns.State.GetAreaToUiMapTable and ns.State.GetAreaToUiMapTable()
        if areaTable and areaTable[zoneId] then
            return areaTable[zoneId]
        end
        -- Check if zoneId is a dungeon instance zoneId -> use its entranceZoneId
        for _, d in pairs(data.dungeons or {}) do
            if d.zoneId == zoneId and d.entranceZoneId then
                local mapped = Waypoint.ResolveUiMapId(d.entranceZoneId, d.entranceZoneName)
                if mapped then return mapped end
            end
        end
        -- Try Questie's ZoneDB if loaded
        if QuestieLoader and QuestieLoader.ImportModule then
            local ok, zoneDB = pcall(QuestieLoader.ImportModule, QuestieLoader, "ZoneDB")
            if ok and zoneDB and zoneDB.GetUiMapIdByAreaId then
                local okMap, uiMapId = pcall(zoneDB.GetUiMapIdByAreaId, zoneDB, zoneId)
                if okMap and uiMapId then
                    return uiMapId
                end
            end
        end
    end

    if not mapByName then
        BuildClientMapIndex()
    end

    local candidates = {}
    if zoneId and zoneId > 0 and C_Map and C_Map.GetAreaInfo then
        local areaName = C_Map.GetAreaInfo(zoneId)
        if areaName and areaName ~= "" then
            candidates[#candidates + 1] = areaName
        end
    end
    if zoneId and data.zones and data.zones[zoneId] and data.zones[zoneId].name then
        candidates[#candidates + 1] = data.zones[zoneId].name
    end
    if zoneName and zoneName ~= "" then
        candidates[#candidates + 1] = zoneName
    end

    for _, name in ipairs(candidates) do
        local found = mapByName and mapByName[string.lower(name)]
        if found then
            return found
        end
    end
    return nil
end

function Waypoint.Clear()
    if lastTomTomUid and TomTom and TomTom.RemoveWaypoint then
        pcall(TomTom.RemoveWaypoint, TomTom, lastTomTomUid)
    end
    lastTomTomUid = nil
    lastTargetSignature = nil
    if C_Map and C_Map.ClearUserWaypoint then
        pcall(C_Map.ClearUserWaypoint)
    end
end

function Waypoint.SetTarget(target, opts)
    opts = opts or {}
    if type(target) ~= "table" or type(target.x) ~= "number" or type(target.y) ~= "number" then
        return { ok = false, reason = "missing_coords" }
    end

    local db = ns.db or {}
    local uiMapId = target.uiMapId or Waypoint.ResolveUiMapId(target.zoneId, target.zoneName)
    if not uiMapId then
        return { ok = false, reason = "unknown_map", zoneId = target.zoneId, zoneName = target.zoneName }
    end

    local nx = math.max(0.001, math.min(0.999, target.x / 100.0))
    local ny = math.max(0.001, math.min(0.999, target.y / 100.0))
    local label = target.title or target.npcName or "ForeverQuestPath"

    local sig = string.format("%s:%.2f:%.2f:%s", tostring(uiMapId), target.x, target.y, label)
    if opts.skipIfSame and sig == lastTargetSignature then
        return { ok = true, uiMapId = uiMapId, unchanged = true }
    end
    lastTargetSignature = sig

    local providers = {}

    -- 1. Blizzard Native User Waypoint + SuperTracking (also consumed automatically by OpenMap)
    if db.useNativePin ~= false and C_Map and C_Map.SetUserWaypoint and UiMapPoint and UiMapPoint.CreateFromCoordinates then
        local canPin = (not C_Map.CanSetUserWaypointOnMap) or C_Map.CanSetUserWaypointOnMap(uiMapId)
        if canPin then
            local okPin = pcall(C_Map.SetUserWaypoint, UiMapPoint.CreateFromCoordinates(uiMapId, nx, ny))
            if okPin then
                providers.native = true
                if C_SuperTrack and C_SuperTrack.SetSuperTrackedUserWaypoint then
                    pcall(C_SuperTrack.SetSuperTrackedUserWaypoint, true)
                    providers.superTrack = true
                end
            end
        end
    end

    -- 2. TomTom Crazy Arrow / Pin
    if db.useTomTom ~= false and TomTom and TomTom.AddWaypoint then
        if lastTomTomUid and TomTom.RemoveWaypoint then
            pcall(TomTom.RemoveWaypoint, TomTom, lastTomTomUid)
            lastTomTomUid = nil
        end
        local okTT, uid = pcall(TomTom.AddWaypoint, TomTom, uiMapId, nx, ny, {
            title = label,
            from = "ForeverQuestPath",
            persistent = false,
            crazy = true,
            silent = true,
        })
        if okTT and uid then
            lastTomTomUid = uid
            providers.tomtom = true
        end
    end

    -- 3. OpenMap / World Map focus (when explicitly requested or configured)
    local shouldOpen = opts.openMap or db.openMapOnPin
    if shouldOpen and not (InCombatLockdown and InCombatLockdown()) then
        if C_Map and C_Map.OpenWorldMap then
            pcall(C_Map.OpenWorldMap, uiMapId)
            providers.worldMap = true
        elseif OpenWorldMap then
            pcall(OpenWorldMap, uiMapId)
            providers.worldMap = true
        end
        if OpenMapAPI and OpenMapAPI.IsStitched and OpenMapAPI.IsStitched()
            and OpenMapAPI.WorldFromMap and OpenMapAPI.ShowWorld then
            local u, v = OpenMapAPI.WorldFromMap(uiMapId, nx, ny)
            if u and v then
                pcall(OpenMapAPI.ShowWorld, u, v)
                providers.openMap = true
            end
        end
    end

    if opts.announce then
        local zName = target.zoneName or ("Map " .. tostring(uiMapId))
        ns.Printf("Waypoint set: %s — %s (%.1f, %.1f)", label, zName, target.x, target.y)
    end

    return {
        ok = (providers.native or providers.tomtom) and true or false,
        uiMapId = uiMapId,
        x = nx,
        y = ny,
        providers = providers,
    }
end

function Waypoint.SetForStep(step, opts)
    if not step then
        return { ok = false, reason = "no_step" }
    end
    if type(opts) ~= "table" then
        opts = (opts == true) and { announce = true } or nil
    end
    if opts and opts.skipIfSame == true and opts.allowWhileDead ~= true
        and ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost() then
        return { ok = false, reason = "player_dead" }
    end
    if step.isInsideDungeonTarget == true and ns.IsPlayerInDungeonInstance and ns.IsPlayerInDungeonInstance()
        and not (opts and (opts.announce or opts.openMap)) then
        Waypoint.Clear()
        return { ok = false, reason = "inside_dungeon" }
    end
    local data = ns.GetData()
    local zoneId = step.zoneId
    local x, y = step.x, step.y
    local npcName = step.npcName
    local zoneName = step.zoneName

    if (not x or not y) and step.dungeonId and data.dungeons and data.dungeons[step.dungeonId] then
        local d = data.dungeons[step.dungeonId]
        if d.entranceCoords then
            zoneId = d.entranceCoords.zoneId or d.entranceZoneId or zoneId
            x = d.entranceCoords.x
            y = d.entranceCoords.y
            zoneName = d.entranceZoneName or zoneName
        end
    end

    if (not x or not y) and step.questId and data.quests and data.quests[step.questId] then
        local q = data.quests[step.questId]
        local pt = ns.Scorer and ns.Scorer.GetQuestPoint and ns.Scorer.GetQuestPoint(q, step.action)
        if pt then
            zoneId = pt.zoneId or zoneId
            x = pt.x
            y = pt.y
        end
    end

    if (not x or not y) and zoneId and data.zones and data.zones[zoneId] and data.zones[zoneId].coords then
        x = data.zones[zoneId].coords.x
        y = data.zones[zoneId].coords.y
        zoneName = zoneName or data.zones[zoneId].name
    end

    if not x or not y then
        return { ok = false, reason = "no_coords" }
    end

    local meta = ns.GetActionMeta(step.action)
    local title = string.format("[%s] %s", meta.label, step.title or "Step")
    if npcName and npcName ~= "" then
        title = title .. " (" .. npcName .. ")"
    end

    local pinZoneId = zoneId
    local pinZoneName = zoneName
    local pinX = x
    local pinY = y
    local pinTitle = title
    local pinNpcName = npcName
    local isGateHop = false
    local gateName = nil

    if (not opts or opts.finalOnly ~= true)
        and (not ns.db or ns.db.navGateWaypoints ~= false)
        and ns.Nav and ns.Nav.ResolveStepHop and ns.State and ns.State.GetPlayerInfo then
        local player = ns.State.GetPlayerInfo()
        if player and (not player.isDead) and (not (ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost()))
            and player.zoneId and player.x and player.y then
            local hop = ns.Nav.ResolveStepHop(player, {
                questId = step.questId,
                action = step.action,
                zoneId = zoneId,
                x = x,
                y = y,
            })
            if hop and hop.reachable and hop.gateNodeId and hop.gateNodeId > 0
                and hop.gateZoneId and hop.gateZoneId > 0 and hop.gateX and hop.gateY then
                local sameSpot = (hop.gateZoneId == zoneId)
                    and (math.abs(hop.gateX - x) <= 1.0 and math.abs(hop.gateY - y) <= 1.0)
                if not sameSpot then
                    isGateHop = true
                    gateName = hop.gateName or hop.hopLabel or "Gate"
                    pinZoneId = hop.gateZoneId
                    pinZoneName = (data.zones and data.zones[hop.gateZoneId] and data.zones[hop.gateZoneId].name) or pinZoneName
                    pinX = hop.gateX
                    pinY = hop.gateY
                    pinNpcName = gateName
                    pinTitle = string.format("[%s] -> [%s] %s", gateName, meta.label, step.title or "Step")
                end
            end
        end
    end

    local res = Waypoint.SetTarget({
        zoneId = pinZoneId,
        zoneName = pinZoneName,
        x = pinX,
        y = pinY,
        title = pinTitle,
        npcName = pinNpcName,
    }, opts)
    if type(res) == "table" and isGateHop then
        res.isGateHop = true
        res.gateName = gateName
        res.finalZoneId = zoneId
        res.finalX = x
        res.finalY = y
    end
    return res
end

function Waypoint.SyncToCurrentStep(announce, openMap)
    local step = ns.State and ns.State.GetCurrentStep and ns.State.GetCurrentStep()
    if not step then
        return { ok = false, reason = "no_step" }
    end
    return Waypoint.SetForStep(step, {
        announce = announce,
        openMap = openMap,
        skipIfSame = not announce and not openMap,
    })
end

function Waypoint.Initialize()
    ns.RegisterCallback("STEP_CHANGED", function(step)
        if ns.db and ns.db.autoWaypoint ~= false and step
            and not (ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost()) then
            Waypoint.SetForStep(step, { skipIfSame = true })
        end
    end)
    ns.RegisterCallback("STATE_UPDATED", function(reason, step)
        if ns.db and ns.db.autoWaypoint ~= false and ns.db.navGateWaypoints ~= false
            and ns.Nav and ns.Nav.IsAvailable and ns.Nav.IsAvailable()
            and not (ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost())
            and (reason == "GPS_MOVED" or reason == "GPS_ZONE_CHANGED" or reason == "ZONE_CHANGED_NEW_AREA" or reason == "GPS_SYNC") then
            local targetStep = step or (ns.State and ns.State.GetCurrentStep and ns.State.GetCurrentStep())
            if targetStep then
                Waypoint.SetForStep(targetStep, { skipIfSame = true })
            end
        end
    end)
end
