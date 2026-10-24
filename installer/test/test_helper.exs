# Tests wait for messages and for state changes with no wall-clock deadline of their own. A stage
# that really hangs is stopped by ExUnit's per-test timeout (60 s by default), so how long a loaded
# host takes can never decide whether a test passes. assert_receive therefore waits beyond that
# timeout instead of its 100 ms default.
ExUnit.start(assert_receive_timeout: :timer.minutes(10))
