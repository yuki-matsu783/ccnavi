/**
 * フェーズ管理画面の本体。注意の帯と、フェーズ定義の一覧。
 *
 * 見せる中身は拡張ホストが渡す（`PhasesData`）。画面が持つのは、ユーザが触って決めるもの
 * （編集中の定義、開いている行、絞り込み、直前の操作の一言）だけ。定義の意味は判定しない。
 *
 * **中身（`data`）が届いたら、編集中の定義はその中身で置き換える。** 届くのは編集を捨ててよい
 * ときだけ（ユーザが「再読込」を押した、保存や作成が通った）で、ファイルが外で変わっただけのときは
 * 帯（`changed`）が出るだけ。
 *
 * **id の重なりだけは画面で止める。** 同じ id が 2 つあると実行ファイルは後ろで何も出さずに上書きする。
 * 止めるのはここだけで、書式の検証は保存のときに実行ファイル（`--lint`）へ渡す。
 *
 * **図と、全体計画の待ち方（`order`）の選択は持たない。** 順序（どのフェーズがどれを待つか）はフェーズ定義では
 * なく親チケットの計画の項の `after` で決め、実行ファイルも定義の順序の欄を読まない。ファイルに残った
 * 古い欄は、上の知らせ（`unreadNote`）が名指しするだけで、保存しても手を付けない。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import type { Lock } from "../../core/lock.js";
import type { PhaseForm, PhasesData, PhasesPage, ToPhases } from "../../core/phases-view.js";
import { applyAppearance } from "../appearance.js";
import { Phase } from "./Phase.js";
import { Tour, TourButton, type TourStep } from "../Tour.js";
import { TargetSelect } from "../TargetSelect.js";
import { post } from "./post.js";
import { countText, duplicateNote, emptyNote, findText, hasMore, unreadNote } from "./text.js";
import { draftOf, duplicates, emptyPhase, formOf, keyer, loadOpen, openedFromIds, saveOpen, type Draft } from "./state.js";

/** 中身が読めなかったときの錠。画面は保存させない */
const NO_LOCK: Lock = { locked: true, reason: "", doing: [] };

const EMPTY_DRAFT: Draft = { rows: [] };

/** 案内を始める前の画面の様子（`beforeTour`） */
interface TourSnapshot {
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
   * 「補足」を開いているか。**行ごとに 1 度だけ値の有無で決め、あとはユーザの開閉で動く。**
   * 描くたびに値の有無で決め直すと、最後の値を消した時点で、打っている欄ごと折りたたまれる
   */
  readonly more: ReadonlyMap<string, boolean>;
}

function pageOf(data: PhasesData): PhasesPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

