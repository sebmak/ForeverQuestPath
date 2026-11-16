-- Mock WoW Forever (Interface 16001, build 1.60.1) Lua 5.1 / LuaJIT test harness.
-- Provides faithful client globals, C_QuestLog, C_Map, C_SuperTrack, UiMapPoint,
-- WorldMapFrame.QuestLog, QuestMapFrame_OpenToQuestDetails, GameTooltip (IsOwned/
-- AddLine/AddDoubleLine), GetQuestLogQuestText, Frame/Widget APIs, SavedVariables
-- lifecycle, .toc loader, group/raid APIs (IsInGroup, IsInRaid, GetNumGroupMembers,
-- GetNumPartyMembers, GetNumRaidMembers, Mock.SetGroup), instance APIs
-- (IsInInstance, GetInstanceInfo, Mock.SetInstance), and TomTom/OpenMap mocks.

local M = {}

local function dirname(path)
    return path:match("^(.*)[/\\][^/\\]+$") or "."
end

local function basename(path)
    return path:match("([^/\\]+)$") or path
end

local function strip_ext(name)
    return name:gsub("%.toc$", "")
end

local function trim(s)
    return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function normalize_sep(p)
    return (p:gsub("\\", "/"))
end

-- Bit library compatibility (LuaJIT provides global `bit`)
if not _G.bit and package and package.preload and package.preload.bit then
    _G.bit = require("bit")
elseif not _G.bit then
    local ok, bitlib = pcall(require, "bit")
    if ok then _G.bit = bitlib end
end

-- Standard WoW Lua string & table helpers
_G.strsplit = function(delimiter, subject, pieces)
    local result = {}
    if not subject then return end
    if delimiter == "" then return subject end
    local pos = 1
    local count = 0
    while true do
        if pieces and count + 1 >= pieces then
            table.insert(result, subject:sub(pos))
            break
        end
        local s, e = subject:find(delimiter, pos, true)
        if not s then
            table.insert(result, subject:sub(pos))
            break
        end
        table.insert(result, subject:sub(pos, s - 1))
        pos = e + 1
        count = count + 1
    end
    return unpack(result)
end

_G.strtrim = function(s, chars)
    if not s then return "" end
    local pat = chars and ("[" .. chars .. "]") or "%s"
    return (s:gsub("^" .. pat .. "+", ""):gsub(pat .. "+$", ""))
end

_G.strjoin = function(delim, ...)
    return table.concat({ ... }, delim)
end

_G.format = string.format
_G.strmatch = string.match
_G.strfind = string.find
_G.strsub = string.sub
_G.strlen = string.len
_G.strlower = string.lower
_G.strupper = string.upper
_G.gmatch = string.gmatch
_G.gsub = string.gsub
_G.tinsert = table.insert
_G.tremove = table.remove
_G.sort = table.sort
_G.wipe = function(t)
    if type(t) ~= "table" then return {} end
    for k in pairs(t) do t[k] = nil end
    return t
end

_G.CopyTable = function(src)
    if type(src) ~= "table" then return src end
    local copy = {}
    for k, v in pairs(src) do
        copy[k] = _G.CopyTable(v)
    end
    return copy
end

_G.Mixin = function(object, ...)
    for i = 1, select("#", ...) do
        local mixin = select(i, ...)
        if type(mixin) == "table" then
            for k, v in pairs(mixin) do
                object[k] = v
            end
        end
    end
    return object
end

_G.CreateFromMixins = function(...)
    return _G.Mixin({}, ...)
end

local error_handler = function(err)
    table.insert(M.errors, tostring(err))
    return err
end
_G.geterrorhandler = function() return error_handler end
_G.seterrorhandler = function(fn) error_handler = fn end

-- Resettable internal state
M.state = {
    character = {
        name = "Testrogue",
        realm = "Forever",
        level = 24,
        xp = 12000,
        xpMax = 31700,
        raceName = "Gnome",
        raceFile = "Gnome",
        raceId = 7,
        className = "Rogue",
        classFile = "ROGUE",
        classId = 4,
        faction = "Alliance",
        money = 45000,
        mapId = 1437, -- Wetlands
        x = 0.118,
        y = 0.586,
        subZone = "Menethil Harbor",
        bindLocation = "Menethil Harbor",
        inCombat = false,
        onTaxi = false,
        isDead = false,
        isGhost = false,
        mounted = false,
        inGroup = false,
        isRaid = false,
        groupMembers = 0,
        inInstance = false,
        instanceType = "none",
        instanceName = nil,
        instanceMapId = nil,
        hearthCooldownStart = 0,
        hearthCooldownDuration = 0,
    },
    tickers = {},
    taxiNodes = {},
    completedQuests = {},
    questLog = {}, -- array of { questID, title, level, isComplete, isFailed, isHeader, objectives, tagName, description, objectivesText }
    selectedQuestID = nil,
    openedQuestDetailsID = nil,
    userWaypoint = nil,
    superTrackedUserWaypoint = false,
    superTrackedQuestID = 0,
    openedMapID = nil,
    tomtomWaypoints = {},
    openMapRegistrations = {},
    chatMessages = {},
    frames = {},
    skills = {},
    metrics = {
        createFrameCalls = 0,
        createRegionCalls = 0,
        isQuestFlaggedCompletedCalls = 0,
        getAllCompletedQuestIDsCalls = 0,
        getQuestObjectivesCalls = 0,
        getQuestsCompletedCalls = 0,
    },
    eventListeners = {},
    loadedAddons = {},
    addonMetadata = {},
    now = 1000.0,
    sounds = {},
}
M.errors = {}

function M.ResetMetrics()
    M.state.metrics = {
        createFrameCalls = 0,
        createRegionCalls = 0,
        isQuestFlaggedCompletedCalls = 0,
        getAllCompletedQuestIDsCalls = 0,
        getQuestObjectivesCalls = 0,
        getQuestsCompletedCalls = 0,
    }
end

function M.GetMetrics()
    return {
        createFrameCalls = M.state.metrics.createFrameCalls,
        createRegionCalls = M.state.metrics.createRegionCalls,
        isQuestFlaggedCompletedCalls = M.state.metrics.isQuestFlaggedCompletedCalls,
        getAllCompletedQuestIDsCalls = M.state.metrics.getAllCompletedQuestIDsCalls,
        getQuestObjectivesCalls = M.state.metrics.getQuestObjectivesCalls,
        getQuestsCompletedCalls = M.state.metrics.getQuestsCompletedCalls,
        totalFrames = #M.state.frames,
    }
end

