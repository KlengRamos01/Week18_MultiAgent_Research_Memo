from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "results" / "report_assets"
OUT = Path.home() / "Downloads" / "Week18_KlengRamos_MultiAgent_Project_Report.docx"

NAVY = RGBColor(31, 78, 121)
DARK = RGBColor(30, 41, 59)
GREY = RGBColor(89, 89, 89)
LIGHT_BLUE = "DCE6F1"
LIGHT_GREY = "F2F2F2"
WHITE = RGBColor(255, 255, 255)

rev = json.loads((ROOT / "results" / "evaluation_reviewed.json").read_text(encoding="utf-8"))
raw = json.loads((ROOT / "results" / "evaluation.json").read_text(encoding="utf-8"))
mcp = json.loads((ROOT / "evidence" / "mcp_verification.json").read_text(encoding="utf-8"))

# Facts used in the report (all from project artifacts).
base = rev["baseline"]
team = rev["multi_agent"]
by_base = {r["topic_id"]: r for r in base}
by_team = {r["topic_id"]: r for r in team}
base_tokens = sum(r["total_tokens"] for r in base)
team_tokens = sum(r["total_tokens"] for r in team)
base_turns = sum(r["turns"] for r in base)
team_turns = sum(r["turns"] for r in team)
base_rej = sum(r["rejections"] for r in base)
team_rej = sum(r["rejections"] for r in team)
base_mcp = sum(r["mcp_calls"] for r in base)
team_mcp = sum(r["mcp_calls"] for r in team)
base_pass = sum(1 for r in base if r["checks"]["rubric_pass"])
team_pass = sum(1 for r in team if r["checks"]["rubric_pass"])
base_fail = 5 - base_pass
team_fail = 5 - team_pass
base_rate = base_fail / 5
team_rate = team_fail / 5
ear = team_rate / base_rate
base_auto = sum(1 for r in base if r["checks"]["automatic_checks_pass"])
team_auto = sum(1 for r in team if r["checks"]["automatic_checks_pass"])

t03 = json.loads((ROOT / "scratchpad" / "run-T03.json").read_text(encoding="utf-8"))
t05 = json.loads((ROOT / "scratchpad" / "run-T05.json").read_text(encoding="utf-8"))
t01 = json.loads((ROOT / "scratchpad" / "run-T01.json").read_text(encoding="utf-8"))
t01_before = json.loads((ROOT / "evidence" / "pre_r6_scratchpads" / "run-T01.json").read_text(encoding="utf-8"))
t03s = t03["state"]
t05s = t05["state"]
t01s = t01["state"]
t01bs = t01_before["state"]

doc = Document()

# Page setup and base styles
section = doc.sections[0]
section.top_margin = Inches(0.9)
section.bottom_margin = Inches(0.9)
section.left_margin = Inches(0.9)
section.right_margin = Inches(0.9)
section.different_first_page_header_footer = True

styles = doc.styles
normal = styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(10.5)
normal.font.color.rgb = DARK
normal.paragraph_format.space_after = Pt(6)
normal.paragraph_format.line_spacing = 1.12

for name, size in (("Heading 1", 16), ("Heading 2", 12.5), ("Heading 3", 11)):
    st = styles[name]
    st.font.name = "Calibri"
    st.font.size = Pt(size)
    st.font.bold = True
    st.font.color.rgb = NAVY
    st.paragraph_format.space_before = Pt(12)
    st.paragraph_format.space_after = Pt(5)
    st.paragraph_format.keep_with_next = True


def add_page_field(paragraph, field: str) -> None:
    begin_run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    begin_run._r.append(begin)

    instr_run = paragraph.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {field} "
    instr_run._r.append(instr)

    separate_run = paragraph.add_run()
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    separate_run._r.append(separate)

    result_run = paragraph.add_run("1")
    result_run.font.size = Pt(8.5)
    result_run.font.color.rgb = GREY

    end_run = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    end_run._r.append(end)


def add_total_page_field(paragraph) -> None:
    add_page_field(paragraph, "NUMPAGES")


def add_header_footer() -> None:
    header = section.header
    hp = header.paragraphs[0]
    hp.text = ""
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = hp.add_run("Week 18 Project Evidence Report  |  Kleng Ramos")
    run.font.size = Pt(8.5)
    run.font.color.rgb = GREY

    footer = section.footer
    fp = footer.paragraphs[0]
    fp.text = ""
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = fp.add_run("Page ")
    r.font.size = Pt(8.5)
    r.font.color.rgb = GREY
    add_page_field(fp, "PAGE")
    r2 = fp.add_run(" of ")
    r2.font.size = Pt(8.5)
    r2.font.color.rgb = GREY
    add_total_page_field(fp)
    for r in fp.runs:
        r.font.size = Pt(8.5)
        r.font.color.rgb = GREY


add_header_footer()


def shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_margins(cell, top=50, bottom=50, left=70, right=70) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for m, v in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        node = tc_mar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def add_table(headers, rows, widths, font_size=8.5, first_col_bold=False):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.rows[0]._tr.get_or_add_trPr()
    hdr_pr = table.rows[0]._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    hdr_pr.append(tbl_header)
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.width = Inches(widths[i])
        shade(cell, "1F4E79")
        set_cell_margins(cell)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(str(h))
        run.bold = True
        run.font.size = Pt(font_size)
        run.font.color.rgb = WHITE
    for ridx, row in enumerate(rows):
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cell = cells[i]
            cell.width = Inches(widths[i])
            set_cell_margins(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            if ridx % 2 == 1:
                shade(cell, LIGHT_GREY)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            run = p.add_run(str(value))
            run.font.size = Pt(font_size)
            if first_col_bold and i == 0:
                run.bold = True
    return table


def add_caption(text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(8)
    r = p.add_run(text)
    r.italic = True
    r.font.size = Pt(8.5)
    r.font.color.rgb = GREY


def add_figure(path: Path, width_in=6.4, caption="") -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(path), width=Inches(width_in))
    if caption:
        add_caption(caption)


def add_bullets(items, style="List Bullet") -> None:
    for item in items:
        p = doc.add_paragraph(style=style)
        p.paragraph_format.space_after = Pt(3)
        p.add_run(item)


def add_para(text: str, bold_lead: str | None = None) -> None:
    p = doc.add_paragraph()
    if bold_lead and text.startswith(bold_lead):
        p.add_run(bold_lead).bold = True
        p.add_run(text[len(bold_lead):])
    else:
        p.add_run(text)


def add_callout(label: str, value: str, fill="EAF1F8") -> None:
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    cell = table.cell(0, 0)
    cell.width = Inches(6.5)
    shade(cell, fill)
    set_cell_margins(cell, top=100, bottom=100, left=140, right=140)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run(label + "  ")
    r.bold = True
    r.font.size = Pt(9)
    r.font.color.rgb = NAVY
    r2 = p.add_run(value)
    r2.font.size = Pt(9)
    r2.font.color.rgb = DARK


# ---------------- Cover ----------------
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(90)
p.alignment = WD_ALIGN_PARAGRAPH.LEFT
r = p.add_run("PROJECT EVIDENCE REPORT")
r.bold = True
r.font.size = Pt(12)
r.font.color.rgb = RGBColor(37, 99, 235)
r.font.name = "Calibri"

p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(6)
p.paragraph_format.space_after = Pt(2)
r = p.add_run("Multi-Agent Research Memo Generator")
r.bold = True
r.font.size = Pt(30)
r.font.color.rgb = NAVY

p = doc.add_paragraph()
p.paragraph_format.space_after = Pt(22)
r = p.add_run("Week 18  |  Supervisor–Researcher–Writer–Reviewer system with genuine MCP retrieval")
r.font.size = Pt(12.5)
r.font.color.rgb = GREY

# thin rule
p = doc.add_paragraph()
p.paragraph_format.space_after = Pt(18)
pPr = p._p.get_or_add_pPr()
pBdr = OxmlElement("w:pBdr")
bottom = OxmlElement("w:bottom")
bottom.set(qn("w:val"), "single")
bottom.set(qn("w:sz"), "12")
bottom.set(qn("w:space"), "1")
bottom.set(qn("w:color"), "1F4E79")
pBdr.append(bottom)
pPr.append(pBdr)

cover_rows = [
    ("Learner", "Kleng Ramos"),
    ("Workspace", "Week18_Research_Memo_Learner_Starter  (user: klengramos)"),
    ("Project", "Multi-Agent Research Memo Generator"),
    ("Report date", "19 November 2026"),
    ("Version / status", "1.0  |  Final"),
    ("File", "Week18_KlengRamos_MultiAgent_Project_Report.docx"),
]
t = doc.add_table(rows=0, cols=2)
t.alignment = WD_TABLE_ALIGNMENT.LEFT
t.autofit = False
for k, v in cover_rows:
    cells = t.add_row().cells
    cells[0].width = Inches(1.45)
    cells[1].width = Inches(5.0)
    p0 = cells[0].paragraphs[0]
    p0.paragraph_format.space_after = Pt(5)
    r0 = p0.add_run(k)
    r0.bold = True
    r0.font.size = Pt(10)
    r0.font.color.rgb = NAVY
    p1 = cells[1].paragraphs[0]
    p1.paragraph_format.space_after = Pt(5)
    r1 = p1.add_run(v)
    r1.font.size = Pt(10)
    r1.font.color.rgb = DARK

p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(70)
p.add_run("Prepared from the project’s own evaluation, scratchpad, MCP and test artifacts. "
          "No external benchmarks or invented metrics are used.").font.size = Pt(9)
p.runs[-1].font.color.rgb = GREY
p.runs[-1].italic = True

doc.add_page_break()

# ---------------- Contents ----------------
doc.add_heading("Contents", level=1)
contents = [
    "1.  Executive summary",
    "2.  Project overview and architecture",
    "3.  Role boundaries",
    "4.  Evidence of a complete Supervisor–Researcher–Writer–Reviewer run",
    "5.  MCP invocation evidence",
    "6.  Scratchpad persistence and successful resume",
    "7.  Rolling-summary memory event",
    "8.  Evaluation results for all five seed topics",
    "9.  Challenges, limitations and improvements",
    "10. Stretch activity",
    "Appendix A.  Artifact index",
    "Appendix B.  T03 final memo",
    "Appendix C.  Reproduction and validation capture",
]
for line in contents:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(line)
    r.font.size = Pt(10.5)
    r.font.color.rgb = DARK

# ---------------- 1 Executive summary ----------------
doc.add_heading("1. Executive summary", level=1)
add_para(
    "This project implements a multi-agent research-memo system on the supplied fictional Northstar corpus. "
    "A supervisor coordinates a Researcher, Writer and Reviewer through typed handoffs. The Researcher reaches "
    "the corpus through a genuine stdio MCP server; the Writer receives only shared state; the Reviewer sees only "
    "the memo and a static rubric. State is written atomically to a scratchpad after every supervisor turn, and a "
    "rolling summary compacts the transcript above 8,000 cumulative tokens."
)
add_para(
    "The system was evaluated on the five fixed seed topics with the same model and settings as the supplied "
    "single-agent baseline: gpt-4o-mini at temperature 0. All ten runs completed with no execution errors. "
    "Manual rubric review found 2/5 baseline passes and 4/5 multi-agent passes. The baseline failure rate was "
    "0.60 and the multi-agent failure rate was 0.20, giving an Error-Amplification Ratio of 0.33."
)
add_callout("Evaluation snapshot:", "baseline 2/5 manual pass (0.60 failure rate)  |  team 4/5 manual pass (0.20 failure rate)  |  EAR 0.33  |  10/10 runs completed")
add_para("")
add_para("Key findings:", bold_lead="Key findings:")
add_bullets([
    "The team produced four manual passes (T02–T05) and one manual failure (T01). The T01 failure is a single unlabeled “growing interest” inference in the main body; the separate interpretation sentence is acceptable.",
    "The baseline produced two manual passes (T01, T03) and three manual failures: T02 (unsupported market-position conclusion and empty footnotes), T04 (empty footnotes) and T05 (249-word body, below the 250-word minimum).",
    "Automatic checks passed 4/5 baseline runs and 5/5 team runs. Manual review changes both counts because the automatic checks cannot assess grounding, overreach, footnote quality or the body-word rule.",
    "The team used 63,246 tokens, 21 turns, 1 rejection and 26 MCP calls across five topics. The baseline used 5,469 tokens, 5 turns, 0 rejections and 0 MCP calls.",
    "No stretch activity was attempted. No stretch results are reported.",
])
add_para(
    "All figures in this report come from results/evaluation.json, results/evaluation_reviewed.json, "
    "results/manual_review.md, the scratchpad JSON files, evidence/mcp_verification.json and the captured "
    "MCP/test output. The raw evaluation file is preserved unchanged; manual annotations are in the reviewed copy."
)

doc.add_page_break()

# ---------------- 2 Architecture ----------------
doc.add_heading("2. Project overview and architecture", level=1)
add_para(
    "The workflow keeps retrieval, drafting and review in separate roles. The supervisor never reads the corpus "
    "or calls worker tools directly; it routes through three typed handoff tools: hand_off_to_researcher, "
    "hand_off_to_writer and hand_off_to_reviewer. Each worker returns a typed update to shared state. The "
    "supervisor writes the state to scratchpad/run-<id>.json after every turn and stops at 8 supervisor turns or "
    "escalates to a human after 3 rejections."
)
add_figure(
    ASSETS / "architecture.png",
    width_in=6.45,
    caption="Figure 1. Brief workflow diagram. Solid double arrows are typed handoffs; dashed lines show persisted state and rolling memory. "
            "The Researcher alone reaches the MCP server, and the Writer and Reviewer exchange only typed state or the memo."
)
add_para(
    "The state schema is typed: topic, snippets, draft_memo, review_verdict, turn_count, summary and status, plus "
    "operational fields such as total_tokens, mcp_calls, trace, rejections and compaction_events. The Researcher "
    "returns 3–5 cited snippets; direct snippets are used by the Writer, while context snippets are retained in "
    "state but excluded from the Writer prompt. The Writer produces a typed memo. The Reviewer returns a typed "
    "accept/revise verdict with reasons."
)
add_para(
    "Memory is separate from the role handoffs. When the cumulative provider-reported transcript exceeds 8,000 "
    "tokens, the system compacts history into a rolling summary that preserves the topic, decisions, rejection "
    "history and draft length. The compaction is idempotent for the same history span; repeating it does not "
    "append duplicate decisions."
)

doc.add_page_break()

# ---------------- 3 Role boundaries ----------------
doc.add_heading("3. Role boundaries", level=1)
add_para(
    "The boundaries are deliberately strict. The table below states what each role may access and what it must "
    "not access; the evidence column points to the implementation and checks that enforce the boundary."
)
role_rows = [
    ("Supervisor", "Shared typed state; the three typed handoff tools.", "Corpus resources, MCP search_corpus, Writer/Reviewer internals, direct drafting.", "Routes only through typed handoffs; stops at 8 turns; escalates after 3 rejections.", "T03 trace shows supervisor handoffs only; test “typed handoff tools are the only routing surface”."),
    ("Researcher", "MCP resources corpus://market/corpus.csv and corpus://market/glossary.md; search_corpus tool.", "Memo drafting or review; direct reads of data/corpus.csv or data/market_corpus.md.", "Returns 3–5 cited snippets and hand_back evidence.", "verify_mcp.py: stdio transport, mcp_calls=4, snippets P01–P03, no_direct_corpus_access=true."),
    ("Writer", "Shared state and direct snippets; a static writing brief.", "MCP server, corpus files, Reviewer internals, raw search results beyond supplied snippets.", "Typed memo with title, body and footnotes; cited evidence only.", "T03 writer hand_back: 257-word body, 2 cited sources, 4 attempts; writer tests reject uncited IDs."),
    ("Reviewer", "The memo and a static rubric.", "Corpus, snippets, MCP tools, Writer prompt or state beyond the memo.", "Typed accept/revise verdict with reasons.", "Reviewer tests: sees only memo and rubric; deterministic length gate; accept/revise verdict recorded."),
    ("Memory", "Transcript history and existing summary.", "External corpus or worker tools.", "Rolling summary above 8,000 tokens; idempotent replacement.", "T05 compaction event; compaction tests for threshold, idempotency, replacement and failure handling."),
]
add_table(
    ["Role", "May access", "Must not access", "Output / limit", "Boundary evidence"],
    role_rows,
    widths=[0.78, 1.35, 1.35, 1.35, 1.65],
    font_size=7.8,
    first_col_bold=True,
)
add_caption("Table 1. Role boundaries and the project evidence used to verify them.")
add_para(
    "The separation is also visible in the complete-run trace: the supervisor hands off to the Researcher, then "
    "the Writer, then the Reviewer, and finally ends the run. No supervisor event calls a corpus or worker tool, "
    "and the Reviewer event is preceded by a memo handoff, not by snippets."
)

# ---------------- 4 Complete run ----------------
doc.add_heading("4. Evidence of a complete Supervisor–Researcher–Writer–Reviewer run", level=1)
add_para(
    "T03 is the cleanest complete run. It finished in four supervisor turns with no rejections, used five MCP "
    "calls, produced a 257-word writer body and ended with an accept verdict. The final memo is 279 total words "
    "(263 body words) and passed both the automatic checks and the manual rubric."
)
add_table(
    ["Run", "Topic", "Status", "Turns", "Tokens", "Rejections", "MCP calls", "Final manual result"],
    [("T03", "SwiftCart delivery growth, reliability and costs", "completed", "4", "8,449", "0", "5", "Pass")],
    widths=[0.55, 1.55, 0.72, 0.52, 0.62, 0.72, 0.62, 0.8],
    font_size=8.3,
)
add_caption("Table 2. T03 run summary from scratchpad/run-T03.json and results/evaluation_reviewed.json.")
trace_rows = []
for e in t03s["trace"]:
    trace_rows.append((
        e.get("turn", ""),
        e.get("node", ""),
        e.get("handoff", ""),
        e.get("status", ""),
        e.get("note", ""),
    ))
add_table(
    ["Turn", "Node", "Handoff", "Status", "Event note"],
    trace_rows,
    widths=[0.45, 0.8, 1.3, 0.65, 3.2],
    font_size=7.7,
)
add_caption("Table 3. Exact trace events for T03, showing the full Supervisor → Researcher → Writer → Reviewer path and the final memory event.")
add_para(
    "The writer attempted four times before producing the 257-word body; the reviewer then issued “accept” with "
    "the reason “All rubric items pass.” The final supervisor event is “Reviewer accepted the memo; run complete.” "
    "The memory event at turn 4 compacted 7 messages from 8,100 to 272 tokens. This is a complete, observable "
    "workflow, not only a final answer."
)
add_para(
    "T01 supplies a second, complementary run: it includes one revise verdict and one rejection, then a rewrite "
    "and a final accept. Its trace is used in Section 6 to show resume behavior. The T03 memo is reproduced in "
    "Appendix B."
)

doc.add_page_break()

# ---------------- 5 MCP ----------------
doc.add_heading("5. MCP invocation evidence", level=1)
add_para(
    "The Researcher uses a real stdio MCP client, not a Python import of the corpus. verify_mcp.py opens an "
    "mcp.ClientSession over stdio, lists resources and tools, reads both resources, calls search_corpus, and then "
    "invokes the project Researcher path. The evidence file records the same transport and the absence of direct "
    "corpus access in both team.researcher and mcp_client."
)
add_table(
    ["Evidence item", "Observed value"],
    [
        ("Server", "mcp_server.py"),
        ("Transport", "stdio"),
        ("Resources", "corpus://market/corpus.csv, corpus://market/glossary.md"),
        ("Tools", "search_corpus"),
        ("Resource reads", "corpus_items=1, glossary_items=1"),
        ("Sample search", "query “solar”; returned 2; sources P01, P02"),
        ("Researcher path", "goto=supervisor; mcp_calls=4; snippets P01, P02, P03; transport=stdio"),
        ("No direct corpus access", "team.researcher=true; mcp_client=true"),
    ],
    widths=[1.65, 4.7],
    font_size=8.6,
    first_col_bold=True,
)
add_caption("Table 4. MCP verification evidence from evidence/mcp_verification.json.")
add_para(
    "The captured stdio log below shows the server receiving ListResourcesRequest, ListToolsRequest, two "
    "ReadResourceRequest calls and a CallToolRequest. It returns both resources, the search_corpus tool, and "
    "P01/P02 search results. This is direct evidence that the server process was launched and queried over stdio."
)
add_figure(
    ASSETS / "mcp_stdio_capture.png",
    width_in=6.35,
    caption="Figure 2. Terminal capture (rendered from evidence/mcp_check_server.txt). Readable points: resources list, tools list, "
            "two resource reads, a CallToolRequest, and search results for solar/P01–P02."
)
add_para(
    "The log is accompanied by evidence/mcp_verification.json, which records transport=stdio, the same resource "
    "and tool names, mcp_calls=4 on the Researcher path, and no_direct_corpus_access=true. The 28/28 offline "
    "checks also include a test that the Researcher uses a real stdio MCP tool call and returns 3–5 cited snippets."
)

doc.add_page_break()

# ---------------- 6 Resume ----------------
doc.add_heading("6. Scratchpad persistence and successful resume", level=1)
add_para(
    "The supervisor writes scratchpad/run-<id>.json after every turn. run_topic resumes only a running or "
    "interrupted scratchpad; completed runs start fresh. T01 provides direct evidence: a running scratchpad with "
    "three snippets was left by an earlier Researcher call, and the evaluator later resumed it and completed the run."
)
add_table(
    ["Artifact", "Status", "Turns", "Snippets", "MCP calls", "Total tokens", "Resume count"],
    [
        ("evidence/pre_r6_scratchpads/run-T01.json (before)", "running", "0", "3 (P01, P02, P03)", "4", "0", "0"),
        ("scratchpad/run-T01.json (after)", "completed", "5", "3 (P01, P02, P03)", "4", "18,999", "1"),
    ],
    widths=[2.2, 0.75, 0.45, 1.15, 0.6, 0.65, 0.6],
    font_size=7.9,
    first_col_bold=True,
)
add_caption("Table 5. Before/after T01 state showing persisted snippets and successful resume. The before-state is archived in evidence/pre_r6_scratchpads.")
add_para(
    "The resumed trace contains the event “Resumed from run-T01.json” and then proceeds through Writer → "
    "Reviewer (revise) → Writer → Reviewer (accept) → end. The run completed with 5 turns, 1 rejection and "
    "18,999 total tokens. This demonstrates both persistence and resume; it also explains why T01 is not a clean "
    "fresh-run cost comparison in the evaluation section."
)
add_para(
    "The state survives a stop because it is atomic and typed. The archived before-state already contains the "
    "snippets and MCP call count, while the after-state contains the completed memo, verdict, rejection count and "
    "resume_count=1. The trace retains the rejection reason and the subsequent repair, so the resumed run is "
    "auditable rather than restarted from scratch."
)

# ---------------- 7 Memory ----------------
doc.add_heading("7. Rolling-summary memory event", level=1)
add_para(
    "T05 records a rolling-summary event at turn 3. The transcript exceeded the 8,000-token trigger; the system "
    "compacted five messages into a 244-token summary and continued to a completed run. All five team runs record "
    "one compaction event; T05 is used here because its event is explicit in the trace and the metrics."
)
add_table(
    ["Compaction field", "Observed value"],
    [
        ("Run", "T05 — CareBridge remote consultations and patient satisfaction"),
        ("Event time", "2026-11-19T02:56:01+00:00"),
        ("Trigger", "history_tokens_before = 10,697 (> 8,000)"),
        ("Compacted up to", "5 messages"),
        ("Summary size", "244 tokens"),
        ("Digest", "d4f57dc1ba4fd136"),
        ("Reason", "history 10697 > 8000"),
        ("Post-compaction transcript", "983 tokens"),
        ("Final run result", "completed; 4 turns; 0 rejections; 11,663 tokens; 5 MCP calls"),
    ],
    widths=[1.9, 4.45],
    font_size=8.5,
    first_col_bold=True,
)
add_caption("Table 6. T05 rolling-summary event from scratchpad/run-T05.json.")
add_para(
    "The summary preserves the topic, the supervisor/worker decisions, the rejection history and the draft size. "
    "The final-state addendum records the later verdict and completion, so a resumed or later reader can still "
    "reconstruct what happened. The event is idempotent for the same history span: the tests check that repeating "
    "compaction does not append duplicate decisions, and that a later history replaces the summary rather than "
    "duplicating it."
)

doc.add_page_break()

# ---------------- 8 Evaluation ----------------
doc.add_heading("8. Evaluation results for all five seed topics", level=1)
add_para(
    "Method. evaluate.py called the supplied baseline.run_topic and the project team.run_topic once per topic on "
    "the five fixed topics. Both systems used gpt-4o-mini at temperature 0 and the same configured endpoint. The "
    "automatic checks cover total word count, citation presence and valid source IDs. Manual review then applied "
    "the six rubric items: 250–350 body words, claim-level citations, valid IDs, evidence versus interpretation, "
    "professional tone/title/footnotes, and limitations. An execution error would count as a failure; none occurred."
)
base_rows = []
for tid in ("T01", "T02", "T03", "T04", "T05"):
    r = by_base[tid]
    if tid == "T01":
        note = "Auto total 355 (fail); body 301 (pass). Manual pass."
    elif tid == "T02":
        note = "Unsupported market-position conclusion; empty [P01]–[P04] footnotes."
    elif tid == "T03":
        note = "Clean; automatic and manual pass."
    elif tid == "T04":
        note = "Empty [P01]–[P12] footnotes; otherwise acceptable."
    else:
        note = "Auto total 301 (pass); body 249 (fail). Manual fail."
    base_rows.append((tid, f"{r['total_tokens']:,}", r["turns"], r["rejections"], r["mcp_calls"], f"{r['checks']['word_count']} / {r['body_word_count']}", "Pass" if r["checks"]["automatic_checks_pass"] else "Fail", "Pass" if r["checks"]["rubric_pass"] else "Fail", note))
add_table(
    ["Topic", "Tokens", "Turns", "Rej.", "MCP", "Total / body words", "Automatic", "Manual", "Review note"],
    base_rows,
    widths=[0.48, 0.62, 0.42, 0.38, 0.4, 0.95, 0.72, 0.62, 2.0],
    font_size=7.5,
)
add_caption("Table 7. Baseline results for all five seed topics. Total/body word counts are shown together because the automatic check and the assignment body rule can disagree.")

team_rows = []
for tid in ("T01", "T02", "T03", "T04", "T05"):
    r = by_team[tid]
    if tid == "T01":
        note = "Unlabeled “growing interest” inference in main body; one recoverable writer-validation retry."
    elif tid == "T02":
        note = "Pass; limitations section is repetitive (14 caveats)."
    elif tid == "T03":
        note = "Clean and balanced; complete-run evidence."
    elif tid == "T04":
        note = "Pass; several limitations repeated in body and section."
    else:
        note = "Pass; limitations-heavy but within length."
    team_rows.append((tid, f"{r['total_tokens']:,}", r["turns"], r["rejections"], r["mcp_calls"], f"{r['checks']['word_count']} / {r['body_word_count']}", "Pass" if r["checks"]["automatic_checks_pass"] else "Fail", "Pass" if r["checks"]["rubric_pass"] else "Fail", note))
add_table(
    ["Topic", "Tokens", "Turns", "Rej.", "MCP", "Total / body words", "Automatic", "Manual", "Review note"],
    team_rows,
    widths=[0.48, 0.62, 0.42, 0.38, 0.4, 0.95, 0.72, 0.62, 2.0],
    font_size=7.5,
)
add_caption("Table 8. Multi-agent results for all five seed topics. T01 resumed from a pre-evaluation scratchpad; the other runs were fresh.")

add_para("Single-agent versus multi-agent comparison", bold_lead="Single-agent versus multi-agent comparison")
add_table(
    ["System", "Manual pass", "Manual fail", "Failure rate", "Automatic pass", "Total tokens", "Turns", "Rejections", "MCP calls"],
    [
        ("Baseline", f"{base_pass}/5", f"{base_fail}/5", f"{base_rate:.2f}", f"{base_auto}/5", f"{base_tokens:,}", base_turns, base_rej, base_mcp),
        ("Multi-agent", f"{team_pass}/5", f"{team_fail}/5", f"{team_rate:.2f}", f"{team_auto}/5", f"{team_tokens:,}", team_turns, team_rej, team_mcp),
    ],
    widths=[1.0, 0.66, 0.62, 0.72, 0.78, 0.75, 0.48, 0.65, 0.62],
    font_size=7.6,
    first_col_bold=True,
)
add_caption("Table 9. Aggregate comparison. Failure rate = failed topics / evaluated topics; no execution errors were observed.")

add_para(
    "Manual review details. The baseline's T01 passes the body rule at 301 words even though the automatic total "
    "is 355; its hedged speculation is labeled as potential rather than asserted. T02 fails on an unsupported "
    "market-position conclusion and empty footnotes; T04 fails on empty footnotes for P01–P12; T05 fails the "
    "250-word body minimum at 249. The team's T02–T05 pass; T01 fails on “The overall trend shows a growing "
    "interest…”, which is not measured by the capacity sources. The team's T02 has 14 limitations and T04 repeats "
    "several caveats, but both stay within the length rule and remain professional."
)
add_para("Error-Amplification Ratio", bold_lead="Error-Amplification Ratio")
add_para(
    f"Baseline failure rate = {base_fail}/5 = {base_rate:.2f}. Multi-agent failure rate = {team_fail}/5 = "
    f"{team_rate:.2f}. EAR = {team_rate:.2f} / {base_rate:.2f} = {ear:.2f}. The baseline failure rate is non-zero, "
    "so the ratio is defined. A ratio below 1 indicates a lower failure rate for the multi-agent system in this "
    "sample. Because n=5, both rates and counts are reported; the ratio is descriptive, not a statistical guarantee."
)
add_para(
    "Zero-baseline analysis: not applicable here because the baseline failure rate is 0.60, not zero. If the "
    "baseline had zero failures, the ratio would be undefined (including 0/0); the report would mark it undefined "
    "and report both rates and counts rather than divide by zero or declare success. If length were interpreted as "
    "total words instead of body words, baseline T01 would fail and baseline T05 would pass; the baseline failure "
    "count would remain 3/5 and the EAR would remain 0.33."
)

doc.add_page_break()

# ---------------- 9 Challenges ----------------
doc.add_heading("9. Challenges, limitations and improvements", level=1)
add_para("Challenges observed", bold_lead="Challenges observed")
add_bullets([
    "Role separation versus useful work. The supervisor is intentionally limited to handoffs, so it cannot repair a memo or fetch evidence itself. The T03 and T01 traces show that the work is carried by the Researcher, Writer and Reviewer, with the supervisor only routing and finalizing.",
    "Grounding and overreach. The baseline T02 conclusion asserts a market position and its causes without supporting data, and the team T01 main body says the trend “shows a growing interest” although the sources measure capacity, not interest. These are the two clearest manual failures.",
    "Presentation quality. Baseline T02 and T04 produced empty footnotes. Team T02 is verbose (14 limitations), T01 repeats the same MW figures, and T04 repeats several limitations. All team memos still fit the 250–350 body rule.",
    "Cost and repair. The team used 63,246 tokens versus 5,469 for the baseline across five topics (about 11.6×). T01 alone used 18,999 tokens because it took 5 turns, one rejection and a rewrite; T03 used 8,449 tokens with no rejection.",
    "Resume and evaluation comparability. T01 resumed from a pre-evaluation scratchpad, so its token total covers post-resume calls and is not a clean fresh-run cost. The other team runs were fresh.",
])
add_para("Limitations", bold_lead="Limitations")
add_bullets([
    "The evaluation uses five topics and one run per topic. Temperature 0 reduces but does not eliminate provider nondeterminism, and n=5 is too small for statistical claims.",
    "The baseline receives the whole corpus in one model call, while the team retrieves excerpts through MCP. This compares the two architectures, not a controlled experiment isolating retrieval, prompting and orchestration.",
    "Manual rubric items 4–6 require judgment. Each decision and its evidence are recorded in results/evaluation_reviewed.json and results/manual_review.md so the review can be audited or repeated.",
    "Provider cost was not returned in the response; only provider-reported token counts are reported. No cost figure is invented.",
])
add_para("Recommended improvements", bold_lead="Recommended improvements")
add_bullets([
    "Run a fresh T01 (and preferably all five topics) from an empty scratchpad, then report the fresh token/cost comparison alongside the resume evidence.",
    "Strengthen the Writer's claim filter to remove unlabeled evaluative phrases such as “growing interest” before the Reviewer sees the memo.",
    "Prioritize two or three limitations per memo instead of exhaustively listing every missing variable. This would address the T02/T04/T05 verbosity without weakening the rubric.",
    "Add retrieval-quality checks for direct versus context snippets, and confirm that the Writer sees only direct cited evidence.",
    "Increase the sample and run multiple repetitions per topic; calibrate the Reviewer against the manual rubric so its accept/revise decisions stay aligned with the evidence standard.",
])

# ---------------- 10 Stretch ----------------
doc.add_heading("10. Stretch activity", level=1)
add_para(
    "No stretch activity was attempted in this project. The project evidence contains the core implementation, "
    "MCP verification, resume and memory evidence, and the five-topic evaluation; no stretch results are reported."
)

doc.add_page_break()

# ---------------- Appendix A ----------------
doc.add_heading("Appendix A. Artifact index", level=1)
artifacts = [
    ("results/evaluation.json", "Raw evaluator output for 5 baseline and 5 multi-agent runs; rubric_pass remains null and is preserved unchanged."),
    ("results/evaluation_reviewed.json", "Reviewed copy with per-run rubric_pass booleans, six rubric items and evidence notes."),
    ("results/manual_review.md", "Full manual review, failure evidence, failure rates, EAR and limitations."),
    ("results/memos.txt", "All ten memo texts used for manual review."),
    ("scratchpad/run-T03.json", "Complete clean run used for Section 4; trace, tokens, memo and verdict."),
    ("scratchpad/run-T05.json", "Rolling-summary event used for Section 7; compaction_events and final state."),
    ("scratchpad/run-T01.json", "Resumed run with one rejection and final accept; resume_count=1."),
    ("evidence/pre_r6_scratchpads/run-T01.json", "Archived running T01 state with three snippets and four MCP calls, used for the before/after resume evidence."),
    ("evidence/mcp_verification.json", "MCP transport, resources, tools, resource reads, search sample and no-direct-access flags."),
    ("evidence/mcp_check_server.txt", "Captured stdio server log used for Figure 2."),
    ("test_team.py output", "28/28 offline checks, including typed routing, MCP Researcher, Writer/Reviewer boundaries, resume and compaction."),
    ("README.md", "Project architecture, run commands, evaluation summary and limitations."),
]
add_table(["Artifact", "Use in this report"], artifacts, widths=[2.25, 4.1], font_size=8.1, first_col_bold=True)
add_caption("Table 10. Project artifacts used as evidence. No external data is used.")

# ---------------- Appendix B ----------------
doc.add_heading("Appendix B. T03 final memo", level=1)
add_para(
    "The following is the final T03 memo from the multi-agent run. It is reproduced verbatim from "
    "results/evaluation.json and corresponds to the complete trace in Section 4."
)
memo_t03 = by_team["T03"]["memo"].strip()
for block in memo_t03.split("\n\n"):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    p.add_run(block).font.size = Pt(9.2)

# ---------------- Appendix C ----------------
doc.add_heading("Appendix C. Reproduction and validation capture", level=1)
add_para("Reproduction commands used for the project evidence:", bold_lead="Reproduction commands used for the project evidence:")
for cmd in [
    ".venv/bin/python evaluate.py --output results/evaluation.json",
    ".venv/bin/python test_team.py",
    "python verify_mcp.py",
]:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.25)
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(cmd)
    r.font.name = "Menlo"
    r.font.size = Pt(8.7)
    r.font.color.rgb = RGBColor(31, 41, 55)
