"""
data.py  --  The data layer for Coach's Film Room.

Everything that touches pandas lives here. The UI (main.py) never sees a
DataFrame or an "nflId"; it only gets back clean Python objects with
coach-friendly names. Keeping the data work in one file makes the whole
project easy to read: the UI is about *showing*, this file is about *knowing*.

The analysis is built from four simple moves on a table:
    FILTER            keep the rows you care about
    GROUP + AGGREGATE bucket rows, then count/sum/average each bucket
    JOIN              glue two tables together on a shared key
    SORT + HEAD       rank, then take the top N
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

# Where the CSV files live. Defaults to ./data next to this file, but can be
# pointed anywhere with configure() / the --data-dir flag, so the tool works
# on ANY Big Data Bowl dataset, not just the one we bundled.
DATA_DIR = Path(__file__).parent / "data"
TRACKING_DIR = DATA_DIR / "tracking"


def configure(data_dir: str | Path) -> None:
    """Point the tool at a different dataset folder.

    The folder must contain games.csv, players.csv, plays.csv,
    pffScoutingData.csv and a tracking/ subfolder of tracking_<gameId>.csv.
    """
    global DATA_DIR, TRACKING_DIR
    DATA_DIR = Path(data_dir).expanduser().resolve()
    TRACKING_DIR = DATA_DIR / "tracking"
    _clear_caches()


def validate_data_dir(data_dir: str | Path) -> list[str]:
    """Return a list of problems with a dataset folder (empty list = all good)."""
    d = Path(data_dir).expanduser().resolve()
    problems = []
    if not d.is_dir():
        return [f"Folder not found: {d}"]
    for name in ("games.csv", "players.csv", "plays.csv", "pffScoutingData.csv"):
        if not (d / name).is_file():
            problems.append(f"Missing {name}")
    tracking = d / "tracking"
    if not tracking.is_dir() or not list(tracking.glob("tracking_*.csv")):
        problems.append("Missing tracking/ folder with tracking_<gameId>.csv files")
    return problems


def _clear_caches() -> None:
    """Forget any data loaded from the previous folder."""
    for fn in (_games_df, _players_df, _plays_df, _pff_df, _tracking_df):
        fn.cache_clear()


def check_tracking_file(csv_path: str | Path) -> tuple[bool, str]:
    """Would this file work as a new game's tracking data? (ok, message).

    A tracking file is only playable if it has the right columns AND its
    gameId already exists in the current plays.csv. We check both so the
    coach gets a clear answer before anything is copied.
    """
    p = Path(csv_path).expanduser()
    if not p.is_file() or p.suffix.lower() != ".csv":
        return False, "That isn't a .csv file."
    try:
        head = pd.read_csv(p, nrows=200)
    except Exception as exc:  # unreadable / not a real csv
        return False, f"Could not read that file: {exc}"

    needed = {"gameId", "playId", "nflId", "frameId", "x", "y", "team"}
    missing = needed - set(head.columns)
    if missing:
        return False, f"Not tracking data (missing columns: {', '.join(sorted(missing))})."

    game_ids = head["gameId"].dropna().unique()
    if len(game_ids) == 0:
        return False, "No gameId found in that file."
    gid = int(game_ids[0])

    if gid in available_game_ids():
        return False, f"Game {gid} is already in your library."
    if gid not in set(_plays_df()["gameId"].unique()):
        return False, (
            f"Game {gid} isn't in this dataset's plays.csv yet, so its plays "
            "can't be shown. Load the full dataset that includes this game."
        )
    return True, f"Ready to add game {gid}."


def add_tracking_file(csv_path: str | Path) -> tuple[bool, str, int | None]:
    """Copy a tracking file into the current dataset. (ok, message, gameId)."""
    import shutil

    ok, msg = check_tracking_file(csv_path)
    if not ok:
        return False, msg, None
    src = Path(csv_path).expanduser()
    gid = int(pd.read_csv(src, nrows=50)["gameId"].dropna().iloc[0])
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    dest = TRACKING_DIR / f"tracking_{gid}.csv"
    shutil.copy2(src, dest)
    _tracking_df.cache_clear()  # re-scan so the new game appears
    return True, f"Added game {gid} to your library.", gid

# Standard NFL field dimensions (yards). Used by the UI to draw the field.
FIELD_LENGTH = 120.0  # includes both 10-yard end zones
FIELD_WIDTH = 53.3


# ---------------------------------------------------------------------------
# Small, typed "view" objects. The UI consumes these instead of raw tables,
# so the UI code reads like coach language, not data-plumbing.
# ---------------------------------------------------------------------------
@dataclass
class Game:
    game_id: int
    week: int
    home: str
    away: str
    date: str

    @property
    def label(self) -> str:
        return f"Week {self.week}:  {self.away} @ {self.home}  ({self.date})"


@dataclass
class Play:
    game_id: int
    play_id: int
    description: str
    quarter: int
    down: int
    yards_to_go: int
    offense: str
    defense: str
    coverage: str
    result: str  # C / I / S / IN / R
    # Field x-positions (yards) of the two key lines, filled when frames load.
    scrimmage_x: float | None = None   # where the ball is snapped from
    first_down_x: float | None = None  # the line to gain
    had_sack: bool = False      # did the defense get a sack on this play?
    had_pressure: bool = False  # any hit or hurry on the QB?

    @property
    def label(self) -> str:
        down_txt = _ordinal(self.down) if self.down else "--"
        badge = ""
        if self.had_sack:
            badge = "  \U0001f6a8 SACK"       # siren: the big moments
        elif self.had_pressure:
            badge = "  \u26a1 pressure"       # lightning: QB got hit/hurried
        return (
            f"Q{self.quarter}  {down_txt} & {self.yards_to_go}  "
            f"({self.offense})  {self._result_word()}{badge}"
        )

    def _result_word(self) -> str:
        return {
            "C": "complete",
            "I": "incomplete",
            "S": "SACK",
            "IN": "INTERCEPTED",
            "R": "scramble",
        }.get(self.result, self.result or "")


@dataclass
class Frame:
    """One moment in time: where every player and the ball are."""
    index: int
    event: str  # "" unless this frame is tagged (e.g. "ball_snap")
    players: list["PlayerDot"]
    ball: tuple[float, float] | None  # (x, y) of the football, if present
    # Live pass-rush pressure 0-100: how close the nearest rusher is to the QB.
    # 0 = QB has a clean pocket, 100 = a rusher is right on top of him.
    pressure: int = 0
    qb_pos: tuple[float, float] | None = None  # the QB's (x, y), for drawing


@dataclass
class PlayerDot:
    x: float
    y: float
    team: str
    jersey: int
    is_offense: bool
    nfl_id: int = 0
    # "" normally, or one of: "sack", "pressure", "beaten" when this player
    # is the one who made/allowed the key play. The UI colors these red.
    highlight: str = ""


# ---------------------------------------------------------------------------
# LOADING. These are cached so we only read each CSV from disk once.
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _games_df() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "games.csv")


@lru_cache(maxsize=1)
def _players_df() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "players.csv")


@lru_cache(maxsize=1)
def _plays_df() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "plays.csv")


@lru_cache(maxsize=1)
def _pff_df() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "pffScoutingData.csv")


@lru_cache(maxsize=4)
def _tracking_df(game_id: int) -> pd.DataFrame:
    """Load ONE game's tracking file. Cached so scrubbing a play is instant."""
    return pd.read_csv(TRACKING_DIR / f"tracking_{game_id}.csv")


