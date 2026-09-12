# 足す・直す

3 本とも、書き方の正解は実行ファイルの読み方にしか無い。書式の細部で迷ったら
`ccnavi/rules.py` `ccnavi/phasetypes.py` `ccnavi/risk.py` の冒頭の docstring を読む。
ここに書くのは、書式ではなく**何を決めるファイルか**と、書いた人が踏みやすい穴。

## 手順

1. **何を変えたいかを 1 行にする。** 「`git push --force` を止める」「調査フェーズを足す」
   「CI に触ったら重くする」。この 1 行が、あとで利用者に渡す文の先頭になる
2. **どのファイルかを決める。** 止める・聞く・通すなら rules。作業の段階と範囲なら phases。
   閉じるときの重さなら risk。2 本にまたがるなら別々の下書きにする
3. **今の本物を読む。** 同じことを言うルールや種類が既に無いか。足すより直すほうが
   よいことが多い
4. **下書きを scratchpad に書く。** 本物の全文を写して、そこに足す。断片だけだと lint に
   掛けられない
5. **検証する。** lint と、rules なら見本。通るまで直す（[check.md](check.md) の手順）
6. **利用者に渡す。** 何をなぜ変えるか、得るもの、失うもの、下書きの場所、差分。
   置くのは利用者

## rules.yml

1 件のルールは「どのツールの」「何に当たったら」「何を言うか」の組。

```yaml
version: 3
deny:
  - id: force-push
    match: Bash
    glob: "*git push*--force*"
    message: リモートの履歴を書き換えます。送り直したい理由を利用者に伝えてください。
ask:
  - id: migrations
    match: Write|Edit
    glob: "*/migrations/*"
    additionalContext: 移行ファイルは実行前に人が中身を見る。何が変わるかを先に言うこと。
allow:
  - id: source
    match: Write|Edit
    glob: "*/src/*"
```

**タイプの選び方。** 強い順に `deny` `ask` `allow` で、1 件でも deny に当たれば止まる。

| タイプ | 置くもの | 文面 |
|---|---|---|
| `deny` | 戻せないもの、代わりの経路があるもの | `message` 必須。なぜ止めたか＋代わりに何をするか。止められた側に向けた言葉 |
| `ask` | 人が毎回見たい確認ポイント。権限モードによらず必ず確認が出る | `message` は書かない（ダイアログにしか出ない）。伝えたいことは `additionalContext` |
| `allow` | 「このプロジェクトで普通にやること」。権限を配る場所ではない | `message` は書かない（どこにも届かない） |

どこにも当たらない呼び出しは Claude Code の権限モードに落ちる（`UNDECLARED`）。
`allow` を 1 行足すたびに、ccnavi が何も言わない範囲が広がる。deny の穴を開ける道具ではない。

**当て方。** `glob` か `regex` のどちらか 1 つ。必ず引用符で囲む（`*` `&` `!` `<<` は YAML が先に読む）。

- `glob` は fnmatch と同じで**文字列全体**に当たる。部分一致は前後に `*` を自分で書く。
  `git push` は `cd /repo && git push` に当たらず、`*git push*` なら当たる
- 語の切れ目は無い。`*sed*` は `sedate` に当たる。右側は `*sed *` と空白で守れるが、
  左側は glob では書けない。左の切れ目が要るときだけ `regex`
