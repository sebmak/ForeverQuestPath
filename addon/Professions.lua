--------------------------------------------------------------------------------
-- Professions.lua
-- Rules:
--   1. Manages selection, client skill-line detection, cache signatures, scoring,
--      and badge formatting across all 12 WoW Forever 16001 professions:
--      Alchemy (171), Blacksmithing (164), Enchanting (333), Engineering (202),
--      Herbalism (182), Leatherworking (165), Mining (186), Skinning (393),
--      Tailoring (197), Cooking (185), First Aid (129), and Fishing (356).
--   2. All module tables, constants, and scratch slots live at the top of the
--      file; hot scoring and signature paths perform zero table allocations.
--   3. Maintains full backward compatibility with hasSkinning, hasLeatherworking,
--      prioritizeSkinningLW, autoDetectProfessions, skinningScore, lwRewards,
--      and synthetic unit-test player.professions tables lacking profMask.
--   4. Supports manual multi-profession prioritization even without trained rank,
--      while live skill-rank gates still protect quests requiring minSkill > rank.
--------------------------------------------------------------------------------

local _, ns = ...

local Professions = {}
ns.Professions = Professions

local SKILL_FIRST_AID = 129
local SKILL_BLACKSMITHING = 164
local SKILL_LEATHERWORKING = 165
local SKILL_ALCHEMY = 171
local SKILL_HERBALISM = 182
local SKILL_COOKING = 185
local SKILL_MINING = 186
local SKILL_TAILORING = 197
local SKILL_ENGINEERING = 202
local SKILL_ENCHANTING = 333
local SKILL_FISHING = 356
local SKILL_SKINNING = 393

local PROFESSIONS_ORDERED = {
    { id = SKILL_ALCHEMY,        key = "alchemy",        name = "Alchemy",        short = "Alch",   color = "|cff66e0ff", bit = 1,    isPrimary = true,  aliases = { "alchemy", "alch" } },
    { id = SKILL_BLACKSMITHING,  key = "blacksmithing",  name = "Blacksmithing",  short = "BS",     color = "|cffb0c4de", bit = 2,    isPrimary = true,  aliases = { "blacksmithing", "blacksmith", "bs", "smith", "smithing" } },
    { id = SKILL_ENCHANTING,     key = "enchanting",     name = "Enchanting",     short = "Ench",   color = "|cffe080ff", bit = 4,    isPrimary = true,  aliases = { "enchanting", "enchant", "ench" } },
    { id = SKILL_ENGINEERING,    key = "engineering",    name = "Engineering",    short = "Eng",    color = "|cffffcc44", bit = 8,    isPrimary = true,  aliases = { "engineering", "engineer", "eng", "engi" } },
    { id = SKILL_HERBALISM,      key = "herbalism",      name = "Herbalism",      short = "Herb",   color = "|cff55ee77", bit = 16,   isPrimary = true,  aliases = { "herbalism", "herb", "herbs" } },
    { id = SKILL_LEATHERWORKING, key = "leatherworking", name = "Leatherworking", short = "LW",     color = "|cffb87333", bit = 32,   isPrimary = true,  aliases = { "leatherworking", "lw", "leather" } },
    { id = SKILL_MINING,         key = "mining",         name = "Mining",         short = "Mine",   color = "|cffd8a878", bit = 64,   isPrimary = true,  aliases = { "mining", "mine", "miner" } },
    { id = SKILL_SKINNING,       key = "skinning",       name = "Skinning",       short = "Skin",   color = "|cffd9a05b", bit = 128,  isPrimary = true,  aliases = { "skinning", "skin" } },
    { id = SKILL_TAILORING,      key = "tailoring",      name = "Tailoring",      short = "Tailor", color = "|cffff99cc", bit = 256,  isPrimary = true,  aliases = { "tailoring", "tailor" } },
    { id = SKILL_COOKING,        key = "cooking",        name = "Cooking",        short = "Cook",   color = "|cffffaa66", bit = 512,  isPrimary = false, aliases = { "cooking", "cook" } },
    { id = SKILL_FIRST_AID,      key = "firstaid",       name = "First Aid",      short = "1stAid", color = "|cffff7777", bit = 1024, isPrimary = false, aliases = { "firstaid", "first_aid", "fa", "bandages" } },
    { id = SKILL_FISHING,        key = "fishing",        name = "Fishing",        short = "Fish",   color = "|cff55bbff", bit = 2048, isPrimary = false, aliases = { "fishing", "fish" } },
}

