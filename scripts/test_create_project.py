"""Validate the project write path against a live ]po[ instance.

Creates a throwaway project, reads it back, then marks it Deleted (status 82)
— ]po[ has no REST DELETE, so this is the closest to a clean-up. Writes the
outcome to scripts/last_project_test.txt AND stdout.

Run:  PO_ALLOW_WRITES=true .venv/Scripts/python.exe scripts/test_create_project.py
"""

import asyncio
import json
import os
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ["PO_ALLOW_WRITES"] = "true"
os.environ.setdefault("PO_LOG_LEVEL", "INFO")

from project_open_mcp import server as s  # noqa: E402
from project_open_mcp.logging_setup import setup_logging  # noqa: E402

setup_logging()

COMPANY_ID = 8720          # internal company observed on existing projects
PROJECT_TYPE_ID = 97       # Strategic Consulting
OUT = Path(__file__).with_name("last_project_test.txt")


def emit(text: str) -> None:
    print(text, flush=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


async def main() -> None:
    OUT.write_text("", encoding="utf-8")
    nr = "ZZTEST_" + str(int(time.time()))
    emit(f"create_project nr={nr}")
    try:
        res = await s.create_project(
            project_name="ZZ TEST MCP (delete me)",
            company_id=COMPANY_ID,
            project_nr=nr,
            project_type_id=PROJECT_TYPE_ID,
            project_status_id=76,
        )
        emit("CREATE = " + json.dumps(res, ensure_ascii=False, indent=2))
        data = res.get("data")
        data = data[0] if isinstance(data, list) else data
        pid = (data or {}).get("project_id") or (data or {}).get("rest_oid")
        if pid:
            back = await s.get_project(int(pid))
            emit("READBACK = " + json.dumps(back, ensure_ascii=False)[:600])
            upd = await s.update_project(int(pid), project_status_id=82)
            emit("MARK_DELETED = " + json.dumps(upd, ensure_ascii=False)[:400])
    except Exception as exc:  # noqa: BLE001
        emit("ERROR = " + repr(exc))
        emit(traceback.format_exc())


if __name__ == "__main__":
    asyncio.run(main())
