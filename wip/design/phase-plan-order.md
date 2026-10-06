---
type: design
title: フェーズの順序を親の計画で決める（設計草案）
description: phases.yml から順序の欄をなくし、親の plan に項どうしの先行を書き、承認画面の図でユーザが順序を決める設計の草案
tags: [phase, approval, board]
keywords: [フェーズ定義, plan, after, 先行, 待ち方, workflow.yml, 承認画面, 図, start, end, feedback, 延期, requires, overlap, order]
---

# フェーズの順序を親の計画で決める（設計草案）

採用前の草案。ADR の草案は同じ置き場の `0109-plan-order-on-plan-items.md`。

## 1. 目的

フェーズの順序（どのフェーズがどれを待つか）を、`phases.yml` のフェーズ定義から外し、親チケットの計画（`plan:` と `feedback:`）の項どうしの関係として決める。
エージェントが計画に先行（案）を書き、ユーザは承認の前に VS Code の承認画面で計画の図を見て、線を引き直して順序を決めてから承認する。

今の形の困りごと。

- 順序がフェーズ定義（`after`）とファイルの頭（`order`）にあるので、計画ごとに形を変えられない。並行させたいかどうかは計画ごとに違う
- 順序に関わる欄が 4 つ（`order` `after` `overlap` `requires`）あり、組み合わせの検査（`after` と `overlap` の両方、レイヤーの食い違い、循環）が要る
- ユーザが順序を見るのはフェーズ管理画面の図で、承認する計画そのものの図ではない

## 2. 変えるもの・変えないもの

### 変えるもの

| 何を | どう |
|---|---|
| `phases.yml` | `order`・`after`・`overlap`・`requires` をなくす。フェーズ定義だけにする（`kind` `title` `review` `scope` `deliverables` `agent` `when`） |
| 親の `plan:` / `feedback:` | 項に `after: [番号]`（先に閉じてレビューが済んでいるべき項）を書けるようにする |
| 待ち方の計算 | 定義の祖先ではなく、項の `after` から計算する。フィードバック計画も同じ計算にする |
| 承認時の検査 | 循環・終端・延期の引き受け手を、項の `after` で見る。`requires` の検査と、定義どうしの関係の検査をなくす |
| VS Code の承認画面 | 計画を持つ親（新規・改版）に図を出し、ユーザが線を引き直せるようにする。直した順序で `--agree` を呼ぶ |
| フェーズ管理画面 | 図と、関係の欄（`overlap` / `requires` / `after`）と `order` の選択をなくす。一覧の編集は残す |
| 取り下げの比べ方 | 待ち方のファイルを、今の `phases.yml` からの再計算ではなく、承認コミットに入った待ち方のファイルと比べる |
| 改版の見つけ方 | 計算し直した待ち方とファイルの違いでは改版にしない。計画（`after` を含む）が違うときだけ改版にする |

### 変えないもの

- 待ち方の保存先は `<承認済みの置き場>/phases/<親>/workflow.yml`。承認はチケットの中身を変えない。ユーザが図で直した順序もここに入る
- `workflow.yml` の形（`order` / `waits` / `review_at`）。`waits` は今と同じく推移的に辿った全部の先行を並べる
- 判定・延期・フィードバック計画の前提は `workflow.yml` だけを読む。書くのは `--agree` だけ
- 承認済みの親で `workflow.yml` が無いもの（手で動かした承認）は一直線で読む
- レビュー準備中・レビュー待ちで止める単位は親ごと。受け入れは受け入れたフェーズとその子孫にだけ効く
- 子チケットの `predecessors`（子どうしの先行）。計画の項の先行とは別のもの
- 端末の `--agree` は文字で結果を見せる。図は出さない
- 番号の振り方（全体計画が 1 から、フィードバック計画はその続き）と、子の `phase:` が番号を指すこと

## 3. `plan:` の新しい書式

### 案（採るもの）: 項に `after: [番号]` を書く

