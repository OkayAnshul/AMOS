// The lane graph: one lane per agent, a capsule per task attempt, a tick per
// call, dependencies as curves. Built only from the stored run (ADR-015), so a
// run from last week draws exactly as it did live.

import { s } from "./dom.js";

export const LANES = {
  orchestrator: { name: "Orchestrator", role: "plans and combines", color: "var(--orchestrator)" },
  researcher: { name: "Researcher", role: "finds and cites", color: "var(--researcher)" },
  analyst: { name: "Analyst", role: "computes", color: "var(--analyst)" },
  writer: { name: "Writer", role: "composes", color: "var(--writer)" },
  agent: { name: "Agent", role: "one agent, every tool", color: "var(--agent)" },
  critic: { name: "Critic", role: "checks the answer", color: "var(--critic)" },
};
const ORDER = ["orchestrator", "researcher", "analyst", "writer", "agent", "critic"];
const FINISH = {
  "task.succeeded": "SUCCEEDED",
  "task.failed": "FAILED",
  "task.timed_out": "TIMED_OUT",
  "task.permanently_failed": "PERMANENTLY_FAILED",
};

export function laneColor(agent) {
  return (LANES[agent] || LANES.agent).color;
}

function laneFor(agent) {
  return LANES[agent] ? agent : "agent";
}

