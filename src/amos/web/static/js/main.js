// AMOS mission control. Plain ES modules, no framework, no build step (ADR-017).
// Everything shown comes from the read API (V1.7); a run is watched by polling
// the whole stored record about once a second (ADR-015).

import { Api, ApiError, explain, forgetKey, keyIsRemembered, loadKey, saveKey } from "./api.js";
import { ago, capitalise, clear, clock, count, h, humanSubject, icon, markdown, ms, s } from "./dom.js";
import { LANES, buildModel, emptyModel, laneColor, render as renderLanes } from "./lanes.js";

const api = new Api(() => state.key);
const content = document.getElementById("content");
const inspector = document.getElementById("inspector");
const inspectorTitle = document.getElementById("inspector-title");
const inspectorBody = document.getElementById("inspector-body");

const TERMINAL = new Set(["COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "DEAD_LETTER"]);
const MAC = /Mac|iPhone|iPad/.test(navigator.platform);
const MOD = MAC ? "⌘" : "Ctrl";

const PRESETS = [
  ["Percent, then subtract", "Calculate 44% of 250 and then subtract 10."],
  ["Why pgvector", "According to the AMOS documentation, why was pgvector chosen over Qdrant?"],
  ["Thundering herd", "What does the documentation say about how retries avoid a thundering herd?"],
  [
    "Look up, then compute",
    "Look up the daily request quota for gemini-3.5-flash in the docs, then calculate how many 5-call goals fit in a week.",
  ],
  ["An honest refusal", "How is AMOS deployed on Kubernetes?"],
  ["Remember a fact", "Remember that my project review is on Friday."],
  ["Recall it", "When is my project review?"],
];

const OPTION_LABELS = {
  planning: "Planning",
  multi_agent: "Several agents",
  critic: "Critic",
  delegation: "Delegation",
  memory: "Memory",
};

const SECTIONS = [
  ["run", "Run", "run"],
  ["history", "History", "history"],
  ["memory", "Memory", "memory"],
  ["knowledge", "Knowledge", "knowledge"],
  ["agents", "Agents and tools", "agents"],
  null,
  ["system", "System", "system"],
  ["insights", "Insights", "insights"],
  ["dead", "Dead letters", "dead"],
  ["settings", "Settings", "settings"],
];

const PURPOSE = {
  planner: "Planner",
  router: "Router",
  agent: "Agent",
  revision: "Revision",
  critic: "Critic",
  synthesis: "Combine",
  delegate: "Delegate",
  reconciler: "Memory check",
};

const state = {
  key: loadKey(),
  system: null,
  route: "run",
  runId: null,
  trace: null,
  watching: null,
  pending: null,
  error: null,
  seen: new Set(),
  selected: null,
  draft: "",
  options: {},
  mode: "sync",
};

// ---------- small pieces ----------

function toast(message) {
  const el = h("div", { class: "toast" }, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), 3200);
}

function statusOf(status) {
  switch (status) {
    case "COMPLETED":
      return { cls: "ok", icon: "check", text: "Completed" };
    case "PARTIALLY_COMPLETED":
      return { cls: "warn", icon: "half", text: "Partly completed" };
    case "FAILED":
      return { cls: "fail", icon: "cross", text: "Failed" };
    case "DEAD_LETTER":
      return { cls: "fail", icon: "tray", text: "Given up" };
    case "RUNNING":
      return { cls: "live", icon: "spinner", text: "Running" };
    case "QUEUED":
      return { cls: "idle", icon: "clock", text: "Waiting for a worker" };
    default:
      return { cls: "live", icon: "spinner", text: "Starting" };
  }
}

function statusBadge(status) {
  const st = statusOf(status);
  return h("span", { class: `status ${st.cls}` }, icon(st.icon), st.text);
}

function statusIcon(status) {
  const st = statusOf(status);
  return h("span", { class: `status ${st.cls}`, title: st.text }, icon(st.icon), h("span", { class: "visually-hidden" }, st.text));
}

function section(title, ...children) {
  return [h("h2", { class: "section-title" }, title), ...children];
}

function row(label, value, sub) {
  return h(
    "div",
    { class: "row" },
    h("div", { class: "row-label" }, h("div", null, label), sub ? h("div", { class: "row-sub" }, sub) : null),
    value === undefined ? null : h("div", { class: "row-value" }, value),
  );
}

function empty(title, text) {
  return h("div", { class: "empty" }, h("div", { class: "empty-title" }, title), text ? h("div", null, text) : null);
}

function errorBanner(error, warn = false) {
  const { title, detail } = explain(error);
  return h(
    "div",
    { class: `banner${warn ? " warn" : ""}`, role: "alert" },
    h("div", { class: "banner-title" }, title),
    h("div", { class: "small" }, detail),
  );
}

function page(title, lede, ...children) {
  return h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "page-head" },
      h("div", null, h("h1", { class: "large-title" }, title), lede ? h("p", { class: "page-lede" }, lede) : null),
    ),
    ...children,
  );
}

function json(value) {
  return h("pre", { class: "code" }, JSON.stringify(value, null, 2));
}

function kv(pairs) {
  return h(
    "dl",
    { class: "kv" },
    pairs.filter(([, v]) => v !== undefined && v !== null && v !== "").flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)]),
  );
}

// ---------- navigation ----------

function renderNav() {
  const nav = document.getElementById("nav");
  clear(nav);
  for (const item of SECTIONS) {
    if (!item) {
      nav.append(h("li", { class: "nav-gap", "aria-hidden": "true" }));
      continue;
    }
    const [id, label, glyph] = item;
    const countValue = id === "dead" && state.system?.dead_letters ? state.system.dead_letters : null;
    nav.append(
      h(
        "li",
        null,
        h(
          "button",
          {
            class: "nav-item",
            type: "button",
            "aria-current": state.route === id ? "page" : false,
            onclick: () => go(id),
          },
          icon(glyph),
          label,
          countValue ? h("span", { class: "nav-count" }, String(countValue)) : null,
        ),
      ),
    );
  }
}

function renderFoot() {
  const foot = clear(document.getElementById("sidebar-foot"));
  const sys = state.system;
  if (!sys) return;
  const q = sys.quota || {};
  const used = q.calls_today ?? 0;
  const pct = Math.min(100, (used / (q.daily_limit || 1)) * 100);
  foot.append(
    h(
      "div",
      { class: "meter", title: q.note || "" },
      h("div", { class: "meter-row" }, h("span", null, "Model calls today"), h("span", { class: "num" }, `${count(used)} of ${count(q.daily_limit)}`)),
      h(
        "div",
        { class: "meter-bar" },
        h("div", { class: `meter-fill${pct > 95 ? " full" : pct > 75 ? " high" : ""}`, style: { width: `${pct}%` } }),
      ),
    ),
    h("div", null, `${sys.provider.model}, AMOS ${sys.version}`),
  );
}

function go(route, runId) {
  const target = runId ? `#/run/${runId}` : `#/${route}`;
  if (window.location.hash === target) render();
  else window.location.hash = target;
}

