"""Portable inert investigation export, rendered without client-side scripts."""

import json
from html import escape


def text(value):
    return escape(str(value), quote=True)


def json_block(value):
    return "<pre>" + text(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"


def table(headers, rows):
    if not rows:
        return "<p>None recorded.</p>"
    head = "".join(f"<th>{text(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{text(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def observed(value, complete):
    """Never present missing coverage as a zero count."""
    if value is None:
        return "Unknown"
    if value == 0 and not complete:
        return "Unknown (0 observed)"
    return str(value)


def status_of(coverage):
    if coverage.get("complete"):
        return "complete"
    return coverage.get("status") or ("partial" if coverage.get("observed") else "unknown")


def coverage_rows(item):
    coverage = item.get("coverage") or {}
    rows = []
    for name in ("transactions", "events", "receipts", "token_metadata"):
        part = coverage.get(name)
        if not isinstance(part, dict):
            continue
        rows.append(
            [
                name,
                status_of(part),
                part.get("method", ""),
                "yes" if part.get("observed") else "no",
                "yes" if part.get("attested") else "no",
                ", ".join(str(gap.get("reason", gap)) for gap in part.get("gaps") or []),
            ]
        )
    return rows


def fact_rows(facts):
    return [
        [
            fact.get("chain_id", ""),
            fact.get("scope", ""),
            fact.get("transactions", ""),
            fact.get("events", ""),
            "complete" if fact.get("coverage_complete") else "observed sample",
            len(fact.get("proof") or []),
        ]
        for fact in facts
    ]


def inference_rows(inferences):
    return [
        [
            item.get("chain_id", ""),
            item.get("type", ""),
            "complete" if item.get("coverage_complete") else "partial",
            len(item.get("evidence_refs") or []),
        ]
        for item in inferences
    ]


def unknown_rows(unknown):
    rows = []
    for item in unknown:
        detail = item.get("reason") or item.get("category") or item.get("scope") or ""
        evidence = item.get("proof") or item.get("evidence_refs") or []
        rows.append(
            [item.get("chain_id", ""), detail, len(evidence), item.get("coverage_status", "")]
        )
    return rows


def daily_table(daily, complete):
    rows = []
    for day, values in (daily or {}).items():
        rows.append(
            [
                day,
                observed(values.get("transactions"), complete),
                observed(values.get("Transfer"), complete),
                observed(values.get("Approval"), complete),
            ]
        )
    return table(["Date (UTC)", "Transactions", "Transfer", "Approval"], rows)


def graph_section(analysis, catalog):
    graph = analysis.get("graph") or {}
    facts = graph.get("facts") or []
    rows = [
        [
            edge.get("from", ""),
            edge.get("relation", ""),
            edge.get("to", ""),
            edge.get("evidence_ref", ""),
        ]
        for edge in facts[:200]
    ]
    note = ""
    if len(facts) > 200:
        note = f"<p>First 200 of {len(facts)} fact edges shown. Export JSON for all edges.</p>"
    return (
        "<h3>Evidence graph</h3>"
        + table(["From", "Relation", "To", "Evidence"], rows)
        + note
        + "<details><summary>Curator attribution edges</summary>"
        + json_block((analysis.get("graph") or {}).get("curator_attribution") or [])
        + "</details>"
        + "<details><summary>Evidence catalog</summary>"
        + json_block(catalog)
        + "</details>"
    )


def render(result):
    conclusions = result.get("conclusions") or {}
    facts = conclusions.get("facts") or []
    inferences = conclusions.get("inferences") or []
    unknown = conclusions.get("unknown") or []
    gaps = conclusions.get("evidence_gaps") or []
    interpretation = conclusions.get("interpretation") or {}
    complete = bool(result.get("complete")) and interpretation.get("coverage_complete", True)
    fragments = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>ChainInquest investigation</title></head><body><main>",
        f"<h1>{text(result.get('project', 'Investigation'))}</h1>",
        f"<p>Investigation {text(result.get('id', ''))}. Goal: {text(result.get('goal', ''))}. "
        f"Coverage: {text('complete for the stated scope' if complete else 'partial or unknown')}.</p>",
        "<h2>Coverage summary</h2>",
        table(
            ["Status", "Facts", "Inferences", "Unknowns", "Evidence gaps"],
            [
                [
                    "complete" if complete else "partial",
                    len(facts),
                    len(inferences),
                    len(unknown),
                    len(gaps),
                ]
            ],
        ),
        "<h2>Established facts</h2>",
        table(
            ["Chain", "Scope", "Transactions", "Events", "Coverage", "Proof references"],
            fact_rows(facts),
        ),
        "<h2>Inferences (candidates, not facts)</h2>",
        table(["Chain", "Type", "Coverage", "Evidence references"], inference_rows(inferences)),
        "<h2>Unknowns and evidence gaps</h2>",
        table(["Chain", "Reason", "Evidence references", "Coverage status"], unknown_rows(unknown)),
        f"<p>{text(interpretation.get('note', 'Missing data is not zero activity.'))}</p>",
    ]
    for chain, item in result.get("chains", {}).items():
        report = item.get("report") or {}
        coverage = item.get("coverage") or {}
        metrics = report.get("deterministic_metrics") or {}
        analysis = item.get("analysis") or {}
        chain_complete = bool(coverage.get("complete"))
        fragments.extend(
            [
                f"<section><h2>Chain {text(chain)}</h2>",
                f"<p>Coverage status: {text(status_of(coverage))}. "
                f"Range: {text(json.dumps(report.get('resolved_range')))}</p>",
                "<h3>Coverage by evidence class</h3>",
                table(
                    ["Class", "Status", "Method", "Observed", "Attested", "Gaps"],
                    coverage_rows(item),
                ),
                "<h3>Observed activity</h3>",
                table(
                    ["Metric", "Value"],
                    [
                        [
                            "Top-level transactions",
                            observed(metrics.get("transactions"), chain_complete),
                        ],
                        [
                            "Successful",
                            observed((metrics.get("status") or {}).get("success"), chain_complete),
                        ],
                        [
                            "Failed",
                            observed((metrics.get("status") or {}).get("failed"), chain_complete),
                        ],
                        [
                            "Verified events",
                            observed(metrics.get("events"), chain_complete),
                        ],
                        [
                            "Observed counterparties",
                            observed(analysis.get("observed_counterparties"), chain_complete),
                        ],
                    ],
                ),
                "<h3>Daily activity</h3>",
                daily_table(analysis.get("daily_utc"), chain_complete),
                graph_section(analysis, report.get("evidence_index") or {}),
                "<details><summary>Sources and cross-check</summary>",
                json_block(
                    {
                        "sources": report.get("data_sources"),
                        "cross_check": item.get("cross_check"),
                    }
                ),
                "</details>",
                "<details><summary>Raw JSON for this chain</summary>",
                json_block(item),
                "</details></section>",
            ]
        )
    fragments.append("</main></body></html>")
    return "".join(fragments).encode("utf-8")
