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
通る（REQ-DIA-03）。試験（`--test` / `--test-expectations`）は
state を内部で外す。拡張と `scripts/check_rules.py` は念のため `--log ""` と `--state ""` も渡す。

| オプション | 出すもの | 終了コード |
|---|---|---|
| `--test <ツール> <対象> [--json] [--cwd <ディレクトリ>] [--permission-mode <モード>] [--agent-id <id>]` | 判定・根拠コード・判定に渡した payload・当たったルール（タイプ、書いた式、翻訳後の正規表現、`file` か `outside`）・行き着く先・縮退したか既定を使ったか・当たった中で実行されるコマンド（`unwrapped`、6.3.1）・返る文面。当たったルールは判定が当てたもの（`Record.hits`）そのもので、ルールファイルを読み直さない。組み込み（保護・退避した既定）は `outside` で、式は頭だけに切る。モードは常に `enable`。payload の 3 つの欄は hook が Claude Code から受け取るもので、既定はルート・権限モード無し・メイン。知らない権限モードと無いディレクトリは断る。切り替えの環境変数（`CCNAVI_GUARD_UNWATCHED` など）は hook と同じ関数で解いてから判定に渡す。`--test Stop "(stop)"` はターンの終わりに使われるルール（6.6）を並べ、あれば `allow`、無ければ `skip`。期待の `tool: Stop` も同じ | 0。payload の欄の誤り・2 語（ツールと対象）にオプションが入った形・余った語は 1 |
| `--test-expectations <期待> [--json]` | 期待する判定をすべて判定に掛け、期待と食い違ったものを名指し | 文字の出力は食い違いがあれば 1、JSON は常に 0、期待のファイルが読めなければ 1 |
| `--explain [--json]` | タイプごとのルール、プロジェクトの一覧とルールの可否、権限モードの扱い（権限モード × 読めたか読み切れないかの結末を、判定と同じ `judge.undeclared_verdict` で組む）、チケット制御の値、元のプロジェクトが無いワークツリー（節「■ 元のプロジェクトが無いワークツリー」。該当があるときだけ。11.3）、承認済みチケットの一覧、親の局面、フェーズごとの状態・マーカー・止まっているか・リスク、上位の設定に却下された要求（節「■ 上位の設定に却下された要求」。全件を 1 件 1 行で、該当があるときだけ。REQ-SES-02） | 0 |
| `--lint [--json]` | 判定を行わず、防御を無効化しうる記述を error と warn に分けて報告。ルール・フェーズ定義・配点・settings.json の登録・チケット・プロジェクトを見る | error があれば 1 |
| `--suggest [--json]` | 記録（`decisions.jsonl` と、同じ置き場で回した `decisions.*.jsonl`）から、ルールの候補を `rules.yml` と `rule-expectations.yml` の形の下書きで出す。どのルールも言及せず何度も渡った形は `ask` のルールの候補、同じ呼び出しを N 回以上止めた `deny` は `message` を見直す候補。候補ごとに `--lint` と同じ読みと `--test-expectations` と同じ判定で確かめ、通ったものだけを出す（ルールを足す候補は共通レイヤーのコピーに足した一時ファイルで試す） | 常に 0 |
| `--version [--json]` | 版・組み立ての元のコミット（`build.py` が埋める。ソースでは `unknown`）・互換の版・受け付けるフラグ（引数の定義から引く）・読む書式の版。設定もワークスペースも読まない（README「版の JSON」） | 0 |
| `--lint [--json] --flow <パス>` | 上に加えて、子のフロー 1 本（9.3.1）を `SubagentStart` と同じ読み手・同じ検査で読み、読めなければ場所 `(flow)` の error で言う。読めたフローの構造と名前の怪しいところは warn。`--json` なら読めた中身を `flow.data`、渡る手順の行を `flow.rendered` に載せ（読めなければどちらも `null`）、選べる名前を `flow.candidates` に載せる。パスは起動した場所からの相対でよい | error があれば 1 |
| `--docs [絞り込み] [--sort …] [-r] [--limit N] [--format table\|path\|detail\|json\|jsonl\|count]` | ワークスペースと、プロジェクトの置き場の直下の各プロジェクト（別の git）の md（それぞれの `git ls-files --cached --others --exclude-standard`、ccnavi ディレクトリの下は除く。ただし `.ccnavi/docs/` は載せる）を、頭の frontmatter の索引で横断して引く（`docsearch`）。パスはワークスペースルートから。md が直下にあるディレクトリごとの `ccnavi-index.jsonl` を差分で新しくしてから引く。書くのは git がそこの `ccnavi-index.jsonl` を無視しているときだけで、どのディレクトリでも無視していないツリーは、書く前にそのツリーの `.gitignore` に `**/ccnavi-index.jsonl` を 1 行だけ足して（ワークスペースもプロジェクトも同じ。足したことは標準エラーと SessionStart の案内で名指しする。未コミットの差分が残る）確かめ直す。`!` で否定する行がある（ユーザが追跡している）・足せない・足しても無視されないツリーだけが対象外になり、名指しされる。ccnavi の形でない `ccnavi-index.jsonl` はそのまま残し、実体がツリーの外に出るディレクトリは対象外にする。一時ファイルは `.git/` の中に作る（`.git` が別のファイルシステムなら、そのディレクトリの下の git に無視される `.ccnavi-tmp-*/ccnavi-index.jsonl`）。git への問い合わせの失敗は対象外と分けて言う。形は README「ドキュメントの索引」 | 引ければ 0（0 件でも）。使い方の誤りは 1 |

