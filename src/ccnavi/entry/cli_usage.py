"""`ccnavi --help` の本文。

`--help` だけなら話題の一覧（`OVERVIEW`）を出す。`ccnavi --docs --help` のように
話題のオプションかサブコマンドに `--help` を付けると、その話題の節（`TOPICS`）だけを出す。
全文は `--help --all`（`USAGE`）。読む量を、必要な話題の分だけに抑えるため。

cli から分けた。cli を読まない。
"""

from __future__ import annotations

_VERSION = """\
バージョンとビルド情報を確認するには、次のコマンドを実行してください。

    ccnavi --version [--json]

バージョン、ビルド元のコミット（ソースから実行した場合は "unknown"）、.ccnavi/scripts/
のスクリプトとVS Code拡張機能が自身の値と照合する互換バージョン、
受け付けるすべてのオプションを出力します。ペイロードも設定も読み込みません。--json の形式は
docs/11_requirements/json-shapes.md（「版の JSON」）に記載しています。
"""

_LINT = """\
ツールの実行可否を判定せずに、ルールファイルと設定を検証するには、次のコマンドを実行します。

    ccnavi --lint [--json]

ペイロードは読み込みません。ガードを無効にしうる事項をエラーまたは警告として報告し、
エラーを報告した場合は0以外の終了コードで終了します。
--json で出力される形式は docs/11_requirements/json-shapes.md（「lint
の JSON」）に記載しており、VS Code拡張機能がこれを読み込みます。

1つのレイヤーのフェーズ定義も同じ方法で渡します（self はワークスペース自身のレイヤーです。
共通レイヤーには --phases を使います）。

    ccnavi --lint --project-phases-file self=/tmp/phases.yml

1つのレイヤーのリスクの配点も同じ方法で渡します（--project-risk-file。共通レイヤーには --risk
を使います）。共通レイヤーと組み合わせて、統合後のレベルの境界、重複するid、各 script:
がそのレイヤー自身のgitプロジェクトルート配下に存在するかを検証します。

    ccnavi --lint --project-risk-file lib=/tmp/risks.yml

子チケット1件のフローも同様に検証します。
SubagentStartが使用するものと同じ読み込み処理・同じ検証を行います（--lint 専用です。VS
Code拡張機能は、フローを開く前や保存する前に、編集中の内容をこの方法で渡します）。

    ccnavi --lint --json --flow /tmp/flow.yml

共通レイヤーのオプション（--rules・--phases・--risk・--projects など）は
ccnavi --help scripts に書いてあります。
"""

_TEST = """\
プロジェクトのルールを保存前に試験または検証するには、
編集中のファイルをプロジェクト名とともに渡します。このオプションは --test、--test-expectations、
--lint、--explain 専用です。それ以外（フックの呼び出し、
チケットやレビューのサブコマンド）では無視され、その旨が標準エラー出力に表示されます。

    ccnavi --test Write projects/lib/src/a.py --project-rules-file lib=/tmp/rules.yml

ツールを実行せずにルールの判定結果を確認する、
またはファイル内の期待する判定をすべてルールに照らして実行するには、
次のコマンドを実行してください。

    ccnavi --test Bash "git push" [--json]
    ccnavi --test-expectations .ccnavi/config/common/rule-expectations.yml [--json]

どちらもフックと同じ判定処理を通ります。
--json で出力される形式は docs/11_requirements/json-shapes.md（「試験の
JSON」）に記載しており、VS Code拡張機能がこれを読み込みます。--test は、
ワークスペースルートから、権限モードなし・メインセッションとして呼び出しを試験します。
別の呼び出し条件で試験するには、次を追加します。

    --cwd <dir> --permission-mode <mode> --agent-id <id>

mode には auto、default、acceptEdits、plan、dontAsk、bypassPermissions のいずれかを指定します。
これ以外の名前や、ディレクトリではない --cwd は拒否されます。--permission-mode と --agent-id は
--test 専用です（--lint や --explain とも併用できません）。フラグは "--test <TOOL> <SUBJECT>"
の前後に置いてください。2つの語の間に置いた場合や、
2つの語の後に余分な語がある場合は拒否されます。
"""

_EXPLAIN = """\
チケット、その置き場、フェーズの印、レビューの保留を機械可読な形式で一覧表示するには、
次のコマンドを実行します（VS Codeのボード拡張機能がこれを読み込みます）。

    ccnavi --explain --json

ペイロードは読み込まず、リモートにも接続しません。
形式は docs/11_requirements/json-shapes.md（「ボードの
JSON」）に記載しています。
"""