local PROF_BY_ID = {}
local PROF_ALIAS_TO_ID = {}
local FALLBACK_SKIN_LW_IDS = { SKILL_SKINNING, SKILL_LEATHERWORKING }
local scratchSkillIds = {}
local scratchNameParts = {}

for i = 1, #PROFESSIONS_ORDERED do
    local p = PROFESSIONS_ORDERED[i]
    PROF_BY_ID[p.id] = p
    PROF_ALIAS_TO_ID[tostring(p.id)] = p.id
    PROF_ALIAS_TO_ID[p.key] = p.id
    PROF_ALIAS_TO_ID[string.lower(p.name)] = p.id
    PROF_ALIAS_TO_ID[string.lower(p.short)] = p.id
    PROF_ALIAS_TO_ID[string.lower(p.name):gsub("[%s_%-]", "")] = p.id
    if p.aliases then
        for j = 1, #p.aliases do
            PROF_ALIAS_TO_ID[string.lower(p.aliases[j])] = p.id
        end
    end
end

function Professions.Initialize()
    if ns.charDB then
        if type(ns.charDB.selectedProfessions) ~= "table" then
            ns.charDB.selectedProfessions = {}
        end
        if ns.charDB.toggles and ns.charDB.toggles.prioritizeProfessions == nil then
            ns.charDB.toggles.prioritizeProfessions = (ns.charDB.toggles.prioritizeSkinningLW ~= false)
        end
    end
end

function Professions.GetOrdered()
    return PROFESSIONS_ORDERED
end

function Professions.GetInfo(skillId)
    return PROF_BY_ID[tonumber(skillId)]
end

function Professions.GetName(skillId)
    local info = PROF_BY_ID[tonumber(skillId)]
    return info and info.name or ("Profession #" .. tostring(skillId))
end

function Professions.GetShort(skillId)
    local info = PROF_BY_ID[tonumber(skillId)]
    return info and info.short or ("P" .. tostring(skillId))
end

function Professions.ResolveToken(tok)
    if tok == nil then return nil end
    local num = tonumber(tok)
    if num and PROF_BY_ID[num] then
        return num
    end
    local s = string.lower(tostring(tok)):match("^%s*(.-)%s*$")
    if s == "" then return nil end
    return PROF_ALIAS_TO_ID[s] or PROF_ALIAS_TO_ID[s:gsub("[%s_%-]", "")]
end

function Professions.IsPrioritizeEnabled(toggles)
    if not toggles then return true end
    return (toggles.prioritizeProfessions ~= false) and (toggles.prioritizeSkinningLW ~= false)
end

function Professions.HasProfession(prof, skillId)
    if not prof then return false end
    local sid = tonumber(skillId)
    if not sid then return false end
    if type(prof.activeSkills) == "table" then
        return prof.activeSkills[sid] == true
    end
    if sid == SKILL_SKINNING and prof.hasSkinning ~= nil then
        return prof.hasSkinning == true
    end
    if sid == SKILL_LEATHERWORKING and prof.hasLeatherworking ~= nil then
        return prof.hasLeatherworking == true
    end
    if type(prof.skills) == "table" and prof.skills[sid] ~= nil then
        return true
    end
    return false
end

function Professions.GetKnownRank(prof, skillId)
    if not prof then return nil end
    local sid = tonumber(skillId)
    if not sid then return nil end
    if type(prof.skills) == "table" and prof.skills[sid] ~= nil then
        return prof.skills[sid]
    end
    if sid == SKILL_SKINNING and prof.skinningRank ~= nil then
        return prof.skinningRank
    end
    if sid == SKILL_LEATHERWORKING and prof.leatherworkingRank ~= nil then
        return prof.leatherworkingRank
    end
    return nil
end

function Professions.GetProfSignatures(prof)
    if not prof then
        return 0, 0
    end
    if prof.profMask ~= nil and prof.rankSig ~= nil then
        return prof.profMask, prof.rankSig
    end
    local mask = 0
    local rSig = 0
    for i = 1, #PROFESSIONS_ORDERED do
        local p = PROFESSIONS_ORDERED[i]
        if Professions.HasProfession(prof, p.id) then
            mask = mask + p.bit
        end
        local rank = tonumber(Professions.GetKnownRank(prof, p.id)) or -1
        rSig = (rSig * 389 + (rank + 2)) % 2147483647
    end
    return mask, rSig
