"""Productive Music Player - Speed up your music for productivity."""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import vlc
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button, Footer, Header, Label,
    ListItem, ListView, ProgressBar, Static,
)

APP_DIR = Path(__file__).parent
AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".wma", ".opus", ".aiff"}
SPEED_PRESETS = [1.0, 1.1, 1.2, 1.3, 1.5, 2.0]


def scan_audio_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [f for f in sorted(directory.iterdir()) if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]


def format_time(ms: int) -> str:
    if ms <= 0:
        return "00:00"
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


# -- Player -------------------------------------------------------------------


class Player:
    """Audio player backend using VLC with speed control and compressor."""

    def __init__(self) -> None:
        self._instance = vlc.Instance(
            "--no-video",
            "--audio-filter=compressor",
            "--compressor-rms-peak=0",
            "--compressor-attack=20",
            "--compressor-release=200",
            "--compressor-threshold=-20",
            "--compressor-ratio=4",
            "--compressor-knee=2.5",
            "--compressor-makeup-gain=7",
        )
        self._vlc = self._instance.media_player_new()
        self._speed: float = 1.0
        self._volume: int = 80
        self._ended: bool = False
        self._compressor: bool = True
        self._current_path: str | None = None

        em = self._vlc.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached, lambda e: setattr(self, "_ended", True))

    def has_ended(self) -> bool:
        if self._ended:
            self._ended = False
            return True
        return False

    def play(self, path: Path, seek_ms: int = 0) -> None:
        self._ended = False
        self._current_path = str(path)
        media = self._instance.media_new(self._current_path)
        if not self._compressor:
            media.add_option(":compressor-ratio=1")
            media.add_option(":compressor-makeup-gain=0")
        if seek_ms > 0:
            media.add_option(f":start-time={seek_ms / 1000:.2f}")
        self._vlc.set_media(media)
        self._vlc.play()
        self._vlc.audio_set_volume(self._volume)

    def toggle_pause(self) -> None:
        if self.is_playing():
            self._vlc.pause()
        elif self._vlc.get_media() is not None:
            self._vlc.play()
            self._vlc.audio_set_volume(self._volume)

    def is_playing(self) -> bool:
        return self._vlc.is_playing() == 1

    def get_state(self) -> str:
        state_map = {
            vlc.State.NothingSpecial: "stopped",
            vlc.State.Opening: "opening",
            vlc.State.Buffering: "buffering",
            vlc.State.Playing: "playing",
            vlc.State.Paused: "paused",
            vlc.State.Stopped: "stopped",
            vlc.State.Ended: "ended",
            vlc.State.Error: "error",
        }
        return state_map.get(self._vlc.get_state(), "unknown")

    def get_position_ms(self) -> int:
        t = self._vlc.get_time()
        return max(0, t) if t is not None else 0

    def get_duration_ms(self) -> int:
        d = self._vlc.get_length()
        return max(0, d) if d is not None else 0

    def seek_ms(self, ms: int) -> None:
        self._vlc.set_time(ms)

    def seek_relative(self, delta_ms: int) -> None:
        target = max(0, min(self.get_position_ms() + delta_ms, self.get_duration_ms()))
        self._vlc.set_time(target)

    def ensure_rate(self) -> None:
        """Re-apply speed if VLC drifted."""
        if abs(self._vlc.get_rate() - self._speed) > 0.01:
            self._vlc.set_rate(self._speed)

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = round(max(0.25, min(3.0, value)), 2)
        self._vlc.set_rate(self._speed)

    @property
    def compressor(self) -> bool:
        return self._compressor

    def toggle_compressor(self) -> bool:
        self._compressor = not self._compressor
        if self._current_path and self.is_playing():
            self.play(Path(self._current_path), seek_ms=self.get_position_ms())
        return self._compressor

    @property
    def volume(self) -> int:
        return self._volume

    @volume.setter
    def volume(self, value: int) -> None:
        self._volume = max(0, min(150, value))
        self._vlc.audio_set_volume(self._volume)

    def release(self) -> None:
        self._vlc.stop()
        self._vlc.release()
        self._instance.release()


# -- Main App -----------------------------------------------------------------


