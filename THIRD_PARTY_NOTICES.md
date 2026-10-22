# Third-party notices

The CLI bundles the unmodified pure-Python sources of Tomli 2.0.1 for Python 3.8–3.10, under the MIT license.

- Project: https://github.com/hukkin/tomli
- Source tag: https://github.com/hukkin/tomli/tree/2.0.1
- Copyright: (c) 2021 Taneli Hukkinen
- License: src/l4d2_cli/_vendor/tomli/LICENSE
- Original download URLs and SHA-256: src/l4d2_cli/_vendor/tomli/SOURCE.json

No SteamCMD or game binaries are bundled with this project.

## Project-owned migration sources

The catalog/mission parsing and bundled SourcePawn sources in `src/l4d2_cli/ops/` were migrated from L4D2-Server-Manager-Skill, Copyright (c) 2026 Wizard23333, under the same MIT license as this repository. No web server or browser frontend is required by these modules. MetaMod, SourceMod and Left4DHooks are fetched only when explicitly requested and retain their respective upstream licenses; their binaries are not bundled.

## Optional web frontend

The optional React frontend is derived from L4D2-Server-Manager-Skill's `frontend-redesign` branch at commit `6d4817f`, Copyright (c) 2026 Wizard23333 (MIT). Source provenance is recorded in `web/frontend/UPSTREAM.json`. It is adapted to the CLI operations contract; the old privileged helper and web business logic are not bundled.

The browser bundle includes React 18.2.0, ReactDOM 18.2.0 and Scheduler 0.23.0, Copyright (c) Meta Platforms, Inc. and affiliates, under MIT. Complete upstream notices are retained in `src/l4d2_cli/web/licenses/`. TypeScript, Vite and build plugins are development tools and are not runtime Python dependencies. The web frontend uses local assets and system font fallbacks.

## Optional external QR backend

QR authentication/downloads can invoke SteamRE/DepotDownloader, tested with release DepotDownloader_3.4.0. It is a separate program, not bundled in the CLI source or wheel; use is governed by its upstream LICENSE.

- Project: https://github.com/SteamRE/DepotDownloader
- Tested release: https://github.com/SteamRE/DepotDownloader/releases/tag/DepotDownloader_3.4.0
- License: https://github.com/SteamRE/DepotDownloader/blob/DepotDownloader_3.4.0/LICENSE
- Interface: -qr / -remember-password / Linux depot manifest and content downloads. No upstream source code is copied into the Python CLI.

## Linux executable distribution

The Linux directory bundle includes CPython 3.12.15 from the unmodified astral-sh/python-build-standalone release 20261013. Its exact upstream asset URL/SHA256 and build-tool versions are recorded in scripts/packaging/runtime.json and the bundle's build-info.json.

- Runtime source and license collection: https://github.com/astral-sh/python-build-standalone/tree/20261013
- CPython and runtime dependency license texts are copied into licenses/python-runtime/ in the executable package. Some upstream notices cover components not collected by this CLI.
- PyInstaller 6.22.3 freezes the CLI using its bootloader exception; its COPYING.txt is retained in licenses/PyInstaller-COPYING.txt. No PyInstaller source is modified.
- The source CLI is MIT-licensed; bundled CPython/runtime libraries retain their own licenses. SteamCMD, game binaries and optional DepotDownloader are excluded.
