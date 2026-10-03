/**
 * フロー編集画面の本体。帯・ツールバー・部品箱・図・右の欄。
 *
 * 見せる中身は拡張ホストが渡す（`FlowData`）。画面が持つのは、人が触って決めるもの（編集中のフロー、
 * 選んでいるもの、直前の操作の一言、元に戻す履歴、写したノード）だけ。
 *
 * **着手中かは画面が決めない。** 錠（`FlowLock`）は実行ファイルの答え（`flow.locked`）のままで、
 * 拡張ホストが渡す。錠が掛かっている間は読むだけ（欄・部品箱・保存・元に戻す・貼る が止まる）。
 * 保存を押したときも、拡張ホストが実行ファイルに聞き直してから書く（ADR-0085）。
 *
 * **中身（`data`）が届いたら、編集中のフローはその中身で置き換える。** 届くのは編集を捨ててよいとき
 * だけ（再読込・保存が通った）。履歴もそこで空にする。
 *
 * **編集中の内容を替えるのは `edit()` だけ。** 直す前の内容を履歴（`core/flow-history.ts`）に積んでから替える。
 * 「未保存」は、読み込んだ中身（`base`）と見比べて決める（`core/flow-diff.ts` の `sameFlow`）ので、
 * 元に戻して読み込んだときと同じ中身になれば消える。
 *
 * **実行ファイルが言ったこと（`FlowChecks`）は画面で作らない。** 開くときの答えは中身と一緒に届き、編集したら
 * 止まってから（`CHECK_MS`）拡張ホストに確かめ直しを頼む（`check` → `checked`。書きはしない）。答えの
 * warn は画面の注意と並べて出し、渡る手順は右の列のプレビュー、候補の名前は右の欄の選択肢に使う。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";

import { diffFlows, sameFlow, type FlowDiff } from "../../core/flow-diff.js";
import {
  absolutePosition,
  addNode,
  connect,
  copyNodes,
  duplicateNodes,
  flowNotices,
  groupNodes,
  isGroup,
  nodeSize,
  PALETTE,
  PASTE_OFFSET,
  pasteNodes,
  placeNodes,
  removeConnectionAt,
  removeNode,
  resizeGroup,
  TYPE_LABELS,
  type FlowClip,
  type FlowDoc,
  type PaletteType,
} from "../../core/flow-doc.js";
import { canRedo, canUndo, emptyHistory, record, redo, seal, undo, type FlowHistory } from "../../core/flow-history.js";
import type { FlowChecks, FlowData, FlowLock, FlowPage, ToFlow } from "../../core/flow-view.js";
import { applyAppearance } from "../appearance.js";
import { Tour, TourButton, useTour, type TourStep } from "../Tour.js";
import { getState, setState } from "../vscode.js";
import { Canvas, type Selection } from "./Canvas.js";
import { Inspector } from "./Inspector.js";
import { post } from "./post.js";
import { Preview } from "./Preview.js";
import { SaveReview } from "./SaveReview.js";
import { badgeOf } from "./text.js";

/** 中身が読めなかったときの錠。画面は保存させない */
const NO_LOCK: FlowLock = { locked: true, reason: "" };
/** 編集中の内容を拡張ホストへ保存させるまでの間（打つたびに送らない） */
const DRAFT_MS = 300;
/** 編集が止まってから実行ファイルに確かめ直させるまでの間 */
const CHECK_MS = 800;

interface Status {
  readonly text: string;
  readonly error: boolean;
}

function pageOf(data: FlowData): FlowPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

/** 画面が覚えておくもの（HTML を作り直しても残る）。いまはミニマップを出すかだけ */
function minimapShown(): boolean {
  try {
    const state = getState() as { minimap?: unknown } | undefined;
    return state?.minimap !== false;
  } catch {
    return true;
  }
}

