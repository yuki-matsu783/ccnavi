/**
 * フロー編集画面の本体。帯・ツールバー・部品箱・図・右の欄。
 *
 * 見せる中身は拡張ホストが渡す（`FlowData`）。画面が持つのは、人が触って決めるもの（編集中のフロー、
 * 選んでいるもの、直前の操作の一言）だけ。
 *
 * **着手中かは画面が決めない。** 錠（`FlowLock`）は実行ファイルの答え（`flow.locked`）の写しで、
 * 拡張ホストが渡す。錠が掛かっている間は読むだけ（欄・部品箱・保存が止まる）。
 * 保存を押したときも、拡張ホストが実行ファイルに聞き直してから書く（ADR-0085）。
 *
 * **中身（`data`）が届いたら、編集中のフローはその中身で置き換える。** 届くのは編集を捨ててよいとき
 * だけ（再読込・保存が通った）。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";

import {
  absolutePosition,
  addNode,
  connect,
  flowNotices,
  groupNodes,
  isGroup,
  nodeSize,
  PALETTE,
  placeNodes,
  removeConnectionAt,
  removeNode,
  resizeGroup,
  TYPE_LABELS,
  type FlowDoc,
  type PaletteType,
} from "../../core/flow-doc.js";
import type { FlowData, FlowLock, FlowPage, ToFlow } from "../../core/flow-view.js";
import { applyAppearance } from "../appearance.js";
import { Tour, TourButton, useTour, type TourStep } from "../Tour.js";
import { Canvas, type Selection } from "./Canvas.js";
import { Inspector } from "./Inspector.js";
import { post } from "./post.js";
import { badgeOf } from "./text.js";

/** 中身が読めなかったときの錠。画面は保存させない */
const NO_LOCK: FlowLock = { locked: true, reason: "" };

interface Status {
  readonly text: string;
  readonly error: boolean;
}

function pageOf(data: FlowData): FlowPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

/**
 * 足したノードを置く場所。いちばん下の縁（図の上の位置で読む。グループはその枠の下の縁）のさらに下。
 * グループの枠の中に落ちないよう、グループの下に置く（足したノードはどのグループにも入らない）
 */
function nextSpot(doc: FlowDoc): { x: number; y: number } {
  if (doc.nodes.length === 0) {
    return { x: 80, y: 80 };
  }
  const spots = doc.nodes.map((node) => {
    const at = absolutePosition(doc, node.id);
    return { x: at.x, bottom: at.y + nodeSize(node).height };
  });
  const low = spots.reduce((max, spot) => (spot.bottom > max.bottom ? spot : max), spots[0]);
  return { x: low.x, y: low.bottom + 50 };
}

