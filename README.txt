STRIKE LIST - desktop target tracker for Torn (Mac + Windows)
Free software under the GNU GPL v3 (see LICENSE.txt). Not affiliated with Torn.

Ready-made apps: StrikeList-<version>-mac.dmg / StrikeList-<version>-windows.exe
(see "How to open" in each download). The steps below are for running from source.

Your API key and data stay on your own computer. It only talks to api.torn.com.

1. Install Python 3 if you don't have it: https://www.python.org/downloads/
   (Windows: tick "Add python.exe to PATH" in the installer.)
2. Unzip this folder somewhere permanent (e.g. Documents).
3. Start it:
   Mac:      double-click "Start Strike List.command"
             (if macOS blocks it: right-click > Open > Open, once)
   Windows:  double-click "Start Strike List.bat"
             (if SmartScreen warns: More info > Run anyway, once)
   The first start takes about a minute while it installs its parts.
4. In the Settings tab paste your Torn API key (Limited Access is enough) and click Save.
   It then pulls your whole attack history (1 API call per 100 attacks).

Using it
- Targets: paste IDs or profile links and press Add. Click the column headers to sort.
  Click Attack to open the attack page, click Flag to cycle the colour, double-click
  Notes to edit, double-click a name to open the profile, right-click for more options.
- Attacks: your attack log. Right-click a row to add that player to Targets.
- FR = flat respect: your last win on them with chain/war/group/overseas bonuses
  removed, so you can compare targets fairly (same idea as YATA's FR).
- Clean up targets: set "Min FF" on the Targets tab (default 2.50). Targets below it show
  red; "Clean up..." lists them so you can untick any to keep, then removes the rest.
  Uses FF Scouter's current FF ("FF now") when it's on, otherwise the FF of your last win.
  Flagged targets are kept unless you untick "Keep flagged targets".
- Share... (Targets tab): copy your list to Strike List on any other phone or computer with a
  share code, or paste one in. Works between Mac, Windows and Strike List Mobile.
- Chains: your hits per chain - length, your share, respect, avg FR/FF, best hit.
  Uses your faction's chain list when your key can see it, otherwise groups your own hits.
- Awards: every honor and medal with your progress, goal and an estimated time to get it.
  Untick "Hide achieved" to see what you already have. Hover a row for the breakdown.
- War Scout: finds your faction's ranked war opponent (or type any faction ID / link)
  and lists every member with estimated battle stats, FF vs you, estimated respect,
  your past record on them, last action and live status. Filter (e.g. "stats <= 1x mine,
  FF >= 2.5") and add players to your War List.
- War List: your pre-war hit list - live hospital/travel timers, auto-refresh every 30s
  (1 API call for the whole faction), flags, notes, one-click attack, and "Copy list"
  to paste into Discord.
- Estimated stats come from FF Scouter (ffscouter.com). It's off by default - turn it on
  in Settings. That sends your Torn API key to FF Scouter, a separate service with its
  own Data Policy and Terms.

Strike List Mobile (phones: Torn PDA, or any browser with Tampermonkey)
- Install: https://raw.githubusercontent.com/PatMcCracken78/StrikeList/main/mobile/strike-list.user.js
- In Torn PDA: open the user scripts manager (in Settings), add a new script and paste the
  link above (or the script's contents).
- A small Strike List tab appears on the side of Torn - drag it up/down or to the other side.

Building the apps yourself
- Mac:     bash build/build_mac.sh        -> build/release/StrikeList-<version>-mac.dmg
- Windows: build\build_windows.bat        -> build\release\StrikeList-<version>-windows.exe
Each also writes the matching source zip (the GPL requires the source to be available).

Credits
- The Awards progress engine (yata_awards folder) comes from YATA by Kivou [2000607],
  https://github.com/Kivou-2000607/yata, licensed GPL-3.0 (see yata_awards/LICENSE-GPL3.md).
  Because of that, Strike List as a whole is shared under GPL-3.0 too.

Your data lives in:
  Mac:      ~/Library/Application Support/StrikeList
  Windows:  %APPDATA%\StrikeList
