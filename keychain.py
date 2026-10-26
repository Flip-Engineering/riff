"""Credentials in macOS Keychain or a Linux Secret Service, never in the repo."""
from contextlib import contextmanager
import ctypes as C
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
from urllib.parse import unquote


def secret_service_environment():
    """Derive only this process account's session; never borrow another user's keyring."""
    import pwd  # Linux only; keep macOS/Windows module import portable.
    env = dict(os.environ)
    uid = os.geteuid()

    def owned(path, kind, private=False):
        try:
            info = Path(path).stat()
        except OSError:
            raise ValueError("Run Riff in this account's unlocked desktop/keyring session; its session path is unavailable.") from None
        if info.st_uid != uid or not kind(info.st_mode) or (private and info.st_mode & 0o077):
            raise ValueError("The keyring session path must belong to Riff's actual process account. Run Riff in that account's desktop session.")

    env["HOME"] = env.get("HOME") or pwd.getpwuid(uid).pw_dir
    owned(env["HOME"], stat.S_ISDIR)
    runtime = env.get("XDG_RUNTIME_DIR")
    bus = env.get("DBUS_SESSION_BUS_ADDRESS")
    if runtime or not bus:
        runtime = runtime or f"/run/user/{uid}"
        owned(runtime, stat.S_ISDIR, private=True)
        env["XDG_RUNTIME_DIR"] = runtime
    if not bus:
        socket = str(Path(runtime) / "bus")
        owned(socket, stat.S_ISSOCK)
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + socket
    else:
        # Explicit nonstandard buses remain explicit. Verify filesystem Unix sockets
        # when present; abstract/session-managed addresses have no filesystem owner.
        for address in bus.split(";"):
            if address.startswith("unix:"):
                options = dict(part.split("=", 1) for part in address[5:].split(",") if "=" in part)
                if "path" in options:
                    owned(unquote(options["path"]), stat.S_ISSOCK)
    return env


