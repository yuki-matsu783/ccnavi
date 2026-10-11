/**
 * 図を画面いっぱいに出す部品。図の枠に `maximizeClass(maximized)` を付け、枠の中に `MaximizeButton` を置く。
 * 出している間は枠が画面全体を覆い（`Maximize.css`）、ほかの表示は隠れる。Esc でも戻る。
 * 大きさが変わると React Flow は全体を映し直さないので、`RefitOnChange` を図の中に置いて映し直す。
 */
import { useEffect, useRef, useState, type JSX } from "react";
import { useReactFlow } from "@xyflow/react";

/** 出しているかと、切り替え。出している間だけ Esc で戻す */
export function useMaximize(): readonly [boolean, () => void] {
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    if (!maximized) {
      return;
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setMaximized(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maximized]);
  return [maximized, () => setMaximized((now) => !now)];
}

export function maximizeClass(maximized: boolean): string {
  return maximized ? "graph-max is-max" : "graph-max";
}

export function MaximizeButton({ maximized, onToggle }: { readonly maximized: boolean; readonly onToggle: () => void }): JSX.Element {
  return (
    <button
      type="button"
      className="action small graph-max-button nodrag nopan"
      data-action="toggle-maximize"
      aria-pressed={maximized}
      aria-label={maximized ? "全画面を閉じる" : "図を全画面で表示"}
      title={maximized ? "全画面を閉じる（Esc）" : "図を全画面で表示"}
      onClick={onToggle}
    >
      {maximized ? "✕ 閉じる" : "⛶ 拡大"}
    </button>
  );
}

/** 全画面の出し入れで枠の大きさが変わったら、図の全体を映し直す（React Flow の中に置く） */
export function RefitOnChange({ value }: { readonly value: boolean }): null {
  const flow = useReactFlow();
  // flow の持ち主は描くたびに替わりうるので、値が替わったときだけ動くよう ref 越しに使う
  const latest = useRef(flow);
  latest.current = flow;
  const before = useRef(value);
  useEffect(() => {
    if (before.current === value) {
      return;
    }
    before.current = value;
    const timer = setTimeout(() => void latest.current.fitView({ padding: 0.1, maxZoom: 1.2 }), 50);
    return () => clearTimeout(timer);
  }, [value]);
  return null;
}
