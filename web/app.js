"use strict";

const byId = (id) => document.getElementById(id);
const manifest = byId("manifest");
const confirmInput = byId("confirmed");
const start = byId("start");
const reportArea = byId("results");
const resultExport = byId("export");
const htmlExport = byId("export-html");
const resumeAction = byId("resume-action");
const chainNames = {1: "Ethereum Mainnet", 8453: "Base Mainnet", 10143: "Monad Testnet"};
const statuses = {queued: "Waiting to start", running: "Investigating", complete: "Investigation complete", partial: "Report ready with coverage gaps", failed: "Investigation failed"};
const phases = {planning: "Plan prepared", pinned_snapshot: "Snapshot pinned", collecting: "Collecting chain evidence", address_checkpoint: "Contract report saved", supplementing: "Supplementary investigation", cross_checking: "Checking evidence", report_ready: "Report ready", failed: "Investigation stopped"};
let reviewed = null;
let reviewGeneration = 0;
let currentJob = null;
let monitor = 0;

function node(tag, text, className) {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  if (className) value.className = className;
  return value;
}

function showError(error) {
  const message = {
    authentication_required: "Enter your beta access token.",
    review_required: "Preview the plan again. The previous confirmation has expired.",
    api_rate_limit: "Request limit reached. Wait briefly, then try again.",
    demo_busy: "Another investigation is running. Wait for it to finish, then try again.",
    invalid_plan: "Check the confirmed contracts, network, sources and query range.",
    invalid_job: "This investigation could not be found in the current local store.",
    attempt_budget: "This investigation has reached its three-attempt limit."
  };
  const output = byId("error");
  output.textContent = message[error.message] || error.message;
  output.hidden = false;
}

async function fetchJson(path, options) {
  const headers = new Headers(options?.headers || {});
  const token = byId("access-token").value.trim();
  if (token) headers.set("Authorization", "Bearer " + token);
  const response = await fetch(path, {...options, headers});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "The local server could not complete the request.");
  return payload;
}

function resetReview() {
  reviewed = null;
  reviewGeneration += 1;
  byId("planned").replaceChildren();
  confirmInput.checked = false;
  start.disabled = true;
  byId("review").hidden = true;
}

async function displayReview() {
  resetReview();
  const generation = reviewGeneration;
  const payload = JSON.parse(manifest.value);
  const objective = byId("objective").value.trim();
  const preview = await fetchJson("/api/plan", postOptions(objective ? {...payload, objective} : payload));
  if (generation !== reviewGeneration) return;
  const plan = preview.plan;
  byId("planned").append(details("Validated scope and planner",
    {goal: plan.goal, ranges: plan.ranges, budget_seconds: plan.budget_seconds, planner: plan.planner,
     plan: plan.plan ? {focus: plan.plan.focus, steps: plan.plan.steps, budget: plan.plan.budget,
                        constraints: plan.plan.constraints} : undefined}));
  const rows = plan.entries.map((contract) => {
    const row = node("div", "", "contract");
    row.append(node("strong", chainNames[contract.chain_id] || String(contract.chain_id)),
      node("p", contract.address), node("p", contract.attribution.status),
      node("p", contract.attribution.sources.join(" | ")));
    return row;
  });
  byId("contracts").replaceChildren(...rows);
  reviewed = {approval_id: preview.approval_id};
  byId("review").hidden = false;
  confirmInput.focus();
}

function details(label, content) {
  const result = node("details");
  result.append(node("summary", label));
  // Render only text. Provider and project strings have no HTML or command execution role.
  result.append(node("pre", JSON.stringify(content, null, 2)));
  return result;
}

function metric(label, value, complete) {
  const element = node("div", "", "measure");
  let shown = value === null || value === undefined ? "Unknown" : String(value);
  if (value === 0 && complete === false) shown = "Unknown (0 observed)";
  element.append(node("strong", shown), node("span", label));
  return element;
}

