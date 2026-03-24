#!/usr/bin/env python3
"""
Generate info.json files from an Excel spreadsheet.

Creates a directory per song named "[Artist] - [Title]" and writes an
info.json inside it matching the format expected by BandMate.

Usage:
    python generate_info.py /path/to/spreadsheet.xlsx [--output-dir ./songs]
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    print("openpyxl is required: pip install openpyxl", file=sys.stderr)
    sys.exit(1)

TUNING_MAP = {
    "standard":        0,
    "half-step down":  -0.5,
    "half step down":  -0.5,
    "drop d":          "Drop D",
    "open-e":          "Open E",
    "standard/capo 1": 0,
}

VOCAL_NAMES = ["Erick", "Sean", "Robyn", "Matt"]
VOCAL_COLS  = [12, 13, 14, 15]


def is_yellow(cell):
    fg = cell.fill.fgColor
    return (
        fg is not None
        and isinstance(getattr(fg, "rgb", None), str)
        and fg.rgb == "FFFFFF00"
    )


def parse_tuning(raw):
    if raw is None or str(raw).strip() in ("", "-"):
        return None
    return TUNING_MAP.get(str(raw).strip().lower(), str(raw).strip())


def parse_capo(raw):
    if raw is None:
        return 0
    return int(raw)


def build_info(row_cells):
    row = [c.value for c in row_cells]

    title        = row[0]
    artist       = row[1]
    tempo        = row[2]
    key          = row[4]
    lead_type    = row[5]
    lead_tuning  = row[6]
    lead_capo    = row[7]
    bass_tuning  = row[8]
    rhythm_type  = row[9]
    rhythm_tuning = row[10]
    rhythm_capo  = row[11]
    start_who    = row[16]
    start_what   = row[17]

    info = {
        "title": title,
        "artist": artist,
    }

    if tempo is not None:
        info["tempo"] = int(tempo)
    if key is not None:
        info["key"] = key

    guitars = {}

    if lead_type and lead_type.strip().lower() != "no guitar":
        tuning = parse_tuning(lead_tuning)
        guitars["lead_guitar"] = {
            "tuning": tuning if tuning is not None else 0,
            "type": lead_type.strip().lower(),
            "capo": parse_capo(lead_capo),
        }

    if rhythm_type and rhythm_type.strip().lower() != "no guitar":
        tuning = parse_tuning(rhythm_tuning)
        guitars["rhythm_guitar"] = {
            "tuning": tuning if tuning is not None else 0,
            "type": rhythm_type.strip().lower(),
            "capo": parse_capo(rhythm_capo),
        }

    bass_tuning_val = parse_tuning(bass_tuning)
    guitars["bass_guitar"] = {
        "tuning": bass_tuning_val if bass_tuning_val is not None else 0,
    }

    if guitars:
        info["guitars"] = guitars

    lead_vocals = []
    backing_vocals = []
    for i, col_idx in enumerate(VOCAL_COLS):
        cell = row_cells[col_idx]
        if cell.value is True:
            if is_yellow(cell):
                lead_vocals.append(VOCAL_NAMES[i])
            else:
                backing_vocals.append(VOCAL_NAMES[i])

    if lead_vocals or backing_vocals:
        vocals = {}
        if lead_vocals:
            vocals["lead"] = lead_vocals
        if backing_vocals:
            vocals["backing"] = backing_vocals
        info["vocals"] = vocals

    if start_who:
        info["starts"] = str(start_who).strip()
    if start_what:
        info["starts_with"] = str(start_what).strip()

    return info


def main():
    parser = argparse.ArgumentParser(description="Generate info.json from spreadsheet")
    parser.add_argument("spreadsheet", help="Path to the .xlsx file")
    parser.add_argument(
        "--output-dir", "-o",
        default="./songs",
        help="Parent directory for song folders (default: ./songs)",
    )
    args = parser.parse_args()

    wb = openpyxl.load_workbook(args.spreadsheet)
    ws = wb["Songs"]
    output = Path(args.output_dir)

    created = 0
    skipped = 0

    for row in ws.iter_rows(min_row=2, max_col=18):
        title = row[0].value
        artist = row[1].value
        if not title or not artist:
            continue

        info = build_info(row)
        dir_name = f"{artist} - {title}"
        dir_name = dir_name.replace("/", "-").replace("\\", "-")
        song_dir = output / dir_name
        song_dir.mkdir(parents=True, exist_ok=True)

        info_path = song_dir / "info.json"
        info_path.write_text(json.dumps(info, indent=4) + "\n")
        created += 1
        print(f"  Created: {dir_name}/info.json")

    print(f"\nDone. {created} info.json files created in {output}")


if __name__ == "__main__":
    main()
