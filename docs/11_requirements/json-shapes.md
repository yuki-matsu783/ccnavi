---
type: requirements
title: 拡張との取り決め JSON の形
description: VS Code 拡張・Chrome 拡張・sh が読む JSON の形（候補・試験・lint・ボード・承認・残った指摘・版）。拡張との契約
tags: [design-doc, extension]
keywords: [JSON, 候補, 試験, lint, ボード, 承認, 残った指摘, 版, compat, COMPAT, version, 拡張, 契約]
---

[ccnavi 要件書](../10_requirements.md) に戻る。取り決め本体は [2.13 拡張との取り決め](extension-if.md)。

# 拡張との取り決め JSON の形

拡張と sh が読む JSON の欄の形をまとめる。どの JSON も形の版（`version` か `schema`）を持ち、変えるときは版を上げる。

## 候補の JSON

`--suggest --json` の最上位。読み手は VS Code 拡張のルール管理画面。終了コードは常に 0。

| キー | 何 |
|---|---|
| `version` | 形の版。整数。いま 1 |
| `root` / `rules_path` | ワークスペースルートと、共通レイヤーのルールファイル |
| `logs` / `records` | 読んだ記録の場所と、読めた行の数 |
| `candidates[]` | 候補 1 件ごと。`{kind, section, id, tool, count, summary, layer, rules_path, rule, expectations, yaml}`。`kind` は `rule` か `message`、`section` は `ask` か `deny`、`layer` と `rules_path` は置くレイヤーとそのファイル、`rule` は書いたままの形の 1 件、`expectations` は `{tool, subject, why}` の配列、`yaml` は文字で出すときと同じ下書き |
| `dropped` | 検証を通らずに除いた候補の数 |

## 試験の JSON

```sh
ccnavi --test Bash "cd /repo && git push" --json
ccnavi --test-expectations .ccnavi/config/common/rule-expectations.yml --json
```

`--test` と `--test-expectations` の結果を JSON で出す。読み手は VS Code 拡張のルール管理画面。判定は文字で出すときと同じ関数を通る
（REQ-DIA-03）。`--json` のときは終了コードが常に 0 で、食い違いの数は `mismatches` で読む（`--test` の payload の欄の誤りだけは、JSON を出さずに 1）。

`--test --json` の最上位。

| キー | 何 |
|---|---|
| `version` | 形の版。整数。いま 1。欄を足しただけでは上げない（拡張は知らない欄を読み飛ばす） |
| `root` / `rules_path` | ワークスペースルートと、適用したルールファイル |
| `known` | 判定が対象を取り出せるツールか。偽なら以下は空のまま（ルールを書いてもヒットしない） |
| `tool` / `subject` | 試した入力そのまま |
| `resolved` | パスを行き着く先まで解いた結果。入力と同じなら空 |
| `verdict` / `code` | 判定（`deny` / `ask` / `allow` / `skip`。`--permission-mode` を渡したときは権限モードへ渡した `handover` も）と根拠コード |
| `reason` / `degraded` / `fallback` | `skip` の理由、生の文字列に適用したかどうか、組み込みの既定で判定したかどうか。無ければ空 |
| `unwrapped` | 実行役のコマンドが中で実行するコマンド、または `cd` で移った先から見たパスでルールにヒットしたときの、そのコマンド。複数なら `\x00` でつなぐ。元の形でヒットしたとき・ヒットしなかったときは空 |
| `rules[]` | ヒットしたルール。`{id, source, section, kind, written, pattern}`。`id` は記録の名前（ルールファイル由来は `<相対パス>:<書いた id>`、組み込みは id）。`source` は `file`（ルールファイルから読んだルール）か `outside`（チケットの範囲・組み込みの保護・退避した組み込みの既定のように、ルールファイルの外から来た根拠）。`outside` でもルールそのものがあれば `section` と `kind` を持ち、`written` と `pattern` は頭の 160 字に `…（全 N 字）` を添えた形に切る（組み込みの式は 2 万字を超えるものがある）。ルールの無い根拠（`builtin:ticket-scope` など）は `section` 以下が空。値はこの 2 つだけで、増やすときは `version` を上げる。`kind` は `glob`・`regex`・`when` のどれか、`written` は書いたまま（`when` は組み合わせ条件を 1 行にした形で、`when: ` は付けない）、`pattern` は翻訳後の正規表現（`when` はふつう空）。`when` のルールだけ、書いた組み合わせ条件そのものを `when` に持つ |
| `response` | エージェントに返る文面そのもの。無ければ空 |
| `quoted` | ヒットしたルールのうち、引用の中から切り出したコマンドにだけヒットしたものの id。そういうルールがあるときだけ出る |
| `payload` | 判定に渡した payload の欄 `{cwd, permission_mode, agent_id}`。`--cwd` / `--permission-mode` / `--agent-id` のどれかを渡したときだけ出る（渡さなければ `cwd` は `root`、ほかは空） |

`--test-expectations --json` の最上位。

| キー | 何 |
|---|---|
| `version` / `root` / `rules_path` / `expectations_path` | 上と同じ。加えて期待する判定の場所 |
| `counts` | タイプごとの `{ok, total}` |
| `mismatches` / `skipped` | 食い違いの数と、allow の期待が判定に入らずに通った数 |
| `expectations[]` | 期待 1 件ごと。`{expected, tool, subject, resolved_subject, why, known, verdict, code, reason, rules, ok, skipped}`。`expected` は置いたタイプ、`resolved_subject` は `/repo` を読み替えた後、`ok` は期待どおりか、`skipped` は判定に入らずに通ったか |


## lint の JSON

```sh
ccnavi --lint --json
```

`--lint` と同じ指摘を、同じ深刻度で 1 つの JSON にまとめて出す。終了コードも同じ（error があれば 1）。読み手は VS Code 拡張の
プロジェクト管理画面と、`--flow` で渡したフローへの指摘（`(flow)`）を読むフロー編集画面。この JSON の形は拡張との契約なので、変えるときは版を上げる。