_DOCS = """\
本文中の行ではなく、ドキュメントの性質でマークダウン文書を探すには、
ワークスペースとそのプロジェクトのfrontmatter索引を検索します（パスはワークスペースルートからの相対パスです。
例: projects/lib/docs/x）。

    ccnavi --docs [--type T] [--tag T] [--keyword K] [--path SUB] [--text SUB]
                  [--since DATE] [--until DATE] [--sort path|mtime|type|title]
                  [-r] [--limit N] [--format table|path|detail|json|jsonl|count]
                  [--no-refresh]

同じフィルターを2回指定した場合はOR、異なるフィルターはANDで評価され、
大文字・小文字は区別されません。--type、--tag、--keyword は値の全体に一致します。--path は .md
を除いたパスの一部に一致します。--text はパス、更新日時、
またはfrontmatterのいずれかの値の一部に一致します。文字列はNFCで比較されます。--since と --until
には YYYY-MM-DD[THH[:MM[:SS]]] を指定でき、--until
は指定した単位の末尾までを範囲に含めます（日付は23:59:59まで、THHは:59:59まで、
THH:MMは:59まで）。一致するものがなくても終了コードは0です。検索の前に、ディレクトリごとの
ccnavi-index.jsonl を最新の状態に更新します。更新するのは、gitが ccnavi-index.jsonl
を無視しており、かつそのファイルがccnavi自身のものである場所だけです。
何も無視していないツリーでは、先に .gitignore へ1行を追加します。それに失敗した場合のみ、
そのツリーは対象から外れ、標準エラー出力に名前が表示されます。
初回は全マークダウンファイルの先頭部分を読み込み、
2回目以降は更新日時が変わったファイルのみを読み込みます。--no-refresh を指定すると、
既存の索引をそのまま再利用します。--json は --format json と同じです。各行の形式は
docs/21_design/diagnostics.md（10.1）に記載しています。これらのフィルターは --docs 専用です。
ほかの実行で指定すると、終了コード1で停止します。他の実行用のフラグを --docs
に指定した場合も同様に停止します。
"""

_TEMPLATES = """\
作業・レビュー計画のテンプレートを一覧・検索・参照するには、
次のコマンドを実行します（計画と子フローを1ファイルにまとめたもので、
.ccnavi/config/common/templates/ と .ccnavi/config/project/templates/ に置かれます。
読み取り専用で、何も書き込みません）。

    ccnavi --templates [--layer self|PROJECT] [--keyword K]... [--json]
    ccnavi --templates --show NAME [--layer self|PROJECT] [--json]
    ccnavi --templates --check-file PATH [--layer self|PROJECT|common] [--json]

一覧は、共通レイヤーと1つのレイヤー（--layer を指定しない場合は self）を合わせたものです。
--keyword は名前、説明、または使う場面の一部に一致します（2回指定した場合はどちらかに一致）。
--show は、1件のテンプレートを計画・フロー・検査結果とともに表示します。
--check-file は、任意のパスのテンプレートを、そのレイヤーのフェーズ定義で検査します
（何も書き込みません。保存前の確かめ用で、--show・--draft・--keyword とは併用できません）。
両方のレイヤーに同じ名前で内容の異なるテンプレートがある場合はエラーとなり、
そのレイヤー側のテンプレートは使用されません。--json は、ボード画面が描画する完全な応答で、
docs/21_design/tickets/templates.mdに記載しています。--keyword は --docs
と共通で、それ以外では拒否されます。--docs のその他のフィルターはここでは拒否されます。--lint
でも同じ検査を報告します。

テンプレートは2段階で収集します。エージェントは --draft を実行できます。
完了済みの親チケット（done/ にあるもの）の計画と子フローを、親のツリーの
wip/handoff/<取り込み先の相対パス> に下書きとしてコピーします（--to の既定は project で、
取り込み先は .ccnavi/config/project/templates/NAME.yml。--to common なら
.ccnavi/config/common/templates/NAME.yml、--layer PROJECT なら
projects/PROJECT/.ccnavi/config/project/templates/NAME.yml）。下書きは効力を持たず、
上書きもされません。取り込み先に同名のテンプレートが既にある場合も作りません。

    ccnavi --templates --draft PARENT [--name NAME] [--to common|project] [--layer NAME] [--json]

下書きの取り込みは、ほかの保護対象（rules.yml・docs・hook など）と同じく、ユーザが
ccnavi-user-handoff.sh で行います（見るだけの形も含めてユーザ専用です。実行ファイルは内部で
--handoff に読み替えます。--handoff を直接渡すと断ります。sh が付ける入口の印（--entry sh）が
ない user handoff も断ります）。テンプレートの
改名・移動・削除は、ユーザがファイルを直接操作します。

    sh <workspace root>/.ccnavi/scripts/ccnavi-user-handoff.sh [PATH...] [--json]
        （見るだけ。PATH はツリーからの相対パスで、省くと下書き全部）
    sh <workspace root>/.ccnavi/scripts/ccnavi-user-handoff.sh [PATH...] --yes --expect HASH
        （取り込みます。ボードは実行ファイルを --entry board --via board で呼びます）

共通レイヤー（ワークスペースの .ccnavi/config/common/）のミラー（各プロジェクトの
.ccnavi/config/common/）がずれていないかの確認と、ずれの同期は、ユーザが
ccnavi-user-mirror.sh で行います（見るだけの形も含めてユーザ専用です。実行ファイルは内部で
--mirror に読み替えます。sh が付ける入口の印（--entry sh）がない user mirror は断ります）。同期は各プロジェクトのワークツリーに書くだけで、コミットはしません。

    sh <workspace root>/.ccnavi/scripts/ccnavi-user-mirror.sh [PROJECT...] [--json]
        （見るだけ。PROJECT を省くと全プロジェクト。差分と --expect の値を出します）
    sh <workspace root>/.ccnavi/scripts/ccnavi-user-mirror.sh [PROJECT...] --yes --expect HASH
        （差分のあるプロジェクトを揃えます。--expect が今の差分と違うときは何も書きません。
        ボードは実行ファイルを --entry board --via board で呼びます）

収集済みのテンプレートは、親の提案を開始するときに使用されます。
エージェントも実行できます（フローの下書きも効力を持ちません）。

    ccnavi ticket new ID --title TITLE --template NAME [--layer self|PROJECT]
                         [--with-feedback] --issue <N|owner/repo#N> --mr <N>

ID という名前のワークツリーに wip/proposals/todo/ID.md を書き込みます。plan:
はテンプレートから取得し（after は、計画内の位置から提案側の番号へ振り直されます）、続けて
phases: を補完します。feedback: は --with-feedback
を指定した場合のみ書き込まれます（フィードバック計画は、メイン計画の番号の続きになります）。
指定しない場合は、テンプレートにフィードバック計画があることが出力に表示されます。
フィードバック計画は、メイン計画の最後のレビューの後に、改訂によって1回だけ追加されます。
最初の提案に書いてしまうと、承認後にメイン計画を改訂できなくなるためです。--with-feedback は、
ticket new --template 以外で指定すると拒否されます。n番目の計画のステップのフローは
wip/proposals/flows/ID-NN-01.yml に出力されます。これは下書きで、
ユーザがフローエディターで取り込むまで効力を持ちません。NAME は、
共通レイヤーと1つのレイヤー（--layer を指定しない場合は、
ワークツリー自身のレイヤー）から検索されます。次の場合は、何も書き込まずに停止します。
テンプレートが存在しない、または使用できない（エラーがある、または重複により除外された）場合、
phases.yml にないタイプがある場合、--plan も指定された場合、
提案またはフローの下書きがすでに存在する場合。提案は、通常の承認を経る必要があります。
"""

