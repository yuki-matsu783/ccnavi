---
type: design
title: 待ち方のファイル（workflow.yml）を廃止できるかの調査
description: 計画の項に after を持たせる新しい設計で、phases/<親>/workflow.yml が承認済みの計画から計算し直せるか、廃止した場合の差と推奨
tags: [phase, approval]
keywords: [workflow.yml, 待ち方, waits, review_at, order, 廃止, 取り下げ, 改版, 手で動かした承認, C1, Chrome, ダイジェスト]
---

# 待ち方のファイル（workflow.yml）を廃止できるかの調査

調査だけ。草案本体（`phase-plan-order.md`・`0109-plan-order-on-plan-items.md`）は直していない。
前提は草案の決定済みの形（計画の項に `after`、`phases.yml` に順序が無い、図で直した順序は承認の前に `--reorder` で提案へ書き戻す（R1））。

## 1. 承認済みの計画から計算し直せるか

**計算し直せる。** 新しい `workflow.compute` の入力は、承認済みチケットの `plan:` / `feedback:` だけになる。

| 出すもの | 入力 | 計画の外の入力 |
|---|---|---|
| `waits` | 各項の `after`（推移的に辿る）。フィードバック計画の項は全体計画の番号を全部足す | 無い |
| `review_at` | 各項の `review: defer` と `waits` | 無い（今の `_defer_target` も定義を読まない） |
| `order` | 新しい承認はいつも `dag` | 無い |

計画の外のものは計算に入らない。

- `phases.yml` の `review` は、承認の検査（引き受け手にレビューがあるか）でだけ読む。計算には入らない。承認のあとに `review` を `none` に直しても `waits` / `review_at` は変わらない。変わるのは「引き受け手のフェーズでレビューを求めるか」で、これは今も `phases.yml` から判定のたびに読んでいる（ファイルの有無と関係が無い）
- `kind` は承認の検査（全体計画とフィードバック計画のどちらに置けるか）でだけ読む
- チケットの状態（`state`）は、今の `workflow.effective` が「承認済みでファイルが無い → 一直線」を選ぶのに使っている。これは計算の入力ではなく、ファイルの有無で読み方を切り替える分岐（下の 3）
- 図で直した順序は、R1 で承認の前に提案へ入るので、承認済みチケットの計画に含まれる

計画と待ち方がずれる経路は、ファイルを残す今の草案でも無い。改版は承認済みチケットの計画とファイルを同じ手順で書き直し（`agree_digest.revise_copy`）、それ以外に承認済みの置き場の計画を変える手段はユーザの手だけ。

**計算し直せない、または結果が今と違う場合。**

| 場合 | ファイルがあるとき | 計画から計算するとき |
|---|---|---|
| 手で動かした承認（`--agree` を通らない） | ファイルが無いので一直線で読む | 計画の `after` で読む。並行が効く（緩む側）。`--agree` の検査（終端・延期の引き受け手）も通っていない |
| 前の版の承認（`ccnavi_approved` と `workflow:` 欄を持つ） | 欄が今の計算と同じときだけ採り、違えば一直線 | `after` が無い計画なので、全部の項が並行になる |
| 閉じた古い親（`done/`）の表示 | ファイルがあればそれ | `after` の無い古い計画は全部並行に見える（判定には使わない） |
| 計算の決まりを後の版で直した | 承認したときの答えのまま | 進行中の親の待ちが版の更新で変わる |

上の 3 つは、「2 項以上あって `after` が 1 つも無い計画は一直線で読む」という読み方の決まりを 1 つ置けば、今と同じ側（止まる側）に倒せる。
新しい形では、`after` の無い 2 項以上の計画は終端の検査で `--agree` を通らないので、この形は前の版か手で動かしたものにしか現れない。
手で動かした承認でも `after` を書いた計画は、計画の `after` で読まれる（下の 3 の迷う点）。

## 2. workflow.yml を読む・書く・比べる箇所

役割の欄: 正＝判定がこれを答えとして読む、写し＝計算できるものを固定したもの、比較＝改版・取り下げ・ダイジェストで見比べる、表示＝人に見せるだけ、管理＝ファイルそのものの扱い。

実行ファイル（`src/ccnavi/`）

