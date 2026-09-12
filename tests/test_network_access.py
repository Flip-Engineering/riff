import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import network_access
from network_access import read_access, request_origin


class NetworkTests(unittest.TestCase):
    access = {"origin": "https://studio.example.ts.net", "host": "studio.example.ts.net", "tailscale_user": "artist@example.test"}

    def test_local_and_private_tailscale_origins(self):
        request_origin({"Host": "127.0.0.1:7878", "Origin": "http://127.0.0.1:7878"}, "127.0.0.1", 7878, None)
        headers = {"Host": self.access["host"], "Origin": self.access["origin"], "X-Forwarded-For": "100.64.0.5",
                   "X-Forwarded-Proto": "https", "Tailscale-User-Login": self.access["tailscale_user"]}
        request_origin(headers, "127.0.0.1", 7878, self.access)
        # A proxy that rewrites Host still must supply the right user and HTTPS origin.
        request_origin({**headers, "Host": "127.0.0.1:7878"}, "127.0.0.1", 7878, self.access)
        for changes in ({"Tailscale-User-Login": "someone-else@example.test"}, {"Tailscale-User-Login": ""},
                        {"Origin": "https://unrelated.invalid"}, {"Origin": "http://127.0.0.1:7878"},
                        {"Host": "unrelated.invalid"}, {"X-Forwarded-Proto": "http"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                request_origin({**headers, **changes}, "127.0.0.1", 7878, self.access)
        with self.assertRaises(ValueError): request_origin(headers, "100.64.0.5", 7878, self.access)
        with self.assertRaises(ValueError): request_origin(headers, "127.0.0.1", 7878, None)

    def test_funnel_or_tagged_traffic_cannot_bypass_identity_with_localhost(self):
        for host in ("127.0.0.1:7878", self.access["host"]):
            with self.assertRaises(ValueError):
                request_origin({"Host": host, "X-Forwarded-For": "100.64.0.8", "X-Forwarded-Proto": "https"},
                               "127.0.0.1", 7878, self.access)

    def test_only_a_configured_https_tailnet_origin_is_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(read_access(root))
            for origin in ("http://studio.example.ts.net", "https://unrelated.invalid", "https://artist@studio.example.ts.net",
                           "https://studio.example.ts.net/path", "https://studio.example.ts.net?other=1"):
                (root / "network.json").write_text(json.dumps({**self.access, "origin": origin}))
                with self.subTest(origin=origin), self.assertRaises(ValueError): read_access(root)
            (root / "network.json").write_text(json.dumps(self.access))
            self.assertEqual(read_access(root), self.access)

    def test_enable_preserves_other_proxies_and_refuses_an_occupied_port(self):
        state = {"BackendState": "Running", "Self": {"DNSName": "studio.example.ts.net.", "UserID": 1},
                 "User": {"1": {"LoginName": "artist@example.test"}}}
        before = {"TCP": {"3080": {"HTTP": True}}, "Web": {"studio.example.ts.net:3080": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3080"}}}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(network_access, "DATA", Path(directory)), \
                patch.object(network_access.shutil, "which", return_value="tailscale"), \
                patch.object(network_access.subprocess, "check_output", side_effect=[json.dumps(state), json.dumps(before)]), \
                patch.object(network_access.subprocess, "run") as command:
            network_access.enable()
            command.assert_called_once_with(["tailscale", "serve", "--bg", "--https=443", "--yes", "http://127.0.0.1:7878"], check=True)
            self.assertEqual(read_access(Path(directory)), self.access)
        with patch.object(network_access.shutil, "which", return_value="tailscale"), \
                patch.object(network_access.subprocess, "check_output", side_effect=[json.dumps(state), json.dumps(before)]), \
                patch.object(network_access.subprocess, "run") as command:
            with self.assertRaises(ValueError): network_access.enable(3080)
            command.assert_not_called()
