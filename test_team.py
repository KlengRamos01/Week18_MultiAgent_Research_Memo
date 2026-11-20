"""Offline checks for R1 supervisor routing, caps, persistence and resume.

Run with: python test_team.py      (no API key or network needed)
"""
from __future__ import annotations

import inspect
import json
import os
import tempfile
import traceback
from pathlib import Path

RUNS = tempfile.mkdtemp(prefix="team-r1-")
os.environ["TEAM_SCRATCHPAD_DIR"] = RUNS
os.environ.pop("TEAM_STOP_AFTER_SUPERVISOR_TURNS", None)
os.environ.pop("TEAM_SUMMARY_TOKEN_LIMIT", None)

import team  # noqa: E402  (env must be set first)
from langgraph.graph import END  # noqa: E402

RESULTS: list[tuple[str, str]] = []


def check(name):
    def decorator(fn):
        try:
            fn()
            RESULTS.append((name, "PASS"))
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001 - test harness
            RESULTS.append((name, "FAIL"))
            print(f"FAIL  {name}: {exc!r}")
            traceback.print_exc()
        return fn

    return decorator


def make_state(topic_id="T03", **overrides):
    state = team._initial_state("Assess SwiftCart delivery growth, reliability and costs.", topic_id)
    state.update(overrides)
    return state


SNIPPET = {"text": "SwiftCart completed 1 million deliveries in 2025, up from 800,000 in 2024.",
           "source": "P05"}
MEMO = {"title": "SwiftCart delivery memo", "body": "SwiftCart completed 1 million deliveries [P05].",
        "footnotes": ["[P05] SwiftCart delivery volume, 2025."]}
ACCEPT = {"decision": "accept", "reasons": ["Grounded and within length."]}
REVISE = {"decision": "revise", "reasons": ["Cost evidence is missing."]}


@check("fresh state routes to researcher via typed handoff")
def _():
    state = make_state("T01")
    command = team.supervisor(state)
    assert command.goto == "researcher", command.goto
    assert command.update["turn_count"] == 1
    assert command.update["status"] == team.STATUS_RUNNING
    assert command.update["trace"][-1]["handoff"] == "hand_off_to_researcher"
    assert team.scratchpad_path("T01").exists()
    payload = json.loads(team.scratchpad_path("T01").read_text())
    assert payload["state"]["turn_count"] == 1
    assert payload["state"]["status"] == "running"


@check("snippets without a draft routes to writer")
def _():
    command = team.supervisor(make_state("T02", snippets=[SNIPPET]))
    assert command.goto == "writer", command.goto
    assert command.update["trace"][-1]["handoff"] == "hand_off_to_writer"


@check("draft awaiting review routes to reviewer")
def _():
    command = team.supervisor(make_state("T03", snippets=[SNIPPET], draft_memo=MEMO))
    assert command.goto == "reviewer", command.goto
    assert command.update["trace"][-1]["handoff"] == "hand_off_to_reviewer"


@check("accepted verdict ends the run as completed")
def _():
    command = team.supervisor(make_state("T04", snippets=[SNIPPET], draft_memo=MEMO,
                                        review_verdict=ACCEPT, rejections=0))
    assert command.goto == END, command.goto
    assert command.update["status"] == team.STATUS_COMPLETED
    assert command.update["trace"][-1]["handoff"] == "end"


@check("revision request routes back to writer")
def _():
    command = team.supervisor(make_state("T05", snippets=[SNIPPET], draft_memo=MEMO,
                                        review_verdict=REVISE, rejections=2))
    assert command.goto == "writer", command.goto


@check("three rejections escalate to a human and stop")
def _():
    command = team.supervisor(make_state("T06", snippets=[SNIPPET], draft_memo=MEMO,
                                        review_verdict=REVISE, rejections=3))
    assert command.goto == END, command.goto
    assert command.update["status"] == team.STATUS_ESCALATED
    assert "escalated" in command.update["trace"][-1]["note"].lower()


