#!/usr/bin/env uv run --script

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "rich>=13.0.0",
#     "click>=8.0.0",
# ]
# ///

import os
import re
import select
import subprocess
import sys
import termios
import urllib.parse
import webbrowser

import click
import rich.console
import rich.markdown
import rich.segment
import rich.syntax

DEFAULT_THEME = "ansi_dark"


@click.command()
@click.argument("files", nargs=-1, required=False)
@click.option(
    "-w",
    "--watch",
    is_flag=True,
    help="Watch file for writes and reload dynamically.",
)
def main(files: tuple[str, ...], watch: bool) -> None:
    files_list = list(files) if files else ["-"]
    watch_flag = watch
    theme = os.environ.get("MD_THEME", DEFAULT_THEME)

    buffer_list = []
    buffer_contents = {}
    buffer_states = {}

    if len(files_list) == 1 and files_list[0] == "-":
        if sys.stdin.isatty():
            context = click.get_current_context()
            click.echo(context.get_help())
            context.exit(0)
        buffer_list.append("stdin")
    else:
        for file_path_argument in files_list:
            if file_path_argument == "-":
                buffer_list.append("stdin")
            else:
                absolute_path = os.path.abspath(file_path_argument)
                if os.path.exists(absolute_path) and os.path.isfile(absolute_path):
                    buffer_list.append(absolute_path)
                else:
                    sys.stderr.write(
                        f"Warning: File '{file_path_argument}' not found.\n"
                    )

        if not buffer_list:
            sys.stderr.write("Error: No valid files to view.\n")
            sys.exit(1)

    current_buffer_index = 0
    current_buffer = buffer_list[current_buffer_index]
    markdown_text = ""
    file_name = "stdin"
    file_path = ""
    file_mtime = 0.0

    navigation_and_history_stack = []

    if current_buffer == "stdin":
        markdown_text = sys.stdin.read()
        buffer_contents["stdin"] = markdown_text
        # Reopen standard input to controlling terminal to allow keyboard interaction
        try:
            sys.stdin = open("/dev/tty")
        except OSError:
            pass
    else:
        file_path = current_buffer
        file_name = os.path.basename(file_path)
        try:
            file_mtime = os.path.getmtime(file_path)
            with open(file_path, "r", encoding="utf-8") as file_pointer:
                markdown_text = file_pointer.read()
            buffer_contents[file_path] = markdown_text
        except FileNotFoundError:
            sys.stderr.write(f"Error: File '{file_path}' not found.\n")
            sys.exit(1)
        except Exception as error:
            sys.stderr.write(f"Error reading '{file_path}': {error}\n")
            sys.exit(1)

    is_interactive = sys.stdout.isatty() and sys.stdin.isatty()

    # If stdin was redirected, try to reopen /dev/tty for interactive keyboard control
    if is_interactive and not sys.stdin.isatty():
        try:
            sys.stdin = open("/dev/tty")
        except OSError:
            is_interactive = False

    if not is_interactive:
        # Non-interactive mode: render to stdout and exit cleanly (like cat/mdcat)
        try:
            terminal_size = os.get_terminal_size()
            width = terminal_size.columns
        except OSError:
            width = 80
        left_margin = 2 if width > 40 else 0
        render_width = width - (left_margin * 2)
        rendered_lines = render_markdown(markdown_text, render_width, theme)
        margin_spaces = " " * left_margin
        for line in rendered_lines:
            sys.stdout.write(f"{margin_spaces}{line}\n")
        sys.stdout.flush()
        sys.exit(0)

    # Store standard terminal attributes for safe restoration
    file_descriptor = sys.stdin.fileno()
    old_settings = termios.tcgetattr(file_descriptor)

    try:
        # Enter alternate screen buffer, hide cursor, and set cbreak mode
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()

        # Set terminal to cbreak mode (disable echo and line buffering, keep output processing)
        mode = termios.tcgetattr(file_descriptor)
        mode[3] = mode[3] & ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, mode)

        terminal_size = os.get_terminal_size()
        current_width = terminal_size.columns
        current_height = terminal_size.lines

        left_margin = 2 if current_width > 40 else 0
        render_width = current_width - (left_margin * 2)
        rendered_lines = render_markdown(markdown_text, render_width, theme)

        scroll_offset = 0
        search_query = None
        search_matches = []
        current_match_index = -1

        document_links = extract_links(rendered_lines)
        selected_link_index = None

        headers = parse_headers(markdown_text)

        raw_mode = False
        raw_lines = None
        show_line_numbers = False

        status_message = None

        while True:
            active_lines = raw_lines if raw_mode else rendered_lines
            draw_screen(
                active_lines,
                scroll_offset,
                current_width,
                current_height,
                file_name,
                search_query,
                search_matches,
                current_match_index,
                document_links,
                None if raw_mode else selected_link_index,
                status_message,
            )
            if watch_flag and file_path:
                # Use short timeout in raw read to run file mtime check every 200ms
                key = read_key(timeout=0.2)

                try:
                    current_mtime = os.path.getmtime(file_path)
                    if current_mtime > file_mtime:
                        file_mtime = current_mtime
                        with open(file_path, "r", encoding="utf-8") as file_pointer:
                            markdown_text = file_pointer.read()

                        headers = parse_headers(markdown_text)
                        left_margin = 2 if current_width > 40 else 0
                        render_width = current_width - (left_margin * 2)
                        rendered_lines = render_markdown(
                            markdown_text, render_width, theme
                        )
                        document_links = extract_links(rendered_lines)

                        if raw_mode:
                            raw_lines = render_raw_markdown(
                                markdown_text,
                                render_width,
                                theme,
                                show_line_numbers,
                            )
                        else:
                            raw_lines = None

                        selected_link_index = None
                        active_lines = raw_lines if raw_mode else rendered_lines

                        if search_query is not None:
                            search_matches = perform_search(active_lines, search_query)
                            if current_match_index >= len(search_matches):
                                current_match_index = len(search_matches) - 1

                        content_height = current_height - 1
                        max_scroll = max(0, len(active_lines) - content_height)
                        scroll_offset = min(scroll_offset, max_scroll)
                        status_message = "File updated. Reloaded."
                except Exception:
                    pass
            else:
                key = read_key()

            if key and status_message:
                status_message = None

            new_size = os.get_terminal_size()
            if new_size.columns != current_width or new_size.lines != current_height:
                current_width = new_size.columns
                current_height = new_size.lines

                left_margin = 2 if current_width > 40 else 0
                render_width = current_width - (left_margin * 2)
                rendered_lines = render_markdown(markdown_text, render_width, theme)
                document_links = extract_links(rendered_lines)

                if raw_mode:
                    raw_lines = render_raw_markdown(
                        markdown_text, render_width, theme, show_line_numbers
                    )
                else:
                    raw_lines = None

                active_lines = raw_lines if raw_mode else rendered_lines
                if search_query is not None:
                    search_matches = perform_search(active_lines, search_query)
                    if current_match_index >= len(search_matches):
                        current_match_index = len(search_matches) - 1

                content_height = current_height - 1
                max_scroll = max(0, len(active_lines) - content_height)
                scroll_offset = min(scroll_offset, max_scroll)

            if not key:
                continue

            active_lines = raw_lines if raw_mode else rendered_lines
            content_height = current_height - 1
            max_scroll = max(0, len(active_lines) - content_height)

            match key:
                case "q" | "\x1b" | "\x03":
                    break
                case "\x1b[A" | "k":
                    scroll_offset = max(0, scroll_offset - 1)
                case "\x1b[B" | "j":
                    scroll_offset = min(max_scroll, scroll_offset + 1)
                case "\x1b[5~" | "b":
                    scroll_offset = max(0, scroll_offset - content_height)
                case "\x1b[6~" | "f" | " ":
                    scroll_offset = min(max_scroll, scroll_offset + content_height)
                case "u" | "\x15":
                    scroll_offset = max(0, scroll_offset - (content_height // 2))
                case "d" | "\x04":
                    scroll_offset = min(
                        max_scroll, scroll_offset + (content_height // 2)
                    )
                case "g":
                    # Wait up to 500ms for a second "g" to make "gg" (Vim style)
                    next_key = read_key(timeout=0.5)
                    if next_key == "g":
                        scroll_offset = 0
                case "\x1b[F" | "G" | "\x1b[4~" | "\x1bOF":
                    scroll_offset = max_scroll
                case "?":
                    draw_help(current_width, current_height)
                    read_key()
                    try:
                        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                    except Exception:
                        pass
                case "r":
                    raw_mode = not raw_mode
                    if raw_mode and raw_lines is None:
                        raw_lines = render_raw_markdown(
                            markdown_text, render_width, theme, show_line_numbers
                        )

                    if raw_mode:
                        selected_link_index = None

                    active_lines = raw_lines if raw_mode else rendered_lines
                    if search_query is not None:
                        search_matches = perform_search(active_lines, search_query)
                        current_match_index = -1

                    max_scroll = max(0, len(active_lines) - content_height)
                    scroll_offset = min(scroll_offset, max_scroll)
                case "h" | "\x1b[D" if len(buffer_list) > 1:
                    state_key = file_path if file_path else "stdin"
                    buffer_states[state_key] = (scroll_offset, selected_link_index)

                    current_buffer_index = (current_buffer_index - 1) % len(buffer_list)
                    current_buffer = buffer_list[current_buffer_index]

                    if current_buffer == "stdin":
                        file_path = ""
                        file_name = "stdin"
                        file_mtime = 0.0
                        markdown_text = buffer_contents["stdin"]
                    else:
                        file_path = current_buffer
                        file_name = os.path.basename(file_path)
                        try:
                            file_mtime = os.path.getmtime(file_path)
                            with open(file_path, "r", encoding="utf-8") as file_pointer:
                                markdown_text = file_pointer.read()
                            buffer_contents[file_path] = markdown_text
                        except Exception as error:
                            markdown_text = f"Error reading file: {error}"
                            file_mtime = 0.0

                    headers = parse_headers(markdown_text)
                    left_margin = 2 if current_width > 40 else 0
                    render_width = current_width - (left_margin * 2)
                    rendered_lines = render_markdown(markdown_text, render_width, theme)
                    document_links = extract_links(rendered_lines)

                    if raw_mode:
                        raw_lines = render_raw_markdown(
                            markdown_text, render_width, theme, show_line_numbers
                        )
                    else:
                        raw_lines = None

                    active_lines = raw_lines if raw_mode else rendered_lines
                    if search_query is not None:
                        search_matches = perform_search(active_lines, search_query)
                        current_match_index = -1

                    max_scroll = max(0, len(active_lines) - content_height)

                    state_key = file_path if file_path else "stdin"
                    if state_key in buffer_states:
                        scroll_offset, selected_link_index = buffer_states[state_key]
                    else:
                        scroll_offset = 0
                        selected_link_index = None
                case "l" | "\x1b[C" if len(buffer_list) > 1:
                    state_key = file_path if file_path else "stdin"
                    buffer_states[state_key] = (scroll_offset, selected_link_index)

                    current_buffer_index = (current_buffer_index + 1) % len(buffer_list)
                    current_buffer = buffer_list[current_buffer_index]

                    if current_buffer == "stdin":
                        file_path = ""
                        file_name = "stdin"
                        file_mtime = 0.0
                        markdown_text = buffer_contents["stdin"]
                    else:
                        file_path = current_buffer
                        file_name = os.path.basename(file_path)
                        try:
                            file_mtime = os.path.getmtime(file_path)
                            with open(file_path, "r", encoding="utf-8") as file_pointer:
                                markdown_text = file_pointer.read()
                            buffer_contents[file_path] = markdown_text
                        except Exception as error:
                            markdown_text = f"Error reading file: {error}"
                            file_mtime = 0.0

                    headers = parse_headers(markdown_text)
                    left_margin = 2 if current_width > 40 else 0
                    render_width = current_width - (left_margin * 2)
                    rendered_lines = render_markdown(markdown_text, render_width, theme)
                    document_links = extract_links(rendered_lines)

                    if raw_mode:
                        raw_lines = render_raw_markdown(
                            markdown_text, render_width, theme, show_line_numbers
                        )
                    else:
                        raw_lines = None

                    active_lines = raw_lines if raw_mode else rendered_lines
                    if search_query is not None:
                        search_matches = perform_search(active_lines, search_query)
                        current_match_index = -1

                    max_scroll = max(0, len(active_lines) - content_height)

                    state_key = file_path if file_path else "stdin"
                    if state_key in buffer_states:
                        scroll_offset, selected_link_index = buffer_states[state_key]
                    else:
                        scroll_offset = 0
                        selected_link_index = None
                case "L":
                    if raw_mode:
                        show_line_numbers = not show_line_numbers
                        raw_lines = render_raw_markdown(
                            markdown_text, render_width, theme, show_line_numbers
                        )
                        active_lines = raw_lines if raw_mode else rendered_lines
                        max_scroll = max(0, len(active_lines) - content_height)
                        scroll_offset = min(scroll_offset, max_scroll)
                    else:
                        status_message = (
                            "Line numbers are only supported in raw view (r)."
                        )
                case "o":
                    jump_line = show_outline(
                        headers, active_lines, current_width, current_height
                    )
                    # Flush stdin buffer to discard extra Enter keypresses
                    try:
                        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                    except Exception:
                        pass

                    if jump_line is not None:
                        scroll_offset = min(max_scroll, jump_line)
                case "\t" if len(document_links) > 0:
                    if selected_link_index is None:
                        selected_link_index = 0
                        for index, link in enumerate(document_links):
                            if link["line_index"] >= scroll_offset:
                                selected_link_index = index
                                break
                    else:
                        selected_link_index = (selected_link_index + 1) % len(
                            document_links
                        )

                    link = document_links[selected_link_index]
                    if (
                        link["line_index"] < scroll_offset
                        or link["line_index"] >= scroll_offset + content_height
                    ):
                        scroll_offset = min(
                            max_scroll,
                            max(0, link["line_index"] - (content_height // 2)),
                        )
                case "\x1b[Z" if len(document_links) > 0:
                    if selected_link_index is None:
                        selected_link_index = len(document_links) - 1
                        for index, link in enumerate(reversed(document_links)):
                            if link["line_index"] < scroll_offset + content_height:
                                selected_link_index = len(document_links) - 1 - index
                                break
                    else:
                        selected_link_index = (selected_link_index - 1) % len(
                            document_links
                        )

                    link = document_links[selected_link_index]
                    if (
                        link["line_index"] < scroll_offset
                        or link["line_index"] >= scroll_offset + content_height
                    ):
                        scroll_offset = min(
                            max_scroll,
                            max(0, link["line_index"] - (content_height // 2)),
                        )
                case "\r" | "\n" if selected_link_index is not None:
                    link = document_links[selected_link_index]
                    url = link["url"]

                    if url.startswith(("http://", "https://")):
                        if open_url(url):
                            status_message = f"Opened URL: {url}"
                        else:
                            status_message = f"Error opening URL: {url}"
                    else:
                        url_parts = url.split("#", 1)
                        file_part = url_parts[0]
                        anchor_part = url_parts[1] if len(url_parts) > 1 else None

                        if not file_part:
                            if anchor_part:
                                target_title = anchor_part.replace("-", " ")
                                target_line = find_header_rendered_line(
                                    rendered_lines, target_title
                                )
                                scroll_offset = min(max_scroll, target_line)
                                selected_link_index = None
                        else:
                            if not file_path:
                                status_message = (
                                    "Cannot navigate relative links from stdin."
                                )
                            else:
                                current_dir = os.path.dirname(file_path)
                                decoded_file_part = urllib.parse.unquote(file_part)
                                target_path = os.path.normpath(
                                    os.path.join(current_dir, decoded_file_part)
                                )

                                if os.path.exists(target_path) and os.path.isfile(
                                    target_path
                                ):
                                    if target_path not in buffer_list:
                                        state_key = file_path if file_path else "stdin"
                                        buffer_states[state_key] = (
                                            scroll_offset,
                                            selected_link_index,
                                        )
                                        buffer_list.append(target_path)
                                        current_buffer_index = len(buffer_list) - 1
                                    else:
                                        state_key = file_path if file_path else "stdin"
                                        buffer_states[state_key] = (
                                            scroll_offset,
                                            selected_link_index,
                                        )
                                        current_buffer_index = buffer_list.index(
                                            target_path
                                        )

                                    navigation_and_history_stack.append(
                                        (file_path, scroll_offset, selected_link_index)
                                    )

                                    file_path = target_path
                                    file_name = os.path.basename(file_path)
                                    file_mtime = os.path.getmtime(file_path)

                                    with open(
                                        file_path, "r", encoding="utf-8"
                                    ) as file_pointer:
                                        markdown_text = file_pointer.read()

                                    headers = parse_headers(markdown_text)
                                    left_margin = 2 if current_width > 40 else 0
                                    render_width = current_width - (left_margin * 2)
                                    rendered_lines = render_markdown(
                                        markdown_text, render_width, theme
                                    )
                                    document_links = extract_links(rendered_lines)

                                    if raw_mode:
                                        raw_lines = render_raw_markdown(
                                            markdown_text,
                                            render_width,
                                            theme,
                                            show_line_numbers,
                                        )
                                    else:
                                        raw_lines = None

                                    scroll_offset = 0
                                    selected_link_index = None

                                    if anchor_part:
                                        target_title = anchor_part.replace("-", " ")
                                        target_line = find_header_rendered_line(
                                            rendered_lines, target_title
                                        )
                                        scroll_offset = min(max_scroll, target_line)
                                else:
                                    status_message = (
                                        f"Link Target Not Found: {decoded_file_part}"
                                    )
                case "\x7f" | "\x08" | "\x0f" if len(navigation_and_history_stack) > 0:
                    previous_file_path, previous_scroll, previous_link = (
                        navigation_and_history_stack.pop()
                    )
                    if previous_file_path in buffer_list:
                        current_buffer_index = buffer_list.index(previous_file_path)

                    file_path = previous_file_path
                    file_name = os.path.basename(file_path)
                    file_mtime = os.path.getmtime(file_path)

                    with open(file_path, "r", encoding="utf-8") as file_pointer:
                        markdown_text = file_pointer.read()

                    headers = parse_headers(markdown_text)
                    left_margin = 2 if current_width > 40 else 0
                    render_width = current_width - (left_margin * 2)
                    rendered_lines = render_markdown(markdown_text, render_width, theme)
                    document_links = extract_links(rendered_lines)

                    if raw_mode:
                        raw_lines = render_raw_markdown(
                            markdown_text, render_width, theme, show_line_numbers
                        )
                    else:
                        raw_lines = None

                    scroll_offset = previous_scroll
                    selected_link_index = previous_link
                case "/":
                    new_query = read_search_query()
                    if new_query is not None:
                        search_query = new_query
                        search_matches = perform_search(active_lines, search_query)
                        current_match_index = -1

                        if len(search_matches) > 0:
                            for index, match_line in enumerate(search_matches):
                                if match_line >= scroll_offset:
                                    current_match_index = index
                                    break
                            if current_match_index == -1:
                                current_match_index = 0
                            scroll_offset = search_matches[current_match_index]
                case "n" if len(search_matches) > 0:
                    current_match_index = (current_match_index + 1) % len(
                        search_matches
                    )
                    scroll_offset = search_matches[current_match_index]
                case "N" if len(search_matches) > 0:
                    current_match_index = (current_match_index - 1) % len(
                        search_matches
                    )
                    scroll_offset = search_matches[current_match_index]

    finally:
        # Restore normal terminal settings, main screen buffer, and show cursor
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\x1b[?1049l\x1b[?25h")
        sys.stdout.flush()


def parse_headers(markdown_text: str) -> list[dict]:
    """Parse headings from markdown text, returning level, title, and raw line number."""
    headers = []
    inside_code_block = False
    for line_index, line in enumerate(markdown_text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("```"):
            inside_code_block = not inside_code_block
            continue
        if inside_code_block:
            continue

        # Match headings of format: # Title
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            headers.append(
                {"level": level, "title": title, "raw_line_index": line_index}
            )
    return headers


class IndentedCodeBlock(rich.markdown.CodeBlock):
    """Code block whose content is indented four spaces instead of rich's one space."""

    def __rich_console__(
        self, console: rich.console.Console, options: rich.console.ConsoleOptions
    ) -> rich.console.RenderResult:
        code_text = str(self.text).rstrip()
        # padding=(top, right, bottom, left): only the left inset grows, to 4
        syntax_element = rich.syntax.Syntax(
            code_text,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=(1, 1, 1, 4),
        )
        yield syntax_element


class IndentedListItem(rich.markdown.ListItem):
    """List item whose bullet/number is indented two spaces instead of rich's one space."""

    def render_bullet(
        self, console: rich.console.Console, options: rich.console.ConsoleOptions
    ) -> rich.console.RenderResult:
        marker = "  • "
        render_options = options.update(width=options.max_width - len(marker))
        lines = console.render_lines(self.elements, render_options, style=self.style)
        bullet_style = console.get_style("markdown.item.bullet", default="none")
        bullet = rich.segment.Segment(marker, bullet_style)
        padding = rich.segment.Segment(" " * len(marker), bullet_style)
        new_line = rich.segment.Segment("\n")
        for index, line in enumerate(lines):
            yield bullet if index == 0 else padding
            yield from line
            yield new_line

    def render_number(
        self,
        console: rich.console.Console,
        options: rich.console.ConsoleOptions,
        number: int,
        last_number: int,
    ) -> rich.console.RenderResult:
        # +3 (vs rich's default +2) puts two leading spaces before the number
        number_width = len(str(last_number)) + 3
        render_options = options.update(width=options.max_width - number_width)
        lines = console.render_lines(self.elements, render_options, style=self.style)
        number_style = console.get_style("markdown.item.number", default="none")
        new_line = rich.segment.Segment("\n")
        padding = rich.segment.Segment(" " * number_width, number_style)
        numeral = rich.segment.Segment(
            f"{number}".rjust(number_width - 1) + " ", number_style
        )
        for index, line in enumerate(lines):
            yield numeral if index == 0 else padding
            yield from line
            yield new_line


class IndentedMarkdown(rich.markdown.Markdown):
    """Markdown renderer that indents code blocks and list items further than rich's defaults."""

    elements = {
        **rich.markdown.Markdown.elements,
        "fence": IndentedCodeBlock,
        "code_block": IndentedCodeBlock,
        "list_item_open": IndentedListItem,
    }


def render_markdown(markdown_text: str, width: int, theme: str) -> list[str]:
    """Render markdown to text with ANSI escape codes for the specified width."""
    console = rich.console.Console(width=width, force_terminal=True)
    markdown_element = IndentedMarkdown(markdown_text, code_theme=theme)
    with console.capture() as capture:
        console.print(markdown_element)

    rendered_output = capture.get()
    # Trailing blank line so content isn't flush against the terminal bottom
    return rendered_output.splitlines() + [""]


def render_raw_markdown(
    markdown_text: str, width: int, theme: str, line_numbers: bool = False
) -> list[str]:
    """Render raw markdown with syntax highlighting, wrapping, and a fixed gutter.

    The gutter is sized to the digit count of the last line (Neovim's numberwidth
    approach) and is always reserved, so toggling line numbers never shifts the
    text. When line_numbers is False the numbers are blank but the separator stays.
    """
    syntax_element = rich.syntax.Syntax(
        markdown_text,
        lexer="markdown",
        theme=theme,
        background_color="default",
    )
    # Highlight the whole doc once (keeps lexer context across code fences),
    # then split into per-source-line styled text
    highlighted_text = syntax_element.highlight(markdown_text)
    highlighted_text.rstrip()
    source_lines = highlighted_text.split("\n")

    number_width = len(str(len(source_lines)))
    # Gutter "<number> │ ": only the separator is dimmed, so toggling the numbers
    # stays visible while the width (and therefore the text column) never changes
    separator = " \x1b[90m│\x1b[0m "
    gutter_width = number_width + 3
    content_width = max(1, width - gutter_width)

    console = rich.console.Console(width=content_width, force_terminal=True)
    rendered_lines = []
    for line_number, line_text in enumerate(source_lines, start=1):
        with console.capture() as capture:
            console.print(line_text, end="")
        # Only the first wrapped row carries the number; continuation rows keep
        # a blank gutter so the text column stays aligned
        wrapped_rows = capture.get().split("\n")
        for row_index, wrapped_row in enumerate(wrapped_rows):
            label = str(line_number) if line_numbers and row_index == 0 else ""
            gutter = f"{label:>{number_width}}{separator}"
            rendered_lines.append(f"{gutter}{wrapped_row}")

    # Trailing blank line so content isn't flush against the terminal bottom
    rendered_lines.append("")
    return rendered_lines


def extract_links(rendered_lines: list[str]) -> list[dict]:
    """Scan for OSC 8 hyperlinks in the rendered lines and return their details."""
    link_pattern = re.compile(r"(\x1b]8;id=[^;]*;([^\x1b]+)\x1b\\.*?\x1b]8;;\x1b\\)")
    links = []
    for line_index, line in enumerate(rendered_lines):
        for match in link_pattern.finditer(line):
            links.append(
                {
                    "url": match.group(2),
                    "line_index": line_index,
                    "full_match": match.group(1),
                }
            )
    return links


def find_header_rendered_line(rendered_lines: list[str], title: str) -> int:
    """Find the rendered line index containing the header title (precise match)."""
    # Box-drawing characters used by rich for headers and margins
    box_chars = " ┃━─┏┓┗┛│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬"

    # Try exact match first (after stripping box characters, hashes, and spaces)
    for index, line in enumerate(rendered_lines):
        plain_line = strip_ansi_codes(line)
        cleaned_line = plain_line.strip(box_chars + "#")
        if cleaned_line.lower() == title.lower():
            return index

    # Fallback to substring match if exact stripped match isn't found
    for index, line in enumerate(rendered_lines):
        plain_line = strip_ansi_codes(line)
        if title.lower() in plain_line.lower():
            return index

    return 0


def perform_search(lines: list[str], query: str) -> list[int]:
    """Search for the query across all rendered lines, returning line indices."""
    matches = []
    for index, line in enumerate(lines):
        plain_line = strip_ansi_codes(line)
        if query.lower() in plain_line.lower():
            matches.append(index)
    return matches


def draw_screen(
    rendered_lines: list[str],
    scroll_offset: int,
    terminal_width: int,
    terminal_height: int,
    file_name: str,
    search_query: str | None,
    search_matches: list[int],
    current_match_index: int,
    document_links: list[dict],
    selected_link_index: int | None,
    status_message: str | None = None,
) -> None:
    """Draw the current visible page of markdown, active link highlights, and the status bar."""
    # Move cursor to home and clear screen
    sys.stdout.write("\x1b[H\x1b[2J")

    content_height = terminal_height - 1
    left_margin = 2 if terminal_width > 40 else 0
    margin_spaces = " " * left_margin

    # Get copy of visible lines slice to safely apply temporary highlights
    visible_lines = list(rendered_lines[scroll_offset : scroll_offset + content_height])

    # Apply active selection highlight to link if visible
    if selected_link_index is not None and selected_link_index < len(document_links):
        link = document_links[selected_link_index]
        relative_line_index = link["line_index"] - scroll_offset
        if 0 <= relative_line_index < len(visible_lines):
            line = visible_lines[relative_line_index]
            escaped_pattern = re.escape(link["full_match"])
            pattern = re.compile(escaped_pattern)
            visible_lines[relative_line_index] = pattern.sub(
                lambda m: f"\x1b[7m{m.group(0)}\x1b[27m", line
            )

    for line in visible_lines:
        sys.stdout.write(f"{margin_spaces}{line}\n")

    for _ in range(content_height - len(visible_lines)):
        sys.stdout.write("\n")

    sys.stdout.write(f"\x1b[{terminal_height};1H\x1b[2K")

    total_lines = len(rendered_lines)
    if total_lines > 0:
        percent = int((scroll_offset + len(visible_lines)) / total_lines * 100)
        percent_str = f"{percent}%"
        if scroll_offset == 0:
            percent_str = "Top"
        elif scroll_offset + content_height >= total_lines:
            percent_str = "Bot"
    else:
        percent_str = "0%"

    search_info = ""
    if search_query is not None:
        if len(search_matches) > 0:
            search_info = f" | Search: '{search_query}' ({current_match_index + 1}/{len(search_matches)})"
        else:
            search_info = f" | Search: '{search_query}' (no match)"

    link_info = ""
    if selected_link_index is not None and selected_link_index < len(document_links):
        link = document_links[selected_link_index]
        link_info = f" | Link: {link['url']}"

    status_left = f" {file_name} | Lines {scroll_offset + 1}-{min(total_lines, scroll_offset + content_height)}/{total_lines}{search_info}{link_info}"

    if status_message:
        status_left = f" {status_message}"

    status_right = f"{percent_str} | [q:Quit] [?:Help] "

    available_width = terminal_width - len(status_right) - 2
    if len(status_left) > available_width:
        status_left = status_left[: available_width - 3] + "..."

    # Format status line with inverted colors
    padding_length = terminal_width - len(status_left) - len(status_right)
    status_line = f"\x1b[7m{status_left}{' ' * padding_length}{status_right}\x1b[0m"
    sys.stdout.write(status_line)
    sys.stdout.flush()


def show_outline(
    headers: list[dict],
    rendered_lines: list[str],
    terminal_width: int,
    terminal_height: int,
) -> int | None:
    """Show an overlay menu of headings and let the user select one to jump to."""
    if not headers:
        return None

    selected_header_index = 0
    outline_scroll = 0
    menu_height = terminal_height - 4

    while True:
        sys.stdout.write("\x1b[H\x1b[2J")
        sys.stdout.write(
            "  Document Outline (Use j/k or Arrows, Enter to jump, q/Esc to close)\n"
        )
        sys.stdout.write("  " + "=" * (terminal_width - 4) + "\n\n")

        visible_headers = headers[outline_scroll : outline_scroll + menu_height]

        for index, header in enumerate(visible_headers):
            global_index = outline_scroll + index
            indent = "  " * (header["level"] - 1)
            bullet = (
                "•" if header["level"] == 1 else "◦" if header["level"] == 2 else "▪"
            )
            prefix = f"{indent}{bullet} "

            line_text = f"  {prefix}{header['title']}"
            if len(line_text) > terminal_width - 4:
                line_text = line_text[: terminal_width - 7] + "..."

            if global_index == selected_header_index:
                sys.stdout.write(
                    f"\x1b[7m{line_text.ljust(terminal_width - 2)}\x1b[0m\n"
                )
            else:
                sys.stdout.write(f"{line_text}\n")

        for _ in range(menu_height - len(visible_headers) + 1):
            sys.stdout.write("\n")

        sys.stdout.write(f"\x1b[{terminal_height};1H\x1b[2K")
        sys.stdout.write(
            f"\x1b[7m Selection: {selected_header_index + 1}/{len(headers)} \x1b[0m"
        )
        sys.stdout.flush()

        key = read_key()
        if not key:
            continue

        if key in ("q", "\x1b"):
            return None
        elif key in ("\r", "\n"):
            selected_header = headers[selected_header_index]
            return find_header_rendered_line(rendered_lines, selected_header["title"])
        elif key in ("\x1b[A", "k"):
            if selected_header_index > 0:
                selected_header_index -= 1
                if selected_header_index < outline_scroll:
                    outline_scroll = selected_header_index
        elif key in ("\x1b[B", "j"):
            if selected_header_index < len(headers) - 1:
                selected_header_index += 1
                if selected_header_index >= outline_scroll + menu_height:
                    outline_scroll = selected_header_index - menu_height + 1


def draw_help(terminal_width: int, terminal_height: int) -> None:
    """Display the help screen listing available controls."""
    sys.stdout.write("\x1b[H\x1b[2J")

    help_lines = [
        "md - Help",
        "",
        "Controls:",
        "  j, Arrow Down      Scroll down by 1 line",
        "  k, Arrow Up        Scroll up by 1 line",
        "  d, Ctrl+D          Scroll down by half a page",
        "  u, Ctrl+U          Scroll up by half a page",
        "  Space, Page Down   Scroll down by 1 page",
        "  b, Page Up         Scroll up by 1 page",
        "  gg, Home           Go to top of document",
        "  G, End             Go to bottom of document",
        "",
        "Navigation & Document Wiki Mode:",
        "  Tab                Cycle focus to next hyperlink",
        "  Shift+Tab          Cycle focus to previous hyperlink",
        "  Enter              Follow hyperlink (Web URLs open in browser;",
        "                     Local Markdown files open directly inside viewer)",
        "  Backspace, Ctrl+O  Go back in link history",
        "  h, l, Left/Right   Switch between open files/buffers",
        "",
        "Search & Outline:",
        "  /                  Search for a query",
        "  n                  Go to next match",
        "  N                  Go to previous match",
        "  o                  Toggle Table of Contents Outline overlay",
        "",
        "Other:",
        "  r                  Toggle syntax-highlighted raw markdown view",
        "  L                  Toggle line numbers in raw view",
        "  ?                  Show this help screen",
        "  q, Esc, Ctrl+C     Quit",
        "",
        "Press any key to return to document...",
    ]

    # Center the help screen vertically and horizontally
    start_row = max(1, (terminal_height - len(help_lines)) // 2)
    left_padding = max(0, (terminal_width - 65) // 2)
    padding_spaces = " " * left_padding

    for index in range(start_row):
        sys.stdout.write("\n")

    for line in help_lines:
        sys.stdout.write(f"{padding_spaces}{line}\n")

    for _ in range(terminal_height - start_row - len(help_lines) - 1):
        sys.stdout.write("\n")

    sys.stdout.write(f"\x1b[{terminal_height};1H\x1b[2K")
    sys.stdout.write("\x1b[7m Press any key to return... \x1b[0m")
    sys.stdout.flush()


def read_key(timeout: float | None = None) -> str:
    """Read a keypress from the terminal (assumes raw mode is already set)."""
    try:
        file_descriptor = sys.stdin.fileno()
        read_list, _, _ = select.select([file_descriptor], [], [], timeout)
        if read_list:
            char_bytes = os.read(file_descriptor, 1)
            if not char_bytes:
                return ""
            char = char_bytes.decode("utf-8", errors="ignore")
            if char == "\x1b":
                sequence = ""
                while True:
                    read_list_esc, _, _ = select.select([file_descriptor], [], [], 0.02)
                    if read_list_esc:
                        next_char_bytes = os.read(file_descriptor, 1)
                        if next_char_bytes:
                            next_char = next_char_bytes.decode("utf-8", errors="ignore")
                            sequence += next_char
                            if next_char.isalpha() or next_char == "~":
                                break
                        else:
                            break
                    else:
                        break
                char += sequence
            return char
    except OSError:
        # Catch signal interrupts like terminal resize (SIGWINCH)
        return ""
    return ""


def read_search_query() -> str | None:
    """Read search query character by character at the bottom of the screen."""
    file_descriptor = sys.stdin.fileno()
    old_settings = termios.tcgetattr(file_descriptor)
    query_string = ""

    # Temporarily show cursor
    sys.stdout.write("\x1b[?25h")
    sys.stdout.flush()

    try:
        while True:
            terminal_size = os.get_terminal_size()
            terminal_height = terminal_size.lines

            # Position cursor on the last line to prompt for search
            sys.stdout.write(f"\x1b[{terminal_height};1H")
            sys.stdout.write("\x1b[2K")
            sys.stdout.write(f"/{query_string}")
            sys.stdout.flush()

            key = read_key()
            if key in ("\r", "\n"):
                break
            elif key in ("\x7f", "\x08"):
                if len(query_string) > 0:
                    query_string = query_string[:-1]
            elif key == "\x1b":
                return None
            elif len(key) == 1 and ord(key) >= 32:
                query_string += key
    finally:
        # Hide cursor again
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()

    return query_string


def open_url(url: str) -> bool:
    """Open a URL in the default browser using system commands with fallback."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["open", url],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        elif sys.platform.startswith("linux"):
            subprocess.run(
                ["xdg-open", url],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
    except Exception:
        pass

    try:
        webbrowser.open(url)
        return True
    except Exception:
        pass

    return False


def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape sequences from a string to get plain text."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


if __name__ == "__main__":
    main()
