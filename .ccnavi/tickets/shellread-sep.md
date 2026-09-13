---
version: 1
ticket: shellread-sep
title: shellread の印を「コマンドの区切り」と「語の中の切れ目」に分ける
rationale: 'shellread はシェルなら一致がまたげない場所すべてに同じ印 `\x00` を置く。置く先は

  3 つで、コマンドとコマンドの間、引用が 2 語を 1 語につないだ空白、語の中に入った

  演算子の文字の両側。rules.yml の regex からはこの 3 つが区別できない。


  そのため allow の prefer-read-grep が「後ろにコマンドが続かない」を `[^\x00]*$` で

  書いた結果、`grep -n "git push" README.md` のように引用の中に空白を含む grep / cat

  は allow に当たらず、権限モードに落ちる。見本ファイルには元からこの形を allow に

  置いた行があり、書いた人は allow になると思っていた。プロジェクト自身の期待と

  判定が食い違っている。


  直し方は、コマンドの区切りは `\x00` のまま、語の中の切れ目（引用の中の空白と

  語の中の演算子）を別の 1 文字にすること。`(^|\x00)` で「コマンドの位置」を求める

  既存の regex（raw-git、find-writes、prefer-webfetch、組み込みの承認経路のルール）は

  どれも区切りの意味で使っているので影響しない見込みだが、1 本ずつ確かめる。

  shellread の出力の形は README「Bash のコマンドは実行される部分だけを見る」に

  書かれている契約なので、文書も直す。


  見本（.claude/ccnavi/rule-samples.yml）の 3 行を allow に戻す作業は、フェーズの

  種類の範囲に無いので、このチケットの外で直接作業として行う。

  '
human_review:
  required: true
  reason: 判定の入力の形が変わる。既存の regex ルール全部に影響しうる
plan:
- type: design
  review: defer
- type: acceptance
  review: defer
- implement
- docs
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: HANDOVER.md
started_at: ''
completed_at: ''
base_sha: ''
ccnavi_approved:
  approved_at: 2026-09-12T18:15:08+0900
  source_tree: shellread-sep
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\shellread-sep\wip\tickets\todo\shellread-sep.md
  revised_at: 2026-09-13T13:10:34+0900
  feedback_at: 2026-09-13T13:10:34+0900
feedback:
- design-feedback
---

# shellread の印を分ける

## いま

| 書いたコマンド | shellread が作る文字列 |
|---|---|
| `cd /repo && ls` | `cd /repo\x00ls` |
| `grep -n "git push" README.md` | `grep -n git\x00push README.md` |
| `grep -n "a>b" f` | `grep -n a\x00>\x00b f` |

3 行とも同じ `\x00` なので、`[^\x00]*$`（後ろにコマンドが続かない）が 2 行目と 3 行目にも
外れる。

## 変えたあと

| 書いたコマンド | shellread が作る文字列 |
|---|---|
| `cd /repo && ls` | `cd /repo\x00ls`（変わらない） |
| `grep -n "git push" README.md` | `grep -n git\x01push README.md` |
| `grep -n "a>b" f` | `grep -n a\x01>\x01b f` |

`\x01` は仮。実際の文字は設計フェーズで決める（コマンドラインが運べない文字で、`\s` に
当たらず、`\w` にも当たらないもの）。

## 確かめること

- `(^|\x00)` を使う既存の regex（rules.yml と phase.py の組み込みルール）が、変更の前後で同じ
  見本に同じ判定を返すこと
- `grep -n "git push" f` / `grep -n "rm -rf" f` / `grep -n "<<EOF" f` が prefer-read-grep に当たり、
  かつ raw-git / recursive-delete / heredoc に当たらないこと
- 引用が演算子だけの 1 語（`grep -n ">" f`）の扱いが今までどおりであること（README の
  「許容する誤検知」）
- 入力に `\x01` が混じっていたら `\x00` と同じく取り除くこと（騙りの防止）

## 進め方

1. 設計（延期。次のレビューと一緒に見る）: 印の文字と置く先を決め、影響する regex の一覧を
   `wip/design/shellread-sep.md` に書く
2. 受入テスト作成（延期）: 上の「確かめること」を tests/ に書く
3. 実装とテスト: shellread.py と、印を知っている呼び手（judge.py など）を直す。1 と 2 をここで
   一緒にレビュー
4. 文書: README「Bash のコマンドは実行される部分だけを見る」、ccnavi.md の該当節、HANDOVER.md