@check("supervisor turn cap of 8 stops with a logged status")
def _():
    command = team.supervisor(make_state("T07", snippets=[SNIPPET], draft_memo=MEMO,
                                        review_verdict=REVISE, rejections=1, turn_count=8))
    assert command.goto == END, command.goto
    assert command.update["status"] == team.STATUS_MAX_TURNS
    assert command.update["turn_count"] == 8
    payload = json.loads(team.scratchpad_path("T07").read_text())
    assert payload["state"]["status"] == team.STATUS_MAX_TURNS


@check("accept outranks a stale rejection counter")
def _():
    command = team.supervisor(make_state("T08", snippets=[SNIPPET], draft_memo=MEMO,
                                        review_verdict=ACCEPT, rejections=3))
    assert command.goto == END, command.goto
    assert command.update["status"] == team.STATUS_COMPLETED


@check("scratchpad reload restores state and turn continuation")
def _():
    first = team.supervisor(make_state("T09", snippets=[SNIPPET]))
    saved = team._load_state("T09")
    assert saved is not None
    assert saved["turn_count"] == 1
    assert saved["snippets"] == [SNIPPET]
    assert saved["topic_id"] == "T09"
    second = team.supervisor(saved)
    assert second.goto == "writer", second.goto
    assert second.update["turn_count"] == 2


@check("stop-after drill interrupts, then resume continues from scratchpad")
def _():
    os.environ["TEAM_STOP_AFTER_SUPERVISOR_TURNS"] = "1"
    try:
        command = team.supervisor(make_state("T10"))
    finally:
        os.environ.pop("TEAM_STOP_AFTER_SUPERVISOR_TURNS", None)
    assert command.goto == END, command.goto
    assert command.update["status"] == team.STATUS_INTERRUPTED
    resumed, was_resumed = team._resume_state("Assess SwiftCart delivery growth, reliability and costs.", "T10")
    assert was_resumed is True
    assert resumed["resume_count"] == 1
    assert resumed["status"] == team.STATUS_RUNNING
    following = team.supervisor(resumed)
    assert following.goto == "researcher", following.goto
    assert following.update["turn_count"] == 2


@check("completed runs do not resume")
def _():
    team._persist_state(make_state("T11", status=team.STATUS_COMPLETED))
    fresh, was_resumed = team._resume_state("topic", "T11")
    assert was_resumed is False
    assert fresh["turn_count"] == 0


@check("typed handoff tools are the only routing surface")
def _():
    for name in team.HANDOFF_TOOL_NAMES:
        assert name in team.HANDOFFS, name
    assert set(team.HANDOFFS) == set(team.HANDOFF_TOOL_NAMES)
    assert team.hand_off_to_researcher().goto == "researcher"
    assert team.hand_off_to_writer().goto == "writer"
    assert team.hand_off_to_reviewer().goto == "reviewer"
    source = inspect.getsource(team.supervisor)
    for forbidden in ("search_corpus", "call_tool", "corpus.csv", "mcp", "urllib"):
        assert forbidden not in source, forbidden
    # The supervisor never builds a Command itself: routing flows through HANDOFFS
    # plus the single END path in _finish.
    assert "Command(" not in source
    assert "HANDOFFS[tool]" in source


@check("required shared-state fields exist in the schema")
def _():
    annotations = team.State.__annotations__
    for field in ("topic", "snippets", "draft_memo", "review_verdict",
                  "turn_count", "summary", "status"):
        assert field in annotations, field


# --------------------------------------------------------------------------- #
# R2: bounded roles, real MCP researcher, typed memo and verdict
# --------------------------------------------------------------------------- #
SENTENCES = (
    "SwiftCart completed one million deliveries in 2025, up from eight hundred thousand in 2024 [P05]. "
    "Its on-time delivery rate rose from ninety percent to ninety-two percent over the same period [P05]. "
    "Average cost per delivery rose from five credits to five point four credits [P06]. "
    "The fictional annual report cited higher fuel and labour costs as the drivers of that increase [P06]. "
)
EVIDENCE = " ".join([SENTENCES] * 4).strip()
GOOD_BODY = (
    EVIDENCE
    + "\n\nInterpretation: The cited figures show higher delivery volume and improved reliability "
      "alongside a higher average cost per delivery."
    + "\n\nLimitations: The source does not state why costs rose, whether the cost figure includes "
      "maintenance, or how satisfied customers are."
)
GOOD_MEMO = {
    "title": "SwiftCart Delivery Growth and Costs",
    "body": GOOD_BODY,
    "footnotes": ["[P05] SwiftCart delivery volume and reliability, 2025.",
                  "[P06] SwiftCart cost per delivery, 2025."],
}
SHORT_MEMO = {"title": "Short", "body": "SwiftCart grew [P05].", "footnotes": ["[P05] SwiftCart."]}
SNIPPETS = [
    {"text": "SwiftCart completed 1 million deliveries in 2025, up from 800,000 in 2024. "
             "Its on-time delivery rate rose from 90% to 92%.", "source": "P05"},
    {"text": "SwiftCart's average cost per delivery rose from 5.00 credits in 2024 to 5.40 credits "
             "in 2025 because of fuel and labour costs.", "source": "P06"},
]