function coverageStatus(coverage) {
  if (coverage.complete) return "Complete for this scope";
  if (!coverage.status || coverage.status === "unknown") return "Coverage unknown";
  return "Partial coverage";
}

function gapList(coverage) {
  const gaps = coverage.gaps || [];
  if (!gaps.length) return null;
  const box = node("details");
  box.append(node("summary", "Evidence gaps (" + gaps.length + ")"));
  const list = node("ul");
  gaps.slice(0, 20).forEach((gap) => list.append(node("li", gap.reason || gap.category || "unclassified gap")));
  if (gaps.length > 20) list.append(node("li", "First 20 shown. Export JSON for all gaps."));
  box.append(list);
  return box;
}

function conclusionsPanel(conclusions) {
  const box = node("details");
  box.open = true;
  box.append(node("summary", "Facts, inferences and unknowns"));
  const facts = conclusions.facts || [];
  const inferences = conclusions.inferences || [];
  const unknown = conclusions.unknown || [];
  const list = (title, items, label) => {
    const wrap = node("section");
    wrap.append(node("h4", title + " (" + items.length + ")"));
    if (!items.length) { wrap.append(node("p", "None recorded.")); return wrap; }
    const ul = node("ul");
    items.slice(0, 25).forEach((item) => ul.append(node("li", label(item))));
    if (items.length > 25) ul.append(node("li", "First 25 shown. Export JSON for all records."));
    wrap.append(ul);
    return wrap;
  };
  box.append(
    list("Established facts", facts, (f) => f.scope === "observed_evidence"
      ? "Chain " + f.chain_id + " · " + f.transactions + " transactions, " + f.events +
        " events · " + (f.coverage_complete ? "complete coverage" : "observed sample")
      : "Chain " + f.chain_id + " · counts withheld: " + (f.reason || "coverage incomplete")),
    list("Inferences (not facts)", inferences, (i) => "Chain " + i.chain_id + " · " + i.type +
      " · " + (i.coverage_complete ? "complete coverage" : "partial coverage")),
    list("Unknowns and evidence gaps", unknown,
      (u) => "Chain " + u.chain_id + " · " + (u.reason || u.category || "unknown")),
    node("p", (conclusions.interpretation || {}).note || "Missing data is not zero activity.")
  );
  return box;
}

function transactionLink(url) {
  try {
    const target = new URL(url);
    const known = new Set(["etherscan.io", "basescan.org", "testnet.monadscan.com"]);
    if (target.protocol !== "https:" || !known.has(target.hostname) || target.username || target.password ||
        target.port || !/^\/tx\/0x[0-9a-f]{64}$/i.test(target.pathname)) return null;
    const link = node("a", "Open transaction in explorer");
    link.href = target.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch {
    return null;
  }
}

function trend(data) {
  const days = Object.entries(data || {});
  if (days.length === 0) return node("p", "No daily buckets for this scope.");
  const panel = node("figure", "", "chart");
  panel.append(node("figcaption", "Daily top-level transactions (UTC)"));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 480 100");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", days.map(([date, v]) => date + ": " + (v.transactions ?? "unknown")).join(", "));
  const maximum = Math.max(1, ...days.map(([, v]) => v.transactions || 0));
  const width = 480 / days.length;
  days.forEach(([date, row], i) => {
    const value = row.transactions;
    const bar = document.createElementNS(svg.namespaceURI, "rect");
    const height = value === null ? 6 : Math.max(2, 90 * value / maximum);
    [["x", i * width], ["y", 97 - height], ["width", Math.max(2, width - 3)], ["height", height],
     ["class", value === null ? "unknown-bar" : "known-bar"]].forEach(([k, v]) => bar.setAttribute(k, v));
    const tip = document.createElementNS(svg.namespaceURI, "title");
    tip.textContent = date + ": " + (value ?? "unknown");
    bar.append(tip); svg.append(bar);
  });
  panel.append(svg, node("p", days[0][0] + " to " + days[days.length - 1][0] + ". Muted bars indicate unknown coverage."));
  return panel;
}

