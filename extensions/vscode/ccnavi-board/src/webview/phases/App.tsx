/**
 * フェーズ管理画面の本体。注意の帯と、フェーズ定義の一覧。
 *
 * 見せる中身は拡張ホストが渡す（`PhasesData`）。画面が持つのは、ユーザが触って決めるもの
 * （編集中の定義、開いている行、絞り込み、直前の操作の一言）だけ。定義の意味は判定しない。
 *
 * 中身（`data`）が届いたら、編集中の定義はその中身で置き換える。 届くのは編集を破棄してよい
 * ときだけ（ユーザが「再読み込み」を押した、保存や作成が通った）で、ファイルが外で変わっただけのときは
 * 帯（`changed`）が出るだけ。
 *
 * id の重なりだけは画面で止める。 同じ id が 2 つあると実行ファイルは後ろで何も出さずに上書きする。
 * 共通の設定の定義と表示名が重なる定義も止める（重なると、この設定の定義が 1 つも使われない）。
 * 止めるのはこの 2 つだけで、書式の検証と、共通の設定と同じ id の中身の比べは保存のときに実行ファイル（`--lint`）へ渡す。
 *
 * 共通の設定の定義は、開いている設定の定義に足されて判定に使われる。出どころが分かるように別の欄に
 * 読み取り専用で並べる（`PhasesPage.common`。実行ファイルの統合結果のうち出どころが共通のもの）。
 *
 * 順序（どのフェーズにどのフェーズが先行するか）はフェーズ定義ではなく親チケットの計画のステップの `after` で決める。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import { lockClass, type Lock } from "../../core/lock.js";
import type { CommonPhase, PhaseForm, PhasesData, PhasesMessage, PhasesPage, ToPhases } from "../../core/phases-view.js";
import { useHostMessages } from "../useHostMessages.js";
import { Phase } from "./Phase.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { parsePhasesSample, parsePhasesTexts } from "../../core/tour-phases.js";
import phasesTour from "../tours/phases.yml";
import { TargetSelect } from "../TargetSelect.js";
import { HandoffLink } from "../shared/HandoffLink.js";
import { post } from "./post.js";
import { Code } from "../shared/Code.js";
import { foldFind } from "../shared/find.js";
import { COMMON_NOTE, commonIdNote, commonTitleNote, countText, duplicateNote, emptyNote, findText, hasMore } from "./text.js";
import { commonClashes, draftOf, duplicates, emptyPhase, formOf, keyer, loadOpen, openedFromIds, saveOpen, type Draft } from "./state.js";

/** 中身が読めなかったときの錠。画面は保存させない */
const NO_LOCK: Lock = { locked: true, reason: "", doing: [] };

const EMPTY_DRAFT: Draft = { rows: [] };

/**
 * 案内（`tours/phases.yml`）と、案内の間だけ描く見本。今の定義は使わず、画面が持つ編集中の内容にも触れない。
 * 読み込み時に形を確かめる（壊れていればテストが先に落とす）。
 */
const TOUR = tourOf(phasesTour);
const SAMPLE = parsePhasesSample(TOUR.sample);
const TEXTS = parsePhasesTexts(TOUR.sample);
/** 見本の行のキー。実際の行のキー（`p1`…）と重ならない */
function sampleDraft(page: PhasesPage, prefix: string): Draft {
  let seq = 0;
  return draftOf(page.model.form, () => `${prefix}${(seq += 1)}`);
}
const SAMPLE_DRAFT = sampleDraft(SAMPLE.page, "s");
const PROJECT_DRAFT = sampleDraft(SAMPLE.project, "t");
/** 「＋ 定義を追加」で足した行の見本（`add: blank` は空の行、`add: filled` は書いた行） */
const ADDED_KEY = "s-add";
const ADDED_BLANK = emptyPhase();
const NO_SCENE: Scene = {};

/** 手順の `scene` の文字列を `,` で割る */
function sceneList(value: Scene[string] | undefined): ReadonlySet<string> {
  return new Set(typeof value === "string" && value !== "" ? value.split(",").map((item) => item.trim()) : []);
}

interface Status {
  readonly text: string;
  readonly error: boolean;
}

interface Editing {
  readonly draft: Draft;
  /** 開いている行のキー */
  readonly open: ReadonlySet<string>;
  /**
   * 「補足」を開いているか。行ごとに 1 度だけ値の有無で決め、あとはユーザの開閉で動く。
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
  /** 保存が通ったときの検証の warn の本文。帯に出し、次の編集か更新（中身の入れ替え）で消す */
  const [saveWarns, setSaveWarns] = useState<readonly string[]>([]);
  const [find, setFind] = useState("");
  const [focusKey, setFocusKey] = useState<string | undefined>(undefined);
  const [helpOpen, setHelpOpen] = useState(false);

