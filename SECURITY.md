# Security Policy

## Supported configuration model

Machine secrets and private environment details must stay outside version control.

Never commit:

- `config/local.json` if it contains private hostnames or local paths you do not want public;
- CodexPro profile JSON files or MCP tokens;
- Cloudflare tunnel credentials;
- private keys or certificates;
- runtime logs/status files from private environments.

The project intentionally reads the MCP credential from the configured local CodexPro profile only at runtime. The token is not part of the tracked configuration and is not intentionally logged.

## Reporting a vulnerability

Please open a GitHub security advisory or contact the repository maintainer privately for vulnerabilities that could expose credentials or allow unintended command execution. Avoid posting working secrets or private logs in a public issue.

## Before publishing a fork

Review:

```text
config/local.json
runtime/
.env*
*.key / *.pem / *.p12 / *.pfx
historical logs
```

The repository `.gitignore` excludes the common local files, but secret review is still the operator's responsibility.
