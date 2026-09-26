# 引き継ぎ

現状と次の一手。次に入る人が最初に読む想定。判断の理由は [docs/adr/](docs/adr/README.md)。

## この道具は何か

Claude Code の hook から呼ばれ、危ないツール呼び出しを止め、代わりに何をすればよいかを名指しで返す。
拒否は目的を諦めさせるのではなく、別の道へ向け直さないと効かない（エージェントは絶対パスで打ち直す）。

| 文書 | 中身 |
|---|---|
| [CONTEXT.md](CONTEXT.md) | 用語集 |
| [requirements.md](requirements.md) | 外から観測できる要求 |
| [ccnavi.md](ccnavi.md) | 設計書。いまの実装の作り |
| [README.md](README.md) | 設定、ルールの書き方、モード、記録の読み方、JSON の形、構成、開発の手順 |
| [docs/adr/](docs/adr/README.md) | 設計判断の記録 |

`knowledge/` は参照専用の資料で git 管理外。Claude Code の hook にまつわる実測が入っている。

## いま動くもの

hook の 7 イベントの全部、実行前のルール照合、実行後の監視、コアファイルの自己防衛、チケット制御、
複数のリポジトリ（層の和。設計 11、REQ-MLT）、診断（`--test` `--test-samples` `--explain` `--lint`）、
VS Code 拡張（ボード・ルール設定・リスク管理・プロジェクト管理・フェーズ管理）。
このリポジトリ自身には dry-run で仕掛けてある。

チケットの流れは ADR-0055、層の和は設計 11.2〜11.4.2、共通層の置き場は ADR-0052、
設定と記録の置き場は ADR-0042。ファイルの構成と開発用 hook（`lint-py.sh` `test-py.sh` `mark-ext.sh`
`test-ext.sh`）は README の「構成」「開発」。

リスク管理画面は共通層の 1 本だけを開く（設計 11.11）。ルール設定画面とフェーズ管理画面は層に追従する。

確認コマンド。

```sh
uv run python -m unittest discover -s tests -t .          # 全件
uv run python -m unittest discover -s tests/core -t .     # 1 グループ（core guard config ticket sh e2e）
uv run --with ruff ruff check .
uv run --with ruff ruff format --check .
uv run --with pyinstaller python build.py
```

テストは `python -m unittest` で回す。ファイルを直接実行すると `tests` パッケージを import できずに落ちる。
回すグループは `.claude/skills/commit/references/test-groups.md` の表で決める。

## 未実装

- 確認の記憶（REQ-PRE-07）。ルールが `ask` と書いた確認は対象外（ADR-0009）
- 確認の記憶の事後無効化（REQ-PST-04）。REQ-PRE-07 と対
- セッション開始時の提示（REQ-SES）。REQ-SES-02 / -03 はチケットがあるので書ける形
- 記憶の消去（REQ-DIA-05）
- 期限（REQ-CMN-08）はループの中で見ているだけで、実測していない
- GitHub の実物に対する `request` と `confirm` は実測していない。GitLab は実物（CE 18.5）で 1 周を確かめてある。
  自動テストは写し（`--result`）を渡す形で通す
- REQ-TKT-35 の後半。`SubagentStart` は親の局面を名指ししない（フェーズの番号と種類までは渡す）

## 次にやること

### 配布する sh を確かめる

```
uv run python -m unittest tests.e2e.test_e2e_sh -v
```

走り出しに出る `sh =` がワークスペースルート側を指していることを確かめる。効くのはワークスペース側の 1 本だけ。
写す前の版を測るときは `CCNAVI_SH_DIR=<場所>` で差し替える。組み立て済みの実行ファイルを試すので、
`ccnavi/` を直したら組み立て直してから回す（無ければ skip）。モード B（`projects/` を使う形）に触ったら回す。

### 未了: Python 側

1. **シェルの守りは、空白を含むパスへの `>` の書き込みを止めない。** リダイレクトの行き先を拾う形
   （`selfguard._WRITE_VERBS`）が引用の中の空白の目印（`\x01`）で止まる。
   `echo x > "projects/has space/.ccnavi/config/rules.yml"` は `builtin-guard-setting-files` に当たらず、
   ルールが何も言わなければ ask になる。`tee` `cp` `mv`、`cd` してからの相対の `>`、Write / Edit は止まる。
   ワークスペースルートの絶対パスに空白があるときも同じ
2. **`--lint` が、まだリポジトリの無い `projects/` の無視を確かめない。** `lint._projects` は
   `tree.projects()` が空なら先に返る。clone する前が一番確かめたい時点
