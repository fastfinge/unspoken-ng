"""Collapsing a burst of calls into one: a timer-backed debounce.

A generic utility, deliberately not a playback decision: #64 moved it out of
the policy module precisely because nothing about it is about role sounds.
Its one user is the settings panel's live theme preview, which `GlobalPlugin`
wires to `wx.CallLater`. Like the policy module, it takes plain values and
imports no NVDA, so the collapsing itself is testable off-NVDA
(`tests/test_debounce.py`).
"""

from __future__ import annotations


class Debounce:
    """Run `action` once, `delay_ms` after the last call, with the last argument.

    The settings panel applies a sound theme live so the user hears the choice
    while arrowing through the combo box -- which means the hook is called once
    per keypress. `SoundThemeLibrary.load()` decodes fourteen WAVs sample by
    sample in pure Python: about 20 ms for the bundled default, linear in the
    theme's size. Per keypress that is a 20 ms stall on the thread NVDA speaks
    from; once, after the user settles, it is a stall while nothing is being
    announced.

    `schedule(delay_ms, callback)` returns a handle with a `Stop()` method --
    `wx.CallLater`'s contract, which is what `GlobalPlugin` passes, and which a
    fake can satisfy in three lines. Nothing here touches wx, so the collapsing
    itself is testable.

    Not thread-safe, and does not need to be: every caller is NVDA's main
    thread.
    """

    def __init__(self, delay_ms: int, action, schedule):
        self._delay_ms = delay_ms
        self._action = action
        self._schedule = schedule
        self._timer = None
        self._pending: tuple | None = None

    def __call__(self, *args) -> None:
        self._pending = args
        self._stop_timer()
        self._timer = self._schedule(self._delay_ms, self._fire)

    def _fire(self) -> None:
        self._timer = None
        pending, self._pending = self._pending, None
        if pending is not None:
            self._action(*pending)

    def cancel(self) -> None:
        """Drop the pending call, if any. `GlobalPlugin.terminate` calls this."""
        self._stop_timer()
        self._pending = None

    def _stop_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.Stop()
