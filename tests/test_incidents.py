from app.incidents import State, Transition, next_state


def test_single_failure_below_threshold_does_not_alert():
    s, t = next_state(State("up", 0), ok=False, threshold=3)
    assert (s.status, s.consecutive_failures, t) == ("up", 1, Transition.NONE)


def test_goes_down_exactly_at_threshold():
    s = State("up", 2)
    s, t = next_state(s, ok=False, threshold=3)
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


def test_flapping_success_resets_failure_streak():
    s = State("up", 2)
    s, _ = next_state(s, ok=True, threshold=3)
    s, t = next_state(s, ok=False, threshold=3)
    assert (s.consecutive_failures, t) == (1, Transition.NONE)
