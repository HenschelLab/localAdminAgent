#!/usr/bin/env python3
"""
grade_submissions.py

Semi-automatically grades/evaluates internship "Final Report" or "Log book"
submissions (PDF/DOCX/XLSX) in a given course directory by extracting their
content and having a local Ollama model (default: gemma4) score them
against the rubric described in that directory's CLAUDE.md.

This replaces the earlier per-course copies (FinalReports/grade_reports.py,
Logbooks/grade_logbooks.py, and their *399 duplicates) with one script
parameterized by --assignment-type and a directory argument, so a new
course or semester never needs its own copy of the grading logic -- only
new paths.

.txt files in the target directory are Blackboard/LMS metadata sidecars
and are NOT graded and NOT read. The student ID is taken from each
submission's own filename (the 9-digit number after the assignment
prefix); student name/grade are merged in later from the gradebook (see
merge_grades_into_gradebook.py), not here.

Shared extraction/Ollama/CSV plumbing lives in grading_common.py.

Usage:
    python3 grade_submissions.py FinalReports --assignment-type report
    python3 grade_submissions.py Logbooks --assignment-type logbook
    python3 grade_submissions.py FinalReports399 --assignment-type report --limit 3
    python3 grade_submissions.py Logbooks399 --assignment-type logbook --force
    python3 grade_submissions.py FinalReports --assignment-type report \\
        --file "Final Report_100053491_..._Report.docx"

Output (inside the given directory):
    grading_results/<same-basename>.json   -- structured rubric result
    grading_results/<same-basename>.txt    -- human-readable version
    grading_results/summary.csv            -- one row per submission
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grading_common as gc  # noqa: E402


# A CSV column spec is (csv_column_name, json_field_name, json_key). Most
# columns pull json_field_name's own "present" key; a couple (see report's
# "sections_and_subsections" and the synthetic "conclusion_clearly_..."
# column) pull a differently-named key, which is why this is a tuple and
# not just a field-name list.
CsvColumn = tuple[str, str, str]


@dataclass
class AssignmentConfig:
    label: str  # human-readable, for log messages ("report" / "logbook")
    filename_prefix: str  # Blackboard assignment prefix, e.g. "Final Report"
    extensions: str  # pipe-separated, no dots, e.g. "pdf|docx"
    rubric_fields: list[str]
    system_prompt: str
    csv_columns: list[CsvColumn]
    skip_title_re: Optional[re.Pattern] = None
    latest_attempt_only: bool = True


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
]

REPORT_SYSTEM_PROMPT = """You are an academic reviewer grading university internship final reports.
Evaluate the submitted report strictly against the rubric below and respond with ONLY a
single valid JSON object (no markdown fences, no commentary outside the JSON).

Rubric (from the course's final report requirements):
1. Length: the report should be approximately 1,500 words.
2. It must include a cover page with an appropriate title.
3. It must include an abstract page.
4. It must include a table of contents, appearing right after the abstract.
5. It must include an introduction, placed after the table of contents.
6. The first main section AFTER the introduction must be a brief about the
   host organization, and should describe the inter-relation of the
   organization's departments using diagrams where appropriate.
7. The report should include appropriate diagrams throughout (not just in
   the organization section).
8. The report should be divided into clear sections and subsections.
9. Pages should be numbered.
10. The report must include appendices.
11. The report must include a summary.
12. The report must end with a conclusion clear enough that a reader could
    tell, just from reading it, whether the internship was successful.

You will be given: the student's extracted report text (it may be
truncated if very long), and some objective facts already computed by a
script (word count, page count, embedded image count). Use the provided
text as the primary evidence for structural/content judgments, and use the
objective facts to sanity-check things like word count and diagram count
(a diagram/figure may appear as an embedded image OR as a captioned
"Figure X" reference in the text -- count either as evidence of a diagram).

Return exactly this JSON shape:

{
  "cover_page": {"present": bool, "comment": "string"},
  "abstract": {"present": bool, "comment": "string"},
  "table_of_contents": {"present": bool, "comment": "string"},
  "introduction": {"present": bool, "comment": "string"},
  "organization_brief_section": {"present": bool, "includes_department_interrelation_diagram": bool, "comment": "string"},
  "diagrams_appropriate_use": {"present": bool, "estimated_diagram_count": int, "comment": "string"},
  "sections_and_subsections": {"well_structured": bool, "comment": "string"},
  "page_numbers": {"present": bool, "comment": "string"},
  "appendices": {"present": bool, "comment": "string"},
  "summary_section": {"present": bool, "comment": "string"},
  "conclusion": {"present": bool, "clearly_indicates_internship_success": bool, "comment": "string"},
  "word_count_comment": "string, note whether the length is roughly on target for 1500 words",
  "overall_score_out_of_100": int,
  "overall_comments": "string, 3-6 sentences summarizing strengths/weaknesses and giving concrete improvement suggestions"
}

