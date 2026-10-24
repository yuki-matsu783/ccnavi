---
type: design
title: 11.14 単体 clone で不都合な処理の一覧
description: ワークスペース内 clone と単体 clone のどちらでも動かす観点で洗い出した、不都合な処理の一覧。場所・振る舞い・重さ・確認の仕方と、未決の設計判断
tags: [design-doc, projects]
keywords: [単体 clone, ワークスペース内 clone, 不都合, 洗い出し, 根, ルート, 統合先, ミラー, 工程スキル, 目録, 縮退]
---

[設計書の入口](../../design.md) > [11. 複数のリポジトリ](../multi-repo.md)

### 11.14 単体 clone で不都合な処理の一覧

[ワークスペース内 clone と単体 clone の両方で動かす](../../adr/0145-work-in-workspace-and-standalone-clone.md)考え方に沿って、ワークスペースにあるものに頼る処理を洗い出した記録。
この文書は未解決の項目だけを残した現状の記述で、直し方は決めていない。「直し方の案」は選択肢で、採用するかどうかは機能ごとに決める。「未決」の節に、設計判断が要る点の選択肢を並べた。

## 調査の範囲

- 行番号は調査の時点のもの
- 用語: ワークスペース内 clone は `<WS>/projects/<名前>/` に clone したプロジェクト。単体 clone はそのリポジトリだけを clone して、そこで作業する形（11.0）
- 実験環境: Linux。ワークスペース、プロジェクト、単体 clone を一時ディレクトリに組み、sh は実物を、ccnavi の実行ファイルはソースを呼ぶ shim で代用して回した
- 確認の仕方の凡例: 実行で確認は、実験環境で再現した。読みのみは、コードを読んだだけで、再現していない
- 未実施: Windows、macOS、WSL の実機。実行ファイルのバイナリ固有の挙動（shim で代用した）。GitHub や GitLab が要るコマンド（`ccnavi-review.sh`、`ccnavi-user-decide.sh` など）。子チケットの作成から finish、review、ready、integrate までの一巡

重さは、単体 clone とワークスペース内 clone の利用者が受ける影響の大きさで付けた。大きい順に blocker、major、中、minor、nit、info。info は意図した設計か、注意書きで足りるもの。

## 一覧

| ID | 処理 | 重さ | 確認 |
|---|---|---|---|
| A1 | プロジェクトで起動したときの、hook と sh の根の食い違い | 中 | 実行（sh 側）、読み（hook 側） |
| A2 | `CLAUDE_PROJECT_DIR` が無い端末の直打ちで、Python が根を取り違える | major | 実行 |
| A3 | `.claude/` を持つ祖先の下に置いた単体 clone で、祖先が根になる | minor | 実行 |
| A4 | sh の `*/projects` 判定が大文字小文字を区別する | minor | 読み（Linux では再現できない） |
| C1 | 単体 clone の直後は `.ccnavi/bin/` が無く、案内が合わない | 初回は blocker（単体 clone 固有ではない） | 実行 |
| C2 | プロジェクトのスキルの目録が、単体 clone で出ない | major | 実行 |
| C3 | setup が `/.claude/worktrees/` を `.gitignore` に足さない | minor から中 | 実行 |
| C4 | `.git` がファイルの clone で、ワークツリーが認識されない | 中 | 実行 |
| D1 | ワークスペースの発言のときの同期が、プロジェクトのミラーと写しを上書きする | minor から中（設計上の緊張） | 実行 |
| D2 | ワークスペースの SessionStart が、プロジェクトの `.gitignore` に追記する | minor | 実行 |
| E1 | ルールの `{root}` が、プロジェクトのレイヤーでもワークスペースルートを指す | info | 読み |
| E2 | `--lint` のレビュー経路の検査が、ワークスペースの origin しか見ない | minor | 読み |
| E3 | 拡張のプロジェクト管理画面が、`projects/` の無い単体 clone に帯を出す | nit | 読み |
| E4 | 単体 clone が自分の `projects/` を追跡していると、`--lint` が警告する | minor | 実行 |
| E5 | issue 起点の識別子が、ワークスペース内と単体で変わる | info（意図した設計） | 読み |
| E6 | 案内文が一方の形を前提にしている | nit | 実行と読み |
| E7 | 同じリポジトリを 2 通りに clone すると、記録が割れる | info（設計上受け入れ済み） | 読み |
| N1 | ワークツリーで起動すると、ログと state がワークツリーの中にできる | 中 | 実行 |
| N2 | 分岐名の接頭辞の設定が、ワークスペース全体で 1 つ | minor | 読み |
| N3 | `ccnavi-user-push-approved.sh` の `given_in` が `projects/*` の下を除く | minor | 読み |

