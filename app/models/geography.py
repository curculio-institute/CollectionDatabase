"""Single-name controlled vocabularies for the administrative-geography hierarchy.

Each is the same shape as `preparation` / `habitat` (id, name UNIQUE), referenced by
FK from `collecting_event`, and managed by the generic `Vocabulary` service so the
values can be edited / merged (fold `Deutschland` → `Germany`). This consistency is
what makes the faceted Explore search work (#40).

  country              — English name (canonicalised via pycountry from the ISO code)
  state_province       — English name (OSM name:en); Bundesland in Germany
  county               — local name (Landkreis)
  island               — local name
  administrative_region — local name; the Regierungsbezirk tier (e.g. "Oberbayern").
                          NO Darwin Core term exists for this level — it sits between
                          stateProvince and county — so it is a LOCAL, non-DwC field
                          kept for permit-area queries.

`municipality` and `locality` stay free text on collecting_event (too specific / the
map's job). `country_code` stays a per-event column (dwc:countryCode).
"""
from __future__ import annotations
from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class _NameVocab(Base, TimestampMixin):
    __abstract__ = True
    id:   Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Country(_NameVocab):
    __tablename__ = "country"
    __table_args__ = (UniqueConstraint("name", name="uq_country_name"),)


class StateProvince(_NameVocab):
    __tablename__ = "state_province"
    __table_args__ = (UniqueConstraint("name", name="uq_state_province_name"),)


class County(_NameVocab):
    __tablename__ = "county"
    __table_args__ = (UniqueConstraint("name", name="uq_county_name"),)


class Island(_NameVocab):
    __tablename__ = "island"
    __table_args__ = (UniqueConstraint("name", name="uq_island_name"),)


class AdministrativeRegion(_NameVocab):
    __tablename__ = "administrative_region"
    __table_args__ = (UniqueConstraint("name", name="uq_administrative_region_name"),)
