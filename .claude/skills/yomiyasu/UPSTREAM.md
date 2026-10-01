# UPSTREAM

- 出所: https://github.com/nanaism/yomiyasu （MIT、LICENSE を同梱）
- sha: `7b61b2f0283265ce1986d76c67929622a775844d`（v1.0.0、main）
- vendored。中身は書き換えない。更新するときは upstream の `skills/yomiyasu/` から差し替える

## 取り込んだもの

upstream の `skills/yomiyasu/` から次をそのまま写した（LICENSE はリポジトリのルートから）。

- SKILL.md
- LICENSE
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
