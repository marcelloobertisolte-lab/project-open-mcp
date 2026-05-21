"""MCP server exposing Project-Open ]po[ data and operations."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .auth import current_credentials
from .client import ClientConfig, ProjectOpenClient, ProjectOpenError
from .logging_setup import setup_logging


def _load_env() -> None:
    """Load .env reliably regardless of the launcher's working directory.

    Existing process env (e.g. set by an MCP launcher) always wins, so
    ``override`` stays False. We try the cwd first, then the project root
    derived from this file's location (works for editable installs).
    """
    cwd_env = find_dotenv(usecwd=True)
    if cwd_env:
        load_dotenv(cwd_env)
    pkg_env = Path(__file__).resolve().parents[2] / ".env"
    if pkg_env.exists():
        load_dotenv(pkg_env)


_load_env()
setup_logging()
logger = logging.getLogger(__name__)


def _transport_security() -> TransportSecuritySettings | None:
    """DNS-rebinding allowlist for the HTTP transport.

    FastMCP defaults to localhost-only, which rejects requests coming through a
    reverse proxy that forwards the public Host header. Set PO_MCP_ALLOWED_HOSTS
    (comma-separated hostnames) to permit them; localhost is always allowed too.
    Returns None when unset so FastMCP keeps its secure localhost-only default.
    """
    raw = os.environ.get("PO_MCP_ALLOWED_HOSTS", "").strip()
    if not raw:
        return None
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    for name in (h.strip() for h in raw.split(",") if h.strip()):
        hosts += [name, f"{name}:*"]
        origins += [f"https://{name}", f"http://{name}"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


mcp = FastMCP("project-open", transport_security=_transport_security())

# Default cap on rows returned by list_* tools, to avoid dumping huge result
# sets (e.g. thousands of im_hour rows) into the model context. ``total`` in
# the response still reflects the full match count.
DEFAULT_LIST_LIMIT = 100


def _resolve_config() -> ClientConfig:
    """Build a client config, preferring per-request pass-through credentials."""
    creds = current_credentials.get()
    if creds is not None:
        return ClientConfig.from_env(username=creds[0], password=creds[1])
    return ClientConfig.from_env()


@asynccontextmanager
async def _client():
    """Yield a Project-Open client scoped to the current request/credentials."""
    async with ProjectOpenClient(_resolve_config()) as c:
        yield c


def _writes_enabled() -> bool:
    return os.environ.get("PO_ALLOW_WRITES", "false").lower() == "true"


def _acting_user() -> str:
    creds = current_credentials.get()
    if creds is not None:
        return creds[0]
    return os.environ.get("PO_USERNAME", "<env>")


def _require_writes(operation: str, **details: Any) -> None:
    if not _writes_enabled():
        logger.warning(
            "WRITE blocked (PO_ALLOW_WRITES=false): %s by=%s %s",
            operation,
            _acting_user(),
            details,
        )
        raise ProjectOpenError(
            "Write operations are disabled. Set PO_ALLOW_WRITES=true to enable."
        )
    logger.info("WRITE %s by=%s %s", operation, _acting_user(), details)


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------------------
# Projects & tasks
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_projects(
    project_name: str | None = None,
    project_status_id: int | None = None,
    project_lead_id: int | None = None,
    parent_id: int | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> Any:
    """List ]po[ projects (`im_project`).

    Note: in ]po[ tasks are sub-projects, so this also returns
    `im_timesheet_task` rows.

    Args:
        project_name: Optional case-insensitive name filter.
        project_status_id: Filter by ]po[ project status category id.
        project_lead_id: Filter by responsible user id.
        parent_id: Filter by parent project id (for sub-projects).
        limit: Max rows to return. ]po[ reports ``total`` as the number of rows
            returned, so a limited result hides how many more exist.
    """
    async with _client() as c:
        return await c.list_objects(
            "im_project",
            filters=_drop_none(
                {
                    "project_name": project_name,
                    "project_status_id": project_status_id,
                    "project_lead_id": project_lead_id,
                    "parent_id": parent_id,
                }
            ),
            limit=limit,
        )


@mcp.tool()
async def get_project(project_id: int) -> Any:
    """Fetch a single project by id."""
    async with _client() as c:
        return await c.get_object("im_project", project_id)


@mcp.tool()
async def list_tasks(
    project_id: int | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> Any:
    """List timesheet tasks (`im_timesheet_task`), optionally filtered by project."""
    async with _client() as c:
        return await c.list_objects(
            "im_timesheet_task",
            filters=_drop_none({"project_id": project_id}),
            limit=limit,
        )


@mcp.tool()
async def get_task(task_id: int) -> Any:
    """Fetch a single timesheet task by id."""
    async with _client() as c:
        return await c.get_object("im_timesheet_task", task_id)


# ---------------------------------------------------------------------------
# Timesheet hours
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_hours(
    user_id: int | None = None,
    project_id: int | None = None,
    day: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> Any:
    """List timesheet entries (`im_hour`).

    This object type can be very large; always filter. With ``limit`` set, ]po[
    reports ``total`` as the number of rows returned, not the full match count.

    Args:
        user_id: Filter by employee id.
        project_id: Filter by project id.
        day: Filter by day in ISO format (YYYY-MM-DD).
        limit: Max rows to return.
    """
    async with _client() as c:
        return await c.list_objects(
            "im_hour",
            filters=_drop_none(
                {"user_id": user_id, "project_id": project_id, "day": day}
            ),
            limit=limit,
        )


# ---------------------------------------------------------------------------
# CRM: companies & users (contacts)
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_companies(
    company_name: str | None = None,
    company_status_id: int | None = None,
    company_type_id: int | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> Any:
    """List CRM companies (`im_company`)."""
    async with _client() as c:
        return await c.list_objects(
            "im_company",
            filters=_drop_none(
                {
                    "company_name": company_name,
                    "company_status_id": company_status_id,
                    "company_type_id": company_type_id,
                }
            ),
            limit=limit,
        )


@mcp.tool()
async def get_company(company_id: int) -> Any:
    """Fetch a single company by id."""
    async with _client() as c:
        return await c.get_object("im_company", company_id)


@mcp.tool()
async def list_users(
    email: str | None = None,
    username: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> Any:
    """List users / contacts (`user`)."""
    async with _client() as c:
        return await c.list_objects(
            "user",
            filters=_drop_none({"email": email, "username": username}),
            limit=limit,
        )


@mcp.tool()
async def get_user(user_id: int) -> Any:
    """Fetch a single user by id."""
    async with _client() as c:
        return await c.get_object("user", user_id)


# ---------------------------------------------------------------------------
# Tickets / helpdesk
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_tickets(
    ticket_status_id: int | None = None,
    ticket_assignee_id: int | None = None,
    ticket_customer_id: int | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> Any:
    """List helpdesk tickets (`im_ticket`)."""
    async with _client() as c:
        return await c.list_objects(
            "im_ticket",
            filters=_drop_none(
                {
                    "ticket_status_id": ticket_status_id,
                    "ticket_assignee_id": ticket_assignee_id,
                    "ticket_customer_id": ticket_customer_id,
                }
            ),
            limit=limit,
        )


@mcp.tool()
async def get_ticket(ticket_id: int) -> Any:
    """Fetch a single ticket by id."""
    async with _client() as c:
        return await c.get_object("im_ticket", ticket_id)


# ---------------------------------------------------------------------------
# Write tools (gated by PO_ALLOW_WRITES)
# ---------------------------------------------------------------------------

@mcp.tool()
async def log_hours(
    user_id: int,
    project_id: int,
    day: str,
    hours: float,
    note: str | None = None,
) -> Any:
    """Create a timesheet entry (`im_hour`).

    Requires ``PO_ALLOW_WRITES=true``.

    Args:
        user_id: Employee logging the hours.
        project_id: Project the hours are charged to.
        day: ISO date (YYYY-MM-DD).
        hours: Decimal hours worked.
        note: Free-text description.
    """
    _require_writes(
        "log_hours", user_id=user_id, project_id=project_id, day=day, hours=hours
    )
    async with _client() as c:
        return await c.create_object(
            "im_hour",
            _drop_none(
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "day": day,
                    "hours": hours,
                    "note": note,
                }
            ),
        )


@mcp.tool()
async def create_project(
    project_name: str,
    company_id: int,
    project_nr: str | None = None,
    parent_id: int | None = None,
    project_type_id: int = 97,
    project_status_id: int = 76,
    project_lead_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    description: str | None = None,
) -> Any:
    """Create a project (`im_project`).

    Requires ``PO_ALLOW_WRITES=true``. Note: ]po[ has no DELETE — to retract a
    project set its status to Deleted (82) via update_project.

    Args:
        project_name: Display name.
        company_id: Customer/company id (e.g. the internal company).
        project_nr: Unique project number. Some instances require it; if your
            ]po[ auto-generates numbers you may omit it.
        parent_id: Parent project id for a sub-project; omit for top-level.
        project_type_id: Category "Intranet Project Type". Common: 97 Strategic
            Consulting, 98 Software Maintenance, 99 Software Development,
            2501 Gantt Project.
        project_status_id: Category "Intranet Project Status". Common: 76 Open,
            81 Closed, 82 Deleted, 83 Canceled.
        project_lead_id: Responsible user id.
        start_date: ISO date (YYYY-MM-DD).
        end_date: ISO date (YYYY-MM-DD).
        description: Free text.
    """
    _require_writes("create_project", project_name=project_name, company_id=company_id)
    async with _client() as c:
        return await c.create_object(
            "im_project",
            _drop_none(
                {
                    "project_name": project_name,
                    "company_id": company_id,
                    "project_nr": project_nr,
                    "parent_id": parent_id,
                    "project_type_id": project_type_id,
                    "project_status_id": project_status_id,
                    "project_lead_id": project_lead_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "description": description,
                }
            ),
        )


@mcp.tool()
async def update_project(
    project_id: int,
    project_name: str | None = None,
    project_status_id: int | None = None,
    project_type_id: int | None = None,
    project_lead_id: int | None = None,
    parent_id: int | None = None,
    percent_completed: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    description: str | None = None,
    note: str | None = None,
) -> Any:
    """Update a project (`im_project`). Only provided fields are sent.

    Requires ``PO_ALLOW_WRITES=true``. To "delete" a project set
    ``project_status_id=82`` (Deleted) — ]po[ has no REST DELETE.
    """
    _require_writes("update_project", project_id=project_id)
    attrs = _drop_none(
        {
            "project_name": project_name,
            "project_status_id": project_status_id,
            "project_type_id": project_type_id,
            "project_lead_id": project_lead_id,
            "parent_id": parent_id,
            "percent_completed": percent_completed,
            "start_date": start_date,
            "end_date": end_date,
            "description": description,
            "note": note,
        }
    )
    if not attrs:
        raise ProjectOpenError("update_project called with no fields to update.")
    async with _client() as c:
        return await c.update_object("im_project", project_id, attrs)


@mcp.tool()
async def create_task(
    parent_id: int,
    task_name: str,
    project_nr: str | None = None,
    company_id: int | None = None,
    planned_units: float | None = None,
    uom_id: int | None = None,
    percent_completed: int | None = None,
    project_status_id: int = 76,
    project_type_id: int = 100,
    description: str | None = None,
) -> Any:
    """Create a timesheet task (`im_timesheet_task`) under a project.

    A task is an `im_project` subtype, so its name is sent as ``project_name``
    and its parent project as ``parent_id``. Requires ``PO_ALLOW_WRITES=true``.

    Args:
        parent_id: Project (or task) this task hangs under.
        task_name: Task name (stored as project_name).
        project_nr: Unique task number; may be required by your instance.
        company_id: Customer id; usually the parent project's company.
        planned_units: Planned effort (in the task's unit of measure).
        uom_id: Unit of measure id (e.g. hours).
        percent_completed: 0-100.
        project_status_id: 76 Open / 81 Closed / 82 Deleted.
        project_type_id: 100 = Task.
        description: Free text.
    """
    _require_writes("create_task", parent_id=parent_id, task_name=task_name)
    async with _client() as c:
        return await c.create_object(
            "im_timesheet_task",
            _drop_none(
                {
                    "parent_id": parent_id,
                    "project_name": task_name,
                    "project_nr": project_nr,
                    "company_id": company_id,
                    "planned_units": planned_units,
                    "uom_id": uom_id,
                    "percent_completed": percent_completed,
                    "project_status_id": project_status_id,
                    "project_type_id": project_type_id,
                    "description": description,
                }
            ),
        )


@mcp.tool()
async def update_task(
    task_id: int,
    task_name: str | None = None,
    project_status_id: int | None = None,
    parent_id: int | None = None,
    planned_units: float | None = None,
    percent_completed: int | None = None,
    deadline_date: str | None = None,
    description: str | None = None,
    note: str | None = None,
) -> Any:
    """Update a timesheet task (`im_timesheet_task`). Only provided fields sent.

    Requires ``PO_ALLOW_WRITES=true``. ``task_name`` maps to ``project_name``;
    set ``parent_id`` to move the task under a different project.
    """
    _require_writes("update_task", task_id=task_id)
    attrs = _drop_none(
        {
            "project_name": task_name,
            "project_status_id": project_status_id,
            "parent_id": parent_id,
            "planned_units": planned_units,
            "percent_completed": percent_completed,
            "deadline_date": deadline_date,
            "description": description,
            "note": note,
        }
    )
    if not attrs:
        raise ProjectOpenError("update_task called with no fields to update.")
    async with _client() as c:
        return await c.update_object("im_timesheet_task", task_id, attrs)


@mcp.tool()
async def create_ticket(
    ticket_name: str,
    ticket_customer_id: int,
    ticket_description: str | None = None,
    ticket_type_id: int | None = None,
    ticket_status_id: int | None = None,
    ticket_assignee_id: int | None = None,
) -> Any:
    """Open a new helpdesk ticket (`im_ticket`).

    Requires ``PO_ALLOW_WRITES=true``.
    """
    _require_writes(
        "create_ticket", ticket_name=ticket_name, customer_id=ticket_customer_id
    )
    async with _client() as c:
        return await c.create_object(
            "im_ticket",
            _drop_none(
                {
                    "ticket_name": ticket_name,
                    "ticket_customer_id": ticket_customer_id,
                    "ticket_description": ticket_description,
                    "ticket_type_id": ticket_type_id,
                    "ticket_status_id": ticket_status_id,
                    "ticket_assignee_id": ticket_assignee_id,
                }
            ),
        )


@mcp.tool()
async def update_ticket(
    ticket_id: int,
    ticket_status_id: int | None = None,
    ticket_assignee_id: int | None = None,
    ticket_description: str | None = None,
) -> Any:
    """Update fields on an existing ticket.

    Requires ``PO_ALLOW_WRITES=true``. Only the provided fields are sent.
    """
    _require_writes("update_ticket", ticket_id=ticket_id)
    attrs = _drop_none(
        {
            "ticket_status_id": ticket_status_id,
            "ticket_assignee_id": ticket_assignee_id,
            "ticket_description": ticket_description,
        }
    )
    if not attrs:
        raise ProjectOpenError("update_ticket called with no fields to update.")
    async with _client() as c:
        return await c.update_object("im_ticket", ticket_id, attrs)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

def build_http_app():
    """Return the streamable-HTTP ASGI app wrapped with pass-through auth."""
    from .auth import PassThroughAuthMiddleware

    return PassThroughAuthMiddleware(mcp.streamable_http_app())


def run() -> None:
    """Entry point. Transport selected via PO_MCP_TRANSPORT (stdio|http)."""
    transport = os.environ.get("PO_MCP_TRANSPORT", "stdio").lower()
    if transport == "http":
        import uvicorn

        host = os.environ.get("PO_MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("PO_MCP_PORT", "8080"))
        uvicorn.run(build_http_app(), host=host, port=port)
    else:
        mcp.run()


if __name__ == "__main__":
    run()
