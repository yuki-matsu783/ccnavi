---
type: design
title: 10. 診断と機械可読な出力
description: ユーザと VS Code 拡張が使う診断のオプションと機械可読な出力
tags: [design-doc, cli]
keywords: [診断, --explain, --test, --lint, --json, 機械可読]
---

[設計書の入口に戻る](../20_design.md)

## 10. 診断と機械可読な出力

診断（この章のオプション）だけは hook から呼ばれない。ユーザがターミナルから打ち、VS Code 拡張が読む。判定は hook と同じ関数を
通る（REQ-DIA-03）。試験（`--test` / `--test-expectations`）は
state を内部で外す。拡張と `scripts/check_rules.py` は念のため `--log ""` と `--state ""` も渡す。

| オプション | 出すもの | 終了コード |
|---|---|---|
| `--test <ツール> <対象> [--json] [--cwd <ディレクトリ>] [--permission-mode <モード>] [--agent-id <id>]` | 判定・根拠コード・判定に渡した payload・ヒットしたルール（タイプ、書いた式、翻訳後の正規表現、`file` か `outside`）・行き着く先・縮退したか既定を使ったか・ヒットした中で実行されるコマンド（`unwrapped`、6.3.1）・返る文面。ヒットしたルールは判定が適用したもの（`Record.hits`）そのもので、ルールファイルを読み直さない。組み込み（保護・退避した既定）は `outside` で、式は頭だけに切る。モードは常に `enable`。payload の 3 つの欄は hook が Claude Code から受け取るもので、既定はルート・権限モード無し・メイン。知らない権限モードと無いディレクトリは断る。切り替えの環境変数（`CCNAVI_GUARD_UNWATCHED` など）は hook と同じ関数で解いてから判定に渡す。`--test Stop "(stop)"` はターンの終わりに使われるルール（6.6）を並べ、あれば `allow`、無ければ `skip`。期待の `tool: Stop` も同じ | 0。payload の欄の誤り・2 語（ツールと対象）にオプションが入った形・余った語は 1 |
| `--test-expectations <期待> [--json]` | 期待する判定をすべて判定に掛け、期待と食い違ったものを名指し | 文字の出力は食い違いがあれば 1、JSON は常に 0、期待のファイルが読めなければ 1 |
| `--explain [--json]` | タイプごとのルール、プロジェクトの一覧とルールの可否、権限モードの扱い（権限モード × 読めたか読み切れないかの結末を、判定と同じ `judge.undeclared_verdict` で組む）、チケット制御の値、元のプロジェクトが無いワークツリー（節「■ 元のプロジェクトが無いワークツリー」。該当があるときだけ。11.3）、承認済みチケットの一覧、親の局面、フェーズごとの状態・マーカー・止まっているか・リスク、上位の設定に却下された要求（節「■ 上位の設定に却下された要求」。全件を 1 件 1 行で、該当があるときだけ。REQ-SES-02） | 0 |
| `--lint [--json]` | 判定を行わず、防御を無効化しうる記述を error と warn に分けて報告。ルール・フェーズ定義・配点・settings.json の登録・チケット・プロジェクトを見る | error があれば 1 |
| `--suggest [--json]` | 記録（`decisions.jsonl` と、同じ置き場で回した `decisions.*.jsonl`）から、ルールの候補を `rules.yml` と `rule-expectations.yml` の形の下書きで出す。どのルールも言及せず何度も渡った形は `ask` のルールの候補、同じ呼び出しを N 回以上止めた `deny` は `message` を見直す候補。候補ごとに `--lint` と同じ読みと `--test-expectations` と同じ判定で確かめ、通ったものだけを出す（ルールを足す候補は共通レイヤーのコピーに足した一時ファイルで試す） | 常に 0 |
| `--version [--json]` | 版・組み立ての元のコミット（`build.py` が埋める。ソースでは `unknown`）・互換の版・受け付けるフラグ（引数の定義から引く）・読む書式の版。設定もワークスペースも読まない（docs/11_requirements/json-shapes.md「版の JSON」） | 0 |
| `--lint [--json] --flow <パス>` | 上に加えて、子のフロー 1 本（9.3.1）を `SubagentStart` と同じ読み手・同じ検査で読み、読めなければ場所 `(flow)` の error で言う。読めたフローの構造と名前の怪しいところは warn。`--json` なら読めた中身を `flow.data`、渡る手順の行を `flow.rendered` に載せ（読めなければどちらも `null`）、選べる名前を `flow.candidates` に載せる。パスは起動した場所からの相対でよい | error があれば 1 |
| `--docs [絞り込み] [--sort …] [-r] [--limit N] [--format table\|path\|detail\|json\|jsonl\|count]` | ワークスペースと、プロジェクトの置き場の直下の各プロジェクト（別の git）の md（それぞれの `git ls-files --cached --others --exclude-standard`、ccnavi ディレクトリの下は除く。ただし `.ccnavi/docs/` は載せる）を、頭の frontmatter の索引で横断して引く（`docsearch`）。パスはワークスペースルートから。md が直下にあるディレクトリごとの `ccnavi-index.jsonl` を差分で新しくしてから引く。書くのは git がそこの `ccnavi-index.jsonl` を無視しているときだけで、どのディレクトリでも無視していないツリーは、書く前にそのツリーの `.gitignore` に `**/ccnavi-index.jsonl` を 1 行だけ足して（ワークスペースもプロジェクトも同じ。足したことは標準エラーと SessionStart の案内で名指しする。未コミットの差分が残る）確かめ直す。`!` で否定する行がある（ユーザが追跡している）・足せない・足しても無視されないツリーだけが対象外になり、名指しされる。ccnavi の形でない `ccnavi-index.jsonl` はそのまま残し、実体がツリーの外に出るディレクトリは対象外にする。一時ファイルは `.git/` の中に作る（`.git` が別のファイルシステムなら、そのディレクトリの下の git に無視される `.ccnavi-tmp-*/ccnavi-index.jsonl`）。git への問い合わせの失敗は対象外と分けて言う。形は docs/21_design/diagnostics.md の 10.1「ドキュメントの索引」 | 引ければ 0（0 件でも）。使い方の誤りは 1 |

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
なく `flags` を見る。sh は `--version` の答えから互換の版を読めなければ「不明」として食い違いに数える。拡張は版を聞けなかったとき、起動では何も言わず、フラグを使う前に理由を出して先へ進まない。レイヤーは頭の `version:` が書式の版を示す。
読めない版は読む側が error にする。

