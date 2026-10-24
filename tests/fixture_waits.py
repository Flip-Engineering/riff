"""Waits for test fixtures that have no wall-clock deadline.

How long a loaded host takes must not decide whether a test passes. Each wait ends on the
fixture's own signal. When the caller names the threads or processes that produce the signal,
the wait also ends, with a failure, once all of them have exited without producing it. A wait
that really hangs is stopped by the CI job's timeout.
"""
import time

POLL_SECONDS = .01


def running(producer):
    """A threading.Thread or a subprocess.Popen that has not exited yet."""
    if hasattr(producer, "is_alive"):
        return producer.is_alive()
    return producer.poll() is None


def until(predicate, *producers):
    """Return once predicate() is true. Fail if every named producer exits first."""
    while not predicate():
        if producers and not any(running(producer) for producer in producers):
            if predicate():
                return
            raise AssertionError("The fixture stopped before it reached the expected state")
        time.sleep(POLL_SECONDS)


def signalled(event, *producers):
    """Return True once event is set, or False if every named producer exits without setting it."""
    while not event.wait(POLL_SECONDS):
        if producers and not any(running(producer) for producer in producers):
            return event.is_set()
    return True
