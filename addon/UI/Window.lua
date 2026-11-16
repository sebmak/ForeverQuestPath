--------------------------------------------------------------------------------
-- UI/Window.lua
-- Rules:
--   1. Full Route (with synchronized Ready Turn-In & GPS location/friend-chain
--      splicing, virtual-completion proactive full-chain expansion using zero-
--      clone simCompleted / GetCompletedReadOnly(), [READY]/[GPS]/[CHAIN]/
--      [Chain X/Y] tags, and Ready Turn-Ins counter), 5-Weight Balance
--      Sliders, Max Quest Level Offset Slider (-3..+5, default +2) with
--      prevNonRedMaxLevelOffset preservation across Allow Red (+5) toggles,
--      Multi-Race/Class Selector, Auto-Reorder (GPS), Skip Elite (Solo),
--      Skip Dungeons & Skinning/LW Toggles, Dungeon Unlocks & Special Rewards,
--      and Diagnostics/Settings window (/fqp).
--   2. Uses fixed-viewport pooled ScrollFrame + vertical Slider lists (<= 22
--      row frames per list, O(1) frame allocation), pooled baseRowPool and
--      overlayRowPool row descriptor reuse across rebuilds, preservation of
--      routeStaticSeq across MAX_LEVEL_CHANGED, 9-slot compact dense-array
--      baseRouteCacheByOff (steps, idxs, owners, flags, sparse reasons) keyed on
--      dbQuestGen + routeReadySig separately from plan.immediate / readySig in
--      displayList using integer ACTION_DEDUP_CODE keys, per-generation tab
--      model caching, dirty-checked RenderViewport / BindRouteRow / controls /
--      labels, reusable routeMidParts scratch buffer, startIdx-independent &
--      remainingHorizonCache with parallel qids/qLvs/reqSkills/actCodes/
--      hasPrereqs/isChain arrays + binary search, high-level exclusiveTo &
--      exclusiveTo & completed/outleveled TRAVEL pruning, ordered
--      routeBefore/routeAfter anchor merging, hoisted EvalHorizonFallbackStep
--      keeping the loopPos loop trace-clean with string.find ready-count scan,
--      hSafeElig integer fast path in ComputeRouteTabCache, complete single-
--      quest (incl. DUNGEON_PREP, DUNGEON_RUN, q.exclusiveTo, and
--      State.GetAcceptStepIndexMap) inline filtering, reusable module-level
--      routeScratch context/maps with LuaJIT table.clear-backed WipeMap +
--      bulk-sync aware isCompFn and lazy lookBlocked allocation, unified
--      inline TRAVEL lookahead plus vBlockedAccept cascade short-circuiting,
--      and playerHint forwarding from Window.Refresh -> RefreshRouteTab ->
--      ComputeRouteTabCache -> GetCurrentStep compatible with WoW Forever
--      16001.
--   3. Exposes Window.GetSnapshot() (including routeRows),
--      Window.ShowTab(route|dungeons|rewards|diag), Window.SelectCharacter(),
--      Window.SetMaxLevelOffset(), and programmatic setters for clean testing.
--------------------------------------------------------------------------------

local _, ns = ...

local Window = {}
ns.Window = Window

local WIN_W = 760
local WIN_H = 540
local PAD = 12
local HEADER_H = 28
local TAB_H = 24

local PRESETS = {
    { key = "balanced",      label = "Balanced" },
    { key = "skinning_lw",   label = "Skin & LW" },
    { key = "dungeon_focus", label = "Dungeons" },
    { key = "treasure_bags", label = "Gold & Bags" },
    { key = "fast_xp",       label = "Fast XP" },
}

local BACKDROP_CACHE = {}
local LOCATION_ONLY_REASONS = {
    GPS_MOVED = true,
    GPS_ZONE_CHANGED = true,
    GPS_SYNC = true,
    ZONE_CHANGED_NEW_AREA = true,
    ZONE_CHANGED = true,
    ZONE_CHANGED_INDOORS = true,
    TAXIMAP_OPENED = true,
}

local frame = nil
local activeTab = "route"
local routeFilter = "remaining" -- "remaining" | "all" | "zone"
local dungeonShowAll = false
local rewardFilter = "all" -- "all" | "bags" | "gold" | "rogue" | "skinning_lw"
local rewardShowAllLevels = false

local tabButtons = {}
local tabPanels = {}
local presetButtons = {}
local sliders = {}
local toggleChecks = {}
local wpChecks = {}

local routeStaticSeq = 1
local baseRouteCacheByOff = {}
local function GetBaseRouteSlot(slotKey)
    local s = baseRouteCacheByOff[slotKey]
    if not s then
        s = {
            key = nil,
            sumCore = "",
            count = 0,
            steps = {},
            idxs = {},
            owners = {},
            flags = {},
            reasons = {},
            plan = nil,
        }
        baseRouteCacheByOff[slotKey] = s
    end
    return s
end
local remainingHorizonCache = {
    key = nil,
    stepsRef = nil,
    count = 0,
    indices = {},
    safeElig = {},
    qids = {},
    qLvs = {},
    reqSkills = {},
    actCodes = {},
    hasPrereqs = {},
    isChain = {},
    blockedAccept = {},
}
local routeTabCache = { key = nil, displayList = {}, summaryText = "" }
local baseRowPool = {}
local overlayRowPool = {}
local routeMidParts = {}
local routeScratch = {
    simCompleted = {},
    vActive = {},
    vBlockedAccept = {},
    lookBlocked = {},
    locSeen = {},
    locDone = {},
    injKeys = {},
    injSeen = {},
    ctxQuests = nil,
    ctxCompletedMap = nil,
    ctxActiveMap = nil,
    ctxIsCompFn = nil,
    ctxPlayer = nil,
    ctxPLv = 1,
    ctxAbsMaxLv = 0,
    ctxMaxQuestLv = 0,
    ctxToggles = nil,
    ctxRouteFilter = nil,
    ctxZoneTarget = nil,
    ctxCurReadyKey = nil,
    ctxHasCustomDone = false,
    ctxSteps = nil,
    ctxPkey = nil,
    ctxStartIdx = 1,
    ctxAcceptStepMap = nil,
    ctxRouteBeforeMap = nil,
    ctxRouteAfterMap = nil,
    ctxDecorateMap = nil,
    ctxHasSkipped = false,
    ctxHasQdbGlobal = false,
    ctxSkipEliteNow = false,
    ctxLocFrozen = false,
    ctxMaxTravelLv = 0,
    ctxSatFn = nil,
    ctxBlockOpts = nil,
    ctxBaseSlot = nil,
    ctxBaseCount = 0,
}
local tableClearFn = nil
do
    if type(require) == "function" then
        local okTc, tcFn = pcall(require, "table.clear")
        if okTc and type(tcFn) == "function" then
            tableClearFn = tcFn
        end
    end
end
local function WipeMap(t)
    if tableClearFn then
        tableClearFn(t)
        return
    end
    for k in pairs(t) do
        t[k] = nil
    end
end
local ACTION_DEDUP_CODE = {
    ACCEPT = 1,
    COMPLETE = 2,
    TURN_IN = 3,
    DUNGEON_PREP = 4,
    DUNGEON_RUN = 5,
    TRAVEL = 6,
}
local ComputeRouteTabCache
local dungeonTabCache = { key = nil, entries = {} }
local rewardTabCache = { key = nil, items = {} }
local diagTabCache = { key = nil, infoText = "", entries = {} }
local ROUTE_BLOCK_OPTS = {
    consultQuestie = false,
    checkIsDoable = false,
    useBulkCompletion = true,
    reasonless = true,
}

local function AllocPooledRow(pool, slotIdx, step, idx, ownerIdx, isCurrent, isDone, isSkipped, isBlocked, blockReason, isDynamic, isInjected, isReadyTurnIn, isGpsReordered, isFriendChain)
    local row = pool[slotIdx]
    if not row then
        row = {}
        pool[slotIdx] = row
    end
    row.step = step
    row.idx = idx
    row.ownerIdx = ownerIdx
    row.isCurrent = isCurrent
    row.isDone = isDone
    row.isSkipped = isSkipped
    row.isBlocked = isBlocked
    row.blockReason = blockReason
    row.isDynamic = isDynamic
    row.isInjected = isInjected
    row.isReadyTurnIn = isReadyTurnIn
    row.isGpsReordered = isGpsReordered
    row.isFriendChain = isFriendChain
    return row
end

local function ApplyBackdrop(f, r, g, b, a, br, bg, bb, ba)
    if f and f.SetBackdrop then
        if not f._fqpBackdropInit then
            f._fqpBackdropInit = true
            if not BACKDROP_CACHE.std then
                BACKDROP_CACHE.std = {
                    bgFile = "Interface\\Buttons\\WHITE8X8",
                    edgeFile = "Interface\\Buttons\\WHITE8X8",
                    edgeSize = 1,
                    insets = { left = 1, right = 1, top = 1, bottom = 1 },
                }
            end
            f:SetBackdrop(BACKDROP_CACHE.std)
        end
        f:SetBackdropColor(r, g, b, a)
        f:SetBackdropBorderColor(br or 0.25, bg or 0.30, bb or 0.36, ba or 1)
    end
end

local function MakeButton(parent, text, width, height)
    local b = CreateFrame("Button", nil, parent, "BackdropTemplate")
    b:SetSize(width, height or 22)
    ApplyBackdrop(b, 0.14, 0.16, 0.21, 1, 0.28, 0.33, 0.40, 1)
    local hl = b:CreateTexture(nil, "HIGHLIGHT")
    hl:SetAllPoints()
    if hl.SetColorTexture then hl:SetColorTexture(1, 1, 1, 0.08) end
    local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    fs:SetPoint("CENTER", 0, 0)
    fs:SetText(text)
    b.label = fs
    b.SetSelected = function(self, on)
        local sel = on and true or false
        if self._selected == sel then return end
        self._selected = sel
        if sel then
            ApplyBackdrop(self, 0.16, 0.36, 0.46, 1, 0.25, 0.80, 0.95, 1)
            fs:SetTextColor(1, 0.95, 0.65)
        else
            ApplyBackdrop(self, 0.14, 0.16, 0.21, 1, 0.28, 0.33, 0.40, 1)
            fs:SetTextColor(0.88, 0.90, 0.94)
        end
    end
    return b
end

local function MakeCheckbox(parent, text, width, onToggle)
    local b = CreateFrame("Button", nil, parent)
    b:SetSize(width, 20)
    local box = CreateFrame("Frame", nil, b, "BackdropTemplate")
    box:SetSize(14, 14)
    box:SetPoint("LEFT", 2, 0)
    ApplyBackdrop(box, 0.10, 0.11, 0.14, 1, 0.35, 0.40, 0.48, 1)

    local mark = box:CreateTexture(nil, "OVERLAY")
    mark:SetPoint("TOPLEFT", 2, -2)
    mark:SetPoint("BOTTOMRIGHT", -2, 2)
    if mark.SetColorTexture then
        mark:SetColorTexture(0.20, 0.85, 0.95, 1)
    end

    local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    fs:SetPoint("LEFT", box, "RIGHT", 6, 0)
    fs:SetPoint("RIGHT", b, "RIGHT", -2, 0)
    fs:SetJustifyH("LEFT")
    fs:SetText(text)
    b.label = fs
    b._labelText = text

    b.SetLabel = function(self, t)
        if self._labelText == t then return end
        self._labelText = t
        fs:SetText(t)
    end
    b.SetChecked = function(self, on)
        local chk = on and true or false
        if self.checked == chk then return end
        self.checked = chk
        if chk then mark:Show() else mark:Hide() end
    end
    b:SetScript("OnClick", function(self)
        local nextVal = not self.checked
        self:SetChecked(nextVal)
        if onToggle then onToggle(nextVal) end
    end)
    return b
end

local function MakeSlider(parent, label, width, minVal, maxVal, step, onChange, formatValue)
    local holder = CreateFrame("Frame", nil, parent)
    holder:SetSize(width, 36)

    local title = holder:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    title:SetPoint("TOPLEFT", 0, 0)
    title:SetText(label)

    local valText = holder:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    valText:SetPoint("TOPRIGHT", 0, 0)
    valText:SetText(formatValue and formatValue(minVal) or "0")

    local s = CreateFrame("Slider", nil, holder, "BackdropTemplate")
    s:SetPoint("TOPLEFT", 0, -16)
    s:SetPoint("TOPRIGHT", 0, -16)
    s:SetHeight(14)
    s:SetOrientation("HORIZONTAL")
    ApplyBackdrop(s, 0.09, 0.10, 0.13, 1, 0.25, 0.30, 0.36, 1)
    if s.SetThumbTexture then
        s:SetThumbTexture("Interface\\Buttons\\UI-SliderBar-Button-Horizontal")
    end
    s:SetMinMaxValues(minVal, maxVal)
    s:SetValueStep(step)
    if s.SetObeyStepOnDrag then s:SetObeyStepOnDrag(true) end

    local filling = false
    holder._lastVal = nil
    holder._lastTxt = nil
    local function FormatSliderVal(v)
        v = math.floor(v / step + 0.5) * step
        if v == 0 then v = 0 end
        return v, (formatValue and formatValue(v) or tostring(v))
    end

    s:SetScript("OnValueChanged", function(_, raw)
        local v, txt = FormatSliderVal(raw)
        holder._lastVal = v
        if holder._lastTxt ~= txt then
            holder._lastTxt = txt
            valText:SetText(txt)
        end
        if filling then return end
        if onChange then onChange(v) end
    end)

    holder.SetSliderValue = function(self, raw)
        local qv = math.floor(raw / step + 0.5) * step
        if qv == 0 then qv = 0 end
        if (not formatValue) and self._lastVal == qv then
            return
        end
        local txt = formatValue and formatValue(qv) or tostring(qv)
        if self._lastVal ~= qv then
            filling = true
            self._lastVal = qv
            self._lastTxt = txt
            s:SetValue(qv)
            valText:SetText(txt)
            filling = false
        elseif self._lastTxt ~= txt then
            self._lastTxt = txt
            valText:SetText(txt)
        end
    end
    holder.valText = valText
    holder.slider = s
    return holder
end

local function MakeScrollList(parent, width, height, rowHeight, poolSize, createRowFn, bindRowFn)
    local container = CreateFrame("Frame", nil, parent, "BackdropTemplate")
    container:SetSize(width, height)
    ApplyBackdrop(container, 0.05, 0.06, 0.08, 0.9, 0.18, 0.22, 0.28, 1)

    local scroll = CreateFrame("ScrollFrame", nil, container)
    scroll:SetPoint("TOPLEFT", 4, -4)
    scroll:SetPoint("BOTTOMRIGHT", -18, 4)
    if scroll.EnableMouseWheel then scroll:EnableMouseWheel(true) end

    local child = CreateFrame("Frame", nil, scroll)
    child:SetSize(width - 22, 1)
    scroll:SetScrollChild(child)

    local bar = CreateFrame("Slider", nil, container, "BackdropTemplate")
    bar:SetPoint("TOPRIGHT", -3, -4)
    bar:SetPoint("BOTTOMRIGHT", -3, 4)
    bar:SetWidth(10)
    bar:SetOrientation("VERTICAL")
    ApplyBackdrop(bar, 0.08, 0.09, 0.12, 1, 0.20, 0.24, 0.30, 1)
    if bar.SetThumbTexture then
        bar:SetThumbTexture("Interface\\Buttons\\UI-ScrollBar-Knob")
        local thumb = bar.GetThumbTexture and bar:GetThumbTexture()
        if thumb and thumb.SetSize then thumb:SetSize(14, 22) end
    end
    bar:SetMinMaxValues(0, 0)
    local rH = rowHeight or 24
    bar:SetValueStep(rH)
    bar:SetValue(0)

    local viewH = height - 8
    local maxPool = poolSize or math.min(22, math.ceil(viewH / rH) + 2)
    local rowW = math.max(80, (width - 22) - 4)

    local pool = {}
    if createRowFn then
        for i = 1, maxPool do
            local row = createRowFn(child, rowW, rH)
            row._shown = false
            row:Hide()
            pool[i] = row
        end
    end

    local itemsRef = {}
    local function RenderViewport()
        if #pool == 0 or not bindRowFn then return end
        local total = #itemsRef
        local scrollOffset = tonumber(bar:GetValue()) or 0
        local maxScroll = math.max(0, total * rH + 4 - viewH)
        if scrollOffset < 0 then scrollOffset = 0 end
        if scrollOffset > maxScroll then scrollOffset = maxScroll end

        local firstIdx = math.max(1, math.floor(scrollOffset / rH) + 1)
        for slot = 1, #pool do
            local row = pool[slot]
            local itemIdx = firstIdx + slot - 1
            local item = itemsRef[itemIdx]
            if item then
                if row._itemIdx ~= itemIdx or row._rowW ~= rowW then
                    row._itemIdx = itemIdx
                    row._rowW = rowW
                    row:ClearAllPoints()
                    row:SetPoint("TOPLEFT", child, "TOPLEFT", 2, -(2 + (itemIdx - 1) * rH))
                    row:SetWidth(rowW)
                end
                bindRowFn(row, item, itemIdx, rowW)
                if not row._shown then
                    row._shown = true
                    row:Show()
                end
            else
                if row._shown ~= false then
                    row._shown = false
                    row:Hide()
                end
            end
        end
    end

    local updatingScroll = false
    bar:SetScript("OnValueChanged", function(_, v)
        if scroll.SetVerticalScroll then scroll:SetVerticalScroll(v) end
        if not updatingScroll then
            RenderViewport()
        end
    end)
    scroll:SetScript("OnMouseWheel", function(_, delta)
        local cur = bar:GetValue() or 0
        local _, maxV = bar:GetMinMaxValues()
        local nextV = math.max(0, math.min(maxV or 0, cur - delta * (rH * 1.5)))
        bar:SetValue(nextV)
    end)

    container.scroll = scroll
    container.child = child
    container.bar = bar
    container.pool = pool
    container.RenderViewport = RenderViewport
    container.SetItems = function(_, items, resetScroll)
        itemsRef = items or {}
        local contentHeight = #itemsRef * rH + 4
        child:SetHeight(math.max(contentHeight, viewH))
        local maxScroll = math.max(0, contentHeight - viewH)
        updatingScroll = true
        bar:SetMinMaxValues(0, maxScroll)
        local cur = resetScroll and 0 or (bar:GetValue() or 0)
        if cur > maxScroll then cur = maxScroll end
        if cur < 0 then cur = 0 end
        bar:SetValue(cur)
        if scroll.SetVerticalScroll then scroll:SetVerticalScroll(cur) end
        updatingScroll = false
        if maxScroll > 0 then bar:Show() else bar:Hide() end
        RenderViewport()
    end
    container.UpdateHeight = function(_, contentHeight)
        child:SetHeight(math.max(contentHeight, viewH))
        local maxScroll = math.max(0, contentHeight - viewH)
        bar:SetMinMaxValues(0, maxScroll)
        if (bar:GetValue() or 0) > maxScroll then
            bar:SetValue(maxScroll)
        end
        if maxScroll > 0 then bar:Show() else bar:Hide() end
    end
    return container
