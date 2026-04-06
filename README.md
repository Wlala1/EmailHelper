# OUMA Email Assistant

OUMA is a multi-agent mailbox assistant for the Outlook + campus-management workflow described in `cs5260 (1).pdf`.

## What is implemented

- Sequential-then-parallel backend flow:
  `Email -> Classifier -> [Attachment | Relationship Graph | Schedule] -> Response`
- Human review before reply draft write-back
- Human-in-the-loop category suggestion workflow with accept/reject and backlog reclassification
- Preference learning from feedback events
- n8n workflows for polling, bootstrap, calendar feedback, profile rebuild, and tag suggestion refresh
- Independent React demo console for overview, review queue, tag suggestions, and insights
- Offline `.eml` anonymization script using Presidio-compatible anonymization operators

## Architecture note

Proposal wording says the system runs without a centralized orchestrator. In this codebase:

- `n8n` is the system-level orchestrator for polling, bootstrap, rebuild, and recurring suggestion refresh.
- [services/orchestration.py](/Users/jackwang/Desktop/email/email_assistant/services/orchestration.py) is an internal API composition layer used to execute the agent chain for one email. It is not a second product-level orchestration surface.

## Proposal-aligned category defaults

The classifier now normalizes common course and career labels into:

- `Academic Conferences`
- `Canvas Course Updates`
- `Campus/Faculty Career Opportunities`
- `Social Events`
- `Teams Meetings`

## Demo frontend

The React console lives in [frontend](/Users/jackwang/Desktop/email/email_assistant/frontend).

```bash
cd frontend
npm install
npm run dev
```

By default Vite proxies `/v2`, `/health`, and `/auth` to `http://127.0.0.1:8000`.

## Privacy tooling

To anonymize exported `.eml` datasets for demos:

```bash
python -m scripts.anonymize_eml_export --input ./sample_eml --output ./anonymized_eml
```

The script anonymizes selected headers and text bodies while preserving the `.eml` structure.

## Deferred vs. proposal

The following proposal items remain intentionally deferred in this repo:

- WhatsApp / WeChat notification channels
- Gemini as a second model provider
- Rich standalone graph visualization beyond the demo dashboard summaries
