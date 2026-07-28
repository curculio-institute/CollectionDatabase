"""flow_diagram_svg (TaxonWorks Collections flow diagram, design pass) — pure
geometry/string generation, no NiceGUI/Vue involved, so it can be checked directly.

    Total -> {Not eligible, Eligible} -> {Not uploaded, Uploaded} -> {Synced, Diverged}
"""
import re

from app.ui.tw_sync_tab import flow_diagram_svg


def _well_formed(svg: str) -> bool:
    """Every `<text>`/`<svg>`/`<g>` opened is closed, every attribute value is
    quoted — cheap enough to not need a real XML parser, and catches exactly the
    class of bug (a stray/missing quote or bracket in the f-string) this function
    exists to make checkable without opening a browser."""
    if svg.count("<svg") != 1 or svg.count("</svg>") != 1:
        return False
    if svg.count("<text") != svg.count("</text>"):
        return False
    if svg.count("<g") != svg.count("</g>"):
        return False
    return svg.count('"') % 2 == 0


def _rect_heights(svg: str) -> list[float]:
    return [float(h) for h in re.findall(r'<rect[^>]*height="([\d.]+)"', svg)]


def test_zero_total_is_an_empty_but_well_formed_svg():
    svg = flow_diagram_svg(1, 0, 0, 0, 0, 0, 0)
    assert _well_formed(svg)
    assert "<rect" not in svg


def test_normal_split_draws_all_four_columns():
    svg = flow_diagram_svg(1, 40, 12, 28, 10, 16, 2)
    assert _well_formed(svg)
    # total, not_eligible, eligible, not_uploaded, uploaded, synced, diverged
    assert svg.count("<rect") == 7
    assert "Total 40" in svg
    assert "Not eligible 12" in svg
    assert "Not uploaded 10" in svg
    assert "Uploaded 18" in svg          # synced + diverged, the structural node
    assert "Synced 16" in svg
    assert "Diverged 2" in svg


def test_zero_count_segments_are_omitted_not_zero_width_boxes():
    """A segment with count 0 must not render — a silently-drawn zero-width box is
    indistinguishable from a bug, per this project's own '#159 never skip silently'
    convention read the other way: nothing to draw is nothing drawn, not a stray tag."""
    svg = flow_diagram_svg(1, 28, 0, 28, 0, 28, 0)
    assert "Not eligible" not in svg
    assert "Not uploaded" not in svg
    assert "Diverged" not in svg
    assert "✓ Synced 28" in svg


def test_pending_shows_only_the_first_split():
    svg = flow_diagram_svg(1, 40, 12, 28, 0, 0, 0, pending=True)
    assert _well_formed(svg)
    assert "Total 40" in svg
    assert "Eligible 28" in svg
    assert "Check now" in svg
    assert "Not uploaded" not in svg
    assert "Uploaded" not in svg
    assert "Synced" not in svg
    assert "Diverged" not in svg


def test_pending_box_is_itself_the_check_trigger():
    """Live review: the Check action lives in the placeholder box, not a separate
    button — clicking it must emit the same 'col' the row-level handler special-cases
    to call _on_check_repo instead of _on_open_col."""
    svg = flow_diagram_svg(9, 40, 12, 28, 0, 0, 0, pending=True)
    assert "repo_id: 9, col: 'check'" in svg


def test_total_label_is_pluralised():
    assert "Total 1 specimen" in flow_diagram_svg(1, 1, 0, 1, 0, 1, 0)
    assert "specimens" not in flow_diagram_svg(1, 1, 0, 1, 0, 1, 0)
    assert "Total 2 specimens" in flow_diagram_svg(1, 2, 0, 2, 0, 2, 0)


def test_clickable_boxes_carry_a_hover_class_and_tooltip():
    svg = flow_diagram_svg(1, 40, 12, 28, 10, 16, 2)
    assert svg.count('class="flow-box"') == 6  # every leaf + the two branch points
    assert svg.count("<title>Explore the subset</title>") == 6
    # The `.flow-box:hover` rule itself does NOT live in this string (design pass) —
    # a `<style>` tag embedded here is silently stripped by Vue's runtime template
    # compilation (verified live), so it is injected once at the page level instead
    # (`build_tw_sync_tab`'s `ui.add_head_html`), keyed to the `flow-box` class this
    # function stamps onto every clickable `<g>`.
    assert "<style>" not in svg


