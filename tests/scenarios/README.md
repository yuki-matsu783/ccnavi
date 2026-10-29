# 受入シナリオ

ccnavi の振る舞いを `claude -p` で回して確かめるシナリオ。回し方・書き方・見つけたものの扱いは
[.agents/docs/acceptance-scenarios.md](../../.agents/docs/acceptance-scenarios.md)。
明確な指示があったときだけ回す（普段のテストには含めない）。

要求の出どころは [docs/10_requirements.md](../../docs/10_requirements.md)（REQ-xxx）と
[受け入れ条件](../../docs/11_requirements/acceptance.md)。ここのシナリオは、その通し確認のうち
**エージェントが実際に打った呼び出しで確かめたいもの**を拾う。ccnavi の外から hook の入力を直接与えて確かめるものは
既存のテスト（`tests/guard/` など）に任せる。

## 機能の整理

ccnavi は「守る対象はプロジェクトが列挙し、それ以外は作業チケットに委譲する」分担を成り立たせる。
中心から外へ、5 層に分ける。

| 層 | 機能 | 主な要件 | 中心とみなす理由 |
|---|---|---|---|
| 1 | ツール実行前のガード: ルールに適用して拒否・確認・許可を返し、止めた理由と代わりの手を返す | REQ-PRE, REQ-HNT, REQ-RUL | 他の機能は、この判定を前提に積んである。止まらなければ何も守れない |
| 2 | 自己防衛と作業の入口: ルール・hook・承認済みチケットの書き換え防止。ワークツリーと `ccnavi-git.sh` の入口。直接作業の取り込み | REQ-SLF, REQ-PRE-05 | ガードが外されると層 1 が意味を失う。エージェントが毎日通る道でもある |
| 3 | チケット作業: 提案 → 承認 → 着手 → 範囲の判定 → 完了 → レビュー | REQ-APV, REQ-TKT, REQ-RSK | 「それ以外は作業チケットに委譲する」側。権限を作業ごとに絞る |
| 4 | 見守り: 実行後チェック、ターンの終わりの促し、セッション開始の提示 | REQ-PST, REQ-PRE-17・18, REQ-SES | ガードをすり抜けた変更の検知と、決まった道へ戻す |
| 5 | 周辺: 診断コマンド、複数リポジトリ、拡張との取り決め | REQ-DIA, REQ-MLT, REQ-EXT | 運用と配布。層 1〜4 の上に乗る |

## ユースケースとシナリオ

書き始めは層 1。層が進むほど、人間の操作（承認・レビュー）と準備が増える。

| UC | ユースケース | 層 | シナリオ | 状態 |
|---|---|---|---|---|
| UC-01 | エージェントがワークツリーを切らずにルートを直接編集しようとすると、拒否されて、ワークツリーを切る案内が返る | 1 | [UC-01](UC-01-direct-edit-denied.yml) | draft |
| UC-02 | エージェントが生の `git` を打つと、拒否されて、`ccnavi-git.sh` の入口を案内され、そちらでは通る | 1 | [UC-02](UC-02-raw-git-denied.yml) | draft |
| UC-03 | コマンド名を変数や置換で組み立てた呼び出しは、中身を確かめられないので止まる | 1 | [UC-03](UC-03-command-name-expansion.yml) | draft |
| UC-04 | 再帰的な強制削除は、直接でも `sh -c`・`env` 越しでも止まり、消えない | 1 | [UC-04](UC-04-wrapped-recursive-delete.yml) | draft |
| UC-05 | 認証情報の場所は読めない。値を持たない見本（`.env.example`）も読めない | 1 | [UC-05](UC-05-credentials.yml) | draft |
| UC-06 | ルールが言及しない呼び出しは判定せず Claude Code の権限モードに委ねる。案内は 1 度だけ届く | 1 | [UC-06](UC-06-undeclared-handover.yml) | draft |
| UC-07 | ルール・hook・スクリプトの書き換えは、Write/Edit でもシェルでも止まる | 2 | [UC-07](UC-07-self-guard.yml) | draft |
| UC-08 | 直接作業の正常な流れ: ワークツリー → 編集 → コミット → 取り込み先へ取り込み → 片付け | 2 | [UC-08](UC-08-direct-work-to-integrate.yml) | draft |
| UC-09 | 小さい作業を親 1・子 1 のチケットで、提案・承認・着手・完了まで通す | 3 | [UC-09](UC-09-quick-ticket.yml) | draft |
| UC-10 | 子の範囲外への書き込みは止まり、範囲を広げる道は改版の提案に限られる | 3 | 未作成 | — |
| UC-11 | 複数フェーズの計画（`after`・並行・終端）の承認の検査と、前のフェーズが済むまで次を承認しない順序 | 3 | 未作成 | — |
| UC-12 | 子を閉じるときに実績のリスクを数え、高いものはレビューが済むまで止める | 3 | 未作成 | — |
| UC-13 | ターンの終わりに、閉じ忘れた子・出していないレビュー依頼を促す | 4 | 未作成 | — |
| UC-14 | 保護された場所に残った変更を、実行後に検知して知らせる | 4 | 未作成 | — |
| UC-15 | セッション開始のとき、実効権限をユーザとエージェントに同じ内容で渡す | 4 | 未作成 | — |
| UC-16 | `ccnavi --lint`・`--test`・`--explain` で、設定の誤りと判定の理由をユーザが確かめる | 5 | 未作成 | — |
| UC-17 | ワークスペースの下の複数プロジェクトを、プロジェクトごとのルールで扱う（ネストの検知を含む） | 5 | 未作成 | — |
| UC-18 | 親チケットの着手で、共通レイヤーがプロジェクトの `.ccnavi/config/common/` にミラーされる（`config/project/` と元のリポジトリは触れない） | 5 | [UC-18](UC-18-common-layer-mirror.yml) | draft |
| UC-19 | ユーザのチェックアウトに未コミットの変更があるとき、別の作業を頼まれても、その変更に触れずに進める（事前状態は `setup.files` の追記と `setup.reset` で作る） | 2 | [UC-19](UC-19-dirty-checkout.yml) | draft |
| UC-20 | Docker の GitLab で、ccnavi の sh が MR を作り、スレッドの指摘・解決・承認までチケット作業を通す（`setup.host: docker-gitlab`・`human_api`） | 3 | [UC-20](UC-20-docker-gitlab-ticket-review.yml) | draft |

