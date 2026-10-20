"""Local YuE2 LoRA library and explicit, anonymous Hugging Face browsing.

The studio stores IDs, never client-supplied engine paths. Installed revisions are
immutable; every execution resolves those IDs and verifies both original and
normalized file digests. Network operations use bounded JSON/header reads and
stream model bytes, and never execute repository code or open pickle files.
"""
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

import lora_format
from paths import DATA


ID_PATTERN = r"[0-9a-f]{64}"
REVISION_PATTERN = r"[0-9a-f]{40}"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_README_BYTES = 512 * 1024
MAX_MEDIA_BYTES = 128 * 1024 * 1024
MAX_PROMPT_BYTES = 64 * 1024
CHECKPOINT_EXTENSIONS = {".pt", ".pth", ".bin", ".ckpt", ".pkl", ".pickle", ".gguf"}
MEDIA_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
               ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".opus": "audio/ogg",
               ".mp4": "video/mp4", ".webm": "video/webm"}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value):
        raise ValueError("Choose an installed LoRA from the library.")
    return value


def repository(value):
    if not isinstance(value, str):
        raise ValueError("Enter a Hugging Face model repository, such as creator/model.")
    if value.startswith("https://"):
        url = urlsplit(value)
        if (url.hostname != "huggingface.co" or url.port not in (None, 443) or url.username
                or url.password or url.query or url.fragment):
            raise ValueError("Use a huggingface.co model repository URL.")
        value = url.path.strip("/")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value)
            or ".." in value or "--" in value or value.endswith((".git", "."))):
        raise ValueError("Enter a valid Hugging Face model repository: creator/model.")
    return value


def revision(value, pinned=False):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("Choose a valid model revision.")
    if pinned:
        if not re.fullmatch(REVISION_PATTERN, value):
            raise ValueError("Model files must use a pinned Hugging Face commit revision.")
    elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", value) or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("Choose a valid model revision.")
    return value


def hub_path(value):
    if (not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value) or "%" in value
            or value.startswith("/") or any(p in ("", ".", "..") for p in value.split("/"))
            or ":" in value or "?" in value or "#" in value):
        raise ValueError("Choose a valid repository file path.")
    return value


def hf_url(value):
    """Validate original requests and each redirect before opening a connection."""
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid Hugging Face download URL.") from exc
    if (parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443)
            or parsed.fragment or not host
            or not (host == "huggingface.co" or host.endswith(".huggingface.co")
                    or host == "hf.co" or host.endswith(".hf.co"))):
        raise ValueError("Model requests and redirects must remain on Hugging Face HTTPS hosts.")
    return value


def resolve_url(repo, commit, path):
    return ("https://huggingface.co/" + repository(repo) + "/resolve/"
            + quote(revision(commit, pinned=True), safe="") + "/" + quote(hub_path(path), safe="/"))


