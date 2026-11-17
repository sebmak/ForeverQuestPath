<img src=".previews/icon.png" width="76" align="left" alt="ForeverQuestPath Icon" />

# ForeverQuestPath

**ForeverQuestPath** is a WoW Forever (`Interface: 16001`) 1–60 quest routing addon paired with a Python offline dataset pipeline and simulator.

<br clear="left" />

## Features

- **1–60 Multi-Faction Routing:** Supports Alliance, Horde, and Skyborne races/classes across all 1–60 zones and leveling dungeons.
- **5 Scoring Profiles:** `xp_speed`, `class_bis`, `balanced`, `dungeon_focus`, and `skinning_lw` with adjustable 5-axis weights.
- **Terrain & Gate-Aware Navigation:** Precomputed CSR navigation grid with bidirectional Dijkstra pathfinding across terrain barriers, tunnels, hubs, and flight paths.
- **Proactive Chain & Turn-In Optimization:** Expands full quest chains ahead of completion (`[Chain X/Y]`), prioritizes nearby ready turn-ins (`[Ready Turn-In]`), and defers distant turn-ins until aligned.
- **Optimal-Path Available Quest Suggestions:** Continuously discovers eligible unaccepted quests (including unrouted side quests, behind-gap route quests, and quests along your hub/walking corridor) whose pickup, objectives, and turn-in fit the active zone route, surfacing hub and corridor pickups before sending you out to distant objectives.
- **GPS Auto-Reorder & Friend-Meetup Pivots:** Near-realtime intra-zone step reordering and off-route friend meetup chain detection (`/fqp gps`, `/fqp reorder`).
- **Configurable Max Quest Level Offset:** Adjustable `-3` to `+5` quest level ceiling via UI slider or `/fqp maxlevel`.
- **Skip Elite Quests Unless Grouped:** Automatically defers open-world elite quests and their follow-ups while solo (`Skip Elite (Solo)` toggle or `/fqp elite`), unlocking them immediately when you join a party or raid while always keeping completed turn-ins active.
- **Skip Dungeon Quests Until Ready:** Defers quests inside dungeon instances (`Skip Dungeons` toggle or `/fqp skipdungeons` / `/fqp dungeons skip|allow|toggle`) while keeping outdoor unlock quests and completed turn-ins active, and automatically rewinds to all deferred dungeon steps when you're ready to run them together.
- **Automatic In-Dungeon Quest Routing:** When you zone inside any Classic or Forever custom dungeon instance, automatically surfaces that dungeon's ready in-dungeon turn-ins, available inside-dungeon pickups, active objectives, follow-up chains, post-run turn-ins, and shareable dungeon quests as your next steps—even while `Skip Dungeons` or `Skip Elite (Solo)` is enabled—and resumes normal open-world routing when you exit.
- **Quest Hover Instructions & Click-to-Open Quest Log:** Hovering over any quest in the HUD or Window tabs displays full quest instructions, narrative text, live objective progress, NPC coordinates, chain progress, and rewards; Left-Clicking opens the Quest Log to that quest (or pins its quest giver if unaccepted), while Right-Clicking pins the waypoint or jumps to the route step.
- **Anti-Shuffling Route Stability:** Keeps lookahead steps anchored cleanly to your current continent and zone (expanding local frontiers when transitioning zones, simulating Hearthstone as single-use, enforcing cross-zone/cross-continent cohesion, and applying walking target/queue hysteresis) so upcoming steps don't flip across continents mid-travel.
- **Low-CPU Event Gating:** Suppresses redundant routing recalculations while stationary, looting/skinning, or dead/ghost.

---

## Install with WowUp (Recommended)

You can install and auto-update **ForeverQuestPath** for **WoW Forever (`Interface: 16001`)** directly through **WowUp**:

1. Open **WowUp** and select your **WoW Forever** client installation.
2. Go to **Get Addons -> Install from URL**.
3. Paste the GitHub repository URL:
   - https://github.com/sebmak/ForeverQuestPath
4. Click **Import** and then **Install / Re-install** — WowUp reads `release.json`, `ForeverQuestPath-v1.6.2-forever.zip`, and `ForeverQuestPath_Forever.toc` automatically and replaces any `Unknown` manual local folder (which otherwise shows an `"F"` placeholder).

---

## Quick Install (Release ZIP)

You can download a prebuilt release binary directly without cloning the repo:

1. Download the latest archive:
   - Direct asset (`v1.6.2`): [`ForeverQuestPath-v1.6.2-forever.zip`](https://github.com/sebmak/ForeverQuestPath/releases/latest/download/ForeverQuestPath-v1.6.2-forever.zip)
   - All releases: [`github.com/sebmak/ForeverQuestPath/releases/latest`](https://github.com/sebmak/ForeverQuestPath/releases/latest)
2. Extract `ForeverQuestPath-v1.6.2-forever.zip` into your WoW Forever AddOns directory:
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
