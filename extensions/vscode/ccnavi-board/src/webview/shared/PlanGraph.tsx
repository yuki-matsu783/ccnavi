/**
 * 計画の図の部品。承認のオーバーレイ（読むだけ）と計画編集タブ（線を引き直す）が共有する。
 * 中身は実行ファイルが返す計画 1 つぶん（`plans` の 1 件）で、点・線・置き場所は `core/plan-graph.ts` が組む。
 * 見た目と React Flow の扱いはフロー編集画面の図（`flow/Canvas.tsx`）に揃えてある。
 *
 * 終端に届かないステップ（`loose`）・すぐ始まるステップ（`ready`）・延期の引き受け手（`review_at`）・
 * 順序の検査の理由（`problems`）は実行ファイルの答えをそのまま描く。図の下の一言も、その値を並べるだけ。
 *
 * **`editable` が偽なら読むだけ。** 線を引けず、点も線も選べず、点を動かせない（`Canvas.tsx` の読むだけの扱いと
 * 同じ）。拡大・縮小と全体表示はできる。`editable` が真なら、ステップからステップへ線を引ける（計画編集タブ）。
 * 引いている最中に、引けない線（自分へ・start と end に触れる・固定した番号へ入る・循環になる）を
 * `canConnect` で断る（放しても線はできない）。引けた線は `onConnect` で呼び手に返すだけで、図は描き直さない
 * （呼び手が計画を作り直して渡し直す。番号の振り直しの正は実行ファイル）。`onDisconnect` を渡せば、消せる線
 * （ステップどうしの線で、固定した番号へ入らないもの。`canDisconnect`）の真ん中に × を出し、押したら呼び手に返す。
 *
 * **置き場所。** `layout` を渡せば、ステップをその場所に置く（振り直す前の番号 `from` で覚えた場所。`core/plan-graph.ts` の
 * `withLayout`）。計画編集タブは、番号が振り直っても点を動かさないためにこれを使う。開いたときの見え方
 * （全体を枠に収める）は `fitKey` が変わったときだけ合わせ直す（渡さなければ計画が替わるたび）。
 *
 * **`detail` が真なら、ステップを押すと右に詳細の欄を出す**（計画編集タブ）。番号・定義・タイトル・見る場所・延期先・固定・
 * 先行と後続・すぐ始まるか・終端に届かないかを、実行ファイルの値のまま読むだけで出す（直すのは線）。欄は畳める
 * （畳んだかは画面が覚える）。
 *
 * 見た目は `PlanGraph.css`。React Flow の CSS は `--xy-*` の変数で出来ているので、そこを `--vscode-*` で上書きする。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type JSX, type MouseEvent as ReactMouseEvent } from "react";
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
  useReactFlow,
  useStore,
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
import { mergeState, readState } from "../vscode.js";
import { maximizeClass, MaximizeButton, RefitOnChange, useMaximize } from "./Maximize.js";
import { FLOW_ARIA_LABELS } from "./flow-aria.js";

interface ItemData extends Record<string, unknown> {
  readonly node: PlanGraphNode;
  readonly editable: boolean;
  readonly picked: boolean;
}

type ItemNode = Node<ItemData, "planItem">;
type EndNode = Node<ItemData, "planEnd">;
type PlanNode = ItemNode | EndNode;

/**
 * 見る場所の言葉。同じオーバーレイの本文（承認画面。`agree_screen.py`）の「レビュー不要」
 * 「レビュー要: マージリクエスト」を、点の幅に収まる短さにしたもの。
 * 知らない表記はそのまま出す
 */
function reviewText(review: string): string {
  switch (review) {
    case "none":
      return "レビュー不要";
    case "mr":
      return "レビュー必須（MR）";
    default:
      return `レビュー: ${review === "" ? "?" : review}`;
  }
}

/** 延期したステップの言葉。本文の先行の一覧（「レビューは 4 と一緒に」）と同じ言い方 */
function deferText(reviewAt: number | null): string {
  return reviewAt === null ? "レビューを受け持つステップがありません" : `レビューは ${reviewAt} と一緒に`;
}

