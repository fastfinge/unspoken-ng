"""Tests for collapsing a burst of live-preview keypresses (issue #38)."""

from debounce import Debounce


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


def test_a_burst_of_calls_collapses_to_one_with_the_last_argument():
    applied = []
    scheduler = _FakeScheduler()
    debounce = Debounce(300, applied.append, scheduler)

    debounce("default")
    debounce("retro")
    debounce("marimba")
    assert applied == []
    assert len(scheduler.live) == 1

    scheduler.fire_latest()
    assert applied == ["marimba"]


def test_the_delay_is_the_one_it_was_given():
    scheduler = _FakeScheduler()
    Debounce(300, lambda theme: None, scheduler)("retro")

    assert scheduler.timers[-1].delay_ms == 300


def test_each_settled_burst_fires_once():
    applied = []
    scheduler = _FakeScheduler()
    debounce = Debounce(300, applied.append, scheduler)

    debounce("retro")
    scheduler.fire_latest()
    debounce("marimba")
    scheduler.fire_latest()

    assert applied == ["retro", "marimba"]


def test_firing_twice_without_a_new_call_does_nothing_twice():
    applied = []
    scheduler = _FakeScheduler()
    debounce = Debounce(300, applied.append, scheduler)

    debounce("retro")
    timer = scheduler.timers[-1]
    timer.callback()
    timer.callback()

    assert applied == ["retro"]


def test_cancel_drops_the_pending_call():
    applied = []
    scheduler = _FakeScheduler()
    debounce = Debounce(300, applied.append, scheduler)

    debounce("retro")
    timer = scheduler.timers[-1]
    debounce.cancel()

    assert timer.stopped is True
    timer.callback()
    assert applied == []


def test_cancelling_an_idle_debounce_is_harmless():
    Debounce(300, lambda theme: None, _FakeScheduler()).cancel()
