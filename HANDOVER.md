# 引き継ぎ

2026-09-12 時点の現状と次の一手。次に入る人が最初に読む想定。
判断の理由と経緯はここには書かず、[docs/adr/](docs/adr/README.md) に 1 枚ずつ置いてある。

## この道具は何か

Claude Code の hook から呼ばれ、危ないツール呼び出しを止め、
同時に「代わりに何をすればよいか」を名指しで返す。止めるだけでは足りない、
というのが出発点。`git push` を禁じられたエージェントは諦めず絶対パスで打ち直すので、
拒否は目的を諦めさせるのではなく別の道へ向け直さないと効かない。

読む順番は次のとおり。

| 文書 | 中身 |
|---|---|
| [CONTEXT.md](CONTEXT.md) | 用語集。全体ルール・チケット制御・直接作業・チケット作業・提案・写し・印。実装のことは書かない |
| [requirements.md](requirements.md) | 外から観測できる要求だけ。実装の理屈は書かない |
| [ccnavi.md](ccnavi.md) | 設計書。いまの実装がどう作られているか。経緯は書かない |
| [README.md](README.md) | 設定、ルールの書き方、モード、記録の読み方、JSON の形 |
| [docs/adr/](docs/adr/README.md) | 設計判断の記録。なぜそう決めたか、以前はどうだったか、採らなかった案 |

`knowledge/` は参照専用の資料で、git 管理外。Claude Code の hook にまつわる実測が
入っている。要件と実装の判断はほぼここから来ている。

## いま動くもの

hook の 7 イベント（`SessionStart` `UserPromptSubmit` `PreToolUse` `PostToolUse` `Stop`
`SubagentStart` `SubagentStop`）の全部。実行前のルール照合、実行後の監視、中核ファイルの
自己防衛、チケット制御（提案・承認・写し・フェーズ・ゲート・レビュー・実績のリスク）、
複数のリポジトリ、診断（`--test` `--test-samples` `--explain` `--lint` とその JSON）、
VS Code 拡張（ボード・ルール設定・リスク管理・プロジェクト管理）。dry-run で自分自身に
仕掛けてある。

```
main.py                     配布物の入口。PyInstaller が渡すスクリプト
ccnavi/hookio.py            stdin の payload の解釈と stdout に返す応答
ccnavi/rules.py             ルールの読み込みと検証
ccnavi/builtin.py           ルールを読めないときの組み込み既定
ccnavi/globmatch.py         glob から正規表現への翻訳
ccnavi/shellread.py         コマンド文字列のうち実際に実行される部分の切り出し
ccnavi/settings.py          環境変数と設定ファイルからの設定解決
ccnavi/gitstate.py          作業ツリーで実際に何が変わったかを git から読む
ccnavi/post.py              実行後の監視。検知・差し戻しの文・復元
ccnavi/ticket.py            チケットの読み込みと、そこが宣言する作業範囲。親子の部分集合
ccnavi/tree.py              作業ツリーの特定。判定の鍵はファイルの行き先
ccnavi/approval.py          承認済みの写し・フェーズの印・子ごとの記録・承認の画面
ccnavi/risk.py              実績で測るリスク。risk.yml・差分の計測・スクリプト・定性項目
ccnavi/phase.py             フェーズの終わりとゲート。提案から写しへの同期
ccnavi/phasetypes.py        フェーズの種類の定義（phases.yml）の読み込みと検証
ccnavi/review.py            レビューの依頼と確認。作業ツリーの前提検査と、sh が渡す写し（JSON）の判定。ネットワークに出ない
ccnavi/ops.py               チケットの状態を動かす ticket start / done / cancel / judge
ccnavi/audit.py             1 行 1 件の追記記録
ccnavi/lint.py              設定とルールの検証
ccnavi/diagnose.py          判定を実行せずに試す --test と --explain
ccnavi/cli.py               引数の解釈と振り分け。ticket / review の副命令を ops / review へ
ccnavi/events.py            hook のイベントごとの手順
ccnavi/judge.py             実行前の判定
ccnavi/reasons.py           判定に添える文面と理由コード
ccnavi/ruleload.py          この呼び出しに当てるルール集合の決定
ccnavi/subagent.py          SubagentStart / SubagentStop
ccnavi/ctxfile.py           additionalContext の組み立てと once の控え
ccnavi/selfguard.py         ccnavi 自身の設定ファイルと実行ファイルの控えと復元
ccnavi/modes.py             enable / dry-run / disable と終了コード
ccnavi/gitcmd.py            git を 1 回起こす
ccnavi/fsio.py              ファイルの読み書きの型
build.py                    PyInstaller の onedir で配布物を組み立てる
scripts/ccnavi-setup.sh     対象プロジェクトに設定を書き、実行ファイルとルールと sh を配る
tests/                      受入テスト。入口（cli.run）に引数と標準入力を渡し、応答だけを見る
tests/inproc.py             その起動をプロセスを起こさずに行う。起動の検査は test_entry.py だけ
tools/gitlab/               実物または代役の GitLab で 1 周する道具。人が手で回す。自動テストは呼ばない
vscode-extension/ccnavi-board/  VS Code 拡張
```

