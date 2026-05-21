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

Quick Linux setup:

```bash
sudo mkdir -p /opt/project-open-mcp && cd /opt/project-open-mcp
# copy the repo here, then:
python3 -m venv .venv && .venv/bin/pip install -e .
sudo mkdir -p /etc/project-open-mcp
sudo cp deploy/project-open-mcp.env /etc/project-open-mcp/
sudo cp deploy/project-open-mcp.service /etc/systemd/system/
sudo cp deploy/nginx-mcp.conf /etc/nginx/conf.d/
sudo systemctl daemon-reload && sudo systemctl enable --now project-open-mcp
sudo nginx -t && sudo systemctl reload nginx
```

Clients then connect to `https://<host>/mcp` with an `Authorization: Basic`
header carrying a ]po[ username/password. Example `.mcp.json`:

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