| 箇所 | 何をしているか | 役割 |
|---|---|---|
| `tickets/approval.py` `load_copy`・`read_workflow`・`workflow_path`・`workflow_bytes`・`write_workflow` | 承認済みの親を読むときにファイルを `Ticket.workflow` に入れる。読めなければ `workflow_unreadable` | 正（判定はこの値だけを読む）。管理 |
| `tickets/approval.py` `settle_old_workflows` | 前の版の `workflow:` 欄を、今の計算と同じときだけ採る | 比較（前の版の扱い） |
| `tickets/approval_ops.py` `admit` | 新規の承認で、提案を動かす前にファイルを書き、落ちたら戻す | 管理 |
| `tickets/agree_digest.py` `carried`・`_workflow_to_write`・`revise_copy` | 書く中身にファイルのバイト列を足してダイジェストに入れる。改版でファイルを書き直す | 比較（ダイジェスト）。管理 |
| `tickets/agree.py` `_workflow_differs`・`plan_batch` | 計画が同じでも、計算し直した待ち方がファイルと違えば改版にする。承認でファイルを書く | 比較（改版の見つけ方）。管理 |
| `tickets/agree_screen.py` | 改版の画面で、ファイルの待ち方と計算し直した待ち方の差を見せる | 表示・比較 |
| `tickets/agree_candidates.py` `revision_problems`・`_workflow_field` | 承認済みの番号までの延期の引き受け手が変わらないかをファイルの値で見る。提案の `workflow:` 欄を拒む | 比較 |
| `tickets/approval_checks.py` `blocking_problems` | 新しい形の承認済みチケットの `workflow:` 欄と、読めないファイルで判定を止める | 正（壊れたら止める） |
| `tickets/workflow.py` `effective`・`waits_of` | ファイルがあればそれ、無ければ承認済みは一直線、提案はその場で計算 | 正 |
| `tickets/ticket_model.py` `Ticket.review_at`・`covered_by` | 延期の引き受け手を `workflow.review_at` から読む | 正 |
| `tickets/phase.py` `is_dag`・`waits_done`・`order_problems`・局面と次の案内（597・1015 付近）、313 行付近 | 子の承認の順序、次の案内、局面。承認前の親はその場で計算した写しを入れる | 正 |
| `tickets/approval_marks.py` `accepted` | 受け入れを、そのフェーズと待つ番号にだけ当てる | 正 |
| `tickets/review.py` 734 行付近 | 最後のレビューが延期を引き受けるか | 正 |
| `tickets/ticket.py` `parse_workflow`・欄の読み | ファイルと前の版の欄の形を読む | 管理 |
| `hook/core_withdraw.py` | 取り下げでファイルを今の計算と比べ、一緒に消す。マーカーの有無の検査からファイルを外す | 比較・管理 |
| `hook/c1.py` | ファイルを「ユーザの判断（`--agree`）が書くもの」として運ぶ | 管理 |
| `entry/lint_ticket.py` | 親が無いのに残ったファイルを warn。前の版の欄の食い違いを warn | 管理 |
| `entry/status.py` | 計画付きの親にファイルが無ければ「一直線で読む」と warn | 表示 |
| `entry/diagnose_explain.py` | `--explain` の「待つ: …」 | 表示（値は `Ticket.workflow` から） |

拡張・Chrome 拡張

| 箇所 | 役割 |
|---|---|
| VS Code: `src/core/model.ts`（`review_at`）、`tour-sample.ts`、`test/fixtures/board.json` | 表示（`--explain --json` の値を読むだけ。ファイルは読まない） |
| Chrome: 同梱の ccnavi が取り下げで返す「消すもの」にファイルが入る。`test/fixtures/core-scenarios.json`、`test/fixtures/host/*/withdraw-*`（5 本）、`test/withdraw-host.test.ts`・`gitlab.test.ts`・`write.test.ts`、`VERIFY.md`、`docs/requirements/withdraw.md`（REQ-CHR-02） | 比較・管理（同梱のコアが決めたものを運ぶ） |

テスト: `tests/ticket/test_core.py`・`test_c1.py`・`test_phases_dag.py`・`test_ticket.py`、`tests/core/test_fsio.py`・`test_module_layers.py`、`tests/sh/test_compat_skew.py`、`tests/config/test_config_union_phases_risk.py`。

