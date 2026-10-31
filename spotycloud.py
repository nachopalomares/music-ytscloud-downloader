import argparse
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import closing, redirect_stdout
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit


class MatchError(ValueError):
    pass


class SelectionSkipped(Exception):
    pass


class SoundCloudRequested(Exception):
    pass


def choose_option(labels, source=None):
    labels = labels[:7]
    print("\n  Opciones encontradas:", flush=True)
    for index, label in enumerate(labels, 1):
        print(f"    {index}. {label}", flush=True)
    extra = ", s = buscar en SoundCloud" if source == "youtube-music" else ""
    while True:
        try:
            answer = input(f"  Elige 1-{len(labels)} (Enter = 1, 0 = saltar{extra}): ").strip()
        except EOFError:
            raise SelectionSkipped("Selección interrumpida.") from None
        if not answer:
            return 0
        if answer == "0":
            raise SelectionSkipped("Canción omitida por el usuario.")
        if answer.casefold() == "s" and source == "youtube-music":
            raise SoundCloudRequested()
        if (len(answer) <= len(str(len(labels))) and answer.isascii()
                and answer.isdigit() and 1 <= int(answer) <= len(labels)):
            return int(answer) - 1
        suffix = " o s para buscar en SoundCloud" if source == "youtube-music" else ""
        print(f"  Introduce un número entre 0 y {len(labels)}{suffix}.", flush=True)


def parse_entry(value):
    text = re.sub(r"^\s*\d+[.)]\s*", "", value.strip())
    platform = "soundcloud" if text.startswith("*") else "youtube-music"
    text = text.lstrip("*").strip()
    if "://" in text:
        platform, url = parse_url(text)
        return {"platform": platform, "url": url}
    artists, separator, title = text.partition(" - ")
    if not separator or not artists.strip() or not title.strip():
        raise ValueError("Usa un enlace o 'Artista - Título'; antepone * para SoundCloud.")
    return {"platform": platform, "artists": artists.strip(), "title": title.strip(),
            "query": f"{artists.strip()} - {title.strip()}"}


def parse_url(value):
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.port not in (None, 443)):
        raise ValueError("Usa un enlace HTTPS público de YouTube, Spotify o SoundCloud.")
    path = parts.path.rstrip("/")
    if parts.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"):
        if parts.hostname == "youtu.be":
            video_ids = [path.lstrip("/")]
        elif path == "/watch":
            video_ids = parse_qs(parts.query).get("v", [])
        elif path.startswith("/shorts/"):
            video_ids = [path.removeprefix("/shorts/")]
        else:
            video_ids = []
        if len(video_ids) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_ids[0]):
            raise ValueError("YouTube: usa el enlace de un vídeo, youtu.be o Short; no se admiten playlists.")
        return "youtube-music", f"https://www.youtube.com/watch?v={video_ids[0]}"
    elif parts.hostname == "open.spotify.com":
        path = re.sub(r"^/intl-[a-zA-Z-]+/", "/", path)
        if not re.fullmatch(r"/(track|playlist)/[A-Za-z0-9]{22}", path):
            raise ValueError("Spotify: se admiten canciones y playlists públicas.")
        platform = "spotify"
    elif parts.hostname in ("soundcloud.com", "www.soundcloud.com"):
        if not re.fullmatch(r"/[\w-]+/(?:sets/)?[\w-]+", path):
            raise ValueError("SoundCloud: usa el enlace completo de una canción o set público.")
        platform = "soundcloud"
    else:
        raise ValueError("Dominio no admitido; usa YouTube, open.spotify.com o soundcloud.com.")
    return platform, urlunsplit(("https", parts.hostname, path, "", ""))


def ydl_options():
    return {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "socket_timeout": 20, "retries": 2, "fragment_retries": 2,
        "extractor_retries": 2, "ignoreerrors": False,
        "js_runtimes": {"deno": {}, "node": {}},
        "logger": logging.getLogger("yt_dlp"),
    }


def spotify_client():
    from spotdl.utils.spotify import SpotifyClient

    if SpotifyClient._instance is None:
        client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        if bool(client_id) != bool(secret):
            raise ValueError("Configura SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET juntos.")
        SpotifyClient.init(
            client_id=client_id, client_secret=secret, headless=True,
            no_cache=True, max_retries=2, use_official_api=bool(client_id),
        )
    return SpotifyClient()


