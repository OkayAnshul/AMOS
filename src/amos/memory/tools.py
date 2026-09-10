"""Memory as tools.

Both remembering and recalling are exposed as tools rather than as implicit
behaviour, for the same reason retrieval was at V0.5: the agent should decide
when memory is relevant, and it inherits argument validation, timeouts and trace
visibility for free.

There is a subtler reason for `remember_fact` specifically. Writing to memory is
the first thing AMOS does that **persists a decision the model made** — every
earlier tool was read-only. It is deliberately kept as a `READ_LOCAL`-scoped,
bounded, single-row write rather than being given `WRITE` permission, because the
registry refuses `WRITE` until an approval workflow exists (docs/13-security.md).
That constraint is real: a fact written from a prompt-injected instruction is a
persistent lie the system will repeat later.

Mitigations actually in place, stated honestly:
  - subject and content are length-bounded
  - supersession is deterministic, so a bad fact overwrites rather than
    accumulating alongside the truth
  - superseded rows are kept, so a bad write is recoverable and inspectable
  - `memory_history` makes tampering visible

What is NOT in place: no approval step, no provenance check on the *content*. A
malicious page fetched by `http_get` could still talk the model into remembering
something false. That is a real limitation, recorded rather than papered over.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from amos.memory.episodic import EpisodicMemory
from amos.memory.semantic import SemanticMemory
from amos.observability import get_current_run_id
from amos.tools.base import Permission, Tool


class RememberArgs(BaseModel):
    subject: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "A short stable key for what this fact is about, e.g. "
            "'user_preferred_language' or 'project_deadline'."
        ),
    )
    content: str = Field(
        min_length=1,
        max_length=2000,
        description="The fact itself, stated plainly and self-contained.",
    )


class RememberFactTool(Tool):
    name: ClassVar[str] = "remember_fact"
    description: ClassVar[str] = (
        "Store a durable fact about the user or their project, so it is available "
        "in later sessions. Use for stated preferences, names, deadlines and "
        "decisions — not for transient details of the current task. Storing a fact "
        "for a subject that already has one REPLACES it."
    )
    input_schema: ClassVar[type[BaseModel]] = RememberArgs
    permission: ClassVar[Permission] = Permission.READ_LOCAL
    timeout_seconds: ClassVar[float] = 30.0

    def __init__(self, factory: Any, embeddings: Any, run_id: uuid.UUID | None = None) -> None:
        self._factory = factory
        self._embeddings = embeddings
        self._run_id = run_id

    def _source_run_id(self) -> uuid.UUID | None:
        """Provenance for this fact.

        The tool is built once at startup, before any run exists, so the run id
        cannot be a constructor argument. It comes from a contextvar set by
        RunService — the same pattern as the request id threaded since V0.1.
        """
        if self._run_id is not None:
            return self._run_id
        current = get_current_run_id()
        if current is None:
            return None
        try:
            return uuid.UUID(current)
        except ValueError:
            return None

    async def _run(self, args: RememberArgs) -> dict[str, Any]:
        from amos.database.engine import session_scope

        async with session_scope(self._factory) as session:
            memory = SemanticMemory(session, self._embeddings)
            previous = await memory.recall_exact(args.subject)
            fact = await memory.remember(
                args.subject, args.content, source_run_id=self._source_run_id()
            )

        return {
            "stored": True,
            "subject": fact.subject,
            "content": fact.content,
            "replaced": previous.content if previous else None,
        }


class RecallArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=300,
        description=(
            "What you want to remember. Use the exact subject key if you know it, "
            "otherwise describe it and similar facts will be returned."
        ),
    )


class RecallFactsTool(Tool):
    name: ClassVar[str] = "recall_facts"
    description: ClassVar[str] = (
        "Recall facts THE USER TOLD YOU in earlier sessions - their preferences, "
        "names, decisions and deadlines. Use whenever a question is about the user "
        "themselves or something they stated. This does not search documentation; "
        "that is search_knowledge."
    )
    input_schema: ClassVar[type[BaseModel]] = RecallArgs
    permission: ClassVar[Permission] = Permission.READ_LOCAL
    timeout_seconds: ClassVar[float] = 30.0

    def __init__(self, factory: Any, embeddings: Any) -> None:
        self._factory = factory
        self._embeddings = embeddings

    async def _run(self, args: RecallArgs) -> dict[str, Any]:
        from amos.database.engine import session_scope

        async with session_scope(self._factory) as session:
            memory = SemanticMemory(session, self._embeddings)

            # Exact first, always. If the caller knows the key, a ranking is the
            # wrong answer — it can return a similar fact about someone else.
            exact = await memory.recall_exact(args.query)
            if exact is not None:
                return {
                    "found": 1,
                    "match": "exact",
                    "facts": [{"subject": exact.subject, "content": exact.content}],
                }

            similar = await memory.recall_similar(args.query)

        if not similar:
            return {
                "found": 0,
                "match": "none",
                "facts": [],
                "instruction": (
                    "Nothing relevant is remembered. Do not invent a remembered "
                    "fact; say you do not have this stored, or ask the user."
                ),
            }
        return {
            "found": len(similar),
            "match": "similar",
            "facts": [
                {
                    "subject": f.subject,
                    "content": f.content,
                    "score": round(f.score or 0.0, 4),
                }
                for f in similar
            ],
        }


class PastRunsArgs(BaseModel):
    goal: str = Field(min_length=1, max_length=500, description="The goal to compare against.")


class RecallPastRunsTool(Tool):
    name: ClassVar[str] = "recall_past_runs"
    description: ClassVar[str] = (
        "Find previous runs with similar goals and how they turned out. Use before "
        "starting substantial work, to reuse what worked and avoid what failed."
    )
    input_schema: ClassVar[type[BaseModel]] = PastRunsArgs
    permission: ClassVar[Permission] = Permission.READ_LOCAL
    timeout_seconds: ClassVar[float] = 30.0

    def __init__(self, factory: Any, embeddings: Any, run_id: uuid.UUID | None = None) -> None:
        self._factory = factory
        self._embeddings = embeddings
        self._run_id = run_id

    async def _run(self, args: PastRunsArgs) -> dict[str, Any]:
        from amos.database.engine import session_scope

        async with session_scope(self._factory) as session:
            episodes = await EpisodicMemory(session, self._embeddings).recall_similar(
                args.goal, exclude_run_id=self._run_id
            )

        return {
            "found": len(episodes),
            "episodes": [
                {
                    "goal": e.goal,
                    "outcome": e.status,
                    "lesson": e.lesson,
                    "tokens_used": e.total_tokens,
                    "similarity": round(e.score or 0.0, 4),
                }
                for e in episodes
            ],
        }