_LEARNINGS = """\
学びの蓄積の wiki（docs/wiki/）のパターンごとの状態と観測元の数を一覧するには、
次のコマンドを実行します（読み取り専用で、何も書き込みません）。

    ccnavi --learnings [--json]

--root で指したツリー（省くと、いまのツリー）の docs/wiki/patterns/ と docs/wiki/impact/ を読み、
パターンごとの状態（提案中・候補・決着）と観測元の数、候補の件数と target ごとの内訳、
観測元の数が閾値以上かどうか、承認待ちの提案の一覧を出します。閾値は環境変数
CCNAVI_LEARNING_SOURCES（既定 3）です。frontmatter が壊れたパターンと採否の記録は、
ファイル名を挙げて数えません（--lint も同じファイルを名指しします）。
--json は機械が読む形です。--docs・--lint など、ほかの経路のフラグとは併用できません。
"""

_LOGS = """\
判定ログをローテーションし、古いログや終了したセッションの状態データを削除するには、
次のコマンドを実行してください（SessionStartでも自動的に同じ処理が行われます）。

    sh <workspace root>/.ccnavi/scripts/ccnavi-user-prune.sh
    ccnavi --prune --preview

ユーザがターミナルから ccnavi-user-prune.sh を打ちます（実行ファイルは内部で --prune に読み替えます。sh が付ける入口の印（--entry sh）がない user prune は断ります。
--prune を user の外で渡すと断ります）。--preview を指定すると、移動予定の項目のみを表示し、
実際の変更は行いません（エージェントも実行できます）。

判定ログ（logs/decisions.jsonl と、ローテーション済みの
decisions.*.jsonl）からルールの候補を作成するには、次のコマンドを実行します。

    ccnavi --suggest [--json]

出力されるのは deny/ask の候補のみです。どのルールにも一致せず、
繰り返しユーザに判断が委ねられた呼び出しと、同じ呼び出しを繰り返し止めた deny（その message
を見直してください）が対象です。各候補は --lint および --test-expectations
と同じ検査を通過しています。通過しなかったものは件数のみ表示され、候補からは除外されます。
何も書き込みません。
--json で出力される形式は docs/11_requirements/json-shapes.md（「候補の JSON」）に記載しており、VS
Code拡張機能がこれを読み込みます。
"""

