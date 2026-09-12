---
version: 1
ticket: approve-popup
title: チケットの承認を拡張のポップアップで行い、承認の事実を Claude Code に伝える
rationale: |
  いまの承認は、拡張のボタンが統合ターミナルに `ccnavi --approve` を送り、人が
  ターミナルで y/N を押す形。承認したことをエージェントに伝えるのも人がチャットで打つ。
  これを、拡張の中のポップアップで承認内容を見て承認し、承認の事実が hook 経由で
  モデルに届き、拡張が「後工程を進める」文を Claude Code に渡せる形にする。

  変えるのは 4 か所。
  1. 実行ファイル: `--approve --preview --json`（束の本文・識別子・対象外の提案と理由・
     読めない提案を返し、写しは置かない）と `--approve --yes --tickets <識別子,…> --json`
     （端末を求めない。いまの束が渡された一覧と違えば承認せず exit 1。承認したら
     Claude Code に渡す文 `prompt` を返す）を足す。組み込み PreToolUse に、Bash で
     `--approve` と `--yes` が同時に付く形の deny を足す（dry-run で止まらないのは許容）。
     hook は、セッションの最初の hook 時点より後に置かれた写しを、UserPromptSubmit と
     PreToolUse の additionalContext でセッションごとに 1 度だけ伝える。文は `--yes` の
     `prompt` と同じ関数から出す。
  2. 拡張: 承認はボードのボタンだけにし、パレットの `ccnaviBoard.approve` は消す。
     ボード内のオーバーレイに preview の内容を出し「承認する / やめる」。承認すると
     子プロセスで `--yes` を打つ。ターミナルに `--approve` を送る経路は消す。承認後の
     通知に「コピー」「新しいセッションで開く」（`vscode://anthropic.claude-code/open?prompt=`）
     の 2 ボタンを出し、押すまで何もしない。accept / wrapup は触らない。
  3. 設計書 §24.10 の「拡張の子プロセスを壁の外に置く」を、壁が「tty」から
     「組み込みの deny」に移ったことに合わせて書き換える。§17 も同じ。
  4. README（実行ファイルと拡張の両方）と HANDOVER。

  端末の壁（stdin.isatty）は §17.6 が言うとおり安い証拠で、hook を経ない主体には
  元から効かない。`--yes` はその壁を持たない代わりに、エージェントの経路を組み込みの
  deny で塞ぐ。走っている Claude Code のセッションにプロンプトを送信する公開 API は
  無い（調査済み）ので、「送信まで自動」にはせず、承認の事実を hook で必ず届けたうえで
  人が Enter を 1 回押す形に着地させる。
human_review:
  required: true
  reason: 人の合意を出す経路の壁の形が変わる。実行ファイル・拡張・hook・設計書にまたがる
plan:
- design
- acceptance
- implement
- docs
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: build.py
- match: Write|Edit
  glob: pyproject.toml
- match: Write|Edit
  glob: vscode-extension/*
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: HANDOVER.md
- match: Write|Edit
  glob: docs/*
started_at: ''
completed_at: ''
base_sha: ''
---

# 承認をポップアップにし、承認の事実を Claude Code に伝える

## 決めたこと

| # | 決定 |
|---|---|
| 1 | 組み込み deny は Bash で `--approve` と `--yes` が同時に付く形だけ。素の `--approve` は tty の壁のまま、`--preview` はエージェントが見てよい |
| 2 | `--yes` は見せた識別子の一覧を受け取り、いまの束と違えば承認しない |
| 3 | 承認画面はボードの Webview のオーバーレイ。パレットの承認コマンドは消す |
| 4 | ターミナルに `--approve` を送る経路は消す。フォールバックは持たない |
| 5 | accept / wrapup は今回触らない（sh の結果を JSON で返す契約が別に要る） |
| 6 | hook が伝えるのは UserPromptSubmit と PreToolUse。セッションごとに 1 度だけ |
| 7 | 承認後の通知は「コピー」「新しいセッションで開く」の 2 ボタン。押すまで何もしない |
| 8 | Claude Code に渡す文は実行ファイルが持つ（`--yes` の `prompt` と hook の文が同じ関数） |
| 9 | 拡張を触るために phases.yml の `implement` の scope に `vscode-extension/*` を足す（人が置く） |
| 10 | 「新しい承認」の起点はセッションの最初の hook 時点。それ以前の写しは伝えない |
| 11 | オーバーレイには本文のほか、対象外の提案と理由・読めない提案も載せる |

## 確かめること

- Bash の `ccnavi --approve --yes ...` が組み込みで deny になり、`--approve --preview --json` は通ること
- `--yes --tickets` に古い一覧を渡すと写しが置かれず exit 1 になること
- 承認の直後の UserPromptSubmit / PreToolUse で additionalContext に承認の文が 1 度だけ載ること。別セッションにも 1 度ずつ載ること
- セッション開始時点で既にあった写しは伝えないこと
- 拡張の core のテストが、preview の JSON からオーバーレイの HTML を組み、`--yes` の引数行を組めること

## 進め方

1. 設計: JSON の形（preview / yes）、hook の「伝えた」の控えの形、組み込み deny の regex、
   オーバーレイの操作の流れを `wip/design/approve-popup.md` に書く
2. 受入テスト作成: 上の「確かめること」を tests/ と vscode-extension/ccnavi-board/test/ に書く
3. 実装とテスト: approval.py / cli.py / builtin.py / ctxfile.py と、拡張の board-panel.ts /
   ccnavi.ts / commands.ts / core/render.ts / core/*model.ts。パレットのコマンドとターミナル経路を消す
4. 文書: README「ボードの JSON」の隣に「承認の JSON」、拡張の README、ccnavi.md §17 / §24.10、HANDOVER
