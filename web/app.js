"use strict";

const byId = (id) => document.getElementById(id);
const manifestInput = byId("manifest");
const confirmInput = byId("confirmed");
const start = byId("start");
const reportArea = byId("results");
const resultExport = byId("export");
const htmlExport = byId("export-html");
const resumeAction = byId("resume-action");
const chainNames = {1: "Ethereum Mainnet", 8453: "Base Mainnet", 10143: "Monad Testnet"};
const statuses = {queued: "Waiting to start", running: "Investigating", complete: "Investigation complete", partial: "Report ready with coverage gaps", failed: "Investigation failed"};
const phases = {planning: "Plan prepared", pinned_snapshot: "Snapshot pinned", collecting: "Collecting chain evidence", address_checkpoint: "Contract report saved", supplementing: "Supplementary investigation", cross_checking: "Checking evidence", report_ready: "Report ready", failed: "Investigation stopped"};
const constraints = {
  membership_from_manifest_only: "Only the contracts you confirmed are investigated.",
  read_only_rpc: "Every chain request is read-only; nothing is signed or broadcast.",
  model_cannot_change_scope_budget_or_membership: "The optional planner cannot change scope, budget or membership.",
  no_unverified_risk_scores: "No risk score is produced; counts and gaps are shown instead.",
  missing_data_is_not_zero_activity: "Missing evidence is unknown, never zero activity."
};
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
  const guides = {
    authentication_required: ["Enter your beta access token.", "The token is shared privately by the operator."],
    review_required: ["Preview the plan again.", "The previous confirmation expired or was already used."],
    api_rate_limit: ["Request limit reached.", "Wait a moment, then retry."],
    demo_busy: ["Another investigation is running.", "Wait for it to finish, then retry."],
    invalid_plan: ["Check the project name, contracts, source and scope.", "Every contract needs 0x + 40 hex characters and an HTTPS source page."],
    invalid_job: ["This investigation is not in the current store.", "Check that you opened the same account and store used to create it."],
    attempt_budget: ["This investigation reached its three-attempt limit.", "Start a new investigation with the same contracts and scope."],
    index_timeout: ["A data source did not answer in time.", "Coverage is partial; resume to retry the missing part."],
    index_transport: ["A data source could not be reached.", "Coverage is partial; resume later or export the JSON report."],
    index_key_required: ["This network needs a configured index key for long ranges.", "Coverage is unknown, not zero. Use a short block range or ask the operator to configure a key."],
    snapshot_changed: ["The chain head changed during the run.", "Start a fresh investigation to pin a new snapshot."],
    wrong_chain: ["The RPC endpoint answers for a different network.", "Ask the operator to fix the network configuration."],
    invalid_range: ["The block or time range is not valid for this scope.", "Use 7/30 days, 1–32 recent blocks, or a span under 32 blocks."],
    invalid_host: ["The page is not served from the configured beta origin.", "Open the exact address printed by the operator."],
    same_origin_required: ["The browser blocked a cross-origin request.", "Reload the page from the investigation address."],
    not_ready: ["The report is still being written.", "Wait a moment and retry."],
    input_budget: ["The request is too large (128 KiB limit).", "Reduce the manifest to the confirmed contracts only."]
  };
  const code = error.message;
  const guide = guides[code];
  const output = byId("error");
  output.replaceChildren();
  output.append(node("strong", guide ? guide[0] : "The request failed."));
  if (guide) output.append(node("span", " " + guide[1]));
  else if (code && code.length < 200) output.append(node("span", " " + code));
  output.hidden = false;
}