_AGREE = """\
承認待ちのチケットを確認し、提案された作業範囲を承認するには、次のコマンドを実行してください。

    sh <workspace root>/.ccnavi/scripts/ccnavi-user-agree.sh
    sh <workspace root>/.ccnavi/scripts/ccnavi-user-agree.sh i0002 i0002-01-01
        （指定したチケットのみ。例: 絞り込んだボードから実行する場合。
         idはすべてのフラグの後ろに指定します）

承認はユーザ専用で、ユーザがターミナルから ccnavi-user-agree.sh を打ちます（実行ファイルは内部で
--agree に読み替えます。sh が付ける入口の印（--entry sh）がない user agree は断ります。承認のあと、
承認済みチケットのコミットと push まで行います。--agree を user の外で渡すと断ります。見るだけの --agree --preview は
エージェントも実行できます）。

すべてのワークツリーの wip/proposals/todo/ をスキャンし、各チケットが書き込み可能にする範囲と、
人によるレビューが必要かどうかを表示します。そのうえで、承認したチケットを
.ccnavi/approved/doing/ に移動します。呼び出しの判定で参照されるのはこの場所だけなので、
提案を書いただけで書き込み可能な範囲が広がることはありません。id
は一括処理の対象を絞り込むだけです。承認待ちでない id や、
承認待ちの親（またはその親の承認待ちの改訂）がない状態で指定された子は、何も承認されません。

エージェントは、ユーザに承認を求める前に、
書いたばかりの提案が承認可能な状態であることを検証します。

    ccnavi --agree --preview --verify [--json] [<id>...]

確認だけを行い、ターミナルも不要です。終了コード0は「可」を表します。指定したすべてのチケット（id
を指定しない場合は承認待ちのすべてのチケット）が、現在の状態のまま一括処理の対象に含まれるため、
ユーザに承認を求められます。終了コード3は「不可」を表します。承認待ちでない id、
承認待ちのチケットが1件もない場合、または承認で除外される提案がある場合です。
理由はチケットごとに表示されます。終了コード1は、ほかの実行と同じく使い方や設定のエラーであり、
判定結果ではありません。そのため、綴りの誤りが、
提案を修正すべきという判定として読み取られることはありません。

計画を持つ親チケットは、その計画が使用するフェーズタイプのコピーを `phases:`
として保持します（タイプidをキーとし、フィールドは phases.yml と同じです）。--agree は、
読み込んだ phases.yml と異なるコピーを拒否します。
エージェントは次のコマンドでコピーを書き込みます。

    ccnavi --plan-order <parent> --fill-phases

承認待ちの親の提案の `phases:` の値を、
その計画とフィードバックが使用するタイプに置き換えます（phases.yml から取得します。改訂の場合、
承認済みの子がすでにある番号が使用するタイプは、承認済みチケットから取得します）。
使用していないタイプは削除されます。それ以外のバイトは変更されず、
ほかのフィールドの読み取り結果が変わる場合は何も書き込まれません。これは --agree
とは別の独立したフラグなので、エージェントも実行できます。承認後の判定は、このコピーを読み込み、
phases.yml は読み込みません。

承認待ちの親の計画の順序も、同じフラグで設定します。VS
Codeのワークフロータブがこれを実行します。エージェントも実行できます。

    ccnavi --plan-order <parent> --json [--order <JSON>]
        （読み取り専用。--order を指定すると、描画された線に基づいて番号を振り直した計画を返します）
    ccnavi --plan-order <parent> --order <JSON> --expect <sha> --write [--json]
        （番号を振り直した計画を提案に書き戻します）

--order は、各ステップの直接の先行を、現在の提案に書かれている番号で指定します: {"plan": {"1": [],
"2": [3], "3": [1]}, "feedback": {...}}。渡された部分は、
すべての番号の先行をそこから取得します（省略された番号は先行なしです）。渡されなかった部分は、
提案のままです。ステップは、先行がすべて配置済みのもののうち現在の番号が最小のものから順に、
トポロジカル順に番号が振り直されます。`after` は新しい番号に追従し、`review: defer`
はステップとともに移動します。改訂の場合、承認済みの子がすでにある番号はそのまま残り、
それ以外のステップは空いている番号に割り当てられます。循環を生む線、
固定された番号の先行を変更する線、固定された番号を避けて詰められない線は拒否され（`refused`）、
番号は一切振り直されません。

応答には次のステップが含まれます。`plans`（部分ごとに1つ。各ステップの `number` と `from`、
新しい番号での `after`、`proposed`、`current`、`loose`、`ready`、`problems`）、
`source_sha`（親の提案、承認待ちの子、および `predecessors`
でそれらを指定している承認待ちの子）、順序の `problems`、`loose`、`refused`、
`revision_problems`、`children`（付け替え対象の承認待ちの子。`ticket` -> `new_ticket`、`phase`
-> `new_phase`、`predecessors` -> `new_predecessors`）、`child_problems`、`mismatched`（`phase:`
が計画にない承認待ちの子）。

--write は、親の提案の `plan:` と `feedback:` の値を書き換えます。
番号が移動する承認待ちの子（todo/）は、そのidの新しい番号（`<parent>-<phase>-<seq>`。
連番は維持されます）、ファイル名、`ticket:`、`phase:` が更新され、`predecessors`
でその子を指定している承認待ちの子も修正されます。承認済みの子には一切触れません。
子を先に書き込み、親を最後に書き込みます。失敗した場合は、書き込んだものをすべて元に戻します。
次の場合は何も書き込みません。--expect が現在の `source_sha` と異なる場合、
拒否された線がある場合、番号を振り直した計画に順序エラーがある、
または改訂のロックに違反する場合、ほかのフィールドの読み取り結果が変わる場合、新しい子のidが、
残るファイルやチケットと衝突する場合、承認済み側（承認済みチケット、記録、マーカー、フロー、
ワークツリー）が、変更されるidを参照している場合。承認は行いません。
書き換えた提案を承認するには、提案ファイルのバイト列が変わるため、新しいプレビューが必要です。

次の2つは「不可」ではありません。--agree もこれらを除外しないためです。
親またはフェーズタイプの範囲を超える範囲（承認後も、そこへの書き込みは拒否されたままです）と、
読み取れない提案（スキャンは、id による絞り込みの前にすべてのワークツリーを対象とするため、
別のセッションの下書きがあると「不可」になります）です。どちらも表示されます。
承認待ちが何もないという状態だけは、両者で扱いが異なります。--agree は、
処理するものがない成功として扱い、検証は「不可」として扱います。

VS Codeのボード拡張機能は、オーバーレイから承認します。

    ccnavi --agree --preview --json [<id>...]  （一括処理の内容を表示します。何も配置しません）
    ccnavi --entry board user agree --yes <id,id,...> --digest <hex> --json [<id>...]
        （表示された内容そのものを承認します。--digest はプレビューの `digest`
         （提案の識別子の一覧と各提案ファイルのバイト列全体のダイジェスト）で、
         末尾のidは同じ絞り込みです）

--yes はターミナルを必要としません。表示後に、一括処理の対象の識別子または提案ファイルの
バイト列（本文・改行・BOM を含む）が変更された場合や、--digest が指定されていない場合は、
「内容が更新された」ことを終了コード 1 と `mismatch`（--json のとき）で返します。
呼び出し側は最新を表示し直し、もう一度承認してもらいます。
ターミナルでの user agree も、y の入力の後にこの照合をして、違えば最新を表示し直して確認し直します。
フックは、承認をモデルに通知しません。拡張機能は、--yes が出力するものと同じテキストを `prompt`
でモデルに渡します。それ以外の場合、親エージェントは `ccnavi ticket status [<parent>]`
で確認します。
"""