-- Zone/Map tree matching WoW Forever 16001 uiMapIDs and areaIds
local MAP_NODES = {
    [946]  = { mapID = 946,  name = "Cosmic",              parentMapID = 0,    mapType = 0, pinnable = false },
    [947]  = { mapID = 947,  name = "Azeroth",             parentMapID = 946,  mapType = 1, pinnable = false },
    [1415] = { mapID = 1415, name = "Eastern Kingdoms",    parentMapID = 947,  mapType = 2, pinnable = false },
    [1414] = { mapID = 1414, name = "Kalimdor",            parentMapID = 947,  mapType = 2, pinnable = false },
    -- Eastern Kingdoms
    [1416] = { mapID = 1416, name = "Alterac Mountains",   parentMapID = 1415, mapType = 3, pinnable = true, areaId = 36 },
    [1417] = { mapID = 1417, name = "Arathi Highlands",    parentMapID = 1415, mapType = 3, pinnable = true, areaId = 45 },
    [1418] = { mapID = 1418, name = "Badlands",            parentMapID = 1415, mapType = 3, pinnable = true, areaId = 3 },
    [1419] = { mapID = 1419, name = "Blasted Lands",       parentMapID = 1415, mapType = 3, pinnable = true, areaId = 4 },
    [1420] = { mapID = 1420, name = "Tirisfal Glades",     parentMapID = 1415, mapType = 3, pinnable = true, areaId = 85 },
    [1421] = { mapID = 1421, name = "Silverpine Forest",   parentMapID = 1415, mapType = 3, pinnable = true, areaId = 130 },
    [1422] = { mapID = 1422, name = "Western Plaguelands", parentMapID = 1415, mapType = 3, pinnable = true, areaId = 28 },
    [1423] = { mapID = 1423, name = "Eastern Plaguelands", parentMapID = 1415, mapType = 3, pinnable = true, areaId = 139 },
    [1424] = { mapID = 1424, name = "Hillsbrad Foothills", parentMapID = 1415, mapType = 3, pinnable = true, areaId = 267 },
    [1425] = { mapID = 1425, name = "The Hinterlands",     parentMapID = 1415, mapType = 3, pinnable = true, areaId = 47 },
    [1426] = { mapID = 1426, name = "Dun Morogh",          parentMapID = 1415, mapType = 3, pinnable = true, areaId = 1 },
    [1427] = { mapID = 1427, name = "Searing Gorge",       parentMapID = 1415, mapType = 3, pinnable = true, areaId = 51 },
    [1428] = { mapID = 1428, name = "Burning Steppes",     parentMapID = 1415, mapType = 3, pinnable = true, areaId = 46 },
    [1429] = { mapID = 1429, name = "Elwynn Forest",       parentMapID = 1415, mapType = 3, pinnable = true, areaId = 12 },
    [1430] = { mapID = 1430, name = "Deadwind Pass",       parentMapID = 1415, mapType = 3, pinnable = true, areaId = 41 },
    [1431] = { mapID = 1431, name = "Duskwood",            parentMapID = 1415, mapType = 3, pinnable = true, areaId = 10 },
    [1432] = { mapID = 1432, name = "Loch Modan",          parentMapID = 1415, mapType = 3, pinnable = true, areaId = 38 },
    [1433] = { mapID = 1433, name = "Redridge Mountains",  parentMapID = 1415, mapType = 3, pinnable = true, areaId = 44 },
    [1434] = { mapID = 1434, name = "Stranglethorn Vale",  parentMapID = 1415, mapType = 3, pinnable = true, areaId = 33 },
    [1435] = { mapID = 1435, name = "Swamp of Sorrows",    parentMapID = 1415, mapType = 3, pinnable = true, areaId = 8 },
    [1436] = { mapID = 1436, name = "Westfall",            parentMapID = 1415, mapType = 3, pinnable = true, areaId = 40 },
    [1437] = { mapID = 1437, name = "Wetlands",            parentMapID = 1415, mapType = 3, pinnable = true, areaId = 11 },
    [1453] = { mapID = 1453, name = "Stormwind City",      parentMapID = 1415, mapType = 3, pinnable = true, areaId = 1519 },
    [1455] = { mapID = 1455, name = "Ironforge",           parentMapID = 1415, mapType = 3, pinnable = true, areaId = 1537 },
    [1458] = { mapID = 1458, name = "Undercity",           parentMapID = 1415, mapType = 3, pinnable = true, areaId = 1497 },
    [2548] = { mapID = 2548, name = "Riverglades",         parentMapID = 1415, mapType = 3, pinnable = true, areaId = 16591 },
    -- Kalimdor
    [1411] = { mapID = 1411, name = "Durotar",             parentMapID = 1414, mapType = 3, pinnable = true, areaId = 14 },
    [1412] = { mapID = 1412, name = "Mulgore",             parentMapID = 1414, mapType = 3, pinnable = true, areaId = 215 },
    [1413] = { mapID = 1413, name = "The Barrens",         parentMapID = 1414, mapType = 3, pinnable = true, areaId = 17 },
    [1438] = { mapID = 1438, name = "Teldrassil",          parentMapID = 1414, mapType = 3, pinnable = true, areaId = 141 },
    [1439] = { mapID = 1439, name = "Darkshore",           parentMapID = 1414, mapType = 3, pinnable = true, areaId = 148 },
    [1440] = { mapID = 1440, name = "Ashenvale",           parentMapID = 1414, mapType = 3, pinnable = true, areaId = 331 },
    [1441] = { mapID = 1441, name = "Thousand Needles",    parentMapID = 1414, mapType = 3, pinnable = true, areaId = 400 },
    [1442] = { mapID = 1442, name = "Stonetalon Mountains",parentMapID = 1414, mapType = 3, pinnable = true, areaId = 406 },
    [1443] = { mapID = 1443, name = "Desolace",            parentMapID = 1414, mapType = 3, pinnable = true, areaId = 405 },
    [1444] = { mapID = 1444, name = "Feralas",             parentMapID = 1414, mapType = 3, pinnable = true, areaId = 357 },
    [1445] = { mapID = 1445, name = "Dustwallow Marsh",    parentMapID = 1414, mapType = 3, pinnable = true, areaId = 15 },
    [1446] = { mapID = 1446, name = "Tanaris",             parentMapID = 1414, mapType = 3, pinnable = true, areaId = 440 },
    [1447] = { mapID = 1447, name = "Azshara",             parentMapID = 1414, mapType = 3, pinnable = true, areaId = 16 },
    [1448] = { mapID = 1448, name = "Felwood",             parentMapID = 1414, mapType = 3, pinnable = true, areaId = 361 },
    [1449] = { mapID = 1449, name = "Un'Goro Crater",      parentMapID = 1414, mapType = 3, pinnable = true, areaId = 490 },
    [1450] = { mapID = 1450, name = "Moonglade",           parentMapID = 1414, mapType = 3, pinnable = true, areaId = 493 },
    [1451] = { mapID = 1451, name = "Silithus",            parentMapID = 1414, mapType = 3, pinnable = true, areaId = 1377 },
    [1452] = { mapID = 1452, name = "Winterspring",        parentMapID = 1414, mapType = 3, pinnable = true, areaId = 618 },
    [1454] = { mapID = 1454, name = "Orgrimmar",           parentMapID = 1414, mapType = 3, pinnable = true, areaId = 1637 },
    [1456] = { mapID = 1456, name = "Thunder Bluff",       parentMapID = 1414, mapType = 3, pinnable = true, areaId = 1638 },
    [1457] = { mapID = 1457, name = "Darnassus",           parentMapID = 1414, mapType = 3, pinnable = true, areaId = 1657 },
    [2482] = { mapID = 2482, name = "Mount Hyjal",         parentMapID = 1414, mapType = 3, pinnable = true, areaId = 616 },
    [2521] = { mapID = 2521, name = "Zephras Isle",        parentMapID = 947,  mapType = 3, pinnable = true, areaId = 16593 },
    [2524] = { mapID = 2524, name = "Darkspear Islands",   parentMapID = 1414, mapType = 3, pinnable = true, areaId = 16606 },
    [2652] = { mapID = 2652, name = "Shen'dralas",         parentMapID = 1414, mapType = 3, pinnable = true, areaId = 16651 },
}

local AREA_TO_MAP = {}
for mid, node in pairs(MAP_NODES) do
    if node.areaId then
        AREA_TO_MAP[node.areaId] = mid
    end
end

local RACE_PRESETS = {
    human = { raceName = "Human", raceFile = "Human", raceId = 1, faction = "Alliance", mapId = 1429 },
    orc = { raceName = "Orc", raceFile = "Orc", raceId = 2, faction = "Horde", mapId = 1411 },
    dwarf = { raceName = "Dwarf", raceFile = "Dwarf", raceId = 3, faction = "Alliance", mapId = 1426 },
    nightelf = { raceName = "Night Elf", raceFile = "NightElf", raceId = 4, faction = "Alliance", mapId = 1438 },
    ["night elf"] = { raceName = "Night Elf", raceFile = "NightElf", raceId = 4, faction = "Alliance", mapId = 1438 },
    undead = { raceName = "Undead", raceFile = "Scourge", raceId = 5, faction = "Horde", mapId = 1420 },
    scourge = { raceName = "Undead", raceFile = "Scourge", raceId = 5, faction = "Horde", mapId = 1420 },
    tauren = { raceName = "Tauren", raceFile = "Tauren", raceId = 6, faction = "Horde", mapId = 1412 },
    gnome = { raceName = "Gnome", raceFile = "Gnome", raceId = 7, faction = "Alliance", mapId = 1426 },
    troll = { raceName = "Troll", raceFile = "Troll", raceId = 8, faction = "Horde", mapId = 1411 },
    goblin = { raceName = "Goblin", raceFile = "Goblin", raceId = 9, faction = "Horde", mapId = 1411 },
    skyborne = { raceName = "High Order Skyborne", raceFile = "HighOrderSkyborne", raceId = 95, faction = "Alliance", mapId = 2521 },
    highorderskyborne = { raceName = "High Order Skyborne", raceFile = "HighOrderSkyborne", raceId = 95, faction = "Alliance", mapId = 2521 },
    ["high order skyborne"] = { raceName = "High Order Skyborne", raceFile = "HighOrderSkyborne", raceId = 95, faction = "Alliance", mapId = 2521 },
    skyborne_alliance = { raceName = "High Order Skyborne", raceFile = "HighOrderSkyborne", raceId = 95, faction = "Alliance", mapId = 2521 },
    skyborne_horde = { raceName = "Windshaper Skyborne", raceFile = "WindshaperSkyborne", raceId = 96, faction = "Horde", mapId = 2521 },
    windshaperskyborne = { raceName = "Windshaper Skyborne", raceFile = "WindshaperSkyborne", raceId = 96, faction = "Horde", mapId = 2521 },
    ["windshaper skyborne"] = { raceName = "Windshaper Skyborne", raceFile = "Windshaper Skyborne", raceId = 96, faction = "Horde", mapId = 2521 },
}

local CLASS_PRESETS = {
    warrior = { className = "Warrior", classFile = "WARRIOR", classId = 1 },
    paladin = { className = "Paladin", classFile = "PALADIN", classId = 2 },
    hunter = { className = "Hunter", classFile = "HUNTER", classId = 3 },
    rogue = { className = "Rogue", classFile = "ROGUE", classId = 4 },
    priest = { className = "Priest", classFile = "PRIEST", classId = 5 },
    shaman = { className = "Shaman", classFile = "SHAMAN", classId = 7 },
    mage = { className = "Mage", classFile = "MAGE", classId = 8 },
    warlock = { className = "Warlock", classFile = "WARLOCK", classId = 9 },
    druid = { className = "Druid", classFile = "DRUID", classId = 11 },
}

