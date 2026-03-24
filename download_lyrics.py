#!/usr/bin/env python3
"""
Download synced lyrics (.lrc) from LRCLIB for songs listed in a spreadsheet.

Reads the "Songs" sheet from an Excel file and for each row searches LRCLIB
for synced lyrics.  If found, writes a lyrics.lrc file into a directory named
"[Artist] - [Title]".

Usage:
    python download_lyrics.py /path/to/spreadsheet.xlsx [--output-dir ./songs]
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import openpyxl
except ImportError:
    print("openpyxl is required: pip install openpyxl", file=sys.stderr)
    sys.exit(1)

LRCLIB_SEARCH = "https://lrclib.net/api/search"
USER_AGENT = "BandMate LRC Downloader v1.0 (https://github.com/bandmate)"


def search_lyrics(track_name: str, artist_name: str):
    """Search LRCLIB and return the best matching result."""
    params = urllib.parse.urlencode({
        "track_name": track_name,
        "artist_name": artist_name,
    })
    url = f"{LRCLIB_SEARCH}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return None, str(e)

    if not data:
        return None, "no results"

    for result in data:
        if result.get("syncedLyrics"):
            return result, None

    if data[0].get("plainLyrics"):
        return data[0], "synced lyrics not available, plain only"

    return None, "no lyrics in results"


def plain_to_lrc(plain_lyrics: str) -> str:
    """Convert plain lyrics to minimal LRC format (all lines at 00:00.00)."""
    lines = plain_lyrics.strip().split("\n")
    return "\n".join(f"[00:00.00] {line}" for line in lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Download lyrics from LRCLIB")
    parser.add_argument("spreadsheet", help="Path to the .xlsx file")
    parser.add_argument(
        "--output-dir", "-o",
        default="./songs",
        help="Parent directory for song folders (default: ./songs)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing lyrics.lrc files",
    )
    args = parser.parse_args()

    wb = openpyxl.load_workbook(args.spreadsheet)
    ws = wb["Songs"]
    output = Path(args.output_dir)

    found = 0
    skipped = 0
    not_found = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        title = row[0]
        artist = row[1]
        if not title or not artist:
            continue

        title = str(title).strip()
        artist = str(artist).strip()
        dir_name = f"{artist} - {title}".replace("/", "-").replace("\\", "-")
        song_dir = output / dir_name
        lrc_path = song_dir / "lyrics.lrc"

        if lrc_path.exists() and not args.overwrite:
            print(f"  SKIP (exists): {dir_name}")
            skipped += 1
            continue

        result, err = search_lyrics(title, artist)

        if result is None:
            print(f"  NOT FOUND: {dir_name} ({err})")
            not_found += 1
            time.sleep(0.3)
            continue

        synced = result.get("syncedLyrics")
        plain = result.get("plainLyrics")

        if synced:
            lrc_content = synced.strip() + "\n"
            tag = "synced"
        elif plain:
            lrc_content = plain_to_lrc(plain)
            tag = "plain only"
        else:
            print(f"  NOT FOUND: {dir_name} (empty lyrics)")
            not_found += 1
            time.sleep(0.3)
            continue

        matched = f"{result.get('artistName', '?')} - {result.get('trackName', '?')}"
        song_dir.mkdir(parents=True, exist_ok=True)
        lrc_path.write_text(lrc_content, encoding="utf-8")
        found += 1
        print(f"  OK ({tag}): {dir_name}  [matched: {matched}]")

        time.sleep(0.3)

    print(f"\nDone. {found} downloaded, {skipped} skipped, {not_found} not found.")


if __name__ == "__main__":
    main()
