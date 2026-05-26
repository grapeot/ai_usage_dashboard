# Working Notes

## Changelog

### 2026-05-25

- Began public-repo scaffold pass.
- Added `.env.example`, `AGENTS.md`, `pyproject.toml`, `scripts/`, and repo-local root skill.
- Moved E1002 local service URL and device ID expectations into private `secrets.h` configuration via the public `secrets.h.example` template.
- Rewrote public README and test docs to remove private workspace paths, fixed LAN IPs, and private deployment assumptions.

## Lessons Learned

- Generated usage files are private artifacts, even when aggregated. Keep `usage.json`, `cursor.csv`, `glm.json`, `token_usage_dashboard.png`, `token_usage_eink.json`, and logs ignored.
- The canonical skill should live inside the project repo. Global workspace skill entries should point to it rather than duplicating content.
- Keep local hardware/network configuration in ignored files. Public firmware should rely on `secrets.h.example` placeholders.
