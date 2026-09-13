"""Isolated browser fixture: fake credentials, fake provider, short fixture audio."""
import json
import hashlib
import array
import base64
import math
import os
import shutil
import wave
from pathlib import Path
import signal
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
workspace = tempfile.TemporaryDirectory(prefix="riff-settings-")
os.environ["RIFF_HOME"] = workspace.name
os.environ.pop("RIFF_INSTALL_ROOT", None)
from reviews import Reviews
from studio import StudioServer
from studio_core import Generator, Store, PRESETS
from test_reviews import FakeKeychain, fake_listener
from test_studio import fixture_audio, recipe
from maintenance import Maintenance
from paths import MODELS
import platform_support


with tempfile.TemporaryDirectory(prefix="riff-review-browser-") as folder:
    root = Path(folder)
    store = Store(root / "data", root / "outputs")
    audio = store.outputs / "fixture.wav"
    if os.environ.get("RIFF_DEMO_WAV"):
        shutil.copyfile(os.environ["RIFF_DEMO_WAV"], audio)
    else:
        samples = array.array("h")
        for i in range(12 * 48000):
            value = int(900 * math.sin(i * 2 * math.pi * 220 / 48000))
            samples.extend((value, value))
        if sys.byteorder != "little": samples.byteswap()
        with wave.open(str(audio), "wb") as stream:
            stream.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            stream.writeframes(samples.tobytes())
    if os.environ.get("RIFF_MULTI_TRACK_FIXTURE"):
        other = store.outputs / "comparison.wav"
        shutil.copyfile(audio, other)
        store.add_track(other, recipe(title="Playback comparison"), {})
    track_id = store.add_track(audio, recipe(title="Reedlight", steps=19, max_seconds=420, seed="15961",
        style=next(p[2] for p in PRESETS if p[0] == "reed-room"),
        lyrics="[Verse]\nReeds lean low where the silver runs.\nWe carry the quiet into the sun.\nA little light, a little room.\nA song takes shape in the afternoon."), {})
    if os.environ.get("RIFF_PERFORMANCE_FIXTURE"):
        codes = audio.with_suffix(".codes.i32"); codes.write_bytes(bytes([7, 0, 0, 0]) * 300)
        saved = store.track(track_id)["recipe"]
        saved["performance"] = {"frames": 300, "sha256": hashlib.sha256(codes.read_bytes()).hexdigest(), "truncated": False}
        with store.db() as db:
            db.execute("UPDATE tracks SET recipe=? WHERE id=?", (json.dumps(saved), track_id))
    score = "X:1\nT:A small motif\nM:4/4\nL:1/8\nQ:1/4=108\nK:Dm\nD2 F2 A2 G2|F2 E2 D4|\n"
    vocabulary = MODELS / "sidecars/yue2-qwen.tiktoken"
    vocabulary.parent.mkdir(parents=True)
    vocabulary.write_text(base64.b64encode(score.encode()).decode() + " 42\n")
    def command(recipe, output):
        if os.environ.get("RIFF_QUEUE_FIXTURE") and recipe.get("style") == "queue fixture hold":
            return [sys.executable, "-c", "import time\nwhile True: time.sleep(1)"]
        if recipe.get("render_mode") == "performance":
            return [sys.executable, "-c", "import pathlib,sys,json;p=pathlib.Path(sys.argv[1]);p.write_bytes(bytes([7,0,0,0])*300);p.with_suffix('.i32.json').write_text(json.dumps(dict(frames=300,truncated=False)))", str(output.with_suffix(".codes.i32"))]
        if recipe.get("render_mode") == "plan":
            return [sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"tokens\":[42],\"truncated\":false}')", str(output.with_suffix(".plan.json"))]
        return [sys.executable, "-c", "import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2])", str(audio), str(output)]
    original_readiness = platform_support.readiness
    platform_support.readiness = lambda: {**original_readiness(), "ready": True, "binary_ready": True, "missing_models": []}
    generator = Generator(store, command)
    original_inspiration = generator.inspiration
    generator.inspiration = lambda payload: {"abc": score.replace("D2 F2", "E2 G2"), "summary": "A lifted opening phrase", "writer_model": "fixture/composer"} if payload.get("task") == "score" else original_inspiration(payload)
    keys = FakeKeychain()
    reviews = Reviews(store, keys, fake_listener)
    reviews.models_cache = [{"id": "fixture/audio-model", "name": "Fixture listener"}]
    reviews.models = lambda refresh=False: reviews.models_cache
    maintenance = Maintenance(generator, reviews)
    server = StudioServer(("127.0.0.1", 0), store, generator, reviews, maintenance)
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "track_id": track_id}), flush=True)
    try:
        server.serve_forever()
    finally:
        maintenance.close()
        reviews.close()
        generator.close()
        server.server_close()
        workspace.cleanup()