end

function Professions.BuildProfessionState(skills, clientReported, apiAvailable, cdb)
    skills = type(skills) == "table" and skills or {}
    local toggles = (cdb and cdb.toggles) or {}
    local sel = nil
    if cdb then
        if type(cdb.selectedProfessions) ~= "table" then
            cdb.selectedProfessions = {}
        end
        sel = cdb.selectedProfessions
    end

    local activeSkills = {}
    local selectedIds = {}
    local autoDetect = (toggles.autoDetectProfessions ~= false)

    if autoDetect then
        if apiAvailable then
            for i = 1, #PROFESSIONS_ORDERED do
                local p = PROFESSIONS_ORDERED[i]
                local active = (skills[p.id] ~= nil)
                activeSkills[p.id] = active
                if sel then
                    sel[p.id] = active
                end
            end
            if cdb and cdb.toggles then
                cdb.toggles.hasSkinning = (activeSkills[SKILL_SKINNING] == true)
                cdb.toggles.hasLeatherworking = (activeSkills[SKILL_LEATHERWORKING] == true)
            end
        else
            for i = 1, #PROFESSIONS_ORDERED do
                local p = PROFESSIONS_ORDERED[i]
                local sid = p.id
                local sVal = sel and (sel[sid] ~= nil and sel[sid] or sel[tostring(sid)]) or nil
                local active
                if sVal ~= nil then
                    active = (sVal == true)
                elseif sid == SKILL_SKINNING then
                    active = (toggles.hasSkinning == true)
                elseif sid == SKILL_LEATHERWORKING then
                    active = (toggles.hasLeatherworking == true)
                else
                    active = false
                end
                activeSkills[sid] = active
            end
        end
    else
        for i = 1, #PROFESSIONS_ORDERED do
            local p = PROFESSIONS_ORDERED[i]
            local sid = p.id
            local sVal = sel and (sel[sid] ~= nil and sel[sid] or sel[tostring(sid)]) or nil
            local active
            if sVal ~= nil then
                active = (sVal == true)
            elseif sid == SKILL_SKINNING then
                active = (toggles.hasSkinning == true)
            elseif sid == SKILL_LEATHERWORKING then
                active = (toggles.hasLeatherworking == true)
            else
                active = false
            end
            activeSkills[sid] = active
        end
    end

    local profMask = 0
    local rankSig = 0
    local activeCount = 0
    local primaryCount = 0
    local nShorts = 0
    for i = 1, #PROFESSIONS_ORDERED do
        local p = PROFESSIONS_ORDERED[i]
        local sid = p.id
        if activeSkills[sid] then
            profMask = profMask + p.bit
            activeCount = activeCount + 1
            selectedIds[activeCount] = sid
            if p.isPrimary then
                primaryCount = primaryCount + 1
            end
            if activeCount <= 3 then
                nShorts = nShorts + 1
                scratchNameParts[nShorts] = p.short
            end
        end
        local rank = tonumber(skills[sid]) or -1
        rankSig = (rankSig * 389 + (rank + 2)) % 2147483647
    end

    local summaryShort
    if activeCount == 0 then
        summaryShort = "None"
    elseif activeCount <= 3 then
        summaryShort = table.concat(scratchNameParts, " + ", 1, nShorts)
    else
        summaryShort = tostring(activeCount) .. " Selected"
    end
    local buttonLabel = "Profs: " .. summaryShort .. (autoDetect and " (Auto)" or "")

    return {
        skills = skills,
        clientReported = clientReported,
        activeSkills = activeSkills,
        selectedIds = selectedIds,
        profMask = profMask,
        rankSig = rankSig,
        activeCount = activeCount,
        primaryCount = primaryCount,
        summaryShort = summaryShort,
        buttonLabel = buttonLabel,
        detectedSkinning = (skills[SKILL_SKINNING] ~= nil),
        detectedLeatherworking = (skills[SKILL_LEATHERWORKING] ~= nil),
        hasSkinning = (activeSkills[SKILL_SKINNING] == true),
        hasLeatherworking = (activeSkills[SKILL_LEATHERWORKING] == true),
        skinningRank = skills[SKILL_SKINNING],
        leatherworkingRank = skills[SKILL_LEATHERWORKING],
    }
