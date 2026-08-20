# Changelog

## Unreleased

### v2.3-minimal-follow

- Replaced the default network-control strategy with a minimal Cloudflared follow mode: Clash/VPN node selection stays native/manual, while the watcher only reacts to TUN-up, Mihomo restart, or a stable selected-node change.
- Stable manual node changes are debounced before restarting Cloudflared; no group-wide delay probe, node reselection, rolling Argotunnel refresh, or managed proxy-group rewrite runs in follow mode.
- Cloudflared restart now has bounded stop/start waits plus a forced process kill fallback for a service stuck in `Stop Pending`, followed by an SCM stop-state wait before restart.
- Follow mode installs the watcher task at `Highest` so it can restart the Windows Cloudflared service without elevating Clash/Mihomo control logic.
- Region-priority policy is off by default and hard-gated from `install`, `update`, and `apply-region-policy` while follow mode is active, including machines with stale `region_priority_enabled=true` overrides.
- WORK field validation: manual node change and TUN off/on both automatically rebuilt Cloudflared; MCP returned healthy at ~179-182 ms through SIN after warm-up. HOME native baseline measured ~168 ms.

### v2.2-region-priority

- Added a managed Clash Verge subscription extension script that keeps the normal automatic route in a strict `Singapore URLTest -> Taiwan fallback` hierarchy.
- Singapore nodes now optimize only against other Singapore nodes; Taiwan is no longer allowed to win because of a small synthetic-delay difference.
- Added `python control.py apply-region-policy` with ignored local backups and Mihomo hot reload for immediate activation.
- `install` / `update` synchronize the managed region policy when enabled without reloading an already-current policy unnecessarily.
- Added nested Mihomo group-to-leaf selection-path resolution so Cloudflared chain mismatch compares against the real leaf proxy.
- Nested recovery health checks now refresh the inner regional group before the outer fallback group.
- WORK validation observed a pre-fix mixed Cloudflared state with Taiwan on most HA paths and >470 ms MCP latency; after region policy + rolling chain alignment, all HA paths used Singapore and MCP returned to ~171 ms.

### v2.1-stability

- Moved the project goal/plan/implementation/current-checkpoint document into tracked `docs/implementation-plan-status.md` so HOME/WORK and new agent sessions resume from the same Git-synchronized breakpoint.
- `control.py` now re-executes itself with the machine-configured Python when the shell's `python` points to another virtual environment.
- Relaxed healthy hot-sample count from 4/5 to 3/5 while keeping median <=200 ms and HTTP status checks.
- Added latency classes: healthy, degraded-observe, severe, HTTP failure, and transport failure.
- 200-350 ms median now uses read-only observation/retry instead of immediately mutating Cloudflared connections.
- Added explicit Mihomo-selected-node vs Argotunnel-chain mismatch detection.
- Severe latency on an already-aligned Tunnel now prefers Mihomo reselection before refreshing the same path.
- Node changes that occur during a long recovery are deferred into the next event-loop iteration instead of being silently absorbed.

### Unified open-source layout

- One shared codebase for multiple Windows machines.
- Added tracked `config/default.json` plus ignored `config/local.json` machine overlay.
- Added Python-only `control.py update` using `git pull --ff-only`; removed the PowerShell control plane.
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