```yaml
plan:
  - research                                  # 1。何も待たない
  - {type: design, after: [1]}                # 2
  - {type: acceptance, after: [2]}            # 3
  - {type: implement, after: [2]}             # 4。3 と並行
  - {type: implement, after: [3, 4], review: defer}   # 5。同じ定義の 2 回目も番号で指す
  - {type: docs, after: [5]}                  # 6。終端
feedback:
  - implement-feedback                        # 7。何も待たない（全体計画は済んでいる）
  - {type: design-feedback}                   # 8。7 と並行
  - {type: implement-feedback, after: [7, 8]} # 9
```

決まり。

- `after` は番号のリスト。指せるのは**自分より小さい番号だけ**。番号の並びがそのまま実行できる順になり、循環は書けない
- `after` を書かない項は何も待たない（すぐ始まる）。線が全てで、隠れた待ちは作らない。同じ定義を 2 回置いても、`after` を書かなければ並行する
- `feedback:` の項が指せるのはフィードバック計画の番号だけ。全体計画はフィードバック計画の前提として全部済んでいるので、線にしない
- 項は今までどおり名前だけか辞書。辞書の欄は `type` `review` `after`
- 同じ番号を 2 度挙げたら 1 つにまとめ、`--agree` が warn を出す

理由。

- 番号は既にフェーズの名前になっている（子の `phase:`、マーカー、`workflow.yml` の鍵、`--explain` の「待つ: 1, 2」）。同じ定義を 2 回置いても番号なら必ず 1 つに決まる。新しい名前の空間を作らない
- 「小さい番号だけ」にすると、今のコードの前提（待つのは前の番号、終端は最後の番号、延期の引き受け手は後ろの番号、順序の検査は前のフェーズを順に見る）がそのまま保てる
- 改版で項を足せるのは子が承認されていない番号だけなので、番号が詰め直されて先行がずれるのは、まだ始まっていない項の中だけになる
- エージェントは `--explain` の「待つ: …」をそのまま `after` に写せる（推移的に並べた先行を書いても、意味は同じ）

### 代案

| 代案 | 採らない理由 |
|---|---|
| 項に名前（`id: impl-a`）を付け、`after: [impl-a]` で指す | 読みやすく、項を足しても指し先がずれない。だが番号と名前の 2 つの呼び名ができ、子の `phase:` は番号のまま。名前の一意の検査も要る |
| 親に辺の一覧を別に持つ（`edges: [[1, 2], [2, 3]]`） | 項を見ただけでは何を待つか分からない。項を消したときに辺が残る |
| 定義の名前で指し、2 回目は `implement#2` と書く | 番号と同じことを読みにくい形で書くだけになる |
| 大きい番号も指せる（任意の DAG） | 項の並びと関係なく順序を決められる。だが循環の検査が要り、「終端は最後の項」「延期の先は後ろ」「前のフェーズを順に見る」の前提が崩れ、直す場所が広がる。並びを変えたいときは差し戻して計画を直させる（11 の問 1） |
| 何も書かなければ一直線（今の `sequential` を既定に残す） | 書き漏れは安全側に倒れる。だが「線のない項はすぐ始まる」というユーザの決定と食い違い、図と待ち方が合わなくなる |

## 4. 待ち方の計算

`workflow.compute(parent, override=None)` の入力を、フェーズ定義から計画の項の `after` に替える。`override` は承認画面でユーザが直した先行（番号 → 番号のリスト）。

1. 項ごとの直接の先行を決める。`override` に親があればそれを、無ければ項の `after` を使う
2. 推移的に辿って `waits` に並べる（今の形と同じ。番号の小さい順）
3. フィードバック計画の項の `waits` は、全体計画の番号を全部と、フィードバック計画の中で辿った先行を並べる
4. 延期の引き受け手を計算する（5 の延期）
5. `order` はいつも `dag` で書く。`sequential` は前の版のファイルと、ファイルの無い承認済みの親を読むためだけに残す

フェーズ定義を読まなくなるので、「計画に読めない定義があれば一直線」の分岐は要らない（読めない定義は承認の検査が error で落とす）。
`phasetypes.PhaseTypes.order`・`ancestors`・`PhaseType.overlaps` は消える。

`workflow.yml` の鍵は、全体計画の番号に加えてフィードバック計画の番号も持つ。フィードバック計画を承認する改版で書き直す。
フィードバック計画の番号を持たない前の版のファイルは、フィードバック計画を一直線（前の番号を全部待つ）で読む。

