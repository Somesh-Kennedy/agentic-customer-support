"""Runs the crew for one ticket and records the REASON / ACT / OUTCOME trace."""

import logging
import os
import sys
from pathlib import Path

from django.utils import timezone

from support.models import Ticket, TicketEvent

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_SUMMARY = 4000


def mcp_server_params():
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        cwd=str(PROJECT_ROOT),
    )


def _event(ticket, phase, actor, summary, payload=None):
    TicketEvent.objects.create(
        ticket=ticket, phase=phase, actor=actor[:100], summary=summary[:MAX_SUMMARY], payload=payload or {}
    )


def _task_logger(ticket):
    def on_task_done(output):
        _event(
            ticket,
            TicketEvent.Phase.REASON,
            str(getattr(output, "agent", "") or "agent"),
            str(getattr(output, "raw", output)),
            {"task": str(getattr(output, "description", ""))[:500]},
        )

    return on_task_done


def run_crew(ticket, tools):
    """Seam for tests: build and run the crew against the given MCP tools."""
    from support_crew.crew import build_crew

    return build_crew(ticket, tools, task_callback=_task_logger(ticket)).kickoff()


def process_ticket(ticket_id: int) -> Ticket:
    """Process a ticket that the caller has already moved to PROCESSING."""
    from crewai_tools import MCPServerAdapter

    ticket = Ticket.objects.get(pk=ticket_id)
    _event(ticket, TicketEvent.Phase.SYSTEM, "worker", "Crew started")
    try:
        with MCPServerAdapter(mcp_server_params(), connect_timeout=60) as tools:
            run_crew(ticket, tools)
    except Exception as exc:  # noqa: BLE001 - any crash must still close the ticket
        logger.exception("Crew failed on ticket %s", ticket_id)
        ticket.refresh_from_db()
        if ticket.is_open:
            Ticket.objects.filter(pk=ticket.pk).update(
                status=Ticket.Status.FAILED,
                escalation_reason=f"Automated handling crashed: {exc}"[:2000],
                finished_at=timezone.now(),
            )
            _event(ticket, TicketEvent.Phase.OUTCOME, "worker", f"Failed: {exc}")
        ticket.refresh_from_db()
        return ticket

    ticket.refresh_from_db()
    if ticket.is_open:
        # The crew must close the ticket itself; if it didn't, a human takes over.
        Ticket.objects.filter(pk=ticket.pk).update(
            status=Ticket.Status.ESCALATED,
            escalation_reason="Agents finished without resolving or escalating the ticket.",
            finished_at=timezone.now(),
        )
        _event(ticket, TicketEvent.Phase.OUTCOME, "worker", "Escalated: crew reached no decision")
        ticket.refresh_from_db()
    return ticket
