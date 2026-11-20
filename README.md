# Week 18 Research Memo Generator

A multi-agent research-memo system built on the supplied fictional corpus. A supervisor coordinates a Researcher, Writer and Reviewer through typed handoffs; the Researcher uses a real stdio MCP server, and the team persists typed state so a stopped run can resume. The supplied single-agent `baseline.py` is the comparator.

## Architecture

- **Supervisor:** routes only through `hand_off_to_researcher`, `hand_off_to_writer` and `hand_off_to_reviewer`. It never calls corpus or worker tools. It stops at 8 supervisor turns and escalates to a human after 3 rejections.
- **Researcher:** connects to the MCP server over stdio, reads `corpus://market/corpus.csv` and `corpus://market/glossary.md`, and calls `search_corpus`. It returns 3–5 cited snippets, using direct evidence plus labeled context snippets.
- **Writer:** receives only shared state, not raw corpus access. It produces a typed memo with a 250–350 word body, claim-level citations and limitations. A bounded repair loop handles validation, uncited evidence and short drafts.
- **Reviewer:** sees only the memo and a static rubric, not the corpus. Deterministic checks cover memo structure, citations, length and limitations; the model judges tone. It returns a typed accept/revise verdict.
- **Shared state:** `topic`, `snippets`, `draft_memo`, `review_verdict`, `turn_count`, `summary` and `status`. Each supervisor turn writes `scratchpad/run-<id>.json` atomically. `run_topic` resumes `running` or `interrupted` runs; completed runs start fresh.
- **Memory:** a rolling summary compacts the transcript above 8,000 cumulative provider-reported tokens. Compaction preserves the topic, decisions, rejection history and draft length, and is idempotent for the same history span.

## Setup

Use Python 3.11 or later:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create `.env` with `MODEL_API_KEY`, `MODEL_BASE_URL` and `MODEL_NAME`. `.env` is git-ignored; never print, commit or submit the key. The evaluation used `gpt-4o-mini` at temperature 0 for both systems.

## Run

```bash
python check_server.py          # MCP resources and tools over stdio
python verify_mcp.py            # researcher/MCP verification evidence
python test_team.py             # 28 offline checks
python evaluate.py --output results/evaluation.json
```

`evaluate.py` runs the baseline and the team on all five fixed topics, then applies the automatic length, citation-presence and valid-ID checks. It writes raw results only; manual rubric review is separate.

## Evaluation results

Raw output is in `results/evaluation.json`. The manually annotated copy is `results/evaluation_reviewed.json`; the full review is `results/manual_review.md`.

| System | Manual pass | Manual fail | Failure rate | Automatic pass |
|---|---:|---:|---:|---:|
| Baseline | 2/5 | 3/5 | 0.60 | 4/5 |
| Multi-agent team | 4/5 | 1/5 | 0.20 | 5/5 |

- **Error-amplification ratio:** `0.20 / 0.60 = 0.33`. The baseline failure rate is non-zero, so the ratio is defined; both rates and counts are reported because n = 5.
- **Baseline failures:** T02 (unsupported market-position conclusion and empty footnotes), T04 (empty footnotes), T05 (249-word body, below the 250-word minimum).
- **Team failure:** T01 (one unlabeled “growing interest” inference in the main body; the separate interpretation sentence is acceptable).
- **Execution errors:** none. All 10 runs completed.
- **Tokens:** baseline 5,469 total; team 63,246 total. Team runs used 21 turns, 1 rejection and 26 MCP calls. Team T01 resumed from a pre-evaluation scratchpad, so its token total is not a clean fresh-run cost comparison.
- **Automatic/manual difference:** the automatic check counts title, headers and footnotes. Baseline T01 fails it at 355 total words but passes the 250–350 body rule at 301; baseline T05 passes at 301 total but fails the body rule at 249.

## Limitations

- The baseline receives the full corpus in one call; the team retrieves excerpts through MCP. This compares architectures, not a controlled experiment isolating retrieval, prompting and orchestration.
- Five topics and one run per topic are too small for statistical claims; temperature 0 does not eliminate provider nondeterminism.
- T01’s team run resumed from a pre-evaluation scratchpad, so its cost is indicative rather than like-for-like.
- Rubric items 4–6 require judgment. Each decision and its evidence are recorded in the reviewed JSON and the manual review.
- Provider cost was not returned; only provider-reported token counts are reported. No metric was fabricated.

## Main files

- `team.py` — supervisor, workers, MCP retrieval, state, resume, compaction and traces
- `mcp_server.py` / `mcp_client.py` — stdio MCP server and client
- `baseline.py` — single-agent full-corpus comparator
- `evaluate.py` — automatic evaluation runner
- `topics.json`, `data/` — fixed topics, corpus and glossary
- `results/` — raw results, reviewed results, memos and manual review
- `scratchpad/` — per-topic state and traces
- `evidence/` — MCP verification and pre-R6 scratchpad evidence
