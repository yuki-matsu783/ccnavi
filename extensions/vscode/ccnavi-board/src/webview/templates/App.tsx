/**
 * テンプレート管理画面の本体。帯・件数の要約・表示の切り替え・テンプレートの一覧・下書きの一覧。
 *
 * 見せる中身は拡張ホストが渡す（`TemplatesData`）。画面が自分で持つのは、ユーザが触って決めるもの
 * （表示の切り替え、開いている行、本文の取り出しの結果）だけ。有効か・捨てた理由・取り込めるかは実行ファイルの答えを読むだけ。
 * 取り込み（取り込み先の選択・下見・取り込みの実行）の状態と配線もここで持つ。取り込みの欄は `ImportForm.tsx`、下見は `Preview.tsx`。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import type { Lock } from "../../core/lock.js";
import type { DraftEntry, TemplateDetail, TemplateEntry, TemplatesData, TemplatesMessage, TemplatesPage, ToTemplates } from "../../core/templates-view.js";
import { tourOf } from "../../core/tour-def.js";
import { applyAppearance } from "../appearance.js";
import { courseOf, coursesOf, Tour, TourButton, useTour } from "../Tour.js";
import templatesTour from "../tours/templates.yml";
import { TargetSelect } from "../TargetSelect.js";
import { Draft } from "./Draft.js";
import { defaultImporting, requestOf, sameRequest, type Importing } from "./ImportForm.js";
import { post } from "./post.js";
import type { PreviewState } from "./Preview.js";
import { loadView, saveView, type View } from "./state.js";
import { Template } from "./Template.js";
import { Problems } from "./Problems.js";
import { layerLabel, layersFor, templatesFor } from "./text.js";

/** 案内（`tours/templates.yml`）。画面は今の中身のまま指す（見本は使わない） */
const TOUR = tourOf(templatesTour);

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

