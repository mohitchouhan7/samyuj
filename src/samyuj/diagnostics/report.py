from __future__ import annotations

import datetime
import json
import platform
from pathlib import Path
from typing import Any

from samyuj.core.logging import redact_secrets

def generate_diagnostic_report(runtime: Any, output_path: Path | str | None = None) -> dict[str, Any]:
    diag = getattr(runtime, "media_diagnostics", None)
    verdict = diag.verdict() if diag is not None and hasattr(diag, "verdict") else {}
    report = {
        "generated_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "device_name": getattr(runtime, "device_name", "Linux PC"),
        "port": getattr(runtime, "port", 0),
        "wifi_ip": getattr(runtime, "wifi_ip", ""),
        "active_connections": len(getattr(runtime, "conns", {}) or {}),
        "media_verdict": verdict,
        "recent_events": [
            redact_secrets(line)
            for line in list(getattr(getattr(runtime, "events", None), "recent_lines", []) or [])[-100:]
        ],
    }
    if output_path is not None:
        out_p = Path(output_path).expanduser().resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
