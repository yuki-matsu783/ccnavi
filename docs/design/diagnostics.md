---
type: design
title: 10. 診断と機械可読な出力
description: ユーザと VS Code 拡張が使う診断のオプションと機械可読な出力
tags: [design-doc, cli]
keywords: [診断, --explain, --test, --lint, --json, 機械可読]
---

[設計書の入口に戻る](../design.md)

## 10. 診断と機械可読な出力

診断（この章のオプション）だけは hook から呼ばれない。ユーザが端末から打ち、VS Code 拡張が読む。判定は hook と同じ関数を
通る（REQ-DIA-03）。記録は診断の経路では書かない。試験（`--test` / `--test-samples`）は
state も内部で外す。拡張と `tools/check_rules.py` は念のため `--log ""` と `--state ""` も渡す。

| オプション | 出すもの | 終了コード |
|---|---|---|
| `--test <ツール> <対象> [--json]` | 判定・根拠コード・当たったルール（タイプ、書いた式、翻訳後の正規表現、`file` か `outside`）・行き着く先・縮退したか既定を使ったか・当たった中で実行されるコマンド（`unwrapped`、6.3.1）・返る文面。モードは常に `enable`。`--test Stop "(stop)"` はターンの終わりに使われるルール（6.6）を並べ、あれば `allow`、無ければ `skip`。見本の `tool: Stop` も同じ | 常に 0 |
| `--test-samples <見本> [--json]` | 見本をすべて判定に掛け、期待と食い違ったものを名指し | 文字の出力は食い違いがあれば 1、JSON は常に 0、見本が読めなければ 1 |
| `--explain [--json]` | タイプごとのルール、プロジェクトの一覧とルールの可否、権限モードの扱い、チケット制御の値、承認済みチケットの一覧、親の局面、フェーズごとの状態・マーカー・止まっているか・リスク。判定は行わない | 0 |
| `--lint [--json]` | 判定を行わず、防御を無効化しうる記述を error と warn に分けて報告。ルール・フェーズ定義・配点・settings.json の登録・チケット・プロジェクトを見る | error があれば 1 |
| `--suggest [--json]` | 記録（`decisions.jsonl` と、同じ置き場で回した `decisions.*.jsonl`）から、ルールの候補を `rules.yml` と `rule-samples.yml` の形の下書きで出す。どのルールも言及せず何度も渡った形は `ask` のルールの候補、同じ呼び出しを N 回以上止めた `deny` は `message` を見直す候補。候補ごとに `--lint` と同じ読みと `--test-samples` と同じ判定で確かめ、通ったものだけを出す（ルールを足す候補は共通レイヤーのコピーに足した一時ファイルで試す）。`allow` は出さない。何も書かない | 常に 0 |
| `--version [--json]` | 版・組み立ての元のコミット（`build.py` が埋める。ソースでは `unknown`）・互換の版・受け付けるフラグ（引数の定義から引く）・読む書式の版。設定もワークスペースも読まない（README「版の JSON」） | 0 |
| `--lint [--json] --flow <パス>` | 上に加えて、子のフロー 1 本（9.3.1）を `SubagentStart` と同じ読み手・同じ検査で読み、読めなければ場所 `(flow)` の error で言う。読めたフローの構造と名前の怪しいところは warn。`--json` なら読めた中身を `flow.data`、渡る手順の行を `flow.rendered` に載せ（読めなければどちらも `null`）、選べる名前を `flow.candidates` に載せる。パスは起動した場所からの相対でよい | error があれば 1 |
| `--docs [絞り込み] [--sort …] [-r] [--limit N] [--format table\|path\|detail\|json\|jsonl\|count]` | ワークスペースと、プロジェクトの置き場の直下の各プロジェクト（別の git）の md（それぞれの `git ls-files --cached --others --exclude-standard`、ccnavi ディレクトリの下は除く）を、頭の frontmatter の索引で横断して引く（`docsearch`）。パスはワークスペースルートから。md が直下にあるディレクトリごとの `index.jsonl` を差分で新しくしてから引く。書くのは git がそこの `index.jsonl` を無視しているときだけで、どのディレクトリでも無視していないツリーは対象外にして名指しする（`.gitignore` は書き換えない。ワークスペースの 1 行は導入スクリプトが配るときに足す）。ccnavi の形でない `index.jsonl` は上書きも削除もせず、実体がツリーの外に出るディレクトリは読まない。一時ファイルは `.git/` の中に作る（`.git` が別のファイルシステムなら、そのディレクトリの下の git に無視される `.ccnavi-tmp-*/index.jsonl`）。git への問い合わせの失敗は対象外と分けて言う。形は README「ドキュメントの索引」 | 引ければ 0（0 件でも）。使い方の誤りは 1 |

