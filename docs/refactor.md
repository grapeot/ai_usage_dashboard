# Refactor Boundaries

## Goal

Reduce the provider-specific responsibilities in `auto_usage.py` without
changing its command-line entry point or its public Python surface. The first
step moves Google Antigravity discovery, RPC, parsing, cache, sync, aggregation,
cost, and quota behavior into `antigravity_usage.py`.

## Boundary

`antigravity_usage.py` owns Antigravity behavior and does not import
`auto_usage`. `auto_usage.py` remains the composition root and compatibility
facade. Its Antigravity functions are intentionally thin wrappers rather than
aliases: they pass the current facade discovery, RPC, trajectory, cache, and
sync helpers into the provider module at call time.

This direction keeps dependencies one-way:

```text
auto_usage -> antigravity_usage -> pricing_config
```

## Invariants

- Existing imports from `auto_usage` continue to resolve, including private
  Antigravity helpers used by tests and local scripts.
- Existing project patch seams for Antigravity discovery, RPC, trajectories,
  cache, sync metadata, parsing, classification, and pricing continue to affect
  high-level calls. Provider implementation globals that are not established
  seams, such as rebinding `auto_usage.datetime`, are not part of this contract.
- `ANTIGRAVITY_CACHE_FILE` is read when a cache helper runs, not captured when
  the module imports. Tests and callers may still redirect it dynamically.
- Cache format, deduplication, sync metadata, model buckets, quota shape, and
  pricing behavior remain unchanged.
- `auto_usage.py` remains the CLI and dashboard orchestration entry point.
- `antigravity_usage` remains a top-level installed module so direct script
  execution and editable or wheel installs resolve it the same way.

## Next Steps

Later passes may extract active-time calculation and Claude-specific usage or
quota behavior behind the same compatibility pattern. Do not rewrite the
entire script at once. Each extraction should preserve the current entry
points, patch seams, and output contracts, then land with focused regression
tests before another provider or concern moves.
