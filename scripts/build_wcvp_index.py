#!/usr/bin/env python
"""Download the WCVP Darwin Core Archive and build the offline plant-name index.

    python scripts/build_wcvp_index.py                  # download + build into data/
    python scripts/build_wcvp_index.py --archive f.zip  # build from an archive on disk
    python scripts/build_wcvp_index.py --keep-archive   # keep the 85 MB zip after building

WCVP has no usable API (see app/services/wcvp.py), so the index is refreshed by re-running
this script when Kew publishes a new version. Rebuilding is idempotent: the target index is
replaced wholesale, so a failed run never leaves a half-built index in place.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from app import config
from app.services import wcvp


def _progress(phase: str, frac):
    """One-line CLI progress, matching what the Settings card shows."""
    if phase == "download":
        bar = f"{100 * frac:4.1f}%" if frac is not None else "…"
        print(f"\r  downloading… {bar}", end="", flush=True)
    else:
        print("\r  downloading… done   ")
        print("  building index…")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", type=Path,
                    help="use this wcvp_dwca.zip instead of downloading")
    ap.add_argument("--out", type=Path, default=None,
                    help="index path (default: config.wcvp_db_path())")
    ap.add_argument("--url", default=wcvp.WCVP_DWCA_URL)
    ap.add_argument("--keep-archive", action="store_true",
                    help="do not delete a downloaded archive after building")
    args = ap.parse_args()

    out = args.out or config.wcvp_db_path()

    tmpdir: tempfile.TemporaryDirectory | None = None
    if args.archive:
        archive = args.archive
        if not archive.exists():
            print(f"error: {archive} does not exist", file=sys.stderr)
            return 1
    else:
        tmpdir = tempfile.TemporaryDirectory()
        target = Path(tmpdir.name) / "wcvp_dwca.zip"
        print(f"Source: {args.url}")
        archive = wcvp.download_archive(target, args.url, progress=_progress)
        if args.keep_archive:
            kept = out.parent / "wcvp_dwca.zip"
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_bytes(archive.read_bytes())
            print(f"  archive kept at {kept}")

    print(f"Building {out} …")
    try:
        report = wcvp.build_index(archive, out)
    except wcvp.WcvpError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()

    size_mb = out.stat().st_size / 1e6
    print(f"\n{report.meta.label}")
    print(f"  citation: {report.meta.citation}")
    print(f"\n  {report.rows:>9,} names indexed → {out}  ({size_mb:.0f} MB)")
    print(f"  {report.accepted:>9,} accepted        (importable)")
    print(f"  {report.replaced:>9,} replaced-by-X   (importable as a synonym link)")
    print(f"  {report.refused:>9,} refused         (Unplaced / Misapplied — shown, never imported)")

    # Kew's own referential errors. Loud, because a silently dropped link is data loss.
    if report.dangling_accepted_ids or report.dangling_parent_ids:
        print(f"\n  note: {report.dangling_accepted_ids} accepted_id and "
              f"{report.dangling_parent_ids} parent_id references point at rows that do not "
              f"exist in the archive. These are errors in Kew's data; the import path "
              f"refuses such a row rather than guessing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
