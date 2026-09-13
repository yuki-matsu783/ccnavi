# 写す手順（launcher-scripts）

このディレクトリの `ccnavi-launcher.sh` は、エージェントが書けない場所（`.ccnavi/scripts/`）に置く
振り分けの sh の完成品です。チケット `launcher-scripts` のフェーズ 4（launcher-scripts-06。手順の直しは launcher-scripts-08）の成果物。
差分ではなく全文を置いてあります。

あわせて、エージェントの範囲の外にある 3 か所を人が直します。

| 何 | なぜ人が直すか |
|---|---|
| `.gitignore` に `/.ccnavi/bin/` | 親チケットの `allow` に無い（設計 15 節 D-4） |
| `.claude/skills/ccnavi-config/SKILL.md` の 45〜47 行 | 同じ（D-6） |
| `.claude/settings.json` の `CCNAVI_BIN_PATH` | implement の範囲（`phases.yml`）に無い（利用者の決定、2026-09-13） |

手順は 2 部に分かれます。

- **A. ブランチで写す（1〜8）。** 親の作業ツリー（ブランチ `launcher-scripts`）で写し、組み立て、確かめ、コミットする
- **B. main に入ったあと、ワークスペースルートで切り替える（9）。** hook が起動するのはワークスペースルートの
  `.claude/settings.json` と sh なので、切り替わるのは MR が main に入って pull したとき。
  実行ファイルの置き場 `.ccnavi/bin/<os>-<arch>/` は無視されていて git が運ばないので、ワークスペースルートでも
  組み立ててから開き直す

**写す前に:** 今は `CCNAVI_GUARD_CORE_FILES=dry-run` なので、人が `.ccnavi/` や `.claude/` を書いても、走っている
セッションの監視は「戻すはずだった」と報告するだけで戻しません。`enable` にしている場合は、写す間このワークスペースの
Claude Code のセッションを止めてください（ツール呼び出しのあとの監視が、人の変更を控えから戻します）。

---

## A. ブランチで写す

以下はすべて親の作業ツリーで打ちます。

```sh
cd <ワークスペースルート>/.claude/worktrees/launcher-scripts
```

### 1. 写す前に見る

```sh
diff -u .ccnavi/scripts/ccnavi-launcher.sh wip/design/scripts/ccnavi-launcher.sh
```

新規なので「`.ccnavi/scripts/ccnavi-launcher.sh`: No such file or directory」と出ます。それで正しいです。

前の原本（main の `scripts/ccnavi-launcher.sh`。このブランチでは消した）との差は次で見られます。
変わるのは冒頭の説明、`bin_dir`（自分の隣ではなく `../bin`）、見つからないときの文面の 3 か所です。

```sh
git show main:scripts/ccnavi-launcher.sh | diff -u - wip/design/scripts/ccnavi-launcher.sh
```

### 2. 写す

hook は sh を `sh` 経由ではなく直に起動するので、実行ビットが要ります。git にもモード 100755 で入れます
（Windows の `core.filemode=false` でも落ちないよう、`--chmod=+x` を付けて足す）。

```sh
cp wip/design/scripts/ccnavi-launcher.sh .ccnavi/scripts/ccnavi-launcher.sh
chmod +x .ccnavi/scripts/ccnavi-launcher.sh
git add --chmod=+x -- .ccnavi/scripts/ccnavi-launcher.sh
cmp wip/design/scripts/ccnavi-launcher.sh .ccnavi/scripts/ccnavi-launcher.sh && echo same
```

### 3. `.gitignore` に `/.ccnavi/bin/` を足す（D-4）

無いと、5 の組み立ての出力が `.ccnavi/` の下の未追跡として git に出て、実行後の監視が報告し、
作業ツリーの `worktree remove` も未追跡のファイルで止まります。

冒頭の 4〜7 行（4 行）を、次の 7 行に置き換えます。すぐ後ろの 8 行目の空行（次の「Python の中間物」の段との
区切り）はそのまま残します。コメントの「dist は hook が指す実行ファイルの置き場」は、切り替えのあと正しくなく
なるので一緒に直します。

直す前:

```gitignore
# 組み立ての出力。dist は hook が指す実行ファイルの置き場、
# build は PyInstaller の作業場所。
/dist/
/build/
```

直したあと:

```gitignore
# 組み立ての出力。dist は PyInstaller の出力、build はその作業場所。
# .ccnavi/bin は build.py が dist から写す、hook が起動する実行ファイルの置き場で、
# 機械ごとに <os>-<arch>/ が並ぶ。振り分けの sh は .ccnavi/scripts/ にあり、
# そちらは追跡する。
/dist/
/build/
/.ccnavi/bin/
```