function parseRoute() {
  const parts = window.location.hash.replace(/^#\/?/, "").split("/");
  const route = SECTIONS.some((x) => x && x[0] === parts[0]) ? parts[0] : "run";
  return { route, runId: route === "run" ? parts[1] || null : null };
}

async function render() {
  const { route, runId } = parseRoute();
  if (route !== "run" && state.route === "run") closeInspector();
  state.route = route;
  renderNav();
  if (!state.key && route !== "settings") {
    content.replaceChildren(connectPage());
    return;
  }
  if (route === "run") {
    // A finished run stays on screen until a new goal replaces it.
    if (runId && runId !== state.runId) openRun(runId);
    content.replaceChildren(runPage());
    drawRun();
    return;
  }
  const builders = {
    history: historyPage,
    memory: memoryPage,
    knowledge: knowledgePage,
    agents: agentsPage,
    system: systemPage,
    insights: insightsPage,
    dead: deadPage,
    settings: settingsPage,
  };
  content.replaceChildren(page("Loading", null));
  try {
    content.replaceChildren(await builders[route]());
  } catch (error) {
    content.replaceChildren(page(SECTIONS.find((x) => x && x[0] === route)[1], null, errorBanner(error)));
    if (error instanceof ApiError && error.status === 401) state.key = "";
  }
}

// ---------- connecting ----------

function connectPage() {
  const input = h("input", { class: "field", type: "password", placeholder: "Paste your API key", autocomplete: "off", "aria-label": "API key" });
  const remember = h("input", { type: "checkbox" });
  const submit = async () => {
    const key = input.value.trim();
    if (!key) return;
    state.key = key;
    try {
      await refreshSystem();
      saveKey(key, remember.checked);
      toast("Connected");
      render();
    } catch (error) {
      state.key = "";
      status.replaceChildren(errorBanner(error));
    }
  };
  input.addEventListener("keydown", (e) => e.key === "Enter" && submit());
  const status = h("div");
  return page(
    "Connect to AMOS",
    "AMOS checks an API key on every request. Use the key printed when your user was created.",
    h(
      "div",
      { class: "group" },
      h("div", { class: "row" }, input),
      h("label", { class: "row switch" }, remember, "Remember on this device"),
    ),
    h("div", { class: "group-foot" }, "Without remembering, the key lasts until this tab closes."),
    h("p", null, h("button", { class: "button primary", type: "button", onclick: submit }, "Connect")),
    status,
  );
}

async function refreshSystem() {
  state.system = await api.get("/v1/system");
  renderFoot();
  renderNav();
  document.getElementById("brand-sub").textContent = `${state.system.provider.model}`;
}

// ---------- the run page ----------

const runDom = {};

function runPage() {
  const features = state.system?.features || {};
  const textarea = h("textarea", {
    rows: 2,
    placeholder: "What should AMOS do?",
    "aria-label": "Goal",
    id: "goal",
  });
  textarea.value = state.draft;
  textarea.addEventListener("input", () => {
    state.draft = textarea.value;
  });

  const presets = h(
    "div",
    { class: "presets", role: "list", "aria-label": "Demo goals" },
    PRESETS.map(([label, goal]) =>
      h(
        "button",
        {
          class: "preset",
          type: "button",
          title: goal,
          role: "listitem",
          onclick: () => {
            textarea.value = goal;
            state.draft = goal;
            textarea.focus();
          },
        },
        label,
      ),
    ),
  );

  const options = h(
    "div",
    { class: "options", role: "group", "aria-label": "Features for this run" },
    Object.entries(OPTION_LABELS).map(([name, label]) => {
      const serverOn = features[name] !== false;
      const box = h("input", {
        type: "checkbox",
        checked: serverOn && state.options[name] !== false,
        disabled: !serverOn,
        onchange: (e) => {
          state.options[name] = e.target.checked ? undefined : false;
        },
      });
      return h(
        "label",
        { class: `switch${serverOn ? "" : " is-disabled"}`, title: serverOn ? `Switch ${label.toLowerCase()} off for this run` : "Off on this server" },
        box,
        label,
      );
    }),
  );

  const mode = h(
    "div",
    { class: "segmented", role: "group", "aria-label": "How to run" },
    [
      ["sync", "Wait here"],
      ["async", "Queue"],
    ].map(([value, label]) =>
      h(
        "button",
        {
          type: "button",
          "aria-pressed": state.mode === value ? "true" : "false",
          disabled: value === "async" && !features.async,
          title: value === "async" && !features.async ? "Queued runs need AMOS_ASYNC_ENABLED=true and a worker" : label === "Queue" ? "A worker runs it; this page watches" : "This page waits for the answer",
          onclick: (e) => {
            state.mode = value;
            for (const b of e.currentTarget.parentElement.children) b.setAttribute("aria-pressed", String(b === e.currentTarget));
          },
        },
        label,
      ),
    ),
  );

  runDom.button = h(
    "button",
    { class: "button primary", type: "button", onclick: submitGoal, disabled: Boolean(state.pending) },
    "Run",
    h("kbd", null, `${MOD} ↵`),
  );

  runDom.head = h("div");
  runDom.stage = h("div", { class: "stage" });
  runDom.lanes = h("div");
  runDom.legend = legend();
  runDom.stage.append(runDom.lanes, runDom.legend);
  runDom.body = h("div", { class: "run-body" });

  return h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "composer" },
      textarea,
      presets,
      h("div", { class: "composer-bar" }, options, h("div", { class: "composer-actions" }, mode, runDom.button)),
    ),
    runDom.head,
    runDom.stage,
    runDom.body,
  );
}

function legend() {
  const item = (mark, text) => h("span", { class: "legend-item" }, mark, text);
  const swatch = (agent) =>
    item(s("svg", { viewBox: "0 0 12 12" }, s("rect", { x: 1, y: 3, width: 10, height: 6, rx: 3, style: { fill: laneColor(agent) } })), LANES[agent].name);
  return h(
    "div",
    { class: "legend", "aria-hidden": "true" },
    item(s("svg", { viewBox: "0 0 12 12" }, s("circle", { cx: 6, cy: 6, r: 3.4, style: { fill: "var(--ink-2)" } })), "model call"),
    item(s("svg", { viewBox: "0 0 12 12" }, s("rect", { x: 2.5, y: 2.5, width: 7, height: 7, rx: 1.5, style: { fill: "none", stroke: "var(--ink-2)", strokeWidth: "1.6" } })), "tool call"),
    item(s("svg", { viewBox: "0 0 12 12" }, s("circle", { cx: 6, cy: 6, r: 3.4, style: { fill: "var(--fail)" } })), "failed call"),
    swatch("researcher"),
    swatch("analyst"),
    swatch("critic"),
  );
}

let resizeObserver = null;

function drawRun() {
  if (!runDom.stage) return;
  const trace = state.trace;
  drawHead(trace);
  drawLanes(trace);
  drawBody(trace);
  if (!resizeObserver) {
    resizeObserver = new ResizeObserver(() => state.route === "run" && drawLanes(state.trace));
  }
  resizeObserver.disconnect();
  resizeObserver.observe(runDom.stage);
}

