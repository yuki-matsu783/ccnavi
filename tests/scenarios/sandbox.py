"""スタブで回すシナリオの、使い捨てのワークスペース（段 1）。

一時ディレクトリに、ccnavi の hook が動くのに要るものだけを置いたワークスペースを組む。

- このリポジトリの作業ツリーから、追跡している `.ccnavi/`（`bin/` は管理外なので入らない）、
  `.claude/settings.json`・`.claude/hooks/`、`.gitignore`、`docs/wiki/` をコピーし、
  git のリポジトリにする
- テストが組む ccnavi の実行ファイル（`tests/exe.py`）を `.ccnavi/bin/<os>-<arch>/` に置く
- `settings.json` の `CCNAVI_MODE` を `enable` にする（この使い捨ての木の中だけ）
- `projects/test-repo` に、README を 1 本持つ小さなリポジトリを置く

ユーザのワークスペースには触れないので、回す前の人間の確認は要らない。
"""

import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from tests import exe
from tests.scenarios.common import ROOT

# docs/wiki は、ccnavi がセッションの開始で学びの案内を作るときに読む
COPIED = (".ccnavi", ".claude/settings.json", ".claude/hooks", ".gitignore", "docs/wiki")
TEST_REPO = "projects/test-repo"
# 受入シナリオが当てにする test-repo の README の見出し（UC-01 の台本が書き換える行）
TEST_REPO_README = "# 演習: テキスト整形CLI\n\n受入シナリオの使い捨てのワークスペースの見本。\n"
OS_NAMES = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}
ARCH_NAMES = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}


def build(base):
    """base の下にワークスペースを組み、そのルートを返す。"""
    ws = Path(base) / "ws"
    for rel in _tracked(COPIED):
        dst = ws / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    _enable(ws / ".claude/settings.json")
    _put_exe(ws)
    _commit(ws)
    repo = ws / TEST_REPO
    repo.mkdir(parents=True)
    (repo / "README.md").write_text(TEST_REPO_README, encoding="utf-8")
    _commit(repo)
    return ws


def make_base():
    return tempfile.mkdtemp(prefix="ccnavi-stub-ws-")


def _tracked(paths):
    r = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", *paths],
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in r.stdout.split("\0") if p and (ROOT / p).is_file()]


def _enable(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("env", {})["CCNAVI_MODE"] = "enable"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _put_exe(ws):
    target = f"{OS_NAMES.get(platform.system(), 'unknown')}-"
    target += ARCH_NAMES.get(platform.machine().lower(), "unknown")
    name = "ccnavi.exe" if platform.system() == "Windows" else "ccnavi"
    dst = ws / ".ccnavi/bin" / target / name
    dst.parent.mkdir(parents=True)
    shutil.copy2(exe.path(), dst)
    os.chmod(dst, 0o755)


def _commit(path):
    git = ["git", "-C", str(path), "-c", "user.name=ccnavi-test", "-c", "user.email=test@invalid"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "使い捨てのワークスペース"], check=True)
