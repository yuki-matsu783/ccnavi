---
type: design
title: 11.9 診断と記録
description: 複数リポジトリでの診断の出力と記録
tags: [design-doc, projects, records]
keywords: [診断, --explain, 記録, レイヤー]
---

[設計書の入口](../../design.md) > [11. 複数のリポジトリ](../multi-repo.md)

### 11.9 診断と記録

**`--explain` はレイヤーごとに全件。** ルールは `id / match / glob（か regex）` を共通レイヤー・自身のレイヤー・各プロジェクトの順に、タイプごとに並べる。
フェーズ定義と配点は `id / 出どころのレイヤー / 主な欄` の表を足す。

```
■ rules 共通レイヤー（.ccnavi/config/common/rules.yml、deny 12 / ask 3 / allow 4）
  deny  workspace-root       Write|Edit|NotebookEdit  ^{root}[\\/]
  ...
■ rules 自身のレイヤー（.ccnavi/config/project/rules.yml、deny 0 / ask 0 / allow 1）
  allow self:worktrees       Write|Edit               */.claude/worktrees/*
■ rules lib（projects/lib/.ccnavi/config/project/rules.yml、deny 2 / ask 0 / allow 1）
  deny  lib:schema           Write|Edit               */schema/*
■ phases（共通レイヤー 7 種、自身のレイヤー 4 種、lib 1 種。使うのは共通 + 親の project: が指すレイヤーの和）
  id            レイヤー     kind  title         review  scope
  design        共通   work  設計          mr      wip/design/*, docs/*
  build         lib    work  ビルド        mr      src/*
■ risk（levels: medium 20 / high 40 / critical 70）
  id            レイヤー     加点条件          points  message
  big-diff      共通   lines_over 300    25      行数が多い
  schema        lib    glob db/schema/*  30      スキーマに触った
```

レイヤーごとの表は、判定と同じ順・同じ重ね方で読んだ結果を並べる。
読めないレイヤーはその位置に「読めない: <理由>」と、空として扱っていることを出す。置いていないレイヤーは「置いていない（無い = 空）」と出す。
`levels` の行に出るのは共通レイヤーの側の値で、実際に使われる値はチケットのレイヤーで決まる。phases の表はレイヤーごとに書いてある定義を並べ、判定が使うのは共通と親の `project:` が指すレイヤーの和（11.4.1）。和は `--explain --json` の `sums[].phases` に出す。ファイルが無いことは、どのレイヤーでも不備として出さない（11.2）。

記録の 1 行は `tree` と `project`（この呼び出しがどのツリーのものと判定されたか）に加えて、出どころのレイヤー
（`common | self | <名前>`）を持つ。入るのは判定を下したルール（ヒットしたものの先頭）のレイヤーで、ヒットしなかった行は空。
配点の記録（`phases/<親>/<子>.risk.json`）は加点した項目ごとに、判定の記録（`<子>.judge.json`）は項目ごとに、その項目のレイヤーを持つ。
フェーズのマーカーは、フェーズ定義を根拠に置くものにだけそのフェーズ定義のレイヤーを書く。

`--explain --json`（ボードの JSON）は `layers[]` にレイヤーごとのルール・フェーズ定義・配点と、それぞれのパスと読めなかった理由を持つ。
拡張はレイヤーを自分で探しに行かない。

`--lint` は、`projects/` がワークスペースの `.gitignore` に入っているか、プロジェクトの中にワークツリー（`.claude/worktrees/`）が無いか、
ワークツリーの元リポジトリが承認済みチケットの `project:` と合うか、走査されないチケットの置き場が残っていないか、に加えて次を言う。
`.ccnavi/config/project/` が無いことは言わない（無いのが正常）。

レイヤーをまたぐ重複と合成の指摘（裸の `id` の重複、同じ `id` で中身違い、`levels` の逆転、`script:` が指す先、レイヤーのファイルの不正、予約名のプロジェクト、ワークツリーにだけあるファイル）の深刻度は、[要件 2.10.1](../../requirements/diagnostics.md#2101-設定の検証の指摘一覧) の表が持つ。

`projects/` の置き場は固定なので、その前にワークスペースの git の索引に `projects/` の下が載っていないかを見る
（`git ls-files -s -- projects/`。プロジェクトが 1 つも無くても見る）。索引の mode で 2 つに分け、どちらも `(projects)` の warn にとどめる。

| 索引の `projects/` の下 | 何と読むか | 案内 | 「`.gitignore` に入っていない」 |
|---|---|---|---|
| 通常のファイル（mode が `160000` 以外）が 1 本以上 | ぶつかり。ワークスペース自身のソースに `projects/` がある | ワークスペースの `projects/` を別の名前に移す。プロジェクトを置かないならそのままでも動く。gitlink も載っていれば 1 本ずつ名指しし、索引から外してから改名する | 出さない（`.gitignore` に入れるとソースが追跡から外れ、誤った案内になる） |
| gitlink（mode `160000`）だけ | 載せ忘れ。`.gitignore` に入れる前に `git add` した入れ子のリポジトリ | 索引から外し（`git rm -r --cached`、コミット前なら `git reset`）、`.gitignore` に `/projects/` を足す | 出さない（索引に残る間は `.gitignore` に足しても無視されず、2 つ並ぶと誤った順で直されうる） |

`.gitmodules` に登録した意図的なサブモジュールも、mode だけで見るので載せ忘れと読む。導入スクリプトも、入れ終わりに同じ条件で同じ案内を出す。
VS Code 拡張は同じ warn をプロジェクト画面に出す。

`--lint` の指摘の全件は [要件 2.10.1](../../requirements/diagnostics.md#2101-設定の検証の指摘一覧)。

`--lint` の JSON も同じ指摘を同じ深刻度で出す。レイヤーの指摘は `where` にレイヤーの名前が入る（`(self) rule-id`、`(projects/lib) (risk) id`）。

ターミナルから打つ `user agree` は、承認の対象の一部が外れたら終了コード 4 で終わる（何も置かずに失敗したときは 1）。通ったぶんの承認済みチケットは置き、外れたものは名指しする。`ccnavi-user-agree.sh` は 4 のときだけ、置かれたぶんをコミットして push する。
拡張が子プロセスで打つ `user agree --yes`（10 章）の終了コードは別。

導入スクリプト `ccnavi-setup.sh` は共通レイヤーに `rules.yml`・`phases.yml`・`risks.yml` のひな形を配る。自身のレイヤーの `phases.yml` は配らず、既にあれば触らない。
「まだ無いもの」の点検には、`rules.yml` と `risks.yml` と、共通にも自身のレイヤーにも無い `phases.yml` を数える。
配布元に共通のテンプレート（`.ccnavi/config/common/templates/*.yml`）があるときは、配布先に無いものも挙げる。共通の工程スキルの配布先に `SKILL.md` や `references/common/` の `.md` が無いときも、書き込みのあとの点検で挙げる（`--check` は書かないので、配るものを「配る」に並べる）。

dry-run でまずレイヤーの分布を見て、Bash の和と、大文字小文字を区別しない照合で増えた `ask` と `deny` を数えてから `enable` に切り替える。
