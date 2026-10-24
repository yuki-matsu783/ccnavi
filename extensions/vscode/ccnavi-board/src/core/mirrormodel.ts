/**
 * プロジェクト管理画面が読む、共通レイヤーのミラーの結果（`user mirror --json`）の読み取りと、画面の判断の部分。
 *
 * ミラーは、ワークスペースの `.ccnavi/config/common/`（共通レイヤー）を各プロジェクトの `.ccnavi/config/common/` に写したもの。
 * ずれの有無・中身・ハッシュ（`expect`）は実行ファイルが決めて返し、同期で書くのも実行ファイル。ここで決めるのは次の 3 つだけ。
 *
 * - 行と帯に出す文言（同期済み / ずれ N 件 / 調べられない、変更の種類の言い方、同期の結果の通知）
 * - ボタンを押せるか（実行ファイルが古い・読めない・同期中・写すものが無い・ずれが無い、のどれかなら押せない）
 * - 「すべて同期」の対象（`state` が `differs` のプロジェクト）と、そのとき渡す `expect`（結果全体の `expect`）
 *
 * VS Code の API も DOM も node も読まない（単体で試せる。画面にもバンドルされる）。
 */

/** JSON の版。欄を追加するだけなら上がらない */
export const MIRROR_VERSION = 1;

/** 変更の種類。知らない値も通す（実行ファイルが種類を足しても、画面は止めない） */
export type MirrorAction = "update" | "create" | "delete" | (string & {});

/** プロジェクトの状態。知らない値も通すが、同期できるのは `differs` だけ */
export type MirrorState = "synced" | "differs" | "error" | (string & {});

/** 写すと変わるファイル 1 本 */
export interface MirrorChange {
  /** プロジェクトのルートからの相対パス。`/` 区切り */
  readonly rel: string;
  readonly action: MirrorAction;
}

/** プロジェクト 1 件の結果 */
export interface MirrorProject {
  readonly name: string;
  readonly state: MirrorState;
  readonly changes: readonly MirrorChange[];
  /** 調べられない・書けない理由（`state` が `error` のとき） */
  readonly error: string;
  /** そのプロジェクトだけを同期するときに渡す `--expect` の値。ずれが無ければ空 */
  readonly expect: string;
}

/** 見るだけの結果（`user mirror [<名前>…] --json`）の全体 */
export interface MirrorList {
  readonly version: number;
  /** 偽なら `error` に理由（共通レイヤーが読めないなど）。そのとき `projects` は空 */
  readonly ok: boolean;
  readonly error: string;
  /** 共通レイヤーに写すものが無い。同期しても何も起きない（ミラーは消さない） */
  readonly commonEmpty: boolean;
  readonly projects: readonly MirrorProject[];
  /** 差分のある全プロジェクトをまとめて同期するときに渡す `--expect` の値。差分が無ければ空 */
  readonly expect: string;
}

/** 同期できたプロジェクト 1 件と、書いたファイル */
export interface MirrorSynced {
  readonly name: string;
  readonly files: readonly string[];
}

/** 同期の結果（`user mirror [<名前>…] --yes --expect <値> --via board --json`）。失敗は `error` に理由 */
export type MirrorResult =
  | { readonly ok: true; readonly synced: readonly MirrorSynced[] }
  // 一部のプロジェクトだけ書けなかったときは、書けた分を `synced` で返す
  | { readonly ok: false; readonly error: string; readonly synced?: readonly MirrorSynced[] };

export type MirrorListParse = { readonly ok: true; readonly value: MirrorList } | { readonly ok: false; readonly error: string };
export type MirrorResultParse = { readonly ok: true; readonly value: MirrorResult } | { readonly ok: false; readonly error: string };

// ---- 読み取り

/**
 * 見るだけの結果を読む。版が違う・欄が足りないときは文面を返す。
 * `ok` が偽の結果は読み取りの失敗にしない（`ok: false` の一覧として返し、画面が理由を出す）。
 */