`--flow <パス>` も診断でだけ有効で、読むのは `--lint` だけ。`--test` / `--test-expectations` / `--explain` に渡すと
「`--lint` でだけ読む」と言って無視する。判定にも採点にも使われない（9.3.1）。

JSON の形は docs/11_requirements/json-shapes.md「試験の JSON」「lint の JSON」「ボードの JSON」「候補の JSON」に定める。拡張はこれを並べるだけで、
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
（形は docs/11_requirements/json-shapes.md「残った指摘の JSON」）。`close-early` はボードに置かない。
［レビュー完了］は、まず `decide <N> --preview` で残った指摘を読む。この読み取りは厳しく検証し（親・フェーズ・MR の番号が頼んだものと同じ、
`digest` が `v2.` で始まる、`threads` が配列でスレッドの要素がすべて正しい、など）、1 つでも外れれば「指摘を読み取れませんでした（理由）」と
言って確定のボタンを出さない。0 件なら「フェーズ N に未解決の指摘はありません」と［レビュー済みにする］、1 件以上なら対応方針の画面になる。
対応方針の画面の「エージェントに相談するプロンプトをコピー」は、`decide <N> --preview` だけを単体で打たせ、確定する操作
（`ccnavi-user-decide.sh`・`confirm`）を禁じるプロンプトをクリップボードに入れるだけで、何も置かない。確定した後の表示は実行ファイルの答えに従い、
`reviewed` が偽で `pending_extra` があれば「表示した後に指摘が追加されたため、レビュー済みにしていません」と言って、決め直しへ戻れる。
「MR の状況を確認」は、`ccnavi sync retired` で片付けの記録を持つ親を集め、親ごとに（そのリポジトリを cwd にして）
`ccnavi-review.sh mr-state <番号>` を打つ。どちらも読むだけで、`mr-state` は 1 回ごとに期限を付ける。マージ済みの親の［片付ける］は、
確認のダイアログのあと、ボードのターミナルへ `cd <ルート> && sh <ルート>/.ccnavi/scripts/ccnavi-sync.sh <親>` を送るだけ
（マージ済みの確かめ直しと片付けは sync が決める）。マージせずに閉じた親は、記録を消すコマンドをコピーするボタンだけを出し、送らない。

