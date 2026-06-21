"""format_catalog_display: render a specimen identifier without doubling the
collection-code prefix, while still surfacing a transferred specimen's current
holder.

catalog_number embeds an immutable prefix (JJPRC-00001) that travels with the
specimen; collection_code is the current holder and is mutable. They coincide at
home and diverge after a transfer.
"""
import pytest

from app.services.identifiers import format_catalog_display as fmt


@pytest.mark.parametrize("cc, cn, expected", [
    # at home: prefix already names the origin → no repeat
    ("JJPRC", "JJPRC-00001", "JJPRC-00001"),
    # transferred: current holder differs from the embedded prefix → show both
    ("ABC",   "JJPRC-00001", "ABC JJPRC-00001"),
    # foreign / visiting specimen with a non-prefixed catalog number → show both
    ("Smith", "ABC123",      "Smith ABC123"),
    # exact-equal edge (no separator) is still treated as embedded
    ("JJPRC", "JJPRC",       "JJPRC"),
    # space separator variant
    ("JJPRC", "JJPRC 00001", "JJPRC 00001"),
    # missing pieces degrade gracefully
    ("JJPRC", "",            "JJPRC"),
    ("",      "JJPRC-00001", "JJPRC-00001"),
    (None,    "JJPRC-00001", "JJPRC-00001"),
    ("JJPRC", None,          "JJPRC"),
])
def test_format_catalog_display(cc, cn, expected):
    assert fmt(cc, cn) == expected
