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
| [CONTEXT.md](CONTEXT.md) | 用語集。全体ルール・チケット制御・直接作業・チケット作業・提案・承認済みチケット・印。実装のことは書かない |
| [requirements.md](requirements.md) | 外から観測できる要求だけ。実装の理屈は書かない |
| [ccnavi.md](ccnavi.md) | 設計書。いまの実装がどう作られているか。経緯は書かない |
| [README.md](README.md) | 設定、ルールの書き方、モード、記録の読み方、JSON の形 |
| [docs/adr/](docs/adr/README.md) | 設計判断の記録。なぜそう決めたか、以前はどうだったか、採らなかった案 |

`knowledge/` は参照専用の資料で、git 管理外。Claude Code の hook にまつわる実測が
入っている。要件と実装の判断はほぼここから来ている。

## いま動くもの

hook の 7 イベント（`SessionStart` `UserPromptSubmit` `PreToolUse` `PostToolUse` `Stop`
`SubagentStart` `SubagentStop`）の全部。実行前のルール照合、実行後の監視、中核ファイルの
自己防衛、チケット制御（提案・承認・承認済みチケット・フェーズ・ゲート・レビュー・実績のリスク）、
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
ccnavi/approval.py          承認済みチケット・フェーズの印・子ごとの記録・承認の画面
ccnavi/risk.py              実績で測るリスク。risk.yml・差分の計測・スクリプト・定性項目
ccnavi/phase.py             フェーズの終わりとゲート。提案から承認済みチケットへの同期
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
- REQ-MLT-14 の後半（記録を `logs/<プロジェクト>/` へ寄せる）は `sh-ws-root` で入った。
  要求表への反映だけが未了（下の「未了: 要求表と設計書への反映」）
- REQ-TKT-35 の後半。`SubagentStart` は親の局面（作業中・レビュー待ちなど）を名指ししない。
  フェーズの番号と種類までは渡す。名指しするのは `--explain` とボードだけ

## 次にやること

### 入った: sh がモード B で動くようになった（`sh-ws-root`）

**配布先に入っている。** 写す作業は済んだ。`.claude/scripts/`（`ccnavi-common.sh` を
新設）、`.claude/hooks/test-py.sh`、`.claude/ccnavi/rules.yml`、`scripts/ccnavi-setup.sh`。

**次に同じ形の作業をする人へ。** これらは `deny` の対象でエージェントが書けない
（`judge.py:246-247`「チケットはルールが何も言わなかったときだけ見る。ルールのほうが
強い」）。ガードは緩めない。完成品を `wip/design/scripts/` に全文で置き、人が写し、
人がコミットする形で通した。手順書（`COPY.md`）も同じ場所に置いた。フェーズの種類は
`staging`（写す版の作成）を使う。写す順は `ccnavi-common.sh` が先。3 本が起動時に
`.` で読むので、本体だけ先に写すと sh が全部動かなくなる。

この一式は `wip/` ごとマージ前に消してある（`ready` が「途中の作業を既定のブランチに
残さない」を求めるため）。中身は git の履歴に残っている。

確かめ方。

```
CCNAVI_E2E=1 uv run python -m unittest tests.test_e2e_sh -v
```

走り出しに、測った `sh` と `exe` の場所が出る。**`sh =` がワークスペースルート側を
指していることを確かめること。** 作業ツリーの `.claude/scripts` を指していたら、
そのツリーに checkout された写しを測っている。`.claude/scripts/` は git が運ぶので
どの作業ツリーにも写しがあるが、実際に効くのはワークスペース側の 1 本だけ。
テストは実装（`ccnavi_workspace`）と同じ規則で `.claude/worktrees/` の下を候補から
外して上へ歩くので、既定ではワークスペース側を向く。

写す前の版を測りたいときは `CCNAVI_SH_DIR=<場所>` で出どころを差し替える。

`tests/test_e2e_sh.py` は重い（実 git・実行ファイル 18MB の写し）ので `CCNAVI_E2E` が
無ければ skip する。**モード B（`projects/` を使う形）に触ったら回すこと。**

写す版で直したもの。

- **保護済み sh 3 本が、自分の根を git に聞いていた**（`--show-toplevel` /
  `--git-common-dir`）。モード A では git のトップとワークスペースルートが一致するので
  露見しなかったが、モード B では一致しない。結果、`ccnavi-ticket.sh` と
  `ccnavi-review.sh` がプロジェクトの中で動かず、後者はプロジェクトに `.claude/` を
  作って失敗し、子チケットの push ガードが黙って効かなくなり、記録が
  `projects/<名前>/logs/` に出ていた
- 根の探し方を `ccnavi-common.sh`（新設）に切り出した。`cwd` から上へ歩いて
  `.claude/scripts/` を持つディレクトリを探す。**`.claude/worktrees/` の下は候補から
  外す。** `.claude/scripts/` は git が運ぶのでどの作業ツリーにも写しがあるが、
  承認済みチケットと `state/` は追跡外で運ばれない。根は運ばれないほうに合わせる
- `worktree add` の行き先を検査するようにした。ワークスペースの `.claude/worktrees/` の
  外なら止め、`cwd` に合わせた正しい綴りを文面に出す。知らないオプションも止める
