# Operations

## Daily commands

```powershell
python control.py doctor
python control.py status
python control.py logs
python control.py logs --tail 100
python control.py run
```

## Layered diagnosis

`doctor` is the first command to run for any "MCP feels wrong" report. It is
read-only and mutates nothing, so it is also safe on a half-built machine.

```powershell
python control.py doctor
python control.py doctor --json
```

The path is five layers, each coupled to the next only through a localhost port
or a public hostname:

```text
L1 exit      Mihomo is up, TUN is up, traffic leaves through the node we think
L2 tunnel    Cloudflared holds HA connections to the Cloudflare edge
L3 origin    the MCP server answers on localhost
L4 edge      the public hostname answers end to end
L5 guardian  the watcher is installed, running, and reporting the truth
```

A failure low in the list explains every failure above it, so fix the layer the
verdict names and re-run rather than reacting to each symptom. Exit code is `0`
for ok/warn and `2` when any layer fails.

The two latency numbers are meant to be read together: L1 reports the
client-to-edge round trip measured by `/cdn-cgi/trace`, which never touches the
tunnel, and L4 reports the same request end to end. The difference is the tunnel
return leg. A slow L4 with a fast L1 is a tunnel/exit problem; both slow is
distance to the edge.

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
selection_path         : <follow group> -> <selected leaf node>
follow_source          : group
argotunnel_connections : 2 or more
mcp_ok                 : None
hot_median_ms          : None
last_error             : empty
```

Follow mode intentionally does not run periodic MCP optimization. Use `python control.py doctor` for an on-demand end-to-end latency check; the operational target is a hot median below 200 ms.

`phase` is only ever `monitoring` when the watcher can actually resolve a follow
target. If the target cannot be resolved it reports `degraded`, because a blind
watcher that claims to be monitoring is worse than one that admits it is not.

`follow_source` says where the followed node came from:

```text
group                  the configured policy group (normal)
argotunnel_chain       the group was missing, so the live tunnel chains were used
argotunnel_chain_mixed the tunnel is straddling two exit nodes; no target chosen
none                   nothing resolvable; phase is degraded
```

Anything other than `group` for a sustained period means `follow_group_name` no
longer matches the subscription. The watcher keeps working from the tunnel's own
chains, but fix the name so intent and observation agree again.

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
no node selection for optimization
no rolling Argotunnel connection deletion
```

### After a restart it checks, and says so

A Cloudflared service in state `Running` only means the process started. If it
never registers with the Cloudflare edge, the public hostname serves `530` with
a Cloudflare `1033` body, the outage is total, and the log still shows a
perfectly successful restart.

So after a restart the watcher waits up to `tunnel_ready_timeout_seconds` for
`readyConnections` to become non-zero, logs `tunnel_ready_check`, and if it
stays zero writes `phase: degraded` with the node named in `last_error`.

It does not try to repair the situation. Picking nodes belongs to the operator,
and a watcher that silently puts a different node back is harder to reason about
than one that tells you which node broke the tunnel. Read the reason, pick a
node that works.

Read-only checks are cheap and can be generous. Every automatic *action* is a
new way for the thing to fail while nobody is watching, so follow mode keeps
exactly one: restart Cloudflared when the node changes.

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