**レイヤーの置き場を動かすフラグは 8 本あり、どれも診断でだけ有効になる**。共通レイヤーの中身は
`--rules` / `--phases` / `--risk`、レイヤーを探す先は `--projects`（プロジェクトのレイヤーの置き場）と
`--project-home`（ccnavi ディレクトリの名前）、1 つのレイヤーだけを差し替えるのは
`--project-rules-file <名前>=<パス>`（その名前のプロジェクトのルールの代わり）と
`--project-phases-file <名前>=<パス>`（その名前のレイヤー。`self` は自身のレイヤーのフェーズ定義の代わり）と
`--project-risk-file <名前>=<パス>`（同じく配点の代わり。統合結果なので共通レイヤーと合わせて検証する。境目の逆転、同 `id` の衝突、
`script:` の存在確認はそのレイヤーの git プロジェクトルートが基準）。
診断の外（hook からの判定、`ticket` / `review` のサブコマンド）に渡すと外し、外したことを標準エラーに
出す。守る対象も本来の場所のまま（守る側は差し替えを見ない `layer_real_path` を引く）。

**記録・state・承認済みチケット・提案の置き場を動かす `--log` / `--state` / `--approved` / `--tickets` も、診断でだけ有効になる。**
診断の外に渡すと、レイヤーの置き場のフラグと同じく外し、同じ文面を標準エラーに出す。`.ccnavi/scripts/` の sh
（`ccnavi-ticket.sh` / `ccnavi-review.sh`）は受け取った引数を `"$@"` で実行ファイルへそのまま渡すので、診断の外で効くと、
エージェントが記録を止めた形（`--log ""`）や、承認済みチケット・提案の置き場を差し替えた形でサブコマンドを動かせる（P2・P6）。
write-and-push の書いたパスの一覧（`--record-writes`）も、診断の外では `--state` を見ずに既定の state の置き場だけを一覧から外す。

**互換の版**（`src/ccnavi/entry/version.py` の `COMPAT`）は、実行ファイルと呼ぶ側（`.ccnavi/scripts/` の sh の `CCNAVI_COMPAT`、拡張の
`EXTENSION_COMPAT`）の契約の版で、3 か所に同じ値を書く。sh は実行ファイルを起動する前に、拡張は起動のときに `--version` を読んで
比べ、`--lint` は sh の値と比べる（`(version)` の warn）。食い違えば、どれも直し方（ccnavi のリポジトリなら組み立て直し、配布先なら
配り直し、拡張が古ければ入れ直し）を名指しする。新しいフラグを使う前は、渡してみて argparse のエラーで見分けるのでは
なく `flags` を見る。`--version` を知らない実行ファイルは古いとして扱う。レイヤーは頭の `version:` が書式の版を示す。
読めない版は読む側が error にする。

`--flow <パス>` も診断でだけ有効で、読むのは `--lint` だけ。`--test` / `--test-expectations` / `--explain` に渡すと
「`--lint` でだけ読む」と言って無視する。判定にも採点にも使われない（9.3.1）。

JSON の形は README の「試験の JSON」「lint の JSON」「ボードの JSON」「候補の JSON」に定める。拡張はこれを並べるだけで、
止まっているか・
承認待ち・ワークツリーの有無は、判定と承認が使う関数をそのまま呼んで載せる。

