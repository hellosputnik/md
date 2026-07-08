# md

A clean, simple terminal markdown viewer with a pager, search, syntax highlighting, and Vim-like controls.

## Requirements

- [uv](https://docs.astral.sh/uv/)

## Installation

Install from GitHub:

```bash
uv tool install git+https://github.com/hellosputnik/md.git
```

Install from source:

```bash
git clone https://github.com/hellosputnik/md.git && cd md
uv tool install .
```

Run without installation:

```bash
uvx --from git+https://github.com/hellosputnik/md.git md README.md
```

## Usage

```bash
# View a single file
md README.md

# View multiple files in buffers
md README.md index.md

# Watch a file and reload dynamically
md -w README.md
```

## Keybindings

- `j` / `k` or Down / Up Arrows: Scroll down / up by 1 line.
- `d` / `u` or Ctrl+D / Ctrl+U: Scroll down / up by half a page.
- `Space` / `b` or Page Down / Page Up: Scroll down / up by 1 page.
- `gg` / `G`: Jump to top / bottom.
- `h` / `l` or Left / Right Arrows: Switch between open file buffers.
- `/`: Search for text (`n` / `N` for next / previous match).
- `o`: Toggle Table of Contents outline.
- `r`: Toggle raw markdown source view.
- `L`: Toggle line numbers in raw view.
- `?`: Show help screen.
- `q`: Quit.
