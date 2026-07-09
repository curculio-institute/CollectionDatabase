#!/usr/bin/env python
"""Report (and optionally repair) taxa that migration 0054 will refuse.

Migration 0054 makes taxon."dwc:nomenclaturalCode" NOT NULL and refuses to run while any
row is NULL, rather than defaulting one in. This script is the remediation path.

    python scripts/repair_taxon_codes.py --db data.real/collection.db            # report only
    python scripts/repair_taxon_codes.py --db data.real/collection.db --apply    # inherit
    python scripts/repair_taxon_codes.py --db data.real/collection.db --apply \
        --assume-code ICN --fix-ranks                                            # + assert

Why a script and not a migration step: the code is a property of the *source* a name came
from, or inherited from its parent chain. Where neither is available, only a human can assert
it. `--assume-code` is that assertion, made explicitly and recorded in the shell history.

Also reports ranks outside TAXON_RANKS. Those come from the same bug (#96): when POWO's fetch
was silently swallowed, the IPNI record's raw rank string ("spec.") was stored instead of
"species". `--fix-ranks` maps the unambiguous IPNI abbreviations back.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.taxa import TAXON_RANKS
from app.vocab import NOMENCLATURAL_CODES

# IPNI's rank abbreviations, which the swallowed-403 fallback stored verbatim. Only the
# unambiguous ones; anything else must be fixed by hand.
_IPNI_RANKS = {
    "spec.": "species",
    "subsp.": "subspecies",
    "var.": "variety",
    "subvar.": "subvariety",
    "f.": "form",
    "subf.": "subform",
    "gen.": "genus",
    "subg.": "subgenus",
    "fam.": "family",
}


def _inherited_code(conn: sqlite3.Connection, taxon_id: int) -> str | None:
    """Walk the parent chain for a code. Returns None if no ancestor carries one."""
    seen: set[int] = set()
    cur = taxon_id
    while cur and cur not in seen:
        seen.add(cur)
        row = conn.execute(
            'SELECT "dwc:parentNameUsageID", "dwc:nomenclaturalCode" FROM taxon WHERE id = ?',
            (cur,),
        ).fetchone()
        if row is None:
            return None
        parent, code = row
        if cur != taxon_id and code:
            return code
        cur = parent
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    ap.add_argument("--assume-code", choices=NOMENCLATURAL_CODES,
                    help="assert this code for rows whose parent chain has none")
    ap.add_argument("--fix-ranks", action="store_true",
                    help="map IPNI rank abbreviations (spec. → species) back to our vocabulary")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"error: {args.db} does not exist", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")

    missing = conn.execute(
        'SELECT id, "dwc:scientificName", "dwc:taxonRank", "dwc:parentNameUsageID" '
        'FROM taxon WHERE "dwc:nomenclaturalCode" IS NULL ORDER BY id'
    ).fetchall()
    bad_ranks = [
        r for r in conn.execute('SELECT id, "dwc:scientificName", "dwc:taxonRank" FROM taxon')
        if r[2] not in TAXON_RANKS
    ]

    if not missing and not bad_ranks:
        print("Nothing to repair: every taxon has a code and a modelled rank.")
        return 0

    resolved: list[tuple[int, str, str]] = []
    unresolved: list[tuple] = []
    for tid, name, rank, _parent in missing:
        code = _inherited_code(conn, tid) or args.assume_code
        if code:
            resolved.append((tid, name, code))
        else:
            unresolved.append((tid, name, rank))

    print(f"{len(missing)} taxon row(s) with no nomenclatural code:\n")
    for tid, name, code in resolved:
        src = "inherited" if _inherited_code(conn, tid) else f"--assume-code {code}"
        print(f"  id={tid:<4} {name:<28} → {code}   ({src})")
    for tid, name, rank in unresolved:
        print(f"  id={tid:<4} {name:<28} → ?     (no ancestor has a code; pass --assume-code)")

    if bad_ranks:
        print(f"\n{len(bad_ranks)} taxon row(s) with a rank outside TAXON_RANKS:\n")
        for tid, name, rank in bad_ranks:
            target = _IPNI_RANKS.get(rank)
            arrow = f"→ {target}" if target else "→ ?  (fix by hand)"
            print(f"  id={tid:<4} {name:<28} {rank!r:10} {arrow}")

    if not args.apply:
        print("\nReport only. Re-run with --apply to write these changes.")
        return 0

    if unresolved:
        print("\nRefusing to apply: some rows have no code and none was asserted. "
              "Re-run with --assume-code.", file=sys.stderr)
        return 1

    with conn:
        for tid, _name, code in resolved:
            conn.execute('UPDATE taxon SET "dwc:nomenclaturalCode" = ?, '
                         "updated_at = datetime('now') WHERE id = ?", (code, tid))
        if args.fix_ranks:
            for tid, _name, rank in bad_ranks:
                target = _IPNI_RANKS.get(rank)
                if target:
                    conn.execute('UPDATE taxon SET "dwc:taxonRank" = ?, '
                                 "updated_at = datetime('now') WHERE id = ?", (target, tid))

    still_bad = [r for r in conn.execute('SELECT id, "dwc:taxonRank" FROM taxon')
                 if r[1] not in TAXON_RANKS]
    print(f"\nApplied: {len(resolved)} code(s) set"
          + (f", {len(bad_ranks) - len(still_bad)} rank(s) fixed" if args.fix_ranks else ""))
    if still_bad:
        print(f"  {len(still_bad)} row(s) still have an unmodelled rank; fix them in the "
              "Taxonomy tab before migrating.")
    print("\nNow run the migration:  python -c \"from app import db_bootstrap; "
          "db_bootstrap.upgrade_to_head()\"   (or just start the app)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
