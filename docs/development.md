---
title: 開発の入口
type: guide
description: ccnavi 自身を開発する人向けの入口。開発環境、テストと整形のコマンド、開発用 hook の登録、build と配布物、配布する sh の確かめ方、条件の足し方、文書の案内表
tags: [development, build, test]
keywords: [開発, 開発環境, テスト, unittest, ruff, build.py, PyInstaller, onedir, lint-py, test-py, mark-ext, test-ext, settings.local.json, CCNAVI_BIN_PATH, CCNAVI_SH_DIR, 配布物, 条件の足し方, ccnavimd]
---

# 開発の入口

ccnavi 自身を開発する人向けの入口である。使う人向けの手引きは[手引きの目次](guide/index.md)にある。

## 文書の案内表

| 知りたいこと | 読む場所 |
|---|---|
| 用語 | [CONTEXT.md](CONTEXT.md) |
| 更新したときの変更点 | [release-notes.md](release-notes.md) |
| 外から観測できる約束 | [requirements.md](requirements.md) |
| 実行ファイルと拡張の JSON の形 | [requirements/json-shapes.md](requirements/json-shapes.md) |
| いまの実装の作り | [design.md](design.md) |
| ディレクトリ構成（モジュールごとの表） | [design/modules.md](design/modules.md) |
| 判断の理由と経緯、採らなかった案 | [adr/README.md](adr/README.md) |
| 実測で確かめた前提と、環境固有の落とし穴 | [設計の付録 C](design/appendix.md)、[claude/environment.md](claude/environment.md) |
| 開発で常に守る決まり（テストの回し方、ログ、sh、出力の案内など） | `docs/claude/`（入口は [CLAUDE.md](../CLAUDE.md) の「詳細」の表） |
| ccnavi の使い方 | [guide/index.md](guide/index.md) |

## 開発環境

Python 3.12 以降。実行時の依存は PyYAML 1 本だけ。
組み立てと検査の道具（PyInstaller、ruff）は開発時にしか要らない。

```sh
uv run python -m tests                             # 全件を別プロセスで同時に回す
uv run python -m tests tests/core                  # 1 グループ（core guard config ticket sh e2e）
uv run --with ruff ruff check .                    # 静的検査
uv run --with ruff ruff format .                   # 整形
uv run --with ruff ruff format --check .           # 整形の確認だけ
uv run --with pyinstaller python build.py          # 実行ファイルの組み立て
```

テストは `python -m tests` か `python -m unittest` で回す。ファイルを直接実行すると `tests` パッケージを import できずに失敗する。
全件を `unittest discover` の 1 本で回さない（時間切れになる）。全件は `python -m tests` で回す（理由と手順は[テストを回す](claude/testing.md)）。
回すグループは `.claude/skills/commit/references/test-groups.md` の表で決める。

`knowledge/` は参照専用の資料で git 管理外。Claude Code の hook にまつわる実測が入っている。

`dist/`、`build/`、`logs/` は git 管理外。記録には絶対パスとコマンド全文が入るのでコミットしない。

手で 1 回動かす。

```sh
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git push"}}' \
  | uv run python -m ccnavi --mode enable
```

## 開発用の hook

Python のファイルを編集するたびに `.claude/hooks/lint-py.sh`（`PostToolUse` の
`Write|Edit|NotebookEdit`）が整形と検査を走らせる。テストはそこでは走らせない。

テストは `.claude/hooks/test-py.sh` で、`Stop` に登録してターンの終わりに 1 回だけ回す。
失敗していたら差し戻すが、差し戻しは 3 回まで。上限に達したら止まらせて判断をユーザへ返す。
このリポジトリの `.claude/settings.json` の `Stop` には ccnavi の実行ファイルしか無いので、
テストを回すなら `Stop` に足す（ユーザごとの `settings.local.json` でよい）。

`lint-py.sh` は、編集したファイルからいちばん近い `pyproject.toml` を上に辿ってツリーを決め、
触ったツリーを `logs/session/<セッション>.trees` に書き残す。`test-py.sh` は触ったツリーだけをテストする。

