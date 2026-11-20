"""Turn labels.csv into an Excel sheet for human review, and the reviewed sheet back.

WHY. labels.csv is the ground truth the state-extraction methods are scored against
(scripts/score_state_methods.py). It was labelled by Claude and needs a human check,
but it is awkward to read: 153 of the 200 posts contain line breaks, so one post
spills over many lines, and 5 are longer than the 32,767 characters an Excel cell
can hold, which Excel would cut without warning.

    export   labels.csv -> labels_review.xlsx   one row per post, text on one line,
                                                Claude's labels beside editable
                                                final_* columns with drop-downs
    import   labels_review.xlsx -> labels.csv   checks every code, keeps a backup,
                                                and writes the scoring format back

The review copy only changes how the text LOOKS (line breaks become " / "). The
original text stays in sample.csv, and import never reads text from the sheet.

    uv run python -m scripts.labels_to_excel export
    # edit final_states / final_main / final_event, set reviewed = Y, save
    uv run python -m scripts.labels_to_excel import
    uv run python -m scripts.score_state_methods
"""
from __future__ import annotations

import argparse
import re
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from h5n1.sources.meta_location import STATE_NAME

FOLDER = Path("data/llm_pilot")
CODES = sorted(set(STATE_NAME.values()))
EXCEL_MAX = 32_767
SHOWN = 32_000            # leave room for the "[cut ...]" marker

# column name, width, editable?
COLUMNS = [
    ("row", 6, False), ("lang", 6, False), ("post_text", 90, False),
    ("claude_states", 14, False), ("claude_main", 10, False), ("claude_event", 10, False),
    ("confidence", 10, False), ("claude_note", 45, False),
    ("final_states", 14, True), ("final_main", 10, True), ("final_event", 10, True),
    ("reviewed", 9, True), ("comment", 35, True),
]


def one_line(text: str) -> str:
    """Line breaks -> ' / ', runs of spaces -> one, and cut to fit an Excel cell."""
    t = re.sub(r"[ \t]+", " ", re.sub(r"\s*[\r\n]+\s*", " / ", text or "")).strip()
    if len(t) > SHOWN:
        t = t[:SHOWN] + f" [cut here: full post is {len(text):,} characters, see sample.csv]"
    return t