export function buildModel(trace, now = Date.now()) {
  const t0 = Date.parse(trace.created_at);
  const finished = trace.completed_at ? Date.parse(trace.completed_at) : null;
  const live = !finished && ["RUNNING", "RECEIVED", "QUEUED"].includes(trace.status);
  const horizon = finished ?? now;

  const calls = [
    ...trace.llm_calls.map((c) => ({ ...c, kind: "llm" })),
    ...trace.tool_calls.map((c) => ({ ...c, kind: "tool" })),
  ].map((c) => {
    const end = c.finished_at ? Date.parse(c.finished_at) : horizon;
    return { ...c, end, start: Math.max(t0, end - (c.latency_ms || 0)) };
  });
  calls.sort((a, b) => a.seq - b.seq);
  const events = trace.events.map((e) => ({ ...e, time: Date.parse(e.at) }));

  const segments = [];
  const push = (seg) => segments.push({ ...seg, end: Math.max(seg.end, seg.start + 1) });

  // The orchestrator: planning and combining.
  for (const [purpose, label] of [
    ["planner", "Plan"],
    ["synthesis", "Combine"],
  ]) {
    const own = calls.filter((c) => c.purpose === purpose);
    if (own.length) {
      push({
        key: purpose,
        lane: "orchestrator",
        kind: purpose,
        label,
        start: Math.min(...own.map((c) => c.start)),
        end: Math.max(...own.map((c) => c.end)),
        calls: own,
        failed: own.every((c) => c.error),
      });
    }
  }

  // Each attempt of each task, on the lane of the agent it was routed to.
  const tasks = new Map(trace.tasks.map((t) => [t.plan_ref, t]));
  const routed = new Map();
  for (const e of events) {
    if (e.kind === "task.routed") routed.set(`${e.task_ref}#${e.task_attempt}`, e.payload.agent || e.agent);
  }
  for (const start of events.filter((e) => e.kind === "task.started")) {
    const ref = start.task_ref;
    const attempt = start.task_attempt;
    const close = events.find(
      (e) => FINISH[e.kind] && e.task_ref === ref && e.task_attempt === attempt && e.seq > start.seq,
    );
    const own = calls.filter(
      (c) => c.task_ref === ref && c.task_attempt === attempt && ["router", "agent", "revision", "reader"].includes(c.purpose),
    );
    const agentCall = own.find((c) => c.purpose === "agent" && c.agent);
    const agent = routed.get(`${ref}#${attempt}`) || agentCall?.agent || tasks.get(ref)?.agent || "agent";
    push({
      key: `${ref}#${attempt}`,
      lane: laneFor(agent),
      agent,
      kind: "task",
      ref,
      attempt,
      label: ref,
      description: tasks.get(ref)?.description || start.payload.description || "",
      start: start.time,
      end: close ? close.time : horizon,
      state: close ? FINISH[close.kind] : live ? "RUNNING" : tasks.get(ref)?.state || "RUNNING",
      running: !close && live,
      failed: close ? FINISH[close.kind] !== "SUCCEEDED" : false,
      calls: own,
    });
  }

  // A run without planning has no task events: one implicit task.
  if (!events.some((e) => e.kind === "task.started")) {
    const own = calls.filter((c) => ["router", "agent", "revision", "reader"].includes(c.purpose));
    if (own.length || live) {
      const agentCall = own.find((c) => c.purpose === "agent" && c.agent);
      const routedRun = events.find((e) => e.kind === "task.routed");
      const agent = routedRun?.payload.agent || agentCall?.agent || "agent";
      push({
        key: "run",
        lane: laneFor(agent),
        agent,
        kind: "task",
        ref: null,
        label: "Goal",
        description: trace.goal,
        start: own.length ? Math.min(...own.map((c) => c.start)) : t0,
        end: finished ? Math.max(...own.map((c) => c.end), t0 + 1) : horizon,
        running: live,
        failed: trace.status === "FAILED",
        calls: own,
      });
    }
  }

  // Each critic review, with its verdict.
  const verdicts = events.filter((e) => e.kind === "critic.verdict");
  calls
    .filter((c) => c.purpose === "critic")
    .forEach((c, index) => {
      const verdict = verdicts.find((v) => v.seq > c.seq && v.task_ref === c.task_ref);
      push({
        key: `critic-${c.seq}`,
        lane: "critic",
        kind: "critic",
        ref: c.task_ref,
        label: c.task_ref ? `Review ${c.task_ref}` : "Review",
        start: c.start,
        end: c.end,
        verdict: verdict?.payload.verdict,
        verdictEvent: verdict,
        calls: [c],
        failed: Boolean(c.error),
        index,
      });
    });

  // Delegations, on the lane of the agent asked.
  for (const started of events.filter((e) => e.kind === "delegation.started")) {
    const id = started.payload.delegation_id;
    const done = events.find((e) => e.kind === "delegation.finished" && e.payload.delegation_id === id);
    const target = started.payload.target;
    const end = done ? done.time : horizon;
    push({
      key: `delegation-${id}`,
      lane: laneFor(target),
      agent: target,
      kind: "delegate",
      ref: started.task_ref,
      label: `For ${started.payload.source}`,
      description: started.payload.instruction,
      start: started.time,
      end,
      running: !done && live,
      calls: calls.filter(
        (c) => c.purpose === "delegate" && c.agent === target && c.end >= started.time - 5 && c.end <= end + 5,
      ),
      event: started,
    });
  }

  // Dependencies: from a task's last attempt to its dependent's first.
  const byRef = new Map();
  for (const seg of segments.filter((x) => x.kind === "task" && x.ref)) {
    const list = byRef.get(seg.ref) || [];
    list.push(seg);
    byRef.set(seg.ref, list);
  }
  const deps = [];
  for (const t of trace.tasks) {
    for (const d of t.depends_on || []) {
      const from = byRef.get(d)?.at(-1);
      const to = byRef.get(t.plan_ref)?.[0];
      if (from && to) deps.push({ key: `${d}>${t.plan_ref}`, from, to });
    }
  }

  const used = new Set(["orchestrator", ...segments.map((x) => x.lane)]);
  const lanes = ORDER.filter((l) => used.has(l));
  const latest = Math.max(horizon, ...segments.map((x) => x.end));
  return { t0, end: latest, live, now, lanes, segments, deps, calls };
}

