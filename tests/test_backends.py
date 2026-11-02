import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ytscloud as app


class BackendTests(unittest.TestCase):
    def setUp(self):
        cache_home = patch.object(Path, "home", return_value=Path(self.cache.name))
        cache_home.start()
        self.addCleanup(cache_home.stop)

    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.TemporaryDirectory()
        with patch.object(Path, "home", return_value=Path(cls.cache.name)):
            try:
                from spotdl.providers.audio import YouTubeMusic
                from spotdl.types.result import Result
                from spotdl.types.song import Song
                from spotdl.utils.spotify import SpotifyClient
                from yt_dlp import YoutubeDL
                from ytmusicapi import YTMusic
            except ImportError as error:
                cls.cache.cleanup()
                raise unittest.SkipTest(f"Instala las dependencias: {error}")
        cls.provider = YouTubeMusic
        cls.result = Result
        cls.song = Song
        cls.spotify = SpotifyClient
        cls.ydl = YoutubeDL
        cls.ytmusic = YTMusic

    @classmethod
    def tearDownClass(cls):
        cls.cache.cleanup()

    def test_spotify_pagination_and_missing_track(self):
        good = {"track": {"id": "A" * 22, "type": "track"}}
        with patch.object(self.spotify, "_instance") as client:
            client.playlist_items.return_value = {"items": [good], "next": "page2"}
            client.next.return_value = {"items": [{"track": None}], "next": None}
            tracks = list(app.spotify_tracks("https://open.spotify.com/playlist/" + "B" * 22))
        self.assertEqual(tracks[0]["id"], "A" * 22)
        self.assertIn("error", tracks[1])
        client.next.assert_called_once()

    def test_spotify_partial_page_failure_is_reported(self):
        with patch.object(self.spotify, "_instance") as client:
            client.playlist_items.return_value = {"items": [], "next": "page2"}
            client.next.return_value = None
            with self.assertRaisesRegex(ValueError, "completa"):
                list(app.spotify_tracks("https://open.spotify.com/playlist/" + "B" * 22))

    def test_matching_uses_real_spotdl_types(self):
        song = self.song.from_missing_data(
            name="Song", artist="Artist", artists=["Artist"], duration=180,
            song_id="A" * 22, album_name="Album",
        )
        result = self.result(
            source="YouTubeMusic", url="https://music.youtube.com/watch?v=test",
            verified=True, name="Song", duration=180, author="Artist",
            result_id="test", artists=("Artist",), album="Album",
        )
        with patch.object(self.provider, "_create_client"):
            with patch.object(self.provider, "get_results", return_value=[result]):
                self.assertEqual(app.spotify_source(song), result.url)

    def test_named_search_uses_youtube_music_without_spotify(self):
        with patch.object(app, "spotify_client") as spotify:
            with patch.object(self.ytmusic, "search", return_value=[{
                "videoId": "_adyojG3-ws", "resultType": "song", "title": "Song",
                "artists": [{"name": "Artist"}], "duration_seconds": 180,
            }]) as search:
                track = app.search_track(app.parse_entry("Artist - Song"))
        spotify.assert_not_called()
        search.assert_called_once_with("Artist - Song", filter="songs", limit=20, ignore_spelling=True)
        self.assertEqual(track["track_url"], "https://music.youtube.com/watch?v=_adyojG3-ws")
        self.assertEqual(track["platform"], "youtube-music")

    def test_named_soundcloud_search_preserves_edit(self):
        with patch.object(self.ydl, "extract_info", return_value={"entries": [{
            "id": "123", "title": "Artist - Song (DJ Edit)", "uploader": "DJ",
            "webpage_url": "https://soundcloud.com/dj/song-edit",
        }]}) as extract:
            track = app.search_track(app.parse_entry("19.* Artist - Song [DJ Edit]"))
        extract.assert_called_once_with("scsearch20:artist song dj edit", download=False)
        self.assertEqual(track["track_url"], "https://soundcloud.com/dj/song-edit")

    def test_soundcloud_offers_different_title_for_manual_confirmation(self):
        title = "PERCOSEX (REGGAETON REMIX) Yung Beef x Cris MJ"
        select = Mock(return_value=0)
        with patch.object(self.ydl, "extract_info", return_value={"entries": [{
            "id": "123", "title": title, "uploader": "DJ", "duration": 180,
            "webpage_url": "https://soundcloud.com/dj/percosex-remix",
        }]}), patch("sys.stdout"):
            track = app.search_track(app.parse_entry("*Cris Mj, Yung Beef - PERCOSEX (Reggaeton Edit)"), select=select)
        self.assertEqual(track["title"], title)
        self.assertEqual(track["selection"], "manual")
        self.assertNotIn("requested_title", track)
        self.assertEqual(len(select.call_args.args[0]), 1)
        self.assertIn(title, select.call_args.args[0][0])
        self.assertIn("180 s", select.call_args.args[0][0])

    def test_soundcloud_offers_all_results_even_when_first_match_is_reliable(self):
        select = Mock(return_value=1)
        with patch.object(self.ydl, "extract_info", return_value={"entries": [
            {"id": "1", "title": "Gotay El Autentiko - Real Love", "uploader": "Gotay",
             "webpage_url": "https://soundcloud.com/gotay/real-love"},
            {"id": "2", "title": "Real Love - Otra publicación", "uploader": "Other",
             "webpage_url": "https://soundcloud.com/other/real-love"},
        ]}), patch("sys.stdout"):
            track = app.search_track(app.parse_entry('* Gotay "El Autentiko" - Real Love'), select=select)
        self.assertEqual(track["id"], "2")
        self.assertEqual(track["selection"], "manual")
        self.assertEqual(len(select.call_args.args[0]), 2)
        select.assert_called_once()

    def test_youtube_offers_unreliable_results_before_using_soundcloud(self):
        select = Mock(return_value=0)
        with patch.object(self.ytmusic, "search", return_value=[{
            "resultType": "song", "videoId": "A" * 11, "title": "Other Song (Remix)",
            "artists": [{"name": "Other Artist"}], "duration_seconds": 180,
        }]):
            with patch.object(self.ydl, "extract_info") as soundcloud, patch("sys.stdout"):
                track = app.search_track(app.parse_entry("Artist - Song"), select=select)
        soundcloud.assert_not_called()
        self.assertEqual(track["title"], "Other Song (Remix)")
        self.assertEqual(track["platform"], "youtube-music")
        self.assertEqual(track["selection"], "manual")
        self.assertIn("Other Artist", select.call_args.args[0][0])

    def test_unreliable_youtube_result_can_be_skipped_without_soundcloud(self):
        with patch.object(self.ytmusic, "search", return_value=[{
            "resultType": "song", "videoId": "A" * 11, "title": "Other Song",
            "artists": [{"name": "Other Artist"}], "duration_seconds": 180,
        }]):
            with patch.object(self.ydl, "extract_info") as soundcloud, patch("sys.stdout"):
                with self.assertRaises(app.SelectionSkipped):
                    app.search_track(app.parse_entry("Artist - Song"), select=Mock(side_effect=app.SelectionSkipped()))
        soundcloud.assert_not_called()

    def test_user_can_search_soundcloud_from_unreliable_youtube_menu(self):
        select = Mock(side_effect=[app.SoundCloudRequested(), 0])
        with patch.object(self.ytmusic, "search", return_value=[{
            "resultType": "song", "videoId": "A" * 11, "title": "Other Song",
            "artists": [{"name": "Other Artist"}], "duration_seconds": 180,
        }]):
            with patch.object(self.ydl, "extract_info", return_value={"entries": [{
                "id": "123", "title": "Artist - Song", "uploader": "Artist",
                "webpage_url": "https://soundcloud.com/artist/song",
            }]}) as soundcloud, patch("sys.stdout"):
                track = app.search_track(app.parse_entry("Artist - Song"), select=select)
        self.assertEqual(track["platform"], "soundcloud")
        self.assertEqual(track["fallback_from"], "youtube-music")
        self.assertEqual([call.kwargs["source"] for call in select.call_args_list],
                         ["youtube-music", "soundcloud"])
        soundcloud.assert_called_once()

    def test_user_can_search_soundcloud_from_ambiguous_youtube_menu(self):
        item = {"resultType": "song", "title": "Song", "artists": [{"name": "Artist"}]}
        select = Mock(side_effect=[app.SoundCloudRequested(), 0])
        with patch.object(self.ytmusic, "search", return_value=[
            {**item, "videoId": "A" * 11, "duration_seconds": 180},
            {**item, "videoId": "B" * 11, "duration_seconds": 190},
        ]):
            with patch.object(self.ydl, "extract_info", return_value={"entries": [{
                "id": "123", "title": "Artist - Song", "uploader": "Artist",
                "webpage_url": "https://soundcloud.com/artist/song",
            }]}), patch("sys.stdout"):
                track = app.search_track(app.parse_entry("Artist - Song"), select=select)
        self.assertEqual(track["id"], "123")
        self.assertEqual(track["fallback_from"], "youtube-music")

    def test_requested_soundcloud_search_without_results_reports_error(self):
        select = Mock(side_effect=app.SoundCloudRequested())
        with patch.object(self.ytmusic, "search", return_value=[{
            "resultType": "song", "videoId": "A" * 11, "title": "Other Song",
            "artists": [{"name": "Other Artist"}], "duration_seconds": 180,
        }]):
            with patch.object(self.ydl, "extract_info", return_value={"entries": []}) as soundcloud, patch("sys.stdout"):
                with self.assertRaises(app.MatchError):
                    app.search_track(app.parse_entry("Artist - Song"), select=select)
        select.assert_called_once()
        soundcloud.assert_called_once()

    def test_automatic_mode_still_falls_back_when_youtube_results_are_unreliable(self):
        with patch.object(self.ytmusic, "search", return_value=[{
            "resultType": "song", "videoId": "A" * 11, "title": "Other Song",
            "artists": [{"name": "Other Artist"}], "duration_seconds": 180,
        }]):
            with patch.object(self.ydl, "extract_info", return_value={"entries": [{
                "id": "123", "title": "Artist - Song", "uploader": "Artist",
                "webpage_url": "https://soundcloud.com/artist/song",
            }]}):
                track = app.search_track(app.parse_entry("Artist - Song"))
        self.assertEqual(track["platform"], "soundcloud")
        self.assertEqual(track["fallback_from"], "youtube-music")

    def test_soundcloud_version_mismatch_still_fails_in_automatic_mode(self):
        with patch.object(self.ydl, "extract_info", return_value={"entries": [{
            "id": "123", "title": "PERCOSEX (REGGAETON REMIX) Yung Beef x Cris MJ", "uploader": "DJ",
            "webpage_url": "https://soundcloud.com/dj/percosex-remix",
        }]}):
            with self.assertRaises(app.MatchError):
                app.search_track(app.parse_entry("*Cris Mj, Yung Beef - PERCOSEX (Reggaeton Edit)"))

    def test_youtube_fallback_keeps_soundcloud_manual_selection(self):
        with patch.object(self.ytmusic, "search", return_value=[]):
            with patch.object(self.ydl, "extract_info", return_value={"entries": [{
                "id": "123", "title": "Artist - Song (Remix)", "uploader": "Artist",
                "webpage_url": "https://soundcloud.com/artist/song-remix",
            }]}), patch("sys.stdout"):
                track = app.search_track(app.parse_entry("Artist - Song"), select=Mock(return_value=0))
        self.assertEqual(track["platform"], "soundcloud")
        self.assertEqual(track["selection"], "manual")
        self.assertEqual(track["fallback_from"], "youtube-music")

    def test_soundcloud_manual_selection_can_be_skipped(self):
        with patch.object(self.ydl, "extract_info", return_value={"entries": [{
            "id": "123", "title": "Artist - Song (Remix)", "uploader": "Artist",
            "webpage_url": "https://soundcloud.com/artist/song-remix",
        }]}), patch("sys.stdout"):
            with self.assertRaises(app.SelectionSkipped):
                app.search_track(app.parse_entry("* Artist - Song"), select=Mock(side_effect=app.SelectionSkipped()))

    def test_missing_youtube_match_falls_back_to_soundcloud(self):
        with patch.object(self.ytmusic, "search", return_value=[]):
            with patch.object(self.ydl, "extract_info", return_value={"entries": [{
                "id": "123", "title": "Artist - Song", "uploader": "Artist",
                "webpage_url": "https://soundcloud.com/artist/song",
            }]}) as extract:
                track = app.search_track(app.parse_entry("Artist - Song"))
        self.assertEqual(track["platform"], "soundcloud")
        self.assertEqual(track["fallback_from"], "youtube-music")
        extract.assert_called_once_with("scsearch20:artist song", download=False)

    def test_soundcloud_ambiguous_search_requires_selection(self):
        with patch.object(self.ydl, "extract_info", return_value={"entries": [
            {"id": "1", "title": "Artist - Song Titles", "uploader": "Artist",
             "webpage_url": "https://soundcloud.com/artist/first"},
            {"id": "2", "title": "Artist - Song Title", "uploader": "Artist",
             "webpage_url": "https://soundcloud.com/artist/second"},
        ]}):
            with self.assertRaises(app.MatchError):
                app.search_track(app.parse_entry("* Artist - Song Title"))

    def test_soundcloud_ambiguity_allows_selection_of_second_result(self):
        with patch.object(self.ydl, "extract_info", return_value={"entries": [
            {"id": "1", "title": "Artist - Song", "uploader": "Artist",
             "webpage_url": "https://soundcloud.com/artist/first"},
            {"id": "2", "title": "Artist - Song", "uploader": "Artist",
             "webpage_url": "https://soundcloud.com/artist/second"},
        ]}), patch("sys.stdout"):
            track = app.search_track(app.parse_entry("* Artist - Song"), select=Mock(return_value=1))
        self.assertEqual(track["id"], "2")

    def test_youtube_ambiguity_can_fall_back_to_unambiguous_soundcloud_match(self):
        item = {"resultType": "song", "title": "Song", "artists": [{"name": "Artist"}]}
        with patch.object(self.ytmusic, "search", return_value=[
            {**item, "videoId": "A" * 11, "duration_seconds": 180},
            {**item, "videoId": "B" * 11, "duration_seconds": 190},
        ]):
            with patch.object(self.ydl, "extract_info", return_value={"entries": [{
                "id": "123", "title": "Artist - Song", "uploader": "Artist",
                "webpage_url": "https://soundcloud.com/artist/song",
            }]}):
                track = app.search_track(app.parse_entry("Artist - Song"))
        self.assertEqual(track["id"], "123")
        self.assertEqual(track["platform"], "soundcloud")
        self.assertEqual(track["fallback_from"], "youtube-music")

    def test_youtube_ambiguity_allows_selection_of_second_result(self):
        item = {"resultType": "song", "title": "Song", "artists": [{"name": "Artist"}]}
        select = Mock(return_value=1)
        with patch.object(self.ytmusic, "search", return_value=[
            {**item, "videoId": "A" * 11, "duration_seconds": 180, "album": {"name": "Album 1"}},
            {**item, "videoId": "B" * 11, "duration_seconds": 190, "album": {"name": "Album 2"}},
        ]):
            with patch.object(self.ydl, "extract_info") as soundcloud:
                track = app.search_track(app.parse_entry("Artist - Song"), select=select)
        self.assertEqual(track["id"], "B" * 11)
        self.assertIn("Album 2", select.call_args.args[0][1])
        self.assertIn("190 s", select.call_args.args[0][1])
        soundcloud.assert_not_called()

    def test_skipping_youtube_selection_does_not_search_soundcloud(self):
        item = {"resultType": "song", "title": "Song", "artists": [{"name": "Artist"}]}
        with patch.object(self.ytmusic, "search", return_value=[
            {**item, "videoId": "A" * 11, "duration_seconds": 180},
            {**item, "videoId": "B" * 11, "duration_seconds": 190},
        ]):
            with patch.object(self.ydl, "extract_info") as soundcloud:
                with self.assertRaises(app.SelectionSkipped):
                    app.search_track(app.parse_entry("Artist - Song"), select=Mock(side_effect=app.SelectionSkipped()))
        soundcloud.assert_not_called()

    def test_identical_titles_and_durations_do_not_prove_recording_identity(self):
        item = {"resultType": "song", "title": "Song", "artists": [{"name": "Artist"}],
                "duration_seconds": 180, "isExplicit": False}
        with patch.object(self.ytmusic, "search", return_value=[
            {**item, "videoId": "A" * 11, "album": {"name": "Album"}},
            {**item, "videoId": "B" * 11, "album": {"name": "Compilation"}},
        ]):
            with patch.object(self.ydl, "extract_info", return_value={"entries": []}):
                with self.assertRaises(app.MatchError):
                    app.search_track(app.parse_entry("Artist - Song"))

    def test_soundcloud_search_removes_punctuation_and_tries_main_artist(self):
        item = {"id": "123", "title": "Artist, Guest - Song (DJ Edit)",
                "uploader": "DJ", "webpage_url": "https://soundcloud.com/dj/song-edit"}
        with patch.object(self.ydl, "extract_info", side_effect=[{"entries": []}, {"entries": [item]}]) as extract:
            track = app.search_track(app.parse_entry("* Artist, Guest - Song [DJ Edit]"))
        self.assertEqual([call.args[0] for call in extract.call_args_list],
                         ["scsearch20:artist guest song dj edit", "scsearch20:artist song dj edit"])
        self.assertEqual(track["requested_artists"], "Artist, Guest")

    def test_javascript_runtimes_are_enabled_for_ytdlp(self):
        with self.ydl(app.ydl_options()) as ydl:
            self.assertIn("node", ydl.params["js_runtimes"])
            self.assertIn("deno", ydl.params["js_runtimes"])

    def test_live_video_filter_rejects_live_and_upcoming_but_allows_recordings(self):
        with self.ydl({**app.ydl_options(), "match_filter": app.reject_live}) as ydl:
            for info in ({"is_live": True}, {"live_status": "is_upcoming"}):
                with self.subTest(info=info):
                    self.assertIsNotNone(ydl._match_entry({"id": "test", "title": "Test", **info}, silent=True))
            self.assertIsNone(ydl._match_entry({"id": "test", "title": "Test", "live_status": "was_live"}, silent=True))

    def test_starred_search_never_queries_spotify(self):
        with patch.object(app, "spotify_client") as client, patch.object(self.ytmusic, "search") as youtube:
            with patch.object(self.ydl, "extract_info", return_value={"entries": []}):
                with self.assertRaises(ValueError):
                    app.search_track(app.parse_entry("* Artist - Song"))
        client.assert_not_called()
        youtube.assert_not_called()

    def test_youtube_connection_failure_is_not_hidden(self):
        with patch.object(self.ytmusic, "search", side_effect=OSError("connection failed")):
            with patch.object(self.ydl, "extract_info") as extract:
                with self.assertRaises(OSError):
                    app.search_track(app.parse_entry("Artist - Song"))
        extract.assert_not_called()

    def test_soundcloud_playlist_entries(self):
        with patch.object(self.ydl, "extract_info", return_value={"entries": [
            {"id": "123", "url": "https://soundcloud.com/artist/song"},
            {"id": "124", "url": "https://api-v2.soundcloud.com/tracks/124"},
            None,
        ]}):
            tracks = list(app.soundcloud_tracks("https://soundcloud.com/artist/sets/list"))
        self.assertEqual([item.get("id") for item in tracks], ["123", "124", None])
        self.assertIn("error", tracks[-1])

    def test_preview_formats_are_excluded_by_real_selector(self):
        with self.ydl({"quiet": True}) as ydl:
            selector = ydl.build_format_selector(
                "bestaudio[format_id!*=preview]/best[format_id!*=preview]"
            )
            selected = list(selector({"formats": [
                {"format_id": "hls_mp3_preview", "ext": "mp3", "vcodec": "none",
                 "acodec": "mp3", "url": "https://example.com/preview.mp3"},
            ], "has_merged_format": False, "incomplete_formats": False}))
        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()
