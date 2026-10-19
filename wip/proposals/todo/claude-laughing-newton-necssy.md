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
  - {type: implement-ccnavi, after: [5]}
  - {type: staging, after: [6]}
  - {type: docs, after: [7]}
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

この作業が目指すのは「**判定**が別 PC でも同じ結果になること」。親を閉じた後の片付けは、`ready` を打った PC のローカル状態（`retire/<親>.json`）に残る。

## 決定済みの方針（ユーザと合意）

| 論点 | 決定 |
|---|---|
| 記録の扱い | `families/<P>` の記録と `integration/` コピーを廃止する。`gone` `blocked` `closed` `broken` の状態を持たない |
| 親ブランチの消失（旧 `gone`） | 状態を持たず、**共通の push 関門**で止める（下の「push の関門」） |
| `ready` 後の片付け | `ready` 時に書く `retire/<親>.json`（既存の `note()` を拡張して `mr` を足す）を列挙元にする。ブランチ名からの MR 探索は補助に下げる（下の「MR の特定」） |
| 閉じた識別子の再利用検知 | オンライン専用。開始時のホスト照会（`ccnavi-start.sh`）だけで検知する。**承認の経路では検知しない**（実行ファイルはネットワークに出ない決まりのため） |
| レイヤーのずれ検知 | 常時の検知と `integration/` コピーを廃止する。運用で「ルール・フェーズ定義・リスク配点の変更は、先に統合先へ入れてから作業する」と決める。ずれは **warn**。比べる対象は、元リポジトリのチェックアウトのレイヤーと、取得済みの `origin/<統合先>` |
| Chrome 拡張 | **この作業に含める。** 拡張の `records()` から `families` と `integration` の記録の組み立てを削る。仮のツリーと、それを手元と同じ関数で読むこと（REQ-EXT-32）は変えない。ホストに親ブランチが無いとき、承認を止める（旧 `gone` と同じ） |
| ロック | **外さない。** 並行する子の `finish` が共有の親ワークツリーで merge するため排他が要る。簡素化は別の作業 |
| 識別子 | `claude-laughing-newton-necssy`（親ブランチ名 `claude/laughing-newton-necssy` の `/` を `-` に置き換えたもの）。先頭の語・番号の形に合わず `--lint` が warn を出すが、承認と子の起票は止まらない。ブランチを切り直す場合は `feature-<番号>-<slug>` に |

## 設計に取り込む事項（敵対的レビューの指摘）

敵対的レビュー（初回 2 本、改訂後 2 本）で見つかったものを、設計の前提として取り込む。

### push の関門

- 素の `git push` を打つ経路は 3 つある: `ccnavi-git.sh push`、C1（`ccnavi-common-c1.sh`。素の push の行は設計時に確かめる）、`ccnavi-push-approved.sh`。記録の `present` 確認を消すと、C1 と承認の push は関門を通らずに消えたブランチを再作成する。
- 関門は 1 つの共通関数にして 3 経路で共有する。置き場は `ccnavi-common-state.sh`（`ccnavi_git_timed` と同じ部品）。内容:
  - 対象は親ワークツリーのブランチだけ（通常ブランチ・子の push には掛けない。`ccnavi_parent_tree` と `ccnavi_branch_to_id` の条件を持ち込む）
  - 「追跡の形跡」は `refs/remotes/origin/<B>` の存在だけ。`branch.<B>.remote` は使わない（`origin/main` から切っただけの未 push の親が形跡ありと誤判定されるため）
  - 形跡が無くても、承認済みチケットや `.requested` のマーカーがある親は初回扱いにしない（`fetch.prune=true` で形跡が消えるため）。**この判定はチケットとマーカーの中身を見るので、sh は自前で読まず、実行ファイルに副命令を新設して聞く**（`tree parent` の延長。sh にチケットの判定を足さない決まりを守る）
  - 形跡があるのにリモートに無いとき、`ls-remote` と `ccnavi-review.sh merged` で聞く。**マージ済みも拒否**する（閉じた名前の再作成を許さない）
  - `ls-remote` / `merged` が失敗・`unknown` のときは**拒否側に倒す**。ユーザが打つ迂回手段を 1 つ用意する
  - `ls-remote` を打つ条件（形跡があるときだけか、形跡が無い承認済みの親でも打つか）は設計で確定する