def strength(value, label="LoRA strength"):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + " must be a finite number.")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(label + " must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(label + " must be a finite number.")
    if abs(number) > 3.4028234663852886e38:
        raise ValueError(label + " is outside the engine's finite F32 range.")
    return number


def _string(value, field, limit, default=""):
    if value is None:
        value = default
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(field + " is invalid or too long.")
    return value.strip()


def metadata_fields(value):
    if not isinstance(value, dict):
        raise ValueError("Send LoRA metadata as an object.")
    result = {}
    for key, limit in (("name", 200), ("notes", 20000), ("license", 2000)):
        if key in value:
            result[key] = _string(value[key], key, limit)
    for key in ("tags", "trigger_words"):
        if key in value:
            entries = value[key]
            if isinstance(entries, str):
                entries = [part.strip() for part in entries.split(",") if part.strip()]
            if not isinstance(entries, list) or len(entries) > 100:
                raise ValueError(key + " must be a list of up to 100 short phrases.")
            result[key] = list(dict.fromkeys(_string(item, key, 200) for item in entries if item))
    if "default_strength" in value:
        result["default_strength"] = strength(value["default_strength"])
    return result


class _HFRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        hf_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Cancelled(ValueError):
    pass


class HubUnavailable(ValueError):
    """A connection/access failure does not establish model incompatibility."""
    pass


class HuggingFace:
    """Small Hub client; opener injection keeps tests completely offline."""
    def __init__(self, opener=None):
        self.opener = opener or build_opener(_HFRedirects())

    def open(self, url, headers=None):
        request = Request(hf_url(url), headers={"User-Agent": "Riff-LoRA-Library/1", **(headers or {})})
        try:
            response = self.opener.open(request, timeout=30)
            hf_url(response.geturl())
            return response
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise HubUnavailable("This model requires Hugging Face access or acceptance of its terms; public anonymous models are supported.") from exc
            if exc.code == 404:
                raise HubUnavailable("That Hugging Face repository, revision or file was not found.") from exc
            raise HubUnavailable("Hugging Face returned HTTP " + str(exc.code) + ". Try again later.") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise HubUnavailable("Could not reach Hugging Face. Check the connection and try again.") from exc

    def read(self, url, limit, headers=None):
        with self.open(url, headers) as response:
            result = response.read(limit + 1)
            if len(result) > limit:
                raise ValueError("Hugging Face response exceeded the allowed size.")
            return result, dict(response.headers)

    def json(self, url):
        raw, headers = self.read(url, MAX_JSON_BYTES)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Hugging Face returned invalid JSON.") from exc
        return value, headers

    def model(self, repo, ref="main"):
        url = "https://huggingface.co/api/models/" + repository(repo) + "/revision/" + quote(revision(ref), safe="") + "?blobs=true"
        model, _ = self.json(url)
        if not isinstance(model, dict):
            raise ValueError("Hugging Face returned invalid model details.")
        revision(model.get("sha"), pinned=True)
        if re.fullmatch(REVISION_PATTERN, ref) and model["sha"] != ref:
            raise ValueError("Hugging Face returned a different revision from the requested commit.")
        if model.get("private") or model.get("gated"):
            raise ValueError("This model is private or gated. The browser currently supports public anonymous models.")
        return model

    def header(self, repo, commit, path, size=None):
        url = resolve_url(repo, commit, path)
        # Some mirrors ignore Range. Read only the needed bytes and close early.
        with self.open(url, {"Range": "bytes=0-7"}) as response:
            first = response.read(8)
        if len(first) != 8:
            raise ValueError("Safetensors file is truncated.")
        length = struct.unpack("<Q", first)[0]
        if not 2 <= length <= lora_format.MAX_HEADER_BYTES or (size and 8 + length > size):
            raise ValueError("Safetensors header length is invalid.")
        with self.open(url, {"Range": "bytes=8-" + str(7 + length)}) as response:
            if response.status == 206:
                match = re.fullmatch(r"bytes 8-(\d+)/(\d+|\*)", response.headers.get("Content-Range", ""))
                if not match or int(match[1]) != length + 7:
                    raise ValueError("Hugging Face returned an incorrect header byte range.")
                raw = response.read(length)
            else:
                if response.read(8) != first:
                    raise ValueError("Model header changed during inspection.")
                raw = response.read(length)
        if len(raw) != length:
            raise ValueError("Safetensors header is truncated.")
        header = lora_format.json_object(raw)
        lora_format.validate_header(header, None if size is None else size - length - 8)
        return header

    def search(self, query="", limit=20, sort="downloads", genre="", page=0):
        query = _string(query, "Search", 200)
        genre = _string(genre, "Genre", 100)
        if type(limit) is not int or limit < 1 or type(page) is not int or page < 0:
            raise ValueError("Choose a positive search page size and a nonnegative page.")
        if sort not in ("downloads", "likes", "lastModified", "last_modified", "trendingScore"):
            raise ValueError("Sort models by downloads, likes, trending or recently updated.")
        sort = "lastModified" if sort == "last_modified" else sort
        desired = (page + 1) * limit + 1
        strategies = [
            {"filter": "base_model:adapter:m-a-p/YuE2-3B"},
            {"filter": "base_model:finetune:m-a-p/YuE2-3B"},
            {"filter": "base_model:adapter:Comfy-Org/YuE2"},
            {"search": "YuE2"},
        ]
        terms = (query + " " + genre).lower().split()
        def matches(model):
            if not isinstance(model, dict):
                return False
            repo = model.get("id", model.get("modelId", ""))
            text = canonical([repo, model.get("tags"), model.get("cardData"), model.get("siblings")]).lower()
            return (("lora" in text or "base_model:adapter:" in text)
                    and all(term in text for term in terms))
        def search_one(strategy):
            params = {"full": "true", "cardData": "true", "sort": sort, "direction": -1,
                      "limit": min(max(30, limit), 100), **strategy}
            url = "https://huggingface.co/api/models?" + urlencode(params)
            found, seen, matching = [], set(), set()
            while url and len(matching) < desired:
                if url in seen:
                    break
                seen.add(url)
                values, headers = self.json(url)
                if not isinstance(values, list):
                    raise ValueError("Hugging Face returned invalid model search results.")
                found.extend(values)
                matching.update(model.get("id", model.get("modelId", "")) for model in values if matches(model))
                link = headers.get("Link", headers.get("link", ""))
                match = re.search(r'<([^>]+)>;\s*rel="next"', link)
                url = match[1] if match else None
                if url:
                    hf_url(url)
                    if urlsplit(url).hostname != "huggingface.co" or urlsplit(url).path != "/api/models":
                        raise ValueError("Invalid Hugging Face pagination link.")
            return found
        combined = {}
        with ThreadPoolExecutor(max_workers=4) as workers:
            for values in workers.map(search_one, strategies):
                for model in values:
                    if not matches(model):
                        continue
                    repo = model.get("id", model.get("modelId", ""))
                    try:
                        repository(repo)
                    except ValueError:
                        continue
                    card = model.get("cardData") or {}
                    tags = model.get("tags") or []
                    combined[repo] = {"repo": repo, "name": repo.rsplit("/", 1)[-1],
                        "downloads": model.get("downloads", 0), "likes": model.get("likes", 0),
                        "trendingScore": model.get("trendingScore", 0),
                        "updated_at": model.get("lastModified", model.get("last_modified")),
                        "license": card.get("license", "") if isinstance(card, dict) else "",
                        "tags": tags, "url": "https://huggingface.co/" + repo,
                        "compatibility": "inspect-required"}
        key = "updated_at" if sort == "lastModified" else sort
        ordered = sorted(combined.values(), key=lambda m: (m.get(key) or ("" if key == "updated_at" else 0), m["repo"]), reverse=True)
        start = page * limit
        return {"models": ordered[start:start + limit], "page": page, "limit": limit,
                "has_more": len(ordered) > start + limit, "query": query, "genre": genre, "sort": sort}


def _remote_file(value):
    if not isinstance(value, dict):
        raise ValueError("Hugging Face returned an invalid file descriptor.")
    path = hub_path(value.get("rfilename", value.get("path")))
    lfs = value.get("lfs") or {}
    if not isinstance(lfs, dict):
        raise ValueError("Hugging Face returned invalid file checksum metadata.")
    size = lfs.get("size", value.get("size"))
    if size is not None and (type(size) is not int or size < 0):
        raise ValueError("Hugging Face returned an invalid file size.")
    sha = lfs.get("sha256", value.get("sha256"))
    blob = value.get("blobId", value.get("oid"))
    if sha is not None and (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
        raise ValueError("Hugging Face returned an invalid file checksum.")
    if blob is not None and (not isinstance(blob, str) or not re.fullmatch(r"[0-9a-f]{40}", blob)):
        blob = None
    return {"path": path, "size": size, "sha256": sha, "git_blob": blob}


def _verified_download(path, file, cancel=None):
    _, size = lora_format.regular_file(path)
    if size != file.get("size"):
        return False
    if file.get("sha256"):
        return lora_format.sha256_file(path, cancel) == file["sha256"]
    if file.get("git_blob"):
        digest = hashlib.sha1(("blob " + str(size) + "\0").encode())
        with Path(path).open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                if cancel:
                    cancel()
                digest.update(chunk)
        return digest.hexdigest() == file["git_blob"]
    return False


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(canonical(value) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class LoRALibrary:
    def __init__(self, root=None, references=None, hub=None, cache_ttl=None,
                 cache_entries=64, cache_bytes=32 * 1024 * 1024):
        self.root = Path(root) if root is not None else DATA / "loras"
        self.references = references or (lambda identifier: [])
        self.hub = hub or HuggingFace()
        self.lock = threading.RLock()
        self.tasks = {}
        self.events = {}
        self.threads = {}
        self.download_gate = threading.Lock()
        try:
            ttl = float(os.environ.get("RIFF_LORA_CACHE_SECONDS", "300") if cache_ttl is None else cache_ttl)
        except (TypeError, ValueError) as exc:
            raise ValueError("LoRA Hub cache lifetime must be a nonnegative number of seconds.") from exc
        if isinstance(cache_ttl, bool) or not math.isfinite(ttl) or ttl < 0:
            raise ValueError("LoRA Hub cache lifetime must be a nonnegative number of seconds.")
        if type(cache_entries) is not int or cache_entries < 0 or type(cache_bytes) is not int or cache_bytes < 0:
            raise ValueError("LoRA Hub cache bounds must be nonnegative whole numbers.")
        self.cache_ttl, self.cache_entries, self.cache_bytes = ttl, cache_entries, cache_bytes
        self.cache = OrderedDict()
        self.cache_size = 0

    def _cached(self, key, loader):
        if not self.cache_ttl or not self.cache_entries or not self.cache_bytes:
            return loader()
        with self.lock:
            existing = self.cache.get(key)
            if existing:
                if existing[0] > time.monotonic():
                    self.cache.move_to_end(key)
                    return json.loads(existing[1])
                self.cache_size -= len(existing[1])
                del self.cache[key]
        value = loader()
        encoded = canonical(value).encode("utf-8")
        if len(encoded) > self.cache_bytes:
            return value
        with self.lock:
            if key in self.cache:
                self.cache_size -= len(self.cache.pop(key)[1])
            self.cache[key] = (time.monotonic() + self.cache_ttl, encoded)
            self.cache_size += len(encoded)
            while len(self.cache) > self.cache_entries or self.cache_size > self.cache_bytes:
                _, removed = self.cache.popitem(last=False)
                self.cache_size -= len(removed[1])
        return json.loads(encoded)

    def _model(self, repo, ref):
        return self._cached(("model", repo, ref), lambda: self.hub.model(repo, ref))

    def _directory(self, value):
        result = self.root / identifier(value)
        if result.is_symlink():
            raise ValueError("LoRA library entries cannot be symbolic links.")
        return result

    def _file(self, directory, relative):
        hub_path(relative)
        path = directory / relative
        current = path
        while current != directory:
            if current.is_symlink():
                raise ValueError("LoRA library files cannot be symbolic links.")
            current = current.parent
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Adapter file escapes its installed library entry.")
        return path

    def _identity(self, value):
        files = value["files"]
        return {"schema": value["schema"], "base_model": value["base_model"], "stages": value["stages"],
                "source": value["source"], "files": files}

    def get(self, value):
        with self.lock:
            path = self._directory(value) / "manifest.json"
            try:
                _, size = lora_format.regular_file(path)
                if size > MAX_JSON_BYTES:
                    raise ValueError("Installed LoRA manifest is oversized.")
                manifest = lora_format.json_object(path.read_bytes())
                expected = hashlib.sha256(canonical(self._identity(manifest)).encode()).hexdigest()
                if manifest.get("id") != value or expected != value:
                    raise ValueError("Installed LoRA provenance has changed; import the adapter again.")
            except (KeyError, TypeError, OSError) as exc:
                raise ValueError("Installed LoRA is missing or its manifest is invalid.") from exc
            return manifest

    def list(self):
        if not self.root.exists():
            return []
        items = []
        for directory in self.root.iterdir():
            if re.fullmatch(ID_PATTERN, directory.name):
                try:
                    value = self.get(directory.name)
                    value["references"] = list(self.references(directory.name))
                    items.append(value)
                except ValueError as exc:
                    items.append({"id": directory.name, "name": "Unavailable adapter", "error": str(exc),
                                  "stages": [], "files": [], "tags": []})
        return sorted(items, key=lambda item: (item.get("name", "").casefold(), item["id"]))

    def import_local(self, paths, metadata=None, source=None, cancel=None):
        metadata = metadata_fields(metadata or {})
        if source is not None:
            if not isinstance(source, dict) or not isinstance(source.get("files"), list):
                raise ValueError("Adapter source needs a list of original filenames.")
            for original in source["files"]:
                hub_path(original)
        explicit_roles = paths if isinstance(paths, dict) else None
        if explicit_roles is not None:
            if not set(paths) <= {"ar", "nar"}:
                raise ValueError("Adapter roles must be AR or NAR.")
            paths = list(paths.values())
        if not isinstance(paths, (tuple, list)) or not 1 <= len(paths) <= 2:
            raise ValueError("Import one AR adapter, one NAR adapter, or an AR/NAR pair.")
        for path in paths:
            lora_format.read_header(path)
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / (".import-" + uuid.uuid4().hex)
        staging.mkdir()
        files, stages = [], set()
        try:
            for index, path in enumerate(paths):
                if cancel:
                    cancel()
                path = Path(path)
                original_name = _string(path.name, "Adapter filename", 255)
                # Browser uploads may use a random temporary filename. Metadata
                # from the upload can preserve names through `source.files`.
                if source and source.get("files") and index < len(source["files"]):
                    original_name = PurePosixPath(source["files"][index]).name
                copy = staging / ("original-" + str(index) + ".safetensors")
                with path.open("rb") as incoming, copy.open("xb") as outgoing:
                    while chunk := incoming.read(1024 * 1024):
                        if cancel:
                            cancel()
                        outgoing.write(chunk)
                config = None
                config_sha = None
                config_name = None
                config_path = path.parent / "adapter_config.json"
                header, _ = lora_format.read_header(copy)
                needs_config = any(name.endswith((".lora_A.weight", ".lora_A.default.weight")) for name in header)
                if needs_config and config_path.exists():
                    _, size = lora_format.regular_file(config_path)
                    if size > MAX_README_BYTES:
                        raise ValueError("PEFT configuration is too large.")
                    config = lora_format.json_object(config_path.read_bytes())
                    config_name = "config-" + str(index) + ".json"
                    (staging / config_name).write_text(canonical(config) + "\n")
                    config_sha = lora_format.sha256_file(staging / config_name, cancel)
                info = lora_format.inspect_header(header, config)
                if stages.intersection(info["stages"]):
                    raise ValueError("Only one adapter for each YuE2 stage can be active. Import alternatives separately.")
                if explicit_roles and (len(info["stages"]) != 1 or str(explicit_roles.get(info["stage"])) != str(path)):
                    raise ValueError("Selected adapter role differs from its actual tensor targets.")
                original_file = copy.name
                original_sha = lora_format.sha256_file(copy, cancel)
                for stage in info["stages"]:
                    stages.add(stage)
                    engine_file = stage + ".safetensors"
                    normalized = lora_format.normalize(copy, staging / engine_file, config, cancel, stage=stage)
                    files.append({"stage": stage, "original_name": original_name, "original_file": original_file,
                        "sha256": original_sha, "bytes": copy.stat().st_size,
                        "engine_file": engine_file, "engine_sha256": normalized["engine_sha256"],
                        "engine_bytes": normalized["engine_bytes"], "targets": normalized["targets"], "rank": normalized["rank"],
                        "format": normalized["format"], "projection_mode": normalized["projection_mode"],
                        "adapter_bytes": normalized["adapter_bytes"], "merge_scratch_bytes": normalized["merge_scratch_bytes"],
                        "metadata": normalized["metadata"], "config_file": config_name, "config_sha256": config_sha})
            files.sort(key=lambda entry: entry["stage"])
            if source is None:
                source = {"type": "local", "files": list(dict.fromkeys(f["original_name"] for f in files))}
            elif source.get("type") == "huggingface":
                source = {"type": "huggingface", "repo": repository(source.get("repo")),
                          "revision": revision(source.get("revision"), pinned=True),
                          "files": [hub_path(p) for p in source.get("files", [])]}
            elif source.get("type") == "local":
                source = {"type": "local", "files": [hub_path(p) for p in source.get("files", [])]}
            else:
                raise ValueError("Unsupported adapter provenance source.")
            manifest = {"schema": 1, "base_model": lora_format.BASE_MODEL, "source": source,
                "files": files, "stages": sorted(stages), "name": Path(files[0]["original_name"]).stem,
                "notes": "", "license": "", "tags": [], "trigger_words": [], "default_strength": 1.0,
                "created_at": utc_now(), **metadata}
            if not manifest["name"]:
                raise ValueError("Give the adapter a name.")
            manifest["id"] = hashlib.sha256(canonical(self._identity(manifest)).encode()).hexdigest()
            manifest["bytes"] = sum(p.stat().st_size for p in staging.iterdir() if p.is_file())
            manifest["adapter_bytes"] = sum(f["adapter_bytes"] for f in files)
            manifest["merge_scratch_bytes"] = max(f["merge_scratch_bytes"] for f in files)
            _atomic_json(staging / "manifest.json", manifest)
            if cancel:
                cancel()
            with self.lock:
                destination = self._directory(manifest["id"])
                if destination.exists():
                    return self.get(manifest["id"])
                staging.rename(destination)
            return manifest
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def update(self, value, metadata):
        allowed = {"name", "notes", "license", "tags", "trigger_words", "default_strength"}
        if not isinstance(metadata, dict) or set(metadata) - allowed:
            raise ValueError("Only the adapter's name, notes, license, tags, trigger words and default strength are editable.")
        changes = metadata_fields(metadata)
        if "name" in changes and not changes["name"]:
            raise ValueError("Give the adapter a name.")
        with self.lock:
            result = self.get(value)
            result.update(changes)
            _atomic_json(self._directory(value) / "manifest.json", result)
            return result

    def delete(self, value, confirm=False):
        if type(confirm) is not bool:
            raise ValueError("Deletion confirmation must be true or false.")
        with self.lock:
            self.get(value)
            references = list(self.references(value))
            active = [r for r in references if isinstance(r, dict) and r.get("kind") == "job"
                      and r.get("status") in ("queued", "waiting", "running", "starting", "loading", "cancelling")]
            if active:
                raise ValueError("This LoRA is used by queued or running takes. Finish or cancel those takes before deleting it.")
            if references and not confirm:
                return {"deleted": False, "confirmation_required": True, "references": references,
                        "message": "Saved takes reference this LoRA. Their recipes remain, but reruns will require reimporting these exact files."}
            path = self._directory(value)
            tombstone = self.root / (".delete-" + uuid.uuid4().hex)
            path.rename(tombstone)
            shutil.rmtree(tombstone)
            return {"deleted": True, "id": value, "references": references}

    def _verify(self, manifest):
        directory = self._directory(manifest["id"])
        for item in manifest["files"]:
            for path_key, hash_key in (("original_file", "sha256"), ("engine_file", "engine_sha256"),
                                       ("config_file", "config_sha256")):
                if not item.get(path_key):
                    continue
                path = self._file(directory, item[path_key])
                if lora_format.sha256_file(path) != item[hash_key]:
                    raise ValueError("LoRA file has changed or is corrupt: " + item["original_name"] + ". Import the original adapter again.")

    def resolve(self, selection):
        if selection in (None, []):
            return {"loras": [], "memory_bytes": 0, "adapter_bytes": 0, "merge_scratch_bytes": 0}
        if not isinstance(selection, list) or not 1 <= len(selection) <= 2:
            raise ValueError("Choose up to one AR adapter and one NAR adapter.")
        result = {"loras": [], "adapter_bytes": 0, "merge_scratch_bytes": 0}
        used = set()
        with self.lock:
            for item in selection:
                if not isinstance(item, dict) or set(item) - {"id", "strength", "ar_scale", "nar_scale"}:
                    raise ValueError("Choose adapters by library ID and finite strength.")
                manifest = self.get(item.get("id"))
                scale = strength(item.get("strength", manifest["default_strength"]))
                scales = {stage: strength(item.get(stage + "_scale", scale)) for stage in manifest["stages"]}
                if any(stage + "_scale" in item and stage not in scales for stage in ("ar", "nar")):
                    raise ValueError("A strength override requires an adapter for that stage.")
                active_stages = {stage for stage, number in scales.items() if number != 0}
                if used.intersection(active_stages):
                    raise ValueError("Only one adapter can affect each YuE2 stage; stacking is not supported.")
                used.update(active_stages)
                self._verify(manifest)
                provenance = {"id": manifest["id"], "name": manifest["name"], "source": manifest["source"],
                    "base_model": manifest["base_model"], "stages": manifest["stages"],
                    "strength": scale, "license": manifest["license"],
                    "files": [{key: file[key] for key in ("stage", "sha256", "engine_sha256", "original_name")}
                              for file in manifest["files"]], **{stage + "_scale": number for stage, number in scales.items()}}
                for file in manifest["files"]:
                    stage = file["stage"]
                    if scales[stage] != 0:
                        result[stage + "_lora"] = str(self._file(self._directory(manifest["id"]), file["engine_file"]).resolve())
                        result[stage + "_lora_scale"] = scales[stage]
                        result["adapter_bytes"] += file["adapter_bytes"]
                        result["merge_scratch_bytes"] = max(result["merge_scratch_bytes"], file["merge_scratch_bytes"])
                result["loras"].append(provenance)
        result["memory_bytes"] = result["adapter_bytes"] + result["merge_scratch_bytes"]
        return result

    def resolve_saved(self, provenance):
        if not isinstance(provenance, list):
            raise ValueError("Saved LoRA provenance must be a list.")
        selections = []
        for item in provenance:
            if not isinstance(item, dict):
                raise ValueError("Saved LoRA provenance is invalid.")
            if any(key not in item for key in ("id", "source", "stages", "files", "strength")):
                raise ValueError("Saved LoRA provenance is incomplete.")
            if not isinstance(item["stages"], list) or any(stage not in ("ar", "nar") for stage in item["stages"]):
                raise ValueError("Saved LoRA stages are invalid.")
            selected = {key: item[key] for key in ("id", "strength", "ar_scale", "nar_scale") if key in item}
            if any(stage + "_scale" not in selected for stage in item["stages"]):
                raise ValueError("Saved LoRA stage strength is missing.")
            selections.append(selected)
        resolved = self.resolve(selections)
        for saved, current in zip(provenance, resolved["loras"]):
            for key in ("id", "source", "stages", "files", "strength", "ar_scale", "nar_scale"):
                if saved.get(key) != current.get(key):
                    raise ValueError("Saved LoRA provenance differs from the installed revision; reimport the exact original adapter.")
            if "base_model" in saved and saved["base_model"] != current["base_model"]:
                raise ValueError("Saved LoRA base model differs from the installed adapter.")
        # Names and license annotations captured for a take remain its history
        # even after someone relabels that adapter in the library.
        resolved["loras"] = json.loads(canonical(provenance))
        for saved, selected in zip(resolved["loras"], selections):
            # Early receipts already cryptographically bound the base model in
            # their library ID. Enrich that older receipt without changing its
            # historical attribution or accepting a conflicting explicit value.
            if "base_model" not in saved:
                saved["base_model"] = self.get(selected["id"])["base_model"]
        return resolved

    def hub_search(self, query="", limit=20, sort="downloads", genre="", page=0,
                   license="", stage="", compatibility=""):
        query, genre = _string(query, "Search", 200), _string(genre, "Genre", 100)
        license = _string(license, "License filter", 200).casefold()
        stage = _string(stage, "Stage filter", 20).casefold()
        compatibility = _string(compatibility, "Compatibility filter", 30).casefold()
        stage = "ar+nar" if stage in ("both", "paired") else stage
        compatibility = "compatible" if compatibility == "header-compatible" else compatibility
        if stage not in ("", "ar", "nar", "ar+nar"):
            raise ValueError("Filter adapters by AR, NAR or both stages.")
        if compatibility not in ("", "compatible", "unsupported", "unverified"):
            raise ValueError("Choose a valid adapter compatibility filter.")
        if type(limit) is not int or limit < 1 or type(page) is not int or page < 0:
            raise ValueError("Choose a positive search page size and a nonnegative page.")
        if sort not in ("downloads", "likes", "trendingScore", "lastModified", "last_modified"):
            raise ValueError("Choose a valid adapter sort order.")
        def basic(batch_limit, batch_page):
            return self._cached(("search", query, batch_limit, sort, genre, batch_page),
                                lambda: self.hub.search(query, batch_limit, sort, genre, batch_page))
        if not any((license, stage, compatibility)):
            return basic(limit, page)
        def filtered():
            wanted = (page + 1) * limit + 1
            batch_limit, batch_page = min(max(20, limit), 100), 0
            matches, seen = [], set()
            while len(matches) < wanted:
                batch = basic(batch_limit, batch_page)
                previous = len(seen)
                for row in batch["models"]:
                    repo = row["repo"]
                    if repo in seen:
                        continue
                    seen.add(repo)
                    declared_license = row.get("license", "")
                    licenses = declared_license if isinstance(declared_license, list) else [declared_license]
                    if license and license not in [str(value).casefold() for value in licenses]:
                        continue
                    if stage or compatibility:
                        try:
                            detail = self.hub_detail(repo)
                            compatible_files = [file for file in detail["files"] if file.get("compatible")]
                            stages = sorted({role for file in compatible_files for role in file.get("stages", [])})
                            status = ("compatible" if compatible_files else
                                      "unverified" if not detail["files"] or any(file.get("compatibility") == "unverified"
                                                                                 for file in detail["files"])
                                      else "unsupported")
                            row.update({"stages": stages, "compatibility": status,
                                        "revision": detail["revision"]})
                        except ValueError as exc:
                            row.update({"stages": [], "compatibility": "unverified", "inspection_error": str(exc)})
                        required = {"ar", "nar"} if stage == "ar+nar" else ({stage} if stage else set())
                        if not required.issubset(row["stages"]):
                            continue
                        if compatibility and row["compatibility"] != compatibility:
                            continue
                    matches.append(row)
                    if len(matches) >= wanted:
                        break
                if not batch.get("has_more") or len(seen) == previous:
                    break
                batch_page += 1
            start = page * limit
            return {"models": matches[start:start + limit], "page": page, "limit": limit,
                    "has_more": len(matches) > start + limit, "query": query, "genre": genre,
                    "sort": sort, "license": license, "stage": stage, "compatibility": compatibility}
        return self._cached(("filtered-search", query, limit, sort, genre, page, license, stage, compatibility), filtered)

    def hub_detail(self, repo, revision="main", inspect_files=True):
        repo = repository(repo)
        ref = globals()["revision"](revision)
        if type(inspect_files) is not bool:
            raise ValueError("Header inspection must be enabled or disabled explicitly.")
        return self._cached(("detail", repo, ref, inspect_files),
                            lambda: self._hub_detail(repo, ref, inspect_files))

    def _hub_detail(self, repo, revision="main", inspect_files=True):
        repo = repository(repo)
        model = self._model(repo, revision)
        commit = model["sha"]
        card = model.get("cardData") or {}
        if not isinstance(card, dict):
            card = {}
        files = [_remote_file(f) for f in model.get("siblings", [])]
        readme = ""
        readme_file = next((f for f in files if f["path"].lower() == "readme.md"), None)
        if readme_file:
            try:
                raw, _ = self.hub.read(resolve_url(repo, commit, readme_file["path"]), MAX_README_BYTES)
                readme = raw.decode("utf-8", errors="replace")
            except ValueError as exc:
                readme = "Model card unavailable: " + str(exc)
        configs = {}
        adapters, demos = [], []
        for file in files:
            suffix = PurePosixPath(file["path"]).suffix.lower()
            if suffix in MEDIA_TYPES:
                demos.append({**file, "mime": MEDIA_TYPES[suffix]})
            if suffix in CHECKPOINT_EXTENSIONS:
                adapters.append({**file, "compatible": False, "compatibility": "unsupported",
                                 "stage": None, "stages": [],
                                 "reason": "Only safetensors adapters are supported. This checkpoint format is not opened or downloaded."})
            if suffix != ".safetensors":
                continue
            item = {**file, "compatible": False, "compatibility": "unverified", "stage": None}
            if inspect_files:
                try:
                    if file["size"] is not None and file["size"] > lora_format.MAX_FILE_BYTES:
                        raise ValueError("File exceeds the adapter size limit.")
                    header = self.hub.header(repo, commit, file["path"], file["size"])
                    needs_config = any(name.endswith((".lora_A.weight", ".lora_A.default.weight")) for name in header)
                    config = None
                    if needs_config:
                        config_path = str(PurePosixPath(file["path"]).parent / "adapter_config.json")
                        if config_path in {entry["path"] for entry in files}:
                            if config_path not in configs:
                                raw, _ = self.hub.read(resolve_url(repo, commit, config_path), MAX_README_BYTES)
                                configs[config_path] = lora_format.json_object(raw)
                            config = configs[config_path]
                            item["config_path"] = config_path
                    info = lora_format.inspect_header(header, config)
                    item.update({key: info[key] for key in ("stage", "stages", "format", "rank", "targets", "metadata", "projection_mode")})
                    item.update({"compatible": True, "compatibility": "header-compatible",
                                 "verification": "Full checksum and finite-value validation occurs on import."})
                except HubUnavailable as exc:
                    item.update({"compatibility": "unverified", "reason": str(exc)})
                except ValueError as exc:
                    item.update({"compatibility": "unsupported", "reason": str(exc)})
            adapters.append(item)
        prompt_files = [file for file in files if PurePosixPath(file["path"]).suffix.lower() == ".txt"
                        and "prompts" in PurePosixPath(file["path"]).parts[:-1]]
        for demo in demos:
            stem = PurePosixPath(demo["path"]).stem.casefold()
            paired = [file for file in prompt_files if PurePosixPath(file["path"]).stem.casefold() == stem]
            if len(paired) == 1:
                prompt = paired[0]
                try:
                    raw, _ = self.hub.read(resolve_url(repo, commit, prompt["path"]), MAX_PROMPT_BYTES)
                    demo["prompt"] = {"path": prompt["path"], "text": raw.decode("utf-8", errors="replace")}
                except ValueError as exc:
                    demo["prompt"] = {"path": prompt["path"], "text": "", "error": str(exc)}
        return {"repo": repo, "revision": commit, "name": repo.rsplit("/", 1)[-1],
            "url": "https://huggingface.co/" + repo + "/tree/" + commit,
            "license": card.get("license", ""), "tags": model.get("tags", []), "readme": readme,
            "files": adapters, "demos": demos, "downloads": model.get("downloads", 0),
            "likes": model.get("likes", 0), "updated_at": model.get("lastModified", model.get("last_modified")),
            "base_model": card.get("base_model", ""), "all_files": files}

    def task(self, value):
        with self.lock:
            if not isinstance(value, str) or value not in self.tasks:
                raise ValueError("LoRA download task was not found.")
            return json.loads(canonical(self.tasks[value]))

    def cancel(self, value):
        with self.lock:
            if not isinstance(value, str) or value not in self.tasks:
                raise ValueError("LoRA download task was not found.")
            self.events[value].set()
            return self.task(value)

    def _download(self, repo, commit, file, destination, cancel=None, progress=None, maximum=None):
        size = file.get("size")
        maximum = maximum or lora_format.MAX_FILE_BYTES
        if type(size) is not int or not 0 <= size <= maximum:
            raise ValueError("Download needs a verified file size within the allowed limit.")
        if not file.get("sha256") and not file.get("git_blob"):
            raise ValueError("Hugging Face did not supply a checksum for this file.")
        destination = Path(destination)
        try:
            relative = destination.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError("Download destination must stay inside the LoRA library.") from exc
        self._file(self.root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        if partial.is_symlink() or destination.is_symlink():
            raise ValueError("Download files cannot be symbolic links.")
        if destination.exists():
            if _verified_download(destination, file, cancel):
                if progress:
                    progress(size)
                return destination
            destination.unlink()
        if partial.exists():
            lora_format.regular_file(partial)
        have = partial.stat().st_size if partial.exists() else 0
        if have > size:
            partial.unlink()
            have = 0
        if cancel:
            cancel()
        if have < size:
            headers = {"Range": "bytes=" + str(have) + "-"} if have else {}
            with self.hub.open(resolve_url(repo, commit, file["path"]), headers) as response:
                if have and response.status == 206:
                    expected = "bytes " + str(have) + "-" + str(size - 1) + "/" + str(size)
                    if response.headers.get("Content-Range") != expected:
                        raise ValueError("Download resume returned an incorrect byte range.")
                elif response.status == 200:
                    have = 0
                elif response.status == 206:
                    if response.headers.get("Content-Range") != "bytes 0-" + str(size - 1) + "/" + str(size):
                        raise ValueError("Download returned an incorrect byte range.")
                else:
                    raise ValueError("Unexpected Hugging Face download status.")
                with partial.open("ab" if have else "wb") as output:
                    while True:
                        if cancel:
                            cancel()
                        chunk = response.read(min(1024 * 1024, size - have + 1))
                        if not chunk:
                            break
                        have += len(chunk)
                        if have > size:
                            raise ValueError("Downloaded file exceeds its published size.")
                        output.write(chunk)
                        if progress:
                            progress(have)
                    output.flush()
                    os.fsync(output.fileno())
        if have != size:
            raise ValueError("Download ended early. Retry to resume the remaining bytes.")
        if not _verified_download(partial, file, cancel):
            partial.unlink(missing_ok=True)
            raise ValueError("Downloaded file checksum does not match Hugging Face. The adapter was not installed.")
        partial.replace(destination)
        if progress:
            progress(size)
        return destination

    def hub_install(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Send a Hugging Face repository and selected files.")
        repo = repository(payload.get("repo"))
        commit = revision(payload.get("revision"), pinned=True)
        paths = payload.get("files")
        if not isinstance(paths, list) or not 1 <= len(paths) <= 2:
            raise ValueError("Select one AR file, one NAR file, or one of each.")
        paths = [hub_path(path) for path in paths]
        if len(set(paths)) != len(paths):
            raise ValueError("Select each adapter file only once.")
        if any(PurePosixPath(path).suffix.lower() != ".safetensors" for path in paths):
            raise ValueError("Only .safetensors adapters can be installed.")
        metadata = metadata_fields(payload)
        task_id = uuid.uuid4().hex
        event = threading.Event()
        with self.lock:
            self.tasks[task_id] = {"id": task_id, "status": "queued", "downloaded": 0, "total": 0,
                                   "error": None, "result": None, "repo": repo, "revision": commit,
                                   "files": paths, "created_at": utc_now()}
            self.events[task_id] = event
        def check():
            if event.is_set():
                raise Cancelled("Download cancelled. Downloaded partial files are retained for a retry.")
        def update(**values):
            with self.lock:
                self.tasks[task_id].update(values)
        def run():
            try:
                with self.download_gate:
                    check()
                    update(status="inspecting")
                    detail = self.hub_detail(repo, commit)
                    selected = []
                    for path in paths:
                        file = next((f for f in detail["files"] if f["path"] == path), None)
                        if not file or not file["compatible"]:
                            raise ValueError("Selected file is not a compatible YuE2-3B adapter: " + path
                                             + (". " + file.get("reason", "") if file else ""))
                        selected.append(file)
                    if len({stage for f in selected for stage in f["stages"]}) != sum(len(f["stages"]) for f in selected):
                        raise ValueError("Choose at most one AR and one NAR adapter; equivalent exports are alternatives.")
                    configs = sorted({f["config_path"] for f in selected if f.get("config_path")})
                    extras = [next(f for f in detail["all_files"] if f["path"] == path) for path in configs]
                    total = sum(f.get("size") or 0 for f in [*selected, *extras])
                    update(status="downloading", total=total)
                    cache_id = hashlib.sha256(canonical({"repo": repo, "revision": commit}).encode()).hexdigest()
                    cache = self.root / ".downloads" / cache_id
                    completed = 0
                    local = []
                    for file in [*selected, *extras]:
                        check()
                        target = cache / hub_path(file["path"])
                        path = self._download(repo, commit, file, target, check,
                                              lambda n: update(downloaded=completed + n))
                        completed += file["size"]
                        if file in selected:
                            local.append(path)
                    check()
                    update(status="validating", downloaded=total)
                    defaults = {"name": detail["name"], "license": detail["license"] if isinstance(detail["license"], str) else canonical(detail["license"]),
                                "tags": [tag for tag in detail["tags"] if isinstance(tag, str)][:100], **metadata}
                    installed = self.import_local(local, defaults,
                        {"type": "huggingface", "repo": repo, "revision": commit, "files": paths}, check)
                    # Cached originals are redundant after the atomic install;
                    # .part files for cancelled downloads remain resumable.
                    for path in local:
                        path.unlink(missing_ok=True)
                    update(status="completed", result=installed, finished_at=utc_now())
            except Cancelled as exc:
                update(status="cancelled", error=str(exc), finished_at=utc_now())
            except Exception as exc:
                update(status="failed", error=str(exc), finished_at=utc_now())
        thread = threading.Thread(target=run, name="riff-lora-" + task_id[:8], daemon=True)
        self.threads[task_id] = thread
        thread.start()
        return self.task(task_id)

    def check_updates(self, value):
        manifest = self.get(value)
        source = manifest["source"]
        if source["type"] != "huggingface":
            return {"id": value, "available": False, "reason": "Locally imported adapter has no Hub revision to check."}
        model = self.hub.model(source["repo"])
        latest = model["sha"]
        return {"id": value, "repo": source["repo"], "installed_revision": source["revision"],
                "revision": latest, "available": latest != source["revision"],
                "files": source["files"], "install_separately": True}

    def media(self, repo, revision, path):
        repo, commit, path = repository(repo), globals()["revision"](revision, pinned=True), hub_path(path)
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in MEDIA_TYPES:
            raise ValueError("Choose an audio or video demo file.")
        model = self._model(repo, commit)
        if model["sha"] != commit:
            raise ValueError("Model revision changed during media retrieval.")
        file = next((_remote_file(f) for f in model.get("siblings", []) if f.get("rfilename") == path), None)
        if file is None:
            raise ValueError("Demo is not present in the selected model revision.")
        key = hashlib.sha256(canonical([repo, commit, path, file["sha256"], file["git_blob"]]).encode()).hexdigest()
        target = self.root / ".media" / (key + suffix)
        if target.exists():
            if _verified_download(target, file):
                return target, MEDIA_TYPES[suffix]
            target.unlink()
        self._download(repo, commit, file, target, maximum=MAX_MEDIA_BYTES)
        return target, MEDIA_TYPES[suffix]

    def close(self):
        with self.lock:
            for event in self.events.values():
                event.set()
        for thread in list(self.threads.values()):
            thread.join(timeout=0.1)
