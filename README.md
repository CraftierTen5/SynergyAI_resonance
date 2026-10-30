# ResonanceAI backend

ResonanceAI includes preprocessing, tenant-scoped PostgreSQL accounts and assignments, and an optional local tutoring placeholder. Public tutoring generation and institution-admin onboarding are not implemented.

## Run locally

Create a virtual environment, install `requirements.txt`, and copy `.env.example` to `.env`. For the initial local migration, set `POSTGRES_DSN` to a PostgreSQL administrator connection (migration `0001` creates roles and enables `pgcrypto`). Also configure `AUTH_SIGNING_KEY` and `PROCESS_LOG_PSEUDONYM_KEY` (each at least 32 random bytes). Apply schema migrations before launching:

```powershell
python scripts/bootstrap_data_layer.py
```

Set `GEMINI_API_KEY` to enable real Gemini preprocessing. Then run:

```powershell
python -m uvicorn app.main:app --reload
```

Set the runtime `POSTGRES_DSN` to the `resonance_app` DSN after migrations. Open http://localhost:8000/app. Create an account with an institution ID (letters, numbers, underscores, or hyphens). Other users in that institution can register using the same ID and exact institution name. Sign-in requires the institution ID, email, and password. Once signed in, add assignments from the dashboard; they are stored for that institution and loaded on later sign-ins.

Generate local secrets with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Registration is open and creates an institution when its ID is new; this is intended for local integration testing, without email verification or institution administrator approval. Protect the service from public access until an institutional onboarding and administrator workflow is added. Session tokens expire after `SESSION_TTL_SECONDS` and are stored in the browser's local storage; signing out removes the browser token.

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
- `POST /api/auth/register`, `POST /api/auth/login`, and `GET /api/auth/me`: institution-scoped registration and bearer sessions. Registration creates an institution when the supplied ID is unused.
- `GET /api/assignments` and `POST /api/assignments`: authenticated, institution-scoped assignment listing and creation.
- `POST /preprocess`: multipart text and PNG/JPEG/WebP input. Returns original and normalized text, language and extraction statuses, provenance, warnings, and policy resolution metadata. Uploads are processed in memory and not persisted.
- `POST /dev/tutor/chat`: deterministic end-to-end demo, mounted only when `LOCAL_PLACEHOLDER_ENABLED=true`.

The router accepts an authoritative policy lookup callback for embedding applications. The standalone app has no such repository, so a missing or unresolved policy uses `YELLOW` guided assistance (`POLICY_NOT_FOUND`, `ASSIGNMENT_NOT_FOUND`, or the relevant conflict reason). A repository callback can distinguish a missing assignment (`ASSIGNMENT_NOT_FOUND`, return `None`) from a known assignment with no policy (empty list). RED remains blocked. YELLOW requires the `disclosure_acknowledged=true` multipart field and returns its configured conditions if acknowledgment is missing. This is a request-level acknowledgment only; no durable acknowledgment record or authenticated user identity exists. Candidate Gemini evidence remains non-authoritative. Stored policy outranks extracted text. Conflicting settings, differing conditions or versions, and unsupported policy scopes use YELLOW and retain their review reason.

## Policy and tutoring boundary

The existing wire values are retained: `NO_AI` → `RED`, `PROCESS_DISCLOSURE` → `YELLOW`, `FULL_INTEGRATION` → `GREEN`. Missing, ambiguous, invalid, or conflicting policy does not become `GREEN`. YELLOW conditions are preserved in the resolution result, but no public tutoring route or disclosure acknowledgment flow exists in this snapshot. Do not expose `MultilingualGuidanceService` directly to untrusted clients; an assignment repository and authorization/disclosure gate must resolve policy first.

The current `MultilingualGuidanceService` remains an internal provider orchestrator. There is no assignment policy repository, authorization context, or tutor HTTP endpoint connecting it to the deterministic resolver. No public route invokes Groq or generates tutoring responses. The provider adapters and preprocessing seam remain available for the host application once that policy gate exists.

## Preprocessing and future classifier

The configured Gemini REST adapter requests structured JSON. Translation retains the student's source text; OCR/document content is returned separately with provenance. Unreadable images produce a partial result and warning. Provider configuration, authentication, throttling, timeout, malformed-response, and outage failures are mapped to safe API errors. Text and image limits come from `PREPROCESS_MAX_*` settings.

`NormalizedClassifierInput` and `IntentActionRecommendation` define the future Qwen interface only; no model adapter or inference is implemented. Its eventual output must match the training prompt format and labels and remain advisory. The Python policy/action layer must validate any future recommendation before generation.

Open registration is intended for local integration testing and has no email verification or institution administrator approval. Session tokens are bearer tokens stored in browser local storage. Do not expose this build to public users until institutional onboarding and administrator authorization are implemented. Selectable-text PDF extraction is not part of this snapshot.
