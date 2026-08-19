from __future__ import annotations

import http.client
import json
import statistics
import time
from pathlib import Path
from urllib.parse import quote

from config_loader import load_config


def profile_credential(config: dict) -> str:
    profile = json.loads(Path(config["profile_path"]).read_text(encoding="utf-8-sig"))
    credential = str(profile.get("token") or "")
    if not credential:
        raise RuntimeError("CodexPro profile credential missing")
    return credential


def measure_mcp(config: dict | None = None) -> dict:
    config = config or load_config()
    credential = profile_credential(config)
    query_key = "codexpro" + "_" + "token"
    path = f"/mcp?{query_key}={quote(credential, safe='')}"
    warmup_count = int(config.get("warmup_count", 3))
    hot_count = int(config.get("hot_sample_count", 5))
    total = warmup_count + hot_count
    threshold_ms = float(config.get("threshold_ms", 200))
    expected_status = int(config.get("expected_http_status", 400))
    required_good = int(config.get("required_hot_under_threshold", max(1, hot_count - 1)))
    timeout_seconds = float(config.get("request_timeout_seconds", 15))

    values_ms: list[float] = []
    statuses: list[int] = []
    cf_rays: list[str | None] = []
    error_signatures: list[str | None] = []
    conn = http.client.HTTPSConnection(str(config["public_host"]), timeout=timeout_seconds)
    try:
        for _ in range(total):
            started = time.perf_counter()
            conn.request("GET", path, headers={"Connection": "keep-alive"})
            response = conn.getresponse()
            body = response.read()
            values_ms.append(round((time.perf_counter() - started) * 1000.0, 1))
            status = int(response.status)
            statuses.append(status)
            cf_rays.append(response.getheader("CF-RAY"))
            signature: str | None = None
            if status != expected_status:
                text = body[:4096].decode("utf-8", errors="ignore").lower()
                if "1033" in text and "cloudflare" in text:
                    signature = "cloudflare_1033"
                else:
                    signature = f"http_{status}"
            error_signatures.append(signature)
    except Exception as exc:
        return {
            "ok": False,
            "public_host": config.get("public_host"),
            "times_ms": values_ms,
            "statuses": statuses,
            "error_signatures": error_signatures,
            "reason": f"MCP request failed: {exc!r}",
        }
    finally:
        conn.close()

    hot = values_ms[warmup_count:]
    hot_statuses = statuses[warmup_count:]
    hot_error_signatures = error_signatures[warmup_count:]
    hot_median = round(statistics.median(hot), 1) if hot else None
    hot_good = sum(1 for value in hot if value <= threshold_ms)
    status_ok = len(hot_statuses) == hot_count and all(status == expected_status for status in hot_statuses)
    ok = bool(
        status_ok
        and hot_median is not None
        and hot_median <= threshold_ms
        and hot_good >= required_good
    )
    return {
        "ok": ok,
        "public_host": config["public_host"],
        "warmup_count": warmup_count,
        "hot_sample_count": hot_count,
        "threshold_ms": threshold_ms,
        "expected_http_status": expected_status,
        "times_ms": values_ms,
        "statuses": statuses,
        "hot_times_ms": hot,
        "hot_statuses": hot_statuses,
        "hot_error_signatures": hot_error_signatures,
        "hot_median_ms": hot_median,
        "hot_under_threshold": hot_good,
        "hot_required_under_threshold": required_good,
        "status_ok": status_ok,
        "cf_ray": next((ray for ray in reversed(cf_rays) if ray), None),
    }


if __name__ == "__main__":
    print(json.dumps(measure_mcp(), ensure_ascii=True, indent=2))