## 5. 承認時の検査

`workflow.problems` に集め、全体計画とフィードバック計画に同じ規則を当てる（今はフィードバック計画の延期だけ `agree_candidates.plan_problems` が別に見ている）。

| 検査 | 全体計画 | フィードバック計画 | 重さ |
|---|---|---|---|
| `after` の形（番号の整数、範囲の中、自分より小さい、フィードバック計画の項は全体計画を指さない） | 見る | 見る | error（計画を読む段で落とす） |
| 循環 | 書けない（小さい番号だけ） | 同じ | — |
| 終端は 1 つ（最後の項が他の全部を推移的に待つ） | 見る | 見ない | error |
| 後続の無い項は延期できない（今の「最後の項は延期できない」を広げる） | 見る | 見る | error |
| 延期の引き受け手がいる（延期した項を推移的に待つ、延期していない項のうち番号の最小） | 見る | 見る | error |
| 引き受け手にレビューがある（定義の `review` が `none` なら項の `review: mr` が要る） | 見る | 見る | error |
| 何も待たない項が 2 つ以上ある | 承認画面に「すぐ始まる: 1, 3」と出す | 同じ | 知らせるだけ |

終端をフィードバック計画で求めないのは、フィードバック計画の終わりは親を閉じることで、1 つの番号を前提に使う処理が無いため。指摘ごとの対応は互いに独立なことが多い（11 の問 2）。

延期の引き受け手は今の計算（後ろでこの番号を待つ、延期していない最小の番号）のまま、フィードバック計画にも当てる。
今のフィードバック計画の「次の延期していない番号」は、全部を一直線に書いたときの特別な場合になる。
フィードバックの定義が `after` を持てないという制約は、定義から順序の欄が無くなるので消える。

### 改版

- 改版になるのは、提案の `plan` / `feedback`（`after` を含む）が承認済みチケットと違うときだけ。計算し直した待ち方とファイルの違いでは改版にしない（`agree._workflow_differs` を消す）。ユーザが図で直した順序は計画と違うのが普通なので、比べると計画が同じ提案が改版に見えてしまう
- 子が承認された番号の先行は変えられない。その番号の直接の先行が、今の `workflow.yml` から戻した直接の先行と違えば error。承認画面ではその点に入る線を動かせない
- 子が承認された番号までの延期の引き受け手を変えさせない、は今のまま
- 改版の図の初期値は提案の `after`。エージェントは改版を書くとき、今の待ち方（`--explain` の「待つ」）を写す。写し損ねた番号は上の検査か、承認画面の差分（今の待ち方との違い）で見つかる

### 取り下げ

今は「`workflow.yml` が、承認コミットの親の提案と今の `phases.yml` から計算した待ち方と同じ」で通している。ユーザが図で直すと計算と合わなくなるので、
**承認コミットに入った `workflow.yml` のバイト列と同じ**で通す。承認コミットの親の提案を引く側（`hook/core.py` から `core_withdraw` に渡す `prior_proposals`）と同じ経路で、承認コミットの `phases/<親>/workflow.yml` も引く。
承認コミットに無く今あるなら、改版で書いたものなので止める。`phases.yml` を直しても待ち方は変わらなくなるので、「`phases.yml` が変わったら取り下げられない」も消える。

### 読むときの形の検査

`workflow.yml` はユーザが図で決めた中身を持つようになる。読むとき（`approval.read_workflow`）に、`waits` の鍵と値が計画の番号の範囲にあり、値が鍵より小さいことを確かめ、外れていれば読めない待ち方として判定を止める（今の `workflow_unreadable` に乗せる）。

### `phases.yml` に残った古い欄

`order`・`after`・`overlap`・`requires` が残っていれば、読み込みは読まずに通し、`--lint` が warn で「読まない。順序は親の計画の `after` で決める」と言う。
error にしないのは、更新した直後に全部の計画の承認と `--lint` が止まらないようにするため。

## 6. 承認画面（VS Code）

置き場は今の承認のオーバーレイ（`webview/board/Approval.tsx`）。一覧に計画を持つ親（新規・改版）があれば、本文の上にその親の図を出す（11 の問 3）。

### 図