/** ステップの点。番号・定義のタイトル・見る場所と、延期・固定・終端に届かない印 */
function ItemView({ data }: NodeProps<ItemNode>): JSX.Element {
  const { node, editable } = data;
  const title = [
    node.type,
    node.loose ? "最後のステップの先行になっていない（終端に届かない）" : "",
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
      data-picked={data.picked ? "1" : "0"}
      title={title}
    >
      <Handle type="target" position={Position.Left} isConnectable={editable && !node.locked} />
      <span className="plan-node-number">{node.number}</span>
      <span className="plan-node-title">{node.label}</span>
      <span className="plan-node-tags">
        {/* 延期したステップは見る場所を出さず、引き受け手を言う（本文も延期したステップに見る場所を書かない） */}
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
 * end は、終端に届かないステップからの線を下の取っ手で受ける。左の取っ手で受けると、最後のステップからの線と
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

/** end の取っ手。ふつうの線は左、終端に届かないステップからの線は下 */
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
            aria-label="この線を削除（先行を外す）"
            title="この線を削除（先行を外す）"
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

function nodesOf(graph: Graph, editable: boolean, picked: string | undefined): PlanNode[] {
  return graph.nodes.map((node) => ({
    id: node.id,
    type: node.kind === "item" ? ("planItem" as const) : ("planEnd" as const),
    position: { x: node.x, y: node.y },
    data: { node, editable, picked: node.id === picked },
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
/**
 * 点がすべて測れたか。React Flow の `useNodesInitialized` は使わない。あれは呼び手が渡した点（`nodes`）に
 * 測った大きさが書き戻されて初めて真になり、点を読むだけで返さない（`onNodesChange` を持たない）この図では、
 * 実際の Chromium でいつまでも偽のまま（全体を枠に収める段が走らない）。React Flow が測って持つ内側の点を見る
 */
function useMeasured(): boolean {
  return useStore((s) => s.nodeLookup.size > 0 && [...s.nodeLookup.values()].every((node) => (node.measured?.width ?? 0) > 0 && (node.measured?.height ?? 0) > 0));
}

function FitOnOpen({ fitKey }: { readonly fitKey: unknown }): null {
  const initialized = useMeasured();
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
  const deferred = plan.items.filter((item) => item.deferred).map((item) => (item.review_at === null ? `${item.number} はレビューを受け持つステップがありません` : `${item.number} は ${item.review_at} と一緒に`));
  const lines: { readonly text: string; readonly warn: boolean }[] = [];
  if (plan.ready.length > 0) {
    lines.push({ text: `すぐ始まる: ${plan.ready.join(", ")}`, warn: false });
  }
  if (deferred.length > 0) {
    lines.push({ text: `延期したレビュー: ${deferred.join("、")}`, warn: false });
  }
  if (plan.loose.length > 0) {
    // 本文の先行の一覧（「最後のステップの先行になっていない: …」）と同じ言い方に、直し方を添える
    lines.push({ text: `最後のステップの先行になっていない: ${plan.loose.join(", ")}。最後のステップへ線を引くか、合流のステップを置いてください`, warn: true });
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

/** 線が引けたときに呼び手へ返すもの。`from` が `to` の先行になる（番号） */
export type PlanConnect = (from: number, to: number) => void;

/**
 * 詳細の欄を畳んだか。図が何枚あっても（全体計画とフィードバック計画）同じ値を見せるため、画面で 1 つだけ持ち、
 * HTML を作り直しても残るよう `getState` にも書く
 */
const FOLD_KEY = "planDetailFolded";
let detailFoldedNow: boolean | undefined;
const foldListeners = new Set<() => void>();

function readDetailFolded(): boolean {
  detailFoldedNow ??= readState(FOLD_KEY) === true;
  return detailFoldedNow;
}

function setDetailFolded(folded: boolean): void {
  detailFoldedNow = folded;
  mergeState(FOLD_KEY, folded);
  foldListeners.forEach((listener) => listener());
}

function useDetailFolded(): boolean {
  return useSyncExternalStore(
    (listener) => {
      foldListeners.add(listener);
      return () => foldListeners.delete(listener);
    },
    readDetailFolded,
  );
}

/** 押したステップの詳細。実行ファイルの値を並べるだけ（判定しない） */
function Detail({ plan, graph, id }: { readonly plan: ApprovePlan; readonly graph: Graph; readonly id: string | undefined }): JSX.Element {
  const node = id === undefined ? undefined : graph.nodes.find((n) => n.id === id);
  if (node === undefined || node.number === null) {
    return <p className="dim small plan-detail-empty">ステップを押すと、ここに詳細を表示します。</p>;
  }
  const number = node.number;
  const numberOfNode = (nodeId: string): number | undefined => graph.nodes.find((n) => n.id === nodeId)?.number ?? undefined;
  const listOf = (ids: readonly (number | undefined)[]): string => {
    const numbers = ids.filter((n): n is number => n !== undefined).sort((a, b) => a - b);
    return numbers.length === 0 ? "なし" : numbers.join(", ");
  };
  const before = graph.edges.filter((e) => e.kind === "after" && e.target === node.id).map((e) => numberOfNode(e.source));
  const after = graph.edges.filter((e) => e.kind === "after" && e.source === node.id).map((e) => numberOfNode(e.target));
  const rows: readonly (readonly [string, string])[] = [
    ["番号", String(number)],
    ["提案での番号", node.from === null ? "" : String(node.from)],
    ["タイトル", node.label],
    ["定義", node.type],
    ["見る場所", node.deferred ? deferText(node.reviewAt) : reviewText(node.review)],
    ["先行ステップ", listOf(before)],
    ["後続ステップ", listOf(after)],
    ["すぐ始まる", plan.ready.includes(number) ? "はい" : "いいえ"],
    ["最後のステップの先行になっていない", node.loose ? "はい（終端に届かない）" : "いいえ"],
    ["番号の固定", node.locked ? "固定（子が承認済み。入る線は変えられません）" : "なし"],
  ];
  return (
    <dl className="plan-detail-list" data-number={number}>
      {rows.map(([name, value]) => (
        <div key={name} className="plan-detail-row">
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export interface PlanGraphProps {
  readonly plan: ApprovePlan;
  /** 真なら、ステップを押すと右に詳細の欄を出す（畳める）。承認のオーバーレイは狭いので出さない */
  readonly detail?: boolean;
  readonly editable?: boolean;
  readonly onConnect?: PlanConnect;
  /** 線を消したときに呼ぶ。`from` が `to` の先行でなくなる（番号）。渡したときだけ × を出す */
  readonly onDisconnect?: PlanConnect;
  /** ステップの置き場所（振り直す前の番号で覚えたもの）。無ければ `planGraphOf` が組んだ場所 */
  readonly layout?: PlanLayout;
  /** 開いたときの見え方を合わせ直す合図。変わったときだけ全体を枠に収める。無ければ計画が替わるたび */
  readonly fitKey?: unknown;
}

export function PlanGraph({ plan, detail = false, editable = false, onConnect, onDisconnect, layout, fitKey }: PlanGraphProps): JSX.Element {
  const graph = useMemo(() => (layout === undefined ? planGraphOf(plan) : withLayout(planGraphOf(plan), layout)), [plan, layout]);
  const removable = editable && onDisconnect !== undefined;
  const [picked, setPicked] = useState<string | undefined>(undefined);
  const folded = useDetailFolded();
  const [maximized, toggleMaximize, maxBox] = useMaximize();
  const nodes = useMemo(() => nodesOf(graph, editable, detail ? picked : undefined), [graph, editable, detail, picked]);
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
      <div className="plan-graph-body" data-folded={detail && folded ? "1" : "0"}>
      <div ref={maxBox} className={`graph plan-graph-canvas ${maximizeClass(maximized)}`} data-nodes={graph.nodes.length} data-edges={graph.edges.length}>
        <ReactFlow<PlanNode, Edge>
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={editable}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          // 押せるのは onNodeClick があるときだけ（無いと点は pointer-events: none で描かれる）
          onNodeClick={
            detail
              ? (event, node) => {
                  // 線を引く取っ手の操作では選ばない（取っ手のクリックも点まで泡が上がる）
                  if (!(event.target as Element).closest(".react-flow__handle")) {
                    setPicked(node.type === "planItem" ? node.id : undefined);
                  }
                }
              : undefined
          }
          onPaneClick={detail ? () => setPicked(undefined) : undefined}
          isValidConnection={isValidConnection}
          onConnect={handleConnect}
          ariaLabelConfig={FLOW_ARIA_LABELS}
          minZoom={0.2}
          maxZoom={1.6}
          // 線と点を消すキーは受けない（読むだけのときに消えたように見えないように）
          deleteKeyCode={null}
        >
          <Background />
          <Controls showInteractive={false} />
          <FitOnOpen fitKey={fitKey === undefined ? plan : fitKey} />
          <RefitOnChange value={maximized} />
        </ReactFlow>
        <MaximizeButton maximized={maximized} onToggle={toggleMaximize} />
      </div>
      {detail && (
        <aside className="plan-detail" id="plan-detail" aria-label="ステップの詳細" data-folded={folded ? "1" : "0"}>
          <button
            type="button"
            className="action small plan-detail-toggle"
            data-action="toggle-plan-detail"
            aria-expanded={!folded}
            title={folded ? "詳細の欄を開く" : "詳細の欄を畳む"}
            onClick={() => setDetailFolded(!folded)}
          >
            {folded ? "◀ 詳細" : "詳細 ▶"}
          </button>
          <Detail plan={plan} graph={graph} id={picked} />
        </aside>
      )}
      </div>
      <Notes plan={plan} />
    </section>
    </RemoveEdge.Provider>
  );
}
