---
version: 1
ticket: shellread-sep-05
parent: shellread-sep
phase: 5
predecessors:
- shellread-sep-04
title: 設計書の影響一覧に main から入った 2 か所を足す
rationale: '設計（wip/design/shellread-sep.md）§2「影響の洗い出し」は、main を取り込む前の

  コードで書いた。取り込んだ main には、`\x00` を使う式が 2 か所増えていた。

  phase.py の `_NOT_PREVIEW` と、selfguard.py の `_TERM` / `_END` である。

  3 番目（実装）でこの 2 か所に気づき、今の判定を保つ形に直して、そのままレビューで

  受け入れられた。ただ設計書には載っていないので、次に印を触る人が一覧を信じると

  同じ見落としをする。一覧と見本の表に足す。


  コードとテストと README / ccnavi.md は、3 番目と 4 番目で直し終えている。

  このフェーズで触るのは設計書だけ。

  '
human_review:
  required: true
  reason: フィードバック計画の作業。設計の記録を直す
allow:
- match: Write|Edit
  glob: wip/design/*
ccnavi_approved:
  approved_at: 2026-09-13T13:10:34+0900
  source_tree: shellread-sep
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\shellread-sep\wip\tickets\todo\shellread-sep-05.md
started_at: 2026-09-13T13:11:20+0900
base_sha: 6ddeb7145a6ef80f167d8310eaec7db3f380e12b
completed_at: 2026-09-13T13:14:59+0900
---

# 設計フィードバック: 影響一覧の漏れを埋める

## やること

`wip/design/shellread-sep.md` を次のように直す。

1. §2「影響の洗い出し」の `ccnavi/` の表に 2 行足す
   - `phase.py` `_NOT_PREVIEW = (?![^\x00;&|\r\n]*--preview\b)`
     - 区分: 語の中
     - 根拠: 分けただけだと `\x01` をまたぎ、`ccnavi --approve x "a --preview"` が deny から ask に落ちる（緩む方向）。`_NOT_A_WORD` を使って `\x01` もまたがない形にする
   - `selfguard.py` `_TERM` / `_END`（`[ \x00]` / `[\\/ \x00]`）
     - 区分: 語の中
     - 根拠: 分けただけだと `\x01` を語の終わりと数えず、`rm ".ccnavi x"`・`rm ".claude x"`・`mv ".ccnavi;x" y` が deny から ask に落ちる。指す名前は別名なので誤検知だが、判定は緩めない。`\x01` も数える
2. §2 の表の行番号を、main を取り込んだあとのコードに合わせて直す（`selfguard.py` の `_WRITE_VERBS`、`ticket.py` の `_place` など）
3. §3「判定が変わる見本」の「変わってはいけないもの」に次の 4 行を足す
   - `ccnavi --approve i0001 "a --preview"`: deny
   - `rm ".ccnavi x"`: deny
   - `rm ".claude x"`: deny
   - `mv ".ccnavi;x" y`: deny
4. 冒頭か §1 に、main を取り込んだあとで一覧を見直したことを 1 段落で書く（何が漏れていて、どこで拾ったか）

## 完了の条件

- §2 の表の `\x00` を使う場所が、`ccnavi/` の `\x00` の使用箇所と過不足なく対応すること（`git grep -n -F 'x00' -- ccnavi/` と突き合わせる）
- §3 に足した見本の判定が `tests/test_repo_rules.py` の期待と一致すること

## 範囲

`wip/design/shellread-sep.md` だけ。コード・テスト・README・ccnavi.md は変えない。
