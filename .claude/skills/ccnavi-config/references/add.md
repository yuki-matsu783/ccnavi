---
type: skill-reference
title: 足す・直す
description: ccnavi の設定（rules.yml、phases.yml、risks.yml）に新しいルールや設定を追加する方法と検証手順
tags: [rules, config, skills]
keywords: [rules.yml, phases.yml, risks.yml, deny, ask, allow, glob, regex, ルール, lint, 検証]
---

# 足す・直す

書式の細部で迷ったら `src/ccnavi/policy/rules.py` `src/ccnavi/tickets/phasetypes.py` `src/ccnavi/tickets/risk.py` の冒頭の docstring を読む。

## 手順

1. 何を変えたいかを 1 行にする（「`git push --force` を止める」など）。ユーザに渡す文の先頭になる
2. どのファイルかを決める。止める・聞く・通すなら rules、フェーズと範囲なら phases、閉じるときの重さなら risk。
   2 本にまたがるなら別々の下書きにする
3. 今の本物を読む。同じことを言うルールやフェーズ定義が既に無いか
4. 下書きをワークツリーの `wip/handoff/`（.ccnavi/docs/scratchpad.md）に書く。本物の全文を写して足す（断片だと lint に掛けられない）
5. 検証する。lint と、rules なら期待する判定。通るまで直す（[check.md](check.md)）
6. ユーザに渡す（下の「ユーザに渡す形」）。置くのはユーザ

## rules.yml

1 件のルールは「どのツールの」「何に当たったら」「何を言うか」の組。

```yaml
version: 1
deny:
  - id: force-push
    match: Bash
    glob: "*git push*--force*"
    message: リモートの履歴を書き換えます。送り直したい理由をユーザに伝えてください。
ask:
  - id: migrations
    match: Write|Edit
    glob: "*/migrations/*"
    additionalContext: 移行ファイルは実行前にユーザが中身を見る。何が変わるかを先に言うこと。
allow:
  - id: source
    match: Write|Edit
    glob: "*/src/*"
```

タイプの選び方。強い順に `deny` `ask` `allow` で、1 件でも deny に当たれば止まる。

| タイプ | 置くもの | 文面 |
|---|---|---|
| `deny` | 戻せないもの、代わりの経路があるもの | `message` 必須。なぜ止めたか＋代わりに何をするか。止められた側に向けた言葉 |
| `ask` | ユーザが毎回見たい確認ポイント。権限モードによらず必ず確認が出る | `message` は書かない（ダイアログにしか出ない）。伝えたいことは `additionalContext` |
| `allow` | 「このプロジェクトで普通にやること」。権限を配る場所ではない | `message` は書かない（どこにも届かない） |

どこにも当たらない呼び出しは Claude Code の権限モードの判断になる（`UNDECLARED`）。`allow` は deny の穴を開ける道具ではない。

コマンド名の位置に変数を書く形（`sh $L ...`、`$CMD push`）は、組み込みの shellread の `command-name-expansion` が
`DENY_COMMAND_NAME_EXPANSION` で止める。ルールに掛かる前に止まるので、ask を書く前に確かめる（効かない ask を書かないため）。

当て方。`glob` か `regex` のどちらか 1 つ。必ず引用符で囲む（`*` `&` `!` `<<` は YAML が先に読む）。

- `glob` は fnmatch と同じで文字列全体に当たる。部分一致は前後に `*` を自分で書く。
  `git push` は `cd /repo && git push` に当たらず、`*git push*` なら当たる
- 語の切れ目は無い。`*sed*` は `sedate` に当たる。右側は `*sed *` と空白で守れるが、
  左側は glob では書けない。左の切れ目が要るときだけ `regex`
