#!/usr/bin/env python3
"""
grading_common.py

Shared plumbing for the internship-assignment grading scripts
(FinalReports/grade_reports.py, Logbooks/grade_logbooks.py, and any future
sibling like Organization supervisor's report grading):

  - discovering submission files by the LMS export naming convention
    "<Assignment Prefix>_<student id>_attempt_<timestamp>_<original title>.ext",
    with optional "keep latest attempt only" and "skip files whose title
    looks unrelated" filtering
  - extracting text (+ rough image/diagram count) from .pdf / .docx / .xlsx
  - calling a local Ollama model and parsing its JSON response
  - writing a summary CSV

Each grading script owns its own rubric (SYSTEM_PROMPT, rubric field list,
human-readable rendering) and just reuses these helpers.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import docx  # python-docx
except ImportError:
    docx = None

try:
    import openpyxl
except ImportError:
    openpyxl = None


OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "gemma4"
MAX_CHARS = 45_000  # cap on extracted text sent to the model
REQUEST_TIMEOUT = 600  # seconds; local generation on a big file can be slow


@dataclass
class SubmissionFile:
    path: Path
    student_id: str
    timestamp: str
    original_title: str


@dataclass
class ExtractedDoc:
    text: str
    word_count: int
    page_count: Optional[int]
    image_count: Optional[int]


def make_filename_re(prefix: str, exts: str) -> re.Pattern:
    """Build the LMS-export filename regex for a given assignment prefix.

    prefix: e.g. "Final Report" or "Log book" (matched literally, case-insensitively)
    exts:   pipe-separated extensions without dots, e.g. "pdf|docx|xlsx"
    """
    return re.compile(
        rf"^{re.escape(prefix)}_(?P<id>\d+)_attempt_(?P<timestamp>[\d-]+)_(?P<title>.+)\.(?P<ext>{exts})$",
        re.IGNORECASE,
    )


def find_submission_files(
    directory: Path,
    filename_re: re.Pattern,
    only_file: Optional[str] = None,
    latest_attempt_only: bool = True,
    skip_title_re: Optional[re.Pattern] = None,
) -> list[SubmissionFile]:
    files = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if only_file and path.name != only_file:
            continue
        m = filename_re.match(path.name)
        if not m:
            continue
        sf = SubmissionFile(
            path=path,
            student_id=m.group("id"),
            timestamp=m.group("timestamp"),
            original_title=m.group("title"),
        )
        if skip_title_re is not None and skip_title_re.search(sf.original_title):
            print(f"  [skip] title doesn't look like this assignment: {path.name}")
            continue
        files.append(sf)

    if latest_attempt_only:
        latest_timestamp_by_id: dict[str, str] = {}
        for sf in files:
            if sf.timestamp > latest_timestamp_by_id.get(sf.student_id, ""):
                latest_timestamp_by_id[sf.student_id] = sf.timestamp
        kept = []
        for sf in files:
            if sf.timestamp == latest_timestamp_by_id[sf.student_id]:
                kept.append(sf)
            else:
                print(f"  [skip] superseded by a later attempt: {sf.path.name}")
        files = kept

    return files


def extract_pdf(path: Path) -> ExtractedDoc:
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed; run: pip install pymupdf")
    doc = fitz.open(path)
    parts = []
    image_count = 0
    for page in doc:
        parts.append(page.get_text())
        image_count += len(page.get_images(full=True))
    text = "\n".join(parts)
    page_count = doc.page_count
    doc.close()
    word_count = len(text.split())
    return ExtractedDoc(text=text, word_count=word_count, page_count=page_count, image_count=image_count)


def extract_docx(path: Path) -> ExtractedDoc:
    if docx is None:
        raise RuntimeError("python-docx is not installed; run: pip install python-docx")
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]

    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)

    for section in document.sections:
        for p in section.footer.paragraphs:
            if p.text.strip():
                parts.append(f"[footer] {p.text}")
        for p in section.header.paragraphs:
            if p.text.strip():
                parts.append(f"[header] {p.text}")

    text = "\n".join(parts)
    word_count = len(text.split())
    image_count = len(document.inline_shapes)
    return ExtractedDoc(text=text, word_count=word_count, page_count=None, image_count=image_count)


def extract_xlsx(path: Path) -> ExtractedDoc:
    if openpyxl is None:
        raise RuntimeError("openpyxl is not installed; run: pip install openpyxl")
    wb = openpyxl.load_workbook(str(path), data_only=True)
    parts = []
    image_count = 0
    for ws in wb.worksheets:
        parts.append(f"[sheet: {ws.title}]")
        for row in ws.iter_rows():
            values = [str(c.value) for c in row if c.value is not None]
            if values:
                parts.append("\t".join(values))
        image_count += len(getattr(ws, "_images", []) or [])
    text = "\n".join(parts)
    word_count = len(text.split())
    return ExtractedDoc(text=text, word_count=word_count, page_count=None, image_count=image_count)


def extract(path: Path) -> ExtractedDoc:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path)
    if ext == ".docx":
        return extract_docx(path)
    if ext == ".xlsx":
        return extract_xlsx(path)
    raise ValueError(f"Unsupported extension: {ext}")


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    url: str = OLLAMA_URL,
    num_ctx: int = 32768,
    temperature: float = 0.2,
    timeout: int = REQUEST_TIMEOUT,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "temperature": temperature,
        },
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"]


def parse_model_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            return json.loads(raw[start : end + 1])
        raise


def build_user_prompt(facts: dict, text: str, max_chars: int = MAX_CHARS) -> str:
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    facts = dict(facts)
    facts["text_truncated_for_review"] = truncated
    return (
        "OBJECTIVE FACTS (computed by script, trust these over your own counting):\n"
        + json.dumps(facts, indent=2)
        + "\n\nEXTRACTED SUBMISSION TEXT"
        + (" (truncated)" if truncated else "")
        + ":\n-----\n"
        + text
        + "\n-----\nEnd of extracted text. Respond with only the JSON object."
    )


def write_csv(rows: list[dict], fieldnames: list[str], out_path: Path) -> None:
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
