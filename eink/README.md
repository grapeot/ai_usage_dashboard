# E-Paper Reference Implementation

`eink/` contains the optional e-paper reference implementation for AI Usage Dashboard. It is not required for the main project installation.

The current implementation targets the **Seeed Studio reTerminal E1002** 800x480 color e-paper display. Most users can ignore this directory unless they want to show the dashboard on that device.

Current POC path:

- the server generates `token_usage_eink.json`
- the local FastAPI service serves the data over LAN HTTP
- the device fetches the JSON and renders the dashboard locally

This directory currently includes an E1002 Arduino POC sketch.

## Files

- `docs/prd.md`: product goal, scope, and success criteria
- `docs/rfc.md`: technical boundaries, system relationships, and minimum implementation path
- `docs/poc.md`: reTerminal E1002 flashing, library selection, deployment path, and POC verification
- `e1002/`: Arduino sketch and device-side notes

## Current Conclusion

- Target device: Seeed Studio reTerminal E1002, 800x480 color e-paper
- Architecture: local FastAPI service plus LAN HTTP JSON fetch, with native rendering on the device
- Status: feasible optional hardware reference implementation, not the default path for normal users

Further implementation should follow the PRD and RFC in this directory.