def test_uploaded_structural_node_has_no_hover_class_or_tooltip():
    svg = flow_diagram_svg(1, 40, 12, 28, 10, 16, 2)
    # "Uploaded" is the only visible box with no open_col target — it must not look
    # or behave clickable either.
    groups = re.findall(r"<g[^>]*>.*?</g>", svg)
    uploaded_group = next(g for g in groups if ">Uploaded 18<" in g)
    assert "flow-box" not in uploaded_group
    assert "<title>" not in uploaded_group


def test_all_not_eligible_has_no_further_columns():
    svg = flow_diagram_svg(1, 20, 20, 0, 0, 0, 0)
    assert _well_formed(svg)
    assert "Not eligible 20" in svg
    assert "Eligible" not in svg  # "Eligible 0" never shown
    assert "Uploaded" not in svg


def test_all_synced_gets_a_checkmark_and_no_diverged_box():
    """#4 (live review): if nothing diverged and nothing is unuploaded, say so
    plainly rather than leaving an empty slot for the reader to notice on their own."""
    svg = flow_diagram_svg(1, 28, 0, 28, 0, 28, 0)
    assert "✓ Synced 28" in svg
    assert "Diverged" not in svg
    assert "Not uploaded" not in svg


def test_not_all_synced_has_no_checkmark():
    svg = flow_diagram_svg(1, 40, 0, 40, 1, 39, 0)
    assert "✓" not in svg
    assert "Synced 39" in svg


def test_uploaded_is_a_structural_node_not_clickable():
    """Uploaded (synced + diverged) has no facet of its own in the rest of the app —
    only the leaves (not_uploaded/synced/diverged) and the two branch points that
    already had one (total/not_eligible/eligible) are click targets."""
    svg = flow_diagram_svg(1, 40, 12, 28, 10, 16, 2)
    assert "col: 'uploaded'" not in svg


def test_box_height_is_identical_regardless_of_count_magnitude():
    """Live review: box size must not scale with the number — a specimen count can
    climb very high, and a box sized to hold "Not uploaded 48213" legibly is not the
    same size as one sized to hold "Diverged 2". Every rendered box must be the same
    fixed height in both a small and a huge collection."""
    small = _rect_heights(flow_diagram_svg(1, 10, 1, 9, 3, 5, 1))
    huge = _rect_heights(flow_diagram_svg(1, 900_000, 100_000, 800_000,
                                           300_000, 499_999, 1))
    assert small == huge
    assert len(set(small)) == 1  # every box in the diagram is the same height


def test_every_leaf_box_click_targets_the_right_repo_and_column():
    svg = flow_diagram_svg(7, 40, 12, 28, 10, 16, 2)
    for col in ("total", "not_eligible", "eligible", "not_uploaded", "synced",
                "diverged"):
        assert f"repo_id: 7, col: '{col}'" in svg


def test_pending_with_zero_eligible_still_draws_a_check_box():
    """Code review fix: when every specimen is ineligible, `eligible_box` does not
    exist to anchor the placeholder on — the pending row must still offer SOME way
    to trigger Check (the header Re-check button only appears once pending is
    false), so the box now falls back to anchoring on Total."""
    svg = flow_diagram_svg(5, 20, 20, 0, 0, 0, 0, pending=True)
    assert _well_formed(svg)
    assert "Check now" in svg
    assert "repo_id: 5, col: 'check'" in svg


def test_small_collection_no_negative_or_nan_dimensions():
    """Every count is 1 or less than a rounding pixel — the classic place a naive
    proportional-height calculation divides by a near-zero and produces garbage."""
    svg = flow_diagram_svg(1, 5, 1, 4, 1, 3, 0)
    assert _well_formed(svg)
    assert "nan" not in svg.lower()
    for attr in ("x", "y", "width", "height"):
        for value in re.findall(rf'{attr}="(-?[\d.]+)"', svg):
            assert float(value) >= 0, f"{attr}={value} is negative in {svg!r}"
