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

## ruff からの除外

`scripts/yomiyasu_lint.py` はこのリポジトリの ruff の設定に合わないので、
`pyproject.toml` の `[tool.ruff] exclude` で `.claude/skills/yomiyasu` を外している。

## ローカルの変更

- 禁止語に「旧い」とその活用形（「旧く」「旧かっ」「旧けれ」）を足した（「古い」に統一する）。`scripts/yomiyasu_lint.py` の `SLOP_WORDS` と `references/slop-catalog.md` の表。upstream から差し替えるときは足し直す

## 既知の注意

- `scripts/yomiyasu_lint.py` の `NEGATIVE_PARALLELISM_PATTERN`（`([^。、]+)ではなく、?([^。、]+)`）は入力長の 2 乗で遅くなる。
  句読点の無い 2 万字の行で約 2.8 秒かかる。長い 1 行の入力は渡さない
- SKILL.md の `python3 <スキル配置ディレクトリ>/scripts/yomiyasu_lint.py` の `<スキル配置ディレクトリ>` は、
  このリポジトリでは `.claude/skills/yomiyasu`。python3 が無い環境（Windows など）では
  `uv run --python 3.12 python .claude/skills/yomiyasu/scripts/yomiyasu_lint.py <対象ファイル>` で回す（`.claude/hooks/lint-py.sh` と同じ版の固定）
- ruff にこのディレクトリのファイルを直接渡すと `exclude` が効かない。`--force-exclude` を付ける。`ruff check .` なら効く
- SKILL.md の Step 0（編集対象を `wip/handoff/` に cp する）は、保護対象のファイルを直すときの手順。
  保護対象ではない通常のファイル（`src/`・`tests/`・`docs/`・`README.md` など）を、ユーザが直接直すよう
  頼んだときは省いて、そのファイルを直接直す。直したあとに元との差分（`ccnavi-git.sh diff`）で意味が
  変わっていないかを見る点は同じ。vendored で SKILL.md を書き換えないので、ここに書く
- Python のコメント・docstring・テスト名を直すときは、行の長さが表示幅 100（ruff の E501。全角は 2、
  半角は 1 と数える）を超えないかを先に見る。言い換えると長くなりやすく、直したあとの `ruff check` で止まる。
  SKILL.md の Step 3 のリンターは日本語の文体を見るもので、行の長さは見ない
- 頭に frontmatter の `type` を足していない。vendored で中身を書き換えないため（`.ccnavi/docs/frontmatter.md` の「対象外」）。
  使う範囲の決まり（明示的に頼まれたときだけ、エージェント向けの文書には当てない）は
  [`.ccnavi/docs/skill-review.md`](../../../.ccnavi/docs/skill-review.md) の「どこに足すか」にある
