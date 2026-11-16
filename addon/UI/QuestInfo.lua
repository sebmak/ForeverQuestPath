--------------------------------------------------------------------------------
-- UI/QuestInfo.lua
-- Rules:
--   1. Shared Quest Info Tooltip & Quest Log Navigation helper for
--      ForeverQuestPath HUD and Route/Dungeons/Rewards/Diagnostics windows.
--   2. Aggregates quest instructions, narrative text, live objective progress,
--      starter/turn-in NPC coordinates, batch dungeon quests, chain progress,
--      and rewards from GeneratedData, active C_QuestLog / GetQuestLogQuestText
--      / GetQuestLogLeaderBoard APIs, and optional QuestieDB via pcall without
--      ever mutating Quest Log selection on hover or allocating frames/regions.
--   3. Left-clicking a quest opens the Blizzard/Classic/Forever Quest Log to
--      that quest (respecting controller InputUtil.IsGamepadUIEnabled and
--      InCombatLockdown guards, supporting Shift-Click chat links, and opening
--      the Quest Log + pinning the quest giver cleanly when a quest is not in
--      the active Quest Log yet).
--------------------------------------------------------------------------------

local _, ns = ...

local QuestInfo = {}
ns.QuestInfo = QuestInfo

local MAX_TOOLTIP_OBJECTIVES = 8
local MAX_TOOLTIP_BATCH_QUESTS = 8
local MAX_TOOLTIP_REWARDS = 4
local MAX_DESC_CHARS = 380

local DEFAULT_TITLE_COLOR = { 1.00, 0.82, 0.00 }
local DEFAULT_TEXT_COLOR  = { 0.92, 0.93, 0.95 }
local DEFAULT_SUB_COLOR   = { 0.68, 0.78, 0.88 }
local DEFAULT_DONE_COLOR  = { 0.30, 0.95, 0.45 }
local DEFAULT_WARN_COLOR  = { 1.00, 0.42, 0.30 }
local DEFAULT_HINT_COLOR  = { 0.58, 0.62, 0.68 }

local dedupObjectivesSeen = {}
local scratchObjectivesList = {}
local scratchInstructionsList = {}
local scratchBatchList = {}
local scratchRewardsList = {}

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

local function StripColorCodes(text)
    if type(text) ~= "string" then
        return ""
    end
    local clean = text:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
    return clean
end

local function TrimText(text)
    if type(text) ~= "string" then
        return ""
    end
    local s = text:gsub("^%s+", ""):gsub("%s+$", "")
    return s
end

local function TruncateText(text, maxLen)
    local s = TrimText(text)
    if s == "" then
        return ""
    end
    local cap = maxLen or MAX_DESC_CHARS
    if #s <= cap then
        return s
    end
    return s:sub(1, cap - 3) .. "..."
end

local function GetZoneName(zoneId, fallbackName, data)
    if zoneId and data and data.zones and data.zones[zoneId] and data.zones[zoneId].name then
        return data.zones[zoneId].name
    end
    if zoneId and C_Map and C_Map.GetAreaInfo then
        local ok, areaName = pcall(C_Map.GetAreaInfo, zoneId)
        if ok and type(areaName) == "string" and areaName ~= "" then
            return areaName
        end
    end
    if type(fallbackName) == "string" and fallbackName ~= "" then
        return fallbackName
    end
    return nil
end