- 部品はフロー定義画面の `webview/flow/Canvas.tsx` を流用する。図の中身は新しい `core/plan-graph.ts` が組む（フローの文書と同じ形の点と線）
- 点は計画の項。番号・定義の題・見る場所（`review`、延期なら「延期 → 5」）を出す
- start と end の仮ノードを描く。先行の無い項へ start から、後続の無い項から end へ線を引く。どちらも図だけのもので、保存しない。実行ファイルは知らない。この 2 種類の線は選べず、消せない（先行を全部消せば start からの線が出る）
- フィードバック計画の図では、start を「全体計画のレビュー後」、end を「親を閉じる」と名付ける
- 子が承認された番号は、点を薄くし、入る線を動かせない
- 置き場所は start からの最長の段数で列に分け、列の中は番号順。**線を引き直しても点は動かさない**。「並べ直す」で並べ直す。位置は画面の状態にだけ持つ

### 線の引き直し

- 線を引く（m から n へ）＝ n が m を待つ。線を消す＝待たない
- 引けるかは `canConnect` で断る。自分へ・start へ入る・end から出る、は `flow-doc.ts` の規則のまま。加えて、実行ファイルが渡す「引いてよい線」（下の `may_wait_on`）に無い線を断る。小さい番号だけ、フィードバック計画は全体計画を指さない、承認済みの番号に入れない、を決めるのは実行ファイルで、画面はその一覧を当てるだけ
- 「案に戻す」で、計画に書いてあった先行に戻す

### 判定の境界

画面は判定しない。終端・延期の引き受け手・すぐ始まる項は、実行ファイルが答える。

1. 線を変えるたびに、拡張は直した先行を付けて `ccnavi --agree --preview --json --order <JSON>` を呼び直す
2. 実行ファイルは直した先行で待ち方を計算し、検査し、本文（待ちの一覧を含む）・ダイジェスト・落とす理由を返す。終端が 2 つあるなどで落ちれば、その親は `rejected[]` に理由付きで入り、画面はその文をそのまま出す
3. 承認は、最後に受けた一覧のダイジェストと同じ `--order` を付けて `--agree --yes <識別子,…> --digest <値> --order <JSON>` を打つ。ダイジェストには書く `workflow.yml` の中身が入っているので、見せた順序と書く順序は食い違わない
4. 呼び直している間は承認のボタンを押せない（承認のオーバーレイの遷移に状態を 1 つ足し、ガードとそのテストを足す）

`--order` の形は `{"<親>": {"2": [1], "3": [2], ...}}`。渡した親は、全部の番号の直接の先行をそこから取る（書いていない番号は何も待たない）。渡さない親は計画の `after` を使う。

`--agree --preview --json` に足す欄（足すだけなので版は上げない。欄が無ければ画面は図を出さない）。

```json
"plans": [
  {
    "ticket": "i0001",
    "part": "plan",
    "items": [{"number": 1, "type": "research", "title": "調査", "review": "none", "deferred": false, "locked": false}],
    "after": {"2": [1], "3": [2]},
    "proposed": {"2": [1], "3": [2]},
    "may_wait_on": {"2": [1], "3": [1, 2]},
    "current": {"2": [1]}
  }
]
```

`after` はいま使っている直接の先行（`--order` を渡したらそれ）、`proposed` は計画に書いてあった先行（「案に戻す」に使う）、`current` は改版のときの今の待ち方から戻した直接の先行（差分を描く）。

### 端末の `--agree` との関係

端末は今までどおり文字で見せる。待ちの一覧は、一直線かどうかに関わらず全部の番号に出し（今は `dag` のときだけ）、「すぐ始まる」の行を足す。端末から順序を直す手段は作らない。直したいときは VS Code で承認するか、計画を直させる。
Chrome 拡張の承認と手で動かした承認も直す手段を持たない。Chrome は計画の `after` で計算した待ち方を書く。手で動かした承認は今と同じく `workflow.yml` を書かず、一直線で読む。

## 7. 失うものと手当て

