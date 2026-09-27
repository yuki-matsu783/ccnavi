/**
 * 右の欄。選んだノードか線の中身を直す。何も選んでいなければフロー自体の名前と説明。
 *
 * 直すのは `core/flow-doc.ts` の関数で作った写しで、**触った欄以外は元のまま**（知らない欄を落とさない）。
 * 画面が欄を持たない種類は、名前だけ直せて、`data` は読むだけ（ファイルと同じ YAML の形で見せる）。
 * グループは名前だけ直せて、解く（中のノードは残して枠だけ消す）か消す（同じく中のノードは残す）。
 */
import type { JSX } from "react";

import {
  addBranch,
  branchItems,
  branchKey,
  connectionFrom,
  connectionFromPort,
  connectionLabel,
  connectionsOf,
  connectionTo,
  dataText,
  groupOf,
  isEditableType,
  isGroup,
  nodeData,
  nodeName,
  nodeType,
  patchBranch,
  patchData,
  removeBranch,
  removeConnectionAt,
  removeNode,
  renameNode,
  setConditionAt,
  setMeta,
  TYPE_LABELS,
  ungroup,
  yamlText,
  type FlowDoc,
  type FlowNode,
} from "../../core/flow-doc.js";
import type { Selection } from "./Canvas.js";
import { badgeOf } from "./text.js";