直したあとの 4〜11 行は、上の 7 行と空行 1 行になります（空行が 2 行続いていないこと）。

### 4. `SKILL.md` の綴りを直す（D-6）

`.claude/skills/ccnavi-config/SKILL.md` の 45〜47 行。

直す前:

```markdown
以下で `ccnavi` と書いたら、このリポジトリでは `uv run python -m ccnavi`。配布先の
プロジェクトでは settings.json の `CCNAVI_BIN_PATH` が指す実行ファイル（既定 `.ccnavi/bin/ccnavi`。
この機械に合う `.ccnavi/bin/<os>-<arch>/ccnavi` を選んで起動する sh）。
```

直したあと:

```markdown
以下で `ccnavi` と書いたら、このリポジトリでは `uv run python -m ccnavi`。配布先の
プロジェクトでは settings.json の `CCNAVI_BIN_PATH` が指す振り分けの sh
（`.ccnavi/scripts/ccnavi-launcher.sh`。この機械に合う `.ccnavi/bin/<os>-<arch>/ccnavi` を選んで起動する）。
```

### 5. 組み立てる

```sh
uv run --with pyinstaller python build.py
cat dist/ccnavi.target
ls .ccnavi/bin/
git status --short .ccnavi/bin/
git check-ignore -v .ccnavi/bin/"$(cat dist/ccnavi.target)"
```

- `cat` がこの機械の語（例 `darwin-arm64`）を出し、`ls` に同じ名前のディレクトリがあること
- `git status --short .ccnavi/bin/` が何も出さないこと（出たら 3 が効いていない）。置き場そのものが無いと
  `warning: could not open directory '.ccnavi/bin/'` が出ることがあります。そのときは直前の `ls` も失敗しているので、
  組み立てが写す段まで届いていません。組み立ての出力を読み直してください
- `git check-ignore -v` が `.gitignore:10:/.ccnavi/bin/` で始まる 1 行を出すこと。何も出ないなら 3 が効いていない。
  `git status` は、無視されているときも置き場がそもそも無いときも何も出さず、`check-ignore` は置き場が無くても
  ルールだけで答えます。なので「置き場がある」は上の `ls` で、「無視されている」はこの `check-ignore` で、分けて見ます
- `.ccnavi/` 全体の `git status` は見ません。親の作業ツリーには、フェーズのマーカー
  （`.ccnavi/tickets/phases/launcher-scripts/<番号>.pending` など）がまだコミットされずに出ていることがあり、
  それは切り替えとは関係ありません
- 組み立てが 4 段目（`.ccnavi/bin/` へ写す）で落ちたら、`dist/ は新しい。.ccnavi/bin/<target>/ は前のまま` と出て 1 で終わります。
  `dist/` はできているので、原因（ディスク、権限、Windows で走っている実行ファイルのロック）を直して回し直してください

### 6. 確かめる（3 段）

`echo $?` だけでは 126 と 127 を区別できないので、原因ごとに分けて見ます。

**1) 実行ビット**

```sh
[ -x .ccnavi/scripts/ccnavi-launcher.sh ] && echo ok || echo NOT-EXECUTABLE
```

`NOT-EXECUTABLE` なら 2 の `chmod +x` からやり直し。

**2) git のモード**

```sh
git ls-files -s .ccnavi/scripts/ccnavi-launcher.sh
```

先頭が `100755` であること。`100644` なら `git add --chmod=+x -- .ccnavi/scripts/ccnavi-launcher.sh` をやり直し。
ここを落とすと、別の機械で clone した直後に hook が 126 で起動しません。

**3) 起動**

sh を通して実行ファイルまで届くかを、記録と控えを外した設定 lint で確かめます
（外さないと、走っているセッションの記録に確かめた行が混ざる）。

```sh
.ccnavi/scripts/ccnavi-launcher.sh --lint --log "" --state ""; echo "exit=$?"
```

| 出たもの | 読み方 |
|---|---|
| `exit=126`（`Permission denied`） | 実行ビットが無い。1) に戻る |
| `exit=127` で `ccnavi: この機械（…）で動く実行ファイルが …/ にありません` | 実体が無い。5 の組み立てを回す。文面の `<os>-<arch>` と `cat dist/ccnavi.target` が違うなら、別の機械の組み立てしか無い |
| `exit=127` で ccnavi の文面が出ない（`No such file or directory`） | sh 自体が無いか綴りが違う。2 に戻る |
| lint の結果が出る（`exit=0` か `1`） | 実体まで届いている。`1` のときは lint の中身を読む（切り替えとは別の指摘のこともある） |