def fake_chat(data_or_fn, calls=None):
    def _fake(messages, **kwargs):
        if calls is not None:
            calls.append(messages)
        data = data_or_fn(messages) if callable(data_or_fn) else data_or_fn
        return {"text": json.dumps(data), "data": data, "total_tokens": 100,
                "usage": {"total_tokens": 100}, "model": "fake"}
    return _fake


class patched_chat:
    def __init__(self, fn):
        self.fn = fn

    def __enter__(self):
        self.previous = team.CHAT_JSON
        team.CHAT_JSON = self.fn

    def __exit__(self, *exc):
        team.CHAT_JSON = self.previous


@check("researcher uses a real stdio MCP tool call and returns 3-5 cited snippets")
def _():
    state = make_state("T20")
    command = team.researcher(state)
    assert command.goto == "supervisor", command.goto
    snippets = command.update["snippets"]
    assert 3 <= len(snippets) <= 5, len(snippets)
    assert all(snippet["source"] in team.VALID_SOURCE_IDS for snippet in snippets)
    assert all(snippet["text"].strip() for snippet in snippets)
    assert command.update["mcp_calls"] >= 1
    evidence = command.update["mcp_evidence"][-1]
    assert evidence["transport"] == "stdio"
    assert "search_corpus" in evidence["tools"]
    assert "corpus://market/corpus.csv" in evidence["resources"]
    assert evidence["tool_calls"] == command.update["mcp_calls"]
    saved = team._load_state("T20")
    assert saved is not None and len(saved["snippets"]) == len(snippets)


@check("writer uses only shared snippets and returns a typed memo")
def _():
    calls = []
    state = make_state("T21", snippets=SNIPPETS)
    with patched_chat(fake_chat(GOOD_MEMO, calls)):
        command = team.writer(state)
    assert command.goto == "supervisor"
    memo = command.update["draft_memo"]
    assert memo["title"] and memo["body"] and memo["footnotes"]
    assert 250 <= team._word_count(memo["body"]) <= 350
    assert command.update["review_verdict"] is None
    assert command.update["total_tokens"] == 100
    prompt = "\n".join(message["content"] for message in calls[0])
    assert "SwiftCart completed 1 million deliveries" in prompt
    assert "Northstar" not in prompt  # corpus content outside the snippets is absent
    assert "search_corpus" not in prompt


@check("writer repairs a too-short memo once")
def _():
    responses = [SHORT_MEMO, GOOD_MEMO]
    calls = []

    def responder(messages):
        calls.append(messages)
        return responses[min(len(calls) - 1, 1)]

    state = make_state("T22", snippets=SNIPPETS)
    with patched_chat(fake_chat(responder)):
        command = team.writer(state)
    assert len(calls) == 2
    assert command.update["total_tokens"] == 200
    assert 250 <= team._word_count(command.update["draft_memo"]["body"]) <= 350
    assert not any("writer validation" in error for error in command.update["errors"])


@check("writer rejects citations that are not in the snippets")
def _():
    bad = {"title": "Bad citations", "body": GOOD_BODY.replace("[P06]", "[P12]"),
           "footnotes": ["[P05] SwiftCart.", "[P12] Invented."]}
    calls = []
    state = make_state("T23", snippets=SNIPPETS)
    with patched_chat(fake_chat(lambda messages: bad if len(calls) == 1 else GOOD_MEMO, calls)):
        command = team.writer(state)
    assert len(calls) == 2
    assert "P12" not in team.memo_text(command.update["draft_memo"])


