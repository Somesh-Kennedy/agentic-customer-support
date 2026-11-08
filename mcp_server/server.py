"""MCP server exposing the support back office to the CrewAI agents.

Every tool takes the ``ticket_id`` it is working on so each call is written to
``ticket_events`` as an ACT step; ``resolve_ticket`` / ``escalate_ticket`` write
the OUTCOME. Business rules (refund limits, order ownership, what can still be
changed) are enforced here, not left to the model.

Run standalone:  python -m mcp_server.server
"""

import json
import os
from decimal import Decimal

from mcp.server.fastmcp import FastMCP

from mcp_server.db import connect, log_event

mcp = FastMCP("support-backoffice")

PRIORITIES = ("low", "normal", "high", "urgent")


def refund_auto_limit() -> Decimal:
    return Decimal(os.environ.get("SUPPORT_REFUND_AUTO_LIMIT", "200"))


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _audit(conn, ticket_id: int, tool: str, args: dict, result: dict, phase: str = "act") -> dict:
    result = _jsonable(result)
    summary = result.get("message") or ("ok" if result.get("ok", True) else "failed")
    log_event(conn, ticket_id, phase, tool, summary, {"args": _jsonable(args), "result": result})
    return result


def _ticket(conn, ticket_id: int):
    return conn.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,)).fetchone()


def _ticket_email(conn, ticket_id: int, email: str):
    """The only email a read tool may look up: the one on the ticket.

    Returns (email, None) when the caller passed nothing or the ticket's own address, and (None, error_message) when it
    asked about someone else (or the ticket does not exist), so a manipulated agent cannot read another customer's data.
    """
    ticket = _ticket(conn, ticket_id)
    if not ticket:
        return None, f"Ticket {ticket_id} not found"
    if email and email.strip().lower() != ticket["email"].strip().lower():
        return None, "Not allowed: you can only look up the customer who opened this ticket"
    return ticket["email"], None


def _order_for_ticket(conn, ticket, order_number: str):
    """Fetch an order only if it belongs to the customer who opened the ticket."""
    return conn.execute(
        """
        SELECT o.* FROM orders o JOIN customers c ON c.id = o.customer_id
        WHERE o.order_number = %s AND lower(c.email) = lower(%s)
        """,
        (order_number.strip().upper(), ticket["email"]),
    ).fetchone()


def _refunded_total(conn, order_id: int) -> Decimal:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM refunds WHERE order_id = %s", (order_id,)
    ).fetchone()
    return Decimal(row["total"])


# --------------------------------------------------------------------------- read tools


@mcp.tool()
def lookup_customer(ticket_id: int, email: str = "") -> dict:
    """Look up the profile (name, tier, account age) of the customer who opened this ticket.

    `email` is optional; if given it must be the ticket's own email address.
    """
    with connect() as conn:
        own, error = _ticket_email(conn, ticket_id, email)
        if error:
            return _audit(conn, ticket_id, "lookup_customer", {"email": email}, {"found": False, "message": error})
        email = own
        row = conn.execute(
            "SELECT id, name, email, tier, created_at FROM customers WHERE lower(email) = lower(%s)",
            (email,),
        ).fetchone()
        result = (
            {"found": True, "customer": row, "message": f"Found {row['tier']} customer {row['name']}"}
            if row
            else {"found": False, "message": f"No customer account for {email}"}
        )
        return _audit(conn, ticket_id, "lookup_customer", {"email": email}, result)


@mcp.tool()
def list_customer_orders(ticket_id: int, email: str = "") -> dict:
    """List recent orders (number, status, total, date) of the customer who opened this ticket.

    `email` is optional; if given it must be the ticket's own email address.
    """
    with connect() as conn:
        own, error = _ticket_email(conn, ticket_id, email)
        if error:
            return _audit(conn, ticket_id, "list_customer_orders", {"email": email}, {"orders": [], "message": error})
        email = own
        rows = conn.execute(
            """
            SELECT o.order_number, o.status, o.total, o.created_at
            FROM orders o JOIN customers c ON c.id = o.customer_id
            WHERE lower(c.email) = lower(%s) ORDER BY o.created_at DESC LIMIT 10
            """,
            (email,),
        ).fetchall()
        result = {"orders": rows, "message": f"{len(rows)} order(s) on file"}
        return _audit(conn, ticket_id, "list_customer_orders", {"email": email}, result)