実行時の third-party 依存は PyYAML 1 本（ADR-0004）。PyInstaller と ruff は開発時にしか要らず、
配布物には入らない。

確認コマンド。

```sh
uv run python -m unittest discover -s tests -t .
uv run --with ruff ruff check .
uv run --with ruff ruff format --check .
uv run --with pyinstaller python build.py
```

## 未実装

要件書にあって手が付いていないもの。

- 確認の記憶（REQ-PRE-07）。同じ根拠で繰り返し聞かない仕組み。ルールが `ask` と書いた確認は
  記憶の対象外（ADR-0009）
- 確認の記憶の事後無効化（REQ-PST-04）。無効にする記憶がまだ無い。REQ-PRE-07 と対
- セッション開始時の提示（REQ-SES）。REQ-SES-02 / -03（却下された要求と想定範囲外の
  許可の区別）はチケットが入ったので書ける形になったが、まだ書いていない
- 診断コマンド（REQ-DIA）のうち、記憶の消去（REQ-DIA-05）
- 期限（REQ-CMN-08）はループの中で見ているだけで、実測していない
- 並行するチケット（REQ-TKT）のうち、GitHub の実物に対する `request` と `check` は
  実測していない。GitLab は実物（CE 18.5）で 1 周した（下の「落とし穴」）。
  自動テストは sh の代わりに写し（`--result`）を渡す形で通す

## 次にやること

**`test-py.sh` が、作業ツリーの抜け殻で落ちる（人が直す。hook はエージェントが触らない）。**
`git worktree remove` が `.venv` の 1 ファイルを消せずに抜け殻を残すことがある（下の
「作業ツリーが消せない」）。抜け殻はディレクトリとしては在るので `[ -d "$target" ]` を通り、
`uv run … unittest discover` が `Start directory is not importable: 'tests'` で落ちて、
関係のない差し戻しがモデルへ届く。`[ -d "$target/tests" ] || continue` に直せば済む。

**`ccnavi-review.sh` に、敵対的レビューで見つかった漏れが 2 つ残っている（人が直す）。**
スクリプトはエージェントが触らない決まりなので、直し方だけ書く。
(1) `origin` を読めなかったときの `fail` が URL をそのまま stderr に出す（114 行付近と、
その後の 2 か所）。`ssh://oauth2:<token>@host:2222/g/p.git` のように読めない綴りだと
トークンが漏れる。伏せた綴りを origin を読む前に 1 度作り、`fail` にはそれだけ渡す。
(2) `origin` サブコマンドの伏せ字が最初の `@` まで（`s#^([a-z]+://)[^/@]+@#`）で、解析は
最後の `@` まで。`glpat-A@B` のように `@` を含む資格情報だと後半が出る。`[^/]*@` に直し、
scheme の `[a-z]+` は大文字も含める。exe 側の `remote_kind` は同じ規則に直してある。
(3) usage の `check` の説明が「依頼より後の未解決スレッドが無ければ」のままで、いまの挙動
（時刻で絞らず未解決の全部を数える。ADR-0031）と違う。冒頭の一覧にも `handoff` `ready`
`wrapup` `origin` が無い。

**複数のリポジトリ（REQ-MLT、設計 §11）で残っているもの。**

- `.claude/scripts/ccnavi-git.sh` の記録を `logs/<プロジェクト>/` へ寄せる（REQ-MLT-14 の後半）。
  今は toplevel の `logs/` に書くので、プロジェクトの中に出る。sh は `guard-scripts` が止めるので人が直す
- プロジェクトの数に対する `ms`。プロジェクト 5 本で期限の半分を超えるなら、ルールの読み込みに
  mtime の控えを足す
- Windows でプロジェクトから切った作業ツリーの `.git` ファイルの `gitdir:` の綴りと区切り
- `projects/` をワークスペースの `.gitignore` に入れたとき、Claude Code がプロジェクトの中の
  CLAUDE.md を読むか。読まれるならワークスペースの CLAUDE.md と矛盾しないように書く