/** 並びが同じか（選んだノードの id を毎回作り直さないため） */
function sameIds(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

const TOUR_STEPS: readonly TourStep[] = [
  {
    target: "#flow-palette",
    title: "部品箱",
    body: "押すとノードが図に足される。利用者に聞く（askUserQuestion）ノードでは、担当のサブエージェントは手を止めてメインに返す。サブエージェントのノードは入れ子で起こし、上限ならメインに返す。",
  },
  {
    target: "#flow-graph",
    title: "図",
    body:
      "ノードの右の点から次のノードの左の点へ引くと線が繋がる。ノードや線を押すと、右の欄で中身を直せる。ノードや線に載せると出る × で消せる。" +
      "Shift を押しながらノードを押す（何も無いところを引いて囲む）といくつも選べ、「グループ化」で枠にまとめられる。枠の中へ引いたノードは枠に入り、外へ引くと出る。",
  },
  {
    target: "#inspector",
    title: "欄",
    body: "選んだノードの中身（プロンプト・問いと選択肢・分岐の条件など）を直す。画面が知らない種類は、名前だけ直せて中身はそのまま残る。",
  },
  {
    target: "#save",
    title: "保存",
    body: "保存の直前に、子チケットが着手中でないかを ccnavi に聞き直す。着手中なら書かない（担当のサブエージェントが読んでいる手順が途中で変わるのを防ぐ）。",
  },
];

export function App({ initial }: { readonly initial: FlowData }): JSX.Element {
  const [data, setData] = useState<FlowData>(initial);
  const [doc, setDoc] = useState<FlowDoc | undefined>(() => pageOf(initial)?.doc);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [lock, setLock] = useState<FlowLock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  const [changed, setChanged] = useState(false);
  const [selected, setSelected] = useState<Selection | undefined>(undefined);
  const [picked, setPicked] = useState<readonly string[]>([]);
  // 図の外で選んだノード（部品箱で足した・グループ化で作った）。図はこれが替わったときだけ、それ 1 つを選び直す
  const [focus, setFocus] = useState<{ readonly id?: string } | undefined>(undefined);
  const tour = useTour(data.kind === "page", { onEnd: () => post({ type: "tourDone" }) });
  const requestTour = tour.request;

  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToFlow> & { data?: FlowData; lock?: FlowLock; message?: string; value?: unknown };
      if (message.type === "data" && message.data !== undefined) {
        const next = message.data;
        setData(next);
        setDoc(pageOf(next)?.doc);
        setDirty(false);
        setBusy(false);
        setStatus(undefined);
        setChanged(false);
        setSelected(undefined);
        setPicked([]);
        setFocus({});
        setLock(pageOf(next)?.lock ?? NO_LOCK);
      } else if (message.type === "failed") {
        setBusy(false);
        setStatus({ text: String(message.message ?? ""), error: true });
      } else if (message.type === "lock" && message.lock !== undefined) {
        setLock(message.lock);
      } else if (message.type === "changed") {
        setChanged(true);
      } else if (message.type === "cancelled") {
        setBusy(false);
        setStatus(undefined);
      } else if (message.type === "tour") {
        requestTour();
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    post({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, [requestTour]);

  const sentDirty = useRef(false);
  useEffect(() => {
    if (sentDirty.current !== dirty) {
      sentDirty.current = dirty;
      post({ type: "dirty", dirty });
    }
  }, [dirty]);

  // 図で選んでいるノードが変わった。**毎回同じ関数を渡す**（React Flow は onSelectionChange が替わるたびに
  // その時の選びで呼び直すので、描くたびに作り直すと、押した直後の古い選びで呼ばれる）
  const pick = useCallback((ids: readonly string[]): void => {
    setPicked((now) => (sameIds(now, ids) ? now : ids));
    // Shift を押しながら選んでいたノードを押すと、React Flow はそれを選びから外す。右の欄がそのノードの
    // ままにならないよう、残った選びの最後のノード（残っていなければ何も無い）に替える
    setSelected((now) => (now?.kind !== "node" || ids.includes(now.id) ? now : ids.length === 0 ? undefined : { kind: "node", id: ids[ids.length - 1] }));
  }, []);

  const notices = useMemo(() => (doc === undefined ? [] : flowNotices(doc)), [doc]);

  if (data.kind === "loading") {
    return (
      <p className="empty" id="ccnavi-loading">
        {data.text}
      </p>
    );
  }
  if (data.kind === "error" || doc === undefined) {
    return (
      <>
        <p className="empty">フロー編集画面を読み直せなかった。原因を直してから「再読込」を押す。</p>
        <pre className="load-error">{data.kind === "error" ? data.error : ""}</pre>
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          再読込
        </button>
      </>
    );
  }
  const page = data.page;
  const readOnly = lock.locked || busy;

  const edit = (next: FlowDoc): void => {
    if (lock.locked) {
      return;
    }
    setDoc(next);
    setDirty(true);
  };

  const reload = (): void => {
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

  const add = (type: PaletteType): void => {
    const added = addNode(doc, type, nextSpot(doc));
    edit(added.doc);
    setSelected({ kind: "node", id: added.id });
    setFocus({ id: added.id });
  };

  /** 消したものを選んでいたら、選ぶのをやめる（線は並びの位置で指すので、線を消したら線の選びは外す） */
  const removeNodeAt = (id: string): void => {
    edit(removeNode(doc, id));
    // ノードと一緒に線も消えて並びの位置がずれるので、線の選びも外す
    if ((selected?.kind === "node" && selected.id === id) || selected?.kind === "edge") {
      setSelected(undefined);
    }
  };
  const removeEdgeAt = (index: number): void => {
    edit(removeConnectionAt(doc, index));
    if (selected?.kind === "edge") {
      setSelected(undefined);
    }
  };

  // グループ化できるのは、グループでないノードを 2 つ以上選んでいるとき
  const groupable = picked.filter((id) => doc.nodes.some((node) => node.id === id && !isGroup(node)));
  const group = (): void => {
    const grouped = groupNodes(doc, groupable);
    if (grouped === undefined) {
      return;
    }
    edit(grouped.doc);
    setSelected({ kind: "node", id: grouped.id });
    setFocus({ id: grouped.id });
  };

  return (
    <>
      {lock.locked && (
        <div id="lock" className="lock" role="status">
          {lock.reason === "" ? "着手中かを確かめられないので、読むだけにしている。" : lock.reason}
        </div>
      )}
      <div id="changed" className={changed ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。再読込してください。
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
          再読込
        </button>
      </div>
      <header className="toolbar">
        <div className="summary">
          <span className="flow-ticket">
            <strong>{page.ticket}</strong> {page.title}
          </span>
          <span className="path" title={`${page.flowRel}（承認済みの領域。書くのは人だけで、コミットも人がする）`}>
            {page.flowPath}
          </span>
          {!page.exists && <span className="dim">（まだ無い。保存すると作る）</span>}
          <span id="dirty" className={dirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <button
            type="button"
            className="action"
            data-action="group-nodes"
            disabled={readOnly || groupable.length < 2}
            title="Shift を押しながらノードを 2 つ以上選ぶと、枠（グループ）にまとめられる。枠は図の上の囲みで、手順は変わらない"
            onClick={group}
          >
            グループ化
          </button>
          <button type="button" className="action" data-action="open-flow" disabled={!page.exists} onClick={() => post({ type: "openFile" })}>
            エディタで開く
          </button>
          <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
            再読込
          </button>
          <button
            type="button"
            className="action primary"
            id="save"
            data-action="save"
            disabled={lock.locked || busy || (!dirty && page.exists)}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "着手中でないかを確かめて保存中…", error: false });
              post({ type: "save", doc });
            }}
          >
            保存
          </button>
        </div>
        <TourButton onClick={tour.start} />
      </header>
      {notices.length > 0 && (
        <ul className="problems" id="flow-notices">
          {notices.map((notice) => (
            <li key={notice}>{notice}</li>
          ))}
        </ul>
      )}
      <div className="flow-body">
        <nav className="flow-palette" id="flow-palette" aria-label="部品箱">
          <h2>部品</h2>
          {PALETTE.map((type) => {
            const badge = badgeOf(type);
            return (
              <button key={type} type="button" className="action small palette-item" data-action="add-node" data-type={type} disabled={readOnly} title={badge?.title} onClick={() => add(type)}>
                {TYPE_LABELS[type]}
                {badge !== undefined && <span className={`flow-badge ${badge.kind}`}>{badge.kind === "ask" ? "戻る" : "入れ子"}</span>}
              </button>
            );
          })}
        </nav>
        <Canvas
          doc={doc}
          readOnly={readOnly}
          selected={selected}
          onSelect={setSelected}
          focus={focus}
          onPick={pick}
          onMove={(moves) => {
            // 押しただけ（動かしていない）なら未保存にしない（placeNodes が同じ写しを返す）
            const next = placeNodes(doc, moves);
            if (next !== doc) {
              edit(next);
            }
          }}
          onConnect={(from, fromPort, to, toPort) => edit(connect(doc, from, fromPort, to, toPort))}
          onRemoveNode={removeNodeAt}
          onRemoveEdge={removeEdgeAt}
          onResizeGroup={(id, size, position) => {
            // 縁を押しただけ（大きさが変わっていない）なら未保存にしない（resizeGroup が同じ写しを返す）
            const next = resizeGroup(doc, id, size, position);
            if (next !== doc) {
              edit(next);
            }
          }}
        />
        <Inspector doc={doc} selected={selected} readOnly={readOnly} onChange={edit} onSelect={setSelected} />
      </div>
      {tour.touring && <Tour steps={TOUR_STEPS} onClose={tour.end} />}
      <footer className={status?.error === true ? "foot error" : "foot"}>
        <span id="status">{status?.text ?? ""}</span>
      </footer>
    </>
  );
}
