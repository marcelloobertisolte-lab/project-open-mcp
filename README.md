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

### nginx + TLS (subpath on the existing ]po[ vhost)

The MCP process listens on `127.0.0.1:8181` (8080 is taken by Tomcat/JasperReports
on the ]po[ host). It is exposed as a subpath `/mcp` on the existing
`projectopen.soltea.it` vhost (`/etc/nginx/sites-enabled/default`,
`server_name _`). Add three location blocks inside that `server { }` (back up
first; run `nginx -t` before every reload):

```nginx
# MCP endpoint -> uvicorn. Set PO_MCP_ALLOWED_HOSTS so the forwarded Host passes
# the SDK's DNS-rebinding check (see project-open-mcp.env).
location /mcp {
    proxy_pass http://127.0.0.1:8181;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 3600s;
}

# Static webroot for the ACME http-01 challenge (because `location /` proxies
# everything to ]po[).
location /.well-known/acme-challenge/ { root /var/www/html; }
```

TLS via **acme.sh** (the host's system certbot is too old for ACMEv2 on
Ubuntu 16.04). Run as a real root shell — acme.sh refuses to run under `sudo`:

```bash
sudo -i
curl https://get.acme.sh | sh -s email=you@example.com
~/.acme.sh/acme.sh --set-default-ca --server letsencrypt
~/.acme.sh/acme.sh --issue -d projectopen.soltea.it -w /var/www/html
mkdir -p /etc/nginx/ssl
~/.acme.sh/acme.sh --install-cert -d projectopen.soltea.it \
  --key-file /etc/nginx/ssl/projectopen.key \
  --fullchain-file /etc/nginx/ssl/projectopen.crt \
  --reloadcmd "systemctl reload nginx"
exit
```

Then add `listen 443 ssl;` + `ssl_certificate`/`ssl_certificate_key` to the same
server block and reload. acme.sh installs its own renewal cron.

Firewall: the host uses **iptables with `INPUT` policy DROP** (not ufw). Open 443
with a single rule and persist it (do NOT flush the chain — SSH depends on
existing rules):

```bash
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
iptables-save > /etc/iptables/rules.v4
```

Smoke test from the server (initialize handshake):

```bash
curl -s -u 'oberti@soltea.it:<pass>' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  https://projectopen.soltea.it/mcp
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
      "url": "https://projectopen.soltea.it/mcp",
      "headers": { "Authorization": "Basic <base64 user:pass>" }
    }
  }
}
```

Generate the header value with: `printf 'user:pass' | base64`.

**Health check:** `GET https://projectopen.soltea.it/mcp/healthz` returns
`200 {"status":"ok"}` **without** authentication, for liveness pings/monitoring.
Every other path on `/mcp` requires Basic auth (an unauthenticated request gets
`401`, which still proves the server is reachable).

## Exposed tools

### Read (always available)

| Tool                 | Project-Open object | Notes                                      |
|----------------------|---------------------|--------------------------------------------|
| `list_projects`      | `im_project`        | Filter by name, status, manager            |
| `get_project`        | `im_project`        | Full project record                        |
| `list_tasks`         | `im_timesheet_task` | Optional `project_id` filter               |
| `get_task`           | `im_timesheet_task` | Full task record                           |
| `list_hours`         | `im_hour`           | Filter by user, project, day or date range |
| `list_absences`      | `im_user_absence`   | Filter by employee, type, date range       |
| `monthly_hours_by_user` | aggregation      | Per-employee hours + absence days for a month |
| `list_companies`     | `im_company`        | CRM companies                              |
| `get_company`        | `im_company`        | Full company record                        |
| `list_users`         | `user`              | Users / contacts                           |
| `get_user`           | `user`              | Full user record                           |
| `list_tickets`       | `im_ticket`         | Filter by status, assignee                 |
| `get_ticket`         | `im_ticket`         | Full ticket record                         |

### Write (require `PO_ALLOW_WRITES=true`)

| Tool             | Project-Open object | Operation                              |
|------------------|---------------------|----------------------------------------|
| `log_hours`      | `im_hour`           | Create a timesheet entry               |
| `create_project` | `im_project`        | Create a project                       |
| `update_project` | `im_project`        | Update fields (status, lead, dates…)   |
| `create_task`    | `im_timesheet_task` | Create a task under a project          |
| `update_task`    | `im_timesheet_task` | Update fields (status, %, parent…)     |
| `create_ticket`  | `im_ticket`         | Open a new ticket                      |
| `update_ticket`  | `im_ticket`         | Update fields (status…)                |

The Project-Open REST API does **not** support DELETE, so there are no deletion
tools. To retract a project/task, set its status to **Deleted (82)** via
`update_project` / `update_task`.

Useful category ids (verify per instance): project status 76 Open, 81 Closed,
82 Deleted, 83 Canceled; project type 97 Strategic Consulting, 98 Software
Maintenance, 99 Software Development, 2501 Gantt, 100 Task.

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
