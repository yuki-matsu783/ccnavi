# 受入シナリオの人間の操作を実行器が代わりに打つ（human_run）設計

## 目的

受入シナリオ（`tests/scenarios/`）の `human: true` の step のうち、`ccnavi-user-agree.sh` のように
決まった手順で打てる操作は、実行器（`tests/scenarios/runner.py`）が人間役として打つ。
今は一時停止して人間に頼むので、承認を挟むシナリオ（UC-09・UC-18）を通しで自動で回せない。

## 前提と受け入れる穴

- ccnavi 本体（hook・`_from_terminal`・`user_only`）は変えない。エージェントが打つ形は、今と同じく止まる
- 実行器は claude の外で、擬似端末（pty）を付けて sh を起動する。ccnavi のターミナル要求は通る
- 実行器はエージェントが Bash から起動できる。エージェントが yml を書いて実行器を通せば、
  開発ワークスペースで自分の承認を出せる。**これは受け入れる**（2026-10-29 にユーザが決めた）
- 実行器は配布物に入らない（`build.py` は `src/` と `.ccnavi/scripts/` だけを配る）。配布先に穴は増えない
- 守るのは「書き誤った yml で、本物のチケットを承認してしまう」ことだけ。悪意のある迂回は対象にしない

## yml の形

```yaml
test_repo: true
steps:
  - prompt: "...提案を作って、承認を頼むところで止めて"
  - human: true
    instruction: >-          # 端末が無いとき（後述）の代わりの手順。今と同じ。必須のまま
      ワークスペースルートで `sh .ccnavi/scripts/ccnavi-user-agree.sh <親> <子>` を打つ
    check:                   # 人間が見る項目。human_run のときは表示と記録だけで、評価しない
      - 承認画面に、親の範囲（README.md）が出ている
    human_run:
      script: agree          # 打てる sh の表（下）の名前
      answers: ["y"]         # 端末へ順に送る行。省略時は []
      expect_exit: 0         # 省略時は 0
```

- 読み込みで確かめる（`load_scenario` → `_check_human_run`。違えば ValueError）
  - `human: true` の step にだけ書ける
  - `human_api` と同じ step には書けない
  - シナリオが `test_repo: true` である
  - `script` は表にある名前。`answers` は文字列の list。`expect_exit` は整数
  - `instruction` がある（端末が無いときの代わり）

## 打てる sh の表

```python
HUMAN_RUN_SCRIPTS = {
    "agree": "ccnavi-user-agree.sh",
}
```

最初は `agree` だけにする。今ある human step で決まった手順になっているのは承認だけだから
（レビューや画面の確認は人間の目が要る）。ほかの sh は、要るシナリオができたときに行を足す。

argv は実行器が組む。yml には書かせない（引数に本物のチケットの識別子や `--root` を書けないように）。

```
sh .ccnavi/scripts/ccnavi-user-agree.sh <この回で増えた test-repo の提案の識別子>...
```

## 対象のチケット（この回で増えたものだけ）

1. 回し始め（`setup` を済ませた直後、最初の step の前）に、承認待ちの提案の識別子を集め、
   `st["proposals_before"]` に書く
2. human_run の step で、もう一度集める。増えた分（後 − 前）を対象にする
3. 集め方: ccnavi の読むだけの関数（`ticket.scan_all`）で提案を読み、`project` が `test-repo` で
   `todo/` にあるものだけを取る。ワークスペースルートや他のプロジェクトの提案は数えない
4. 増えた分が 0 件なら打たない。`halt`（`kind: human_run`、理由「承認する提案が増えていない」）で止め、
   `inconclusive` にする

識別子を必ず並べて打つので、`ccnavi-user-agree.sh` の「識別子なしなら承認待ちを全部」の形にはならない。

## 打ち方

- `os.openpty()` で端末を作り、`subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=root)`
- 環境は `_host_env(st)` から `CCNAVI_GUARD_TICKET_APPROVAL` を外したもの（本物の端末の経路を通すため。
  外に `disable` が残っていても使わない）
- `answers` は起動直後に 1 行ずつ（改行付きで）書く。端末の行単位の入力なので、sh が読むまで溜まる
- 出力は EOF まで読み、`human-run-<step>.log` に書く。表示には末尾の数行だけを出す
- 時間の上限は 300 秒。超えたら止めて（kill）、`halt` で `inconclusive`
- 終了コードが `expect_exit` と違えば `halt`（理由に終了コードとログのパス）で `inconclusive`

## 端末が作れないとき

Windows の Python には `pty` が無い。`os.openpty` が無いか失敗したら、今の human step と同じく
`instruction` を表示して一時停止する（終了コード 10）。表示に「この環境では人間役を打てないので、手で打つ」と添える。

## 再開と記録

- 結果は `st["human_runs"]` に 1 件ずつ書く（`step`・`script`・`tickets`・`exit`・`log`）
- 書いてから `next_step` を進める。`next` で再開しても、済んだ step を打ち直さない
- 報告（evidence.md）に「人間役の操作（端末）」として、打ったコマンド・対象の識別子・終了コード・`check` の項目
  （評価していないことを明記）を出す
- `drive` の分岐は `human_api` の隣に置く（`if step.get("human") and step.get("human_run")`）

## 文書の変更

- `docs/42_claude/acceptance-scenarios.md`
  - 基本の決まりの「人間しかできない操作は一時停止して人間に打ってもらう」に、「`human_run` があれば実行器が人間役として打つ」を足す
  - シナリオの形に `human_run` の欄、決まり（`test_repo: true`、表、この回で増えた提案だけ）を書く
  - 受け入れた穴（開発ワークスペースでは、エージェントが実行器を通して承認を出せる）と、配布物に入らないことを書く
- `tests/scenarios/README.md` の表に変更があれば合わせる
- UC-09・UC-18 の承認の step に `human_run` を足すのは、実ログで確かめてから（`status` は draft のまま）

## テスト（`tests/scenarios/test_runner.py`）

- 読み込みの検査: 各 ValueError の条件
- 対象の選び方: 前後の集合の差、他のプロジェクトの提案を数えないこと、0 件で止まること
- 打ち方: `HUMAN_RUN_SCRIPTS` を差し替えた偽の sh（`[ -t 0 ]` を確かめ、1 行読んで終了コードを返す）で、
  端末が付くこと・`answers` が届くこと・終了コードの違いで止まること・時間切れ
- 再開: 済んだ human_run を打ち直さないこと
- 端末が無いとき: `os.openpty` を無くすと一時停止（10）になること
