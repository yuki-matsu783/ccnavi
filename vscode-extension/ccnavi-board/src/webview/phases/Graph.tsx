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
import { Background, BaseEdge, Controls, Handle, useInternalNode, useStore, ViewportPortal, MarkerType, Position, ReactFlow, type Edge, type EdgeProps, type EdgeTypes, type Node, type NodeProps, type NodeTypes } from "@xyflow/react";

import { keepSpots, NODE_WIDTH, withSpot, type PhasesGraph, type Relation, type Spots } from "../../core/phases-graph.js";
import { pathOf, pointsOf, routeOf, type Rect, type Route, type Side } from "../../core/phases-route.js";
import { KIND_LABELS, REVIEW_LABELS } from "../../core/phases-view.js";
import { loadSpots, saveSpots } from "./state.js";

/** 点 1 つが持つ中身。React Flow の `data` に載る */
interface PhaseData extends Record<string, unknown> {
  readonly title: string;
  readonly kind: string;
  readonly review: string;
}

type PhaseNode = Node<PhaseData, "phase">;

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
  /** 引き方（`phases-route.ts` の `routeOf`） */
  readonly route: Route;
  /** 関係ごとのずらし（px）。同じ組の線を平行に並べる */
  readonly shift: number;
}

type RelationEdge = Edge<RelationData, "relation">;

/**
 * 関係ごとのずらし。同じ組が複数の関係を持つことがあり、同じ経路だと破線が実線の下に隠れる。
 * 隙間の半分（列の間の 40px の半分）より小さく抑え、隣の点に食い込ませない。
 */
const SHIFT: Readonly<Record<Relation, number>> = { requires: -7, after: 0, overlap: 7 };

/** 点の枠。測れる前は見込みの大きさで囲む */
function rectOf(node: { internals: { positionAbsolute: { x: number; y: number } }; measured?: { width?: number; height?: number } } | undefined): Rect | undefined {
  if (node === undefined) {
    return undefined;
  }
  const { x, y } = node.internals.positionAbsolute;
  return { x, y, width: node.measured?.width ?? NODE_WIDTH, height: node.measured?.height ?? NODE_HEIGHT };
}

/**
 * 線の見た目。経路は `phases-route.ts` が点の枠から組む（点と点の間の隙間だけを通る折れ線）。
 * React Flow が渡す端の座標は使わない。枠から組むほうが、隙間の位置を正しく知れるため。
 */
function RelationEdgeView({ source, target, data, markerStart, markerEnd, style }: EdgeProps<RelationEdge>): JSX.Element | null {
  const from = rectOf(useInternalNode(source));
  const to = rectOf(useInternalNode(target));
  if (from === undefined || to === undefined || data === undefined) {
    return null;
  }
  return <BaseEdge path={pathOf(pointsOf(data.route, from, to, data.shift))} markerStart={markerStart} markerEnd={markerEnd} style={style} />;
}

/** 点の高さの見込み。測れる前（初回の描画）にだけ使う */
const NODE_HEIGHT = 80;
/** 枠の内側の余白と、見出しの高さ */
const GROUP_PAD = 14;
const GROUP_LABEL = 22;

/** 区分ごとの枠の見出し。work は全体計画、feedback はレビュー後の対応 */
const GROUP_LABELS: Readonly<Record<"work" | "feedback", string>> = {
  work: "作業（plan:）",
  feedback: "フィードバック対応（feedback:）",
};