3. **孤児のワークツリー。** 元リポジトリであるプロジェクトを消すと列挙から外れ、その中のパスがワークスペースルートとして
   判定される（プロジェクトの `deny` が外れる）。判定は変えず `--lint` と `--explain` が名指しする方針だが、まだ言わない
4. **`message` の `{root}`。** `--lint` が「`message` に `{root}` の無い `.ccnavi/scripts/` の綴りがある」を warn で言うようにする
5. 層が無いことを `--lint` が言うか（消す・古いコミットへ `checkout` するとプロジェクトの deny が痕跡なく消える）は別の issue で決める

### ccnavi 自身の設計の穴

1. **保護済みファイル（`.ccnavi/scripts/`、`.claude/hooks/`、`rules.yml`）を直すチケットは `implement` では承認されない。**
   `staging` 種別（自身の層の `phases.yml`、`scope: [wip/design/*, tests/*]`）のフェーズで完成品を
   `wip/design/scripts/` に全文で置き、人が写してコミットする。写す順は `ccnavi-common.sh` が先
   （3 本が起動時に読む）。`phases.yml` は人が持つ設定で、エージェントは足せない
2. **シェルでフィクスチャを組み立てると `builtin-guard-setting-files` が反応する。** コマンドに `.ccnavi` が
   含まれるだけで当たる。受入テストは Python の中で写すので通るが、手で確かめるときに踏む

### 未了: `ccnavi-review.sh` の usage が実際の挙動と違う（人が直す）

usage の `confirm` の説明が「依頼より後の未解決スレッドが無ければ」のままで、挙動（時刻で絞らず未解決の全部を
数える。ADR-0031）と違う。冒頭のコメントの一覧にも `ready` `close-early` `origin` が無い。
`.ccnavi/scripts/` は `deny` なので、人が直すか `staging` のフェーズで写す版を作る。

### 複数のリポジトリで確かめること

- プロジェクトの数に対する `ms`。5 本で期限の半分を超えるなら、ルールの読み込みに mtime の控えを足す
- `projects/` をワークスペースの `.gitignore` に入れたとき、Claude Code がプロジェクトの中の CLAUDE.md を読むか
- `cwd` がプロジェクトの中にあるとき、hook の `${CLAUDE_PROJECT_DIR}` がワークスペースルートのままか

### VS Code 拡張

- プロジェクト管理画面（0.3.0）・リスク管理画面（0.4.0）・フェーズ管理画面（0.5.0）は拡張開発ホストで通していない。
  拡張の README の手動確認の表 25〜44 を 1 度踏む
- フェーズ管理画面とリスク管理画面の既知の限界。`rules.yml` が壊れている間は保存できない。YAML の構文が壊れた
  ファイルは画面から直せない。保存はアトミックではなく、競合の検出は更新時刻だけ。CRLF と BOM は保存で落ちる

### 並行するチケット（REQ-TKT、設計 9）で実測が要るもの

- `SubagentStart` の `additionalContext` がサブエージェントに届くか。届かなければ最初の `PreToolUse` で渡す（設計 9.12）
- `isolation: worktree` で起動したサブエージェントの hook が受け取る `cwd`（親を cwd で引くので、そこが割れる）
- GitHub の実物に `request` / `confirm` を当てる。GraphQL の `reviewThreads` は文書どおりに書いただけ。
  GitLab の `request_changes` は EE でしか当てられない

### 決めていないもの

- 人が子を再開しても、そのフェーズの `reviewed` は残り、再び `finish` しても止まらず告知も出ない（設計 9.6）。
  再開の手順でマーカーも消すか、機構が消すかは決めていない

### 記録で実測すること

- **権限モードへの委譲。** `code` が `UNDECLARED` の行を数え、`tool` と `subject` の傾向を見る。同じ場所が繰り返すなら
  `allow` に 1 行足す、毎回違うなら `allow` の粒度が細かすぎる。`RULE_ASK` との比も見る
- **実行後の監視。** `event` が `PostToolUse` で `deny` の回を見る。`worktree-unreadable` が出ていないか、`detail` の
  `preexisting` が毎セッション大量に出ていないか、`paths` に同じ場所が繰り返し出ていないか。`ms` を `PreToolUse` と比べる
- **誤検知。** `degraded` の割合を数える（`echo "git push"` の類が止まっていないことも）。大きければ縮退の条件が広すぎる

### 残っている誤検知と取りこぼし

- 空白を含まないパターンは引用の中でも当たる（`echo ".env"` が `.env` のルールに当たる）。塞ぐならルール書式の版が上がる（ADR-0010）
- `git -C /repo push` は `*git push*` に当たらない。knowledge にグローバルオプションの飛ばし方がある

### 未了: 別件