| キー | 何 |
|---|---|
| `version` | 形の版。整数（いま 1）。欄を足すだけなら上げない |
| `root` / `rules` / `mode` / `ticket_control` | 何を見て検証したか。ユーザ向けの文面が先頭に出すものと同じ |
| `projects[]` | 検証の対象になったプロジェクトの名前 |
| `problems[]` | 指摘 1 件ずつの `{severity, where, detail}`。`severity` は `error` / `warn` / `info` |
| `errors` / `warns` / `infos` | 件数 |
| `flow` | `--flow` を渡したときだけ在る。`{path, data, rendered, candidates}` |
| `flow.path` / `flow.data` | 確かめたファイルの絶対パス / 実行ファイルが読んだ中身（下）。`data` は読めなければ `null` |
| `flow.rendered` | `SubagentStart` で担当のサブエージェントに渡る手順の行。読めなければ `null` |
| `flow.candidates` | フローで選べる名前。`{agents: [{name, source}], skills: [{name, source}]}` |

`problems[].where` は、ユーザ向けの文面で `error:` の後ろに出る場所。`(projects/lib) rule-id` や `(self) (phases) design` のような形で、
`--flow` で渡したフローへの指摘なら `(flow)` になる。ファイル全体への指摘なら空。

`flow.rendered` は文字列の配列で、`SubagentStart` で渡す手順の行そのまま。子のパスやロックの案内は入らない。

`flow.candidates` の `source` は `builtin` か `project`。`builtin` は `general-purpose`・`Explore`・`Plan` の 3 つで、`project` は
ワークスペースの `.claude/agents/*.md` と `.claude/skills/*/SKILL.md` から読む。`project` の名前は frontmatter の `name` で、
無ければファイルかディレクトリの名前を使う。frontmatter として読むのは頭の `---` の区間だけで、そこを YAML として読む。
エージェントはふつうのファイルだけを数え、`.md` の大文字小文字は区別しない。`.claude` を含む途中にリンクがあれば読まない。
`flow.candidates` は、フローが読めなくても載る。

### 子のフローを保存せずに確かめる

```sh
ccnavi --lint --json --flow /tmp/flow.yml
```

`--flow <パス>` は、子チケットのフロー 1 本を、`SubagentStart` が読むのと同じ読み手・同じ検査
（大きさ、リンク・ふつうのファイルでない・ハードリンク、UTF-8 として読めない、YAML として読めない、別名、形）で読む。
読めなければ場所 `(flow)` の error として報告し、`detail` は渡したパスで始まる。無いファイルも error。VS Code の拡張の
フロー編集画面が、開くときと保存の前に本文を一時ファイルに書いて渡し、`(flow)` の指摘と `flow.data` を読む
（ほかの設定への指摘ではフローを止めない）。

読めたフローには、手順として怪しいところを `(flow)` の warn で足す。線の `from` / `to` が
無いノードを指す、`start` から届かないノード（`group` は外す）、`start` に入る線、`end` から出る線、分岐・問いの出口に
線が無い、`start` / `end` が無い、`loop` の `condition` が空・`maxIterations` が 1 以上の整数でない・出口（`branches`）が 2 つでない、`loop` の繰り返す側を通らない巡回、`subAgent` の `builtInType` と `skill` の `name` が `flow.candidates` に無い
（大文字小文字だけ違えば正しい表記を添える。空の欄と `:` を含むプラグインのスキルは報告しない）。`loop` の繰り返す側を通る巡回は報告しない。
`detail` は error と同じく渡したパスで始まる。

`flow.data` は読めた中身を JSON にしたもの。YAML 1.1 の読みのままで、`0755` は 493、
`yes` は `true`、`0o17` は文字列になる。JSON にそのまま載らない値は `{"$ccnavi": <種類>, ...}` の形で包む。

| 読めた値 | `data` での形 |
|---|---|
| 文字列・真偽値・null・リスト | そのまま |
| 整数（`±(2**53 - 1)` まで） | 数 |
| それより大きい整数 | `{"$ccnavi": "int", "text": <十進>}` |
| 浮動小数 | `{"$ccnavi": "float", "value": <数>}`。JSON では 1 と 1.0 の区別が消えるので包む。有限でなければ `"text"` に `inf` / `-inf` / `nan` |
| キーが全部文字列のマップ | オブジェクト。キーに `$ccnavi` があれば下の `map` |
| キーが文字列でないマップ | `{"$ccnavi": "map", "items": [[キー, 値], ...]}` |
| 日付・日時・バイト列（`!!binary`）・集合（`!!set`）・組（`!!omap` / `!!pairs`） | `{"$ccnavi": "date" \| "datetime" \| "bytes" \| "set" \| "tuple", "text": <表記>}`。ほかは `"other"` |

フロー編集画面は、開くときに自分の読み手（YAML 1.2）で読んだ中身とこれを見比べ、食い違えば開かない。
保存の前には、書き出す本文をこれに掛けて、画面が書こうとした中身と同じに読まれるときだけ書く。値の意味を
拡張が自分で決めないため。
読むのは `--lint` だけで、診断の外では無視し、`--test` / `--test-expectations` / `--explain` でも「`--lint` でだけ読む」と
出して無視する。フローは判定の材料にならないので、レイヤーの置き場を動かすフラグ（下）とは別に数える。

### 1 つのプロジェクトのルールを保存せずに試す

```sh
ccnavi --test Write projects/lib/src/a.py --json --project-rules-file lib=/tmp/edited.yml
ccnavi --lint --json --project-rules-file lib=/tmp/edited.yml
ccnavi --lint --json --project-phases-file self=/tmp/phases.yml
ccnavi --lint --json --project-phases-file lib=/tmp/phases.yml
ccnavi --lint --json --project-risk-file self=/tmp/risks.yml
ccnavi --lint --json --project-risk-file lib=/tmp/risks.yml
```

