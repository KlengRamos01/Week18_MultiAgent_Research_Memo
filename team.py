"""Multi-agent research memo team.

Architecture
------------
Supervisor (typed handoffs only) -> Researcher (MCP stdio) -> Writer (shared
state only) -> Reviewer (memo + static rubric only). The supervisor never touches
the corpus or a worker tool, and each role has a bounded surface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

import llm
import mcp_client
from config import load_env

ROOT = Path(__file__).resolve().parent
load_env()

MAX_SUPERVISOR_TURNS = 8
MAX_REJECTIONS = 3
SUMMARY_TOKEN_LIMIT = 8000
SCHEMA_VERSION = 1

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_INTERRUPTED = "interrupted"
STATUS_MAX_TURNS = "max_turns"
STATUS_ESCALATED = "escalated_human"
STATUS_ERROR = "error"

# Injected for tests; production always uses the real provider client.
CHAT_JSON = llm.chat_json
CHAT_TEXT = llm.chat

# The supervisor may route only through these three typed handoff tools.
HANDOFF_TOOL_NAMES = (
    "hand_off_to_researcher",
    "hand_off_to_writer",
    "hand_off_to_reviewer",
)


class Snippet(TypedDict):
    text: str
    source: str
    relevance: str


class Memo(TypedDict):
    title: str
    body: str
    footnotes: list[str]


class Verdict(TypedDict):
    decision: Literal["accept", "revise"]
    reasons: list[str]


class Message(TypedDict):
    role: str
    node: str
    turn: int
    content: str
    tokens: int


class TraceEntry(TypedDict):
    at: str
    node: str
    turn: int
    handoff: str
    status: str
    note: str


class State(TypedDict):
    # Required shared scratchpad fields (assignment contract).
    topic: str
    snippets: list[Snippet]
    draft_memo: Memo | None
    review_verdict: Verdict | None
    turn_count: int
    summary: str
    status: str
    # Bookkeeping required for caps, resume, memory and metrics.
    topic_id: str
    run_id: str
    rejections: int
    verdict_history: list[Verdict]
    messages: list[Message]
    compacted_upto: int
    compaction_events: list[dict]
    transcript_tokens: int
    total_tokens: int
    mcp_calls: int
    mcp_evidence: list[dict]
    errors: list[str]
    trace: list[TraceEntry]
    created_at: str
    updated_at: str
    resume_count: int


# --------------------------------------------------------------------------- #
# Helpers: time, slugs, token estimates
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-")
    return cleaned or "run"


def _estimate_tokens(text: str) -> int:
    """Conservative character-based estimate; provider totals override it."""
    return max(0, math.ceil(len(text) / 4))


def _int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _summary_limit() -> int:
    return _int_env("TEAM_SUMMARY_TOKEN_LIMIT") or SUMMARY_TOKEN_LIMIT


# --------------------------------------------------------------------------- #
# Scratchpad persistence (atomic write, resume after kill)
# --------------------------------------------------------------------------- #
def _scratchpad_dir() -> Path:
    raw = os.getenv("TEAM_SCRATCHPAD_DIR", "scratchpad")
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def scratchpad_path(run_id: str) -> Path:
    return _scratchpad_dir() / f"run-{_slug(run_id)}.json"


def _persist_state(state: State) -> Path:
    """Write the full run state after every supervisor turn (atomic replace)."""
    state["updated_at"] = _now()
    path = scratchpad_path(state["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": state["run_id"],
        "topic_id": state["topic_id"],
        "topic": state["topic"],
        "status": state["status"],
        "turn_count": state["turn_count"],
        "updated_at": state["updated_at"],
        "state": state,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _initial_state(topic: str, topic_id: str) -> State:
    now = _now()
    return {
        "topic": topic,
        "topic_id": topic_id,
        "run_id": topic_id,
        "snippets": [],
        "draft_memo": None,
        "review_verdict": None,
        "turn_count": 0,
        "summary": "",
        "status": STATUS_RUNNING,
        "rejections": 0,
        "verdict_history": [],
        "messages": [],
        "compacted_upto": 0,
        "compaction_events": [],
        "transcript_tokens": 0,
        "total_tokens": 0,
        "mcp_calls": 0,
        "mcp_evidence": [],
        "errors": [],
        "trace": [],
        "created_at": now,
        "updated_at": now,
        "resume_count": 0,
    }


def _normalize_state(raw: dict | None, *, topic: str = "", topic_id: str = "") -> State:
    base = _initial_state(topic, topic_id)
    state: dict[str, Any] = {**base, **(raw or {})}
    state["topic"] = state.get("topic") or topic
    state["topic_id"] = state.get("topic_id") or topic_id
    state["run_id"] = state.get("run_id") or state["topic_id"] or topic_id
    for key in ("snippets", "verdict_history", "messages", "compaction_events",
                "mcp_evidence", "errors", "trace"):
        if not isinstance(state.get(key), list):
            state[key] = []
    for key in ("turn_count", "rejections", "compacted_upto", "transcript_tokens",
                "total_tokens", "mcp_calls", "resume_count"):
        try:
            state[key] = int(state.get(key) or 0)
        except (TypeError, ValueError):
            state[key] = 0
    state["status"] = str(state.get("status") or STATUS_RUNNING)
    state["summary"] = str(state.get("summary") or "")
    if state.get("draft_memo") is not None and not isinstance(state["draft_memo"], dict):
        state["draft_memo"] = None
    if state.get("review_verdict") is not None and not isinstance(state["review_verdict"], dict):
        state["review_verdict"] = None
    return state  # type: ignore[return-value]


def _load_state(run_id: str) -> State | None:
    path = scratchpad_path(run_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    raw = payload.get("state") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return None
    return _normalize_state(raw)


def _resume_state(topic: str, topic_id: str) -> tuple[State, bool]:
    """Resume an unfinished run; start fresh when the previous run is final."""
    saved = _load_state(topic_id)
    if saved and saved["status"] in {STATUS_RUNNING, STATUS_INTERRUPTED}:
        saved["resume_count"] += 1
        saved["status"] = STATUS_RUNNING
        saved["topic"] = topic or saved["topic"]
        saved["trace"] = list(saved["trace"]) + [{
            "at": _now(), "node": "run_topic", "turn": saved["turn_count"],
            "handoff": "resume", "status": STATUS_RUNNING,
            "note": f"Resumed from {scratchpad_path(topic_id).name}",
        }]
        _persist_state(saved)
        return saved, True
    return _initial_state(topic, topic_id), False


# --------------------------------------------------------------------------- #
# Typed handoff tools (the supervisor's only routing surface)
# --------------------------------------------------------------------------- #
def _require_tool(name: str) -> None:
    if name not in HANDOFF_TOOL_NAMES:
        raise ValueError(f"Unsupported handoff tool: {name}")


def _handoff(target: str, tool_name: str, update: dict | None) -> Command:
    _require_tool(tool_name)
    return Command(goto=target, update=dict(update or {}))


def hand_off_to_researcher(*, update: dict | None = None) -> Command:
    """Typed handoff tool: Supervisor -> Researcher (corpus retrieval)."""
    return _handoff("researcher", "hand_off_to_researcher", update)


def hand_off_to_writer(*, update: dict | None = None) -> Command:
    """Typed handoff tool: Supervisor -> Writer (memo drafting/revision)."""
    return _handoff("writer", "hand_off_to_writer", update)


def hand_off_to_reviewer(*, update: dict | None = None) -> Command:
    """Typed handoff tool: Supervisor -> Reviewer (rubric verdict)."""
    return _handoff("reviewer", "hand_off_to_reviewer", update)


HANDOFFS = {
    "hand_off_to_researcher": hand_off_to_researcher,
    "hand_off_to_writer": hand_off_to_writer,
    "hand_off_to_reviewer": hand_off_to_reviewer,
}


# --------------------------------------------------------------------------- #
# Supervisor
# --------------------------------------------------------------------------- #
def _turn_update(state: State, turn: int, note: str, status: str,
                 handoff: str | None) -> dict:
    message: Message = {
        "role": "supervisor",
        "node": "supervisor",
        "turn": turn,
        "content": f"Turn {turn}: {note}",
        "tokens": _estimate_tokens(note) + 4,
    }
    messages = [*state["messages"], message]
    entry: TraceEntry = {
        "at": _now(),
        "node": "supervisor",
        "turn": turn,
        "handoff": handoff or "end",
        "status": status,
        "note": note,
    }
    return {
        "turn_count": turn,
        "status": status,
        "messages": messages,
        "trace": [*state["trace"], entry],
        "transcript_tokens": _history_tokens(state, messages),
    }


def _summary_with_final_state(state: State, status: str) -> str:
    """Append a deterministic final-state line so later verdicts are not lost."""
    verdicts = state["verdict_history"]
    if not state["summary"] or not verdicts:
        return state["summary"]
    last = verdicts[-1]
    cited = sorted({f"P{number}" for number in re.findall(r"P(\d{2})",
                     memo_text(state.get("draft_memo")))})
    line = (f"Final state: {len(verdicts)} reviewer verdict(s); latest {last.get('decision')} "
            f"({'; '.join(last.get('reasons', []))[:180]}); draft {_draft_words(state)} words; "
            f"cited {', '.join(cited) or 'none'}; status {status}.")
    summary = state["summary"].rstrip()
    return summary if line in summary else summary + "\n" + line


def _finish(state: State, turn: int, status: str, note: str) -> Command:
    update = _turn_update(state, turn, note, status, handoff=None)
    merged = _normalize_state({**state, **update}, topic=state["topic"], topic_id=state["topic_id"])
    compaction = _maybe_compact(merged)
    if compaction:
        update = {**update, **compaction}
        merged = _normalize_state({**merged, **compaction}, topic=state["topic"],
                                  topic_id=state["topic_id"])
    final_summary = _summary_with_final_state(merged, status)
    if final_summary != merged["summary"]:
        update["summary"] = final_summary
        update["transcript_tokens"] = _history_tokens({**merged, "summary": final_summary},
                                                      merged["messages"])
        merged["summary"] = final_summary
        merged["transcript_tokens"] = update["transcript_tokens"]
    _persist_state(merged)
    return Command(goto=END, update=update)


def supervisor(state: State) -> Command[Literal["researcher", "writer", "reviewer", "__end__"]]:
    """Route between bounded roles using typed handoff tools only.

    The supervisor reads shared state, never the corpus, and never calls a worker
    tool directly. Every decision is persisted before control is handed off.
    """
    state = _normalize_state(state)
    next_turn = state["turn_count"] + 1
    verdict = state["review_verdict"]
    note: str

    if state["status"] == STATUS_ERROR:
        last_error = state["errors"][-1] if state["errors"] else "worker failed"
        return _finish(state, state["turn_count"], STATUS_ERROR,
                       f"Run stopped after a worker error: {last_error}")

    if next_turn > MAX_SUPERVISOR_TURNS:
        return _finish(state, state["turn_count"], STATUS_MAX_TURNS,
                       f"Supervisor turn cap ({MAX_SUPERVISOR_TURNS}) reached before completion; logged and stopped.")

    if verdict and verdict.get("decision") == "accept" and state["draft_memo"]:
        return _finish(state, next_turn, STATUS_COMPLETED,
                       "Reviewer accepted the memo; run complete.")

    if state["rejections"] >= MAX_REJECTIONS:
        return _finish(state, next_turn, STATUS_ESCALATED,
                       f"Reviewer rejected the memo {state['rejections']} times; escalated to a human reviewer. "
                       f"Last reasons: {'; '.join(verdict.get('reasons', [])) if verdict else 'n/a'}")

    stop_after = _int_env("TEAM_STOP_AFTER_SUPERVISOR_TURNS")
    if stop_after and next_turn >= stop_after:
        return _finish(state, next_turn, STATUS_INTERRUPTED,
                       f"Stopped after supervisor turn {next_turn} on request; scratchpad saved for resume.")

    if not state["snippets"]:
        tool, target = "hand_off_to_researcher", "researcher"
        note = "No cited snippets in shared state; handing off to the Researcher for corpus retrieval."
    elif state["draft_memo"] is None:
        tool, target = "hand_off_to_writer", "writer"
        note = "Cited snippets are available and no draft exists; handing off to the Writer."
    elif verdict is None:
        tool, target = "hand_off_to_reviewer", "reviewer"
        note = "A draft memo is waiting for review; handing off to the Reviewer."
    else:
        tool, target = "hand_off_to_writer", "writer"
        note = "Reviewer requested a revision; handing back to the Writer."

    update = _turn_update(state, next_turn, note, STATUS_RUNNING, handoff=tool)
    merged = _normalize_state({**state, **update}, topic=state["topic"], topic_id=state["topic_id"])
    compaction = _maybe_compact(merged)
    if compaction:
        update = {**update, **compaction}
        merged = _normalize_state({**merged, **compaction}, topic=state["topic"],
                                  topic_id=state["topic_id"])
    _persist_state(merged)
    return HANDOFFS[tool](update=update)


# --------------------------------------------------------------------------- #
# Worker support: bounded prompts, validation, MCP retrieval helpers
# --------------------------------------------------------------------------- #
STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "by",
    "at", "from", "assess", "its", "it", "is", "are", "was", "were", "be", "as",
}
GENERIC_TERMS = {"2025", "2024", "northstar", "fictional", "credits", "year", "new"}
VALID_SOURCE_IDS = {f"P{i:02d}" for i in range(1, 13)}
INTERPRETIVE_VERBS = {
    "suggest", "suggests", "suggested", "suggesting", "indicate", "indicates", "indicated",
    "indicating", "reflect", "reflects", "reflected", "reflecting", "demonstrate",
    "demonstrates", "demonstrated", "demonstrating", "highlight", "highlights", "highlighting",
    "underscore", "underscores", "underscoring", "imply", "implies", "implying", "signal",
    "signals", "signaling",
}
SPECULATIVE_TERMS = {
    "profit", "profits", "profitability", "competitive", "competition", "competitor",
    "strategy", "strategies", "strategic", "future", "demand", "retention", "loyalty",
    "commitment", "sustainability", "sustainable", "invest", "investment", "infrastructure",
    "revenue", "revenues", "pricing", "market share", "outlook",
}
EVALUATIVE_TERMS = {
    "significant", "significantly", "substantial", "substantially", "robust", "promising",
    "positive", "successful", "successfully", "effectively", "milestone", "noteworthy",
    "vital", "crucial", "important", "remarkable", "strong", "strongly", "excellent",
}

WRITER_SYSTEM = (
    "You are a bounded research writer. Use ONLY the supplied direct snippets; never add outside "
    "facts, never invent numbers, companies or source IDs. Return exactly one JSON object with keys: "
    '"title" (at most 10 words), "evidence" (4-8 sentences), "interpretation" (exactly one '
    'sentence, containing no numbers), "limitations" (5-7 sentences), and "footnotes" (a short list such as '
    '"[P05] SwiftCart annual report: delivery volume, 2025."). '
    "The memo is assembled as an evidence paragraph, then 'Interpretation:', then 'Limitations:'. "
    "Every evidence sentence must restate a supplied figure or fact and end with its [P##] "
    "citation. Do not use interpretive verbs (indicate, suggest, reflect, demonstrate, highlight) "
    "or evaluative words (significant, substantial, robust, promising, positive, successful, "
    "effectively, crucial, important, milestone) anywhere: state computed differences plainly. "
    "The interpretation sentence may only restate what the cited figures show. Limitations state "
    "which details the source does not provide and need no citation. Do not speculate about "
    "causes, demand, profitability, pricing strategy, competitiveness or future performance. "
    "HARD LENGTH RULE: the assembled body must contain 255-340 words and the full memo including "
    "title and footnotes at most 348 words; aim for 290 words. If the assembled body is under "
    "255 words, add another cited evidence sentence and another limitation sentence. "
    "Professional, concise tone; no bullet lists. Count the body words before returning."
)

REVIEW_RUBRIC = (
    "1. The body is 250-350 words, and the full memo (title + body + footnotes) is at most 350 words.\n"
    "2. Every sentence that states a figure or factual claim carries an inline [P##] citation.\n"
    "3. Only supplied source IDs (P01-P12) are cited; no invented sources or figures.\n"
    "4. Evidence and interpretation are clearly separated: interpretation is explicitly labelled and "
    "adds no new facts or unsupported causal claims.\n"
    "5. Tone is professional and concise; the title and footnotes are brief.\n"
    "6. Relevant limitations are stated (measurement basis, survey versus transactions, stated exclusions) "
    "without inventing caveats the memo's evidence cannot support."
)

REVIEW_SYSTEM = (
    "You assess the professional tone of a research memo. You see only the memo. "
    "Item 5 passes when the tone is professional, concise and coherent, the title is brief, and "
    "the footnotes are brief source notes. Fail it only for clear unprofessional language, an "
    "overlong title, or footnotes that are not concise source notes. "
    'Return {"items": {"5": "pass" or "fail"}, "reasons": [short strings]}. '
    "Reasons must describe only actual violations and quote the offending text. "
    "Do not fail for content, citations or limitations; other items are checked separately. "
    "Do not add facts or rewrite the memo."
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _usage_tokens(result: dict) -> int:
    value = result.get("total_tokens")
    try:
        if value is not None:
            return int(value)
    except (TypeError, ValueError):
        pass
    return _estimate_tokens(result.get("text", ""))


def _worker_update(state: State, node: str, note: str, *, status: str | None = None,
                   usage_tokens: int = 0, **fields) -> dict:
    message: Message = {
        "role": node,
        "node": node,
        "turn": state["turn_count"],
        "content": note,
        "tokens": _estimate_tokens(note) + 4 + int(usage_tokens),
    }
    messages = [*state["messages"], message]
    entry: TraceEntry = {
        "at": _now(),
        "node": node,
        "turn": state["turn_count"],
        "handoff": "hand_back",
        "status": status or state["status"],
        "note": note[:500],
    }
    update = {
        "messages": messages,
        "trace": [*state["trace"], entry],
        "transcript_tokens": _history_tokens(state, messages),
    }
    if status:
        update["status"] = status
    update.update(fields)
    return update


def _history_tokens(state: State, messages: list[Message]) -> int:
    """Summary plus the message history not yet folded into the summary."""
    start = min(max(0, state["compacted_upto"]), len(messages))
    return _estimate_tokens(state["summary"]) + sum(message["tokens"] for message in messages[start:])


# --------------------------------------------------------------------------- #
# Rolling-summary memory (R5)
# --------------------------------------------------------------------------- #
SUMMARY_SYSTEM = (
    "You compress the working memory of a multi-agent research run. Reply with plain text only, "
    "under 180 words, no preamble. Preserve, in this order: (1) the topic; (2) each supervisor "
    "decision with its turn number and handoff target; (3) every reviewer rejection with its "
    "reasons; (4) the current draft word count and cited source IDs; (5) the pending action. "
    "Merge the previous summary when one is supplied, and never invent facts or numbers."
)


def _draft_words(state: State) -> int:
    memo = state.get("draft_memo") or {}
    return _word_count(str(memo.get("body") or ""))


def _compaction_input(state: State) -> str:
    decisions = [
        f"- turn {entry['turn']} {entry['node']} -> {entry['handoff']} [{entry['status']}]: {entry['note']}"
        for entry in state["trace"]
        if entry.get("node") in {"supervisor", "researcher", "writer", "reviewer"}
    ]
    rejections = [
        f"- {verdict['decision']}: {'; '.join(verdict.get('reasons', []))}"
        for verdict in state["verdict_history"]
    ]
    cited = sorted({f"P{number}" for number in re.findall(r"P(\d{2})",
                     memo_text(state.get("draft_memo")))})
    return (
        f"Topic: {state['topic']}\n\n"
        f"Previous summary: {state['summary'] or '(none)'}\n\n"
        f"Decisions:\n" + ("\n".join(decisions) or "(none)") + "\n\n"
        f"Rejections ({state['rejections']}):\n" + ("\n".join(rejections) or "(none)") + "\n\n"
        f"Draft: {_draft_words(state)} words; cited IDs: {', '.join(cited) or 'none'}\n\n"
        f"Pending: {state['status']} at supervisor turn {state['turn_count']}"
    )


def _compaction_digest(state: State, upto: int) -> str:
    payload = {
        "upto": upto,
        "messages": len(state["messages"]),
        "topic": state["topic"],
        "rejections": state["rejections"],
        "draft_words": _draft_words(state),
        "last": state["messages"][-1]["content"] if state["messages"] else "",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _maybe_compact(state: State) -> dict:
    """Fold history into a rolling summary once real transcript tokens exceed the limit.

    Idempotent: the same history span produces the same digest, and an existing event
    with that digest means the span was already compacted.
    """
    if state["transcript_tokens"] <= _summary_limit():
        return {}
    messages = state["messages"]
    upto = len(messages)
    if state["compacted_upto"] >= upto:
        return {}
    digest = _compaction_digest(state, upto)
    if any(event.get("digest") == digest for event in state["compaction_events"]):
        return {}
    before = state["transcript_tokens"]

    try:
        result = CHAT_TEXT(
            [{"role": "system", "content": SUMMARY_SYSTEM},
             {"role": "user", "content": _compaction_input(state)}],
            temperature=0.0,
            max_tokens=260,
        )
        summary = result["text"].strip()
        if not summary:
            raise RuntimeError("empty summary")
    except Exception as exc:  # noqa: BLE001 - memory failure must not kill the run
        event = {"at": _now(), "turn": state["turn_count"], "compacted_upto": upto,
                 "history_tokens_before": before, "digest": digest, "failed": True,
                 "reason": f"compaction failed: {type(exc).__name__}"}
        return {
            "compaction_events": [*state["compaction_events"], event],
            "errors": [*state["errors"], f"compaction: {type(exc).__name__}: {exc}"],
        }

    event = {"at": _now(), "turn": state["turn_count"], "compacted_upto": upto,
             "history_tokens_before": before, "summary_tokens": _estimate_tokens(summary),
             "digest": digest, "reason": f"history {before} > {_summary_limit()}"}
    compacted_state = _normalize_state({**state, "summary": summary, "compacted_upto": upto},
                                       topic=state["topic"], topic_id=state["topic_id"])
    entry: TraceEntry = {
        "at": _now(), "node": "memory", "turn": state["turn_count"], "handoff": "compact",
        "status": state["status"],
        "note": f"Compacted {upto} messages ({before} -> {_estimate_tokens(summary)} tokens).",
    }
    return {
        "summary": summary,
        "compacted_upto": upto,
        "compaction_events": [*state["compaction_events"], event],
        "transcript_tokens": _history_tokens(compacted_state, messages),
        "total_tokens": state["total_tokens"] + _usage_tokens(result),
        "trace": [*state["trace"], entry],
    }


def _hand_back(state: State, node: str, note: str, *, status: str | None = None,
               usage_tokens: int = 0, **fields) -> Command[Literal["supervisor"]]:
    update = _worker_update(state, node, note, status=status, usage_tokens=usage_tokens, **fields)
    merged = _normalize_state({**state, **update}, topic=state["topic"], topic_id=state["topic_id"])
    _persist_state(merged)
    return Command(goto="supervisor", update=update)


def _worker_error(state: State, node: str, exc: Exception) -> Command[Literal["supervisor"]]:
    detail = f"{type(exc).__name__}: {exc}"
    return _hand_back(state, node, f"{node} failed: {detail}", status=STATUS_ERROR,
                      errors=[*state["errors"], f"{node}: {detail}"])


def _memo_issues(memo: Memo | None, allowed_sources: set[str]) -> list[str]:
    """Deterministic checks shared by the writer repair loop and reviewer gate."""
    if not memo:
        return ["No memo was produced."]
    title = str(memo.get("title") or "").strip()
    body = str(memo.get("body") or "").strip()
    footnotes = [str(item).strip() for item in (memo.get("footnotes") or []) if str(item).strip()]
    issues: list[str] = []
    if not title:
        issues.append("The title is missing.")
    if not body:
        issues.append("The body is missing.")
    body_words = _word_count(body)
    total_words = _word_count(memo_text({**memo, "title": title, "body": body, "footnotes": footnotes}))
    if not 250 <= body_words <= 350:
        issues.append(f"Body word count {body_words} is outside the 250-350 range.")
    if total_words > 350:
        issues.append(f"Full memo word count {total_words} exceeds 350 once title and footnotes are counted.")
    text = memo_text({"title": title, "body": body, "footnotes": footnotes})
    ids = set(re.findall(r"\[(P\d{2})\]", text))
    body_ids = set(re.findall(r"\[(P\d{2})\]", body))
    if not body_ids:
        issues.append("The body has no inline [P##] citations.")
    invalid = sorted(ids - allowed_sources)
    if invalid:
        issues.append("Citations not present in the supplied sources: " + ", ".join(invalid))
    evidence, interpretation, limitations = _split_body_sections(body)
    uncited = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", evidence)
               if re.search(r"\d", sentence) and not re.search(r"\[P\d{2}\]", sentence)]
    if uncited:
        issues.append("A sentence with figures has no citation: " + uncited[0][:140])
    uncited_evidence = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", evidence)
                        if sentence.strip() and not re.search(r"\[P\d{2}\]", sentence)]
    if uncited_evidence:
        issues.append("Every evidence sentence needs a citation; add one to: "
                      + uncited_evidence[0][:140])
    footnote_ids = set(re.findall(r"\[(P\d{2})\]", "\n".join(footnotes)))
    missing = sorted(body_ids - footnote_ids)
    if missing:
        issues.append("Cited sources missing from the footnote list: " + ", ".join(missing))

    if not interpretation:
        issues.append("The body needs an explicit 'Interpretation:' sentence.")
    elif re.search(r"\d", interpretation):
        issues.append("The Interpretation sentence must not contain numbers; it should state only "
                      "the direction of the cited changes.")
    if len([s for s in re.split(r"(?<=[.!?])\s+", evidence) if s.strip()]) < 4:
        issues.append("The evidence paragraph needs at least four cited sentences.")
    if not limitations:
        issues.append("The body needs an explicit 'Limitations:' paragraph.")
    speculative_hits = sorted({
        term for term in SPECULATIVE_TERMS
        if re.search(rf"\b{re.escape(term)}\b", (evidence + " " + interpretation).lower())
    })
    if speculative_hits:
        issues.append("Remove unsupported speculation outside the limitations: "
                      + ", ".join(speculative_hits))
    for sentence in re.split(r"(?<=[.!?])\s+", evidence):
        lowered = sentence.lower()
        verbs = [verb for verb in (INTERPRETIVE_VERBS | EVALUATIVE_TERMS)
                 if re.search(rf"\b{re.escape(verb)}\b", lowered)]
        if verbs:
            issues.append("Keep the evidence section factual; move interpretation out of: "
                          + sentence.strip()[:140])
            break
    return issues


def _split_body_sections(body: str) -> tuple[str, str, str]:
    """Split the memo body at its explicit Interpretation / Limitations labels."""
    match = re.search(r"interpretation\s*:", body, re.IGNORECASE)
    if not match:
        return body, "", ""
    evidence = body[: match.start()]
    rest = body[match.end():]
    limit = re.search(r"limitations\s*:", rest, re.IGNORECASE)
    if not limit:
        return evidence, rest, ""
    return evidence, rest[: limit.start()], rest[limit.end():]


def _as_list(value) -> list[str]:
    """Normalise a possibly-string field into a list of non-empty strings."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _evidence_list(data: dict) -> list[str]:
    raw = _as_list(data.get("evidence"))
    sentences: list[str] = []
    for item in raw:
        sentences.extend(sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", item)
                         if sentence.strip())
    return sentences


