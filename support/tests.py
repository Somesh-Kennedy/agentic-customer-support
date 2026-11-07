import asyncio
import json
import os
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

import httpx
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from mcp_server import server as mcp
from support.management.commands.run_support_worker import claim_next_ticket
from support.metrics import support_metrics
from support.models import Customer, Order, Refund, Ticket, TicketEvent
from support_crew import runner


def _test_database_url():
    s = connection.settings_dict
    return f"postgres://{s['USER']}:{s['PASSWORD']}@{s['HOST'] or 'localhost'}:{s['PORT'] or 5432}/{s['NAME']}"


class McpDatabaseTestCase(TransactionTestCase):
    """The MCP server opens its own connections, so data must be committed (no TestCase)."""

    def setUp(self):
        call_command("seed_demo", stdout=open(os.devnull, "w"))
        env = mock.patch.dict(os.environ, {"DATABASE_URL": _test_database_url()})
        env.start()
        self.addCleanup(env.stop)

    def ticket(self, email="ava@example.com", **kw):
        return Ticket.objects.create(
            email=email, subject=kw.pop("subject", "help"), body=kw.pop("body", "help"),
            status=Ticket.Status.PROCESSING, **kw,
        )


class McpToolTests(McpDatabaseTestCase):
    def test_get_order_only_returns_the_ticket_owners_orders(self):
        t = self.ticket()
        self.assertTrue(mcp.get_order(t.pk, "ord-1001")["found"])
        self.assertFalse(mcp.get_order(t.pk, "ORD-2001")["found"])  # Liam's order

    def test_refund_within_limit_is_recorded(self):
        t = self.ticket()
        result = mcp.issue_refund(t.pk, "ORD-1001", 89.99, "damaged on arrival")
        self.assertTrue(result["ok"])
        self.assertEqual(Refund.objects.get().amount, Decimal("89.99"))
        event = t.events.get(actor="issue_refund")
        self.assertEqual(event.phase, "act")
        self.assertEqual(event.payload["args"]["amount"], 89.99)

    def test_refund_over_auto_limit_requires_human(self):
        t = self.ticket(email="sofia@example.com")
        result = mcp.issue_refund(t.pk, "ORD-3001", 649.00, "dead pixels")
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_human_approval"])
        self.assertFalse(Refund.objects.exists())

    def test_refund_cannot_exceed_refundable_balance(self):
        t = self.ticket()
        self.assertTrue(mcp.issue_refund(t.pk, "ORD-1001", 50, "partial")["ok"])
        self.assertFalse(mcp.issue_refund(t.pk, "ORD-1001", 50, "again")["ok"])
        self.assertEqual(mcp.get_order(t.pk, "ORD-1001")["refundable_remaining"], "39.99")

    def test_cancel_only_before_shipping(self):
        t = self.ticket()
        self.assertTrue(mcp.cancel_order(t.pk, "ORD-1002", "ordered by mistake")["ok"])
        self.assertEqual(Order.objects.get(order_number="ORD-1002").status, "cancelled")
        self.assertFalse(mcp.cancel_order(t.pk, "ORD-1001", "changed mind")["ok"])

    def test_update_address_only_before_shipping(self):
        t = self.ticket(email="noah@example.com")
        new = "47 Oak Blvd, Apt 3, Denver, CO 80203"
        self.assertTrue(mcp.update_shipping_address(t.pk, "ORD-4001", new)["ok"])
        self.assertEqual(Order.objects.get(order_number="ORD-4001").shipping_address, new)
        t2 = self.ticket(email="liam@example.com")
        self.assertFalse(mcp.update_shipping_address(t2.pk, "ORD-2001", new)["ok"])

    def test_knowledge_base_search(self):
        t = self.ticket()
        slugs = [a["slug"] for a in mcp.search_knowledge_base(t.pk, "refund for a broken item")["articles"]]
        self.assertIn("refund-policy", slugs)

    def test_resolve_closes_ticket_once(self):
        t = self.ticket()
        self.assertTrue(mcp.resolve_ticket(t.pk, "refund", "Refunded, sorry!")["ok"])
        t.refresh_from_db()
        self.assertEqual(t.status, "resolved")
        self.assertEqual(t.resolution, "Refunded, sorry!")
        self.assertIsNotNone(t.finished_at)
        self.assertEqual(t.events.get(actor="resolve_ticket").phase, "outcome")
        # A second decision can't overwrite the first.
        self.assertFalse(mcp.escalate_ticket(t.pk, "refund", "second thoughts")["ok"])
        t.refresh_from_db()
        self.assertEqual(t.status, "resolved")

    def test_escalate_sets_reason_and_priority(self):
        t = self.ticket()
        mcp.escalate_ticket(t.pk, "complaint", "legal threat", priority="urgent", customer_reply="We'll call you.")
        t.refresh_from_db()
        self.assertEqual((t.status, t.priority, t.escalation_reason), ("escalated", "urgent", "legal threat"))
        mcp.escalate_ticket(self.ticket().pk, "other", "x", priority="bogus")  # invalid priority is coerced
        self.assertEqual(Ticket.objects.order_by("-id").first().priority, "normal")


