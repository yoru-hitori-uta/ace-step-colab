"""ace_step_colab.ipynb を作る（ノートブックはこのスクリプトから生成する。直接編集しない）。

    python tools/build_notebook.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACE_STEP_COMMIT = "ca1e85f"  # 動作を確認して作った版（2026-08-29）


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text, title=None):
    meta = {"cellView": "form"} if title else {}
    return {"cell_type": "code", "metadata": meta, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


cells = [
    md("""
# 🎵 ACE-Step 1.5 で曲を作る（夜、ひとりで聴く歌）

Google ドライブの **`ace-step/lyrics/`** にある歌詞ファイルから、1曲につき3パターンの曲を作り、
**`ace-step/output/曲名/`** に保存します。途中で止まっても、もう一度実行すれば続きから作ります。

## 使い方（毎回これだけ）
1. 上のメニュー **「ランタイム」→「ランタイムのタイプを変更」→「T4 GPU」→「保存」**（初回だけ。次からは覚えています）
2. 上のメニュー **「ランタイム」→「すべてのセルを実行」**
3. 「Google ドライブへのアクセスを許可」の画面が出たら、自分のアカウントを選んで **「許可」**
4. 終わると、いちばん下に作った曲が並び、その場で聴けます

⏱ 目安: 準備（毎回）5〜10分 ＋ 1曲3パターンで数分。無料の Colab は使える時間に上限があります。
"""),
    code(f"""
#@title ① 設定（ふだんは変えなくて大丈夫）
#@markdown 音楽を作るモデル（無料の T4 では「2B turbo」がおすすめ。XL は動かない可能性があります）
MODEL = "2B turbo（おすすめ・速い）" #@param ["2B turbo（おすすめ・速い）", "2B sft（高品質・遅い）", "XL turbo（試験的・T4では動かないかも）"]
#@markdown 作曲を手伝う言語モデル（BPM やキーを決めたり、構成を考えたりする）
LM = "0.6B（おすすめ）" #@param ["0.6B（おすすめ）", "1.7B（T4ではメモリ不足になるかも）", "使わない"]
#@markdown 1曲あたり何パターン作るか
TAKES = 3 #@param {{type:"integer"}}
#@markdown 保存する形式（flac は wav の半分くらいの大きさで、音質は同じ）
FORMAT = "flac" #@param ["flac", "wav", "mp3"]
#@markdown ドライブの中のフォルダ名
DRIVE_FOLDER = "ace-step" #@param {{type:"string"}}
#@markdown ACE-Step の版（「確認済み」はこのノートブックを作ったときに確かめた版）
ACE_STEP_VERSION = "確認済み（{ACE_STEP_COMMIT}）" #@param ["確認済み（{ACE_STEP_COMMIT}）", "最新"]

MODEL_ID = {{"2B turbo": "acestep-v15-turbo", "2B sft": "acestep-v15-sft", "XL turbo": "acestep-v15-xl-turbo"}}[MODEL.split("（")[0]]
LM_ID = {{"0.6B": "acestep-5Hz-lm-0.6B", "1.7B": "acestep-5Hz-lm-1.7B", "使わない": "none"}}[LM.split("（")[0]]
COMMIT = None if ACE_STEP_VERSION == "最新" else "{ACE_STEP_COMMIT}"
print("設定:", MODEL_ID, "/", LM_ID, "/", TAKES, "パターン /", FORMAT)
""", title=True),
    code("""
#@title ② GPU の確認
if "MODEL_ID" not in globals():
    raise SystemExit("❌ 先に上のセルが実行されていません。メニュー「ランタイム」→「すべてのセルを実行」を押してください"
                     "（1つずつ実行するときは、①から順番に）")
import subprocess
r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True)
if r.returncode != 0:
    raise SystemExit("❌ GPU が使えません。上のメニュー「ランタイム」→「ランタイムのタイプを変更」で「T4 GPU」を選び、"
                     "「保存」してから、もう一度「すべてのセルを実行」してください。"
                     "（無料枠の上限に達したときも GPU が使えなくなります。時間をおいて試してください）")
print("✅ GPU:", r.stdout.strip())
""", title=True),
    code("""
#@title ③ Google ドライブにつなぐ（「許可」を押してください）
import os, shutil, glob
from google.colab import drive
try:
    drive.mount("/content/drive", force_remount=True)
except Exception as e:  # 許可の画面で止まった・許可が足りないとき
    raise SystemExit(
        "❌ Google ドライブにつなげませんでした（" + str(e) + "）
"
        "次のことを確かめて、このセル（③）の左の ▶ を押してやり直してください:
"
        " ・許可の画面では、右上で Colab にログインしているのと同じアカウントを選ぶ
"
        " ・「すべて選択」にチェックを入れてから「続行」を押す（チェックが外れていると失敗します）
"
        " ・画面が出ないときは、ブラウザのポップアップのブロックを解除する
"
        "つながったら、メニュー「ランタイム」→「すべてのセルを実行」をもう一度押してください")
BASE = f"/content/drive/MyDrive/{DRIVE_FOLDER}"
os.makedirs(f"{BASE}/lyrics", exist_ok=True)
os.makedirs(f"{BASE}/output", exist_ok=True)

