/**
 * `ccnavi --test --json` と `--test-samples --json`（試験の JSON）が出す形。
 * 実行ファイルと拡張の契約で、拡張はこれ以外を読まない。形の定義は ccnavi の README「試験の JSON」。
 * 同じ例が test/fixtures/test.json と samples.json にあり、Python 側のテスト
 * （tests/test_test_json.py）が同じ例で形を確かめる。
 */

/** 拡張が読める版。実行ファイルが違う版を出したら、解釈せずに版の違いを伝える */
export const TEST_VERSION = 1;

export interface RuleHitJson {
  readonly id: string;
  readonly source: "file" | "outside";
  readonly section: string;
  readonly kind: string;
  readonly written: string;
  readonly pattern: string;
}

export interface TestJson {
  readonly version: number;
  readonly root: string;
  readonly rules_path: string;
  readonly known: boolean;
  readonly tool: string;
  readonly subject: string;
  readonly resolved: string;
  readonly verdict: string;
  readonly code: string;
  readonly reason: string;
  readonly degraded: string;
  readonly fallback: string;
  readonly rules: readonly RuleHitJson[];
  readonly response: string;
}

export interface SampleJson {
  readonly expected: string;
  readonly tool: string;
  readonly subject: string;
  readonly resolved_subject: string;
  readonly why: string;
  readonly known: boolean;
  readonly verdict: string;
  readonly code: string;
  readonly reason: string;
  readonly rules: readonly RuleHitJson[];
  readonly ok: boolean;
  readonly skipped: boolean;
}

export interface SamplesJson {
  readonly version: number;
  readonly root: string;
  readonly rules_path: string;
  readonly samples_path: string;
  readonly counts: Readonly<Record<string, { readonly ok: number; readonly total: number }>>;
  readonly mismatches: number;
  readonly skipped: number;
  readonly samples: readonly SampleJson[];
}

export type Parsed<T> = { readonly ok: true; readonly value: T } | { readonly ok: false; readonly error: string };

export function parseTestJson(text: string): Parsed<TestJson> {
  const raw = readObject(text);
  if (!raw.ok) {
    return raw;
  }
  const r = raw.value;
  return {
    ok: true,
    value: {
      version: TEST_VERSION,
      root: str(r.root),
      rules_path: str(r.rules_path),
      known: r.known === true,
      tool: str(r.tool),
      subject: str(r.subject),
      resolved: str(r.resolved),
      verdict: str(r.verdict),
      code: str(r.code),
      reason: str(r.reason),
      degraded: str(r.degraded),
      fallback: str(r.fallback),
      rules: hits(r.rules),
      response: str(r.response),
    },
  };
}

export function parseSamplesJson(text: string): Parsed<SamplesJson> {
  const raw = readObject(text);
  if (!raw.ok) {
    return raw;
  }
  const r = raw.value;
  const counts: Record<string, { ok: number; total: number }> = {};
  if (isRecord(r.counts)) {
    for (const [section, c] of Object.entries(r.counts)) {
      if (isRecord(c)) {
        counts[section] = { ok: num(c.ok), total: num(c.total) };
      }
    }
  }
  return {
    ok: true,
    value: {
      version: TEST_VERSION,
      root: str(r.root),
      rules_path: str(r.rules_path),
      samples_path: str(r.samples_path),
      counts,
      mismatches: num(r.mismatches),
      skipped: num(r.skipped),
      samples: list(r.samples)
        .filter(isRecord)
        .map((s) => ({
          expected: str(s.expected),
          tool: str(s.tool),
          subject: str(s.subject),
          resolved_subject: str(s.resolved_subject),
          why: str(s.why),
          known: s.known === true,
          verdict: str(s.verdict),
          code: str(s.code),
          reason: str(s.reason),
          rules: hits(s.rules),
          ok: s.ok === true,
          skipped: s.skipped === true,
        })),
    },
  };
}

function readObject(text: string): Parsed<Record<string, unknown>> {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `試験の JSON として読めない: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "試験の JSON の最上位がオブジェクトではない" };
  }
  if (raw.version !== TEST_VERSION) {
    return {
      ok: false,
      error: `試験の JSON の版が違う（拡張は ${TEST_VERSION}、実行ファイルは ${String(raw.version)}）。拡張か実行ファイルを揃える`,
    };
  }
  return { ok: true, value: raw };
}

function hits(value: unknown): RuleHitJson[] {
  return list(value)
    .filter(isRecord)
    .map((h) => ({
      id: str(h.id),
      source: h.source === "outside" ? "outside" : "file",
      section: str(h.section),
      kind: str(h.kind),
      written: str(h.written),
      pattern: str(h.pattern),
    }));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