設定を画面で直す 3 つ（ルール管理・リスク管理・フェーズ管理）は、編集中の内容を一時ファイルに書いて
`--rules` / `--risk` / `--phases`（ワークスペースとプロジェクトの設定のレイヤーは `--project-rules-file` / `--project-risk-file` / `--project-phases-file`）で渡し、`--lint` が error を返さないときだけ保存する。フロー編集画面も
本文を一時ファイルに書いて `--lint --json --flow` に渡し、`(flow)` の error が無く、`flow.data` が画面の中身と
同じときだけ開く・保存する（`--version` の `flags` に `--flow` が無いか、版を確かめられないときは開かない）。

リスク管理・フェーズ管理・テンプレート管理は、`CCNAVI_TICKET_CONTROL` が `disable` のワークスペースでは開かない（適用されない
設定だから）。入口はすべて隠す（サイドパネル・コマンドパレット、フェーズ管理はプロジェクト管理画面の
ボタンも）。開く側でも見る。ルール管理は隠さない。既に開いている画面は追いかけない。

フェーズ管理の画面は、`yes` `on` `1:30` `0755` のような語を引用符で囲んで書く（実行ファイルは YAML 1.1 で
読むため）。同じ識別子を 2 つ書く保存は拡張が止める。計画が指すフェーズ定義を消す保存は `--lint` が止める
（承認済みチケットについては定義が読めるかまで。計画との食い違いは次の承認と着手で実行ファイルが言う）。

`--lint` の登録の検査は `.claude/settings.json` しか見ず、モードがどこから来たかを示す。`rules.yml` に
error がある間は配点もフェーズ定義も保存できない。

`--lint` は `.claude/settings.json` の env の `CCNAVI_ENTRY_PATH` も見る（`lint_project._bin_path`）。指す先が在るのに
実行できなければ error（hook が起動しない）。POSIX でだけ見る（`os.access(X_OK)`）。パスは書いたとおりに見て、
`.exe` を補わない。指す先が無いときは言わない（自己防衛が missing と言う）。

**拡張との取り決め**（REQ-EXT）のうち、作りに関わるもの。

- 文字コード: 入口（`__main__` の `hookio.rebind_streams`）が標準入出力を UTF-8・改行 LF に張り直す。診断の JSON
  （`--explain` / `--test` / `--test-expectations` / `--lint` / `--suggest` / `--version`）は `ensure_ascii=True` で ASCII に変換し、
  承認の JSON と残った指摘の JSON は `ensure_ascii=False` で書く
- `parse_args` の `SystemExit` は捕まえて終了コード 1 にする
- `ccnavi write-and-push family <親>` の答え（1 行目 `write-and-push 1`、`target yes|no|stop`）は、sh（write-and-push）だけでなく拡張も読む。
  フローを保存した後に承認の push を送るかを、これで決める
- 診断ログ（`logs/diag/`）の行の形・置き場・レベルは、sh・Python・拡張の 3 つの logger で揃える（`.agents/docs/logging.md`）。
  `tests/core/test_diaglog.py` が 3 つの行を突き合わせる
- Chrome 拡張は `src/ccnavi/` を Pyodide に同梱し、公開の入口 `entry/ext.py` が再公開するモジュールを通して、判定のコア（`tickets/core/api.py`（実体は `core.base`・`core.approve`・`core.review`）の
  Snapshot → 判定 → Changes）と `cli.run` を直接呼ぶ。呼ぶ名前の一覧は REQ-EXT-27 の表にある

### 10.1 `--docs` の索引

使い方は [ドキュメントの索引の使い方](../41_guide/docs-search.md)。ここは索引の作り方と形を書く。