### 7. テストを回す

写す前の版を名指しした結果と、写したあとに名指し無しで回した結果が同じになることを確かめます。

```sh
CCNAVI_TEST_LAUNCHER=wip/design/scripts/ccnavi-launcher.sh \
  uv run python -m unittest tests.test_launcher tests.test_setup tests.test_config_union_guard tests.test_sh_portability
uv run python -m unittest tests.test_launcher tests.test_setup tests.test_config_union_guard tests.test_sh_portability
uv run python -m unittest discover -s tests -t .
```

- 1 本目と 2 本目がどちらも `OK`（フェーズ 3・4 の時点で 244 件）
- 3 本目が `OK`（skip はあってよい。フェーズ 3 の時点で 772 件、skip 31）

2 本目だけが赤なら、写した sh が名指しの版と違うか、実行ビットが落ちています（2 の `cmp` と 6 の 1)）。

### 8. `CCNAVI_BIN_PATH` を直して、コミットする

**1〜7 が済んでから直します。** このブランチの `settings.json` はこの作業ツリーの hook には効きませんが、
ここで sh と実行ファイルが揃っていることを確かめてから入れておくと、B で main に入ったときの食い違いを
減らせます。

`.claude/settings.json` の 10 行目を `dist/ccnavi/ccnavi` から `.ccnavi/scripts/ccnavi-launcher.sh` に直します。
直したあとの `env` の全文:

```json
  "env": {
    "PYTHONUTF8": "1",
    "ARCHIFY_UPDATE_CHECK_DISABLED": "1",
    "CCNAVI_GUARD_CORE_FILES": "dry-run",
    "CCNAVI_MODE": "dry-run",
    "CCNAVI_RESTORE_IF_DENY": "dry-run",
    "CCNAVI_RULES": ".ccnavi/common/rules.yml",
    "CCNAVI_LOG": "logs/log.jsonl",
    "CCNAVI_BIN_PATH": ".ccnavi/scripts/ccnavi-launcher.sh",
    "CCNAVI_TICKET_CONTROL": "enable"
  },
```

直したら、設定 lint が新しい綴りを読んで error を出さないことを確かめます（lint は `settings.json` の env を読み、
指す先が在るのに実行できなければ error を出す）。

```sh
uv run python -m ccnavi --lint --log "" --state ""; echo "exit=$?"
```

| 出たもの | 読み方 |
|---|---|
| 最後の行が `error 0 件、warn <n> 件、info <n> 件` で `exit=0` | 揃っている。warn は切り替えと別の指摘（dry-run のモードなど）なので、ここでは読むだけでよい |
| `error: (project): .claude/settings.json の env の CCNAVI_BIN_PATH=.ccnavi/scripts/ccnavi-launcher.sh は在るが実行できない。…` の行があり、最後の行が `error 1 件、…` で `exit=1` | sh の実行ビットが落ちている。6 の 1) に戻る |
| 上と別の `error:` の行があって `exit=1` | 切り替えとは別の指摘。直してから進むか、利用者に相談する |

コミットの分け方の案（1 行のメッセージ、フッター無し）。最初のコミットは、2 で `--chmod=+x` を付けて足した
sh だけが入った状態で打ちます。`git commit -- <パス>` の形は、そのパスを作業ツリーから入れ直します。
`core.filemode=true` の機械（macOS・Linux・多くの WSL）で、作業ツリーの sh に実行ビットが付いていないと
（2 の `chmod +x` が効いていない、別の手で写し直した、など）、`--chmod=+x` で入れた 100755 が 100644 に戻ります。
原因を問わず、この形は使わないでください。

```sh
git diff --cached --name-only    # .ccnavi/scripts/ccnavi-launcher.sh の 1 行だけであること
git commit -m "feat: 振り分けの sh を .ccnavi/scripts/ に置く"
git add -- .gitignore
git commit -m "chore: 実行ファイルの置き場 .ccnavi/bin/ を無視する"
git add -- .claude/skills/ccnavi-config/SKILL.md .claude/settings.json
git commit -m "ai-asset: CCNAVI_BIN_PATH を振り分けの sh に向け、スキルの綴りを直す"
git ls-files -s .ccnavi/scripts/ccnavi-launcher.sh
git push origin launcher-scripts
```

最後の `ls-files` がもう一度 `100755` を出すこと。

