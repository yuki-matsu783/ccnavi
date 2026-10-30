# 提案の草稿: ユーザに渡す操作を検査にする（`--check`）

状態: 草稿。ユーザの合意待ち。実装はしていない。

## 目的

承認・レビューをユーザに頼んだあとに、ユーザが拒否や不備を見て戻ってくる往復をなくす。

## 規則

> ユーザに渡す操作（`propose`・`request`）は、ユーザがその場で次の一手を打てる状態でなければ、何も書かずに拒否する。

流れは 3 手に減る。

1. エージェントが `propose` か `request` を打つ。拒否されたら理由を直して打ち直す。
2. 成功したときだけ、出力の案内（承認方法、レビュー先）をユーザに伝える。
3. ユーザの一言のあと、`ccnavi-ticket.sh status` を 1 回見て続ける。

## 変えること

| # | 変更 | 理由 |
|---|---|---|
| 1 | `propose` 本体の拒否条件に、承認の画面（`--agree`）の検査を全部入れる（`mr_problems`・`place_problems`・`family_problems`・`branch_problems`・`predecessor_problems`・`project_problems`・`blocking_problems`） | 承認の画面で初めて拒否される往復をなくす |
| 2 | `propose` 本体の拒否条件に、親のワークツリーの未コミットの変更（`dirty_parents` と同じ条件）を入れる。`propose_guard` の隣に置く | 承認後に子の `finish` が止まる穴をふさぐ |
| 3 | `propose --check` と `request --check` を足す。拒否条件を同じ経路で走らせ、何も書かない・投稿しない・コミットしない。出力は ok / ng / unverified / skipped（`ccnavi-start.sh --check` と同じ語彙） | 渡さずに状態だけ見る入口 |
| 4 | `--agree --preview --verify` を `propose --check` に統合し、検査の入口を 1 つにする | 入口が 2 つあると、どちらを打つか迷う |
| 5 | ホストへの問い合わせは、今の `propose` の事前確認（`ticket_preflight`）と同じ sh 側だけに置く。実行ファイルはネットワークに出ない | ADR-0160 |

## 変えないこと

- `request` の拒否条件（`_unmet`・`prepare`）。すでに渡す時点で拒否している。
- 承認後の確認は `status` のまま。
- 承認の経路 4 つと、承認はユーザが行うこと。

## メリット

- 承認・レビューを頼んだあとの、ユーザの戻りの往復がなくなる。
- 検査が渡す操作と同じ経路なので、`--check` と実際の拒否がずれない。

## デメリット

- `propose` が重くなる（ローカルのファイル検査が増える。ネットワークは今の事前確認のみ）。
- 承認の画面の検査と `propose` の拒否条件が同じ関数を共有するため、片方だけを緩める変更ができなくなる。
- 提案を書いている途中でも、条件を満たすまで `propose` が通らない。
- `--agree --preview --verify` を使っている文書・拡張の呼び出しを直す必要がある。

## 先に確かめること

- `--agree --preview --verify` の呼び出し元（文書・VS Code / Chrome 拡張・テスト）。
- ELI5 の検査が `request` のどこにあるか。
- 「承認の画面の検査」のうち、`propose` の時点で判定できないもの（子の提案が同じ承認に入るか、など）。

## 範囲（見込み）

- `.ccnavi/scripts/ccnavi-ticket.sh`、`.ccnavi/scripts/ccnavi-review.sh`
- `src/ccnavi/tickets/proposal/propose.py`、`agree_candidates.py`、`src/ccnavi/tickets/review/request.py`
- `.ccnavi/docs/ticket-flow.md`、`proposal-format.md`、`decisions.md`、ADR（新規）
- テスト
