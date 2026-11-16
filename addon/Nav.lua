-- ForeverQuestPath Nav.lua
--
-- Module rules:
--   1. Variables and preallocated scratch pools are declared at the top of the module.
--   2. Zero-GC Bidirectional Dijkstra (forward + reverse CSR with epoch-stamped visited
--      arrays and flat binary min-heaps) provides exact multimodal travel times and
--      intermediate gate/pass/flight/boat/tram/hearth hops in < 0.8ms without per-tick
--      table allocations or table wipes, using pre-anchor two-level integer
--      path cache keys across 2048 preallocated slots (512 source buckets) and
--      numeric capId/togglesMask identifiers (with O(1) PATH_CACHE checks in
--      both QueryPath and GetTravelSeconds before anchor resolution).
--   3. Spatial coord->node resolution uses precomputed 20x20 zoneCells with a bounded
--      3x3 / 5x5 walkable fallback ring and component matching so routes never cut
--      through mountains, cliffs, or oceans.
--   4. Reverse eligible-quest index nodeToEligible is rebuilt strictly when
--      questStateGeneration (or player level/maxlevel/faction/race/class/
--      profile/group, skipEliteSolo, or skipDungeons state) changes -- NEVER on 1.5s
--      GPS_MOVED ticks or virtual lookahead steps with skipIndexRebuild=true --
--      on top of zero-GC per-character minLevel buckets (staticLevelBuckets),
--      excluding solo-skipped open-world elite and deferred dungeon "s"/"o"
--      quests while preserving ready turn-ins ("t"), cached nearest-quest pool
--      (nearestCache), and exact settledBoundSec partial single-source field
--      reuse (singleCache).
--   5. RecordTaxiNodes only invalidates path caches and reports changed=true when
--      at least one previously unknown flight master is discovered, while
--      Nav.GetCacheEpoch() exposes a monotonic epoch bumped on every cache clear.
--   6. Gracefully falls back when ns.NavData is unavailable (e.g. isolated unit tests).

local addonName, ns = ...

local Nav = {}
ns.Nav = Nav

local navStats = {
    queryCalls = 0,
    bidirSolves = 0,
    singleFieldCalls = 0,
    nearestCalls = 0,
    floodSolves = 0,
}

--------------------------------------------------------------------------------
-- Top-Level Constants & Preallocated Pools (Rule 1: Variables at Top)
--------------------------------------------------------------------------------

local INF_SEC = 1e12
local DEFAULT_CELL_GRID = 20
local DEFAULT_UNMOUNTED_YDS = 7.5
local DEFAULT_MOUNTED_YDS = 11.4
local HEARTH_ITEM_ID = 6948
local HEARTH_CAST_SEC = 12.0
local FP_UNKNOWN_PENALTY_SEC = 45.0
local CELL_RING_STEP_PENALTY = 12.0
local DETOUR_MIN_STRAIGHT_YDS = 120.0
local DETOUR_MIN_RATIO = 1.30
local MAX_STAMP_VALUE = 1000000000
local PATH_CACHE_SIZE = 2048
local pathCacheEpoch = 1
local SRC_BUCKET_POOL_SIZE = 512
local MAX_END_ANCHORS = 3
local ROLE_TO_CODE = {
    s = 1,
    o = 2,
    t = 3,
}

local MODE_WALK = 1
local MODE_GATE = 2
local MODE_FLIGHT = 3
local MODE_BOAT = 4
local MODE_ZEPPELIN = 5
local MODE_AIRSHIP = 6
local MODE_TRAM = 7
local MODE_HEARTH = 8

local MODE_KINDS = {
    [MODE_WALK]     = "walk",
    [MODE_GATE]     = "gate",
    [MODE_FLIGHT]   = "flight",
    [MODE_BOAT]     = "boat",
    [MODE_ZEPPELIN] = "zeppelin",
    [MODE_AIRSHIP]  = "airship",
    [MODE_TRAM]     = "tram",
    [MODE_HEARTH]   = "hearth",
}

local FACTION_TO_SIDE = {
    Alliance = 1,
    Horde = 2,
    Both = 3,
    Neutral = 3,
}

local MODE_VERBS = {
    [MODE_WALK]     = "Via",
    [MODE_GATE]     = "Via",
    [MODE_FLIGHT]   = "Flight",
    [MODE_BOAT]     = "Boat",
    [MODE_ZEPPELIN] = "Zeppelin",
    [MODE_AIRSHIP]  = "Airship",
    [MODE_TRAM]     = "Deeprun Tram",
    [MODE_HEARTH]   = "Hearthstone",
}

-- Bidirectional Dijkstra pooled arrays (allocated once per capacity grow)
local arrayCap = 0
local searchStamp = 0
local fDist = {}
local bDist = {}
local fStamp = {}
local bStamp = {}
local fPrev = {}
local fViaEdge = {}
local bNext = {}
local bViaEdge = {}
local fHeapNode = {}
local fHeapDist = {}
local bHeapNode = {}
local bHeapDist = {}
local fHeapSize = 0
local bHeapSize = 0

-- Dedicated single-source outward Dijkstra pool (for FindNearestEligibleQuests
-- and O(1) distance queries from cached origin without clobbering Bidir state)
local sStampVal = 0
local sDist = {}
local sStamp = {}
local sHeapNode = {}
local sHeapDist = {}
local sHeapSize = 0
local singleCache = {
    valid = false,
    zoneId = 0,
    cellIdx = 0,
    capId = -1,
    stamp = 0,
    settledBoundSec = 0,
    fullField = false,
    sameZoneOnly = false,
}

-- Preallocated anchor/path scratch arrays (zero per-call allocation)
local srcAnchorNodes = { 0, 0, 0, 0 }
local srcAnchorSecs = { 0, 0, 0, 0 }
local srcAnchorComps = { 0, 0, 0, 0 }
local srcAnchorCount = 0

local dstAnchorNodes = { 0, 0, 0, 0 }
local dstAnchorSecs = { 0, 0, 0, 0 }
local dstAnchorComps = { 0, 0, 0, 0 }
local dstAnchorCount = 0

local pathNodesScratch = {}
local pathEdgesScratch = {}
local revNodesScratch = {}
local revEdgesScratch = {}
local pathNodesCount = 0
local pathEdgesCount = 0

-- Static NavData lookup caches built once on load
local lookupBuiltFor = nil
local hearthNameToNode = {}
local fpNameToId = {}

-- Dynamic reverse quest index (rebuilt ONLY on questStateGeneration changes)
local nodeHead = {}
local nodeHeadStamp = {}
local nodeStampVal = 0
local eligEdgeCount = 0
local eligNext = {}
local eligQid = {}
local eligRole = {}
local eligWalk = {}
local eligZone = {}
local eligX = {}
local eligY = {}
local priorityQids = {}
local unanchoredQids = {}
local seenQidStamp = {}
local qSearchStamp = 0
local activeSeenStamp = {}
local activeStampVal = 0
local eligQidStamp = {}
local eligStampVal = 0
local MAX_STATIC_MIN_LEVEL = 65
local staticLevelBuckets = {}
local staticLevelCounts = {}
for lv = 0, MAX_STATIC_MIN_LEVEL do
    staticLevelBuckets[lv] = {}
    staticLevelCounts[lv] = 0
end
local charStaticMeta = {
    valid = false,
    data = nil,
    nav = nil,
    factionMask = 0,
    raceId = 0,
    classId = 0,
    raceBitmask = -1,
    classBitmask = -1,
    classFile = "",
}
local eligibleMeta = {
    valid = false,
    version = 0,
    stateGen = -1,
    level = -1,
    maxOffset = -99,
    factionMask = 0,
    raceId = 0,
    classId = 0,
    raceBitmask = -1,
    classBitmask = -1,
    classFile = "",
    profile = "",
    togglesMask = -1,
    count = 0,
}
local nearestCache = {
    valid = false,
    eligVersion = -1,
    zoneId = 0,
    cellIdx = 0,
    capId = -1,
    sameZoneOnly = false,
    roleFilter = nil,
    maxSec = 0,
    maxResults = 0,
    keepFullField = false,
    noCap = false,
    outCount = 0,
}
local FAST_NAV_BLOCK_OPTS = { consultQuestie = false, checkIsDoable = false, useBulkCompletion = true, reasonless = true }
local nearestResultsPool = {}
local nearestCount = 0

-- Preallocated circular path query cache (zero GC per hit or update)
local pathCacheSlots = {}
local srcBucketSlots = {}
local srcBucketByKey = {}
local srcBucketFree = {}
local srcBucketFreeTop = SRC_BUCKET_POOL_SIZE
local pathCacheCursor = 0
for i = 1, SRC_BUCKET_POOL_SIZE do
    local bucket = {
        key = 0,
        refCount = 0,
        map = {},
    }
    srcBucketSlots[i] = bucket
    srcBucketFree[i] = bucket
end
for i = 1, PATH_CACHE_SIZE do
    pathCacheSlots[i] = {
        srcKey = 0,
        dstKey = 0,
        srcBucket = nil,
        reachable = false,
        totalSec = 0,
        straightYards = 0,
        usedHearth = false,
        hopKind = nil,
        hopLabel = nil,
        hopNote = nil,
        gateNodeId = 0,
        gateZoneId = 0,
        gateX = 0,
        gateY = 0,
        gateName = nil,
    }
end

--------------------------------------------------------------------------------
-- Internal Helpers
--------------------------------------------------------------------------------

