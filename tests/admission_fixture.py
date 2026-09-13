"""A controllable admission boundary for process/lifecycle tests, without models."""
import threading
from model_admission import AdmissionCancelled


class FixtureAdmission:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = {}
        self.gates = {kind: threading.Event() for kind in ("native", "writer")}
        for gate in self.gates.values():
            gate.set()

    def reserve(self, identity, recipe, kind, cancelled, on_wait=None, writer_settings=None):
        with self.lock:
            self.entries[identity] = {"kind": kind, "admitted": False, "process": None}
        try:
            while not cancelled.is_set():
                if self.gates[kind].is_set():
                    with self.lock:
                        self.entries[identity]["admitted"] = True
                    return {"policy": "test_fixture", "kind": kind}
                if on_wait:
                    on_wait("Waiting for memory")
                cancelled.wait(.02)
            raise AdmissionCancelled()
        except BaseException:
            self.release(identity)
            raise

    def attach(self, identity, process):
        with self.lock:
            self.entries[identity]["process"] = process

    def release(self, identity):
        with self.lock:
            self.entries.pop(identity, None)

    def close(self):
        assert not self.entries, "Model ownership leaked at shutdown"
