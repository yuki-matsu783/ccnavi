/**
 * 時系列の図（案内の中で「誰が誰に何をするか」を見せる）。登場者を左から並べ、矢印を上から下へ時間順に並べる。
 * 矢印には、実際に入れる値や出る結果を等幅で添えられる（`value`）。値を変えると結果が変わる例は、図を分けて並べる。
 *
 * 絵は DOM と CSS だけで描く（画像にしない）ので、VS Code のテーマに従い、文字を直すのは YAML だけで済む。
 * `focus` に入れた矢印は光らせ、ほかは薄く出す（空なら全部を同じ濃さで出す）。
 * 矢印の向きは `from` から `to`。同じ登場者なら、その列の覚え書き（自分の中の動き）として箱で出す。
 */
import type { CSSProperties, JSX } from "react";

import type { DiagramDef } from "../../core/tour-def.js";

export function SequenceDiagram({ name, diagram, focus }: { readonly name: string; readonly diagram: DiagramDef; readonly focus: readonly string[] }): JSX.Element {
  const count = diagram.actors.length;
  const column = (actor: string): number => diagram.actors.indexOf(actor);
  // 縦の線（登場者の時間軸）は、列の中央に背景として引く。行ごとに分かれて途切れないよう、箱全体に引く
  const lines = diagram.actors.map((_, index) => `linear-gradient(var(--tour-diagram-line), var(--tour-diagram-line)) calc(${index} * 100% / ${count} + 50% / ${count}) 0 / 1px 100% no-repeat`).join(", ");
  const style = { gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))`, backgroundImage: lines } as CSSProperties;
  return (
    <div className="tour-diagram" data-diagram={name} style={style} role="group" aria-label="時系列の図">
      {diagram.actors.map((actor, index) => (
        <div key={actor} className="tour-diagram-actor" style={{ gridColumn: index + 1, gridRow: 1 }}>
          {actor}
        </div>
      ))}
      {diagram.steps.map((step, index) => {
        const from = column(step.from);
        const to = column(step.to);
        const lit = focus.length === 0 || focus.includes(step.id);
        const classes = `tour-diagram-step${lit ? "" : " dim"}${focus.includes(step.id) ? " focus" : ""}`;
        const row = index + 2;
        if (from === to) {
          // 自分の中の動き。右端の列では左へ 1 列広げる（箱がはみ出さないように）
          const start = Math.min(from, count - 2);
          return (
            <div key={step.id} className={`${classes} note`} data-step={step.id} style={{ gridColumn: `${start + 1} / span 2`, gridRow: row }}>
              <span className="tour-diagram-no">{index + 1}</span>
              <span className="tour-diagram-text">{step.text}</span>
              {step.value !== "" && <code className="tour-diagram-value">{step.value}</code>}
            </div>
          );
        }
        const left = Math.min(from, to);
        const span = Math.abs(to - from) + 1;
        const arrow = to > from ? "right" : "left";
        return (
          <div key={step.id} className={`${classes} arrow ${arrow}`} data-step={step.id} style={{ gridColumn: `${left + 1} / span ${span}`, gridRow: row, ["--tour-diagram-inset" as string]: `calc(50% / ${span})` }}>
            <span className="tour-diagram-no">{index + 1}</span>
            <span className="tour-diagram-text">{step.text}</span>
            {step.value !== "" && <code className="tour-diagram-value">{step.value}</code>}
            <span className="tour-diagram-line" aria-hidden="true" />
          </div>
        );
      })}
    </div>
  );
}
