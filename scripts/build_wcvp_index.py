#!/usr/bin/env python
"""Populate the WCVP folder: Kew's archive, the index built from it, and a README.

    python scripts/build_wcvp_index.py                  # download + build into data/wcvp/
    python scripts/build_wcvp_index.py --archive f.zip  # build from an archive on disk
    python scripts/build_wcvp_index.py --dir /tmp/wcvp  # somewhere else

The same thing is available in the app: Settings → Plant names (WCVP) → Download and install.
Both call wcvp.install(), so the UI and the CLI cannot drift.

WCVP has no usable API (see app/services/wcvp.py), so the folder is refreshed by re-running
this when Kew publishes a new release. The index is written to a temp file and atomically
renamed, so a failed download or a corrupt archive never leaves a half-built index in place —
nor replaces a working one.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.services import wcvp


def _progress(phase: str, done: int, total: int | None) -> None:
    """One-line CLI progress, mirroring what the Settings card shows.

    The size comes from the server, never from a constant: the archive is whatever Kew is
    serving today.
    """
    if phase == "download":
        got = (f"{100 * done / total:5.1f}% of {total / 1e6:.0f} MB" if total
               else f"{done / 1e6:.0f} MB")
        print(f"\r  downloading… {got}", end="", flush=True)
    else:
        print("\r  downloading… done" + " " * 20)
        print("  building index…")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", type=Path,
                    help="build from this wcvp_dwca.zip instead of downloading; it is copied "
                         "in, so the folder still holds the source the index was built from")
    ap.add_argument("--dir", type=Path, default=None,
                    help="target folder (default: config.wcvp_dir(), i.e. data/wcvp)")
    ap.add_argument("--url", default=wcvp.WCVP_DWCA_URL)
    args = ap.parse_args()

    folder = args.dir or config.wcvp_dir()
    if args.archive and not args.archive.exists():
        print(f"error: {args.archive} does not exist", file=sys.stderr)
        return 1

    print(f"Source: {args.archive or args.url}")
    print(f"Target: {folder}")
    try:
        report = wcvp.install(folder, url=args.url, archive=args.archive, progress=_progress)
    except wcvp.WcvpError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    index = folder / "wcvp.sqlite"
    print(f"\n{report.meta.label}")
    print(f"  citation: {report.meta.citation}")
    print(f"\n  {report.rows:>9,} names indexed → {index}  ({index.stat().st_size / 1e6:.0f} MB)")
    print(f"  {report.accepted:>9,} accepted        (importable)")
    print(f"  {report.replaced:>9,} replaced-by-X   (importable as a synonym link)")
    print(f"  {report.refused:>9,} refused         (Unplaced / Misapplied — shown, never imported)")
    print(f"\n  archive kept at {folder / 'wcvp_dwca.zip'}")
    print(f"  provenance written to {folder / 'README.md'}")

    # Kew's own referential errors. Loud, because a silently dropped link is data loss.
    if report.dangling_accepted_ids or report.dangling_parent_ids:
        print(f"\n  note: {report.dangling_accepted_ids} accepted_id and "
              f"{report.dangling_parent_ids} parent_id references point at rows that do not "
              f"exist in the archive. These are errors in Kew's data; the import path "
              f"refuses such a row rather than guessing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
