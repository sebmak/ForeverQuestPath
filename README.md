# ForeverQuestPath

**ForeverQuestPath** is a WoW Forever (`Interface: 16001`) 1–60 quest routing addon paired with a Python offline dataset pipeline and simulator.

## Features

- **1–60 Multi-Faction Routing:** Supports Alliance, Horde, and Skyborne races/classes across all 1–60 zones and leveling dungeons.
- **5 Scoring Profiles:** `xp_speed`, `class_bis`, `balanced`, `dungeon_focus`, and `skinning_lw` with adjustable 5-axis weights.
- **Terrain & Gate-Aware Navigation:** Precomputed CSR navigation grid with bidirectional Dijkstra pathfinding across terrain barriers, tunnels, hubs, and flight paths.
- **Proactive Chain & Turn-In Optimization:** Expands full quest chains ahead of completion (`[Chain X/Y]`), prioritizes nearby ready turn-ins (`[Ready Turn-In]`), and defers distant turn-ins until aligned.
- **GPS Auto-Reorder & Friend-Meetup Pivots:** Near-realtime intra-zone step reordering and off-route friend meetup chain detection (`/fqp gps`, `/fqp reorder`).
- **Configurable Max Quest Level Offset:** Adjustable `-3` to `+5` quest level ceiling via UI slider or `/fqp maxlevel`.
- **Skip Elite Quests Unless Grouped:** Automatically defers open-world elite quests and their follow-ups while solo (`Skip Elite (Solo)` toggle or `/fqp elite`), unlocking them immediately when you join a party or raid while always keeping completed turn-ins active.
- **Skip Dungeon Quests Until Ready:** Defers quests inside dungeon instances (`Skip Dungeons` toggle or `/fqp skipdungeons` / `/fqp dungeons skip|allow|toggle`) while keeping outdoor unlock quests and completed turn-ins active, and automatically rewinds to all deferred dungeon steps when you're ready to run them together.
- **Quest Hover Instructions & Click-to-Open Quest Log:** Hovering over any quest in the HUD or Window tabs displays full quest instructions, narrative text, live objective progress, NPC coordinates, chain progress, and rewards; Left-Clicking opens the Quest Log to that quest (or pins its quest giver if unaccepted), while Right-Clicking pins the waypoint or jumps to the route step.
- **Low-CPU Event Gating:** Suppresses redundant routing recalculations while stationary, looting/skinning, or dead/ghost.

---

## Quick Install (Release ZIP)

You can download a prebuilt release binary directly without cloning the repo:

1. Download the latest archive:
   - Direct asset (`v1.3.0`): [`ForeverQuestPath-v1.3.0.zip`](https://github.com/sebmak/ForeverQuestPath/releases/latest/download/ForeverQuestPath-v1.3.0.zip)
   - All releases: [`github.com/sebmak/ForeverQuestPath/releases/latest`](https://github.com/sebmak/ForeverQuestPath/releases/latest)
2. Extract `ForeverQuestPath-v1.3.0.zip` into your WoW Forever AddOns directory:
   - **macOS:** `/Applications/World of Warcraft/_forever_/Interface/AddOns/`
   - **Windows:** `C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns\`
3. Confirm the extracted folder structure contains:
   ```text
   <World of Warcraft>/_forever_/Interface/AddOns/ForeverQuestPath/ForeverQuestPath.toc
   ```
4. Launch WoW Forever (or type `/reload` in-game), then open the window with `/fqp` or the minimap button.

---

## Developer Commands

```bash
# Validate .toc + LuaJIT bytecode and sync addon/ into WoW _forever_ AddOns
bash scripts/install_addon.sh

# Build and verify release zip + SHA-256 checksum into dist/
python3 scripts/package_release.py

# Refresh vendored upstream QuestieDB data into scraper/vendor/QuestieDB/
python3 scraper/sync_questiedb.py

# Run full validation, simulator, mock, and LuaJIT addon test suite
python3 -m unittest discover -s tests -v
```
