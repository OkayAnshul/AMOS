# 00 — Vision

## The problem

A single LLM call is a poor fit for goals that need several steps, external information, and
verification. Ask a chatbot to "compare two job-queue designs and recommend one for my project"
and you get one pass of plausible text: no decomposition, no retrieval of what your project
actually is, no check that the claims hold, no record of how it got there, and no recovery when
part of it fails.

The gap is not model quality. It is that there is no **system** around the model — no task
state, no memory, no validation, no trace.

## What AMOS is

A platform that accepts a goal and runs it to completion: decomposes it into tasks, routes them
to specialised agents, executes tools, retrieves relevant knowledge, keeps memory across
sessions, validates results, and recovers from failure — while recording enough that you can
answer "what exactly happened?" afterwards.

AMOS is **domain-general** by design. It is infrastructure for goal completion, not a product
for one vertical.

## What AMOS is not

- Not a chatbot wrapper. A single-shot LLM call is the baseline AMOS must beat.
- Not an autonomy demo. Irreversible actions stay behind validation, permissions and human
  approval (see `docs/13-security.md` when written at V0.2).
- Not a technology showcase. Every dependency needs an ADR. A simple system that works beats a
  complicated one that does not.

## The honest risk

A domain-general platform with no specific user has a well-known failure mode: it becomes
demo-ware — impressive architecture, nothing anyone uses, and no forcing function to make it
correct.

Mitigation, applied from V0.1 onward: a **fixed demo-goal set** and a **seed corpus** (the
project's own documentation and Anshul's prior project docs). Every milestone must run the
demo goals successfully. When a goal set stops being satisfiable, that is a real regression,
not a cosmetic one. This substitutes for real users; it is not as good as real users, and that
should be stated rather than papered over.

## Principles

1. **LLMs handle uncertainty; software handles guarantees.** The model interprets ambiguity,
   decomposes goals, and selects tools. Code owns state, permissions, validation, retries,
   timeouts, persistence and security. The model never decides whether a task is complete.
2. **Every milestone is a working project.** No ten-module march to a system that only runs at
   the end.
3. **Nothing is claimed without evidence.** A capability is real when a file, a test and a demo
   exist for it.
4. **Explainability is a feature.** If a design cannot be explained to a senior engineer six
   months from now, it gets documented, simplified, or removed.

## Success

AMOS succeeds if it runs; if its boundaries are coherent; if agents do meaningful reasoning and
tool selection; if failures are handled deliberately rather than accidentally; if retrieval is
measurable rather than asserted; if any run can be inspected end to end; and if Anshul can
defend every decision in it.

The goal is not "an AI built AMOS". It is: **built with an AI pair-programmer, understood
deeply enough to modify, debug, explain and extend independently.**