## A. 根の決め方がずれる

#### A1 プロジェクトで起動したときの、hook と sh の根の食い違い（中）

- 処理と場所: hook の根は `src/ccnavi/entry/cli.py:1300-1315` の `default_root` で、`CLAUDE_PROJECT_DIR` があればそのまま返す。sh の根は `.ccnavi/scripts/ccnavi-common.sh:150-185` の `ccnavi_workspace` で、`CLAUDE_PROJECT_DIR` を読まず、cwd から上へたどって `*/projects` の親に ccnavi があればそこを根にする
- ワークスペース内 clone: ワークスペース内のプロジェクト p で Claude Code を起動する（`CLAUDE_PROJECT_DIR=<ws>/projects/p`）と、hook は根を p にして `p/logs/` に書く。sh は根をワークスペースにして `ws/logs/` に書き、ワークツリーも `ws/.claude/worktrees/` に切る
- 単体 clone: 根は p だけで、食い違わない
- 起きること: hook の拒否文は `sh p/.ccnavi/scripts/ccnavi-git.sh worktree add .claude/worktrees/<名前> ...` と案内する。打つと sh が「ワークスペースの外に出ます」と断る。この拒否文は自己修正の案内を含む。ログと state の置き場が割れる
- 前提: 文書上の前提は、ワークスペースから起動して `cd` で入る形（`to-measure.md`、手引き）。一方で、プロジェクトで起動するとそのプロジェクトの hook が使われると書いてあり（[複数のリポジトリ：道具はワークスペース、設定はプロジェクト](../../adr/0033-projects.md)の追記）、この形は否定されていない
- 直し方の案: sh も `CLAUDE_PROJECT_DIR` を第一候補にする案は採れない。理由: Bash ツールの環境に `CLAUDE_PROJECT_DIR` は来ない（`docs/design/appendix.md:102`）。コマンド行に付けて渡すことも `wrapguard.py:67` が止める。別の案は未決（「未決」の根の決め方）

#### A2 `CLAUDE_PROJECT_DIR` が無い端末の直打ちで、Python が根を取り違える（major）

- 処理と場所: `src/ccnavi/entry/cli.py:1326-1343` の `_find_project_root` は、最も近い `.claude/` を持つ祖先を根にする。プロジェクトが `.claude/` を持つのは通常の形なので、プロジェクト自身が根になる
- ワークスペース内 clone: `ws/projects/proj1` に入り、`CLAUDE_PROJECT_DIR` 無しで `ccnavi ticket status` や `--agree --preview` を打つと、根がプロジェクトになる。提案とワークツリーは `<WS>/.claude/worktrees/` と `<WS>/wip/` にあるので、「チケットはありません」「承認待ちはありません」と出る。`sh ../../.ccnavi/scripts/ccnavi-ticket.sh status` は正しく出る（sh は `projects/` の親を根にする）。壊れるのは、`ccnavi user handoff` や `ccnavi user agree` の直打ち。`.ccnavi/CCNAVI.md` はこの直打ちを案内している
- 単体 clone: プロジェクトが根で、期待どおり動く
- 起きること: `--root` を渡さない限り、そのプロジェクトが根と見なされる。`--root` を付けるか、sh のラッパーを通すと避けられる
- 確認: 実行で確認。`.claude/` を持つ `projects/B` で `env -u CLAUDE_PROJECT_DIR python -m ccnavi --lint` を `--root` 無しで回すと、出力 JSON の `root` が B、`projects` が `[]` になる
- 直し方の案: 祖先をたどるとき、`<親>/projects/<名前>` の形で、親が ccnavi の根（`.ccnavi/scripts/ccnavi-common.sh` を持つ）なら親を根にして sh と揃える。採るかどうかは未決

#### A3 `.claude/` を持つ祖先の下に置いた単体 clone で、祖先が根になる（minor）