class McpProtocolTests(McpDatabaseTestCase):
    def test_server_speaks_mcp_over_stdio(self):
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        t = self.ticket()

        async def run():
            async with stdio_client(runner.mcp_server_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {tool.name for tool in (await session.list_tools()).tools}
                    result = await session.call_tool(
                        "lookup_customer", {"ticket_id": t.pk, "email": "ava@example.com"}
                    )
                    return tools, json.loads(result.content[0].text)

        tools, customer = asyncio.run(run())
        self.assertEqual(
            tools,
            {"lookup_customer", "list_customer_orders", "get_order", "search_knowledge_base",
             "get_ticket_history", "issue_refund", "cancel_order", "update_shipping_address",
             "resolve_ticket", "escalate_ticket"},
        )
        self.assertEqual(customer["customer"]["tier"], "premium")
        self.assertTrue(t.events.filter(actor="lookup_customer").exists())


@contextmanager
def fake_mcp_adapter():
    adapter = mock.MagicMock()
    adapter.return_value.__enter__.return_value = []
    with mock.patch("crewai_tools.MCPServerAdapter", adapter):
        yield


class RunnerTests(McpDatabaseTestCase):
    def test_crew_decision_is_kept(self):
        t = self.ticket()

        def crew(ticket, tools):
            mcp.resolve_ticket(ticket.pk, "refund", "Done!")

        with fake_mcp_adapter(), mock.patch.object(runner, "run_crew", side_effect=crew):
            result = runner.process_ticket(t.pk)
        self.assertEqual(result.status, "resolved")

    def test_no_decision_escalates(self):
        t = self.ticket()
        with fake_mcp_adapter(), mock.patch.object(runner, "run_crew"):
            result = runner.process_ticket(t.pk)
        self.assertEqual(result.status, "escalated")
        self.assertIn("without resolving", result.escalation_reason)

    def test_crash_marks_failed(self):
        t = self.ticket()
        with fake_mcp_adapter(), mock.patch.object(runner, "run_crew", side_effect=RuntimeError("boom")):
            result = runner.process_ticket(t.pk)
        self.assertEqual(result.status, "failed")
        self.assertIn("boom", result.escalation_reason)

    def test_task_callback_records_reasoning(self):
        t = self.ticket()
        runner._task_logger(t)(mock.Mock(agent="Support Triage Specialist", raw="category: refund"))
        event = t.events.get(phase="reason")
        self.assertEqual((event.actor, event.summary), ("Support Triage Specialist", "category: refund"))


class CrewBuildTests(TestCase):
    def test_agents_get_their_tools(self):
        from crewai.tools import BaseTool

        from support_crew.crew import build_crew

        names = ["lookup_customer", "list_customer_orders", "get_ticket_history", "search_knowledge_base",
                 "get_order", "issue_refund", "cancel_order", "update_shipping_address",
                 "resolve_ticket", "escalate_ticket"]

        def make_tool(n):
            return type(n, (BaseTool,), {"__annotations__": {"name": str, "description": str},
                                         "name": n, "description": n, "_run": lambda self, **kw: ""})()

        ticket = Ticket(pk=7, email="a@example.com", subject="s", body="b")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
            crew = build_crew(ticket, [make_tool(n) for n in names])
        self.assertEqual(len(crew.agents), 3)
        qa_tools = {t.name for t in crew.agents[2].tools}
        self.assertEqual(qa_tools, {"get_order", "resolve_ticket", "escalate_ticket"})
        # One-tool agents make CrewAI force tool_choice, which current Claude models reject.
        self.assertTrue(all(len(a.tools) >= 2 for a in crew.agents))
        self.assertIn("ticket_id=7", crew.tasks[0].description)


class InterceptorTests(TestCase):
    def test_adds_effort_and_fallbacks(self):
        from support_crew.llm import FALLBACK_BETA, MessagesRequestOptions

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages", json={"model": "m"})
        out = MessagesRequestOptions(effort="high", fallbacks=True).on_outbound(req)
        body = json.loads(out.read())
        self.assertEqual(body["output_config"], {"effort": "high"})
        self.assertEqual(body["fallbacks"], "default")
        self.assertIn(FALLBACK_BETA, out.headers["anthropic-beta"])
        self.assertEqual(int(out.headers["content-length"]), len(out.read()))

    def test_ignores_other_requests(self):
        from support_crew.llm import MessagesRequestOptions

        req = httpx.Request("GET", "https://api.anthropic.com/v1/models")
        self.assertIs(MessagesRequestOptions("high", True).on_outbound(req), req)


class WebTests(TestCase):
    def setUp(self):
        call_command("seed_demo", stdout=open(os.devnull, "w"))

    def test_submit_creates_queued_ticket_linked_to_customer(self):
        resp = self.client.post(reverse("submit_ticket"), {
            "email": "AVA@example.com", "order_number": " ord-1001 ", "subject": "Broken", "body": "It broke",
        })
        ticket = Ticket.objects.get()
        self.assertRedirects(resp, reverse("ticket_detail", args=[ticket.pk]))
        self.assertEqual(ticket.status, "queued")
        self.assertEqual(ticket.customer, Customer.objects.get(email="ava@example.com"))
        self.assertEqual(ticket.order_number, "ORD-1001")

    def test_detail_shows_trace_lanes(self):
        t = Ticket.objects.create(email="x@example.com", subject="Hello", body="b")
        TicketEvent.objects.create(ticket=t, phase="act", actor="get_order", summary="Order is shipped",
                                   payload={"args": {"order_number": "ORD-1"}, "result": {"ok": False}})
        resp = self.client.get(reverse("ticket_detail", args=[t.pk]))
        self.assertContains(resp, "get_order")
        self.assertContains(resp, "refused")
        self.assertEqual(self.client.get(reverse("ticket_status", args=[t.pk])).json()["events"], 1)

    @override_settings(SUPPORT_BASELINE_ESCALATION_RATE="0.5")
    def test_metrics(self):
        for status in ["resolved", "resolved", "resolved", "escalated", "queued"]:
            Ticket.objects.create(email="x@example.com", subject="s", body="b", status=status, category="refund")
        m = support_metrics()
        self.assertEqual((m["closed"], m["open"]), (4, 1))
        self.assertAlmostEqual(m["resolution_rate"], 0.75)
        self.assertAlmostEqual(m["escalation_rate"], 0.25)
        self.assertAlmostEqual(m["escalation_delta"], -0.5)
        self.assertContains(self.client.get(reverse("dashboard")), "75%")

    def test_worker_claims_oldest_queued_ticket(self):
        first = Ticket.objects.create(email="x@example.com", subject="1", body="b")
        Ticket.objects.create(email="x@example.com", subject="2", body="b")
        claimed = claim_next_ticket()
        self.assertEqual(claimed.pk, first.pk)
        self.assertEqual(claimed.status, "processing")
        self.assertIsNotNone(claimed.started_at)
