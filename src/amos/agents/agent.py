"""The grounded agent: one LLM call, validated, with bounded repair.

## Why a repair loop when the provider enforces the schema?

Gemini's structured output makes conforming JSON very likely — not certain. It
can still fail to produce a usable object when:

  - the response is truncated mid-JSON (finish_reason MAX_TOKENS)
  - generation stops for safety or recitation reasons
  - the schema is expressible in JSON Schema but the model fills it with values
    that violate a Pydantic constraint the provider did not enforce

Unvalidated model output must never become control flow (requirement N-1), so the
loop is defensive by design. How often it actually fires is an open question,
queued as an experiment in engineering/experiments-log.md — `repair_count` is
recorded on every result so the answer comes from data rather than assumption.
"""

from __future__ import annotations

import logging
import time

from pydantic import ValidationError

from amos.agents.schemas import AgentResponse, AgentResult
from amos.errors import OutputValidationError
from amos.llm.base import LLMCallRecord, LLMProvider, LLMRequest
from amos.observability import get_request_id, log_event, new_request_id

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are AMOS, a precise reasoning agent.

Rules:
- Answer the goal directly. Do not pad.
- State every assumption you had to make because the goal was underspecified.
- Set confidence honestly. Low confidence with stated caveats is far better than
  a confident answer you cannot support.
- If you do not know something, say so in caveats rather than inventing it.
"""

REPAIR_INSTRUCTION = """Your previous response could not be parsed into the required schema.

Error: {error}

Return ONLY a valid JSON object matching the schema. No prose, no markdown fence."""


class GroundedAgent:
    """Turns a goal into a validated AgentResponse.

    Holds no state between calls — everything about one execution lives in the
    returned AgentResult. That is what lets V0.8 run many of these concurrently
    without sharing anything.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        timeout: float = 30.0,
        max_repair_attempts: int = 2,
        temperature: float = 0.2,
    ) -> None:
        self._provider = provider
        self._timeout = timeout
        self._max_repair_attempts = max_repair_attempts
        self._temperature = temperature

    @property
    def tool_names(self) -> list[str]:
        """No tools by design. Present so both agents satisfy the shape the API
        layer depends on, rather than the API special-casing agent types."""
        return []

    async def run(self, goal: str) -> AgentResult:
        request_id = get_request_id() or new_request_id()
        started = time.perf_counter()

        calls: list[LLMCallRecord] = []
        prompt = goal
        last_error = ""

        # attempt 0 is the real try; 1..N are repairs
        for attempt in range(self._max_repair_attempts + 1):
            system = SYSTEM_INSTRUCTION
            if attempt > 0:
                system = SYSTEM_INSTRUCTION + "\n\n" + REPAIR_INSTRUCTION.format(error=last_error)

            request = LLMRequest(
                prompt=prompt,
                system_instruction=system,
                response_schema=AgentResponse,
                temperature=self._temperature,
            )

            response = await self._provider.complete(request, timeout=self._timeout)

            record = LLMCallRecord(
                provider=response.provider,
                model=response.model,
                prompt_tokens=response.prompt_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
                repair_attempt=attempt,
            )

            validated, error = self._validate(response.parsed, response.text)
            if validated is not None:
                calls.append(record)
                total_ms = int((time.perf_counter() - started) * 1000)
                log_event(
                    logger,
                    "agent.completed",
                    model=response.model,
                    provider=response.provider,
                    repair_count=attempt,
                    total_tokens=sum(c.prompt_tokens + c.output_tokens for c in calls),
                    latency_ms=total_ms,
                    confidence=validated.confidence.value,
                )
                return AgentResult(
                    request_id=request_id,
                    response=validated,
                    llm_calls=calls,
                    repair_count=attempt,
                    total_tokens=sum(c.prompt_tokens + c.output_tokens for c in calls),
                    latency_ms=total_ms,
                )

            last_error = error
            record.error = error
            calls.append(record)
            log_event(
                logger,
                "agent.output_invalid",
                attempt=attempt,
                error=error,
                will_retry=attempt < self._max_repair_attempts,
            )

        raise OutputValidationError(
            f"Model output failed validation after {self._max_repair_attempts + 1} attempts",
            attempts=self._max_repair_attempts + 1,
            last_error=last_error,
            details={
                "request_id": request_id,
                "total_tokens": sum(c.prompt_tokens + c.output_tokens for c in calls),
            },
        )

    @staticmethod
    def _validate(parsed: object | None, raw_text: str) -> tuple[AgentResponse | None, str]:
        """Validate provider output, whether or not the provider pre-parsed it.

        Returns (response, "") on success or (None, error_message) on failure.
        Never raises: the caller decides whether to repair or give up.
        """
        if isinstance(parsed, AgentResponse):
            return parsed, ""

        if parsed is not None:
            # Provider parsed into something, but not our type. Re-validate.
            try:
                dumped = (
                    parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)  # type: ignore[call-overload]
                )
                return AgentResponse.model_validate(dumped), ""
            except (ValidationError, TypeError, ValueError) as exc:
                return None, str(exc)[:500]

        if not raw_text.strip():
            return None, "Model returned an empty response"

        try:
            return AgentResponse.model_validate_json(raw_text), ""
        except ValidationError as exc:
            return None, str(exc)[:500]
