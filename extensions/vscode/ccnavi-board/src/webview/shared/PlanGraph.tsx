/**
 * 計画の図の部品。承認のオーバーレイ（読むだけ）とワークフロー編集タブ（線を引き直す）が共有する。
 * 中身は実行ファイルが返す計画 1 つぶん（`plans` の 1 件）で、点・線・置き場所は `core/plan-graph.ts` が組む。
 * 見た目と React Flow の扱いはフロー編集画面の図（`flow/Canvas.tsx`）に揃えてある。
 *
 * **判定はしない。** 終端に当たらない項（`loose`）・すぐ始まる項（`ready`）・延期の引き受け手（`review_at`）・
 * 順序の検査の理由（`problems`）は実行ファイルの答えをそのまま描く。図の下の一言も、その値を並べるだけ。
 *
 * **`editable` が偽なら読むだけ。** 線を引けず、点も線も選べず、点を動かせない（`Canvas.tsx` の読むだけの扱いと
 * 同じ）。拡大・縮小と全体表示はできる。`editable` が真なら、項から項へ線を引ける（ワークフロー編集タブ）。
 * 引いている最中に、引けない線（自分へ・start と end に触れる・固定した番号へ入る・循環になる）を
 * `canConnect` で断る（放しても線はできない）。引けた線は `onConnect` で呼び手に返すだけで、図は描き直さない
 * （呼び手が計画を作り直して渡し直す。番号の振り直しの正は実行ファイル）。`onDisconnect` を渡せば、消せる線
 * （項どうしの線で、固定した番号へ入らないもの。`canDisconnect`）の真ん中に × を出し、押したら呼び手に返す。
 *
 * **置き場所。** `layout` を渡せば、項をその場所に置く（振り直す前の番号 `from` で覚えた場所。`core/plan-graph.ts` の
 * `withLayout`）。ワークフロー編集タブは、番号が振り直っても点を動かさないためにこれを使う。開いたときの見え方
 * （全体を枠に収める）は `fitKey` が変わったときだけ合わせ直す（渡さなければ計画が替わるたび）。
 *
 * 見た目は `PlanGraph.css`。React Flow の CSS は `--xy-*` の変数で出来ているので、そこを `--vscode-*` で上書きする。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type JSX, type MouseEvent as ReactMouseEvent } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  getBezierPath,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useNodesInitialized,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeProps,
  type EdgeTypes,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";

import type { ApprovePlan } from "../../core/approvemodel.js";
import {
  canConnect,
  canDisconnect,
  PLAN_FIT_MAX_ZOOM,
  PLAN_FIT_MIN_ZOOM,
  planGraphOf,
  withLayout,
  type PlanGraph as Graph,
  type PlanGraphNode,
  type PlanLayout,
} from "../../core/plan-graph.js";

interface ItemData extends Record<string, unknown> {
  readonly node: PlanGraphNode;
  readonly editable: boolean;
}

type ItemNode = Node<ItemData, "planItem">;
type EndNode = Node<ItemData, "planEnd">;
type PlanNode = ItemNode | EndNode;

/**
 * 見る場所の言葉。同じオーバーレイの本文（承認画面。`agree_screen.py`）の「レビュー不要」
 * 「レビュー要: マージリクエスト」「レビュー要: このセッションで」を、点の幅に収まる短さにしたもの。
 * 知らない表記はそのまま出す
 */
function reviewText(review: string): string {
  switch (review) {
    case "none":
      return "レビュー不要";
    case "mr":
      return "レビュー要（MR）";
    case "chat":
      return "レビュー要（このセッション）";
    default:
      return `レビュー: ${review === "" ? "?" : review}`;
  }
}

/** 延期した項の言葉。本文の待ちの一覧（「レビューは 4 と一緒に」）と同じ言い方 */
function deferText(reviewAt: number | null): string {
  return reviewAt === null ? "レビューを引き受ける項が無い" : `レビューは ${reviewAt} と一緒に`;
}