async function fetchJson(path, options) {
  const headers = new Headers(options?.headers || {});
  const token = byId("access-token").value.trim();
  if (token) headers.set("Authorization", "Bearer " + token);
  let response;
  try {
    response = await fetch(path, {...options, headers});
  } catch {
    throw new Error("The investigation service is unreachable. Check that the terminal server is running.");
  }
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

function utcLabel(value) {
  const text = String(value).replace("+00:00", "Z");
  return text.replace("T", " ").replace("Z", " UTC");
}

function scopeRequest() {
  const choice = byId("scope").value;
  if (choice === "days7") return {days: 7};
  if (choice === "days30") return {days: 30};
  if (choice === "recent") {
    const blocks = Number(byId("recent-blocks").value);
    if (!Number.isInteger(blocks) || blocks < 1 || blocks > 32) {
      throw new Error("Recent blocks must be a whole number from 1 to 32.");
    }
    return {recent_blocks: blocks};
  }
  const startBlock = Number(byId("start-block").value);
  const endBlock = Number(byId("end-block").value);
  if (!Number.isInteger(startBlock) || !Number.isInteger(endBlock) || startBlock < 0 || endBlock < startBlock) {
    throw new Error("Enter a valid start block and an end block at or after it.");
  }
  if (endBlock - startBlock > 31) throw new Error("A block range covers at most 32 blocks (start + 31).");
  return {start_block: startBlock, end_block: endBlock};
}

function guidedManifest() {
  const project = byId("project").value.trim();
  if (!project) throw new Error("Enter the project name.");
  if (project.length > 120) throw new Error("The project name is limited to 120 characters.");
  const addresses = byId("addresses").value.split(/\s+/).filter(Boolean);
  if (!addresses.length) throw new Error("Add at least one contract address.");
  if (addresses.length > 8) throw new Error("This beta investigates at most 8 contracts per run.");
  const seen = new Set();
  for (const address of addresses) {
    if (!/^0x[0-9a-fA-F]{40}$/.test(address)) {
      throw new Error("Contract " + address + " is not 0x followed by 40 hexadecimal characters.");
    }
    if (seen.has(address.toLowerCase())) throw new Error("Duplicate contract address: " + address);
    seen.add(address.toLowerCase());
  }
  const source = byId("source").value.trim();
  if (!/^https:\/\/[^\s@?#]{1,500}$/.test(source)) {
    throw new Error("Add an HTTPS page that lists these contract addresses.");
  }
  return {
    schema_version: "1",
    project,
    deployments: [{
      id: "main",
      chain_id: Number(byId("chain").value),
      contracts: addresses.map((address) => ({
        address,
        attribution: {status: "confirmed", sources: [source]}
      }))
    }]
  };
}

function buildRequest() {
  let manifest;
  if (byId("json-mode").checked) {
    try {
      manifest = JSON.parse(manifestInput.value);
    } catch {
      throw new Error("The advanced manifest is not valid JSON.");
    }
    if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
      throw new Error("The advanced manifest must be a JSON object.");
    }
  } else {
    manifest = guidedManifest();
  }
  return {goal: byId("goal").value, scope: scopeRequest(), manifest};
}

function planSummary(plan) {
  const box = node("div", "", "summary");
  const chainIds = [...new Set(plan.entries.map((entry) => entry.chain_id))];
  chainIds.forEach((id) => box.append(node("span", chainNames[id] || String(id), "chip")));
  box.append(node("span", "Goal: " + plan.goal, "chip"));
  box.append(node("span", "Time budget: " + plan.budget_seconds + "s", "chip"));
  box.append(node("span", "Planner: " + (plan.planner?.mode || "deterministic"), "chip"));
  const ranges = Object.entries(plan.ranges || {}).map(([chain, range]) => {
    const name = chainNames[chain] || chain;
    if (range.from_time) return name + ": " + utcLabel(range.from_time) + " → " + utcLabel(range.to_time);
    if (range.recent_blocks) return name + ": last " + range.recent_blocks + " blocks";
    return name + ": blocks " + range.start_block + "–" + range.end_block;
  });
  const list = node("ul");
  ranges.forEach((line) => list.append(node("li", line)));
  return [box, list];
}

function planSteps(plan) {
  if (!plan.plan?.steps) return null;
  const wrap = node("div");
  wrap.append(node("h4", "What will be checked"));
  const list = node("ol");
  plan.plan.steps.forEach((step) => list.append(node("li", step.purpose || step.id)));
  wrap.append(list);
  if (plan.plan.constraints?.length) {
    const rules = node("ul", undefined, "rules");
    plan.plan.constraints.forEach((key) => rules.append(node("li", constraints[key] || key)));
    wrap.append(node("h4", "Rules"), rules);
  }
  return wrap;
}

async function displayReview() {
  resetReview();
  byId("error").hidden = true;
  const generation = reviewGeneration;
  const request = buildRequest();
  const objective = byId("objective").value.trim();
  const preview = await fetchJson("/api/plan", postOptions(objective ? {...request, objective} : request));
  if (generation !== reviewGeneration) return;
  const plan = preview.plan;
  const planned = byId("planned");
  planned.replaceChildren(...planSummary(plan));
  const steps = planSteps(plan);
  if (steps) planned.append(steps);
  planned.append(details("Raw plan and budget (advanced)", plan));
  const rows = plan.entries.map((contract) => {
    const row = node("div", "", "contract");
    const status = contract.attribution.status;
    row.append(node("strong", chainNames[contract.chain_id] || String(contract.chain_id)),
      node("p", contract.address),
      node("p", "Declared attribution: " + status +
        (status === "confirmed" ? " (provided by you; not independently verified)" : "")),
      node("p", "Source: " + contract.attribution.sources.join(" | ")));
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

function streamStates(coverage) {
  const wrap = node("div", "", "streams");
  [["transactions", "Transactions"], ["events", "Events"], ["receipts", "Receipts"]].forEach(([key, label]) => {
    const part = coverage[key];
    if (!part) return;
    const state = part.complete ? "complete" : (part.status || (part.observed ? "partial" : "unknown"));
    const gaps = (part.gaps || []).length;
    const chip = node("span", label + " · " + state + (gaps ? " · " + gaps + " gap" + (gaps === 1 ? "" : "s") : ""), "stream");
    chip.dataset.state = state;
    wrap.append(chip);
  });
  return wrap;
}

function coverageBox(coverage) {
  const box = node("div", "", "coverage-box");
  box.append(node("strong", coverageStatus(coverage)), streamStates(coverage));
  return box;
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
  const list = (title, items, label, help) => {
    const wrap = node("section");
    wrap.append(node("h4", title + " (" + items.length + ")"), node("p", help, "facts-help"));
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
      : "Chain " + f.chain_id + " · counts withheld: " + (f.reason || "coverage incomplete"),
      "Only records verified against chain data in the stated scope."),
    list("Inferences (not facts)", inferences, (i) => "Chain " + i.chain_id + " · " + i.type +
      " · " + (i.coverage_complete ? "complete coverage" : "partial coverage"),
      "Rule-based candidates that need your own review; they are not proof."),
    list("Unknowns and evidence gaps", unknown,
      (u) => "Chain " + u.chain_id + " · " + (u.reason || u.category || "unknown"),
      "Missing or failed evidence. An unknown range does not mean zero activity."),
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
  card.append(coverageBox(chain.coverage));
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

function elapsedSeconds(state) {
  const created = Date.parse(state.created_at || "");
  if (Number.isNaN(created)) return null;
  const ended = state.status === "complete" || state.status === "partial" || state.status === "failed";
  const end = ended ? Date.parse(state.updated_at || "") : Date.now();
  if (Number.isNaN(end)) return null;
  return Math.max(0, Math.round((end - created) / 1000));
}

function recoveryPanel(state) {
  const panel = byId("recovery");
  if (state.status === "partial") {
    panel.replaceChildren(
      node("strong", "Some evidence is missing."),
      node("p", "The report is ready and keeps every gap visible. Resume to reuse saved checkpoints and retry the missing part (" + state.attempts + " of 3 attempts used).")
    );
    panel.hidden = false;
  } else if (state.status === "failed") {
    panel.replaceChildren(
      node("strong", "The run stopped before a report."),
      node("p", "Completed checkpoints are kept. Resume to continue and reuse collected evidence (" + state.attempts + " of 3 attempts used).")
    );
    panel.hidden = false;
  } else {
    panel.replaceChildren();
    panel.hidden = true;
  }
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
  const elapsed = elapsedSeconds(state);
  const budget = state.plan?.budget_seconds;
  const running = state.status === "running" || state.status === "queued";
  byId("run-status").hidden = !state.plan;
  if (state.plan) {
    const shown = elapsed === null ? "elapsed unknown" : elapsed + "s elapsed";
    const phase = (state.logs || []).length ? phases[state.logs[state.logs.length - 1].phase] || state.logs[state.logs.length - 1].phase : "Starting";
    byId("run-detail").textContent = phase + " · " + shown + (budget ? " · " + budget + "s budget · attempt " + Math.max(1, state.attempts) + " of 3" : "");
    const percent = !running ? 100 : budget && elapsed !== null ? Math.min(99, Math.round(100 * elapsed / budget)) : 0;
    byId("job-progress").value = percent;
  }
  recoveryPanel(state);
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
  byId("feedback").hidden = true;
  byId("feedback-form").hidden = false;
  byId("feedback-done").hidden = true;
  reportArea.replaceChildren();
  while (sequence === monitor) {
    const state = await fetchJson("/api/jobs/" + id);
    if (sequence !== monitor) break;
    renderState(state);
    if (state.result_ready) {
      const path = "/api/jobs/" + id + "/result";
      const result = await fetchJson(path);
      if (sequence !== monitor) break;
      const chains = Object.entries(result.chains);
      const complete = chains.filter(([, value]) => value.coverage.complete).length;
      const gaps = chains.reduce((total, [, value]) => total + (value.coverage.gaps || []).length, 0);
      reportArea.replaceChildren(
        node("p", complete + " of " + chains.length + " networks complete for the stated scope" +
          (gaps ? " · " + gaps + " evidence gap" + (gaps === 1 ? "" : "s") + " listed" : " · no evidence gaps"), "status-line"),
        conclusionsPanel(result.conclusions || {}),
        node("h3", result.project),
        ...chains.map(([key, value]) => renderChain(key, value))
      );
      if (result.issues.length) reportArea.append(details("Investigation issues", result.issues));
      resultExport.hidden = false;
      htmlExport.hidden = false;
      byId("feedback").hidden = false;
      byId("feedback-form").hidden = false;
      byId("feedback-done").hidden = true;
      break;
    }
    if (state.status !== "running" && state.status !== "queued") break;
    const updated = Date.parse(state.updated_at || "");
    const limit = 1000 * ((state.plan?.budget_seconds || 120) + 90);
    if (state.status === "running" && !Number.isNaN(updated) && Date.now() - updated > limit) {
      byId("outcome").textContent = "Run appears interrupted";
      byId("recovery").replaceChildren(
        node("strong", "This run stopped updating."),
        node("p", "The server may have restarted. Resume to continue from the last checkpoint; saved evidence is kept.")
      );
      byId("recovery").hidden = false;
      resumeAction.hidden = false;
      byId("resume").disabled = false;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 1100));
  }
}

function postOptions(payload) {
  return {method: "POST", headers: {"Content-Type": "application/json", "X-ChainInquest": "local"},
          body: JSON.stringify(payload)};
}

function clearOutput() {
  monitor += 1;
  currentJob = null;
  reportArea.replaceChildren();
  byId("progress").replaceChildren();
  byId("job-id").textContent = "";
  byId("outcome").textContent = "Ready to investigate";
  resumeAction.hidden = true;
  byId("run-status").hidden = true;
  byId("recovery").hidden = true;
  resultExport.hidden = true;
  htmlExport.hidden = true;
  byId("feedback").hidden = true;
}

const scopeFields = () => {
  byId("recent-scope").hidden = byId("scope").value !== "recent";
  byId("range-scope").hidden = byId("scope").value !== "range";
};

byId("scope").addEventListener("change", () => { scopeFields(); resetReview(); });
["project", "addresses", "source", "chain", "goal", "recent-blocks", "start-block", "end-block", "objective"].forEach((id) => {
  byId(id).addEventListener("input", resetReview);
});
manifestInput.addEventListener("input", resetReview);
byId("json-mode").addEventListener("change", () => {
  const manual = byId("json-mode").checked;
  ["project", "chain", "addresses", "source"].forEach((id) => { byId(id).disabled = manual; });
  resetReview();
});
byId("access-token").addEventListener("input", () => { resetReview(); clearOutput(); });
byId("open-job").addEventListener("click", () => follow(location.hash.slice(1)).catch(showError));
byId("example").addEventListener("click", async () => {
  try {
    resetReview();
    byId("error").hidden = true;
    const example = await fetchJson("/api/examples/circle");
    const deployment = example.manifest.deployments[0];
    byId("project").value = example.manifest.project;
    byId("chain").value = String(deployment.chain_id);
    byId("addresses").value = deployment.contracts.map((contract) => contract.address).join("\n");
    byId("source").value = deployment.contracts[0].attribution.sources[0];
    byId("goal").value = example.goal;
    byId("scope").value = "recent";
    byId("recent-blocks").value = String(example.scope.recent_blocks);
    byId("json-mode").checked = false;
    manifestInput.value = JSON.stringify(example.manifest, null, 2);
    scopeFields();
    byId("project").focus();
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
byId("feedback-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  byId("error").hidden = true;
  try {
    await fetchJson("/api/jobs/" + currentJob + "/feedback", postOptions({
      completed: byId("fb-completed").value,
      understood: byId("fb-understood").value,
      incomplete: byId("fb-incomplete").value,
      reuse: byId("fb-reuse").value,
      comment: byId("fb-comment").value
    }));
    byId("feedback-form").hidden = true;
    byId("feedback-done").hidden = false;
  } catch (error) { showError(error); }
});
fetchJson("/api/config").then((config) => {
  byId("auth").hidden = !config.authentication_required;
  if (!config.authentication_required && location.hash) follow(location.hash.slice(1)).catch(showError);
}).catch(showError);