class ProductiveMusicPlayer(App):
    TITLE = "Productive Music Player"
    SUB_TITLE = "The music player that makes you productive"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("space", "toggle_pause", "Play/Pause", priority=True),
        Binding("right_square_bracket", "speed_up", "Speed +0.05"),
        Binding("left_square_bracket", "speed_down", "Speed -0.05"),
        Binding("right_curly_bracket", "speed_up(0.2)", "Speed +0.2", priority=True),
        Binding("left_curly_bracket", "speed_down(0.2)", "Speed -0.2", priority=True),
        Binding("up", "volume_up", "Vol +", priority=True),
        Binding("down", "volume_down", "Vol -", priority=True),
        Binding("n", "next_track", "Next"),
        Binding("p", "prev_track", "Prev"),
        Binding("s", "toggle_shuffle", "Shuffle"),
        Binding("c", "toggle_compressor", "Compressor"),
        Binding("right", "seek(5000)", "Seek +5s", priority=True),
        Binding("left", "seek(-5000)", "Seek -5s", priority=True),
        *[Binding(str(i + 1), f"preset({i})", f"{p:.1f}x", show=False) for i, p in enumerate(SPEED_PRESETS)],
        Binding("ctrl+q", "quit_app", "Quit", priority=True),
    ]

    def __init__(self, music_dir: Path | None = None) -> None:
        super().__init__()
        self._player = Player()
        self._tracks: list[Path] = []
        self._current_index: int = -1
        self._shuffle: bool = True
        self._shuffle_order: list[int] = []
        self._shuffle_pos: int = 0
        self._initial_dir = music_dir

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="playlist-panel"):
            yield Static("Playlist")
            yield ListView(id="track-list")
        with Vertical(id="now-playing-panel"):
            yield Label("No track loaded", id="track-title")
            yield Label("Stopped", id="state-label")
            with Horizontal(id="status-row"):
                yield Label("Shuffle: ON", id="shuffle-label", classes="-on")
                yield Label("Compressor: ON", id="compressor-label")
            with Horizontal(id="time-row"):
                yield Label("00:00", id="time-current")
                yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="progress-bar")
                yield Label("00:00", id="time-total")
            yield Label("Speed: x1.00", id="speed-display")
            yield Label("Volume: 80%", id="volume-display")
            with Horizontal(id="controls-row"):
                yield Button("Prev", variant="default", id="btn-prev")
                yield Button("Play", variant="primary", id="btn-play")
                yield Button("Next", variant="default", id="btn-next")
        with Horizontal(id="bottom-bar"):
            yield Label("Speed: ")
            for i, preset in enumerate(SPEED_PRESETS):
                yield Button(f"x{preset:.1f}", classes="speed-preset", id=f"speed-preset-{i}")
        yield Static(
            "space=Play/Pause  \\[]/\\[]=Speed  up/down=Vol  n/p=Track  "
            "s=Shuffle  c=Compressor  1-6=Presets  ctrl+q=Quit",
            id="shortcuts-help",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.25, self._update_ui)
        self._update_speed_presets()
        self._update_toggle_label("#compressor-label", self._player.compressor, "Compressor")
        if self._initial_dir and self._initial_dir.is_dir():
            self._load_directory(self._initial_dir)
        else:
            default_dir = APP_DIR / "music"
            if default_dir.is_dir():
                self._load_directory(default_dir)

    # -- UI updates --

    def _update_ui(self) -> None:
        if self._player.has_ended():
            self.action_next_track()
            return

        pos = self._player.get_position_ms()
        dur = self._player.get_duration_ms()
        self.query_one("#time-current", Label).update(format_time(pos))
        self.query_one("#time-total", Label).update(format_time(dur))
        self.query_one("#progress-bar", ProgressBar).update(
            progress=pos * 100 // dur if dur > 0 else 0, total=100
        )

        state = self._player.get_state()
        self.query_one("#state-label", Label).update(
            {"playing": "Playing", "paused": "Paused", "stopped": "Stopped",
             "opening": "Loading..."}.get(state, state.title())
        )
        self.query_one("#btn-play", Button).label = "Pause" if self._player.is_playing() else "Play"

        if self._player.is_playing():
            self._player.ensure_rate()

    def _update_speed_presets(self) -> None:
        for i, preset in enumerate(SPEED_PRESETS):
            btn = self.query_one(f"#speed-preset-{i}", Button)
            if abs(self._player.speed - preset) < 0.01:
                btn.add_class("-active")
            else:
                btn.remove_class("-active")

    def _update_speed_label(self) -> None:
        self.query_one("#speed-display", Label).update(f"Speed: x{self._player.speed:.2f}")
        self._update_speed_presets()

    def _update_toggle_label(self, selector: str, is_on: bool, name: str) -> None:
        label = self.query_one(selector, Label)
        label.update(f"{name}: {'ON' if is_on else 'OFF'}")
        label.add_class("-on") if is_on else label.remove_class("-on")

    def _update_track_info(self) -> None:
        if 0 <= self._current_index < len(self._tracks):
            self.query_one("#track-title", Label).update(self._tracks[self._current_index].stem)
            lv = self.query_one("#track-list", ListView)
            if 0 <= self._current_index < len(lv.children):
                lv.index = self._current_index
        else:
            self.query_one("#track-title", Label).update("No track loaded")

    # -- Shuffle --

    def _reshuffle(self) -> None:
        self._shuffle_order = list(range(len(self._tracks)))
        random.shuffle(self._shuffle_order)
        if (self._current_index >= 0
                and len(self._shuffle_order) > 1
                and self._shuffle_order[0] == self._current_index):
            swap = random.randint(1, len(self._shuffle_order) - 1)
            self._shuffle_order[0], self._shuffle_order[swap] = (
                self._shuffle_order[swap], self._shuffle_order[0]
            )

    # -- Load / Play --

    def _load_directory(self, path: Path) -> None:
        self._tracks = scan_audio_files(path)
        self._reshuffle()
        self._shuffle_pos = 0
        lv = self.query_one("#track-list", ListView)
        lv.clear()
        for track in self._tracks:
            lv.append(ListItem(Label(track.name)))
        if self._tracks:
            self._current_index = self._shuffle_order[0] if self._shuffle else 0
            self._update_track_info()
            self.notify(f"Loaded {len(self._tracks)} tracks from {path.name}")
        else:
            self.notify(f"No audio files found in {path.name}", severity="warning")

    def _play_current(self) -> None:
        if 0 <= self._current_index < len(self._tracks):
            self._player.play(self._tracks[self._current_index])
            self._update_track_info()

    # -- Event handlers --

    @on(ListView.Selected, "#track-list")
    def _track_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one("#track-list", ListView).index
        if idx is not None and 0 <= idx < len(self._tracks):
            self._current_index = idx
            if self._shuffle and idx in self._shuffle_order:
                self._shuffle_pos = self._shuffle_order.index(idx)
            self._play_current()

    @on(Button.Pressed, "#btn-play")
    def _btn_play(self) -> None:
        self.action_toggle_pause()

    @on(Button.Pressed, "#btn-prev")
    def _btn_prev(self) -> None:
        self.action_prev_track()

    @on(Button.Pressed, "#btn-next")
    def _btn_next(self) -> None:
        self.action_next_track()

    @on(Button.Pressed, ".speed-preset")
    def _speed_preset_clicked(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("speed-preset-"):
            idx = int(btn_id.split("-")[-1])
            if 0 <= idx < len(SPEED_PRESETS):
                self._player.speed = SPEED_PRESETS[idx]
                self._update_speed_label()

    # -- Actions (key bindings) --

    def action_toggle_pause(self) -> None:
        if self._player.get_state() == "stopped" and self._tracks:
            if self._current_index < 0:
                self._current_index = 0
            self._play_current()
        else:
            self._player.toggle_pause()

    def action_speed_up(self, step: float = 0.05) -> None:
        self._player.speed += step
        self._update_speed_label()

    def action_speed_down(self, step: float = 0.05) -> None:
        self._player.speed -= step
        self._update_speed_label()

    def action_volume_up(self) -> None:
        self._player.volume += 5
        self.query_one("#volume-display", Label).update(f"Volume: {self._player.volume}%")

    def action_volume_down(self) -> None:
        self._player.volume -= 5
        self.query_one("#volume-display", Label).update(f"Volume: {self._player.volume}%")

    def action_next_track(self) -> None:
        if not self._tracks:
            return
        if self._shuffle:
            self._shuffle_pos += 1
            if self._shuffle_pos >= len(self._shuffle_order):
                self._reshuffle()
                self._shuffle_pos = 0
            self._current_index = self._shuffle_order[self._shuffle_pos]
        else:
            self._current_index = (self._current_index + 1) % len(self._tracks)
        self._play_current()

    def action_prev_track(self) -> None:
        if not self._tracks:
            return
        if self._player.get_position_ms() > 3000:
            self._player.seek_ms(0)
            return
        if self._shuffle:
            self._shuffle_pos = max(0, self._shuffle_pos - 1)
            self._current_index = self._shuffle_order[self._shuffle_pos]
        else:
            self._current_index = (self._current_index - 1) % len(self._tracks)
        self._play_current()

    def action_toggle_shuffle(self) -> None:
        self._shuffle = not self._shuffle
        if self._shuffle:
            self._reshuffle()
            self._shuffle_pos = 0
            if self._current_index in self._shuffle_order:
                self._shuffle_order.remove(self._current_index)
                self._shuffle_order.insert(0, self._current_index)
            self.notify("Shuffle ON")
        else:
            self.notify("Shuffle OFF")
        self._update_toggle_label("#shuffle-label", self._shuffle, "Shuffle")

    def action_toggle_compressor(self) -> None:
        is_on = self._player.toggle_compressor()
        self.notify("Compressor ON" if is_on else "Compressor OFF")
        self._update_toggle_label("#compressor-label", is_on, "Compressor")

    def action_seek(self, delta_ms: int = 5000) -> None:
        self._player.seek_relative(delta_ms)

    def action_preset(self, index: int) -> None:
        if 0 <= index < len(SPEED_PRESETS):
            self._player.speed = SPEED_PRESETS[index]
            self._update_speed_label()

    def action_quit_app(self) -> None:
        self._player.release()
        self.exit()

    def on_unmount(self) -> None:
        self._player.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="Productive Music Player")
    parser.add_argument("directory", nargs="?", default="music", help="Music directory (relative to app dir)")
    args = parser.parse_args()
    ProductiveMusicPlayer(music_dir=APP_DIR / args.directory).run()


if __name__ == "__main__":
    main()