class MacKeychain:
    storage_name = "macOS Keychain"
    available = True

    def __init__(self, service="Riff OpenRouter", account="api-key"):
        self.service, self.account = service, account
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.sec = C.CDLL("/System/Library/Frameworks/Security.framework/Security")
        pointer = C.c_void_p
        self.cf.CFStringCreateWithCString.argtypes = [pointer, C.c_char_p, C.c_uint32]
        self.cf.CFStringCreateWithCString.restype = pointer
        self.cf.CFDataCreate.argtypes = [pointer, C.c_char_p, C.c_long]
        self.cf.CFDataCreate.restype = pointer
        self.cf.CFDictionaryCreate.argtypes = [pointer, C.POINTER(pointer), C.POINTER(pointer), C.c_long, pointer, pointer]
        self.cf.CFDictionaryCreate.restype = pointer
        self.cf.CFDataGetLength.argtypes = [pointer]
        self.cf.CFDataGetLength.restype = C.c_long
        self.cf.CFDataGetBytePtr.argtypes = [pointer]
        self.cf.CFDataGetBytePtr.restype = pointer
        self.cf.CFRelease.argtypes = [pointer]
        for name in ("SecItemAdd", "SecItemCopyMatching"):
            function = getattr(self.sec, name)
            function.argtypes, function.restype = [pointer, C.POINTER(pointer)], C.c_int32
        self.sec.SecItemUpdate.argtypes, self.sec.SecItemUpdate.restype = [pointer, pointer], C.c_int32
        self.sec.SecItemDelete.argtypes, self.sec.SecItemDelete.restype = [pointer], C.c_int32
        self.key_callbacks = C.addressof(C.c_byte.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks"))
        self.value_callbacks = C.addressof(C.c_byte.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks"))

    def constant(self, name):
        return C.c_void_p.in_dll(self.cf if name.startswith("kCF") else self.sec, name).value

    @contextmanager
    def dictionary(self, entries):
        owned, keys, values = [], [], []
        try:
            for key, value in entries.items():
                keys.append(self.constant(key))
                if isinstance(value, bytes):
                    value = self.cf.CFDataCreate(None, value, len(value))
                    owned.append(value)
                elif isinstance(value, str):
                    value = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
                    owned.append(value)
                values.append(value)
            array = C.c_void_p * len(keys)
            result = self.cf.CFDictionaryCreate(None, array(*keys), array(*values), len(keys),
                                                self.key_callbacks, self.value_callbacks)
            if not result:
                raise RuntimeError("Keychain attributes could not be created.")
            try:
                yield result
            finally:
                self.cf.CFRelease(result)
        finally:
            for value in owned:
                self.cf.CFRelease(value)

    def query(self):
        return {"kSecClass": self.constant("kSecClassGenericPassword"),
                "kSecAttrService": self.service, "kSecAttrAccount": self.account}

    @staticmethod
    def check(status):
        if status:
            raise ValueError(f"macOS Keychain could not complete this action (status {status}). Unlock your login keychain and try again.")

    def exists(self):
        with self.dictionary(self.query()) as query:
            status = self.sec.SecItemCopyMatching(query, None)
        if status == -25300:
            return False
        self.check(status)
        return True

    def get(self):
        result = C.c_void_p()
        with self.dictionary({**self.query(), "kSecReturnData": self.constant("kCFBooleanTrue")}) as query:
            status = self.sec.SecItemCopyMatching(query, C.byref(result))
        if status == -25300:
            raise ValueError("Add your OpenRouter key in review settings.")
        self.check(status)
        try:
            return C.string_at(self.cf.CFDataGetBytePtr(result), self.cf.CFDataGetLength(result)).decode()
        finally:
            self.cf.CFRelease(result)

    def set(self, key):
        if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key.strip()):
            raise ValueError("Enter your OpenRouter API key.")
        encoded = key.strip().encode()
        with self.dictionary({**self.query(), "kSecValueData": encoded}) as attributes:
            status = self.sec.SecItemAdd(attributes, None)
        if status == -25299:
            with self.dictionary(self.query()) as query, self.dictionary({"kSecValueData": encoded}) as update:
                status = self.sec.SecItemUpdate(query, update)
        self.check(status)

    def delete(self):
        with self.dictionary(self.query()) as query:
            status = self.sec.SecItemDelete(query)
        if status != -25300:
            self.check(status)


class SecretService:
    storage_name = "Secret Service keyring"

    def __init__(self, service="Riff OpenRouter", account="api-key"):
        self.service, self.account = service, account
        self.available = bool(shutil.which("secret-tool"))

    def invoke(self, action, key=None):
        if not self.available:
            raise ValueError("Install libsecret-tools and unlock a Secret Service keyring to save a key.")
        command = ["secret-tool", action]
        if action == "store":
            command += ["--label=Riff OpenRouter"]
        command += ["service", self.service, "account", self.account]
        try:
            result = subprocess.run(command, input=key, text=True, capture_output=True, timeout=30,
                                    env=secret_service_environment())
        except subprocess.TimeoutExpired:
            raise ValueError("Unlock your desktop keyring and try again.") from None
        if result.returncode and (action != "lookup" or result.stderr.strip()):
            raise ValueError("The keyring could not complete this action. Unlock your desktop keyring and try again.")
        return result.stdout.rstrip("\n")

    def exists(self):
        return bool(self.invoke("lookup")) if self.available else False

    def get(self):
        key = self.invoke("lookup")
        if not key:
            raise ValueError("Add your OpenRouter key in review settings.")
        return key

    def set(self, key):
        if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key.strip()):
            raise ValueError("Enter your OpenRouter API key.")
        self.invoke("store", key.strip())

    def delete(self):
        if self.exists():
            self.invoke("clear")


class Keychain:
    """Keep old installations connected while renaming their Keychain entry."""
    def __init__(self, service="Riff OpenRouter", account="api-key"):
        cls = MacKeychain if platform.system() == "Darwin" else SecretService
        self.current = cls(service, account)
        self.legacy = cls("Rill OpenRouter", account) if service == "Riff OpenRouter" and platform.system() == "Darwin" else None
        self.storage_name, self.available = self.current.storage_name, self.current.available

    def exists(self):
        return self.current.exists() or bool(self.legacy and self.legacy.exists())

    def get(self):
        if self.current.exists():
            return self.current.get()
        if self.legacy and self.legacy.exists():
            key = self.legacy.get()
            self.current.set(key)
            return key
        return self.current.get()

    def set(self, key):
        self.current.set(key)

    def delete(self):
        self.current.delete()
        if self.legacy:
            self.legacy.delete()