function editingOf(data: PhasesData, nextKey: () => string): Editing {
  const page = pageOf(data);
  const draft = page === undefined ? EMPTY_DRAFT : draftOf(page.model.form, nextKey);
  const more = new Map(draft.rows.map((row) => [row.key, hasMore(row.phase)]));
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
  /** 吹き出しの案内を出しているか。拡張ホストの `tour`（初回）かヘルプのボタンで出る */
  const [touring, setTouring] = useState(false);
  /** 拡張ホストが初回の案内を頼んだが、中身がまだ無くて出せていない */
  const [tourPending, setTourPending] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  /** 案内を始める前の画面の様子。案内は見本の行と補足を開き、絞り込みを外すので、閉じたらこれに戻す */
  const beforeTour = useRef<TourSnapshot | undefined>(undefined);


  const { draft, open, more } = editing;
  const page = pageOf(data);

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

  // 足した行の id へ焦点を移す。折りたたんだままでは何を足したか分からないので、行は開いて出してある
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
   * ことがあり（設定ファイルの場所を解く）、その間に打った内容は、届いた中身で気づかないうちに消えるため。
   * ユーザが「破棄して読み直す？」をやめたときは `cancelled` が返り、欄が戻る。
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

  // 初回の案内は、定義の中身が出てから始める（読み込み中やエラーの画面には指す先が無い）
  useEffect(() => {
    if (tourPending && data.kind === "page") {
      setTourPending(false);
      if (!touring) {
        beforeTour.current = { find, open: editing.open, more: editing.more, data };
        setTouring(true);
      }
    }
  }, [tourPending, data, find, editing, touring]);

  // 案内の最中に中身が読み込み中やエラーへ替わった。吹き出しは描かれなくなるので、ここで閉じたことにする
  // （閉じずに残すと、中身が戻ったときにユーザが始めていない案内が 1 段目から出直す）。行の鍵は古いので戻さない
  useEffect(() => {
    if (touring && data.kind !== "page") {
      setTouring(false);
      const was = beforeTour.current;
      beforeTour.current = undefined;
      if (was !== undefined) {
        setFind(was.find);
      }
      post({ type: "tourDone" });
    }
  }, [touring, data]);

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
          フェーズ管理画面を読み込めませんでした。原因を直してから「更新」を押してください。別の設定を選べば、このタブの中身がその設定に替わります。
        </p>
        <TargetSelect target={data.target} targets={data.targets} onSwitch={(kind, name) => post({ type: "switchTarget", kind, name })} />
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          更新
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

  const startTour = (): void => {
    beforeTour.current = { find, open, more, data };
    setHelpOpen(false);
    setTouring(true);
  };

  /** 案内を閉じた（最後まで見ても、途中でやめても）。前の様子に戻し、拡張ホストに伝える（次からは初回の案内を出さない） */
  const endTour = (): void => {
    setTouring(false);
    const was = beforeTour.current;
    if (was !== undefined) {
      setFind(was.find);
      if (was.data === data) {
        setEditing((now) => ({ ...now, open: was.open, more: was.more }));
      }
    }
    beforeTour.current = undefined;
    post({ type: "tourDone" });
  };

  /** 案内で指す見本の行。補足（agent / when）を持つ最初の行、無ければ最初の行 */
  const sample = draft.rows.find((item) => item.phase.id.trim() !== "" && hasMore(item.phase)) ?? draft.rows[0];

  /** 見本の行とその補足を開く。絞り込みで隠れないよう外す（閉じたら戻す） */
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
      title: "フェーズ定義",
      body: "工程の型です。作業（work）の定義は親チケットの計画 plan: に、フィードバック対応（feedback）の定義はレビューのあとの feedback: に並べます。順序（どのフェーズがどれを待つか）は、親チケットの計画の項の after で決めます（この画面には書きません）。行を押すと欄が開き、「＋ 定義を追加」で増やせます。",
    },
    {
      target: sample === undefined ? "#phases" : `.phase[data-key="${sample.key}"] details.more`,
      title: "補足",
      body:
        sample === undefined
          ? "定義を足して行を開くと「補足」の欄があり、案内するエージェントと使う場面を書けます。どちらもエージェントへの案内にだけ使い、判定には使いません。"
          : "案内するエージェントと使う場面です。どちらもエージェントへの案内にだけ使い、判定には使いません。計画のどこに置くか（受入テスト作成を先に置く、フィードバック計画の最後に置く、など）も使う場面に書きます。",
      before: openSample,
    },
    {
      target: "#save",
      title: "保存",
      body: "保存すると実行ファイルが検証してから書き込みます。通らなければ、下に理由が出ます。",
    },
    {
      target: '[data-action="help"]',
      title: "ヘルプ",
      body: "細かい説明はここから開けます。",
    },
    {
      target: '[data-action="tour"]',
      title: "案内",
      body: "この案内は、ヘッダ右上の ? からもう一度見られます。",
    },
  ];

  /** 定義を足す。絞り込みは外す（id が空の行は絞り込みに当たらず隠れる） */
  const add = (): void => {
    const row = { key: nextKey(), phase: emptyPhase() };
    editDraft({ ...draft, rows: [...draft.rows, row] }, new Set([...open, row.key]));
    setFind("");
    // 足した定義は補足も空なので、「補足」は折りたたんで出す
    setEditing((now) => ({ ...now, more: new Map(now.more).set(row.key, false) }));
    setFocusKey(row.key);
  };

  const dup = duplicates(draft);
  const unread = page === undefined ? "" : unreadNote(page.model.unread);
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
      {(page?.errors ?? []).map((error, index) => (
        <div key={`error-${index}`} className="banner error" data-banner="common-phases">
          {error}
        </div>
      ))}
      {(page?.notices ?? []).map((notice, index) => (
        <div key={index} className="banner warn">
          {notice}
        </div>
      ))}
      {unread !== "" && (
        <div className="banner warn" id="unread">
          {unread}
        </div>
      )}
      <div id="changed" className={changed ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。更新してください。
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={reload}>
          更新
        </button>
      </div>
      <header className="toolbar">
        <div className="summary">
          <TargetSelect
            target={page?.target}
            targets={page?.targets}
            disabled={busy}
            onSwitch={(kind, name) => post({ type: "switchTarget", kind, name })}
          />
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
          <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={reload}>
            更新
          </button>
          <button
            type="button"
            className="action primary"
            id="save"
            data-action="save"
            disabled={!dirty || lock.locked || busy || dup.size > 0}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "検証して保存中…", error: false });
              post({ type: "save", form: formOf(draft) });
            }}
          >
            保存
          </button>
        </div>
        <TourButton onClick={startTour} />
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
      <section className="block">
        <h2>
          フェーズ定義{" "}
          <span className="count" id="phase-count">
            {countText(draft.rows.length, query, shown, kept)}
          </span>
          <button type="button" className="action small" data-action="add" disabled={busy} onClick={add}>
            ＋ 定義を追加
          </button>
          <button
            type="button"
            className={helpOpen ? "action small on" : "action small"}
            data-action="help"
            title="この画面の説明"
            aria-expanded={helpOpen}
            aria-controls="help"
            onClick={() => setHelpOpen(!helpOpen)}
          >
            ヘルプ
          </button>
        </h2>
        <div className="find">
          <input id="find" type="search" placeholder="id・title・scope・when で絞り込む" spellCheck={false} value={find} onChange={(event) => setFind(event.target.value)} />
        </div>
        {helpOpen && (
          <div className="help-panel" id="help">
            <p className="hint">
              親チケットの <code>plan:</code> に <code>work</code> の定義を並べたものが全体計画で、<code>--agree</code> が通ることが合意になります。レビューのあとは{" "}
              <code>feedback:</code> に <code>feedback</code> の定義を並べて計画を改訂します。<code>id</code> と <code>title</code> はどちらも一意です。<code>scope</code>{" "}
              は子チケットの範囲の上限（ワークツリーのルートからの glob。<code>inherit</code> なら親の範囲そのまま）、<code>deliverables</code> は閉じる前に存在し、git に追跡されているべきものです。
              <code>agent</code> と <code>when</code> はエージェントへの案内にだけ使い、判定には使いません。範囲と成果物は <code>,</code> で区切ります。
            </p>
            <p className="hint">
              順序（どのフェーズがどれを待つか）はこのファイルには書きません。親チケットの計画の項に <code>after</code> で先行を書きます（最後の項がほかの全部を待つようにします）。
              前の版の <code>order</code>・<code>after</code>・<code>overlap</code>・<code>requires</code> は実行ファイルが読まないので、この画面にも出しません。ファイルに残っていれば上に名指しし、保存しても欄はそのまま残ります。
              親チケットは承認のときに使う定義の写し（<code>phases:</code>）を持つので、あとでここを直しても進行中の親には反映されません。
            </p>
          </div>
        )}
        <ul className="list" id="phases">
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
              disabled={busy}
              onToggle={() => toggle(row.key)}
              onToggleMore={(value) => toggleMore(row.key, value)}
              onChange={(phase) => editRow(row.key, phase)}
              onMove={(delta) => move(row.key, delta)}
              onRemove={() => remove(row.key)}
            />
          ))}
          {draft.rows.length === 0 && <li className="empty">{emptyNote(page?.exists === true)}</li>}
        </ul>
      </section>
      {touring && <Tour steps={tourSteps} onClose={endTour} />}
      <footer className={shownStatus?.error === true ? "foot error" : "foot"}>
        <span id="status">{shownStatus?.text ?? ""}</span>
      </footer>
    </>
  );
}