文書: `docs/design/tickets/phases.md`・`approval.md`・`state-transitions.md`、`README.md`、`docs/adr/0104-*`・`0105-*`（状態の行）。

## 3. 廃止した場合の設計

**`Ticket.workflow` の型と読む側はそのまま残し、入れ方だけを変える。** `approval.load_copy` がファイルを読む代わりに `workflow.compute(ticket)` を入れる。提案も承認済みも同じ関数で入るので、`workflow.effective` の分岐（承認済みでファイルが無ければ一直線）は消える。`Ticket.review_at`・`phase.py`・`approval_marks.accepted`・`review.py` は手を入れずに済む。

| 項目 | 廃止したとき |
|---|---|
| 判定の挙動 | `--agree` を通った親は同じ答え（ファイルは同じ計算の写しだったので）。違うのは 1 の表の場合だけ |
| 壊れた計画 | ファイルが読めないときに止めていた（`workflow_unreadable`）代わりに、判定の側（`blocking_problems`）で `workflow.problems` を当て、終端・延期の引き受け手に error があれば止める。手で動かした承認が `--agree` の検査を通っていないぶんを、ここで受ける |
| 取り下げ | 「`doing/` が承認コミットの親の提案とバイト単位で同じ」だけになる。待ち方は `doing/` から決まるので、待ち方の比較とファイルを消す段が要らない。**W1 は変わらない**（取り下げられない原因は `doing/` と承認コミットの親の提案の違いで、ファイルとは関係が無い） |
| 改版の見つけ方 | 計画（`after` を含む）が承認済みチケットと違うときだけ。`_workflow_differs` は消える。改版の画面の差分は、承認済みチケットの計画から計算した待ち方と提案から計算した待ち方を比べる |
| 延期の引き受け手を変えさせない | 承認済みチケットの計画から計算した `review_at` と、提案から計算した `review_at` を比べる（今の `workflow.effective(current)` が計算に替わるだけ） |
| ダイジェスト | 書く中身はチケットのバイト列だけ。待ち方はそのバイト列から決まるので、見せたものと効くものは今と同じく縛られる |
| 性能 | 1 親あたり項数 n に対して O(n²)（推移的に辿る）。n はふつう 10 未満。判定のたびに親ごとに 1 回計算し、`Ticket` に入れて使い回す。代わりにファイルを 1 本読む I/O と YAML の読みが減るので、遅くはならない見込み |
| 移行 | 承認済みで進んでいる親は無い（ユーザの確認）。残っているファイルは読まない。`--lint` が「読まないファイル」と warn で言い、ユーザが消す（承認済みの置き場はエージェントが書けない） |

## 4. 残す場合と廃止する場合

| 観点 | 残す（今の草案） | 廃止する |
|---|---|---|
| 情報の持ち場 | 計画（承認済みチケット）と、その写し（ファイル）の 2 か所 | 計画の 1 か所 |
| 食い違いが起きる経路 | 改版でチケットとファイルの片方だけが書けたとき（戻す処理で防いでいる）。手で動かした承認でファイルが無いとき。親が消えてファイルだけ残ったとき（`--lint` の warn）。前の版の欄 | 無い。計算の決まりを後の版で直したときに、進行中の親の待ちが変わる経路が代わりに 1 つできる |
| 手で動かした承認 | 一直線（ファイルが無いので） | 計画の `after` で読む（緩む側）。「`after` の無い 2 項以上の計画は一直線」で前の版と `after` を書かない手動の承認は止まる側に倒せる |
| 取り下げ | チケットのバイト一致と、ファイルと計算の一致。ファイルも消す | チケットのバイト一致だけ。W1 は同じ |
| 改版 | 計画の違いに加え、ファイルと計算の違い（前の版の扱い） | 計画の違いだけ |
| C1・Chrome・`ccnavi-push-approved.sh` | ファイルを運ぶ・消す扱いが要る（今ある） | 要らない。C1 の名前の一覧と Chrome の見本から外す |
| ADR-0104 | そのまま | 「待ち方は承認時に固定し、`phases/<親>/workflow.yml` に置く」の行と、取り下げで待ち方を比べる行を置き換える。「承認はチケットの中身を変えない」はそのまま（承認は提案を動かすだけになり、むしろ書くものが減る） |