def available_game_ids() -> set[int]:
    """Only games we actually downloaded tracking for can be animated."""
    ids = set()
    for p in TRACKING_DIR.glob("tracking_*.csv"):
        ids.add(int(p.stem.split("_")[1]))
    return ids


# ---------------------------------------------------------------------------
# GAMES  --  list the games we can animate (FILTER to ones with tracking).
# ---------------------------------------------------------------------------
def list_games() -> list[Game]:
    df = _games_df()
    have_tracking = available_game_ids()
    df = df[df["gameId"].isin(have_tracking)]  # MOVE 1: filter
    games = [
        Game(
            game_id=int(r.gameId),
            week=int(r.week),
            home=str(r.homeTeamAbbr),
            away=str(r.visitorTeamAbbr),
            date=str(r.gameDate),
        )
        for r in df.itertuples()
    ]
    return sorted(games, key=lambda g: g.week)


# ---------------------------------------------------------------------------
# PLAYS  --  list the pass plays in a game that actually have tracking frames.
# ---------------------------------------------------------------------------
# The filters a coach can apply to the play list.
PLAY_FILTERS = ["All plays", "Sacks", "QB pressured", "3rd downs"]


def list_plays(game_id: int, limit: int = 80, play_filter: str = "All plays") -> list[Play]:
    plays = _plays_df()
    track = _tracking_df(game_id)
    pff = _pff_df()

    # MOVE 1 (filter): only this game's plays...
    plays = plays[plays["gameId"] == game_id]
    # ...and only plays that exist in the tracking file (so animation works).
    playable = set(track["playId"].unique())
    plays = plays[plays["playId"].isin(playable)]

    # Pre-compute which plays had a sack / any pressure (one pass over PFF).
    gpff = pff[pff["gameId"] == game_id]
    sack_plays = set(gpff[gpff["pff_sack"] == 1]["playId"])
    pressure_plays = set(
        gpff[(gpff["pff_hit"] == 1) | (gpff["pff_hurry"] == 1)]["playId"]
    )

    out = []
    for r in plays.itertuples():
        pid = int(r.playId)
        out.append(
            Play(
                game_id=int(r.gameId),
                play_id=pid,
                description=str(r.playDescription),
                quarter=int(r.quarter) if pd.notna(r.quarter) else 0,
                down=int(r.down) if pd.notna(r.down) else 0,
                yards_to_go=int(r.yardsToGo) if pd.notna(r.yardsToGo) else 0,
                offense=str(r.possessionTeam),
                defense=str(r.defensiveTeam),
                coverage=str(r.pff_passCoverage) if pd.notna(r.pff_passCoverage) else "",
                result=str(r.passResult) if pd.notna(r.passResult) else "",
                had_sack=pid in sack_plays,
                had_pressure=pid in pressure_plays,
            )
        )

    # Apply the coach's chosen filter.
    if play_filter == "Sacks":
        out = [p for p in out if p.had_sack]
    elif play_filter == "QB pressured":
        out = [p for p in out if p.had_pressure or p.had_sack]
    elif play_filter == "3rd downs":
        out = [p for p in out if p.down == 3]

    return out[:limit]


