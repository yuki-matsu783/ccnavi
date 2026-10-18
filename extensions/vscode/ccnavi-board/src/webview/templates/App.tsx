/**
 * テンプレート管理画面の本体。帯・件数の要約・テンプレートの一覧・統合結果の枠・下書きの一覧。
 *
 * 見せる中身は拡張ホストが渡す（`TemplatesData`）。画面が自分で持つのは、ユーザが触って決めるもの
 * （開いている行、本文の取り出しの結果）だけ。有効か・捨てた理由・取り込めるかは実行ファイルの答えを読むだけ。
 * 取り込み（取り込み先の選択・下見・取り込みの実行）の状態と配線もここで持つ。取り込みの欄は `ImportForm.tsx`、下見は `Preview.tsx`。 案内の間は見本（`core/tour-templates.ts`）を描き、実際の中身・開閉・取り込みの欄には触れない。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import type { Lock } from "../../core/lock.js";
import { defaultSum } from "../../core/sums.js";
import { editLockReason } from "../../core/templates-lock.js";
import type { DraftEntry, EditAnswer, EditOp, EditRequest, TemplateDetail, TemplateEntry, TemplatesData, TemplatesMessage, TemplatesPage, ToTemplates } from "../../core/templates-view.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { parseTemplatesSample } from "../../core/tour-templates.js";
import { applyAppearance } from "../appearance.js";
import { courseOf, coursesOf, Tour, TourButton, useTour } from "../Tour.js";
import templatesTour from "../tours/templates.yml";
import { TargetSelect } from "../TargetSelect.js";
import { Draft } from "./Draft.js";
import { EditPanel, type EditState } from "./EditPanel.js";
import { defaultImporting, requestOf, sameRequest, type Importing } from "./ImportForm.js";
import { post } from "./post.js";
import type { PreviewState } from "./Preview.js";
import { Sum } from "./Sum.js";
import { Template } from "./Template.js";
import { Problems } from "./Problems.js";
import { layerFor } from "./text.js";

/** 案内（`tours/templates.yml`）と、案内の間だけ描く見本。今の中身は使わず、画面が持つ開閉・取り込みの欄にも触れない */
const TOUR = tourOf(templatesTour);
const SAMPLE = parseTemplatesSample(TOUR.sample);
const NO_SCENE: Scene = {};

/** 手順の `scene` の文字列を `,` で割る */
function sceneList(value: Scene[string] | undefined): readonly string[] {
  return typeof value === "string" && value !== "" ? value.split(",").map((item) => item.trim()) : [];
}

/** 中身が無いときの錠。画面は取り込みを止めない理由が無いので、止めていない扱いにする */
const NO_LOCK: Lock = { locked: false, reason: "", doing: [] };

interface Status {
  readonly text: string;
  readonly error: boolean;
}

/** 1 件の本文の答え。`error` があれば本文は無い */
interface Detail {
  readonly detail?: TemplateDetail;
  readonly error: string;
}

function pageOf(data: TemplatesData): TemplatesPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

/** 二つの編集の要求が同じか（古い往復の答えを見分けるため。全欄を比べる） */
function sameEditRequest(a: EditRequest, b: EditRequest): boolean {
  return a.op === b.op && a.name === b.name && a.on === b.on && a.layer === b.layer && a.newName === b.newName && a.to === b.to && a.toLayer === b.toLayer && a.body === b.body;
}

/** 編集が書けたあとの帯の文面 */
function doneText(request: EditRequest, answer: EditAnswer): string {
  switch (request.op) {
    case "save":
      return `保存しました: ${answer.target}`;
    case "rename":
      return `改名しました: ${answer.target}`;
    case "move":
      return `移動しました: ${answer.target}`;
    default:
      return `削除しました: ${answer.source}`;
  }
}