引くのはワークスペースと、プロジェクトの置き場の直下の各プロジェクト（それぞれ別の git）を合わせたもの。どこから打っても同じで、
パス（`concept_id`）はワークスペースルートから書く（`projects/lib/docs/x`）。`--path projects/lib` で 1 つのプロジェクトに絞れる。
どのツリーもルートに `.git` を持つものだけで、`.git` の無い `projects/<名前>/` は `--docs` が標準エラーで名指しする。
それぞれの `git ls-files --cached --others --exclude-standard` のうち `*.md` を載せ、実体の無いもの（消してまだステージしていないもの）、
シンボリックリンク、ccnavi ディレクトリ（`.ccnavi/`）の下（`.ccnavi/docs/` を除く）は載せない。ワークスペースの一覧からはプロジェクトの置き場を外す。
実体のパスがそのツリーの外に出るディレクトリ（ジャンクションやシンボリックリンク越し）は読みも書きもしない。
同じ `concept_id`（NFC で揃えて同じもの）は 1 本にまとめる。

frontmatter は md の頭の 64 KiB までを UTF-8 として読む（それより後ろで閉じる frontmatter と、UTF-8 でない md は読めない）。
frontmatter の無い md は頭の 4 KiB だけで止める。

引く前に、md が直下にあるディレクトリごとの `ccnavi-index.jsonl` を新しくする（`--no-refresh` で省く）。`concept_id` と `mtime` が
同じ行は読み直さずに使い回し、中身が変わらなければ書かない。初めての回は md を全部読むので、md が数千本あるツリーでは
数秒かかる。2 回目からは `mtime` の変わった md だけを読む。

- **書くのは git がそこの `ccnavi-index.jsonl` を無視しているときだけ。** md を持つディレクトリのどれでも無視されていないツリー
  （ワークスペースかプロジェクト）は索引の対象外にし、引かずに標準エラーで名指しする。ただし、足せるツリーは書く前に実行ファイルの `ccnavi` が自分で足す。`.gitignore` の末尾に `**/ccnavi-index.jsonl` を 1 行足して（ファイルが無ければ作る。既存の行は変えない）確かめ直し、足したことは標準エラーと `SessionStart` の案内で名指しする（未コミットの差分が残る）。
  足すのは書き込める回だけで、`--no-refresh` では足さない。同じ行が既にあれば足さず、`!` で索引を否定する行があれば（ユーザが追跡している）足さない。足せない・足しても無視されない（追跡されているなど）ときは対象外のまま。
  ワークスペースには導入スクリプト `scripts/ccnavi-setup.sh` も配るときに足す。一部のディレクトリだけが無視されていない（追跡されている
  `ccnavi-index.jsonl` がある）なら、そこは書かずに行だけを組む
- **ccnavi の形でない `ccnavi-index.jsonl` は上書きも削除もしない。** 空か、空でない行が全部下の 4 つのキーを持つ行として読めるときだけを
  ccnavi のものとみなす。それ以外（よその道具のファイル、破損した行、リンク、16 MiB を超えるもの、入れ子が 32 段より深い行、
  `NaN`・`Infinity` を持つ行）は触らず、行だけを組んで引き、`--docs` は標準エラーで、`SessionStart` は 1 行で名指しする。
  行は `\n` だけで割る（値の中の U+2028・U+2029・U+0085 では割らない。行末の `\r` は除く）
- 書くときは git のディレクトリ（`.git/`、submodule なら指す先）に一時ファイルを排他で作り、置き換える。ワークツリーの
  `git status` には出ない。打ち切りで残った一時ファイル（`ccnavi-index-*.tmp`）は、10 分より古ければ次の回に消す。
  `.git` がワークツリーと別のファイルシステムにある（置き換えが EXDEV で失敗する）ときは、そのディレクトリの下に
  `.ccnavi-tmp-<番号>/ccnavi-index.jsonl` を作って置き換え、ディレクトリごと消す。この名前が git に無視される（`**/ccnavi-index.jsonl` でヒットする）
  ことを先に確かめ、無視されなければ書かない。残骸は 10 分より古く、中身が `ccnavi-index.jsonl` だけのものに限って消す
- git への問い合わせの失敗（git が無い・期限切れ・破損したリポジトリ）は、`.git` が無いのとも無視されていないのとも別に扱う。
  `--docs` は「git への問い合わせに失敗したので引かない」とそのツリーを名指しし、`SessionStart` は何も出さない。
  git にはユーザの環境の pathspec の読み方（`GIT_LITERAL_PATHSPECS` など）を外して聞き、`:(exclude)` のような名前の
  ディレクトリも字どおりに扱う