end

--------------------------------------------------------------------------------
-- Snapshot / ViewModel (shared by rendering and automated verification)
--------------------------------------------------------------------------------

function Window.GetSnapshot()
    local player = ns.State.GetPlayerInfo()
    local currentStep, meta = ns.State.GetCurrentStep()
    local weights = ns.GetActiveWeights()
    local dungeons = ns.Scorer.GetDungeonsNearLevel(player.level, dungeonShowAll)
    local rewards = ns.Scorer.GetSpecialRewards(player, rewardFilter, 25, rewardShowAllLevels)
    local toggles = (ns.charDB and ns.charDB.toggles) or {}
    local rCache = ComputeRouteTabCache and ComputeRouteTabCache() or routeTabCache

    return {
        activeTab = activeTab,
        player = player,
        currentStep = currentStep,
        meta = meta,
        weights = weights,
        dungeons = dungeons,
        specialRewards = rewards,
        maxLevelOffset = ns.State.GetEffectiveMaxLevelOffset and ns.State.GetEffectiveMaxLevelOffset() or 2,
        blockedSteps = ns.State.GetBlockedSteps and ns.State.GetBlockedSteps() or {},
        readyTurnIns = ns.State.GetReadyTurnInQuests and ns.State.GetReadyTurnInQuests() or {},
        readyTurnInCount = ns.State.GetReadyTurnInCount and ns.State.GetReadyTurnInCount() or 0,
        locationReorder = (toggles.locationReorder ~= false),
        skipEliteSolo = (toggles.skipEliteSolo ~= false),
        skipDungeons = (toggles.skipDungeons ~= false),
        inGroup = (player and player.inGroup == true),
        locationPlanMode = meta and meta.locationPlan and meta.locationPlan.mode or nil,
        routeRows = (rCache and rCache.displayList) or {},
        routeSummary = (rCache and rCache.summaryText) or "",
    }
end

--------------------------------------------------------------------------------
-- Pooled Row Builders & Binders
--------------------------------------------------------------------------------

local function CreateRouteRow(child, _, rowH)
    local row = CreateFrame("Button", nil, child, "BackdropTemplate")
    row:SetHeight(rowH - 2)
    local hl = row:CreateTexture(nil, "HIGHLIGHT")
    hl:SetAllPoints()
    if hl.SetColorTexture then hl:SetColorTexture(1, 1, 1, 0.08) end

    row.left = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.left:SetPoint("LEFT", 6, 0)
    row.left:SetWidth(355)
    row.left:SetJustifyH("LEFT")
    if row.left.SetWordWrap then row.left:SetWordWrap(false) end

    row.mid = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    row.mid:SetPoint("LEFT", row.left, "RIGHT", 6, 0)
    row.mid:SetWidth(185)
    row.mid:SetJustifyH("LEFT")
    if row.mid.SetWordWrap then row.mid:SetWordWrap(false) end

    row.pin = MakeButton(row, "Pin", 38, 18)
    row.pin:SetPoint("RIGHT", -4, 0)
    row.pin:SetScript("OnClick", function(self)
        local st = self:GetParent().step
        if st and ns.Waypoint and ns.Waypoint.SetForStep then
            ns.Waypoint.SetForStep(st, { announce = true })
        end
    end)

    row:SetScript("OnClick", function(self)
        if not self.isDynamic and self.stepIdx then
            ns.State.JumpToStep(self.stepIdx)
        elseif self.step then
            ns.Waypoint.SetForStep(self.step, { announce = true })
        end
    end)
    return row
end

local function BindRouteRow(row, item)
    local st = item.step
    local am = ns.GetActionMeta(st.action)
    local isReady = (item.isReadyTurnIn == true) or (st.isReadyTurnIn == true)
    local isGps = (item.isGpsReordered == true) or (st.isGpsReordered == true)
    local isFriend = (item.isFriendChain == true) or (st.isFriendChain == true)
    local statusTag = (item.isCurrent and isReady and "|cff4dff9e[READY]|r ")
        or (item.isCurrent and "|cff33ff99[NEXT]|r ")
        or (isReady and "|cff4dff9e[READY]|r ")
        or (item.isDone and "|cff66aa66[DONE]|r ")
        or (item.isBlocked and "|cffff6644[LOCKED]|r ")
        or (item.isSkipped and "|cff888888[SKIP]|r ")
        or "|cff666666[ ]|r "
    local gpsTag = isFriend and "|cff66e0ff[CHAIN]|r " or (isGps and "|cff99ff66[GPS]|r " or "")

    local bgKind = item.isCurrent and "curr" or (isReady and "ready" or ((isGps or isFriend) and "gps" or "norm"))
    if row._bgKind ~= bgKind then
        row._bgKind = bgKind
        if item.isCurrent then
            ApplyBackdrop(row, 0.12, 0.24, 0.30, 0.95, 0.25, 0.80, 0.95, 1)
        elseif isReady then
            ApplyBackdrop(row, 0.08, 0.18, 0.14, 0.90, 0.22, 0.72, 0.45, 0.9)
        elseif isGps or isFriend then
            ApplyBackdrop(row, 0.08, 0.14, 0.18, 0.88, 0.25, 0.58, 0.72, 0.9)
        else
            ApplyBackdrop(row, 0.08, 0.09, 0.12, 0.80, 0.16, 0.18, 0.22, 0.8)
        end
    end

    row.step = st
    row.stepIdx = item.idx
    row.isDynamic = item.isDynamic or item.isInjected

    local b = st.badges
    local ch = (b and b.chain) or (st.chainTotal and st.chainTotal >= 2 and {
        pos = st.chainPos or st.chainStep,
        total = st.chainTotal,
        remainingQuests = st.chainRemainingQuests,
        remainingSteps = st.chainRemainingSteps,
    }) or (ns.State and ns.State.GetChainProgressInfo and st.questId and ns.State.GetChainProgressInfo(st.questId, nil, nil, st.action))
    local chPos = ch and ch.pos or nil
    local chTot = ch and ch.total or nil
    local chRemQ = ch and ch.remainingQuests or nil
    local chRemS = ch and ch.remainingSteps or nil
    if row._boundStep == st
        and row._boundIdx == item.idx
        and row._boundStatus == statusTag
        and row._boundGps == gpsTag
        and row._boundChainPos == chPos
        and row._boundChainTot == chTot
        and row._boundChainRemQ == chRemQ
        and row._boundChainRemS == chRemS then
        return
    end
    row._boundStep = st
    row._boundIdx = item.idx
    row._boundStatus = statusTag
    row._boundGps = gpsTag
    row._boundChainPos = chPos
    row._boundChainTot = chTot
    row._boundChainRemQ = chRemQ
    row._boundChainRemS = chRemS

    local numTag = item.idx and ("#" .. tostring(item.idx)) or "+"
    local chainTag = (ch and (ch.total or 0) >= 2) and string.format("|cff66e0ff[Chain %d/%d]|r ", ch.pos or 1, ch.total) or ""
    row.left:SetText(string.format("%s%s%s|cff888888%s|r %s[%s]|r %s", statusTag, gpsTag, chainTag, numTag, am.hex, am.label, st.title or "Step"))
    local mCount = 1
    routeMidParts[1] = st.zoneName or ""
    if ch and (ch.total or 0) >= 2 then
        local rq = tonumber(ch.remainingQuests) or 0
        local rs = tonumber(ch.remainingSteps) or 0
        mCount = mCount + 1
        if rq > 0 or rs > 0 then
            routeMidParts[mCount] = string.format("%dq/%ds left", rq, rs)
        else
            routeMidParts[mCount] = "Final"
        end
    end
    if b then
        if b.xp and b.xp > 0 then
            mCount = mCount + 1
            routeMidParts[mCount] = "+" .. ns.FormatNumber(b.xp) .. "xp"
        end
        if b.bagSlots and b.bagSlots > 0 then
            mCount = mCount + 1
            routeMidParts[mCount] = b.bagSlots .. "Bag"
        end
        if b.isLeatherworking then
            mCount = mCount + 1
            routeMidParts[mCount] = "|cffb87333[LW]|r"
        elseif b.isSkinning or (b.skinningScore and b.skinningScore >= 30) then
            mCount = mCount + 1
            routeMidParts[mCount] = "|cffd9a05b[Skin]|r"
        end
        if b.unlocksDungeon then
            mCount = mCount + 1
            routeMidParts[mCount] = "Unlock"
        end
    end
    row.mid:SetText(table.concat(routeMidParts, " · ", 1, mCount))
end

local function CreateDungeonRow(child, rowW)
    local row = CreateFrame("Frame", nil, child, "BackdropTemplate")
    row:SetHeight(20)
    row.left = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.left:SetPoint("LEFT", 4, 0)
    row.left:SetWidth(rowW - 90)
    row.left:SetJustifyH("LEFT")
    if row.left.SetWordWrap then row.left:SetWordWrap(false) end

    row.right = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    row.right:SetPoint("RIGHT", -46, 0)
    row.right:SetJustifyH("RIGHT")

    row.pin = MakeButton(row, "Pin", 36, 16)
    row.pin:SetPoint("RIGHT", -3, 0)
    row.pin:SetScript("OnClick", function(self)
        local pt = self:GetParent().pinTarget
        if pt and ns.Waypoint and ns.Waypoint.SetTarget then
            ns.Waypoint.SetTarget(pt, { announce = true })
        end
    end)
    return row
end

local function BindDungeonRow(row, item)
    local bgKind = item.kind == "header" and "header" or "sub"
    if row._bgKind ~= bgKind then
        row._bgKind = bgKind
        if bgKind == "header" then
            ApplyBackdrop(row, 0.13, 0.15, 0.21, 0.95, 0.30, 0.36, 0.48, 1)
        else
            ApplyBackdrop(row, 0.07, 0.08, 0.11, 0.65, 0.12, 0.14, 0.18, 0.6)
        end
    end
    row.left:SetText(item.left)
    row.right:SetText(item.right or "")
    row.pinTarget = item.pinTarget
    if item.pinTarget then row.pin:Show() else row.pin:Hide() end
end

local function CreateRewardRow(child, rowW)
    local row = CreateFrame("Frame", nil, child, "BackdropTemplate")
    row:SetHeight(34)
    row.top = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.top:SetPoint("TOPLEFT", 5, -3)
    row.top:SetWidth(rowW - 52)
    row.top:SetJustifyH("LEFT")
    if row.top.SetWordWrap then row.top:SetWordWrap(false) end

    row.bot = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    row.bot:SetPoint("TOPLEFT", row.top, "BOTTOMLEFT", 0, -2)
    row.bot:SetWidth(rowW - 52)
    row.bot:SetJustifyH("LEFT")
    if row.bot.SetWordWrap then row.bot:SetWordWrap(false) end

    row.pin = MakeButton(row, "Pin", 36, 20)
    row.pin:SetPoint("RIGHT", -4, 0)
    row.pin:SetScript("OnClick", function(self)
        local pt = self:GetParent().pinTarget
        if pt and ns.Waypoint and ns.Waypoint.SetTarget then
            ns.Waypoint.SetTarget(pt, { announce = true })
        end
    end)
    ApplyBackdrop(row, 0.08, 0.09, 0.13, 0.85, 0.18, 0.22, 0.28, 0.9)
    return row
end

