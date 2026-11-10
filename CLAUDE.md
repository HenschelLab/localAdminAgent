# localAdminAgent — repo instructions

This repo is a tutorial: confidentiality-preserving document grading with a local LLM
(Ollama + gemma4), packaged as three Claude Code Skills. It is a teaching artifact, not
a production grading system — everything under `Workshop_Exercise/` is synthetic data
(invented students, invented companies).

## What's here

- `grading_common.py`, `grade_submissions.py` — extraction + local-LLM scoring.
- `merge_gradebook.py` — the gradebook-merge step; intentionally generic (every path is
  a CLI flag), unlike a real deployment's own merge script.
- `.claude/skills/{grade-submissions,write-grading-csv,merge-gradebook}/SKILL.md` — the
  three Skills this tutorial is built around. Read these before changing the pipeline.
- `example_course_layout/FinalReports/CLAUDE.md` — an example of the layered-CLAUDE.md
  pattern: a rubric file living exactly where the skill expects to find one, next to
  the submissions it would grade.
- `Workshop_Exercise/` — the hands-on material. Treat its contents as fixtures, not as
  a real course directory.

## Working in this repo

- Never add real student data, real course identifiers, or real gradebook exports to
  this repo. If you're tempted to copy something in "for reference" from a real
  deployment, don't — write a generic/synthetic equivalent instead, the same way
  `merge_gradebook.py` was deliberately rewritten rather than copied.
- Keep `grading_results/` output out of version control (see `.gitignore`) — it's
  generated locally by running the exercises, not something to commit.
- `example_course_layout/FinalReports/CLAUDE.md` is a paraphrased, original rubric
  description written for this tutorial. Don't replace it with verbatim text from any
  institution's internal handbook.
- Preserve the human-in-the-loop judgment calls already built into the pipeline —
  never auto-overwrite an existing grade, flag (don't silently resolve) ambiguous
  multi-file matches, and flag a 0-word extraction instead of scoring it as empty. If
  you're adding a new edge case, default to flagging it for a human rather than having
  the code decide. See "Human judgment, kept in the loop" in `README.md`.