function evidenceConnections(graph, catalog) {
  const box = node("details");
  box.append(node("summary", "Evidence graph (" + graph.facts.length + " fact edges)"));
  const table = node("div", "", "connections");
  const short = (value) => value.length <= 28 ? value : value.slice(0, 19) + "…" + value.slice(-6);
  graph.facts.slice(0, 20).forEach((edge) => {
    const row = node("div", "", "connection");
    row.append(node("span", short(edge.from)), node("strong", edge.relation.replaceAll("_", " ")),
      node("span", short(edge.to)));
    const evidence = details("Evidence reference", {id: edge.evidence_ref, ...catalog[edge.evidence_ref]});
    evidence.className = "connection-ref";
    table.append(row, evidence);
  });
  box.append(table, details("Curator attribution", graph.curator_attribution));
  return box;
}

function renderChain(id, chain) {
  const card = node("article", "", "chain");
  const title = node("div", "", "section-title");
  title.append(node("h3", chainNames[id] || id), node("span", coverageStatus(chain.coverage), "chip"));
  card.append(title);
  const report = chain.report;
  const complete = Boolean(chain.coverage.complete);
  const bound = report.resolved_range;
  if (bound) {
    const line = bound.from_time_inclusive ?
      bound.from_time_inclusive + " to " + bound.to_time_exclusive :
      "Blocks " + bound.start_block + "–" + bound.end_block;
    card.append(node("p", line));
  }
  const metrics = node("div", "", "metrics");
  const values = report.deterministic_metrics;
  metrics.append(metric("Top-level transactions", values?.transactions, complete),
                 metric("Verified events", values?.events, complete),
                 metric("Observed counterparties", chain.analysis?.observed_counterparties, complete));
  card.append(metrics);
  const gaps = gapList(chain.coverage);
  if (gaps) card.append(gaps);
  card.append(details("Coverage and sources", {coverage: chain.coverage, sources: report.data_sources, cross_check: chain.cross_check}));
  card.append(details("Contracts and attribution", report.deployments));
  if (chain.analysis) {
    card.append(details("Daily activity and investigation candidates",
      {daily: chain.analysis.daily_utc, counterparty_evidence: chain.analysis.counterparty_evidence,
       candidates: chain.analysis.investigation_candidates, metrics: values}));
    const graph = chain.analysis.graph;
    card.append(trend(chain.analysis.daily_utc));
    card.append(evidenceConnections(graph, report.evidence_index));
  }
  const evidence = node("details");
  const entries = Object.entries(report.evidence_index);
  evidence.append(node("summary", "Evidence records (" + entries.length + ")"));
  if (entries.length > 50) evidence.append(node("p", "First 50 records shown. Export JSON for all records."));
  entries.slice(0, 50).forEach(([ref, item]) => {
    const parts = item.pointer.split("/");
    const raw = report.on_chain_evidence[parts[2]]?.[Number(parts[3])];
    const entry = details(ref, {record: raw, reference: item});
    const link = transactionLink(item.explorer_url);
    if (link) entry.append(link);
    evidence.append(entry);
  });
  card.append(evidence);
  return card;
}

function renderState(state) {
  const outcome = byId("outcome");
  outcome.textContent = statuses[state.status] || state.status;
  outcome.dataset.level = state.status;
  byId("job-id").textContent = "Investigation " + state.id;
  const list = node("ol");
  (state.logs || []).slice(-8).forEach((log) => {
    list.append(node("li", phases[log.phase] || log.phase));
  });
  byId("progress").replaceChildren(list);
  resumeAction.hidden = state.status === "complete" || state.attempts >= 3;
  byId("resume").textContent = state.status === "running" ? "Resume if interrupted" : "Resume this investigation";
  byId("resume").disabled = state.status === "queued";
  if (state.issues?.length && !state.result_ready) reportArea.replaceChildren(details("Investigation issues", state.issues));
}