local function NormalizeName(str)
    if type(str) ~= "string" then
        return ""
    end
    local s = string.lower(str)
    s = s:gsub("^%s+", ""):gsub("%s+$", "")
    s = s:gsub("[%.,'`]", "")
    s = s:gsub("%s+", " ")
    return s
end

local function EnsureCapacity(n)
    if n <= arrayCap then
        return
    end
    local oldCap = arrayCap
    local newCap = math.max(n, oldCap * 2, 1600)
    for i = oldCap + 1, newCap do
        fDist[i] = INF_SEC
        bDist[i] = INF_SEC
        fStamp[i] = 0
        bStamp[i] = 0
        fPrev[i] = 0
        fViaEdge[i] = 0
        bNext[i] = 0
        bViaEdge[i] = 0
        sDist[i] = INF_SEC
        sStamp[i] = 0
    end
    arrayCap = newCap
end

local function NextBidirStamp()
    searchStamp = searchStamp + 1
    if searchStamp >= MAX_STAMP_VALUE then
        for i = 1, arrayCap do
            fStamp[i] = 0
            bStamp[i] = 0
        end
        searchStamp = 1
    end
    return searchStamp
end

local function NextSingleStamp()
    sStampVal = sStampVal + 1
    if sStampVal >= MAX_STAMP_VALUE then
        for i = 1, arrayCap do
            sStamp[i] = 0
        end
        sStampVal = 1
        singleCache.valid = false
    end
    return sStampVal
end

local function ClearPathCache()
    pathCacheEpoch = pathCacheEpoch + 1
    for i = 1, PATH_CACHE_SIZE do
        local slot = pathCacheSlots[i]
        slot.srcKey = 0
        slot.dstKey = 0
        slot.srcBucket = nil
    end
    for k in pairs(srcBucketByKey) do
        srcBucketByKey[k] = nil
    end
    for i = 1, SRC_BUCKET_POOL_SIZE do
        local bucket = srcBucketSlots[i]
        bucket.key = 0
        bucket.refCount = 0
        local bmap = bucket.map
        for k in pairs(bmap) do
            bmap[k] = nil
        end
        srcBucketFree[i] = bucket
    end
    srcBucketFreeTop = SRC_BUCKET_POOL_SIZE
    pathCacheCursor = 0
end

local function EnsureStaticLookups(nav)
    if not nav or lookupBuiltFor == nav then
        return
    end
    lookupBuiltFor = nav
    EnsureCapacity((nav.nodeCount or 0) + 8)
    ClearPathCache()

    for k in pairs(hearthNameToNode) do hearthNameToNode[k] = nil end
    for k in pairs(fpNameToId) do fpNameToId[k] = nil end

    local nodeName = nav.nodeName or {}
    local nodeZone = nav.nodeZone or {}
    local nodeKind = nav.nodeKind or {}
    local nodeFpId = nav.nodeFpId or {}
    local hearthNodes = nav.hearthNodes or {}

    -- Map zone names & hearth node names -> hearth nodeId
    local data = ns.GetData and ns.GetData() or ns.GeneratedData
    local zones = data and data.zones
    for zid, nid in pairs(hearthNodes) do
        if type(nid) == "number" and nid > 0 then
            local nm = nodeName[nid]
            if type(nm) == "string" and nm ~= "" then
                hearthNameToNode[NormalizeName(nm)] = nid
            end
            local zInfo = zones and zones[tonumber(zid) or zid]
            if zInfo and type(zInfo.name) == "string" then
                hearthNameToNode[NormalizeName(zInfo.name)] = nid
            end
            if zInfo and type(zInfo.hub) == "string" then
                hearthNameToNode[NormalizeName(zInfo.hub)] = nid
            end
        end
    end

    -- Also index any node marked kind==1 (hub) or 3 (fp) by normalized name for bind matching
    for nid = 1, (nav.nodeCount or 0) do
        local nm = nodeName[nid]
        if type(nm) == "string" and nm ~= "" then
            local norm = NormalizeName(nm)
            local zid = nodeZone[nid]
            local hNid = zid and (hearthNodes[zid] or hearthNodes[tostring(zid)])
            if hNid and not hearthNameToNode[norm] then
                hearthNameToNode[norm] = hNid
            end
            local commaPrefix = nm:match("^([^,]+),")
            if commaPrefix then
                local pNorm = NormalizeName(commaPrefix)
                if hNid and not hearthNameToNode[pNorm] then
                    hearthNameToNode[pNorm] = hNid
                end
                if nodeKind[nid] == 3 and nodeFpId[nid] then
                    fpNameToId[pNorm] = nodeFpId[nid]
                end
            end
            if nodeKind[nid] == 3 and nodeFpId[nid] then
                fpNameToId[norm] = nodeFpId[nid]
            end
        end
    end
end

-- Min-heap push/pop helpers (separate tiny functions on preallocated arrays = zero GC)
local function HeapPush(heapN, heapD, size, node, dist)
    size = size + 1
    local i = size
    while i > 1 do
        local p = math.floor(i * 0.5)
        if heapD[p] <= dist then
            break
        end
        heapN[i] = heapN[p]
        heapD[i] = heapD[p]
        i = p
    end
    heapN[i] = node
    heapD[i] = dist
    return size
end

local function HeapPop(heapN, heapD, size)
    local topN = heapN[1]
    local topD = heapD[1]
    local lastN = heapN[size]
    local lastD = heapD[size]
    size = size - 1
    if size >= 1 then
        local i = 1
        local half = math.floor(size * 0.5)
        while i <= half do
            local left = i * 2
            local right = left + 1
            local best = left
            local bestD = heapD[left]
            if right <= size and heapD[right] < bestD then
                best = right
                bestD = heapD[right]
            end
            if lastD <= bestD then
                break
            end
            heapN[i] = heapN[best]
            heapD[i] = bestD
            i = best
        end
        heapN[i] = lastN
        heapD[i] = lastD
    end
    return size, topN, topD
end

--------------------------------------------------------------------------------
-- Public Availability, Coordinates & Capability Context
--------------------------------------------------------------------------------

function Nav.IsAvailable()
    local nav = ns.NavData
    return type(nav) == "table"
        and type(nav.nodeCount) == "number" and nav.nodeCount > 0
        and type(nav.fHead) == "table" and type(nav.fTo) == "table"
        and type(nav.rHead) == "table" and type(nav.rFrom) == "table"
        and type(nav.rFwdIdx) == "table" and type(nav.zoneCells) == "table"
end

function Nav.InvalidatePathCache()
    ClearPathCache()
    singleCache.valid = false
end

function Nav.GetCacheEpoch()
    return pathCacheEpoch
end

function Nav.GetCellGrid(nav)
    return (nav and nav.cellGrid) or DEFAULT_CELL_GRID
end

function Nav.CoordToCellXY(nav, x, y)
    local grid = (nav and nav.cellGrid) or DEFAULT_CELL_GRID
    local nx = tonumber(x) or 50
    local ny = tonumber(y) or 50
    if nx < 0 then nx = 0 elseif nx >= 100 then nx = 99.999 end
    if ny < 0 then ny = 0 elseif ny >= 100 then ny = 99.999 end
    local cx = math.floor(nx * grid / 100)
    local cy = math.floor(ny * grid / 100)
    if cx < 0 then cx = 0 elseif cx >= grid then cx = grid - 1 end
    if cy < 0 then cy = 0 elseif cy >= grid then cy = grid - 1 end
    return cx, cy, cy * grid + cx + 1
end

function Nav.ZoneCoordsToYards(nav, zoneId, x1, y1, x2, y2)
    local b = nav and nav.zoneBounds and zoneId and nav.zoneBounds[zoneId]
    local dx = (tonumber(x2) or 50) - (tonumber(x1) or 50)
    local dy = (tonumber(y2) or 50) - (tonumber(y1) or 50)
    if b and b[2] and b[3] and b[4] and b[5] then
        local dWy = (dx * 0.01) * (b[3] - b[2])
        local dWx = (dy * 0.01) * (b[5] - b[4])
        return math.sqrt(dWy * dWy + dWx * dWx)
    end
    local sx = dx * 28.0
    local sy = dy * 18.67
    return math.sqrt(sx * sx + sy * sy)
end

function Nav.ResolveCellAnchors(nav, zoneId, x, y, outNodes, outSecs, outComps)
    if not (nav and zoneId and x and y) then
        return 0, 0, 0
    end
    local zc = nav.zoneCells and (nav.zoneCells[zoneId] or nav.zoneCells[tostring(zoneId)])
    if not zc then
        return 0, 0, 0
    end
    local grid = nav.cellGrid or DEFAULT_CELL_GRID
    local cx, cy, cellIdx = Nav.CoordToCellXY(nav, x, y)
    local cell = zc[cellIdx]
    local extraPenalty = 0

    if not cell then
        -- Bounded 3x3 then 5x5 walkable fallback ring (for points on zone edge / cliff rim)
        local bestDist2 = INF_SEC
        local bestCell = nil
        local cellW = 100 / grid
        for radius = 1, 2 do
            for dy = -radius, radius do
                local ny = cy + dy
                if ny >= 0 and ny < grid then
                    for dx = -radius, radius do
                        if math.abs(dx) == radius or math.abs(dy) == radius then
                            local nx = cx + dx
                            if nx >= 0 and nx < grid then
                                local idx = ny * grid + nx + 1
                                local cand = zc[idx]
                                if cand and cand[2] then
                                    local ccx = (nx + 0.5) * cellW
                                    local ccy = (ny + 0.5) * cellW
                                    local ddx = x - ccx
                                    local ddy = y - ccy
                                    local d2 = ddx * ddx + ddy * ddy
                                    if d2 < bestDist2 then
                                        bestDist2 = d2
                                        bestCell = cand
                                        extraPenalty = math.sqrt(dx * dx + dy * dy) * CELL_RING_STEP_PENALTY
                                    end
                                end
                            end
                        end
                    end
                end
            end
            if bestCell then
                cell = bestCell
                break
            end
        end
    end

    if not cell or not cell[2] then
        return 0, cellIdx, 0
    end

    local compId = tonumber(cell[1]) or 0
    local count = 0
    local nodeComp = nav.nodeComp
    local pos = 2
    while cell[pos] and cell[pos + 1] and count < MAX_END_ANCHORS do
        count = count + 1
        local nid = cell[pos]
        outNodes[count] = nid
        outSecs[count] = (tonumber(cell[pos + 1]) or 0) + extraPenalty
        outComps[count] = (nodeComp and nodeComp[nid]) or compId
        pos = pos + 2
    end
    return count, cellIdx, compId
