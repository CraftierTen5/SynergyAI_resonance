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

## Local tutoring pipeline placeholder

The fine-tuned model can be unavailable while the request workflow is being
integrated. For a deterministic, offline end-to-end run, set
`LOCAL_PLACEHOLDER_ENABLED=true` in `.env` and start the app as usual. This
mounts `POST /dev/tutor/chat`, backed by local adapters that implement the same
ports used by the orchestrator. The route uses server-owned demo assignments:
`demo-green`, `demo-yellow`, and `demo-red`; policy is never accepted from the
request. Its demo identity and audit records are in-memory development
fixtures, not production authentication or durable storage. Leave the flag off
outside local development.

Example request:

```json
{
  "request_id": "local-1",
  "assignment_id": "demo-green",
  "message": "Explain recursion"
}
```

The model and response adapters live in `app/agent/local_placeholder.py`. To
use the fine-tuned model later, replace the `ModelClient` implementation (and,
if desired, `ResponseGenerator`) in the application composition; the
orchestrator and API result contract remain the same.

Gemini is required for translation, image extraction, and candidate policy evidence extraction. The process can start without a Gemini key; `/ready` reports whether it is configured. PostgreSQL is not configured in this source snapshot. No live provider calls are made by the standard test suite.

## Current routes

- `GET /` and `GET /health`: process liveness.
- `GET /ready`: process readiness and truthful Gemini/assignment-store/Qwen configuration state. It does not probe provider connectivity.
- `POST /preprocess`: multipart text and PNG/JPEG/WebP input. Returns original and normalized text, language and extraction statuses, provenance, warnings, and policy resolution metadata. Uploads are processed in memory and not persisted.
- `POST /dev/tutor/chat`: deterministic end-to-end demo, mounted only when `LOCAL_PLACEHOLDER_ENABLED=true`.

The router accepts an authoritative policy lookup callback for embedding applications. The standalone app has no such repository, so a missing or unresolved policy uses `YELLOW` guided assistance (`POLICY_NOT_FOUND`, `ASSIGNMENT_NOT_FOUND`, or the relevant conflict reason). A repository callback can distinguish a missing assignment (`ASSIGNMENT_NOT_FOUND`, return `None`) from a known assignment with no policy (empty list). RED remains blocked. YELLOW requires the `disclosure_acknowledged=true` multipart field and returns its configured conditions if acknowledgment is missing. This is a request-level acknowledgment only; no durable acknowledgment record or authenticated user identity exists. Candidate Gemini evidence remains non-authoritative. Stored policy outranks extracted text. Conflicting settings, differing conditions or versions, and unsupported policy scopes use YELLOW and retain their review reason.

## Policy and tutoring boundary

The existing wire values are retained: `NO_AI` → `RED`, `PROCESS_DISCLOSURE` → `YELLOW`, `FULL_INTEGRATION` → `GREEN`. Missing, ambiguous, invalid, or conflicting policy does not become `GREEN`. YELLOW conditions are preserved in the resolution result, but no public tutoring route or disclosure acknowledgment flow exists in this snapshot. Do not expose `MultilingualGuidanceService` directly to untrusted clients; an assignment repository and authorization/disclosure gate must resolve policy first.

The current `MultilingualGuidanceService` remains an internal provider orchestrator. There is no assignment policy repository, authorization context, or tutor HTTP endpoint connecting it to the deterministic resolver. No public route invokes Groq or generates tutoring responses. The provider adapters and preprocessing seam remain available for the host application once that policy gate exists.

## Preprocessing and future classifier

The configured Gemini REST adapter requests structured JSON. Translation retains the student's source text; OCR/document content is returned separately with provenance. Unreadable images produce a partial result and warning. Provider configuration, authentication, throttling, timeout, malformed-response, and outage failures are mapped to safe API errors. Text and image limits come from `PREPROCESS_MAX_*` settings.

`NormalizedClassifierInput` and `IntentActionRecommendation` define the future Qwen interface only; no model adapter or inference is implemented. Its eventual output must match the training prompt format and labels and remain advisory. The Python policy/action layer must validate any future recommendation before generation.

No PDF upload, durable document storage, durable decision audit, institution isolation, or authenticated multi-user access is implemented. Selectable-text PDF extraction is also not part of this snapshot.
