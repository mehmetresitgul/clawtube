import sys
import types
from pathlib import Path

import pytest

from clawtube import cli
from clawtube.core import whisper_fallback as wf
from clawtube.core.errors import WhisperFailed


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def _install_fakes(monkeypatch, segments, language="en", audio=True):
    class FakeModel:
        def __init__(self, size, compute_type):
            self.size = size

        def transcribe(self, path, vad_filter):
            return iter(segments), types.SimpleNamespace(language=language)

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)

    def fake_download(video_id, dest):
        if not audio:
            raise WhisperFailed("audio download failed: HTTP Error 403: Forbidden")
        p = Path(dest) / f"{video_id}.mp3"
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(wf, "_download_audio", fake_download)


def test_transcribe_audio(monkeypatch):
    _install_fakes(monkeypatch, [_Seg(0, 2.5, " Hello "), _Seg(2.5, 3, "  "), _Seg(3, 4, "World")], "tr")
    r = wf.transcribe_audio("vid")
    assert (r.source, r.language) == ("whisper", "tr")
    assert [(s.start, s.duration, s.text) for s in r.segments] == [(0, 2.5, "Hello"), (3, 1, "World")]


def test_transcribe_reports_audio_download_failure(monkeypatch):
    _install_fakes(monkeypatch, [], audio=False)
    with pytest.raises(WhisperFailed, match="audio download failed: HTTP Error 403"):
        wf.transcribe_audio("vid")


def test_transcribe_reports_no_speech(monkeypatch):
    _install_fakes(monkeypatch, [_Seg(0, 1, "  ")])
    with pytest.raises(WhisperFailed, match="no speech"):
        wf.transcribe_audio("vid")


def test_transcribe_reports_missing_faster_whisper(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import raises ImportError
    with pytest.raises(WhisperFailed, match="not installed"):
        wf.transcribe_audio("vid")


def test_missing_requirements(monkeypatch):
    monkeypatch.setattr(wf.importlib.util, "find_spec", lambda n: None)
    assert "faster-whisper" in wf.missing_requirements()
    monkeypatch.setattr(wf.importlib.util, "find_spec", lambda n: object())
    monkeypatch.setattr(wf.shutil, "which", lambda n: None)
    assert "ffmpeg" in wf.missing_requirements()
    monkeypatch.setattr(wf.shutil, "which", lambda n: "/usr/bin/ffmpeg")
    assert wf.missing_requirements() is None


@pytest.mark.parametrize("argv", [["video", "dQw4w9WgXcQ"], ["playlist", "PLFsQleAmcz5wg8-8f3ryd8LWL5x82vGey"]])
def test_cli_fails_fast_when_whisper_unavailable(argv, tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "missing_requirements", lambda: "ffmpeg was not found")
    called = []
    monkeypatch.setattr(cli, "fetch_transcript", lambda *a: called.append(1))
    monkeypatch.setattr(cli, "extract_playlist", lambda *a, **k: called.append(1))
    with pytest.raises(SystemExit) as e:
        cli.main(argv + ["--whisper-fallback", "--out", str(tmp_path)])
    assert e.value.code == 1
    assert called == []
