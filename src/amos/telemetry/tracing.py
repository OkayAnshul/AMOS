"""OpenTelemetry tracing.

## Why this is cheap to add now

The request id has been threaded through every log line since V0.1, for
debugging. That was the seam; this is what it was for. Correlation was already
solved — this milestone adds structure and a standard wire format on top of it.

## Two rules that keep tracing useful rather than expensive

**1. Goal text is never a span attribute by default.**

A goal is user content. It can contain names, credentials someone pasted, or a
whole document. Span attributes are shipped to a collector, stored, and often
searchable — putting user input there by default is a data-handling decision
disguised as a debugging convenience. `AMOS_TRACE_CONTENT=true` opts in for local
debugging; the default records only a length.

**2. High-cardinality values go on spans, never on metric labels.**

A span attribute holding a run id is fine — spans are individual events. The same
value as a *metric* label creates one time series per run, which is how
monitoring backends fall over. The rule of thumb this module follows: metric
labels must come from a small, closed set (a model name, a tool name, a status),
and anything unbounded belongs on a span.

## No-op by default

Tracing is off unless `AMOS_OTLP_ENDPOINT` is set. The OpenTelemetry API is
designed for exactly this: with no provider configured, every span call is a
cheap no-op. So tests need no collector and the app needs no observability stack
to run — the same principle as the optional database.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from amos.config import Settings
from amos.observability import get_request_id

logger = logging.getLogger(__name__)

_TRACER_NAME = "amos"

#: Attributes that must never be set, whatever a caller passes. Belt and braces
#: alongside the content flag — a helper that silently drops known-sensitive keys
#: is more reliable than remembering not to pass them.
_FORBIDDEN_ATTRIBUTES = frozenset(
    {"api_key", "gemini_api_key", "password", "token", "authorization", "secret"}
)


def configure_tracing(settings: Settings) -> bool:
    """Set up the tracer provider. Returns whether tracing is active.

    Without an endpoint this does nothing, and every later span call becomes a
    no-op through the OpenTelemetry API's default provider.
    """
    if not settings.otlp_endpoint:
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment": settings.env,
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    logger.info("tracing enabled: %s", settings.otlp_endpoint)
    return True


def tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def safe_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Drop forbidden keys and None values.

    None is dropped because OpenTelemetry rejects it, and a span that fails to
    record because one optional field was absent is worse than a span missing
    that field.
    """
    return {
        key: value
        for key, value in attributes.items()
        if value is not None and key.lower() not in _FORBIDDEN_ATTRIBUTES
    }


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Start a span, recording exceptions and setting an error status.

    A span that ends without a status looks successful in every backend, so a
    failure that was not explicitly marked is a failure you will not find.
    """
    with tracer().start_as_current_span(name) as current:
        for key, value in safe_attributes(attributes).items():
            current.set_attribute(key, value)
        if (request_id := get_request_id()) is not None:
            # The V0.1 seam, now a span attribute: it correlates traces with the
            # structured logs that predate tracing by eight milestones.
            current.set_attribute("amos.request_id", request_id)
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def record_llm_call(
    current: Span,
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    output_tokens: int,
    latency_ms: int,
) -> None:
    """Attach LLM cost to a span.

    Model and provider are low cardinality and safe as metric dimensions too.
    Token counts are values, not dimensions — they are summed, never grouped by.
    """
    # Names follow the OpenTelemetry GenAI semantic conventions where they exist,
    # so a standard backend understands them without custom dashboards.
    current.set_attribute("gen_ai.system", provider)
    current.set_attribute("gen_ai.request.model", model)
    current.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
    current.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    current.set_attribute("amos.latency_ms", latency_ms)


def describe_goal(goal: str, include_content: bool) -> dict[str, Any]:
    """What may be recorded about a goal.

    Length always; the text only when explicitly opted in. A goal is user content
    and may contain anything the user pasted.
    """
    attributes: dict[str, Any] = {"amos.goal.length": len(goal)}
    if include_content:
        attributes["amos.goal.text"] = goal[:1000]
    return attributes