function drawHead(trace) {
  const head = clear(runDom.head);
  if (!trace && !state.pending) return;
  const goal = trace?.goal || state.pending?.goal;
  const elapsed = trace
    ? (trace.completed_at ? Date.parse(trace.completed_at) : Date.now()) - Date.parse(trace.created_at)
    : null;
  const options = Object.entries(trace?.options || {})
    .filter(([, v]) => v === false)
    .map(([k]) => h("span", { class: "chip" }, `${OPTION_LABELS[k] || k} off`));
  head.append(
    h(
      "div",
      { class: "run-head" },
      h("h2", { class: "run-goal" }, goal),
      statusBadge(trace?.status || "RECEIVED"),
    ),
    h(
      "div",
      { class: "run-stats" },
      elapsed !== null ? h("span", null, h("strong", null, ms(elapsed)), " elapsed") : null,
      trace ? h("span", null, h("strong", null, count(trace.total_tokens || sumTokens(trace))), " tokens") : null,
      trace ? h("span", null, h("strong", null, String(trace.llm_calls.length)), " model calls") : null,
      trace ? h("span", null, h("strong", null, String(trace.tool_calls.length)), " tool calls") : null,
      trace?.attempt_count > 1 ? h("span", null, `attempt ${trace.attempt_count}`) : null,
      ...options,
    ),
  );
  if (state.error) head.append(errorBanner(state.error));
}

function sumTokens(trace) {
  return trace.llm_calls.reduce((n, c) => n + c.prompt_tokens + c.output_tokens, 0);
}

function drawLanes(trace) {
  if (!runDom.lanes) return;
  const width = Math.max(runDom.stage.clientWidth || 800, 480);
  const model = trace ? buildModel(trace) : emptyModel();
  renderLanes(runDom.lanes, model, {
    width,
    seen: state.seen,
    selected: state.selected?.segment?.key,
    onSelect: select,
  });
  const note = runDom.stage.querySelector(".stage-empty");
  if (!trace && !note) {
    runDom.stage.insertBefore(
      h("div", { class: "stage-empty" }, "Choose a goal or write your own. Each agent gets a lane, and every task, model call and tool call appears on it as it happens."),
      runDom.legend,
    );
  } else if (trace && note) note.remove();
}

function drawBody(trace) {
  const body = clear(runDom.body);
  if (!trace) return;
  const answerCol = h("div");
  const evidenceCol = h("div");

  if (trace.result) {
    const r = trace.result;
    answerCol.append(
      ...section(
        "Answer",
        h(
          "div",
          { class: "answer" },
          markdown(r.answer),
          h(
            "div",
            { class: "answer-meta" },
            h("span", null, `Confidence: ${r.confidence}`),
            r.assumptions?.length ? h("span", null, `${r.assumptions.length} assumption${r.assumptions.length > 1 ? "s" : ""}`) : null,
          ),
          r.caveats?.length ? h("ul", { class: "caveats" }, r.caveats.map((c) => h("li", null, c))) : null,
        ),
      ),
    );
    if (r.reasoning) answerCol.append(h("p", { class: "muted small" }, r.reasoning));
  } else if (trace.error) {
    answerCol.append(...section("Answer", errorBanner(new ApiError(500, trace.error.type, trace.error.message))));
  } else {
    answerCol.append(...section("Answer", h("div", { class: "answer muted" }, "The answer appears here when the run finishes.")));
  }

  const verdicts = trace.events.filter((e) => e.kind === "critic.verdict");
  if (verdicts.length) {
    evidenceCol.append(
      ...section(
        "Critic",
        h(
          "div",
          { class: "group" },
          verdicts.map((v) =>
            h(
              "button",
              { class: "row", type: "button", onclick: () => select({ type: "event", item: v }) },
              h(
                "span",
                { class: `status ${v.payload.verdict === "accept" ? "ok" : "warn"}` },
                icon(v.payload.verdict === "accept" ? "check" : "half"),
              ),
              h(
                "div",
                { class: "row-label" },
                h("div", { class: "row-title" }, `${v.payload.verdict === "accept" ? "Accepted" : "Asked to revise"}${v.task_ref ? ` ${v.task_ref}` : ""}`),
                h("div", { class: "row-sub" }, v.payload.reasoning || ""),
              ),
            ),
          ),
        ),
      ),
    );
  }

  const citations = collectCitations(trace);
  if (citations.length) {
    const answerText = (trace.result?.answer || "").toLowerCase();
    evidenceCol.append(
      ...section(
        "Sources",
        h(
          "div",
          { class: "citations" },
          citations.map((c) => {
            const base = c.citation.split("/").pop().replace(/\.[a-z]+$/, "").toLowerCase();
            const cited = answerText.includes(c.citation.toLowerCase()) || (base.length > 3 && answerText.includes(base));
            return h(
              "button",
              { class: `citation${cited ? " cited" : ""}`, type: "button", onclick: () => openChunk(c) },
              h("span", { class: "citation-source" }, c.citation),
              c.heading ? h("span", { class: "citation-heading" }, c.heading) : null,
              h("span", { class: "citation-score" }, `similarity ${c.score.toFixed(2)}${cited ? ", cited in the answer" : ""}`),
            );
          }),
        ),
      ),
    );
  }

  const memory = trace.tool_calls.filter((t) => ["remember_fact", "recall_facts", "recall_past_runs"].includes(t.tool_name));
  if (memory.length) {
    evidenceCol.append(
      ...section(
        "Memory",
        h(
          "div",
          { class: "group" },
          memory.map((t) => {
            const out = t.output || {};
            const text =
              t.tool_name === "remember_fact"
                ? out.stored
                  ? `Stored "${humanSubject(out.subject)}": ${out.content}`
                  : `Not stored: ${out.reason || t.error || ""}`
                : t.tool_name === "recall_facts"
                  ? out.found
                    ? (out.facts || []).map((f) => `${humanSubject(f.subject)}: ${f.content}`).join("; ")
                    : "Nothing remembered matched"
                  : `${out.found || 0} similar past run${out.found === 1 ? "" : "s"}`;
            return h(
              "button",
              { class: "row", type: "button", onclick: () => select({ type: "tool", item: t }) },
              h("div", { class: "row-label" }, h("div", { class: "row-title" }, t.tool_name.replace(/_/g, " ")), h("div", { class: "row-sub" }, text)),
            );
          }),
        ),
      ),
    );
  }

  body.append(evidenceCol.childElementCount ? h("div", { class: "grid-2" }, answerCol, evidenceCol) : answerCol);
  body.append(...section("Activity", h("div", { class: "group feed" }, feedRows(trace))));
}

function collectCitations(trace) {
  const seen = new Map();
  for (const call of trace.tool_calls) {
    if (call.tool_name !== "search_knowledge" || !call.output) continue;
    for (const p of call.output.passages || []) {
      const key = p.chunk_id || `${p.citation}:${p.heading}`;
      if (!seen.has(key) || seen.get(key).score < p.score) seen.set(key, p);
    }
  }
  return [...seen.values()].sort((a, b) => b.score - a.score);
}