end

function Nav.IsHearthReady()
    local fn = (C_Container and C_Container.GetItemCooldown) or GetItemCooldown
    if type(fn) ~= "function" then
        return false
    end
    local ok, start, duration = pcall(fn, HEARTH_ITEM_ID)
    if not ok or type(start) ~= "number" or type(duration) ~= "number" then
        return false
    end
    if start == 0 or duration == 0 then
        return true
    end
    local now = (GetTime and GetTime()) or 0
    return (start + duration - now) <= 15
end

function Nav.ResolveHearthNodeId(nav, player, opts)
    if not nav or not nav.hearthNodes then
        return 0
    end
    EnsureStaticLookups(nav)
    local hz = opts and opts.hearthZoneId or (player and player.hearthZoneId)
    if hz then
        local nid = nav.hearthNodes[hz] or nav.hearthNodes[tostring(hz)]
        if nid and nid > 0 then
            return nid
        end
    end
    local bindLoc = (opts and opts.bindLocation) or (player and player.bindLocation)
    if type(bindLoc) == "string" and bindLoc ~= "" then
        local norm = NormalizeName(bindLoc)
        local nid = hearthNameToNode[norm]
        if nid and nid > 0 then
            return nid
        end
        local prefix = bindLoc:match("^([^,]+),")
        if prefix then
            nid = hearthNameToNode[NormalizeName(prefix)]
            if nid and nid > 0 then
                return nid
            end
        end
    end
    return 0
end

function Nav.RecordTaxiNodes()
    if not (NumTaxiNodes and TaxiNodeName) then
        return 0, false
    end
    local nav = ns.NavData
    if nav then
        EnsureStaticLookups(nav)
    end
    ns.charDB = ns.charDB or {}
    ns.charDB.knownFlightMasters = ns.charDB.knownFlightMasters or {}
    local known = ns.charDB.knownFlightMasters
    local added = 0
    if not known.__scanned then
        known.__scanned = true
        added = 1
    end
    local n = tonumber(NumTaxiNodes()) or 0
    local recorded = 0
    for i = 1, n do
        local name = TaxiNodeName(i)
        if type(name) == "string" and name ~= "" then
            local norm = NormalizeName(name)
            if not known[norm] then
                known[norm] = true
                added = added + 1
            end
            local prefix = name:match("^([^,]+),")
            local normPrefix = prefix and NormalizeName(prefix) or nil
            if normPrefix and not known[normPrefix] then
                known[normPrefix] = true
                added = added + 1
            end
            local fpId = fpNameToId[norm] or (normPrefix and fpNameToId[normPrefix])
            if fpId and not known[fpId] then
                known[fpId] = true
                added = added + 1
            end
            recorded = recorded + 1
        end
    end
    if added > 0 then
        Nav.InvalidatePathCache()
    end
    return recorded, added > 0
end

local function BuildCapabilityContext(nav, player, opts)
    local facStr = (opts and opts.faction)
        or (player and player.faction)
        or (UnitFactionGroup and select(1, UnitFactionGroup("player")))
        or "Alliance"
    local factionMask = (facStr == "Horde") and 2 or 1

    local pLv = tonumber((opts and opts.level) or (player and player.level) or (UnitLevel and UnitLevel("player"))) or 24
    local isMountedNow = (IsMounted and IsMounted()) and true or false
    local assumeMounted = (pLv >= 40) and not (opts and opts.unmountedOnly == true)
    if opts and opts.mounted ~= nil then
        assumeMounted = (opts.mounted == true)
    end

    local uSpeed = nav.unmountedYdsPerSec or DEFAULT_UNMOUNTED_YDS
    local mSpeed = nav.mountedYdsPerSec or DEFAULT_MOUNTED_YDS
    local mountedFlag = (isMountedNow or assumeMounted) and 1 or 0
    local groundScale = (opts and tonumber(opts.groundScale))
        or ((mountedFlag == 1 and mSpeed > 0) and (uSpeed / mSpeed) or 1.0)

    local hearthNodeId = 0
    if not (opts and opts.allowHearth == false) then
        local hearthReady = (opts and opts.forceHearthReady == true) or Nav.IsHearthReady()
        if hearthReady then
            hearthNodeId = Nav.ResolveHearthNodeId(nav, player, opts)
        end
    end

    local knownFps = (opts and opts.knownFps) or (ns.charDB and ns.charDB.knownFlightMasters)
    local hasKnownFps = (type(knownFps) == "table" and (knownFps.__scanned == true or next(knownFps) ~= nil)) and 1 or 0
    local factionBit = (factionMask == 2) and 1 or 0
    local capId = ((hearthNodeId * 8) + (factionBit * 4) + (mountedFlag * 2) + hasKnownFps)
    return factionMask, groundScale, hearthNodeId, knownFps, hasKnownFps == 1, capId
end

local function GetEdgeCost(nav, e, factionMask, groundScale, knownFps, hasKnownFps)
    local fac = nav.eFaction[e]
    if fac ~= 3 and fac ~= factionMask then
        return nil
    end
    local c = nav.eCost[e]
    if not c or c < 0 then
        return nil
    end
    if nav.eGround[e] == 1 then
        c = c * groundScale
    elseif hasKnownFps and nav.eMode[e] == MODE_FLIGHT then
        local fpId = nav.nodeFpId and nav.nodeFpId[nav.fTo[e]]
        if fpId and not knownFps[fpId] then
            local nm = nav.nodeName and nav.nodeName[nav.fTo[e]]
            if not (nm and knownFps[NormalizeName(nm)]) then
                c = c + FP_UNKNOWN_PENALTY_SEC
            end
        end
    end
    if c < 0.01 then
        c = 0.01
    end
    return c
end

--------------------------------------------------------------------------------
-- Zero-GC Bidirectional Dijkstra + Multi-Hop Gate Extraction
--------------------------------------------------------------------------------

local function SeedQuestOrCellAnchors(nav, zoneId, x, y, questId, role, outNodes, outSecs, outComps)
    if questId and nav.questAnchors then
        local qa = nav.questAnchors[questId] or nav.questAnchors[tostring(questId)]
        local list = qa and (qa[role] or (role == "t" and qa.s))
        if type(list) == "table" and list[1] and list[2] then
            local nodeComp = nav.nodeComp
            local count = 0
            local pos = 1
            while list[pos] and list[pos + 1] and count < MAX_END_ANCHORS do
                count = count + 1
                local nid = list[pos]
                outNodes[count] = nid
                outSecs[count] = tonumber(list[pos + 1]) or 0
                outComps[count] = (nodeComp and nodeComp[nid]) or 0
                pos = pos + 2
            end
            if count > 0 then
                local _, _, cellIdx = Nav.CoordToCellXY(nav, x, y)
                local zc = nav.zoneCells and (nav.zoneCells[zoneId] or nav.zoneCells[tostring(zoneId)])
                local cell = zc and zc[cellIdx]
                local compId = (cell and tonumber(cell[1])) or 0
                return count, cellIdx, compId
            end
        end
    end
    return Nav.ResolveCellAnchors(nav, zoneId, x, y, outNodes, outSecs, outComps)
end

local function ExpandForwardStep(nav, stamp, factionMask, groundScale, knownFps, hasKnownFps, bestTotal, meetNode)
    local u, d
    fHeapSize, u, d = HeapPop(fHeapNode, fHeapDist, fHeapSize)
    if d > fDist[u] then
        return bestTotal, meetNode
    end
    local fHead = nav.fHead
    local fTo = nav.fTo
    local startE = fHead[u]
    local endE = fHead[u + 1] - 1
    for e = startE, endE do
        local w = GetEdgeCost(nav, e, factionMask, groundScale, knownFps, hasKnownFps)
        if w then
            local v = fTo[e]
            local nd = d + w
            if fStamp[v] ~= stamp or nd < fDist[v] then
                fStamp[v] = stamp
                fDist[v] = nd
                fPrev[v] = u
                fViaEdge[v] = e
                fHeapSize = HeapPush(fHeapNode, fHeapDist, fHeapSize, v, nd)
                if bStamp[v] == stamp then
                    local total = nd + bDist[v]
                    if total < bestTotal then
                        bestTotal = total
                        meetNode = v
                    end
                end
            end
        end
    end
    return bestTotal, meetNode
end