  const { draft, open, more } = editing;
  const page = pageOf(data);
  /**
   * 案内の間は見本を描き、手順ごとの様子（開く定義・絞り込み・設定の切り替えなど）は `scene` に入れる。実際の編集中の定義や開いた行は
   * 動かさないので、閉じたあとに戻すものは無い。
   */
  const [scene, setScene] = useState<Scene>(NO_SCENE);
  const tour = useTour(data.kind === "page", {
    onStart: () => setScene(NO_SCENE),
    onEnd: () => {
      setScene(NO_SCENE);
      post({ type: "tourDone" });
    },
  });
  const requestTour = tour.request;
  /** 拡張ホストへの送信。案内の間は実際の操作を送らない（`tourDone` と最初の `ready` は直に送る） */
  const act = (message: PhasesMessage): void => {
    if (!tour.touring) {
      post(message);
    }
  };

  useHostMessages<ToPhases>((message) => {
    if (message.type === "data" && message.data !== undefined) {
      const next = message.data;
      setData(next);
      setEditing(editingOf(next, nextKey));
      setDirty(false);
      setBusy(false);
      setStatus(undefined);
      setChanged(false);
      setSaveWarns([]);
      setLock(pageOf(next)?.lock ?? NO_LOCK);
    } else if (message.type === "saved") {
      setSaveWarns(message.warns ?? []);
    } else if (message.type === "failed") {
      setBusy(false);
      setStatus({ text: String(message.message ?? ""), error: true });
    } else if (message.type === "lock" && message.lock !== undefined) {
      setLock(message.lock);
    } else if (message.type === "changed") {
      setChanged(true);
    } else if (message.type === "cancelled") {
      // 「破棄して読み直す？」の確認をやめたので、止めていた欄を戻す
      setBusy(false);
      setStatus(undefined);
    } else if (message.type === "tour") {
      requestTour();
    }
  }, () => post({ type: "ready" }));

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
   * 読み直しを頼む。押した時点で欄を止める。 拡張ホストは実行ファイルに聞いてから中身を返す
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
    if (tour.touring) {
      return;
    }
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

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
          フェーズ管理画面を読み込めませんでした。原因を直してから「更新」を押してください。別の設定を選べば、このタブの中身がその設定に変わります。
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
    if (tour.touring) {
      return;
    }
    setEditing((now) => ({ ...now, draft: next, open: open ?? now.open }));
    setDirty(true);
    setSaveWarns([]);
    // id が重なっている間はその文面だけを出す。直前の失敗の文面は今のことではない
    if (duplicates(next).size > 0) {
      setStatus(undefined);
    }
  };

  const editRow = (key: string, phase: PhaseForm): void => {
    editDraft({ ...draft, rows: draft.rows.map((row) => (row.key === key ? { ...row, phase } : row)) });
  };

  const toggle = (key: string): void => {
    if (tour.touring) {
      return;
    }
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
    if (tour.touring) {
      return;
    }
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

  const courses = coursesOf(TOUR, setScene);

  /** 定義を足す。絞り込みは外す（id が空の行は絞り込みにヒットせず隠れる） */
  const add = (): void => {
    if (tour.touring) {
      return;
    }
    const row = { key: nextKey(), phase: emptyPhase() };
    editDraft({ ...draft, rows: [...draft.rows, row] }, new Set([...open, row.key]));
    setFind("");
    // 足した定義は補足も空なので、「補足」は折りたたんで出す
    setEditing((now) => ({ ...now, more: new Map(now.more).set(row.key, false) }));
    setFocusKey(row.key);
  };

  // 案内の間は、今の定義ではなく見本を描く。画面の state（編集中の定義・開いた行・絞り込み・状態の一言）は触らない
  const touring = tour.touring;
  const sampleLib = scene.target === "lib";
  const shownPage: PhasesPage | undefined = touring ? (sampleLib ? SAMPLE.project : SAMPLE.page) : page;
  const sampleBase = sampleLib ? PROJECT_DRAFT : SAMPLE_DRAFT;
  /** 案内で足した行（`add`）。見本の末尾に付ける */
  const addedRow = touring && (scene.add === "blank" || scene.add === "filled") ? { key: ADDED_KEY, phase: scene.add === "blank" ? ADDED_BLANK : SAMPLE.added } : undefined;
  const shownDraft: Draft = !touring ? draft : addedRow === undefined ? sampleBase : { rows: [...sampleBase.rows, addedRow] };
  const shownDirty = touring ? scene.dirty === true : dirty;
  const shownBusy = touring ? false : busy;
  const sampleLocked = touring && scene.locked === true;
  const shownLock: Lock = touring
    ? sampleLocked
      ? { locked: true, reason: sampleLib ? TEXTS.locks.lib : TEXTS.locks.self, doing: [sampleLib ? "2026-10-05-lib-parser" : "2026-10-03-search-api"] }
      : { locked: false, reason: "", doing: [] }
    : lock;
  const sceneOpen = sceneList(scene.open);
  const sceneMore = sceneList(scene.more);
  const isShownOpen = (row: { readonly key: string; readonly phase: PhaseForm }): boolean => (touring ? row.key === ADDED_KEY || sceneOpen.has(row.phase.id) : open.has(row.key));
  const isShownMore = (row: { readonly key: string; readonly phase: PhaseForm }): boolean => (touring ? sceneMore.has(row.phase.id) : more.get(row.key) === true);
  const shownFind = touring ? (typeof scene.find === "string" ? scene.find : "") : find;
  const shownChanged = touring ? scene.changed === true : changed;
  const shownHelp = touring ? scene.help === true : helpOpen;

  const dup = touring ? new Set<string>() : duplicates(draft);
  const common = touring ? [] : (page?.common ?? []);
  const clash = commonClashes(touring ? { rows: [] } : draft, common);
  const query = foldFind(shownFind.trim());
  const rows = shownDraft.rows.map((row) => {
    const text = findText(row.phase);
    return { ...row, find: text, hidden: query !== "" && !text.includes(query) };
  });
  const shown = rows.filter((row) => !row.hidden).length;
  const kept = rows.filter((row) => row.hidden && isShownOpen(row)).length;
  /** 案内が指す定義（足した行、無ければ開いている最初の行、それも無ければ最初の行）。中の部品の `data-tour` はこの行にだけ付く */
  const tourRowKey = touring
    ? (addedRow?.key ?? rows.find((row) => !row.hidden && isShownOpen(row))?.key ?? rows.find((row) => !row.hidden)?.key)
    : undefined;
  const shownStatus: Status | undefined = touring
    ? scene.error === true
      ? { text: TEXTS.error, error: true }
      : undefined
    : dup.size > 0
      ? { text: duplicateNote(dup), error: true }
      : clash.titles.size > 0
        ? { text: commonTitleNote(clash.titles), error: true }
        : status;

  return (
    <>
      {(touring ? [] : page?.errors ?? []).map((error, index) => (
        <div key={`error-${index}`} className="banner error" data-banner="common-phases">
          {error}
        </div>
      ))}
      {(touring ? [] : page?.notices ?? []).map((notice, index) => (
        <div key={index} className="banner warn">
          {notice}
        </div>
      ))}
      {(touring ? [] : saveWarns).map((warn, index) => (
        <div key={`save-warn-${index}`} className="banner warn" data-banner="save-warn">
          <Code text={warn} />
        </div>
      ))}
      {touring ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
      <div id="changed" data-tour="changed" className={shownChanged ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。更新してください。
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={shownBusy} onClick={reload}>
          更新
        </button>
      </div>
      <ScreenHeader courses={courses} onPick={tour.start}>
        <div className="summary">
          <span data-tour="target">
            <TargetSelect target={shownPage?.target} targets={shownPage?.targets} disabled={shownBusy} onSwitch={(kind, name) => act({ type: "switchTarget", kind, name })} />
          </span>
          <span className="path" data-tour="path" title={touring ? "" : page?.root ?? ""}>
            {touring ? `（見本）${shownPage?.phasesPath ?? ""}` : page?.phasesPath ?? ""}
          </span>
          <span id="dirty" data-tour="dirty" className={shownDirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <HandoffLink entry={touring ? undefined : page?.handoff} disabled={touring} onOpen={() => act({ type: "openHandoff" })} />
          <button type="button" className="action" data-action="open-phases" data-tour="open-file" disabled={shownPage?.exists !== true} onClick={() => act({ type: "openFile" })}>
            エディタで開く
          </button>
          <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={shownBusy} onClick={reload}>
            更新
          </button>
          <button
            type="button"
            className="action primary"
            id="save"
            data-action="save"
            data-tour="save"
            disabled={!shownDirty || shownLock.locked || shownBusy || dup.size > 0 || clash.titles.size > 0}
            onClick={() => {
              if (tour.touring) {
                return;
              }
              setBusy(true);
              setStatus({ text: "検証して保存中…", error: false });
              post({ type: "save", form: formOf(draft) });
            }}
          >
            保存
          </button>
        </div>
      </ScreenHeader>
      <p id="lock" data-tour="lock" className={lockClass(shownLock)}>
        {shownLock.reason}
      </p>
      {!touring && page !== undefined && page.model.problems.length > 0 && (
        <ul className="problems">
          {page.model.problems.map((problem, index) => (
            <li key={index}>
              <Code text={problem} />
            </li>
          ))}
        </ul>
      )}
      <section className="block">
        <h2>
          フェーズ定義{" "}
          <span className="count" id="phase-count" data-tour="phase-count">
            {countText(shownDraft.rows.length, query, shown, kept)}
          </span>
          <button type="button" className="action small" data-action="add" data-tour="add" disabled={shownBusy} onClick={add}>
            ＋ 定義を追加
          </button>
          <button
            type="button"
            className={shownHelp ? "action small on" : "action small"}
            data-action="help"
            data-tour="help"
            title="この画面の説明"
            aria-expanded={shownHelp}
            aria-controls="help"
            onClick={() => {
              if (!tour.touring) {
                setHelpOpen(!helpOpen);
              }
            }}
          >
            ヘルプ
          </button>
        </h2>
        <div className="find" data-tour="find">
          <input
            id="find"
            type="search"
            placeholder="id・title・scope・when で絞り込む"
            spellCheck={false}
            value={shownFind}
            onChange={(event) => {
              if (!tour.touring) {
                setFind(event.target.value);
              }
            }}
          />
        </div>
        {shownHelp && (
          <div className="help-panel" id="help" data-tour="help-panel">
            <p className="hint">
              区分が <code>work</code> の定義を親チケットの計画に並べたものが全体計画で、ユーザが合意すると確定します。レビューのあとは、区分が <code>feedback</code> の定義を並べて計画を改訂します。<code>id</code> と <code>title</code> はどちらも一意です。<code>scope</code>{" "}
              は子チケットの編集可能な範囲の上限（ワークツリーのルートからの glob。<code>inherit</code> なら親の編集可能な範囲そのまま）、<code>deliverables</code> は必須成果物で、閉じる前に存在し、git に追跡されているべきものです。
              <code>agent</code> と <code>when</code> はエージェントへの案内にだけ使い、判定には使いません。編集可能な範囲と必須成果物は <code>,</code> で区切ります。
            </p>
            <details className="help">
              <summary>計画と合意のコマンド・キー</summary>
              <p className="hint">
                全体計画は親チケットの <code>plan:</code> に、計画の改訂は <code>feedback:</code> に書きます。合意は <code>ccnavi user agree</code> が通ることです。
              </p>
            </details>
            <p className="hint">
              順序（どのフェーズにどのフェーズが先行するか）はこのファイルには記載しません。親チケットの計画のステップに <code>after</code> で先行を指定します（最後のステップにほかの全部が先行するようにします）。
              親チケットは承認のときに使う定義のコピー（<code>phases:</code>）を持つので、あとでここを直しても進行中の親には反映されません。
            </p>
          </div>
        )}
        <ul className="list" id="phases" data-tour="phases">
          {rows.map((row, index) => (
            <Phase
              key={row.key}
              phaseKey={row.key}
              phase={row.phase}
              find={row.find}
              hidden={row.hidden}
              open={isShownOpen(row)}
              moreOpen={isShownMore(row)}
              duplicate={dup.has(row.phase.id.trim()) || clash.titles.has(row.phase.id.trim())}
              disabled={shownBusy}
              tour={row.key === tourRowKey}
              frozen={touring}
              onToggle={() => toggle(row.key)}
              onToggleMore={(value) => toggleMore(row.key, value)}
              onChange={(phase) => editRow(row.key, phase)}
              finding={query !== ""}
              first={index === 0}
              last={index === rows.length - 1}
              onMove={(delta) => move(row.key, delta)}
              onRemove={() => remove(row.key)}
            />
          ))}
          {rows.length > 0 && shown + kept === 0 && <li className="empty">絞り込みにヒットする定義がありません。語を変えるか、絞り込み欄を空にしてください</li>}
          {shownDraft.rows.length === 0 && <li className="empty">{emptyNote(shownPage?.exists === true)}</li>}
        </ul>
        {clash.ids.size > 0 && (
          <p className="hint" id="common-same-id">
            {commonIdNote(clash.ids)}
          </p>
        )}
      </section>
      {common.length > 0 && <CommonList common={common} />}
      {tour.touring && <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} />}
      <footer className={shownStatus?.error === true ? "foot error" : "foot"}>
        <span id="status" data-tour="status">
          <Code text={shownStatus?.text ?? ""} />
        </span>
      </footer>
    </>
  );
}

/** 共通の設定の定義（読み取り専用）。出どころが共通であることを見出しで言い、編集の欄は持たない */
function CommonList(props: { readonly common: readonly CommonPhase[] }): JSX.Element {
  return (
    <section className="block" id="common-phases">
      <h2>
        共通の設定の定義{" "}
        <span className="count" id="common-count">
          {props.common.length}
        </span>
      </h2>
      <p className="hint">{COMMON_NOTE}</p>
      <ul className="list">
        {props.common.map((phase) => (
          <li key={phase.id} className="common-phase" data-id={phase.id} data-source="common">
            <code>{phase.id}</code> {phase.title} <span>（区分 {phase.kind} / レビュー {String(phase.review)} / 編集可能な範囲 {phase.scope.join(", ")}）</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