- `--project-rules-file <名前>=<パス>` は、その名前のプロジェクトのルールファイルの代わりに `<パス>` を読む
- `--project-phases-file <名前>=<パス>` は、その名前のレイヤー（`self` は自身のレイヤー）のフェーズ定義の代わりに `<パス>` を読み、共通レイヤーの `phases.yml` と足した形で確かめる（同 `id` で中身が違う、`title` の重なり）。配点（risk）のレイヤーには次の `--project-risk-file` がある
- `--project-risk-file <名前>=<パス>` は、その名前のレイヤー（`self` は自身のレイヤー）の配点の代わりに `<パス>` を読む。phases と同じく統合結果なので、共通レイヤーの配点と合わせて確かめる（境目の `levels` が合成後に逆転していないか、同 `id` で中身が違う衝突の warn、`script:` が指す先がそのレイヤーの git プロジェクトルートの `.ccnavi/scripts/` に在るか）。共通レイヤーの配点は `--risk`。ファイルの無いレイヤーに渡しても、そのレイヤーの配点として合成する
- レイヤーの置き場を動かすフラグ 8 本（`--rules` / `--phases` / `--risk` / `--projects` / `--project-home` と上の 3 本）は、`--test` / `--test-expectations` /
  `--lint` / `--explain` でだけ有効になる。診断の外（hook からの判定、`ticket` / `review` のサブコマンド）に渡すと無視し、標準エラーにその旨を出す。
  守る対象（コアファイル）も差し替えを見ない
- `--root` と `--cwd` は 1 度しか渡せない（2 本目が在れば止める）。sh が自分のぶんを先に置くので、後勝ちの上書きを防ぐため

## ボードの JSON

```sh
ccnavi --explain --json
```

`--explain` のうちチケットに関わる部分を JSON で出す。読み手は VS Code の拡張「ccnavi ボード」。拡張はこれを並べるだけで、提案やマーカーを
自分では読まない。`version` が拡張の知っている版（いま 1）と違えば、拡張は読まずに版の違いを伝える。

| キー | 何 |
|---|---|
| `version` | 形の版。整数 |
| `root` / `generated_at` | ワークスペースルートと、出した時刻 |
| `settings` | `mode`（動作モード。`enable` / `dry-run` / `disable`）/ `ticket_control`（チケット制御を使うか）/ `tickets`（提案の置き場、相対）/ `approved`（承認済みチケットの置き場）/ `projects`（プロジェクトの置き場）。`mode` と `ticket_control` は hook がいま受け取るはずの値で、プロセスの環境の上にワークスペースルートの `.claude/settings.json`・`.claude/settings.local.json` の env をこの順で重ね、判定と同じ解決（読めない値は `enable`）を通したもの。ホームの設定は読まない。ボードの中身も同じ `ticket_control` で組む。`mode` を持たない古い実行ファイルの答えでは、拡張はモードの帯を出さない |
| `trees[]` | ワークスペースルート・プロジェクト・ワークツリー。`{name, root, project, kind}`。`kind` は `main` / `project` / `worktree` |
| `layers[]` | レイヤーごとの宣言。順序は 共通レイヤー → 自身のレイヤー → プロジェクト（名前順）。`{name, rules, phases, risk, phases_file}`。`name` は `common` / `self` / プロジェクトの名前。`rules` は `{path, unreadable, deny, ask, allow}` で、各タイプはそのレイヤーから実際に判定へ入ったルール `{id, section, source, match, kind, written, pattern, message}`（`when` のルールだけ、書いた組み合わせ条件を `when` に持つ。重複で破棄したものは入らない）。`phases` はそのレイヤーのファイルに書いてあるフェーズ定義 `{id, source, kind, title, review, scope}`、`risk` は `{path, unreadable, factors}`（`factors[]` は `{id, source, kind, value, points, message}`）、`phases_file` は `{path, unreadable}`。phases と risk は合成前の、そのレイヤーのぶんだけ |
| `sums[]` | ワークスペースとプロジェクトごとの「共通 + 1 レイヤー」の和（判定と同じ合成。拡張は足し直さずこれを見せる）。順序は 自身のレイヤー → プロジェクト（名前順）。`{name, layers, rules, risk, phases}`。`name` は `self` / プロジェクトの名前、`layers` は `["common", <name>]`。`rules` は `{path, unreadable, missing, deny, ask, allow}` で、各タイプは共通レイヤーに足した和（`layers[]` と違い、他のレイヤーの定義で欠けない）。`risk` は `{levels, factors, fallback, problems}`（`levels` は実際に使う境目の点）、`phases` は共通 + そのレイヤーの和 `{path, unreadable, types}`（`path` と `unreadable` はそのレイヤーのファイル、`types` の `source` は定義の出どころのレイヤー。足すときの衝突は `--lint` が言う。定義は順序を持たないので先行関係の欄は無い） |
| `projects[]` | プロジェクトの名前 |
| `problems[]` | 読めなかった提案や承認済みチケットの説明。あっても他は出す |
| `pending_approval[]` | `user agree` で承認の対象に入る識別子（承認済みチケットの無い提案と、親の改版） |
| `tickets[]` | 識別子ごとに 1 件。提案と承認済みチケットのどちらか一方しか無くても出す |
| `parents[]` | 承認済みチケットのある親ごとの局面とフェーズ。承認前の親はフェーズを持たないのでここに無い |
| `turns[]` | いまターンの途中にいるセッション。`{session, since, seen}`（`since` はターンが始まった時刻、`seen` は最後にツールの呼び出しがあった時刻。どちらも UNIX 秒）。ターン中に設定ファイルを直すと、次のツール呼び出しで戻されることがあるので、拡張は保存の前にこれで確かめる。更新が 30 分以上無い印と、読めない印は数えない。チケット制御の有無によらず出す。この欄を追加する前の実行ファイルが答えた場合は、拡張はターン中の確認を出さない |
| `archived[]` | 手元の退避（`logs/archive/`。`ready` が閉じた親子のチケットを移した先）にある閉じたチケット。表示のためだけの任意の欄で、判定（承認待ち・先行・局面）には混ぜない。`tickets[]` に同じ識別子があるものは出さない。`{ticket, parent, phase, title, project, path, started_at, completed_at, merged_sha, cancelled_at, cancel_reason}`。`path` は退避したファイルの絶対パス。この欄を持たない古い実行ファイルの答えも同じ版のまま読める（拡張は無ければ空とする） |

