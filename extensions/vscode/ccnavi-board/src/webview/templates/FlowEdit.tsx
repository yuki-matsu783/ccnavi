/**
 * テンプレートの子のフロー 1 本の編集。フロー編集画面の図（`flow/Canvas.tsx`）と右の欄（`flow/Inspector.tsx`）をそのまま使い、
 * 部品箱でノードを足す。中身は `core/flow-doc.ts` の関数で作り直す（コピーを返す）。
 *
 * 画面が持つのは編集中のコピーだけ。形が正しいかは実行ファイルが読んで言う（編集欄の苦情に出る）。ここは判定しない。
 * 元に戻す・コピー・グループ化・ミニマップは持たない（フロー編集画面の機能。テンプレートの編集全体の元に戻すで戻る）。
 */
import { useState, type JSX } from "react";

import {
  addNode,
  connect,
  nextSpot,
  PALETTE,
  placeNodes,
  removeConnectionAt,
  removeNode,
  resizeGroup,
  TYPE_LABELS,
  type FlowDoc,
  type PaletteType,
} from "../../core/flow-doc.js";
import { Canvas, type Selection } from "../flow/Canvas.js";
import { Inspector } from "../flow/Inspector.js";

export interface FlowEditProps {
  readonly doc: FlowDoc;
  /** コピーを作り直したとき。欄に打った文字は `typing`（どの欄か）が付く */
  readonly onChange: (doc: FlowDoc, typing?: string) => void;
}

export function FlowEdit({ doc, onChange }: FlowEditProps): JSX.Element {
  const [selected, setSelected] = useState<Selection | undefined>(undefined);
  const [focus, setFocus] = useState<{ readonly ids: readonly string[] } | undefined>(undefined);

  const add = (type: PaletteType): void => {
    const added = addNode(doc, type, nextSpot(doc));
    onChange(added.doc);
    setSelected({ kind: "node", id: added.id });
    setFocus({ ids: [added.id] });
  };

  return (
    <div className="flow-body tpl-flow-edit" data-palette-folded="0" data-side-folded="0">
      <nav className="flow-palette" aria-label="部品箱">
        {PALETTE.map((type) => (
          <button key={type} type="button" className="action small palette-item" data-action="add-node" data-type={type} onClick={() => add(type)}>
            {TYPE_LABELS[type]}
          </button>
        ))}
      </nav>
      <Canvas
        doc={doc}
        readOnly={false}
        selected={selected}
        onSelect={setSelected}
        focus={focus}
        onPick={() => undefined}
        onMove={(moves) => {
          const next = placeNodes(doc, moves);
          if (next !== doc) {
            onChange(next);
          }
        }}
        onConnect={(from, fromPort, to, toPort) => onChange(connect(doc, from, fromPort, to, toPort))}
        onRemoveNode={(id) => {
          onChange(removeNode(doc, id));
          if ((selected?.kind === "node" && selected.id === id) || selected?.kind === "edge") {
            setSelected(undefined);
          }
        }}
        onRemoveEdge={(index) => {
          onChange(removeConnectionAt(doc, index));
          if (selected?.kind === "edge") {
            setSelected(undefined);
          }
        }}
        onResizeGroup={(id, size, position) => {
          const next = resizeGroup(doc, id, size, position);
          if (next !== doc) {
            onChange(next);
          }
        }}
      />
      <div className="flow-side">
        <Inspector doc={doc} selected={selected} readOnly={false} noGrouping onChange={onChange} onSelect={setSelected} />
      </div>
    </div>
  );
}
