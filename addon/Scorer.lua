--------------------------------------------------------------------------------
-- Scorer.lua
-- Rules:
--   1. Multi-objective dynamic quest re-scorer (XP, Travel, Dungeon Unlocks,
--      Gold/Bags/Class BiS Gear, and Skinning/Leatherworking) prioritizing
--      in-window dungeons, local-zone momentum (including boosted same-hub /
--      on-path same-zone ACCEPT quests within ~25 yds), proactive
--      virtual-completion full-chain expansion and chain progress annotations,
--      recent/friend chain follow-ups, travel-aware ready turn-ins, hub-batched
--      turn-ins, and opposing-faction zone penalties, plus priority class
--      quests, unlock chains, and rewards.
--   2. Consumes embedded GeneratedData (quests, dungeons, zones, xpCurve,
--      classScores, indexes) plus live/overridden character state, user
--      slider weights/toggles, QuestieDB turn-in fallbacks via State, and
--      configurable maxLevelOffset (-3..+5, default +2), bounding QuestieDB
--      IsDoable checks, memoizing static GetDungeonUnlockInfo results in
--      dungeonUnlockMemo (preserved across non-static InvalidateCaches), reusing
--      pooled scorerScratch (metricsPool, rawCandidates, seenCand, readyPoints,
--      readyExtras, peerCountByQid, recentChainSet, simChainSet,
--      activeChainSet, readyCtx), using maxCandidates = 1 + fromLiveSeed
--      IsIndexedEligible fast path + O(N) top-1 selection on lookahead steps,
--      and memoizing read views per generation plus step-1 isLiveState /
--      seedQids / dynamicStepsCache with metatable vCompleted.
--   3. Ready turn-ins in active log are never blocked by eligibility, exclusive
--      lists, elite-solo gating, dungeon-skip gating, or level offset checks;
--      open-world elite quests (incomplete active or available) are skipped
--      when skipEliteNow is true (toggles.skipEliteSolo ~= false and solo),
--      and instance dungeon quests (q.isDungeon or act.isDungeon) are skipped
--      when skipDungeons is enabled (toggles.skipDungeons ~= false) while
--      preserving ready turn-ins and outdoor unlock quests; pure scoring
--      functions must never mutate live SavedVariables or game state.
--   4. Travel estimates and dynamic candidate selection plug into ns.Nav
--      (terrain/road/gate Bidirectional Dijkstra + frontier nodeToEligible
--      search, with bounded skipIndexRebuild=true lookahead steps) when
--      NavData is present, eliminating full-DB scans inside GetDynamicSteps;
--      STATE_UPDATED view-cache maintenance skips location-only GPS/zone ticks.
--------------------------------------------------------------------------------

local _, ns = ...

local Scorer = {}
ns.Scorer = Scorer

local DEFAULT_XP_PER_LEVEL = 28000
local MAX_CANDIDATE_QUESTIE_CHECKS = 25
local READY_NEAR_MINS = 1.05
local READY_HUB_RADIUS = 12.0
local READY_SAME_ZONE_NEAR_BONUS = 34
local READY_SAME_ZONE_DISTANT_BONUS = 12
local READY_CROSS_ZONE_BASE_BONUS = 6
local READY_CROSS_ZONE_DETOUR_PENALTY = 10
local READY_HUB_CLUSTER_STEP_BONUS = 4
local READY_FOLLOWUP_BONUS = 6
local CLASS_FILE_TO_BIT = {
    WARRIOR = 1,
    PALADIN = 2,
    HUNTER  = 4,
    ROGUE   = 8,
    PRIEST  = 16,
    SHAMAN  = 64,
    MAGE    = 128,
    WARLOCK = 256,
    DRUID   = 1024,
}

local LIVE_NAV_OPTS = { maxResults = 75, maxSec = 2400.0, keepFullField = true }
local LOOKAHEAD_NAV_OPTS = { maxResults = 36, maxSec = 1800.0, keepFullField = false, skipIndexRebuild = true }
local FAST_BLOCK_OPTS = { consultQuestie = false, checkIsDoable = false, useBulkCompletion = true, reasonless = true }
local LIVE_BLOCK_OPTS = { consultQuestie = true, checkIsDoable = true, useBulkCompletion = true, reasonless = true }
local UNMOUNTED_NAV_OPTS = { unmountedOnly = true }

local cachedIndexDataRef = nil
local fallbackIndexes = nil
local cachedDungeonDataRef = nil
local dungeonUnlockMemo = {}
local dungeonsViewCache = {}
local rewardsViewCache = {}
local rankLiveCache = {
    valid = false,
    key = nil,
    out = nil,
    seedQids = nil,
}
local dynamicStepsCache = {
    valid = false,
    key = nil,
    steps = nil,
    requestedCount = 0,
}
local scorerScratch = {
    metricsPool = {},
    rawCandidates = {},
    seenCand = {},
    seenList = {},
    readyPointPool = {},
    readyPoints = {},
    readyExtras = {},
    peerCountByQid = {},
    recentChainSet = {},
    simChainSet = {},
    activeChainSet = {},
    readyCtx = {},
}

local wipeOk, tableClearFn = pcall(require, "table.clear")
if not wipeOk then
    tableClearFn = nil
end

local function WipeMap(t)
    if not t then return end
    if _G.wipe then
        _G.wipe(t)
    elseif tableClearFn then
        tableClearFn(t)
    else
        for k in pairs(t) do
            t[k] = nil
        end
    end
    return t
end

local function CompareCandidateMetrics(a, b)
    if math.abs(a.score - b.score) > 0.001 then
        return a.score > b.score
    end
    return (a.quest.id or 0) < (b.quest.id or 0)
end