_TICKET = """\
親エージェントは、.ccnavi/scripts/ のスクリプトを通じて、チケットの状態を移動し、
レビューを依頼します。スクリプトは次を呼び出します。

    ccnavi ticket start|finish|cancel <id> [--reason <why>]
    ccnavi ticket consolidate <親>
        （親の finish が「判定されていない気づきがある」と止めたときに打つ。処理する統合の子
         <親>-00-NN を doing/ に作る。処理すべきものが無いとき、disable のときは作らない）
        （start は、基点を .ccnavi/approved/doing/<id>.md に書き込みます。finish は、
         フェーズがレビュー対象の場合は wip/proposals/review/ に、それ以外は
         .ccnavi/approved/done/ に移動します。cancel は .ccnavi/approved/done/ に移動します）
    ccnavi ticket propose <id>
        （親子の承認待ちの提案 wip/proposals/todo/ と子のフローの下書き wip/proposals/flows/ を、
         書いたパスの一覧に載せます。載せるだけで、コミットと push は
         ccnavi-ticket.sh propose が write-and-push の手順で行います）
    ccnavi ticket record-risk <child> <factor> yes|no --reason <why>   （定性的なリスク）
    ccnavi ticket learn [<id>] [--note <text> [--target <kind>] [--signal <kind>]]
        （学習記録の雛形と判定ログのダイジェストを wip/handoff/learnings/<id>.* に書き込みます。
         --note を指定すると学習内容を1件追記します。チケット外の場合は
         docs/wiki/inbox/ に書き込みます。
         <id> はオプションの前に指定します）
    ccnavi ticket new <id> --title <title> --plan <type,type,...> [--scope <glob>]...
                           --issue <N|owner/repo#N> --mr <N>
        （<id> という名前のワークツリーの wip/proposals/todo/<id>.md に、親の提案の雛形を
         書き込みます。そのワークツリーの中で実行してください。選択したフェーズの範囲を
         phases.yml から集めて allow に入れ、--scope の glob（この作業が触れるファイル。
         ワークツリーのルートからの相対パス。複数指定可）を追加し、計画を直線状につなぎ、
         phases: を補完します。本文の見出しは、目的・範囲・対象外・完了の確かめ方・計画の理由
         の順で、rationale と各見出しの下に空欄の目印「（ここに〜を書く）」が入ります。目印が
         残った新規の提案は、承認と --lint が止めるので書き換えてください。
         quick-work のような scope: inherit のタイプは、それ自体では
         何も集めないため、--scope を指定してください。--issue と --mr は必須で、
         ccnavi-start.sh が「必須」の行で出す値（issue は番号か owner/repo#番号。MR は親のブランチの
         マージリクエストの番号）。親の提案の issue: と mr: に書き、足りなければ停止
         します。phases.yml が
         ない場合、子のidを指定した場合、または allow が空になる場合は停止します。
         allow の項が計画のどのフェーズの範囲とも重ならないときは、停止せず
         「承認しても書けない」と警告します
         （scope: inherit のタイプが計画にあれば警告しません）。
         ccnavi を直接実行します（sh のラッパーは使いません））
    ccnavi ticket new <id> --title <title> --template <name> [--layer <name>]
                           [--with-feedback] --issue <N|owner/repo#N> --mr <N>
        （上と同じですが、計画と子フローの下書きは --plan の代わりにテンプレートから取得します。
         フィードバック計画は --with-feedback を指定した場合のみ書き込まれます。
         上記の --templates の節を参照してください）
    ccnavi ticket status [<parent>]
        （読み取り専用。置き場、承認時刻、着手済みかどうか、未コミット・未pushの有無
         （ローカルのリモート追跡参照に基づく）、停止している理由、次の手順を表示します。
         サブエージェントも実行できます）
    ccnavi ticket next-id [<prefix> <slug>]
        （読み取り専用。直接作業のワークツリー名に付ける次の連番、またはそれから組み立てた
         識別子 <prefix>-<number>-<slug> を表示します。サブエージェントも実行できます。
         チケット制御が無効でも利用できます。手元のブランチとワークツリーの名前も数え、
         数えきれなかったときは番号を出さず、終了コード 2 で理由を標準エラーへ出します）
"""