export function emptyModel() {
  const t0 = Date.now();
  return {
    t0,
    end: t0 + 10_000,
    live: false,
    now: t0,
    lanes: ["orchestrator", "researcher", "analyst", "critic"],
    segments: [],
    deps: [],
    calls: [],
  };
}

const LABEL_W = 156;
const PAD_R = 26;
const LANE_H = 64;
const CAP_Y = 10;
const CAP_H = 30;
const AXIS_H = 26;

function step(spanMs) {
  const seconds = spanMs / 1000;
  for (const candidate of [1, 2, 5, 10, 15, 30, 60, 120, 300, 600]) {
    if (seconds / candidate <= 8) return candidate * 1000;
  }
  return 1_200_000;
}

// The longest label that fits: the full one (truncated only if a useful amount
// survives), then a short form, then nothing — a capsule reading "Co…" says
// less than an unlabelled one, whose name is a click or a focus away.
function fit(px, truncate, ...labels) {
  const max = Math.floor(px / 6.6);
  const [full, ...shorter] = labels.filter(Boolean);
  if (full && full.length <= max) return full;
  // Only a description is worth cutting mid-way ("t1 Search the web for…");
  // "Review t1, as…" says less than "Review t1 ↺".
  if (truncate && full && max >= 14) return `${full.slice(0, max - 1)}…`;
  return shorter.find((label) => label.length <= max) || "";
}

