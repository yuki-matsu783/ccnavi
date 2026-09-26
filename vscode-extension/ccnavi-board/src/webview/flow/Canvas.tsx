/**
 * フローの図。点がノード、線が `connections`。見た目は cc-wf-studio に寄せてあるが、コードは
 * 使っていない（あちらは AGPL。ここは React Flow の部品を組んだだけ）。
 *
 * 図が持つのは描き方だけで、フローの中身は持たない。動かす・繋ぐ・選ぶは `onMove` などで
 * 呼び手（`App.tsx`）に返し、呼び手が `core/flow-doc.ts` の関数で写しを作り直して戻す。
 *
 * **点の位置は React Flow の手元（`nodes`）で動かし、放したときだけ写しに書く。** ドラッグの間に
 * 写しを作り直すと、放すまでに何十回も「未保存」を立て直すことになる。写しが替わったら手元を
 * 作り直すが、測った大きさ（`measured`）は引き継ぐ（引き継がないと点が一瞬消える）。
 *
 * 見た目は `Canvas.css`。
 */
import { useCallback, useEffect, useMemo, useState, type JSX } from "react";
import {
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";

import {
  connectionFrom,
  connectionFromPort,
  connectionLabel,
  connectionsOf,
  connectionTo,
  connectionToPort,
  INPUT_PORT,
  isEditableType,
  nodeName,
  nodePosition,
  nodeType,
  OUTPUT_PORT,
  portsOf,
  TYPE_LABELS,
  type FlowDoc,
  type FlowPoint,
  type PortInfo,
} from "../../core/flow-doc.js";
import { badgeOf, summaryOf, type Badge } from "./text.js";

/** いま選んでいるもの。線は並びの位置で指す（id が無い線もある） */
export type Selection = { readonly kind: "node"; readonly id: string } | { readonly kind: "edge"; readonly index: number };

interface StepData extends Record<string, unknown> {
  readonly type: string;
  readonly name: string;
  readonly summary: string;
  readonly badge?: Badge;
  readonly inputs: readonly PortInfo[];
  readonly outputs: readonly PortInfo[];
  readonly known: boolean;
  readonly readOnly: boolean;
}

type StepNode = Node<StepData, "step">;

/** 種類を CSS の綴りにする（知らない種類は 1 つにまとめる） */
function cssType(type: string, known: boolean): string {
  return known ? type : "other";
}

function StepView({ data }: NodeProps<StepNode>): JSX.Element {
  const many = data.outputs.length > 1 || data.outputs.some((port) => port.label !== "");
  return (
    <div className="flow-node" data-type={cssType(data.type, data.known)} title={data.known ? undefined : `この画面で欄を持たない種類（${data.type || "種類なし"}）。中身は保存してもそのまま残る`}>
      {data.inputs.map((port, index) => (
        <Handle
          key={`in-${port.id}`}
          id={port.id}
          type="target"
          position={Position.Left}
          isConnectable={!data.readOnly}
          style={data.inputs.length > 1 ? { top: `${((index + 1) * 100) / (data.inputs.length + 1)}%` } : undefined}
        />
      ))}
      <span className="flow-node-type">{TYPE_LABELS[data.type] ?? (data.type || "種類なし")}</span>
      <span className="flow-node-name">{data.name}</span>
      {data.summary !== "" && <span className="flow-node-summary">{data.summary}</span>}
      {data.badge !== undefined && (
        <span className={`flow-badge ${data.badge.kind}`} title={data.badge.title}>
          {data.badge.text}
        </span>
      )}
      {many ? (
        <ul className="flow-ports">
          {data.outputs.map((port) => (
            <li key={port.id} className="flow-port" data-port={port.id}>
              <span>{port.label || port.id}</span>
              <Handle id={port.id} type="source" position={Position.Right} isConnectable={!data.readOnly} />
            </li>
          ))}
        </ul>
      ) : (
        data.outputs.map((port) => <Handle key={`out-${port.id}`} id={port.id} type="source" position={Position.Right} isConnectable={!data.readOnly} />)
      )}
    </div>
  );
}

// 描くたびに作り直すと React Flow が「点の種類が変わった」と言って組み直す。1 度だけ作る
const NODE_TYPES: NodeTypes = { step: StepView };

function stepsOf(doc: FlowDoc, selected: Selection | undefined, readOnly: boolean): StepNode[] {
  const connections = connectionsOf(doc);
  return doc.nodes.map((node, index) => {
    const type = nodeType(node);
    const ports = portsOf(node, connections);
    return {
      id: node.id,
      type: "step" as const,
      position: nodePosition(node, index),
      selected: selected?.kind === "node" && selected.id === node.id,
      data: {
        type,
        name: nodeName(node) || node.id,
        summary: summaryOf(node),
        badge: badgeOf(type),
        inputs: ports.inputs,
        outputs: ports.outputs,
        known: isEditableType(type),
        readOnly,
      },
    };
  });
}

function edgesOf(doc: FlowDoc, selected: Selection | undefined): Edge[] {
  const ids = new Set(doc.nodes.map((node) => node.id));
  const edges: Edge[] = [];
  connectionsOf(doc).forEach((c, index) => {
    // 行き先の無い線は描けない（React Flow が落とす）。写しには残る
    if (!ids.has(connectionFrom(c)) || !ids.has(connectionTo(c))) {
      return;
    }
    const label = connectionLabel(doc, c);
    edges.push({
      id: `e-${index}`,
      source: connectionFrom(c),
      target: connectionTo(c),
      sourceHandle: connectionFromPort(c),
      targetHandle: connectionToPort(c),
      label: label === "" ? undefined : label,
      selected: selected?.kind === "edge" && selected.index === index,
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--vscode-descriptionForeground)" },
      data: { index },
    });
  });
  return edges;
}