- 処理と場所: A2 と同じ `_find_project_root`
- ワークスペース内 clone: 関係しない
- 単体 clone: 単体 clone が `.claude/` を持たず、`.claude/` のある祖先（`$HOME` など）の下にあると、その祖先を根に取り、祖先の `.ccnavi/config/common/rules.yml` を読む。`~/.claude` は利用者の `$HOME` にあるので、`.claude` を持たないリポジトリは `$HOME` を根に取る
- 起きること: 端末から直接打ったときだけ。hook には常に `CLAUDE_PROJECT_DIR` が来る
- 直し方の案: 目印を `.ccnavi/scripts/ccnavi-common.sh` の存在にする。未決

#### A4 sh の `*/projects` 判定が大文字小文字を区別する（minor）

- 処理と場所: `ccnavi-common.sh:166` 付近の `ccnavi_workspace` と、`:283` 付近の `ccnavi_project`。`*/projects` を大文字小文字を区別して比べる。CLAUDE.md の決まり（パスは区別せずに比べる）に反する。Python の `_is_worktree_dir` は `pathcase.fold` を通している
- ワークスペース内 clone: macOS や Windows では `ws/Projects/p2` と `ws/projects/p2` は同じディレクトリなのに、sh が別物として扱い、根が p2 になる
- 単体 clone: 関係しない
- 起きること: Linux では `Projects` と `projects` が別ディレクトリなので、実害を再現できない。macOS と Windows は未確認
- 直し方の案: `pathcase` 相当の小文字化比較にする

## C. 単体 clone で動かない・縮退する

#### C1 単体 clone の直後は `.ccnavi/bin/` が無く、案内が合わない（初回は blocker。単体 clone 固有ではない）

- 処理と場所: `ccnavi-common.sh:195-205` の `ccnavi_bin`、`:214-235` の `ccnavi_compat_skew`、`ccnavi-launcher.sh:50-65`
- ワークスペース内 clone・単体 clone: `.ccnavi/bin/` は setup が `.gitignore` に入れるので、どの fresh clone にも無い。単体 clone に限らない
- 起きること: 全 hook が exit 127 で終わり、ガードは働かない（`docs/design/startup-config.md` の「見つからなければ終了コード 127」）。`CCNAVI_ENTRY_PATH` が設定されているとランチャーの sh はあるので `ccnavi_bin` は成功し、`ccnavi-ticket.sh status` は「実行ファイル <ランチャー> は互換 不明、sh は互換 N で食い違っています。build.py を実行し `ccnavi-setup.sh <このワークスペース> --force` で配り直してください」と出す。原因は「この機械向けの実行ファイルが無い」ことで、案内が違う。`ccnavi-worktree.sh` は別の文面（id-unchecked）、`ccnavi-fetch.sh` は無言で終了し、`ccnavi-git.sh worktree add` は通る
- `bin/` に無いときは PATH の `ccnavi` を起動する。`bin/` もソースも無い配布先の `ccnavi_bin` も PATH の `ccnavi` を返す。PATH にも無ければ、以上のとおり
- 確認: 実行で確認
- 直し方の案: 「ランチャーが指す `bin/<os>-<arch>/` が空」を専用の文面で案内し、単体 clone の導入手順（プロジェクトに setup を実行する）を入れる。未決（「未決」の単体の初回導入）

#### C2 プロジェクトのスキルの目録が、単体 clone で出ない（major）

- 処理と場所: `src/ccnavi/hook/projskills.py:101-108` の `project_of` は、MAIN のツリーでは `t.project` が空なので `""` を返す。呼び出しは `hook/events.py:624`、`hook/subagent.py:59`、`hook/judge.py:709`。目録を出す hook はワークスペースの `.claude` にしか無い
- ワークスペース内 clone: ワークスペースから `cwd=projects/proj1` の SessionStart を呼ぶと、目録が入る
- 単体 clone: `skills/<名前>/SKILL.md` は git に入るが、SessionStart、SubagentStart、最初の PreToolUse のどこでも目録は渡らない。`skillsync` が写した工程スキルの入口（`skills/<工程>/SKILL.md`）も同じ。reference は `ccnavi --docs` で引ける（`--root B --docs --type skill-reference` で再現した）。単体 clone で Claude Code が自動で読むのは、`skills/` ではなく `.claude/skills/` のほうである
- 起きること: `project_of` は単体 clone を検出せず、黙って空を返す。[ワークスペース内 clone と単体 clone の両方で動かす](../../adr/0145-work-in-workspace-and-standalone-clone.md)の「縮退して動く」の定義（頼るものが無いことを検出し、決めた小さい振る舞いに落とす）には当たらない。工程スキルの写しを決めたときの採らなかった案に「単体 clone で目録に載らない」があるが、実装はこれを満たしていない
- 確認: 実行で確認
- 直し方の案: MAIN のツリー（`project == ""`）でも `root/skills` があれば目録を出す。工程スキルがワークスペース内 clone で目録と `--docs` に二重に出る例外（`--docs` は 2 件に当たる）との整理も要る。未決

