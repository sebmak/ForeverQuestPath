# ForeverQuestPath Simulation & Route Optimization Report

**Primary Target Character:** Level 1 Alliance Gnome Rogue starting in **Dun Morogh** (progression through Level 60, max quest level offset `+2`)
**Dataset Loaded:** 5914 total quests (4099 leveling: 2684 Alliance-eligible, 2373 Horde-eligible), 28 dungeons, 50 zone hubs, 407 travel edges, 10 races, 9 classes.

## 1. Comparative Algorithm Simulation (`balanced` profile)

| Strategy | End Level | Total Time (h) | Travel Share (%) | Avg XP / hr | Quests Done | Dungeons Run | Gold Earned | Bags | Class Upgrades | Prereq / Level Violations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `naive_greedy` | 51 (99%) | 129.96 | 85.4% | 20,057 | 900 | 0 | 71g 92s 07c | 8 | 32 | 0 / 0 |
| `hub_lookahead` | 60 (100%) | 115.36 | 72.9% | 35,410 | 1158 | 22 | 116g 45s 67c | 6 | 50 | 0 / 0 |
| `beam_optimizer` | 60 (100%) | 118.86 | 73.0% | 34,400 | 1128 | 24 | 118g 84s 67c | 7 | 49 | 0 / 0 |

## 2. Optimization Profile Comparison (`beam_optimizer`)

| Profile | Weights (XP / Travel / Dungeon / Reward / Skin) | End Level | Time (h) | Travel (%) | XP / hr | Dungeons Scheduled | Gold | Bags (Max Slots) | Class Upgrades | LW / Beast Quests |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `balanced` (Balanced) | 42/23/18/7/10 | 60 | 118.86 | 73.0% | 34,400 | hall-of-thanes, ruins-of-lordaeron, the-deadmines, the-stockade, blackfathom-deeps, excavation-site-wetlands, gnomeregan, city-of-dalaran, razorfen-kraul, scarlet-monastery, the-drowned-city, krol-dok-stronghold, razorfen-downs, zul-farrak, uldaman, maraudon, alcaz-prison, the-temple-of-atal-hakkar, blackrock-depths, blackmaw-hold, the-shapers-terrace, blackrock-spire, stratholme, scholomance | 118g 84s 67c | 7 (12s) | 49 | 12 / 134 |
| `skinning_lw` (Skinning & Leatherworking) | 30/18/12/10/30 | 60 | 127.14 | 75.4% | 32,153 | hall-of-thanes, wailing-caverns, the-deadmines, the-stockade, blackfathom-deeps, excavation-site-wetlands, gnomeregan, city-of-dalaran, razorfen-kraul, the-drowned-city, razorfen-downs, krol-dok-stronghold, uldaman, zul-farrak, maraudon, alcaz-prison, the-temple-of-atal-hakkar, blackrock-depths, blackmaw-hold, the-shapers-terrace, dire-maul, blackrock-spire | 106g 63s 97c | 8 (10s) | 50 | 13 / 145 |
| `dungeon_focus` (Dungeon Focus) | 28/15/43/9/5 | 60 | 119.92 | 72.6% | 34,107 | hall-of-thanes, wailing-caverns, ruins-of-lordaeron, the-deadmines, the-stockade, blackfathom-deeps, excavation-site-wetlands, gnomeregan, razorfen-kraul, city-of-dalaran, scarlet-monastery, the-drowned-city, krol-dok-stronghold, razorfen-downs, uldaman, zul-farrak, maraudon, the-temple-of-atal-hakkar, alcaz-prison, blackrock-depths, blackmaw-hold, blackrock-spire, the-shapers-terrace, dire-maul, stratholme, scholomance | 127g 91s 57c | 8 (12s) | 48 | 8 / 136 |
| `treasure_bags` (Gold & Bags) | 22/14/8/46/10 | 60 | 128.00 | 74.6% | 31,936 | hall-of-thanes, wailing-caverns, the-deadmines, the-stockade, blackfathom-deeps, excavation-site-wetlands, gnomeregan, razorfen-kraul, the-drowned-city, razorfen-downs, zul-farrak, krol-dok-stronghold, uldaman, maraudon, alcaz-prison, blackrock-depths, the-temple-of-atal-hakkar, blackmaw-hold, the-shapers-terrace, stratholme, blackrock-spire, scholomance | 124g 45s 42c | 12 (12s) | 65 | 9 / 144 |
| `fast_xp` (Fast XP & Low Travel) | 55/35/5/5/0 | 60 | 116.69 | 73.4% | 35,040 | hall-of-thanes, the-deadmines, the-stockade, blackfathom-deeps, excavation-site-wetlands, gnomeregan, razorfen-kraul, the-drowned-city, krol-dok-stronghold, razorfen-downs, zul-farrak, uldaman, maraudon, alcaz-prison, blackrock-depths, the-temple-of-atal-hakkar, blackmaw-hold, the-shapers-terrace, blackrock-spire, scholomance, stratholme | 114g 14s 57c | 6 (10s) | 51 | 3 / 133 |

