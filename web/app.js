"use strict";

const byId = (id) => document.getElementById(id);
const manifest = byId("manifest");
const confirmInput = byId("confirmed");
const start = byId("start");
const reportArea = byId("results");
const resultExport = byId("export");
const resumeAction = byId("resume-action");
const chainNames = {1: "Ethereum Mainnet", 8453: "Base Mainnet", 10143: "Monad Testnet"};
const statuses = {queued: "Waiting to start", running: "Investigating", complete: "Investigation complete", partial: "Report ready with coverage gaps", failed: "Investigation failed"};
const phases = {planning: "Plan prepared", pinned_snapshot: "Snapshot pinned", collecting: "Collecting chain evidence", address_checkpoint: "Contract report saved", cross_checking: "Checking evidence", report_ready: "Report ready", failed: "Investigation stopped"};
let reviewed = null;
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
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "The local server could not complete the request.");
  return payload;
}

function resetReview() {
  reviewed = null;
  confirmInput.checked = false;
  start.disabled = true;
  byId("review").hidden = true;
}

function displayReview() {
  const payload = JSON.parse(manifest.value);
  if (!Array.isArray(payload.manifest?.deployments) || payload.manifest.deployments.length === 0) {
    throw new Error("Add at least one deployment and a confirmed contract to the project request.");
  }
  const rows = payload.manifest.deployments.flatMap((deployment) => {
    if (!Array.isArray(deployment.contracts)) throw new Error("Each deployment needs a contract list.");
    return deployment.contracts.map((contract) => {
      const row = node("div", "", "contract");
      row.append(
        node("strong", chainNames[deployment.chain_id] || String(deployment.chain_id)),
        node("p", String(contract.address)),
        node("p", String(contract.attribution?.status || "unconfirmed")),
        node("p", Array.isArray(contract.attribution?.sources) ? contract.attribution.sources.join(" | ") : "")
      );
      return row;
    });
  });
  byId("contracts").replaceChildren(...rows);
  reviewed = payload;
  confirmInput.checked = false;
  start.disabled = true;
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

function metric(label, value) {
  const element = node("div", "", "measure");
  element.append(node("strong", value === null || value === undefined ? "Unknown" : String(value)), node("span", label));
  return element;
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

function renderChain(id, chain) {
  const card = node("article", "", "chain");
  const title = node("div", "", "section-title");
  title.append(node("h3", chainNames[id] || id), node("span", chain.coverage.complete ? "Complete for this scope" : "Partial coverage", "chip"));
  card.append(title);
  const report = chain.report;
  const bound = report.resolved_range;
  if (bound) {
    const line = bound.from_time_inclusive ?
      bound.from_time_inclusive + " to " + bound.to_time_exclusive :
      "Blocks " + bound.start_block + "–" + bound.end_block;
    card.append(node("p", line));
  }
  const metrics = node("div", "", "metrics");
  const values = report.deterministic_metrics;
  metrics.append(metric("Top-level transactions", values?.transactions),
                 metric("Verified events", values?.events),
                 metric("Observed counterparties", chain.analysis?.observed_counterparties));
  card.append(metrics);
  card.append(details("Coverage and sources", {coverage: chain.coverage, sources: report.data_sources, cross_check: chain.cross_check}));
  card.append(details("Contracts and attribution", report.deployments));
  if (chain.analysis) {
    card.append(details("Daily activity and investigation candidates",
      {daily: chain.analysis.daily_utc, counterparty_evidence: chain.analysis.counterparty_evidence,
       candidates: chain.analysis.investigation_candidates, metrics: values}));
    const graph = chain.analysis.graph;
    card.append(details("Evidence relationships",
      {total_facts: graph.facts.length, displayed: graph.facts.slice(0, 40), curator_attribution: graph.curator_attribution}));
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
      resultExport.href = path;
      resultExport.hidden = false;
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
  try { displayReview(); } catch (error) { showError(error); }
});
confirmInput.addEventListener("change", () => { start.disabled = !confirmInput.checked; });
start.addEventListener("click", async () => {
  if (!reviewed || !confirmInput.checked) return;
  start.disabled = true;
  byId("error").hidden = true;
  try {
    const job = await fetchJson("/api/jobs", postOptions(reviewed));
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
if (location.hash) follow(location.hash.slice(1)).catch(showError);
