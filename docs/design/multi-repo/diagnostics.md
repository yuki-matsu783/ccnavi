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
重複で捨てた定義は出さない。フェーズ定義と配点は `id / 出どころのレイヤー / 主な欄` の表を足す。

```
■ rules 共通レイヤー（.ccnavi/common/rules.yml、deny 12 / ask 3 / allow 4）
  deny  guard-hooks          Write|Edit|NotebookEdit  */.claude/hooks/*
  ...
■ rules 自身のレイヤー（.ccnavi/config/rules.yml、deny 0 / ask 0 / allow 1）
  allow self:worktrees       Write|Edit               */.claude/worktrees/*
■ rules lib（projects/lib/.ccnavi/config/rules.yml、deny 2 / ask 0 / allow 1）
  deny  lib:schema           Write|Edit               */schema/*
■ phases（自身のレイヤー 7 種、lib 3 種。使うのは親の project: が指す 1 本）
  id            レイヤー     kind  title         review  scope
  design        self   work  設計          mr      wip/design/*, docs/*
  build         lib    work  ビルド        mr      src/*
■ risk（levels: medium 20 / high 40 / critical 70）
  id            レイヤー     加点条件          points  message
  big-diff      共通   lines_over 300    25      行数が多い
  schema        lib    glob db/schema/*  30      スキーマに触った
```

レイヤーごとの表は、判定と同じ順・同じ重ね方で読んだ結果を並べる。
読めないレイヤーはその位置に「読めない: <理由>」と、空として扱っていることを出す。置いていないレイヤーは「置いていない（無い = 空）」と出す。
`levels` の行に出るのは共通レイヤーの側の値で、実際に使われる値はチケットのレイヤーで決まる。共通レイヤーに `phases.yml` があれば、`--lint` が error でそのファイルを名指しし、`--explain` の phases では「共通には置けない。使わない」と出す（11.4.1）。ファイルが無いことは、どのレイヤーでも不備として出さない（11.2）。

記録の 1 行は `tree` と `project`（この呼び出しがどのツリーのものと判定されたか）に加えて、出どころのレイヤー
（`common | self | <名前>`）を持つ。入るのは判定を下したルール（当たったものの先頭）のレイヤーで、当たらなかった行は空。
配点の記録（`phases/<親>/<子>.risk.json`）は加点した項目ごとに、判定の記録（`<子>.judge.json`）は項目ごとに、その項目のレイヤーを持つ。
フェーズのマーカーは、フェーズ定義を根拠に置くものにだけそのフェーズ定義のレイヤーを書く。

`--explain --json`（ボードの JSON）は `layers[]` にレイヤーごとのルール・フェーズ定義・配点と、それぞれのパスと読めなかった理由を持つ。
拡張はレイヤーを自分で探しに行かない。

`--lint` は、`projects/` がワークスペースの `.gitignore` に入っているか、プロジェクトが `.claude/` を持たないか、
ワークツリーの元リポジトリが承認済みチケットの `project:` と合うか、走査されないチケットの置き場が残っていないか、に加えて次を言う。
`.ccnavi/config/` が無いことは言わない（無いのが正常）。

| 深刻度 | 何を言うか |
|---|---|
| info | 裸の `id` と全欄が一致する重複を後ろのレイヤーで捨てた（ルール / フェーズ定義 / 配点） |
| warn | ルールの同 `id` で中身が違う（両方適用されている） |
| warn | 配点の同 `id` で中身が違う（両方数え、後ろのレイヤーは `<レイヤー>:<id>`） |
| error | フェーズ定義の同 `id` で中身が違う、表示名がレイヤーをまたいで重なる |
| error | 配点の合成後の `levels` の逆転 |
| error | 配点の `script:` が指す先が git プロジェクトルートに無い、レイヤーの外を指している |
| error | レイヤーのファイルが壊れている（空として扱っている） |
| error | 予約名（`common` / `self`、表記違いを含む）のプロジェクトがある |
| error | 裸の `id` にコロンが書かれている（3 本とも） |
| warn | ワークツリーの `.ccnavi/` に、元リポジトリに無いファイルがある（承認済みの領域の下は数えない） |

`projects/` の置き場は固定なので、その前にワークスペースの git の索引に `projects/` の下が載っていないかを見る
（`git ls-files -s -- projects/`。プロジェクトが 1 つも無くても見る）。索引の mode で 2 つに分け、どちらも `(projects)` の warn にとどめる。

| 索引の `projects/` の下 | 何と読むか | 案内 | 「`.gitignore` に入っていない」 |
|---|---|---|---|
| 通常のファイル（mode が `160000` 以外）が 1 本以上 | ぶつかり。ワークスペース自身のソースに `projects/` がある | ワークスペースの `projects/` を別の名前に移す。プロジェクトを置かないならそのままでも動く。gitlink も載っていれば 1 本ずつ名指しし、索引から外してから改名する | 出さない（`.gitignore` に入れるとソースが追跡から外れ、誤った案内になる） |
| gitlink（mode `160000`）だけ | 載せ忘れ。`.gitignore` に入れる前に `git add` した入れ子のリポジトリ | 索引から外し（`git rm -r --cached`、コミット前なら `git reset`）、`.gitignore` に `/projects/` を足す | 出さない（索引に残る間は `.gitignore` に足しても無視されず、2 つ並ぶと誤った順で直されうる） |

`.gitmodules` に登録した意図的なサブモジュールも、mode だけで見るので載せ忘れと読む。索引を読めない
（git が無い、git のリポジトリではない）ときは何も言わない。導入スクリプトも入れ終わりに同じ条件で同じ案内を出す。
止めず、終了コードも変えず、`--check` でも「揃っていない」には数えない。VS Code 拡張は同じ warn をプロジェクト画面に
出し、どちらの場合も「`.gitignore` に足す」ボタンを出さない。

`--lint` の JSON も同じ指摘を同じ深刻度で出す。レイヤーの指摘は `where` にレイヤーの名前が入る（`(self) rule-id`、`(projects/lib) (risk) id`）。

端末から打つ `--agree` は、承認の対象の一部が落ちたら終了コード 1 で終わる。通ったぶんの承認済みチケットは置き、落ちたものは名指しする。
拡張が子プロセスで打つ `--agree --yes`（10 章）の終了コードは別。

導入スクリプト `ccnavi-setup.sh` は共通レイヤーに `rules.yml` と `risks.yml`、自身のレイヤーに `phases.yml` のひな形を配り、3 本とも
「まだ無いもの」の点検に数える。

dry-run でまずレイヤーの分布を見て、Bash の和と、大文字小文字を区別しない照合で増えた `ask` と `deny` を数えてから `enable` に切り替える。
