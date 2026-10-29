from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.ai import AITutorRequest, StreamChunk, TutorMessage
from app.services.ai.guidance import MultilingualGuidanceService
from app.services.ai.errors import ProviderUnavailableError
from app.services.policy.guard import PolicyGuardEngine, PolicyLevel


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}
        self.events = []

    async def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        stop = end + 1 if end >= 0 else len(values) + end + 1
        return values[start:stop]

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def ltrim(self, key, start, end):
        values = self.lists.get(key, [])
        stop = end + 1 if end >= 0 else len(values) + end + 1
        self.lists[key] = values[start:stop]

    async def expire(self, *args):
        return True

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        return True

    async def xadd(self, stream, fields, **kwargs):
        self.events.append((stream, fields, kwargs))


class FakeMeter:
    def __init__(self):
        self.calls = []

    async def reserve(self, institution, provider, model, input_tokens, output_tokens):
        record = {"institution": institution, "provider": provider, "model": model}
        self.calls.append(("reserve", record))
        return record

    async def settle(self, reservation, input_tokens, output_tokens):
        self.calls.append(("settle", reservation, input_tokens, output_tokens))

    async def release(self, reservation):
        self.calls.append(("release", reservation))


class FakeProvider:
    def __init__(self, name, chunks=None, error=None):
        self.provider_name = name
        self.settings = SimpleNamespace(groq_model="groq-test", gemini_model="gemini-test")
        self.chunks = chunks or []
        self.error = error
        self.calls = 0

    async def stream_completion(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk


def request(tier="green"):
    return AITutorRequest(
        request_id="request-1", institution_id="school-1", conversation_id="chat-1",
        messages=[TutorMessage(role="user", content="Explain fractions")],
        policy_tier=tier, instruction_profile="direct", policy_version="v1",
        system_instruction="Follow assignment policy.",
    )


def chunk(provider, text):
    return StreamChunk(request_id="request-1", delta=text, provider=provider,
                       model=f"{provider}-test")


async def collect(service, req, selected="groq"):
    return [item async for item in service.generate_multilingual_guidance(req, selected)]


def service(providers, redis=None, meter=None):
    return MultilingualGuidanceService(providers, redis or FakeRedis(), meter or FakeMeter())


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["yellow", "green"])
async def test_allowed_policy_tiers_stream_and_meter(tier):
    redis, meter = FakeRedis(), FakeMeter()
    groq = FakeProvider("groq", [chunk("groq", "Answer")])
    result = await collect(service({"groq": groq}, redis, meter), request(tier))
    assert "".join(item.delta for item in result) == "Answer"
    assert [call[0] for call in meter.calls] == ["reserve", "settle"]


def test_red_policy_tier_denies_provider_access():
    guard = PolicyGuardEngine(
        {"policy_tier": PolicyLevel.RED},
        {PolicyLevel.RED: {"log_required": True}},
    )
    decision = guard.validate_request("Do my assignment")
    assert decision["allowed"] is False
    assert decision["system_instruction"] == ""


@pytest.mark.asyncio
async def test_repeated_query_uses_tenant_scoped_response_cache():
    redis, meter = FakeRedis(), FakeMeter()
    groq = FakeProvider("groq", [chunk("groq", "Cached answer")])
    svc = service({"groq": groq}, redis, meter)
    first = await collect(svc, request())
    second_request = request()
    second_request.conversation_id = "chat-2"
    second = await collect(svc, second_request)
    assert "".join(item.delta for item in first) == "Cached answer"
    assert "".join(item.delta for item in second) == "Cached answer"
    assert groq.calls == 1
    assert len([call for call in meter.calls if call[0] == "reserve"]) == 1


@pytest.mark.asyncio
async def test_groq_outage_falls_back_to_gemini_and_settles_actual_provider():
    redis, meter = FakeRedis(), FakeMeter()
    groq = FakeProvider("groq", error=ProviderUnavailableError("offline"))
    gemini = FakeProvider("gemini", [chunk("gemini", "Backup")])
    svc = service({"groq": groq, "gemini": gemini}, redis, meter)
    result = await collect(svc, request())
    assert "".join(item.delta for item in result) == "Backup"
    assert [call[0] for call in meter.calls] == ["reserve", "release", "reserve", "settle"]
    assert meter.calls[-1][1]["provider"] == "gemini"


@pytest.mark.asyncio
async def test_no_fallback_after_partial_output_and_reservation_is_settled():
    async def broken_stream(request):
        yield chunk("groq", "partial")
        raise ProviderUnavailableError("connection lost")

    redis, meter = FakeRedis(), FakeMeter()
    groq = FakeProvider("groq")
    groq.stream_completion = broken_stream
    gemini = FakeProvider("gemini", [chunk("gemini", "should not run")])
    svc = service({"groq": groq, "gemini": gemini}, redis, meter)
    with pytest.raises(ProviderUnavailableError):
        await collect(svc, request())
    assert gemini.calls == 0
    assert meter.calls[-1][0] == "settle"


@pytest.mark.asyncio
async def test_stream_logging_is_scheduled_outside_completion_path():
    redis = FakeRedis()
    svc = service({"groq": FakeProvider("groq", [chunk("groq", "OK")])}, redis)
    await collect(svc, request())
    await asyncio.gather(*svc._log_tasks)
    assert redis.events
    assert all(stream == "resonanceai:logs:v1" for stream, _, _ in redis.events)


@pytest.mark.asyncio
async def test_guidance_persists_only_approved_messages_and_run_metadata():
    class Persistence:
        store = SimpleNamespace(institution_id="school-1")

        def __init__(self):
            self.events = []

        async def ensure_conversation(self, conversation_id, **kwargs):
            self.events.append(("conversation", conversation_id, kwargs))

        async def append_message(self, conversation_id, **kwargs):
            self.events.append(("message", conversation_id, kwargs))

        async def find_model_version_id(self, model_name, version):
            self.events.append(("model_lookup", model_name, version))
            return None

        async def start_run(self, conversation_id, **kwargs):
            self.events.append(("start", conversation_id, kwargs))
            return "run-1"

        async def finish_run(self, run_id, **kwargs):
            self.events.append(("finish", run_id, kwargs))

    persistence = Persistence()
    provider = FakeProvider("groq", [chunk("groq", "Visible answer")])
    svc = MultilingualGuidanceService(
        {"groq": provider}, FakeRedis(), FakeMeter(), persistence=persistence
    )
    chunks = await collect(svc, request())
    assert "".join(item.delta for item in chunks) == "Visible answer"
    messages = [event for event in persistence.events if event[0] == "message"]
    assert [event[2]["role"] for event in messages] == ["user", "assistant"]
    assert all("system" not in event[2]["content"] for event in messages)
    assert any(event[0] == "finish" for event in persistence.events)


@pytest.mark.asyncio
async def test_cache_and_logging_failures_do_not_fail_student_response():
    class UnavailableRedis(FakeRedis):
        async def get(self, key):
            raise OSError("cache down")

        async def set(self, *args, **kwargs):
            raise OSError("cache down")

        async def xadd(self, *args, **kwargs):
            raise OSError("stream down")

    redis = UnavailableRedis()
    svc = service({"groq": FakeProvider("groq", [chunk("groq", "Still answers")])}, redis)
    result = await collect(svc, request())
    await asyncio.gather(*svc._log_tasks)
    assert "".join(item.delta for item in result) == "Still answers"