# ---------------------------------------------------------------------------
# FRAMES  --  turn one play's tracking rows into a list of Frames to animate.
# This is the heart of the "cool" feature.
# ---------------------------------------------------------------------------
def load_play_frames(game_id: int, play_id: int) -> list[Frame]:
    track = _tracking_df(game_id)

    # Which team is on offense? We color dots by offense/defense, not home/away.
    play_row = _plays_df()
    play_row = play_row[
        (play_row["gameId"] == game_id) & (play_row["playId"] == play_id)
    ]
    offense_team = str(play_row["possessionTeam"].iloc[0]) if len(play_row) else ""

    # Work out which players to spotlight in red (the sacker, the pressuring
    # defender, the beaten blocker). Returned as {nflId: "sack"/"pressure"/...}.
    spotlight = _spotlight_players(game_id, play_id)

    # Who is the QB, and who are the pass rushers? We read this from the PFF
    # roles so the pressure meter knows which distance to measure.
    qb_id, rusher_ids = _qb_and_rushers(game_id, play_id)

    # MOVE 1 (filter): just this one play, sorted in time order.
    rows = track[track["playId"] == play_id].sort_values(["frameId"])

    frames: list[Frame] = []
    # GROUP: bucket rows by frameId -> each bucket is one moment in time.
    for frame_id, chunk in rows.groupby("frameId"):
        players: list[PlayerDot] = []
        ball = None
        event = ""
        qb_pos = None
        rusher_positions = []
        for r in chunk.itertuples():
            if isinstance(r.event, str) and r.event not in ("None", "nan"):
                event = r.event
            if r.team == "football":
                ball = (float(r.x), float(r.y))
                continue
            nfl_id = int(r.nflId) if pd.notna(r.nflId) else 0
            if nfl_id == qb_id:
                qb_pos = (float(r.x), float(r.y))
            if nfl_id in rusher_ids:
                rusher_positions.append((float(r.x), float(r.y)))
            players.append(
                PlayerDot(
                    x=float(r.x),
                    y=float(r.y),
                    team=str(r.team),
                    jersey=int(r.jerseyNumber) if pd.notna(r.jerseyNumber) else 0,
                    is_offense=(str(r.team) == offense_team),
                    nfl_id=nfl_id,
                    highlight=spotlight.get(nfl_id, ""),
                )
            )
        pressure = _pressure_score(qb_pos, rusher_positions)
        frames.append(
            Frame(
                index=int(frame_id),
                event=event,
                players=players,
                ball=ball,
                pressure=pressure,
                qb_pos=qb_pos,
            )
        )
    return frames