- `origin` の伏せ字を `ccnavi_mask_url` に集約し、生の URL を文面に入れる綴りを
  1 つも残していない（上の (1)(2) はこれで塞がった）
- `test-py.sh` の存在チェックを `[ -d "$target/tests" ]` にした
- `rules.yml` の拒否の文面 3 か所を `{root}/.claude/scripts/...` にした
- `ccnavi-setup.sh` の配布と点検の一覧に `ccnavi-common.sh` を足した

### 未了: 要求表と設計書への反映

`ccnavi.md` §25 と `requirements.md` の REQ-MLT 表に、上の振る舞いを**書いていない**。
進行中の `config-union` が §25 を構造ごと改版するため、先に足すと解き直しになる。
**実装が入っているのに要求表に無い期間ができている。** `config-union` が統合先に
入ったら足すこと。

`wip/design/sh-ws-root.md` の 2 節も、根の決め方を「最初に当たったもの」と書いたまま。
実装は「`.claude/worktrees/` の下を外して最初に当たったもの」。上の説明が正。

### 未了: Python 側の 6 件（`config-union` の後）

2026-09-12 の監査で見つけたもの。`config-union` が同じ場所を作り直すので、それが
統合先に入ってから出す。

1. **識別子の接頭辞が消える。** `Record` を全ツリーで使い回すため、壊れたルールを持つ
   プロジェクトが 1 つあると、それ以降のツリーが「組み込み」扱いになり `zzz:own` が
   `own` になる（`ruleload.py:37`、`events.py:116`）。REQ-MLT-07 が壊れている
2. **プロジェクト名に空白が入ると自己防衛が抜ける。** `selfguard.py:222` の
   `[^\\/ \x00]+` が空白を除いているため、`projects/has space/config/rules.yml` への
   Write もシェル書き込みも止まらない。同じ欠陥が `ticket.py:712` の `wip/<名前>/` にも
   ある。**エージェントが自分のルールを緩められる穴**
3. **プロジェクトから切った作業ツリーの控えが作られない。** `selfguard.py:452` が
   `tree.worktrees(root)` を `projects_dir` 無しで呼ぶため空を返す
4. **`--lint` の早期 return。** `lint.py:484-486` が、`projects/` があってもリポジトリが
   無ければ返るので、`.gitignore` の確認が行われない。clone する前が一番確かめたい時点
5. **孤児の作業ツリー。** 切り元のプロジェクトを消すと、相互参照が切れて列挙から静かに
   外れ、その中のパスがワークスペースルートとして判定される（`tree.py:133-159`）。
   ワークスペース向けの `allow` が孤児の中で効く。判定は変えず、`--lint` と `--explain` が
   名指しする方針で決まっている
6. **`message` の `{root}`。** `--lint` が「`message` に `{root}` の無い
   `.claude/scripts/` の綴りがある」を warn で言うようにする

### 伝えること: `config-union` に残る見込みの穴

上の 2 は `config-union` の範囲と重なるが、**あちらの計画には入っていない。**
置き場を `projects/<名前>/.ccnavi/config/` に移しても、名前の区画の正規表現は同じなので
穴がそのまま移植される。再現は次のとおり。

```
projects/has space/ を作り、config/rules.yml（移行後は .ccnavi/config/rules.yml）への
Write が deny にならないことを見る
```

### ccnavi 自身の設計の穴（2026-09-12 の作業で踏んだもの）

どれも回避して進めたが、次に同じことをする人も同じ場所で止まる。

1. **保護済みファイルを直すチケットが行き止まりに入る。** 直す対象（`.claude/scripts/`、
   `.claude/hooks/`、`rules.yml`）は `deny` なのでエージェントは書けない。完成品を
   `wip/design/scripts/` に置いて人が写す形にしたが、`implement` の種類の `scope` に
   `wip/design/*` が無く、承認が拒まれる。親の `allow` は改版で変えられない
   （変えられるのは `plan` と `feedback` だけ）。しかも着手後は親の提案が `doing/` に
   あり、`builtin-ticket-state` が編集を止めるので `plan` の改版もできない。
   今回は `phases.yml` に `staging`（写す版の作成、`scope: [wip/design/*, tests/*]`）を
   足して回避した。**`phases.yml` は人が持つ設定なので、エージェントは足せない。**
   同じ形の作業が来たら、この種類を使うこと
2. **`.claude/` の中で、git が運ぶものと運ばないものが混ざっている。** `scripts/` と
   `hooks/` は追跡されるので作業ツリーに写しがある。`ccnavi/tickets/`（承認済みチケット）と
   `ccnavi/state/` は `.gitignore` に入るので運ばれない。この非対称のせいで、
   「道具のある場所」を印にして根を決めると作業ツリーが自分を根と見なし、チケットが
   見つからなくなる。sh 側は `.claude/worktrees/` を候補から外して解いた
3. **シェルでフィクスチャを組み立てると `builtin-guard-setting-files` が反応する。**
   コマンドの文字列に `.claude/scripts` が含まれるだけで当たるので、一時ディレクトリに
   検証用のワークスペースを作る `cp` も止まる。受入テストは Python の中で写すので
   通るが、手で確かめるときに踏む
