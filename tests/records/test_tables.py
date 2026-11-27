"""A Markdown table read as rows of plain-text cells."""

from app.records import tables


class TestRows:
    def test_reads_each_row_but_the_delimiter_as_its_cells(self):
        markdown = "| Item | Pay |\n| :--- | --: |\n| Late | **5** |"

        assert tables.rows(markdown) == (("Item", "Pay"), ("Late", "5"))

    def test_keeps_an_escaped_pipe_within_its_cell(self):
        assert tables.rows("| A \\| B | Pay |") == (("A | B", "Pay"),)

    def test_leaves_out_empty_cells_and_rows(self):
        assert tables.rows("| A |  |\n|  |  |") == (("A",),)

    def test_reads_a_break_in_a_cell_as_a_space(self):
        assert tables.rows("| Print<br>it |") == (("Print it",),)

    def test_reads_a_list_in_a_cell_as_its_items_without_their_marks(self):
        assert tables.rows("| Do | - Apply<br>- Record |") == (("Do", "Apply Record"),)


class TestLines:
    """Each cell's lines, as the guide page shows them: a list in a cell is its
    items, each on a line of its own, without their marks."""

    def test_reads_each_cell_as_a_line(self):
        assert tables.lines("| Item | Pay |\n|---|---|\n| Late | 5 |") == (
            "Item",
            "Pay",
            "Late",
            "5",
        )

    def test_reads_each_item_of_a_list_in_a_cell_as_a_line_without_its_mark(self):
        assert tables.lines("| Do | - Apply<br>- Record |") == ("Do", "Apply", "Record")

    def test_reads_the_lines_before_a_cells_list_as_one_line(self):
        assert tables.lines("| Steps:<br>first<br>- Apply<br>- Record |") == (
            "Steps: first",
            "Apply",
            "Record",
        )

    def test_reads_a_cell_as_one_line_when_a_line_after_a_mark_has_none(self):
        assert tables.lines("| - Apply<br>then record |") == ("- Apply then record",)

    def test_reads_a_number_as_words_not_as_a_mark(self):
        assert tables.lines("| 1. Apply<br>2. Record |") == ("1. Apply 2. Record",)

    def test_reads_a_cell_as_one_line_when_bold_runs_on_across_its_marks(self):
        assert tables.lines("| **Intro<br>- Apply<br>- Record** |") == (
            "Intro - Apply - Record",
        )

    def test_reads_a_cell_as_one_line_when_a_link_runs_on_across_its_marks(self):
        assert tables.lines("| [Intro<br>- Apply<br>- Record](#steps) |") == (
            "Intro - Apply - Record",
        )

    def test_reads_a_list_after_bold_closed_before_its_marks(self):
        assert tables.lines("| **Intro**<br>- Apply<br>- Record |") == (
            "Intro",
            "Apply",
            "Record",
        )
