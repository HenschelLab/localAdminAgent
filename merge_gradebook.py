#!/usr/bin/env python3
"""
merge_gradebook.py

Step 3 of the 3-stage grading pipeline (see .claude/skills/merge-gradebook/SKILL.md):
merges AI-assisted grading results (a summary.csv produced by grade_submissions.py)
into a Blackboard/Grade-Center-style gradebook export CSV, keyed on Student ID.

For the Final Report assignment:
  - its gradebook score column (header starting "Final Report [Total Pts: N Score]")
    is filled in with the AI score converted to that column's point scale
    (overall_score_out_of_100 * N / 100), but ONLY if the cell is currently blank or
    "Needs Grading".
  - New advisory columns are appended: AI_FR_Score_100, one True/False column per
    rubric criterion (rendered as checkmarks), AI_FR_Needs_Manual_Review, and
    AI_FR_Comments.

Safety rule: a column is NEVER auto-filled if it already holds a real grade (anything
other than blank/"Needs Grading") -- this catches a student who was already manually
graded, or a matched file that isn't actually a legitimate submission for that
assignment. Those rows are left untouched but flagged AI_FR_Needs_Manual_Review=True
with the reason, so a human can follow up.

This script is intentionally generic: every path is a CLI argument (with defaults
that point at this repo's own synthetic Workshop_Exercise data for a zero-argument
demo run), and known-bad-match notes come from an optional --extra-review-json file
rather than being hardcoded -- a real instructor supplies their own per semester
instead of editing this script.

Output is a NEW file; the input gradebook export is never modified.

Usage (demo, no arguments -- runs against this repo's synthetic practice data):
    python3 merge_gradebook.py

Usage (real course):
    python3 merge_gradebook.py \\
        --gradebook "path/to/gc_<course>_columns_<timestamp>.csv" \\
        --report-summary "path/to/FinalReports/grading_results/summary.csv" \\
        --output "path/to/gc_<course>_with_AI_grades.csv" \\
        --extra-review-json "path/to/extra_review_notes.json"
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PRACTICE_DIR = REPO_ROOT / "Workshop_Exercise" / "PracticeReports"

DEFAULT_GRADEBOOK_CSV = PRACTICE_DIR / "fake_blackboard_export.csv"
DEFAULT_REPORT_SUMMARY = PRACTICE_DIR / "grading_results" / "summary.csv"
DEFAULT_OUTPUT_CSV = PRACTICE_DIR / "fake_blackboard_export_with_AI_grades.csv"

REPORT_RUBRIC_FIELDS = [
    "cover_page",
    "abstract",
    "table_of_contents",
    "introduction",
    "organization_brief_section",
    "diagrams_appropriate_use",
    "sections_and_subsections",
    "page_numbers",
    "appendices",
    "summary_section",
    "conclusion",
    "conclusion_clearly_indicates_success",
]

COL_PREFIX = "Final Report"
AI_PREFIX = "FR"


def load_extra_manual_review(path: Path | None) -> dict[str, str]:
    """Optional, instructor-supplied 'known bad match' notes keyed by student ID
    (e.g. a file that turned out to be the wrong document type entirely). Empty by
    default -- a real instructor passes --extra-review-json for their own semester
    rather than this script hardcoding anyone's data."""
    if path is None:
        return {}
    return json.loads(path.read_text())


def find_gradebook_col(header: list[str], col_prefix: str) -> int:
    for i, col in enumerate(header):
        if col.startswith(col_prefix + " ["):
            return i
    raise SystemExit(f"Expected column starting with {col_prefix!r} not found in gradebook")


def parse_max_pts(col_header: str) -> float:
    m = re.search(r"Total Pts:\s*([\d.]+)", col_header)
    if not m:
        raise SystemExit(f"Could not parse max points from column header: {col_header!r}")
    return float(m.group(1))


def bool_icon(value: str) -> str:
    """Render a rubric True/False as a checkmark/X for readability in Excel."""
    if value in ("True", True):
        return "✅"
    if value in ("False", False):
        return "❌"
    return value