**レイヤーの置き場を動かすフラグは 7 本あり、どれも診断でだけ有効になる**。共通レイヤーの中身は
`--rules` / `--phases` / `--risk`、レイヤーを探す先は `--projects`（プロジェクトのレイヤーの置き場）と
`--project-home`（ccnavi ディレクトリの名前）、1 つのレイヤーだけを差し替えるのは
`--project-rules-file <名前>=<パス>`（その名前のプロジェクトのルールの代わり）と
`--project-phases-file <名前>=<パス>`（その名前のレイヤー。`self` は自身のレイヤーのフェーズ定義の代わり）。
診断の外（hook からの判定、`ticket` / `review` の副命令）に渡すと落とし、落としたことを標準エラーに
出す。守る対象も本来の場所のまま。レイヤーの配点にはまだ差し替えが無い。

**互換の版**（`src/ccnavi/entry/version.py` の `COMPAT`）は、実行ファイルと呼ぶ側（`.ccnavi/scripts/` の sh の `CCNAVI_COMPAT`、拡張の
`EXTENSION_COMPAT`）の契約の版で、3 か所に同じ値を書く。sh は実行ファイルを起動する前に、拡張は起動のときに `--version` を読んで
比べ、`--lint` は sh の値と比べる（`(version)` の warn）。食い違えば、どれも直し方（ccnavi のリポジトリなら組み立て直し、配布先なら
配り直し、拡張が古ければ入れ直し）を名指しする。止めはしない。新しいフラグを使う前は、渡してみて argparse のエラーで見分けるのでは
なく `flags` を見る。`--version` を知らない実行ファイルは古いとして扱う。レイヤーは頭の `version:` が書式の版を示す。
読めない版は読む側が error にするので、レイヤーには別の版を足さない。

`--flow <パス>` も診断でだけ有効で、読むのは `--lint` だけ。`--test` / `--test-samples` / `--explain` に渡すと
「`--lint` でだけ読む」と言って落とす。判定にも採点にも使われない（9.3.1）。

JSON の形は README の「試験の JSON」「lint の JSON」「ボードの JSON」「候補の JSON」に定める。拡張はこれを並べるだけで、
提案もマーカーも自分で解釈せず、glob も regex も自分で当てず、点も数えない。止まっているか・
承認待ち・ワークツリーの有無は、判定と承認が使う関数をそのまま呼んで載せる。チケットには状態の履歴（9.6）の
新しい側も `history` で載せる。カードはそれを折りたためる「履歴」に並べるだけで、列やバッジは履歴から決めない。

承認は拡張の中で完結する（9.4）。オーバーレイが `--agree --preview --json` で一覧を見せ、ユーザが押したら
`--agree --yes` を子プロセスで打つ。承認できたら、渡す文を見せる。そのあとボードを読み直し、列が変わったカードに動いた表示を
出す。動いた表示は次に何かが動くまで残す。それを覚えているのは拡張ホスト。
残った指摘の対応方針（`decide`）も拡張の中で決める。オーバーレイが `ccnavi-review.sh decide <N> --preview` で
指摘とダイジェストを見せ、ユーザが指摘ごとに選んで押したら `decide <N> --choices … --digest …` を子プロセスで打つ
（形は README「残った指摘の JSON」）。`close-early` はボードに置かない。「レビュー済み連絡」は、承認の文と
同じ経路（コピー / 新しいセッションで開く）で文を渡すだけで、拡張はマーカーを置かず実行ファイルも起動しない。
`confirm` を打つのは文を受けた親（9.8、単体の `sh …ccnavi-review.sh`）。依頼のマーカーが持つマージリクエストの
URL はリンクとして出すが、その先の状態は見に行かない。