- `/` は `\` にも当たる。パスのルールは `/` で書く
- `{root}` はワークスペースルートの実パスに置き換わる。glob なら `{root}/wip/*`、regex なら
  `^{root}[\\/]`。絶対パスを直書きしない
- `regex` は先読み・後読み・後方参照が使えない。組み合わせ爆発を起こす書き方は、判定が
  固まって hook が期限切れで素通りする入口になる
- `match` に書けるのは、判定が対象を取り出せるツールだけ。`Bash` `PowerShell`（コマンド）、
  `Read` `Edit` `Write` `NotebookEdit`（パス）、`Grep` `Glob`（探す場所）、`Skill`（スキル名）、
  `Agent`（見出し）、`WebFetch`（URL）。`Bash` のルールは `PowerShell` に及ばない。
  及ぼすなら `Bash|PowerShell`

**Bash は実行される部分に当たる。** コマンドはシェルとして読まれ、`$( )` の中や `&&` の
先も 1 本ずつ当たる。ヒアドキュメントの中身は当たらない（既定の `heredoc` ルールが入口ごと
止めている）。

**モデルに渡す一言。** `additionalContext` は当たったときに進む側へ渡る文で、どのタイプにも
書ける。`additionalContextOnce` は 1 つの文脈（セッション、サブエージェントの 1 回）で最初に
当たったときだけ。長い説明は once に、毎回添える一言は無印に分ける。ファイルの本文を渡すなら
`additionalContextFile` / `additionalContextOnceFile`（ルートからの相対。先頭 4000 文字まで）。
**広い `allow` には書かない。** `ls` のたびに同じ文が積まれ、2 回目から読まれなくなる。
lint は何にでも当たる allow と選択肢が 3 つ以上ある regex を warn にする。

**見本を一緒に足す。** ルールを 1 件足したら `.claude/ccnavi/rule-samples.yml` に、当たってほしい
見本と**当たってほしくない見本を両方**足す。片側だけでは広げすぎに気づけない。見本は
`tool` `subject` `why` の 3 つで、置いたタイプが期待する判定。`subject` の `/repo` は
ワークスペースルートに読み替わる。見本の下書きも scratchpad に置き、`--test-samples` で
本物と一緒に回す。

## phases.yml

フェーズの**種類**を定義する。親チケットの `plan:` に種類の名前を順に並べたものが全体計画で、
子は `phase: N` で番号を指す。種類が無ければ番号しか無く、`plan:` は読めない。

```yaml
version: 1
phases:
  research:
    kind: work                  # work は全体計画に、feedback はフィードバック計画にだけ置ける
    title: 調査                 # id と title はどちらも一意
    review: none                # none | mr。既定であって上限ではない（計画の項で mr に強められる）
    scope: ["wip/research/*"]   # 子が宣言できる範囲の上限。inherit なら親の範囲そのまま
    deliverables: ["wip/research/summary.md"]   # 閉じる前に親の作業ツリーに在って追跡されているべきもの
    when: 既存の振る舞いや依存が分からないとき   # 案内。次のフェーズを促す文に出る
  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope: ["src/*", "tests/*"]
    requires: [acceptance]      # 計画に置くなら一緒に要る種類
  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope: ["tests/*"]
    overlap: [implement]        # 並行してよい（対称）。前が閉じる前に次を承認できる
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: mr                  # feedback は mr 固定
    scope: inherit
```

**決めるときに考えること。**

- `review: none` にしてよいのは、その成果を人が見なくても次に進んで困らない種類だけ。
  ただし実績のリスクが HIGH 以上なら宣言に関わらずゲートが閉じる（risk.yml の側）
- `scope` は子 ⊆ 種類 ⊆ 親の真ん中。広く書けば子が何でも宣言できる。rules.yml の deny が
  止める場所（`.claude/hooks/*` など）を scope に入れても deny が勝つので、入れる意味は無い。
  glob は作業ツリーのルートからの相対で、`..` `~` `$` と絶対パスは受け付けない
- `deliverables` は「在って追跡されている」ことだけ見る。中身は見ない。閉じるときに無ければ
  最後の子を閉じられないので、必ず作れる名前にする
- `overlap` は対称に効く。`acceptance: overlap: [implement]` と書けば implement 側にも効く。
  `requires` は計画を承認するときに見る（「implement を置くなら acceptance も要る」）
- `feedback` の種類は必ず `review: mr`。フィードバック対応の結果を人が見ない道は作らない
- `when` と `agent` は判定に使われない案内。`when` は「次は X の計画です（種類の案内: …）」の
  形でモデルに届くので、その種類を飛ばしてよい条件まで書く

**既に承認された親がある間は慎重に。** 開いている親の `plan:` が指す種類を消す・名前を変える
と、その親の計画が読めなくなる。`--explain` で承認済みの親と計画を見てから変える。

## risk.yml

子を `ticket done` で閉じるとき、その子の作業ツリーで `base_sha..HEAD` の差分を数え、点を
付ける。フェーズの点は子の最大値。**HIGH 以上なら宣言に関わらずレビューが要る扱いになり、
ゲートが閉じる。** 実績が小さくても宣言のレビュー要を下げることはしない。

```yaml
version: 1
levels: {medium: 20, high: 40, critical: 70}   # 段階の名前は 4 つで固定。閾値だけ動かす
factors:
  - {id: big-diff,   points: 25, lines_over: 300,   message: 行数が多い}
  - {id: many-files, points: 15, files_over: 10,    message: ファイルが多い}
  - {id: ci,         points: 35, glob: ".github/**", max: 35, message: CI に触った}
  - {id: deletes,    points: 20, deleted_over: 3,   message: 消したファイルが多い}
  - {id: complexity, points: 30, script: .claude/ccnavi/risk/complexity.sh, message: 複雑度}
  - {id: untested,   points: 30, judge: テストの無い振る舞いの変更を含むか, message: テスト無し}
```

項目は 1 件につき当て方を **1 つ**だけ書く。

| 当て方 | 何を数えるか | 決めるときに考えること |
|---|---|---|
| `lines_over` / `files_over` / `deleted_over` | 差分の行数・ファイル数・消したファイル数が閾値を**超えた**ら加点 | 閾値はこのプロジェクトの普通の子の大きさで決める。`ccnavi-git.sh log --shortstat` で最近の差分を見る |
| `glob` | 当たったファイル**ごと**に加点。`max` で上限 | 触ったら人が見るべき場所（CI、移行、`.claude/`）。作業ツリーのルートからの相対。`**` が使える |
| `script` | `.claude/ccnavi/` か `.claude/scripts/` の下の sh。cwd は子の作業ツリー、`CCNAVI_BASE_SHA` `CCNAVI_HEAD` `CCNAVI_TICKET` `CCNAVI_PARENT` を受け取り、標準出力に整数か `{"points": N, "message": "…"}` | 失敗・無出力・読めない出力は**重い側**に倒れて `points` が丸ごと加点される。30 秒で打ち切り。黙って 0 を出す形にしない |
| `judge` | 問いの文。親がサブエージェントに差分を読ませ、`ccnavi-ticket.sh judge <子> <項目> yes\|no --reason` で記録。揃うまで子は閉じられない | 差分を読んで yes / no で答えられる問いにする。「品質は十分か」は答えられない |

`levels` は `medium <= high <= critical`。段階の名前は増やせない（知らない名前は warn）。
`points` と `max` は 0 以上の整数。`id` は英数と `._-`。

**閾値は宣言を裏切るために在る。** 「軽い」と宣言した作業が大きくなったときにレビューへ
戻すのが目的なので、普通の子が毎回 HIGH に届く配点は、宣言の意味を消す。逆に届かない配点は
入っていないのと同じ。最近の子の差分で何点になるかを、渡すときに添える。

## 利用者に渡す形

```
変えるもの: .claude/ccnavi/rules.yml（下書き: <scratchpad のパス>）
なぜ: <1 行>
得るもの: <何が止まる / 通る / 変わるか>
失うもの: <広がる沈黙、増える確認、閉じにくくなるフェーズ、など>
やらない場合: <今どうなっているか>
検証: --lint error 0 / warn N（内容）、見本 deny a/b ask c/d allow e/f 食い違い 0
差分:
  <本物との diff>
見本の追加: .claude/ccnavi/rule-samples.yml に <n> 件（下書き: <パス>）
```

置いたあとに `/ccnavi-config` の確かめる側をもう一度回す。下書きで通った検証は、置き場所が
違えば別の答えになる（`additionalContextFile` の相対パス、`{root}`）。
