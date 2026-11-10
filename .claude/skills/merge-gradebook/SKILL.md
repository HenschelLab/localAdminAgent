---
name: merge-gradebook
description: "Use this skill to merge AI grading summary.csv files (from the write-grading-csv skill) into a Blackboard/Grade Center gradebook export CSV for a Khalifa University internship course, joined by Student ID. Never overwrites a grade that's already in the gradebook, and flags ambiguous or suspicious matches for manual review instead of guessing. Produces a new CSV; never modifies the original Blackboard export."
---

# Merging AI grades into the Blackboard gradebook

Step 3 of the 3-step grading pipeline:

1. [[grade-submissions]] — directory of submissions → `grading_results/*.json`
2. [[write-grading-csv]] — `grading_results/*.json` → `grading_results/summary.csv`
3. **merge-gradebook** (this skill) — `summary.csv` + Blackboard export → merged gradebook CSV

## Inputs

- The **Blackboard Grade Center export** CSV for the course, e.g.
  `gc_<term>-<course-id>_columns_<timestamp>.csv` — downloaded fresh by the instructor each time; this
  is the source of truth for the roster and for any grades already entered manually. Read it
  with `encoding="utf-8-sig"` (Blackboard exports carry a BOM).
- One or more **AI grading summary CSVs**, one per assignment (Final Report, Logbook), each
  produced by [[write-grading-csv]].
- The join key is always **Student ID** — the gradebook's `Student ID` column against each
  summary row's `student_id`.

## Core rule: never overwrite an existing grade

A gradebook score column is only auto-filled with the AI score when its current value is blank
or `"Needs Grading"`. Anything else already in that cell (a real grade, a manually entered
override) is left completely untouched — the AI result still gets recorded in a separate
advisory column, but flagged `Needs_Manual_Review=True` with the reason, never silently
overwritten. This is the single most important safeguard in this whole pipeline — the committee
explicitly asked for it after discussing the risk of an automated pass clobbering manual grading
work.

## Per-assignment column scheme

For each assignment, append these new advisory columns (never remove or reorder the gradebook's
own columns):

- `AI_<prefix>_Score_100` — the raw 0–100 AI score (e.g. `AI_FR_Score_100`, `AI_LB_Score_100`).
- `AI_<prefix>_<rubric_field>` — one column per rubric criterion, rendered as `✅`/`❌` via the
  same `bool_icon` convention as the summary CSV (not `True`/`False` text).
- `AI_<prefix>_Needs_Manual_Review` — `True`/`False`.
- `AI_<prefix>_Comments` — the AI's overall comments, prefixed with `[NEEDS MANUAL REVIEW: ...]`
  and the specific reason(s) when flagged.

The actual gradebook score column to fill is found by matching the Blackboard header prefix
(e.g. a column starting with `"Final Report ["` — Blackboard embeds `[Total Pts: N Score]` in
the header). Parse `N` from that header and scale the AI's 0–100 score to it:
`score_scaled = round(ai_score_100 * N / 100, 2)`. Don't hardcode a point value — different
courses/semesters can have different max points for the same assignment.

## Flag for manual review, don't guess, when:

- **The gradebook cell already holds a grade** (anything other than blank/`"Needs Grading"`) —
  record the existing value in the flag reason.
- **A student has more than one graded file** for the assignment (e.g. the same report
  uploaded as both `.docx` and `.pdf`, or several attachments on one attempt) — list every
  filename and its score in the flag reason; never pick one arbitrarily. When ambiguous, leave
  the per-rubric-field columns blank rather than presenting one file's breakdown as "the" answer.
- **`word_count == 0`** for the graded file — very likely a scanned/image-only PDF (no OCR
  available), not an empty submission; say so explicitly rather than implying the student
  submitted nothing.
- **A known-bad match for a specific student ID** — e.g. the extracted file turned out to be the
  wrong document type entirely (an Organization Supervisor Report or Academic Supervisor Report
  submitted into the Final Report slot). Keep a small `extra_manual_review` dict of
  `student_id -> note` for cases like this that the generic rules above wouldn't otherwise catch,
  found during review of earlier output — don't silently drop the note when re-running for a new
  semester unless the committee has resolved it.

## Output and reporting

- Write to a **new** file (e.g. `gc_<course>_with_AI_grades.csv`), using `encoding="utf-8-sig"`
  so Excel renders the ✅/❌ icons correctly — never modify the original Blackboard export in place.
- After writing, report per assignment: how many gradebook rows matched an AI result, how many
  were auto-filled vs. flagged, and any AI-graded student ID that had **no** matching gradebook
  row at all (a roster mismatch worth surfacing, not silently dropping).

## Running for a new course or semester

The script should take `--gradebook`, `--output`, and one `--<assignment>-summary` path per
assignment as CLI args rather than hardcoding a single course's paths — each new course
(e.g. the 399-numbered course alongside the original) just needs new paths passed in, not a
new copy of the merge logic.
