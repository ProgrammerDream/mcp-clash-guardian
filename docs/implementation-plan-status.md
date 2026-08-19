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

## 3. Current shared strategy — v2.2-region-priority

### Region priority

```text
automatic selection (fallback)
├─ Singapore automatic (URLTest, primary)
│  └─ Singapore leaf proxies only
└─ Taiwan fallback (URLTest, disaster recovery)
   └─ Taiwan leaf proxies only
```

Singapore nodes compete only with other Singapore nodes. Taiwan must not become the normal route while the Singapore group is healthy. The policy is persisted in the active Clash Verge subscription extension script and can be replayed with `python control.py apply-region-policy`.

### Normal node change

```text
Mihomo regional URLTest changes the leaf node
→ resolve nested policy path to the real leaf proxy
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

- Singapore and Taiwan are separate regional URLTest pools; the outer fallback preserves Singapore-first ordering.
- Nested policy groups are resolved to the final leaf before node-change and chain-mismatch decisions.
- URLTest leaf changes are debounced.
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
│  ├─ clash_verge_policy.py             # managed SG-first/TW-fallback integration
│  └─ watcher.py
└─ runtime/                              # ignored local status/logs/backups
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
| 5 | v2.1 stability foundation | ✅ Done | latency classification, chain mismatch detection, deferred node-change handling |
| 6 | WORK migration to shared repository | ✅ Done | watcher task runs shared `src/watcher.py`; Python-only control validated |
| 7 | WORK current functional validation | ✅ Done | healthy MCP observed around mid-160–170 ms range, expected HTTP status, Singapore Cloudflare edge |
| 8 | HOME migration to shared repository | ✅ Done | HOME cloned the same repository, created only ignored `config/local.json`, and switched the existing watcher task to shared `src/watcher.py` |
| 9 | HOME shared-repo functional validation | ✅ Done | shared `control.py run/install/update/status` passed; healthy MCP observed around ~159–170 ms with Singapore edge and 2 HA connections |
| 10 | Create/push public GitHub repository | ✅ Done | public repository created and `main` pushed to GitHub under `ProgrammerDream/mcp-clash-guardian` |
| 11 | Bind both machines to same remote and test update | ✅ Done | WORK and HOME both use the same GitHub remote; `python control.py update` passed on both machines without replacing local config/runtime |
| 12 | Archive HOME pre-unification implementation | ✅ Done | old HOME standalone Clash watcher moved to `docs/archive/network/mcp-clash-home-pre-unification-2026-08-19` outside the shared repository |
| 13 | Make this Git-tracked file the sole implementation/status document | ✅ Done | legacy infrastructure document becomes a pointer only |
| 14 | v2.2 Singapore-first / Taiwan-fallback rollout | 🔄 Partial | WORK applied and validated: 15 SG + 9 TW nodes, outer fallback selects SG tier, 4 HA paths aligned to SG, MCP recovered to ~171 ms; HOME update pending |

## 8. Current checkpoint

```text
Shared strategy: v2.2-region-priority (WORK active; HOME rollout pending this commit)
Control plane: Python-only
Shared-code machines: WORK + HOME
WORK watcher: Running; region path = automatic selection -> Singapore automatic -> Singapore leaf; 4 HA connections aligned to Singapore; healthy MCP ~171 ms after settle
HOME watcher: Running on prior shared commit until v2.2 is pulled/applied
WORK and HOME: same repository / machine-only differences remain in ignored local config/runtime
GitHub remote: public `ProgrammerDream/mcp-clash-guardian`
WORK region-policy backup: local ignored runtime/backups entry created before persistent extension-script change
Pre-unification standalone implementations: archived and removed from active run paths
```

Important recent findings already incorporated into v2.2:

1. A shell may resolve `python` to an unrelated virtual environment without `pywin32`; `control.py` now bootstraps into the configured interpreter.
2. Treating `median ~180 ms` plus a few >200 ms samples as a failure caused unnecessary rolling refresh and much worse latency; v2.1 separates degraded latency from real failures.
3. Mihomo may change node while Cloudflared long-lived UDP/7844 connections remain on the previous proxy chain; v2.1 detects this explicit mismatch.
4. A node change occurring during a long recovery must not be swallowed by updating `last_node` at the end of the same recovery.
5. A global URLTest can select Taiwan even though the real MCP path becomes dramatically slower. WORK evidence showed most Cloudflared HA paths on a Taiwan leaf while MCP rose above ~470 ms; strict SG-first regional fallback plus chain alignment returned the path to ~171 ms.

## 9. Acceptance criteria

### Shared maintenance

- HOME and WORK run the same shared source commit after migration completes.
- Shared `src/`, `control.py`, policy docs, and this status document exist only once in Git.
- Machine differences exist only in ignored local config/runtime.
- `git pull` / `python control.py update` preserve local config and runtime.
- Public tracked files contain no credentials/runtime logs/private profile data.

### Functional

- With Singapore nodes healthy, the outer automatic group remains on the Singapore tier; Taiwan is only a fallback tier.
- Nested policy paths resolve to a real leaf proxy before Cloudflared chain comparison.
- Normal regional URLTest leaf changes do not mutate Tunnel paths when MCP remains healthy.
- 200–350 ms latency degradation is observed before any mutation.
- Confirmed hard/severe failures can recover automatically without deleting all HA paths at once.
- `502`, `530`, and Cloudflare `1033` are distinguishable in logs.
- TUN/Mihomo restarts eventually return to a healthy MCP state without manual service restart in the validated topology.

## 10. Next actions

Current continuation order:

```text
1. Commit/push the validated v2.2 region-priority implementation from WORK.
2. Run `python control.py update` on HOME; it should install/sync the managed policy and preserve HOME local config/runtime.
3. Validate HOME `selection_path` stays on the Singapore tier and MCP remains healthy.
4. Update this checkpoint to mark v2.2 rollout complete and confirm both machines share the same commit.
5. Thereafter, normal operation returns to `python control.py update` on both machines and evidence-driven stability tuning only.
```

Unified migration is complete. Future work is iterative stability tuning rather than another HOME/WORK migration project.

---

**Maintenance rule:** after this file enters Git, any future project-level breakpoint/state update goes here. Do not recreate machine-specific implementation-status Markdown files.