local function ExpandBackwardStep(nav, stamp, factionMask, groundScale, knownFps, hasKnownFps, bestTotal, meetNode)
    local v, d
    bHeapSize, v, d = HeapPop(bHeapNode, bHeapDist, bHeapSize)
    if d > bDist[v] then
        return bestTotal, meetNode
    end
    local rHead = nav.rHead
    local rFrom = nav.rFrom
    local rFwdIdx = nav.rFwdIdx
    local startRe = rHead[v]
    local endRe = rHead[v + 1] - 1
    for re = startRe, endRe do
        local e = rFwdIdx[re]
        local w = GetEdgeCost(nav, e, factionMask, groundScale, knownFps, hasKnownFps)
        if w then
            local u = rFrom[re]
            local nd = d + w
            if bStamp[u] ~= stamp or nd < bDist[u] then
                bStamp[u] = stamp
                bDist[u] = nd
                bNext[u] = v
                bViaEdge[u] = e
                bHeapSize = HeapPush(bHeapNode, bHeapDist, bHeapSize, u, nd)
                if fStamp[u] == stamp then
                    local total = fDist[u] + nd
                    if total < bestTotal then
                        bestTotal = total
                        meetNode = u
                    end
                end
            end
        end
    end
    return bestTotal, meetNode
end

local function ReconstructPath(nav, meetNode, stamp, cap)
    pathNodesCount = 0
    pathEdgesCount = 0
    if meetNode <= 0 then
        return false
    end

    local revN = 0
    local revE = 0
    local cur = meetNode
    local usedHearth = false
    local guard = 0
    while cur > 0 and guard <= cap do
        guard = guard + 1
        revN = revN + 1
        revNodesScratch[revN] = cur
        local prev = (fStamp[cur] == stamp) and fPrev[cur] or 0
        if prev == -1 then
            usedHearth = true
            break
        elseif prev > 0 then
            revE = revE + 1
            revEdgesScratch[revE] = fViaEdge[cur]
            cur = prev
        else
            break
        end
    end

    for i = revN, 1, -1 do
        pathNodesCount = pathNodesCount + 1
        pathNodesScratch[pathNodesCount] = revNodesScratch[i]
    end
    for i = revE, 1, -1 do
        pathEdgesCount = pathEdgesCount + 1
        pathEdgesScratch[pathEdgesCount] = revEdgesScratch[i]
    end

    cur = meetNode
    guard = 0
    while cur > 0 and guard <= cap do
        guard = guard + 1
        local nxt = (bStamp[cur] == stamp) and bNext[cur] or 0
        if nxt <= 0 then
            break
        end
        pathEdgesCount = pathEdgesCount + 1
        pathEdgesScratch[pathEdgesCount] = bViaEdge[cur]
        pathNodesCount = pathNodesCount + 1
        pathNodesScratch[pathNodesCount] = nxt
        cur = nxt
    end
    return usedHearth
end

local function FormatNodeOrZone(nav, nid)
    local nm = nav.nodeName and nav.nodeName[nid]
    if type(nm) == "string" and nm ~= "" then
        return nm
    end
    local zid = nav.nodeZone and nav.nodeZone[nid]
    local data = ns.GetData and ns.GetData() or ns.GeneratedData
    local zInfo = zid and data and data.zones and data.zones[zid]
    if zInfo and zInfo.name then
        return zInfo.name
    end
    return zid and ("Zone " .. tostring(zid)) or "Route Point"
end

local function PopulateSlotHopSummary(nav, slot, srcZone, srcX, srcY, dstZone, dstX, dstY, totalSec, usedHearth, groundScale)
    local uSpeed = nav.unmountedYdsPerSec or DEFAULT_UNMOUNTED_YDS
    local speedYds = uSpeed / ((groundScale and groundScale > 0) and groundScale or 1.0)
    local straightYards = (srcZone == dstZone) and Nav.ZoneCoordsToYards(nav, srcZone, srcX, srcY, dstX, dstY) or 0

    slot.reachable = true
    slot.totalSec = totalSec
    slot.straightYards = straightYards
    slot.usedHearth = usedHearth
    slot.hopKind = nil
    slot.hopLabel = nil
    slot.hopNote = nil
    slot.gateNodeId = 0
    slot.gateZoneId = 0
    slot.gateX = 0
    slot.gateY = 0
    slot.gateName = nil

    if usedHearth and pathNodesCount >= 1 then
        local hNode = pathNodesScratch[1]
        local hName = FormatNodeOrZone(nav, hNode)
        slot.hopKind = "hearth"
        slot.hopLabel = "Hearthstone -> " .. hName
        slot.hopNote = string.format("Hearthstone to %s (~%.1fm)", hName, math.max(0.1, totalSec / 60.0))
        return
    end

    local eMode = nav.eMode
    local nodeZone = nav.nodeZone
    local nodeX = nav.nodeX
    local nodeY = nav.nodeY
    local edgeNotes = nav.edgeNotes

    -- Priority 1: First transport (flight/boat/zepp/airship/tram) or zone-border gate/tunnel
    for i = 1, pathEdgesCount do
        local e = pathEdgesScratch[i]
        local u = pathNodesScratch[i]
        local v = pathNodesScratch[i + 1]
        local mode = eMode[e] or MODE_WALK
        local uZone = nodeZone[u]
        local vZone = nodeZone[v]

        if mode >= MODE_FLIGHT or mode == MODE_GATE or (uZone ~= vZone) then
            local kind = MODE_KINDS[mode] or ((uZone ~= vZone) and "gate" or "walk")
            local customNote = edgeNotes and edgeNotes[e]
            local uLabel = FormatNodeOrZone(nav, u)
            local vLabel = FormatNodeOrZone(nav, v)
            local label
            if type(customNote) == "string" and customNote ~= "" then
                label = customNote
            elseif mode >= MODE_FLIGHT then
                label = string.format("%s: %s -> %s", MODE_VERBS[mode] or "Travel", uLabel, vLabel)
            else
                local passName = (nav.nodeName and nav.nodeName[u] and nav.nodeName[u] ~= "" and nav.nodeName[u])
                    or (nav.nodeName and nav.nodeName[v] and nav.nodeName[v] ~= "" and nav.nodeName[v])
                    or (vLabel .. " Border")
                label = "Via " .. passName
            end
            slot.hopKind = kind
            slot.hopLabel = label
            slot.hopNote = string.format("%s (~%.1fm)", label, math.max(0.1, totalSec / 60.0))
            slot.gateNodeId = u
            slot.gateZoneId = uZone or srcZone
            slot.gateX = nodeX[u] or dstX
            slot.gateY = nodeY[u] or dstY
            slot.gateName = label
            return
        end
    end

    -- Priority 2: Intra-zone cliff/mountain/water detour -> pin intermediate road/hub waypoint
    if srcZone == dstZone and pathNodesCount >= 2 and straightYards >= DETOUR_MIN_STRAIGHT_YDS then
        local pathYards = totalSec * speedYds
        if pathYards >= straightYards * DETOUR_MIN_RATIO then
            for i = 1, pathNodesCount do
                local nid = pathNodesScratch[i]
                if nodeZone[nid] == srcZone and nodeX[nid] and nodeY[nid] then
                    local distFromSrc = Nav.ZoneCoordsToYards(nav, srcZone, srcX, srcY, nodeX[nid], nodeY[nid])
                    local distToDst = Nav.ZoneCoordsToYards(nav, srcZone, nodeX[nid], nodeY[nid], dstX, dstY)
                    if distFromSrc >= 45.0 and distToDst >= 45.0 then
                        local uLabel = FormatNodeOrZone(nav, nid)
                        local label = "Via " .. uLabel .. " (road)"
                        slot.hopKind = "detour"
                        slot.hopLabel = label
                        slot.hopNote = string.format("%s (~%.1fm)", label, math.max(0.1, totalSec / 60.0))
                        slot.gateNodeId = nid
                        slot.gateZoneId = srcZone
                        slot.gateX = nodeX[nid]
                        slot.gateY = nodeY[nid]
                        slot.gateName = label
                        return
                    end
                end
            end
        end
    end
end

local function CheckLocalSameCellOrBox(nav, srcZone, srcX, srcY, dstZone, dstX, dstY, srcCellIdx, dstCellIdx, srcComp, dstComp)
    if srcZone ~= dstZone then
        return false
    end
    if srcCellIdx and srcCellIdx > 0 and srcCellIdx == dstCellIdx
        and math.abs((tonumber(srcX) or 50) - (tonumber(dstX) or 50)) <= 0.45
        and math.abs((tonumber(srcY) or 50) - (tonumber(dstY) or 50)) <= 0.45 then
        return true
    end
    if srcComp <= 0 or srcComp ~= dstComp then
        return false
    end
    if srcCellIdx == dstCellIdx then
        return true
    end
    local grid = nav.cellGrid or DEFAULT_CELL_GRID
    local scx = (srcCellIdx - 1) % grid
    local scy = math.floor((srcCellIdx - 1) / grid)
    local dcx = (dstCellIdx - 1) % grid
    local dcy = math.floor((dstCellIdx - 1) / grid)
    if math.abs(scx - dcx) <= 1 and math.abs(scy - dcy) <= 1 then
        local zc = nav.zoneCells and (nav.zoneCells[srcZone] or nav.zoneCells[tostring(srcZone)])
        if not zc then return false end
        local minX, maxX = math.min(scx, dcx), math.max(scx, dcx)
        local minY, maxY = math.min(scy, dcy), math.max(scy, dcy)
        for cy = minY, maxY do
            for cx = minX, maxX do
                local cell = zc[cy * grid + cx + 1]
                if not cell or (tonumber(cell[1]) or 0) ~= srcComp then
                    return false
                end
            end
        end
        return true
    end
    return false
end