end

function Professions.GetActiveSkillIds(prof, fallbackSkinLW)
    if prof and type(prof.selectedIds) == "table" and #prof.selectedIds > 0 then
        return prof.selectedIds
    end
    local cnt = 0
    if prof then
        for i = 1, #PROFESSIONS_ORDERED do
            local sid = PROFESSIONS_ORDERED[i].id
            if Professions.HasProfession(prof, sid) then
                cnt = cnt + 1
                scratchSkillIds[cnt] = sid
            end
        end
    end
    for i = cnt + 1, #scratchSkillIds do
        scratchSkillIds[i] = nil
    end
    if cnt > 0 then
        return scratchSkillIds
    end
    if fallbackSkinLW then
        return FALLBACK_SKIN_LW_IDS
    end
    return scratchSkillIds
end

local function DirectSkillScoreOnQuest(q, sid)
    if not q then return 0, false end
    local ps = q.profScores and q.profScores[sid] or 0
    local direct = false
    local req = q.requiredSkill
    if type(req) == "table" and tonumber(req.skillId or req.skillID or req[1]) == sid then
        direct = true
        if ps < 88 then ps = 88 end
    end
    if sid == SKILL_LEATHERWORKING and q.isLeatherworking == true then
        direct = true
        if ps < 88 then ps = 88 end
    elseif sid == SKILL_SKINNING and q.isSkinning == true then
        direct = true
        if ps < 88 then ps = 88 end
    end
    if type(q.profRewards) == "table" then
        for i = 1, #q.profRewards do
            local pr = q.profRewards[i]
            if pr and tonumber(pr.skillId) == sid then
                direct = true
                if ps < 90 then ps = 90 end
            end
        end
    end
    if sid == SKILL_LEATHERWORKING and type(q.lwRewards) == "table" and #q.lwRewards > 0 then
        direct = true
        if ps < 90 then ps = 90 end
    end
    if sid == SKILL_SKINNING and ps == 0 then
        local legacySkin = tonumber(q.skinningScore or (q.features and q.features.skinningValue)) or 0
        if legacySkin > 0 then
            ps = legacySkin
        end
    end
    return ps, direct
end

function Professions.IsQuestDirectMatch(q, prof, toggles)
    if not (q and prof and Professions.IsPrioritizeEnabled(toggles)) then
        return false, nil
    end
    for i = 1, #PROFESSIONS_ORDERED do
        local sid = PROFESSIONS_ORDERED[i].id
        if Professions.HasProfession(prof, sid) then
            local _, isDirect = DirectSkillScoreOnQuest(q, sid)
            if isDirect then
                return true, sid
            end
        end
    end
    return false, nil
end

function Professions.ScoreQuestForPlayer(q, prof, toggles, activeProfile)
    if not q then
        return 0, nil, 0
    end
    local mask = 0
    if prof then
        mask = Professions.GetProfSignatures(prof)
    end
    local useFallbackSkinLW = (mask == 0 and activeProfile == "skinning_lw")

    local bestScore = 0
    local bestSkillId = nil
    local matchCount = 0

    if useFallbackSkinLW then
        for i = 1, #FALLBACK_SKIN_LW_IDS do
            local sid = FALLBACK_SKIN_LW_IDS[i]
            local s = DirectSkillScoreOnQuest(q, sid)
            if s > 0 then
                if s >= 30 then matchCount = matchCount + 1 end
                if s > bestScore then
                    bestScore = s
                    bestSkillId = sid
                end
            end
        end
    elseif mask > 0 then
        for i = 1, #PROFESSIONS_ORDERED do
            local sid = PROFESSIONS_ORDERED[i].id
            if Professions.HasProfession(prof, sid) then
                local s = DirectSkillScoreOnQuest(q, sid)
                if s > 0 then
                    if s >= 30 then matchCount = matchCount + 1 end
                    if s > bestScore then
                        bestScore = s
                        bestSkillId = sid
                    end
                end
            end
        end
    else
        return 0, nil, 0
    end

    if bestScore > 0 and matchCount >= 2 then
        bestScore = math.min(100, bestScore + 8)
    end
    if bestScore > 0 and not Professions.IsPrioritizeEnabled(toggles) then
        bestScore = bestScore * 0.2
    end
    return bestScore, bestSkillId, matchCount