## 3. Multi-Race & Multi-Class Simulation Matrix (`balanced` profile)

| Character | Start -> End | Time (h) | Travel (%) | XP / hr | Quests | Dungeons | Class Upgrades | Violations (Prereq / MinLv / Red / Elig) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `Alliance_Gnome_ROGUE_1` (Alliance Gnome ROGUE) | Lv 1 Dun Morogh -> 60 | 118.86 | 73.0% | 34,400 | 1128 | 24 | 49 | 0 / 0 / 0 / 0 |
| `Alliance_Gnome_ROGUE_24` (Alliance Gnome ROGUE) | Lv 24 Wetlands -> 60 | 99.71 | 73.3% | 38,264 | 914 | 21 | 40 | 0 / 0 / 0 / 0 |
| `Alliance_Human_PALADIN_1` (Alliance Human PALADIN) | Lv 1 Elwynn Forest -> 60 | 116.81 | 72.7% | 34,995 | 1127 | 24 | 137 | 0 / 0 / 0 / 0 |
| `Alliance_NightElf_HUNTER_1` (Alliance NightElf HUNTER) | Lv 1 Teldrassil -> 60 | 118.18 | 72.8% | 34,591 | 1122 | 24 | 37 | 0 / 0 / 0 / 0 |
| `Horde_Orc_WARRIOR_1` (Horde Orc WARRIOR) | Lv 1 Durotar -> 60 | 123.23 | 74.1% | 33,188 | 1094 | 20 | 147 | 0 / 0 / 0 / 0 |
| `Horde_Scourge_MAGE_1` (Horde Scourge MAGE) | Lv 1 Tirisfal Glades -> 60 | 127.27 | 74.8% | 32,150 | 1114 | 21 | 45 | 0 / 0 / 0 / 0 |
| `Alliance_HighOrderSkyborne_ROGUE_1` (Alliance HighOrderSkyborne ROGUE) | Lv 1 Zephras Isle -> 60 | 115.41 | 71.9% | 35,433 | 1108 | 24 | 52 | 0 / 0 / 0 / 0 |
| `Horde_WindshaperSkyborne_ROGUE_1` (Horde WindshaperSkyborne ROGUE) | Lv 1 Zephras Isle -> 60 | 119.51 | 73.2% | 34,228 | 1131 | 20 | 58 | 0 / 0 / 0 / 0 |

## 4. Precomputed Starter Zones (`1->24`) & Horde Spine (`20->60`)

### Starting Zones (Level 1-24)

