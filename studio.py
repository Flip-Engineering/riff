#!/usr/bin/python3
"""Launch Riff, a small local music studio for YuE2."""
import argparse
from contextlib import nullcontext
import fcntl
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import threading
from urllib.parse import quote, unquote, urlsplit
import webbrowser

import run as engine
from studio_core import CONTEXT, ROOT, Generator, Store
from reviews import Reviews
from paths import DATA, OUTPUTS
from model_options import schema
from maintenance import Maintenance
from video import VideoExports
from network_access import read_access, request_origin
import platform_support

WEB = ROOT / "web"
TRACK_ROUTE = re.compile(r"/api/tracks/([a-f0-9]{32})(?:/(audio|recipe|visualization|video-exports|lineage))?")
VIDEO_ROUTE = re.compile(r"/api/video-exports/([a-f0-9]{32})(?:/(frames|finish|cancel|download))?")
JOB_ROUTE = re.compile(r"/api/jobs/([a-f0-9]{32})(?:/(cancel|retry|move))?")
PRESET_ROUTE = re.compile(r"/api/presets/([a-z0-9-]+)")
TRACK_REVIEWS_ROUTE = re.compile(r"/api/tracks/([a-f0-9]{32})/reviews")
REVIEW_ROUTE = re.compile(r"/api/reviews/([a-f0-9]{32})(?:/(cancel))?")


def studio_identity(web, version):
    """Identify the shipped page independently of the first state poll."""
    digest = hashlib.sha256(version.encode("utf-8"))
    for file in sorted(path for path in web.rglob("*") if path.is_file()):
        digest.update(file.relative_to(web).as_posix().encode("utf-8") + b"\0")
        file_digest = hashlib.sha256()
        with file.open("rb") as source:
            while block := source.read(64 * 1024):
                file_digest.update(block)
        digest.update(file_digest.digest())
    return {"version": version, "build": digest.hexdigest()}