-- Core zero-GC path query. Returns a reusable slot table (do NOT store across later calls;
-- copy fields immediately if persisting) or nil when NavData / zone cannot route.
function Nav.QueryPath(srcZone, srcX, srcY, dstZone, dstX, dstY, player, opts)
    navStats.queryCalls = navStats.queryCalls + 1
    if not Nav.IsAvailable() then
        return nil
    end
    if not (srcZone and srcX and srcY and dstZone and dstX and dstY) then
        return nil
    end

    local nav = ns.NavData
    EnsureStaticLookups(nav)

    local factionMask, groundScale, hearthNodeId, knownFps, hasKnownFps, capId = BuildCapabilityContext(nav, player, opts)
    local _, _, sCellIdx = Nav.CoordToCellXY(nav, srcX, srcY)
    local _, _, dCellIdx = Nav.CoordToCellXY(nav, dstX, dstY)
    local role = opts and opts.role or "s"
    local qid = opts and opts.questId or nil

    local rawSComp, rawDComp = 0, 0
    if srcZone == dstZone then
        local zc = nav.zoneCells and (nav.zoneCells[srcZone] or nav.zoneCells[tostring(srcZone)])
        if zc then
            rawSComp = (zc[sCellIdx] and tonumber(zc[sCellIdx][1])) or 0
            rawDComp = (zc[dCellIdx] and tonumber(zc[dCellIdx][1])) or 0
        end
        if rawSComp > 0 and CheckLocalSameCellOrBox(nav, srcZone, srcX, srcY, dstZone, dstX, dstY, sCellIdx, dCellIdx, rawSComp, rawDComp) then
            local uSpeed = nav.unmountedYdsPerSec or DEFAULT_UNMOUNTED_YDS
            local speed = uSpeed / ((groundScale and groundScale > 0) and groundScale or 1.0)
            local yards = Nav.ZoneCoordsToYards(nav, srcZone, srcX, srcY, dstX, dstY)
            if not Nav._localTransientSlot then
                Nav._localTransientSlot = {}
            end
            local tr = Nav._localTransientSlot
            tr.reachable = true
            tr.totalSec = math.max(1.0, yards / speed)
            tr.straightYards = yards
            tr.usedHearth = false
            tr.hopKind = nil
            tr.hopLabel = nil
            tr.hopNote = nil
            tr.gateNodeId = 0
            tr.gateZoneId = 0
            tr.gateX = 0
            tr.gateY = 0
            tr.gateName = nil
            return tr
        end
    end

    local rCode = ROLE_TO_CODE[role] or 1
    local qNum = tonumber(qid) or 0
    local srcKey = ((capId * 32768 + srcZone) * 512) + sCellIdx
    local dstKey
    if qNum > 0 and qNum < 2097152 then
        dstKey = ((((dstZone * 512 + dCellIdx) * 4 + rCode) * 2097152) + qNum)
    elseif qNum <= 0 then
        dstKey = -((dstZone * 512 + dCellIdx) * 4 + rCode)
    else
        dstKey = nil
    end

    local hitBucket = (dstKey and not (opts and opts.noCache)) and srcBucketByKey[srcKey] or nil
    local hit = hitBucket and hitBucket.map[dstKey] or nil
    if hit then
        return hit
    end

    local sCount, resolvedSCell, sComp = Nav.ResolveCellAnchors(nav, srcZone, srcX, srcY, srcAnchorNodes, srcAnchorSecs, srcAnchorComps)
    local dCount, resolvedDCell, dComp = SeedQuestOrCellAnchors(nav, dstZone, dstX, dstY, qid, role, dstAnchorNodes, dstAnchorSecs, dstAnchorComps)

    if sCount == 0 or dCount == 0 then
        return nil
    end

    -- Local continuous distance fallback when raw cell component was 0 and ring resolution found matching component
    if srcZone == dstZone and rawSComp <= 0 and CheckLocalSameCellOrBox(nav, srcZone, srcX, srcY, dstZone, dstX, dstY, resolvedSCell, resolvedDCell, sComp, dComp) then
        local uSpeed = nav.unmountedYdsPerSec or DEFAULT_UNMOUNTED_YDS
        local speed = uSpeed / ((groundScale and groundScale > 0) and groundScale or 1.0)
        local yards = Nav.ZoneCoordsToYards(nav, srcZone, srcX, srcY, dstX, dstY)
        -- Use dedicated transient slot outside circular cache so local moves never evict long-range entries
        if not Nav._localTransientSlot then
            Nav._localTransientSlot = {}
        end
        local tr = Nav._localTransientSlot
        tr.reachable = true
        tr.totalSec = math.max(1.0, yards / speed)
        tr.straightYards = yards
        tr.usedHearth = false
        tr.hopKind = nil
        tr.hopLabel = nil
        tr.hopNote = nil
        tr.gateNodeId = 0
        tr.gateZoneId = 0
        tr.gateX = 0
        tr.gateY = 0
        tr.gateName = nil
        return tr
    end

    -- Run Zero-GC Bidirectional Dijkstra
    local stamp = NextBidirStamp()
    navStats.bidirSolves = navStats.bidirSolves + 1
    fHeapSize = 0
    bHeapSize = 0
    local bestTotal = INF_SEC
    local meetNode = 0

    -- Seed forwardPQ from source anchors
    for i = 1, sCount do
        local u = srcAnchorNodes[i]
        local d = srcAnchorSecs[i] * groundScale
        if fStamp[u] ~= stamp or d < fDist[u] then
            fStamp[u] = stamp
            fDist[u] = d
            fPrev[u] = 0
            fViaEdge[u] = 0
            fHeapSize = HeapPush(fHeapNode, fHeapDist, fHeapSize, u, d)
        end
    end

    -- Optional O(1) Hearthstone edge seeded straight into forwardPQ
    if hearthNodeId > 0 then
        if fStamp[hearthNodeId] ~= stamp or HEARTH_CAST_SEC < fDist[hearthNodeId] then
            fStamp[hearthNodeId] = stamp
            fDist[hearthNodeId] = HEARTH_CAST_SEC
            fPrev[hearthNodeId] = -1
            fViaEdge[hearthNodeId] = 0
            fHeapSize = HeapPush(fHeapNode, fHeapDist, fHeapSize, hearthNodeId, HEARTH_CAST_SEC)
        end
    end

    -- Seed backwardPQ from destination anchors
    for j = 1, dCount do
        local v = dstAnchorNodes[j]
        local d = dstAnchorSecs[j] * groundScale
        if bStamp[v] ~= stamp or d < bDist[v] then
            bStamp[v] = stamp
            bDist[v] = d
            bNext[v] = 0
            bViaEdge[v] = 0
            bHeapSize = HeapPush(bHeapNode, bHeapDist, bHeapSize, v, d)
            if fStamp[v] == stamp then
                local total = fDist[v] + d
                if total < bestTotal then
                    bestTotal = total
                    meetNode = v
                end
            end
        end
    end

    -- Bidirectional meeting loop
    while fHeapSize > 0 or bHeapSize > 0 do
        if fHeapSize > 0 and bHeapSize > 0 then
            if fHeapDist[1] + bHeapDist[1] >= bestTotal then
                break
            end
            if fHeapSize <= bHeapSize then
                bestTotal, meetNode = ExpandForwardStep(nav, stamp, factionMask, groundScale, knownFps, hasKnownFps, bestTotal, meetNode)
            else
                bestTotal, meetNode = ExpandBackwardStep(nav, stamp, factionMask, groundScale, knownFps, hasKnownFps, bestTotal, meetNode)
            end
        elseif fHeapSize > 0 then
            if fHeapDist[1] >= bestTotal then
                break
            end
            bestTotal, meetNode = ExpandForwardStep(nav, stamp, factionMask, groundScale, knownFps, hasKnownFps, bestTotal, meetNode)
        else
            if bHeapDist[1] >= bestTotal then
                break
            end
            bestTotal, meetNode = ExpandBackwardStep(nav, stamp, factionMask, groundScale, knownFps, hasKnownFps, bestTotal, meetNode)
        end
    end

    -- Write into circular preallocated slot
    pathCacheCursor = (pathCacheCursor % PATH_CACHE_SIZE) + 1
    local slot = pathCacheSlots[pathCacheCursor]
    if slot.srcBucket then
        local oldB = slot.srcBucket
        oldB.map[slot.dstKey] = nil
        oldB.refCount = oldB.refCount - 1
        if oldB.refCount <= 0 then
            srcBucketByKey[oldB.key] = nil
            oldB.key = 0
            oldB.refCount = 0
            srcBucketFreeTop = srcBucketFreeTop + 1
            srcBucketFree[srcBucketFreeTop] = oldB
        end
        slot.srcBucket = nil
        slot.srcKey = 0
        slot.dstKey = 0
    end

    if bestTotal >= INF_SEC or meetNode <= 0 then
        slot.reachable = false
        slot.totalSec = INF_SEC
        slot.straightYards = 0
        slot.usedHearth = false
        slot.hopKind = nil
        slot.hopLabel = nil
        slot.hopNote = nil
        slot.gateNodeId = 0
        slot.gateZoneId = 0
        slot.gateX = 0
        slot.gateY = 0
        slot.gateName = nil
    else
        local usedHearth = ReconstructPath(nav, meetNode, stamp, nav.nodeCount or 1600)
        PopulateSlotHopSummary(nav, slot, srcZone, srcX, srcY, dstZone, dstX, dstY, bestTotal, usedHearth, groundScale)
    end

    if dstKey and not (opts and opts.noCache) then
        local bucket = srcBucketByKey[srcKey]
        if not bucket and srcBucketFreeTop > 0 then
            bucket = srcBucketFree[srcBucketFreeTop]
            srcBucketFree[srcBucketFreeTop] = nil
            srcBucketFreeTop = srcBucketFreeTop - 1
            bucket.key = srcKey
            bucket.refCount = 0
            srcBucketByKey[srcKey] = bucket
        end
        if bucket then
            slot.srcKey = srcKey
            slot.dstKey = dstKey
            slot.srcBucket = bucket
            bucket.map[dstKey] = slot
            bucket.refCount = bucket.refCount + 1
        end
    end
    return slot