@check("reviewer accepts a clean memo and records the typed verdict")
def _():
    state = make_state("T24", snippets=SNIPPETS, draft_memo=GOOD_MEMO)
    with patched_chat(fake_chat({"decision": "accept", "reasons": ["All rubric items pass."]})):
        command = team.reviewer(state)
    assert command.update["review_verdict"]["decision"] == "accept"
    assert command.update["rejections"] == 0
    assert command.update["verdict_history"][-1]["decision"] == "accept"


@check("reviewer sees only the memo and rubric, never the snippets")
def _():
    calls = []
    state = make_state("T25", snippets=SNIPPETS, draft_memo=GOOD_MEMO)
    with patched_chat(fake_chat({"decision": "accept", "reasons": ["ok"]}, calls)):
        team.reviewer(state)
    prompt = "\n".join(message["content"] for message in calls[0])
    for snippet in SNIPPETS:
        assert snippet["text"] not in prompt
    assert "SwiftCart's average cost per delivery rose from 5.00 credits" not in prompt


@check("reviewer gates length with a deterministic revise verdict")
def _():
    state = make_state("T26", snippets=SNIPPETS, draft_memo=SHORT_MEMO)
    with patched_chat(fake_chat({"decision": "accept", "reasons": ["looks fine"]})):
        command = team.reviewer(state)
    verdict = command.update["review_verdict"]
    assert verdict["decision"] == "revise"
    assert command.update["rejections"] == 1
    assert any("word count" in reason for reason in verdict["reasons"])


@check("worker errors stop the run with an explicit error status")
def _():
    state = make_state("T27", snippets=SNIPPETS)

    def boom(messages, **kwargs):
        raise RuntimeError("provider down")

    with patched_chat(boom):
        command = team.writer(state)
    assert command.update["status"] == team.STATUS_ERROR
    merged = team._normalize_state({**state, **command.update})
    final = team.supervisor(merged)
    assert final.goto == END
    assert final.update["status"] == team.STATUS_ERROR


@check("offline end-to-end flow: Supervisor-Research-Write-Review-Accept")
def _():
    state = make_state("T28")
    calls = []

    def responder(messages):
        joined = "\n".join(message["content"] for message in messages)
        if "ITEM 5 RUBRIC" in joined:
            return {"decision": "accept", "reasons": ["Grounded, cited and within limits."]}
        return GOOD_MEMO

    with patched_chat(fake_chat(responder, calls)):
        result = team.run_topic("Assess SwiftCart delivery growth, reliability and costs.", "T28")
    assert result["status"] == team.STATUS_COMPLETED, result
    assert result["turns"] == 4, result["turns"]
    assert result["rejections"] == 0
    assert result["mcp_calls"] >= 1
    assert "[P05]" in result["memo"]
    nodes = [entry["node"] for entry in result["trace"]]
    assert nodes == ["supervisor", "researcher", "supervisor", "writer",
                     "supervisor", "reviewer", "supervisor"], nodes
    saved = team._load_state("T28")
    assert saved is not None and saved["status"] == team.STATUS_COMPLETED
    assert saved["draft_memo"]["title"] == GOOD_MEMO["title"]



class patched_text:
    def __init__(self, fn):
        self.fn = fn

    def __enter__(self):
        self.previous = team.CHAT_TEXT
        team.CHAT_TEXT = self.fn

    def __exit__(self, *exc):
        team.CHAT_TEXT = self.previous


def fake_summary(text="Topic: SwiftCart delivery. Decisions: turn 1 researcher. "
                      "Rejections (1): body too short. Draft: 20 words; cited P05. Pending: writer."):
    def _fake(messages, **kwargs):
        return {"text": text, "total_tokens": 120, "usage": {"total_tokens": 120}, "model": "fake"}
    return _fake


@check("compaction does nothing below the 8,000 token threshold")
def _():
    state = make_state("T30")
    state["transcript_tokens"] = 7999
    assert team._maybe_compact(state) == {}


