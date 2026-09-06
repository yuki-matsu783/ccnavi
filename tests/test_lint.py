"""設定検証の受入テスト。内部の関数は呼ばず、標準出力と終了コードだけを見る。

見るのは 3 つ。error があれば非ゼロで終わること、error と warn が分かれていること、
そして検証が判定と同じ読み込みを使っていること。3 つ目が崩れると、検証が通ったのに
実運用で落ちるという、検証があるぶんかえって危ない形になる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 不備の無いルール 1 件。ここに 1 つずつ壊した欄を足して試す。
SOUND = {
    "id": "git-push",
    "match": "Bash",
    "pattern": "git push *",
    "message": "git push is not run by the agent. Ask the user to push.",
}


def write(directory: str, name: str, text: str) -> str:
    path = os.path.join(directory, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# 検証を通すのに要る最小の allow。無いと「allow が空」の warn が毎回 1 件増え、
# 数を見ているテストが、そのテストの主題と関係の無い 1 件を数えることになる。
ALLOWED = {"id": "anything", "match": "Read", "regex": "."}


def rules_file(directory: str, *rules, version: int = 2, allow: bool = True) -> str:
    """ルールファイルを 1 本置く。並べたルールは deny の区画に入る。

    書き出すのは JSON。YAML は JSON の上位互換なので、判定が読むのと同じ
    読み手がそのまま受け取る。区画の形だけを見たいテストで、YAML の綴りの
    話に付き合わずに済む。
    """
    body: dict = {"version": version, "deny": list(rules)}
    if allow:
        body["allow"] = [ALLOWED]
    return write(directory, "rules.yml", json.dumps(body, indent=2))


def ccnavi(root: str, *args: str) -> subprocess.CompletedProcess:
    """道具を 1 回動かす。

    モードもルールもフラグで固定する。このリポジトリは ccnavi を自分自身に
    仕掛けているので、テストを走らせるセッションが既に設定を持っている。
    それを読むテストは、コードではなく走った機械のことを報告してしまう。
    """
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    return subprocess.run(
        [sys.executable, "-m", "ccnavi", "--root", root, *args],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        env=environment,
    )


def lint(root: str, rules_path: str, mode: str = "block") -> subprocess.CompletedProcess:
    return ccnavi(root, "--lint", "--rules", rules_path, "--mode", mode)


def counts(text: str) -> tuple[int, int]:
    """報告の最後の行から error と warn の件数を読む。"""
    for line in reversed(text.splitlines()):
        if line.startswith("error "):
            fields = line.replace("件", "").replace("、", " ").split()
            return int(fields[1]), int(fields[3])
    raise AssertionError(f"件数の行が無い: {text!r}")


class LintTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # 空のディレクトリを根に使う。設定ファイルの有無まで自分で決められないと、
        # テストが走った機械にあるファイルを報告することになる。
        self.root = directory.name

    def test_不備が無ければ何も咎めずに0で終わる(self):
        result = lint(self.root, rules_file(self.root, SOUND))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(counts(result.stdout), (0, 0))
        # どのファイルを見た結果なのかを名乗らない報告は、別の設定についての
        # 報告と見分けが付かない。
        self.assertIn("rules.yml", result.stdout)

    def test_読めないルールはerrorで非ゼロで終わる(self):
        # block モードではこれが全ツール呼び出しの拒否になり、直すための
        # 呼び出しまで止まる。検証がいちばん先に見つけなければならない形。
        path = write(self.root, "rules.yml", "version: 2\ndeny: [\n  - id: x\n")

        result = lint(self.root, path)

        self.assertEqual(result.returncode, 1, "error があるのに 0 で終わった")
        self.assertIn("error:", result.stdout)
        self.assertIn("ルールを読めない", result.stdout)

    def test_無いルールファイルもerrorになる(self):
        result = lint(self.root, os.path.join(self.root, "どこにも無い.yml"))

        self.assertEqual(result.returncode, 1)
        self.assertEqual(counts(result.stdout)[0], 1)

    def test_版が違うルールはerrorになる(self):
        result = lint(self.root, rules_file(self.root, SOUND, version=99))

        self.assertEqual(result.returncode, 1)
        self.assertIn("版", result.stdout)

    def test_壊れたルールは1件ずつ名指しでerrorになる(self):
        # 落ちたルールは黙って消える。消えた穴は誰も気づかないので、
        # 1 件ずつ id で名指しする。
        result = lint(
            self.root,
            rules_file(
                self.root,
                dict(SOUND, id="文面無し", message=""),
                dict(SOUND, id="match無し", match=""),
                dict(SOUND, id="当てるもの無し", pattern="", regex=""),
                dict(SOUND, id="二重指定", regex="git push"),
                dict(SOUND, id="組み立て不能", pattern="", regex="git push ("),
                dict(SOUND, id="先読み", pattern="", regex="git (?=push)"),
            ),
        )

        self.assertEqual(result.returncode, 1)
        for name in (
            "文面無し",
            "match無し",
            "当てるもの無し",
            "二重指定",
            "組み立て不能",
            "先読み",
        ):
            # 名前には区画が付く。同じ id が別の区画に居ることがあるので、
            # どちらの話なのかを名前が言えないと直しに行く先が決まらない。
            self.assertIn(f"error: deny:{name}:", result.stdout, f"{name} を咎めていない")

    def test_denyが1件も無いのはerrorになる(self):
        # 何も止めないガードは、入っていないガードと同じでありながら、
        # 入っているように見える。いちばん見つけにくい壊れ方なので error。
        result = lint(self.root, rules_file(self.root))

        self.assertEqual(result.returncode, 1)
        self.assertIn("`deny` が空", result.stdout)

    def test_allowが1件も無いのはwarnになる(self):
        # 判定は動いている。ただし、どのルールも言及しない呼び出しが
        # すべて暗黙的 ask になるので、確認が出続ける状態と区別が付かない。
        result = lint(self.root, rules_file(self.root, SOUND, allow=False))

        self.assertEqual(result.returncode, 0)
        self.assertIn("`allow` が空", result.stdout)

    def test_判定は動くが効かない記述はwarnで0のまま(self):
        # ここが error と warn を分ける意味そのもの。ガードは動いているので
        # CI を落とす必要は無く、しかし守っているつもりの穴は開いている。
        result = lint(
            self.root,
            rules_file(
                self.root,
                dict(SOUND, id=""),
                dict(SOUND, id="重複"),
                dict(SOUND, id="重複", pattern="rm -rf *"),
                dict(SOUND, id="当たらないツール", match="Task|Bash"),
            ),
        )

        self.assertEqual(result.returncode, 0, "warn だけで非ゼロにしてはいけない")
        errors, warns = counts(result.stdout)
        self.assertEqual(errors, 0)
        # id 無しが 1 件、重複が 1 件、当たらない match が 1 件。重複は 2 件目だけを
        # 咎める。1 件目は、他に同じ id が無ければそのままで正しいルールだから。
        self.assertEqual(warns, 3, result.stdout)
        self.assertIn("id が無い", result.stdout)
        self.assertIn("id が重複", result.stdout)
        self.assertIn("Task", result.stdout)

    def test_止めないモードはwarnとして報告される(self):
        result = lint(self.root, rules_file(self.root, SOUND), mode="warn")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(counts(result.stdout), (0, 1))
        self.assertIn("warn:", result.stdout)

    def test_モードとして読めない値はwarnとして報告される(self):
        result = lint(self.root, rules_file(self.root, SOUND), mode="blocking")

        self.assertEqual(result.returncode, 0, "block に落ちるのでガードは弱まらない")
        self.assertEqual(counts(result.stdout), (0, 1))
        self.assertIn("blocking", result.stdout)

    def test_作業ツリーの中に書かれたoffはerrorになる(self):
        # 監視される側が書けるファイルから監視を止める記述。判定の側には
        # 人が渡した off と見分ける手段が無いので、ここで見つけるしかない。
        write(
            self.root,
            os.path.join(".claude", "settings.json"),
            json.dumps({"env": {"CCNAVI_MODE": "off"}}),
        )

        result = lint(self.root, rules_file(self.root, SOUND))

        self.assertEqual(result.returncode, 1)
        self.assertIn("settings.json", result.stdout)
        self.assertIn("off", result.stdout)

    def test_検証は判定と同じ読み込みを使う(self):
        # 別の読み方をすると、検証は通ったのに実運用で落ちる。同じ壊れたルールに
        # ついて、検証が名指しするものと、判定が走るときに苦情を言うものが
        # 一致することで確かめる。
        path = rules_file(self.root, SOUND, dict(SOUND, id="組み立て不能", pattern="", regex="("))

        checked = lint(self.root, path)
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
            }
        )
        decided = subprocess.run(
            [sys.executable, "-m", "ccnavi", "--root", self.root, "--rules", path]
            + ["--mode", "block", "--log", ""],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env={k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        )

        self.assertIn("組み立て不能", checked.stdout)
        self.assertIn("組み立て不能", decided.stderr, "判定と検証が別のことを言っている")

    def test_実行後の監視が登録されていなければwarnになる(self):
        write(
            self.root,
            os.path.join(".claude", "settings.json"),
            json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"command": "ccnavi"}]}]}}),
        )

        result = lint(self.root, rules_file(self.root, SOUND))

        self.assertEqual(result.returncode, 0, "実行前の判定は動くのでガードは消えていない")
        self.assertEqual(counts(result.stdout), (0, 1))
        self.assertIn("PostToolUse", result.stdout)

    def test_git_の作業ツリーでなければ監視が何も見ないとwarnになる(self):
        # 登録はされているのに見る先が無い状態。実行後の監視は git の差分で
        # 見るので、リポジトリでない場所では 1 件も検知しない。
        write(
            self.root,
            os.path.join(".claude", "settings.json"),
            json.dumps({"hooks": {"PostToolUse": [{"hooks": [{"command": "ccnavi"}]}]}}),
        )

        result = lint(self.root, rules_file(self.root, SOUND))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(counts(result.stdout), (0, 1))
        self.assertIn("作業ツリーを読めない", result.stdout)

    def test_設定ファイルが無ければ登録については何も言わない(self):
        # hook は利用者ごとの設定にも書ける。そちらはここから見えないので、
        # 見えないものを「無い」と報告すると正しい設定に苦情を出すことになる。
        result = lint(self.root, rules_file(self.root, SOUND))

        self.assertEqual(counts(result.stdout), (0, 0), result.stdout)


if __name__ == "__main__":
    unittest.main()
