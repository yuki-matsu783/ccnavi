/**
 * テンプレートの計画の表。番号・種類・レビュー・先行ステップ。先行ステップは計画の中の添字ではなく、提案にコピーしたときの番号で出す。
 */
import type { JSX } from "react";

import type { PlanItem } from "../../core/templates-view.js";
import { afterNumbers, afterText } from "./text.js";

export function Plan({ title, items }: { readonly title: string; readonly items: readonly PlanItem[] }): JSX.Element | null {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="tpl-plan-wrap">
      <table className="tpl-plan">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">番号</th>
            <th scope="col">種類</th>
            <th scope="col">レビュー</th>
            <th scope="col">先行ステップ</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.index}>
              <td className="mono">{item.number}</td>
              <td className="mono">{item.type}</td>
              <td>{item.review ? "review: true" : item.deferred ? "deferred: true" : "既定"}</td>
              <td>{afterText(afterNumbers(item, items))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