- sh に判定を足さない決まりの例外（`docs/claude/exe-boundary.md` の P13、ADR-0118）は、今は `ccnavi-git.sh` の中だけ。関門を C1 と `push-approved` にも置くので、**例外の範囲を明示して広げる**（`exe-boundary.md` と ADR に書く）。
- 旧 `--forget` の代わりに、捨てた親の扱い（親のワークツリーを片付ければ関門の対象外）を案内する。

### MR の特定

- ワークツリーが生きている間は、親チケットの `mr:`（必須欄）を第一とする。
- `ready` 時の `retire/<親>.json`（`retire.py` の `note()` が既に `parent` `project` `branch` `children` を書く）に `mr` を足す。ワークツリーを片付けた後の列挙と MR 特定はこれを使う。
  - sh は JSON を読まない決まりなので、列挙と `mr` の取得は実行ファイルの副命令に聞く。今の `add_retired`（`families/` の走査）と `sync_retired`（記録の `mr` を読む）を置き換える。
  - `note()` が書けなかったときの扱いを決める（`ready` の失敗または明示の警告にする）。列挙できない親は一覧の案内だけを出し、自動では消さない。
  - 片付けの完了で `retire/<親>.json` を消す今の動き（`retire.py` の `run_merged`）と、「まだ列挙できない」の区別を設計で決める。
- ブランチ名からの MR 探索（ADR-0103 の仕組み）は補助。使うときは次を要求する: 返った MR の head sha が手元の履歴に含まれる、base が統合先、複数ヒットなら全件を見て open が 1 件でもあれば「閉じていない」、GitLab は `source_project_id` で絞る。
- `tidy` はワークツリーの削除を、ブランチ確認の後に移す。同名ブランチの再利用で生きた親のワークツリーを消さない。
- これは ADR-0136 の「推定しない」を一部改める。

### Chrome 拡張

- 拡張の `ccnavi_chrome.py` の `records()` は、仮のツリーに `integration/` と `families/<P>` を組み、ホストに無いブランチには `state gone` を書く。**`families` の記録と `integration` の `state` に関わる部分だけを削り、仮のツリーと、手元と同じ関数で読むこと（REQ-EXT-32）は変えない。** REQ-EXT-32 の文言は、取り込み状態に触れる記述だけを削る。
- ホストに親ブランチが無いときは、承認を止める（旧 `gone` と同じ向き）。ブランチの有無は拡張が既にホストから得ている情報を使う。拡張は git を起こせず `ls-remote` を打てない。
- 直す対象: `ccnavi_chrome.py`、`extensions/chrome/ccnavi-approval/test/*.test.ts`（件数は設計時に数える。初回レビューは約 13）、`VERIFY.md`、`test/fixtures`、`docs/requirements/extension-if.md`。
- 互換の版（`CCNAVI_COMPAT`=12）は 3 か所: `src/ccnavi/entry/version.py`、`.ccnavi/scripts/ccnavi-common.sh`、VS Code 拡張の `EXTENSION_COMPAT`。拡張は統合先の sh の版が自分と違えば、書く操作（承認の取り下げ、plan）を断る。読む判定は断らない。**拡張の公開は、`staging` のコピーが統合先に入った後にする。**

### レイヤーのずれ（warn）

- `integration/` コピーなしで、`--lint` と `sync check` の時に `git show origin/<統合先>:<パス>` で読んで比べる。違えば warn。
- 判定が読むのは元リポジトリのチェックアウトのレイヤー。比べる対象もそこに揃える。
- Chrome の承認は統合先の snapshot を読むので、ずれは手元の warn でだけ知らされる。