### 未了: `ccnavi-review.sh` の usage が実際の挙動と違う（人が直す）

敵対的レビューで見つかった 3 件のうち、資格情報の漏れ 2 件（`fail` が生の URL を出す、
伏せ字が最初の `@` までしか消さない）は `sh-ws-root` で直した。残る 1 件。

usage の `check` の説明が「依頼より後の未解決スレッドが無ければ」のままで、いまの挙動
（時刻で絞らず未解決の全部を数える。ADR-0031）と違う。冒頭の一覧にも `handoff` `ready`
`wrapup` `origin` が無い。文面だけの修正だが、`.claude/scripts/` は `deny` なので
人が直すか、`staging` 種別のフェーズを持つチケットで写す版を作る。

**複数のリポジトリ（REQ-MLT、設計 §11）で残っているもの。**

- プロジェクトの数に対する `ms`。プロジェクト 5 本で期限の半分を超えるなら、ルールの読み込みに
  mtime の控えを足す
- **Windows の `gitdir:` の綴りは実測済み**（2026-09-12、git 2.39.2、Git Bash と PowerShell）。
  絶対パス、区切りは `/` のみ、ドライブレターは大文字、`gitdir:` の後ろは半角空白 1 個。
  呼び出し側のシェルや引数の区切りに依存しない。`ccnavi_project`（sh）と `tree.py` が
  この綴りを前提にしている
- `projects/` をワークスペースの `.gitignore` に入れたとき、Claude Code がプロジェクトの中の
  CLAUDE.md を読むか。読まれるならワークスペースの CLAUDE.md と矛盾しないように書く
- `cwd` がプロジェクトの中にあるとき、hook の `${CLAUDE_PROJECT_DIR}` がワークスペースルートのままか

**`.claude/ccnavi/rules.yml` に、もう当たらないルールが 1 件残っている（人が直す）。** `ask` の
`current-ticket` が `*/.current-ticket.md` に当てているが、提案の置き場は `wip/tickets/<状態>/` に
変わっていて（ADR-0023）、この綴りのファイルはもう作られない。文面も「承認台帳の側」という
廃止した言い方をしている。消すか、`*/wip/tickets/*` に当てて文面を承認済みチケットの話に直す。設定 3 本は
エージェントが触らない決まりなので、`/ccnavi-config` で下書きを作って渡す。

**コードのコメントに残る旧設計書の節番号（約 30 か所）。** ADR-0037 の対応表で引けるが、旧 §9 と
新 §9、旧 §11 と新 §11、旧 §4 と新 §3 は同じ綴りで別の中身を指すので、読み手が体系を当てることに
なる。とくに `vscode-extension/ccnavi-board/src/core/projects.ts` は、他プロジェクトの `.gitignore` に
「（設計 §25.2）」と**書き込む**ので、次に clone した人の手元に古い参照が入る。`ccnavi/modes.py` の
`resolve_mode` の docstring も旧モード名（`warn` と `block`）のまま。まとめて 1 本のチケットで直す。

**VS Code 拡張のプロジェクト管理画面（0.3.0）・リスク管理画面（0.4.0）・フェーズ管理画面（0.5.0）は、
まだ拡張開発ホストで通していない。** 拡張の README の手動確認の表 25〜44 を 1 度踏む。

**フェーズ管理画面の既知の限界（直していない）。** `--lint` は設定をまとめて見るので、`rules.yml` が
壊れている間は配点も種類も保存できない（先に rules を直す）。YAML の構文が壊れたファイルは画面から
直せない（エディタで直す）。保存はアトミックではなく、競合の検出は更新時刻だけ。CRLF と BOM は保存で
落ちる。リスク管理画面も同じ。

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

- `ticket start` / `done` は承認済みチケットの有無を見ない。未承認のまま `doing/` `done/` まで進める。止めるか、
  せめて「未承認」を stderr に出すかは決めていない（`--lint` は言う）
- `--approve` は `done/` にある未承認の提案も束に入れる。承認した承認済みチケットは次の hook で即座に閉じる。
  `cancelled/` と同じく `done/` も除くほうが自然に見える
- 人が子を再開しても、そのフェーズの `reviewed` は残る。再び `done` にしてもゲートは閉じず、
  告知も出ない。再開の手順に「印も消す」を入れるか、承認済みチケットを戻したときに機構が消すかは決めていない

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

次のセッションで同じところを踏まないように。Claude Code の振る舞いについて測った前提は
設計書の付録 C が一覧で持っている。ここはそれに、踏んだときに何が起きたかと逃げ方を足したもの。
測り直したときは両方を直す。

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
- **Windows は実行中の実行ファイルを上書きできない。** 名前の変更はできるので、
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
| URL にトークンを埋めた origin（`http://oauth2:<token>@localhost:8929/...`）で host にトークンが混ざり、`origin` の出力にそのまま出た | sh は利用者の情報を落とし、出力で伏せる。実行ファイルの `remote_kind` も読み飛ばす。`tests/test_review_origin.py` |
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
