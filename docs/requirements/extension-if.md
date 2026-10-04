---
type: requirements
title: ccnavi 要件書 2.13 拡張との取り決め
description: ccnavi が VS Code 拡張と Chrome 拡張に約束すること（実行ファイルの探し方・互換の版・出力の形・Python の API）
tags: [design-doc, extension]
keywords: [要件, REQ-EXT, 拡張, VS Code, Chrome, 互換, COMPAT, CCNAVI_COMPAT, Pyodide, JSON, API, ダイジェスト]
---

[ccnavi 要件書](../requirements.md) に戻る。

### 2.13 拡張との取り決め — REQ-EXT

ccnavi を読む拡張は 2 つある。VS Code 拡張「ccnavi ボード」（`extensions/vscode/ccnavi-board/`）は実行ファイルと sh を
子プロセスで起動し、JSON を読み、ワークスペースのファイルを直接読み書きする。Chrome 拡張「ccnavi 承認ボード」
（`extensions/chrome/ccnavi-approval/`）は実行ファイルを起動せず、ccnavi のソースを同梱して Pyodide の上で import し、
Python の関数を直接呼ぶ。

この節には、ccnavi が拡張に約束することだけを書く。拡張が画面でどうふるまうかは、各拡張の要件書
（VS Code は [ccnavi ボードの要件](../../extensions/vscode/ccnavi-board/docs/requirements.md) の REQ-VSC、
Chrome は [ccnavi 承認ボードの要件](../../extensions/chrome/ccnavi-approval/docs/requirements.md) の REQ-CHR）にある。
JSON の欄の細かい形は README の各 JSON の節と [設計 10](../design/diagnostics.md) に置き、ここからはそこを指す。

### 2.13.1 共通