def scrimmage_lines(game_id: int, play_id: int) -> tuple[float | None, float | None]:
    """Return (line of scrimmage x, first-down line x) in field yards.

    The scrimmage is where the ball sits at the snap. The first-down line is
    `yardsToGo` further along in the direction the offense is moving: toward
    SMALLER x if the play goes left, LARGER x if it goes right.
    """
    track = _tracking_df(game_id)
    play_rows = track[track["playId"] == play_id]
    if play_rows.empty:
        return None, None

    # Ball position at the first frame = the line of scrimmage.
    first_frame = play_rows["frameId"].min()
    ball = play_rows[(play_rows["frameId"] == first_frame) & (play_rows["team"] == "football")]
    if ball.empty:
        return None, None
    scrimmage = float(ball["x"].iloc[0])

    plays = _plays_df()
    pr = plays[(plays["gameId"] == game_id) & (plays["playId"] == play_id)]
    ytg = int(pr["yardsToGo"].iloc[0]) if len(pr) else 10
    direction = str(play_rows["playDirection"].iloc[0])

    first_down = scrimmage - ytg if direction == "left" else scrimmage + ytg
    return scrimmage, first_down


def _qb_and_rushers(game_id: int, play_id: int) -> tuple[int, set[int]]:
    """Return (qb nflId, {pass-rusher nflIds}) from the PFF roles."""
    pff = _pff_df()
    rows = pff[(pff["gameId"] == game_id) & (pff["playId"] == play_id)]
    qb_rows = rows[rows["pff_role"] == "Pass"]["nflId"]
    qb_id = int(qb_rows.iloc[0]) if len(qb_rows) else 0
    rushers = {int(n) for n in rows[rows["pff_role"] == "Pass Rush"]["nflId"]}
    return qb_id, rushers


def _pressure_score(
    qb_pos: tuple[float, float] | None,
    rusher_positions: list[tuple[float, float]],
) -> int:
    """Turn 'how close is the nearest rusher to the QB' into a 0-100 score.

    This is the heart of the pressure meter. We measure the straight-line
    distance (in yards) from the QB to the closest pass rusher, then map it:
        >= 10 yards away  -> 0   (clean pocket)
        <= 1 yard away    -> 100 (rusher is right on top of him)
    """
    if qb_pos is None or not rusher_positions:
        return 0
    qx, qy = qb_pos
    nearest = min(
        ((qx - rx) ** 2 + (qy - ry) ** 2) ** 0.5 for rx, ry in rusher_positions
    )
    clean, ontop = 10.0, 1.0  # yards
    if nearest >= clean:
        return 0
    if nearest <= ontop:
        return 100
    # Linear between 1 and 10 yards.
    return int(round((clean - nearest) / (clean - ontop) * 100))


def peak_pressure_frame(frames: list[Frame]) -> int:
    """Index of the frame with the highest pressure (for 'jump to the moment')."""
    if not frames:
        return 0
    return max(range(len(frames)), key=lambda i: frames[i].pressure)


def _spotlight_players(game_id: int, play_id: int) -> dict[int, str]:
    """Which players made/allowed the key pass-pro moment, by nflId.

    Priority order so one player gets one label: sack > pressure > beaten.
    """
    pff = _pff_df()
    rows = pff[(pff["gameId"] == game_id) & (pff["playId"] == play_id)]
    spot: dict[int, str] = {}
    # Beaten blockers first (lowest priority), then overwrite with stronger tags.
    for nid in rows[rows["pff_beatenByDefender"] == 1]["nflId"]:
        spot[int(nid)] = "beaten"
    for nid in rows[(rows["pff_hurry"] == 1) | (rows["pff_hit"] == 1)]["nflId"]:
        spot[int(nid)] = "pressure"
    for nid in rows[rows["pff_sack"] == 1]["nflId"]:
        spot[int(nid)] = "sack"
    return spot


