# 受入シナリオの人間の操作を実行器が代わりに打つ（human_run）設計

## 目的

受入シナリオ（`tests/scenarios/`）の `human: true` の step のうち、`ccnavi-user-agree.sh` のように
決まった手順で打てる操作は、実行器（`tests/scenarios/runner.py`）が人間役として打つ。
今は一時停止して人間に頼むので、承認を挟むシナリオ（UC-09・UC-18）を `run` 1 本で通して回せない。

## 前提と受け入れる穴

- ccnavi 本体（hook・`_from_terminal`・`user_only`）は変えない。エージェントが打つ形は、今と同じく止まる
- 実行器は claude の外で、擬似端末（pty）を付けて sh を起動する。ccnavi のターミナル要求は通る
- 実行器はエージェントが Bash から起動できる。エージェントが yml を書いて実行器を通せば、
  開発ワークスペースで自分の承認を出せる。**これは受け入れる**（2026-10-29 にユーザが決めた）
- 実行器は配布物に入らない（`build.py` は `src/` と `.ccnavi/scripts/` だけを配る）。配布先に穴は増えない
- 守るのは「書き誤った yml や、回している間に増えた別の提案で、本物のチケットを承認してしまう」ことだけ。
  悪意のある迂回は対象にしない
- yml に `human_run` を書くことが、その回で実行器が test-repo の提案を承認し push することへの同意になる。
  回す前の確認文には、そのことを出す（後述）。確認事項は増やさない

## yml の形

```yaml
test_repo: true
steps:
  - prompt: "...提案を作って、承認を頼むところで止めて"
  - human: true
    instruction: >-          # 端末が作れないときの代わりの手順。今と同じ。必須のまま
      ワークスペースルートで `sh .ccnavi/scripts/ccnavi-user-agree.sh <親> <子>` を打つ
    check:                   # 人間が見る項目。human_run では評価せず needs-review の結果行にする
      - 承認画面に、親の範囲（README.md）が出ている
    human_run:
      script: agree          # 打てる sh の表（下）の名前
      answers: ["y"]         # 端末へ送る行。省略時は []
```

読み込みで確かめる（`load_scenario` → `_check_human_run`。違えば ValueError）。

- `human: true` の step にだけ書ける。`human_api` と同じ step には書けない
- シナリオが `test_repo: true` である
- `script` は表にある名前。`answers` は文字列の list
- `instruction` がある

成否は終了コードでなく状態で見る（後述）ので、`expect_exit` の欄は持たない。
human step に `expect_after` を付けられない今の決まりも変えない。

## 打てる sh の表

```python
HUMAN_RUN_SCRIPTS = {"agree": "ccnavi-user-agree.sh"}
```

最初は `agree` だけにする。今ある human step で決まった手順になっているのは承認だけだから
（レビューや画面の確認は人間の目が要る）。ほかの sh は、要るシナリオができたときに行を足す。

argv は実行器が組む。yml には書かせない（引数に本物のチケットの識別子や `--root` を書けないように）。

```
sh .ccnavi/scripts/ccnavi-user-agree.sh <対象の識別子>...
```

識別子を必ず並べて打つので、「識別子なしなら承認待ちを全部」の形にはならない。

## 対象のチケット

1. 集め方: `settings.load(root)` で設定を読み、`ccnavi.tickets.model.ticket.scan_all(root, conf.tickets, conf.projects)`
   で提案を集める（同名の `approval.store.scan_all` は承認済みを読む別物）。`state` が `todo` のものの
   `(識別子, project)` を set にする（同じ識別子が複数のツリーに出るので畳む）
2. 基準: 回し始め（`setup` を済ませた直後、最初の step の前）に集めて `st["proposals_before"]` に書く。
   human_run を打ち終えるたびに集め直して更新する（2 回目の human_run が 1 回目の残りを拾わないように）
3. human_run の step で集め、基準にない識別子を対象にする
4. 対象が 0 件なら打たずに止める（理由「承認する提案が増えていない」）
5. 対象に `project` が `test-repo` でないものが 1 件でもあれば、打たずに止める（理由に識別子と置き場）。
   黙って外すと親だけが落ちて承認が失敗するので、外さずに止める
6. 同じ識別子のまま中身を書き換えた提案（親の改版）は増分に入らない。承認を挟むシナリオでは改版の承認を
   扱わないので、受け入れる（必要になったら別に決める）

## 打ち方

- 環境: `dict(os.environ)` から `CCNAVI_GUARD_TICKET_APPROVAL` を外し、`CCNAVI_WORKSPACE` を root に固定する。
  `setup.host` が模擬（`fake-gitlab`・`docker-gitlab`）のときは `_host_env(st)` の値を重ねる（push 先を模擬に向けるため。
  docker-gitlab ではエージェント役のトークンで push するが、承認の push は git の push だけで、誰の名前かは確かめないので構わない）。
  real では `_host_env` が None なので重ねない。人間が打つときと同じ認証になる