`SessionStart` はサブエージェントでなければ、ワークスペースとプロジェクトの索引を同じ手順で新しくし、引き方の 1 行（frontmatter の
決まりの文書があれば、その引き方の 1 文）を `additionalContext` につける。対象外にしたツリーと書き換えなかった `ccnavi-index.jsonl` があれば短く名指しする。
md が 1 本も無い・git の外なら何も出さない。新しくするのに使うのは 3 秒（`docsearch.START_SECONDS`）と hook の判定の期限の
残りの小さいほうまで。まず全部のディレクトリで使い回せる行を見て、読み直しの要る md を、待つ本数の少ないディレクトリから
1 本ずつ期限を見ながら読む。過ぎたら読むのをやめ、どのディレクトリも「読めた md の新しい行と、読めなかった md の既存の行」で
書く（読めなかった md は次の回に読む）。大きなディレクトリも回を重ねれば埋まり、小さなディレクトリはその後ろで待たない。
残りが 0.5 秒（`MIN_REFRESH_SECONDS`）に満たない（期限が既に切れている）ときは新しくせず、md を読まず書かずに既存の
`ccnavi-index.jsonl` の行だけで案内する（git には 1 秒だけ与える。それも間に合わなければ、ワークスペースルートに md があれば
引き方だけを出す）。`--docs` は引く前に期限なしで新しくする。何が起きてもセッションの開始は最後まで進む。

絞り込みのフラグ（`--type` から `--no-refresh` まで）を `--docs` の外で渡すと、その旨を出して 1 で終わる（このフラグが無かったころに
argparse が止めていた打ち間違いを、知らせずに通さないため）。`--docs` にほかの経路のフラグ（`--lint` `--yes` `--preview` `--result`
`--tickets` `--flow` など）と、判定やチケットの経路の設定・sh が渡すフラグ（`--cwd` `--mode` `--approved` `--rules` `--phases`
`--risk` `--projects` `--project-home` `--ticket-control` `--guard-core-files` `--guard-ticket-approval` `--restore-if-deny`
`--record-writes` `--choose-out` `--actor` `--via` など）を付けたときも、無視せずに 1 で終わる。
使われるのは引く場所の `--root` と、記録の置き場の `--log` / `--state`（`--docs` は何も記録しないので結果は変わらない）だけ。

`ccnavi-index.jsonl` と `--format jsonl` の 1 行、`--format json` の配列の要素は同じ形。

```json
{"concept_id":".ccnavi/docs/worktree","directory":".ccnavi/docs","frontmatter":{"type":"guide","tags":["worktree","git"]},"mtime":"2026-09-27T19:56:23"}
```

| キー | 何 |
|---|---|
| `concept_id` | 引いた結果ではワークスペースルートからの相対パス（`/` 区切り）から `.md` を除いたもの。`ccnavi-index.jsonl` に書く行は、そのリポジトリのルートから（プロジェクトの頭の `projects/<名前>/` が無い） |
| `directory` | そのファイルがあるディレクトリ（`concept_id` と同じ基準。リポジトリのルート直下は `.`、プロジェクトのルート直下は `projects/<名前>`） |
| `frontmatter` | 頭の `---` から `---`（か `...`）までを YAML（SafeLoader、別名は拒む）で読んだもの。無い・読めない・マッピングでない・JSON に書けない（桁の多すぎる整数など）なら `null`。日付などの JSON に無い値は文字列 |
| `mtime` | ファイルの更新日時。ローカル時刻の `YYYY-MM-DDTHH:MM:SS` |

**古い `ccnavi-index.jsonl` が残る条件。** md が全部消えたディレクトリの `ccnavi-index.jsonl` を消すのは、そこに追跡されている md が
あった（`git ls-files --cached` に出るが実体が無い）ときだけで、しかも ccnavi の形で、git に無視されているものに限る。
追跡されていない md だけを持っていたディレクトリ、ディレクトリごと消えたもの、`.gitignore` に入ったディレクトリ、索引の対象外に
なったツリーの `ccnavi-index.jsonl` は残る。残ったものは読まない（引くのは、いま md を持つディレクトリの分だけ）ので結果は変わらない。
`mtime` は秒で比べるので、同じ秒の中で 2 度書き換えた md は次に `mtime` が変わるまで古い行のままになる。