Be evidence-based and specific in each "comment" (cite what you saw or didn't see).
Be a fair but rigorous grader: only mark something "present": true if you actually
see evidence of it in the extracted text.
"""

REPORT_CSV_COLUMNS: list[CsvColumn] = [
    ("cover_page", "cover_page", "present"),
    ("abstract", "abstract", "present"),
    ("table_of_contents", "table_of_contents", "present"),
    ("introduction", "introduction", "present"),
    ("organization_brief_section", "organization_brief_section", "present"),
    ("diagrams_appropriate_use", "diagrams_appropriate_use", "present"),
    ("sections_and_subsections", "sections_and_subsections", "well_structured"),
    ("page_numbers", "page_numbers", "present"),
    ("appendices", "appendices", "present"),
    ("summary_section", "summary_section", "present"),
    ("conclusion", "conclusion", "present"),
    ("conclusion_clearly_indicates_success", "conclusion", "clearly_indicates_internship_success"),
]

LOGBOOK_RUBRIC_FIELDS = [
    "ongoing_record_of_activity",
    "new_learnings",
    "objectives_achieved",
    "feedback_received",
    "other_important_information",
    "circuit_or_technical_sketches",
    "formulas_used",
    "results_of_work",
    "organization_diagrammatic_info",
]

LOGBOOK_SYSTEM_PROMPT = """You are an academic reviewer grading university internship logbooks.
Evaluate the submitted logbook strictly against the rubric below and respond with ONLY a
single valid JSON object (no markdown fences, no commentary outside the JSON).

Rubric (from the course's logbook requirements):
Students are required to keep a record (logbook) of their work activities.
The logbook should read as an ongoing record built up over the internship
(e.g. dated or weekly entries), not a single one-off write-up produced at
the end. In their logbook, students should include:
1. Notes about new learnings.
2. Notes about objectives achieved.
3. Notes about feedback received (e.g. from a supervisor).
4. Any other information the student deemed important.
5. Sketches of circuit diagrams (where applicable to the student's field/work).
6. Formulas used in calculations (where applicable).
7. Sets of results about the work performed.
8. Other diagrammatic information about the organization (e.g. process
   flows, system diagrams, org charts encountered during the work).

Not every entry needs every item above (e.g. a non-electrical internship may have
no circuit diagrams) -- judge "circuit_or_technical_sketches" and "formulas_used"
generously as "any technical sketches/diagrams" and "any calculations/formulas"
respectively if the student's field doesn't involve literal circuits, and mark
them present if there is credible evidence of the *category* of content (technical
diagrams, or quantitative/formula-based work), not literally circuits specifically.
If a category is genuinely inapplicable to the internship's domain, still mark it
"present": false but say so plainly in the comment rather than guessing.

IMPORTANT -- be lenient specifically about "circuit_or_technical_sketches": these
are Computer Science students, and the large majority of internships are
software/IT-focused (web/app development, data, networking, IT support, business
systems, etc.) where literal circuit diagrams are simply not part of the work.
Only set "applicable_to_field": true for this item if the logbook gives clear
evidence the internship involves hardware, electronics, or embedded systems work.
Otherwise mark "applicable_to_field": false and do NOT let the absence of circuit
diagrams pull down "overall_score_out_of_100" at all -- it should have zero
weight in the score for a typical software/IT internship. "formulas_used" should
still be judged normally/generously (data analysis, algorithms, statistics, etc.
all count as formulas/calculations for a CS student).

You will be given: the student's extracted logbook text (it may be
truncated if very long; for spreadsheet logbooks it is a row-by-row dump
of all sheets), and some objective facts already computed by a script
(word count, page count, embedded image count). Use the provided text as
the primary evidence for content judgments, and use the objective facts to
sanity-check volume and diagram/sketch counts (a diagram/sketch may appear
as an embedded image OR as a described/labeled figure in the text -- count
either as evidence).

Return exactly this JSON shape:

{
  "ongoing_record_of_activity": {"present": bool, "comment": "string"},
  "new_learnings": {"present": bool, "comment": "string"},
  "objectives_achieved": {"present": bool, "comment": "string"},
  "feedback_received": {"present": bool, "comment": "string"},
  "other_important_information": {"present": bool, "comment": "string"},
  "circuit_or_technical_sketches": {"present": bool, "applicable_to_field": bool, "comment": "string"},
  "formulas_used": {"present": bool, "applicable_to_field": bool, "comment": "string"},
  "results_of_work": {"present": bool, "comment": "string"},
  "organization_diagrammatic_info": {"present": bool, "comment": "string"},
  "overall_score_out_of_100": int,
  "overall_comments": "string, 3-6 sentences summarizing strengths/weaknesses and giving concrete improvement suggestions"
}

Be evidence-based and specific in each "comment" (cite what you saw or didn't see).
Be a fair but rigorous grader: only mark something "present": true if you actually
see evidence of it in the extracted text.
"""

LOGBOOK_CSV_COLUMNS: list[CsvColumn] = [(f, f, "present") for f in LOGBOOK_RUBRIC_FIELDS]

ASSIGNMENTS: dict[str, AssignmentConfig] = {
    "report": AssignmentConfig(
        label="report",
        filename_prefix="Final Report",
        extensions="pdf|docx",
        rubric_fields=REPORT_RUBRIC_FIELDS,
        system_prompt=REPORT_SYSTEM_PROMPT,
        csv_columns=REPORT_CSV_COLUMNS,
        skip_title_re=re.compile(r"presentation|slides?\b", re.IGNORECASE),
    ),
    "logbook": AssignmentConfig(
        label="logbook",
        filename_prefix="Log book",
        extensions="pdf|docx|xlsx",
        rubric_fields=LOGBOOK_RUBRIC_FIELDS,
        system_prompt=LOGBOOK_SYSTEM_PROMPT,
        csv_columns=LOGBOOK_CSV_COLUMNS,
        skip_title_re=None,
    ),
    # Workshop-only: same rubric/prompt as "report", but reading the
    # "Practice Report_*" filenames used by Workshop_Exercise/PracticeReports/
    # so the exercise never touches real course directories. skip_title_re is
    # deliberately None here (unlike "report") so participants can see the
    # model's own content-based judgment catch a wrong-document-type
    # submission, instead of it being filtered out by filename before grading.
    "practice": AssignmentConfig(
        label="practice",
        filename_prefix="Practice Report",
        extensions="pdf|docx",
        rubric_fields=REPORT_RUBRIC_FIELDS,
        system_prompt=REPORT_SYSTEM_PROMPT,
        csv_columns=REPORT_CSV_COLUMNS,
        skip_title_re=None,
    ),
}


def build_user_prompt(sf: gc.SubmissionFile, doc: gc.ExtractedDoc) -> str:
    facts = {
        "student_id": sf.student_id,
        "filename": sf.path.name,
        "word_count": doc.word_count,
        "page_count": doc.page_count,
        "embedded_image_count": doc.image_count,
    }
    return gc.build_user_prompt(facts, doc.text)


def render_human_readable(cfg: AssignmentConfig, sf: gc.SubmissionFile, doc: gc.ExtractedDoc, result: dict) -> str:
    lines = []
    lines.append(f"{cfg.label.capitalize()}: {sf.path.name}")
    lines.append(f"Student ID: {sf.student_id}")
    lines.append(
        f"Word count: {doc.word_count}"
        + (f" | Page count: {doc.page_count}" if doc.page_count else "")
        + (f" | Embedded images: {doc.image_count}" if doc.image_count is not None else "")
    )
    lines.append("")
    lines.append(f"OVERALL SCORE: {result.get('overall_score_out_of_100', 'N/A')}/100")
    lines.append("")
    lines.append("Overall comments:")
    lines.append(f"  {result.get('overall_comments', '')}")
    lines.append("")
    if result.get("word_count_comment"):
        lines.append(f"Word count assessment: {result['word_count_comment']}")
        lines.append("")
    lines.append("Rubric breakdown:")
    for field_name in cfg.rubric_fields:
        item = result.get(field_name, {})
        if not isinstance(item, dict):
            continue
        flags = ", ".join(
            f"{k}={v}" for k, v in item.items() if k != "comment" and isinstance(v, (bool, int))
        )
        lines.append(f"  - {field_name}: [{flags}]")
        comment = item.get("comment", "")
        if comment:
            lines.append(f"      {comment}")
    return "\n".join(lines)


def process_submission(
    cfg: AssignmentConfig, sf: gc.SubmissionFile, output_dir: Path, model: str, url: str, force: bool
) -> Optional[dict]:
    # Use the full filename (not just .stem) as the cache key: two
    # submissions can share a stem but differ by extension (e.g. the same
    # document uploaded as both .docx and .pdf), and .stem alone would
    # collide them onto the same cached output.
    out_json = output_dir / f"{sf.path.name}.json"
    out_txt = output_dir / f"{sf.path.name}.txt"

    if out_json.exists() and not force:
        print(f"  [skip] already graded -> {out_json.name} (use --force to re-grade)")
        return json.loads(out_json.read_text())

    print(f"  extracting content from {sf.path.name} ...")
    try:
        doc = gc.extract(sf.path)
    except Exception as e:
        print(f"  [error] failed to extract content: {e}")
        return None

    print(
        f"  extracted {doc.word_count} words"
        + (f", {doc.page_count} pages" if doc.page_count else "")
        + f"; calling ollama model '{model}' ..."
    )
    user_prompt = build_user_prompt(sf, doc)
    try:
        raw = gc.call_ollama(cfg.system_prompt, user_prompt, model=model, url=url)
        result = gc.parse_model_json(raw)
    except Exception as e:
        print(f"  [error] ollama call/parsing failed: {e}")
        return None

    result["_meta"] = {
        "student_id": sf.student_id,
        "filename": sf.path.name,
        "word_count": doc.word_count,
        "page_count": doc.page_count,
        "embedded_image_count": doc.image_count,
        "model": model,
    }

    output_dir.mkdir(exist_ok=True)
    out_json.write_text(json.dumps(result, indent=2))
    out_txt.write_text(render_human_readable(cfg, sf, doc, result))
    print(f"  -> wrote {out_json.name} and {out_txt.name}")
    return result


def write_summary_csv(cfg: AssignmentConfig, rows: list[Optional[dict]], output_dir: Path) -> None:
    summary_path = output_dir / "summary.csv"
    fieldnames = (
        ["student_id", "filename", "overall_score_out_of_100", "word_count"]
        + [csv_name for csv_name, _, _ in cfg.csv_columns]
        + ["overall_comments"]
    )
    csv_rows = []
    skipped = 0
    for result in rows:
        if not result:
            skipped += 1
            continue
        meta = result.get("_meta", {})
        row = {
            "student_id": meta.get("student_id"),
            "filename": meta.get("filename"),
            "overall_score_out_of_100": result.get("overall_score_out_of_100"),
            "word_count": meta.get("word_count"),
            "overall_comments": result.get("overall_comments"),
        }
        for csv_name, json_field, json_key in cfg.csv_columns:
            row[csv_name] = result.get(json_field, {}).get(json_key)
        csv_rows.append(row)
    gc.write_csv(csv_rows, fieldnames, summary_path)
    print(f"\nSummary written to {summary_path} ({len(csv_rows)} row(s))")
    if skipped:
        print(f"  NOTE: {skipped} submission(s) had no result (failed extraction/Ollama call) and were omitted")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grade internship Final Report or Logbook submissions in a directory using Ollama."
    )
    parser.add_argument("directory", type=Path, help="Course directory containing the submissions (e.g. FinalReports, Logbooks399)")
    parser.add_argument(
        "--assignment-type", choices=sorted(ASSIGNMENTS), required=True, help="Which rubric/prompt to grade against"
    )
    parser.add_argument("--model", default=gc.DEFAULT_MODEL, help=f"Ollama model to use (default: {gc.DEFAULT_MODEL})")
    parser.add_argument("--url", default=gc.OLLAMA_URL, help="Ollama chat endpoint URL")
    parser.add_argument("--force", action="store_true", help="Re-grade even if cached output exists")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N submissions (for testing)")
    parser.add_argument("--file", default=None, help="Only process this one submission filename")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = ASSIGNMENTS[args.assignment_type]
    directory = args.directory.resolve()
    output_dir = directory / "grading_results"

    filename_re = gc.make_filename_re(cfg.filename_prefix, cfg.extensions)
    submissions = gc.find_submission_files(
        directory,
        filename_re,
        only_file=args.file,
        latest_attempt_only=cfg.latest_attempt_only,
        skip_title_re=cfg.skip_title_re if args.file is None else None,
    )
    if not submissions:
        print(f"No '{cfg.filename_prefix}_*.{{{cfg.extensions}}}' files found in {directory}.")
        return 1

    if args.limit:
        submissions = submissions[: args.limit]

    print(f"Found {len(submissions)} {cfg.label}(s) to process in {directory}.\n")

    output_dir.mkdir(exist_ok=True)
    results = []
    for i, sf in enumerate(submissions, 1):
        print(f"[{i}/{len(submissions)}] {sf.student_id} — {sf.path.name}")
        result = process_submission(cfg, sf, output_dir, args.model, args.url, args.force)
        results.append(result)
        print()

    write_summary_csv(cfg, results, output_dir)
    ok = sum(1 for r in results if r)
    print(f"\nDone. {ok}/{len(submissions)} {cfg.label}s graded successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
