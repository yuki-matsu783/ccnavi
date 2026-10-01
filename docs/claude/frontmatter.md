---
title: md の frontmatter の決まり
type: guide
description: md の頭に付ける frontmatter のキーと type の値。ccnavi --docs の索引はこれを引く
tags: [frontmatter, docs-search]
keywords: [frontmatter, type, tags, keywords, index.jsonl, ccnavi --docs, 索引, 検索, kebab-case]
---

# md の frontmatter の決まり

`ccnavi --docs` は md の本文ではなく頭の frontmatter を引く（形と絞り込みは README「ドキュメントの索引」）。
frontmatter が無い md も一覧には出るが、`--type` `--tag` `--keyword` には当たらず、`--text` もパスと日時にしか当たらない。
**書いた frontmatter の質が、そのまま探しやすさになる。**

## キー

| キー | 要否 | 書くこと |
|---|---|---|
| `type` | 必須 | 文書の種類。下の表の値から選ぶ |
| `title` | 推奨 | 人が読む題名。本文の見出しと揃える |
| `description` | 推奨 | 何の文書かを 1 文で。一覧で開くかどうかを決める材料になる |
| `tags` | 推奨 | 横断の分類。kebab-case で 2〜4 個（`worktree`、`ticket-control` など）。既にある語に揃える |
| `keywords` | 推奨 | 本文の特徴語。3〜20 個（目安 10 個）。日本語の文書なら日本語の語も混ぜる |

```yaml
---
title: ワークツリーで作業する
type: guide
description: ワークツリーの切り方・統合先へ戻し方・片付け方と、他セッションの変更の扱い
tags: [worktree, git]
keywords: [ワークツリー, 統合先, ccnavi-git.sh, worktree add, fast-forward, 片付け, 他セッション]
---
```

- md は UTF-8 で書き、frontmatter はファイルの頭の 64 KiB の中で閉じる。それより後ろで閉じるものと、UTF-8 でないものは読めず、
  frontmatter が無い扱い（`null`）になる
- 値は 1 行で書く。`tags` と `keywords` は並び（`[a, b]`）で書く。スカラーで書いても 1 要素として読むが、揃えておく
- YAML の別名（`*名前`）は使わない。使うと frontmatter 全体が読まれず `null` になる
- 既存の語彙は `ccnavi --docs --format jsonl` の `frontmatter.tags` で見られる。同じ意味の別の綴りを増やさない

## type の値

| type | 対象 |
|---|---|
| `guide` | 使い方と作業の手引き。`README.md`、`docs/claude/*.md`、`docs/adr/README.md`、`vscode-extension/**/README.md` |
| `rule` | 常に守る決まり。`CLAUDE.md` |
| `design` | いまの実装の書き下し。`ccnavi.md`、`wip/design/**` の設計メモ |
| `requirements` | 外から観測できる約束。`requirements.md` |
| `glossary` | 用語集。`CONTEXT.md` |
| `handover` | 引き継ぎ。`HANDOVER.md` |
| `adr` | 設計判断の記録。`docs/adr/NNNN-*.md` |
| `skill` | スキル本体。`.claude/skills/*/SKILL.md`、プロジェクトの `docs/skills/*/SKILL.md` |
| `skill-reference` | スキルから切り出した資料。`.claude/skills/*/references/*.md` |
| `report` | 調べた結果・作業の報告を残すもの。置き場はその都度（`wip/` の下など） |

表に無い種類の文書を足すときは、この表に行を足してから使う。

## プロジェクト

`projects/<名前>/` の md も同じ決まりで書く（`ccnavi --docs` はワークスペースとプロジェクトを横断し、パスは
`projects/<名前>/…` で出る）。プロジェクトの置き場ごとの文書（`docs/spec/` など）は、上の表の近い値を使うか、表に行を足す。
索引に載るのは、そのリポジトリの `.gitignore` に `**/index.jsonl` があるプロジェクトだけ。無いプロジェクトは SessionStart の
案内で名指しされる。足すのはそのプロジェクトのチケットの範囲で行う（ccnavi は書き換えない。導入スクリプト
`scripts/ccnavi-setup.sh` が足すのはワークスペースの `.gitignore` だけ）。

## 索引のファイル

- `index.jsonl` は md のあるディレクトリごとに ccnavi が書く生成物。手で直さない。同じ名前でよその道具のファイルを
  置いていると、ccnavi はそれを書き換えず、案内で名指しする
- 初めての回（`--docs` か SessionStart）は md を全部読むので、md が数千本あると数秒かかる。SessionStart は短い期限で打ち切り、
  続きは次の回に回す。2 回目からは更新日時の変わった md だけを読む
- md を消しても `index.jsonl` が残ることがある（追跡されていない md だけのディレクトリ、ディレクトリごと消したときなど）。
  残ったものは読まれないので結果は変わらない。気になれば消してよい（git に無視されているので差分は出ない）

## 対象外

| もの | 扱い | なぜ |
|---|---|---|
| チケット（`wip/proposals/**`、`.ccnavi/approved/**`、`wip/design/scripts/i*.md` などの下書き） | 付けない | チケットは独自の frontmatter（`version:` `ticket:` …）を持ち、ccnavi が読む。`.ccnavi/` の下は索引にも載らない |
| `SKILL.md` の `name` と `description` | 変えない | Claude Code がスキルを選ぶのに使う実キー。`description` は流用し、`type` などを下に足す |
| 下書き（`scratchpad/`） | 付けなくてよい | 使い捨て。`.gitignore` にあるので索引にも載らない |
| `.claude/skills/yomiyasu/**` | 付けない | vendored（`UPSTREAM.md`）。中身を書き換えないので frontmatter も足さない |

既にある md に付けるときは、既存のキーの値と順を変えず、足りないキーだけを下に足す。