### 閉じた識別子の再利用検知

- 現在の承認時の検知は統合先の `done/` コピーに頼るが、統合先には閉じたチケットを残さない運用なので、実際にはほとんど効いていない。失うものは小さい。
- 検知は開始時の照会（`ccnavi-start.sh`）に限る。名前は識別子の別表記（`/` と `-`、英字の大小）を正規化して聞く。承認の経路では検知しない（上の表）。
- 先行チケットの解決（`approval_checks._closed_elsewhere`）の置き換え先を決める。
- `--mcp-checked`（自己申告）が開始時の照会を飛ばせる点は、調査で確かめる。

### 順序と互換の版

sh（`.ccnavi/scripts/`）はエージェントが書けず、ユーザがコピーするまで効かない。実行ファイルだけ先に記録を消すと、旧 sh は「記録なし」を `state != present` と読み、C1 と承認の push が止まる。そこで順序は次のとおりにする。

1. 実装（追加のみ）: 新しい副命令（push 関門の判定、`retire` の列挙）、warn、拡張の受け口。**既存の記録の読み書きは消さない。** 実行ファイルは「記録が無い」を既に許す
2. staging（1 回目）: sh を新しい形にする。記録を読まず書かず、新しい副命令を使う。旧実行ファイルでも動くよう、記録が残っていても邪魔しない。互換の版は変えない
3. 実装（削除）: `syncstate` の記録・統合先の部分、拡張の記録の組み立て、テストを消す。互換の版の上げは、実行ファイルと拡張をここで 13 にする
4. staging（2 回目）: sh の互換の版を 13 にする小さな差し替え。**3 の MR のマージと同時にユーザがコピーする**（間は `--lint` が版の食い違いを error にする）
5. docs

拡張の公開は 4 の後。

## 変更の内容

### 消すもの

- `families/<P>` の記録すべてと `ccnavi-sync.sh --forget`
- `integration/` コピー（`ccnavi-sync.sh` の `write_integration`、`syncstate.Integration` ほか）
- `syncstate.py` のうち記録・統合先に関わる部分。残すのは、親のワークツリーの有無・HEAD とフォルダ名の一致・途中の操作というファイルだけで決まる立ち位置（`Standing`）。`DoneCopy` `same_parent` `_copy_fields`（`sh_ask.py` `archive.py` が `git show` 側で使う）は別モジュールへ移す
- 記録の読み書き: `ccnavi-sync.sh` `ccnavi-git.sh`（push 後の書き戻し `:1980-2010`）`ccnavi-common-c1.sh` `ccnavi-push-approved.sh` `ccnavi-start.sh`（`record_mr`）
- 記録に依る表示・分岐: `lint.py`（`family_check`）`lint_layers.py` `diagnose_board.py` `status.py` `overview.py` `scaffold.py` `ops_stop_integrate.py` `c1.py`（`state` 行）`approval_checks.py` `ops_close.py`
- `Standing.stop` と `guidance()` の `gone` `blocked` `--forget` の案内文

### 足すもの

1. 共通の push 関門（3 経路）と、実行ファイルの副命令（親にチケットやマーカーがあるか）
2. `retire/<親>.json` への `mr` の追加と、実行ファイルの列挙の副命令。それを列挙元にする片付け
3. 照会に失敗したときの迂回手段
4. レイヤーのずれの warn（`git show` ベース）
5. 旧 `families/*` の 1 回限りの移行（`ready` 済みで `note()` が無い旧親も含め、`mr` と `branch` を `retire/` へ移し、旧ファイルを削除）
6. Chrome 拡張の記録の組み立ての削除と、ホストに親ブランチが無いときの承認の停止

### 残すもの