def spotify_tracks(url):
    from spotdl.types.song import Song

    client = spotify_client()
    if "/track/" in url:
        yield {"id": url.rsplit("/", 1)[1], "track_url": url, "song_loader": Song.from_url}
        return
    page = client.playlist_items(url)
    while True:
        if not isinstance(page, dict) or "items" not in page:
            raise ValueError("No se ha podido leer la playlist completa de Spotify.")
        for item in page["items"]:
            track = item.get("track") or {}
            artists = ", ".join(artist["name"] for artist in track.get("artists", []) if artist.get("name"))
            display_name = f'{artists} - {track["name"]}' if artists and track.get("name") else track.get("name")
            track_id = track.get("id")
            if not track_id or track.get("type") != "track" or item.get("is_local"):
                yield {"error": "Pista eliminada, local o no disponible.", "display_name": display_name}
                continue
            yield {"id": track_id, "track_url": f"https://open.spotify.com/track/{track_id}",
                   "song_loader": Song.from_url, "display_name": display_name}
        if not page.get("next"):
            break
        page = client.next(page)


def soundcloud_tracks(url):
    from yt_dlp import YoutubeDL

    with YoutubeDL({**ydl_options(), "extract_flat": "in_playlist"}) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise ValueError("SoundCloud no ha devuelto metadatos.")
        entries = info["entries"] if "entries" in info else [info]
        for entry in entries:
            if not entry:
                yield {"error": "Pista de SoundCloud no disponible."}
                continue
            source = entry.get("webpage_url") or entry.get("url")
            host = urlsplit(source or "").hostname
            if host not in ("soundcloud.com", "www.soundcloud.com",
                            "api.soundcloud.com", "api-v2.soundcloud.com"):
                yield {"error": "SoundCloud ha devuelto una fuente inesperada."}
                continue
            yield {"id": str(entry["id"]), "track_url": source, "display_name": entry.get("title")}


def normalized(text):
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                    if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", plain))


def match_labels(options):
    return [f'{", ".join(c["artists"])} - {c["title"]} | '
            f'{c.get("album") or "Sin álbum"} | {c.get("duration") or "?"} s\n'
            f'       {c["track_url"]}' for c in options]


def confirm_search_results(candidates, platform, select):
    options = list({c["id"]: c for c in candidates}.values())
    source = "SoundCloud" if platform == "soundcloud" else "YouTube Music"
    print(f"  Resultados de {source}: confirma una opción o pulsa 0 para saltar. Revisa título y versión.", flush=True)
    track = options[select(match_labels(options), source=platform)]
    return {**track, "platform": platform, "selection": "manual"}


def choose_named_match(artists, title, candidates, select=None, source=None):
    expected = normalized(title)
    version_words = {"edit", "remix", "live", "instrumental", "cover", "remaster",
                     "remastered", "slowed", "sped", "acoustic", "karaoke", "extended"}
    versions = set(expected.split()) & version_words
    matches = {}
    for candidate in candidates:
        raw_title = candidate["title"]
        prefix, separator, remainder = raw_title.partition(" - ")
        main_artist = set(normalized(artists.split(",")[0]).split())
        if separator and main_artist <= set(normalized(prefix).split()):
            raw_title = remainder
        actual = normalized(raw_title)
        credits = normalized(" ".join(candidate["artists"]) + " " + candidate["title"]).split()
        if not all(set(normalized(artist).split()) <= set(credits) for artist in artists.split(",")):
            continue
        if set(actual.split()) & version_words != versions:
            continue
        # Named edits must match exactly; fuzzy matching can select another DJ's version.
        if versions and actual != expected:
            continue
        if not versions and candidate.get("album") and expected == normalized(f'{raw_title} {candidate["album"]}'):
            actual = expected
        score = SequenceMatcher(None, expected, actual).ratio()
        key = candidate.get("recording_id") or candidate["id"]
        if score >= .92 and (key not in matches or score > matches[key][0]):
            matches[key] = (score, candidate)
    ranked = sorted(matches.values(), key=lambda pair: pair[0], reverse=True)
    if not ranked:
        raise MatchError("No se encontró una coincidencia fiable de artista, título y versión.")
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .05:
        options = [candidate for score, candidate in matches.values()]
        if select:
            return options[select(match_labels(options), source=source)]
        return options[0]
    return ranked[0][1]


