"""Structured, bounded feedback for closed-beta investigations."""

import datetime as dt

from evidence import QueryError
from jobs import write

CHOICES = {
    "completed": {"yes", "partly", "no"},
    "understood": {"yes", "partly", "no"},
    "incomplete": {"yes", "no", "unsure"},
    "reuse": {"yes", "maybe", "no"},
}
MAX_COMMENT = 500


def elapsed(state):
    try:
        started = dt.datetime.fromisoformat(state["created_at"])
        ended = dt.datetime.fromisoformat(state["updated_at"])
        return max(0, round((ended - started).total_seconds()))
    except (KeyError, TypeError, ValueError):
        return None


def record(directory, job_id, state, result, payload):
    """Validate one feedback submission and persist it beside the job."""
    if not isinstance(payload, dict) or set(payload) - {
        "completed",
        "understood",
        "incomplete",
        "reuse",
        "comment",
    }:
        raise QueryError("Complete the feedback questions", "invalid_feedback")
    answers = {}
    for key, allowed in CHOICES.items():
        value = payload.get(key)
        if value not in allowed:
            raise QueryError("Complete the feedback questions", "invalid_feedback")
        answers[key] = value
    comment = payload.get("comment", "")
    if not isinstance(comment, str):
        raise QueryError("Feedback comment must be text", "invalid_feedback")
    comment = comment.strip()
    if len(comment) > MAX_COMMENT:
        raise QueryError("Feedback comment is limited to 500 characters", "invalid_feedback")
    chains = (result or {}).get("chains", {})
    document = {
        "schema_version": "1",
        "job_id": job_id,
        "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed(state),
        "coverage": {
            "chains": len(chains),
            "complete": sum(1 for chain in chains.values() if chain["coverage"]["complete"]),
            "gaps": sum(len(chain["coverage"].get("gaps", [])) for chain in chains.values()),
        },
        "answers": answers,
        "comment": comment,
    }
    write(directory / "feedback.json", document, 16 * 1024)
    return document
