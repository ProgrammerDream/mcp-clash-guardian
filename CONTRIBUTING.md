# Contributing

Thanks for considering a contribution.

## Scope

This project intentionally keeps a narrow responsibility boundary:

- Mihomo/Clash owns routing and proxy selection.
- MCP Clash Guardian observes state, verifies the real MCP endpoint, and performs conservative recovery only after confirmed failure.
- Avoid adding a second node-ranking or proxy-scheduling engine unless there is strong evidence that Mihomo cannot own that responsibility.

## Development

On Windows:

```powershell
python -m pip install -r requirements.txt
python -m py_compile control.py src\config_loader.py src\check_mcp.py src\mihomo_api.py src\watcher.py
```

## Configuration changes

Shared behavior belongs in `config/default.json`.

Machine-local values belong only in ignored `config/local.json` and must not be committed.

## Security

Do not attach real CodexPro profile files, tokens, Cloudflare tunnel credentials, private keys, or unsanitized runtime logs to issues or pull requests.

## Pull requests

Keep changes small and evidence-driven. For recovery-policy changes, include the observed failure mode and explain why the new action will not create a stronger control-loop oscillation than the problem it fixes.
