"""The fixture repair table is memorised, so its size is worth watching."""

from app.ocr_fixture_repairs import (
    FIXTURE_REPAIRS,
    MAX_FIXTURE_REPAIRS,
    apply_fixture_repairs,
)


def test_the_memorised_repair_table_stays_capped():
    """Adding an entry is always the cheapest way to make a fixture photo pass,
    and an entry only ever helps that photo. This cap does not prevent growth —
    it makes growing the table a decision rather than a reflex. If this fails,
    the question to answer first is whether Vision's customWords prior, the
    lexicon, or the candidate ranking could fix the line instead.
    """
    assert len(FIXTURE_REPAIRS) <= MAX_FIXTURE_REPAIRS


def test_repairs_leave_ordinary_grocery_lines_untouched():
    """These share letters with table entries but must survive unchanged."""
    for line in (
        "Bread",
        "Butter",
        "poha",
        "ice cream",
        "brown bread",
        "chana dal",
        "red chilli powder",
        "tomato soup",
        "blue cheese",
        "quinoa",
        "banana",
    ):
        assert apply_fixture_repairs(line) == line


def test_a_context_scoped_repair_needs_its_context():
    # "come" only becomes "cone" beside ice cream.
    assert apply_fixture_repairs("come chocolate") == "come chocolate"
    assert (
        apply_fixture_repairs("Havmor ice cream come chocolate")
        == "Havmor ice cream cone chocolate"
    )


def test_a_whole_line_repair_never_fires_inside_a_longer_line():
    assert apply_fixture_repairs("citcat") == "KitKat"
    assert apply_fixture_repairs("citcat wafer bar") == "citcat wafer bar"
