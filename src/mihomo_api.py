from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import win32file

DEFAULT_PIPE = r"\\.\pipe\verge-mihomo"


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


def _decode_chunked(data: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while pos < len(data):
        line_end = data.find(b"\r\n", pos)
        if line_end < 0:
            break
        size_text = data[pos:line_end].split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError:
            return data
        pos = line_end + 2
        if size == 0:
            break
        out += data[pos : pos + size]
        pos += size + 2
    return bytes(out)


def _read_all(handle) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            _, payload = win32file.ReadFile(handle, 65536)
            if payload:
                chunks.append(payload)
        except Exception:
            break
    return b"".join(chunks)


def request(path: str, method: str = "GET", body: bytes | None = None, pipe: str = DEFAULT_PIPE) -> Response:
    handle = win32file.CreateFile(
        pipe,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )
    try:
        payload = body or b""
        headers = [
            f"{method} {path} HTTP/1.1",
            "Host: localhost",
            "Connection: close",
            "Accept: application/json",
        ]
        if payload:
            headers.extend([
                "Content-Type: application/json",
                f"Content-Length: {len(payload)}",
            ])
        raw = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + payload
        win32file.WriteFile(handle, raw)
        response = _read_all(handle)
    finally:
        win32file.CloseHandle(handle)

    head, sep, raw_body = response.partition(b"\r\n\r\n")
    if not sep:
        raise RuntimeError(f"Invalid Mihomo HTTP response: {response[:200]!r}")
    lines = head.decode("iso-8859-1").split("\r\n")
    match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d+)", lines[0])
    if not match:
        raise RuntimeError(f"Invalid Mihomo status line: {lines[0]!r}")
    status = int(match.group(1))
    parsed_headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed_headers[key.strip().lower()] = value.strip()
    if "chunked" in parsed_headers.get("transfer-encoding", "").lower():
        raw_body = _decode_chunked(raw_body)
    return Response(status=status, headers=parsed_headers, body=raw_body)


def version(pipe: str = DEFAULT_PIPE) -> dict[str, Any]:
    response = request("/version", pipe=pipe)
    if response.status != 200:
        raise RuntimeError(f"Mihomo /version HTTP {response.status}")
    return response.json()


def proxies(pipe: str = DEFAULT_PIPE) -> dict[str, Any]:
    response = request("/proxies", pipe=pipe)
    if response.status != 200:
        raise RuntimeError(f"Mihomo /proxies HTTP {response.status}")
    return response.json()


def group_snapshot(pipe: str = DEFAULT_PIPE) -> dict[str, dict[str, Any]]:
    payload = proxies(pipe)
    result: dict[str, dict[str, Any]] = {}
    for name, item in (payload.get("proxies") or {}).items():
        if isinstance(item, dict) and item.get("type") in {"URLTest", "Fallback", "Selector", "LoadBalance"}:
            result[name] = item
    return result


def auto_group(pipe: str = DEFAULT_PIPE, preferred: str | None = None) -> tuple[str, dict[str, Any]]:
    groups = group_snapshot(pipe)
    if preferred and preferred in groups:
        return preferred, groups[preferred]
    for name, item in groups.items():
        if item.get("type") == "URLTest":
            return name, item
    raise RuntimeError("No Mihomo URLTest group found")


def group_delay(
    group_name: str,
    test_url: str,
    timeout_ms: int,
    expected_status: int | None = None,
    pipe: str = DEFAULT_PIPE,
) -> dict[str, Any]:
    params: dict[str, Any] = {"url": test_url, "timeout": timeout_ms}
    if expected_status is not None:
        params["expected"] = expected_status
    path = f"/group/{quote(group_name, safe='')}/delay?{urlencode(params)}"
    response = request(path, pipe=pipe)
    if response.status != 200:
        raise RuntimeError(f"Mihomo group delay HTTP {response.status}: {response.body[:200]!r}")
    return response.json()


def connections(pipe: str = DEFAULT_PIPE) -> dict[str, Any]:
    response = request("/connections", pipe=pipe)
    if response.status != 200:
        raise RuntimeError(f"Mihomo /connections HTTP {response.status}")
    return response.json()


def delete_connection(connection_id: str, pipe: str = DEFAULT_PIPE) -> None:
    response = request(f"/connections/{quote(connection_id, safe='')}", method="DELETE", pipe=pipe)
    if response.status not in {200, 204}:
        raise RuntimeError(f"Mihomo delete connection HTTP {response.status}")