export function App({ initial }: { readonly initial: TemplatesData }): JSX.Element {
  const [data, setData] = useState<TemplatesData>(initial);
  /** 取り込みの欄を開いている下書き（同時に 1 件）。undefined なら閉じている */
  const [importing, setImporting] = useState<Importing | undefined>(undefined);
  /** 取り込みの下見の答え。開いている欄の要求と同じものだけを入れる */
  const [preview, setPreview] = useState<PreviewState | undefined>(undefined);
  /** 下見・取り込みの往復の途中か */
  const [importBusy, setImportBusy] = useState(false);
  /** 編集の欄を開いている行のキー（`on:layer:name`）と状態（同時に 1 件）。undefined なら閉じている */
  const [editing, setEditing] = useState<{ readonly key: string; readonly state: EditState } | undefined>(undefined);
  const editingRef = useRef(editing);
  editingRef.current = editing;
  /** 未保存の変更がある間は真（開いているプレビューと、往復の途中、編集の欄）。拡張ホストへ `dirty` で伝える */
  const dirty = preview !== undefined || importBusy || editing !== undefined;
  /** 届いたメッセージの中で「いまの欄」を見るための ref（listener は最初の描画の値を持つため） */
  const importingRef = useRef(importing);
  importingRef.current = importing;
  const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lock, setLock] = useState<Lock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  /** プロジェクトごとの錠。プロジェクトへの取り込みのプレビューが使う */
  const [projectLocks, setProjectLocks] = useState<TemplatesPage["projectLocks"]>(() => pageOf(initial)?.projectLocks ?? {});
  const [status, setStatus] = useState<Status | undefined>(undefined);
  /** 開いているテンプレートのキー（`layer/name`） */
  const [openTemplates, setOpenTemplates] = useState<ReadonlySet<string>>(() => new Set());
  /** 開いている下書きの名前 */
  const [openDrafts, setOpenDrafts] = useState<ReadonlySet<string>>(() => new Set());
  /** 本文の答え（キーは `layer/name`） */
  const [details, setDetails] = useState<Readonly<Record<string, Detail>>>({});

  /** 案内の間の様子（開く行・開く下書き・取り込みの欄・統合結果）。実際の開閉や取り込みの欄は動かさないので、閉じたあとに戻すものは無い */
  const [scene, setScene] = useState<Scene>(NO_SCENE);
  const tour = useTour(data.kind === "page", {
    onStart: () => setScene(NO_SCENE),
    onEnd: () => {
      setScene(NO_SCENE);
      post({ type: "tourDone" });
    },
  });
  const requestTour = tour.request;
  /** 拡張ホストへの送信。案内の間は実際の操作を送らない（`tourDone` と `ready` は直に送る） */
  const act = (message: TemplatesMessage): void => {
    if (!tour.touring) {
      post(message);
    }
  };

  /** 未保存の変更の有無が変わったら拡張ホストに伝える。変わったときだけ送る */
  const sentDirty = useRef(false);
  useEffect(() => {
    if (sentDirty.current !== dirty) {
      sentDirty.current = dirty;
      post({ type: "dirty", dirty });
    }
  }, [dirty]);

  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToTemplates>;
      if (message.type === "data" && message.data !== undefined) {
        const next = message.data;
        setData(next);
        setChanged(false);
        setBusy(false);
        setStatus(undefined);
        setLock(pageOf(next)?.lock ?? NO_LOCK);
        setProjectLocks(pageOf(next)?.projectLocks ?? {});
        setDetails({});
        // 読み直しで取り込みの途中は破棄する（古い下書きの答えを見せないため）
        setImporting(undefined);
        setPreview(undefined);
        setImportBusy(false);
        // 読み直しで編集の途中も捨てる（古い行の答えを見せないため）
        setEditing(undefined);
      } else if (message.type === "failed") {
        setBusy(false);
        setStatus({ text: String(message.message ?? ""), error: true });
      } else if (message.type === "lock" && message.lock !== undefined) {
        setLock(message.lock);
        setProjectLocks(message.projectLocks ?? {});
      } else if (message.type === "changed") {
        setChanged(true);
      } else if (message.type === "cancelled") {
        // 頼んだ往復が起きなかった。止めた欄を戻す
        setBusy(false);
        setStatus(undefined);
      } else if (message.type === "tour") {
        requestTour();
      } else if (message.type === "detail" && message.name !== undefined && message.layer !== undefined) {
        const key = `${message.layer}/${message.name}`;
        setDetails((now) => ({ ...now, [key]: { detail: message.template ?? undefined, error: String(message.error ?? "") } }));
      } else if (message.type === "preview" && message.request !== undefined) {
        // 頼んだ下見と、いまの欄の要求が同じときだけ受ける。違えば古い往復なので捨てる
        const current = importingRef.current;
        if (current !== undefined && sameRequest(requestOf(current), message.request)) {
          setPreview({ request: message.request, answer: message.answer ?? null, error: String(message.error ?? "") });
          setImportBusy(false);
        }
      } else if (message.type === "imported" && message.request !== undefined) {
        setImportBusy(false);
        const error = String(message.error ?? "");
        const answer = message.answer ?? null;
        const current = importingRef.current;
        const mine = current !== undefined && sameRequest(requestOf(current), message.request);
        if (error !== "") {
          // 書けなかった。プレビューは残して、理由を見せる
          setStatus({ text: error, error: true });
        } else if (answer !== null && answer.written) {
          const kept = answer.removed ? "。下書きを削除しました" : answer.kept !== "" ? `。下書き: ${answer.kept}` : "";
          setStatus({ text: `取り込みました: ${answer.target}${kept}`, error: false });
          setPreview(undefined);
          setImporting(undefined);
        } else if (answer !== null && mine) {
          // 書かずに返った（問題で取り込めない）。答えを下見に入れ替えて見せる
          setPreview({ request: message.request, answer, error: "" });
          setStatus({ text: "取り込めませんでした", error: true });
        }
      } else if (message.type === "editPreview" && message.request !== undefined) {
        // 頼んだ下見と、いまの欄の要求が同じときだけ受ける。違えば古い往復なので捨てる
        const current = editingRef.current;
        if (current !== undefined && sameEditRequest(current.state.request, message.request)) {
          setEditing({ key: current.key, state: { ...current.state, answer: message.answer ?? null, error: String(message.error ?? ""), busy: false } });
        }
      } else if (message.type === "edited" && message.request !== undefined) {
        const current = editingRef.current;
        const mine = current !== undefined && sameEditRequest(current.state.request, message.request);
        const error = String(message.error ?? "");
        const answer = message.answer ?? null;
        if (error !== "") {
          // 書けなかった。欄は残して、理由を見せる
          setStatus({ text: error, error: true });
          if (mine && current !== undefined) {
            setEditing({ key: current.key, state: { ...current.state, busy: false } });
          }
        } else if (answer !== null && answer.ok && (answer.written || answer.removed)) {
          setStatus({ text: doneText(message.request, answer), error: false });
          setEditing(undefined);
        } else if (answer !== null && !answer.ok && (answer.written || answer.removed)) {
          // 途中で失敗した（新しい側は書けたが元を消せなかった、など）。欄は残し、ファイルの状態を読み直して確かめてもらう
          setStatus({ text: `途中で失敗しました: ${answer.errors.join(" / ") || "理由は不明です"}。一覧を再読み込みして、ファイルの状態を確認してください`, error: true });
          if (mine && current !== undefined) {
            setEditing({ key: current.key, state: { ...current.state, answer, error: "", busy: false } });
          }
        } else if (mine && current !== undefined) {
          // 書かずに返った（問題で実行できない）。答えを下見に入れ替えて見せる
          setEditing({ key: current.key, state: { ...current.state, answer, error: "", busy: false } });
          setStatus({ text: "実行できませんでした", error: true });
        }
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    // 組み上がったと伝える。非表示になって捨てられた画面は、再表示されると拡張ホストが入れてある HTML から作り直される。
    // その HTML は少し古いことがあるので、いまの中身をもらい直す
    post({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, [requestTour]);

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
        <p className="empty">テンプレート管理画面を読み込めませんでした。原因を直してから「更新」を押してください。別の設定を選べば、このタブの中身がその設定に替わります。</p>
        <TargetSelect target={data.target} targets={data.targets} onSwitch={(kind, name) => post({ type: "switchTarget", kind, name })} />
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" onClick={() => post({ type: "refresh", dirty: false })}>
          更新
        </button>
      </>
    );
  }

  const touring = tour.touring;
  /** 案内の間は見本を描く。実際の中身（`data.page`）は案内の間は使わない */
  const page = touring ? SAMPLE.page : data.page;
  const courses = coursesOf(TOUR, setScene);
  /** 案内の間の様子から作る、見本の開閉・取り込みの欄・下見 */
  const sceneOpenTemplates = new Set(sceneList(scene.open).map((name) => `self/${name}`));
  const sceneOpenDrafts = new Set(sceneList(scene.draft));
  const sceneDraft = typeof scene.import === "string" && scene.import !== "" ? SAMPLE.page.data.drafts.find((draft) => draft.name === scene.import) : undefined;
  const sceneImporting: Importing | undefined = sceneDraft === undefined ? undefined : defaultImporting(sceneDraft);
  const scenePreview: PreviewState | undefined =
    scene.preview === true && sceneImporting !== undefined ? { request: requestOf(sceneImporting), answer: SAMPLE.preview, error: "" } : undefined;
  const sceneSum = typeof scene.sum === "string" && scene.sum !== "" ? scene.sum : undefined;
  const shownLock: Lock = touring ? NO_LOCK : lock;
  const shownBusy = touring ? false : busy;
  const shownDirty = touring ? false : dirty;
  const shownStatus: Status | undefined = touring ? undefined : status;
  const layer = layerFor(page.target, page.data.layers);
  const shownTemplates: readonly TemplateEntry[] = layer?.templates ?? [];
  const count = shownTemplates.length;
  /** 案内が指すテンプレート（いま見えている一覧の最初の 1 件）。`data-tour` はこの 1 件にだけ付く */
  const first: TemplateEntry | undefined = shownTemplates[0];
  const firstKey = first === undefined ? undefined : `${first.layer}/${first.name}`;

  const toggleTemplate = (key: string): void => {
    if (tour.touring) {
      return;
    }
    setOpenTemplates((now) => {
      const next = new Set(now);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const toggleDraft = (name: string): void => {
    if (tour.touring) {
      return;
    }
    setOpenDrafts((now) => {
      const next = new Set(now);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const refresh = (): void => {
    if (tour.touring) {
      return;
    }
    setBusy(true);
    setStatus(undefined);
    act({ type: "refresh", dirty });
  };

  /** 取り込みの欄を開く。別の下書きの欄が開いていれば閉じ、そのプレビューも捨てる。案内の間は開かない */
  const openImport = (draft: DraftEntry): void => {
    if (tour.touring || importing?.draft === draft.name) {
      return;
    }
    // 編集の欄が開いていれば閉じる（同時に開くのは 1 件だけ）
    setEditing(undefined);
    setImporting(defaultImporting(draft));
    setPreview(undefined);
    setImportBusy(false);
  };

  const closeImport = (): void => {
    setImporting(undefined);
    setPreview(undefined);
    setImportBusy(false);
  };

  /** 取り込み先か置き換えを変えた。開いているプレビューと往復の途中は捨てる（古い答えを見せないため） */
  const changeImport = (next: Importing): void => {
    setImporting(next);
    setPreview(undefined);
    setImportBusy(false);
  };

  /** 下見を頼む。何も書かない */
  const requestPreview = (): void => {
    if (importing === undefined || tour.touring) {
      return;
    }
    setPreview(undefined);
    setImportBusy(true);
    setStatus(undefined);
    act({ type: "previewImport", request: requestOf(importing) });
  };

  /** 取り込みを実行する。見せた下書きの sha を `expect` に渡す */
  const importYes = (expect: string): void => {
    if (preview === undefined || tour.touring) {
      return;
    }
    setImportBusy(true);
    setStatus(undefined);
    act({ type: "importYes", request: preview.request, expect });
  };

  /** プレビューに渡す錠。共通への取り込みは全体の錠、プロジェクトへの取り込みはそのプロジェクトの錠（無ければ全体の錠） */
  const previewLock: Lock = preview === undefined || preview.request.to === "common" ? lock : (projectLocks[preview.request.layer] ?? lock);

  /** 編集の欄を開く。取り込みの欄が開いていれば閉じる。案内の間は開かない。同じ行・同じ操作の欄は開き直さない */
  const openEdit = (request: EditRequest): void => {
    if (tour.touring || (editing?.key === editKey(request) && editing.state.request.op === request.op)) {
      return;
    }
    closeImport();
    setEditing({ key: editKey(request), state: { request, answer: null, error: "", busy: false } });
  };

  const closeEdit = (): void => setEditing(undefined);

  /** 編集の欄を変えた。開いている下見は捨てる（古い答えを見せないため） */
  const changeEdit = (request: EditRequest): void => {
    if (editing !== undefined) {
      setEditing({ key: editing.key, state: { request, answer: null, error: "", busy: false } });
    }
  };

  /** 編集の下見を頼む。何も書かない */
  const previewEdit = (): void => {
    if (editing === undefined || tour.touring) {
      return;
    }
    setEditing({ key: editing.key, state: { ...editing.state, answer: null, error: "", busy: true } });
    setStatus(undefined);
    act({ type: "previewEdit", request: editing.state.request });
  };

  /** 編集を実行する。見せた主語の sha を `expect` に渡す */
  const executeEdit = (expect: string): void => {
    if (editing === undefined || tour.touring) {
      return;
    }
    setEditing({ key: editing.key, state: { ...editing.state, busy: true } });
    setStatus(undefined);
    act({ type: "editYes", request: editing.state.request, expect });
  };

  /** 行の編集の要求。欄の行キーに使う（`on:layer:name`。操作は含まない） */
  const editKey = (request: EditRequest): string => `${request.on}:${request.layer}:${request.name}`;

  /** 下書きの行の編集の要求 */
  const draftRequest = (draft: DraftEntry, op: EditOp): EditRequest => ({
    op,
    name: draft.name,
    on: "draft",
    layer: "",
    newName: "",
    to: "",
    toLayer: "",
    body: op === "save" ? draft.text : "",
  });

  /** テンプレートの行の編集の要求。移動の既定の行き先は共通。元が共通なら共通へは移せないので、最初の層へ移す */
  const templateRequest = (entry: TemplateEntry, op: EditOp): EditRequest => {
    const onCommon = entry.layer === "common";
    return {
      op,
      name: entry.name,
      on: onCommon ? "common" : "project",
      layer: onCommon ? "" : entry.layer,
      newName: "",
      to: op === "move" ? (onCommon ? "project" : "common") : "",
      toLayer: op === "move" && onCommon ? (page.importLayers[0] ?? "") : "",
      body: "",
    };
  };

  /** 開いている編集の欄を、その行の位置に出す。別の行のものなら出さない */
  const editPanelFor = (key: string): JSX.Element | null =>
    !touring && editing !== undefined && editing.key === key ? (
      <EditPanel
        state={editing.state}
        layers={page.importLayers}
        lockReason={editLockReason(editing.state.request, lock, projectLocks)}
        touring={tour.touring}
        onChange={changeEdit}
        onPreview={previewEdit}
        onExecute={executeEdit}
        onClose={closeEdit}
      />
    ) : null;

  const templateRow = (entry: TemplateEntry): JSX.Element => {
    const key = `${entry.layer}/${entry.name}`;
    const shown = details[key];
    const sampleText = touring && scene.body === true ? SAMPLE.texts[entry.name] : undefined;
    const sampleDetail: TemplateDetail | undefined = sampleText === undefined ? undefined : { ...entry, text: sampleText };
    return (
      <Template
        key={key}
        entry={entry}
        open={touring ? sceneOpenTemplates.has(key) : openTemplates.has(key)}
        onToggle={() => toggleTemplate(key)}
        detail={touring ? sampleDetail : shown?.detail}
        detailError={touring ? "" : (shown?.error ?? "")}
        onShow={() => act({ type: "showTemplate", name: entry.name, layer: entry.layer })}
        tour={key === firstKey}
        editEnabled={page.edit.enabled}
        editReason={page.edit.reason}
        onEdit={(op) => openEdit(templateRequest(entry, op))}
        editPanel={editPanelFor(editKey(templateRequest(entry, "rename")))}
      />
    );
  };

  /** 同じ名前の下書きが 2 件以上ある名前。名前では取り込み先を指せないので、取り込みは止める */
  const duplicateNames = new Set(page.data.drafts.map((draft) => draft.name).filter((name, index, all) => all.indexOf(name) !== index));
  // 案内の「取り込む…」は、取り込める最初の 1 件に付ける（先頭が取り込めないときは、その次へ送る）
  const importTourName = page.data.drafts.find((draft) => draft.importable && !duplicateNames.has(draft.name))?.name;

  return (
    <>
      {page.notices.map((notice, index) => (
        <div key={index} className="banner warn">
          {notice}
        </div>
      ))}
      <div id="changed" data-banner="changed" className={changed && !touring ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。更新してください（開いているプレビューは破棄されます）。
        <button type="button" className="action" data-action="reload" title="ファイルを再読み込みします" disabled={shownBusy} onClick={refresh}>
          更新
        </button>
      </div>
      {touring ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
      <header className="toolbar">
        <div className="summary" data-tour="target">
          <TargetSelect target={touring ? undefined : page.target} targets={touring ? undefined : page.targets} disabled={shownBusy} onSwitch={(kind, name) => act({ type: "switchTarget", kind, name })} />
          <span className="path" title={touring ? "" : page.root}>
            {touring ? `（見本）${layer?.path ?? ""}` : (layer?.path ?? "")}
          </span>
          <span id="dirty" className={shownDirty ? "dirty" : "dirty hidden"}>
            編集またはプレビューを開いています
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="reload" data-tour="refresh" title="ファイルを再読み込みします" disabled={shownBusy} onClick={refresh}>
            更新
          </button>
        </div>
        <TourButton courses={courses} onPick={tour.start} />
      </header>
      <p id="lock" data-banner="lock" className={shownLock.locked ? "lock" : "lock hidden"}>
        {shownLock.locked ? `取り込みと編集は止めています: ${shownLock.reason}` : ""}
      </p>
      <p className="count" id="counts" data-tour="summary">
        <span>テンプレート {shownTemplates.length} 件</span>・<span>下書き {page.data.drafts.length} 件</span>・<span>error {page.data.errors}</span>・<span>warn {page.data.warns}</span>・<span>info {page.data.infos}</span>
      </p>
      <section className="templates" data-tour="list">
        <h2>
          テンプレート <span className="count">{count}</span>
        </h2>
        {layer === undefined ? (
          <p className="empty">この設定のテンプレートの置き場を、実行ファイルが返していません。</p>
        ) : (
          <>
            {!layer.exists && <p className="hint">置き場がありません</p>}
            {layer.unreadable !== "" && <p className="status failed">読み込めません: {layer.unreadable}</p>}
            {!layer.writable && <span className="tag">読み取り専用</span>}
            <Problems problems={layer.problems} />
            {shownTemplates.length === 0 ? (
              <p className="empty">{"テンプレートがありません。共通は .ccnavi/config/common/templates/、プロジェクトは .ccnavi/config/project/templates/ に <名前>.yml で置きます。"}</p>
            ) : (
              <ul className="list">{shownTemplates.map((entry) => templateRow(entry))}</ul>
            )}
          </>
        )}
      </section>
      {page.data.sums.length > 0 && (
        <Sum
          sums={page.data.sums}
          initial={defaultSum(page.data.sums, touring ? undefined : page.target)}
          // 見本は別の枠として作る（閉じたあと、見本で選んだ統合結果が実際の枠に残らない）
          key={touring ? "tour" : `${page.target.kind}:${page.target.name}`}
          tour={touring ? { open: sceneSum !== undefined, picked: sceneSum ?? page.data.sums[0]?.name ?? "" } : undefined}
        />
      )}
      <section className="drafts" data-tour="drafts">
        <h2>
          下書き <span className="count">{page.data.drafts.length}</span>
        </h2>
        <p className="hint">エージェントが作成した効力のない下書きです。取り込むまでテンプレートとしては使われません。</p>
        {page.data.drafts.length === 0 ? (
          <p className="empty">{"下書きはありません。親チケットがクローズする前に ccnavi --templates --draft <親> で作れます。"}</p>
        ) : (
          <ul className="list">
            {page.data.drafts.map((draft, index) => (
              <Draft
                key={`${draft.tree}:${draft.name}`}
                draft={draft}
                open={touring ? sceneOpenDrafts.has(draft.name) : openDrafts.has(draft.name)}
                onToggle={() => toggleDraft(draft.name)}
                tour={index === 0}
                importTour={draft.name === importTourName}
                importing={touring ? sceneImporting : importing}
                ambiguous={duplicateNames.has(draft.name)}
                importLayers={page.importLayers}
                editEnabled={page.edit.enabled}
                editReason={page.edit.reason}
                onEdit={(op) => openEdit(draftRequest(draft, op))}
                editPanel={editPanelFor(editKey(draftRequest(draft, "save")))}
                onOpenImport={() => openImport(draft)}
                onCloseImport={closeImport}
                onImportChange={changeImport}
                onPreview={requestPreview}
                preview={touring ? scenePreview : preview}
                onClosePreview={() => setPreview(undefined)}
                importBusy={touring ? false : importBusy}
                lock={touring ? NO_LOCK : previewLock}
                onImport={importYes}
                touring={touring}
              />
            ))}
          </ul>
        )}
      </section>
      {tour.touring && <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} />}
      <footer className={shownStatus?.error === true ? "foot error" : "foot"}>
        {shownStatus !== undefined && (
          <span className={shownStatus.error ? "status failed" : "status"} id="status">
            {shownStatus.text}
          </span>
        )}
      </footer>
    </>
  );
}
