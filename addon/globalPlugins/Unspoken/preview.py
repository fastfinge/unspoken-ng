"""The settings panel's live preview, and the plugin-side adapter behind it.

The panel is open in front of a user choosing a sound theme by ear, so every
selection change has to be heard. That is the whole of this interface: preview
a theme, preview a reverb preset, put both back when the user cancels.
Everything about *how* -- that decoding a theme is ~20 ms of pure Python on the
thread NVDA speaks from and must be collapsed over a burst of keypresses, that
reverb is a handful of EFX writes and goes straight through, that reapplying
what is already playing is free to skip -- lives in the adapter. The panel
knows none of it.

No NVDA imports: the adapter is handed the Sound Player, the sound theme
library and a `schedule` callable, so the collapsing and the fallbacks are
testable off NVDA (`tests/test_preview.py`).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

try:  # pragma: no cover - package import inside NVDA
    from . import debounce
except ImportError:  # pragma: no cover - bare import under pytest
    import debounce

try:  # pragma: no cover - depends on whether NVDA is hosting us
    from logHandler import log
except ImportError:  # pragma: no cover - off-NVDA (tests, tooling)
    import logging

    log = logging.getLogger(__name__)


#: How long a burst of live-preview keypresses is collapsed over before the
#: sound theme is decoded. Long enough that holding an arrow key through a
#: ten-theme list decodes once, short enough to still feel like a preview.
THEME_PREVIEW_DEBOUNCE_MS = 300


@runtime_checkable
class Preview(Protocol):
    """What the settings panel may ask of the running addon.

    Three calls, all on NVDA's main thread, one per selection change -- that
    is, one per arrow keypress while the user moves through a combo box.
    None returns anything, none raises, and none may block.
    """

    def preview_theme(self, theme_id: str) -> None:
        """Make `theme_id` what the user hears.

        May be collapsed with the calls around it, so the sound follows the
        selection after a short pause rather than per keypress.
        """

    def preview_reverb(self, preset: str) -> None:
        """Make `preset` what the user hears, immediately."""

    def revert(self, theme_id: str, preset: str) -> None:
        """Put both back to what the panel opened, or last saved, showing.

        Called unconditionally on Cancel: the panel says what it wants heard
        and does not decide whether that costs anything, because what is
        already applied is the adapter's knowledge, not the panel's.
        """


class LivePreview:
    """The running addon's `Preview`: the debounce, the player, the library.

    `schedule(delay_ms, callback)` is `wx.CallLater`'s contract (see
    `debounce.Debounce`). `theme_id` and `reverb_preset` are what
    `GlobalPlugin` has already applied at construction, so the first preview
    of something already playing is free.
    """

    def __init__(
        self,
        player,
        themes,
        schedule,
        *,
        theme_id,
        reverb_preset,
        delay_ms=THEME_PREVIEW_DEBOUNCE_MS,
    ):
        self._player = player
        self._themes = themes
        self._applied_theme = theme_id
        self._applied_reverb = reverb_preset
        self._closed = False
        self._pending_theme = debounce.Debounce(
            delay_ms, self._apply_theme, schedule
        )

    def preview_theme(self, theme_id):
        if not self._closed:
            self._pending_theme(theme_id)

    def preview_reverb(self, preset):
        if not self._closed:
            self._apply_reverb(preset)

    def revert(self, theme_id, preset):
        # Cancel, then put both back now: the user has just pressed Cancel,
        # there is nothing left to collapse and no reason to make them wait.
        if self._closed:
            return
        self._pending_theme.cancel()
        self._apply_theme(theme_id)
        self._apply_reverb(preset)

    def close(self):
        """Teardown: drop any pending preview and go inert.

        Inert rather than merely cancelled, because a settings dialog left
        open across a plugin reload still holds a panel bound to this
        adapter.
        """
        self._closed = True
        self._pending_theme.cancel()

    def _apply_theme(self, theme_id):
        # Checked here rather than in `preview_theme`, so arrowing away and
        # back inside the debounce window collapses to nothing instead of
        # skipping the call that would have cancelled the pending one.
        if theme_id == self._applied_theme:
            return
        try:
            self._player.set_theme(self._themes.load(theme_id))
        except Exception:
            log.error(
                f"Unspoken: could not apply sound theme {theme_id!r}",
                exc_info=True,
            )
            return
        self._applied_theme = theme_id

    def _apply_reverb(self, preset):
        if preset == self._applied_reverb:
            return
        try:
            self._player.set_reverb(preset)
        except Exception:
            log.error(
                f"Unspoken: could not apply reverb preset {preset!r}",
                exc_info=True,
            )
            return
        self._applied_reverb = preset
