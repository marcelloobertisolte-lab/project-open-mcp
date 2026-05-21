"""One-off diagnostic: log 1 hour for Marcello on task 67950 today.

Writes the outcome to scripts/last_log_result.txt AND stdout, so the result is
recoverable even if the console shows nothing.

Run:  .venv/Scripts/python.exe scripts/log_hour.py
"""

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ["PO_ALLOW_WRITES"] = "true"
os.environ.setdefault("PO_LOG_LEVEL", "DEBUG")

from project_open_mcp.client import ProjectOpenClient  # noqa: E402
from project_open_mcp.logging_setup import setup_logging  # noqa: E402

setup_logging()

USER_ID = 8892        # Marcello Oberti
TASK_ID = 67950       # Manutenzione infrastruttura / Attività Interne 2026
DAY = "2026-05-21"
HOURS = 1

OUT = Path(__file__).with_name("last_log_result.txt")


def emit(text: str) -> None:
    print(text, flush=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


async def main() -> None:
    OUT.write_text("", encoding="utf-8")
    attrs = {
        "user_id": USER_ID,
        "project_id": TASK_ID,
        "day": DAY,
        "hours": HOURS,
        "note": "Manutenzione infrastruttura",
    }
    emit(f"POST im_hour attrs = {json.dumps(attrs, ensure_ascii=False)}")
    try:
        async with ProjectOpenClient() as c:
            res = await c.create_object("im_hour", attrs)
        emit("RESULT = " + json.dumps(res, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        emit("ERROR = " + repr(exc))
        emit(traceback.format_exc())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:  # noqa: BLE001
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(traceback.format_exc())
        raise
    sys.exit(0)
