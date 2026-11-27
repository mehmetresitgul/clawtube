from clawtube.core import format as fmt


def test_hms():
    assert fmt._hms(5) == "00:05"
    assert fmt._hms(75) == "01:15"
    assert fmt._hms(3725) == "01:02:05"


def test_to_markdown(meta, result):
    md = fmt.to_markdown(meta, result)
    assert md.startswith("# How Transformers Work\n")
    assert "**Channel:** Example Channel" in md
    assert "**Duration:** 01:02:05" in md
    assert "**Transcript source:** timedtext (en)" in md
    assert "## Chapters" in md
    assert "[01:15](https://youtu.be/dQw4w9WgXcQ?t=75) Attention" in md
    assert "**[00:00](https://youtu.be/dQw4w9WgXcQ?t=0)** Welcome back." in md
    assert "**[01:02:05](https://youtu.be/dQw4w9WgXcQ?t=3725)** Late line." in md


def test_to_markdown_without_chapters_or_optional_fields(meta, result):
    meta.chapters, meta.channel, meta.upload_date, meta.duration = [], None, None, None
    md = fmt.to_markdown(meta, result)
    assert "## Chapters" not in md
    assert "unknown" in md


def test_to_srt(result):
    srt = fmt.to_srt(result)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,500\nWelcome back.\n")
    assert "2\n01:02:05,400 --> 01:02:06,400\nLate line." in srt


def test_write_video_outputs(tmp_path, meta, result):
    meta.title = 'Bad/title: "quotes"?'
    path = fmt.write_video_outputs(tmp_path / "out", meta, result, ["md", "srt"])
    assert path.suffix == ".md" and path.exists()
    assert "/" not in path.name and ":" not in path.name
    assert path.with_suffix(".srt").exists()


def test_write_video_outputs_md_only(tmp_path, meta, result):
    path = fmt.write_video_outputs(tmp_path, meta, result, ["md"])
    assert not path.with_suffix(".srt").exists()


def test_combined_markdown(meta, result):
    doc = fmt.combined_markdown([(meta, result), (meta, result)])
    assert doc.startswith("# Playlist transcript (2 videos)")
    assert doc.count("# How Transformers Work") == 2


def test_filename_never_starts_with_dash_or_dot(tmp_path, meta, result):
    meta.title = "-.--Weird title"
    path = fmt.write_video_outputs(tmp_path, meta, result, ["md"])
    assert path.name == f"Weird title_{meta.id}.md"


def test_filename_when_metadata_unavailable(tmp_path, result):
    from clawtube.core.metadata import VideoMeta

    placeholder = VideoMeta("-7J-Gwc9pVg", "-7J-Gwc9pVg", None, None, None, None, None, [])
    path = fmt.write_video_outputs(tmp_path, placeholder, result, ["md"])
    assert path.name == "video_-7J-Gwc9pVg.md"
