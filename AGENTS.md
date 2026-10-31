# AGENTS.md

このファイルには、ccnavi の開発で常に守ることだけを書く。詳細は `.ccnavi/docs/`（ccnavi の使い方。配布先にも配る。引き先の表は `.ccnavi/CCNAVI.md`）と `.agents/docs/`（ccnavi の開発）に記載。

## 応答ルール

1. 簡潔で明確な日本語を使う
2. 「です・ます」調は使うが、冗長な敬語表現は避ける
3. 「いたします」→「します」、「ございます」→「あります」に置き換える
4. 「恐れ入りますが」などのクッション言葉は省く

## 常に守ること

- sh は Windows (Git Bash / WSL)・Linux・macOS のどれでも動くように書く
- パスと名前は、どの機械でも大文字小文字を区別せず比べる（同じ綴り違いは同じものとして、両立させない）。`os.path.normcase`を単独で使わず、`pathcase.fold`を通す。決まりは `.agents/docs/environment.md`
- まれな異常状態のために推測・修復・やり直し・詳しい説明を作り込まず、状態と直し方を言って止める（ユーザに頼む）。データ損失を防ぐ処理、保護の強さ、普段の経路で使う処理は残す。決まりは `.agents/docs/irregular-states.md`
- 操作を止める条件（関門）は 1 か所の関数に置き、同じ意味の条件を操作ごとに書き直さない。承認・レビューを頼む操作（`approval-request`・`review-request`）は、後の関門で止まる条件を同じ関数で先に拒否する。決まりは `.agents/docs/gate-conditions.md`
- 実装は一番シンプルになる方法を選ぶ。共通化などで単純にできる部分は、修正範囲が広がっても進める（ユーザと合意が要る変更の扱いは別）。決まりは `.agents/docs/gate-conditions.md`
- 責務と依存関係を考える。1 つのモジュールは 1 つの責務を持ち、import は段が同じか下へだけ向ける（`tests/core/test_module_layers.py`）。共通化で置き場を動かすときは、段と責務を先に決める。決まりは `.agents/docs/gate-conditions.md`
- 後方互換は考えない。名前・オプション・形式を変えるときは、別名や旧形式を読む処理を作らず、リポジトリ内の旧名を全部置き換える（ADR は履歴なので直さない）。決まりは `.agents/docs/gate-conditions.md`
- AGENTS.md には、常に守る決まりだけを短く書く。理由・手順・例は「詳細」の表が指す文書に書く。新しい決まりは、まずその文書に書き、表に 1 行足す（段階的詳細化）
- ccnavi の使い方に関わる考え方と決まり（ワークツリー・git の入口・下書きの置き場・サブエージェントへの委任・合意が要る変更の範囲・保護対象の直し方）は `.ccnavi/CCNAVI.md` に書く。セッション開始時に ccnavi がそれを渡すので、ここには書かない。ccnavi の振る舞いが変わったら CCNAVI.md を直す

## 詳細

ccnavi の開発（`.agents/docs/`）は、作業が表の「読むとき」に該当した場合に参照する。
ccnavi の使い方（`.ccnavi/docs/`）は `.ccnavi/CCNAVI.md` の表に記載する。

| 読むとき | 開くファイル |
|---|---|
| ccnavi の開発の入口（環境のコマンド・構成・文書の案内）を確かめる、利用者向けの手引き（`docs/50_guide/`）を足す・直す | `docs/40_development.md` |
| 開発用の hook（`.claude/hooks/`）を足す・直す、ターンの終わりのテストの仕組みを確かめる | `.agents/docs/dev-hooks.md` |
| 実行ファイルを組み立てる（`build.py`）、`.ccnavi/bin/` や `dist/` を扱う、配布する sh を確かめる | `.agents/docs/build-and-dist.md` |
| 作業の決まり（`.ccnavi/CCNAVI.md`）に、実行ファイル側で条件の名前を足す | `.agents/docs/ccnavimd-conditions.md` |
| ccnavi のリポジトリの md に付ける frontmatter の `type` の値を確かめる | `.agents/docs/frontmatter.md`（付け方は `.ccnavi/docs/frontmatter.md`） |
| 思想（`docs/00_idea.md`・`docs/01_idea/`）を書く・直す（ユーザが依頼しない限り直さない） | `.agents/docs/idea-writing.md` |
| 文書（`docs/`・`.ccnavi/docs/`・`.agents/docs/`・`README.md`）を足す・動かす・改名する、どの読み手向けの文書か・どこに置くかを決める、文書から別の文書へリンクを張る（上位から下位へ張ってよいか） | `.agents/docs/docs-structure.md` |
| 要件書・設計書・ADR（`docs/11_requirements`・`docs/21_design`・`docs/30_adr`）を書く・直す、これらの文書から別の文書を指す | `.agents/docs/docs-writing.md` |
| sh やスクリプトを書く、パス・名前を比べる、コードのコメントを書く、文書・コメント・ユーザに見える文言で ADR に触れたくなった | `.agents/docs/environment.md` |
| ccnavi の実行ファイル（`src/ccnavi/`）か `.ccnavi/scripts/` の sh を直す | `.agents/docs/exe-boundary.md` |
| エージェントを止める・返事を求める仕組みを足す・直す、拒否の文を書く | `.agents/docs/stop-patterns.md` |
| 異常状態（壊れた記録・書きかけ・手で動かされた状態・一時的な失敗）を扱う処理を足す・直す・削る | `.agents/docs/irregular-states.md` |
| 操作を止める条件（関門）を足す・直す、承認・レビューを頼む操作の拒否条件を決める、名前・オプション・形式を変える（後方互換を残さない） | `.agents/docs/gate-conditions.md` |
| エージェントに渡す次の一手を、どの契機・担当に置くか選ぶ（`status`・`guidance()`・Stop の促し・ルールの文脈など） | `.agents/docs/next-step-placement.md` |
| sh・Python・TS でログを書く、logger を直す | `.agents/docs/logging.md` |
| sh や実行ファイルの出力に案内（`次:`・`案内:`・`必須:`）を足す・直す、チケット作業を進める出力を新しく書く | `.agents/docs/script-output.md` |
| Python のテストを回す（特に全件）、回すグループを決める | `.agents/docs/testing.md` |
| GitLab の模擬（`tests/fake_gitlab.py`）の応答を変える、契約テスト（`tests/contract/`）・録画した応答の形（`recorded*.json`）を足す・直す | `.agents/docs/gitlab-contract.md` |
| 受入シナリオ（`tests/scenarios/`）を書く、`claude -p` で振る舞いを回す（明確な指示があったときだけ） | `.agents/docs/acceptance-scenarios.md` |
| 手動で本物の GitLab（Docker）を立てて 1 周する | `.agents/docs/gitlab-manual.md` |
| ユーザに実機確認（VS Code 拡張の画面）を頼まれた | `.agents/docs/real-screen-verification.md`（Playwright と code-server を入れてよい。まず自分で確かめる。手順は `extensions/vscode/ccnavi-board/docs/design/real-screen-check.md`） |
| 拒否・促し・次の一手の文を文面ファイルに足す・直す | `.agents/docs/message-files.md`（移行の進み具合は `.agents/docs/message-migration.md`） |
