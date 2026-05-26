# E1002 Local Dashboard PRD

## Background

AI Usage Dashboard can generate an e-ink-friendly JSON payload. The E1002 companion project turns that payload into a low-frequency desktop display.

The preferred architecture is local-first:

1. Python aggregation computes usage data.
2. FastAPI exposes a local HTTP interface.
3. E1002 fetches JSON and renders the dashboard.

## Product Goal

Build a single-page, fixed-layout, low-frequency dashboard for a personal desk e-ink device. The device should show recent AI usage without requiring a cloud service or public data publishing.

## Scope

In scope:

- reTerminal E1002 target hardware
- 800x480 native rendering
- local HTTP JSON protocol
- 7D / 30D view switching
- green-button force update
- daytime scheduled refresh
- fallback to cached or last-good data on refresh failure

Out of scope:

- cloud-hosted dashboard service
- public JSON publishing
- general widget platform
- multi-user auth
- high-frequency refresh
- device-side token aggregation

## User Experience

- Startup fetches dashboard JSON and renders the default 7D view.
- Green button requests fresh data and redraws.
- White button toggles 7D / 30D using already cached data.
- Scheduled wake performs updates only during the configured daytime window.
- If the local service is unavailable, the device keeps stale data when possible and shows a visible warning.

## Privacy Boundary

Real local hostnames, LAN IPs, Wi-Fi credentials, and device identifiers belong in ignored private config. Public docs and examples use placeholders only.

## Success Criteria

- The local API returns valid `meta` / `summary` / `daily` JSON.
- The E1002 can parse and render that JSON.
- Button behavior matches the documented contract.
- Failure modes preserve last-good data instead of blanking the screen.
- Firmware compiles with public placeholder config and with private local config.
