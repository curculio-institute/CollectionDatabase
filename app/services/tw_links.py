"""One link / verify mechanism for the three linkable vocabularies (spec "tw_links.py").

A local term (tag, data-attribute term) or biological relationship may carry ONE link to
a TaxonWorks keyword / predicate / biological relationship: ``tw_id`` + ``tw_name`` (TW's
name as last fetched) + ``tw_host`` (the host the link was CONFIRMED on; NULL = not
verified). The link is hard to earn and easy to lose: ``tw_host`` is written only when a
fetch from that host returned this id with this name (the host travels on the fetched
``TwTerm`` / ``VerifyOutcome``, never as a caller-supplied argument); anything else clears it. Nothing
here runs on its own — every fetch is a user action (the launch sync is gone).

Verified against SpeciesFileGroup/taxonworks @ 897f385:
* ``GET /api/v1/controlled_vocabulary_terms?type=Keyword|Predicate`` — paged,
  project-scoped (``config/routes/api_v1.rb:142``; ``controlled_vocabulary_terms_controller
  .rb#api_index``; ``type`` filter ``lib/queries/controlled_vocabulary_term/filter.rb:29``).
  Fields ``id, type, name, definition, uri, uri_relation, css_color``
  (``app/views/controlled_vocabulary_terms/api/v1/_attributes.json.jbuilder:1``).
* ``GET /api/v1/biological_relationships`` — paged, ordered by name
  (``biological_relationships_controller.rb#api_index:20``); fields ``id, name,
  inverted_name, …`` (``app/views/biological_relationships/api/v1/_attributes.json.jbuilder``).
  Biological relationships have no colour column (``db/schema.rb``).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

import app.services.taxonworks as tw
from app.models import AnnotationTerm, BiologicalRelationship
from app.models.base import _utcnow
from app.services import annotations as ann
from app.services.taxonworks import TaxonWorksUnreachable

KEYWORD = "keyword"
PREDICATE = "predicate"
RELATIONSHIP = "relationship"
_CVT_TYPE = {KEYWORD: "Keyword", PREDICATE: "Predicate"}
_TERM_KIND = {"tag": KEYWORD, "data_attribute": PREDICATE}
_KIND_TERM = {v: k for k, v in _TERM_KIND.items()}
_NOUN = {KEYWORD: "keyword", PREDICATE: "predicate", RELATIONSHIP: "relationship"}
_TIMEOUT = httpx.Timeout(25.0)


def _client() -> httpx.AsyncClient:
    """Factory, so tests can substitute an ``httpx.MockTransport``."""
    return httpx.AsyncClient(timeout=_TIMEOUT)


@dataclass(frozen=True)
class TwTerm:
    kind: str
    id: int
    name: str
    uri: str | None = None
    css_color: object = None      # raw, unvalidated — see annotations.tw_css_color
    host: str = ""                # the host the fetch ran against; "" = not from a fetch


@dataclass(frozen=True)
class VerifyOutcome:
    row_id: int
    name: str                     # the local name
    state: str                    # verified | name_differs | not_found
    tw_name: str | None           # TW's current name (None when not found)
    color_warning: str | None
    host: str                     # the host the verifying fetch ran against
    tw_id: int | None = None      # the row's link id the outcome was computed for


def kind_of(row) -> str:
    if isinstance(row, BiologicalRelationship):
        return RELATIONSHIP
    if isinstance(row, AnnotationTerm):
        return _TERM_KIND[row.kind]
    raise TypeError(f"not a linkable vocabulary row: {row!r}")


def rows_of_kind(session: Session, kind: str) -> list:
    if kind == RELATIONSHIP:
        return (session.query(BiologicalRelationship)
                .order_by(func.lower(BiologicalRelationship.name)).all())
    return ann.list_terms(session, _KIND_TERM[kind])


def linked_holder(session: Session, kind: str, tw_id: int, exclude=None):
    """The local row of this kind already linked to ``tw_id``, if any."""
    for row in rows_of_kind(session, kind):
        if row.tw_id == tw_id and row is not exclude:
            return row
    return None


def _check_named(row: dict, what: str) -> None:
    if not isinstance(row.get("id"), int) or not str(row.get("name") or "").strip():
        raise TaxonWorksUnreachable(f"{what} returned a row without an id or name.")


async def fetch_candidates(kind: str) -> list[TwTerm]:
    """The TaxonWorks list for one kind, complete or not at all. Every term is stamped
    with the host the pull ran against (read once, with the base URL), so ``link`` and
    ``apply_verification`` earn ``tw_host`` from the fetch itself, not from a caller."""
    host = tw.configured_host()
    if kind in _CVT_TYPE:
        expected = _CVT_TYPE[kind]
        what = f"the TaxonWorks {expected} listing"

        def check(row: dict) -> None:
            if row.get("type") != expected:
                raise TaxonWorksUnreachable(
                    f"{what} returned a {row.get('type')!r} row — the type filter did not "
                    f"apply. Treated as a lookup failure.")
            _check_named(row, what)

        async with _client() as client:
            rows = await tw.fetch_all_pages(client, "/controlled_vocabulary_terms",
                                            {"type": expected}, what=what, check_row=check)
        terms = [TwTerm(kind, r["id"], str(r["name"]).strip(), r.get("uri") or None,
                        r.get("css_color"), host) for r in rows]
    elif kind == RELATIONSHIP:
        what = "the TaxonWorks biological relationship listing"
        async with _client() as client:
            rows = await tw.fetch_all_pages(client, "/biological_relationships", {},
                                            what=what,
                                            check_row=lambda r: _check_named(r, what))
        terms = [TwTerm(kind, r["id"], str(r["name"]).strip(), host=host) for r in rows]
    else:
        raise ValueError(f"unknown link kind {kind!r}")
    return sorted(terms, key=lambda t: t.name.casefold())


def _require_clean_tw_name(name: str) -> None:
    if not name.strip() or any(ch in name for ch in ("\t", "\n", "\r")):
        raise ValueError(f"TaxonWorks name {name!r} contains a tab or line break and "
                         f"cannot become an export column.")


def link(session: Session, row, tw_term: TwTerm) -> str | None:
    """Link ``row`` to a term picked from a FRESH fetch, so it is verified at once.
    Returns a colour warning (annotation terms only) or None."""
    kind = kind_of(row)
    if tw_term.kind != kind:
        raise ValueError(f"cannot link a {_NOUN[kind]} to a TaxonWorks {_NOUN[tw_term.kind]}")
    if not tw_term.host:
        raise ValueError("This term did not come from a TaxonWorks fetch — refusing to "
                         "record a link that was never confirmed on a host.")
    _require_clean_tw_name(tw_term.name)
    if row.tw_id is not None and row.tw_id != tw_term.id:
        raise ValueError(f"'{row.name}' is already linked to TaxonWorks '{row.tw_name}' "
                         f"— unlink it first.")
    holder = linked_holder(session, kind, tw_term.id, exclude=row)
    if holder is not None:
        raise ValueError(f"TaxonWorks {_NOUN[kind]} '{tw_term.name}' is already linked to "
                         f"'{holder.name}'.")
    row.tw_id = tw_term.id
    row.tw_name = tw_term.name
    row.tw_host = tw_term.host
    warning = None
    if isinstance(row, AnnotationTerm):
        row.css_color, warning = ann.tw_css_color(tw_term.css_color)
    row.updated_at = _utcnow()
    session.flush()
    return warning


def unlink(session: Session, row) -> None:
    """The row becomes local only. An annotation term keeps its colour as a local one."""
    row.tw_id = None
    row.tw_name = None
    row.tw_host = None
    row.updated_at = _utcnow()
    session.flush()


def link_state(row, host: str) -> str:
    if row.tw_id is None:
        return "local_only"
    if row.tw_host and row.tw_host == host:
        return "verified"
    return "not_verified"


def apply_verification(session: Session, kind: str, candidates: list[TwTerm], *,
                       host: str) -> list[VerifyOutcome]:
    """Per linked row: same id + same name -> confirmed on ``host``; same id, other name
    -> unconfirmed, user decides (``resolve_name_difference``); id unknown here ->
    unconfirmed, link kept. The colour of an annotation term follows TW whenever the id
    was found (spec "Term colour")."""
    # `host` is consulted only for an empty list (nothing to read it from); otherwise every
    # candidate must carry it, so a link is confirmed only on the host that answered.
    if not host:
        raise ValueError("No TaxonWorks host to verify against.")
    for c in candidates:
        if c.host != host:
            raise ValueError(f"candidate {c.name!r} carries host {c.host!r}, expected "
                             f"{host!r} — refusing to verify against a mixed or unstamped list.")
    by_id = {c.id: c for c in candidates}
    out: list[VerifyOutcome] = []
    for row in rows_of_kind(session, kind):
        if row.tw_id is None:
            continue
        cand = by_id.get(row.tw_id)
        warning = None
        if cand is None:
            row.tw_host = None
            state, tw_name = "not_found", None
        else:
            if isinstance(row, AnnotationTerm):
                row.css_color, warning = ann.tw_css_color(cand.css_color)
            tw_name = cand.name
            if cand.name == row.tw_name:
                row.tw_host, state = host, "verified"
            else:
                row.tw_host, state = None, "name_differs"
        row.updated_at = _utcnow()
        out.append(VerifyOutcome(row.id, row.name, state, tw_name, warning, host,
                                 row.tw_id))
    session.flush()
    return out


async def fetch_for_verify(kinds) -> tuple[str, dict[str, list[TwTerm]]]:
    """The network half of Verify, with no session open: reads the configured host ONCE,
    fetches every kind (concurrently), and refuses — ``TaxonWorksUnreachable`` naming
    the cause — when no host is set or any fetched term carries another host (the setting
    changed mid-fetch). Feed the result to ``apply_verification`` per kind in one short
    session."""
    kinds = list(dict.fromkeys(kinds))
    host = tw.configured_host()
    if not host:
        raise TaxonWorksUnreachable(
            "No TaxonWorks API base URL set — Settings → TaxonWorks connection.")
    lists = await asyncio.gather(*(fetch_candidates(k) for k in kinds))
    if any(c.host != host for terms in lists for c in terms):
        raise TaxonWorksUnreachable(
            "The configured TaxonWorks host changed during the fetch — try again.")
    return host, dict(zip(kinds, lists))


async def verify(session: Session, kind: str) -> list[VerifyOutcome]:
    """Only on user request: ``fetch_for_verify`` for one kind, then
    ``apply_verification``. Raises ``TaxonWorksUnreachable`` naming the cause."""
    host, fetched = await fetch_for_verify([kind])
    return apply_verification(session, kind, fetched[kind], host=host)


def _local_name_taken(session: Session, row, name: str) -> bool:
    if isinstance(row, AnnotationTerm):
        return ann.name_taken(session, row.kind, name, exclude_id=row.id)
    q = session.query(BiologicalRelationship).filter(
        func.lower(BiologicalRelationship.name) == name.strip().lower(),
        BiologicalRelationship.id != row.id)
    return session.query(q.exists()).scalar()


def resolve_name_difference(session: Session, row, outcome: VerifyOutcome,
                            choice: str) -> None:
    """The user's answer to a ``name_differs`` outcome — nothing is decided silently. The
    host and TW's name come from the outcome (i.e. from the fetch), never from the caller."""
    if outcome.state != "name_differs" or outcome.tw_name is None:
        raise ValueError("only a 'name_differs' outcome can be resolved")
    if outcome.row_id != row.id:
        raise ValueError("the outcome belongs to a different row")
    if row.tw_id is None:
        raise ValueError("the row is not linked")
    if not outcome.host:
        raise ValueError("the outcome carries no host")
    if outcome.tw_id is None or row.tw_id != outcome.tw_id:
        # A stale outcome (the row was unlinked and relinked since) must not stamp another
        # term's name / host onto the new link.
        raise ValueError(f"'{row.name}' has been relinked since this check — verify again.")
    if choice == "unlink":
        unlink(session, row)
        return
    tw_name = outcome.tw_name
    _require_clean_tw_name(tw_name)
    if choice == "use_tw":
        if _local_name_taken(session, row, tw_name):
            raise ValueError(f"A local entry named '{tw_name}' already exists — merge or "
                             f"rename it first.")
        row.name = tw_name.strip()
    elif choice != "keep_local":
        raise ValueError(f"unknown choice {choice!r}")
    row.tw_name = tw_name
    row.tw_host = outcome.host
    row.updated_at = _utcnow()
    session.flush()