#### C3 setup が `/.claude/worktrees/` を `.gitignore` に足さない（minor から中）

- 処理と場所: `scripts/ccnavi-setup.sh:960-1060,1627-1652` が `.gitignore` に足すのは `.ccnavi/bin/<os-arch>/`、`**/ccnavi-index.jsonl`、`/scratchpad`、`/logs/`。`/.claude/worktrees/` は足さない。ccnavi 自身のリポジトリは `.gitignore` に `/.claude/worktrees/` と `/projects/` を手で書いている
- ワークスペース内 clone: ワークスペース側にも `/.claude/worktrees/` を足さない。ワークスペースの `.gitignore` に `/projects/` も足されず、警告だけが出る（setup の 1274 行以降）。重さは minor
- 単体 clone: ワークツリーがプロジェクト自身の `.claude/worktrees/` に切られ、`git status` に `?? .claude/worktrees/` が出る。`ccnavi-git.sh add .` が `adding embedded git repository` の警告を出し、gitlink を staged する。重さは中
- 確認: 実行で確認。単体 clone 固有ではない
- 直し方の案: setup が `/.claude/worktrees/` を足す（ワークスペース側には `/projects/` も）。未決

#### C4 `.git` がファイルの clone で、ワークツリーが認識されない（中）

- 処理と場所: `src/ccnavi/infra/tree.py:222-250` の `is_worktree_of` は `<root>/.git/worktrees/` の下しか受け付けない。`src/ccnavi/tickets/worktrees.py:437-460` の `_owner` は、common dir の名前が `.git` であることを要求する。`tree.projects()` は、サブモジュールとして置かれた形も許すとコメントしている。sh の `ccnavi_project` も `*/.git/worktrees/*` だけを見る
- ワークスペース内 clone・単体 clone: リンクされたワークツリー、`--separate-git-dir`、サブモジュールの形は、どちらでも同じ経路で落ちる（後ろの 2 つは読みのみ）
- 起きること: リンク済みのワークツリー `linked-main` の中で `.claude/worktrees/feature-9-x` を作ると、`--explain` の trees は MAIN のみ、`--lint` は「元のプロジェクトがありません」、`ccnavi worktree remove` も断られる。ccnavi のワークツリーが 1 本も認識されない
- 確認: リンク済みのワークツリーは実行で確認
- 直し方の案: `git rev-parse --git-common-dir` 相当にする。または `.git` ファイルの gitdir を 1 段読んで比べる。未決

## D. 別リポジトリへの書き込み・上書き

#### D1 ワークスペースの発言のときの同期が、プロジェクトのミラーと写しを上書きする（minor から中。設計上の緊張）

- 処理と場所: `src/ccnavi/tickets/promptsync.py` の `sync`、および `configsync.py`、`skillsync.py`。プロジェクトの `.ccnavi/config/common/` と `skills/<工程>/` を、共通レイヤーと工程スキルの写しで上書きする
- ワークスペース内 clone: プロジェクトの `config/common/rules.yml` に 1 行足してコミットしておくと、「共通レイヤーの中身に揃えた: rules.yml（上書き）」と書き換える。書き換えるのは親のワークツリーで、差分はマージリクエストに出る。静かにデータが消えるわけではない
- 単体 clone: `found.project` が空で写しは動かず、このミラーが共通レイヤーそのものになる。`user handoff` の取り込み先（`handoff.py` の `KINDS`）に `.ccnavi/config/common/…` が入っているので、単体の利用者は編集できる。ミラーは誰も編集しないという前提が単体 clone では成り立たず、更新の手段もワークスペースの発言のときの同期か setup だけ
- 確認: 実行で確認
- 直し方の案: 単体用とワークスペース用の写しを印（指紋）で見分ける。または、ミラーがワークスペースの共通と違うときは上書きせず、差を warn して止める。未決（「未決」のミラーの指紋）

