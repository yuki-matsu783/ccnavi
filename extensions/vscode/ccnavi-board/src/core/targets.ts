/**
 * ルール管理・リスク管理・フェーズ管理・テンプレート管理の画面が、開いたまま切り替えられる設定の対象。
 * 共通設定、ワークスペースの設定、プロジェクト1つの設定の3種で、一覧は実行ファイルの答え（`trees`と`layers`）から作る。
 *
 * ここはvscodeにもDOMにも触れない。拡張ホストと画面の両方から読まれる。
 */
import { projectLayer } from "./layers.js";
import type { BoardJson } from "./model.js";
import type { SettingsTarget } from "./screens.js";

/** 2つの対象が同じか */
export function sameTarget(a: SettingsTarget, b: SettingsTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 開いている対象の欄の値（`TargetOption`の`kind`と`name`。`project`以外は`name`が空） */
export function targetKey(target: SettingsTarget): { kind: string; name: string } {
  return { kind: target.kind, name: target.kind === "project" ? target.name : "" };
}

/** 確認の文や知らせで使う対象の名前。「設定」欄の表示（`targetLabel`）をそのまま使う */
export function targetName(target: SettingsTarget): string {
  const key = targetKey(target);
  return targetLabel(key.kind, key.name);
}

/** 選ぶ欄の値（`kind`と`name`）を対象に戻す。知らない`kind`はundefined */
export function targetFrom(value: { readonly kind: unknown; readonly name: unknown }): SettingsTarget | undefined {
  if (value.kind === "workspace" || value.kind === "self") {
    return { kind: value.kind };
  }
  if (value.kind === "project" && typeof value.name === "string" && value.name !== "") {
    return { kind: "project", name: value.name };
  }
  return undefined;
}

/**
 * 設定の画面を開くときの対象。覚えた最後の選択（`remembered`）が、いまの答えの選択肢にあればそれを使う。
 * 無ければ画面の既定（`fallback`）で開き、そのことを1行で返す（`notice`。使えたときは空）。
 * 答えがまだ無い（`board`がundefined）ときは、選択肢を確かめられないので覚えたものをそのまま使う。
 */
export function openingTarget(remembered: SettingsTarget | undefined, board: BoardJson | undefined, fallback: SettingsTarget): { target: SettingsTarget; notice: string } {
  if (remembered === undefined || board === undefined) {
    return { target: remembered ?? fallback, notice: "" };
  }
  const key = targetKey(remembered);
  if (targetOptions(board, targetKey(fallback)).some((o) => o.kind === key.kind && o.name === key.name)) {
    return { target: remembered, notice: "" };
  }
  return { target: fallback, notice: `${targetName(remembered)}にはこの設定が無いため、${targetName(fallback)}を開きました` };
}

/** 対象1つ。`kind`は共通設定の呼び名（`workspace`）、`self`、`project`のどれか */
export interface TargetOption {
  readonly kind: string;
  /** `project`のときだけプロジェクト名。ほかは空 */
  readonly name: string;
  readonly label: string;
}

/** 対象を選ぶ欄の値（`kind`と`name`を1つの文字列にしたもの）。`name`に`:`が入っていても`kind`は`:`を含まないので戻せる */
export function targetValue(target: { readonly kind: string; readonly name: string }): string {
  return `${target.kind}:${target.name}`;
}

/**
 * 切り替えられる対象の一覧。共通、ワークスペース、設定の対象になっているプロジェクトの順。
 * 開いている対象が一覧に無ければ（ボードを読めなかったときなど）、それだけを足す。
 */
export function targetOptions(board: BoardJson | undefined, current: { readonly kind: string; readonly name: string }): TargetOption[] {
  const options: TargetOption[] = [
    { kind: "workspace", name: "", label: "共通設定" },
    { kind: "self", name: "", label: "ワークスペース" },
  ];
  if (board !== undefined) {
    for (const tree of board.trees) {
      if (tree.kind === "project" && projectLayer(board, tree.name) !== undefined) {
        options.push({ kind: "project", name: tree.name, label: `プロジェクト${tree.name}` });
      }
    }
  }
  if (!options.some((o) => o.kind === current.kind && o.name === current.name)) {
    options.push({ kind: current.kind, name: current.name, label: targetLabel(current.kind, current.name) });
  }
  return options;
}

/** 一覧に無い対象の表記。`project`と`self`以外は共通設定（呼び名は画面ごとに違うが、指すものは同じ） */
export function targetLabel(kind: string, name: string): string {
  return kind === "project" ? `プロジェクト${name}` : kind === "self" ? "ワークスペース" : "共通設定";
}
