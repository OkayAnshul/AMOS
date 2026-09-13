# Interview — Authentication and Isolation (V1.4)

**Advance gate: nothing further begins until these can be answered unaided.**

The milestone that made AMOS exposable in principle — and still not in practice. Note what is
*not* claimed: there is no authorization, no TLS, no rate limiting, and no key rotation.

---

### Why was this last, when security is supposed to be designed in from the start?

Both halves of that are true, and the project did both.

The *controls that constrain the model* were built at V0.2 and have been there since: tool
permission allowlists, the `read_file` sandbox, the `http_get` host allowlist and SSRF checks, an
AST-walking calculator instead of `eval`. Those are the security decisions that would have been
expensive to retrofit, because they shape what the agent is allowed to be.

**Identity is different.** `user_id` on every table and a filter on every query is mechanical, and
its cost is proportional to the number of tables and queries — which only grows. Doing it once
against a schema that had stopped moving was cheaper than doing it at V0.3 and then carrying it
through six milestones of schema change.

The honest framing: `docs/13-security.md` has said since V0.2 that AMOS was not safe to expose,
and it was not exposed. The risk was managed by not deploying it, which is a legitimate control.

### This reverses a stated non-goal. Does that not make the requirements meaningless?

It would if it were done quietly. `docs/01-requirements.md` listed multi-tenancy under **explicit
non-goals** — "not deferred-and-planned; out of scope, and pretending otherwise would distort the
architecture."

So the entry was removed *with a note saying it was removed, when, and by which ADR*. A non-goal
that silently becomes a goal is how a requirements document stops being trustworthy; one that is
overturned on the record is just a decision that changed.

And the original reasoning was right for its time: building isolation before there was anything to
isolate would have distorted the architecture.

### Why API keys rather than JWT?

JWT's advantage is **stateless verification** — any service can validate a token without asking a
shared store. That is worth real complexity when many services check auth and should not share a
database.

AMOS is one process with one database (ADR-004). Statelessness buys nothing here, and costs
signing-key management and rotation. An API key looked up by hash is one indexed query in a
database the request is already going to touch.

Reconsider when a second service needs to verify identity without reaching this database.

### Why store a hash rather than the key?

So a database that leaks does not also hand over access. Lookup is by hash, so the plaintext key
exists only inside the request presenting it — it is never written to a row, a log, or a trace.

`create_user` returns the plaintext **once**, because after that it is genuinely unrecoverable.
That is also why the test fixture carries it: there is nowhere else to get it.

### What is the actual hard problem in this milestone?

**Isolation fails silently.**

A missing `WHERE user_id = ...` does not raise. It does not log. It returns *more* data rather
than less. So the failure presents as a working feature, and it is invisible in review because the
query looks like every other query.

Every other failure mode in this project announces itself — an illegal state transition raises, a
malformed plan is rejected, a tool timeout produces an outcome. This one does not, and that is
what shaped the enforcement decision.

### Where is isolation enforced, and why there?

**At construction.** `RunRepository(session, actor)` and `SemanticMemory(session, embeddings,
actor)` take the acting user as a constructor argument, and every query they build applies the
filter. There is no method callable without an owner because **there is no repository without
one**.

The alternatives and why not:

- **In each handler** — puts the guarantee in the layer most likely to be copy-pasted.
- **`user_id` as a method argument** — one forgotten argument away from a leak, and the forgotten
  version still compiles and still returns rows.
- **Postgres row-level security** — genuinely stronger, and the right answer at a different scale.
  It moves the guarantee into the database, at the cost of every query running under a session
  variable that must be set correctly on a *pooled* connection. That is a new failure mode in
  exchange for defence against a class of mistake construction-scoping already makes hard.
  Reconsider if a second service shares this database.

### A test can't prove the absence of a bug. What do the guards actually assert?

They assert the bypass **cannot be written in this module**:

- no `select(Run)` in the repository without `Run.user_id` on the same statement
- no raw SQL statement mentioning `memories` without a user filter

Both were verified by reintroducing the bypass and watching them fail — a guard nobody has seen
fail is a guard nobody knows works.

The subtle case is `recall_similar`. An **exact** lookup missing its filter returns nothing and
looks broken, so somebody notices. A **similarity** search missing its filter returns another
user's fact, ranked highly, and looks like it worked.

### What does the worker run as?

**As the run's owner**, read from the claimed row — never unscoped.

The tempting shortcut is to give the worker a privileged repository, since it legitimately
processes every user's runs. That would make the one component touching all users' data the one
component with no isolation. The claim query already returns `user_id`; the worker builds an
`Actor` from it.

### Memory tools are built once at startup and serve everyone. How do they know whose memory to use?

They look the owner up **from the run** (`auth.actor_for_run`), using the run id already in the
context.

The alternative was a second ambient value — an actor contextvar beside the run id — and ambient
identity is precisely how a query ends up scoped to whoever happened to be in the contextvar.
Deriving it from the run keeps **one** ambient value and makes the scope follow the data: a memory
written during a run belongs to whoever owns that run, which is correct on the synchronous path
and in the worker without either having to arrange it.

Cost: one indexed lookup per memory-tool call.

### Why is the corpus not isolated?

`documents.user_id` is nullable and `NULL` means the system corpus, readable by everyone.

AMOS's own documentation *is* the corpus. A private copy per user means re-embedding ~300 chunks
per user — roughly ten minutes of quota each — to isolate data that is already public in the
repository. Retrieval matches `user_id IS NULL OR user_id = :actor`, so a user's own ingested
documents would still be private.

It is the one place isolation is deliberately not total, and stating that is the difference
between a decision and an oversight.

### What happens with no database?

The API runs **unauthenticated**, as a single local user, and logs a warning at startup.

No database means no users to check against, nothing stored, and no isolation to enforce.
Requiring a key would mean the API could not run without infrastructure at all — a property held
since V0.3, with its own CI job.

The warning is a `logger.warning`, not a field in a log line, because `authenticated=false` in
structured output is easy to miss and an operator assuming V1.4's auth applies would expose an
open API.

### Why 404 rather than 403 when fetching someone else's run?

Because confirming that a run *exists* but is not yours is itself a leak. A `403` tells an
attacker their guessed id was real.

This falls out of the design rather than being special-cased: the scoped query simply does not
find the row, so the endpoint's existing "no run with that id" path handles it.

### Why one error for missing, malformed and wrong keys?

The distinction is not useful to a client, and enumerating it tells an attacker which half of
their guess was right. All three produce the same `401` with the same body — asserted by a test,
because "they happen to be the same today" is not the same as "they are the same".

### What is still missing, and what would you build next?

- **Authorization.** Auth here is *identity only*. Any authenticated user can call any endpoint;
  there are no scopes, roles or read-only keys. This is the gap most likely to be mistaken for
  solved, because "we have auth" is routinely used to mean both.
- **Key rotation.** A leaked key is full access until the user is deleted and recreated.
- **Audit of authentication attempts.** A brute-force attempt leaves no distinguishable trace.
- **TLS and inbound rate limiting** — unchanged since V0.2.

Next would be authorization, because it is the one people assume is already there.

---

## What this milestone does *not* claim

- **AMOS is still not safe to expose publicly.** Authentication is a precondition, not a
  sufficient condition, and `docs/13-security.md` keeps saying so.
- Not authorization. Identity only.
- No key rotation, no audit log of auth attempts, no TLS, no rate limiting.
- The corpus is shared by design.
- Isolation is enforced in **application code**, not by the database. Row-level security would be
  stronger and is the documented reconsider-if.
