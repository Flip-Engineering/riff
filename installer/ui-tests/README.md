# Installer interface checks

From the repository root, after installing its development dependencies:

```sh
node installer/ui-tests/browser.mjs
```

`RIFF_BROWSER_EXECUTABLE` can select an already installed Playwright-compatible
Chromium. The test starts its own loopback fixture and writes screenshots and a
JSON receipt into `.artifacts/`. It does not change the installed studio or
download production models.

The fixture exercises keyboard and touch input, aggregate download progress,
stale status responses, cancellation, retained progress, retry, incomplete app
packaging, readiness, connection recovery, launch-token headers and reduced
motion. It also verifies that the browser never follows a server-supplied app
URL: opening belongs to the authenticated, verified application opener.

For a separately running Elixir installer with isolated fixture downloads:

```sh
RIFF_INSTALLER_URL=http://127.0.0.1:PORT \
RIFF_INSTALLER_EXPECT_ASSET_IDS=fixture-music,fixture-decoder \
node installer/ui-tests/real-server.mjs
```

The exact complete asset list must match before any install request is made.
This check downloads and verifies those fixtures through the real graphical
flow, retries using the existing files, and checks actual served source bytes,
authentication, CSP, desktop/mobile rendering and the models-only completion
guard. Do not point it at an ordinary installation or a production manifest.
