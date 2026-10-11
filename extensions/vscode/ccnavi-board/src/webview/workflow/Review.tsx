/**
 * 保存（「この順序に決める」）の前の確かめ。番号の変わる項と、一緒に付け替える `todo/` の子の提案
 * （旧識別子 → 新識別子、`phase:` の旧 → 新、`predecessors` の直し）を見せる。並べるのは実行ファイルの答え
 * （`children`・`mismatched`）と、答えの番号と元の番号の違い（`renumbered`）だけで、画面は決めない。
 */
import { useEffect, useRef, type JSX } from "react";

import type { PlanOrderAnswer, PlanOrderChild } from "../../core/planordermodel.js";
import { renumbered } from "../../core/workflow-view.js";

/** 子 1 本の付け替えの 1 行。「i0001-02-01 → i0001-03-01（phase: 2 → 3）」。`predecessors` を直すなら添える */
function childText(child: PlanOrderChild): string {
  const parts: string[] = [];
  if (child.phase !== child.new_phase) {
    parts.push(`phase: ${child.phase ?? "?"} → ${child.new_phase ?? "?"}`);
  }
  if (child.predecessors.join(",") !== child.new_predecessors.join(",")) {
    parts.push(`predecessors: ${child.predecessors.join(", ")} → ${child.new_predecessors.join(", ")}`);
  }
  const head = child.ticket === child.new_ticket ? child.ticket : `${child.ticket} → ${child.new_ticket}`;
  return parts.length === 0 ? head : `${head}（${parts.join("、")}）`;
}

export interface ReviewProps {
  readonly ticket: string;
  readonly answer: PlanOrderAnswer;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}

export function Review({ ticket, answer, onConfirm, onCancel }: ReviewProps): JSX.Element {
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    confirm.current?.focus();
  }, []);
  const moved = renumbered(answer);
  return (
    <div className="workflow-review-backdrop">
      <section className="workflow-review" id="save-review" role="dialog" aria-modal="true" aria-labelledby="save-review-title">
        <h2 id="save-review-title">この順序に決める</h2>
        <p className="dim small">ccnavi が {ticket} の提案の計画（plan: / feedback:）と、番号の変わる項を指す子の提案を書き換えます。承認はしません。</p>
        <h3>番号が変わる項（{moved.length}）</h3>
        {moved.length === 0 ? (
          <p className="dim small">番号は変わりません（待ちだけが変わります）。</p>
        ) : (
          <ul id="review-numbers">
            {moved.map((item) => (
              <li key={`${item.part}-${item.from}`}>
                {item.title}: {item.from} → {item.number}
              </li>
            ))}
          </ul>
        )}
        <h3>付け替える子の提案（{answer.children.length}）</h3>
        {answer.children.length === 0 ? (
          <p className="dim small">付け替える子の提案はありません。</p>
        ) : (
          <ul id="review-children">
            {answer.children.map((child) => (
              <li key={child.ticket}>{childText(child)}</li>
            ))}
          </ul>
        )}
        {answer.mismatched.length > 0 && (
          <>
            <h3>計画と食い違う子の提案（書き換えません）</h3>
            <ul id="review-mismatched">
              {answer.mismatched.map((m) => (
                <li key={m.ticket}>{m.text}</li>
              ))}
            </ul>
          </>
        )}
        <p className="small">
          計画の値の中に書いたコメントは消えます（値の外のコメントは残ります）。書き換えた提案はコミットされないまま承認されるので、承認したあとは取り下げられません（取り消して出し直します）。保存したら、承認はボードから開き直してください。
        </p>
        <div className="workflow-review-actions">
          <button type="button" className="action" data-action="cancel-save" onClick={onCancel}>
            やめる
          </button>
          <button type="button" className="action primary" data-action="confirm-save" ref={confirm} onClick={onConfirm}>
            保存する
          </button>
        </div>
      </section>
    </div>
  );
}