local function FormatNpcPoint(npcName, zoneId, fallbackZone, x, y, data)
    local parts = {}
    if type(npcName) == "string" and npcName ~= "" then
        parts[#parts + 1] = npcName
    end
    local zName = GetZoneName(zoneId, fallbackZone, data)
    if zName and zName ~= "" then
        parts[#parts + 1] = zName
    end
    if type(x) == "number" and type(y) == "number" and ns.FormatCoords then
        parts[#parts + 1] = "(" .. ns.FormatCoords(x, y) .. ")"
    end
    return table.concat(parts, " · ")
end

local function WipeArray(arr)
    for i = #arr, 1, -1 do
        arr[i] = nil
    end
end

local function WipeMap(t)
    for k in pairs(t) do
        t[k] = nil
    end
end

function QuestInfo.FindQuestLogIndex(questId)
    local qid = tonumber(questId)
    if not qid or qid <= 0 then
        return nil
    end
    if C_QuestLog and C_QuestLog.GetLogIndexForQuestID then
        local ok, idx = pcall(C_QuestLog.GetLogIndexForQuestID, qid)
        idx = ok and tonumber(idx) or nil
        if idx and idx > 0 then
            if C_QuestLog.GetInfo then
                local okI, info = pcall(C_QuestLog.GetInfo, idx)
                if okI and info and not info.isHeader and tonumber(info.questID) == qid then
                    return idx
                end
            else
                return idx
            end
        end
    end
    if C_QuestLog and C_QuestLog.GetNumQuestLogEntries and C_QuestLog.GetInfo then
        local okN, numEntries = pcall(C_QuestLog.GetNumQuestLogEntries)
        numEntries = okN and tonumber(numEntries) or 0
        for i = 1, numEntries do
            local okI, info = pcall(C_QuestLog.GetInfo, i)
            if okI and info and not info.isHeader and tonumber(info.questID) == qid then
                return i
            end
        end
    elseif GetNumQuestLogEntries and GetQuestLogTitle then
        local okN, numEntries = pcall(GetNumQuestLogEntries)
        numEntries = okN and tonumber(numEntries) or 0
        for i = 1, numEntries do
            local _, _, _, isHeader, _, _, _, entryQuestId = GetQuestLogTitle(i)
            if (not isHeader) and tonumber(entryQuestId) == qid then
                return i
            end
        end
    end
    return nil
end

function QuestInfo.ResolvePrimaryQuestId(questId, step)
    local explicit = tonumber(questId)
    if explicit and explicit > 0 then
        return explicit
    end
    if type(step) == "table" then
        if type(step.questIds) == "table" and #step.questIds > 0 then
            for i = 1, #step.questIds do
                local cand = tonumber(step.questIds[i])
                if cand and cand > 0 and QuestInfo.FindQuestLogIndex(cand) then
                    return cand
                end
            end
            for i = 1, #step.questIds do
                local cand = tonumber(step.questIds[i])
                if cand and cand > 0 then
                    local isDone = ns.State and ns.State.IsQuestCompleted and ns.State.IsQuestCompleted(cand, true)
                    if not isDone then
                        return cand
                    end
                end
            end
            local firstId = tonumber(step.questIds[1])
            if firstId and firstId > 0 then
                return firstId
            end
        end
        local stepQid = tonumber(step.questId)
        if stepQid and stepQid > 0 then
            return stepQid
        end
    end
    return nil
end

local function QueryQuestTitle(qid, q, step, qdb)
    if C_QuestLog and C_QuestLog.GetTitleForQuestID and qid then
        local ok, cTitle = pcall(C_QuestLog.GetTitleForQuestID, qid)
        if ok and type(cTitle) == "string" and cTitle ~= "" then
            return cTitle
        end
    end
    if q and type(q.name) == "string" and q.name ~= "" then
        return q.name
    end
    if qdb and qdb.QueryQuestSingle and qid then
        local ok, qName = pcall(qdb.QueryQuestSingle, qid, "name")
        if ok and type(qName) == "string" and qName ~= "" then
            return qName
        end
    end
    if step and type(step.title) == "string" and step.title ~= "" then
        return step.title
    end
    if qid then
        return "Quest #" .. tostring(qid)
    end
    return "Quest Step"
end

local function ResolveQuestStatus(qid, logIndex)
    if qid and ns.State and ns.State.GetQuestStatus then
        local st = ns.State.GetQuestStatus(qid, true)
        if st then
            return st
        end
    end
    if logIndex then
        if C_QuestLog and C_QuestLog.IsComplete and qid then
            local ok, done = pcall(C_QuestLog.IsComplete, qid)
            if ok and done == true then
                return "ready"
            end
        end
        return "active"
    end
    if qid and C_QuestLog and C_QuestLog.IsQuestFlaggedCompleted then
        local ok, comp = pcall(C_QuestLog.IsQuestFlaggedCompleted, qid)
        if ok and comp == true then
            return "completed"
        end
    end
    return "available"
end

local function AppendUniqueInstruction(outList, seenMap, text)
    local clean = TrimText(text)
    if clean == "" then
        return
    end
    local key = string.lower(clean)
    if not seenMap[key] then
        seenMap[key] = true
        outList[#outList + 1] = clean
    end
end

local function CollectLiveObjectives(qid, logIndex, q, data, outList)
    local count = 0
    if C_QuestLog and C_QuestLog.GetQuestObjectives and qid then
        local ok, objs = pcall(C_QuestLog.GetQuestObjectives, qid)
        if ok and type(objs) == "table" and #objs > 0 then
            for i = 1, math.min(#objs, MAX_TOOLTIP_OBJECTIVES) do
                local obj = objs[i]
                if type(obj) == "table" and type(obj.text) == "string" and obj.text ~= "" then
                    local coordHint = nil
                    if q and type(q.objectives) == "table" and q.objectives[i] then
                        local qo = q.objectives[i]
                        if qo.x and qo.y then
                            local zName = GetZoneName(qo.zoneId, q.zoneName, data)
                            coordHint = zName and string.format("%s (%s)", zName, ns.FormatCoords(qo.x, qo.y))
                                or ns.FormatCoords(qo.x, qo.y)
                        end
                    end
                    count = count + 1
                    outList[count] = {
                        text = obj.text,
                        finished = obj.finished == true,
                        coordHint = coordHint,
                    }
                end
            end
            if count > 0 then
                return count
            end
        end
    end
    if logIndex and GetNumQuestLeaderBoards and GetQuestLogLeaderBoard then
        local okN, numLB = pcall(GetNumQuestLeaderBoards, logIndex)
        numLB = okN and tonumber(numLB) or 0
        for i = 1, math.min(numLB, MAX_TOOLTIP_OBJECTIVES) do
            local okL, text, _, finished = pcall(GetQuestLogLeaderBoard, i, logIndex)
            if okL and type(text) == "string" and text ~= "" then
                count = count + 1
                outList[count] = {
                    text = text,
                    finished = finished == true,
                }
            end
        end
    end
    return count
end

local function BuildDungeonTooltipModel(d, ctx, data)
    WipeArray(scratchBatchList)
    local questsMap = data and data.quests or {}
    local seenQ = {}

    for _, ch in ipairs(d.unlockChains or {}) do
        for _, cs in ipairs(ch.steps or {}) do
            if #scratchBatchList >= MAX_TOOLTIP_BATCH_QUESTS then break end
            if cs.questId and not seenQ[cs.questId] then
                seenQ[cs.questId] = true
                local q = questsMap[cs.questId]
                scratchBatchList[#scratchBatchList + 1] = {
                    questId = cs.questId,
                    name = cs.name or (q and q.name) or ("Quest #" .. tostring(cs.questId)),
                    level = cs.questLevel or (q and q.questLevel) or d.recommendedLevel,
                    status = cs.status or ResolveQuestStatus(cs.questId, QuestInfo.FindQuestLogIndex(cs.questId)),
                    tag = "Unlock",
                }
            end
        end
    end
    for _, dq in ipairs(d.quests or {}) do
        if #scratchBatchList >= MAX_TOOLTIP_BATCH_QUESTS then break end
        if dq.questId and not seenQ[dq.questId] then
            seenQ[dq.questId] = true
            local q = questsMap[dq.questId]
            scratchBatchList[#scratchBatchList + 1] = {
                questId = dq.questId,
                name = dq.name or (q and q.name) or ("Quest #" .. tostring(dq.questId)),
                level = dq.questLevel or (q and q.questLevel) or d.recommendedLevel,
                status = dq.status or ResolveQuestStatus(dq.questId, QuestInfo.FindQuestLogIndex(dq.questId)),
            }
        end
    end

    local entranceStr = d.entranceZoneName or ""
    if d.entranceCoords and d.entranceCoords.x and d.entranceCoords.y and ns.FormatCoords then
        local c = ns.FormatCoords(d.entranceCoords.x, d.entranceCoords.y)
        entranceStr = entranceStr ~= "" and (entranceStr .. " (" .. c .. ")") or c
    end

    return {
        kind = "dungeon",
        title = d.name or "Dungeon",
        levelRange = string.format("Lv %d-%d (Rec %d)", tonumber(d.minLevel) or 1, tonumber(d.maxLevel) or 60, tonumber(d.recommendedLevel) or 1),
        progressLine = string.format("%d/%d quests completed", tonumber(d.completedQuests) or 0, tonumber(d.totalQuests) or 0),
        entranceLine = entranceStr,
        chains = d.unlockChains or {},
        quests = scratchBatchList,
        rightClickHint = ctx and ctx.rightClickHint or nil,
        leftClickHint = "Click: Open active dungeon quest in Quest Log",
    }
end

function QuestInfo.BuildTooltipModel(ctx)
    if type(ctx) ~= "table" then
        return nil
    end
    local data = ns.GetData and ns.GetData() or {}
    if ctx.dungeon and not ctx.questId and not ctx.step then
        return BuildDungeonTooltipModel(ctx.dungeon, ctx, data)
    end

    local step = ctx.step
    local qid = QuestInfo.ResolvePrimaryQuestId(ctx.questId, step)
    local q = qid and data.quests and data.quests[qid] or nil
    local qdb = GetQuestieDB()
    local isBatch = (type(step) == "table" and type(step.questIds) == "table" and #step.questIds > 1)
    local logIndex = qid and QuestInfo.FindQuestLogIndex(qid) or nil
    local status = qid and ResolveQuestStatus(qid, logIndex) or "available"
    local activeEntry = (qid and ns.State and ns.State.GetQuestLogEntry) and ns.State.GetQuestLogEntry(qid) or nil

    local title = QueryQuestTitle(qid, q, step, qdb)
    local qLevel = (q and tonumber(q.questLevel))
        or (activeEntry and tonumber(activeEntry.level))
        or (step and tonumber(step.questLevel))
        or nil
    local minLevel = (q and tonumber(q.minLevel)) or nil
    if (not qLevel or not minLevel) and qdb and qdb.QueryQuestSingle and qid then
        if not qLevel then
            local okL, rLvl = pcall(qdb.QueryQuestSingle, qid, "questLevel")
            if okL and tonumber(rLvl) and tonumber(rLvl) > 0 then
                qLevel = tonumber(rLvl)
            end
        end
        if not minLevel then
            local okM, rMin = pcall(qdb.QueryQuestSingle, qid, "requiredLevel")
            if okM and tonumber(rMin) and tonumber(rMin) > 0 then
                minLevel = tonumber(rMin)
            end
        end
    end

    local isDungeon = (step and (step.isDungeon == true or step.action == "DUNGEON_PREP" or step.action == "DUNGEON_RUN"))
        or (q and q.isDungeon == true)
        or (qid and ns.State and ns.State.IsQuestDungeon and ns.State.IsQuestDungeon(qid, q))
        or false
    local isElite = (not isDungeon) and (
        (step and (step.isElite == true or step.isGroup == true or (step.badges and step.badges.isElite)))
        or (q and q.isGroup == true)
        or (qid and ns.State and ns.State.IsQuestElite and ns.State.IsQuestElite(qid, q))
    ) or false

    local tags = {}
    if qLevel and qLevel > 0 then
        if minLevel and minLevel > 0 and minLevel < qLevel then
            tags[#tags + 1] = string.format("Lv %d (Min %d)", qLevel, minLevel)
        else
            tags[#tags + 1] = string.format("Lv %d", qLevel)
        end
    elseif minLevel and minLevel > 0 then
        tags[#tags + 1] = string.format("Min Lv %d", minLevel)
    end
    if isDungeon then tags[#tags + 1] = "Dungeon" end
    if isElite then tags[#tags + 1] = "Elite" end
    if qid then tags[#tags + 1] = "ID " .. tostring(qid) end

    local statusLabel, statusKind
    if ctx.blockReason and ctx.blockReason ~= "" then
        statusLabel = "Locked: " .. tostring(ctx.blockReason)
        statusKind = "blocked"
    elseif status == "ready" then
        statusLabel = "Ready to Turn In"
        statusKind = "ready"
    elseif status == "active" then
        statusLabel = "In Quest Log"
        statusKind = "active"
    elseif status == "completed" then
        statusLabel = "Completed"
        statusKind = "completed"
    else
        statusLabel = "Available (Not in Quest Log)"
        statusKind = "available"
    end

    local actionLine = nil
    if step and step.action and ns.GetActionMeta then
        local am = ns.GetActionMeta(step.action)
        local targetWhere = FormatNpcPoint(step.npcName, step.zoneId, step.zoneName, step.x, step.y, data)
        if targetWhere ~= "" then
            actionLine = am.label .. ": " .. targetWhere
        else
            actionLine = am.label
        end
    end

    local startLine = nil
    if q and q.starter then
        local sStr = FormatNpcPoint(q.starter.name, q.starter.zoneId or q.zoneId, q.zoneName, q.starter.x, q.starter.y, data)
        if sStr ~= "" then
            startLine = "Start: " .. sStr
        end
    end
    local turninLine = nil
    if q and q.turnin then
        local tStr = FormatNpcPoint(q.turnin.name, q.turnin.zoneId or q.zoneId, q.zoneName, q.turnin.x, q.turnin.y, data)
        if tStr ~= "" then
            turninLine = "Turn In: " .. tStr
        end
    elseif q and q.starter and q.starter.kind == "npc" and not q.turnin then
        local tStr = FormatNpcPoint(q.starter.name, q.starter.zoneId or q.zoneId, q.zoneName, q.starter.x, q.starter.y, data)
        if tStr ~= "" then
            turninLine = "Turn In: " .. tStr
        end
    end

    WipeMap(dedupObjectivesSeen)
    WipeArray(scratchInstructionsList)
    local descriptionText = nil

    if qdb and qdb.QueryQuestSingle and qid then
        local okOT, objText = pcall(qdb.QueryQuestSingle, qid, "objectivesText")
        if okOT then
            if type(objText) == "table" then
                for _, line in ipairs(objText) do
                    AppendUniqueInstruction(scratchInstructionsList, dedupObjectivesSeen, line)
                end
            elseif type(objText) == "string" then
                AppendUniqueInstruction(scratchInstructionsList, dedupObjectivesSeen, objText)
            end
        end
        local okTrig, trig = pcall(qdb.QueryQuestSingle, qid, "triggerEnd")
        if okTrig and type(trig) == "table" and type(trig[1]) == "string" then
            AppendUniqueInstruction(scratchInstructionsList, dedupObjectivesSeen, trig[1])
        end
    end

    if logIndex and GetQuestLogQuestText then
        local safeIndex = true
        if C_QuestLog and C_QuestLog.GetInfo then
            local okI, info = pcall(C_QuestLog.GetInfo, logIndex)
            if not (okI and info and tonumber(info.questID) == qid) then
                safeIndex = false
            end
        end
        if safeIndex then
            local okQT, qDesc, qObjText = pcall(GetQuestLogQuestText, logIndex)
            if okQT then
                if type(qObjText) == "string" and TrimText(qObjText) ~= "" then
                    AppendUniqueInstruction(scratchInstructionsList, dedupObjectivesSeen, qObjText)
                end
                if type(qDesc) == "string" and TrimText(qDesc) ~= "" then
                    local truncDesc = TruncateText(qDesc, MAX_DESC_CHARS)
                    local dKey = string.lower(truncDesc)
                    if not dedupObjectivesSeen[dKey] then
                        descriptionText = truncDesc
                    end
                end
            end
        end
    end

    WipeArray(scratchObjectivesList)
    local objCount = 0
    if logIndex then
        objCount = CollectLiveObjectives(qid, logIndex, q, data, scratchObjectivesList)
        if objCount == 0 and status == "ready" then
            objCount = 1
            scratchObjectivesList[1] = {
                text = "Ready for turn-in",
                finished = true,
            }
        end
    elseif (not isBatch) and q and type(q.objectives) == "table" and #q.objectives > 0 then
        WipeMap(dedupObjectivesSeen)
        for _, qo in ipairs(q.objectives) do
            if objCount >= MAX_TOOLTIP_OBJECTIVES then break end
            if type(qo) == "table" then
                local lbl = TrimText(qo.label or "Objective")
                local zName = GetZoneName(qo.zoneId, q.zoneName, data)
                local cStr = (qo.x and qo.y and ns.FormatCoords) and ns.FormatCoords(qo.x, qo.y) or nil
                local coordPart = (zName and cStr) and (zName .. " (" .. cStr .. ")") or (cStr and ("(" .. cStr .. ")") or zName)
                local key = string.format("%s|%s|%s", string.lower(lbl), tostring(zName or ""), tostring(cStr or ""))
                if not dedupObjectivesSeen[key] then
                    dedupObjectivesSeen[key] = true
                    objCount = objCount + 1
                    scratchObjectivesList[objCount] = {
                        text = lbl,
                        coordHint = coordPart,
                        finished = false,
                    }
                end
            end
        end
    elseif (not isBatch) and q and q.turnin and q.turnin.name then
        local targetStr = FormatNpcPoint(q.turnin.name, q.turnin.zoneId or q.zoneId, q.zoneName, q.turnin.x, q.turnin.y, data)
        if targetStr ~= "" then
            objCount = 1
            scratchObjectivesList[1] = {
                text = "Speak with / Deliver to " .. targetStr,
                finished = false,
            }
        end
    end

    WipeArray(scratchBatchList)
    if isBatch then
        for i = 1, math.min(#step.questIds, MAX_TOOLTIP_BATCH_QUESTS) do
            local bqid = tonumber(step.questIds[i])
            if bqid then
                local bq = data.quests and data.quests[bqid]
                local bLog = QuestInfo.FindQuestLogIndex(bqid)
                local bStatus = ResolveQuestStatus(bqid, bLog)
                local bObj = (bq and bq.objectives and bq.objectives[1] and bq.objectives[1].label) or nil
                if (not bObj) and qdb and qdb.QueryQuestSingle then
                    local okB, bTxt = pcall(qdb.QueryQuestSingle, bqid, "objectivesText")
                    if okB and type(bTxt) == "table" then
                        bObj = bTxt[1]
                    elseif okB and type(bTxt) == "string" then
                        bObj = bTxt
                    end
                end
                scratchBatchList[#scratchBatchList + 1] = {
                    questId = bqid,
                    name = QueryQuestTitle(bqid, bq, nil, qdb),
                    level = bq and bq.questLevel or nil,
                    status = bStatus,
                    objective = bObj,
                }
            end
        end
    end

    local chainLine = nil
    local ch = (step and step.badges and step.badges.chain)
        or (step and step.chainTotal and step.chainTotal >= 2 and {
            pos = step.chainPos or step.chainStep,
            total = step.chainTotal,
            remainingQuests = step.chainRemainingQuests,
            remainingSteps = step.chainRemainingSteps,
        })
        or (qid and ns.State and ns.State.GetChainProgressInfo and ns.State.GetChainProgressInfo(qid, nil, nil, step and step.action))
    if ch and (ch.total or 0) >= 2 then
        local rq = tonumber(ch.remainingQuests) or 0
        local rs = tonumber(ch.remainingSteps) or 0
        if rq > 0 or rs > 0 then
            chainLine = string.format("Chain %d/%d (%d quests, %d steps left)", ch.pos or 1, ch.total, rq, rs)
        else
            chainLine = string.format("Chain %d/%d (final step)", ch.pos or ch.total, ch.total)
        end
    end

    WipeArray(scratchRewardsList)
    local xpVal = (q and tonumber(q.xp)) or (step and step.badges and tonumber(step.badges.xp)) or 0
    local goldVal = (q and tonumber(q.goldCopper)) or (step and step.badges and tonumber(step.badges.goldCopper)) or 0
    local bagVal = (q and tonumber(q.bagSlots)) or (step and step.badges and tonumber(step.badges.bagSlots)) or 0
    local unlocksDung = (q and q.unlocksDungeon) or (step and step.badges and step.badges.unlocksDungeon) or nil
    if q and type(q.specialRewards) == "table" then
        for i = 1, math.min(#q.specialRewards, MAX_TOOLTIP_REWARDS) do
            local rw = q.specialRewards[i]
            if type(rw) == "table" and rw.name and rw.name ~= "" then
                scratchRewardsList[#scratchRewardsList + 1] = {
                    name = rw.name,
                    quality = tonumber(rw.quality) or 1,
                }
            end
        end
    end

    local leftHint
    if logIndex then
        leftHint = "Click: Open in Quest Log"
    elseif status == "completed" then
        leftHint = "Click: Open Quest Log (Already Completed)"
    elseif qid then
        leftHint = "Click: Open Quest Log & Pin Quest Giver"
    end

    return {
        kind = "quest",
        questId = qid,
        title = title,
        metaLine = table.concat(tags, "  ·  "),
        statusLabel = statusLabel,
        statusKind = statusKind,
        inLog = (logIndex ~= nil),
        actionLine = actionLine,
        startLine = startLine,
        turninLine = turninLine,
        travelNote = step and step.travelNote or nil,
        whyText = step and step.why or nil,
        instructions = scratchInstructionsList,
        descriptionText = descriptionText,
        objectives = scratchObjectivesList,
        batchQuests = scratchBatchList,
        totalBatchCount = isBatch and #step.questIds or 0,
        chainLine = chainLine,
        xp = xpVal,
        goldCopper = goldVal,
        bagSlots = bagVal,
        unlocksDungeon = unlocksDung,
        rewardItems = scratchRewardsList,
        leftClickHint = leftHint,
        rightClickHint = ctx.rightClickHint,
    }
end

local function ChooseAnchor(owner)
    if not owner then
        return "ANCHOR_RIGHT"
    end
    local cx = owner.GetCenter and owner:GetCenter() or nil
    local sw = (UIParent and UIParent.GetWidth and UIParent:GetWidth()) or 1024
    if cx and cx > (sw * 0.5) then
        return "ANCHOR_LEFT"
    end
    return "ANCHOR_RIGHT"
end

local function GetStatusColor(kind)
    if kind == "ready" or kind == "completed" then
        return DEFAULT_DONE_COLOR[1], DEFAULT_DONE_COLOR[2], DEFAULT_DONE_COLOR[3]
    elseif kind == "blocked" then
        return DEFAULT_WARN_COLOR[1], DEFAULT_WARN_COLOR[2], DEFAULT_WARN_COLOR[3]
    elseif kind == "active" then
        return 0.48, 0.86, 1.00
    end
    return 1.00, 0.85, 0.35
end

local function RenderDungeonTooltip(tt, vm)
    tt:AddDoubleLine(vm.title, vm.levelRange, DEFAULT_TITLE_COLOR[1], DEFAULT_TITLE_COLOR[2], DEFAULT_TITLE_COLOR[3], 0.72, 0.80, 0.90)
    tt:AddLine(vm.progressLine, DEFAULT_DONE_COLOR[1], DEFAULT_DONE_COLOR[2], DEFAULT_DONE_COLOR[3], true)
    if vm.entranceLine and vm.entranceLine ~= "" then
        tt:AddLine("Entrance: " .. vm.entranceLine, DEFAULT_SUB_COLOR[1], DEFAULT_SUB_COLOR[2], DEFAULT_SUB_COLOR[3], true)
    end
    if vm.chains and #vm.chains > 0 then
        tt:AddLine(" ")
        tt:AddLine("Unlock Chains:", 1.00, 0.75, 0.35)
        for _, ch in ipairs(vm.chains) do
            tt:AddLine(string.format("• %s (%d/%d)", ch.chainName or "Chain", ch.doneCount or 0, ch.totalCount or 0), DEFAULT_TEXT_COLOR[1], DEFAULT_TEXT_COLOR[2], DEFAULT_TEXT_COLOR[3], true)
        end
    end
    if vm.quests and #vm.quests > 0 then
        tt:AddLine(" ")
        tt:AddLine("Dungeon Quests:", 1.00, 0.82, 0.35)
        for _, bq in ipairs(vm.quests) do
            local tagText = (bq.status == "ready" and "[Ready]")
                or (bq.status == "active" and "[In Log]")
                or (bq.status == "completed" and "[Done]")
                or "[Open]"
            local lr, lg, lb = GetStatusColor(bq.status)
            tt:AddDoubleLine(
                string.format("• %s%s [%d]", bq.tag and ("(" .. bq.tag .. ") ") or "", bq.name or "Quest", tonumber(bq.level) or 0),
                tagText,
                DEFAULT_TEXT_COLOR[1], DEFAULT_TEXT_COLOR[2], DEFAULT_TEXT_COLOR[3],
                lr, lg, lb
            )
        end
    end
    if (vm.leftClickHint and vm.leftClickHint ~= "") or (vm.rightClickHint and vm.rightClickHint ~= "") then
        tt:AddLine(" ")
        if vm.leftClickHint and vm.leftClickHint ~= "" then
            tt:AddLine(vm.leftClickHint, DEFAULT_HINT_COLOR[1], DEFAULT_HINT_COLOR[2], DEFAULT_HINT_COLOR[3], true)
        end
        if vm.rightClickHint and vm.rightClickHint ~= "" then
            tt:AddLine(vm.rightClickHint, DEFAULT_HINT_COLOR[1], DEFAULT_HINT_COLOR[2], DEFAULT_HINT_COLOR[3], true)
        end
    end
end

local function RenderQuestTooltip(tt, vm)
    tt:AddLine(vm.title, DEFAULT_TITLE_COLOR[1], DEFAULT_TITLE_COLOR[2], DEFAULT_TITLE_COLOR[3], true)
    if vm.metaLine and vm.metaLine ~= "" then
        tt:AddLine(vm.metaLine, DEFAULT_SUB_COLOR[1], DEFAULT_SUB_COLOR[2], DEFAULT_SUB_COLOR[3], true)
    end
    local sr, sg, sb = GetStatusColor(vm.statusKind)
    tt:AddLine(vm.statusLabel, sr, sg, sb, true)

    local hasWhere = (vm.actionLine and vm.actionLine ~= "")
        or (vm.startLine and vm.startLine ~= "")
        or (vm.turninLine and vm.turninLine ~= "")
        or (vm.travelNote and vm.travelNote ~= "")
    if hasWhere then
        tt:AddLine(" ")
        if vm.actionLine and vm.actionLine ~= "" then
            tt:AddLine(vm.actionLine, DEFAULT_TEXT_COLOR[1], DEFAULT_TEXT_COLOR[2], DEFAULT_TEXT_COLOR[3], true)
        end
        if vm.startLine and vm.startLine ~= "" and vm.startLine ~= vm.actionLine then
            tt:AddLine(vm.startLine, 0.78, 0.88, 0.96, true)
        end
        if vm.turninLine and vm.turninLine ~= "" and vm.turninLine ~= vm.actionLine and vm.turninLine ~= vm.startLine then
            tt:AddLine(vm.turninLine, 0.55, 0.95, 0.85, true)
        end
        if vm.travelNote and vm.travelNote ~= "" then
            tt:AddLine("Route: " .. vm.travelNote, 0.45, 0.75, 1.00, true)
        end
    end

    if (vm.instructions and #vm.instructions > 0) or (vm.descriptionText and vm.descriptionText ~= "") then
        tt:AddLine(" ")
        tt:AddLine("Quest Instructions:", 1.00, 0.82, 0.30)
        for i = 1, #vm.instructions do
            tt:AddLine(vm.instructions[i], DEFAULT_TEXT_COLOR[1], DEFAULT_TEXT_COLOR[2], DEFAULT_TEXT_COLOR[3], true)
        end
        if vm.descriptionText and vm.descriptionText ~= "" then
            tt:AddLine(vm.descriptionText, 0.80, 0.82, 0.86, true)
        end
    end

    if vm.objectives and #vm.objectives > 0 then
        tt:AddLine(" ")
        tt:AddLine(vm.inLog and "Progress & Objectives:" or "Objectives:", 0.40, 0.85, 1.00)
        for i = 1, #vm.objectives do
            local obj = vm.objectives[i]
            local lineText = (obj.finished and "✓ " or "• ") .. tostring(obj.text or "")
            if obj.coordHint and obj.coordHint ~= "" then
                lineText = lineText .. " — " .. obj.coordHint
            end
            if obj.finished then
                tt:AddLine(lineText, DEFAULT_DONE_COLOR[1], DEFAULT_DONE_COLOR[2], DEFAULT_DONE_COLOR[3], true)
            else
                tt:AddLine(lineText, DEFAULT_TEXT_COLOR[1], DEFAULT_TEXT_COLOR[2], DEFAULT_TEXT_COLOR[3], true)
            end
        end
    end

    if vm.batchQuests and #vm.batchQuests > 0 then
        tt:AddLine(" ")
        tt:AddLine(string.format("Dungeon Batch (%d Quests):", vm.totalBatchCount or #vm.batchQuests), 0.80, 0.55, 1.00)
        for i = 1, #vm.batchQuests do
            local bq = vm.batchQuests[i]
            local tagText = (bq.status == "ready" and "[Ready]")
                or (bq.status == "active" and "[In Log]")
                or (bq.status == "completed" and "[Done]")
                or "[Open]"
            local lr, lg, lb = GetStatusColor(bq.status)
            local lvlPart = bq.level and string.format(" [%d]", bq.level) or ""
            tt:AddDoubleLine(
                string.format("• %s%s", bq.name or "Quest", lvlPart),
                tagText,
                DEFAULT_TEXT_COLOR[1], DEFAULT_TEXT_COLOR[2], DEFAULT_TEXT_COLOR[3],
                lr, lg, lb
            )
            if bq.objective and bq.objective ~= "" then
                tt:AddLine("    " .. tostring(bq.objective), 0.72, 0.76, 0.82, true)
            end
        end
    end

    local hasExtras = (vm.chainLine and vm.chainLine ~= "")
        or (vm.xp and vm.xp > 0)
        or (vm.goldCopper and vm.goldCopper > 0)
        or (vm.bagSlots and vm.bagSlots > 0)
        or (vm.unlocksDungeon and vm.unlocksDungeon ~= "")
        or (vm.rewardItems and #vm.rewardItems > 0)
    if hasExtras then
        tt:AddLine(" ")
        if vm.chainLine and vm.chainLine ~= "" then
            tt:AddLine(vm.chainLine, 0.40, 0.88, 1.00, true)
        end
        local rewardParts = {}
        if vm.xp and vm.xp > 0 and ns.FormatNumber then
            rewardParts[#rewardParts + 1] = "+" .. ns.FormatNumber(vm.xp) .. " XP"
        end
        if vm.goldCopper and vm.goldCopper > 0 and ns.FormatMoney then
            rewardParts[#rewardParts + 1] = StripColorCodes(ns.FormatMoney(vm.goldCopper))
        end
        if vm.bagSlots and vm.bagSlots > 0 then
            rewardParts[#rewardParts + 1] = string.format("%d-Slot Bag", vm.bagSlots)
        end
        if #rewardParts > 0 then
            tt:AddLine("Rewards: " .. table.concat(rewardParts, " · "), 0.78, 0.92, 0.68, true)
        end
        for i = 1, #(vm.rewardItems or {}) do
            local rw = vm.rewardItems[i]
            local qc = ITEM_QUALITY_COLORS and ITEM_QUALITY_COLORS[rw.quality]
            if qc then
                tt:AddLine("  Item: " .. tostring(rw.name), qc.r, qc.g, qc.b, true)
            else
                tt:AddLine("  Item: " .. tostring(rw.name), 1.00, 1.00, 1.00, true)
            end
        end
        if vm.unlocksDungeon and vm.unlocksDungeon ~= "" then
            tt:AddLine("Unlocks Dungeon: " .. tostring(vm.unlocksDungeon), 1.00, 0.58, 0.30, true)
        end
    end

    if vm.whyText and vm.whyText ~= "" then
        tt:AddLine(" ")
        tt:AddLine("Why: " .. tostring(vm.whyText), 0.62, 0.74, 0.84, true)
    end

    if (vm.leftClickHint and vm.leftClickHint ~= "") or (vm.rightClickHint and vm.rightClickHint ~= "") then
        tt:AddLine(" ")
        if vm.leftClickHint and vm.leftClickHint ~= "" then
            tt:AddLine(vm.leftClickHint, DEFAULT_HINT_COLOR[1], DEFAULT_HINT_COLOR[2], DEFAULT_HINT_COLOR[3], true)
        end
        if vm.rightClickHint and vm.rightClickHint ~= "" then
            tt:AddLine(vm.rightClickHint, DEFAULT_HINT_COLOR[1], DEFAULT_HINT_COLOR[2], DEFAULT_HINT_COLOR[3], true)
        end
    end
end

function QuestInfo.ShowTooltip(owner, ctx)
    if not owner or not GameTooltip or not GameTooltip.SetOwner then
        return nil
    end
    local vm = QuestInfo.BuildTooltipModel(ctx)
    if not vm then
        return nil
    end

    GameTooltip:SetOwner(owner, ChooseAnchor(owner))
    if GameTooltip.ClearLines then
        GameTooltip:ClearLines()
    end

    if vm.kind == "dungeon" then
        RenderDungeonTooltip(GameTooltip, vm)
    else
        RenderQuestTooltip(GameTooltip, vm)
    end

    if GameTooltip.Show then
        GameTooltip:Show()
    end
    return vm
end

function QuestInfo.HideTooltip(owner)
    if not GameTooltip then
        return
    end
    if owner and GameTooltip.IsOwned and (not GameTooltip:IsOwned(owner)) then
        return
    end
    if GameTooltip.Hide then
        GameTooltip:Hide()
    end
end

function QuestInfo.TryInsertChatLink(questId, step)
    local isChatLink = (IsModifiedClick and IsModifiedClick("CHATLINK"))
        or (IsShiftKeyDown and IsShiftKeyDown())
    if not isChatLink then
        return false
    end
    local activeEdit = ChatEdit_GetActiveWindow and ChatEdit_GetActiveWindow()
    if not (activeEdit and ChatEdit_InsertLink) then
        return false
    end
    local qid = QuestInfo.ResolvePrimaryQuestId(questId, step)
    if not qid then
        return false
    end
    local data = ns.GetData and ns.GetData() or {}
    local q = data.quests and data.quests[qid] or nil
    local qdb = GetQuestieDB()
    local title = QueryQuestTitle(qid, q, step, qdb)
    ChatEdit_InsertLink("[" .. tostring(title) .. " (" .. tostring(qid) .. ")]")
    return true, qid
end

local function GetStandaloneQuestLogFrame()
    return _G.QuestLogExFrame or _G.ClassicQuestLog or _G.QuestLogFrame
end

local function PinQuestGiverFallback(qid, q, step, pinTarget)
    if not ns.Waypoint then
        return
    end
    if step and ns.Waypoint.SetForStep then
        ns.Waypoint.SetForStep(step, { announce = false })
        return
    end
    if pinTarget and ns.Waypoint.SetTarget then
        ns.Waypoint.SetTarget(pinTarget, { announce = false })
        return
    end
    if q and q.starter and type(q.starter.x) == "number" and type(q.starter.y) == "number" and ns.Waypoint.SetTarget then
        local data = ns.GetData and ns.GetData() or {}
        local zId = q.starter.zoneId or q.zoneId
        ns.Waypoint.SetTarget({
            zoneId = zId,
            zoneName = GetZoneName(zId, q.zoneName, data),
            x = q.starter.x,
            y = q.starter.y,
            title = q.name or ("Quest #" .. tostring(qid)),
            npcName = q.starter.name,
        }, { announce = false })
    end
end

local function OpenEmptyQuestLogOrMap(uiMapId)
    local questFrame = GetStandaloneQuestLogFrame()
    if questFrame then
        if not (questFrame.IsShown and questFrame:IsShown()) then
            if type(_G.ShowUIPanel) == "function" then
                pcall(_G.ShowUIPanel, questFrame)
            elseif questFrame.Show then
                pcall(questFrame.Show, questFrame)
            end
        end
        return true
    end
    if _G.WorldMapFrame then
        if uiMapId and type(_G.OpenWorldMap) == "function" then
            pcall(_G.OpenWorldMap, uiMapId)
        elseif uiMapId and C_Map and C_Map.OpenWorldMap then
            pcall(C_Map.OpenWorldMap, uiMapId)
        end
        if not (_G.WorldMapFrame.IsShown and _G.WorldMapFrame:IsShown()) then
            if type(_G.ShowUIPanel) == "function" then
                pcall(_G.ShowUIPanel, _G.WorldMapFrame)
            elseif _G.WorldMapFrame.Show then
                pcall(_G.WorldMapFrame.Show, _G.WorldMapFrame)
            end
        end
        if _G.WorldMapFrame.QuestLog and not (_G.WorldMapFrame.QuestLog.IsShown and _G.WorldMapFrame.QuestLog:IsShown()) then
            if _G.WorldMapFrame.HandleUserActionOpenSidePanel then
                pcall(_G.WorldMapFrame.HandleUserActionOpenSidePanel, _G.WorldMapFrame)
            elseif _G.WorldMapFrame.QuestLog.Show then
                pcall(_G.WorldMapFrame.QuestLog.Show, _G.WorldMapFrame.QuestLog)
            end
        end
        return true
    end
    if type(_G.ToggleQuestLog) == "function" then
        pcall(_G.ToggleQuestLog)
        return true
    end
    return false
end

function QuestInfo.OpenQuestInLog(questId, step, opts)
    opts = opts or {}
    local qid = QuestInfo.ResolvePrimaryQuestId(questId, step)

    local linked, linkedQid = QuestInfo.TryInsertChatLink(qid, step)
    if linked then
        return { ok = true, mode = "chat_link", questId = linkedQid }
    end

    if _G.InputUtil and _G.InputUtil.IsGamepadUIEnabled and _G.InputUtil.IsGamepadUIEnabled() then
        ns.Print("Opening the Quest Log from ForeverQuestPath is disabled in controller mode.")
        return { ok = false, reason = "gamepad_mode", questId = qid }
    end

    local logIndex = qid and QuestInfo.FindQuestLogIndex(qid) or nil
    if logIndex and C_QuestLog and C_QuestLog.SetSelectedQuest then
        pcall(C_QuestLog.SetSelectedQuest, qid)
    end

    if InCombatLockdown and InCombatLockdown() then
        ns.Print("Cannot open Quest Log while in combat.")
        return { ok = false, reason = "combat_lockdown", questId = qid, inLog = (logIndex ~= nil) }
    end

    local data = ns.GetData and ns.GetData() or {}
    local q = qid and data.quests and data.quests[qid] or nil
    local qdb = GetQuestieDB()
    local title = QueryQuestTitle(qid, q, step, qdb)
    local questFrame = GetStandaloneQuestLogFrame()

    if logIndex then
        if questFrame then
            if C_QuestLog and C_QuestLog.SetSelectedQuest then
                pcall(C_QuestLog.SetSelectedQuest, qid)
            end
            if type(_G.QuestLog_SetSelection) == "function" then
                pcall(_G.QuestLog_SetSelection, logIndex)
            elseif type(_G.SelectQuestLogEntry) == "function" then
                pcall(_G.SelectQuestLogEntry, logIndex)
            end
            local sb = (_G.QuestLogListScrollFrame and _G.QuestLogListScrollFrame.ScrollBar) or _G.QuestLogListScrollFrameScrollBar
            if sb and sb.GetValueStep and sb.SetValue then
                local okStep, stepVal = pcall(sb.GetValueStep, sb)
                local scrollStep = (okStep and tonumber(stepVal) and tonumber(stepVal) > 0) and tonumber(stepVal) or 16
                pcall(sb.SetValue, sb, math.max(0, logIndex * scrollStep - scrollStep * 3))
            end
            if not (questFrame.IsShown and questFrame:IsShown()) then
                if type(_G.ShowUIPanel) == "function" then
                    pcall(_G.ShowUIPanel, questFrame)
                elseif questFrame.Show then
                    pcall(questFrame.Show, questFrame)
                end
                if _G.QuestLogEx and _G.QuestLogEx.Maximize then
                    pcall(_G.QuestLogEx.Maximize, _G.QuestLogEx)
                end
            end
            if type(_G.QuestLog_UpdateQuestDetails) == "function" then
                pcall(_G.QuestLog_UpdateQuestDetails)
            end
            if type(_G.QuestLog_Update) == "function" then
                pcall(_G.QuestLog_Update)
            end
            return { ok = true, mode = "standalone_quest_log", questId = qid, logIndex = logIndex, inLog = true }
        end

        if type(_G.QuestMapFrame_OpenToQuestDetails) == "function" then
            if C_QuestLog and C_QuestLog.SetSelectedQuest then
                pcall(C_QuestLog.SetSelectedQuest, qid)
            end
            pcall(_G.QuestMapFrame_OpenToQuestDetails, qid)
            return { ok = true, mode = "quest_map_details", questId = qid, logIndex = logIndex, inLog = true }
        end

        if _G.WorldMapFrame then
            if C_QuestLog and C_QuestLog.SetSelectedQuest then
                pcall(C_QuestLog.SetSelectedQuest, qid)
            end
            if not (_G.WorldMapFrame.IsShown and _G.WorldMapFrame:IsShown()) then
                if type(_G.ShowUIPanel) == "function" then
                    pcall(_G.ShowUIPanel, _G.WorldMapFrame)
                elseif _G.WorldMapFrame.Show then
                    pcall(_G.WorldMapFrame.Show, _G.WorldMapFrame)
                end
            end
            if _G.WorldMapFrame.QuestLog and not (_G.WorldMapFrame.QuestLog.IsShown and _G.WorldMapFrame.QuestLog:IsShown()) then
                if _G.WorldMapFrame.HandleUserActionOpenSidePanel then
                    pcall(_G.WorldMapFrame.HandleUserActionOpenSidePanel, _G.WorldMapFrame)
                elseif _G.WorldMapFrame.QuestLog.Show then
                    pcall(_G.WorldMapFrame.QuestLog.Show, _G.WorldMapFrame.QuestLog)
                end
            end
            if type(_G.QuestMapFrame_ShowQuestDetails) == "function" then
                pcall(_G.QuestMapFrame_ShowQuestDetails, qid)
            end
            return { ok = true, mode = "world_map_quest_log", questId = qid, logIndex = logIndex, inLog = true }
        end

        if type(_G.ToggleQuestLog) == "function" then
            if C_QuestLog and C_QuestLog.SetSelectedQuest then
                pcall(C_QuestLog.SetSelectedQuest, qid)
            end
            pcall(_G.ToggleQuestLog)
            return { ok = true, mode = "toggle_quest_log", questId = qid, logIndex = logIndex, inLog = true }
        end
        return { ok = false, reason = "no_quest_log_frame", questId = qid, logIndex = logIndex, inLog = true }
    end

    local status = qid and ResolveQuestStatus(qid, nil) or "available"
    local zId = (step and step.zoneId)
        or (q and q.starter and q.starter.zoneId)
        or (q and q.zoneId)
        or (opts.pinTarget and opts.pinTarget.zoneId)
        or nil
    local zName = (step and step.zoneName)
        or (q and q.zoneName)
        or (opts.pinTarget and opts.pinTarget.zoneName)
        or nil
    local uiMapId = (ns.Waypoint and ns.Waypoint.ResolveUiMapId) and ns.Waypoint.ResolveUiMapId(zId, zName) or nil

    OpenEmptyQuestLogOrMap(uiMapId)

    if status ~= "completed" and opts.pinFallback ~= false then
        PinQuestGiverFallback(qid, q, step, opts.pinTarget)
    end

    if not opts.silent then
        if not qid then
            ns.Print("Opened Quest Log.")
        elseif status == "completed" then
            ns.Printf("%s (#%d) is already completed — opened Quest Log.", tostring(title), qid)
        else
            ns.Printf("%s (#%d) is not in your Quest Log yet — opened Quest Log and pinned quest giver.", tostring(title), qid)
        end
    end

    return { ok = true, mode = "not_in_log_opened", questId = qid, inLog = false, status = status }
end

function QuestInfo.OpenDungeonInLog(dungeon, pinTarget, opts)
    opts = opts or {}
    if type(dungeon) == "table" then
        local firstIncompleteId = nil
        for _, ch in ipairs(dungeon.unlockChains or {}) do
            for _, cs in ipairs(ch.steps or {}) do
                local qid = tonumber(cs.questId)
                if qid and qid > 0 then
                    if QuestInfo.FindQuestLogIndex(qid) then
                        return QuestInfo.OpenQuestInLog(qid, nil, { pinTarget = pinTarget, silent = opts.silent })
                    end
                    if (not firstIncompleteId) and cs.status ~= "completed" then
                        firstIncompleteId = qid
                    end
                end
            end
        end
        for _, dq in ipairs(dungeon.quests or {}) do
            local qid = tonumber(dq.questId)
            if qid and qid > 0 then
                if QuestInfo.FindQuestLogIndex(qid) then
                    return QuestInfo.OpenQuestInLog(qid, nil, { pinTarget = pinTarget, silent = opts.silent })
                end
                if (not firstIncompleteId) and dq.status ~= "completed" then
                    firstIncompleteId = qid
                end
            end
        end
        if firstIncompleteId then
            return QuestInfo.OpenQuestInLog(firstIncompleteId, nil, { pinTarget = pinTarget, silent = opts.silent })
        end
    end

    if _G.InputUtil and _G.InputUtil.IsGamepadUIEnabled and _G.InputUtil.IsGamepadUIEnabled() then
        ns.Print("Opening the Quest Log from ForeverQuestPath is disabled in controller mode.")
        return { ok = false, reason = "gamepad_mode" }
    end
    if InCombatLockdown and InCombatLockdown() then
        ns.Print("Cannot open Quest Log while in combat.")
        return { ok = false, reason = "combat_lockdown" }
    end

    local uiMapId = pinTarget and ns.Waypoint and ns.Waypoint.ResolveUiMapId and ns.Waypoint.ResolveUiMapId(pinTarget.zoneId, pinTarget.zoneName) or nil
    OpenEmptyQuestLogOrMap(uiMapId)
    if pinTarget and ns.Waypoint and ns.Waypoint.SetTarget and opts.pinFallback ~= false then
        ns.Waypoint.SetTarget(pinTarget, { announce = false })
    end
    return { ok = true, mode = "dungeon_not_in_log_opened", inLog = false }
end