| ID | 型 | 要求 |
|---|---|---|
| REQ-EXT-01 | 常時 | ccnavi は、振り分けの sh の名前を `ccnavi-launcher.sh` とし、その名前の sh が起動する実行ファイルを、sh の置き場の親の `bin/<os>-<arch>/ccnavi`（Windows は `ccnavi.exe`）に置くこと。`<os>` は `darwin`・`linux`・`windows`、`<arch>` は `x86_64`・`arm64` とすること。arm64 の macOS と Windows では、自分向けが無いときだけ `x86_64` の実行ファイルを起動すること。配布先では sh を `.ccnavi/scripts/ccnavi-launcher.sh` に置き、`.claude/settings.json` の env `CCNAVI_BIN_PATH` にそのパスを書くこと |
| REQ-EXT-02 | 事象 | `--root <パス>` を受けたとき、ccnavi は、そのパスをワークスペースルートとして読むこと。`--root` が 2 度渡されたときは、標準エラーに理由を出して何もしないこと |
| REQ-EXT-03 | 常時 | ccnavi は、標準出力と標準エラーを、コンソールの文字コードによらず UTF-8・改行 LF で書くこと。`--explain --json`・`--test --json`・`--test-samples --json`・`--lint --json`・`--suggest --json`・`--version --json` の JSON は ASCII に落として（ASCII でない字は `\u` で）書き、承認の JSON（`--agree … --json`）と残った指摘の JSON は UTF-8 の字のまま書くこと |
| REQ-EXT-04 | 事象 | `--version --json` を求められたとき、ccnavi は、設定もワークスペースも読まず、`schema`・`version`・`commit`・`built`・`compat`・`flags`・`formats` を返して 0 で終わること。`flags` は引数の定義から引き、足したフラグがそのまま並ぶこと |
| REQ-EXT-05 | 常時 | ccnavi は、実行ファイルと呼ぶ側（`.ccnavi/scripts/` の sh、VS Code 拡張、Chrome 拡張）の契約の版（互換の版）を持ち、実行ファイル（`src/ccnavi/entry/version.py` の `COMPAT`）・sh（`.ccnavi/scripts/ccnavi-common.sh` の `CCNAVI_COMPAT`）・VS Code 拡張（`src/core/version.ts` の `EXTENSION_COMPAT`）に同じ値を書くこと。呼ぶ側が頼るフラグや出力の形を、呼ぶ側を直さないと動かない形に変えたとき、データの形（承認済みの置き場に置くものの並び、待ち方の置き場、取り下げの条件など）を変えたとき、sh が古い実行ファイルの知らない副命令を呼ぶようになったときに 1 上げること。フラグや欄を足すだけでデータの形も変わらないなら上げないこと |
| REQ-EXT-06 | 常時 | ccnavi は、互換の版を、`src/ccnavi/entry/version.py` では行頭から行末までの 1 行 `COMPAT = <整数>`（`^COMPAT = N$`）で、`.ccnavi/scripts/ccnavi-common.sh` では行頭から始まる 1 行 `CCNAVI_COMPAT=<整数>`（`^CCNAVI_COMPAT=N$`）で書くこと。Chrome 拡張の組み立ては前者を、Chrome 拡張は統合先の後者を、それぞれ正規表現で読む |
| REQ-EXT-07 | 異常時 | 知らないオプションを渡されたなら、ccnavi は、何もせずに argparse の文言（`ccnavi: error: unrecognized arguments: <オプション>`）を標準エラーに出し、終了コード 1 で終わること |
| REQ-EXT-08 | 常時 | ccnavi は、使い方の誤りと読めない設定を、`ccnavi:` で始まる標準エラーの文と終了コード 1 で示すこと。終了コード 2 は hook の拒否と前の名前（`--approve`）の案内に、3 は確かめの「いいえ」（REQ-APV-13）に使い、1 と分けること |
| REQ-EXT-09 | 常時 | ccnavi は、診断ログをワークスペースルートの `logs/diag/<出どころ>.log` に、sh・Python・拡張（TS）で同じ行の形（`<時刻> <レベル 5 字> <出どころ>[<pid>] <本文> <key=value>…`）で書くこと。出すレベルは `CCNAVI_LOG_LEVEL`（読めない値は `INFO`）で決め、新しいファイルは 0600 で作り、`logs`・`logs/diag`・書き込み先がシンボリックリンクなら書かないこと。片付け（`--prune` とセッションの開始）は、拡張が書いたものを含めて `logs/diag/*.log` を同じしきい値でローテートし、消すこと |
| REQ-EXT-10 | 常時 | ccnavi は、時刻を次の形で書くこと。マーカーと記録の時刻は現地時刻とオフセット（`YYYY-MM-DDThh:mm:ss+hhmm`。`fsio.stamp`）、状態の履歴の `at` は UTC（`YYYY-MM-DDThh:mm:ssZ`）、診断ログは現地時刻とコロン付きのオフセット（`+hh:mm`）。時刻を固定する `fsio.clock` は `fsio.stamp` の形でない値を受けないこと |
| REQ-EXT-11 | 常時 | ccnavi は、置き場（提案 `wip/proposals/`、承認済みチケット `.ccnavi/approved/`、共通層 `.ccnavi/common/`、記録と state `logs/`、プロジェクト `projects/`、自身の層とプロジェクトの層 `.ccnavi/config/`）を既定のパスに固定し、環境変数でも上書き設定ファイルでも動かさないこと。別の場所を指せるのは診断のフラグだけとすること |

REQ-EXT-01 の探す順（拡張の設定 → `CCNAVI_BIN_PATH` → `dist/ccnavi/ccnavi` → 振り分けの sh → ソースなら `uv run python -m ccnavi`）は
VS Code 拡張が決める。ccnavi が約束するのは置き場と名前と `<os>-<arch>` の語で、語は `src/ccnavi/infra/platformtag.py`・
`scripts/ccnavi-setup.sh`・`.ccnavi/scripts/ccnavi-launcher.sh`・拡張の `src/core/locate.ts` で揃える。

REQ-EXT-03 の UTF-8 は、実行ファイルの入口（`hookio.rebind_streams`）が標準入出力を張り直して守る。VS Code 拡張は
念のため `PYTHONUTF8=1`・`PYTHONIOENCODING=utf-8` も渡す。