**`--explain --json` の `settings.mode` と `settings.ticket_control` は、hook がいま受け取るはずの値で答える。**
拡張が起動した実行ファイルには、Claude Code が hook に渡す `.claude/settings*.json` の `env` が届かない。
そこで `--explain --json` だけは、プロセスの環境の上に、ワークスペースルートの `.claude/settings.json`、
`.claude/settings.local.json` の `env` をこの順で重ねる（Claude Code と同じ順。`settings.hook_view`）。ccnavi 自身の
ソースツリーでは上書き設定ファイル（`ccnavi.settings.local.json`）が環境より強いのも `load` と同じ。重ねた値は、判定と同じ
`modes.resolve_mode` と `selfguard.resolve` で解く。読めない値は `enable`、設定から来た `disable` のモードは
「環境からだけ受ける」の規則のまま（`env` は Claude Code が環境として渡すので受ける）。フラグ（`--mode`・`--ticket-control`）が
あればそれが勝つ。ボードの中身（チケットを出すか）も同じ値で組み、`settings` の値と食い違わせない。
hook の判定の経路（`load`）はこの重ねを使わず、プロセスに渡った環境だけを読む。
拡張はこの 2 つの値をそのまま表示と入口の出し分けに使う。

承認は拡張の中で完結する（9.4）。オーバーレイが `--agree --preview --json` で一覧を見せ、ユーザが押したら
`user agree --yes` を子プロセスで打つ。承認できたら、渡す文を見せる。そのあとボードを読み直し、列が変わったカードに動いた表示を
出す。動いた表示は次に何かが動くまで残す。それを覚えているのは拡張ホスト。
残った指摘の対応方針（`decide`）も拡張の中で決める。オーバーレイが `ccnavi-review.sh decide <N> --preview` で
指摘とダイジェストを見せ、ユーザが指摘ごとに選んで押したら `ccnavi-user-decide.sh <N> --choices … --digest …` を子プロセスで打つ
（形は README「残った指摘の JSON」）。`close-early` はボードに置かない。「レビュー済み連絡」は、承認の文と
同じ経路（コピー / 新しいセッションで開く）で文を渡す。押したときに未解決の指摘の有無を文に入れるため `decide <N> --preview` で読む。
`confirm` を打つのは文を受けた親（9.8、単体の `sh …ccnavi-review.sh`）。依頼のマーカーが持つマージリクエストの
URL はリンクとして出すが、その先の状態は見に行かない。

設定を画面で直す 3 つ（ルール管理・リスク管理・フェーズ管理）は、編集中の内容を一時ファイルに書いて
`--rules` / `--risk` / `--phases`（ワークスペースとプロジェクトの設定のレイヤーは `--project-rules-file` / `--project-risk-file` / `--project-phases-file`）で渡し、`--lint` が error を返さないときだけ保存する。フロー編集画面も
本文を一時ファイルに書いて `--lint --json --flow` に渡し、`(flow)` の error が無く、`flow.data` が画面の中身と
同じときだけ開く・保存する（`--version` の `flags` に `--flow` が無いか、`--version` を知らない古い実行ファイルでは開かない）。

リスク管理・フェーズ管理・テンプレート管理は、`CCNAVI_TICKET_CONTROL` が `disable` のワークスペースでは開かない（適用されない
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
  （`--explain` / `--test` / `--test-expectations` / `--lint` / `--suggest` / `--version`）は `ensure_ascii=True` で ASCII に変換し、
  承認の JSON と残った指摘の JSON は `ensure_ascii=False` で書く
- 古い実行ファイルの見分け: argparse の苦情（`unrecognized arguments: --version`）を拡張が読む。
  `parse_args` の `SystemExit` は捕まえて終了コード 1 にする
- `ccnavi write-and-push family <親>` の答え（1 行目 `write-and-push 1`、`target yes|no|stop`）は、sh（write-and-push）だけでなく拡張も読む。
  フローを保存した後に承認の push を送るかを、これで決める
- 診断ログ（`logs/diag/`）の行の形・置き場・レベルは、sh・Python・拡張の 3 つの logger で揃える（`docs/claude/logging.md`）。
  `tests/core/test_diaglog.py` が 3 つの行を突き合わせる
- Chrome 拡張は `src/ccnavi/` を Pyodide で import して、判定のコア（`tickets/core.py`（実体は `core_base`・`core_approve`・`core_review`）の
  Snapshot → 判定 → Changes）と `cli.run` を直に呼ぶ。呼ぶ名前の一覧は REQ-EXT-27 の表にある
