--------------------------------------------------------------------------------
-- State.lua
-- Rules:
--   1. Live Character & Quest State Engine for ForeverQuestPath.
--   2. Reads live or overridden character identity (faction, race, class, level),
--      zone/coordinates (with UnitOnTaxi and dead/ghost location freeze via
--      IsPlayerLocationFrozen, lastAliveLocation, and deadBootLocation across
--      /reload while already dead + resurrection resume), active quest log,
--      learned professions (Skinning 393 / Leatherworking 165), and completed
--      quest flags via zero-clone GetCompletedReadOnly() and incremental
--      completedBulk/completedMemo updates (including O(1) QUEST_TURNED_IN).
--   3. Selects optimal precomputed routes (1-60 primary, per-race starterRoutes,
--      and hordeRoutes) with cross-zone outlevel catch-up, fresh zone-aligned
--      entry, O(1)-first IsStepDirectBlocked filtering of orphaned COMPLETE/TURN_IN
--      steps, fast CURSOR_BLOCK_OPTS cursor advancement with deferred/blocked
--      cleanup on rewind, O(1) lazy blockedStepsLog compaction (preserving
--      step metadata for UI hover/click), smart ready-
--      turn-in quest log detection (with PROFILE_CHANGED bulk completion sync,
--      readySigGen-memoized GetReadyTurnInSignature / GetRouteReadySignature,
--      and flat zero-subtable existingTurninIdx/existingTurninSingle tracking),
--      readySigGen-memoized GetReadyTurnInQuests/GetReadyTurnInCount, reusable
--      emptyReadyPlan, and travel-optimal batch injection via pending-zone-
--      bounded perfScratch.readyRouteScanCache & multi-cursor
--      perfScratch.readyScanCtx (preserving rs.lastPlan across immCount > 1 at
--      identical coordinates, inline single-quest, TRAVEL lookahead,
--      DUNGEON_PREP/exclusiveTo, and State.GetAcceptStepIndexMap checks),
--      shared pending/hubEdges, perfScratch.readyStaticCache + cached
--      BuildInjectedTurninStep, hoisted zero-FNEW ready helpers, continent &
--      RouteDistCoord travel-aware perfScratch.SortReadyZones, 35-bit
--      perfScratch.readyStepDist step-to-ready distance caching, and 50/42-bit
--      RouteDistCoord packing for Forever IDs; near-realtime GPS walking reorder
--      with hysteresis (lazy single-source nav field priming, multi-cursor
--      perfScratch.gpsBaseCtx, region-keyed gbw optimal-path available quest
--      discovery via perfScratch.InjectOptimalPathAvailableQuests /
--      MinPathProximity, per-ws solved planByOff + allOpenOff clamping,
--      same-hub / on-corridor ACCEPT prioritization over distant
--      recent-accepted objectives via AdjustUnlockedAcceptBoosts /
--      ShouldAcceptOverrideSticky,
--      recentAcceptedSeq-memoized GetLocationPlanSignature & offDbLocalSig,
--      cursor-independent perfScratch.gpsDist pairMap+playerMap keyed by
--      distId, LuaJIT table.clear-backed WipeMap, scratch maps/arrays, hoisted
--      zero-FNEW chain/watcher helpers, pooled gpsUnlockedPool, bounded
--      reorderedStepCache, pure-Lua linear min+sticky selection without
--      table.sort callbacks, staticDist in RouteDistCoord, O(1) single-quest
--      fast paths, complete inline single-quest DAG check, and prereqWatchers/
--      exclWatchers-targeted invalidation), automatic in-dungeon quest routing
--      via ResolveActiveDungeon / State.GetActiveDungeonPlan (prioritizing
--      that dungeon's ready in-dungeon turn-ins, available in-dungeon pickups,
--      active incomplete objectives, follow-up chains, post-run outdoor
--      turn-ins, and shareable dungeon quests when zoned inside an instance,
--      overriding skipDungeons/skipEliteSolo/maxLevelOffset while preserving
--      instance context across dead/ghost wipe corpse runs), proactive
--      full-chain expansion & zero-FNEW iterative chain collection with monotonic
--      [-3..maxValidOff] negative-span seeding, [minOff, maxOff] span-seeded
--      decCache.byMaxOff + playerRef fast-path + _rawChainStep-canonical
--      decCache.byStepBadge step clone reuse and epoch/offset-span stamped
--      zero-alloc dynamic step decoration, zero-alloc friend-meetup off-route
--      chain pivoting with scratch candidate selection & fPairDist caching,
--      pInfo/playerHint forwarding into GetCurrentStep + STATE_UPDATED +
--      in-place cache wrapper reuse, O(1) GetBlockedStepCount(), class-quest
--      injection, deviation/skip handling, step history, and Nav routing.
--   4. Enforces strict live eligibility guards (factionMask, raceMask, classMask,
--      memoized IsEligibleForPlayer, minLevel, prereqIds, configurable
--      questLevel <= playerLevel + maxLevelOffset cap defaulting to +2 with
--      -3..+5 slider, prevNonRedMaxLevelOffset memory across red-quest toggles,
--      and auto-unlock deferral, open-world elite gating when toggles.skipEliteSolo
--      is enabled and player is solo via IsQuestElite / ShouldSkipEliteNow
--      [never blocking already-ready turn-ins or dungeon quests], dungeon quest
--      deferral when toggles.skipDungeons is enabled via IsQuestDungeon /
--      ShouldSkipDungeonsNow ("dungeon_skipped", recorded in deferredSteps while
--      preserving completed ready turn-ins and outdoor unlock chains), requiredSkill
--      gating, and optional QuestieDB checks) using two-stage maxOffset-independent
--      questBaseMemo / questPostMemo, bulkSynced-aware GetCompletedReadOnly(),
--      zero weak-key ephemeron tables, dbQuestStateGeneration + chain/block
--      preservation across MAX_LEVEL_CHANGED and off-DB quest events, bounded
--      stepKeyCache and acceptIdxByStepsCache for O(1) IsAcceptSkippedEarlier
--      lookups, dagState memoization in BuildIntraZoneReorderPlan, and
--      step/player caches.
--   5. Eliminates idle and loot/skinning CPU work: fingerprints active quest
--      IDs, isComplete/isFailed flags, player level, profession skill ranks, and
--      live solo/group status (HasGroupStatusChanged) so roster noise refreshes
--      only when solo/grouped flips; keeps route state and caches fast.
--      zone transitions without calling GetAllCompletedQuestIDs or GetQuestObjectives
--      on routine QUEST_LOG_UPDATE bursts (suppressing normal loot and partial
--      1/10..9/10 objective drops cleanly), freezes location/GPS/zone-catchup/
--      TRAVEL/reordering while dead or ghost via lastAliveLocation, deadBootLocation,
--      and deadLocationSkipped gating (resuming cleanly on OnPlayerResurrected),
--      and gates GPS polling via zero-alloc raw early-exit, indoor nil
--      suppression, real-yard + 20x20 Nav cell thresholds, and movement cooldowns.
--------------------------------------------------------------------------------

local _, ns = ...

local State = {}
ns.State = State

-- Fallback mapping from Wowhead/Forever AreaID -> Classic/Forever UiMapID
local AREA_TO_UIMAP = {
    [1]     = 1426, -- Dun Morogh
    [3]     = 1418, -- Badlands
    [4]     = 1419, -- Blasted Lands
    [8]     = 1435, -- Swamp of Sorrows
    [10]    = 1431, -- Duskwood
    [11]    = 1437, -- Wetlands
    [12]    = 1429, -- Elwynn Forest
    [14]    = 1411, -- Durotar
    [15]    = 1445, -- Dustwallow Marsh
    [16]    = 1447, -- Azshara
    [17]    = 1413, -- The Barrens
    [28]    = 1422, -- Western Plaguelands
    [33]    = 1434, -- Stranglethorn Vale
    [36]    = 1416, -- Alterac Mountains
    [38]    = 1432, -- Loch Modan
    [40]    = 1436, -- Westfall
    [41]    = 1430, -- Deadwind Pass
    [44]    = 1433, -- Redridge Mountains
    [45]    = 1417, -- Arathi Highlands
    [46]    = 1428, -- Burning Steppes
    [47]    = 1425, -- The Hinterlands
    [51]    = 1427, -- Searing Gorge
    [85]    = 1420, -- Tirisfal Glades
    [130]   = 1421, -- Silverpine Forest
    [139]   = 1423, -- Eastern Plaguelands
    [141]   = 1438, -- Teldrassil
    [148]   = 1439, -- Darkshore
    [215]   = 1412, -- Mulgore
    [267]   = 1424, -- Hillsbrad Foothills
    [331]   = 1440, -- Ashenvale
    [357]   = 1444, -- Feralas
    [361]   = 1448, -- Felwood
    [400]   = 1441, -- Thousand Needles
    [405]   = 1443, -- Desolace
    [406]   = 1442, -- Stonetalon Mountains
    [440]   = 1446, -- Tanaris
    [490]   = 1449, -- Un'Goro Crater
    [493]   = 1450, -- Moonglade
    [618]   = 1452, -- Winterspring
    [1377]  = 1451, -- Silithus
    [1497]  = 1458, -- Undercity
    [1519]  = 1453, -- Stormwind City
    [1537]  = 1455, -- Ironforge
    [1637]  = 1454, -- Orgrimmar
    [1638]  = 1456, -- Thunder Bluff
    [1657]  = 1457, -- Darnassus
    [16591] = 2548, -- Riverglades (Forever)
    [16593] = 2521, -- Zephras Isle (Forever)
    [16732] = 1437, -- Excavation Site: Wetlands (entrance in Wetlands)
}

local FACTION_TO_SIDE = {
    Alliance = 1,
    Horde = 2,
    Neutral = 3,
    Both = 3,
}

local FALLBACK_RACES = {
    Human              = { id = 1,  key = "Human",              label = "Human",               faction = "Alliance", bitmask = 1,          startZoneId = 12,    allowedClasses = { "WARRIOR", "PALADIN", "ROGUE", "PRIEST", "MAGE", "WARLOCK" } },
    Orc                = { id = 2,  key = "Orc",                label = "Orc",                 faction = "Horde",    bitmask = 2,          startZoneId = 14,    allowedClasses = { "WARRIOR", "HUNTER", "ROGUE", "SHAMAN", "WARLOCK" } },
    Dwarf              = { id = 3,  key = "Dwarf",              label = "Dwarf",               faction = "Alliance", bitmask = 4,          startZoneId = 1,     allowedClasses = { "WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST" } },
    NightElf           = { id = 4,  key = "NightElf",           label = "Night Elf",           faction = "Alliance", bitmask = 8,          startZoneId = 141,   allowedClasses = { "WARRIOR", "HUNTER", "ROGUE", "PRIEST", "DRUID" } },
    Scourge            = { id = 5,  key = "Scourge",            label = "Undead",              faction = "Horde",    bitmask = 16,         startZoneId = 85,    allowedClasses = { "WARRIOR", "ROGUE", "PRIEST", "MAGE", "WARLOCK" } },
    Tauren             = { id = 6,  key = "Tauren",             label = "Tauren",              faction = "Horde",    bitmask = 32,         startZoneId = 215,   allowedClasses = { "WARRIOR", "HUNTER", "SHAMAN", "DRUID" } },
    Gnome              = { id = 7,  key = "Gnome",              label = "Gnome",               faction = "Alliance", bitmask = 64,         startZoneId = 1,     allowedClasses = { "WARRIOR", "ROGUE", "MAGE", "WARLOCK" } },
    Troll              = { id = 8,  key = "Troll",              label = "Troll",               faction = "Horde",    bitmask = 128,        startZoneId = 14,    allowedClasses = { "WARRIOR", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE" } },
    HighOrderSkyborne  = { id = 95, key = "HighOrderSkyborne",  label = "High Order Skyborne", faction = "Alliance", bitmask = 4294967296, startZoneId = 16593 },
    WindshaperSkyborne = { id = 96, key = "WindshaperSkyborne", label = "Windshaper Skyborne", faction = "Horde",    bitmask = 8589934592, startZoneId = 16593 },
}

local FALLBACK_CLASSES = {
    WARRIOR = { id = 1,  key = "WARRIOR", label = "Warrior", bitmask = 1    },
    PALADIN = { id = 2,  key = "PALADIN", label = "Paladin", bitmask = 2    },
    HUNTER  = { id = 3,  key = "HUNTER",  label = "Hunter",  bitmask = 4    },
    ROGUE   = { id = 4,  key = "ROGUE",   label = "Rogue",   bitmask = 8    },
    PRIEST  = { id = 5,  key = "PRIEST",  label = "Priest",  bitmask = 16   },
    SHAMAN  = { id = 7,  key = "SHAMAN",  label = "Shaman",  bitmask = 64   },
    MAGE    = { id = 8,  key = "MAGE",    label = "Mage",    bitmask = 128  },
    WARLOCK = { id = 9,  key = "WARLOCK", label = "Warlock", bitmask = 256  },
    DRUID   = { id = 11, key = "DRUID",   label = "Druid",   bitmask = 1024 },
}

local SKILL_LEATHERWORKING = 165
local SKILL_SKINNING = 393
local SECONDARY_SKILLS = {
    [129] = "First Aid",
    [185] = "Cooking",
    [356] = "Fishing",
}
local SKILL_ID_NAMES = {
    [129] = "First Aid",
    [164] = "Blacksmithing",
    [165] = "Leatherworking",
    [171] = "Alchemy",
    [182] = "Herbalism",
    [185] = "Cooking",
    [186] = "Mining",
    [197] = "Tailoring",
    [202] = "Engineering",
    [333] = "Enchanting",
    [356] = "Fishing",
    [393] = "Skinning",
}
local SKILL_NAME_TO_ID = {}
for sid, sname in pairs(SKILL_ID_NAMES) do
    SKILL_NAME_TO_ID[string.lower(sname)] = sid
end

local playerCache = nil
local activeQuests = {}
local completedBulk = {}
local hasBulkSync = false
local completedMemo = {}
local questBaseMemo = nil
local questPostMemo = nil
local professionCache = nil
local sessionCompleted = {}
local sessionAccepted = {}
local levelOverride = nil
local refreshGeneration = 1

local indexesBuilt = false
local uiMapToZoneId = {}
local zoneNameToZoneId = {}
local questToDungeons = {}
local zoneQuestsIndex = {}

local MAX_PREREQ_DEPTH = 8
local MIN_MAX_LEVEL_OFFSET = -3
local MAX_MAX_LEVEL_OFFSET = 5
local DEFAULT_MAX_LEVEL_OFFSET = 2
local NEAR_READY_TURNIN_DIST = 22.0
local HUB_CLUSTER_DIST = 12.0
local READY_ROUTE_HORIZON = 40
local READY_ZONE_SCAN_CAP = 140
local GPS_MOVE_THRESHOLD_SQ = 25.0
local GPS_MOVE_YARDS_THRESHOLD = 95.0
local GPS_LARGE_MOVE_YARDS = 160.0
local GPS_UPDATE_COOLDOWN_SEC = 4.5
local GPS_HYSTERESIS_SAVING = 8.0
local GPS_REORDER_WINDOW = 40
local GPS_FRIEND_NEAR_RADIUS = 32.0
local GPS_FRIEND_SCAN_CAP = 35
local GPS_CHAIN_FOLLOW_DEPTH = 8
local CHAIN_PROGRESS_MAX_DEPTH = 8
local CHAIN_PROGRESS_MAX_NODES = 12
local questStateGeneration = 0
local dbQuestStateGeneration = 0
local chainProgressCache = {}
local CAPITAL_ZONES = {
    [1497] = true, -- Undercity
    [1519] = true, -- Stormwind City
    [1537] = true, -- Ironforge
    [1637] = true, -- Orgrimmar
    [1638] = true, -- Thunder Bluff
    [1657] = true, -- Darnassus
}

local lastStepSignature = nil
local blockedStepsLog = {}
local blockedStepsByKey = {}
local blockedStepsDirty = false
local blockedStepsCount = 0
local turninResolveCache = {}
local readyPlanCache = nil
local readyHistory = {}
local locationPlanCache = nil
local gpsHistory = {}
local lastAliveLocation = nil
local deadBootLocation = nil
local lastTrackedZoneId = nil
local racesCacheDataRef = nil
local racesMergedCache = nil
local racesByIdCache = nil
local racesByCompactCache = nil
local racesTokenCache = {}
local classesCacheDataRef = nil
local classesMergedCache = nil
local classesByIdCache = nil
local classesByCompactCache = nil
local classesTokenCache = {}
local staticDist = {
    map = {},
    count = 0,
    epoch = -1,
    cap = -1,
    navRef = nil,
}
local ROLE_DIST_CODE = { s = 1, o = 2, t = 3 }
local perfScratch = {
    elig = { side = -1, cKey = -1, rKey = -1, questsRef = nil, qRefs = {}, vals = {} },
    chainVisited = {},
    chainSuffixQ = {},
    chainSuffixS = {},
    chainList = {},
    chainFwdSeen = {},
    chainStkId = {},
    chainStkDepth = {},
    chainChildren = {},
    cQuests = nil,
    cMinOff = -3,
    cMaxOff = 5,
    readyRouteScanCache = nil,
    readyStaticCache = nil,
    gpsBaseCtx = nil,
    gpsLiveStateGen = 0,
    gpsRpParts = {},
    gpsZoneWindowCache = nil,
    gpsUnlockedPool = {},
    gpsProactiveStepCache = {},
    gpsProactiveStepCacheGen = -1,
    reorderedStepCache = {},
    singleQidListCache = {},
    readySimComp = {},
    readySimAct = {},
    readySimBlk = {},
    readySigParts = {},
    readySigGen = 0,
    cachedReadySigGen = -1,
    cachedReadySig = "",
    cachedRouteReadySigGen = -1,
    cachedRouteReadySig = "",
    cachedReadyQuests = nil,
    cachedReadyQuestsGen = -1,
    cachedReadyQuestsQdb = nil,
    cachedReadyCount = 0,
    cachedReadyCountGen = -1,
    emptyReadyPlan = nil,
    tableClear = nil,
    locSigSeq = -1,
    locSigTarget = false,
    locSigVal = "",
    locSigParts = {},
    offDbSigReadyGen = -1,
    offDbSigRecSeq = -1,
    offDbSigZone = false,
    offDbSigVal = "",
    offDbSigParts = {},
    sigQids = {},
    sigParts = {},
    readyPendQids = {},
    readyPendZones = {},
    readyLookBlk = {},
    readyIsImm = {},
    readyImmItems = {},
    readyImmQueue = {},
    readyImmParts = {},
    readyScanCtx = nil,
    cqQid = {},
    cqDepth = {},
    cqRoot = {},
    cqAct = {},
    chainSeen = {},
    gpsDist = {
        stepsRef = nil,
        pZone = nil,
        navAvail = nil,
        faction = nil,
        mounted = nil,
        pairMap = {},
        pairCount = 0,
        playerMap = {},
        playerX = nil,
        playerY = nil,
    },
    gpsDirectSet = {},
    gpsChainSet = {},
    gpsChainExpand = {},
    gpsChainExpSet = {},
    gpsVComp = {},
    gpsVAct = {},
    gpsUnp1 = {},
    gpsUnp2 = {},
    gpsDagState = {},
    gpsDagVer = {},
    gpsPicked = {},
    gpsOrdered = {},
    gpsSortedOrig = {},
    reorderedCount = 0,
    stepKeyCount = 0,
    acceptIdxCount = 0,
    friendPts = {},
    friendKeys = {},
    friendHasObjBase = {},
    friendHasObjLive = {},
    friendPairDist = {},
    readyStepDist = { map = {}, stepsRef = nil, navAvail = nil, faction = nil, count = 0 },
    offDbEliteMemo = {},
    lastObservedInGroup = nil,
    optPathWpX = {},
    optPathWpY = {},
    optPathCands = {},
    optPathCosts = {},
    optPathReadyImm = {},
    instIdToDungeon = {
        [33]   = "shadowfang-keep",
        [34]   = "the-stockade",
        [36]   = "the-deadmines",
        [43]   = "wailing-caverns",
        [47]   = "razorfen-kraul",
        [48]   = "blackfathom-deeps",
        [70]   = "uldaman",
        [90]   = "gnomeregan",
        [109]  = "the-temple-of-atal-hakkar",
        [129]  = "razorfen-downs",
        [189]  = "scarlet-monastery",
        [209]  = "zul-farrak", -- DBC InstanceID 209 = Zul'Farrak (AreaID 209 = Shadowfang Keep)
        [229]  = "blackrock-spire",
        [230]  = "blackrock-depths",
        [289]  = "scholomance",
        [329]  = "stratholme",
        [349]  = "maraudon",
        [389]  = "ragefire-chasm",
        [429]  = "dire-maul",
        [2959] = "city-of-dalaran",
        [2970] = "the-drowned-city",
        [2974] = "blackmaw-hold",
        [2994] = "alcaz-prison",
        [2998] = "excavation-site-wetlands",
        [2999] = "ruins-of-lordaeron",
        [3001] = "the-shapers-terrace",
        [3039] = "krol-dok-stronghold",
        [3065] = "hall-of-thanes",
    },
    dungeonByName = {},
    dungeonByZoneId = {},
    dungeonPlanCache = nil,
    lastObservedInInstance = nil,
    lastObservedInstMapId = nil,
    lastObservedInstName = nil,
    inDungCandQids = {},
    inDungCandSeen = {},
    inDungExtraQids = {},
    inDungVComp = {},
    inDungVAct = {},
    inDungIncQids = {},
    inDungOutQids = {},
    readyZoneSameReg = {},
    readyZoneDist = {},
}
local decCache = {
    byMaxOff = {},
    byStepBadge = {},
    epoch = 1,
    gen = -1,
    playerRef = nil,
    lv = -1,
    race = -1,
    class = -1,
    faction = nil,
    skin = -2,
    lw = -2,
    questie = nil,
    chains = nil,
}
local currentStepCache = nil
local stepKeyCache = {}
local acceptIdxByStepsCache = {}
local lastTrackedX = nil
local lastTrackedY = nil
local lastRawInit = false
local lastRawUiMapId = nil
local lastRawGx = nil
local lastRawGy = nil
local lastGpsRefreshAt = nil
local activeLogInitialized = false
local activeQuestCount = 0
local sessionAcceptedPending = false
local trackedStepQids = {}
local trackedStepQidCount = 0
local lastObservedPlayerLevel = nil
local lastGpsTargetKey = nil
local gpsTickChecks = 0
local gpsTickTriggers = 0
local refreshCalls = 0
local refreshSkipped = 0
local refreshExecuted = 0
local deadLocationSkipped = 0
local recentAcceptedQuests = {}
local recentChainFollowups = {}
local recentAcceptedSeq = 0

do
    if type(require) == "function" then
        local okTc, tcFn = pcall(require, "table.clear")
        if okTc and type(tcFn) == "function" then
            perfScratch.tableClear = tcFn
        end
    end
end

local function WipeMap(t)
    local tc = perfScratch.tableClear
    if tc then
        tc(t)
        return t
    end
    for k in pairs(t) do
        t[k] = nil
    end
    return t
end

function perfScratch.CopyFlatMapInto(dst, src)
    WipeMap(dst)
    if src then
        for k, v in pairs(src) do
            dst[k] = v
        end
    end
    return dst
end

local function ClearBlockedStepsLog()
    for i = 1, #blockedStepsLog do
        blockedStepsLog[i] = nil
    end
    WipeMap(blockedStepsByKey)
    blockedStepsDirty = false
    blockedStepsCount = 0
end

local function RemoveBlockedStep(key)
    if not key or not blockedStepsByKey[key] then return end
    blockedStepsByKey[key] = nil
    blockedStepsCount = math.max(0, blockedStepsCount - 1)
    blockedStepsDirty = true
end

local SAT_ACCEPT = { active = true, ready = true, completed = true }
local SAT_COMPLETE = { ready = true, completed = true }
local SAT_TURNIN = { completed = true }
local CURSOR_BLOCK_OPTS = {
    useBulkCompletion = true,
}
local FAST_BLOCK_OPTS = {
    consultQuestie = false,
    checkIsDoable = false,
    useBulkCompletion = true,
    reasonless = true,
}
local COORD_SEC_SCALE = 3.2
local GPS_ZONE_FIELD_OPTS = {
    sameZoneOnly = true,
    unmountedOnly = true,
    allowHearth = false,
}
local LOCATION_ONLY_REASONS = {
    GPS_MOVED = true,
    GPS_ZONE_CHANGED = true,
    GPS_SYNC = true,
    ZONE_CHANGED_NEW_AREA = true,
    ZONE_CHANGED = true,
    ZONE_CHANGED_INDOORS = true,
    TAXIMAP_OPENED = true,
}
local PROFESSION_REFRESH_REASONS = {
    PLAYER_LOGIN = true,
    PLAYER_ENTERING_WORLD = true,
    CHAR_CHANGED = true,
    SKILL_LINES_CHANGED = true,
    CHAT_MSG_SKILL = true,
    MANUAL_RESET = true,
}
local BULK_COMPLETED_REASONS = {
    PLAYER_LOGIN = true,
    PLAYER_ENTERING_WORLD = true,
    CHAR_CHANGED = true,
    PROFILE_CHANGED = true,
    MANUAL_SYNC = true,
    MANUAL_RESET = true,
}
local questBlockMemoBaseSig = nil
local questBlockMemoRankSig = nil
local questBlockMemoQdbRef = nil
local questBlockMemoQueryFn = nil
local questBlockMemoDoableFn = nil
local composedRoutesCache = {}
local composedRoutesDataRef = nil
local manualCursorIndex = nil

local function GetSingleQidList(qid)
    local cache = perfScratch.singleQidListCache
    local t = cache[qid]
    if not t then
        t = { qid }
        cache[qid] = t
    end
    return t
end

local function EvictQuestFromBlockMemo(qid)
    if not qid then return end
    if questBaseMemo then
        questBaseMemo[1][qid] = nil
        questBaseMemo[2][qid] = nil
    end
    if questPostMemo then
        for i = 1, 4 do
            questPostMemo[i][qid] = nil
        end
    end
end

function State.IsLocationOnlyReason(reason)
    return LOCATION_ONLY_REASONS[reason] == true
end

local function IsPlayerLocationFrozen(player)
    if player and (player.onTaxi or player.isDead) then
        return true
    end
    if UnitOnTaxi and UnitOnTaxi("player") then
        return true
    end
    if ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost() then
        return true
    end
    return false
end

function State.GetIdlePerfStats()
    return {
        gpsTickChecks = gpsTickChecks,
        gpsTickTriggers = gpsTickTriggers,
        refreshCalls = refreshCalls,
        refreshSkipped = refreshSkipped,
        refreshExecuted = refreshExecuted,
        deadLocationSkipped = deadLocationSkipped,
        moveYardsThreshold = GPS_MOVE_YARDS_THRESHOLD,
        largeMoveYards = GPS_LARGE_MOVE_YARDS,
        cooldownSec = GPS_UPDATE_COOLDOWN_SEC,
    }
end

function State.ResetIdlePerfStats()
    gpsTickChecks = 0
    gpsTickTriggers = 0
    refreshCalls = 0
    refreshSkipped = 0
    refreshExecuted = 0
    deadLocationSkipped = 0
end

local function BumpGeneration(keepChainCache, refreshProfessions, keepDbQuestCaches, isMaxLevelOnly)
    refreshGeneration = refreshGeneration + 1
    if readyPlanCache then
        readyPlanCache.gen = -1
        readyPlanCache.plan = nil
    end
    if locationPlanCache then
        locationPlanCache.gen = -1
        locationPlanCache.plan = nil
    end
    perfScratch.dungeonPlanCache = nil
    if currentStepCache then
        currentStepCache.gen = -1
        currentStepCache.step = nil
        currentStepCache.meta = nil
    end
    if isMaxLevelOnly then
        questStateGeneration = questStateGeneration + 1
        perfScratch.readyStaticCache = nil
        perfScratch.readyRouteScanCache = nil
        perfScratch.gpsZoneWindowCache = nil
        return
    end
    if not keepChainCache then
        questStateGeneration = questStateGeneration + 1
        perfScratch.readySigGen = perfScratch.readySigGen + 1
        perfScratch.gpsLiveStateGen = perfScratch.gpsLiveStateGen + 1
        perfScratch.readyStaticCache = nil
        perfScratch.readyScanCtx = nil
        if not keepDbQuestCaches then
            dbQuestStateGeneration = dbQuestStateGeneration + 1
            completedMemo = {}
            questBaseMemo = nil
            questPostMemo = nil
            questBlockMemoBaseSig = nil
            questBlockMemoRankSig = nil
            chainProgressCache = {}
            perfScratch.readyRouteScanCache = nil
            perfScratch.gpsBaseCtx = nil
            perfScratch.gpsZoneWindowCache = nil
            perfScratch.gpsProactiveStepCache = {}
            perfScratch.gpsProactiveStepCacheGen = -1
            perfScratch.reorderedStepCache = {}
            perfScratch.reorderedCount = 0
            perfScratch.offDbEliteMemo = {}
            decCache.byMaxOff = {}
            decCache.byStepBadge = {}
            decCache.epoch = decCache.epoch + 1
            decCache.gen = -1
            decCache.playerRef = nil
        end
    end
    if refreshProfessions or (not keepChainCache and refreshProfessions ~= false) then
        professionCache = nil
    end
end

function State.GetQuestStateGeneration()
    return questStateGeneration
end

function State.GetDbQuestStateGeneration()
    return dbQuestStateGeneration
end

function State.GetRefreshGeneration()
    return refreshGeneration
end

--------------------------------------------------------------------------------
-- Index Construction
--------------------------------------------------------------------------------

function State.BuildIndexes()
    if indexesBuilt then return end
    indexesBuilt = true
    uiMapToZoneId = {}
    zoneNameToZoneId = {}
    questToDungeons = {}
    zoneQuestsIndex = {}

    for areaId, uiMapId in pairs(AREA_TO_UIMAP) do
        if not uiMapToZoneId[uiMapId] then
            uiMapToZoneId[uiMapId] = areaId
        end
    end

    local data = ns.GetData()
    for areaId, uiMapId in pairs((data.meta and data.meta.zoneUiMapIds) or {}) do
        local zid = tonumber(areaId) or areaId
        local mid = tonumber(uiMapId)
        if zid and mid then
            AREA_TO_UIMAP[zid] = AREA_TO_UIMAP[zid] or mid
            if not uiMapToZoneId[mid] then
                uiMapToZoneId[mid] = zid
            end
        end
    end
    for zoneId, z in pairs(data.zones or {}) do
        local zid = tonumber(zoneId) or zoneId
        if z.uiMapId then
            uiMapToZoneId[z.uiMapId] = zid
        end
        if z.name then
            zoneNameToZoneId[string.lower(z.name)] = zid
        end
    end

    local function LinkQuestDungeon(qid, dungeonId)
        if not qid or not dungeonId then return end
        questToDungeons[qid] = questToDungeons[qid] or {}
        for _, existing in ipairs(questToDungeons[qid]) do
            if existing == dungeonId then return end
        end
        table.insert(questToDungeons[qid], dungeonId)
    end

    perfScratch.dungeonByName = {}
    perfScratch.dungeonByZoneId = {}
    perfScratch.dungeonPlanCache = nil

    for dungeonId, d in pairs(data.dungeons or {}) do
        if d.zoneId then
            perfScratch.dungeonByZoneId[tonumber(d.zoneId) or d.zoneId] = dungeonId
        end
        if type(d.name) == "string" and d.name ~= "" then
            local lowName = string.lower((d.name:gsub("^%s+", ""):gsub("%s+$", "")))
            perfScratch.dungeonByName[lowName] = perfScratch.dungeonByName[lowName] or dungeonId
            local noThe = lowName:gsub("^the%s+", "")
            if noThe ~= "" then
                perfScratch.dungeonByName[noThe] = perfScratch.dungeonByName[noThe] or dungeonId
            end
        end
        for _, qid in ipairs(d.questIds or {}) do
            LinkQuestDungeon(qid, dungeonId)
        end
        for _, chain in ipairs(d.unlockChains or {}) do
            for _, qid in ipairs(chain.questIds or {}) do
                LinkQuestDungeon(qid, dungeonId)
            end
        end
    end

    perfScratch.dungeonByName["stormwind stockade"] = perfScratch.dungeonByName["stormwind stockade"] or "the-stockade"
    perfScratch.dungeonByName["stockade"] = perfScratch.dungeonByName["stockade"] or "the-stockade"
    perfScratch.dungeonByName["deadmines"] = perfScratch.dungeonByName["deadmines"] or "the-deadmines"
    perfScratch.dungeonByName["sunken temple"] = perfScratch.dungeonByName["sunken temple"] or "the-temple-of-atal-hakkar"
    perfScratch.dungeonByName["atal'hakkar"] = perfScratch.dungeonByName["atal'hakkar"] or "the-temple-of-atal-hakkar"
    perfScratch.dungeonByName["the temple of atal'hakkar"] = perfScratch.dungeonByName["the temple of atal'hakkar"] or "the-temple-of-atal-hakkar"
    perfScratch.dungeonByName["lower blackrock spire"] = perfScratch.dungeonByName["lower blackrock spire"] or "blackrock-spire"
    perfScratch.dungeonByName["upper blackrock spire"] = perfScratch.dungeonByName["upper blackrock spire"] or "blackrock-spire"
    perfScratch.dungeonByName["excavation site"] = perfScratch.dungeonByName["excavation site"] or "excavation-site-wetlands"
    perfScratch.dungeonByName["excavation site: wetlands"] = perfScratch.dungeonByName["excavation site: wetlands"] or "excavation-site-wetlands"
    perfScratch.dungeonByName["shaper's terrace"] = perfScratch.dungeonByName["shaper's terrace"] or "the-shapers-terrace"
    perfScratch.dungeonByName["hall of thanes"] = perfScratch.dungeonByName["hall of thanes"] or "hall-of-thanes"
    perfScratch.dungeonByName["the hall of thanes"] = perfScratch.dungeonByName["the hall of thanes"] or "hall-of-thanes"

    for qid, q in pairs(data.quests or {}) do
        if q.dungeonId then
            LinkQuestDungeon(qid, q.dungeonId)
        end
    end

    if type(data.indexes and data.indexes.byZone) == "table" and next(data.indexes.byZone) ~= nil then
        zoneQuestsIndex = data.indexes.byZone
    else
        for qid, q in pairs(data.quests or {}) do
            if q and q.zoneId then
                local bucket = zoneQuestsIndex[q.zoneId]
                if not bucket then
                    bucket = {}
                    zoneQuestsIndex[q.zoneId] = bucket
                end
                bucket[#bucket + 1] = qid
            end
        end
    end
end

function State.InvalidateIndexes()
    indexesBuilt = false
    zoneQuestsIndex = {}
    composedRoutesCache = {}
    composedRoutesDataRef = nil
    turninResolveCache = {}
    perfScratch.dungeonByName = {}
    perfScratch.dungeonByZoneId = {}
    perfScratch.dungeonPlanCache = nil
    BumpGeneration()
end

function State.GetQuestDungeons(questId)
    State.BuildIndexes()
    return questToDungeons[questId] or {}
end

function State.GetAreaToUiMapTable()
    return AREA_TO_UIMAP
end

function State.ResolveZoneId(uiMapId, zoneName)
    State.BuildIndexes()
    if uiMapId and uiMapToZoneId[uiMapId] then
        return uiMapToZoneId[uiMapId]
    end
    if zoneName and zoneName ~= "" then
        local lower = string.lower(zoneName)
        if zoneNameToZoneId[lower] then
            return zoneNameToZoneId[lower]
        end
    end
    return nil
end

--------------------------------------------------------------------------------
-- Live Character & Map State
--------------------------------------------------------------------------------

function State.DetectProfessions(forceRefresh)
    if professionCache and not forceRefresh then
        return professionCache
    end

    local skills = {}
    local apiAvailable = false
    local clientReported = false

    local function RecordSkill(skillId, name, rank)
        local id = tonumber(skillId)
        if not id and type(name) == "string" and name ~= "" then
            id = SKILL_NAME_TO_ID[string.lower(name)]
        end
        if id then
            local r = tonumber(rank) or 0
            if not skills[id] or r > skills[id] then
                skills[id] = r
            end
        end
    end

    if _G.C_SkillInfo and _G.C_SkillInfo.GetNumSkillLines and _G.C_SkillInfo.GetSkillLineInfo then
        local okN, numLines = pcall(_G.C_SkillInfo.GetNumSkillLines)
        if okN and tonumber(numLines) then
            apiAvailable = true
            for i = 1, tonumber(numLines) do
                local okI, a, b, _, d = pcall(_G.C_SkillInfo.GetSkillLineInfo, i)
                if okI then
                    if type(a) == "table" and not a.isHeader then
                        clientReported = true
                        RecordSkill(a.skillID or a.skillId, a.name, a.rank or a.skillRank)
                    elseif type(a) == "string" and not b then
                        clientReported = true
                        RecordSkill(nil, a, d)
                    end
                end
            end
        end
    end

    if _G.GetNumSkillLines and _G.GetSkillLineInfo then
        local okN, numLines = pcall(_G.GetNumSkillLines)
        if okN and tonumber(numLines) then
            apiAvailable = true
            for i = 1, tonumber(numLines) do
                local okI, a, b, _, d = pcall(_G.GetSkillLineInfo, i)
                if okI then
                    if type(a) == "table" then
                        if not a.isHeader then
                            clientReported = true
                            RecordSkill(a.skillID or a.skillId, a.name, a.rank or a.skillRank)
                        end
                    elseif type(a) == "string" and not b then
                        clientReported = true
                        RecordSkill(nil, a, d)
                    end
                end
            end
        end
    end

    if _G.GetProfessions and _G.GetProfessionInfo then
        local okP, p1, p2, arch, fish, cook, firstAid = pcall(_G.GetProfessions)
        if okP then
            apiAvailable = true
            local slots = { p1, p2, arch, fish, cook, firstAid }
            for _, slot in ipairs(slots) do
                if slot then
                    local okI, name, _, rank, _, _, _, skillLine = pcall(_G.GetProfessionInfo, slot)
                    if okI and type(name) == "string" then
                        clientReported = true
                        RecordSkill(skillLine, name, rank)
                    end
                end
            end
        end
    end

    local clientSkin = skills[SKILL_SKINNING] ~= nil
    local clientLW = skills[SKILL_LEATHERWORKING] ~= nil
    local cdb = ns.charDB
    local toggles = (cdb and cdb.toggles) or {}

    local hasSkinning
    local hasLeatherworking
    if toggles.autoDetectProfessions == false then
        hasSkinning = (toggles.hasSkinning == true)
        hasLeatherworking = (toggles.hasLeatherworking == true)
    elseif apiAvailable then
        hasSkinning = clientSkin
        hasLeatherworking = clientLW
        if cdb and cdb.toggles then
            cdb.toggles.hasSkinning = clientSkin
            cdb.toggles.hasLeatherworking = clientLW
        end
    else
        hasSkinning = (toggles.hasSkinning == true)
        hasLeatherworking = (toggles.hasLeatherworking == true)
    end

    professionCache = {
        skills = skills,
        clientReported = clientReported,
        detectedSkinning = clientSkin,
        detectedLeatherworking = clientLW,
        hasSkinning = hasSkinning,
        hasLeatherworking = hasLeatherworking,
        skinningRank = skills[SKILL_SKINNING],
        leatherworkingRank = skills[SKILL_LEATHERWORKING],
    }
    return professionCache
end

local function CollectRacesTable()
    local data = ns.GetData and ns.GetData() or nil
    if racesMergedCache and racesCacheDataRef == data then
        return racesMergedCache
    end
    local merged = {}
    local byId = {}
    local byCompact = {}
    for k, v in pairs(FALLBACK_RACES) do
        local entry = {
            id = v.id,
            key = v.key or k,
            label = v.label or k,
            faction = v.faction,
            bitmask = v.bitmask,
            startZoneId = v.startZoneId,
            startCoords = v.startCoords,
            allowedClasses = v.allowedClasses,
        }
        merged[k] = entry
        if entry.id and entry.id > 0 then byId[entry.id] = entry end
        byCompact[string.lower(tostring(k)):gsub("[%s_%-']", "")] = entry
        if entry.label then
            byCompact[string.lower(tostring(entry.label)):gsub("[%s_%-']", "")] = entry
        end
    end
    local metaRaces = data and data.meta and data.meta.races
    if type(metaRaces) == "table" then
        for k, v in pairs(metaRaces) do
            if type(v) == "table" then
                local canon = tostring(v.key or k)
                local prev = merged[canon]
                local entry = {
                    id = tonumber(v.id) or (prev and prev.id) or 0,
                    key = canon,
                    label = v.label or v.name or canon,
                    faction = v.faction or (prev and prev.faction) or "Alliance",
                    bitmask = tonumber(v.bitmask) or (prev and prev.bitmask) or 0,
                    startZoneId = tonumber(v.startZoneId) or (prev and prev.startZoneId),
                    startCoords = v.startCoords or (prev and prev.startCoords),
                    allowedClasses = v.allowedClasses or (prev and prev.allowedClasses),
                }
                merged[canon] = entry
                if entry.id and entry.id > 0 then byId[entry.id] = entry end
                byCompact[string.lower(canon):gsub("[%s_%-']", "")] = entry
                if entry.label then
                    byCompact[string.lower(tostring(entry.label)):gsub("[%s_%-']", "")] = entry
                end
            end
        end
    end
    racesCacheDataRef = data
    racesMergedCache = merged
    racesByIdCache = byId
    racesByCompactCache = byCompact
    racesTokenCache = {}
    return merged
end

local function CollectClassesTable()
    local data = ns.GetData and ns.GetData() or nil
    if classesMergedCache and classesCacheDataRef == data then
        return classesMergedCache
    end
    local merged = {}
    local byId = {}
    local byCompact = {}
    for k, v in pairs(FALLBACK_CLASSES) do
        local entry = {
            id = v.id,
            key = v.key or k,
            label = v.label or k,
            bitmask = v.bitmask,
        }
        merged[k] = entry
        if entry.id and entry.id > 0 then byId[entry.id] = entry end
        byCompact[string.lower(tostring(k)):gsub("[%s_%-']", "")] = entry
        if entry.label then
            byCompact[string.lower(tostring(entry.label)):gsub("[%s_%-']", "")] = entry
        end
    end
    local metaClasses = data and data.meta and data.meta.classes
    if type(metaClasses) == "table" then
        for k, v in pairs(metaClasses) do
            if type(v) == "table" then
                local canon = string.upper(tostring(v.key or k))
                local prev = merged[canon]
                local entry = {
                    id = tonumber(v.id) or (prev and prev.id) or 0,
                    key = canon,
                    label = v.label or v.name or canon,
                    bitmask = tonumber(v.bitmask) or (prev and prev.bitmask) or 0,
                }
                merged[canon] = entry
                if entry.id and entry.id > 0 then byId[entry.id] = entry end
                byCompact[string.lower(canon):gsub("[%s_%-']", "")] = entry
                if entry.label then
                    byCompact[string.lower(tostring(entry.label)):gsub("[%s_%-']", "")] = entry
                end
            end
        end
    end
    classesCacheDataRef = data
    classesMergedCache = merged
    classesByIdCache = byId
    classesByCompactCache = byCompact
    classesTokenCache = {}
    return merged
end

function State.GetAvailableRaces(factionFilter)
    local out = {}
    for _, r in pairs(CollectRacesTable()) do
        if (not factionFilter) or r.faction == factionFilter then
            out[#out + 1] = r
        end
    end
    table.sort(out, function(a, b)
        if a.faction ~= b.faction then
            return (a.faction or "") < (b.faction or "")
        end
        if (a.id or 999) ~= (b.id or 999) then
            return (a.id or 999) < (b.id or 999)
        end
        return (a.label or "") < (b.label or "")
    end)
    return out
end

function State.GetAvailableClasses(raceToken)
    local classes = CollectClassesTable()
    local allowedSet = nil
    if raceToken ~= nil then
        local rObj = State.ResolveRace(raceToken)
        if rObj and type(rObj.allowedClasses) == "table" and #rObj.allowedClasses > 0 then
            allowedSet = {}
            for _, ck in ipairs(rObj.allowedClasses) do
                allowedSet[string.upper(tostring(ck))] = true
            end
        end
    end
    local out = {}
    for k, c in pairs(classes) do
        if (not allowedSet) or allowedSet[string.upper(tostring(c.key or k))] then
            out[#out + 1] = c
        end
    end
    table.sort(out, function(a, b)
        return (a.id or 999) < (b.id or 999)
    end)
    return out
end

function State.ResolveRace(token)
    if token == nil then return nil end
    CollectRacesTable()
    if type(token) == "number" then
        return racesByIdCache[token]
    end
    local cached = racesTokenCache[token]
    if cached ~= nil then
        return (cached ~= false) and cached or nil
    end
    local s = tostring(token):match("^%s*(.-)%s*$")
    if s == "" then
        racesTokenCache[token] = false
        return nil
    end
    local num = tonumber(s)
    if num and racesByIdCache[num] then
        racesTokenCache[token] = racesByIdCache[num]
        return racesByIdCache[num]
    end
    local compact = string.lower(s):gsub("[%s_%-']", "")
    if compact == "undead" or compact == "forsaken" then
        compact = "scourge"
    elseif compact == "ne" or compact == "nelf" then
        compact = "nightelf"
    elseif compact == "helf" then
        compact = "highelf"
    elseif compact == "belf" then
        compact = "bloodelf"
    elseif compact == "skyborne" or compact == "highorder" or compact == "allianceskyborne" then
        compact = "highorderskyborne"
    elseif compact == "windshaper" or compact == "hordeskyborne" then
        compact = "windshaperskyborne"
    end
    local res = racesByCompactCache[compact]
    racesTokenCache[token] = res or false
    return res
end

function State.ResolveClass(token)
    if token == nil then return nil end
    CollectClassesTable()
    if type(token) == "number" then
        return classesByIdCache[token]
    end
    local cached = classesTokenCache[token]
    if cached ~= nil then
        return (cached ~= false) and cached or nil
    end
    local s = tostring(token):match("^%s*(.-)%s*$")
    if s == "" then
        classesTokenCache[token] = false
        return nil
    end
    local num = tonumber(s)
    if num and classesByIdCache[num] then
        classesTokenCache[token] = classesByIdCache[num]
        return classesByIdCache[num]
    end
    local compact = string.lower(s):gsub("[%s_%-']", "")
    local res = classesByCompactCache[compact]
    classesTokenCache[token] = res or false
    return res
end

function State.SetCharacterOverride(raceToken, classToken, factionToken)
    if not ns.charDB then return false, "SavedVariables not initialized" end
    local raceObj = nil
    local classObj = nil
    if raceToken ~= nil and tostring(raceToken) ~= "" then
        raceObj = State.ResolveRace(raceToken)
        if not raceObj then
            return false, "Unknown race: " .. tostring(raceToken)
        end
    end
    if classToken ~= nil and tostring(classToken) ~= "" then
        classObj = State.ResolveClass(classToken)
        if not classObj then
            return false, "Unknown class: " .. tostring(classToken)
        end
    end
    ns.charDB.toggles = ns.charDB.toggles or {}
    ns.charDB.toggles.autoDetectChar = false
    if raceObj then
        ns.charDB.overrideRace = raceObj.key
        ns.charDB.overrideFaction = factionToken or raceObj.faction
    elseif factionToken then
        ns.charDB.overrideFaction = factionToken
    end
    if classObj then
        ns.charDB.overrideClass = classObj.key
    end
    ns.charDB.stepIndex = 1
    manualCursorIndex = nil
    ns.charDB.history = {}
    ns.charDB.deferredSteps = {}
    ClearBlockedStepsLog()
    deadBootLocation = nil
    playerCache = nil
    State.Refresh("CHAR_CHANGED")
    return true
end

function State.ClearCharacterOverride()
    if not ns.charDB then return end
    ns.charDB.toggles = ns.charDB.toggles or {}
    ns.charDB.toggles.autoDetectChar = true
    ns.charDB.overrideRace = nil
    ns.charDB.overrideClass = nil
    ns.charDB.overrideFaction = nil
    ns.charDB.stepIndex = 1
    manualCursorIndex = nil
    ns.charDB.history = {}
    ns.charDB.deferredSteps = {}
    ClearBlockedStepsLog()
    deadBootLocation = nil
    playerCache = nil
    State.Refresh("CHAR_CHANGED")
end

local function ClampMaxLevelOffset(val)
    local n = tonumber(val)
    if not n then return DEFAULT_MAX_LEVEL_OFFSET end
    n = math.floor(n + 0.5)
    if n == 0 then n = 0 end
    if n < MIN_MAX_LEVEL_OFFSET then n = MIN_MAX_LEVEL_OFFSET end
    if n > MAX_MAX_LEVEL_OFFSET then n = MAX_MAX_LEVEL_OFFSET end
    return n
end

function State.GetMaxLevelOffset()
    local cdb = ns.charDB
    if cdb and cdb.maxLevelOffset ~= nil then
        return ClampMaxLevelOffset(cdb.maxLevelOffset)
    end
    local data = ns.GetData and ns.GetData() or nil
    local defOff = data and data.meta and tonumber(data.meta.defaultMaxLevelOffset)
    return ClampMaxLevelOffset(defOff ~= nil and defOff or DEFAULT_MAX_LEVEL_OFFSET)
end

function State.GetEffectiveMaxLevelOffset(toggles)
    local off = State.GetMaxLevelOffset()
    if type(toggles) == "table" then
        if toggles.maxLevelOffset ~= nil then
            off = ClampMaxLevelOffset(toggles.maxLevelOffset)
        end
        if toggles.allowRedQuests == true and off < 5 then
            off = 5
        end
    end
    return off
end

function State.SetMaxLevelOffset(offset)
    local clamped = ClampMaxLevelOffset(offset)
    if not ns.charDB then return clamped end
    if clamped >= 5 then
        local prev = tonumber(ns.charDB.maxLevelOffset)
        if prev and prev < 5 then
            ns.charDB.prevNonRedMaxLevelOffset = ClampMaxLevelOffset(prev)
        end
    else
        ns.charDB.prevNonRedMaxLevelOffset = clamped
    end
    ns.charDB.maxLevelOffset = clamped
    ns.charDB.toggles = ns.charDB.toggles or {}
    ns.charDB.toggles.allowRedQuests = (clamped >= 5)
    ClearBlockedStepsLog()
    if ns.Scorer and ns.Scorer.InvalidateCaches then
        ns.Scorer.InvalidateCaches()
    end
    State.Refresh("MAX_LEVEL_CHANGED")
    return clamped
end

function State.GetPlayerInfo()
    State.BuildIndexes()
    local prof = State.DetectProfessions()
    local name = UnitName and UnitName("player") or "Adventurer"
    local realm = GetRealmName and GetRealmName() or "Forever"
    local rawLevel = (UnitLevel and UnitLevel("player")) or 24
    if levelOverride and rawLevel >= levelOverride then
        levelOverride = nil
    end
    local level = math.max(rawLevel or 24, levelOverride or 0)
    local xp = UnitXP and UnitXP("player") or 0
    local xpMax = UnitXPMax and UnitXPMax("player") or 1
    if xpMax <= 0 then xpMax = 1 end
    local xpPct = math.floor((xp / xpMax) * 100 + 0.5)

    local liveRaceName, liveRaceFile, liveRaceId = "Gnome", "Gnome", 7
    if UnitRace then
        local rn, rf, rid = UnitRace("player")
        liveRaceName = rn or liveRaceName
        liveRaceFile = rf or liveRaceFile
        liveRaceId = rid or liveRaceId
    end

    local liveClassName, liveClassFile, liveClassId = "Rogue", "ROGUE", 4
    if UnitClass then
        local cn, cf, cid = UnitClass("player")
        liveClassName = cn or liveClassName
        liveClassFile = cf or liveClassFile
        liveClassId = cid or liveClassId
    end

    local liveFaction = (UnitFactionGroup and UnitFactionGroup("player")) or "Alliance"

    local cdb = ns.charDB or {}
    local toggles = cdb.toggles or {}
    local isOverride = (toggles.autoDetectChar == false)
    local raceInfo = (isOverride and cdb.overrideRace and State.ResolveRace(cdb.overrideRace))
        or State.ResolveRace(liveRaceFile)
        or State.ResolveRace(liveRaceName)
        or State.ResolveRace(liveRaceId)
    local classInfo = (isOverride and cdb.overrideClass and State.ResolveClass(cdb.overrideClass))
        or State.ResolveClass(liveClassFile)
        or State.ResolveClass(liveClassName)
        or State.ResolveClass(liveClassId)

    local raceName = (isOverride and raceInfo and raceInfo.label) or liveRaceName
    local raceFile = (isOverride and raceInfo and raceInfo.key) or liveRaceFile
    local raceId = (raceInfo and raceInfo.id and raceInfo.id > 0 and raceInfo.id) or liveRaceId
    local raceBitmask = (raceInfo and raceInfo.bitmask and raceInfo.bitmask > 0 and raceInfo.bitmask) or (raceId and raceId >= 1 and raceId <= 50 and (2 ^ (raceId - 1)) or 0)
    local startZoneId = raceInfo and raceInfo.startZoneId or nil

    local className = (isOverride and classInfo and classInfo.label) or liveClassName
    local classFile = string.upper((isOverride and classInfo and classInfo.key) or liveClassFile or "ROGUE")
    local classId = (classInfo and classInfo.id and classInfo.id > 0 and classInfo.id) or liveClassId
    local classBitmask = (classInfo and classInfo.bitmask and classInfo.bitmask > 0 and classInfo.bitmask) or (classId and classId >= 1 and classId <= 30 and (2 ^ (classId - 1)) or 0)

    local faction = (isOverride and ((raceInfo and raceInfo.faction) or cdb.overrideFaction)) or liveFaction
    local bindLocation = GetBindLocation and GetBindLocation() or nil

    local uiMapId = C_Map and C_Map.GetBestMapForUnit and C_Map.GetBestMapForUnit("player") or nil
    local px, py = nil, nil
    if uiMapId and C_Map and C_Map.GetPlayerMapPosition then
        local pos = C_Map.GetPlayerMapPosition(uiMapId, "player")
        if pos then
            local gx, gy
            if pos.GetXY then
                gx, gy = pos:GetXY()
            elseif type(pos) == "table" then
                gx, gy = pos.x or pos[1], pos.y or pos[2]
            end
            if type(gx) == "number" and type(gy) == "number" and (gx > 0 or gy > 0) then
                px = gx * 100
                py = gy * 100
            end
        end
    end

    local mapInfo = uiMapId and C_Map and C_Map.GetMapInfo and C_Map.GetMapInfo(uiMapId) or nil
    local zoneName = (mapInfo and mapInfo.name) or (GetRealZoneText and GetRealZoneText()) or "Wetlands"
    local subZoneName = GetSubZoneText and GetSubZoneText() or ""
    local zoneId = State.ResolveZoneId(uiMapId, zoneName) or startZoneId or 11
    local deadNow = (ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost()) and true or false
    local onTaxiNow = (UnitOnTaxi and UnitOnTaxi("player")) and true or false
    local inGroupNow = (ns.IsPlayerInGroup and ns.IsPlayerInGroup()) and true or false
    local inDungeonInst, instName, instMapId = false, nil, nil
    if ns.GetPlayerInstanceState then
        inDungeonInst, instName, instMapId = ns.GetPlayerInstanceState()
    end
    local activeDungeonId = nil
    if inDungeonInst and not deadNow then
        State.BuildIndexes()
        local data = ns.GetData()
        local realZText = GetRealZoneText and GetRealZoneText() or nil
        local dgKey, activeDg = perfScratch.ResolveActiveDungeon(
            true, instMapId, instName, realZText, mapInfo and mapInfo.name, subZoneName, zoneId, data
        )
        activeDungeonId = dgKey
        if activeDg then
            zoneId = activeDg.zoneId or zoneId
            zoneName = activeDg.name or instName or zoneName
        elseif instName and instName ~= "" then
            zoneName = instName
        end
    end

    if not deadNow then
        deadBootLocation = nil
        if not lastAliveLocation then
            lastAliveLocation = {}
        end
        local sameMap = (lastAliveLocation.uiMapId == uiMapId) and (lastAliveLocation.zoneId == zoneId)
        lastAliveLocation.uiMapId = uiMapId
        lastAliveLocation.zoneId = zoneId
        lastAliveLocation.zoneName = zoneName
        lastAliveLocation.subZoneName = subZoneName
        lastAliveLocation.inInstance = inDungeonInst
        lastAliveLocation.activeDungeonId = activeDungeonId
        if px and py then
            lastAliveLocation.x = px
            lastAliveLocation.y = py
        elseif not sameMap then
            lastAliveLocation.x = nil
            lastAliveLocation.y = nil
        end
    elseif lastAliveLocation then
        deadBootLocation = nil
        uiMapId = lastAliveLocation.uiMapId
        zoneId = lastAliveLocation.zoneId
        zoneName = lastAliveLocation.zoneName
        subZoneName = lastAliveLocation.subZoneName
        px = lastAliveLocation.x
        py = lastAliveLocation.y
        inDungeonInst = (lastAliveLocation.inInstance == true)
        activeDungeonId = lastAliveLocation.activeDungeonId
    else
        if inDungeonInst then
            State.BuildIndexes()
            local data = ns.GetData()
            local realZText = GetRealZoneText and GetRealZoneText() or nil
            local dgKey, activeDg = perfScratch.ResolveActiveDungeon(
                true, instMapId, instName, realZText, mapInfo and mapInfo.name, subZoneName, zoneId, data
            )
            activeDungeonId = dgKey
            if activeDg then
                zoneId = activeDg.zoneId or zoneId
                zoneName = activeDg.name or instName or zoneName
            elseif instName and instName ~= "" then
                zoneName = instName
            end
        end
        if not deadBootLocation then
            deadBootLocation = {
                uiMapId = uiMapId,
                zoneId = zoneId,
                zoneName = zoneName,
                subZoneName = subZoneName,
                x = px,
                y = py,
                inInstance = inDungeonInst,
                activeDungeonId = activeDungeonId,
            }
        else
            uiMapId = deadBootLocation.uiMapId
            zoneId = deadBootLocation.zoneId
            zoneName = deadBootLocation.zoneName
            subZoneName = deadBootLocation.subZoneName
            px = deadBootLocation.x
            py = deadBootLocation.y
            inDungeonInst = (deadBootLocation.inInstance == true)
            activeDungeonId = deadBootLocation.activeDungeonId
        end
    end

    local isCharOverride = isOverride and (cdb.overrideRace ~= nil or cdb.overrideClass ~= nil)
    local pc = playerCache
    if pc
        and pc.name == name
        and pc.realm == realm
        and pc.level == level
        and pc.xp == xp
        and pc.xpMax == xpMax
        and pc.xpPct == xpPct
        and pc.raceName == raceName
        and pc.raceFile == raceFile
        and pc.raceId == raceId
        and pc.raceBitmask == raceBitmask
        and pc.startZoneId == startZoneId
        and pc.className == className
        and pc.classFile == classFile
        and pc.classId == classId
        and pc.classBitmask == classBitmask
        and pc.faction == faction
        and pc.liveRaceName == liveRaceName
        and pc.liveRaceFile == liveRaceFile
        and pc.liveClassName == liveClassName
        and pc.liveClassFile == liveClassFile
        and pc.liveFaction == liveFaction
        and pc.isCharOverride == isCharOverride
        and pc.bindLocation == bindLocation
        and pc.uiMapId == uiMapId
        and pc.x == px
        and pc.y == py
        and pc.zoneId == zoneId
        and pc.zoneName == zoneName
        and pc.subZoneName == subZoneName
        and pc.isDead == deadNow
        and pc.onTaxi == onTaxiNow
        and pc.inGroup == inGroupNow
        and pc.inInstance == inDungeonInst
        and pc.activeDungeonId == activeDungeonId
        and pc.hasSkinning == prof.hasSkinning
        and pc.hasLeatherworking == prof.hasLeatherworking
        and pc.skinningRank == prof.skinningRank
        and pc.leatherworkingRank == prof.leatherworkingRank
        and pc.detectedSkinning == prof.detectedSkinning
        and pc.detectedLeatherworking == prof.detectedLeatherworking
        and pc.professions == prof
    then
        return pc
    end

    playerCache = {
        name = name,
        realm = realm,
        level = level,
        xp = xp,
        xpMax = xpMax,
        xpPct = xpPct,
        raceName = raceName,
        raceFile = raceFile,
        raceId = raceId,
        raceBitmask = raceBitmask,
        startZoneId = startZoneId,
        className = className,
        classFile = classFile,
        classId = classId,
        classBitmask = classBitmask,
        faction = faction,
        liveRaceName = liveRaceName,
        liveRaceFile = liveRaceFile,
        liveClassName = liveClassName,
        liveClassFile = liveClassFile,
        liveFaction = liveFaction,
        isCharOverride = isCharOverride,
        bindLocation = bindLocation,
        uiMapId = uiMapId,
        x = px,
        y = py,
        zoneId = zoneId,
        zoneName = zoneName,
        subZoneName = subZoneName,
        isDead = deadNow,
        onTaxi = onTaxiNow,
        inGroup = inGroupNow,
        inInstance = inDungeonInst,
        activeDungeonId = activeDungeonId,
        hasSkinning = prof.hasSkinning,
        hasLeatherworking = prof.hasLeatherworking,
        skinningRank = prof.skinningRank,
        leatherworkingRank = prof.leatherworkingRank,
        detectedSkinning = prof.detectedSkinning,
        detectedLeatherworking = prof.detectedLeatherworking,
        professions = prof,
    }
    return playerCache
end

function State.GetSnapshot()
    return State.GetPlayerInfo()
end

--------------------------------------------------------------------------------
-- Quest Completion & Quest Log State
--------------------------------------------------------------------------------

function State.RefreshCompletedBulk()
    completedBulk = {}
    hasBulkSync = false
    if C_QuestLog and C_QuestLog.GetAllCompletedQuestIDs then
        local ok, list = pcall(C_QuestLog.GetAllCompletedQuestIDs)
        if ok and type(list) == "table" then
            for i = 1, #list do
                local qid = list[i]
                if qid then
                    completedBulk[qid] = true
                end
            end
            hasBulkSync = true
        end
    elseif GetQuestsCompleted then
        local ok, tbl = pcall(GetQuestsCompleted)
        if ok and type(tbl) == "table" then
            for qid, val in pairs(tbl) do
                if val then
                    completedBulk[qid] = true
                end
            end
            hasBulkSync = true
        end
    end
    completedMemo = {}
    for qid, val in pairs(sessionCompleted) do
        if val then
            completedBulk[qid] = true
        end
    end
    perfScratch.readySigGen = perfScratch.readySigGen + 1
    perfScratch.gpsLiveStateGen = perfScratch.gpsLiveStateGen + 1
end

function State.IsQuestCompleted(questId, preferBulk)
    if not questId then return false end
    if sessionCompleted[questId] or completedBulk[questId] then
        return true
    end
    if preferBulk and hasBulkSync then
        return false
    end
    local cached = completedMemo[questId]
    if cached ~= nil then
        return cached
    end
    local done = false
    if C_QuestLog and C_QuestLog.IsQuestFlaggedCompleted then
        local ok, res = pcall(C_QuestLog.IsQuestFlaggedCompleted, questId)
        if ok and res then done = true end
    elseif IsQuestFlaggedCompleted then
        local ok, res = pcall(IsQuestFlaggedCompleted, questId)
        if ok and res then done = true end
    end
    if done then
        completedBulk[questId] = true
    end
    completedMemo[questId] = done
    return done
end

function State.IsQuestCompletedFast(questId)
    return State.IsQuestCompleted(questId, true)
end

function State.GetCompletedReadOnly()
    if not hasBulkSync then
        State.RefreshCompletedBulk()
    end
    for qid, val in pairs(sessionCompleted) do
        if val and not completedBulk[qid] then
            completedBulk[qid] = true
        end
    end
    return completedBulk, hasBulkSync
end

function State.GetCompletedSet()
    local base = State.GetCompletedReadOnly()
    local out = {}
    for qid, val in pairs(base) do
        if val then out[qid] = true end
    end
    return out
end

function State.IsQuestExcluded(questId, preferBulk)
    local data = ns.GetData()
    local q = data.quests and data.quests[questId]
    if not q or not q.exclusiveTo then
        return false
    end
    for _, otherId in ipairs(q.exclusiveTo) do
        if otherId ~= questId and (State.IsQuestCompleted(otherId, preferBulk) or activeQuests[otherId] ~= nil) then
            return true
        end
    end
    return false
end

local function ComputeObjectivesSig(objs)
    if type(objs) ~= "table" then
        return 0
    end
    local sig = #objs
    for i = 1, #objs do
        local o = objs[i]
        if type(o) == "table" then
            local fin = o.finished and 1 or 0
            local cur = tonumber(o.numFulfilled) or 0
            local req = tonumber(o.numRequired) or 0
            local txtLen = (type(o.text) == "string") and #o.text or 0
            local txtTail = (txtLen > 0) and string.byte(o.text, txtLen) or 0
            sig = sig * 131 + cur * 17 + req * 5 + fin * 3 + txtLen * 7 + txtTail + i
            if sig > 1073741824 then
                sig = sig % 1073741823
            end
        end
    end
    return sig
end

local function ResolveCQuestLogFlags(qid, info)
    local isComplete = false
    if info.isComplete == true or info.isComplete == 1 then
        isComplete = true
    elseif C_QuestLog.IsComplete then
        local okC, c = pcall(C_QuestLog.IsComplete, qid)
        if okC and (c == true or c == 1) then
            isComplete = true
        end
    end
    if (not isComplete) and C_QuestLog.ReadyForTurnIn then
        local okR, r = pcall(C_QuestLog.ReadyForTurnIn, qid)
        if okR and (r == true or r == 1) then
            isComplete = true
        end
    end
    local isFailed = false
    if C_QuestLog.IsFailed then
        local okF, f = pcall(C_QuestLog.IsFailed, qid)
        isFailed = okF and (f == true) or false
    end
    return isComplete, isFailed
end

local function ResolveLegacyQuestFlags(questID, rawComplete)
    local failedFlag = (rawComplete == -1)
    local readyFlag = (not failedFlag) and (rawComplete == 1 or rawComplete == true)
    if (not readyFlag) and (not failedFlag) and C_QuestLog then
        if C_QuestLog.IsComplete then
            local okC, c = pcall(C_QuestLog.IsComplete, questID)
            if okC and (c == true or c == 1) then readyFlag = true end
        end
        if (not readyFlag) and C_QuestLog.ReadyForTurnIn then
            local okR, r = pcall(C_QuestLog.ReadyForTurnIn, questID)
            if okR and (r == true or r == 1) then readyFlag = true end
        end
    end
    return readyFlag, failedFlag
end

local function InspectQuestLogDelta()
    if (not activeLogInitialized) or sessionAcceptedPending then
        return true, (not hasBulkSync)
    end
    local curLevel = UnitLevel and tonumber(UnitLevel("player")) or nil
    if curLevel and lastObservedPlayerLevel and curLevel ~= lastObservedPlayerLevel then
        return true, false
    end

    if C_QuestLog and C_QuestLog.GetNumQuestLogEntries and C_QuestLog.GetInfo then
        local ok, numEntries = pcall(C_QuestLog.GetNumQuestLogEntries)
        if not ok then return true, false end
        numEntries = tonumber(numEntries) or 0
        local matched = 0
        for i = 1, numEntries do
            local okInfo, info = pcall(C_QuestLog.GetInfo, i)
            if okInfo and info and not info.isHeader and info.questID and info.questID > 0 then
                local qid = info.questID
                local prev = activeQuests[qid]
                if not prev then
                    return true, false
                end
                local isComplete, isFailed = ResolveCQuestLogFlags(qid, info)
                if isComplete ~= (prev.isComplete == true) or isFailed ~= (prev.isFailed == true) then
                    return true, false
                end
                matched = matched + 1
            end
        end
        if matched ~= activeQuestCount then
            return true, true
        end
    elseif GetNumQuestLogEntries and GetQuestLogTitle then
        local ok, numEntries = pcall(GetNumQuestLogEntries)
        if not ok then return true, false end
        numEntries = tonumber(numEntries) or 0
        local matched = 0
        for i = 1, numEntries do
            local _, _, _, isHeader, _, isComplete, _, questID = GetQuestLogTitle(i)
            if not isHeader and questID and questID > 0 then
                local prev = activeQuests[questID]
                if not prev then
                    return true, false
                end
                local readyFlag, failedFlag = ResolveLegacyQuestFlags(questID, isComplete)
                if readyFlag ~= (prev.isComplete == true) or failedFlag ~= (prev.isFailed == true) then
                    return true, false
                end
                matched = matched + 1
            end
        end
        if matched ~= activeQuestCount then
            return true, true
        end
    else
        return true, (not hasBulkSync)
    end

    if not hasBulkSync then
        return true, true
    end

    if trackedStepQidCount > 0 then
        local checkFn = (C_QuestLog and C_QuestLog.IsQuestFlaggedCompleted) or IsQuestFlaggedCompleted
        if checkFn then
            for i = 1, trackedStepQidCount do
                local cqid = trackedStepQids[i]
                if cqid and (not completedBulk[cqid]) and (not sessionCompleted[cqid]) then
                    local okF, done = pcall(checkFn, cqid)
                    if okF and done then
                        return true, true
                    end
                end
            end
        end
    end

    return false, false
end

local function ProfTablesEqual(a, b, depth)
    if a == b then return true end
    if type(a) ~= "table" or type(b) ~= "table" then return false end
    depth = depth or 0
    for k, v in pairs(a) do
        local bv = b[k]
        if bv ~= v then
            if depth == 0 and type(v) == "table" and type(bv) == "table" then
                if not ProfTablesEqual(v, bv, 1) then
                    return false
                end
            else
                return false
            end
        end
    end
    for k in pairs(b) do
        if a[k] == nil then
            return false
        end
    end
    return true
end

function perfScratch.IsEliteQuestTag(tagName, suggestedGroup)
    if type(tagName) == "string" and tagName ~= "" then
        local lower = string.lower(tagName)
        if lower == "dungeon" or lower == "heroic" then
            return false
        end
        if lower == "elite" or lower == "group" or lower == "raid" then
            return true
        end
    end
    if type(suggestedGroup) == "number" and suggestedGroup >= 2 then
        return true
    end
    return false
end

function perfScratch.IsDungeonQuestTag(tagName)
    if type(tagName) == "string" and tagName ~= "" then
        local lower = string.lower(tagName)
        if lower == "dungeon" or lower == "heroic" then
            return true
        end
    end
    return false
end

function State.ShouldSkipEliteNow(player, toggles)
    local t = toggles or (ns.charDB and ns.charDB.toggles)
    if t and t.skipEliteSolo == false then
        return false
    end
    if player ~= nil and player.inGroup ~= nil then
        return not player.inGroup
    end
    if ns.IsPlayerInGroup and ns.IsPlayerInGroup() then
        return false
    end
    return true
end

function State.ShouldSkipDungeonsNow(toggles)
    local t = toggles or (ns.charDB and ns.charDB.toggles)
    if t and t.skipDungeons == false then
        return false
    end
    return true
end

function State.HasGroupStatusChanged()
    local curInGroup = (ns.IsPlayerInGroup and ns.IsPlayerInGroup()) and true or false
    if perfScratch.lastObservedInGroup == nil then
        return true
    end
    return perfScratch.lastObservedInGroup ~= curInGroup
end

local function GetQuestieDB()
    if _G.QuestieDB and type(_G.QuestieDB.QueryQuestSingle) == "function" then
        return _G.QuestieDB
    end
    if _G.QuestieLoader and type(_G.QuestieLoader.ImportModule) == "function" then
        local ok, qdb = pcall(_G.QuestieLoader.ImportModule, _G.QuestieLoader, "QuestieDB")
        if ok and type(qdb) == "table" and type(qdb.QueryQuestSingle) == "function" then
            return qdb
        end
    end
    return nil
end

function State.IsQuestDungeon(questId, q)
    if not questId then
        return false
    end
    if not q then
        local data = ns.GetData()
        q = data and data.quests and data.quests[questId] or nil
    end
    if q then
        if q.isDungeon == true then
            return true
        end
        local act = activeQuests and activeQuests[questId]
        if act and act.isDungeon == true then
            return true
        end
        return false
    end
    local act = activeQuests and activeQuests[questId]
    if act and act.isDungeon == true then
        return true
    end
    local qdb = GetQuestieDB and GetQuestieDB() or nil
    if perfScratch.offDbDungeonQdbRef ~= qdb then
        perfScratch.offDbDungeonMemo = {}
        perfScratch.offDbDungeonQdbRef = qdb
    end
    local memo = perfScratch.offDbDungeonMemo
    if memo then
        local cached = memo[questId]
        if cached ~= nil then
            return cached == true
        end
    end
    local isDg = false
    if C_QuestLog and C_QuestLog.GetQuestTagInfo then
        local okT, tagInfo = pcall(C_QuestLog.GetQuestTagInfo, questId)
        if okT and type(tagInfo) == "table" and perfScratch.IsDungeonQuestTag(tagInfo.tagName) then
            isDg = true
        end
    end
    if (not isDg) and qdb and qdb.QueryQuestSingle then
        local okF, flags = pcall(qdb.QueryQuestSingle, questId, "questFlags")
        flags = okF and tonumber(flags) or nil
        if flags and flags > 0 then
            local hasBit32 = (math.floor(flags / 32) % 2) == 1
            if hasBit32 then
                isDg = true
            end
        end
    end
    if memo then
        memo[questId] = isDg
    end
    return isDg
end

function State.IsQuestElite(questId, q)
    if not questId then
        return false
    end
    if not q then
        local data = ns.GetData()
        q = data and data.quests and data.quests[questId] or nil
    end
    if q then
        if q.isDungeon == true then
            return false
        end
        if q.isGroup == true then
            return true
        end
    end
    local act = activeQuests and activeQuests[questId]
    if act and act.isElite == true then
        return true
    end
    if q then
        return false
    end
    local qdb = GetQuestieDB and GetQuestieDB() or nil
    if perfScratch.offDbEliteQdbRef ~= qdb then
        perfScratch.offDbEliteMemo = {}
        perfScratch.offDbEliteQdbRef = qdb
    end
    local memo = perfScratch.offDbEliteMemo
    if memo then
        local cached = memo[questId]
        if cached ~= nil then
            return cached == true
        end
    end
    local isEl = false
    if C_QuestLog and C_QuestLog.GetQuestTagInfo then
        local okT, tagInfo = pcall(C_QuestLog.GetQuestTagInfo, questId)
        if okT and type(tagInfo) == "table" and perfScratch.IsEliteQuestTag(tagInfo.tagName, tagInfo.suggestedGroup) then
            isEl = true
        end
    end
    if (not isEl) and qdb and qdb.QueryQuestSingle then
        local okF, flags = pcall(qdb.QueryQuestSingle, questId, "questFlags")
        flags = okF and tonumber(flags) or nil
        if flags and flags > 0 then
            local hasBit64 = (math.floor(flags / 64) % 2) == 1
            local hasBit32 = (math.floor(flags / 32) % 2) == 1
            if hasBit64 and not hasBit32 then
                isEl = true
            end
        end
    end
    if memo then
        memo[questId] = isEl
    end
    return isEl
end

function State.ScanQuestLog()
    local updated = {}
    local data = ns.GetData()
    local dbQuests = data and data.quests
    if C_QuestLog and C_QuestLog.GetNumQuestLogEntries and C_QuestLog.GetInfo then
        local ok, numEntries = pcall(C_QuestLog.GetNumQuestLogEntries)
        numEntries = ok and tonumber(numEntries) or 0
        for i = 1, numEntries do
            local okInfo, info = pcall(C_QuestLog.GetInfo, i)
            if okInfo and info and not info.isHeader and info.questID and info.questID > 0 then
                local qid = info.questID
                local isComplete, isFailed = ResolveCQuestLogFlags(qid, info)
                local isEl = perfScratch.IsEliteQuestTag(info.tagName, info.suggestedGroup)
                local isDung = perfScratch.IsDungeonQuestTag(info.tagName)
                if (not isEl or not isDung) and info.tagName == nil and (not (dbQuests and dbQuests[qid])) and C_QuestLog.GetQuestTagInfo then
                    local okT, tagInfo = pcall(C_QuestLog.GetQuestTagInfo, qid)
                    if okT and type(tagInfo) == "table" then
                        if not isEl then
                            isEl = perfScratch.IsEliteQuestTag(tagInfo.tagName, tagInfo.suggestedGroup)
                        end
                        if not isDung then
                            isDung = perfScratch.IsDungeonQuestTag(tagInfo.tagName)
                        end
                    end
                end
                updated[qid] = {
                    questId = qid,
                    title = info.title,
                    level = info.level,
                    isComplete = isComplete,
                    isFailed = isFailed,
                    isElite = isEl or nil,
                    isDungeon = isDung or nil,
                    objectives = nil,
                }
                sessionAccepted[qid] = nil
            end
        end
    elseif GetNumQuestLogEntries and GetQuestLogTitle then
        local ok, numEntries = pcall(GetNumQuestLogEntries)
        numEntries = ok and tonumber(numEntries) or 0
        for i = 1, numEntries do
            local title, level, questTag, isHeader, _, isComplete, _, questID = GetQuestLogTitle(i)
            if not isHeader and questID and questID > 0 then
                local readyFlag, failedFlag = ResolveLegacyQuestFlags(questID, isComplete)
                local isEl = perfScratch.IsEliteQuestTag(questTag, nil)
                local isDung = perfScratch.IsDungeonQuestTag(questTag)
                updated[questID] = {
                    questId = questID,
                    title = title,
                    level = level,
                    isComplete = readyFlag,
                    isFailed = failedFlag,
                    isElite = isEl or nil,
                    isDungeon = isDung or nil,
                    objectives = nil,
                }
                sessionAccepted[questID] = nil
            end
        end
    end

    local stillPending = false
    for qid, rawScans in pairs(sessionAccepted) do
        local scans = (type(rawScans) == "number" and rawScans or 0) + 1
        if scans <= 1 and (not State.IsQuestCompleted(qid)) and (not updated[qid]) then
            local q = dbQuests and dbQuests[qid]
            updated[qid] = {
                questId = qid,
                title = q and q.name or ("Quest #" .. tostring(qid)),
                level = q and q.questLevel or 0,
                isComplete = false,
                isFailed = false,
                isElite = (q and q.isGroup == true and not q.isDungeon) or nil,
                isDungeon = (q and q.isDungeon == true) or nil,
                objectives = nil,
            }
            sessionAccepted[qid] = scans
            stillPending = true
        else
            sessionAccepted[qid] = nil
        end
    end
    sessionAcceptedPending = stillPending

    local count = 0
    for _ in pairs(updated) do
        count = count + 1
    end
    activeQuests = updated
    activeQuestCount = count
    activeLogInitialized = true
    if readyPlanCache then
        readyPlanCache.gen = -1
        readyPlanCache.plan = nil
    end
    perfScratch.readySigGen = perfScratch.readySigGen + 1
    perfScratch.gpsLiveStateGen = perfScratch.gpsLiveStateGen + 1
    return activeQuests
end

function State.GetActiveQuests()
    return activeQuests
end

function State.GetQuestLogEntry(questId)
    return activeQuests[questId]
end

-- Returns: "completed" | "ready" | "active" | "available"
function State.GetQuestStatus(questId, preferBulk)
    if not questId then return "available" end
    if State.IsQuestCompleted(questId, preferBulk) then
        return "completed"
    end
    local entry = activeQuests[questId]
    if entry then
        if entry.isComplete and not entry.isFailed then
            return "ready"
        end
        return "active"
    end
    return "available"
end

function State.ArePrereqsMet(questId, extraCompleted, preferBulk)
    local data = ns.GetData()
    local q = data.quests[questId]
    if not q or not q.prereqIds or #q.prereqIds == 0 then
        return true
    end
    local isAny = (q.prereqMode == "any")
    for _, pid in ipairs(q.prereqIds) do
        local done = State.IsQuestCompleted(pid, preferBulk) or (extraCompleted and extraCompleted[pid] == true)
        if isAny and done then
            return true
        elseif (not isAny) and (not done) then
            return false
        end
    end
    return not isAny
end

function State.IsEligibleForPlayer(questId, player)
    player = player or playerCache or State.GetPlayerInfo()
    local data = ns.GetData()
    local quests = data.quests
    local q = quests and quests[questId]
    if not q then return false end

    local pSide = FACTION_TO_SIDE[player.faction] or 1
    local cKey = player.classBitmask or player.classId or 0
    local rKey = player.raceBitmask or player.raceId or 0
    local em = perfScratch.elig
    if em.side ~= pSide or em.cKey ~= cKey or em.rKey ~= rKey or em.questsRef ~= quests then
        em.side = pSide
        em.cKey = cKey
        em.rKey = rKey
        em.questsRef = quests
        em.qRefs = {}
        em.vals = {}
    end
    if em.qRefs[questId] == q then
        return em.vals[questId]
    end

    local ok = true
    local side = q.side
    if not side and q.faction then
        side = FACTION_TO_SIDE[q.faction] or 3
    end
    if not side and q.factionMask then
        side = q.factionMask
    end
    if side and side ~= 3 and side ~= pSide then
        ok = false
    end

    if ok and q.classMask and q.classMask > 0 then
        local cBit = player.classBitmask
        if cBit and cBit > 0 then
            if not ns.HasBitMask(q.classMask, cBit) then
                ok = false
            end
        elseif player.classId and not ns.HasBit(q.classMask, player.classId) then
            ok = false
        end
    end
    if ok and q.raceMask and q.raceMask > 0 then
        local rBit = player.raceBitmask
        if rBit and rBit > 0 then
            if not ns.HasBitMask(q.raceMask, rBit) then
                ok = false
            end
        elseif player.raceId and not ns.HasBit(q.raceMask, player.raceId) then
            ok = false
        end
    end

    em.qRefs[questId] = q
    em.vals[questId] = ok
    return ok
end

--------------------------------------------------------------------------------
-- Live Eligibility Guards & Prerequisite Resolution
--------------------------------------------------------------------------------

local function RecordBlockedStep(step, idx, profileKey, code, reason)
    if not step or not code then return end
    local key = State.GetStepKey(step, idx, profileKey)
    local existing = blockedStepsByKey[key]
    if existing then
        existing.index = idx or step.index or existing.index or 0
        existing.code = code
        existing.reason = reason
        existing.step = step or existing.step
        return
    end
    local entry = {
        key = key,
        index = idx or step.index or 0,
        action = step.action or "ACCEPT",
        questId = step.questId,
        title = step.title or ("Step #" .. tostring(idx or "?")),
        zoneName = step.zoneName or "",
        code = code,
        reason = reason or code,
        step = step,
    }
    blockedStepsByKey[key] = entry
    blockedStepsLog[#blockedStepsLog + 1] = entry
    blockedStepsCount = blockedStepsCount + 1
end

function State.GetBlockedSteps()
    if blockedStepsDirty then
        local w = 0
        for r = 1, #blockedStepsLog do
            local e = blockedStepsLog[r]
            if e and blockedStepsByKey[e.key] == e then
                w = w + 1
                blockedStepsLog[w] = e
            end
        end
        for c = w + 1, #blockedStepsLog do
            blockedStepsLog[c] = nil
        end
        blockedStepsCount = w
        blockedStepsDirty = false
    end
    return blockedStepsLog
end

function State.GetBlockedStepCount()
    return blockedStepsCount
end

local function IsRequiredSkillBlocked(reqSkill, prof, reasonless)
    if type(reqSkill) ~= "table" then
        return nil, nil
    end
    local reqId = tonumber(reqSkill.skillId or reqSkill.skillID or reqSkill[1])
    local reqRank = tonumber(reqSkill.minRank or reqSkill.minSkill or reqSkill.rank or reqSkill[2] or 0) or 0
    if not reqId or reqId <= 0 then
        return nil, nil
    end
    if reqId ~= SKILL_SKINNING and reqId ~= SKILL_LEATHERWORKING then
        if SECONDARY_SKILLS[reqId] and prof.skills[reqId] ~= nil then
            if reqRank > 0 and prof.skills[reqId] < reqRank then
                local skillName = not reasonless and (SKILL_ID_NAMES[reqId] or ("Profession #" .. tostring(reqId)))
                return "skill_rank_too_low", reasonless and "skill_rank_too_low" or string.format("Requires %s (%d, currently %d)", skillName, reqRank, prof.skills[reqId])
            end
        else
            local skillName = not reasonless and (SKILL_ID_NAMES[reqId] or ("Profession #" .. tostring(reqId)))
            return "wrong_profession", reasonless and "wrong_profession" or string.format("Requires %s (excluded profession)", skillName)
        end
    else
        local hasProf = (reqId == SKILL_SKINNING and prof.hasSkinning) or (reqId == SKILL_LEATHERWORKING and prof.hasLeatherworking)
        if not hasProf then
            local skillName = not reasonless and (SKILL_ID_NAMES[reqId] or ("Profession #" .. tostring(reqId)))
            return "missing_profession", reasonless and "missing_profession" or string.format("Requires %s", skillName)
        end
        local knownRank = prof.skills[reqId]
        if reqRank > 0 and knownRank ~= nil and knownRank < reqRank then
            local skillName = not reasonless and (SKILL_ID_NAMES[reqId] or ("Profession #" .. tostring(reqId)))
            return "skill_rank_too_low", reasonless and "skill_rank_too_low" or string.format("Requires %s (%d, currently %d)", skillName, reqRank, knownRank)
        end
    end
    return nil, nil
end

function State.IsQuestBlockedForNav(questId, q, prof, skipEliteNow, skipDungeonsNow)
    if not q then return true end
    if skipEliteNow == nil then
        skipEliteNow = State.ShouldSkipEliteNow()
    end
    if skipEliteNow and State.IsQuestElite(questId, q) then
        return true
    end
    if skipDungeonsNow == nil then
        skipDungeonsNow = State.ShouldSkipDungeonsNow()
    end
    if skipDungeonsNow and (q.isDungeon == true or State.IsQuestDungeon(questId, q)) then
        return true
    end
    if q.exclusiveTo then
        for _, otherId in ipairs(q.exclusiveTo) do
            if otherId ~= questId and (State.IsQuestCompleted(otherId, true) or activeQuests[otherId] ~= nil) then
                return true
            end
        end
    end
    if q.requiredSkill then
        local code = IsRequiredSkillBlocked(q.requiredSkill, prof or State.DetectProfessions(), true)
        if code then return true end
    end
    if q.prereqIds and #q.prereqIds > 0 then
        local isAny = (q.prereqMode == "any")
        for _, pid in ipairs(q.prereqIds) do
            local done = State.IsQuestCompleted(pid, true)
            if isAny and done then
                return false
            elseif (not isAny) and (not done) then
                return true
            end
        end
        return isAny
    end
    return false
end

function State.GetQuestBlockReason(questId, action, player, extraCompleted, extraActive, toggles, opts)
    if not questId then
        return nil, nil
    end

    local data = ns.GetData()
    local q = data.quests and data.quests[questId]
    player = player or playerCache or State.GetPlayerInfo()
    local level = (player and tonumber(player.level)) or 24
    toggles = type(toggles) == "table" and toggles or ((ns.charDB and ns.charDB.toggles) or {})

    local useBulk = (opts and opts.useBulkCompletion == true)
    local reasonless = (opts and opts.reasonless == true)
    local isLiveCheck = (extraCompleted == nil and extraActive == nil)
    local useQuestie = (toggles.consultQuestie ~= false) and (not opts or opts.consultQuestie ~= false)
    local shouldCheckIsDoable = (opts and opts.checkIsDoable == true) or (isLiveCheck and (not opts or opts.checkIsDoable ~= false))
    local qdb = useQuestie and GetQuestieDB() or nil
    local prof = (player and player.professions) or State.DetectProfessions()

    local maxOffset = State.GetEffectiveMaxLevelOffset(toggles)
    local skipEliteNow = (opts and opts.skipEliteSolo ~= nil) and (opts.skipEliteSolo == true) or State.ShouldSkipEliteNow(player, toggles)
    local skipDungeonsNow = (opts and opts.skipDungeons ~= nil) and (opts.skipDungeons == true) or State.ShouldSkipDungeonsNow(toggles)
    local canUseBaseMemo = false
    local canUsePostMemo = false
    if useBulk then
        if isLiveCheck then
            canUseBaseMemo = true
            canUsePostMemo = true
        else
            local directTainted = (extraCompleted and extraCompleted[questId] == true)
                or (extraActive and extraActive[questId] ~= nil)
            if (not directTainted) and q and q.exclusiveTo then
                for _, oid in ipairs(q.exclusiveTo) do
                    if (extraCompleted and extraCompleted[oid] == true) or (extraActive and extraActive[oid] ~= nil) then
                        directTainted = true
                        break
                    end
                end
            end
            local prereqTainted = directTainted
            if (not prereqTainted) and extraCompleted and q and q.prereqIds then
                for _, pid in ipairs(q.prereqIds) do
                    if extraCompleted[pid] == true then
                        prereqTainted = true
                        break
                    end
                end
            end
            canUseBaseMemo = not directTainted
            canUsePostMemo = not prereqTainted
        end
    end

    local hasQdbNow = (qdb ~= nil)
    local baseBucket = nil
    local postBucket = nil
    if canUseBaseMemo or canUsePostMemo then
        local baseSig = level * 4
            + ((prof and prof.hasSkinning == true) and 2 or 0)
            + ((prof and prof.hasLeatherworking == true) and 1 or 0)
        local charSig = (player and (((tonumber(player.raceId) or 0) * 64 + (tonumber(player.classId) or 0)) * 4 + (FACTION_TO_SIDE[player.faction] or 1))) or 0
        local rSig = charSig * 100000000 + ((prof and ((prof.skinningRank or -1) * 10000 + (prof.leatherworkingRank or -1))) or 0)
        if (not questBaseMemo) or questBlockMemoBaseSig ~= baseSig or questBlockMemoRankSig ~= rSig then
            questBaseMemo = { {}, {} }
            questPostMemo = { {}, {}, {}, {} }
            questBlockMemoBaseSig = baseSig
            questBlockMemoRankSig = rSig
            questBlockMemoQdbRef = nil
            questBlockMemoQueryFn = nil
            questBlockMemoDoableFn = nil
        end
        if hasQdbNow then
            local curQueryFn = qdb.QueryQuestSingle
            local curDoableFn = qdb.IsDoable
            if questBlockMemoQdbRef ~= qdb or questBlockMemoQueryFn ~= curQueryFn or questBlockMemoDoableFn ~= curDoableFn then
                questBaseMemo[2] = {}
                questPostMemo[2] = {}
                questPostMemo[4] = {}
                questBlockMemoQdbRef = qdb
                questBlockMemoQueryFn = curQueryFn
                questBlockMemoDoableFn = curDoableFn
            end
        end
        if canUseBaseMemo then
            baseBucket = questBaseMemo[hasQdbNow and 2 or 1]
        end
        if canUsePostMemo then
            local postIdx = (shouldCheckIsDoable and 2 or 0) + (hasQdbNow and 1 or 0) + 1
            postBucket = questPostMemo[postIdx]
        end
    end

    local effectiveQLevel = 0
    local qdbHasQuest = false
    local qdbHasQuestChecked = false
    local baseHit = false
    if (skipEliteNow and action ~= "DUNGEON_PREP" and action ~= "DUNGEON_RUN") or skipDungeonsNow then
        local isVirtDone = extraCompleted and extraCompleted[questId] == true
        if not isVirtDone then
            local virtAct = extraActive and extraActive[questId]
            local liveAct = activeQuests[questId]
            local actEntry = (virtAct ~= nil) and virtAct or liveAct
            if actEntry ~= nil then
                local isDoneOrReady = State.IsQuestCompleted(questId, useBulk)
                    or (type(actEntry) == "table" and actEntry.isComplete == true)
                    or (type(actEntry) ~= "table" and liveAct and liveAct.isComplete == true)
                if not isDoneOrReady then
                    if skipEliteNow and action ~= "DUNGEON_PREP" and action ~= "DUNGEON_RUN" and State.IsQuestElite(questId, q) then
                        return "elite_solo", reasonless and "elite_solo" or "Group / Elite quest skipped while solo"
                    end
                    if skipDungeonsNow and ((action == "DUNGEON_PREP" or action == "DUNGEON_RUN")
                        or (q ~= nil and q.isDungeon == true)
                        or (type(actEntry) == "table" and actEntry.isDungeon == true)
                        or (q == nil and State.IsQuestDungeon(questId, nil))) then
                        return "dungeon_skipped", reasonless and "dungeon_skipped" or "Dungeon quest skipped (Skip Dungeons enabled)"
                    end
                end
            end
        end
    end
    if baseBucket then
        local bm = baseBucket[questId]
        if bm == -1 then
            return nil, nil
        elseif type(bm) == "table" then
            return bm[1], (reasonless and bm[1] or bm[2])
        elseif type(bm) == "number" then
            effectiveQLevel = bm
            baseHit = true
        end
    end

    if not baseHit then
        local isVirtCompleted = extraCompleted and extraCompleted[questId] == true
        local isVirtActive = extraActive and extraActive[questId] ~= nil

        local status
        if isVirtCompleted then
            status = "completed"
        elseif isVirtActive then
            status = "active"
        else
            status = State.GetQuestStatus(questId, useBulk)
        end

        if status == "completed" or status == "active" or status == "ready" then
            if baseBucket then baseBucket[questId] = -1 end
            return nil, nil
        end

        if q and q.exclusiveTo then
            for _, otherId in ipairs(q.exclusiveTo) do
                if otherId ~= questId then
                    local exDone = State.IsQuestCompleted(otherId, useBulk)
                        or activeQuests[otherId] ~= nil
                        or (extraCompleted and extraCompleted[otherId] == true)
                        or (extraActive and extraActive[otherId] ~= nil)
                    if exDone then
                        local msg = "Exclusive alternative quest already active or completed"
                        if baseBucket then baseBucket[questId] = { "exclusive_taken", msg } end
                        return "exclusive_taken", msg
                    end
                end
            end
        end

        if q and not State.IsEligibleForPlayer(questId, player) then
            local msg = "Faction, class, or race requirement not met"
            if baseBucket then baseBucket[questId] = { "ineligible_character", msg } end
            return "ineligible_character", msg
        end

        local qdbMinLevel = nil
        local qdbQuestLevel = nil
        local qdbRequiredSkill = nil

        if qdb then
            qdbHasQuestChecked = true
            local okN, qName = pcall(qdb.QueryQuestSingle, questId, "name")
            if okN and qName ~= nil then
                qdbHasQuest = true
                local okMin, rMin = pcall(qdb.QueryQuestSingle, questId, "requiredLevel")
                if okMin and tonumber(rMin) and tonumber(rMin) > 0 then
                    qdbMinLevel = tonumber(rMin)
                end
                local okLvl, rLvl = pcall(qdb.QueryQuestSingle, questId, "questLevel")
                if okLvl and tonumber(rLvl) and tonumber(rLvl) > 0 then
                    qdbQuestLevel = tonumber(rLvl)
                end
                if not (q and q.requiredSkill) then
                    local okSkill, rSkill = pcall(qdb.QueryQuestSingle, questId, "requiredSkill")
                    if okSkill and type(rSkill) == "table" then
                        qdbRequiredSkill = rSkill
                    end
                end
            end
        end

        local reqSkill = (q and q.requiredSkill) or qdbRequiredSkill
        if type(reqSkill) == "table" then
            local skCode, skMsg = IsRequiredSkillBlocked(reqSkill, prof, (baseBucket == nil) and reasonless or false)
            if skCode then
                if baseBucket then baseBucket[questId] = { skCode, skMsg } end
                return skCode, (reasonless and skCode or skMsg)
            end
        end

        local effectiveMinLevel = math.max((q and tonumber(q.minLevel)) or 1, qdbMinLevel or 1)
        if level < effectiveMinLevel then
            local msg = (baseBucket == nil and reasonless) and "min_level" or string.format("Requires Lv %d (currently Lv %d)", effectiveMinLevel, level)
            if baseBucket then baseBucket[questId] = { "min_level", msg } end
            return "min_level", (reasonless and "min_level" or msg)
        end

        effectiveQLevel = math.max((q and tonumber(q.questLevel)) or 0, qdbQuestLevel or 0)
        if baseBucket then
            baseBucket[questId] = effectiveQLevel
        end
    end

    if effectiveQLevel > 0 and effectiveQLevel > (level + maxOffset) then
        if reasonless then
            return "quest_level_too_high", "quest_level_too_high"
        end
        local sign = (maxOffset >= 0) and "+" or ""
        return "quest_level_too_high", string.format("Lv %d quest exceeds max level (Lv %d %s%d = %d)", effectiveQLevel, level, sign, maxOffset, level + maxOffset)
    end

    if skipEliteNow and action ~= "DUNGEON_PREP" and action ~= "DUNGEON_RUN" then
        local isEl
        if q then
            isEl = (not q.isDungeon) and ((q.isGroup == true) or (activeQuests ~= nil and activeQuests[questId] ~= nil and activeQuests[questId].isElite == true))
        else
            isEl = State.IsQuestElite(questId, nil)
        end
        if isEl then
            return "elite_solo", reasonless and "elite_solo" or "Group / Elite quest skipped while solo"
        end
    end

    if skipDungeonsNow then
        local isDg
        if action == "DUNGEON_PREP" or action == "DUNGEON_RUN" then
            isDg = true
        elseif q then
            isDg = (q.isDungeon == true) or (activeQuests ~= nil and activeQuests[questId] ~= nil and activeQuests[questId].isDungeon == true)
        else
            isDg = State.IsQuestDungeon(questId, nil)
        end
        if isDg then
            return "dungeon_skipped", reasonless and "dungeon_skipped" or "Dungeon quest skipped (Skip Dungeons enabled)"
        end
    end

    if postBucket then
        local pm = postBucket[questId]
        if pm == false then
            return nil, nil
        elseif type(pm) == "table" then
            return pm[1], (reasonless and pm[1] or pm[2])
        end
    end

    if q and not State.ArePrereqsMet(questId, extraCompleted, useBulk) then
        if (not postBucket) and reasonless then
            return "missing_prereq", "missing_prereq"
        end
        local missingParts = {}
        for _, pid in ipairs(q.prereqIds or {}) do
            local done = State.IsQuestCompleted(pid, useBulk) or (extraCompleted and extraCompleted[pid] == true)
            if not done then
                local pq = data.quests and data.quests[pid]
                missingParts[#missingParts + 1] = pq and string.format("%s (%d)", pq.name or "Quest", pid) or tostring(pid)
            end
        end
        local msg = "Missing prerequisite: " .. table.concat(missingParts, ", ")
        if postBucket then postBucket[questId] = { "missing_prereq", msg } end
        return "missing_prereq", (reasonless and "missing_prereq" or msg)
    end

    if shouldCheckIsDoable and qdb and qdb.IsDoable then
        if not qdbHasQuestChecked then
            local okN, qName = pcall(qdb.QueryQuestSingle, questId, "name")
            qdbHasQuest = (okN and qName ~= nil)
        end
        if qdbHasQuest then
            local okD, doable = pcall(qdb.IsDoable, questId)
            if okD and doable == false then
                local detail = nil
                if qdb.IsDoableVerbose then
                    local okV, msg = pcall(qdb.IsDoableVerbose, questId, false, true, true)
                    if okV and type(msg) == "string" and msg ~= "" then
                        detail = msg:gsub("[\r\n]+", " ")
                    end
                end
                local msg = detail and ("QuestieDB: " .. detail) or "Blocked by QuestieDB eligibility check"
                if postBucket then postBucket[questId] = { "questie_not_doable", msg } end
                return "questie_not_doable", msg
            end
        end
    end

    if postBucket then
        postBucket[questId] = false
    end
    return nil, nil
end

local function BuildResolvedPrereqStep(pq, status, targetStep, routeIdx, targetQuestId)
    local data = ns.GetData()
    local action = "ACCEPT"
    local pt = pq.starter
    if status == "ready" then
        action = "TURN_IN"
        pt = pq.turnin or pq.starter
    elseif status == "active" then
        action = "COMPLETE"
        pt = (pq.objectives and pq.objectives[1]) or pq.turnin or pq.starter
    end

    local zoneId = (pt and pt.zoneId) or pq.zoneId
    local zInfo = zoneId and data.zones and data.zones[zoneId]
    local zoneName = (zInfo and zInfo.name) or pq.zoneName or (targetStep and targetStep.zoneName) or "Unknown"
    local verb = (action == "TURN_IN" and "Turn In") or (action == "COMPLETE" and "Complete") or "Accept"

    return {
        index = routeIdx,
        action = action,
        questId = pq.id,
        questIds = { pq.id },
        title = string.format("%s Prereq: %s", verb, pq.name or ("Quest " .. tostring(pq.id))),
        zoneId = zoneId,
        zoneName = zoneName,
        x = pt and pt.x or nil,
        y = pt and pt.y or nil,
        npcId = pt and pt.id or nil,
        npcName = pt and pt.name or nil,
        travelNote = string.format("Prerequisite for %s", (targetStep and targetStep.title) or ("Quest #" .. tostring(targetQuestId))),
        why = string.format("Unlocks %s (prereq #%d)", (targetStep and targetStep.title) or tostring(targetQuestId), pq.id),
        estMinutes = 3.0,
        badges = {
            xp = pq.xp or 0,
            goldCopper = pq.goldCopper or 0,
            bagSlots = pq.bagSlots or 0,
            rogueScore = pq.rogueScore or 0,
        },
        isResolvedPrereq = true,
        parentTargetStepIndex = routeIdx,
        parentTargetQuestId = targetQuestId,
    }
end

function State.FindActionablePrereqStep(questId, targetStep, routeIdx, player, profileKey, toggles, depth, visited)
    depth = depth or 1
    visited = visited or {}
    if not questId or depth > MAX_PREREQ_DEPTH or visited[questId] then
        return nil
    end
    visited[questId] = true

    local data = ns.GetData()
    local q = data.quests and data.quests[questId]
    if not q or not q.prereqIds or #q.prereqIds == 0 then
        return nil
    end

    local isAny = (q.prereqMode == "any")
    local skippedSteps = ns.charDB and ns.charDB.skippedSteps or {}
    local skipEliteNow = State.ShouldSkipEliteNow(player, toggles)
    local skipDungeonsNow = State.ShouldSkipDungeonsNow(toggles)

    for _, pid in ipairs(q.prereqIds) do
        if not State.IsQuestCompleted(pid) then
            local pq = data.quests and data.quests[pid]
            if pq and not State.IsQuestExcluded(pid) then
                local pst = State.GetQuestStatus(pid)
                if pst == "ready" then
                    return BuildResolvedPrereqStep(pq, pst, targetStep, routeIdx, questId)
                elseif pst == "active" then
                    if not (skipEliteNow and State.IsQuestElite(pid, pq))
                        and not (skipDungeonsNow and State.IsQuestDungeon(pid, pq)) then
                        return BuildResolvedPrereqStep(pq, pst, targetStep, routeIdx, questId)
                    elseif not isAny then
                        return nil
                    end
                elseif not skippedSteps["prereq:" .. tostring(pid)] then
                    local pCode = State.GetQuestBlockReason(pid, pq.isDungeon and "DUNGEON_PREP" or "ACCEPT", player, nil, nil, toggles)
                    if pCode == nil then
                        return BuildResolvedPrereqStep(pq, "available", targetStep, routeIdx, questId)
                    elseif pCode == "missing_prereq" then
                        local nested = State.FindActionablePrereqStep(pid, targetStep, routeIdx, player, profileKey, toggles, depth + 1, visited)
                        if nested then
                            return nested
                        elseif not isAny then
                            return nil
                        end
                    elseif not isAny then
                        return nil
                    end
                elseif not isAny then
                    return nil
                end
            elseif not isAny then
                return nil
            end
        end
    end
    return nil
end

--------------------------------------------------------------------------------
-- Route Cursor, Auto-Advance & Deviation Handling
--------------------------------------------------------------------------------

local function CollectStepQuestIds(step)
    local out = {}
    if type(step.questIds) == "table" and #step.questIds > 0 then
        for _, qid in ipairs(step.questIds) do
            out[#out + 1] = qid
        end
    elseif step.questId then
        out[1] = step.questId
    end
    return out
end

function State.GetStepKey(step, idx, profileKey)
    profileKey = profileKey or (ns.charDB and ns.charDB.activeProfile) or "balanced"
    local stepIdx = step.index or idx or 0
    local action = step.action or "STEP"
    local ref = step.questId or step.dungeonId or step.zoneId or 0
    local entry = stepKeyCache[step]
    if entry and entry.profileKey == profileKey and entry.stepIdx == stepIdx and entry.action == action and entry.ref == ref then
        return entry.key
    end
    local key = string.format("%s:%s:%s:%s", profileKey, tostring(stepIdx), tostring(action), tostring(ref))
    if not entry then
        if perfScratch.stepKeyCount >= 4096 then
            stepKeyCache = {}
            perfScratch.stepKeyCount = 0
        end
        perfScratch.stepKeyCount = perfScratch.stepKeyCount + 1
        stepKeyCache[step] = {
            profileKey = profileKey,
            stepIdx = stepIdx,
            action = action,
            ref = ref,
            key = key,
        }
    else
        entry.profileKey = profileKey
        entry.stepIdx = stepIdx
        entry.action = action
        entry.ref = ref
        entry.key = key
    end
    return key
end

local function CheckStepStatuses(step, allowedStatuses, preferBulk)
    local qids = step.questIds
    if type(qids) == "table" and #qids > 0 then
        for i = 1, #qids do
            local qid = qids[i]
            if not State.IsQuestExcluded(qid, preferBulk) then
                local st = State.GetQuestStatus(qid, preferBulk)
                if not allowedStatuses[st] then
                    return false
                end
            end
        end
        return true
    elseif step.questId then
        local qid = step.questId
        if State.IsQuestExcluded(qid, preferBulk) then
            return true
        end
        return allowedStatuses[State.GetQuestStatus(qid, preferBulk)] == true
    end
    return false
end

function State.IsStepSkipped(step, idx, profileKey)
    local skippedTbl = ns.charDB and ns.charDB.skippedSteps
    if not skippedTbl or not next(skippedTbl) then return false end
    local key = State.GetStepKey(step, idx, profileKey)
    return skippedTbl[key] == true
end

function State.IsStepSatisfied(step, steps, idx, player, profileKey, preferBulk)
    if not step then return false end
    local customTbl = ns.charDB and ns.charDB.completedCustomSteps
    if customTbl and next(customTbl) then
        local key = State.GetStepKey(step, idx, profileKey)
        if customTbl[key] then
            return true
        end
    end

    local action = step.action or "ACCEPT"
    if action == "ACCEPT" then
        return CheckStepStatuses(step, SAT_ACCEPT, preferBulk)
    elseif action == "COMPLETE" then
        return CheckStepStatuses(step, SAT_COMPLETE, preferBulk)
    elseif action == "TURN_IN" then
        return CheckStepStatuses(step, SAT_TURNIN, preferBulk)
    elseif action == "TRAVEL" then
        player = player or playerCache or State.GetPlayerInfo()
        if IsPlayerLocationFrozen(player) then
            return false
        end
        if step.zoneId and player.zoneId == step.zoneId then
            return true
        end
        if step.zoneName and player.zoneName then
            if step.zoneName == player.zoneName then
                return true
            end
            if (not step.zoneId or not player.zoneId) and string.lower(step.zoneName) == string.lower(player.zoneName) then
                return true
            end
        end
        -- Also satisfy TRAVEL if the immediately following quest step at the destination is already in progress/done.
        if steps and idx then
            for look = idx + 1, math.min(#steps, idx + 3) do
                local nextStep = steps[look]
                if nextStep and nextStep.action ~= "TRAVEL" then
                    if step.zoneId and nextStep.zoneId == step.zoneId and State.IsStepSatisfied(nextStep, steps, look, player, profileKey, preferBulk) then
                        return true
                    end
                    break
                end
            end
        end
        return false
    elseif action == "DUNGEON_PREP" then
        if step.prepMode == "accept" then
            return CheckStepStatuses(step, SAT_ACCEPT, preferBulk)
        end
        return CheckStepStatuses(step, SAT_TURNIN, preferBulk)
    elseif action == "DUNGEON_RUN" then
        return CheckStepStatuses(step, SAT_COMPLETE, preferBulk)
    end
    return false
end

function State.IsStepSatisfiedFast(step, steps, idx, player, profileKey)
    return State.IsStepSatisfied(step, steps, idx, player, profileKey, true)
end

local function IsAcceptStepSkippedOrBlocked(s, i, profileKey, player, extraCompleted, extraActive, toggles, questId)
    if State.IsStepSkipped(s, i, profileKey) then
        return true
    end
    if (manualCursorIndex == nil or i > manualCursorIndex) and State.IsStepOutleveled(s, player) then
        return true
    end
    local checkQid = s.questId or questId
    if checkQid and State.GetQuestBlockReason(checkQid, s.action or "ACCEPT", player, extraCompleted, extraActive, toggles, FAST_BLOCK_OPTS) ~= nil then
        return true
    end
    return false
end

local function GetAcceptStepIndexMap(steps)
    local map = acceptIdxByStepsCache[steps]
    if map then
        return map
    end
    map = {}
    for k = 1, #steps do
        local s = steps[k]
        if s and (s.action == "ACCEPT" or (s.action == "DUNGEON_PREP" and s.prepMode == "accept")) then
            local sq = s.questIds
            if type(sq) == "table" and #sq > 0 then
                for j = 1, #sq do
                    local mq = sq[j]
                    local cur = map[mq]
                    if cur == nil then
                        map[mq] = k
                    elseif type(cur) == "number" then
                        map[mq] = { cur, k }
                    else
                        cur[#cur + 1] = k
                    end
                end
            elseif s.questId then
                local mq = s.questId
                local cur = map[mq]
                if cur == nil then
                    map[mq] = k
                elseif type(cur) == "number" then
                    map[mq] = { cur, k }
                else
                    cur[#cur + 1] = k
                end
            end
        end
    end
    if perfScratch.acceptIdxCount >= 16 then
        acceptIdxByStepsCache = {}
        perfScratch.acceptIdxCount = 0
    end
    perfScratch.acceptIdxCount = perfScratch.acceptIdxCount + 1
    acceptIdxByStepsCache[steps] = map
    return map
end

State.GetAcceptStepIndexMap = GetAcceptStepIndexMap

local function IsAcceptSkippedEarlier(questId, steps, idx, profileKey, player, extraCompleted, extraActive, toggles)
    if not questId or not steps or not idx then return false end
    local entry = GetAcceptStepIndexMap(steps)[questId]
    if not entry then
        return false
    end
    local minSearch = math.max(1, idx - 150)
    if type(entry) == "number" then
        return entry < idx and entry >= minSearch
            and IsAcceptStepSkippedOrBlocked(steps[entry], entry, profileKey, player, extraCompleted, extraActive, toggles, questId)
    end
    for p = #entry, 1, -1 do
        local i = entry[p]
        if i < idx and i >= minSearch
            and IsAcceptStepSkippedOrBlocked(steps[i], i, profileKey, player, extraCompleted, extraActive, toggles, questId) then
            return true
        end
    end
    return false
end

local function IsStepDirectBlocked(step, steps, idx, player, profileKey, extraCompleted, extraActive, toggles, opts)
    if not step then return nil, nil end
    local action = step.action or "ACCEPT"
    local qids = step.questIds
    local singleQid = step.questId
    if (type(qids) ~= "table" or #qids == 0) and not singleQid then
        return nil, nil
    end

    player = player or playerCache or State.GetPlayerInfo()
    toggles = type(toggles) == "table" and toggles or ((ns.charDB and ns.charDB.toggles) or {})
    local useBulk = (opts and opts.useBulkCompletion == true)
    local skipEliteNow = (opts and opts.skipEliteSolo ~= nil) and (opts.skipEliteSolo == true) or State.ShouldSkipEliteNow(player, toggles)
    local skipDungeonsNow = (opts and opts.skipDungeons ~= nil) and (opts.skipDungeons == true) or State.ShouldSkipDungeonsNow(toggles)

    local count = (type(qids) == "table" and #qids > 0) and #qids or 1
    if action == "ACCEPT" or (action == "DUNGEON_PREP" and step.prepMode == "accept") then
        for i = 1, count do
            local qid = qids and qids[i] or singleQid
            local code, reason = State.GetQuestBlockReason(qid, action, player, extraCompleted, extraActive, toggles, opts)
            if code then
                return code, reason
            end
        end
        return nil, nil
    end

    -- For COMPLETE / TURN_IN / DUNGEON_RUN / post-accept DUNGEON_PREP:
    -- If at least one non-blocked quest is already active/ready/completed, let player proceed;
    -- incomplete elite/dungeon quests in the quest log still block while enabled (unless already ready),
    -- DUNGEON_RUN blocks whenever any quest in the run is not completed/ready under skipDungeonsNow,
    -- and other steps block when EVERY incomplete quest is neither active/ready nor eligible/accepted.
    local data = ns.GetData()
    local dbQuests = data and data.quests
    local reasonless = (opts and opts.reasonless == true)
    if skipDungeonsNow and action == "DUNGEON_RUN" then
        for i = 1, count do
            local qid = qids and qids[i] or singleQid
            local isDone = State.IsQuestCompleted(qid, useBulk) or (extraCompleted and extraCompleted[qid] == true)
            if not isDone then
                local virtAct = extraActive and extraActive[qid]
                local liveAct = activeQuests[qid]
                local actRec = (virtAct ~= nil) and virtAct or liveAct
                local isReady = (type(actRec) == "table" and actRec.isComplete == true)
                    or (actRec ~= nil and type(actRec) ~= "table" and liveAct and liveAct.isComplete == true)
                if not isReady then
                    return "dungeon_skipped", reasonless and "dungeon_skipped" or "Dungeon quest skipped (Skip Dungeons enabled)"
                end
            end
        end
        return nil, nil
    end

    local anyInProgressOrDone = false
    local firstCode, firstReason = nil, nil
    for i = 1, count do
        local qid = qids and qids[i] or singleQid
        local isDone = State.IsQuestCompleted(qid, useBulk) or (extraCompleted and extraCompleted[qid] == true)
        local virtAct = extraActive and extraActive[qid]
        local liveAct = activeQuests[qid]
        local actRec = (virtAct ~= nil) and virtAct or liveAct
        local isReady = (type(actRec) == "table" and actRec.isComplete == true)
            or (actRec ~= nil and type(actRec) ~= "table" and liveAct and liveAct.isComplete == true)
        local st = isDone and "completed" or (isReady and "ready") or (actRec ~= nil and "active") or State.GetQuestStatus(qid, useBulk)
        if st == "completed" or st == "ready" then
            anyInProgressOrDone = true
        elseif st == "active" then
            if skipEliteNow and action ~= "DUNGEON_RUN" and action ~= "DUNGEON_PREP" and State.IsQuestElite(qid, dbQuests and dbQuests[qid]) then
                if not firstCode then
                    firstCode = "elite_solo"
                    firstReason = reasonless and "elite_solo" or "Group / Elite quest skipped while solo"
                end
            elseif skipDungeonsNow and State.IsQuestDungeon(qid, dbQuests and dbQuests[qid]) then
                if not firstCode then
                    firstCode = "dungeon_skipped"
                    firstReason = reasonless and "dungeon_skipped" or "Dungeon quest skipped (Skip Dungeons enabled)"
                end
            else
                anyInProgressOrDone = true
            end
        else
            local code, reason = State.GetQuestBlockReason(qid, action, player, extraCompleted, extraActive, toggles, opts)
            if not code and (action == "COMPLETE" or action == "TURN_IN") and IsAcceptSkippedEarlier(qid, steps, idx, profileKey, player, extraCompleted, extraActive, toggles) then
                code = "accept_skipped"
                reason = "Accept step for this quest was skipped, outleveled, or blocked and quest is not in log"
            end
            if code and not firstCode then
                firstCode, firstReason = code, reason
            end
        end
    end
    if not anyInProgressOrDone and firstCode then
        return firstCode, firstReason
    end
    return nil, nil
end

function State.IsStepBlocked(step, steps, idx, player, profileKey, extraCompleted, extraActive, toggles, opts)
    if not step then return nil, nil end
    local code, reason = IsStepDirectBlocked(step, steps, idx, player, profileKey, extraCompleted, extraActive, toggles, opts)
    if code then
        return code, reason
    end

    local useBulk = (opts and opts.useBulkCompletion == true)
    local reasonless = (opts and opts.reasonless == true)
    if (step.action == "TRAVEL") and step.zoneId and steps and idx then
        local pLv = (player and tonumber(player.level)) or 24
        local maxOff = State.GetEffectiveMaxLevelOffset(toggles)
        if step.expectedLevel and step.expectedLevel > (pLv + math.max(maxOff, 2) + 4) then
            return "quest_level_too_high", reasonless and nil or string.format("Travel destination is for Lv %d (currently Lv %d)", step.expectedLevel, pLv)
        end
        local skippedTbl = ns.charDB and ns.charDB.skippedSteps
        local hasSkipped = (type(skippedTbl) == "table" and next(skippedTbl) ~= nil)
        local foundAny = false
        local foundActionable = false
        local destZone = step.zoneId
        local maxLook = math.min(#steps, idx + 12)
        for look = idx + 1, maxLook do
            local nsStep = steps[look]
            if not nsStep then break end
            if nsStep.action ~= "TRAVEL" then
                if nsStep.zoneId == destZone then
                    foundAny = true
                    if not (hasSkipped and State.IsStepSkipped(nsStep, look, profileKey)) then
                        local done = useBulk
                            and State.IsStepSatisfiedFast(nsStep, steps, look, player, profileKey)
                            or State.IsStepSatisfied(nsStep, steps, look, player, profileKey, useBulk)
                        if (not done) and (not IsStepDirectBlocked(nsStep, steps, look, player, profileKey, extraCompleted, extraActive, toggles, opts)) then
                            foundActionable = true
                            break
                        end
                    end
                else
                    break
                end
            else
                if foundAny then
                    break
                end
                destZone = nsStep.zoneId
            end
        end
        if foundAny and not foundActionable then
            return "redundant_travel", reasonless and nil or "All steps in destination zone are satisfied, skipped, or blocked"
        end
    end

    return nil, nil
end

function State.IsStepBlockedFast(step, steps, idx, player, profileKey, toggles)
    return State.IsStepBlocked(step, steps, idx, player, profileKey, nil, nil, toggles, FAST_BLOCK_OPTS)
end

local function IsStepCharEligible(step, player, data)
    if not step then return false end
    if not player or not data or not data.quests then return true end
    local qid = step.questId
    if qid and data.quests[qid] then
        if not State.IsEligibleForPlayer(qid, player) then
            return false
        end
    end
    local qids = step.questIds
    if type(qids) == "table" and #qids > 0 then
        local anyOk = false
        for j = 1, #qids do
            local subQid = qids[j]
            if not data.quests[subQid] or State.IsEligibleForPlayer(subQid, player) then
                anyOk = true
                break
            end
        end
        if not anyOk then return false end
    end
    return true
end

local function ComposeRouteList(prefixSteps, tailSteps, minTailLevel, player, data)
    local out = {}
    if type(prefixSteps) == "table" then
        for i = 1, #prefixSteps do
            local st = prefixSteps[i]
            if IsStepCharEligible(st, player, data) then
                out[#out + 1] = st
            end
        end
    end
    if type(tailSteps) == "table" then
        for i = 1, #tailSteps do
            local st = tailSteps[i]
            if ((not minTailLevel) or ((st.expectedLevel or 0) >= minTailLevel))
                and IsStepCharEligible(st, player, data) then
                out[#out + 1] = st
            end
        end
    end
    return out
end

local function LookupStarterRoute(tbl, zId, faction)
    if type(tbl) ~= "table" or not zId then return nil end
    if faction then
        local fKeyNum = tostring(zId) .. "_" .. tostring(faction)
        if tbl[fKeyNum] then return tbl[fKeyNum] end
        local fKeyColon = tostring(zId) .. ":" .. tostring(faction)
        if tbl[fKeyColon] then return tbl[fKeyColon] end
    end
    local entry = tbl[zId] or tbl[tostring(zId)]
    if type(entry) == "table" and faction and type(entry[faction]) == "table" and type(entry[faction].steps) == "table" then
        return entry[faction]
    end
    return entry
end

function State.IsStepOutleveled(step, player)
    if not step or type(step.expectedLevel) ~= "number" then
        return false
    end
    player = player or playerCache or State.GetPlayerInfo()
    local pLevel = tonumber(player.level) or 1

    local qids = step.questIds
    if type(qids) == "table" and #qids > 0 then
        for i = 1, #qids do
            if activeQuests[qids[i]] ~= nil then
                return false
            end
        end
    elseif step.questId and activeQuests[step.questId] ~= nil then
        return false
    end

    local data = ns.GetData()
    local q = step.questId and data.quests and data.quests[step.questId] or nil
    local isDungeonQuest = (q and q.isDungeon == true) or (step.action == "DUNGEON_RUN")
    if q and not isDungeonQuest then
        local qLevel = tonumber(q.questLevel or q.minLevel) or step.expectedLevel
        if qLevel <= pLevel - 7 then
            return true
        end
    end
    if pLevel < step.expectedLevel + 2 then
        return false
    end

    -- Skip outleveled cross-zone non-dungeon steps when already 2+ levels ahead in another zone
    local locationFrozen = IsPlayerLocationFrozen(player)
    if (not locationFrozen) and (not isDungeonQuest) and step.zoneId and player.zoneId and step.zoneId ~= player.zoneId and pLevel >= step.expectedLevel + 2 then
        return true
    end

    if pLevel < step.expectedLevel + 3 then
        return false
    end

    local action = step.action or "ACCEPT"
    if action == "TRAVEL" then
        return (not locationFrozen) and (step.zoneId ~= nil) and (step.zoneId ~= player.zoneId)
    end
    if (not locationFrozen) and step.zoneId and player.zoneId and step.zoneId ~= player.zoneId and pLevel >= step.expectedLevel + 3 then
        return true
    end
    if q then
        if q.isDungeon and pLevel <= (q.questLevel or step.expectedLevel) + 8 then
            return false
        end
        local qLevel = tonumber(q.questLevel or q.minLevel) or step.expectedLevel
        if qLevel <= pLevel - 7 then
            return true
        end
    elseif pLevel >= step.expectedLevel + 7 then
        return true
    end
    return false
end

function State.GetActiveProfileSteps()
    local data = ns.GetData()
    if composedRoutesDataRef ~= data then
        composedRoutesCache = {}
        composedRoutesDataRef = data
    end

    local cdb = ns.charDB or {}
    local pkey = cdb.activeProfile or "balanced"
    if pkey == "custom" then
        pkey = "balanced"
    end

    local profile = data.profiles and data.profiles[pkey]
    if not profile and data.profiles then
        for k, v in pairs(data.profiles) do
            profile = v
            pkey = k
            break
        end
    end

    if type(pkey) == "string" and pkey:sub(1, 2) == "__" then
        return (profile and profile.steps) or {}, profile, pkey, false
    end

    local player = playerCache or State.GetPlayerInfo()
    local target = (data.meta and data.meta.targetCharacter) or {}
    local targetFaction = target.faction or "Alliance"
    local targetRace = target.race or "Gnome"
    local targetClass = string.upper(tostring(target.class or "ROGUE"))
    local faction = player.faction or "Alliance"
    local startZ = player.startZoneId or (FALLBACK_RACES[player.raceFile] and FALLBACK_RACES[player.raceFile].startZoneId)

    if faction == "Horde" then
        local cacheKey = string.format("H:%s:%d:%s:%s", tostring(startZ or 0), tonumber(player.raceId) or 0, tostring(player.classFile or ""), tostring(pkey))
        local cached = composedRoutesCache[cacheKey]
        if cached then
            return cached.steps, cached.wrapper, pkey, true
        end
        local starter = LookupStarterRoute(data.starterRoutes, startZ, faction)
        local spine = data.hordeRoutes and (data.hordeRoutes[pkey] or data.hordeRoutes.balanced or (data.hordeRoutes.steps and data.hordeRoutes))
        if starter or spine then
            local sSteps = starter and starter.steps or {}
            local hSteps = spine and spine.steps or {}
            local maxStarterLvl = (starter and tonumber(starter.maxLevel)) or 22
            local merged = ComposeRouteList(sSteps, hSteps, (#sSteps > 0) and math.max(18, maxStarterLvl - 1) or nil, player, data)
            local label = (starter and spine and ((starter.label or "Starter") .. " -> Horde")) or (spine and spine.label) or (starter and starter.label) or "Horde Route"
            local wrapper = { label = label, steps = merged, summary = (spine and spine.summary) or (starter and starter.summary) }
            composedRoutesCache[cacheKey] = { steps = merged, wrapper = wrapper }
            return merged, wrapper, pkey, true
        end
        return {}, nil, pkey, true
    end

    if faction ~= targetFaction or (player.raceFile ~= targetRace and startZ ~= target.startZoneId) then
        local starter = LookupStarterRoute(data.starterRoutes, startZ, faction)
        if starter and type(starter.steps) == "table" and #starter.steps > 0 then
            local cacheKey = string.format("A:%s:%d:%s:%s", tostring(startZ or 0), tonumber(player.raceId) or 0, tostring(player.classFile or ""), tostring(pkey))
            local cached = composedRoutesCache[cacheKey]
            if cached then
                return cached.steps, cached.wrapper, pkey, true
            end
            local maxStarterLvl = tonumber(starter.maxLevel) or 23
            local pSteps = profile and profile.steps or {}
            local merged = ComposeRouteList(starter.steps, pSteps, math.max(20, maxStarterLvl - 1), player, data)
            local wrapper = {
                label = (starter.label or "Starter") .. " -> " .. ns.GetPresetLabel(pkey),
                steps = merged,
                summary = profile and profile.summary,
            }
            composedRoutesCache[cacheKey] = { steps = merged, wrapper = wrapper }
            return merged, wrapper, pkey, true
        end
    end

    if player.raceFile ~= targetRace or player.classFile ~= targetClass then
        local cacheKey = string.format("AT:%d:%s:%s", tonumber(player.raceId) or 0, tostring(player.classFile or ""), tostring(pkey))
        local cached = composedRoutesCache[cacheKey]
        if cached then
            return cached.steps, cached.wrapper, pkey, true
        end
        local pSteps = profile and profile.steps or {}
        local filtered = ComposeRouteList(nil, pSteps, nil, player, data)
        local wrapper = {
            label = (profile and profile.label) or ns.GetPresetLabel(pkey),
            steps = filtered,
            summary = profile and profile.summary,
        }
        composedRoutesCache[cacheKey] = { steps = filtered, wrapper = wrapper }
        return filtered, wrapper, pkey, true
    end

    return (profile and profile.steps) or {}, profile, pkey, false
end

--------------------------------------------------------------------------------
-- Ready Turn-In Detection, Resolution & Route Injection
--------------------------------------------------------------------------------

local function IsReadyStepHandled(readyKey)
    if not readyKey or not ns.charDB then return false end
    if ns.charDB.skippedSteps and ns.charDB.skippedSteps[readyKey] then
        return true
    end
    if ns.charDB.completedCustomSteps and ns.charDB.completedCustomSteps[readyKey] then
        return true
    end
    return false
end

local function PickFirstPositive(...)
    for i = 1, select("#", ...) do
        local v = select(i, ...)
        if type(v) == "number" and v > 0 then
            return v
        end
    end
    return nil
end

local function ExtractTurninPoint(tbl, fallbackZoneId, fallbackZoneName)
    if type(tbl) ~= "table" then return nil end
    local x = tonumber(tbl.x or tbl[1])
    local y = tonumber(tbl.y or tbl[2])
    local zId = tonumber(tbl.zoneId) or fallbackZoneId
    local zName = tbl.zoneName or fallbackZoneName
    if x and y and (x > 0 or y > 0) then
        return {
            zoneId = zId,
            zoneName = zName,
            x = x,
            y = y,
            npcId = tonumber(tbl.npcId or tbl.id),
            npcName = tbl.npcName or tbl.name,
        }
    end
    if tbl.npcId or tbl.npcName or zId then
        return {
            zoneId = zId,
            zoneName = zName,
            x = x,
            y = y,
            npcId = tonumber(tbl.npcId or tbl.id),
            npcName = tbl.npcName or tbl.name,
        }
    end
    return nil
end

local function PickBestQuestieSpawn(spawns, preferredZoneId, playerZoneId, dataZones)
    if type(spawns) ~= "table" then return nil, nil, nil end
    local function firstXY(coords)
        if type(coords) ~= "table" or #coords == 0 then return nil, nil end
        local pair = coords[1]
        if type(pair) ~= "table" then return nil, nil end
        local x, y = tonumber(pair[1]), tonumber(pair[2])
        if x and y and (x >= 0 or y >= 0) then
            return x, y
        end
        return nil, nil
    end

    if playerZoneId and spawns[playerZoneId] then
        local x, y = firstXY(spawns[playerZoneId])
        if x and y then return playerZoneId, x, y end
    end
    if preferredZoneId and spawns[preferredZoneId] then
        local x, y = firstXY(spawns[preferredZoneId])
        if x and y then return preferredZoneId, x, y end
    end
    local bestAreaId, bestX, bestY
    local sortedAreas = {}
    for areaId in pairs(spawns) do
        if type(areaId) == "number" then
            sortedAreas[#sortedAreas + 1] = areaId
        end
    end
    table.sort(sortedAreas)
    for _, areaId in ipairs(sortedAreas) do
        local x, y = firstXY(spawns[areaId])
        if x and y then
            if dataZones and dataZones[areaId] then
                return areaId, x, y
            elseif not bestAreaId then
                bestAreaId, bestX, bestY = areaId, x, y
            end
        end
    end
    return bestAreaId, bestX, bestY
end

local function ResolveZoneNameById(zoneId, fallbackName, data)
    if fallbackName and fallbackName ~= "" then
        return fallbackName
    end
    if not zoneId then return nil end
    local z = data and data.zones and data.zones[zoneId]
    if z and z.name then
        return z.name
    end
    local uiMapId = AREA_TO_UIMAP[zoneId] or (data and data.meta and data.meta.zoneUiMapIds and data.meta.zoneUiMapIds[zoneId])
    if uiMapId and C_Map and C_Map.GetMapInfo then
        local ok, info = pcall(C_Map.GetMapInfo, uiMapId)
        if ok and info and info.name then
            return info.name
        end
    end
    return "Zone " .. tostring(zoneId)
end

function State.ResolveQuestTurnin(questId)
    if not questId then return nil end
    local data = ns.GetData()
    local player = playerCache or State.GetPlayerInfo()
    local qdb = GetQuestieDB()
    local cached = turninResolveCache[questId]
    if cached and (cached.hadCoords or (cached.checkedQdb == (qdb ~= nil))) then
        return cached.info
    end

    local q = data.quests and data.quests[questId]
    local activeEntry = activeQuests and activeQuests[questId]
    local qName = (q and q.name) or (activeEntry and activeEntry.title)
    local qLevel = (q and (q.questLevel or q.minLevel)) or (activeEntry and activeEntry.level)
    local qZoneId = (q and q.zoneId) or (activeEntry and activeEntry.zoneId)
    local qZoneName = q and q.zoneName

    local zId, zName, tx, ty, npcId, npcName
    local fromQuestie = false

    if q then
        local pt = ExtractTurninPoint(q.turnin, qZoneId, qZoneName)
            or ExtractTurninPoint(q.starter, qZoneId, qZoneName)
        if pt then
            zId = pt.zoneId
            zName = pt.zoneName
            tx = pt.x
            ty = pt.y
            npcId = pt.npcId
            npcName = pt.npcName
        end
        if (not tx or not ty) and type(q.objectives) == "table" then
            for _, obj in ipairs(q.objectives) do
                local opt = ExtractTurninPoint(obj, qZoneId, qZoneName)
                if opt and opt.x and opt.y then
                    tx, ty = tx or opt.x, ty or opt.y
                    zId = zId or opt.zoneId
                    zName = zName or opt.zoneName
                    break
                end
            end
        end
    end

    if qdb and ((not tx or not ty) or not npcName or not qName) then
        if not qName then
            local okN, dbName = pcall(qdb.QueryQuestSingle, questId, "name")
            if okN and type(dbName) == "string" and dbName ~= "" then
                qName = dbName
                fromQuestie = true
            end
        end
        if not qLevel then
            local okL, dbLevel = pcall(qdb.QueryQuestSingle, questId, "questLevel")
            if okL and type(dbLevel) == "number" and dbLevel > 0 then
                qLevel = dbLevel
            end
        end

        local function TryEntitySource(fieldName)
            local okF, fin = pcall(qdb.QueryQuestSingle, questId, fieldName)
            if not okF or type(fin) ~= "table" then return end
            -- fin[1] = NPC list, fin[2] = GameObject list
            if type(fin[1]) == "table" then
                for _, nid in ipairs(fin[1]) do
                    if type(nid) == "number" then
                        local okSp, spawns = pcall(qdb.QueryNPCSingle, nid, "spawns")
                        local szId, sx, sy = nil, nil, nil
                        if okSp and type(spawns) == "table" then
                            szId, sx, sy = PickBestQuestieSpawn(spawns, qZoneId, player and player.zoneId, data.zones)
                        end
                        local okNm, eName = pcall(qdb.QueryNPCSingle, nid, "name")
                        if sx and sy then
                            npcId = npcId or nid
                            npcName = npcName or (okNm and eName) or nil
                            zId = zId or szId
                            tx, ty = sx, sy
                            fromQuestie = true
                            return true
                        elseif not npcName and okNm and type(eName) == "string" then
                            npcId = npcId or nid
                            npcName = eName
                            fromQuestie = true
                        end
                    end
                end
            end
            if type(fin[2]) == "table" and qdb.QueryObjectSingle then
                for _, oid in ipairs(fin[2]) do
                    if type(oid) == "number" then
                        local okSp, spawns = pcall(qdb.QueryObjectSingle, oid, "spawns")
                        local szId, sx, sy = nil, nil, nil
                        if okSp and type(spawns) == "table" then
                            szId, sx, sy = PickBestQuestieSpawn(spawns, qZoneId, player and player.zoneId, data.zones)
                        end
                        local okNm, oName = pcall(qdb.QueryObjectSingle, oid, "name")
                        if sx and sy then
                            npcId = npcId or oid
                            npcName = npcName or (okNm and oName) or nil
                            zId = zId or szId
                            tx, ty = sx, sy
                            fromQuestie = true
                            return true
                        elseif not npcName and okNm and type(oName) == "string" then
                            npcId = npcId or oid
                            npcName = oName
                            fromQuestie = true
                        end
                    end
                end
            end
            return false
        end

        if not tx or not ty or not npcName then
            local resolved = TryEntitySource("finishedBy")
            if not resolved and (not tx or not ty) then
                TryEntitySource("startedBy")
            end
        end
    end

    zId = zId or qZoneId
    zName = ResolveZoneNameById(zId, zName or qZoneName, data)
    qName = qName or ("Quest #" .. tostring(questId))

    local info = {
        questId = questId,
        title = qName,
        level = qLevel,
        zoneId = zId,
        zoneName = zName,
        x = tx,
        y = ty,
        npcId = npcId,
        npcName = npcName,
        xp = (q and q.xp) or 0,
        money = (q and (q.goldCopper or q.money)) or 0,
        bagSlots = (q and q.bagSlots) or 0,
        rogueScore = (q and q.rogueScore) or 0,
        classBisScore = (q and q.classBisScore) or 0,
        dungeonId = q and q.dungeonId or nil,
        isDungeon = (q and q.isDungeon == true) or false,
        nextQuestIds = q and q.nextQuestIds or nil,
        inData = (q ~= nil),
        fromQuestie = fromQuestie,
        _readyKey = "ready_turnin:" .. tostring(questId),
    }

    turninResolveCache[questId] = {
        info = info,
        hadCoords = (tx ~= nil and ty ~= nil),
        checkedQdb = (qdb ~= nil),
    }
    return info
end

function perfScratch.SortReadyInfos(a, b)
    return (a.questId or 0) < (b.questId or 0)
end

function State.GetReadyTurnInQuests()
    local curGen = perfScratch.readySigGen
    local curQdb = GetQuestieDB()
    if perfScratch.cachedReadyQuestsGen == curGen
        and perfScratch.cachedReadyQuestsQdb == curQdb
        and perfScratch.cachedReadyQuests ~= nil then
        return perfScratch.cachedReadyQuests
    end
    local ready = {}
    for qid, entry in pairs(activeQuests or {}) do
        if entry and entry.isComplete == true and not entry.isFailed and not State.IsQuestCompleted(qid, true) then
            local info = State.ResolveQuestTurnin(qid)
            if info then
                ready[#ready + 1] = info
            end
        end
    end
    if #ready > 1 then
        table.sort(ready, perfScratch.SortReadyInfos)
    end
    perfScratch.cachedReadyQuests = ready
    perfScratch.cachedReadyQuestsGen = curGen
    perfScratch.cachedReadyQuestsQdb = curQdb
    return ready
end

function State.GetReadyTurnInCount()
    local curGen = perfScratch.readySigGen
    if perfScratch.cachedReadyCountGen == curGen then
        return perfScratch.cachedReadyCount
    end
    local n = 0
    for qid, entry in pairs(activeQuests or {}) do
        if entry and entry.isComplete == true and not entry.isFailed and not State.IsQuestCompleted(qid, true) then
            n = n + 1
        end
    end
    perfScratch.cachedReadyCount = n
    perfScratch.cachedReadyCountGen = curGen
    return n
end

local function TurninDist(x1, y1, x2, y2)
    if x1 and y1 and x2 and y2 then
        local dx = x1 - x2
        local dy = y1 - y2
        return math.sqrt(dx * dx + dy * dy)
    end
    return nil
end

local function RouteDistCoord(fromZoneId, x1, y1, toZoneId, x2, y2, player, questId, role)
    local qNum = tonumber(questId) or 0
    local navAvail = (ns.Nav and ns.Nav.GetTravelSeconds and ns.Nav.IsAvailable and ns.Nav.IsAvailable())
    local cacheBucket, k2 = nil, nil
    if navAvail and fromZoneId and x1 and y1 and toZoneId and x2 and y2
        and fromZoneId > 0 and fromZoneId < 32768
        and toZoneId > 0 and toZoneId < 32768
        and qNum >= 0 and qNum < 1048576 then
        local navEpoch = (ns.Nav.GetCacheEpoch and ns.Nav.GetCacheEpoch()) or 0
        local facBit = (player and player.faction == "Horde") and 1 or 0
        local mountBit = (IsMounted and IsMounted()) and 1 or 0
        local capSig = facBit * 2 + mountBit
        if staticDist.epoch ~= navEpoch or staticDist.cap ~= capSig or staticDist.navRef ~= ns.NavData or staticDist.count > 8192 then
            staticDist.map = {}
            staticDist.count = 0
            staticDist.epoch = navEpoch
            staticDist.cap = capSig
            staticDist.navRef = ns.NavData
        end
        local qx1 = math.max(0, math.min(1023, math.floor(x1 * 10 + 0.5)))
        local qy1 = math.max(0, math.min(1023, math.floor(y1 * 10 + 0.5)))
        local qx2 = math.max(0, math.min(1023, math.floor(x2 * 10 + 0.5)))
        local qy2 = math.max(0, math.min(1023, math.floor(y2 * 10 + 0.5)))
        local rCode = ROLE_DIST_CODE[role or "s"] or 1
        local k1 = (((fromZoneId * 32768 + toZoneId) * 1024 + qx1) * 1024) + qy1
        k2 = (((qx2 * 1024 + qy2) * 4 + rCode) * 1048576) + qNum
        cacheBucket = staticDist.map[k1]
        if cacheBucket then
            local hit = cacheBucket[k2]
            if hit ~= nil then
                return hit or nil
            end
        else
            cacheBucket = {}
            staticDist.map[k1] = cacheBucket
        end
    end

    local res = nil
    if fromZoneId and x1 and y1 and toZoneId and x2 and y2 and navAvail then
        local opts = nil
        if questId or role then
            if not State._distOptsScratch then State._distOptsScratch = {} end
            opts = State._distOptsScratch
            opts.questId = questId
            opts.role = role or "s"
            opts.unmountedOnly = true
            opts.allowHearth = false
        else
            if not State._distPlainOpts then
                State._distPlainOpts = { unmountedOnly = true, allowHearth = false }
            end
            opts = State._distPlainOpts
        end
        local sec, ok = ns.Nav.GetTravelSeconds(fromZoneId, x1, y1, toZoneId, x2, y2, player, opts)
        if ok and sec then
            res = sec / COORD_SEC_SCALE
        end
    end
    if not res then
        if fromZoneId and toZoneId and fromZoneId ~= toZoneId then
            res = 999
        else
            res = TurninDist(x1, y1, x2, y2)
        end
    end
    if cacheBucket then
        cacheBucket[k2] = res or false
        staticDist.count = staticDist.count + 1
    end
    return res
end

local function SortTurninsNearestNeighbor(items, startX, startY, startZoneId, player)
    if #items <= 1 then
        return items
    end
    local remaining = {}
    for i = 1, #items do
        remaining[i] = items[i]
    end
    local ordered = {}
    local curX, curY = startX, startY
    local curZone = startZoneId
    while #remaining > 0 do
        local bestIdx = 1
        local bestDist = math.huge
        for i = 1, #remaining do
            local it = remaining[i]
            local itZone = it.zoneId or curZone
            local d = RouteDistCoord(curZone, curX, curY, itZone, it.x, it.y, player, it.questId, "t")
            local scoreDist = d or 9999
            if scoreDist < bestDist - 0.001 then
                bestDist = scoreDist
                bestIdx = i
            elseif math.abs(scoreDist - bestDist) <= 0.001 then
                local bestItem = remaining[bestIdx]
                local npcA = it.npcId or 9999999
                local npcB = bestItem.npcId or 9999999
                if npcA < npcB or (npcA == npcB and (it.questId or 0) < (bestItem.questId or 0)) then
                    bestIdx = i
                end
            end
        end
        local chosen = table.remove(remaining, bestIdx)
        ordered[#ordered + 1] = chosen
        if chosen.x and chosen.y then
            curX, curY = chosen.x, chosen.y
            curZone = chosen.zoneId or curZone
        end
    end
    return ordered
end

local function MakeReadyBadges(info, data, batchCount)
    local q = data and data.quests and data.quests[info.questId]
    local dName = nil
    if q and q.dungeonUnlocks and #q.dungeonUnlocks > 0 then
        local dId = q.dungeonUnlocks[1]
        local d = data.dungeons and data.dungeons[dId]
        dName = d and d.name or tostring(dId)
    end
    return {
        isReadyTurnIn = true,
        readyBatchCount = batchCount or 1,
        xp = info.xp or 0,
        goldCopper = info.money or 0,
        bagSlots = info.bagSlots or 0,
        rogueScore = info.rogueScore or 0,
        classScore = info.classBisScore or info.rogueScore or 0,
        isLeatherworking = q and q.isLeatherworking or false,
        isSkinning = q and q.isSkinning or false,
        skinningScore = q and q.skinningScore or 0,
        requiredSkill = q and q.requiredSkill or nil,
        unlocksDungeon = dName,
    }
end

local function BuildInjectedTurninStep(info, placementReason, batchCount, data)
    local cacheKey = tostring(batchCount or 1) .. ":" .. tostring(placementReason or "")
    local stepCache = info._stepCache
    if stepCache then
        local hit = stepCache[cacheKey]
        if hit then
            return hit
        end
    else
        stepCache = {}
        info._stepCache = stepCache
    end
    local why
    if (batchCount or 1) > 1 then
        why = string.format("Ready in quest log — %s (%d quests grouped at this hub)", placementReason or "optimal turn-in", batchCount)
    else
        why = string.format("Ready in quest log — %s", placementReason or "ready to turn in")
    end
    local built = {
        action = "TURN_IN",
        questId = info.questId,
        questIds = { info.questId },
        title = info.title,
        zoneId = info.zoneId,
        zoneName = info.zoneName,
        x = info.x,
        y = info.y,
        npcId = info.npcId,
        npcName = info.npcName,
        expectedLevel = info.level,
        xp = info.xp or 0,
        money = info.money or 0,
        bagSlots = info.bagSlots or 0,
        rogueScore = info.rogueScore or 0,
        classBisScore = info.classBisScore or 0,
        dungeonId = info.dungeonId,
        badges = MakeReadyBadges(info, data, batchCount),
        travelNote = info.npcName and string.format("Turn in to %s%s", info.npcName, info.zoneName and (" in " .. info.zoneName) or "") or nil,
        why = why,
        isReadyTurnIn = true,
        isDynamic = true,
        readyStepKey = info._readyKey or ("ready_turnin:" .. tostring(info.questId)),
        readyPlacement = placementReason,
    }
    stepCache[cacheKey] = built
    return built
end

local function BuildInjectedTravelStep(zoneId, zoneName, firstInfo, batchItems, fromZoneName, player)
    local names = {}
    for i = 1, math.min(#batchItems, 3) do
        names[#names + 1] = batchItems[i].title or ("#" .. tostring(batchItems[i].questId))
    end
    local extra = (#batchItems > 3) and string.format(" (+%d more)", #batchItems - 3) or ""
    local travelNote = string.format("Batched %s turn-in trip%s", zoneName or "zone", fromZoneName and (" from " .. fromZoneName) or "")
    local estMinutes = nil
    local tx = firstInfo and firstInfo.x or nil
    local ty = firstInfo and firstInfo.y or nil
    if (not IsPlayerLocationFrozen(player)) and player and player.zoneId and player.x and player.y and zoneId and tx and ty
        and ns.Nav and ns.Nav.ResolveStepHop and ns.Nav.IsAvailable and ns.Nav.IsAvailable() then
        local hop = ns.Nav.ResolveStepHop(player, {
            questId = firstInfo and firstInfo.questId,
            action = "TRAVEL",
            zoneId = zoneId,
            x = tx,
            y = ty,
        })
        if hop and hop.reachable and hop.totalSec then
            estMinutes = math.max(0.1, math.floor((hop.totalSec / 60.0) * 10 + 0.5) / 10)
            if hop.hopNote and hop.hopNote ~= "" then
                travelNote = hop.hopNote .. " · " .. travelNote
            end
        end
    end
    return {
        action = "TRAVEL",
        zoneId = zoneId,
        zoneName = zoneName or ("Zone " .. tostring(zoneId)),
        title = string.format("Travel to %s (%d Ready Turn-Ins)", zoneName or ("Zone " .. tostring(zoneId)), #batchItems),
        x = tx,
        y = ty,
        npcName = firstInfo and firstInfo.npcName or nil,
        estMinutes = estMinutes,
        badges = {
            isReadyTurnIn = true,
            readyBatchCount = #batchItems,
        },
        travelNote = travelNote,
        why = string.format("Batched %d completed quests in %s: %s%s", #batchItems, zoneName or "destination", table.concat(names, ", "), extra),
        isReadyTurnIn = true,
        isDynamic = true,
        readyStepKey = "ready_travel:" .. tostring(zoneId),
    }
end

local function CloneDecoratedReadyStep(step, readyInfo)
    local copy = {}
    for k, v in pairs(step) do
        copy[k] = v
    end
    copy.isReadyTurnIn = true
    copy.readyStepKey = "ready_turnin:" .. tostring(readyInfo.questId)
    local badgesCopy = {}
    if type(step.badges) == "table" then
        for k, v in pairs(step.badges) do
            badgesCopy[k] = v
        end
    end
    badgesCopy.isReadyTurnIn = true
    copy.badges = badgesCopy
    local baseWhy = step.why and (step.why .. " ") or ""
    if not baseWhy:find("Ready in quest log", 1, true) then
        copy.why = baseWhy .. "(Ready in quest log — aligned at this hub)"
    end
    return copy
end

function State.GetReadyTurnInSignature()
    if perfScratch.cachedReadySigGen == perfScratch.readySigGen and perfScratch.cachedReadySig ~= nil then
        return perfScratch.cachedReadySig
    end
    local completed = State.GetCompletedReadOnly and State.GetCompletedReadOnly() or nil
    local qids = perfScratch.sigQids
    local n = 0
    for qid, entry in pairs(activeQuests or {}) do
        if entry and entry.isComplete == true and not entry.isFailed
            and not (completed and completed[qid])
            and not State.IsQuestCompleted(qid, true) then
            n = n + 1
            qids[n] = qid
        end
    end
    if n == 0 then
        perfScratch.cachedReadySigGen = perfScratch.readySigGen
        perfScratch.cachedReadySig = ""
        return ""
    end
    for i = #qids, n + 1, -1 do qids[i] = nil end
    if n > 1 then table.sort(qids) end
    local skipped = ns.charDB and ns.charDB.skippedSteps
    local doneCustom = ns.charDB and ns.charDB.completedCustomSteps
    local parts = perfScratch.sigParts
    for i = 1, n do
        local qid = qids[i]
        local k = "ready_turnin:" .. tostring(qid)
        local tag = ((skipped and skipped[k]) or (doneCustom and doneCustom[k])) and "S" or "R"
        parts[i] = tostring(qid) .. ":" .. tag
    end
    local sig = table.concat(parts, ",", 1, n)
    perfScratch.cachedReadySigGen = perfScratch.readySigGen
    perfScratch.cachedReadySig = sig
    return sig
end

function State.GetRouteReadySignature()
    if perfScratch.cachedRouteReadySigGen == perfScratch.readySigGen and perfScratch.cachedRouteReadySig ~= nil then
        return perfScratch.cachedRouteReadySig
    end
    local data = ns.GetData and ns.GetData()
    local quests = data and data.quests
    local completed = State.GetCompletedReadOnly and State.GetCompletedReadOnly() or nil
    local qids = perfScratch.sigQids
    local n = 0
    for qid, entry in pairs(activeQuests or {}) do
        if entry and entry.isComplete == true and not entry.isFailed
            and not (completed and completed[qid])
            and not State.IsQuestCompleted(qid, true) then
            local inRouteDb = (quests and quests[qid] ~= nil)
            local hasZone = false
            if not inRouteDb then
                local info = State.ResolveQuestTurnin(qid)
                hasZone = (info ~= nil and info.zoneId ~= nil)
            end
            if inRouteDb or hasZone then
                n = n + 1
                qids[n] = qid
            end
        end
    end
    if n == 0 then
        perfScratch.cachedRouteReadySigGen = perfScratch.readySigGen
        perfScratch.cachedRouteReadySig = ""
        return ""
    end
    for i = #qids, n + 1, -1 do qids[i] = nil end
    if n > 1 then table.sort(qids) end
    local skipped = ns.charDB and ns.charDB.skippedSteps
    local doneCustom = ns.charDB and ns.charDB.completedCustomSteps
    local parts = perfScratch.sigParts
    for i = 1, n do
        local qid = qids[i]
        local k = "ready_turnin:" .. tostring(qid)
        local tag = ((skipped and skipped[k]) or (doneCustom and doneCustom[k])) and "S" or "R"
        parts[i] = tostring(qid) .. ":" .. tag
    end
    local sig = table.concat(parts, ",", 1, n)
    perfScratch.cachedRouteReadySigGen = perfScratch.readySigGen
    perfScratch.cachedRouteReadySig = sig
    return sig
end

function perfScratch.CheckInlineReadyElig(qid, q, isVirtExcl, player, pLevel, maxQuestLv, vSimCompleted, completedMap, checkCompSlow, skipEliteNow, skipDungeonsNow)
    if isVirtExcl then
        return "exclusive_taken"
    end
    if not State.IsEligibleForPlayer(qid, player) then
        return "ineligible_character"
    end
    if (tonumber(q.minLevel) or 1) > pLevel then
        return "min_level"
    end
    if (tonumber(q.questLevel) or 0) > maxQuestLv then
        return "quest_level_too_high"
    end
    if skipEliteNow and q.isGroup == true and not q.isDungeon then
        return "elite_solo"
    end
    if skipDungeonsNow and q.isDungeon == true then
        return "dungeon_skipped"
    end
    if q.prereqIds and #q.prereqIds > 0 then
        local pids = q.prereqIds
        if q.prereqMode == "any" then
            local anyDone = false
            for pi = 1, #pids do
                local pid = pids[pi]
                if vSimCompleted[pid] or (completedMap[pid] == true) or (checkCompSlow ~= nil and checkCompSlow(pid, true)) then
                    anyDone = true
                    break
                end
            end
            if not anyDone then
                return "missing_prereq"
            end
        else
            for pi = 1, #pids do
                local pid = pids[pi]
                if not (vSimCompleted[pid] or (completedMap[pid] == true) or (checkCompSlow ~= nil and checkCompSlow(pid, true))) then
                    return "missing_prereq"
                end
            end
        end
    end
    return nil
end

function perfScratch.GetAnchorForPending(i, rs, steps, player, navAvail, curMounted)
    local anchorByIdx = rs.anchorByIdx
    local cached = anchorByIdx[i]
    if cached ~= nil then
        return cached
    end
    local info = rs.pending[i]
    local exIdx = rs.existingTurninIdx[info.questId]
    local anchorIdx = nil
    local rsd = perfScratch.readyStepDist
    local rFac = player and player.faction or "Alliance"
    local rMounted = curMounted and true or false
    if rsd.stepsRef ~= steps or rsd.navAvail ~= navAvail or rsd.faction ~= rFac or rsd.mounted ~= rMounted or rsd.count > 8192 then
        rsd.map = {}
        rsd.stepsRef = steps
        rsd.navAvail = navAvail
        rsd.faction = rFac
        rsd.mounted = rMounted
        rsd.count = 0
    end
    local rsdMap = rsd.map
    local iQid = tonumber(info.questId) or 0
    local validKeyRange = (iQid > 0 and iQid < 1048576)
    if info.zoneId then
        local anchorMemo = rs.anchorMemo
        local aKey = tostring(info.questId) .. ":" .. tostring(info.zoneId or 0) .. ":" .. tostring(info.x or 0) .. ":" .. tostring(info.y or 0)
        local memoed = anchorMemo[aKey]
        if memoed ~= nil then
            anchorIdx = memoed or nil
        else
            local scanCount = rs.scanCount
            local scanIdxs = rs.scanIdxs
            local scanStepList = rs.scanStepList
            local bestHubIdx, bestHubScore = nil, math.huge
            local bestTurninIdx, bestTurninDist = nil, math.huge
            local firstZoneStepIdx = nil
            local afterTravelIdx = nil
            for k = 1, scanCount do
                local eIdx = scanIdxs[k]
                local st = scanStepList[k]
                if st.zoneId == info.zoneId then
                    if st.action == "TRAVEL" then
                        afterTravelIdx = eIdx
                    else
                        if not firstZoneStepIdx then
                            firstZoneStepIdx = eIdx
                        end
                        local d = nil
                        if info.x and info.y and st.x and st.y then
                            if validKeyRange and eIdx > 0 and eIdx < 32768 then
                                local dKey = eIdx * 1048576 + iQid
                                local cachedD = rsdMap[dKey]
                                if cachedD ~= nil then
                                    d = (cachedD ~= false) and cachedD or nil
                                else
                                    d = RouteDistCoord(st.zoneId, st.x, st.y, info.zoneId, info.x, info.y, player, info.questId, "t")
                                    rsdMap[dKey] = d or false
                                    rsd.count = rsd.count + 1
                                end
                            else
                                d = RouteDistCoord(st.zoneId, st.x, st.y, info.zoneId, info.x, info.y, player, info.questId, "t")
                            end
                        end
                        if d and d <= NEAR_READY_TURNIN_DIST then
                            local penalty = (st.action == "TURN_IN") and 0 or 5
                            local score = d + penalty
                            if score < bestHubScore then
                                bestHubScore = score
                                bestHubIdx = eIdx
                            end
                        end
                        if st.action == "TURN_IN" then
                            local td = d or 50
                            if td < bestTurninDist then
                                bestTurninDist = td
                                bestTurninIdx = eIdx
                            end
                        end
                    end
                end
            end
            anchorIdx = bestHubIdx or bestTurninIdx or firstZoneStepIdx or afterTravelIdx
            anchorMemo[aKey] = anchorIdx or false
        end
    end
    local exAlreadyOptimal = false
    if anchorIdx and exIdx then
        local exStep = steps[exIdx]
        local hubDist = 0
        if exStep and exStep.x and exStep.y and info.x and info.y then
            if validKeyRange and exIdx > 0 and exIdx < 32768 and exStep.zoneId == info.zoneId then
                local dKey = exIdx * 1048576 + iQid
                local cachedD = rsdMap[dKey]
                if cachedD ~= nil then
                    hubDist = (cachedD ~= false) and cachedD or 0
                else
                    local cd = RouteDistCoord(exStep.zoneId or info.zoneId, exStep.x, exStep.y, info.zoneId, info.x, info.y, player, info.questId, "t")
                    rsdMap[dKey] = cd or false
                    rsd.count = rsd.count + 1
                    hubDist = cd or 0
                end
            else
                hubDist = RouteDistCoord(exStep.zoneId or info.zoneId, exStep.x, exStep.y, info.zoneId, info.x, info.y, player, info.questId, "t") or 0
            end
        end
        if math.abs(exIdx - anchorIdx) <= 6 and hubDist <= NEAR_READY_TURNIN_DIST then
            exAlreadyOptimal = true
        end
    end
    cached = {
        anchorIdx = anchorIdx,
        exAlreadyOptimal = exAlreadyOptimal,
    }
    anchorByIdx[i] = cached
    return cached
end

function perfScratch.MatchesImmediateAnchor(i, info, allowPlayerAnchor, pZone, pX, pY, player, curStep, curStepNearByIdx)
    if allowPlayerAnchor and info.zoneId and pZone and info.zoneId == pZone then
        if info.x and info.y and pX and pY then
            local d = RouteDistCoord(pZone, pX, pY, info.zoneId, info.x, info.y, player, info.questId, "t")
            if d and d <= NEAR_READY_TURNIN_DIST then
                return true
            end
        elseif (not curStep) or (curStep.zoneId ~= pZone) then
            return true
        end
    end
    if curStepNearByIdx[i] then
        return true
    end
    return false
end

function perfScratch.SortReadyZones(a, b)
    local pz = perfScratch.readySortPZone
    if a == pz and b ~= pz then return true end
    if b == pz and a ~= pz then return false end
    local sameReg = perfScratch.readyZoneSameReg
    local sa = sameReg[a] and true or false
    local sb = sameReg[b] and true or false
    if sa ~= sb then
        return sa
    end
    local zd = perfScratch.readyZoneDist
    local da = zd[a] or 999999
    local db = zd[b] or 999999
    if da ~= db then
        return da < db
    end
    return (tonumber(a) or 0) < (tonumber(b) or 0)
end

function State.GetReadyTurnInPlan(steps, cursor, player, pkey, toggles)
    steps = steps or {}
    player = player or playerCache or State.GetPlayerInfo()
    toggles = toggles or (ns.charDB and ns.charDB.toggles) or {}
    local useDynamic = (toggles.dynamicRescore == true) or (ns.charDB and ns.charDB.activeProfile == "custom")

    local cx = player and player.x and math.floor(player.x * 0.25) or -1
    local cy = player and player.y and math.floor(player.y * 0.25) or -1
    if readyPlanCache
        and readyPlanCache.gen == refreshGeneration
        and readyPlanCache.pkey == pkey
        and readyPlanCache.cursor == cursor
        and readyPlanCache.zoneId == (player and player.zoneId)
        and readyPlanCache.cx == cx
        and readyPlanCache.cy == cy
        and readyPlanCache.useDynamic == useDynamic then
        return readyPlanCache.plan
    end

    local readyList = State.GetReadyTurnInQuests()
    local readyCount = #readyList
    if readyCount == 0 then
        local plan = perfScratch.emptyReadyPlan
        if not plan then
            plan = {
                readyCount = 0,
                readyQuests = readyList,
                immediate = {},
                routeBefore = {},
                routeAfter = {},
                suppressedRouteIdx = {},
                decorateRouteIdx = {},
            }
            perfScratch.emptyReadyPlan = plan
        else
            plan.readyQuests = readyList
        end
        if not readyPlanCache then
            readyPlanCache = {}
        end
        readyPlanCache.gen = refreshGeneration
        readyPlanCache.pkey = pkey
        readyPlanCache.cursor = cursor
        readyPlanCache.zoneId = player and player.zoneId
        readyPlanCache.cx = cx
        readyPlanCache.cy = cy
        readyPlanCache.useDynamic = useDynamic
        readyPlanCache.plan = plan
        return plan
    end

    local data = ns.GetData()
    local autoAdvance = (toggles.autoAdvance ~= false)
    local pZone = player and player.zoneId
    local pX = player and player.x
    local pY = player and player.y
    local pLevel = player and player.level
    local curStep = (cursor and cursor <= #steps) and steps[cursor] or nil
    local maxOff = State.GetEffectiveMaxLevelOffset(toggles)
    local navAvail = (ns.Nav and ns.Nav.IsAvailable and ns.Nav.IsAvailable()) and true or false
    local sGen = questStateGeneration
    local dbGen = dbQuestStateGeneration
    local raceId = player and player.raceId
    local classId = player and player.classId
    local faction = player and player.faction
    local curMounted = (IsMounted and IsMounted()) and true or false

    -- Compute ready signature from already-built & sorted readyList
    local skippedSteps = ns.charDB and ns.charDB.skippedSteps
    local doneCustomSteps = ns.charDB and ns.charDB.completedCustomSteps
    local sigParts = perfScratch.readySigParts
    for i = 1, readyCount do
        local info = readyList[i]
        local k = info._readyKey or ("ready_turnin:" .. tostring(info.questId))
        local handled = (skippedSteps and skippedSteps[k]) or (doneCustomSteps and doneCustomSteps[k])
        sigParts[i] = tostring(info.questId) .. (handled and ":S" or ":R")
    end
    local readySig = table.concat(sigParts, ",", 1, readyCount)

    local consultQuestie = (toggles.consultQuestie ~= false)
    local skipEliteNow = State.ShouldSkipEliteNow(player, toggles)
    local skipDungeonsNow = State.ShouldSkipDungeonsNow(toggles)
    local rs = perfScratch.readyStaticCache
    if not (rs
        and rs.sGen == sGen
        and rs.dbGen == dbGen
        and rs.pkey == pkey
        and rs.stepsRef == steps
        and rs.cursor == cursor
        and rs.pZone == pZone
        and rs.level == pLevel
        and rs.maxOff == maxOff
        and rs.autoAdvance == autoAdvance
        and rs.manualCursor == manualCursorIndex
        and rs.readySig == readySig
        and rs.raceId == raceId
        and rs.classId == classId
        and rs.faction == faction
        and rs.consultQuestie == consultQuestie
        and rs.skipEliteNow == skipEliteNow
        and rs.skipDungeonsNow == skipDungeonsNow
        and rs.useDynamic == useDynamic
        and rs.navAvail == navAvail
        and rs.mounted == curMounted) then

        local rCtx = perfScratch.readyScanCtx
        if not (rCtx
            and rCtx.dbGen == dbGen
            and rCtx.pkey == pkey
            and rCtx.stepsRef == steps
            and rCtx.pZone == pZone
            and rCtx.level == pLevel
            and rCtx.autoAdvance == autoAdvance
            and rCtx.manualCursor == manualCursorIndex
            and rCtx.raceId == raceId
            and rCtx.classId == classId
            and rCtx.faction == faction
            and rCtx.consultQuestie == consultQuestie
            and rCtx.skipEliteNow == skipEliteNow
            and rCtx.skipDungeonsNow == skipDungeonsNow
            and rCtx.useDynamic == useDynamic
            and rCtx.navAvail == navAvail
            and rCtx.mounted == curMounted
            and rCtx.readySig == readySig) then

            -- Partition actionable (non-skipped) ready quests & precompute hub edges once across cursors
            local hdrPending = {}
            for _, info in ipairs(readyList) do
                local rKey = info._readyKey or ("ready_turnin:" .. tostring(info.questId))
                if not IsReadyStepHandled(rKey) then
                    hdrPending[#hdrPending + 1] = info
                end
            end

            local hdrHubEdges = {}
            for i = 1, #hdrPending do
                local info = hdrPending[i]
                if info.x and info.y and info.zoneId then
                    local edges = nil
                    for j = 1, #hdrPending do
                        if i ~= j then
                            local other = hdrPending[j]
                            if other.zoneId == info.zoneId and other.x and other.y then
                                local cd = RouteDistCoord(info.zoneId, info.x, info.y, other.zoneId, other.x, other.y, player, other.questId, "t")
                                if cd and cd <= HUB_CLUSTER_DIST then
                                    if not edges then edges = {} end
                                    edges[#edges + 1] = j
                                end
                            end
                        end
                    end
                    hdrHubEdges[i] = edges
                end
            end

            rCtx = {
                dbGen = dbGen,
                pkey = pkey,
                stepsRef = steps,
                pZone = pZone,
                level = pLevel,
                autoAdvance = autoAdvance,
                manualCursor = manualCursorIndex,
                raceId = raceId,
                classId = classId,
                faction = faction,
                consultQuestie = consultQuestie,
                skipEliteNow = skipEliteNow,
                skipDungeonsNow = skipDungeonsNow,
                useDynamic = useDynamic,
                navAvail = navAvail,
                mounted = curMounted,
                readySig = readySig,
                pending = hdrPending,
                hubEdges = hdrHubEdges,
                byCursor = {},
            }
            perfScratch.readyScanCtx = rCtx
        end

        local curKey = cursor or 0
        local cSlot = rCtx.byCursor[curKey]
        if not cSlot then
            local curNear = {}
            local pendList = rCtx.pending
            for i = 1, #pendList do
                local info = pendList[i]
                if curStep and curStep.action ~= "TRAVEL" and curStep.zoneId == info.zoneId and curStep.x and curStep.y and info.x and info.y then
                    local d = RouteDistCoord(curStep.zoneId, curStep.x, curStep.y, info.zoneId, info.x, info.y, player, info.questId, "t")
                    if d and d <= NEAR_READY_TURNIN_DIST then
                        curNear[i] = true
                    end
                end
            end
            cSlot = {
                curStepNearByIdx = curNear,
                scanByOff = {},
                rsByOff = {},
            }
            rCtx.byCursor[curKey] = cSlot
        end

        rs = cSlot.rsByOff[maxOff]
        if rs then
            rs.sGen = sGen
            perfScratch.readyStaticCache = rs
        else
            local rsc = cSlot.scanByOff[maxOff]
            if not rsc then
                -- Single bounded scan (<= READY_ZONE_SCAN_CAP = 140 steps, pruned to last relevant zone/turn-in)
                -- to build hub anchors, existing route TURN_IN map, and contiguous local zone cluster boundary.
                local scanStartIdx = cursor or 1
                local scanLimit = math.min(#steps, scanStartIdx + READY_ZONE_SCAN_CAP)
                local lastRelevantIdx = scanStartIdx - 1
                do
                    local pendingQids = WipeMap(perfScratch.readyPendQids)
                    local pendingZones = WipeMap(perfScratch.readyPendZones)
                    local hasNoZonePending = false
                    for i = 1, readyCount do
                        local info = readyList[i]
                        if info.questId then
                            pendingQids[info.questId] = true
                        end
                        if info.zoneId then
                            pendingZones[info.zoneId] = true
                        else
                            hasNoZonePending = true
                        end
                    end
                    if hasNoZonePending then
                        lastRelevantIdx = scanLimit
                    else
                        for k = scanLimit, scanStartIdx, -1 do
                            local st = steps[k]
                            if st then
                                local act = st.action or "ACCEPT"
                                if (not st.zoneId)
                                    or (st.zoneId == pZone)
                                    or pendingZones[st.zoneId]
                                    or (act == "TURN_IN" and ((st.questId and pendingQids[st.questId]) or type(st.questIds) == "table")) then
                                    lastRelevantIdx = k
                                    break
                                end
                            end
                        end
                    end
                end

                local scanIdxs = {}
                local scanStepList = {}
                local scanCount = 0
                local existingTurninIdx = {}
                local existingTurninSingle = {}
                local localClusterEnd = nil
                local stillInLocalCluster = (pZone ~= nil) and (cursor ~= nil and cursor <= #steps)
                local vSimCompleted = WipeMap(perfScratch.readySimComp)
                local vSimActive = WipeMap(perfScratch.readySimAct)
                local vSimBlockedAccept = WipeMap(perfScratch.readySimBlk)
                local hasSkipped = (type(skippedSteps) == "table" and next(skippedSteps) ~= nil)
                local hasCustomDone = (type(doneCustomSteps) == "table" and next(doneCustomSteps) ~= nil)
                local completedMap, bulkSynced = State.GetCompletedReadOnly()
                local checkCompSlow = (not bulkSynced) and State.IsQuestCompleted or nil
                local quests = data and data.quests
                local maxQuestLv = pLevel + maxOff

                local hasQdbNow = (consultQuestie and GetQuestieDB() ~= nil)
                local maxTravelLv = pLevel + math.max(maxOff, 2) + 4
                local locFrozen = IsPlayerLocationFrozen(player)
                local acceptMap = GetAcceptStepIndexMap(steps)

                for idx = scanStartIdx, lastRelevantIdx do
                    local st = steps[idx]
                    if st then
                        local handled = false
                        local skipped = false
                        local satisfied = false
                        local outleveled = false
                        local bCode = nil
                        if (not hasCustomDone) and st.action == "TRAVEL" and st.zoneId and (not st.questId) and (not st.questIds)
                            and (not st.faction) and (not st.races) and (not st.race) and (not st.classes) and (not st.class) then
                            handled = true
                            skipped = hasSkipped and State.IsStepSkipped(st, idx, pkey) or false
                            if not skipped then
                                if (manualCursorIndex == nil or idx > manualCursorIndex) and type(st.expectedLevel) == "number" and pLevel >= st.expectedLevel + 2 then
                                    if (not locFrozen) and pZone and st.zoneId ~= pZone then
                                        outleveled = true
                                    elseif pLevel >= st.expectedLevel + 3 and (not locFrozen) and st.zoneId ~= pZone then
                                        outleveled = true
                                    elseif pLevel >= st.expectedLevel + 7 then
                                        outleveled = true
                                    end
                                end
                                if not outleveled then
                                    if autoAdvance and (not locFrozen) and (pZone == st.zoneId or (st.zoneName and player.zoneName and (st.zoneName == player.zoneName or ((not pZone) and string.lower(st.zoneName) == string.lower(player.zoneName))))) then
                                        satisfied = true
                                    else
                                        local checkedImmDone = (not autoAdvance) or locFrozen
                                        local destZone = st.zoneId
                                        local foundAny = false
                                        local foundActionable = false
                                        local lookBlockedAccept = nil
                                        local maxLook = math.min(#steps, idx + 12)
                                        for look = idx + 1, maxLook do
                                            local nsStep = steps[look]
                                            if not nsStep then break end
                                            if nsStep.action ~= "TRAVEL" then
                                                local nSingle = false
                                                local nDoneQ, nActE, nsDone = false, nil, false
                                                local nqid = nsStep.questId
                                                local nq = (nqid and (not nsStep.questIds) and quests) and quests[nqid] or nil
                                                local nAct = nsStep.action or "ACCEPT"
                                                local nIsAccLike = (nAct == "ACCEPT") or (nAct == "DUNGEON_PREP" and nsStep.prepMode == "accept")
                                                local nIsCompLike = (nAct == "COMPLETE") or (nAct == "DUNGEON_RUN")
                                                local nLiveExcl, nVirtExcl = false, false
                                                if nq and (nAct == "ACCEPT" or nAct == "COMPLETE" or nAct == "TURN_IN" or nAct == "DUNGEON_PREP" or nAct == "DUNGEON_RUN") then
                                                    nSingle = true
                                                    nActE = activeQuests[nqid]
                                                    nDoneQ = (completedMap[nqid] == true) or (checkCompSlow ~= nil and checkCompSlow(nqid, true))
                                                    if nq.exclusiveTo then
                                                        for _, oid in ipairs(nq.exclusiveTo) do
                                                            if oid ~= nqid then
                                                                if (completedMap[oid] == true) or activeQuests[oid] ~= nil or (checkCompSlow ~= nil and checkCompSlow(oid, true)) then
                                                                    nLiveExcl = true
                                                                    break
                                                                elseif vSimCompleted[oid] or vSimActive[oid] then
                                                                    nVirtExcl = true
                                                                end
                                                            end
                                                        end
                                                    end
                                                    if nLiveExcl then
                                                        nsDone = true
                                                    elseif nIsAccLike then
                                                        nsDone = nDoneQ or (nActE ~= nil)
                                                    elseif nIsCompLike then
                                                        nsDone = nDoneQ or (nActE ~= nil and nActE.isComplete == true and not nActE.isFailed)
                                                    else
                                                        nsDone = nDoneQ
                                                    end
                                                else
                                                    nsDone = State.IsStepSatisfiedFast(nsStep, steps, look, player, pkey)
                                                end

                                                if not checkedImmDone then
                                                    checkedImmDone = true
                                                    if look <= idx + 3 and nsStep.zoneId == st.zoneId and nsDone then
                                                        satisfied = true
                                                        break
                                                    end
                                                    if (st.expectedLevel or 0) > maxTravelLv then
                                                        bCode = "quest_level_too_high"
                                                        break
                                                    end
                                                elseif (not foundAny) and (st.expectedLevel or 0) > maxTravelLv then
                                                    bCode = "quest_level_too_high"
                                                    break
                                                end

                                                if nsStep.zoneId == destZone then
                                                    foundAny = true
                                                    local nsSkip = hasSkipped and State.IsStepSkipped(nsStep, look, pkey) or false
                                                    if (not nsSkip) and (not nsDone) then
                                                        local nsBlocked = false
                                                        local nIncompElite = skipEliteNow and (nqid ~= nil)
                                                            and nAct ~= "DUNGEON_PREP" and nAct ~= "DUNGEON_RUN"
                                                            and (not nDoneQ) and not (nActE ~= nil and nActE.isComplete == true)
                                                            and (nq ~= nil
                                                                and ((not nq.isDungeon) and ((nq.isGroup == true) or (nActE ~= nil and nActE.isElite == true)))
                                                                or (nq == nil and State.IsQuestElite(nqid, nil)))
                                                        local nIncompDung = skipDungeonsNow and (nqid ~= nil)
                                                            and (not nDoneQ) and not (nActE ~= nil and nActE.isComplete == true)
                                                            and ((nAct == "DUNGEON_PREP" or nAct == "DUNGEON_RUN")
                                                                or (nq ~= nil and ((nq.isDungeon == true) or (nActE ~= nil and nActE.isDungeon == true)))
                                                                or (nq == nil and State.IsQuestDungeon(nqid, nil)))
                                                        if (nIncompElite or nIncompDung) and (nActE ~= nil or vSimActive[nqid]) then
                                                            nsBlocked = true
                                                        elseif nSingle and (nDoneQ or (nActE ~= nil) or vSimCompleted[nqid] or vSimActive[nqid]) then
                                                            nsBlocked = false
                                                        elseif nSingle and (not hasQdbNow) and (not nq.requiredSkill) then
                                                            local nbCode = perfScratch.CheckInlineReadyElig(nqid, nq, nVirtExcl, player, pLevel, maxQuestLv, vSimCompleted, completedMap, checkCompSlow, skipEliteNow, skipDungeonsNow)
                                                            if (not nbCode) and (nAct == "COMPLETE" or nAct == "TURN_IN") then
                                                                if vSimBlockedAccept[nqid] or (lookBlockedAccept and lookBlockedAccept[nqid]) then
                                                                    nbCode = "accept_skipped"
                                                                else
                                                                    local aEntry = acceptMap and acceptMap[nqid]
                                                                    if type(aEntry) == "number" then
                                                                        if aEntry < look and aEntry >= (look - 150) then
                                                                            if not (aEntry >= scanStartIdx and aEntry < idx and steps[aEntry] and steps[aEntry].questId == nqid and not steps[aEntry].questIds) then
                                                                                if IsAcceptSkippedEarlier(nqid, steps, look, pkey, player, vSimCompleted, vSimActive, toggles) then
                                                                                    nbCode = "accept_skipped"
                                                                                end
                                                                            end
                                                                        end
                                                                    elseif aEntry ~= nil then
                                                                        if IsAcceptSkippedEarlier(nqid, steps, look, pkey, player, vSimCompleted, vSimActive, toggles) then
                                                                            nbCode = "accept_skipped"
                                                                        end
                                                                    end
                                                                end
                                                            end
                                                            nsBlocked = (nbCode ~= nil)
                                                            if nIsAccLike and nsBlocked then
                                                                if not lookBlockedAccept then
                                                                    lookBlockedAccept = WipeMap(perfScratch.readyLookBlk)
                                                                end
                                                                lookBlockedAccept[nqid] = true
                                                            end
                                                        else
                                                            nsBlocked = (IsStepDirectBlocked(nsStep, steps, look, player, pkey, vSimCompleted, vSimActive, toggles, FAST_BLOCK_OPTS) ~= nil)
                                                        end
                                                        if not nsBlocked then
                                                            foundActionable = true
                                                            break
                                                        end
                                                    end
                                                else
                                                    break
                                                end
                                            else
                                                if foundAny then
                                                    break
                                                end
                                                if (not checkedImmDone) and look > idx + 3 then
                                                    checkedImmDone = true
                                                    if (st.expectedLevel or 0) > maxTravelLv then
                                                        bCode = "quest_level_too_high"
                                                        break
                                                    end
                                                end
                                                destZone = nsStep.zoneId
                                            end
                                        end
                                        if (not satisfied) and (not bCode) then
                                            if (st.expectedLevel or 0) > maxTravelLv then
                                                bCode = "quest_level_too_high"
                                            elseif foundAny and not foundActionable then
                                                bCode = "redundant_travel"
                                            end
                                        end
                                    end
                                end
                            end
                        elseif (not hasCustomDone) and st.questId and (not st.questIds) and quests then
                            local sAct = st.action or "ACCEPT"
                            local qid = st.questId
                            local q = quests[qid]
                            if q and (sAct == "ACCEPT" or sAct == "COMPLETE" or sAct == "TURN_IN" or sAct == "DUNGEON_PREP" or sAct == "DUNGEON_RUN") then
                                handled = true
                                local actE = activeQuests[qid]
                                local isDoneQ = (completedMap[qid] == true) or (checkCompSlow ~= nil and checkCompSlow(qid, true))
                                local isAcceptLike = (sAct == "ACCEPT") or (sAct == "DUNGEON_PREP" and st.prepMode == "accept")
                                local isCompLike = (sAct == "COMPLETE") or (sAct == "DUNGEON_RUN")
                                local liveExcluded, virtExcluded = false, false
                                if q.exclusiveTo then
                                    for _, oid in ipairs(q.exclusiveTo) do
                                        if oid ~= qid then
                                            if (completedMap[oid] == true) or activeQuests[oid] ~= nil or (checkCompSlow ~= nil and checkCompSlow(oid, true)) then
                                                liveExcluded = true
                                                break
                                            elseif vSimCompleted[oid] or vSimActive[oid] then
                                                virtExcluded = true
                                            end
                                        end
                                    end
                                end
                                skipped = hasSkipped and State.IsStepSkipped(st, idx, pkey) or false
                                if not skipped then
                                    if autoAdvance then
                                        if liveExcluded then
                                            satisfied = true
                                        elseif isAcceptLike then
                                            satisfied = isDoneQ or (actE ~= nil)
                                        elseif isCompLike then
                                            satisfied = isDoneQ or (actE ~= nil and actE.isComplete == true and not actE.isFailed)
                                        else
                                            satisfied = isDoneQ
                                        end
                                    end
                                    if (not satisfied) and (manualCursorIndex == nil or idx > manualCursorIndex) and type(st.expectedLevel) == "number" and actE == nil then
                                        local isDung = (q.isDungeon == true) or (sAct == "DUNGEON_RUN")
                                        if (not isDung) and ((tonumber(q.questLevel or q.minLevel) or st.expectedLevel) <= pLevel - 7) then
                                            outleveled = true
                                        elseif pLevel >= st.expectedLevel + 2 then
                                            outleveled = State.IsStepOutleveled(st, player)
                                        end
                                    end
                                    if (not satisfied) and (not outleveled) then
                                        local isIncompElite = skipEliteNow
                                            and sAct ~= "DUNGEON_PREP" and sAct ~= "DUNGEON_RUN"
                                            and (not isDoneQ) and not (actE ~= nil and actE.isComplete == true)
                                            and (not q.isDungeon) and ((q.isGroup == true) or (actE ~= nil and actE.isElite == true))
                                        local isIncompDung = skipDungeonsNow
                                            and (not isDoneQ) and not (actE ~= nil and actE.isComplete == true)
                                            and ((sAct == "DUNGEON_PREP" or sAct == "DUNGEON_RUN") or (q.isDungeon == true) or (actE ~= nil and actE.isDungeon == true))
                                        if (isIncompElite or isIncompDung) and (actE ~= nil or vSimActive[qid]) then
                                            bCode = isIncompElite and "elite_solo" or "dungeon_skipped"
                                        elseif isDoneQ or (actE ~= nil) or vSimCompleted[qid] or vSimActive[qid] then
                                            bCode = nil
                                        elseif (not hasQdbNow) and (not q.requiredSkill) then
                                            bCode = perfScratch.CheckInlineReadyElig(qid, q, virtExcluded, player, pLevel, maxQuestLv, vSimCompleted, completedMap, checkCompSlow, skipEliteNow, skipDungeonsNow)
                                            if (not bCode) and (sAct == "COMPLETE" or sAct == "TURN_IN") then
                                                if vSimBlockedAccept[qid] then
                                                    bCode = "accept_skipped"
                                                else
                                                    local aEntry = acceptMap and acceptMap[qid]
                                                    if type(aEntry) == "number" then
                                                        if aEntry < idx and aEntry >= (idx - 150) then
                                                            if not (aEntry >= scanStartIdx and steps[aEntry] and steps[aEntry].questId == qid and not steps[aEntry].questIds) then
                                                                if IsAcceptSkippedEarlier(qid, steps, idx, pkey, player, vSimCompleted, vSimActive, toggles) then
                                                                    bCode = "accept_skipped"
                                                                end
                                                            end
                                                        end
                                                    elseif aEntry ~= nil then
                                                        if IsAcceptSkippedEarlier(qid, steps, idx, pkey, player, vSimCompleted, vSimActive, toggles) then
                                                            bCode = "accept_skipped"
                                                        end
                                                    end
                                                end
                                            end
                                        else
                                            bCode = State.IsStepBlocked(st, steps, idx, player, pkey, vSimCompleted, vSimActive, toggles, FAST_BLOCK_OPTS)
                                        end
                                    end
                                end
                                if isAcceptLike and (not satisfied) and (skipped or outleveled or bCode ~= nil) then
                                    vSimBlockedAccept[qid] = true
                                end
                            end
                        end

                        if not handled then
                            skipped = hasSkipped and State.IsStepSkipped(st, idx, pkey) or false
                            satisfied = autoAdvance and State.IsStepSatisfiedFast(st, steps, idx, player, pkey)
                            outleveled = (manualCursorIndex == nil or idx > manualCursorIndex) and State.IsStepOutleveled(st, player)
                            if (not skipped) and (not satisfied) and (not outleveled) then
                                bCode = State.IsStepBlocked(st, steps, idx, player, pkey, vSimCompleted, vSimActive, toggles, FAST_BLOCK_OPTS)
                            end
                            local nAct = st.action or "ACCEPT"
                            if (not satisfied) and (skipped or outleveled or bCode ~= nil)
                                and (nAct == "ACCEPT" or (nAct == "DUNGEON_PREP" and st.prepMode == "accept")) then
                                if type(st.questIds) == "table" then
                                    for qi = 1, #st.questIds do
                                        vSimBlockedAccept[st.questIds[qi]] = true
                                    end
                                elseif st.questId then
                                    vSimBlockedAccept[st.questId] = true
                                end
                            end
                        end

                        if (not skipped) and (not satisfied) and (not outleveled) and (not bCode) then
                            scanCount = scanCount + 1
                            scanIdxs[scanCount] = idx
                            scanStepList[scanCount] = st
                            local act = st.action or "ACCEPT"
                            local stQids = st.questIds
                            if type(stQids) == "table" and #stQids > 0 then
                                local isSingleQ = (#stQids == 1)
                                for qi = 1, #stQids do
                                    local qid = stQids[qi]
                                    if act == "ACCEPT" or (act == "DUNGEON_PREP" and st.prepMode == "accept") then
                                        vSimActive[qid] = true
                                    elseif act == "TURN_IN" or act == "DUNGEON_RUN" then
                                        vSimActive[qid] = nil
                                        vSimCompleted[qid] = true
                                    end
                                    if act == "TURN_IN" and not existingTurninIdx[qid] then
                                        existingTurninIdx[qid] = idx
                                        existingTurninSingle[qid] = isSingleQ
                                    end
                                end
                            elseif st.questId then
                                local qid = st.questId
                                if act == "ACCEPT" or (act == "DUNGEON_PREP" and st.prepMode == "accept") then
                                    vSimActive[qid] = true
                                elseif act == "TURN_IN" or act == "DUNGEON_RUN" then
                                    vSimActive[qid] = nil
                                    vSimCompleted[qid] = true
                                end
                                if act == "TURN_IN" and not existingTurninIdx[qid] then
                                    existingTurninIdx[qid] = idx
                                    existingTurninSingle[qid] = true
                                end
                            end
                            if stillInLocalCluster then
                                if st.action == "TRAVEL" and st.zoneId and st.zoneId ~= pZone then
                                    stillInLocalCluster = false
                                elseif st.zoneId and st.zoneId ~= pZone then
                                    stillInLocalCluster = false
                                elseif (not st.zoneId) or st.zoneId == pZone then
                                    localClusterEnd = idx
                                end
                            end
                        end
                    end
                end

                rsc = {
                    dbGen = dbGen,
                    pkey = pkey,
                    stepsRef = steps,
                    cursor = cursor,
                    pZone = pZone,
                    level = pLevel,
                    maxOff = maxOff,
                    autoAdvance = autoAdvance,
                    manualCursor = manualCursorIndex,
                    raceId = raceId,
                    classId = classId,
                    faction = faction,
                    consultQuestie = consultQuestie,
                    useDynamic = useDynamic,
                    navAvail = navAvail,
                    scanCount = scanCount,
                    scanIdxs = scanIdxs,
                    scanStepList = scanStepList,
                    existingTurninIdx = existingTurninIdx,
                    existingTurninSingle = existingTurninSingle,
                    localClusterEnd = localClusterEnd,
                    anchorMemo = {},
                }
                cSlot.scanByOff[maxOff] = rsc
                perfScratch.readyRouteScanCache = rsc
            end

            rs = {
                sGen = sGen,
                dbGen = dbGen,
                pkey = pkey,
                stepsRef = steps,
                cursor = cursor,
                pZone = pZone,
                level = pLevel,
                maxOff = maxOff,
                autoAdvance = autoAdvance,
                manualCursor = manualCursorIndex,
                readySig = readySig,
                raceId = raceId,
                classId = classId,
                faction = faction,
                consultQuestie = consultQuestie,
                skipEliteNow = skipEliteNow,
                skipDungeonsNow = skipDungeonsNow,
                useDynamic = useDynamic,
                navAvail = navAvail,
                mounted = curMounted,
                scanCount = rsc.scanCount,
                scanIdxs = rsc.scanIdxs,
                scanStepList = rsc.scanStepList,
                existingTurninIdx = rsc.existingTurninIdx,
                existingTurninSingle = rsc.existingTurninSingle,
                localClusterEnd = rsc.localClusterEnd,
                anchorMemo = rsc.anchorMemo,
                pending = rCtx.pending,
                curStepNearByIdx = cSlot.curStepNearByIdx,
                hubEdges = rCtx.hubEdges,
                anchorByIdx = {},
                lastPlan = nil,
                lastImmSig = nil,
                lastAllowPlayerAnchor = nil,
                lastPX = nil,
                lastPY = nil,
                lastPZoneName = nil,
                lastTravelSkipSig = nil,
            }
            cSlot.rsByOff[maxOff] = rs
            perfScratch.readyStaticCache = rs
        end
    end

    local pending = rs.pending
    local existingTurninIdx = rs.existingTurninIdx
    local existingTurninSingle = rs.existingTurninSingle
    local localClusterEnd = rs.localClusterEnd
    local curStepNearByIdx = rs.curStepNearByIdx
    local hubEdges = rs.hubEdges

    local plan = nil
    if #pending > 0 then
        local allowPlayerAnchor = not (IsPlayerLocationFrozen(player) and lastAliveLocation == nil)
        if allowPlayerAnchor and navAvail and ns.Nav and ns.Nav.EnsureSingleSourceField and pZone and pX and pY then
            for i = 1, #pending do
                local info = pending[i]
                if info.zoneId == pZone and info.x and info.y then
                    ns.Nav.EnsureSingleSourceField(pZone, pX, pY, data, player, GPS_ZONE_FIELD_OPTS)
                    break
                end
            end
        end

        -- Pass 1: Immediate nearby batching (<= NEAR_READY_TURNIN_DIST = 22 units)
        local isImmediate = WipeMap(perfScratch.readyIsImm)
        local immediateItems = perfScratch.readyImmItems
        local immCount = 0
        local immQueue = perfScratch.readyImmQueue
        local qTail = 0

        for i = 1, #pending do
            local info = pending[i]
            if perfScratch.MatchesImmediateAnchor(i, info, allowPlayerAnchor, pZone, pX, pY, player, curStep, curStepNearByIdx) then
                isImmediate[i] = true
                qTail = qTail + 1
                immQueue[qTail] = i
                immCount = immCount + 1
                immediateItems[immCount] = info
            end
        end

        -- Hub cascade: pull any same-zone ready quest within HUB_CLUSTER_DIST of an immediate item
        local qHead = 1
        while qHead <= qTail do
            local u = immQueue[qHead]
            qHead = qHead + 1
            local edges = hubEdges[u]
            if edges then
                for k = 1, #edges do
                    local v = edges[k]
                    if not isImmediate[v] then
                        isImmediate[v] = true
                        qTail = qTail + 1
                        immQueue[qTail] = v
                        immCount = immCount + 1
                        immediateItems[immCount] = pending[v]
                    end
                end
            end
        end
        for trim = #immediateItems, immCount + 1, -1 do
            immediateItems[trim] = nil
        end

        local immSig = ""
        if immCount > 0 then
            local immParts = perfScratch.readyImmParts
            local immPartCount = 0
            for i = 1, #pending do
                if isImmediate[i] then
                    immPartCount = immPartCount + 1
                    immParts[immPartCount] = tostring(i)
                end
            end
            immSig = table.concat(immParts, ",", 1, immPartCount)
        end

        local unvisitedInPZoneCount = 0
        local hasUnvisitedTravel = false
        local travelSkipSig = ""
        local tsCount = 0
        for i = 1, #pending do
            if not isImmediate[i] then
                local anc = perfScratch.GetAnchorForPending(i, rs, steps, player, navAvail, curMounted)
                if not (anc and anc.anchorIdx) then
                    local iz = pending[i].zoneId
                    if iz == pZone then
                        unvisitedInPZoneCount = unvisitedInPZoneCount + 1
                    elseif iz then
                        local handled = IsReadyStepHandled("ready_travel:" .. tostring(iz))
                        tsCount = tsCount + 1
                        perfScratch.readyImmParts[tsCount] = tostring(iz) .. (handled and ":1" or ":0")
                        if not handled then
                            hasUnvisitedTravel = true
                        end
                    end
                end
            end
        end
        if tsCount > 0 then
            travelSkipSig = table.concat(perfScratch.readyImmParts, ",", 1, tsCount)
        end

        local pZoneName = player and player.zoneName
        local samePlayerPos = (rs.lastPX == pX and rs.lastPY == pY and rs.lastPZoneName == pZoneName and rs.lastTravelSkipSig == travelSkipSig)
        if rs.lastPlan
            and rs.lastAllowPlayerAnchor == allowPlayerAnchor
            and rs.lastImmSig == immSig
            and (samePlayerPos or (immCount <= 1 and unvisitedInPZoneCount <= 1 and not hasUnvisitedTravel)) then
            if not readyPlanCache then
                readyPlanCache = {}
            end
            readyPlanCache.gen = refreshGeneration
            readyPlanCache.pkey = pkey
            readyPlanCache.cursor = cursor
            readyPlanCache.zoneId = pZone
            readyPlanCache.cx = cx
            readyPlanCache.cy = cy
            readyPlanCache.useDynamic = useDynamic
            readyPlanCache.plan = rs.lastPlan
            return rs.lastPlan
        end

        plan = {
            readyCount = readyCount,
            readyQuests = readyList,
            immediate = {},
            routeBefore = {},
            routeAfter = {},
            suppressedRouteIdx = {},
            decorateRouteIdx = {},
        }

        if immCount > 0 then
            local sortedImm = SortTurninsNearestNeighbor(immediateItems, pX, pY, pZone, player)
            local batchCount = #sortedImm
            for _, info in ipairs(sortedImm) do
                local exIdx = existingTurninIdx[info.questId]
                local keepAtCursor = exIdx and exIdx == cursor and curStep and curStep.zoneId == info.zoneId
                if keepAtCursor then
                    plan.decorateRouteIdx[exIdx] = info
                else
                    plan.immediate[#plan.immediate + 1] = BuildInjectedTurninStep(info, "nearby hub turn-in", batchCount, data)
                    if exIdx and existingTurninSingle[info.questId] and exIdx ~= cursor then
                        plan.suppressedRouteIdx[exIdx] = true
                    end
                end
            end
        end

        -- Pass 2: Match remaining ready turn-ins to upcoming route hubs/zones (<= 140 steps),
        -- or group unvisited zones after localClusterEnd (or immediate if no local route steps remain).
        local matchedByAnchor = {}
        local unvisitedByZone = {}
        local noZoneItems = {}

        for i = 1, #pending do
            if not isImmediate[i] then
                local info = pending[i]
                local exIdx = existingTurninIdx[info.questId]
                local anc = perfScratch.GetAnchorForPending(i, rs, steps, player, navAvail, curMounted)
                local anchorIdx = anc and anc.anchorIdx or nil
                if anchorIdx then
                    if anc.exAlreadyOptimal and exIdx then
                        plan.decorateRouteIdx[exIdx] = info
                    else
                        matchedByAnchor[anchorIdx] = matchedByAnchor[anchorIdx] or {}
                        table.insert(matchedByAnchor[anchorIdx], info)
                        if exIdx and existingTurninSingle[info.questId] and exIdx ~= cursor then
                            plan.suppressedRouteIdx[exIdx] = true
                        end
                    end
                elseif info.zoneId then
                    unvisitedByZone[info.zoneId] = unvisitedByZone[info.zoneId] or {}
                    table.insert(unvisitedByZone[info.zoneId], info)
                    if exIdx and existingTurninSingle[info.questId] and exIdx ~= cursor then
                        plan.suppressedRouteIdx[exIdx] = true
                    end
                else
                    noZoneItems[#noZoneItems + 1] = info
                end
            end
        end

        for anchorIdx, bucket in pairs(matchedByAnchor) do
            local st = steps[anchorIdx]
            local sorted = SortTurninsNearestNeighbor(bucket, st and st.x, st and st.y, st and st.zoneId, player)
            local attachAfter = (st and (st.action == "TRAVEL" or st.action == "TURN_IN"))
            local targetMap = attachAfter and plan.routeAfter or plan.routeBefore
            targetMap[anchorIdx] = targetMap[anchorIdx] or {}
            local reason = string.format("aligned with %s hub", (st and st.zoneName) or "upcoming zone")
            for _, info in ipairs(sorted) do
                table.insert(targetMap[anchorIdx], BuildInjectedTurninStep(info, reason, #sorted, data))
            end
        end

        if next(unvisitedByZone) then
            local zoneOrder = {}
            local sameReg = perfScratch.readyZoneSameReg
            local zDist = perfScratch.readyZoneDist
            for k in pairs(sameReg) do sameReg[k] = nil end
            for k in pairs(zDist) do zDist[k] = nil end
            local dataZones = data and data.zones
            local pReg = (pZone and dataZones and dataZones[pZone]) and dataZones[pZone].region
            for zId, bucket in pairs(unvisitedByZone) do
                zoneOrder[#zoneOrder + 1] = zId
                local zReg = dataZones and dataZones[zId] and dataZones[zId].region
                sameReg[zId] = (pReg ~= nil and zReg ~= nil and zReg == pReg) and true or false
                if zId == pZone then
                    zDist[zId] = 0
                elseif pZone and RouteDistCoord then
                    local bestDist = nil
                    for bi = 1, #bucket do
                        local info = bucket[bi]
                        local d = RouteDistCoord(pZone, pX or 50, pY or 50, zId, info and info.x or 50, info and info.y or 50, player, info and info.questId, "t")
                        if d and (bestDist == nil or d < bestDist) then
                            bestDist = d
                        end
                    end
                    zDist[zId] = bestDist and math.floor(bestDist * 10 + 0.5) or 999999
                else
                    zDist[zId] = 999999
                end
            end
            perfScratch.readySortPZone = pZone
            table.sort(zoneOrder, perfScratch.SortReadyZones)

            for _, zId in ipairs(zoneOrder) do
                local bucket = unvisitedByZone[zId]
                local startX = (zId == pZone) and pX or (bucket[1] and bucket[1].x)
                local startY = (zId == pZone) and pY or (bucket[1] and bucket[1].y)
                local sorted = SortTurninsNearestNeighbor(bucket, startX, startY, zId, player)
                local batch = {}
                local zName = sorted[1] and sorted[1].zoneName or ResolveZoneNameById(zId, nil, data)
                if zId ~= pZone and not IsReadyStepHandled("ready_travel:" .. tostring(zId)) then
                    batch[#batch + 1] = BuildInjectedTravelStep(zId, zName, sorted[1], sorted, player and player.zoneName, player)
                end
                local reason = localClusterEnd and string.format("batched %s trip after current zone cluster", zName or "zone")
                    or string.format("batched %s trip (no local steps remaining)", zName or "zone")
                for _, info in ipairs(sorted) do
                    batch[#batch + 1] = BuildInjectedTurninStep(info, reason, #sorted, data)
                end

                if localClusterEnd then
                    plan.routeAfter[localClusterEnd] = plan.routeAfter[localClusterEnd] or {}
                    for _, bStep in ipairs(batch) do
                        table.insert(plan.routeAfter[localClusterEnd], bStep)
                    end
                else
                    for _, bStep in ipairs(batch) do
                        plan.immediate[#plan.immediate + 1] = bStep
                    end
                end
            end
        end

        for _, info in ipairs(noZoneItems) do
            plan.immediate[#plan.immediate + 1] = BuildInjectedTurninStep(info, "ready to turn in", 1, data)
        end

        rs.lastAllowPlayerAnchor = allowPlayerAnchor
        rs.lastImmSig = immSig
        rs.lastPX = pX
        rs.lastPY = pY
        rs.lastPZoneName = pZoneName
        rs.lastTravelSkipSig = travelSkipSig
        rs.lastPlan = plan
    else
        plan = rs.lastPlan
        if not plan then
            plan = {
                readyCount = readyCount,
                readyQuests = readyList,
                immediate = {},
                routeBefore = {},
                routeAfter = {},
                suppressedRouteIdx = {},
                decorateRouteIdx = {},
            }
            rs.lastPlan = plan
        end
    end

    if not readyPlanCache then
        readyPlanCache = {}
    end
    readyPlanCache.gen = refreshGeneration
    readyPlanCache.pkey = pkey
    readyPlanCache.cursor = cursor
    readyPlanCache.zoneId = pZone
    readyPlanCache.cx = cx
    readyPlanCache.cy = cy
    readyPlanCache.useDynamic = useDynamic
    readyPlanCache.plan = plan
    return plan
end

--------------------------------------------------------------------------------
-- Near-Realtime GPS Reordering & Friend-Meetup Chain Adaptation
--------------------------------------------------------------------------------

function State.GetRecentAcceptedQuests()
    return recentAcceptedQuests, recentChainFollowups
end

local function ReadRawMapAndCoords()
    local uiMapId = nil
    if C_Map and C_Map.GetBestMapForUnit then
        local okM, mapRes = pcall(C_Map.GetBestMapForUnit, "player")
        if okM then
            uiMapId = mapRes
        end
    end
    local rawGx, rawGy = nil, nil
    if uiMapId and C_Map and C_Map.GetPlayerMapPosition then
        local okP, pos = pcall(C_Map.GetPlayerMapPosition, uiMapId, "player")
        if okP and pos then
            if pos.GetXY then
                local okXY, xVal, yVal = pcall(pos.GetXY, pos)
                if okXY then
                    rawGx, rawGy = xVal, yVal
                end
            elseif type(pos) == "table" then
                rawGx, rawGy = pos.x or pos[1], pos.y or pos[2]
            end
        end
    end
    return uiMapId, rawGx, rawGy
end

local function ResolveLiveLocation(uiMapId, rawGx, rawGy)
    local px, py = nil, nil
    if type(rawGx) == "number" and type(rawGy) == "number" and (rawGx > 0 or rawGy > 0) then
        px = rawGx * 100
        py = rawGy * 100
    end
    local zoneName = nil
    if uiMapId and C_Map and C_Map.GetMapInfo then
        local okI, info = pcall(C_Map.GetMapInfo, uiMapId)
        if okI and info and info.name then
            zoneName = info.name
        end
    end
    if not zoneName and GetRealZoneText then
        zoneName = GetRealZoneText()
    end
    local zoneId = State.ResolveZoneId(uiMapId, zoneName)
    return zoneId, px, py
end

local function ShouldTriggerGpsMove(zoneId, px, py, nowSec, force, inInstNow, instMapIdNow, instNameNow)
    if inInstNow == nil and ns.GetPlayerInstanceState then
        inInstNow, instNameNow, instMapIdNow = ns.GetPlayerInstanceState()
    end
    local instChanged = false
    if perfScratch.lastObservedInInstance ~= nil then
        if (inInstNow == true) ~= (perfScratch.lastObservedInInstance == true) then
            instChanged = true
        elseif inInstNow and (instMapIdNow ~= perfScratch.lastObservedInstMapId or instNameNow ~= perfScratch.lastObservedInstName) then
            instChanged = true
        end
    elseif inInstNow then
        instChanged = true
    end
    if force or instChanged then
        return true, (instChanged or (zoneId ~= nil and lastTrackedZoneId ~= nil and zoneId ~= lastTrackedZoneId))
    end
    if inInstNow then
        -- Suppress idle/walking ticks while remaining inside the same instance.
        return false, false
    end
    if not zoneId and not px then
        return false, false
    end
    local zoneChanged = (zoneId ~= nil) and (lastTrackedZoneId ~= nil) and (zoneId ~= lastTrackedZoneId)
    local firstInit = (lastTrackedZoneId == nil and zoneId ~= nil)
    if firstInit or zoneChanged then
        return true, zoneChanged
    end
    if not (px and py) then
        -- Suppress same-zone indoor nil/0,0 doorway flicker.
        return false, false
    end
    if not (lastTrackedX and lastTrackedY) then
        return true, false
    end
    local dx = px - lastTrackedX
    local dy = py - lastTrackedY
    local movedSq = dx * dx + dy * dy
    local cooldownReady = (not nowSec) or (not lastGpsRefreshAt) or ((nowSec - lastGpsRefreshAt) >= GPS_UPDATE_COOLDOWN_SEC)
    local nav = ns.NavData
    local navAvail = ns.Nav and ns.Nav.IsAvailable and ns.Nav.IsAvailable()
    if navAvail and nav and zoneId then
        local movedYards = ns.Nav.ZoneCoordsToYards(nav, zoneId, lastTrackedX, lastTrackedY, px, py)
        if movedYards >= GPS_LARGE_MOVE_YARDS and movedSq >= GPS_MOVE_THRESHOLD_SQ then
            return true, false
        end
        if movedYards >= GPS_LARGE_MOVE_YARDS and cooldownReady then
            return true, false
        end
        if movedSq < GPS_MOVE_THRESHOLD_SQ or movedYards < GPS_MOVE_YARDS_THRESHOLD then
            return false, false
        end
        local _, _, prevCell = ns.Nav.CoordToCellXY(nav, lastTrackedX, lastTrackedY)
        local _, _, curCell = ns.Nav.CoordToCellXY(nav, px, py)
        if curCell == prevCell then
            return false, false
        end
        if not cooldownReady then
            return false, false
        end
        return true, false
    end
    if movedSq < GPS_MOVE_THRESHOLD_SQ then
        return false, false
    end
    if movedSq < 64.0 and not cooldownReady then
        return false, false
    end
    return true, false
end

function State.CheckLivePosition(force)
    gpsTickChecks = gpsTickChecks + 1
    if not ns.charDB then return false end
    local deadNow = (ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost()) and true or false
    if deadNow or (UnitOnTaxi and UnitOnTaxi("player")) then
        if deadNow then
            deadLocationSkipped = deadLocationSkipped + 1
        end
        return false
    end
    local toggles = ns.charDB.toggles or {}
    if toggles.locationReorder == false and not force then
        return false
    end

    local uiMapId, rawGx, rawGy = ReadRawMapAndCoords()
    local inInstNow, instNameNow, instMapIdNow = false, nil, nil
    if ns.GetPlayerInstanceState then
        inInstNow, instNameNow, instMapIdNow = ns.GetPlayerInstanceState()
    end
    local sameInst = (perfScratch.lastObservedInInstance == inInstNow)
        and ((not inInstNow) or (instMapIdNow == perfScratch.lastObservedInstMapId and instNameNow == perfScratch.lastObservedInstName))
    if (not force) and sameInst and lastRawInit and uiMapId == lastRawUiMapId and rawGx == lastRawGx and rawGy == lastRawGy then
        return false
    end

    State.BuildIndexes()
    local zoneId, px, py = ResolveLiveLocation(uiMapId, rawGx, rawGy)
    local nowSec = GetTime and tonumber(GetTime()) or nil
    local shouldRun, zoneChanged = ShouldTriggerGpsMove(zoneId, px, py, nowSec, force, inInstNow, instMapIdNow, instNameNow)

    perfScratch.lastObservedInInstance = inInstNow
    perfScratch.lastObservedInstMapId = instMapIdNow
    perfScratch.lastObservedInstName = instNameNow

    if shouldRun then
        lastTrackedZoneId = zoneId or lastTrackedZoneId
        if px and py then
            lastTrackedX = px
            lastTrackedY = py
        end
        if zoneChanged then
            lastGpsTargetKey = nil
        end
        if nowSec then
            lastGpsRefreshAt = nowSec
        end
        lastRawUiMapId = uiMapId
        lastRawGx = rawGx
        lastRawGy = rawGy
        lastRawInit = true
        gpsTickTriggers = gpsTickTriggers + 1
        State.Refresh(zoneChanged and "GPS_ZONE_CHANGED" or "GPS_MOVED")
        return true
    end

    lastRawUiMapId = uiMapId
    lastRawGx = rawGx
    lastRawGy = rawGy
    lastRawInit = true
    return false
end

function State.GetLocationPlanSignature()
    local curTarget = lastGpsTargetKey or ""
    if perfScratch.locSigSeq == recentAcceptedSeq and perfScratch.locSigTarget == curTarget then
        return perfScratch.locSigVal
    end
    local cnt = 0
    local nParts = 0
    local parts = perfScratch.locSigParts
    for qid in pairs(recentAcceptedQuests) do
        cnt = cnt + 1
        nParts = nParts + 1
        parts[nParts] = tostring(qid)
    end
    for qid in pairs(recentChainFollowups) do
        nParts = nParts + 1
        parts[nParts] = "f" .. tostring(qid)
    end
    for trim = #parts, nParts + 1, -1 do
        parts[trim] = nil
    end
    if nParts > 1 then
        table.sort(parts)
    end
    local sig = string.format("%s|%d|%s", curTarget, cnt, table.concat(parts, ",", 1, nParts))
    perfScratch.locSigSeq = recentAcceptedSeq
    perfScratch.locSigTarget = curTarget
    perfScratch.locSigVal = sig
    return sig
end

local function QuestHasObjectives(q, activeEntry)
    if q then
        if type(q.objectives) == "table" and #q.objectives > 0 then
            return true
        end
        if q.isBreadcrumb then
            return false
        end
        return false
    end
    if activeEntry and activeEntry.isComplete == false then
        return true
    end
    return false
end

local function GetQuestStepPoint(q, action, resolvedFallback)
    if q then
        local tbl = (action == "TURN_IN" and (q.turnin or q.starter))
            or (action == "COMPLETE" and ((q.objectives and q.objectives[1]) or q.turnin or q.starter))
            or (q.starter or q.turnin or (q.objectives and q.objectives[1]))
        if type(tbl) == "table" then
            local pt = ExtractTurninPoint(tbl, q.zoneId, q.zoneName)
            if pt and pt.x and pt.y then
                return pt
            end
        end
        if action == "COMPLETE" and type(q.objectives) == "table" then
            for _, obj in ipairs(q.objectives) do
                local opt = ExtractTurninPoint(obj, q.zoneId, q.zoneName)
                if opt and opt.x and opt.y then
                    return opt
                end
            end
        end
        if action ~= "TURN_IN" then
            local pt2 = ExtractTurninPoint(q.starter, q.zoneId, q.zoneName) or ExtractTurninPoint(q.turnin, q.zoneId, q.zoneName)
            if pt2 then return pt2 end
        end
    end
    if resolvedFallback then
        return {
            zoneId = resolvedFallback.zoneId,
            zoneName = resolvedFallback.zoneName,
            x = resolvedFallback.x,
            y = resolvedFallback.y,
            npcId = resolvedFallback.npcId,
            npcName = resolvedFallback.npcName,
        }
    end
    return nil
end

local function MakeGpsBadges(q, isFriendChain, data)
    local dName = nil
    if q and q.dungeonUnlocks and #q.dungeonUnlocks > 0 then
        local dId = q.dungeonUnlocks[1]
        local d = data and data.dungeons and data.dungeons[dId]
        dName = d and d.name or tostring(dId)
    end
    return {
        isGpsReordered = true,
        isFriendChain = isFriendChain and true or false,
        xp = (q and q.xp) or 0,
        goldCopper = (q and (q.goldCopper or q.money)) or 0,
        bagSlots = (q and q.bagSlots) or 0,
        rogueScore = (q and q.rogueScore) or 0,
        classScore = (q and q.classBisScore) or (q and q.rogueScore) or 0,
        isLeatherworking = q and q.isLeatherworking or false,
        isSkinning = q and q.isSkinning or false,
        skinningScore = (q and q.skinningScore) or 0,
        requiredSkill = q and q.requiredSkill or nil,
        unlocksDungeon = dName,
    }
end

local function CloneReorderedRouteStep(step, origIdx, reasonNote)
    local note = reasonNote or "GPS Reorder"
    local rCache = perfScratch.reorderedStepCache
    if step and not step.isReadyTurnIn then
        local cached = rCache[step]
        if cached and cached.origRouteIdx == origIdx and cached._reasonNote == note then
            return cached
        end
    end
    local copy = {}
    for k, v in pairs(step) do
        copy[k] = v
    end
    copy.isGpsReordered = true
    copy.origRouteIdx = origIdx
    copy.gpsStepKey = "gps_route:" .. tostring(origIdx) .. ":" .. tostring(step.questId or step.action or "")
    local badgesCopy = {}
    if type(step.badges) == "table" then
        for k, v in pairs(step.badges) do
            badgesCopy[k] = v
        end
    end
    badgesCopy.isGpsReordered = true
    copy.badges = badgesCopy
    local baseWhy = step.why or ""
    if not baseWhy:find("GPS Reorder", 1, true) then
        copy.why = string.format("[%s] %s", note, baseWhy)
    end
    copy._reasonNote = note
    if step and not step.isReadyTurnIn then
        if rCache[step] == nil then
            if perfScratch.reorderedCount >= 1024 then
                rCache = {}
                perfScratch.reorderedStepCache = rCache
                perfScratch.reorderedCount = 0
            end
            perfScratch.reorderedCount = perfScratch.reorderedCount + 1
        end
        rCache[step] = copy
    end
    return copy
end

local function BuildGpsQuestStep(q, qid, action, player, isFriendChain, whyDetail, data)
    local resolvedFallback = (not q) and State.ResolveQuestTurnin(qid) or nil
    local pt = GetQuestStepPoint(q, action, resolvedFallback)
    local activeEntry = activeQuests and activeQuests[qid]
    local qName = (q and q.name) or (resolvedFallback and resolvedFallback.title) or (activeEntry and activeEntry.title) or ("Quest #" .. tostring(qid))
    local zId = (pt and pt.zoneId) or (q and q.zoneId) or (player and player.zoneId)
    local zName = (pt and pt.zoneName) or (q and q.zoneName) or ResolveZoneNameById(zId, nil, data)
    local tagLabel = isFriendChain and "[Friend Chain] [GPS Reorder]" or "[Active Chain] [GPS Reorder]"
    local actionLabel = (action == "ACCEPT" and "Accept")
        or (action == "COMPLETE" and "Complete")
        or (action == "TURN_IN" and "Turn In")
        or tostring(action)
    return {
        action = action,
        questId = qid,
        questIds = { qid },
        title = actionLabel .. ": " .. qName,
        zoneId = zId,
        zoneName = zName,
        x = pt and pt.x or nil,
        y = pt and pt.y or nil,
        npcId = (action ~= "COMPLETE") and (pt and pt.npcId) or nil,
        npcName = (action ~= "COMPLETE") and (pt and pt.npcName) or nil,
        expectedLevel = (q and (q.questLevel or q.minLevel)) or (activeEntry and activeEntry.level) or (player and player.level),
        xp = (q and q.xp) or 0,
        money = (q and (q.goldCopper or q.money)) or 0,
        bagSlots = (q and q.bagSlots) or 0,
        badges = MakeGpsBadges(q, isFriendChain, data),
        why = string.format("%s %s", tagLabel, whyDetail or "Nearest chain step from live position"),
        isGpsReordered = true,
        isFriendChain = isFriendChain and true or false,
        isDynamic = true,
        gpsStepKey = "gps:" .. tostring(action) .. ":" .. tostring(qid),
    }
end

local function IsGpsStepSkippedOrDone(gpsKey)
    if not gpsKey or not ns.charDB then return false end
    if ns.charDB.skippedSteps and ns.charDB.skippedSteps[gpsKey] then
        return true
    end
    if ns.charDB.completedCustomSteps and ns.charDB.completedCustomSteps[gpsKey] then
        return true
    end
    return false
end

local function IsQuestInZoneChain(q, pZone, allowMissingPoint)
    if not q or not pZone then return false end
    if q.zoneId and q.zoneId ~= pZone then
        return false
    end
    local acceptPt = GetQuestStepPoint(q, "ACCEPT")
    if acceptPt and acceptPt.zoneId and acceptPt.zoneId ~= pZone then
        return false
    end
    local nextAction = QuestHasObjectives(q, nil) and "COMPLETE" or "TURN_IN"
    local nextPt = GetQuestStepPoint(q, nextAction)
    if nextPt and nextPt.zoneId and nextPt.zoneId ~= pZone then
        return false
    end
    if nextAction == "COMPLETE" then
        local turnPt = GetQuestStepPoint(q, "TURN_IN")
        if turnPt and turnPt.zoneId and turnPt.zoneId ~= pZone then
            return false
        end
    end
    if (not allowMissingPoint) and (not acceptPt or not acceptPt.x or not acceptPt.y) then
        return false
    end
    return true
end

local function IsSameZoneChainQuest(q, anchorZoneId)
    if not anchorZoneId or not q then
        return true
    end
    local qz = q.zoneId
    local tuz = q.turnin and q.turnin.zoneId
    if qz and qz ~= anchorZoneId then
        return false
    end
    if tuz and tuz ~= anchorZoneId then
        return false
    end
    return true
end

local function CountQuestSteps(q)
    if not q then return 2 end
    local hasObj = type(q.objectives) == "table" and #q.objectives > 0
    return hasObj and 3 or 2
end

local function ResolveChainEntryAction(entry, actKey)
    if not entry then return nil end
    if actKey == "ACCEPT" and not entry.curHasObj then
        actKey = "COMPLETE"
    end
    local existing = entry[actKey]
    if existing ~= nil then
        return existing
    end
    local addSteps = (actKey == "ACCEPT" and 2) or (actKey == "COMPLETE" and 1) or 0
    local remSteps = entry.tailRemSteps + addSteps
    local badge = {
        pos = entry.pos,
        total = entry.total,
        remainingQuests = entry.remQuests,
        remainingSteps = remSteps,
        rootId = entry.rootId,
        rootName = entry.rootName,
    }
    local info = {
        pos = entry.pos,
        total = entry.total,
        remainingQuests = entry.remQuests,
        remainingSteps = remSteps,
        rootId = entry.rootId,
        rootName = entry.rootName,
        chainIds = entry.chainIds,
        badge = badge,
    }
    entry[actKey] = info
    return info
end

local function MakeChainCacheKey(qid, zId, pLv, mOff, skipEliteNow, skipDungeonsNow)
    local z = tonumber(zId) or 0
    local se = skipEliteNow and 1 or 0
    local sd = skipDungeonsNow and 1 or 0
    if z >= 0 and z < 8192 and mOff >= -8 and mOff <= 7 and qid > 0 and qid < 500000 then
        return ((((qid * 8192 + z) * 128 + pLv) * 16 + (mOff + 8)) * 2 + se) * 2 + sd
    end
    return tostring(qid) .. ":" .. tostring(z) .. ":" .. tostring(pLv) .. ":" .. tostring(mOff) .. ":" .. tostring(se) .. ":" .. tostring(sd)
end

function perfScratch.IsChainNodeEligible(candId, candQ, player, anchorZoneId, pLevel, maxOff, completedMap, checkCompSlow, activeMap, skipEliteNow, skipDungeonsNow)
    if not candQ then return false end
    if not State.IsEligibleForPlayer(candId, player) then return false end
    if anchorZoneId and not IsSameZoneChainQuest(candQ, anchorZoneId) then return false end
    local isDone = (completedMap[candId] == true) or (checkCompSlow ~= nil and checkCompSlow(candId, true))
    local actEntry = activeMap[candId]
    local isAct = actEntry ~= nil
    local isActReady = isAct and actEntry.isComplete == true
    if not (isDone or isActReady) then
        if skipEliteNow and State.IsQuestElite(candId, candQ) then
            return false
        end
        if skipDungeonsNow and State.IsQuestDungeon(candId, candQ) then
            return false
        end
    end
    if not (isDone or isAct) then
        if State.IsQuestExcluded and State.IsQuestExcluded(candId, true) then
            return false
        end
        local minLv = tonumber(candQ.minLevel) or 0
        if minLv > pLevel then
            return false
        end
        local qLv = tonumber(candQ.questLevel) or 0
        local reqOff = qLv - pLevel
        if reqOff > maxOff then
            if reqOff - 1 < perfScratch.cMaxOff then
                perfScratch.cMaxOff = reqOff - 1
            end
            return false
        elseif reqOff > perfScratch.cMinOff then
            perfScratch.cMinOff = reqOff
        end
    end
    return true
end

function perfScratch.SortChainChildren(a, b)
    local quests = perfScratch.cQuests
    local qa, qb = quests[a], quests[b]
    local la, lb = tonumber(qa and qa.questLevel) or 0, tonumber(qb and qb.questLevel) or 0
    if la ~= lb then return la < lb end
    return a < b
end

function State.GetChainProgressInfo(questId, player, toggles, action)
    questId = tonumber(questId)
    if not questId then return nil end
    local data = ns.GetData and ns.GetData()
    local quests = data and data.quests
    local targetQ = quests and quests[questId]
    if not targetQ then return nil end

    player = player or playerCache or State.GetPlayerInfo()
    toggles = toggles or (ns.charDB and ns.charDB.toggles) or {}
    local pLevel = tonumber(player and player.level) or 1
    local maxOff = State.GetEffectiveMaxLevelOffset(toggles)
    local skipEliteNow = State.ShouldSkipEliteNow(player, toggles)
    local skipDungeonsNow = State.ShouldSkipDungeonsNow(toggles)
    local pZoneFallback = player and player.zoneId
    local anchorZoneId = targetQ.zoneId or pZoneFallback
    local actKey = (action == "ACCEPT" and "ACCEPT") or (action == "COMPLETE" and "COMPLETE") or "OTHER"
    local baseKey = MakeChainCacheKey(questId, anchorZoneId, pLevel, maxOff, skipEliteNow, skipDungeonsNow)
    local cachedEntry = chainProgressCache[baseKey]
    if cachedEntry ~= nil then
        if type(cachedEntry) == "number" then
            local lo = math.floor(cachedEntry / 16) - 8
            local hi = (cachedEntry % 16) - 8
            return nil, lo, hi
        elseif cachedEntry == false then
            return nil, maxOff, maxOff
        end
        return ResolveChainEntryAction(cachedEntry, actKey), (cachedEntry.minOff or maxOff), (cachedEntry.maxOff or maxOff)
    end

    local activeMap = State.GetActiveQuests() or {}
    local completedMap, bulkSynced = State.GetCompletedReadOnly()
    local checkCompSlow = (not bulkSynced) and State.IsQuestCompleted or nil
    local minValidOff = -3
    local maxValidOff = 5

    -- Walk backward to chain root within same zone and character/level eligibility
    local rootId = questId
    local backVisited = WipeMap(perfScratch.chainVisited)
    backVisited[questId] = true
    for _ = 1, CHAIN_PROGRESS_MAX_DEPTH do
        local curQ = quests[rootId]
        local prereqs = curQ and curQ.prereqIds
        if type(prereqs) ~= "table" or #prereqs == 0 then
            break
        end
        local prevId = nil
        for _, pid in ipairs(prereqs) do
            pid = tonumber(pid)
            if pid and not backVisited[pid] then
                local pq = quests[pid]
                if pq and State.IsEligibleForPlayer(pid, player) and IsSameZoneChainQuest(pq, anchorZoneId) then
                    local pDone = (completedMap[pid] == true) or (checkCompSlow ~= nil and checkCompSlow(pid, true))
                    local pAct = activeMap[pid]
                    if pDone or (pAct and pAct.isComplete == true) then
                        prevId = pid
                        break
                    elseif not (skipEliteNow and State.IsQuestElite(pid, pq))
                        and not (skipDungeonsNow and State.IsQuestDungeon(pid, pq)) then
                        if pAct then
                            prevId = pid
                            break
                        elseif (tonumber(pq.minLevel) or 0) <= pLevel then
                            local reqOff = (tonumber(pq.questLevel) or 0) - pLevel
                            if reqOff <= maxOff then
                                if reqOff > minValidOff then
                                    minValidOff = reqOff
                                end
                                prevId = pid
                                break
                            elseif reqOff - 1 < maxValidOff then
                                maxValidOff = reqOff - 1
                            end
                        end
                    end
                end
            end
        end
        if not prevId then
            break
        end
        backVisited[prevId] = true
        rootId = prevId
    end

    perfScratch.cMinOff = minValidOff
    perfScratch.cMaxOff = maxValidOff

    -- Traverse forward deterministically from rootId up to bounded depth/node cap
    local chain = perfScratch.chainList
    local chainLen = 0
    local seen = WipeMap(perfScratch.chainFwdSeen)
    local stkId = perfScratch.chainStkId
    local stkDepth = perfScratch.chainStkDepth
    local children = perfScratch.chainChildren
    local stkTop = 1
    stkId[1] = rootId
    stkDepth[1] = 0
    while stkTop > 0 and chainLen < CHAIN_PROGRESS_MAX_NODES do
        local cid = stkId[stkTop]
        local depth = stkDepth[stkTop]
        stkTop = stkTop - 1
        if depth <= CHAIN_PROGRESS_MAX_DEPTH and not seen[cid] then
            local cq = quests[cid]
            if perfScratch.IsChainNodeEligible(cid, cq, player, anchorZoneId, pLevel, maxOff, completedMap, checkCompSlow, activeMap, skipEliteNow, skipDungeonsNow) then
                seen[cid] = true
                chainLen = chainLen + 1
                chain[chainLen] = cid
                local nIds = cq.nextQuestIds
                if type(nIds) == "table" and #nIds > 0 then
                    if #nIds == 1 then
                        local nid = tonumber(nIds[1])
                        if nid and not seen[nid] and quests[nid] then
                            stkTop = stkTop + 1
                            stkId[stkTop] = nid
                            stkDepth[stkTop] = depth + 1
                        end
                    else
                        local cLen = 0
                        for _, nid in ipairs(nIds) do
                            nid = tonumber(nid)
                            if nid and not seen[nid] and quests[nid] then
                                cLen = cLen + 1
                                children[cLen] = nid
                            end
                        end
                        if cLen > 0 then
                            for trim = #children, cLen + 1, -1 do
                                children[trim] = nil
                            end
                            if cLen > 1 then
                                perfScratch.cQuests = quests
                                table.sort(children, perfScratch.SortChainChildren)
                            end
                            for ci = cLen, 1, -1 do
                                stkTop = stkTop + 1
                                stkId[stkTop] = children[ci]
                                stkDepth[stkTop] = depth + 1
                            end
                        end
                    end
                end
            end
        end
    end
    perfScratch.cQuests = nil
    minValidOff = perfScratch.cMinOff
    maxValidOff = perfScratch.cMaxOff

    local total = chainLen
    if total >= CHAIN_PROGRESS_MAX_NODES then
        minValidOff = maxOff
        maxValidOff = maxOff
    else
        if minValidOff > maxOff then minValidOff = maxOff end
        if maxValidOff < maxOff then maxValidOff = maxOff end
    end

    local pos = nil
    for i = 1, total do
        if chain[i] == questId then
            pos = i
            break
        end
    end
    if (not pos) or total < 2 then
        if rootId == questId and total < CHAIN_PROGRESS_MAX_NODES then
            minValidOff = -3
        end
        local loClamped = math.max(-3, math.min(maxOff, minValidOff))
        local hiClamped = math.min(5, math.max(maxOff, maxValidOff))
        local negSpan = (loClamped + 8) * 16 + (hiClamped + 8)
        for o = loClamped, hiClamped do
            local okKey = (o == maxOff) and baseKey or MakeChainCacheKey(questId, anchorZoneId, pLevel, o, skipEliteNow, skipDungeonsNow)
            if chainProgressCache[okKey] == nil then
                chainProgressCache[okKey] = negSpan
            end
        end
        return nil, loClamped, hiClamped
    end

    local chainCopy = {}
    for i = 1, total do
        chainCopy[i] = chain[i]
    end

    local suffixQuests = WipeMap(perfScratch.chainSuffixQ)
    local suffixSteps = WipeMap(perfScratch.chainSuffixS)
    local rqAcc = 0
    local rsAcc = 0
    for k = total, 1, -1 do
        suffixQuests[k] = rqAcc
        suffixSteps[k] = rsAcc
        local cid = chainCopy[k]
        if not ((completedMap[cid] == true) or (checkCompSlow ~= nil and checkCompSlow(cid, true))) then
            rqAcc = rqAcc + 1
            local qSteps = CountQuestSteps(quests[cid])
            if activeMap[cid] then
                qSteps = math.max(1, qSteps - 1)
            end
            rsAcc = rsAcc + qSteps
        end
    end

    local rootQ = quests[rootId]
    local rName = rootQ and rootQ.name or (targetQ and targetQ.name) or nil
    local canSeedLinear = false
    for k = 1, total do
        local ckId = chainCopy[k]
        local ckQ = quests[ckId]
        if ckQ then
            local ckZone = ckQ.zoneId or pZoneFallback
            if k == 1 then
                canSeedLinear = (ckZone == anchorZoneId)
            else
                local ckPr = ckQ.prereqIds
                canSeedLinear = canSeedLinear
                    and (ckZone == anchorZoneId)
                    and (type(ckPr) == "table" and #ckPr == 1 and tonumber(ckPr[1]) == chainCopy[k - 1])
            end
            if k == pos or canSeedLinear then
                local entry = {
                    pos = k,
                    total = total,
                    remQuests = suffixQuests[k],
                    tailRemSteps = suffixSteps[k],
                    curHasObj = QuestHasObjectives(ckQ, activeMap[ckId]),
                    rootId = rootId,
                    rootName = rName,
                    chainIds = chainCopy,
                    minOff = minValidOff,
                    maxOff = maxValidOff,
                }
                local seedZone = (k == pos) and anchorZoneId or ckZone
                for o = minValidOff, maxValidOff do
                    local ckKey = (k == pos and o == maxOff) and baseKey or MakeChainCacheKey(ckId, seedZone, pLevel, o, skipEliteNow, skipDungeonsNow)
                    if chainProgressCache[ckKey] == nil then
                        chainProgressCache[ckKey] = entry
                    end
                end
            end
        else
            canSeedLinear = false
        end
    end
    local hitEntry = chainProgressCache[baseKey]
    if type(hitEntry) == "table" then
        return ResolveChainEntryAction(hitEntry, actKey), (hitEntry.minOff or minValidOff), (hitEntry.maxOff or maxValidOff)
    end
    return nil, minValidOff, maxValidOff
end

function State.DecorateStepChain(step, player, toggles)
    if not step then return nil end
    local act = step.action
    if act == "TRAVEL" or act == "DUNGEON_PREP" or act == "DUNGEON_RUN" then
        return step
    end
    local qid = (step.qids and step.qids[1]) or step.questId
    if not qid then
        return step
    end
    local data = ns.GetData and ns.GetData()
    local q = data and data.quests and data.quests[qid]
    if not q then
        return step
    end
    local pIds = q.prereqIds
    local nIds = q.nextQuestIds
    if (not pIds or #pIds == 0) and (not nIds or #nIds == 0) then
        return step
    end

    player = player or playerCache or State.GetPlayerInfo()
    toggles = type(toggles) == "table" and toggles or ((ns.charDB and ns.charDB.toggles) or {})
    local maxOffset = State.GetEffectiveMaxLevelOffset(toggles)
    local decLv = tonumber(player and player.level) or 24
    local decQuestie = (toggles.consultQuestie ~= false)
    local decChains = (toggles.proactiveChains ~= false)
    local decSkipElite = State.ShouldSkipEliteNow(player, toggles)
    local decSkipDungeons = State.ShouldSkipDungeonsNow(toggles)
    if decCache.gen ~= dbQuestStateGeneration
        or decCache.playerRef ~= player
        or decCache.lv ~= decLv
        or decCache.questie ~= decQuestie
        or decCache.chains ~= decChains
        or decCache.skipElite ~= decSkipElite
        or decCache.skipDungeons ~= decSkipDungeons then
        local prof = player and player.professions
        local decRace = tonumber(player and player.raceId) or 0
        local decClass = tonumber(player and player.classId) or 0
        local decFaction = (player and player.faction) or "Alliance"
        local decSkin = (prof and prof.skinningRank) or -1
        local decLw = (prof and prof.leatherworkingRank) or -1
        if decCache.gen ~= dbQuestStateGeneration
            or decCache.lv ~= decLv
            or decCache.race ~= decRace
            or decCache.class ~= decClass
            or decCache.faction ~= decFaction
            or decCache.skin ~= decSkin
            or decCache.lw ~= decLw
            or decCache.questie ~= decQuestie
            or decCache.chains ~= decChains
            or decCache.skipElite ~= decSkipElite
            or decCache.skipDungeons ~= decSkipDungeons then
            decCache.byMaxOff = {}
            decCache.byStepBadge = {}
            decCache.epoch = decCache.epoch + 1
            decCache.gen = dbQuestStateGeneration
            decCache.lv = decLv
            decCache.race = decRace
            decCache.class = decClass
            decCache.faction = decFaction
            decCache.skin = decSkin
            decCache.lw = decLw
            decCache.questie = decQuestie
            decCache.chains = decChains
            decCache.skipElite = decSkipElite
            decCache.skipDungeons = decSkipDungeons
        end
        decCache.playerRef = player
    end
    local isMutableDyn = (step.isGpsReordered == true or step.isFriendChain == true or step.isReadyTurnIn == true)
    local baseStep = (not isMutableDyn) and (step._rawChainStep or step) or step
    if isMutableDyn then
        if step._chainEpoch == decCache.epoch
            and maxOffset >= (step._chainMinOff or 99)
            and maxOffset <= (step._chainMaxValidOff or -99) then
            return step
        end
    else
        local decBucket = decCache.byMaxOff[maxOffset]
        if decBucket then
            local cached = decBucket[baseStep]
            if cached ~= nil then
                return cached
            end
        end
    end

    local info, minValidOff, maxValidOff = State.GetChainProgressInfo(qid, player, toggles, act)
    local loOff, hiOff
    if minValidOff ~= nil then
        loOff = (minValidOff > -3) and minValidOff or -3
        hiOff = (maxValidOff < 5) and maxValidOff or 5
        if loOff > maxOffset then loOff = maxOffset end
        if hiOff < maxOffset then hiOff = maxOffset end
    else
        loOff, hiOff = maxOffset, maxOffset
    end
    if not info then
        if isMutableDyn then
            if step.chainTotal ~= nil or (type(step.badges) == "table" and step.badges.chain ~= nil) then
                step.chainStep = nil
                step.chainPos = nil
                step.chainTotal = nil
                step.chainRemainingQuests = nil
                step.chainRemainingSteps = nil
                step.chainRootId = nil
                step.chainRootName = nil
                if type(step.badges) == "table" then
                    step.badges.chain = nil
                end
            end
            step._chainEpoch = decCache.epoch
            step._chainMinOff = loOff
            step._chainMaxValidOff = hiOff
            return step
        else
            for o = loOff, hiOff do
                local b = decCache.byMaxOff[o]
                if not b then
                    b = {}
                    decCache.byMaxOff[o] = b
                end
                b[baseStep] = baseStep
            end
            return baseStep
        end
    end
    if baseStep.chainTotal == info.total and baseStep.chainPos == info.pos and baseStep.chainRemainingSteps == info.remainingSteps and baseStep.badges and baseStep.badges.chain then
        if isMutableDyn then
            step._chainEpoch = decCache.epoch
            step._chainMinOff = loOff
            step._chainMaxValidOff = hiOff
            return step
        else
            for o = loOff, hiOff do
                local b = decCache.byMaxOff[o]
                if not b then
                    b = {}
                    decCache.byMaxOff[o] = b
                end
                b[baseStep] = baseStep
            end
            return baseStep
        end
    end
    local chainBadge = info.badge or {
        pos = info.pos,
        total = info.total,
        remainingQuests = info.remainingQuests,
        remainingSteps = info.remainingSteps,
        rootId = info.rootId,
        rootName = info.rootName,
    }
    if isMutableDyn then
        if type(step.badges) ~= "table" then
            step.badges = {}
        end
        step.badges.chain = chainBadge
        step.chainStep = info.pos
        step.chainPos = info.pos
        step.chainTotal = info.total
        step.chainRemainingQuests = info.remainingQuests
        step.chainRemainingSteps = info.remainingSteps
        step.chainRootId = info.rootId
        step.chainRootName = info.rootName
        step._chainEpoch = decCache.epoch
        step._chainMinOff = loOff
        step._chainMaxValidOff = hiOff
        return step
    end
    local sbMap = decCache.byStepBadge[baseStep]
    if sbMap then
        local prevOut = sbMap[chainBadge]
        if prevOut then
            for o = loOff, hiOff do
                local b = decCache.byMaxOff[o]
                if not b then
                    b = {}
                    decCache.byMaxOff[o] = b
                end
                b[baseStep] = prevOut
            end
            return prevOut
        end
    end
    local out = {}
    for k, v in pairs(baseStep) do
        out[k] = v
    end
    local badges = {}
    if type(baseStep.badges) == "table" then
        for k, v in pairs(baseStep.badges) do
            badges[k] = v
        end
    end
    badges.chain = chainBadge
    out.badges = badges
    out.chainStep = info.pos
    out.chainPos = info.pos
    out.chainTotal = info.total
    out.chainRemainingQuests = info.remainingQuests
    out.chainRemainingSteps = info.remainingSteps
    out.chainRootId = info.rootId
    out.chainRootName = info.rootName
    out._rawChainStep = baseStep
    if not sbMap then
        sbMap = {}
        decCache.byStepBadge[baseStep] = sbMap
    end
    sbMap[chainBadge] = out
    for o = loOff, hiOff do
        local b = decCache.byMaxOff[o]
        if not b then
            b = {}
            decCache.byMaxOff[o] = b
        end
        b[baseStep] = out
    end
    return out
end

local function BuildZoneFriendPivotPlan(steps, cursor, player, pkey, toggles, readyPlan, data)
    local pZone = player.zoneId
    local pLevel = tonumber(player.level) or 24
    local pX = player.x or 50
    local pY = player.y or 50
    local maxOffset = State.GetEffectiveMaxLevelOffset(toggles)
    local skipEliteNow = State.ShouldSkipEliteNow(player, toggles)
    local skipDungeonsNow = State.ShouldSkipDungeonsNow(toggles)
    local completedMap, bulkSynced = State.GetCompletedReadOnly()
    local checkCompSlow = (not bulkSynced) and State.IsQuestCompleted or nil
    local function IsQDone(qid)
        return (completedMap[qid] == true) or (checkCompSlow ~= nil and checkCompSlow(qid, true))
    end

    local zoneIds = zoneQuestsIndex[pZone] or {}
    local isCapital = CAPITAL_ZONES[pZone] == true

    -- Check engagement: active quests in pZone, recentAcceptedQuests in pZone, recentChainFollowups in pZone
    local seedSet = {}
    local seedOrder = {}
    local hasRecentOrActive = false
    local isFriendTriggered = false

    local function AddSeed(qid, isFriend)
        if not qid or seedSet[qid] then return end
        seedSet[qid] = true
        seedOrder[#seedOrder + 1] = qid
        hasRecentOrActive = true
        if isFriend then
            isFriendTriggered = true
        end
    end

    for qid, entry in pairs(activeQuests or {}) do
        if entry and not IsQDone(qid) then
            local q = data.quests and data.quests[qid]
            if not (skipEliteNow and (not entry.isComplete) and State.IsQuestElite(qid, q))
                and not (skipDungeonsNow and (not entry.isComplete) and State.IsQuestDungeon(qid, q)) then
                local qz = (q and q.zoneId) or (recentAcceptedQuests[qid] and recentAcceptedQuests[qid].zoneId)
                if (not qz) and State.ResolveQuestTurnin then
                    local r = State.ResolveQuestTurnin(qid)
                    qz = r and r.zoneId
                end
                if qz == pZone then
                    local wasRecent = recentAcceptedQuests[qid] ~= nil
                    AddSeed(qid, wasRecent)
                end
            end
        end
    end

    for qid, rec in pairs(recentAcceptedQuests) do
        if rec and rec.zoneId == pZone and not IsQDone(qid) then
            local rq = data.quests and data.quests[qid]
            local rAct = activeQuests and activeQuests[qid]
            if not (skipEliteNow and (not (rAct and rAct.isComplete)) and State.IsQuestElite(qid, rq))
                and not (skipDungeonsNow and (not (rAct and rAct.isComplete)) and State.IsQuestDungeon(qid, rq)) then
                AddSeed(qid, true)
            end
        end
    end
    for qid, rec in pairs(recentChainFollowups) do
        if rec and rec.zoneId == pZone and not IsQDone(qid) then
            local fq = data.quests and data.quests[qid]
            local fAct = activeQuests and activeQuests[qid]
            if not (skipEliteNow and (not (fAct and fAct.isComplete)) and State.IsQuestElite(qid, fq))
                and not (skipDungeonsNow and (not (fAct and fAct.isComplete)) and State.IsQuestDungeon(qid, fq)) then
                AddSeed(qid, true)
            end
        end
    end

    if (not hasRecentOrActive) and isCapital then
        return nil
    end

    -- Discover nearby eligible quests strictly rooted in pZone (via Nav frontier when available)
    local nearbyEligible = {}
    local minAcceptedLv = (not hasRecentOrActive and not isCapital) and (pLevel - 6) or (pLevel - 7)
    if ns.Nav and ns.Nav.FindNearestEligibleQuests and ns.Nav.IsAvailable and ns.Nav.IsAvailable() and pX and pY then
        local pool, pCount = ns.Nav.FindNearestEligibleQuests(pZone, pX, pY, data, player, {
            maxSec = GPS_FRIEND_NEAR_RADIUS * 3.5,
            maxResults = 28,
            roleFilter = "s",
            sameZoneOnly = true,
            allowHearth = false,
        })
        for i = 1, pCount do
            local rec = pool[i]
            local qid = rec and rec.qid
            if qid and not seedSet[qid] and not IsQDone(qid) then
                local q = data.quests and data.quests[qid]
                if q and not IsGpsStepSkippedOrDone("gps:ACCEPT:" .. tostring(qid)) then
                    local qLv = tonumber(q.questLevel or q.minLevel) or pLevel
                    if qLv >= minAcceptedLv and (q.minLevel or 1) <= pLevel and qLv <= pLevel + maxOffset
                        and not (skipEliteNow and State.IsQuestElite(qid, q))
                        and not (skipDungeonsNow and State.IsQuestDungeon(qid, q))
                        and IsQuestInZoneChain(q, pZone, false) then
                        local d = (rec.travelSec or 0) / COORD_SEC_SCALE
                        if d <= GPS_FRIEND_NEAR_RADIUS then
                            nearbyEligible[#nearbyEligible + 1] = { qid = qid, dist = d }
                        end
                    end
                end
            end
        end
    else
        for _, qid in ipairs(zoneIds) do
            if not seedSet[qid] and not IsQDone(qid) then
                local q = data.quests and data.quests[qid]
                if q and not IsGpsStepSkippedOrDone("gps:ACCEPT:" .. tostring(qid)) then
                    local qLv = tonumber(q.questLevel or q.minLevel) or pLevel
                    if qLv >= minAcceptedLv and (q.minLevel or 1) <= pLevel and qLv <= pLevel + maxOffset
                        and not (skipEliteNow and State.IsQuestElite(qid, q))
                        and not (skipDungeonsNow and State.IsQuestDungeon(qid, q))
                        and IsQuestInZoneChain(q, pZone, false) then
                        local pt = GetQuestStepPoint(q, "ACCEPT")
                        local ptZone = pt and pt.zoneId
                        local d = (pt and (not ptZone or ptZone == pZone) and pt.x and pt.y) and TurninDist(pX, pY, pt.x, pt.y) or 999
                        if d <= GPS_FRIEND_NEAR_RADIUS then
                            local bCode = State.GetQuestBlockReason(qid, "ACCEPT", player, nil, nil, toggles, FAST_BLOCK_OPTS)
                            if not bCode then
                                nearbyEligible[#nearbyEligible + 1] = { qid = qid, dist = d }
                            end
                        end
                    end
                end
            end
        end
        table.sort(nearbyEligible, function(a, b)
            if math.abs(a.dist - b.dist) > 0.01 then return a.dist < b.dist end
            return a.qid < b.qid
        end)
    end

    if (not hasRecentOrActive) and (isCapital or #nearbyEligible == 0) then
        return nil
    end

    -- Build quest pool: seeds -> recursive same-zone nextQuestIds (up to depth 4) -> closest nearby pZone quests
    local poolSet = {}
    local poolList = {}
    local function AddToPool(qid, priority)
        if not qid or poolSet[qid] or #poolList >= GPS_FRIEND_SCAN_CAP then return end
        if IsQDone(qid) then return end
        local q = data.quests and data.quests[qid]
        local actQ = activeQuests[qid]
        local isActReady = actQ and actQ.isComplete == true
        if (skipEliteNow and (not isActReady) and State.IsQuestElite(qid, q))
            or (skipDungeonsNow and (not isActReady) and State.IsQuestDungeon(qid, q)) then
            return
        end
        if q and not actQ then
            if (q.minLevel or 1) > pLevel or (q.questLevel or 0) > pLevel + maxOffset then
                return
            end
            if not State.IsEligibleForPlayer(qid, player) then
                return
            end
        end
        poolSet[qid] = priority or 0
        poolList[#poolList + 1] = qid
    end

    -- Expand seeds & their same-zone chain follow-ups using reusable scratch queue arrays
    local qIds = perfScratch.cqQid
    local qDepths = perfScratch.cqDepth
    local qTail = 0
    for _, qid in ipairs(seedOrder) do
        AddToPool(qid, 2)
        qTail = qTail + 1
        qIds[qTail] = qid
        qDepths[qTail] = 0
    end
    local head = 1
    while head <= qTail and #poolList < GPS_FRIEND_SCAN_CAP do
        local curQid = qIds[head]
        local curDepth = qDepths[head]
        head = head + 1
        local q = data.quests and data.quests[curQid]
        if q and curDepth < GPS_CHAIN_FOLLOW_DEPTH and type(q.nextQuestIds) == "table" then
            for _, nqid in ipairs(q.nextQuestIds) do
                local nq = data.quests and data.quests[nqid]
                if nq and IsQuestInZoneChain(nq, pZone, true) and IsSameZoneChainQuest(nq, pZone) and not poolSet[nqid] then
                    AddToPool(nqid, 1)
                    qTail = qTail + 1
                    qIds[qTail] = nqid
                    qDepths[qTail] = curDepth + 1
                end
            end
        end
    end

    local nearbyCap = hasRecentOrActive and 5 or 12
    local nearbyAdded = 0
    for i = 1, math.min(#nearbyEligible, GPS_FRIEND_SCAN_CAP) do
        if nearbyAdded >= nearbyCap or #poolList >= 16 then break end
        AddToPool(nearbyEligible[i].qid, 0)
        nearbyAdded = nearbyAdded + 1
    end

    if #poolList == 0 then
        return nil
    end

    -- Virtual lifecycle state initialized from live + immediate ready turn-ins
    local vCompleted = {}
    local vActive = {}
    local immTurnedIn = {}
    if readyPlan and readyPlan.immediate then
        for _, rst in ipairs(readyPlan.immediate) do
            if rst.questId then
                immTurnedIn[rst.questId] = true
                vCompleted[rst.questId] = true
            end
        end
    end
    for qid, entry in pairs(activeQuests or {}) do
        if entry and not immTurnedIn[qid] then
            vActive[qid] = entry.isComplete and "ready" or "active"
        end
    end

    -- Per-quest phase tracker in pivot simulation ("done" | "ready" | "active" | "avail")
    -- plus precomputed static role metadata to avoid per-iteration allocations
    local phase = {}
    local fPts = WipeMap(perfScratch.friendPts)
    local fKeys = WipeMap(perfScratch.friendKeys)
    local fHasObjBase = WipeMap(perfScratch.friendHasObjBase)
    local fHasObjLive = WipeMap(perfScratch.friendHasObjLive)
    local fPairDist = WipeMap(perfScratch.friendPairDist)
    local questsData = data.quests

    for i = 1, #poolList do
        local qid = poolList[i]
        if IsQDone(qid) or vCompleted[qid] then
            phase[qid] = "done"
        elseif vActive[qid] == "ready" then
            phase[qid] = "ready"
        elseif vActive[qid] == "active" then
            phase[qid] = "active"
        else
            phase[qid] = "avail"
        end
        local q = questsData and questsData[qid]
        local resolved = (not q) and State.ResolveQuestTurnin(qid) or nil
        fHasObjBase[qid] = QuestHasObjectives(q, nil)
        fHasObjLive[qid] = QuestHasObjectives(q, activeQuests and activeQuests[qid])
        for r = 1, 3 do
            local rAct = (r == 1 and "ACCEPT") or (r == 2 and "COMPLETE") or "TURN_IN"
            local k = "gps:" .. rAct .. ":" .. tostring(qid)
            local slot = qid * 4 + r
            fPts[slot] = GetQuestStepPoint(q, rAct, resolved) or false
            fKeys[slot] = (not IsGpsStepSkippedOrDone(k)) and k or false
        end
    end

    local outSteps = {}
    local curX, curY = pX, pY
    local curNodeKey = 0
    local totalLimit = 28
    local customDoneMap = ns.charDB and ns.charDB.completedCustomSteps
    local hasCustomDone = (type(customDoneMap) == "table" and next(customDoneMap) ~= nil)
    local hasQdbNow = ((toggles.consultQuestie ~= false) and GetQuestieDB() ~= nil)
    local profNow = (player and player.professions) or State.DetectProfessions()
    local maxQuestLv = pLevel + maxOffset
    local navFieldAvail = (ns.Nav and ns.Nav.EnsureSingleSourceField and ns.Nav.IsAvailable and ns.Nav.IsAvailable()) and true or false
    if navFieldAvail and curX and curY then
        ns.Nav.EnsureSingleSourceField(pZone, curX, curY, data, player, GPS_ZONE_FIELD_OPTS)
    end

    while #outSteps < totalLimit do
        local needHyst = (#outSteps == 0 and lastGpsTargetKey ~= nil)
        local bestQid = nil
        local bestQ, bestAct, bestKey, bestRole = nil, nil, nil, 0
        local bestDist, bestEffDist, bestCross, bestHasCoords, bestX, bestY = 0, 0, false, false, curX, curY
        local hystQid = nil
        local hystQ, hystAct, hystKey, hystRole = nil, nil, nil, 0
        local hystDist, hystCross, hystHasCoords, hystX, hystY = 0, false, false, curX, curY

        for i = 1, #poolList do
            local qid = poolList[i]
            local ph = phase[qid]
            if ph ~= "done" then
                local q = questsData and questsData[qid]
                local action, roleCode, roleChar
                if ph == "ready" then
                    action, roleCode, roleChar = "TURN_IN", 3, "t"
                elseif ph == "active" then
                    if fHasObjLive[qid] then
                        action, roleCode, roleChar = "COMPLETE", 2, "o"
                    else
                        action, roleCode, roleChar = "TURN_IN", 3, "t"
                    end
                elseif ph == "avail" then
                    action, roleCode, roleChar = "ACCEPT", 1, "s"
                end

                if action then
                    local slot = qid * 4 + roleCode
                    local gKey = fKeys[slot]
                    if gKey then
                        local blocked = false
                        if roleCode == 1 then
                            if (not hasCustomDone) and (not hasQdbNow) and q then
                                if not (vCompleted[qid] or vActive[qid] or activeQuests[qid] ~= nil or IsQDone(qid)) then
                                    if q.exclusiveTo then
                                        for _, oid in ipairs(q.exclusiveTo) do
                                            if oid ~= qid and (vCompleted[oid] or vActive[oid] or activeQuests[oid] ~= nil or IsQDone(oid)) then
                                                blocked = true
                                                break
                                            end
                                        end
                                    end
                                    if (not blocked) and (not State.IsEligibleForPlayer(qid, player)) then
                                        blocked = true
                                    end
                                    if (not blocked) and type(q.requiredSkill) == "table" then
                                        if IsRequiredSkillBlocked(q.requiredSkill, profNow, true) ~= nil then
                                            blocked = true
                                        end
                                    end
                                    if (not blocked) and (tonumber(q.minLevel) or 1) > pLevel then
                                        blocked = true
                                    end
                                    if (not blocked) and (tonumber(q.questLevel) or 0) > maxQuestLv then
                                        blocked = true
                                    end
                                    if (not blocked) and ((skipEliteNow and State.IsQuestElite(qid, q)) or (skipDungeonsNow and State.IsQuestDungeon(qid, q))) then
                                        blocked = true
                                    end
                                    if (not blocked) and q.prereqIds and #q.prereqIds > 0 then
                                        local pids = q.prereqIds
                                        if q.prereqMode == "any" then
                                            local anyDone = false
                                            for pi = 1, #pids do
                                                local pid = pids[pi]
                                                if vCompleted[pid] or IsQDone(pid) then
                                                    anyDone = true
                                                    break
                                                end
                                            end
                                            if not anyDone then
                                                blocked = true
                                            end
                                        else
                                            for pi = 1, #pids do
                                                local pid = pids[pi]
                                                if not (vCompleted[pid] or IsQDone(pid)) then
                                                    blocked = true
                                                    break
                                                end
                                            end
                                        end
                                    end
                                end
                            else
                                blocked = (State.GetQuestBlockReason(qid, "ACCEPT", player, vCompleted, vActive, toggles, FAST_BLOCK_OPTS) ~= nil)
                            end
                        end
                        if not blocked then
                            local pt = fPts[slot]
                            local ptZone = pt and pt.zoneId
                            local isCrossZone = (pt ~= false) and (ptZone ~= nil) and (ptZone ~= pZone)
                            local hasCoords = (not isCrossZone) and (pt ~= false) and (pt.x ~= nil) and (pt.y ~= nil)
                            local tx = hasCoords and pt.x or curX
                            local ty = hasCoords and pt.y or curY
                            local dist = 15.0
                            if hasCoords then
                                if curNodeKey > 0 and curNodeKey < 262144 and qid < 65536 then
                                    local pKey = curNodeKey * 262144 + slot
                                    dist = fPairDist[pKey]
                                    if dist == nil then
                                        dist = RouteDistCoord(pZone, curX, curY, ptZone or pZone, tx, ty, player, qid, roleChar) or 15.0
                                        fPairDist[pKey] = dist
                                    end
                                else
                                    dist = RouteDistCoord(pZone, curX, curY, ptZone or pZone, tx, ty, player, qid, roleChar) or 15.0
                                end
                            end
                            local crossPenalty = isCrossZone and 200.0 or 0.0
                            local prio = poolSet[qid] or 0
                            local bias = 0
                            if not isCrossZone then
                                if prio >= 2 then
                                    bias = (roleCode ~= 1) and 48.0 or 38.0
                                elseif prio == 1 then
                                    bias = (roleCode ~= 1) and 36.0 or 32.0
                                elseif roleCode ~= 1 then
                                    bias = 12.0
                                end
                            end
                            local effDist = dist + crossPenalty - bias
                            if bestQid == nil or (effDist < bestEffDist - 0.01) or (math.abs(effDist - bestEffDist) <= 0.01 and qid < bestQid) then
                                bestQid, bestQ, bestAct, bestKey, bestRole = qid, q, action, gKey, roleCode
                                bestDist, bestEffDist, bestCross, bestHasCoords, bestX, bestY = dist, effDist, isCrossZone, hasCoords, tx, ty
                            end
                            if needHyst and gKey == lastGpsTargetKey then
                                hystQid, hystQ, hystAct, hystKey, hystRole = qid, q, action, gKey, roleCode
                                hystDist, hystCross, hystHasCoords, hystX, hystY = dist, isCrossZone, hasCoords, tx, ty
                            end
                        end
                    else
                        -- Skipped step should not stall the loop infinitely
                        if roleCode == 2 then
                            phase[qid] = "ready"
                        else
                            phase[qid] = "done"
                        end
                    end
                end
            end
        end

        if not bestQid then
            break
        end

        if needHyst and hystQid and bestKey ~= lastGpsTargetKey and (hystDist - bestDist) <= GPS_HYSTERESIS_SAVING then
            bestQid, bestQ, bestAct, bestKey, bestRole = hystQid, hystQ, hystAct, hystKey, hystRole
            bestDist, bestCross, bestHasCoords, bestX, bestY = hystDist, hystCross, hystHasCoords, hystX, hystY
        end

        if #outSteps == 0 then
            lastGpsTargetKey = bestKey
        end

        local whyDetail
        if isFriendTriggered or (poolSet[bestQid] or 0) >= 1 then
            whyDetail = string.format("Friend/local chain optimization in %s (~%.0f yds walk)", player.zoneName or "current zone", (bestDist or 0) * 10)
        else
            whyDetail = string.format("Closest eligible quest in %s (~%.0f yds walk)", player.zoneName or "current zone", (bestDist or 0) * 10)
        end

        outSteps[#outSteps + 1] = BuildGpsQuestStep(bestQ, bestQid, bestAct, player, isFriendTriggered or ((poolSet[bestQid] or 0) >= 1), whyDetail, data)
        if not bestCross then
            curX = bestX or curX
            curY = bestY or curY
            if bestHasCoords then
                curNodeKey = bestQid * 4 + bestRole
            end
        end

        if bestRole == 1 then
            vActive[bestQid] = "active"
            if fHasObjBase[bestQid] then
                phase[bestQid] = "active"
            else
                phase[bestQid] = "ready"
            end
        elseif bestRole == 2 then
            vActive[bestQid] = "ready"
            phase[bestQid] = "ready"
        elseif bestRole == 3 then
            vActive[bestQid] = nil
            vCompleted[bestQid] = true
            phase[bestQid] = "done"
        end
    end

    if #outSteps == 0 then
        return nil
    end

    return {
        mode = "friend_pivot",
        zoneId = pZone,
        steps = outSteps,
        suppressedOrigIdx = {},
        endOrigIdx = nil,
    }
end

local STAGE_OF = {
    ACCEPT = 1,
    DUNGEON_PREP = 1,
    COMPLETE = 2,
    DUNGEON_RUN = 2,
    TURN_IN = 3,
}

local function SortGpsUnlocked(a, b)
    local diff = a.effDist - b.effDist
    if diff < -0.01 then return true end
    if diff > 0.01 then return false end
    local ao = a.item.origIdx or (900000 + (a.item.seq or 0))
    local bo = b.item.origIdx or (900000 + (b.item.seq or 0))
    return ao < bo
end

local function CloneFlatMap(src)
    local dst = {}
    if src then
        for k, v in pairs(src) do
            dst[k] = v
        end
    end
    return dst
end

function perfScratch.EnqueueGpsChain(qid, depth, rootSeed, rootIsActive, chainSeen, qTail)
    if not qid or depth > GPS_CHAIN_FOLLOW_DEPTH or chainSeen[qid] then
        return qTail
    end
    chainSeen[qid] = true
    qTail = qTail + 1
    perfScratch.cqQid[qTail] = qid
    perfScratch.cqDepth[qTail] = depth
    perfScratch.cqRoot[qTail] = rootSeed or qid
    perfScratch.cqAct[qTail] = (rootIsActive == true)
    return qTail
end

function perfScratch.GetCachedProactiveStep(q, qid, act, whyMsg, pZone, pZoneName, player, data)
    if perfScratch.gpsProactiveStepCacheGen ~= dbQuestStateGeneration then
        perfScratch.gpsProactiveStepCache = {}
        perfScratch.gpsProactiveStepCacheGen = dbQuestStateGeneration
    end
    local ck = tostring(pZone) .. ":" .. tostring(pZoneName) .. ":" .. act .. ":" .. tostring(qid)
    local pCache = perfScratch.gpsProactiveStepCache
    local hit = pCache[ck]
    if not hit then
        hit = BuildGpsQuestStep(q, qid, act, player, false, whyMsg .. pZoneName, data)
        pCache[ck] = hit
    end
    return hit
end

function perfScratch.AllocGpsExtraId(gbw, key)
    local extraItemIds = gbw.extraItemIds
    local eid = extraItemIds[key]
    if not eid then
        eid = gbw.nextItemId + 1
        gbw.nextItemId = eid
        extraItemIds[key] = eid
    end
    return eid
end

function perfScratch.ClearWatchersForQuest(qid, prereqWatchers, exclWatchers, dagState)
    local watchers = prereqWatchers and prereqWatchers[qid]
    if watchers then
        for w = 1, #watchers do
            local wIdx = watchers[w]
            if dagState[wIdx] ~= "hard" then
                dagState[wIdx] = nil
            end
        end
    end
    local eWatchers = exclWatchers and exclWatchers[qid]
    if eWatchers then
        for w = 1, #eWatchers do
            local wIdx = eWatchers[w]
            if dagState[wIdx] ~= "hard" then
                dagState[wIdx] = nil
            end
        end
    end
end

function perfScratch.MinPathProximity(tx, ty, wpX, wpY, wCount)
    if not (tx and ty and wCount and wCount >= 1) then
        return 9999, 9999
    end
    local minNodeDist = 9999
    for j = 1, wCount do
        local d = TurninDist(wpX[j], wpY[j], tx, ty)
        if d < minNodeDist then
            minNodeDist = d
        end
    end
    local minDetour = minNodeDist
    if wCount >= 2 then
        for j = 1, wCount - 1 do
            local ax, ay = wpX[j], wpY[j]
            local bx, by = wpX[j + 1], wpY[j + 1]
            local baseD = TurninDist(ax, ay, bx, by)
            local detour = TurninDist(ax, ay, tx, ty) + TurninDist(tx, ty, bx, by) - baseD
            if detour < minDetour then
                minDetour = detour
            end
        end
    end
    return minNodeDist, minDetour
end

function perfScratch.IsStaticAcceptManuallySkipped(qid, acceptMap, steps, pkey)
    local skippedTbl = ns.charDB and ns.charDB.skippedSteps
    if (not skippedTbl) or (not next(skippedTbl)) or (not acceptMap) then
        return false
    end
    local aEntry = acceptMap[qid]
    if aEntry == nil then
        return false
    end
    if type(aEntry) == "number" then
        local ast = steps and steps[aEntry]
        return ast ~= nil and State.IsStepSkipped(ast, aEntry, pkey)
    end
    for p = 1, #aEntry do
        local aIdx = aEntry[p]
        local ast = steps and steps[aIdx]
        if ast ~= nil and State.IsStepSkipped(ast, aIdx, pkey) then
            return true
        end
    end
    return false
end

function perfScratch.InjectOptimalPathAvailableQuests(gbw, pX, pY, player, toggles, readyPlan, prof, data)
    if (not gbw) or (not data) or (not data.quests) then
        return
    end
    local pZone = gbw.pZone
    if (not pZone) or CAPITAL_ZONES[pZone] == true then
        return
    end
    State.BuildIndexes()
    local zoneIds = zoneQuestsIndex[pZone]
    if (not zoneIds) or (#zoneIds == 0) then
        return
    end

    local baseWindowItems = gbw.baseWindowItems
    if (#baseWindowItems <= 1) and (gbw.extraLocalCount == 0) then
        return
    end
    local windowHasAccept = gbw.windowHasAccept
    local windowHasComplete = gbw.windowHasComplete
    local windowHasTurnin = gbw.windowHasTurnin
    local activeLocalSet = gbw.activeLocalSet
    local inWindowByQidBase = gbw.inWindowByQidBase

    local wpX = perfScratch.optPathWpX
    local wpY = perfScratch.optPathWpY
    wpX[1] = pX
    wpY[1] = pY
    local wCount = 1
    for i = 1, #baseWindowItems do
        local wItem = baseWindowItems[i]
        if wItem and wItem.x and wItem.y then
            if TurninDist(wpX[wCount], wpY[wCount], wItem.x, wItem.y) >= 1.0 then
                wCount = wCount + 1
                wpX[wCount] = wItem.x
                wpY[wCount] = wItem.y
                if wCount >= 24 then
                    break
                end
            end
        end
    end

    local readyImm = WipeMap(perfScratch.optPathReadyImm)
    if readyPlan and readyPlan.immediate then
        for i = 1, #readyPlan.immediate do
            local rst = readyPlan.immediate[i]
            if rst and rst.questId then
                readyImm[rst.questId] = true
            end
        end
    end

    local completedMap, bulkSynced = State.GetCompletedReadOnly()
    local checkCompSlow = (not bulkSynced) and State.IsQuestCompleted or nil
    local acceptMap = GetAcceptStepIndexMap(gbw.stepsRef)
    local questsData = data.quests
    local pLevel = gbw.pLevel
    local pkey = gbw.pkey
    local maxCandLv = pLevel + DEFAULT_MAX_LEVEL_OFFSET
    local skipEliteNow = gbw.skipEliteNow
    local skipDungeonsNow = gbw.skipDungeonsNow

    local cands = perfScratch.optPathCands
    local costs = perfScratch.optPathCosts
    local candCount = 0

    for zi = 1, #zoneIds do
        local qid = zoneIds[zi]
        local q = qid and questsData[qid]
        if q
            and (not windowHasAccept[qid])
            and activeQuests[qid] == nil
            and (not q.isDungeon)
            and not ((completedMap[qid] == true) or (checkCompSlow ~= nil and checkCompSlow(qid, true)) or readyImm[qid]) then
            local minLv = tonumber(q.minLevel) or 1
            local qLv = tonumber(q.questLevel) or minLv
            local nonTrivial = (qLv > pLevel - 7)
                or ((tonumber(q.bagSlots) or 0) > 0)
                or (q.classMask and q.classMask > 0)
            if minLv <= pLevel
                and qLv <= maxCandLv
                and nonTrivial
                and not IsGpsStepSkippedOrDone("gps:ACCEPT:" .. tostring(qid))
                and not perfScratch.IsStaticAcceptManuallySkipped(qid, acceptMap, gbw.stepsRef, pkey)
                and not (State.IsQuestExcluded and State.IsQuestExcluded(qid, true))
                and State.IsEligibleForPlayer(qid, player)
                and not (type(q.requiredSkill) == "table" and IsRequiredSkillBlocked(q.requiredSkill, prof, true))
                and not (skipEliteNow and State.IsQuestElite(qid, q))
                and not (skipDungeonsNow and State.IsQuestDungeon(qid, q))
                and IsQuestInZoneChain(q, pZone, false)
                and IsSameZoneChainQuest(q, pZone) then

                local blockedExcl = false
                if type(q.exclusiveTo) == "table" then
                    for _, oid in ipairs(q.exclusiveTo) do
                        if oid ~= qid and (
                            windowHasAccept[oid]
                            or activeQuests[oid] ~= nil
                            or readyImm[oid]
                            or (completedMap[oid] == true)
                            or (checkCompSlow ~= nil and checkCompSlow(oid, true))
                        ) then
                            blockedExcl = true
                            break
                        end
                    end
                end

                local prereqOk = true
                if (not blockedExcl) and type(q.prereqIds) == "table" and #q.prereqIds > 0 then
                    if q.prereqMode == "any" then
                        prereqOk = false
                        for pi = 1, #q.prereqIds do
                            local pid = q.prereqIds[pi]
                            if (completedMap[pid] == true)
                                or (checkCompSlow ~= nil and checkCompSlow(pid, true)) then
                                prereqOk = true
                                break
                            end
                        end
                    else
                        for pi = 1, #q.prereqIds do
                            local pid = q.prereqIds[pi]
                            if not ((completedMap[pid] == true)
                                or (checkCompSlow ~= nil and checkCompSlow(pid, true))) then
                                prereqOk = false
                                break
                            end
                        end
                    end
                end

                if (not blockedExcl) and prereqOk then
                    local sPt = GetQuestStepPoint(q, "ACCEPT")
                    if sPt and sPt.x and sPt.y and TurninDist(pX, pY, sPt.x, sPt.y) <= 30.0 then
                        local sCount = (wCount < 8) and wCount or 8
                        local sNode, sDet = perfScratch.MinPathProximity(sPt.x, sPt.y, wpX, wpY, sCount)
                        if sNode <= 12.0 or sDet <= 6.5 then
                            local pathOk = true
                            local oNode, oDet = nil, nil
                            if QuestHasObjectives(q, nil) then
                                local oPt = GetQuestStepPoint(q, "COMPLETE")
                                if oPt and oPt.x and oPt.y then
                                    oNode, oDet = perfScratch.MinPathProximity(oPt.x, oPt.y, wpX, wpY, wCount)
                                    if not (oNode <= 22.0 or oDet <= 14.0) then
                                        pathOk = false
                                    end
                                end
                            end
                            local tNode, tDet = nil, nil
                            if pathOk then
                                local tPt = GetQuestStepPoint(q, "TURN_IN")
                                if tPt and tPt.x and tPt.y then
                                    tNode, tDet = perfScratch.MinPathProximity(tPt.x, tPt.y, wpX, wpY, wCount)
                                    if not (tNode <= 22.0 or tDet <= 14.0) then
                                        pathOk = false
                                    end
                                end
                            end
                            if pathOk then
                                local sMin = math.min(sNode, math.max(0, sDet))
                                local oMin = oNode and math.min(oNode, math.max(0, oDet)) or 0
                                local tMin = tNode and math.min(tNode, math.max(0, tDet)) or 0
                                local cost = sMin + 0.35 * oMin + 0.15 * tMin
                                local ins = candCount + 1
                                for ci = 1, candCount do
                                    local cc = costs[ci]
                                    if (cost < cc - 0.001) or (math.abs(cost - cc) <= 0.001 and qid < cands[ci]) then
                                        ins = ci
                                        break
                                    end
                                end
                                if ins <= 12 then
                                    local limit = math.min(candCount + 1, 12)
                                    for sh = limit, ins + 1, -1 do
                                        cands[sh] = cands[sh - 1]
                                        costs[sh] = costs[sh - 1]
                                    end
                                    cands[ins] = qid
                                    costs[ins] = cost
                                    if candCount < 12 then
                                        candCount = candCount + 1
                                    end
                                end
                            end
                        end
                    end
                end
            end
        end
    end

    if candCount == 0 then
        return
    end

    local pZoneName = gbw.zoneName or "current zone"
    local added = 0
    for ci = 1, candCount do
        if added >= 4 then
            break
        end
        local qid = cands[ci]
        local q = qid and questsData[qid]
        if q then
            local conflict = false
            if type(q.exclusiveTo) == "table" then
                for _, oid in ipairs(q.exclusiveTo) do
                    if oid ~= qid and windowHasAccept[oid] then
                        conflict = true
                        break
                    end
                end
            end
            if not conflict then
                local validQid = (type(qid) == "number" and qid > 0 and qid < 262144)
                local aKey = "gps:ACCEPT:" .. tostring(qid)
                local stA = perfScratch.GetCachedProactiveStep(q, qid, "ACCEPT", "Optimal-path available quest in ", pZone, pZoneName, player, data)
                gbw.nextItemId = gbw.nextItemId + 1
                baseWindowItems[#baseWindowItems + 1] = {
                    id = gbw.nextItemId,
                    distId = validQid and (32768 + qid * 4 + 1) or nil,
                    origIdx = nil,
                    seq = #baseWindowItems + 1,
                    step = stA,
                    action = "ACCEPT",
                    qids = GetSingleQidList(qid),
                    qid1 = qid,
                    isSingleQ = true,
                    x = stA.x,
                    y = stA.y,
                    key = aKey,
                    isExtra = true,
                    isOptimalPath = true,
                }
                inWindowByQidBase[qid] = true
                windowHasAccept[qid] = true

                if (not windowHasComplete[qid]) and QuestHasObjectives(q, nil) then
                    local cKey = "gps:COMPLETE:" .. tostring(qid)
                    if not IsGpsStepSkippedOrDone(cKey) then
                        local stC = perfScratch.GetCachedProactiveStep(q, qid, "COMPLETE", "Optimal-path objective in ", pZone, pZoneName, player, data)
                        gbw.nextItemId = gbw.nextItemId + 1
                        baseWindowItems[#baseWindowItems + 1] = {
                            id = gbw.nextItemId,
                            distId = validQid and (32768 + qid * 4 + 2) or nil,
                            origIdx = nil,
                            seq = #baseWindowItems + 1,
                            step = stC,
                            action = "COMPLETE",
                            qids = GetSingleQidList(qid),
                            qid1 = qid,
                            isSingleQ = true,
                            x = stC.x,
                            y = stC.y,
                            key = cKey,
                            isExtra = true,
                            isOptimalPath = true,
                        }
                        windowHasComplete[qid] = true
                    end
                end

                if not windowHasTurnin[qid] then
                    local tKey = "gps:TURN_IN:" .. tostring(qid)
                    if not IsGpsStepSkippedOrDone(tKey) then
                        local stT = perfScratch.GetCachedProactiveStep(q, qid, "TURN_IN", "Optimal-path turn-in in ", pZone, pZoneName, player, data)
                        gbw.nextItemId = gbw.nextItemId + 1
                        baseWindowItems[#baseWindowItems + 1] = {
                            id = gbw.nextItemId,
                            distId = validQid and (32768 + qid * 4 + 3) or nil,
                            origIdx = nil,
                            seq = #baseWindowItems + 1,
                            step = stT,
                            action = "TURN_IN",
                            qids = GetSingleQidList(qid),
                            qid1 = qid,
                            isSingleQ = true,
                            x = stT.x,
                            y = stT.y,
                            key = tKey,
                            isExtra = true,
                            isOptimalPath = true,
                        }
                        windowHasTurnin[qid] = true
                    end
                end

                gbw.extraLocalCount = gbw.extraLocalCount + 1
                if gbw.optPathSet then
                    gbw.optPathSet[qid] = true
                end
                added = added + 1
            end
        end
    end
end

function perfScratch.AdjustUnlockedAcceptBoosts(unlocked, uCount, curX, curY, directFollowupSet, chainFollowupSet)
    local actDist, actX, actY = nil, nil, nil
    local maxRecentBoost = 0
    local maxNonChainBoost = 0
    local hasNonChainRecent = false
    local hasAnyAccept = false
    for k = 1, uCount do
        local slot = unlocked[k]
        local it = slot and slot.item
        if it then
            local act = it.action
            if act == "ACCEPT" then
                hasAnyAccept = true
            elseif act == "COMPLETE" or act == "TURN_IN" then
                local sd = slot.dist
                if (not actDist) or sd < actDist then
                    actDist = sd
                    actX = it.x
                    actY = it.y
                end
                local b = sd - slot.effDist
                if b > maxRecentBoost then
                    maxRecentBoost = b
                end
                local q1 = it.qid1
                if not (q1 and (chainFollowupSet and chainFollowupSet[q1])) then
                    if b > maxNonChainBoost then
                        maxNonChainBoost = b
                    end
                    if b >= 30.0 then
                        hasNonChainRecent = true
                    end
                end
            end
        end
    end
    if not (hasAnyAccept and actDist and actDist > 7.5) then
        return
    end
    -- Only elevate general hub/corridor ACCEPTs above active objectives when an objective
    -- is using the recentAccepted/active boost rather than an active mid-chain continuation.
    local useBoost = nil
    if hasNonChainRecent then
        useBoost = math.min(41.0, maxRecentBoost + 3.5)
    elseif maxNonChainBoost > 0 and maxRecentBoost < 20.0 then
        useBoost = maxNonChainBoost + 3.5
    end
    if not useBoost then
        return
    end
    for k = 1, uCount do
        local slot = unlocked[k]
        local it = slot and slot.item
        if it and it.action == "ACCEPT" then
            local q1 = it.qid1
            local d = slot.dist
            if not (q1 and directFollowupSet and directFollowupSet[q1] and d <= 6.0) then
                local inHub = (d <= 5.2)
                local onCorridor = false
                if (not inHub) and (d + 3.0 < actDist) and curX and curY and it.x and it.y and actX and actY then
                    local detour = TurninDist(curX, curY, it.x, it.y)
                        + TurninDist(it.x, it.y, actX, actY)
                        - TurninDist(curX, curY, actX, actY)
                    if detour <= 3.5 then
                        onCorridor = true
                    end
                end
                if inHub or onCorridor then
                    local candEff = d - useBoost
                    if candEff < slot.effDist then
                        slot.effDist = candEff
                    end
                end
            end
        end
    end
end

function perfScratch.ShouldAcceptOverrideSticky(chosen, stickySlot, curX, curY)
    if not (chosen and stickySlot and chosen.item and stickySlot.item) then
        return false
    end
    if chosen.item.action ~= "ACCEPT" or stickySlot.dist <= 7.5 then
        return false
    end
    if chosen.dist <= 5.2 then
        return true
    end
    if (chosen.dist + 3.0 < stickySlot.dist) and curX and curY and chosen.item.x and chosen.item.y and stickySlot.item.x and stickySlot.item.y then
        local detour = TurninDist(curX, curY, chosen.item.x, chosen.item.y)
            + TurninDist(chosen.item.x, chosen.item.y, stickySlot.item.x, stickySlot.item.y)
            - TurninDist(curX, curY, stickySlot.item.x, stickySlot.item.y)
        if detour <= 3.5 then
            return true
        end
    end
    return false
end

local function BuildIntraZoneReorderPlan(steps, cursor, player, pkey, toggles, readyPlan, data)
    if (not steps) or (not cursor) or cursor > #steps then
        return nil
    end
    local pZone = player.zoneId
    local pX = player.x
    local pY = player.y
    if not pZone or not pX or not pY then
        return nil
    end

    local autoAdvance = (toggles.autoAdvance ~= false)
    local pLevel = tonumber(player and player.level) or 1
    local maxOff = State.GetEffectiveMaxLevelOffset(toggles)
    local prof = player and player.professions
    local skinRank = (prof and prof.skinningRank) or -1
    local lwRank = (prof and prof.leatherworkingRank) or -1
    local consultQuestie = (toggles.consultQuestie ~= false)
    local skipEliteNow = State.ShouldSkipEliteNow(player, toggles)
    local skipDungeonsNow = State.ShouldSkipDungeonsNow(toggles)
    local navFieldAvail = (ns.Nav and ns.Nav.EnsureSingleSourceField and ns.Nav.IsAvailable and ns.Nav.IsAvailable()) and true or false
    local curMounted = (IsMounted and IsMounted()) and true or false
    local curNavEpoch = (ns.Nav and ns.Nav.GetCacheEpoch and ns.Nav.GetCacheEpoch()) or 0
    local questsData = data and data.quests

    local readyWinSig = ""
    if readyPlan then
        local rpParts = perfScratch.gpsRpParts
        local rpCount = 0
        if readyPlan.immediate then
            for i = 1, #readyPlan.immediate do
                local rst = readyPlan.immediate[i]
                if rst and rst.questId and ((questsData and questsData[rst.questId]) or rst.zoneId == pZone) then
                    rpCount = rpCount + 1
                    rpParts[rpCount] = "I:" .. tostring(rst.questId)
                end
            end
        end
        local supMap = readyPlan.suppressedRouteIdx
        local decMap = readyPlan.decorateRouteIdx
        local rbMap = readyPlan.routeBefore
        local raMap = readyPlan.routeAfter
        if (supMap and next(supMap) ~= nil)
            or (decMap and next(decMap) ~= nil)
            or (rbMap and next(rbMap) ~= nil)
            or (raMap and next(raMap) ~= nil) then
            local maxSigIdx = math.min(#steps, cursor + GPS_REORDER_WINDOW + 8)
            for idx = cursor, maxSigIdx do
                if supMap and supMap[idx] then
                    rpCount = rpCount + 1
                    rpParts[rpCount] = "S:" .. tostring(idx)
                end
                local dec = decMap and decMap[idx]
                if dec then
                    rpCount = rpCount + 1
                    rpParts[rpCount] = "D:" .. tostring(idx) .. ":" .. tostring(dec.questId or 0)
                end
                local rbList = rbMap and rbMap[idx]
                if rbList and #rbList > 0 then
                    rpCount = rpCount + 1
                    rpParts[rpCount] = "B:" .. tostring(idx) .. ":" .. tostring(#rbList) .. ":" .. tostring(rbList[1].questId or 0)
                end
                local raList = raMap and raMap[idx]
                if raList and #raList > 0 then
                    rpCount = rpCount + 1
                    rpParts[rpCount] = "A:" .. tostring(idx) .. ":" .. tostring(#raList) .. ":" .. tostring(raList[1].questId or 0)
                end
            end
        end
        if rpCount > 0 then
            readyWinSig = table.concat(rpParts, ",", 1, rpCount)
        end
    end

    local offDbLocalSig
    if perfScratch.offDbSigReadyGen == perfScratch.readySigGen
        and perfScratch.offDbSigRecSeq == recentAcceptedSeq
        and perfScratch.offDbSigZone == pZone then
        offDbLocalSig = perfScratch.offDbSigVal
    else
        local olParts = perfScratch.offDbSigParts
        local olCount = 0
        for qid, entry in pairs(activeQuests or {}) do
            if entry and not entry.isComplete and (not (questsData and questsData[qid])) then
                local rec = recentAcceptedQuests[qid]
                if rec and rec.zoneId == pZone then
                    olCount = olCount + 1
                    olParts[olCount] = tostring(qid)
                end
            end
        end
        for trim = #olParts, olCount + 1, -1 do
            olParts[trim] = nil
        end
        if olCount > 1 then
            table.sort(olParts)
        end
        offDbLocalSig = (olCount > 0) and table.concat(olParts, ",", 1, olCount) or ""
        perfScratch.offDbSigReadyGen = perfScratch.readySigGen
        perfScratch.offDbSigRecSeq = recentAcceptedSeq
        perfScratch.offDbSigZone = pZone
        perfScratch.offDbSigVal = offDbLocalSig
    end

    local completedMap, bulkSynced = State.GetCompletedReadOnly()
    local checkCompSlow = (not bulkSynced) and State.IsQuestCompleted or nil

    local gCtx = perfScratch.gpsBaseCtx
    if not (gCtx
        and gCtx.dbGen == dbQuestStateGeneration
        and gCtx.pkey == pkey
        and gCtx.stepsRef == steps
        and gCtx.pZone == pZone
        and gCtx.zoneName == player.zoneName
        and gCtx.pLevel == pLevel
        and gCtx.autoAdvance == autoAdvance
        and gCtx.manualCursor == manualCursorIndex
        and gCtx.raceId == player.raceId
        and gCtx.classId == player.classId
        and gCtx.faction == player.faction
        and gCtx.navAvail == navFieldAvail
        and gCtx.skipEliteNow == skipEliteNow
        and gCtx.skipDungeonsNow == skipDungeonsNow
        and gCtx.offDbLocalSig == offDbLocalSig) then
        gCtx = {
            dbGen = dbQuestStateGeneration,
            pkey = pkey,
            stepsRef = steps,
            pZone = pZone,
            zoneName = player.zoneName,
            pLevel = pLevel,
            autoAdvance = autoAdvance,
            manualCursor = manualCursorIndex,
            raceId = player.raceId,
            classId = player.classId,
            faction = player.faction,
            navAvail = navFieldAvail,
            skipEliteNow = skipEliteNow,
            skipDungeonsNow = skipDungeonsNow,
            offDbLocalSig = offDbLocalSig,
            byCursorKey = {},
        }
        perfScratch.gpsBaseCtx = gCtx
    end

    local gbwKey = tostring(cursor)
        .. ":" .. tostring(math.floor(pX * 0.0625) * 8 + math.floor(pY * 0.0625))
        .. ((readyWinSig == "") and "" or ("\1" .. readyWinSig))
    local gbw = gCtx.byCursorKey[gbwKey]
    if not gbw then

        local baseWindowItems = {}
        local suppressedOrigIdxBase = {}
        local windowEndIdx = cursor - 1
        local inWindowByQidBase = {}
        local nextItemId = 0

        local scanEnd = math.min(#steps, cursor + GPS_REORDER_WINDOW - 1)
        for idx = cursor, scanEnd do
            local st = steps[idx]
            if not st then break end
            if st.zoneId and st.zoneId ~= pZone then
                break
            end
            if st.action == "TRAVEL" and st.zoneId and st.zoneId ~= pZone then
                break
            end

            windowEndIdx = idx
            local isSup = readyPlan and readyPlan.suppressedRouteIdx and readyPlan.suppressedRouteIdx[idx] == true
            local skipped = State.IsStepSkipped(st, idx, pkey)
            local satisfied = autoAdvance and State.IsStepSatisfiedFast(st, steps, idx, player, pkey)
            local outleveled = (manualCursorIndex == nil or idx > manualCursorIndex) and State.IsStepOutleveled(st, player)

            if isSup then
                suppressedOrigIdxBase[idx] = true
            elseif (not skipped) and (not satisfied) and (not outleveled) then
                local decInfo = readyPlan and readyPlan.decorateRouteIdx and readyPlan.decorateRouteIdx[idx]
                local baseStep = decInfo and CloneDecoratedReadyStep(st, decInfo) or st
                local qids = (type(baseStep.questIds) == "table" and #baseStep.questIds > 0)
                    and baseStep.questIds
                    or (baseStep.questId and GetSingleQidList(baseStep.questId) or CollectStepQuestIds(baseStep))
                for _, qid in ipairs(qids) do
                    inWindowByQidBase[qid] = true
                end
                nextItemId = nextItemId + 1
                baseWindowItems[#baseWindowItems + 1] = {
                    id = nextItemId,
                    distId = ((not decInfo) and idx > 0 and idx < 32768) and idx or nil,
                    origIdx = idx,
                    seq = #baseWindowItems + 1,
                    step = baseStep,
                    action = baseStep.action or "ACCEPT",
                    qids = qids,
                    qid1 = qids[1],
                    isSingleQ = (#qids == 1),
                    x = baseStep.x,
                    y = baseStep.y,
                    key = "gps_route:" .. tostring(idx) .. ":" .. tostring(baseStep.questId or baseStep.action or ""),
                }
            end
        end

        -- If the fixed window cut cleanly mid-quest (e.g. ACCEPT in window, TURN_IN just after),
        -- pull trailing same-zone COMPLETE/TURN_IN steps for quests already inside baseWindowItems.
        local windowHasAccept = {}
        local windowHasComplete = {}
        local windowHasTurnin = {}
        for _, wItem in ipairs(baseWindowItems) do
            local qid1 = wItem.qid1
            if qid1 then
                if wItem.action == "ACCEPT" then windowHasAccept[qid1] = true
                elseif wItem.action == "COMPLETE" then windowHasComplete[qid1] = true
                elseif wItem.action == "TURN_IN" then windowHasTurnin[qid1] = true end
            end
        end
        local tailLimit = math.min(#steps, windowEndIdx + 8)
        for idx = windowEndIdx + 1, tailLimit do
            local st = steps[idx]
            if not st or (st.zoneId and st.zoneId ~= pZone) or (st.action == "TRAVEL") then
                break
            end
            local qid = st.questId
            local act = st.action or "ACCEPT"
            if qid and inWindowByQidBase[qid] and (not windowHasTurnin[qid]) and (act == "COMPLETE" or act == "TURN_IN") then
                windowEndIdx = idx
                local isSup = readyPlan and readyPlan.suppressedRouteIdx and readyPlan.suppressedRouteIdx[idx] == true
                local skipped = State.IsStepSkipped(st, idx, pkey)
                local satisfied = autoAdvance and State.IsStepSatisfiedFast(st, steps, idx, player, pkey)
                local outleveled = (manualCursorIndex == nil or idx > manualCursorIndex) and State.IsStepOutleveled(st, player)
                if isSup then
                    suppressedOrigIdxBase[idx] = true
                elseif (not skipped) and (not satisfied) and (not outleveled) then
                    local decInfo = readyPlan and readyPlan.decorateRouteIdx and readyPlan.decorateRouteIdx[idx]
                    local baseStep = decInfo and CloneDecoratedReadyStep(st, decInfo) or st
                    local qids = (type(baseStep.questIds) == "table" and #baseStep.questIds > 0)
                        and baseStep.questIds
                        or (baseStep.questId and GetSingleQidList(baseStep.questId) or CollectStepQuestIds(baseStep))
                    nextItemId = nextItemId + 1
                    baseWindowItems[#baseWindowItems + 1] = {
                        id = nextItemId,
                        distId = ((not decInfo) and idx > 0 and idx < 32768) and idx or nil,
                        origIdx = idx,
                        seq = #baseWindowItems + 1,
                        step = baseStep,
                        action = baseStep.action or "ACCEPT",
                        qids = qids,
                        qid1 = qids[1],
                        isSingleQ = (#qids == 1),
                        x = baseStep.x,
                        y = baseStep.y,
                        key = "gps_route:" .. tostring(idx) .. ":" .. tostring(baseStep.questId or baseStep.action or ""),
                    }
                    if act == "COMPLETE" then windowHasComplete[qid] = true
                    elseif act == "TURN_IN" then windowHasTurnin[qid] = true end
                end
            elseif qid and not inWindowByQidBase[qid] then
                break
            end
        end

        -- Collect active local quests & extra off-route local active steps
        local extraLocalQuests = {}
        local activeLocalSet = {}
        for qid, entry in pairs(activeQuests or {}) do
            if entry and not ((completedMap[qid] == true) or (checkCompSlow ~= nil and checkCompSlow(qid, true))) then
                local q = questsData and questsData[qid]
                if not (skipEliteNow and (not entry.isComplete) and State.IsQuestElite(qid, q))
                    and not (skipDungeonsNow and (not entry.isComplete) and State.IsQuestDungeon(qid, q)) then
                    local qz = (q and q.zoneId) or (recentAcceptedQuests[qid] and recentAcceptedQuests[qid].zoneId)
                    if qz == pZone then
                        activeLocalSet[qid] = true
                        if (not inWindowByQidBase[qid]) and (not entry.isComplete) then
                            extraLocalQuests[#extraLocalQuests + 1] = qid
                        end
                    end
                end
            end
        end

        for _, qid in ipairs(extraLocalQuests) do
            local q = questsData and questsData[qid]
            local entry = activeQuests[qid]
            local validExtraQid = (type(qid) == "number" and qid > 0 and qid < 262144)
            if entry and not entry.isComplete and QuestHasObjectives(q, entry) then
                local cKey = "gps:COMPLETE:" .. tostring(qid)
                if not IsGpsStepSkippedOrDone(cKey) then
                    local stC = BuildGpsQuestStep(q, qid, "COMPLETE", player, recentAcceptedQuests[qid] ~= nil, "Off-route active quest in " .. (player.zoneName or "current zone"), data)
                    nextItemId = nextItemId + 1
                    baseWindowItems[#baseWindowItems + 1] = {
                        id = nextItemId,
                        distId = validExtraQid and (32768 + qid * 4 + 2) or nil,
                        origIdx = nil,
                        seq = #baseWindowItems + 1,
                        step = stC,
                        action = "COMPLETE",
                        qids = GetSingleQidList(qid),
                        qid1 = qid,
                        isSingleQ = true,
                        x = stC.x,
                        y = stC.y,
                        key = cKey,
                        isExtra = true,
                    }
                end
            end
            local tKey = "gps:TURN_IN:" .. tostring(qid)
            if (entry and not entry.isComplete) and (not IsGpsStepSkippedOrDone(tKey)) then
                local stT = BuildGpsQuestStep(q, qid, "TURN_IN", player, recentAcceptedQuests[qid] ~= nil, "Turn in active quest in " .. (player.zoneName or "current zone"), data)
                nextItemId = nextItemId + 1
                baseWindowItems[#baseWindowItems + 1] = {
                    id = nextItemId,
                    distId = validExtraQid and (32768 + qid * 4 + 3) or nil,
                    origIdx = nil,
                    seq = #baseWindowItems + 1,
                    step = stT,
                    action = "TURN_IN",
                    qids = GetSingleQidList(qid),
                    qid1 = qid,
                    isSingleQ = true,
                    x = stT.x,
                    y = stT.y,
                    key = tKey,
                    isExtra = true,
                }
            end
        end

        gbw = {
            dbGen = dbQuestStateGeneration,
            pkey = pkey,
            stepsRef = steps,
            cursor = cursor,
            pZone = pZone,
            zoneName = player.zoneName,
            pLevel = pLevel,
            autoAdvance = autoAdvance,
            manualCursor = manualCursorIndex,
            raceId = player.raceId,
            classId = player.classId,
            faction = player.faction,
            navAvail = navFieldAvail,
            skipEliteNow = skipEliteNow,
            skipDungeonsNow = skipDungeonsNow,
            optPathSet = {},
            readyWinSig = readyWinSig,
            offDbLocalSig = offDbLocalSig,
            baseWindowItems = baseWindowItems,
            suppressedOrigIdxBase = suppressedOrigIdxBase,
            windowEndIdx = windowEndIdx,
            inWindowByQidBase = inWindowByQidBase,
            windowHasAccept = windowHasAccept,
            windowHasComplete = windowHasComplete,
            windowHasTurnin = windowHasTurnin,
            extraLocalCount = #extraLocalQuests,
            activeLocalSet = activeLocalSet,
            nextItemId = nextItemId,
            extraItemIds = {},
            winByChainSig = {},
        }
        perfScratch.InjectOptimalPathAvailableQuests(gbw, pX, pY, player, toggles, readyPlan, prof or State.DetectProfessions(), data)
        gCtx.byCursorKey[gbwKey] = gbw
    end

    local inWindowByQidBase = gbw.inWindowByQidBase
    local windowHasAccept = gbw.windowHasAccept
    local windowHasComplete = gbw.windowHasComplete
    local windowHasTurnin = gbw.windowHasTurnin
    local activeLocalSet = gbw.activeLocalSet

    local directFollowupSet = WipeMap(perfScratch.gpsDirectSet)
    local chainFollowupSet = WipeMap(perfScratch.gpsChainSet)
    local chainExpandQuests = perfScratch.gpsChainExpand
    local ceCount = 0
    local chainExpandedSet = WipeMap(perfScratch.gpsChainExpSet)
    local chainSeen = WipeMap(perfScratch.chainSeen)
    local cqQid = perfScratch.cqQid
    local cqDepth = perfScratch.cqDepth
    local cqRoot = perfScratch.cqRoot
    local cqAct = perfScratch.cqAct
    local qTail = 0

    if readyPlan and readyPlan.immediate then
        for _, rst in ipairs(readyPlan.immediate) do
            local rq = rst.questId and questsData and questsData[rst.questId]
            local rqz = rst.zoneId or (rq and rq.zoneId)
            if rst.questId and rqz == pZone then
                qTail = perfScratch.EnqueueGpsChain(rst.questId, 0, rst.questId, true, chainSeen, qTail)
                if rq and type(rq.nextQuestIds) == "table" then
                    for _, nid in ipairs(rq.nextQuestIds) do
                        local nq = questsData and questsData[nid]
                        if nq and IsSameZoneChainQuest(nq, pZone) then
                            directFollowupSet[nid] = true
                        end
                    end
                end
            end
        end
    end

    for fid, rec in pairs(recentChainFollowups or {}) do
        if type(rec) == "table" and ((not rec.zoneId) or rec.zoneId == pZone) then
            local fq = questsData and questsData[fid]
            if fq and IsSameZoneChainQuest(fq, pZone)
                and not (skipEliteNow and State.IsQuestElite(fid, fq))
                and not (skipDungeonsNow and State.IsQuestDungeon(fid, fq))
                and not ((completedMap[fid] == true) or (checkCompSlow ~= nil and checkCompSlow(fid, true))) then
                directFollowupSet[fid] = true
                qTail = perfScratch.EnqueueGpsChain(fid, 1, rec.fromQid or fid, true, chainSeen, qTail)
            end
        end
    end

    for qid in pairs(activeLocalSet) do
        qTail = perfScratch.EnqueueGpsChain(qid, 0, qid, true, chainSeen, qTail)
    end
    for qid in pairs(windowHasTurnin) do
        local q = questsData and questsData[qid]
        if q and q.zoneId == pZone and not (gbw.optPathSet and gbw.optPathSet[qid]) then
            qTail = perfScratch.EnqueueGpsChain(qid, 0, qid, false, chainSeen, qTail)
        end
    end

    local qHead = 1
    while qHead <= qTail and ceCount < 16 do
        local nQid = cqQid[qHead]
        local nDepth = cqDepth[qHead]
        local nRoot = cqRoot[qHead]
        local nActFlag = cqAct[qHead]
        qHead = qHead + 1
        local cq = questsData and questsData[nQid]
        if cq then
            local canTraverse = (nDepth == 0)
            if nDepth > 0 then
                local isDone = (completedMap[nQid] == true) or (checkCompSlow ~= nil and checkCompSlow(nQid, true))
                local isAct = activeQuests and activeQuests[nQid] ~= nil
                local excl = State.IsQuestExcluded and State.IsQuestExcluded(nQid, true)
                local minLv = tonumber(cq.minLevel) or 0
                local qLv = tonumber(cq.questLevel) or 0
                if (not isDone) and (not excl) and State.IsEligibleForPlayer(nQid, player)
                    and not (skipEliteNow and (not (isAct and activeQuests[nQid] and activeQuests[nQid].isComplete)) and State.IsQuestElite(nQid, cq))
                    and not (skipDungeonsNow and (not (isAct and activeQuests[nQid] and activeQuests[nQid].isComplete)) and State.IsQuestDungeon(nQid, cq))
                    and IsSameZoneChainQuest(cq, pZone)
                    and (isAct or (minLv <= pLevel and qLv <= (pLevel + maxOff))) then
                    canTraverse = true
                    if nActFlag then
                        chainFollowupSet[nRoot] = true
                        chainFollowupSet[nQid] = true
                    end
                    if ((not inWindowByQidBase[nQid] and not chainExpandedSet[nQid]) or not windowHasTurnin[nQid]) and not isAct then
                        ceCount = ceCount + 1
                        chainExpandQuests[ceCount] = nQid
                        chainExpandedSet[nQid] = true
                    end
                end
            end
            if canTraverse and nDepth < GPS_CHAIN_FOLLOW_DEPTH and type(cq.nextQuestIds) == "table" then
                for _, nid in ipairs(cq.nextQuestIds) do
                    local nq = questsData and questsData[nid]
                    if nq and not chainSeen[nid] and IsSameZoneChainQuest(nq, pZone) then
                        qTail = perfScratch.EnqueueGpsChain(nid, nDepth + 1, nRoot, nActFlag, chainSeen, qTail)
                    end
                end
            end
        end
    end
    for trim = #chainExpandQuests, ceCount + 1, -1 do
        chainExpandQuests[trim] = nil
    end

    local chainSig = (ceCount == 0) and "0" or table.concat(chainExpandQuests, ",", 1, ceCount)
    local followupSig = "0"
    if next(directFollowupSet) ~= nil or next(chainFollowupSet) ~= nil then
        local fpParts = perfScratch.gpsRpParts
        local fpCount = 0
        for qid in pairs(directFollowupSet) do
            fpCount = fpCount + 1
            fpParts[fpCount] = "d" .. tostring(qid)
        end
        for qid in pairs(chainFollowupSet) do
            fpCount = fpCount + 1
            fpParts[fpCount] = "c" .. tostring(qid)
        end
        for trim = #fpParts, fpCount + 1, -1 do
            fpParts[trim] = nil
        end
        if fpCount > 1 then
            table.sort(fpParts)
        end
        followupSig = table.concat(fpParts, ",", 1, fpCount)
    end
    local ws = gbw.winByChainSig[chainSig]
    if not ws then
        local baseWindowItems = gbw.baseWindowItems
        local isEmpty = (#baseWindowItems <= 1 and gbw.extraLocalCount == 0 and ceCount == 0)
        local windowItems = baseWindowItems
        local baseVCompleted = {}
        local baseVActive = {}
        local itemStage = {}
        local prereqWatchers = {}
        local exclWatchers = {}
        local baseUnpicked1 = {}
        local baseUnpicked2 = {}

        if not isEmpty then
            if readyPlan and readyPlan.immediate then
                for _, rst in ipairs(readyPlan.immediate) do
                    if rst.questId then
                        baseVCompleted[rst.questId] = true
                    end
                end
            end
            for qid, entry in pairs(activeQuests or {}) do
                if entry and not baseVCompleted[qid] then
                    baseVActive[qid] = entry.isComplete and "ready" or "active"
                end
            end

            -- Proactively inject same-zone chain follow-ups up to GPS_CHAIN_FOLLOW_DEPTH
            if ceCount > 0 then
                windowItems = {}
                for bIdx = 1, #baseWindowItems do
                    windowItems[bIdx] = baseWindowItems[bIdx]
                end
                local pZoneName = player.zoneName or "current zone"
                for ci = 1, ceCount do
                    local qid = chainExpandQuests[ci]
                    local q = questsData and questsData[qid]
                    local validChainQid = (type(qid) == "number" and qid > 0 and qid < 262144)
                    if q then
                        local aKey = "gps:ACCEPT:" .. tostring(qid)
                        if (not windowHasAccept[qid]) and (not IsGpsStepSkippedOrDone(aKey)) then
                            local stA = perfScratch.GetCachedProactiveStep(q, qid, "ACCEPT", "Proactive chain follow-up in ", pZone, pZoneName, player, data)
                            windowItems[#windowItems + 1] = {
                                id = perfScratch.AllocGpsExtraId(gbw, aKey),
                                distId = validChainQid and (32768 + qid * 4 + 1) or nil,
                                origIdx = nil,
                                seq = #windowItems + 1,
                                step = stA,
                                action = "ACCEPT",
                                qids = GetSingleQidList(qid),
                                qid1 = qid,
                                isSingleQ = true,
                                x = stA.x,
                                y = stA.y,
                                key = aKey,
                                isExtra = true,
                                isChainFollowup = true,
                            }
                        end
                        if (not windowHasComplete[qid]) and QuestHasObjectives(q, nil) then
                            local cKey = "gps:COMPLETE:" .. tostring(qid)
                            if not IsGpsStepSkippedOrDone(cKey) then
                                local stC = perfScratch.GetCachedProactiveStep(q, qid, "COMPLETE", "Proactive chain follow-up objective in ", pZone, pZoneName, player, data)
                                windowItems[#windowItems + 1] = {
                                    id = perfScratch.AllocGpsExtraId(gbw, cKey),
                                    distId = validChainQid and (32768 + qid * 4 + 2) or nil,
                                    origIdx = nil,
                                    seq = #windowItems + 1,
                                    step = stC,
                                    action = "COMPLETE",
                                    qids = GetSingleQidList(qid),
                                    qid1 = qid,
                                    isSingleQ = true,
                                    x = stC.x,
                                    y = stC.y,
                                    key = cKey,
                                    isExtra = true,
                                    isChainFollowup = true,
                                }
                            end
                        end
                        local tKey = "gps:TURN_IN:" .. tostring(qid)
                        if (not windowHasTurnin[qid]) and (not IsGpsStepSkippedOrDone(tKey)) then
                            local stT = perfScratch.GetCachedProactiveStep(q, qid, "TURN_IN", "Proactive chain follow-up turn-in in ", pZone, pZoneName, player, data)
                            windowItems[#windowItems + 1] = {
                                id = perfScratch.AllocGpsExtraId(gbw, tKey),
                                distId = validChainQid and (32768 + qid * 4 + 3) or nil,
                                origIdx = nil,
                                seq = #windowItems + 1,
                                step = stT,
                                action = "TURN_IN",
                                qids = GetSingleQidList(qid),
                                qid1 = qid,
                                isSingleQ = true,
                                x = stT.x,
                                y = stT.y,
                                key = tKey,
                                isExtra = true,
                                isChainFollowup = true,
                            }
                        end
                    end
                end
            end

            for i = 1, #windowItems do
                local wItem = windowItems[i]
                local stg = STAGE_OF[wItem.action] or 0
                itemStage[i] = stg
                local isAcceptItem = (wItem.action == "ACCEPT") or (wItem.action == "DUNGEON_PREP" and wItem.step and wItem.step.prepMode == "accept")
                for _, qid in ipairs(wItem.qids) do
                    if stg == 1 then
                        baseUnpicked1[qid] = (baseUnpicked1[qid] or 0) + 1
                    elseif stg == 2 then
                        baseUnpicked2[qid] = (baseUnpicked2[qid] or 0) + 1
                    end
                    if isAcceptItem then
                        local selfBucket = prereqWatchers[qid]
                        if not selfBucket then
                            selfBucket = {}
                            prereqWatchers[qid] = selfBucket
                        end
                        selfBucket[#selfBucket + 1] = i
                    end
                    local q = questsData and questsData[qid]
                    if q then
                        if type(q.exclusiveTo) == "table" then
                            for _, oid in ipairs(q.exclusiveTo) do
                                oid = tonumber(oid)
                                if oid and oid ~= qid then
                                    local eBucket = exclWatchers[oid]
                                    if not eBucket then
                                        eBucket = {}
                                        exclWatchers[oid] = eBucket
                                    end
                                    eBucket[#eBucket + 1] = i
                                end
                            end
                        end
                        if isAcceptItem and type(q.prereqIds) == "table" then
                            for _, pid in ipairs(q.prereqIds) do
                                pid = tonumber(pid)
                                if pid then
                                    local bucket = prereqWatchers[pid]
                                    if not bucket then
                                        bucket = {}
                                        prereqWatchers[pid] = bucket
                                    end
                                    bucket[#bucket + 1] = i
                                end
                            end
                        end
                    end
                end
            end
        end

        local canClampOff = (not isEmpty) and (#windowItems > 0)
        local maxReqOff = -3
        if canClampOff then
            local acceptMapWs = GetAcceptStepIndexMap(steps)
            for i = 1, #windowItems do
                local wItem = windowItems[i]
                local iAct = wItem.action
                local sq = wItem.qid1
                local q = wItem.isSingleQ and sq and questsData and questsData[sq] or nil
                if not (q and (iAct == "ACCEPT" or iAct == "COMPLETE" or iAct == "TURN_IN" or iAct == "DUNGEON_PREP" or iAct == "DUNGEON_RUN")) then
                    canClampOff = false
                    break
                end
                if (iAct == "COMPLETE" or iAct == "TURN_IN")
                    and not (windowHasAccept[sq] or activeQuests[sq] ~= nil or completedMap[sq] == true or (acceptMapWs and acceptMapWs[sq]) == nil) then
                    canClampOff = false
                    break
                end
                if not ((skipEliteNow and iAct ~= "DUNGEON_PREP" and iAct ~= "DUNGEON_RUN" and (not q.isDungeon) and q.isGroup == true and activeQuests[sq] == nil and completedMap[sq] ~= true)
                    or (skipDungeonsNow and (iAct == "DUNGEON_PREP" or iAct == "DUNGEON_RUN" or q.isDungeon == true) and activeQuests[sq] == nil and completedMap[sq] ~= true)) then
                    local reqOff = (tonumber(q.questLevel) or 0) - pLevel
                    if reqOff > maxReqOff then
                        maxReqOff = reqOff
                    end
                end
            end
        end

        ws = {
            isEmpty = isEmpty,
            windowItems = windowItems,
            baseVCompleted = baseVCompleted,
            baseVActive = baseVActive,
            itemStage = itemStage,
            prereqWatchers = prereqWatchers,
            exclWatchers = exclWatchers,
            baseUnpicked1 = baseUnpicked1,
            baseUnpicked2 = baseUnpicked2,
            staticDagByOff = {},
            planByOff = {},
            allOpenOff = canClampOff and maxReqOff or nil,
            skinRank = skinRank,
            lwRank = lwRank,
            consultQuestie = consultQuestie,
            skipEliteNow = skipEliteNow,
            skipDungeonsNow = skipDungeonsNow,
            solLiveGen = -1,
        }
        gbw.winByChainSig[chainSig] = ws
    end

    if ws.isEmpty then
        return nil
    end

    local customDoneMap = ns.charDB and ns.charDB.completedCustomSteps
    local hasCustomDone = (type(customDoneMap) == "table" and next(customDoneMap) ~= nil)
    local hasQdbNow = (consultQuestie and GetQuestieDB() ~= nil) and true or false
    if ws.skinRank ~= skinRank or ws.lwRank ~= lwRank or ws.consultQuestie ~= consultQuestie or ws.skipEliteNow ~= skipEliteNow or ws.skipDungeonsNow ~= skipDungeonsNow then
        ws.staticDagByOff = {}
        ws.planByOff = {}
        ws.skinRank = skinRank
        ws.lwRank = lwRank
        ws.consultQuestie = consultQuestie
        ws.skipEliteNow = skipEliteNow
        ws.skipDungeonsNow = skipDungeonsNow
    elseif ws.solLiveGen ~= perfScratch.gpsLiveStateGen
        or ws.solRecSeq ~= recentAcceptedSeq
        or ws.solPX ~= pX
        or ws.solPY ~= pY
        or ws.solMounted ~= curMounted
        or ws.solNavEpoch ~= curNavEpoch
        or ws.solQdb ~= hasQdbNow
        or ws.solCustomDone ~= hasCustomDone then
        ws.planByOff = {}
    end
    ws.solLiveGen = perfScratch.gpsLiveStateGen
    ws.solRecSeq = recentAcceptedSeq
    ws.solPX = pX
    ws.solPY = pY
    ws.solMounted = curMounted
    ws.solNavEpoch = curNavEpoch
    ws.solQdb = hasQdbNow
    ws.solCustomDone = hasCustomDone

    local effOff = (ws.allOpenOff and (not hasCustomDone) and (not hasQdbNow) and maxOff > ws.allOpenOff) and ws.allOpenOff or maxOff
    local entryTargetKey = lastGpsTargetKey or false
    local subKey = (followupSig == "0") and entryTargetKey or ((entryTargetKey or "") .. "\1" .. followupSig)
    local offBucket = ws.planByOff[effOff]
    if offBucket then
        local solHit = offBucket[subKey]
        if solHit then
            if solHit.hasOutTarget then
                lastGpsTargetKey = solHit.outTarget
            end
            return solHit.plan
        end
    else
        offBucket = {}
        ws.planByOff[effOff] = offBucket
    end

    local staticDagState = ws.staticDagByOff[effOff]
    if not staticDagState then
        staticDagState = {}
        ws.staticDagByOff[effOff] = staticDagState
    end

    local windowItems = ws.windowItems
    local windowEndIdx = gbw.windowEndIdx
    local itemStage = ws.itemStage
    local prereqWatchers = ws.prereqWatchers
    local exclWatchers = ws.exclWatchers

    local gd = perfScratch.gpsDist
    if gd.stepsRef ~= steps or gd.pZone ~= pZone or gd.navAvail ~= navFieldAvail
        or gd.faction ~= player.faction or gd.mounted ~= curMounted
        or gd.navEpoch ~= curNavEpoch or gd.pairCount > 16384 then
        WipeMap(gd.pairMap)
        WipeMap(gd.playerMap)
        gd.pairCount = 0
        gd.stepsRef = steps
        gd.pZone = pZone
        gd.navAvail = navFieldAvail
        gd.faction = player.faction
        gd.mounted = curMounted
        gd.navEpoch = curNavEpoch
        gd.playerX = pX
        gd.playerY = pY
    elseif gd.playerX ~= pX or gd.playerY ~= pY then
        WipeMap(gd.playerMap)
        gd.playerX = pX
        gd.playerY = pY
    end
    local pairDist = gd.pairMap
    local playerDist = gd.playerMap

    local vCompleted = perfScratch.CopyFlatMapInto(perfScratch.gpsVComp, ws.baseVCompleted)
    local vActive = perfScratch.CopyFlatMapInto(perfScratch.gpsVAct, ws.baseVActive)
    local unpickedStage1 = perfScratch.CopyFlatMapInto(perfScratch.gpsUnp1, ws.baseUnpicked1)
    local unpickedStage2 = perfScratch.CopyFlatMapInto(perfScratch.gpsUnp2, ws.baseUnpicked2)

    local dagState = WipeMap(perfScratch.gpsDagState)
    local dagVer = WipeMap(perfScratch.gpsDagVer)
    for idx, val in pairs(staticDagState) do
        dagState[idx] = val
        dagVer[idx] = 0
    end
    local hasImmReady = false
    if readyPlan and readyPlan.immediate then
        for i = 1, #readyPlan.immediate do
            local rst = readyPlan.immediate[i]
            local rqid = rst and rst.questId
            if rqid then
                vCompleted[rqid] = true
                hasImmReady = true
                perfScratch.ClearWatchersForQuest(rqid, prereqWatchers, exclWatchers, dagState)
            end
        end
    end
    local vStateVer = 0

    local picked = WipeMap(perfScratch.gpsPicked)
    local ordered = perfScratch.gpsOrdered
    local ordCount = 0
    local curX, curY = pX, pY
    local curDistId = 0

    local maxQuestLv = pLevel + effOff
    local acceptMap = GetAcceptStepIndexMap(steps)
    local profNow = prof or State.DetectProfessions()
    local fieldPrimed = false
    local unlocked = perfScratch.gpsUnlockedPool
    local hasPickedTarget = false
    local pickedTargetKey = nil

    for pickNum = 1, #windowItems do
        local uCount = 0
        for i = 1, #windowItems do
            if not picked[i] then
                local item = windowItems[i]
                local stage = itemStage[i]
                local lifecycleBlocked = false
                if stage == 2 then
                    if item.isSingleQ then
                        if (unpickedStage1[item.qid1] or 0) > 0 then
                            lifecycleBlocked = true
                        end
                    else
                        for _, qid in ipairs(item.qids) do
                            if (unpickedStage1[qid] or 0) > 0 then
                                lifecycleBlocked = true
                                break
                            end
                        end
                    end
                elseif stage == 3 then
                    if item.isSingleQ then
                        local sq = item.qid1
                        if (unpickedStage1[sq] or 0) > 0 or (unpickedStage2[sq] or 0) > 0 then
                            lifecycleBlocked = true
                        end
                    else
                        for _, qid in ipairs(item.qids) do
                            if (unpickedStage1[qid] or 0) > 0 or (unpickedStage2[qid] or 0) > 0 then
                                lifecycleBlocked = true
                                break
                            end
                        end
                    end
                end

                local dagBlocked = false
                if not lifecycleBlocked then
                    local cachedState = dagState[i]
                    if cachedState == "hard" then
                        dagBlocked = true
                    elseif cachedState == false then
                        dagBlocked = false
                    elseif cachedState == "soft" and (item.action == "ACCEPT" or (item.action == "DUNGEON_PREP" and item.step and item.step.prepMode == "accept")) then
                        dagBlocked = true
                    elseif dagVer[i] == vStateVer and cachedState ~= nil then
                        dagBlocked = (cachedState ~= false)
                    else
                        local bCode = nil
                        local usedInlineDag = false
                        if (not hasCustomDone) and (not hasQdbNow) and item.isSingleQ then
                            local qid = item.qid1
                            local q = questsData and questsData[qid]
                            local iAct = item.action
                            if q and (iAct == "ACCEPT" or iAct == "COMPLETE" or iAct == "TURN_IN" or iAct == "DUNGEON_PREP" or iAct == "DUNGEON_RUN") then
                                usedInlineDag = true
                                local isDoneQ = vCompleted[qid] or (completedMap[qid] == true) or (checkCompSlow ~= nil and checkCompSlow(qid, true))
                                local actQ = activeQuests[qid]
                                local vAct = vActive[qid]
                                local isActReady = (actQ and actQ.isComplete == true)
                                if isDoneQ then
                                    bCode = nil
                                elseif skipEliteNow and iAct ~= "DUNGEON_PREP" and iAct ~= "DUNGEON_RUN" and (not isActReady) and (not q.isDungeon) and ((q.isGroup == true) or (actQ ~= nil and actQ.isElite == true)) and (actQ ~= nil or vAct ~= nil) then
                                    bCode = "elite_solo"
                                elseif skipDungeonsNow and (not isActReady) and ((iAct == "DUNGEON_PREP" or iAct == "DUNGEON_RUN") or (q.isDungeon == true) or (actQ ~= nil and actQ.isDungeon == true)) and (actQ ~= nil or vAct ~= nil) then
                                    bCode = "dungeon_skipped"
                                elseif vAct or actQ ~= nil then
                                    bCode = nil
                                else
                                    if q.exclusiveTo then
                                        for _, oid in ipairs(q.exclusiveTo) do
                                            if oid ~= qid and (vCompleted[oid] or vActive[oid] or activeQuests[oid] ~= nil or (completedMap[oid] == true) or (checkCompSlow ~= nil and checkCompSlow(oid, true))) then
                                                bCode = "exclusive_taken"
                                                break
                                            end
                                        end
                                    end
                                    if (not bCode) and (not State.IsEligibleForPlayer(qid, player)) then
                                        bCode = "ineligible_character"
                                    end
                                    if (not bCode) and q.requiredSkill then
                                        bCode = IsRequiredSkillBlocked(q.requiredSkill, profNow, true)
                                    end
                                    if (not bCode) and (tonumber(q.minLevel) or 1) > pLevel then
                                        bCode = "min_level"
                                    end
                                    if (not bCode) and (tonumber(q.questLevel) or 0) > maxQuestLv then
                                        bCode = "quest_level_too_high"
                                    end
                                    if (not bCode) and skipEliteNow and iAct ~= "DUNGEON_PREP" and iAct ~= "DUNGEON_RUN" and (not q.isDungeon) and (q.isGroup == true) then
                                        bCode = "elite_solo"
                                    end
                                    if (not bCode) and skipDungeonsNow and ((iAct == "DUNGEON_PREP" or iAct == "DUNGEON_RUN") or q.isDungeon == true) then
                                        bCode = "dungeon_skipped"
                                    end
                                    if (not bCode) and q.prereqIds and #q.prereqIds > 0 then
                                        local pids = q.prereqIds
                                        if q.prereqMode == "any" then
                                            local anyDone = false
                                            for pi = 1, #pids do
                                                local pid = pids[pi]
                                                if vCompleted[pid] or (completedMap[pid] == true) or (checkCompSlow ~= nil and checkCompSlow(pid, true)) then
                                                    anyDone = true
                                                    break
                                                end
                                            end
                                            if not anyDone then
                                                bCode = "missing_prereq"
                                            end
                                        else
                                            for pi = 1, #pids do
                                                local pid = pids[pi]
                                                if not (vCompleted[pid] or (completedMap[pid] == true) or (checkCompSlow ~= nil and checkCompSlow(pid, true))) then
                                                    bCode = "missing_prereq"
                                                    break
                                                end
                                            end
                                        end
                                    end
                                    if (not bCode) and (iAct == "COMPLETE" or iAct == "TURN_IN") then
                                        local aEntry = acceptMap and acceptMap[qid]
                                        if aEntry ~= nil and IsAcceptSkippedEarlier(qid, steps, item.origIdx or cursor, pkey, player, vCompleted, vActive, toggles) then
                                            bCode = "accept_skipped"
                                        end
                                    end
                                end
                            end
                        end
                        if not usedInlineDag then
                            bCode = State.IsStepBlocked(item.step, steps, item.origIdx or cursor, player, pkey, vCompleted, vActive, toggles, FAST_BLOCK_OPTS)
                        end
                        dagVer[i] = vStateVer
                        if not bCode then
                            dagState[i] = false
                            dagBlocked = false
                        elseif bCode == "quest_level_too_high" or bCode == "min_level"
                            or bCode == "ineligible_character" or bCode == "wrong_profession"
                            or bCode == "missing_profession" or bCode == "skill_rank_too_low"
                            or bCode == "elite_solo" or bCode == "dungeon_skipped" then
                            dagState[i] = "hard"
                            dagBlocked = true
                        else
                            dagState[i] = "soft"
                            dagBlocked = true
                        end
                        if vStateVer == 0 and (not hasImmReady) then
                            staticDagState[i] = dagState[i]
                        end
                    end
                end

                if not lifecycleBlocked and not dagBlocked then
                    local qid1 = item.qid1
                    local roleChar = (item.action == "TURN_IN" and "t") or (item.action == "COMPLETE" and "o") or "s"
                    local itemZone = (item.step and item.step.zoneId) or pZone
                    local itemDistId = item.distId
                    local d
                    if curDistId == 0 and itemDistId then
                        d = playerDist[itemDistId]
                        if d == nil then
                            if navFieldAvail and not fieldPrimed then
                                ns.Nav.EnsureSingleSourceField(pZone, curX, curY, data, player, GPS_ZONE_FIELD_OPTS)
                                fieldPrimed = true
                            end
                            d = RouteDistCoord(pZone, curX, curY, itemZone, item.x, item.y, player, qid1, roleChar) or 25.0
                            playerDist[itemDistId] = d
                        end
                    elseif curDistId > 0 and itemDistId then
                        local pKey = curDistId * 2097152 + itemDistId
                        d = pairDist[pKey]
                        if d == nil then
                            d = RouteDistCoord(pZone, curX, curY, itemZone, item.x, item.y, player, qid1, roleChar) or 25.0
                            pairDist[pKey] = d
                            gd.pairCount = gd.pairCount + 1
                        end
                    else
                        if curDistId == 0 and navFieldAvail and not fieldPrimed then
                            ns.Nav.EnsureSingleSourceField(pZone, curX, curY, data, player, GPS_ZONE_FIELD_OPTS)
                            fieldPrimed = true
                        end
                        d = RouteDistCoord(pZone, curX, curY, itemZone, item.x, item.y, player, qid1, roleChar) or 25.0
                    end
                    local activeBoost = 0
                    if qid1 then
                        if item.action == "ACCEPT" and directFollowupSet[qid1] and d <= 42.0 then
                            activeBoost = (d <= 6.0) and 44.0 or 32.0
                        elseif (item.action == "COMPLETE" or item.action == "TURN_IN") and (directFollowupSet[qid1] or recentAcceptedQuests[qid1]) and d <= 65.0 then
                            activeBoost = 40.0
                        elseif (item.action == "TURN_IN" or item.action == "COMPLETE") and vActive[qid1] then
                            activeBoost = 3.0
                            if chainFollowupSet[qid1] and d <= 42.0 then
                                activeBoost = 24.0
                            end
                        elseif chainFollowupSet[qid1] and d <= 28.0 then
                            activeBoost = 16.0
                        end
                    end
                    uCount = uCount + 1
                    local slot = unlocked[uCount]
                    if not slot then
                        slot = {}
                        unlocked[uCount] = slot
                    end
                    slot.idx = i
                    slot.item = item
                    slot.dist = d
                    slot.effDist = d - activeBoost
                end
            end
        end

        if uCount == 0 then
            break
        end
        if uCount > 1 then
            perfScratch.AdjustUnlockedAcceptBoosts(unlocked, uCount, curX, curY, directFollowupSet, chainFollowupSet)
        end
        local chosen = unlocked[1]
        local stickyKey = (pickNum == 1) and (lastGpsTargetKey or (windowItems[1] and windowItems[1].key)) or nil
        local stickySlot = (stickyKey and chosen.item.key == stickyKey) and chosen or nil
        for k = 2, uCount do
            local cand = unlocked[k]
            if SortGpsUnlocked(cand, chosen) then
                chosen = cand
            end
            if stickyKey and (not stickySlot) and cand.item.key == stickyKey then
                stickySlot = cand
            end
        end
        if pickNum == 1 and stickySlot and chosen ~= stickySlot then
            local cQid = chosen.item.qids and chosen.item.qids[1]
            local hasLockedTarget = (lastGpsTargetKey ~= nil)
            local chosenIsDirectFollowup = cQid and (
                (chosen.item.action == "ACCEPT" and directFollowupSet[cQid] and (chosen.dist <= 12.0))
                or ((not hasLockedTarget) and chosen.item.action ~= "ACCEPT" and (directFollowupSet[cQid] or recentAcceptedQuests[cQid]))
            )
            if (not chosenIsDirectFollowup)
                and (not perfScratch.ShouldAcceptOverrideSticky(chosen, stickySlot, curX, curY))
                and ((stickySlot.dist - chosen.dist) <= GPS_HYSTERESIS_SAVING) then
                chosen = stickySlot
            end
        end

        local cItem = chosen.item
        picked[chosen.idx] = true
        local chosenStg = itemStage[chosen.idx]
        local stageMap = (chosenStg == 1 and unpickedStage1) or (chosenStg == 2 and unpickedStage2) or nil
        if stageMap then
            local qCount = cItem.isSingleQ and 1 or #cItem.qids
            for qi = 1, qCount do
                local qid = cItem.isSingleQ and cItem.qid1 or cItem.qids[qi]
                if qid then
                    local c = stageMap[qid]
                    if c and c > 1 then
                        stageMap[qid] = c - 1
                    else
                        stageMap[qid] = nil
                    end
                end
            end
        end
        ordCount = ordCount + 1
        ordered[ordCount] = cItem
        if pickNum == 1 then
            lastGpsTargetKey = cItem.key
            hasPickedTarget = true
            pickedTargetKey = cItem.key
        end
        if cItem.x and cItem.y then
            curX, curY = cItem.x, cItem.y
            curDistId = cItem.distId or -1
        end

        local act = cItem.action
        local isAcceptLike = (act == "ACCEPT" or (act == "DUNGEON_PREP" and cItem.step.prepMode == "accept"))
        local isTurnInLike = (act == "TURN_IN" or act == "DUNGEON_RUN")
        if isAcceptLike or isTurnInLike then
            vStateVer = vStateVer + 1
            local qCount = cItem.isSingleQ and 1 or #cItem.qids
            for qi = 1, qCount do
                local qid = cItem.isSingleQ and cItem.qid1 or cItem.qids[qi]
                if qid then
                    if isAcceptLike then
                        vActive[qid] = true
                        perfScratch.ClearWatchersForQuest(qid, prereqWatchers, exclWatchers, dagState)
                    else
                        vActive[qid] = nil
                        vCompleted[qid] = true
                        perfScratch.ClearWatchersForQuest(qid, prereqWatchers, exclWatchers, dagState)
                        local tq = data.quests and data.quests[qid]
                        if tq and type(tq.nextQuestIds) == "table" and (chainFollowupSet[qid] or directFollowupSet[qid] or activeLocalSet[qid]) then
                            for _, nqid in ipairs(tq.nextQuestIds) do
                                local nq = data.quests and data.quests[nqid]
                                if nq and IsSameZoneChainQuest(nq, pZone) then
                                    directFollowupSet[nqid] = true
                                    chainFollowupSet[nqid] = true
                                end
                            end
                        end
                    end
                end
            end
        end
    end
    for trim = #ordered, ordCount + 1, -1 do
        ordered[trim] = nil
    end

    if ordCount == 0 then
        offBucket[subKey] = {
            plan = nil,
            hasOutTarget = hasPickedTarget,
            outTarget = pickedTargetKey,
        }
        return nil
    end

    -- Check if ordering actually changed relative to original eligible sequence or injected chain steps
    local firstMoved = (not windowItems[1]) or (ordered[1].origIdx ~= windowItems[1].origIdx) or ordered[1].isExtra
    if not firstMoved then
        local sortedOrig = perfScratch.gpsSortedOrig
        local soCount = 0
        for i = 1, ordCount do
            local it = ordered[i]
            if not it.isExtra and it.origIdx then
                soCount = soCount + 1
                sortedOrig[soCount] = it.origIdx
            end
        end
        for trim = #sortedOrig, soCount + 1, -1 do
            sortedOrig[trim] = nil
        end
        if soCount > 1 then
            table.sort(sortedOrig)
        end
        local topChanged = false
        for i = 1, math.min(3, ordCount) do
            if ordered[i].isExtra or ordered[i].origIdx ~= sortedOrig[i] then
                topChanged = true
                break
            end
        end
        if not topChanged then
            for i = 1, ordCount do
                if ordered[i].isExtra then
                    topChanged = true
                    break
                end
            end
        end
        if not topChanged then
            offBucket[subKey] = {
                plan = nil,
                hasOutTarget = hasPickedTarget,
                outTarget = pickedTargetKey,
            }
            return nil
        end
    end

    -- Materialize steps including any routeBefore/routeAfter ready turn-ins tied to moved indices
    local suppressedOrigIdx = CloneFlatMap(gbw.suppressedOrigIdxBase)
    local outSteps = {}
    for pos = 1, ordCount do
        local it = ordered[pos]
        local oIdx = it.origIdx
        if oIdx then
            suppressedOrigIdx[oIdx] = true
            if readyPlan and readyPlan.routeBefore and readyPlan.routeBefore[oIdx] then
                for _, rbSt in ipairs(readyPlan.routeBefore[oIdx]) do
                    outSteps[#outSteps + 1] = rbSt
                end
            end
            if pos == 1 and oIdx == windowItems[1].origIdx then
                outSteps[#outSteps + 1] = it.step
            else
                outSteps[#outSteps + 1] = CloneReorderedRouteStep(it.step, oIdx, "GPS Reorder")
            end
            if readyPlan and readyPlan.routeAfter and readyPlan.routeAfter[oIdx] then
                for _, raSt in ipairs(readyPlan.routeAfter[oIdx]) do
                    outSteps[#outSteps + 1] = raSt
                end
            end
        else
            outSteps[#outSteps + 1] = it.step
        end
    end

    local resPlan = {
        mode = "gps_reorder",
        zoneId = pZone,
        steps = outSteps,
        suppressedOrigIdx = suppressedOrigIdx,
        endOrigIdx = windowEndIdx,
        firstChanged = firstMoved,
    }
    offBucket[subKey] = {
        plan = resPlan,
        hasOutTarget = hasPickedTarget,
        outTarget = pickedTargetKey,
    }
    return resPlan
end

function State.GetLocationAdaptedPlan(steps, cursor, player, pkey, toggles, readyPlan)
    toggles = toggles or (ns.charDB and ns.charDB.toggles) or {}
    if toggles.locationReorder == false then
        return nil
    end
    local useDynamic = (toggles.dynamicRescore == true) or (ns.charDB and ns.charDB.activeProfile == "custom")
    if useDynamic then
        return nil
    end
    player = player or playerCache or State.GetPlayerInfo()
    local onTaxiNow = (player and player.onTaxi) or (UnitOnTaxi and UnitOnTaxi("player"))
    if onTaxiNow then
        return nil
    end
    local locationFrozen = IsPlayerLocationFrozen(player)

    State.BuildIndexes()
    local pZone = player and player.zoneId
    if not pZone then
        return nil
    end
    local qx = player.x and math.floor(player.x * 0.5) or -1
    local qy = player.y and math.floor(player.y * 0.5) or -1
    local skipEliteNow = State.ShouldSkipEliteNow(player, toggles)
    local skipDungeonsNow = State.ShouldSkipDungeonsNow(toggles)
    local locSig = State.GetLocationPlanSignature()
    if (not locationFrozen or lastAliveLocation ~= nil)
        and locationPlanCache
        and locationPlanCache.gen == refreshGeneration
        and locationPlanCache.pkey == pkey
        and locationPlanCache.cursor == cursor
        and locationPlanCache.zoneId == pZone
        and locationPlanCache.qx == qx
        and locationPlanCache.qy == qy
        and locationPlanCache.skipEliteNow == skipEliteNow
        and locationPlanCache.skipDungeonsNow == skipDungeonsNow
        and locationPlanCache.locSig == locSig then
        return locationPlanCache.plan
    end
    if locationFrozen then
        return nil
    end

    local data = ns.GetData()
    local curStep = (steps and cursor and cursor <= #steps) and steps[cursor] or nil
    local plan = nil

    if curStep and curStep.zoneId and curStep.zoneId ~= pZone then
        plan = BuildZoneFriendPivotPlan(steps, cursor, player, pkey, toggles, readyPlan, data)
    elseif (not curStep) and cursor and cursor > #(steps or {}) then
        plan = BuildZoneFriendPivotPlan(steps, cursor, player, pkey, toggles, readyPlan, data)
    elseif curStep and (curStep.zoneId == pZone or not curStep.zoneId) then
        plan = BuildIntraZoneReorderPlan(steps, cursor, player, pkey, toggles, readyPlan, data)
    end

    if not locationPlanCache then
        locationPlanCache = {}
    end
    locationPlanCache.gen = refreshGeneration
    locationPlanCache.pkey = pkey
    locationPlanCache.cursor = cursor
    locationPlanCache.zoneId = pZone
    locationPlanCache.qx = qx
    locationPlanCache.qy = qy
    locationPlanCache.skipEliteNow = skipEliteNow
    locationPlanCache.skipDungeonsNow = skipDungeonsNow
    locationPlanCache.locSig = locSig
    locationPlanCache.plan = plan
    return plan
end

function perfScratch.HasEliteDeferredPrereq(questId, player, toggles, visited)
    if not questId then return false end
    visited = visited or {}
    if visited[questId] then return false end
    visited[questId] = true
    local data = ns.GetData()
    local q = data and data.quests and data.quests[questId]
    if not q or type(q.prereqIds) ~= "table" or #q.prereqIds == 0 then
        return false
    end
    for _, pid in ipairs(q.prereqIds) do
        if not State.IsQuestCompleted(pid, true) then
            local pq = data.quests[pid]
            local code = State.GetQuestBlockReason(pid, (pq and pq.isDungeon) and "DUNGEON_PREP" or "ACCEPT", player, nil, nil, toggles, FAST_BLOCK_OPTS)
            if code == "elite_solo" or code == "dungeon_skipped" then
                return true
            elseif code == "missing_prereq" and perfScratch.HasEliteDeferredPrereq(pid, player, toggles, visited) then
                return true
            end
        end
    end
    return false
end

function State.AdvanceRouteCursor()
    if not ns.charDB then return 1 end
    local steps, _, pkey = State.GetActiveProfileSteps()
    local total = #steps
    if total == 0 then
        ns.charDB.stepIndex = 1
        return 1
    end

    local initialCursor = math.max(1, ns.charDB.stepIndex or 1)
    local cursor = initialCursor
    if cursor > total then
        cursor = total + 1
    end

    local player = playerCache or State.GetPlayerInfo()
    local toggles = ns.charDB.toggles or {}
    local autoAdvance = toggles.autoAdvance ~= false
    local cursorBlockOpts = hasBulkSync and CURSOR_BLOCK_OPTS or nil
    local hasSkipped = (type(ns.charDB.skippedSteps) == "table" and next(ns.charDB.skippedSteps) ~= nil)
    local hasCustomDone = (type(ns.charDB.completedCustomSteps) == "table" and next(ns.charDB.completedCustomSteps) ~= nil)

    local deferred = ns.charDB.deferredSteps and ns.charDB.deferredSteps[pkey]
    if type(deferred) == "table" and next(deferred) then
        local rewindIdx = nil
        for dIdx in pairs(deferred) do
            if type(dIdx) == "number" and dIdx >= 1 and dIdx <= total then
                local dStep = steps[dIdx]
                local dSkipped = hasSkipped and State.IsStepSkipped(dStep, dIdx, pkey) or false
                local dSatisfied = autoAdvance and State.IsStepSatisfiedFast(dStep, steps, dIdx, player, pkey)
                local dOutleveled = (manualCursorIndex == nil or dIdx > manualCursorIndex) and State.IsStepOutleveled(dStep, player)
                if dSkipped or dSatisfied or dOutleveled then
                    deferred[dIdx] = nil
                    RemoveBlockedStep(State.GetStepKey(dStep, dIdx, pkey))
                elseif (manualCursorIndex == nil or dIdx >= manualCursorIndex) then
                    local dCode = State.IsStepBlocked(dStep, steps, dIdx, player, pkey, nil, nil, toggles, cursorBlockOpts)
                    local unblocked = (not dCode)
                        or (dCode == "missing_prereq" and dStep.questId and State.FindActionablePrereqStep(dStep.questId, dStep, dIdx, player, pkey, toggles) ~= nil)
                    if unblocked then
                        RemoveBlockedStep(State.GetStepKey(dStep, dIdx, pkey))
                        if (not rewindIdx) or dIdx < rewindIdx then
                            rewindIdx = dIdx
                        end
                    end
                end
            else
                deferred[dIdx] = nil
            end
        end
        if rewindIdx and rewindIdx < cursor then
            cursor = rewindIdx
            lastGpsTargetKey = nil
            for k in pairs(deferred) do
                if type(k) == "number" and k >= rewindIdx then
                    deferred[k] = nil
                end
            end
            for bKey, bEntry in pairs(blockedStepsByKey) do
                if bEntry and (bEntry.index or 0) >= rewindIdx then
                    blockedStepsByKey[bKey] = nil
                    blockedStepsCount = math.max(0, blockedStepsCount - 1)
                    blockedStepsDirty = true
                end
            end
        end
    end

    local data = ns.GetData and ns.GetData()
    local quests = data and data.quests
    local completedMap = State.GetCompletedReadOnly()
    local qdbNow = GetQuestieDB()
    local pLv = tonumber(player.level) or 24
    local maxOff = State.GetEffectiveMaxLevelOffset(toggles)
    local maxQuestLv = pLv + maxOff
    local deferCapLv = pLv + 8
    local deferSign = (maxOff >= 0) and "+" or ""

    while cursor <= total do
        local step = steps[cursor]
        local fastHigh = false
        if (not qdbNow) and (not hasCustomDone) and step and quests then
            local qid = step.questId
            local act = step.action or "ACCEPT"
            if qid and (not step.questIds)
                and (act == "ACCEPT" or act == "COMPLETE" or act == "TURN_IN")
                and (not (hasSkipped and State.IsStepSkipped(step, cursor, pkey)))
                and (type(step.expectedLevel) ~= "number" or pLv < step.expectedLevel + 2)
                and (not completedMap[qid]) and (activeQuests[qid] == nil) then
                local q = quests[qid]
                if q and (not q.exclusiveTo) and (not q.requiredSkill)
                    and State.IsEligibleForPlayer(qid, player)
                    and (tonumber(q.questLevel) or 0) > maxQuestLv then
                    fastHigh = true
                    local effectiveQLevel = tonumber(q.questLevel) or 0
                    if (step.expectedLevel or 0) <= deferCapLv then
                        ns.charDB.deferredSteps = ns.charDB.deferredSteps or {}
                        ns.charDB.deferredSteps[pkey] = ns.charDB.deferredSteps[pkey] or {}
                        ns.charDB.deferredSteps[pkey][cursor] = true
                        deferred = ns.charDB.deferredSteps[pkey]
                        RecordBlockedStep(step, cursor, pkey, "quest_level_too_high",
                            string.format("Lv %d quest exceeds max level (Lv %d %s%d = %d)", effectiveQLevel, pLv, deferSign, maxOff, maxQuestLv))
                    elseif blockedStepsCount < 32 then
                        RecordBlockedStep(step, cursor, pkey, "quest_level_too_high", "quest_level_too_high")
                    end
                    cursor = cursor + 1
                end
            end
        end

        if not fastHigh then
            local skipped = hasSkipped and State.IsStepSkipped(step, cursor, pkey) or false
            local satisfied = autoAdvance and State.IsStepSatisfiedFast(step, steps, cursor, player, pkey)
            local outleveled = (manualCursorIndex == nil or cursor > manualCursorIndex) and State.IsStepOutleveled(step, player)
            if skipped or satisfied or outleveled then
                if deferred then deferred[cursor] = nil end
                cursor = cursor + 1
            else
                local blockCode, blockReason = State.IsStepBlocked(step, steps, cursor, player, pkey, nil, nil, toggles, cursorBlockOpts)
                if blockCode == "missing_prereq" and step.questId then
                    local resolved = State.FindActionablePrereqStep(step.questId, step, cursor, player, pkey, toggles)
                    if resolved then
                        break
                    end
                    if perfScratch.HasEliteDeferredPrereq(step.questId, player, toggles) then
                        ns.charDB.deferredSteps = ns.charDB.deferredSteps or {}
                        ns.charDB.deferredSteps[pkey] = ns.charDB.deferredSteps[pkey] or {}
                        ns.charDB.deferredSteps[pkey][cursor] = true
                        deferred = ns.charDB.deferredSteps[pkey]
                    elseif deferred then
                        deferred[cursor] = nil
                    end
                    RecordBlockedStep(step, cursor, pkey, blockCode, blockReason .. " (prereq locked)")
                    cursor = cursor + 1
                elseif blockCode then
                    if ((blockCode == "quest_level_too_high" or blockCode == "min_level")
                        and ((step.expectedLevel or 0) <= deferCapLv))
                        or blockCode == "elite_solo" or blockCode == "dungeon_skipped" then
                        ns.charDB.deferredSteps = ns.charDB.deferredSteps or {}
                        ns.charDB.deferredSteps[pkey] = ns.charDB.deferredSteps[pkey] or {}
                        ns.charDB.deferredSteps[pkey][cursor] = true
                        deferred = ns.charDB.deferredSteps[pkey]
                    elseif deferred then
                        deferred[cursor] = nil
                    end
                    RecordBlockedStep(step, cursor, pkey, blockCode, blockReason)
                    cursor = cursor + 1
                else
                    break
                end
            end
        end
    end

    if initialCursor == 1 and manualCursorIndex == nil and cursor <= total
        and (tonumber(player.level) or 1) >= 15 and player.zoneId
        and not IsPlayerLocationFrozen(player)
        and steps[cursor] and steps[cursor].zoneId ~= player.zoneId then
        local catchupZone = player.zoneId
        local maxCatchupLv = (tonumber(player.level) or 15) + 2
        local scanLimit = math.min(total, cursor + 300)
        for probe = cursor + 1, scanLimit do
            local pStep = steps[probe]
            if pStep then
                if type(pStep.expectedLevel) == "number" and pStep.expectedLevel > maxCatchupLv then
                    break
                end
                if pStep.zoneId == catchupZone and pStep.action ~= "TRAVEL" and pStep.questId ~= nil then
                    local pSkip = State.IsStepSkipped(pStep, probe, pkey)
                    local pSat = autoAdvance and State.IsStepSatisfied(pStep, steps, probe, player, pkey)
                    local pOut = State.IsStepOutleveled(pStep, player)
                    local pBlk = State.IsStepBlocked(pStep, steps, probe, player, pkey, nil, nil, toggles)
                    if (not pSkip) and (not pSat) and (not pOut) and (not pBlk) then
                        if deferred then
                            for dIdx in pairs(deferred) do
                                if type(dIdx) == "number" and dIdx < probe then
                                    deferred[dIdx] = nil
                                end
                            end
                        end
                        cursor = probe
                        break
                    end
                end
            end
        end
    end

    ns.charDB.stepIndex = cursor
    return cursor
end

function perfScratch.MatchDungeonName(rawName)
    if type(rawName) ~= "string" or rawName == "" then
        return nil
    end
    local low = string.lower((rawName:gsub("^%s+", ""):gsub("%s+$", "")))
    if low == "" then
        return nil
    end
    local byName = perfScratch.dungeonByName
    if byName[low] then
        return byName[low]
    end
    local noThe = low:gsub("^the%s+", "")
    if byName[noThe] then
        return byName[noThe]
    end
    local wingPrefix = low:match("^(.-)%s+%-%s+.+$")
    if wingPrefix and wingPrefix ~= "" then
        if byName[wingPrefix] then
            return byName[wingPrefix]
        end
        local wingNoThe = wingPrefix:gsub("^the%s+", "")
        if byName[wingNoThe] then
            return byName[wingNoThe]
        end
    end
    return nil
end

function perfScratch.ResolveActiveDungeon(inInst, instMapId, instName, realZoneName, mapZoneName, subZoneName, resolvedZoneId, data)
    if not inInst then
        return nil, nil
    end
    local dungeons = data and data.dungeons or nil
    if instMapId and perfScratch.instIdToDungeon[instMapId] then
        local key = perfScratch.instIdToDungeon[instMapId]
        if dungeons and dungeons[key] then
            return key, dungeons[key]
        end
    end
    local byNameKey = perfScratch.MatchDungeonName(instName)
        or perfScratch.MatchDungeonName(realZoneName)
        or perfScratch.MatchDungeonName(mapZoneName)
        or perfScratch.MatchDungeonName(subZoneName)
    if byNameKey and dungeons and dungeons[byNameKey] then
        return byNameKey, dungeons[byNameKey]
    end
    if resolvedZoneId and perfScratch.dungeonByZoneId[resolvedZoneId] then
        local key = perfScratch.dungeonByZoneId[resolvedZoneId]
        if dungeons and dungeons[key] then
            return key, dungeons[key]
        end
    end
    return "__instance__:" .. tostring(instMapId or instName or realZoneName or "unknown"), nil
end

function perfScratch.IsPointInsideDungeon(pt, dg)
    if not dg then
        return not (pt and pt.x and pt.y)
    end
    if not pt or not pt.zoneId then
        return true
    end
    if dg.zoneId and pt.zoneId == dg.zoneId then
        return true
    end
    if dg.entranceZoneId and pt.zoneId == dg.entranceZoneId then
        if not pt.x or not pt.y then
            return true
        end
        local ec = dg.entranceCoords
        if ec and ec.x and ec.y then
            local d = TurninDist(pt.x, pt.y, ec.x, ec.y)
            if d and d <= 1.5 then
                return true
            end
        end
    end
    return false
end

function perfScratch.IsInDungeonStepSkippedOrDone(action, qid)
    local gKey = "gps:" .. tostring(action) .. ":" .. tostring(qid)
    if IsGpsStepSkippedOrDone(gKey) then
        return true
    end
    if action == "TURN_IN" and ns.charDB then
        local rKey = "ready_turnin:" .. tostring(qid)
        if (ns.charDB.skippedSteps and ns.charDB.skippedSteps[rKey])
            or (ns.charDB.completedCustomSteps and ns.charDB.completedCustomSteps[rKey]) then
            return true
        end
    end
    return false
end

function perfScratch.IsQuestExcludedInDungeon(qid, q, completedMap, vComp, vAct)
    if State.IsQuestExcluded(qid, true) then
        return true
    end
    if q and type(q.exclusiveTo) == "table" then
        for _, oid in ipairs(q.exclusiveTo) do
            if oid ~= qid and (completedMap[oid] or vComp[oid] or vAct[oid] ~= nil or State.IsQuestCompleted(oid, true)) then
                return true
            end
        end
    end
    return false
end

function perfScratch.IsDungeonQuestAvailable(qid, q, player, completedMap, vComp, vAct)
    if not q then
        return false
    end
    if completedMap[qid] or vComp[qid] or activeQuests[qid] ~= nil or vAct[qid] ~= nil then
        return false
    end
    if State.IsQuestCompleted(qid, true) then
        return false
    end
    if not State.IsEligibleForPlayer(qid, player) then
        return false
    end
    if q.requiredSkill then
        local prof = (player and player.professions) or State.DetectProfessions()
        if IsRequiredSkillBlocked(q.requiredSkill, prof, true) then
            return false
        end
    end
    if perfScratch.IsQuestExcludedInDungeon(qid, q, completedMap, vComp, vAct) then
        return false
    end
    if (tonumber(q.minLevel) or 1) > (tonumber(player and player.level) or 1) then
        return false
    end
    if not State.ArePrereqsMet(qid, vComp, true) then
        return false
    end
    return true
end

function perfScratch.MakeInDungeonStep(q, qid, action, player, dgKey, dg, dgName, dgZoneId, insideTarget, isReady, whyText, data)
    local resolvedFallback = (not q) and State.ResolveQuestTurnin(qid) or nil
    local pt = GetQuestStepPoint(q, action, resolvedFallback)
    local activeEntry = activeQuests and activeQuests[qid]
    local qName = (q and q.name) or (resolvedFallback and resolvedFallback.title) or (activeEntry and activeEntry.title) or ("Quest #" .. tostring(qid))
    local zId, zName, x, y, npcId, npcName
    if insideTarget then
        zId = dgZoneId or (pt and pt.zoneId) or (player and player.zoneId)
        zName = dgName
        x = nil
        y = nil
        if action ~= "COMPLETE" then
            npcId = pt and pt.npcId or nil
            npcName = pt and pt.npcName or nil
        end
    else
        zId = (pt and pt.zoneId) or (q and q.zoneId) or (player and player.zoneId)
        zName = (pt and pt.zoneName) or ResolveZoneNameById(zId, q and q.zoneName, data)
        x = pt and pt.x or nil
        y = pt and pt.y or nil
        if action ~= "COMPLETE" then
            npcId = pt and pt.npcId or nil
            npcName = pt and pt.npcName or nil
        end
    end
    local actionLabel = (action == "ACCEPT" and "Accept")
        or (action == "COMPLETE" and "Complete")
        or (action == "TURN_IN" and "Turn In")
        or tostring(action)
    local badges = MakeGpsBadges(q, false, data)
    badges.isGpsReordered = false
    badges.isInDungeon = true
    badges.isDungeon = true
    badges.isReadyTurnIn = (isReady == true)
    return {
        action = action,
        questId = qid,
        questIds = { qid },
        title = actionLabel .. ": " .. qName,
        zoneId = zId,
        zoneName = zName,
        x = x,
        y = y,
        npcId = npcId,
        npcName = npcName,
        expectedLevel = (q and (q.questLevel or q.minLevel)) or (activeEntry and activeEntry.level) or (player and player.level),
        xp = (q and q.xp) or 0,
        money = (q and (q.goldCopper or q.money)) or 0,
        bagSlots = (q and q.bagSlots) or 0,
        badges = badges,
        why = whyText or string.format("[In Dungeon] %s (%s)", actionLabel, dgName),
        isDynamic = true,
        isGpsReordered = true,
        isInDungeon = true,
        isDungeon = true,
        isInsideDungeonTarget = (insideTarget == true),
        isReadyTurnIn = (isReady == true),
        readyStepKey = isReady and ("ready_turnin:" .. tostring(qid)) or nil,
        dungeonId = dg and dgKey or nil,
        gpsStepKey = "gps:" .. tostring(action) .. ":" .. tostring(qid),
    }
end

function State.GetActiveDungeonPlan(player, toggles)
    player = player or playerCache or State.GetPlayerInfo()
    if not (player and player.inInstance == true and player.activeDungeonId) then
        return nil
    end
    if player.onTaxi or (UnitOnTaxi and UnitOnTaxi("player")) then
        return nil
    end

    local dgKey = player.activeDungeonId
    local pLevel = tonumber(player.level) or 24
    local skinRank = tonumber(player.skinningRank) or -1
    local lwRank = tonumber(player.leatherworkingRank) or -1
    local cache = perfScratch.dungeonPlanCache
    if cache
        and cache.gen == refreshGeneration
        and cache.sGen == questStateGeneration
        and cache.dungeonId == dgKey
        and cache.level == pLevel
        and cache.faction == player.faction
        and cache.raceId == player.raceId
        and cache.classId == player.classId
        and cache.skinRank == skinRank
        and cache.lwRank == lwRank then
        return cache.plan
    end

    State.BuildIndexes()
    local data = ns.GetData()
    local quests = data and data.quests or {}
    local dg = data and data.dungeons and data.dungeons[dgKey] or nil
    local dgName = (dg and dg.name) or player.zoneName or "Dungeon"
    local dgZoneId = (dg and dg.zoneId) or player.zoneId

    local candQids = perfScratch.inDungCandQids
    local candSeen = WipeMap(perfScratch.inDungCandSeen)
    local candCount = 0

    if dg and type(dg.questIds) == "table" then
        for _, qid in ipairs(dg.questIds) do
            if qid and not candSeen[qid] then
                candSeen[qid] = true
                candCount = candCount + 1
                candQids[candCount] = qid
            end
        end
    end

    local extraQids = perfScratch.inDungExtraQids
    local extraCount = 0
    if dg and dg.zoneId and zoneQuestsIndex[dg.zoneId] then
        for _, qid in ipairs(zoneQuestsIndex[dg.zoneId]) do
            if qid and not candSeen[qid] then
                candSeen[qid] = true
                extraCount = extraCount + 1
                extraQids[extraCount] = qid
            end
        end
    end
    for qid, entry in pairs(activeQuests) do
        if qid and entry and not candSeen[qid] then
            local q = quests[qid]
            local belongs = (q and (q.dungeonId == dgKey or (dg and dg.zoneId and q.zoneId == dg.zoneId)))
                or ((not dg) and (State.IsQuestDungeon(qid, q) or entry.isDungeon == true))
            if belongs then
                candSeen[qid] = true
                extraCount = extraCount + 1
                extraQids[extraCount] = qid
            end
        end
    end
    if extraCount > 1 then
        for trim = #extraQids, extraCount + 1, -1 do
            extraQids[trim] = nil
        end
        table.sort(extraQids)
    end
    for i = 1, extraCount do
        candCount = candCount + 1
        candQids[candCount] = extraQids[i]
    end
    for trim = #candQids, candCount + 1, -1 do
        candQids[trim] = nil
    end

    local completedMap = State.GetCompletedReadOnly()
    local vComp = WipeMap(perfScratch.inDungVComp)
    local vAct = WipeMap(perfScratch.inDungVAct)
    for i = 1, candCount do
        local qid = candQids[i]
        local entry = activeQuests[qid]
        if entry and not completedMap[qid] and not State.IsQuestCompleted(qid, true) then
            vAct[qid] = {
                isComplete = (entry.isComplete == true),
                isFailed = (entry.isFailed == true),
                fromLog = true,
            }
        end
    end

    local outSteps = {}

    -- Phase 1: Ready in-dungeon turn-ins first
    for i = 1, candCount do
        local qid = candQids[i]
        local va = vAct[qid]
        if va and va.isComplete and not va.isFailed then
            local q = quests[qid]
            local turnPt = GetQuestStepPoint(q, "TURN_IN", (not q) and State.ResolveQuestTurnin(qid) or nil)
            if perfScratch.IsPointInsideDungeon(turnPt, dg) then
                if not perfScratch.IsInDungeonStepSkippedOrDone("TURN_IN", qid) then
                    outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                        q, qid, "TURN_IN", player, dgKey, dg, dgName, dgZoneId, true, true,
                        string.format("[In Dungeon] Ready to turn in inside %s", dgName), data
                    )
                end
                vAct[qid] = nil
                vComp[qid] = true
            end
        end
    end

    -- Phase 2: Available unaccepted quests whose starters are inside this dungeon
    for _ = 1, 4 do
        local progressed = false
        for i = 1, candCount do
            local qid = candQids[i]
            local q = quests[qid]
            if perfScratch.IsDungeonQuestAvailable(qid, q, player, completedMap, vComp, vAct) then
                local startPt = GetQuestStepPoint(q, "ACCEPT")
                if perfScratch.IsPointInsideDungeon(startPt, dg) then
                    if not perfScratch.IsInDungeonStepSkippedOrDone("ACCEPT", qid) then
                        outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                            q, qid, "ACCEPT", player, dgKey, dg, dgName, dgZoneId, true, false,
                            string.format("[In Dungeon] Available inside %s", dgName), data
                        )
                    end
                    local turnPt = GetQuestStepPoint(q, "TURN_IN")
                    local hasObj = QuestHasObjectives(q, nil)
                    if (not hasObj) and perfScratch.IsPointInsideDungeon(turnPt, dg) then
                        if not perfScratch.IsInDungeonStepSkippedOrDone("TURN_IN", qid) then
                            outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                                q, qid, "TURN_IN", player, dgKey, dg, dgName, dgZoneId, true, false,
                                string.format("[In Dungeon] Turn in inside %s", dgName), data
                            )
                        end
                        vComp[qid] = true
                    else
                        local autoDone = (not hasObj) and (q.isBreadcrumb == true)
                        vAct[qid] = { isComplete = autoDone, isFailed = false, fromLog = false }
                    end
                    progressed = true
                end
            end
        end
        if not progressed then
            break
        end
    end

    -- Phase 3: Active/accepted incomplete quests inside this dungeon + in-dungeon follow-up chains
    for _ = 1, 6 do
        local incQids = perfScratch.inDungIncQids
        local incCount = 0
        for i = 1, candCount do
            local qid = candQids[i]
            local va = vAct[qid]
            if va and (not va.isComplete) and (not va.isFailed) then
                incCount = incCount + 1
                incQids[incCount] = qid
            end
        end
        if incCount == 0 then
            break
        end
        for trim = #incQids, incCount + 1, -1 do
            incQids[trim] = nil
        end
        if incCount > 1 then
            table.sort(incQids, function(a, b)
                local qa = quests[a]
                local qb = quests[b]
                local la = (qa and tonumber(qa.questLevel or qa.minLevel)) or (activeQuests[a] and tonumber(activeQuests[a].level)) or 0
                local lb = (qb and tonumber(qb.questLevel or qb.minLevel)) or (activeQuests[b] and tonumber(activeQuests[b].level)) or 0
                if la ~= lb then return la < lb end
                return a < b
            end)
        end
        local unlockedAny = false
        for idx = 1, incCount do
            local qid = incQids[idx]
            local va = vAct[qid]
            local q = quests[qid]
            if not perfScratch.IsInDungeonStepSkippedOrDone("COMPLETE", qid) then
                outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                    q, qid, "COMPLETE", player, dgKey, dg, dgName, dgZoneId, true, false,
                    string.format("[In Dungeon] Complete objectives inside %s", dgName), data
                )
            end
            va.isComplete = true
            local turnPt = GetQuestStepPoint(q, "TURN_IN", (not q) and State.ResolveQuestTurnin(qid) or nil)
            if perfScratch.IsPointInsideDungeon(turnPt, dg) then
                if not perfScratch.IsInDungeonStepSkippedOrDone("TURN_IN", qid) then
                    outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                        q, qid, "TURN_IN", player, dgKey, dg, dgName, dgZoneId, true, false,
                        string.format("[In Dungeon] Turn in inside %s", dgName), data
                    )
                end
                vAct[qid] = nil
                vComp[qid] = true
                for cIdx = 1, candCount do
                    local cqid = candQids[cIdx]
                    local cq = quests[cqid]
                    if perfScratch.IsDungeonQuestAvailable(cqid, cq, player, completedMap, vComp, vAct) then
                        local startPt = GetQuestStepPoint(cq, "ACCEPT")
                        if perfScratch.IsPointInsideDungeon(startPt, dg) then
                            if not perfScratch.IsInDungeonStepSkippedOrDone("ACCEPT", cqid) then
                                outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                                    cq, cqid, "ACCEPT", player, dgKey, dg, dgName, dgZoneId, true, false,
                                    string.format("[In Dungeon] Follow-up inside %s", dgName), data
                                )
                            end
                            local cTurnPt = GetQuestStepPoint(cq, "TURN_IN")
                            local cHasObj = QuestHasObjectives(cq, nil)
                            if (not cHasObj) and perfScratch.IsPointInsideDungeon(cTurnPt, dg) then
                                if not perfScratch.IsInDungeonStepSkippedOrDone("TURN_IN", cqid) then
                                    outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                                        cq, cqid, "TURN_IN", player, dgKey, dg, dgName, dgZoneId, true, false,
                                        string.format("[In Dungeon] Turn in inside %s", dgName), data
                                    )
                                end
                                vComp[cqid] = true
                            else
                                vAct[cqid] = { isComplete = ((not cHasObj) and (cq.isBreadcrumb == true)), isFailed = false, fromLog = false }
                                unlockedAny = true
                            end
                        end
                    end
                end
            end
        end
        if not unlockedAny then
            break
        end
    end

    -- Phase 4: Ready & post-run outdoor turn-ins for this dungeon's quests
    local outQids = perfScratch.inDungOutQids
    local outCount = 0
    for i = 1, candCount do
        local qid = candQids[i]
        local va = vAct[qid]
        if va and va.isComplete and not va.isFailed then
            outCount = outCount + 1
            outQids[outCount] = qid
        end
    end
    for trim = #outQids, outCount + 1, -1 do
        outQids[trim] = nil
    end
    if outCount > 1 then
        table.sort(outQids, function(a, b)
            local fa = (vAct[a] and vAct[a].fromLog == true) and 1 or 0
            local fb = (vAct[b] and vAct[b].fromLog == true) and 1 or 0
            if fa ~= fb then return fa > fb end
            local pa = GetQuestStepPoint(quests[a], "TURN_IN")
            local pb = GetQuestStepPoint(quests[b], "TURN_IN")
            local za = pa and tonumber(pa.zoneId) or 0
            local zb = pb and tonumber(pb.zoneId) or 0
            if za ~= zb then return za < zb end
            return a < b
        end)
    end
    for i = 1, outCount do
        local qid = outQids[i]
        local va = vAct[qid]
        local q = quests[qid]
        local isRealReady = (va and va.fromLog == true)
        if not perfScratch.IsInDungeonStepSkippedOrDone("TURN_IN", qid) then
            local turnPt = GetQuestStepPoint(q, "TURN_IN", (not q) and State.ResolveQuestTurnin(qid) or nil)
            local outZName = (turnPt and (turnPt.zoneName or ResolveZoneNameById(turnPt.zoneId, q and q.zoneName, data))) or dgName
            local whyMsg = isRealReady
                and string.format("[Dungeon Complete] Ready to turn in (%s)", outZName)
                or string.format("[After %s] Turn in (%s)", dgName, outZName)
            outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                q, qid, "TURN_IN", player, dgKey, dg, dgName, dgZoneId, false, isRealReady, whyMsg, data
            )
        end
        vAct[qid] = nil
        vComp[qid] = true
    end

    -- Phase 5: Eligible outdoor-starter / shareable quests for this dungeon
    local shareQids = outQids
    local shareCount = 0
    local entZone = dg and dg.entranceZoneId or nil
    for i = 1, candCount do
        local qid = candQids[i]
        local q = quests[qid]
        if perfScratch.IsDungeonQuestAvailable(qid, q, player, completedMap, vComp, vAct) then
            local startPt = GetQuestStepPoint(q, "ACCEPT")
            if not perfScratch.IsPointInsideDungeon(startPt, dg) then
                shareCount = shareCount + 1
                shareQids[shareCount] = qid
            end
        end
    end
    for trim = #shareQids, shareCount + 1, -1 do
        shareQids[trim] = nil
    end
    if shareCount > 1 then
        table.sort(shareQids, function(a, b)
            local qa = quests[a]
            local qb = quests[b]
            local pa = GetQuestStepPoint(qa, "ACCEPT")
            local pb = GetQuestStepPoint(qb, "ACCEPT")
            local ea = (entZone and pa and pa.zoneId == entZone) and 1 or 0
            local eb = (entZone and pb and pb.zoneId == entZone) and 1 or 0
            if ea ~= eb then return ea > eb end
            local la = (qa and tonumber(qa.questLevel or qa.minLevel)) or 0
            local lb = (qb and tonumber(qb.questLevel or qb.minLevel)) or 0
            if la ~= lb then return la < lb end
            return a < b
        end)
    end
    for i = 1, shareCount do
        local qid = shareQids[i]
        local q = quests[qid]
        if not perfScratch.IsInDungeonStepSkippedOrDone("ACCEPT", qid) then
            local startPt = GetQuestStepPoint(q, "ACCEPT")
            local sZName = (startPt and (startPt.zoneName or ResolveZoneNameById(startPt.zoneId, q and q.zoneName, data))) or "outside"
            outSteps[#outSteps + 1] = perfScratch.MakeInDungeonStep(
                q, qid, "ACCEPT", player, dgKey, dg, dgName, dgZoneId, false, false,
                string.format("[In Dungeon: %s] Ask party to share or pick up in %s", dgName, sZName), data
            )
        end
    end

    local resultPlan = (#outSteps > 0) and {
        mode = "in_dungeon",
        dungeonId = dgKey,
        dungeonName = dgName,
        zoneId = dgZoneId,
        steps = outSteps,
        suppressedOrigIdx = {},
    } or nil

    perfScratch.dungeonPlanCache = {
        gen = refreshGeneration,
        sGen = questStateGeneration,
        dungeonId = dgKey,
        level = pLevel,
        faction = player.faction,
        raceId = player.raceId,
        classId = player.classId,
        skinRank = skinRank,
        lwRank = lwRank,
        plan = resultPlan,
    }
    return resultPlan
end

local function ComputeCurrentStep(player, cdb, toggles, useDynamic)
    local steps, profile, pkey, isAdaptedChar = State.GetActiveProfileSteps()
    local cursor = State.AdvanceRouteCursor()
    local dPlan = (not useDynamic) and player and player.inInstance and State.GetActiveDungeonPlan(player, toggles) or nil
    local plan = (not useDynamic) and State.GetReadyTurnInPlan(steps, cursor, player, pkey, toggles) or nil
    local locPlan = (not useDynamic) and (dPlan or State.GetLocationAdaptedPlan(steps, cursor, player, pkey, toggles, plan)) or nil

    if dPlan and dPlan.steps and dPlan.steps[1] then
        return State.DecorateStepChain(dPlan.steps[1], player, toggles), {
            source = "in_dungeon",
            profileKey = pkey,
            profile = profile,
            stepIndex = cursor,
            totalSteps = #steps,
            readyPlan = plan,
            readyCount = plan and plan.readyCount or State.GetReadyTurnInCount(),
            locationPlan = dPlan,
            locationPos = 1,
        }
    end

    if (not useDynamic) and plan and plan.immediate and plan.immediate[1] then
        return State.DecorateStepChain(plan.immediate[1], player, toggles), {
            source = "ready_turnin",
            profileKey = pkey,
            profile = profile,
            stepIndex = cursor,
            totalSteps = #steps,
            readyPlan = plan,
            readyCount = plan.readyCount,
            readyImmediateIdx = 1,
            locationPlan = locPlan,
        }
    end

    if (not useDynamic) and isAdaptedChar and ns.Scorer and ns.Scorer.GetPriorityClassQuestStep then
        local routeZoneId = (cursor <= #steps and steps[cursor] and steps[cursor].zoneId) or nil
        local classStep = ns.Scorer.GetPriorityClassQuestStep(player, toggles, routeZoneId)
        if classStep and ((not locPlan) or locPlan.mode ~= "friend_pivot" or (classStep.zoneId == player.zoneId)) then
            return State.DecorateStepChain(classStep, player, toggles), {
                source = "class_quest",
                profileKey = pkey,
                profile = profile,
                stepIndex = cursor,
                totalSteps = #steps,
                readyPlan = plan,
                readyCount = plan and plan.readyCount or State.GetReadyTurnInCount(),
                locationPlan = locPlan,
            }
        end
    end

    if (not useDynamic) and locPlan and locPlan.steps and locPlan.steps[1] then
        return State.DecorateStepChain(locPlan.steps[1], player, toggles), {
            source = locPlan.mode or "gps_reorder",
            profileKey = pkey,
            profile = profile,
            stepIndex = cursor,
            totalSteps = #steps,
            readyPlan = plan,
            readyCount = plan and plan.readyCount or State.GetReadyTurnInCount(),
            locationPlan = locPlan,
            locationPos = 1,
        }
    end

    if (not useDynamic) and cursor <= #steps then
        if plan and plan.routeBefore and plan.routeBefore[cursor] and plan.routeBefore[cursor][1] then
            return State.DecorateStepChain(plan.routeBefore[cursor][1], player, toggles), {
                source = "ready_turnin",
                profileKey = pkey,
                profile = profile,
                stepIndex = cursor,
                totalSteps = #steps,
                readyPlan = plan,
                readyCount = plan.readyCount,
                readyRouteBeforeIdx = cursor,
                readyRouteBeforePos = 1,
                locationPlan = locPlan,
            }
        end
        local step = steps[cursor]
        local blockCode = State.IsStepBlocked(step, steps, cursor, player, pkey, nil, nil, toggles)
        if blockCode == "missing_prereq" and step.questId then
            local resolved = State.FindActionablePrereqStep(step.questId, step, cursor, player, pkey, toggles)
            if resolved then
                return State.DecorateStepChain(resolved, player, toggles), {
                    source = "route",
                    profileKey = pkey,
                    profile = profile,
                    stepIndex = cursor,
                    totalSteps = #steps,
                    resolvedPrereq = true,
                    blockedStep = step,
                    readyPlan = plan,
                    readyCount = plan and plan.readyCount or State.GetReadyTurnInCount(),
                    locationPlan = locPlan,
                }
            end
        end
        local decInfo = plan and plan.decorateRouteIdx and plan.decorateRouteIdx[cursor]
        local outStep = decInfo and CloneDecoratedReadyStep(step, decInfo) or step
        return State.DecorateStepChain(outStep, player, toggles), {
            source = "route",
            profileKey = pkey,
            profile = profile,
            stepIndex = cursor,
            totalSteps = #steps,
            readyPlan = plan,
            readyCount = plan and plan.readyCount or State.GetReadyTurnInCount(),
            locationPlan = locPlan,
        }
    end

    if ns.Scorer and ns.Scorer.GetDynamicSteps then
        local dynamic = ns.Scorer.GetDynamicSteps(player, ns.GetActiveWeights(), toggles, 20)
        if dynamic and dynamic[1] then
            return State.DecorateStepChain(dynamic[1], player, toggles), {
                source = "dynamic",
                profileKey = cdb.activeProfile or "custom",
                profile = profile,
                stepIndex = 1,
                totalSteps = #dynamic,
                dynamicList = dynamic,
                readyCount = State.GetReadyTurnInCount(),
            }
        end
    end

    return nil, {
        source = "complete",
        profileKey = pkey,
        profile = profile,
        stepIndex = cursor,
        totalSteps = #steps,
        readyCount = State.GetReadyTurnInCount(),
    }
end

function State.GetCurrentStep(playerHint)
    local player = playerHint or State.GetPlayerInfo()
    local cdb = ns.charDB or {}
    local toggles = cdb.toggles or {}
    local useDynamic = (toggles.dynamicRescore == true) or (cdb.activeProfile == "custom")
    local maxOff = State.GetEffectiveMaxLevelOffset(toggles)
    local hReady = (ns.Nav and ns.Nav.IsHearthReady and ns.Nav.IsHearthReady()) and 1 or 0
    local hasQdb = (_G.QuestieDB ~= nil or _G.QuestieLoader ~= nil) and 1 or 0
    local w = useDynamic and (ns.GetActiveWeights and ns.GetActiveWeights()) or nil
    local wXp = w and (tonumber(w.xp) or 45) or 0
    local wTrv = w and (tonumber(w.travel) or 25) or 0
    local wDun = w and (tonumber(w.dungeon) or 20) or 0
    local wRew = w and (tonumber(w.reward) or 10) or 0
    local wSkin = w and (tonumber(w.skinning or w.profession) or 0) or 0
    local lastAliveSet = (lastAliveLocation ~= nil)

    local tAutoAdv = (toggles.autoAdvance ~= false)
    local tLoc = (toggles.locationReorder ~= false)
    local tRed = (toggles.allowRedQuests == true)
    local tQuestie = (toggles.consultQuestie ~= false)
    local tChains = (toggles.proactiveChains ~= false)
    local tAutoChar = (toggles.autoDetectChar ~= false)
    local tDun = (toggles.preferDungeonUnlocks ~= false)
    local tBags = (toggles.prioritizeBags ~= false)
    local tGear = (toggles.prioritizeClassGear ~= false and toggles.prioritizeRogueGear ~= false)
    local tSkin = (toggles.prioritizeSkinningLW ~= false)
    local tSkipElite = (toggles.skipEliteSolo ~= false)
    local tSkipDungeons = (toggles.skipDungeons ~= false)

    local cs = currentStepCache
    if cs
        and cs.gen == refreshGeneration
        and cs.sGen == questStateGeneration
        and cs.activeProfile == cdb.activeProfile
        and cs.stepIndex == cdb.stepIndex
        and cs.manualCursor == manualCursorIndex
        and cs.useDynamic == useDynamic
        and cs.maxOff == maxOff
        and cs.lastAliveSet == lastAliveSet
        and cs.level == player.level
        and cs.xpPct == player.xpPct
        and cs.zoneId == player.zoneId
        and cs.x == player.x
        and cs.y == player.y
        and cs.isDead == player.isDead
        and cs.onTaxi == player.onTaxi
        and cs.inGroup == player.inGroup
        and cs.inInstance == (player.inInstance == true)
        and cs.activeDungeonId == player.activeDungeonId
        and cs.raceId == player.raceId
        and cs.classId == player.classId
        and cs.faction == player.faction
        and cs.skinRank == player.skinningRank
        and cs.lwRank == player.leatherworkingRank
        and cs.hasSkin == player.hasSkinning
        and cs.hasLw == player.hasLeatherworking
        and cs.tAutoAdv == tAutoAdv
        and cs.tLoc == tLoc
        and cs.tRed == tRed
        and cs.tQuestie == tQuestie
        and cs.tChains == tChains
        and cs.tAutoChar == tAutoChar
        and cs.tDun == tDun
        and cs.tBags == tBags
        and cs.tGear == tGear
        and cs.tSkin == tSkin
        and cs.tSkipElite == tSkipElite
        and cs.tSkipDungeons == tSkipDungeons
        and cs.wXp == wXp
        and cs.wTrv == wTrv
        and cs.wDun == wDun
        and cs.wRew == wRew
        and cs.wSkin == wSkin
        and cs.hReady == hReady
        and cs.hasQdb == hasQdb then
        return cs.step, cs.meta
    end

    local outStep, outMeta = ComputeCurrentStep(player, cdb, toggles, useDynamic)
    if not currentStepCache then
        currentStepCache = {}
    end
    local csOut = currentStepCache
    csOut.gen = refreshGeneration
    csOut.sGen = questStateGeneration
    csOut.activeProfile = cdb.activeProfile
    csOut.stepIndex = cdb.stepIndex
    csOut.manualCursor = manualCursorIndex
    csOut.useDynamic = useDynamic
    csOut.maxOff = maxOff
    csOut.lastAliveSet = lastAliveSet
    csOut.level = player.level
    csOut.xpPct = player.xpPct
    csOut.zoneId = player.zoneId
    csOut.x = player.x
    csOut.y = player.y
    csOut.isDead = player.isDead
    csOut.onTaxi = player.onTaxi
    csOut.inGroup = player.inGroup
    csOut.inInstance = (player.inInstance == true)
    csOut.activeDungeonId = player.activeDungeonId
    csOut.raceId = player.raceId
    csOut.classId = player.classId
    csOut.faction = player.faction
    csOut.skinRank = player.skinningRank
    csOut.lwRank = player.leatherworkingRank
    csOut.hasSkin = player.hasSkinning
    csOut.hasLw = player.hasLeatherworking
    csOut.tAutoAdv = tAutoAdv
    csOut.tLoc = tLoc
    csOut.tRed = tRed
    csOut.tQuestie = tQuestie
    csOut.tChains = tChains
    csOut.tAutoChar = tAutoChar
    csOut.tDun = tDun
    csOut.tBags = tBags
    csOut.tGear = tGear
    csOut.tSkin = tSkin
    csOut.tSkipElite = tSkipElite
    csOut.tSkipDungeons = tSkipDungeons
    csOut.wXp = wXp
    csOut.wTrv = wTrv
    csOut.wDun = wDun
    csOut.wRew = wRew
    csOut.wSkin = wSkin
    csOut.hReady = hReady
    csOut.hasQdb = hasQdb
    csOut.step = outStep
    csOut.meta = outMeta
    return outStep, outMeta
end

function State.GetUpcomingSteps(limit)
    limit = limit or 5
    local current, meta = State.GetCurrentStep()
    local out = {}
    if not current then
        return out, meta
    end

    if meta.source == "dynamic" then
        local list = meta.dynamicList or {}
        for i = 2, math.min(#list, limit + 1) do
            out[#out + 1] = State.DecorateStepChain(list[i], playerCache or State.GetPlayerInfo(), (ns.charDB and ns.charDB.toggles) or {})
        end
        return out, meta
    end

    local steps, _, pkey = State.GetActiveProfileSteps()
    local player = playerCache or State.GetPlayerInfo()
    local toggles = (ns.charDB and ns.charDB.toggles) or {}
    local autoAdvance = toggles.autoAdvance ~= false

    local simCompleted = {}
    local vActive = {}
    for qid, entry in pairs(State.GetActiveQuests() or {}) do
        if entry and not State.IsQuestCompleted(qid, true) then
            vActive[qid] = true
        end
    end
    local emittedActionQids = {}
    local function SimulateStep(st)
        if not st then return end
        local act = st.action or "ACCEPT"
        local qids = CollectStepQuestIds(st)
        for _, qid in ipairs(qids) do
            emittedActionQids[act .. ":" .. tostring(qid)] = true
            if act == "ACCEPT" or (act == "DUNGEON_PREP" and st.prepMode == "accept") then
                vActive[qid] = true
            elseif act == "TURN_IN" or act == "DUNGEON_RUN" then
                vActive[qid] = nil
                simCompleted[qid] = true
            end
        end
    end

    local plan = meta.readyPlan or State.GetReadyTurnInPlan(steps, meta.stepIndex, player, pkey, toggles)
    local locPlan = meta.locationPlan

    local function PushStep(st)
        if not st or #out >= limit then return false end
        local dec = State.DecorateStepChain(st, player, toggles)
        out[#out + 1] = dec
        SimulateStep(dec)
        return #out < limit
    end

    local function PushFriendReturnIfNeeded()
        if #out >= limit then return false end
        if locPlan and locPlan.mode == "friend_pivot" and meta.stepIndex and meta.stepIndex <= #steps then
            local nextRouteStep = steps[meta.stepIndex]
            if nextRouteStep and nextRouteStep.zoneId and player.zoneId
                and nextRouteStep.zoneId ~= player.zoneId and nextRouteStep.action ~= "TRAVEL" then
                local data = ns.GetData()
                local targetZoneName = ResolveZoneNameById(nextRouteStep.zoneId, nextRouteStep.zoneName, data)
                return PushStep({
                    action = "TRAVEL",
                    zoneId = nextRouteStep.zoneId,
                    zoneName = targetZoneName,
                    x = nextRouteStep.x,
                    y = nextRouteStep.y,
                    expectedLevel = nextRouteStep.expectedLevel,
                    title = "Return to Route: " .. tostring(targetZoneName),
                    why = "[Friend Chain] Resume main route in " .. tostring(targetZoneName),
                    isGpsReordered = true,
                    isFriendChain = true,
                    gpsStepKey = "gps:RETURN_ROUTE:" .. tostring(meta.stepIndex),
                })
            end
        end
        return true
    end

    SimulateStep(current)

    local startIdx = meta.stepIndex
    if meta.source == "ready_turnin" then
        if meta.readyImmediateIdx then
            local imm = plan and plan.immediate or {}
            for i = meta.readyImmediateIdx + 1, #imm do
                if not PushStep(imm[i]) then return out, meta end
            end
            if locPlan and locPlan.steps then
                for i = 1, #locPlan.steps do
                    if not PushStep(locPlan.steps[i]) then return out, meta end
                end
                if not PushFriendReturnIfNeeded() then return out, meta end
            end
        elseif meta.readyRouteBeforeIdx then
            local rb = plan and plan.routeBefore and plan.routeBefore[meta.readyRouteBeforeIdx] or {}
            for i = (meta.readyRouteBeforePos or 1) + 1, #rb do
                if not PushStep(rb[i]) then return out, meta end
            end
        end
    elseif meta.source == "in_dungeon" or meta.source == "gps_reorder" or meta.source == "friend_pivot" then
        if locPlan and locPlan.steps then
            local startPos = (meta.locationPos or 1) + 1
            for i = startPos, #locPlan.steps do
                if not PushStep(locPlan.steps[i]) then return out, meta end
            end
            if not PushFriendReturnIfNeeded() then return out, meta end
        end
        startIdx = meta.stepIndex
    elseif meta.source == "class_quest" or meta.resolvedPrereq then
        if meta.source == "class_quest" and locPlan and locPlan.steps then
            for i = 1, #locPlan.steps do
                if not PushStep(locPlan.steps[i]) then return out, meta end
            end
            if not PushFriendReturnIfNeeded() then return out, meta end
        end
        startIdx = meta.stepIndex
    else
        local ra = plan and plan.routeAfter and plan.routeAfter[meta.stepIndex]
        if ra then
            for i = 1, #ra do
                if not PushStep(ra[i]) then return out, meta end
            end
        end
        startIdx = meta.stepIndex + 1
    end

    for idx = startIdx, #steps do
        if #out >= limit then break end
        local st = steps[idx]
        if st then
            local isLocSup = (locPlan and locPlan.suppressedOrigIdx and locPlan.suppressedOrigIdx[idx] == true)
            if isLocSup then
                SimulateStep(st)
            else
                if not (meta.source == "ready_turnin" and meta.readyRouteBeforeIdx == idx) then
                    local rb = plan and plan.routeBefore and plan.routeBefore[idx]
                    if rb then
                        for i = 1, #rb do
                            if not PushStep(rb[i]) then return out, meta end
                        end
                    end
                end

                local isSuppressed = (plan and plan.suppressedRouteIdx and plan.suppressedRouteIdx[idx] == true)
                local alreadyEmitted = false
                if not isSuppressed then
                    local stAct = st.action or ""
                    local sqids = CollectStepQuestIds(st)
                    for _, sqid in ipairs(sqids) do
                        if emittedActionQids[stAct .. ":" .. tostring(sqid)]
                            or (simCompleted[sqid] and (stAct == "ACCEPT" or stAct == "COMPLETE" or stAct == "TURN_IN")) then
                            alreadyEmitted = true
                            break
                        end
                    end
                end
                local skipSelf = isSuppressed
                    or alreadyEmitted
                    or State.IsStepSkipped(st, idx, pkey)
                    or (autoAdvance and State.IsStepSatisfied(st, steps, idx, player, pkey))
                    or ((manualCursorIndex == nil or idx > manualCursorIndex) and State.IsStepOutleveled(st, player))
                local bCode = (not skipSelf) and State.IsStepBlocked(st, steps, idx, player, pkey, simCompleted, vActive, toggles) or nil

                if isSuppressed or alreadyEmitted then
                    SimulateStep(st)
                elseif (not skipSelf) and (not bCode) then
                    local decInfo = plan and plan.decorateRouteIdx and plan.decorateRouteIdx[idx]
                    local emitted = decInfo and CloneDecoratedReadyStep(st, decInfo) or st
                    if not PushStep(emitted) then return out, meta end
                end

                local ra = plan and plan.routeAfter and plan.routeAfter[idx]
                if ra then
                    for i = 1, #ra do
                        if not PushStep(ra[i]) then return out, meta end
                    end
                end
            end
        end
    end
    return out, meta
end

function State.GetDeviationStatus()
    local player = playerCache or State.GetPlayerInfo()
    local step, meta = State.GetCurrentStep()
    if not step then
        return { deviated = false, reason = "Route complete" }
    end
    if meta.source == "dynamic" then
        return { deviated = false, dynamicActive = true, reason = "Live DAG Re-Scorer active" }
    end
    if meta.source == "in_dungeon" then
        local dName = (meta.locationPlan and meta.locationPlan.dungeonName) or player.zoneName or "Instance"
        return {
            deviated = false,
            gpsAdapted = true,
            gpsMode = "in_dungeon",
            inDungeon = true,
            reason = "In Dungeon: " .. tostring(dName),
        }
    end
    if meta.source == "friend_pivot" or meta.source == "gps_reorder" then
        return {
            deviated = false,
            gpsAdapted = true,
            gpsMode = meta.source,
            reason = (meta.source == "friend_pivot")
                and ("Friend/local chain adaptation active in " .. (player.zoneName or "current zone"))
                or ("GPS walking reorder active in " .. (player.zoneName or "current zone")),
        }
    end
    if step.zoneId and player.zoneId and step.zoneId ~= player.zoneId and step.action ~= "TRAVEL" then
        local z = ns.GetData().zones[step.zoneId]
        local targetName = (z and z.name) or step.zoneName or ("Zone " .. tostring(step.zoneId))
        return {
            deviated = true,
            reason = string.format("You are in %s; route step is in %s", player.zoneName or "another zone", targetName),
        }
    end
    return { deviated = false }
end

--------------------------------------------------------------------------------
-- Manual Navigation & Profile Switching
--------------------------------------------------------------------------------

function State.SkipCurrentStep()
    local step, meta = State.GetCurrentStep()
    if not step then return end

    if meta.source == "ready_turnin" or (step.isReadyTurnIn and step.readyStepKey and meta.source ~= "route" and meta.source ~= "in_dungeon") then
        local rKey = step.readyStepKey or (step.questId and ("ready_turnin:" .. tostring(step.questId)))
        if rKey then
            ns.charDB.skippedSteps[rKey] = true
            table.insert(readyHistory, rKey)
        end
    elseif meta.source == "in_dungeon" or meta.source == "gps_reorder" or meta.source == "friend_pivot" then
        if step.origRouteIdx then
            local steps = State.GetActiveProfileSteps()
            local origStep = steps[step.origRouteIdx]
            if origStep then
                local oKey = State.GetStepKey(origStep, step.origRouteIdx, meta.profileKey)
                ns.charDB.skippedSteps[oKey] = true
                if ns.charDB.deferredSteps and ns.charDB.deferredSteps[meta.profileKey] then
                    ns.charDB.deferredSteps[meta.profileKey][step.origRouteIdx] = nil
                end
                table.insert(gpsHistory, {
                    type = "gps_route",
                    key = oKey,
                    origIdx = step.origRouteIdx,
                    gpsStepKey = step.gpsStepKey,
                })
                if step.origRouteIdx == meta.stepIndex then
                    ns.charDB.stepIndex = step.origRouteIdx + 1
                end
            end
        else
            local gKey = step.gpsStepKey or ("gps:" .. tostring(step.action or "STEP") .. ":" .. tostring(step.questId or step.title or ""))
            ns.charDB.skippedSteps[gKey] = true
            table.insert(gpsHistory, { type = "gps", key = gKey })
        end
        lastGpsTargetKey = nil
    elseif meta.source == "dynamic" then
        -- Skipping a dynamic step marks its quest/step temporarily skipped for this character
        local key = State.GetStepKey(step, 1, "dynamic")
        ns.charDB.skippedSteps[key] = true
    elseif meta.source == "class_quest" and step.questId then
        ns.charDB.skippedSteps["class_quest:" .. tostring(step.questId)] = true
    elseif meta.resolvedPrereq and step.isResolvedPrereq then
        if step.questId then
            ns.charDB.skippedSteps["prereq:" .. tostring(step.questId)] = true
        end
        local steps = State.GetActiveProfileSteps()
        local parentStep = steps[meta.stepIndex]
        if parentStep then
            local pKey = State.GetStepKey(parentStep, meta.stepIndex, meta.profileKey)
            ns.charDB.skippedSteps[pKey] = true
        end
        ns.charDB.history = ns.charDB.history or {}
        table.insert(ns.charDB.history, meta.stepIndex)
        ns.charDB.stepIndex = meta.stepIndex + 1
    else
        local key = State.GetStepKey(step, meta.stepIndex, meta.profileKey)
        ns.charDB.history = ns.charDB.history or {}
        table.insert(ns.charDB.history, meta.stepIndex)
        ns.charDB.skippedSteps[key] = true
        if ns.charDB.deferredSteps and ns.charDB.deferredSteps[meta.profileKey] then
            ns.charDB.deferredSteps[meta.profileKey][meta.stepIndex] = nil
        end
        ns.charDB.stepIndex = meta.stepIndex + 1
    end
    lastGpsTargetKey = nil
    State.Refresh("MANUAL_SKIP")
end

function State.MarkCurrentStepComplete()
    local step, meta = State.GetCurrentStep()
    if not step then return end
    if meta.source == "ready_turnin" then
        local rKey = step.readyStepKey or (step.questId and ("ready_turnin:" .. tostring(step.questId)))
        if rKey then
            ns.charDB.completedCustomSteps[rKey] = true
            table.insert(readyHistory, rKey)
        end
    elseif meta.source == "in_dungeon" or meta.source == "gps_reorder" or meta.source == "friend_pivot" then
        if step.origRouteIdx then
            local steps = State.GetActiveProfileSteps()
            local origStep = steps[step.origRouteIdx]
            if origStep then
                local oKey = State.GetStepKey(origStep, step.origRouteIdx, meta.profileKey)
                ns.charDB.completedCustomSteps[oKey] = true
                if ns.charDB.deferredSteps and ns.charDB.deferredSteps[meta.profileKey] then
                    ns.charDB.deferredSteps[meta.profileKey][step.origRouteIdx] = nil
                end
                table.insert(gpsHistory, {
                    type = "gps_route",
                    key = oKey,
                    origIdx = step.origRouteIdx,
                    gpsStepKey = step.gpsStepKey,
                })
                if step.origRouteIdx == meta.stepIndex then
                    ns.charDB.stepIndex = step.origRouteIdx + 1
                end
            end
        else
            local gKey = step.gpsStepKey or ("gps:" .. tostring(step.action or "STEP") .. ":" .. tostring(step.questId or step.title or ""))
            ns.charDB.completedCustomSteps[gKey] = true
            table.insert(gpsHistory, { type = "gps", key = gKey })
        end
        lastGpsTargetKey = nil
    elseif meta.source == "route" and not meta.resolvedPrereq then
        local key = State.GetStepKey(step, meta.stepIndex, meta.profileKey)
        ns.charDB.history = ns.charDB.history or {}
        table.insert(ns.charDB.history, meta.stepIndex)
        ns.charDB.completedCustomSteps[key] = true
        if ns.charDB.deferredSteps and ns.charDB.deferredSteps[meta.profileKey] then
            ns.charDB.deferredSteps[meta.profileKey][meta.stepIndex] = nil
        end
        ns.charDB.stepIndex = meta.stepIndex + 1
    elseif meta.source == "class_quest" and step.questId then
        sessionCompleted[step.questId] = true
        completedBulk[step.questId] = true
    elseif meta.resolvedPrereq and step.questId then
        sessionCompleted[step.questId] = true
        completedBulk[step.questId] = true
    else
        local key = State.GetStepKey(step, 1, "dynamic")
        ns.charDB.completedCustomSteps[key] = true
    end
    manualCursorIndex = nil
    lastGpsTargetKey = nil
    State.Refresh("MANUAL_COMPLETE")
end

function State.StepBack()
    if not ns.charDB then return end
    local prevGps = gpsHistory and table.remove(gpsHistory) or nil
    if prevGps then
        if prevGps.key then
            if ns.charDB.skippedSteps then
                ns.charDB.skippedSteps[prevGps.key] = nil
            end
            if ns.charDB.completedCustomSteps then
                ns.charDB.completedCustomSteps[prevGps.key] = nil
            end
        end
        if prevGps.type == "gps_route" and prevGps.origIdx then
            if (ns.charDB.stepIndex or 1) > prevGps.origIdx then
                ns.charDB.stepIndex = prevGps.origIdx
                manualCursorIndex = prevGps.origIdx
            end
            lastGpsTargetKey = prevGps.gpsStepKey
        else
            lastGpsTargetKey = prevGps.key
        end
        State.Refresh("MANUAL_BACK")
        return
    end
    local prevReady = readyHistory and table.remove(readyHistory) or nil
    if prevReady then
        if ns.charDB.skippedSteps then
            ns.charDB.skippedSteps[prevReady] = nil
        end
        if ns.charDB.completedCustomSteps then
            ns.charDB.completedCustomSteps[prevReady] = nil
        end
        State.Refresh("MANUAL_BACK")
        return
    end
    local steps, _, pkey = State.GetActiveProfileSteps()
    local player = playerCache or State.GetPlayerInfo()
    local toggles = ns.charDB.toggles or {}
    local prevIdx = ns.charDB.history and table.remove(ns.charDB.history) or nil
    if prevIdx and prevIdx >= 1 then
        if steps[prevIdx] then
            local key = State.GetStepKey(steps[prevIdx], prevIdx, pkey)
            ns.charDB.skippedSteps[key] = nil
            ns.charDB.completedCustomSteps[key] = nil
        end
        ns.charDB.stepIndex = prevIdx
        manualCursorIndex = prevIdx
    elseif (ns.charDB.stepIndex or 1) > 1 then
        local target = ns.charDB.stepIndex - 1
        while target >= 1 do
            local key = State.GetStepKey(steps[target], target, pkey)
            ns.charDB.skippedSteps[key] = nil
            ns.charDB.completedCustomSteps[key] = nil
            local satisfied = State.IsStepSatisfied(steps[target], steps, target, player, pkey)
            local blocked = State.IsStepBlocked(steps[target], steps, target, player, pkey, nil, nil, toggles)
            if (not satisfied) and (not blocked) then
                break
            end
            target = target - 1
        end
        ns.charDB.stepIndex = math.max(1, target)
        manualCursorIndex = ns.charDB.stepIndex
    end
    State.Refresh("MANUAL_BACK")
end

function State.JumpToStep(idx)
    if not ns.charDB then return end
    local steps, _, pkey = State.GetActiveProfileSteps()
    if idx < 1 or idx > #steps then return end
    local player = playerCache or State.GetPlayerInfo()
    local toggles = ns.charDB.toggles or {}
    local bCode, bReason = State.IsStepBlocked(steps[idx], steps, idx, player, pkey, nil, nil, toggles)
    if bCode and bCode ~= "missing_prereq" then
        ns.Printf("Step #%d (%s) blocked: %s", idx, steps[idx].title or "Step", bReason or bCode)
    end
    ns.charDB.history = ns.charDB.history or {}
    table.insert(ns.charDB.history, ns.charDB.stepIndex or 1)
    local key = State.GetStepKey(steps[idx], idx, pkey)
    ns.charDB.skippedSteps[key] = nil
    ns.charDB.completedCustomSteps[key] = nil
    ns.charDB.stepIndex = idx
    manualCursorIndex = idx
    lastGpsTargetKey = nil
    State.Refresh("MANUAL_JUMP")
end

function State.ResetProgress()
    if not ns.charDB then return end
    ns.charDB.stepIndex = 1
    manualCursorIndex = nil
    ns.charDB.skippedSteps = {}
    ns.charDB.deferredSteps = {}
    ns.charDB.completedCustomSteps = {}
    ns.charDB.history = {}
    readyHistory = {}
    if readyPlanCache then
        readyPlanCache.gen = -1
        readyPlanCache.plan = nil
    end
    perfScratch.readySigGen = perfScratch.readySigGen + 1
    perfScratch.gpsLiveStateGen = perfScratch.gpsLiveStateGen + 1
    perfScratch.readyRouteScanCache = nil
    perfScratch.readyScanCtx = nil
    perfScratch.readyStaticCache = nil
    gpsHistory = {}
    perfScratch.gpsBaseCtx = nil
    perfScratch.gpsZoneWindowCache = nil
    perfScratch.gpsProactiveStepCache = {}
    perfScratch.gpsProactiveStepCacheGen = -1
    perfScratch.reorderedStepCache = {}
    stepKeyCache = {}
    acceptIdxByStepsCache = {}
    perfScratch.reorderedCount = 0
    perfScratch.stepKeyCount = 0
    perfScratch.acceptIdxCount = 0
    if locationPlanCache then
        locationPlanCache.gen = -1
        locationPlanCache.plan = nil
    end
    perfScratch.dungeonPlanCache = nil
    if currentStepCache then
        currentStepCache.gen = -1
        currentStepCache.step = nil
        currentStepCache.meta = nil
    end
    decCache.byMaxOff = {}
    decCache.byStepBadge = {}
    decCache.epoch = decCache.epoch + 1
    decCache.gen = -1
    decCache.playerRef = nil
    recentAcceptedQuests = {}
    recentChainFollowups = {}
    recentAcceptedSeq = 0
    perfScratch.locSigSeq = -1
    perfScratch.offDbSigRecSeq = -1
    lastGpsTargetKey = nil
    lastAliveLocation = nil
    deadBootLocation = nil
    lastTrackedZoneId = nil
    lastTrackedX = nil
    lastTrackedY = nil
    lastRawInit = false
    lastRawUiMapId = nil
    lastRawGx = nil
    lastRawGy = nil
    lastGpsRefreshAt = nil
    perfScratch.lastObservedInInstance = nil
    perfScratch.lastObservedInstMapId = nil
    perfScratch.lastObservedInstName = nil
    activeLogInitialized = false
    activeQuestCount = 0
    sessionAcceptedPending = false
    trackedStepQidCount = 0
    lastObservedPlayerLevel = nil
    sessionCompleted = {}
    sessionAccepted = {}
    ClearBlockedStepsLog()
    State.Refresh("MANUAL_RESET")
end

function State.SetProfile(profileKey)
    if not ns.charDB then return end
    ns.charDB.activeProfile = profileKey
    if profileKey ~= "custom" then
        ns.charDB.customWeights = false
        ns.charDB.weights = ns.GetPresetWeights(profileKey)
        if ns.charDB.toggles then
            ns.charDB.toggles.dynamicRescore = false
        end
    else
        if ns.charDB.toggles then
            ns.charDB.toggles.dynamicRescore = true
        end
    end
    ns.charDB.stepIndex = 1
    manualCursorIndex = nil
    ns.charDB.history = {}
    ns.charDB.deferredSteps = {}
    gpsHistory = {}
    if readyPlanCache then
        readyPlanCache.gen = -1
        readyPlanCache.plan = nil
    end
    perfScratch.readySigGen = perfScratch.readySigGen + 1
    perfScratch.gpsLiveStateGen = perfScratch.gpsLiveStateGen + 1
    perfScratch.readyRouteScanCache = nil
    perfScratch.readyScanCtx = nil
    perfScratch.readyStaticCache = nil
    perfScratch.gpsBaseCtx = nil
    perfScratch.gpsZoneWindowCache = nil
    perfScratch.reorderedStepCache = {}
    stepKeyCache = {}
    acceptIdxByStepsCache = {}
    perfScratch.reorderedCount = 0
    perfScratch.stepKeyCount = 0
    perfScratch.acceptIdxCount = 0
    if locationPlanCache then
        locationPlanCache.gen = -1
        locationPlanCache.plan = nil
    end
    perfScratch.dungeonPlanCache = nil
    if currentStepCache then
        currentStepCache.gen = -1
        currentStepCache.step = nil
        currentStepCache.meta = nil
    end
    decCache.byMaxOff = {}
    decCache.byStepBadge = {}
    decCache.epoch = decCache.epoch + 1
    decCache.gen = -1
    decCache.playerRef = nil
    lastGpsTargetKey = nil
    ClearBlockedStepsLog()
    State.Refresh("PROFILE_CHANGED")
end

function State.SetCustomWeights(weightsTable, enableDynamic)
    if not ns.charDB then return end
    ns.charDB.weights = {
        xp = math.max(0, math.min(100, tonumber(weightsTable.xp) or 45)),
        travel = math.max(0, math.min(100, tonumber(weightsTable.travel) or 25)),
        dungeon = math.max(0, math.min(100, tonumber(weightsTable.dungeon) or 20)),
        reward = math.max(0, math.min(100, tonumber(weightsTable.reward) or 10)),
        skinning = math.max(0, math.min(100, tonumber(weightsTable.skinning) or 0)),
    }
    ns.charDB.customWeights = true
    if enableDynamic ~= false and ns.charDB.toggles then
        ns.charDB.toggles.dynamicRescore = true
    end
    State.Refresh("WEIGHTS_CHANGED")
end

--------------------------------------------------------------------------------
-- Refresh Loop & Initialization
--------------------------------------------------------------------------------

local function SyncLiveCharIdentity(pInfo)
    if not (ns.charDB and pInfo) then return end
    local toggles = ns.charDB.toggles
    if toggles and toggles.autoDetectChar == false then return end
    local sig = string.format("%s:%s:%s",
        tostring(pInfo.liveFaction or pInfo.faction or ""),
        tostring(pInfo.liveRaceFile or pInfo.raceFile or ""),
        tostring(pInfo.liveClassFile or pInfo.classFile or ""))
    if ns.charDB.lastAutoCharSig and ns.charDB.lastAutoCharSig ~= sig then
        ns.charDB.stepIndex = 1
        manualCursorIndex = nil
        ns.charDB.history = {}
        ns.charDB.deferredSteps = {}
        ClearBlockedStepsLog()
    end
    ns.charDB.lastAutoCharSig = sig
end

function State.Initialize()
    State.BuildIndexes()
    State.RefreshCompletedBulk()
    local pInfo = State.GetPlayerInfo()
    perfScratch.lastObservedInGroup = (pInfo and pInfo.inGroup == true)
    if (not pInfo.onTaxi) and (not pInfo.isDead) and ns.GetPlayerInstanceState then
        local curInInst, curInstName, curInstMapId = ns.GetPlayerInstanceState()
        perfScratch.lastObservedInInstance = (curInInst == true)
        perfScratch.lastObservedInstMapId = curInstMapId
        perfScratch.lastObservedInstName = curInstName
    end
    SyncLiveCharIdentity(pInfo)
    State.ScanQuestLog()
end

local function StepSignature(step, meta)
    if not step then
        return "NONE"
    end
    return string.format("%s|%s|%s|%s|%s|%s|%s",
        tostring(meta and meta.source or ""),
        tostring(meta and meta.profileKey or ""),
        tostring(step.index or ""),
        tostring(step.action or ""),
        tostring(step.questId or step.dungeonId or step.zoneId or step.title or ""),
        tostring(step.readyStepKey or ""),
        tostring(step.gpsStepKey or ""))
end

function State.OnPlayerDead()
    if playerCache then
        playerCache.isDead = true
    end
end

function State.OnPlayerResurrected()
    if ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost() then
        return false
    end
    if playerCache then
        playerCache.isDead = false
    end
    local prevAt = lastGpsRefreshAt
    lastGpsRefreshAt = nil
    local triggered = false
    if State.CheckLivePosition then
        triggered = State.CheckLivePosition(false)
    end
    if (not triggered) and prevAt then
        lastGpsRefreshAt = prevAt
    end
    return triggered
end

function State.Refresh(reason, arg1, arg2)
    refreshCalls = refreshCalls + 1
    State.BuildIndexes()
    local onTaxiNow = (UnitOnTaxi and UnitOnTaxi("player")) and true or false
    local deadNow = (ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost()) and true or false
    local isLocationOnly = LOCATION_ONLY_REASONS[reason] == true
    local needsBulkSync = (BULK_COMPLETED_REASONS[reason] == true) or (not hasBulkSync)

    if isLocationOnly and deadNow then
        deadLocationSkipped = deadLocationSkipped + 1
        refreshSkipped = refreshSkipped + 1
        return
    end

    if reason == "QUEST_LOG_UPDATE" then
        local changed, questBulkNeeded = InspectQuestLogDelta()
        if not changed then
            refreshSkipped = refreshSkipped + 1
            return
        end
        if questBulkNeeded then
            needsBulkSync = true
        end
    elseif reason == "SKILL_LINES_CHANGED" or reason == "CHAT_MSG_SKILL" then
        if professionCache and State.DetectProfessions then
            local prevProf = professionCache
            local nextProf = State.DetectProfessions(true)
            if ProfTablesEqual(prevProf, nextProf) then
                refreshSkipped = refreshSkipped + 1
                return
            end
        end
    elseif reason == "ZONE_CHANGED" or reason == "ZONE_CHANGED_INDOORS" then
        if onTaxiNow or deadNow then
            refreshSkipped = refreshSkipped + 1
            return
        end
        local uiMapId, rawGx, rawGy = ReadRawMapAndCoords()
        local zId, px, py = ResolveLiveLocation(uiMapId, rawGx, rawGy)
        local nowSec = GetTime and tonumber(GetTime()) or nil
        local shouldRun = ShouldTriggerGpsMove(zId, px, py, nowSec, false)
        lastRawUiMapId = uiMapId
        lastRawGx = rawGx
        lastRawGy = rawGy
        lastRawInit = true
        if not shouldRun then
            refreshSkipped = refreshSkipped + 1
            return
        end
    elseif reason == "GROUP_ROSTER_UPDATE" or reason == "PARTY_MEMBERS_CHANGED" or reason == "RAID_ROSTER_UPDATE" then
        if not State.HasGroupStatusChanged() then
            refreshSkipped = refreshSkipped + 1
            return
        end
        lastGpsTargetKey = nil
    elseif reason == "TOGGLE_skipEliteSolo" or reason == "TOGGLE_skipDungeons" then
        lastGpsTargetKey = nil
    end

    refreshExecuted = refreshExecuted + 1
    local refreshProfs = (PROFESSION_REFRESH_REASONS[reason] == true)
        and (reason ~= "SKILL_LINES_CHANGED" and reason ~= "CHAT_MSG_SKILL")
    local keepDbCaches = false
    if (not isLocationOnly) and (not needsBulkSync)
        and (reason == "QUEST_TURNED_IN" or reason == "QUEST_ACCEPTED" or reason == "QUEST_REMOVED") then
        local evQid = (reason == "QUEST_ACCEPTED") and (tonumber(arg2) or tonumber(arg1)) or tonumber(arg1)
        if evQid and evQid > 0 then
            local d = ns.GetData()
            if (not (d and d.quests and d.quests[evQid])) and (GetQuestieDB() == nil) then
                keepDbCaches = true
            end
        end
    end
    BumpGeneration(isLocationOnly, refreshProfs, keepDbCaches, reason == "MAX_LEVEL_CHANGED")

    if reason == "QUEST_TURNED_IN" then
        local qid = tonumber(arg1)
        if qid then
            sessionCompleted[qid] = true
            completedBulk[qid] = true
            completedMemo[qid] = true
            if keepDbCaches then
                EvictQuestFromBlockMemo(qid)
            end
            local hadRecent = (recentAcceptedQuests[qid] ~= nil or recentChainFollowups[qid] ~= nil)
            sessionAccepted[qid] = nil
            recentAcceptedQuests[qid] = nil
            recentChainFollowups[qid] = nil
            lastGpsTargetKey = nil
            local data = ns.GetData()
            local q = data and data.quests and data.quests[qid]
            if hadRecent or (q and type(q.nextQuestIds) == "table") then
                recentAcceptedSeq = recentAcceptedSeq + 1
            end
            if q and type(q.nextQuestIds) == "table" then
                local curPlayer = playerCache or State.GetPlayerInfo()
                local curZone = curPlayer and curPlayer.zoneId
                for _, nid in ipairs(q.nextQuestIds) do
                    local nq = data.quests[nid]
                    if nq and ((not curZone) or nq.zoneId == curZone) then
                        recentChainFollowups[nid] = {
                            qid = nid,
                            fromQid = qid,
                            zoneId = nq.zoneId or curZone,
                            seq = recentAcceptedSeq,
                        }
                    end
                end
            end
        else
            needsBulkSync = true
        end
    elseif reason == "QUEST_ACCEPTED" then
        local qid = tonumber(arg2) or tonumber(arg1)
        if qid and qid > 0 then
            if keepDbCaches then
                completedMemo[qid] = nil
                EvictQuestFromBlockMemo(qid)
            end
            sessionAccepted[qid] = 0
            sessionAcceptedPending = true
            recentAcceptedSeq = recentAcceptedSeq + 1
            local curPlayer = State.GetPlayerInfo()
            local data = ns.GetData()
            local q = data and data.quests and data.quests[qid]
            recentAcceptedQuests[qid] = {
                qid = qid,
                zoneId = (q and q.zoneId) or (curPlayer and curPlayer.zoneId) or nil,
                x = (curPlayer and curPlayer.x) or (q and q.acceptX) or nil,
                y = (curPlayer and curPlayer.y) or (q and q.acceptY) or nil,
                seq = recentAcceptedSeq,
            }
            recentChainFollowups[qid] = nil
            lastGpsTargetKey = nil
        end
    elseif reason == "QUEST_REMOVED" and tonumber(arg1) then
        local qid = tonumber(arg1)
        if keepDbCaches then
            completedMemo[qid] = nil
            EvictQuestFromBlockMemo(qid)
        end
        if recentAcceptedQuests[qid] ~= nil or recentChainFollowups[qid] ~= nil then
            recentAcceptedSeq = recentAcceptedSeq + 1
        end
        sessionAccepted[qid] = nil
        recentAcceptedQuests[qid] = nil
        recentChainFollowups[qid] = nil
        lastGpsTargetKey = nil
    elseif reason == "PLAYER_LEVEL_UP" and tonumber(arg1) then
        levelOverride = tonumber(arg1)
    elseif (reason == "ZONE_CHANGED_NEW_AREA" or reason == "ZONE_CHANGED") and (not onTaxiNow) and (not deadNow) then
        lastGpsTargetKey = nil
    end

    if (not isLocationOnly) and needsBulkSync then
        State.RefreshCompletedBulk()
    end
    local pInfo = State.GetPlayerInfo()
    perfScratch.lastObservedInGroup = (pInfo and pInfo.inGroup == true)
    if (not pInfo.onTaxi) and (not pInfo.isDead) and ns.GetPlayerInstanceState then
        local curInInst, curInstName, curInstMapId = ns.GetPlayerInstanceState()
        perfScratch.lastObservedInInstance = (curInInst == true)
        perfScratch.lastObservedInstMapId = curInstMapId
        perfScratch.lastObservedInstName = curInstName
    end
    SyncLiveCharIdentity(pInfo)
    if (not isLocationOnly) and not (reason == "MAX_LEVEL_CHANGED" and activeLogInitialized) then
        State.ScanQuestLog()
        if reason == "PLAYER_ENTERING_WORLD" and ns.Nav and ns.Nav.EnsureEligibleIndex then
            ns.Nav.EnsureEligibleIndex(ns.GetData(), pInfo)
        end
    end

    if UnitLevel then
        local rawLv = tonumber(UnitLevel("player"))
        if rawLv then
            lastObservedPlayerLevel = rawLv
        end
    end

    if (not pInfo.onTaxi) and (not pInfo.isDead) and pInfo.zoneId then
        if lastTrackedZoneId and lastTrackedZoneId ~= pInfo.zoneId then
            lastGpsTargetKey = nil
        end
        lastTrackedZoneId = pInfo.zoneId
        if pInfo.x and pInfo.y then
            lastTrackedX = pInfo.x
            lastTrackedY = pInfo.y
        end
        if GetTime then
            lastGpsRefreshAt = tonumber(GetTime()) or lastGpsRefreshAt
        end
        if reason ~= "GPS_MOVED" and reason ~= "GPS_ZONE_CHANGED" and reason ~= "ZONE_CHANGED" and reason ~= "ZONE_CHANGED_INDOORS" then
            lastRawInit = false
        end
    end

    local step, meta = State.GetCurrentStep(pInfo)
    trackedStepQidCount = 0
    if step then
        if step.questId then
            trackedStepQidCount = 1
            trackedStepQids[1] = step.questId
        elseif type(step.questIds) == "table" then
            for i = 1, math.min(4, #step.questIds) do
                trackedStepQids[i] = step.questIds[i]
                trackedStepQidCount = i
            end
        end
    end
    local sig = StepSignature(step, meta)
    local stepChanged = (sig ~= lastStepSignature)
    lastStepSignature = sig

    ns.FireCallback("STATE_UPDATED", reason, step, meta, pInfo)
    if stepChanged then
        ns.FireCallback("STEP_CHANGED", step, meta, reason)
    end
end