local function BindRewardRow(row, rw)
    local badges = {}
    if rw.bagSlots > 0 then badges[#badges + 1] = string.format("|cff33ff99%d-Slot Bag|r", rw.bagSlots) end
    local bis = tonumber(rw.classScore) or tonumber(rw.rogueScore) or 0
    if bis >= 30 then
        badges[#badges + 1] = string.format("|cfffff569%s BiS %d|r", rw.classLabel or "Class", math.floor(bis + 0.5))
    end
    if rw.goldCopper >= 4000 then badges[#badges + 1] = ns.FormatMoney(rw.goldCopper) end
    if rw.isLeatherworking then badges[#badges + 1] = "|cffb87333[LW]|r" end
    if rw.isSkinning or (rw.skinningScore and rw.skinningScore >= 30) then
        badges[#badges + 1] = string.format("|cffd9a05b[Skin %d]|r", math.floor((rw.skinningScore or 0) + 0.5))
    end
    local stLabel = (rw.status == "ready" and "|cff33ffcc[TurnIn]|r")
        or (rw.status == "active" and "|cffc080ff[In Log]|r")
        or (rw.prereqsMet and "|cff44ee77[Open]|r")
        or "|cffff7755[Prereq]|r"

    row.top:SetText(string.format("%s %s |cff888888[%d]|r", stLabel, rw.name, rw.questLevel or 0))
    row.bot:SetText(string.format("%s (%s) · %s", rw.zoneName or "?", ns.FormatCoords(rw.x, rw.y), table.concat(badges, "  ")))
    row.pinTarget = (rw.x and rw.y) and {
        zoneId = rw.zoneId,
        zoneName = rw.zoneName,
        x = rw.x,
        y = rw.y,
        title = rw.name,
        npcName = rw.npcName,
    } or nil
    if row.pinTarget then row.pin:Show() else row.pin:Hide() end
end

local function CreateDiagRow(child)
    local row = CreateFrame("Frame", nil, child, "BackdropTemplate")
    row:SetHeight(24)
    row.left = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.left:SetPoint("LEFT", 6, 0)
    row.left:SetWidth(350)
    row.left:SetJustifyH("LEFT")
    if row.left.SetWordWrap then row.left:SetWordWrap(false) end

    row.right = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    row.right:SetPoint("RIGHT", -6, 0)
    row.right:SetPoint("LEFT", row.left, "RIGHT", 6, 0)
    row.right:SetJustifyH("RIGHT")
    return row
end

local function BindDiagRow(row, item)
    local bgKind = item.isBlocked and "blocked" or "norm"
    if row._bgKind ~= bgKind then
        row._bgKind = bgKind
        if item.isBlocked then
            ApplyBackdrop(row, 0.16, 0.08, 0.08, 0.90, 0.45, 0.20, 0.18, 0.95)
        else
            ApplyBackdrop(row, 0.08, 0.09, 0.12, 0.85, 0.16, 0.18, 0.24, 0.9)
        end
    end
    row.left:SetText(item.left)
    row.right:SetText(item.right)
end

--------------------------------------------------------------------------------
-- Tab Refresh Implementations
--------------------------------------------------------------------------------

local function WeightSig(w)
    return string.format("%d:%d:%d:%d:%d",
        tonumber(w.xp) or 0,
        tonumber(w.travel) or 0,
        tonumber(w.dungeon) or 0,
        tonumber(w.reward) or 0,
        tonumber(w.skinning) or 0)
end

local function SimRouteStep(rSt)
    if not rSt then return end
    local vActive = routeScratch.vActive
    local simCompleted = routeScratch.simCompleted
    local act = rSt.action or "ACCEPT"
    if rSt.questId then
        if act == "ACCEPT" or (act == "DUNGEON_PREP" and rSt.prepMode == "accept") then
            vActive[rSt.questId] = true
        elseif act == "TURN_IN" or act == "DUNGEON_RUN" then
            vActive[rSt.questId] = nil
            simCompleted[rSt.questId] = true
        end
    elseif type(rSt.questIds) == "table" then
        for _, rqid in ipairs(rSt.questIds) do
            if act == "ACCEPT" or (act == "DUNGEON_PREP" and rSt.prepMode == "accept") then
                vActive[rqid] = true
            elseif act == "TURN_IN" or act == "DUNGEON_RUN" then
                vActive[rqid] = nil
                simCompleted[rqid] = true
            end
        end
    end
end

local function MarkBlockedAcceptStep(s)
    if not s then return end
    local sAct = s.action or "ACCEPT"
    if sAct ~= "ACCEPT" and not (sAct == "DUNGEON_PREP" and s.prepMode == "accept") then
        return
    end
    local vBlockedAccept = routeScratch.vBlockedAccept
    if s.questId and not s.questIds then
        vBlockedAccept[s.questId] = true
    elseif type(s.questIds) == "table" then
        for j = 1, #s.questIds do
            vBlockedAccept[s.questIds[j]] = true
        end
    end
end

local function AppendBaseInjected(list, ownerIdx)
    if not list then return end
    local rs = routeScratch
    local decFn = ns.State.DecorateStepChain
    local rFilter = rs.ctxRouteFilter
    local zTarget = rs.ctxZoneTarget
    local cPlayer = rs.ctxPlayer
    local cToggles = rs.ctxToggles
    local bSlot = rs.ctxBaseSlot
    local bSteps = bSlot.steps
    local bIdxs = bSlot.idxs
    local bOwners = bSlot.owners
    local bFlags = bSlot.flags
    local ownerVal = ownerIdx or false
    local baseCount = rs.ctxBaseCount
    for i = 1, #list do
        local rSt = list[i]
        SimRouteStep(rSt)
        if rFilter ~= "zone" or rSt.zoneId == zTarget then
            local decSt = (decFn and decFn(rSt, cPlayer, cToggles)) or rSt
            baseCount = baseCount + 1
            bSteps[baseCount] = decSt
            bIdxs[baseCount] = false
            bOwners[baseCount] = ownerVal
            bFlags[baseCount] = 56
        end
    end
    rs.ctxBaseCount = baseCount
end

local function IsQuestAlwaysBlocked(qid)
    if not qid then return false end
    local rs = routeScratch
    local completedMap = rs.ctxCompletedMap
    local activeMap = rs.ctxActiveMap
    if (completedMap[qid] == true) or activeMap[qid] ~= nil then
        return false
    end
    local quests = rs.ctxQuests
    local q = quests[qid]
    if not q then
        return false
    end
    local pLv = rs.ctxPLv
    local absMaxQuestLv = rs.ctxAbsMaxLv
    local cPlayer = rs.ctxPlayer
    local overOrInelig = ((tonumber(q.questLevel) or 0) > absMaxQuestLv)
        or ((tonumber(q.minLevel) or 1) > pLv)
        or (not ns.State.IsEligibleForPlayer(qid, cPlayer))
    if not overOrInelig then
        return false
    end
    local isCompFn = rs.ctxIsCompFn
    local ex = q.exclusiveTo
    if ex then
        for j = 1, #ex do
            local oid = ex[j]
            if oid ~= qid then
                if (completedMap[oid] == true) or activeMap[oid] ~= nil or (isCompFn ~= nil and isCompFn(oid, true)) then
                    return false
                end
                local oq = quests[oid]
                if oq and ((tonumber(oq.questLevel) or 0) <= absMaxQuestLv)
                    and ((tonumber(oq.minLevel) or 1) <= pLv)
                    and ns.State.IsEligibleForPlayer(oid, cPlayer) then
                    return false
                end
            end
        end
    end
    if isCompFn ~= nil and isCompFn(qid, true) then
        return false
    end
    return true
end

local function IsStepAlwaysBlocked(s)
    if not s then return false end
    local sAct = s.action or "ACCEPT"
    if sAct ~= "ACCEPT" and sAct ~= "COMPLETE" and sAct ~= "TURN_IN"
        and sAct ~= "DUNGEON_PREP" and sAct ~= "DUNGEON_RUN" then
        return false
    end
    local qids = s.questIds
    if type(qids) == "table" and #qids > 0 then
        for j = 1, #qids do
            if not IsQuestAlwaysBlocked(qids[j]) then
                return false
            end
        end
        return true
    elseif s.questId then
        return IsQuestAlwaysBlocked(s.questId)
    end
    return false
end

local function IsStepAlwaysNonActionableRem(s)
    if IsStepAlwaysBlocked(s) then return true end
    if not (s and s.questId and (not s.questIds)) then return false end
    local nAct = s.action or "ACCEPT"
    if nAct ~= "ACCEPT" and nAct ~= "COMPLETE" and nAct ~= "TURN_IN"
        and nAct ~= "DUNGEON_PREP" and nAct ~= "DUNGEON_RUN" then
        return false
    end
    local rs = routeScratch
    local nqid = s.questId
    local nq = rs.ctxQuests[nqid]
    if not nq or nq.exclusiveTo then return false end
    if rs.ctxCompletedMap[nqid] == true then return true end
    local isCompFn = rs.ctxIsCompFn
    if rs.ctxActiveMap[nqid] ~= nil or (isCompFn ~= nil and isCompFn(nqid, true)) then return false end
    if rs.ctxSkipEliteNow and (nAct ~= "DUNGEON_PREP" and nAct ~= "DUNGEON_RUN") and nq.isGroup == true and not nq.isDungeon then
        return true
    end
    if rs.ctxSkipDungeonsNow and (nAct == "DUNGEON_PREP" or nAct == "DUNGEON_RUN" or nq.isDungeon == true) then
        return true
    end
    if type(s.expectedLevel) == "number" then
        local pLv = rs.ctxPLv
        local isDung = (nq.isDungeon == true) or (nAct == "DUNGEON_RUN")
        if (not isDung) and ((tonumber(nq.questLevel or nq.minLevel) or s.expectedLevel) <= pLv - 7) then
            return true
        elseif pLv >= s.expectedLevel + 2 and ns.State.IsStepOutleveled(s, rs.ctxPlayer) then
            return true
        end
    end
    return false
end

local function IsQuestHighBlocked(qid)
    local rs = routeScratch
    local quests = rs.ctxQuests
    local completedMap = rs.ctxCompletedMap
    if not (qid and quests and completedMap) then return false end
    local activeMap = rs.ctxActiveMap
    local simCompleted = rs.simCompleted
    local vActive = rs.vActive
    if completedMap[qid] or activeMap[qid] ~= nil or simCompleted[qid] or vActive[qid] then
        return false
    end
    local q = quests[qid]
    if not q or (tonumber(q.questLevel) or 0) <= rs.ctxMaxQuestLv then
        return false
    end
    local ex = q.exclusiveTo
    if ex then
        for j = 1, #ex do
            local oid = ex[j]
            if completedMap[oid] or activeMap[oid] ~= nil or simCompleted[oid] or vActive[oid] then
                return false
            end
        end
    end
    return true
end

local function IsStepHighBlocked(s)
    if routeScratch.ctxHasCustomDone or (not s) then return false end
    local sAct = s.action or "ACCEPT"
    if sAct ~= "ACCEPT" and sAct ~= "COMPLETE" and sAct ~= "TURN_IN"
        and sAct ~= "DUNGEON_PREP" and sAct ~= "DUNGEON_RUN" then
        return false
    end
    local qids = s.questIds
    if type(qids) == "table" and #qids > 0 then
        for j = 1, #qids do
            if not IsQuestHighBlocked(qids[j]) then
                return false
            end
        end
        return true
    elseif s.questId then
        return IsQuestHighBlocked(s.questId)
    end
    return false
end

local function EvalHorizonFallbackStep(idx, st, isSuppressed, isSafeHorizon, baseCount)
    local rs = routeScratch
    local routeFilter = rs.ctxRouteFilter
    local hasCustomDone = rs.ctxHasCustomDone
    local hasSkipped = rs.ctxHasSkipped
    local quests = rs.ctxQuests
    local completedMap = rs.ctxCompletedMap
    local activeMap = rs.ctxActiveMap
    local isCompFn = rs.ctxIsCompFn
    local player = rs.ctxPlayer
    local pLv = rs.ctxPLv
    local maxQuestLv = rs.ctxMaxQuestLv
    local t = rs.ctxToggles
    local zoneTarget = rs.ctxZoneTarget
    local steps = rs.ctxSteps
    local pkey = rs.ctxPkey
    local startIdx = rs.ctxStartIdx
    local acceptStepMap = rs.ctxAcceptStepMap
    local routeBeforeMap = rs.ctxRouteBeforeMap
    local routeAfterMap = rs.ctxRouteAfterMap
    local decorateMap = rs.ctxDecorateMap
    local hasQdbGlobal = rs.ctxHasQdbGlobal
    local locFrozen = rs.ctxLocFrozen
    local maxTravelLv = rs.ctxMaxTravelLv
    local satFn = rs.ctxSatFn
    local blockOpts = rs.ctxBlockOpts
    local simCompleted = rs.simCompleted
    local vActive = rs.vActive
    local vBlockedAccept = rs.vBlockedAccept
    local bSlot = rs.ctxBaseSlot
    local decFn = ns.State.DecorateStepChain

    local fastBlockedHigh = false
    if routeFilter ~= "all" and (not isSuppressed) and (not hasCustomDone) and st and (st.action ~= "TRAVEL") then
        fastBlockedHigh = IsStepHighBlocked(st)
    end

    if fastBlockedHigh then
        MarkBlockedAcceptStep(st)
        if routeBeforeMap and routeBeforeMap[idx] then
            rs.ctxBaseCount = baseCount
            AppendBaseInjected(routeBeforeMap[idx], idx)
            baseCount = rs.ctxBaseCount
        end
        if routeAfterMap and routeAfterMap[idx] then
            rs.ctxBaseCount = baseCount
            AppendBaseInjected(routeAfterMap[idx], idx)
            baseCount = rs.ctxBaseCount
        end
        return baseCount
    end

    local skipped = hasSkipped and ns.State.IsStepSkipped(st, idx, pkey) or false
    local outleveled = false
    local done = false
    local blockCode, blockReason = nil, nil
    local fastSingleHandled = false
    if (not hasCustomDone) and st and st.questId and (not st.questIds) and quests and completedMap then
        local stAct = st.action or "ACCEPT"
        if stAct == "ACCEPT" or stAct == "COMPLETE" or stAct == "TURN_IN" or stAct == "DUNGEON_PREP" or stAct == "DUNGEON_RUN" then
            local qid = st.questId
            local q = quests[qid]
            if q then
                fastSingleHandled = true
                local actE = activeMap[qid]
                local isDoneQ = (completedMap[qid] == true) or (isCompFn ~= nil and isCompFn(qid, true))
                local isAcceptLike = (stAct == "ACCEPT") or (stAct == "DUNGEON_PREP" and st.prepMode == "accept")
                local isCompLike = (stAct == "COMPLETE") or (stAct == "DUNGEON_RUN")
                local liveExcluded, virtExcluded = false, false
                local ex = q.exclusiveTo
                if ex then
                    for j = 1, #ex do
                        local oid = ex[j]
                        if oid ~= qid then
                            if (completedMap[oid] == true) or activeMap[oid] ~= nil or (isCompFn ~= nil and isCompFn(oid, true)) then
                                liveExcluded = true
                                break
                            elseif simCompleted[oid] or vActive[oid] then
                                virtExcluded = true
                            end
                        end
                    end
                end
                local isSafeElig = isSafeHorizon and (actE == nil)
                if (not isSafeElig) and (not skipped) and routeFilter == "remaining" then
                    if type(st.expectedLevel) == "number" and actE == nil then
                        local isDung = (q.isDungeon == true) or (stAct == "DUNGEON_RUN")
                        if (not isDung) and ((tonumber(q.questLevel or q.minLevel) or st.expectedLevel) <= pLv - 7) then
                            outleveled = true
                        elseif pLv >= st.expectedLevel + 2 then
                            outleveled = ns.State.IsStepOutleveled(st, player)
                        end
                    end
                end
                if (not skipped) and (not outleveled) then
                    if liveExcluded then
                        done = true
                    elseif isAcceptLike then
                        done = isDoneQ or (actE ~= nil)
                    elseif isCompLike then
                        done = isDoneQ or (actE ~= nil and actE.isComplete == true and not actE.isFailed)
                    else
                        done = isDoneQ
                    end
                    if (routeFilter == "all") or ((not isSuppressed) and (not done)) then
                        local isEliteSkip = rs.ctxSkipEliteNow and (stAct ~= "DUNGEON_PREP" and stAct ~= "DUNGEON_RUN")
                            and (not q.isDungeon) and ((q.isGroup == true) or (actE ~= nil and actE.isElite == true))
                        local isDungSkip = rs.ctxSkipDungeonsNow and ((stAct == "DUNGEON_PREP" or stAct == "DUNGEON_RUN")
                            or (q.isDungeon == true) or (actE ~= nil and actE.isDungeon == true))
                        local isActReady = (actE ~= nil and actE.isComplete == true)
                        if isDoneQ then
                            blockCode, blockReason = nil, nil
                        elseif (isEliteSkip or isDungSkip) and (actE ~= nil or vActive[qid]) and (not isActReady) then
                            blockCode, blockReason = isEliteSkip and "elite_solo" or "dungeon_skipped", nil
                        elseif (actE ~= nil) or simCompleted[qid] or vActive[qid] then
                            blockCode, blockReason = nil, nil
                        elseif (routeFilter ~= "all") and (not hasQdbGlobal) and (not q.requiredSkill) then
                            local checkElig = not isSafeElig
                            if virtExcluded then
                                blockCode = "exclusive_taken"
                            elseif checkElig and (not ns.State.IsEligibleForPlayer(qid, player)) then
                                blockCode = "ineligible_character"
                            elseif checkElig and ((tonumber(q.minLevel) or 1) > pLv) then
                                blockCode = "min_level"
                            elseif (tonumber(q.questLevel) or 0) > maxQuestLv then
                                blockCode = "quest_level_too_high"
                            elseif isEliteSkip then
                                blockCode = "elite_solo"
                            elseif isDungSkip then
                                blockCode = "dungeon_skipped"
                            elseif q.prereqIds and #q.prereqIds > 0 then
                                local pids = q.prereqIds
                                if q.prereqMode == "any" then
                                    local anyDone = false
                                    for pi = 1, #pids do
                                        local pid = pids[pi]
                                        if simCompleted[pid] or completedMap[pid] or (isCompFn and isCompFn(pid, true)) then
                                            anyDone = true
                                            break
                                        end
                                    end
                                    if not anyDone then
                                        blockCode = "missing_prereq"
                                    end
                                else
                                    for pi = 1, #pids do
                                        local pid = pids[pi]
                                        if not (simCompleted[pid] or completedMap[pid] or (isCompFn and isCompFn(pid, true))) then
                                            blockCode = "missing_prereq"
                                            break
                                        end
                                    end
                                end
                            end
                            if (not blockCode) and (stAct == "COMPLETE" or stAct == "TURN_IN") then
                                if vBlockedAccept[qid] then
                                    blockCode = "accept_skipped"
                                else
                                    local aEntry = acceptStepMap and acceptStepMap[qid]
                                    if type(aEntry) == "number" then
                                        if aEntry >= idx or aEntry < (idx - 150) then
                                            blockCode = nil
                                        elseif aEntry >= startIdx and steps[aEntry] and steps[aEntry].questId == qid and not steps[aEntry].questIds then
                                            blockCode = nil
                                        else
                                            blockCode, blockReason = ns.State.IsStepBlocked(st, steps, idx, player, pkey, simCompleted, vActive, t, blockOpts)
                                        end
                                    elseif aEntry ~= nil then
                                        blockCode, blockReason = ns.State.IsStepBlocked(st, steps, idx, player, pkey, simCompleted, vActive, t, blockOpts)
                                    end
                                end
                            end
                        else
                            blockCode, blockReason = ns.State.IsStepBlocked(st, steps, idx, player, pkey, simCompleted, vActive, t, blockOpts)
                        end
                    end
                end
                if isAcceptLike and (not done) and (skipped or outleveled or blockCode ~= nil) then
                    vBlockedAccept[qid] = true
                end
            end
        end
    elseif (not hasCustomDone) and (routeFilter ~= "all") and st and st.action == "TRAVEL"
        and st.zoneId and (not st.questId) and (not st.questIds)
        and (not st.faction) and (not st.races) and (not st.race) and (not st.classes) and (not st.class) then
        fastSingleHandled = true
        if (not skipped) and routeFilter == "remaining" and type(st.expectedLevel) == "number" and pLv >= st.expectedLevel + 3 then
            outleveled = (not locFrozen) and (st.zoneId ~= player.zoneId)
        end
        if (not skipped) and (not outleveled) then
            if (not locFrozen) and (player.zoneId == st.zoneId or (st.zoneName and player.zoneName and (st.zoneName == player.zoneName or ((not player.zoneId) and string.lower(st.zoneName) == string.lower(player.zoneName))))) then
                done = true
            else
                local checkedImmDone = locFrozen
                local destZone = st.zoneId
                local foundAny = false
                local foundActionable = false
                local lookBlockedAccept = rs.lookBlocked
                local hasLookBlocked = false
                local maxLook = math.min(#steps, idx + 12)
                for look = idx + 1, maxLook do
                    local nsStep = steps[look]
                    if not nsStep then break end
                    if nsStep.action ~= "TRAVEL" then
                        local nqid = nsStep.questId
                        local nq = (nqid and (not nsStep.questIds) and quests) and quests[nqid] or nil
                        local nSingle = (nq ~= nil)
                        local nAct = nsStep.action or "ACCEPT"
                        local nActE, nDoneQ, nsDone
                        local nIsAccLike = (nAct == "ACCEPT") or (nAct == "DUNGEON_PREP" and nsStep.prepMode == "accept")
                        local nIsCompLike = (nAct == "COMPLETE") or (nAct == "DUNGEON_RUN")
                        local nLiveExcl, nVirtExcl = false, false
                        if nSingle and (nAct == "ACCEPT" or nAct == "COMPLETE" or nAct == "TURN_IN" or nAct == "DUNGEON_PREP" or nAct == "DUNGEON_RUN") then
                            nActE = activeMap[nqid]
                            nDoneQ = (completedMap and completedMap[nqid] == true) or (isCompFn ~= nil and isCompFn(nqid, true))
                            local nex = nq.exclusiveTo
                            if nex then
                                for j = 1, #nex do
                                    local oid = nex[j]
                                    if oid ~= nqid then
                                        if (completedMap and completedMap[oid] == true) or activeMap[oid] ~= nil or (isCompFn ~= nil and isCompFn(oid, true)) then
                                            nLiveExcl = true
                                            break
                                        elseif simCompleted[oid] or vActive[oid] then
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
                            nSingle = false
                            nsDone = satFn(nsStep, steps, look, player, pkey)
                        end
                        if not checkedImmDone then
                            checkedImmDone = true
                            if look <= idx + 3 and nsStep.zoneId == st.zoneId and nsDone then
                                done = true
                                break
                            end
                            if (not isSuppressed) and (st.expectedLevel or 0) > maxTravelLv then
                                blockCode = "quest_level_too_high"
                                break
                            end
                        end
                        if isSuppressed then
                            break
                        end
                        if nsStep.zoneId == destZone then
                            foundAny = true
                            local nsSkip = hasSkipped and ns.State.IsStepSkipped(nsStep, look, pkey) or false
                            if (not nsSkip) and (not nsDone) then
                                local nsBlocked = false
                                if nSingle then
                                    local nEliteSkip = rs.ctxSkipEliteNow and (nAct ~= "DUNGEON_PREP" and nAct ~= "DUNGEON_RUN")
                                        and (not nq.isDungeon) and ((nq.isGroup == true) or (nActE ~= nil and nActE.isElite == true))
                                    local nDungSkip = rs.ctxSkipDungeonsNow and ((nAct == "DUNGEON_PREP" or nAct == "DUNGEON_RUN")
                                        or (nq.isDungeon == true) or (nActE ~= nil and nActE.isDungeon == true))
                                    local nActReady = (nActE ~= nil and nActE.isComplete == true)
                                    if nDoneQ then
                                        nsBlocked = false
                                    elseif (nEliteSkip or nDungSkip) and (nActE ~= nil or vActive[nqid]) and (not nActReady) then
                                        nsBlocked = true
                                    elseif (nActE ~= nil) or simCompleted[nqid] or vActive[nqid] then
                                        nsBlocked = false
                                    elseif (not hasQdbGlobal) and (not nq.requiredSkill) then
                                        if nVirtExcl then
                                            nsBlocked = true
                                        elseif not ns.State.IsEligibleForPlayer(nqid, player) then
                                            nsBlocked = true
                                        elseif (tonumber(nq.minLevel) or 1) > pLv or (tonumber(nq.questLevel) or 0) > maxQuestLv then
                                            nsBlocked = true
                                        elseif nEliteSkip or nDungSkip then
                                            nsBlocked = true
                                        elseif nq.prereqIds and #nq.prereqIds > 0 then
                                            local pids = nq.prereqIds
                                            if nq.prereqMode == "any" then
                                                local anyDone = false
                                                for pi = 1, #pids do
                                                    local pid = pids[pi]
                                                    if simCompleted[pid] or (completedMap and completedMap[pid]) or (isCompFn and isCompFn(pid, true)) then
                                                        anyDone = true
                                                        break
                                                    end
                                                end
                                                if not anyDone then
                                                    nsBlocked = true
                                                end
                                            else
                                                for pi = 1, #pids do
                                                    local pid = pids[pi]
                                                    if not (simCompleted[pid] or (completedMap and completedMap[pid]) or (isCompFn and isCompFn(pid, true))) then
                                                        nsBlocked = true
                                                        break
                                                    end
                                                end
                                            end
                                        end
                                        if (not nsBlocked) and (nAct == "COMPLETE" or nAct == "TURN_IN") then
                                            if vBlockedAccept[nqid] or (hasLookBlocked and lookBlockedAccept[nqid]) then
                                                nsBlocked = true
                                            else
                                                local aEntry = acceptStepMap and acceptStepMap[nqid]
                                                if type(aEntry) == "number" then
                                                    if aEntry >= look or aEntry < (look - 150) then
                                                        nsBlocked = false
                                                    elseif aEntry >= startIdx and aEntry < idx and steps[aEntry] and steps[aEntry].questId == nqid and not steps[aEntry].questIds then
                                                        nsBlocked = false
                                                    else
                                                        nsBlocked = (ns.State.IsStepBlocked(nsStep, steps, look, player, pkey, simCompleted, vActive, t, blockOpts) ~= nil)
                                                    end
                                                elseif aEntry ~= nil then
                                                    nsBlocked = (ns.State.IsStepBlocked(nsStep, steps, look, player, pkey, simCompleted, vActive, t, blockOpts) ~= nil)
                                                end
                                            end
                                        end
                                        if nIsAccLike and nsBlocked then
                                            if not hasLookBlocked then
                                                WipeMap(lookBlockedAccept)
                                                hasLookBlocked = true
                                            end
                                            lookBlockedAccept[nqid] = true
                                        end
                                    else
                                        nsBlocked = (ns.State.IsStepBlocked(nsStep, steps, look, player, pkey, simCompleted, vActive, t, blockOpts) ~= nil)
                                    end
                                else
                                    nsBlocked = (ns.State.IsStepBlocked(nsStep, steps, look, player, pkey, simCompleted, vActive, t, blockOpts) ~= nil)
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
                        destZone = nsStep.zoneId
                    end
                end
                if (not done) and (not isSuppressed) and (not blockCode) then
                    if (st.expectedLevel or 0) > maxTravelLv then
                        blockCode = "quest_level_too_high"
                    elseif foundAny and not foundActionable then
                        blockCode = "redundant_travel"
                    end
                end
            end
        end
    end
    if not fastSingleHandled then
        outleveled = (routeFilter == "remaining") and ns.State.IsStepOutleveled and ns.State.IsStepOutleveled(st, player)
        done = (not skipped) and (not outleveled) and satFn(st, steps, idx, player, pkey)
        if (routeFilter == "all") or ((not isSuppressed) and (not skipped) and (not outleveled) and (not done)) then
            blockCode, blockReason = ns.State.IsStepBlocked(st, steps, idx, player, pkey, simCompleted, vActive, t, blockOpts)
        end
        if (not done) and (skipped or outleveled or blockCode ~= nil) then
            MarkBlockedAcceptStep(st)
        end
    end

    local stepPasses = (routeFilter == "all")
        or ((not skipped) and (not outleveled) and (not done) and (not blockCode))
    if routeFilter == "zone" and st.zoneId ~= zoneTarget then
        stepPasses = false
    end

    if routeBeforeMap and routeBeforeMap[idx] then
        rs.ctxBaseCount = baseCount
        AppendBaseInjected(routeBeforeMap[idx], idx)
        baseCount = rs.ctxBaseCount
    end

    if stepPasses and not isSuppressed then
        local decInfo = decorateMap and decorateMap[idx]
        local outSt = st
        if (baseCount < 50 or st.zoneId == zoneTarget) and decFn then
            outSt = decFn(st, player, t)
        end
        baseCount = baseCount + 1
        bSlot.steps[baseCount] = outSt
        bSlot.idxs[baseCount] = idx
        bSlot.owners[baseCount] = idx
        local rFlag = (done and 1 or 0) + (skipped and 2 or 0) + (decInfo ~= nil and 32 or 0)
        if blockCode ~= nil then
            rFlag = rFlag + 4
            if blockReason ~= nil then
                bSlot.reasons[baseCount] = blockReason
            end
        end
        bSlot.flags[baseCount] = rFlag
    end

    if isSuppressed or done or (stepPasses and not blockCode) or (routeFilter == "all" and not blockCode) then
        SimRouteStep(st)
    end

    if routeAfterMap and routeAfterMap[idx] then
        rs.ctxBaseCount = baseCount
        AppendBaseInjected(routeAfterMap[idx], idx)
        baseCount = rs.ctxBaseCount
    end
    return baseCount
end

ComputeRouteTabCache = function(playerHint)
    local cdb = ns.charDB or {}
    local activeKey = cdb.activeProfile or "balanced"
    local t = cdb.toggles or {}
    local isDynamic = t.dynamicRescore == true
    local w = ns.GetActiveWeights()
    local player = playerHint or ns.State.GetPlayerInfo()
    local maxOffset = ns.State.GetEffectiveMaxLevelOffset and ns.State.GetEffectiveMaxLevelOffset(t) or 2

    local questGen = ns.State.GetQuestStateGeneration and ns.State.GetQuestStateGeneration() or 0
    local dbQuestGen = ns.State.GetDbQuestStateGeneration and ns.State.GetDbQuestStateGeneration() or questGen
    local wSig = isDynamic and WeightSig(w) or ""
    local readyCount = ns.State.GetReadyTurnInCount and ns.State.GetReadyTurnInCount() or 0
    local readySig = ns.State.GetReadyTurnInSignature and ns.State.GetReadyTurnInSignature() or ""
    local routeReadySig = ns.State.GetRouteReadySignature and ns.State.GetRouteReadySignature() or readySig
    local routeReadyCount = 0
    if routeReadySig ~= "" then
        routeReadyCount = 1
        local rPos = 1
        while true do
            local comma = string.find(routeReadySig, ",", rPos, true)
            if not comma then break end
            routeReadyCount = routeReadyCount + 1
            rPos = comma + 1
        end
    end
    local currentStep, meta = ns.State.GetCurrentStep(player)
    local locPlan = (not isDynamic) and meta and meta.locationPlan or nil
    local locFirst = locPlan and locPlan.steps and locPlan.steps[1]
    local locSecond = locPlan and locPlan.steps and locPlan.steps[2]
    local locDisplaySig = string.format("%s:%s:%s:%d:%s:%s",
        tostring(meta and meta.source or ""),
        tostring(meta and meta.locationPos or 0),
        tostring(locPlan and locPlan.mode or ""),
        tonumber(locPlan and locPlan.steps and #locPlan.steps or 0),
        tostring(locFirst and (locFirst.gpsStepKey or locFirst.origRouteIdx or locFirst.questId or locFirst.title) or ""),
        tostring(locSecond and (locSecond.gpsStepKey or locSecond.origRouteIdx or locSecond.questId or locSecond.title) or ""))
    local cursorIdx = tonumber(meta and meta.stepIndex) or tonumber(ns.charDB and ns.charDB.stepIndex) or 1
    local skipEliteNow = (ns.State.ShouldSkipEliteNow and ns.State.ShouldSkipEliteNow(player, t)) or (t.skipEliteSolo ~= false and not (player and player.inGroup))
    routeScratch.ctxSkipDungeonsNow = (ns.State.ShouldSkipDungeonsNow and ns.State.ShouldSkipDungeonsNow(t)) or (t.skipDungeons ~= false)
    local baseZoneKey = (routeFilter == "zone" or routeReadyCount > 0) and tostring(player.zoneId or 0) or "any"
    local baseKey = string.format("%d:%s:%s:%s:%s:%s:%s:%d:%s:%d:%d:%s:%d:%d:%s:%s",
        routeStaticSeq,
        tostring(activeKey),
        tostring(isDynamic),
        tostring(routeFilter),
        baseZoneKey,
        wSig,
        tostring(player.faction or "Alliance"),
        tonumber(player.raceId) or 0,
        tostring(player.classFile or "ROGUE"),
        tonumber(player.level) or 24,
        maxOffset,
        routeReadySig,
        cursorIdx,
        dbQuestGen,
        tostring(skipEliteNow),
        tostring(routeScratch.ctxSkipDungeonsNow))
    local cacheKey = string.format("%s:%d:%s:%s:%s",
        baseKey,
        questGen,
        readySig,
        tostring(t.locationReorder ~= false),
        locDisplaySig)

    if routeTabCache.key ~= cacheKey then
        local steps, profile, pkey = ns.State.GetActiveProfileSteps()
        local bSlotKey = isDynamic and "dyn" or maxOffset
        local bSlot = GetBaseRouteSlot(bSlotKey)
        if bSlot.key ~= baseKey then
            local sum = profile and profile.summary
            local sumCore
            local routeLabel = (profile and profile.label) or ns.GetPresetLabel(activeKey)
            if sum then
                sumCore = string.format(
                    "|cff33d9ff%s|r  |  Est: |cffffffff%.1fh|r  |  Avg XP/hr: |cffc080ff%s|r  |  Dungeons: |cffff944d%s|r  |  Gold: %s  |  Bags: |cff33ff99%d|r  |  %s BiS: |cfffff569%d|r",
                    routeLabel,
                    tonumber(sum.totalTimeHours) or 0,
                    ns.FormatNumber(sum.avgXpPerHour or 0),
                    tostring(sum.dungeonsScheduled or 0),
                    ns.FormatMoney(sum.totalGoldCopper or 0),
                    tonumber(sum.bagsEarned) or 0,
                    player.className or "Class",
                    tonumber(sum.rogueUpgrades) or 0
                )
            else
                sumCore = string.format("Strategy: |cff33d9ff%s|r  |  %s %s %s (%d steps)",
                    routeLabel, player.faction or "Alliance", player.raceName or "Gnome", player.className or "Rogue", #steps)
            end

            WipeMap(bSlot.reasons)
            local bSteps = bSlot.steps
            local bIdxs = bSlot.idxs
            local bOwners = bSlot.owners
            local bFlags = bSlot.flags
            local baseCount = 0
            local plan = nil
            if isDynamic then
                local dyn = ns.Scorer.GetDynamicSteps(player, w, t, 20)
                for i, st in ipairs(dyn) do
                    baseCount = baseCount + 1
                    bSteps[baseCount] = st
                    bIdxs[baseCount] = i
                    bOwners[baseCount] = false
                    local fl = 8
                    if i == 1 then fl = fl + 256 end
                    if st.isReadyTurnIn == true then fl = fl + 32 end
                    if st.isGpsReordered == true then fl = fl + 64 end
                    if st.isFriendChain == true then fl = fl + 128 end
                    bFlags[baseCount] = fl
                end
            else
                local satFn = ns.State.IsStepSatisfiedFast or ns.State.IsStepSatisfied
                local startIdx = (routeFilter == "remaining") and math.max(1, cursorIdx) or 1
                local zoneTarget = player.zoneId
                plan = meta.readyPlan or (ns.State.GetReadyTurnInPlan and ns.State.GetReadyTurnInPlan(steps, cursorIdx, player, pkey, t)) or nil
                local curReadyKey = (meta.source == "ready_turnin" and currentStep and currentStep.readyStepKey) or nil

                local simCompleted = routeScratch.simCompleted
                local vActive = routeScratch.vActive
                local vBlockedAccept = routeScratch.vBlockedAccept
                WipeMap(simCompleted)
                WipeMap(vActive)
                WipeMap(vBlockedAccept)
                local completedMap, bulkSynced = nil, false
                if ns.State.GetCompletedReadOnly then
                    completedMap, bulkSynced = ns.State.GetCompletedReadOnly()
                end
                local isCompFn = (not bulkSynced) and ns.State.IsQuestCompleted or nil
                local activeMap = ns.State.GetActiveQuests and ns.State.GetActiveQuests() or {}
                for aqid, entry in pairs(activeMap) do
                    if entry and not ((completedMap and completedMap[aqid] == true) or (isCompFn ~= nil and isCompFn(aqid, true))) then
                        vActive[aqid] = true
                    end
                end
                local blockOpts = ROUTE_BLOCK_OPTS

                if routeFilter ~= "all" and plan and plan.immediate then
                    for i = 1, #plan.immediate do
                        SimRouteStep(plan.immediate[i])
                    end
                end

                local data = ns.GetData and ns.GetData()
                local quests = data and data.quests
                local customDoneMap = ns.charDB and ns.charDB.completedCustomSteps
                local hasCustomDone = (type(customDoneMap) == "table" and next(customDoneMap) ~= nil)
                local skippedMap = ns.charDB and ns.charDB.skippedSteps
                local hasSkipped = (type(skippedMap) == "table" and next(skippedMap) ~= nil)
                local hasQdbGlobal = (_G.QuestieDB ~= nil or _G.QuestieLoader ~= nil) and (t.consultQuestie ~= false)
                local pLv = tonumber(player and player.level) or 24
                local maxQuestLv = pLv + maxOffset
                local maxTravelLv = pLv + math.max(maxOffset, 2) + 4
                local suppressedMap = (routeFilter ~= "all" and plan and plan.suppressedRouteIdx) or nil
                local routeBeforeMap = (routeFilter ~= "all" and plan and plan.routeBefore) or nil
                local routeAfterMap = (routeFilter ~= "all" and plan and plan.routeAfter) or nil
                local decorateMap = (plan and plan.decorateRouteIdx) or nil

                local locFrozen = (player and player.onTaxi == true) or (ns.State.IsPlayerDeadOrGhost ~= nil and ns.State.IsPlayerDeadOrGhost() == true)
                local acceptStepMap = ns.State.GetAcceptStepIndexMap and ns.State.GetAcceptStepIndexMap(steps) or nil

                routeScratch.ctxQuests = quests
                routeScratch.ctxCompletedMap = completedMap
                routeScratch.ctxActiveMap = activeMap
                routeScratch.ctxIsCompFn = isCompFn
                routeScratch.ctxPlayer = player
                routeScratch.ctxPLv = pLv
                routeScratch.ctxAbsMaxLv = pLv + 5
                routeScratch.ctxMaxQuestLv = maxQuestLv
                routeScratch.ctxToggles = t
                routeScratch.ctxRouteFilter = routeFilter
                routeScratch.ctxZoneTarget = zoneTarget
                routeScratch.ctxCurReadyKey = curReadyKey
                routeScratch.ctxHasCustomDone = hasCustomDone
                routeScratch.ctxSteps = steps
                routeScratch.ctxPkey = pkey
                routeScratch.ctxStartIdx = startIdx
                routeScratch.ctxAcceptStepMap = acceptStepMap
                routeScratch.ctxRouteBeforeMap = routeBeforeMap
                routeScratch.ctxRouteAfterMap = routeAfterMap
                routeScratch.ctxDecorateMap = decorateMap
                routeScratch.ctxHasSkipped = hasSkipped
                routeScratch.ctxHasQdbGlobal = hasQdbGlobal
                routeScratch.ctxSkipEliteNow = skipEliteNow
                routeScratch.ctxLocFrozen = locFrozen
                routeScratch.ctxMaxTravelLv = maxTravelLv
                routeScratch.ctxSatFn = satFn
                routeScratch.ctxBlockOpts = blockOpts
                routeScratch.ctxBaseSlot = bSlot
                routeScratch.ctxBaseCount = baseCount
                local useHorizon = (routeFilter ~= "all") and (not hasCustomDone) and (quests ~= nil) and (completedMap ~= nil)
                local hIndices = nil
                local hSafeElig = nil
                local hQids = nil
                local hQLvs = nil
                local hReqSkills = nil
                local hActCodes = nil
                local hHasPrereqs = nil
                local hIsChain = nil
                local hCount = 0
                if useHorizon then
                    local horizonKey = string.format("%d:%s:%d:%s:%d:%d:%s:%d:%s:%d:%s:%s:%s:%s",
                        routeStaticSeq,
                        tostring(pkey),
                        pLv,
                        tostring(routeFilter),
                        tonumber(zoneTarget) or 0,
                        dbQuestGen,
                        tostring(player.faction or "Alliance"),
                        tonumber(player.raceId) or 0,
                        tostring(player.classFile or "ROGUE"),
                        tonumber(player.classId) or 0,
                        tostring(locFrozen),
                        tostring(hasSkipped),
                        tostring(skipEliteNow),
                        tostring(routeScratch.ctxSkipDungeonsNow))
                    hIndices = remainingHorizonCache.indices
                    hSafeElig = remainingHorizonCache.safeElig
                    hQids = remainingHorizonCache.qids
                    hQLvs = remainingHorizonCache.qLvs
                    hReqSkills = remainingHorizonCache.reqSkills
                    hActCodes = remainingHorizonCache.actCodes
                    hHasPrereqs = remainingHorizonCache.hasPrereqs
                    hIsChain = remainingHorizonCache.isChain
                    if remainingHorizonCache.key ~= horizonKey or remainingHorizonCache.stepsRef ~= steps then
                        local hBlockedAccept = remainingHorizonCache.blockedAccept
                        WipeMap(hBlockedAccept)
                        local absMaxQuestLv = pLv + 5
                        local absMaxTravelLv = pLv + 9
                        for cIdx = 1, #steps do
                            local cst = steps[cIdx]
                            local keep = true
                            local isSafeSingle = false
                            local safeQid = 0
                            local safeQLv = -1
                            local safeReqSkill = false
                            local safeActCode = 0
                            local safeHasPrereqs = false
                            local safeIsChain = false
                            if cst then
                                local cAct = cst.action or "ACCEPT"
                                if cAct == "TRAVEL" then
                                    local plainTravel = cst.zoneId and (not cst.questId) and (not cst.questIds)
                                        and (not cst.faction) and (not cst.races) and (not cst.race) and (not cst.classes) and (not cst.class)
                                    if cst.zoneId and (not cst.questId) and (not cst.questIds) then
                                        if routeFilter == "zone" and cst.zoneId ~= zoneTarget then
                                            keep = false
                                        elseif (cst.expectedLevel or 0) > absMaxTravelLv then
                                            keep = false
                                        elseif plainTravel and routeFilter == "remaining" and (not hasSkipped) and (not locFrozen)
                                            and (player.zoneId == cst.zoneId or (type(cst.expectedLevel) == "number" and pLv >= cst.expectedLevel + 3 and cst.zoneId ~= player.zoneId)) then
                                            keep = false
                                        elseif player.zoneId ~= cst.zoneId then
                                            local destZone = cst.zoneId
                                            local sawAny = false
                                            local allBlocked = true
                                            local immDone = false
                                            local checkedImm = not (plainTravel and routeFilter == "remaining" and (not hasSkipped) and (not locFrozen))
                                            local remCheck = (routeFilter == "remaining" and (not hasSkipped))
                                            local maxLook = math.min(#steps, cIdx + 12)
                                            for look = cIdx + 1, maxLook do
                                                local nsStep = steps[look]
                                                if not nsStep then break end
                                                if nsStep.action ~= "TRAVEL" then
                                                    if not checkedImm then
                                                        checkedImm = true
                                                        local nqid = nsStep.questId
                                                        local nq = (nqid and (not nsStep.questIds)) and quests[nqid] or nil
                                                        if look <= cIdx + 3 and nsStep.zoneId == cst.zoneId and nq and (not nq.exclusiveTo) and (completedMap[nqid] == true) then
                                                            immDone = true
                                                            break
                                                        end
                                                    end
                                                    if nsStep.zoneId == destZone then
                                                        sawAny = true
                                                        local nonAct = remCheck and IsStepAlwaysNonActionableRem(nsStep) or IsStepAlwaysBlocked(nsStep)
                                                        if not nonAct then
                                                            allBlocked = false
                                                            break
                                                        end
                                                    else
                                                        break
                                                    end
                                                else
                                                    if sawAny then
                                                        break
                                                    end
                                                    destZone = nsStep.zoneId
                                                end
                                            end
                                            if immDone or (sawAny and allBlocked) then
                                                keep = false
                                            end
                                        end
                                    end
                                elseif routeFilter == "remaining"
                                    and (not hasSkipped)
                                    and cst.questId and (not cst.questIds)
                                    and (cAct == "ACCEPT" or cAct == "COMPLETE" or cAct == "TURN_IN" or cAct == "DUNGEON_PREP" or cAct == "DUNGEON_RUN") then
                                    local cqid = cst.questId
                                    local cq = quests[cqid]
                                    if cq and (not cq.exclusiveTo) then
                                        if (completedMap[cqid] == true) then
                                            keep = false
                                        elseif activeMap[cqid] == nil then
                                            if isCompFn ~= nil and isCompFn(cqid, true) then
                                                keep = false
                                            else
                                                local isDung = (cq.isDungeon == true) or (cAct == "DUNGEON_RUN")
                                                local cOutleveled = false
                                                if type(cst.expectedLevel) == "number" then
                                                    if (not isDung) and ((tonumber(cq.questLevel or cq.minLevel) or cst.expectedLevel) <= pLv - 7) then
                                                        cOutleveled = true
                                                    elseif pLv >= cst.expectedLevel + 2 then
                                                        cOutleveled = ns.State.IsStepOutleveled(cst, player)
                                                    end
                                                end
                                                local eligOk = ns.State.IsEligibleForPlayer(cqid, player)
                                                local minLvOk = (tonumber(cq.minLevel) or 1) <= pLv
                                                local cqLevel = tonumber(cq.questLevel) or 0
                                                local eliteSkipped = skipEliteNow and (cAct ~= "DUNGEON_PREP" and cAct ~= "DUNGEON_RUN") and (cq.isGroup == true and not cq.isDungeon)
                                                local cAlwaysBlocked = (cqLevel > absMaxQuestLv) or (not minLvOk) or (not eligOk) or eliteSkipped
                                                    or (routeScratch.ctxSkipDungeonsNow and (cAct == "DUNGEON_PREP" or cAct == "DUNGEON_RUN" or cq.isDungeon == true))
                                                if cOutleveled or cAlwaysBlocked then
                                                    keep = false
                                                    if cAct == "ACCEPT" or (cAct == "DUNGEON_PREP" and cst.prepMode == "accept") then
                                                        hBlockedAccept[cqid] = cIdx
                                                    end
                                                else
                                                    isSafeSingle = true
                                                    safeQid = cqid
                                                    safeQLv = cqLevel
                                                    safeReqSkill = (cq.requiredSkill ~= nil)
                                                    safeHasPrereqs = (cq.prereqIds ~= nil and #cq.prereqIds > 0)
                                                    safeIsChain = safeHasPrereqs or (cq.nextQuestIds ~= nil and #cq.nextQuestIds > 0)
                                                    if cAct == "ACCEPT" or (cAct == "DUNGEON_PREP" and cst.prepMode == "accept") then
                                                        safeActCode = 1
                                                    elseif cAct == "COMPLETE" then
                                                        safeActCode = 2
                                                    elseif cAct == "TURN_IN" then
                                                        safeActCode = 3
                                                    elseif cAct == "DUNGEON_RUN" then
                                                        safeActCode = 4
                                                    else
                                                        safeActCode = 5
                                                    end
                                                end
                                            end
                                        end
                                    elseif IsStepAlwaysBlocked(cst) then
                                        keep = false
                                    end
                                elseif IsStepAlwaysBlocked(cst) then
                                    keep = false
                                end
                            end
                            if keep then
                                hCount = hCount + 1
                                hIndices[hCount] = cIdx
                                hSafeElig[hCount] = isSafeSingle
                                hQids[hCount] = isSafeSingle and safeQid or 0
                                hQLvs[hCount] = isSafeSingle and safeQLv or -1
                                hReqSkills[hCount] = isSafeSingle and safeReqSkill or false
                                hActCodes[hCount] = isSafeSingle and safeActCode or 0
                                hHasPrereqs[hCount] = isSafeSingle and safeHasPrereqs or false
                                hIsChain[hCount] = isSafeSingle and safeIsChain or false
                            end
                        end
                        for trim = hCount + 1, #hIndices do
                            hIndices[trim] = nil
                            hSafeElig[trim] = nil
                            hQids[trim] = nil
                            hQLvs[trim] = nil
                            hReqSkills[trim] = nil
                            hActCodes[trim] = nil
                            hHasPrereqs[trim] = nil
                            hIsChain[trim] = nil
                        end
                        remainingHorizonCache.key = horizonKey
                        remainingHorizonCache.stepsRef = steps
                        remainingHorizonCache.count = hCount
                    else
                        hCount = remainingHorizonCache.count
                    end
                    for bq, bIdx in pairs(remainingHorizonCache.blockedAccept) do
                        if bIdx >= startIdx then
                            vBlockedAccept[bq] = true
                        end
                    end
                end

                local firstLoopPos = 1
                local loopLimit
                if useHorizon then
                    if hCount > 0 then
                        local lo, hi = 1, hCount + 1
                        while lo < hi do
                            local mid = math.floor((lo + hi) * 0.5)
                            if hIndices[mid] < startIdx then
                                lo = mid + 1
                            else
                                hi = mid
                            end
                        end
                        firstLoopPos = lo
                    else
                        firstLoopPos = 1
                    end
                    loopLimit = hCount
                else
                    loopLimit = math.max(0, #steps - startIdx + 1)
                end
                local injKeys = routeScratch.injKeys
                local injCount = 0
                local injPos = 1
                if useHorizon and (routeBeforeMap or routeAfterMap) then
                    local injSeen = routeScratch.injSeen
                    WipeMap(injSeen)
                    if routeBeforeMap then
                        for kIdx in pairs(routeBeforeMap) do
                            if type(kIdx) == "number" and kIdx >= startIdx and not injSeen[kIdx] then
                                injSeen[kIdx] = true
                                injCount = injCount + 1
                                injKeys[injCount] = kIdx
                            end
                        end
                    end
                    if routeAfterMap then
                        for kIdx in pairs(routeAfterMap) do
                            if type(kIdx) == "number" and kIdx >= startIdx and not injSeen[kIdx] then
                                injSeen[kIdx] = true
                                injCount = injCount + 1
                                injKeys[injCount] = kIdx
                            end
                        end
                    end
                    for trim = injCount + 1, #injKeys do
                        injKeys[trim] = nil
                    end
                    if injCount > 1 then
                        table.sort(injKeys)
                    end
                end
                local decFn = ns.State.DecorateStepChain
                for loopPos = firstLoopPos, loopLimit do
                    local idx = useHorizon and hIndices[loopPos] or (startIdx + loopPos - 1)
                    while injPos <= injCount and injKeys[injPos] < idx do
                        local kIdx = injKeys[injPos]
                        injPos = injPos + 1
                        if routeBeforeMap and routeBeforeMap[kIdx] then
                            routeScratch.ctxBaseCount = baseCount
                            AppendBaseInjected(routeBeforeMap[kIdx], kIdx)
                            baseCount = routeScratch.ctxBaseCount
                        end
                        if routeAfterMap and routeAfterMap[kIdx] then
                            routeScratch.ctxBaseCount = baseCount
                            AppendBaseInjected(routeAfterMap[kIdx], kIdx)
                            baseCount = routeScratch.ctxBaseCount
                        end
                    end
                    if injPos <= injCount and injKeys[injPos] == idx then
                        injPos = injPos + 1
                    end
                    local st = steps[idx]
                    local isSuppressed = (suppressedMap ~= nil and suppressedMap[idx] == true)
                    local handledFastSafe = false
                    if useHorizon and (hSafeElig[loopPos] == true) and (not hasCustomDone) and (not hasSkipped) and (not hasQdbGlobal) and st then
                        local qid = hQids[loopPos]
                        local actCode = hActCodes[loopPos]
                        local actE = activeMap[qid]
                        local isDoneQ = (completedMap[qid] == true) or (isCompFn ~= nil and isCompFn(qid, true))
                        if actE == nil and (not isDoneQ) and (not hReqSkills[loopPos]) then
                            handledFastSafe = true
                            local fastBlockedHigh = (not isSuppressed) and (hQLvs[loopPos] > maxQuestLv) and (not simCompleted[qid]) and (not vActive[qid])
                            if fastBlockedHigh then
                                if actCode == 1 then
                                    vBlockedAccept[qid] = true
                                end
                                if routeBeforeMap and routeBeforeMap[idx] then
                                    routeScratch.ctxBaseCount = baseCount
                                    AppendBaseInjected(routeBeforeMap[idx], idx)
                                    baseCount = routeScratch.ctxBaseCount
                                end
                                if routeAfterMap and routeAfterMap[idx] then
                                    routeScratch.ctxBaseCount = baseCount
                                    AppendBaseInjected(routeAfterMap[idx], idx)
                                    baseCount = routeScratch.ctxBaseCount
                                end
                            else
                                local blockCode = nil
                                if (not isSuppressed) and (not simCompleted[qid]) and (not vActive[qid]) then
                                    if hHasPrereqs[loopPos] then
                                        local q = quests[qid]
                                        local pids = q and q.prereqIds
                                        if pids and #pids > 0 then
                                            if q.prereqMode == "any" then
                                                local anyDone = false
                                                for pi = 1, #pids do
                                                    local pid = pids[pi]
                                                    if simCompleted[pid] or completedMap[pid] or (isCompFn and isCompFn(pid, true)) then
                                                        anyDone = true
                                                        break
                                                    end
                                                end
                                                if not anyDone then
                                                    blockCode = "missing_prereq"
                                                end
                                            else
                                                for pi = 1, #pids do
                                                    local pid = pids[pi]
                                                    if not (simCompleted[pid] or completedMap[pid] or (isCompFn and isCompFn(pid, true))) then
                                                        blockCode = "missing_prereq"
                                                        break
                                                    end
                                                end
                                            end
                                        end
                                    end
                                    if (not blockCode) and (actCode == 2 or actCode == 3) then
                                        if vBlockedAccept[qid] then
                                            blockCode = "accept_skipped"
                                        else
                                            local aEntry = acceptStepMap and acceptStepMap[qid]
                                            if type(aEntry) == "number" then
                                                if aEntry < idx and aEntry >= (idx - 150) then
                                                    if not (aEntry >= startIdx and steps[aEntry] and steps[aEntry].questId == qid and not steps[aEntry].questIds) then
                                                        blockCode = ns.State.IsStepBlocked(st, steps, idx, player, pkey, simCompleted, vActive, t, blockOpts)
                                                    end
                                                end
                                            elseif aEntry ~= nil then
                                                blockCode = ns.State.IsStepBlocked(st, steps, idx, player, pkey, simCompleted, vActive, t, blockOpts)
                                            end
                                        end
                                    end
                                    if actCode == 1 and blockCode ~= nil then
                                        vBlockedAccept[qid] = true
                                    end
                                end

                                if routeBeforeMap and routeBeforeMap[idx] then
                                    routeScratch.ctxBaseCount = baseCount
                                    AppendBaseInjected(routeBeforeMap[idx], idx)
                                    baseCount = routeScratch.ctxBaseCount
                                end

                                local stepPasses = (blockCode == nil)
                                if stepPasses and not isSuppressed then
                                    local decInfo = decorateMap and decorateMap[idx]
                                    local outSt = st
                                    if hIsChain[loopPos] and (baseCount < 50 or st.zoneId == zoneTarget) and decFn then
                                        outSt = decFn(st, player, t)
                                    end
                                    baseCount = baseCount + 1
                                    bSteps[baseCount] = outSt
                                    bIdxs[baseCount] = idx
                                    bOwners[baseCount] = idx
                                    bFlags[baseCount] = (decInfo ~= nil) and 32 or 0
                                end

                                if isSuppressed or stepPasses then
                                    if actCode == 1 then
                                        vActive[qid] = true
                                    elseif actCode == 3 or actCode == 4 then
                                        vActive[qid] = nil
                                        simCompleted[qid] = true
                                    end
                                end

                                if routeAfterMap and routeAfterMap[idx] then
                                    routeScratch.ctxBaseCount = baseCount
                                    AppendBaseInjected(routeAfterMap[idx], idx)
                                    baseCount = routeScratch.ctxBaseCount
                                end
                            end
                        end
                    end
                    if not handledFastSafe then
                        local isSafeHorizon = (useHorizon and hSafeElig[loopPos] == true) and true or false
                        baseCount = EvalHorizonFallbackStep(idx, st, isSuppressed, isSafeHorizon, baseCount)
                    end
                end
                while injPos <= injCount do
                    local kIdx = injKeys[injPos]
                    injPos = injPos + 1
                    if routeBeforeMap and routeBeforeMap[kIdx] then
                        routeScratch.ctxBaseCount = baseCount
                        AppendBaseInjected(routeBeforeMap[kIdx], kIdx)
                        baseCount = routeScratch.ctxBaseCount
                    end
                    if routeAfterMap and routeAfterMap[kIdx] then
                        routeScratch.ctxBaseCount = baseCount
                        AppendBaseInjected(routeAfterMap[kIdx], kIdx)
                        baseCount = routeScratch.ctxBaseCount
                    end
                end
                routeScratch.ctxQuests = nil
                routeScratch.ctxCompletedMap = nil
                routeScratch.ctxActiveMap = nil
                routeScratch.ctxIsCompFn = nil
                routeScratch.ctxPlayer = nil
                routeScratch.ctxToggles = nil
                routeScratch.ctxSteps = nil
                routeScratch.ctxPkey = nil
                routeScratch.ctxAcceptStepMap = nil
                routeScratch.ctxRouteBeforeMap = nil
                routeScratch.ctxRouteAfterMap = nil
                routeScratch.ctxDecorateMap = nil
                routeScratch.ctxSatFn = nil
                routeScratch.ctxBlockOpts = nil
                routeScratch.ctxBaseSlot = nil
            end

            for k = baseCount + 1, #bSteps do
                bSteps[k] = nil
                bIdxs[k] = nil
                bOwners[k] = nil
                bFlags[k] = nil
            end
            bSlot.count = baseCount
            bSlot.key = baseKey
            bSlot.sumCore = sumCore
            bSlot.plan = plan
        end

        local sumText = bSlot.sumCore
        if readyCount > 0 then
            sumText = sumText .. string.format("  |  Ready Turn-Ins: |cff4dff9e%d|r", readyCount)
        end
        if locPlan and locPlan.mode == "friend_pivot" then
            sumText = sumText .. "  |  |cff66e0ffFriend Chain Active|r"
        elseif locPlan and locPlan.mode == "gps_reorder" then
            sumText = sumText .. "  |  |cff99ff66GPS Reorder Active|r"
        end

        local displayList = routeTabCache.displayList
        if type(displayList) ~= "table" then
            displayList = {}
        end
        local bCount = bSlot.count
        local bSteps = bSlot.steps
        local bIdxs = bSlot.idxs
        local bOwners = bSlot.owners
        local bFlags = bSlot.flags
        local bReasons = bSlot.reasons
        if isDynamic then
            for i = 1, bCount do
                local fl = bFlags[i]
                local isCur = fl >= 256
                if isCur then fl = fl - 256 end
                local isFc = fl >= 128
                if isFc then fl = fl - 128 end
                local isGps = fl >= 64
                if isGps then fl = fl - 64 end
                local isRdy = fl >= 32
                displayList[i] = AllocPooledRow(
                    baseRowPool, i, bSteps[i], bIdxs[i] ~= false and bIdxs[i] or nil, nil,
                    isCur, false, false, false, nil, true, false, isRdy, isGps, isFc
                )
            end
            for k = bCount + 1, #displayList do
                displayList[k] = nil
            end
        else
            local dispCount = 0
            local ovCount = 0
            local zoneTarget = player.zoneId
            local curReadyKey = (meta.source == "ready_turnin" and currentStep and currentStep.readyStepKey) or nil
            local curGpsKey = ((meta.source == "gps_reorder" or meta.source == "friend_pivot") and currentStep and currentStep.gpsStepKey) or nil
            local curOrigIdx = ((meta.source == "gps_reorder" or meta.source == "friend_pivot") and currentStep and currentStep.origRouteIdx) or nil
            local suppressedMap = (routeFilter ~= "all" and locPlan and locPlan.suppressedOrigIdx) or nil
            local locSeenActionQids = routeScratch.locSeen
            local locCompletedQids = routeScratch.locDone
            WipeMap(locSeenActionQids)
            WipeMap(locCompletedQids)
            local livePlan = (meta and meta.readyPlan) or bSlot.plan

            if routeFilter ~= "all" and livePlan and livePlan.immediate then
                local decFn = ns.State.DecorateStepChain
                for i = 1, #livePlan.immediate do
                    local rSt = livePlan.immediate[i]
                    if routeFilter ~= "zone" or rSt.zoneId == zoneTarget then
                        local decSt = (decFn and decFn(rSt, player, t)) or rSt
                        ovCount = ovCount + 1
                        dispCount = dispCount + 1
                        displayList[dispCount] = AllocPooledRow(
                            overlayRowPool, ovCount, decSt, nil, nil,
                            (curReadyKey ~= nil and decSt.readyStepKey == curReadyKey),
                            false, false, false, nil, true, true, true, nil, nil
                        )
                        local rq = decSt and decSt.questId
                        if rq then
                            local rAct = decSt.action or ""
                            local dKey = (type(rq) == "number") and (rq * 8 + (ACTION_DEDUP_CODE[rAct] or 7)) or (rAct .. ":" .. tostring(rq))
                            locSeenActionQids[dKey] = true
                            if rAct == "TURN_IN" then locCompletedQids[rq] = true end
                        end
                    end
                end
            end

            if routeFilter ~= "all" and locPlan and locPlan.steps then
                for lPos, rawLSt in ipairs(locPlan.steps) do
                    local lSt = (ns.State.DecorateStepChain and ns.State.DecorateStepChain(rawLSt, player, t)) or rawLSt
                    local lq = lSt.questId or (lSt.qids and lSt.qids[1])
                    if lq then
                        local lAct = lSt.action or ""
                        local dKey = (type(lq) == "number") and (lq * 8 + (ACTION_DEDUP_CODE[lAct] or 7)) or (lAct .. ":" .. tostring(lq))
                        locSeenActionQids[dKey] = true
                        if lAct == "TURN_IN" or lAct == "DUNGEON_RUN" then
                            locCompletedQids[lq] = true
                        end
                    end
                    if routeFilter ~= "zone" or lSt.zoneId == zoneTarget then
                        local isCurrGps = false
                        if meta.source == "gps_reorder" or meta.source == "friend_pivot" then
                            if lPos == (meta.locationPos or 1) then
                                isCurrGps = true
                            elseif curGpsKey and lSt.gpsStepKey == curGpsKey then
                                isCurrGps = true
                            elseif curOrigIdx and lSt.origRouteIdx == curOrigIdx then
                                isCurrGps = true
                            end
                        end
                        ovCount = ovCount + 1
                        dispCount = dispCount + 1
                        displayList[dispCount] = AllocPooledRow(
                            overlayRowPool, ovCount, lSt, lSt.origRouteIdx, nil,
                            isCurrGps, false, false, false, nil, (lSt.origRouteIdx == nil), true,
                            lSt.isReadyTurnIn == true, lSt.isGpsReordered == true, lSt.isFriendChain == true
                        )
                    end
                end
            end

            local bpCount = 0
            local nonGpsSource = (meta.source ~= "ready_turnin" and meta.source ~= "gps_reorder" and meta.source ~= "friend_pivot")
            local notAllFilter = (routeFilter ~= "all")
            for i = 1, bCount do
                local fl = bFlags[i]
                local st = bSteps[i]
                local rIdx = bIdxs[i]
                local oIdx = bOwners[i]
                if fl == 0 then
                    local rq = st and st.questId
                    local rAct = st and st.action or ""
                    local dKey = rq and ((type(rq) == "number") and (rq * 8 + (ACTION_DEDUP_CODE[rAct] or 7)) or (rAct .. ":" .. tostring(rq))) or nil
                    local dupLoc = notAllFilter and rq and (locSeenActionQids[dKey] or (locCompletedQids[rq] and (rAct == "ACCEPT" or rAct == "COMPLETE" or rAct == "TURN_IN")))
                    if (not (suppressedMap and oIdx and suppressedMap[oIdx])) and (not dupLoc) then
                        bpCount = bpCount + 1
                        dispCount = dispCount + 1
                        local row = baseRowPool[bpCount]
                        if not row then
                            row = {}
                            baseRowPool[bpCount] = row
                        end
                        row.step = st
                        row.idx = rIdx ~= false and rIdx or nil
                        row.ownerIdx = oIdx ~= false and oIdx or nil
                        row.isCurrent = nonGpsSource and (cursorIdx == rIdx)
                        row.isDone = false
                        row.isSkipped = false
                        row.isBlocked = false
                        row.blockReason = nil
                        row.isDynamic = false
                        row.isInjected = false
                        row.isReadyTurnIn = false
                        row.isGpsReordered = nil
                        row.isFriendChain = nil
                        displayList[dispCount] = row
                    end
                else
                    local remFl = fl
                    if remFl >= 256 then remFl = remFl - 256 end
                    if remFl >= 128 then remFl = remFl - 128 end
                    if remFl >= 64 then remFl = remFl - 64 end
                    local isRdy = remFl >= 32
                    if isRdy then remFl = remFl - 32 end
                    local isInj = remFl >= 16
                    if isInj then remFl = remFl - 16 end
                    local isDyn = remFl >= 8
                    if isDyn then remFl = remFl - 8 end
                    if not (isInj and (oIdx == false or oIdx == nil)) then
                        local rq = st and st.questId
                        local rAct = st and st.action or ""
                        local dKey = rq and ((type(rq) == "number") and (rq * 8 + (ACTION_DEDUP_CODE[rAct] or 7)) or (rAct .. ":" .. tostring(rq))) or nil
                        local dupLoc = notAllFilter and rq and (locSeenActionQids[dKey] or (locCompletedQids[rq] and (rAct == "ACCEPT" or rAct == "COMPLETE" or rAct == "TURN_IN")))
                        if (not (suppressedMap and oIdx and suppressedMap[oIdx])) and (not dupLoc) then
                            local isBlk = remFl >= 4
                            if isBlk then remFl = remFl - 4 end
                            local isSkip = remFl >= 2
                            if isSkip then remFl = remFl - 2 end
                            local isDn = remFl >= 1
                            local isCur = isInj and (curReadyKey ~= nil and st and st.readyStepKey == curReadyKey) or ((not isInj) and nonGpsSource and (cursorIdx == rIdx))
                            bpCount = bpCount + 1
                            dispCount = dispCount + 1
                            displayList[dispCount] = AllocPooledRow(
                                baseRowPool, bpCount, st, rIdx ~= false and rIdx or nil, oIdx ~= false and oIdx or nil,
                                isCur, isDn, isSkip, isBlk, isBlk and bReasons[i] or nil,
                                isDyn, isInj, isRdy, nil, nil
                            )
                        end
                    end
                end
            end
            for k = dispCount + 1, #displayList do
                displayList[k] = nil
            end
        end

        routeTabCache.key = cacheKey
        routeTabCache.summaryText = sumText
        routeTabCache.displayList = displayList
    end

    return routeTabCache
end

local function RefreshRouteTab(playerHint)
    local p = tabPanels.route
    if not p then return end

    local cdb = ns.charDB or {}
    local activeKey = cdb.activeProfile or "balanced"
    local t = cdb.toggles or {}
    local isDynamic = t.dynamicRescore == true

    for _, pb in ipairs(presetButtons) do
        pb:SetSelected((not cdb.customWeights) and (pb.key == activeKey))
    end

    local w = ns.GetActiveWeights()
    sliders.xp:SetSliderValue(w.xp or 45)
    sliders.travel:SetSliderValue(w.travel or 25)
    sliders.dungeon:SetSliderValue(w.dungeon or 20)
    sliders.reward:SetSliderValue(w.reward or 10)
    if sliders.skinning then
        sliders.skinning:SetSliderValue(w.skinning or 0)
    end

    local prof = ns.State.DetectProfessions and ns.State.DetectProfessions() or {}
    local player = playerHint or ns.State.GetPlayerInfo()
    local maxOffset = ns.State.GetEffectiveMaxLevelOffset and ns.State.GetEffectiveMaxLevelOffset(t) or 2
    if sliders.maxLevel then
        sliders.maxLevel:SetSliderValue(maxOffset)
    end
    local classGearChecked = (t.prioritizeClassGear ~= false) and (t.prioritizeRogueGear ~= false)
    toggleChecks.dynamic:SetChecked(isDynamic)
    toggleChecks.dungeons:SetChecked(t.preferDungeonUnlocks ~= false)
    toggleChecks.bags:SetChecked(t.prioritizeBags ~= false)
    toggleChecks.rogue:SetChecked(classGearChecked)
    if toggleChecks.rogue.SetLabel then
        toggleChecks.rogue:SetLabel((player.className or "Class") .. " BiS")
    end
    toggleChecks.auto:SetChecked(t.autoAdvance ~= false)
    if toggleChecks.elite then toggleChecks.elite:SetChecked(t.skipEliteSolo ~= false) end
    if wpChecks.skipEliteSolo then wpChecks.skipEliteSolo:SetChecked(t.skipEliteSolo ~= false) end
    if toggleChecks.skipDungeons then toggleChecks.skipDungeons:SetChecked(t.skipDungeons ~= false) end
    if wpChecks.skipDungeons then wpChecks.skipDungeons:SetChecked(t.skipDungeons ~= false) end
    if toggleChecks.skinning then toggleChecks.skinning:SetChecked(prof.hasSkinning == true) end
    if toggleChecks.lw then toggleChecks.lw:SetChecked(prof.hasLeatherworking == true) end
    if toggleChecks.prioritizeProf then toggleChecks.prioritizeProf:SetChecked(t.prioritizeSkinningLW ~= false) end
    if toggleChecks.autoDetectProf then toggleChecks.autoDetectProf:SetChecked(t.autoDetectProfessions ~= false) end
    if toggleChecks.gps then toggleChecks.gps:SetChecked(t.locationReorder ~= false) end

    if p.raceBtn and p.raceBtn.label then
        local rTxt = "Race: " .. (player.raceName or "Gnome")
        if p.raceBtn._lastText ~= rTxt then
            p.raceBtn._lastText = rTxt
            p.raceBtn.label:SetText(rTxt)
        end
    end
    if p.classBtn and p.classBtn.label then
        local cTxt = "Class: " .. (player.className or "Rogue")
        if p.classBtn._lastText ~= cTxt then
            p.classBtn._lastText = cTxt
            p.classBtn.label:SetText(cTxt)
        end
    end
    if p.autoCharBtn then
        p.autoCharBtn:SetSelected(t.autoDetectChar ~= false)
        if p.autoCharBtn.label then
            local aTxt = (t.autoDetectChar ~= false) and "Char: Auto" or "Auto Char"
            if p.autoCharBtn._lastText ~= aTxt then
                p.autoCharBtn._lastText = aTxt
                p.autoCharBtn.label:SetText(aTxt)
            end
        end
    end

    p.filterRem:SetSelected(routeFilter == "remaining")
    p.filterAll:SetSelected(routeFilter == "all")
    p.filterZone:SetSelected(routeFilter == "zone")

    local rc = ComputeRouteTabCache(player)
    if p._lastSummaryText ~= rc.summaryText then
        p._lastSummaryText = rc.summaryText
        p.summaryText:SetText(rc.summaryText)
    end
    p.list:SetItems(rc.displayList, true)
end

local function RefreshDungeonsTab()
    local p = tabPanels.dungeons
    if not p then return end

    local player = ns.State.GetPlayerInfo()
    local gen = ns.State.GetRefreshGeneration and ns.State.GetRefreshGeneration() or 0

    p.nearBtn:SetSelected(not dungeonShowAll)
    p.allBtn:SetSelected(dungeonShowAll)

    p.rAllBtn:SetSelected(rewardFilter == "all")
    p.rBagBtn:SetSelected(rewardFilter == "bags")
    p.rGoldBtn:SetSelected(rewardFilter == "gold")
    p.rRogueBtn:SetSelected(rewardFilter == "rogue" or rewardFilter == "bis" or rewardFilter == "class")
    if p.rRogueBtn.label then
        p.rRogueBtn.label:SetText((player.className or "Class") .. " BiS")
    end
    if p.rSkinBtn then
        p.rSkinBtn:SetSelected(rewardFilter == "skinning_lw")
    end
    p.rLevelBtn:SetSelected(rewardShowAllLevels)

    -- Left list: Dungeons & Unlock Chains
    local dKey = string.format("%d:%d:%s:%s:%d:%s",
        gen,
        player.level or 24,
        tostring(dungeonShowAll == true),
        tostring(player.faction or "Alliance"),
        tonumber(player.raceId) or 0,
        tostring(player.classFile or "ROGUE"))
    if dungeonTabCache.key ~= dKey then
        local dungeons = ns.Scorer.GetDungeonsNearLevel(player.level, dungeonShowAll, player)
        local dEntries = {}
        for _, d in ipairs(dungeons) do
            dEntries[#dEntries + 1] = {
                kind = "header",
                left = string.format("|cffffd100%s|r |cffaaaaaa[%d-%d, rec %d]|r", d.name, d.minLevel, d.maxLevel, d.recommendedLevel),
                right = string.format("%d/%d Q", d.completedQuests, d.totalQuests),
                pinTarget = d.entranceCoords and {
                    zoneId = d.entranceCoords.zoneId or d.entranceZoneId,
                    zoneName = d.entranceZoneName,
                    x = d.entranceCoords.x,
                    y = d.entranceCoords.y,
                    title = d.name .. " Entrance",
                } or nil,
            }
            if d.entranceZoneName then
                local cStr = d.entranceCoords and ns.FormatCoords(d.entranceCoords.x, d.entranceCoords.y) or "?"
                dEntries[#dEntries + 1] = {
                    kind = "sub",
                    left = string.format("  |cff88bbddEntrance:|r %s (%s)", d.entranceZoneName, cStr),
                    right = "",
                }
            end
            for _, ch in ipairs(d.chains) do
                dEntries[#dEntries + 1] = {
                    kind = "sub",
                    left = string.format("  |cffff944dChain: %s|r (%d/%d)", ch.chainName, ch.doneCount, ch.totalCount),
                    right = ch.nextMissing and "|cffffcc33Next|r" or "|cff33ff99Ready|r",
                }
                if ch.travelTip and ch.travelTip ~= "" then
                    dEntries[#dEntries + 1] = {
                        kind = "sub",
                        left = "    |cff888888Tip: " .. ch.travelTip .. "|r",
                        right = "",
                    }
                end
                for _, cs in ipairs(ch.steps) do
                    local mark = (cs.status == "completed" and "|cff33ff99[✓]|r")
                        or (cs.status == "ready" and "|cff33ffcc[TurnIn]|r")
                        or (cs.status == "active" and "|cffc080ff[Log]|r")
                        or (cs.prereqsMet and "|cffffcc33[Next]|r")
                        or "|cff777777[Lock]|r"
                    local pt = cs.starter
                    dEntries[#dEntries + 1] = {
                        kind = "step",
                        left = string.format("    %s %s |cff888888[%d]|r", mark, cs.name, cs.questLevel or 0),
                        right = "",
                        pinTarget = (cs.status ~= "completed" and pt and pt.x and pt.y) and {
                            zoneId = pt.zoneId,
                            x = pt.x,
                            y = pt.y,
                            title = cs.name,
                            npcName = pt.name,
                        } or nil,
                    }
                end
            end
            for _, dq in ipairs(d.quests) do
                local mark = (dq.status == "completed" and "|cff33ff99[✓]|r")
                    or (dq.status == "ready" and "|cff33ffcc[TurnIn]|r")
                    or (dq.status == "active" and "|cffc080ff[Log]|r")
                    or "|cffaaaaaa[○]|r"
                local pt = dq.starter
                local extra = {}
                if dq.bagSlots and dq.bagSlots > 0 then extra[#extra + 1] = dq.bagSlots .. "Bag" end
                local dqBis = tonumber(dq.classScore) or tonumber(dq.rogueScore) or 0
                if dqBis >= 40 then extra[#extra + 1] = "BiS" end
                dEntries[#dEntries + 1] = {
                    kind = "step",
                    left = string.format("  %s %s [%d]", mark, dq.name, dq.questLevel or 0),
                    right = table.concat(extra, " "),
                    pinTarget = (dq.status ~= "completed" and pt and pt.x and pt.y) and {
                        zoneId = pt.zoneId,
                        x = pt.x,
                        y = pt.y,
                        title = dq.name,
                        npcName = pt.name,
                    } or nil,
                }
            end
        end
        dungeonTabCache.key = dKey
        dungeonTabCache.entries = dEntries
    end

    p.dungeonList:SetItems(dungeonTabCache.entries, true)

    -- Right list: Special Rewards
    local rKey = string.format("%d:%d:%s:%s:%s:%s:%s:%d:%s",
        gen,
        player.level or 24,
        tostring(rewardFilter),
        tostring(rewardShowAllLevels == true),
        tostring(player.hasSkinning == true),
        tostring(player.hasLeatherworking == true),
        tostring(player.faction or "Alliance"),
        tonumber(player.raceId) or 0,
        tostring(player.classFile or "ROGUE"))
    if rewardTabCache.key ~= rKey then
        rewardTabCache.key = rKey
        rewardTabCache.items = ns.Scorer.GetSpecialRewards(player, rewardFilter, 25, rewardShowAllLevels)
    end
    p.rewardList:SetItems(rewardTabCache.items, true)
end

local function RefreshDiagTab()
    local p = tabPanels.diag
    if not p then return end

    local db = ns.db or {}
    local toggles = (ns.charDB and ns.charDB.toggles) or {}
    wpChecks.autoWp:SetChecked(db.autoWaypoint ~= false)
    wpChecks.tomtom:SetChecked(db.useTomTom ~= false)
    wpChecks.native:SetChecked(db.useNativePin ~= false)
    wpChecks.openMap:SetChecked(db.openMapOnPin == true)
    wpChecks.minimap:SetChecked(db.showMinimap ~= false)
    wpChecks.hud:SetChecked(db.hudShown ~= false)
    local maxOffset = ns.State.GetEffectiveMaxLevelOffset and ns.State.GetEffectiveMaxLevelOffset(toggles) or 2
    if wpChecks.consultQuestie then
        wpChecks.consultQuestie:SetChecked(toggles.consultQuestie ~= false)
    end
    if wpChecks.allowRedQuests then
        wpChecks.allowRedQuests:SetChecked(maxOffset >= 5)
    end
    if wpChecks.skipEliteSolo then
        wpChecks.skipEliteSolo:SetChecked(toggles.skipEliteSolo ~= false)
    end
    if toggleChecks.elite then
        toggleChecks.elite:SetChecked(toggles.skipEliteSolo ~= false)
    end
    if wpChecks.skipDungeons then
        wpChecks.skipDungeons:SetChecked(toggles.skipDungeons ~= false)
    end
    if toggleChecks.skipDungeons then
        toggleChecks.skipDungeons:SetChecked(toggles.skipDungeons ~= false)
    end

    local gen = ns.State.GetRefreshGeneration and ns.State.GetRefreshGeneration() or 0
    local player = ns.State.GetPlayerInfo()
    local w = ns.GetActiveWeights()
    local readyCount = ns.State.GetReadyTurnInCount and ns.State.GetReadyTurnInCount() or 0
    local readySig = ns.State.GetReadyTurnInSignature and ns.State.GetReadyTurnInSignature() or ""
    local locSig = ns.State.GetLocationPlanSignature and ns.State.GetLocationPlanSignature() or ""
    local qx = player.x and math.floor(player.x * 0.5) or -1
    local qy = player.y and math.floor(player.y * 0.5) or -1
    local diagKey = string.format("%d:%d:%s:%s:%s:%d:%s:%s:%s:%d:%s:%s:%s:%s:%s:%s:%s:%s:%d:%d",
        gen,
        player.level or 24,
        tostring(player.zoneId or 0),
        WeightSig(w),
        tostring(toggles.consultQuestie ~= false),
        maxOffset,
        tostring(toggles.hasSkinning == true),
        tostring(toggles.hasLeatherworking == true),
        tostring(player.faction or "Alliance"),
        tonumber(player.raceId) or 0,
        tostring(player.classFile or "ROGUE"),
        tostring(toggles.autoDetectChar ~= false),
        tostring(toggles.locationReorder ~= false),
        tostring(toggles.skipEliteSolo ~= false),
        tostring(toggles.skipDungeons ~= false),
        tostring(player.inGroup == true),
        readySig,
        locSig,
        qx,
        qy)

    if diagTabCache.key ~= diagKey then
        local blocked = ns.State.GetBlockedSteps and ns.State.GetBlockedSteps() or {}
        local integrations = {}
        integrations[#integrations + 1] = "TomTom:" .. (TomTom and "|cff33ff99Yes|r" or "|cff888888No|r")
        integrations[#integrations + 1] = "OpenMap:" .. (OpenMapAPI and "|cff33ff99Yes|r" or "|cff888888No|r")
        integrations[#integrations + 1] = "Questie:" .. ((QuestieLoader or QuestieDB) and "|cff33ff99Yes|r" or "|cff888888No|r")
        integrations[#integrations + 1] = "Skin:" .. (player.hasSkinning and (player.skinningRank and tostring(player.skinningRank) or "|cff33ff99Yes|r") or "|cff888888No|r")
        integrations[#integrations + 1] = "LW:" .. (player.hasLeatherworking and (player.leatherworkingRank and tostring(player.leatherworkingRank) or "|cff33ff99Yes|r") or "|cff888888No|r")
        integrations[#integrations + 1] = "GPS:" .. ((toggles.locationReorder ~= false) and "|cff33ff99On|r" or "|cff888888Off|r")
        integrations[#integrations + 1] = "Elite:" .. ((player.inGroup == true) and "|cff33ff99Group|r" or ((toggles.skipEliteSolo ~= false) and "|cffffd200SkipSolo|r" or "|cff888888All|r"))
        integrations[#integrations + 1] = "Dung:" .. ((toggles.skipDungeons ~= false) and "|cffffd200Skip|r" or "|cff33ff99All|r")
        integrations[#integrations + 1] = "Guarded:" .. tostring(#blocked)
        if readyCount > 0 then
            integrations[#integrations + 1] = "Ready:" .. tostring(readyCount)
        end

        local offSign = (maxOffset >= 0) and "+" or ""
        local maxLvText = string.format("%s%d (<=Lv %d)", offSign, maxOffset, (player.level or 24) + maxOffset)
        local charModeTag = (player.isCharOverride or toggles.autoDetectChar == false) and " |cffffd200(Override)|r" or " |cff66ff88(Auto)|r"
        local infoText = string.format(
            "Character: |cffffffff%s|r (Lv %d %s %s %s%s, %d%% XP)  |  MaxLv: |cff33d9ff%s|r  |  Map: |cff33d9ff%s|r (%s)  |  %s",
            player.name, player.level, player.faction, player.raceName, player.className, charModeTag, player.xpPct,
            maxLvText, player.zoneName, ns.FormatCoords(player.x, player.y), table.concat(integrations, "  ")
        )

        local vCompleted = (ns.State.GetCompletedReadOnly and ns.State.GetCompletedReadOnly()) or (ns.State.GetCompletedSet and ns.State.GetCompletedSet()) or {}
        local vState = {
            player = player,
            level = player.level,
            xpPct = player.xpPct,
            zoneId = player.zoneId,
            zoneName = player.zoneName,
            x = player.x or 50,
            y = player.y or 50,
            completed = vCompleted,
            active = ns.State.GetActiveQuests() or {},
            stepCounter = 1,
            isLiveState = true,
            maxCandidates = 12,
        }
        local ranked = ns.Scorer.RankAvailableCandidates(vState, w, toggles)

        local diagEntries = {}
        local maxBlocked = math.min(8, #blocked)
        for i = 1, maxBlocked do
            local blk = blocked[i]
            diagEntries[#diagEntries + 1] = {
                isBlocked = true,
                left = string.format("|cffff6644[BLOCKED]|r #%d %s — %s", blk.index or 0, blk.title or "Step", blk.zoneName or ""),
                right = blk.reason or blk.code or "Blocked",
            }
        end
        local maxRank = math.min(12, #ranked)
        for i = 1, maxRank do
            local c = ranked[i]
            local sb = c.step.scoreBreakdown or {}
            diagEntries[#diagEntries + 1] = {
                isBlocked = false,
                left = string.format("#%d [%.1f] %s — %s (%s)", i, c.score, c.step.action, c.step.title, c.step.zoneName or ""),
                right = string.format("XP:%d  Trv:-%d  Dng:%d  Rwd:%d  Skn:%d", sb.xp or 0, sb.travel or 0, sb.dungeon or 0, sb.reward or 0, sb.skinning or 0),
            }
        end

        diagTabCache.key = diagKey
        diagTabCache.infoText = infoText
        diagTabCache.entries = diagEntries
    end

    p.infoText:SetText(diagTabCache.infoText)
    p.diagList:SetItems(diagTabCache.entries, true)
end

function Window.Refresh()
    if not frame or not frame:IsShown() then return end

    local player = ns.State.GetPlayerInfo()
    local charTxt = string.format("Lv %d %s %s %s%s · %s",
        player.level,
        player.faction or "Alliance",
        player.raceName,
        player.className,
        player.isCharOverride and " (Override)" or "",
        player.zoneName)
    if frame._lastCharText ~= charTxt then
        frame._lastCharText = charTxt
        frame.charText:SetText(charTxt)
    end

    local panelKey = (activeTab == "rewards") and "dungeons" or activeTab
    for key, btn in pairs(tabButtons) do
        btn:SetSelected(key == panelKey)
    end
    if frame._activePanelKey ~= panelKey then
        frame._activePanelKey = panelKey
        for key, panel in pairs(tabPanels) do
            if key == panelKey then panel:Show() else panel:Hide() end
        end
    end

    if panelKey == "route" then
        RefreshRouteTab(player)
    elseif panelKey == "dungeons" then
        RefreshDungeonsTab()
    elseif panelKey == "diag" then
        RefreshDiagTab()
    end
end

function Window.ShowTab(tabKey)
    if tabKey == "route" or tabKey == "dungeons" or tabKey == "rewards" or tabKey == "diag" then
        activeTab = tabKey
    end
    Window.SetShown(true)
end

--------------------------------------------------------------------------------
-- Frame Construction
--------------------------------------------------------------------------------

local function CreateWindowFrame()
    if frame or not CreateFrame then return end

    frame = CreateFrame("Frame", "ForeverQuestPathWindow", UIParent, "BackdropTemplate")
    frame:SetSize(WIN_W, WIN_H)
    frame:SetFrameStrata("DIALOG")
    frame:SetClampedToScreen(true)
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    ApplyBackdrop(frame, 0.06, 0.07, 0.09, 0.96, 0.25, 0.65, 0.80, 0.9)

    local pos = ns.db and ns.db.windowPos
    if pos and pos.point then
        frame:SetPoint(pos.point, UIParent, pos.relPoint or pos.point, pos.x or 0, pos.y or 0)
    else
        frame:SetPoint("CENTER", UIParent, "CENTER", 0, 20)
    end

    frame:SetScript("OnDragStart", function(self) if self.StartMoving then self:StartMoving() end end)
    frame:SetScript("OnDragStop", function(self)
        if self.StopMovingOrSizing then self:StopMovingOrSizing() end
        if ns.db then
            local pt, _, rel, x, y = self:GetPoint(1)
            if pt then ns.db.windowPos = { point = pt, relPoint = rel or pt, x = x or 0, y = y or 0 } end
        end
    end)

    if UISpecialFrames then
        table.insert(UISpecialFrames, "ForeverQuestPathWindow")
    end

    -- Title Bar
    local titleBar = CreateFrame("Frame", nil, frame, "BackdropTemplate")
    titleBar:SetPoint("TOPLEFT", 1, -1)
    titleBar:SetPoint("TOPRIGHT", -1, -1)
    titleBar:SetHeight(HEADER_H)
    ApplyBackdrop(titleBar, 0.10, 0.13, 0.18, 1, 0.18, 0.24, 0.30, 1)

    local titleText = titleBar:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    titleText:SetPoint("LEFT", 12, 0)
    titleText:SetText("|cff33d9ffForeverQuestPath|r  |cff888888v" .. ns.version .. "|r")

    local closeBtn = MakeButton(titleBar, "X", 22, 20)
    closeBtn:SetPoint("RIGHT", -6, 0)
    closeBtn:SetScript("OnClick", function() frame:Hide() end)

    frame.charText = titleBar:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    frame.charText:SetPoint("RIGHT", closeBtn, "LEFT", -12, 0)

    -- Tabs
    local tabDefs = {
        { key = "route",    label = "Route & Balance" },
        { key = "dungeons", label = "Dungeon Unlocks & Rewards" },
        { key = "diag",     label = "Settings & Scorer" },
    }
    for i, td in ipairs(tabDefs) do
        local tb = MakeButton(frame, td.label, 175, TAB_H)
        tb:SetPoint("TOPLEFT", frame, "TOPLEFT", PAD + (i - 1) * 180, -(HEADER_H + 8))
        tb:SetScript("OnClick", function()
            activeTab = td.key
            Window.Refresh()
        end)
        tabButtons[td.key] = tb
    end

    local bodyTop = -(HEADER_H + 8 + TAB_H + 8)

    ------------------------------------------------------------------ Tab 1: Route
    local pRoute = CreateFrame("Frame", nil, frame)
    pRoute:SetPoint("TOPLEFT", frame, "TOPLEFT", PAD, bodyTop)
    pRoute:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -PAD, PAD)
    tabPanels.route = pRoute

    for i, pr in ipairs(PRESETS) do
        local b = MakeButton(pRoute, pr.label, 104, 22)
        b:SetPoint("TOPLEFT", (i - 1) * 108, 0)
        b.key = pr.key
        b:SetScript("OnClick", function()
            ns.State.SetProfile(pr.key)
        end)
        presetButtons[i] = b
    end

    local rescoreBtn = MakeButton(pRoute, "Re-Score Live", 98, 22)
    rescoreBtn:SetPoint("TOPRIGHT", -84, 0)
    rescoreBtn:SetScript("OnClick", function()
        if ns.charDB and ns.charDB.toggles then
            ns.charDB.toggles.dynamicRescore = true
        end
        ns.State.Refresh("MANUAL_RESCORE")
    end)

    local resetBtn = MakeButton(pRoute, "Reset Route", 80, 22)
    resetBtn:SetPoint("TOPRIGHT", 0, 0)
    resetBtn:SetScript("OnClick", function()
        ns.State.ResetProgress()
    end)

    pRoute.summaryText = pRoute:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    pRoute.summaryText:SetPoint("TOPLEFT", 0, -28)
    pRoute.summaryText:SetPoint("TOPRIGHT", 0, -28)
    pRoute.summaryText:SetJustifyH("LEFT")

    -- Balance Sliders & Toggles Box
    local balBox = CreateFrame("Frame", nil, pRoute, "BackdropTemplate")
    balBox:SetPoint("TOPLEFT", 0, -48)
    balBox:SetPoint("TOPRIGHT", 0, -48)
    balBox:SetHeight(128)
    ApplyBackdrop(balBox, 0.08, 0.10, 0.13, 0.9, 0.20, 0.25, 0.32, 1)

    local function PushSliderWeights()
        local w = {
            xp = sliders.xp.slider:GetValue(),
            travel = sliders.travel.slider:GetValue(),
            dungeon = sliders.dungeon.slider:GetValue(),
            reward = sliders.reward.slider:GetValue(),
            skinning = sliders.skinning.slider:GetValue(),
        }
        ns.State.SetCustomWeights(w, true)
    end

    local function FormatMaxLevelLabel(off)
        local lv = (ns.State and ns.State.GetPlayerInfo and ns.State.GetPlayerInfo().level) or 24
        local n = tonumber(off) or 2
        n = math.floor(n + 0.5)
        if n == 0 then n = 0 end
        local sign = (n >= 0) and "+" or ""
        return string.format("%s%d (<=%d)", sign, n, lv + n)
    end

    local sw = 110
    sliders.xp       = MakeSlider(balBox, "XP Efficiency", sw, 0, 100, 5, PushSliderWeights)
    sliders.travel   = MakeSlider(balBox, "Travel Penalty", sw, 0, 100, 5, PushSliderWeights)
    sliders.dungeon  = MakeSlider(balBox, "Dungeons", sw, 0, 100, 5, PushSliderWeights)
    sliders.reward   = MakeSlider(balBox, "Gold/Bags/BiS", sw, 0, 100, 5, PushSliderWeights)
    sliders.skinning = MakeSlider(balBox, "Skinning / LW", sw, 0, 100, 5, PushSliderWeights)
    sliders.maxLevel = MakeSlider(balBox, "Max Quest Lv", 130, -3, 5, 1, function(val)
        if ns.State and ns.State.SetMaxLevelOffset then
            ns.State.SetMaxLevelOffset(val)
        end
    end, FormatMaxLevelLabel)

    sliders.xp:SetPoint("TOPLEFT", 8, -6)
    sliders.travel:SetPoint("TOPLEFT", 126, -6)
    sliders.dungeon:SetPoint("TOPLEFT", 244, -6)
    sliders.reward:SetPoint("TOPLEFT", 362, -6)
    sliders.skinning:SetPoint("TOPLEFT", 480, -6)
    sliders.maxLevel:SetPoint("TOPLEFT", 598, -6)

    local function ToggleOpt(optKey)
        return function(val)
            ns.charDB.toggles[optKey] = val
            ns.State.Refresh("TOGGLE_" .. optKey)
        end
    end

    local function ToggleProfOpt(optKey)
        return function(val)
            ns.charDB.toggles.autoDetectProfessions = false
            ns.charDB.toggles[optKey] = val
            ns.State.Refresh("TOGGLE_" .. optKey)
        end
    end

    toggleChecks.dynamic  = MakeCheckbox(balBox, "Live Re-Scorer", 128, ToggleOpt("dynamicRescore"))
    toggleChecks.dungeons = MakeCheckbox(balBox, "Dungeon Unlocks", 130, ToggleOpt("preferDungeonUnlocks"))
    toggleChecks.bags     = MakeCheckbox(balBox, "Bag Rewards", 110, ToggleOpt("prioritizeBags"))
    toggleChecks.rogue    = MakeCheckbox(balBox, "Class BiS", 104, function(val)
        ns.charDB.toggles.prioritizeRogueGear = val
        ns.charDB.toggles.prioritizeClassGear = val
        ns.State.Refresh("TOGGLE_prioritizeClassGear")
    end)
    toggleChecks.auto     = MakeCheckbox(balBox, "Auto-Advance", 112, ToggleOpt("autoAdvance"))
    toggleChecks.elite    = MakeCheckbox(balBox, "Skip Elite (Solo)", 118, ToggleOpt("skipEliteSolo"))

    toggleChecks.dynamic:SetPoint("TOPLEFT", 10, -48)
    toggleChecks.dungeons:SetPoint("TOPLEFT", 144, -48)
    toggleChecks.bags:SetPoint("TOPLEFT", 280, -48)
    toggleChecks.rogue:SetPoint("TOPLEFT", 396, -48)
    toggleChecks.auto:SetPoint("TOPLEFT", 506, -48)
    toggleChecks.elite:SetPoint("TOPLEFT", 620, -48)

    toggleChecks.skinning       = MakeCheckbox(balBox, "Have Skinning", 104, ToggleProfOpt("hasSkinning"))
    toggleChecks.lw             = MakeCheckbox(balBox, "Have Leatherworking", 136, ToggleProfOpt("hasLeatherworking"))
    toggleChecks.prioritizeProf = MakeCheckbox(balBox, "Prioritize Skin/LW", 122, ToggleOpt("prioritizeSkinningLW"))
    toggleChecks.autoDetectProf = MakeCheckbox(balBox, "Auto-Detect Profs", 120, ToggleOpt("autoDetectProfessions"))
    toggleChecks.gps            = MakeCheckbox(balBox, "Auto-Reorder (GPS)", 126, ToggleOpt("locationReorder"))
    toggleChecks.skipDungeons   = MakeCheckbox(balBox, "Skip Dungeons", 106, ToggleOpt("skipDungeons"))

    toggleChecks.skinning:SetPoint("TOPLEFT", 10, -70)
    toggleChecks.lw:SetPoint("TOPLEFT", 116, -70)
    toggleChecks.prioritizeProf:SetPoint("TOPLEFT", 254, -70)
    toggleChecks.autoDetectProf:SetPoint("TOPLEFT", 378, -70)
    toggleChecks.gps:SetPoint("TOPLEFT", 500, -70)
    toggleChecks.skipDungeons:SetPoint("TOPLEFT", 628, -70)

    pRoute.filterRem  = MakeButton(balBox, "Upcoming", 80, 20)
    pRoute.filterAll  = MakeButton(balBox, "All Steps", 76, 20)
    pRoute.filterZone = MakeButton(balBox, "Current Zone", 92, 20)
    pRoute.filterRem:SetPoint("BOTTOMLEFT", 10, 6)
    pRoute.filterAll:SetPoint("LEFT", pRoute.filterRem, "RIGHT", 6, 0)
    pRoute.filterZone:SetPoint("LEFT", pRoute.filterAll, "RIGHT", 6, 0)
    pRoute.filterRem:SetScript("OnClick", function() routeFilter = "remaining" RefreshRouteTab() end)
    pRoute.filterAll:SetScript("OnClick", function() routeFilter = "all" RefreshRouteTab() end)
    pRoute.filterZone:SetScript("OnClick", function() routeFilter = "zone" RefreshRouteTab() end)

    pRoute.raceBtn = MakeButton(balBox, "Race: Gnome", 126, 20)
    pRoute.classBtn = MakeButton(balBox, "Class: Rogue", 118, 20)
    pRoute.autoCharBtn = MakeButton(balBox, "Char: Auto", 84, 20)
    pRoute.autoCharBtn:SetPoint("BOTTOMRIGHT", -10, 6)
    pRoute.classBtn:SetPoint("RIGHT", pRoute.autoCharBtn, "LEFT", -6, 0)
    pRoute.raceBtn:SetPoint("RIGHT", pRoute.classBtn, "LEFT", -6, 0)

    pRoute.raceBtn:SetScript("OnClick", function()
        local races = ns.State.GetAvailableRaces and ns.State.GetAvailableRaces() or {}
        if #races == 0 then return end
        local curInfo = ns.State.GetPlayerInfo()
        local cur = curInfo.raceFile
        local idx = 1
        for i, r in ipairs(races) do
            if r.key == cur then idx = i break end
        end
        local nextRace = races[(idx % #races) + 1]
        if nextRace then
            local nextClass = curInfo.classFile
            local validClasses = ns.State.GetAvailableClasses and ns.State.GetAvailableClasses(nextRace.key) or {}
            if #validClasses > 0 then
                local isValid = false
                for _, c in ipairs(validClasses) do
                    if c.key == nextClass then
                        isValid = true
                        break
                    end
                end
                if not isValid then
                    nextClass = validClasses[1].key
                end
            end
            ns.State.SetCharacterOverride(nextRace.key, nextClass)
        end
    end)
    pRoute.classBtn:SetScript("OnClick", function()
        local curInfo = ns.State.GetPlayerInfo()
        local classes = ns.State.GetAvailableClasses and ns.State.GetAvailableClasses(curInfo.raceFile) or {}
        if #classes == 0 then return end
        local cur = curInfo.classFile
        local idx = 1
        for i, c in ipairs(classes) do
            if c.key == cur then idx = i break end
        end
        local nextClass = classes[(idx % #classes) + 1]
        if nextClass then
            ns.State.SetCharacterOverride(nil, nextClass.key)
        end
    end)
    pRoute.autoCharBtn:SetScript("OnClick", function()
        ns.State.ClearCharacterOverride()
    end)

    pRoute.list = MakeScrollList(pRoute, WIN_W - 2 * PAD, 274, 26, 15, CreateRouteRow, BindRouteRow)
    pRoute.list:SetPoint("TOPLEFT", balBox, "BOTTOMLEFT", 0, -8)

    ------------------------------------------------------------------ Tab 2: Dungeons & Rewards
    local pDun = CreateFrame("Frame", nil, frame)
    pDun:SetPoint("TOPLEFT", frame, "TOPLEFT", PAD, bodyTop)
    pDun:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -PAD, PAD)
    tabPanels.dungeons = pDun

    local leftW = 390
    local rightW = (WIN_W - 2 * PAD) - leftW - 10

    pDun.nearBtn = MakeButton(pDun, "Near My Level", 110, 20)
    pDun.allBtn  = MakeButton(pDun, "All Dungeons", 105, 20)
    pDun.nearBtn:SetPoint("TOPLEFT", 0, 0)
    pDun.allBtn:SetPoint("LEFT", pDun.nearBtn, "RIGHT", 6, 0)
    pDun.nearBtn:SetScript("OnClick", function() dungeonShowAll = false RefreshDungeonsTab() end)
    pDun.allBtn:SetScript("OnClick", function() dungeonShowAll = true RefreshDungeonsTab() end)

    pDun.dungeonList = MakeScrollList(pDun, leftW, 430, 22, 22, CreateDungeonRow, BindDungeonRow)
    pDun.dungeonList:SetPoint("TOPLEFT", 0, -26)

    pDun.rAllBtn   = MakeButton(pDun, "All", 32, 20)
    pDun.rBagBtn   = MakeButton(pDun, "Bags", 42, 20)
    pDun.rGoldBtn  = MakeButton(pDun, "Gold", 42, 20)
    pDun.rRogueBtn = MakeButton(pDun, "Class BiS", 76, 20)
    pDun.rSkinBtn  = MakeButton(pDun, "Skin/LW", 60, 20)
    pDun.rLevelBtn = MakeButton(pDun, "All Lvl", 54, 20)

    local rx0 = leftW + 10
    pDun.rAllBtn:SetPoint("TOPLEFT", rx0, 0)
    pDun.rBagBtn:SetPoint("LEFT", pDun.rAllBtn, "RIGHT", 4, 0)
    pDun.rGoldBtn:SetPoint("LEFT", pDun.rBagBtn, "RIGHT", 4, 0)
    pDun.rRogueBtn:SetPoint("LEFT", pDun.rGoldBtn, "RIGHT", 4, 0)
    pDun.rSkinBtn:SetPoint("LEFT", pDun.rRogueBtn, "RIGHT", 4, 0)
    pDun.rLevelBtn:SetPoint("LEFT", pDun.rSkinBtn, "RIGHT", 4, 0)

    pDun.rAllBtn:SetScript("OnClick", function() rewardFilter = "all" RefreshDungeonsTab() end)
    pDun.rBagBtn:SetScript("OnClick", function() rewardFilter = "bags" RefreshDungeonsTab() end)
    pDun.rGoldBtn:SetScript("OnClick", function() rewardFilter = "gold" RefreshDungeonsTab() end)
    pDun.rRogueBtn:SetScript("OnClick", function() rewardFilter = "rogue" RefreshDungeonsTab() end)
    pDun.rSkinBtn:SetScript("OnClick", function() rewardFilter = "skinning_lw" RefreshDungeonsTab() end)
    pDun.rLevelBtn:SetScript("OnClick", function() rewardShowAllLevels = not rewardShowAllLevels RefreshDungeonsTab() end)

    pDun.rewardList = MakeScrollList(pDun, rightW, 430, 36, 14, CreateRewardRow, BindRewardRow)
    pDun.rewardList:SetPoint("TOPLEFT", rx0, -26)

    ------------------------------------------------------------------ Tab 3: Settings & Diag
    local pDiag = CreateFrame("Frame", nil, frame)
    pDiag:SetPoint("TOPLEFT", frame, "TOPLEFT", PAD, bodyTop)
    pDiag:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -PAD, PAD)
    tabPanels.diag = pDiag

    local setBox = CreateFrame("Frame", nil, pDiag, "BackdropTemplate")
    setBox:SetPoint("TOPLEFT", 0, 0)
    setBox:SetPoint("TOPRIGHT", 0, 0)
    setBox:SetHeight(122)
    ApplyBackdrop(setBox, 0.08, 0.10, 0.13, 0.9, 0.20, 0.25, 0.32, 1)

    wpChecks.autoWp = MakeCheckbox(setBox, "Auto-Set Waypoint on Step Change", 230, function(v) ns.db.autoWaypoint = v end)
    wpChecks.tomtom = MakeCheckbox(setBox, "TomTom Crazy Arrow Integration", 220, function(v) ns.db.useTomTom = v end)
    wpChecks.native = MakeCheckbox(setBox, "Blizzard Map Pin + SuperTrack", 220, function(v) ns.db.useNativePin = v end)
    wpChecks.openMap = MakeCheckbox(setBox, "Open World Map / OpenMap on Pin", 230, function(v) ns.db.openMapOnPin = v end)
    wpChecks.minimap = MakeCheckbox(setBox, "Show Minimap Button", 180, function(v)
        ns.db.showMinimap = v
        if ns.Minimap and ns.Minimap.Refresh then ns.Minimap.Refresh() end
    end)
    wpChecks.hud = MakeCheckbox(setBox, "Show Compact HUD Tracker", 190, function(v)
        if ns.HUD and ns.HUD.SetShown then ns.HUD.SetShown(v) end
    end)

    wpChecks.autoWp:SetPoint("TOPLEFT", 10, -8)
    wpChecks.tomtom:SetPoint("TOPLEFT", 250, -8)
    wpChecks.native:SetPoint("TOPLEFT", 480, -8)
    wpChecks.openMap:SetPoint("TOPLEFT", 10, -34)
    wpChecks.minimap:SetPoint("TOPLEFT", 250, -34)
    wpChecks.hud:SetPoint("TOPLEFT", 480, -34)

    local clrWpBtn = MakeButton(setBox, "Clear Waypoints", 110, 22)
    clrWpBtn:SetPoint("TOPLEFT", 10, -60)
    clrWpBtn:SetScript("OnClick", function()
        if ns.Waypoint and ns.Waypoint.Clear then ns.Waypoint.Clear() end
        ns.Print("Cleared active waypoints.")
    end)

    local resyncBtn = MakeButton(setBox, "Resync Character & Flags", 146, 22)
    resyncBtn:SetPoint("LEFT", clrWpBtn, "RIGHT", 6, 0)
    resyncBtn:SetScript("OnClick", function()
        ns.State.ResetProgress()
    end)

    wpChecks.consultQuestie = MakeCheckbox(setBox, "QuestieDB Filter", 126, function(v)
        if ns.charDB and ns.charDB.toggles then
            ns.charDB.toggles.consultQuestie = v
            ns.State.Refresh("GUARD_TOGGLED")
        end
    end)
    wpChecks.consultQuestie:SetPoint("BOTTOMLEFT", 10, 6)

    wpChecks.allowRedQuests = MakeCheckbox(setBox, "Allow Red (+5) Quests", 156, function(v)
        if ns.State and ns.State.SetMaxLevelOffset then
            if v then
                local cur = (ns.State.GetMaxLevelOffset and ns.State.GetMaxLevelOffset()) or (ns.charDB and ns.charDB.maxLevelOffset) or 2
                if cur < 5 and ns.charDB then
                    ns.charDB.prevNonRedMaxLevelOffset = cur
                end
                ns.State.SetMaxLevelOffset(5)
            else
                local prev = ns.charDB and ns.charDB.prevNonRedMaxLevelOffset
                local restore = (prev ~= nil and prev < 5) and prev or 2
                ns.State.SetMaxLevelOffset(restore)
            end
        elseif ns.charDB and ns.charDB.toggles then
            ns.charDB.toggles.allowRedQuests = v
            ns.State.Refresh("GUARD_TOGGLED")
        end
    end)
    wpChecks.allowRedQuests:SetPoint("LEFT", wpChecks.consultQuestie, "RIGHT", 6, 0)

    wpChecks.skipEliteSolo = MakeCheckbox(setBox, "Skip Elite (Solo)", 132, function(v)
        if ns.charDB and ns.charDB.toggles then
            ns.charDB.toggles.skipEliteSolo = v
            ns.State.Refresh("TOGGLE_skipEliteSolo")
        end
    end)
    wpChecks.skipEliteSolo:SetPoint("LEFT", wpChecks.allowRedQuests, "RIGHT", 6, 0)

    wpChecks.skipDungeons = MakeCheckbox(setBox, "Skip Dungeons", 118, function(v)
        if ns.charDB and ns.charDB.toggles then
            ns.charDB.toggles.skipDungeons = v
            ns.State.Refresh("TOGGLE_skipDungeons")
        end
    end)
    wpChecks.skipDungeons:SetPoint("LEFT", wpChecks.skipEliteSolo, "RIGHT", 6, 0)

    pDiag.infoText = pDiag:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    pDiag.infoText:SetPoint("TOPLEFT", setBox, "BOTTOMLEFT", 0, -10)
    pDiag.infoText:SetPoint("TOPRIGHT", setBox, "BOTTOMRIGHT", 0, -10)
    pDiag.infoText:SetJustifyH("LEFT")

    local diagTitle = pDiag:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    diagTitle:SetPoint("TOPLEFT", pDiag.infoText, "BOTTOMLEFT", 0, -10)
    diagTitle:SetText("|cff99bbccGuard Log (Blocked Steps) & Live Re-Scorer Candidate Ranking|r")

    pDiag.diagList = MakeScrollList(pDiag, WIN_W - 2 * PAD, 286, 26, 14, CreateDiagRow, BindDiagRow)
    pDiag.diagList:SetPoint("TOPLEFT", diagTitle, "BOTTOMLEFT", 0, -6)

    frame:Hide()
end

function Window.SetShown(show)
    if not frame and show then
        CreateWindowFrame()
    end
    if frame then
        if show then
            frame:Show()
            Window.Refresh()
        else
            frame:Hide()
        end
    end
end

function Window.Toggle()
    local isShown = frame and frame:IsShown()
    Window.SetShown(not isShown)
end

function Window.SelectCharacter(raceToken, classToken, factionToken)
    if ns.State and ns.State.SetCharacterOverride then
        return ns.State.SetCharacterOverride(raceToken, classToken, factionToken)
    end
    return false
end

function Window.ClearCharacterOverride()
    if ns.State and ns.State.ClearCharacterOverride then
        ns.State.ClearCharacterOverride()
    end
end

function Window.SetMaxLevelOffset(offset)
    if ns.State and ns.State.SetMaxLevelOffset then
        return ns.State.SetMaxLevelOffset(offset)
    end
    return offset
end

function Window.Initialize()
    if CreateFrame and UIParent then
        CreateWindowFrame()
    end
    ns.RegisterCallback("STATE_UPDATED", function(reason)
        if (not LOCATION_ONLY_REASONS[reason])
            and reason ~= "QUEST_ACCEPTED"
            and reason ~= "QUEST_TURNED_IN"
            and reason ~= "QUEST_REMOVED"
            and reason ~= "MAX_LEVEL_CHANGED" then
            routeStaticSeq = routeStaticSeq + 1
        end
        Window.Refresh()
    end)
end
