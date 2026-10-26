"""ace_generate.py の、GPU を使わない部分のテスト（歌詞ファイルの読み込み・再開・保存）。

    python -m pytest tests
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ace_generate as ag  # noqa: E402


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_example_lyrics_parse():
    song = ag.parse_song(ROOT / "examples" / "lyrics" / "午前二時の既読.txt")
    assert song.title == "午前二時の既読" and song.bpm == 70 and song.duration == 100 and song.language == "ja"
    assert "女性ボーカル" in song.style and song.lyrics.startswith("[Verse]") and not song.problems


def test_header_variants_and_errors(tmp_path):
    p = write(tmp_path / "x.txt", "曲名：夜の歌\nstyle: lofi\nBPM: 999\nシード: 1, 2、3\n謎: 1\n---\n[Verse]\nあ\n")
    song = ag.parse_song(p)
    assert song.title == "夜の歌" and song.style == "lofi" and song.seeds == [1, 2, 3]
    assert song.bpm == 999 or any("BPM" in x for x in song.problems)
    assert any("知らない設定" in x for x in song.problems)


def test_without_separator_everything_is_lyrics(tmp_path):
    song = ag.parse_song(write(tmp_path / "名前だけ.txt", "[Chorus]\nらら\n"))
    assert song.title == "名前だけ" and song.lyrics == "[Chorus]\nらら" and song.style == ag.DEFAULT_STYLE


def test_safe_name():
    assert ag.safe_name('定時退社RTA ～17:30の攻防～') == "定時退社RTA ～17：30の攻防～"
    assert ag.safe_name("a/b?") == "a／b？"


def test_resume_skips_finished_takes(tmp_path, monkeypatch, capsys):
    drive = tmp_path / "ace-step"
    write(drive / "lyrics" / "歌.txt", "タイトル: 歌\n---\n[Verse]\nあいうえお\n")
    out = drive / "output" / "歌"
    write(out / "take1.flac", "x")
    write(out / "take1.json", "{}")
    write(out / "take2.flac", "x")  # 記録がない＝途中で止まった → 作り直す

    made = []

    def fake_generate(engine, song, seed, fmt, dest):
        dest.write_bytes(b"audio")
        made.append((dest.name, seed))
        return {"seed": seed}

    monkeypatch.setattr(ag, "load_engine", lambda *a, **k: ag.Engine(None, None, "m", None, []))
    monkeypatch.setattr(ag, "generate_take", fake_generate)
    assert ag.main(["--drive", str(drive), "--repo", str(tmp_path), "--takes", "3"]) == 0
    assert [m[0] for m in made] == ["take2.flac", "take3.flac"]
    assert json.loads((out / "take3.json").read_text(encoding="utf-8"))["seed"] == made[1][1]
    assert (out / "lyrics.txt").read_text(encoding="utf-8") == "[Verse]\nあいうえお\n"
    assert json.loads((out / "song.json").read_text(encoding="utf-8"))["title"] == "歌"

    made.clear()
    assert ag.main(["--drive", str(drive), "--repo", str(tmp_path), "--takes", "3"]) == 0
    assert made == [] and "作成済み" in capsys.readouterr().out


def test_failure_keeps_going_and_is_retried(tmp_path, monkeypatch):
    drive = tmp_path / "ace-step"
    write(drive / "lyrics" / "歌.txt", "シード: 7, 8\n---\nあいうえお\n")
    calls = []

    def flaky(engine, song, seed, fmt, dest):
        calls.append(seed)
        if seed == 7:
            raise RuntimeError("CUDA out of memory")
        dest.write_bytes(b"a")
        return {"seed": seed}

    monkeypatch.setattr(ag, "load_engine", lambda *a, **k: ag.Engine(None, None, "m", None, []))
    monkeypatch.setattr(ag, "generate_take", flaky)
    assert ag.main(["--drive", str(drive), "--repo", str(tmp_path), "--takes", "2"]) == 2
    assert calls == [7, 8]
    assert ag.missing_takes(drive / "output" / "歌", 2, "flac") == [1]
