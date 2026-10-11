/**
 * 条件の木（`when:`）を図で見せて直す欄。ルールの行を開いたときに、パターンの欄の代わりに出る。
 *
 * 図は React Flow で、根を左に置いて右へ伸ばす（置き場所は `layoutCond` が木の形だけで決める）。
 * 点を押すと選ばれ、下の操作の欄がその点に効く。操作は、子を足す・消す・種別を替える（all ⇄ any、
 * glob ⇄ regex）・葉の文字を直す・not で包む・not を外す。直すたびに木ぜんたいを `onChange` で返し、
 * 書き戻し（`rules-doc.ts`）が `when:` の YAML に直す。保存と保存前の `--lint` は他の欄と同じ流れ。
 *
 * 誤り（空のまとめ、中が空の not、空の葉、読めなかったノード、深さとノード数の上限）は図の下に並べ、
 * 押すとその点を選ぶ。止めるのは保存前の `--lint` で、ここは見せるだけ。
 */
import { useMemo, useState, type JSX } from "react";
import { Background, Controls, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps, type NodeTypes } from "@xyflow/react";

import {
  addChild,
  changeOp,
  condProblems,
  group,
  layoutCond,
  leaf,
  nodeAt,
  not,
  pathKey,
  pathOfKey,
  removeAt,
  replaceAt,
  setPattern,
  unwrapNot,
  wrapNot,
  type CondNode,
  type CondPath,
  type CondPlace,
} from "../../core/rules-when.js";

interface CondData extends Record<string, unknown> {
  readonly place: CondPlace;
  readonly selected: boolean;
  readonly broken: boolean;
  readonly pick: (key: string) => void;
}

type CondFlowNode = Node<CondData, "cond">;

const OP_TITLES: Readonly<Record<string, string>> = {
  all: "all: 中のすべてに当たれば当たる",
  any: "any: 中のどれかに当たれば当たる",
  not: "not: 中に当たらなければ当たる",
  glob: "glob: 対象をこの glob で当てる",
  regex: "regex: 対象をこの正規表現で当てる",
  invalid: "読めないノード",
  empty: "空。押して中に入れるものを選ぶ",
};

function CondNodeView({ data }: NodeProps<CondFlowNode>): JSX.Element {
  const { place } = data;
  const classes = ["cond-node"];
  if (data.selected) {
    classes.push("selected");
  }
  if (data.broken) {
    classes.push("broken");
  }
  const leafy = place.op === "glob" || place.op === "regex" || place.op === "invalid";
  return (
    <div className={classes.join(" ")} data-op={place.op} data-key={place.key} title={OP_TITLES[place.op]} onClick={() => data.pick(place.key)}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <Handle type="source" position={Position.Right} isConnectable={false} />
      <span className="cond-op">{place.op === "empty" ? "空" : place.op}</span>
      <span className={leafy ? "cond-label mono" : "cond-label"}>{place.label === "" && leafy ? "（空）" : place.label}</span>
    </div>
  );
}

const NODE_TYPES: NodeTypes = { cond: CondNodeView };

export interface WhenProps {
  readonly tree: CondNode;
  readonly onChange: (next: CondNode) => void;
}

/** 選んだ点の親（`empty` の点は親の not に効かせる）と、その点そのもの */
function targetOf(tree: CondNode, key: string): { readonly path: CondPath; readonly node: CondNode | null } | undefined {
  const path = pathOfKey(key);
  if (path === undefined) {
    return undefined;
  }
  const node = nodeAt(tree, path);
  if (node !== undefined) {
    return { path, node };
  }
  // 中が空の not の穴（親の not の 0 番）
  const parent = path.length === 0 ? undefined : nodeAt(tree, path.slice(0, -1));
  if (parent?.op === "not" && parent.child === null && path[path.length - 1] === 0) {
    return { path, node: null };
  }
  return undefined;
}

