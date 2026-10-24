/**
 * 案内が指す先の印（`data-tour`）を、共通の図の部品（`shared/PlanGraph.tsx`）の中の点・線・詳細の欄などに付ける。
 *
 * 図の部品は承認のオーバーレイとも共有していて、そこに案内の印を持ち込まない。そこで、案内の間だけ画面が実際の DOM を
 * 見て、図の中の要素に印を付ける。点と線は React Flow が描くので、描き足されるたびに付け直す（`MutationObserver`）。
 * 印の値は小文字の英字で始まる英小文字・数字・- だけ（`parseTour` の検査）。
 *
 *   graph-<計画>  計画ごとの図（plan / feedback）      detail-<計画>  詳細の欄
 *   notes-<計画>  図の下の一言                          zoom-<計画>    拡大縮小のボタン
 *   node-<番号>   ステップの点（振り直したあとの番号）         edge-<先行>-<番号>  線        cut-<先行>-<番号>  線の ×
 *
 * 線は React Flow が点を測ってから描くので、案内が指す先を測る刻みには、まだ線が無いことがある。印を新しく付けたら、案内に
 * 測り直させる（案内は画面の大きさが変わったときに測り直すので、`resize` を流す）。
 *
 * 手順が `pick` でステップを選ぶときは、その点を押して詳細の欄を出す（見本の図の中の押す操作で、拡張ホストへは何も送らない）。
 */
import { useEffect, type RefObject } from "react";

/** 図の領域の中の要素に、案内の印を付ける。付け直しても同じ結果になる。新しく付けた印の数を返す */
export function markGraphs(root: ParentNode): number {
  let marked = 0;
  const mark = (el: Element | null, value: string): void => {
    if (el !== null && el.getAttribute("data-tour") !== value) {
      el.setAttribute("data-tour", value);
      marked += 1;
    }
  };
  for (const section of Array.from(root.querySelectorAll<HTMLElement>(".plan-graph[data-part]"))) {
    const part = section.getAttribute("data-part") ?? "";
    mark(section, `graph-${part}`);
    mark(section.querySelector(".plan-detail"), `detail-${part}`);
    mark(section.querySelector(".plan-graph-notes"), `notes-${part}`);
    mark(section.querySelector(".react-flow__controls"), `zoom-${part}`);
    for (const node of Array.from(section.querySelectorAll(".react-flow__node[data-id]"))) {
      const found = /^n(\d+)$/.exec(node.getAttribute("data-id") ?? "");
      if (found !== null) {
        mark(node, `node-${found[1]}`);
      }
    }
    for (const edge of Array.from(section.querySelectorAll(".react-flow__edge[data-id]"))) {
      const found = /^after-(\d+)-(\d+)$/.exec(edge.getAttribute("data-id") ?? "");
      if (found !== null) {
        mark(edge, `edge-${found[1]}-${found[2]}`);
      }
    }
    for (const button of Array.from(section.querySelectorAll('button[data-action="plan-remove-edge"][data-edge]'))) {
      const found = /^after-(\d+)-(\d+)$/.exec(button.getAttribute("data-edge") ?? "");
      if (found !== null) {
        mark(button, `cut-${found[1]}-${found[2]}`);
      }
    }
  }
  return marked;
}

/**
 * 案内の間（`enabled`）、`ref` の領域に印を付け続ける。`pick` があれば、その番号の点を 1 度だけ押す。
 * `generation` が変わったら（見本の図が作り直された）、押し直す
 */
export function useGraphMarks(ref: RefObject<HTMLElement | null>, enabled: boolean, pick: number | undefined, generation: string): void {
  useEffect(() => {
    const root = ref.current;
    if (!enabled || root === null) {
      return undefined;
    }
    let picked = false;
    const apply = (): void => {
      if (markGraphs(root) > 0) {
        window.dispatchEvent(new Event("resize"));
      }
      if (!picked && pick !== undefined) {
        const node = root.querySelector<HTMLElement>(`.react-flow__node[data-id="n${pick}"]`);
        if (node !== null) {
          picked = true;
          node.click();
        }
      }
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [ref, enabled, pick, generation]);
}
