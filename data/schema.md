# World of Warcraft: Forever (1.60.1) Quest, Dungeon & Travel Dataset Schema

Generated deterministically by `scraper/build_dataset.py` from local WoW Forever add-on stores (`QuestieDB_Forever.toc`, `QuestXP/xpDB-classic.lua`, `QuestXP/xpDB-traces.lua`, `ForeverLevelingBiS/Data/*.lua`, and `OpenMap/Data/Forever/*.lua`), cached Wowhead Forever (`dataEnv=16/4`) NPC creature type metadata (`scraper/cache/npc_creature_types.json`), and live `foreverchanges.pro` Forever quest/dungeon pages plus Classic/Forever XP curve and dungeon unlock metadata across all **10 races**, **9 classes**, and **Levels 1–60**.

## Data Coverage Summary

| Metric | Count |
| --- | ---: |
| Total quests in `data/quests.json` | 5914 |
| Level 1–60 leveling quests (`isLevelingQuest`) | 4099 |
| Level 1–60 Alliance-only leveling quests (`factionMask == 1`) | 1726 |
| Level 1–60 Horde-only leveling quests (`factionMask == 2`) | 1415 |
| Level 1–60 Neutral leveling quests (`factionMask == 3`) | 958 |
| Eligible Alliance Gnome Rogue quests (all levels) | 2178 |
| Eligible Alliance Gnome Rogue quests (level 22–60) | 1672 |
| Eligible Wetlands & Excavation Site quests | 74 |
| Eligible Leatherworking quests (`isLeatherworking`) | 15 |
| Eligible Skinning quests (`isSkinning`) | 5 |
| Eligible Skinnable Beast/Dragonkin quests (`skinningScore > 0`) | 187 |
| Playable Races / Classes exported in `travel_graph.json` | 10 / 9 |
| Dungeons in `data/dungeons.json` | 28 |
| World/city nodes in `data/travel_graph.json` | 50 |
| Travel edges in `data/travel_graph.json` | 407 |
| Missing coords on Wetlands or lvl 1–45 leveling quests | 0 |
| Non-breadcrumb lvl 24–45 eligible quests with 0 XP | 7 |

## File Schemas

### 1. `data/quests.json`
Top-level keys:
- `meta`: `{source, build, generatedAt, totalQuests, levelingQuestCount, levelingAllianceCount, levelingHordeCount, levelingNeutralCount, allianceRogueAllCount, allianceRogue22to60Count}`
- `quests`: map `"<questId>" -> QuestObject`