export function When({ tree, onChange }: WhenProps): JSX.Element {
  const [picked, setPicked] = useState("r");
  const layout = useMemo(() => layoutCond(tree), [tree]);
  const problems = useMemo(() => condProblems(tree), [tree]);
  const brokenKeys = useMemo(() => new Set(problems.map((p) => pathKey(p.path))), [problems]);
  // 選んだ点が無くなったら（消した、種別が変わって子が減った）根に戻す
  const target = targetOf(tree, picked) ?? { path: [], node: tree };
  const selectedKey = pathKey(target.path);

  const nodes = useMemo<CondFlowNode[]>(
    () =>
      layout.places.map((place) => ({
        id: place.key,
        type: "cond",
        position: { x: place.x, y: place.y },
        data: { place, selected: place.key === selectedKey, broken: brokenKeys.has(place.key), pick: setPicked },
        draggable: false,
      })),
    [layout, selectedKey, brokenKeys],
  );
  const edges = useMemo<Edge[]>(() => layout.edges.map((edge) => ({ id: edge.id, source: edge.from, target: edge.to })), [layout]);

  const edit = (next: CondNode, select?: CondPath): void => {
    onChange(next);
    if (select !== undefined) {
      setPicked(pathKey(select));
    }
  };

  return (
    <div className="cond" data-nodes={layout.places.length}>
      <div className="cond-canvas">
        <ReactFlow<CondFlowNode, Edge>
          // 木の形が変わったら置き直して全体を映す（点の数で見分ける）
          key={layout.places.length}
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesConnectable={false}
          nodesDraggable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          fitView
          fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
          ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "拡大", "controls.zoomOut.ariaLabel": "縮小", "controls.fitView.ariaLabel": "全体を表示" }}
          minZoom={0.2}
          maxZoom={1.5}
          deleteKeyCode={null}
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <Inspector tree={tree} path={target.path} node={target.node} edit={edit} />
      {problems.length > 0 && (
        <ul className="cond-problems">
          {problems.map((problem, i) => (
            <li key={i}>
              <button type="button" className="action small" data-key={pathKey(problem.path)} onClick={() => setPicked(pathKey(problem.path))}>
                {problem.message}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 足せるもの。押すと新しいノードを作る */
const ADDABLE: readonly { readonly name: string; readonly make: () => CondNode }[] = [
  { name: "glob", make: () => leaf("glob") },
  { name: "regex", make: () => leaf("regex") },
  { name: "all", make: () => group("all") },
  { name: "any", make: () => group("any") },
  { name: "not", make: () => not(null) },
];

function Inspector({
  tree,
  path,
  node,
  edit,
}: {
  readonly tree: CondNode;
  readonly path: CondPath;
  readonly node: CondNode | null;
  readonly edit: (next: CondNode, select?: CondPath) => void;
}): JSX.Element {
  const isRoot = path.length === 0;
  const adders = (into: CondPath, index: number): JSX.Element => (
    <span className="cond-add">
      <span className="dim">子を足す:</span>
      {ADDABLE.map(({ name, make }) => (
        <button key={name} type="button" className="action small" data-add={name} onClick={() => edit(addChild(tree, into, make()), [...into, index])}>
          ＋ {name}
        </button>
      ))}
    </span>
  );

  if (node === null) {
    // 中が空の not の穴
    const parent = path.slice(0, -1);
    return <div className="cond-inspector" data-key={pathKey(path)}>{adders(parent, 0)}</div>;
  }

  return (
    <div className="cond-inspector" data-key={pathKey(path)} data-op={node.op}>
      {(node.op === "glob" || node.op === "regex") && (
        <span className="inline">
          <select className="f-cond-op" value={node.op} onChange={(event) => edit(changeOp(tree, path, event.target.value as "glob" | "regex"))}>
            <option value="glob">glob</option>
            <option value="regex">regex</option>
          </select>
          <input
            type="text"
            className="f-cond-pattern pattern"
            spellCheck={false}
            placeholder={node.op === "glob" ? "{root}/.claude/worktrees/**" : "^{root}[\\\\/]"}
            value={node.pattern}
            onChange={(event) => edit(setPattern(tree, path, event.target.value))}
          />
        </span>
      )}
      {(node.op === "all" || node.op === "any") && (
        <span className="inline">
          <select className="f-cond-op" value={node.op} onChange={(event) => edit(changeOp(tree, path, event.target.value as "all" | "any"))}>
            <option value="all">all（すべてに当たる）</option>
            <option value="any">any（どれかに当たる）</option>
          </select>
          {adders(path, node.children.length)}
        </span>
      )}
      {node.op === "not" && (
        <span className="inline">
          <span className="dim">not（中に当たらなければ当たる）</span>
          {node.child === null && adders(path, 0)}
          {node.child !== null && (
            <button type="button" className="action small" data-op-action="unwrap" onClick={() => edit(unwrapNot(tree, path), path)}>
              not を外す
            </button>
          )}
        </span>
      )}
      {node.op === "invalid" && (
        <span className="inline">
          <span className="cond-invalid">
            {node.reason}: <code>{node.text}</code>
          </span>
          <button type="button" className="action small" data-op-action="replace" onClick={() => edit(replaceAt(tree, path, leaf("glob")), path)}>
            glob に置き換える
          </button>
        </span>
      )}
      <span className="buttons">
        <button type="button" className="action small" data-op-action="wrap" onClick={() => edit(wrapNot(tree, path), path)}>
          not で包む
        </button>
        <button
          type="button"
          className="action small"
          data-op-action="remove"
          disabled={isRoot}
          title={isRoot ? "根は消せません（not で包むか、種別を替えてください）" : "このノードを消す"}
          onClick={() => edit(removeAt(tree, path), path.slice(0, -1))}
        >
          ノードを削除
        </button>
      </span>
    </div>
  );
}