function describe(item, trace) {
  const offset = (iso) => (iso ? `${((Date.parse(iso) - Date.parse(trace.created_at)) / 1000).toFixed(1)} s` : "");
  if (item.kind === "llm") {
    const who = item.agent ? LANES[item.agent]?.name || capitalise(item.agent) : PURPOSE[item.purpose] || "Model";
    const what = {
      planner: "Planner drafted the plan",
      router: "Router chose an agent",
      agent: `${who} thought`,
      revision: `${who} revised the answer`,
      critic: "Critic reviewed the answer",
      synthesis: "Combined the task results",
      delegate: `${who} worked on a delegation`,
      reconciler: "Checked what was claimed against what was stored",
    }[item.purpose] || "Model call";
    return {
      time: offset(item.finished_at),
      title: item.error ? `${what}, and the call failed` : what,
      detail: item.error ? item.error : [item.task_ref, item.model].filter(Boolean).join(", "),
      meta: `${count(item.prompt_tokens + item.output_tokens)} tok, ${ms(item.latency_ms)}`,
      mark: h("span", { class: "dot", style: { background: item.error ? "var(--fail)" : laneColor(item.agent || "orchestrator") } }),
      error: Boolean(item.error),
    };
  }
  if (item.kind === "tool") {
    const args = item.arguments || {};
    const summary = args.query || args.expression || args.url || args.path || args.subject || args.instruction || args.goal || "";
    const who = item.agent ? LANES[item.agent]?.name || capitalise(item.agent) : "Agent";
    return {
      time: offset(item.finished_at),
      title: `${who} used ${item.tool_name}${item.status !== "ok" ? ` (${item.status})` : ""}`,
      detail: String(summary),
      meta: ms(item.latency_ms),
      mark: h("span", { class: "square", style: { "--c": laneColor(item.agent || "agent") } }),
      error: item.status !== "ok",
    };
  }
  const p = item.payload || {};
  const ref = item.task_ref ? `Task ${item.task_ref} ` : "The task ";
  const titles = {
    "run.started": "Run started",
    "run.claimed": `A worker took the run (attempt ${p.attempt})`,
    "plan.created": `Planned ${p.tasks?.length || 0} task${p.tasks?.length === 1 ? "" : "s"}`,
    "plan.resumed": "Resumed the stored plan",
    "task.started": `${ref}started`,
    "task.routed": `${ref}went to ${LANES[p.agent]?.name || p.agent}`,
    "task.succeeded": `${ref}finished`,
    "task.failed": `${ref}failed`,
    "task.timed_out": `${ref}ran out of time`,
    "task.retrying": `${ref}will retry in ${ms((p.delay_seconds || 0) * 1000)}`,
    "task.permanently_failed": `${ref}gave up after ${p.attempts} attempts`,
    "task.skipped": `${ref}skipped`,
    "task.resumed": `${ref}already done on an earlier attempt`,
    "critic.verdict": `Critic ${p.verdict === "accept" ? "accepted" : "asked to revise"}${item.task_ref ? ` ${item.task_ref}` : " the answer"}`,
    "delegation.started": `${capitalise(p.source)} asked ${capitalise(p.target)} for help`,
    "delegation.finished": `${capitalise(p.target)} answered ${capitalise(p.source)}`,
    "run.finished": "Run finished",
    "run.failed": "Run failed",
  };
  const detail = p.reason || p.reasoning || p.error || p.instruction || p.answer || p.description || "";
  return {
    time: offset(item.at),
    title: titles[item.kind] || capitalise(item.kind),
    detail: String(detail),
    meta: "",
    mark: h("span", { class: "ring" }),
    error: ["task.failed", "task.timed_out", "task.permanently_failed", "run.failed"].includes(item.kind),
  };
}

function feedRows(trace) {
  const items = [
    ...trace.llm_calls.map((c) => ({ ...c, kind: "llm" })),
    ...trace.tool_calls.map((c) => ({ ...c, kind: "tool" })),
    ...trace.events.map((e) => ({ ...e, kind: e.kind, isEvent: true })),
  ].sort((a, b) => a.seq - b.seq);
  if (!items.length) return [empty("Nothing has happened yet", "Calls and decisions appear here in the order they happened.")];
  return items.map((item) => {
    const d = describe(item.isEvent ? item : item, trace);
    const type = item.isEvent ? "event" : item.kind;
    const selectedSeq = state.selected?.item?.seq;
    return h(
      "button",
      {
        class: `feed-row${d.error ? " error" : ""}${selectedSeq === item.seq ? " selected" : ""}`,
        type: "button",
        onclick: () => select({ type, item }),
      },
      h("span", { class: "feed-time" }, d.time),
      h("span", { class: "feed-icon" }, d.mark),
      h("span", { class: "feed-text" }, h("div", { class: "feed-title" }, d.title), d.detail ? h("div", { class: "feed-detail" }, d.detail) : null),
      h("span", { class: "feed-meta" }, d.meta),
    );
  });
}

// ---------- submitting and watching ----------

function chosenOptions() {
  const options = {};
  for (const [name, value] of Object.entries(state.options)) if (value === false) options[name] = false;
  return Object.keys(options).length ? options : undefined;
}

async function submitGoal() {
  const goal = (document.getElementById("goal")?.value || "").trim();
  if (!goal || state.pending) return;
  const body = { goal, options: chosenOptions() };
  state.error = null;
  state.seen = new Set();
  state.selected = null;
  closeInspector();
  state.trace = null;
  state.runId = null;
  state.watching = null;
  state.pending = { goal };
  if (runDom.button) runDom.button.disabled = true;
  drawRun();

  if (state.mode === "async") {
    try {
      const queued = await api.post("/v1/goals/async", body);
      state.pending = null;
      go("run", queued.run_id);
    } catch (error) {
      state.pending = null;
      state.error = error;
      drawRun();
    } finally {
      if (runDom.button) runDom.button.disabled = false;
    }
    return;
  }

  // Synchronous: the page chooses the idempotency key, so it can find the run
  // row — written before execution — while the POST is still waiting (V1.7).
  const key = crypto.randomUUID();
  state.pending = { goal, key };
  const post = api.post("/v1/goals", body, { "idempotency-key": key });
  let settled = false;
  post.finally(() => {
    settled = true;
  });
  (async () => {
    while (!settled && !state.runId && state.pending?.key === key) {
      try {
        const [found] = await api.get("/v1/runs", { idempotency_key: key });
        if (found) {
          go("run", found.run_id);
          return;
        }
      } catch {
        /* the POST's own outcome will report a failure */
      }
      await sleep(450);
    }
  })();

  try {
    const result = await post;
    if (!state.runId && result.run_id) go("run", result.run_id);
  } catch (error) {
    state.error = error;
    if (!state.runId) {
      try {
        const [found] = await api.get("/v1/runs", { idempotency_key: key });
        if (found) go("run", found.run_id);
      } catch {
        /* nothing more to show */
      }
    }
  } finally {
    state.pending = null;
    if (runDom.button) runDom.button.disabled = false;
    if (state.runId) await refreshTrace(state.runId);
    drawRun();
    refreshSystem().catch(() => {});
  }
}

function sleep(msValue) {
  return new Promise((resolve) => setTimeout(resolve, msValue));
}

function openRun(runId) {
  if (state.runId !== runId) {
    state.runId = runId;
    state.trace = null;
    state.seen = new Set();
    state.selected = null;
    closeInspector();
  }
  watch(runId);
}

async function refreshTrace(runId) {
  const trace = await api.get(`/v1/runs/${runId}`);
  if (state.runId === runId) {
    state.trace = trace;
    if (state.route === "run") drawRun();
    if (state.selected) refreshInspector();
  }
  return trace;
}