local AREA_TO_NAME = {
    [1] = "Dun Morogh", [3] = "Badlands", [4] = "Blasted Lands", [8] = "Swamp of Sorrows",
    [10] = "Duskwood", [11] = "Wetlands", [12] = "Elwynn Forest", [14] = "Durotar",
    [15] = "Dustwallow Marsh", [16] = "Azshara", [17] = "The Barrens", [28] = "Western Plaguelands",
    [33] = "Stranglethorn Vale", [36] = "Alterac Mountains", [38] = "Loch Modan", [40] = "Westfall",
    [41] = "Deadwind Pass", [44] = "Redridge Mountains", [45] = "Arathi Highlands", [46] = "Burning Steppes",
    [47] = "The Hinterlands", [51] = "Searing Gorge", [85] = "Tirisfal Glades", [130] = "Silverpine Forest",
    [139] = "Eastern Plaguelands", [141] = "Teldrassil", [148] = "Darkshore", [209] = "Shadowfang Keep",
    [215] = "Mulgore", [267] = "Hillsbrad Foothills", [331] = "Ashenvale", [357] = "Feralas",
    [361] = "Felwood", [400] = "Thousand Needles", [405] = "Desolace", [406] = "Stonetalon Mountains",
    [440] = "Tanaris", [490] = "Un'Goro Crater", [491] = "Razorfen Kraul", [493] = "Moonglade",
    [618] = "Winterspring", [717] = "The Stockade", [718] = "Wailing Caverns", [719] = "Blackfathom Deeps",
    [721] = "Gnomeregan", [722] = "Razorfen Downs", [796] = "Scarlet Monastery", [1176] = "Zul'Farrak",
    [1337] = "Uldaman", [1377] = "Silithus", [1477] = "The Temple of Atal'Hakkar", [1497] = "Undercity",
    [1519] = "Stormwind City", [1537] = "Ironforge", [1581] = "The Deadmines", [1583] = "Blackrock Spire",
    [1584] = "Blackrock Depths", [1637] = "Orgrimmar", [1638] = "Thunder Bluff", [1657] = "Darnassus",
    [2017] = "Stratholme", [2057] = "Scholomance", [2100] = "Maraudon", [2437] = "Ragefire Chasm",
    [2557] = "Dire Maul", [16544] = "City of Dalaran", [16550] = "The Drowned City",
    [16591] = "Riverglades", [16593] = "Zephras Isle", [16606] = "Darkspear Islands",
    [16611] = "Ruins of Lordaeron", [16651] = "Shen'dralas",
    [16732] = "Excavation Site: Wetlands", [16842] = "Krol'dok Stronghold", [16919] = "The Hall of Thanes",
}

-- Frame & Widget factory
local function make_region(regionType, name, parent)
    local r = {
        _type = regionType,
        _name = name,
        _parent = parent,
        _shown = true,
        _alpha = 1,
        _points = {},
        _text = "",
        _color = { 1, 1, 1, 1 },
        _width = 100,
        _height = 20,
    }
    function r:GetObjectType() return self._type end
    function r:GetName() return self._name end
    function r:GetParent() return self._parent end
    function r:SetParent(p) self._parent = p end
    function r:Show() self._shown = true end
    function r:Hide() self._shown = false end
    function r:SetShown(v) self._shown = not not v end
    function r:IsShown() return self._shown end
    function r:IsVisible() return self._shown and (not self._parent or not self._parent.IsVisible or self._parent:IsVisible()) end
    function r:SetAlpha(a) self._alpha = a or 1 end
    function r:GetAlpha() return self._alpha end
    function r:ClearAllPoints() self._points = {} end
    function r:SetPoint(point, relativeTo, relativePoint, x, y)
        table.insert(self._points, { point, relativeTo, relativePoint, x or 0, y or 0 })
    end
    function r:SetAllPoints(target)
        self._points = { { "TOPLEFT", target or self._parent, "TOPLEFT", 0, 0 }, { "BOTTOMRIGHT", target or self._parent, "BOTTOMRIGHT", 0, 0 } }
    end
    function r:GetNumPoints() return #self._points end
    function r:GetPoint(i)
        local p = self._points[i or 1]
        if p then return unpack(p) end
    end
    function r:SetSize(w, h) self._width = w or 0; self._height = h or 0 end
    function r:SetWidth(w) self._width = w or 0 end
    function r:SetHeight(h) self._height = h or 0 end
    function r:GetWidth() return self._width end
    function r:GetHeight() return self._height end
    function r:GetSize() return self._width, self._height end
    function r:SetText(t) self._text = t == nil and "" or tostring(t) end
    function r:GetText() return self._text end
    function r:SetFormattedText(fmt, ...) self._text = string.format(fmt, ...) end
    function r:SetTextColor(r0, g0, b0, a0) self._color = { r0 or 1, g0 or 1, b0 or 1, a0 or 1 } end
    function r:GetTextColor() return unpack(self._color) end
    function r:SetFont() end
    function r:GetFont() return "Fonts\\FRIZQT__.TTF", 12, "" end
    function r:SetFontObject(fo) self._fontObject = fo end
    function r:GetFontObject() return self._fontObject end
    function r:SetJustifyH() end
    function r:SetJustifyV() end
    function r:SetWordWrap() end
    function r:SetNonSpaceWrap() end
    function r:SetMaxLines() end
    function r:GetStringWidth() return #self._text * 6 end
    function r:GetStringHeight() return 14 end
    function r:SetTexture(tex) self._texture = tex end
    function r:GetTexture() return self._texture end
    function r:SetColorTexture(r0, g0, b0, a0) self._colorTexture = { r0, g0, b0, a0 or 1 } end
    function r:SetTexCoord(...) self._texCoord = { ... } end
    function r:SetVertexColor(r0, g0, b0, a0) self._vertexColor = { r0, g0, b0, a0 or 1 } end
    function r:SetBlendMode() end
    function r:SetDrawLayer() end
    function r:SetRotation() end
    function r:SetMask() end
    if name then _G[name] = r end
    return r
end

_G.BackdropTemplateMixin = {
    OnBackdropLoaded = function() end,
    SetBackdrop = function(self, bd) self._backdrop = bd end,
    GetBackdrop = function(self) return self._backdrop end,
    SetBackdropColor = function(self, r, g, b, a) self._backdropColor = { r, g, b, a } end,
    SetBackdropBorderColor = function(self, r, g, b, a) self._backdropBorderColor = { r, g, b, a } end,
}
_G.TooltipBackdropTemplateMixin = _G.BackdropTemplateMixin

