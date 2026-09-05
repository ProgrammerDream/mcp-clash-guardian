"""Console output that survives a legacy code page.

Proxy node names carry emoji and CJK characters. On a Windows console still
running GBK, printing them raises UnicodeEncodeError and kills the command, so
every human-readable line goes through here first.
"""
from __future__ import annotations

import sys

_configured = False


def _configure() -> None:
    """Emit UTF-8 whenever stdout is a pipe rather than a console.

    A redirected stdout is being read by another program, which will assume
    UTF-8; an interactive console must keep its own code page or the user sees
    mojibake instead. Only the pipe case can be safely upgraded.
    """
    global _configured
    if _configured:
        return
    _configured = True
    try:
        if not sys.stdout.isatty():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_text(value: object) -> str:
    _configure()
    text = "" if value is None else str(value)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    return text


def print_line(value: object = "") -> None:
    print(safe_text(value))
