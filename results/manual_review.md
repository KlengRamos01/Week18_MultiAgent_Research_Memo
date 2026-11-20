# R6 evaluation and manual rubric review

## Scope and method

- **Evaluator:** `.venv/bin/python evaluate.py`
- **Raw results:** `results/evaluation.json` (preserved unchanged; every `checks.rubric_pass` is `null`)
- **Reviewed results:** `results/evaluation_reviewed.json` (adds per-item decisions and `rubric_pass` booleans)
- **Topics:** T01–T05, the five fixed topics in `topics.json`
- **Model:** `gpt-4o-mini`
- **Settings:** temperature `0`, same configured OpenAI-compatible endpoint for both systems; credentials were not recorded or printed
- **Cost:** the provider response did not supply a cost field; token counts are reported and no cost is fabricated

The automatic checks count every word in the returned memo, including title, headers and footnotes. The assignment's length rule is a 250–350 word **body**. For this review, body words were counted with the evaluator's regex after removing the title/header lines and the footnotes. Both counts are reported below.

### Rubric used

A memo passes only if **all six** items pass.

1. **Length:** 250–350 body words.
2. **Claim-level citations:** factual claims are supported by a supplied source ID.
3. **Valid sources:** only supplied IDs are used; no invented sources.
4. **Evidence vs interpretation:** evidence is separated from interpretation; no unsupported causal or evaluative overreach.
5. **Tone and presentation:** professional tone, concise title and meaningful footnotes.
6. **Limitations:** relevant caveats and limitations are stated.

Execution errors count as failures. No execution error occurred: all 10 runs finished with `status: completed`. The team T01 run records one recoverable writer-validation retry in `errors`; it is not counted as an execution failure because the run recovered and completed.

## Automatic results

| System | Topic | Status | Tokens | Turns | Rejections | MCP calls | Errors | Total words | Body words | Automatic | Manual |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---|---|
| baseline | T01 | completed | 1,115 | 1 | 0 | 0 | none | 355 | 301 | fail | **pass** |
| baseline | T02 | completed | 1,069 | 1 | 0 | 0 | none | 323 | 299 | pass | **fail** |
| baseline | T03 | completed | 1,113 | 1 | 0 | 0 | none | 329 | 291 | pass | **pass** |
| baseline | T04 | completed | 1,122 | 1 | 0 | 0 | none | 348 | 317 | pass | **fail** |
| baseline | T05 | completed | 1,050 | 1 | 0 | 0 | none | 301 | 249 | pass | **fail** |
| multi-agent | T01 | completed | 18,999 | 5 | 1 | 4 | 1 recoverable writer-validation retry | 276 | 261 | pass | **fail** |
| multi-agent | T02 | completed | 12,328 | 4 | 0 | 6 | none | 301 | 283 | pass | **pass** |
| multi-agent | T03 | completed | 8,449 | 4 | 0 | 5 | none | 279 | 263 | pass | **pass** |
| multi-agent | T04 | completed | 11,807 | 4 | 0 | 6 | none | 308 | 291 | pass | **pass** |
| multi-agent | T05 | completed | 11,663 | 4 | 0 | 5 | none | 295 | 281 | pass | **pass** |

Automatic/manual disagreements are intentional:

- **Baseline T01** fails the automatic total-word check at 355 words, but its body is 301 words. Under the assignment's body rule it passes the length item.
- **Baseline T05** passes the automatic total-word check at 301 words, but its body is 249 words. It fails the length item by one word under the evaluator-style regex (247 by whitespace).

If length is instead interpreted as total words, baseline T01 fails and baseline T05 passes. The baseline failure count remains 3/5, and the error-amplification ratio is unchanged.

## Per-item manual review

| System | Topic | 1 Length | 2 Citations | 3 Valid IDs | 4 Evidence/interpretation | 5 Tone/footnotes | 6 Limitations | Overall |
|---|---|---|---|---|---|---|---|---|
| baseline | T01 | Pass | Pass | Pass | Pass | Pass | Pass | **Pass** |
| baseline | T02 | Pass | Pass | Pass | **Fail** | **Fail** | Pass | **Fail** |
| baseline | T03 | Pass | Pass | Pass | Pass | Pass | Pass | **Pass** |
| baseline | T04 | Pass | Pass | Pass | Pass | **Fail** | Pass | **Fail** |
| baseline | T05 | **Fail** | Pass | Pass | Pass | Pass | Pass | **Fail** |
| multi-agent | T01 | Pass | Pass | Pass | **Fail** | Pass | Pass | **Fail** |
| multi-agent | T02 | Pass | Pass | Pass | Pass | Pass | Pass | **Pass** |
| multi-agent | T03 | Pass | Pass | Pass | Pass | Pass | Pass | **Pass** |
| multi-agent | T04 | Pass | Pass | Pass | Pass | Pass | Pass | **Pass** |
| multi-agent | T05 | Pass | Pass | Pass | Pass | Pass | Pass | **Pass** |

