/**
 * 右の欄。選んだノードか線の中身を直す。何も選んでいなければフロー自体の名前と説明。
 *
 * 直すのは `core/flow-doc.ts` の関数で作ったコピーで、**触った欄以外は元のまま**（知らない欄を落とさない）。
 * 画面が欄を持たない種類は、名前だけ直せて、`data` は読むだけ（ファイルと同じ YAML の形で見せる）。
 * グループは名前だけ直せて、解く（中のノードは残して枠だけ消す）か消す（同じく中のノードは残す）。
 * サブエージェントの種類とスキルの名前は、実行ファイルが挙げた候補（`candidates`）から選べる。打って決めても
 * よく（ユーザ・プラグインのものは候補に無い）、候補のどれか（組み込み・プロジェクト）か、候補に無いかをつける。
 * 欄に打った字は、どの欄かをつけて返す（呼び手が同じ欄への打ち込みを元に戻す 1 件にまとめる）。フォーカスが外れたら `onSeal`。
 */
import type { JSX } from "react";

import type { LintFlowCandidate, LintFlowCandidates } from "../../core/lintmodel.js";

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
  /**
   * 直したコピーを返す。欄に打った文字は `typing`（どの欄か）をつける。呼び手はそれを手がかりに、
   * 同じ欄に続けて打ったものを元に戻す 1 件にまとめる
   */
  readonly onChange: (doc: FlowDoc, typing?: string) => void;
  /** 欄からフォーカスが外れた。打ち込みのまとまりを区切る */
  readonly onSeal?: () => void;
  readonly onSelect: (selection: Selection | undefined) => void;
  /** 実行ファイルが挙げた、選べるサブエージェントとスキルの名前。無ければ候補を出さない */
  readonly candidates?: LintFlowCandidates;
}

/** 候補の出どころの呼び名 */
export function sourceLabel(source: string): string {
  return source === "builtin" ? "組み込み" : source === "project" ? "プロジェクト（.claude/ の下）" : source;
}

