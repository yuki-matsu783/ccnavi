/**
 * 取り込みの結果。取り込めた対象（`applied`）、消した下書き、残した下書きと理由（`kept_drafts`）を出す。
 * 失敗のときは理由を出す（実行ファイルが断ったときも、拡張が呼ぶ前にはじいたときも同じ形）。
 */
import type { JSX } from "react";

import type { HandoffOutcome } from "../../core/handoff-view.js";
import { resultSummary } from "../../core/handoffmodel.js";
import { kindLabel } from "./text.js";

export function Result({ outcome }: { readonly outcome: HandoffOutcome }): JSX.Element {
  const { result } = outcome;
  if (!result.ok) {
    return (
      <div className="result failed" data-role="result" role="status">
        <h3>取り込めませんでした</h3>
        <p data-role="result-error">{result.error}</p>
      </div>
    );
  }
  return (
    <div className="result" data-role="result" role="status">
      <h3>{resultSummary(result)}</h3>
      {result.applied.length > 0 && (
        <ul data-role="applied">
          {result.applied.map((a) => (
            <li key={a.path}>
              {a.path}（{kindLabel(a.kind)}）
            </li>
          ))}
        </ul>
      )}
      {result.removedDrafts.length > 0 && (
        <>
          <p>消した下書き</p>
          <ul data-role="removed-drafts">
            {result.removedDrafts.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
        </>
      )}
      {result.keptDrafts.length > 0 && (
        <>
          <p>残した下書き（編集中のため消さない、など）</p>
          <ul data-role="kept-drafts">
            {result.keptDrafts.map((kept) => (
              <li key={kept.path}>
                {kept.path} <span className="why">{kept.reason}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
