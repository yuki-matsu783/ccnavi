---
version: 1
ticket: config-union-07
parent: config-union
phase: 3
predecessors: [config-union-05]
title: 敵対的レビューが見つけた守りの穴を塞ぐ
rationale: |
  設計への敵対的レビューが見つけた穴のうち、判定を厳しくする側の 5 件を塞ぐ。どれも
  `.ccnavi/` の組み込み deny が乗る土台の話で、塞がないと「`*/.ccnavi/*` で守っている」が
  実際には守れていない状態になる。4 件は既存の穴でもあり、`.claude/` の側も同時に直る。
  1 件（層が無いことを lint が言うか）は設計の「言わない」を変えるので、着手前に人の判断が要る。
  PR のレビュー記録: https://github.com/yuki-matsu783/ccnavi/pull/2#issuecomment-5645975126
human_review:
  required: true
  reason: 共有の守り（ルールの照合、シェル書き込みのガード）を変えるため
allow:
  - match: Write|Edit
    glob: "ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
started_at: ""
completed_at: ""
base_sha: ""
---

# 敵対的レビューが見つけた守りの穴を塞ぐ

## A-2 大文字小文字（既存の穴）

`ccnavi/rules.py:325` は glob を翻訳した正規表現をフラグ無しでコンパイルする。一方
`ccnavi/risk.py:274`、`ccnavi/phasetypes.py:239`、`ccnavi/ticket.py:854` は
`tree.CASE_INSENSITIVE` を見て `re.IGNORECASE` を付ける。rules だけが区別する側にいる。

`.ccnavi/` はまだどこにも無いので、最初に `.Ccnavi/` で作れば `*/.ccnavi/*` に当たらない。
実在した後は正しい綴りで渡しても `os.path.realpath` が実ディスクの綴りへ補正するため、
以後も当たらない（実行して確認済み）。

直す先: `rules.py` の `_build` で `risk.py` と同じ `flags` を使う。`regex` で書いたルールは
今までどおり区別する（書いた人が意図を持てるので。README の「`regex` で書いた範囲だけは
区別を残す」と同じ理屈）。

**代償**: 既存のワークスペースで、`glob` で書いた deny / ask / allow の当たり方が広がる。
広がる側なので deny と ask は安全に倒れるが、`allow` も広がる。`--test-samples` を回して
見本の判定が変わらないことを確かめる。

## A-3 区切りが続かない綴り（既存の穴）

`ccnavi/selfguard.py:183` の `_PLACES` は `\.claude[\\/](...)` の形で、`.claude` の直後に
区切りが続く綴りにしか当たらない。`rm -rf .claude`、`mv .ccnavi .ccnavi.bak` は素通りする
（正規表現を組んで確認済み）。`.claude` は中身のあるディレクトリなので上書きしにくいが、
`.ccnavi/` は「無くてよい層」なので、丸ごと消して作り直す道が素直に通る。

直す先: `_PLACES` / `_COPY_PLACES` と `project_home_clause` を
`\.ccnavi(?:[\\/]|$)` の形にする。`.claude` の側も同じ。

## A-4 生の `id` のコロン

共通層に `id: "lib:custom"` と書けば、プロジェクト `lib` の定義に見える。`rules.py` にも
`ruleload.py` にも `lint.py` にも、生の `id` のコロンを弾く検証が無い（grep 済み）。設計は
「id だけで直しに行くファイルが分かる」を重複排除と記録と `--explain` の前提にしている。

直す先: `rules.py` / `phasetypes.py` / `risk.py` の `parse` で、生の `id` にコロンを含むものを
error にする。`--lint` が名指しする。

## A-5 `self` の予約

`projects/Self/` は `self:id`（ワークスペース自身の層）と紛らわしい。権限は広がらないが、
記録を読む人が取り違える。`os.path.normcase` か `casefold` で比べる。

## A-6 risk のスクリプトの書き換え

`.ccnavi/scripts/` は中核に入れず deny と復元に任せる設計だが、その deny が A-2 と A-3 で
回避できた。`run_script` は返ってきた `points` をそのまま信じる（`ccnavi/risk.py:500`）。
「失敗は重い側に倒れる」は「測れなかった」場合の話で、正常応答として 0 を返す改ざんには効かない。

A-2 と A-3 を塞げば、書き換えの道は Write / Edit とシェルの両方で止まる。**この項目は
A-2 / A-3 を直したうえで、見本を回して実際に止まることを確かめるところまで**。中核に
入れる（ハッシュ突き合わせ）形は今回入れない。

## A-1 層が無いことを lint が言うか（**着手前に人の判断が要る**）

設計は「層のファイルが無い = その層は空。`--lint` は言わない」としている（無いのが正常な
状態だから）。この結果、プロジェクトの層を消す・`.ccnavi/config/` が入る前のコミットへ
`checkout` する、のどちらでも、そのプロジェクトの deny が痕跡なく消える。ラッパーは
`--force` などだけを弾き、素のブランチ切り替えは通す（`.claude/scripts/ccnavi-git.sh:325-347`）。
「壊す」と error、「消す」と無言、という非対称が効いている。

案: 「承認済みチケットの `project:` が指すプロジェクトなのに、その層の 3 本が 1 本も無い」を
`--lint` の info か warn に足す。得るもの: 消した・切り替えたことが目に付く。失うもの:
層を持たないプロジェクト（書き込めない他人の repo）で常に鳴る warn になりうるので、
「承認済みチケットが指すもの」に絞る必要がある。判定は変わらない（言うだけ）。

**この項目だけは、設計の明文を変える。人が決めるまで着手しない。**

## 成果物

- 上の 5 件（A-1 を除く）の実装と、それぞれの受入テスト
- `.claude/ccnavi/rule-samples.yml` の見本を回して、既存の判定が変わらないことの確認結果を報告に添える
- 既存テスト全体が緑
