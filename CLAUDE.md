# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server that exposes a subset of a **Project-Open (]po[)** instance — projects & tasks, timesheet hours, CRM (companies/users), tickets — through ]po['s `intranet-rest` package. It speaks two transports: **stdio** (default) and **streamable-HTTP** (for remote agents behind a reverse proxy).

## Commands

```bash
# Install (dev, Windows): use the `py` launcher — `python` is not on PATH
py -m venv .venv && .venv/Scripts/python.exe -m pip install -e .

# Run (transport chosen by PO_MCP_TRANSPORT: stdio | http)
.venv/Scripts/python.exe -m project_open_mcp

# List the registered MCP tools without a live ]po[ instance
.venv/Scripts/python.exe -c "import asyncio; from project_open_mcp.server import mcp; print([t.name for t in asyncio.run(mcp.list_tools())])"
```

There is **no automated test suite**. Verify against a real ]po[ by driving the client directly, e.g.:

```bash
.venv/Scripts/python.exe -c "import asyncio; from dotenv import load_dotenv; load_dotenv(); from project_open_mcp.client import ProjectOpenClient; print(asyncio.run(ProjectOpenClient().list_objects('im_company', limit=2)))"
```

For the HTTP transport, smoke-test with an MCP `initialize` POST (see README "Smoke test"). Config comes from `.env` (see `.env.example`); `server.py` calls `load_dotenv` at import.

## Architecture

Three layers, each in `src/project_open_mcp/`:

- **`client.py`** — async `httpx` wrapper over `/intranet-rest/<type>[/<id>]`. `ClientConfig.from_env(username=, password=)` lets credentials be overridden per call. Returns the raw ]po[ JSON envelope unchanged.
- **`auth.py`** — pure ASGI middleware (`PassThroughAuthMiddleware`) for the HTTP transport. It parses Basic auth, **validates it against ]po[ itself** (cached 60s), stashes the credentials in a `contextvars.ContextVar`, and the tools build a per-request client from it. Pure ASGI (not Starlette `BaseHTTPMiddleware`) on purpose, so the contextvar reliably reaches the tool coroutines.
- **`server.py`** — `FastMCP` tools + transport selection. `_resolve_config()` prefers the per-request contextvar credentials and falls back to env (stdio). Every tool runs inside `async with _client()`. `run()` picks stdio vs uvicorn(streamable-http) from `PO_MCP_TRANSPORT`.

**Credential model (important):** stdio acts as the single `PO_USERNAME`/`PO_PASSWORD` account. HTTP is **pass-through**: each request's Basic credentials are reused for that request's REST calls, giving per-user ]po[ permissions — there is **no service account** in HTTP mode, so `PO_USERNAME`/`PO_PASSWORD` are not set on the server.

**Writes** (`log_hours`, `create_project`, `update_project`, `create_task`, `update_task`, `create_ticket`, `update_ticket`) are gated by `PO_ALLOW_WRITES=true`; read tools are always on. Projects and tasks share the `im_project` schema — a task is an `im_timesheet_task` with `parent_id` set, its name sent as `project_name`. There is no DELETE: "remove" by setting status to Deleted (82).

## ]po[ REST behaviours that are not obvious (and shape the code)

- Responses are a JSON envelope `{"success", "total", "message", "data"}`. The wiki mentions XML, but this build **rejects `format=xml`** — always `format=json`.
- **Auth failure returns HTTP 200** with `{"success": false, "message": "No authentication found..."}`, not 401. `client._request` treats `success: false` as an error; `client.validate()` and the auth middleware rely on this.
- `limit=N` is the **only** working row cap, and it makes `total` equal the rows *returned*, not the full match count — a limited response cannot reveal how many more rows exist. List tools default to `limit=100`.
- The API has **no DELETE** — there are no deletion tools, and timesheet/ticket writes are effectively irreversible via REST.
- In ]po[, **tasks are sub-projects**: `list_projects` (`im_project`) also returns `im_timesheet_task` rows. Hours are logged against the task via `im_hour.project_id`.
- Query filters are interpreted as **raw SQL where-clause fragments** (e.g. a date filter must be quoted server-side). Treat filter values as injected SQL.

## HTTP transport gotcha

FastMCP's streamable-HTTP enables DNS-rebinding protection with a localhost-only Host allowlist, so a reverse proxy forwarding the public Host gets **HTTP 421 "Invalid Host header"**. Set `PO_MCP_ALLOWED_HOSTS=<public-host>` (comma-separated; localhost stays allowed) to permit it while keeping honest `Host` forwarding.

## Logging

All components log via stdlib `logging` to stderr + a rotating file (`PO_LOG_FILE`, `PO_LOG_LEVEL`). **Never log to stdout** — under stdio it carries the MCP JSON-RPC stream. `setup_logging` tolerates an unwritable log path (falls back to stderr) rather than crashing.

## Deployment (the live target)

The production host (`projectopen.soltea.it`) is **Ubuntu 16.04 / Python 3.5** — too old, so `deploy/install.sh` provisions CPython 3.12 via **uv** under the install dir; do not use the system Python. Other host-specific facts baked into `deploy/`: port **8181** (8080 is Tomcat/JasperReports), the log dir is created explicitly (systemd 229 ignores `LogsDirectory=`), and `deploy/install.sh` must stay mode `100755`. Exposed as a subpath `projectopen.soltea.it/mcp` with TLS via **acme.sh** (system certbot is too old for ACMEv2) and a manual **iptables** 443 rule (host policy is `INPUT DROP` — never flush the chain or you lose SSH). Full procedure is in `README.md`.
