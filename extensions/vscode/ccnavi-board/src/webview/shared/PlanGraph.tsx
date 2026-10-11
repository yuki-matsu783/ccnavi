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
 * （呼び手が計画を作り直して渡し直す。番号の振り直しの正は実行ファイル）。
 *
 * 見た目は `PlanGraph.css`。React Flow の CSS は `--xy-*` の変数で出来ているので、そこを `--vscode-*` で上書きする。
 */
import { useCallback, useMemo, type JSX } from "react";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Connection, type Edge, type Node, type NodeProps, type NodeTypes } from "@xyflow/react";

import type { ApprovePlan } from "../../core/approvemodel.js";
import { canConnect, planGraphOf, type PlanGraph as Graph, type PlanGraphNode } from "../../core/plan-graph.js";

interface ItemData extends Record<string, unknown> {
  readonly node: PlanGraphNode;
  readonly editable: boolean;
}

type ItemNode = Node<ItemData, "planItem">;
type EndNode = Node<ItemData, "planEnd">;
type PlanNode = ItemNode | EndNode;

/** 見る場所の言葉。知らない表記はそのまま出す */
function reviewText(review: string): string {
  if (review === "none") {
    return "レビューなし";
  }
  return review === "" ? "レビュー ?" : `レビュー ${review}`;
}

/** 項の点。番号・定義の題・見る場所と、延期・固定・終端に当たらない印 */
function ItemView({ data }: NodeProps<ItemNode>): JSX.Element {
  const { node, editable } = data;
  const title = [
    node.type,
    node.loose ? "最後の項がこの項を待っていない（終端に当たらない）" : "",
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
        <span className="tag" data-review={node.review}>
          {reviewText(node.review)}
        </span>
        {node.deferred && <span className="tag deferred">{node.reviewAt === null ? "延期（引き受け手なし）" : `延期 → ${node.reviewAt}`}</span>}
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

// 描くたびに作り直すと React Flow が「点の種類が変わった」と言って組み直す。1 度だけ作る
const NODE_TYPES: NodeTypes = { planItem: ItemView, planEnd: EndView };

function nodesOf(graph: Graph, editable: boolean): PlanNode[] {
  return graph.nodes.map((node) => ({
    id: node.id,
    type: node.kind === "item" ? ("planItem" as const) : ("planEnd" as const),
    position: { x: node.x, y: node.y },
    data: { node, editable },
  }));
}

function edgesOf(graph: Graph): Edge[] {
  return graph.edges.map((edge) => {
    const color = edge.loose ? "var(--vscode-editorWarning-foreground)" : "var(--vscode-descriptionForeground)";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      ...(edge.kind === "end" ? { targetHandle: edge.loose ? END_LOOSE : END_IN } : {}),
      className: ["plan-edge", `plan-${edge.kind}`, edge.loose ? "loose" : ""].filter((name) => name !== "").join(" "),
      markerEnd: { type: MarkerType.ArrowClosed, color },
      // start と end への線は図が引くもので、消せない
      deletable: false,
    };
  });
}

/** 計画の見出し */
function headingOf(plan: ApprovePlan): string {
  return `${plan.ticket} の${plan.part === "feedback" ? "フィードバック計画（feedback:）" : "全体計画（plan:）"}`;
}

/** 図の下の一言。実行ファイルの値を並べるだけ */
function Notes({ plan }: { readonly plan: ApprovePlan }): JSX.Element | null {
  const deferred = plan.items.filter((item) => item.deferred).map((item) => `${item.number} → ${item.review_at ?? "なし"}`);
  const lines: { readonly text: string; readonly warn: boolean }[] = [];
  if (plan.ready.length > 0) {
    lines.push({ text: `すぐ始まる: ${plan.ready.join(", ")}`, warn: false });
  }
  if (deferred.length > 0) {
    lines.push({ text: `延期: ${deferred.join("、")}（レビューを引き受ける番号）`, warn: false });
  }
  if (plan.loose.length > 0) {
    lines.push({ text: `最後の項がこの項を待っていない: ${plan.loose.join(", ")}。最後の項へ線を引くか、合流の項を置く`, warn: true });
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

export function PlanGraph({ plan, editable = false, onConnect }: { readonly plan: ApprovePlan; readonly editable?: boolean; readonly onConnect?: PlanConnect }): JSX.Element {
  const graph = useMemo(() => planGraphOf(plan), [plan]);
  const nodes = useMemo(() => nodesOf(graph, editable), [graph, editable]);
  const edges = useMemo(() => edgesOf(graph), [graph]);
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

  return (
    <section className="plan-graph" data-part={plan.part} data-ticket={plan.ticket} data-editable={editable ? "1" : "0"}>
      <h3>{headingOf(plan)}</h3>
      <div className="graph plan-graph-canvas" data-nodes={graph.nodes.length} data-edges={graph.edges.length}>
        <ReactFlow<PlanNode, Edge>
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesDraggable={false}
          nodesConnectable={editable}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          isValidConnection={isValidConnection}
          onConnect={handleConnect}
          fitView
          ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "拡大", "controls.zoomOut.ariaLabel": "縮小", "controls.fitView.ariaLabel": "全体を表示" }}
          minZoom={0.2}
          maxZoom={1.6}
          // 線と点を消すキーは受けない（読むだけのときに消えたように見えないように）
          deleteKeyCode={null}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <Notes plan={plan} />
    </section>
  );
}
