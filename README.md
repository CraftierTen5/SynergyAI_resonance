# ResonanceAI backend

This repository snapshot contains the AI provider adapters, Redis-backed tutoring orchestration, policy guard, assessment utilities, and the preprocessing MVP. It does not contain assignment SQLAlchemy models/repositories, a PostgreSQL session, authentication, or authorization. Accordingly, the server exposes preprocessing and status routes only; tutoring generation is not exposed as a public route.

## Run locally

Create a virtual environment, install `requirements.txt`, copy `.env.example` to `.env`, and set `GEMINI_API_KEY` to enable Gemini preprocessing. Then run:

```powershell
python -m uvicorn app.main:app --reload
```

Run the deterministic test suite with:

```powershell
python -m pytest -q
```

Gemini is required for translation, image extraction, and candidate policy evidence extraction. The process can start without a Gemini key; `/ready` reports whether it is configured. PostgreSQL is not configured in this source snapshot. No live provider calls are made by the standard test suite.

## Current routes

- `GET /` and `GET /health`: process liveness.
- `GET /ready`: process readiness and truthful Gemini/assignment-store/Qwen configuration state. It does not probe provider connectivity.
- `POST /preprocess`: multipart text and PNG/JPEG/WebP input. Returns original and normalized text, language and extraction statuses, provenance, warnings, and policy resolution metadata. Uploads are processed in memory and not persisted.

The router accepts an authoritative policy lookup callback for embedding applications. The standalone app has no such repository, so supplied assignment IDs resolve to `UNKNOWN` (`POLICY_NOT_FOUND`) before Gemini is called. A repository callback can distinguish a missing assignment (`ASSIGNMENT_NOT_FOUND`, return `None`) from a known assignment with no policy (empty list). RED and UNKNOWN assignments are rejected before Gemini. YELLOW requires the `disclosure_acknowledged=true` multipart field and returns its configured conditions if acknowledgment is missing. This is a request-level acknowledgment only; no durable acknowledgment record or authenticated user identity exists. Candidate Gemini evidence remains non-authoritative and cannot grant permission. Stored policy outranks extracted text. Conflicting settings, differing conditions or versions, and unsupported policy scopes resolve to `UNKNOWN` and require review.

## Policy and tutoring boundary

The existing wire values are retained: `NO_AI` → `RED`, `PROCESS_DISCLOSURE` → `YELLOW`, `FULL_INTEGRATION` → `GREEN`. Missing, ambiguous, invalid, or conflicting policy does not become `GREEN`. YELLOW conditions are preserved in the resolution result, but no public tutoring route or disclosure acknowledgment flow exists in this snapshot. Do not expose `MultilingualGuidanceService` directly to untrusted clients; an assignment repository and authorization/disclosure gate must resolve policy first.

The current `MultilingualGuidanceService` remains an internal provider orchestrator. There is no assignment policy repository, authorization context, or tutor HTTP endpoint connecting it to the deterministic resolver. No public route invokes Groq or generates tutoring responses. The provider adapters and preprocessing seam remain available for the host application once that policy gate exists.

## Preprocessing and future classifier

The configured Gemini REST adapter requests structured JSON. Translation retains the student's source text; OCR/document content is returned separately with provenance. Unreadable images produce a partial result and warning. Provider configuration, authentication, throttling, timeout, malformed-response, and outage failures are mapped to safe API errors. Text and image limits come from `PREPROCESS_MAX_*` settings.

`NormalizedClassifierInput` and `IntentActionRecommendation` define the future Qwen interface only; no model adapter or inference is implemented. Its eventual output must match the training prompt format and labels and remain advisory. The Python policy/action layer must validate any future recommendation before generation.

No PDF upload, durable document storage, durable decision audit, institution isolation, or authenticated multi-user access is implemented. Selectable-text PDF extraction is also not part of this snapshot.
