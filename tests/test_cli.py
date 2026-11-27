import pytest

from clawtube import cli
from clawtube.core.errors import NoCaptionsAvailable
from clawtube.core.state import RunState


def test_parser_defaults():
    args = cli.build_parser().parse_args(["playlist", "PLabc"])
    assert args.concurrency == 4 and args.formats == "md" and not args.no_resume
    args = cli.build_parser().parse_args(["video", "x", "--langs", "tr,en", "--whisper-fallback"])
    assert args.langs == "tr,en" and args.whisper_fallback


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_langs_arg():
    assert cli._langs_arg("tr, en,") == ("tr", "en")
    assert cli._langs_arg("") == ()


def test_video_rejects_playlist(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["video", "https://www.youtube.com/playlist?list=PLFsQleAmcz5wg8-8f3ryd8LWL5x82vGey", "--out", str(tmp_path)])
    assert e.value.code == 1


def test_playlist_rejects_video(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["playlist", "dQw4w9WgXcQ", "--out", str(tmp_path)])
    assert e.value.code == 1


def test_video_happy_path(tmp_path, monkeypatch, meta, result):
    monkeypatch.setattr(cli, "fetch_transcript", lambda *a: result)
    monkeypatch.setattr(cli, "get_video_meta", lambda v, fallback=False: meta)
    with pytest.raises(SystemExit) as e:
        cli.main(["video", "dQw4w9WgXcQ", "--out", str(tmp_path), "--formats", "md,srt"])
    assert e.value.code == 0
    assert list(tmp_path.glob("*.md")) and list(tmp_path.glob("*.srt"))


def test_video_uses_cache_second_time(tmp_path, monkeypatch, meta, result):
    calls = []
    monkeypatch.setattr(cli, "fetch_transcript", lambda *a: calls.append(1) or result)
    monkeypatch.setattr(cli, "get_video_meta", lambda v, fallback=False: meta)
    for _ in range(2):
        with pytest.raises(SystemExit):
            cli.main(["video", "dQw4w9WgXcQ", "--out", str(tmp_path)])
    assert len(calls) == 1


def test_video_failure_exit_code(tmp_path, monkeypatch):
    def boom(*a):
        raise NoCaptionsAvailable("nope")

    monkeypatch.setattr(cli, "fetch_transcript", boom)
    with pytest.raises(SystemExit) as e:
        cli.main(["video", "dQw4w9WgXcQ", "--out", str(tmp_path)])
    assert e.value.code == 1


@pytest.mark.parametrize("failed,code", [(False, 0), (True, 2)])
def test_playlist_exit_codes(tmp_path, monkeypatch, failed, code):
    def fake_extract(ref_id, out, **kw):
        s = RunState(out)
        s.mark("a", "failed" if failed else "done", error="x" if failed else None)
        return s

    monkeypatch.setattr(cli, "extract_playlist", fake_extract)
    with pytest.raises(SystemExit) as e:
        cli.main(["playlist", "PLFsQleAmcz5wg8-8f3ryd8LWL5x82vGey", "--out", str(tmp_path)])
    assert e.value.code == code


def test_channel_command_resolves_videos_tab(tmp_path, monkeypatch):
    seen = {}

    def fake_extract(source, out, **kw):
        seen.update(source=source, **kw)
        return RunState(out)

    monkeypatch.setattr(cli, "extract_playlist", fake_extract)
    with pytest.raises(SystemExit) as e:
        cli.main(["channel", "https://www.youtube.com/@Naval", "--out", str(tmp_path), "--limit", "5"])
    assert e.value.code == 0
    assert seen["source"] == "https://www.youtube.com/@Naval/videos"
    assert seen["limit"] == 5


def test_channel_rejects_non_channel(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["channel", "dQw4w9WgXcQ", "--out", str(tmp_path)])
    assert e.value.code == 1


def test_playlist_command_points_channels_to_channel_command(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["playlist", "https://www.youtube.com/@Naval", "--out", str(tmp_path)])
    assert e.value.code == 1
    assert "clawtube channel" in capsys.readouterr().out


def test_listing_failure_is_reported_not_a_traceback(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("Private playlist")

    monkeypatch.setattr(cli, "extract_playlist", boom)
    with pytest.raises(SystemExit) as e:
        cli.main(["playlist", "PLFsQleAmcz5wg8-8f3ryd8LWL5x82vGey", "--out", str(tmp_path)])
    assert e.value.code == 1


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "clawtube" in capsys.readouterr().out


def test_cookie_flags_reach_yt_dlp_options(tmp_path, monkeypatch, meta, result):
    from clawtube.core import metadata

    monkeypatch.setattr(cli, "fetch_transcript", lambda *a: result)
    monkeypatch.setattr(cli, "get_video_meta", lambda v, fallback=False: meta)
    with pytest.raises(SystemExit):
        cli.main(["video", "dQw4w9WgXcQ", "--out", str(tmp_path), "--cookies", "c.txt", "--cookies-from-browser", "chrome"])
    opts = metadata.ydl_opts()
    assert opts["cookiefile"] == "c.txt"
    assert opts["cookiesfrombrowser"] == ("chrome",)


def test_playlist_accepts_watch_url_with_list(tmp_path, monkeypatch):
    seen = {}

    def fake_bulk(source, args):
        seen["source"] = source
        return 0

    monkeypatch.setattr(cli, "_run_bulk", fake_bulk)
    with pytest.raises(SystemExit) as e:
        cli.main(["playlist", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijklm", "--out", str(tmp_path)])
    assert e.value.code == 0 and seen["source"] == "PLabcdefghijklm"
