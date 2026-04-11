# OUMA Email Assistant

OUMA is an AI-powered email intelligence platform for campus and Outlook workflows. It connects to a user's Microsoft Outlook mailbox, processes emails through a sequential-then-parallel multi-agent pipeline, and surfaces actionable insights — reply drafts, calendar event suggestions, and relationship intelligence — for human review before any write-back occurs.

---

## Architecture

```
                        Email Input
                             │
                        [1] Intake
                      (persist to DB)
                             │
                    [2] Classifier
            ┌─── (parse attachments,
            │     build context,
            │     LLM classify)
            │
        [attachment]  ← sub-step inside Classifier;
                        creates its own agent_run record
                             │
          ┌──────────────────┴──────────────────┐
   [3] Relationship Graph               [4] Schedule Agent
    (person/org roles, Neo4j sync)     (time extraction,
                                        conflict check)
          └──────────────────┬──────────────────┘
                             │
                      [5] Response Agent
                  (reply required? tone templates)
                             │
                  [6] Human Review Queue
                             │
                  Outlook Draft Write-back
```

**Orchestration note**: `n8n` is the system-level orchestrator that drives polling, bootstrap, calendar feedback sync, profile rebuilds, and tag suggestion refresh on schedule. [`services/orchestration.py`](email_assistant/services/orchestration.py) is the internal API composition layer that chains the five agents for a single email — it is not a second product-level orchestrator.

---

## Features

- **Sequential-then-parallel agent chain** — classification runs first, then attachment analysis, relationship graph, and scheduling execute in parallel before the response agent synthesizes results
- **Microsoft Outlook integration** via Microsoft Graph API with OAuth 2.0 (delta-sync polling, draft creation, calendar read/write)
- **OpenAI GPT-4o classification and analysis** with heuristic fallbacks
- **Document parsing** for PDF, Excel, DOCX, and PPTX attachments
- **Relationship graph** with weighted contact observations stored in PostgreSQL and optionally Neo4j
- **Schedule detection** — extracts time references from email body, checks free/busy, creates tentative Outlook events when confidence is high
- **Human-in-the-loop reply review** with three tone templates per suggestion (professional, casual, colloquial)
- **Category suggestion workflow** — detects new email clusters every 12 hours, proposes new category labels, and backfill-reclassifies history upon acceptance
- **Writing profile learning** from sent email history (greeting/closing patterns, tone profile)
- **Preference vector learning** from user feedback events
- **React + Vite demo console** (overview, review queue, tag suggestions, insights)
- **PII anonymization tooling** for `.eml` dataset demos

---

## Technology Stack

| Layer | Technology |
|---|---|
| API | FastAPI, Uvicorn |
| Database | PostgreSQL 16 (prod) / SQLite (dev), SQLAlchemy 2, Alembic |
| Graph DB | Neo4j 5 (optional) |
| LLM | OpenAI GPT-4o / GPT-4o-mini |
| Email | Microsoft Graph API (OAuth 2.0) |
| Document Parsing | pdfminer.six, openpyxl, mammoth, python-pptx |
| PII Redaction | Microsoft Presidio |
| Workflow Orchestration | n8n |
| Frontend | React 18, TypeScript, Vite |
| Containerization | Docker, Docker Compose |

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+ (frontend only)
- An Azure App Registration with the following delegated scopes: `offline_access openid profile User.Read Mail.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite`
- An OpenAI API key
- A Neo4j 5 instance (optional — set `NEO4J_REQUIRED=false` to run without it)

### Environment Configuration

```bash
cp email_assistant/.env.example email_assistant/.env
```

Edit `.env` and fill in at minimum:

```
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
AZURE_REDIRECT_URI=http://localhost:8000/auth/microsoft/callback

OPENAI_API_KEY=...

DATABASE_URL=sqlite:///./data/ouma.db   # or postgresql+psycopg://user:pass@host:5432/db
```