def _memo_from_data(data: dict) -> Memo:
    """Assemble a typed memo from structured writer fields (or a plain body)."""
    title = str(data.get("title") or "").strip()
    footnotes = _as_list(data.get("footnotes"))
    body = str(data.get("body") or "").strip()
    if not body:
        evidence = _evidence_list(data)
        interpretation = " ".join(_as_list(data.get("interpretation")))
        limitations = _as_list(data.get("limitations"))
        parts = []
        if evidence:
            parts.append(" ".join(evidence))
        if interpretation:
            parts.append("Interpretation: " + interpretation)
        if limitations:
            parts.append("Limitations: " + " ".join(limitations))
        body = "\n\n".join(parts)
    return {"title": title, "body": body, "footnotes": footnotes}


def _sanitize_evidence(memo: Memo) -> Memo:
    """Drop evidence sentences that are uncited, interpretive or evaluative."""
    evidence, interpretation, limitations = _split_body_sections(memo["body"])
    banned = INTERPRETIVE_VERBS | EVALUATIVE_TERMS | SPECULATIVE_TERMS
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", evidence):
        sentence = sentence.strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if not re.search(r"\[P\d{2}\]", sentence):
            continue
        if any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in banned):
            continue
        kept.append(sentence)
    body = " ".join(kept).strip()
    if interpretation.strip():
        body += "\n\nInterpretation: " + interpretation.strip()
    if limitations.strip():
        body += "\n\nLimitations: " + limitations.strip()
    return {**memo, "body": body}


