import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import spotycloud as app


SPOTIFY = "https://open.spotify.com/track/" + "A" * 22
SOUNDCLOUD = "https://soundcloud.com/artist/song"


@dataclass(frozen=True)
class Candidate:
    url: str = "https://music.youtube.com/watch?v=one"
    name: str = "Song"
    duration: float = 180
    verified: bool = True
    author: str = "Artist"
    album: str = "Album"


class ChoiceTests(unittest.TestCase):
    def test_s_requests_soundcloud_from_youtube_menu(self):
        for response in ("s", "S"):
            with self.subTest(response=response), redirect_stdout(io.StringIO()):
                with patch("builtins.input", return_value=response) as prompt:
                    with self.assertRaises(app.SoundCloudRequested):
                        app.choose_option(["first"], source="youtube-music")
                self.assertIn("s = buscar en SoundCloud", prompt.call_args.args[0])

    def test_soundcloud_menu_does_not_offer_another_soundcloud_search(self):
        with patch("builtins.input", side_effect=["s", "1"]) as prompt, redirect_stdout(io.StringIO()):
            self.assertEqual(app.choose_option(["first"], source="soundcloud"), 0)
        self.assertEqual(prompt.call_count, 2)
        self.assertNotIn("s = buscar", prompt.call_args.args[0])

    def test_menu_shows_at_most_seven_options_and_rejects_hidden_choices(self):
        labels = [f"option-{index}" for index in range(1, 11)]
        with patch("builtins.input", side_effect=["8", "7"]) as prompt, redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(app.choose_option(labels), 6)
        self.assertIn("7. option-7", stdout.getvalue())
        self.assertNotIn("option-8", stdout.getvalue())
        self.assertNotIn("option-10", stdout.getvalue())
        self.assertIn("1-7", prompt.call_args.args[0])

    def test_choose_second_option(self):
        with patch("builtins.input", return_value="2"), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(app.choose_option(["Artist - Song | Album 1", "Artist - Song | Album 2"]), 1)
        self.assertIn("1. Artist - Song | Album 1", stdout.getvalue())
        self.assertIn("2. Artist - Song | Album 2", stdout.getvalue())

    def test_invalid_input_reprompts_and_enter_selects_first(self):
        with patch("builtins.input", side_effect=["text", "3", "-1", "9" * 5000, ""]), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(app.choose_option(["first", "second"]), 0)
        self.assertEqual(stdout.getvalue().count("Introduce un número"), 4)

    def test_zero_and_eof_skip_the_song(self):
        for response in ("0", EOFError()):
            with self.subTest(response=response), redirect_stdout(io.StringIO()):
                with patch("builtins.input", side_effect=[response]):
                    with self.assertRaises(app.SelectionSkipped):
                        app.choose_option(["first", "second"])


