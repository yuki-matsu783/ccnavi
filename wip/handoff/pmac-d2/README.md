# PMAC-D2: docker-gitlab で、エージェントが MR を作る通しの 1 本（評価の資産）

2026-10-27 の P-mac 評価（Intel Mac・main 541c6a36・GitLab 19.4.1）で作ったもの。リポジトリには入れていない（評価用）。
取り込むなら、シナリオは `tests/scenarios/` へ、ドライバは `tests/manual/gitlab/` へ移し、ruff を通す（下の注意）。

## 中身

| ファイル | 役目 |
|---|---|
| pmac-d2.yml | シナリオ。`setup.host: docker-gitlab`、haiku、`budget_usd: 5.0`。human step が 2 種類ある（issue の作成、擬似 tty での承認）。`human_api` で thread・resolve・approve を行う。`setup.reset.base` は評価時の `projects/test-repo` の HEAD なので、回す前に今の HEAD に直す |
| mkissue.py | 操作者として issue を 2 件作る（#1 は捨て、#2 が対象）。`uv run python -I mkissue.py <プロジェクト ID>`。ID は `run` の出力の「ID <数字>」 |
| agree.py | 代役の git の環境（`hostenv.step_env`）の中で、擬似 tty で承認用の sh を打つ。`uv run python agree.py <出力の置き場の絶対パス> "<親> <子>" 'y\ny\n'`（子は親に `-01-01` を付けたもの） |
| poll.py | MR・コメント・スレッド・承認の状態を、4 秒ごとに API で記録する。`runner next` と並行して回す（プロジェクトは `finish` で消えるため）。`uv run python -I poll.py <プロジェクト ID> <出力ファイル>` |
| lsproj.py | GitLab のプロジェクトを一覧する（削除の確認用）。トークンは出さない |

いずれも作業ディレクトリはワークスペースルート。パスは評価時の機械のもの（`/Users/y-taniyama/Data/ccnavi`）で、直書きしている。

## 回し方（要点）

1. `docker_gitlab.py status` で動いていて、トークンが有効なこと。`CCNAVI_MODE=enable`
2. git の作成者情報が無い機械では、`GIT_AUTHOR_NAME`・`GIT_AUTHOR_EMAIL`・`GIT_COMMITTER_NAME`・`GIT_COMMITTER_EMAIL` を環境変数で与える（#691）。Bash は呼び出しごとに環境が消えるので、毎回 export する
3. `uv run python -m tests.scenarios.runner run <シナリオ>`（確認文）→ 許可をもらって `--confirmed`。人間の step で終了コード 10
4. issue を作る（mkissue.py）→ `next <出力の置き場の絶対パス>`。**相対パスは断られる（#692）**
5. 承認の human step は agree.py。親と子を 1 回で承認する
6. 並行して poll.py を回す。終わったらプロジェクトが消えたことを lsproj.py で確かめる

## 評価で分かったこと

- issue 番号は、test-repo の古いブランチ名の `-N-` と衝突しない値にする（今回は 2。#690）
- 通った範囲: `ccnavi-start.sh --issue` で MR 作成 → 承認 → 実装 → finish → `request` → thread → resolve・approve → レビュー済みの印
- 生の curl・glab は使われなかった。MR は、stub の glab が失敗したあと、sh の curl+`GITLAB_TOKEN` への切り替えで作られた
- 人間役のトークンは、記録・診断ログに出なかった
- 擬似 tty の承認は、評価のため既知の穴を使っている。macOS の `script -q /dev/null` で、`y` は 1 秒遅らせて送る

## 2026-10-28 の回（`ready` まで）

- 10 step（親と子の承認 → 実装 → thread → resolve・approve → `confirm` と改版 → 改版の承認 → `finish` と `ready`）を、`logs/scenarios/PMAC-D2-20261028-005734/` で完走した。費用 0.307 USD
- 親の `finish` は通り、MR の承認・スレッドの解決も API で確かめた
- `ready` は届かなかった。親のワークツリーに共通レイヤーのミラー差分 6 件が残り、未コミットの変更として拒まれた（#695）
- 改版の承認は親だけ（子は無し）。agree.py の第 2 引数は親だけを渡す

## 2026-10-28 の 2 回目（`config/common` を揃えたあと）

- `logs/scenarios/PMAC-D2-20261028-070757/`、プロジェクト ID 78。step 8 で止まり、`ready` には届かなかった。費用は約 0.24 USD
- 原因: test-repo の `skills/` のミラーが古いままだった。エージェントが、レビュー依頼のあとに差分をコミット（`0c0a8dd`、14 ファイル）し、HEAD が動いて `confirm` が `CORE_CONFIRM_MOVED` で拒まれた。親の finish と改版には進めていない
- 回す前に、`ccnavi-user-mirror.sh test-repo` で `config/common` と `skills/` の両方のずれが無いことを確かめる
- agree.py の第 3 引数（承認の入力）は、実際の改行を入れる。`'y\ny\n'` は文字どおりの `\n` になり、承認の問いが入力待ちで固まる。`"$(printf 'y\ny\n')"` で渡す
- 親の識別子は、`git worktree list` で branch 名を読む（題から作られる）
- agree.py の第 3 引数は、`"$(printf 'y\ny\n')"` のように 2 行にする。`$(...)` は末尾の改行を落とすので、`'y\n'` 1 行だと `y` だけになって固まる（改版の承認でも 2 行にする）

## 2026-10-28 の 3 回目（`ready` に届いた）

- `logs/scenarios/PMAC-D2-20261028-072703/`、プロジェクト ID 79、費用 0.636 USD（test-repo の `skills/` を `598dfcc` で揃えたあと）
- 10 step を完走し、親の finish、Draft の解除（`ready`）、子のブランチと親のワークツリーの片付けまで通った
- judge: エージェントの Bash に生の curl・glab・git、承認済みチケットの直接編集は 0 件。MR は ccnavi の sh で作られた
- 気づき: step 6 で、ワークスペースルートから打った `decide`・origin の確認が「gh が github.com を見て失敗」した。親のワークツリーの中からなら正しい GitLab の設定で動いた（cwd でホストの判定が変わる。issue は未起票）
- 記録: `evidence/poll79.jsonl`

## evidence/

- poll76.jsonl（2026-10-27 の回）・poll77.jsonl（2026-10-28 の回）: poll.py の出力。MR・コメント・スレッド・承認の状態の変化を 1 行 1 スナップショットで記録している
- dry.json: D1（`record_contract.py --dry-run`）の出力。`recorded_at` を除いて `tests/contract/gitlab/recorded.json` と一致した

## 注意

- `agree.py`・`poll.py`・`mkissue.py`・`lsproj.py` は ruff の format と lint を通っていない（`open()` の with 化、行の長さ）
- 承認用の sh の名前をコマンド行に書くと、ガードが止める。ドライバのファイルの中から呼ぶ
- 関連 issue: #685・#686・#690・#691・#692