REQ-EXT-05 の 3 か所は `tests/sh/test_compat_skew.py` が突き合わせる。Chrome 拡張は互換の版を自分のソースに持たず、
組み立てのときに REQ-EXT-06 の行から埋め込む。版の JSON の形と、食い違ったときの直し方は README「[版の JSON](../../README.md#版の-json)」にある。

REQ-EXT-07 の文言は、`--version` を知らない古い実行ファイルを VS Code 拡張が見分けるのに使う。新しいフラグを知っているかは
`--version --json` の `flags` で見る（REQ-EXT-04）。

### 2.13.2 VS Code 拡張

VS Code 拡張は、実行ファイルをワークスペースルートを cwd にして起動し、引数の先頭に `--root <ワークスペースルート>` を付ける。
sh は `<ワークスペースルート>/.ccnavi/scripts/` のものを絶対パスで呼ぶ（Windows は Git Bash）。

| ID | 型 | 要求 |
|---|---|---|
| REQ-EXT-12 | 事象 | `--explain --json` を求められたとき、ccnavi は、`version`・`root`・`generated_at`・`settings`・`trees`・`layers`・`projects`・`problems`・`pending_approval`・`tickets`・`parents`・`archived` を必ず載せた JSON を返し、0 で終わること（REQ-DIA-06） |
| REQ-EXT-13 | 事象 | `--agree --preview --json [<絞り>...]` を求められたとき、ccnavi は、`version`・`batch`・`text`・`digest`・`rejected`・`problems` を載せた JSON を返し、承認済みチケットを置かないこと。承認の対象にしない提案があっても 0 で終わること（REQ-DIA-10） |
| REQ-EXT-14 | 事象 | `--agree --yes <識別子,…> --digest <値> --json [<絞り>...]` を求められたとき、ccnavi は、標準入力が端末であることを求めず、REQ-APV-09 の照合が通るときだけ承認済みチケットを置き、`version`・`approved`・`copies`・`lines`・`prompt` を返して 0 で終わること。照合が通らなければ何も置かずに `mismatch` を、置いている途中で止まれば `partial` を返し、どちらも終了コード 1 で終わること。状態の履歴の経路は `board` とすること |
| REQ-EXT-15 | 事象 | `ccnavi-review.sh decide <N> --preview` を求められたとき、ccnavi は、何も置かずに `version`・`parent`・`phase`・`mr`・`can_issue`・`threads`・`digest`（64 桁の 16 進）を返すこと。`decide <N> --choices <JSON> --digest <値>` を求められたときは、答えの JSON を標準出力の最後の行に書き、見せた指摘と今の指摘が違えば何も置かずに `mismatch` を返して 1 で終わること（REQ-TKT-18） |
| REQ-EXT-16 | 事象 | `--test <ツール> <対象> --json` を求められたとき、ccnavi は、操作を実行せずに判定を JSON で返し、常に 0 で終わること。`--test-samples <見本> --json` は常に 0、見本が読めなければ 1 で終わること。どちらも `--ticket-control disable --state "" --log ""` と、`--rules <パス>`・`--project-rules-file <名前\|self>=<パス>` の差し替えを受けること（REQ-DIA-02・REQ-DIA-09） |
| REQ-EXT-17 | 事象 | `--lint` か `--lint --json` を求められたとき、ccnavi は、error があれば 1、無ければ 0 で終わること。`--rules`・`--risk`・`--phases`・`--project-rules-file`・`--project-phases-file` の差し替えを、hook と同じ読み手で検証すること。`--json` の苦情は `{severity, where, detail}` で、置き場の苦情は `where` を `(projects)`・`(projects/<名前>)` にすること。`--flow <パス>` を足したときは、そのフローを `SubagentStart` と同じ読み手で読み、読めた中身を `flow` に載せ、苦情の場所を `(flow)` にすること（REQ-DIA-04・REQ-DIA-08） |
| REQ-EXT-18 | 事象 | `--suggest --json` を求められたとき、ccnavi は、記録からルールの候補を JSON で返し、何も書かずに 0 で終わること |
| REQ-EXT-19 | 事象 | `ccnavi c1 family <親>` を求められたとき、ccnavi は、何も書かずに、1 行目に `c1 1`、続けて `<鍵> <値>` の行（`target yes`・`target no`・`target stop` を含む）を返して 0 で終わること。識別子の形でない値には 1 で終わること |
| REQ-EXT-20 | 事象 | `sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-push-approved.sh [<親>...]` をユーザが打ったとき、ccnavi は、承認済みチケットの置き場の変更をコミットし、保護されたブランチでなければ push すること。親を並べたときは、その親子のチケットのうち取り込み済みのものだけを送ること（REQ-APV-10） |
| REQ-EXT-21 | 状態 | フェーズをレビューで止めている間も、ccnavi は、親のワークツリーで単体で打った `sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-review.sh confirm --phase <N>` を通すこと。sh のパスは realpath で解いたワークスペースルートから `/` 区切りで書き、空白やシェルの記号を含むときだけ引用する形（`settings.script_command`）を通すこと |
| REQ-EXT-22 | 常時 | ccnavi は、VS Code 拡張が直接読み書きするファイルを次の場所に置くこと。提案はワークスペース・プロジェクト・ワークツリーの `wip/proposals/`、承認済みチケットとマーカーは同じツリーの `.ccnavi/approved/`、閉じたチケットの退避は `logs/archive/`、共通層は `.ccnavi/common/` の `rules.yml`・`risks.yml`・`phases.yml`・`rule-samples.yml`。自身の層とプロジェクトの層のパスは `--explain --json` の `layers[]` で示すこと |
| REQ-EXT-23 | 常時 | ccnavi は、チケット制御の宣言 `CCNAVI_TICKET_CONTROL`（`.claude/settings.json` か `.claude/settings.local.json` の env に書き、Claude Code がプロセスに渡すもの）を `enable` と `disable` の 2 値で読み、読めない値は `enable` として扱うこと。`--explain --json` の `settings.ticket_control` には、解決した値を必ず `enable` か `disable` で載せること（REQ-DIA-07） |
| REQ-EXT-24 | 常時 | ccnavi は、層のファイル（`rules.yml`・`risks.yml`・`phases.yml`）を YAML 1.1（PyYAML の safe な読み手）で読み、頭の `version:` が読める書式の版（`--version --json` の `formats`）でなければ error にすること。拡張が書いたファイルも、hook と同じ読み手で読むこと |
| REQ-EXT-25 | 常時 | ccnavi は、子のフローを本物とする側のツリーの `.ccnavi/approved/flows/<子>.yml`（YAML、256 KiB まで）から読み、ファイルかツリーのルートからそこまでの途中がシンボリックリンクなら読まないこと。下書き `wip/proposals/flows/<子>.yml` には効力を持たせないこと。`ccnavi-push-approved.sh` は `flows/` の下の `.*.tmp` をコミットしないこと |

JSON の欄の形は README の「[ボードの JSON](../../README.md#ボードの-json)」「[承認の JSON](../../README.md#承認の-json)」
「[残った指摘の JSON](../../README.md#残った指摘の-json)」「[試験の JSON](../../README.md#試験の-json)」
「[lint の JSON](../../README.md#lint-の-json)」「[候補の JSON](../../README.md#候補の-json)」にある。どの JSON も形の版
（`version`。版の JSON は `schema`）を持ち、拡張は知らない版を読まない。ボード・承認・試験・候補の JSON は、拡張の
`test/fixtures/` の実例と同じ例で本体のテストが形を確かめる。

REQ-EXT-14 の経路をエージェントがシェルから打つ形は、組み込みの deny が止める（REQ-APV-07・REQ-APV-12）。
REQ-EXT-15 の答えが最後の行なのは、sh が実行ファイルの答えに投稿の結果（`issue_url`・`warning`）を足して最後に書き、
その前に投稿の行を出すことがあるため。

REQ-EXT-19 の `target` は、フローを保存した後に承認の push を送るかを VS Code 拡張が決めるのに使う。sh（C1）も同じ答えを読む。

### 2.13.3 Chrome 拡張

Chrome 拡張は、`src/ccnavi/` の `.py` を階層ごと、PyYAML の純 Python 版（`uv.lock` の版とハッシュ）と一緒に同梱し、
Pyodide のメモリ上のファイルシステムに組んだ仮のツリーを `root` にして、下の表の名前を呼ぶ。

| ID | 型 | 要求 |
|---|---|---|
| REQ-EXT-26 | 常時 | ccnavi は、実行時の依存を Python の標準ライブラリと PyYAML に限り、PyYAML の C 拡張が無いときは純 Python の読み手で同じ値を読むこと。ネットワークに出ないこと。ホストから取るもの（マージリクエストのスレッドとレビュー、変更の一覧、承認コミットの親の提案）は、呼び手から値で受け取ること |
| REQ-EXT-27 | 常時 | ccnavi は、下の表の名前（モジュール・関数・クラス・定数。下線で始まる `lint._SH_COMPAT` と `review._is_sha` を含む）を、表の引数と戻り値のまま置くこと。どれかの名前・引数・戻り値を変えるときは、互換の版を上げること（REQ-EXT-05） |
| REQ-EXT-28 | 事象 | `cli.run(stdin, stdout, stderr, argv)` を呼ばれたとき、ccnavi は、渡された標準入力・標準出力・標準エラーに書き、終了コードを返すこと（引数の読み違いの苦情だけは argparse がプロセスの標準エラーに書く）。判定の設定として読む環境変数は、名前が `CCNAVI_` で始まるものと `CLAUDE_PROJECT_DIR` に限ること |
| REQ-EXT-29 | 事象 | `core.judge_approval(snapshot, only, shown_ids, shown_digest)` を呼ばれたとき、ccnavi は、`--agree --preview --json` と同じ関数で承認の対象・画面の本文・ダイジェストを出すこと。見せた識別子（`shown_ids`）とダイジェスト（`shown_digest`、大文字小文字は問わない）が渡されたときは今のものと比べ、どちらかが違えば `mismatch`（`{expected, current, digest: {expected, current}}`）を返すこと。絞り（`only`）が通らないときは、絞らない一覧を今の一覧として比べること |
| REQ-EXT-30 | 常時 | ccnavi は、`core.plan`・`core.withdraw`・`core.confirm` で、書くものを値（`Changes`）で返すだけにし、ディスクにもホストにも書かないこと。`withdraw` と `confirm` は、通らなければ `changes` を `None` にして理由を `problems` で返すこと。`Changes.per_branch()` はブランチごとに `{op: create\|update\|delete, path, content}`（`path` はツリーからの相対、`content` はバイト列）を返すこと |
| REQ-EXT-31 | 常時 | ccnavi は、`Snapshot.stamp` が渡されたとき、書くものの時刻（マーカー・承認の記録・状態の履歴）をその時刻に固定し、同じ入力から手元と同じバイト列を出すこと。`core.withdraw` と `core.confirm` は、`Snapshot.actor`（`Actor(account, via, version)`）の経路・アカウント・拡張の版を状態の履歴に、アカウントと経路をレビュー済みのマーカーに書くこと。`core.plan` の状態の履歴の経路とアカウントは、呼び手が `history.session` で渡すこと（Chrome 拡張は経路に `chrome` を渡す） |
| REQ-EXT-32 | 常時 | ccnavi は、手元と同じ形に組んだ仮のツリー（ワークスペースルートに統合先、`.claude/worktrees/<識別子>` に親子のチケット、`projects/<名前>` にプロジェクトの統合先、取り込み状態は `logs/state/sync/self/` と `logs/state/sync/<名前>/`）を、手元と同じ関数で読むこと。ワークツリーの見分けとブランチ名は `.git` のファイル（`HEAD`・`gitdir:`）から読むこと。git を起こせない場（`sys.platform` が `emscripten`）では、承認の時刻のように git に頼る表示の値を分からないとして空にすること |

Chrome 拡張が呼ぶ名前。引数と戻り値は今の実装のとおり。

| モジュール | 名前 |
|---|---|
| `ccnavi.hook.core` | `Snapshot`・`Actor`・`Changes`（`per_branch()`・`lines`・`planned.stopped`）・`read_fs(conf, root, stamp="", actor=None)`・`judge_approval(snapshot, only=None, shown_ids=None, shown_digest=None)`（戻り値の `gathered.refused`・`batch`・`mismatch`・`identifiers`・`screen_text`・`digest`・`rejected`）・`plan(snapshot, verdict)`・`withdraw(snapshot, ids, prior_proposals, reason="")`・`withdrawable(snapshot, family)`・`confirm(snapshot, parent_id, phase_no, result, changed_since_request)`（`withdraw` と `confirm` の戻り値の `problems`・`changes`）・`reviewable(snapshot, parent_id)`・`requested_head(snapshot, parent_id, phase_no)`・`moved_on_host(snapshot, parent_id, phase_no, head, changed)` |
| `ccnavi.entry.cli` | `run(stdin, stdout, stderr, argv)` |
| `ccnavi.entry.lint` | `SH_COMPAT_FILE`・`_SH_COMPAT`（`CCNAVI_COMPAT` の行を読む正規表現） |
| `ccnavi.entry.version` | `VERSION`・`COMPAT` |
| `ccnavi.infra.fsio` | `clock(fixed)`・`stamp()` |
| `ccnavi.infra.settings` | `DEFAULT_TICKETS`・`DEFAULT_APPROVED`・`DEFAULT_STATE`・`DEFAULT_PROJECTS`・`DEFAULT_PROJECT_HOME`・`DEFAULT_BRANCH_PREFIXES`・`LAYER_CONFIG_DIR`・`LAYER_FILE_NAMES`・`load(root)`・`is_branch_prefix(word)`・`is_reserved_layer_name(name)` |
| `ccnavi.tickets.configsync` | `projected(conf, kind, content)` |
| `ccnavi.tickets.history` | `session(via, stderr, actor="", version="")`・`VIA_CHROME` |
| `ccnavi.tickets.review` | `Result.from_data(data)`（戻り値の `error`）・`_is_sha(value)` |
| `ccnavi.tickets.syncstate` | `SELF` |
| `ccnavi.tickets.ticket` | `TODO`・`DOING`・`DONE`・`STATES`・`ID_CHARS`・`RESERVED_BRANCH_IDS`・`DEFAULT_ISSUE_PREFIX`・`Ticket`（`ticket`・`title`・`body`・`is_child`・`predecessors`）・`parse(text)`・`load(path)`・`is_valid_id(text)`・`is_valid_name(text)`・`child_pattern()`・`branch_name(t)`・`branch_name_problems(t, integration="", serial=0, prefixes=…)`・`branch_problem(name)`・`issue_identifier(number, title="", project="", prefix="feature")` |

REQ-EXT-27 の規則は、Chrome 拡張の入口（`py/ccnavi_chrome.py`）が名前を直に引くため。改名すると、同梱の版を組み立て直すまで
Chrome 拡張だけが壊れる。上げた互換の版は統合先の `CCNAVI_COMPAT` に載り、古い Chrome 拡張は書く操作を出さなくなる（REQ-CHR-03）。
`tests/ticket/test_core.py` は、手元の承認・取り下げ・レビュー済みが書いたバイト列と、同じ入力で Chrome の入口が返す書くものとを突き合わせる。

REQ-EXT-29 のダイジェストが覆うものは README「[承認の JSON](../../README.md#承認の-json)」と同じ（承認画面の本文・判定が読んだ中身・
書き出す中身）。書いてよいのは、`batch` があり、`gathered.refused` も `mismatch` も無いときだけで、Chrome 拡張の入口は
そのときだけ `plan` を呼ぶ。書く先のブランチと互換の版で断るのは拡張の側（REQ-CHR-03・REQ-CHR-04）。

REQ-EXT-30 の `confirm` は、依頼の後にユーザが見るものが動いたかの説明（`changed_since_request`）を受ける。Chrome 拡張は
`moved_on_host` に compare API の変更の一覧を渡してこれを作り、一覧が打ち切られた・依頼時の先頭が祖先でないときは
`changed` を `None` にして「動いた」と数えさせる（`review.moved_since`）。