def search_track(entry, select=None):
    if entry["platform"] == "youtube-music":
        from ytmusicapi import YTMusic

        items = YTMusic().search(entry["query"], filter="songs", limit=20, ignore_spelling=True)
        candidates = [{
            "id": item["videoId"], "title": item["title"],
            "artists": [artist["name"] for artist in item["artists"]],
            "album": (item.get("album") or {}).get("name"),
            "recording_id": (
                json.dumps([normalized(item["title"]), sorted(normalized(a["name"]) for a in item["artists"]),
                            item["duration_seconds"], item.get("isExplicit")]) if item.get("duration_seconds") else None
            ),
            "track_url": f'https://music.youtube.com/watch?v={item["videoId"]}',
            "duration": item.get("duration_seconds"),
        } for item in items if item and item.get("resultType") == "song"
            and re.fullmatch(r"[A-Za-z0-9_-]{11}", item.get("videoId") or "")
            and item.get("isAvailable") is not False]
    else:
        from yt_dlp import YoutubeDL

        with YoutubeDL({**ydl_options(), "extract_flat": True}) as ydl:
            candidates = []
            queries = [normalized(entry["query"]), normalized(f'{entry["artists"].split(",")[0]} {entry["title"]}')]
            for query in dict.fromkeys(queries):
                response = ydl.extract_info(f"scsearch20:{query}", download=False)
                for item in (response or {}).get("entries", []):
                    if not item or not item.get("title"):
                        continue
                    source = item.get("webpage_url") or item.get("url")
                    if urlsplit(source or "").hostname not in (
                            "soundcloud.com", "www.soundcloud.com", "api.soundcloud.com", "api-v2.soundcloud.com"):
                        continue
                    candidates.append({
                        "id": str(item["id"]), "title": item["title"], "track_url": source,
                        "artists": [*(item.get("artists") or []), item.get("uploader") or ""],
                        "duration": item.get("duration"),
                    })
    if entry["platform"] == "soundcloud" and select and candidates:
        return confirm_search_results(candidates, "soundcloud", select)
    try:
        try:
            track = choose_named_match(entry["artists"], entry["title"], candidates,
                                       select=select, source=entry["platform"])
        except MatchError:
            if select and candidates:
                return confirm_search_results(candidates, entry["platform"], select)
            raise
    except (MatchError, SoundCloudRequested):
        if entry["platform"] != "youtube-music":
            raise
        if select:
            print("  Buscando esta canción en SoundCloud…", flush=True)
        track = search_track({**entry, "platform": "soundcloud"}, select=select)
        return {**track, "fallback_from": "youtube-music"}
    return {**track, "platform": entry["platform"], "requested_artists": entry["artists"],
            "requested_title": entry["title"]}


def choose_match(song, candidates, select=None):
    versions = {"live", "remix", "remaster", "remastered", "acoustic", "cover",
                "karaoke", "instrumental", "slowed", "sped", "edit", "extended"}
    expected = set(re.findall(r"\w+", song.name.casefold())) & versions
    eligible = {}
    for result, score in candidates.items():
        actual = set(re.findall(r"\w+", result.name.casefold())) & versions
        if (result.verified and actual == expected and song.duration > 0
                and abs(result.duration - song.duration) <= max(3, song.duration * .02)):
            previous = eligible.get(result.url)
            if previous is None or score > previous[1]:
                eligible[result.url] = (result, score)
    ranked = sorted(eligible.values(), key=lambda pair: pair[1], reverse=True)
    if not ranked or ranked[0][1] < 90:
        raise MatchError("No hay una coincidencia suficientemente fiable para esta canción.")
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 5:
        options = [result for result, score in eligible.values() if score >= 90]
        if select:
            labels = [f'{c.author} - {c.name} | {c.album or "Sin álbum"} | {c.duration} s\n'
                      f'       {c.url}' for c in options]
            return options[select(labels)].url
        return options[0].url
    return ranked[0][0].url


