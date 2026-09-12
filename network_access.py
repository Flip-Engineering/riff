"""Optional Tailscale Serve access; the studio itself stays on loopback."""
import argparse
from email.header import decode_header, make_header
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit

from paths import DATA


def read_access(data_root):
    path = data_root / "network.json"
    if not path.exists(): return None
    value = json.loads(path.read_text())
    origin, login = value.get("origin", ""), value.get("tailscale_user", "")
    url = urlsplit(origin)
    if (url.scheme != "https" or not url.hostname or not url.hostname.endswith(".ts.net")
            or url.username or url.password or url.path or url.query or url.fragment
            or not isinstance(login, str) or not login.strip() or any(c in login for c in "\r\n")):
        raise ValueError("Riff's Tailscale access settings are invalid.")
    if url.port is not None and not 0 < url.port < 65536:
        raise ValueError("Riff's Tailscale port is invalid.")
    return {"origin": origin, "host": url.netloc, "tailscale_user": login}


def request_origin(headers, peer, port, access):
    local = {f"127.0.0.1:{port}", f"localhost:{port}"}
    host = headers.get("Host")
    proxied = bool(headers.get("X-Forwarded-For") or headers.get("X-Forwarded-Proto")
                   or headers.get("Tailscale-User-Login") or (access and host == access["host"]))
    if proxied:
        login = str(make_header(decode_header(headers.get("Tailscale-User-Login", ""))))
        if (not access or peer not in ("127.0.0.1", "::1")
                or host not in local | {access["host"]}
                or login.casefold() != access["tailscale_user"].casefold()
                or headers.get("X-Forwarded-Proto", "https") != "https"):
            raise ValueError("This studio is available to its connected Tailscale account.")
        origins = {access["origin"]}
    else:
        if host not in local:
            raise ValueError("Open Riff at its studio address.")
        origins = {f"http://{name}" for name in local}
    if headers.get("Origin") and headers["Origin"] not in origins:
        raise ValueError("This request did not come from the studio.")


def enable(https_port=443, studio_port=7878):
    binary = shutil.which("tailscale")
    app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    if not binary and app.is_file(): binary = str(app)
    if not binary: raise ValueError("Install and connect Tailscale before enabling access.")
    def inspect(*arguments):
        return json.loads(subprocess.check_output([binary, *arguments, "--json"], text=True))
    state, before = inspect("status"), inspect("serve", "status")
    if state.get("BackendState") != "Running": raise ValueError("Connect Tailscale first.")
    device = state["Self"]
    owner = state.get("User", {}).get(str(device.get("UserID")), {}).get("LoginName")
    if not owner: raise ValueError("Use a Tailscale device associated with your user account.")
    host = device["DNSName"].rstrip(".") + (f":{https_port}" if https_port != 443 else "")
    target = f"http://127.0.0.1:{studio_port}"
    existing = before.get("Web", {}).get(device["DNSName"].rstrip(".") + f":{https_port}")
    expected = {"Handlers": {"/": {"Proxy": target}}}
    if str(https_port) in before.get("TCP", {}) and existing != expected:
        raise ValueError("That Tailscale port already serves another application. Choose a different --https-port.")
    value = {"origin": "https://" + host, "tailscale_user": owner}
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "network.json"
    old = path.read_bytes() if path.exists() else None
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    read_access(DATA)
    try:
        subprocess.run([binary, "serve", "--bg", f"--https={https_port}", "--yes", target], check=True)
    except Exception:
        if old is None: path.unlink(missing_ok=True)
        else: path.write_bytes(old)
        raise
    print("Restart Riff to enable " + value["origin"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--https-port", type=int, default=443)
    parser.add_argument("--studio-port", type=int, default=7878)
    args = parser.parse_args()
    if not all(0 < port < 65536 for port in (args.https_port, args.studio_port)):
        parser.error("Choose ports between 1 and 65535.")
    enable(args.https_port, args.studio_port)
