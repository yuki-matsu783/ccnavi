/**
 * フェーズ管理画面の本体。注意の帯と、フェーズの種類の一覧。
 *
 * 見せる中身は拡張ホストが渡す（`PhasesData`）。画面が持つのは、人が触って決めるもの
 * （編集中の種類、開いている行、絞り込み、直前の操作の一言）だけ。種類の意味は判定しない。
 *
 * **中身（`data`）が届いたら、編集中の種類はその中身で置き換える。** 届くのは編集を捨ててよい
 * ときだけ（人が「再読込」を押した、保存や作成が通った）で、ファイルが外で変わっただけのときは
 * 帯（`changed`）が出るだけ（ADR-0062）。
 *
 * **id の重なりだけは画面で止める。** 同じ id が 2 つあると実行ファイルは後ろで黙って上書きする。
 * 止めるのはここだけで、書式の検証は保存のときに実行ファイル（`--lint`）へ渡す（ADR-0035）。
 */
import { useEffect, useMemo, useRef, useState, type JSX } from "react";

import type { Lock } from "../../core/lock.js";
import { graphOf } from "../../core/phases-graph.js";
import { editable as canEdit, ORDER_LABELS, ORDERS, type PhaseForm, type PhaseKind, type PhaseOrder, type PhasesData, type PhasesPage, type ToPhases } from "../../core/phases-view.js";
import { applyAppearance } from "../appearance.js";
import { Graph, Legend } from "./Graph.js";
import { Phase } from "./Phase.js";
import { Tour, type TourStep } from "./Tour.js";
import { post } from "./post.js";
import { countText, duplicateNote, emptyNote, findText, graphNotices, hasRelations } from "./text.js";
import { draftOf, duplicates, emptyPhase, formOf, keyer, loadOpen, loadView, openedFromIds, saveOpen, saveView, type Draft, type View } from "./state.js";

/** 中身が読めなかったときの錠。画面は保存させない */
const NO_LOCK: Lock = { locked: true, reason: "", doing: [] };

const EMPTY_DRAFT: Draft = { order: "sequential", rows: [] };

/** 案内を始める前の画面の様子（`beforeTour`） */
interface TourSnapshot {
  readonly view: View;
  readonly find: string;
  readonly open: ReadonlySet<string>;
  readonly more: ReadonlyMap<string, boolean>;
  /** 始めたときの中身。案内の間に読み直されたら、開いていた行の鍵は古いので戻さない */
  readonly data: PhasesData;
}

interface Status {
  readonly text: string;
  readonly error: boolean;
}

interface Editing {
  readonly draft: Draft;
  /** 開いている行の鍵 */
  readonly open: ReadonlySet<string>;
  /**
   * 「ほかの種類との関係・補足」を開いているか。**行ごとに 1 度だけ値の有無で決め、あとは人の開閉で動く。**
   * 描くたびに値の有無で決め直すと、最後の値を消した瞬間に、打っている欄ごと畳まれる
   */
  readonly more: ReadonlyMap<string, boolean>;
}

function pageOf(data: PhasesData): PhasesPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

function editingOf(data: PhasesData, nextKey: () => string): Editing {
  const page = pageOf(data);
  const draft = page === undefined ? EMPTY_DRAFT : draftOf(page.model.form, nextKey);
  const more = new Map(draft.rows.map((row) => [row.key, hasRelations(row.phase)]));
  return { draft, open: openedFromIds(draft, loadOpen()), more };
}

