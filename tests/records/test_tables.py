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
