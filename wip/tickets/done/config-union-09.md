---
version: 1
ticket: config-union-09
parent: config-union
phase: 3
predecessors: [config-union-07]
title: 層の名前とプロジェクト名の衝突を塞ぐ
rationale: |
  実装への敵対的レビューが、層の名札（`common` / `self`）とプロジェクト名の衝突を 2 件見つけた。
  どちらも「予約が片側にしか掛かっていない」ことから来る。`projects/self/` は
  `ruleload.layers` が数えないのに `ruleload.layer_for` が `LAYER_SELF` と同じ名前として
  ワークスペース自身の層に当ててしまい、**そのプロジェクトへの Write / Edit がプロジェクト自身の
  deny を一度も読まずに、ワークスペースの層のルールで判定される**（実測で確認）。
  `projects/common/` は `selfguard._layer_key` の文字列比較で共通層と同じ控えの key になり、
  重複の排除で**その層の 3 本が控えと復元の対象から丸ごと落ちる**。
  チケット 07 の A-5 は「権限は広がらない」と書いたが、前者は判定そのものがすり替わるので誤り。
  予約を 1 か所で持ち、名札の文字列比較に頼らない形に直す。
human_review:
  required: true
  reason: 判定に使う層の選び方と自己保護の対象を変えるため
allow:
  - match: Write|Edit
    glob: "ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
started_at: "2026-09-13T05:48:26+0900"
completed_at: "2026-09-13T06:26:12+0900"
base_sha: "b77eb77b0f657bd33b9940b2d9b8fb20d18e2b8b"
---

# 層の名前とプロジェクト名の衝突を塞ぐ

## 穴 1（高）: `projects/self/` への書き込みがワークスペースの層で判定される

`ccnavi/ruleload.py` の `layers` は `is_self_name` で `projects/self/` を一覧から外す。一方
`layer_for` は `name = target.project or LAYER_SELF` としてから名前で引くので、`target.project`
が `"self"` のとき `LAYER_SELF`（`"self"`）と一致し、**ワークスペース自身の層**が返る。

結果、`projects/self/secret.txt` への Write は、そのプロジェクトの `.ccnavi/config/rules.yml` の
`deny` を一度も読まずに、ワークスペースの層のルールで判定される。ワークスペースの層に広い
`allow` があれば通る。Bash の和からは最初から外れている（そちらは既存テストが確認済み）。

`projects/Self/`（綴り違い）は `layers` から外れ、`layer_for` の名前引きにも当たらないので
「層無し」になる。安全側だが、プロジェクトのルールが黙って効かない点は同じ。

## 穴 2（中）: `projects/common/` の層が自己保護の対象から落ちる

`ccnavi/selfguard.py` の `_layer_key` は `layer == settings.LAYER_COMMON` の文字列比較で
「共通層なら kind そのまま」を決める。`projects/common/` があると、その層の key が
`rules` / `phases` / `risk` になり、共通層の key と完全に一致する。`_places` の重複の排除は
先に積んだ共通層を残すので、そのプロジェクトの 3 本は控えと復元の対象から丸ごと落ちる。

Write / Edit とシェルの拒否は glob と regex で当てるので効き続ける。落ちるのは「deny を
回避されても戻す」側だけ。

## 直し方

**予約を 1 か所で持つ。** `settings` に予約名の集合（`LAYER_COMMON` と `LAYER_SELF`）と
`is_reserved_layer_name(name)`（`casefold` で比べる）を置き、`ruleload.is_self_name` を
そこへ寄せる。次の 4 か所がそれを使う。

1. `ruleload.layers`: 予約名のプロジェクトを数えない（今は `self` だけ）
2. `ruleload.layer_for`: **行き先が予約名のプロジェクトなら「層無し」を返す。**
   ワークスペース自身の層へ落ちてはいけない。ここが穴 1 の本体
3. `ccnavi/lint.py`: 予約名のプロジェクトを error で名指しする（今は `self` だけ）
4. `ccnavi/approval.py` の `project_problems`: 予約名を `known` から外し、`project: self` の
   チケットを承認しない。今は `tree.projects` の素の一覧を使うので通ってしまう

**名札の文字列比較をやめる。** `selfguard._layer_key` は「その層がプロジェクト名前空間から
来たか」を引数で受ける形にし、`layer == LAYER_COMMON` の比較に依存させない。
`ruleload.layer_files` が種別を添えて渡す。

**`phase.layer_types` と `risk.layer_definition`** も予約名を除外する（今は `self` でも
除外していない。フェーズ 3 のレビューで別 issue 候補として挙げたもの。ここで一緒に直す）。

## 受入テスト

`tests/test_config_union_holes.py` に足す。黒箱（`run_ccnavi` 経由）。

- `projects/self/` があるとき、そのプロジェクトへの Write が**ワークスペースの層の allow で
  通らない**こと（層無しとして扱われ、共通層だけで判定される）
- `projects/Self/` でも同じであること
- `projects/self/` を指す `project: self` のチケットが `--approve` で通らないこと
- `projects/common/` の層の 3 本が控えと復元の対象に入ること（key が共通層と衝突しない）
- `projects/common/` と `projects/self/` の両方が `--lint` の error で名指しされること
- 予約名でないプロジェクト（`lib`）は今までどおり効くこと（対照）

## 成果物

- 上の直しと受入テスト
- 既存テスト全体が緑
- チケット 07 の A-5 の記述（「権限は広がらない」）が誤りだったことを、この提案の rationale に
  残してある。設計書への反映はフェーズ 4（文書）で行う
