"""Tests for the settings panel's live-preview adapter."""

import preview


class _RecordingPlayer:
    def __init__(self, *, fail_theme=False, fail_reverb=False):
        self.calls = []
        self.fail_theme = fail_theme
        self.fail_reverb = fail_reverb

    def set_theme(self, sounds):
        self.calls.append(("theme", sounds))
        if self.fail_theme:
            raise RuntimeError("theme failed")

    def set_reverb(self, preset):
        self.calls.append(("reverb", preset))
        if self.fail_reverb:
            raise RuntimeError("reverb failed")


class _StubLibrary:
    def __init__(self, *, fail=False):
        self.loads = []
        self.fail = fail

    def load(self, theme_id):
        self.loads.append(theme_id)
        if self.fail:
            raise RuntimeError("decode failed")
        return {"slot": (theme_id.encode(), 44100)}


class _FakeTimer:
    def __init__(self, delay_ms, callback):
        self.delay_ms = delay_ms
        self.callback = callback
        self.stopped = False

    def Stop(self):
        self.stopped = True


class _FakeScheduler:
    """wx.CallLater's contract, minus wx: schedule, Stop, and fire on demand."""

    def __init__(self):
        self.timers = []

    def __call__(self, delay_ms, callback):
        timer = _FakeTimer(delay_ms, callback)
        self.timers.append(timer)
        return timer

    def fire_latest(self):
        timer = self.timers[-1]
        assert not timer.stopped
        timer.callback()

    @property
    def live(self):
        return [timer for timer in self.timers if not timer.stopped]


def _adapter(*, player=None, themes=None):
    player = player or _RecordingPlayer()
    themes = themes or _StubLibrary()
    scheduler = _FakeScheduler()
    adapter = preview.LivePreview(
        player,
        themes,
        scheduler,
        theme_id="default",
        reverb_preset="none",
    )
    return adapter, player, themes, scheduler


def test_theme_previews_are_collapsed_into_one_decode():
    adapter, player, themes, scheduler = _adapter()

    adapter.preview_theme("retro")
    adapter.preview_theme("marimba")
    adapter.preview_theme("modern")

    assert themes.loads == []
    assert player.calls == []
    scheduler.fire_latest()
    assert themes.loads == ["modern"]
    assert player.calls == [
        ("theme", {"slot": (b"modern", 44100)}),
    ]


def test_reverb_previews_apply_immediately():
    adapter, player, themes, scheduler = _adapter()

    adapter.preview_reverb("hall")

    assert player.calls == [("reverb", "hall")]
    assert themes.loads == []
    assert scheduler.timers == []


def test_previewing_what_is_already_playing_costs_nothing():
    adapter, player, themes, scheduler = _adapter()

    adapter.preview_theme("retro")
    adapter.preview_theme("default")
    scheduler.fire_latest()

    assert themes.loads == []
    assert player.calls == []


def test_revert_cancels_a_pending_preview_and_applies_now():
    adapter, player, themes, scheduler = _adapter()
    adapter.preview_theme("retro")
    pending_timer = scheduler.timers[-1]

    adapter.revert("default2", "hall")

    assert pending_timer.stopped is True
    assert themes.loads == ["default2"]
    assert player.calls == [
        ("theme", {"slot": (b"default2", 44100)}),
        ("reverb", "hall"),
    ]
    pending_timer.callback()
    assert themes.loads == ["default2"]
    assert len(player.calls) == 2


def test_reverting_to_what_is_already_playing_costs_nothing():
    adapter, player, themes, scheduler = _adapter()

    adapter.revert("default", "none")

    assert themes.loads == []
    assert player.calls == []
    assert scheduler.timers == []


def test_a_failed_theme_decode_is_logged_and_does_not_escape(caplog):
    library = _StubLibrary(fail=True)
    adapter, player, themes, scheduler = _adapter(themes=library)

    adapter.preview_theme("retro")
    scheduler.fire_latest()
    adapter.preview_theme("retro")
    scheduler.fire_latest()

    assert themes.loads == ["retro", "retro"]
    assert player.calls == []
    assert caplog.messages.count(
        "Unspoken: could not apply sound theme 'retro'"
    ) == 2


def test_a_failed_reverb_change_is_logged_and_does_not_escape(caplog):
    failing_player = _RecordingPlayer(fail_reverb=True)
    adapter, player, themes, scheduler = _adapter(player=failing_player)

    adapter.preview_reverb("hall")
    adapter.preview_reverb("hall")

    assert player.calls == [
        ("reverb", "hall"),
        ("reverb", "hall"),
    ]
    assert themes.loads == []
    assert scheduler.timers == []
    assert caplog.messages.count(
        "Unspoken: could not apply reverb preset 'hall'"
    ) == 2


def test_close_drops_a_pending_preview_and_goes_inert():
    adapter, player, themes, scheduler = _adapter()
    adapter.preview_theme("retro")
    pending_timer = scheduler.timers[-1]

    adapter.close()
    adapter.preview_theme("modern")
    adapter.preview_reverb("hall")
    adapter.revert("default2", "smallRoom")

    assert pending_timer.stopped is True
    pending_timer.callback()
    assert themes.loads == []
    assert player.calls == []
    assert len(scheduler.timers) == 1


def test_the_adapter_satisfies_the_declared_interface():
    adapter, player, themes, scheduler = _adapter()

    assert isinstance(adapter, preview.Preview)