export function parseMirrorList(text: string): MirrorListParse {
  const parsed = parseRecord(text);
  if (!parsed.ok) {
    return parsed;
  }
  const raw = parsed.value;
  if (raw.version !== MIRROR_VERSION) {
    return { ok: false, error: versionMismatch(raw.version) };
  }
  if (typeof raw.ok !== "boolean") {
    return { ok: false, error: "ミラーの JSON の ok 欄が真偽値ではありません" };
  }
  if (!raw.ok) {
    return {
      ok: true,
      value: { version: MIRROR_VERSION, ok: false, error: str(raw.error) || "ミラーを調べられませんでした（理由は返りませんでした）", commonEmpty: raw.common_empty === true, projects: [], expect: "" },
    };
  }
  if (!Array.isArray(raw.projects)) {
    return { ok: false, error: "ミラーの JSON の projects 欄が配列ではありません" };
  }
  const projects: MirrorProject[] = [];
  for (const item of raw.projects) {
    if (!isRecord(item) || typeof item.name !== "string" || item.name === "") {
      return { ok: false, error: "ミラーの JSON の projects に、name の無いプロジェクトがあります" };
    }
    projects.push({
      name: item.name,
      state: str(item.state),
      changes: list(item.changes)
        .filter(isRecord)
        .map((c) => ({ rel: str(c.rel), action: str(c.action) }))
        .filter((c) => c.rel !== ""),
      error: str(item.error),
      expect: str(item.expect),
    });
  }
  return {
    ok: true,
    value: { version: MIRROR_VERSION, ok: true, error: str(raw.error), commonEmpty: raw.common_empty === true, projects, expect: str(raw.expect) },
  };
}

/**
 * 同期の結果を読む。失敗の結果（`{version, ok:false, error}`）は `ok:false` の結果として返す（読み取りの失敗ではない）。
 */
export function parseMirrorResult(text: string): MirrorResultParse {
  const parsed = parseRecord(text);
  if (!parsed.ok) {
    return parsed;
  }
  const raw = parsed.value;
  if (raw.version !== MIRROR_VERSION) {
    return { ok: false, error: versionMismatch(raw.version) };
  }
  if (typeof raw.ok !== "boolean") {
    return { ok: false, error: "ミラーの同期の JSON の ok 欄が真偽値ではありません" };
  }
  const readSynced = (value: unknown): readonly MirrorSynced[] =>
    Array.isArray(value)
      ? value
          .filter(isRecord)
          .map((s) => ({ name: str(s.name), files: list(s.files).filter((f): f is string => typeof f === "string") }))
          .filter((s) => s.name !== "")
      : [];
  if (!raw.ok) {
    const partial = readSynced(raw.synced);
    const error = str(raw.error) || "共通設定を同期できませんでした（理由は返りませんでした）";
    return { ok: true, value: partial.length > 0 ? { ok: false, error, synced: partial } : { ok: false, error } };
  }
  if (!Array.isArray(raw.synced)) {
    return { ok: false, error: "ミラーの同期の JSON の synced 欄が配列ではありません" };
  }
  return { ok: true, value: { ok: true, synced: readSynced(raw.synced) } };
}

// ---- 画面の判断

/** 名前を比べるための形。大文字小文字と NFC/NFD の違いは無視する（どの機械でも区別しない）。表示や引数には使わない */
function foldName(name: string): string {
  return name.normalize("NFC").toLowerCase();
}

/** 一覧からプロジェクトを引く。無ければ undefined */
export function mirrorOf(list: MirrorList, name: string): MirrorProject | undefined {
  const key = foldName(name);
  return list.projects.find((p) => foldName(p.name) === key);
}

/**
 * 同期の対象として受け取れない名前。`-` で始まるもの（実行ファイルのオプションに読まれる）と、いま見せている
 * 一覧に無いもの。画面から届く値を、そのまま実行ファイルの引数にしないための確かめ
 */
export function unknownSyncNames(status: MirrorStatus | undefined, names: readonly string[]): readonly string[] {
  const list = status !== undefined && status.kind === "ok" ? status.list : undefined;
  return names.filter((n) => n.startsWith("-") || list === undefined || mirrorOf(list, n) === undefined);
}

/** 行に出す状態の一言 */
export function stateText(project: MirrorProject): string {
  switch (project.state) {
    case "synced":
      return "同期済み";
    case "differs":
      return `ずれ ${project.changes.length} 件`;
    case "error":
      return "調べられません";
    default:
      return `知らない状態です（${project.state}）`;
  }
}

/** 変更の種類の言い方 */
export function actionText(action: MirrorAction): string {
  switch (action) {
    case "update":
      return "更新";
    case "create":
      return "追加";
    case "delete":
      return "削除";
    default:
      return action;
  }
}

/** 「すべて同期」の対象。`differs` のプロジェクトの名前を、結果の順で返す */
export function syncAllTargets(list: MirrorList): string[] {
  return list.projects.filter((p) => p.state === "differs").map((p) => p.name);
}

/** ボタンを押せるか。押せないときは `reason` に理由（ボタンの title に出す） */
export interface SyncButton {
  readonly enabled: boolean;
  readonly reason: string;
}

/**
 * ミラーを調べた結果（`projects-view.ts` の `MirrorData` が持つ）。`waiting` はまだ調べていない、`unsupported` は実行ファイルが
 * `user mirror` を持たない、`failed` は調べられなかった（実行ファイルが無い・期限切れ・結果が読めない）
 */