- `cwd` がプロジェクトの中にあるとき、hook の `${CLAUDE_PROJECT_DIR}` がワークスペースルートのままか

**VS Code 拡張のプロジェクト管理画面（0.3.0）とリスク管理画面（0.4.0）は、まだ拡張開発ホストで
通していない。** 拡張の README の手動確認の表 25〜38 を 1 度踏む。

**並行するチケット（REQ-TKT、設計 §9）で実測が要るもの。**

- `SubagentStart` の `additionalContext` がサブエージェントに届くか。届かなければ、
  サブエージェント内の最初の `PreToolUse` で渡す形に変える（設計 §9.12）
- `isolation: worktree` で起動したサブエージェントの hook が受け取る `cwd`。判定は行き先で
  決まるので止め方は変わらないが、ゲートは cwd で親を引くので、そこが割れる
- GitHub の実物に `request` / `check` を当てる。GraphQL の `reviewThreads` は文書どおりに
  書いただけ。GitLab の変更要求（`request_changes`）だけは CE に無い機能で、EE でしか当てられない
- `.claude/scripts/` への Write は `guard-scripts` が止める。sh 3 本はこのリポジトリで作ったので
  入っているが、他のプロジェクトへ配るときは導入スクリプトが写す

**状態遷移（設計 §9.6）で、いまの挙動として書いてあるが、それでよいかを決めていないもの。**

- `ticket start` / `done` は写しの有無を見ない。未承認のまま `doing/` `done/` まで進める。止めるか、
  せめて「未承認」を stderr に出すかは決めていない（`--lint` は言う）
- `--approve` は `done/` にある未承認の提案も束に入れる。承認した写しは次の hook で即座に閉じる。
  `cancelled/` と同じく `done/` も除くほうが自然に見える
- 人が子を再開しても、そのフェーズの `reviewed` は残る。再び `done` にしてもゲートは閉じず、
  告知も出ない。再開の手順に「印も消す」を入れるか、写しを戻したときに機構が消すかは決めていない

**権限モードへの委譲がどれだけ出るかを実測する。** ここがいちばん未知。記録の `code` が
`UNDECLARED` の行を数え、`tool` と `subject` の傾向を見る。
同じ場所が繰り返し出るなら `allow` に 1 行足す先が決まっているということで、
毎回違う場所が出るなら `allow` の粒度が細かすぎる。数が多いまま放置されると、
人は中身を読まずに承認するようになり、ルールが置いた確認もそこに埋もれる。
`RULE_ASK` との比も同時に見る。前者が後者を大きく上回っているうちは、
確認の質が落ちている。

**実行後の監視を実測で見る。** 記録の `event` が `PostToolUse` の行を数え、`decision` が
`deny` の回に何が挙がっているかを見る。見たいのは 3 つ。`worktree-unreadable` が出ていないか
（出ていれば監視は 1 件も見ていない）、`detail` の `preexisting` が毎セッション大量に出ていないか
（出ていれば控えの取り方が合っていない）、`paths` に同じ場所が繰り返し出ていないか
（出ていれば止めるべきは経路ではなく出力先の設定）。`git status` を毎回起こす代金も、
`ms` の欄で `PreToolUse` の行と比べられる。

**誤検知の数を実測で比べる。** 記録に `degraded` が付くので、止めたもののうちどれだけが
読み切れないまま出た判定かを数えられる。`.claude/ccnavi/log.jsonl` を貯めて、導入前の誤検知
（`echo "git push"` `grep -n "git push"` の類）が消えたことと、`degraded` の割合が小さいことを
確かめる。割合が大きければ縮退の条件が広すぎる。

**残っている誤検知は 1 語のパターン。** 空白を含まないパターンは、引用の中にあっても当たる。
`echo ".env"` は今も `.env` のルールに当たる。塞ぐならルール側に「コマンド位置だけ」
「引数だけ」の区別を足すことになり、ルール書式の版が上がる（ADR-0010）。

**前からある取りこぼし。** `git -C /repo push` は `*git push*` に当たらない。`glob` が語の並びを
そのまま探すからで、記法を替えても消えていない。knowledge の同じ文書がグローバルオプションの
飛ばし方を書いている。

## 実測で分かった落とし穴

次のセッションで同じところを踏まないように。

