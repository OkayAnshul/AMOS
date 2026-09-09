"""Telemetry safety.

The interesting tests here are not "does a span get created" — they are the two
that stop tracing becoming a data-handling incident or an outage:

1. user content does not leave the process by default
2. unbounded values never become metric labels
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from amos.config import Settings
from amos.telemetry.metrics import ALLOWED_LABELS, safe_labels
from amos.telemetry.tracing import configure_tracing, describe_goal, safe_attributes, span

#: OpenTelemetry allows `set_tracer_provider` **once per process** — later calls
#: are ignored with a warning, not an error. A per-test provider therefore works
#: for the first test and silently records nothing for every one after it, which
#: presents as "no spans captured" rather than "your fixture is wrong".
#:
#: So: one provider for the module, and the exporter is cleared per test.
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))


@pytest.fixture
def captured() -> InMemorySpanExporter:
    from opentelemetry import trace

    trace.set_tracer_provider(_PROVIDER)  # no-op after the first call
    _EXPORTER.clear()
    return _EXPORTER


# ---------- content safety ----------


def test_goal_text_is_not_recorded_by_default() -> None:
    """A goal is user content. It can contain names, pasted credentials, or a
    whole document — and spans are shipped, stored and searchable."""
    attributes = describe_goal("my password is hunter2", include_content=False)

    assert attributes == {"amos.goal.length": 22}
    assert "hunter2" not in str(attributes)


def test_goal_text_is_recorded_only_when_explicitly_enabled() -> None:
    attributes = describe_goal("some goal", include_content=True)
    assert attributes["amos.goal.text"] == "some goal"


def test_recorded_goal_text_is_truncated() -> None:
    attributes = describe_goal("x" * 5000, include_content=True)
    assert len(attributes["amos.goal.text"]) == 1000


def test_content_tracing_is_off_by_default_in_settings() -> None:
    """The default must be the safe one — an opt-out would ship user content
    from every deployment that forgot to configure it."""
    assert Settings(_env_file=None).trace_content is False


@pytest.mark.parametrize(
    "key", ["api_key", "API_KEY", "password", "token", "authorization", "secret"]
)
def test_sensitive_attributes_are_dropped(key: str) -> None:
    """Belt and braces alongside the content flag: a helper that silently drops
    known-sensitive keys is more reliable than remembering not to pass them."""
    assert safe_attributes({key: "value", "safe": "kept"}) == {"safe": "kept"}


def test_none_values_are_dropped() -> None:
    """OpenTelemetry rejects None, and a span that fails to record because one
    optional field was absent is worse than a span missing that field."""
    assert safe_attributes({"a": None, "b": 1}) == {"b": 1}


# ---------- cardinality ----------


@pytest.mark.parametrize("label", ["run_id", "goal", "user", "request_id", "error_message"])
def test_unbounded_values_cannot_become_metric_labels(label: str) -> None:
    """One time series per distinct value is how a monitoring backend falls over.
    These belong on spans — one event each — never as metric dimensions."""
    assert safe_labels(**{label: "anything"}) == {}


def test_allowed_labels_come_from_a_closed_set() -> None:
    assert safe_labels(model="gemini-3.5-flash-lite", tool="calculator") == {
        "model": "gemini-3.5-flash-lite",
        "tool": "calculator",
    }
    assert {"provider", "model", "tool", "status", "agent", "outcome"} == ALLOWED_LABELS


def test_label_allowlist_is_not_a_blocklist() -> None:
    """An allowlist fails closed. A blocklist has to anticipate every unbounded
    field and fails open when it misses one — the same reasoning as http_get."""
    assert safe_labels(some_new_field_nobody_thought_of="x") == {}


# ---------- behaviour ----------


def test_a_span_is_recorded(captured: InMemorySpanExporter) -> None:
    with span("test.operation", **{"amos.thing": "value"}):
        pass

    spans = captured.get_finished_spans()
    assert [s.name for s in spans] == ["test.operation"]
    assert spans[0].attributes is not None
    assert spans[0].attributes["amos.thing"] == "value"


def test_an_exception_marks_the_span_as_an_error(captured: InMemorySpanExporter) -> None:
    """A span that ends without a status looks successful in every backend, so an
    unmarked failure is a failure you will not find."""
    from opentelemetry.trace import StatusCode

    with pytest.raises(ValueError), span("test.failing"):
        raise ValueError("boom")

    finished = captured.get_finished_spans()[0]
    assert finished.status.status_code is StatusCode.ERROR
    assert finished.events, "the exception should be recorded on the span"


def test_the_request_id_is_attached_to_spans(captured: InMemorySpanExporter) -> None:
    """The V0.1 seam: correlates traces with structured logs that predate tracing
    by eight milestones."""
    from amos.observability import set_request_id

    set_request_id("abc123def456")
    with span("test.correlated"):
        pass

    attributes = captured.get_finished_spans()[0].attributes
    assert attributes is not None
    assert attributes["amos.request_id"] == "abc123def456"


# ---------- no-op by default ----------


def test_tracing_is_disabled_without_an_endpoint() -> None:
    """Tests need no collector, and the app runs with no observability stack —
    the same principle as the optional database."""
    assert configure_tracing(Settings(_env_file=None, otlp_endpoint="")) is False


def test_spans_are_safe_to_create_when_tracing_is_disabled() -> None:
    """With no provider configured the API's no-op tracer handles this, so call
    sites need no branching."""
    with span("test.noop", **{"amos.thing": 1}):
        pass
