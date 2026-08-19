# MCP Clash Guardian — Goal, Plan, Implementation & Status

> **Single source of truth for project continuation.** This file tracks the project goal, architecture decisions, implementation plan, current rollout state, validation criteria, and next actions across machines. After `git pull`, an AI/agent or operator should read this file before continuing work.
>
> Runtime operation belongs in `README.md` / `OPERATIONS.md`; strategy history belongs in `CHANGELOG.md`; machine-local values belong only in ignored `config/local.json` and `runtime/`.

## 1. Goal

Converge multiple Windows machines from independently maintained Clash/Mihomo + Cloudflared MCP recovery scripts into one open-source repository:

```text
Git repository: mcp-clash-guardian
        │
        ├─ one watcher implementation
        ├─ one MCP health checker
        ├─ one Mihomo named-pipe API client
        ├─ one Python control plane
        ├─ one shared default policy
        ├─ one README / OPERATIONS / CHANGELOG
        └─ one cross-device implementation/status document (this file)

HOME clone                         WORK clone
    │                                  │
config/local.json                 config/local.json
machine-only values               machine-only values
    │                                  │
runtime/                          runtime/
local state/logs                  local state/logs
```

Normal update path:

```powershell
python control.py update
```

`config/local.json` and `runtime/` are Git ignored and must survive pulls/updates.

## 2. Responsibility boundary

```text
Clash Verge / Mihomo
= TUN + routing + proxy health + automatic proxy selection

MCP Clash Guardian
= observe TUN / Mihomo / selected-node changes
+ validate the real MCP endpoint
+ classify failures
+ avoid unnecessary network mutations
+ recover Cloudflared paths only after confirmed failure
```

The watcher must **not** become a second proxy scheduler.

## 3. Current shared strategy — v2.1-stability

### Normal node change

```text
Mihomo URLTest changes node
→ debounce until selection is stable
→ test real MCP endpoint
→ healthy: zero Tunnel action
```

### Health classification

```text
HTTP expected + median <= 200 ms
→ healthy

HTTP expected + median 200–350 ms
→ latency_degraded
→ read-only observation/retry
→ do not mutate Cloudflared just to chase latency

HTTP expected + median >= 350 ms
→ latency_severe
→ confirm before recovery

502 / 530 / Cloudflare 1033 / request transport failure
→ hard failure class
→ confirm before recovery
```

Default healthy sampling:

```text
3 warm-up requests
5 hot requests
expected HTTP status = 400
hot median <= 200 ms
at least 3/5 hot requests <= 200 ms
```

### Safe recovery

```text
confirmed failure
→ compare Mihomo selected node vs existing Cloudflared UDP/7844 chain
→ if Tunnel is clearly stuck on an old node, rolling refresh is allowed
→ if path is already aligned, prefer Mihomo native health/reselection before refreshing same path
→ rolling refresh changes one HA connection at a time
→ Mihomo group-delay/reselection is cooldown protected
→ final settle phase is read-only
```

### Control-loop protections

- URLTest changes are debounced.
- A single slow sample does not mutate the network.
- 200–350 ms median is observation-only.
- Rolling refresh is a rescue action, never a normal node-change action.
- Subscription-information pseudo nodes are detected and not treated as valid Tunnel migration targets.
- Node changes that happen during a long recovery are deferred back to the next event-loop iteration instead of being silently absorbed.
- Degraded latency is checked again sooner than the normal periodic health interval.

## 4. Repository layout

```text
mcp-clash-guardian/
├─ README.md
├─ OPERATIONS.md
├─ CHANGELOG.md
├─ CONTRIBUTING.md
├─ SECURITY.md
├─ LICENSE
├─ control.py
├─ requirements.txt
├─ docs/
│  └─ implementation-plan-status.md   # this file; cross-device continuation source
├─ config/
│  ├─ default.json                    # tracked shared policy
│  ├─ local.example.json              # tracked template
│  └─ local.json                      # ignored machine-local overlay
├─ src/
│  ├─ config_loader.py
│  ├─ check_mcp.py
│  ├─ mihomo_api.py
│  └─ watcher.py
└─ runtime/                            # ignored local status/logs
```

## 5. Cross-device continuation workflow

When continuing work on any machine or in a new chat/session:

```text
1. git pull --ff-only
2. read docs/implementation-plan-status.md
3. read CHANGELOG.md if strategy history is relevant
4. run: python control.py status
5. inspect runtime logs only when troubleshooting that machine
6. implement against shared src/config/default.json
7. validate locally
8. update this document's task/status section if project state changed
9. update CHANGELOG.md if behavior/policy changed
10. commit/push shared changes
```

Do **not** create separate HOME/WORK copies of watcher code, policy documentation, or implementation-status documents.

## 6. Open-source / privacy boundary

Tracked public files may contain:

- generic architecture and recovery strategy;
- source code;
- shared defaults;
- sanitized rollout state (`HOME`, `WORK`, latency ranges, strategy version);
- generic examples and troubleshooting guidance.

Tracked public files must not contain:

- MCP/CodexPro credentials or tokens;
- contents of local profile files;
- private keys or Cloudflare tunnel credentials;
- machine-specific profile paths;
- machine-specific `config/local.json`;
- runtime logs/status dumps;
- private hostnames that are intentionally kept local.

Credentials are read at runtime through the local profile path and remain machine-local.

## 7. Implementation plan & current status

| # | Task | Status | Result / continuation note |
|---|---|---|---|
| 1 | Establish one shared repository layout | ✅ Done | HOME/WORK code branching removed from shared implementation |
| 2 | Python-only control plane | ✅ Done | `control.py` owns install/update/status/logs/run/start/stop/uninstall/rollback |
| 3 | Interpreter bootstrap | ✅ Done | `python control.py ...` re-execs with machine-configured Python when PATH points to another venv |
| 4 | Shared default + local overlay model | ✅ Done | `config/default.json` tracked; `config/local.json` ignored |
| 5 | v2.1 stability policy | ✅ Done | latency classification, chain mismatch detection, deferred node-change handling |
| 6 | WORK migration to shared repository | ✅ Done | watcher task runs shared `src/watcher.py`; Python-only control validated |
| 7 | WORK current functional validation | ✅ Done | healthy MCP observed around mid-160–170 ms range, expected HTTP status, Singapore Cloudflare edge |
| 8 | HOME migration to shared repository | ⚠️ Pending | HOME connector was returning 502 during the last migration attempt; existing stable HOME watcher intentionally left untouched |
| 9 | HOME shared-repo functional validation | ⏳ Pending | migrate only after connector is reachable; then validate status/run/TUN recovery and latency |
| 10 | Create/push public GitHub repository | ✅ Done | public repository created and `main` pushed to GitHub under `ProgrammerDream/mcp-clash-guardian` |
| 11 | Bind both machines to same remote and test update | 🔄 Partial | WORK `main` now tracks `origin/main`; HOME remote/update validation remains pending |
| 12 | Archive HOME pre-unification implementation | ⏳ Pending | only after HOME shared implementation passes validation |
| 13 | Make this Git-tracked file the sole implementation/status document | ✅ Done | legacy infrastructure document becomes a pointer only |

## 8. Current checkpoint

```text
Shared strategy: v2.1-stability
Control plane: Python-only
Current shared-code machine: WORK
WORK watcher: Running
WORK current path: healthy after latest recovery/strategy iteration
WORK recent healthy MCP: ~165–170 ms class
WORK Cloudflare edge observed: Singapore
Shared local Git: active; WORK main tracks origin/main
HOME: existing stable implementation preserved; unified migration pending connector recovery
GitHub remote: public repository created and initial main push completed
```

Important recent findings already incorporated into v2.1:

1. A shell may resolve `python` to an unrelated virtual environment without `pywin32`; `control.py` now bootstraps into the configured interpreter.
2. Treating `median ~180 ms` plus a few >200 ms samples as a failure caused unnecessary rolling refresh and much worse latency; v2.1 separates degraded latency from real failures.
3. Mihomo may change node while Cloudflared long-lived UDP/7844 connections remain on the previous proxy chain; v2.1 detects this explicit mismatch.
4. A node change occurring during a long recovery must not be swallowed by updating `last_node` at the end of the same recovery.

## 9. Acceptance criteria

### Shared maintenance

- HOME and WORK run the same shared source commit after migration completes.
- Shared `src/`, `control.py`, policy docs, and this status document exist only once in Git.
- Machine differences exist only in ignored local config/runtime.
- `git pull` / `python control.py update` preserve local config and runtime.
- Public tracked files contain no credentials/runtime logs/private profile data.

### Functional

- Normal URLTest node changes do not mutate Tunnel paths when MCP remains healthy.
- 200–350 ms latency degradation is observed before any mutation.
- Confirmed hard/severe failures can recover automatically without deleting all HA paths at once.
- `502`, `530`, and Cloudflare `1033` are distinguishable in logs.
- TUN/Mihomo restarts eventually return to a healthy MCP state without manual service restart in the validated topology.

## 10. Next actions

Current continuation order:

```text
1. Verify remote + `python control.py update` on WORK.
2. Reconnect to HOME when its connector is healthy.
3. Clone/pull the same repository on HOME.
4. Create HOME `config/local.json` only; do not fork shared policy/code.
5. Install shared watcher task on HOME.
6. Validate HOME status/run/TUN recovery/latency.
7. Archive HOME's pre-unification implementation.
8. Confirm HOME and WORK are on the same Git commit.
```

---

**Maintenance rule:** after this file enters Git, any future project-level breakpoint/state update goes here. Do not recreate machine-specific implementation-status Markdown files.