_SCRIPTS = """\
共通レイヤーの3つのファイルは --rules、--phases、--risk で、レイヤーを探す場所は --projects と
--project-home で変更します。この5つはいずれも同じ制限が適用され、診断専用で、
それ以外の実行では無視されます。

--root と --cwd は、この実行がどこで行われているかを指定します。.ccnavi/scripts/
のラッパースクリプトがこれらを判定して渡すため、各1回のみ指定できます。2回目の指定は、
上書きとしては扱われず拒否されます。

設定の値を上書きするフラグもあります。--mode、--ticket-control、--restore-if-deny、
--guard-core-files、--guard-ticket-approval は環境変数と同じ値を取ります。--log、--state、
--approved、--tickets は記録と置き場の場所を動かし、上の5つと同じく診断専用です。

.ccnavi/scripts/ のスクリプトが付けるフラグは次のとおりです。

    --record-writes <file> [--record-tree <dir>]
        （この実行で書いた・消したパスを、logs/state/write-and-push/ の下の <file> に1行1つで
         書き出します。--record-tree を渡すと一覧はそのツリーからの相対になり、
         置き場の外に書いたらエラーになります。write-and-push と ccnavi-user-agree.sh が使います）
    --placed-out <file>
        （--agree 専用。実際に書けた承認済みチケットのパスなどを JSON で書き出します）
    --choose-out <file>
        （decide の前半。選択とダイジェストを書くだけです）
    --accept-plan <file>
        （decide の --yes と --close-early 用。受け入れる指摘と、スレッドに残す返信の
         本文を書くだけです。スクリプトが返信と解決を済ませてから、
         置く呼び出しを打ちます）
    --accept-check <file>
        （--close-early 用。--accept-plan で書いた計画のあとで、未解決の指摘が変わっていないかを
         確かめるだけです）
    --accept-done <file>
        （decide の --yes と --close-early 用。--accept-plan で書いた計画を読み、返信と解決が済んだ
         結果を確かめて置きます。--close-early は y/N を聞き直しません）
    --no-issue
        （--close-early 用。受け入れた指摘への返信に、issue に回すと書かないようにします）
    --via terminal|board
        （decide が書くレビュー済みの印と、user handoff --yes の経路。handoff は board だけ）

    ccnavi review prepare   --cwd <dir> --phase N --body-file <path>
    ccnavi review requested --cwd <dir> --phase N --result <json>
    ccnavi review confirm   --cwd <dir> --phase N --result <json> [--actor <account>]
        （手で done/ に動かして共有済みの子は、移動の前の未解決の指摘を受け入れ台帳 accepted.json に
         版つきで足して確定します。移動の後に付いた指摘が残れば確定しません）
    ccnavi review ready     --cwd <dir> --result <json>
        （標準出力: コメントの下書き、"tree <dir>"、続いて "wip-from <sha>" と、
         wip/ から削除した追跡済みファイルごとに "wip <path>" を1行ずつ出力します）
    ccnavi worktree drop <name> [--cwd <dir>]
        （ビルド成果物を削除し、.claude/worktrees/<name> に対して --force なしで
         `git worktree remove` を実行します。ブランチは残します。--cwd がその中にある場合、
         または未コミットの変更がある場合は何もせず、その旨を表示します）
    ccnavi worktree tidy <parent> [--phase N] [--cwd <dir>]
        （クローズ済み（完了または取り消し）の子のワークツリーを削除します。--phase N を
         指定した場合はそのフェーズのみが対象です。doing/ にある子のワークツリーだけは残ります。
         子のidは、チケットから読み取った親を指すものとして扱われます）
    ccnavi worktree remaining <parent> [--cwd <dir>]
        （そのファミリーのワークツリーのうち、まだ残っているものを一覧表示します。削除はしません。
         対象は、チケットを保持する親と異なる名前のワークツリーと、クローズ済みの子のワークツリーで
         未保存の下書きがあるものです。該当がなければ何も表示しません）
    ccnavi worktree retire --abandon <parent>
        （放棄した親（MR をマージせずに閉じた親）の片付けの記録 state/retire/<parent>.json を
         消します。親のワークツリーが残っている間は断ります。ユーザがターミナルで打ちます。
         エージェントからは打てません）
    ccnavi worktree retire <parent> [--cwd <dir>]
        （ready が Draft を外した後に、親を閉じる片付けを行います。閉じた子のワークツリーと、
         子の手元のブランチ（合流した子: 先頭が merged_sha と同じで、それが push 済みの親に含まれる
         場合のみ）、親のワークツリーを削除します。取り消した子のブランチは一覧表示のみで、
         ユーザが確認した後に、ユーザが ! を付けて
         ccnavi-user-clean-branch.sh <name> --confirmed を打って削除します。
         ccnavi-review.sh ready が中で呼びます。エージェントからは打てません。
         未コミットの変更や、git が無視しているファイルがあるワークツリーは理由を出して残します）
    ccnavi worktree branch <name> [confirmed]
        （retire が記録した子のブランチだけを、条件を確かめ直して削除します。記録の子が
         チケットの parent: でもその親の子であることを確かめます。取り消した子は confirmed が
         要ります。ユーザが ! を付けて打つコマンドで、エージェントからは打てません）
    ccnavi sync tidy <parent> <repo> <branch> <sha>
        （ccnavi-sync.sh 用。親の MR がマージ済みと分かった後に、親のワークツリーと
         手元のブランチを、<branch> が親のブランチで、手元の先頭が push 済みの先頭
         （origin/<branch>）と <sha>（MR の先頭）の両方に含まれるときだけ削除します。
         エージェントからは打てません。
         1 行ずつ報告し、最後に
         "record yes|no"（片付けが済んだか）を出力します。済んだときは retire/<parent>.json も
         消します。ネットワークには出ません）
    ccnavi worktree name <name>
        （直接作業するワークツリーの名前が <prefix>-<number>-<slug> の形式でない場合に、
         "warn: ..." を1行表示します。形式どおりなら何も表示しません。拒否はしません）
    ccnavi sync paths
        （.ccnavi/scripts/ccnavi-sync.sh 用。1行に "<key> <value>" を1組ずつ出力します。
         対象は、承認済みと提案の置き場、ccnaviのディレクトリです。統合先ブランチは返しません
         （sh が各リポジトリの origin/HEAD で決めます））
    ccnavi sync check <parent> [<repo>]
        （ccnavi-sync.sh が <parent> を取り込んだ後に使用します。ファミリーの承認済みチケットと
         その権限を再判定します。"check 1" の行に続いて、"error <detail>" か "warn <detail>" を
         1行ずつ出力します（warn には、手元のレイヤーが取得済みの origin/<統合先> のレイヤーと
         違うことを含みます。git show でローカルの参照を読むだけで、ネットワークには出ません）。
         error がある場合は終了コード1になります）
    ccnavi sync retired
        （ccnavi-sync.sh 用。"retired 1" の行に続いて、親を閉じた後の片付けの列挙元
         （state/retire/<parent>.json）を "retired <parent> <self|project> <mr|-> <branch|->" の
         形で1行ずつ出力します。読めない記録や mr の無い記録は "incomplete <parent> <理由>"
         です。ネットワークにも git にも触れません）
    ccnavi tree parent <tree> <name>
        （スクリプト用。"tree 1" の行に続いて、"parent yes|no|unreadable"（そのツリーが
         親のチケットまたは提案 <name> を保持しているか）、"approved yes|no|unreadable"
         （承認済みの親チケットがあるか。提案は数えません）、"requested yes|no"
         （.requested のマーカーがあるか）、"archived yes|no"（手元の退避に親があるか）、
         "mr <番号>|-"（親チケットの mr:）を出力します。スクリプト自身は
         チケットのYAMLを読み込みません）
    ccnavi tree bound <tree> <name>
        （ccnavi-git.sh integrate 用。"tree 1" の行に続いて、"bound yes|no"（その名前の
         ワークツリーがチケットに結び付くか。結び付くと integrate は断ります）、結び付くなら
         "why <理由>" を出力します。Stop の促しと同じ判定です）
    ccnavi tree base-bound <main-checkout> <branch>
        （ccnavi-worktree.sh 用。"tree 1" の行に続いて、"bound yes|no|unknown"（取り込み先の
         ブランチがチケットに結び付くか。yes と unknown は切るのを断ります）、yes なら
         "why <理由>" を出力します。結び付きは取り込み先のリポジトリだけを見ます）
    ccnavi tree guarded <main-checkout> <path>...
        （ccnavi-git.sh integrate 用。"tree 1" の行に続いて、"guarded yes|no"（パスが自己防衛の
         守る対象にヒットするか。
         ヒットすると integrate-done はターミナルから打つよう案内します）、
         ヒットするパスごとに "path <パス>" を出力します。
         パスはユーザのチェックアウトからの相対です）
    ccnavi tree handoff-pending <tree>
        （ccnavi-git.sh integrate 用。"tree 1" の行に続いて、"pending <件数>"（取り込んでいない
         wip/handoff/ の下書きの数）と、1 件ごとに "path <パス>" を出力します）
    ccnavi worktree id <branch>
        （スクリプト用。"id <識別子>"（ブランチ名の / を - に置き換えたもの）に続いて、
         親に採用できなければ "problem <理由>"（58文字以内・末尾が -2桁でない・字の種類・NFC）、
         直接作業や子のワークツリーの名前に指定できなければ
         "direct <理由>"（64文字以内・字の種類・NFC）、
         子の識別子の形なら "child yes" を出力します）
    ccnavi worktree issue-id <number> <prefix> [<project>] --title <title>
        （ccnavi-start.sh 用。issue から新しい親のブランチ名（= 識別子）を作り、"id <名前>" を
         出力します。Chrome 拡張の「始める」と同じ作り方です。題が長くて slug を
         語の区切りで切ったときは、続けて "long yes" を出力します）
    ccnavi write-and-push family <id>
        （write-and-push 用。"write-and-push 1" の行に続いて、
        親子のチケット・リポジトリ・write-and-push の対象か
         （target yes|no|stop）・親のワークツリーとブランチなどを1行ずつ出力します。読み取り専用）
    ccnavi write-and-push sort <parent> [<rev>]
        （write-and-push 用。
        置き場の変更をコミットしてよいものと止めるものに分けて1行ずつ出力します）
    ccnavi write-and-push merge <child>
        （write-and-push 用。finish が子を親のブランチへ合流する前の検査）
    ccnavi write-and-push merge-blame <child> <path>...
        （write-and-push 用。合流で衝突したパスを持ち込んだ、先に合流した別の子を出力します）
    ccnavi branches issue|mr <number> [--result <json>] [--json]
        （ccnavi-branches.sh 用。issue・MR に紐づくブランチの候補を出力します。読み取り専用）

ccnavi自身は、リモートに接続しません。スクリプトがマージリクエストとそのスレッド、
レビューを取得し、--result <json> として渡します。

未解決のレビュースレッドは、人が親のワークツリーのターミナルから、次のコマンドで受け入れます
（VS Code のボードの「決める」も、同じ判定を通ります）。

    sh <workspace root>/.ccnavi/scripts/ccnavi-user-decide.sh N

（スクリプトはワークスペースにのみ存在するため、プロジェクトから作成したワークツリーからは、
相対パスで到達できません）

このコマンドはスレッドを取得し、次を実行します。

    ccnavi --entry sh user reviewed N --accept-unresolved --result <json> --cwd <parent worktree>
        [--actor=<account> --via=terminal|board]
        （スクリプトは、読み取れる場合はトークンの所有者を渡します。レビュー済みの印には、
         アカウントと実行方法が記録されます。渡さなかった場合、印はこれまでどおりです）

実行ファイルは user reviewed を内部で --reviewed に読み替えます。--reviewed を user の外で
渡すと断ります。見るだけの --reviewed N --accept-unresolved --preview は
ccnavi-review.sh decide N --preview が呼び、エージェントも実行できます。

人が親を早期にクローズする（「今のところ十分」とする）には、次を実行します。

    sh <workspace root>/.ccnavi/scripts/ccnavi-user-close-early.sh --reason <why>

`ccnavi --entry sh user close-early --reason <why> --result <json>` を実行した後、
マージリクエストのDraftを解除し、残った項目を新しいissueとして登録します。
"""

