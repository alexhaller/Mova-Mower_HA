# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Home Assistant custom integration for Dreame/Mova robotic lawn mowers. Communicates via local LAN (Xiaomi MiIO protocol, UDP 54321) with cloud-assisted device discovery (Dreame Home, Mova Home, or MI cloud). Forked from [bhuebschen/dreame-mower](https://github.com/bhuebschen/dreame-mower/).

- GitHub: https://github.com/alexhaller/Mova-Mower_HA
- Domain: `dreame_mower`
- pip-audit packages: `pybase64>=1.4.3`, `pycryptodome>=3.23.0`, `python-miio>=0.5.12`
  (Pillow is a Home Assistant core dependency and is deliberately not listed)

## Commands

```powershell
# Lint
ruff check .
ruff format --check .

# Type check
mypy custom_components/

# Auto-fix formatting and UP rules
ruff format .
ruff check . --select UP --fix

# pip-audit against manifest requirements
python -c "import json; reqs=json.load(open('custom_components/dreame_mower/manifest.json'))['requirements']; open('_reqs_tmp.txt','w').write('\n'.join(reqs))" && pip-audit -r _reqs_tmp.txt && Remove-Item _reqs_tmp.txt
```

`hassfest` and HACS validation run in CI only (`.github/workflows/validate.yml`).

## Architecture

### Communication layer (`custom_components/dreame_mower/dreame/`)

The inner `dreame/` package is the device SDK — it has no HA dependencies and handles all hardware communication:

- **`protocol.py`** — `DreameMowerProtocol` wraps three backends:
  - Local MiIO (`DreameMowerDeviceProtocol`, extends `MiIOProtocol` from `python-miio`)
  - Dreame/Mova cloud (`DreameMowerDreameHomeCloudProtocol`, HTTPS via `requests`)
  - MI cloud (country-specific Xiaomi backend)
  - Local connection is always attempted first; cloud fallback if unavailable.
- **`device.py`** — `DreameMowerDevice`: the stateful device model. Owns all 100+ properties and 50+ actions; parses raw MiIO responses into typed Python objects.
- **`types.py`** — Enums, dataclasses, and property/action descriptors used by `device.py`.
- **`const.py`** — Device-level constants (status codes, error codes, feature flags).
- **`map.py` / `map_app.py`** — Multi-floor map parsing and PNG retrieval from cloud.

### HA integration layer (`custom_components/dreame_mower/`)

- **`coordinator.py`** — `DreameMowerDataUpdateCoordinator` polls the device every 10 seconds. Handles 2FA notifications, consumable warnings, and temporary map notifications.
- **`entity.py`** — `DreameMowerEntity(CoordinatorEntity)`: base class for all platform entities. Unique ID is derived from device MAC address. Device identity is connected via `CONNECTION_NETWORK_MAC`.
- **`__init__.py`** — Loads 8 platforms: `lawn_mower`, `sensor`, `switch`, `button`, `select`, `number`, `camera`, `time`. Stores coordinator at `hass.data[DOMAIN][entry.entry_id]`.
- **`config_flow.py`** — Multi-step flow: account type selection → cloud login (with 2FA support) → device picker → options. Three account types: Dreamehome, Mova Home, Local (no map).
- **`const.py`** — 23 HA services (zone/segment/spot cleaning, map management, etc.) and map configuration constants.

### Platform files

Each platform (`sensor.py`, `switch.py`, `button.py`, `select.py`, `number.py`, `camera.py`, `time.py`, `lawn_mower.py`) uses the `EntityDescription` dataclass pattern. Availability, icon, and formatting are driven by lambdas on the description objects that read from `coordinator.data` (the `DreameMowerDevice` instance).

After any device write, platforms call `await self.coordinator.async_request_refresh()`.

## Brand assets

- `custom_components/dreame_mower/brand/icon.png` — HACS action validation
- `brands/icon.png` — HACS store UI
- Both are the same 512×512 PNG.

## `.releaserc.json` `prepareCmd` path

`custom_components/dreame_mower/manifest.json`
