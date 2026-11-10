---
name: grade-submissions
description: "Use this skill when grading internship Final Reports or Logbooks for a Khalifa University SDP & Internship Committee course directory (e.g. FinalReports, FinalReports399, Logbooks, Logbooks399). Extracts text from each student's submission (PDF/DOCX/XLSX), scores it against the rubric in that directory's CLAUDE.md using a local Ollama model, and writes one JSON result per student into grading_results/. Does NOT write the summary CSV (use write-grading-csv for that) and does NOT touch the Blackboard gradebook (use merge-gradebook for that)."
---

# Grading internship submissions (extraction + scoring)

This skill produces per-student structured grading results for one assignment type in one
course directory. It is step 1 of a 3-step pipeline that repeats every semester:

1. **grade-submissions** (this skill) — directory of submissions → `grading_results/*.json`
2. [[write-grading-csv]] — `grading_results/*.json` → `grading_results/summary.csv`
3. [[merge-gradebook]] — `summary.csv` + Blackboard export → merged gradebook CSV

Keep this skill scoped to extraction + scoring only. If you're tempted to also write the
summary CSV or touch the gradebook here, stop — those are separate skills on purpose, so each
stage can be rerun, fixed, or swapped independently (e.g. changing the CSV icon format should
never require re-running Ollama on 150 files).

## Inputs you need before starting

- **The course directory** to grade, e.g. `FinalReports`, `Logbooks`, `FinalReports399`,
  `Logbooks399`. Each such directory has its own `CLAUDE.md` with the verbatim rubric from the
  Internship Handbook — always read it fresh, never rely on a remembered rubric, since wording
  (and leniency notes added after instructor feedback) can change between courses or semesters.
- **The assignment's filename prefix**, matching the Blackboard LMS export convention:
  `<Assignment Prefix>_<9-digit student id>_attempt_<timestamp>_<original title>.<ext>`
  — "Final Report" for reports, "Log book" for logbooks. Confirm the exact prefix by listing a
  few files in the directory; don't guess.
- **Which file extensions are valid** for this assignment (reports: pdf/docx; logbooks:
  pdf/docx/xlsx — logbooks are sometimes kept as spreadsheets).

## Shared plumbing: `grading_common.py`

A shared module at the course root (`Internship_2026/grading_common.py`) already provides:

- `make_filename_re(prefix, exts)` — builds the LMS filename regex for a given prefix/extensions.
- `find_submission_files(directory, filename_re, only_file=None, latest_attempt_only=True, skip_title_re=None)`
  — discovers submissions, keeps only the latest attempt per student ID by default, and can skip
  files whose original title looks like a different document (e.g. presentation slides
  accidentally attached to the report assignment).
- `extract(path)` — dispatches to `extract_pdf` / `extract_docx` / `extract_xlsx`, returning text,
  word count, page count (PDF only), and embedded-image count.
- `call_ollama(system_prompt, user_prompt, model="gemma4", ...)` — calls the local Ollama
  `/api/chat` endpoint with `format: "json"`.