#### D2 ワークスペースの SessionStart が、プロジェクトの `.gitignore` に追記する（minor）

- 処理と場所: `src/ccnavi/hook/docsearch_index.py:31,702-735` の `_add_ignore`。プロジェクトの追跡ファイル `.gitignore` に `**/ccnavi-index.jsonl` を足す
- ワークスペース内 clone: `M .gitignore` が残り、未コミットがあると断るプロジェクトの integrate が止まる。プロジェクトが自前で setup を済ませていれば起きない
- 単体 clone: 関係しない
- 確認: 実行で確認
- 直し方の案: 他リポジトリの `.gitignore` は書かず、案内だけにする

## E. ルール・検査・表示・案内

#### E1 ルールの `{root}` が、プロジェクトのレイヤーでもワークスペースルートを指す（info）

- 処理と場所: `src/ccnavi/policy/rules.py:61-65,330-340` の `fill_root`、`.ccnavi/docs/projects.md:74-78`
- ワークスペース内 clone・単体 clone: 同じ `^{root}/src/` がモードによって効いたり効かなかったりする。プロジェクトのレイヤーのルールは、`*/schema/*` のようにルートに依存しない書き方をするのが想定（`rules.md`）
- 確認: 読みのみ
- 直し方の案: 注意書きを 1 行足す。ツリー相対の合言葉（`{home}` など）を足す案もある。未決

#### E2 `--lint` のレビュー経路の検査が、ワークスペースの origin しか見ない（minor）

- 処理と場所: `src/ccnavi/entry/lint_ticket.py:517-533` の `_review_token(root)`、呼び出しは `:224-225`
- ワークスペース内 clone: ワークスペースに origin が無くプロジェクトにあれば誤って警告し、逆なら検出を漏らす
- 単体 clone: そのプロジェクトの origin を見るので合う
- 確認: 読みのみ
- 直し方の案: 承認済みの親の `project` ごとに、そのリポジトリの origin を検査する

#### E3 拡張のプロジェクト管理画面が、`projects/` の無い単体 clone に帯を出す（nit）

- 処理と場所: `extensions/vscode/ccnavi-board/src/webview/projects/App.tsx:195-203`、`src/projects-panel.ts:169,463-474` の `fixIgnore`
- 単体 clone: `.gitignore` に `/projects/` が無いという帯を出す。ボタンを押すのは利用者で、押すと追跡ファイルの `.gitignore` に `/projects/` が入る。Python の lint（`lint_places._projects`）はディレクトリが無ければ何も言わない
- 確認: 読みのみ
- 直し方の案: 帯の条件に「置き場のディレクトリがある」を足す

#### E4 単体 clone が自分の `projects/` を追跡していると、`--lint` が警告する（minor）

- 処理と場所: `src/ccnavi/entry/lint_places.py:25-80,140-240`、`src/ccnavi/infra/settings.py:158` の `DEFAULT_PROJECTS`。`scripts/ccnavi-setup.sh:1274-1380` の `projects_notice_of` も同様
- 単体 clone: `projects/` を実ディレクトリとして追跡していると、「`projects/` はワークスペースの git が追跡している… `git mv projects apps`」と出す。追跡を外しても「無視されていない」と warn する
- ワークスペース内 clone: 関係しない
- 確認: 実行で確認
- 直し方の案: `.git` を持つ子が無ければ黙る。または単体向けに文面を出し分ける

#### E5 issue 起点の識別子が、ワークスペース内と単体で変わる（info。意図した設計）

- 処理と場所: `ccnavi-start.sh:295-320`、`src/ccnavi/tickets/ticket_ids.py:181-205,409-429`
- 振る舞い: ワークスペース内では `feature-12-p-<slug>`、単体では `feature-12-<slug>` になる。同じ remote に別名のブランチが並ぶ。`issue_identifier` の docstring が、番号はリポジトリごとなので `<プロジェクト名>-` を付けるという意図を書いている
- 確認: 読みのみ

#### E6 案内文が一方の形を前提にしている（nit）

