---
title: ドキュメントの索引の使い方
type: guide
description: ccnavi --docs で文書を frontmatter から引く方法。絞り方、並べ方、出力の形式、案内が出るタイミング
tags: [user-guide, docs-search, frontmatter]
keywords: [--docs, 索引, frontmatter, type, tags, keywords, 検索, NOTICE_DOC_FRONTMATTER, 絞り込み, 出力形式]
---

# ドキュメントの索引の使い方

`ccnavi --docs` で、ワークスペースとプロジェクトの md を frontmatter から引く。ここは使い方と見方だけを書く。
索引の作り方、読む対象、書く条件、索引の 1 行の形は [診断と機械可読な出力](../21_design/diagnostics.md) の「`--docs` の索引」にある。

これがコア機能 3「ドキュメント・スキルの検索と必要時の参照」の中心である。文書とスキルの本文を最初から全部は渡さず、目録と索引で読む対象を絞り、
本文はエージェントが必要になったときに読む。引くのはワークスペースと下のプロジェクトの文書で、ccnavi 自身のヘルプに限らない。流れは次の 3 つ。

- 文書: `ccnavi --docs` で frontmatter（`type` `tags` `keywords` `title` `description` など）とパスを引いて読む文書を特定し、本文は別に読む
- プロジェクトのスキル: セッション開始・サブエージェント開始・プロジェクトに入った最初の呼び出しで、スキルの名前・説明・パスの目録だけを渡す（「プロジェクトのスキル」）。本文は渡さない
- 工程スキル（`process-*`）: 短い基本の考え方だけを持ち、細かい手順は `references` にある。`ccnavi --docs --type skill-reference --tag <工程スキル名>` で引いて読む

**検索の実態。** `--docs` が見るのは frontmatter とパスと更新日時だけで、本文は読まない。種類・タグ・キーワード（完全一致）、パスと語（部分一致）、更新日時で絞る。
本文の関連箇所の抽出、意味検索、自動要約、トークンの予算に応じた選別はしない。`--limit` は件数で切るだけである。
索引の差分更新は、索引を新しくする手間を省くもので、モデルに渡す量を減らす仕組みではない。
必要な文書が必ず見つかることも、渡す量が一定量減ることも保証しない。frontmatter が無い・合っていない文書は絞り込みにヒットしない。

**ガイドとの違い。** ガード・ガイド（コア機能 1）は、操作や作業の状況がきっかけで、必要な説明が届く。こちらは、エージェントが情報を必要としたときに、資料や手順を自分で探して読む。

```sh
ccnavi --docs --text マージ --format detail           # 話題の見当を付ける
ccnavi --docs --type adr --sort mtime -r --limit 10     # 新しい ADR から 10 本
ccnavi --docs --tag worktree --tag git --format path    # どちらかのタグを持つもの（OR）のパスだけ
ccnavi --docs --path .ccnavi/docs --format count         # 件数だけ
```

絞り方と出力の形式の一覧は `ccnavi --docs --help` で出る。`ccnavi --help` は話題の一覧だけを出し、話題ごとの節は `ccnavi <オプション> --help`（例: `ccnavi ticket --help`）か `ccnavi --help <話題>` で出す。全文は `ccnavi --help --all` で出る。SessionStart の案内もここを指す。

md を本文ではなく頭の frontmatter で引く。grep はヒットした行を返すので、そのファイルが何の文書かは開くまで分からず、
よそからの言及も同じ重みで混ざる。frontmatter の書き方は [.ccnavi/docs/frontmatter.md](../../.ccnavi/docs/frontmatter.md)。

`SessionStart` が渡すのは引き方の 1 行だけで、オプションの一覧と frontmatter の書き方は渡さない。書き方は、Write で新しく作った md に
frontmatter か `type` が無いときに、`PostToolUse` が `[ccnavi] NOTICE_DOC_FRONTMATTER (<パス>)` で 1 度だけ案内する。
Edit と上書きでは出さない。索引に載らない md（git が無視するもの、`.ccnavi/` の下）と、`README.md`・`CLAUDE.md`・`AGENTS.md`・`CHANGELOG.md`、
frontmatter の決まりの対象外の表にある置き場（チケットの置き場・チケットの下書き `wip/design/scripts/i*.md`・下書きの置き場
`wip/handoff/`・外から取り込んだ `.claude/skills/yomiyasu/**`）にも出さない。ワークツリーの md は、ワークスペースの `.gitignore` ではなくワークツリー自身の
git で決める（統合した後は統合先の索引に入るため）。

## 絞り方と出力

| オプション | 対象 | 一致 |
|---|---|---|
| `--type <値>` | `frontmatter.type` | 完全一致 |
| `--tag <値>` | `frontmatter.tags` の要素（スカラーでも 1 要素のリストとして扱う） | 完全一致 |
| `--keyword <値>` | `frontmatter.keywords` の要素 | 完全一致 |
| `--path <部分>` | 文書のパス | 部分一致 |
| `--text <部分>` | 文書のパス・更新日時・frontmatter のすべてのスカラーの値（キー名は含まない） | 部分一致 |
| `--since <日時>` / `--until <日時>` | `mtime`（`YYYY-MM-DD[THH[:MM[:SS]]]`。在る日時だけ受ける）。`--until` は書いた桁の終わりまで（日付だけなら `T23:59:59`、`THH` なら `:59:59`、`THH:MM` なら `:59`） | 以上 / 以下 |

同じオプションの繰り返しは OR、違うオプションどうしは AND。大文字小文字は区別せず、文字列は NFC に揃えてから比べる。
並べ方は `--sort path|mtime|type|title`（既定 `path`）、
`-r` / `--reverse` で逆、`--limit <N>` で並べた後の先頭 N 件（0 以下は全部）。
`--sort` の `type` と `title` は大文字小文字を区別しない。第 2 キーは `concept_id`。
`--format` は `table`（既定。`type` / `concept_id` / `title` を、全角を幅 2・結合文字を幅 0 として桁揃え）・`path`・`detail`・`json`・
`jsonl`・`count`（`matched=<絞った数> [shown=<出した数>] total=<全部>`。`shown` は `--limit` で切ったときだけ）。
`--json` は `--format json` と同じ。`table` と `detail` は同じ件数の 1 行を標準エラーにも出す。0 件でも終了コードは 0 で、
使い方の誤りだけが 1。値が `-` で始まるときは `--text=-A` のように `=` で繋ぐ。
