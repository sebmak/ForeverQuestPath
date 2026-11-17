--------------------------------------------------------------------------------
-- UI/Minimap.lua
-- Rules:
--   1. Provides the draggable minimap launcher button (ForeverQuestPathMinimapButton)
--      and WoW Forever AddonCompartment callbacks using the circularly-masked
--      bundled Media/IconRound texture for quick HUD/Window/Pin access.
--   2. Respects ns.db.showMinimap and persists ns.db.minimapAngle cleanly.
--   3. Safe no-op in headless Lua test harnesses where Minimap/GameTooltip/CreateFrame
--      may be absent or partial.
--------------------------------------------------------------------------------

local _, ns = ...

local MinimapMod = {}
ns.Minimap = MinimapMod

local ICON_TEXTURE = ns.ICON_ROUND_TEXTURE or "Interface\\AddOns\\ForeverQuestPath\\Media\\IconRound"
local DEFAULT_ANGLE = 215
local RADIUS_OFFSET = 5

local MINIMAP_SHAPES = {
    ["ROUND"]                 = { true,  true,  true,  true  },
    ["SQUARE"]                = { false, false, false, false },
    ["CORNER-TOPLEFT"]        = { false, false, false, true  },
    ["CORNER-TOPRIGHT"]       = { false, false, true,  false },
    ["CORNER-BOTTOMLEFT"]     = { false, true,  false, false },
    ["CORNER-BOTTOMRIGHT"]    = { true,  false, false, false },
    ["SIDE-LEFT"]             = { false, true,  false, true  },
    ["SIDE-RIGHT"]            = { true,  false, true,  false },
    ["SIDE-TOP"]              = { false, false, true,  true  },
    ["SIDE-BOTTOM"]           = { true,  true,  false, false },
    ["TRICORNER-TOPLEFT"]     = { false, true,  true,  true  },
    ["TRICORNER-TOPRIGHT"]    = { true,  false, true,  true  },
    ["TRICORNER-BOTTOMLEFT"]  = { true,  true,  false, true  },
    ["TRICORNER-BOTTOMRIGHT"] = { true,  true,  true,  false },
}

local button = nil

local function Atan2(y, x)
    if math.atan2 then
        return math.atan2(y, x)
    end
    return math.atan(y, x)
end

local function SetWidgetVisible(widget, visible)
    if not widget then return end
    if widget.SetShown then
        widget:SetShown(visible)
    elseif visible and widget.Show then
        widget:Show()
    elseif not visible and widget.Hide then
        widget:Hide()
    end
end

local function StripColorCodes(text)
    if type(text) ~= "string" then return "" end
    local clean = text:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
    return clean
end

local function PopulateTooltip(tooltip, isCompartment)
    if not tooltip or not tooltip.AddLine then return end

    tooltip:AddLine("Forever Quest Path", 0.20, 0.85, 1.00)

    local vm = ns.HUD and ns.HUD.GetViewModel and ns.HUD.GetViewModel() or nil
    if vm then
        if vm.playerLine then
            tooltip:AddDoubleLine(vm.playerLine, vm.modeLabel or "Balanced", 0.88, 0.90, 0.94, 0.45, 0.85, 1.00)
        end
        if vm.current then
            tooltip:AddLine(" ")
            tooltip:AddLine("Next: " .. StripColorCodes(vm.current.actionText or "") .. " " .. (vm.current.title or ""), 1.00, 0.92, 0.55, true)
            if vm.current.whereLine and vm.current.whereLine ~= "" then
                tooltip:AddLine(StripColorCodes(vm.current.whereLine), 0.78, 0.82, 0.88, true)
            end
        end
    end

    tooltip:AddLine(" ")
    tooltip:AddDoubleLine("Left-Click", "Route & Config Window", 0.70, 0.86, 1.00, 0.90, 0.90, 0.90)
    tooltip:AddDoubleLine("Right-Click", "Toggle HUD Tracker", 0.70, 0.86, 1.00, 0.90, 0.90, 0.90)
    tooltip:AddDoubleLine("Shift-Left-Click", "Pin Current Waypoint", 0.70, 0.86, 1.00, 0.90, 0.90, 0.90)
    if not isCompartment then
        tooltip:AddDoubleLine("Drag", "Move Minimap Button", 0.70, 0.86, 1.00, 0.90, 0.90, 0.90)
    end
end

local function HandleClickAction(mouseButton)
    local isShift = IsShiftKeyDown and IsShiftKeyDown()
    if mouseButton == "RightButton" then
        if ns.HUD and ns.HUD.Toggle then
            ns.HUD.Toggle()
        end
    elseif isShift then
        if ns.Waypoint and ns.Waypoint.SyncToCurrentStep then
            ns.Waypoint.SyncToCurrentStep(true)
        end
    else
        if ns.Window and ns.Window.Toggle then
            ns.Window.Toggle()
        end
    end
