# UPSTREAM

- 出所: https://github.com/nanaism/yomiyasu
- sha: `7b61b2f0283265ce1986d76c67929622a775844d`（v1.0.1、main）
- vendored。中身は書き換えない。更新するときは upstream の `skills/yomiyasu/` から差し替える

## 取り込んだもの

upstream の `skills/yomiyasu/` から次をそのまま写した。

- SKILL.md
- references/slop-catalog.md
- references/gemini-syntax.md
- references/domains/tech.md, business.md, essay.md
- scripts/yomiyasu_lint.py

## 省いたもの

- assets/algo-artis.png（README 用のバナー。SKILL.md から参照されない）
- .claude-plugin/（プラグイン配布用の plugin.json・marketplace.json）
- README.md, articles/, evals/, tests/corpus/
- scripts/benchmark_corpus.py, build_corpus.py, setup_corpus_static.py（コーパス作成とベンチマーク用）
- .gitignore

## ローカルの変更

- 禁止語に「旧い」とその活用形（「旧く」「旧かっ」「旧けれ」）を足した（「古い」に統一する）。`scripts/yomiyasu_lint.py` の `SLOP_WORDS` と `references/slop-catalog.md` の表。upstream から差し替えるときは足し直す
- 禁止語に「関門」を足した（「ゲート」に統一する。初出は「操作を止める条件（ゲート）」）。`scripts/yomiyasu_lint.py` の `SLOP_WORDS` と `references/slop-catalog.md` の表。upstream から差し替えるときは足し直す
- SKILL.md から、和欧文間の半角空白を除く規則を消した（description・「装飾記号の完全排除」・Step 2 の 5）。空白の入れ方はこのスキルでは決めず、対象の文書や画面の既存の書き方に合わせる。upstream から差し替えるときは消し直す（リンターには空白の検査は無い）

## 既知の注意

- `scripts/yomiyasu_lint.py` の `NEGATIVE_PARALLELISM_PATTERN`（`([^。、]+)ではなく、?([^。、]+)`）は入力長の 2 乗で遅くなる。
  句読点の無い 2 万字の行で約 2.8 秒かかる。長い 1 行の入力は渡さない
- SKILL.md の `python3 <スキル配置ディレクトリ>/scripts/yomiyasu_lint.py` の `<スキル配置ディレクトリ>` は、
  このリポジトリでは `.claude/skills/yomiyasu`。標準ライブラリだけで動くので、`python3` があればこのスキルだけで回る
- SKILL.md の Step 0（編集対象を `wip/handoff/` に cp する）は、保護対象のファイルを直すときの手順。
  保護対象ではない通常のファイル（`src/`・`tests/`・`docs/`・`README.md` など）を、ユーザが直接直すよう
  頼んだときは省いて、そのファイルを直接直す。直したあとに元との差分（`ccnavi-git.sh diff`）で意味が
  変わっていないかを見る点は同じ。vendored で SKILL.md を書き換えないので、ここに書く
- 頭に frontmatter の `type` を足していない。vendored で中身を書き換えないため（`.ccnavi/docs/frontmatter.md` の「対象外」）。
  使う範囲の決まり（明示的に頼まれたときだけ、エージェント向けの文書には当てない）は
  [`.ccnavi/docs/skill-review.md`](../../../.ccnavi/docs/skill-review.md) の「どこに足すか」にある
