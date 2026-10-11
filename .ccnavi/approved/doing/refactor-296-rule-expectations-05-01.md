---
version: 1
ticket: refactor-296-rule-expectations-05-01
parent: refactor-296-rule-expectations
phase: 5
title: commit スキルのテストグループ表に、拡張の fixtures を読む Python のテストを足す
rationale: |
  フィードバック計画の項 5（skill-improve）。フェーズ 1 で src の `--json` のキーと CLI の旗を改めたところ、
  拡張の fixtures を読む Python のテスト 2 本が落ち、フェーズ 2 で fixtures を直して通った。
  拡張を別フェーズに分ける計画では、前のフェーズでこの 2 本が落ちると先に分かっていれば、落ちるのを見込んで分けられる。
  `test-groups.md` に規則を 1 つ足し、次の同種の改名で回すグループを取り違えないようにする。
human_review:
  required: true
  reason: エージェントの手順（回すテストのグループの決め方）を変えるため
allow:
  - match: Write|Edit
    glob: ".claude/skills/commit/references/*"
  - match: Write|Edit
    glob: "docs/wiki/*"
---
## 目的

`.claude/skills/commit/references/test-groups.md` に、次の規則を足す。候補は
`wip/scratchpad/skill-candidates.md` の 1 件だけ。直すスキルもこの 1 枚だけ（`skill-wiki.md`: 提案は 1 つのスキルを対象にする）。

> `src/` の `--json` 出力のキーや CLI の旗を変えると、`extensions/vscode/ccnavi-board/test/fixtures/` を読む Python のテストが落ちる。回すグループは `core` と `guard` の両方。

## 既存の規則との突き合わせ（`test-groups.md` を読んだ結果）

同じ規則は無い。近いものが 2 つあり、足さずに強める。

- 59 行目 `extensions/vscode/` の行: 「`ticket`（`core` の `test_test_json` も例を読む）」。`core` の 1 本は書いてあるが、
  `guard` の `tests/guard/test_suggest.py` が無く、`src/` を変えたときに起きることだと分からない。
  回すグループは上の「`src/ccnavi/**/*.py` は `guard` `config` `ticket`」と `core` の常時で、`guard` は足りているが、
  理由を知らないと「拡張側の fixtures を変えたとき」に `guard` を落とす
- 76 行目: 「`e2e` は足さない」の 1 段落。同じ形の「足す・足さない」の補足がここにあるので、置き場は表の直後に補足 1 段落を足すのが自然

どちらも「規則 + なぜ（1 句）」の形に直す。日付・番号・チケット名は書かない。

## 差分の下書き

### 1. 59 行目を直す（表の行）

```diff
-| `extensions/vscode/` | `ticket`（`core` の `test_test_json` も例を読む） |
+| `extensions/vscode/` | `ticket`（`core` の `test_test_json` と `guard` の `test_suggest` も `test/fixtures/` を読む） |
```

### 2. 76 行目の段落の前に、補足を 1 段落足す

```diff
+`src/` の `--json` 出力のキーや CLI の旗を変えるときは、`core` と `guard` の両方を回す。`extensions/vscode/ccnavi-board/test/fixtures/` を
+Python のテスト（`tests/core/test_test_json.py`・`tests/guard/test_suggest.py`）も読むので、fixtures の旧キーのままだと落ちる。
+拡張を別のフェーズに分けて計画するなら、前のフェーズでこの 2 本が落ちると分かったうえで分ける（直すのは fixtures を持つフェーズ）。
+
 `src/ccnavi/**/*.py` を変えたとき `e2e` は足さない（e2e は組み立て済みの実行ファイルを試す）。組み立て直したなら足す。
```

## 手順

1. `test-groups.md` を読み直す（会話中の引用で済ませない）。上の差分を当てる。行番号は動いていることがあるので、文を見て当てる
2. 保護対象で Edit が拒否されたら、`CLAUDE.md` の手順どおり `wip/scratchpad/` に `cp` して編集し、`mv` のフルパスをユーザに渡す
3. wiki に反映する。下の「wiki」の差分を入れる
4. 検査: `ccnavi --lint` の error が 0。文書だけなのでテストは回さない（`test-groups.md` の表: 「そのほかのドキュメントだけ」は回さない）。
   frontmatter を触らないので `docs/claude/frontmatter.md` の検査は不要

## wiki（`docs/wiki/`）

`docs/wiki/` は、この時点でまだ無い（`index.md`・`skill-impact.md` とも読めなかった）。最初の振り返りなので、`docs/claude/skill-wiki.md`
の置き場と中身の決まりに従って新しく作る。`type` は `skill-wiki`（`docs/claude/frontmatter.md`）。

| ファイル | 書くこと |
|---|---|
| `docs/wiki/index.md` | 1 行: 外に見える名前（JSON のキー・CLI の旗）を改めると、拡張の fixtures を読む別言語側のテストが落ちる。原因は fixtures が旧名のままのため。直し方は、回すグループを `core` と `guard` にし、fixtures を持つフェーズで直す |
| `docs/wiki/patterns/external-name-fixtures.md` | 10〜30 行。説明、原因、操作の例（どの 2 本が落ちたか）、回避策（フェーズを分けるなら前のフェーズで落ちる本数を見込む）。成功したやり方として、フェーズ 2 で fixtures を直して通ったことも書く |
| `docs/wiki/skill-impact.md` | この提案: 対象は `commit/references/test-groups.md`、要点、上の差分、採否（承認待ち。採否はユーザが決める。エージェントは推測で書かない） |
| `docs/wiki/log.md` | 今回の振り返り: 拾ったもの（サブエージェントの報告と親の作業で同じ候補が出た）、足したパターン 1 件 |

- 却下された提案の記録は無い（wiki が無いため）。覆すべき理由も無い
- `test-groups.md` は `references/` 直下にあり、`common/` `project/` に分かれていない。`purpose.md` を置く先が決まらないので、
  `purpose.md` は今回は足さない（相談）。スキルの本文には wiki への参照を書かない

## 完了条件

- `test-groups.md` に上の 2 か所の差分が入り、同じ規則が 2 か所に重複していない
- `docs/wiki/` に上の 4 ファイルがある
- `ccnavi --lint` の error が 0
