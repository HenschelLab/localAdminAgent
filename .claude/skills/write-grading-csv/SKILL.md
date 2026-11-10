---
name: write-grading-csv
description: "Use this skill to roll up per-student grading JSON results (from grading_results/*.json, produced by the grade-submissions skill) into a single grading_results/summary.csv for a Khalifa University internship Final Report or Logbook course directory. Handles the Excel BOM/encoding issue and renders boolean rubric fields as colored checkmark icons. Does not call Ollama or extract text (that's grade-submissions) and does not touch the Blackboard gradebook (that's merge-gradebook)."
---

# Writing the grading summary CSV

Step 2 of the 3-step grading pipeline:

1. [[grade-submissions]] — directory of submissions → `grading_results/*.json`
2. **write-grading-csv** (this skill) — `grading_results/*.json` → `grading_results/summary.csv`
3. [[merge-gradebook]] — `summary.csv` + Blackboard export → merged gradebook CSV

This stage is pure rollup: read every JSON file already written by `grade-submissions`, flatten
each into one CSV row, write the file. No LLM calls, no file extraction, no gradebook logic here
— if a result looks wrong, fix it in the grading script and re-run step 1, don't patch it up at
CSV-writing time.

## What one row needs

Pull from each result's `_meta` block and top-level rubric fields:

- `student_id`, `filename`, `word_count` (from `_meta`)
- `overall_score_out_of_100`
- One column per rubric field in that assignment's `RUBRIC_FIELDS` list — extract the boolean
  (e.g. `result["cover_page"]["present"]`, `result["conclusion"]["clearly_indicates_internship_success"]`)
  rather than the whole nested dict; the per-field narrative comment stays in the `.txt` file,
  not the CSV.
- `overall_comments` — last column, free text.

Skip any `result` that is `None` (a file that failed extraction or an Ollama call) rather than
writing a blank/broken row — but mention the skip count when reporting back, since a silently
dropped row is exactly the kind of thing that should be flagged to the user, not just omitted.

## Format requirements (both confirmed by the user — don't deviate)

- **CSV, not Excel.** No `.xlsx` import step.
- **Student ID comes from the filename, already parsed upstream** (in `_meta.student_id`) — this
  skill never re-derives it; student name is filled in later by the gradebook merge, not here.
- **Boolean fields render as colored checkmark icons, not `True`/`False`** — `✅` for true, `❌`
  for false. (Plain `True`/`False` text was the original format; the user asked for icons
  specifically for readability when scanning the sheet.)
- **Write with `encoding="utf-8-sig"` (BOM), not plain `utf-8`.** Without the BOM, Excel
  guesses a legacy encoding for the file and renders the ✅/❌ icons (and any accented names) as
  garbled characters — this bit the first version of this pipeline and was fixed by switching to
  `utf-8-sig`. Don't regress this.

```python
def bool_icon(value) -> str:
    if value is True or value == "True":
        return "✅"
    if value is False or value == "False":
        return "❌"
    return value  # leave non-boolean values (e.g. missing) untouched

with out_path.open("w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(csv_rows)
```

## Output location

`grading_results/summary.csv`, inside the same course/assignment directory the JSON results
live in (e.g. `FinalReports/grading_results/summary.csv`, `Logbooks399/grading_results/summary.csv`)
— `merge-gradebook` expects to find it there by default.

## After writing

Spot-check row count against the number of JSON files in `grading_results/` — a mismatch means
some results were silently skipped (usually `None` results from a failed extraction/Ollama
call), which is worth surfacing to the user rather than letting the CSV quietly undercount.