---

## B. main に入ったあと、ワークスペースルートで切り替える

### 9. pull → 組み立てる → 確かめる → 開き直す

MR（#30）が main に入ったあとに打ちます。

**始める前に、このワークスペースを開いているものをすべて閉じます。**

- Claude Code のセッション（並行しているもの、別の作業ツリーで動いているものも含む）
- このワークスペースを開いている VS Code のウィンドウ（ボード拡張が ccnavi の実行ファイルを起動する）
- 同じフォルダを別の機械（Windows と WSL など）から開いているなら、そちらも

pull で `settings.json` が新しい綴りに変わってから `.ccnavi/bin/<この機械>/` ができるまでの間に hook が走ると、
sh が 127 で終わります。Claude Code はそれを hook のエラーとして扱い、その回は判定が走りません（止まらない側に倒れる）。
Windows では、実行ファイルを起動しているものが残っていると、組み立ての入れ替え（`.ccnavi/bin/<target>/` への rename）が
落ちることがあります。

```sh
cd <ワークスペースルート>
git status --short
git pull; echo "exit=$?"
```

**pull の前に `git status --short` を見ます。** 何か出たら、それは他のセッションの書きかけかもしれません
（CLAUDE.md「他セッションの作業を踏まないために」）。commit・stash・reset・checkout で片付けず、誰のものかを
確かめてから進みます。

**pull が `exit=0` 以外で終わったら、ここで止まります。** 次のどれかが出ます。

| 出たもの | 何が起きたか |
|---|---|
| `error: Your local changes to the following files would be overwritten by merge:`（`exit=1`） | 上の `git status` に出た変更が、取り込む変更と重なった |
| `fatal: Need to specify how to reconcile divergent branches.` や `fatal: Not possible to fast-forward, aborting.`（`exit=128`） | ワークスペースルートの main に、origin に無いコミットがある |
| `CONFLICT` | 取り込みが衝突した（pull の設定でマージしようとした） |

どの場合も、`build.py` は回さず、開き直しもしません。`exit=1` と `exit=128` のときは何も取り込まれていないので、
`.claude/settings.json` は前の `dist/ccnavi/ccnavi` のままで、そのまま開き直しても前の形で動きます。
`CONFLICT` のときは、マージで取り込んでいたなら `git merge --abort`、リベースで取り込んでいたなら（`pull.rebase=true`）
`git rebase --abort` で取り込む前に戻し、`git status --short` が pull の前と同じになってから開き直します。どれも、原因を確かめて利用者に相談してから
やり直します。

pull が `exit=0` で終わったら、組み立てます。

```sh
uv run --with pyinstaller python build.py
```

`build.py` が 1 で終わったら（`dist/ は新しい。.ccnavi/bin/<target>/ は前のまま`）、**開き直しません。**
初回は `.ccnavi/bin/<target>/` がまだ無いので、そのまま開くと hook が 127 で起動しません。閉じ忘れたものを閉じるなど
原因を直して回し直すか、回し直せなければ「戻し方」の B に進みます。

続けて、6 の 3 段をワークスペースルートで打ちます（2) の `ls-files` は `100755` のはず）。

```sh
[ -x .ccnavi/scripts/ccnavi-launcher.sh ] && echo ok || echo NOT-EXECUTABLE
git ls-files -s .ccnavi/scripts/ccnavi-launcher.sh
.ccnavi/scripts/ccnavi-launcher.sh --lint --log "" --state ""; echo "exit=$?"
git status --short .ccnavi/bin/
git check-ignore -v .ccnavi/bin/"$(cat dist/ccnavi.target)"
```

3 段が揃ったら、Claude Code を開き直します（env の `CCNAVI_BIN_PATH` の変更は、ここで効きます）。

開き直したら、hook が sh を通って動いていることを確かめます。何かツールを 1 回使わせてから:

```sh
wc -l logs/log.jsonl
tail -n 1 logs/log.jsonl
```

行数が増え、最後の行の時刻が開き直したあとであること。増えないときは、Claude Code の hook のエラー表示
（`/hooks` や起動時の通知）に 126 / 127 が出ていないかを見て、6 の表で読みます。

**同じフォルダを別の機械（Windows と WSL など）からも開くなら、その機械でも `build.py` を 1 回回します。**
`.ccnavi/bin/` には機械ごとの `<os>-<arch>/` が並び、sh は起動した機械の分を選びます。回していない機械では
127 の文面が出ます。

---

## 戻し方