async function follow(id) {
  if (!/^[0-9a-f]{32}$/.test(id)) return;
  currentJob = id;
  const sequence = ++monitor;
  resultExport.hidden = true;
  htmlExport.hidden = true;
  reportArea.replaceChildren();
  while (sequence === monitor) {
    const state = await fetchJson("/api/jobs/" + id);
    if (sequence !== monitor) break;
    renderState(state);
    if (state.result_ready) {
      const path = "/api/jobs/" + id + "/result";
      const result = await fetchJson(path);
      if (sequence !== monitor) break;
      reportArea.replaceChildren(node("h3", result.project), ...Object.entries(result.chains).map(([key, value]) => renderChain(key, value)));
      if (result.issues.length) reportArea.append(details("Investigation issues", result.issues));
      reportArea.prepend(conclusionsPanel(result.conclusions || {}));
      resultExport.hidden = false;
      htmlExport.hidden = false;
      break;
    }
    if (state.status !== "running" && state.status !== "queued") break;
    await new Promise((resolve) => setTimeout(resolve, 1100));
  }
}

function postOptions(payload) {
  return {method: "POST", headers: {"Content-Type": "application/json", "X-ChainInquest": "local"},
          body: JSON.stringify(payload)};
}

manifest.addEventListener("input", resetReview);
byId("objective").addEventListener("input", resetReview);
byId("access-token").addEventListener("input", () => {
  resetReview();
  monitor += 1;
  currentJob = null;
  reportArea.replaceChildren();
  byId("progress").replaceChildren();
  byId("job-id").textContent = "";
  byId("outcome").textContent = "Ready to investigate";
  resumeAction.hidden = true;
  resultExport.hidden = true;
  htmlExport.hidden = true;
});
byId("open-job").addEventListener("click", () => follow(location.hash.slice(1)).catch(showError));
byId("example").addEventListener("click", async () => {
  try {
    resetReview();
    manifest.value = JSON.stringify(await fetchJson("/api/examples/circle"), null, 2);
    manifest.focus();
  } catch (error) { showError(error); }
});
byId("scope-form").addEventListener("submit", (event) => {
  event.preventDefault();
  byId("error").hidden = true;
  displayReview().catch(showError);
});
confirmInput.addEventListener("change", () => { start.disabled = !confirmInput.checked; });
start.addEventListener("click", async () => {
  if (!reviewed || !confirmInput.checked) return;
  start.disabled = true;
  byId("error").hidden = true;
  try {
    const job = await fetchJson("/api/jobs", postOptions(reviewed));
    resetReview();
    history.replaceState(null, "", "#" + job.id);
    await follow(job.id);
  } catch (error) { showError(error); }
  finally { start.disabled = !confirmInput.checked; }
});
byId("resume").addEventListener("click", async () => {
  try {
    byId("error").hidden = true;
    await fetchJson("/api/jobs/" + currentJob + "/resume", postOptions({}));
    await follow(currentJob);
  } catch (error) { showError(error); }
});
async function download(kind) {
  const headers = new Headers();
  const token = byId("access-token").value.trim();
  if (token) headers.set("Authorization", "Bearer " + token);
  const response = await fetch("/api/jobs/" + currentJob + "/" + (kind === "json" ? "result" : "html"), {headers});
  if (!response.ok) throw new Error("The report export is unavailable.");
  const url = URL.createObjectURL(await response.blob());
  const link = node("a");
  link.href = url;
  link.download = "chaininquest-" + currentJob + "." + kind;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
resultExport.addEventListener("click", () => download("json").catch(showError));
htmlExport.addEventListener("click", () => download("html").catch(showError));
fetchJson("/api/config").then((config) => {
  byId("auth").hidden = !config.authentication_required;
  if (!config.authentication_required && location.hash) follow(location.hash.slice(1)).catch(showError);
}).catch(showError);