| Route Key | Zone | Faction | Representative | End Lv | Time (h) | Steps | Quests | Violations (P/M) |
|---|---|---|---|---:|---:|---:|---:|---:|
| `1` | Dun Morogh (1) | Alliance | Gnome ROGUE | 24 | 14.79 | 657 | 225 | 0/0 |
| `12` | Elwynn Forest (12) | Alliance | Human PALADIN | 24 | 18.76 | 720 | 233 | 0/0 |
| `14` | Durotar (14) | Horde | Orc WARRIOR | 24 | 17.95 | 684 | 197 | 0/0 |
| `85` | Tirisfal Glades (85) | Horde | Scourge MAGE | 24 | 19.02 | 704 | 203 | 0/0 |
| `141` | Teldrassil (141) | Alliance | NightElf HUNTER | 24 | 15.97 | 651 | 211 | 0/0 |
| `215` | Mulgore (215) | Horde | Tauren DRUID | 24 | 17.55 | 668 | 193 | 0/0 |
| `16593_Alliance` | Zephras Isle (16593) | Alliance | HighOrderSkyborne MAGE | 24 | 15.78 | 673 | 220 | 0/0 |
| `16593_Horde` | Zephras Isle (16593) | Horde | WindshaperSkyborne SHAMAN | 24 | 19.29 | 742 | 216 | 0/0 |

### Horde Spine Profiles (`20->60`)

| Profile | End Lv | Time (h) | Travel (%) | XP / hr | Dungeons | Class Upgrades | Violations (P/M) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `balanced` (Balanced) | 60 | 107.83 | 73.5% | 36,342 | 19 | 136 | 0/0 |
| `skinning_lw` (Skinning & Leatherworking) | 60 | 109.99 | 74.3% | 35,635 | 20 | 136 | 0/0 |
| `dungeon_focus` (Dungeon Focus) | 60 | 109.51 | 72.9% | 35,880 | 24 | 133 | 0/0 |
| `treasure_bags` (Gold & Bags) | 60 | 119.13 | 74.5% | 32,887 | 22 | 158 | 0/0 |
| `fast_xp` (Fast XP & Low Travel) | 60 | 101.53 | 72.5% | 38,633 | 18 | 135 | 0/0 |

## 5. Level 24 Wetlands Regression Benchmark (`Alliance Gnome Rogue`, `24->60`)

| Strategy / Profile | End Lv | Time (h) | Travel (%) | XP / hr | Dungeons | LW / Beast | Violations (P/M) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `naive_greedy` | 58 | 139.27 | 85.6% | 25,848 | 0 | 9/106 | 0/0 |
| `hub_lookahead` | 60 | 100.56 | 73.7% | 37,951 | 19 | 9/104 | 0/0 |
| `beam:balanced` | 60 | 99.71 | 73.3% | 38,264 | 21 | 9/109 | 0/0 |
| `beam:skinning_lw` | 60 | 109.39 | 76.3% | 34,879 | 20 | 10/113 | 0/0 |
| `beam:dungeon_focus` | 60 | 104.85 | 73.4% | 36,443 | 24 | 7/114 | 0/0 |
| `beam:treasure_bags` | 60 | 109.42 | 75.5% | 34,897 | 18 | 7/114 | 0/0 |
| `beam:fast_xp` | 60 | 98.28 | 73.9% | 38,792 | 18 | 3/98 | 0/0 |

## 6. Leveling Milestones (`balanced` route)

| Milestone Level | Elapsed Hours |
|---:|---:|
| Level 5 | 0.57h |
| Level 10 | 2.48h |
| Level 15 | 5.77h |
| Level 20 | 10.48h |
| Level 24 | 15.04h |
| Level 28 | 23.87h |
| Level 30 | 27.07h |
| Level 35 | 37.48h |
| Level 40 | 50.95h |
| Level 45 | 61.88h |
| Level 50 | 79.27h |
| Level 55 | 104.39h |
| Level 60 | 118.86h |

## 7. Opening Steps from Level 1 Dun Morogh (`balanced` route preview, first 40 steps)