# ---------------------------------------------------------------------------
# PASS-PRO REPORT  --  the coach's read on a single play.
# Uses JOIN (pff -> players) + FILTER to summarise who won the pass-pro battle.
# ---------------------------------------------------------------------------
def pass_pro_report(game_id: int, play_id: int) -> dict:
    pff = _pff_df()
    players = _players_df()

    # MOVE 1 (filter): just this play's scouting rows.
    rows = pff[(pff["gameId"] == game_id) & (pff["playId"] == play_id)]

    # MOVE 3 (join): attach names + positions to the IDs.
    rows = rows.merge(
        players[["nflId", "displayName", "officialPosition"]],
        on="nflId",
        how="left",
    )

    def names_where(col: str) -> list[str]:
        hits = rows[rows[col] == 1]
        return [f"{r.displayName} ({r.officialPosition})" for r in hits.itertuples()]

    return {
        "sacks_by": names_where("pff_sack"),
        "hurries_by": names_where("pff_hurry"),
        "hits_by": names_where("pff_hit"),
        "beaten_blockers": names_where("pff_beatenByDefender"),
        "sack_allowed_by": names_where("pff_sackAllowed"),
        "verdict": _verdict(rows),
    }


def _verdict(rows: pd.DataFrame) -> str:
    """One plain-English sentence judging the pass-protection rep.

    This is the 'so what' line a coach wants: did protection hold or break?
    """
    def first_name(col: str) -> str | None:
        hit = rows[rows[col] == 1]
        return str(hit.iloc[0]["displayName"]) if len(hit) else None

    sacker = first_name("pff_sack")
    if sacker:
        return f"\u274c Protection broke down \u2014 {sacker} got home for the sack."
    hitter = first_name("pff_hit")
    if hitter:
        return f"\u26a0\ufe0f  Pocket collapsed \u2014 {hitter} got a clean hit on the QB."
    hurried = first_name("pff_hurry")
    if hurried:
        return f"\u26a0\ufe0f  Some heat \u2014 {hurried} hurried the QB off his spot."
    return "\u2705 Protection held \u2014 the QB had a clean pocket to throw from."


# ---------------------------------------------------------------------------
# PLAYER CARD  --  everything we know about one player on one play.
# ---------------------------------------------------------------------------
def players_on_play(game_id: int, play_id: int) -> list[tuple[int, str]]:
    """Return [(nflId, "#12 Josh Allen - QB")] for the dropdown, O first."""
    pff = _pff_df()
    players = _players_df()
    track = _tracking_df(game_id)

    ids = pff[(pff["gameId"] == game_id) & (pff["playId"] == play_id)]["nflId"].unique()
    # Jersey numbers come from the tracking file for this play.
    jerseys = (
        track[track["playId"] == play_id][["nflId", "jerseyNumber", "team"]]
        .dropna(subset=["nflId"])
        .drop_duplicates("nflId")
        .set_index("nflId")
    )
    pinfo = players.set_index("nflId")

    out = []
    for nid in ids:
        nid = int(nid)
        if nid not in pinfo.index:
            continue
        name = pinfo.loc[nid, "displayName"]
        pos = pinfo.loc[nid, "officialPosition"]
        jersey = ""
        if nid in jerseys.index:
            jn = jerseys.loc[nid, "jerseyNumber"]
            jersey = f"#{int(jn)} " if pd.notna(jn) else ""
        out.append((nid, f"{jersey}{name} - {pos}"))
    return sorted(out, key=lambda t: t[1])


@dataclass
class RosterEntry:
    nfl_id: int
    jersey: int
    name: str
    position: str
    highlight: str  # "", "sack", "pressure", "beaten" (same tags as the field)