/** 名前が候補のどれか。無ければ undefined */
function candidateOf(list: readonly LintFlowCandidate[], name: string): LintFlowCandidate | undefined {
  return list.find((c) => c.name === name);
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function Inspector({ doc, selected, readOnly, onChange, onSeal, onSelect, candidates }: InspectorProps): JSX.Element {
  if (selected?.kind === "node") {
    const node = doc.nodes.find((n) => n.id === selected.id);
    if (node !== undefined) {
      return <NodeFields doc={doc} node={node} readOnly={readOnly} onChange={onChange} onSeal={onSeal} onSelect={onSelect} candidates={candidates} />;
    }
  }
  if (selected?.kind === "edge") {
    const connection = connectionsOf(doc)[selected.index];
    if (connection !== undefined) {
      const name = (id: string): string => {
        const node = doc.nodes.find((n) => n.id === id);
        return node === undefined ? `${id}（見つかりません）` : nodeName(node) || id;
      };
      return (
        <aside className="inspector" id="inspector" data-selected="edge" onBlur={onSeal}>
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
              placeholder="空なら出口の名前を条件として使います"
              disabled={readOnly}
              onChange={(event) => onChange(setConditionAt(doc, selected.index, event.target.value), `edge:${selected.index}:condition`)}
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
    <aside className="inspector" id="inspector" data-selected="flow" onBlur={onSeal}>
      <h2>フロー</h2>
      <label className="field">
        <span title="name">名前</span>
        <input type="text" className="f-flow-name" value={str(doc.name)} disabled={readOnly} onChange={(event) => onChange(setMeta(doc, { name: event.target.value }), "flow:name")} />
      </label>
      <label className="field">
        <span title="description">説明</span>
        <textarea className="f-flow-description" rows={3} value={str(doc.description)} disabled={readOnly} onChange={(event) => onChange(setMeta(doc, { description: event.target.value }), "flow:description")} />
      </label>
      <p className="hint">ノードを押すと、ここに欄が出ます。ノードの右の点から左の点へドラッグすると、線でつながります。線を押すと条件を書けます。ノードや線にポインタを載せると × が出て、押すと消せます。Shift を押しながらノードを選ぶと、「グループ化」で枠にまとめられます。Ctrl+Z で元に戻せます。選んだノードは Ctrl+C でコピー、Ctrl+V で貼り付け、Ctrl+D で複製できます。</p>
    </aside>
  );
}

function NodeFields({ doc, node, readOnly, onChange, onSeal, onSelect, candidates }: { readonly doc: FlowDoc; readonly node: FlowNode; readonly readOnly: boolean; readonly onChange: (doc: FlowDoc, typing?: string) => void; readonly onSeal?: () => void; readonly onSelect: (selection: Selection | undefined) => void; readonly candidates?: LintFlowCandidates }): JSX.Element {
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
        <textarea className={`f-${key}`} rows={5} value={dataText(node, key)} placeholder={options.placeholder} disabled={readOnly} onChange={(event) => onChange(patchData(doc, node.id, { [key]: event.target.value }), `node:${node.id}:${key}`)} />
      ) : (
        <input type="text" className={`f-${key}`} value={dataText(node, key)} placeholder={options.placeholder} disabled={readOnly} onChange={(event) => onChange(patchData(doc, node.id, { [key]: event.target.value }), `node:${node.id}:${key}`)} />
      )}
    </label>
  );
  /** 候補から選べる欄。`list` は datalist の id。候補が無ければふつうの欄 */
  const pickable = (key: string, label: string, list: readonly LintFlowCandidate[] | undefined, listId: string, placeholder: string): JSX.Element => {
    const value = dataText(node, key);
    const found = list === undefined ? undefined : candidateOf(list, value);
    return (
      <label className="field">
        <span title={`data.${key}`}>{label}</span>
        <input
          type="text"
          className={`f-${key}`}
          value={value}
          placeholder={placeholder}
          disabled={readOnly}
          list={list === undefined ? undefined : listId}
          onChange={(event) => onChange(patchData(doc, node.id, { [key]: event.target.value }), `node:${node.id}:${key}`)}
        />
        {list !== undefined && (
          <datalist id={listId}>
            {list.map((c) => (
              <option key={`${c.source}:${c.name}`} value={c.name} label={sourceLabel(c.source)} />
            ))}
          </datalist>
        )}
        {list !== undefined && value !== "" && (
          <span className={found === undefined ? "candidate-source missing" : "candidate-source"} data-source={found?.source ?? "missing"}>
            {found === undefined ? "候補にありません（ユーザやプラグインのものなら気にしなくてかまいません）" : sourceLabel(found.source)}
          </span>
        )}
      </label>
    );
  };
  return (
    <aside className="inspector" id="inspector" data-selected="node" data-type={type} onBlur={onSeal}>
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
        <input type="text" className="f-name" value={nodeName(node)} disabled={readOnly} onChange={(event) => onChange(renameNode(doc, node.id, event.target.value), `node:${node.id}:name`)} />
      </label>
      {(type === "start" || type === "end") && text("label", "説明")}
      {type === "prompt" && text("prompt", "プロンプト", { area: true })}
      {type === "subAgent" && (
        <>
          {text("description", "何を任せるか")}
          {pickable("builtInType", "サブエージェントの種類", candidates?.agents, "flow-agent-candidates", "general-purpose / Explore / Plan など")}
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
          {pickable("name", "スキルの名前", candidates?.skills, "flow-skill-candidates", "")}
          {text("description", "説明")}
        </>
      )}
      {known && branchKey(type) !== undefined && <Branches doc={doc} node={node} readOnly={readOnly} onChange={onChange} />}
      {host !== undefined && <p className="dim small">グループ「{nodeName(host) || host.id}」の中にあります。枠の外へドラッグすると、グループから出ます。</p>}
      {group && (
        <>
          <p className="hint">図の上の枠です。手順には入りません（線はつなぎません）。中のノードは {members} 個です。枠の中へドラッグしたノードは枠に入り、外へドラッグすると出ます。枠を選ぶと、縁をドラッグして大きさを変えられます。</p>
          <div className="buttons">
            <button
              type="button"
              className="action"
              data-action="ungroup"
              disabled={readOnly}
              title="枠だけ消して、中のノードはその場に残します"
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
          <p className="hint">この画面に入力欄が無い種類です。名前と位置だけを変えられ、中身（data）は保存してもそのまま残ります。</p>
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
function Branches({ doc, node, readOnly, onChange }: { readonly doc: FlowDoc; readonly node: FlowNode; readonly readOnly: boolean; readonly onChange: (doc: FlowDoc, typing?: string) => void }): JSX.Element {
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
            onChange={(event) => onChange(patchBranch(doc, node.id, index, { label: event.target.value }), `node:${node.id}:branch:${index}:label`)}
          />
          <input
            type="text"
            className={`f-branch-${second}`}
            value={str(item[second])}
            placeholder={options ? "説明" : "条件"}
            disabled={readOnly}
            onChange={(event) => onChange(patchBranch(doc, node.id, index, { [second]: event.target.value }), `node:${node.id}:branch:${index}:${second}`)}
          />
          {!fixed && (
            <button type="button" className="action small" data-action="remove-branch" disabled={readOnly || items.length <= 1} title="この出口と、そこから出る線を消します" onClick={() => onChange(removeBranch(doc, node.id, index))}>
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
