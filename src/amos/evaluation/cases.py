"""The golden goal set.

End-to-end goals, not component tests. Each states what a correct run looks like
in terms that are **checkable without reading the answer**: which tools should
run, which facts must appear, which sources should be cited, and whether the
honest response is a refusal.

Deliberately small. Every case costs real API calls against a 20/day quota, so a
set of 300 would be unrunnable — and a set that cannot be run is not a gate.

The composition is chosen to cover the failure modes that matter rather than to
be representative of usage:

- arithmetic — a single objectively correct answer
- retrieval — must cite, and cite the right document
- **refusal** — the corpus does not contain the answer; inventing one is the
  worst failure the system can produce and the easiest to score
- memory — must reach for the store rather than answer from context
"""

from __future__ import annotations

from amos.evaluation.metrics import GoalCase

GOLDEN_GOALS: list[GoalCase] = [
    GoalCase(
        goal="What is 17 percent of 2340?",
        expects_tools=frozenset({"calculator"}),
        expects_in_answer=("397.8",),
        note="One correct answer. Catches a model doing arithmetic in its head.",
    ),
    GoalCase(
        goal="Calculate 44% of 250 and then subtract 10.",
        expects_tools=frozenset({"calculator"}),
        expects_in_answer=("100",),
        note="Two steps; checks the tool is used more than once or composed correctly.",
    ),
    GoalCase(
        goal="According to the AMOS documentation, why was pgvector chosen over Qdrant?",
        expects_tools=frozenset({"search_knowledge"}),
        expects_citations=frozenset({"03-architecture-decisions.md"}),
        expects_in_answer=("transaction",),
        note="Must retrieve and cite the right ADR, not answer from model knowledge.",
    ),
    GoalCase(
        goal="What does the AMOS documentation say about the daily request quota?",
        expects_tools=frozenset({"search_knowledge"}),
        expects_in_answer=("20",),
        note="A specific number that exists in the corpus and nowhere in training data.",
    ),
    GoalCase(
        goal=(
            "According to the AMOS documentation, what is AMOS's Kubernetes "
            "autoscaling policy and what are its configured replica counts?"
        ),
        expects_tools=frozenset({"search_knowledge"}),
        expects_refusal=True,
        note=(
            "THE most important case. The corpus says Kubernetes is not used. A "
            "confident invented answer here is the failure RAG exists to prevent, "
            "and the one a user is least able to detect."
        ),
    ),
    GoalCase(
        goal="What does the documentation say about how retries avoid a thundering herd?",
        expects_tools=frozenset({"search_knowledge"}),
        expects_in_answer=("jitter",),
        note="Retrieval of a specific mechanism, phrased without the target word.",
    ),
]
