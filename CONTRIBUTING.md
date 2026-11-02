# Contributing

Small fixes, reproducible bug reports, and documentation corrections are welcome.
For larger changes, open an issue first to agree on the scope.

## Development setup

Install Python 3.11–3.15 and FFmpeg, then run from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip check
python -m unittest discover -s tests -v
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.
Tests mock remote services and generate synthetic audio locally with FFmpeg.
Backend and audio tests can be skipped if dependencies or FFmpeg are missing;
a full validation must run them without skips.

## Before opening a pull request

- Keep changes focused and preserve the `ytscloud` entry point.
- Add a regression test for changed behavior. Do not require credentials, a network
  connection, or copyrighted audio fixtures to run tests.
- Preserve existing files, record the actual audio source, and reject ambiguous
  matches in non-interactive mode.
- Update the README when commands, requirements, or JSON output change.
- Describe the problem, the resulting behavior, the checks run, and known limits.

To check the distributable package as CI does:

```bash
python -m pip install build
python -m build
```

## Reporting a bug

Use [GitHub Issues](https://github.com/nachopalomares/music-ytscloud-downloader/issues)
and include your operating system, Python and FFmpeg versions, the command used,
the expected result, and the actual error. Include dependency versions from
`python -m pip show ytscloud spotdl yt-dlp ytmusicapi` when relevant.
Remove credentials, private paths, account information, and private URLs first.
Share only audio or links you have permission to redistribute.

For suspected security issues, follow [SECURITY.md](SECURITY.md).