| 失うもの | 手当て |
|---|---|
| `requires`（この定義を置くなら一緒に要る）の検査 | 判定では持たない。フェーズ定義の `when`（次のフェーズを促す文と計画を書くときの案内）に「受入テスト作成を先に置く」のように書く。ユーザは承認画面の図で見る。このワークスペースの `implement` と `staging` の `when` に書き足す（staging の版に入れる） |
| `overlap`（並行の許可）の意味 | 線を引かないことで同じことが書ける。失う機能は無い |
| **辺の書き漏れが並行として通る。** 今の既定（一直線）なら書き漏れは止まる側に倒れた | (1) 終端の検査で、どこにも繋がらない項と途中で切れた枝は error になる。`after` を 1 つも書かない 2 項以上の計画は必ず落ちる。(2) 承認画面の図で、start から何本出ているかが見える。(3) 本文に「すぐ始まる: …」を出す。(4) 計画の書き方の案内に「最初の項のほかは `after` を書く」と書く。残るのは、終端には繋がっているが途中の 1 本を書き忘れた形（例: 3 と 4 を 5 が待つが、4 が 3 を待つはずだった）。これは図で見るしかない |
| ワークフローの形を定義で一度決めて使い回す手段 | 計画ごとに書く。エージェントは前の計画や `when` の案内を手本にする |
| 同じ定義を 2 回置いたら順に進む、の暗黙の待ち | 線で書く。図に出るので見落としにくい |
| フェーズ管理画面の図 | 一覧で見る。順序は計画の図で見る |
| 前の版の `workflow:` 欄で `dag` だったもの | 欄は今の計算と同じときだけ採る決まりなので、計画に `after` の無い古い欄は採られず一直線で読む（止まる側）。承認済みで動いている親は無いとユーザが確かめている |
| 承認画面の重さ | ボードの画面に React Flow が入り、`out/webview/board.js` が 180KB ほど増える見込み（フェーズ管理画面の図を入れたときの実測が +182KB）。フェーズ管理画面からは抜けるので、合計はほぼ変わらない |

## 8. 触るファイル

### エージェントが直せるもの

実行ファイル（`src/ccnavi/`）

| ファイル | 何を |
|---|---|
| `tickets/ticket_model.py` | `PlanItem.after`、`as_raw`・`__eq__`、`Ticket.review_at` / `covered_by` がフィードバック計画も `workflow` で読む |
| `tickets/ticket.py` | `_plan` で `after` を読む（形の検査、後続の無い延期）。`parse_workflow` に番号の形の検査 |
| `tickets/workflow.py` | `compute`（項の `after` と `override`）、`problems`（5 の表）、`waits_of`（フィードバック計画も `workflow` で読む）、`lines`（全部の番号と「すぐ始まる」） |
| `tickets/phasetypes.py` | `order`・`after`・`overlap`・`requires` と、その検査（参照先、`after` と `overlap` の両方、循環、自己参照）、`ancestors`、`key()` から外す。古い欄の warn |
| `tickets/phase.py` | `compute` の呼び方、`is_dag` の扱い（新しい承認はいつも `dag`） |
| `tickets/agree_candidates.py` | `plan_problems` から `requires` とフィードバック計画の延期の別扱いを外す。`revision_problems` に承認済みの番号の先行の検査 |
| `tickets/agree.py` | `_workflow_differs` を消す。`--order` を受け取って候補に渡す。JSON に `plans` |
| `tickets/agree_screen.py` | 待ちの一覧の出し方、改版の差分 |
| `tickets/agree_digest.py` | `_workflow_to_write`・`revise_copy` に `override` |
| `tickets/approval.py`・`approval_ops.py`・`approval_checks.py` | 待ち方のファイルの読み書きと形の検査、古い欄の扱い |
| `hook/core_withdraw.py`・`hook/core.py`・`hook/core_base.py` | 取り下げの比べ方（承認コミットの `workflow.yml`） |
| `entry/cli_args.py`・`entry/cli_usage.py` | `--order` |
| `entry/diagnose_board.py` | フェーズ管理画面へ渡す `order` を外す |
| `entry/diagnose_explain.py`・`entry/diagnose_shared.py`・`entry/status.py`・`entry/lint_ticket.py`・`entry/lint*.py` | 待ちの出し方、古い欄の warn、文言 |
| `tickets/review.py`・`tickets/ops_close.py` | フィードバック計画の番号の読み方を確かめる（一直線を前提にしていれば直す） |