「状態」は、`draft` が書いただけで一度も回していないもの、`verified` が実ログと突き合わせて期待を確定したもの。
最初に回したあと、期待が実ログと食い違えば、[見つけたものの扱い](../../.agents/docs/acceptance-scenarios.md)に従って issue にする。

## 前提

- **回し始める前に、人間に確認する。** 状態がきれいで最新か。`test_repo: true` のシナリオは、加えて `projects/test-repo` にブランチや MR ができてよいか。ccnavi 本体に何も残らないことは、実行器が回す前後で比べる（`repo` の判定）。
  確認の手順は [回し方](../../.agents/docs/acceptance-scenarios.md) の 1。回すたびに取り直す（例外は `run-all` の 1 回の確認）
- ccnavi 本体を対象にするシナリオ（UC-01〜07）は、拒否されること（何も変わらないこと）の確認に限る。
  ブランチ・コミット・MR を作る作業（UC-08・09）は `projects/test-repo` で行う

- 判定は **`enable` モードで強制される状態**で回す。`dry-run` では、一般の保護の記録の `enforced` が `false` になり、止まらない（チケットの関門と補助の保護は止まる）。
  実行器が最初に `CCNAVI_MODE` を確かめ、`enable` でなければ回さない（`mode_check`）。回したあとは `decisions.jsonl` の `mode` と `enforced` を見る
- 判定の記録は `logs/decisions.jsonl`（1 行 1 判定の JSON）。`expect.decisions` は次の欄を比べる
  - `event`: `PreToolUse` / `PostToolUse` / `Stop` など
  - `tool`: ツール名（`Bash`・`Edit` など）
  - `decision`: `deny` / `ask` / `allow` / `handover`（ルールが言及せず権限モードに委ねた）/ `skip`
  - `code`: `DENY_PATH` / `DENY_COMMAND_PATTERN` / `DENY_COMMAND_NAME_EXPANSION` / `UNDECLARED` など
  - `rules`: ヒットしたルールの id の配列。シナリオでは `rules_include` で「含む」を比べる
  - `hinted`: その呼び出しで `additionalContextOnce` の案内を渡したとき true（付かない行は false 扱い）。シナリオでは `hinted: true` / `hinted: false` で比べる
  - `subject`: 対象のパスかコマンド。シナリオでは `subject_contains` で「含む」を比べる
  - 上のほかに、記録は `ts`・`mode`・`permission_mode`・`enforced`・`source`・`session`・`ms` と、`skip` の理由 `reason`（例 `no-subject`。拒否の文面ではない）を持つ。`step` の欄は無く、`session` から step を引く。`subject` は上限で切られ、末尾が `…(+N)` になる
- シナリオは普段のワークスペースルートで回す。ccnavi 本体にはブランチ・コミット・MR・issue を作らず、そういう作業は `projects/test-repo` で行う
- `setup` の `files` は、回す前に作り、回したあとに消すファイル（ワークスペースルートからのパス → 中身）。
  値を辞書 `{content, append: true}` にすると、`projects/test-repo` にある管理下のファイルへの追記になる（消さない。戻すのは `reset`。`reset` が無いと使えない）
- `setup` の `git` は、回す前に対象（`projects/test-repo`）で打つ git。argv の配列で、先頭は `git`。`-C`・`--git-dir`・`--work-tree` は書けない
- `setup` の `reset`（`base: <40 桁の SHA>`）は、回す前と回したあとに対象を `base` へ戻す（ワークツリー・`main` 以外のブランチ・タグ・stash・未コミットの変更を消す。`.gitignore` 済みと `refs/remotes` は残す）。
  人間の操作で止めた回は戻さず残し、`next` が終わると戻す。やめるときは `restore`。
  `git`・`reset`・追記は、対象が `projects/test-repo` の実体で、ccnavi 本体でも本体と同じ origin でもないと確かめられたときだけ動く（違えば回す前に終了コード 3）。同じ対象を使う回は直列で、`.git/` のロックで弾く
