# SamarthAI / ResonanceAI backend

A small FastAPI modular monolith for a multilingual, responsible-AI learning platform. The MVP centers on deterministic assignment policy checks, assignment management, and a tutor endpoint that checks policy before calling a configurable LLM.

## What is implemented

- `GET /`, `GET /health`, and `GET /ready`.
- `GET /policies` and `POST /policies/check` for normalized RED, YELLOW, and GREEN decisions.
- Assignment create/list/read/policy-update routes. With PostgreSQL configured these use SQLAlchemy; without it, development uses volatile in-memory assignments and labels every response `volatile_development`.
- `POST /tutor/chat`: RED is refused in application logic without provider lookup; YELLOW selects Socratic mode and requires explicit disclosure acknowledgment; GREEN permits full-assistance mode.
- An optional OpenAI-compatible chat-completions adapter configured through environment variables. With no configuration, the app still starts and tutor requests that need a model return 503.
- `POST /viva/generate` using the configured provider, and a development-only in-memory process-log view.
- Pytest coverage for routes, policy decisions, disclosure, and provider behavior.

## Limitations

Authentication, role checks, and institution-scoped authorization are not implemented. Consequently, these routes are a hackathon development/demo backend and must not be exposed as a production multi-user system. In-memory assignments and logs disappear when the process restarts and are used only when `APP_ENV=development` and no database is configured. Process logs are not persisted in PostgreSQL. The database currently defines the assignment table only; institution, users, and durable logs need schema/model work. Analytics, CSV ingestion, duplicate detection, and retention rules are not implemented. The checked-in synthetic CSV can be validated, but is not imported into application storage. Model instructions for YELLOW improve guidance but cannot guarantee the model will never produce a complete answer. Review generated content and viva questions.

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

The adapter supports OpenAI-compatible Chat Completions endpoints:

```dotenv
LLM_PROVIDER=openai
LLM_API_KEY=your-local-secret
LLM_MODEL=provider-model-name
LLM_BASE_URL=https://api.openai.com/v1
```

Keep `.env` private; it is ignored by Git. For a compatible service, set its base URL and model. The request uses `/chat/completions`. Provider calls are not made by ordinary tests. The API key and full database URL are not returned by health/readiness endpoints.

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