- **作業ツリーが消せない（Windows）。`git worktree remove` が `Permission denied` で落ち、
  `.venv` の 1 ファイルだけの抜け殻が残る。** 原因は uv のハードリンクと Windows の
  削除規則の組み合わせで、消そうとしている作業ツリーで**何も走っていなくても**起きる。
  2026-09-11 に隔離した場所で再現させて確かめた（`fsutil hardlink list` と、掴む側 /
  消す側を分けた実験）。
  1. uv は wheel の中身をキャッシュから venv へハードリンクで置く。実体は 1 つで、
     `_yaml.cp312-win_amd64.pyd` は main・全作業ツリー・uv のキャッシュで同じファイル
     （このプロジェクトで C 拡張を持つ依存は PyYAML だけなので、当たるのはこの 1 本）
  2. Windows は、実体が DLL として読み込まれている間、**どの名前も**消させない。
     rename は通る。Linux は mmap 中でも unlink できるので、ここは Windows だけの話
  3. 並行するセッションはターンの終わりに `test-py.sh` で数分テストを走らせ、
     そこで PyYAML を読み込む。作業ツリーが数本あると、ほぼ常に誰かが掴んでいる
  4. 掴まれている間に別の作業ツリーを消そうとすると、その 1 ファイルだけが残る
  対処は入れた（`pyproject.toml` の `[tool.uv] link-mode = "copy"`。複製にすれば実体が
  分かれる）。ただし**既にある `.venv` はハードリンクのまま**なので、効くのは次に作る
  ぶんから。いま在るものを切り替えるなら、テストが走っていないときに各作業ツリーの
  `.venv` を消して作り直す。
  それでも「自分のテストが走っている間に自分の作業ツリーを消せない」は残る。落ちたら、
  掴みが離れるのを待つか、抜け殻を `mv` で `.claude/worktrees/` の外へ出して
  `git worktree prune` する（rename は通るので、これは必ず成功する）。

- **`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。**
  中括弧のままの文字列が渡る。`env` には相対パスを書く
- **`env` ブロックは再読み込みされない。** 値を変えてもセッションを開き直すまで
  古い値が残る。hook の `command` は即座に反映される。ccnavi は未知のモード値を報告するので、
  古い値が残っていれば原因はすぐ分かる
- **ツールのプロセスに `CLAUDE_PROJECT_DIR` は入っていない。** hook の環境にだけ来る。
  だからワークスペースルートは上方向の探索でも見つけられるようにしてある
- **`.claude/settings.json` はスキーマ検証があり、未知のトップレベルキーを拒否する**
- **Windows は実行中の exe を上書きできない。** 名前の変更はできるので、
  `build.py` は組み上がったものを別の場所に作り、古いほうを `.old` に退避してから
  入れ替える。失敗しても数回やり直す
- **テストが session の環境を継承する。** 自分自身に仕掛けているので
  `CCNAVI_MODE` が入ってくる。テスト側でモードを固定してある
- **PyInstaller は指定したスクリプトをパッケージの外の素のスクリプトとして走らせる。**
  `ccnavi/__main__.py` を直接渡すと相対 import が解決できない。だから絶対 import で
  書いた `main.py` を root に置いて、それを渡している
- **`shlex.lineno` はトークンの後ろの空白まで進んだ位置を返す。** 行末のトークンは
  返ってきた時点で次の行に数えられている。ヒアドキュメントの本文を落とすときに
  「同じ行に残っている語」を判定するので、行番号はトークンを読む前に控える
- **`shlex` は引用された `<<` と素の `<<` を区別しない。** どちらも同じ文字列で
  返ってくるので、`grep -n "<<" README.md` はヒアドキュメントの始まりに見える。
  閉じない本文として縮退し、ヒアドキュメントのルールに当たって止まる。
  **これは直す対象ではない。** 許容する誤検知として設計に書いてある
  （ccnavi.md §6.3、§12.2）。塞ぐにはトークンが引用されていたかを
  `shlex` から取り出す必要があり、公開された手段が無い
- **算術式の `$((1 << 2))` は左シフトであってヒアドキュメントではない。**
  `$` `((` … `))` として落としてから読む。落とさないと本文の始まりに見えて、
  コマンド全体が読めなくなる
- **`shlex` は行継続を知らない。** `\` と改行をトークンの中に改行として残すので、
  語のつなぎ目として読まれてしまう。トークン化の前に 2 文字とも落とす
- **Python の識別子に空白は入らない。** テスト名を日本語で書くとき
  `def test_warn は…` のように英字と日本語の間に空白を入れると構文エラーになる
- **複合コマンドは 1 つの区間が読めなければ全体が縮退する。** `a && bash -c "..."`
  は `a` の側も生の文字列で判定される。縮退の向きが安全側なのでそのままにしてある
- **lint hook は編集 1 回ごとに走る。** 複数ファイルにまたがる変更は途中の状態で
  必ず差し戻される。順番に直せば通るので、途中の差し戻しは無視してよい
- **Windows のコンソール経由で日本語を引数に渡すと CP932 になり、`jq --arg` が UTF-8 でない
  JSON を作る。** 本文はファイルで渡すこと。`jq` の実体は `C:\Program Files\jq\jq` で、
  パスに空白がある。`"$JQ"` と引用しないと割れる（本番の sh は全部引用済み）
- **Docker Desktop を起動すると、`restart=unless-stopped` の GitLab が勝手に上がる。** 2GB の VM に
  収まらず engine ごと落ち、`docker exec` も `docker ps` も 500 を返すようになった。GitLab CE には
  4GB 要る。VM を 4GB にして `tools/gitlab/probe_gitlab.py` で 1 周した

GitLab の実物（CE 18.5.4）で分かったこと。

| 分かったこと | どうしたか |
|---|---|
| 変更要求（`POST .../request_changes`）は CE の `lib/api` に無い。EE 限定 | 当てられない。sh の `requested_changes` の読みは EE の文書どおりのまま。CE では `reviewers` の `state` は `unreviewed` / `reviewed` / `approved` だけ |
| URL にトークンを埋めた origin（`http://oauth2:<token>@localhost:8929/...`）で host にトークンが混ざり、`origin` の出力にそのまま出た | sh はユーザ情報を落とし、出力で伏せる。exe の `remote_kind` も読み飛ばす。`tests/test_review_origin.py` |
| ラッパ経由の push は `GIT_CONFIG_COUNT` を落とす（設定の注入を塞ぐため）ので、環境変数で credential helper を差し替えても効かず、`GIT_TERMINAL_PROMPT=0` で即失敗する | 認証は git の設定側に置く。probe はリポジトリの `credential.helper` を空文字で一度リセットしてから、トークンを返す helper を足す。実運用なら Git Credential Manager に保存しておく |
| トークンは `docker exec -i gitlab gitlab-rails runner -` に Ruby を流し込んで作れる（`tools/gitlab/make_gitlab_tokens.rb`）。ブラウザも初期パスワードも要らない | GitLab 18 は組織（organization）とパスワードの強度を求める。root と reviewer の 2 人分を作る |
| 起動直後は API の `PUT` が 30 秒を超えることがあった | probe は 120 秒で 3 回まで待つ。sh の curl は無期限 |
| 未解決の一覧で、位置の無い討論が ` :0 ` と出る | 直していない。読めるので後回し |