# (話題, 引き方, 一覧に出す 1 行, 本文)。一覧と全文はこの順で並ぶ。
TOPICS = (
    ("version", "ccnavi --version", "版・ビルド情報・受け付けるフラグを出す", _VERSION),
    ("lint", "ccnavi --lint", "ルール・フェーズ・リスクの設定を検証する", _LINT),
    ("test", "ccnavi --test", "ツールを動かさずにルールの判定を試す", _TEST),
    ("explain", "ccnavi --explain", "チケットとその置き場を、機械が読める形で一覧する", _EXPLAIN),
    ("docs", "ccnavi --docs", "md の frontmatter の索引を引く", _DOCS),
    ("templates", "ccnavi --templates", "作業・レビュー計画のテンプレートを扱う", _TEMPLATES),
    ("learnings", "ccnavi --learnings", "学びの蓄積のパターンの状態を一覧する", _LEARNINGS),
    ("logs", "ccnavi-user-prune.sh / --suggest", "判定ログを整理し、ルールの候補を作る", _LOGS),
    ("agree", "ccnavi-user-agree.sh / --plan-order", "提案の承認と、計画の順序", _AGREE),
    ("ticket", "ccnavi ticket", "チケットの開始・終了・状態・雛形", _TICKET),
    (
        "scripts",
        "ccnavi review|worktree|sync ほか",
        "スクリプトが呼ぶサブコマンドと内部オプション",
        _SCRIPTS,
    ),
)