/** 項の点。番号・定義の題・見る場所と、延期・固定・終端に当たらない印 */
function ItemView({ data }: NodeProps<ItemNode>): JSX.Element {
  const { node, editable } = data;
  const title = [
    node.type,
    node.loose ? "最後の項が待たない（終端に当たらない）" : "",
    node.locked ? "子が承認された番号です。入る線は変えられません" : "",
  ]
    .filter((part) => part !== "")
    .join("\n");
  return (
    <div
      className="plan-node"
      data-number={node.number ?? ""}
      data-loose={node.loose ? "1" : "0"}
      data-locked={node.locked ? "1" : "0"}
      data-deferred={node.deferred ? "1" : "0"}
      title={title}
    >
      <Handle type="target" position={Position.Left} isConnectable={editable && !node.locked} />
      <span className="plan-node-number">{node.number}</span>
      <span className="plan-node-title">{node.label}</span>
      <span className="plan-node-tags">
        {/* 延期した項は見る場所を出さず、引き受け手を言う（本文も延期した項に見る場所を書かない） */}
        {node.deferred ? (
          <span className="tag deferred">{deferText(node.reviewAt)}</span>
        ) : (
          <span className="tag" data-review={node.review}>
            {reviewText(node.review)}
          </span>
        )}
        {node.locked && <span className="tag locked">子が承認済み</span>}
      </span>
      <Handle type="source" position={Position.Right} isConnectable={editable} />
    </div>
  );
}

/**
 * start と end の仮の点。名前だけ。線は図が引くので、取っ手から線は引けない。
 * end は、終端に当たらない項からの線を下の取っ手で受ける。左の取っ手で受けると、最後の項からの線と
 * end の手前で重なり、どちらが注意の線か見分けられない
 */
function EndView({ data }: NodeProps<EndNode>): JSX.Element {
  const { node } = data;
  return (
    <div className="plan-end" data-kind={node.kind}>
      {node.kind === "end" && <Handle id={END_IN} type="target" position={Position.Left} isConnectable={false} />}
      {node.kind === "end" && <Handle id={END_LOOSE} type="target" position={Position.Bottom} isConnectable={false} />}
      {node.label}
      {node.kind === "start" && <Handle type="source" position={Position.Right} isConnectable={false} />}
    </div>
  );
}

/** end の取っ手。ふつうの線は左、終端に当たらない項からの線は下 */
const END_IN = "in";
const END_LOOSE = "loose";

/** 消せる線の × を押したときに呼ぶもの。線の id を渡す。図の部品ごとに 1 つ */
const RemoveEdge = createContext<(id: string) => void>(() => undefined);

/**
 * 消せる線。矢印は既定の線と同じに描き、真ん中に × を置く（いつも出す。線を選ばせないので、載せたときだけ出す形に
 * すると、どの線が消せるかが分からない）。押しても図を動かさない（`nodrag` `nopan`）
 */
function RemovableEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, style, interactionWidth }: EdgeProps): JSX.Element {
  const remove = useContext(RemoveEdge);
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const stop = (event: ReactMouseEvent): void => event.stopPropagation();
  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={style} interactionWidth={interactionWidth} />
      <EdgeLabelRenderer>
        <div className="plan-edge-tools nodrag nopan" style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
          <button
            type="button"
            className="plan-remove nodrag nopan"
            data-action="plan-remove-edge"
            data-edge={id}
            aria-label="この線を消す（待たなくする）"
            title="この線を消す（待たなくする）"
            onMouseDown={stop}
            onPointerDown={stop}
            onClick={(event) => {
              event.stopPropagation();
              remove(id);
            }}
          >
            ×
          </button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

// 描くたびに作り直すと React Flow が「点の種類が変わった」と言って組み直す。1 度だけ作る
const NODE_TYPES: NodeTypes = { planItem: ItemView, planEnd: EndView };
const EDGE_TYPES: EdgeTypes = { planRemovable: RemovableEdge };

function nodesOf(graph: Graph, editable: boolean): PlanNode[] {
  return graph.nodes.map((node) => ({
    id: node.id,
    type: node.kind === "item" ? ("planItem" as const) : ("planEnd" as const),
    position: { x: node.x, y: node.y },
    data: { node, editable },
  }));
}

function edgesOf(graph: Graph, removable: boolean): Edge[] {
  return graph.edges.map((edge) => {
    const color = edge.loose ? "var(--vscode-editorWarning-foreground)" : "var(--vscode-descriptionForeground)";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      ...(edge.kind === "end" ? { targetHandle: edge.loose ? END_LOOSE : END_IN } : {}),
      ...(removable && canDisconnect(graph, edge.source, edge.target) ? { type: "planRemovable" } : {}),
      className: ["plan-edge", `plan-${edge.kind}`, edge.loose ? "loose" : ""].filter((name) => name !== "").join(" "),
      markerEnd: { type: MarkerType.ArrowClosed, color },
      // start と end への線は図が引くもので、消せない
      deletable: false,
    };
  });
}