def _compose_body(evidence: str, interpretation: str, limitations: str) -> str:
    body = evidence.strip()
    if interpretation.strip():
        body += "\n\nInterpretation: " + interpretation.strip()
    if limitations.strip():
        body += "\n\nLimitations: " + limitations.strip()
    return body.strip()


def _expand_limitations(state: State, memo: Memo, target: int = 270) -> tuple[Memo, dict]:
    """Extend only the Limitations paragraph, sentence by sentence, within bounds."""
    messages = [
        {"role": "system", "content": (
            "You extend only the Limitations paragraph of a research memo. Return one JSON object "
            'with the key "limitations": a list of 2-4 sentences. Each sentence must describe '
            "information the supplied memo does not provide. Do not add facts, numbers, citations, "
            "causes, forecasts or new claims about the company; only state what is missing.")},
        {"role": "user", "content": (
            f"Topic: {state['topic']}\n\nCurrent memo:\n{memo_text(memo)}\n\n"
            "Add 2-4 limitation sentences of about 12-18 words each.")},
    ]
    result = CHAT_JSON(messages, temperature=0.0)
    added = _as_list((result.get("data") or {}).get("limitations"))
    evidence, interpretation, limitations = _split_body_sections(memo["body"])
    current = limitations.strip()
    for sentence in added:
        if re.search(r"\d|\[P\d{2}\]", sentence):
            continue
        candidate = (current + " " + sentence).strip()
        body = _compose_body(evidence, interpretation, candidate)
        if _word_count(body) > 340:
            break
        current = candidate
        if _word_count(body) >= target:
            break
    return {**memo, "body": _compose_body(evidence, interpretation, current)}, result


