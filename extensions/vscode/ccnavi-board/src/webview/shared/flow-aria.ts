/**
 * 図（React Flow）が読み上げ用に付ける名前の日本語。既定は英語なので、図を描く3か所（フロー・計画・組み合わせ条件）で
 * 同じものを渡す。
 */
import type { AriaLabelConfig } from "@xyflow/react";

/** React Flowが渡す方向（英語）の日本語 */
const DIRECTIONS: Readonly<Record<string, string>> = { up: "上", down: "下", left: "左", right: "右" };

export const FLOW_ARIA_LABELS: Partial<AriaLabelConfig> = {
  "node.a11yDescription.default": "EnterかSpaceでノードを選びます。Escでやめます。",
  "node.a11yDescription.keyboardDisabled": "EnterかSpaceでノードを選び、矢印キーで動かします。Escでやめます。",
  "node.a11yDescription.ariaLiveMessage": ({ direction, x, y }) => `選んだノードを${DIRECTIONS[direction] ?? direction}へ動かしました。位置はx: ${x}、y: ${y}です`,
  "edge.a11yDescription.default": "EnterかSpaceで線を選びます。Escでやめます。",
  "controls.ariaLabel": "図の操作",
  "controls.zoomIn.ariaLabel": "拡大",
  "controls.zoomOut.ariaLabel": "縮小",
  "controls.fitView.ariaLabel": "全体を表示",
  "controls.interactive.ariaLabel": "操作の切り替え",
  "minimap.ariaLabel": "ミニマップ",
  "handle.ariaLabel": "接続口",
};
