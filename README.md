# Project-Open MCP Server

MCP server that exposes a subset of a [Project-Open (]po[)](https://project-open.com) instance
through the `intranet-rest` package: projects & tasks, timesheet hours, CRM
(companies/users) and tickets.

Reads are always enabled. Writes (logging hours, creating tasks/tickets,
updating ticket status) are **gated by an environment variable** so the server
defaults to safe read-only operation.

## Requirements

- Python 3.10+
- A Project-Open instance with the `intranet-rest` package installed and a user
  account that has REST permissions
- Network reachability to `<PO_BASE_URL>/intranet-rest/...`

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
# then edit .env with your PO_BASE_URL / PO_USERNAME / PO_PASSWORD
```

## Run (stdio)

```powershell
project-open-mcp
```

Or, equivalently:

```powershell
python -m project_open_mcp
```

## Register with Claude Code (stdio)

Add an entry under `mcpServers` in your `claude_desktop_config.json` (or in
project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "project-open": {
      "command": "project-open-mcp",
      "env": {
        "PO_BASE_URL": "https://po.example.com",
        "PO_USERNAME": "apiuser",
        "PO_PASSWORD": "changeme",
        "PO_ALLOW_WRITES": "false"
      }
    }
  }
}
```

## Run as an HTTP service (deploy on the Project-Open server)

The server can run as a long-lived **streamable-HTTP** service for multiple
clients. Authentication is **pass-through**: each client sends HTTP Basic
credentials, the server validates them against Project-Open itself and reuses
them for that request's REST calls. There is **no service account** — every
caller gets their own ]po[ permissions.

Set `PO_MCP_TRANSPORT=http`. The process binds to `PO_MCP_HOST:PO_MCP_PORT`
(default `127.0.0.1:8080`) and mounts the MCP endpoint at `/mcp`.

```powershell
$env:PO_MCP_TRANSPORT="http"; project-open-mcp
```

**Always put TLS in front.** With plain HTTP, Basic credentials travel in
clear text. Recommended topology on the ]po[ host:

```
client --HTTPS--> nginx (TLS, :443) --localhost:8080--> MCP --localhost--> ]po[ REST
```

Deployment artifacts are in `deploy/`:

| File                          | Purpose                                          |
|-------------------------------|--------------------------------------------------|
| `project-open-mcp.env`        | EnvironmentFile (no PO_USERNAME/PASSWORD!)       |
| `project-open-mcp.service`    | systemd unit                                     |
| `nginx-mcp.conf`              | nginx TLS reverse proxy (SSE-friendly)           |

### Install on the ]po[ host (git clone)

The host Python may be too old (e.g. Ubuntu 16.04 ships Python 3.5). `install.sh`
provisions a standalone CPython 3.12 via [uv](https://docs.astral.sh/uv/),
under `/opt/project-open-mcp/.python`, without touching the system Python.

```bash
# 1. Prerequisites (uv brings its own Python; we just need git + curl)
sudo apt update && sudo apt install -y git curl rsync

# 2. Get the code
sudo git clone <REPO_URL> /opt/project-open-mcp
cd /opt/project-open-mcp

# 3. Install service (uv+venv, user, EnvironmentFile, systemd unit, starts it)
sudo deploy/install.sh

# 4. Edit the EnvironmentFile if needed (PO_BASE_URL, PO_ALLOW_WRITES, ...)
sudo nano /etc/project-open-mcp/project-open-mcp.env
sudo systemctl restart project-open-mcp
```

`install.sh` is idempotent: to update, `sudo git -C /opt/project-open-mcp pull`
then `sudo deploy/install.sh` again.

### nginx + TLS

The MCP process listens on `127.0.0.1:8080`. Expose it over HTTPS (Basic auth
must not travel in clear). Using a dedicated subdomain `mcp.soltea.it`:

```bash
# DNS: point mcp.soltea.it -> this server's IP first, then:
sudo cp /opt/project-open-mcp/deploy/nginx-mcp.conf /etc/nginx/sites-available/mcp.conf
sudo ln -s /etc/nginx/sites-available/mcp.conf /etc/nginx/sites-enabled/
sudo certbot --nginx -d mcp.soltea.it     # obtains + wires the TLS cert
sudo nginx -t && sudo systemctl reload nginx
```

Smoke test from the server (initialize handshake):

```bash
curl -s -u 'oberti@soltea.it:<pass>' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  http://127.0.0.1:8080/mcp
```

### Connect openclaw (or any HTTP MCP client)

Point the agent at the HTTPS endpoint and send a ]po[ user's credentials as an
`Authorization: Basic` header (base64 of `user:password`). Those credentials
authenticate the client *and* become the identity used for the REST calls, so
the agent acts with that user's ]po[ permissions.

```json
{
  "mcpServers": {
    "project-open": {
      "type": "http",
      "url": "https://mcp.soltea.it/mcp",
      "headers": { "Authorization": "Basic <base64 user:pass>" }
    }
  }
}
```

Generate the header value with: `printf 'user:pass' | base64`.

## Exposed tools

### Read (always available)

| Tool                 | Project-Open object | Notes                                      |
|----------------------|---------------------|--------------------------------------------|
| `list_projects`      | `im_project`        | Filter by name, status, manager            |
| `get_project`        | `im_project`        | Full project record                        |
| `list_tasks`         | `im_timesheet_task` | Optional `project_id` filter               |
| `get_task`           | `im_timesheet_task` | Full task record                           |
| `list_hours`         | `im_hour`           | Filter by user, project, day               |
| `list_companies`     | `im_company`        | CRM companies                              |
| `get_company`        | `im_company`        | Full company record                        |
| `list_users`         | `user`              | Users / contacts                           |
| `get_user`           | `user`              | Full user record                           |
| `list_tickets`       | `im_ticket`         | Filter by status, assignee                 |
| `get_ticket`         | `im_ticket`         | Full ticket record                         |

### Write (require `PO_ALLOW_WRITES=true`)

| Tool                 | Project-Open object | Operation               |
|----------------------|---------------------|-------------------------|
| `log_hours`          | `im_hour`           | Create a timesheet entry|
| `create_task`        | `im_timesheet_task` | Create a task on project|
| `create_ticket`      | `im_ticket`         | Open a new ticket       |
| `update_ticket`      | `im_ticket`         | Update fields (status…) |

The Project-Open REST API does **not** support DELETE, so this server also
does not expose any deletion tools.

## Logging

All components log via the standard `logging` module to **stderr and a
rotating file** (never stdout — that carries the MCP stream in stdio mode).

- `PO_LOG_LEVEL` — `DEBUG` | `INFO` (default) | `WARNING` | `ERROR`
- `PO_LOG_FILE` — file path (default `logs/project-open-mcp.log`); empty
  disables the file handler

What gets logged: every REST call (method, path, params, and POST body) and
its outcome, auth successes/failures (username only — never passwords), and
each write tool invocation with the acting user. Tail it while diagnosing:

```powershell
Get-Content logs\project-open-mcp.log -Wait -Tail 20
```

## Limitations & notes

- The `intranet-rest` API on this instance returns JSON (`format=json`)
  wrapped in an envelope `{"success", "total", "message", "data"}`. The client
  returns that payload directly. (Older ]po[ docs mention XML; this build
  rejects `format=xml`.)
- `list_*` tools can return large result sets (e.g. thousands of `im_hour`
  rows). They default to `limit=100`; pass `limit` to widen/narrow. Caveat:
  ]po[ reports `total` as the number of rows *returned*, so a limited response
  does not reveal how many more rows match.
- Auth failures are reported as **HTTP 200** with `{"success": false,
  "message": "No authentication found ('')"}`, not as 401. The client treats
  `success: false` as an error; the HTTP pass-through middleware uses this to
  validate credentials.
- In ]po[, tasks are sub-projects: `list_projects` (`im_project`) also returns
  `im_timesheet_task` rows.
- Fields available on each object depend on your ]po[ version and on the
  permissions of the API user.
- Filters are passed through as query parameters; supported filters follow
  the ]po[ DynView / object-search conventions.
- Authentication uses HTTP Basic Auth. Cookie and `auto_login` token methods
  are not implemented.
