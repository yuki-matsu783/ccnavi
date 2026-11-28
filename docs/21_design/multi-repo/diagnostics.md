---
type: design
title: 11.9 診断と記録
description: 複数リポジトリでの診断の出力と記録
tags: [design-doc, projects, records]
keywords: [診断, --explain, 記録, 設定]
---

[設計書の入口](../../20_design.md) > [11. 複数のリポジトリ](../multi-repo.md)

### 11.9 診断と記録

**`--explain` は設定ごとに全件。** ルールは `id / match / glob（か regex）` を共通設定・ワークスペースの設定・各プロジェクトの順に、タイプごとに並べる。
フェーズ定義と配点は `id / 出どころの設定 / 主な欄` の表を足す。

```
■ rules 共通設定（.ccnavi/config/common/rules.yml、deny 12 / ask 3 / allow 4）
  deny  workspace-root       Write|Edit|NotebookEdit  ^{root}[\\/]
  ...
■ rules ワークスペースの設定（.ccnavi/config/project/rules.yml、deny 0 / ask 0 / allow 1）
  allow self:worktrees       Write|Edit               */.claude/worktrees/*
■ rules lib（projects/lib/.ccnavi/config/project/rules.yml、deny 2 / ask 0 / allow 1）
  deny  lib:schema           Write|Edit               */schema/*
■ phases（共通設定 7 種、ワークスペースの設定 4 種、lib 1 種。共通設定に、親チケットの project: で指定された設定を足して適用します）
  id            設定     kind  title         review  scope
  design        共通設定 work  設計          true    wip/design/*, docs/*
  build         lib          work  ビルド        false   src/*
■ risk（levels: medium 20 / high 40 / critical 70）
  id            設定     加点条件          points  message
  big-diff      共通設定 lines_over 300    25      行数が多い
  schema        lib          glob db/schema/*  30      スキーマに触った
```

設定ごとの表は、判定と同じ順・同じ足し方で読んだ結果を並べる。
読めない設定はその位置に「読めない: <理由>。この設定は空として扱う」と出す（phases と risk の表では頭に設定の名前を付ける）。rules で置いていない設定は「この設定ファイルはありません。設定なしとして扱います。」と出す。phases の `review` 列はレビューが要るかの真偽値（`true` / `false`）。
`levels` の行に出るのは共通その設定の値で、実際に使われる値はチケットの設定で決まる。phases の表は設定ごとに書いてある定義を並べ、判定が使うのは共通設定に親の `project:` が指す設定を足した統合結果（11.4.1）。和は `--explain --json` の `sums[].phases` に出す。ファイルが無いことは、どの設定でも不備として出さない（11.2）。

記録の 1 行は `tree` と `project`（この呼び出しがどのツリーのものと判定されたか）に加えて、出どころの設定
（`common | self | <名前>`）を持つ。入るのは判定を下したルール（ヒットしたものの先頭）の設定で、ヒットしなかった行は空。
配点の記録（`phases/<親>/<子>.risk.json`）は加点した項目ごとに、判定の記録（`<子>.judge.json`）は項目ごとに、その項目の設定を持つ。
フェーズのマーカーは、フェーズ定義を根拠に置くものにだけそのフェーズ定義の設定を書く。

`--explain --json`（ボードの JSON）は `layers[]` に設定ごとのルール・フェーズ定義・配点と、それぞれのパスと読めなかった理由を持つ。
拡張は設定を自分で探しに行かない。

`--lint` は、`projects/` がワークスペースの `.gitignore` に入っているか、プロジェクトの中にワークツリー（`.claude/worktrees/`）が無いか、
ワークツリーの元リポジトリが承認済みチケットの `project:` と合うか、走査されないチケットの置き場が残っていないか、に加えて次を言う。
`.ccnavi/config/project/` が無いことは言わない（無いのが正常）。

設定をまたぐ重複と合成の指摘（裸の `id` の重複、同じ `id` で中身違い、`levels` の逆転、`script:` が指す先、設定ファイルの不正、予約名のプロジェクト、ワークツリーにだけあるファイル）の深刻度は、[要件 2.10.1](../../11_requirements/diagnostics.md#2101-設定の検証の指摘一覧) の表が持つ。

`projects/` の置き場は固定なので、その前にワークスペースの git の索引に `projects/` の下が載っていないかを見る
（`git ls-files -s -- projects/`。プロジェクトが 1 つも無くても見る）。索引の mode で 2 つに分け、どちらも `(projects)` の warn にとどめる。

| 索引の `projects/` の下 | 何と読むか | 案内 | 「`.gitignore` に入っていない」 |
|---|---|---|---|
| 通常のファイル（mode が `160000` 以外）が 1 本以上 | ぶつかり。ワークスペースのソースに `projects/` がある | ワークスペースの `projects/` を別の名前に移す。プロジェクトを置かないならそのままでも動く。gitlink も載っていれば 1 本ずつ名指しし、索引から外してから改名する | 出さない（`.gitignore` に入れるとソースが追跡から外れ、誤った案内になる） |
| gitlink（mode `160000`）だけ | 載せ忘れ。`.gitignore` に入れる前に `git add` した入れ子のリポジトリ | 索引から外し（`git rm -r --cached`、コミット前なら `git reset`）、`.gitignore` に `/projects/` を足す | 出さない（索引に残る間は `.gitignore` に足しても無視されず、2 つ並ぶと誤った順で直されうる） |

`.gitmodules` に登録した意図的なサブモジュールも、mode だけで見るので載せ忘れと読む。導入スクリプトは、入れ終わりに `--lint` を起こしてこの案内をそのまま見せる。
VS Code 拡張は同じ warn をプロジェクト画面に出す。

`--lint` の指摘の全件は [要件 2.10.1](../../11_requirements/diagnostics.md#2101-設定の検証の指摘一覧)。

`--lint` の JSON も同じ指摘を同じ深刻度で出す。設定の指摘は `where` に設定の名前が入る（`(self) rule-id`、`(projects/lib) (risk) id`）。

ターミナルから打つ `user agree` は、承認の対象の一部が外れたら終了コード 4 で終わる（何も置かずに失敗したときは 1）。通ったぶんの承認済みチケットは置き、外れたものは名指しする。`ccnavi-user-agree.sh` は 0 と 4 のどちらでも、この承認で書いたパスをコミットして push し、4 のときは最後に 1 で終わる。
拡張が子プロセスで打つ `user agree --yes`（10 章）の終了コードは別。

導入スクリプト `ccnavi-setup.sh` は共通設定に `rules.yml`・`phases.yml`・`risks.yml`・`ticket-sections.yml` のひな形を配る。ワークスペースの設定の `phases.yml` は配らず、既にあれば触らない。
「まだ無いもの」の点検には、`rules.yml` と `risks.yml` と、共通にもワークスペースの設定にも無い `phases.yml` を数える。
配布元に共通のテンプレート（`.ccnavi/config/common/templates/*.yml`）があるときは、配布先に無いものも挙げる。配るスキル（工程スキルと配るだけのスキル）の配布先に `SKILL.md` や `references/common/` の `.md` が無いときも、書き込みのあとの点検で挙げる（`--check` は書かないので、配るものを「配る」に並べる）。

dry-run でまず設定の分布を見て、Bash の統合結果と、大文字小文字を区別しない照合で増えた `ask` と `deny` を数えてから `enable` に切り替える。
