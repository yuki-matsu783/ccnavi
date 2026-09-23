/**
 * フェーズの種類の関係を図で見せる。点が種類、線が `requires` と `overlap` と `after`。
 *
 * **矢印を付けるのは `after` だけ。** `requires` は「一緒に置くべき」で、順序ではない（`phases-graph.ts` の頭）。
 * 見る場所が `none` でない種類は、点の縁を強めて「人が見る」を添える（種類の宣言。計画の延期や
 * 実績のリスクで変わることは図の下の一言が言う）。
 * 図が判定をしないのも同じところに書いてある。ここは `graphOf` が組んだものを描くだけで、
 * 何が正しいかは言わない。
 *
 * **編集はしない。** 点をドラッグで動かせるが、動かした先は画面の控え（`state.ts` の spots）に入るだけで、
 * `phases.yml` には書かない。人が持つ設定に座標は入れない。関係そのものを直すのは一覧のほう。
 *
 * 点を押すと一覧へ戻り、その種類の行が開く（`onPick`）。
 *
 * 見た目は `Graph.css`。React Flow の CSS は `--xy-*` の変数で出来ているので、そこを
 * `--vscode-*` で上書きする。直書きの色を打ち消す `!important` は要らない。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
import { Background, BaseEdge, Controls, getBezierPath, Handle, MarkerType, Position, ReactFlow, type Edge, type EdgeProps, type EdgeTypes, type Node, type NodeProps, type NodeTypes } from "@xyflow/react";

import { COLUMN, keepSpots, NODE_WIDTH, ROW, withSpot, type PhasesGraph, type Relation, type Spots } from "../../core/phases-graph.js";
import { KIND_LABELS, REVIEW_LABELS } from "../../core/phases-view.js";
import { loadSpots, saveSpots } from "./state.js";

/** 点 1 つが持つ中身。React Flow の `data` に載る */
interface PhaseData extends Record<string, unknown> {
  readonly title: string;
  readonly kind: string;
  readonly review: string;
}

type PhaseNode = Node<PhaseData, "phase">;

/** 端の辺。l = 左、r = 右、t = 上、b = 下 */
type Side = "l" | "r" | "t" | "b";

const SIDES: readonly { readonly side: Side; readonly position: Position }[] = [
  { side: "l", position: Position.Left },
  { side: "r", position: Position.Right },
  { side: "t", position: Position.Top },
  { side: "b", position: Position.Bottom },
];

/**
 * 点の見た目。`Handle` は線の端を留めるためだけに置き、目には見せない（`Graph.css`）。
 * React Flow が端を要るから置くのであって、**上下左右に意味は無い**。どの辺を使うかは
 * 相手の点との位置で決める（`routeOf`）。4 辺それぞれに、出る端と入る端を置いておく。
 */
function PhaseNodeView({ id, data }: NodeProps<PhaseNode>): JSX.Element {
  return (
    <div className="phase-node" data-kind={data.kind} data-review={data.review} title={`${KIND_LABELS[data.kind as "work"] ?? data.kind}\n${REVIEW_LABELS[data.review as "mr"] ?? data.review}`}>
      {SIDES.flatMap(({ side, position }) => [
        <Handle key={`s-${side}`} id={`s-${side}`} type="source" position={position} isConnectable={false} />,
        <Handle key={`t-${side}`} id={`t-${side}`} type="target" position={position} isConnectable={false} />,
      ])}
      <span className="phase-node-id">{id}</span>
      {data.title !== "" && <span className="phase-node-title">{data.title}</span>}
      <span className="phase-node-tags">
        <span className="tag" data-kind={data.kind}>
          {data.kind}
        </span>
        <span className="tag" data-review={data.review}>
          {data.review}
        </span>
        {data.review !== "none" && <span className="tag hitl">人が見る</span>}
      </span>
    </div>
  );
}

/** 線 1 本が持つ中身。React Flow の `data` に載る */
interface RelationData extends Record<string, unknown> {
  /** 辺に沿って端をずらす量（px）。同じ組の 2 本を平行に並べる */
  readonly shift: number;
  /** 間の点を跨ぐときに膨らませる向き。無ければ点どうしを曲線で結ぶ */
  readonly bulge?: "down" | "right";
}

type RelationEdge = Edge<RelationData, "relation">;

/**
 * 関係ごとの端のずらし。同じ組が両方の関係を持つことがあり（雛形とこのリポジトリの設定の
 * `acceptance` と `implement`）、同じ端どうしを結ぶと 2 本がぴったり重なって破線が実線の下に隠れる。
 */
const SHIFT: Readonly<Record<Relation, number>> = { requires: -8, after: 0, overlap: 8 };

/** 跨ぐ曲線の膨らみ（px）。行の間（`ROW` − 点の高さ）に収まる量にする */
const BULGE = 44;

/**
 * 線の見た目。端をずらし、間の点を跨ぐときは外へ膨らませ、そうでなければ
 * React Flow のベジェ曲線で結ぶ（cc-wf-studio の線と同じ `getBezierPath`）。
 */