function rememberMinimap(shown: boolean): void {
  try {
    const state = (getState() ?? {}) as Record<string, unknown>;
    setState({ ...state, minimap: shown });
  } catch {
    // 覚えられなくても画面は動く
  }
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

/** 配列が同じか（選んだノードの id を毎回作り直さないため） */
function sameIds(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

/**
 * いまの時刻（履歴のまとめに使う）。テストは `window.ccnaviClock` に関数を置いて時計を固定する
 * （実時間に頼ると、遅い機械で打ち込みのまとまりが切れる）
 */
function now(): number {
  const clock = (window as { ccnaviClock?: unknown }).ccnaviClock;
  return typeof clock === "function" ? Number((clock as () => number)()) : Date.now();
}

/** 押した鍵が欄の中か（欄の中の Ctrl+Z や Ctrl+C は欄に任せる） */
function inField(target: EventTarget | null): boolean {
  const element = target as { tagName?: unknown; isContentEditable?: unknown } | null;
  if (element === null || typeof element.tagName !== "string") {
    return false;
  }
  const tag = element.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || element.isContentEditable === true;
}

const TOUR_STEPS: readonly TourStep[] = [
  {
    target: "#flow-palette",
    title: "部品箱",
    body: "押すとノードが図に足される。利用者に聞く（askUserQuestion）ノードでは、担当のサブエージェントは手を止めてメインに返す。サブエージェントのノードは入れ子のサブエージェントとして起動し、入れ子の上限に当たったらメインに返す。",
  },
  {
    target: "#flow-graph",
    title: "図",
    body:
      "ノードの右の点から次のノードの左の点へ引くと線が繋がる（開始へ入る線と、終了から出る線は引けない）。ノードや線を押すと、右の欄で中身を直せる。ノードや線にポインタを載せると出る × で消せる。" +
      "Shift を押しながらノードを押す（何も無いところを引いて囲む）といくつも選べ、「グループ化」で枠にまとめられる。枠の中へ引いたノードは枠に入り、外へ引くと出る。" +
      "Ctrl+Z で元に戻し、Ctrl+Shift+Z（Ctrl+Y）でやり直す。選んだノードは Ctrl+C で写して Ctrl+V で貼り、Ctrl+D で複製する（開始は写さない）。",
  },
  {
    target: "#inspector",
    title: "欄",
    body: "選んだノードの中身（プロンプト・問いと選択肢・分岐の条件など）を直す。この画面に入力欄が無い種類は、名前だけ直せて中身はそのまま残る。",
  },
  {
    target: "#save",
    title: "保存",
    body: "読み込んだ時点からの変更（足した・消した・変えたノードと線）を一覧で見せてから保存する。保存の直前に、子チケットが着手中でないかを ccnavi に聞き直す。着手中なら書かない（担当のサブエージェントが読んでいる手順が途中で変わるのを防ぐ）。",
  },
];

export function App({ initial }: { readonly initial: FlowData }): JSX.Element {
  const [data, setData] = useState<FlowData>(initial);
  // 読み込んだ中身（未保存の見比べと、保存前の差分の元）と、編集中の内容
  const [base, setBase] = useState<FlowDoc | undefined>(() => pageOf(initial)?.doc);
  const [doc, setDoc] = useState<FlowDoc | undefined>(() => pageOf(initial)?.draft ?? pageOf(initial)?.doc);
  const [history, setHistory] = useState<FlowHistory>(emptyHistory);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [lock, setLock] = useState<FlowLock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  const [changed, setChanged] = useState(false);
  const [selected, setSelected] = useState<Selection | undefined>(undefined);
  const [picked, setPicked] = useState<readonly string[]>([]);
  // 図の外で選んだノード（部品箱で足した・グループ化で作った・貼った）。図はこれが替わったときだけ、それを選び直す
  const [focus, setFocus] = useState<{ readonly ids: readonly string[] } | undefined>(undefined);
  const [minimap, setMinimap] = useState(minimapShown);
  const [reviewSave, setReviewSave] = useState(() => pageOf(initial)?.reviewSave === true);
  const [review, setReview] = useState<FlowDiff | undefined>(undefined);
  // 写したノード（画面の中のクリップボード）と、同じものを何回貼ったか（貼るたびに少しずつずらす）
  const clip = useRef<{ readonly clip: FlowClip; pasted: number } | undefined>(undefined);
  const [hasClip, setHasClip] = useState(false);
  // 実行ファイルが言ったこと。`checkedDoc` は出している答えが指す内容、`pendingDoc` は頼んで答えを待っている内容、
  // `checkSeq` は最後に頼んだ確かめの番号。内容が替わるたびに番号を進め、待っていた答えは捨てる
  // （答えを待つ間に直すと、古い内容の答えを今の答えと取り違えるため）
  const [checks, setChecks] = useState<FlowChecks | undefined>(() => pageOf(initial)?.checks);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | undefined>(undefined);
  const checkedDoc = useRef<FlowDoc | undefined>(pageOf(initial)?.checks === undefined ? undefined : pageOf(initial)?.doc);
  const pendingDoc = useRef<FlowDoc | undefined>(undefined);
  const checkSeq = useRef(0);
  // 外で変わった知らせを受けているか。拡張ホストはタブを表に戻すたびに送り直すので、最初の 1 回だけ履歴を空にする
  const changedSeen = useRef(false);
  const tour = useTour(data.kind === "page", { onEnd: () => post({ type: "tourDone" }) });
  const requestTour = tour.request;

  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToFlow> & { data?: FlowData; lock?: FlowLock; message?: string; value?: unknown };
      if (message.type === "data" && message.data !== undefined) {
        const next = message.data;
        const page = pageOf(next);
        setData(next);
        setBase(page?.doc);
        setDoc(page?.draft ?? page?.doc);
        setHistory(emptyHistory());
        setBusy(false);
        setStatus(undefined);
        setChanged(false);
        changedSeen.current = false;
        setSelected(undefined);
        setPicked([]);
        setFocus({ ids: [] });
        setReview(undefined);
        setReviewSave(page?.reviewSave === true);
        setLock(page?.lock ?? NO_LOCK);
        // 頼んでいた確かめの答えは捨てる（番号を進める）
        checkSeq.current += 1;
        checkedDoc.current = page?.checks === undefined ? undefined : page.doc;
        pendingDoc.current = undefined;
        setChecks(page?.checks);
        setChecking(false);
        setCheckError(undefined);
      } else if (message.type === "failed") {
        setBusy(false);
        setStatus({ text: String(message.message ?? ""), error: true });
      } else if (message.type === "lock" && message.lock !== undefined) {
        setLock(message.lock);
      } else if (message.type === "changed") {
        // 外で変わった。いまの編集は残すが、戻す先はもう読み込んだ中身と合わないので履歴は空にする。
        // 送り直し（タブを表に戻した）では空にし直さない（その後に積んだ履歴を消さない）
        if (!changedSeen.current) {
          changedSeen.current = true;
          setChanged(true);
          setHistory(emptyHistory());
        }
      } else if (message.type === "cancelled") {
        setBusy(false);
        setStatus(undefined);
      } else if (message.type === "checked") {
        const answer = message as { seq?: unknown; checks?: FlowChecks; error?: string };
        const asked = pendingDoc.current;
        if (answer.seq !== checkSeq.current || asked === undefined) {
          return;
        }
        pendingDoc.current = undefined;
        checkedDoc.current = asked;
        setChecking(false);
        if (answer.checks !== undefined) {
          setChecks(answer.checks);
          setCheckError(undefined);
        } else {
          setCheckError(String(answer.error ?? ""));
        }
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

  const dirty = useMemo(() => doc !== undefined && base !== undefined && !sameFlow(doc, base), [doc, base]);

  const sentDirty = useRef(false);
  useEffect(() => {
    if (sentDirty.current !== dirty) {
      sentDirty.current = dirty;
      post({ type: "dirty", dirty });
    }
  }, [dirty]);

  // 未保存の内容を拡張ホストに保存させる（未保存のまま閉じられたら、開き直して戻せるように）。
  // 打つたびには送らず、止まってから送る。未保存でなくなったら保存したものを消させる
  const sentDraft = useRef(false);
  useEffect(() => {
    if (!dirty) {
      if (sentDraft.current) {
        sentDraft.current = false;
        post({ type: "draft", doc: null });
      }
      return;
    }
    const timer = setTimeout(() => {
      if (doc !== undefined) {
        sentDraft.current = true;
        post({ type: "draft", doc });
      }
    }, DRAFT_MS);
    return () => clearTimeout(timer);
  }, [doc, dirty]);

  // 図で選んでいるノードが変わった。**毎回同じ関数を渡す**（React Flow は onSelectionChange が替わるたびに
  // その時の選びで呼び直すので、描くたびに作り直すと、押した直後の古い選びで呼ばれる）
  const pick = useCallback((ids: readonly string[]): void => {
    setPicked((now) => (sameIds(now, ids) ? now : ids));
    // Shift を押しながら選んでいたノードを押すと、React Flow はそれを選びから外す。右の欄がそのノードの
    // ままにならないよう、残った選びの最後のノード（残っていなければ何も無い）に替える
    setSelected((now) => (now?.kind !== "node" || ids.includes(now.id) ? now : ids.length === 0 ? undefined : { kind: "node", id: ids[ids.length - 1] }));
  }, []);

  // 編集が止まったら実行ファイルに確かめ直させる。出している答えが指す内容と同じなら頼まない
  useEffect(() => {
    if (doc === undefined || pendingDoc.current === doc) {
      return;
    }
    // 内容が替わった。待っていた答えは古い内容のものなので捨てる（番号を進める）
    checkSeq.current += 1;
    pendingDoc.current = undefined;
    if (checkedDoc.current === doc) {
      // 答えが指す内容に戻った（元に戻すなど）。確かめ直さない
      setChecking(false);
      return;
    }
    setChecking(true);
    const timer = setTimeout(() => {
      checkSeq.current += 1;
      pendingDoc.current = doc;
      post({ type: "check", seq: checkSeq.current, doc });
    }, CHECK_MS);
    return () => clearTimeout(timer);
  }, [doc]);

  // 画面の注意と、実行ファイルの warn。実行ファイルの答えがあれば、同じことを言う画面の注意は出さない
  const notices = useMemo(() => (doc === undefined ? [] : flowNotices(doc, { exe: checks !== undefined })), [doc, checks]);

  // 鍵を受け取る側は 1 度だけ張り、中身は描くたびに最新へ差し替える
  const onKey = useRef<(event: KeyboardEvent) => void>(() => undefined);
  useEffect(() => {
    const listener = (event: KeyboardEvent): void => onKey.current(event);
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);

  if (data.kind === "loading") {
    onKey.current = () => undefined;
    return (
      <p className="empty" id="ccnavi-loading">
        {data.text}
      </p>
    );
  }
  if (data.kind === "error" || doc === undefined || base === undefined) {
    onKey.current = () => undefined;
    return (
      <>
        <p className="empty">フロー編集画面を読み込み直せなかった。原因を直してから「再読込」を押してください。</p>
        <pre className="load-error">{data.kind === "error" ? data.error : ""}</pre>
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          再読込
        </button>
      </>
    );
  }
  const page = data.page;
  const readOnly = lock.locked || busy;

  /**
   * 編集中の内容を替える唯一の入り口。直す前の内容を履歴に積む。`typing` は欄に打った文字のときの欄の名前で、
   * 同じ欄に続けて打ったものは 1 件にまとめる
   */
  const edit = (next: FlowDoc, typing?: string): void => {
    if (lock.locked || next === doc) {
      return;
    }
    const at = now();
    setHistory((history) => record(history, doc, typing === undefined ? {} : { key: typing, now: at }));
    setDoc(next);
  };

  /** 戻した・やり直した内容に無いものを選んでいたら外す（線はリストの位置で指すので、線の選びは外す） */
  const travel = (moved: { readonly history: FlowHistory; readonly doc: FlowDoc } | undefined): void => {
    if (moved === undefined || readOnly) {
      return;
    }
    setHistory(moved.history);
    setDoc(moved.doc);
    if (selected?.kind === "edge" || (selected?.kind === "node" && !moved.doc.nodes.some((node) => node.id === selected.id))) {
      setSelected(undefined);
    }
  };
  const undoEdit = (): void => travel(undo(history, doc));
  const redoEdit = (): void => travel(redo(history, doc));

  const reload = (): void => {
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

  const add = (type: PaletteType): void => {
    const added = addNode(doc, type, nextSpot(doc));
    edit(added.doc);
    setSelected({ kind: "node", id: added.id });
    setFocus({ ids: [added.id] });
  };

  /** 消したものを選んでいたら、選ぶのをやめる（線はリストの位置で指すので、線を消したら線の選びは外す） */
  const removeNodeAt = (id: string): void => {
    edit(removeNode(doc, id));
    // ノードと一緒に線も消えてリストの位置がずれるので、線の選びも外す
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
    setFocus({ ids: [grouped.id] });
  };

  // 写す・複製するのは、図で選んでいるノード（無ければ右の欄に出しているノード）
  const chosen = picked.length > 0 ? picked : selected?.kind === "node" ? [selected.id] : [];
  const copyable = copyNodes(doc, chosen) !== undefined;

  /** 貼った・複製したノードを選ぶ。右の欄は最後のノード */
  const choose = (ids: readonly string[]): void => {
    setFocus({ ids });
    setSelected(ids.length === 0 ? undefined : { kind: "node", id: ids[ids.length - 1] });
  };

  const copy = (): void => {
    const copied = copyNodes(doc, chosen);
    if (copied === undefined) {
      setStatus({ text: "写せるノードを選んでいない（開始は写さない）", error: false });
      return;
    }
    clip.current = { clip: copied, pasted: 0 };
    setHasClip(true);
    setStatus({ text: `ノードを ${copied.nodes.length} 個、線を ${copied.connections.length} 本写した。Ctrl+V で貼る`, error: false });
  };

  const paste = (): void => {
    const held = clip.current;
    if (held === undefined || readOnly) {
      return;
    }
    held.pasted += 1;
    const pasted = pasteNodes(doc, held.clip, { x: PASTE_OFFSET.x * held.pasted, y: PASTE_OFFSET.y * held.pasted });
    edit(pasted.doc);
    choose(pasted.ids);
    setStatus({ text: `ノードを ${pasted.ids.length} 個貼った`, error: false });
  };

  const duplicate = (): void => {
    if (readOnly) {
      return;
    }
    const made = duplicateNodes(doc, chosen);
    if (made === undefined) {
      setStatus({ text: "複製できるノードを選んでいない（開始は複製しない）", error: false });
      return;
    }
    edit(made.doc);
    choose(made.ids);
    setStatus({ text: `ノードを ${made.ids.length} 個複製した`, error: false });
  };

  const toggleMinimap = (): void => {
    const next = !minimap;
    setMinimap(next);
    rememberMinimap(next);
  };

  const sendSave = (): void => {
    setReview(undefined);
    setBusy(true);
    setStatus({ text: "着手中でないかを確かめて保存中…", error: false });
    post({ type: "save", doc });
  };

  const save = (): void => {
    // 未保存なら、差分が空（順序だけ変わった）でも一覧を出す（一覧がそう言う）
    if (reviewSave && dirty) {
      setReview(diffFlows(base, doc));
      return;
    }
    sendSave();
  };

  const confirmSave = (skipNext: boolean): void => {
    if (skipNext) {
      setReviewSave(false);
      post({ type: "reviewSave", value: false });
    }
    sendSave();
  };

  onKey.current = (event: KeyboardEvent): void => {
    if (review !== undefined) {
      if (event.key === "Escape") {
        event.preventDefault();
        setReview(undefined);
      }
      return;
    }
    if (!(event.ctrlKey || event.metaKey) || event.altKey || inField(event.target)) {
      return;
    }
    const key = event.key.toLowerCase();
    if (key === "z" && !event.shiftKey) {
      event.preventDefault();
      undoEdit();
    } else if ((key === "z" && event.shiftKey) || key === "y") {
      event.preventDefault();
      redoEdit();
    } else if (key === "c" && !event.shiftKey) {
      copy();
    } else if (key === "v" && !event.shiftKey) {
      event.preventDefault();
      paste();
    } else if (key === "d" && !event.shiftKey) {
      event.preventDefault();
      duplicate();
    }
  };

  return (
    <>
      {lock.locked && (
        <div id="lock" className="lock" role="status">
          {lock.reason === "" ? "着手中かどうかを確かめられないので、読み取り専用で開いている。" : lock.reason}
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
          {!page.exists && <span className="dim">（ファイルはまだ無い。保存すると作られる）</span>}
          <span id="dirty" className={dirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="undo" disabled={readOnly || !canUndo(history)} title="元に戻す（Ctrl+Z）" onClick={undoEdit}>
            元に戻す
          </button>
          <button type="button" className="action" data-action="redo" disabled={readOnly || !canRedo(history)} title="やり直す（Ctrl+Shift+Z / Ctrl+Y）" onClick={redoEdit}>
            やり直す
          </button>
          <button type="button" className="action" data-action="copy-nodes" disabled={!copyable} title="選んだノードと、選んだノード同士の線を写す（Ctrl+C）。開始は写さない" onClick={copy}>
            写す
          </button>
          <button type="button" className="action" data-action="paste-nodes" disabled={readOnly || !hasClip} title="写したノードを貼る（Ctrl+V）" onClick={paste}>
            貼る
          </button>
          <button type="button" className="action" data-action="duplicate-nodes" disabled={readOnly || !copyable} title="選んだノードをその場で複製する（Ctrl+D）。開始は複製しない" onClick={duplicate}>
            複製
          </button>
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
          <button type="button" className="action" data-action="toggle-minimap" aria-pressed={minimap} title="図の右下のミニマップを出す・隠す" onClick={toggleMinimap}>
            {minimap ? "ミニマップを隠す" : "ミニマップを出す"}
          </button>
          <button type="button" className="action" data-action="open-flow" disabled={!page.exists} onClick={() => post({ type: "openFile" })}>
            エディタで開く
          </button>
          <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
            再読込
          </button>
          <button type="button" className="action primary" id="save" data-action="save" disabled={lock.locked || busy || (!dirty && page.exists)} onClick={save}>
            保存
          </button>
        </div>
        <TourButton onClick={tour.start} />
      </header>
      {notices.length + (checks?.warns.length ?? 0) > 0 && (
        <ul className="problems" id="flow-notices">
          {notices.map((notice) => (
            <li key={notice} data-source="screen">
              {notice}
            </li>
          ))}
          {(checks?.warns ?? []).map((warn, index) => (
            <li key={`exe-${index}`} data-source="exe" title="ccnavi --lint --flow の warn（保存は止めない）">
              {warn}
            </li>
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
          minimap={minimap}
          onPick={pick}
          onMove={(moves) => {
            // 押しただけ（動かしていない）なら未保存にしない（placeNodes が同じ値を返す）
            const next = placeNodes(doc, moves);
            if (next !== doc) {
              edit(next);
            }
          }}
          onConnect={(from, fromPort, to, toPort) => edit(connect(doc, from, fromPort, to, toPort))}
          onRemoveNode={removeNodeAt}
          onRemoveEdge={removeEdgeAt}
          onResizeGroup={(id, size, position) => {
            // 縁を押しただけ（大きさが変わっていない）なら未保存にしない（resizeGroup が同じ値を返す）
            const next = resizeGroup(doc, id, size, position);
            if (next !== doc) {
              edit(next);
            }
          }}
        />
        <div className="flow-side">
          <Inspector doc={doc} selected={selected} readOnly={readOnly} onChange={edit} onSeal={() => setHistory(seal)} onSelect={setSelected} candidates={checks?.candidates} />
          <Preview checks={checks} checking={checking} error={checkError} />
        </div>
      </div>
      {tour.touring && <Tour steps={TOUR_STEPS} onClose={tour.end} />}
      {review !== undefined && <SaveReview diff={review} onConfirm={confirmSave} onCancel={() => setReview(undefined)} />}
      <footer className={status?.error === true ? "foot error" : "foot"}>
        <span id="status">{status?.text ?? ""}</span>
      </footer>
    </>
  );
}
