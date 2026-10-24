/**
 * 時系列の図（案内の中で「誰が誰に何をするか」を見せる）。登場者を左から並べ、矢印を上から下へ時間順に並べる。
 * 矢印には、実際に入れる値や出る結果を等幅で添えられる（`value`）。値を変えると結果が変わる例は、図を分けて並べる。
 *
 * 絵は DOM と CSS だけで描く（画像にしない）ので、VS Code のテーマに従い、文字を直すのは YAML だけで済む。
 * `focus` に入れた矢印は光らせ、ほかは薄く出す（空なら全部を同じ濃さで出す）。
 * 矢印の向きは `from` から `to`。同じ登場者なら、その列の覚え書き（自分の中の動き）として箱で出す。
 */
import { Fragment, type CSSProperties, type JSX, type ReactNode } from "react";

import type { DiagramDef } from "../../core/tour-def.js";

/** 文の中の `…` を、コードの字で出す（バッククォートを生のまま見せない）。奇数個で閉じていなければ、そのまま出す */
export function inlineCode(text: string): ReactNode {
  const parts = text.split("`");
  if (parts.length < 3 || parts.length % 2 === 0) {
    return text;
  }
  return parts.map((part, index) => (index % 2 === 1 ? <code key={index} className="tour-code">{part}</code> : <Fragment key={index}>{part}</Fragment>));
}

/** 図の中の順番。吹き出しの「3 / 5」（手順の番号）と見分けがつくよう、丸数字で出す（21 以上は数字のまま） */
function circled(order: number): string {
  return order >= 1 && order <= 20 ? String.fromCodePoint(0x2460 + order - 1) : String(order);
}

export function SequenceDiagram({ name, diagram, focus }: { readonly name: string; readonly diagram: DiagramDef; readonly focus: readonly string[] }): JSX.Element {
  const count = diagram.actors.length;
  const column = (actor: string): number => diagram.actors.indexOf(actor);
  const style = { gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` } as CSSProperties;
  return (
    <div className="tour-diagram" data-diagram={name} style={style} role="group" aria-label="時系列の図">
      {diagram.actors.map((actor, index) => (
        <div key={actor} className="tour-diagram-actor" style={{ gridColumn: index + 1, gridRow: 1 }}>
          {actor}
        </div>
      ))}
      {/* 縦の線（登場者の時間軸）。列ごとに、矢印の行すべてにまたがる要素で引く（列の隙間や余白に左右されず、箱の中央に出る） */}
      {diagram.actors.map((actor, index) => (
        <div key={`life-${actor}`} className="tour-diagram-life" aria-hidden="true" style={{ gridColumn: index + 1, gridRow: `2 / span ${diagram.steps.length}` }} />
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
              <span className="tour-diagram-no">{circled(index + 1)}</span>
              <span className="tour-diagram-text">{inlineCode(step.text)}</span>
              {step.value !== "" && <code className="tour-diagram-value">{step.value}</code>}
            </div>
          );
        }
        const left = Math.min(from, to);
        const span = Math.abs(to - from) + 1;
        const arrow = to > from ? "right" : "left";
        return (
          <div key={step.id} className={`${classes} arrow ${arrow}`} data-step={step.id} style={{ gridColumn: `${left + 1} / span ${span}`, gridRow: row, ["--tour-diagram-inset" as string]: `calc(50% / ${span})` }}>
            <span className="tour-diagram-no">{circled(index + 1)}</span>
            <span className="tour-diagram-text">{inlineCode(step.text)}</span>
            {step.value !== "" && <code className="tour-diagram-value">{step.value}</code>}
            <span className="tour-diagram-line" aria-hidden="true" />
          </div>
        );
      })}
    </div>
  );
}
