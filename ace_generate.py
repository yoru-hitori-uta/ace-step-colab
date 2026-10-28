"""ACE-Step 1.5 で、Google ドライブの歌詞ファイルから曲を作る（Google Colab 用）。

ノートブック（ace_step_colab.ipynb）から、ACE-Step の仮想環境の中で実行される:
    uv run python ace_generate.py --drive /content/drive/MyDrive/ace-step --repo /content/ACE-Step-1.5

- ドライブの <drive>/lyrics/*.txt を1曲1ファイルとして読む
- 1曲につき --takes 本（既定3本）作り、<drive>/output/<曲名>/take1.flac … に保存する
- 各テイクの設定（シード・スタイル・BPM など）を take1.json … に記録する
- すでにあるテイクは作らない（途中で止まっても、もう一度実行すれば続きから作る）

歌詞ファイルの書き方（先頭の「---」より上が設定。どれも省略できる）:
    タイトル: 午前二時の既読
    スタイル: 切ないバラード, 女性ボーカル, ピアノ
    BPM: 70
    長さ: 90          # 秒。省略すると歌詞の長さから自動
    キー: Am          # 省略すると自動
    言語: ja          # 歌の言語（ja / en など）。省略すると ja
    シード: 1, 2, 3   # テイクごとのシード。省略するとランダム（使った値は記録される）
    ---
    [Verse]
    歌詞…
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Colab のノートブックから起動すると、グラフ表示用の設定（MPLBACKEND=module://matplotlib_inline...）が
# 引き継がれ、ACE-Step の環境では読み込めずに止まる。画面に描かないので、ファイル出力用に固定する
os.environ["MPLBACKEND"] = "Agg"

HEADER_KEYS = {
    "タイトル": "title", "曲名": "title", "title": "title",
    "スタイル": "style", "ジャンル": "style", "雰囲気": "style", "style": "style", "caption": "style",
    "bpm": "bpm", "テンポ": "bpm",
    "長さ": "duration", "duration": "duration",
    "キー": "key", "key": "key",
    "拍子": "timesig", "time": "timesig",
    "言語": "language", "language": "language",
    "シード": "seeds", "seed": "seeds", "seeds": "seeds",
}
DEFAULT_STYLE = "emotional japanese pop ballad, female vocal, piano"
SEPARATOR = "---"


@dataclass
class Song:
    path: Path
    title: str
    lyrics: str
    style: str = DEFAULT_STYLE
    bpm: int | None = None
    duration: float = -1.0
    key: str = ""
    timesig: str = ""
    language: str = "ja"
    seeds: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def safe_name(title: str) -> str:
    """フォルダ名に使えない文字を全角に置き換える（Windows でも開けるように）。"""
    table = str.maketrans({":": "：", "/": "／", "\\": "＼", "|": "｜", "?": "？", "*": "＊", '"': "”",
                           "<": "＜", ">": "＞"})
    name = re.sub(r"[\x00-\x1f]", "", title.translate(table)).strip().rstrip(".")
    return name or "untitled"


def parse_song(path: Path) -> Song:
    """歌詞ファイルを読む。先頭の「---」より上を設定、下を歌詞とみなす（「---」がなければ全部歌詞）。"""
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    lines = text.split("\n")
    head, body = [], lines
    if SEPARATOR in [ln.strip() for ln in lines]:
        i = [ln.strip() for ln in lines].index(SEPARATOR)
        head, body = lines[:i], lines[i + 1:]
    song = Song(path=path, title=path.stem, lyrics="\n".join(body).strip())
    for raw in head:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([^:：]+)[:：]\s*(.*)$", line)
        if not m:
            song.problems.append(f"設定の行が読めません: {raw.strip()}")
            continue
        key = HEADER_KEYS.get(m.group(1).strip().lower()) or HEADER_KEYS.get(m.group(1).strip())
        value = m.group(2).strip()
        if not key:
            song.problems.append(f"知らない設定です（無視します）: {m.group(1).strip()}")
            continue
        try:
            if key == "bpm":
                song.bpm = int(float(value)) if value else None
                if song.bpm is not None and not 30 <= song.bpm <= 300:
                    raise ValueError("30〜300")
            elif key == "duration":
                song.duration = float(value) if value else -1.0
                if song.duration > 0 and not 10 <= song.duration <= 600:
                    raise ValueError("10〜600 秒")
            elif key == "seeds":
                song.seeds = [int(s) for s in re.split(r"[,\s、]+", value) if s]
            else:
                setattr(song, key, value)
        except ValueError as e:
            song.problems.append(f"{m.group(1).strip()} の値が正しくありません（{value}。{e}）")
    if not song.lyrics:
        song.problems.append("歌詞がありません")
    return song


def take_paths(out_dir: Path, n: int, fmt: str) -> tuple[Path, Path]:
    return out_dir / f"take{n}.{fmt}", out_dir / f"take{n}.json"


def missing_takes(out_dir: Path, takes: int, fmt: str) -> list[int]:
    """まだ作っていないテイク番号（音声と記録の両方がそろっていれば作成済み）。"""
    return [n for n in range(1, takes + 1) if not all(p.is_file() for p in take_paths(out_dir, n, fmt))]


def write_song_files(song: Song, out_dir: Path) -> None:
    """取り込み用に、歌詞（設定を除いたもの）と曲の設定を出力フォルダに置く。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "lyrics.txt").write_text(song.lyrics + "\n", encoding="utf-8")
    info = {"title": song.title, "style": song.style, "bpm": song.bpm, "duration": song.duration,
            "key": song.key, "timesig": song.timesig, "language": song.language, "source": song.path.name}
    (out_dir / "song.json").write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --- ACE-Step の読み込み（Colab の GPU 上でだけ使う） ---