`QuestObject` fields:
- `questId` (`int`): Quest ID
- `name` (`str`): Quest title
- `zoneId` (`int`): Normalized WoW Forever zone/dungeon ID
- `zoneName` (`str`): Canonical zone/dungeon name
- `minLevel` (`int`): Required minimum character level (missing mid/high-level entries never fall back to `1`; uses Forever/QuestieDB/ForeverLevelingBiS or safe `questLevel - 5` / zone floor)
- `questLevel` (`int`): Quest difficulty/target level
- `xp` (`int`): Verified quest completion XP
- `goldCopper` (`int`): Money reward in copper
- `bagSlots` (`int`): Maximum general bag slot reward (`0` if none; reagent bags appear in `specialRewards` with `kind: "reagent_bag"`)
- `specialRewards` (`list`): `{itemId, name, kind, slots, quality, itemLevel, isChoice, rogueScore, classScores}` (`kind` in `"weapon"|"gear"|"bag"|"mount"`, `classScores` maps all 9 class keys to `0.0..100.0`)
- `rogueScore` (`float`): Max Rogue utility score `0..100` across `specialRewards` (`== classScores["ROGUE"]`)
- `classScores` (`dict[str, float]`): Max normalized `0.0..100.0` utility score across `specialRewards` for each of `WARRIOR`, `PALADIN`, `HUNTER`, `ROGUE`, `PRIEST`, `SHAMAN`, `MAGE`, `WARLOCK`, `DRUID`
- `requiredSkill` (`{"skillId": int, "minSkill": int}|null`): QuestieDB profession requirement (`165`=Leatherworking, `393`=Skinning, etc.)
- `isLeatherworking` (`bool`): True when `requiredSkill.skillId == 165` or Questie sortKey `-182` (Leatherworking)
- `isSkinning` (`bool`): True when `requiredSkill.skillId == 393` (Skinning)
- `lwRewards` (`list`): `{itemId, name, kind, quality}` for Leatherworking patterns/recipes (`kind: "lw_pattern"`) or skinning tools (`"skinning_tool"`)
- `skinningScore` (`float`): `0.0..100.0` skinnable Beast/Dragonkin target synergy from objective NPCs/drops (excludes Spiders/Crabs/non-skinnable beasts)
- `beastTargetCount` (`int`): Estimated skinnable Beast/Dragonkin target kill or skin/hide/scale collect quantity
- `beastTypes` (`list[str]`): Sorted distinct skinnable creature types (`"Beast"`, `"Dragonkin"`)
- `prereqIds` (`list[int]`): Verified prerequisite quest IDs (acyclic DAG, symmetric with `nextQuestIds`, free of cross-faction/cross-race/cross-class leaks)
- `nextQuestIds` (`list[int]`): Follow-up quests unlocked by this quest
- `exclusiveTo` (`list[int]`): Mutually exclusive quest/breadcrumb IDs
- `isBreadcrumb` (`bool`): True if optional breadcrumb leading into another quest
- `questKind` (`str`): `"talk"|"kill"|"collect"|"escort"|"dungeon"|"breadcrumb"`
- `objectiveCount` (`int`): Count of distinct objectives/triggers
- `chainId` (`int|null`), `chainStep` (`int|null`): ForeverLevelingBiS chain identifiers when part of a chain
- `starterNpcId` (`int|null`), `starterKind` (`"npc"|"object"|"item"`), `starterName` (`str|null`), `starterCoords` (`{zoneId, x, y}|null`)
- `turninNpcId` (`int|null`), `turninKind` (`"npc"|"object"`), `turninName` (`str|null`), `turninCoords` (`{zoneId, x, y}|null`)
- `objectiveCoords` (`list[{zoneId, x, y, label}]`)
- `isDungeon` (`bool`), `dungeonId` (`str|null`), `isGroup` (`bool`)
- `factionMask` (`int`: `1`=Alliance, `2`=Horde, `3`=Both), `raceMask` (`int`, supports >31-bit Skyborne bits `2^32`/`2^33`), `classMask` (`int`)
- `isLevelingQuest` (`bool`): True for non-seasonal, non-daily, non-repeatable, non-BG/raid/craft-gated Lv 1–60 leveling quests across all 10 races and 9 classes
- `eligibleAllianceGnomeRogue` (`bool`): Strict subset of `isLevelingQuest` where Alliance/Neutral, permits Gnome (`bit 64`) and Rogue (`bit 8`), and excludes Horde-only dungeons
- `sourceProvenance` (`list[str]`): Contributing data sources

### 2. `data/dungeons.json`
Top-level `{meta, dungeons}`, keyed by `dungeonId` slug:
- `dungeonId`, `zoneId`, `name`, `zoneName`, `entranceZoneId`, `entranceZoneName`, `entranceCoords` (`{zoneId, x, y}`), `minLevel`, `maxLevel`, `recommendedLevel`, `faction` (`"Alliance"|"Horde"|"Both"`), `questIds` (`list[int]`), `unlockChains` (`list[{chainName, questIds, minLevel, travelTip}]`).

### 3. `data/travel_graph.json`
Top-level `{meta, xpCurve, races, classes, nodes, edges}`:
- `xpCurve`: `"1".."59"` XP required to advance to level `L+1` (`4,084,700` total XP from 1 to 60).
- `races`: list of 10 canonical WoW Forever races (`{id, name, label, raceBit, faction, startZoneId, startCoords, classes}`, including Classic races and custom Skyborne races `95`/`96`).
- `classes`: list of 9 canonical WoW Forever classes (`{id, key, label, classBit, races}`).
- `nodes`: keyed by `"<zoneId>"` -> `{zoneId, uiMapId, name, faction ("Alliance"|"Horde"|"Contested"), continent, region, minLevel, maxLevel, hubName, hubCoords, flightNodeId, flightNodeIds, hasBoat, hasTram, hasZeppelin, hasAirship}`.
- `edges`: directed edges `{fromZoneId, toZoneId, mode ("run"|"flight"|"boat"|"zeppelin"|"airship"|"tram"|"hearth"), seconds, costCopper, faction ("Alliance"|"Horde"|"Both"), note}`. Covers all 69 non-BG OpenMap flight masters, Deeprun Tram, boats, Horde Zeppelins (`Orgrimmar <-> Tirisfal <-> STV`), and Forever Airships (`Alterac <-> Zephras Isle <-> Mulgore`).