export type MirrorStatus =
  | { readonly kind: "waiting" }
  | { readonly kind: "unsupported"; readonly reason: string }
  | { readonly kind: "failed"; readonly error: string }
  | { readonly kind: "ok"; readonly list: MirrorList };

/** 一覧に共通する押せない理由。押せるなら空 */
function commonBlock(status: MirrorStatus, busy: boolean): string {
  if (busy) {
    return "同期しています。終わるまで待ってください";
  }
  switch (status.kind) {
    case "waiting":
      return "ミラーを調べています";
    case "unsupported":
      return status.reason;
    case "failed":
      return "ミラーを調べられませんでした。「更新」で調べ直してください";
    case "ok":
      if (!status.list.ok) {
        return "ミラーを調べられませんでした。「更新」で調べ直してください";
      }
      return status.list.commonEmpty ? "共通レイヤーに写すものがありません" : "";
  }
}

/** プロジェクト 1 件の「共通設定を同期」を押せるか */
export function projectSyncButton(status: MirrorStatus, busy: boolean, name: string): SyncButton {
  const common = commonBlock(status, busy);
  if (common !== "") {
    return { enabled: false, reason: common };
  }
  const project = status.kind === "ok" ? mirrorOf(status.list, name) : undefined;
  if (project === undefined) {
    return { enabled: false, reason: "ミラーの結果にこのプロジェクトがありません" };
  }
  if (project.state === "synced") {
    return { enabled: false, reason: "ずれはありません" };
  }
  if (project.state !== "differs") {
    return { enabled: false, reason: project.error || stateText(project) };
  }
  if (project.expect === "") {
    return { enabled: false, reason: "同期に要る値が返りませんでした。「更新」で調べ直してください" };
  }
  return { enabled: true, reason: "" };
}

/** 「すべて同期」を押せるか。件数も返す（ボタンの文言に出す） */
export function syncAllButton(status: MirrorStatus, busy: boolean): SyncButton & { readonly count: number } {
  const count = status.kind === "ok" ? syncAllTargets(status.list).length : 0;
  const common = commonBlock(status, busy);
  if (common !== "") {
    return { enabled: false, reason: common, count };
  }
  if (count === 0) {
    return { enabled: false, reason: "ずれのあるプロジェクトはありません", count };
  }
  if (status.kind === "ok" && status.list.expect === "") {
    return { enabled: false, reason: "同期に要る値が返りませんでした。「更新」で調べ直してください", count };
  }
  return { enabled: true, reason: "", count };
}

/** 見せた内容と、同期する直前の内容がずれたという結果か（画面を読み直して、もう一度押してもらう） */
export function isStale(error: string): boolean {
  return error.includes("内容が更新されました");
}

/** 同期の結果の通知の文。`kind` は通知の種類（成功は情報、ずれは警告、失敗はエラー） */
export function syncNotice(result: MirrorResult): { readonly kind: "info" | "warn" | "error"; readonly message: string } {
  if (!result.ok) {
    if (isStale(result.error)) {
      return { kind: "warn", message: "表示した後でミラーの内容が変わったので、同期しませんでした。最新の状態を読み直したので、確かめてからもう一度押してください" };
    }
    const done = result.synced ?? [];
    if (done.length > 0) {
      const names = done.map((s) => s.name).join("、");
      return {
        kind: "error",
        message: `共通設定を同期できなかったものがあります: ${result.error}。同期できたのは ${names} です（作業ツリーに書いただけで、コミットはしていません）`,
      };
    }
    return { kind: "error", message: `共通設定を同期できませんでした: ${result.error}` };
  }
  if (result.synced.length === 0) {
    return { kind: "info", message: "同期するものはありませんでした" };
  }
  const files = result.synced.reduce((sum, s) => sum + s.files.length, 0);
  const names = result.synced.map((s) => s.name).join("、");
  return {
    kind: "info",
    message: `${names} の共通設定を同期しました（${files} ファイル）。書いたのは各プロジェクトの作業ツリーで、コミットはしていません。必要なら各プロジェクトでコミットしてください`,
  };
}

/** 最終確認の時刻の表示（`HH:MM`）。拡張ホストの時計で組む */
export function clockText(date: Date): string {
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// ---- 共通の部品

/** JSON として読み、最上位がオブジェクトのものを返す。読めなければ文面 */
function parseRecord(text: string): { readonly ok: true; readonly value: Record<string, unknown> } | { readonly ok: false; readonly error: string } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として解釈できません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  return { ok: true, value: raw };
}

function versionMismatch(version: unknown): string {
  return `ミラーの JSON の形が違います（拡張は ${MIRROR_VERSION}、実行ファイルは ${String(version)}）`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}