end

function Nav.GetTravelSeconds(srcZone, srcX, srcY, dstZone, dstX, dstY, player, opts)
    if not (Nav.IsAvailable() and srcZone and srcX and srcY and dstZone and dstX and dstY) then
        return nil, false
    end
    local nav = ns.NavData
    local _, groundScale, _, _, _, capId = BuildCapabilityContext(nav, player, opts)
    local _, _, sCell = Nav.CoordToCellXY(nav, srcX, srcY)
    local _, _, dCell = Nav.CoordToCellXY(nav, dstX, dstY)

    -- Fast O(1) continuous distance inside same walkable cell or unobstructed 2x2 box
    if srcZone == dstZone then
        local zc = nav.zoneCells and (nav.zoneCells[srcZone] or nav.zoneCells[tostring(srcZone)])
        local sComp = (zc and zc[sCell] and tonumber(zc[sCell][1])) or 0
        local dComp = (zc and zc[dCell] and tonumber(zc[dCell][1])) or 0
        if CheckLocalSameCellOrBox(nav, srcZone, srcX, srcY, dstZone, dstX, dstY, sCell, dCell, sComp, dComp) then
            local uSpeed = nav.unmountedYdsPerSec or DEFAULT_UNMOUNTED_YDS
            local speed = uSpeed / ((groundScale and groundScale > 0) and groundScale or 1.0)
            local yards = Nav.ZoneCoordsToYards(nav, srcZone, srcX, srcY, dstX, dstY)
            return math.max(1.0, yards / speed), true
        end
    end

    -- Fast path: if origin matches valid single-source outward Dijkstra field (full or settledBoundSec), check anchors in O(1)
    if singleCache.valid
        and srcZone == singleCache.zoneId
        and sCell == singleCache.cellIdx
        and capId == singleCache.capId
        and (not singleCache.sameZoneOnly or dstZone == srcZone) then
        local role = opts and opts.role or "s"
        local qid = opts and opts.questId or nil
        local dCount = SeedQuestOrCellAnchors(nav, dstZone, dstX, dstY, qid, role, dstAnchorNodes, dstAnchorSecs, dstAnchorComps)
        if dCount > 0 then
            local bound = singleCache.settledBoundSec
            local best = INF_SEC
            local st = singleCache.stamp
            for j = 1, dCount do
                local nid = dstAnchorNodes[j]
                if sStamp[nid] == st and sDist[nid] < bound then
                    local tot = sDist[nid] + dstAnchorSecs[j] * groundScale
                    if tot < best then
                        best = tot
                    end
                end
            end
            if best < bound then
                return best, true
            end
        end
        if singleCache.fullField then
            return nil, false
        end
    end

    -- Direct O(1) PATH_CACHE check before invoking QueryPath anchor resolution
    local role = opts and opts.role or "s"
    local qNum = (opts and tonumber(opts.questId)) or 0
    local rCode = ROLE_TO_CODE[role] or 1
    local srcKey = ((capId * 32768 + srcZone) * 512) + sCell
    local dstKey
    if qNum > 0 and qNum < 2097152 then
        dstKey = ((((dstZone * 512 + dCell) * 4 + rCode) * 2097152) + qNum)
    elseif qNum <= 0 then
        dstKey = -((dstZone * 512 + dCell) * 4 + rCode)
    else
        dstKey = nil
    end
    local hitBucket = (dstKey and not (opts and opts.noCache)) and srcBucketByKey[srcKey] or nil
    local hit = hitBucket and hitBucket.map[dstKey] or nil
    if hit then
        if hit.reachable then
            return hit.totalSec, true
        end
        return nil, false
    end

    local res = Nav.QueryPath(srcZone, srcX, srcY, dstZone, dstX, dstY, player, opts)
    if res and res.reachable then
        return res.totalSec, true
    end
    return nil, false
end

function Nav.ResolveStepHop(player, step, opts)
    if not (player and step and player.zoneId and player.x and player.y and step.zoneId and step.x and step.y) then
        return nil
    end
    local role = (step.action == "TURN_IN") and "t" or ((step.action == "COMPLETE") and "o" or "s")
    local qOpts = opts and {} or nil
    if qOpts then
        for k, v in pairs(opts) do qOpts[k] = v end
    else
        if not Nav._hopOptsScratch then Nav._hopOptsScratch = {} end
        qOpts = Nav._hopOptsScratch
    end
    qOpts.questId = step.questId
    qOpts.role = role

    local res = Nav.QueryPath(player.zoneId, player.x, player.y, step.zoneId, step.x, step.y, player, qOpts)
    if not (res and res.reachable) then
        return nil
    end
    return res
end

--------------------------------------------------------------------------------
-- Lazy Reverse Eligible Quest Index (Rebuilt Strictly on questStateGeneration)
--------------------------------------------------------------------------------

local function AppendNodeEligible(nodeId, qid, roleCode, walkSec, zoneId, x, y)
    local e = eligEdgeCount + 1
    eligEdgeCount = e
    local nStamp = nodeStampVal
    eligQid[e] = qid
    eligRole[e] = roleCode
    eligWalk[e] = walkSec
    eligZone[e] = zoneId
    eligX[e] = x
    eligY[e] = y
    eligNext[e] = (nodeHeadStamp[nodeId] == nStamp) and nodeHead[nodeId] or 0
    nodeHead[nodeId] = e
    nodeHeadStamp[nodeId] = nStamp
end

local function ResolveQuestCoords(q, roleCode)
    if roleCode == "t" then
        local pt = q.turnin or q.starter
        if pt and pt.zoneId and pt.x and pt.y then
            return pt.zoneId, pt.x, pt.y
        end
    elseif roleCode == "o" then
        local obj = q.objectives and q.objectives[1]
        if obj and obj.zoneId and obj.x and obj.y then
            return obj.zoneId, obj.x, obj.y
        end
    end
    local pt = q.starter or q.turnin
    if pt and pt.zoneId and pt.x and pt.y then
        return pt.zoneId, pt.x, pt.y
    end
    return q.zoneId, nil, nil
end

local function RegisterQuestToIndex(nav, questAnchors, qid, q, roleCode, classFile, pCount, uCount, addedCount)
    local qa = questAnchors[qid] or questAnchors[tostring(qid)]
    local list = qa and (qa[roleCode] or (roleCode ~= "s" and qa.s))
    local zId, qx, qy = ResolveQuestCoords(q, roleCode)
    if type(list) == "table" and list[1] and list[2] then
        local pos = 1
        local reg = 0
        while list[pos] and list[pos + 1] and reg < MAX_END_ANCHORS do
            AppendNodeEligible(list[pos], qid, roleCode, tonumber(list[pos + 1]) or 0, zId, qx, qy)
            reg = reg + 1
            pos = pos + 2
        end
        addedCount = addedCount + 1
    elseif zId and qx and qy then
        local ac = Nav.ResolveCellAnchors(nav, zId, qx, qy, dstAnchorNodes, dstAnchorSecs, dstAnchorComps)
        if ac > 0 then
            for i = 1, ac do
                AppendNodeEligible(dstAnchorNodes[i], qid, roleCode, dstAnchorSecs[i], zId, qx, qy)
            end
            addedCount = addedCount + 1
        else
            uCount = uCount + 1
            unanchoredQids[uCount] = qid
        end
    else
        uCount = uCount + 1
        unanchoredQids[uCount] = qid
    end

    local r = q.rewards
    local cb = r and r.classScores and r.classScores[classFile] or 0
    local rScore = tonumber(q.features and q.features.rogueScore) or 0
    if q.dungeonId
        or (r and r.bagSlots and r.bagSlots > 0)
        or cb >= 50
        or (classFile == "ROGUE" and rScore >= 50) then
        pCount = pCount + 1
        priorityQids[pCount] = qid
    end
    return pCount, uCount, addedCount
end

local function EnsureCharStaticIndex(nav, data, player, factionMask, raceId, classId, raceBitmask, classBitmask, classFile)
    if charStaticMeta.valid
        and charStaticMeta.data == data
        and charStaticMeta.nav == nav
        and charStaticMeta.factionMask == factionMask
        and charStaticMeta.raceId == raceId
        and charStaticMeta.classId == classId
        and charStaticMeta.raceBitmask == raceBitmask
        and charStaticMeta.classBitmask == classBitmask
        and charStaticMeta.classFile == classFile then
        return
    end

    for lv = 0, MAX_STATIC_MIN_LEVEL do
        staticLevelCounts[lv] = 0
    end

    local State = ns.State
    local isEligFn = State and State.IsEligibleForPlayer
    for qid, q in pairs(data.quests) do
        local charOk
        if isEligFn then
            charOk = isEligFn(qid, player)
        else
            local qSide = q.side or (q.faction and FACTION_TO_SIDE[q.faction]) or q.factionMask
            charOk = (not qSide or qSide == 3 or qSide == factionMask)
        end
        if charOk then
            local lv = tonumber(q.minLevel) or 1
            if lv < 0 then
                lv = 0
            elseif lv > MAX_STATIC_MIN_LEVEL then
                lv = MAX_STATIC_MIN_LEVEL
            end
            local c = staticLevelCounts[lv] + 1
            staticLevelCounts[lv] = c
            staticLevelBuckets[lv][c] = qid
        end
    end

    charStaticMeta.valid = true
    charStaticMeta.data = data
    charStaticMeta.nav = nav
    charStaticMeta.factionMask = factionMask
    charStaticMeta.raceId = raceId
    charStaticMeta.classId = classId
    charStaticMeta.raceBitmask = raceBitmask
    charStaticMeta.classBitmask = classBitmask
    charStaticMeta.classFile = classFile