- `build.py` の置き換えが `PermissionError` で落ちると、`dist/ccnavi.target` が書かれない
- ワークスペースの `.git` の commit-graph の控えの一覧が欠けた控えを指している。`git commit-graph write --reachable --split=replace` で直る
- 承認済みチケットの書き込みが原子的でない。途中で機械が落ちると中身が NUL で埋まる
- Windows で `tests.guard.test_fallback` が 1 件落ちる。テストが絶対パスを引用せずに埋め込んでおり、bash が `\` を落とす。
  ガードの判定は正しく、テストの綴りを直す
- 未解決の一覧で、位置の無いスレッドが ` :0 ` と出る

## 実測で分かった落とし穴

Claude Code の振る舞いについて測った前提は設計書の付録 C。測り直したときは両方を直す。

- **ワークツリーが消せない（Windows）。** uv のハードリンクで `.venv` の `_yaml.*.pyd` が全ツリーで同じ実体になり、
  誰かのテストが読み込んでいる間は消せない。`pyproject.toml` の `link-mode = "copy"` で分けてあるが、
  ハードリンクで作った `.venv` はそのままなので、テストが走っていないときに作り直す。自分のテストが走っている間は残る。
  落ちたら残ったディレクトリを `mv` で `.claude/worktrees/` の外へ出して `git worktree prune`。
  先に `sh .ccnavi/scripts/ccnavi-clean.sh <名前>` で生成物を消すと node_modules で止まる分は避けられる
- **`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。** `env` には相対パスを書く
- **`env` ブロックは再読み込みされない。** セッションを開き直すまで古い値が残る。`command` は即座に反映される
- **ツールのプロセスに `CLAUDE_PROJECT_DIR` は入っていない。** hook の環境にだけ来る
- **`.claude/settings.json` は未知のトップレベルキーを拒否する**
- **PyInstaller は指定したスクリプトをパッケージの外で走らせる。** 相対 import が解けないので、絶対 import の `main.py` を渡す
- **`shlex` は引用・`#`・改行・行継続・`<<` の扱いがシェルと違う。** `shellread.py` は先に原文を走査してから語の分割だけを
  shlex に任せる。走査か shlex のどちらかに手を入れたら `tests/core/test_shellread.py` の `SHELL_CASES`（bash 3.2 と zsh で実測）を回す。
  引用された `<<` による縮退は許容する誤検知（ccnavi.md 6.3、12.2）
- **`$( )` の中の `case` は bash 3.2 と zsh で読みが割れる。** `case` が現れたら縮退させている（許容した誤検知）
- **複合コマンドは 1 つの区間が読めなければ全体が縮退する。** 安全側なのでそのまま
- **Python の識別子に空白は入らない。** `def test_warn は…` のように英字と日本語の間に空白を入れると構文エラー
- **Windows のコンソール経由で日本語を引数に渡すと CP932 になり、`jq --arg` が UTF-8 でない JSON を作る。** 本文はファイルで渡す。
  `jq` の実体は `C:\Program Files\jq\jq` で、`"$JQ"` と引用しないと割れる
- **Windows の `gitdir:` の綴り**（git 2.39.2、Git Bash と PowerShell）。絶対パス、区切りは `/`、ドライブレターは大文字、
  `gitdir:` の後ろは半角空白 1 個。`ccnavi_project`（sh）と `tree.py` がこれを前提にしている
- **Docker Desktop を起動すると `restart=unless-stopped` の GitLab が勝手に上がり、2GB の VM では engine ごと落ちる。**
  GitLab CE には 4GB 要る

GitLab の実物（CE 18.5.4）で分かったこと。

| 分かったこと | どうしたか |
|---|---|
| 変更要求（`POST .../request_changes`）は EE 限定 | 当てられない。CE の `reviewers` の `state` は `unreviewed` / `reviewed` / `approved` だけ |
| URL にトークンを埋めた origin はそのままでは `origin` の出力に出る | sh は利用者の情報を落として伏せる。実行ファイルの `remote_kind` も読み飛ばす |
| ラッパースクリプト経由の push は `GIT_CONFIG_COUNT` を落とすので、環境変数で credential helper を差し替えても効かない | 認証は git の設定側に置く（probe はリポジトリの `credential.helper` を空にしてから足す） |
| トークンは `docker exec -i gitlab gitlab-rails runner -` に Ruby を流して作れる（`tools/gitlab/make_gitlab_tokens.rb`） | root と reviewer の 2 人分を作る |
| 起動直後は API の `PUT` が 30 秒を超えることがある | probe は 120 秒で 3 回まで待つ |

`dist/`、`build/`、`logs/` は git 管理外。記録には絶対パスとコマンド全文が入るのでコミットしない。
