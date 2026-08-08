import unittest

import md


class FrontmatterTest(unittest.TestCase):
    def rendered_text(self, markdown_text: str, width: int = 72) -> str:
        rendered_lines = md.render_markdown(
            markdown_text,
            width,
            md.DEFAULT_THEME,
            use_color=False,
        )
        return "\n".join(md.strip_ansi_codes(line) for line in rendered_lines)

    def test_complete_leading_frontmatter_is_hidden_from_rendered_output(self) -> None:
        markdown_text = (
            "---\n"
            "name: demo-skill\n"
            "description: Demonstrates frontmatter rendering.\n"
            "---\n"
            "\n"
            "# Instructions\n"
            "\n"
            "Do the thing.\n"
        )

        rendered_text = self.rendered_text(markdown_text)

        self.assertNotIn("demo-skill", rendered_text)
        self.assertNotIn("Demonstrates frontmatter rendering.", rendered_text)
        self.assertIn("Instructions", rendered_text)
        self.assertIn("Do the thing.", rendered_text)

    def test_raw_rendering_preserves_frontmatter_source_lines(self) -> None:
        markdown_text = (
            "---\n"
            "name: demo-skill\n"
            "description: Demonstrates frontmatter rendering.\n"
            "---\n"
            "\n"
            "# Instructions\n"
            "\n"
            "Do the thing.\n"
        )

        for line_numbers in (False, True):
            with self.subTest(line_numbers=line_numbers):
                rendered_lines = md.render_raw_markdown(
                    markdown_text,
                    160,
                    md.DEFAULT_THEME,
                    line_numbers=line_numbers,
                    use_color=False,
                )
                source_lines = [
                    rendered_line.split(" │ ", 1)[1]
                    for rendered_line in rendered_lines[:-1]
                ]

                self.assertEqual(source_lines, markdown_text.splitlines())

    def test_non_leading_thematic_break_is_rendered(self) -> None:
        markdown_text = "Before\n\n---\n\nAfter\n"

        self.assertIsNone(md.find_leading_frontmatter_end(markdown_text))
        rendered_text = self.rendered_text(markdown_text, width=40)
        self.assertIn("Before", rendered_text)
        self.assertIn("After", rendered_text)

    def test_leading_rule_and_later_rule_do_not_hide_markdown(self) -> None:
        markdown_text = (
            "---\n"
            "# Important\n"
            "\n"
            "This section is ordinary Markdown.\n"
            "\n"
            "---\n"
            "\n"
            "# Remaining\n"
        )

        self.assertIsNone(md.find_leading_frontmatter_end(markdown_text))
        rendered_text = self.rendered_text(markdown_text)
        self.assertIn("Important", rendered_text)
        self.assertIn("This section is ordinary Markdown.", rendered_text)
        self.assertIn("Remaining", rendered_text)

    def test_unterminated_leading_block_is_rendered_as_markdown(self) -> None:
        markdown_text = "---\nname: not-frontmatter\n\nBody\n"

        self.assertIsNone(md.find_leading_frontmatter_end(markdown_text))
        rendered_text = self.rendered_text(markdown_text, width=40)
        self.assertIn("name: not-frontmatter", rendered_text)
        self.assertIn("Body", rendered_text)

    def test_non_lf_frontmatter_is_hidden_from_rendered_output(self) -> None:
        for line_ending in ("\r\n", "\r"):
            with self.subTest(line_ending=repr(line_ending)):
                markdown_text = line_ending.join(
                    [
                        "---",
                        "name: demo-skill",
                        "description: Uses non-LF line endings.",
                        "---",
                        "",
                        "Body text.",
                        "",
                    ]
                )

                rendered_text = self.rendered_text(markdown_text)
                self.assertNotIn("demo-skill", rendered_text)
                self.assertNotIn("Uses non-LF line endings.", rendered_text)
                self.assertIn("Body text.", rendered_text)

    def test_frontmatter_markdown_like_lines_are_excluded_from_outline(self) -> None:
        markdown_text = (
            "---\n"
            "name: demo-skill\n"
            "# Metadata comment\n"
            "description: Demonstrates outline filtering.\n"
            "---\n"
            "\n"
            "# Instructions\n"
            "\n"
            "## Details\n"
        )

        headers = md.parse_headers(markdown_text)

        self.assertEqual(
            headers,
            [
                {"level": 1, "title": "Instructions", "raw_line_index": 6},
                {"level": 2, "title": "Details", "raw_line_index": 8},
            ],
        )

    def test_outline_indexes_include_bom_and_non_lf_frontmatter_lines(self) -> None:
        for line_ending in ("\r\n", "\r"):
            with self.subTest(line_ending=repr(line_ending)):
                markdown_text = "\ufeff" + line_ending.join(
                    [
                        "---",
                        "name: demo-skill",
                        "description: Preserves source indexes.",
                        "---",
                        "",
                        "# Instructions",
                        "",
                    ]
                )

                self.assertEqual(
                    md.parse_headers(markdown_text),
                    [
                        {
                            "level": 1,
                            "title": "Instructions",
                            "raw_line_index": 5,
                        }
                    ],
                )

    def test_utf_8_bom_before_frontmatter_is_hidden(self) -> None:
        markdown_text = (
            "\ufeff---\n"
            "name: demo-skill\n"
            "description: Starts after a byte order mark.\n"
            "---\n"
            "\n"
            "Body text.\n"
        )

        rendered_text = self.rendered_text(markdown_text)

        self.assertNotIn("demo-skill", rendered_text)
        self.assertNotIn("Starts after a byte order mark.", rendered_text)
        self.assertIn("Body text.", rendered_text)

    def test_frontmatter_delimiters_must_be_exact(self) -> None:
        padded_opening_delimiter = "--- \nname: demo-skill\n---\n"
        padded_closing_delimiter = "---\nname: demo-skill\n--- \n"

        self.assertIsNone(
            md.find_leading_frontmatter_end(padded_opening_delimiter)
        )
        self.assertIsNone(
            md.find_leading_frontmatter_end(padded_closing_delimiter)
        )


if __name__ == "__main__":
    unittest.main()
