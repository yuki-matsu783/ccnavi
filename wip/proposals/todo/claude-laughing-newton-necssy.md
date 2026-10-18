---
version: 1
ticket: claude-laughing-newton-necssy
mr: 430
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
  staging:
    kind: work
    title: ユーザがコピーする版の作成
    review: mr
    scope:
    - wip/design/*
    - tests/*
    when: 保護済みファイル（.ccnavi/scripts/、.claude/hooks/、rules.yml）を修正するとき。エージェントはそこに書けないので、完成品を
      wip/design/scripts/ に全文で配置し、ユーザがコピーする。受入テスト作成を先に置く（計画に受入テスト作成の項も置く）
  docs:
    kind: work
    title: 文書
    review: mr
    scope:
    - README.md
    - docs/*
    - extensions/vscode/ccnavi-board/docs/*
    - extensions/chrome/ccnavi-approval/docs/*
    - CLAUDE.md
    - .claude/skills/*
plan:
  - research
  - {type: design, after: [1]}
  - {type: acceptance, after: [2]}
  - {type: implement-ccnavi, after: [3]}
  - {type: staging, after: [4]}
  - {type: docs, after: [5]}
rationale: |
  チケットの移動以外で ccnavi が持つ状態（取り込み状態 sync/families と sync/integration）を廃止し、
  判定を「親ブランチの状態」だけにして、別 PC でも同じ結果になるようにする。
  設計書は取り込み状態を「キャッシュで判定には使わない」と書くが、実装は gone・blocked・broken で操作を止めている。この食い違いも解消する。
  Chrome 拡張（仮のツリーに記録を組み立てて渡している）と、手元の sh・実行ファイルを同じ作業で揃える。
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
    glob: "README.md"
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

取り込み状態は手元にだけあるので、別 PC に引き継げない。`gone` `blocked` `broken` は判定を止めるのに使われ、設計書（`docs/design/tickets/sharing.md`）の「判定には使わない」と食い違っている。実装側では、C1 の `present` 確認（`ccnavi-common-c1.sh:263`、`ccnavi-push-approved.sh:255`）と `ccnavi-git.sh push` の `gone` 拒否が、記録に頼って「消えた親ブランチの再作成」を止めている。

## 決定済みの方針（ユーザと合意）

| 論点 | 決定 |
|---|---|
| 記録の扱い | `families/<P>` の記録と `integration/` コピーを廃止する。`gone` `blocked` `closed` `broken` の状態を持たない |
| 親ブランチの消失（旧 `gone`） | 状態を持たず、**共通の push 関門**で止める（下の「push の関門」） |
| `ready` 後の片付け | `ready` 時に `retire/<親>.json` へ `mr` と `branch` を書き、片付けの列挙元をこれだけにする。ブランチ名からの MR 探索は補助に下げる（下の「MR の特定」） |
| 閉じた識別子の再利用検知 | オンライン専用。開始時のホスト照会（`ccnavi-start.sh`）に寄せる |
| レイヤーのずれ検知 | 常時の検知と `integration/` コピーを廃止する。運用で「ルール・フェーズ定義・リスク配点の変更は、先に統合先へ入れてから作業する」と決める。ずれは **warn**（停止にしない）。比べる対象は、元リポジトリのチェックアウトのレイヤーと、取得済みの `origin/<統合先>` |
| Chrome 拡張 | **この作業に含める。** 記録を組み立てて渡す方式をやめ、拡張が持つ情報（ホストの snapshot）を判定の入力に直す。要件 REQ-EXT-32 を改め、互換の版を上げる |
| ロック | **外さない。** 並行する子の `finish` が共有の親ワークツリーで merge するため排他が要る。簡素化は別の作業 |

## レビューで見つかり、設計に取り込む事項

敵対的レビュー（2 本）で見つかったものを、設計の前提として取り込む。

### push の関門

- 素の `git push` を打つ経路は 3 つある: `ccnavi-git.sh push`、C1（`ccnavi-common-c1.sh:592` 付近）、`ccnavi-push-approved.sh`。記録の `present` 確認を消すと、C1 と承認の push は関門を通らずに消えたブランチを再作成する。
- 関門は 1 つの共通関数にして 3 経路で共有する。内容:
  - 対象は親ワークツリーのブランチだけ（通常ブランチの push には掛けない）
  - 「追跡の形跡」は `refs/remotes/origin/<B>` の存在だけ。`branch.<B>.remote` は使わない（`origin/main` から切っただけの未 push の親が形跡ありと誤判定されるため）
  - 形跡が無くても、承認済みチケットや `.requested` のマーカーがある親は初回扱いにしない（`fetch.prune=true` で形跡が消えるため）
  - 形跡があるのにリモートに無いとき、`ls-remote` と `ccnavi-review.sh merged` で聞く。**マージ済みも拒否**する（閉じた名前の再作成を許さない）
  - `ls-remote` / `merged` が失敗・`unknown` のときは**拒否側に倒す**。ユーザが打つ迂回手段を 1 つ用意する
- 旧 `--forget` の代わりに、捨てた親の扱い（親のワークツリーを片付ければ関門の対象外）を案内する。

### MR の特定

- ワークツリーが生きている間は、親チケットの `mr:`（必須欄）を第一とする。
- `ready` 時に `retire/<親>.json` へ `mr` と `branch` を書く。ワークツリーを片付けた後の列挙と MR 特定はこれを使う。列挙できない親は一覧の案内だけを出し、自動では消さない。
- ブランチ名からの MR 探索（ADR-0103 の仕組み）は補助。使うときは次を要求する: 返った MR の head sha が手元の履歴に含まれる、base が統合先、複数ヒットなら全件を見て open が 1 件でもあれば「閉じていない」。
- `tidy` はワークツリーの削除を、ブランチ確認の後に移す。同名ブランチの再利用で生きた親のワークツリーを消さない。
- これは ADR-0136 の「推定しない」を一部改める。

### Chrome 拡張

- 拡張の `ccnavi_chrome.py` の `records()` は、仮のツリーに `integration/` と `families/<P>` を組み、ホストに無いブランチには `state gone` を書く。これをやめ、判定のコアが明示的な入力（ホストの snapshot とブランチの有無）を受ける形に直す。
- 拡張は git を起こせず `ls-remote` を打てない。ブランチの有無は拡張が既にホストから得ている情報を使う。
- 直す対象: `ccnavi_chrome.py`、`extensions/chrome/ccnavi-approval/test/*.test.ts`（約 13 ファイル）、`VERIFY.md`、`test/fixtures`、`docs/requirements/extension-if.md` の REQ-EXT-32。
- 互換の版（`CCNAVI_COMPAT`=12 の 3 か所: `version.py`、`ccnavi-common.sh`、VS Code 拡張の `EXTENSION_COMPAT`）を上げる。旧拡張との互換期間を置くかは設計で決める。

### レイヤーのずれ（warn）

- `integration/` コピーなしで、`--lint` と `sync check` の時に `git show origin/<統合先>:<パス>` で読んで比べる。違えば warn。停止にはしない。
- 判定が読むのは元リポジトリのチェックアウトのレイヤー。比べる対象もそこに揃える。
- Chrome の承認は統合先の snapshot を読むので、ずれは手元の warn でだけ知らされる。

### 閉じた識別子の再利用検知

- 現在の承認時の検知は統合先の `done/` コピーに頼るが、統合先には閉じたチケットを残さない運用なので、実際にはほとんど効いていない。失うものは小さい。
- 承認の経路は実行ファイルがネットワークに出ない決まりのため、開始時の照会を直接呼べない。sh が先に取得して JSON で渡す形にするか、承認ではオフラインで通すかを設計で決める。
- 開始時の照会は `ccnavi-start.sh` の経路だけにある。名前は識別子の別表記（`/` と `-`、英字の大小）を正規化して聞く。承認の経路では `--mcp-checked`（自己申告）を受けない。
- 先行チケットの解決（`approval_checks._closed_elsewhere`）の置き換え先を決める。

## 変更の内容

### 消すもの

- `families/<P>` の記録すべてと `ccnavi-sync.sh --forget`
- `integration/` コピー（`ccnavi-sync.sh` の `write_integration`、`syncstate.Integration` ほか）
- `syncstate.py` のうち記録・統合先に関わる部分。残すのは、親のワークツリーの有無・HEAD とフォルダ名の一致・途中の操作というファイルだけで決まる立ち位置（`Standing`）。`DoneCopy` `same_parent` `_copy_fields`（`sh_ask.py` `archive.py` が `git show` 側で使う）は別モジュールへ移す
- 記録の読み書き: `ccnavi-sync.sh` `ccnavi-git.sh`（push 後の書き戻し `:1980-2010`）`ccnavi-common-c1.sh` `ccnavi-push-approved.sh` `ccnavi-start.sh`（`record_mr`）
- 記録に依る表示・分岐: `lint.py`（`family_check`）`lint_layers.py` `diagnose_board.py` `status.py` `overview.py` `scaffold.py` `ops_stop_integrate.py` `c1.py`（`state` 行）`approval_checks.py` `ops_close.py`
- `Standing.stop` と `guidance()` の `gone` `blocked` `--forget` の案内文

### 足すもの

1. 共通の push 関門（3 経路）
2. `ready` 時の `retire/<親>.json` への `mr` `branch` の書き込みと、それを列挙元にする片付け
3. 照会に失敗したときの迂回手段
4. レイヤーのずれの warn（`git show` ベース）
5. 承認の経路での再利用の扱い（調査の結果次第）
6. 旧 `families/*` の 1 回限りの移行（`mr` と `branch` を `retire/` へ移し、旧ファイルを削除）
7. Chrome 拡張の入力形式の変更と互換の版

### 残すもの

- `ccnavi-sync.sh` の取り込み実体（fetch・早送り・merge・衝突時の取りやめ）、MR のマージ済み判定と手元の片付け（`sync tidy`）
- ロック（`ccnavi-common-lock.sh`）、`ccnavi_record_get/write/put`（`integrate` のマーカー）、`ccnavi_repo_key`（ロックの置き場）、`retire/<親>.json`、`logs/archive/`
- `ccnavi_integration`（統合先ブランチ名の解決）。ただし `integration/head` の枝は外す。環境変数と `settings.local.json` の `env`（sh 側でも読む）と `origin/HEAD` の順に揃える

## 調査で確かめること（research の項）

敵対的レビューで答えが出た 7 項目は、ここに結果を書いた。残る確認:

1. `settings.local.json` の `env` が sh（`ccnavi-git.sh` `ccnavi-fetch.sh`）に渡るか。渡らないなら統合先の解決を実行ファイルに聞く形にする
2. `hook` が取り込み直後のリモートの承認済みチケットを、検査の前に範囲判定へ使う窓（旧 `blocked` が埋めていた分）の大きさ。`Ticket.blocked` を hook が見るか
3. `approval_checks._closed_elsewhere` の置き換え先。閉じた親子の子を先行に持つチケットの扱い
4. 旧 sh と新実行ファイルの混在時の挙動（`sync check` を知らない旧版は検査が fail-open になる）。新 sh と旧実行ファイルの組み合わせ
5. `start` 直後〜提案作成前の `integrate` の束縛判定の窓（`ig_bound` の前段が false の間）
6. 放棄した親（MR をマージせずに閉じた）が毎回 `sync` の終了コードを 1 にする問題の扱い
7. `ccnavi-git.sh` のホワイトリストで、追跡設定の解除が打てるか
8. 旧拡張との互換期間を置くか、`CCNAVI_COMPAT` で止めるか
9. GitLab の `find_merged_mr`（`ccnavi-start.sh:344`）に `source_project_id` の絞りが無いこと（フォークの MR で誤検知）。`ccnavi-review.sh` 側は絞っている

## メリットとデメリット

得るもの:
- 手元にだけある状態が減り、別 PC への引き継ぎで食い違わない
- 設計書と実装の食い違い（「判定に使わない」と書いて止めに使っている）が消える
- `syncstate.py`（776 行）の大半、`ccnavi-sync.sh` の書き込み部、`integration` コピー、Chrome 拡張の記録組み立てが消える
- 旧 `gone` の拒否が、3 経路で同じ関門になる（今は C1 と承認の push が記録頼み）

失うもの・残るもの:
- `gone` `blocked` の永続的な検知。オフラインの hook は消失を知れず、気づくのは sync・push・セッション開始の fetch の時点になる
- 閉じた識別子の再利用検知のオフライン動作（現状も効いていない）
- 常時のレイヤーのずれ検知。warn と運用で守る。ccnavi 自身の開発では、レイヤーを変えながらそれを使う作業を別チケットに分ける
- push の前に `ls-remote` が走る。失敗時は拒否に倒すので、認証が対話式の環境では迂回手段が要る
- `retire/<親>.json` はローカル状態として残る。「別 PC に引き継げない状態」はゼロにならない（`ready` を打った PC にしか列挙元が無い）
- 規模が大きい。sh 6 本以上、Python 10 ファイル以上、Chrome 拡張とそのテスト 13 ファイル以上、テスト約 24 ファイル

やらなかった場合: 取り込み状態が手元だけに残り続け、別 PC で `gone` や `blocked` の食い違いが出る。設計書との食い違いも残る。

## 代案

- **`gone` だけ残して `blocked` と `closed` を消す。** オフラインの hook でも消失を知れるが、手元だけの状態は残り、方針に反する
- **`integration/` コピーを残して `families/` だけ消す。** 変更は小さいが、レイヤーのずれ検知と再利用検知の窓口が手元のコピーに残り、別 PC に引き継げない状態が残る
- **Chrome 拡張を別の作業に分ける。** 手元側を先に直せるが、拡張が組み立てる記録の形と食い違う期間ができ、互換の維持が要る（今回は含める判断）
- **ロックも外す。** 同一 PC の並行する子の `finish` が親ワークツリーを奪い合うため採らない

## 計画の理由

research → design → acceptance → implement-ccnavi → staging → docs の一直線にした。

- research: 上の 9 項目の答えが設計を左右するため、最初に置く
- design: 消す・足す・残すを確定し、ADR（新規 1 本。既存の ADR-0093・0136・0103 は `docs/adr/README.md` の規則に従い、状態行への追記か置き換えかを決める）と設計書の変更案を書く。拡張の入力形式と互換の版もここで決める
- acceptance: 共通の push 関門（3 経路）、再利用検知、レイヤーの warn、`ready` 後の片付け、拡張の判定の受入テストを先に書く。記録に依存する既存テスト（約 24 ファイル）の書き換え方針を決める
- implement-ccnavi: Python（`src/`）、Chrome 拡張、テストを直す。子を分ける想定で、(a) 共通の push 関門と `retire/` への書き込みと warn の追加、(b) Chrome 拡張の入力形式の変更、(c) 記録と `integration/` の削除と移行。追加が先に入れば削除が安全になる
- staging: **`.ccnavi/scripts/` の sh は保護対象で、エージェントは書けない。** 完成品を `wip/design/scripts/` に全文で置き、ユーザがコピーする。sh の変更（`ccnavi-sync.sh` `ccnavi-git.sh` `ccnavi-common-c1.sh` `ccnavi-push-approved.sh` `ccnavi-start.sh` ほか）はすべてこの項で扱う。受入テストは先に書く
- docs: `README.md`、設計書、`docs/CONTEXT.md`、`docs/requirements/extension-if.md`、ADR の整理。`README.md` は既存のフェーズ定義 `docs`（`.ccnavi/config/project/phases.yml`）の範囲に入っているので、フェーズ定義の変更は要らない

## 未解決

- `.ccnavi/scripts/` の sh を直す子は、保護対象のため staging の項で扱う（`implement-ccnavi` の範囲 `.ccnavi/*` は sh には効かない）。
- 識別子の番号 `1` は通し番号（`ticket next-id` の答え）。issue があるなら付け替える。