@dataclass
class Engine:
    dit: object
    llm: object
    model: str
    lm: str | None
    notes: list[str]


def force_float32(dit) -> None:
    """音楽モデルを 32bit で動かす（T4 など古い GPU の 16bit では計算があふれて NaN になるため）。

    ACE-Step は古い GPU では float16 に固定する。読み込んだあとで本体を float32 に変え、
    CPU から出し入れする部品（VAE・テキストエンコーダ）も float32 で載るようにする。
    """
    import torch

    dit.dtype = torch.float32
    if getattr(dit, "model", None) is not None:
        dit.model = dit.model.float()
    if getattr(dit, "silence_latent", None) is not None:
        dit.silence_latent = dit.silence_latent.float()
    for name in ("vae", "text_encoder"):
        part = getattr(dit, name, None)
        if part is not None:
            part.float()
    dit._get_vae_dtype = lambda device=None: torch.float32  # VAE も古い GPU では float16 に固定されるため


def load_engine(repo: Path, model: str, lm: str | None, try_xl: bool, precision: str = "auto") -> Engine:
    import torch
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler

    notes = []
    major, minor = torch.cuda.get_device_capability(0)
    name = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    old_gpu = major < 8  # T4 など。bfloat16 が使えない
    notes.append(f"GPU: {name}（{vram:.1f}GB, compute {major}.{minor}）")
    if try_xl:
        model = model.replace("acestep-v15-", "acestep-v15-xl-") if "-xl-" not in model else model
    xl = "-xl-" in model
    fp32 = precision == "fp32" or (precision == "auto" and old_gpu)
    offload = xl or vram < 20
    # 32bit にするときは 8bit 圧縮（量子化）を使わない（圧縮した重みは 32bit に戻せない）
    quant = None if fp32 else ("int8_weight_only" if xl or vram < 20 else None)

    dit = AceStepHandler()
    status, ok = dit.initialize_service(project_root=str(repo), config_path=model, device="cuda",
                                        offload_to_cpu=offload, quantization=quant)
    if not ok:
        raise RuntimeError(f"音楽モデル {model} を読み込めませんでした: {status}")
    if fp32:
        force_float32(dit)
    notes.append(f"音楽モデル: {model}（CPU退避={offload}, 量子化={quant}, 精度={'32bit' if fp32 else '自動'}）")

    llm = LLMHandler()
    if lm:
        from acestep.model_downloader import ensure_lm_model

        ckpt = repo / "checkpoints"
        dl_ok, dl_msg = ensure_lm_model(lm, ckpt)
        # T4 など古い GPU では、作曲用の言語モデルを標準の方式（pt）と 32bit で動かす
        backend, dtype = ("pt", torch.float32) if old_gpu else ("vllm", None)
        status, ok = llm.initialize(checkpoint_dir=str(ckpt), lm_model_path=lm, backend=backend, device="cuda",
                                    offload_to_cpu=offload, dtype=dtype) if dl_ok else (dl_msg, False)
        if ok:
            notes.append(f"作曲用の言語モデル: {lm}（{backend}）")
        else:
            notes.append(f"作曲用の言語モデルを使わずに続けます（{status}）")
            lm = None
    return Engine(dit, llm, model, lm, notes)


