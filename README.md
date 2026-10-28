## ResonanceAI backend

A small FastAPI modular monolith for a multilingual, responsible-AI learning platform. The MVP centers on deterministic assignment policy checks, assignment management, and a tutor endpoint that checks policy before calling a configurable LLM.

## What is implemented

- `GET /`, `GET /health`, and `GET /ready`.
- `GET /policies` and `POST /policies/check` for normalized RED, YELLOW, and GREEN decisions.
- Policy schemas accept the backend labels (`RED`, `YELLOW`, `GREEN`) and the orchestrate wire values (`NO_AI`, `PROCESS_DISCLOSURE`, `FULL_INTEGRATION`), then store and return canonical backend labels.
- Assignment create/list/read/policy-update routes. With PostgreSQL configured these use SQLAlchemy; without it, development uses volatile in-memory assignments and labels every response `volatile_development`.
- `POST /tutor/chat`: RED is refused in application logic without provider lookup; YELLOW selects Socratic mode and requires explicit disclosure acknowledgment; GREEN permits full-assistance mode.
- Groq (OpenAI-compatible Chat Completions), Gemini (`generateContent`), and legacy OpenAI-compatible adapters. Provider credentials and model names come from environment variables; no key is required for startup.
- `POST /tutor/chat/stream`: a provider-neutral adapter for the orchestrate branch's `generate_multilingual_guidance(request, provider)` contract. It derives tier/instructions from the stored assignment and rejects client-supplied policy fields. The service must be installed at `app.state.guidance_service`; until then this endpoint returns 503.
- `POST /viva/generate/from-process-logs`: a boundary for the orchestrate branch's evidence-based viva generator. It reads logs through an injected, authorization-aware server-side reader and rejects institution mismatches; logs are never accepted from the request body.
- `POST /viva/generate` using the configured provider, and a development-only in-memory process-log view.
- Pytest coverage for routes, policy decisions, disclosure, and provider behavior.

## Limitations

Authentication, role checks, and institution-scoped authorization are not implemented. Assignment, tutor, viva, and process-log routes therefore return 503 outside `APP_ENV=development`; do not expose the demo environment as a production multi-user system. In-memory assignments and logs disappear when the process restarts and are only enabled in development. Process logs retain hashes of prompt/response text rather than the raw text. The database currently defines the assignment table only; institution, users, and durable logs need schema/model work. The orchestrate-compatible streaming route is an integration boundary and needs an injected service to provide Redis context, cache, provider fallback, and usage metering. Analytics, CSV ingestion, duplicate detection, and retention rules are not implemented. The checked-in synthetic CSV can be validated, but is not imported into application storage. Model instructions for YELLOW cannot guarantee the model will never produce a complete answer. Review generated content and viva questions.

## Structure

```text
app/
  config.py       environment configuration
  database.py     PostgreSQL-only SQLAlchemy engine and assignment model
  main.py         HTTP routes and tutor policy orchestration
  policy.py       pure deterministic policy service
  schemas.py      validated request/response models
  tutor.py        provider adapter
tests/
  test_backend.py
```

## Windows PowerShell setup

Python 3.10 or newer is recommended. The repository already contains `.venv`; activate it or create a fresh one if needed:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
uvicorn app.main:app --reload
```

The server starts without a database or LLM. Open <http://127.0.0.1:8000/docs> for interactive API docs. `/health` reports that the process is running. `/ready` reports database readiness separately. Without a database, its `database` value is `not_configured`, and demo assignments are volatile.

Run tests:

```powershell
python -m pytest -q
```

Validate the synthetic CSV sample without writing to a database:

```powershell
python scripts/validate_sample_csv.py
```

## PostgreSQL configuration

Ask the teammate provisioning PostgreSQL for the host, port, database name, username, password, and any required SSL mode. Set `DATABASE_URL` in the local `.env`, for example:

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE
```

Use URL escaping for reserved characters in credentials. The app rejects non-PostgreSQL URLs and never falls back to SQLite. Once the database exists and is reachable, create the current development schema explicitly:

```powershell
python -c "from app.database import Base, engine; Base.metadata.create_all(engine)"
```

This creates missing tables only; it does not drop tables. For shared or production deployments, introduce and review Alembic migrations rather than relying on `create_all`. Verify the connection with `GET /ready`. No live PostgreSQL credentials or server were available during implementation, so this integration has not been verified against a real database.

## LLM provider configuration

The standard `/tutor/chat` endpoint supports Groq, Gemini, and OpenAI-compatible providers. Configure one provider:

```dotenv
GROQ_API_KEY=your-local-secret
GROQ_MODEL=llama-3.3-70b-versatile
# Or instead configure GEMINI_API_KEY and GEMINI_MODEL.
```

Keep `.env` private; it is ignored by Git. Groq uses `/chat/completions`; Gemini uses `:generateContent`. For a compatible custom provider, set `LLM_PROVIDER=openai_compatible`, `LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL`. Provider calls are not made by ordinary tests. The API key and full database URL are not returned by health/readiness endpoints.

## Orchestrate service integration

The streaming route creates an internal request with the orchestrate schema fields: request and institution IDs, conversation ID, messages, language, policy tier, instruction profile, policy version, system instruction, technical terms, and generation limits. It only uses policy data from the assignment; those fields cannot be overridden by the HTTP client. This backend branch defines the integration contract only; the orchestration implementation remains on its independent branch, so this endpoint returns 503 until a service is injected. The evidence viva route likewise needs an authorized process-log reader and an injected viva generator.

When the orchestration service is brought into this checkout, install it from the application composition layer before serving requests:

```python
from app.main import install_orchestration_services

install_orchestration_services(
    guidance_service=MultilingualGuidanceService(
        providers={"groq": groq_adapter, "gemini": gemini_adapter},
        redis_client=redis_client,
        usage_meter=usage_meter,
    ),
    process_log_reader=authorized_log_reader,
    viva_generator=viva_question_generator,
)
```

The meter must have an institution budget and centrally managed per-model token prices. `REDIS_URL`, `AI_INSTITUTION_BUDGETS_USD`, `AI_MODEL_PRICING`, and `PROCESS_LOG_PSEUDONYM_KEY` are provided as configuration placeholders in `.env.example`; no budget or pricing values are invented here. `/ready` reports whether the service boundary has been installed.

## Example API requests

Check a policy:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/policies/check -ContentType 'application/json' -Body '{"policy_level":" yellow "}'
```

Create a development assignment:

```powershell
$assignment = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/assignments -ContentType 'application/json' -Body '{"title":"AI ethics reflection","course":"Computing","policy_level":"YELLOW"}'
$assignment
```

Try tutor chat (requires provider configuration and acknowledgment for YELLOW):

```powershell
$body = @{ assignment_id = $assignment.id; message = 'Help me plan my reflection'; language = 'hinglish'; disclosure_acknowledged = $true } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/tutor/chat -ContentType 'application/json' -Body $body
```

## Short judge demo

1. Start the server and show `/health`, `/ready`, and `/docs`.
2. Use `/policies/check` with RED, YELLOW, and GREEN, then an invalid value.
3. Create one assignment per policy in the docs. Show that RED returns a local refusal even with no LLM key.
4. Show that YELLOW requests require disclosure acknowledgment and use Socratic mode; configure a provider to demonstrate the model response.
5. Show that development assignments are marked volatile and disappear on restart. Explain that production access requires real authentication and institution authorization before deployment.