- 場所と内容: `src/ccnavi/entry/lint.py:374-386` の `compat_fix` が「ccnavi-setup.sh <このワークスペース> --force」と案内する。単体 clone の利用者には当たらない
- 設計済みで、不都合に数えないもの: cwd が `projects/p` のとき `worktree add` が一度断られることは、`.ccnavi/docs/worktree.md:68` が設計済みで、拒否文は正しいパスを教える。`_misplaced_worktrees` は単体の `.claude/worktrees/` では 0 件で、`projects/` の下の各プロジェクトにだけ出る
- 確認: 実行と読み

#### E7 同じリポジトリを 2 通りに clone すると、記録が割れる（info。設計上受け入れ済み）

- 処理と場所: `logs/archive/<リポジトリ>/`（`archive.py:14-18`）、`logs/state/retire/<親>.json`（`retire.py:95-107`）、閉じた子の連番の「使い済み」記録（`approval_ops.py:174`）
- 振る舞い: 同じリポジトリを同じ PC でワークスペースの下と単体の 2 通りに clone すると、これらが割れる。ワークスペースでは識別子が全リポジトリで一意でなければならない（`lint_ticket.py:440-445`、`next_id.py:22-30`）が、単体では強制されない。退避の置き場は手元にしか無く、別の機械では使えない。`archive.py` にそう明記してあり、ユーザが受け入れている
- 確認: 読みのみ

## N. 洗い出しのあとに見つかったもの

#### N1 ワークツリーで起動すると、ログと state がワークツリーの中にできる（中）

- 処理と場所: A1 と同じ原因。`src/ccnavi/entry/cli.py:1309-1311` の `default_root` が `CLAUDE_PROJECT_DIR` をそのまま返す
- ワークスペース内 clone・単体 clone: Claude Code をワークツリー（`.claude/worktrees/<id>`）の中で起動すると、`CLAUDE_PROJECT_DIR` がそのワークツリーになり、ログと state がワークツリーの中にできる
- 起きること: [複数のリポジトリ：道具はワークスペース、設定はプロジェクト](../../adr/0033-projects.md)の追記に書いた起動形で、A1 より起きやすい
- 確認: 実行で確認
- 直し方の案: 未決（根の決め方）

#### N2 分岐名の接頭辞の設定が、ワークスペース全体で 1 つ（minor）

- 処理と場所: `CCNAVI_BRANCH_PREFIXES`。ワークスペース全体で 1 つの値を使う
- 確認: 読みのみ

#### N3 `ccnavi-user-push-approved.sh` の `given_in` が `projects/*` の下を除く（minor）

- 処理と場所: `ccnavi-user-push-approved.sh:206` の `given_in` が、ルートのツリーで `projects/*` の下を無言で除く。`ccnavi-fetch.sh:270,283`、`ccnavi-git.sh:1826` は `.git` の有無を見ているので、実害は `given_in` だけ
- 単体 clone: `projects/` を実ディレクトリとして使っていると、その下の `--path` が飛ばされる
- 確認: 読みのみ

## 未決

次は設計判断が要る点で、選択肢だけを並べる。どれも決定していない。

- 根の決め方（A1、A2、A3、N1）: 次の 3 つの選択肢がある。(a) いまのまま、hook は `CLAUDE_PROJECT_DIR` を採り、Python は `.claude/` を持つ最も近い祖先を採る。(b) 祖先をたどるとき、`<親>/projects/<名前>` の形で親が ccnavi の根なら親を採り、sh と揃える。(c) 根の目印を `.claude/` ではなく `.ccnavi/scripts/ccnavi-common.sh` にする。sh に `CLAUDE_PROJECT_DIR` を読ませる案は、Bash ツールの環境に来ないので選択肢にならない
- 分岐名の接頭辞の設定（N2）: `CCNAVI_BRANCH_PREFIXES` をプロジェクトごとに持つかどうか
- ミラーの指紋（D1）: 単体用とワークスペース用の写しを印で見分けるか、ワークスペースの共通と違うときは上書きせず差を warn して止めるか
- 単体 clone の初回導入（C1）: 実行ファイルが無いときの専用の案内と、単体 clone に setup を実行する手順を、どこに置くか
- 単体 clone での縮退の中身（C2）: 目録を MAIN のツリーでも出すか、別の形に落とすか。工程スキルがワークスペース内 clone で二重に出る例外をどうするか