## セッション中に自分自身へ仕掛けたもの

`.claude/settings.json` に入っている hook。

- 7 つのイベント（`SessionStart` `UserPromptSubmit` `PreToolUse` `PostToolUse` `Stop`
  `SubagentStart` `SubagentStop`）に `dist/ccnavi/ccnavi`。`CCNAVI_MODE` は dry-run なので
  手を出さない。通知だけ返す。`PostToolUse` の `matcher` は絞らない。絞ると Bash が抜ける
- `PostToolUse` の `Write|Edit|NotebookEdit` に `.claude/hooks/lint-py.sh`。
  Python ファイルの編集時だけ動き、整形と検査をかける

`.claude/hooks/test-py.sh` は `Stop` に登録してターンの終わりに 1 回テストを走らせるための
スクリプトだが、リポジトリの `settings.json` には登録していない。回すなら利用者ごとの
`settings.local.json` で `Stop` に足す（ADR-0036）。

検査もテストも、通らないと exit 2 で差し戻される。うるさければ hooks から外す。

Stop の差し戻しには上限がある。3 回で打ち切って止まらせる。回数はセッションごとに
`.claude/ccnavi/session/<セッション>.retries` に置いて数える。数の一生は次のとおり。作るのは
差し戻すときだけ。消えるのは、テストが通ったとき、新しい連鎖が始まったとき
（`stop_hook_active` が false）、上限に達したとき、そして 1 時間経ったとき。最後の 1 つが
要るのは、連鎖の途中でセッションが終わるとファイルが取り残されるから。

どのツリーをテストするかも、そのターンで触ったものだけに絞ってある。どこを触ったかは
`PostToolUse` の `lint-py.sh` が `<セッション>.trees` に書き残し、`Stop` の `test-py.sh` が
それを読む。ツリーは編集したファイルからいちばん近い `pyproject.toml` を上に辿って決める。

実行ファイルはどちらの hook でも作り直さない。PyInstaller が 11 秒かかるので、
動かして確かめるときに手で `uv run --with pyinstaller python build.py` を回す。

`dist/`、`build/`、`.claude/ccnavi/log.jsonl` は git 管理外。
記録には絶対パスとコマンド全文が入るのでコミットしない。