設定を画面で直す 3 つ（ルール管理・リスク管理・フェーズ管理）は、編集中の内容を一時ファイルに書いて
`--rules` / `--risk` / `--phases` で渡し、`--lint` が error を返さないときだけ保存する。フロー編集画面も
本文を一時ファイルに書いて `--lint --json --flow` に渡し、`(flow)` の error が無く、`flow.data` が画面の中身と
同じときだけ開く・保存する（ほかの設定の指摘では止めない。`--version` の `flags` に `--flow` が無いか、`--version` を知らない古い実行ファイルでは開かない）。

リスク管理とフェーズ管理は、`CCNAVI_TICKET_CONTROL` が `disable` のワークスペースでは開かない（適用されない
設定だから）。入口はすべて隠す（サイドパネル・コマンドパレット、フェーズ管理はプロジェクト管理画面の
ボタンも）。開く側でも見る。ルール管理は隠さない。既に開いている画面は追いかけない。

フェーズ管理の画面は、`yes` `on` `1:30` `0755` のような語を引用符で囲んで書く（実行ファイルは YAML 1.1 で
読むため）。同じ識別子を 2 つ書く保存は拡張が止める。計画が指すフェーズ定義を消す保存は `--lint` が止める
（承認済みチケットについては定義が読めるかまで。計画との食い違いは次の承認と着手で実行ファイルが言う）。

`--lint` の登録の検査は `.claude/settings.json` しか見ず、モードがどこから来たかを示す。`rules.yml` に
error がある間は配点もフェーズ定義も保存できない。

`--lint` は `.claude/settings.json` の env の `CCNAVI_BIN_PATH` も見る（`lint_project._bin_path`）。指す先が在るのに
実行できなければ error（hook が起動しない）。POSIX でだけ見る（`os.access(X_OK)`）。パスは書いたとおりに見て、
`.exe` を補わない。指す先が無いときは言わない（自己防衛が missing と言う）。

**拡張との取り決め**（REQ-EXT）のうち、作りに関わるもの。

- 文字コード: 入口（`__main__` の `hookio.rebind_streams`）が標準入出力を UTF-8・改行 LF に張り直す。診断の JSON
  （`--explain` / `--test` / `--test-samples` / `--lint` / `--suggest` / `--version`）は `ensure_ascii=True` で ASCII に落とし、
  承認の JSON と残った指摘の JSON は `ensure_ascii=False` で書く
- 古い実行ファイルの見分け: argparse の苦情（`unrecognized arguments: --version`）を拡張が読む。
  `parse_args` の `SystemExit` は捕まえて終了コード 1 にする
- `ccnavi c1 family <親>` の答え（1 行目 `c1 1`、`target yes|no|stop`）は、sh（C1）だけでなく拡張も読む。
  フローを保存した後に承認の push を送るかを、これで決める
- 診断ログ（`logs/diag/`）の行の形・置き場・レベルは、sh・Python・拡張の 3 つの logger で揃える（`docs/claude/logging.md`）。
  `tests/core/test_diaglog.py` が 3 つの行を突き合わせる
- Chrome 拡張は実行ファイルを起動せず、`src/ccnavi/` を Pyodide で import して、判定のコア（`hook/core.py`（実体は `core_base`・`core_approve`・`core_review`・`core_withdraw`）の
  Snapshot → 判定 → Changes）と `cli.run` を直に呼ぶ。呼ぶ名前の一覧は REQ-EXT-27 の表にある
