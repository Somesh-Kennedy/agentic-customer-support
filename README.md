# Agentic Customer Support

A multi-agent support system built on **CrewAI + MCP**, backed by **PostgreSQL**, with a **Django** web app for capturing
requests, a worker queue, a live trace page and a metrics dashboard.

```
 Django form ──► tickets (Postgres) ──► worker ──► CrewAI crew ──MCP/stdio──► back-office tools ──► Postgres
                                                    REASON              ACT                       OUTCOME
```

| Stage | What happens | Where |
|---|---|---|
| **Reason** | A three-agent crew: *Triage Specialist* → *Resolution Agent* → *Quality & Escalation Lead* | `support_crew/crew.py` |
| **Act** | Agents call back-office tools over MCP: customers, orders, knowledge base, refunds, cancellations, address changes | `mcp_server/server.py` |
| **Outcome** | The QA agent closes the ticket with `resolve_ticket` or `escalate_ticket`; the worker escalates anything left undecided | `support_crew/runner.py` |

Each step is written to `ticket_events`. The ticket page shows the full trace, and the dashboard reports the
**end-to-end resolution rate** and **escalation rate**, both computed from real ticket outcomes.

## Contents
[One ticket, end to end](#one-ticket-end-to-end) · [The agents](#the-agents) · [MCP tools and rules](#mcp-tools-and-the-rules-they-enforce) ·
[Database](#database) · [Web app, worker, dashboard](#web-app-worker-and-dashboard) · [Design notes](#design-notes) ·
[Measured behaviour](#measured-behaviour) · [Quick start](#quick-start-docker) · [Tests](#tests) · [Layout](#layout) · [Limitations](#limitations)

## One ticket, end to end
1. A customer fills in the form at `/` (email, subject, message, optional order number). A `tickets` row is created with
   status `queued`.
2. The worker (`manage.py run_support_worker`) claims the oldest queued ticket, marks it `processing`, and starts the MCP
   server as a child process over stdio.
3. The **triage** agent looks up the customer, their orders and earlier tickets, searches the knowledge base, and writes a
   brief: category, priority, relevant order and status, applicable policy.
4. The **resolution** agent reads the brief, confirms the order with `get_order`, and acts (refund, cancel, change address)
   if policy allows. If a tool refuses, it records why and does not work around it. It drafts a short reply.
5. The **quality lead** reviews the work and closes the ticket with exactly one call: `resolve_ticket` (issue fully handled,
   reply accurate) or `escalate_ticket` (anything else, with a reason, a priority and a holding reply for the customer).
6. Every step above is a row in `ticket_events`. The ticket page polls `/tickets/<id>/status/` while the ticket is open and
   shows the trace; the dashboard recalculates from the final statuses.

## The agents
| Agent | Tools it can use | Job |
|---|---|---|
| Support Triage Specialist | `lookup_customer`, `list_customer_orders`, `get_ticket_history`, `search_knowledge_base` | Facts first: who is the customer, what do they want, which policy applies. Never guesses. |
| Customer Resolution Agent | `get_order`, `search_knowledge_base`, `issue_refund`, `cancel_order`, `update_shipping_address` | Fix it within policy. Told to claim an action happened only if the tool returned `ok=true`. |
| Quality & Escalation Lead | `get_order`, `resolve_ticket`, `escalate_ticket` | Checks the reply matches what the tools did. Escalates legal threats, safety issues, fraud signals, policy exceptions, angry repeat contacts, and anything a tool refused. |

Each agent only receives the tools in its own list (the crew picks them by name and fails at start-up if the MCP server is
missing one). The customer's message is wrapped in `<customer_message>` tags and the agents are told to treat it as a
description of the problem, never as instructions. Agents run one after another (`Process.sequential`), each with a cap of
12 iterations, and later tasks receive earlier outputs as context.

## MCP tools and the rules they enforce
The agents never touch the database. They call tools on an **MCP server** (`FastMCP`, stdio), and the **server enforces the
business rules**, so a persuasive customer or a confused model cannot break them. Every call is written to `ticket_events`.

| Tool | Rule enforced in code |
|---|---|
| `lookup_customer`, `list_customer_orders`, `get_ticket_history` | Read only. They take an email address (see Limitations). |
| `get_order` | Returns an order only if it belongs to the customer (matched by email) who opened the ticket. Also reports refunded so far and refundable remaining. |
| `search_knowledge_base` | PostgreSQL full-text search over the help articles; if the AND query finds nothing it retries with OR. |
| `issue_refund` | Order must belong to the ticket's customer; amount must be positive; cannot exceed what is still refundable; **cannot exceed the auto-approval limit** (`SUPPORT_REFUND_AUTO_LIMIT`, default $200) and says to escalate instead. |
| `cancel_order` | Order must belong to the ticket's customer and still be `processing`. Shipped or delivered orders are refused. |
| `update_shipping_address` | Same ownership check, only `processing` orders, and the new address must look complete. |
| `resolve_ticket` | Needs a non-empty customer reply. A ticket can be closed only once. |
| `escalate_ticket` | Records reason and priority (`low`, `normal`, `high`, `urgent`; anything else becomes `normal`) and an optional holding reply. A ticket can be closed only once. |

## Database
Django owns the schema and migrations. The MCP server reads and writes the same tables with plain SQL through psycopg, so
every model pins its table name.

| Table | Contents |
|---|---|
| `customers` | name, unique email, tier (standard or premium) |
| `orders` | order number, status (processing, shipped, delivered, cancelled), total, items (JSON), shipping address, tracking number |
| `kb_articles` | slug, title, body, tags (the policy knowledge base searched by the agents) |
| `tickets` | email, subject, body, order number, status (queued, processing, resolved, escalated, failed), category, priority, reply, escalation reason, started and finished times |
| `ticket_events` | one row per step: phase (reason, act, outcome, system), actor, summary, JSON payload |
| `refunds` | order, ticket, amount, reason |

## Web app, worker and dashboard
- **Django, full stack:** models and migrations, forms with validation, views and templates (submit page, ticket detail with
  trace, dashboard), the admin (with a re-queue action for failed tickets), and the worker as a management command.
- **Worker:** claims tickets with `SELECT … FOR UPDATE SKIP LOCKED` inside a transaction, so several workers can run without
  two of them taking the same ticket.
- **Dashboard** (`/dashboard/`): totals; resolved, escalated and failed counts; **resolution rate** and **escalation rate**
  over closed tickets (escalated and failed both count as human handling); average handling time; resolution by category;
  most-used tools. If you set `SUPPORT_BASELINE_ESCALATION_RATE` (0 to 1) it also shows the relative change in escalation
  rate against that baseline, so an earlier version's escalation rate can be compared with the current one.

## Design notes
- **The MCP server enforces policy, not the LLM** (see the table above).
- **Fail closed.** If the crew finishes without making a decision, the ticket is escalated to a human. If it crashes, the
  ticket is marked `failed` and can be re-queued from the admin.
- **Prompt-injection hygiene.** Customer text is delimited and labelled as data, and the tools that change anything check
  that the order belongs to the ticket's customer.
- **LLM:** every agent uses Claude Opus 5.5 (`anthropic/claude-opus-5-5`) through CrewAI's native Anthropic provider. A
  small HTTP interceptor (`support_crew/llm.py`) sets `output_config.effort` (`SUPPORT_LLM_EFFORT`, default `medium`)
  and turns on server-side refusal fallbacks (`fallbacks: "default"`; set `SUPPORT_LLM_FALLBACKS=0` to turn it off).
  To change the model, set `SUPPORT_LLM_MODEL`; any model string CrewAI supports works, and `groq/<model>` (for example
  `groq/openai/gpt-oss-120b`, with `GROQ_API_KEY` set) goes through Groq's OpenAI-compatible endpoint.

## Measured behaviour
**Automated tests:** 21 tests pass against a real PostgreSQL 16 container (see [Tests](#tests)). No LLM calls are made.

**One live run** with `groq/openai/gpt-oss-120b` on Groq's free tier, on the five sample tickets from `seed_demo --tickets`
(2026-11-07):

| Ticket | Scenario | Outcome | Right? |
|---|---|---|---|
| 1 | Headphones arrived broken (ORD-1001, $89.99) | escalated: "agents finished without resolving or escalating" | Should have been a refund; failed safe |
| 2 | Cancel the cable order (ORD-1002, processing) | escalated: same reason, **but the order was cancelled** | Action correct, ticket not closed; failed safe |
| 3 | "Where is my keyboard?" (shipped) | resolved with a reply naming the order and dispatch date | Yes |
| 4 | Monitor with dead pixels, refund of $649 | escalated: "exceeds auto-approval limit, requires supervisor approval" | Yes: above the $200 limit |
| 5 | Wrong address on a processing order | escalated: same "no decision" reason | Should have changed the address; failed safe |

That is **1 resolved, 1 correct escalation and 3 safe fall-backs out of 5**. Three things to read into it: the policy and
fail-closed paths worked (a $649 refund was not approved, and no ticket was left open); the free tier returned 429
rate-limit errors and retried, and this model did not always finish with a closing tool call, so this is not a resolution
rate; and I did not inspect what tickets 1 and 5 had done before they stopped. It is **not** a benchmark. The default model
(Claude Opus 5.5) was not run here.

## Quick start (Docker)

```bash
cp .env.example .env          # add your ANTHROPIC_API_KEY
docker compose up --build
```

Open http://localhost:8000. The `web` service migrates and seeds the demo data, and `worker` runs the crew.

## Local development

```bash
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # set ANTHROPIC_API_KEY and DATABASE_URL
docker compose up -d db                             # or point DATABASE_URL at any Postgres 14+

python manage.py migrate
python manage.py seed_demo            # demo customers, orders and KB; add --tickets to queue 5 sample tickets
python manage.py createsuperuser      # optional, for /admin
python manage.py runserver            # terminal 1
python manage.py run_support_worker   # terminal 2 (add --once to drain the queue and exit)
```

To run the MCP server on its own (for example, with an MCP inspector): `python -m mcp_server.server`

## Demo data

4 customers, 5 orders and 6 knowledge-base articles (refunds, cancellation, address changes, shipping, warranty, accounts).

| Customer | Orders | Scenario to try |
|---|---|---|
| ava@example.com (premium) | ORD-1001 delivered $89.99 · ORD-1002 processing | Broken headphones → refund; cancel the cable order |
| liam@example.com | ORD-2001 shipped | "Where is my keyboard?" |
| sofia@example.com | ORD-3001 delivered $649 | Refund over the limit → escalated |
| noah@example.com (premium) | ORD-4001 processing | Change shipping address |

## Tests

```bash
python manage.py test support
```

The tests need a reachable Postgres. They cover each MCP tool's guardrails against a real database, an MCP stdio
round-trip, the runner's resolved, undecided and crash paths, crew wiring, the request interceptor, views, metrics and
the worker's ticket claiming. No LLM calls are made.

## Layout

```
config/          Django settings and URLs
support/         Django app: models, views, templates, metrics, admin, management commands
mcp_server/      FastMCP server exposing the back-office tools (stdio)
support_crew/    CrewAI crew, LLM config, and the ticket runner
```

## Limitations
- All data is demo data; no real customers or orders. The knowledge base has six short articles.
- `lookup_customer`, `list_customer_orders` and `get_ticket_history` take an email address from the model and are not tied to
  the ticket's own email, so a manipulated agent could read another customer's order list or ticket history. Order details and
  every write are tied to the ticket's customer. Binding the read tools too is the obvious hardening step.
- No authentication on the ticket form, and no email delivery: the "reply" is stored on the ticket, not sent.
- Quality depends on the model. Smaller or rate-limited models may stop without closing the ticket (the worker then
  escalates it). There is no automated end-to-end evaluation of resolution quality in this repo, only the one live run above.
- Refund, cancellation and address rules are simple and fixed; real policies have many more cases.