# `--help <語>` と `ccnavi <語> --help` で話題を選ぶ語。
# 話題名のほか、スクリプト用のサブコマンドも含む。
TOPIC_WORDS = {
    **{name: name for name, *_ in TOPICS},
    **{
        verb: "scripts"
        for verb in ("review", "worktree", "sync", "tree", "write-and-push", "branches")
    },
}

_INTRO = """\
ccnaviはエージェントのツール呼び出しを制御し、より安全な操作へ誘導します。

通常はフックとして実行します。標準入力からフックのJSONデータを1件読み込み、
処理結果を標準出力に出力します。

ツールの実行前に呼び出しを判定するには、PreToolUseに登録してください。
実行後に保護対象ファイルへの変更を確認するには、PostToolUseにも登録してください。
動作確認は次のように行います。

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash",
           "tool_input":{"command":"git push"}}' | ccnavi
"""

# 一覧の「引き方」の欄の幅。
_HOW_WIDTH = 34

OVERVIEW = (
    _INTRO
    + """
話題ごとの使い方は、話題のオプションかサブコマンドに --help を付けると出ます
（例: ccnavi --docs --help、ccnavi ticket --help）。ccnavi --help <話題> でも同じです。
全文は ccnavi --help --all で出ます。

"""
    + "\n".join(f"    {name:<10} {how:<{_HOW_WIDTH}} {summary}" for name, how, summary, _ in TOPICS)
    + "\n"
)

# 全文は一覧のあとに各話題の節を並べたもので、節の外に残る文はない。
USAGE = OVERVIEW + "\n" + "\n".join(body for *_, body in TOPICS)

DOCS_USAGE = _DOCS


def topic(name: str) -> str:
    """TOPICS にある話題名の節を返す。"""
    return next(body for n, _, _, body in TOPICS if n == name)