拡張（`extensions/vscode/ccnavi-board`）のテストも同じ形で回す。`PostToolUse` の
`.claude/hooks/mark-ext.sh` が、拡張のファイルを触ったら `logs/session/<セッション>.ext-files` に
書き残し、`Stop` の `.claude/hooks/test-ext.sh` がターンの終わりに 1 回回す。回すのは触ったファイルが
関わるグループだけで、決めるのは `extensions/vscode/ccnavi-board/scripts/test-groups.js`
（テストの `import` を辿る。辿れない 6 つだけは表に名前を書いてある）。拡張を触っていないターンは何もしない。
差し戻しは 3 回までで、回数は `test-py.sh` と別に数える。

この 2 本で気づけるのは「同じ機械で 1 回回して失敗すること」だけで、混み具合で結果が変わる失敗（React の描き直しを
待つテストなど）は見つけられない（CI も繰り返しの仕掛けも無い）。Bash で変えたもの（`sed -i`、`merge` で入った変更）は
書き残されないので回らない。

これも `.claude/settings.json` には登録していないので、回すなら `settings.local.json` で
`PostToolUse`（`Write|Edit`）と `Stop` に足す。

```json
"PostToolUse": [
  { "matcher": "Write|Edit",
    "hooks": [{ "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.claude/hooks/mark-ext.sh\"", "timeout": 10 }] }
],
"Stop": [
  { "matcher": "",
    "hooks": [{ "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.claude/hooks/test-ext.sh\"", "timeout": 180 }] }
]
```

実行ファイルはどちらの hook でも作り直さない。確かめるときに手で `uv run --with pyinstaller python build.py` を回す。

## 組み立てと配布物

このリポジトリの hook も、配布先と同じく振り分けの sh（`.ccnavi/scripts/ccnavi-launcher.sh`）を通る。
`build.py` は `dist/ccnavi/` に組み立てたあと、それを `.ccnavi/bin/<os>-<arch>/` へコピーする（隣の `<os>-<arch>.new` に
コピーし切ってから入れ替える）。`.ccnavi/bin/` は `.gitignore` に入っている。コピーする段で失敗したら次の行を出して
終了コード 1 で終わる。原因（ディスク、権限、Windows で走っている実行ファイルのロック）を直して回し直す。

```
dist/ は新しい。.ccnavi/bin/<target>/ は前のまま
```

ワークツリーで組み立てると、コピー先はそのワークツリーの `.ccnavi/bin/` になり、走っている hook は変わらない。
ワークスペースルートで組み立て直すと、セッション開始で取った実行ファイルのバックアップと食い違う。
`CCNAVI_GUARD_CORE_FILES` が `enable` なら次のツール呼び出しのあとでバックアップの版に戻される（`dry-run` なら報告だけ）。
ワークスペースルートで組み立てたら、セッションを開き直す。仕組みは[導入と配布の設計](design/startup-config.md)の 4.6 にある。

`dist/` を直に起動したいときは、`.claude/settings.local.json`（追跡しない）の `env` で `CCNAVI_BIN_PATH` を
`dist/ccnavi/ccnavi` にする。自己防衛も同じ env を読むので、守る実体と起動する実体は食い違わない。代償は、その機械では
振り分けの sh の不具合に気づけないこと。`ccnavi.settings.local.json` のキー `bin` でも
`CCNAVI_BIN_PATH` を上書きできるが、変わるのは自己防衛が何を守るかだけで、hook が何を起動するかは変わらない。

配布物は PyInstaller の onedir で組み立てる。Windows なら `dist/ccnavi/ccnavi.exe`、
Linux なら `dist/ccnavi/ccnavi`。onefile は起動のたびにランタイムを展開して遅いので使わない
（onedir は 1 呼び出しあたり約 220 ms で配布物は 17 MB のフォルダ、onefile は 1000〜1500 ms で 7 MB の 1 ファイル）。
配布物の設計は[導入と配布の設計](design/startup-config.md)の 4.1 にある。