def _evidence_sentences(memo: Memo) -> list[str]:
    evidence, _, _ = _split_body_sections(str(memo.get("body") or ""))
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", evidence)
            if sentence.strip()]


def _expand_evidence(state: State, memo: Memo, snippets: list[Snippet],
                     target: int = 4) -> tuple[Memo, dict]:
    """Add cited evidence sentences only, drawn from the direct snippets."""
    block = "\n".join(f"[{snippet['source']}] {snippet['text']}" for snippet in snippets)
    messages = [
        {"role": "system", "content": (
            "You add evidence sentences to a research memo using only the supplied snippets. "
            'Return one JSON object with the key "evidence": a list of sentences. Each sentence '
            "must restate one supplied figure or fact and end with that snippet's [P##] citation. "
            "Do not add interpretation, evaluation, causes, forecasts or numbers that are not in "
            "the snippets.")},
        {"role": "user", "content": (
            f"Topic: {state['topic']}\n\nCurrent memo:\n{memo_text(memo)}\n\n"
            f"Snippets:\n{block}\n\n"
            f"Add {max(1, target - len(_evidence_sentences(memo)))} evidence sentence(s) that are "
            "missing from the memo, especially figures for each requested dimension.")},
    ]
    result = CHAT_JSON(messages, temperature=0.0)
    added = _as_list((result.get("data") or {}).get("evidence"))
    evidence = _evidence_sentences(memo)
    seen = {re.sub(r"\s+", " ", sentence.lower()) for sentence in evidence}
    for sentence in added:
        normalized = re.sub(r"\s+", " ", sentence.lower())
        if not re.search(r"\[P\d{2}\]", sentence) or normalized in seen:
            continue
        evidence.append(sentence)
        seen.add(normalized)
    _, interpretation, limitations = _split_body_sections(memo["body"])
    return {**memo, "body": _compose_body(" ".join(evidence), interpretation, limitations)}, result


