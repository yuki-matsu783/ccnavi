/**
 * 保存（「この順序に決める」）の前の確かめ。番号の変わるステップと、一緒に付け替える `todo/` の子の提案
 * （旧識別子 → 新識別子、`phase:` の旧 → 新、`predecessors` の直し）を見せる。並べるのは実行ファイルの答え
 * （`children`・`mismatched`）と、答えの番号と元の番号の違い（`renumbered`）だけで、画面は決めない。
 */
import { useEffect, useRef, type JSX } from "react";

import type { PlanOrderAnswer, PlanOrderChild } from "../../core/planordermodel.js";
import { Dialog, DialogActions } from "../shared/Dialog.js";
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
  /** 開いたとき「保存する」へ焦点を移すか。案内の見本の確かめでは移さない（案内の吹き出しが焦点を持つ） */
  readonly focus?: boolean;
}

export function Review({ ticket, answer, onConfirm, onCancel, focus = true }: ReviewProps): JSX.Element {
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (focus) {
      confirm.current?.focus();
    }
  }, [focus]);
  const moved = renumbered(answer);
  return (
    <Dialog titleId="save-review-title" backdropClass="workflow-review-backdrop" cardClass="workflow-review" id="save-review">
      <h2 id="save-review-title">この順序に決める</h2>
      <p className="dim small">ccnavi が {ticket} の提案の計画（plan: / feedback:）と、番号の変わるステップを指す子の提案を変更します。承認はしません。</p>
      <div data-tour="review-numbers">
        <h3>番号が変わるステップ（{moved.length}）</h3>
        {moved.length === 0 ? (
          <p className="dim small">番号は変わりません（先行だけが変わります）。</p>
        ) : (
          <ul id="review-numbers">
            {moved.map((item) => (
              <li key={`${item.part}-${item.from}`}>
                {item.title}: {item.from} → {item.number}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div data-tour="review-children">
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
      </div>
      {answer.mismatched.length > 0 && (
        <>
          <h3>計画と食い違う子の提案（変更しません）</h3>
          <ul id="review-mismatched">
            {answer.mismatched.map((m) => (
              <li key={m.ticket}>{m.text}</li>
            ))}
          </ul>
        </>
      )}
      <p className="small" data-tour="review-note">
        計画の値の中のコメントは消えます（値の外のコメントは残ります）。変更した提案はコミットされないまま承認されるので、承認したあとに直すときは、子は取り消して出し直し、親は改版するか取り消します。保存したら、承認はボードから開き直してください。
      </p>
      <DialogActions className="workflow-review-actions">
        <button type="button" className="action" data-action="cancel-save" onClick={onCancel}>
          キャンセル
        </button>
        <button type="button" className="action primary" data-action="confirm-save" ref={confirm} onClick={onConfirm}>
          保存する
        </button>
      </DialogActions>
    </Dialog>
  );
}