export interface InspectorProps {
  readonly doc: FlowDoc;
  readonly selected: Selection | undefined;
  readonly readOnly: boolean;
  readonly onChange: (doc: FlowDoc) => void;
  readonly onSelect: (selection: Selection | undefined) => void;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function Inspector({ doc, selected, readOnly, onChange, onSelect }: InspectorProps): JSX.Element {
  if (selected?.kind === "node") {
    const node = doc.nodes.find((n) => n.id === selected.id);
    if (node !== undefined) {
      return <NodeFields doc={doc} node={node} readOnly={readOnly} onChange={onChange} onSelect={onSelect} />;
    }
  }
  if (selected?.kind === "edge") {
    const connection = connectionsOf(doc)[selected.index];
    if (connection !== undefined) {
      const name = (id: string): string => {
        const node = doc.nodes.find((n) => n.id === id);
        return node === undefined ? `${id}（無い）` : nodeName(node) || id;
      };
      return (
        <aside className="inspector" id="inspector" data-selected="edge">
          <h2>線</h2>
          <p className="mono small">
            {name(connectionFrom(connection))} → {name(connectionTo(connection))}
          </p>
          <p className="dim small">出口: {connectionFromPort(connection)}{connectionLabel(doc, connection) !== "" ? `（${connectionLabel(doc, connection)}）` : ""}</p>
          <label className="field">
            <span title="condition">条件</span>
            <input
              type="text"
              className="f-condition"
              value={str(connection.condition)}
              placeholder="空なら出口の名前で読む"
              disabled={readOnly}
              onChange={(event) => onChange(setConditionAt(doc, selected.index, event.target.value))}
            />
          </label>
          <div className="buttons">
            <button
              type="button"
              className="action"
              data-action="remove-edge"
              disabled={readOnly}
              onClick={() => {
                onChange(removeConnectionAt(doc, selected.index));
                onSelect(undefined);
              }}
            >
              線を消す
            </button>
          </div>
        </aside>
      );
    }
  }
  return (
    <aside className="inspector" id="inspector" data-selected="flow">
      <h2>フロー</h2>
      <label className="field">
        <span title="name">名前</span>
        <input type="text" className="f-flow-name" value={str(doc.name)} disabled={readOnly} onChange={(event) => onChange(setMeta(doc, { name: event.target.value }))} />
      </label>
      <label className="field">
        <span title="description">説明</span>
        <textarea className="f-flow-description" rows={3} value={str(doc.description)} disabled={readOnly} onChange={(event) => onChange(setMeta(doc, { description: event.target.value }))} />
      </label>
      <p className="hint">ノードを押すと、ここに欄が出る。ノードの右の点から左の点へ引くと線が繋がる。線を押すと条件を書ける。ノードや線に載せると出る × で消せる。Shift を押しながらノードを選ぶと、「グループ化」で枠にまとめられる。</p>
    </aside>
  );
}

function NodeFields({ doc, node, readOnly, onChange, onSelect }: { readonly doc: FlowDoc; readonly node: FlowNode; readonly readOnly: boolean; readonly onChange: (doc: FlowDoc) => void; readonly onSelect: (selection: Selection | undefined) => void }): JSX.Element {
  const type = nodeType(node);
  const known = isEditableType(type);
  const group = isGroup(node);
  const badge = badgeOf(type);
  const members = group ? doc.nodes.filter((n) => groupOf(doc, n)?.id === node.id).length : 0;
  const host = groupOf(doc, node);
  const text = (key: string, label: string, options: { readonly area?: boolean; readonly placeholder?: string } = {}): JSX.Element => (
    <label className="field">
      <span title={`data.${key}`}>{label}</span>
      {options.area === true ? (
        <textarea className={`f-${key}`} rows={5} value={dataText(node, key)} placeholder={options.placeholder} disabled={readOnly} onChange={(event) => onChange(patchData(doc, node.id, { [key]: event.target.value }))} />
      ) : (
        <input type="text" className={`f-${key}`} value={dataText(node, key)} placeholder={options.placeholder} disabled={readOnly} onChange={(event) => onChange(patchData(doc, node.id, { [key]: event.target.value }))} />
      )}
    </label>
  );
  return (
    <aside className="inspector" id="inspector" data-selected="node" data-type={type}>
      <h2>
        {TYPE_LABELS[type] ?? (type || "種類なし")} <span className="mono small dim">{node.id}</span>
      </h2>
      {badge !== undefined && (
        <p className={`flow-badge ${badge.kind}`} title={badge.title}>
          {badge.text}
        </p>
      )}
      {badge !== undefined && <p className="hint">{badge.title}</p>}
      <label className="field">
        <span title="name">名前</span>
        <input type="text" className="f-name" value={nodeName(node)} disabled={readOnly} onChange={(event) => onChange(renameNode(doc, node.id, event.target.value))} />
      </label>
      {(type === "start" || type === "end") && text("label", "説明")}
      {type === "prompt" && text("prompt", "プロンプト", { area: true })}
      {type === "subAgent" && (
        <>
          {text("description", "何を任せるか")}
          {text("builtInType", "サブエージェントの種類", { placeholder: "general-purpose / Explore / Plan など" })}
          {text("prompt", "プロンプト", { area: true })}
        </>
      )}
      {type === "askUserQuestion" && (
        <>
          {text("questionText", "問い", { area: true })}
          <label className="field check">
            <input
              type="checkbox"
              className="f-multiSelect"
              checked={nodeData(node).multiSelect === true}
              disabled={readOnly}
              onChange={(event) => onChange(patchData(doc, node.id, { multiSelect: event.target.checked }))}
            />
            <span title="data.multiSelect">複数選択（選択肢ごとに出口を分けない）</span>
          </label>
        </>
      )}
      {(type === "ifElse" || type === "switch") && text("evaluationTarget", "何で分けるか")}
      {type === "skill" && (
        <>
          {text("name", "スキルの名前")}
          {text("description", "説明")}
        </>
      )}
      {known && branchKey(type) !== undefined && <Branches doc={doc} node={node} readOnly={readOnly} onChange={onChange} />}
      {host !== undefined && <p className="dim small">グループ「{nodeName(host) || host.id}」の中。枠の外へ引くとグループから出る。</p>}
      {group && (
        <>
          <p className="hint">図の上の枠。手順には入らない（線は繋がない）。中のノードは {members} 個。枠の中へ引いたノードは枠に入り、外へ引くと出る。選ぶと縁を引いて大きさを変えられる。</p>
          <div className="buttons">
            <button
              type="button"
              className="action"
              data-action="ungroup"
              disabled={readOnly}
              title="枠だけ消して、中のノードはその場に残す"
              onClick={() => {
                onChange(ungroup(doc, node.id));
                onSelect(undefined);
              }}
            >
              グループを解く
            </button>
          </div>
        </>
      )}
      {!known && !group && (
        <>
          <p className="hint">この画面で欄を持たない種類。名前と位置だけ変えられ、中身（data）は保存してもそのまま残る。</p>
          <pre className="flow-raw">{yamlText(nodeData(node))}</pre>
        </>
      )}
      <div className="buttons">
        <button
          type="button"
          className="action"
          data-action="remove-node"
          disabled={readOnly}
          onClick={() => {
            onChange(removeNode(doc, node.id));
            onSelect(undefined);
          }}
        >
          {group ? "グループを消す（中のノードは残す）" : "ノードを消す"}
        </button>
      </div>
    </aside>
  );
}

/** 分岐の出口（`branches`）か選択肢（`options`）。1 件ずつが出口になる */
function Branches({ doc, node, readOnly, onChange }: { readonly doc: FlowDoc; readonly node: FlowNode; readonly readOnly: boolean; readonly onChange: (doc: FlowDoc) => void }): JSX.Element {
  const key = branchKey(nodeType(node));
  const options = key === "options";
  const items = branchItems(node);
  const second = options ? "description" : "condition";
  // if / else の出口は真と偽の 2 本で決まっている（増やすなら switch）
  const fixed = nodeType(node) === "ifElse";
  return (
    <fieldset className="branches">
      <legend title={`data.${key ?? ""}`}>{options ? "選択肢（1 件ずつが出口）" : "出口"}</legend>
      {items.map((item, index) => (
        <div key={index} className="branch" data-index={index}>
          <input
            type="text"
            className="f-branch-label"
            value={str(item.label)}
            placeholder="名前"
            disabled={readOnly}
            onChange={(event) => onChange(patchBranch(doc, node.id, index, { label: event.target.value }))}
          />
          <input
            type="text"
            className={`f-branch-${second}`}
            value={str(item[second])}
            placeholder={options ? "説明" : "条件"}
            disabled={readOnly}
            onChange={(event) => onChange(patchBranch(doc, node.id, index, { [second]: event.target.value }))}
          />
          {!fixed && (
            <button type="button" className="action small" data-action="remove-branch" disabled={readOnly || items.length <= 1} title="この出口と、そこから出る線を消す" onClick={() => onChange(removeBranch(doc, node.id, index))}>
              消す
            </button>
          )}
        </div>
      ))}
      {!fixed && <button
        type="button"
        className="action small"
        data-action="add-branch"
        disabled={readOnly}
        onClick={() => onChange(addBranch(doc, node.id, options ? { label: `選択肢 ${items.length + 1}`, description: "" } : { label: `出口 ${items.length + 1}`, condition: "" }))}
      >
        ＋ {options ? "選択肢" : "出口"}を足す
      </button>}
    </fieldset>
  );
}