/** 下限で止めたときに、開始の左に空ける幅（px） */
const START_PAD = 16;

/**
 * 開いたときの見え方。点の大きさが測れたら、全体を枠に収める（`PLAN_FIT_MIN_ZOOM` より縮めず、
 * `PLAN_FIT_MAX_ZOOM` より拡げない）。下限で止まって収まらないときは、開始（左端の列）が見えるように
 * 左へ寄せる（真ん中に合わせると、開始も終了も枠の外になる）。計画が替わったら合わせ直す。
 * React Flow の `fitView` の属性は使わない（下限で止めたあとに寄せる段を挟めないため）
 */
function FitOnOpen({ fitKey }: { readonly fitKey: unknown }): null {
  const initialized = useNodesInitialized();
  const flow = useReactFlow();
  const done = useRef<{ readonly key: unknown } | undefined>(undefined);
  useEffect(() => {
    if (!initialized || (done.current !== undefined && done.current.key === fitKey)) {
      return;
    }
    done.current = { key: fitKey };
    void Promise.resolve(flow.fitView({ minZoom: PLAN_FIT_MIN_ZOOM, maxZoom: PLAN_FIT_MAX_ZOOM, padding: 0.1 })).then(() => {
      const view = flow.getViewport();
      if (view.zoom <= PLAN_FIT_MIN_ZOOM + 1e-6) {
        const left = Math.min(...flow.getNodes().map((node) => node.position.x));
        void flow.setViewport({ x: START_PAD - left * view.zoom, y: view.y, zoom: view.zoom });
      }
    });
  }, [initialized, fitKey, flow]);
  return null;
}

/** 計画の見出し */
function headingOf(plan: ApprovePlan): string {
  return `${plan.ticket} の${plan.part === "feedback" ? "フィードバック計画（feedback:）" : "全体計画（plan:）"}`;
}

/** 図の下の一言。実行ファイルの値を並べるだけ */
function Notes({ plan }: { readonly plan: ApprovePlan }): JSX.Element | null {
  const deferred = plan.items.filter((item) => item.deferred).map((item) => (item.review_at === null ? `${item.number} は引き受ける項が無い` : `${item.number} は ${item.review_at} と一緒に`));
  const lines: { readonly text: string; readonly warn: boolean }[] = [];
  if (plan.ready.length > 0) {
    lines.push({ text: `すぐ始まる: ${plan.ready.join(", ")}`, warn: false });
  }
  if (deferred.length > 0) {
    lines.push({ text: `延期したレビュー: ${deferred.join("、")}`, warn: false });
  }
  if (plan.loose.length > 0) {
    // 本文の待ちの一覧（「最後の項が待たない: …」）と同じ言い方に、直し方を添える
    lines.push({ text: `最後の項が待たない: ${plan.loose.join(", ")}。最後の項へ線を引くか、合流の項を置く`, warn: true });
  }
  for (const problem of plan.problems) {
    lines.push({ text: problem, warn: true });
  }
  if (lines.length === 0) {
    return null;
  }
  return (
    <ul className="plan-graph-notes">
      {lines.map((line, index) => (
        <li key={index} className={line.warn ? "plan-graph-note warn" : "plan-graph-note"}>
          {line.text}
        </li>
      ))}
    </ul>
  );
}

