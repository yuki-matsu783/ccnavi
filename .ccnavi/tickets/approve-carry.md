---
version: 1
ticket: approve-carry
title: ボードの承認もコミットと push まで運び、範囲の超過は承認で止めず判定で止める
rationale: '承認の経路の 2 つの抜けを直す。 1) VS Code のボードで承認すると承認済みチケットが置かれるだけで、コミットと push が起きない。
  コミットと push は ccnavi-approve.sh にしか無く、ボードは実行ファイルの --approve --yes を直に打つため。 sh を「承認」と「運ぶ」に分け、ボードは承認のあと「運ぶ」を端末に送る。
  2) 子の範囲の超過（親の範囲・フェーズの種類の scope・regex）は承認で必ず弾かれ、dry-run でも 範囲を書き直すまで承認できない。承認では警告に下げて通し、代わりに判定のときにフェーズの種類の
  scope でも切り詰めて deny / ask にする。dry-run ならいつもどおり言うだけで通る。

  '
human_review:
  required: true
  reason: 承認の検査を緩め、判定の範囲の決め方と配布する sh を変えるため
plan:
- design
- acceptance
- implement
- staging
- docs
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: docs/*
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: scripts/*
- match: Write|Edit
  glob: vscode-extension/*
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: CLAUDE.md
started_at: ''
completed_at: ''
base_sha: ''
ccnavi_approved:
  approved_at: 2026-09-13T19:36:21+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry.md
  revised_at: 2026-09-15T16:40:38+0900
  feedback_at: 2026-09-15T16:40:38+0900
feedback: []
---

# ボードの承認もコミットと push まで運び、範囲の超過は承認で止めず判定で止める

2026-09-13 のセッションで決めたことの控え。

## 1. ボードの承認がコミットされない

### 今

- 承認済みチケットは親のツリーの `.ccnavi/tickets/` に置かれ、コミットして push するまで他の機械に
  届かない（設計 §9.2）
- コミットと push は `ccnavi-approve.sh` の後半にしか無い（`.ccnavi/scripts/ccnavi-approve.sh:86-121`）
- ボードは `--approve --yes` を子プロセスで直に打つ（`vscode-extension/ccnavi-board/src/ccnavi.ts`）ので、
  置かれた承認済みチケットは未追跡のまま残る
- `ccnavi-fetch.sh` もレビューの前提検査も `--untracked-files=no` で見るので、誰も気づかない

### 決めたこと（案 A）

- `ccnavi-approve.sh` を分ける。後半（ツリーを数え、承認済みチケットだけをパスを限ってコミットし、
  保護されたブランチ以外へ push する）を別の sh に切り出す。`ccnavi-approve.sh` は承認のあとそれを呼ぶ
- ボードは `--approve --yes` が通ったら、切り出した sh を端末に送る（`accept` と同じ `runInTerminal`）。
  push の認証を求められても人に見える
- 導入スクリプトの `DEPLOY_SCRIPTS` に加える

### 受け入れる代償

- ボードで承認するたびに端末が開く
- 端末を閉じる・push が落ちると運ばれない（コミットは残るので、もう一度打てば送れる）

## 2. 範囲の超過は承認で止めず、判定で止める

### 今

- 子の承認で次の 3 つが error になり、束から外れる。モードを問わない
  - 子の範囲が親の範囲を超えている（`ticket.subset_problems`）
  - 子の範囲がフェーズの種類の scope を超えている（`phasetypes.scope_problems`）
  - 子の範囲が regex で、上の 2 つを確かめられない
- dry-run では判定が止めないのに、承認だけは範囲を書き直すまで通らない
- 判定のときは親の範囲で切り詰める（`ticket.combine`）が、フェーズの種類の scope は見ていない

### 決めたこと

- 承認では 3 つとも warn に下げ、束に載せる。承認画面とボードに警告として出す
- 形が壊れているもの（親が承認されていない、深さ、計画に無い番号、種類が読めない、project）は今のまま error
- 判定のときに、子の範囲をフェーズの種類の scope でも切り詰める。親と同じく厳しい側が勝つ形。
  超えた先への書き込みは、範囲の外と同じく deny / ask になる
- dry-run ではその判定をいつもどおり言うだけで通す。言うときに dry-run であることと、
  どの上限（親 / 種類）を超えたかを添える
- 承認の処理はモードを読まない（判定で切り詰めるので、承認で分ける理由が無い）

### 受け入れる代償

- 承認できても、超えた部分は enable では書けない。承認したのに止まる形になるので、承認画面の警告と
  判定の文面で、どの上限に当たったかを名指しする
- 判定がフェーズの種類を読むようになる。種類が読めないときにどちらへ倒すかを設計で決める
- 承認で弾いていた守りが判定に移る。判定側の穴は承認では拾えなくなる

## 変える場所（設計フェーズで詰める）

- `.ccnavi/scripts/ccnavi-approve.sh` と、切り出す sh（staging で人が写す）
- `scripts/ccnavi-setup.sh`: `DEPLOY_SCRIPTS`
- `vscode-extension/ccnavi-board/src/board-panel.ts` / `core/commands.ts`: 承認のあとの端末送り
- `ccnavi/approval.py` / `ccnavi/ticket.py` / `ccnavi/phasetypes.py`: 超過を warn に
- `ccnavi/judge.py` ほか判定の範囲の合成: 種類の scope での切り詰め、文面（`ccnavi/reasons.py`）
- `ccnavi/subagent.py`: 範囲外の差し戻しが種類の scope も数えるか
- テスト、README / ccnavi.md（§9）/ requirements.md

## 今回入れないもの

- 未追跡の承認済みチケットを `ccnavi-fetch.sh` やボードが知らせる検出（案 D）
- 承認の処理が `CCNAVI_MODE` を読む形

## 他のチケットとの重なり

- `launcher-scripts` も `.ccnavi/scripts/` と `DEPLOY_SCRIPTS` を触る。合流の順で衝突を解く

## 承認前に人が決めること

- 切り出す sh の名前（例: `ccnavi-carry.sh`）
- 切り出す sh をエージェントにも打たせるか
  - 通す: 承認そのものではなく運ぶだけなので合意は作れない。ボードで承認したあとの取りこぼしを
    エージェントが拾える。代償はエージェントが push まで行うこと
  - 止める: `ccnavi-approve.sh` と同じく組み込みの deny に入れる。運ぶのは常に人の手になる
