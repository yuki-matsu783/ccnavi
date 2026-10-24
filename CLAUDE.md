# CLAUDE.md

このファイルには、ccnavi の開発で常に守ることだけを書く。詳細は `.ccnavi/docs/`（ccnavi の使い方。配布先にも配る。引き先の表は `.ccnavi/CCNAVI.md`）と `docs/claude/`（ccnavi の開発）に記載。

## 応答ルール

1. 簡潔で明確な日本語を使う
2. 「です・ます」調は使うが、冗長な敬語表現は避ける
3. 「いたします」→「します」、「ございます」→「あります」に置き換える
4. 「恐れ入りますが」などのクッション言葉は省く

## 常に守ること

- sh は Windows (Git Bash / WSL)・Linux・macOS のどれでも動くように書く
- パスと名前は、どの機械でも大文字小文字を区別せず比べる（同じ綴り違いは同じものとして、両立させない）。`os.path.normcase`を単独で使わず、`pathcase.fold`を通す。決まりは `docs/claude/environment.md`
- CLAUDE.md には、常に守る決まりだけを短く書く。理由・手順・例は「詳細」の表が指す文書に書く。新しい決まりは、まずその文書に書き、表に 1 行足す（段階的詳細化）
- ccnavi の使い方に関わる考え方と決まり（ワークツリー・git の入口・下書きの置き場・サブエージェントへの委任・合意が要る変更の範囲・保護対象の直し方）は `.ccnavi/CCNAVI.md` に書く。セッション開始時に ccnavi がそれを渡すので、ここには書かない。ccnavi の振る舞いが変わったら CCNAVI.md を直す

## 詳細

ccnavi の開発（`docs/claude/`）は、作業が表の「読むとき」に当てはまった場合に参照する。
ccnavi の使い方（`.ccnavi/docs/`）は `.ccnavi/CCNAVI.md` の表に記載する。

| 読むとき | 開くファイル |
|---|---|
| ccnavi の開発の入口（環境・テスト・build・構成・文書の置き場）を確かめる、利用者向けの手引き（`docs/guide/`）を足す・直す | `docs/development.md` |
| ccnavi のリポジトリの md に付ける frontmatter の `type` の値を確かめる | `docs/claude/frontmatter.md`（付け方は `.ccnavi/docs/frontmatter.md`） |
| 思想（`docs/00_idea.md`・`docs/01_idea/`。ユーザが依頼しない限り直さない）、要件書・設計書・ADR（`docs/requirements`・`docs/design`・`docs/adr`）を書く・直す、これらの文書から別の文書を指す | `docs/claude/docs-writing.md` |
| sh やスクリプトを書く、パス・名前を比べる、文書・コメント・ユーザに見える文言で ADR に触れたくなった | `docs/claude/environment.md` |
| ccnavi の実行ファイル（`src/ccnavi/`）か `.ccnavi/scripts/` の sh を直す | `docs/claude/exe-boundary.md` |
| sh・Python・TS でログを書く、logger を直す | `docs/claude/logging.md` |
| sh や実行ファイルの出力に案内（`次:`・`案内:`・`必須:`）を足す・直す、ワークフローを進める出力を新しく書く | `docs/claude/script-output.md` |
| Python のテストを回す（特に全件）、回すグループを決める | `docs/claude/testing.md` |
| 受入シナリオ（`tests/scenarios/`）を書く、`claude -p` で振る舞いを回す（明確な指示があったときだけ） | `docs/claude/acceptance-scenarios.md` |