async function watch(runId) {
  if (state.watching === runId) return;
  state.watching = runId;
  while (state.watching === runId) {
    try {
      const trace = await refreshTrace(runId);
      if (TERMINAL.has(trace.status) && !state.pending) break;
    } catch (error) {
      state.error = error;
      if (state.route === "run") drawRun();
      break;
    }
    await sleep(document.hidden ? 4000 : 800);
  }
  if (state.watching === runId) state.watching = null;
}

// ---------- the inspector ----------

function select(selection) {
  state.selected = selection;
  refreshInspector();
  if (state.route === "run") {
    drawLanes(state.trace);
    for (const el of content.querySelectorAll(".feed-row.selected")) el.classList.remove("selected");
  }
}

function closeInspector() {
  inspector.hidden = true;
  state.selected = null;
}

function refreshInspector() {
  const sel = state.selected;
  if (!sel) return;
  inspector.hidden = false;
  const body = clear(inspectorBody);
  const trace = state.trace;
  const t0 = trace ? Date.parse(trace.created_at) : 0;
  const at = (t) => (t0 ? `${((t - t0) / 1000).toFixed(1)} s into the run` : "");

  if (sel.type === "segment") {
    const seg = sel.segment;
    if (seg.kind === "task") {
      const task = trace?.tasks.find((t) => t.plan_ref === seg.ref);
      inspectorTitle.textContent = seg.ref ? `Task ${seg.ref}` : "The goal";
      body.append(
        kv([
          ["Agent", LANES[seg.agent]?.name || capitalise(seg.agent)],
          ["Attempt", seg.attempt ? String(seg.attempt) : null],
          ["State", capitalise((seg.state || task?.state || "").toLowerCase().replace(/_/g, " "))],
          ["Started", at(seg.start)],
          ["Took", ms(seg.end - seg.start)],
          ["Calls", `${seg.calls.length}`],
          ["Needs", task?.depends_on?.length ? task.depends_on.join(", ") : null],
        ]),
        h("div", { class: "inspector-subtitle" }, "Instruction"),
        h("div", { class: "passage" }, seg.description || task?.description || ""),
      );
      if (task?.answer) body.append(h("div", { class: "inspector-subtitle" }, "Result"), markdown(task.answer));
      if (task?.error) body.append(h("div", { class: "inspector-subtitle" }, "Error"), h("div", { class: "passage" }, task.error));
    } else if (seg.kind === "critic") {
      const p = seg.verdictEvent?.payload || {};
      inspectorTitle.textContent = seg.label;
      body.append(
        kv([
          ["Verdict", p.verdict === "accept" ? "Accepted" : p.verdict ? "Asked to revise" : "—"],
          ["Revision", p.revision !== undefined ? String(p.revision) : null],
          ["Evidence items", p.evidence_items !== undefined ? String(p.evidence_items) : null],
          ["Took", ms(seg.end - seg.start)],
        ]),
      );
      if (p.reasoning) body.append(h("div", { class: "inspector-subtitle" }, "Reasoning"), h("div", { class: "passage" }, p.reasoning));
      if (p.unsupported_claims?.length)
        body.append(h("div", { class: "inspector-subtitle" }, "Unsupported claims"), h("ul", null, p.unsupported_claims.map((c) => h("li", null, c))));
    } else if (seg.kind === "delegate") {
      const done = trace?.events.find((e) => e.kind === "delegation.finished" && e.payload.delegation_id === seg.event.payload.delegation_id);
      inspectorTitle.textContent = `Delegation to ${LANES[seg.agent]?.name || seg.agent}`;
      body.append(
        kv([
          ["Asked by", capitalise(seg.event.payload.source)],
          ["Depth", String(seg.event.payload.depth)],
          ["Tokens", done ? count(done.payload.tokens) : null],
          ["Took", ms(seg.end - seg.start)],
        ]),
        h("div", { class: "inspector-subtitle" }, "Instruction"),
        h("div", { class: "passage" }, seg.event.payload.instruction || ""),
      );
      if (done) body.append(h("div", { class: "inspector-subtitle" }, "Answer"), markdown(done.payload.answer));
    } else {
      const planEvent = trace?.events.find((e) => e.kind === "plan.created");
      inspectorTitle.textContent = seg.kind === "planner" ? "The plan" : "Combining the results";
      body.append(kv([["Calls", String(seg.calls.length)], ["Took", ms(seg.end - seg.start)], ["Started", at(seg.start)]]));
      if (seg.kind === "planner" && planEvent) {
        if (planEvent.payload.reasoning) body.append(h("div", { class: "inspector-subtitle" }, "Why this plan"), h("div", { class: "passage" }, planEvent.payload.reasoning));
        body.append(
          h("div", { class: "inspector-subtitle" }, "Tasks"),
          h(
            "div",
            { class: "group" },
            planEvent.payload.tasks.map((t) => row(`${t.id}  ${t.description}`, undefined, t.depends_on.length ? `needs ${t.depends_on.join(", ")}` : "independent")),
          ),
        );
      }
    }
    if (seg.calls.length) {
      body.append(
        h("div", { class: "inspector-subtitle" }, "Calls"),
        h(
          "div",
          { class: "group feed" },
          seg.calls.map((c) => {
            const d = describe(c, trace);
            return h(
              "button",
              { class: "feed-row", type: "button", onclick: () => select({ type: c.kind, item: c }) },
              h("span", { class: "feed-time" }, d.time),
              h("span", { class: "feed-icon" }, d.mark),
              h("span", { class: "feed-text" }, h("div", { class: "feed-title" }, d.title), h("div", { class: "feed-detail" }, d.detail)),
              h("span", { class: "feed-meta" }, d.meta),
            );
          }),
        ),
      );
    }
    return;
  }

  if (sel.type === "llm") {
    const c = sel.item;
    inspectorTitle.textContent = `${PURPOSE[c.purpose] || "Model"} call`;
    body.append(
      kv([
        ["Purpose", PURPOSE[c.purpose] || c.purpose || "unattributed"],
        ["Agent", c.agent ? LANES[c.agent]?.name || c.agent : null],
        ["Task", c.task_ref],
        ["Task attempt", c.task_attempt ? String(c.task_attempt) : null],
        ["Provider", `${c.provider}, ${c.model}`],
        ["Tokens in", count(c.prompt_tokens)],
        ["Tokens out", count(c.output_tokens)],
        ["Latency", ms(c.latency_ms)],
        ["Finished", c.finished_at ? `${clock(c.finished_at)}, ${at(Date.parse(c.finished_at))}` : null],
        ["Order", `#${c.seq}`],
      ]),
    );
    if (c.error) body.append(h("div", { class: "inspector-subtitle" }, "Error"), h("div", { class: "passage" }, c.error));
    body.append(h("p", { class: "muted small" }, "Prompts and replies are not stored; the record keeps who called, why, and what it cost."));
    return;
  }

  if (sel.type === "tool") {
    const t = sel.item;
    inspectorTitle.textContent = t.tool_name;
    body.append(
      kv([
        ["Status", t.status],
        ["Agent", t.agent ? LANES[t.agent]?.name || t.agent : null],
        ["Task", t.task_ref],
        ["Latency", ms(t.latency_ms)],
        ["Order", `#${t.seq}`],
      ]),
      h("div", { class: "inspector-subtitle" }, "Arguments"),
      json(t.arguments),
    );
    if (t.error) body.append(h("div", { class: "inspector-subtitle" }, "Error"), h("div", { class: "passage" }, t.error));
    if (t.output) body.append(h("div", { class: "inspector-subtitle" }, "Output"), json(t.output));
    return;
  }

  if (sel.type === "event") {
    const e = sel.item;
    inspectorTitle.textContent = describe(e, trace).title;
    body.append(kv([["Kind", e.kind], ["Task", e.task_ref], ["Agent", e.agent], ["When", `${clock(e.at)}, ${at(Date.parse(e.at))}`], ["Order", `#${e.seq}`]]), json(e.payload));
    return;
  }

  if (sel.type === "chunk") {
    const c = sel.item;
    inspectorTitle.textContent = c.source;
    body.append(
      kv([
        ["Section", c.heading],
        ["Passage", String(c.chunk_index + 1)],
        ["Owner", c.owner === "you" ? "You" : "Shared corpus"],
      ]),
      h("div", { class: "passage" }, c.content),
    );
  }
}

