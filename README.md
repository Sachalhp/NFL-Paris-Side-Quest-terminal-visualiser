# 🏈 Coach's Film Room

## The insight (the short version)

Raw NFL tracking data is just millions of (x, y) coordinates that no coach,
journalist, or fan can read. **Coach's Film Room turns that firehose into
something anyone understands in seconds: it replays any play as 22 moving
players on a terminal football field, and overlays a live "QB Pressure" meter
that measures, 10 times a second, how close the nearest pass-rusher is to the
quarterback.** You instantly see the pocket form and collapse, who won the
protection rep, and the tool even writes the verdict in plain English. Built
in one sitting with Python, pandas, and Textual, it works for any Big Data Bowl
dataset and lets a non-technical user add new games with a single button.

---

**Watch any NFL play come to life in your terminal, and instantly see who won the pass-protection battle.**

Pick a game, pick a play, and all 22 players move across a football field drawn
right in your terminal. A live **QB Pressure** meter fills as the pass rush
closes in, a color-coded roster tells you who's who, and a one-line coach's
verdict says whether protection held or broke down.

Built for the NFL Big Data Bowl dataset.

---

## Why a coach cares

On film day a coach asks simple questions: *Did we protect the quarterback?
Who got beat? Where did the pressure come from?* This tool answers them
**visually**, no spreadsheets, no jargon. Just the play on the field, a live
pressure gauge, and a plain-English read.

---

## What makes it stand out

- **Live tracking animation in a terminal.** Real player-tracking data (10
  frames a second) replayed as moving dots with fading motion trails. Most
  tools draw static charts; this one moves.
- **The QB Pressure meter.** Every frame, it measures the distance from the
  nearest pass-rusher to the quarterback and turns it into a 0–100 heat score.
  Watch it go red as the pocket collapses. Jump straight to the peak moment
  with one key.
- **Coach's verdict.** One sentence per play: *"Protection broke down —
  Mercilus got home for the sack"* or *"Protection held — clean pocket."*
- **Color-coded rosters + player cards.** See both line-ups at a glance, and
  click any player for height, weight, college, role, and season sacks.
- **Grows with you, no restart needed.** Click **📂 Load new data…** to load a
  whole new season's folder, or **➕ Add a game…** to drop a single game's
  tracking file into your current library. Both are point-and-click, both
  validate before changing anything, and the new games appear instantly. Add
  next year's CSVs and keep using it, for a decade.

---

## Quick start

```bash
# 1. Create a virtual environment and install the libraries
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 2. Run it
./venv/bin/python3 main.py

# Optional: use a different dataset folder
./venv/bin/python3 main.py --data-dir /path/to/bigdatabowl
```

> The field animation needs a reasonably wide terminal (three columns: play
> list · field · roster). Maximize your window for the best view.

### Controls

| Key / Button          | Action                              |
|-----------------------|-------------------------------------|
| `space` / ▶ ⏸         | Play / pause the replay             |
| `r` / ⏮ Restart       | Back to the snap                    |
| `⚡` / `p`             | Jump to the peak-pressure moment    |
| `←` `→` / ◀ ▶          | Previous / next play                |
| `g` / 🏈              | Whole-game mode (auto-play every play) |
| `l`                   | Season leaderboard                  |
| `d` / 📂 Load new data | Browse to and load a new dataset folder |
| `a` / ➕ Add a game    | Add one game's tracking file to the current library |
| `h`                   | Help / welcome screen               |
| `q`                   | Quit                                |

**Workflow:** pick a game → (optionally filter: Sacks / QB pressured / 3rd
downs) → click a play → press Play → hit ⚡ to skip to the key moment → pick a
player to inspect.

---

## How it's built

Two files, one job each, so the project is easy to read:

| File       | Responsibility |
|------------|----------------|
| `data.py`  | All the data work (pandas). Reads the CSVs and returns clean objects. The UI never sees a raw table or a player ID. |
| `main.py`  | The interactive UI (textual + textual-plotext). Draws the field, runs the animation, shows the meter, rosters, and verdict. |

### The analysis, in four moves

Every stat in `data.py` is built from four simple operations on a table. If
you're new to data analysis, this is the whole idea:

| Move | Meaning | Example |
|------|---------|---------|
| **Filter** | keep rows you care about | only this play's tracking rows |
| **Group + aggregate** | bucket rows, then count/sum each bucket | total sacks per player |
| **Join** | glue two tables on a shared key | attach player names to IDs |
| **Sort + head** | rank, then take the top N | top 12 pass rushers |

### The pressure meter (the signature technique)

The tracking data gives every player's `(x, y)` position 10 times a second.
For each frame the tool:

1. finds the quarterback (from the PFF scouting role),
2. finds every pass rusher,
3. measures the straight-line distance from the QB to the **closest** rusher,
4. maps that distance to a 0–100 score (10+ yards = clean pocket, ≤1 yard = in
   his face).

It's an honest proxy: a straight-line distance, not an account of blocking.
That simplicity is deliberate, it's fast, explainable, and reacts live.

---

## The data

Uses the NFL Big Data Bowl regional-event dataset:

```
data/
├── games.csv              every game
├── players.csv            every player (names, positions, bios)
├── plays.csv              every play (down, distance, result, coverage)
├── pffScoutingData.csv    PFF pass-pro scouting (roles, sacks, hurries, blocks)
└── tracking/
    └── tracking_<gameId>.csv   player positions, 10 Hz (one file per game)
```

Leaderboards use the **full season**. The animated replay works for any game
with a `tracking_<gameId>.csv` file. Drop more tracking files into
`data/tracking/` and they appear in the game picker automatically.

Tracking data by **NFL Next Gen Stats**; scouting data by **Pro Football Focus (PFF)**.

---

## Ideas for next steps

- A pressure meter that also factors in rusher speed and angle, not just distance.
- Route trails colored by player.
- Export a play's verdict + stats to a shareable text card.
