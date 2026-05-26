# E1002 Dashboard POC

## Goal

The POC proves the local end-to-end path:

1. `auto_usage.py` generates `token_usage_eink.json`.
2. A local FastAPI service returns dashboard JSON over HTTP.
3. E1002 fetches the JSON through the local network and renders it natively.

The server returns structured data. The device owns rendering.

## Local Server

Run from the repo root:

```bash
scripts/ai-usage-service
```

Public examples use `127.0.0.1`. Real LAN hostnames or IP addresses belong in private config such as `eink/e1002/secrets.h`.

## JSON Contract

The E1002 payload has three top-level keys:

- `meta`: version, generated time, date range, day count, currency
- `summary`: total tokens, total AI hours, optional total cost, category totals
- `daily`: per-day category totals, total tokens, AI hours, optional cost

The device should treat this as a fixed dashboard contract, not a general BI schema.

## Device Config

Copy `eink/e1002/secrets.h.example` to `eink/e1002/secrets.h` and fill in private values:

- Wi-Fi SSID
- Wi-Fi password
- dashboard update URL
- cached JSON URL
- device id

`secrets.h` is ignored by git.

## POC Acceptance Criteria

- Local FastAPI returns valid dashboard JSON.
- E1002 can request and parse that JSON.
- E1002 renders the minimal dashboard clearly on the 800x480 display.
- Green button triggers a fresh update.
- White button switches view locally without a network request.
