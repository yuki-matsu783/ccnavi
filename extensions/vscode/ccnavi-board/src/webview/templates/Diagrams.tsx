/**
 * 一覧の1行を開いたときに最初に見せる図。計画（全体計画・フィードバック計画）の図と、子のフローの図を読むだけで出す。
 * 中身は実行ファイルに読ませた答え（`TemplateCheck`）のまま描き、ここは判定しない。直すのは「図を編集」で開く編集欄（`Edit.tsx`）。
 */
import type { JSX } from "react";

import { type FlowDoc, sideBySide } from "../../core/flow-doc.js";
import { fromData } from "../../core/template-edit.js";
import type { TemplateCheck } from "../../core/templates-view.js";
import { Canvas } from "../flow/Canvas.js";
import { PlanGraph } from "../shared/PlanGraph.js";

const NOOP = (): void => undefined;

/** 子のフローの見出し。「1:調査 のフロー」の形で、フローの中身（名前）は書かない。番号に当たる計画の段が無ければ「1番のフロー」 */
function flowHeading(check: TemplateCheck, index: string): string {
  const item = check.plans.find((plan) => plan.part === "plan")?.items.find((it) => String(it.number) === index);
  return item === undefined ? `${index}番のフロー` : `${index}:${item.title !== "" ? item.title : item.type} のフロー`;
}

export function Diagrams({ check }: { readonly check: TemplateCheck }): JSX.Element {
  const template = fromData(check.data);
  const flows = template === undefined ? [] : Object.entries(template.flows).sort(([a], [b]) => Number(a) - Number(b));
  return (
    <div className="tpl-diagrams" data-role="diagrams">
      {check.plans.map((plan) => (
        <div key={plan.part} className="tpl-diagram">
          <h3>{plan.part === "plan" ? "全体計画" : "フィードバック計画"}</h3>
          <PlanGraph plan={plan} fitKey={`${plan.part}:${plan.items.length}`} />
        </div>
      ))}
      {flows.map(([index, doc]) => (
        <div key={index} className="tpl-diagram" data-role="flow-diagram" data-index={index}>
          <h3>{flowHeading(check, index)}</h3>
          <div className="flow-body tpl-flow-view" data-palette-folded="1" data-side-folded="1">
            <Canvas
              doc={sideBySide(doc as FlowDoc)}
              readOnly
              selected={undefined}
              onSelect={NOOP}
              onPick={NOOP}
              onMove={NOOP}
              onConnect={NOOP}
              onRemoveNode={NOOP}
              onRemoveEdge={NOOP}
              onResizeGroup={NOOP}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
