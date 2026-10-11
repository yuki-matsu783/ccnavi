---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-01-04
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 1
title: "#205 期限(REQ-CMN-08)の実測"
rationale: |
  期限はルールを 1 つ照合するたびに見ているだけで、守れるかを測るものが無い。測る道具とテストを足す。判定は変えない。
human_review:
  required: true
  reason: 実測の結果によっては、期限を守れない入力が見つかり、別の課題(判定の変更を伴う)になりうるため
allow:
  - match: Write|Edit
    glob: "tests/core/*"
  - match: Write|Edit
    glob: "tests/guard/*"
started_at: "2026-10-12T03:12:42+0900"
completed_at: "2026-10-12T03:46:52+0900"
base_sha: "8548fc14bfe6a07a792443d35f8c15550610d23f"
---
## 調べて分かったこと

- 期限の確認は `judge.py` のルール照合のループの中(`time.monotonic() > deadline`)だけ。1 つのルールの照合の途中、
  および照合の前の読み(`shellread`)は中断できないので、1 回の照合や読みが期限より長いと、超えた時間はそのまま延びる。
- 期限切れの経路(終了コード 2 / `dry-run` は 0、`audit.REASON_DEADLINE_EXCEEDED`、標準エラーの文面 `reasons.deadline_exceeded`)を直接確かめるテストが無い。
- 設計は既に重い入力の目安を持つ(`docs/design/limits.md`: 置換 8 万個・949KB で 3.6 秒、`appendix.md` の `DENY_REDIRECT_LIMIT`: 400KB で約 1.8 秒)が、再現できる測定は無い。

## 方針

1. 決定的なテスト(`tests/guard/test_deadline.py`、新規): 時計と期限を差し替えて、期限切れの 2 つのモード(enable: 終了コード 2、dry-run: 0)、記録の `reason`、標準エラーの文面、
   「期限に達した回は allow を返さない」を確かめる。実時間を待たない。
2. 実測の道具(`tests/core/bench_deadline.py`、新規): `bench_not_root.py` と同じ流儀で、名前を `test_` で始めず `unittest discover` に拾わせない。
   重い入力(長いコマンド、置換の多いコマンド、`>` の多い入力、400KB の尾、巨大な `file_path`)を実際のルールと組み込みで判定し、かかった時間と期限(3 秒)との比を出す。合否は付けない(機械で変わるため)。
3. 実時間のテストは、期限の 1/3 程度に収まる代表入力を 1〜2 件だけ緩い上限で置く(CI の遅い機械で揺れないこと)。
4. 守れない入力が見つかったら、直さずに報告する。直し方(照合を中断できる形にする、読みに期限を渡す、など)は判定の挙動に触れるので、別の課題としてユーザに諮る。

結果は `docs/design/limits.md` の「巨大なコマンドを期限の中で読み切ること」の行に反映する(文書の子 03-01)。