| # | Action | Zone | Coords | Step | Lv | XP | Gold | Why |
|---:|---|---|---|---:|---:|---:|---:|---|
| 1 | `ACCEPT` | Dun Morogh | (29.9, 71.2) | Accept: Dwarven Outfitters | 1 | 0 | - | Grouped in 2-quest local loop; +80 quest XP; Skinnable beasts (74/100) |
| 2 | `ACCEPT` | Dun Morogh | (22.6, 71.4) | Accept: The Boar Hunter | 1 | 0 | - | Grouped in 2-quest local loop; +250 quest XP; Skinnable beasts (76/100) |
| 3 | `COMPLETE` | Dun Morogh | (23.3, 78.7) | Complete: Dwarven Outfitters | 1 | 200 | - | Batched clear (~200 mob XP) |
| 4 | `COMPLETE` | Dun Morogh | (32.3, 71.7) | Complete: The Boar Hunter | 2 | 264 | - | Batched clear (~264 mob XP) |
| 5 | `TURN_IN` | Dun Morogh | (29.9, 71.2) | Turn In: Dwarven Outfitters | 2 | 80 | - | Grouped in 2-quest local loop; +80 quest XP; Skinnable beasts (74/100) |
| 6 | `ACCEPT` | Dun Morogh | (29.9, 71.2) | Accept: Coldridge Valley Mail Delivery | 2 | 0 | - | Immediate local follow-up unlocked at turn-in NPC |
| 7 | `ACCEPT` | Dun Morogh | (29.9, 71.2) | Accept: Encrypted Memorandum | 2 | 0 | - | Immediate local follow-up unlocked at turn-in NPC |
| 8 | `TURN_IN` | Dun Morogh | (28.4, 67.5) | Turn In: Encrypted Memorandum | 2 | 40 | - | Grouped in 2-quest local loop; +40 quest XP |
| 9 | `TURN_IN` | Dun Morogh | (22.6, 71.4) | Turn In: The Boar Hunter | 2 | 250 | - | Grouped in 2-quest local loop; +250 quest XP; Skinnable beasts (76/100) |
| 10 | `TURN_IN` | Dun Morogh | (22.6, 71.4) | Turn In: Coldridge Valley Mail Delivery | 2 | 190 | - | Grouped in 2-quest local loop; +190 quest XP |
| 11 | `ACCEPT` | Dun Morogh | (22.6, 71.4) | Accept: Coldridge Valley Mail Delivery | 2 | 0 | - | Immediate local follow-up unlocked at turn-in NPC |
| 12 | `TURN_IN` | Dun Morogh | (25.1, 75.7) | Turn In: Coldridge Valley Mail Delivery | 2 | 270 | 0g 00s 07c | Grouped in 2-quest local loop; +270 quest XP |
| 13 | `ACCEPT` | Dun Morogh | (25.1, 75.7) | Accept: The Troll Cave | 2 | 0 | - | Grouped in 2-quest local loop; +360 quest XP |
| 14 | `ACCEPT` | Dun Morogh | (29.7, 71.2) | Accept: A New Threat | 2 | 0 | - | Grouped in 2-quest local loop; +170 quest XP |
| 15 | `COMPLETE` | Dun Morogh | (32.0, 74.1) | Complete: A New Threat | 3 | 220 | - | Batched clear (~220 mob XP) |
| 16 | `COMPLETE` | Dun Morogh | (20.8, 75.9) | Complete: The Troll Cave | 3 | 272 | - | Batched clear (~272 mob XP) |
| 17 | `TURN_IN` | Dun Morogh | (25.1, 75.7) | Turn In: The Troll Cave | 3 | 360 | - | Grouped in 2-quest local loop; +360 quest XP |
| 18 | `TURN_IN` | Dun Morogh | (29.7, 71.2) | Turn In: A New Threat | 3 | 170 | - | Grouped in 2-quest local loop; +170 quest XP |
| 19 | `ACCEPT` | Dun Morogh | (28.6, 67.6) | Accept: A Refugee's Quandary | 3 | 0 | - | Grouped in 3-quest local loop; +250 quest XP |
| 20 | `ACCEPT` | Dun Morogh | (33.9, 72.2) | Accept: Supplies to Tannok | 3 | 0 | - | Grouped in 3-quest local loop; +110 quest XP |
| 21 | `ACCEPT` | Dun Morogh | (25.1, 75.7) | Accept: The Stolen Journal | 3 | 0 | - | Grouped in 3-quest local loop; +550 quest XP |
| 22 | `COMPLETE` | Dun Morogh | (23.3, 78.5) | Complete: A Refugee's Quandary | 3 | 240 | - | Batched clear (~240 mob XP) |
| 23 | `COMPLETE` | Dun Morogh | (30.5, 80.2) | Complete: The Stolen Journal | 4 | 308 | - | Batched clear (~308 mob XP) |
| 24 | `TURN_IN` | Dun Morogh | (25.1, 75.7) | Turn In: The Stolen Journal | 4 | 550 | - | Grouped in 3-quest local loop; +550 quest XP |
| 25 | `ACCEPT` | Dun Morogh | (25.1, 75.7) | Accept: Senir's Observations | 4 | 0 | - | Immediate local follow-up unlocked at turn-in NPC |
| 26 | `TURN_IN` | Dun Morogh | (28.6, 67.6) | Turn In: A Refugee's Quandary | 4 | 250 | - | Grouped in 3-quest local loop; +250 quest XP |
| 27 | `TURN_IN` | Dun Morogh | (33.5, 71.8) | Turn In: Senir's Observations | 4 | 340 | - | Grouped in 3-quest local loop; +340 quest XP |
| 28 | `TRAVEL` | Dun Morogh | (47.2, 52.2) | Travel to Dun Morogh | 4 | 0 | - | Turn in batch at Dun Morogh |
| 29 | `TURN_IN` | Dun Morogh | (47.2, 52.2) | Turn In: Supplies to Tannok | 4 | 110 | - | Grouped in 3-quest local loop; +110 quest XP |
| 30 | `ACCEPT` | Dun Morogh | (46.0, 51.7) | Accept: Tools for Steelgrill | 4 | 0 | - | Grouped in 6-quest local loop; +110 quest XP |
| 31 | `ACCEPT` | Dun Morogh | (30.2, 45.5) | Accept: Bitter Rivals | 4 | 0 | - | Grouped in 6-quest local loop; +140 quest XP |
| 32 | `ACCEPT` | Dun Morogh | (33.5, 71.8) | Accept: Senir's Observations | 4 | 0 | - | Grouped in 6-quest local loop; +340 quest XP |
| 33 | `ACCEPT` | Dun Morogh | (33.5, 71.8) | Accept: The Adventurer | 4 | 0 | - | Grouped in 6-quest local loop; +410 quest XP |
| 34 | `ACCEPT` | Dun Morogh | (28.7, 67.5) | Accept: Grund and Gozwin | 4 | 0 | - | Grouped in 6-quest local loop; +410 quest XP; 6-slot bag |
| 35 | `ACCEPT` | Dun Morogh | (25.0, 76.0) | Accept: Scalding Mornbrew Delivery | 4 | 0 | - | Grouped in 6-quest local loop; +230 quest XP |
| 36 | `COMPLETE` | Dun Morogh | (27.3, 62.9) | Complete: Grund and Gozwin | 4 | 328 | - | Clear area (~328 mob XP) |
| 37 | `TURN_IN` | Dun Morogh | (28.8, 66.4) | Turn In: Scalding Mornbrew Delivery | 4 | 230 | - | Grouped in 6-quest local loop; +230 quest XP |
| 38 | `ACCEPT` | Dun Morogh | (28.8, 66.4) | Accept: Bring Back the Mug | 4 | 0 | - | Immediate local follow-up unlocked at turn-in NPC |
| 39 | `TURN_IN` | Dun Morogh | (28.7, 67.5) | Turn In: Grund and Gozwin | 5 | 410 | - | Grouped in 6-quest local loop; +410 quest XP; 6-slot bag |
| 40 | `TURN_IN` | Dun Morogh | (25.0, 76.0) | Turn In: Bring Back the Mug | 5 | 450 | - | Grouped in 6-quest local loop; +450 quest XP |

