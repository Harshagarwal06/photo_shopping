import pytest

from app.local_vision import _parse_item, plan_locally


@pytest.mark.parametrize(
    ("line", "term", "quantity", "unit"),
    [
        # A dozen is a count of twelve, not one of something.
        ("dozen eggs", "eggs", 12, "count"),
        ("1 dozen eggs", "eggs", 12, "count"),
        ("2 dozen bananas", "bananas", 24, "count"),
        ("eggs 1 dozen", "eggs", 12, "count"),
        # "packet" used to be left in the search term, so the provider was asked
        # for "packet maggi".
        ("2 packet maggi", "maggi", 2, "pack"),
        ("besan 1 packet", "besan", 1, "pack"),
        ("packet bread", "bread", 1, "pack"),
        # Fractions, written both ways.
        ("1/2 kg paneer", "paneer", 0.5, "kg"),
        # "tomatoes" singularises onto the lexicon term; see the fuzzy tests below.
        ("½ kg tomatoes", "tomato", 0.5, "kg"),
        ("1½ kg atta", "atta", 1.5, "kg"),
        ("3/4 l milk", "milk", 0.75, "l"),
        # A decimal is not a numbered-list marker: "2." must not be stripped.
        ("2.5 kg rice", "rice", 2.5, "kg"),
        ("1. milk 2 l", "milk", 2, "l"),
    ],
)
def test_parses_dozens_packets_and_fractions(line, term, quantity, unit):
    item = _parse_item(line, "photo")

    assert item is not None
    assert (item.search_term, item.quantity, item.unit) == (term, quantity, unit)


@pytest.mark.parametrize(
    ("line", "term", "quantity", "unit"),
    [
        ("2 bottle oil", "oil", 2, "pack"),
        ("bottle oil", "oil", 1, "pack"),
        ("1 tin ghee", "ghee", 1, "pack"),
        ("6 can coke", "coke", 6, "pack"),
    ],
)
def test_container_units_normalise_to_pack(line, term, quantity, unit):
    """Containers must map onto "pack": it is the only unit whose requested count
    constraints.units_for_candidate multiplies."""
    item = _parse_item(line, "photo")

    assert (item.search_term, item.quantity, item.unit) == (term, quantity, unit)


@pytest.mark.parametrize("line", ["Coke Can", "Water Bottle", "can opener"])
def test_container_words_inside_a_product_name_are_left_alone(line):
    """A container word only counts when a quantity or line start marks it as one.

    "can" is excluded from the implicit-one rewrite because, unlike "bottle" or
    "tin", it is an ordinary English verb.
    """
    item = _parse_item(line, "photo")

    assert (item.search_term, item.quantity, item.unit) == (line, 1, "item")


@pytest.mark.parametrize(
    ("line", "term"),
    [
        ("2 kg pyaz", "onion"),
        ("aloo 1 kg", "potato"),
        ("dahi 400gm", "curd"),
        ("1/2 kg tamatar", "tomato"),
        ("2 packet jeera", "cumin"),
        ("chai patti", "tea"),
        ("haldi", "turmeric"),
    ],
)
def test_hinglish_terms_resolve_to_searchable_names(line, term):
    assert _parse_item(line, "photo").search_term == term


@pytest.mark.parametrize("line", ["paneer 200gm", "besan 1 packet", "bhindi 500g"])
def test_retail_hindi_names_are_not_translated(line):
    """These are what the catalogues call them; "okra" would search for a word
    the listings do not use."""
    expected = line.split()[0]

    assert _parse_item(line, "photo").search_term == expected


@pytest.mark.parametrize(
    ("line", "term"),
    [
        # Character confusions, undone by exact match on a corrected spelling.
        ("5 kg 0nion", "onion"),
        ("2 kg 0ni0n", "onion"),
        ("rnilk 2 l", "milk"),
        ("1 kg a10o", "potato"),
        ("hald1", "turmeric"),
        # Genuine near-misses, caught by the fuzzy pass.
        ("tomatoe 1 kg", "tomato"),
        ("corriander", "coriander"),
        ("½ kg tomatoes", "tomato"),
        # A lexicon word inside a longer phrase, resolved token by token.
        ("fresh dhaniya", "fresh coriander"),
        ("2 kg desi pyaz", "desi onion"),
    ],
)
def test_misread_terms_resolve(line, term):
    assert _parse_item(line, "photo").search_term == term


@pytest.mark.parametrize(
    "line",
    [
        # "paneer" scores 0.8 against "pani" (water) and "pen" is close to several
        # short terms. Anything already known must never be rewritten.
        "paneer",
        "pen",
        "black pen",
        "Coke Can",
        "Water Bottle",
        "Coffee",
        "besan",
        "bhindi",
        "dal",
        "ghee",
        "toor dal",
        "Amul Butter",
        "tea",
    ],
)
def test_known_and_unknown_words_are_never_fuzzed_onto_something_else(line):
    assert _parse_item(line, "photo").search_term == line


@pytest.mark.parametrize(
    "line",
    [
        "Grocery list",
        "• Crocery list.",  # the "G" misread, as photographed
        "Shopping list:",
        "sabzi list",
    ],
)
def test_a_list_heading_is_not_a_product(line):
    """Blinkit will happily sell something for "grocery list" if it is searched."""
    assert _parse_item(line, "photo") is None