- `ccnavi-sync.sh` の取り込み実体（fetch・早送り・merge・衝突時の取りやめ）、MR のマージ済み判定と手元の片付け（`sync tidy`）
- ロック（`ccnavi-common-lock.sh`）、`ccnavi_record_get/write/put`（`integrate` のマーカー）、`ccnavi_repo_key`（ロックの置き場）、`retire/<親>.json`、`logs/archive/`
- `ccnavi_integration`（統合先ブランチ名の解決）。ただし `integration/head` の枝は外す。統合先は各リポジトリの手元の `origin/HEAD` だけで決める（ADR-0148）

## 調査で確かめること（research の項）

初回レビューで答えが出ていた 7 項目（`sync check` の中身、`ready` 後の列挙元、`integrate` の束縛判定、`ccnavi_repo_key` の使い道、拡張が `sync/` の書き手であること、承認の経路から照会を呼べないこと、統合先の解決順の食い違い）は、上の設計に反映した。残る確認:

1. `hook` が取り込み直後のリモートの承認済みチケットを、検査の前に範囲判定へ使う窓（旧 `blocked` が埋めていた分）の大きさ。`Ticket.blocked` を hook が見るか
2. `approval_checks._closed_elsewhere` の置き換え先。閉じた親子の子を先行に持つチケットの扱い
4. 新 sh と旧実行ファイル、旧 sh と新実行ファイルの混在時の挙動（`sync check` を知らない旧版は検査が fail-open になる）。上の「順序と互換の版」で足りるか
5. `start` 直後〜提案作成前の `integrate` の束縛判定の窓（`ig_bound` の前段が false の間）
6. 放棄した親（MR をマージせずに閉じた）が毎回 `sync` の終了コードを 1 にする問題の扱い
7. `ccnavi-git.sh` のホワイトリストで、追跡設定の解除が打てるか
8. 旧拡張との互換（拡張の公開順序で足りるか）
9. `--mcp-checked` の使われ方（承認経路で受けない方針の影響）
10. C1 の素の push の行番号と、push 関門を差し込む位置
11. 関門で `ls-remote` を打つ条件の確定

## メリットとデメリット

得るもの:
- 手元にだけある状態が減り、別 PC への引き継ぎで判定が食い違わない
- 設計書と実装の食い違い（「判定に使わない」と書いて止めに使っている）が消える
- `syncstate.py`（776 行）の大半、`ccnavi-sync.sh` の書き込み部、`integration` コピー、Chrome 拡張の記録組み立てが消える
- 旧 `gone` の拒否が、3 経路で同じ関門になる（今は C1 と承認の push が記録頼み）

失うもの・残るもの:
- `gone` `blocked` の永続的な検知。オフラインの hook は消失を知れず、気づくのは sync・push・セッション開始の fetch の時点になる
- 閉じた識別子の再利用検知のオフライン動作と、承認経路での検知（現状も効いていない）
- 常時のレイヤーのずれ検知。warn と運用で守る。ccnavi 自身の開発では、レイヤーを変えながらそれを使う作業を別チケットに分ける
- push の前に `ls-remote` が走る。失敗時は拒否に倒すので、認証が対話式の環境では迂回手段が要る
- `retire/<親>.json` はローカル状態として残る。片付けの列挙元は `ready` を打った PC にしか無い
- ユーザの手が 2 回増える（sh のコピー。ADR-0030 の趣旨に照らし、保護済みファイルの規則から避けられない）
- 規模が大きい。sh 6 本以上、Python 10 ファイル以上、Chrome 拡張とそのテスト、記録に依存する既存テスト（件数は設計時に数える）

やらなかった場合: 取り込み状態が手元だけに残り続け、別 PC で `gone` や `blocked` の食い違いが出る。設計書との食い違いも残る。

## 代案

- **`gone` だけ残して `blocked` と `closed` を消す。** オフラインの hook でも消失を知れるが、手元だけの状態は残り、方針に反する
- **`integration/` コピーを残して `families/` だけ消す。** 変更は小さいが、レイヤーのずれ検知と再利用検知の窓口が手元のコピーに残り、別 PC に引き継げない状態が残る
- **Chrome 拡張を別の作業に分ける。** 手元側を先に直せるが、拡張が組み立てる記録の形と食い違う期間ができる（今回は含める判断）
- **ロックも外す。** 同一 PC の並行する子の `finish` が親ワークツリーを奪い合うため採らない