## 8. Scheduled Dungeon Windows (`dungeon_focus` vs `balanced`)

### Profile `balanced`
- **Step 299 — Dungeon Run: The Hall of Thanes** at Level 14 in Ironforge (27.7, 47.9): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 13-18 window (+5,422 kill XP + boss drops)
- **Step 524 — Dungeon Run: Ruins of Lordaeron** at Level 20 in Undercity (72.5, 11.4): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 15-20 window (+6,502 kill XP + boss drops)
- **Step 534 — Dungeon Run: The Deadmines** at Level 21 in Westfall (42.5, 71.7): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 18-23 window (+5,051 kill XP + boss drops)
- **Step 672 — Dungeon Run: The Stockade** at Level 24 in Stormwind City (49.3, 68.4): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 22-30 window (+8,336 kill XP + boss drops)
- **Step 813 — Dungeon Run: Blackfathom Deeps** at Level 26 in Ashenvale (14.6, 15.3): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 24-32 window (+8,057 kill XP + boss drops)
- **Step 972 — Dungeon Run: Excavation Site: Wetlands** at Level 29 in Wetlands (47.8, 56.2): 5 quest(s), Batched 5 dungeon quest(s) in optimal Lv 24-29 window (+9,132 kill XP + boss drops)
- **Step 1011 — Dungeon Run: Gnomeregan** at Level 30 in Dun Morogh (24.5, 39.8): 6 quest(s), Batched 6 dungeon quest(s) in optimal Lv 29-38 window (+9,779 kill XP + boss drops)
- **Step 1096 — Dungeon Run: City of Dalaran** at Level 31 in Alterac Mountains (8.5, 59.3): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 28-33 window (+9,098 kill XP + boss drops)
- **Step 1147 — Dungeon Run: Razorfen Kraul** at Level 33 in The Barrens (42.3, 90.2): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 30-40 window (+7,502 kill XP + boss drops)
- **Step 1364 — Dungeon Run: Scarlet Monastery** at Level 36 in Tirisfal Glades (85.0, 32.0): 2 quest(s), Batched 2 dungeon quest(s) in optimal Lv 28-45 window (+8,508 kill XP + boss drops)
- **Step 1564 — Dungeon Run: The Drowned City** at Level 38 in Stranglethorn Vale (21.3, 27.8): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 30-40 window (+8,995 kill XP + boss drops)
- **Step 1853 — Dungeon Run: Krol'dok Stronghold** at Level 42 in Riverglades (39.5, 39.1): 2 quest(s), Batched 2 dungeon quest(s) in optimal Lv 37-45 window (+9,440 kill XP + boss drops)
- **Step 1947 — Dungeon Run: Razorfen Downs** at Level 43 in The Barrens (49.3, 93.8): 2 quest(s), Batched 2 dungeon quest(s) in optimal Lv 40-50 window (+9,508 kill XP + boss drops)
- **Step 2063 — Dungeon Run: Zul'Farrak** at Level 45 in Tanaris (38.7, 20.1): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 44-54 window (+13,069 kill XP + boss drops)
- **Step 2083 — Dungeon Run: Uldaman** at Level 45 in Badlands (43.5, 13.7): 9 quest(s), Batched 9 dungeon quest(s) in optimal Lv 42-52 window (+13,824 kill XP + boss drops)
- **Step 2233 — Dungeon Run: Maraudon** at Level 47 in Desolace (29.2, 62.6): 6 quest(s), Batched 6 dungeon quest(s) in optimal Lv 46-55 window (+15,109 kill XP + boss drops)
- **Step 2698 — Dungeon Run: Alcaz Prison** at Level 51 in Dustwallow Marsh (75.3, 14.1): 1 quest(s), Batched 1 dungeon quest(s) in optimal Lv 48-53 window (+9,444 kill XP + boss drops)
- **Step 2821 — Dungeon Run: The Temple of Atal'Hakkar** at Level 52 in Swamp of Sorrows (70.0, 53.7): 2 quest(s), Batched 2 dungeon quest(s) in optimal Lv 50-60 window (+12,348 kill XP + boss drops)
- **Step 3001 — Dungeon Run: Blackrock Depths** at Level 53 in Searing Gorge (34.8, 85.3): 5 quest(s), Batched 5 dungeon quest(s) in optimal Lv 52-60 window (+14,866 kill XP + boss drops)
- **Step 3485 — Dungeon Run: Blackmaw Hold** at Level 57 in Azshara (38.4, 32.6): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 55-60 window (+14,552 kill XP + boss drops)
- **Step 3492 — Dungeon Run: The Shaper's Terrace** at Level 58 in Un'Goro Crater (50.0, 12.0): 1 quest(s), Batched 1 dungeon quest(s) in optimal Lv 58-60 window (+12,053 kill XP + boss drops)
- **Step 3576 — Dungeon Run: Blackrock Spire** at Level 59 in Burning Steppes (29.0, 38.0): 10 quest(s), Batched 10 dungeon quest(s) in optimal Lv 55-60 window (+22,319 kill XP + boss drops)
- **Step 3598 — Dungeon Run: Stratholme** at Level 59 in Eastern Plaguelands (26.6, 8.9): 6 quest(s), Batched 6 dungeon quest(s) in optimal Lv 58-60 window (+17,455 kill XP + boss drops)
- **Step 3613 — Dungeon Run: Scholomance** at Level 59 in Western Plaguelands (69.6, 73.1): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 58-60 window (+14,903 kill XP + boss drops)