end

function Professions.FormatQuestBadge(q, stepBadges, prof, includeSkinScore)
    local sid = stepBadges and stepBadges.profSkillId or nil
    if (not sid) and q and prof then
        local mask = Professions.GetProfSignatures(prof)
        if mask > 0 then
            local bestS = 0
            for i = 1, #PROFESSIONS_ORDERED do
                local p = PROFESSIONS_ORDERED[i]
                if Professions.HasProfession(prof, p.id) then
                    local s = DirectSkillScoreOnQuest(q, p.id)
                    if s >= 30 and s > bestS then
                        bestS = s
                        sid = p.id
                    end
                end
            end
        end
    end
    if not sid and q then
        if type(q.requiredSkill) == "table" then
            local reqId = tonumber(q.requiredSkill.skillId or q.requiredSkill.skillID or q.requiredSkill[1])
            if reqId and PROF_BY_ID[reqId] then
                sid = reqId
            end
        elseif type(q.profRewards) == "table" and q.profRewards[1] and PROF_BY_ID[q.profRewards[1].skillId] then
            sid = q.profRewards[1].skillId
        end
    end
    if (not sid) and stepBadges then
        if stepBadges.isLeatherworking then
            sid = SKILL_LEATHERWORKING
        elseif stepBadges.isSkinning or (stepBadges.skinningScore and stepBadges.skinningScore >= 30) then
            sid = SKILL_SKINNING
        end
    elseif (not sid) and q then
        if q.isLeatherworking then
            sid = SKILL_LEATHERWORKING
        elseif q.isSkinning or (tonumber(q.skinningScore) or 0) >= 30 then
            sid = SKILL_SKINNING
        end
    end
    if not sid then
        return nil, nil
    end
    local info = PROF_BY_ID[sid]
    if not info then
        return nil, nil
    end
    if sid == SKILL_SKINNING and includeSkinScore then
        local sScore = tonumber((stepBadges and stepBadges.skinningScore) or (q and q.skinningScore)) or 0
        if sScore >= 30 then
            return string.format("|cffd9a05b[Skin %d]|r", math.floor(sScore + 0.5)), sid
        end
    end
    return info.color .. "[" .. info.short .. "]|r", sid
end

local function SyncManualTable()
    if not ns.charDB then return nil end
    if type(ns.charDB.selectedProfessions) ~= "table" then
        ns.charDB.selectedProfessions = {}
    end
    ns.charDB.toggles = ns.charDB.toggles or {}
    return ns.charDB.selectedProfessions
end

function Professions.SetProfession(skillId, enabled)
    local sid = Professions.ResolveToken(skillId)
    if not sid then return false end
    local sel = SyncManualTable()
    if not sel then return false end
    ns.charDB.toggles.autoDetectProfessions = false
    sel[sid] = (enabled == true)
    if sid == SKILL_SKINNING then
        ns.charDB.toggles.hasSkinning = (enabled == true)
    elseif sid == SKILL_LEATHERWORKING then
        ns.charDB.toggles.hasLeatherworking = (enabled == true)
    end
    if ns.State and ns.State.Refresh then
        ns.State.Refresh("PROFESSIONS_UPDATED")
    end
    return true
end

function Professions.ToggleProfession(skillId)
    local sid = Professions.ResolveToken(skillId)
    if not sid then return false end
    local prof = ns.State and ns.State.DetectProfessions and ns.State.DetectProfessions() or nil
    local cur = Professions.HasProfession(prof, sid)
    return Professions.SetProfession(sid, not cur)
end

function Professions.SetAutoDetect(enabled)
    SyncManualTable()
    if not ns.charDB then return false end
    ns.charDB.toggles.autoDetectProfessions = (enabled == true)
    if ns.State and ns.State.Refresh then
        ns.State.Refresh("PROFESSIONS_UPDATED")
    end
    return true
end