add_para(
    "The raw evaluation file is kept separate from the reviewed file. Manual edits are limited to the reviewed "
    "copy and the manual review; the evaluator's raw output is not rewritten."
)
add_figure(
    ASSETS / "test_suite_capture.png",
    width_in=6.35,
    caption="Figure 3. Terminal capture (rendered from the current test output). Readable result: 28/28 checks passed, including typed handoff routing, "
            "scratchpad resume, real stdio MCP Researcher, Writer/Reviewer boundaries and compaction."
)
add_para(
    "The test capture is supporting validation, not a replacement for the live evaluation. It shows that the "
    "offline checks cover the same boundaries exercised by the report: typed routing, persistence/resume, MCP "
    "retrieval, Writer/Reviewer separation and memory compaction."
)

# Ask Word to refresh PAGE/NUMPAGES fields when the document opens.
settings = doc.settings.element
update_fields = OxmlElement("w:updateFields")
update_fields.set(qn("w:val"), "true")
settings.append(update_fields)

# Final document properties
doc.core_properties.title = "Project Evidence Report — Multi-Agent Research Memo Generator"
doc.core_properties.author = "Kleng Ramos"
doc.core_properties.subject = "Week 18 project evidence"
doc.core_properties.comments = "Built from project evaluation, scratchpad, MCP and test artifacts."

OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(OUT)
print(f"Wrote {OUT}")
print(f"Pages/sections: {len(doc.sections)}; tables: {len(doc.tables)}; paragraphs: {len(doc.paragraphs)}")
