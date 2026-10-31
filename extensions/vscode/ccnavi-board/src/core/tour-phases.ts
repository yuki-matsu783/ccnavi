/**
 * フェーズ管理画面の案内の見本（`webview/tours/phases.yml` の `sample:`）を、画面が描く形にする。
 *
 * 定義は `phases.yml` と同じ書き方（`version` と `phases`。定義は id をキーにした対応表）で見本に書き、ここで `PhasesPage`
 * （画面が描く形）にする。案内の間に描くだけで、保存にも判定にも使わない。`phases-doc.ts` は YAML の解析器を持つので画面から読めない。
 * 同じ読み方をここで小さく持ち、そろっていることはテストが見る。
 */
import { PHASE_KINDS, type PhaseForm, type PhaseKind, type PhasesForm, type PhasesPage } from "./phases-view.js";
import type { TargetOption } from "./targets.js";

export interface PhasesSample {
  /** ワークスペースの設定の画面（既定） */
  readonly page: PhasesPage;
  /** プロジェクトの設定の画面（scene の `target: lib`） */
  readonly project: PhasesPage;
  /** 「＋ 定義を追加」で足した定義（scene の `add: filled`） */
  readonly added: PhaseForm;
}

function object(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${at} がオブジェクトではない`);
  }
  return value as Record<string, unknown>;
}

/** 欄の文字。無いか null なら空（`phases-doc.ts` の `scalarText` と同じ） */
function str(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item) => item !== null && item !== undefined).map((item) => str(item)) : [];
}

function phaseOf(origin: number | null, id: string, raw: unknown): PhaseForm {
  const map = object(raw, `phases.${id}`);
  const kindText = str(map.kind) || "work";
  // scope は glob のリストなら上限、それ以外（inherit・欄なし）は親の範囲そのまま
  const inherit = !Array.isArray(map.scope);
  return {
    origin,
    id,
    title: str(map.title),
    kind: (PHASE_KINDS as readonly string[]).includes(kindText) ? (kindText as PhaseKind) : "work",
    review: map.review !== false,
    inherit,
    scope: inherit ? [] : list(map.scope),
    deliverables: list(map.deliverables),
    agent: str(map.agent),
    when: str(map.when),
  };
}

/** `phases.yml` の書き方の定義の対応表（`phases:` の中身）を、画面の `PhaseForm` の並びにする */
function phasesOf(raw: unknown, at: string): PhaseForm[] {
  return Object.entries(object(raw, at)).map(([id, value], index) => phaseOf(index, id, value));
}

/** `phases.yml` の書き方（`version` と `phases`）を、画面の `PhasesForm` にする */
export function phasesFormOf(raw: unknown, at = "phases"): PhasesForm {
  const root = object(raw, at);
  return { phases: phasesOf(root.phases, `${at}.phases`) };
}

function pageOf(raw: unknown, at: string, path: string, target: TargetOption, targets: readonly TargetOption[]): PhasesPage {
  const root = object(raw, at);
  const version = typeof root.version === "number" ? root.version : null;
  return {
    root: "",
    phasesPath: path,
    exists: true,
    model: { version, form: phasesFormOf(root, at), problems: [] },
    // 錠の文は scene の locked で見せる。錠そのものは案内の間は掛けない（見本は保存できる状態から始める）
    lock: { locked: false, reason: "", doing: [] },
    target: { kind: target.kind, name: target.name },
    targets,
  };
}

/** 作業中のチケットがあるときの文（scene の `locked`）。`self` と `lib` のものを返す */
export interface PhasesSampleLocks {
  readonly self: string;
  readonly lib: string;
}

export interface PhasesSampleTexts {
  readonly locks: PhasesSampleLocks;
  /** 保存の検証が通らなかったときの文（scene の `error`） */
  readonly error: string;
}

export function parsePhasesSample(raw: unknown): PhasesSample {
  const root = object(raw, "sample");
  const path = str(root.path);
  const project = object(root.project, "sample.project");
  const name = str(project.name);
  const targets: readonly TargetOption[] = [
    { kind: "self", name: "", label: "ワークスペース" },
    { kind: "project", name, label: `プロジェクト ${name}` },
  ];
  const page = pageOf(root.phases, "sample.phases", path, targets[0], targets);
  const lib = pageOf(project.phases, "sample.project.phases", str(project.path), targets[1], targets);
  const added = phasesOf(root.added, "sample.added");
  if (added.length !== 1) {
    throw new Error("sample.added は定義 1 件にする");
  }
  return { page, project: lib, added: { ...added[0], origin: null } };
}

/** 作業中のチケットの文と、検証が通らなかった文。画面が scene の `locked` / `error` で出す */
export function parsePhasesTexts(raw: unknown): PhasesSampleTexts {
  const root = object(raw, "sample");
  const project = object(root.project, "sample.project");
  return { locks: { self: str(root.lock), lib: str(project.lock) }, error: str(root.error) };
}
