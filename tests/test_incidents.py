from datetime import UTC, datetime, timedelta

from app.incidents import State, Transition, next_state, retry_delay, should_warn_tls


def test_single_failure_below_threshold_does_not_alert():
    s, t = next_state(State("up", 0), ok=False, threshold=3)
    assert (s.status, s.consecutive_failures, t) == ("up", 1, Transition.NONE)


def test_goes_down_exactly_at_threshold():
    s, t = next_state(State("up", 2), ok=False, threshold=3)
    assert (s.status, t) == ("down", Transition.WENT_DOWN)


def test_no_repeated_alert_while_down():
    s, t = next_state(State("down", 3), ok=False, threshold=3)
    assert (s.status, s.consecutive_failures, t) == ("down", 4, Transition.NONE)


def test_recovery_alerts_once_and_resets_counter():
    s, t = next_state(State("down", 5), ok=True, threshold=3)
    assert (s.status, s.consecutive_failures, t) == ("up", 0, Transition.RECOVERED)


def test_first_success_from_unknown_is_silent():
    s, t = next_state(State("unknown", 0), ok=True, threshold=3)
    assert (s.status, t) == ("up", Transition.NONE)


def test_new_monitor_that_never_worked_still_alerts():
    s = State("unknown", 0)
    s, _ = next_state(s, ok=False, threshold=2)
    s, t = next_state(s, ok=False, threshold=2)
    assert (s.status, t) == ("down", Transition.WENT_DOWN)


def test_flapping_success_resets_failure_streak():
    s, _ = next_state(State("up", 2), ok=True, threshold=3)
    s, t = next_state(s, ok=False, threshold=3)
    assert (s.consecutive_failures, t) == (1, Transition.NONE)


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_tls_warning_inside_window_only():
    assert should_warn_tls(NOW + timedelta(days=10), None, NOW, warn_days=14)
    assert not should_warn_tls(NOW + timedelta(days=60), None, NOW, warn_days=14)
    assert not should_warn_tls(None, None, NOW, warn_days=14)


def test_tls_warning_fires_once_per_certificate():
    old = NOW + timedelta(days=10)
    assert not should_warn_tls(old, warned_for=old, now=NOW, warn_days=14)
    renewed_but_short = NOW + timedelta(days=5)
    assert should_warn_tls(renewed_but_short, warned_for=old, now=NOW, warn_days=14)


def test_expired_certificate_warns():
    assert should_warn_tls(NOW - timedelta(days=1), None, NOW, warn_days=14)


def test_retry_delay_grows_exponentially_and_is_capped():
    assert [retry_delay(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert retry_delay(50) == 3600
