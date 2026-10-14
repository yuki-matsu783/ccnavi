"""`ccnavi --help` の本文。

cli から分けた。cli を読まない。
"""

from __future__ import annotations

USAGE = """ccnaviはエージェントのツール呼び出しを制御し、より安全な操作へ誘導します。

通常はフックとして実行します。標準入力からフックのJSONデータを1件読み込み、
処理結果を標準出力に出力します。

ツールの実行前に呼び出しを判定するには、PreToolUseに登録してください。
実行後に保護対象ファイルへの変更を確認するには、PostToolUseにも登録してください。
動作確認は次のように行います。

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash",
           "tool_input":{"command":"git push"}}' | ccnavi

バージョンとビルド情報を確認するには、次のコマンドを実行してください。

    ccnavi --version [--json]

バージョン、ビルド元のコミット（ソースから実行した場合は "unknown"）、.ccnavi/scripts/
のスクリプトとVS Code拡張機能が自身の値と照合する互換バージョン、
受け付けるすべてのフラグを出力します。ペイロードも設定も読み込みません。--json の形式は
README.md（「版の JSON」）に記載しています。

ツールの実行可否を判定せずに、ルールファイルと設定を検証するには、次のコマンドを実行します。

    ccnavi --lint [--json]

ペイロードは読み込みません。ガードを無効にしうる事項をエラーまたは警告として報告し、
エラーを報告した場合は0以外の終了コードで終了します。--json で出力される形式は README.md（「lint
の JSON」）に記載しており、VS Code拡張機能がこれを読み込みます。

プロジェクトのルールを保存前に試験または検証するには、
編集中のファイルをプロジェクト名とともに渡します。このフラグは --test、--test-expectations、
--lint、--explain 専用です。それ以外（フックの呼び出し、
チケットやレビューのサブコマンド）では無視され、その旨が標準エラー出力に表示されます。

    ccnavi --test Write projects/lib/src/a.py --project-rules-file lib=/tmp/rules.yml

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
README.md（「ドキュメントの索引」）に記載しています。これらのフィルターは --docs 専用です。
ほかの実行で指定すると、終了コード1で停止します。他の実行用のフラグを --docs
に指定した場合も同様に停止します。

作業・レビュー計画のテンプレートを一覧・検索・参照するには、
次のコマンドを実行します（計画と子フローを1ファイルにまとめたもので、
.ccnavi/config/common/templates/ と .ccnavi/config/project/templates/ に置かれます。
読み取り専用で、何も書き込みません）。

    ccnavi --templates [--layer self|PROJECT] [--keyword K]... [--json]
    ccnavi --templates --show NAME [--layer self|PROJECT] [--json]

一覧は、共通レイヤーと1つのレイヤー（--layer を指定しない場合は self）を合わせたものです。
--keyword は名前、説明、または使う場面の一部に一致します（2回指定した場合はどちらかに一致）。
--show は、1件のテンプレートを計画・フロー・検査結果とともに表示します。
両方のレイヤーに同じ名前で内容の異なるテンプレートがある場合はエラーとなり、
そのレイヤー側のテンプレートは使用されません。--json は、ボード画面が描画する完全な応答で、
README.md（「作業・レビュー計画のテンプレート」）に記載しています。--keyword は --docs
と共通で、それ以外では拒否されます。--docs のその他のフィルターはここでは拒否されます。--lint
でも同じ検査を報告します。

テンプレートは2段階で収集します。エージェントは --draft を実行できます。
完了済みの親チケット（done/ にあるもの）の計画と子フローを、wip/proposals/templates/NAME.yml
に下書きとしてコピーします。下書きは効力を持たず、上書きもされません。--import
はユーザーだけが実行します。--yes を指定しない場合は何も書き込まず、検査結果と、
取り込み先の同名テンプレートとの差分を表示します。--yes と、--expect
に下書きのハッシュを指定した場合は書き込み、下書きが変更されていなければ下書きを削除します。

    ccnavi --templates --draft PARENT [--name NAME] [--json]
    ccnavi --templates --import DRAFT --to common|project [--layer NAME]
                       [--replace] [--yes --expect HASH] [--json]

下書きと収集済みのテンプレートは編集もできます。--yes を指定しない場合は何も書き込まず、
変更内容を表示します。--yes と、--expect に対象のハッシュを指定した場合は書き込みます。--yes
はユーザーだけが実行します。--save-draft、--rename-template、--move-template、--delete-template
はユーザー専用です（エージェントは実行できず、プレビューも実行できません。エージェントは Write
で下書きを編集します）。--delete-draft のプレビューは、エージェントも実行できます。

    ccnavi --templates --save-draft NAME --body-file FILE [--yes --expect HASH] [--json]
    ccnavi --templates --delete-draft NAME [--yes --expect HASH] [--json]
    ccnavi --templates --rename-template NAME --new-name NEW --on draft|common|project
                       [--layer NAME] [--yes --expect HASH] [--json]
    ccnavi --templates --move-template NAME --on common|project [--layer NAME]
                       --to common|project [--to-layer NAME] [--yes --expect HASH] [--json]
    ccnavi --templates --delete-template NAME --on common|project [--layer NAME]
                       [--yes --expect HASH] [--json]

収集済みのテンプレートは、親の提案を開始するときに使用されます。
エージェントも実行できます（フローの下書きも効力を持ちません）。

    ccnavi ticket new ID --title TITLE --template NAME [--layer self|PROJECT]
                         [--with-feedback] [--issue N]

ID という名前のワークツリーに wip/proposals/todo/ID.md を書き込みます。plan:
はテンプレートから取得し（after は、計画内の位置から提案側の番号へ振り直されます）、続けて
phases: を補完します。feedback: は --with-feedback
を指定した場合のみ書き込まれます（フィードバック計画は、メイン計画の番号の続きになります）。
指定しない場合は、テンプレートにフィードバック計画があることが出力に表示されます。
フィードバック計画は、メイン計画の最後のレビューの後に、改訂によって1回だけ追加されます。
最初の提案に書いてしまうと、承認後にメイン計画を改訂できなくなるためです。--with-feedback は、
ticket new --template 以外で指定すると拒否されます。n番目の計画項目のフローは
wip/proposals/flows/ID-NN-01.yml に出力されます。これは下書きで、
ユーザーがフローエディターで取り込むまで効力を持ちません。NAME は、
共通レイヤーと1つのレイヤー（--layer を指定しない場合は、
ワークツリー自身のレイヤー）から検索されます。次の場合は、何も書き込まずに停止します。
テンプレートが存在しない、または使用できない（エラーがある、または重複により除外された）場合、
phases.yml にないタイプがある場合、--plan も指定された場合、
提案またはフローの下書きがすでに存在する場合。提案は、通常の承認を経る必要があります。

共通レイヤーの3つのファイルは --rules、--phases、--risk で、レイヤーを探す場所は --projects と
--project-home で変更します。この5つはいずれも同じ制限が適用され、診断専用で、
それ以外の実行では無視されます。

--root と --cwd は、この実行がどこで行われているかを指定します。.ccnavi/scripts/
のラッパースクリプトがこれらを判定して渡すため、各1回のみ指定できます。2回目の指定は、
上書きとしては扱われず拒否されます。

チケット、その置き場、フェーズの印、レビューの保留を機械可読な形式で一覧表示するには、
次のコマンドを実行します（VS Codeのボード拡張機能がこれを読み込みます）。

    ccnavi --explain --json

ペイロードは読み込まず、リモートにも接続しません。形式は README.md（「ボードの
JSON」）に記載しています。

ツールを実行せずにルールの判定結果を確認する、
またはファイル内の期待する判定をすべてルールに照らして実行するには、
次のコマンドを実行してください。

    ccnavi --test Bash "git push" [--json]
    ccnavi --test-expectations .ccnavi/config/common/rule-expectations.yml [--json]

どちらもフックと同じ判定処理を通ります。--json で出力される形式は README.md（「試験の
JSON」）に記載しており、VS Code拡張機能がこれを読み込みます。--test は、
ワークスペースルートから、権限モードなし・メインセッションとして呼び出しを試験します。
別の呼び出し条件で試験するには、次を追加します。

    --cwd <dir> --permission-mode <mode> --agent-id <id>

mode には auto、default、acceptEdits、plan、dontAsk、bypassPermissions のいずれかを指定します。
これ以外の名前や、ディレクトリではない --cwd は拒否されます。--permission-mode と --agent-id は
--test 専用です（--lint や --explain とも併用できません）。フラグは "--test <TOOL> <SUBJECT>"
の前後に置いてください。2つの語の間に置いた場合や、
2つの語の後に余分な語がある場合は拒否されます。

判定ログをローテーションし、古いログや終了したセッションの状態データを削除するには、
次のコマンドを実行してください（SessionStartでも自動的に同じ処理が行われます）。

    ccnavi --prune [--preview]

--preview を指定すると、移動予定の項目のみを表示し、実際の変更は行いません。
指定しない場合、--prune はターミナルから実行する必要があります。

判定ログ（logs/decisions.jsonl と、ローテーション済みの
decisions.*.jsonl）からルールの候補を作成するには、次のコマンドを実行します。

    ccnavi --suggest [--json]

出力されるのは deny/ask の候補のみです。どのルールにも一致せず、
繰り返しユーザーに判断が委ねられた呼び出しと、同じ呼び出しを繰り返し止めた deny（その message
を見直してください）が対象です。各候補は --lint および --test-expectations
と同じ検査を通過しています。通過しなかったものは件数のみ表示され、候補からは除外されます。
何も書き込みません。--json で出力される形式は README.md（「候補の JSON」）に記載しており、VS
Code拡張機能がこれを読み込みます。

承認待ちのチケットを確認し、提案された作業範囲を承認するには、次のコマンドを実行してください。

    ccnavi --agree
    ccnavi --agree i0002 i0002-01-01
        （指定したチケットのみ。例: 絞り込んだボードから実行する場合。
         idはすべてのフラグの後ろに指定します）

すべてのワークツリーの wip/proposals/todo/ をスキャンし、各チケットが書き込み可能にする範囲と、
人によるレビューが必要かどうかを表示します。そのうえで、承認したチケットを
.ccnavi/approved/doing/ に移動します。呼び出しの判定で参照されるのはこの場所だけなので、
提案を書いただけで書き込み可能な範囲が広がることはありません。id
は一括処理の対象を絞り込むだけです。承認待ちでない id や、
承認待ちの親（またはその親の承認待ちの改訂）がない状態で指定された子は、何も承認されません。

エージェントは、ユーザーに承認を求める前に、
書いたばかりの提案が承認可能な状態であることを検証します。

    ccnavi --agree --preview --verify [--json] [<id>...]

何も配置せず、ターミナルも不要です。終了コード0は「可」を表します。指定したすべてのチケット（id
を指定しない場合は承認待ちのすべてのチケット）が、現在の状態のまま一括処理の対象に含まれるため、
ユーザーに承認を求められます。終了コード3は「不可」を表します。承認待ちでない id、
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

--order は、各項目の直接の先行を、現在の提案に書かれている番号で指定します: {"plan": {"1": [],
"2": [3], "3": [1]}, "feedback": {...}}。渡された部分は、
すべての番号の先行をそこから取得します（省略された番号は何も待ちません）。渡されなかった部分は、
提案のままです。項目は、先行がすべて配置済みのもののうち現在の番号が最小のものから順に、
トポロジカル順に番号が振り直されます。`after` は新しい番号に追従し、`review: defer`
は項目とともに移動します。改訂の場合、承認済みの子がすでにある番号はそのまま残り、
それ以外の項目は空いている番号に割り当てられます。循環を生む線、
固定された番号の先行を変更する線、固定された番号を避けて詰められない線は拒否され（`refused`）、
番号は一切振り直されません。

応答には次の項目が含まれます。`plans`（部分ごとに1つ。各項目の `number` と `from`、
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
書き換えた提案を承認するには、本文が変わるため、新しいプレビューが必要です。

次の2つは「不可」ではありません。--agree もこれらを除外しないためです。
親またはフェーズタイプの範囲を超える範囲（承認後も、そこへの書き込みは拒否されたままです）と、
読み取れない提案（スキャンは、id による絞り込みの前にすべてのワークツリーを対象とするため、
別のセッションの下書きがあると「不可」になります）です。どちらも表示されます。
承認待ちが何もないという状態だけは、両者で扱いが異なります。--agree は、
処理するものがない成功として扱い、検証は「不可」として扱います。

VS Codeのボード拡張機能は、ターミナルではなくオーバーレイから承認します。

    ccnavi --agree --preview --json [<id>...]  （一括処理の内容を表示します。何も配置しません）
    ccnavi --agree --yes <id,id,...> --json
        （指定したidを承認します。指定したidが承認の対象を兼ねます。--digest も
         末尾のidも受け付けず、付けると終了コード 1 になります）

--yes はターミナルを必要としません。承認する時点の提案を、ターミナルでの --agree と同じ検査にかけ、
指定したidだけを承認します。プレビューで表示した内容とは比較しません。表示後に編集された提案は、
その時点の内容で承認されるため、ユーザが判断している間は承認待ちの提案を編集しないでください。
指定したidが承認待ちにない場合、絞り込みが成り立たない場合（改訂された親を含めずに子だけを指定した場合）、
指定したものが検査から外れた場合（このときはどれも承認しません）は、何も配置せず
終了コード 1（`[CORE_APPROVE_NOTHING]`）になります。指定していない提案は承認しません。
フックは、承認をモデルに通知しません。拡張機能は、--yes が出力するものと同じテキストを `prompt`
でモデルに渡します。それ以外の場合、親エージェントは `ccnavi ticket status [<parent>]`
で確認します。

親エージェントは、.ccnavi/scripts/ のスクリプトを通じて、チケットの状態を移動し、
レビューを依頼します。スクリプトは次を呼び出します。

    ccnavi ticket start|finish|cancel <id> [--reason <why>]
        （start は、基点を .ccnavi/approved/doing/<id>.md に書き込みます。finish は、
         フェーズがレビュー対象の場合は wip/proposals/review/ に、それ以外は
         .ccnavi/approved/done/ に移動します。cancel は .ccnavi/approved/done/ に移動します）
    ccnavi ticket record-risk <child> <factor> yes|no --reason <why>   （定性的なリスク）
    ccnavi ticket learn [<id>] [--note <text> [--target <kind>] [--signal <kind>]]
        （学習記録の雛形と判定ログのダイジェストを wip/handoff/learnings/<id>.* に書き込みます。
         --note を指定すると学習内容を1件追記します。チケット外の場合は
         docs/wiki/inbox/ に書き込みます。
         <id> はオプションの前に指定します）
    ccnavi ticket new <id> --title <title> --plan <type,type,...> [--scope <glob>]... [--issue N]
        （<id> という名前のワークツリーの wip/proposals/todo/<id>.md に、親の提案の雛形を
         書き込みます。そのワークツリーの中で実行してください。選択したフェーズの範囲を
         phases.yml から集めて allow に入れ、--scope の glob（この作業が触れるファイル。
         ワークツリーのルートからの相対パス。複数指定可）を追加し、計画を直線状につなぎ、
         phases: を補完します。quick-work のような scope: inherit のタイプは、それ自体では
         何も集めないため、--scope を指定してください。phases.yml がない場合、子のidを
         指定した場合、または allow が空になる場合は停止します。
         ccnavi-ticket.sh を経由せず、直接実行します）
    ccnavi ticket new <id> --title <title> --template <name> [--layer <name>]
                           [--with-feedback] [--issue N]
        （上と同じですが、計画と子フローの下書きは --plan の代わりにテンプレートから取得します。
         フィードバック計画は --with-feedback を指定した場合のみ書き込まれます。
         上記の --templates の節を参照してください）
    ccnavi ticket status [<parent>]
        （読み取り専用。置き場、承認時刻、着手済みかどうか、未コミット・未pushの有無
         （ローカルのリモート追跡参照に基づく）、停止している理由、次の手順を表示します。
         サブエージェントも実行できます）
    ccnavi ticket next-id [<prefix> <slug>]
        （読み取り専用。issueのない親の次の連番、またはそれから組み立てた
         識別子 <prefix>-<number>-<slug> を表示します。サブエージェントも実行できます）
    ccnavi review prepare   --cwd <dir> --phase N --body-file <path>
    ccnavi review requested --cwd <dir> --phase N --result <json>
    ccnavi review confirm   --cwd <dir> --phase N --result <json> [--actor <account>]
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
    ccnavi worktree name <name>
        （直接作業するワークツリーの名前が <prefix>-<number>-<slug> の形式でない場合に、
         "warn: ..." を1行表示します。形式どおりなら何も表示しません。拒否はしません）
    ccnavi sync paths
        （.ccnavi/scripts/ccnavi-sync.sh 用。1行に "<key> <value>" を1組ずつ出力します。
         対象は、承認済みと提案の置き場、ccnaviのディレクトリ、.claude/settings.local.json の
         env に書かれた統合先ブランチです。統合先ブランチについては、環境変数を読みません）
    ccnavi sync check <parent> [<repo>]
        （ccnavi-sync.sh が <parent> を取り込んだ後に使用します。ファミリーの承認済みチケットと
         その権限を再判定します。"check 1" の行に続いて、"<severity> <detail>" を1行ずつ出力します。
         エラーによってファミリーが停止する場合は、終了コード1になります）
    ccnavi sync closed <parent> <repo> <integration> <tree>
        （ccnavi-sync.sh 用。"closed 1" の行に続いて、"integration yes|no"（統合先ブランチの
         done/ に同じ親があるか）と "archive yes|no"（ローカルのアーカイブにそれがあるか）を
         出力します。読み取り専用で、git show はローカルで実行されます）
    ccnavi tree parent <tree> <name>
        （スクリプト用。"tree 1" の行に続いて、"parent yes|no|unreadable"（そのツリーが
         親のチケットまたは提案 <name> を保持しているか）を出力します。スクリプト自身は
         チケットのYAMLを読み込みません）

ccnavi自身は、リモートに接続しません。スクリプトがマージリクエストとそのスレッド、
レビューを取得し、--result <json> として渡します。

未解決のレビュースレッドは、人が親のワークツリーから、次のコマンドで受け入れます。

    sh <workspace root>/.ccnavi/scripts/ccnavi-review.sh decide N

（スクリプトはワークスペースにのみ存在するため、プロジェクトから作成したワークツリーからは、
相対パスで到達できません）

このコマンドはスレッドを取得し、次を実行します。

    ccnavi --reviewed N --accept-unresolved --result <json> --cwd <parent worktree>
        [--actor=<account> --via=terminal|board]
        （スクリプトは、読み取れる場合はトークンの所有者を渡します。レビュー済みの印には、
         アカウントと実行方法が記録されます。渡さなかった場合、印はこれまでどおりです）

人が親を早期にクローズする（「今のところ十分」とする）には、次を実行します。

    sh <workspace root>/.ccnavi/scripts/ccnavi-review.sh close-early --reason <why>

`ccnavi --close-early --reason <why> --result <json>` を実行した後、
マージリクエストのDraftを解除し、残った項目を新しいissueとして登録します。
"""