## 配布する sh を確かめる

```sh
uv run python -m unittest tests.e2e.test_e2e_sh -v
```

実行の最初に出る `sh =` がワークスペースルート側を指していることを確かめる。実際に使われるのはワークスペース側の 1 本だけ。
写す前の版を測るときは `CCNAVI_SH_DIR=<場所>` で差し替える。組み立て済みの実行ファイルを試すので、
`src/ccnavi/` を直したら組み立て直してから回す（無ければ skip）。モード B（`projects/` を使う形）に触ったら回す。

## 作業の決まりに条件を足す

`SessionStart` で渡す作業の決まり（`.ccnavi/CCNAVI.md`）の書き方と、配布先で条件を足す方法は
[`.ccnavi/docs/ccnavi-md.md`](../.ccnavi/docs/ccnavi-md.md) にある。ここには、ccnavi の実行ファイル側で条件を足す手順を書く（実行ファイルを触らず、配布先だけで足すときは、配布先の `.ccnavi/scripts/ccnavimd.sh` に関数 `ccnavi_cond_<名前>` を書く。足し方の 2 経路のうち後者で、実行ファイルのコードは変えない）。

1. `src/ccnavi/hook/ccnavimd.py` に、`(ctx: Context) -> bool` の関数を 1 つ書く。判定に要る状況が `Context` に無ければ、フィールドを足して `flags_for` で渡す。
2. 関数を `CONDITIONS` に登録する。名前は md に書くもの。英数字と `_` だけ（先頭は数字でない。`-` は `--lint` が error にする）。組み込みと同じ名前を `ccnavimd.sh` の関数に使わない。
3. `.ccnavi/CCNAVI.md` で `<!-- ccnavi:if 名前 -->` … `<!-- ccnavi:endif -->` で行を囲む。
4. `.ccnavi/docs/ccnavi-md.md` の条件の表に 1 行足す。判定の関数のテストを `tests/core/test_ccnavimd.py` に足す。

### 条件の判定の仕組み

条件の判定は、名前ごとに独立した関数が持つ。

- 関数は、そのセッションの状況（設定・ワークスペースルート・`--docs`を出す回か・ルールのdenyのid・動作モード）を受け取り、真偽を返す。1つの関数は1つの名前だけを判定する
- 読み込みのたびに、全部の関数を呼んで、真になった名前の集合を作る。判定はmdを読む前に済む
- mdを上から読み、`ccnavi:if`で囲まれた行は、囲みの名前がすべて集合に入っているときだけ載る
- 載せる行が決まったら、`{{名前}}`を値に置き換え、続く空行をまとめて1つの文にする
- 組み込みにも`ccnavimd.sh`の関数にも無い名前は、判定できないので真にして囲んだ行を出す。`ccnavi --lint`が警告する。差し込みの一覧に無い`{{名前}}`は、そのまま残る（書き間違いが見える形で届く）

## ディレクトリ構成（トップレベル）

モジュールごとの表は [design/modules.md](design/modules.md)。