**戻す間の監視:** 「写す前に」と同じく、`CCNAVI_GUARD_CORE_FILES=enable` にしているなら、戻す間もこのワークスペースの
Claude Code のセッションを止めてください。止めないと、ツール呼び出しのあとの監視が、戻した `.ccnavi/`・`.claude/` の
変更を控えから元に戻します（dry-run なら報告だけ）。

**作業ツリーがもう無いとき:** MR を Ready にしたあとは作業ツリー `launcher-scripts` を片付けるので、A の戻し方を
打つ時点で無いことがあります。ブランチは残っているので、作業ツリーとして作り直してから下を打ちます
（`-b` を付けない。付けると別のブランチができる）。

```sh
cd <ワークスペースルート>
git worktree add .claude/worktrees/launcher-scripts launcher-scripts
```

エージェントに頼むときは、ラッパーを通す綴り `sh .ccnavi/scripts/ccnavi-git.sh worktree add .claude/worktrees/launcher-scripts launcher-scripts` になります。

**A の途中（8 の最初のコミットより前）:** 2 の写す段より前にやめた場合など、まだ無いものは飛ばされます。

```sh
cd <ワークスペースルート>/.claude/worktrees/launcher-scripts
if [ -n "$(git ls-files .ccnavi/scripts/ccnavi-launcher.sh)" ]; then git rm --cached -q -- .ccnavi/scripts/ccnavi-launcher.sh; fi
rm -f .ccnavi/scripts/ccnavi-launcher.sh
git restore -- .gitignore .claude/skills/ccnavi-config/SKILL.md .claude/settings.json
rm -rf .ccnavi/bin
```

**A の 8 でコミットしたあと（push の前でも後でも）:** 履歴を書き換えず、打ち消すコミットを足します。
push の後に書き換えると、取り込んだ人の手元と食い違うためです。

```sh
cd <ワークスペースルート>/.claude/worktrees/launcher-scripts
git log --oneline -5    # 8 で作ったコミット（最大 3 つ）を確かめる
git revert --no-edit <新しいほうから順に SHA を並べる>
rm -rf .ccnavi/bin
git ls-files .ccnavi/scripts/ccnavi-launcher.sh    # 何も出ないこと
git push origin launcher-scripts                   # push 済みだったときだけ
```

`git revert` が `CONFLICT` と `error: could not revert …` を出して `exit=1` で止まったら、手で解きません。
8 のあとに同じ行を直したコミットが入っています。`git revert --abort` で revert する前の状態に戻し
（`git status --short` が何も出さないこと）、利用者に相談します。

8 のコミットの途中（1 つ目だけ済んだ、など）でやめたなら、済んだコミットだけを `revert` し、まだコミットしていない
ファイルは上の「最初のコミットより前」の `git restore` で戻します。

**B のあと、hook が起動しない:** ワークスペースルートの `.claude/settings.json` の `CCNAVI_BIN_PATH` を
`dist/ccnavi/ccnavi` に戻して開き直します。9 の `build.py` が `dist/ccnavi/` も作り直しているので、前と同じく
実行ファイルを直に起動する形で動きます。原因を直したら、また `.ccnavi/scripts/ccnavi-launcher.sh` に向けます。

## 何が変わるか

| ファイル | 変更 |
|---|---|
| `.ccnavi/scripts/ccnavi-launcher.sh` | 新設（100755）。1 つ上の `bin/<os>-<arch>/` から実行ファイルを選んで起動する。隣は探さない。無ければ 127 |
| `.gitignore` | `/.ccnavi/bin/` を足し、組み立ての出力のコメントを直す |
| `.claude/skills/ccnavi-config/SKILL.md` | 配布先の `CCNAVI_BIN_PATH` の綴りを `.ccnavi/scripts/ccnavi-launcher.sh` に |
| `.claude/settings.json` | `CCNAVI_BIN_PATH` を `dist/ccnavi/ccnavi` から `.ccnavi/scripts/ccnavi-launcher.sh` に |

## 切り替えで変わること

- ツール呼び出しのたびに、sh の起動と `uname` 1 回ぶんが乗ります
- `build.py` を回したあと、`.ccnavi/bin/<os>-<arch>/` に写し終えるまでは、hook は前の実行ファイルを起動します。
  写す瞬間（rename 2 回の間、数 ms）に来た hook は 127 で終わり、その回は判定が走りません
- 組み立て直すと、セッション開始で取った実行ファイルの控えと食い違い、開き直すまで `bin-launched` の
  「戻すはずだった」が出ます（今の `dist/ccnavi/ccnavi` を指す形と同じ）
