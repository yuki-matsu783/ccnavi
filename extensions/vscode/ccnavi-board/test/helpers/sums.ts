/**
 * 足し算（`--explain --json` の `sums[]`）の見本。実行ファイルの出力の形（tests/config/test_config_absent.py が見る形）で、
 * ワークスペースの足し算（共通 + 自身）とプロジェクト lib の足し算（共通 + lib）を持つ。
 */
import { parseBoardJson, type BoardJson } from "../../src/core/model.js";
import { fixture } from "./fixture.js";

const RULE_COMMON = { id: "guard-approved", section: "deny", source: "common", match: "Write|Edit", kind: "glob", written: ".ccnavi/approved/**", pattern: "^\\.ccnavi/approved/.*$", message: "承認済みは書かない" };
const RULE_LIB = { id: "lib:no-tmp", section: "deny", source: "lib", match: "Write", kind: "regex", written: "tmp/.*", pattern: "tmp/.*", message: "tmp には書かない" };
const RULE_ASK = { id: "lib:deps", section: "ask", source: "lib", match: "Edit", kind: "glob", written: "pyproject.toml", pattern: "^pyproject\\.toml$", message: "" };

function sum(name: string, rules: unknown[], extra: Record<string, unknown> = {}): Record<string, unknown> {
  const of = (section: string) => rules.filter((r) => (r as { section: string }).section === section);
  return {
    name,
    layers: ["common", name],
    rules: { path: name === "self" ? ".ccnavi/config/rules.yml" : `projects/${name}/.ccnavi/config/rules.yml`, unreadable: "", missing: false, deny: of("deny"), ask: of("ask"), allow: of("allow") },
    risk: {
      levels: { medium: 20, high: 40, critical: name === "self" ? 70 : 50 },
      factors:
        name === "self"
          ? [{ id: "big-diff", source: "common", kind: "lines_over", value: 300, points: 25, message: "行数が多い" }]
          : [
              { id: "big-diff", source: "common", kind: "lines_over", value: 300, points: 25, message: "行数が多い" },
              { id: "schema", source: "lib", kind: "glob", value: "db/**", points: 30, message: "" },
            ],
      fallback: "",
      problems: [],
    },
    phases: { path: "", unreadable: "", order: "sequential", types: [] },
    ...extra,
  };
}

/** `sums` を持つボード。ワークスペースの足し算と lib の足し算の 2 件 */
export function sumsBoard(): BoardJson {
  const parsed = parseBoardJson(JSON.stringify({ ...JSON.parse(JSON.stringify(fixture())), sums: [sum("self", [RULE_COMMON]), sum("lib", [RULE_COMMON, RULE_LIB, RULE_ASK])] }));
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.board;
}
