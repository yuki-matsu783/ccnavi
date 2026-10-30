/**
 * 組み合わせ条件（`when:`）を図で見せて直す欄。ルールの行を開いたときに、パターンの欄の代わりに出る。
 *
 * 図は React Flow で、最上位のノードを左に置いて右へ伸ばす（置き場所は `layoutCond` が組み合わせ条件の形だけで決める）。
 * 点を押すと選ばれ、下の操作の欄がその点に効く。操作は、子を足す・消す・種別を替える（all ⇄ any、
 * glob ⇄ regex）・個別条件の文字を直す・all / any / not で包む・not を外す。直すたびに組み合わせ条件ぜんたいを `onChange` で返し、
 * 書き戻し（`rules-doc.ts`）が `when:` の YAML に直す。保存と保存前の `--lint` は他の欄と同じ流れ。
 *
 * 誤り（空の演算子、中が空の not、空の個別条件、読めなかったノード、深さとノード数の上限）は図の下に並べ、
 * 押すとその点を選ぶ。止めるのは保存前の `--lint` で、ここは見せるだけ。
 */
import { useEffect, useMemo, useState, type JSX } from "react";
import { Background, Controls, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps, type NodeTypes } from "@xyflow/react";

import { maximizeClass, MaximizeButton, RefitOnChange, useMaximize } from "../shared/Maximize.js";
import { FLOW_ARIA_LABELS } from "../shared/flow-aria.js";
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
  wrapGroup,
  wrapNot,
  type CondNode,
  type CondPath,
  type CondPlace,
} from "../../core/rules-when.js";

interface CondData extends Record<string, unknown> {
  readonly place: CondPlace;
  readonly selected: boolean;
  readonly broken: boolean;
}

type CondFlowNode = Node<CondData, "cond">;

const OP_TITLES: Readonly<Record<string, string>> = {
  all: "all: 中のすべてにヒットすればヒットします",
  any: "any: 中のどれかにヒットすればヒットします",
  not: "not: 中にヒットしなければヒットします",
  glob: "glob: 対象をこの glob で照合します",
  regex: "regex: 対象をこの正規表現で照合します",
  invalid: "読み込めない条件",
  empty: "空。押して中に入れるものを選びます",
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
    <div className={classes.join(" ")} data-op={place.op} data-key={place.key} title={OP_TITLES[place.op]}>
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
  /** 外から選ばせる点（`pathKey`）。案内が見本の点を指すのに使う。変わったときだけ選び直す */
  readonly pick?: string;
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

export function When({ tree, onChange, pick }: WhenProps): JSX.Element {
  const [picked, setPicked] = useState(pick ?? "r");
  useEffect(() => {
    if (pick !== undefined) {
      setPicked(pick);
    }
  }, [pick]);
  const [maximized, toggleMaximize, maxBox] = useMaximize();
  const layout = useMemo(() => layoutCond(tree), [tree]);
  const problems = useMemo(() => condProblems(tree), [tree]);
  const brokenKeys = useMemo(() => new Set(problems.map((p) => pathKey(p.path))), [problems]);
  // 選んだ点が無くなったら（消した、種別が変わって子が減った）最上位のノードに戻す
  const target = targetOf(tree, picked) ?? { path: [], node: tree };
  const selectedKey = pathKey(target.path);

  const nodes = useMemo<CondFlowNode[]>(
    () =>
      layout.places.map((place) => ({
        id: place.key,
        type: "cond",
        position: { x: place.x, y: place.y },
        data: { place, selected: place.key === selectedKey, broken: brokenKeys.has(place.key) },
        draggable: false,
      })),
    [layout, selectedKey, brokenKeys],
  );
  const edges = useMemo<Edge[]>(() => layout.edges.map((edge) => ({ id: edge.id, source: edge.from, target: edge.to, ariaLabel: "条件の線" })), [layout]);

  const edit = (next: CondNode, select?: CondPath): void => {
    onChange(next);
    if (select !== undefined) {
      setPicked(pathKey(select));
    }
  };

  return (
    <div className="cond" data-nodes={layout.places.length}>
      <div ref={maxBox} data-tour="when-graph" className={`cond-canvas ${maximizeClass(maximized)}`}>
        <ReactFlow<CondFlowNode, Edge>
          // 組み合わせ条件の形が変わったら置き直して全体を映す（点の数で見分ける）
          key={layout.places.length}
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesConnectable={false}
          nodesDraggable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          // 押せるのは onNodeClick があるときだけ。無いと React Flow は点を pointer-events: none で描き、押しても届かない
          onNodeClick={(_event, node) => setPicked(node.id)}
          fitView
          fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
          ariaLabelConfig={FLOW_ARIA_LABELS}
          minZoom={0.2}
          maxZoom={1.5}
          deleteKeyCode={null}
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls showInteractive={false} />
          <RefitOnChange value={maximized} />
        </ReactFlow>
        <MaximizeButton maximized={maximized} onToggle={toggleMaximize} />
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
      <span className="dim">子を足す</span>
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
    return <div className="cond-inspector" data-tour="when-inspector" data-key={pathKey(path)}>{adders(parent, 0)}</div>;
  }

  return (
    <div className="cond-inspector" data-tour="when-inspector" data-key={pathKey(path)} data-op={node.op}>
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
            <option value="all">all（すべてにヒットする）</option>
            <option value="any">any（どれかにヒットする）</option>
          </select>
          {adders(path, node.children.length)}
        </span>
      )}
      {node.op === "not" && (
        <span className="inline">
          <span className="dim">not（中にヒットしなければヒットする）</span>
          {node.child === null && adders(path, 0)}
          {node.child !== null && (
            <button type="button" className="action small" data-op-action="unwrap" onClick={() => edit(unwrapNot(tree, path), path)}>
              not を解除
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
        <button type="button" className="action small" data-op-action="wrap-all" title="この条件を all で包みます。隣に子を足して組み合わせにできます" onClick={() => edit(wrapGroup(tree, path, "all"), path)}>
          all で包む
        </button>
        <button type="button" className="action small" data-op-action="wrap-any" title="この条件を any で包みます。隣に子を足して組み合わせにできます" onClick={() => edit(wrapGroup(tree, path, "any"), path)}>
          any で包む
        </button>
        <button type="button" className="action small" data-op-action="wrap" onClick={() => edit(wrapNot(tree, path), path)}>
          not で包む
        </button>
        <button
          type="button"
          className="action small"
          data-op-action="remove"
          disabled={isRoot}
          title={isRoot ? "最上位の条件は削除できません（all / any / not で包んでから中を直すか、個別条件なら文字を直してください）" : "この条件を削除"}
          onClick={() => edit(removeAt(tree, path), path.slice(0, -1))}
        >
          この条件を削除
        </button>
      </span>
    </div>
  );
}
