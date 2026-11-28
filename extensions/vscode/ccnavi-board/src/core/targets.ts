/**
 * ルール管理・リスク管理・フェーズ管理・テンプレート管理の画面が、開いたまま切り替えられる設定の対象。
 * 共通の設定、ワークスペースの設定、プロジェクト1つの設定の3種で、一覧は実行ファイルの答え（`trees`と`layers`）から作る。
 *
 * ここはvscodeにもDOMにも触れない。拡張ホストと画面の両方から読まれる。
 */
import { projectLayer } from "./layers.js";
import type { BoardJson } from "./model.js";
import type { LayerTarget } from "./screens.js";

/** 2つのレイヤーが同じか */
export function sameLayer(a: LayerTarget, b: LayerTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 選ぶ欄と突き合わせる形（`project`以外は`name`が空） */
export function layerKey(target: LayerTarget): { kind: string; name: string } {
  return { kind: target.kind, name: target.kind === "project" ? target.name : "" };
}

/** レイヤーの名前。「設定」欄の表示（`targetOptions`の`label`）と揃える */
export function layerName(target: LayerTarget): string {
  switch (target.kind) {
    case "workspace":
      return "共通の設定";
    case "self":
      return "ワークスペース";
    case "project":
      return `プロジェクト${target.name}`;
  }
}

/** 選ぶ欄の値（`kind`と`name`）をレイヤーに戻す。知らない`kind`はundefined */
export function layerFrom(value: { readonly kind: unknown; readonly name: unknown }): LayerTarget | undefined {
  if (value.kind === "workspace" || value.kind === "self") {
    return { kind: value.kind };
  }
  if (value.kind === "project" && typeof value.name === "string" && value.name !== "") {
    return { kind: "project", name: value.name };
  }
  return undefined;
}

/**
 * 設定の画面を開くときのレイヤー。覚えた最後の選択（`remembered`）が、いまの答えの選択肢にあればそれを使う。
 * 無ければ画面の既定（`fallback`）で開き、そのことを1行で返す（`notice`。使えたときは空）。
 * 答えがまだ無い（`board`がundefined）ときは、選択肢を確かめられないので覚えたものをそのまま使う。
 */
export function openingLayer(remembered: LayerTarget | undefined, board: BoardJson | undefined, fallback: LayerTarget): { target: LayerTarget; notice: string } {
  if (remembered === undefined || board === undefined) {
    return { target: remembered ?? fallback, notice: "" };
  }
  const key = layerKey(remembered);
  if (targetOptions(board, "workspace", layerKey(fallback)).some((o) => o.kind === key.kind && o.name === key.name)) {
    return { target: remembered, notice: "" };
  }
  return { target: fallback, notice: `${layerName(remembered)}にはこの設定が無いため、${layerName(fallback)}を開きました` };
}

/** 対象1つ。`kind`は共通の設定の呼び名（`workspace`）、`self`、`project`のどれか */
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
 * `commonKind`がundefinedなら、共通の設定を並べない（今はどの画面も`workspace`を渡す）。
 * 開いている対象が一覧に無ければ（ボードを読めなかったときなど）、それだけを足す。
 */
export function targetOptions(board: BoardJson | undefined, commonKind: string | undefined, current: { readonly kind: string; readonly name: string }): TargetOption[] {
  const options: TargetOption[] = [
    ...(commonKind === undefined ? [] : [{ kind: commonKind, name: "", label: "共通の設定" }]),
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
    options.push({ kind: current.kind, name: current.name, label: current.kind === "project" ? `プロジェクト${current.name}` : current.kind });
  }
  return options;
}
