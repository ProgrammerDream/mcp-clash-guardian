# Operations

## Daily commands

```powershell
python control.py status
python control.py logs
python control.py logs --tail 100
python control.py run
python control.py apply-region-policy
```

`apply-region-policy` repairs/replays the managed `Singapore -> Taiwan fallback` policy for the active Clash Verge subscription. It creates a local ignored backup before replacing the default/managed extension script and hot-reloads Mihomo for immediate effect.

## Lifecycle

```powershell
python control.py start
python control.py stop
python control.py install
python control.py uninstall
python control.py rollback
```

`rollback` removes only the watcher task and disables the local automation flag. It does not automatically revert a managed region-priority extension script; local backups are kept under `runtime/backups/clash-verge-region-priority/`.

## Update

```powershell
python control.py update
```

This uses `git pull --ff-only`. A divergent local branch is intentionally not auto-merged; resolve it explicitly instead of letting an unattended network watcher rewrite source history.

## What status means

Healthy example:

```text
strategy_version       : v2.2-region-priority
watcher_state          : Running
phase                  : monitoring
tun_up                 : True
mihomo_api             : True
region_tier            : 新加坡自动
selection_path         : 自动选择 -> 新加坡自动 -> <leaf Singapore node>
argotunnel_connections : 2 or more
mcp_ok                 : True
hot_median_ms          : < threshold
last_error             : empty
```

With the managed region policy, `region_tier=台湾兜底` is expected only when the Singapore policy group is unavailable. If Singapore nodes are healthy and the tier remains Taiwan, inspect `selection_path` and re-run `python control.py apply-region-policy`.

## Event policy

### URLTest node change

```text
node A -> node B
→ debounce
→ real MCP test
→ healthy: no action
→ confirmed failure: recovery
```

### TUN or Mihomo restart

```text
TUN up / Mihomo PID changed
→ short settle
→ real MCP test
→ confirmed failure only: recovery
```

### Recovery

```text
confirm failure
→ if invalid subscription-info node: do not migrate Tunnel to it
→ otherwise rolling-refresh UDP/7844
→ re-test MCP
→ optional Mihomo group delay (cooldown protected)
→ if selected node really changed, rolling refresh again
→ final read-only settle retries
→ degraded only after all retries fail
```

## 502 / 530 / 1033

Check the JSONL log for `mcp_check` records:

```json
{
  "hot_statuses": [530, 530, 530, 530, 530],
  "hot_error_signatures": ["cloudflare_1033"]
}
```

Interpretation:

- `502`: upstream/tunnel path temporarily unavailable or failing.
- `530` with a Cloudflare `1033` body: Cloudflare cannot currently find a usable Tunnel connector for the hostname.
- HTTP `400` with acceptable hot latency is the expected healthy MCP probe result for this deployment model.

## Local configuration

`config/local.json` is the only machine-specific file. Keep it local.

Typical differences between machines are only:

```text
machine_name
public_host
profile_path
python_exe
watcher_task_name
```

All policy tuning should normally go into tracked `config/default.json` so every machine receives the same behavior after pull.

## Logs and privacy

The credential is read from the profile at runtime and used only to build the in-memory MCP request. It is not intentionally written to `runtime/automation.jsonl`.

Before attaching logs to public issues, still review them for hostnames, paths, node names, or other environment details you do not want to disclose.
