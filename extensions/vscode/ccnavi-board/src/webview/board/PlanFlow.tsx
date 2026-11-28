/**
 * 承認済みの親の計画の流れ（開いた親の行の「計画の流れを見る」）。承認のオーバーレイと同じ図の部品（`PlanGraph`）を
 * 読むだけで出す。中身は`--explain --json`の`parents[].plans`で、実行ファイルが承認のpreviewと同じ関数で組んだもの。
 * 承認・編集の操作は置かない。開いているかは画面だけが持ち（拡張ホストへは返さない）、Escか「閉じる」で閉じる。
 */
import { useEffect, useRef, type JSX } from "react";

import type { Card } from "../../core/board.js";
import { Dialog, DialogActions } from "../shared/Dialog.js";
import { PlanGraph } from "../shared/PlanGraph.js";
import { ESCAPE_LAYER, useEscape } from "../useEscape.js";

export function PlanFlow({ card, onClose }: { readonly card: Card; readonly onClose: () => void }): JSX.Element {
  const box = useRef<HTMLElement>(null);
  // 開いたら「閉じる」に焦点を置く
  useEffect(() => {
    box.current?.querySelector<HTMLButtonElement>('button[data-action="plan-flow-close"]')?.focus({ preventScroll: true });
  }, [card.id]);
  useEscape(true, ESCAPE_LAYER.overlay, onClose);
  return (
    <Dialog titleId="plan-flow-title" backdropClass="plan-flow-backdrop" cardClass="plan-flow" backdropData={{ "data-plan-flow": card.id }} cardRef={box}>
      <h2 id="plan-flow-title">
        計画の流れ: {card.id}
        {card.title === "" ? null : <span className="plan-flow-name">{card.title}</span>}
      </h2>
      <p className="approval-note">承認済みの計画です。ここでは見るだけで、順序は直せません。</p>
      <div className="plan-graphs">
        {card.plans.map((plan) => (
          <PlanGraph key={`${plan.ticket}-${plan.part}`} plan={plan} />
        ))}
      </div>
      <DialogActions className="plan-flow-actions">
        <button type="button" className="action" data-action="plan-flow-close" onClick={onClose}>
          閉じる
        </button>
      </DialogActions>
    </Dialog>
  );
}
