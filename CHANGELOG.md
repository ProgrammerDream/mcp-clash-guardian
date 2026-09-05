# Changelog

## Unreleased

### v2.3.3-one-action

- Removed the boot-recovery restart. It restarted Cloudflared unconditionally 60 seconds after every watcher start, including on a fully healthy tunnel, and it needed a forced kill each time. On the day it was reviewed it fired twice and interrupted a working tunnel twice.
- The case it was written for is already covered: if TUN comes up after the watcher, the loop sees the rising edge and restarts. The residual case, Cloudflared connecting before TUN was ready and staying on a stale path, is now diagnosable in one read-only command, since `doctor` L1 shows an empty `tunnel_chain_nodes` when the connector is not egressing through the proxy at all.
- Follow mode now performs exactly one automatic action: restart Cloudflared when the followed node changes. Everything else it does is read and report. Removes `cloudflared_boot_recovery_enabled` and `cloudflared_boot_recovery_delay_seconds`.

### v2.3.2-tunnel-verification

- The watcher now checks that Cloudflared actually registered with the Cloudflare edge after it restarts, instead of treating a `Running` service as success. A connector that starts but never registers serves `530`/`1033` on the public hostname, and that outage was previously invisible: the log recorded a clean restart and `phase` stayed `monitoring`. It now logs `tunnel_ready_check` and writes `phase: degraded` with the node named in `last_error`.
- It does not try to repair that situation. An earlier draft of this change restored the last node known to carry the tunnel; that was cut. Choosing nodes belongs to the operator, and the transport fix below removed the failure it was written for. Follow mode keeps exactly one automatic action: restart Cloudflared when the node changes.
- Added `src/cloudflared_service.py`, so the watcher and the doctor share one implementation of service inspection, metrics-endpoint discovery and connector readiness rather than keeping parallel copies.
- `doctor` L2 now also reports `protocol_flag`, so the configured edge transport is visible next to the observed one.
- New tracked default: `tunnel_ready_timeout_seconds`.

Field note: an exit node that could not carry Cloudflared's default QUIC transport took the hostname down with 1033 while every log line looked healthy. Running the connector with `--protocol http2` fixed it outright, at no measurable latency cost. On a proxied path where UDP is the fragile part, http2 is the better default.

### v2.3.1-observability

- Added `python control.py doctor`: a read-only, five-layer diagnosis (exit, tunnel, origin, edge, guardian) that names the first failing layer instead of leaving the operator to probe each hop by hand. `--json` for machine-readable output; exit code `2` when any layer fails.
- `doctor` runs without a complete `config/local.json`, so a half-built machine gets findings rather than a crash.
- The watcher no longer goes blind when the configured follow group is missing. `follow_group_name` is now the intent; the live Argotunnel connection chains are the fallback, and `follow_source` records which one was used.
- `phase` reports `degraded` when the follow target cannot be resolved. It previously reported `monitoring` while the watcher had no target at all.
- TUN state now debounces its falling edge (`tun_down_confirmations`, default 2 polls). A single missing WMI adapter row used to flip TUN down and back up, and that synthetic rising edge restarted Cloudflared for nothing.
- Follow mode now refuses to restart Cloudflared onto an entry matching `invalid_node_patterns`. The patterns existed but were only consulted in guardian mode, so a subscription placeholder such as a remaining-quota line could become the tunnel path.
- Argotunnel connection selection and chain-leaf extraction moved into pure functions shared by the watcher and the doctor, so both use one definition of "this is a tunnel connection".
- Console output no longer dies on a legacy code page: node names carrying emoji used to raise `UnicodeEncodeError` and kill `status` / `logs`. Redirected output is now UTF-8; an interactive console keeps its own code page.
- New tracked defaults: `origin_base_url`, `expected_ready_connections`, `status_stale_seconds`, `tun_down_confirmations`, `cloudflared_metrics`.

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