## Sample Verified Bag, Gear & Profession Quests

### Verified Bag Quests
| QuestID | Name | Zone | Levels | Bag Slots |
| --- | --- | --- | --- | ---: |
| 1795 | The Binding | The Barrens | 30–30 | 24 |
| 1474 | The Binding | Undercity | 20–20 | 20 |
| 1513 | The Binding | Orgrimmar | 20–20 | 20 |
| 1739 | The Binding | Stormwind City | 20–20 | 20 |
| 3628 | You Are Rakh'likh, Demon | Blasted Lands | 45–60 | 16 |
| 2439 | The Platinum Discs | Uldaman | 40–47 | 14 |
| 2440 | The Platinum Discs | Thunder Bluff | 40–47 | 14 |
| 79192 | Stepping Stones | The Barrens | 14–24 | 12 |
| 96260 | Stronger than Steel | Mulgore | 23–30 | 12 |
| 626 | Cortello's Riddle | Stranglethorn Vale | 35–51 | 12 |
| 1486 | Deviate Hides | Wailing Caverns | 13–17 | 10 |
| 531 | Vyrin's Revenge | Loch Modan | 15–20 | 10 |
| 58 | The Night Watch | Duskwood | 18–30 | 10 |
| 470 | Digging Through the Ooze | Wetlands | 19–24 | 10 |
| 1003 | Buzzbox 525 | Darkshore | 7–16 | 8 |

### Top Rogue Upgrade Quests (Level 1–60)
| QuestID | Name | Zone | Quest Lvl | Rogue Score | Reward Items |
| --- | --- | --- | ---: | ---: | --- |
| 5730 | Hidden Enemies | Ragefire Chasm | 16 | 88.9 | Kris of Orgrimmar, Hammer of Orgrimmar |
| 914 | Leaders of the Fang | Wailing Caverns | 22 | 84.0 | Wingblade |
| 6922 | Baron Aquanis | Blackfathom Deeps | 30 | 78.6 | Outlaw Sabre, Dreamer's Chestguard |
| 96078 | A Tale Spun Around the Fire | Stranglethorn Vale | 40 | 76.5 | Sacrificial Sword, I'lalai Flayer |
| 3341 | Bring the End | Razorfen Downs | 42 | 76.4 | Vanquisher's Sword, Amberglow Talisman |
| 3636 | Bring the Light | Razorfen Downs | 42 | 76.4 | Vanquisher's Sword, Amberglow Talisman |
| 98297 | Stopping the Cycle | Wetlands | 34 | 76.2 | Subdued Dragon's Fang |
| 78699 | The Eye of Bhossca | Silverpine Forest | 35 | 76.0 | Ruby-Adorned Blade |
| 9023 | The Perfect Poison | Silithus | 60 | 75.8 | Ravenholdt Slicer, Shivsprocket's Shiv, The Thunderwood Poker, Fa |
| 94927 | Dunston Checks Out | Riverglades | 44 | 75.3 | Dunston's Ogre Duster |
| 1048 | Into The Scarlet Monastery | Scarlet Monastery | 42 | 74.7 | Sword of Omen, Dragon's Blood Necklace |
| 94968 | Eye of Mugg | Krol'dok Stronghold | 45 | 74.1 | Rock on Stick |
| 94982 | Eye of Mugg | Krol'dok Stronghold | 45 | 74.1 | Rock on Stick |
| 95264 | Shaw Needs To Know | Stormwind City | 53 | 74.1 | Dustwallow Dirk |
| 95483 | Thrall Needs to Know | Alcaz Prison | 53 | 74.1 | Dustwallow Dirk |