def _structured_issues(data: dict, allowed_sources: set[str]) -> list[str]:
    """Checks that only apply when the writer returns the structured schema."""
    evidence = _evidence_list(data)
    if not evidence:
        return []
    issues: list[str] = []
    if len(evidence) < 4:
        issues.append(f"The evidence paragraph has {len(evidence)} sentences; supply at least four, "
                      "each with a citation.")
    for sentence in evidence:
        if not re.search(r"\[P\d{2}\]", sentence):
            issues.append("Evidence sentence has no citation: " + sentence[:140])
            break
    allowed_terms = INTERPRETIVE_VERBS | EVALUATIVE_TERMS
    for sentence in evidence:
        lowered = sentence.lower()
        hits = [term for term in allowed_terms if re.search(rf"\b{re.escape(term)}\b", lowered)]
        if hits:
            issues.append("Remove interpretive/evaluative wording from the evidence ('"
                          + ", ".join(sorted(hits)[:3]) + "'): " + sentence[:140])
            break
    return issues


def _entities(topic: str) -> list[str]:
    return [w for w in re.findall(r"[A-Z][A-Za-z0-9]+", topic)
            if w.lower() not in {"assess"}]


def _stem(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("es") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def _term_set(text: str) -> set[str]:
    return {_stem(word) for word in re.findall(r"[a-z0-9]+", text.lower())
            if len(word) > 2 and word not in STOPWORDS}


def _research_queries(topic: str) -> list[str]:
    terms = [w for w in re.findall(r"[a-z0-9]+", topic.lower())
             if w not in STOPWORDS and len(w) > 2]
    entities = [entity.lower() for entity in _entities(topic)]
    queries: list[str] = []

    def add(words: list[str]) -> None:
        query = " ".join(dict.fromkeys(words)).strip()
        if query and query not in queries:
            queries.append(query)

    if entities:
        add(entities + terms[:5])
    add(terms)
    if entities:
        for index in range(0, min(len(terms), 6), 2):
            add(entities + terms[index:index + 2])
    # Fallback keeps 3-5 cited snippets reachable when a narrow topic matches only
    # two paragraphs; these extra candidates are labelled as context evidence.
    add(["northstar", "2025", "fictional"])
    return queries[:6]


def _select_snippets(candidates: list[dict], topic: str) -> list[dict]:
    """Rank MCP candidates by entity/topic overlap and return 3-5 cited snippets."""
    topic_terms = _term_set(topic) - GENERIC_TERMS
    entity_terms = {_stem(entity.lower()) for entity in _entities(topic)}
    best: dict[str, dict] = {}
    for item in candidates:
        text = item.get("text", "")
        source = item.get("source", "")
        if not isinstance(text, str) or not isinstance(source, str):
            continue
        if source not in VALID_SOURCE_IDS or not text.strip():
            continue
        terms = _term_set(text)
        entity_hits = len(terms & entity_terms)
        topic_hits = len(terms & topic_terms)
        score = 2 * entity_hits + topic_hits
        current = best.get(source)
        if current is None or score > current["score"]:
            best[source] = {
                "text": text.strip(),
                "source": source,
                "score": score,
                "entity_hits": entity_hits,
                "topic_hits": topic_hits,
            }
    ranked = sorted(best.values(), key=lambda row: (-row["score"], -row["topic_hits"], row["source"]))
    company_entities = {_stem(entity.lower()) for entity in _entities(topic)} - {"northstar"}
    if company_entities:
        # For a named company, a snippet is direct only if it actually mentions it.
        direct = [row for row in ranked if row["entity_hits"] > 0 and row["topic_hits"] > 0]
    else:
        # For a region-level topic, two overlapping topic terms are enough.
        direct = [row for row in ranked if row["topic_hits"] >= 2]
    if len(direct) >= 5:
        selected = direct[:5]
    elif len(direct) >= 3:
        selected = direct
    else:
        selected = (direct + [row for row in ranked if row not in direct])[:3]
    if len(selected) < 3:
        raise RuntimeError(f"MCP returned only {len(selected)} usable citations; need at least 3")
    for row in selected:
        row["relevance"] = "direct" if row in direct else "context"
    return selected


# --------------------------------------------------------------------------- #
# Workers: bounded surfaces
# --------------------------------------------------------------------------- #
def researcher(state: State) -> Command[Literal["supervisor"]]:
    """Researcher: only `search_corpus` over a genuine MCP stdio session."""
    state = _normalize_state(state)
    queries = _research_queries(state["topic"])
    try:
        result = mcp_client.search_corpus(queries)
        selected = _select_snippets(result["candidates"], state["topic"])
    except Exception as exc:  # noqa: BLE001 - report, do not hide MCP failures
        return _worker_error(state, "researcher", exc)

    snippets: list[Snippet] = [
        {"text": row["text"], "source": row["source"], "relevance": row["relevance"]}
        for row in selected
    ]
    evidence = {
        "at": _now(),
        "transport": "stdio",
        "server": mcp_client.SERVER.name,
        "tools": result["tools"],
        "resources": result["resources"],
        "queries": result["queries"],
        "tool_calls": result["tool_calls"],
        "returned": len(snippets),
        "selected": [{key: row[key] for key in ("source", "score", "topic_hits", "entity_hits", "relevance")}
                     for row in selected],
        "glossary_loaded": bool(result.get("glossary")),
    }
    note = (f"MCP search_corpus returned {len(snippets)} cited snippets via "
            f"{result['tool_calls']} tool calls; tools={result['tools']}; "
            f"resources={result['resources']}.")
    return _hand_back(state, "researcher", note, status=STATUS_RUNNING,
                      usage_tokens=_estimate_tokens(" ".join(item["text"] for item in snippets)),
                      snippets=snippets,
                      mcp_calls=state["mcp_calls"] + int(result["tool_calls"]),
                      mcp_evidence=[*state["mcp_evidence"], evidence])


def _writer_snippets(state: State) -> list[Snippet]:
    """Only direct evidence reaches the writer; retrieval context stays in state."""
    direct = [snippet for snippet in state["snippets"]
              if snippet.get("relevance", "direct") == "direct"]
    return direct or list(state["snippets"])


def _writer_messages(state: State, snippets: list[Snippet]) -> list[dict]:
    snippet_block = "\n\n".join(f"[{s['source']}] {s['text']}" for s in snippets)
    context_count = len(state["snippets"]) - len(snippets)
    content = f"Topic: {state['topic']}\n\nDirect snippets (cite only these):\n{snippet_block}\n\n"
    if context_count:
        content += (f"{context_count} additional context snippet(s) are held in shared state but are "
                    "not shown because they are not direct evidence for this topic. Do not cite them.\n\n")
    if state["draft_memo"] and state["review_verdict"] and state["review_verdict"].get("decision") == "revise":
        reasons = "; ".join(state["review_verdict"].get("reasons", []))
        current_words = _word_count(str(state["draft_memo"].get("body") or ""))
        content += (f"The reviewer rejected the previous draft for: {reasons}\n"
                    f"Previous draft ({current_words}-word body):\n{memo_text(state['draft_memo'])}\n\n"
                    "Produce a corrected memo that fixes every listed issue. If the body is short, "
                    f"expand it to about 290 words (current: {current_words}).\n\n")
    content += "Return the JSON object now."
    return [{"role": "system", "content": WRITER_SYSTEM}, {"role": "user", "content": content}]


def writer(state: State) -> Command[Literal["supervisor"]]:
    """Writer: shared state only. No MCP, no corpus, no worker tools."""
    state = _normalize_state(state)
    if not state["snippets"]:
        return _worker_error(state, "writer", RuntimeError("no snippets in shared state"))
    usable = _writer_snippets(state)
    allowed = {snippet["source"] for snippet in usable}
    messages = _writer_messages(state, usable)
    tokens = 0
    attempts = 0
    memo: Memo | None = None
    issues: list[str] = []

    try:
        while attempts < 5:
            result = CHAT_JSON(messages, temperature=0.0)
            tokens += _usage_tokens(result)
            attempts += 1
            data = result.get("data") or {}
            candidate = _memo_from_data(data)
            candidate_issues = list(dict.fromkeys(
                _memo_issues(candidate, allowed) + _structured_issues(data, allowed)))
            if memo is None or len(candidate_issues) <= len(issues):
                memo, issues = candidate, candidate_issues
            if not issues:
                break
            body_words = _word_count(str(memo.get("body") or ""))
            correction = ("Fix every validation issue and return only the structured JSON object "
                          "(title, evidence, interpretation, limitations, footnotes): "
                          + " ".join(issues))
            if any("250-350" in issue for issue in issues):
                correction += (f" The body has {body_words} words; add about "
                               f"{max(10, 290 - body_words)} words of cited detail and specifically "
                               "worded limitations from the direct snippets, without adding new facts. "
                               "Use at least eight evidence sentences, each stating a supplied figure "
                               "and ending with its [P##] citation, and six limitation sentences.")
            if any("speculation" in issue or "factual" in issue for issue in issues):
                correction += (" Delete the offending sentence entirely rather than paraphrasing it. "
                               "Evidence sentences must only restate a supplied snippet; put any "
                               "analysis in the single 'Interpretation:' sentence and mention "
                               "unknowns only in 'Limitations:'.")
            messages = messages + [
                {"role": "assistant", "content": result["text"]},
                {"role": "user", "content": correction},
            ]
    except Exception as exc:  # noqa: BLE001 - provider/parse failures are reported
        return _worker_error(state, "writer", exc)

    assert memo is not None
    memo = _sanitize_evidence(memo)
    evidence_additions = 0
    while len(_evidence_sentences(memo)) < 4 and evidence_additions < 2:
        before = len(_evidence_sentences(memo))
        memo, expansion = _expand_evidence(state, memo, usable, target=4)
        tokens += _usage_tokens(expansion)
        evidence_additions += 1
        if len(_evidence_sentences(memo)) <= before:
            break
    memo = _sanitize_evidence(memo)
    issues = _memo_issues(memo, allowed)
    expansions = 0
    while _word_count(memo["body"]) < 250 and expansions < 3:
        before = _word_count(memo["body"])
        memo, expansion = _expand_limitations(state, memo, target=270)
        tokens += _usage_tokens(expansion)
        expansions += 1
        if _word_count(memo["body"]) <= before:
            break
        issues = _memo_issues(memo, allowed)
    errors = [*state["errors"]]
    if issues:
        errors.append("writer validation: " + "; ".join(issues))
    cited = sorted(set(re.findall(r"\[(P\d{2})\]", memo["body"])))
    note = (f"Writer produced a {_word_count(memo['body'])}-word body with "
            f"{len(cited)} cited source(s) after {attempts} attempt(s), {evidence_additions} "
            f"evidence expansion(s) and {expansions} limitation expansion(s); "
            f"{tokens} provider tokens.")
    return _hand_back(state, "writer", note, status=STATUS_RUNNING, usage_tokens=tokens,
                      draft_memo=memo,
                      review_verdict=None,
                      total_tokens=state["total_tokens"] + tokens,
                      errors=errors)


def reviewer(state: State) -> Command[Literal["supervisor"]]:
    """Reviewer: only the writer's memo plus the static rubric. No snippets."""
    state = _normalize_state(state)
    if not state["draft_memo"]:
        return _worker_error(state, "reviewer", RuntimeError("no draft memo to review"))
    memo = state["draft_memo"]
    text = memo_text(memo)
    issues = _memo_issues(memo, VALID_SOURCE_IDS)
    _, interpretation, limitations = _split_body_sections(str(memo.get("body") or ""))
    limitation_sentences = [sentence.strip() for sentence in
                            re.split(r"(?<=[.!?])\s+", limitations) if sentence.strip()]
    deterministic: dict[str, list[str]] = {}

    def add_item(item: str, message: str) -> None:
        deterministic.setdefault(item, []).append(message)

    for issue in issues:
        if "word count" in issue:
            add_item("1", issue)
        elif "Citations not present" in issue:
            add_item("3", issue)
        elif any(key in issue for key in
                 ("Interpretation", "Limitations", "speculation", "Keep the evidence")):
            add_item("4", issue)
        else:
            add_item("2", issue)
    if not limitation_sentences:
        add_item("6", "The Limitations paragraph needs at least one missing-data sentence.")
    limit_digits = re.sub(r"\b(?:19|20)\d{2}\b", "", limitations)
    if re.search(r"\[P\d{2}\]|\d", limit_digits):
        add_item("6", "Limitations must describe missing data without citations or new figures.")

    checks = (
        f"Item 1 (length): {'fail' if '1' in deterministic else 'pass'} "
        f"(body {_word_count(str(memo.get('body') or ''))} words, total {_word_count(text)})\n"
        f"Item 2 (inline citations and footnote coverage): "
        f"{'fail' if '2' in deterministic else 'pass'}\n"
        f"Item 3 (source IDs): {'fail' if '3' in deterministic else 'pass'}\n"
        f"Item 4 (evidence/interpretation separation): "
        f"{'fail' if '4' in deterministic else 'pass'}\n"
        f"Item 6 (limitations): {'fail' if '6' in deterministic else 'pass'}\n"
        f"Details: {'; '.join(issues) or 'none'}"
    )
    messages = [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user", "content": f"Topic: {state['topic']}\n\nITEM 5 RUBRIC:\n"
                                    "[5] Tone is professional and concise; the title and footnotes "
                                    "are brief.\n\nITEMS 1-4 AND 6 ARE CHECKED AUTOMATICALLY:\n"
                                    f"{checks}\n\nMEMO:\n{text}"},
    ]
    try:
        result = CHAT_JSON(messages, temperature=0.0)
        data = result.get("data") or {}
        raw_items = data.get("items") or {}
        tone_verdict = ""
        if isinstance(raw_items, dict):
            tone_verdict = str(raw_items.get("5") or raw_items.get(5) or "").strip().lower()
        model_reasons = _as_list(data.get("reasons"))
    except Exception as exc:  # noqa: BLE001 - provider/parse failures are reported
        return _worker_error(state, "reviewer", exc)

    failed = sorted(set(deterministic) | ({"5"} if tone_verdict.startswith("fail") else set()))
    decision = "revise" if failed else "accept"
    reasons: list[str] = [issue for item in failed for issue in deterministic.get(item, [])]
    if tone_verdict.startswith("fail"):
        reasons.extend(model_reasons)
    if failed and not reasons:
        reasons = [f"Rubric item {item} failed." for item in failed]
    verdict: Verdict = {"decision": decision,
                        "reasons": list(dict.fromkeys(reasons))[:6] or ["All rubric items pass."]}
    rejections = state["rejections"] + (1 if decision == "revise" else 0)
    note = (f"Reviewer verdict: {decision} "
            f"({rejections} rejection(s) so far). Reasons: {'; '.join(verdict['reasons'])}")
    return _hand_back(state, "reviewer", note, status=STATUS_RUNNING,
                      usage_tokens=_usage_tokens(result),
                      review_verdict=verdict,
                      verdict_history=[*state["verdict_history"], verdict],
                      rejections=rejections,
                      total_tokens=state["total_tokens"] + _usage_tokens(result))