end

function Nav.EnsureEligibleIndex(data, player)
    if not Nav.IsAvailable() or not data or not data.quests then
        return 0
    end
    local nav = ns.NavData
    EnsureStaticLookups(nav)

    local State = ns.State
    local stateGen = (State and State.GetQuestStateGeneration and State.GetQuestStateGeneration()) or 0
    local pLevel = tonumber(player and player.level) or 24
    local maxOffset = (State and State.GetEffectiveMaxLevelOffset and State.GetEffectiveMaxLevelOffset()) or 2
    local facStr = (player and player.faction) or "Alliance"
    local factionMask = (facStr == "Horde") and 2 or 1
    local raceId = tonumber(player and player.raceId) or 0
    local classId = tonumber(player and player.classId) or 0
    local raceBitmask = tonumber(player and player.raceBitmask) or 0
    local classBitmask = tonumber(player and player.classBitmask) or 0
    local classFile = tostring((player and player.classFile) or "ROGUE")
    local profile = tostring((ns.charDB and ns.charDB.activeProfile) or "balanced")
    local toggles = (ns.charDB and ns.charDB.toggles) or {}
    local skipEliteNow = (ns.State and ns.State.ShouldSkipEliteNow) and ns.State.ShouldSkipEliteNow(player, toggles) or (toggles.skipEliteSolo ~= false and not (player and player.inGroup))
    local skipDungeonsNow = (ns.State and ns.State.ShouldSkipDungeonsNow) and ns.State.ShouldSkipDungeonsNow(toggles) or (toggles.skipDungeons ~= false)
    local togglesMask = ((toggles.includeDungeons ~= false) and 1 or 0)
        + ((toggles.consultQuestie ~= false) and 2 or 0)
        + ((toggles.includeSkinning ~= false) and 4 or 0)
        + ((toggles.includeGoldBags ~= false) and 8 or 0)
        + (skipEliteNow and 16 or 0)
        + (skipDungeonsNow and 32 or 0)

    if eligibleMeta.valid
        and eligibleMeta.stateGen == stateGen
        and eligibleMeta.level == pLevel
        and eligibleMeta.maxOffset == maxOffset
        and eligibleMeta.factionMask == factionMask
        and eligibleMeta.raceId == raceId
        and eligibleMeta.classId == classId
        and eligibleMeta.raceBitmask == raceBitmask
        and eligibleMeta.classBitmask == classBitmask
        and eligibleMeta.classFile == classFile
        and eligibleMeta.profile == profile
        and eligibleMeta.togglesMask == togglesMask then
        return eligibleMeta.count
    end

    EnsureCharStaticIndex(nav, data, player, factionMask, raceId, classId, raceBitmask, classBitmask, classFile)

    -- O(1) stamp wipe of flat linked-list buckets (zero table allocation)
    eligEdgeCount = 0
    nodeStampVal = nodeStampVal + 1
    if nodeStampVal >= MAX_STAMP_VALUE then
        for k in pairs(nodeHeadStamp) do nodeHeadStamp[k] = 0 end
        nodeStampVal = 1
    end
    local pCount = 0
    local uCount = 0
    local addedCount = 0

    local pMaxLevel = pLevel + maxOffset
    local completedCheck = State and State.IsQuestCompleted
    local fastNavBlocked = State and State.IsQuestBlockedForNav
    local blockCheck = State and State.GetQuestBlockReason
    local prof = (player and player.professions) or (State and State.DetectProfessions and State.DetectProfessions()) or nil
    local activeMap = (State and State.GetActiveQuests and State.GetActiveQuests()) or {}
    local questAnchors = nav.questAnchors or {}
    local questsMap = data.quests

    activeStampVal = activeStampVal + 1
    if activeStampVal >= MAX_STAMP_VALUE then
        for k in pairs(activeSeenStamp) do activeSeenStamp[k] = 0 end
        activeStampVal = 1
    end
    local aStamp = activeStampVal

    eligStampVal = eligStampVal + 1
    if eligStampVal >= MAX_STAMP_VALUE then
        for k in pairs(eligQidStamp) do eligQidStamp[k] = 0 end
        eligStampVal = 1
    end
    local curEligStamp = eligStampVal

    -- 1. Active & Ready Turn-In quests first (usually <= 25 entries)
    for qid, aEntry in pairs(activeMap) do
        activeSeenStamp[qid] = aStamp
        local q = questsMap[qid]
        if q and (not aEntry.isFailed) and not (completedCheck and completedCheck(qid, true)) then
            if aEntry.isComplete == true then
                pCount, uCount, addedCount = RegisterQuestToIndex(nav, questAnchors, qid, q, "t", classFile, pCount, uCount, addedCount)
            elseif not (skipEliteNow and State and State.IsQuestElite and State.IsQuestElite(qid, q))
                and not (skipDungeonsNow and (q.isDungeon == true or (State and State.IsQuestDungeon and State.IsQuestDungeon(qid, q)))) then
                pCount, uCount, addedCount = RegisterQuestToIndex(nav, questAnchors, qid, q, "o", classFile, pCount, uCount, addedCount)
            end
        end
    end

    -- 2. Scan zero-GC minLevel buckets up to pLevel
    local lvCap = math.min(MAX_STATIC_MIN_LEVEL, pLevel)
    for lv = 0, lvCap do
        local bucket = staticLevelBuckets[lv]
        local bCount = staticLevelCounts[lv]
        for i = 1, bCount do
            local qid = bucket[i]
            if activeSeenStamp[qid] ~= aStamp and not (completedCheck and completedCheck(qid, true)) then
                local q = questsMap[qid]
                local qLv = q and tonumber(q.questLevel) or 0
                if q and (qLv == 0 or qLv <= pMaxLevel) then
                    local blocked
                    if fastNavBlocked then
                        blocked = fastNavBlocked(qid, q, prof, skipEliteNow, skipDungeonsNow)
                    else
                        blocked = (blockCheck and blockCheck(qid, q.isDungeon and "DUNGEON_PREP" or "ACCEPT", player, nil, nil, toggles, FAST_NAV_BLOCK_OPTS)) ~= nil
                    end
                    if not blocked then
                        eligQidStamp[qid] = curEligStamp
                        pCount, uCount, addedCount = RegisterQuestToIndex(nav, questAnchors, qid, q, "s", classFile, pCount, uCount, addedCount)
                    end
                end
            end
        end
    end

    for i = pCount + 1, #priorityQids do priorityQids[i] = nil end
    for i = uCount + 1, #unanchoredQids do unanchoredQids[i] = nil end

    eligibleMeta.valid = true
    eligibleMeta.version = eligibleMeta.version + 1
    eligibleMeta.stateGen = stateGen
    eligibleMeta.level = pLevel
    eligibleMeta.maxOffset = maxOffset
    eligibleMeta.factionMask = factionMask
    eligibleMeta.raceId = raceId
    eligibleMeta.classId = classId
    eligibleMeta.raceBitmask = raceBitmask
    eligibleMeta.classBitmask = classBitmask
    eligibleMeta.classFile = classFile
    eligibleMeta.profile = profile
    eligibleMeta.togglesMask = togglesMask
    eligibleMeta.count = addedCount
    nearestCache.valid = false
    return addedCount
end

function Nav.IsIndexedEligible(qid)
    return eligibleMeta.valid and qid ~= nil and eligQidStamp[qid] == eligStampVal
end

function Nav.GetPriorityAndUnanchoredQids()
    return priorityQids, unanchoredQids
end

