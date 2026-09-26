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
 * グループ（`type: "group"`）は React Flow の親子で描く。中のノードに `parentId` を付け、位置は
 * グループからの位置のまま渡す。グループは点の並びの前に置き、ほかの点より奥に描く。
 * ドラッグを放したときに、どのグループに入るか・出るかは写しの側（`placeNodes`）が決める。
 *
 * ノードと線には × のボタンを付ける（ノードは右上、線は真ん中。載せた・選んだときだけ見える）。
 * 押すと呼び手に返すだけで、消すのは写しの側。読むだけのときは出さない。
 * Shift を押しながら押す・囲むと、いくつも選べる。選んだノードの id は `onPick` で返す（グループ化に使う）。
 *
 * 見た目は `Canvas.css`。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type JSX, type MouseEvent as ReactMouseEvent } from "react";
import {
  applyNodeChanges,
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  getBezierPath,
  Handle,
  MarkerType,
  NodeResizer,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type EdgeProps,
  type EdgeTypes,
  type Node,
  type NodeChange,
  type NodeProps,
  type NodeTypes,
  type OnSelectionChangeParams,
} from "@xyflow/react";

import {
  connectionFrom,
  connectionFromPort,
  connectionLabel,
  connectionsOf,
  connectionTo,
  connectionToPort,
  GROUP_MIN,
  groupOf,
  INPUT_PORT,
  isEditableType,
  isGroup,
  nodeName,
  nodePosition,
  nodeSize,
  nodeType,
  OUTPUT_PORT,
  portsOf,
  TYPE_LABELS,
  type FlowDoc,
  type FlowPoint,
  type FlowSize,
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

interface GroupData extends Record<string, unknown> {
  readonly name: string;
  readonly readOnly: boolean;
}

type GroupNode = Node<GroupData, "flowGroup">;
type FlowNodeView = StepNode | GroupNode;

interface EdgeData extends Record<string, unknown> {
  readonly index: number;
  readonly readOnly: boolean;
}

type FlowEdge = Edge<EdgeData, "flow">;

/**
 * 点と線の部品から呼び手へ返す口。部品は React Flow が描くので、props では渡せない。
 * `data` に関数を入れると写しが替わるたびに点を作り直すことになるので、context で渡す
 */
interface CanvasActions {
  readonly removeNode: (id: string) => void;
  readonly removeEdge: (index: number) => void;
  readonly resizeGroup: (id: string, size: FlowSize, position: FlowPoint) => void;
}

const NO_ACTIONS: CanvasActions = { removeNode: () => undefined, removeEdge: () => undefined, resizeGroup: () => undefined };
const Actions = createContext<CanvasActions>(NO_ACTIONS);

/** × のボタン。押しても選ばない（押した点・線を選ぶ前に止める）。`nodrag` `nopan` でドラッグも始めない */
function RemoveButton({ action, label, onRemove }: { readonly action: string; readonly label: string; readonly onRemove: () => void }): JSX.Element {
  const stop = (event: ReactMouseEvent): void => event.stopPropagation();
  return (
    <button
      type="button"
      className="flow-remove nodrag nopan"
      data-action={action}
      aria-label={label}
      title={label}
      onMouseDown={stop}
      onPointerDown={stop}
      onDoubleClick={stop}
      onClick={(event) => {
        event.stopPropagation();
        onRemove();
      }}
    >
      ×
    </button>
  );
}

/** 種類を CSS の綴りにする（知らない種類は 1 つにまとめる） */
function cssType(type: string, known: boolean): string {
  return known ? type : "other";
}

function StepView({ id, data }: NodeProps<StepNode>): JSX.Element {
  const actions = useContext(Actions);
  const many = data.outputs.length > 1 || data.outputs.some((port) => port.label !== "");
  return (
    <div className="flow-node" data-type={cssType(data.type, data.known)} title={data.known ? undefined : `この画面で欄を持たない種類（${data.type || "種類なし"}）。中身は保存してもそのまま残る`}>
      {!data.readOnly && <RemoveButton action="canvas-remove-node" label="このノードを消す" onRemove={() => actions.removeNode(id)} />}
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

/**
 * グループ。点線の枠と名前だけで、出入口は持たない。選んでいるときは縁を引いて大きさを変えられ、
 * 放したときに大きさ（左や上の辺なら位置も）を呼び手に返す
 */
function GroupView({ id, data, selected }: NodeProps<GroupNode>): JSX.Element {
  const actions = useContext(Actions);
  return (
    <>
      <NodeResizer
        isVisible={selected && !data.readOnly}
        minWidth={GROUP_MIN.width}
        minHeight={GROUP_MIN.height}
        color="var(--vscode-focusBorder)"
        onResizeEnd={(_event, params) => actions.resizeGroup(id, { width: params.width, height: params.height }, { x: params.x, y: params.y })}
      />
      <div className="flow-group" data-type="group">
        <span className="flow-group-label">{data.name}</span>
      </div>
      {!data.readOnly && <RemoveButton action="canvas-remove-node" label="このグループを消す（中のノードは残す）" onRemove={() => actions.removeNode(id)} />}
    </>
  );
}

/** 載せている間だけ真。外れてから少し待って偽にする（線から × のボタンへ移る間に消さない） */
function useHover(): readonly [boolean, { readonly onMouseEnter: () => void; readonly onMouseLeave: () => void }] {
  const [hover, setHover] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const handlers = useMemo(
    () => ({
      onMouseEnter: () => {
        clearTimeout(timer.current);
        setHover(true);
      },
      onMouseLeave: () => {
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setHover(false), 150);
      },
    }),
    [],
  );
  return [hover, handlers];
}

/** 線。矢印と言葉は React Flow の既定の線と同じに描き、真ん中に × のボタンを置く */
function FlowEdgeView({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, style, label, selected, data, interactionWidth }: EdgeProps<FlowEdge>): JSX.Element {
  const actions = useContext(Actions);
  const [hover, handlers] = useHover();
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const shown = data !== undefined && !data.readOnly && (hover || selected === true);
  // 言葉があれば、その下にずらして重ねない
  const offset = label === undefined || label === "" ? 0 : 20;
  return (
    <>
      <g {...handlers}>
        <BaseEdge path={path} markerEnd={markerEnd} style={style} label={label} labelX={labelX} labelY={labelY} interactionWidth={interactionWidth} />
      </g>
      {shown && (
        <EdgeLabelRenderer>
          <div className="flow-edge-tools nodrag nopan" style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY + offset}px)` }} {...handlers}>
            <RemoveButton action="canvas-remove-edge" label="この線を消す" onRemove={() => actions.removeEdge(data.index)} />
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

// 描くたびに作り直すと React Flow が「点の種類が変わった」と言って組み直す。1 度だけ作る
const NODE_TYPES: NodeTypes = { step: StepView, flowGroup: GroupView };
const EDGE_TYPES: EdgeTypes = { flow: FlowEdgeView };

/** グループを奥に描く。選んでも（React Flow は選んだ点を 1000 だけ手前に上げる）ほかのノードより奥に留める */
const GROUP_Z = -2000;

function stepsOf(doc: FlowDoc, readOnly: boolean): FlowNodeView[] {
  const connections = connectionsOf(doc);
  const views = doc.nodes.map((node, index): FlowNodeView => {
    const type = nodeType(node);
    if (isGroup(node)) {
      const size = nodeSize(node);
      return {
        id: node.id,
        type: "flowGroup" as const,
        position: nodePosition(node, index),
        width: size.width,
        height: size.height,
        zIndex: GROUP_Z,
        data: { name: nodeName(node) || String((node.data as { label?: unknown } | undefined)?.label ?? "") || node.id, readOnly },
      };
    }
    const ports = portsOf(node, connections);
    const group = groupOf(doc, node);
    return {
      id: node.id,
      type: "step" as const,
      position: nodePosition(node, index),
      ...(group === undefined ? {} : { parentId: group.id }),
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
  // 親（グループ）を子より前に並べる（React Flow は前から読んで親を探す）
  return [...views.filter((view) => view.type === "flowGroup"), ...views.filter((view) => view.type !== "flowGroup")];
}

function edgesOf(doc: FlowDoc, selected: Selection | undefined, readOnly: boolean): FlowEdge[] {
  // 行き先の無い線と、グループに繋がる線は描けない（グループは出入口を持たない）。写しには残る
  const ids = new Set(doc.nodes.filter((node) => !isGroup(node)).map((node) => node.id));
  const edges: FlowEdge[] = [];
  connectionsOf(doc).forEach((c, index) => {
    if (!ids.has(connectionFrom(c)) || !ids.has(connectionTo(c))) {
      return;
    }
    const label = connectionLabel(doc, c);
    edges.push({
      id: `e-${index}`,
      type: "flow",
      source: connectionFrom(c),
      target: connectionTo(c),
      sourceHandle: connectionFromPort(c),
      targetHandle: connectionToPort(c),
      label: label === "" ? undefined : label,
      selected: selected?.kind === "edge" && selected.index === index,
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--vscode-descriptionForeground)" },
      data: { index, readOnly },
    });
  });
  return edges;
}

export interface CanvasProps {
  readonly doc: FlowDoc;
  readonly readOnly: boolean;
  readonly selected: Selection | undefined;
  readonly onSelect: (selection: Selection | undefined) => void;
  /** 図で選んでいるノードの id が変わった（Shift で選び足したものも含む） */
  readonly onPick: (ids: readonly string[]) => void;
  /**
   * 図の外で選んだノード（部品箱で足したものなど）。これが替わったときだけ、そのノード 1 つを選び直す
   * （`id` が無ければ選びを全部外す。中身を読み直したとき）。
   * 図で押したノードは React Flow が選ぶ（Shift での選び足し・外しもそのまま）ので、ここには来ない
   */
  readonly focus?: { readonly id?: string } | undefined;
  /** ドラッグを放した点。位置は React Flow の決まり（グループの中のノードはグループからの位置） */
  readonly onMove: (moves: readonly { readonly id: string; readonly position: FlowPoint }[]) => void;
  readonly onConnect: (from: string, fromPort: string, to: string, toPort: string) => void;
  readonly onRemoveNode: (id: string) => void;
  readonly onRemoveEdge: (index: number) => void;
  readonly onResizeGroup: (id: string, size: FlowSize, position: FlowPoint) => void;
}

export function Canvas({ doc, readOnly, selected, focus, onSelect, onPick, onMove, onConnect, onRemoveNode, onRemoveEdge, onResizeGroup }: CanvasProps): JSX.Element {
  const base = useMemo(() => stepsOf(doc, readOnly), [doc, readOnly]);
  const edges = useMemo(() => edgesOf(doc, selected, readOnly), [doc, selected, readOnly]);
  const [nodes, setNodes] = useState<FlowNodeView[]>(base);

  // 写しが替わったら手元を作り直す。測った大きさと、選んでいるか（Shift で選び足したものも）は引き継ぐ。
  // 呼び手が図の外から選んだノード（`focus`。部品箱で足したもの）は、それが替わったときだけ、それ 1 つを選ぶ。
  // 図で押したノードは React Flow が選んでいる（Shift で外したものも）ので、ここでは選び直さない
  const focused = useRef(focus);
  useEffect(() => {
    const fresh = focused.current !== focus;
    focused.current = focus;
    setNodes((now) => {
      const before = new Map(now.map((node) => [node.id, node]));
      return base.map((node) => {
        const old = before.get(node.id);
        const chosen = fresh && focus !== undefined ? node.id === focus.id : old?.selected === true;
        return { ...node, selected: chosen, ...(old?.measured === undefined ? {} : { measured: old.measured }) };
      });
    });
  }, [base, focus]);

  const onNodesChange = useCallback((changes: NodeChange<FlowNodeView>[]) => {
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

  const handleSelection = useCallback(({ nodes: chosenNodes }: OnSelectionChangeParams) => onPick(chosenNodes.map((node) => node.id)), [onPick]);

  const actions = useMemo<CanvasActions>(
    () => (readOnly ? NO_ACTIONS : { removeNode: onRemoveNode, removeEdge: onRemoveEdge, resizeGroup: onResizeGroup }),
    [readOnly, onRemoveNode, onRemoveEdge, onResizeGroup],
  );

  return (
    <div className="graph flow-graph" id="flow-graph" data-nodes={doc.nodes.length} data-edges={edges.length} data-readonly={readOnly ? "1" : "0"}>
      <Actions.Provider value={actions}>
        <ReactFlow<FlowNodeView, FlowEdge>
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          onNodesChange={onNodesChange}
          onNodeDragStop={(_event, _node, dragged) => {
            if (!readOnly) {
              onMove(dragged.map((node) => ({ id: node.id, position: node.position })));
            }
          }}
          onNodeClick={(_event, node) => onSelect({ kind: "node", id: node.id })}
          onEdgeClick={(_event, edge) => {
            const index = edge.data?.index;
            if (typeof index === "number") {
              onSelect({ kind: "edge", index });
            }
          }}
          onPaneClick={() => onSelect(undefined)}
          onSelectionChange={handleSelection}
          onConnect={handleConnect}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          fitView
          ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "拡大", "controls.zoomOut.ariaLabel": "縮小", "controls.fitView.ariaLabel": "全体を表示" }}
          minZoom={0.2}
          maxZoom={1.8}
          // Shift を押しながら押すと選び足し、Shift を押しながら何も無いところを引くと囲んで選ぶ
          selectionKeyCode="Shift"
          multiSelectionKeyCode={["Shift", "Meta", "Control"]}
          // 消すのは × のボタンと右の欄のボタンだけ。Delete の鍵で消えると、読むだけのときにも消えたように見える
          deleteKeyCode={null}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </Actions.Provider>
    </div>
  );
}