### Verified Leatherworking & Skinning Quests
| QuestID | Name | Zone | Levels | Profession (Min Skill) | LW Rewards |
| --- | --- | --- | --- | --- | --- |
| 96031 | Camping 101: Leatherworking | Dun Morogh | 4–6 | LW (1) | — |
| 96056 | Camping 101: Skinning | Dun Morogh | 4–6 | Skin (1) | — |
| 97922 | Camping 101: Leatherworking | Elwynn Forest | 4–6 | LW (1) | — |
| 97924 | Camping 101: Skinning | Elwynn Forest | 4–6 | Skin (1) | — |
| 97946 | Camping 101: Leatherworking | Teldrassil | 4–6 | LW (1) | — |
| 97949 | Camping 101: Skinning | Teldrassil | 4–6 | Skin (1) | — |
| 91751 | Rough Wolf Pelts | Elwynn Forest | 5–8 | Skin (1) | — |
| 1582 | Moonglow Vest | Darnassus | 8–18 | LW (90) | Pattern: Moonglow Vest |
| 1487 | Deviate Eradication | Wailing Caverns | 15–21 | Pattern (0) | Pattern: Deviate Scale Belt |
| 2847 | Wild Leather Armor | Feralas | 30–45 | LW (200) | — |
| 2848 | Wild Leather Shoulders | Feralas | 30–45 | LW (225) | Pattern: Wild Leather Shoulders |
| 2849 | Wild Leather Vest | Feralas | 30–45 | LW (225) | Pattern: Wild Leather Vest |
| 2850 | Wild Leather Helmet | Feralas | 30–45 | LW (225) | Pattern: Wild Leather Helmet |
| 2851 | Wild Leather Boots | Feralas | 30–45 | LW (225) | Pattern: Wild Leather Boots |
| 2852 | Wild Leather Leggings | Feralas | 30–45 | LW (225) | Pattern: Wild Leather Leggings |
| 2853 | Master of the Wild Leather | Feralas | 30–45 | LW (225) | Pattern: Wild Leather Cloak |
| 5141 | Dragonscale Leatherworking | Azshara | 40–55 | LW (225) | — |
| 5143 | Tribal Leatherworking | Feralas | 40–55 | LW (225) | — |
| 5144 | Elemental Leatherworking | Searing Gorge | 40–55 | LW (225) | — |
| 5885 | Salve via Skinning | Felwood | 48–55 | Skin (200) | — |

### Top Skinnable Beast/Dragonkin Synergy Quests (Level 22–60)
| QuestID | Name | Zone | Quest Lvl | Skinning Score | Target Count | Beast Types |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 4182 | Dragonkin Menace | Burning Steppes | 54 | 99.7 | 33 | Dragonkin |
| 98215 | Dragon Scales, Post Burnt Tails | Dustwallow Marsh | 44 | 97.8 | 20 | Dragonkin |
| 5529 | Plagued Hatchlings | Scholomance | 58 | 93.5 | 20 | Dragonkin |
| 1175 | A Bump in the Road | Thousand Needles | 33 | 91.9 | 30 | Beast |
| 5543 | Blood Tinged Skies | Eastern Plaguelands | 56 | 91.9 | 30 | Beast |
| 95792 | Clear 'em Out | Riverglades | 42 | 89.3 | 16 | Dragonkin |
| 98205 | Whelps are the Worst! | Dustwallow Marsh | 44 | 89.3 | 16 | Dragonkin |
| 4503 | Shizzle's Flyer | Un'Goro Crater | 51 | 87.7 | 16 | Beast |
| 86577 | A Sampling of Putrescence | Felwood | 55 | 87.7 | 16 | Beast |
| 95924 | Panic at the Digsite | The Shaper's Terrace | 60 | 87.3 | 21 | Beast |
| 2581 | Snickerfang Jowls | Blasted Lands | 50 | 86.7 | 15 | Beast |
| 2583 | A Boar's Vitality | Blasted Lands | 50 | 86.7 | 15 | Beast |
| 2585 | The Decisive Striker | Blasted Lands | 50 | 86.7 | 15 | Beast |
| 294 | Ormer's Revenge | Wetlands | 24 | 86.5 | 20 | Beast |
| 96137 | Ira's Dagger | Duskwood | 24 | 86.5 | 20 | Beast |