end

local function UpdatePosition()
    if not button or not Minimap then return end

    local angleDeg = (ns.db and tonumber(ns.db.minimapAngle)) or DEFAULT_ANGLE
    local rad = math.rad(angleDeg)
    local x = math.cos(rad)
    local y = math.sin(rad)

    local quadrant = 1
    if x < 0 then quadrant = quadrant + 1 end
    if y > 0 then quadrant = quadrant + 2 end

    local shape = (GetMinimapShape and GetMinimapShape()) or "ROUND"
    local quadTable = MINIMAP_SHAPES[shape] or MINIMAP_SHAPES["ROUND"]

    local halfW = ((Minimap.GetWidth and Minimap:GetWidth()) or 140) / 2 + RADIUS_OFFSET
    local halfH = ((Minimap.GetHeight and Minimap:GetHeight()) or 140) / 2 + RADIUS_OFFSET

    if quadTable[quadrant] then
        x = x * halfW
        y = y * halfH
    else
        local diagW = math.sqrt(2 * (halfW ^ 2)) - 10
        local diagH = math.sqrt(2 * (halfH ^ 2)) - 10
        x = math.max(-halfW, math.min(x * diagW, halfW))
        y = math.max(-halfH, math.min(y * diagH, halfH))
    end

    button:ClearAllPoints()
    button:SetPoint("CENTER", Minimap, "CENTER", x, y)
end

local function OnDragUpdate(self)
    if not Minimap or not GetCursorPosition or not Minimap.GetCenter then return end
    local cx, cy = Minimap:GetCenter()
    if not cx or not cy then return end
    local mx, my = GetCursorPosition()
    local scale = (Minimap.GetEffectiveScale and Minimap:GetEffectiveScale()) or 1
    mx, my = mx / scale, my / scale

    local deg = math.deg(Atan2(my - cy, mx - cx))
    if deg < 0 then deg = deg + 360 end

    if ns.db then
        ns.db.minimapAngle = math.floor(deg + 0.5)
    end
    UpdatePosition()
end

function MinimapMod.Initialize()
    if button or not CreateFrame or not Minimap then
        MinimapMod.Refresh()
        return
    end

    button = CreateFrame("Button", "ForeverQuestPathMinimapButton", Minimap)
    button:SetSize(31, 31)
    button:SetFrameStrata("MEDIUM")
    button:SetFrameLevel(8)
    button:EnableMouse(true)
    button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    button:RegisterForDrag("LeftButton")
    if button.SetHighlightTexture then
        button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")
    end

    local overlay = button:CreateTexture(nil, "OVERLAY")
    overlay:SetSize(53, 53)
    overlay:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
    overlay:SetPoint("TOPLEFT", 0, 0)

    local bg = button:CreateTexture(nil, "BACKGROUND")
    bg:SetSize(20, 20)
    bg:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
    bg:SetPoint("TOPLEFT", 7, -5)

    local icon = button:CreateTexture(nil, "ARTWORK")
    icon:SetSize(18, 18)
    icon:SetTexture(ICON_TEXTURE)
    if icon.SetTexCoord then
        icon:SetTexCoord(0.04, 0.96, 0.04, 0.96)
    end
    icon:SetPoint("TOPLEFT", 7, -6)
    button.icon = icon

    button:SetScript("OnClick", function(_, mouseButton)
        HandleClickAction(mouseButton)
    end)

    button:SetScript("OnDragStart", function(self)
        self:SetScript("OnUpdate", OnDragUpdate)
    end)

    button:SetScript("OnDragStop", function(self)
        self:SetScript("OnUpdate", nil)
    end)

    button:SetScript("OnEnter", function(self)
        if not GameTooltip then return end
        GameTooltip:SetOwner(self, "ANCHOR_LEFT")
        PopulateTooltip(GameTooltip, false)
        GameTooltip:Show()
    end)

    button:SetScript("OnLeave", function()
        if GameTooltip then
            GameTooltip:Hide()
        end
    end)

    MinimapMod.Refresh()
end

function MinimapMod.Refresh()
    if not button then return end
    UpdatePosition()
    local shouldShow = not ns.db or ns.db.showMinimap ~= false
    SetWidgetVisible(button, shouldShow)
end

function MinimapMod.GetButton()
    return button
end

function _G.ForeverQuestPath_OnAddonCompartmentClick(_, buttonName)
    HandleClickAction(buttonName)
end

function _G.ForeverQuestPath_OnAddonCompartmentEnter(_, menuButtonFrame)
    if not GameTooltip or not menuButtonFrame then return end
    GameTooltip:SetOwner(menuButtonFrame, "ANCHOR_LEFT")
    PopulateTooltip(GameTooltip, true)
    GameTooltip:Show()
end

function _G.ForeverQuestPath_OnAddonCompartmentLeave()
    if GameTooltip then
        GameTooltip:Hide()
    end
end
