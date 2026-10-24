/**
 * フロー編集画面の本体。帯・ツールバー・部品箱・図・右の欄。
 *
 * 見せる中身は拡張ホストが渡す（`FlowData`）。画面が持つのは、ユーザが触って決めるもの（編集中のフロー、
 * 選んでいるもの、直前の操作の一言、元に戻す履歴、コピーしたノード）だけ。
 *
 * 着手中かは画面が決めない。 錠（`FlowLock`）は実行ファイルの答え（`flow.locked`）をそのまま反映したもので、
 * 拡張ホストが渡す。錠が掛かっている間は読むだけ（欄・部品箱・保存・元に戻す・貼り付け が止まる）。
 * 保存を押したときも、拡張ホストが実行ファイルに聞き直してから書く。
 *
 * 中身（`data`）が届いたら、編集中のフローはその中身で置き換える。 届くのは編集を捨ててよいとき
 * だけ（再読込・保存が通った）。履歴もそこで空にする。
 *
 * コピーを替えるのは `edit()` だけ。 直す前のコピーを履歴（`core/flow-history.ts`）に積んでから替える。
 * 「未保存」は、読み込んだ中身（`base`）と見比べて決める（`core/flow-diff.ts` の `sameFlow`）ので、
 * 元に戻して読み込んだときと同じ中身になれば消える。
 *
 * 実行ファイルが言ったこと（`FlowChecks`）は画面で作らない。 開くときの答えは中身と一緒に届き、編集したら
 * 止まってから（`CHECK_MS`）拡張ホストに確かめ直しを頼む（`check` → `checked`。書きはしない）。答えの
 * warn は画面の注意と並べて出し、渡る手順は右の列のプレビュー、候補の名前は右の欄の選択肢に使う。
 *
 * エージェントの下書き。 拡張ホストが「提案あり」（`offer`）を渡したら帯を出す。開くと拡張ホストが
 * 実行ファイルに確かめさせた中身が届き（`proposal`）、文の前後まで見せる差分（`Proposal.tsx`）から「取り込む」で
 * 編集中の内容に入れる（`edit()` を通すので元に戻せる）。保存のときに取り込んだ下書きのハッシュを添え、拡張ホストは
 * 保存が通ったあと、同じ中身の下書きだけを消す。依頼のボタン（`request`）は着手の前だけ拡張ホストが言葉を渡す。
 *
 * 案内（`tours/flow.yml`）。 案内の間は、実際のフローではなく案内用の見本（`core/tour-flow.ts`）を描く。見本の上の様子
 * （どの見本か・選ぶノード・未保存か・差分や下書きを出すか）は手順ごとの `scene` が決め、画面の state には入れない。
 * 実際の編集内容・履歴・選択・保存・下書きには触れず、案内の間は編集・保存・拡張ホストへの送信（`post.ts` の `muteForTour`）が動かない。
 * 閉じたら実際の表示に戻る。
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
  nextSpot,
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
import { lockedReason, OPEN_LOCK, type FlowChecks, type FlowData, type FlowLock, type FlowOffer, type FlowPage, type FlowProposal, type ToFlow } from "../../core/flow-view.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { parseFlowSample, sceneOf } from "../../core/tour-flow.js";
import { useHostMessages } from "../useHostMessages.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import { graphMaximized } from "../shared/Maximize.js";
import { mergeState, readState } from "../vscode.js";
import { Canvas, type Selection } from "./Canvas.js";
import { Inspector } from "./Inspector.js";
import { muteForTour, post } from "./post.js";
import { Preview } from "./Preview.js";
import { ProposalReview, RequestText, type ProposalView } from "./Proposal.js";
import { SaveReview } from "./SaveReview.js";
import { badgeOf } from "./text.js";
import flowTour from "../tours/flow.yml";

/** 中身が読めなかったときの錠。画面は保存させない */
const NO_LOCK: FlowLock = { locked: true, reason: "" };
/** 編集中のコピーを拡張ホストへ送って覚えさせるまでの間（打つたびに送らない） */
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

/** 画面が覚えておくもの（HTML を作り直しても残る）。ミニマップを出すか、左右の欄を畳んだか */
function minimapShown(): boolean {
  return readState("minimap") !== false;
}