# このノートブックの部品（生成スクリプトと見本の歌詞）を取ってくる
TOOL = "/content/ace-step-colab"
if not os.path.isdir(TOOL):
    subprocess.run(["git", "clone", "-q", "https://github.com/yoru-hitori-uta/ace-step-colab.git", TOOL], check=True)

# 歌詞フォルダが空なら、動作確認用の見本を1曲入れる
if not glob.glob(f"{BASE}/lyrics/*.txt"):
    for f in glob.glob(f"{TOOL}/examples/lyrics/*.txt"):
        shutil.copy(f, f"{BASE}/lyrics/")
    print("📝 歌詞フォルダが空だったので、見本の歌詞を入れました")
print("✅ ドライブ:", BASE)
for f in sorted(glob.glob(f"{BASE}/lyrics/*.txt")):
    print("   歌詞:", os.path.basename(f))
""", title=True),
    code("""
#@title ④ ACE-Step を準備する（毎回 5〜10 分。モデルは初回の生成時に自動でダウンロード）
if "BASE" not in globals():
    raise SystemExit("❌ 先に上のセルが実行されていません。メニュー「ランタイム」→「すべてのセルを実行」を押してください"
                     "（1つずつ実行するときは、①から順番に）")
REPO = "/content/ACE-Step-1.5"
if not os.path.isdir(REPO):
    subprocess.run(["git", "clone", "-q", "https://github.com/ace-step/ACE-Step-1.5.git", REPO], check=True)
    if COMMIT:
        subprocess.run(["git", "-C", REPO, "checkout", "-q", COMMIT], check=True)
subprocess.run(["pip", "install", "-q", "uv"], check=True)
r = subprocess.run("cd /content/ACE-Step-1.5 && uv sync 2>&1 | tail -n 3", shell=True, text=True, capture_output=True)
print(r.stdout)
if r.returncode != 0:
    raise SystemExit("❌ ACE-Step の準備に失敗しました。上の表示を Claude に伝えてください。")
print("✅ ACE-Step の準備ができました")
""", title=True),
    code("""
#@title ⑤ 曲を作る（作成済みのパターンは飛ばします。止まったら、もう一度「すべてのセルを実行」）
if "REPO" not in globals():
    raise SystemExit("❌ 先に上のセルが実行されていません。メニュー「ランタイム」→「すべてのセルを実行」を押してください"
                     "（1つずつ実行するときは、①から順番に）")
cmd = ["uv", "run", "python", f"{TOOL}/ace_generate.py", "--drive", BASE, "--repo", REPO,
       "--model", MODEL_ID, "--lm", LM_ID, "--takes", str(TAKES), "--format", FORMAT]
if MODEL_ID.startswith("acestep-v15-xl"):
    cmd.append("--try-xl")
proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
log = []
for line in proc.stdout:
    log.append(line)
    # 細かい進み具合の表示は省いて、日本語の案内と重要な行だけ出す
    if any(k in line for k in ("■", "作成中", "保存しました", "失敗", "完了", "注意", "GPU:", "モデル", "Error", "error")):
        print(line, end="")
proc.wait()
open(f"{BASE}/output/_last_run.log", "w", encoding="utf-8").writelines(log)
print("（詳しい記録: ドライブの", f"{DRIVE_FOLDER}/output/_last_run.log）")
""", title=True),
    code("""
#@title ⑥ できた曲を聴く
if "BASE" not in globals():
    raise SystemExit("❌ 先に上のセルが実行されていません。メニュー「ランタイム」→「すべてのセルを実行」を押してください"
                     "（1つずつ実行するときは、①から順番に）")
import json
from IPython.display import Audio, display, Markdown
found = False
for d in sorted(glob.glob(f"{BASE}/output/*/")):
    takes = sorted(glob.glob(f"{d}take*.{FORMAT}"))
    if not takes:
        continue
    display(Markdown(f"### {os.path.basename(d.rstrip('/'))}"))
    for t in takes:
        info_path = t.rsplit(".", 1)[0] + ".json"
        info = json.load(open(info_path, encoding="utf-8")) if os.path.exists(info_path) else {}
        print(os.path.basename(t), "シード", info.get("seed"), "/", info.get("model"), "/", info.get("elapsed_sec"), "秒")
        display(Audio(t))
        found = True
if not found:
    print("まだ曲がありません。⑤ の表示に「失敗」が出ていないか確認してください")
"""),
    md("""
## 作った曲を「夜、ひとりで聴く歌」のツールに入れるには
PC の Google ドライブ（ドライブ for デスクトップ）から、気に入ったパターンを選んで取り込みます:
```
uv run python -m shorts_tool ace-import                  # 作った曲とパターンの一覧
uv run python -m shorts_tool ace-import 曲名 --take 2    # 2番目のパターンを取り込む
```
"""),
]

nb = {
    "nbformat": 4, "nbformat_minor": 0,
    "metadata": {"colab": {"provenance": [], "gpuType": "T4"}, "accelerator": "GPU",
                 "kernelspec": {"name": "python3", "display_name": "Python 3"}, "language_info": {"name": "python"}},
    "cells": cells,
}
out = ROOT / "ace_step_colab.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(out)
