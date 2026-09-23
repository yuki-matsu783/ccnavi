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
import { editable as canEdit, ORDER_LABELS, ORDERS, type PhaseForm, type PhaseOrder, type PhasesData, type PhasesPage, type ToPhases } from "../../core/phases-view.js";
import { applyAppearance } from "../appearance.js";
import { Graph } from "./Graph.js";
import { Phase } from "./Phase.js";
import { post } from "./post.js";
import { countText, duplicateNote, emptyNote, findText, graphNote, hasRelations } from "./text.js";
import { draftOf, duplicates, emptyPhase, formOf, keyer, loadOpen, loadView, openedFromIds, saveOpen, saveView, type Draft, type View } from "./state.js";

/** 中身が読めなかったときの錠。画面は保存させない */
const NO_LOCK: Lock = { locked: true, reason: "", doing: [] };

const EMPTY_DRAFT: Draft = { order: "sequential", rows: [] };

interface Status {
  readonly text: string;
  readonly error: boolean;
}

interface Editing {
  readonly draft: Draft;
  /** 開いている行の鍵 */
  readonly open: ReadonlySet<string>;
  /**
   * 「関係と案内」を開いているか。**行ごとに 1 度だけ値の有無で決め、あとは人の開閉で動く。**
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
        {data.title}を読み込んでいる…
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

  const add = (): void => {
    const row = { key: nextKey(), phase: emptyPhase() };
    editDraft({ ...draft, rows: [...draft.rows, row] }, new Set([...open, row.key]));
    // 足した種類は関係も案内も空なので、「関係と案内」は畳んで出す
    setEditing((now) => ({ ...now, more: new Map(now.more).set(row.key, false) }));
    setFocusKey(row.key);
  };

  const dup = duplicates(draft);
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
        ファイルが外で変更されたので、画面の内容は古い。
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
        <details className="help">
          <summary>この画面の説明</summary>
          <p className="hint">
            親チケットの <code>plan:</code> に <code>work</code> の種類を順に並べたものが全体計画で、<code>--approve</code> が通ることが合意になる。レビューのあとは{" "}
            <code>feedback:</code> に <code>feedback</code> の種類を並べて改版を出す。<code>id</code> と <code>title</code> はどちらも一意。<code>scope</code>{" "}
            は子チケットの範囲の上限（ワークツリーのルートからの glob。<code>inherit</code> なら親の範囲そのまま）、<code>deliverables</code> は閉じる前に存在し、git に追跡されているべきもの。
            <code>overlap</code> は並行してよい種類（対称）、<code>requires</code> は計画に置くなら一緒に要る種類。<code>after</code> は待ち方が <code>dag</code> のときの依存（先に閉じてレビューが済んでいるべき種類）で、書かない種類は何も待たない。
            辺の書き漏れはそのまま並行として通るので、図で確かめる。待ち方は親チケットの承認のときに親へ写り、あとで直しても進行中の親には効かない。<code>agent</code> と <code>when</code> は案内にだけ使う。並びの欄は{" "}
            <code>,</code> で区切る。
          </p>
        </details>
        {view === "graph" && (
          <>
            <Graph graph={graph} onPick={pick} />
            <p className="graph-note">{graphNote(graph)}</p>
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
