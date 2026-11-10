# Hands-On Exercise 1: Write a Rubric, Watch It Get Applied

**Case study:** Local LLM grading (Ollama)
**Time:** ~30–40 minutes
**You will:** write your own grading rubric as code, run it against four practice
submissions with a local LLM, and compare the model's verdict to your own.

Everything in this exercise lives under `Workshop_Exercise/` and is fully synthetic —
invented students, invented companies. It never reads or writes anything in
`FinalReports`, `Logbooks`, or their `399` counterparts, so there's no way to affect
real course data.

## Before you start

Confirm these three things from a terminal in the `Internship_2026` folder:

```bash
ollama list | grep gemma4          # the model the pipeline uses must be pulled
python3 -c "import docx, fitz"     # python-docx and PyMuPDF must be importable
ls Workshop_Exercise/PracticeReports
```

You should see `gemma4` in the model list, no import error, and four `.docx` files
in `PracticeReports`.

## Step 1 — Meet the four practice submissions

Open the four files in `Workshop_Exercise/PracticeReports/`. Read each one — they're
short on purpose. Each was built to isolate one specific grading challenge:

| File (student ID) | What it's testing |
| --- | --- |
| `..._100000001_..._NovaGrid.docx` | A reasonably complete report — but still only 300 words against a 1,500-word target. Is "well-structured" enough, or does length matter too? |
| `..._100000002_..._BrightPath.docx` | Thin, missing most required sections, and the conclusion never says whether the internship succeeded. |
| `..._100000003_..._Dune Analytics.docx` | Not a report at all — it reads like presentation speaker notes. Will your rubric catch a submission of the *wrong type*, not just a weak one? |
| `..._100000004_..._Scan.docx` | Effectively blank — a stand-in for what a scanned, unreadable PDF looks like after extraction (0 words). |

Before running anything, **grade all four yourself** on a scale of 0–100 and jot down
why. You'll compare this to the model's output later.

## Step 2 — Write your rubric

Open `grade_submissions.py` and find the `ASSIGNMENTS` dictionary near the bottom of
the file. You'll see three entries already: `"report"`, `"logbook"`, and `"practice"`
(the last one is the instructor's answer key — don't look at it yet, or it'll spoil
the comparison in Step 4).

Add your own entry. Keep the rubric simple — four or five criteria, not the full
Handbook list — so you can see clearly which of your criteria catch each problem
above. For example:

```python
MY_RUBRIC_FIELDS = [
    "has_clear_structure",       # cover/intro/body/conclusion, in that order
    "organization_described",    # says who the organization is and what it does
    "work_described",            # describes what the student actually did
    "conclusion_indicates_success",  # conclusion says whether the internship went well
]

MY_SYSTEM_PROMPT = """You are grading a short internship report. For each of the
following criteria, return true/false with one sentence of evidence:
- has_clear_structure
- organization_described
- work_described
- conclusion_indicates_success
Also return an overall_score_out_of_100 and one sentence of overall_comments.
Respond with ONLY a JSON object, no markdown fences."""

MY_CSV_COLUMNS = [(f, f, "present") for f in MY_RUBRIC_FIELDS]
```

Then add the config entry (pick a name that won't collide with anyone else's, e.g.
`"practice_<yourname>"` if several people are editing this file at once):

```python
    "practice_yourname": AssignmentConfig(
        label="practice_yourname",
        filename_prefix="Practice Report",
        extensions="pdf|docx",
        rubric_fields=MY_RUBRIC_FIELDS,
        system_prompt=MY_SYSTEM_PROMPT,
        csv_columns=MY_CSV_COLUMNS,
        skip_title_re=None,
    ),
```

This is exactly the pattern described in the `grade-submissions` skill's "Adding a
new assignment type" section — the same extension point used for every real course.

## Step 3 — Run it

```bash
python3 grade_submissions.py Workshop_Exercise/PracticeReports --assignment-type practice_yourname
```

This calls your local Ollama model once per file — no API key, no network call, no
student data (synthetic here, but real in production) leaving the machine. Four
short files should take well under a minute.

Read the four JSON files it writes to `Workshop_Exercise/PracticeReports/grading_results/`.
Each one contains a `true`/`false` verdict and a one-sentence justification per
criterion, plus an overall score and comment — look at `_meta.word_count` too.

## Step 4 — Compare

1. **Your rubric vs. your own manual grades (Step 1).** Where did the model agree
   with you? Where didn't it? Did `has_clear_structure` and `work_described` end up
   true for the BrightPath submission even though it's clearly weak — i.e. is your
   rubric too coarse to catch *how* weak?
2. **Your rubric vs. the instructor's `"practice"` entry.** Now look at
   `Workshop_Exercise/instructor_answer_key/grading_results_reference_run/` — the
   same four files graded against the *real*, full report rubric (11 criteria,
   including diagrams, page numbers, and a dedicated summary section). On the actual
   run used to build this exercise, the real rubric scored them 65 / 20 / 20 / 0 —
   and its comments explicitly called out the Dune Analytics file for being
   "presented as a set of presentation speaker notes rather than a formal academic
   report," and the Scan file for being "entirely blank." Did your simpler rubric
   catch those same two problems, or did it need the extra criteria to catch them?

## Discussion prompts

- The real rubric penalized the NovaGrid submission (65/100) for word count even
  though every section was structurally present. Is that fair for a workshop
  demo file that was never meant to hit 1,500 words? What does this tell you about
  testing a rubric against short synthetic examples versus real submissions?
- Our production `"report"` config also has a filename-based safeguard
  (`skip_title_re`) that filters out submissions whose *title* contains "slides"
  before the model ever sees them — we turned that off here on purpose, so the
  model's own judgment had to catch the Dune Analytics file through its content.
  Which safeguard would you rather rely on, and why might you want both?
- What's one criterion from the real Internship Handbook rubric you'd add to your
  own, now that you've seen what it catches that a 4-field rubric doesn't?

## Cleanup

Your run wrote a `grading_results/` folder inside `PracticeReports/`. Delete it
before the next group runs the exercise, or pass `--force` to re-grade from scratch:

```bash
rm -rf Workshop_Exercise/PracticeReports/grading_results
```