export function App({ initial }: { readonly initial: PhasesData }): JSX.Element {
  const nextKey = useRef(keyer()).current;
  const [data, setData] = useState<PhasesData>(initial);
  const [editing, setEditing] = useState<Editing>(() => editingOf(initial, nextKey));
  const [dirty, setDirty] = useState(false);
  /** 保存や作成の往復の間。欄を止める（通ると中身が入れ替わり、その間の編集は消えるため） */
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [lock, setLock] = useState<Lock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  const [changed, setChanged] = useState(false);
  const [find, setFind] = useState("");
  const [focusKey, setFocusKey] = useState<string | undefined>(undefined);
  const [view, setView] = useState<View>(() => loadView());
  /** 吹き出しの案内を出しているか。拡張ホストの `tour`（初回）かヘルプのボタンで出る */
  const [touring, setTouring] = useState(false);
  /** 拡張ホストが初回の案内を頼んだが、中身がまだ無くて出せていない */
  const [tourPending, setTourPending] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  /**
   * 案内を始める前の画面の様子。案内は一覧と図を切り替え、見本の行と関係の欄を開き、絞り込みを外すので、
   * 閉じたらこれに戻す。**案内の間の一覧と図の切り替えは控え（`saveView`）に書かない**（途中でタブを
   * 閉じたときに、次から図で開く、ということを起こさない）。
   */
  const beforeTour = useRef<TourSnapshot | undefined>(undefined);


  const { draft, open, more } = editing;
  const page = pageOf(data);
  const editable = page !== undefined && canEdit(page);

  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToPhases> & { data?: PhasesData; lock?: Lock; message?: string; value?: unknown };
      if (message.type === "data" && message.data !== undefined) {
        const next = message.data;
        setData(next);
        setEditing(editingOf(next, nextKey));
        setDirty(false);
        setBusy(false);
        setStatus(undefined);
        setChanged(false);
        setLock(pageOf(next)?.lock ?? NO_LOCK);
      } else if (message.type === "failed") {
        setBusy(false);
        setStatus({ text: String(message.message ?? ""), error: true });
      } else if (message.type === "lock" && message.lock !== undefined) {
        setLock(message.lock);
      } else if (message.type === "changed") {
        setChanged(true);
      } else if (message.type === "cancelled") {
        // 「破棄して読み直す？」をやめた。止めた欄を戻す
        setBusy(false);
        setStatus(undefined);
      } else if (message.type === "tour") {
        setTourPending(true);
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    post({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, [nextKey]);

  // 足した行の id へ焦点を移す。畳んだままでは何を足したか分からないので、行は開いて出してある
  useEffect(() => {
    if (focusKey === undefined) {
      return;
    }
    const input = document.querySelector<HTMLInputElement>(`.phase[data-key="${focusKey}"] input.f-id`);
    input?.focus();
    setFocusKey(undefined);
  }, [focusKey]);

  /**
   * 読み直しを頼む。**押した時点で欄を止める。** 拡張ホストは実行ファイルに聞いてから中身を返す
   * ことがあり（層の置き場を解く）、その間に打った内容は、届いた中身で黙って消えるため。
   * 人が「破棄して読み直す？」をやめたときは `cancelled` が返り、欄が戻る。
   */
  /**
   * 未保存の変更の有無が変わったら拡張ホストに伝える。同じ種類のタブは 1 枚で、別の対象を開くと
   * このタブの中身が入れ替わるので、拡張ホストはこれを見て「破棄して切り替える？」を聞く。
   * 送るのは変わったときだけ（最初の「変更なし」は拡張ホストも同じ前提で始まるので送らない）
   */
  const sentDirty = useRef(false);
  useEffect(() => {
    if (sentDirty.current !== dirty) {
      sentDirty.current = dirty;
      post({ type: "dirty", dirty });
    }
  }, [dirty]);

  const reload = (): void => {
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

  // 初回の案内は、種類の中身が出てから始める（読み込み中やエラーの画面には指す先が無い）
  useEffect(() => {
    if (tourPending && data.kind === "page") {
      setTourPending(false);
      if (!touring) {
        beforeTour.current = { view, find, open: editing.open, more: editing.more, data };
        setTouring(true);
      }
    }
  }, [tourPending, data, view, find, editing, touring]);

  /**
   * 図の中身。**メモ化する。** 描くたびに新しい形を作ると、React Flow は `nodes` の参照が
   * 変わったと見て内部の点を作り直す（`adoptUserNodes` の `checkEquality`）。ドラッグしている
   * 最中に絞り込みや「外で変わった」の報せが届くと、掴んだ点が掴む前の位置へ戻る。
   *
   * **読み込み中とエラーの早めの return より前に置く。** 後ろに置くと、中身から読み込み中・エラーへ
   * 移ったときにフックの数が変わって React が落ちる
   */
  const graph = useMemo(() => graphOf(formOf(draft)), [draft]);

  if (data.kind === "loading") {
    return (
      <p className="empty" id="ccnavi-loading">
        {data.text}
      </p>
    );
  }

  if (data.kind === "error") {
    return (
      <>
        <p className="empty">
          フェーズ管理画面を読み直せなかった。原因を直してから「再読込」を押す（同じ対象を開き直しても前面に出るだけ。別の対象を開けば、このタブの中身がその対象に替わる）。
        </p>
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          再読込
        </button>
      </>
    );
  }

  const editDraft = (next: Draft, open?: ReadonlySet<string>): void => {
    setEditing((now) => ({ ...now, draft: next, open: open ?? now.open }));
    setDirty(true);
    // id が重なっている間はその文面だけを出す。直前の失敗の文面は今のことではない
    if (duplicates(next).size > 0) {
      setStatus(undefined);
    }
  };

  const editRow = (key: string, phase: PhaseForm): void => {
    editDraft({ ...draft, rows: draft.rows.map((row) => (row.key === key ? { ...row, phase } : row)) });
  };

  const toggle = (key: string): void => {
    const next = new Set(open);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    setEditing((now) => ({ ...now, open: next }));
    saveOpen(draft, next);
  };

  const toggleMore = (key: string, value: boolean): void => {
    setEditing((now) => ({ ...now, more: new Map(now.more).set(key, value) }));
  };

  const move = (key: string, delta: number): void => {
    const rows = draft.rows.slice();
    const from = rows.findIndex((row) => row.key === key);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= rows.length) {
      return;
    }
    const moved = rows[from];
    rows[from] = rows[to];
    rows[to] = moved;
    editDraft({ ...draft, rows });
  };

  const remove = (key: string): void => {
    editDraft({ ...draft, rows: draft.rows.filter((row) => row.key !== key) });
  };

  const showView = (next: View): void => {
    setView(next);
    saveView(next);
  };

  /**
   * 図の点を押した。一覧へ戻し、その id の行を開いて焦点を移す。
   * 同じ id が 2 つあるときは先に出てきたほう（図に出ているのがそれ）。
   */
  const pick = (id: string): void => {
    const row = draft.rows.find((item) => item.phase.id.trim() === id);
    showView("list");
    setFind("");
    if (row === undefined) {
      return;
    }
    const next = new Set(open);
    next.add(row.key);
    setEditing((now) => ({ ...now, open: next }));
    saveOpen(draft, next);
    setFocusKey(row.key);
  };

  const startTour = (): void => {
    beforeTour.current = { view, find, open, more, data };
    setHelpOpen(false);
    setTouring(true);
  };

  /** 案内を閉じた（最後まで見ても、途中でやめても）。前の様子に戻し、拡張ホストに伝える（次からは初回の案内を出さない） */
  const endTour = (): void => {
    setTouring(false);
    const was = beforeTour.current;
    if (was !== undefined) {
      setView(was.view);
      setFind(was.find);
      if (was.data === data) {
        setEditing((now) => ({ ...now, open: was.open, more: was.more }));
      }
    }
    beforeTour.current = undefined;
    post({ type: "tourDone" });
  };

  /** 案内の間の一覧と図の切り替え。控えには書かない */
  const peekView = (next: View): void => setView(next);

  /** 案内で指す見本の行。関係（overlap / requires / after）を持つ最初の行、無ければ最初の行 */
  const sample = draft.rows.find((item) => item.phase.id.trim() !== "" && item.phase.overlap.length + item.phase.requires.length + item.phase.after.length > 0) ?? draft.rows[0];

  /** 見本の行とその関係の欄を開く。絞り込みで隠れないよう外す（閉じたら戻す） */
  const openSample = (): void => {
    if (sample === undefined) {
      return;
    }
    setFind("");
    setEditing((now) => ({ ...now, open: new Set([...now.open, sample.key]), more: new Map(now.more).set(sample.key, true) }));
  };

  const tourSteps: readonly TourStep[] = [
    {
      target: "#phases",
      title: "フェーズの種類",
      body: "工程の型。作業（work）の種類は親チケットの計画 plan: に、フィードバック対応（feedback）の種類はレビューのあとの feedback: に並べる。行を押すと欄が開き、「＋ 種類を追加」で増やせる。",
      before: () => peekView("list"),
    },
    {
      target: sample === undefined ? "#phases" : `.phase[data-key="${sample.key}"] details.more`,
      title: "ほかの種類との関係",
      body:
        sample === undefined
          ? "種類を足して行を開くと「ほかの種類との関係」の欄があり、並行できる種類・一緒に必要な種類・先に済ませる種類をチェックで選べる。先に済ませる種類（after）は、待ち方が dag のときに判定が待つ相手になる。"
          : "並行できる種類・一緒に必要な種類・先に済ませる種類を、チェックで選ぶ。先に済ませる種類（after）は、待ち方が dag のときに判定が待つ相手になる。",
      before: () => {
        peekView("list");
        openSample();
      },
    },
    {
      target: "#f-order",
      title: "全体計画の待ち方",
      body: "sequential は plan: に並べた順に一つずつ進む。dag は after でつないだ種類だけを待ち、つながっていない種類は並行して進む。層のどれかが sequential なら、判定は sequential で待つ。",
    },
    {
      target: "#phase-graph",
      title: "図",
      body: "矢印が after の流れ、実線が一緒に必要、破線が並行できる関係。作業（work）とフィードバック対応（feedback）は枠で分かれ、枠の間の矢印はレビュー後の順を表す。点を押すと一覧のその行へ移る。",
      before: () => peekView("graph"),
    },
    {
      target: "#save",
      title: "保存",
      body: "保存すると実行ファイルが検証してから書き込む。通らなければ、下に理由が出る。",
    },
    {
      target: '[data-action="help"]',
      title: "ヘルプ",
      body: "細かい説明はここから開く。この案内も、ここからもう一度見られる。",
      before: () => peekView(beforeTour.current?.view ?? view),
    },
  ];

  /**
   * 種類を足す。**図を見ていても一覧へ移す。** 足した種類は id が空で図に出ないので、図のままだと
   * 押しても何も変わらないように見える。絞り込みも外す（id が空の行は絞り込みに当たらず隠れる）。
   */
  const add = (): void => {
    const row = { key: nextKey(), phase: emptyPhase() };
    editDraft({ ...draft, rows: [...draft.rows, row] }, new Set([...open, row.key]));
    showView("list");
    setFind("");
    // 足した種類は関係も補足も空なので、「ほかの種類との関係・補足」は畳んで出す
    setEditing((now) => ({ ...now, more: new Map(now.more).set(row.key, false) }));
    setFocusKey(row.key);
  };

  const dup = duplicates(draft);
  // 関係の欄の候補。同じ id が 2 つあるときは先に出てきたほう（図と同じ読み方）
  const kinds = new Map<string, PhaseKind>();
  for (const row of draft.rows) {
    const id = row.phase.id.trim();
    if (id !== "" && !kinds.has(id)) {
      kinds.set(id, row.phase.kind);
    }
  }
  const query = find.trim().toLowerCase();
  const rows = draft.rows.map((row) => {
    const text = findText(row.phase);
    return { ...row, find: text, hidden: query !== "" && !text.includes(query) };
  });
  const shown = rows.filter((row) => !row.hidden).length;
  const kept = rows.filter((row) => row.hidden && open.has(row.key)).length;
  const shownStatus: Status | undefined = dup.size > 0 ? { text: duplicateNote(dup), error: true } : status;

  return (
    <>
      {(page?.notices ?? []).map((notice, index) => (
        <div key={index} className="banner warn">
          {notice}
        </div>
      ))}
      <div id="changed" className={changed ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。再読込してください。
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
          再読込
        </button>
      </div>
      <header className="toolbar">
        <div className="summary">
          <span className="path" title={page?.root ?? ""}>
            {page?.phasesPath ?? ""}
          </span>
          <span id="dirty" className={dirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="open-phases" disabled={page?.exists !== true} onClick={() => post({ type: "openFile" })}>
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
            disabled={!dirty || lock.locked || busy || !editable || dup.size > 0}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "検証して保存中…", error: false });
              post({ type: "save", form: formOf(draft) });
            }}
          >
            保存
          </button>
        </div>
      </header>
      <p id="lock" className={lock.locked ? "lock" : "lock hidden"}>
        {lock.reason}
      </p>
      {page !== undefined && page.model.problems.length > 0 && (
        <ul className="problems">
          {page.model.problems.map((problem, index) => (
            <li key={index}>{problem}</li>
          ))}
        </ul>
      )}
      {page !== undefined && !page.exists && <Missing page={page} busy={busy} onCreate={() => {
        setBusy(true);
        setStatus({ text: "ファイルを作成中…", error: false });
        post({ type: "create" });
      }} />}
      <section className="block">
        <h2>
          フェーズの種類{" "}
          <span className="count" id="phase-count">
            {countText(draft.rows.length, query, shown, kept)}
          </span>
          <button type="button" className="action small" data-action="add" disabled={busy || !editable} onClick={add}>
            ＋ 種類を追加
          </button>
          <button
            type="button"
            className={helpOpen ? "action small on" : "action small"}
            data-action="help"
            title="この画面の説明と案内"
            aria-expanded={helpOpen}
            aria-controls="help"
            onClick={() => setHelpOpen(!helpOpen)}
          >
            ？ ヘルプ
          </button>
        </h2>
        <div className="tabs" role="tablist">
          <button type="button" className={view === "list" ? "action small on" : "action small"} role="tab" aria-selected={view === "list"} data-action="show-list" onClick={() => showView("list")}>
            一覧
          </button>
          <button type="button" className={view === "graph" ? "action small on" : "action small"} role="tab" aria-selected={view === "graph"} data-action="show-graph" onClick={() => showView("graph")}>
            図
          </button>
        </div>
        <label className="order">
          全体計画の待ち方{" "}
          <select
            id="f-order"
            data-yaml-key="order"
            value={draft.order}
            disabled={busy || !editable}
            onChange={(event) => editDraft({ ...draft, order: event.target.value as PhaseOrder })}
          >
            {ORDERS.map((order) => (
              <option key={order} value={order}>
                {ORDER_LABELS[order]}
              </option>
            ))}
          </select>
        </label>
        {view === "list" && (
          <div className="find">
            <input id="find" type="search" placeholder="id・title・scope・when で絞り込む" spellCheck={false} value={find} onChange={(event) => setFind(event.target.value)} />
          </div>
        )}
        {helpOpen && (
          <div className="help-panel" id="help">
            <p className="hint">
            親チケットの <code>plan:</code> に <code>work</code> の種類を順に並べたものが全体計画で、<code>--approve</code> が通ることが合意になる。レビューのあとは{" "}
            <code>feedback:</code> に <code>feedback</code> の種類を並べて改版を出す。<code>id</code> と <code>title</code> はどちらも一意。<code>scope</code>{" "}
            は子チケットの範囲の上限（ワークツリーのルートからの glob。<code>inherit</code> なら親の範囲そのまま）、<code>deliverables</code> は閉じる前に存在し、git に追跡されているべきもの。
            <code>overlap</code> は並行してよい種類（対称）、<code>requires</code> は計画に置くなら一緒に必要な種類。<code>after</code> は待ち方が <code>dag</code> のときの依存（先に閉じてレビューが済んでいるべき種類）で、書かない種類は何も待たない。
            辺の書き漏れはそのまま並行として通るので、図で確かめる。待ち方は親チケットの承認のときに親へ写り、あとで直しても進行中の親には効かない。<code>agent</code> と <code>when</code> はエージェントへの案内にだけ使い、判定には効かない。
            関係の欄はこのファイルのほかの種類から選ぶ（層の画面では、ほかの層の種類の id を打って足せる）。範囲と成果物は <code>,</code> で区切る。
            </p>
            <p className="hint">
              図の「人が見る」は種類の宣言（<code>review</code>）で、計画の延期や実績のリスクで実際に見る場所は変わる。判定が使う待ち方は、層を合わせたうえで親チケットの承認のときに決まる（層のどれかが{" "}
              <code>sequential</code> なら <code>sequential</code>）。図はこのファイルの中だけを描くので、ほかの層の種類を指す関係は線にならない。
            </p>
            <button type="button" className="action small" data-action="tour" onClick={startTour}>
              案内をもう一度見る
            </button>
          </div>
        )}
        {view === "graph" && (
          <>
            <Graph graph={graph} onPick={pick} />
            <Legend />
            {graphNotices(graph, formOf(draft), page?.layer === true).map((notice) => (
              <p key={notice} className="graph-note">
                {notice}
              </p>
            ))}
          </>
        )}
        <ul className={view === "list" ? "list" : "list hidden"} id="phases">
          {rows.map((row) => (
            <Phase
              key={row.key}
              phaseKey={row.key}
              phase={row.phase}
              find={row.find}
              hidden={row.hidden}
              open={open.has(row.key)}
              moreOpen={more.get(row.key) === true}
              kinds={kinds}
              layer={page?.layer === true}
              duplicate={dup.has(row.phase.id.trim())}
              disabled={busy || !editable}
              onToggle={() => toggle(row.key)}
              onToggleMore={(value) => toggleMore(row.key, value)}
              onChange={(phase) => editRow(row.key, phase)}
              onMove={(delta) => move(row.key, delta)}
              onRemove={() => remove(row.key)}
            />
          ))}
          {draft.rows.length === 0 && <li className="empty">{emptyNote(page?.exists === true, editable)}</li>}
        </ul>
      </section>
      {touring && <Tour steps={tourSteps} onClose={endTour} />}
      <footer className={shownStatus?.error === true ? "foot error" : "foot"}>
        <span id="status">{shownStatus?.text ?? ""}</span>
      </footer>
    </>
  );
}