def spotify_source(song, select=None):
    from spotdl.providers.audio import YouTubeMusic
    from spotdl.utils.matching import order_results

    provider = YouTubeMusic()
    results = provider.get_results(
        f"{' '.join(song.artists)} - {song.name}", filter="songs", limit=20,
    )
    return choose_match(song, order_results(results, song), select=select)


def reject_live(info, *, incomplete):
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        return "No se admiten emisiones en directo ni estrenos pendientes."


def download_audio(source, target, fmt):
    from yt_dlp import YoutubeDL

    options = {
        **ydl_options(), "format": "bestaudio[format_id!*=preview]/best[format_id!*=preview]", "noplaylist": True,
        "overwrites": False, "outtmpl": str(target / "audio.%(ext)s"),
        "match_filter": reject_live,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": "0"},
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(source, download=True)
    if not info:
        raise ValueError("La descarga no ha devuelto audio; comprueba su disponibilidad y que no sea un directo.")
    return info


def validate_audio(path, expected_duration):
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("La descarga no ha producido audio.")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    metadata = json.loads(probe.stdout)
    duration = float(metadata["format"]["duration"])
    codec = "mp3" if path.suffix == ".mp3" else "pcm_s16le"
    if (not metadata.get("streams") or metadata["streams"][0]["codec_name"] != codec
            or not math.isfinite(expected_duration) or not math.isfinite(duration)
            or expected_duration <= 0 or duration <= 0
            or abs(duration - expected_duration) > max(3, expected_duration * .02)):
        raise ValueError("El audio no coincide con el formato o duración esperados.")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
         "-map", "0:a:0", "-f", "null", "-"],
        check=True, capture_output=True, timeout=300,
    )
    return duration


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audio_name(artist, title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "-", f"{artist} - {title}")
    return name.encode("utf-8")[:180].decode("utf-8", errors="ignore").strip(" .")


