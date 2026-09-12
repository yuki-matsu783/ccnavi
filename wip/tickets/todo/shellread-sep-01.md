---
version: 1
ticket: shellread-sep-01
parent: shellread-sep
phase: 1
title: 印の文字と置く先を決め、影響する regex を洗い出す
rationale: |
  shellread の印を 2 種類に分けるにあたり、実装の前に決めておくことが 3 つある。
  語の中の切れ目に使う文字、その文字を入力からどう取り除くか、`\x00` を使っている
  既存の regex（rules.yml、phase.py の組み込みルール、配り先の想定）が変更の前後で
  同じ判定を返すかの一覧。これを wip/design/shellread-sep.md に書く。
  レビューは 3 番目（実装）と一緒に見る。
human_review:
  required: false
  reason: 文書 1 本の作成。実装と一緒に 3 番目でレビューする（延期）
allow:
  - match: Write|Edit
    glob: "wip/design/*"
started_at: ""
completed_at: ""
base_sha: ""
---

# 設計: shellread の印を分ける

書くもの: `wip/design/shellread-sep.md`。

## 決めること

1. **語の中の切れ目に使う文字。** 条件は 3 つ。実際のコマンドラインが運べない（入力側が
   騙れない）、正規表現の `\s` に当たらない、`\w` に当たらない。候補は `\x01`。
   `shlex` がその文字をどう扱うかも確かめる（語の一部として残るか）
2. **入力からの除去。** いま `read()` は `\x00` を入力から取り除いている。新しい文字も同じく
   取り除く。取り除く場所と順序を決める
3. **置く先の対応表。** shellread.py 冒頭のコメントにある 3 つの置き先（コマンドの間、引用が
   つないだ空白、語の中の演算子の両側）を、どちらの文字にするかで表にする。
   引用が演算子だけの 1 語（`grep -n ">" f`）は演算子として扱う今の読みを変えない

## 洗い出すこと

`\x00` を使っている regex を全部挙げ、それぞれ「コマンドの区切り」の意味で使っているか、
「語の中の切れ目」も含めて使っているかを書く。見る場所は次の 4 つ。

- `.claude/ccnavi/rules.yml`（raw-git、find-writes、prefer-webfetch、prefer-read-grep、prefer-glob）
- `ccnavi/phase.py` の `_EXEMPT_COMMAND` `_FORBIDDEN_COMMAND` と `ticket_approval_rule` の式
- `ccnavi/judge.py` `ccnavi/post.py` など、`shellread.SEP` や `\x00` を直接見ているコード
- README「Bash のコマンドは実行される部分だけを見る」に書かれた契約の文

それぞれについて、変更後に判定が変わる見本があれば挙げる。変わらないなら「変わらない」と
根拠を書く。

## 受入テストに渡すこと

2 番目（受入テスト作成）がそのままテストに書ける形で、「この入力がこの文字列になる」の
対応表と、「この見本がこの判定になる」の一覧を、設計文書の末尾に置く。

## 範囲

書くのは `wip/design/` の下だけ。ソースと README は読むが触らない。