See [Configuration Reference](#configuration-reference) for all options.

### Local Development

**Backend:**

```bash
cd email_assistant
pip install -r requirements.txt
uvicorn main:app --reload
# API available at http://localhost:8000
# Interactive docs at http://localhost:8000/docs
```

**Frontend:**

```bash
cd email_assistant/frontend
npm install
npm run dev
# UI available at http://localhost:5173
```

Vite proxies `/v2`, `/health`, and `/auth` to `http://127.0.0.1:8000` automatically.

### Docker Compose

```bash
cp email_assistant/.env.example email_assistant/.env
# fill in credentials in .env
docker compose -f email_assistant/docker-compose.yml up --build
```

| Service | Port | Description |
|---|---|---|
| `api` | 8000 | FastAPI backend |
| `frontend` | 80 | React demo console |
| `postgres` | 5432 | Primary database |
| `neo4j` | 7474 / 7687 | Graph database (optional) |

---

## How It Works

### 1. OAuth Login

The user visits the frontend and clicks "Sign in with Microsoft". The backend returns an Azure authorization URL. After the user approves the required scopes, the callback handler stores OAuth tokens in the `user_mailbox_accounts` table and sets `bootstrap_status = "running"`.

### 2. Bootstrap Sync

A background worker (or n8n trigger) runs `MailboxSyncService.bootstrap_user()`:

- Fetches the last 180 days of inbox and sent folder emails via the Graph API
- For each email, executes the full five-agent chain (Intake → Classifier → [Attachment | Relationship | Schedule] → Response)
- For sent emails, additionally calls `learn_from_outbound_email()` to build the writing profile
- Records `bootstrap_completed_at_utc` when finished

### 3. Live Polling

Every 300 seconds, `MailboxSyncService.poll_user()` fetches only new or modified messages using the Microsoft Graph delta-sync mechanism. Delta tokens are stored per-folder in `user_mailbox_state` and reused on each cycle.

### 4. Human Review

The dashboard presents:
- **Reply queue** — pending `ReplySuggestion` records; user selects a tone, optionally edits, and approves; the backend then creates a draft in Outlook via the Graph API
- **Tag suggestions** — proposed new category labels; accepting one triggers backfill reclassification of historical emails

### 5. Category Suggestion Cycle (every 12 hours)

The background worker scans recently classified emails, detects clusters with similar patterns, and writes `CategorySuggestion` records for user review.

### 6. Writing Profile Rebuild (every 24 hours)

Analyzes the last N sent emails to update `UserWritingProfile` — tone style, language, greeting and closing patterns, and a behavioral preference vector derived from accepted/rejected feedback events.

### 7. Calendar Feedback Sync

Periodically fetches Outlook calendar events created by OUMA and records whether the user accepted, marked tentative, or declined each one. These signals update relationship observation weights for future scheduling decisions.

---

## Agent Details

| Agent | Input | Core Logic | Output Tables |
|---|---|---|---|
| **Intake** | OUMAEnvelope | Upsert user, email, recipients, attachment metadata | `users`, `emails`, `email_recipients`, `attachments` |
| **Classifier** | Email ID | Parse attachments (PDF/Excel/DOCX/PPTX) → build combined context → LLM classify (GPT-4o) with heuristic fallback → normalize category. Attachment parsing runs as an internal sub-step and writes its own `agent_run` record (`agent_name="attachment"`) before classification proceeds. | `classifier_results`, `category_definitions`, `attachment_results`, `agent_runs` (attachment) |
| **Relationship Graph** | Email sender + recipients | Extract person role and organization via LLM → record observation → sync to Neo4j | `relationship_observations`, Neo4j Person/Organization nodes |
| **Schedule** | Classifier result + email body | Extract time expressions → check free/busy → rank candidates → optionally create tentative Outlook event | `schedule_candidates` |
| **Response** | Classifier output + attachment/relationship/schedule statuses + writing profile | Determine sender identity tier (authority / professional / peer) → decide reply required → generate three tone templates | `reply_suggestions`, `reply_draft_writes` |

---

## API Reference

### Authentication

| Method | Path | Description |
|---|---|---|
| GET | `/auth/microsoft/start` | Returns Azure OAuth authorization URL |
| GET | `/auth/microsoft/callback` | Handles OAuth callback, stores tokens, redirects to dashboard |

### Email Intake

| Method | Path | Description |
|---|---|---|
| POST | `/v2/intake/email` | Ingest an OUMAEnvelope (used by n8n and tests) |

### Agents

| Method | Path | Description |
|---|---|---|
| POST | `/v2/agents/classifier/run` | Run classifier for an email |
| POST | `/v2/agents/relationship_graph/run` | Extract relationship data |
| POST | `/v2/agents/schedule/run` | Detect schedule candidates |
| POST | `/v2/agents/response/run` | Generate reply suggestion |
| GET | `/v2/agents/classifier/tag_suggestions/{user_id}` | List category suggestions |
| POST | `/v2/agents/classifier/tag_suggestions/{suggestion_id}` | Accept or reject a suggestion |

### Reply Review

| Method | Path | Description |
|---|---|---|
| GET | `/v2/agents/response/review/{email_id}` | Get review status |
| POST | `/v2/agents/response/review/{email_id}` | Submit user decision (approve / reject / defer) |

### Dashboard & Status

| Method | Path | Description |
|---|---|---|
| GET | `/v2/users/{user_id}/status` | Bootstrap status, polling state, profile info |
| GET | `/v2/users/{user_id}/dashboard` | Aggregated analytics (cards, review queue, relationships, feedback) |
| POST | `/v2/users/{user_id}/bootstrap/retry` | Restart a failed bootstrap |
| GET | `/v2/traces/{trace_id}/emails/{email_id}/status` | Per-email agent run trace |
| GET | `/health` | Liveness probe |

### n8n Integration Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v2/n8n/users_due_for_poll` | User IDs ready for polling |
| GET | `/v2/n8n/users_pending_bootstrap` | User IDs needing bootstrap |
| GET | `/v2/n8n/active_users` | All connected users |
| POST | `/v2/n8n/poll_user/{user_id}` | Trigger inbox/sent poll |
| POST | `/v2/n8n/bootstrap_user/{user_id}` | Trigger bootstrap |
| POST | `/v2/n8n/sync_calendar_feedback/{user_id}` | Sync calendar acceptance signals |
| POST | `/v2/n8n/rebuild_profile/{user_id}` | Rebuild writing profile |
| POST | `/v2/n8n/generate_tag_suggestions/{user_id}` | Generate category suggestions |

---

## Database Schema

| Table | Purpose |
|---|---|
| `users` | User profiles, timezone, last login |
| `emails` | Email records (sender, subject, body, direction, folder) |
| `email_recipients` | TO/CC recipients per email |
| `attachments` | File metadata and local storage path |
| `agent_runs` | Audit trail for every agent invocation |
| `classifier_results` | Category, urgency, summary, sender role, NER entities, time expressions |
| `category_definitions` | User-specific category catalog |
| `category_suggestions` | Proposed new categories (pending / accepted / rejected) |
| `attachment_results` | Document type, relevance score, extracted text, topics |
| `relationship_observations` | Person + org + signal type + weight + timestamp |
| `schedule_candidates` | Meeting time slots, conflict score, action (create / suggest / ignore) |
| `reply_suggestions` | `reply_required` flag + three tone templates |
| `reply_draft_writes` | Draft creation status and Outlook draft ID |
| `user_mailbox_accounts` | OAuth token blob, expiry, Graph user ID |
| `user_mailbox_state` | Bootstrap status, polling enabled, delta tokens |
| `user_writing_profiles` | Tone profile, greeting/closing patterns, preference vector |
| `user_feedback_events` | User signals from accepted/rejected/edited suggestions |
| `sync_runs` | Audit log for bootstrap and poll operations |
| `system_leases` | Distributed lock table (prevents duplicate jobs) |

Schema is managed by Alembic. Migrations live in [`email_assistant/migrations/versions/`](email_assistant/migrations/versions/).

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required. OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | Model for classification and analysis |
| `OPENAI_ATTACHMENT_SUMMARY_MODEL` | `gpt-4o-mini` | Model for attachment summarization |
| `OPENAI_STYLE_PROFILE_MODEL` | `gpt-4o-mini` | Model for writing profile extraction |
| `DATABASE_URL` | `sqlite:///./data/ouma.db` | SQLAlchemy connection string |
| `AUTO_CREATE_SCHEMA` | `true` | Set to `false` in production and use Alembic |
| `NEO4J_URI` | — | Neo4j bolt URI (optional) |
| `NEO4J_REQUIRED` | `false` | If `true`, startup fails without Neo4j |
| `AZURE_CLIENT_ID` | — | Required. Azure App client ID |
| `AZURE_CLIENT_SECRET` | — | Required. Azure App client secret |
| `AZURE_TENANT_ID` | `common` | Azure tenant (use `common` for multi-tenant) |
| `AZURE_REDIRECT_URI` | `http://localhost:8000/auth/microsoft/callback` | OAuth callback URL |
| `BOOTSTRAP_LOOKBACK_DAYS` | `180` | How many days of history to sync on bootstrap |
| `BOOTSTRAP_MAX_PROFILE_EMAILS` | `200` | Max sent emails used for writing profile |
| `POLL_INTERVAL_SECONDS` | `300` | Inbox polling frequency |
| `PROFILE_REBUILD_INTERVAL_SECONDS` | `86400` | Writing profile rebuild interval (24h) |
| `CATEGORY_SUGGESTION_INTERVAL_SECONDS` | `43200` | Category suggestion generation interval (12h) |
| `AUTO_DRAFT_RELATIONSHIP_THRESHOLD` | `0.8` | Minimum relationship weight to auto-create tentative events |
| `MAX_CLASSIFIER_CONTEXT_CHARS` | `12000` | Max characters fed to the classifier |
| `ENABLE_BACKGROUND_WORKERS` | `true` | Disable to rely entirely on n8n for scheduling |
| `USE_DECAYED_WEIGHT` | `false` | Phase B: time-decayed relationship weights |
| `USE_PREFERENCE_VECTOR` | `false` | Phase E: behavioral preference vector for tone ranking |

---

## Proposal-Aligned Category Defaults

The classifier normalizes common course and career labels into:

- `Academic Conferences`
- `Canvas Course Updates`
- `Campus/Faculty Career Opportunities`
- `Social Events`
- `Teams Meetings`

---

## Frontend Console

The React console is in [`email_assistant/frontend/`](email_assistant/frontend/) and provides four views:

- **Overview** — summary cards (pending reviews, draft writes, schedule candidates), category distribution
- **Review Queue** — pending reply suggestions awaiting user decision
- **Tag Suggestions** — proposed new categories to accept or reject
- **Insights** — relationship graph summary, feedback analytics, schedule overview

```bash
cd email_assistant/frontend
npm install
npm run dev
```

Vite proxies `/v2`, `/health`, and `/auth` to `http://127.0.0.1:8000`.

---

## Privacy Tooling

To anonymize exported `.eml` datasets for demos using Microsoft Presidio:

```bash
python -m scripts.anonymize_eml_export --input ./sample_eml --output ./anonymized_eml
```

The script anonymizes selected headers and text bodies while preserving the `.eml` structure.

---

## Deferred Items

The following proposal items are intentionally deferred:

- WhatsApp / WeChat notification channels
- Gemini as a second LLM provider
- Rich standalone graph visualization beyond the demo dashboard summaries