async function openChunk(passage) {
  if (!passage.chunk_id) {
    select({ type: "chunk", item: { source: passage.citation, heading: passage.heading, chunk_index: 0, owner: "system", content: passage.content } });
    return;
  }
  try {
    select({ type: "chunk", item: await api.get(`/v1/chunks/${passage.chunk_id}`) });
  } catch (error) {
    toast(explain(error).title);
  }
}

document.getElementById("inspector-close").addEventListener("click", () => {
  closeInspector();
  if (state.route === "run") drawLanes(state.trace);
});

// ---------- other pages ----------

async function historyPage() {
  const runs = await api.get("/v1/runs", { limit: 60 });
  return page(
    "History",
    "Every run you have made, newest first. Open one to replay its record exactly as it happened.",
    runs.length
      ? h(
          "div",
          { class: "group" },
          runs.map((r) =>
            h(
              "button",
              { class: "row", type: "button", onclick: () => go("run", r.run_id) },
              statusIcon(r.status),
              h(
                "div",
                { class: "row-label" },
                h("div", { class: "row-title" }, r.goal),
                h(
                  "div",
                  { class: "row-sub" },
                  [ago(r.created_at), r.latency_ms ? ms(r.latency_ms) : null, r.total_tokens ? `${count(r.total_tokens)} tokens` : null, ...Object.entries(r.options || {}).filter(([, v]) => v === false).map(([k]) => `${OPTION_LABELS[k] || k} off`)]
                    .filter(Boolean)
                    .join(", "),
                ),
              ),
              icon("chevron", "chevron"),
            ),
          ),
        )
      : h("div", { class: "group" }, empty("No runs yet", "Run a goal and it will appear here.")),
  );
}

async function memoryPage() {
  const facts = await api.get("/v1/memories");
  const list = h("div", { class: "group" });
  if (!facts.length) list.append(empty("Nothing remembered yet", 'Try "Remember that my project review is on Friday."'));
  for (const f of facts) {
    const historyHost = h("div");
    const toggle = h("button", { class: "link-button small", type: "button" }, "History");
    toggle.addEventListener("click", async () => {
      if (historyHost.childElementCount) {
        clear(historyHost);
        return;
      }
      const versions = await api.get("/v1/memories/history", { subject: f.subject });
      historyHost.append(
        ...versions.map((v) =>
          h("div", { class: "row small" }, h("div", { class: "row-label muted" }, `${v.superseded ? "Was" : "Now"}: ${v.content}`), h("div", { class: "row-value" }, ago(v.created_at))),
        ),
      );
    });
    list.append(
      h(
        "div",
        { class: "row" },
        h(
          "div",
          { class: "row-label" },
          h("div", { class: "row-title" }, humanSubject(f.subject)),
          h("div", null, f.content),
          h(
            "div",
            { class: "row-sub" },
            `Learned ${ago(f.created_at)}`,
            f.source_run_id ? h("span", null, ", ", h("button", { class: "link-button small", type: "button", onclick: () => go("run", f.source_run_id) }, "from this run")) : null,
          ),
        ),
        toggle,
      ),
      historyHost,
    );
  }
  return page(
    "Memory",
    "Facts AMOS has been told to keep. A new value supersedes the old one rather than deleting it, so history stays.",
    list,
    h("div", { class: "group-foot" }, "Only your own facts are shown. Memory is written by the agent's remember tool and checked afterwards against what the answer claims."),
  );
}

async function knowledgePage() {
  const corpus = await api.get("/v1/corpus");
  const t = corpus.totals;
  const input = h("input", { type: "file", accept: ".md,.txt,text/markdown,text/plain", class: "visually-hidden", id: "upload-file" });
  const drop = h(
    "div",
    { class: "drop" },
    h("div", { class: "empty-title" }, "Add a document"),
    h("div", { class: "small" }, "Drop a .md or .txt file up to 20 KB, or ", h("label", { class: "link-button", for: "upload-file" }, "choose one"), ". Only you can retrieve it."),
    input,
  );
  const upload = async (file) => {
    if (!file) return;
    try {
      const text = await file.text();
      const result = await api.post("/v1/documents", { filename: file.name, text });
      toast(result.status === "unchanged" ? `${file.name} is already in your corpus` : `Added ${file.name}, ${result.chunks} passage${result.chunks === 1 ? "" : "s"}`);
      render();
      refreshSystem().catch(() => {});
    } catch (error) {
      const e = explain(error);
      toast(`${e.title}. ${e.detail}`);
    }
  };
  input.addEventListener("change", () => upload(input.files[0]));
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    upload(e.dataTransfer.files[0]);
  });
  const docRow = (d) => row(d.source, `${count(d.chunk_count)} passages`);
  const yours = corpus.documents.filter((d) => d.owner === "you");
  const shared = corpus.documents.filter((d) => d.owner !== "you");
  return page(
    "Knowledge",
    `Retrieval searches ${count(t.system_chunks)} passages from ${count(t.system_documents)} shared documents${t.your_documents ? `, plus ${count(t.your_chunks)} from your ${t.your_documents}` : ""}.`,
    drop,
    ...section("Yours", h("div", { class: "group" }, yours.length ? yours.map(docRow) : empty("No uploads yet"))),
    ...section("Shared corpus", h("div", { class: "group" }, shared.map(docRow))),
    h("div", { class: "group-foot" }, "The shared corpus is AMOS's own documentation, indexed with `make ingest`. It is readable by everyone and writable by no one through the API."),
  );
}

async function agentsPage() {
  const sys = state.system || (await api.get("/v1/system"));
  const agents = sys.agents.map((a) =>
    h(
      "div",
      { class: "row" },
      h("span", { class: "dot", style: { background: laneColor(a.name) } }),
      h(
        "div",
        { class: "row-label" },
        h("div", { class: "row-title" }, capitalise(a.name)),
        h("div", { class: "row-sub" }, a.purpose),
        h("div", { class: "row-sub" }, a.tools.length ? `Tools: ${a.tools.join(", ")}` : "No tools: it judges, it does not act"),
      ),
      h("div", { class: "row-value small" }, a.routable ? `up to ${a.max_iterations} rounds` : "never routed to"),
    ),
  );
  const tools = sys.tools.map((t) => {
    const schema = h("div");
    const button = h(
      "button",
      { class: "row", type: "button" },
      h("div", { class: "row-label" }, h("div", { class: "row-title" }, t.name), h("div", { class: "row-sub" }, t.description)),
      h("div", { class: "row-value small" }, `${t.permission.toLowerCase().replace(/_/g, " ")}, ${t.timeout_seconds} s`),
      icon("chevron", "chevron"),
    );
    button.addEventListener("click", () => {
      if (schema.childElementCount) clear(schema);
      else schema.append(h("div", { class: "row" }, json(t.parameters)));
    });
    return [button, schema];
  });
  return page(
    "Agents and tools",
    "Each agent is built with only its own tools. A tool it is not allowed is absent from its registry, not refused by a prompt.",
    ...section("Agents", h("div", { class: "group" }, agents)),
    ...section("Tools", h("div", { class: "group" }, tools.length ? tools : empty("No tools listed", "Start the API with a database to list its tools."))),
    h("div", { class: "group-foot" }, "Arguments are validated against these schemas before a tool runs; the model never executes anything itself."),
  );
}