local function create_frame(frameType, name, parent, template)
    M.state.metrics.createFrameCalls = M.state.metrics.createFrameCalls + 1
    local f = make_region(frameType or "Frame", name, parent)
    f._width = 300
    f._height = 200
    f._scale = 1
    f._effectiveScale = 1
    f._strata = "MEDIUM"
    f._frameLevel = 1
    f._scripts = {}
    f._events = {}
    f._allEvents = false
    f._children = {}
    f._regions = {}
    f._enabled = true
    f._checked = false
    f._minVal = 0
    f._maxVal = 100
    f._val = 0
    f._valStep = 1
    f._vscroll = 0
    f._hscroll = 0
    f._id = 0

    if template and tostring(template):find("Backdrop") then
        _G.Mixin(f, _G.BackdropTemplateMixin)
    else
        -- WoW Forever/Classic frames often expose SetBackdrop if BackdropTemplate or inherited;
        -- keep SetBackdrop only when BackdropTemplate is requested or mixed in, to catch missing template bugs!
        -- Wait: also check if template is a comma-separated list including BackdropTemplate.
    end

    if parent and parent._children then
        table.insert(parent._children, f)
    end

    function f:SetScale(s) self._scale = s or 1 end
    function f:GetScale() return self._scale end
    function f:GetEffectiveScale() return self._effectiveScale * self._scale end
    function f:SetFrameStrata(s) self._strata = s end
    function f:GetFrameStrata() return self._strata end
    function f:SetFrameLevel(l) self._frameLevel = l end
    function f:GetFrameLevel() return self._frameLevel end
    function f:Raise() end
    function f:Lower() end
    function f:SetToplevel() end
    function f:SetMovable(v) self._movable = not not v end
    function f:IsMovable() return self._movable end
    function f:SetResizable(v) self._resizable = not not v end
    function f:SetResizeBounds(mw, mh, xw, xh) self._minSize = { mw, mh }; self._maxSize = { xw, xh } end
    function f:SetMinResize(mw, mh) self._minSize = { mw, mh } end
    function f:SetMaxResize(xw, xh) self._maxSize = { xw, xh } end
    function f:SetClampedToScreen(v) self._clamped = not not v end
    function f:EnableMouse(v) self._mouse = not not v end
    function f:EnableMouseWheel(v) self._mouseWheel = not not v end
    function f:RegisterForDrag(...) self._dragButtons = { ... } end
    function f:RegisterForClicks(...) self._clickButtons = { ... } end
    function f:StartMoving() self._moving = true end
    function f:StopMovingOrSizing() self._moving = false end
    function f:GetCenter() return 400, 300 end
    function f:GetLeft() return 250 end
    function f:GetRight() return 250 + self._width end
    function f:GetTop() return 400 end
    function f:GetBottom() return 400 - self._height end
    function f:IsMouseOver() return false end
    function f:SetID(id) self._id = id end
    function f:GetID() return self._id end
    function f:GetChildren() return unpack(self._children) end
    function f:GetRegions() return unpack(self._regions) end

    function f:CreateTexture(texName, layer, inherits)
        M.state.metrics.createRegionCalls = M.state.metrics.createRegionCalls + 1
        local t = make_region("Texture", texName, self)
        t._layer = layer or "ARTWORK"
        table.insert(self._regions, t)
        return t
    end
    function f:CreateFontString(fsName, layer, inherits)
        M.state.metrics.createRegionCalls = M.state.metrics.createRegionCalls + 1
        local fs = make_region("FontString", fsName, self)
        fs._layer = layer or "OVERLAY"
        fs._inherits = inherits
        table.insert(self._regions, fs)
        return fs
    end

    function f:SetScript(handler, fn)
        self._scripts[handler] = fn
    end
    function f:GetScript(handler)
        return self._scripts[handler]
    end
    function f:HasScript(handler)
        return true
    end
    function f:HookScript(handler, fn)
        local prev = self._scripts[handler]
        if prev then
            self._scripts[handler] = function(...)
                prev(...)
                return fn(...)
            end
        else
            self._scripts[handler] = fn
        end
    end

    function f:RegisterEvent(ev)
        self._events[ev] = true
    end
    function f:UnregisterEvent(ev)
        self._events[ev] = nil
    end
    function f:UnregisterAllEvents()
        self._events = {}
        self._allEvents = false
    end
    function f:RegisterAllEvents()
        self._allEvents = true
    end
    function f:IsEventRegistered(ev)
        return self._allEvents or self._events[ev] == true
    end

    -- Button / CheckButton / Slider / EditBox / ScrollFrame / StatusBar methods
    function f:Enable() self._enabled = true end
    function f:Disable() self._enabled = false end
    function f:SetEnabled(v) self._enabled = not not v end
    function f:IsEnabled() return self._enabled end
    function f:Click(button, down)
        if not self._enabled then return end
        if self._type == "CheckButton" then
            self._checked = not self._checked
        end
        if self._scripts.OnClick then
            self._scripts.OnClick(self, button or "LeftButton", down)
        end
    end
    function f:SetNormalTexture(tex) self._normalTex = tex end
    function f:GetNormalTexture() return self._normalTex end
    function f:SetPushedTexture(tex) self._pushedTex = tex end
    function f:SetHighlightTexture(tex) self._highlightTex = tex end
    function f:SetDisabledTexture(tex) self._disabledTex = tex end
    function f:SetCheckedTexture(tex) self._checkedTex = tex end
    function f:SetThumbTexture(tex) self._thumbTex = tex end
    function f:SetNormalFontObject(fo) self._normalFont = fo end
    function f:SetHighlightFontObject(fo) self._hlFont = fo end
    function f:SetDisabledFontObject(fo) self._disFont = fo end
    function f:GetFontString()
        if not self._fs then
            self._fs = self:CreateFontString(nil, "OVERLAY", "GameFontNormal")
        end
        return self._fs
    end
    function f:SetText(t)
        self._text = t == nil and "" or tostring(t)
        if self._fs then self._fs:SetText(self._text) end
    end
    function f:GetText() return self._text end
    function f:LockHighlight() self._lockedHighlight = true end
    function f:UnlockHighlight() self._lockedHighlight = false end
    function f:SetChecked(v) self._checked = not not v end
    function f:GetChecked() return self._checked end
    function f:SetMinMaxValues(mn, mx) self._minVal = mn; self._maxVal = mx end
    function f:GetMinMaxValues() return self._minVal, self._maxVal end
    function f:SetValue(v)
        self._val = v
        if self._scripts.OnValueChanged then
            self._scripts.OnValueChanged(self, v, true)
        end
    end
    function f:GetValue() return self._val end
    function f:SetValueStep(s) self._valStep = s end
    function f:GetValueStep() return self._valStep end
    function f:SetObeyStepOnDrag(v) self._obeyStep = not not v end
    function f:SetStepsPerPage(n) self._stepsPerPage = n end
    function f:SetOrientation(o) self._orientation = o end
    function f:SetStatusBarTexture(tex) self._sbTex = tex end
    function f:SetStatusBarColor(...) self._sbColor = { ... } end
    function f:SetNumber(n) self._text = tostring(n or 0) end
    function f:GetNumber() return tonumber(self._text) or 0 end
    function f:SetMultiLine(v) self._multiLine = not not v end
    function f:SetAutoFocus(v) self._autoFocus = not not v end
    function f:HighlightText() end
    function f:SetCursorPosition() end
    function f:SetFocus() self._focused = true end
    function f:ClearFocus() self._focused = false end
    function f:SetMaxLetters(n) self._maxLetters = n end
    function f:SetScrollChild(child) self._scrollChild = child end
    function f:GetScrollChild() return self._scrollChild end
    function f:SetVerticalScroll(v) self._vscroll = v or 0 end
    function f:GetVerticalScroll() return self._vscroll end
    function f:SetHorizontalScroll(v) self._hscroll = v or 0 end
    function f:GetHorizontalScroll() return self._hscroll end
    function f:GetVerticalScrollRange()
        if not self._scrollChild then return 0 end
        return math.max(0, (self._scrollChild._height or 0) - self._height)
    end
    function f:UpdateScrollChildRect() end
    function f:SetClipsChildren(v) self._clips = not not v end
    function f:SetAttribute(k, v) self._attrs = self._attrs or {}; self._attrs[k] = v end
    function f:GetAttribute(k) return self._attrs and self._attrs[k] end

    table.insert(M.state.frames, f)
    return f
end

