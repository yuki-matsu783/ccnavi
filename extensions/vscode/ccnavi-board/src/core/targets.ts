/**
 * ルール管理とフェーズ管理の画面が、開いたまま切り替えられる設定の対象。
 * 共通の設定、ワークスペースの設定、プロジェクト 1 つの設定の 3 種で、一覧は実行ファイルの答え（`trees` と `layers`）から作る。
 *
 * ここは vscode にも DOM にも触れない。拡張ホストと画面の両方から読まれる。
 */
import { projectLayer } from "./layers.js";
import type { BoardJson } from "./model.js";

/** 対象 1 つ。`kind` は画面ごとの共通の設定の呼び名（ルール管理は `workspace`、フェーズ管理は `common`）、`self`、`project` のどれか */
export interface TargetOption {
  readonly kind: string;
  /** `project` のときだけプロジェクト名。ほかは空 */
  readonly name: string;
  readonly label: string;
}

/** 対象を選ぶ欄の値（`kind` と `name` を 1 つの文字列にしたもの）。`name` に `:` が入っていても `kind` は `:` を含まないので戻せる */
export function targetValue(target: { readonly kind: string; readonly name: string }): string {
  return `${target.kind}:${target.name}`;
}

/**
 * 切り替えられる対象の一覧。共通、ワークスペース、設定の対象になっているプロジェクトの順。
 * 開いている対象が一覧に無ければ（ボードを読めなかったときなど）、それだけを足す。
 */
export function targetOptions(board: BoardJson | undefined, commonKind: string, current: { readonly kind: string; readonly name: string }): TargetOption[] {
  const options: TargetOption[] = [
    { kind: commonKind, name: "", label: "共通の設定" },
    { kind: "self", name: "", label: "ワークスペース" },
  ];
  if (board !== undefined) {
    for (const tree of board.trees) {
      if (tree.kind === "project" && projectLayer(board, tree.name) !== undefined) {
        options.push({ kind: "project", name: tree.name, label: `プロジェクト ${tree.name}` });
      }
    }
  }
  if (!options.some((o) => o.kind === current.kind && o.name === current.name)) {
    options.push({ kind: current.kind, name: current.name, label: current.kind === "project" ? `プロジェクト ${current.name}` : current.kind });
  }
  return options;
}