def cached_track(db, key, requested_input, output):
    row = db.execute("SELECT record FROM downloads WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    filename = record["filename"]
    if Path(filename).name != filename:
        raise ValueError("El índice contiene un nombre de archivo inválido.")
    audio = output / filename
    if record["sha256"] != checksum(audio):
        raise ValueError("El archivo existente está dañado; no se sobrescribe.")
    return {**record, "requested_input": requested_input, "status": "skipped", "path": str(audio)}


def previous_youtube_audio(db, track, output, fmt):
    records = [(json.loads(row[0]), None) for row in db.execute("SELECT record FROM downloads")]
    for info in output.glob("spotify-*/info.json"):
        try:
            records.append((json.loads(info.read_text(encoding="utf-8")), info.parent / f"audio.{fmt}"))
        except (OSError, ValueError):
            continue
    for record, legacy_audio in records:
        source = urlsplit(record.get("source_url") or "")
        video_id = source.path.strip("/") if source.hostname == "youtu.be" else parse_qs(source.query).get("v", [None])[0]
        if (source.hostname not in ("youtu.be", "youtube.com", "www.youtube.com", "music.youtube.com")
                or video_id != track["id"] or record.get("format") != fmt):
            continue
        filename = record.get("filename", "")
        if legacy_audio is None and (not filename or Path(filename).name != filename):
            raise ValueError("El índice contiene un nombre de archivo inválido.")
        audio = legacy_audio if legacy_audio is not None else output / filename
        if record["sha256"] != checksum(audio):
            raise ValueError("El archivo anterior está dañado; no se sobrescribe.")
        return {**record, "platform": "youtube-music", "id": track["id"], "track_url": track["track_url"]}, audio
    return None


def save_track(platform, track, requested_input, output, fmt, select=None):
    track_id = track["id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", track_id):
        raise ValueError("Identificador de pista inválido.")
    key = f"{platform}-{track_id}-{fmt}"
    with closing(sqlite3.connect(output / ".spotycloud.sqlite3")) as db:
        db.execute("CREATE TABLE IF NOT EXISTS downloads (key TEXT PRIMARY KEY, record TEXT NOT NULL)")
        existing = cached_track(db, key, requested_input, output)
        if existing:
            return existing
        with tempfile.TemporaryDirectory(prefix=".spotycloud-", dir=output) as temporary:
            staging = Path(temporary)
            legacy = output / key
            previous = previous_youtube_audio(db, track, output, fmt) if platform == "youtube-music" else None
            if previous:
                record, audio = previous
                status = "skipped"
            elif legacy.exists() or legacy.is_symlink():
                record = json.loads((legacy / "info.json").read_text(encoding="utf-8"))
                audio = legacy / f"audio.{fmt}"
                if (record["track_url"] != track["track_url"] or record["format"] != fmt
                        or record["sha256"] != checksum(audio)):
                    raise ValueError("La descarga anterior está dañada; no se sobrescribe.")
                status = "skipped"
            else:
                if platform == "spotify":
                    song = track["song_loader"](track["track_url"])
                    track["display_name"] = f'{", ".join(song.artists)} - {song.name}'
                    source = spotify_source(song, select=select) if select else spotify_source(song)
                else:
                    song = None
                    source = track["track_url"]
                info = download_audio(source, staging, fmt)
                audio = staging / f"audio.{fmt}"
                duration = validate_audio(audio, song.duration if song else track.get("duration") or info.get("duration", 0))
                artist = ", ".join(song.artists) if song else info.get("artist") or ", ".join(info.get("artists") or [])
                title = song.name if song else info.get("title") or "Sin título"
                if not artist and " - " in title:
                    artist, title = title.split(" - ", 1)
                artist = artist or info.get("uploader") or "Artista desconocido"
                if title.casefold().startswith(f"{artist} - ".casefold()):
                    title = title[len(artist) + 3:]
                record = {
                    "platform": platform, "id": track_id, "track_url": track["track_url"],
                    "source_url": info.get("webpage_url") or source, "title": title, "artist": artist,
                    "format": fmt, "duration": duration, "sha256": checksum(audio),
                }
                status = "downloaded"
            record["requested_input"] = requested_input
            if "requested_artists" in track:
                record["artist"] = track["requested_artists"]
                record["title"] = track["requested_title"]
            if "fallback_from" in track:
                record["fallback_from"] = track["fallback_from"]
            if track.get("selection") == "manual":
                record["selection"] = "manual"
            stem = audio_name(record["artist"], record["title"])
            db.execute("BEGIN IMMEDIATE")
            existing = cached_track(db, key, requested_input, output)
            if existing:
                return existing
            destination = output / f"{stem}.{fmt}"
            linked = False
            if audio != destination:
                try:
                    os.link(audio, destination)
                except FileExistsError:
                    destination = output / f"{stem} [{platform}-{track_id}].{fmt}"
                    os.link(audio, destination)
                linked = True
            try:
                record["filename"] = destination.name
                db.execute("INSERT INTO downloads VALUES (?, ?)", (key, json.dumps(record, ensure_ascii=False)))
                db.commit()
            except BaseException:
                if linked:
                    destination.unlink()
                raise
    return {**record, "status": status, "path": str(destination)}


def emit(record, json_output=False):
    if json_output:
        print(json.dumps(record, ensure_ascii=False), flush=True)
    elif record["status"] == "summary":
        print(f'\nFinalizado: {record["downloaded"]} descargadas · '
              f'{record["skipped"]} omitidas · {record["errors"]} errores', flush=True)
        if record.get("failed_tracks"):
            print("\nCanciones con errores:", flush=True)
            for name in record["failed_tracks"]:
                print(f"  - {name}", flush=True)
    elif record["status"] == "error":
        print("  Error: " + str(record["error"]).replace("\n", "\n    "), flush=True)
    elif record.get("reason") == "user_choice":
        print("  Saltada por elección del usuario.", flush=True)
    else:
        label = "Guardada" if record["status"] == "downloaded" else "Ya descargada"
        print(f'  {label}: {record.get("path", "")}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Descarga audio de YouTube y SoundCloud; acepta enlaces de Spotify.")
    parser.add_argument("entries", nargs="*", help="Enlaces o 'Artista - Título'; * indica SoundCloud")
    parser.add_argument("--file", type=Path, help="Lista de canciones, una por línea; - lee stdin")
    parser.add_argument("--output", "--folder", type=Path, default=Path("downloads"),
                        help="Carpeta de salida (por defecto: downloads)")
    parser.add_argument("--format", choices=("mp3", "wav"), default="mp3")
    parser.add_argument("--json", action="store_true", help="Emitir JSON Lines para automatización")
    parser.add_argument("--yes", "-y", action="store_true", help="Elegir automáticamente la primera coincidencia válida")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        lines = [line for value in args.entries for line in value.splitlines() if line.strip()]
        if args.file:
            text = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8-sig")
            lines.extend(line for line in text.splitlines() if line.strip())
        if not lines:
            raise ValueError("Proporciona un enlace, una canción o --file lista.txt.")
        entries = []
        for line in lines:
            try:
                entries.append({**parse_entry(line), "input": line})
            except ValueError as error:
                entries.append({"input": line, "error": str(error)})
        if all("error" in entry for entry in entries):
            raise ValueError(entries[0]["error"])
        if not all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe")):
            raise ValueError("Falta FFmpeg/ffprobe. En macOS: brew install ffmpeg")
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as error:
        emit({"status": "error", "error": str(error)}, args.json)
        return 2
    counts = {"downloaded": 0, "skipped": 0, "errors": 0}
    failed_tracks = []
    select = choose_option if not args.json and not args.yes and sys.stdin.isatty() and str(args.file) != "-" else None
    names = {"youtube-music": "YouTube Music", "soundcloud": "SoundCloud", "spotify": "Spotify"}
    for index, entry in enumerate(entries, 1):
        requested_input = entry["input"]
        if not args.json:
            print(f"\n[{index}/{len(entries)}] {requested_input}", flush=True)
        found = False
        try:
            if "error" in entry:
                raise ValueError(entry["error"])
            platform = entry["platform"]
            url = entry.get("url", entry.get("query"))
            if not args.json:
                if platform == "youtube-music" and "url" in entry:
                    print("  Abriendo vídeo de YouTube…", flush=True)
                else:
                    print(f"  Consultando {names[platform]}…", flush=True)
            with redirect_stdout(sys.stderr):
                if "query" in entry:
                    tracks = iter([search_track(entry, select=select) if select else search_track(entry)])
                elif platform == "youtube-music":
                    video_id = parse_qs(urlsplit(url).query)["v"][0]
                    tracks = iter([{"id": video_id, "track_url": url}])
                else:
                    tracks = iter(spotify_tracks(url) if platform == "spotify" else soundcloud_tracks(url))
            while True:
                with redirect_stdout(sys.stderr):
                    track = next(tracks, None)
                if track is None:
                    break
                found = True
                try:
                    if "error" in track:
                        raise ValueError(track["error"])
                    actual_platform = track.get("platform", platform)
                    if not args.json:
                        if track.get("fallback_from"):
                            print("  Usando SoundCloud como fuente alternativa.", flush=True)
                        print(f"  Comprobando archivos, descargando y validando desde {names[actual_platform]}…", flush=True)
                    with redirect_stdout(sys.stderr):
                        if select:
                            record = save_track(actual_platform, track, requested_input, output, args.format, select=select)
                        else:
                            record = save_track(actual_platform, track, requested_input, output, args.format)
                    counts[record["status"]] += 1
                except SelectionSkipped:
                    counts["skipped"] += 1
                    record = {"status": "skipped", "reason": "user_choice", "requested_input": requested_input}
                except Exception as error:
                    counts["errors"] += 1
                    failed_tracks.append(track.get("display_name") or entry.get("query") or requested_input)
                    record = {"status": "error", "requested_input": requested_input,
                              "track_url": track.get("track_url"), "error": str(error)}
                emit(record, args.json)
            if not found:
                raise ValueError("La fuente no ha devuelto canciones.")
        except SelectionSkipped:
            counts["skipped"] += 1
            emit({"status": "skipped", "reason": "user_choice", "requested_input": requested_input}, args.json)
        except Exception as error:
            counts["errors"] += 1
            failed_tracks.append(entry.get("query") or requested_input)
            emit({"status": "error", "requested_input": requested_input, "error": str(error)}, args.json)
    emit({"status": "summary", **counts, "failed_tracks": failed_tracks}, args.json)
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
