# Contributing

Report bugs and suggest improvements in
[GitHub Issues](https://github.com/UpsideDownDisk/DiscSteward/issues).
Include your Windows/Python version, disc type, steps to reproduce, and the
exact error. Remove personal paths from logs; do not attach videos, downloaded
subtitles, credentials or disc contents.

## Local checks

Use Windows with Python 3.10 or newer, including Tkinter. The regression suite
uses temporary dummy files and mocked external tools; it does not need a disc,
MakeMKV, FFmpeg, an online account or downloaded subtitles.

From the project root, in PowerShell:

```powershell
$env:PYTHONPATH = 'src'
py -3 -m unittest discover -s tests -v
py -3 -m compileall -q src discsteward-ui.py scripts
```

For pytest and package builds, use a virtual environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m build
```

If your environment restricts the standard temporary folder, create a dedicated
test directory and set `DISCSTEWARD_TEST_TEMP` to its absolute path. Tests remove
only their own generated child directories.

Keep automatic identity evidence separate from manual choices. Never infer an
episode number from a playlist number alone. File operations must preserve
existing destinations and record successful changes accurately. Add regression
coverage for changes to parsers, matching decisions or file operations.

Contributions are accepted under the repository's MIT licence. Preserve any
third-party attribution when introducing external code or assets.
