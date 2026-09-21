/**
 * フェーズの種類の関係を図で見せる。点が種類、線が `requires` と `overlap`。
 *
 * **線に矢印は付けない。** `requires` は「一緒に置くべき」で、順序ではない（`phases-graph.ts` の頭）。
 * 図が判定をしないのも同じところに書いてある。ここは `graphOf` が組んだものを描くだけで、
 * 何が正しいかは言わない。
 *
 * **編集はしない。** 点を摘まんで動かせるが、動かした先は画面の控え（`state.ts` の spots）に入るだけで、
 * `phases.yml` には書かない。人が持つ設定に座標は入れない。関係そのものを直すのは一覧のほう。
 *
 * 点を押すと一覧へ戻り、その種類の行が開く（`onPick`）。
 *
 * 見た目は `Graph.css`。React Flow の CSS は `--xy-*` の変数で出来ているので、そこを
 * `--vscode-*` で上書きする。直書きの色を打ち消す `!important` は要らない。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
import { Background, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps, type NodeTypes } from "@xyflow/react";

import type { PhasesGraph } from "../../core/phases-graph.js";
import { KIND_LABELS, REVIEW_LABELS } from "../../core/phases-view.js";
import { loadSpots, saveSpots, type Spots } from "./state.js";

/** 点 1 つが持つ中身。React Flow の `data` に載る */
interface PhaseData extends Record<string, unknown> {
  readonly title: string;
  readonly kind: string;
  readonly review: string;
}

type PhaseNode = Node<PhaseData, "phase">;

/**
 * 点の見た目。`Handle` は線の端を留めるためだけに置き、目には見せない（`Graph.css`）。
 * 左右に 1 つずつ置くのは React Flow が端を要るからで、向きの意味は無い。
 */
function PhaseNodeView({ id, data }: NodeProps<PhaseNode>): JSX.Element {
  return (
    <div className="phase-node" data-kind={data.kind} title={`${KIND_LABELS[data.kind as "work"] ?? data.kind}\n${REVIEW_LABELS[data.review as "mr"] ?? data.review}`}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span className="phase-node-id">{id}</span>
      {data.title !== "" && <span className="phase-node-title">{data.title}</span>}
      <span className="phase-node-tags">
        <span className="tag" data-kind={data.kind}>
          {data.kind}
        </span>
        <span className="tag" data-review={data.review}>
          {data.review}
        </span>
      </span>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

// 描くたびに作り直すと React Flow が「点の種類が変わった」と言って組み直す。1 度だけ作る
const NODE_TYPES: NodeTypes = { phase: PhaseNodeView };

function nodesOf(graph: PhasesGraph, spots: Spots): PhaseNode[] {
  return graph.nodes.map((node) => {
    const spot = spots[node.id];
    return {
      id: node.id,
      type: "phase" as const,
      position: { x: spot?.x ?? node.x, y: spot?.y ?? node.y },
      data: { title: node.title, kind: node.kind, review: node.review },
    };
  });
}

function edgesOf(graph: PhasesGraph): Edge[] {
  return graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.a,
    target: edge.b,
    type: "straight" as const,
    className: `rel-${edge.relation}`,
    // 向きが無いので、端の印は付けない
    label: edge.relation === "overlap" ? "並行" : "同席",
  }));
}

export function Graph({ graph, onPick }: { readonly graph: PhasesGraph; readonly onPick: (id: string) => void }): JSX.Element {
  const [spots, setSpots] = useState<Spots>(() => loadSpots());
  const nodes = useMemo(() => nodesOf(graph, spots), [graph, spots]);
  const edges = useMemo(() => edgesOf(graph), [graph]);

  // 図に出なくなった種類の控えは落とす（id を打ち替えるたびに溜まるため）
  const known = useRef<string>("");
  useEffect(() => {
    const ids = graph.nodes.map((node) => node.id).join("\u0000");
    if (known.current === ids) {
      return;
    }
    known.current = ids;
    setSpots((now) => {
      const next: Spots = {};
      for (const node of graph.nodes) {
        if (now[node.id] !== undefined) {
          next[node.id] = now[node.id];
        }
      }
      if (Object.keys(next).length === Object.keys(now).length) {
        return now;
      }
      saveSpots(next);
      return next;
    });
  }, [graph]);

  const onDragStop = useCallback((_event: unknown, node: PhaseNode) => {
    setSpots((now) => {
      const next = { ...now, [node.id]: { x: Math.round(node.position.x), y: Math.round(node.position.y) } };
      saveSpots(next);
      return next;
    });
  }, []);

  if (graph.nodes.length === 0) {
    return <p className="empty">図に出せる種類が無い（id を入れると出る）。</p>;
  }
  return (
    <div className="graph" id="phase-graph" data-nodes={graph.nodes.length} data-edges={graph.edges.length}>
      <ReactFlow<PhaseNode>
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeDragStop={onDragStop}
        onNodeClick={(_event, node) => onPick(node.id)}
        nodesConnectable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        fitView
        minZoom={0.3}
        maxZoom={1.6}
        // 図は読むだけなので、消す・繋ぐの鍵は受けない
        deleteKeyCode={null}
      >
        <Background />
      </ReactFlow>
    </div>
  );
}