class UrlTests(unittest.TestCase):
    def test_youtube_video_urls_are_canonicalized_and_playlist_parameters_are_removed(self):
        video_id = "_adyojG3-ws"
        canonical = "https://www.youtube.com/watch?v=" + video_id
        for url in (
            canonical,
            canonical + "&list=PLexample&index=2&t=30s",
            "https://youtu.be/" + video_id + "?si=tracking",
            "https://music.youtube.com/watch?v=" + video_id,
            "https://m.youtube.com/watch?v=" + video_id,
            "https://youtube.com/shorts/" + video_id + "?feature=share",
        ):
            with self.subTest(url=url):
                self.assertEqual(app.parse_url(url), ("youtube-music", canonical))

    def test_invalid_youtube_urls_are_rejected(self):
        for url in (
            "https://youtube.com.evil/watch?v=" + "A" * 11,
            "https://youtube.com/playlist?list=PLexample",
            "https://youtube.com/watch?v=short",
            "https://youtube.com/watch?v=" + "A" * 11 + "&v=" + "B" * 11,
            "https://youtu.be/" + "A" * 11 + "/other",
            "https://youtube.com/shorts/" + "A" * 11 + "/other",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                app.parse_url(url)

    def test_spotify_localized_and_tracking(self):
        url = SPOTIFY.replace("/track/", "/intl-es/track/") + "?si=tracking#fragment"
        self.assertEqual(app.parse_url(url), ("spotify", SPOTIFY))

    def test_soundcloud_set(self):
        url = "https://soundcloud.com/artist/sets/list"
        self.assertEqual(app.parse_url(url), ("soundcloud", url))

    def test_invalid_urls(self):
        for url in ("http://soundcloud.com/a/b", "https://soundcloud.com.evil/a/b",
                    "https://user:pass@soundcloud.com/a/b", "file:///tmp/song",
                    "https://soundcloud.com:8000/a/b", "https://soundcloud.com/profile",
                    "https://open.spotify.com/track/short", "https://on.soundcloud.com/abc",
                    "https://soundcloud.com/a/../../file", "https://soundcloud.com:bad/a/b"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                app.parse_url(url)


class MatchingTests(unittest.TestCase):
    song = SimpleNamespace(name="Song", duration=180)

    def test_unique_good_match(self):
        candidate = Candidate()
        self.assertEqual(app.choose_match(self.song, {candidate: 95}), candidate.url)

    def test_rejects_unreliable_versions_and_duration(self):
        for candidate, score in ((Candidate(), 89), (Candidate(verified=False), 99),
                                 (Candidate(name="Song (Remix)"), 99),
                                 (Candidate(name="Song Live"), 99),
                                 (Candidate(duration=30), 99)):
            with self.subTest(candidate=candidate, score=score), self.assertRaises(ValueError):
                app.choose_match(self.song, {candidate: score})

    def test_ambiguous_results_choose_first_valid_source(self):
        first = Candidate()
        self.assertEqual(app.choose_match(self.song, {first: 94, Candidate(url="other"): 96}), first.url)
        self.assertEqual(app.choose_match(self.song, {Candidate(url="weak"): 89, first: 94,
                                                    Candidate(url="other"): 96}), first.url)

    def test_spotify_audio_ambiguity_accepts_user_selection(self):
        first, second = Candidate(), Candidate(url="other")
        labels = []

        def select(options):
            labels.extend(options)
            return 1

        self.assertEqual(app.choose_match(self.song, {first: 94, second: 96}, select=select), "other")
        self.assertIn("Artist - Song", labels[1])
        self.assertIn("other", labels[1])

    def test_same_source_is_not_ambiguous(self):
        self.assertEqual(app.choose_match(self.song, {
            Candidate(): 96, Candidate(duration=181): 94,
        }), Candidate().url)


class ListTests(unittest.TestCase):
    def test_user_example_routes_four_tracks_to_each_platform(self):
        sample = Path(__file__).resolve().parents[1] / "examples" / "set.txt"
        entries = [app.parse_entry(line) for line in sample.read_text().splitlines()]
        self.assertEqual([item["platform"] for item in entries], ["youtube-music"] * 4 + ["soundcloud"] * 4)
        self.assertEqual(entries[4]["title"], "PERDIÓ [KAYVVY EDIT]")
        self.assertEqual(entries[5]["artists"], "LEÏTI, La Zowi")

    def test_link_routes_by_domain(self):
        self.assertEqual(app.parse_entry(SOUNDCLOUD), {"platform": "soundcloud", "url": SOUNDCLOUD})
        self.assertEqual(app.parse_entry(SPOTIFY)["platform"], "spotify")

    def test_artist_numbers_are_preserved(self):
        self.assertEqual(app.parse_entry("17. 8belial - Mr. Fino")["artists"], "8belial")

    def test_named_edit_matches_across_accents_and_punctuation(self):
        candidate = {"id": "123", "title": "Bad Gyal, Ivy Queen - Perdio (KAYVVY EDIT)", "artists": ["Kayvvy"]}
        self.assertEqual(app.choose_named_match("Bad Gyal, Ivy Queen", "PERDIÓ [KAYVVY EDIT]", [candidate]), candidate)

    def test_wrong_edit_and_wrong_artist_are_rejected(self):
        candidates = [
            {"id": "123", "title": "Perdió (Other DJ Edit)", "artists": ["Bad Gyal", "Ivy Queen"]},
            {"id": "124", "title": "Perdió (Kayvvy Edit)", "artists": ["Other Artist"]},
        ]
        with self.assertRaises(ValueError):
            app.choose_named_match("Bad Gyal, Ivy Queen", "Perdió (Kayvvy Edit)", candidates)

    def test_title_hyphen_is_not_confused_with_artist_prefix(self):
        candidate = {"id": "123", "title": "Song - Radio Edit", "artists": ["Artist"]}
        self.assertEqual(app.choose_named_match("Artist", "Song - Radio Edit", [candidate]), candidate)

    def test_two_valid_uploads_choose_first(self):
        candidates = [{"id": str(index), "title": "Song", "artists": ["Artist"]} for index in (1, 2)]
        self.assertEqual(app.choose_named_match("Artist", "Song", candidates), candidates[0])

    def test_soundcloud_ambiguity_uses_search_order_instead_of_score(self):
        first = {"id": "1", "title": "Song Titles", "artists": ["Artist"]}
        second = {"id": "2", "title": "Song Title", "artists": ["Artist"]}
        self.assertEqual(app.choose_named_match("Artist", "Song Title", [first, second]), first)

    def test_soundcloud_first_match_still_checks_artist_and_version(self):
        invalid = {"id": "1", "title": "Song (Other Edit)", "artists": ["Artist"]}
        valid = {"id": "2", "title": "Song (DJ Edit)", "artists": ["Artist"]}
        repeated = {**valid, "id": "3"}
        self.assertEqual(app.choose_named_match("Artist", "Song (DJ Edit)", [invalid, valid, repeated]), valid)
        with self.assertRaises(app.MatchError):
            app.choose_named_match("Wrong Artist", "Song (DJ Edit)", [valid])

    def test_same_spotify_recording_on_multiple_albums_is_deduplicated(self):
        candidates = [{"id": str(index), "title": "Song", "artists": ["Artist"],
                       "recording_id": "same-isrc"} for index in (1, 2)]
        self.assertEqual(app.choose_named_match("Artist", "Song", candidates)["id"], "1")

    def test_album_context_in_requested_title(self):
        candidate = {"id": "123", "title": "Intro", "artists": ["Yung Beef"], "album": "ADROMICFMS 4"}
        self.assertEqual(app.choose_named_match("Yung Beef", "Intro (ADROMICFMS 4)", [candidate]), candidate)
        with self.assertRaises(app.MatchError):
            app.choose_named_match("Yung Beef", "Intro (ADROMICFMS 3)", [candidate])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name)
        self.track = {"id": "123", "track_url": SOUNDCLOUD}

    @staticmethod
    def fake_download(source, target, fmt):
        (target / f"audio.{fmt}").write_bytes(b"synthetic test payload")
        return {"title": "Song", "uploader": "Artist", "duration": 180, "webpage_url": source}

    def save(self, fmt="mp3"):
        return app.save_track("soundcloud", self.track, SOUNDCLOUD, self.output, fmt)

    def test_download_then_skip_and_detect_corruption(self):
        with patch.object(app, "download_audio", side_effect=self.fake_download) as download:
            with patch.object(app, "validate_audio", return_value=180):
                result = self.save()
            self.assertEqual(result["status"], "downloaded")
            self.assertEqual(Path(result["path"]), self.output / "Artist - Song.mp3")
            self.assertEqual(list(self.output.glob("*.json")), [])
            self.assertEqual([p for p in self.output.iterdir() if p.is_dir()], [])
            self.assertEqual(result["source_url"], SOUNDCLOUD)
            self.assertEqual(self.save()["status"], "skipped")
            self.assertEqual(download.call_count, 1)
            Path(result["path"]).write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "dañado"):
                self.save()
            self.assertEqual(Path(result["path"]).read_bytes(), b"corrupt")

    def test_failed_validation_never_publishes_audio(self):
        with patch.object(app, "download_audio", side_effect=self.fake_download):
            with patch.object(app, "validate_audio", side_effect=ValueError("audio inválido")):
                with self.assertRaises(ValueError):
                    self.save()
        self.assertEqual(list(self.output.glob("*.mp3")), [])
        self.assertEqual([p for p in self.output.iterdir() if p.is_dir()], [])

    def test_existing_incomplete_destination_is_preserved(self):
        folder = self.output / "soundcloud-123-mp3"
        folder.mkdir()
        audio = folder / "audio.mp3"
        audio.write_bytes(b"keep")
        with self.assertRaises(OSError):
            self.save()
        self.assertEqual(audio.read_bytes(), b"keep")

    def test_unsafe_id_is_rejected(self):
        self.track["id"] = "../escape"
        with self.assertRaises(ValueError):
            self.save()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_formats_have_independent_destinations(self):
        with patch.object(app, "download_audio", side_effect=self.fake_download):
            with patch.object(app, "validate_audio", return_value=180):
                mp3, wav = self.save(), self.save("wav")
        self.assertNotEqual(mp3["path"], wav["path"])

    def test_spotify_source_is_recorded(self):
        track = {"id": "A" * 22, "track_url": SPOTIFY,
                 "song_loader": lambda url: SimpleNamespace(name="Song", artist="Artist", artists=["Artist"], duration=180)}
        source = "https://music.youtube.com/watch?v=test"
        with patch.object(app, "spotify_source", return_value=source):
            with patch.object(app, "download_audio", side_effect=self.fake_download):
                with patch.object(app, "validate_audio", return_value=180):
                    result = app.save_track("spotify", track, SPOTIFY, self.output, "mp3")
        self.assertEqual(result["track_url"], SPOTIFY)
        self.assertEqual(result["source_url"], source)

    def test_colliding_titles_do_not_overwrite_and_repeat_is_skipped(self):
        with patch.object(app, "download_audio", side_effect=self.fake_download):
            with patch.object(app, "validate_audio", return_value=180):
                first = self.save()
                self.track["id"] = "456"
                second = self.save()
        self.assertEqual(Path(first["path"]).name, "Artist - Song.mp3")
        self.assertEqual(Path(second["path"]).name, "Artist - Song [soundcloud-456].mp3")
        self.assertEqual(self.save()["path"], second["path"])
        self.assertEqual(len(list(self.output.glob("*.mp3"))), 2)

    def test_untracked_file_is_preserved(self):
        original = self.output / "Artist - Song.mp3"
        original.write_bytes(b"existing user file")
        with patch.object(app, "download_audio", side_effect=self.fake_download):
            with patch.object(app, "validate_audio", return_value=180):
                result = self.save()
        self.assertNotEqual(Path(result["path"]), original)
        self.assertEqual(original.read_bytes(), b"existing user file")

    def test_filename_is_safe_and_keeps_accents(self):
        name = app.audio_name('LEÏTI/La Zowi', 'APOCALIPSI: "Edit"?')
        self.assertIn("LEÏTI", name)
        self.assertFalse(any(char in name for char in '/\\:*?"'))
        self.assertLessEqual(len(app.audio_name("á" * 300, "track").encode()), 180)

    def test_legacy_download_is_reused_without_deleting_original(self):
        legacy = self.output / "soundcloud-123-mp3"
        legacy.mkdir()
        audio = legacy / "audio.mp3"
        audio.write_bytes(b"previous download")
        record = {"platform": "soundcloud", "id": "123", "track_url": SOUNDCLOUD,
                  "artist": "Artist", "title": "Song", "format": "mp3", "sha256": app.checksum(audio)}
        (legacy / "info.json").write_text(json.dumps(record))
        with patch.object(app, "download_audio") as download:
            result = self.save()
        download.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(Path(result["path"]), self.output / "Artist - Song.mp3")
        self.assertEqual(audio.read_bytes(), Path(result["path"]).read_bytes())

    def test_soundcloud_artist_prefix_is_not_repeated(self):
        def download(source, target, fmt):
            return {**self.fake_download(source, target, fmt), "title": "Artista - Canción", "artist": "Artista"}

        with patch.object(app, "download_audio", side_effect=download):
            with patch.object(app, "validate_audio", return_value=180):
                result = self.save()
        self.assertEqual(Path(result["path"]).name, "Artista - Canción.mp3")

    def test_verified_query_names_take_precedence_over_channel_metadata(self):
        self.track.update(requested_artists="Bad Gyal", requested_title="Fiebre [KAYVVY EDIT]")
        with patch.object(app, "download_audio", side_effect=self.fake_download):
            with patch.object(app, "validate_audio", return_value=180):
                result = self.save()
        self.assertEqual(Path(result["path"]).name, "Bad Gyal - Fiebre [KAYVVY EDIT].mp3")

    def test_manually_confirmed_version_keeps_actual_source_title(self):
        self.track["selection"] = "manual"

        def download(source, target, fmt):
            return {**self.fake_download(source, target, fmt), "title": "PERCOSEX (REGGAETON REMIX)",
                    "artist": "Yung Beef, Cris MJ"}

        with patch.object(app, "download_audio", side_effect=download):
            with patch.object(app, "validate_audio", return_value=180):
                result = app.save_track("soundcloud", self.track, "Cris Mj, Yung Beef - PERCOSEX (Reggaeton Edit)", self.output, "mp3")
        self.assertIn("PERCOSEX (REGGAETON REMIX)", Path(result["path"]).name)
        self.assertNotIn("Reggaeton Edit", Path(result["path"]).name)
        self.assertEqual(result["selection"], "manual")

    def test_youtube_lookup_reuses_download_previously_identified_by_spotify(self):
        source = "https://music.youtube.com/watch?v=_adyojG3-ws"
        spotify = {"id": "A" * 22, "track_url": SPOTIFY,
                   "song_loader": lambda url: SimpleNamespace(name="Song", artists=["Artist"], duration=180)}
        with patch.object(app, "spotify_source", return_value=source):
            with patch.object(app, "download_audio", side_effect=self.fake_download):
                with patch.object(app, "validate_audio", return_value=180):
                    first = app.save_track("spotify", spotify, "Artist - Song", self.output, "mp3")
        youtube = {"id": "_adyojG3-ws", "track_url": source}
        with patch.object(app, "download_audio") as download:
            second = app.save_track("youtube-music", youtube, "Artist - Song", self.output, "mp3")
            repeated = app.save_track("youtube-music", youtube, "Artist - Song", self.output, "mp3")
        download.assert_not_called()
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["path"], first["path"])
        self.assertEqual(repeated["path"], first["path"])
        self.assertEqual(len(list(self.output.glob("*.mp3"))), 1)

    def test_youtube_lookup_reuses_old_spotify_folder(self):
        legacy = self.output / ("spotify-" + "A" * 22 + "-mp3")
        legacy.mkdir()
        audio = legacy / "audio.mp3"
        audio.write_bytes(b"previous download")
        source = "https://www.youtube.com/watch?v=_adyojG3-ws"
        record = {"platform": "spotify", "id": "A" * 22, "track_url": SPOTIFY,
                  "source_url": source, "artist": "Artist", "title": "Song",
                  "format": "mp3", "sha256": app.checksum(audio)}
        (legacy / "info.json").write_text(json.dumps(record))
        track = {"id": "_adyojG3-ws", "track_url": source.replace("www.", "music.")}
        with patch.object(app, "download_audio") as download:
            result = app.save_track("youtube-music", track, "Artist - Song", self.output, "mp3")
        download.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(Path(result["path"]).name, "Artist - Song.mp3")
        self.assertTrue(audio.exists())


