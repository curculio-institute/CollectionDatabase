"""Single-name controlled vocabularies for the administrative-geography hierarchy.

Each is the same shape as `preparation` / `habitat` (id, name UNIQUE), referenced by
FK from `collecting_event`, and managed by the generic `Vocabulary` service so the
values can be edited / merged (fold `Deutschland` → `Germany`). This consistency is
what makes the faceted Explore search work (#40).

  country              — English name (OSM name:en) + iso_code (ISO 3166-1, "DE")
  state_province       — English name (OSM name:en) + iso_code (ISO 3166-2, "DE-BY")
  county               — local name (Landkreis)
  island               — local name

`country` and `state_province` are keyed by (name, iso_code): 40 ISO 3166-2 subdivision
names are shared across countries, so the name alone is not an identity. The lower tiers
have no ISO code and keep UNIQUE(name) — the same flaw, with no honest fix. See
migration 0056 and CLAUDE.md.
  administrative_region — local name; the Regierungsbezirk tier (e.g. "Oberbayern").
                          NO Darwin Core term exists for this level — it sits between
                          stateProvince and county — so it is a LOCAL, non-DwC field
                          kept for permit-area queries.

`municipality` and `locality` stay free text on collecting_event (too specific / the
map's job). `country_code` stays a per-event column (dwc:countryCode).
"""
from __future__ import annotations
from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class _NameVocab(Base, TimestampMixin):
    __abstract__ = True
    id:   Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


# A geography vocab row is identified by (name, iso_code) — NOT by name alone. 40 of the
# 5,420 ISO 3166-2 subdivision names are shared across countries (Limburg = BE-VLI + NL-LI,
# Punjab = IN-PB + PK-PB), so UNIQUE(name) would force two real places into one row.
# IFNULL(): SQLite treats NULL != NULL, so a plain UNIQUE(name, iso_code) would allow endless
# uncoded duplicates. This way: one uncoded row per name, one row per distinct code.
# Migration 0056.
def _name_iso_unique(table: str) -> Index:
    return Index(f"uq_{table}_name_iso", "name", text("IFNULL(iso_code, '')"), unique=True)


class Country(_NameVocab):
    __tablename__ = "country"
    __table_args__ = (_name_iso_unique("country"),)

    # ISO 3166-1 alpha-2 ("DE"). `dwc:countryCode` remains a per-event column — that is the
    # Darwin Core term the export emits, and is not this row's identity.
    iso_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class StateProvince(_NameVocab):
    __tablename__ = "state_province"
    __table_args__ = (_name_iso_unique("state_province"),)

    # ISO 3166-2 code of the first-order subdivision ("DE-BY", "GR-J", "CN-YN"), as tagged
    # on the containing OSM boundary relation — the same tag the geocoder uses to identify
    # which relation *is* the state. Nullable: rows created before migration 0055, or by
    # hand, have none, and the code is not required.
    iso_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class County(_NameVocab):
    __tablename__ = "county"
    __table_args__ = (UniqueConstraint("name", name="uq_county_name"),)


class Island(_NameVocab):
    __tablename__ = "island"
    __table_args__ = (UniqueConstraint("name", name="uq_island_name"),)


class AdministrativeRegion(_NameVocab):
    __tablename__ = "administrative_region"
    __table_args__ = (UniqueConstraint("name", name="uq_administrative_region_name"),)