function M.Reset(opts)
    opts = opts or {}
    M.errors = {}
    M.state.character = {
        name = "Testrogue",
        realm = "Forever",
        level = 24,
        xp = 12000,
        xpMax = 31700,
        raceName = "Gnome",
        raceFile = "Gnome",
        raceId = 7,
        className = "Rogue",
        classFile = "ROGUE",
        classId = 4,
        faction = "Alliance",
        money = 45000,
        mapId = 1437, -- Wetlands
        x = 0.118,
        y = 0.586,
        subZone = "Menethil Harbor",
        bindLocation = "Menethil Harbor",
        inCombat = false,
        onTaxi = false,
        isDead = false,
        isGhost = false,
        mounted = false,
        inGroup = false,
        isRaid = false,
        groupMembers = 0,
        inInstance = false,
        instanceType = "none",
        instanceName = nil,
        instanceMapId = nil,
        hearthCooldownStart = 0,
        hearthCooldownDuration = 0,
    }
    if opts.character then
        M.SetCharacter(opts.character)
    end
    M.state.tickers = {}
    M.state.taxiNodes = {}
    M.state.completedQuests = {}
    M.state.questLog = {}
    M.state.selectedQuestID = nil
    M.state.openedQuestDetailsID = nil
    M.state.userWaypoint = nil
    M.state.superTrackedUserWaypoint = false
    M.state.superTrackedQuestID = 0
    M.state.openedMapID = nil
    M.state.tomtomWaypoints = {}
    M.state.openMapRegistrations = {}
    M.state.chatMessages = {}
    M.state.frames = {}
    M.state.skills = opts.skills or {
        { name = "Professions", isHeader = true, isExpanded = true },
        { name = "Leatherworking", rank = 165, maxRank = 225, isAbandonable = true },
        { name = "Skinning", rank = 185, maxRank = 225, isAbandonable = true },
    }
    M.ResetMetrics()
    M.state.loadedAddons = {}
    M.state.addonMetadata = {}
    M.state.sounds = {}
    M.state.now = 1000.0

    -- Core Blizzard Globals
    _G.CreateFrame = create_frame
    _G.UIParent = create_frame("Frame", "UIParent", nil)
    _G.UIParent:SetSize(1920, 1080)
    _G.Minimap = create_frame("Frame", "Minimap", _G.UIParent)
    _G.Minimap:SetSize(140, 140)
    _G.WorldMapFrame = create_frame("Frame", "WorldMapFrame", _G.UIParent)
    _G.WorldMapFrame:Hide()
    _G.WorldMapFrame.ScrollContainer = create_frame("Frame", nil, _G.WorldMapFrame)
    _G.WorldMapFrame.QuestLog = create_frame("Frame", nil, _G.WorldMapFrame)
    _G.WorldMapFrame.QuestLog:Hide()
    function _G.WorldMapFrame:HandleUserActionOpenSidePanel()
        if self.QuestLog then self.QuestLog:Show() end
    end
    function _G.WorldMapFrame:SetMapID(id) M.state.openedMapID = id end
    function _G.WorldMapFrame:GetMapID() return M.state.openedMapID or M.state.character.mapId end
    _G.QuestMapFrame_ShowQuestDetails = function(questID)
        M.state.openedQuestDetailsID = questID
        M.state.selectedQuestID = questID
        if _G.WorldMapFrame and _G.WorldMapFrame.QuestLog then
            _G.WorldMapFrame.QuestLog:Show()
        end
    end
    _G.QuestMapFrame_OpenToQuestDetails = function(questID)
        if _G.WorldMapFrame then _G.WorldMapFrame:Show() end
        _G.QuestMapFrame_ShowQuestDetails(questID)
    end
    _G.CharacterFrame = create_frame("Frame", "CharacterFrame", _G.UIParent)
    _G.UISpecialFrames = {}

    local function make_tooltip(name)
        local tt = create_frame("GameTooltip", name, _G.UIParent, "BackdropTemplate")
        tt._lines = {}
        tt._shown = false
        function tt:SetOwner(owner, anchor) self._owner = owner; self._anchor = anchor; self._lines = {}; self._shown = true end
        function tt:IsOwned(owner) return self._shown and self._owner == owner end
        function tt:Hide() self._shown = false; self._owner = nil end
        function tt:ClearLines() self._lines = {} end
        function tt:AddLine(text, r, g, b, wrap) table.insert(self._lines, { text = text, r = r, g = g, b = b, wrap = wrap }) end
        function tt:AddDoubleLine(l, r, ...) table.insert(self._lines, { left = l, right = r }) end
        function tt:SetHyperlink(link) self._hyperlink = link end
        function tt:SetInventoryItem(unit, slot) end
        function tt:NumLines() return #self._lines end
        return tt
    end
    _G.GameTooltip = make_tooltip("GameTooltip")

    _G.DEFAULT_CHAT_FRAME = {
        AddMessage = function(_, msg)
            table.insert(M.state.chatMessages, tostring(msg))
        end
    }

    local function make_font(name)
        local fo = { _name = name }
        function fo:SetFont(...) end
        function fo:GetFont() return "Fonts\\FRIZQT__.TTF", 12, "" end
        function fo:SetTextColor(...) end
        function fo:CopyFontObject(...) end
        _G[name] = fo
        return fo
    end
    for _, fname in ipairs({
        "GameFontNormal", "GameFontNormalSmall", "GameFontNormalLarge", "GameFontNormalHuge",
        "GameFontHighlight", "GameFontHighlightSmall", "GameFontHighlightLarge",
        "GameFontDisable", "GameFontDisableSmall", "GameFontWhite", "ChatFontNormal",
        "NumberFontNormalSmall",
    }) do
        make_font(fname)
    end
    _G.ChatFrame1 = _G.DEFAULT_CHAT_FRAME
    _G.CreateFont = make_font

    _G.GetBuildInfo = function()
        return "1.60.1", "69913", "Nov 12 2026", 16001
    end
    _G.GetLocale = function() return "enUS" end
    _G.GetTime = function() return M.state.now end
    _G.GetCursorPosition = function() return 500, 500 end
    _G.GetMinimapShape = function() return "ROUND" end
    _G.UnitName = function(unit)
        if unit == "player" then return M.state.character.name, M.state.character.realm end
        return "Unknown"
    end
    _G.GetRealmName = function() return M.state.character.realm end
    _G.UnitLevel = function(unit) return M.state.character.level end
    _G.UnitXP = function(unit) return M.state.character.xp end
    _G.UnitXPMax = function(unit) return M.state.character.xpMax end
    _G.UnitRace = function(unit)
        local c = M.state.character
        return c.raceName, c.raceFile, c.raceId
    end
    _G.UnitClass = function(unit)
        local c = M.state.character
        return c.className, c.classFile, c.classId
    end
    _G.UnitFactionGroup = function(unit)
        return M.state.character.faction, M.state.character.faction
    end
    _G.GetMoney = function() return M.state.character.money end
    _G.GetRealZoneText = function()
        local c = M.state.character
        if c.inInstance == true and c.instanceName and c.instanceName ~= "" then
            return c.instanceName
        end
        local node = MAP_NODES[c.mapId]
        return node and node.name or "Wetlands"
    end
    _G.GetZoneText = _G.GetRealZoneText
    _G.GetSubZoneText = function() return M.state.character.subZone or "" end
    _G.GetBindLocation = function() return M.state.character.bindLocation or "Menethil Harbor" end
    _G.UnitClassBase = function(unit) return M.state.character.classFile end
    _G.BreakUpLargeNumbers = function(num)
        local s = tostring(math.floor(tonumber(num) or 0))
        local formatted = s:reverse():gsub("(%d%d%d)", "%1,"):reverse():gsub("^,", "")
        return formatted
    end
    _G.GetCoinTextureString = function(copper)
        copper = tonumber(copper) or 0
        local g = math.floor(copper / 10000)
        local s = math.floor((copper % 10000) / 100)
        local c = copper % 100
        return string.format("%dg %ds %dc", g, s, c)
    end
    _G.InCombatLockdown = function() return M.state.character.inCombat end
    _G.UnitOnTaxi = function(unit)
        if unit == nil or unit == "player" then
            return M.state.character.onTaxi == true
        end
        return false
    end
    _G.UnitIsDead = function(unit)
        if unit == nil or unit == "player" then
            return M.state.character.isDead == true
        end
        return false
    end
    _G.UnitIsGhost = function(unit)
        if unit == nil or unit == "player" then
            return M.state.character.isGhost == true
        end
        return false
    end
    _G.UnitIsDeadOrGhost = function(unit)
        if unit == nil or unit == "player" then
            return (M.state.character.isDead == true) or (M.state.character.isGhost == true)
        end
        return false
    end
    _G.IsMounted = function()
        return M.state.character.mounted == true
    end
    _G.IsInGroup = function()
        return M.state.character.inGroup == true
    end
    _G.IsInRaid = function()
        return M.state.character.isRaid == true
    end
    _G.IsInInstance = function()
        local c = M.state.character
        return c.inInstance == true, c.instanceType or (c.inInstance and "party" or "none")
    end
    _G.GetInstanceInfo = function()
        local c = M.state.character
        local name = c.instanceName or (_G.GetRealZoneText and _G.GetRealZoneText()) or "Unknown"
        local iType = (c.inInstance and (c.instanceType or "party")) or "none"
        return name, iType, (iType == "raid" and 9 or 1), "Normal", (iType == "raid" and 40 or 5), 0, false, tonumber(c.instanceMapId) or 0
    end
    _G.GetNumGroupMembers = function()
        return M.state.character.groupMembers or (M.state.character.inGroup and 2 or 0)
    end
    _G.GetNumPartyMembers = function()
        if M.state.character.isRaid then return 0 end
        local n = M.state.character.groupMembers or (M.state.character.inGroup and 2 or 0)
        return (n > 1) and (n - 1) or 0
    end
    _G.GetNumRaidMembers = function()
        if not M.state.character.isRaid then return 0 end
        return M.state.character.groupMembers or (M.state.character.inGroup and 10 or 0)
    end
    _G.GetItemCooldown = function(itemId)
        if tonumber(itemId) == 6948 then
            return M.state.character.hearthCooldownStart or 0, M.state.character.hearthCooldownDuration or 0, 1
        end
        return 0, 0, 1
    end
    _G.C_Container = {
        GetItemCooldown = _G.GetItemCooldown,
    }
    _G.NumTaxiNodes = function()
        return #(M.state.taxiNodes or {})
    end
    _G.TaxiNodeName = function(index)
        local n = M.state.taxiNodes and M.state.taxiNodes[index]
        if type(n) == "table" then return n.name end
        return n
    end
    _G.TaxiNodeGetType = function(index)
        local n = M.state.taxiNodes and M.state.taxiNodes[index]
        if type(n) == "table" and n.nodeType then return n.nodeType end
        return n and "REACHABLE" or "NONE"
    end
    _G.GetNumSkillLines = function()
        return #(M.state.skills or {})
    end
    _G.GetSkillLineInfo = function(index)
        local s = M.state.skills and M.state.skills[index]
        if not s then return nil end
        return s.name, s.isHeader == true, s.isExpanded ~= false,
            s.rank or 0, s.tempPoints or 0, s.modifier or 0,
            s.maxRank or 0, s.isAbandonable == true, nil, nil, nil, nil, s.description or ""
    end
    _G.ExpandSkillHeader = function(index)
        local s = M.state.skills and M.state.skills[index]
        if s and s.isHeader then s.isExpanded = true end
    end
    _G.IsShiftKeyDown = function() return false end
    _G.IsControlKeyDown = function() return false end
    _G.IsAltKeyDown = function() return false end
    _G.IsModifiedClick = function() return false end
    _G.GetCVarBool = function() return false end
    _G.PlaySound = function(id) table.insert(M.state.sounds, id) end
    _G.SOUNDKIT = {
        IG_MAINMENU_OPEN = 850,
        IG_MAINMENU_CLOSE = 851,
        IG_MAINMENU_OPTION_CHECKBOX_ON = 856,
        IG_QUEST_LIST_SELECT = 875,
        IG_ABILITY_PAGE_TURN = 829,
    }
    _G.ITEM_QUALITY_COLORS = {
        [0] = { r = 0.62, g = 0.62, b = 0.62, hex = "|cff9d9d9d" },
        [1] = { r = 1.00, g = 1.00, b = 1.00, hex = "|cffffffff" },
        [2] = { r = 0.12, g = 1.00, b = 0.00, hex = "|cff1eff00" },
        [3] = { r = 0.00, g = 0.44, b = 0.87, hex = "|cff0070dd" },
        [4] = { r = 0.64, g = 0.21, b = 0.93, hex = "|cffa335ee" },
    }
    _G.RAID_CLASS_COLORS = {
        ROGUE = { r = 1.0, g = 0.96, b = 0.41, colorStr = "fffff569" },
        WARRIOR = { r = 0.78, g = 0.61, b = 0.43, colorStr = "ffc79c6e" },
        MAGE = { r = 0.25, g = 0.78, b = 0.92, colorStr = "ff3fc7eb" },
        PRIEST = { r = 1.0, g = 1.0, b = 1.0, colorStr = "ffffffff" },
        WARLOCK = { r = 0.53, g = 0.53, b = 0.93, colorStr = "ff8788ee" },
        HUNTER = { r = 0.67, g = 0.83, b = 0.45, colorStr = "ffaad372" },
        PALADIN = { r = 0.96, g = 0.55, b = 0.73, colorStr = "fff58cba" },
        SHAMAN = { r = 0.0, g = 0.44, b = 0.87, colorStr = "ff0070dd" },
        DRUID = { r = 1.0, g = 0.49, b = 0.04, colorStr = "ffff7d0a" },
    }

    _G.SlashCmdList = {}
    _G.hash_SlashCmdList = {}
    _G.StaticPopupDialogs = {}
    _G.StaticPopup_Show = function(which) return which end
    _G.hooksecurefunc = function(tbl, name, hook)
        if type(tbl) == "string" then
            hook = name
            name = tbl
            tbl = _G
        end
        local orig = tbl[name]
        if type(orig) == "function" then
            tbl[name] = function(...)
                local res = { orig(...) }
                hook(...)
                return unpack(res)
            end
        end
    end

    _G.C_Timer = {
        After = function(delay, cb)
            if type(cb) == "function" then cb() end
        end,
        NewTimer = function(delay, cb)
            return {
                _cancelled = false,
                Cancel = function(self) self._cancelled = true end,
                IsCancelled = function(self) return self._cancelled == true end,
            }
        end,
        NewTicker = function(interval, cb, iterations)
            local iv = tonumber(interval) or 1.0
            if iv <= 0 then iv = 1.0 end
            local h = { _cancelled = false }
            function h:Cancel() self._cancelled = true end
            function h:IsCancelled() return self._cancelled == true end
            table.insert(M.state.tickers, {
                interval = iv,
                cb = cb,
                remaining = iterations,
                nextRun = M.state.now + iv,
                handle = h,
            })
            return h
        end,
    }

    _G.C_Item = {
        GetItemInfo = function(id) return nil end,
        GetItemQualityByID = function(id) return nil end,
        GetItemIconByID = function(id) return 134400 end,
        RequestLoadItemDataByID = function(id) end,
    }
    _G.GetItemInfo = _G.C_Item.GetItemInfo

    -- C_QuestLog + Legacy Quest APIs (WoW Forever 16001)
    _G.C_QuestLog = {
        IsQuestFlaggedCompleted = function(qid)
            M.state.metrics.isQuestFlaggedCompletedCalls = M.state.metrics.isQuestFlaggedCompletedCalls + 1
            return M.state.completedQuests[qid] == true
        end,
        GetAllCompletedQuestIDs = function()
            M.state.metrics.getAllCompletedQuestIDsCalls = M.state.metrics.getAllCompletedQuestIDsCalls + 1
            local out = {}
            for qid, done in pairs(M.state.completedQuests) do
                if done then table.insert(out, qid) end
            end
            return out
        end,
        GetNumQuestLogEntries = function()
            local total = #M.state.questLog
            local quests = 0
            for _, e in ipairs(M.state.questLog) do
                if not e.isHeader then quests = quests + 1 end
            end
            return total, quests
        end,
        GetInfo = function(index)
            return M.state.questLog[index]
        end,
        GetLogIndexForQuestID = function(qid)
            for idx, e in ipairs(M.state.questLog) do
                if e.questID == qid then return idx end
            end
            return nil
        end,
        IsOnQuest = function(qid)
            for _, e in ipairs(M.state.questLog) do
                if e.questID == qid then return true end
            end
            return false
        end,
        IsComplete = function(qid)
            for _, e in ipairs(M.state.questLog) do
                if e.questID == qid then return e.isComplete == true end
            end
            return false
        end,
        ReadyForTurnIn = function(qid)
            for _, e in ipairs(M.state.questLog) do
                if e.questID == qid then return e.isComplete == true end
            end
            return false
        end,
        IsFailed = function(qid)
            for _, e in ipairs(M.state.questLog) do
                if e.questID == qid then return e.isFailed == true end
            end
            return false
        end,
        GetQuestObjectives = function(qid)
            M.state.metrics.getQuestObjectivesCalls = M.state.metrics.getQuestObjectivesCalls + 1
            for _, e in ipairs(M.state.questLog) do
                if e.questID == qid then return e.objectives or {} end
            end
            return {}
        end,
        GetNumQuestObjectives = function(qid)
            for _, e in ipairs(M.state.questLog) do
                if e.questID == qid then return #(e.objectives or {}) end
            end
            return 0
        end,
        GetQuestTagInfo = function(qid)
            for _, e in ipairs(M.state.questLog) do
                if e.questID == qid and e.tagName then
                    return { tagName = e.tagName }
                end
            end
            return nil
        end,
        SetSelectedQuest = function(qid)
            M.state.selectedQuestID = qid
        end,
        GetSelectedQuest = function()
            return M.state.selectedQuestID
        end,
        GetTitleForQuestID = function(qid)
            for _, e in ipairs(M.state.questLog) do
                if e.questID == qid then return e.title end
            end
            return nil
        end,
        RequestLoadQuestByID = function(qid) end,
        GetTrivialRange = function() return 5 end,
        GetMaxNumQuestsCanAccept = function() return 20 end,
    }
    _G.IsQuestFlaggedCompleted = _G.C_QuestLog.IsQuestFlaggedCompleted
    _G.GetQuestsCompleted = function(tbl)
        M.state.metrics.getQuestsCompletedCalls = M.state.metrics.getQuestsCompletedCalls + 1
        local out = (type(tbl) == "table") and tbl or {}
        for qid, done in pairs(M.state.completedQuests) do
            if done then out[qid] = true end
        end
        return out
    end
    _G.GetNumQuestLogEntries = _G.C_QuestLog.GetNumQuestLogEntries
    _G.GetQuestLogTitle = function(index)
        local info = M.state.questLog[index]
        if not info then return nil end
        return info.title, info.level, info.tagName, info.isHeader, info.isCollapsed,
               info.isComplete and 1 or nil, info.frequency, info.questID
    end
    _G.GetQuestLogQuestText = function(logIndex)
        local idx = logIndex
        if not idx and M.state.selectedQuestID then
            idx = _G.C_QuestLog.GetLogIndexForQuestID(M.state.selectedQuestID)
        end
        local e = idx and M.state.questLog[idx]
        if e then
            return e.description or "", e.objectivesText or ""
        end
        return "", ""
    end
    _G.GetQuestGreenRange = function() return 5 end

    -- C_Map + UiMapPoint + C_SuperTrack + OpenWorldMap
    _G.UiMapPoint = {
        CreateFromCoordinates = function(mapID, x, y, z)
            return { uiMapID = mapID, position = { x = x, y = y, z = z, GetXY = function(self) return self.x, self.y end } }
        end
    }
    _G.C_Map = {
        GetBestMapForUnit = function(unit)
            return M.state.character.mapId
        end,
        GetPlayerMapPosition = function(mapID, unit)
            if not mapID or mapID ~= M.state.character.mapId then return nil end
            local px, py = M.state.character.x, M.state.character.y
            if px == nil or py == nil then return nil end
            return { x = px, y = py, GetXY = function() return px, py end }
        end,
        GetFallbackWorldMapID = function() return 947 end,
        GetMapInfo = function(mapID)
            return MAP_NODES[mapID]
        end,
        GetMapChildrenInfo = function(mapID, mapType, allDescendants)
            local out = {}
            local function collect(pid)
                for _, node in pairs(MAP_NODES) do
                    if node.parentMapID == pid then
                        if not mapType or node.mapType == mapType then
                            table.insert(out, node)
                        end
                        if allDescendants then
                            collect(node.mapID)
                        end
                    end
                end
            end
            collect(mapID)
            table.sort(out, function(a, b) return a.mapID < b.mapID end)
            return out
        end,
        CanSetUserWaypointOnMap = function(mapID)
            local node = MAP_NODES[mapID]
            return node and node.pinnable or false
        end,
        SetUserWaypoint = function(point)
            M.state.userWaypoint = point
        end,
        HasUserWaypoint = function()
            return M.state.userWaypoint ~= nil
        end,
        GetUserWaypoint = function()
            return M.state.userWaypoint
        end,
        ClearUserWaypoint = function()
            M.state.userWaypoint = nil
        end,
        GetAreaInfo = function(areaId)
            return AREA_TO_NAME[areaId]
        end,
        OpenWorldMap = function(mapID)
            M.state.openedMapID = mapID
        end,
    }
    _G.OpenWorldMap = function(mapID)
        M.state.openedMapID = mapID
    end
    _G.C_SuperTrack = {
        SetSuperTrackedUserWaypoint = function(v)
            M.state.superTrackedUserWaypoint = not not v
        end,
        IsSuperTrackedUserWaypoint = function()
            return M.state.superTrackedUserWaypoint
        end,
        SetSuperTrackedQuestID = function(qid)
            M.state.superTrackedQuestID = qid or 0
        end,
        GetSuperTrackedQuestID = function()
            return M.state.superTrackedQuestID
        end,
    }

    _G.C_AddOns = {
        IsAddOnLoaded = function(name)
            return M.state.loadedAddons[name] == true, M.state.loadedAddons[name] == true
        end,
        GetAddOnMetadata = function(name, field)
            local m = M.state.addonMetadata[name]
            return m and m[field] or nil
        end,
        GetAddOnInfo = function(name)
            local loaded = M.state.loadedAddons[name] == true
            return name, name, "", loaded, loaded and "" or "MISSING", "INSECURE", false
        end,
    }
    _G.IsAddOnLoaded = function(name) return M.state.loadedAddons[name] == true end
    _G.GetAddOnMetadata = _G.C_AddOns.GetAddOnMetadata
    _G.GetAddOnInfo = _G.C_AddOns.GetAddOnInfo

    _G.AddonCompartmentFrame = {
        RegisterAddon = function(_, info)
            M.state.compartmentRegistered = info
        end
    }

    -- Optional Addons: enabled by default unless opts.tomtom==false / opts.openmap==false
    if opts.tomtom ~= false then
        M.state.loadedAddons["TomTom"] = true
        _G.TomTom = {
            AddWaypoint = function(self, mapId, x, y, wopts)
                local wp = { mapId = mapId, x = x, y = y, opts = wopts }
                table.insert(M.state.tomtomWaypoints, wp)
                return wp
            end,
            RemoveWaypoint = function(self, uid)
                for i = #M.state.tomtomWaypoints, 1, -1 do
                    if M.state.tomtomWaypoints[i] == uid then
                        table.remove(M.state.tomtomWaypoints, i)
                    end
                end
            end,
        }
    else
        M.state.loadedAddons["TomTom"] = nil
        _G.TomTom = nil
    end

    if opts.openmap ~= false then
        M.state.loadedAddons["OpenMap"] = true
        _G.OpenMapAPI = {
            major = 1,
            version = 4,
            IsStitched = function() return true end,
            WorldFromMap = function(uiMapID, x, y) return x, y end,
            MapFromWorld = function(uiMapID, u, v) return u, v end,
            ShowWorld = function(u, v, zoom)
                table.insert(M.state.openMapRegistrations, { kind = "ShowWorld", u = u, v = v, zoom = zoom })
                return true
            end,
            RegisterMenuToggle = function(owner, spec)
                table.insert(M.state.openMapRegistrations, { kind = "MenuToggle", owner = owner, spec = spec })
            end,
            RegisterDungeonSection = function(owner, fn)
                table.insert(M.state.openMapRegistrations, { kind = "DungeonSection", owner = owner, fn = fn })
            end,
        }
    else
        M.state.loadedAddons["OpenMap"] = nil
        _G.OpenMapAPI = nil
    end