interface Box {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/**
 * 区分ごとの枠の位置。点の実寸（`measured`）から囲むので、ドラッグで点を動かしても枠が付いてくる。
 * 返すのは文字列にした形で、`useStore` が値の比較だけで描き直しを判断できるようにする。
 */
function boxesKey(state: { nodeLookup: Map<string, { internals: { positionAbsolute: { x: number; y: number } }; measured?: { width?: number; height?: number }; data: Record<string, unknown> }> }): string {
  const bounds: Record<string, [number, number, number, number]> = {};
  for (const node of state.nodeLookup.values()) {
    const kind = node.data.kind === "feedback" ? "feedback" : "work";
    const { x, y } = node.internals.positionAbsolute;
    const right = x + (node.measured?.width ?? NODE_WIDTH);
    const bottom = y + (node.measured?.height ?? NODE_HEIGHT);
    const now = bounds[kind];
    bounds[kind] = now === undefined ? [x, y, right, bottom] : [Math.min(now[0], x), Math.min(now[1], y), Math.max(now[2], right), Math.max(now[3], bottom)];
  }
  return JSON.stringify(bounds);
}

/**
 * work と feedback の枠と、その間の「レビュー後」の矢印。**点の上に重ねて描く**（`ViewportPortal`
 * は点より手前に来る）ので、枠は縁だけにして中を塗らず、押す操作も受けない（`Graph.css`）。
 *
 * 矢印は種類どうしの関係ではなく、区分の順（全体計画を終えてレビューを受けたあとに feedback の
 * 種類で直す）。両方の区分に種類があるときだけ描く。
 */
function Groups(): JSX.Element | null {
  const key = useStore(boxesKey as (state: unknown) => string);
  const bounds = JSON.parse(key) as Record<string, [number, number, number, number]>;
  const boxes: Partial<Record<"work" | "feedback", Box>> = {};
  for (const kind of ["work", "feedback"] as const) {
    const b = bounds[kind];
    if (b !== undefined) {
      boxes[kind] = { x: b[0] - GROUP_PAD, y: b[1] - GROUP_PAD - GROUP_LABEL, width: b[2] - b[0] + GROUP_PAD * 2, height: b[3] - b[1] + GROUP_PAD * 2 + GROUP_LABEL };
    }
  }
  const { work, feedback } = boxes;
  let arrow: JSX.Element | null = null;
  if (work !== undefined && feedback !== undefined && feedback.x > work.x + work.width) {
    const x1 = work.x + work.width;
    const x2 = feedback.x;
    // feedback の枠の中ほどの高さが work の枠に収まるなら、水平に結ぶ
    const y2 = feedback.y + GROUP_LABEL + (feedback.height - GROUP_LABEL) / 2;
    const y1 = y2 > work.y + GROUP_LABEL && y2 < work.y + work.height ? y2 : work.y + GROUP_LABEL + (work.height - GROUP_LABEL) / 2;
    const top = Math.min(y1, y2) - 20;
    const height = Math.abs(y2 - y1) + 40;
    arrow = (
      <svg className="phase-group-arrow" style={{ position: "absolute", left: x1, top, width: x2 - x1, height, overflow: "visible" }}>
        <defs>
          <marker id="phase-group-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        <line x1={0} y1={y1 - top} x2={x2 - x1 - 2} y2={y2 - top} markerEnd="url(#phase-group-arrowhead)" />
        <text x={(x2 - x1) / 2} y={Math.min(y1, y2) - top - 6} textAnchor="middle">
          レビュー後
        </text>
      </svg>
    );
  }
  return (
    <ViewportPortal>
      {(["work", "feedback"] as const).map((kind) => {
        const box = boxes[kind];
        return box === undefined ? null : (
          <div key={kind} className="phase-group" data-kind={kind} style={{ position: "absolute", left: box.x, top: box.y, width: box.width, height: box.height }}>
            <span className="phase-group-label">{GROUP_LABELS[kind]}</span>
          </div>
        );
      })}
      {arrow}
    </ViewportPortal>
  );
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

/**
 * 線を React Flow に渡す形にする。引き方は `routeOf`（`phases-route.ts`）、同じ組の線は関係ごとにずらす（`SHIFT`）。
 *
 * `after` は向きを持つ（待たれる側 a → 待つ側 b）。引き方の都合で出る点が b になったときは、
 * 矢印を始点の側に付ける（React Flow の印は始点では向きが反転するので、b を指す）。
 * 向きの無い線には端の印を付けない。**線にラベルも付けない**（同じ組の 2 本はラベルどうしが
 * 重なって片方が読めなくなる）。実線と破線の読み方は、図の下の一言が言う（`text.ts` の `graphNote`）。
 */
function edgesOf(graph: PhasesGraph, at: ReadonlyMap<string, { x: number; y: number }>): RelationEdge[] {
  return graph.edges.map((edge) => {
    const route = routeOf(edge.a, edge.b, at);
    // 矢印の頭は線と同じ色にする（`Graph.css` の `.rel-after`）。style に入るので CSS の変数が効く
    const arrow = { type: MarkerType.ArrowClosed, color: "var(--vscode-focusBorder)" };
    const marker = edge.relation !== "after" ? {} : route.to === edge.b ? { markerEnd: arrow } : { markerStart: arrow };
    return {
      ...marker,
      id: edge.id,
      source: route.from,
      target: route.to,
      sourceHandle: `s-${route.fromSide}`,
      targetHandle: `t-${route.toSide}`,
      type: "relation" as const,
      data: { route, shift: SHIFT[edge.relation] },
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
        // Controls のボタンの名前。既定は英語なので、画面の他と揃える
        ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "拡大", "controls.zoomOut.ariaLabel": "縮小", "controls.fitView.ariaLabel": "全体を表示" }}
        minZoom={0.3}
        maxZoom={1.6}
        // 図は読むだけなので、消す・繋ぐの鍵は受けない
        deleteKeyCode={null}
      >
        <Background />
        <Groups />
        {/* 拡大・縮小と全体表示（cc-wf-studio と同じ）。図は読むだけなので、錠のボタンは出さない */}
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