def load_ai_results(summary_csv: Path) -> dict[str, list[dict]]:
    """Group summary.csv rows by student_id (a list, not a single row).

    A student can legitimately have more than one row here: multiple files attached
    to a single submission attempt (e.g. the same document uploaded as both .docx
    and .pdf) each get graded independently upstream. Returning a list -- rather
    than silently keeping only the last row seen -- lets the caller flag these as
    ambiguous instead of picking one arbitrarily.
    """
    with summary_csv.open(newline="") as f:
        rows = list(csv.DictReader(f))
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["student_id"], []).append(row)
    return grouped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge AI grading results into a gradebook export CSV (Final Report only)."
    )
    parser.add_argument("--gradebook", type=Path, default=DEFAULT_GRADEBOOK_CSV, help="Gradebook CSV to merge into")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_CSV, help="Path to write the merged CSV to")
    parser.add_argument(
        "--report-summary", type=Path, default=DEFAULT_REPORT_SUMMARY, help="grade_submissions.py summary.csv"
    )
    parser.add_argument(
        "--extra-review-json",
        type=Path,
        default=None,
        help="Optional JSON file of {student_id: note} for known-bad matches (see SKILL.md)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    extra_manual_review = load_extra_manual_review(args.extra_review_json)
    results = load_ai_results(args.report_summary)
    print(f"Loaded {len(results)} AI-graded {COL_PREFIX} submissions from {args.report_summary}")

    with args.gradebook.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [list(row) for row in reader]

    id_col = header.index("Student ID")
    col_index = find_gradebook_col(header, COL_PREFIX)
    max_pts = parse_max_pts(header[col_index])

    ai_cols = (
        [f"AI_{AI_PREFIX}_Score_100"]
        + [f"AI_{AI_PREFIX}_{f}" for f in REPORT_RUBRIC_FIELDS]
        + [f"AI_{AI_PREFIX}_Needs_Manual_Review", f"AI_{AI_PREFIX}_Comments"]
    )
    new_header = list(header) + ai_cols

    out_rows = []
    matched = autofilled = flagged = 0
    unmatched_ids = set(results.keys())

    for row in rows:
        row = list(row)
        student_id = row[id_col].strip()

        result_list = results.get(student_id)
        if result_list is None:
            row += [""] * len(ai_cols)
            out_rows.append(row)
            continue

        matched += 1
        unmatched_ids.discard(student_id)

        # A student can have more than one row here if multiple files were
        # attached to a single submission attempt -- each gets graded
        # independently upstream. Never silently pick one; flag for manual review.
        ambiguous = len(result_list) > 1
        result = result_list[0]
        score_100 = float(result["overall_score_out_of_100"])
        current_value = row[col_index].strip()
        extra_reason = extra_manual_review.get(student_id)
        has_existing_grade = current_value not in ("", "Needs Grading")

        manual_review_reasons = []
        if has_existing_grade:
            manual_review_reasons.append(f"gradebook already has a grade ({current_value!r}); not overwritten")
        word_count = int(result.get("word_count") or 0)
        if word_count == 0:
            manual_review_reasons.append(
                "0 words extracted from the file -- this is very likely a scanned/image-only PDF "
                "our text extraction can't read (no OCR), NOT necessarily an empty submission; "
                "open the file directly before trusting this score"
            )
        if ambiguous:
            detail = "; ".join(f"{r['filename']!r} scored {r['overall_score_out_of_100']}" for r in result_list)
            manual_review_reasons.append(
                f"AMBIGUOUS: {len(result_list)} separate files were graded for this student "
                f"(likely multiple attachments on one submission attempt) -- pick the right one "
                f"manually: {detail}"
            )
        if extra_reason:
            manual_review_reasons.append(extra_reason)

        if manual_review_reasons:
            flagged += 1
        else:
            score_scaled = round(score_100 * max_pts / 100, 2)
            row[col_index] = f"{score_scaled:.2f}"
            autofilled += 1

        if ambiguous:
            # Don't present one file's rubric breakdown as "the" answer when
            # several files were graded independently.
            ai_row_values = [""] + [""] * len(REPORT_RUBRIC_FIELDS)
            comment = ""
        else:
            ai_row_values = [f"{score_100:.0f}"]
            for f in REPORT_RUBRIC_FIELDS:
                ai_row_values.append(bool_icon(result.get(f, "")))
            comment = result.get("overall_comments", "")
        ai_row_values.append("True" if manual_review_reasons else "False")
        if manual_review_reasons:
            comment = f"[NEEDS MANUAL REVIEW: {'; '.join(manual_review_reasons)}] {comment}"
        ai_row_values.append(comment)

        row += ai_row_values
        out_rows.append(row)

    # utf-8-sig adds a BOM so Excel (which otherwise guesses a legacy encoding for
    # BOM-less CSVs) renders the checkmark icons correctly instead of mangling them.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(new_header)
        writer.writerows(out_rows)

    print(f"\n{COL_PREFIX}: matched {matched}/{len(rows)} gradebook rows to an AI result")
    print(f"  -> auto-filled for {autofilled} students")
    print(f"  -> flagged for manual review (not auto-filled) for {flagged} students")
    if unmatched_ids:
        print(f"  WARNING: {len(unmatched_ids)} AI-graded ID(s) had no matching gradebook row: {sorted(unmatched_ids)}")

    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