## ADR と文書

- ADR は新規 1 本（番号は 0142 の見込み。足す直前に `origin/main` を取って最後尾の次を採る）を **design の項**で書く。既存の ADR-0093（取り込みと C1 の D 項）、ADR-0136（記録はキャッシュ、推定しない）、ADR-0103（ブランチからの MR 探索）は、部分的に改めるだけなので、`docs/adr/README.md` の規則に従い**状態行への追記**で扱う。
- `docs/adr/README.md` の一覧に新規の 1 行を足す（`tests/core/test_adr_numbers.py` が見る。ドキュメントだけのコミットでは回らない）。この一覧と追記は docs の項で扱う。
- 直す文書（確認済みの語の hit）: `README.md`、`docs/design/tickets/sharing.md` `places.md`、`docs/CONTEXT.md`、`docs/requirements/extension-if.md`、`docs/claude/exe-boundary.md`、ADR-0102・0104（記録語の hit）。
- **`.ccnavi/` の下は全部が保護対象**（`builtin:guard-project-home`）。sh だけでなく `.ccnavi/docs/*.md` も、エージェントは書けない。次の文書は、完成品を `wip/design/` に置いてユーザがコピーする（staging の項に含める）: `.ccnavi/docs/worktree-start.md`、`worktree-cleanup.md`、`projects.md`、`worktree.md`、`worktree-merge.md`（内容は未読。直す対象かは設計時に確かめる）。`.ccnavi/CCNAVI.md` は記録語の hit が無いため、直す必要があるかは設計時に確かめる。

## 計画の理由

research → design → acceptance → implement-ccnavi（追加）→ staging（1 回目）→ implement-ccnavi（削除）→ staging（2 回目）→ docs の一直線にした。

- research: 上の 11 項目の答えが設計を左右するため、最初に置く
- design: 消す・足す・残すを確定し、ADR と設計書の変更案を書く。拡張の入力、互換の版、push 関門の置き場、`ls-remote` の条件を決める。**research の答えで足りない判断は、design の完了条件でユーザに聞く**（「迂回手段」「放棄した親」「互換」はユーザの判断を増やすため）
- acceptance: 共通の push 関門（3 経路）、再利用検知、レイヤーの warn、`ready` 後の片付け、拡張の判定の受入テストを先に書く。sh のテスト（`tests/sh/*`）は staging のコピーが済むまで通らない
- implement-ccnavi（追加）: 新しい副命令と warn と拡張の受け口を足す。既存の記録の読み書きは消さない。子を分ける想定で、(a) push 関門の副命令、(b) `retire` の拡張と列挙の副命令、(c) warn、(d) 拡張の受け口
- staging（1 回目）: **`.ccnavi/` の下（sh、`.ccnavi/docs/*.md`）はエージェントが書けない。** 完成品を `wip/design/` に全文で置き、ユーザがコピーする。旧実行ファイルでも動く形にし、互換の版は変えない
- implement-ccnavi（削除）: 記録と `integration/` に関わる Python、拡張、テストを消し、互換の版を実行ファイルと拡張で 13 にする。1 回目の staging の sh がコピー済みであることを前提にする
- staging（2 回目）: sh の互換の版を 13 にする差し替え。削除の MR のマージと同時にユーザがコピーする
- docs: `README.md`、設計書、`docs/CONTEXT.md`、要件、ADR の追記と一覧。`README.md` は既存のフェーズ定義 `docs` の範囲に入っているので、フェーズ定義の変更は要らない

## 未解決

- 親の allow から、書けない `.ccnavi/*` と、今回は触らない `build.py` `pyproject.toml` `scripts/*` を外した。`implement-ccnavi` の範囲に `.ccnavi/*` が入っているが、実際には効かない。
- 初回の ADR の番号は、足す直前の `origin/main` で確定する。