`tickets[]` の 1 件。

| キー | 何 |
|---|---|
| `ticket` / `parent` / `phase` / `title` / `project` / `issue` / `mr` / `predecessors` / `human_review` | 提案（無ければ承認済みチケット）の frontmatter から。`mr` は親を載せるマージリクエストの番号（親チケットの `mr:`）。`human_review` は `{required, reason}` |
| `branch` / `remote` | 親で、親のワークツリーがあるときだけ値が入る。`branch` は親のワークツリーがいま居るブランチ（それ以外は空文字）、`remote` は最後に取ってきた origin との差 `{branch, ahead, behind}`（`ahead` は未送信のコミット数、`behind` は取り込んでいない数。読めなければ `null`。子とワークツリーが無い親は `remote` 全体が `null`） |
| `predecessors_unmet[]` | 満たしていない先行。`{ticket, state, label}`。`state` は `todo` / `doing` / `review`（先行が閉じれば満たす）と `cancelled` / `missing` / `scattered` / `self` / `ancestor` / `cycle`（待っても満たさない）、`label` はユーザ向けの言葉（「作業中（doing/）」など）。空でなければ承認と着手（`start`）が止まる（書き込みと `finish` は止まらない）。先行が無い子・親・閉じたチケットは空。ボードはこれで「先行待ち」のバッジを出し、自分では数えない |
| `proposal` | `{state, tree, tree_root, path, has_plan}`。正とする側のツリー（親のツリー。無ければ元ツリー）の提案の置き場で見つけたもの。`state` は `todo`（承認待ち）/ `review`（レビュー待ち）。`has_plan` は親の提案が全体計画（`plan:`）を持つか（`feedback:` だけの親と子は `false`。計画の順序を直すタブ（`--plan-order`）は `plan:` の無い親を開けないため）。承認前の親は `parents[]` に出ないので、ボードはこれで計画の順序を直すタブの入口を出し分ける。足しただけの欄で、読まない拡張はそのまま動く。`doing/` `done/` に在るときは `null`。承認済みの識別子では、正とする側のツリーを、承認済みチケットがどのツリーにあるかで決める。そのツリーの外に残った古い提案（承認の前に切ったワークツリーの `todo/` など）は出さない（`seen_in` には出る） |
| `template_draft` | 完了した（`done/` の）計画付きの親か（取り消した親・子・計画の無い親は `false`）。ボードはこれで親のカードに『テンプレートの下書きを作る』ボタンを出す（`ccnavi --templates --draft <親>` を呼ぶ）。足しただけの欄で、読まない拡張はそのまま動く。この欄を出さない古い実行ファイルでは `false` と読む |
| `copy` | `{status, approved_at, approved_from, path}`。`status` は `none`（未承認。このときは `{status}` だけ）/ `open`（`doing/`）/ `review`（`wip/proposals/review/`）/ `closed`（`done/`）。`approved_at` は承認の時刻で、承認済みチケットの欄ではなく、`doing/<識別子>.md` を足したコミットから引く。`approved_from` はどこから引いたか（`commit` / `uncommitted`（コミットが無い。手で置いてまだコミットしていない）/ 空（分からない。git を打たない場））。`uncommitted` と空のとき `approved_at` は空。 |
| `blocked` | 空でなければ「読めるが信じられない」理由。判定はこのチケットのワークツリーへの書き込みを `DENY_TICKET_BLOCKED` で全部止める。`status` は `open` のままなので、止まっていることはこの欄でしか分からない |
| `worktree` | `{exists, path, project}`（無いときは `{exists: false, path}`）。`.claude/worktrees/<識別子>` が実際のワークツリーか |
| `started_at` / `completed_at` / `base_sha` / `merged_sha` / `cancelled_at` / `cancel_reason` | スクリプトが書く欄。`merged_sha` は `finish` が親のブランチへ合流した子の先頭（表示だけ。ボードは閉じた子に「合流」を出す） |
| `in_progress` | 作業中か。承認済みチケットが `doing/` にあり、着手していて、終わってもいないし取り消されてもいないとき真。`flow.locked` と同じ読み方。承認しただけで着手していないもの・レビュー待ち・閉じたものは偽。ボードはルール・配点・フェーズ定義の保存を止めるかをこれで決め、`started_at` などから組み直さない。足しただけの欄で、この欄を持たない古い実行ファイルの答えでは、ボードは作業中かを確かめられないとして保存しない |
| `seen_in[]` | 同じ識別子のチケットがある場所の全部。`{tree, state, path}`。子のワークツリーは親のブランチから切るので、子のワークツリーにも親の提案があるのが普通 |
| `scattered[]` | どれが正か決まらない、チケットがある場所の全部。`{tree, state, path}`。決まっていれば空。正とする側のツリー（親のツリー → 元ツリーの順）で絞り込んでも 2 つ以上残り、その残りが 2 つの置き場にまたがるか同じ置き場に重なるときに入る。状態の操作が「複数の場所にある」で止まる条件と、`--lint` が ERROR を出す条件と同じ。`seen_in` の数は食い違いを意味しない |
| `flow` | 子のフロー。親と、フローが無い閉じた子（終わった・取り消した）は `null`（ボードはこのとき「フローを作る」を出さない）。`{path, rel, tree, exists, linked, locked, lock_kind, draft}`。`path` は読む先の絶対パス、`rel` はツリーのルートからの相対、`tree` はそのファイルを持つツリーのルート、`exists` はファイルが在るか、`linked` はファイルかツリーのルートからそこまでの途中がシンボリックリンクか（真なら読まないし書かない）、`locked` は判定がいまその書き込みを `DENY_TICKET_FLOW_LOCKED` で止めているか（着手中・レビュー中）、`lock_kind` はその種類（`doing` は着手中、`review` はレビュー中、止めていなければ null。種類の欄が無い古い実行ファイルでは出ない）、`draft` はエージェントが書く下書き（効力は無い）の `{path, rel, exists, linked}`。`path` が指すのは正とする側のツリー＝承認済みチケットが在るツリーの版だけで、子のワークツリー上のフローは読まない。承認の前は提案が在るツリーで、承認でフローもチケットと一緒に動く。`rel` は承認済みの領域の固定の置き場 `.ccnavi/approved/flows/<子>.yml` で、中身は YAML。`draft` の置き場はフローと同じツリーの `wip/proposals/flows/<子>.yml` で、ボードはパスを組まずにこれを読み、いまのフローと違えば「提案あり」を出す。読むのは承認済みチケット（無ければ提案）の欄。ボードは `locked` をそのままコピーし、自分で組み直さない |
| `risk` / `judge` | 子の記録 `phases/<親>/<子>.risk.json` と `.judge.json` の中身。無ければ `null` |
| `issues[]` | 読み手が気づくべき不備の文。この順に入る。`blocked` が空でなければ `書き込みが止まっています: <blocked>`、提案が無く `copy.status` が `open` でも `closed` でもなければ `提案が見つかりません（承認済みチケットだけがあります）`、子で親の識別子のチケットが `tickets[]` に無ければ `親 <親> が見つかりません`。無ければ空。ボードはこれをそのまま出し、自分では組み直さない |
| `attention` | ユーザが動く必要があるか。真になるのは、`pending_approval` に入っている、子で自分のフェーズが `gate_closed` か `review_waiting`、未着手・作業中（提案が無く `closed` のもの以外）なのにワークツリーが無い、自分の `risk` の水準が HIGH 以上、`scattered[]` が空でない、`issues[]` が空でない、親でフェーズのどれかが `gate_closed` / `review_waiting` / `risk_escalates` のとき。ボードの「ユーザ待ちのみ」はこれで絞るだけ |