local function EnsureIndexes(data)
    data = data or ns.GetData()
    local di = data.indexes
    if type(di) == "table"
        and type(di.specialRewardIds) == "table"
        and type(di.lwQuestIds) == "table"
        and type(di.skinningQuestIds) == "table"
        and type(di.classRewardIds) == "table"
        and type(di.classQuests) == "table" then
        return di
    end

    if cachedIndexDataRef == data and fallbackIndexes then
        return fallbackIndexes
    end

    local specialSeen = {}
    local bags = (di and di.bagQuestIds) or {}
    local gold = (di and di.goldQuestIds) or {}
    local rogue = (di and di.rogueQuestIds) or {}
    local classRewards = (di and di.classRewardIds) or {}
    local lw = (di and di.lwQuestIds) or {}
    local skin = (di and di.skinningQuestIds) or {}
    local special = (di and di.specialRewardIds) or {}
    local byZone = (di and di.byZone) or {}
    local byFaction = (di and di.byFaction) or { [1] = {}, [2] = {}, [3] = {} }
    local classQuests = (di and di.classQuests) or {}

    local needRewards = not (di and type(di.specialRewardIds) == "table" and type(di.classRewardIds) == "table")
    local needClassQ = not (di and type(di.classQuests) == "table")
    local needByFaction = not (di and type(di.byFaction) == "table")
    local needByZone = not (di and type(di.byZone) == "table")

    if needRewards or needClassQ or needByFaction or needByZone then
        if needRewards then
            bags, gold, rogue, classRewards, lw, skin, special = {}, {}, {}, {}, {}, {}, {}
        end
        if needClassQ then
            classQuests = {}
            for cf in pairs(CLASS_FILE_TO_BIT) do
                classQuests[cf] = {}
            end
        end
        for qid, q in pairs(data.quests or {}) do
            if needByZone and q.zoneId then
                byZone[q.zoneId] = byZone[q.zoneId] or {}
                byZone[q.zoneId][#byZone[q.zoneId] + 1] = qid
            end
            if needByFaction then
                local side = q.side or q.factionMask or 3
                if side == 1 or side == 3 then
                    byFaction[1][#byFaction[1] + 1] = qid
                end
                if side == 2 or side == 3 then
                    byFaction[2][#byFaction[2] + 1] = qid
                end
                if side == 3 then
                    byFaction[3][#byFaction[3] + 1] = qid
                end
            end
            if needClassQ and q.classMask and q.classMask > 0 then
                for cf, cbit in pairs(CLASS_FILE_TO_BIT) do
                    if ns.HasBitMask(q.classMask, cbit) then
                        classQuests[cf][#classQuests[cf] + 1] = qid
                    end
                end
            end
            if needRewards then
                local reqId = type(q.requiredSkill) == "table" and tonumber(q.requiredSkill.skillId or q.requiredSkill[1]) or nil
                local isBag = (tonumber(q.bagSlots) or 0) > 0
                local isGold = (tonumber(q.goldCopper) or 0) >= 4000
                local rScore = tonumber(q.rogueScore) or 0
                local isRogue = rScore >= 30
                local maxClassScore = rScore
                if type(q.classScores) == "table" then
                    for _, sv in pairs(q.classScores) do
                        local n = tonumber(sv) or 0
                        if n > maxClassScore then maxClassScore = n end
                    end
                end
                local isClassRwd = maxClassScore >= 30
                local isLW = (q.isLeatherworking == true) or (type(q.lwRewards) == "table" and #q.lwRewards > 0) or (reqId == 165)
                local isSkin = (q.isSkinning == true) or (tonumber(q.skinningScore or (q.features and q.features.skinningValue)) or 0) >= 30 or (reqId == 393)

                if isBag then bags[#bags + 1] = qid end
                if isGold then gold[#gold + 1] = qid end
                if isRogue then rogue[#rogue + 1] = qid end
                if isClassRwd then classRewards[#classRewards + 1] = qid end
                if isLW then lw[#lw + 1] = qid end
                if isSkin then skin[#skin + 1] = qid end

                if (isBag or isGold or isClassRwd or isLW or isSkin) and not specialSeen[qid] then
                    specialSeen[qid] = true
                    special[#special + 1] = qid
                end
            end
        end
    end

    fallbackIndexes = {
        bagQuestIds = bags,
        goldQuestIds = gold,
        rogueQuestIds = rogue,
        classRewardIds = classRewards,
        lwQuestIds = lw,
        skinningQuestIds = skin,
        specialRewardIds = special,
        classQuests = classQuests,
        byZone = byZone,
        byFaction = byFaction,
    }
    cachedIndexDataRef = data
    return fallbackIndexes
end

function Scorer.InvalidateCaches(clearStatic)
    if clearStatic == true then
        cachedIndexDataRef = nil
        fallbackIndexes = nil
        cachedDungeonDataRef = nil
        dungeonUnlockMemo = {}
    end
    dungeonsViewCache = {}
    rewardsViewCache = {}
    rankLiveCache.valid = false
    rankLiveCache.key = nil
    rankLiveCache.out = nil
    rankLiveCache.seedQids = nil
    dynamicStepsCache.valid = false
    dynamicStepsCache.key = nil
    dynamicStepsCache.steps = nil
    dynamicStepsCache.requestedCount = 0
end

--------------------------------------------------------------------------------
-- XP, Travel & Reward Primitives
--------------------------------------------------------------------------------

function Scorer.XpToLevel(level, data)
    data = data or ns.GetData()
    local curve = data.xpCurve
    if curve and curve[level] and curve[level] > 0 then
        return curve[level]
    end
    -- Fallback Classic/Forever approximation if xpCurve is incomplete
    return math.floor(400 * level * (1 + level * 0.08))
end

function Scorer.XpDecayMultiplier(playerLevel, questLevel)
    if not playerLevel or not questLevel or questLevel <= 0 then
        return 1.0
    end
    local diff = playerLevel - questLevel
    if diff <= 5 then
        return 1.0
    elseif diff == 6 then
        return 0.8
    elseif diff == 7 then
        return 0.6
    elseif diff == 8 then
        return 0.4
    elseif diff == 9 then
        return 0.2
    else
        return 0.1
    end
end

local function FirstPoint(listOrPoint)
    if type(listOrPoint) ~= "table" then return nil end
    if listOrPoint.zoneId or listOrPoint.x then
        return listOrPoint
    end
    if listOrPoint[1] and type(listOrPoint[1]) == "table" then
        return listOrPoint[1]
    end
    return nil
end

function Scorer.GetQuestPoint(q, action)
    if not q then return nil end
    if action == "TURN_IN" then
        return FirstPoint(q.turnin) or FirstPoint(q.starter) or FirstPoint(q.objectives)
    elseif action == "COMPLETE" then
        return FirstPoint(q.objectives) or FirstPoint(q.turnin) or FirstPoint(q.starter)
    else
        return FirstPoint(q.starter) or FirstPoint(q.objectives) or FirstPoint(q.turnin)
    end
end

function Scorer.EstimateTravelMinutes(fromZoneId, fromX, fromY, toZoneId, toX, toY, data, player, opts)
    data = data or ns.GetData()
    fromX = tonumber(fromX) or 50
    fromY = tonumber(fromY) or 50
    toX = tonumber(toX) or 50
    toY = tonumber(toY) or 50

    if fromZoneId and toZoneId and ns.Nav and ns.Nav.GetTravelSeconds and ns.Nav.IsAvailable and ns.Nav.IsAvailable() then
        local sec, ok = ns.Nav.GetTravelSeconds(fromZoneId, fromX, fromY, toZoneId, toX, toY, player, opts)
        if ok and sec then
            return math.max(0.1, math.floor((sec / 60.0) * 100 + 0.5) / 100)
        end
    end

    if not toZoneId or not fromZoneId or fromZoneId == toZoneId then
        local dx = toX - fromX
        local dy = toY - fromY
        local dist = math.sqrt(dx * dx + dy * dy)
        return math.max(0.3, dist / 22.0)
    end

    local zFrom = data.zones and data.zones[fromZoneId]
    local zTo = data.zones and data.zones[toZoneId]
    local rFrom = zFrom and zFrom.region
    local rTo = zTo and zTo.region
    if rFrom ~= nil and rTo ~= nil and rFrom ~= rTo then
        return 8.5
    end
    return 4.5
end

function Scorer.GetDungeonUnlockInfo(questId, playerLevel, data)
    local liveData = ns.GetData()
    data = data or liveData
    local useMemo = (data == liveData and type(questId) == "number" and type(playerLevel) == "number")
    local lvMemo = nil
    if useMemo then
        if cachedDungeonDataRef ~= data then
            cachedDungeonDataRef = data
            dungeonUnlockMemo = {}
        end
        lvMemo = dungeonUnlockMemo[playerLevel]
        if lvMemo then
            local cached = lvMemo[questId]
            if cached ~= nil then
                return cached or nil
            end
        else
            lvMemo = {}
            dungeonUnlockMemo[playerLevel] = lvMemo
        end
    end
    local dungeonIds = ns.State.GetQuestDungeons(questId)
    if not dungeonIds or #dungeonIds == 0 then
        if useMemo then
            lvMemo[questId] = false
        end
        return nil
    end

    local bestScore = 0
    local bestInfo = nil

    for _, dId in ipairs(dungeonIds) do
        local d = data.dungeons and data.dungeons[dId]
        if d then
            local minL = d.minLevel or 15
            local maxL = d.maxLevel or 60
            local recL = d.recommendedLevel or math.floor((minL + maxL) / 2)
            local inWindow = (playerLevel >= minL - 5) and (playerLevel <= maxL + 3)
            local dist = math.abs(playerLevel - recL)
            local prox = math.max(0, 1.0 - dist / 12.0)
            local chainBonus = 0
            local chainName = nil
            local stepIdx, totalSteps = nil, nil

            for _, chain in ipairs(d.unlockChains or {}) do
                for i, cid in ipairs(chain.questIds or {}) do
                    if cid == questId then
                        chainBonus = inWindow and 25 or 4
                        chainName = chain.chainName
                        stepIdx = i
                        totalSteps = #(chain.questIds or {})
                        break
                    end
                end
                if chainName then break end
            end

            local raw = (inWindow and 55 or 18) * (0.4 + 0.6 * prox) + chainBonus
            if raw > bestScore then
                bestScore = raw
                bestInfo = {
                    score = math.min(100, raw),
                    dungeonId = dId,
                    dungeonName = d.name or tostring(dId),
                    chainName = chainName,
                    stepIndex = stepIdx,
                    totalSteps = totalSteps,
                    inWindow = inWindow,
                }
            end
        end
    end

    if useMemo then
        lvMemo[questId] = bestInfo or false
    end
    return bestInfo
end

--------------------------------------------------------------------------------
-- Candidate Scoring & Live Re-Scorer
--------------------------------------------------------------------------------

local function HasEligibleFollowup(q, vState, data)
    if not q or type(q.nextQuestIds) ~= "table" or #q.nextQuestIds == 0 then
        return false
    end
    local pLevel = vState.level or 24
    local p = vState.player or {}
    for _, nid in ipairs(q.nextQuestIds) do
        local nq = data.quests and data.quests[nid]
        if nq and (not vState.completed or not vState.completed[nid]) and (not vState.active or not vState.active[nid]) then
            if (nq.minLevel or 1) <= pLevel and (not ns.State or not ns.State.IsEligibleForPlayer or ns.State.IsEligibleForPlayer(nid, p)) then
                return true
            end
        end
    end
    return false
end

local function ComputeCandidateMetrics(q, action, vState, weights, toggles, data, readyCtx, outSlot)
    local level = vState.level or 24
    local effAction = action
    if q.isDungeon and action == "ACCEPT" then
        effAction = "DUNGEON_PREP"
    elseif q.isDungeon and action == "COMPLETE" then
        effAction = "DUNGEON_RUN"
    end

    local pt = Scorer.GetQuestPoint(q, action)
    local targetZoneId = (pt and pt.zoneId) or q.zoneId or vState.zoneId
    local targetX = (pt and pt.x) or 50
    local targetY = (pt and pt.y) or 50

    if not Scorer._navStepOpts then Scorer._navStepOpts = {} end
    local navOpts = Scorer._navStepOpts
    navOpts.questId = q.id
    navOpts.role = (action == "TURN_IN" and "t") or (action == "COMPLETE" and "o") or "s"
    local travelMins = Scorer.EstimateTravelMinutes(
        vState.zoneId, vState.x, vState.y,
        targetZoneId, targetX, targetY, data, vState.player, navOpts
    )
    local estMins = travelMins + (action == "COMPLETE" and (q.isGroup and 10 or 5) or 0.5)

    -- XP component (0..100)
    local xpToLvl = Scorer.XpToLevel(level, data)
    if xpToLvl <= 0 then xpToLvl = DEFAULT_XP_PER_LEVEL end
    local mult = Scorer.XpDecayMultiplier(level, q.questLevel or level)
    local effXp = math.floor((tonumber(q.xp) or 0) * mult)
    local xpPct = (effXp / xpToLvl) * 100.0
    local xpNorm = math.min(100, xpPct * 4.2)
    if (q.questLevel or level) > level + 3 then
        xpNorm = xpNorm * 0.65
    end

    -- Travel penalty (0..100)
    local travelPenalty = math.min(100, travelMins * 11.5)

    -- Dungeon unlock component (0..100)
    local dungeonInfo = Scorer.GetDungeonUnlockInfo(q.id, level, data)
    local dungeonNorm = dungeonInfo and dungeonInfo.score or 0
    if toggles.preferDungeonUnlocks == false then
        dungeonNorm = dungeonNorm * 0.25
    end

    -- Reward component (Gold + Bags + Class BiS, 0..100)
    local p = vState.player or {}
    local classFile = p.classFile or "ROGUE"
    local className = p.className or "Rogue"
    local goldCopper = tonumber(q.goldCopper) or 0
    local bagSlots = tonumber(q.bagSlots) or 0
    local classScore = ns.GetQuestClassScore(q, classFile)
    local rogueScore = (classFile == "ROGUE") and classScore or (tonumber(q.rogueScore) or 0)

    local goldScore = math.min(100, (goldCopper / 10000.0) * 35.0)
    local bagScore = bagSlots > 0 and math.min(100, 45 + bagSlots * 3.5) or 0
    if toggles.prioritizeBags == false then
        bagScore = bagScore * 0.2
    end
    local gearScore = math.min(100, classScore)
    if toggles.prioritizeClassGear == false or toggles.prioritizeRogueGear == false then
        gearScore = gearScore * 0.2
    end
    local rewardNorm = math.min(100, math.max(bagScore, gearScore * 0.85) + goldScore * 0.45)

    -- Skinning & Leatherworking component (0..100)
    local skinScore = tonumber(q.skinningScore or (q.features and q.features.skinningValue)) or 0
    local reqId = type(q.requiredSkill) == "table" and tonumber(q.requiredSkill.skillId or q.requiredSkill[1]) or nil
    local isLW = (q.isLeatherworking == true) or (type(q.lwRewards) == "table" and #q.lwRewards > 0) or (reqId == 165)
    local isSkin = (q.isSkinning == true) or (reqId == 393) or (skinScore >= 30)
    local skinningNorm = math.min(100, skinScore)
    if isLW then
        skinningNorm = math.max(skinningNorm, 75)
    end
    if toggles.prioritizeSkinningLW == false then
        skinningNorm = skinningNorm * 0.2
    end

    -- Momentum bonus keeps player turning in finished quests & clearing active log quests
    -- without cross-map or cross-zone detours while local hub work remains
    local momentum = 0
    local readyPeerCount = (readyCtx and readyCtx.peerCountByQid and readyCtx.peerCountByQid[q.id]) or 0
    local hasFollowup = false
    if action == "TURN_IN" then
        if targetZoneId == vState.zoneId then
            if travelMins <= READY_NEAR_MINS then
                momentum = READY_SAME_ZONE_NEAR_BONUS
            else
                momentum = READY_SAME_ZONE_DISTANT_BONUS
            end
        else
            momentum = READY_CROSS_ZONE_BASE_BONUS
        end
        if readyPeerCount > 0 then
            momentum = momentum + math.min(12, readyPeerCount * READY_HUB_CLUSTER_STEP_BONUS)
        end
        if HasEligibleFollowup(q, vState, data) then
            momentum = momentum + READY_FOLLOWUP_BONUS
            hasFollowup = true
        end
    elseif action == "COMPLETE" then
        momentum = 18
    elseif targetZoneId == vState.zoneId then
        if action == "ACCEPT" and (not q.turnin or not q.turnin.zoneId or q.turnin.zoneId == vState.zoneId) and travelMins <= 0.08 then
            momentum = 16
        else
            momentum = 14
        end
    end
    local isFriendChain = false
    if toggles.locationReorder ~= false and targetZoneId == vState.zoneId and readyCtx then
        if readyCtx.recentChainSet and readyCtx.recentChainSet[q.id] then
            momentum = momentum + 24
            isFriendChain = true
        elseif readyCtx.simChainSet and readyCtx.simChainSet[q.id] then
            local closeHub = (travelMins <= 0.15)
            momentum = momentum + ((action == "ACCEPT") and (closeHub and 40 or 32) or 28)
        elseif readyCtx.activeChainSet and readyCtx.activeChainSet[q.id] then
            momentum = momentum + 24
        end
    end
    local zTarget = targetZoneId and data.zones and data.zones[targetZoneId]
    if zTarget and zTarget.faction and p.faction and (zTarget.faction == "Alliance" or zTarget.faction == "Horde") and zTarget.faction ~= p.faction then
        momentum = momentum - 18
    end
    if q.classMask and q.classMask > 0 and p.classId then
        momentum = momentum + 10
    end
    if q.isGroup and action ~= "TURN_IN" and not q.isDungeon then
        momentum = momentum - 6
    end

    local wXp = tonumber(weights.xp) or 45
    local wTravel = tonumber(weights.travel) or 25
    local wDungeon = tonumber(weights.dungeon) or 20
    local wReward = tonumber(weights.reward) or 10
    local wSkin = tonumber(weights.skinning or weights.profession) or 0
    local wSum = math.max(1, wXp + wTravel + wDungeon + wReward + wSkin)

    local weighted = (
        wXp * xpNorm
        - wTravel * travelPenalty
        + wDungeon * dungeonNorm
        + wReward * rewardNorm
        + wSkin * skinningNorm
    ) / wSum

    local out = outSlot or {}
    out.quest = q
    out.rawAction = action
    out.effAction = effAction
    out.score = weighted + momentum
    out.level = level
    out.pt = pt
    out.targetZoneId = targetZoneId
    out.targetX = targetX
    out.targetY = targetY
    out.travelMins = travelMins
    out.estMins = estMins
    out.effXp = effXp
    out.xpPct = xpPct
    out.xpNorm = xpNorm
    out.travelPenalty = travelPenalty
    out.dungeonInfo = dungeonInfo
    out.dungeonNorm = dungeonNorm
    out.goldCopper = goldCopper
    out.bagSlots = bagSlots
    out.classScore = classScore
    out.className = className
    out.classFile = classFile
    out.rogueScore = rogueScore
    out.rewardNorm = rewardNorm
    out.skinScore = skinScore
    out.isLW = isLW
    out.isSkin = isSkin
    out.skinningNorm = skinningNorm
    out.readyPeerCount = readyPeerCount
    out.hasFollowup = hasFollowup
    out.isFriendChain = isFriendChain
    return out
end

local function MaterializeCandidate(m, vState, data)
    local q = m.quest
    local action = m.rawAction
    local level = m.level
    local targetZoneId = m.targetZoneId
    local zInfo = targetZoneId and data.zones and data.zones[targetZoneId]
    local targetZoneName = (zInfo and zInfo.name) or q.zoneName or ("Zone " .. tostring(targetZoneId or "?"))

    local reasons = {}
    if m.isFriendChain then
        reasons[#reasons + 1] = "[Friend Chain] [GPS Reorder]"
    end
    if action == "TURN_IN" then
        if (m.readyPeerCount or 0) > 0 then
            reasons[#reasons + 1] = string.format("Ready Turn-In · ready to turn in (%d grouped at hub)", m.readyPeerCount + 1)
        elseif m.hasFollowup then
            reasons[#reasons + 1] = "Ready Turn-In · ready to turn in (unlocks follow-up)"
        else
            reasons[#reasons + 1] = "Ready Turn-In · ready to turn in"
        end
    elseif action == "COMPLETE" then
        reasons[#reasons + 1] = "Already in quest log"
    end
    if m.effXp > 0 then
        reasons[#reasons + 1] = string.format("+%s XP (%.1f%% lvl)", ns.FormatNumber(m.effXp), m.xpPct)
    end
    if targetZoneId == vState.zoneId then
        reasons[#reasons + 1] = string.format("In %s (%.1fm)", targetZoneName, m.travelMins)
    else
        reasons[#reasons + 1] = string.format("Travel to %s (%.1fm)", targetZoneName, m.travelMins)
    end
    if m.dungeonInfo and m.dungeonInfo.score >= 30 then
        if m.dungeonInfo.stepIndex then
            reasons[#reasons + 1] = string.format("Unlocks %s (%d/%d)", m.dungeonInfo.dungeonName, m.dungeonInfo.stepIndex, m.dungeonInfo.totalSteps or 1)
        else
            reasons[#reasons + 1] = "Dungeon: " .. m.dungeonInfo.dungeonName
        end
    end
    if m.bagSlots > 0 then
        reasons[#reasons + 1] = string.format("+%d-Slot Bag", m.bagSlots)
    end
    if (m.classScore or 0) >= 40 then
        reasons[#reasons + 1] = string.format("%s BiS %d", m.className or "Class", math.floor(m.classScore + 0.5))
    elseif m.goldCopper >= 3500 then
        reasons[#reasons + 1] = ns.FormatMoneyPlain(m.goldCopper)
    end
    if m.isLW then
        reasons[#reasons + 1] = "Leatherworking"
    elseif m.isSkin then
        reasons[#reasons + 1] = string.format("Skinning %d", math.floor(m.skinScore + 0.5))
    end

    local chainInfo = (ns.State and ns.State.GetChainProgressInfo and ns.State.GetChainProgressInfo(q.id, vState.player, nil, m.effAction)) or nil
    if chainInfo and (chainInfo.total or 0) >= 2 then
        if (chainInfo.remainingQuests or 0) > 0 or (chainInfo.remainingSteps or 0) > 0 then
            reasons[#reasons + 1] = string.format("Chain %d/%d (%dq/%ds left)", chainInfo.pos or 1, chainInfo.total, chainInfo.remainingQuests or 0, chainInfo.remainingSteps or 0)
        else
            reasons[#reasons + 1] = string.format("Chain %d/%d (final)", chainInfo.pos or chainInfo.total, chainInfo.total)
        end
    end

    local npcObj = (action == "TURN_IN" and q.turnin) or q.starter
    local travelNote = nil
    if targetZoneId ~= vState.zoneId then
        travelNote = string.format("Move from %s to %s", vState.zoneName or "current zone", targetZoneName)
    end

    local step = {
        index = vState.stepCounter or 1,
        action = m.effAction,
        questId = q.id,
        questIds = { q.id },
        dungeonId = q.dungeonId or (m.dungeonInfo and m.dungeonInfo.dungeonId) or nil,
        title = q.name or ("Quest #" .. tostring(q.id)),
        zoneId = targetZoneId,
        zoneName = targetZoneName,
        x = m.targetX,
        y = m.targetY,
        npcId = npcObj and npcObj.id or nil,
        npcName = (npcObj and npcObj.name) or (m.pt and m.pt.label) or nil,
        travelNote = travelNote,
        why = table.concat(reasons, " · "),
        expectedLevel = level,
        expectedXpPct = math.min(99, math.floor((vState.xpPct or 0) + m.xpPct)),
        estMinutes = math.floor(m.estMins * 10 + 0.5) / 10,
        isReadyTurnIn = (action == "TURN_IN"),
        isGpsReordered = m.isFriendChain and true or nil,
        isFriendChain = m.isFriendChain and true or nil,
        isDynamic = true,
        chainStep = chainInfo and chainInfo.pos or nil,
        chainPos = chainInfo and chainInfo.pos or nil,
        chainTotal = chainInfo and chainInfo.total or nil,
        chainRemainingQuests = chainInfo and chainInfo.remainingQuests or nil,
        chainRemainingSteps = chainInfo and chainInfo.remainingSteps or nil,
        chainRootId = chainInfo and chainInfo.rootId or nil,
        chainRootName = chainInfo and chainInfo.rootName or nil,
        badges = {
            isReadyTurnIn = (action == "TURN_IN"),
            isGpsReordered = m.isFriendChain and true or nil,
            isFriendChain = m.isFriendChain and true or nil,
            readyBatchCount = (action == "TURN_IN") and ((m.readyPeerCount or 0) + 1) or nil,
            chain = chainInfo and {
                pos = chainInfo.pos,
                total = chainInfo.total,
                remainingQuests = chainInfo.remainingQuests,
                remainingSteps = chainInfo.remainingSteps,
                rootId = chainInfo.rootId,
                rootName = chainInfo.rootName,
            } or nil,
            xp = m.effXp,
            goldCopper = m.goldCopper,
            bagSlots = m.bagSlots,
            classScore = m.classScore,
            classLabel = m.className,
            rogueScore = m.rogueScore,
            skinningScore = m.skinScore,
            isLeatherworking = m.isLW,
            isSkinning = m.isSkin,
            requiredSkill = q.requiredSkill,
            unlocksDungeon = m.dungeonInfo and m.dungeonInfo.dungeonName or nil,
        },
        scoreBreakdown = {
            total = math.floor(m.score * 10 + 0.5) / 10,
            xp = math.floor(m.xpNorm + 0.5),
            travel = math.floor(m.travelPenalty + 0.5),
            dungeon = math.floor(m.dungeonNorm + 0.5),
            reward = math.floor(m.rewardNorm + 0.5),
            skinning = math.floor(m.skinningNorm + 0.5),
        },
    }

    return {
        step = step,
        quest = q,
        rawAction = action,
        score = m.score,
        effXp = m.effXp,
    }
end

function Scorer.EvaluateCandidate(q, action, vState, weights, toggles, data)
    data = data or ns.GetData()
    weights = weights or ns.GetActiveWeights()
    toggles = type(toggles) == "table" and toggles or ((ns.charDB and ns.charDB.toggles) or {})
    local m = ComputeCandidateMetrics(q, action, vState, weights, toggles, data)
    return MaterializeCandidate(m, vState, data)
end

function Scorer.RankAvailableCandidates(vState, weights, toggles, data)
    local liveData = ns.GetData()
    local isDefaultData = (data == nil or data == liveData)
    data = data or liveData
    weights = weights or ns.GetActiveWeights()
    toggles = type(toggles) == "table" and toggles or ((ns.charDB and ns.charDB.toggles) or {})

    local isLive = (vState and vState.isLiveState == true)
    local cap = (vState and tonumber(vState.maxCandidates)) or ((vState and vState.stepCounter and vState.stepCounter > 1) and 1 or 20)
    local pLevel = (vState and vState.level) or 24
    local maxOffset = ns.State.GetEffectiveMaxLevelOffset and ns.State.GetEffectiveMaxLevelOffset(toggles) or 2
    local rankLiveKey = nil
    if isLive and isDefaultData and ((vState.stepCounter or 1) == 1) and ns.State then
        local gen = ns.State.GetRefreshGeneration and ns.State.GetRefreshGeneration() or 0
        local sGen = ns.State.GetQuestStateGeneration and ns.State.GetQuestStateGeneration() or 0
        local readySig = ns.State.GetReadyTurnInSignature and ns.State.GetReadyTurnInSignature() or ""
        local locSig = ns.State.GetLocationPlanSignature and ns.State.GetLocationPlanSignature() or ""
        local vp = vState.player or {}
        local qx = vState.x and math.floor(vState.x * 0.5) or -1
        local qy = vState.y and math.floor(vState.y * 0.5) or -1
        local hReady = (ns.Nav and ns.Nav.IsHearthReady and ns.Nav.IsHearthReady()) and 1 or 0
        local hasQdb = (_G.QuestieDB ~= nil or _G.QuestieLoader ~= nil) and 1 or 0
        local rankSkipElite = (ns.State and ns.State.ShouldSkipEliteNow) and ns.State.ShouldSkipEliteNow(vp, toggles) or (toggles.skipEliteSolo ~= false and not (vp and vp.inGroup))
        local rankSkipDungeons = (ns.State and ns.State.ShouldSkipDungeonsNow) and ns.State.ShouldSkipDungeonsNow(toggles) or (toggles.skipDungeons ~= false)
        rankLiveKey = string.format(
            "%d:%d:%d:%d:%s:%d:%d:%d:%.1f:%.1f:%.1f:%.1f:%.1f:%s:%s:%s:%s:%s:%s:%d:%s:%s:%d:%d:%d:%d:%d",
            gen, sGen, pLevel, maxOffset, tostring(vState.zoneId or 0), qx, qy, cap,
            tonumber(weights.xp) or 45,
            tonumber(weights.travel) or 25,
            tonumber(weights.dungeon) or 20,
            tonumber(weights.reward) or 10,
            tonumber(weights.skinning or weights.profession) or 0,
            tostring(toggles.consultQuestie ~= false),
            tostring(toggles.preferDungeonUnlocks ~= false),
            tostring(toggles.prioritizeBags ~= false),
            tostring(toggles.prioritizeClassGear ~= false and toggles.prioritizeRogueGear ~= false),
            tostring(toggles.prioritizeSkinningLW ~= false),
            tostring(toggles.locationReorder ~= false),
            tonumber(vp.raceId) or 0,
            tostring(vp.faction or "Alliance"),
            tostring(vp.classFile or "ROGUE"),
            hReady,
            hasQdb,
            #((ns.State.GetBlockedSteps and ns.State.GetBlockedSteps()) or {}),
            rankSkipElite and 1 or 0,
            rankSkipDungeons and 1 or 0
        ) .. "|" .. readySig .. "|" .. locSig
        if rankLiveCache.valid and rankLiveCache.key == rankLiveKey and rankLiveCache.out then
            if vState and rankLiveCache.seedQids then
                vState.seedQids = rankLiveCache.seedQids
            end
            return rankLiveCache.out
        end
    end

    local cdb = ns.charDB or {}
    local skipped = cdb.skippedSteps or {}
    local doneCustom = cdb.completedCustomSteps or {}
    local hasSkipOrDone = (next(skipped) ~= nil) or (next(doneCustom) ~= nil)
    local metricsPool = scorerScratch.metricsPool
    local rawCandidates = scorerScratch.rawCandidates
    local candCount = 0

    local vPlayer
    if vState.player then
        if vState.level == vState.player.level then
            vPlayer = vState.player
        else
            vPlayer = {
                level = vState.level,
                faction = vState.player.faction,
                raceName = vState.player.raceName,
                raceFile = vState.player.raceFile,
                raceId = vState.player.raceId,
                raceBitmask = vState.player.raceBitmask,
                className = vState.player.className,
                classFile = vState.player.classFile,
                classId = vState.player.classId,
                classBitmask = vState.player.classBitmask,
                inGroup = vState.player.inGroup,
                hasSkinning = vState.player.hasSkinning,
                hasLeatherworking = vState.player.hasLeatherworking,
                skinningRank = vState.player.skinningRank,
                leatherworkingRank = vState.player.leatherworkingRank,
                professions = vState.player.professions,
                bindLocation = vState.player.bindLocation,
                hearthZoneId = vState.player.hearthZoneId,
            }
        end
    else
        vPlayer = { level = vState.level }
    end

    local fastOpts = FAST_BLOCK_OPTS

    local pClassFile = vPlayer.classFile or "ROGUE"

    -- Precompute ready turn-in points, peer hub counts, and off-DB synthetic quests
    local readyPoints = scorerScratch.readyPoints
    local readyPointPool = scorerScratch.readyPointPool
    local readyPointCount = 0
    local readyExtras = scorerScratch.readyExtras
    local readyExtraCount = 0
    for qid, activeEntry in pairs(vState.active or {}) do
        if activeEntry and activeEntry.isComplete == true and not activeEntry.isFailed and not vState.completed[qid] then
            local q = data.quests and data.quests[qid] or nil
            if not q and ns.State and ns.State.ResolveQuestTurnin then
                local resolved = ns.State.ResolveQuestTurnin(qid)
                if resolved then
                    q = {
                        id = qid,
                        name = resolved.title or activeEntry.title or ("Quest #" .. tostring(qid)),
                        questLevel = resolved.level or activeEntry.level or pLevel,
                        minLevel = 1,
                        zoneId = resolved.zoneId,
                        zoneName = resolved.zoneName,
                        xp = resolved.xp or 0,
                        goldCopper = resolved.money or 0,
                        bagSlots = resolved.bagSlots or 0,
                        rogueScore = resolved.rogueScore or 0,
                        turnin = (resolved.x or resolved.npcName or resolved.zoneId) and {
                            zoneId = resolved.zoneId,
                            zoneName = resolved.zoneName,
                            x = resolved.x,
                            y = resolved.y,
                            id = resolved.npcId,
                            name = resolved.npcName,
                        } or nil,
                        isSyntheticReady = true,
                    }
                    readyExtraCount = readyExtraCount + 1
                    readyExtras[readyExtraCount] = q
                end
            end
            if q then
                local pt = Scorer.GetQuestPoint(q, "TURN_IN")
                readyPointCount = readyPointCount + 1
                local rp = readyPointPool[readyPointCount]
                if not rp then
                    rp = {}
                    readyPointPool[readyPointCount] = rp
                end
                rp.qid = qid
                rp.zoneId = (pt and pt.zoneId) or q.zoneId
                rp.x = pt and pt.x or nil
                rp.y = pt and pt.y or nil
                readyPoints[readyPointCount] = rp
            end
        end
    end
    for trim = readyPointCount + 1, #readyPoints do
        readyPoints[trim] = nil
    end
    for trim = readyExtraCount + 1, #readyExtras do
        readyExtras[trim] = nil
    end

    local peerCountByQid = WipeMap(scorerScratch.peerCountByQid)
    local navAvail = (ns.Nav and ns.Nav.IsAvailable and ns.Nav.IsAvailable()) and true or false
    for i = 1, readyPointCount do
        local a = readyPoints[i]
        local peers = 0
        for j = 1, readyPointCount do
            if i ~= j then
                local b = readyPoints[j]
                if a.zoneId and b.zoneId and a.zoneId == b.zoneId and a.x and a.y and b.x and b.y then
                    local dx, dy = a.x - b.x, a.y - b.y
                    if navAvail then
                        if (dx * dx + dy * dy) <= 900.0 then
                            local tSec, ok = ns.Nav.GetTravelSeconds(a.zoneId, a.x, a.y, b.zoneId, b.x, b.y, vPlayer, UNMOUNTED_NAV_OPTS)
                            if ok and tSec and tSec <= 48.0 then
                                peers = peers + 1
                            end
                        end
                    elseif math.sqrt(dx * dx + dy * dy) <= READY_HUB_RADIUS then
                        peers = peers + 1
                    end
                end
            end
        end
        peerCountByQid[a.qid] = peers
    end
    local recentChainSet = WipeMap(scorerScratch.recentChainSet)
    local simChainSet = WipeMap(scorerScratch.simChainSet)
    local activeChainSet = WipeMap(scorerScratch.activeChainSet)
    if ns.State and ns.State.GetRecentAcceptedQuests then
        local recentMap, recentFollowups = ns.State.GetRecentAcceptedQuests()
        for rqid, info in pairs(recentMap or {}) do
            if (not vState.zoneId) or (not info.zoneId) or info.zoneId == vState.zoneId then
                recentChainSet[rqid] = true
                local rq = data.quests and data.quests[rqid]
                if rq and type(rq.nextQuestIds) == "table" then
                    for _, nid in ipairs(rq.nextQuestIds) do
                        local nq = data.quests and data.quests[nid]
                        local nz = nq and (nq.turnin and nq.turnin.zoneId or nq.zoneId)
                        if (not vState.zoneId) or (not nz) or nz == vState.zoneId then
                            recentChainSet[nid] = true
                        end
                    end
                end
            end
        end
        for fqid, info in pairs(recentFollowups or {}) do
            if (not vState.zoneId) or (not info.zoneId) or info.zoneId == vState.zoneId then
                recentChainSet[fqid] = true
            end
        end
    end
    for fqid in pairs(vState.chainFollowups or {}) do
        local fq = data.quests and data.quests[fqid]
        if fq and ((not vState.zoneId) or fq.zoneId == vState.zoneId) then
            simChainSet[fqid] = true
        end
    end
    for cqid in pairs(vState.inChainQuests or {}) do
        local cq = data.quests and data.quests[cqid]
        if cq and ((not vState.zoneId) or cq.zoneId == vState.zoneId) then
            activeChainSet[cqid] = true
        end
    end
    for aqid, aEntry in pairs(vState.active or {}) do
        if aEntry then
            local aq = data.quests and data.quests[aqid]
            if aq and ((not vState.zoneId) or aq.zoneId == vState.zoneId) and type(aq.nextQuestIds) == "table" then
                for _, nid in ipairs(aq.nextQuestIds) do
                    local nq = data.quests and data.quests[nid]
                    local nz = nq and (nq.turnin and nq.turnin.zoneId or nq.zoneId)
                    if (not vState.zoneId) or (not nz) or nz == vState.zoneId then
                        activeChainSet[nid] = true
                    end
                end
            end
        end
    end
    local readyCtx = scorerScratch.readyCtx
    readyCtx.peerCountByQid = peerCountByQid
    readyCtx.recentChainSet = recentChainSet
    readyCtx.simChainSet = simChainSet
    readyCtx.activeChainSet = activeChainSet
    local hasLocalSameZoneWork = false

    local function EvaluateAndPush(q, action)
        local slotIdx = candCount + 1
        local slot = metricsPool[slotIdx]
        if not slot then
            slot = {}
            metricsPool[slotIdx] = slot
        end
        local m = ComputeCandidateMetrics(q, action, vState, weights, toggles, data, readyCtx, slot)
        local skipEntry = false
        if hasSkipOrDone then
            local dynKey = ns.State.GetStepKey({ index = 1, action = m.effAction, questId = q.id }, 1, "dynamic")
            if skipped[dynKey] or doneCustom[dynKey] then
                skipEntry = true
            end
        end
        if not skipEntry then
            candCount = slotIdx
            rawCandidates[candCount] = m
            if (action == "ACCEPT" or action == "COMPLETE") and vState.zoneId and m.targetZoneId == vState.zoneId then
                hasLocalSameZoneWork = true
            end
        end
    end

    local canUseLiveIndex = navAvail and isDefaultData and ns.Nav and ns.Nav.IsIndexedEligible
        and (isLive or (vState and vState.fromLiveSeed == true and vState.player ~= nil and vState.level == vState.player.level))
    local navIndexedOkFn = canUseLiveIndex and ns.Nav.IsIndexedEligible or nil

    local function PassesAcceptWorthiness(q, qid)
        local qL = q.questLevel or q.minLevel or 1
        local mult = Scorer.XpDecayMultiplier(pLevel, qL)
        return mult > 0.15
            or (q.bagSlots or 0) > 0
            or ns.GetQuestClassScore(q, pClassFile) >= 50
            or (q.classMask and q.classMask > 0)
            or q.isLeatherworking
            or Scorer.GetDungeonUnlockInfo(qid, pLevel, data)
    end

    local evalSkipElite = (ns.State and ns.State.ShouldSkipEliteNow)
        and ns.State.ShouldSkipEliteNow(vPlayer, toggles)
        or (toggles.skipEliteSolo ~= false and not (vPlayer and vPlayer.inGroup))
    local evalSkipDungeons = (ns.State and ns.State.ShouldSkipDungeonsNow)
        and ns.State.ShouldSkipDungeonsNow(toggles)
        or (toggles.skipDungeons ~= false)

    local function TryEvaluateQuestId(qid, q)
        if not q or vState.completed[qid] then
            return
        end
        local activeEntry = vState.active[qid]
        local isReadyTurnIn = (activeEntry ~= nil) and (activeEntry.isComplete == true) and (not activeEntry.isFailed)
        if isReadyTurnIn then
            EvaluateAndPush(q, "TURN_IN")
        elseif activeEntry then
            if ns.State.IsEligibleForPlayer(qid, vPlayer)
                and not (evalSkipElite and (not q.isDungeon) and (q.isGroup == true or activeEntry.isElite == true))
                and not (evalSkipDungeons and (q.isDungeon == true or activeEntry.isDungeon == true)) then
                EvaluateAndPush(q, "COMPLETE")
            end
        elseif (q.minLevel or 1) <= pLevel and (q.questLevel or 0) <= pLevel + maxOffset
            and not (evalSkipElite and q.isGroup == true and not q.isDungeon)
            and not (evalSkipDungeons and q.isDungeon == true) then
            if navIndexedOkFn and navIndexedOkFn(qid) then
                local exclBlocked = false
                if (not isLive) and q.exclusiveTo then
                    for _, exId in ipairs(q.exclusiveTo) do
                        if exId ~= qid and (vState.completed[exId] or vState.active[exId]) then
                            exclBlocked = true
                            break
                        end
                    end
                end
                if (not exclBlocked) and PassesAcceptWorthiness(q, qid) then
                    EvaluateAndPush(q, "ACCEPT")
                end
            elseif ns.State.IsEligibleForPlayer(qid, vPlayer) then
                local isExcluded = false
                if q.exclusiveTo then
                    for _, exId in ipairs(q.exclusiveTo) do
                        if exId ~= qid and (vState.completed[exId] or vState.active[exId]) then
                            isExcluded = true
                            break
                        end
                    end
                end

                if not isExcluded then
                    local blkComp = isLive and nil or vState.completed
                    local blkAct = isLive and nil or vState.active
                    local blockCode = ns.State.GetQuestBlockReason(
                        qid,
                        q.isDungeon and "DUNGEON_PREP" or "ACCEPT",
                        vPlayer,
                        blkComp,
                        blkAct,
                        toggles,
                        fastOpts
                    )
                    if not blockCode and PassesAcceptWorthiness(q, qid) then
                        EvaluateAndPush(q, "ACCEPT")
                    end
                end
            end
        end
    end

    local isLookahead = ((vState.stepCounter or 1) > 1)
    if navAvail and vState.zoneId and vState.x and vState.y and data.quests then
        local navPlayer = isLookahead and (vState.player or vPlayer) or vPlayer
        local navOpts = isLookahead and LOOKAHEAD_NAV_OPTS or LIVE_NAV_OPTS
        local pool, poolCount = ns.Nav.FindNearestEligibleQuests(vState.zoneId, vState.x, vState.y, data, navPlayer, navOpts)
        local seenCand = scorerScratch.seenCand
        local seenList = scorerScratch.seenList
        local seenCount = 0
        local function EvalOnce(qid)
            if qid and not seenCand[qid] then
                seenCand[qid] = true
                seenCount = seenCount + 1
                seenList[seenCount] = qid
                TryEvaluateQuestId(qid, data.quests[qid])
            end
        end
        for aqid in pairs(vState.active or {}) do
            EvalOnce(aqid)
            local aq = data.quests[aqid]
            if aq and type(aq.nextQuestIds) == "table" then
                for _, nid in ipairs(aq.nextQuestIds) do
                    EvalOnce(nid)
                end
            end
        end
        for i = 1, (poolCount or 0) do
            local rec = pool[i]
            if rec and rec.qid then
                EvalOnce(rec.qid)
            end
        end
        if isLookahead and type(vState.seedQids) == "table" then
            local seeds = vState.seedQids
            for i = 1, #seeds do
                EvalOnce(seeds[i])
            end
            if vState.player and vState.level ~= vState.player.level then
                local priorityList = ns.Nav.GetPriorityAndUnanchoredQids()
                for i = 1, #(priorityList or {}) do
                    EvalOnce(priorityList[i])
                end
            end
        else
            local priorityList, unanchoredList = ns.Nav.GetPriorityAndUnanchoredQids()
            for i = 1, #(priorityList or {}) do
                EvalOnce(priorityList[i])
            end
            local uCount = #(unanchoredList or {})
            local uLimit = (vState.unanchoredCap and tonumber(vState.unanchoredCap)) or 40
            if uCount > uLimit then uCount = uLimit end
            for i = 1, uCount do
                EvalOnce(unanchoredList[i])
            end
        end
        for fqid in pairs(vState.chainFollowups or {}) do
            EvalOnce(fqid)
        end
        for uqid in pairs(vState.newlyUnlocked or {}) do
            EvalOnce(uqid)
        end
        for i = 1, seenCount do
            seenCand[seenList[i]] = nil
            seenList[i] = nil
        end
    else
        for qid, q in pairs(data.quests or {}) do
            TryEvaluateQuestId(qid, q)
        end
    end

    for _, eq in ipairs(readyExtras) do
        EvaluateAndPush(eq, "TURN_IN")
    end

    if hasLocalSameZoneWork and vState.zoneId then
        for i = 1, candCount do
            local m = rawCandidates[i]
            if m.rawAction == "TURN_IN" and m.targetZoneId and m.targetZoneId ~= vState.zoneId then
                m.score = m.score - READY_CROSS_ZONE_DETOUR_PENALTY
            end
        end
    end

    for i = candCount + 1, #rawCandidates do
        rawCandidates[i] = nil
    end

    local out = {}
    local checkLive = (vState.stepCounter == 1) and (toggles.consultQuestie ~= false)
    local seedQids = nil

    if candCount > 0 then
        if cap == 1 and (not checkLive) and (vState.stepCounter or 1) > 1 then
            local best = rawCandidates[1]
            for i = 2, candCount do
                local m = rawCandidates[i]
                if CompareCandidateMetrics(m, best) then
                    best = m
                end
            end
            out[1] = MaterializeCandidate(best, vState, data)
        else
            if candCount > 1 then
                table.sort(rawCandidates, CompareCandidateMetrics)
            end

            if (vState.stepCounter or 1) == 1 then
                seedQids = {}
                local seenSeed = {}
                for i = 1, candCount do
                    local q = rawCandidates[i].quest
                    local qid = q and q.id
                    if qid and not q.isSyntheticReady and not seenSeed[qid] then
                        seenSeed[qid] = true
                        seedQids[#seedQids + 1] = qid
                        if #seedQids >= 36 then
                            break
                        end
                    end
                end
                if vState then
                    vState.seedQids = seedQids
                end
            end

            -- Bound expensive QuestieDB live/IsDoable checks and full step materialization to top sorted candidates
            local liveOpts = LIVE_BLOCK_OPTS
            local questieChecked = 0
            local liveComp = isLive and nil or vState.completed
            local liveAct = isLive and nil or vState.active

            for i = 1, candCount do
                local m = rawCandidates[i]
                local keep = true
                if checkLive and m.rawAction == "ACCEPT" then
                    if questieChecked < MAX_CANDIDATE_QUESTIE_CHECKS then
                        questieChecked = questieChecked + 1
                        local q = m.quest
                        local bCode = ns.State.GetQuestBlockReason(
                            q.id,
                            q.isDungeon and "DUNGEON_PREP" or "ACCEPT",
                            vPlayer,
                            liveComp,
                            liveAct,
                            toggles,
                            liveOpts
                        )
                        if bCode then
                            keep = false
                        end
                    else
                        break
                    end
                end
                if keep then
                    out[#out + 1] = MaterializeCandidate(m, vState, data)
                    if #out >= cap then
                        break
                    end
                end
            end
        end
    elseif (vState.stepCounter or 1) == 1 then
        seedQids = {}
        if vState then
            vState.seedQids = seedQids
        end
    end

    if rankLiveKey then
        rankLiveCache.valid = true
        rankLiveCache.key = rankLiveKey
        rankLiveCache.out = out
        rankLiveCache.seedQids = seedQids
    end

    return out
end

function Scorer.GetDynamicSteps(player, weights, toggles, count)
    if type(toggles) == "number" and count == nil then
        count = toggles
        toggles = nil
    end
    toggles = type(toggles) == "table" and toggles or ((ns.charDB and ns.charDB.toggles) or {})
    weights = weights or (ns.GetActiveWeights and ns.GetActiveWeights()) or {}
    count = count or 6
    player = player or ns.State.GetPlayerInfo()
    local data = ns.GetData()

    local gen = ns.State and ns.State.GetRefreshGeneration and ns.State.GetRefreshGeneration() or 0
    local sGen = ns.State and ns.State.GetQuestStateGeneration and ns.State.GetQuestStateGeneration() or 0
    local maxOffset = ns.State and ns.State.GetEffectiveMaxLevelOffset and ns.State.GetEffectiveMaxLevelOffset(toggles) or 2
    local readySig = ns.State and ns.State.GetReadyTurnInSignature and ns.State.GetReadyTurnInSignature() or ""
    local locSig = ns.State and ns.State.GetLocationPlanSignature and ns.State.GetLocationPlanSignature() or ""
    local hReady = (ns.Nav and ns.Nav.IsHearthReady and ns.Nav.IsHearthReady()) and 1 or 0
    local hasQdb = (_G.QuestieDB ~= nil or _G.QuestieLoader ~= nil) and 1 or 0
    local blkCount = #((ns.State and ns.State.GetBlockedSteps and ns.State.GetBlockedSteps()) or {})
    local prof = player and player.professions
    local skipEliteNow = (ns.State and ns.State.ShouldSkipEliteNow) and ns.State.ShouldSkipEliteNow(player, toggles) or (toggles.skipEliteSolo ~= false and not (player and player.inGroup))
    local skipDungeonsNow = (ns.State and ns.State.ShouldSkipDungeonsNow) and ns.State.ShouldSkipDungeonsNow(toggles) or (toggles.skipDungeons ~= false)

    local dynKey = string.format(
        "%d:%d:%d:%d:%d:%s:%s:%s:%s:%.1f:%.1f:%.1f:%.1f:%.1f:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%d:%d:%d",
        gen, sGen,
        tonumber(player and player.level) or 24,
        tonumber(player and player.xpPct) or 0,
        maxOffset,
        tostring(player and player.zoneId or 0),
        tostring(player and player.x or -1),
        tostring(player and player.y or -1),
        tostring(player and player.isDead == true),
        tonumber(weights.xp) or 45,
        tonumber(weights.travel) or 25,
        tonumber(weights.dungeon) or 20,
        tonumber(weights.reward) or 10,
        tonumber(weights.skinning or weights.profession) or 0,
        tostring(toggles.consultQuestie ~= false),
        tostring(toggles.preferDungeonUnlocks ~= false),
        tostring(toggles.prioritizeBags ~= false),
        tostring(toggles.prioritizeClassGear ~= false and toggles.prioritizeRogueGear ~= false),
        tostring(toggles.prioritizeSkinningLW ~= false),
        tostring(toggles.locationReorder ~= false),
        tostring(toggles.proactiveChains ~= false),
        tostring(skipEliteNow),
        tostring(skipDungeonsNow),
        tostring(player and player.raceId or 0),
        tostring(player and player.faction or "Alliance"),
        tostring(player and player.classFile or "ROGUE"),
        tostring(prof and prof.skinningRank or -1),
        tostring(prof and prof.leatherworkingRank or -1),
        hReady,
        hasQdb,
        blkCount
    ) .. "|" .. readySig .. "|" .. locSig

    if dynamicStepsCache.valid and dynamicStepsCache.key == dynKey and dynamicStepsCache.steps and dynamicStepsCache.requestedCount >= count then
        if count == dynamicStepsCache.requestedCount then
            return dynamicStepsCache.steps
        end
        local sliced = {}
        local cachedList = dynamicStepsCache.steps
        for i = 1, math.min(count, #cachedList) do
            sliced[i] = cachedList[i]
        end
        return sliced
    end

    local baseCompleted = (ns.State and ns.State.GetCompletedReadOnly and ns.State.GetCompletedReadOnly())
        or (ns.State and ns.State.GetCompletedSet and ns.State.GetCompletedSet())
    if not baseCompleted then
        baseCompleted = {}
        for qid in pairs(data.quests or {}) do
            if ns.State and ns.State.IsQuestCompleted and ns.State.IsQuestCompleted(qid) then
                baseCompleted[qid] = true
            end
        end
    end
    local vCompleted = setmetatable({}, { __index = baseCompleted })

    local vActive = {}
    for qid, entry in pairs(ns.State.GetActiveQuests() or {}) do
        vActive[qid] = {
            questId = qid,
            isComplete = entry.isComplete,
            isFailed = entry.isFailed,
            isElite = entry.isElite,
            isDungeon = entry.isDungeon,
        }
    end

    local simChainFollowups = {}
    local simInChainQuests = {}
    if ns.State and ns.State.GetRecentAcceptedQuests then
        local _, recentFollowups = ns.State.GetRecentAcceptedQuests()
        for fqid, info in pairs(recentFollowups or {}) do
            if (not player.zoneId) or (not info.zoneId) or info.zoneId == player.zoneId then
                simChainFollowups[fqid] = true
                simInChainQuests[fqid] = true
            end
        end
    end
    for aqid in pairs(vActive) do
        local aq = data.quests and data.quests[aqid]
        if aq and type(aq.nextQuestIds) == "table" and #aq.nextQuestIds > 0 then
            simInChainQuests[aqid] = true
        end
    end

    local vState = {
        player = player,
        level = player.level or 24,
        xpPct = player.xpPct or 0,
        zoneId = player.zoneId or 11,
        zoneName = player.zoneName or "Wetlands",
        x = player.x or 50,
        y = player.y or 50,
        completed = vCompleted,
        active = vActive,
        chainFollowups = simChainFollowups,
        inChainQuests = simInChainQuests,
        newlyUnlocked = {},
        stepCounter = 1,
        isLiveState = true,
        maxCandidates = 1,
        fromLiveSeed = true,
    }

    local steps = {}
    for i = 1, count do
        vState.stepCounter = i
        vState.isLiveState = (i == 1)
        local ranked = Scorer.RankAvailableCandidates(vState, weights, toggles, data)
        local pick = ranked[1]
        if not pick then
            break
        end
        steps[#steps + 1] = pick.step

        -- Advance virtual simulation state for next lookahead step
        local qid = pick.quest.id
        local prevZoneId = vState.zoneId
        vState.zoneId = pick.step.zoneId or vState.zoneId
        vState.zoneName = pick.step.zoneName or vState.zoneName
        vState.x = pick.step.x or vState.x
        vState.y = pick.step.y or vState.y

        if pick.rawAction == "ACCEPT" then
            local hasObj = pick.quest and type(pick.quest.objectives) == "table" and #pick.quest.objectives > 0
            vState.active[qid] = { questId = qid, isComplete = (not hasObj) }
            if vState.chainFollowups[qid] then
                vState.inChainQuests[qid] = true
                vState.chainFollowups[qid] = nil
            end
        elseif pick.rawAction == "COMPLETE" then
            vState.active[qid] = { questId = qid, isComplete = true }
        elseif pick.rawAction == "TURN_IN" then
            vState.active[qid] = nil
            vState.completed[qid] = true
            local pq = pick.quest
            local qz = (pq and pq.zoneId) or prevZoneId
            if pq and type(pq.nextQuestIds) == "table" then
                for _, nid in ipairs(pq.nextQuestIds) do
                    vState.newlyUnlocked[nid] = true
                    local nq = data.quests and data.quests[nid]
                    local nqz = nq and nq.zoneId
                    local ntuz = nq and nq.turnin and nq.turnin.zoneId
                    if nq and (not qz or (nqz == qz and (not ntuz or ntuz == qz))) then
                        vState.chainFollowups[nid] = true
                        vState.inChainQuests[nid] = true
                    end
                end
            end
            local toLvl = Scorer.XpToLevel(vState.level, data)
            local addPct = toLvl > 0 and ((pick.effXp / toLvl) * 100) or 0
            local newPct = (vState.xpPct or 0) + addPct
            while newPct >= 100 and vState.level < 60 do
                newPct = newPct - 100
                vState.level = vState.level + 1
            end
            vState.xpPct = math.floor(newPct + 0.5)
        end
    end

    dynamicStepsCache.valid = true
    dynamicStepsCache.key = dynKey
    dynamicStepsCache.steps = steps
    dynamicStepsCache.requestedCount = count

    return steps
end

--------------------------------------------------------------------------------
-- Dungeon Unlock & Special Rewards Views
--------------------------------------------------------------------------------

function Scorer.GetPriorityClassQuestStep(player, toggles, routeZoneId, data)
    player = player or ns.State.GetPlayerInfo()
    data = data or ns.GetData()
    toggles = type(toggles) == "table" and toggles or ((ns.charDB and ns.charDB.toggles) or {})
    local idx = EnsureIndexes(data)
    local classList = idx.classQuests and idx.classQuests[player.classFile or "ROGUE"]
    if type(classList) ~= "table" or #classList == 0 then
        return nil
    end

    local skipped = (ns.charDB and ns.charDB.skippedSteps) or {}
    local pLevel = tonumber(player.level) or 1
    local skipEliteNow = (ns.State and ns.State.ShouldSkipEliteNow) and ns.State.ShouldSkipEliteNow(player, toggles) or (toggles.skipEliteSolo ~= false and not (player and player.inGroup))
    local skipDungeonsNow = (ns.State and ns.State.ShouldSkipDungeonsNow) and ns.State.ShouldSkipDungeonsNow(toggles) or (toggles.skipDungeons ~= false)
    local best, bestPri = nil, -999999
    local fastOpts = { consultQuestie = false, checkIsDoable = false, useBulkCompletion = true, reasonless = true }

    for _, qid in ipairs(classList) do
        local q = data.quests and data.quests[qid]
        if q and not skipped["class_quest:" .. tostring(qid)] and ns.State.IsEligibleForPlayer(qid, player) then
            local st = ns.State.GetQuestStatus(qid, true)
            if st ~= "completed" and not ns.State.IsQuestExcluded(qid, true) then
                local action = (st == "ready" and "TURN_IN") or (st == "active" and "COMPLETE") or "ACCEPT"
                local minL = tonumber(q.minLevel) or 1
                local qL = tonumber(q.questLevel) or minL
                local okBand = (st == "ready" or st == "active") or (minL <= pLevel and qL >= pLevel - 6 and qL <= pLevel + 2)
                if okBand then
                    local blocked
                    if action == "ACCEPT" then
                        blocked = ns.State.GetQuestBlockReason(qid, "ACCEPT", player, nil, nil, toggles, fastOpts)
                    elseif action == "COMPLETE" then
                        if skipEliteNow and ns.State.IsQuestElite and ns.State.IsQuestElite(qid, q) then
                            blocked = "elite_solo"
                        elseif skipDungeonsNow and ((q and q.isDungeon == true) or (ns.State.IsQuestDungeon and ns.State.IsQuestDungeon(qid, q))) then
                            blocked = "dungeon_skipped"
                        end
                    end
                    if not blocked then
                        local qZone = q.zoneId
                        local inZone = (qZone == player.zoneId) or (routeZoneId and qZone == routeZoneId)
                        local pri = (st == "ready" and 500 or (st == "active" and 300 or 100))
                            + (inZone and 80 or 0)
                            + ns.GetQuestClassScore(q, player.classFile)
                            - math.abs(qL - pLevel) * 5
                        if inZone or st == "ready" or st == "active" or minL >= math.max(1, pLevel - 4) then
                            if pri > bestPri then
                                bestPri = pri
                                best = { q = q, action = action }
                            end
                        end
                    end
                end
            end
        end
    end

    if not best then return nil end
    local vState = {
        player = player,
        level = pLevel,
        xpPct = player.xpPct or 0,
        zoneId = player.zoneId,
        zoneName = player.zoneName,
        x = player.x or 50,
        y = player.y or 50,
        stepCounter = 1,
    }
    local m = ComputeCandidateMetrics(best.q, best.action, vState, ns.GetActiveWeights(), toggles, data)
    local cand = MaterializeCandidate(m, vState, data)
    if cand and cand.step then
        local actionLabel = (best.action == "TURN_IN" and "Turn In") or (best.action == "COMPLETE" and "Complete") or "Accept"
        cand.step.title = string.format("%s (%s): %s", actionLabel, player.className or "Class", best.q.name or ("Quest #" .. tostring(best.q.id)))
        cand.step.why = string.format("%s class quest · %s", player.className or "Class", cand.step.why or "")
        return cand.step
    end
    return nil
end

function Scorer.GetDungeonsNearLevel(playerLevel, includeAll, player)
    player = player or ns.State.GetPlayerInfo()
    playerLevel = playerLevel or player.level or 24
    local pFaction = player.faction or "Alliance"
    local pClassFile = player.classFile or "ROGUE"
    local gen = ns.State.GetRefreshGeneration and ns.State.GetRefreshGeneration() or 0
    local cacheKey = string.format("%d:%d:%s:%s:%s:%s",
        gen,
        tonumber(playerLevel) or 24,
        tostring(includeAll == true),
        tostring(pFaction),
        tostring(player.raceId or 0),
        tostring(pClassFile))
    if dungeonsViewCache[cacheKey] then
        return dungeonsViewCache[cacheKey]
    end

    local data = ns.GetData()
    local list = {}

    for dungeonId, d in pairs(data.dungeons or {}) do
        local minL = d.minLevel or 15
        local maxL = d.maxLevel or 60
        local recL = d.recommendedLevel or math.floor((minL + maxL) / 2)
        local near = (playerLevel >= minL - 5) and (playerLevel <= maxL + 4)
        local df = d.faction
        local factionOk = (not df) or (df == "Both") or (df == "Neutral") or (df == pFaction)

        if factionOk and (includeAll or near) then
            local chains = {}
            for _, ch in ipairs(d.unlockChains or {}) do
                local chainSteps = {}
                local doneCount = 0
                local nextMissing = nil
                for _, qid in ipairs(ch.questIds or {}) do
                    local q = data.quests and data.quests[qid]
                    local st = ns.State.GetQuestStatus(qid, true)
                    local prereqsOk = ns.State.ArePrereqsMet(qid, nil, true)
                    if st == "completed" then
                        doneCount = doneCount + 1
                    elseif not nextMissing then
                        nextMissing = {
                            questId = qid,
                            name = (q and q.name) or ("Quest #" .. tostring(qid)),
                            minLevel = q and q.minLevel or minL,
                            questLevel = q and q.questLevel or recL,
                            status = st,
                            prereqsMet = prereqsOk,
                            starter = q and q.starter or nil,
                            zoneId = q and q.zoneId or d.entranceZoneId,
                            zoneName = q and q.zoneName or d.entranceZoneName,
                        }
                    end
                    chainSteps[#chainSteps + 1] = {
                        questId = qid,
                        name = (q and q.name) or ("Quest #" .. tostring(qid)),
                        minLevel = q and q.minLevel or minL,
                        questLevel = q and q.questLevel or recL,
                        status = st,
                        prereqsMet = prereqsOk,
                        starter = q and q.starter or nil,
                    }
                end

                chains[#chains + 1] = {
                    chainName = ch.chainName or "Unlock Chain",
                    travelTip = ch.travelTip,
                    doneCount = doneCount,
                    totalCount = #(ch.questIds or {}),
                    nextMissing = nextMissing,
                    steps = chainSteps,
                }
            end

            local dQuests = {}
            local qDone, qActiveOrReady = 0, 0
            for _, qid in ipairs(d.questIds or {}) do
                local q = data.quests and data.quests[qid]
                if not q or ns.State.IsEligibleForPlayer(qid, player) then
                    local st = ns.State.GetQuestStatus(qid, true)
                    if st == "completed" then
                        qDone = qDone + 1
                    elseif st == "active" or st == "ready" then
                        qActiveOrReady = qActiveOrReady + 1
                    end
                    local qClassScore = q and ns.GetQuestClassScore(q, pClassFile) or 0
                    dQuests[#dQuests + 1] = {
                        questId = qid,
                        name = (q and q.name) or ("Quest #" .. tostring(qid)),
                        questLevel = q and q.questLevel or recL,
                        minLevel = q and q.minLevel or minL,
                        status = st,
                        bagSlots = q and q.bagSlots or 0,
                        goldCopper = q and q.goldCopper or 0,
                        classScore = qClassScore,
                        classLabel = player.className or "Class",
                        rogueScore = (pClassFile == "ROGUE") and qClassScore or (q and q.rogueScore or 0),
                        starter = q and q.starter or nil,
                    }
                end
            end

            list[#list + 1] = {
                dungeonId = dungeonId,
                name = d.name or tostring(dungeonId),
                zoneId = d.zoneId,
                zoneName = d.zoneName or d.name,
                entranceZoneId = d.entranceZoneId,
                entranceZoneName = d.entranceZoneName,
                entranceCoords = d.entranceCoords,
                minLevel = minL,
                maxLevel = maxL,
                recommendedLevel = recL,
                isNearLevel = near,
                distanceFromPlayer = math.abs(playerLevel - recL),
                chains = chains,
                quests = dQuests,
                completedQuests = qDone,
                activeQuests = qActiveOrReady,
                totalQuests = #dQuests,
            }
        end
    end

    table.sort(list, function(a, b)
        if a.isNearLevel ~= b.isNearLevel then
            return a.isNearLevel
        end
        if a.distanceFromPlayer ~= b.distanceFromPlayer then
            return a.distanceFromPlayer < b.distanceFromPlayer
        end
        return (a.recommendedLevel or 0) < (b.recommendedLevel or 0)
    end)

    dungeonsViewCache[cacheKey] = list
    return list
end

function Scorer.GetSpecialRewards(player, filterKind, limit, includeAllLevels)
    player = player or ns.State.GetPlayerInfo()
    filterKind = filterKind or "all"
    limit = limit or 20
    local level = player.level or 24
    local classFile = player.classFile or "ROGUE"
    local classLabel = player.className or "Rogue"
    local gen = ns.State.GetRefreshGeneration and ns.State.GetRefreshGeneration() or 0
    local cacheKey = string.format("%d:%s:%d:%s:%d:%s:%s:%s:%d:%s",
        gen,
        tostring(filterKind),
        tonumber(limit) or 20,
        tostring(includeAllLevels == true),
        level,
        tostring(player.hasSkinning == true),
        tostring(player.hasLeatherworking == true),
        tostring(player.faction or "Alliance"),
        tonumber(player.raceId) or 0,
        tostring(classFile))
    if rewardsViewCache[cacheKey] then
        return rewardsViewCache[cacheKey]
    end

    local data = ns.GetData()
    local idx = EnsureIndexes(data)
    local candidateIds = {}
    local seen = {}

    local function AppendIds(idList)
        if type(idList) ~= "table" then return end
        for _, qid in ipairs(idList) do
            if not seen[qid] then
                seen[qid] = true
                candidateIds[#candidateIds + 1] = qid
            end
        end
    end

    if filterKind == "bags" then
        AppendIds(idx.bagQuestIds)
    elseif filterKind == "gold" then
        AppendIds(idx.goldQuestIds)
    elseif filterKind == "rogue" or filterKind == "bis" or filterKind == "class" then
        AppendIds(idx.classRewardIds)
        if classFile == "ROGUE" then
            AppendIds(idx.rogueQuestIds)
        end
    elseif filterKind == "skinning" then
        AppendIds(idx.skinningQuestIds)
    elseif filterKind == "lw" then
        AppendIds(idx.lwQuestIds)
    elseif filterKind == "skinning_lw" or filterKind == "professions" then
        AppendIds(idx.lwQuestIds)
        AppendIds(idx.skinningQuestIds)
    else
        AppendIds(idx.specialRewardIds)
        AppendIds(idx.classRewardIds)
        AppendIds(idx.lwQuestIds)
        AppendIds(idx.skinningQuestIds)
    end

    local results = {}
    for _, qid in ipairs(candidateIds) do
        local q = data.quests and data.quests[qid]
        if q and ns.State.IsEligibleForPlayer(qid, player)
            and not ns.State.IsQuestCompleted(qid, true)
            and not ns.State.IsQuestExcluded(qid, true) then
            local qL = q.questLevel or q.minLevel or level
            local minL = q.minLevel or 1
            local inLevelBand = includeAllLevels or (minL <= level + 5 and qL >= level - 10)
            if inLevelBand then
                local bags = tonumber(q.bagSlots) or 0
                local gold = tonumber(q.goldCopper) or 0
                local classScore = ns.GetQuestClassScore(q, classFile)
                local rogue = (classFile == "ROGUE") and classScore or (tonumber(q.rogueScore) or 0)
                local skinScore = tonumber(q.skinningScore or (q.features and q.features.skinningValue)) or 0
                local reqId = type(q.requiredSkill) == "table" and tonumber(q.requiredSkill.skillId or q.requiredSkill[1]) or nil

                local isBag = bags > 0
                local isGold = gold >= 4000
                local isClassBiS = classScore >= 30
                local isLW = (q.isLeatherworking == true) or (type(q.lwRewards) == "table" and #q.lwRewards > 0) or (reqId == 165)
                local isSkin = (q.isSkinning == true) or (skinScore >= 30) or (reqId == 393)

                local matchesFilter = (filterKind == "all" and (isBag or isGold or isClassBiS or isLW or isSkin))
                    or (filterKind == "bags" and isBag)
                    or (filterKind == "gold" and isGold)
                    or ((filterKind == "rogue" or filterKind == "bis" or filterKind == "class") and isClassBiS)
                    or (filterKind == "skinning" and isSkin)
                    or (filterKind == "lw" and isLW)
                    or ((filterKind == "skinning_lw" or filterKind == "professions") and (isLW or isSkin))

                if matchesFilter then
                    local valueScore = (isBag and (60 + bags * 4) or 0)
                        + (classScore * 1.1)
                        + math.min(60, gold / 300.0)
                        + (isLW and 55 or 0)
                        + (skinScore * 0.65)
                        - math.abs(qL - level) * 2.5

                    local pt = Scorer.GetQuestPoint(q, "ACCEPT")
                    local zId = (pt and pt.zoneId) or q.zoneId
                    local zInfo = zId and data.zones and data.zones[zId]

                    results[#results + 1] = {
                        questId = qid,
                        name = q.name or ("Quest #" .. tostring(qid)),
                        questLevel = qL,
                        minLevel = minL,
                        zoneId = zId,
                        zoneName = (zInfo and zInfo.name) or q.zoneName or "Unknown",
                        x = pt and pt.x or nil,
                        y = pt and pt.y or nil,
                        npcName = q.starter and q.starter.name or nil,
                        bagSlots = bags,
                        goldCopper = gold,
                        classScore = classScore,
                        classLabel = classLabel,
                        rogueScore = rogue,
                        skinningScore = skinScore,
                        isLeatherworking = isLW,
                        isSkinning = isSkin,
                        lwRewards = q.lwRewards or {},
                        requiredSkill = q.requiredSkill,
                        specialRewards = q.specialRewards or {},
                        status = ns.State.GetQuestStatus(qid, true),
                        prereqsMet = ns.State.ArePrereqsMet(qid, nil, true),
                        isDungeon = q.isDungeon == true,
                        valueScore = valueScore,
                    }
                end
            end
        end
    end

    table.sort(results, function(a, b)
        if math.abs(a.valueScore - b.valueScore) > 0.001 then
            return a.valueScore > b.valueScore
        end
        return (a.questLevel or 0) < (b.questLevel or 0)
    end)

    if #results > limit then
        local trimmed = {}
        for i = 1, limit do
            trimmed[i] = results[i]
        end
        results = trimmed
    end

    rewardsViewCache[cacheKey] = results
    return results
end

function Scorer.Initialize()
    EnsureIndexes(ns.GetData())
    ns.RegisterCallback("STATE_UPDATED", function(reason)
        if ns.State and ns.State.IsLocationOnlyReason and ns.State.IsLocationOnlyReason(reason) then
            return
        end
        dynamicStepsCache.valid = false
        -- Retain cached indexes; generation key automatically isolates stale view models.
        -- Keep bounded table size if sessions run for many hours.
        local count = 0
        for _ in pairs(rewardsViewCache) do count = count + 1 end
        if count > 40 then rewardsViewCache = {} end
        count = 0
        for _ in pairs(dungeonsViewCache) do count = count + 1 end
        if count > 20 then dungeonsViewCache = {} end
    end)
end
