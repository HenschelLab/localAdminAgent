# Hands-On Exercise 2 (bonus): Merge Grades Into a Gradebook

**Case study:** closing the loop — from local-LLM scores to a gradebook
**Time:** ~15 minutes
**Prerequisite:** finish Exercise 1 first (you need a real `summary.csv` to merge)
**You will:** take the scores you just produced and merge them into a synthetic
Blackboard-style gradebook export, and see the "never overwrite an existing grade"
safeguard fire on purpose.

Like Exercise 1, everything here is synthetic — a fake gradebook export with four
fictional students, no real data involved.

## Step 1 — Make sure you have a summary.csv

If you haven't run Exercise 1 yet (or deleted its output), generate one now using
the real report rubric rather than your own practice rubric:

```bash
python3 grade_submissions.py Workshop_Exercise/PracticeReports --assignment-type practice
```

This writes `Workshop_Exercise/PracticeReports/grading_results/summary.csv`.

## Step 2 — Look at the fake gradebook before merging

Open `Workshop_Exercise/PracticeReports/fake_blackboard_export.csv`. It's a
four-row stand-in for a Blackboard Grade Center export: one `Final Report [Total
Pts: 100 Score]` column, currently `"Needs Grading"` for all four students.

## Step 3 — Run the merge

```bash
python3 merge_gradebook.py
```

With no arguments it defaults to this repo's synthetic practice data (see the
`DEFAULT_*` constants at the top of `merge_gradebook.py` if you want to point it at
different files). It writes
`Workshop_Exercise/PracticeReports/fake_blackboard_export_with_AI_grades.csv` and
prints a short per-assignment summary to the terminal.

## Step 4 — Open the output and find three things

1. **An auto-filled score.** At least one student's `Final Report [Total Pts: 100
   Score]` cell should now hold a real number instead of `"Needs Grading"` — scaled
   from the AI's 0–100 score to this gradebook column's 100-point max.
2. **The advisory columns.** `AI_FR_Score_100`, one checkmark column per rubric
   criterion, `AI_FR_Needs_Manual_Review`, and `AI_FR_Comments` — all appended after
   the original columns, which are never reordered or removed.
3. **The safeguard, demonstrated.** Open `fake_blackboard_export.csv` again and
   manually change one student's score cell from `"Needs Grading"` to `"88"` (a real
   grade already entered), save it, and re-run Step 3. That student's row should now
   come back with `AI_FR_Needs_Manual_Review=True` and the original `"88"`
   untouched — the script refuses to overwrite a grade that's already there, even
   though it has its own AI score sitting right next to it in the advisory columns.

## Discussion prompts

- Why record the AI score in an advisory column *even when* it isn't auto-filled
  into the real grade cell? What would be lost if the script just skipped flagged
  rows entirely instead?
- `merge_gradebook.py` takes an optional `--extra-review-json` file for "known bad
  match" notes (e.g. a submission that turned out to be the wrong document type
  entirely) instead of hardcoding student IDs in the script. Why does that matter
  for a script that's meant to be reused across courses and semesters?
- This script only ever *writes a new file* — it never edits the gradebook export
  you pass in. What failure mode does that design choice prevent?

## Cleanup

```bash
rm -rf Workshop_Exercise/PracticeReports/grading_results
rm -f Workshop_Exercise/PracticeReports/fake_blackboard_export_with_AI_grades.csv
git checkout -- Workshop_Exercise/PracticeReports/fake_blackboard_export.csv
```