`parents[]` の 1 件。

| キー | 何 |
|---|---|
| `ticket` / `closed` / `stage` | 識別子、閉じた承認済みチケットか、いまの局面（レビュー準備中・レビュー待ちなど、ユーザに見せる言葉。閉じた親は空文字） |
| `plan` / `feedback` | 全体計画とフィードバック計画（`null` は未計画） |
| `close_early` / `ready` / `closed_record` | 親のマーカー `close-early.json` / `ready.json` / `closed.json` の中身。無ければ `null`。`closed.json` は親を閉じたときに置かれ、どのフェーズをどこで見たか（`reviews`）を持つ |
| `phases[]` | 番号順。`{number, type, title, label, state, tickets, states, marks, review_required, gate_closed, review_waiting, deferred, review_at, covers, risk, risk_escalates, risk_line, manual}`。`state` は `planned`（子がまだ無い）/ `active` / `ended`。`marks` はマーカーの種類 → 中身。`review_required` はそのフェーズの終わりにユーザのレビューが要るか（真偽値。定義・ステップ・引き受けた延期のどれかが要ると言うか、子の宣言か実績のリスクが要ると言えば真）。`gate_closed` は判定が使うのと同じ値で、レビューが済むまで止めているかを示す（ユーザに見せる名前は「レビュー準備中」「レビュー待ち」）。`review_waiting` は依頼を出したのに止まったまま（ユーザのレビュー待ち）で、ボードはこれをコピーするだけで組み直さない。`manual` は、手で `done/` に動かされた子の確認待ち・移動が未共有など、手で動かした子についての 1 行（無ければ空文字）。VS Code のフェーズ行に出す。 |

VS Code のボードは、`gate_closed`・`review_waiting`・`marks` を次のように言葉に置き換えるだけで、レビューの要否・ゲートの条件・リスクは判定し直さない（フェーズ行の一言は上から最初にヒットしたもの）。`gate_closed` かつ `review_waiting` は「レビュー待ち」、`gate_closed` は「レビュー準備中」、`marks.reviewed` があれば「レビュー済み」、`marks.skipped.deferred_to` があり、その番号のフェーズに `marks.reviewed` があれば「レビュー済み」、`deferred_to` があれば「レビュー予定」、`marks.skipped` があれば「レビュー省略」、ほかは `state` により「終了」「進行中」「これから」。`marks` の中身（`skipped` の `deferred_to`、`requested` の `mr` と `url`）は JSON にそのまま載っているので、ボードは推測で代わりを作らない。

また、ボードの「MR の状況を確認」は、`ccnavi sync retired`（片付けの記録を持つ親の一覧）と `ccnavi-review.sh mr-state <番号>`（マージリクエストの状況。読むだけ）を使う。どちらも形を厳しく検証し、形が想定外のものを「マージ待ち」「マージ済み」「ありません」と読まない。互換の版は上げない（追加だけで、実行ファイルを起こさない）。

## 承認の JSON

```sh
ccnavi --agree --preview --json [<絞り>...]           # 一覧を見る（承認済みチケットは置かない）
ccnavi --agree --preview --verify --json [<絞り>...]  # 承認できる状態かを確かめる（同上）
ccnavi --entry board user agree --yes <識別子,…> --digest <値> --json [<絞り>...]    # 見せた提案（識別子の一覧と各ファイルのバイト列）が今も同じなら承認する
```

VS Code の拡張が、承認をボードのオーバーレイで行うための形。承認の対象を組むのは `user agree` と同じ関数で、`--explain --json` の
`pending_approval` と答えが食い違わない。`version` が拡張の知っている版（いま 1）と違えば、拡張は読まずに版の違いを伝える。

`--preview` の答え。