- `parse_model_json(raw)` — strips markdown code fences and parses the model's JSON response.
  Always use this rather than `json.loads` directly — gemma4 sometimes wraps its JSON in ```` ```json ```` fences despite being asked not to.
  Model configuration note: `OLLAMA_URL`, `DEFAULT_MODEL = "gemma4"`, `MAX_CHARS = 45_000` (cap
  on extracted text sent to the model — truncation is flagged to the model via
  `text_truncated_for_review` so it can account for it).
- `build_user_prompt(facts, text)` — formats the objective facts (word count, page count, image
  count computed by the script — these are ground truth, tell the model to trust them over its
  own counting) plus the extracted text into one user message.

**Reuse this module. Do not re-implement extraction, filename parsing, or the Ollama call.**
If the course directory doesn't yet have a `grading_common.py` symlink/import path, add
`sys.path.insert(0, str(course_root))` before `import grading_common as gc`.

## Running it: `grade_submissions.py`

One parameterized script lives at the course root, `Internship_2026/grade_submissions.py`.
It replaces the earlier per-course copies (`FinalReports/grade_reports.py`,
`Logbooks/grade_logbooks.py`, and their `*399` duplicates) — **don't write a new per-course
script; add a new `--assignment-type` to this one instead.**

```
python3 grade_submissions.py FinalReports --assignment-type report --limit 3   # sanity-check a few first
python3 grade_submissions.py FinalReports --assignment-type report             # full run
python3 grade_submissions.py Logbooks --assignment-type logbook
python3 grade_submissions.py FinalReports399 --assignment-type report          # same script, new course
python3 grade_submissions.py Logbooks399 --assignment-type logbook --force     # re-grade ignoring cache
```

Review a handful of the `.txt` outputs by hand before treating a full run as trustworthy,
especially after any rubric or prompt change — a wording tweak can shift scores in ways worth
catching before 150 files are graded on it.

**⚠️ `--file` / `--limit` write a partial `summary.csv`, not just a partial grading pass.**
The script's final step always rewrites `grading_results/summary.csv` from whatever set of
results it just processed. Running `--file "<one file>"` on a directory that already has a full
`summary.csv` from a previous run **overwrites that full CSV with a 1-row file** — this actually
happened once while testing this script against real data (caught immediately because the row
count dropped from 39 to 2, and recovered by rerunning with no `--file`/`--limit` so it rebuilt
the full CSV from the still-intact cached JSON files). To test against one file or a few without
touching a directory's real summary, either rerun once more afterward with no `--file`/`--limit`
(cheap — every already-graded file is cache-skipped, no Ollama calls), or point `--file`/`--limit`
testing at a scratch copy of the directory instead of the real one.

### Adding a new assignment type

Each assignment type is one `AssignmentConfig` entry in the `ASSIGNMENTS` dict inside
`grade_submissions.py`: filename prefix + extensions, `rubric_fields`, `system_prompt`,
`csv_columns` (which JSON field/key each CSV column pulls from — most are `(name, name,
"present")`, but a field can map to a differently-named JSON key, e.g. report's
`sections_and_subsections` → `well_structured`), and an optional `skip_title_re` for filtering
out wrong-assignment attachments. Follow the existing `report`/`logbook` entries as the template:

1. `rubric_fields` — the list of top-level JSON keys the model must return, one per rubric
   criterion, taken directly from the directory's `CLAUDE.md`.
2. `system_prompt` — restate the rubric verbatim (don't paraphrase/compress it; the handbook
   wording is the actual grading standard the committee agreed to), tell the model to respond
   with only a JSON object in an exact shape, and tell it to be evidence-based (`"present": true`
   only when it actually saw evidence, cite what it saw).
3. **Apply any standing rubric adjustments** agreed by the committee — e.g. logbook circuit/
   technical-sketch criteria are graded leniently for CS-track students, since circuits aren't
   central to most internships; say so explicitly in the prompt.
4. Caching, error handling, scanned-PDF detection, and the two output files (`.json` + `.txt`)
   per student are all handled generically by `process_submission()` — no per-assignment code
   needed for these.

## Safeguards (do not skip these)

- **Keep latest attempt only** by default — a resubmission supersedes earlier attempts for
  that student ID.
- **Skip files whose original title doesn't match the assignment** (e.g. slides attached to a
  report submission) via `skip_title_re`, unless grading one file explicitly by name with `--file`.
- **Never silently merge multiple files for one student into a single verdict** — that
  disambiguation happens downstream in [[merge-gradebook]]; this skill just grades every
  matching file independently and lets the student_id collision surface later.
- **Flag, don't fabricate** — a 0-word extraction, a wrong-document-type submission, or an
  Ollama/parsing failure should produce an explicit signal (absent/null JSON, or a note),
  never a guessed score.
