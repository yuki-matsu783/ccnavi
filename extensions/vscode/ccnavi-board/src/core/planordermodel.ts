/**
 * `--plan-order <親> --json [--order <JSON>]` と、保存の `--plan-order <親> --order <JSON> --expect <ハッシュ> --write --json`
 * の答え（実行ファイルとの契約）と読み取り。形はリポジトリの設計（`docs/design/tickets/approval.md` の
 * 計画の順序の振り直しの表）と、Python 側が書き出す実例 `test/fixtures/plan-order.json`。
 *
 * 計画編集タブ（`workflow-panel.ts`）が読む。**番号の振り直し・順序の検査・書けるか（`writable`）は
 * 実行ファイルが答え、拡張は読むだけ**。計画ごとの図の中身（`plans`）は承認の preview と同じ関数で作られるので、
 * 読み方も `approvemodel.ts` の `readPlans` を使う。
 *
 * VS Code の API も DOM も node も読まない。
 */
import { readPlans, type ApprovePlan } from "./approvemodel.js";

/** 答えの版（実行ファイルの `plan_order.VERSION`）。欄を足すだけなら上がらない */
export const PLAN_ORDER_VERSION = 1;

/**
 * 元の番号（いま提案に書いてある番号）での直接の先行。計画（`plan` / `feedback`）ごとに、番号の文字列 → 先行の番号。
 * 渡した計画は全部の番号の先行をそこから取る（書いていない番号は先行なし）。渡さない計画は提案のまま
 */
export type PlanOrder = Readonly<Record<string, Readonly<Record<string, readonly number[]>>>>;

/** 振り直せない線（`cycle` 循環・`locked` 子が承認された番号へ入る線を変えた・`unpackable` 詰められない） */
export interface PlanOrderRefusal {
  readonly kind: string;
  readonly number: number;
  readonly text: string;
}

/** 付け替える `todo/` の子の提案 1 本（旧 → 新） */
export interface PlanOrderChild {
  readonly ticket: string;
  readonly new_ticket: string;
  readonly path: string;
  readonly new_path: string;
  readonly phase: number | null;
  readonly new_phase: number | null;
  readonly predecessors: readonly string[];
  readonly new_predecessors: readonly string[];
}

/** `phase:` が親の計画の番号に無い `todo/` の子の提案。知らせるだけで、書き戻しは止めない */
export interface PlanOrderMismatch {
  readonly ticket: string;
  readonly path: string;
  readonly phase: number | null;
  readonly text: string;
}

export interface PlanOrderAnswer {
  readonly version: number;
  readonly ticket: string;
  /** 親の提案と子の提案をまとめたハッシュ。保存の `--expect` に返す */
  readonly source_sha: string;
  readonly plans: readonly ApprovePlan[];
  /** 振り直した計画の順序の検査の error（両方の計画） */
  readonly problems: readonly string[];
  readonly loose: readonly number[];
  /** 断った線。断ったときは振り直さず、提案のままの答えが入っている */
  readonly refused: readonly PlanOrderRefusal[];
  /** 改版の錠（子が承認された番号）にヒットするもの */
  readonly revision_problems: readonly string[];
  readonly children: readonly PlanOrderChild[];
  /** 子を付け替えられない理由 */
  readonly child_problems: readonly string[];
  readonly mismatched: readonly PlanOrderMismatch[];
  /** 書き戻せるか（実行ファイルが決める） */
  readonly writable: boolean;
  /** `--write` のときだけ。書いたか */
  readonly written?: boolean;
}

export type PlanOrderParse = { readonly ok: true; readonly value: PlanOrderAnswer } | { readonly ok: false; readonly error: string };

export function parsePlanOrder(text: string): PlanOrderParse {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として解釈できません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  if (raw.version !== PLAN_ORDER_VERSION) {
    return { ok: false, error: `--plan-order の答えの版が違います（拡張は ${PLAN_ORDER_VERSION}、実行ファイルは ${String(raw.version)}）` };
  }
  return {
    ok: true,
    value: {
      version: PLAN_ORDER_VERSION,
      ticket: str(raw.ticket),
      source_sha: str(raw.source_sha),
      plans: readPlans(raw.plans),
      problems: strings(raw.problems),
      loose: list(raw.loose).filter(isNumber),
      refused: list(raw.refused)
        .filter(isRecord)
        .map((r) => ({ kind: str(r.kind), number: isNumber(r.number) ? r.number : 0, text: str(r.text) })),
      revision_problems: strings(raw.revision_problems),
      children: list(raw.children)
        .filter(isRecord)
        .map((c) => ({
          ticket: str(c.ticket),
          new_ticket: str(c.new_ticket),
          path: str(c.path),
          new_path: str(c.new_path),
          phase: isNumber(c.phase) ? c.phase : null,
          new_phase: isNumber(c.new_phase) ? c.new_phase : null,
          predecessors: strings(c.predecessors),
          new_predecessors: strings(c.new_predecessors),
        })),
      child_problems: strings(raw.child_problems),
      mismatched: list(raw.mismatched)
        .filter(isRecord)
        .map((m) => ({ ticket: str(m.ticket), path: str(m.path), phase: isNumber(m.phase) ? m.phase : null, text: str(m.text) })),
      // 真偽でなければ書けない側に読む
      writable: raw.writable === true,
      ...(typeof raw.written === "boolean" ? { written: raw.written } : {}),
    },
  };
}

/**
 * 保存（`--write --json`）の答えを、パネルが読む形にしたもの。`changed` は開いたときのハッシュ（`--expect`）が
 * 今の中身と違った（外で変わった。何も書いていない）。`refused` は書かなかった（順序の検査・改版の錠・子の付け替え
 * など。理由は実行ファイルの標準エラー）。`unchanged` は振り直しても変わらず、書くものが無かった
 */
export type PlanOrderWrite =
  | { readonly kind: "written"; readonly answer: PlanOrderAnswer }
  | { readonly kind: "unchanged"; readonly answer: PlanOrderAnswer }
  | { readonly kind: "changed"; readonly answer: PlanOrderAnswer }
  | { readonly kind: "refused"; readonly answer: PlanOrderAnswer; readonly reason: string }
  | { readonly kind: "error"; readonly error: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  return list(value).filter((v): v is string => typeof v === "string");
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}