| キー | 何 |
|---|---|
| `version` | 形の版。整数。`--yes` と同じ番号 |
| `root` / `generated_at` | ワークスペースルートと、出した時刻 |
| `batch[]` | 承認の対象。`{ticket, title, parent, phase, revision, tree, path, overflow}`。`parent` と `phase` は子だけ（親は `null`）。`revision` は親の改版。空なら承認待ちが無い |
| `batch[].overflow[]` | 範囲の超過（親の範囲・フェーズ定義の `scope` を超える項、regex の項、計画のどの定義の `scope` とも重ならない親の `allow` の項）の説明。文字列の配列で、無ければ `[]`。承認は通るが、判定で止まる |
| `text` | 承認画面の本文そのまま。拡張はこれを等幅で並べ、項目には分けない |
| `digest` | 承認の対象の提案の識別子の一覧と、各提案ファイルの元のバイト列全体から作るダイジェスト（SHA-256 の 16 進）。`--yes` の `--digest` にそのまま渡す。承認画面の本文は入らない |
| `rejected[]` | 承認の対象にしない提案。`{ticket, problems[]}`。載るのは形の不正な提案（親が承認されていない、計画に無い番号、順序、先行（`predecessors`）が `done/` に無いか取り消し済みなど）だけで、範囲の超過だけの子は `batch[]` に載る |
| `problems[]` | 読めない提案や承認済みチケットの説明 |
| `plans[]` | 承認の対象に入った計画を持つ親の、計画ごとの図の中身。`ccnavi --plan-order <親> --json` の `plans` と同じ関数で作る（`--order` は渡さないので、承認する提案そのものの順序）。`{ticket, part, source_sha, items[], after, proposed, current, loose, ready, problems}`。`items[]` は `{number, from, type, title, review, deferred, review_at, locked}` |

`--preview` 自身は、対象にしない提案があっても 0 で返る。答えが要るときだけ `--verify` を足す。
`--verify` の答えは `--preview` の答えに `verify` を足したもの。終了コードが答えそのもの（0 = はい、3 = いいえ。1 は使い方か設定の誤り）。

| キー | 何 |
|---|---|
| `verify.ok` | 真偽。`user agree` を打てば、指定したもの（指定が無ければ承認待ち全部）がそのまま承認の対象に入るか |
| `branch_warnings[]` | 新規の親の識別子と同じ名前のブランチが手元か origin に既にあるときの説明（文字列の配列、無ければ `[]`）。承認は止めず、答えも変えない |
| `verify.reason` | 答えの理由の名前。全部入るなら `ok`、入らないなら `refused`（絞りが通らない）/ `nothing-pending`（承認待ちが 1 件も無い）/ `rejected`（承認の対象にしない提案がある）/ `proposal`（送る提案を名指しする problems がある）/ `dirty_parent`（親のワークツリーに置き場の外の未コミットの変更がある）/ `unpushed_code`（親のブランチに未 push のコードがある。後ろの 2 つは `approval-request` が止まる条件と同じ）。読めない提案（`problems[]`）はここに出ない |

`--verify` は、`user agree` が承認を止めない範囲の超過（`batch[].overflow`）では「いいえ」にしない。本文には出す。送る提案を名指しする `problems[]`（警告を含む）は `proposal` で「いいえ」にする（`approval-request` が止まる条件と同じ。他の提案の `problems[]` は見ない）。
承認されないものは `ccnavi --lint` も同じ関数で名指しする。ただし「まだ承認できない」子は `--lint` では warn で、先行が閉じれば通る子もここに入る。取り消し済み・どこにも無い・複数の場所にある・自分自身・自分の親・輪になった先行は error。

`--yes` の答え。値は `--preview` の `batch[].ticket` をカンマで並べたもの。

| キー | 何 |
|---|---|
| `version` | 同上 |
| `approved[]` / `copies[]` | 承認した識別子と、置いた承認済みチケットのパス |
| `lines[]` | ターミナルなら標準出力に出ていた行（マーカーを消したことなど） |
| `written[]` | この承認で書いた・消したパス（ワークスペースルートからの相対）。ボードはこれだけを `ccnavi-user-push-approved.sh --path` でコミット・push させる |
| `worktrees[]` | 承認した子それぞれのワークツリーの計画。`{ticket, parent, path, branch, base, state}`。`state` は `create`（親のワークツリーが在り子のはまだ無い。承認の後に拡張が作る。`base` は親のワークツリーがチェックアウトしているブランチ）/ `exists`（既に在る）/ `no-parent`（親のワークツリーが無く作れない）/ `project`（プロジェクトの子。承認の後には作らない）。親だけの承認では空 |
| `dirty_parent[]` | 承認した親のワークツリーに残る、提案とチケットの置き場以外の未コミットの変更。`{ticket（親の識別子）, paths[]}`。承認は止めない。親のワークツリーが無い・読めないときは載せない。承認の途中で止まった `partial` には載せない。`prompt` には戻し方を添えた `案内:` の行が付く。変更が無いときは空の配列 |
| `prompt` | Claude Code に渡す文。拡張がオーバーレイの 3 ボタン（コピー・新しいセッションで開く・ターミナルで実行）で渡す。hook は承認を伝えない |
| `mismatch` | 見せたあとに提案が更新されたとき（一覧が増減した、提案ファイルのバイト列が変わった）。`{expected[], current[], digest: {expected, current}}`（`digest` は常に付く。一覧が同じでダイジェストだけ違う場合も同じ形）。答えは `{version, mismatch}` だけで、`approved[]` などは付かない。このとき承認済みチケットは置かれず、終了コードは 1。呼び手は「内容が更新されました」と伝えて最新を表示し直し、もう一度押してもらう |
| `partial` | 置いている途中で止まったとき（書けない、など）。`{placed[], paths[], written[], ticket, reason, lines[]}`。`placed[]` はそこまでに承認済みチケットに入ったぶんの識別子（新規は置いた、改版は書き換えた）、`paths[]` はそのうち実際に書けたパス、`written[]` は止まるまでに書いた・消したパス（成功時の `written[]` と同じ。ボードが送るパスはこれ）、`ticket` は止まったところ（書けなかった提案）、`reason` は理由、`lines[]` は止まるまでに出た行（ターミナルなら標準出力に出ていたぶん）。**置いたものは戻さない**ので、どこまで進んだかをそのまま返す。終了コードは 1。ボードは書けた分（`placed[]` が空でないとき）の `written[]` のパスだけを `ccnavi-user-push-approved.sh` で統合ターミナルへ送り、書けなかった `ticket` と未送信かどうかを画面に出す |

- `--yes` はターミナルを求めない代わりに、見せた一覧と今の一覧が同じで、`--digest` の値（大文字小文字は問わない）が承認の直前に読み直した提案のダイジェストと
  合うことを求める。`--digest` が無ければ承認せず、終了コードは 1