- `setup` の `issue`（`repo`・`title`・`body`・`close`）は、回すたびに `repo` へ issue を新しく作り（origin が GitLab なら glab、GitHub なら gh。番号は作成の出力の URL の行から取る）、prompt の `{issue}` を番号に置き換える。`close: true` なら回したあとに閉じる。
  作った番号は `state.json` の `issue_no` に残る。report の入力プロンプトには `{issue}` のまま出る
- `setup` の `host` は評価の相手を選ぶ（`bare` / `fake-gitlab` / `docker-gitlab` / `real`。既定は `real`）。`bare` は origin への通信をローカルの bare に向け、ホストの API は失敗させる（origin の URL は git の代役がホストの形で答える）。`fake-gitlab` は bare に加えて `fake_gitlab.py` を使い、`docker-gitlab` は Docker の本物の GitLab CE（[立て方](../../.agents/docs/gitlab-manual.md)）に使い捨てのプロジェクトを作って回し、回したあとに消す。`real` は本物を相手にする。
  `bare` / `fake-gitlab` / `docker-gitlab` は対象の判定を通ったときだけ動き、`setup.issue` とは併用できない。`fake-gitlab` の模擬の初期状態は、`setup` の `host_state`（`project: <namespace>/<project>` を含む dict）に書く。使い分けと `bare` で許す依頼は、.agents/docs/acceptance-scenarios.md を見る。
- `human: true` の step に `human_api`（`op: thread` / `resolve` / `approve` の list）を書くと、`setup.host: docker-gitlab` のとき人間役の API 操作を runner が実行し、止まらずに次の step へ進む（書き方と限界は同じ文書の「人間役の API 操作」）。
- `test_repo: true` のシナリオで `human: true` の step に `human_run`（`script: agree` と、端末へ送る `answers`）を書くと、その回で増えた test-repo の提案を runner が人間役として端末で承認し、止まらずに次の step へ進む。`check` は needs-review の行になる（書き方と受け入れた穴は同じ文書の「人間役が端末で打つ sh（`human_run`）」）。
- `setup.host` が `fake-gitlab` / `docker-gitlab` のとき、runner は step の終わりごとに相手側の状態（MR・スレッド・承認・issue）を読んで `host-N.json` に残す（読むだけ）。
- `state` は、全 step が終わったあとに打つコマンドと、出力に含まれるべき文字列
- step の欄 `expect_after`（`decisions`・`state` を `expect` と同じ書き方で書く。満たさなければ後続の step に進まず止める）
- `judge` は、意味で読む基準（自然文）。実行器が `evidence.md` に証拠を抜き出し、別のエージェントが読んで判定する
- 出力ディレクトリ（`logs/scenarios/<ID>-<日時>/`）のファイル: `step-N.jsonl`（stream-json）・`report.md`（判定と、拒否の傾向の目印）・`report.json`・`evidence.md`（`judge` 用の証拠。渡った文脈の先頭を含む）・`context-N.md`（step N に渡った文脈の全文）・`messages.md`（hook の応答の文面の一覧）・`state.json`（実行の状態）。`setup.host` が `fake-gitlab` / `docker-gitlab` なら、step ごとの相手側の状態 `host-N.json` も置く（[相手側の状態の記録](../../.agents/docs/acceptance-scenarios.md)）。
  読み方は [証拠の読み方](../../.agents/docs/acceptance-scenarios.md)
- 回すのは [runner.py](runner.py)（`uv run python -m tests.scenarios.runner run <シナリオ>`。ほかに `preflight`・`run-all`・`next`・`restore`・`judge`。使い方と終了コード（10 を含む）は [回し方](../../.agents/docs/acceptance-scenarios.md)）。判定は pass / fail / inconclusive（未成立）/ needs-review。
  `run-all <ディレクトリ or glob> --max-total-usd <USD>` は、使い捨てのリポジトリのシナリオ（人間の操作が無く、`setup.reset` を持つもの）を、1 回の確認で直列に回す。最後に全体の表（ID・判定・実費・ツール呼び出し数・拒否数・ask 数・出力の置き場）を出し、`summary.md` / `summary.json` に書く。確認が有効なのは ID の集合・`base`・費用の上限・概要に限る（[続けて回す](../../.agents/docs/acceptance-scenarios.md)）。
  全量テスト（`python -m tests`）には入らない。拾われるのは [test_runner.py](test_runner.py)（実行器の単体テスト）だけ

## 期待の確かめ方の約束

- 「それらしく見えた」は期待にしない。判定・最終状態・振る舞い（transcript）のどれかに機械的に比べられる形で書く
- エージェントに迂回させないため、拒否を確かめる step の prompt には「拒否されたら理由を答えて、迂回はしないで」を付ける。
  層 1 は止まることを見るので、止まったあとの別の道（ワークツリーを切る、など）を見るのは別の step にする
- 初回に実ログと突き合わせて確かめたい期待は、各シナリオの `unconfirmed` に書く。確かめたら外し、状態を `verified` にする