テスト

- `tests/ticket/test_phases_dag.py`（書き直し）、`tests/ticket/test_phases.py`、`tests/ticket/test_ticket.py`、`tests/ticket/test_c1.py`、`tests/ticket/test_lint_places.py`
- `tests/config/test_config_union_phases_risk.py`、`tests/sh/test_fetch_sh.py`、`tests/sh/test_sync_sh.py`（見本の `phases.yml` と計画）
- 足すもの: 項の `after` の形、フィードバック計画の延期と並行、`--order` の計算とダイジェスト、改版の承認済みの番号、取り下げ（図で直した親を取り下げられる・改版した親は止まる）

VS Code 拡張（`extensions/vscode/ccnavi-board/`）

| ファイル | 何を |
|---|---|
| `src/core/phases-graph.ts`・`src/core/phases-route.ts`・`src/webview/phases/Graph.tsx`・`Graph.css` | 消す |
| `src/core/phases-doc.ts` | 関係の欄と `order` を外す。雛形（`TEMPLATE_PHASES_TEXT`）から `order` と `after` を外す。古い欄は手を付けずに残し、画面に「読まない欄」と出す |
| `src/core/phases-view.ts`・`src/webview/phases/{App.tsx,Phase.tsx,text.ts,state.ts,style.css}` | 図の切り替え・関係の欄・`order` の選択・図の注意と案内の段を外す |
| `src/core/plan-graph.ts`（新） | 実行ファイルの `plans` から図の中身を組む。start / end、引ける線の判定（`flow-doc.ts` の `canConnect` と `may_wait_on`）、`--order` の JSON |
| `src/webview/board/PlanGraph.tsx`・`PlanGraph.css`（新）、`Approval.tsx`・`style.css` | 承認のオーバーレイに図を入れる |
| `src/webview/flow/Canvas.tsx` | 承認の図からも使えるように、フロー固有の部分（点の種類・検査の印）を差し替えられる形にする |
| `src/core/approvemodel.ts`・`approval-machine.ts`・`board-view.ts`・`src/board-panel.ts`・`src/ccnavi.ts` | `plans` を読む、`--order` を付けて呼ぶ、呼び直し中の状態とガード |
| `src/core/tour-sample.ts` | 見本の親の計画に `after` を書く。承認の案内の見本に図を足すなら、その見本 |
| `test/phases/phases-graph*.test.ts`・`phases-notices.test.ts`・`phases-doc.test.ts`・`phases.dom.test.ts`・`phases-layer.test.ts`・`test/helpers/phases.ts` | 図のテストを消し、残りを直す |
| `test/shared/approval-machine.test.ts`（変異テストを含む）、`test/board/plan-graph*.test.ts`（新）、`test/shared/style.test.ts` | 新しい状態とガード、図の組み立て、ボードの CSS に React Flow が入ること |

Chrome 拡張: `extensions/chrome/ccnavi-approval/test/fixtures/core-scenarios.json`（実行ファイルから作り直す。`workflow.yml` の `order` が `dag` になる）と `docs/`。

文書

- `docs/design/tickets/phases.md`（9.7 の欄の表・`order` の表・待ち方・承認の検査・改版）、`docs/design/tickets/proposal-format.md`、`docs/design/tickets/approval.md`、`docs/design/tickets/hitl.md`、`docs/design/multi-repo/rules.md`、`docs/requirements/acceptance.md`、`README.md`
- `extensions/vscode/ccnavi-board/README.md`、`docs/requirements/phases.md`（図をなくす）、`docs/requirements/board.md`（承認の図）
- `.claude/skills/ccnavi-config/references/add.md`・`check.md`（関係の欄の説明を消し、順序は計画で書くと案内する）
- `docs/adr/0109-*.md`（新）と `docs/adr/README.md` の索引。`0026`・`0070`・`0078`・`0082`・`0104` の状態の行に置き換えを書く

### staging が要るもの（エージェントは書けない）

| 保護された置き場 | staging の置き場 | 中身 |
|---|---|---|
| `.ccnavi/config/phases.yml` | `wip/design/scripts/phases.yml`（全文） | `acceptance.overlap`・`implement.requires`・`staging.requires` を消す。頭のコメントの「名前を順に並べる」を「項に `after` で先行を書く」に直す。`implement` と `staging` の `when` に「受入テスト作成を先に置く」を足す |

