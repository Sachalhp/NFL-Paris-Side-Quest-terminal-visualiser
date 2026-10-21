"""
Coach's Film Room  --  an interactive NFL play viewer for your terminal.

Pick a game, pick a play (or watch the whole game back-to-back), and see all
22 players move on the field. A side panel gives the coach's read: who
pressured the quarterback and who got beaten in pass protection.

Built with:
    textual          the interactive terminal UI (buttons, panels, screens)
    textual-plotext  draws the football field as a live chart
    pandas           crunches the data (kept entirely in data.py)

Run it with:
    ./venv/bin/python3 main.py
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
)
from textual_plotext import PlotextPlot

import data

# --- Colors, chosen so the three groups never clash -----------------------
# Offense and defense use the two classic chalkboard colors; the key player
# is a bright magenta that pops against BOTH of them.
OFFENSE_COLOR = "blue+"     # bright blue  -> the team with the ball
DEFENSE_COLOR = "white"     # white        -> the defense
BALL_COLOR = "orange+"      # bright orange-> the football
HIGHLIGHT_COLOR = "magenta+"  # the player who made/allowed the big moment

TRAIL_LENGTH = 7  # how many past frames of fading "comet tail" to draw


class FieldPlot(PlotextPlot):
    """The football field. Redraws every player's position for one frame,
    with fading motion trails and a spotlight on the key player."""

    def draw_frame(
        self,
        frames: list["data.Frame"],
        current: int,
        off_team: str,
        def_team: str,
        scrimmage_x: float | None = None,
        first_down_x: float | None = None,
    ) -> None:
        plt = self.plt
        plt.clear_data()
        plt.clear_figure()

        frame = frames[current]

        # ZOOM to the action: instead of the whole 120-yard field (which
        # crams all 22 players into a sliver and overlaps their numbers),
        # show a ~44-yard window centered on the ball. Players spread out
        # and every jersey number is readable.
        center = frame.ball[0] if frame.ball else (scrimmage_x or 60)
        half = 22.0
        x_lo = max(0, center - half)
        x_hi = min(data.FIELD_LENGTH, center + half)
        if x_hi - x_lo < 2 * half:  # keep a full window near the sidelines
            if x_lo == 0:
                x_hi = 2 * half
            elif x_hi == data.FIELD_LENGTH:
                x_lo = data.FIELD_LENGTH - 2 * half
        plt.xlim(x_lo, x_hi)
        plt.ylim(0, data.FIELD_WIDTH)

        # Yard-line ticks every 5 yards within the window.
        ticks = [y for y in range(0, 121, 5) if x_lo <= y <= x_hi]
        plt.xticks(ticks, [str(_yard_marker(y)) for y in ticks])
        plt.yticks([])
        plt.xlabel("")

        # Faint yard lines every 5 yards so it reads as a real field.
        for yard in range(0, 121, 5):
            if x_lo <= yard <= x_hi:
                plt.vertical_line(yard, color=(40, 90, 40))
        # End-zone boundaries (goal lines) drawn a touch brighter.
        for goal in (10, 110):
            if x_lo <= goal <= x_hi:
                plt.vertical_line(goal, color="green")

        # The two lines a fan knows from TV: the blue line of scrimmage
        # (where the ball is snapped) and the yellow first-down line to gain.
        if scrimmage_x is not None:
            plt.vertical_line(scrimmage_x, color="blue+")
        if first_down_x is not None:
            plt.vertical_line(first_down_x, color="yellow+")

        # --- MOTION TRAILS: faint fading dots behind each player -----------
        trail_start = max(0, current - TRAIL_LENGTH)
        for past in range(trail_start, current):
            age = current - past
            shade = _trail_shade(age)
            tx = [p.x for p in frames[past].players]
            ty = [p.y for p in frames[past].players]
            if tx:
                plt.scatter(tx, ty, marker="dot", color=shade)

        # --- CURRENT POSITIONS: clean colored DOTS (never overlap like
        # numbers do when players bunch at the snap). The roster panel on the
        # right maps numbers->names, so the field stays readable.
        off_x, off_y, def_x, def_y, hot_x, hot_y = [], [], [], [], [], []
        for p in frame.players:
            if p.highlight:
                hot_x.append(p.x); hot_y.append(p.y)
            elif p.is_offense:
                off_x.append(p.x); off_y.append(p.y)
            else:
                def_x.append(p.x); def_y.append(p.y)
        if off_x:
            plt.scatter(off_x, off_y, marker="O", color=OFFENSE_COLOR)
        if def_x:
            plt.scatter(def_x, def_y, marker="O", color=DEFENSE_COLOR)
        if hot_x:
            plt.scatter(hot_x, hot_y, marker="O", color=HIGHLIGHT_COLOR)

        # The football, as a distinct marker.
        if frame.ball:
            plt.scatter([frame.ball[0]], [frame.ball[1]], marker="O", color=BALL_COLOR)

        # Plain-English title; the legend lives in its own widget below.
        title = f"{off_team} (offense)  vs  {def_team} (defense)"
        if frame.event:
            title += f"      \u2605 {frame.event.replace('_', ' ').upper()}"
        plt.title(title)
        self.refresh()


class PressureMeter(Static):
    """A live bar showing how much heat the QB is under, right now.

    Empty + green = clean pocket. Full + red = a rusher is in his face.
    The value is computed in data.py from the nearest rusher's distance.
    """

    def show(self, pressure: int) -> None:
        blocks = int(round(pressure / 100 * 20))  # 20-cell bar
        if pressure >= 70:
            color, word = "red", "UNDER PRESSURE!"
        elif pressure >= 35:
            color, word = "yellow", "feeling heat"
        else:
            color, word = "green", "clean pocket"
        bar = f"[{color}]" + "\u2588" * blocks + "[/]" + "\u2591" * (20 - blocks)
        self.update(
            f"[b]QB PRESSURE[/b]\n{bar}  [{color} b]{pressure:>3}%[/]\n[{color}]{word}[/]"
        )

    def clear_meter(self) -> None:
        self.update("[b]QB PRESSURE[/b]\n[dim]\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591  --[/dim]")


class CoachReport(Static):
    """The side panel: plain-English play info + pass-pro result."""

    def show_play(self, play: "data.Play", report: dict) -> None:
        lines = []
        # The verdict is the headline: one sentence on who won the rep.
        lines.append(f"[b]{report.get('verdict', '')}[/b]")
        lines.append("")
        lines.append(f"[b]THE PLAY[/b]  {play.offense} ball vs {play.defense}"
                     + (f"  \u2022  {play.coverage}" if play.coverage else ""))
        lines.append(f"  {_wrap(play.description, 60)}")
        lines.append("")
        lines.append("[b]PASS-PROTECTION DETAILS[/b]")
        lines.append(_report_line("Sacked the QB", report["sacks_by"]))
        lines.append(_report_line("Hit the QB", report["hits_by"]))
        lines.append(_report_line("Hurried the QB", report["hurries_by"]))
        lines.append(_report_line("Gave up a sack", report["sack_allowed_by"]))
        self.update("\n".join(lines))

    def clear_report(self) -> None:
        self.update("[dim]Pick a play from the list to see the coach's read.[/dim]")


class PlayerCard(Static):
    """Everything we know about one player on the current play."""

    def show(self, info: dict) -> None:
        if not info:
            self.clear_card()
            return
        lines = [f"[b]{info['name']}[/b]  ({info['position']})"]
        bio = "  ".join(x for x in (info["height"], info["weight"]) if x)
        if bio:
            lines.append(f"  {bio}")
        if info["college"]:
            lines.append(f"  College: {info['college']}")
        if info["role"]:
            lines.append(f"  This play: {info['role']}  ({info['lined_up']})")
        if info["did"]:
            lines.append("  [magenta]" + ", ".join(info["did"]) + "[/magenta]")
        lines.append(f"  Season sacks: {info['season_sacks']:.0f}")
        self.update("\n".join(lines))

    def clear_card(self) -> None:
        self.update("[dim]Pick a player to see their full card.[/dim]")


class RosterPanel(Static):
    """Both teams' line-ups for this play, color-coded so you can tell at a
    glance who is who. Offense in blue, defense in white, key player magenta."""

    def show(self, rosters: dict) -> None:
        off = rosters["offense"]
        deff = rosters["defense"]
        lines = []
        lines.append(f"[blue b]\u25cf {off['team']} \u2014 OFFENSE[/]")
        for e in off["players"]:
            lines.append(self._row(e, "blue"))
        lines.append("")
        lines.append(f"[b]\u25cf {deff['team']} \u2014 DEFENSE[/]")
        for e in deff["players"]:
            lines.append(self._row(e, "white"))
        self.update("\n".join(lines))

    def _row(self, entry, base_color: str) -> str:
        tag = {
            "sack": " [magenta b]\u25c0 SACK[/]",
            "pressure": " [magenta b]\u25c0 pressure[/]",
            "beaten": " [magenta b]\u25c0 beaten[/]",
        }.get(entry.highlight, "")
        color = "magenta" if entry.highlight else base_color
        num = f"{entry.jersey:>2}"
        return f"  [{color}]#{num}  {entry.name:<18}[/][dim] {entry.position}[/]{tag}"

    def clear_panel(self) -> None:
        self.update("[dim]Pick a play to see both teams' line-ups.[/dim]")


class FilmRoom(App):
    """The main application: game picker, play list, field, and report."""

    # Every panel has a FIXED height so the layout never reflows. Whatever
    # play or player is shown, the field and the buttons stay in exactly the
    # same place -- important for a coach who isn't a computer person.
    CSS = """
    Screen { layout: vertical; }
    #pickers { height: 3; padding: 0 1; }
    #game_select { width: 1fr; }
    #load_data_btn { margin: 0 0 0 2; }
    #add_file_btn { margin: 0 0 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #body { height: 1fr; }
    #left { width: 36; border-right: solid $primary; }
    #center { width: 1fr; }
    #rosters { width: 40; border-left: solid $primary; }
    #roster_title { height: 1; padding: 0 1; text-style: bold; }
    RosterPanel { height: 1fr; padding: 0 1; }
    FieldPlot { height: 1fr; min-height: 16; }
    PressureMeter { height: 3; padding: 0 1; }
    #legend { height: 1; padding: 0 1; }
    #controls { height: 3; padding: 0 1; }
    CoachReport { height: 11; padding: 0 1; border-top: solid $primary; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    Button { margin: 0 1 0 0; }
    #plays_title { height: 1; padding: 0 1; text-style: bold; }
    #player_title { height: 1; padding: 0 1; text-style: bold; }
    #filter_select { height: 3; }
    #play_list { height: 1fr; }
    #player_select { height: 3; }
    PlayerCard { height: 8; padding: 0 1; border-top: solid $primary; }
    """

    BINDINGS = [
        Binding("space", "toggle_play", "Play / Pause"),
        Binding("r", "reset", "Restart play"),
        Binding("right", "next_play", "Next play"),
        Binding("left", "prev_play", "Prev play"),
        Binding("p", "jump_to_pressure", "Jump to pressure"),
        Binding("g", "toggle_whole_game", "Whole-game mode"),
        Binding("l", "leaderboard", "Leaderboard"),
        Binding("d", "load_data", "Load data"),
        Binding("a", "add_file", "Add a game"),
        Binding("h", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    frame_index = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self.games = data.list_games()
        self.current_plays: list[data.Play] = []
        self.frames: list[data.Frame] = []
        self.current_play: data.Play | None = None
        self.play_pos: int = -1        # index into current_plays
        self.whole_game: bool = False  # auto-advance to next play at the end?
        self.peak_frame: int = 0       # frame index of highest QB pressure
        self.scrimmage_x: float | None = None
        self.first_down_x: float | None = None
        self.current_game_id: int | None = None
        self.play_filter: str = "All plays"
        self.timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="pickers"):
            yield Select(
                [(g.label, g.game_id) for g in self.games],
                prompt="1. Pick a game \u25be",
                id="game_select",
            )
            yield Button("\U0001f4c2 Load new data\u2026", id="load_data_btn", variant="primary")
            yield Button("\u2795 Add a game\u2026", id="add_file_btn", variant="primary")
        yield Static(
            "Then click a play on the left \u2190   "
            "SPACE = play/pause   \u2190/\u2192 = prev/next play   "
            "G = watch whole game   L = leaderboard",
            id="help",
        )
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Label("2. PICK A PLAY", id="plays_title")
                yield Select(
                    [(f, f) for f in data.PLAY_FILTERS],
                    value="All plays",
                    allow_blank=False,
                    id="filter_select",
                )
                yield ListView(id="play_list")
                yield Label("3. INSPECT A PLAYER", id="player_title")
                yield Select([], prompt="Pick a player \u25be", id="player_select")
                yield PlayerCard(id="player_card")
            with Vertical(id="center"):
                yield FieldPlot(id="field")
                yield PressureMeter(id="meter")
                yield Static(_legend_text(), id="legend")
                with Horizontal(id="controls"):
                    yield Button("\u25b6 Play", id="play_btn", variant="success")
                    yield Button("\u23f8 Pause", id="pause_btn")
                    yield Button("\u23ee Restart", id="reset_btn")
                    yield Button("\u26a1 Jump to pressure", id="jump_btn", variant="error")
                    yield Button("\u25c0 Prev", id="prev_btn")
                    yield Button("Next \u25b6", id="next_btn")
                    yield Button("\U0001f3c8 Whole game: OFF", id="game_btn", variant="warning")
                yield Static("Pick a game to begin.", id="status")
                yield CoachReport(id="report")
            with Vertical(id="rosters"):
                yield Label("ON THE FIELD", id="roster_title")
                yield RosterPanel(id="roster")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(CoachReport).clear_report()
        self.query_one(PressureMeter).clear_meter()
        self.query_one(PlayerCard).clear_card()
        self.query_one(RosterPanel).clear_panel()
        # Greet the judges with a branded title screen on launch.
        self.push_screen(WelcomeScreen())

    # -- Picking a game or a player ------------------------------------------
    def on_select_changed(self, event: Select.Changed) -> None:
        value = event.value
        sid = event.select.id
        if sid == "game_select":
            if isinstance(value, int):
                self.current_game_id = value
                self._load_game(value)
        elif sid == "player_select":
            # Repopulating via set_options fires a Changed with a non-int
            # "no selection" sentinel; only act on a real player id.
            if isinstance(value, int):
                self._show_player(value)
        elif sid == "filter_select":
            if isinstance(value, str):
                self.play_filter = value
                if self.current_game_id is not None:
                    self._load_game(self.current_game_id)

    def _show_player(self, nfl_id: int) -> None:
        if self.current_play is None:
            return
        info = data.player_info(
            self.current_play.game_id, self.current_play.play_id, nfl_id
        )
        self.query_one(PlayerCard).show(info)

    def _load_game(self, game_id: int) -> None:
        self._stop_timer()  # never leave an old play animating
        self.current_plays = data.list_plays(game_id, play_filter=self.play_filter)
        self.play_pos = -1
        self.frames = []
        self.current_play = None
        play_list = self.query_one("#play_list", ListView)
        # Rebuild the list WITHOUT per-item IDs (that caused the crash on
        # switching games). We track the selection by its position instead.
        play_list.clear()
        for play in self.current_plays:
            play_list.append(ListItem(Label(play.label)))
        self.query_one(CoachReport).clear_report()
        self.query_one(RosterPanel).clear_panel()
        filt = "" if self.play_filter == "All plays" else f" ({self.play_filter})"
        if self.current_plays:
            msg = f"{len(self.current_plays)} plays{filt}. Click one to watch."
        else:
            msg = f"No plays match '{self.play_filter}'. Try another filter."
        self.query_one("#status", Static).update(msg)

    # -- Picking a play ------------------------------------------------------
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # ListView knows the index of the chosen row; use it directly.
        idx = event.list_view.index
        if idx is None or idx >= len(self.current_plays):
            return
        self._load_play_at(idx)

    def _load_play_at(self, pos: int) -> None:
        if not (0 <= pos < len(self.current_plays)):
            return
        self.play_pos = pos
        play = self.current_plays[pos]
        self.current_play = play
        self.frames = data.load_play_frames(play.game_id, play.play_id)
        self.peak_frame = data.peak_pressure_frame(self.frames)
        self.scrimmage_x, self.first_down_x = data.scrimmage_lines(
            play.game_id, play.play_id
        )
        report = data.pass_pro_report(play.game_id, play.play_id)
        self.query_one(CoachReport).show_play(play, report)
        # Fill the player dropdown with this play's 22 players.
        roster = data.players_on_play(play.game_id, play.play_id)
        player_select = self.query_one("#player_select", Select)
        player_select.set_options([(label, nid) for nid, label in roster])
        self.query_one(PlayerCard).clear_card()
        # Fill the two-team roster panel.
        self.query_one(RosterPanel).show(
            data.play_rosters(play.game_id, play.play_id)
        )
        # Keep the list highlight in sync when we move via buttons/arrows.
        self.query_one("#play_list", ListView).index = pos
        self.frame_index = 0
        self._draw_current_frame()

    # -- Animation -----------------------------------------------------------
    def watch_frame_index(self, _old: int, _new: int) -> None:
        self._draw_current_frame()

    def _draw_current_frame(self) -> None:
        if not self.frames or self.current_play is None:
            return
        idx = max(0, min(self.frame_index, len(self.frames) - 1))
        frame = self.frames[idx]
        self.query_one(FieldPlot).draw_frame(
            self.frames,
            idx,
            self.current_play.offense,
            self.current_play.defense,
            self.scrimmage_x,
            self.first_down_x,
        )
        self.query_one(PressureMeter).show(frame.pressure)
        pos_txt = f"Play {self.play_pos + 1} of {len(self.current_plays)}"
        self.query_one("#status", Static).update(
            f"{pos_txt}    \u2022    frame {idx + 1} / {len(self.frames)}"
        )

    def _advance(self) -> None:
        if self.frame_index >= len(self.frames) - 1:
            # End of the play. In whole-game mode, roll into the next play.
            if self.whole_game and self.play_pos < len(self.current_plays) - 1:
                self._load_play_at(self.play_pos + 1)
                return
            self._stop_timer()
            return
        self.frame_index += 1

    def _start_timer(self) -> None:
        if self.timer is None and self.frames:
            # Tracking is 10 frames/sec; 0.1s per frame = real-time replay.
            self.timer = self.set_interval(0.1, self._advance)

    def _stop_timer(self) -> None:
        if self.timer is not None:
            self.timer.stop()
            self.timer = None

    # -- Buttons + key actions ----------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "play_btn":
            self.action_play()
        elif bid == "pause_btn":
            self._stop_timer()
        elif bid == "reset_btn":
            self.action_reset()
        elif bid == "jump_btn":
            self.action_jump_to_pressure()
        elif bid == "prev_btn":
            self.action_prev_play()
        elif bid == "next_btn":
            self.action_next_play()
        elif bid == "game_btn":
            self.action_toggle_whole_game()
        elif bid == "load_data_btn":
            self.action_load_data()
        elif bid == "add_file_btn":
            self.action_add_file()

    def action_play(self) -> None:
        if not self.frames:
            return
        if self.frame_index >= len(self.frames) - 1:
            self.frame_index = 0
        self._start_timer()

    def action_toggle_play(self) -> None:
        if self.timer is not None:
            self._stop_timer()
        else:
            self.action_play()

    def action_reset(self) -> None:
        self._stop_timer()
        self.frame_index = 0

    def action_jump_to_pressure(self) -> None:
        """Skip the dead time: jump straight to the peak-pressure moment."""
        if not self.frames:
            return
        self._stop_timer()
        self.frame_index = self.peak_frame

    def action_next_play(self) -> None:
        if self.play_pos < len(self.current_plays) - 1:
            was_playing = self.timer is not None
            self._load_play_at(self.play_pos + 1)
            if was_playing:
                self.action_play()

    def action_prev_play(self) -> None:
        if self.play_pos > 0:
            was_playing = self.timer is not None
            self._load_play_at(self.play_pos - 1)
            if was_playing:
                self.action_play()

    def action_toggle_whole_game(self) -> None:
        self.whole_game = not self.whole_game
        state = "ON" if self.whole_game else "OFF"
        self.query_one("#game_btn", Button).label = f"\U0001f3c8 Whole game: {state}"
        if self.whole_game:
            # Start from the first play and roll through them all.
            if self.play_pos < 0:
                self._load_play_at(0)
            self.action_play()

    def action_leaderboard(self) -> None:
        self.push_screen(LeaderboardScreen())

    def action_help(self) -> None:
        self.push_screen(WelcomeScreen())

    def action_load_data(self) -> None:
        """Open the coach-proof folder browser to load a new dataset."""
        self._stop_timer()
        self.push_screen(DataLoaderScreen(), self._apply_new_dataset)

    def action_add_file(self) -> None:
        """Open the file browser to add a single game to the current library."""
        self._stop_timer()
        self.push_screen(AddFileScreen(), self._after_add_file)

    def _after_add_file(self, new_game_id: int | None) -> None:
        """Called when the add-file screen closes. None means cancelled."""
        if new_game_id is None:
            return
        # Refresh the game picker so the newly added game appears, and jump to it.
        self.games = data.list_games()
        game_select = self.query_one("#game_select", Select)
        game_select.set_options([(g.label, g.game_id) for g in self.games])
        self.query_one("#status", Static).update(
            f"\u2705 Added a new game. Your library now has {len(self.games)} games."
        )
        game_select.value = new_game_id  # auto-select the freshly added game

    def _apply_new_dataset(self, folder: str | None) -> None:
        """Called when the loader screen closes. folder=None means cancelled."""
        if not folder:
            return
        # Point the data layer at the new folder and rebuild the game picker.
        data.configure(folder)
        self.games = data.list_games()
        game_select = self.query_one("#game_select", Select)
        game_select.set_options([(g.label, g.game_id) for g in self.games])
        # Reset everything to a clean slate on the new data.
        self.current_game_id = None
        self.current_plays = []
        self.current_play = None
        self.frames = []
        self.query_one("#play_list", ListView).clear()
        self.query_one(CoachReport).clear_report()
        self.query_one(PressureMeter).clear_meter()
        self.query_one(PlayerCard).clear_card()
        self.query_one(RosterPanel).clear_panel()
        self.query_one(FieldPlot).plt.clear_figure()
        self.query_one(FieldPlot).refresh()
        self.query_one("#status", Static).update(
            f"Loaded new data: {len(self.games)} game(s) found. Pick a game above."
        )


# ---------------------------------------------------------------------------
# A second screen: the season-wide pass-rush leaderboard.
# ---------------------------------------------------------------------------
class WelcomeScreen(ModalScreen):
    """Branded title + quick how-to. Shown on launch and via the H key."""

    CSS = """
    WelcomeScreen { align: center middle; }
    #welcome { width: 76; height: auto; border: round $accent; background: $surface; padding: 1 3; }
    #title { color: $accent; text-style: bold; text-align: center; padding: 0 0 1 0; }
    """

    BINDINGS = [
        Binding("enter", "close", "Start"),
        Binding("escape", "close", "Start"),
        Binding("space", "close", "Start"),
    ]

    def compose(self) -> ComposeResult:
        body = (
            "[b]Watch any NFL play come to life in your terminal[/b]\n\n"
            "Pick a game, pick a play, and see all 22 players move on the field.\n"
            "The live [magenta]QB Pressure[/magenta] meter shows how close the rush is to\n"
            "the quarterback, computed from real tracking data 10 times a second.\n\n"
            "[b]How to use it[/b]\n"
            "  1. Pick a [b]game[/b] (top dropdown)\n"
            "  2. Click a [b]play[/b] from the list on the left\n"
            "  3. Press [b]\u25b6 Play[/b] (or SPACE) to watch it\n"
            "  4. Hit [red]\u26a1 Jump to pressure[/red] to skip to the key moment\n"
            "  5. Pick a [b]player[/b] to see their full card\n\n"
            "[b]Keys[/b]  SPACE play/pause \u2022 \u2190/\u2192 prev/next play \u2022 "
            "P pressure \u2022 G whole game \u2022 L leaderboard \u2022 H help \u2022 Q quit\n\n"
            "[dim]Data: NFL Big Data Bowl (tracking by NFL Next Gen Stats, scouting by PFF)[/dim]\n"
            "[$accent b]Press ENTER to start \u2192[/]"
        )
        with Vertical(id="welcome"):
            yield Static("\U0001f3c8  C O A C H ' S   F I L M   R O O M  \U0001f3c8", id="title")
            yield Static(body)

    def action_close(self) -> None:
        self.app.pop_screen()


class DataLoaderScreen(ModalScreen):
    """A coach-proof folder browser: click through folders, pick one, and the
    app checks it and loads it. No typing, no paths, no way to crash it.

    Returns the chosen folder path (str) to the caller, or None if cancelled.
    """

    CSS = """
    DataLoaderScreen { align: center middle; }
    #loader { width: 86; height: 34; border: thick $primary; background: $surface; padding: 1 2; }
    #loader_title { text-style: bold; color: $accent; }
    #loader_help { color: $text-muted; height: auto; }
    DirectoryTree { height: 1fr; border: solid $primary; margin: 1 0; }
    #loader_status { height: 3; }
    #loader_buttons { height: 3; align-horizontal: right; }
    #loader_buttons Button { margin: 0 0 0 2; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self) -> None:
        super().__init__()
        # The folder currently highlighted in the tree.
        self.selected: str | None = None

    def compose(self) -> ComposeResult:
        from pathlib import Path
        from textual.widgets import DirectoryTree

        with Vertical(id="loader"):
            yield Static("\U0001f4c2  Load a new dataset", id="loader_title")
            yield Static(
                "Click folders to open them, click the one that holds your "
                "games.csv / players.csv / plays.csv files, then press "
                "\u201cUse this folder\u201d.",
                id="loader_help",
            )
            yield DirectoryTree(str(Path.home()), id="data_tree")
            yield Static(
                "[dim]No folder selected yet.[/dim]", id="loader_status"
            )
            with Horizontal(id="loader_buttons"):
                yield Button("\u2716 Cancel", id="loader_cancel")
                yield Button("\u2713 Use this folder", id="loader_use", variant="success")

    def on_directory_tree_directory_selected(self, event) -> None:
        self.selected = str(event.path)
        self._preview(self.selected)

    def _preview(self, folder: str) -> None:
        """Show, before committing, whether this folder is a valid dataset."""
        problems = data.validate_data_dir(folder)
        status = self.query_one("#loader_status", Static)
        if not problems:
            status.update(f"[green]\u2713 Looks good: {folder}[/green]")
        else:
            issues = "  \u2022  ".join(problems)
            status.update(
                f"[yellow]Selected:[/] {folder}\n[red]Not a dataset yet: {issues}[/red]"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "loader_cancel":
            self.action_cancel()
        elif event.button.id == "loader_use":
            self._try_use()

    def _try_use(self) -> None:
        if not self.selected:
            self.query_one("#loader_status", Static).update(
                "[red]Pick a folder in the list first.[/red]"
            )
            return
        problems = data.validate_data_dir(self.selected)
        if problems:
            # Refuse to load a broken folder; explain why, stay open.
            issues = "  \u2022  ".join(problems)
            self.query_one("#loader_status", Static).update(
                f"[red]Can't use that folder: {issues}[/red]"
            )
            return
        self.dismiss(self.selected)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddFileScreen(ModalScreen):
    """Add ONE game's tracking file to the current library. The coach browses
    to a tracking_<gameId>.csv, the app checks it, copies it in, and the new
    game appears in the picker. Returns the new gameId, or None if cancelled.
    """

    CSS = """
    AddFileScreen { align: center middle; }
    #add { width: 86; height: 34; border: thick $primary; background: $surface; padding: 1 2; }
    #add_title { text-style: bold; color: $accent; }
    #add_help { color: $text-muted; height: auto; }
    DirectoryTree { height: 1fr; border: solid $primary; margin: 1 0; }
    #add_status { height: 3; }
    #add_buttons { height: 3; align-horizontal: right; }
    #add_buttons Button { margin: 0 0 0 2; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self) -> None:
        super().__init__()
        self.selected: str | None = None

    def compose(self) -> ComposeResult:
        from pathlib import Path
        from textual.widgets import DirectoryTree

        with Vertical(id="add"):
            yield Static("\u2795  Add a game to your library", id="add_title")
            yield Static(
                "Browse to a game's tracking file (named like "
                "[b]tracking_2021091211.csv[/b]) and click \u201cAdd this game\u201d. "
                "The app copies it into your current data so you keep it.",
                id="add_help",
            )
            yield DirectoryTree(str(Path.home()), id="file_tree")
            yield Static("[dim]No file selected yet.[/dim]", id="add_status")
            with Horizontal(id="add_buttons"):
                yield Button("\u2716 Cancel", id="add_cancel")
                yield Button("\u2713 Add this game", id="add_use", variant="success")

    def on_directory_tree_file_selected(self, event) -> None:
        self.selected = str(event.path)
        ok, msg = data.check_tracking_file(self.selected)
        status = self.query_one("#add_status", Static)
        color = "green" if ok else "red"
        status.update(f"[{color}]{msg}[/]\n[dim]{self.selected}[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add_cancel":
            self.action_cancel()
        elif event.button.id == "add_use":
            self._try_add()

    def _try_add(self) -> None:
        if not self.selected:
            self.query_one("#add_status", Static).update(
                "[red]Pick a tracking file in the list first.[/red]"
            )
            return
        ok, msg, gid = data.add_tracking_file(self.selected)
        if not ok:
            self.query_one("#add_status", Static).update(f"[red]{msg}[/red]")
            return
        self.dismiss(gid)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LeaderboardScreen(ModalScreen):
    CSS = """
    LeaderboardScreen { align: center middle; }
    #box { width: 72; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    """

    BINDINGS = [Binding("escape", "close", "Close"), Binding("l", "close", "Close")]

    def compose(self) -> ComposeResult:
        rushers = data.top_pass_rushers(stat="pff_sack", top_n=12)
        lines = ["[b]SEASON LEADERBOARD \u2014 Top Pass Rushers (sacks)[/b]", ""]
        best = rushers[0][2] if rushers else 1
        for rank, (name, pos, total) in enumerate(rushers, start=1):
            bar = "\u2588" * int((total / best) * 24) if best else ""
            lines.append(f"{rank:>2}. {name:<20} {pos:<4} {total:>4.0f}  {bar}")
        lines.append("")
        lines.append("[dim]Press Esc or L to close[/dim]")
        with Vertical(id="box"):
            yield Static("\n".join(lines))

    def action_close(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
def _legend_text() -> str:
    """Plain-words legend so nobody has to guess what a dot means."""
    return (
        "[blue b]\u25cf[/] Offense   [b]\u25cf[/] Defense   "
        "[magenta b]\u25cf[/] Key player   [orange1 b]\u25cf[/] Ball   "
        "[blue b]\u2502[/] Scrimmage   [yellow b]\u2502[/] 1st-down line   "
        "[dim](numbers \u2192 names in the roster)[/dim]"
    )


def _yard_marker(field_x: int) -> int:
    """Convert a field x (10=own goal, 110=other goal) to the TV yard number.

    The field runs 0-120 with 10-yard end zones, so the playing field is
    10..110 and the 50-yard line sits at x=60. Numbers mirror at midfield.
    """
    yl = field_x - 10  # 0..100 from the left goal line
    return min(yl, 100 - yl)


def _trail_shade(age: int):
    """Grey for a trail dot. Older (bigger age) = darker -> a comet tail."""
    level = max(60, 200 - age * 20)
    return (level, level, level)


def _report_line(label: str, names: list[str]) -> str:
    if not names:
        return f"  {label}: [dim]\u2014[/dim]"
    return f"  {label}: [magenta]{', '.join(names)}[/magenta]"


def _wrap(text: str, width: int) -> str:
    import textwrap

    return "\n  ".join(textwrap.wrap(text, width)) or text


def _parse_args() -> None:
    """Let a user point the tool at ANY Big Data Bowl dataset folder."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Coach's Film Room - an interactive NFL play viewer."
    )
    parser.add_argument(
        "--data-dir",
        help="Path to a dataset folder (games.csv, players.csv, plays.csv, "
        "pffScoutingData.csv and a tracking/ subfolder). Defaults to ./data.",
    )
    args = parser.parse_args()
    if args.data_dir:
        problems = data.validate_data_dir(args.data_dir)
        if problems:
            print("Cannot use that data folder:")
            for p in problems:
                print(f"  - {p}")
            sys.exit(1)
        data.configure(args.data_dir)


if __name__ == "__main__":
    _parse_args()
    FilmRoom().run()
