/**
 * リスク管理画面の案内の見本（`webview/tours/risk.yml` の `sample:`）を、画面が描く形にする。
 *
 * 配点は `risks.yml` と同じ書き方（`levels:` と `factors:`。加点条件は `lines_over:` などのキー）で見本に書き、ここで
 * `RiskPage`（画面が描く形）にする。統合結果は実行ファイルの JSON と同じ形で書く。いずれも案内の間に描くだけで、保存にも判定にも使わない。
 * `risk-doc.ts` は YAML の解析器を持つので画面から読めない。同じ読み方をここで小さく持ち、そろっていることはテストが見る。
 */
import { KINDS, LEVEL_NAMES, type FactorForm, type FactorKind, type LevelName, type RiskForm, type RiskPage } from "./risk-view.js";
import { sumLabel, type RiskSum } from "./sums.js";

export interface RiskSample {
  /** 配点のファイルがあるときの画面（見本の配点） */
  readonly page: RiskPage;
  /** 配点のファイルが無いときの画面（組み込みの配点を読み取り専用で見せる） */
  readonly missing: RiskPage;
}

function object(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${at} がオブジェクトではない`);
  }
  return value as Record<string, unknown>;
}

/** 欄の文字。無いか null なら空（`risk-doc.ts` の `scalarText` と同じ） */
function str(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

/** include / exclude は glob のリスト。欄には 1 行に 1 つで出す（リストでない書き方は 1 つの値として出す） */
function globsText(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => str(item)).join("\n") : str(value);
}

function factorOf(raw: unknown, index: number): FactorForm {
  const map = object(raw, `factors[${index}]`);
  const kind: FactorKind = KINDS.find((key) => key in map) ?? "lines_over";
  return {
    origin: index,
    id: str(map.id),
    points: str(map.points),
    kind,
    value: str(map[kind]),
    max: str(map.max),
    include: globsText(map.include),
    exclude: globsText(map.exclude),
    message: str(map.message),
  };
}

/** `risks.yml` の書き方の配点（`levels` と `factors`）を、画面の `RiskForm` にする */
export function riskFormOf(raw: unknown, at = "risks"): RiskForm {
  const root = object(raw, at);
  const levels = { medium: "", high: "", critical: "" } as Record<LevelName, string>;
  if (root.levels !== undefined) {
    const given = object(root.levels, `${at}.levels`);
    for (const name of LEVEL_NAMES) {
      levels[name] = str(given[name]);
    }
  }
  const list = root.factors ?? [];
  if (!Array.isArray(list)) {
    throw new Error(`${at}.factors はリスト`);
  }
  return { levels, factors: list.map((item, index) => factorOf(item, index)) };
}

function sumOf(raw: unknown, index: number): RiskSum {
  const at = `sample.sums[${index}]`;
  const map = object(raw, at);
  const name = str(map.name);
  const levels = object(map.levels, `${at}.levels`);
  const layers = Array.isArray(map.layers) ? map.layers.map((layer) => str(layer)) : [];
  const factors = Array.isArray(map.factors) ? map.factors : [];
  return {
    name,
    label: sumLabel(name),
    layers,
    levels: {
      medium: typeof levels.medium === "number" ? levels.medium : null,
      high: typeof levels.high === "number" ? levels.high : null,
      critical: typeof levels.critical === "number" ? levels.critical : null,
    },
    factors: factors.map((item, i) => {
      const f = object(item, `${at}.factors[${i}]`);
      return {
        id: str(f.id),
        source: str(f.source),
        kind: str(f.kind),
        // 数でも文字列でも文字にして持つ（実行ファイルの JSON と同じ）
        value: str(f.value),
        points: typeof f.points === "number" ? f.points : null,
        message: str(f.message),
      };
    }),
    fallback: "",
    problems: [],
  };
}

export function parseRiskSample(raw: unknown): RiskSample {
  const root = object(raw, "sample");
  const path = str(root.path);
  const unlocked = { locked: false, reason: "", doing: [], turns: [] };
  const sums = Array.isArray(root.sums) ? root.sums.map((item, index) => sumOf(item, index)) : [];
  const page: RiskPage = {
    root: "",
    riskPath: path,
    exists: true,
    model: { version: 1, form: riskFormOf(root.risks, "sample.risks"), problems: [] },
    lock: unlocked,
    sums,
  };
  const missing: RiskPage = {
    root: "",
    riskPath: path,
    exists: false,
    builtin: true,
    model: { version: null, form: riskFormOf(root.builtin, "sample.builtin"), problems: [] },
    lock: unlocked,
  };
  return { page, missing };
}
