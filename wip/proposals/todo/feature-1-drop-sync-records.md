---
version: 1
ticket: feature-1-drop-sync-records
title: "取り込み状態（sync/）を廃止し、親ブランチの状態だけで判定する"
phases:
  research:
    kind: work
    title: 調査
    review: none
    scope:
    - wip/research/*
    deliverables:
    - wip/research/summary.md
    when: 既存の振る舞いや依存が分からないとき。分かっているなら省略する
  design:
    kind: work
    title: 設計
    review: mr
    scope:
    - wip/design/*
    - docs/*
    deliverables:
    - wip/design/*.md
    when: 変更箇所が 3 か所を超えるか、外から見える振る舞いが変わるとき
  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope:
    - tests/*
    when: 振る舞いが変わるとき。実装と並行してよい（実装の項の `after` にこの項を書かなければ並行する）
  implement-ccnavi:
    kind: work
    title: 実装とテスト（ccnavi 自身）
    review: mr
    scope:
    - src/*
    - tests/*
    - build.py
    - pyproject.toml
    - scripts/*
    - .ccnavi/*
    - extensions/*
    when: 振る舞いを変えるとき。受入テスト作成を先に置く（計画に受入テスト作成の項も置く）
plan:
  - research
  - {type: design, after: [1]}
  - {type: acceptance, after: [2]}
  - {type: implement-ccnavi, after: [3]}
rationale: |
  チケットの移動以外で ccnavi が持つ状態（取り込み状態 sync/families と sync/integration）を廃止し、
  判定を「親ブランチの状態」だけにして、別 PC でも同じ結果になるようにする。
  設計書は取り込み状態を「キャッシュで判定には使わない」と書くが、実装は gone・blocked・broken で操作を止めている。この食い違いも解消する。
allow:
  - match: Write|Edit
    glob: "wip/research/*"
  - match: Write|Edit
    glob: "wip/design/*"
  - match: Write|Edit
    glob: "docs/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "src/*"
  - match: Write|Edit
    glob: ".ccnavi/*"
  - match: Write|Edit
    glob: "extensions/*"
---
## 背景

ccnavi は、チケットの置き場（`todo/` `doing/` `review/` `done/`）のほかに、手元の `logs/state/sync/` に状態を持っている。

- `families/<P>`: 親子ごとの取り込み状態（`state` は present / closed / gone / blocked、ほかに `mr` `branch` `sha` `fetched_at`）
- `integration/`: 統合先の `done/`・レイヤー・設定のコピーと `head`

目指す姿は、次の 2 つだけで判定が決まること。

1. 親ブランチの状態（承認済みチケット、`phases/<親>/` のマーカー、MR）からチケットの状態を判断する
2. チケットの切れ目で別 PC に移っても動く

取り込み状態は手元にだけあるので、別 PC に引き継げない。`gone` `blocked` `broken` は判定を止めるのに使われ、設計書（`docs/design/tickets/sharing.md`）の「判定には使わない」と食い違っている。

## 決定済みの方針（ユーザと合意）

| 論点 | 決定 |
|---|---|
| `gone`（親ブランチ消失） | 状態を持たない。`ccnavi-git.sh push` の時点で、手元に追跡の形跡があるのにリモートに親ブランチが無ければ、`ls-remote` と `ccnavi-review.sh merged` で聞き、マージ済みでなければ拒否する。形跡が無ければ初回 push として通す |
| `ready` 後の片付け | 親のワークツリー・記録が無くなった後の MR の特定は、ブランチ名から探す（`ccnavi-branches.sh` / ADR-0103 の仕組みを流用）。`ccnavi-sync.sh` の「ブランチから MR を探す推定はしない」を改める |
| 閉じた識別子の再利用検知 | オンライン専用にする。開始時にホストへ「同名のブランチにマージ済みの MR が無いか」を聞く（`ccnavi-start.sh` に既にある）。承認の経路から同じ確認を呼べるかは調査で確かめる |
| レイヤーのずれ検知 | 常時の検知（`lint_layers._layer_drift`）は廃止し、`integration/` コピーも持たない。運用で「ルール・フェーズ定義・リスク配点の変更は、先に統合先へ入れてから作業する」と決める。強制は、親の `ticket start` と承認の時点で、作業ツリーのレイヤーが取得済みの `origin/<統合先>` と同じかを 1 回だけ `git show` で確かめ、違えば止める |
| ロック（`ccnavi-common-lock.sh`） | **この作業では外さない。** 同一 PC で複数の子を並行すると、`finish` が共有の親ワークツリーで merge するため、排他が要る。簡素化（入れ子と古いロックの強制取得）は別の作業で扱う |

## 変更の内容

### 消すもの

- `families/<P>` の記録すべて（`gone` `blocked` `closed` `broken` `mr` `branch` `sha` `fetched_at` `remote`）と、`ccnavi-sync.sh --forget`
- `integration/` コピー（`ccnavi-sync.sh` の `write_integration`、`syncstate.Integration` ほか）
- `syncstate.py` のうち記録・統合先に関わる部分。残すのは、親のワークツリーの有無・HEAD とフォルダ名の一致・途中の操作というファイルだけで決まる立ち位置（`Standing`）
- 記録を読む・書く sh: `ccnavi-sync.sh` `ccnavi-git.sh`（push 後の書き戻し、`:1980-2010`）`ccnavi-common-c1.sh`（`present` 確認）`ccnavi-push-approved.sh` `ccnavi-start.sh`（`record_mr`）`ccnavi-common-state.sh`
- 記録の有無に依る表示・分岐: `overview.py`（最終取り込み時刻・MR 番号）`scaffold.py`（`mr` のフォールバック）`ops_stop_integrate.py`

### 足すもの

1. push 時の親ブランチ確認（`ccnavi-git.sh`）
2. 承認の経路での閉じた識別子の再利用検知（開始時の照会の再利用）
3. `ticket start` と承認でのレイヤーの 1 回の照合
4. `ready` 後の片付けの対象の列挙と、ブランチ名からの MR 特定

### 残すもの

`ccnavi-sync.sh` の取り込み実体（fetch・早送り・merge・衝突時の取りやめ）、MR のマージ済み判定と手元の片付け（`sync tidy`）、ロック。

## 調査で確かめること（research の項）

どれも実装の前に読んで答える。答え次第で設計が変わる。

1. **`sync check`（`cli_ops.py` の `sync_check`）が何を error とするか。** `blocked` を持たない代わりに、C1 の `begin` で同じ検査を毎回打てるか。取り込みから次の状態操作までの間に判定が緩む窓がどれだけあるか
2. **`ready` 後の親の列挙元。** 記録の一覧（`family_names`）を失った後、片付けの対象を何で見つけるか（手元ブランチの一覧、`retire/<親>.json`、退避など）
3. **integrate の束縛判定**（`ccnavi-git.sh:748` `ops_stop_integrate.py`）が、記録なしで `ready` 後〜退避前の窓を漏らさないか
4. **`ccnavi-fetch.sh:230` の `ccnavi_repo_key` の使い道**
5. **Chrome 拡張が使う同梱のコア**が `syncstate` のどこを呼ぶか。拡張の互換の版（`CCNAVI_COMPAT` / `EXTENSION_COMPAT`）を上げる必要があるか
6. **承認の経路で閉じた識別子の再利用を確かめる手段。** 既存の照会（`ccnavi-start.sh`）を承認からも呼べるか、承認はオフラインで通してよいか
7. **統合先ブランチ名の解決**（環境変数 → `origin/HEAD`）だけで、`ccnavi_integration` の取り込み結果の枝を外してよいか

## メリットとデメリット

得るもの:
- 手元にだけある状態が減り、別 PC への引き継ぎで食い違わない
- 設計書と実装の食い違い（「判定に使わない」と書いて止めに使っている）が消える
- `syncstate.py`（776 行）の大半と `ccnavi-sync.sh` の書き込み部、`integration` コピーの書き出し・読み出しが消える
- `--forget` や「取り込み状態が壊れた」という運用が不要になる

失うもの:
- `gone`（消えたブランチ）の永続的な検知。オフラインの hook は消失を知れず、気づくのは sync・push・セッション開始の fetch の時点になる。実害が出る場面は、ブランチの消し間違いと、別 PC で古い親ワークツリーが残った場合
- `blocked`（取り込み後の検査で止めた状態）の永続化。取り込みから次の状態操作までは、問題のある承認済みチケットでも範囲の判定が走る
- 閉じた識別子の再利用検知のオフライン動作
- レイヤーの常時のずれ検知。代わりに運用（変更を先に統合先へ入れる）と 1 回の関門で守る。ccnavi 自身の開発で、レイヤーを変えながらそれを使う作業は、別チケットに分ける運用になる
- ブランチ名からの MR 特定は、同名ブランチの別の MR を取り違える余地がある（推定をやめていた理由）。取り違えを避ける照合（元ブランチ名・マージ先・状態）を設計で決める

やらなかった場合: 取り込み状態が手元だけに残り続け、別 PC で `gone` や `blocked` の食い違いが出る。設計書との食い違いも残る。

## 代案

- **`gone` だけ残して `blocked` と `closed` を消す。** オフラインの hook でも消失を知れるが、手元だけの状態は残り、方針に反する
- **`integration/` コピーを残して `families/` だけ消す。** 変更は小さいが、レイヤーのずれ検知と再利用検知の窓口が手元のコピーに残り、別 PC に引き継げない状態が残る
- **ロックも外す。** 同一 PC の並行する子の `finish` が親ワークツリーを奪い合うため採らない

## 計画の理由

research → design → acceptance → implement-ccnavi の一直線にした。

- research: 上の未検証 7 項目の答えが設計を左右するため、最初に置く
- design: 消す・足す・残すを、調査の答えで確定し、ADR（`gone` を決めた ADR を置き換える 1 本）と設計書（`sharing.md` `places.md`、README）の変更案を書く
- acceptance: `gone` なしの push 拒否、再利用検知、レイヤーの照合、`ready` 後の片付けの受入テストを先に書く。既存の `test_sync_sh.py` `test_sync_authority.py` `test_c1_sh.py` などで記録に依存するものを洗い出し、書き換え方針を決める
- implement-ccnavi: 追加（push 確認・再利用検知・レイヤー照合・MR 特定）を先に入れ、そのあと記録と `integration/` を削除する。追加が先に入れば削除が安全になる

実装の子は、(a) 追加、(b) 削除、(c) ドキュメントと ADR の整理に分けて提案する想定。

## 未解決

- `mr:`（必須）はまだ書いていない。この作業を載せる MR の番号が決まったら、`ccnavi ticket new --mr` 相当で足してから承認を頼む。
- 識別子の番号 `1` は通し番号（`ticket next-id` の答え）。issue があるなら付け替える。