async function systemPage() {
  await refreshSystem();
  const sys = state.system;
  const q = sys.quota;
  const b = sys.bounds;
  const feature = (name, label) => row(label, sys.features[name] ? "On" : "Off");
  const quotaPct = Math.min(100, ((q.calls_today || 0) / q.daily_limit) * 100);
  return page(
    "System",
    "What this server is, what it may spend, and what bounds every run.",
    h(
      "div",
      { class: "grid-2" },
      h(
        "div",
        null,
        ...section(
          "Model",
          h(
            "div",
            { class: "group" },
            row("Provider", capitalise(sys.provider.name)),
            row("Model", sys.provider.model),
            row("Embeddings", `${sys.provider.embedding_model}, ${sys.provider.embedding_dimensions} dimensions`),
            row("On a 5xx", "one retry, inside the call's deadline"),
          ),
        ),
        ...section(
          "Today's quota",
          h(
            "div",
            { class: "group" },
            h(
              "div",
              { class: "row" },
              h(
                "div",
                { class: "row-label meter" },
                h("div", { class: "meter-row" }, h("span", null, "Model calls"), h("span", { class: "num" }, `${count(q.calls_today)} of ${count(q.daily_limit)}`)),
                h("div", { class: "meter-bar" }, h("div", { class: `meter-fill${quotaPct > 95 ? " full" : quotaPct > 75 ? " high" : ""}`, style: { width: `${quotaPct}%` } })),
              ),
            ),
            row("In the last minute", `${count(q.calls_last_minute)} of ${count(q.minute_limit)}`),
            row("Rate-limited today", count(q.rate_limited_today)),
            row("Provider busy (503) today", count(q.unavailable_today)),
            row("Tokens today", count(q.tokens_today)),
            row("Resets", q.resets),
          ),
          h("div", { class: "group-foot" }, q.note),
        ),
        ...section(
          "Features",
          h(
            "div",
            { class: "group" },
            Object.entries(OPTION_LABELS).map(([k, label]) => feature(k, label)),
            feature("memory_reconcile", "Memory check after each run"),
            feature("async", "Queued runs"),
          ),
          h("div", { class: "group-foot" }, "A run can switch any of the first five off for itself, never on."),
        ),
      ),
      h(
        "div",
        null,
        ...section(
          "Queue and workers",
          sys.queue
            ? h(
                "div",
                { class: "group" },
                row("Waiting", count(sys.queue.queued)),
                row("Running", count(sys.queue.running)),
                row("Last claim", sys.queue.last_claim_at ? ago(sys.queue.last_claim_at) : "never"),
                row("Workers seen in the last hour", sys.queue.workers_seen_last_hour.length ? sys.queue.workers_seen_last_hour.join(", ") : "none"),
                row("Your dead letters", count(sys.dead_letters)),
              )
            : h("div", { class: "group" }, empty("No database", "Queued runs need one.")),
          h("div", { class: "group-foot" }, "There are no heartbeats: a worker is known only by the runs it claims."),
        ),
        ...section(
          "Database",
          h(
            "div",
            { class: "group" },
            row("Connected", sys.database.connected ? "Yes" : "No"),
            row("Schema", sys.database.revision || "—", sys.database.current === false ? `behind the code (${sys.database.head}); run make migrate` : null),
            sys.corpus ? row("Corpus", `${count(sys.corpus.system_documents)} documents, ${count(sys.corpus.system_chunks)} passages`) : null,
          ),
        ),
        ...section(
          "Telemetry",
          h(
            "div",
            { class: "group" },
            row("Traces", sys.telemetry.tracing ? `to ${sys.telemetry.endpoint}` : "off"),
            row("Service name", sys.telemetry.service_name),
            row("Goal text in spans", sys.telemetry.goal_text_in_spans ? "included" : "left out"),
          ),
          h("div", { class: "group-foot" }, "Everything on this page comes from stored runs, not from telemetry."),
        ),
      ),
    ),
    ...section(
      "Bounds",
      h(
        "div",
        { class: "group" },
        h(
          "div",
          { class: "chain" },
          h("div", { class: "chain-step" }, h("span", { class: "small muted" }, "a model call"), h("strong", null, `${b.llm_timeout_seconds} s`)),
          h("span", { class: "chain-lt" }, "<"),
          h("div", { class: "chain-step" }, h("span", { class: "small muted" }, "a task"), h("strong", null, `${b.task_timeout_seconds} s`)),
          h("span", { class: "chain-lt" }, "<"),
          h("div", { class: "chain-step" }, h("span", { class: "small muted" }, "a worker's claim"), h("strong", null, `${b.worker_visibility_timeout} s`)),
        ),
        row("Task attempts", count(b.task_max_attempts)),
        row("Worker attempts per run", count(b.worker_max_attempts)),
        row("Tool rounds per agent", count(b.agent_max_iterations)),
        row("Critic revisions", count(b.max_revisions)),
        row("Delegation", `depth ${b.max_delegation_depth}, ${b.delegation_budget} per task`),
        row("Retrieval threshold", String(b.retrieval_min_score)),
      ),
      h("div", { class: "group-foot" }, "The order matters more than the values: each bound must sit inside the next (ADR-009)."),
    ),
  );
}

function bars(rows, { color } = {}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return h(
    "div",
    { class: "bars" },
    rows.map((r) =>
      h(
        "div",
        { class: "bar-row" },
        h("span", null, r.label),
        h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: { width: `${(r.value / max) * 100}%`, "--c": r.color || color || "var(--ink-2)" } })),
        h("span", { class: "bar-value" }, r.display ?? count(r.value)),
      ),
    ),
  );
}