function RelationEdgeView({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerStart, markerEnd, style }: EdgeProps<RelationEdge>): JSX.Element {
  const shift = data?.shift ?? 0;
  const across = sourcePosition === Position.Left || sourcePosition === Position.Right;
  const [sx, sy, tx, ty] = across ? [sourceX, sourceY + shift, targetX, targetY + shift] : [sourceX + shift, sourceY, targetX + shift, targetY];
  // 跨ぐ曲線は、端をずらすだけでは中ほどで寄り合うので、膨らみの深さも関係ごとに変える
  const depth = BULGE + shift * 1.5;
  let path: string;
  if (data?.bulge === "down") {
    path = `M ${sx},${sy} C ${sx},${Math.max(sy, ty) + depth} ${tx},${Math.max(sy, ty) + depth} ${tx},${ty}`;
  } else if (data?.bulge === "right") {
    path = `M ${sx},${sy} C ${Math.max(sx, tx) + depth},${sy} ${Math.max(sx, tx) + depth},${ty} ${tx},${ty}`;
  } else {
    [path] = getBezierPath({ sourceX: sx, sourceY: sy, sourcePosition, targetX: tx, targetY: ty, targetPosition });
  }
  return <BaseEdge path={path} markerStart={markerStart} markerEnd={markerEnd} style={style} />;
}

// 描くたびに作り直すと React Flow が「点の種類が変わった」と言って組み直す。1 度だけ作る
const NODE_TYPES: NodeTypes = { phase: PhaseNodeView };
const EDGE_TYPES: EdgeTypes = { relation: RelationEdgeView };

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

/** 線 1 本の引き方。どちらの点から出て、どの辺に留め、外へ膨らませるか */
interface Route {
  readonly from: string;
  readonly to: string;
  readonly fromSide: Side;
  readonly toSide: Side;
  readonly bulge?: "down" | "right";
}

/**
 * 線の引き方を、2 つの点の置き場所から決める。**線が点を横切らないようにする。**
 *
 * - 横に離れている: 左の点の右辺から、右の点の左辺へ
 * - ほぼ縦に並ぶ: 上の点の下辺から、下の点の上辺へ
 * - 同じ行で 1 つ以上離れている: 間の点を跨ぐので、両方の下辺から下へ膨らませる
 * - 同じ列で 1 つ以上離れている: 同じく、両方の右辺から右へ膨らませる
 *
 * 前は関係ごとに辺を固定していた（`requires` は右→左、`overlap` は下→上）。点は id の順に並ぶので、
 * 雛形の `acceptance` と `implement` の間には `design` が入り、線がその上を突き抜けていた。
 * 点の置き場所は変えない（`phases-graph.ts` の頭。関係を直すたびに絵が組み替わらないように）。
 */
function routeOf(a: string, b: string, at: ReadonlyMap<string, { x: number; y: number }>): Route {
  const pa = at.get(a) ?? { x: 0, y: 0 };
  const pb = at.get(b) ?? { x: 0, y: 0 };
  const dx = pb.x - pa.x;
  const dy = pb.y - pa.y;
  if (Math.abs(dx) < NODE_WIDTH) {
    const [from, to] = dy >= 0 ? [a, b] : [b, a];
    return Math.abs(dy) > ROW * 1.5 ? { from, to, fromSide: "r", toSide: "r", bulge: "right" } : { from, to, fromSide: "b", toSide: "t" };
  }
  const [from, to] = dx >= 0 ? [a, b] : [b, a];
  return Math.abs(dy) < ROW / 2 && Math.abs(dx) > COLUMN * 1.5 ? { from, to, fromSide: "b", toSide: "b", bulge: "down" } : { from, to, fromSide: "r", toSide: "l" };
}

/**
 * 線を React Flow に渡す形にする。引き方は `routeOf`、同じ組の線は関係ごとに端をずらす（`SHIFT`）。
 *
 * `after` は向きを持つ（待たれる側 a → 待つ側 b）。引き方の都合で出る点が b になったときは、
 * 矢印を始点の側に付ける（React Flow の印は始点では向きが反転するので、b を指す）。
 * 向きの無い線には端の印を付けない。**線にラベルも付けない**（同じ組の 2 本はラベルどうしが
 * 重なって片方が読めなくなる）。実線と破線の読み方は、図の下の一言が言う（`text.ts` の `graphNote`）。
 */
function edgesOf(graph: PhasesGraph, at: ReadonlyMap<string, { x: number; y: number }>): RelationEdge[] {
  return graph.edges.map((edge) => {
    const route = routeOf(edge.a, edge.b, at);
    const arrow = { type: MarkerType.ArrowClosed };
    const marker = edge.relation !== "after" ? {} : route.to === edge.b ? { markerEnd: arrow } : { markerStart: arrow };
    return {
      ...marker,
      id: edge.id,
      source: route.from,
      target: route.to,
      sourceHandle: `s-${route.fromSide}`,
      targetHandle: `t-${route.toSide}`,
      type: "relation" as const,
      data: { shift: SHIFT[edge.relation], ...(route.bulge === undefined ? {} : { bulge: route.bulge }) },
      className: `rel-${edge.relation}`,
    };
  });
}

export function Graph({ graph, onPick }: { readonly graph: PhasesGraph; readonly onPick: (id: string) => void }): JSX.Element {
  const [spots, setSpots] = useState<Spots>(() => loadSpots());
  const nodes = useMemo(() => nodesOf(graph, spots), [graph, spots]);
  const edges = useMemo(() => edgesOf(graph, new Map(nodes.map((node) => [node.id, node.position]))), [graph, nodes]);

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
      <ReactFlow<PhaseNode, RelationEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
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
        {/* 拡大・縮小と全体表示（cc-wf-studio と同じ）。図は読むだけなので、錠のボタンは出さない */}
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
