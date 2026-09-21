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

import { keepSpots, withSpot, type PhasesGraph, type Spots } from "../../core/phases-graph.js";
import { KIND_LABELS, REVIEW_LABELS } from "../../core/phases-view.js";
import { loadSpots, saveSpots } from "./state.js";

/** 点 1 つが持つ中身。React Flow の `data` に載る */
interface PhaseData extends Record<string, unknown> {
  readonly title: string;
  readonly kind: string;
  readonly review: string;
}

type PhaseNode = Node<PhaseData, "phase">;

/**
 * 点の見た目。`Handle` は線の端を留めるためだけに置き、目には見せない（`Graph.css`）。
 * React Flow が端を要るから置くのであって、**上下左右に意味は無い**。
 *
 * 端を 4 つ置くのは、**`requires` と `overlap` を別の辺に留めるため**。同じ 2 つの種類が
 * 両方の関係を持つことがあり（このリポジトリの設定の `acceptance` と `implement` がそれで、
 * 雛形も同じ組）、同じ端どうしを結ぶと 2 本がぴったり重なって、破線が実線の下に隠れる。
 * `requires` は左右、`overlap` は上下に留める。
 */
function PhaseNodeView({ id, data }: NodeProps<PhaseNode>): JSX.Element {
  return (
    <div className="phase-node" data-kind={data.kind} title={`${KIND_LABELS[data.kind as "work"] ?? data.kind}\n${REVIEW_LABELS[data.review as "mr"] ?? data.review}`}>
      <Handle id="l" type="target" position={Position.Left} isConnectable={false} />
      <Handle id="t" type="target" position={Position.Top} isConnectable={false} />
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
      <Handle id="r" type="source" position={Position.Right} isConnectable={false} />
      <Handle id="b" type="source" position={Position.Bottom} isConnectable={false} />
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

/**
 * 線を React Flow に渡す形にする。
 *
 * **留める端を関係ごとに分ける**（`requires` は左右、`overlap` は上下）。同じ端どうしにすると、
 * 両方の関係を持つ組で 2 本がぴったり重なり、破線が実線の下に隠れてラベルも読めなくなる
 * （`PhaseNodeView` の頭）。曲線にして離す手もあるが、曲線は外へ大きく振れてラベルが迷子になる
 * （実際の設定で確かめた）。端を分けるほうが、経路もラベルの位置も読める。
 */
function edgesOf(graph: PhasesGraph): Edge[] {
  return graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.a,
    target: edge.b,
    sourceHandle: edge.relation === "overlap" ? "b" : "r",
    targetHandle: edge.relation === "overlap" ? "t" : "l",
    type: "straight" as const,
    className: `rel-${edge.relation}`,
    // 向きが無いので、端の印は付けない。**線に札も付けない**（同じ組の 2 本は midpoint が
    // 近く、札どうしが重なって片方が読めなくなる。実際の設定で確かめた）。
    // 実線と破線の読み方は、図の下の一言が言う（`text.ts` の `graphNote`）
  }));
}

export function Graph({ graph, onPick }: { readonly graph: PhasesGraph; readonly onPick: (id: string) => void }): JSX.Element {
  const [spots, setSpots] = useState<Spots>(() => loadSpots());
  const nodes = useMemo(() => nodesOf(graph, spots), [graph, spots]);
  const edges = useMemo(() => edgesOf(graph), [graph]);

  /**
   * 控えの書き込みは、**state を更新する関数の中でやらない**。更新関数は呼ばれる回数を
   * 約束しない（StrictMode や並行描画で 2 度呼ばれる）ので、そこに外への書き込みを置くと
   * 二重に書く。`spots` が変わったあとに 1 度だけ書く。
   */
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      // 読み込んだ直後は、読んだものをそのまま書き戻すだけになるので書かない
      first.current = false;
      return;
    }
    saveSpots(spots);
  }, [spots]);

  // 図に出なくなった種類の控えは落とす（id を打ち替えるたびに溜まるため）
  const known = useRef<string>("");
  useEffect(() => {
    const ids = graph.nodes.map((node) => node.id).join("\u0000");
    if (known.current === ids) {
      return;
    }
    known.current = ids;
    setSpots((now) => keepSpots(now, graph.nodes.map((node) => node.id)));
  }, [graph]);

  const onDragStop = useCallback((_event: unknown, node: PhaseNode) => {
    setSpots((now) => withSpot(now, node.id, node.position.x, node.position.y));
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