`.ccnavi/scripts/`・`.claude/hooks/`・`rules.yml` は、調べた範囲では順序の欄にも `workflow.yml` にも触れていないので、直すものは無い見込み。実装の段で `grep` し直す。
`phases.yml` は、実行ファイルが古い欄を warn にする段（段 1）と同じ時に差し替える。先に差し替えても今の実行ファイルは一直線で動くので、順番が逆になっても困らない。

## 9. 実装の段階分け

| 段 | 中身 | 確かめ方 |
|---|---|---|
| 1 | 実行ファイル: 全体計画の項の `after`、`compute` と `problems`、`phases.yml` の古い欄を読まない（warn）、改版の見つけ方、取り下げの比べ方、`workflow.yml` の形の検査、端末の待ちの一覧。文書 9.7 と提案の書式。`phases.yml` の staging。Chrome の見本の作り直し | `uv run pytest`。端末で `ccnavi --agree` を打ち、`after` を書いた親・書き漏れた親（終端の error）・延期の親で本文を見る。図で直した形は `workflow.yml` を直接書いた見本で取り下げを試す |
| 2 | フェーズ管理画面: 図・関係の欄・`order` を消す。雛形を直す | 拡張のテスト（phases のグループ）。フェーズ管理画面を開き、古い欄のある `phases.yml` で「読まない欄」の表示と、保存で欄が残ることを見る |
| 3 | フィードバック計画の `after`、延期の計算をまとめる、`workflow.yml` にフィードバック計画の番号 | `uv run pytest`。フィードバック計画で並行と延期を書いた改版を端末で承認し、`--explain` の待ちと、`ticket start` が待つ番号で止まるかを見る |
| 4 | `--agree --preview --json` の `plans` と `--order` | `uv run pytest`。`--order` を変えるとダイジェストが変わり、古いダイジェストの `--yes` が止まることをテストで見る |
| 5 | VS Code の承認画面の図、遷移の状態とガード、見本 | 拡張のテスト（承認の遷移の変異テストを含む）。実機で計画を持つ親を承認し、線を引き直す・終端を 2 つにして落ちる・案に戻す・承認して `workflow.yml` に直した順序が入る、を見る |
| 6 | 案内と文書の仕上げ（スキル、README、ADR の状態の行と索引） | `ccnavi --lint`、文書のリンク |

段 1 と段 2 は独立に出せる。段 4 までは、ユーザは計画に書いた先行（案）のまま承認する（図はまだ無い）。

## 10. 迷ったところ

- 同じ定義を 2 回置いたときの暗黙の待ちを消した。「線のない項はすぐ始まる」に揃えるためで、残すと図に無い待ちが残る
- 手で動かした承認は一直線のまま。計画の `after` で読むと、ユーザが図を見ていない順序が並行として効く（緩む側）
- 取り下げに承認コミットの `workflow.yml` を引く経路を足す。ホストを読む側の手間が 1 つ増える
- `--order` を付けて preview を呼び直す回数は、線を 1 本変えるごとに 1 回。重ければ、線を変えてから少し待って呼ぶ形にする

## 11. ユーザに決めてほしいこと

1. **項が指せるのは自分より小さい番号だけ、でよいか。** よければ、承認画面で並びの逆の線（3 が 5 を待つ）は引けず、並びを変えたいときは差し戻して計画を直させる。代わりに任意の DAG を許すと、図だけで並びも変えられるが、終端・延期・順序の検査の前提を作り直すことになる（実装が大きくなる）
2. **フィードバック計画に「終端は 1 つ」を求めないでよいか。** 求めないと、指摘ごとの対応を並行のまま終われる。求めると全体計画と規則が揃う
3. **承認の図の置き場を、今の承認のオーバーレイの中にするか、別のパネルにするか。** オーバーレイなら承認の流れが 1 つで済むが、ボードの画面が約 180KB 重くなる。別のパネルならボードは軽いままだが、図で直した順序をオーバーレイの承認へ渡す段取りが増える。草案はオーバーレイにしている