def play_rosters(game_id: int, play_id: int) -> dict:
    """Both teams' line-ups for a play, grouped offense vs defense.

    Returns {"offense": {"team": "BUF", "players": [RosterEntry,...]},
             "defense": {"team": "HOU", "players": [...]}} so the UI can
    show a color-coded 'who's on the field' list beside the animation.
    """
    players = _players_df().set_index("nflId")
    track = _tracking_df(game_id)
    plays = _plays_df()

    pr = plays[(plays["gameId"] == game_id) & (plays["playId"] == play_id)]
    offense_team = str(pr["possessionTeam"].iloc[0]) if len(pr) else ""
    defense_team = str(pr["defensiveTeam"].iloc[0]) if len(pr) else ""

    spotlight = _spotlight_players(game_id, play_id)

    # Team + jersey for each player on this play, from the tracking file.
    rows = (
        track[(track["playId"] == play_id) & (track["team"] != "football")]
        [["nflId", "jerseyNumber", "team"]]
        .dropna(subset=["nflId"])
        .drop_duplicates("nflId")
    )

    offense, defense = [], []
    for r in rows.itertuples():
        nid = int(r.nflId)
        if nid not in players.index:
            continue
        entry = RosterEntry(
            nfl_id=nid,
            jersey=int(r.jerseyNumber) if pd.notna(r.jerseyNumber) else 0,
            name=str(players.loc[nid, "displayName"]),
            position=str(players.loc[nid, "officialPosition"]),
            highlight=spotlight.get(nid, ""),
        )
        (offense if str(r.team) == offense_team else defense).append(entry)

    offense.sort(key=lambda e: e.jersey)
    defense.sort(key=lambda e: e.jersey)
    return {
        "offense": {"team": offense_team, "players": offense},
        "defense": {"team": defense_team, "players": defense},
    }


def player_info(game_id: int, play_id: int, nfl_id: int) -> dict:
    """Full card for one player: bio + what he did on THIS play."""
    players = _players_df()
    pff = _pff_df()

    bio_rows = players[players["nflId"] == nfl_id]
    if bio_rows.empty:
        return {}
    bio = bio_rows.iloc[0]

    play_rows = pff[
        (pff["gameId"] == game_id)
        & (pff["playId"] == play_id)
        & (pff["nflId"] == nfl_id)
    ]
    role = ""
    lined_up = ""
    did = []
    if not play_rows.empty:
        pr = play_rows.iloc[0]
        role = str(pr["pff_role"]) if pd.notna(pr["pff_role"]) else ""
        lined_up = str(pr["pff_positionLinedUp"]) if pd.notna(pr["pff_positionLinedUp"]) else ""
        # Plain-English list of what the scouting flagged for this player.
        flags = {
            "pff_sack": "recorded a SACK",
            "pff_hit": "hit the QB",
            "pff_hurry": "hurried the QB",
            "pff_beatenByDefender": "was beaten by a defender",
            "pff_sackAllowed": "allowed a sack",
            "pff_hurryAllowed": "allowed a hurry",
            "pff_hitAllowed": "allowed a hit",
        }
        for col, text in flags.items():
            if col in pr and pr[col] == 1:
                did.append(text)

    # Season sacks for this player (a little breadth on the card).
    season_sacks = float(pff[pff["nflId"] == nfl_id]["pff_sack"].sum())

    return {
        "name": str(bio["displayName"]),
        "position": str(bio["officialPosition"]),
        "height": str(bio["height"]),
        "weight": f"{int(bio['weight'])} lb" if pd.notna(bio["weight"]) else "",
        "college": str(bio["collegeName"]),
        "role": role,
        "lined_up": lined_up,
        "did": did,
        "season_sacks": season_sacks,
    }


# ---------------------------------------------------------------------------
# SEASON LEADERBOARD  --  full-season breadth using all four moves.
# ---------------------------------------------------------------------------
def top_pass_rushers(stat: str = "pff_sack", top_n: int = 15) -> list[tuple[str, str, float]]:
    """Return [(player name, position, total)] for a pressure stat."""
    pff = _pff_df()
    players = _players_df()

    # MOVE 2 (group + aggregate): total the stat per player.
    totals = pff.groupby("nflId")[stat].sum()
    # MOVE 4 (sort + head): rank and take the top N.
    totals = totals.sort_values(ascending=False).head(top_n).reset_index()
    # MOVE 3 (join): put names on the IDs.
    named = totals.merge(
        players[["nflId", "displayName", "officialPosition"]], on="nflId", how="left"
    )
    return [
        (str(r.displayName), str(r.officialPosition), float(getattr(r, stat)))
        for r in named.itertuples()
    ]


# ---------------------------------------------------------------------------
# Tiny helper.
# ---------------------------------------------------------------------------
def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(n, f"{n}th")