- **照合するのは、承認の対象の提案の識別子の一覧と、各提案ファイルの元のバイト列全体（本文・改行・BOM を含む）だけ。** 解析した後の値では比べない。
  判定が読んだ中身（承認済みチケット・マーカー・フェーズ定義・設定）、承認画面の本文、書き出す中身は照合しない。
  ただし承認の可否は、承認の直前に最新の状態から計算し直す（外れた提案があれば承認しない）。計算は、識別子の順に
  「識別子と提案ファイルの SHA-256」を並べ、件数を頭に付けて、その全体の SHA-256 を取る
- 違いが見つかったら、拒否で終わらず「内容が更新されました」と最新の一覧を表示し直し、もう一度押してもらう。何も承認しない。
  ターミナルの `user agree` は y の入力のあとに、ボードは `mismatch` を受けて、Chrome 拡張は親のブランチの先頭が動いたら読み直して、同じ照合をする
- 指していない提案が増えても承認しない。指した識別子が承認待ちに残っていなければ（絞りが通らない場合も含む）、ほかの提案を代わりに承認しない
- エージェントが Bash や PowerShell で `--yes` を打つ形は、組み込みの deny（`builtin:guard-ticket-approval`）が止める。`--preview` は通す
- 後ろの `<絞り>` は `ccnavi user agree <識別子>...` と同じで、承認の対象を狭める。`--yes` の値（ユーザが見た識別子）とは別に渡す。
  比べるのは「その絞りで今できる一覧」と「見た識別子」

## 残った指摘の JSON

ボードの［レビュー完了］が読み書きする形。拡張は sh（`ccnavi-review.sh decide <N> --preview` と `ccnavi-user-decide.sh`）を子プロセスで打ち、sh が取得した JSON を実行ファイルに渡す。
版は `version`（今は 2。ボードは版 1 を断る）で、承認の JSON と別に数える。

`ccnavi-review.sh decide <N> --preview`（実行ファイルは `--reviewed N --accept-unresolved --preview --json`）は何も置かない。

```json
{"version": 2, "parent": "i0050", "phase": 2,
 "mr": {"number": 7, "url": "https://…/merge_requests/7"},
 "can_issue": true,
 "threads": [{"key": "https://…#note_1", "url": "https://…#note_1", "path": "src/a.py", "line": 3, "body": "…",
              "version": "<指摘の版>", "last_comment": {"id": "…", "at": "…", "author": "…"}}],
 "digest": "v2.<不透明な文字列>"}
```

- `key` は選択を指摘に結び付けるキー（URL か、無ければスレッドの id）。`reviewed` のマーカーの `accepted` 欄にもこの値が入る
- `can_issue` は issue に回せるか。常に真（互換のために欄を残す）
- `digest` は見せた指摘のダイジェスト。`v2.` で始まる不透明な文字列で、中身は読まない。親・フェーズ・マージリクエストの番号・`can_issue`・各指摘のキーと場所と本文に加え、指摘ごとの版（`thread_version`）も含む。指摘ごとの版は、見せたあとに指摘が書き換わったかを見分けるために使う

`ccnavi-user-decide.sh <N> --choices <JSON> --digest <ダイジェスト>`（実行ファイルは `user reviewed N --accept-unresolved --yes <JSON> --digest <ダイジェスト> --json`）は
`{"<key>": "keep" | "fix" | "issue", …}` を受け、見せた指摘の全部に 1 つずつ付いていることを求める。

```json
{"version": 2, "ok": true, "parent": "i0050", "phase": 2,
 "reviewed": false, "pending_extra": [], "followup": "i0050-02-02",
 "kept": ["…"], "fix": ["…"], "issue": [],
 "issue_draft": "", "prompt": "[ccnavi] ユーザが…", "op": "<操作ID>",
 "issue_url": "", "warning": ""}
```

- `reviewed` はレビュー済みになったか（直す指摘が無く、追加分も残っていなければ真）、`followup` は起こした続きの子
- `pending_extra` は、見せたあとに増えた指摘（追加分）のキー。止めずに未対応のまま残し、次の `decide` で扱う。追加分が残る間は、直す指摘が無くてもレビュー済みのマーカーを置かない
- `op` はこの判断の操作記録の ID（`--resume` に渡す。操作記録を使わない呼び出しでは空）
- `issue_url` と `warning` は sh が足す。置いたあとの投稿（issue とコメント）で起きたことで、置いたことは戻さない
- 見せた指摘のうち書き換わった・消えたものがあれば（増えただけなら止まらない）、何も置かずに
  `{"version": 2, "ok": false, "mismatch": true, "digest": {"expected", "current"}, "changed": ["<キー>", …]}` を
  返して 1 で終わる。`changed` は食い違った指摘のキー（親・フェーズ・マージリクエスト・`can_issue` が変わったときは `target`）
- 操作の途中で失敗した送信は、`ccnavi-user-decide.sh --resume <操作ID>` で続きを送る（操作記録は `logs/state/ops/`）

## ミラーの JSON

```sh
ccnavi --entry board user mirror [<プロジェクト名>…] --json                                   # 見るだけ
ccnavi --entry board user mirror [<プロジェクト名>…] --yes --expect <値> --via board --json   # 揃える
```

ワークスペースの共通レイヤーと、各プロジェクトのユーザのチェックアウトにあるミラー
（`.ccnavi/config/common/`）と、共通の工程スキルとプロジェクトの `skills/` のずれを調べる（見るだけ）形と、ずれを揃える形。揃える形はワークツリーに書き、
コミットはしない。読み手は VS Code 拡張のプロジェクト管理画面。

```json
{"version": 1, "ok": true, "error": "", "common_empty": false,
 "projects": [
   {"name": "app", "state": "synced", "changes": [], "error": "", "expect": ""},
   {"name": "web", "state": "differs",
    "changes": [{"rel": ".ccnavi/config/common/rules.yml", "action": "update"}],
    "error": "", "expect": "…"},
   {"name": "x", "state": "error", "changes": [], "error": "理由", "expect": ""}
 ],
 "expect": "…"}
```

