/**
 * 図を画面いっぱいに出す部品。図の枠に `maximizeClass(maximized)` と `ref` を付け、枠の中に `MaximizeButton` を置く。
 * 出している間は枠が画面全体を覆い（`Maximize.css`）、ほかの表示は隠れる。
 *
 * - **ほかの表示は触れない。** 枠から `body` までの道すじの兄弟に `inert` を付け、Tab でも読み上げでも入れなくする
 *   （戻すときに外す）。`body` には `data-graph-max` を付け、画面のキー操作（元に戻す・貼り付けなど）が見えない文書を
 *   直さないよう、画面側が見て止められるようにする。出すとフォーカスをボタンへ移し、戻すとボタンへ戻す。
 * - **Esc は図が先に受ける。** `useEscape` の層（`ESCAPE_LAYER.maximize`）で受けるので、背後の承認オーバーレイなどは Esc では閉じない（より手前の案内の吹き出しが出ていれば、そちらが先に受ける）。
 * - 大きさが変わると React Flow は全体を映し直さないので、`RefitOnChange` を図の中に置く。
 */
import { useEffect, useRef, useState, type JSX, type RefObject } from "react";
import { ESCAPE_LAYER, useEscape } from "../useEscape.js";
import { useReactFlow, useStore, type Viewport } from "@xyflow/react";

/** 出している間 `body` に付ける属性 */
export const GRAPH_MAX_ATTR = "data-graph-max";

/** 全画面を出している間か（画面のキー操作が止めるのに使う） */
export function graphMaximized(): boolean {
  return document.body.hasAttribute(GRAPH_MAX_ATTR);
}

/** 出しているかと、切り替えと、図の枠につける ref */
export function useMaximize(): readonly [boolean, () => void, RefObject<HTMLDivElement | null>] {
  const [maximized, setMaximized] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  useEscape(maximized, ESCAPE_LAYER.maximize, () => setMaximized(false));
  useEffect(() => {
    if (!maximized) {
      return;
    }
    const el = box.current;
    const covered: Element[] = [];
    if (el !== null) {
      let node: Element = el;
      while (node.parentElement !== null) {
        for (const sibling of Array.from(node.parentElement.children)) {
          if (sibling !== node && !sibling.hasAttribute("inert") && sibling.tagName !== "SCRIPT") {
            sibling.setAttribute("inert", "");
            covered.push(sibling);
          }
        }
        if (node.parentElement === document.body) {
          break;
        }
        node = node.parentElement;
      }
    }
    document.body.setAttribute(GRAPH_MAX_ATTR, "1");
    const button = el?.querySelector<HTMLElement>('[data-action="toggle-maximize"]');
    button?.focus();
    return () => {
      for (const sibling of covered) {
        sibling.removeAttribute("inert");
      }
      document.body.removeAttribute(GRAPH_MAX_ATTR);
      button?.focus();
    };
  }, [maximized]);
  return [maximized, () => setMaximized((now) => !now), box];
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

/**
 * 全画面の出し入れで枠の大きさが変わったら映し直す（React Flow の中に置く）。出すときは全体を映し、戻すときは出す前の
 * 見え方（拡大・位置）に戻す。React Flow が新しい大きさを測り終えた（`width` `height` が替わった）ときに合わせる。
 * 大きさが替わらなかったとき用に、遅れて 1 度だけ合わせる。
 */
export function RefitOnChange({ value }: { readonly value: boolean }): null {
  const flow = useReactFlow();
  const size = useStore((s) => `${s.width}x${s.height}`);
  const latest = useRef(flow);
  latest.current = flow;
  const before = useRef(value);
  const pending = useRef<{ readonly open: boolean } | undefined>(undefined);
  const saved = useRef<Viewport | undefined>(undefined);
  const apply = useRef((): void => undefined);
  apply.current = (): void => {
    const todo = pending.current;
    if (todo === undefined) {
      return;
    }
    pending.current = undefined;
    if (todo.open || saved.current === undefined) {
      void latest.current.fitView({ padding: 0.1, maxZoom: 1.2 });
    } else {
      void latest.current.setViewport(saved.current);
    }
  };
  useEffect(() => {
    if (before.current === value) {
      return;
    }
    before.current = value;
    if (value) {
      saved.current = latest.current.getViewport();
    }
    pending.current = { open: value };
    const timer = setTimeout(() => apply.current(), 300);
    return () => clearTimeout(timer);
  }, [value]);
  useEffect(() => {
    // 大きさが替わったら、測り終えたあとの描画で合わせる
    const frame = requestAnimationFrame(() => apply.current());
    return () => cancelAnimationFrame(frame);
  }, [size]);
  return null;
}