export function App({ initial }: { readonly initial: TemplatesData }): JSX.Element {
  const [data, setData] = useState<TemplatesData>(initial);
  const [view, setView] = useState<View>(loadView);
  /** 取り込みの欄を開いている下書き（同時に 1 件）。undefined なら閉じている */
  const [importing, setImporting] = useState<Importing | undefined>(undefined);
  /** 取り込みの下見の答え。開いている欄の要求と同じものだけを入れる */
  const [preview, setPreview] = useState<PreviewState | undefined>(undefined);
  /** 下見・取り込みの往復の途中か */
  const [importBusy, setImportBusy] = useState(false);
  /** 未保存の変更がある間は真（開いているプレビューと、往復の途中）。拡張ホストへ `dirty` で伝える */
  const dirty = preview !== undefined || importBusy;
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

  const tour = useTour(data.kind === "page", { onEnd: () => post({ type: "tourDone" }) });
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
          const kept = answer.removed ? "。下書きを消しました" : answer.kept !== "" ? `。下書き: ${answer.kept}` : "";
          setStatus({ text: `取り込みました: ${answer.target}${kept}`, error: false });
          setPreview(undefined);
          setImporting(undefined);
        } else if (answer !== null && mine) {
          // 書かずに返った（問題で取り込めない）。答えを下見に入れ替えて見せる
          setPreview({ request: message.request, answer, error: "" });
          setStatus({ text: "取り込めませんでした", error: true });
        }
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    // 組み上がったと伝える。裏に回って捨てられた画面は、表に戻ると拡張ホストが入れてある HTML から作り直される。
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
        <button type="button" className="action" data-action="refresh" onClick={() => post({ type: "refresh", dirty: false })}>
          更新
        </button>
      </>
    );
  }

  const page = data.page;
  const courses = coursesOf(TOUR, () => undefined);
  const shownTemplates = templatesFor(page.target, page.data.templates);
  const shownLayers = layersFor(page.target, page.data.layers);
  const count = view === "sum" ? shownTemplates.length : shownLayers.reduce((total, layer) => total + layer.templates.length, 0);
  /** 案内が指すテンプレート（いま見えている一覧の最初の 1 件）。`data-tour` はこの 1 件にだけ付く */
  const first: TemplateEntry | undefined = view === "sum" ? shownTemplates[0] : shownLayers.flatMap((layer) => layer.templates)[0];
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

  const pick = (next: View): void => {
    setView(next);
    saveView(next);
  };

  /** 取り込みの欄を開く。別の下書きの欄が開いていれば閉じ、そのプレビューも捨てる。案内の間は開かない */
  const openImport = (draft: DraftEntry): void => {
    if (tour.touring || importing?.draft === draft.name) {
      return;
    }
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

  const templateRow = (entry: TemplateEntry): JSX.Element => {
    const key = `${entry.layer}/${entry.name}`;
    const shown = details[key];
    return (
      <Template
        key={key}
        entry={entry}
        open={openTemplates.has(key)}
        onToggle={() => toggleTemplate(key)}
        detail={shown?.detail}
        detailError={shown?.error ?? ""}
        onShow={() => act({ type: "showTemplate", name: entry.name, layer: entry.layer })}
        tour={key === firstKey}
      />
    );
  };

  /** 同じ名前の下書きが 2 件以上ある名前。名前では取り込み先を指せないので、取り込みは止める */
  const duplicateNames = new Set(page.data.drafts.map((draft) => draft.name).filter((name, index, all) => all.indexOf(name) !== index));
  // 案内の「取り込む…」は、取り込める最初の 1 件に付ける（先頭が取り込めないときは、その次へ送る）
  const importTourName = page.data.drafts.find((draft) => draft.importable && !duplicateNames.has(draft.name))?.name;

  return (
    <>
      <header className="toolbar">
        <div className="summary" data-tour="summary">
          <span>テンプレート {shownTemplates.length} 件</span>
          <span>下書き {page.data.drafts.length} 件</span>
          <span>error {page.data.errors}</span>
          <span>warn {page.data.warns}</span>
          <span>info {page.data.infos}</span>
        </div>
        <span data-tour="target">
          <TargetSelect target={page.target} targets={page.targets} disabled={tour.touring} onSwitch={(kind, name) => act({ type: "switchTarget", kind, name })} />
        </span>
        <div className="controls">
          <button type="button" className="action" data-action="refresh" data-tour="refresh" disabled={busy} onClick={refresh}>
            更新
          </button>
          <span data-tour="view" className="view-switch">
            <button type="button" className={view === "sum" ? "action small on" : "action small"} data-action="view-sum" aria-pressed={view === "sum"} onClick={() => pick("sum")}>
              足し算
            </button>
            <button type="button" className={view === "raw" ? "action small on" : "action small"} data-action="view-raw" aria-pressed={view === "raw"} onClick={() => pick("raw")}>
              層ごと
            </button>
          </span>
        </div>
        {dirty && <span className="dirty">プレビューを開いています</span>}
        <TourButton courses={courses} onPick={tour.start} />
      </header>
      {changed && (
        <div className="banner warn" data-banner="changed">
          ファイルが外で変わりました。「更新」で読み直します（開いているプレビューは破棄されます）。
        </div>
      )}
      {page.notices.map((notice, index) => (
        <div key={index} className="banner warn">
          {notice}
        </div>
      ))}
      {lock.locked && (
        <div className="lock" role="status" data-banner="lock">
          取り込みは止めています: {lock.reason}
        </div>
      )}
      <section className="templates" data-tour="list">
        <h2>
          テンプレート <span className="count">{count}</span>
        </h2>
        {view === "sum" ? (
          shownTemplates.length === 0 ? (
            <p className="empty">{"テンプレートがありません。共通は .ccnavi/config/common/templates/、プロジェクトは .ccnavi/config/project/templates/ に <名前>.yml で置きます。"}</p>
          ) : (
            <ul className="list">{shownTemplates.map((entry) => templateRow(entry))}</ul>
          )
        ) : (
          shownLayers.map((layer) => (
            <div className="tpl-layer" data-layer={layer.name} key={layer.name}>
              <h3>
                {layerLabel(layer.name)} <span className="mono small dim">{layer.path}</span>
              </h3>
              {!layer.exists && <p className="hint">置き場がありません</p>}
              {layer.unreadable !== "" && <p className="status failed">読めません: {layer.unreadable}</p>}
              {!layer.writable && <span className="tag">読み取り専用</span>}
              <Problems problems={layer.problems} />
              {layer.templates.length === 0 ? (
                <p className="hint">テンプレートはありません</p>
              ) : (
                <ul className="list">{layer.templates.map((entry) => templateRow(entry))}</ul>
              )}
            </div>
          ))
        )}
      </section>
      <section className="drafts" data-tour="drafts">
        <h2>
          下書き <span className="count">{page.data.drafts.length}</span>
        </h2>
        <p className="hint">エージェントが書いた効力のない下書きです。取り込むまでテンプレートとしては使われません。</p>
        {page.data.drafts.length === 0 ? (
          <p className="empty">{"下書きはありません。親チケットが閉じる前に ccnavi --templates --draft <親> で作れます。"}</p>
        ) : (
          <ul className="list">
            {page.data.drafts.map((draft, index) => (
              <Draft
                key={`${draft.tree}:${draft.name}`}
                draft={draft}
                open={openDrafts.has(draft.name)}
                onToggle={() => toggleDraft(draft.name)}
                tour={index === 0}
                importTour={draft.name === importTourName}
                importing={importing}
                ambiguous={duplicateNames.has(draft.name)}
                importLayers={page.importLayers}
                onOpenImport={() => openImport(draft)}
                onCloseImport={closeImport}
                onImportChange={changeImport}
                onPreview={requestPreview}
                preview={preview}
                onClosePreview={() => setPreview(undefined)}
                importBusy={importBusy}
                lock={previewLock}
                onImport={importYes}
                touring={tour.touring}
              />
            ))}
          </ul>
        )}
      </section>
      {tour.touring && <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} />}
      <footer className="foot">
        {status !== undefined && (
          <span className={status.error ? "status failed" : "status"} id="status">
            {status.text}
          </span>
        )}{" "}
        最終更新 {page.generatedAt}（{page.root}）
      </footer>
    </>
  );
}
