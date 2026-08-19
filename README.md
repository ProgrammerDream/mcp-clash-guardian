# MCP Clash Guardian

A Windows stability watcher for **Clash Verge / Mihomo + Cloudflared + a public MCP endpoint**.

It was built for a specific failure mode: when Mihomo's automatic proxy selection changes, long-lived Cloudflared QUIC connections may temporarily stay on an old path or become unavailable. Aggressively restarting every connection can make this worse and surface `502`, `530`, or Cloudflare `1033` errors.

MCP Clash Guardian uses a conservative strategy:

```text
Mihomo URLTest changes node
        ↓
debounce and wait for a stable choice
        ↓
measure the real public MCP endpoint
        ↓
healthy → do nothing
        ↓
confirmed unhealthy → safe recovery only then
        ↓
rolling Cloudflared UDP/7844 refresh
        ↓
optional Mihomo group delay / reselection with cooldown
        ↓
read-only settle retries
```

The core rule is simple:

> **Proxy selection belongs to Mihomo. The watcher observes and verifies; it does not become a second proxy scheduler.**

## Features

- Windows scheduled-task watcher, runs as the current user with `Limited` privilege.
- Uses `pythonw.exe` when available, so no permanent console window is shown.
- Reads Clash Verge/Mihomo through the Windows named pipe `\\.\pipe\verge-mihomo`; no extra HTTP controller port is required.
- Watches:
  - Meta TUN down/up;
  - Mihomo process restart;
  - URLTest selected-node changes;
  - periodic end-to-end MCP health.
- 10-second URLTest node debounce before reacting.
- Real MCP validation: warm-up + hot samples, HTTP status and latency threshold.
- Detects `502`, `530`, and Cloudflare `1033` signatures in health logs.
- Protects against subscription-information entries such as “remaining traffic” or “expiry date” being selected as if they were normal proxies.
- Does **not** touch Cloudflared while the real MCP endpoint remains healthy.
- On confirmed failure, refreshes UDP/7844 connections one HA path at a time and waits for replacement before touching the next one.
- Mihomo group-delay operations have a cooldown to avoid control-loop oscillation.
- Machine-specific configuration and runtime logs are excluded from Git.

## Requirements

- Windows 10/11.
- Clash Verge Rev / Mihomo with TUN enabled.
- Cloudflared tunnel already configured and running.
- Python 3.10+.
- A CodexPro-compatible MCP public endpoint where a valid profile token can be read locally from a CodexPro profile JSON.

Install Python dependency:

```powershell
python -m pip install -r requirements.txt
```

`control.ps1 install` also installs the requirement automatically.

## Quick start

Clone the repository, then create the machine-local configuration:

```powershell
Copy-Item .\config\local.example.json .\config\local.json
notepad .\config\local.json
```

Example:

```json
{
  "machine_name": "desktop",
  "public_host": "mcp.example.com",
  "profile_path": "C:\\Users\\me\\.codexpro\\profiles\\profile.json",
  "python_exe": "C:\\Users\\me\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
  "watcher_task_name": "MCP Clash Guardian"
}
```

Then:

```powershell
.\control.ps1 install
.\control.ps1 status
```

`config/local.json` is intentionally ignored by Git.

## Update

Normal update is one command:

```powershell
.\control.ps1 update
```

It performs:

```text
stop watcher task
→ git pull --ff-only
→ install/update Python requirements
→ start watcher task
→ print current status
```

Your `config/local.json` and `runtime/` directory are not tracked and are preserved across updates.

## Health policy

Default policy lives in `config/default.json`.

The default MCP acceptance rule is:

```text
3 warm-up requests
5 hot requests
expected HTTP status = 400
hot median <= 200 ms
at least 4/5 hot requests <= 200 ms
```

A single slow request or one transient `502/530` does not immediately mutate the network. The watcher confirms the failure first.

## Why rolling refresh is only a rescue action

Cloudflared commonly keeps multiple HA QUIC paths. Deleting all of them at once can create a short interval where Cloudflare cannot find a healthy connector, which may surface as a `1033` page.

This project therefore refreshes one detected UDP/7844 connection at a time and waits until the HA count has recovered before continuing.

Even rolling refresh can interrupt an individual in-flight session, so it is **never** performed just because URLTest selected a different node. It is reserved for confirmed MCP failure.

## Files

```text
README.md                 public overview
OPERATIONS.md             operator commands and troubleshooting
CHANGELOG.md              strategy history
SECURITY.md               secret-handling policy
LICENSE                    MIT
control.ps1                install/update/status/run/stop/start entrypoint
requirements.txt
config/default.json        tracked shared policy
config/local.example.json  tracked template
config/local.json          ignored machine-local values
src/config_loader.py
src/check_mcp.py
src/mihomo_api.py
src/watcher.py
runtime/                   ignored status/log output
```

## Security

The repository does not need the actual MCP credential in source code or config. `src/check_mcp.py` reads it from the local profile at runtime and never writes it into the JSONL log.

Do not commit `config/local.json`, CodexPro profile files, Cloudflare tunnel credentials, private keys, or runtime logs containing private environment information.

See [SECURITY.md](SECURITY.md).

## License

MIT.
