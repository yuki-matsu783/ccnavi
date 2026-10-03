/**
 * `ccnavi --suggest --json`（候補の JSON）が出す形。実行ファイルと拡張の契約で、形の定義は
 * ccnavi の README「候補の JSON」。
 *
 * 候補は実行ファイルが記録から起こし、`--lint` と `--test-samples` と同じ検査を通したものだけ。
 * 拡張はそれを並べるだけで、候補を自分で起こさず、ルールにも足さない（置くのは人）。
 */

/** 拡張が読める版。実行ファイルが違う版を出したら、解釈せずに版の違いを伝える */
export const SUGGEST_VERSION = 1;

export interface SuggestSampleJson {
  readonly tool: string;
  readonly subject: string;
  readonly why: string;
}

export interface SuggestCandidateJson {
  /** `rule`（ルールを足す）か `message`（文面を見直す） */
  readonly kind: string;
  /** `ask` か `deny`。`allow` は出ない */
  readonly section: string;
  readonly id: string;
  readonly tool: string;
  readonly count: number;
  readonly summary: string;
  readonly layer: string;
  readonly rules_path: string;
  readonly samples: readonly SuggestSampleJson[];
  /** 文字で出すときと同じ下書き（`rules:` と `samples:` の組） */
  readonly yaml: string;
}

export interface SuggestJson {
  readonly version: number;
  readonly root: string;
  readonly rules_path: string;
  readonly logs: readonly string[];
  readonly records: number;
  readonly candidates: readonly SuggestCandidateJson[];
  readonly dropped: number;
}

export type Parsed<T> = { readonly ok: true; readonly value: T } | { readonly ok: false; readonly error: string };

export function parseSuggestJson(text: string): Parsed<SuggestJson> {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `候補の JSON として読めません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "候補の JSON の最上位がオブジェクトではありません" };
  }
  if (raw.version !== SUGGEST_VERSION) {
    return {
      ok: false,
      error: `候補の JSON の版が違います（拡張は ${SUGGEST_VERSION}、実行ファイルは ${String(raw.version)}）。拡張か実行ファイルを揃えてください`,
    };
  }
  return {
    ok: true,
    value: {
      version: SUGGEST_VERSION,
      root: str(raw.root),
      rules_path: str(raw.rules_path),
      logs: list(raw.logs).filter((p): p is string => typeof p === "string"),
      records: num(raw.records),
      // 出るのは deny と ask だけの約束。違う表記が来ても、通す側の候補は並べない
      candidates: list(raw.candidates)
        .filter(isRecord)
        .filter((c) => c.section === "deny" || c.section === "ask")
        .map((c) => ({
          kind: str(c.kind),
          section: str(c.section),
          id: str(c.id),
          tool: str(c.tool),
          count: num(c.count),
          summary: str(c.summary),
          layer: str(c.layer),
          rules_path: str(c.rules_path),
          samples: list(c.samples)
            .filter(isRecord)
            .map((s) => ({ tool: str(s.tool), subject: str(s.subject), why: str(s.why) })),
          yaml: str(c.yaml),
        })),
      dropped: num(raw.dropped),
    },
  };
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