end

function M.ParseTOC(tocPath)
    local f, err = io.open(tocPath, "r")
    assert(f, "Failed to open TOC: " .. tostring(tocPath) .. " (" .. tostring(err) .. ")")
    local meta = {}
    local files = {}
    for line in f:lines() do
        line = line:gsub("\r$", "")
        local t = trim(line)
        if t:sub(1, 2) == "##" then
            local k, v = t:match("^##%s*([^:]+):%s*(.*)$")
            if k then meta[trim(k)] = trim(v) end
        elseif t ~= "" and t:sub(1, 1) ~= "#" then
            table.insert(files, normalize_sep(t))
        end
    end
    f:close()
    return meta, files
end

function M.LoadAddonTOC(tocPath, opts)
    opts = opts or {}
    local meta, files = M.ParseTOC(tocPath)
    local addonDir = dirname(tocPath)
    local addonName = opts.addonName or strip_ext(basename(tocPath))
    local ns = {}

    M.state.addonMetadata[addonName] = meta
    M.state.loadedAddons[addonName] = true

    for _, relPath in ipairs(files) do
        if relPath:lower():match("%.lua$") then
            local fullPath = addonDir .. "/" .. relPath
            local chunk, lerr = loadfile(fullPath)
            assert(chunk, "Lua syntax/load error in " .. fullPath .. ": " .. tostring(lerr))
            local ok, rerr = xpcall(function() return chunk(addonName, ns) end, debug.traceback)
            assert(ok, "Lua runtime error loading " .. fullPath .. ":\n" .. tostring(rerr))
        end
    end

    if meta.SavedVariables then
        for sv in meta.SavedVariables:gmatch("[^,%s]+") do
            if _G[sv] == nil and opts.savedVars and opts.savedVars[sv] then
                _G[sv] = opts.savedVars[sv]
            end
        end
    end
    if meta.SavedVariablesPerCharacter then
        for sv in meta.SavedVariablesPerCharacter:gmatch("[^,%s]+") do
            if _G[sv] == nil and opts.savedVarsPerChar and opts.savedVarsPerChar[sv] then
                _G[sv] = opts.savedVarsPerChar[sv]
            end
        end
    end

    return addonName, ns, meta, files