// Renders into `host`. `seen` persists across renders of one run so only new
// capsules play the entrance; `selected` is the key of the selected capsule.
export function render(host, model, { width, seen, selected, onSelect }) {
  const span = Math.max(model.end - model.t0, 6000);
  const inner = Math.max(width - LABEL_W - PAD_R, 120);
  const x = (t) => LABEL_W + ((t - model.t0) / span) * inner;

  // Tasks that run at the same time on one agent's lane stack in sub-rows, so
  // three researchers working in parallel read as three capsules, not one blur.
  const rowsByLane = new Map(model.lanes.map((lane) => [lane, []]));
  const rowOf = new Map();
  for (const seg of [...model.segments].sort((a, b) => a.start - b.start)) {
    const rows = rowsByLane.get(seg.lane);
    let row = rows.findIndex((end) => end <= seg.start);
    if (row === -1) {
      row = rows.length;
      rows.push(0);
    }
    rows[row] = seg.end;
    rowOf.set(seg.key, row);
  }
  const ROW_H = CAP_H + 20;
  const laneHeight = (lane) => LANE_H + Math.max(0, rowsByLane.get(lane).length - 1) * ROW_H;
  const laneTop = new Map();
  let top = 0;
  for (const lane of model.lanes) {
    laneTop.set(lane, top);
    top += laneHeight(lane);
  }
  const height = top + AXIS_H;
  const laneY = (lane) => laneTop.get(lane);
  const segY = (seg) => laneY(seg.lane) + (rowOf.get(seg.key) || 0) * ROW_H;

  const root = s("svg", {
    class: "lanes",
    viewBox: `0 0 ${width} ${height}`,
    width,
    height,
    role: "group",
    "aria-label": "Run timeline: one lane per agent",
  });

  model.lanes.forEach((lane, i) => {
    const y = laneY(lane);
    const info = LANES[lane];
    root.append(
      s("rect", { class: `lane-bg${i % 2 ? " alt" : ""}`, x: 0, y, width, height: laneHeight(lane) }),
      s("rect", { x: 18, y: y + 22, width: 8, height: 8, rx: 4, style: { fill: info.color } }),
      s("text", { class: "lane-name", x: 34, y: y + 30 }, info.name),
      s("text", { class: "lane-role", x: 34, y: y + 45 }, info.role),
    );
    if (i > 0) root.append(s("line", { class: "lane-rule", x1: 0, x2: width, y1: y, y2: y }));
  });

  const every = step(span);
  for (let t = 0; t <= span; t += every) {
    const px = x(model.t0 + t);
    root.append(
      s("line", { class: "axis-tick", x1: px, x2: px, y1: 0, y2: height - AXIS_H }),
      s("text", { class: "axis-label", x: px, y: height - 8, "text-anchor": "middle" }, `${Math.round(t / 1000)} s`),
    );
  }

  for (const dep of model.deps) {
    const x1 = x(dep.from.end);
    const y1 = segY(dep.from) + CAP_Y + CAP_H / 2;
    const x2 = x(dep.to.start);
    const y2 = segY(dep.to) + CAP_Y + CAP_H / 2;
    const bend = Math.max(24, (x2 - x1) / 2);
    const fresh = !seen.has(`dep:${dep.key}`);
    seen.add(`dep:${dep.key}`);
    root.append(
      s("path", {
        class: `dep${fresh ? " enter" : ""}`,
        d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
      }),
    );
  }

  for (const seg of model.segments) {
    const color = seg.kind === "critic" ? LANES.critic.color : LANES[seg.lane].color;
    const x1 = x(seg.start);
    const w = Math.max(x(seg.end) - x1, 22);
    const y = segY(seg) + CAP_Y;
    const fresh = !seen.has(seg.key);
    seen.add(seg.key);
    const classes = ["seg", seg.kind];
    if (seg.running) classes.push("running");
    if (seg.failed) classes.push("failed");
    if (fresh) classes.push("enter");
    if (selected === seg.key) classes.push("selected");
    const mark = seg.verdict === "accept" ? " ✓" : seg.verdict ? " ↺" : "";
    const retry = seg.attempt > 1 ? ` retry ${seg.attempt - 1}` : "";
    const label =
      seg.kind === "task" && seg.ref
        ? `${seg.label}${retry}  ${seg.description}`
        : seg.kind === "critic"
          ? `${seg.label}${seg.verdict ? (seg.verdict === "accept" ? ", accepted" : ", asked to revise") : ""}`
          : `${seg.label}${seg.description ? `  ${seg.description}` : ""}`;
    const short =
      seg.kind === "task" && seg.ref
        ? [`${seg.label}${retry}`, seg.label]
        : seg.kind === "critic"
          ? [`${seg.label}${mark}`, `${seg.ref || ""}${mark}`.trim(), mark.trim()]
          : [seg.label];
    const g = s(
      "g",
      {
        class: classes.join(" "),
        tabindex: 0,
        role: "button",
        "aria-label": label,
        style: { "--c": color },
        onclick: () => onSelect({ type: "segment", segment: seg }),
        onkeydown: (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect({ type: "segment", segment: seg });
          }
        },
      },
      s("rect", { class: "body", x: x1, y, width: w, height: CAP_H, rx: CAP_H / 2 }),
      s("text", { class: "seg-label", x: x1 + 13, y: y + 19 }, fit(w - 22, seg.kind === "task" || seg.kind === "delegate", label, ...short)),
    );
    root.append(g);

    for (const call of seg.calls) {
      const cx = Math.min(Math.max(x(call.end), x1 + 4), x1 + w - 4);
      const cy = y + CAP_H + 9;
      const cls = `tick ${call.kind}${call.error ? " error" : ""}`;
      const select = () => onSelect({ type: call.kind, item: call });
      const tick =
        call.kind === "tool"
          ? s("rect", { class: cls, x: cx - 3.5, y: cy - 3.5, width: 7, height: 7, rx: 1.5 })
          : s("circle", { class: cls, cx, cy, r: 3.4 });
      tick.style.setProperty("--c", call.purpose === "router" ? LANES.orchestrator.color : color);
      tick.addEventListener("click", select);
      tick.setAttribute("aria-hidden", "true");
      root.append(tick);
    }
  }

  if (model.live) {
    const px = x(model.now);
    root.append(s("line", { class: "now-line", x1: px, x2: px, y1: 0, y2: height - AXIS_H }));
  }

  host.replaceChildren(root);
}