function Professions.SetPrioritize(enabled)
    SyncManualTable()
    if not ns.charDB then return false end
    local v = (enabled == true)
    ns.charDB.toggles.prioritizeProfessions = v
    ns.charDB.toggles.prioritizeSkinningLW = v
    if ns.State and ns.State.Refresh then
        ns.State.Refresh("PROFESSIONS_UPDATED")
    end
    return true
end

function Professions.ClearManual()
    local sel = SyncManualTable()
    if not sel then return false end
    ns.charDB.toggles.autoDetectProfessions = false
    for i = 1, #PROFESSIONS_ORDERED do
        sel[PROFESSIONS_ORDERED[i].id] = false
    end
    ns.charDB.toggles.hasSkinning = false
    ns.charDB.toggles.hasLeatherworking = false
    if ns.State and ns.State.Refresh then
        ns.State.Refresh("PROFESSIONS_UPDATED")
    end
    return true
end

function Professions.GetSummaryText(prof, toggles)
    prof = prof or (ns.State and ns.State.DetectProfessions and ns.State.DetectProfessions()) or nil
    toggles = toggles or (ns.charDB and ns.charDB.toggles) or {}
    local parts = {}
    for i = 1, #PROFESSIONS_ORDERED do
        local p = PROFESSIONS_ORDERED[i]
        if Professions.HasProfession(prof, p.id) then
            local rk = Professions.GetKnownRank(prof, p.id)
            if rk and rk > 0 then
                parts[#parts + 1] = string.format("%s (%d)", p.name, rk)
            else
                parts[#parts + 1] = p.name
            end
        end
    end
    local listStr = (#parts > 0) and table.concat(parts, ", ") or "None"
    local modeStr = (toggles.autoDetectProfessions ~= false) and "Auto-Detect" or "Manual"
    local prioStr = Professions.IsPrioritizeEnabled(toggles) and "Prioritize ON" or "Prioritize OFF"
    return string.format("Professions (%s, %s): %s", modeStr, prioStr, listStr)
end

function Professions.ApplySlashCommand(rawRest)
    local rest = tostring(rawRest or ""):match("^%s*(.-)%s*$")
    local lower = string.lower(rest)
    if lower == "" or lower == "status" or lower == "list" then
        ns.Print(Professions.GetSummaryText())
        ns.Print("Usage: /fqp prof <auto|clear|on|off|mining bs ...> (e.g. '/fqp prof herb alch cook')")
        return true
    end
    if lower == "auto" then
        Professions.SetAutoDetect(true)
        Professions.SetPrioritize(true)
        ns.Print(Professions.GetSummaryText())
        return true
    end
    if lower == "clear" or lower == "none" then
        Professions.ClearManual()
        ns.Print(Professions.GetSummaryText())
        return true
    end
    if lower == "on" or lower == "enable" then
        Professions.SetPrioritize(true)
        ns.Print(Professions.GetSummaryText())
        return true
    end
    if lower == "off" or lower == "disable" then
        Professions.SetPrioritize(false)
        ns.Print(Professions.GetSummaryText())
        return true
    end

    local chosen = {}
    local count = 0
    for tok in lower:gmatch("[^,%s]+") do
        local sid = Professions.ResolveToken(tok)
        if sid and not chosen[sid] then
            chosen[sid] = true
            count = count + 1
        end
    end
    if count == 0 then
        ns.Print("Unknown profession token(s): " .. rest)
        ns.Print("Supported: alchemy, bs, enchanting, engineering, herbalism, lw, mining, skinning, tailoring, cooking, firstaid, fishing")
        return false
    end

    local sel = SyncManualTable()
    if not sel then return false end
    ns.charDB.toggles.autoDetectProfessions = false
    ns.charDB.toggles.prioritizeProfessions = true
    ns.charDB.toggles.prioritizeSkinningLW = true
    for i = 1, #PROFESSIONS_ORDERED do
        local sid = PROFESSIONS_ORDERED[i].id
        local active = (chosen[sid] == true)
        sel[sid] = active
        if sid == SKILL_SKINNING then
            ns.charDB.toggles.hasSkinning = active
        elseif sid == SKILL_LEATHERWORKING then
            ns.charDB.toggles.hasLeatherworking = active
        end
    end
    if ns.State and ns.State.Refresh then
        ns.State.Refresh("PROFESSIONS_UPDATED")
    end
    ns.Print(Professions.GetSummaryText())
    return true
end
