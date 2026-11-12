from app.ratelimit import FailureLimiter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_blocks_after_limit_and_unblocks_when_window_passes():
    clock = Clock()
    limiter = FailureLimiter(max_failures=3, window_seconds=60, clock=clock)
    for _ in range(2):
        limiter.record_failure("a")
    assert limiter.retry_after("a") == 0
    limiter.record_failure("a")
    assert 59 <= limiter.retry_after("a") <= 61
    clock.now += 30
    assert 29 <= limiter.retry_after("a") <= 31
    clock.now += 31
    assert limiter.retry_after("a") == 0


def test_clients_are_independent():
    limiter = FailureLimiter(max_failures=1, window_seconds=60, clock=Clock())
    limiter.record_failure("attacker")
    assert limiter.retry_after("attacker") > 0
    assert limiter.retry_after("someone-else") == 0


def test_window_slides_instead_of_resetting_all_at_once():
    clock = Clock()
    limiter = FailureLimiter(max_failures=2, window_seconds=60, clock=clock)
    limiter.record_failure("a")
    clock.now += 50
    limiter.record_failure("a")
    clock.now += 15  # первая неудача вышла из окна, вторая ещё в нём
    assert limiter.retry_after("a") == 0
    limiter.record_failure("a")
    assert limiter.retry_after("a") > 0


def test_memory_is_bounded():
    limiter = FailureLimiter(max_failures=5, window_seconds=60, clock=Clock(), max_clients=100)
    for i in range(1000):
        limiter.record_failure(f"ip-{i}")
    assert len(limiter._failures) == 100
