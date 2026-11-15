--------------------------------------------------------------------------------
-- UI/HUD.lua
-- Rules:
--   1. Compact, movable HUD Tracker displaying the Next Step card (action,
--      quest/target, NPC & coords, travel note, XP/Gold/Bag/<Class> BiS/Skin/LW/
--      Ready Turn-In/Friend Chain/GPS Reorder/Chain Progress badges, "Why
--      this step?"), active MaxLv cap, GPS status, Ready Turn-Ins counter,
--      Skip / Back / Reset / Pin buttons, and 5 Upcoming Steps with Chain tags.
--   2. Reuses pooled row widgets; never leaks frames across refreshes.
--   3. Separates view-model computation (HUD.GetViewModel, forwarding
--      player into State.GetCurrentStep) from frame updates so both in-game
--      rendering and headless Lua tests remain deterministic.
--   4. Enriches the current step with live Nav shortest-path ETA & next-hop
--      gate/pass/transport summary without mutating static GeneratedData steps
--      (skipping live Nav hop resolution while frozen on taxi or dead/ghost).
--------------------------------------------------------------------------------

local _, ns = ...

local HUD = {}
ns.HUD = HUD

local HUD_WIDTH = 318
local PAD = 10
local UPCOMING_COUNT = 5

local frame = nil
local collapsed = false
local upcomingRows = {}

local function ApplyBackdrop(f, r, g, b, a, br, bg, bb, ba)
    if f and f.SetBackdrop then
        f:SetBackdrop({
            bgFile = "Interface\\Buttons\\WHITE8X8",
            edgeFile = "Interface\\Buttons\\WHITE8X8",
            edgeSize = 1,
            insets = { left = 1, right = 1, top = 1, bottom = 1 },
        })
        f:SetBackdropColor(r, g, b, a)
        f:SetBackdropBorderColor(br or 0.25, bg or 0.28, bb or 0.34, ba or 1)
    end
end

local function MakeButton(parent, text, width, height)
    local b = CreateFrame("Button", nil, parent, "BackdropTemplate")
    b:SetSize(width, height or 20)
    ApplyBackdrop(b, 0.14, 0.16, 0.20, 1, 0.28, 0.32, 0.38, 1)
    local hl = b:CreateTexture(nil, "HIGHLIGHT")
    hl:SetAllPoints()
    if hl.SetColorTexture then
        hl:SetColorTexture(1, 1, 1, 0.10)
    end
    local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    fs:SetPoint("CENTER", 0, 0)
    fs:SetText(text)
    b.label = fs
    b.SetButtonText = function(_, t) fs:SetText(t) end
    return b
end

local function GetStepProfBadges(step)
    if not step then return false, false, 0 end
    local b = step.badges or {}
    local isLW = b.isLeatherworking == true
    local skinScore = tonumber(b.skinningScore) or 0
    local isSkin = (b.isSkinning == true) or (skinScore >= 30)
    if step.questId and b.isLeatherworking == nil and b.skinningScore == nil then
        local data = ns.GetData and ns.GetData()
        local q = data and data.quests and data.quests[step.questId]
        if q then
            local reqId = type(q.requiredSkill) == "table" and tonumber(q.requiredSkill.skillId or q.requiredSkill[1]) or nil
            isLW = (q.isLeatherworking == true) or (type(q.lwRewards) == "table" and #q.lwRewards > 0) or (reqId == 165)
            skinScore = tonumber(q.skinningScore or (q.features and q.features.skinningValue)) or 0
            isSkin = (q.isSkinning == true) or (reqId == 393) or (skinScore >= 30)
        end
    end
    return isLW, isSkin, skinScore
end

local function GetStepClassBiS(step, player)
    if not step then return 0, "Class" end
    local classLabel = (player and player.className) or "Class"
    local classFile = (player and player.classFile) or "ROGUE"
    local b = step.badges or {}
    if step.questId and ns.GetData and ns.GetQuestClassScore then
        local data = ns.GetData()
        local q = data and data.quests and data.quests[step.questId]
        if q then
            local qScore = ns.GetQuestClassScore(q, classFile)
            if qScore > 0 then
                return qScore, classLabel
            end
        end
    end
    if b.classScore and (not b.classLabel or b.classLabel == classLabel) then
        return tonumber(b.classScore) or 0, b.classLabel or classLabel
    end
    if classFile == "ROGUE" and b.rogueScore then
        return tonumber(b.rogueScore) or 0, classLabel
    end
    return 0, classLabel
end

local function FormatBadges(step, player)
    if not step then return "" end
    local b = step.badges or {}
    local parts = {}
    if step.isFriendChain or b.isFriendChain then
        parts[#parts + 1] = "|cff66e0ff[Friend Chain]|r"
    end
    if step.isGpsReordered or b.isGpsReordered then
        parts[#parts + 1] = "|cff99ff66[GPS Reorder]|r"
    end
    if b.isReadyTurnIn or step.isReadyTurnIn then
        parts[#parts + 1] = "|cff4dff9e[Ready Turn-In]|r"
    end
    local ch = b.chain or (step.chainTotal and step.chainTotal >= 2 and {
        pos = step.chainPos or step.chainStep,
        total = step.chainTotal,
        remainingQuests = step.chainRemainingQuests,
        remainingSteps = step.chainRemainingSteps,
    })
    if ch and (ch.total or 0) >= 2 then
        local rq = tonumber(ch.remainingQuests) or 0
        local rs = tonumber(ch.remainingSteps) or 0
        if rq > 0 or rs > 0 then
            parts[#parts + 1] = string.format("|cff66e0ff[Chain %d/%d · %dq/%ds left]|r", ch.pos or 1, ch.total, rq, rs)
        else
            parts[#parts + 1] = string.format("|cff66e0ff[Chain %d/%d · final]|r", ch.pos or ch.total, ch.total)
        end
    end
    if b.xp and b.xp > 0 then
        parts[#parts + 1] = "|cffc080ff+" .. ns.FormatNumber(b.xp) .. " XP|r"
    end
    if b.goldCopper and b.goldCopper > 0 then
        parts[#parts + 1] = ns.FormatMoney(b.goldCopper)
    end
    if b.bagSlots and b.bagSlots > 0 then
        parts[#parts + 1] = string.format("|cff33ff99%d-Slot Bag|r", b.bagSlots)
    end
    local bisScore, bisClass = GetStepClassBiS(step, player)
    if bisScore >= 30 then
        parts[#parts + 1] = string.format("|cfffff569%s BiS %d|r", bisClass, math.floor(bisScore + 0.5))
    end
    local isLW, isSkin, skinScore = GetStepProfBadges(step)
    if isLW then
        parts[#parts + 1] = "|cffb87333[LW]|r"
    end
    if isSkin then
        if skinScore > 0 then
            parts[#parts + 1] = string.format("|cffd9a05b[Skin %d]|r", math.floor(skinScore + 0.5))
        else
            parts[#parts + 1] = "|cffd9a05b[Skin]|r"
        end
    end
    if b.unlocksDungeon and b.unlocksDungeon ~= "" then
        parts[#parts + 1] = "|cffff944dUnlocks: " .. tostring(b.unlocksDungeon) .. "|r"
    end
    return table.concat(parts, "  ")
end

function HUD.GetViewModel()
    local player = ns.State.GetPlayerInfo()
    local step, meta = ns.State.GetCurrentStep(player)
    local upcoming = ns.State.GetUpcomingSteps(UPCOMING_COUNT)
    local deviation = ns.State.GetDeviationStatus()

    local cdb = ns.charDB or {}
    local maxOff = ns.State.GetEffectiveMaxLevelOffset and ns.State.GetEffectiveMaxLevelOffset(cdb.toggles) or 2
    local capLv = (player.level or 24) + maxOff
    local offSign = (maxOff >= 0) and "+" or ""
    local readyCount = (ns.State.GetReadyTurnInCount and ns.State.GetReadyTurnInCount()) or (meta and meta.readyCount) or 0
    local readySuffix = (readyCount > 0) and string.format(" · Ready Turn-Ins: %d", readyCount) or ""
    local gpsEnabled = not (cdb.toggles and cdb.toggles.locationReorder == false)
    local gpsSuffix = ""
    if meta and meta.source == "friend_pivot" then
        gpsSuffix = " · Friend Chain"
    elseif meta and meta.source == "gps_reorder" then
        gpsSuffix = " · GPS Reorder"
    elseif not gpsEnabled then
        gpsSuffix = " · GPS Off"
    end
    local modeLabel = ((cdb.toggles and cdb.toggles.dynamicRescore)
        and (ns.GetPresetLabel(cdb.activeProfile or "custom") .. " · Live")
        or ns.GetPresetLabel(cdb.activeProfile or "balanced"))
        .. string.format(" · MaxLv %s%d (<=%d)", offSign, maxOff, capLv)
        .. readySuffix
        .. gpsSuffix

    local charTag = string.format("%s %s%s", player.raceName or "Gnome", player.className or "Rogue", player.isCharOverride and "*" or "")
    local vm = {
        playerLine = string.format("Lv %d (%d%%) · %s · %s", player.level or 24, player.xpPct or 0, charTag, player.zoneName or "Wetlands"),
        modeLabel = modeLabel,
        readyTurnInCount = readyCount,
        meta = meta,
        deviation = deviation,
        current = nil,
        upcoming = {},
    }

    if step then
        local am = ns.GetActionMeta(step.action)
        local coords = (step.x and step.y) and ns.FormatCoords(step.x, step.y) or "No coords"
        local whereParts = {}
        if step.npcName and step.npcName ~= "" then
            whereParts[#whereParts + 1] = step.npcName
        end
        if step.zoneName and step.zoneName ~= "" then
            whereParts[#whereParts + 1] = step.zoneName
        end
        whereParts[#whereParts + 1] = "(" .. coords .. ")"
        local locFrozen = (player and (player.isDead or player.onTaxi))
            or (UnitOnTaxi and UnitOnTaxi("player"))
            or (ns.IsPlayerDeadOrGhost and ns.IsPlayerDeadOrGhost())
        local navHop = (not locFrozen) and (ns.Nav and ns.Nav.ResolveStepHop) and ns.Nav.ResolveStepHop(player, step) or nil
        local displayMinutes = (navHop and navHop.reachable and navHop.totalSec)
            and math.max(0.1, math.floor((navHop.totalSec / 60.0) * 10 + 0.5) / 10)
            or step.estMinutes
        local eta = displayMinutes and string.format("  |cff888888~%.1fm|r", displayMinutes) or ""

        local travelText = step.travelNote
        if navHop and navHop.reachable and navHop.hopNote and navHop.hopNote ~= "" then
            if travelText and travelText ~= "" and travelText ~= navHop.hopNote and not tostring(travelText):find(navHop.hopLabel or navHop.hopNote, 1, true) then
                travelText = navHop.hopNote .. " · " .. travelText
            else
                travelText = navHop.hopNote
            end
        end

        local actionText
        if step.isReadyTurnIn then
            actionText = (step.action == "TRAVEL") and "|cff4dff9e[Ready Batch Travel]|r" or "|cff4dff9e[Ready Turn-In]|r"
        else
            actionText = am.hex .. "[" .. am.label .. "]|r"
        end

        local ch = (step.badges and step.badges.chain) or (step.chainTotal and step.chainTotal >= 2 and {
            pos = step.chainPos or step.chainStep,
            total = step.chainTotal,
            remainingQuests = step.chainRemainingQuests,
            remainingSteps = step.chainRemainingSteps,
        })
        local whyText = step.why or "Optimal step for current strategy weights."
        if ch and (ch.total or 0) >= 2 and not tostring(whyText):find("Chain ", 1, true) then
            local rq = tonumber(ch.remainingQuests) or 0
            local rs = tonumber(ch.remainingSteps) or 0
            if rq > 0 or rs > 0 then
                whyText = whyText .. string.format(" · Chain %d/%d (%d quests, %d steps left)", ch.pos or 1, ch.total, rq, rs)
            else
                whyText = whyText .. string.format(" · Chain %d/%d (final step)", ch.pos or ch.total, ch.total)
            end
        end

        vm.current = {
            step = step,
            actionText = actionText,
            title = step.title or "Unnamed Step",
            whereLine = table.concat(whereParts, " · ") .. eta,
            travelNote = travelText,
            navHop = navHop,
            badgesLine = FormatBadges(step, player),
            whyText = whyText,
        }
    end

    for i, u in ipairs(upcoming) do
        local uam = ns.GetActionMeta(u.action)
        local tag = ""
        if u.badges then
            local uLW, uSkin = GetStepProfBadges(u)
            local uBis = GetStepClassBiS(u, player)
            if u.badges.bagSlots and u.badges.bagSlots > 0 then
                tag = string.format("|cff33ff99%d-Bag|r", u.badges.bagSlots)
            elseif u.badges.unlocksDungeon then
                tag = "|cffff944dDungeon|r"
            elseif uBis >= 40 then
                tag = "|cfffff569BiS|r"
            elseif uLW then
                tag = "|cffb87333[LW]|r"
            elseif uSkin then
                tag = "|cffd9a05b[Skin]|r"
            elseif u.badges.xp and u.badges.xp > 0 then
                tag = "|cffaaaaaa+" .. ns.FormatNumber(u.badges.xp) .. "xp|r"
            end
        end
        local readyPre = u.isReadyTurnIn and "|cff4dff9e[READY]|r " or ""
        local uCh = (u.badges and u.badges.chain) or (u.chainTotal and u.chainTotal >= 2 and {
            pos = u.chainPos or u.chainStep,
            total = u.chainTotal,
            remainingQuests = u.chainRemainingQuests,
            remainingSteps = u.chainRemainingSteps,
        })
        local gpsPre = u.isFriendChain and "|cff66e0ff[Friend]|r " or (u.isGpsReordered and "|cff99ff66[GPS]|r " or "")
        local chainPre = (uCh and (uCh.total or 0) >= 2)
            and string.format("|cff66e0ff[Chain %d/%d]|r ", uCh.pos or 1, uCh.total)
            or (u.isFriendChain and "|cff66e0ff[Chain]|r " or "")
        vm.upcoming[i] = {
            step = u,
            left = string.format("%s%s%s%s%s|r %s", readyPre, gpsPre, chainPre, uam.hex, uam.label, u.title or "Step"),
            right = (u.zoneName or "") .. (tag ~= "" and (" · " .. tag) or ""),
        }
    end

    return vm
end

local function SavePosition()
    if not frame or not ns.db then return end
    local point, _, relPoint, x, y = frame:GetPoint(1)
    if point then
        ns.db.hudPos = { point = point, relPoint = relPoint or point, x = x or 0, y = y or 0 }
    end
end

local function UpdateLockVisual()
    if not frame then return end
    local locked = ns.db and ns.db.hudLocked
    frame.lockBtn:SetButtonText(locked and "Unlock" or "Lock")
    frame:SetMovable(not locked)
end

local function CreateHUDFrame()
    if frame or not CreateFrame then return end

    frame = CreateFrame("Frame", "ForeverQuestPathHUD", UIParent, "BackdropTemplate")
    frame:SetWidth(HUD_WIDTH)
    frame:SetHeight(330)
    frame:SetFrameStrata("MEDIUM")
    frame:SetClampedToScreen(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    ApplyBackdrop(frame, 0.06, 0.07, 0.09, 0.92, 0.22, 0.55, 0.68, 0.85)

    local pos = ns.db and ns.db.hudPos
    if pos and pos.point then
        frame:SetPoint(pos.point, UIParent, pos.relPoint or pos.point, pos.x or 0, pos.y or 0)
    else
        frame:SetPoint("TOPRIGHT", UIParent, "TOPRIGHT", -36, -180)
    end

    frame:SetScript("OnDragStart", function(self)
        if not (ns.db and ns.db.hudLocked) and self.StartMoving then
            self:StartMoving()
        end
    end)
    frame:SetScript("OnDragStop", function(self)
        if self.StopMovingOrSizing then
            self:StopMovingOrSizing()
        end
        SavePosition()
    end)

    -- Header bar
    local header = CreateFrame("Frame", nil, frame, "BackdropTemplate")
    header:SetPoint("TOPLEFT", 1, -1)
    header:SetPoint("TOPRIGHT", -1, -1)
    header:SetHeight(24)
    ApplyBackdrop(header, 0.10, 0.13, 0.18, 1, 0.18, 0.24, 0.30, 1)
    frame.header = header

    local title = header:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    title:SetPoint("LEFT", 8, 0)
    title:SetText("|cff33d9ffForeverQuestPath|r")

    local closeBtn = MakeButton(header, "x", 18, 18)
    closeBtn:SetPoint("RIGHT", -4, 0)
    closeBtn:SetScript("OnClick", function() HUD.SetShown(false) end)

    local collapseBtn = MakeButton(header, "-", 18, 18)
    collapseBtn:SetPoint("RIGHT", closeBtn, "LEFT", -3, 0)
    collapseBtn:SetScript("OnClick", function()
        collapsed = not collapsed
        collapseBtn:SetButtonText(collapsed and "+" or "-")
        HUD.Refresh()
    end)
    frame.collapseBtn = collapseBtn

    local lockBtn = MakeButton(header, "Lock", 40, 18)
    lockBtn:SetPoint("RIGHT", collapseBtn, "LEFT", -3, 0)
    lockBtn:SetScript("OnClick", function()
        ns.db.hudLocked = not ns.db.hudLocked
        UpdateLockVisual()
    end)
    frame.lockBtn = lockBtn

    local routeBtn = MakeButton(header, "Route", 42, 18)
    routeBtn:SetPoint("RIGHT", lockBtn, "LEFT", -3, 0)
    routeBtn:SetScript("OnClick", function()
        if ns.Window and ns.Window.Toggle then
            ns.Window.Toggle()
        end
    end)

    -- Player & Strategy bar
    local statusLine = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    statusLine:SetPoint("TOPLEFT", header, "BOTTOMLEFT", PAD, -6)
    statusLine:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
    statusLine:SetJustifyH("LEFT")
    frame.statusLine = statusLine

    local modeLine = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    modeLine:SetPoint("TOPLEFT", statusLine, "BOTTOMLEFT", 0, -2)
    modeLine:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
    modeLine:SetJustifyH("LEFT")
    frame.modeLine = modeLine

    -- Next Step Card
    local card = CreateFrame("Frame", nil, frame, "BackdropTemplate")
    card:SetPoint("TOPLEFT", modeLine, "BOTTOMLEFT", 0, -6)
    card:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
    card:SetHeight(112)
    ApplyBackdrop(card, 0.09, 0.11, 0.15, 0.95, 0.25, 0.35, 0.45, 1)
    frame.card = card

    local stepTitle = card:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    stepTitle:SetPoint("TOPLEFT", 8, -8)
    stepTitle:SetPoint("TOPRIGHT", -8, -8)
    stepTitle:SetJustifyH("LEFT")
    stepTitle:SetWordWrap(true)
    frame.stepTitle = stepTitle

    local stepWhere = card:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    stepWhere:SetPoint("TOPLEFT", stepTitle, "BOTTOMLEFT", 0, -4)
    stepWhere:SetPoint("RIGHT", card, "RIGHT", -8, 0)
    stepWhere:SetJustifyH("LEFT")
    frame.stepWhere = stepWhere

    local stepTravel = card:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    stepTravel:SetPoint("TOPLEFT", stepWhere, "BOTTOMLEFT", 0, -3)
    stepTravel:SetPoint("RIGHT", card, "RIGHT", -8, 0)
    stepTravel:SetJustifyH("LEFT")
    frame.stepTravel = stepTravel

    local stepBadges = card:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    stepBadges:SetPoint("TOPLEFT", stepTravel, "BOTTOMLEFT", 0, -3)
    stepBadges:SetPoint("RIGHT", card, "RIGHT", -8, 0)
    stepBadges:SetJustifyH("LEFT")
    frame.stepBadges = stepBadges

    local stepWhy = card:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    stepWhy:SetPoint("TOPLEFT", stepBadges, "BOTTOMLEFT", 0, -4)
    stepWhy:SetPoint("RIGHT", card, "RIGHT", -8, 0)
    stepWhy:SetJustifyH("LEFT")
    stepWhy:SetWordWrap(true)
    frame.stepWhy = stepWhy

    -- Action buttons row
    local btnRow = CreateFrame("Frame", nil, frame)
    btnRow:SetPoint("TOPLEFT", card, "BOTTOMLEFT", 0, -6)
    btnRow:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
    btnRow:SetHeight(22)
    frame.btnRow = btnRow

    local bw = (HUD_WIDTH - 2 * PAD - 12) / 4
    local pinBtn = MakeButton(btnRow, "Pin", bw, 20)
    pinBtn:SetPoint("LEFT", 0, 0)
    pinBtn:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    pinBtn:SetScript("OnClick", function(_, button)
        if ns.Waypoint and ns.Waypoint.SyncToCurrentStep then
            ns.Waypoint.SyncToCurrentStep(true, button == "RightButton")
        end
    end)

    local skipBtn = MakeButton(btnRow, "Skip", bw, 20)
    skipBtn:SetPoint("LEFT", pinBtn, "RIGHT", 4, 0)
    skipBtn:SetScript("OnClick", function()
        if ns.State and ns.State.SkipCurrentStep then
            ns.State.SkipCurrentStep()
        end
    end)

    local backBtn = MakeButton(btnRow, "Back", bw, 20)
    backBtn:SetPoint("LEFT", skipBtn, "RIGHT", 4, 0)
    backBtn:SetScript("OnClick", function()
        if ns.State and ns.State.StepBack then
            ns.State.StepBack()
        end
    end)

    local resetBtn = MakeButton(btnRow, "Reset", bw, 20)
    resetBtn:SetPoint("LEFT", backBtn, "RIGHT", 4, 0)
    resetBtn:SetScript("OnClick", function()
        if ns.State and ns.State.ResetProgress then
            ns.State.ResetProgress()
        end
    end)

    -- Upcoming Steps Section
    local upHeader = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    upHeader:SetPoint("TOPLEFT", btnRow, "BOTTOMLEFT", 0, -8)
    upHeader:SetText("|cff99bbccUpcoming Steps|r")
    frame.upHeader = upHeader

    for i = 1, UPCOMING_COUNT do
        local row = CreateFrame("Button", nil, frame, "BackdropTemplate")
        row:SetPoint("TOPLEFT", upHeader, "BOTTOMLEFT", 0, -4 - (i - 1) * 20)
        row:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
        row:SetHeight(18)
        ApplyBackdrop(row, 0.08, 0.09, 0.12, 0.75, 0.16, 0.19, 0.24, 0.75)
        local hl = row:CreateTexture(nil, "HIGHLIGHT")
        hl:SetAllPoints()
        if hl.SetColorTexture then hl:SetColorTexture(1, 1, 1, 0.08) end

        row.left = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
        row.left:SetPoint("LEFT", 5, 0)
        row.left:SetWidth(182)
        row.left:SetJustifyH("LEFT")
        if row.left.SetWordWrap then row.left:SetWordWrap(false) end

        row.right = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
        row.right:SetPoint("RIGHT", -5, 0)
        row.right:SetPoint("LEFT", row.left, "RIGHT", 4, 0)
        row.right:SetJustifyH("RIGHT")
        if row.right.SetWordWrap then row.right:SetWordWrap(false) end

        row:SetScript("OnClick", function(self)
            if self.step and ns.Waypoint and ns.Waypoint.SetForStep then
                ns.Waypoint.SetForStep(self.step, { announce = true })
            end
        end)
        upcomingRows[i] = row
    end

    UpdateLockVisual()
end

function HUD.Refresh()
    local vm = HUD.GetViewModel()
    if not frame then return vm end

    frame.statusLine:SetText(vm.playerLine)
    local devSuffix = (vm.deviation and vm.deviation.deviated) and "  |cffff944d(Deviated)|r" or ""
    frame.modeLine:SetText("Strategy: |cff33d9ff" .. vm.modeLabel .. "|r" .. devSuffix)

    if vm.current then
        frame.stepTitle:SetText(vm.current.actionText .. " " .. vm.current.title)
        frame.stepWhere:SetText(vm.current.whereLine)
        if vm.current.travelNote and vm.current.travelNote ~= "" then
            frame.stepTravel:SetText("|cff66b2ffRoute:|r " .. vm.current.travelNote)
            frame.stepTravel:Show()
        else
            frame.stepTravel:SetText("")
            frame.stepTravel:Hide()
        end
        frame.stepBadges:SetText(vm.current.badgesLine ~= "" and vm.current.badgesLine or "|cff888888Standard progression step|r")
        frame.stepWhy:SetText("|cff99bbccWhy:|r " .. vm.current.whyText)
    else
        frame.stepTitle:SetText("|cff33ff99All route steps complete!|r")
        frame.stepWhere:SetText("Try switching strategy or enabling Dynamic Live Re-Score.")
        frame.stepTravel:SetText("")
        frame.stepTravel:Hide()
        frame.stepBadges:SetText("")
        frame.stepWhy:SetText("")
    end

    if collapsed then
        frame.upHeader:Hide()
        for i = 1, UPCOMING_COUNT do
            upcomingRows[i]:Hide()
        end
        frame:SetHeight(206)
    else
        frame.upHeader:Show()
        local shownCount = 0
        for i = 1, UPCOMING_COUNT do
            local row = upcomingRows[i]
            local item = vm.upcoming[i]
            if item then
                row.step = item.step
                row.left:SetText(item.left)
                row.right:SetText(item.right)
                row:Show()
                shownCount = shownCount + 1
            else
                row.step = nil
                row:Hide()
            end
        end
        frame:SetHeight(228 + math.max(1, shownCount) * 20)
    end

    return vm
end

function HUD.SetShown(show)
    if ns.db then
        ns.db.hudShown = show and true or false
    end
    if not frame and show then
        CreateHUDFrame()
    end
    if frame then
        if show then
            HUD.Refresh()
            frame:Show()
        else
            frame:Hide()
        end
    end
end

function HUD.Toggle()
    local isShown = frame and frame:IsShown() or (ns.db and ns.db.hudShown)
    HUD.SetShown(not isShown)
end

function HUD.Initialize()
    if CreateFrame and UIParent then
        CreateHUDFrame()
        if ns.db and ns.db.hudShown == false then
            frame:Hide()
        else
            HUD.Refresh()
            frame:Show()
        end
    end
    ns.RegisterCallback("STATE_UPDATED", function()
        HUD.Refresh()
    end)
end
