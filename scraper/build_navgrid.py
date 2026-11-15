#!/usr/bin/env python3
"""Build verified 50-zone condensed multimodal NavGraph + 20x20 spatial grids.

Inputs (local addons + existing verified data/):
  - data/quests.json, data/dungeons.json, data/travel_graph.json
  - scraper/build_dataset.py (LAND_ADJACENCIES)
  - scraper/vendor/QuestieDB (via cbor_reader.py) + scraper/cache/npc_creature_types.json
  - WoW Forever OpenMap addon data & textures:
    FlightData.lua, Places.lua, Gaps.lua, CaveMinimap.lua, Caves.lua,
    MapZoneLand/*.tga, MapTerritory/*.tga, MapCityMask/*.tga, MapMiniLOD/*.tga

Outputs:
  - data/nav_graph.json matching locked schema (#104/#106/#107).
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import re
import struct
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cbor_reader import QuestieDBReader
from lua_parser import parse_lua_table_at
import build_dataset

ADDONS_DIR = Path("/Applications/World of Warcraft/_forever_/Interface/AddOns")
OPENMAP_DATA_DIR = ADDONS_DIR / "OpenMap/Data/Forever"
TEX_BASE_DIR = ADDONS_DIR / "OpenMap/Media/Textures/Forever"
QUESTIEDB_DIR = SCRIPT_DIR / "vendor/QuestieDB"

UNMOUNTED_YDS_PER_SEC = 7.5
MOUNTED_YDS_PER_SEC = 11.4
ADT_YARDS = 1600.0 / 3.0
GRID_FINE = 80
GRID_COARSE = 20
MAX_NODES = 1500
MAX_EDGES = 6000

WORLD_MAP_DATA = {
    0: (44688.53, 29795.12, 32601.03, 9894.93),
    1: (44878.66, 29916.10, 8723.96, 14824.53),
}

AQUATIC_KEYWORDS = (
    "frenzy",
    "thresher",
    "shark",
    "turtle",
    "crab",
    "crawler",
    "makrura",
    "eel",
    "hydra",
    "snapjaw",
    "crocolisk",
    "naga",
    "siren",
    "sea ",
    "deep ",
    "reef",
    "murloc tide",
)

AIRBORNE_KEYWORDS = (
    "wyvern",
    "wind serpent",
    "eagle",
    "hawk",
    "owl",
    "condor",
    "vulture",
    "bat",
    "buzzard",
    "roc",
    "hippogryph",
    "griffon",
    "dragon",
    "drake",
    "whelp",
    "wisp",
    "chimaera",
)

AIRBORNE_AQUATIC_FAMILIES = {
    "wind serpent",
    "bat",
    "carrion bird",
    "owl",
    "turtle",
    "crab",
    "crocolisk",
    "nether ray",
}

SPECIAL_BORDER_HINTS = {
    frozenset((331, 406)): (1, 1731.5, -657.5, 5.0, "Talondeep Path tunnel"),
    frozenset((361, 493)): (1, 7096.0, -2162.5, 6.0, "Timbermaw Hold tunnel (Felwood <-> Moonglade)"),
    frozenset((361, 618)): (1, 7096.0, -2162.5, 6.0, "Timbermaw Hold tunnel (Felwood <-> Winterspring)"),
    frozenset((45, 11)): (0, -2420.0, -2465.0, 6.0, "Thandol Span bridge"),
    frozenset((11, 38)): (0, -4510.0, -2640.0, 8.0, "Dun Algaz tunnels"),
    frozenset((51, 46)): (0, -7550.0, -1250.0, 10.0, "Blackrock Mountain corridor"),
    frozenset((17, 400)): (1, -4468.0, -1633.0, 8.0, "The Great Lift"),
    frozenset((141, 1657)): (1, 9936.0, 2493.0, 6.0, "Teldrassil <-> Darnassus gate"),
}


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def get_zone_entries(container: Any, zone_id: int) -> list:
    if not container:
        return []
    if isinstance(container, dict):
        return container.get(zone_id) or []
    if isinstance(container, list) and 1 <= zone_id <= len(container):
        return container[zone_id - 1] or []
    return []


def zone_to_world(bounds: tuple[int, float, float, float, float], zx: float, zy: float) -> tuple[int, float, float]:
    cont, left_wy, right_wy, top_wx, bottom_wx = bounds
    wy = left_wy - (left_wy - right_wy) * (zx / 100.0)
    wx = top_wx - (top_wx - bottom_wx) * (zy / 100.0)
    return cont, wx, wy


def world_to_zone(bounds: tuple[int, float, float, float, float], wx: float, wy: float) -> tuple[float, float]:
    cont, left_wy, right_wy, top_wx, bottom_wx = bounds
    zx = (left_wy - wy) / (left_wy - right_wy) * 100.0
    zy = (top_wx - wx) / (top_wx - bottom_wx) * 100.0
    return zx, zy


class ZoneCalibrator:
    """Calibrates and validates exact (zoneId, zx, zy) <-> (cont, wx, wy) transforms.

    Rules:
    - Always calibrate from verified OpenMap geometry (Gaps.lua, FlightData.lua, Places.lua,
      CaveMinimap.lua) while maintaining WoW's exact 1.5:1 zone width-to-height aspect ratio.
    - Store calibrated bounds keyed by integer zoneId and expose coordinate helpers plus
      cave bounding boxes without mutating addon source files.
    """

    zones: dict[int, dict[str, Any]]
    uimap_by_zone: dict[int, int]
    fd_nodes: dict[int, list[Any]]
    fd_paths: list[list[Any]]
    transports: list[list[Any]]
    cave_boxes: list[tuple[int, float, float, float, float]]
    bounds_by_zone: dict[int, tuple[int, float, float, float, float]]

    def __init__(self, zones: dict[int, dict[str, Any]]) -> None:
        self.zones = zones
        self.uimap_by_zone = {}
        self.fd_nodes = {}
        self.fd_paths = []
        self.transports = []
        self.cave_boxes = []
        self.bounds_by_zone = {}
        self._load_sources()
        self._calibrate_all()

    def _load_sources(self) -> None:
        a2u_q = parse_lua_table_at(
            (ADDONS_DIR / "QuestieDB/support/Forever/Zones/areaIdToUiMapId.lua").read_text(),
            r"(?:=|return)\s*",
        )
        for zid, zinfo in self.zones.items():
            self.uimap_by_zone[zid] = int(zinfo.get("uiMapId") or a2u_q[zid])

        fd_tbl = parse_lua_table_at((OPENMAP_DATA_DIR / "FlightData.lua").read_text(), r"ns\.MapFlights\s*=")
        self.fd_nodes = {int(k): v for k, v in fd_tbl["nodes"].items()}
        self.fd_paths = list(fd_tbl["paths"])

        places_txt = (OPENMAP_DATA_DIR / "Places.lua").read_text()
        self.transports = parse_lua_table_at(places_txt, r"transports\s*=")

        cm_tbl = parse_lua_table_at((OPENMAP_DATA_DIR / "CaveMinimap.lua").read_text(), r"ns\.MapCaveMinimap\s*=")
        for box in cm_tbl:
            self.cave_boxes.append((int(box[0]), float(box[1]), float(box[2]), float(box[3]), float(box[4])))
        caves_tbl = parse_lua_table_at((OPENMAP_DATA_DIR / "Caves.lua").read_text(), r"ns\.MapCaves\s*=")
        for box in caves_tbl.values():
            self.cave_boxes.append((int(box[1]), float(box[2]), float(box[3]), float(box[4]), float(box[5])))

    @staticmethod
    def _bounds_from_1pt(cont: int, zx: float, zy: float, wx: float, wy: float, height: float) -> tuple[int, float, float, float, float]:
        width = 1.5 * height
        left_wy = wy + width * (zx / 100.0)
        top_wx = wx + height * (zy / 100.0)
        return (cont, left_wy, left_wy - width, top_wx, top_wx - height)

    @staticmethod
    def _fit_15(cont: int, pts: list[tuple[str, float, float, float, float]]) -> tuple[int, float, float, float, float]:
        n = len(pts)
        us = [p[1] / 100.0 for p in pts]
        vs = [p[2] / 100.0 for p in pts]
        wxs = [p[3] for p in pts]
        wys = [p[4] for p in pts]
        u_bar, v_bar = sum(us) / n, sum(vs) / n
        wx_bar, wy_bar = sum(wxs) / n, sum(wys) / n
        num = sum(-1.5 * (u - u_bar) * (wy - wy_bar) - (v - v_bar) * (wx - wx_bar) for u, v, wx, wy in zip(us, vs, wxs, wys))
        den = sum((1.5 * (u - u_bar)) ** 2 + (v - v_bar) ** 2 for u, v in zip(us, vs))
        height = num / den
        width = 1.5 * height
        left_wy = wy_bar + width * u_bar
        top_wx = wx_bar + height * v_bar
        return (cont, left_wy, left_wy - width, top_wx, top_wx - height)

    def _calibrate_all(self) -> None:
        by_norm = {norm_name(v["name"]): zid for zid, v in self.zones.items()}
        gaps_art = parse_lua_table_at((OPENMAP_DATA_DIR / "Gaps.lua").read_text(), r"\bart\s*=")
        for entry in gaps_art:
            zid = by_norm.get(norm_name(entry[0]))
            if zid is not None:
                cont = 1 if entry[1] == 1414 else 0
                w_c, h_c, left_c, top_c = WORLD_MAP_DATA[cont]
                l, r, t, b = entry[2]
                self.bounds_by_zone[zid] = (cont, left_c - l * w_c, left_c - r * w_c, top_c - t * h_c, top_c - b * h_c)

        fd = self.fd_nodes
        self.bounds_by_zone[46] = self._fit_15(0, [("FD70", 65.69, 24.22, *fd[70][2:4]), ("FD71", 84.33, 68.33, *fd[71][2:4])])
        self.bounds_by_zone[40] = self._bounds_from_1pt(0, 56.55, 52.64, *fd[4][2:4], 2333.33)
        self.bounds_by_zone[33] = self._fit_15(0, [("FD19", 27.53, 77.79, *fd[19][2:4]), ("FD20", 32.54, 29.35, *fd[20][2:4]), ("ZG", 53.9, 17.6, -11916.1, -1224.6)])
        self.bounds_by_zone[405] = self._fit_15(1, [("FD37", 64.66, 10.52, *fd[37][2:4]), ("FD38", 21.6, 74.12, *fd[38][2:4]), ("Mara", 29.0, 62.5, -1432.7, 2925.0)])
        self.bounds_by_zone[618] = self._fit_15(1, [("FD52", 62.34, 36.6, *fd[52][2:4]), ("Runner", 27.6, 40.9, 6604.2, -3040.2)])
        self.bounds_by_zone[493] = self._fit_15(1, [("FD62", 44.28, 45.87, *fd[62][2:4]), ("FD49", 48.10, 67.34, *fd[49][2:4]), ("FD69", 32.09, 66.61, *fd[69][2:4])])
        self.bounds_by_zone[130] = self._fit_15(0, [("FD10", 45.62, 42.6, *fd[10][2:4]), ("SFK", 44.8, 67.8, -231.0, 1568.5)])
        self.bounds_by_zone[267] = self._fit_15(0, [("FD14", 49.34, 52.27, *fd[14][2:4]), ("FD13", 60.14, 18.62, *fd[13][2:4])])
        self.bounds_by_zone[28] = self._fit_15(0, [("FD66", 42.93, 85.07, *fd[66][2:4]), ("Scholo", 69.0, 72.9, 1274.8, -2552.6)])
        self.bounds_by_zone[47] = self._fit_15(0, [("FD43", 11.07, 46.15, *fd[43][2:4]), ("FD76", 81.7, 81.76, *fd[76][2:4])])
        self.bounds_by_zone[15] = self._fit_15(1, [("FD32", 67.47, 51.29, *fd[32][2:4]), ("FD55", 35.57, 31.85, *fd[55][2:4])])
        self.bounds_by_zone[11] = self._fit_15(0, [("FD7", 9.49, 59.69, *fd[7][2:4]), ("Thandol", 51.3, 8.1, -2420.0, -2465.0)])
        self.bounds_by_zone[139] = self._fit_15(0, [
            ("FD68", 70.53, 47.55, *fd[68][2:4]),
            ("FD67", 71.81, 49.60, *fd[67][2:4]),
            ("FD84", 18.16, 25.06, *fd[84][2:4]),
            ("FD85", 49.25, 18.20, *fd[85][2:4]),
            ("FD86", 59.04, 39.41, *fd[86][2:4]),
            ("FD87", 34.09, 64.10, *fd[87][2:4]),
        ])
        self.bounds_by_zone[1637] = self._fit_15(1, [("FD23", 45.1, 63.9, *fd[23][2:4]), ("RFC", 52.8, 49.1, 1816.8, -4423.4)])
        self.bounds_by_zone[16591] = self._fit_15(0, [
            ("FD3276", 58.45, 79.78, *fd[3276][2:4]),
            ("Powderfuse", 79.45, 52.71, -8232.3, -5800.8),
            ("Toka", 57.27, 44.21, *fd[3203][2:4]),
        ])
        self.bounds_by_zone[16593] = self._fit_15(2991, [("DalP", 65.7, 83.3, 1848.3, 569.1), ("TBP", 58.0, 80.5, 1952.5, 1019.1)])
        self.bounds_by_zone[141] = self._bounds_from_1pt(1, 58.40, 94.02, *fd[27][2:4], 3400.0)
        self.bounds_by_zone[148] = self._bounds_from_1pt(1, 36.34, 45.58, *fd[26][2:4], 4367.0)
        self.bounds_by_zone[10] = self._fit_15(0, [("FD12", 77.52, 44.33, *fd[12][2:4]), ("Crypt104", 19.0, 31.0, -10286.5, 291.5), ("Ogre109", 36.0, 81.0, -11176.0, -147.5)])
        self.bounds_by_zone[4] = self._fit_15(0, [("FD45", 65.56, 24.42, *fd[45][2:4]), ("Mine105", 64.0, 7.0, -10750.5, -3415.0), ("Garrison111", 65.1, 32.0, -11244.5, -3506.0)])
        self.bounds_by_zone[8] = self._bounds_from_1pt(0, 46.08, 54.83, *fd[56][2:4], 1530.0)
        self.bounds_by_zone[44] = self._fit_15(0, [("FD5", 30.60, 59.42, *fd[5][2:4]), ("Rethban91", 23.8, 17.5, -8807.5, -2077.0), ("Render93", 80.5, 20.5, -8846.0, -3341.5)])
        self.bounds_by_zone[41] = self._fit_15(0, [("Cellar", 45.0, 77.0, -11055.0, -1900.0), ("Ogre108", 65.0, 66.0, -10894.0, -2342.5)])
        self.bounds_by_zone[85] = self._fit_15(0, [("SM", 84.3, 32.0, 2892.2, -811.3), ("ZepLanding", 61.6, 61.61, 2062.4, 293.0)])
        self.bounds_by_zone[12] = self._fit_15(0, [("Fargodeep", 40.0, 82.0, -9796.5, 198.0), ("Jasperlode", 61.5, 50.0, -9149.0, -514.0), ("EchoRidge", 49.0, 27.0, -8686.5, -145.5)])
        self.bounds_by_zone[1519] = self._fit_15(0, [("FD2", 71.29, 72.23, *fd[2][2:4]), ("Stockade", 51.49, 69.38, -8762.4, 848.0), ("Harbor23", 24.5, 57.0, -8654.5, 1344.4)])
        self.bounds_by_zone[1537] = self._bounds_from_1pt(0, 55.50, 47.74, *fd[6][2:4], 640.0)
        self.bounds_by_zone[1497] = self._bounds_from_1pt(0, 63.25, 48.56, *fd[11][2:4], 800.0)
        self.bounds_by_zone[1638] = self._bounds_from_1pt(1, 47.0, 49.83, *fd[22][2:4], 680.0)
        self.bounds_by_zone[1657] = self._bounds_from_1pt(1, 50.0, 58.0, 9910.0, 2510.0, 700.0)
        self.bounds_by_zone[215] = self._fit_15(1, [("Airship", 33.83, 21.92, -802.0, 366.5), ("TB_edge", 41.2, 28.0, -1197.2, 29.7), ("Palemane", 34.5, 63.0, -2380.0, 405.0), ("VentureMine", 61.8, 46.8, -1683.0, -1188.5)])
        self.bounds_by_zone[36] = self._fit_15(0, [("DAL", 8.5, 59.3, 544.8, 424.2), ("Mug", 35.68, 54.25, 623.0, -681.5)])
        self.bounds_by_zone[616] = (1, -1100.0, -5300.0, 6700.0, 3900.0)
        self.bounds_by_zone[16651] = (1, 2100.0, 150.0, -2350.0, -3650.0)

        missing = [z for z in self.zones if z not in self.bounds_by_zone]
        if missing:
            raise ValueError(f"Missing calibrated bounds for zones: {missing}")

    def world_dist_yards(self, za: int, xa: float, ya: float, zb: int, xb: float, yb: float) -> float | None:
        ba, bb = self.bounds_by_zone[za], self.bounds_by_zone[zb]
        if ba[0] != bb[0]:
            return None
        _, wxa, wya = zone_to_world(ba, xa, ya)
        _, wxb, wyb = zone_to_world(bb, xb, yb)
        return math.hypot(wxa - wxb, wya - wyb)


class TextureTerrainSampler:
    """Loads and samples OpenMap uncompressed TGA landmass masks and MapMiniLOD tiles.

    Rules:
    - Uses stdlib struct parsing only (no PIL/numpy).
    - Respects bottom-left vs top-left TGA origin bit (descriptor bit 5) and BGRA byte order.
    - Never guesses continent sea boundaries; relies strictly on disk TGA assets.
    """

    tex_base: Path
    lod_cache: dict[tuple[int, int, int], tuple[int, int, int, bool, bytes] | None]
    mask_cache: dict[tuple[str, int], tuple[int, int, int, bool, bytes] | None]

    def __init__(self, tex_base: Path = TEX_BASE_DIR) -> None:
        self.tex_base = tex_base
        self.lod_cache = {}
        self.mask_cache = {}

    @staticmethod
    def _load_tga(path: Path) -> tuple[int, int, int, bool, bytes] | None:
        if not path.exists():
            return None
        data = path.read_bytes()
        id_len = data[0]
        w, h, bpp, desc = struct.unpack("<HHBB", data[12:18])
        return (w, h, bpp // 8, bool(desc & 0x20), data[18 + id_len :])

    def has_layer(self, folder: str, ui_id: int) -> bool:
        key = (folder, ui_id)
        if key not in self.mask_cache:
            self.mask_cache[key] = self._load_tga(self.tex_base / f"{folder}/{ui_id}.tga")
        return self.mask_cache[key] is not None

    def sample_mask_alpha(self, folder: str, ui_id: int, zx: float, zy: float) -> int:
        key = (folder, ui_id)
        if key not in self.mask_cache:
            self.mask_cache[key] = self._load_tga(self.tex_base / f"{folder}/{ui_id}.tga")
        img = self.mask_cache[key]
        if img is None or not (0.0 <= zx <= 100.0 and 0.0 <= zy <= 100.0):
            return 0
        w, h, stride, top_origin, buf = img
        px = min(w - 1, max(0, int((zx / 100.0) * w)))
        py_t = min(h - 1, max(0, int((zy / 100.0) * h)))
        py = py_t if top_origin else (h - 1 - py_t)
        idx = (py * w + px) * stride
        return buf[idx + 3] if stride == 4 else buf[idx]

    def sample_lod_rgb(self, cont: int, wx: float, wy: float) -> tuple[int, int, int] | None:
        if cont not in (0, 1):
            return None
        per = 4
        fx = 32.0 - wy / ADT_YARDS
        fy = 32.0 - wx / ADT_YARDS
        col = math.floor(fx / per)
        row = math.floor(fy / per)
        key = (cont, col, row)
        if key not in self.lod_cache:
            self.lod_cache[key] = self._load_tga(self.tex_base / f"MapMiniLOD/{cont}_4_{col}_{row}.tga")
        img = self.lod_cache[key]
        if img is None:
            return None
        w, h, stride, top_origin, buf = img
        px = min(w - 1, max(0, int(((fx - col * per) / per) * w)))
        py_t = min(h - 1, max(0, int(((fy - row * per) / per) * h)))
        py = py_t if top_origin else (h - 1 - py_t)
        idx = (py * w + px) * stride
        return (buf[idx + 2], buf[idx + 1], buf[idx])

    @staticmethod
    def is_water_rgb(rgb: tuple[int, int, int] | None) -> bool:
        if rgb is None or rgb == (0, 0, 0):
            return False
        r, g, b = rgb
        return (b >= r and b >= g - 12 and r < 85) or (r < 12 and g < 22 and b < 15)


@dataclass
class ZoneTerrainData:
    """80x80 terrain representation, component labels, and 20x20 condensed cells for one zone.

    Rules:
    - Always compute flood-fill componentIds on terrain80 BEFORE condensing into 20x20 cells.
    - Expose yard-per-cell scales and terrain-aware 80x80 Dijkstra helpers cleanly.
    """

    zone_id: int
    bounds: tuple[int, float, float, float, float]
    dx_yards: float
    dy_yards: float
    terrain80: list[list[int]]
    comp80: list[list[int]]
    comp_sizes: dict[int, int]
    coarse_cells: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_fine(self, zx: float, zy: float) -> tuple[int, int]:
        gx = min(GRID_FINE - 1, max(0, int(zx / 100.0 * GRID_FINE)))
        gy = min(GRID_FINE - 1, max(0, int(zy / 100.0 * GRID_FINE)))
        return gx, gy

    def to_zone_coords(self, gx: int, gy: int) -> tuple[float, float]:
        zx = round((gx + 0.5) * 100.0 / GRID_FINE, 2)
        zy = round((gy + 0.5) * 100.0 / GRID_FINE, 2)
        return zx, zy

    def cell_cost(self, gx: int, gy: int) -> float:
        t = self.terrain80[gy][gx]
        if t == 3:
            return 0.94
        if t == 2:
            return 1.60
        return 1.00

    def snap_to_walkable(self, zx: float, zy: float, prefer_comp: int | None = None, max_rad: int = 12) -> tuple[int, int, int]:
        gx0, gy0 = self.to_fine(zx, zy)
        if self.terrain80[gy0][gx0] > 0 and (prefer_comp is None or self.comp80[gy0][gx0] == prefer_comp):
            return gx0, gy0, self.comp80[gy0][gx0]
        best = None
        for r in range(1, max_rad + 1):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if abs(dx) != r and abs(dy) != r:
                        continue
                    nx, ny = gx0 + dx, gy0 + dy
                    if 0 <= nx < GRID_FINE and 0 <= ny < GRID_FINE and self.terrain80[ny][nx] > 0:
                        c = self.comp80[ny][nx]
                        if prefer_comp is not None and c != prefer_comp:
                            continue
                        d = math.hypot(dx * self.dx_yards, dy * self.dy_yards)
                        if best is None or d < best[0]:
                            best = (d, nx, ny, c)
            if best is not None:
                return best[1], best[2], best[3]
        # Global fallback within zone if max_rad failed
        for gy in range(GRID_FINE):
            for gx in range(GRID_FINE):
                if self.terrain80[gy][gx] > 0 and (prefer_comp is None or self.comp80[gy][gx] == prefer_comp):
                    d = math.hypot((gx - gx0) * self.dx_yards, (gy - gy0) * self.dy_yards)
                    if best is None or d < best[0]:
                        best = (d, gx, gy, self.comp80[gy][gx])
        if best is not None:
            return best[1], best[2], best[3]
        if prefer_comp is not None:
            return self.snap_to_walkable(zx, zy, prefer_comp=None, max_rad=80)
        return gx0, gy0, 0

    def dijkstra_from(self, src_gx: int, src_gy: int, max_sec: float | None = None) -> dict[tuple[int, int], float]:
        comp = self.comp80[src_gy][src_gx]
        if comp == 0:
            return {}
        dist: dict[tuple[int, int], float] = {(src_gx, src_gy): 0.0}
        pq: list[tuple[float, int, int]] = [(0.0, src_gx, src_gy)]
        diag = math.hypot(self.dx_yards, self.dy_yards)
        steps = (
            (1, 0, self.dx_yards),
            (-1, 0, self.dx_yards),
            (0, 1, self.dy_yards),
            (0, -1, self.dy_yards),
            (1, 1, diag),
            (1, -1, diag),
            (-1, 1, diag),
            (-1, -1, diag),
        )
        while pq:
            d, x, y = heapq.heappop(pq)
            if d > dist.get((x, y), 1e18):
                continue
            if max_sec is not None and d > max_sec:
                break
            c_here = self.cell_cost(x, y)
            for dx, dy, yds in steps:
                nx, ny = x + dx, y + dy
                if 0 <= nx < GRID_FINE and 0 <= ny < GRID_FINE and self.comp80[ny][nx] == comp:
                    if dx and dy and (self.terrain80[y][nx] == 0 and self.terrain80[ny][x] == 0):
                        continue
                    step_sec = (yds / UNMOUNTED_YDS_PER_SEC) * 0.5 * (c_here + self.cell_cost(nx, ny))
                    nd = d + step_sec
                    if nd < dist.get((nx, ny), 1e18):
                        dist[(nx, ny)] = nd
                        heapq.heappush(pq, (nd, nx, ny))
        return dist


class ZoneTerrainBuilder:
    """Constructs verified 80x80 terrain grids and flood-filled componentIds for all 50 zones.

    Rules:
    - Marks walkable land from OpenMap MapZoneLand / MapTerritory / MapCityMask, CaveMinimap/Caves
      tunnels, verified ground-NPC/object Questie spawns, and coastal shallow water channels.
    - Preserves real mountain walls (e.g. Stonetalon ridges, Coldridge outer ridge except tunnel)
      as BLOCKED while bridging single-cell feather artifacts and connecting water islands via SWIM.
    """

    cal: ZoneCalibrator
    tex: TextureTerrainSampler
    db: Any
    creature_types: dict[str, dict[str, Any]]
    quest_pts_by_zone: dict[int, list[tuple[float, float]]]

    def __init__(
        self,
        cal: ZoneCalibrator,
        tex: TextureTerrainSampler,
        db: Any,
        creature_types: dict[str, dict[str, Any]],
        quests: dict[str, dict[str, Any]],
    ) -> None:
        self.cal = cal
        self.tex = tex
        self.db = db
        self.creature_types = creature_types
        self.quest_pts_by_zone = {}
        for q in quests.values():
            for field_name in ("starterCoords", "turninCoords"):
                c = q.get(field_name)
                if c and c.get("zoneId") is not None:
                    self.quest_pts_by_zone.setdefault(int(c["zoneId"]), []).append((float(c["x"]), float(c["y"])))
            for c in q.get("objectiveCoords") or []:
                if c and c.get("zoneId") is not None:
                    self.quest_pts_by_zone.setdefault(int(c["zoneId"]), []).append((float(c["x"]), float(c["y"])))

    def _is_ground_npc(self, nid: int, name: str) -> bool:
        nm = name.lower()
        fam = (self.creature_types.get(str(nid), {}).get("family") or "").lower()
        if fam in AIRBORNE_AQUATIC_FAMILIES:
            return False
        if any(k in nm for k in AQUATIC_KEYWORDS + AIRBORNE_KEYWORDS):
            return False
        return True

    def _collect_ground_spawns(self, zone_id: int) -> list[list[bool]]:
        q_ground = [[False] * GRID_FINE for _ in range(GRID_FINE)]
        for nid, npc in self.db.npcs.items():
            pts = get_zone_entries(npc.get(7), zone_id)
            if not pts or not self._is_ground_npc(int(nid), npc.get(1) or ""):
                continue
            for p in pts:
                if 0.0 <= p[0] <= 100.0 and 0.0 <= p[1] <= 100.0:
                    gx = min(GRID_FINE - 1, int(p[0] / 100.0 * GRID_FINE))
                    gy = min(GRID_FINE - 1, int(p[1] / 100.0 * GRID_FINE))
                    q_ground[gy][gx] = True
        for obj in self.db.objects.values():
            pts = get_zone_entries(obj.get(4), zone_id)
            for p in pts:
                if 0.0 <= p[0] <= 100.0 and 0.0 <= p[1] <= 100.0:
                    gx = min(GRID_FINE - 1, int(p[0] / 100.0 * GRID_FINE))
                    gy = min(GRID_FINE - 1, int(p[1] / 100.0 * GRID_FINE))
                    q_ground[gy][gx] = True
        for zx, zy in self.quest_pts_by_zone.get(zone_id, []):
            if 0.0 <= zx <= 100.0 and 0.0 <= zy <= 100.0:
                gx = min(GRID_FINE - 1, int(zx / 100.0 * GRID_FINE))
                gy = min(GRID_FINE - 1, int(zy / 100.0 * GRID_FINE))
                q_ground[gy][gx] = True
        return q_ground

    def _punch_cave_tunnels(self, zone_id: int, bounds: tuple[int, float, float, float, float], terrain80: list[list[int]]) -> None:
        cont = bounds[0]
        for c_cont, min_x, min_y, max_x, max_y in self.cal.cave_boxes:
            if c_cont != cont:
                continue
            zx0, zy1 = world_to_zone(bounds, min_x, max_y)
            zx1, zy0 = world_to_zone(bounds, max_x, min_y)
            if zx1 < 0.0 or zx0 > 100.0 or zy1 < 0.0 or zy0 > 100.0:
                continue
            # Only punch narrow caves/tunnels that actually cross or touch zone boundaries/inside playfield
            gx0 = max(0, int(max(0.0, zx0) / 100.0 * GRID_FINE))
            gx1 = min(GRID_FINE - 1, int(min(99.9, zx1) / 100.0 * GRID_FINE))
            gy0 = max(0, int(max(0.0, zy0) / 100.0 * GRID_FINE))
            gy1 = min(GRID_FINE - 1, int(min(99.9, zy1) / 100.0 * GRID_FINE))
            # Avoid giant Raid/Outdoor boxes > 800y from overwriting mountain walls blindly
            if (max_x - min_x) > 550.0 or (max_y - min_y) > 550.0:
                continue
            # Carve centered cross/core inside the tunnel bounding box if overlapping existing walkable land
            has_land = any(
                terrain80[gy][gx] > 0
                for gy in range(max(0, gy0 - 1), min(GRID_FINE, gy1 + 2))
                for gx in range(max(0, gx0 - 1), min(GRID_FINE, gx1 + 2))
            )
            if not has_land:
                continue
            mid_gx = (gx0 + gx1) // 2
            mid_gy = (gy0 + gy1) // 2
            for gy in range(gy0, gy1 + 1):
                terrain80[gy][mid_gx] = max(terrain80[gy][mid_gx], 1)
            for gx in range(gx0, gx1 + 1):
                terrain80[mid_gy][gx] = max(terrain80[mid_gy][gx], 1)

    @staticmethod
    def _flood_fill(terrain80: list[list[int]]) -> tuple[list[list[int]], dict[int, int]]:
        comp80 = [[0] * GRID_FINE for _ in range(GRID_FINE)]
        comp_sizes: dict[int, int] = {}
        cid = 0
        for gy in range(GRID_FINE):
            for gx in range(GRID_FINE):
                if terrain80[gy][gx] > 0 and comp80[gy][gx] == 0:
                    cid += 1
                    q = deque([(gx, gy)])
                    comp80[gy][gx] = cid
                    sz = 0
                    while q:
                        x, y = q.popleft()
                        sz += 1
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < GRID_FINE and 0 <= ny < GRID_FINE and terrain80[ny][nx] > 0 and comp80[ny][nx] == 0:
                                comp80[ny][nx] = cid
                                q.append((nx, ny))
                    comp_sizes[cid] = sz
        return comp80, comp_sizes

    def _connect_swim_and_micro_islands(
        self,
        zone_id: int,
        water80: list[list[bool]],
        terrain80: list[list[int]],
    ) -> tuple[list[list[int]], dict[int, int]]:
        # First pass: bridge micro islands (<= 8 cells) ONLY across water OR <= 2 cells gap across shore
        comp80, sizes = self._flood_fill(terrain80)
        if len(sizes) <= 1:
            return comp80, sizes
        main_cid = max(sizes, key=lambda k: sizes[k])
        cells_by_cid: dict[int, list[tuple[int, int]]] = {}
        for gy in range(GRID_FINE):
            for gx in range(GRID_FINE):
                c = comp80[gy][gx]
                if c > 0:
                    cells_by_cid.setdefault(c, []).append((gx, gy))
        main_cells = cells_by_cid[main_cid]
        for cid, pts in list(cells_by_cid.items()):
            if cid == main_cid:
                continue
            # Check shortest distance from this component to main component
            best = None
            for ax, ay in pts:
                for bx, by in main_cells[:: max(1, len(main_cells) // 250)]:
                    d = abs(ax - bx) + abs(ay - by)
                    if best is None or d < best[0]:
                        best = (d, ax, ay, bx, by)
            if best is None:
                continue
            steps, ax, ay, bx, by = best
            # Re-check exact closest point in main_cells near (bx, by)
            near = [(abs(ax - x) + abs(ay - y), ax, ay, x, y) for (x, y) in main_cells if abs(x - bx) <= 10 and abs(y - by) <= 10]
            if near:
                steps, ax, ay, bx, by = min(near)
            if steps == 0:
                continue
            # Inspect intermediate cells along Manhattan L/Bresenham path
            n_pts = max(abs(bx - ax), abs(by - ay))
            path_pts = []
            water_count = 0
            for k in range(1, n_pts):
                px = round(ax + (bx - ax) * k / n_pts)
                py = round(ay + (by - ay) * k / n_pts)
                path_pts.append((px, py))
                if water80[py][px]:
                    water_count += 1
            # Bridge ONLY if crossing water (swim channel up to 16 cells) OR tiny feather gap (<= 2 dry cells)
            if (steps <= 16 and water_count >= max(1, len(path_pts) - 2)) or steps <= 2:
                for px, py in path_pts:
                    if terrain80[py][px] == 0:
                        terrain80[py][px] = 2 if water80[py][px] else 1
        return self._flood_fill(terrain80)

    def build_zone_terrain(self, zone_id: int, fixed_pts: list[tuple[float, float]]) -> ZoneTerrainData:
        bounds = self.cal.bounds_by_zone[zone_id]
        ui_id = self.cal.uimap_by_zone[zone_id]
        width_yds = abs(bounds[1] - bounds[2])
        height_yds = abs(bounds[3] - bounds[4])
        dx_yards = width_yds / GRID_FINE
        dy_yards = height_yds / GRID_FINE

        q_ground = self._collect_ground_spawns(zone_id)
        for zx, zy in fixed_pts:
            if 0.0 <= zx <= 100.0 and 0.0 <= zy <= 100.0:
                q_ground[min(GRID_FINE - 1, int(zy / 100.0 * GRID_FINE))][min(GRID_FINE - 1, int(zx / 100.0 * GRID_FINE))] = True

        has_zl = self.tex.has_layer("MapZoneLand", ui_id)
        has_terr = (not has_zl) and self.tex.has_layer("MapTerritory", ui_id)
        has_city = (not has_zl) and self.tex.has_layer("MapCityMask", ui_id)
        is_underground_city = zone_id == 1497  # Undercity lies under Lordaeron moat water in MapMiniLOD

        terrain80 = [[0] * GRID_FINE for _ in range(GRID_FINE)]
        water80 = [[False] * GRID_FINE for _ in range(GRID_FINE)]
        lum80 = [[0.0] * GRID_FINE for _ in range(GRID_FINE)]
        rgb80: list[list[tuple[int, int, int] | None]] = [[None] * GRID_FINE for _ in range(GRID_FINE)]

        # Pre-dilate quest spawns by 1 cell ONLY in cities lacking MapZoneLand/MapCityMask (Stormwind, Darnassus)
        city_dilate = [[False] * GRID_FINE for _ in range(GRID_FINE)]
        if not has_zl and not has_terr:
            for gy in range(GRID_FINE):
                for gx in range(GRID_FINE):
                    if q_ground[gy][gx]:
                        for dy in range(-2, 3):
                            for dx in range(-2, 3):
                                ny, nx = gy + dy, gx + dx
                                if 0 <= ny < GRID_FINE and 0 <= nx < GRID_FINE and (dx * dx + dy * dy) <= 5:
                                    city_dilate[ny][nx] = True

        for gy in range(GRID_FINE):
            for gx in range(GRID_FINE):
                zx = (gx + 0.5) * 100.0 / GRID_FINE
                zy = (gy + 0.5) * 100.0 / GRID_FINE
                _, wx, wy = zone_to_world(bounds, zx, zy)
                rgb = self.tex.sample_lod_rgb(bounds[0], wx, wy)
                rgb80[gy][gx] = rgb
                if rgb:
                    lum80[gy][gx] = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
                is_wtr = (not is_underground_city) and self.tex.is_water_rgb(rgb)
                water80[gy][gx] = is_wtr

                if has_zl:
                    a = self.tex.sample_mask_alpha("MapZoneLand", ui_id, zx, zy)
                    land = a >= 80
                elif has_terr:
                    a = self.tex.sample_mask_alpha("MapTerritory", ui_id, zx, zy)
                    land = a >= 80
                elif has_city:
                    a = self.tex.sample_mask_alpha("MapCityMask", ui_id, zx, zy)
                    land = (a >= 96) or city_dilate[gy][gx]
                else:
                    # Stormwind (1519), Darnassus (1657): LOD landmass or dilated city district spawns inside map frame
                    land = city_dilate[gy][gx] or (
                        12.0 <= zx <= 88.0 and 12.0 <= zy <= 88.0 and rgb is not None and rgb != (0, 0, 0) and not is_wtr
                    )

                if land or q_ground[gy][gx]:
                    terrain80[gy][gx] = 2 if (is_wtr and not q_ground[gy][gx]) else 1

        self._punch_cave_tunnels(zone_id, bounds, terrain80)

        # Despeckled ROAD detection from MapMiniLOD warm bright trails
        road_cand = [[False] * GRID_FINE for _ in range(GRID_FINE)]
        for gy in range(GRID_FINE):
            for gx in range(GRID_FINE):
                rgb = rgb80[gy][gx]
                if terrain80[gy][gx] == 1 and rgb is not None:
                    r, g, b_ch = rgb
                    box = [
                        lum80[ny][nx]
                        for ny in range(max(0, gy - 2), min(GRID_FINE, gy + 3))
                        for nx in range(max(0, gx - 2), min(GRID_FINE, gx + 3))
                        if lum80[ny][nx] > 0
                    ]
                    if box:
                        avg = sum(box) / len(box)
                        if lum80[gy][gx] > avg + 18.0 and r >= g and (r - b_ch) >= 20:
                            road_cand[gy][gx] = True
        for gy in range(GRID_FINE):
            for gx in range(GRID_FINE):
                if road_cand[gy][gx]:
                    nbrs = sum(
                        1
                        for dy in (-1, 0, 1)
                        for dx in (-1, 0, 1)
                        if (dx or dy) and 0 <= gy + dy < GRID_FINE and 0 <= gx + dx < GRID_FINE and road_cand[gy + dy][gx + dx]
                    )
                    if nbrs >= 2:
                        terrain80[gy][gx] = 3

        comp80, comp_sizes = self._connect_swim_and_micro_islands(zone_id, water80, terrain80)
        return ZoneTerrainData(
            zone_id=zone_id,
            bounds=bounds,
            dx_yards=dx_yards,
            dy_yards=dy_yards,
            terrain80=terrain80,
            comp80=comp80,
            comp_sizes=comp_sizes,
        )


class NavGraphBuilder:
    """Builds condensed NavGraph nodes/edges, 20x20 zoneGrids, hearthNodes, and questAnchors.

    Rules:
    - Enforces nodeCount <= MAX_NODES (1500) and edgeCount <= MAX_EDGES (6000).
    - Uses OpenMap zone-land masks and port hints so docks/airships always land in their true zone.
    - Bridges every land border gate onto each zone's main connected component when available.
    - Estimates missing/zero-second OpenMap flight times from yards/30s so flights never warp in 0.5s.
    - Guarantees every 20x20 non-blocked cell receives <= 3 anchors strictly sharing its componentId.
    - Guarantees every quest in data/quests.json with coordinates gets valid terrain-snapped anchors
      (t=null when turn-in matches starter).
    """

    root_dir: Path
    cal: ZoneCalibrator
    tex: TextureTerrainSampler
    quests: dict[str, dict[str, Any]]
    dungeons: dict[str, dict[str, Any]]
    travel_graph: dict[str, Any]
    creature_types: dict[str, dict[str, Any]]
    zdata: dict[int, ZoneTerrainData]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    hearth_nodes: dict[str, int]
    zone_grids: dict[str, dict[str, Any]]
    quest_anchors: dict[str, dict[str, Any]]

    def __init__(self, root_dir: Path = PROJECT_ROOT) -> None:
        self.root_dir = root_dir
        self.quests = json.loads((root_dir / "data/quests.json").read_text())["quests"]
        self.dungeons = json.loads((root_dir / "data/dungeons.json").read_text())["dungeons"]
        self.travel_graph = json.loads((root_dir / "data/travel_graph.json").read_text())
        self.creature_types = json.loads((root_dir / "scraper/cache/npc_creature_types.json").read_text())["npcs"]
        zones = {int(k): v for k, v in self.travel_graph["nodes"].items()}
        self.cal = ZoneCalibrator(zones)
        self.tex = TextureTerrainSampler(TEX_BASE_DIR)
        self.db = QuestieDBReader(QUESTIEDB_DIR).load()
        self.zdata = {}
        self.nodes = []
        self.edges = []
        self.hearth_nodes = {}
        self.zone_grids = {}
        self.quest_anchors = {}

    def _map_fd_to_zone(self, fd_id: int) -> int:
        info = self.cal.fd_nodes[fd_id]
        name_l = info[0].lower()
        cont, wx, wy = int(info[1]), float(info[2]), float(info[3])
        explicit_prefix = {
            "stormwind": 1519,
            "ironforge": 1537,
            "undercity": 1497,
            "orgrimmar": 1637,
            "thunder bluff": 1638,
            "darnassus": 1657,
            "rut'theran": 141,
            "rog'mar": 16591,
            "farholde": 16591,
        }
        for pref, zid in explicit_prefix.items():
            if pref in name_l:
                return zid
        best = None
        for zid, b in self.cal.bounds_by_zone.items():
            if b[0] != cont:
                continue
            zname = self.cal.zones[zid]["name"].lower()
            zx, zy = world_to_zone(b, wx, wy)
            if -2.0 <= zx <= 102.0 and -2.0 <= zy <= 102.0:
                score = abs(zx - 50.0) + abs(zy - 50.0)
                if zname in name_l or zname.replace("the ", "") in name_l:
                    score -= 1000.0
                if best is None or score < best[0]:
                    best = (score, zid)
        if best is None:
            raise ValueError(f"Could not place flight node {fd_id}: {info}")
        return best[1]

    def _compute_fixed_pois_and_gates(
        self,
    ) -> tuple[dict[int, list[dict[str, Any]]], list[tuple[int, int, str, float, bool, str, str]], dict[int, int]]:
        """Collect POI specs per zone and cross-zone gate/transport pairings."""
        raw_pois: dict[int, list[dict[str, Any]]] = {zid: [] for zid in self.cal.zones}
        fd_to_spec: dict[int, tuple[int, dict[str, Any]]] = {}

        # 1. Flight masters (all open-world nodes in FlightData)
        for fd_id, info in sorted(self.cal.fd_nodes.items()):
            if int(info[1]) not in (0, 1, 2991):
                continue
            zid = self._map_fd_to_zone(fd_id)
            zx, zy = world_to_zone(self.cal.bounds_by_zone[zid], float(info[2]), float(info[3]))
            zx = round(min(99.5, max(0.5, zx)), 2)
            zy = round(min(99.5, max(0.5, zy)), 2)
            fac = "Alliance" if info[4] == 1 else ("Horde" if info[4] == 2 else "Both")
            spec = {
                "key": f"fp_{fd_id}",
                "zoneId": zid,
                "x": zx,
                "y": zy,
                "kind": "flight",
                "faction": fac,
                "flightNodeId": int(fd_id),
                "name": info[0],
            }
            raw_pois[zid].append(spec)
            fd_to_spec[fd_id] = (zid, spec)

        # 2. Boats, Zeppelins, Airships (from Places.lua transports), plus Deeprun Tram & Rut'theran teleporter
        transport_specs: list[tuple[str, int, float, float, str, str, str]] = []
        # Map transport world coords cleanly to zone coords
        def map_transport_pt(label: str, cont: int, wx: float, wy: float, mode: str) -> tuple[int, float, float]:
            l_low = label.lower()
            hints = [
                ("booty bay", 33),
                ("ratchet", 17),
                ("grom'gol", 33),
                ("orgrimmar", 14),
                ("tirisfal", 85),
                ("undercity", 85),
                ("menethil", 11),
                ("theramore", 15),
                ("rut'theran", 141),
                ("auberdine", 148),
                ("feathermoon", 357),
                ("southshore", 267),
                ("steamwheedle", 440),
                ("powderfuse", 16591),
                ("alterac", 36),
                ("mulgore", 215),
                ("zephras", 16593),
                ("stormwind", 1519),
            ]
            # Match dock endpoint strictly preferring port name hint + inside zone + walkable landmass mask
            cands = []
            for zid, b in self.cal.bounds_by_zone.items():
                if b[0] != cont:
                    continue
                zx, zy = world_to_zone(b, wx, wy)
                if -5.0 <= zx <= 105.0 and -5.0 <= zy <= 105.0:
                    in_bounds = 0.0 <= zx <= 100.0 and 0.0 <= zy <= 100.0
                    czx, czy = min(99.5, max(0.5, zx)), min(99.5, max(0.5, zy))
                    ui_id = self.cal.uimap_by_zone[zid]
                    has_land = (
                        self.tex.sample_mask_alpha("MapZoneLand", ui_id, czx, czy) >= 40
                        if self.tex.has_layer("MapZoneLand", ui_id)
                        else in_bounds
                    )
                    hint_match = any(k in l_low and h_zid == zid for k, h_zid in hints)
                    zepp_prio = -10 if (mode == "zeppelin" and zid in (14, 85)) else 0
                    score = (
                        0 if hint_match else 1,
                        0 if in_bounds else 1,
                        0 if has_land else 1,
                        zepp_prio,
                        abs(zx - 50.0) + abs(zy - 50.0),
                    )
                    cands.append((score, zid, czx, czy))
            cands.sort()
            _, zid, zx, zy = cands[0]
            return zid, round(zx, 2), round(zy, 2)

        def transport_faction(mode: str, label: str) -> str:
            if mode == "zeppelin":
                return "Horde"
            if mode == "boat":
                ll = label.lower()
                if any(k in ll for k in ("booty bay", "feathermoon", "steamwheedle", "powderfuse")):
                    return "Both"
                return "Alliance"
            return "Both"

        # Deduplicate transport dock endpoints by (zid, mode, rounded coord)
        dock_nodes: dict[tuple[int, str, int, int], dict[str, Any]] = {}
        cross_edges_pending: list[tuple[dict[str, Any], dict[str, Any], str, float, bool, str, str, int | None]] = []

        # Map transport timings from travel_graph edges
        tg_trans_sec: dict[tuple[int, int, str], int] = {}
        for e in self.travel_graph["edges"]:
            if e["mode"] in ("boat", "zeppelin", "airship", "tram"):
                tg_trans_sec[(int(e["fromZoneId"]), int(e["toZoneId"]), e["mode"])] = int(e["seconds"])

        def get_or_add_dock(zid: int, zx: float, zy: float, mode: str, fac: str, label: str) -> dict[str, Any]:
            # Group docks in same zone & mode within 3.5 map units
            for (dz, dm, rx, ry), dspec in dock_nodes.items():
                if dz == zid and dm == mode and math.hypot(dspec["x"] - zx, dspec["y"] - zy) <= 3.5:
                    if fac != dspec["faction"]:
                        dspec["faction"] = "Both"
                    return dspec
            key_t = (zid, mode, int(round(zx)), int(round(zy)))
            spec = {
                "key": f"{mode}_{zid}_{int(round(zx))}_{int(round(zy))}",
                "zoneId": zid,
                "x": zx,
                "y": zy,
                "kind": mode,
                "faction": fac,
                "flightNodeId": None,
                "name": label,
            }
            dock_nodes[key_t] = spec
            raw_pois[zid].append(spec)
            return spec

        for tr in self.cal.transports:
            mode, label, c1, x1, y1, c2, x2, y2 = tr[0], tr[1], int(tr[2]), float(tr[3]), float(tr[4]), int(tr[5]), float(tr[6]), float(tr[7])
            z1, zx1, zy1 = map_transport_pt(label, c1, x1, y1, mode)
            z2, zx2, zy2 = map_transport_pt(label, c2, x2, y2, mode)
            fac = transport_faction(mode, label)
            s1 = get_or_add_dock(z1, zx1, zy1, mode, fac, label)
            s2 = get_or_add_dock(z2, zx2, zy2, mode, fac, label)
            # Lookup timing (Durotar zeppelin maps to Org in travel_graph)
            tz1 = 1637 if z1 == 14 else z1
            tz2 = 1637 if z2 == 14 else z2
            sec = tg_trans_sec.get((tz1, tz2, mode)) or tg_trans_sec.get((z1, z2, mode)) or (300 if mode == "boat" else 180)
            cross_edges_pending.append((s1, s2, mode, float(sec), False, fac, label, z2))

        # Add Deeprun Tram (SW Dwarf District 63.9, 8.2 <-> IF Tinker Town 76.2, 51.2)
        tram_sw = get_or_add_dock(1519, 63.9, 8.2, "tram", "Alliance", "Deeprun Tram (Stormwind)")
        tram_if = get_or_add_dock(1537, 76.2, 51.2, "tram", "Alliance", "Deeprun Tram (Ironforge)")
        cross_edges_pending.append((tram_sw, tram_if, "tram", 75.0, False, "Alliance", "Deeprun Tram (Stormwind -> Ironforge)", 1537))
        cross_edges_pending.append((tram_if, tram_sw, "tram", 75.0, False, "Alliance", "Deeprun Tram (Ironforge -> Stormwind)", 1519))

        # 3. Zone Hearth / Hub nodes from travel_graph
        hearth_specs: dict[int, dict[str, Any]] = {}
        for zid, zinfo in sorted(self.cal.zones.items()):
            hc = zinfo["hubCoords"]
            zx, zy = float(hc["x"]), float(hc["y"])
            # Reuse existing flight/dock node in same zone if within 80y
            matched = None
            b = self.cal.bounds_by_zone[zid]
            _, hwx, hwy = zone_to_world(b, zx, zy)
            for p in raw_pois[zid]:
                _, pwx, pwy = zone_to_world(b, p["x"], p["y"])
                if math.hypot(hwx - pwx, hwy - pwy) <= 85.0:
                    matched = p
                    break
            if matched is not None:
                hearth_specs[zid] = matched
            else:
                spec = {
                    "key": f"hub_{zid}",
                    "zoneId": zid,
                    "x": round(zx, 2),
                    "y": round(zy, 2),
                    "kind": "hub",
                    "faction": zinfo["faction"] if zinfo["faction"] in ("Alliance", "Horde") else "Both",
                    "flightNodeId": None,
                    "name": zinfo["hubName"],
                }
                raw_pois[zid].append(spec)
                hearth_specs[zid] = spec

        # 4. Dungeon entrances from data/dungeons.json
        for dinfo in self.dungeons.values():
            ec = dinfo.get("entranceCoords")
            if not ec:
                continue
            zid = int(ec["zoneId"])
            zx, zy = float(ec["x"]), float(ec["y"])
            b = self.cal.bounds_by_zone[zid]
            _, dwx, dwy = zone_to_world(b, zx, zy)
            dup = False
            for p in raw_pois[zid]:
                _, pwx, pwy = zone_to_world(b, p["x"], p["y"])
                if p["kind"] == "dungeon" and math.hypot(dwx - pwx, dwy - pwy) <= 60.0:
                    dup = True
                    break
            if not dup:
                raw_pois[zid].append({
                    "key": f"dun_{dinfo['dungeonId']}",
                    "zoneId": zid,
                    "x": round(zx, 2),
                    "y": round(zy, 2),
                    "kind": "dungeon",
                    "faction": dinfo["faction"] if dinfo["faction"] in ("Alliance", "Horde") else "Both",
                    "flightNodeId": None,
                    "name": dinfo["name"],
                })

        return raw_pois, fd_to_spec, cross_edges_pending, hearth_specs

    def _find_border_gate_pair(
        self, za: int, zb: int, base_secs: float, note: str
    ) -> tuple[tuple[float, float], tuple[float, float], float, str]:
        ta, tb = self.zdata[za], self.zdata[zb]
        ba, bb = ta.bounds, tb.bounds
        key_pair = frozenset((za, zb))

        if key_pair in SPECIAL_BORDER_HINTS:
            _, wx, wy, cross_sec, desc = SPECIAL_BORDER_HINTS[key_pair]
            ax, ay = world_to_zone(ba, wx, wy)
            bx, by = world_to_zone(bb, wx, wy)
            ax = min(99.5, max(0.5, ax))
            ay = min(99.5, max(0.5, ay))
            bx = min(99.5, max(0.5, bx))
            by = min(99.5, max(0.5, by))
            return (ax, ay), (bx, by), cross_sec, desc

        main_a = max(ta.comp_sizes, key=lambda k: ta.comp_sizes[k])
        main_b = max(tb.comp_sizes, key=lambda k: tb.comp_sizes[k])

        # Search walkable overlaps between ta and tb preferring main component + ROAD/dry ground
        cands = []
        for gy in range(GRID_FINE):
            for gx in range(GRID_FINE):
                if ta.terrain80[gy][gx] in (1, 3):
                    zx, zy = ta.to_zone_coords(gx, gy)
                    _, wx, wy = zone_to_world(ba, zx, zy)
                    bzx, bzy = world_to_zone(bb, wx, wy)
                    if 0.0 <= bzx <= 100.0 and 0.0 <= bzy <= 100.0:
                        bgx, bgy = tb.to_fine(bzx, bzy)
                        if tb.terrain80[bgy][bgx] in (1, 3):
                            ca = ta.comp80[gy][gx]
                            cb = tb.comp80[bgy][bgx]
                            road_bonus = (2 if ta.terrain80[gy][gx] == 3 else 0) + (2 if tb.terrain80[bgy][bgx] == 3 else 0)
                            comp_ok = (
                                (4 if ca == main_a else (1 if ta.comp_sizes.get(ca, 0) >= 80 else 0))
                                + (4 if cb == main_b else (1 if tb.comp_sizes.get(cb, 0) >= 80 else 0))
                            )
                            cands.append((comp_ok, road_bonus, zx, zy, bzx, bzy))
        main_cands = [c for c in cands if c[0] == 8]
        if main_cands:
            best_score = max(c[1] for c in main_cands)
            top = [c for c in main_cands if c[1] == best_score]
            # Pick median point in sorted order along overlap strip (stays on actual terrain overlap!)
            top.sort(key=lambda c: (c[2], c[3]))
            mid = top[len(top) // 2]
            return (mid[2], mid[3]), (mid[4], mid[5]), 4.0, note

        # If no direct overlap (tiny boundary river/gap like WPL<->Hillsbrad or Desolace<->Feralas): pick closest pair in main components!
        pts_a = [(gx, gy) for gy in range(GRID_FINE) for gx in range(GRID_FINE) if ta.terrain80[gy][gx] in (1, 3) and ta.comp80[gy][gx] == main_a]
        pts_b = [(gx, gy) for gy in range(GRID_FINE) for gx in range(GRID_FINE) if tb.terrain80[gy][gx] in (1, 3) and tb.comp80[gy][gx] == main_b]
        # Downsample first, then refine
        best_p = None
        for gx, gy in pts_a[::3]:
            zx, zy = ta.to_zone_coords(gx, gy)
            _, wxa, wya = zone_to_world(ba, zx, zy)
            for bx_g, by_g in pts_b[::3]:
                bzx, bzy = tb.to_zone_coords(bx_g, by_g)
                _, wxb, wyb = zone_to_world(bb, bzx, bzy)
                d = math.hypot(wxa - wxb, wya - wyb)
                if best_p is None or d < best_p[0]:
                    best_p = (d, zx, zy, bzx, bzy)
        d0, azx0, azy0, bzx0, bzy0 = best_p
        # Local refinement within 4 cells
        agx0, agy0 = ta.to_fine(azx0, azy0)
        bgx0, bgy0 = tb.to_fine(bzx0, bzy0)
        best_f = (d0, azx0, azy0, bzx0, bzy0)
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                ax, ay = agx0 + dx, agy0 + dy
                if 0 <= ax < GRID_FINE and 0 <= ay < GRID_FINE and ta.terrain80[ay][ax] in (1, 3) and ta.comp80[ay][ax] == main_a:
                    zx, zy = ta.to_zone_coords(ax, ay)
                    _, wxa, wya = zone_to_world(ba, zx, zy)
                    for bdy in range(-4, 5):
                        for bdx in range(-4, 5):
                            bx, by = bgx0 + bdx, bgy0 + bdy
                            if 0 <= bx < GRID_FINE and 0 <= by < GRID_FINE and tb.terrain80[by][bx] in (1, 3) and tb.comp80[by][bx] == main_b:
                                bzx, bzy = tb.to_zone_coords(bx, by)
                                _, wxb, wyb = zone_to_world(bb, bzx, bzy)
                                d = math.hypot(wxa - wxb, wya - wyb)
                                if d < best_f[0]:
                                    best_f = (d, zx, zy, bzx, bzy)
        cross_sec = max(5.0, round(best_f[0] / UNMOUNTED_YDS_PER_SEC, 1))
        return (best_f[1], best_f[2]), (best_f[3], best_f[4]), cross_sec, note

    def build(self) -> dict[str, Any]:
        raw_pois, fd_to_spec, cross_edges_pending, hearth_specs = self._compute_fixed_pois_and_gates()

        # Pass 1: Build 80x80 terrain per zone (including POI anchors as ground spawns)
        print("Building 80x80 terrain + flood-filling components across 50 zones...")
        z_builder = ZoneTerrainBuilder(self.cal, self.tex, self.db, self.creature_types, self.quests)
        for zid in sorted(self.cal.zones):
            pts = [(p["x"], p["y"]) for p in raw_pois[zid]]
            self.zdata[zid] = z_builder.build_zone_terrain(zid, pts)

        # Pass 2: Pair all LAND_ADJACENCIES borders + Rut'theran<->Darnassus teleporter gate
        gate_pairs_edges: list[tuple[dict[str, Any], dict[str, Any], str, float, bool, str, str, int | None]] = []
        adj_list = list(build_dataset.LAND_ADJACENCIES)
        for za, zb, secs, note in adj_list:
            (ax, ay), (bx, by), cross_sec, desc = self._find_border_gate_pair(za, zb, float(secs), note)
            ga = {
                "key": f"gate_{za}_{zb}_a",
                "zoneId": za,
                "x": round(ax, 2),
                "y": round(ay, 2),
                "kind": "gate",
                "faction": "Both",
                "flightNodeId": None,
                "name": f"{desc} ({self.cal.zones[za]['name']} side)",
            }
            gb = {
                "key": f"gate_{za}_{zb}_b",
                "zoneId": zb,
                "x": round(bx, 2),
                "y": round(by, 2),
                "kind": "gate",
                "faction": "Both",
                "flightNodeId": None,
                "name": f"{desc} ({self.cal.zones[zb]['name']} side)",
            }
            raw_pois[za].append(ga)
            raw_pois[zb].append(gb)
            gate_pairs_edges.append((ga, gb, "gate", round(cross_sec, 1), True, "Both", desc, None))
            gate_pairs_edges.append((gb, ga, "gate", round(cross_sec, 1), True, "Both", desc, None))

        # Add Rut'theran Village (Teldrassil 55.9, 89.9) <-> Darnassus Teleporter Gate (30.5, 41.4) + Darnassus Gate
        rut_gate = {
            "key": "gate_rut_tele_teldrassil",
            "zoneId": 141,
            "x": 55.9,
            "y": 89.9,
            "kind": "gate",
            "faction": "Alliance",
            "flightNodeId": None,
            "name": "Rut'theran Village Teleporter (Teldrassil)",
        }
        darn_tele = {
            "key": "gate_rut_tele_darnassus",
            "zoneId": 1657,
            "x": 30.5,
            "y": 41.4,
            "kind": "gate",
            "faction": "Alliance",
            "flightNodeId": None,
            "name": "Darnassus Temple Teleporter -> Rut'theran",
        }
        raw_pois[141].append(rut_gate)
        raw_pois[1657].append(darn_tele)
        gate_pairs_edges.append((rut_gate, darn_tele, "gate", 8.0, True, "Alliance", "Rut'theran <-> Darnassus Teleporter", None))
        gate_pairs_edges.append((darn_tele, rut_gate, "gate", 8.0, True, "Alliance", "Darnassus <-> Rut'theran Teleporter", None))

        # Ensure Teldrassil Rut'theran & Darnassus gate coords are walkable in zdata
        for g in (rut_gate, darn_tele):
            zt = self.zdata[g["zoneId"]]
            fgx, fgy = zt.to_fine(g["x"], g["y"])
            if zt.terrain80[fgy][fgx] == 0:
                zt.terrain80[fgy][fgx] = 1
                zt.comp80, zt.comp_sizes = ZoneTerrainBuilder._flood_fill(zt.terrain80)

        # Snap EVERY fixed POI to its walkable 80x80 pixel & assign componentId
        zone_nodes_specs: dict[int, list[dict[str, Any]]] = {zid: [] for zid in self.cal.zones}
        for zid in sorted(self.cal.zones):
            zt = self.zdata[zid]
            for spec in raw_pois[zid]:
                gx, gy, comp = zt.snap_to_walkable(spec["x"], spec["y"])
                spec["_gx"] = gx
                spec["_gy"] = gy
                spec["componentId"] = comp
                zone_nodes_specs[zid].append(spec)

        # Pass 3: Interior condensation nodes via farthest-point sampling within remaining budget
        fixed_total = sum(len(v) for v in zone_nodes_specs.values())
        avail_interior = max(200, min(800, MAX_NODES - 80 - fixed_total))
        zone_weights: dict[int, float] = {}
        for zid, zt in self.zdata.items():
            walk_cnt = sum(1 for gy in range(GRID_FINE) for gx in range(GRID_FINE) if zt.terrain80[gy][gx] > 0)
            area_km = (abs(zt.bounds[1] - zt.bounds[2]) * abs(zt.bounds[3] - zt.bounds[4])) / 1_000_000.0
            zone_weights[zid] = math.sqrt(max(0.2, area_km)) * (walk_cnt / 6400.0 + 0.15)
        tot_w = sum(zone_weights.values())

        print(f"Fixed POIs={fixed_total}; allocating ~{avail_interior} condensed road/anchor nodes across 50 zones...")
        for zid in sorted(self.cal.zones):
            zt = self.zdata[zid]
            specs = zone_nodes_specs[zid]
            # Track shortest walk-sec to any node per 80x80 cell per component
            dist_grid = [[1e9] * GRID_FINE for _ in range(GRID_FINE)]
            for spec in specs:
                dmap = zt.dijkstra_from(spec["_gx"], spec["_gy"])
                for (x, y), d in dmap.items():
                    if d < dist_grid[y][x]:
                        dist_grid[y][x] = d

            # First guarantee every connected component of size >= 4 has >= 1 node
            for cid, sz in sorted(zt.comp_sizes.items(), key=lambda kv: -kv[1]):
                if sz < 4:
                    continue
                if not any(s["componentId"] == cid for s in specs):
                    comp_pts = [(gx, gy) for gy in range(GRID_FINE) for gx in range(GRID_FINE) if zt.comp80[gy][gx] == cid]
                    avg_x = sum(p[0] for p in comp_pts) / len(comp_pts)
                    avg_y = sum(p[1] for p in comp_pts) / len(comp_pts)
                    gx, gy = min(comp_pts, key=lambda p: (0 if zt.terrain80[p[1]][p[0]] == 3 else 1, math.hypot(p[0] - avg_x, p[1] - avg_y)))
                    zx, zy = zt.to_zone_coords(gx, gy)
                    spec = {
                        "key": f"anchor_{zid}_c{cid}",
                        "zoneId": zid,
                        "x": zx,
                        "y": zy,
                        "kind": "road" if zt.terrain80[gy][gx] == 3 else "anchor",
                        "faction": "Both",
                        "flightNodeId": None,
                        "name": f"{self.cal.zones[zid]['name']} Basin {cid}",
                        "componentId": cid,
                        "_gx": gx,
                        "_gy": gy,
                    }
                    specs.append(spec)
                    dmap = zt.dijkstra_from(gx, gy)
                    for (x, y), d in dmap.items():
                        if d < dist_grid[y][x]:
                            dist_grid[y][x] = d

            # Greedy farthest-point interior nodes up to zone quota
            quota = max(4, min(32, round(avail_interior * (zone_weights[zid] / tot_w))))
            # Only add if farthest cell is > 28s walk away (~210y) or large zone needs density
            for idx_k in range(quota):
                if sum(len(v) for v in zone_nodes_specs.values()) >= MAX_NODES - 10:
                    break
                best_pt = None
                for gy in range(GRID_FINE):
                    for gx in range(GRID_FINE):
                        if zt.terrain80[gy][gx] > 0 and zt.comp_sizes.get(zt.comp80[gy][gx], 0) >= 6:
                            d = dist_grid[gy][gx]
                            # Mildly favor ROAD cells by +4s priority when near same max distance
                            eff_d = d + (4.0 if zt.terrain80[gy][gx] == 3 else 0.0)
                            if best_pt is None or eff_d > best_pt[0]:
                                best_pt = (eff_d, d, gx, gy)
                if best_pt is None or best_pt[1] < 24.0:
                    break
                _, real_d, gx, gy = best_pt
                cid = zt.comp80[gy][gx]
                zx, zy = zt.to_zone_coords(gx, gy)
                spec = {
                    "key": f"anc_{zid}_{idx_k + 1}",
                    "zoneId": zid,
                    "x": zx,
                    "y": zy,
                    "kind": "road" if zt.terrain80[gy][gx] == 3 else "anchor",
                    "faction": "Both",
                    "flightNodeId": None,
                    "name": "",
                    "componentId": cid,
                    "_gx": gx,
                    "_gy": gy,
                }
                specs.append(spec)
                dmap = zt.dijkstra_from(gx, gy)
                for (x, y), d in dmap.items():
                    if d < dist_grid[y][x]:
                        dist_grid[y][x] = d

        # Assign final 1..N sequential IDs cleanly
        next_id = 1
        for zid in sorted(self.cal.zones):
            for spec in zone_nodes_specs[zid]:
                spec["id"] = next_id
                self.nodes.append({
                    "id": next_id,
                    "key": spec["key"],
                    "zoneId": spec["zoneId"],
                    "x": spec["x"],
                    "y": spec["y"],
                    "kind": spec["kind"],
                    "faction": spec["faction"],
                    "componentId": spec["componentId"],
                    "flightNodeId": spec["flightNodeId"],
                    "name": spec["name"],
                })
                next_id += 1

        for zid, hspec in sorted(hearth_specs.items()):
            self.hearth_nodes[str(zid)] = int(hspec["id"])

        # Pass 4: Build intra-zone walk edges (sparse connected graph per zone+component) + all cross-zone edges
        print(f"Final nodeCount={len(self.nodes)}. Building intra-zone terrain mesh & cross-zone edges...")
        edge_dedup: set[tuple[int, int, str]] = set()

        def emit_edge(from_id: int, to_id: int, mode: str, sec: float, ground_scaled: bool, faction: str, flight_dest: int | None = None, note: str | None = None) -> None:
            if from_id == to_id:
                return
            ekey = (from_id, to_id, mode)
            if ekey in edge_dedup:
                return
            edge_dedup.add(ekey)
            obj: dict[str, Any] = {
                "fromId": from_id,
                "toId": to_id,
                "mode": mode,
                "seconds": max(0.5, round(sec, 1)),
                "groundScaled": ground_scaled,
                "faction": faction,
            }
            if flight_dest is not None:
                obj["flightDestZoneId"] = flight_dest
            if note:
                obj["note"] = note
            self.edges.append(obj)

        # Intra-zone terrain shortest path edges per zone
        # Save per-node 80x80 dmap per zone temporarily for zoneGrids & questAnchors!
        for zid in sorted(self.cal.zones):
            zt = self.zdata[zid]
            specs = zone_nodes_specs[zid]
            dmaps: dict[int, dict[tuple[int, int], float]] = {}
            for spec in specs:
                dmaps[spec["id"]] = zt.dijkstra_from(spec["_gx"], spec["_gy"])

            # Group by componentId
            by_comp: dict[int, list[dict[str, Any]]] = {}
            for spec in specs:
                by_comp.setdefault(spec["componentId"], []).append(spec)

            for cid, cspecs in by_comp.items():
                if len(cspecs) <= 1:
                    continue
                # Compute pairwise true terrain walk times
                pair_sec: dict[tuple[int, int], float] = {}
                for i in range(len(cspecs)):
                    u = cspecs[i]
                    umap = dmaps[u["id"]]
                    for j in range(i + 1, len(cspecs)):
                        v = cspecs[j]
                        d = umap.get((v["_gx"], v["_gy"]))
                        if d is not None:
                            pair_sec[(u["id"], v["id"])] = d
                            pair_sec[(v["id"], u["id"])] = d

                # Minimum Spanning Tree (guaranteed 100% component connectivity) + up to k=5 nearest non-redundant neighbors
                parent = {s["id"]: s["id"] for s in cspecs}

                def find(x: int) -> int:
                    while parent[x] != x:
                        parent[x] = parent[parent[x]]
                        x = parent[x]
                    return x

                all_pairs = sorted(
                    ((d, min(u, v), max(u, v)) for (u, v), d in pair_sec.items() if u < v)
                )
                mst_edges: set[tuple[int, int]] = set()
                deg: dict[int, int] = {s["id"]: 0 for s in cspecs}
                for d, u, v in all_pairs:
                    ru, rv = find(u), find(v)
                    if ru != rv:
                        parent[ru] = rv
                        mst_edges.add((u, v))
                        deg[u] += 1
                        deg[v] += 1
                        emit_edge(u, v, "walk", d, True, "Both")
                        emit_edge(v, u, "walk", d, True, "Both")

                # Add sparse local cross-links (Relative-Neighborhood / <= 3 extra links, deg <= 5) for tight shortcuts
                for u in cspecs:
                    uid = u["id"]
                    nbrs = sorted(
                        ((pair_sec[(uid, v["id"])], v["id"]) for v in cspecs if v["id"] != uid and (uid, v["id"]) in pair_sec)
                    )
                    kept = 0
                    for d_uv, vid in nbrs:
                        if kept >= 3 or deg[uid] >= 5:
                            break
                        a, b = min(uid, vid), max(uid, vid)
                        if (a, b) in mst_edges:
                            kept += 1
                            continue
                        if deg[vid] >= 5:
                            continue
                        # Relative-Neighborhood / near-collinear prune: skip if existing w satisfies duw + dwv <= 1.14 * d_uv
                        shadowed = False
                        for w in cspecs:
                            wid = w["id"]
                            if wid in (uid, vid):
                                continue
                            duw = pair_sec.get((uid, wid))
                            dwv = pair_sec.get((wid, vid))
                            if duw is not None and dwv is not None and (duw + dwv) <= 1.14 * d_uv:
                                shadowed = True
                                break
                        if not shadowed:
                            emit_edge(uid, vid, "walk", d_uv, True, "Both")
                            emit_edge(vid, uid, "walk", d_uv, True, "Both")
                            deg[uid] += 1
                            deg[vid] += 1
                            kept += 1

            # Build 20x20 condensed zoneGrid for zid cleanly using dmaps!
            cells_out: dict[str, dict[str, Any]] = {}
            for cy in range(GRID_COARSE):
                for cx in range(GRID_COARSE):
                    comp_votes: dict[int, int] = {}
                    t_votes = {1: 0, 2: 0, 3: 0}
                    sub_pts: dict[int, list[tuple[int, int]]] = {}
                    for dy in range(4):
                        for dx in range(4):
                            gy, gx = cy * 4 + dy, cx * 4 + dx
                            t = zt.terrain80[gy][gx]
                            if t > 0:
                                cid = zt.comp80[gy][gx]
                                comp_votes[cid] = comp_votes.get(cid, 0) + 1
                                t_votes[t] += 1
                                sub_pts.setdefault(cid, []).append((gx, gy))
                    if not comp_votes:
                        continue
                    dom_cid = max(comp_votes, key=lambda c: (comp_votes[c], zt.comp_sizes.get(c, 0)))
                    dom_pts = sub_pts[dom_cid]
                    # Representative subpixel closest to sub-cell centroid inside dom_cid
                    ax = sum(p[0] for p in dom_pts) / len(dom_pts)
                    ay = sum(p[1] for p in dom_pts) / len(dom_pts)
                    rep_gx, rep_gy = min(dom_pts, key=lambda p: math.hypot(p[0] - ax, p[1] - ay))
                    # Rank reachable nodes strictly in dom_cid
                    cands_a = []
                    for spec in by_comp.get(dom_cid, []):
                        d = dmaps[spec["id"]].get((rep_gx, rep_gy))
                        if d is not None:
                            cands_a.append((round(max(0.5, d), 1), spec["id"]))
                    cands_a.sort()
                    if not cands_a:
                        continue
                    terrain_str = "road" if t_votes[3] >= 2 else ("swim" if t_votes[2] > t_votes[1] + t_votes[3] else "walkable")
                    cell_idx = cy * GRID_COARSE + cx + 1
                    cells_out[str(cell_idx)] = {
                        "terrain": terrain_str,
                        "componentId": int(dom_cid),
                        "anchors": [[nid, sec] for sec, nid in cands_a[:3]],
                    }

            self.zone_grids[str(zid)] = {
                "width": GRID_COARSE,
                "height": GRID_COARSE,
                "worldBounds": [int(zt.bounds[0]), round(zt.bounds[1], 2), round(zt.bounds[2], 2), round(zt.bounds[3], 2), round(zt.bounds[4], 2)],
                "cells": cells_out,
            }

            # Compute questAnchors for quests anchored in zone zid!
            self._populate_zone_quest_anchors(zid, zt, by_comp, dmaps)

        # Emit cross-zone gates & transports
        for s1, s2, mode, sec, g_scaled, fac, desc, f_dest in gate_pairs_edges + cross_edges_pending:
            emit_edge(int(s1["id"]), int(s2["id"]), mode, sec, g_scaled, fac, f_dest, desc)

        # Emit all OpenMap FlightData point-to-point flight edges
        for src_fd, dst_fd, yds, sec, _wps in self.cal.fd_paths:
            if src_fd not in fd_to_spec or dst_fd not in fd_to_spec:
                continue
            z1, s1 = fd_to_spec[src_fd]
            z2, s2 = fd_to_spec[dst_fd]
            f1 = s1["faction"]
            f2 = s2["faction"]
            if f1 == "Both":
                fac = f2
            elif f2 == "Both":
                fac = f1
            elif f1 == f2:
                fac = f1
            else:
                continue
            f_sec = float(sec) if sec else 0.0
            if f_sec <= 0.0:
                if float(yds) > 0.0:
                    f_sec = float(yds) / 30.0
                else:
                    n1, n2 = self.cal.fd_nodes[src_fd], self.cal.fd_nodes[dst_fd]
                    wdist = math.hypot(float(n1[2]) - float(n2[2]), float(n1[3]) - float(n2[3])) if int(n1[1]) == int(n2[1]) else 2400.0
                    f_sec = max(20.0, 1.12 * wdist / 30.0)
            emit_edge(
                int(s1["id"]),
                int(s2["id"]),
                "flight",
                f_sec,
                False,
                fac,
                z2,
                f"Flight: {s1['name']} -> {s2['name']} ({yds} yds)",
            )

        out = {
            "meta": {
                "version": 1,
                "unmountedYdsPerSec": UNMOUNTED_YDS_PER_SEC,
                "mountedYdsPerSec": MOUNTED_YDS_PER_SEC,
                "gridWidth": GRID_COARSE,
                "gridHeight": GRID_COARSE,
                "nodeCount": len(self.nodes),
                "edgeCount": len(self.edges),
            },
            "nodes": self.nodes,
            "edges": self.edges,
            "zoneGrids": self.zone_grids,
            "questAnchors": self.quest_anchors,
            "hearthNodes": self.hearth_nodes,
        }
        return out

    def _populate_zone_quest_anchors(
        self,
        zid: int,
        zt: ZoneTerrainData,
        by_comp: dict[int, list[dict[str, Any]]],
        dmaps: dict[int, dict[tuple[int, int], float]],
    ) -> None:
        def anchors_for_coord(c: dict[str, Any] | None) -> list[list[Any]] | None:
            if not c or c.get("zoneId") is None:
                return None
            czid = int(c["zoneId"])
            if czid != zid:
                return None
            zx, zy = float(c["x"]), float(c["y"])
            gx, gy, comp = zt.snap_to_walkable(zx, zy)
            if comp == 0 or not by_comp.get(comp):
                # Snap to nearest component that HAS anchored nodes!
                valid_comps = {c_id for c_id, lst in by_comp.items() if lst}
                best = None
                for vg_y in range(GRID_FINE):
                    for vg_x in range(GRID_FINE):
                        if zt.comp80[vg_y][vg_x] in valid_comps:
                            d = math.hypot((vg_x - gx) * zt.dx_yards, (vg_y - gy) * zt.dy_yards)
                            if best is None or d < best[0]:
                                best = (d, vg_x, vg_y, zt.comp80[vg_y][vg_x])
                if best is None:
                    return None
                _, gx, gy, comp = best
            # Direct snap distance to snapped pixel + terrain walk distance inside comp
            szx, szy = zt.to_zone_coords(gx, gy)
            _, wx_q, wy_q = zone_to_world(zt.bounds, zx, zy)
            _, wx_s, wy_s = zone_to_world(zt.bounds, szx, szy)
            snap_sec = math.hypot(wx_q - wx_s, wy_q - wy_s) / UNMOUNTED_YDS_PER_SEC
            cands = []
            for spec in by_comp[comp]:
                d = dmaps[spec["id"]].get((gx, gy))
                if d is not None:
                    # Include exact sub-cell offset to node if in same 80x80 cell
                    if (gx, gy) == (spec["_gx"], spec["_gy"]):
                        _, wx_n, wy_n = zone_to_world(zt.bounds, spec["x"], spec["y"])
                        tot = math.hypot(wx_q - wx_n, wy_q - wy_n) / UNMOUNTED_YDS_PER_SEC
                    else:
                        tot = snap_sec + d
                    cands.append((round(max(0.2, tot), 1), spec["id"]))
            cands.sort()
            return [[nid, sec] for sec, nid in cands[:3]]

        for qid_str, q in self.quests.items():
            entry = self.quest_anchors.get(qid_str)
            sc = q.get("starterCoords")
            tc = q.get("turninCoords")
            ocs = q.get("objectiveCoords") or []

            sa = anchors_for_coord(sc)
            ta = anchors_for_coord(tc)
            # Pick objective centroid in primary zone (prefer quest zoneId if matching, else mode of ocs)
            oa = None
            if ocs:
                zone_ocs = [o for o in ocs if o.get("zoneId") == zid]
                # Only compute o in the zone matching primary objective cluster or quest zone
                if zone_ocs and (zid == q.get("zoneId") or all(o.get("zoneId") == zid for o in ocs) or not any(o.get("zoneId") == q.get("zoneId") for o in ocs)):
                    # Pick objective coord centroid (if dispersed across wide cluster, snap nearest representative cluster point)
                    avg_x = sum(float(o["x"]) for o in zone_ocs) / len(zone_ocs)
                    avg_y = sum(float(o["y"]) for o in zone_ocs) / len(zone_ocs)
                    # If centroid lands in blocked mountain far from actual objectives, pick cluster medoid
                    med = min(zone_ocs, key=lambda o: math.hypot(float(o["x"]) - avg_x, float(o["y"]) - avg_y))
                    cgx, cgy = zt.to_fine(avg_x, avg_y)
                    target = {"zoneId": zid, "x": avg_x, "y": avg_y} if zt.terrain80[cgy][cgx] > 0 else med
                    oa = anchors_for_coord(target)

            if sa is not None or oa is not None or ta is not None:
                if entry is None:
                    entry = {"s": [], "o": [], "t": None}
                    self.quest_anchors[qid_str] = entry
                if sa is not None:
                    entry["s"] = sa
                if oa is not None:
                    entry["o"] = oa
                if ta is not None:
                    same_spot = (
                        sc
                        and tc
                        and int(sc.get("zoneId", -1)) == int(tc.get("zoneId", -2))
                        and math.hypot(float(sc["x"]) - float(tc["x"]), float(sc["y"]) - float(tc["y"])) < 0.05
                    )
                    if not same_spot:
                        entry["t"] = ta


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate data/nav_graph.json for ForeverQuestPath")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data/nav_graph.json")
    args = parser.parse_args()

    builder = NavGraphBuilder(PROJECT_ROOT)
    nav_data = builder.build()

    out_path: Path = args.out
    out_path.write_text(json.dumps(nav_data, separators=(",", ":")))
    size_kb = out_path.stat().st_size / 1024.0
    print(
        f"Wrote {out_path} ({size_kb:.1f} KB): "
        f"nodes={nav_data['meta']['nodeCount']}, edges={nav_data['meta']['edgeCount']}, "
        f"zones={len(nav_data['zoneGrids'])}, questsAnchored={len(nav_data['questAnchors'])}"
    )


if __name__ == "__main__":
    main()