- 起動: `os.openpty()` で端末を作り、`subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=root,
  env=..., start_new_session=True)`。起動したら親は slave を閉じる
- 入力: `answers` は起動直後に 1 行ずつ（改行付きで）master に書く。端末の行単位の入力なので、sh が読むまで溜まる。
  子が先に終わって書けない例外（`OSError`）は握る
- 出力: `select` で締め切り（300 秒）まで master を読む。`EIO` は EOF として扱う。`\r` を除いて
  `human-run-<step>.log` に書き、表示には末尾の数行だけを出す
- 時間切れ: `os.killpg(pid, SIGKILL)` でプロセスグループごと止め、`wait()` してから止める（halt）。
  孫（ccnavi の実行ファイル・git）が残って、後片付けのあとに承認や push を書くのを防ぐ。
  `KeyboardInterrupt` でも同じ後始末を通す

## 成否

終了コードだけでは足りない。`ccnavi-user-agree.sh` は push に失敗しても、保護ブランチで push しなくても、
承認待ちが空でも 0 で終わる。

- 打ったあと、`approval.store.scan_all(conf, root)` で承認済みチケットを集め、対象の識別子が全部あるかを見る。
  欠けていれば止める（理由に欠けた識別子、終了コード、ログのパス）
- push まで済んだかは、ここでは見ない。push の結果を要るシナリオは、続く prompt の step の `expect_after.state`
  か、末尾の `expect.state` で確かめる
- 止めたときの結果は `inconclusive`（`halt`、`kind: human_run`）。後続の step には進まない

## 端末が作れないとき

Windows の Python には `pty` が無い。`os.openpty` が無いか失敗したら、今の human step と同じく
`instruction` を表示して一時停止する（終了コード 10）。表示に「この環境では人間役を打てないので、手で打つ」と添える。

## 再開と記録

- 打つ前に `st["human_runs"]` へ `{step, script, tickets, status: "started"}` を足して書く。
  終わったら `status` を `done`（か `halted`）にして、`next_step` を進める
- 再開（`next`）で、その step の記録が `started` のまま残っていたら打ち直さずに止める
  （理由「前の回で打ち始めたまま止まった。承認と push の状態を確かめてから回し直す」）。自動では打ち直さない
- state は `st.get(...)`・`setdefault` で読む（この欄の無い古い `state.json` を `next` で続けても落ちない）

## 結果と表示

- human_run の step の `check` は、項目ごとに `needs-review`（REVIEW）の結果行にする。
  人間が見ていた項目を誰も見ていないので、`overall` は `pass` にならない。ログのパスを添えて、あとから人間が見られるようにする
- `scenario_summary`・`confirmation_text`・`step_answer_lines` は、human_run の step を
  「実行器が打つ: `ccnavi-user-agree.sh`（この回で増えた test-repo の提案を承認し、push する）」と出す
- `run_all_reason` は変えない。human step のあるシナリオは、human_run でも run-all から外れる
  （`check` が needs-review になるので、run-all で続けて回しても pass で閉じない）
- `drive` の分岐は `human_api` の隣に置く（`if step.get("human") and step.get("human_run")`）

## 文書の変更

- `docs/42_claude/acceptance-scenarios.md`
  - 基本の決まりの「人間しかできない操作は一時停止して人間に打ってもらう」に、「`human_run` があれば実行器が人間役として打つ」を足す
  - シナリオの形に `human_run` の欄と決まり（`test_repo: true`、表、増えた test-repo の提案だけ、成否は承認済みに在るか、
    `check` は needs-review）を書く
  - 受け入れた穴（開発ワークスペースでは、エージェントが実行器を通して承認を出せる）と、配布物に入らないこと、
    `human_run` を書くことが承認と push への同意になることを書く
- UC-09・UC-18 の承認の step に `human_run` を足すのは、実ログで確かめてから（`status` は draft のまま）

## テスト（`tests/scenarios/test_runner.py`）

偽の sh（`[ -t 0 ]` を確かめ、1 行読み、引数を書き出して終了コードを返す）を `HUMAN_RUN_SCRIPTS` に差し替えて回す。

- 読み込みの検査: 各 ValueError の条件
- 対象: 基準との差、test-repo 以外が混ざると止まる、0 件で止まる、2 回目の human_run で基準が更新されている
- 打ち方: 端末が付く、`answers` が届く、孫プロセスが slave を握ったままの時間切れで止まり孫も残らない
- 成否: 終了コード 0 でも承認済みに無ければ止まる
- 再開: `started` のまま残った記録で止まる、古い `state.json` で落ちない
- 結果: `check` が needs-review の行になる
- 端末が無いとき: `os.openpty` を無くすと一時停止（10）になる