end

function M.FireEvent(event, ...)
    for _, frame in ipairs(M.state.frames) do
        if frame:IsEventRegistered(event) then
            local fn = frame:GetScript("OnEvent")
            if fn then
                local ok, err = xpcall(fn, debug.traceback, frame, event, ...)
                assert(ok, "Event handler error on " .. tostring(event) .. ":\n" .. tostring(err))
            end
        end
    end
end

local function normalize_key(s)
    return tostring(s or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
end

function M.SetCharacter(raceOrFields, classArg, factionArg, levelArg, zoneIdArg, fireEvents)
    local c = M.state.character
    if type(raceOrFields) == "table" then
        for k, v in pairs(raceOrFields) do
            c[k] = v
        end
        if raceOrFields.zoneId and not raceOrFields.mapId then
            c.mapId = AREA_TO_MAP[raceOrFields.zoneId] or raceOrFields.zoneId
        end
        if raceOrFields.raceName and not raceOrFields.raceId then
            local rp = RACE_PRESETS[normalize_key(raceOrFields.raceName)]
            if rp then
                c.raceId = rp.raceId
                c.raceFile = c.raceFile or rp.raceFile
            end
        end
        if raceOrFields.className and not raceOrFields.classId then
            local cp = CLASS_PRESETS[normalize_key(raceOrFields.className)]
            if cp then
                c.classId = cp.classId
                c.classFile = c.classFile or cp.classFile
            end
        end
    else
        if raceOrFields ~= nil then
            local rkey = normalize_key(raceOrFields)
            local rp = RACE_PRESETS[rkey]
            if rkey == "skyborne" and factionArg and normalize_key(factionArg) == "horde" then
                rp = RACE_PRESETS.skyborne_horde
            end
            if rp then
                c.raceName = rp.raceName
                c.raceFile = rp.raceFile
                c.raceId = rp.raceId
                c.faction = rp.faction
                c.mapId = rp.mapId
            else
                c.raceName = tostring(raceOrFields)
                c.raceFile = tostring(raceOrFields):gsub("%s+", "")
            end
        end
        if classArg ~= nil then
            local cp = CLASS_PRESETS[normalize_key(classArg)]
            if cp then
                c.className = cp.className
                c.classFile = cp.classFile
                c.classId = cp.classId
            else
                c.className = tostring(classArg)
                c.classFile = tostring(classArg):upper():gsub("%s+", "")
            end
        end
        if factionArg ~= nil then
            c.faction = factionArg
        end
        if levelArg ~= nil then
            c.level = tonumber(levelArg) or c.level
            c.xp = 0
            c.xpMax = math.floor(400 * c.level * (1 + c.level * 0.08))
        end
        if zoneIdArg ~= nil then
            local zid = tonumber(zoneIdArg)
            if zid then
                c.mapId = AREA_TO_MAP[zid] or (MAP_NODES[zid] and zid) or c.mapId
            end
        end
    end
    local node = MAP_NODES[c.mapId]
    if node then
        c.subZone = node.name
        c.bindLocation = node.name
    end
    if fireEvents then
        M.FireEvent("PLAYER_LEVEL_UP", c.level)
        M.FireEvent("ZONE_CHANGED_NEW_AREA")
    end
end

function M.SetSkills(skillsList, fireEvent)
    M.state.skills = skillsList or {}
    if fireEvent ~= false then
        M.FireEvent("SKILL_LINES_CHANGED")
    end
end

function M.CompleteQuest(qid)
    M.state.completedQuests[qid] = true
    for i = #M.state.questLog, 1, -1 do
        if M.state.questLog[i].questID == qid then
            table.remove(M.state.questLog, i)
        end
    end
end

function M.AcceptQuest(qid, title, level, isComplete, tagName)
    for _, e in ipairs(M.state.questLog) do
        if e.questID == qid then
            e.isComplete = not not isComplete
            for _, obj in ipairs(e.objectives or {}) do
                obj.finished = not not isComplete
            end
            M.FireEvent("QUEST_LOG_UPDATE")
            return
        end
    end
    table.insert(M.state.questLog, {
        questID = qid,
        title = title or ("Quest " .. tostring(qid)),
        level = level or M.state.character.level,
        isComplete = not not isComplete,
        isFailed = false,
        isHeader = false,
        tagName = tagName,
        objectives = { { text = "Objective 1", type = "monster", finished = not not isComplete } },
    })
    M.FireEvent("QUEST_ACCEPTED", qid)
    M.FireEvent("QUEST_LOG_UPDATE")
end

function M.SetQuestObjectivesComplete(qid, complete)
    for _, e in ipairs(M.state.questLog) do
        if e.questID == qid then
            e.isComplete = complete ~= false
            for _, obj in ipairs(e.objectives or {}) do
                obj.finished = complete ~= false
            end
            M.FireEvent("QUEST_LOG_UPDATE")
            return true
        end
    end
    return false
end

function M.SetQuestObjectiveProgress(qid, objIndex, numFulfilled, numRequired, finished)
    for _, e in ipairs(M.state.questLog) do
        if e.questID == qid then
            e.objectives = e.objectives or {}
            local idx = tonumber(objIndex) or 1
            local req = tonumber(numRequired) or 10
            local ful = tonumber(numFulfilled) or 0
            local fin = (finished ~= nil) and (finished == true) or (ful >= req)
            local obj = e.objectives[idx]
            if not obj then
                obj = { type = "item" }
                e.objectives[idx] = obj
            end
            obj.numFulfilled = ful
            obj.numRequired = req
            obj.finished = fin
            obj.text = string.format("Objective %d: %d/%d", idx, ful, req)
            local allDone = true
            for _, o in ipairs(e.objectives) do
                if not o.finished then
                    allDone = false
                    break
                end
            end
            e.isComplete = allDone
            M.FireEvent("QUEST_LOG_UPDATE")
            return true
        end
    end
    return false
end

function M.SetQuestLogText(qid, description, objectivesText)
    for _, e in ipairs(M.state.questLog) do
        if e.questID == qid then
            e.description = description
            e.objectivesText = objectivesText
            return true
        end
    end
    return false
end

function M.TurnInQuest(qid, xpReward, moneyReward)
    M.CompleteQuest(qid)
    if xpReward and xpReward > 0 then
        M.state.character.xp = M.state.character.xp + xpReward
    end
    if moneyReward and moneyReward > 0 then
        M.state.character.money = M.state.character.money + moneyReward
    end
    M.FireEvent("QUEST_TURNED_IN", qid, xpReward or 0, moneyReward or 0)
    M.FireEvent("QUEST_LOG_UPDATE")
end

function M.LevelUp(newLevel)
    M.state.character.level = newLevel
    M.state.character.xp = 0
    M.FireEvent("PLAYER_LEVEL_UP", newLevel)
end

function M.ChangeZone(mapId, x, y)
    M.state.character.mapId = mapId
    if x then M.state.character.x = (x > 1) and (x / 100.0) or x end
    if y then M.state.character.y = (y > 1) and (y / 100.0) or y end
    M.FireEvent("ZONE_CHANGED_NEW_AREA")
end

function M.SetPlayerPosition(uiMapId, x, y, subZone, fireEvent)
    if uiMapId then
        M.state.character.mapId = (MAP_NODES[uiMapId] and uiMapId) or AREA_TO_MAP[uiMapId] or uiMapId
    end
    if x then M.state.character.x = (x > 1) and (x / 100.0) or x end
    if y then M.state.character.y = (y > 1) and (y / 100.0) or y end
    if subZone ~= nil then
        M.state.character.subZone = subZone
    end
    if fireEvent == true then
        M.FireEvent("ZONE_CHANGED_NEW_AREA")
    end
end

function M.SetGroup(inGroup, memberCount, isRaid, fireEvent)
    local grouped = (inGroup == true)
    local raid = grouped and (isRaid == true) or false
    local count = grouped and (tonumber(memberCount) or (raid and 10 or 2)) or 0
    M.state.character.inGroup = grouped
    M.state.character.isRaid = raid
    M.state.character.groupMembers = count
    if fireEvent ~= false then
        M.FireEvent("GROUP_ROSTER_UPDATE")
    end
end

function M.SetInstance(inInstance, instanceMapId, instanceName, instanceType, fireEvents)
    local c = M.state.character
    local inInst = (inInstance == true)
    c.inInstance = inInst
    c.instanceMapId = inInst and tonumber(instanceMapId) or nil
    c.instanceName = inInst and instanceName or nil
    c.instanceType = inInst and (instanceType or "party") or "none"
    if inInst then
        c.mapId = nil
        c.x = nil
        c.y = nil
    end
    if fireEvents ~= false then
        M.FireEvent("PLAYER_ENTERING_WORLD", false, false)
        M.FireEvent("ZONE_CHANGED_NEW_AREA")
    end
end

function M.SetOnTaxi(onTaxi)
    M.state.character.onTaxi = (onTaxi == true)
end

function M.SetDead(isDead, isGhost, fireEvent)
    M.state.character.isDead = (isDead == true)
    M.state.character.isGhost = (isGhost == true)
    if type(fireEvent) == "string" then
        M.FireEvent(fireEvent)
    elseif fireEvent == true then
        if M.state.character.isDead then
            M.FireEvent("PLAYER_DEAD")
        elseif M.state.character.isGhost then
            M.FireEvent("PLAYER_ALIVE")
        else
            M.FireEvent("PLAYER_UNGHOST")
            M.FireEvent("PLAYER_ALIVE")
        end
    end
end

function M.ReleaseSpirit(mapId, x, y, subZone)
    M.state.character.isDead = false
    M.state.character.isGhost = true
    if mapId ~= nil or x ~= nil or y ~= nil or subZone ~= nil then
        M.SetPlayerPosition(mapId, x, y, subZone, false)
    end
    M.FireEvent("PLAYER_ALIVE")
    M.FireEvent("ZONE_CHANGED")
end

function M.Resurrect(mapId, x, y, subZone)
    M.state.character.isDead = false
    M.state.character.isGhost = false
    if mapId ~= nil or x ~= nil or y ~= nil or subZone ~= nil then
        M.SetPlayerPosition(mapId, x, y, subZone, false)
    end
    M.FireEvent("PLAYER_UNGHOST")
    M.FireEvent("PLAYER_ALIVE")
end

function M.SetMounted(mounted)
    M.state.character.mounted = (mounted == true)
end

function M.SetBindLocation(bindLocation)
    M.state.character.bindLocation = bindLocation
end

function M.SetHearthCooldown(startSec, durationSec)
    M.state.character.hearthCooldownStart = tonumber(startSec) or M.state.now or 0
    M.state.character.hearthCooldownDuration = tonumber(durationSec) or 0
end

function M.OpenTaxiMap(nodesList)
    M.state.taxiNodes = {}
    for _, item in ipairs(nodesList or {}) do
        table.insert(M.state.taxiNodes, item)
    end
    M.FireEvent("TAXIMAP_OPENED")
end

function M.AdvanceTimers(seconds)
    local dt = tonumber(seconds)
    if dt == nil then dt = 2.0 end
    local target = M.state.now + math.max(0, dt)
    M.state.now = target
    local fired = 0
    for _, t in ipairs(M.state.tickers or {}) do
        while (not t.handle._cancelled) and t.nextRun <= target + 1e-9 do
            t.nextRun = t.nextRun + t.interval
            if type(t.cb) == "function" then
                local ok, err = xpcall(t.cb, debug.traceback, t.handle)
                assert(ok, "C_Timer.NewTicker callback error:\n" .. tostring(err))
                fired = fired + 1
            end
            if t.remaining then
                t.remaining = t.remaining - 1
                if t.remaining <= 0 then
                    t.handle._cancelled = true
                    break
                end
            end
        end
    end
    return fired
end

function M.RunSlash(cmd, msg)
    local targetCmd = cmd:lower()
    for key, fn in pairs(_G.SlashCmdList) do
        local i = 1
        while true do
            local alias = _G["SLASH_" .. key .. i]
            if not alias then break end
            if alias:lower() == targetCmd then
                fn(msg or "")
                return true
            end
            i = i + 1
        end
    end
    return false
end

function M.ClickAddonCompartment(addonName, buttonName)
    local meta = M.state.addonMetadata[addonName]
    local fnName = meta and meta.AddonCompartmentFunc
    if fnName and type(_G[fnName]) == "function" then
        _G[fnName](addonName, buttonName or "LeftButton")
        return true
    end
    return false
end

function M.FindFrameByName(name)
    if _G[name] then return _G[name] end
    for _, f in ipairs(M.state.frames) do
        if f._name == name then return f end
    end
    return nil
end

_G.__MockWoW = M
return M