def export(folder: Path) -> None:
    lab = pd.read_csv(folder / "labels.csv", dtype=str, keep_default_na=False)
    wb = Workbook()
    ws = wb.active
    ws.title = "labels"
    ws.append([c for c, _, _ in COLUMNS])
    for r in lab.itertuples():
        ws.append([int(r.row), r.lang, one_line(r.post_text),
                   r.human_states, r.human_main, r.human_event, r.confidence, r.note,
                   r.human_states, r.human_main, r.human_event, "", ""])

    edit_fill = PatternFill("solid", fgColor="EAF2FB")      # light blue: you edit these
    for i, (name, width, editable) in enumerate(COLUMNS, start=1):
        col = ws.cell(row=1, column=i).column_letter
        ws.column_dimensions[col].width = width
        ws.cell(row=1, column=i).font = Font(bold=True)
        ws.cell(row=1, column=i).alignment = Alignment(wrap_text=True, vertical="bottom")
        for cell in ws[col][1:]:
            cell.alignment = Alignment(wrap_text=name in ("post_text", "claude_note", "comment"),
                                       vertical="top")
            if editable:
                cell.fill = edit_fill
    for i in range(2, ws.max_row + 1):
        ws.row_dimensions[i].height = 90                    # drag taller to read more
    ws.freeze_panes = "D2"                                  # header and post text stay put

    n = ws.max_row
    event = DataValidation(type="list", formula1='"1,0"', allow_blank=False)
    reviewed = DataValidation(type="list", formula1='"Y"', allow_blank=True)
    ws.add_data_validation(event)
    ws.add_data_validation(reviewed)
    event.add(f"K2:K{n}")
    reviewed.add(f"L2:L{n}")
    # low-confidence rows in yellow, so they can be checked first
    ws.conditional_formatting.add(f"A2:H{n}", FormulaRule(
        formula=['$G2="low"'], fill=PatternFill("solid", fgColor="FFF4CC")))
    ws.auto_filter.ref = f"A1:M{n}"
    ws.page_setup.orientation = "landscape"                 # printable: all columns on one page width
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0

    guide = wb.create_sheet("how to label")
    for line in [
        "Edit only the blue columns. Claude's labels are kept beside them for comparison.",
        "final_states: every US state with a bird-flu event, as codes separated by commas (KS,TX). Blank if none.",
        "final_main: the one state the post is mainly about (must be one of final_states). Blank if none.",
        "final_event: 1 if the post reports a specific bird-flu event inside the US, 0 if not.",
        "reviewed: set to Y once you have checked the row, whether or not you changed it.",
        "Yellow rows were marked low confidence: borderline calls to check first.",
        "",
        "Rules (the same ones the LLM prompt uses): an event is a detection, case, outbreak, cull,",
        "quarantine, or a state acting on an event in its own territory. Not counted: a state that is only",
        "part of a name (Lake Michigan, California condor, Oregon Zoo is Oregon), 'Washington' meaning the",
        "federal government, a state named for contrast or as history, a dateline, a link address, a",
        "company headquarters or a price comparison.",
        "",
        "State codes: " + ", ".join(CODES),
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 110

    path = folder / "labels_review.xlsx"
    wb.save(path)
    print(f"wrote {path}: {len(lab)} posts, {sum(lab.confidence == 'low')} marked low confidence, "
          f"{sum(lab.post_text.str.len() > EXCEL_MAX)} long posts shown cut")


def _codes(s) -> list[str]:
    return sorted({c.strip().upper() for c in re.split(r"[,;\s]+", str(s or "")) if c.strip()})


def import_(folder: Path) -> None:
    ws = load_workbook(folder / "labels_review.xlsx", data_only=True)["labels"]
    rows = list(ws.iter_rows(values_only=True))
    sheet = pd.DataFrame(rows[1:], columns=rows[0]).fillna("")
    sheet["row"] = sheet["row"].astype(int).astype(str)

    problems = []
    for r in sheet.itertuples():
        states, main = _codes(r.final_states), str(r.final_main).strip().upper()
        bad = [c for c in states + ([main] if main else []) if c not in CODES]
        if bad:
            problems.append(f"row {r.row}: not a state code: {', '.join(bad)}")
        if main and main not in states:
            problems.append(f"row {r.row}: final_main {main} is not in final_states")
        if states and not main:
            problems.append(f"row {r.row}: final_states given but no final_main")
        if str(r.final_event).strip() not in ("1", "0", "1.0", "0.0"):
            problems.append(f"row {r.row}: final_event must be 1 or 0")
    if problems:
        print("Nothing written. Fix these in labels_review.xlsx first:")
        print("\n".join("  " + p for p in problems))
        return

    lab = pd.read_csv(folder / "labels.csv", dtype=str, keep_default_na=False)
    new = sheet.set_index("row")
    reviewed = new.reviewed.astype(str).str.strip().str.upper() == "Y"
    changed = 0
    for i, r in lab.iterrows():
        s = new.loc[r.row]
        states = ",".join(_codes(s.final_states))
        main = str(s.final_main).strip().upper()
        event = str(s.final_event).strip()[0]
        if (states, main, event) != (r.human_states, r.human_main, r.human_event):
            changed += 1
        lab.loc[i, ["human_states", "human_main", "human_event"]] = [states, main, event]
        if reviewed[r.row]:
            lab.loc[i, "labelled_by"] = f"claude-opus-5-5, reviewed by Max {date.today()}"
        if str(s.comment).strip():
            lab.loc[i, "note"] = f"{r.note} | Max: {str(s.comment).strip()}"

    backup = folder / "backups" / f"labels.before_review_{date.today()}.csv"
    backup.parent.mkdir(exist_ok=True)       # a subfolder, so scoring does not read it
    if not backup.exists():
        shutil.copy(folder / "labels.csv", backup)
    lab.to_csv(folder / "labels.csv", index=False)
    print(f"wrote labels.csv: {changed} labels changed, {reviewed.sum()} of {len(lab)} rows reviewed "
          f"(backup: backups/{backup.name})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("--folder", default=str(FOLDER))
    args = ap.parse_args()
    (export if args.action == "export" else import_)(Path(args.folder))


if __name__ == "__main__":
    main()