| キー | 何 |
|---|---|
| `ok` / `error` | 全体の成否と理由。偽のとき（共通レイヤーが読めない、渡した名前がプロジェクトに無い）は `projects` が空 |
| `common_empty` | 共通レイヤーにも工程スキルにもコピーするものが無い。揃えても何も起きず、ミラーは消さない |
| `projects[].state` | `synced`（ずれなし）、`differs`（ずれあり）、`error`（調べられない。理由は `error`） |
| `projects[].changes` | ずれの中身。`rel` はプロジェクトのルートからの相対、`action` は `create`・`update`・`delete` |
| `projects[].expect` | そのプロジェクトだけを揃えるときの `--expect` の値。ずれが無ければ空 |
| `expect` | 差分のある全プロジェクトを揃えるときの `--expect` の値。差分が無ければ空 |

全体の `expect` に入るのは、`differs` のプロジェクトだけ（`error` と `synced` は入らない）。`common_empty` が真のときは、`projects` は全部 `synced` で、`expect` は空。`changes[].rel` は `.ccnavi/config/common/…` か `skills/…`。`changes[].action` は `create`・`update`・`delete` の 3 つだけ。
名前は大文字小文字を区別せずに引き、結果には実際のディレクトリの綴りで返す。
終了コードは、見るだけは 0（`projects` に `error` があっても 0）、全体の誤り（`ok` が偽）は 1。揃える形は、全部書けたとき 0、書けなかったもの・照合の不一致・対象が無いとき 1。どちらも、終了コードより先に標準出力の JSON を読む。

揃える形の結果は `{"version": 1, "ok": true, "error": "", "synced": [{"name": "web", "files": ["…"]}]}`。
`--expect` が今の差分と一致しないときは何も書かず、`ok` を偽にして `error` に「内容が更新されました」で始まる文を返す
（呼ぶ側は画面を読み直して、もう一度押してもらう）。1 つのプロジェクトが書けなくても、ほかのプロジェクトは書き、
書けた分を `synced` に、書けなかった理由を `error` に返す。

## 版の JSON

```sh
ccnavi --version          # 1 行 1 項目（`compat: 19` など。sh が読む）
ccnavi --version --json
```

実行ファイルが自分自身の情報を出す。設定もワークスペースも読まず、常に 0 で終わる。読み手は VS Code 拡張と
`.ccnavi/scripts/` の sh で、どちらも起動のときに互換の版を自分のものと比べる。

```json
{"schema": 1, "version": "0.1.0", "commit": "88182c2…", "built": true, "compat": 19,
 "features": ["user-entry", "handoff", "mirror"],
 "flags": ["--accept-unresolved", "--agree", "…", "--version", "--yes"],
 "formats": {"phases": 1, "risks": 1, "rules": 1, "ticket": 1}}
```

| キー | 何 |
|---|---|
| `schema` | この JSON の形の版（いま 1）。欄を足すだけなら上げない |
| `version` | ccnavi の版 |
| `commit` | 組み立ての元のコミット。組み立てのときに埋め、未コミットの変更があれば `-dirty` が付く。ソースで動かしているときは `unknown` |
| `built` | 組み立てた実行ファイルなら真、ソースで動いていれば偽 |
| `compat` | 互換の版。実行ファイルと、それを呼ぶ側（sh と拡張）の契約の版（下） |
| `flags` | 受け付けるフラグ。引数の定義から引くので、フラグを足せばここにも並ぶ |
| `formats` | 読む書式の版。レイヤーのファイル（`rules.yml` / `phases.yml` / `risks.yml`）とチケットの頭の `version:` と比べるもの |
| `features` | オプションから読めない振る舞いの名前（文字列の配列）。現在の名前は `user-entry`（ユーザ専用の入口 `user <操作>`）、`handoff`（保護対象の取り込み）、`mirror`（共通レイヤーのミラーの同期）。呼ぶ側が振る舞いの有無を見分けるためのもので、追加しても `compat` は上げない |

**互換の版**は 3 か所に同じ値で持つ。実行ファイル（`--version --json` の `compat`）、sh（`ccnavi-common.sh` の
`CCNAVI_COMPAT`）、VS Code 拡張。Chrome 拡張は組み立てのときに実行ファイルの値を埋め込む。
sh や拡張が頼るフラグや出力の形を、呼ぶ側を直さないと動かない形に変えたときに上げる。データの形（承認済みの置き場に置くものの並び、
先行関係の置き場など）が変わるときも上げる。古い実行ファイル（古いコアを積んだ Chrome 拡張を含む）が
新しい形のデータを読み違えるため。フラグや欄を足すだけで、データの形も変わらないなら上げない（拡張は使う前に `flags` を見る）。
レイヤーのファイルは頭の `version:` が書式の版を示し、読めない版は `--lint` が既に error を出すので、レイヤーに別の版は足さない。

食い違ったとき、どこでも直し方を名指しする。止める・通すは実行ファイルと hook が持つ。

| 見つける場所 | 言うこと |
|---|---|
| sh（`ccnavi-ticket.sh` / `ccnavi-user-agree.sh` / `ccnavi-review.sh` など） | 実行ファイルを起動する前に `--version` を読み、食い違えば標準エラーに 1 行出して先へ進む |
| VS Code 拡張 | 起動のときに 1 度問い合わせ、食い違えば通知を出す。`--flow` を使う前には `flags` を見て、無ければフロー編集画面を開かない・保存しない |
| `ccnavi --lint` | sh の `CCNAVI_COMPAT` と比べ、`(version)` の warn を出す |

直し方は、ccnavi のリポジトリで実行ファイルを組み立て直し、`sh scripts/ccnavi-setup.sh <ワークスペース>` で配り直す（実行ファイルは配布元と中身が違えば置き換わる）。sh も配り直すなら `--force` を付ける。
拡張のほうが古ければ拡張を入れ直す。sh は `--version` の答えから互換の版を読めなければ「不明」として食い違いに数える。拡張は版を聞けなかったとき、起動では何も言わず、フラグを使う前に理由を出して先へ進まない。
