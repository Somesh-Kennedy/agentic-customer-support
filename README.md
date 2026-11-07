# Agentic Customer Support

A multi-agent support system built on **CrewAI + MCP**, backed by **PostgreSQL**, with a **Django** front end for capturing requests.

```
 Django form ──► tickets (Postgres) ──► worker ──► CrewAI crew ──MCP/stdio──► back-office tools ──► Postgres
                                                    REASON              ACT                       OUTCOME
```

| Stage | What happens | Where |
|---|---|---|
| **Reason** | A three-agent crew: *Triage Specialist* → *Resolution Agent* → *Quality & Escalation Lead* | `support_crew/crew.py` |
| **Act** | Agents call back-office tools over MCP: customers, orders, knowledge base, refunds, cancellations, address changes | `mcp_server/server.py` |
| **Outcome** | The QA agent closes the ticket with `resolve_ticket` or `escalate_ticket`; the worker escalates anything left undecided | `support_crew/runner.py` |

Each step is written to `ticket_events`. The ticket page shows the full trace live, and the dashboard reports the
**end-to-end resolution rate** and **escalation rate**, both computed from real ticket outcomes.

## Design notes

- **The MCP server enforces policy, not the LLM.** Orders are only visible to the customer who opened the ticket. Refunds
  can't exceed the refundable balance or the auto-approval limit (`SUPPORT_REFUND_AUTO_LIMIT`, default $200). Shipped
  orders can't be cancelled or re-addressed. A ticket can only be closed once.
- **Django owns the schema.** The MCP server talks to the same tables with plain SQL through psycopg. Every model pins
  its `db_table`.
- **Fail closed.** If the crew finishes without making a decision, the ticket is escalated. If it crashes, the ticket is
  marked `failed` and can be re-queued from the admin.
- **The worker is safe to scale.** Tickets are claimed with `SELECT … FOR UPDATE SKIP LOCKED`.
- **LLM:** every agent uses Claude Opus 5.5 (`anthropic/claude-opus-5-5`) through CrewAI's native Anthropic provider. A
  small HTTP interceptor (`support_crew/llm.py`) sets `output_config.effort` (`SUPPORT_LLM_EFFORT`, default `medium`)
  and turns on server-side refusal fallbacks (`fallbacks: "default"`; set `SUPPORT_LLM_FALLBACKS=0` to turn it off).
  To change the model, set `SUPPORT_LLM_MODEL`; any model string CrewAI/LiteLLM accepts works (for example
  `groq/llama-3.3-70b-versatile` with `GROQ_API_KEY` set).

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
