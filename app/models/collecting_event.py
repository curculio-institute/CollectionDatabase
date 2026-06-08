from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Integer, String, Float, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class CollectingEvent(Base, TimestampMixin):
    """Where and when specimens were collected. DwC columns carry dwc: prefix."""

    __tablename__ = "collecting_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # DwC verbatim label (MaterialEntity term)
    verbatim_label: Mapped[Optional[str]] = mapped_column("dwc:verbatimLabel", String, nullable=True)

    # DwC locality hierarchy: continent → country → stateProvince → county → municipality → locality
    continent: Mapped[Optional[str]] = mapped_column("dwc:continent", String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column("dwc:country", String, nullable=True)
    country_code: Mapped[Optional[str]] = mapped_column("dwc:countryCode", String, nullable=True)
    state_province: Mapped[Optional[str]] = mapped_column("dwc:stateProvince", String, nullable=True)
    county: Mapped[Optional[str]] = mapped_column("dwc:county", String, nullable=True)
    municipality: Mapped[Optional[str]] = mapped_column("dwc:municipality", String, nullable=True)
    island: Mapped[Optional[str]] = mapped_column("dwc:island", String, nullable=True)
    locality: Mapped[Optional[str]] = mapped_column("dwc:locality", String, nullable=True)
    verbatim_locality: Mapped[Optional[str]] = mapped_column("dwc:verbatimLocality", String, nullable=True)
    location_remarks: Mapped[Optional[str]] = mapped_column("dwc:locationRemarks", String, nullable=True)

    # DwC coordinates
    decimal_latitude: Mapped[Optional[float]] = mapped_column("dwc:decimalLatitude", Float, nullable=True)
    decimal_longitude: Mapped[Optional[float]] = mapped_column("dwc:decimalLongitude", Float, nullable=True)
    geodetic_datum: Mapped[Optional[str]] = mapped_column("dwc:geodeticDatum", String, nullable=True, default="WGS84")
    coordinate_uncertainty_in_meters: Mapped[Optional[float]] = mapped_column("dwc:coordinateUncertaintyInMeters", Float, nullable=True)
    coordinate_precision: Mapped[Optional[float]] = mapped_column("dwc:coordinatePrecision", Float, nullable=True)
    verbatim_coordinates: Mapped[Optional[str]] = mapped_column("dwc:verbatimCoordinates", String, nullable=True)
    verbatim_coordinate_system: Mapped[Optional[str]] = mapped_column("dwc:verbatimCoordinateSystem", String, nullable=True)

    # DwC elevation
    minimum_elevation_in_meters: Mapped[Optional[float]] = mapped_column("dwc:minimumElevationInMeters", Float, nullable=True)
    maximum_elevation_in_meters: Mapped[Optional[float]] = mapped_column("dwc:maximumElevationInMeters", Float, nullable=True)
    verbatim_elevation: Mapped[Optional[str]] = mapped_column("dwc:verbatimElevation", String, nullable=True)

    # DwC georeferencing provenance
    georeferenced_by: Mapped[Optional[str]] = mapped_column("dwc:georeferencedBy", String, nullable=True)
    georeferenced_date: Mapped[Optional[str]] = mapped_column("dwc:georeferencedDate", String, nullable=True)
    georeference_protocol: Mapped[Optional[str]] = mapped_column("dwc:georeferenceProtocol", String, nullable=True)
    georeference_sources: Mapped[Optional[str]] = mapped_column("dwc:georeferenceSources", String, nullable=True)
    georeference_remarks: Mapped[Optional[str]] = mapped_column("dwc:georeferenceRemarks", String, nullable=True)
    georeference_verification_status: Mapped[Optional[str]] = mapped_column("dwc:georeferenceVerificationStatus", String, nullable=True)

    # DwC event
    event_date: Mapped[Optional[str]] = mapped_column("dwc:eventDate", String, nullable=True)
    verbatim_event_date: Mapped[Optional[str]] = mapped_column("dwc:verbatimEventDate", String, nullable=True)
    field_number: Mapped[Optional[str]] = mapped_column("dwc:fieldNumber", String, nullable=True)
    habitat: Mapped[Optional[str]] = mapped_column("dwc:habitat", String, nullable=True)
    sampling_protocol: Mapped[Optional[str]] = mapped_column("dwc:samplingProtocol", String, nullable=True)
    recorded_by: Mapped[Optional[str]] = mapped_column("dwc:recordedBy", String, ForeignKey("person.full_name", ondelete="RESTRICT"), nullable=True)
    event_remarks: Mapped[Optional[str]] = mapped_column("dwc:eventRemarks", String, nullable=True)

    # Non-DwC: Phase-3 GIS enrichment (populated by habitat enrichment script)
    habitat_enriched: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 1 = buffer spans >1 habitat class; 0 = unambiguous; NULL = not yet assessed
    habitat_ambiguous: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            '"dwc:decimalLatitude" IS NULL OR ("dwc:decimalLatitude" >= -90.0 AND "dwc:decimalLatitude" <= 90.0)',
            name="ck_ce_lat_range",
        ),
        CheckConstraint(
            '"dwc:decimalLongitude" IS NULL OR ("dwc:decimalLongitude" >= -180.0 AND "dwc:decimalLongitude" <= 180.0)',
            name="ck_ce_lon_range",
        ),
        CheckConstraint(
            '"dwc:coordinateUncertaintyInMeters" IS NULL OR "dwc:coordinateUncertaintyInMeters" >= 0.0',
            name="ck_ce_uncertainty_positive",
        ),
        CheckConstraint(
            '"dwc:countryCode" IS NULL OR length("dwc:countryCode") = 2',
            name="ck_ce_country_code_len",
        ),
        CheckConstraint(
            "habitat_ambiguous IS NULL OR habitat_ambiguous IN (0, 1)",
            name="ck_ce_habitat_ambiguous_bool",
        ),
    )

    collection_objects: Mapped[List["CollectionObject"]] = relationship(
        "CollectionObject", back_populates="collecting_event"
    )