/** 線が引けたときに呼び手へ返すもの。`from` を `to` が待つ（番号） */
export type PlanConnect = (from: number, to: number) => void;

export interface PlanGraphProps {
  readonly plan: ApprovePlan;
  readonly editable?: boolean;
  readonly onConnect?: PlanConnect;
  /** 線を消したときに呼ぶ。`from` を `to` が待たなくなる（番号）。渡したときだけ × を出す */
  readonly onDisconnect?: PlanConnect;
  /** 項の置き場所（振り直す前の番号で覚えたもの）。無ければ `planGraphOf` が組んだ場所 */
  readonly layout?: PlanLayout;
  /** 開いたときの見え方を合わせ直す合図。変わったときだけ全体を枠に収める。無ければ計画が替わるたび */
  readonly fitKey?: unknown;
}

export function PlanGraph({ plan, editable = false, onConnect, onDisconnect, layout, fitKey }: PlanGraphProps): JSX.Element {
  const graph = useMemo(() => (layout === undefined ? planGraphOf(plan) : withLayout(planGraphOf(plan), layout)), [plan, layout]);
  const removable = editable && onDisconnect !== undefined;
  const nodes = useMemo(() => nodesOf(graph, editable), [graph, editable]);
  const edges = useMemo(() => edgesOf(graph, removable), [graph, removable]);
  const numberOf = useCallback((id: string): number | undefined => graph.nodes.find((node) => node.id === id)?.number ?? undefined, [graph]);

  // 引いている最中に断る。読むだけなら何も引けない
  const isValidConnection = useCallback((connection: Connection | Edge) => editable && canConnect(graph, connection.source, connection.target), [graph, editable]);
  const handleConnect = useCallback(
    (connection: Connection) => {
      const from = numberOf(connection.source);
      const to = numberOf(connection.target);
      if (editable && onConnect !== undefined && from !== undefined && to !== undefined && canConnect(graph, connection.source, connection.target)) {
        onConnect(from, to);
      }
    },
    [editable, graph, numberOf, onConnect],
  );

  const removeEdge = useCallback(
    (id: string) => {
      const edge = graph.edges.find((e) => e.id === id);
      const from = edge === undefined ? undefined : numberOf(edge.source);
      const to = edge === undefined ? undefined : numberOf(edge.target);
      if (removable && edge !== undefined && from !== undefined && to !== undefined && canDisconnect(graph, edge.source, edge.target)) {
        onDisconnect?.(from, to);
      }
    },
    [graph, numberOf, onDisconnect, removable],
  );

  return (
    <RemoveEdge.Provider value={removeEdge}>
    <section className="plan-graph" data-part={plan.part} data-ticket={plan.ticket} data-editable={editable ? "1" : "0"}>
      <h3>{headingOf(plan)}</h3>
      <div className="graph plan-graph-canvas" data-nodes={graph.nodes.length} data-edges={graph.edges.length}>
        <ReactFlow<PlanNode, Edge>
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          nodesDraggable={false}
          nodesConnectable={editable}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          isValidConnection={isValidConnection}
          onConnect={handleConnect}
          ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "拡大", "controls.zoomOut.ariaLabel": "縮小", "controls.fitView.ariaLabel": "全体を表示" }}
          minZoom={0.2}
          maxZoom={1.6}
          // 線と点を消すキーは受けない（読むだけのときに消えたように見えないように）
          deleteKeyCode={null}
        >
          <Background />
          <Controls showInteractive={false} />
          <FitOnOpen fitKey={fitKey === undefined ? plan : fitKey} />
        </ReactFlow>
      </div>
      <Notes plan={plan} />
    </section>
    </RemoveEdge.Provider>
  );
}
