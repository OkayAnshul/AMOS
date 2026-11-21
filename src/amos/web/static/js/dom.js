// DOM helpers. Every piece of text on this page goes in through `textContent`
// (via `h`'s string children) — model output, document text and user input are
// untrusted (N-12, ADR-017), and nothing here ever assigns innerHTML.

const SVG_NS = "http://www.w3.org/2000/svg";

function apply(el, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.setAttribute("class", value);
    else if (key === "text") el.textContent = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style") {
      // CSSOM, which a strict CSP permits; a `style` attribute would not be.
      for (const [prop, v] of Object.entries(value)) {
        if (prop.startsWith("--")) el.style.setProperty(prop, v);
        else el.style[prop] = v;
      }
    } else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  apply(el, attrs);
  append(el, children);
  return el;
}

export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  apply(el, attrs);
  append(el, children);
  return el;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

// ---------- icons: stroked, 24-unit grid, drawn for this page ----------

const PATHS = {
  run: "M5 12h3l2-6 4 12 2-6h3",
  history: "M12 7v5l3 2M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4",
  memory: "M7 4h10a1 1 0 0 1 1 1v15l-6-3.5L6 20V5a1 1 0 0 1 1-1z",
  knowledge: "M4 5.5C6.5 4 9.5 4 12 5.5v14C9.5 18 6.5 18 4 19.5zM20 5.5C17.5 4 14.5 4 12 5.5v14c2.5-1.5 5.5-1.5 8 0z",
  agents: "M8.5 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM16.5 11a2.5 2.5 0 1 0 0-5M3 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5M15 14c3 0 5.5 2 5.5 5",
  system: "M12 20a8 8 0 1 1 8-8M12 12l4-4M4 12h1.5M12 4v1.5M18.5 12H20",
  insights: "M5 19V11M10 19V5M15 19v-6M20 19V9",
  dead: "M4 13l2.5-7h11L20 13v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM4 13h5l1 2h4l1-2h5",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2.2-1.3L14 3h-4l-.4 2.4a7 7 0 0 0-2.2 1.3l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2.2 1.3L10 21h4l.4-2.4a7 7 0 0 0 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.1-.9.1-1.3z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  cross: "M7 7l10 10M17 7L7 17",
  clock: "M12 7v5l3 2M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16z",
  spinner: "M12 4a8 8 0 1 0 8 8",
  half: "M12 20a8 8 0 1 0 0-16zM12 4v16",
  tray: "M4 13l2.5-7h11L20 13v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM4 13h5l1 2h4l1-2h5",
  chevron: "M9 6l6 6-6 6",
  upload: "M12 16V5M7.5 9.5L12 5l4.5 4.5M5 19h14",
};

export function icon(name, cls = "") {
  return s("svg", { viewBox: "0 0 24 24", class: cls, "aria-hidden": "true" }, s("path", { d: PATHS[name] }));
}

// ---------- formatting ----------

export function ms(value) {
  if (value === null || value === undefined) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
  const m = Math.floor(value / 60_000);
  const sec = Math.round((value % 60_000) / 1000);
  return `${m} min ${sec} s`;
}

export function count(n) {
  return n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US");
}

export function ago(iso) {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function clock(iso) {
  return iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";
}

export function humanSubject(subject) {
  const text = String(subject || "").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function capitalise(text) {
  const t = String(text || "");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// ---------- a small, safe markdown renderer ----------
// Builds DOM nodes; never parses HTML. Covers what the agents actually write:
// paragraphs, headings, lists, tables, bold, italics, inline code and links.

function inline(text) {
  const out = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("**")) out.push(h("strong", null, token.slice(2, -2)));
    else if (token.startsWith("`")) out.push(h("code", null, token.slice(1, -1)));
    else if (token.startsWith("[")) {
      const [, label, url] = token.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
      if (/^https:\/\//i.test(url)) {
        out.push(h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label));
      } else out.push(label);
    } else out.push(h("em", null, token.slice(1, -1)));
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function markdown(source) {
  const root = h("div", { class: "answer-body" });
  const lines = String(source || "").replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      root.append(h(heading[1].length <= 2 ? "h3" : "h4", null, inline(heading[2])));
      i += 1;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(line);
      i += 2;
      const body = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) body.push(cells(lines[i++]));
      root.append(
        h(
          "table",
          null,
          h("thead", null, h("tr", null, head.map((c) => h("th", null, inline(c))))),
          h("tbody", null, body.map((r) => h("tr", null, r.map((c) => h("td", null, inline(c)))))),
        ),
      );
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const list = h(ordered ? "ol" : "ul");
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        list.append(h("li", null, inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ""))));
        i += 1;
      }
      root.append(list);
      continue;
    }
    // The first line is always consumed, so a line no rule claims (a stray
    // "| x" that is not a table) cannot stall the loop.
    const para = [line.trim()];
    i += 1;
    while (i < lines.length && lines[i].trim() && !/^(#{1,4})\s|^\s*([-*•]|\d+[.)])\s+|^\s*\|/.test(lines[i])) {
      para.push(lines[i].trim());
      i += 1;
    }
    root.append(h("p", null, inline(para.join(" "))));
  }
  return root;
}
