# Security

This project is experimental. Fixes target the current `main` branch; older
versions do not have a separate maintenance or security support policy.

## Reporting a vulnerability

Do not post credentials, private URLs, or working exploits in public issues.
Use GitHub's **Security → Report a vulnerability** option if it is available.
If private reporting is unavailable, open an issue asking the maintainer for a
private reporting channel, without including sensitive details.

Include the affected version or commit, the impact, and minimal reproduction
steps using synthetic data. There is no guaranteed response time.

## Local data and dependencies

- Spotify credentials are read from `SPOTIFY_CLIENT_ID` and
  `SPOTIFY_CLIENT_SECRET`. The application does not load `.env` files.
- Output folders contain audio and a SQLite index with source URLs, filenames,
  checksums, and requested inputs. JSON output can also include absolute paths.
  Review these before sharing them.
- `.gitignore` excludes common audio formats, local databases, logs, and `.env`
  files. It does not remove files already committed or protect arbitrary files
  saved outside the repository.
- Audio processing invokes FFmpeg and uses third-party services through spotDL,
  yt-dlp, and ytmusicapi. Keep these tools updated within the package's dependency
  constraints and rerun the tests after updating.

The tool does not provide DRM bypass, authentication cookies, or private-content
access options. Download only content you own or are authorized to download.
