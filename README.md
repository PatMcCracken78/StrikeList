<p align="center"><img src="assets/header.png" alt="Strike List" width="560"></p>

<p align="center"><b>A free desktop target tracker for <a href="https://www.torn.com">Torn</a> — Mac &amp; Windows.</b><br>
Your API key and data stay on your computer. Built because YATA keeps going down.</p>

<p align="center"><img src="docs/screenshot.png" alt="Strike List screenshot" width="100%"></p>

## Features

| | |
|---|---|
| 🎯 **Targets** | Your hit list: last attack & result, FF, **FF now** (FF Scouter), **FR** (flat respect), level, last action, life, live hospital/travel countdowns, flags, notes, one-click attack. **Min FF + Clean up** removes targets you've outgrown. |
| 🗡️ **Attacks** | Your full attack log (pulled from the v2 API, all history), respect, FF, FR, chain number. Right-click any row to add that player to Targets. |
| ⛓️ **Chains** | Your hits per chain — length, your share, respect, avg FR/FF, best hit. Uses your faction's chain list when your key can see it. |
| 🏅 **Awards** | Every honor and medal with your progress, goal and estimated time to get it (YATA's awards engine). |
| 🔭 **War Scout** | Finds your ranked-war opponent (or any faction ID/link) and lists every member with **estimated stats**, "vs me", FF, estimated respect, your record on them and live status. Filter and build a list. |
| 📋 **War List** | Your pre-war hit list with live timers, 30-second auto-refresh (1 API call for the whole faction), flags, notes, and **Copy list** for Discord. |
| ⇄ **Share** | Copy your target list to any other phone or computer (Strike List Mobile or desktop) with a share code. |
| ⏱️ **Header** | Energy, drug & booster cooldowns, **live chain timer with alerts**, armory Medical/Drugs shortcuts, and your own status & life. |

## 📱 Strike List Mobile (Torn PDA & browsers)

<p align="center"><img src="docs/mobile.png" alt="Strike List Mobile" width="100%"></p>

A small Strike List tab sits on the edge of every Torn page — drag it up or down, or over to the other side, and it snaps into place. Tap it for your **Targets** — everyone on your list, attackable first and then by who leaves hospital soonest — with live countdowns, FF / **FF now** / **FR**, a big **Attack** button, flags, notes, Min FF clean-up and optional FF Scouter estimates. When you open Torn it quickly catches up on anyone out of date (top of the list first, a few seconds for 30+ targets), keeps likely-attackable targets warm in the background, and while the panel is open re-checks anyone who looks attackable about every 20 s; cards show yellow **“Okay? · checked 2m ago”** when data is old, and **Attack** double-checks a player before opening the attack page if their data is more than 15 s old. Player profiles get a **+ Strike List** button. The green badge shows how many targets you can hit right now.

**⚔ War view:** tap **War** at the top of the panel. It finds your faction's ranked war (or scout any faction by ID/link) and lists every enemy with estimated stats, **× me**, FF and estimated FR, sorted by who's out of hospital soonest. Star (☆) players to build your war list, filter **Attackable** or **≤ me**, and **Copy list** for Discord. The roster refreshes every 30 seconds while you're watching (one API call for the whole faction).

<p align="center"><img src="docs/mobile-war.png" alt="Targets and War views" width="70%"></p>

**Install:** [`strike-list.user.js`](https://raw.githubusercontent.com/PatMcCracken78/StrikeList/main/mobile/strike-list.user.js)
- **Torn PDA:** open the user scripts manager in Settings, add a new script and paste the link above (or the script's contents). PDA supplies your API key automatically.
- **Desktop browsers:** install [Tampermonkey](https://www.tampermonkey.net/), then open the link above. Paste your Limited key in the panel's ⚙.

**Move your list between devices:** tap **⇄ Share** (mobile) or **Share…** on the Targets tab (desktop) to copy a code, then paste it on the other device. It works between any phone and any computer, in either direction.

## Download

Grab the latest **`.dmg`** (Mac, Apple Silicon) or **`.exe`** (Windows) from **[Releases](../../releases)**.

The apps aren't signed with paid certificates, so the first time you open them:
- **Mac:** right-click the app → **Open** → **Open**.
- **Windows:** SmartScreen → **More info** → **Run anyway**.

Then go to **Settings**, paste a **Limited Access** API key and click **Save**.

## How your API key is used

| Data storage | Data sharing | Purpose of use | Key storage & sharing | Key access level |
|---|---|---|---|---|
| Only on your computer | Nobody. If you turn on FF Scouter, your key and the player IDs you look up are sent to [ffscouter.com](https://ffscouter.com/) | Personal tools: targets, attack log, chains, awards, war lists | Saved only on your computer, readable by your user account only. Never shared, except with FF Scouter if you turn it on | Limited Access |

Strike List only **reads** from the Torn API. It never takes actions in the game for you.

Data folder: `~/Library/Application Support/StrikeList` (Mac) · `%APPDATA%\StrikeList` (Windows)

## Run from source

Needs Python 3.10+.

- **Mac:** double-click `Start Strike List.command`
- **Windows:** double-click `Start Strike List.bat`

The first start sets up a private Python environment (about a minute).

## Build the apps

- **Mac:** `bash build/build_mac.sh` → `build/release/StrikeList-<version>-mac.dmg`
- **Windows:** `build\build_windows.bat` → `build\release\StrikeList-<version>-windows.exe`

Each also writes the matching source zip.

## Credits & licence

- **Awards engine** from [YATA](https://github.com/Kivou-2000607/yata) by Kivou [2000607] (GPL-3) — thank you.
- **Stat estimates** by [FF Scouter](https://ffscouter.com/) (optional).
- Built with Qt for Python (PySide6), requests and NumPy — see [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt).

Strike List is free software under the **GNU General Public License v3** — see [LICENSE.txt](LICENSE.txt).
Not affiliated with or endorsed by Torn.