@pytest.mark.parametrize(
    ("line", "term"),
    [
        ("Jecra", "cumin"),  # jeera, the "e" read as "c"
        ("Panees", "paneer"),  # paneer, the "r" read as "s"
        ("rnethi", "methi"),
        ("dhanlya", "coriander"),
    ],
)
def test_letter_shape_confusions_resolve(line, term):
    assert _parse_item(line, "photo").search_term == term


@pytest.mark.parametrize(
    "line",
    ["Dalcheeni", "Pads", "Tide detergent", "Barbecue sauce", "ID Dosa batter", "maggi"],
)
def test_single_letter_swaps_do_not_capture_ordinary_products(line):
    """Dalcheeni is cinnamon; one letter from "cheeni" it would become sugar."""
    assert _parse_item(line, "photo").search_term == line


@pytest.mark.parametrize(
    ("line", "term", "context"),
    [
        ("Paneer (Amul)", "Paneer", "Amul"),
        ("Panees (Amul)", "paneer", "Amul"),  # as photographed, "r" read as "s"
        ("Curd (Nestle) 400 gm", "Curd", "Nestle"),
        ("Milk (full cream) 2 l", "Milk", "full cream"),
    ],
)
def test_a_bracketed_brand_becomes_context_not_part_of_the_query(line, term, context):
    """The ranker scores search_term and context together, so the brand still
    counts — but the provider is asked for "paneer", not "paneer (amul)"."""
    item = _parse_item(line, "photo")

    assert (item.search_term, item.context) == (term, context)


@pytest.mark.parametrize(
    ("line", "quantity", "unit"),
    [
        ("Atta (5 kg)", 5, "kg"),
        ("rice (1/2 kg)", 0.5, "kg"),
        ("eggs (dozen)", 12, "count"),
        ("maggi (packet)", 1, "pack"),
    ],
)
def test_a_bracketed_amount_is_read_as_the_quantity(line, quantity, unit):
    item = _parse_item(line, "photo")

    assert (item.quantity, item.unit, item.context) == (quantity, unit, "")


def test_a_line_that_is_only_a_bracketed_word_keeps_that_word():
    item = _parse_item("(Amul)", "photo")

    assert (item.search_term, item.context) == ("Amul", "")


@pytest.mark.parametrize(
    "line",
    ["GoodLuck", "Page No.", "Date", "Name", "Roll No.", "• Grocery ust", "• Crocery list."],
)
def test_notebook_furniture_and_misread_headings_are_dropped(line):
    """A ruled notebook prints its own words, and OCR reads them as list items.

    The heading is matched on its first word: "ust" is no nearer to "list" than
    "salt" is, while "Crocery" is unmistakably "Grocery".
    """
    assert _parse_item(line, "photo") is None


@pytest.mark.parametrize(
    "line",
    ["salt", "Pads", "Dalcheeni", "sabzi masala", "market fresh paneer", "Date nut bar"],
)
def test_furniture_filter_leaves_products_alone(line):
    assert _parse_item(line, "photo") is not None


@pytest.mark.parametrize(
    ("line", "term", "context", "quantity", "unit"),
    [
        ("milk (Amul - 500ml)", "milk", "Amul", 500, "ml"),
        ("curd (Nestle - 400 gm)", "curd", "Nestle", 400, "g"),
        ("atta (Aashirvaad 5 kg)", "atta", "Aashirvaad", 5, "kg"),
        # An opening bracket read as "C", as photographed.
        ("Cao mill CAmul - 500ml)", "Cao mill", "Amul", 500, "ml"),
    ],
)
def test_a_bracket_holding_brand_and_size_yields_both(line, term, context, quantity, unit):
    item = _parse_item(line, "photo")

    assert (item.search_term, item.context, item.quantity, item.unit) == (
        term,
        context,
        quantity,
        unit,
    )


@pytest.mark.parametrize(
    "line", ["Coke Can", "Cold coffee", "Coffee", "Curd", "Crocin tablets", "Cao mill"]
)
def test_the_bracket_repair_needs_an_unclosed_bracket(line):
    """Only a line that closes a bracket it never opened is repaired, so an
    ordinary product beginning with "C" is untouched."""
    item = _parse_item(line, "photo")

    assert (item.search_term, item.context) == (line, "")


@pytest.mark.parametrize("line", ["1/0 kg broken", "0 kg nothing"])
def test_unusable_quantity_falls_back_instead_of_raising(line):
    """A zero denominator must not divide by zero, and 0 fails PlannedItem's gt=0."""
    item = _parse_item(line, "photo")

    assert item is not None
    assert item.quantity == 1


def test_local_planner_combines_typed_and_recognized_items(monkeypatch):
    monkeypatch.setattr(
        "app.local_vision.recognize_text",
        lambda _bytes, _media_type: "12 eggs\nrice 2 kg",
    )

    plan = plan_locally(
        text="milk 2 l, under 800",
        image_bytes=b"image",
        image_media_type="image/jpeg",
    )

    assert [item.search_term for item in plan.items] == ["milk", "eggs", "rice"]
    assert [item.quantity for item in plan.items] == [2, 12, 2]
    assert plan.constraints.cart_budget == 800
    assert "locally on this Mac" in plan.processing_note
