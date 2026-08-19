# Changelog

## Unreleased

### Unified open-source layout

- One shared codebase for multiple Windows machines.
- Added tracked `config/default.json` plus ignored `config/local.json` machine overlay.
- Added `control.ps1 update` using `git pull --ff-only`.
- Removed HOME/WORK code branches from the shared implementation.

### v2-stability

- URLTest node changes are debounced before any reaction.
- A node change no longer implies a Cloudflared connection refresh.
- Real MCP health is checked first; healthy MCP means zero network mutation.
- Added transient failure confirmation before recovery.
- Added subscription-information pseudo-node protection.
- Changed Cloudflared UDP/7844 recovery from all-at-once deletion to rolling HA refresh.
- Added a safety floor so the watcher refuses to intentionally drop the last usable Tunnel paths.
- Switched Mihomo health-check target to `https://cp.cloudflare.com/generate_204`.
- Added Mihomo group-delay cooldown to reduce control-loop oscillation.
- Added `502`, `530`, and Cloudflare `1033` health signatures to logs.
- Final recovery stage is read-only settle/retry instead of repeated route mutation.

## v1

- Initial Clash Verge / Mihomo watcher.
- TUN/PID/URLTest monitoring.
- Real MCP warm-up + hot latency checks.
- Mihomo named-pipe integration.
- Cloudflared UDP/7844 connection discovery and refresh.