def byte_range(value, size):
    if not value:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()):
        raise ValueError("Invalid byte range")
    first, last = match.groups()
    if not first:
        length = int(last)
        if length <= 0:
            raise ValueError("Invalid byte range")
        begin, end = max(0, size - length), size - 1
    else:
        begin, end = int(first), min(int(last), size - 1) if last else size - 1
    if begin >= size or begin < 0 or end < begin:
        raise ValueError("Unsatisfiable byte range")
    return begin, end, True


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, generator, reviews=None, maintenance=None):
        self.store, self.generator = store, generator
        self.reviews = reviews
        self.maintenance = maintenance
        self.restart_requested = False
        self.network_access = read_access(store.data_root)
        self.studio_identity = studio_identity(WEB, (ROOT / "VERSION").read_text().strip())
        self.studio_index = (WEB / "index.html").read_text().replace(
            "__RIFF_STUDIO_BUILD__", html.escape(self.studio_identity["build"], quote=True)).encode("utf-8")
        super().__init__(address, Handler)
        self.video_exports = VideoExports(store)
        if maintenance:
            maintenance.video_exports = self.video_exports

    def server_close(self):
        if hasattr(self, "video_exports"):
            self.video_exports.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "Riff/1"

    def log_message(self, format, *args):
        if args and str(args[1] if len(args) > 1 else "") not in ("200", "206"):
            super().log_message(format, *args)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; worker-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    def safe_request(self, mutation=False, content_type="application/json"):
        try:
            request_origin(self.headers, self.client_address[0], self.server.server_port, self.server.network_access)
        except ValueError as error:
            self.json_response(403, {"error": str(error)})
            return False
        if mutation and (self.headers.get("X-Riff-Request", self.headers.get("X-Rill-Request")) != "1" or self.headers.get("Content-Type", "").split(";")[0] != content_type):
            self.json_response(403, {"error": "Use the studio to make this change."})
            return False
        return True

    def json_response(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_json(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request size.")
        # UTF-8 JSON text budget derived from the model's context capacity.
        if size <= 0 or size > CONTEXT * 4:
            raise ValueError("The request is empty or larger than this model's text budget.")
        try:
            payload = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("The studio could not read this request.")
        if not isinstance(payload, dict):
            raise ValueError("Send a recording object.")
        return payload

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.safe_request():
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/state":
                state = self.server.store.snapshot()
                state["studio"] = self.server.studio_identity
                state["live"] = self.server.generator.status()
                state["writing"] = self.server.generator.writing_status()
                state["reviews"] = self.server.reviews.snapshot() if self.server.reviews else []
                state["review_settings"] = self.server.reviews.settings() if self.server.reviews else {"enabled": False}
                state["engine"] = {**platform_support.readiness(), "writer_ready": self.server.generator.writer_ready(),
                                   "model": "YuE2", "precision": "Q4", "local": True, "refinement_controls": schema()}
                if self.server.maintenance:
                    state["maintenance"] = self.server.maintenance.snapshot()
                self.json_response(200, state)
            elif path.startswith("/api/scores/"):
                reference = path[len("/api/scores/"):]
                if not self.server.store.artifacts:
                    raise KeyError("Score not found.")
                self.json_response(200, self.server.store.artifacts.describe(reference))
            elif path.startswith("/api/acoustics/"):
                if not self.server.store.acoustics:
                    raise KeyError("Saved sound not found.")
                self.json_response(200, self.server.store.acoustics.describe(path[len("/api/acoustics/"):]))
            elif path == "/api/health":
                self.json_response(200, {"app": "Riff", "status": "ready"})
            elif path == "/api/capabilities":
                from capabilities import describe
                self.json_response(200, describe())
            elif path == "/api/system" and self.server.maintenance:
                self.json_response(200, self.server.maintenance.snapshot())
            elif path == "/api/system/log" and self.server.maintenance:
                self.send_file(DATA / "setup.log", "text/plain; charset=utf-8")
            elif path == "/api/review/settings" and self.server.reviews:
                self.json_response(200, self.server.reviews.connection())
            elif path == "/api/review/models" and self.server.reviews:
                self.json_response(200, {"models": self.server.reviews.models("refresh" in parsed.query)})
            elif (match := TRACK_REVIEWS_ROUTE.fullmatch(path)) and self.server.reviews:
                self.server.store.track(match[1])
                self.json_response(200, {"reviews": self.server.reviews.list(match[1])})
            elif (match := REVIEW_ROUTE.fullmatch(path)) and not match[2] and self.server.reviews:
                self.json_response(200, self.server.reviews.get(match[1]))
            elif (match := PRESET_ROUTE.fullmatch(path)):
                self.json_response(200, self.server.store.preset(match[1]))
            elif (match := VIDEO_ROUTE.fullmatch(path)):
                if match[2] == "download":
                    file, title = self.server.video_exports.download(match[1])
                    self.send_file(file, "video/mp4", title, ranged=True)
                elif not match[2]:
                    self.json_response(200, self.server.video_exports.get(match[1]))
                else:
                    raise KeyError("Action not found.")
            elif (match := TRACK_ROUTE.fullmatch(path)):
                track_id, action = match.groups()
                track = self.server.store.track(track_id)
                if action == "lineage":
                    self.json_response(200, self.server.store.lineage(track_id))
                elif action == "audio":
                    self.send_file(self.server.store.audio_path(track_id), "audio/wav",
                                   track["title"] + ".wav" if "download" in parsed.query else None, ranged=True)
                elif action == "visualization":
                    self.json_response(200, self.server.video_exports.visualization(track_id))
                elif action == "video-exports":
                    raise KeyError("Action not found.")
                elif action == "recipe":
                    recipe = dict(track["recipe"], title=track["title"], notes=track["notes"])
                    body = json.dumps(recipe, ensure_ascii=False, indent=2).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(track["title"] + ".json", safe=""))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                else:
                    del track["file"]
                    self.json_response(200, track)
            elif (match := JOB_ROUTE.fullmatch(path)) and not match[2]:
                self.json_response(200, self.server.store.job(match[1]))
            else:
                files = {"/": "index.html", "/app.js": "app.js", "/explore.js": "explore.js", "/review.js": "review.js",
                         "/artwork.js": "artwork.js", "/visualizer.js": "visualizer.js", "/video.js": "video.js", "/video-encoder.js": "video-encoder.js", "/lineage.js": "lineage.js", "/style.css": "style.css",
                         "/theme.js": "theme.js", "/suite.css": "suite.css", "/controls.js": "controls.js", "/updates.js": "updates.js", "/acoustics.js": "acoustics.js",
                         "/flip-face.svg": "flip-face.svg", "/score.js": "score.js", "/compare.js": "compare.js",
                         "/vendor/abcjs-basic-min.js": "vendor/abcjs-basic-min.js",
                         "/icon.svg": "icon.svg", "/manifest.webmanifest": "manifest.webmanifest"}
                if path not in files:
                    raise KeyError("Page not found.")
                if path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(self.server.studio_index)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(self.server.studio_index)
                    return
                file = WEB / files[path]
                content_type = mimetypes.guess_type(file)[0] or "application/octet-stream"
                if file.suffix == ".webmanifest":
                    content_type = "application/manifest+json"
                self.send_file(file, content_type)
        except sqlite3.OperationalError:
            self.json_response(503, {"error": "Riff could not access its library. Check available disk space and try again."})
        except KeyError as exc:
            self.json_response(404, {"error": str(exc.args[0])})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, OSError) as exc:
            self.json_response(400, {"error": str(exc)})

    def send_file(self, file, content_type, download=None, ranged=False):
        size = file.stat().st_size
        try:
            begin, end, partial = byte_range(self.headers.get("Range") if ranged else None, size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(max(0, end - begin + 1)))
        self.send_header("Cache-Control", "no-cache" if not ranged else "private, max-age=86400")
        if ranged:
            self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {begin}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(download, safe=""))
        self.end_headers()
        if self.command == "HEAD":
            return
        with file.open("rb") as source:
            source.seek(begin)
            remaining = end - begin + 1
            while remaining > 0:
                block = source.read(min(64 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_POST(self):
        match = VIDEO_ROUTE.fullmatch(unquote(urlsplit(self.path).path))
        if match and match[2] == "frames":
            self.receive_video_frame(match[1])
            return
        self.mutate("POST")

    def receive_video_frame(self, export_id):
        content_type = self.headers.get("Content-Type", "").split(";")[0]
        if content_type not in ("image/png", "video/h264"):
            if not self.safe_request(mutation=True, content_type="image/png"):
                return
            self.json_response(400, {"error": "Send a PNG or encoded H.264 video frame."})
            return
        if not self.safe_request(mutation=True, content_type=content_type):
            return
        try:
            job = self.server.video_exports.get(export_id)
            expected = "video/h264" if job.get("transport", "png") == "h264" else "image/png"
            if content_type != expected:
                raise ValueError("This export expects a different frame path.")
            size = int(self.headers.get("Content-Length", "0"))
            index = int(self.headers.get("X-Riff-Frame", "-1"))
            # A single RGBA frame plus PNG overhead, independent of the music text budget.
            if size <= 0 or size > job["width"] * job["height"] * 4 + 1024 * 1024:
                raise ValueError("Send one complete visualization frame.")
            frame = self.rfile.read(size)
            if len(frame) != size:
                raise ValueError("The visualization frame was interrupted.")
            self.json_response(200, self.server.video_exports.frame(export_id, index, frame))
        except sqlite3.OperationalError:
            self.json_response(503, {"error": "Riff could not access its library. Check available disk space and try again."})
        except KeyError as exc:
            self.json_response(404, {"error": str(exc.args[0])})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, OSError) as exc:
            self.json_response(400, {"error": str(exc)})

    def do_PATCH(self):
        self.mutate("PATCH")

    def do_DELETE(self):
        self.mutate("DELETE")

    def mutate(self, method):
        if not self.safe_request(mutation=True):
            return
        path = unquote(urlsplit(self.path).path)
        try:
            payload = self.read_json()
            if path in ("/api/generations", "/api/plans") and method == "POST":
                with self.server.maintenance.lock if self.server.maintenance else nullcontext():
                    if self.server.restart_requested:
                        raise ValueError("Riff is restarting. Your draft is saved.")
                    if not payload.get("acoustic_source") and not platform_support.readiness()["ready"]:
                        raise ValueError("Open Studio settings to finish setting up the music engine.")
                    if self.server.maintenance and self.server.maintenance.task["status"] == "running" and self.server.maintenance.task.get("action") != "check":
                        raise ValueError("Setup is still running. Your draft is saved.")
                    result = self.server.generator.submit({**payload, "render_mode": "plan" if path == "/api/plans" else payload.get("render_mode", "music")})
                self.json_response(201, result)
            elif path == "/api/system/setup" and method == "POST" and self.server.maintenance:
                self.json_response(202, self.server.maintenance.start("setup", payload))
            elif path == "/api/system/engine" and method == "POST" and self.server.maintenance:
                with self.server.maintenance.lock:
                    if self.server.maintenance.busy() or self.server.maintenance.task["status"] == "running":
                        raise ValueError("Finish the current work before switching engines.")
                    result = platform_support.configure(payload)
                self.json_response(200, result)
            elif path in ("/api/system/check", "/api/system/update") and method == "POST" and self.server.maintenance:
                self.json_response(202, self.server.maintenance.start(path.rsplit("/", 1)[1], payload))
            elif path == "/api/system/cancel" and method == "POST" and self.server.maintenance:
                self.json_response(200, self.server.maintenance.cancel())
            elif path == "/api/system/preferences" and method == "POST" and self.server.maintenance:
                self.json_response(200, self.server.maintenance.configure(payload))
            elif path == "/api/system/restart" and method == "POST" and self.server.maintenance:
                with self.server.maintenance.lock:
                    result = self.server.maintenance.apply_pending()
                    self.server.restart_requested = True
                self.json_response(200, result)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif path == "/api/presets" and method == "POST":
                self.json_response(201, self.server.store.add_preset(payload))
            elif path == "/api/inspiration" and method == "POST":
                with self.server.maintenance.lock if self.server.maintenance else nullcontext():
                    if self.server.restart_requested:
                        raise ValueError("Riff is restarting. Your draft is saved.")
                # The generator admits the writer atomically with its quiesce
                # boundary; never hold the studio lock for the whole AI call.
                self.json_response(200, self.server.generator.inspiration(payload))
            elif path == "/api/composition/revise" and method == "POST":
                with self.server.maintenance.lock if self.server.maintenance else nullcontext():
                    if self.server.restart_requested:
                        raise ValueError("Riff is restarting. Your score is saved.")
                self.json_response(200, self.server.generator.inspiration({**payload, "task": "score", "idea_engine": "openrouter"}))
            elif path == "/api/inspiration/cancel" and method == "POST":
                self.json_response(200, self.server.generator.cancel_writing())
            elif path == "/api/review/settings" and method == "POST" and self.server.reviews:
                self.json_response(200, self.server.reviews.configure(payload))
            elif path == "/api/review/key" and method == "DELETE" and self.server.reviews:
                self.json_response(200, self.server.reviews.remove_key())
            elif (match := TRACK_REVIEWS_ROUTE.fullmatch(path)) and method == "POST" and self.server.reviews:
                with self.server.maintenance.lock if self.server.maintenance else nullcontext():
                    if self.server.restart_requested:
                        raise ValueError("Riff is restarting. Review this recording when the studio opens.")
                    review = self.server.reviews.submit(match[1], payload)
                self.json_response(201, review)
            elif (match := TRACK_ROUTE.fullmatch(path)) and match[2] == "video-exports" and method == "POST":
                with self.server.maintenance.lock if self.server.maintenance else nullcontext():
                    if self.server.restart_requested:
                        raise ValueError("Riff is restarting. Export again when the studio opens.")
                    self.json_response(201, self.server.video_exports.start(match[1], payload))
            elif (match := VIDEO_ROUTE.fullmatch(path)) and method == "POST" and match[2] in ("finish", "cancel"):
                result = (self.server.video_exports.finish(match[1], payload.get("timings"))
                          if match[2] == "finish" else self.server.video_exports.cancel(match[1]))
                self.json_response(200, result)
            elif (match := REVIEW_ROUTE.fullmatch(path)) and match[2] == "cancel" and method == "POST" and self.server.reviews:
                self.json_response(200, self.server.reviews.cancel(match[1]))
            elif (match := PRESET_ROUTE.fullmatch(path)) and method in ("PATCH", "DELETE"):
                if method == "PATCH":
                    self.json_response(200, self.server.store.update_preset(match[1], payload))
                else:
                    self.server.store.delete_preset(match[1])
                    self.json_response(200, {"status": "removed"})
            elif (match := JOB_ROUTE.fullmatch(path)) and method == "POST":
                if match[2] == "move":
                    self.json_response(200, self.server.generator.move_queued(match[1], payload.get("direction")))
                elif match[2] == "cancel":
                    status = self.server.generator.cancel(match[1])
                    self.json_response(200, {"status": status})
                else:
                    raise KeyError("Action not found.")
            elif (match := TRACK_ROUTE.fullmatch(path)) and not match[2] and method == "PATCH":
                self.json_response(200, self.server.store.update_track(match[1], payload))
            else:
                raise KeyError("Action not found.")
        except sqlite3.OperationalError:
            self.json_response(503, {"error": "Riff could not access its library. Check available disk space and try again."})
        except KeyError as exc:
            self.json_response(404, {"error": str(exc.args[0])})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, OSError) as exc:
            self.json_response(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7878)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--check", action="store_true", help="Check application imports without opening the library")
    args = parser.parse_args()
    if args.check:
        print(json.dumps({"app": "Riff", "version": (ROOT / "VERSION").read_text().strip(), "python": sys.version.split()[0]}))
        return
    data_root = DATA
    data_root.mkdir(parents=True, exist_ok=True)
    lock_file = (data_root / "studio.lock").open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            lock_file.seek(0)
            port = json.load(lock_file)["port"]
            if type(port) is not int or not 0 < port < 65536:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            print("Riff is starting. Try opening it again in a moment.", file=sys.stderr)
            lock_file.close()
            return
        lock_file.close()
        url = f"http://127.0.0.1:{port}"
        print(f"Riff is already running at {url}")
        if not args.no_open:
            webbrowser.open(url)
        return
    store = Store(data_root, OUTPUTS)
    store.import_existing()
    generator = Generator(store)
    reviews = maintenance = None
    try:
        reviews = Reviews(store)
        maintenance = Maintenance(generator, reviews)
        server = StudioServer(("127.0.0.1", args.port), store, generator, reviews, maintenance)
    except Exception:
        if maintenance:
            maintenance.close()
        if reviews:
            reviews.close()
        generator.close()
        raise
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    url = f"http://127.0.0.1:{server.server_port}"
    lock_file.seek(0)
    lock_file.truncate()
    json.dump({"port": server.server_port}, lock_file)
    lock_file.flush()
    print(f"Riff is ready at {url}\nPress Control-C to close the studio. Models load only while generating.", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        maintenance.close()
        reviews.close()
        generator.close()
        server.server_close()
        lock_file.close()
    if server.restart_requested:
        launcher = Path(os.environ["RIFF_INSTALL_ROOT"]) / "launcher.py"
        os.execv(sys.executable, [sys.executable, str(launcher), "--no-open", "--port", str(args.port)])


if __name__ == "__main__":
    main()