async function insightsPage() {
  const i = await api.get("/v1/insights");
  const total = Object.values(i.runs_by_status).reduce((a, b) => a + b, 0);
  const purposeColor = { planner: "var(--orchestrator)", synthesis: "var(--orchestrator)", router: "var(--orchestrator)", critic: "var(--critic)" };
  const days = i.runs_per_day;
  const dayLabel = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const spark = bars(days.map((d) => ({ label: dayLabel(d.day), value: d.runs, display: `${count(d.runs)} runs, ${count(d.tokens)} tok` })));
  return page(
    "Insights",
    "Computed from your stored runs, so every figure can be re-derived from the database.",
    h(
      "div",
      { class: "figures" },
      [
        [count(total), "runs"],
        [i.latency_ms.p50 !== null ? ms(i.latency_ms.p50) : "—", "median run time"],
        [i.latency_ms.p95 !== null ? ms(i.latency_ms.p95) : "—", "95th percentile"],
        [count(i.avg_tokens_per_run), "tokens per run"],
        [count(i.task_events["task.retrying"] || 0), "task retries"],
      ].map(([v, l]) => h("div", { class: "figure" }, h("div", { class: "figure-value" }, v), h("div", { class: "figure-label" }, l))),
    ),
    h(
      "div",
      { class: "grid-2" },
      h(
        "div",
        null,
        ...section("Runs per day", h("div", { class: "group" }, days.length ? spark : empty("No runs in the last two weeks"))),
        ...section("Runs by outcome", h("div", { class: "group" }, bars(Object.entries(i.runs_by_status).map(([k, v]) => ({ label: capitalise(k.toLowerCase().replace(/_/g, " ")), value: v, color: k === "COMPLETED" ? "var(--ok)" : k.includes("FAIL") || k === "DEAD_LETTER" ? "var(--fail)" : "var(--ink-2)" }))))),
        ...section("Who the router chose", h("div", { class: "group" }, Object.keys(i.routed_to).length ? bars(Object.entries(i.routed_to).map(([k, v]) => ({ label: capitalise(k), value: v, color: laneColor(k) }))) : empty("No routing recorded yet"))),
      ),
      h(
        "div",
        null,
        ...section("Model calls by purpose", h("div", { class: "group" }, i.llm_calls_by_purpose.length ? bars(i.llm_calls_by_purpose.map((p) => ({ label: PURPOSE[p.purpose] || capitalise(p.purpose), value: p.calls, color: purposeColor[p.purpose] }))) : empty("No calls recorded yet"))),
        ...section("Tool calls", h("div", { class: "group" }, i.tool_calls.length ? bars(i.tool_calls.map((t) => ({ label: `${t.tool}${t.status !== "ok" ? ` (${t.status})` : ""}`, value: t.calls, color: t.status === "ok" ? "var(--ink-2)" : "var(--fail)" }))) : empty("No tool calls yet"))),
        ...section(
          "Critic and provider",
          h(
            "div",
            { class: "group" },
            row("Accepted", count(i.critic_verdicts.accept || 0)),
            row("Asked to revise", count(i.critic_verdicts.revise || 0)),
            ...Object.entries(i.provider_errors).map(([k, v]) => row(k, count(v))),
          ),
        ),
      ),
    ),
  );
}

async function deadPage() {
  const runs = await api.get("/v1/runs/dead-letter");
  return page(
    "Dead letters",
    "Runs the queue gave up on: a worker died on them every time it tried, so they never got a verdict.",
    h(
      "div",
      { class: "group" },
      runs.length
        ? runs.map((r) =>
            h(
              "button",
              { class: "row", type: "button", onclick: () => go("run", r.run_id) },
              h("div", { class: "row-label" }, h("div", { class: "row-title" }, r.goal), h("div", { class: "row-sub" }, `${r.reason}, ${r.attempt_count} attempts, last on ${r.last_worker || "an unknown worker"}`)),
              h("div", { class: "row-value small" }, ago(r.dead_lettered_at)),
              icon("chevron", "chevron"),
            ),
          )
        : empty("Nothing has been given up on", "A run lands here only after its worker fails it on every attempt."),
    ),
  );
}

function settingsPage() {
  const key = h("input", { class: "field", type: "password", value: state.key, autocomplete: "off", "aria-label": "API key" });
  const remember = h("input", { type: "checkbox", checked: keyIsRemembered() });
  const theme = document.documentElement.dataset.theme || "system";
  const presenter = document.documentElement.dataset.presenter === "on";
  const themeControl = h(
    "div",
    { class: "segmented", role: "group", "aria-label": "Appearance" },
    ["system", "light", "dark"].map((value) =>
      h(
        "button",
        {
          type: "button",
          "aria-pressed": String(theme === value),
          onclick: () => {
            setTheme(value);
            render();
          },
        },
        capitalise(value),
      ),
    ),
  );
  const presenterBox = h("input", {
    type: "checkbox",
    checked: presenter,
    onchange: (e) => setPresenter(e.target.checked),
  });
  return page(
    "Settings",
    null,
    ...section(
      "Connection",
      h(
        "div",
        { class: "group" },
        h("div", { class: "row" }, key),
        h("label", { class: "row switch" }, remember, "Remember on this device"),
      ),
      h(
        "p",
        null,
        h(
          "button",
          {
            class: "button primary",
            type: "button",
            onclick: async () => {
              state.key = key.value.trim();
              try {
                await refreshSystem();
                saveKey(state.key, remember.checked);
                toast("Saved");
              } catch (error) {
                toast(explain(error).title);
              }
            },
          },
          "Save key",
        ),
        " ",
        h(
          "button",
          {
            class: "button",
            type: "button",
            onclick: () => {
              forgetKey();
              state.key = "";
              toast("Key forgotten");
              render();
            },
          },
          "Forget key",
        ),
      ),
    ),
    ...section(
      "Appearance",
      h("div", { class: "group" }, row("Theme", themeControl), h("label", { class: "row switch" }, presenterBox, h("span", { class: "row-label" }, "Presenter mode", h("div", { class: "row-sub" }, "Larger type and no side panel, for a projector.")))),
    ),
    ...section(
      "Keyboard",
      h("div", { class: "group" }, row("Write a goal", `${MOD} K`), row("Run it", `${MOD} ↵`), row("Close details", "Esc")),
    ),
  );
}

// ---------- preferences ----------

function setTheme(value) {
  if (value === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = value;
  try {
    localStorage.setItem("amos.theme", value);
  } catch {
    /* preference not kept */
  }
}

function setPresenter(on) {
  if (on) document.documentElement.dataset.presenter = "on";
  else delete document.documentElement.dataset.presenter;
  try {
    localStorage.setItem("amos.presenter", on ? "on" : "off");
  } catch {
    /* preference not kept */
  }
  if (state.route === "run") drawLanes(state.trace);
}

function restorePreferences() {
  try {
    const theme = localStorage.getItem("amos.theme");
    if (theme && theme !== "system") document.documentElement.dataset.theme = theme;
    if (localStorage.getItem("amos.presenter") === "on") document.documentElement.dataset.presenter = "on";
  } catch {
    /* defaults */
  }
}

// ---------- start ----------

document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (state.route !== "run") go("run");
    setTimeout(() => document.getElementById("goal")?.focus(), 30);
  } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && state.route === "run") {
    // Anywhere on the run page, as the button's hint promises — not only while
    // the goal box has focus.
    e.preventDefault();
    submitGoal();
  } else if (e.key === "Escape" && !inspector.hidden) {
    closeInspector();
    if (state.route === "run") drawLanes(state.trace);
  }
});

window.addEventListener("hashchange", render);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.system) refreshSystem().catch(() => {});
});

restorePreferences();
renderNav();
if (state.key) {
  refreshSystem()
    .catch((error) => {
      if (error instanceof ApiError && error.status === 401) {
        forgetKey();
        state.key = "";
      }
    })
    .finally(render);
} else {
  render();
}
setInterval(() => {
  if (!document.hidden && state.key && state.system) refreshSystem().catch(() => {});
}, 20_000);