触るファイルの数（今の草案に対して増減するもの）。

| 区分 | 廃止で増える（草案に無く、新たに触る） | 廃止で減る（草案で触るが、要らなくなる作業） |
|---|---|---|
| 実行ファイル | 4（`approval_marks.py` の名前、`hook/c1.py`、`hook/core.py` 周りの取り下げの「消すもの」、`entry/lint_ticket.py` の孤児の warn を「読まないファイル」に） | `workflow.yml` にフィードバック計画の番号を足す作業、`parse_workflow` の形の検査（どちらも消えるので書かない） |
| Chrome 拡張 | 9（見本 6、テスト 3）と文書 2（`VERIFY.md`・`withdraw.md`） | — |
| テスト（Python） | 3（`test_core.py`・`test_c1.py`・`test_compat_skew.py`。ファイルを前提にした取り下げ・C1・版ずれ） | `workflow.yml` の形の検査のテスト |
| 文書 | 2（`state-transitions.md`、ADR-0105 の状態の行） | — |

草案で既に触る実行ファイル（`approval.py`・`approval_ops.py`・`agree.py`・`agree_digest.py`・`agree_screen.py`・`agree_candidates.py`・`approval_checks.py`・`workflow.py`・`ticket.py`・`phase.py`・`core_withdraw.py`・`status.py`）は、廃止でも同じく触る（中身は減る方向）。差し引きで新たに触るのは 20 本ほどで、多くは Chrome の見本とテストの機械的な直し。

段階分けへの影響。

| 段 | 廃止したとき |
|---|---|
| 1（実行ファイルの計画と検査） | 増える。ファイルの読み書き・C1・取り下げ・`--lint`・Chrome の見本をここで外す。分けるなら段 1 の直後に「1b: 待ち方のファイルをやめる」を置く |
| 3（フィードバック計画） | 減る。ファイルにフィードバック計画の番号を足す作業が消える |
| 4（`--reorder`・JSON） | 変わらない |
| 2・5・6・7 | 変わらない |

## 5. 結論

**廃止を推奨する。迷う点は 2 つある。**

理由。

- R1 で図の順序が承認の前に計画へ入るので、ファイルは承認済みの計画の完全な写しになった。写しを持つ理由だった「`phases.yml` を後で直しても進行中の親の待ちを変えない」は、`phases.yml` が待ち方の入力でなくなったことで消えた
- 2 か所に持つことで要っていた仕組み（改版で片方だけ書けたときの戻し、取り下げでの待ち方の比較と削除、C1 の名前、`--lint` の孤児の warn、Chrome の運び方、前の版の欄の見比べ）が全部要らなくなる
- 判定が読む型（`Ticket.workflow`）と読む側の多くはそのまま残せるので、直す箇所は入れ方と、ファイルを扱う箇所に集まる
- ADR-0104 の「承認はチケットの中身を変えない」は保たれる。承認で書くものは提案の移動だけになる

迷う点。

1. **手で動かした承認が、計画の `after` で並行に進むようになる（判定が緩む側の変更）。** 今はファイルが無いことを合図に一直線で読んでいる。廃止すると、その合図が無くなる。「`after` の無い 2 項以上の計画は一直線」で前の版と `after` を書かない手動の承認は止まる側に残せるが、`after` を書いた計画を手で動かせば、その `after` が効く。ユーザが中身を見て動かしたのだから `after` も承認したと読むか、`--agree` を通っていないので信じないか、はユーザの判断が要る。信じないなら、判定の側で `workflow.problems` を当てて壊れた形を止めたうえでも、ファイル（か別の目印）が残る
2. **計算の決まりを後の版で直すと、進行中の親の待ちが変わる。** ファイルなら承認したときの答えが残る。決まりは「`after` を推移的に辿る」「延期は待つ側の最小の番号が引き受ける」の 2 つだけで、直す見込みは小さいが、直すときは ADR を立てて版ずれのテスト（`test_compat_skew.py`）で見張ることになる
