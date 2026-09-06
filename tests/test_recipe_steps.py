import recipe_steps

INGREDIENTS = [
    {"name": "chicken thighs"},
    {"name": "tomatoes"},
    {"name": "chipotle"},
    {"name": "tortillas"},
]

STEPS = [
    {"id": "s1", "action": "season and sear", "inputs": ["chicken thighs"]},
    {"id": "s2", "action": "simmer 45 min", "inputs": ["s1", "tomatoes", "chipotle"]},
    {"id": "s3", "action": "serve", "inputs": ["s2", "tortillas"]},
]


def test_every_ingredient_gets_a_row():
    table = recipe_steps.build_table(INGREDIENTS, STEPS)

    assert [row["ingredient"]["name"] for row in table["rows"]] == [
        "chicken thighs",
        "tomatoes",
        "chipotle",
        "tortillas",
    ]


def test_column_count_is_the_depth_of_the_graph():
    table = recipe_steps.build_table(INGREDIENTS, STEPS)

    assert table["columns"] == 3


def test_a_step_spans_the_rows_of_everything_it_consumes():
    table = recipe_steps.build_table(INGREDIENTS, STEPS)
    cells = {c["action"]: c for row in table["rows"] for c in row["cells"]}

    assert cells["season and sear"]["rowspan"] == 1
    assert cells["simmer 45 min"]["rowspan"] == 3
    assert cells["serve"]["rowspan"] == 4


def test_each_step_sits_in_its_own_column():
    table = recipe_steps.build_table(INGREDIENTS, STEPS)
    cells = {c["action"]: c for row in table["rows"] for c in row["cells"]}

    assert cells["season and sear"]["column"] == 0
    assert cells["simmer 45 min"]["column"] == 1
    assert cells["serve"]["column"] == 2


def test_a_cell_is_emitted_only_on_its_first_row():
    table = recipe_steps.build_table(INGREDIENTS, STEPS)

    real = [len([c for c in row["cells"] if not c.get("spacer")]) for row in table["rows"]]
    assert real == [3, 0, 0, 0], "all three real cells start on the first row here"


def test_ingredients_are_reordered_so_spans_stay_contiguous():
    """Declared order puts tortillas between things that combine earlier."""
    ingredients = [
        {"name": "tortillas"},
        {"name": "chicken thighs"},
        {"name": "tomatoes"},
    ]
    steps = [
        {"id": "s1", "action": "simmer", "inputs": ["chicken thighs", "tomatoes"]},
        {"id": "s2", "action": "serve", "inputs": ["s1", "tortillas"]},
    ]

    table = recipe_steps.build_table(ingredients, steps)

    names = [row["ingredient"]["name"] for row in table["rows"]]
    assert names.index("chicken thighs") + 1 == names.index("tomatoes")


def test_an_unused_ingredient_still_gets_a_row():
    table = recipe_steps.build_table(
        INGREDIENTS + [{"name": "lime"}], STEPS
    )

    assert "lime" in [row["ingredient"]["name"] for row in table["rows"]]


def test_a_step_naming_an_unknown_ingredient_gains_a_row_for_it():
    """Nothing the model wrote should silently vanish from the table."""
    table = recipe_steps.build_table(
        [{"name": "chicken thighs"}],
        [{"id": "s1", "action": "sear with", "inputs": ["chicken thighs", "butter"]}],
    )

    assert "butter" in [row["ingredient"]["name"] for row in table["rows"]]


def test_no_steps_means_no_table():
    assert recipe_steps.build_table(INGREDIENTS, []) is None


def test_a_step_cycle_does_not_hang():
    """Model output is untrusted; a self-referential graph must not loop."""
    table = recipe_steps.build_table(
        [{"name": "a"}],
        [
            {"id": "s1", "action": "one", "inputs": ["s2", "a"]},
            {"id": "s2", "action": "two", "inputs": ["s1"]},
        ],
    )

    assert table is not None


def test_method_lines_number_the_steps_in_order():
    lines = recipe_steps.method_lines(INGREDIENTS, STEPS)

    assert lines[0].startswith("1.")
    assert "season and sear" in lines[0]
    assert "chicken thighs" in lines[0]
    assert len(lines) == 3


BRANCHED = [
    {"id": "s1", "action": "sear", "inputs": ["chicken"]},
    {"id": "s2", "action": "soften", "inputs": ["onion"]},
    {"id": "s3", "action": "blend", "inputs": ["s2", "chipotle", "tomatoes"]},
    {"id": "s4", "action": "simmer", "inputs": ["s1", "s3"]},
    {"id": "s5", "action": "serve", "inputs": ["s4", "tortillas"]},
]
BRANCHED_INGREDIENTS = [
    {"name": "chicken"},
    {"name": "chipotle"},
    {"name": "tomatoes"},
    {"name": "onion"},
    {"name": "tortillas"},
]


def test_every_row_accounts_for_every_column():
    """HTML lays cells out left-to-right and ignores our column numbers, so
    a gap needs an explicit spacer or later cells shift left and collide."""
    table = recipe_steps.build_table(BRANCHED_INGREDIENTS, BRANCHED)

    columns = table["columns"]
    occupied = [0] * len(table["rows"])
    for index, row in enumerate(table["rows"]):
        emitted = 0
        for cell in row["cells"]:
            emitted += 1
            for offset in range(cell["rowspan"]):
                if offset:
                    occupied[index + offset] += 1
        assert emitted + occupied[index] == columns, f"row {index} does not fill the table"


def test_spacers_are_marked_so_they_render_empty():
    table = recipe_steps.build_table(BRANCHED_INGREDIENTS, BRANCHED)

    spacers = [c for row in table["rows"] for c in row["cells"] if c.get("spacer")]
    assert spacers, "this graph needs at least one spacer"
    assert all(c["action"] == "" for c in spacers)


def test_a_straight_chain_also_fills_every_column():
    """`sear` spans one row while `simmer` spans three, so the rows below
    still need spacers in the first column."""
    table = recipe_steps.build_table(INGREDIENTS, STEPS)

    columns = table["columns"]
    occupied = [0] * len(table["rows"])
    for index, row in enumerate(table["rows"]):
        emitted = len(row["cells"])
        for cell in row["cells"]:
            for offset in range(1, cell["rowspan"]):
                occupied[index + offset] += 1
        assert emitted + occupied[index] == columns