class CliTests(unittest.TestCase):
    def setUp(self):
        terminal = patch.object(app.sys.stdin, "isatty", return_value=False)
        terminal.start()
        self.addCleanup(terminal.stop)

    def run_cli(self, urls, tracks):
        with tempfile.TemporaryDirectory() as folder, io.StringIO() as stdout:
            with patch.object(app.shutil, "which", return_value="/tools/ffmpeg"):
                with patch.object(app, "soundcloud_tracks", side_effect=tracks):
                    with patch.object(app, "save_track", return_value={"status": "downloaded"}):
                        with redirect_stdout(stdout):
                            code = app.main([*urls, "--output", folder, "--json"])
            return code, [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_youtube_link_downloads_requested_video_without_searching(self):
        video_id = "_adyojG3-ws"
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            with patch.object(app.shutil, "which", return_value="ffmpeg"):
                with patch.object(app, "search_track") as search, patch.object(app, "spotify_client") as spotify:
                    with patch.object(app, "soundcloud_tracks") as soundcloud:
                        with patch.object(app, "save_track", return_value={"status": "downloaded"}) as save:
                            code = app.main(["https://youtu.be/" + video_id, "--folder", folder, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(save.call_args.args[1]["id"], video_id)
        self.assertEqual(save.call_args.args[1]["track_url"], "https://www.youtube.com/watch?v=" + video_id)
        self.assertEqual(save.call_args.args[4], "mp3")
        search.assert_not_called()
        spotify.assert_not_called()
        soundcloud.assert_not_called()

    def test_network_failure_does_not_stop_next_url(self):
        code, records = self.run_cli([SOUNDCLOUD, SOUNDCLOUD], [
            OSError("network failed"), [{"id": "123", "track_url": SOUNDCLOUD}],
        ])
        self.assertEqual(code, 1)
        self.assertEqual(records[-1], {"status": "summary", "downloaded": 1, "skipped": 0, "errors": 1,
                                      "failed_tracks": [SOUNDCLOUD]})

    def test_unavailable_track_and_success(self):
        code, records = self.run_cli([SOUNDCLOUD], [[
            {"error": "Unavailable", "display_name": "Artist - Missing Song"},
            {"id": "123", "track_url": SOUNDCLOUD},
        ]])
        self.assertEqual(code, 1)
        self.assertEqual([r["status"] for r in records], ["error", "downloaded", "summary"])
        self.assertEqual(records[-1]["failed_tracks"], ["Artist - Missing Song"])

    def test_empty_playlist_is_error(self):
        code, records = self.run_cli([SOUNDCLOUD], [[]])
        self.assertEqual(code, 1)
        self.assertEqual(records[-1]["errors"], 1)

    def test_invalid_input_has_no_filesystem_side_effect(self):
        with patch.object(app.Path, "mkdir") as mkdir, redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(["https://example.com/file"]), 2)
        mkdir.assert_not_called()

    def test_missing_ffmpeg_is_actionable(self):
        with patch.object(app.shutil, "which", return_value=None), io.StringIO() as output:
            with redirect_stdout(output):
                self.assertEqual(app.main([SOUNDCLOUD, "--json"]), 2)
            self.assertIn("brew install ffmpeg", json.loads(output.getvalue())["error"])

    def test_file_batch_routes_searches_and_continues_after_malformed_line(self):
        with tempfile.TemporaryDirectory() as folder:
            batch = Path(folder) / "lista.txt"
            batch.write_text("15. Artist - Song\n\n19.* Artist - Song (Edit)\ninvalid\n", encoding="utf-8-sig")
            searches = []

            def search(entry):
                searches.append(entry["platform"])
                return {"id": "123", "track_url": SOUNDCLOUD, "platform": "soundcloud"}

            with patch.object(app.shutil, "which", return_value="ffmpeg"):
                with patch.object(app, "search_track", side_effect=search):
                    with patch.object(app, "save_track", return_value={"status": "downloaded"}) as save:
                        with redirect_stdout(io.StringIO()) as stdout:
                            code = app.main(["--file", str(batch), "--output", str(Path(folder) / "output"), "--json"])
            self.assertEqual(searches, ["youtube-music", "soundcloud"])
            self.assertEqual([call.args[0] for call in save.call_args_list], ["soundcloud", "soundcloud"])
            self.assertEqual(code, 1)
            summary = json.loads(stdout.getvalue().splitlines()[-1])
            self.assertEqual(summary["downloaded"], 2)
            self.assertEqual(summary["errors"], 1)

    def test_default_output_is_readable_and_folder_alias_is_used(self):
        youtube = {"id": "A" * 11, "track_url": "https://music.youtube.com/watch?v=" + "A" * 11,
                   "platform": "youtube-music"}
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as stdout:
            with patch.object(app.shutil, "which", return_value="ffmpeg"):
                with patch.object(app, "search_track", return_value=youtube):
                    with patch.object(app, "save_track", return_value={"status": "downloaded", "path": str(Path(folder) / "Artist - Song.mp3")}) as save:
                        code = app.main(["Artist - Song", "--folder", folder])
        self.assertEqual(code, 0)
        self.assertEqual(save.call_args.args[0], "youtube-music")
        self.assertEqual(save.call_args.args[3], Path(folder).resolve())
        self.assertIn("[1/1] Artist - Song", stdout.getvalue())
        self.assertIn("Consultando YouTube Music", stdout.getvalue())
        self.assertIn("Guardada:", stdout.getvalue())
        self.assertIn("1 descargadas", stdout.getvalue())
        self.assertNotIn('{"', stdout.getvalue())

    def test_human_output_keeps_error_and_skip_legible(self):
        with redirect_stdout(io.StringIO()) as stdout:
            app.emit({"status": "error", "error": "No hay coincidencia."})
            app.emit({"status": "skipped", "path": "/music/Artist - Song.mp3"})
        self.assertIn("Error: No hay coincidencia.", stdout.getvalue())
        self.assertIn("Ya descargada: /music/Artist - Song.mp3", stdout.getvalue())

    def test_json_output_has_no_human_progress(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as stdout:
            with patch.object(app.shutil, "which", return_value="ffmpeg"):
                with patch.object(app, "search_track", return_value={
                    "platform": "soundcloud", "id": "123", "track_url": SOUNDCLOUD,
                    "fallback_from": "youtube-music",
                }):
                    with patch.object(app, "save_track", return_value={
                        "status": "downloaded", "platform": "soundcloud", "fallback_from": "youtube-music",
                    }):
                        code = app.main(["Artist - Song", "--folder", folder, "--json"])
        self.assertEqual(code, 0)
        records = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([record["status"] for record in records], ["downloaded", "summary"])
        self.assertNotIn("Consultando", stdout.getvalue())
        self.assertEqual(records[-1]["failed_tracks"], [])

    def test_final_summary_names_failed_searches_in_human_and_json_modes(self):
        track = {"platform": "youtube-music", "id": "A" * 11, "track_url": "https://music.youtube.com/watch?v=" + "A" * 11}
        for json_mode in (False, True):
            with self.subTest(json_mode=json_mode):
                with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as stdout:
                    with patch.object(app.shutil, "which", return_value="ffmpeg"):
                        with patch.object(app, "search_track", side_effect=[track, app.MatchError("no match")]):
                            with patch.object(app, "save_track", return_value={"status": "downloaded", "path": str(Path(folder) / "Good.mp3")}):
                                args = ["1. Artist - Good", "2.* Artist - Missing", "--folder", folder]
                                code = app.main(args + (["--json"] if json_mode else []))
                self.assertEqual(code, 1)
                text = stdout.getvalue()
                if json_mode:
                    summary = json.loads(text.splitlines()[-1])
                    self.assertEqual(summary["failed_tracks"], ["Artist - Missing"])
                    self.assertEqual(summary["errors"], 1)
                else:
                    failures = text.split("Canciones con errores:")[-1]
                    self.assertIn("Artist - Missing", failures)
                    self.assertNotIn("Artist - Good", failures)

    def test_terminal_selection_can_skip_without_becoming_an_error(self):
        track = {"platform": "youtube-music", "id": "A" * 11, "track_url": "https://music.youtube.com/watch?v=" + "A" * 11}
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as stdout:
            with patch.object(app.sys.stdin, "isatty", return_value=True):
                with patch.object(app.shutil, "which", return_value="ffmpeg"):
                    with patch.object(app, "search_track", side_effect=[app.SelectionSkipped(), track]) as search:
                        with patch.object(app, "save_track", return_value={"status": "downloaded", "path": str(Path(folder) / "Song.mp3")}):
                            code = app.main(["Artist - Skip", "Artist - Song", "--folder", folder])
        self.assertEqual(code, 0)
        self.assertIs(search.call_args_list[0].kwargs["select"], app.choose_option)
        self.assertIn("Saltada por elección", stdout.getvalue())
        self.assertIn("1 descargadas · 1 omitidas · 0 errores", stdout.getvalue())
        self.assertNotIn("Canciones con errores:", stdout.getvalue())

    def test_yes_and_json_disable_terminal_prompts(self):
        track = {"platform": "youtube-music", "id": "A" * 11, "track_url": "https://music.youtube.com/watch?v=" + "A" * 11}
        for flag in ("--yes", "--json"):
            with self.subTest(flag=flag):
                with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
                    with patch.object(app.sys.stdin, "isatty", return_value=True):
                        with patch.object(app.shutil, "which", return_value="ffmpeg"):
                            with patch.object(app, "search_track", return_value=track) as search:
                                with patch.object(app, "save_track", return_value={"status": "downloaded"}):
                                    code = app.main(["Artist - Song", "--folder", folder, flag])
                self.assertEqual(code, 0)
                self.assertEqual(search.call_args.kwargs, {})

    def test_explicit_spotify_link_is_not_replaced_by_soundcloud(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            with patch.object(app.shutil, "which", return_value="ffmpeg"):
                with patch.object(app, "spotify_tracks", return_value=[{"id": "123", "track_url": SPOTIFY}]):
                    with patch.object(app, "save_track", side_effect=app.MatchError("no audio")):
                        with patch.object(app, "search_track") as search:
                            code = app.main([SPOTIFY, "--output", folder])
        self.assertEqual(code, 1)
        search.assert_not_called()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg no instalado")
class AudioTests(unittest.TestCase):
    def test_generated_audio_and_wrong_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            for fmt in ("mp3", "wav"):
                with self.subTest(fmt=fmt):
                    audio = Path(folder) / f"audio.{fmt}"
                    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                                    "sine=frequency=440:duration=1", str(audio)], check=True)
                    self.assertAlmostEqual(app.validate_audio(audio, 1), 1, delta=.1)
                    with self.assertRaises(ValueError):
                        app.validate_audio(audio, 180)

    def test_corrupt_audio_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "audio.mp3"
            audio.write_bytes(b"not audio")
            with self.assertRaises(subprocess.CalledProcessError):
                app.validate_audio(audio, 180)


if __name__ == "__main__":
    unittest.main()