# --------------------------------------------------------------------------- #
# Graph and adapter
# --------------------------------------------------------------------------- #
def build_graph():
    graph = StateGraph(State)
    for name, node in [("supervisor", supervisor), ("researcher", researcher),
                       ("writer", writer), ("reviewer", reviewer)]:
        graph.add_node(name, node)
    graph.add_edge(START, "supervisor")
    return graph.compile()


def memo_text(memo: Memo | None) -> str:
    if not memo:
        return ""
    footnotes = "\n".join(memo.get("footnotes") or [])
    parts = [memo.get("title", "").strip(), memo.get("body", "").strip()]
    if footnotes:
        parts.append("Footnotes:\n" + footnotes)
    return "\n\n".join(part for part in parts if part)


def run_topic(topic: str, topic_id: str) -> dict:
    """Adapter contract from EVALUATION.md; resumes an unfinished run."""
    state, resumed = _resume_state(topic, topic_id)
    graph = build_graph()
    try:
        final = graph.invoke(state, config={"recursion_limit": 4 * MAX_SUPERVISOR_TURNS + 20})
    except KeyboardInterrupt:
        saved = _load_state(topic_id) or state
        saved["status"] = STATUS_INTERRUPTED
        _persist_state(saved)
        raise
    except Exception as exc:
        saved = _load_state(topic_id) or state
        saved["status"] = STATUS_INTERRUPTED
        saved["errors"] = [*saved["errors"], f"{type(exc).__name__}: {exc}"]
        _persist_state(saved)
        raise

    final = _normalize_state(final, topic=topic, topic_id=topic_id)
    return {
        "topic_id": topic_id,
        "memo": memo_text(final["draft_memo"]),
        "total_tokens": final["total_tokens"] or None,
        "turns": final["turn_count"],
        "rejections": final["rejections"],
        "mcp_calls": final["mcp_calls"],
        "status": final["status"],
        "run_id": final["run_id"],
        "resumed": resumed,
        "scratchpad": str(scratchpad_path(topic_id)),
        "trace": final["trace"],
        "metrics": {
            "transcript_tokens": final["transcript_tokens"],
            "compaction_events": len(final["compaction_events"]),
        },
        "errors": final["errors"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or resume one seed topic.")
    parser.add_argument("--topic", required=True, help="topic id from topics.json, e.g. T04")
    parser.add_argument("--fresh", action="store_true", help="ignore an existing scratchpad")
    args = parser.parse_args()

    topics = json.loads((ROOT / "topics.json").read_text(encoding="utf-8"))
    match = next((t for t in topics if t["id"] == args.topic), None)
    if match is None:
        raise SystemExit(f"Unknown topic id: {args.topic}")
    if args.fresh:
        scratchpad_path(args.topic).unlink(missing_ok=True)

    result = run_topic(match["topic"], match["id"])
    print(json.dumps({key: value for key, value in result.items() if key != "trace"}, indent=2))
    print("Trace:")
    for entry in result["trace"]:
        print(f"  turn {entry['turn']:>2} {entry['node']:<12} {entry['handoff']:<24} {entry['status']}")


if __name__ == "__main__":
    main()