@check("compaction triggers above 8,000 tokens and preserves decisions, rejections and draft size")
def _():
    state = make_state("T31", snippets=[SNIPPET], draft_memo=MEMO, rejections=1,
                       verdict_history=[REVISE], turn_count=4)
    state["messages"] = [{"role": "supervisor", "node": "supervisor", "turn": 1,
                          "content": "Turn 1: hand off to researcher.", "tokens": 9000}]
    state["transcript_tokens"] = 9000
    captured = []

    def summarizer(messages, **kwargs):
        captured.append(messages)
        return fake_summary()(messages, **kwargs)

    with patched_text(summarizer):
        update = team._maybe_compact(state)
    assert update["compacted_upto"] == 1
    assert update["summary"].startswith("Topic: SwiftCart")
    assert update["compaction_events"][0]["history_tokens_before"] == 9000
    assert update["transcript_tokens"] < 9000
    prompt = captured[0][-1]["content"]
    for needle in ("Topic:", "Decisions:", "Rejections (1):", "Draft:", "Pending:"):
        assert needle in prompt, needle
    assert "Cost evidence is missing." in prompt
    event = update["compaction_events"][0]
    assert event["digest"] and event["reason"].startswith("history 9000")


@check("repeating compaction for the same history span is idempotent")
def _():
    state = make_state("T32", snippets=[SNIPPET], draft_memo=MEMO, turn_count=3)
    state["messages"] = [{"role": "supervisor", "node": "supervisor", "turn": 1,
                          "content": "Turn 1: hand off.", "tokens": 9000}]
    state["transcript_tokens"] = 9000
    with patched_text(fake_summary()):
        first = team._maybe_compact(state)
        assert len(first["compaction_events"]) == 1
        merged = {**state, **first}
        merged["transcript_tokens"] = 9000  # force the trigger again
        again = team._maybe_compact(merged)
    assert again == {}, again


@check("later history replaces the summary instead of appending duplicates")
def _():
    state = make_state("T33", snippets=[SNIPPET], draft_memo=MEMO, turn_count=3)
    state["messages"] = [{"role": "supervisor", "node": "supervisor", "turn": 1,
                          "content": "Turn 1: hand off.", "tokens": 9000}]
    state["transcript_tokens"] = 9000
    with patched_text(fake_summary("OLD SUMMARY")):
        first = team._maybe_compact(state)
    merged = {**state, **first}
    merged["messages"] = merged["messages"] + [
        {"role": "writer", "node": "writer", "turn": 2, "content": "Draft ready.", "tokens": 9000}]
    merged["transcript_tokens"] = 9000
    with patched_text(fake_summary("NEW SUMMARY")):
        second = team._maybe_compact(merged)
    assert second["summary"] == "NEW SUMMARY"
    assert len(second["compaction_events"]) == 2
    combined = second["summary"]
    assert combined.count("NEW SUMMARY") == 1 and "OLD SUMMARY" not in combined


@check("compaction failure is recorded and does not stop the run")
def _():
    state = make_state("T34", turn_count=2)
    state["messages"] = [{"role": "supervisor", "node": "supervisor", "turn": 1,
                          "content": "Turn 1.", "tokens": 9000}]
    state["transcript_tokens"] = 9000

    def boom(messages, **kwargs):
        raise RuntimeError("summary provider down")

    with patched_text(boom):
        update = team._maybe_compact(state)
    assert update["compaction_events"][-1]["failed"] is True
    assert "summary" not in update
    assert any("compaction:" in error for error in update["errors"])


@check("terminal summary gains later verdicts without duplicating the line")
def _():
    state = make_state("T35", snippets=[SNIPPET], draft_memo=MEMO, rejections=1,
                       verdict_history=[REVISE, ACCEPT], turn_count=4)
    state["summary"] = "Topic: SwiftCart. Rejections (1): body too short."
    updated = team._summary_with_final_state(state, team.STATUS_COMPLETED)
    assert "Final state: 2 reviewer verdict(s)" in updated
    assert "latest accept" in updated
    assert "status completed" in updated
    state["summary"] = updated
    assert team._summary_with_final_state(state, team.STATUS_COMPLETED) == updated


def main() -> int:
    failed = [name for name, verdict in RESULTS if verdict == "FAIL"]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed; scratchpads in {RUNS}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