## Evidence for failures

### Baseline T02 — unsupported conclusion and empty footnotes

The conclusion asserts a market outcome and its causes without data:

> "Solara's home battery sales and pricing trends indicate a favorable market position, driven by increased consumer interest in renewable energy solutions and competitive pricing strategies."

The corpus does not measure market position, consumer interest, or competitive strategy; P03 and P04 report unit sales, price, market-coverage and maintenance exclusions only. This is evaluative/causal overreach, so item 4 fails.

The footnotes are also empty:

> `[P01]`<br>`[P02]`<br>`[P03]`<br>`[P04]`

P01 is not used in the text, and none of the entries describes a source. Item 5 fails.

### Baseline T04 — empty footnotes

The memo lists twelve empty footnote entries, `[P01]` through `[P12]`, although the body uses only P05, P07 and P08. The empty entries are not meaningful footnotes and include nine unused IDs. Item 5 fails. The body's evidence/interpretation balance is otherwise acceptable.

### Baseline T05 — body below the minimum

The body is 249 words by the evaluator-style regex (247 by whitespace), below the 250-word minimum. The total including headers and footnotes is 301, which is why the automatic check passes; the assignment's body rule does not. Item 1 fails.

### Multi-agent T01 — one unlabeled inference

The main body states:

> "The overall trend shows a growing interest in rooftop solar installations in Northstar, with both residential and commercial sectors contributing to this growth [P01][P02]."

P01 and P02 measure installed capacity, not interest. The memo's separate Interpretation sentence is acceptable, but "shows a growing interest" in the main body is an unlabeled behavioral inference. Item 4 fails. The rest of the memo's factual claims are correctly cited, and the limitations section is thorough.

## Passing-team caveats (not rubric failures)

The team's remaining memos pass, but they are not flawless:

- **T01:** repeats the 20 MW, 15 MW and 5 MW figures and carries a long seven-caveat limitations block. Verbose, but within length and professional.
- **T02:** the limitations section is excessive and repetitive (14 caveats, with repeated "the memo lacks..." phrasing). It remains within length and does not overreach.
- **T03:** the most balanced memo; direct evidence, a short interpretation, and six relevant limitations.
- **T04:** several limitations are stated in both the body and the limitations section. Redundant, but accurate.
- **T05:** limitations-heavy (six caveats), but relevant and within length.

The baseline's passing memos also have caveats: T01's speculation about awareness and incentives is explicitly hedged ("potentially"), and T03's causal language is hedged ("likely", "may", "could"). Those were treated as interpretation rather than overreach, unlike the unhedged claims in baseline T02 and team T01.

## Summary

| System | Passed | Failed | Evaluated | Failure rate |
|---|---:|---:|---:|---:|
| baseline | 2 | 3 | 5 | **0.60** |
| multi-agent | 4 | 1 | 5 | **0.20** |

- **Baseline failures:** T02 (overreach and empty footnotes), T04 (empty footnotes), T05 (body below 250 words).
- **Multi-agent failure:** T01 (unlabeled "growing interest" inference).
- **Error-amplification ratio:** `0.20 / 0.60 = 0.333...`, reported as **0.33**. The baseline failure rate is non-zero, so the ratio is defined. Both rates and counts are reported because n = 5 is small.
- **Execution errors:** 0/10; all runs completed.
- **Automatic checks:** baseline 4/5, multi-agent 5/5. Manual review changes both counts because the automatic checks cannot assess grounding, overreach, footnote quality or the body-length rule.

## Limitations and threats to validity

1. **Small sample:** five topics per system; the failure rates and ratio are descriptive, not statistically precise.
2. **Different information access:** the baseline receives the whole corpus in one call, while the team retrieves excerpts through MCP. This compares the chosen architectures, not a controlled experiment isolating retrieval from other factors.
3. **T01 resume:** the team T01 run has `resumed: true` because it continued a pre-evaluation scratchpad. Its token total covers post-resume calls and is not a clean fresh-run cost comparison. The other team runs were fresh. T01 is still a genuine completed run with the same model and settings, but its cost should not be compared like-for-like.
4. **Single run:** each topic was run once at temperature 0; provider nondeterminism and run-to-run variation are not measured.
5. **Subjective review:** items 4–6 require judgment. The evidence and decision for each item are recorded in `results/evaluation_reviewed.json` so the review can be audited or repeated.
6. **Cost:** provider cost was not returned; only token counts are reported.

## Artifacts

- Raw evaluator output: `results/evaluation.json`
- Reviewed output with per-item decisions: `results/evaluation_reviewed.json`
- All ten memo texts: `results/memos.txt`
- This review: `results/manual_review.md`
- Team metrics and traces: `scratchpad/run-T01.json` … `scratchpad/run-T05.json`
