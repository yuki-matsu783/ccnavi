# CLAUDE.md

このファイルには、ccnavi の開発で常に守ることだけを書く。詳細は `.ccnavi/docs/`（ccnavi の使い方。配布先にも配る。引き先の表は `.ccnavi/CCNAVI.md`）と `docs/claude/`（ccnavi の開発）に記載。

## 応答ルール

1. 簡潔で明確な日本語を使う
2. 「です・ます」調は使うが、冗長な敬語表現は避ける
3. 「いたします」→「します」、「ございます」→「あります」に置き換える
4. 「恐れ入りますが」などのクッション言葉は省く

## 常に守ること

- sh は Windows (Git Bash / WSL)・Linux・macOS のどれでも動くように書く
- ccnavi の使い方に関わる考え方と決まり（ワークツリー・git の入口・下書きの置き場・サブエージェントへの委任・合意が要る変更の範囲・保護対象の直し方）は `.ccnavi/CCNAVI.md` に書く。セッション開始時に ccnavi がそれを渡すので、ここには書かない。ccnavi の振る舞いが変わったら CCNAVI.md を直す

## 詳細

ccnavi の開発（`docs/claude/`）は、作業が表の「読むとき」に当てはまった場合に参照する。
ccnavi の使い方（`.ccnavi/docs/`）は `.ccnavi/CCNAVI.md` の表に記載する。

| 読むとき | 開くファイル |
|---|---|
| ccnavi のリポジトリの md に付ける frontmatter の `type` の値を確かめる | `docs/claude/frontmatter.md`（付け方は `.ccnavi/docs/frontmatter.md`） |
| sh やスクリプトを書く、文書・コメント・ユーザに見える文言で ADR に触れたくなった | `docs/claude/environment.md` |
| ccnavi の実行ファイル（`src/ccnavi/`）か `.ccnavi/scripts/` の sh を直す | `docs/claude/exe-boundary.md` |
| sh・Python・TS でログを書く、logger を直す | `docs/claude/logging.md` |
| VS Code 拡張（`extensions/vscode/`）の案内（ガイドツアー）を作る・直す・ユースケースを足す | `docs/claude/extension-tour.md` |
