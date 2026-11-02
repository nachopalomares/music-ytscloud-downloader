# ytscloud

[![CI](https://github.com/nachopalomares/music-ytscloud-downloader/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nachopalomares/music-ytscloud-downloader/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A local Python CLI for turning tracklists and supported music links into MP3 or
WAV files. Search YouTube Music or SoundCloud, review uncertain matches in your
terminal, and export JSON Lines for scripts.

**Experimental, version 0.1.0.** Service changes, rate limits, region restrictions,
and unavailable uploads can prevent downloads. Terminal messages are currently
in Spanish. The repository is `music-ytscloud-downloader`; the Python package and
primary command are `ytscloud`.

Use only content you own or are authorized to download. Spotify provides
metadata; audio comes from an alternative source, never directly from Spotify.
WAV conversion does not improve the quality of compressed source audio. The
tool does not bypass DRM or access controls.

## Install

Requirements:

- Python 3.11–3.15.
- FFmpeg and ffprobe on your `PATH` for conversion and audio validation.
- A supported JavaScript runtime for YouTube downloads. Deno is recommended;
  Node.js is also enabled by the application. Check the current requirements in
  [yt-dlp's EJS setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

On macOS with Homebrew:

```bash
brew install python ffmpeg deno
```

On Debian or Ubuntu, install the base tools, then install a supported runtime
using the EJS guide above. Distribution packages for Node.js may be too old.

```bash
sudo apt update
sudo apt install ffmpeg python3 python3-venv python3-pip git
```

Install from this repository in a virtual environment:

```bash
git clone https://github.com/nachopalomares/music-ytscloud-downloader.git
cd music-ytscloud-downloader
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
ytscloud --help
```

On Windows, install Python, FFmpeg, and a supported runtime, then use
`py -m venv .venv` and `.venv\Scripts\Activate.ps1` in PowerShell before the pip
command. Windows is not currently covered by CI.

The installation above uses the repository source; it does not assume a PyPI
release exists. To update, pull the latest changes and rerun `python -m pip install .`
in the same environment.

## Usage

The following artist names and URLs are placeholders. Replace them with your
own or authorized content before running a download.

```bash
# Search YouTube Music, with SoundCloud fallback if no reliable match is found.
ytscloud 'Your Artist - Original Track' --folder 'My Set'

# Search SoundCloud directly. Keep the asterisk inside quotes.
ytscloud '* Your Artist - Original Track (My Edit)' --folder 'My Set'

# Download a particular public SoundCloud upload.
ytscloud 'https://soundcloud.com/your-artist/your-authorized-track' --folder 'My Set'

# Choose WAV output. MP3 is the default.
ytscloud --file set.txt --folder 'My Set' --format wav
```

Supported input:

| Input | Behavior |
| --- | --- |
| `Artist - Title` | Search YouTube Music; try SoundCloud if matching fails. |
| `* Artist - Title` | Search SoundCloud. |
| YouTube video, `youtu.be`, or Shorts URL | Download that video without a music search. |
| `https://open.spotify.com/track/…` | Resolve metadata and match audio on YouTube Music. |
| `https://open.spotify.com/playlist/…` | Process public playlist tracks individually. |
| `https://soundcloud.com/artist/track` | Download the public track. |
| `https://soundcloud.com/artist/sets/name` | Process a public set. |

Only HTTPS links on the supported domains are accepted. YouTube playlists,
Spotify albums, shortened Spotify/SoundCloud links, private content, live streams,
and upcoming premieres are not supported. Spotify inputs do not fall back to
SoundCloud.

### Tracklists

Save a UTF-8 text file with one entry per line. Blank lines are ignored; leading
numbers such as `01.` or `02)` are removed. Timestamps and comment lines are not
supported. [examples/set.txt](examples/set.txt) is a template with placeholders.

```text
01. Your Artist - Original Track
02.* Your Artist - Original Track (My Edit)
03. https://soundcloud.com/your-artist/your-authorized-track
```

Edit the template before using it:

```bash
cp examples/set.txt set.txt
# Replace the placeholders in set.txt with your authorized tracks.
ytscloud --file set.txt --folder 'My Set'
cat set.txt | ytscloud --file - --folder 'My Set' --json
```

### Matching and manual selection

Automatic searches check artist, title, and recognized version labels. Spotify
matching also checks duration and spotDL's verification and match score.
These are metadata checks, not audio fingerprinting or proof of recording identity.
Two uploads with identical titles and durations can still be different recordings.

In a terminal, uncertain search results can be reviewed with up to seven choices:

| Key | Action |
| --- | --- |
| `1`–`7` | Select the displayed candidate. |
| Enter | Select the first displayed candidate. |
| `0` | Skip the track. |
| `s` | Search SoundCloud from a YouTube Music query menu. |

A manual choice can accept a version that failed automatic checks; verify the
displayed title and source. Such choices are recorded as `"selection": "manual"`.

`--yes`, `--json`, non-interactive stdin, and `--file -` disable prompts.
Ambiguous matches are rejected in those modes. For text searches, the tool may
then try SoundCloud; it still requires a reliable, unambiguous match. Use an
explicit track URL when you need a particular upload.

## Automation

```bash
ytscloud --file set.txt --folder 'My Set' --json > results.jsonl
```

`stdout` contains JSON Lines; provider diagnostics go to `stderr`. Each processed
track emits `downloaded`, `skipped`, or `error`. An illustrative success record
(additional metadata fields are included in actual output):

```json
{"status":"downloaded","platform":"soundcloud","id":"123","requested_input":"Your Artist - Original Track","track_url":"https://soundcloud.com/your-artist/original-track","source_url":"https://soundcloud.com/your-artist/original-track","path":"/music/My Set/Your Artist - Original Track.mp3","format":"mp3"}
```

A completed batch ends with a summary:

```json
{"status":"summary","downloaded":1,"skipped":0,"errors":0,"failed_tracks":[]}
```

Setup failures return an error record without a summary. Argument-parser errors
use argparse's text output on `stderr`. Interrupted runs may have partial results
and no summary. Consumers should check both the exit code and emitted records.

| Exit code | Meaning |
| --- | --- |
| `0` | Completed successfully, including cached downloads or intentional skips. |
| `1` | At least one input or track failed; other items are still processed. |
| `2` | Invalid arguments, input-file or output-directory error, or missing FFmpeg. |
| `130` | Interrupted with Ctrl+C. |

JSON output is available for custom integrations. **No Piloto DJ integration has
been implemented or verified**; its contract must be confirmed separately.

## Files and duplicate handling

The default output directory is `downloads/`. `--folder` and `--output` are aliases.

```text
My Set/
├── Artist - Track.mp3
├── Artist - Track [soundcloud-456].mp3
└── .ytscloud.sqlite3
```

Audio is staged in a temporary directory, checked with ffprobe, and decoded with
FFmpeg before publication. Validation checks codec, duration, and decoding errors;
it does not guarantee source quality or correct musical content.

The SQLite index records source URLs, format, filename, and SHA-256. Repeating the
same platform/track/format checks the stored checksum and skips a complete file.
Existing filenames are preserved; collisions use a source-ID suffix or fail if
that filename is also occupied. Missing or modified indexed files cause an error
and are not silently replaced. Renamed files are not rediscovered automatically.

Duplicate detection is local to an output folder and is not audio fingerprinting
across services. YouTube lookups can reuse a previously indexed Spotify download
of the same video. The application's old per-track folders are supported;
arbitrary spotDL folders are not imported.
The output filesystem must support hard links.

## Spotify credentials

The default spotDL client resolves public metadata without your own credentials.
Availability and rate limits depend on Spotify and spotDL. To use the official
API, set both variables in your shell:

```bash
export SPOTIFY_CLIENT_ID='your-client-id'
export SPOTIFY_CLIENT_SECRET='your-client-secret'
```

The app does not read `.env` files. Credentials do not grant access to Spotify
audio or remove API restrictions. Never commit secrets, audio, or your local index.

## Troubleshooting

| Problem | Next step |
| --- | --- |
| Missing FFmpeg/ffprobe | Install FFmpeg and check both executables are on `PATH`. |
| YouTube JavaScript errors | Check your runtime against the [EJS guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS), then reinstall with `python -m pip install --upgrade .`. |
| Ambiguous or missing match | Review in a terminal or supply an authorized direct URL. |
| HTTP 403/429, unavailable upload | Check availability and rate limits; retry later. Do not bypass access controls. |
| Existing file missing or changed | Check the named file and restore it from backup if appropriate; no overwrite is attempted. |
| Hard-link error | Choose a local filesystem that supports hard links. |

## Development and contributing

```bash
python -m pip install -e .
python -m pip check
python -m unittest discover -s tests -v
```

Tests mock remote services and generate synthetic MP3/WAV samples locally.
CI builds a wheel and source distribution, installs the wheel, and runs the CLI
and the offline tests on Python 3.11–3.15. Live service compatibility is
not exercised by this suite.

See [CONTRIBUTING.md](CONTRIBUTING.md) for bug reports and pull requests,
[SECURITY.md](SECURITY.md) for security reporting, and
[GitHub Actions](https://github.com/nachopalomares/music-ytscloud-downloader/actions)
for the current CI results.

## License and dependencies

[MIT](LICENSE). Dependencies keep their own licenses:
[spotDL](https://github.com/spotDL/spotify-downloader),
[yt-dlp](https://github.com/yt-dlp/yt-dlp), and
[ytmusicapi](https://github.com/sigma67/ytmusicapi).
FFmpeg and the JavaScript runtime are installed separately. This project is not
affiliated with Spotify, YouTube, or SoundCloud. Respect platform terms and the
rights attached to each recording.