- `/` は `\` にも当たる。パスのルールは `/` で書く
- `{root}` はワークスペースルートの実パスに置き換わる。glob なら `{root}/wip/*`、regex なら
  `^{root}[\\/]`。絶対パスを直書きしない
- 文面（`message` / `additionalContext` / `additionalContextOnce`）でスクリプトを案内するときは `{script:<名前>}` と書く（`sh <引用したパス>` に置き換わる。名前は `[A-Za-z0-9._-]+`。形が違う・スクリプトが無いときは lint が warn する）
- `{!root}` は「ワークスペースルートの外」。`{root}` と性質が違うので、下の節を読んでから書く
- `regex` は先読み・後読み・後方参照が使えない。組み合わせ爆発を起こす書き方をしない（hook が期限切れで素通りする）
- 「A だが B は除く」は `except:` で書く。1 本の regex に詰めると 1 文字ずつの場合分けになり、読んで確かめられない。
  値は `- glob: '...'` か `- regex: '...'` を並べたリストで、本体に当たっても、どれかに当たれば当たらない。
  各パターンは本体と同じ読み方（`{root}`・`{!root}`・大文字小文字・先読みの禁止）。
  空のリスト・`glob` と `regex` の両方・知らないキーは **error** でルールごと読まれない。
  当てるのは本体と同じ文字列で、`Bash` ならコマンド列の全体。除く語をどこかに足すだけで外れるので、
  `Bash` / `PowerShell` の `deny` / `ask` に付けると **warn**。付けるなら語の位置まで狭く書く
- 「A か B」「A かつ B」は `glob` / `regex` の代わりに `when:` で組み合わせ条件として書く。ルールに書けるのは
  `glob` / `regex` / `when` のどれか 1 つ。ノードは `glob: '...'` / `regex: '...'`（個別条件。本体と同じ読み方）、
  `all: [ノード, ...]`、`any: [ノード, ...]`、`not: ノード` のどれか 1 つのキーだけを持つ。`except:` と併せて書ける。
  知らないキー・キーが 0 個か 2 個以上のノード・空のリスト・型違い・入れ子 8 段超え・ノード 64 個超えは
  **error** でルールごと読まれない。何にでも当たる・何にも当たらないノード（`not: {glob: '*'}`、`any` の中の `glob: '*'`）と、同じ個別条件を肯定と否定で並べた `all` / `any` と、
  `Bash` / `PowerShell` の `deny` / `ask` で `not` を使った組み合わせ条件は **warn**（`not` はコマンドのどこかに語を足すだけで外れる）

  ```yaml
  - id: secrets
    match: Write|Edit
    when:
      any:
        - glob: '*.pem'
        - all:
            - glob: '*/secrets/*'
            - not: {glob: '*.md'}
    message: ...
  ```

- `except` と `when` のどちらを使うか。「本体に当たっても、ここは除く」だけなら `except`（並んだ除外を読むだけで意味が取れる。
  `workspace-root` がこの形）。「A か B」「A かつ B」、除外の中にさらに例外がある形のように、組み合わせが要るときだけ `when`。
  同じことを 1 本の regex の選択肢や場合分けで書きたくなったら、まず `when` に分けられないかを考える
- 大文字小文字は `glob` も `regex` も区別せずに当たる（コマンド名も）。区別が要る部分だけ `(?-i:...)` で囲む。
  パスの文字を除外する否定（`\.[^c\\/]` など）は囲む。囲まないと大文字の側まで除外され、当たる範囲が変わる
- `match` に書けるのは、判定が対象を取り出せるツールだけ。`Bash` `PowerShell`（コマンド）、
  `Read` `Edit` `Write` `NotebookEdit`（パス）、`Grep` `Glob`（探す場所）、`Skill`（スキル名）、
  `Agent`（見出し）、`WebFetch`（URL）。`Bash` のルールは `PowerShell` に及ばない。
  及ぼすなら `Bash|PowerShell`。ツールでない名前は `Stop`（メインエージェントのターンの終わり）だけで、
  `allow` に `glob: "*"` と `every: N` を添えて置く。渡す回にターンの終わりを止めて文を渡す。
  `every` が 2 より小さいもの、プロジェクトのレイヤーに置いたものは使われない（`--lint` が warn）。
  当たるかは `ccnavi --test Stop "(stop)"` か期待の `tool: Stop` で確かめる

Bash は実行される部分に当たる。`$( )` の中や `&&` の先も 1 本ずつ当たり、ヒアドキュメントの中身は当たらない
（既定の `heredoc` ルールが入口ごと止める）。

### `{!root}`（ワークスペースの外）の書き方

| 決まり | |
|---|---|
| 書ける場所 | `regex` だけ。`glob` に書くと **error** |
| 置ける位置 | `^` の直後に 1 回だけ。`x{!root}` も `^{!root}a{!root}` も **error** |
| 直後の量化子 | **error**（`^{!root}?` はどの対象にも当たる式になる） |
| 後ろに続く式 | 書ける（`^{!root}.*\.py$` は「外にある .py」）。ただし `.*` のように任意の位置まで進む形で受けていなければ **warn** |
| 対象がルートそのもの | 「中」。`Glob` を `path` 省略で呼ぶと対象が cwd（＝ルート）になる |
| 大文字小文字 | 区別しないので、表記だけが違うパスは「中」 |

素朴に `deny: ^{!root}` と置かない。セッションのスクラッチパッド（.ccnavi/docs/scratchpad.md）、
`projects/` の外の参照用ツリー、他ツールの一時出力先まで一度に塞がる。

- `match` を絞る。「外への書き込みを止める」なら `Write|Edit|NotebookEdit` にして `Read` は残す
- 例外が要るなら `except:` で除く（`except: [{regex: '^{!root}.*[\\/]tmp[\\/]'}]` のように、除く側にも `{!root}` が書ける）。
  除く場所が多いなら、場所を名指しするルールにする

ルートが 256 字以上の機械では組み立てられず、そのルールが見るツールが全部止まる。回復はユーザが
`rules.yml` から外すことだけで、エージェントには外せない。`--lint` が error で名指しする。
展開した式は先読みを使わず、ルートの表記を 1 字ずつ「そこで終わる・表記が違う・同じなら次の字へ」の入れ子にしたもの。詳しくは設計 5.3。

モデルに渡す一言。`additionalContext` は当たったときに進む側へ渡る文で、どのタイプにも
書ける。`additionalContextOnce` は 1 つの文脈（セッション、サブエージェントの 1 回）で最初に
当たったときだけ。長い説明は once に、毎回添える一言は once の付かない側に分ける。ファイルの本文を渡すなら
`additionalContextFile` / `additionalContextOnceFile`（ルートからの相対。先頭 4000 文字まで）。
広い `allow` には書かない（呼び出しのたびに同じ文が積まれる）。lint は何にでも当たる allow と選択肢が 3 つ以上ある regex を warn にする。

期待する判定を一緒に足す。ルールを 1 件足したら `.ccnavi/config/common/rule-expectations.yml` に、当たってほしい
期待と当たってほしくない期待を両方足す。期待は
`tool` `subject` `why` の 3 つで、置いたタイプが期待する判定。`subject` の `/repo` は
ワークスペースルートに読み替わる。その下書きも `wip/handoff/` に置き、`--test-expectations` で
本物と一緒に回す。

## phases.yml

フェーズ定義を書く。親チケットの `plan:` にフェーズ定義の名前を並べ、項に `after: [番号]` で先行を書いたものが
全体計画で、子は `phase: N` で番号を指す。フェーズ定義は順序を持たない（順序は計画の項の `after` で書く）。
計画を持つ親は、計画が使う定義の写しを `phases:` に持ち（`ccnavi --plan-order <親> --fill-phases` で差し込む）、
承認のあとの判定はその写しを読む。使う定義は共通レイヤーと親の `project:` のレイヤーの phases.yml の和で、どちらにも無ければ
計画を持つ親は承認できない。どのプロジェクトでも同じ構成を前提にできる定義は共通に、`scope` がレイアウトに依存する定義は
config に置く。共通にある定義と同じ `id` を config で別の中身にはできない（error）ので、変えたいときは別の `id` と `title` を付ける。

```yaml
version: 1
phases:
  research:
    kind: work                  # work は全体計画に、feedback はフィードバック計画にだけ置ける
    title: 調査                 # id と title はどちらも一意
    review: none                # none | mr。既定であって上限ではない（計画の項で mr に強められる）
    scope: ["wip/research/*"]   # 子が宣言できる範囲の上限。inherit なら親の範囲そのまま
    deliverables: ["wip/research/summary.md"]   # 閉じる前に親のワークツリーに在って追跡されているべきもの
    when: 既存の振る舞いや依存が分からないとき   # 案内。次のフェーズを促す文に出る
  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope: ["src/*", "tests/*"]
    when: 振る舞いを変えるとき。受入テスト作成を先に置く   # 一緒に置くべき定義の組は when に書く
  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope: ["tests/*"]
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: mr                  # feedback は mr（none は書けない）
    scope: inherit
    when: フィードバック計画の最後に置き、ほかの対応を受ける
```

決めるときに考えること:

- `review: none` にしてよいのは、その成果をユーザが見なくても次に進んで困らないフェーズ定義だけ。
  ただし実績のリスクが HIGH 以上なら宣言に関わらずレビューが済むまで止まる（risks.yml の側）
- `scope` は子 ⊆ フェーズ定義 ⊆ 親の真ん中。rules.yml の deny が止める場所を入れても deny が勝つ。
  glob はワークツリーのルートからの相対で、`..` `~` `$` と絶対パスは受け付けない
- `deliverables` は「在って追跡されている」ことだけ見る。閉じるときに無ければ
  最後の子を閉じられないので、必ず作れる名前にする
- `learning: off` を書くと、その定義の子は学びの蓄積（気づきの記録を閉じる前に求める検査）を受けない。
  小さい作業のように気づきを求める意味が薄い定義にだけ書く。既定は `on`。YAML は引用符なしの `on` / `off` を真偽値に読むが、どちらも読める
- フェーズ定義に順序は書かない。並行させたいかは計画ごとに、親の計画の項の `after` で書く（線を引かない項は並行する）。一緒に置くべき定義の組は `when` に書く
- `scope` と `deliverables` はそれぞれ 20 件まで
- `feedback` のフェーズ定義に `review: none` は書けない
- 小さい作業を親 1 本・子 1 本で回すなら、`review: mr`・`scope: inherit` の定義（例: `quick`）を 1 つ置き、親は `plan: [quick]`・`feedback: []` にする
  （親の `phases:` は `--fill-phases` で差し込む。手順は docs/design/tickets/phases.md の「小さい作業」）
- `when` と `agent` は判定に使われない案内。`when` は「次は X の計画です（定義の案内: …）」の
  形でモデルに届くので、そのフェーズ定義を飛ばしてよい条件まで書く

承認済みの親は、承認したときの定義の写し（`phases:`）で判定されるので、phases.yml を直しても進行中の親には
届かない（`--lint` が食い違いを warn で言う）。承認待ちの提案は写しが phases.yml と違うと `--agree` で落ちるので、
直したら提案に `ccnavi --plan-order <親> --fill-phases` を打ち直してもらう。

## risks.yml

子を `ticket finish` で閉じるとき、その子のワークツリーで `base_sha..HEAD` の差分を数え、点を
付ける。フェーズの点は子の最大値。HIGH 以上なら宣言に関わらずレビューが要る扱いになり、
レビューが済むまで止まる。実績が小さくても宣言のレビュー要を下げることはしない。

```yaml
version: 1
levels: {medium: 20, high: 40, critical: 70}   # リスクレベルの名前は 4 つで固定。境目の点だけ動かす
factors:
  - {id: big-diff,   points: 25, lines_over: 300,   message: 行数が多い}
  - {id: many-files, points: 15, files_over: 10,    message: ファイルが多い}
  - {id: ci,         points: 35, glob: ".github/**", max: 35, message: CI に触った}
  - {id: deletes,    points: 20, deleted_over: 3,   message: 消したファイルが多い}
  - {id: complexity, points: 30, script: .ccnavi/config/common/scripts/complexity.sh, message: 複雑度}
  - {id: untested,   points: 30, judge: テストの無い振る舞いの変更を含むか, message: テスト無し}
```

項目は 1 件につき加点条件を 1 つだけ書く。

| 加点条件 | 何を数えるか | 決めるときに考えること |
|---|---|---|
| `lines_over` / `files_over` / `deleted_over` | 差分の行数・ファイル数・消したファイル数が基準を超えたら加点 | 基準はこのプロジェクトの普通の子の大きさで決める。`ccnavi-git.sh log --shortstat` で最近の差分を見る。生成物やロックファイルが差分を膨らませるなら `exclude: ["*.lock"]` のように外し、一部のディレクトリだけ測るなら `include: ["src/**"]` を書く（どちらも既定は無指定＝全パス。`exclude` は `include` に当たっていても外す。バイナリは行数 0）。`*` は `/` をまたぐので `*.lock` は下の階層の `x.lock` にも当たる。`**/*.lock` はルート直下の `x.lock` に当たらない。末尾が `/` の glob はディレクトリ（`src/` は `src/**`）。`include` にワイルドカードの無い名前（`src`）を書くとそのファイルにしか当たらず点が 0 になるので、lint が warn する。rename は元のパスも見る（`include` は旧新どちらか、`exclude` は両方に当たったときだけ外す） |
| `glob` | 当たったファイルごとに加点。`max` で上限 | 触ったらユーザが見るべき場所（CI、移行、`.claude/`）。ワークツリーのルートからの相対（`.github/**` のように書く） |
| `script` | レイヤーの `scripts/` の下の sh（共通レイヤーは `.ccnavi/config/common/scripts/`（プロジェクトの `.ccnavi/config/common/` のミラーも同じ表記）、自身のレイヤーとプロジェクトのレイヤーは `.ccnavi/scripts/`。たがいの側は指せない）。cwd は子のワークツリー、`CCNAVI_BASE_SHA` `CCNAVI_HEAD` `CCNAVI_TICKET` `CCNAVI_PARENT` を受け取り、標準出力に整数か `{"points": N, "message": "…"}` | 失敗・無出力・読めない出力は重いほうとして扱われ、`points` が丸ごと加点される。30 秒で打ち切り。黙って 0 を出す形にしない |
| `judge` | 問いの文。親がサブエージェントに差分を読ませ、`ccnavi-ticket.sh record-risk <子> <項目> yes\|no --reason` で記録。揃うまで子は閉じられない | 差分を読んで yes / no で答えられる問いにする。「品質は十分か」は答えられない |

`levels` は `medium <= high <= critical`。リスクレベルの名前は増やせない（知らない名前は warn）。
`points` と `max` は 0 以上の整数。`id` は英数と `._-`。

基準は「軽い」と宣言した作業が大きくなったときにレビューへ戻すためのもの。普通の子が毎回 HIGH に届く配点も、
どの子も届かない配点も役に立たない。最近の子の差分で何点になるかを、渡すときに添える。

## ユーザに渡す形

```
変えるもの: .ccnavi/config/common/rules.yml（下書き: <下書きのパス>）
なぜ: <1 行>
得るもの: <何が止まる / 通る / 変わるか>
失うもの: <広がる沈黙、増える確認、閉じにくくなるフェーズ、など>
やらない場合: <今どうなっているか>
検証: --lint error 0 / warn N（内容）、期待 deny a/b ask c/d allow e/f 食い違い 0
差分:
  <本物との diff>
期待の追加: .ccnavi/config/common/rule-expectations.yml に <n> 件（下書き: <パス>）
```

置いたあとに `/ccnavi-config` の確かめる側をもう一度回す（`additionalContextFile` の相対パスや `{root}` で答えが変わる）。
