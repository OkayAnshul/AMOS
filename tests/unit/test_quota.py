"""V1.5: a daily quota fails fast; a per-minute one is still waited out.

The 429 bodies are recorded fixtures (`tests/fixtures/gemini/`), raised as real
`google.genai.errors.ClientError`s from a patched client — no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.genai import errors as genai_errors

from amos.errors import ProviderRateLimitError
from amos.llm.gemini import GeminiProvider
from amos.llm.quota import quota_ids, quota_scope
from amos.rag import embeddings as embeddings_module
from amos.rag.embeddings import GeminiEmbeddings

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gemini"


def _body(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


def _client_error(name: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(429, _body(name))


# ---------- the classifier ----------


@pytest.mark.parametrize(
    ("fixture", "scope"),
    [
        ("429_embed_per_day.json", "day"),
        ("429_embed_per_minute.json", "minute"),
        ("429_generate_per_day.json", "day"),
    ],
)
def test_scope_is_read_from_the_quota_id(fixture: str, scope: str) -> None:
    assert quota_scope(_body(fixture)) == scope


def test_the_daily_fixture_carries_a_retry_delay_too() -> None:
    """The reason the delay cannot be the signal: a daily limit has one as well.

    The real capture says ~74000s; the 2026-09-14 log said 57s for the same limit.
    """
    details = _body("429_embed_per_day.json")["error"]["details"]  # type: ignore[index]
    retry = [d for d in details if d["@type"].endswith("RetryInfo")]
    assert retry and retry[0]["retryDelay"].endswith("s")


def test_a_daily_violation_wins_over_a_per_minute_one() -> None:
    body = _body("429_embed_per_minute.json")
    daily = _body("429_embed_per_day.json")
    body["error"]["details"][1]["violations"] += daily["error"]["details"][1]["violations"]  # type: ignore[index]
    assert quota_scope(body) == "day"


@pytest.mark.parametrize(
    "body",
    [
        None,
        "text",
        [],
        {},
        {"error": {}},
        {"error": {"details": "x"}},
        [{"error": {"details": []}}],
    ],
)
def test_unrecognisable_bodies_have_no_scope(body: object) -> None:
    assert quota_ids(body) == []
    assert quota_scope(body) is None


def test_a_single_element_list_body_is_unwrapped() -> None:
    """The SDK unwraps `[body]` itself; the classifier does not depend on that."""
    assert quota_scope([_body("429_embed_per_day.json")]) == "day"


# ---------- the embedding retry ----------


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _embeddings_raising(
    monkeypatch: pytest.MonkeyPatch, fixture: str, *, retries: int = 3
) -> tuple[GeminiEmbeddings, _Sleeps, list[int]]:
    emb = GeminiEmbeddings(api_key="test-key", max_rate_limit_retries=retries)
    attempts: list[int] = []

    async def always_429(**_: object) -> object:
        attempts.append(1)
        raise _client_error(fixture)

    monkeypatch.setattr(emb._client.aio.models, "embed_content", always_429)
    sleeps = _Sleeps()
    monkeypatch.setattr(embeddings_module.asyncio, "sleep", sleeps)
    return emb, sleeps, attempts


async def test_a_daily_limit_fails_on_the_first_attempt_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emb, sleeps, attempts = _embeddings_raising(monkeypatch, "429_embed_per_day.json")
    with pytest.raises(ProviderRateLimitError) as raised:
        await emb.embed_query("anything")
    assert raised.value.details["quota_scope"] == "day"
    assert len(attempts) == 1
    assert sleeps.calls == []


async def test_a_per_minute_limit_is_still_waited_out(monkeypatch: pytest.MonkeyPatch) -> None:
    emb, sleeps, attempts = _embeddings_raising(monkeypatch, "429_embed_per_minute.json", retries=3)
    with pytest.raises(ProviderRateLimitError) as raised:
        await emb.embed_query("anything")
    assert raised.value.details["quota_scope"] == "minute"
    assert len(attempts) == 4
    assert sleeps.calls == [14.0, 14.0, 14.0]  # the API's 12s, plus the 2s margin


# ---------- generation ----------


def test_a_generation_429_carries_its_scope() -> None:
    error = GeminiProvider._translate_client_error(_client_error("429_generate_per_day.json"))
    assert isinstance(error, ProviderRateLimitError)
    assert error.details == {"status": 429, "quota_scope": "day"}
    assert "(day limit)" in str(error)