| 場所 | 中身 |
|---|---|
| `main.py` | 配布物の入口。PyInstaller が渡すスクリプト |
| `src/ccnavi/__main__.py` | `python -m ccnavi` の入口。サブパッケージは役割ごとに 6 つで、読む向きは infra < records < policy < tickets < hook < entry（`tests/core/test_module_layers.py` が見る） |
| `build.py` | 配布物の組み立て。`dist/ccnavi/` を `.ccnavi/bin/<os>-<arch>/` へコピーする |
| `scripts/ccnavi-setup.sh` | 対象プロジェクトに設定を書き、実行ファイルとルールとスクリプトと使い方の文書（`.ccnavi/docs/*.md`）と共通のテンプレート（`.ccnavi/config/common/templates/*.yml`）と共通の工程スキル（`.claude/skills/` のうち `process: true` を持つもの。`SKILL.md` と `references/common/` の下の `.md` だけ）を配る |
| `.claude/hooks/lint-py.sh` / `test-py.sh` | このリポジトリ自身の開発用 hook。整形と検査、ターンの終わりのテスト |
| `.claude/hooks/mark-ext.sh` / `test-ext.sh` | 同じく拡張のぶん。触ったことの書き残しと、ターンの終わりに関わるグループだけ回すテスト |
| `extensions/vscode/ccnavi-board/scripts/test-groups.js` | 拡張のテストの入口。触ったファイルから回すグループを決め、コンパイルは 1 回で済ませる |
| `.claude/skills/` | このリポジトリのスキル。設定 3 本を足す・確かめる `ccnavi-config`、ccnavi を入れる・更新する `ccnavi-install`、コミットの手順 `commit`、気づきを記録に足す `learn`、図解の説明 `eli5` / `eli10`、文章の推敲 `yomiyasu`、工程の考え方の入口 `process-*`（`process: true` のものは `scripts/ccnavi-setup.sh` が配る） |
| `.ccnavi/scripts/ccnavi-launcher.sh` | hook が起動する振り分けの sh（モード 100755）。原本と配布先で同じパス。1 つ上の `bin/<os>-<arch>/` から、この機械の実行ファイルを選ぶ。無ければ 127 |
| `.ccnavi/scripts/ccnavi-git.sh` | 安全な git だけを通し、出力を抑えて結果だけ返すラッパースクリプト |
| `.ccnavi/scripts/ccnavi-ticket.sh` | チケットの状態を動かす。親だけが呼ぶ。状態を聞く `status` と、気づきの記録を書く `learn` は、サブエージェントも呼べる。本体は `ccnavi ticket`。取り込み済みの親子のチケットでは write-and-push（取り込んでから書き、書いたパスだけをコミットして push するまで完了にしない） |
| `.ccnavi/scripts/ccnavi-review.sh` | レビューの依頼と確認。親だけが呼ぶ。本体は `ccnavi review`。状態を書くサブコマンドは write-and-push の対象の親子のチケットで write-and-push。`decide <N> --preview`（見るだけ）もここにある。ユーザ専用の `close-early`・`decide` は `ccnavi-user-close-early.sh`・`ccnavi-user-decide.sh` に分かれている |
| `.ccnavi/scripts/ccnavi-user-agree.sh` | 承認し、`ccnavi-user-push-approved.sh` でコミットして push する。ユーザが端末で打つ。本体は `ccnavi user agree` |
| `.ccnavi/scripts/ccnavi-user-push-approved.sh` | 承認済みチケットの置き場だけをコミットし、保護されたブランチでなければ親のブランチへ push する。ユーザが打つ（エージェントからは止まる）。端末の `ccnavi-user-agree.sh`・ボードの承認とフローの保存・ユーザの判断の入口のあとに呼ばれる。`[<親>...]` で親子のチケットを限る。取り込み済みの親子のチケットはロックを取り、取り込んでから送る |
| `.ccnavi/scripts/ccnavi-fetch.sh` | セッション開始時に親ブランチと、ワークツリーの起点になる統合先ブランチを取ってくる。進めるのは fast-forward だけ |
| `.ccnavi/scripts/ccnavi-session-start.sh` | セッション開始時の入口。`ccnavi-fetch.sh` と `ccnavi-dirs.sh` を順に呼ぶ。SessionStart にはこれを登録する |
| `.ccnavi/scripts/ccnavi-dirs.sh` | セッション開始時に、プロジェクト直下のディレクトリを更新が新しい順に最大 10 件出す。超えるときは `ls` を案内する |
| `.ccnavi/scripts/ccnavi-sync.sh` | 親のブランチを取り込む（早送りか merge。衝突したら取りやめてユーザの対応に切り替える）。リモートから消えた親のブランチを診断する（閉じたかはホストの MR で決める） |
| `.ccnavi/scripts/ccnavi-branches.sh` | issue・MR に紐づくブランチを探す。読むだけ。ホストは sh が読み、手元の候補は `ccnavi branches` が集める |
| `.ccnavi/scripts/ccnavi-start.sh` | issue・MR を指定された依頼の着手の入口。`ccnavi-branches.sh` で候補を探し、無ければ Draft MR・ワークツリー・ブランチを作る |
| `.ccnavi/scripts/ccnavi-worktree.sh` | 直接作業のワークツリーを、取り込み先（メインのチェックアウトが開いているブランチ）から切る。ずれが無ければ確認なしで取り込み先のHEADから切る。cwdのブランチ違い・取り込み先が統合先でない・`origin/<取り込み先>` とのずれ（遅れ・進み・分岐・無い・リモート未確認）・書きかけがあるときは、切らずに終了コード 3 で状態・勧め・リスク・確認コードを出す。ユーザの了承を得て `--base`・`--accept`・`--copy-wip`/`--no-wip` を付けて打ち直す。`--side-by-side` で、横で作業する間の自動復元を緩める（`ask` ルールで確認が出る）。記録は共通のgitディレクトリの `ccnavi-direct/<名前>`。`--offline`・`--from-origin` は廃止 |
| `.ccnavi/scripts/ccnavi-stage.sh` | 書き込みを止められている保護対象のファイルを `wip/handoff/`（元と同じ階層。cwd が `.claude/worktrees/<名前>` の中ならそのワークツリーの `wip/handoff/`）へ `cp` する。`--commands` は編集済みの下書きの状態を出す（端末の前のユーザは `mv` の代わりに `ccnavi user handoff` で取り込める）。取り込むのはユーザ |
| `.ccnavi/scripts/ccnavi-clean.sh` / `ccnavi-clean.js` | ワークツリー 1 本の生成物（node_modules・.venv など）を消す。`worktree remove` の前に打つ。node が無ければ sh で同じものを消す。`--worktree <名前>` は掃除と `worktree remove` を 1 本で行う（本体は `ccnavi worktree drop`。cwd が中なら消さない）。子のブランチを消す `--branch` は `ccnavi-user-clean-branch.sh` に分かれている。配る（`scripts/ccnavi-setup.sh` の `DEPLOY_SCRIPTS`） |
| `.ccnavi/scripts/ccnavi-user-clean-branch.sh` | 親を閉じたときに残した子の手元のブランチを、条件を確かめ直して消す。ユーザが端末で打つ（エージェントからは止まる）。本体は `ccnavi worktree branch` |
| `.ccnavi/scripts/ccnavi-user-close-early.sh` | 残りがあるまま親を早めに閉じる判断を置く。ユーザが端末で打つ。残りを issue に書き出し、コメントを投稿する。本体は `ccnavi user close-early` |
| `.ccnavi/scripts/ccnavi-user-decide.sh` | 残った指摘（未解決のスレッド）の対応方針を決めて置く。ユーザが端末で打つ（ボードが押した選択を送る形も同じ）。本体は `ccnavi user reviewed` |
| `.ccnavi/scripts/ccnavi-user-lib-review.sh` | ユーザ専用のレビューの sh（`ccnavi-user-decide.sh`・`ccnavi-user-close-early.sh`）が共有する、ユーザの判断をホストに書く部品（指摘のスレッドへの返信と解決、issue の作成）。単体では動かない |
| `.ccnavi/scripts/ccnavi-common.sh` | 保護済み sh が共有する部分の入口。単体では動かない。パスの絶対化・ワークスペースルート・実行ファイルの場所・互換の版の食い違い・識別子とブランチ名の書き方の確かめを持ち、下の部品を最後に読み込む |
| `.ccnavi/scripts/ccnavi-common-state.sh` | `ccnavi-common.sh` の部品。state の置き場、統合先、親ブランチの push の関門、タイムアウト監視つきの git |
| `.ccnavi/scripts/ccnavi-common-lock.sh` | `ccnavi-common.sh` の部品。親子のチケットのロック |
| `.ccnavi/scripts/ccnavi-common-write-and-push.sh` | `ccnavi-common.sh` の部品。write-and-push。チケットの状態を書く一連の操作を 1 つにまとめる |
| `.ccnavi/scripts/ccnavi-common-host.sh` | `ccnavi-common.sh` の部品。ホスト（GitHub / GitLab）への接続 |
| `.ccnavi/scripts/ccnavi-common-log.sh` | `ccnavi-common.sh` の部品。診断ログ（`log_debug` / `log_info` / `log_warn` / `log_error`）。`logs/diag/<出どころ>.log` に 1 行足すだけ |
| `.ccnavi/scripts/ccnavi-common-review.sh` | レビューの sh が共有する、ホストのマージリクエストを読む部品。`ccnavi-common.sh` は読まず、レビューの sh だけが読む |
| `scripts/ccnavi-install.sh` | ccnavi を入れる入口。状況を調べ（`ccnavi-doctor.sh`）、入れる先に配り（`ccnavi-setup.sh`）、最後に lint で確かめる |
| `scripts/ccnavi-doctor.sh` | 入れる前に、この機械の状況を調べて 1 項目 1 行で報告する。何も書き換えない |
| `scripts/ccnavi-package.sh` | 配る側が ccnavi の配布用の zip を作る。ソースのリポジトリで打つ |
| `scripts/ccnavi-build-install.sh` | ccnavi・VS Code 拡張・Chrome 拡張をビルドし、ccnavi と VS Code 拡張をこの機械に入れる |
| `scripts/ccnavi-stop-review.sh` | Stop の AI 判定の agent 型 Stop hook を `.claude/settings.json` に登録・解除・点検する |
| `scripts/check_rules.py` | このリポジトリの共通レイヤーの `rules.yml` と `rule-expectations.yml` で `ccnavi --test-expectations` を呼ぶだけの薄いラッパースクリプト。引数でルールファイル（下書き）を差し替えられる。`CCNAVI_*` を外した環境で、承認済みチケット・state・記録を使わずに回し、終了コードは実行ファイルのまま返す（食い違いがあれば 1）。判定は実行ファイル側で行い、ここでは作らない。全件テスト（`tests/core/test_check_rules.py`）も同じ引数を使う。配布先には無く、そこでは `ccnavi --test-expectations` を直接打つ。`/ccnavi-config` スキルが呼ぶ |
| `extensions/` | VS Code 拡張（`vscode/ccnavi-board`）と Chrome 拡張（`chrome/ccnavi-approval`）、共有の設定（`shared/`） |
| `docs/` | 設計・要件・ADR・手引き（`guide/`）・ccnavi の開発の決まり（`claude/`） |
| `wip/` | 作業中の下書き（`design/`・`handoff/`・`proposals/`） |
| `tests/` | 受入テスト。内部の関数は呼ばず、標準入出力と終了コードだけを見る |
| `tests/__main__.py` | `python -m tests`。テストをモジュールごとに別プロセスで同時に回す。回す中身は discover と同じ |
| `tests/manual/gitlab/` | 実物または代役の GitLab に sh と実行ファイルを当てて 1 周する、ユーザが手で回す道具。自動テストは呼ばない |
| `tests/fixtures/` | テスト用のルール（`rules.yml`、言及の無い呼び出しを見る `rules-undeclared.yml`） |
| `.ccnavi/config/common/rules.yml` / `phases.yml` / `risks.yml` | このリポジトリ自身の共通レイヤーの設定（`phases.yml` はどのプロジェクトでも使える定義） |
| `.ccnavi/config/project/phases.yml` | このリポジトリ自身のレイヤーのフェーズ定義（このリポジトリのレイアウトに依存する定義） |
| `.ccnavi/config/common/rule-expectations.yml` | ルールが何を止めて何を通すかの期待する判定 |
| `extensions/vscode/ccnavi-board/` | VS Code 拡張。ボード・ルール管理・リスク管理・プロジェクト管理の画面 |
| `docs/adr/` | 設計判断の記録 |