/**
 * ファイルが無いときの帯。共通層は「雛形で作る」まで欄を触れない。
 * 層（自身の層・プロジェクト）には雛形を置かない（雛形の id は共通層の種類と重なりやすく、
 * 中身が違えばその層が空として扱われる）。代わりに画面で足させ、最初の保存でファイルを作る。
 */
function Missing({ page, busy, onCreate }: { readonly page: PhasesPage; readonly busy: boolean; readonly onCreate: () => void }): JSX.Element {
  if (page.layer === true) {
    return (
      <div className="banner missing">
        <span>
          {page.phasesPath} が無い。無い層は空で、共通層の種類だけが使われる。この層に種類を足すなら、下で足して保存する（最初の保存でファイルが作られる）。雛形は置かない。雛形の id は共通層の種類と重なりやすく、中身が違えばこの層が空として扱われるため。
        </span>
      </div>
    );
  }
  return (
    <div className="banner missing">
      <span>
        {page.phasesPath} が無い。実行ファイルはフェーズを番号だけで扱っていて、親チケットの <code>plan:</code> も読めない。種類を使うにはまずファイルを作る。雛形は README の例で、
        <code>scope</code> の綴りは作ったあとにこのプロジェクトの置き場へ直す。
      </span>
      <button type="button" className="action primary" data-action="create" disabled={busy} onClick={onCreate}>
        雛形でファイルを作る
      </button>
    </div>
  );
}