export interface CanvasProps {
  readonly doc: FlowDoc;
  readonly readOnly: boolean;
  readonly selected: Selection | undefined;
  readonly onSelect: (selection: Selection | undefined) => void;
  readonly onMove: (id: string, position: FlowPoint) => void;
  readonly onConnect: (from: string, fromPort: string, to: string, toPort: string) => void;
}

export function Canvas({ doc, readOnly, selected, onSelect, onMove, onConnect }: CanvasProps): JSX.Element {
  const base = useMemo(() => stepsOf(doc, selected, readOnly), [doc, selected, readOnly]);
  const edges = useMemo(() => edgesOf(doc, selected), [doc, selected]);
  const [nodes, setNodes] = useState<StepNode[]>(base);

  // 写しが替わったら手元を作り直す。測った大きさは引き継ぐ
  useEffect(() => {
    setNodes((now) => {
      const measured = new Map(now.map((node) => [node.id, node.measured]));
      return base.map((node) => {
        const size = measured.get(node.id);
        return size === undefined ? node : { ...node, measured: size };
      });
    });
  }, [base]);

  const onNodesChange = useCallback((changes: NodeChange<StepNode>[]) => {
    // 消す・足すは写しの側でしかしない（Delete の鍵も受けない）。ここで受けるのは大きさ・位置・選択
    setNodes((now) => applyNodeChanges(changes.filter((change) => change.type !== "remove" && change.type !== "add"), now));
  }, []);

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (readOnly) {
        return;
      }
      onConnect(connection.source, connection.sourceHandle ?? OUTPUT_PORT, connection.target, connection.targetHandle ?? INPUT_PORT);
    },
    [readOnly, onConnect],
  );

  return (
    <div className="graph flow-graph" id="flow-graph" data-nodes={doc.nodes.length} data-edges={edges.length} data-readonly={readOnly ? "1" : "0"}>
      <ReactFlow<StepNode, Edge>
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onNodeDragStop={(_event, node) => {
          if (!readOnly) {
            onMove(node.id, node.position);
          }
        }}
        onNodeClick={(_event, node) => onSelect({ kind: "node", id: node.id })}
        onEdgeClick={(_event, edge) => {
          const index = (edge.data as { index?: unknown } | undefined)?.index;
          if (typeof index === "number") {
            onSelect({ kind: "edge", index });
          }
        }}
        onPaneClick={() => onSelect(undefined)}
        onConnect={handleConnect}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        fitView
        ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "拡大", "controls.zoomOut.ariaLabel": "縮小", "controls.fitView.ariaLabel": "全体を表示" }}
        minZoom={0.2}
        maxZoom={1.8}
        // 消すのは右の欄のボタンだけ。Delete の鍵で消えると、読むだけのときにも消えたように見える
        deleteKeyCode={null}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
