# Operations

## Daily commands

```powershell
python control.py status
python control.py logs
python control.py logs --tail 100
python control.py run
```

The default strategy is `v2.3-minimal-follow`. Clash/VPN remains the source of truth for node selection. The watcher only follows TUN/Mihomo/node-path changes and rebuilds Cloudflared when needed.

`apply-region-policy` is intentionally blocked while `watcher_mode=cloudflared-follow`. The legacy managed `Singapore -> Taiwan fallback` strategy remains available only after an explicit mode change.

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
strategy_version       : v2.3-minimal-follow
watcher_state          : Running
phase                  : monitoring
tun_up                 : True
mihomo_api             : True
region_tier            : None
selection_path         : 飞鸟云 -> <selected leaf node>
argotunnel_connections : 2 or more
mcp_ok                 : None
hot_median_ms          : None
last_error             : empty
```

Follow mode intentionally does not run periodic MCP optimization. Use `python src/check_mcp.py` for an on-demand end-to-end latency check; the operational target is a hot median below 200 ms.

## Event policy

### Manual/native node change

```text
node A -> node B
→ 8 s debounce / stability confirmation
→ restart Cloudflared once
→ wait for service/tunnel settle
```

### TUN or Mihomo restart

```text
TUN up / Mihomo PID changed
→ short settle
→ restart Cloudflared once
→ wait for service/tunnel settle
```

### What follow mode never does

```text
no proxy-group rewrite
no region-priority group creation
no group-wide /group/<name>/delay probe
no automatic node reselection
no rolling Argotunnel connection deletion
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
