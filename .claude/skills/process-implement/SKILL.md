---
name: process-implement
process: true
description: >-
  実装の標準的な考え方への入口。機能の追加、不具合の修正、挙動を変えないコード整理など、コードを書き換える作業で使う。原因が分からない不具合は process-debug、テストを書く・回す作業は process-test。細かい手順は references にあり、ccnavi --docs で共通とプロジェクトの両方から引く。
---

# process-implement

実装の作業に入る前に、`ccnavi --docs --type skill-reference --tag process-implement` で共通とプロジェクトの reference を引き、ヒットしたものを読む。

## 基本の考え方

- 書く前に、周りのコードの命名・構造・コメントの量を読み、それに合わせる
- 振る舞いを変えるときは、先に失敗するテストを置き、通る最小の変更を足す
- チケットの範囲の外は直さない。途中で見つけた別件は、提案にして分ける
- 検査（lint・テスト）を通してから渡す。通らないものを、後で直す前提で出さない

## reference の置き場

- 共通: `.claude/skills/process-implement/references/common/`（プロジェクトの `skills/process-implement/references/common/` へコピーされる。プロジェクトの中では直さない）
- そのリポジトリ固有: `references/project/`（ワークスペースは `.claude/skills/process-implement/references/project/`、プロジェクトは `projects/<名前>/skills/process-implement/references/project/`。コピーされない）

中のディレクトリ構造は任意。足し方と書き方は `.ccnavi/docs/skill-processes.md`。