### Profile `dungeon_focus`
- **Step 236 — Dungeon Run: The Hall of Thanes** at Level 14 in Ironforge (27.7, 47.9): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 13-18 window (+5,664 kill XP + boss drops)
- **Step 365 — Dungeon Run: Wailing Caverns** at Level 18 in The Barrens (46.0, 35.8): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 15-25 window (+6,419 kill XP + boss drops)
- **Step 471 — Dungeon Run: Ruins of Lordaeron** at Level 20 in Undercity (72.5, 11.4): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 15-20 window (+6,502 kill XP + boss drops)
- **Step 481 — Dungeon Run: The Deadmines** at Level 21 in Westfall (42.5, 71.7): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 18-23 window (+5,051 kill XP + boss drops)
- **Step 595 — Dungeon Run: The Stockade** at Level 24 in Stormwind City (49.3, 68.4): 1 quest(s), Batched 1 dungeon quest(s) in optimal Lv 22-30 window (+6,697 kill XP + boss drops)
- **Step 606 — Dungeon Run: Blackfathom Deeps** at Level 24 in Ashenvale (14.6, 15.3): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 24-32 window (+8,951 kill XP + boss drops)
- **Step 794 — Dungeon Run: Excavation Site: Wetlands** at Level 27 in Wetlands (47.8, 56.2): 1 quest(s), Batched 1 dungeon quest(s) in optimal Lv 24-29 window (+6,085 kill XP + boss drops)
- **Step 918 — Dungeon Run: Gnomeregan** at Level 29 in Dun Morogh (24.5, 39.8): 6 quest(s), Batched 6 dungeon quest(s) in optimal Lv 29-38 window (+9,843 kill XP + boss drops)
- **Step 981 — Dungeon Run: Razorfen Kraul** at Level 30 in The Barrens (42.3, 90.2): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 30-40 window (+8,944 kill XP + boss drops)
- **Step 1030 — Dungeon Run: City of Dalaran** at Level 31 in Alterac Mountains (8.5, 59.3): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 28-33 window (+9,098 kill XP + boss drops)
- **Step 1379 — Dungeon Run: Scarlet Monastery** at Level 36 in Tirisfal Glades (85.0, 32.0): 2 quest(s), Batched 2 dungeon quest(s) in optimal Lv 28-45 window (+8,508 kill XP + boss drops)
- **Step 1523 — Dungeon Run: The Drowned City** at Level 38 in Stranglethorn Vale (21.3, 27.8): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 30-40 window (+8,995 kill XP + boss drops)
- **Step 1575 — Dungeon Run: Krol'dok Stronghold** at Level 39 in Riverglades (39.5, 39.1): 1 quest(s), Batched 1 dungeon quest(s) in optimal Lv 37-45 window (+9,771 kill XP + boss drops)
- **Step 1702 — Dungeon Run: Razorfen Downs** at Level 40 in The Barrens (49.3, 93.8): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 40-50 window (+13,023 kill XP + boss drops)
- **Step 1931 — Dungeon Run: Uldaman** at Level 43 in Badlands (43.5, 13.7): 8 quest(s), Batched 8 dungeon quest(s) in optimal Lv 42-52 window (+14,577 kill XP + boss drops)
- **Step 2066 — Dungeon Run: Zul'Farrak** at Level 45 in Tanaris (38.7, 20.1): 5 quest(s), Batched 5 dungeon quest(s) in optimal Lv 44-54 window (+14,023 kill XP + boss drops)
- **Step 2143 — Dungeon Run: Maraudon** at Level 46 in Desolace (29.2, 62.6): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 46-55 window (+13,664 kill XP + boss drops)
- **Step 2682 — Dungeon Run: The Temple of Atal'Hakkar** at Level 51 in Swamp of Sorrows (70.0, 53.7): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 50-60 window (+16,525 kill XP + boss drops)
- **Step 2694 — Dungeon Run: Alcaz Prison** at Level 51 in Dustwallow Marsh (75.3, 14.1): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 48-53 window (+11,694 kill XP + boss drops)
- **Step 2790 — Dungeon Run: Blackrock Depths** at Level 52 in Searing Gorge (34.8, 85.3): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 52-60 window (+14,357 kill XP + boss drops)
- **Step 3169 — Dungeon Run: Blackmaw Hold** at Level 55 in Azshara (38.4, 32.6): 2 quest(s), Batched 2 dungeon quest(s) in optimal Lv 55-60 window (+14,814 kill XP + boss drops)
- **Step 3408 — Dungeon Run: Blackrock Spire** at Level 58 in Burning Steppes (29.0, 38.0): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 55-60 window (+15,512 kill XP + boss drops)
- **Step 3420 — Dungeon Run: The Shaper's Terrace** at Level 58 in Un'Goro Crater (50.0, 12.0): 3 quest(s), Batched 3 dungeon quest(s) in optimal Lv 58-60 window (+15,815 kill XP + boss drops)
- **Step 3480 — Dungeon Run: Dire Maul** at Level 59 in Feralas (59.0, 41.8): 9 quest(s), Batched 9 dungeon quest(s) in optimal Lv 58-60 window (+20,877 kill XP + boss drops)
- **Step 3503 — Dungeon Run: Stratholme** at Level 59 in Eastern Plaguelands (26.6, 8.9): 6 quest(s), Batched 6 dungeon quest(s) in optimal Lv 58-60 window (+17,455 kill XP + boss drops)
- **Step 3518 — Dungeon Run: Scholomance** at Level 59 in Western Plaguelands (69.6, 73.1): 4 quest(s), Batched 4 dungeon quest(s) in optimal Lv 58-60 window (+14,903 kill XP + boss drops)
