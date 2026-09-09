"""Metrics.

Deliberately a small, closed set. Every label here comes from a bounded domain —
a model name, a tool name, a status — because **an unbounded label creates one
time series per distinct value**, and that is the standard way to take down a
monitoring backend.

Run ids, goal text, user input and error messages are therefore *span* attributes
(one event each) and never metric labels.

Like tracing, this is a no-op without a configured endpoint.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from amos.config import Settings

_METER_NAME = "amos"

#: Labels permitted on metrics. Anything not listed is dropped rather than
#: trusted — a new label with unbounded values is the failure this guards.
ALLOWED_LABELS = frozenset({"provider", "model", "tool", "status", "agent", "outcome"})


class _Instruments:
    """Created lazily so importing this module never starts a meter provider."""

    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)
        self.llm_calls = meter.create_counter(
            "amos.llm.calls", description="LLM calls made", unit="1"
        )
        self.llm_tokens = meter.create_counter(
            "amos.llm.tokens", description="Tokens consumed", unit="1"
        )
        self.tool_calls = meter.create_counter(
            "amos.tool.calls", description="Tool invocations", unit="1"
        )
        self.runs = meter.create_counter("amos.runs", description="Runs by outcome", unit="1")
        self.run_duration = meter.create_histogram(
            "amos.run.duration", description="Run wall time", unit="ms"
        )
        self.retries = meter.create_counter(
            "amos.task.retries", description="Task retries", unit="1"
        )


_instruments: _Instruments | None = None


def configure_metrics(settings: Settings) -> bool:
    global _instruments
    if not settings.otlp_endpoint:
        return False

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    provider = MeterProvider(
        resource=Resource.create({"service.name": settings.otel_service_name}),
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=settings.otlp_endpoint.replace("/traces", "/metrics"))
            )
        ],
    )
    metrics.set_meter_provider(provider)
    _instruments = _Instruments()
    return True


def instruments() -> _Instruments:
    global _instruments
    if _instruments is None:
        # The API's default no-op meter, so callers need no branching.
        _instruments = _Instruments()
    return _instruments


def safe_labels(**labels: Any) -> dict[str, Any]:
    """Keep only allowlisted, low-cardinality labels.

    An allowlist rather than a blocklist, for the same reason `http_get` uses one:
    a blocklist has to anticipate every unbounded field and fails open when it
    misses one.
    """
    return {
        key: str(value)
        for key, value in labels.items()
        if key in ALLOWED_LABELS and value is not None
    }