function rememberMinimap(shown: boolean): void {
  mergeState("minimap", shown);
}

type Side = "palette" | "side";

function sideFolded(side: Side): boolean {
  const folded = readState("folded") as Record<string, unknown> | undefined;
  return folded?.[side] === true;
}

function rememberSideFolded(side: Side, folded: boolean): void {
  const now = (readState("folded") ?? {}) as Record<string, unknown>;
  mergeState("folded", { ...now, [side]: folded });
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

/** キーを押した先が欄の中か（欄の中の Ctrl+Z や Ctrl+C は欄に任せる） */
function inField(target: EventTarget | null): boolean {
  const element = target as { tagName?: unknown; isContentEditable?: unknown } | null;
  if (element === null || typeof element.tagName !== "string") {
    return false;
  }
  const tag = element.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || element.isContentEditable === true;
}

/** 案内（`tours/flow.yml`）と、案内の間だけ描く見本。実際のフローにも、画面が持つ編集中の内容にも触れない */
const TOUR = tourOf(flowTour);
const SAMPLE = parseFlowSample(TOUR.sample);
const NO_SCENE: Scene = {};
const NOOP = (): void => undefined;
/** 部品箱のボタンの印（`data-tour="palette-<ここ>"`）。小文字の英字と - だけ */
const PALETTE_MARKS: Readonly<Record<PaletteType, string>> = {
  start: "start",
  end: "end",
  prompt: "prompt",
  subAgent: "sub-agent",
  askUserQuestion: "ask",
  ifElse: "if-else",
  switch: "switch",
  loop: "loop",
  skill: "skill",
};

export function App({ initial }: { readonly initial: FlowData }): JSX.Element {
  const [data, setData] = useState<FlowData>(initial);
  // 読み込んだ中身（未保存の見比べと、保存前の差分の元）と、編集中のコピー
  const [base, setBase] = useState<FlowDoc | undefined>(() => pageOf(initial)?.doc);
  const [doc, setDoc] = useState<FlowDoc | undefined>(() => pageOf(initial)?.draft ?? pageOf(initial)?.doc);
  const [history, setHistory] = useState<FlowHistory>(emptyHistory);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [lock, setLock] = useState<FlowLock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  const [changed, setChanged] = useState(false);
  const [selected, setSelected] = useState<Selection | undefined>(undefined);
  const [picked, setPicked] = useState<readonly string[]>([]);
  // 図の外で選んだノード（部品箱で足した・グループ化で作った・貼り付けた）。図はこれが替わったときだけ、それを選び直す
  const [focus, setFocus] = useState<{ readonly ids: readonly string[] } | undefined>(undefined);
  const [minimap, setMinimap] = useState(minimapShown);
  const [folded, setFolded] = useState<Readonly<Record<Side, boolean>>>(() => ({ palette: sideFolded("palette"), side: sideFolded("side") }));
  const toggleSide = (side: Side): void => {
    // 案内の間は畳んだ欄も開いて見せる。覚えている状態も変えない
    if (tour.touring) {
      return;
    }
    setFolded((now) => {
      const next = !now[side];
      rememberSideFolded(side, next);
      return { ...now, [side]: next };
    });
  };
  const [reviewSave, setReviewSave] = useState(() => pageOf(initial)?.reviewSave === true);
  const [review, setReview] = useState<FlowDiff | undefined>(undefined);
  // コピーしたノード（画面の中に保持する）と、同じものを何回貼り付けたか（貼り付けるたびに少しずつずらす）
  const clip = useRef<{ readonly clip: FlowClip; pasted: number } | undefined>(undefined);
  const [hasClip, setHasClip] = useState(false);
  // 実行ファイルが言ったこと。`checkedDoc` は出している答えが指すコピー、`pendingDoc` は頼んで答えを待っているコピー、
  // `checkSeq` は最後に頼んだ確認の番号。コピーが替わるたびに番号を進め、待っていた答えは捨てる
  // （答えを待つ間に直すと、古いコピーの答えを今の答えと取り違えるため）
  const [checks, setChecks] = useState<FlowChecks | undefined>(() => pageOf(initial)?.checks);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | undefined>(undefined);
  const checkedDoc = useRef<FlowDoc | undefined>(pageOf(initial)?.checks === undefined ? undefined : pageOf(initial)?.doc);
  const pendingDoc = useRef<FlowDoc | undefined>(undefined);
  const checkSeq = useRef(0);
  // 外で変わった知らせを受けているか。拡張ホストはタブを再表示するたびに送り直すので、最初の 1 回だけ履歴を空にする
  const changedSeen = useRef(false);
  // エージェントの下書き。依頼のボタンの言葉、「提案あり」、開いた下書き、依頼の文、取り込んだ下書きのハッシュ
  const [request, setRequest] = useState<string | undefined>(() => pageOf(initial)?.request);
  const [offer, setOffer] = useState<FlowOffer | undefined>(() => pageOf(initial)?.offer);
  const [proposal, setProposal] = useState<ProposalView | undefined>(undefined);
  const [requestText, setRequestText] = useState<string | undefined>(undefined);
  // 取り込んだ下書きのハッシュと中身。編集中の中身が取り込んだ中身と同じ間だけ持ち、保存に付ける（違えば下書きを消させない）
  const imported = useRef<{ readonly hash: string; readonly doc: FlowDoc } | undefined>(undefined);
  // 案内の間の見本の上の様子（どの見本か・選ぶノードなど）。実際の選択や編集は動かさないので、閉じたあとに戻すものは図の選択だけ
  const [scene, setScene] = useState<Scene>(NO_SCENE);
  const tour = useTour(data.kind === "page", {
    onStart: () => {
      muteForTour(true);
      setScene(NO_SCENE);
    },
    onEnd: () => {
      muteForTour(false);
      setScene(NO_SCENE);
      // 案内で図を作り直したので、実際の図は選んでいたノードを選び直して始める
      setFocus({ ids: picked });
      post({ type: "tourDone" });
    },
  });
  const sc = sceneOf(SAMPLE, scene);
  // 手順の様子が替わるたびに新しい値にする（図は替わったときだけ、そのノードを選び直す）
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const tourFocus = useMemo(() => ({ ids: sc.picked }), [scene]);

  // 案内の間は、畳んだ欄も開いて見せる（案内が指す部品箱と右の欄が見えるように）。覚えている状態は変えない
  const fold = { palette: folded.palette && !tour.touring, side: folded.side && !tour.touring };
  const requestTour = tour.request;

  useHostMessages<ToFlow>((message) => {
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
      // 頼んでいた確認の答えは捨てる（番号を進める）
      checkSeq.current += 1;
      checkedDoc.current = page?.checks === undefined ? undefined : page.doc;
      pendingDoc.current = undefined;
      setChecks(page?.checks);
      setChecking(false);
      setCheckError(undefined);
      setRequest(page?.request);
      setOffer(page?.offer);
      setProposal(undefined);
      setRequestText(undefined);
      imported.current = undefined;
    } else if (message.type === "offer") {
      const answer = message as { request?: string; offer?: FlowOffer };
      setRequest(answer.request);
      setOffer(answer.offer);
    } else if (message.type === "proposal") {
      const answer = message as { proposal?: FlowProposal; error?: string };
      // 閉じたあとに届いた答えは捨てる
      setProposal((now) =>
        now === undefined ? now : answer.proposal !== undefined ? { kind: "ready", proposal: answer.proposal } : { kind: "error", error: String(answer.error ?? "") },
      );
    } else if (message.type === "requestText") {
      setRequestText(String((message as { prompt?: unknown }).prompt ?? ""));
    } else if (message.type === "failed") {
      setBusy(false);
      setStatus({ text: String(message.message ?? ""), error: true });
    } else if (message.type === "lock" && message.lock !== undefined) {
      setLock(message.lock);
    } else if (message.type === "changed") {
      // 外で変わった。いまの編集は残すが、戻す先はもう読み込んだ中身と合わないので履歴は空にする。
      // 送り直し（タブを再表示した）では空にし直さない（その後に積んだ履歴を消さない）
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
    }
  }, () => post({ type: "ready" }));

  const dirty = useMemo(() => doc !== undefined && base !== undefined && !sameFlow(doc, base), [doc, base]);

  const sentDirty = useRef(false);
  useEffect(() => {
    // 案内の間は送らない（閉じたあとにもう一度見て、送り損ねたぶんを送る）
    if (!tour.touring && sentDirty.current !== dirty) {
      sentDirty.current = dirty;
      post({ type: "dirty", dirty });
    }
  }, [dirty, tour.touring]);

  // 未保存のコピーを拡張ホストに覚えさせる（未保存のまま閉じられたら、開き直して戻せるように）。
  // 打つたびには送らず、止まってから送る。未保存でなくなったら覚えたコピーを消させる
  const sentDraft = useRef(false);
  useEffect(() => {
    if (tour.touring) {
      return;
    }
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
  }, [doc, dirty, tour.touring]);

  // 図で選んでいるノードが変わった。毎回同じ関数を渡す（React Flow は onSelectionChange が替わるたびに
  // その時の選択で呼び直すので、描くたびに作り直すと、押した直後の古い選択で呼ばれる）
  const pick = useCallback((ids: readonly string[]): void => {
    setPicked((now) => (sameIds(now, ids) ? now : ids));
    // Shift を押しながら選んでいたノードを押すと、React Flow はそれを選択から外す。右の欄がそのノードの
    // ままにならないよう、選択に残ったノードの最後（残っていなければ何も無い）に替える
    setSelected((now) => (now?.kind !== "node" || ids.includes(now.id) ? now : ids.length === 0 ? undefined : { kind: "node", id: ids[ids.length - 1] }));
  }, []);

  // 編集が止まったら実行ファイルに確かめ直させる。出している答えが指すコピーと同じなら頼まない
  useEffect(() => {
    if (doc === undefined || pendingDoc.current === doc || tour.touring) {
      return;
    }
    // コピーが替わった。待っていた答えは古いコピーのものなので捨てる（番号を進める）
    checkSeq.current += 1;
    pendingDoc.current = undefined;
    if (checkedDoc.current === doc) {
      // 答えが指すコピーに戻った（元に戻すなど）。確かめ直さない
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
  }, [doc, tour.touring]);

  // 取り込んだ中身から編集で離れたら、取り込んだ記録を外す（元に戻して同じ中身にしても付け直さない。下書きを消さない側）
  useEffect(() => {
    const taken = imported.current;
    if (taken !== undefined && doc !== undefined && !sameFlow(doc, taken.doc)) {
      imported.current = undefined;
    }
  }, [doc]);

  // 画面の注意と、実行ファイルの warn。実行ファイルの答えがあれば、同じことを言う画面の注意は出さない
  const notices = useMemo(() => (doc === undefined ? [] : flowNotices(doc, { exe: checks !== undefined })), [doc, checks]);

  // キー入力を受け取る側は 1 度だけ張り、中身は描くたびに最新へ差し替える
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
        <p className="empty">フロー編集画面を読み込み直せませんでした。原因を直してから「再読込」を押してください。</p>
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
   * コピーを替える唯一の入り口。直す前のコピーを履歴に積む。`typing` は欄に打った文字のときの欄の名前で、
   * 同じ欄に続けて打ったものは 1 件にまとめる
   */
  const edit = (next: FlowDoc, typing?: string): void => {
    if (tour.touring || lock.locked || next === doc) {
      return;
    }
    const at = now();
    setHistory((history) => record(history, doc, typing === undefined ? {} : { key: typing, now: at }));
    setDoc(next);
  };

  /** 戻した・やり直したコピーに無いものを選んでいたら外す（線は配列の位置で指すので、線の選択は外す） */
  const travel = (moved: { readonly history: FlowHistory; readonly doc: FlowDoc } | undefined): void => {
    if (moved === undefined || readOnly || tour.touring) {
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
    if (tour.touring) {
      return;
    }
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

  const add = (type: PaletteType): void => {
    if (tour.touring) {
      return;
    }
    const added = addNode(doc, type, nextSpot(doc));
    edit(added.doc);
    setSelected({ kind: "node", id: added.id });
    setFocus({ ids: [added.id] });
  };

  /** 消したものを選んでいたら、選ぶのをやめる（線は配列の位置で指すので、線を消したら線の選択は外す） */
  const removeNodeAt = (id: string): void => {
    if (tour.touring) {
      return;
    }
    edit(removeNode(doc, id));
    // ノードと一緒に線も消えて配列の位置がずれるので、線の選択も外す
    if ((selected?.kind === "node" && selected.id === id) || selected?.kind === "edge") {
      setSelected(undefined);
    }
  };
  const removeEdgeAt = (index: number): void => {
    if (tour.touring) {
      return;
    }
    edit(removeConnectionAt(doc, index));
    if (selected?.kind === "edge") {
      setSelected(undefined);
    }
  };

  // グループ化できるのは、グループでないノードを 2 つ以上選んでいるとき
  const groupable = picked.filter((id) => doc.nodes.some((node) => node.id === id && !isGroup(node)));
  const group = (): void => {
    if (tour.touring) {
      return;
    }
    const grouped = groupNodes(doc, groupable);
    if (grouped === undefined) {
      return;
    }
    edit(grouped.doc);
    setSelected({ kind: "node", id: grouped.id });
    setFocus({ ids: [grouped.id] });
  };

  // コピー・複製するのは、図で選んでいるノード（無ければ右の欄に出しているノード）
  const chosen = picked.length > 0 ? picked : selected?.kind === "node" ? [selected.id] : [];
  const copyable = copyNodes(doc, chosen) !== undefined;

  /** 貼り付けた・複製したノードを選ぶ。右の欄は最後のノード */
  const choose = (ids: readonly string[]): void => {
    setFocus({ ids });
    setSelected(ids.length === 0 ? undefined : { kind: "node", id: ids[ids.length - 1] });
  };

  const copy = (): void => {
    if (tour.touring) {
      return;
    }
    const copied = copyNodes(doc, chosen);
    if (copied === undefined) {
      setStatus({ text: "コピーできるノードが選ばれていません（開始はコピーしません）", error: false });
      return;
    }
    clip.current = { clip: copied, pasted: 0 };
    setHasClip(true);
    setStatus({ text: `ノードを ${copied.nodes.length} 個、線を ${copied.connections.length} 本コピーしました。Ctrl+V で貼り付けられます`, error: false });
  };

  const paste = (): void => {
    const held = clip.current;
    if (held === undefined || readOnly || tour.touring) {
      return;
    }
    held.pasted += 1;
    const pasted = pasteNodes(doc, held.clip, { x: PASTE_OFFSET.x * held.pasted, y: PASTE_OFFSET.y * held.pasted });
    edit(pasted.doc);
    choose(pasted.ids);
    setStatus({ text: `ノードを ${pasted.ids.length} 個貼り付けました`, error: false });
  };

  const duplicate = (): void => {
    if (readOnly || tour.touring) {
      return;
    }
    const made = duplicateNodes(doc, chosen);
    if (made === undefined) {
      setStatus({ text: "複製できるノードが選ばれていません（開始は複製しません）", error: false });
      return;
    }
    edit(made.doc);
    choose(made.ids);
    setStatus({ text: `ノードを ${made.ids.length} 個複製しました`, error: false });
  };

  const toggleMinimap = (): void => {
    if (tour.touring) {
      return;
    }
    const next = !minimap;
    setMinimap(next);
    rememberMinimap(next);
  };

  const sendSave = (): void => {
    if (tour.touring) {
      return;
    }
    setReview(undefined);
    setBusy(true);
    setStatus({ text: "着手中でないかを確認して保存中…", error: false });
    const taken = imported.current;
    post({ type: "save", doc, ...(taken !== undefined && sameFlow(doc, taken.doc) ? { imported: taken.hash } : {}) });
  };

  const openProposal = (): void => {
    if (tour.touring) {
      return;
    }
    setProposal({ kind: "loading" });
    post({ type: "openProposal" });
  };

  /** 下書きの中身を編集中の内容に入れる。書かない（保存はいつもの経路）。着手中は入れない */
  const importProposal = (taken: FlowProposal): void => {
    if (lock.locked || tour.touring) {
      return;
    }
    edit(taken.doc);
    imported.current = { hash: taken.hash, doc: taken.doc };
    setProposal(undefined);
    setSelected(undefined);
    setStatus({ text: "下書きを取り込みました（まだ保存していません）。そのまま保存すると、取り込んだ下書きは消えます（直してから保存したときは残します）", error: false });
  };

  const save = (): void => {
    if (tour.touring) {
      return;
    }
    // 未保存なら、差分が空（順序だけ変わった）でも一覧を出す（一覧がそう言う）
    if (reviewSave && dirty) {
      setReview(diffFlows(base, doc));
      return;
    }
    sendSave();
  };

  const confirmSave = (skipNext: boolean): void => {
    if (tour.touring) {
      return;
    }
    if (skipNext) {
      setReviewSave(false);
      post({ type: "reviewSave", value: false });
    }
    sendSave();
  };

  onKey.current = (event: KeyboardEvent): void => {
    // 案内の間は、案内が受ける（Esc・矢印・Tab）。背後の編集（Ctrl+Z など）は動かさない
    if (tour.touring) {
      return;
    }
    // 図を全画面にしている間は、見えない文書を直さない
    if (graphMaximized()) {
      return;
    }
    // 提案・依頼文・保存前の確認の Esc は、この keydown が受ける（Ctrl+Z などと同じ分岐に並ぶため `useEscape` へ移していない）。
    // `useEscape` は window の捕捉段階で止めるので、全画面表示の図や案内の吹き出しが手前に出ている間は、ここへ届かない
    if (proposal !== undefined || requestText !== undefined) {
      if (event.key === "Escape") {
        event.preventDefault();
        setProposal(undefined);
        setRequestText(undefined);
      }
      return;
    }
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

  const touring = tour.touring;

  // 案内の間に描くもの。実際の値は触らず、見本の上の様子（scene）から決める
  const courses = coursesOf(TOUR, setScene);
  const sampleGroupable = sc.picked.filter((id) => sc.flow.doc.nodes.some((node) => node.id === id && !isGroup(node)));
  const shownPage = touring ? { ...SAMPLE.page, exists: true } : page;
  const shownDoc = touring ? sc.flow.doc : doc;
  const shownLock: FlowLock = touring ? (sc.lock ? { locked: true, reason: lockedReason(SAMPLE.page.ticket) } : OPEN_LOCK) : lock;
  const shownReadOnly = touring ? sc.lock : readOnly;
  const shownDirty = touring ? sc.dirty : dirty;
  const shownChanged = touring ? sc.changed : changed;
  const shownOffer = touring ? (sc.offer ? SAMPLE.offer : undefined) : offer;
  const shownRequest = touring ? SAMPLE.request : request;
  const shownChecks = touring ? sc.flow.checks : checks;
  const shownChecking = touring ? sc.checking : checking;
  const shownCheckError = touring ? undefined : checkError;
  const shownNotices = touring ? flowNotices(sc.flow.doc, { exe: true }) : notices;
  const shownSelected: Selection | undefined = touring ? sc.selected : selected;
  const shownMinimap = touring ? sc.minimap : minimap;
  const shownBusy = touring ? false : busy;
  const canUndoNow = touring ? sc.history : canUndo(history);
  const canRedoNow = touring ? sc.history : canRedo(history);
  const copyableNow = touring ? sc.picked.length > 0 : copyable;
  const hasClipNow = touring ? sc.clip : hasClip;
  const groupableCount = touring ? sampleGroupable.length : groupable.length;

  return (
    <>
      {touring ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
      {shownLock.locked && (
        <div id="lock" className="lock" data-tour="lock" role="status">
          {shownLock.reason === "" ? "着手中かどうかを確認できないため、読み取り専用で開いています。" : shownLock.reason}
        </div>
      )}
      <div id="changed" data-tour="changed" className={shownChanged ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。再読込してください。
        <button type="button" className="action" data-action="reload" disabled={shownBusy} onClick={reload}>
          再読込
        </button>
      </div>
      {shownOffer !== undefined && (
        <div id="offer" data-tour="offer" className="banner missing" role="status">
          <span>
            提案あり: エージェントの下書き（{shownOffer.draftPath}）が、いまのフローと違います。
            {shownOffer.problem !== undefined && <span className="dim">（読み込めません: {shownOffer.problem}）</span>}
          </span>
          <button type="button" className="action" data-action="open-proposal" disabled={shownBusy} onClick={openProposal}>
            提案を見る
          </button>
        </div>
      )}
      <ScreenHeader courses={courses} onPick={tour.start}>
        <div className="summary">
          <span className="flow-ticket">
            <strong>{shownPage.ticket}</strong> {shownPage.title}
          </span>
          <span className="path" title={`${shownPage.flowRel}（承認済みの領域です。変更・コミットできるのはユーザだけです）`}>
            {shownPage.flowPath}
          </span>
          {!shownPage.exists && <span className="dim">（ファイルはまだありません。保存すると作られます）</span>}
          <span id="dirty" data-tour="dirty" className={shownDirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="undo" data-tour="undo" disabled={shownReadOnly || !canUndoNow} title="元に戻す（Ctrl+Z）" onClick={undoEdit}>
            元に戻す
          </button>
          <button type="button" className="action" data-action="redo" data-tour="redo" disabled={shownReadOnly || !canRedoNow} title="やり直す（Ctrl+Shift+Z / Ctrl+Y）" onClick={redoEdit}>
            やり直す
          </button>
          <button type="button" className="action" data-action="copy-nodes" data-tour="copy" disabled={!copyableNow} title="選んだノードと、それらをつなぐ線をコピーします（Ctrl+C）。開始はコピーしません" onClick={copy}>
            コピー
          </button>
          <button type="button" className="action" data-action="paste-nodes" data-tour="paste" disabled={shownReadOnly || !hasClipNow} title="コピーしたノードを貼り付けます（Ctrl+V）" onClick={paste}>
            貼り付け
          </button>
          <button type="button" className="action" data-action="duplicate-nodes" data-tour="duplicate" disabled={shownReadOnly || !copyableNow} title="選んだノードをその場で複製します（Ctrl+D）。開始は複製しません" onClick={duplicate}>
            複製
          </button>
          <button
            type="button"
            className="action"
            data-action="group-nodes"
            data-tour="group"
            disabled={shownReadOnly || groupableCount < 2}
            title="Shift を押しながらノードを 2 つ以上選ぶと、枠（グループ）にまとめられます。枠は図の上の囲みで、手順は変わりません"
            onClick={group}
          >
            グループ化
          </button>
          <button type="button" className="action" data-action="toggle-minimap" data-tour="minimap" aria-pressed={shownMinimap} title="図の右下のミニマップを出す・隠す" onClick={toggleMinimap}>
            {shownMinimap ? "ミニマップを隠す" : "ミニマップを出す"}
          </button>
          {shownRequest !== undefined && !shownLock.locked && (
            <button
              type="button"
              className="action"
              data-action="request-flow"
              data-tour="request"
              disabled={shownBusy}
              title="エージェントへの依頼の文を作ります。エージェントは下書きのみを作成します。取り込むかどうかは、この画面で差分を確認して判断してください"
              onClick={() => post({ type: "request" })}
            >
              {shownRequest}
            </button>
          )}
          <button type="button" className="action" data-action="open-flow" disabled={!shownPage.exists} onClick={() => post({ type: "openFile" })}>
            エディタで開く
          </button>
          <button type="button" className="action" data-action="reload" disabled={shownBusy} onClick={reload}>
            再読込
          </button>
          <button type="button" className="action primary" id="save" data-action="save" data-tour="save" disabled={shownLock.locked || shownBusy || (!shownDirty && shownPage.exists)} onClick={save}>
            保存
          </button>
        </div>
      </ScreenHeader>
      {shownNotices.length + (shownChecks?.warns.length ?? 0) > 0 && (
        <ul className="problems" id="flow-notices" data-tour="notices">
          {shownNotices.map((notice) => (
            <li key={notice} data-source="screen">
              {notice}
            </li>
          ))}
          {(shownChecks?.warns ?? []).map((warn, index) => (
            <li key={`exe-${index}`} data-source="exe" title="ccnavi --lint --flow の warn です（保存は止めません）">
              {warn}
            </li>
          ))}
        </ul>
      )}
      <div className="flow-body" data-palette-folded={fold.palette ? "1" : "0"} data-side-folded={fold.side ? "1" : "0"}>
        <nav className="flow-palette" id="flow-palette" data-tour="palette" aria-label="部品箱" data-folded={fold.palette ? "1" : "0"}>
          <button type="button" className="action small flow-fold" data-action="toggle-palette" aria-expanded={!fold.palette} title={fold.palette ? "部品箱を開く" : "部品箱を畳む"} onClick={() => toggleSide("palette")}>
            {fold.palette ? "▶ 部品" : "◀ 部品"}
          </button>
          {PALETTE.map((type) => {
            const badge = badgeOf(type);
            return (
              <button key={type} type="button" className="action small palette-item" data-action="add-node" data-tour={`palette-${PALETTE_MARKS[type]}`} data-type={type} disabled={shownReadOnly} title={badge?.title} onClick={() => add(type)}>
                {TYPE_LABELS[type]}
                {badge !== undefined && <span className={`flow-badge ${badge.kind}`}>{badge.kind === "ask" ? "戻る" : "入れ子"}</span>}
              </button>
            );
          })}
        </nav>
        <Canvas
          // 案内の見本と実際のフローは別の図にする（組み直すと、収まる位置も選びも見本のものになる）。見本は見本の名前ごとに組み直す
          key={touring ? `tour:${sc.name}` : "real"}
          doc={shownDoc}
          readOnly={touring || readOnly}
          selected={shownSelected}
          onSelect={touring ? NOOP : setSelected}
          focus={touring ? tourFocus : focus}
          minimap={shownMinimap}
          marks={touring}
          onPick={touring ? NOOP : pick}
          onMove={(moves) => {
            // 押しただけ（動かしていない）なら未保存にしない（placeNodes が同じものを返す）
            const next = placeNodes(doc, moves);
            if (next !== doc) {
              edit(next);
            }
          }}
          onConnect={(from, fromPort, to, toPort) => edit(connect(doc, from, fromPort, to, toPort))}
          onRemoveNode={removeNodeAt}
          onRemoveEdge={removeEdgeAt}
          onResizeGroup={(id, size, position) => {
            // 縁を押しただけ（大きさが変わっていない）なら未保存にしない（resizeGroup が同じものを返す）
            const next = resizeGroup(doc, id, size, position);
            if (next !== doc) {
              edit(next);
            }
          }}
        />
        <div className="flow-side" data-tour="side" data-folded={fold.side ? "1" : "0"}>
          <button type="button" className="action small flow-fold" data-action="toggle-side" aria-expanded={!fold.side} title={fold.side ? "右の欄を開く" : "右の欄を畳む"} onClick={() => toggleSide("side")}>
            {fold.side ? "◀ 詳細" : "詳細 ▶"}
          </button>
          <Inspector
            doc={shownDoc}
            selected={shownSelected}
            readOnly={shownReadOnly}
            onChange={touring ? NOOP : edit}
            onSeal={touring ? NOOP : () => setHistory(seal)}
            onSelect={touring ? NOOP : setSelected}
            candidates={shownChecks?.candidates}
          />
          <Preview checks={shownChecks} checking={shownChecking} error={shownCheckError} />
        </div>
      </div>
      {touring && <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} />}
      {!touring && review !== undefined && <SaveReview diff={review} onConfirm={confirmSave} onCancel={() => setReview(undefined)} />}
      {touring && sc.review && <SaveReview diff={diffFlows(sc.base.doc, sc.flow.doc)} onConfirm={NOOP} onCancel={NOOP} />}
      {!touring && proposal !== undefined && (
        <ProposalReview view={proposal} base={base} lock={lock} dirty={dirty} onImport={importProposal} onCancel={() => setProposal(undefined)} />
      )}
      {touring && sc.proposal && (
        <ProposalReview view={{ kind: "ready", proposal: SAMPLE.proposal }} base={sc.base.doc} lock={shownLock} dirty={sc.dirty} onImport={NOOP} onCancel={NOOP} />
      )}
      {!touring && requestText !== undefined && <RequestText prompt={requestText} onClose={() => setRequestText(undefined)} />}
      {touring && sc.requestText && <RequestText prompt={SAMPLE.requestPrompt} onClose={NOOP} />}
      <footer className={!touring && status?.error === true ? "foot error" : "foot"}>
        <span id="status">{touring ? "" : status?.text ?? ""}</span>
      </footer>
    </>
  );
}