def generate_take(engine: Engine, song: Song, seed: int, fmt: str, dest: Path) -> dict:
    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    turbo = "turbo" in engine.model
    params = GenerationParams(
        task_type="text2music", caption=song.style, lyrics=song.lyrics, vocal_language=song.language or "ja",
        bpm=song.bpm, keyscale=song.key, timesignature=song.timesig, duration=song.duration,
        inference_steps=8 if turbo else 50, shift=3.0 if turbo else 1.0, seed=seed,
        thinking=bool(engine.lm),
        use_cot_caption=False,  # 書いたスタイルを言語モデルに書き換えさせない
        use_cot_language=False,  # 言語は歌詞ファイルの指定に従う
    )
    config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[seed], audio_format=fmt)
    with tempfile.TemporaryDirectory() as tmp:
        started = time.time()
        result = generate_music(engine.dit, engine.llm, params, config, save_dir=tmp)
        if not result.success or not result.audios:
            raise RuntimeError(result.error or result.status_message or "生成に失敗しました")
        audio = result.audios[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")  # 書き込み途中のファイルを完成品と取り違えない
        shutil.copyfile(audio["path"], part)
        part.replace(dest)
        used = audio.get("params") or {}
        return {
            "seed": seed, "style": song.style, "bpm": song.bpm, "duration": song.duration, "key": song.key,
            "language": song.language, "model": engine.model, "lm": engine.lm,
            "precision": str(getattr(engine.dit, "dtype", "")),
            "inference_steps": params.inference_steps, "format": fmt, "sample_rate": audio.get("sample_rate"),
            "elapsed_sec": round(time.time() - started, 1),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model_params": {k: v for k, v in used.items() if isinstance(v, (str, int, float, bool, type(None)))},
        }


DONE_DIR = "済み"  # 作り終わった歌詞ファイルの移動先（次回からは読まない）
LAST_RUN = "_last_run_songs.json"  # ⑥ で聴く曲（今回の実行で作った曲）の一覧


def retire_lyrics(path: Path) -> Path:
    """作り終わった歌詞ファイルを lyrics/済み/ へ移す（同じ名前があれば番号を付ける）。"""
    done = path.parent / DONE_DIR
    done.mkdir(exist_ok=True)
    dest = done / path.name
    n = 2
    while dest.exists():
        dest = done / f"{path.stem}_{n}{path.suffix}"
        n += 1
    path.replace(dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drive", type=Path, required=True, help="ドライブの ace-step フォルダ")
    ap.add_argument("--repo", type=Path, required=True, help="ACE-Step-1.5 を置いたフォルダ")
    ap.add_argument("--model", default="acestep-v15-turbo")
    ap.add_argument("--lm", default="acestep-5Hz-lm-0.6B", help="作曲用の言語モデル（none で使わない）")
    ap.add_argument("--takes", type=int, default=3)
    ap.add_argument("--format", default="flac", choices=["flac", "wav", "mp3"])
    ap.add_argument("--try-xl", action="store_true", help="XL（4B）モデルを試す")
    ap.add_argument("--precision", default="auto", choices=["auto", "fp32", "fp16"],
                    help="計算の精度（auto: T4 など古い GPU では 32bit）")
    ap.add_argument("--dry-run", action="store_true", help="作る予定を表示するだけ")
    args = ap.parse_args(argv)

    lyrics_dir, out_root = args.drive / "lyrics", args.drive / "output"
    files = sorted(p for p in lyrics_dir.glob("*.txt") if not p.name.startswith(("_", ".")))
    if not files:
        print(f"歌詞ファイルがありません: {lyrics_dir}")
        return 1
    jobs = []
    for f in files:
        song = parse_song(f)
        for p in song.problems:
            print(f"  [{f.name}] 注意: {p}")
        if not song.lyrics:
            continue
        out_dir = out_root / safe_name(song.title)
        todo = missing_takes(out_dir, args.takes, args.format)
        print(f"■ {song.title}: " + (f"テイク {todo} を作ります" if todo else "作成済み（歌詞を「済み」へ移します）"))
        if todo:
            jobs.append((song, out_dir, todo))
        elif not args.dry_run:
            retire_lyrics(f)
    if args.dry_run or not jobs:
        return 0
    (out_root / LAST_RUN).parent.mkdir(parents=True, exist_ok=True)
    (out_root / LAST_RUN).write_text(json.dumps([out_dir.name for _, out_dir, _ in jobs], ensure_ascii=False) + "\n",
                                     encoding="utf-8")

    lm = None if args.lm.lower() in ("", "none", "なし") else args.lm
    engine = load_engine(args.repo, args.model, lm, args.try_xl, args.precision)
    for n in engine.notes:
        print(n)
    failed = 0
    for song, out_dir, todo in jobs:
        write_song_files(song, out_dir)
        for n in todo:
            seed = song.seeds[n - 1] if len(song.seeds) >= n else random.randint(1, 2 ** 31 - 1)
            audio_path, info_path = take_paths(out_dir, n, args.format)
            print(f"  {song.title} テイク{n}（シード {seed}）を作成中…", flush=True)
            try:
                info = generate_take(engine, song, seed, args.format, audio_path)
            except Exception as e:  # noqa: BLE001 - 1本の失敗で全体を止めない（次回の実行で作り直す）
                failed += 1
                print(f"  → 失敗しました（次に実行したとき作り直します）: {type(e).__name__}: {e}")
                continue
            finally:
                try:
                    import torch

                    torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001
                    pass
            info_path.write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"  → 保存しました: {audio_path}（{info.get('elapsed_sec')} 秒）", flush=True)
        if not missing_takes(out_dir, args.takes, args.format):
            print(f"  歌詞を「{DONE_DIR}」フォルダへ移しました（次回は作りません）: {retire_lyrics(song.path).name}")
    print("完了" if not failed else f"完了（失敗 {failed} 本。もう一度実行すると作り直します）")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