local function RunSingleSourceFlood(nav, srcZone, srcX, srcY, player, opts, collectEligible)
    local factionMask, groundScale, hearthNodeId, knownFps, hasKnownFps, capId = BuildCapabilityContext(nav, player, opts)
    local sCount, sCellIdx = Nav.ResolveCellAnchors(nav, srcZone, srcX, srcY, srcAnchorNodes, srcAnchorSecs, srcAnchorComps)
    if sCount == 0 then
        singleCache.valid = false
        singleCache.fullField = false
        if collectEligible then
            nearestCache.valid = false
        end
        return 0
    end

    local keepFullField = (not collectEligible) or (opts and opts.keepFullField == true) or false
    local maxSec = (opts and tonumber(opts.maxSec)) or (keepFullField and INF_SEC or 2400.0)
    local maxResults = (opts and tonumber(opts.maxResults)) or 64
    local reqNoCap = (opts and opts.noCap == true) or false
    local collectTarget = reqNoCap and 999999 or (maxResults + 24)
    local roleFilter = opts and opts.roleFilter or nil
    local sameZoneOnly = (opts and opts.sameZoneOnly == true) or false

    local st = NextSingleStamp()
    sHeapSize = 0
    for i = 1, sCount do
        local u = srcAnchorNodes[i]
        local d = srcAnchorSecs[i] * groundScale
        if sStamp[u] ~= st or d < sDist[u] then
            sStamp[u] = st
            sDist[u] = d
            sHeapSize = HeapPush(sHeapNode, sHeapDist, sHeapSize, u, d)
        end
    end
    if hearthNodeId > 0 and not (opts and opts.allowHearth == false) then
        if sStamp[hearthNodeId] ~= st or HEARTH_CAST_SEC < sDist[hearthNodeId] then
            sStamp[hearthNodeId] = st
            sDist[hearthNodeId] = HEARTH_CAST_SEC
            sHeapSize = HeapPush(sHeapNode, sHeapDist, sHeapSize, hearthNodeId, HEARTH_CAST_SEC)
        end
    end

    local qStamp = 0
    if collectEligible then
        qSearchStamp = qSearchStamp + 1
        if qSearchStamp >= MAX_STAMP_VALUE then
            for k in pairs(seenQidStamp) do seenQidStamp[k] = 0 end
            qSearchStamp = 1
        end
        qStamp = qSearchStamp
    end

    nearestCount = 0
    local cutoffTriggerSec = INF_SEC
    local collectStopped = false
    local fHead = nav.fHead
    local fTo = nav.fTo
    local nodeZone = nav.nodeZone
    local curNodeStamp = nodeStampVal

    local settledBoundSec = INF_SEC
    while sHeapSize > 0 do
        local u, d
        sHeapSize, u, d = HeapPop(sHeapNode, sHeapDist, sHeapSize)
        if d <= sDist[u] then
            if not keepFullField then
                if d > maxSec then
                    settledBoundSec = d
                    break
                end
                if collectEligible and nearestCount >= collectTarget and d > cutoffTriggerSec then
                    settledBoundSec = d
                    break
                end
            elseif collectEligible and (not collectStopped) and nearestCount >= collectTarget and d > cutoffTriggerSec then
                collectStopped = true
            end

            if collectEligible and not collectStopped then
                local e = (nodeHeadStamp[u] == curNodeStamp) and nodeHead[u] or 0
                while e ~= 0 do
                    local qid = eligQid[e]
                    local rCode = eligRole[e]
                    local zId = eligZone[e]
                    if (not roleFilter or rCode == roleFilter)
                        and (not sameZoneOnly or zId == srcZone)
                        and seenQidStamp[qid] ~= qStamp then
                        local tSec = d + (eligWalk[e] or 0) * groundScale
                        if tSec <= maxSec or keepFullField then
                            seenQidStamp[qid] = qStamp
                            nearestCount = nearestCount + 1
                            local rec = nearestResultsPool[nearestCount]
                            if not rec then
                                rec = {}
                                nearestResultsPool[nearestCount] = rec
                            end
                            rec.qid = qid
                            rec.role = rCode
                            rec.travelSec = tSec
                            rec.zoneId = zId
                            rec.x = eligX[e]
                            rec.y = eligY[e]
                            rec.nodeId = u
                            if nearestCount == collectTarget then
                                cutoffTriggerSec = d + 60.0
                            end
                        end
                    end
                    e = eligNext[e]
                end
            end

            local startE = fHead[u]
            local endE = fHead[u + 1] - 1
            for e = startE, endE do
                local w = GetEdgeCost(nav, e, factionMask, groundScale, knownFps, hasKnownFps)
                if w then
                    local v = fTo[e]
                    if not (sameZoneOnly and nodeZone[v] ~= srcZone) then
                        local nd = d + w
                        if (keepFullField or nd <= maxSec) and (sStamp[v] ~= st or nd < sDist[v]) then
                            sStamp[v] = st
                            sDist[v] = nd
                            sHeapSize = HeapPush(sHeapNode, sHeapDist, sHeapSize, v, nd)
                        end
                    end
                end
            end
        end
    end

    if collectEligible then
        -- Zero-GC insertion sort on [1..nearestCount] by travelSec
        for i = 2, nearestCount do
            local keyRec = nearestResultsPool[i]
            local keySec = keyRec.travelSec
            local j = i - 1
            while j >= 1 and nearestResultsPool[j].travelSec > keySec do
                nearestResultsPool[j + 1] = nearestResultsPool[j]
                j = j - 1
            end
            nearestResultsPool[j + 1] = keyRec
        end
    end

    singleCache.valid = true
    singleCache.zoneId = srcZone
    singleCache.cellIdx = sCellIdx
    singleCache.capId = capId
    singleCache.stamp = st
    singleCache.settledBoundSec = settledBoundSec
    singleCache.fullField = (settledBoundSec >= INF_SEC)
    singleCache.sameZoneOnly = sameZoneOnly

    if not collectEligible then
        return 0
    end
    local outCount = (not reqNoCap and (not keepFullField or (opts and tonumber(opts.maxResults))))
        and math.min(nearestCount, maxResults) or nearestCount
    nearestCache.valid = true
    nearestCache.eligVersion = eligibleMeta.version
    nearestCache.zoneId = srcZone
    nearestCache.cellIdx = sCellIdx
    nearestCache.capId = capId
    nearestCache.sameZoneOnly = sameZoneOnly
    nearestCache.roleFilter = roleFilter
    nearestCache.maxSec = maxSec
    nearestCache.maxResults = maxResults
    nearestCache.keepFullField = keepFullField
    nearestCache.noCap = reqNoCap
    nearestCache.outCount = outCount
    return outCount
end

-- Runs single-source outward forward Dijkstra from (srcZone, srcX, srcY), populating
-- nearestResultsPool with up to opts.maxResults eligible quests sorted by true graph
-- travelSec, stopping cleanly once heapMin > maxSec (when opts.keepFullField is false).
function Nav.FindNearestEligibleQuests(srcZone, srcX, srcY, data, player, opts)
    navStats.nearestCalls = navStats.nearestCalls + 1
    if not Nav.IsAvailable() or not (srcZone and srcX and srcY) then
        return nil, 0
    end
    local nav = ns.NavData
    EnsureStaticLookups(nav)
    if not (opts and opts.skipIndexRebuild == true and eligibleMeta.valid) then
        Nav.EnsureEligibleIndex(data, player)
    end

    local _, _, sCellIdx = Nav.CoordToCellXY(nav, srcX, srcY)
    local _, _, _, _, _, capId = BuildCapabilityContext(nav, player, opts)
    local reqKeepFull = (opts and opts.keepFullField == true) or false
    local reqMaxSec = (opts and tonumber(opts.maxSec)) or (reqKeepFull and INF_SEC or 2400.0)
    local reqMaxResults = (opts and tonumber(opts.maxResults)) or 64
    local reqNoCap = (opts and opts.noCap == true) or false
    local reqSameZone = (opts and opts.sameZoneOnly == true) or false
    local reqRole = opts and opts.roleFilter or nil

    if nearestCache.valid
        and nearestCache.eligVersion == eligibleMeta.version
        and nearestCache.zoneId == srcZone
        and nearestCache.cellIdx == sCellIdx
        and nearestCache.capId == capId
        and nearestCache.sameZoneOnly == reqSameZone
        and nearestCache.roleFilter == reqRole
        and nearestCache.maxSec == reqMaxSec
        and nearestCache.maxResults == reqMaxResults
        and nearestCache.keepFullField == reqKeepFull
        and nearestCache.noCap == reqNoCap
        and (not reqKeepFull or (singleCache.valid and singleCache.fullField and singleCache.zoneId == srcZone and singleCache.cellIdx == sCellIdx and singleCache.capId == capId and (not singleCache.sameZoneOnly or reqSameZone))) then
        return nearestResultsPool, nearestCache.outCount
    end

    navStats.floodSolves = navStats.floodSolves + 1
    local outCount = RunSingleSourceFlood(nav, srcZone, srcX, srcY, player, opts, true)
    if outCount == 0 and singleCache.valid == false and sHeapSize == 0 and nearestCount == 0 then
        return nil, 0
    end
    return nearestResultsPool, outCount
end

function Nav.EnsureSingleSourceField(srcZone, srcX, srcY, data, player, opts)
    navStats.singleFieldCalls = navStats.singleFieldCalls + 1
    if not Nav.IsAvailable() or not (srcZone and srcX and srcY) then
        return false
    end
    local nav = ns.NavData
    EnsureStaticLookups(nav)
    local _, _, sCellIdx = Nav.CoordToCellXY(nav, srcX, srcY)
    local _, _, _, _, _, capId = BuildCapabilityContext(nav, player, opts)
    local reqSameZoneOnly = (opts and opts.sameZoneOnly == true) or false
    local reqMaxSec = (opts and tonumber(opts.maxSec)) or INF_SEC
    if singleCache.valid
        and (singleCache.fullField or singleCache.settledBoundSec >= reqMaxSec)
        and singleCache.zoneId == srcZone
        and singleCache.cellIdx == sCellIdx
        and singleCache.capId == capId
        and (not singleCache.sameZoneOnly or reqSameZoneOnly) then
        return true
    end
    navStats.floodSolves = navStats.floodSolves + 1
    RunSingleSourceFlood(nav, srcZone, srcX, srcY, player, opts, false)
    return singleCache.valid
end

function Nav.GetStats()
    return navStats
end

function Nav.ResetStats()
    navStats.queryCalls = 0
    navStats.bidirSolves = 0
    navStats.singleFieldCalls = 0
    navStats.nearestCalls = 0
    navStats.floodSolves = 0
end

function Nav.Reset()
    charStaticMeta.valid = false
    eligibleMeta.valid = false
    nearestCache.valid = false
    singleCache.valid = false
    ClearPathCache()
end