@mcp.tool()
def get_order(ticket_id: int, order_number: str) -> dict:
    """Get full details of an order (status, items, address, tracking, refunds so far).

    Only returns orders that belong to the customer who opened the ticket.
    """
    with connect() as conn:
        ticket = _ticket(conn, ticket_id)
        order = _order_for_ticket(conn, ticket, order_number) if ticket else None
        if not order:
            result = {"found": False, "message": f"Order {order_number} not found for this customer"}
        else:
            refunded = _refunded_total(conn, order["id"])
            order = {k: v for k, v in order.items() if k not in ("id", "customer_id")}
            result = {
                "found": True,
                "order": order,
                "refunded_so_far": refunded,
                "refundable_remaining": Decimal(order["total"]) - refunded,
                "message": f"Order {order['order_number']} is {order['status']}",
            }
        return _audit(conn, ticket_id, "get_order", {"order_number": order_number}, result)


@mcp.tool()
def search_knowledge_base(ticket_id: int, query: str, limit: int = 3) -> dict:
    """Full-text search the help-center / policy knowledge base. Returns the best matching articles."""
    limit = max(1, min(limit, 5))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT slug, title, body,
                   ts_rank(to_tsvector('english', title || ' ' || body || ' ' || tags),
                           websearch_to_tsquery('english', %(q)s)) AS rank
            FROM kb_articles
            WHERE to_tsvector('english', title || ' ' || body || ' ' || tags)
                  @@ websearch_to_tsquery('english', %(q)s)
            ORDER BY rank DESC LIMIT %(limit)s
            """,
            {"q": query, "limit": limit},
        ).fetchall()
        if not rows:
            # websearch_to_tsquery ANDs terms; fall back to OR-ing them.
            terms = " | ".join(w for w in "".join(c if c.isalnum() else " " for c in query).split())
            if terms:
                rows = conn.execute(
                    """
                    SELECT slug, title, body,
                           ts_rank(to_tsvector('english', title || ' ' || body || ' ' || tags),
                                   to_tsquery('english', %(q)s)) AS rank
                    FROM kb_articles
                    WHERE to_tsvector('english', title || ' ' || body || ' ' || tags)
                          @@ to_tsquery('english', %(q)s)
                    ORDER BY rank DESC LIMIT %(limit)s
                    """,
                    {"q": terms, "limit": limit},
                ).fetchall()
        result = {
            "articles": [{k: v for k, v in r.items() if k != "rank"} for r in rows],
            "message": f"{len(rows)} article(s) for '{query}'",
        }
        return _audit(conn, ticket_id, "search_knowledge_base", {"query": query}, result)


@mcp.tool()
def get_ticket_history(ticket_id: int, email: str = "") -> dict:
    """List the previous tickets of the customer who opened this one and how they ended (useful for repeat issues).

    `email` is optional; if given it must be the ticket's own email address.
    """
    with connect() as conn:
        own, error = _ticket_email(conn, ticket_id, email)
        if error:
            return _audit(conn, ticket_id, "get_ticket_history", {"email": email}, {"tickets": [], "message": error})
        email = own
        rows = conn.execute(
            """
            SELECT id, subject, status, category, created_at FROM tickets
            WHERE lower(email) = lower(%s) AND id <> %s ORDER BY created_at DESC LIMIT 10
            """,
            (email, ticket_id),
        ).fetchall()
        result = {"tickets": rows, "message": f"{len(rows)} previous ticket(s)"}
        return _audit(conn, ticket_id, "get_ticket_history", {"email": email}, result)


# -------------------------------------------------------------------------- write tools


@mcp.tool()
def issue_refund(ticket_id: int, order_number: str, amount: float, reason: str) -> dict:
    """Refund part or all of an order to the customer's original payment method.

    Refunds above the auto-approval limit, or above what remains refundable,
    are rejected; those cases must be escalated to a human.
    """
    args = {"order_number": order_number, "amount": amount, "reason": reason}
    amount_d = Decimal(str(amount)).quantize(Decimal("0.01"))
    with connect() as conn:
        ticket = _ticket(conn, ticket_id)
        order = _order_for_ticket(conn, ticket, order_number) if ticket else None
        if not order:
            result = {"ok": False, "message": f"Order {order_number} not found for this customer"}
        elif amount_d <= 0:
            result = {"ok": False, "message": "Refund amount must be positive"}
        elif amount_d > Decimal(order["total"]) - _refunded_total(conn, order["id"]):
            result = {"ok": False, "message": "Amount exceeds the refundable balance of the order"}
        elif amount_d > refund_auto_limit():
            result = {
                "ok": False,
                "requires_human_approval": True,
                "message": f"Refunds over ${refund_auto_limit()} need human approval - escalate",
            }
        else:
            conn.execute(
                "INSERT INTO refunds (order_id, ticket_id, amount, reason, created_at) "
                "VALUES (%s, %s, %s, %s, now())",
                (order["id"], ticket_id, amount_d, reason),
            )
            result = {"ok": True, "message": f"Refunded ${amount_d} on {order['order_number']}"}
        return _audit(conn, ticket_id, "issue_refund", args, result)


@mcp.tool()
def cancel_order(ticket_id: int, order_number: str, reason: str) -> dict:
    """Cancel an order that has not shipped yet. Shipped/delivered orders cannot be cancelled."""
    args = {"order_number": order_number, "reason": reason}
    with connect() as conn:
        ticket = _ticket(conn, ticket_id)
        order = _order_for_ticket(conn, ticket, order_number) if ticket else None
        if not order:
            result = {"ok": False, "message": f"Order {order_number} not found for this customer"}
        elif order["status"] != "processing":
            result = {"ok": False, "message": f"Order is already {order['status']}; cannot cancel"}
        else:
            conn.execute(
                "UPDATE orders SET status = 'cancelled', updated_at = now() WHERE id = %s",
                (order["id"],),
            )
            result = {"ok": True, "message": f"Cancelled {order['order_number']}"}
        return _audit(conn, ticket_id, "cancel_order", args, result)


@mcp.tool()
def update_shipping_address(ticket_id: int, order_number: str, new_address: str) -> dict:
    """Change the shipping address of an order that has not shipped yet."""
    args = {"order_number": order_number, "new_address": new_address}
    with connect() as conn:
        ticket = _ticket(conn, ticket_id)
        order = _order_for_ticket(conn, ticket, order_number) if ticket else None
        if not order:
            result = {"ok": False, "message": f"Order {order_number} not found for this customer"}
        elif order["status"] != "processing":
            result = {"ok": False, "message": f"Order is already {order['status']}; address is locked"}
        elif len(new_address.strip()) < 10:
            result = {"ok": False, "message": "Address looks incomplete"}
        else:
            conn.execute(
                "UPDATE orders SET shipping_address = %s, updated_at = now() WHERE id = %s",
                (new_address.strip(), order["id"]),
            )
            result = {"ok": True, "message": f"Updated shipping address on {order['order_number']}"}
        return _audit(conn, ticket_id, "update_shipping_address", args, result)


# ------------------------------------------------------------------------ outcome tools


def _close_ticket(conn, ticket_id: int, status: str, category: str, priority: str, **fields) -> bool:
    row = conn.execute(
        f"""
        UPDATE tickets SET status = %s, category = %s, priority = %s, finished_at = now(),
               {", ".join(f"{k} = %s" for k in fields)}
        WHERE id = %s AND status IN ('queued', 'processing')
        RETURNING id
        """,
        (status, category[:40], priority, *fields.values(), ticket_id),
    ).fetchone()
    return row is not None


@mcp.tool()
def resolve_ticket(ticket_id: int, category: str, customer_reply: str) -> dict:
    """Close the ticket as RESOLVED and send the final reply to the customer.

    Use only when the customer's issue is fully handled without a human.
    """
    args = {"category": category, "customer_reply": customer_reply}
    with connect() as conn:
        if not customer_reply.strip():
            result = {"ok": False, "message": "A customer reply is required"}
            return _audit(conn, ticket_id, "resolve_ticket", args, result)
        ok = _close_ticket(conn, ticket_id, "resolved", category, "normal", resolution=customer_reply)
        result = {"ok": ok, "message": "Ticket resolved" if ok else "Ticket is already closed"}
        return _audit(conn, ticket_id, "resolve_ticket", args, result, phase="outcome" if ok else "act")


@mcp.tool()
def escalate_ticket(
    ticket_id: int, category: str, reason: str, priority: str = "normal", customer_reply: str = ""
) -> dict:
    """Hand the ticket to a human agent, with the reason and a priority (low|normal|high|urgent).

    ``customer_reply`` is an optional holding message telling the customer what happens next.
    """
    priority = priority if priority in PRIORITIES else "normal"
    args = {"category": category, "reason": reason, "priority": priority, "customer_reply": customer_reply}
    with connect() as conn:
        ok = _close_ticket(
            conn, ticket_id, "escalated", category, priority,
            escalation_reason=reason, resolution=customer_reply,
        )
        result = {"ok": ok, "message": f"Escalated ({priority})" if ok else "Ticket is already closed"}
        return _audit(conn, ticket_id, "escalate_ticket", args, result, phase="outcome" if ok else "act")


def main():
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
