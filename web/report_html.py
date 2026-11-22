"""Portable inert investigation export, rendered without client-side scripts."""

import json
from html import escape


def text(value):
    return escape(str(value), quote=True)


def json_block(value):
    return "<pre>" + text(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"


def render(result):
    fragments = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>ChainInquest investigation</title></head><body><main>",
        f"<h1>{text(result['project'])}</h1>",
        f"<p>Investigation {text(result['id'])}. Goal: {text(result['goal'])}. "
        f"Coverage: {'complete for the stated scope' if result['complete'] else 'partial'}.</p>",
        "<h2>Conclusions</h2>",
        json_block(result.get("conclusions", {"unknown": ["Report predates conclusion sections"]})),
    ]
    for chain, item in result["chains"].items():
        r = item["report"]
        fragments.extend(
            [
                f"<section><h2>Chain {text(chain)}</h2>",
                "<h3>Range and sources</h3>",
                json_block({"range": r["resolved_range"], "sources": r["data_sources"]}),
                "<h3>Coverage and corroboration</h3>",
                json_block({"coverage": item["coverage"], "secondary": item.get("cross_check")}),
                "<h3>Metrics</h3>",
                json_block(r["deterministic_metrics"]),
                "<details><summary>Evidence catalog and raw records</summary>",
                json_block({"catalog": r["evidence_index"], "records": r["on_chain_evidence"]}),
                "</details></section>",
            ]
        )
    fragments.append("</main></body></html>")
    return "".join(fragments).encode("utf-8")
