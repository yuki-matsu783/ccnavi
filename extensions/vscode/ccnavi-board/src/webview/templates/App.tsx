/**
 * テンプレート管理画面の本体。件数の要約・テンプレートの一覧・統合結果の枠・下書きの一覧。
 *
 * 見せる中身は拡張ホストが渡す（`TemplatesData`）。画面が自分で持つのは、ユーザが触って決めるもの
 * （開いている行、本文の取り出しの結果）だけ。有効か・破棄した理由は実行ファイルの答えを読むだけ。
 * 下書きは読むだけで、取り込みは「取り込み待ち」の画面が受け持つ（ここにあるのは入口だけ）。テンプレートの編集は `Edit.tsx`。
 * 案内の間は見本（`core/tour-templates.ts`）を描き、実際の中身・開閉には触れない。
 */
import { useRef, useState, type JSX } from "react";

import { defaultSum } from "../../core/sums.js";
import { templateNameTaken, validTemplateName } from "../../core/templatesmodel.js";
import type { EditOpened, TemplateCheck, TemplateDetail, TemplateEntry, TemplatesData, TemplatesMessage, TemplatesPage, ToTemplates } from "../../core/templates-view.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { parseTemplatesSample } from "../../core/tour-templates.js";
import { useHostMessages } from "../useHostMessages.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import templatesTour from "../tours/templates.yml";
import { TargetSelect } from "../TargetSelect.js";
import { HandoffLink } from "../shared/HandoffLink.js";
import { Draft } from "./Draft.js";
import { Edit } from "./Edit.js";
import { post } from "./post.js";
import { Sum } from "./Sum.js";
import { Template } from "./Template.js";
import { Problems } from "./Problems.js";
import { layerFor } from "./text.js";

/** 案内（`tours/templates.yml`）と、案内の間だけ描く見本。今の中身は使わず、画面が持つ開閉にも触れない */
const TOUR = tourOf(templatesTour);
const SAMPLE = parseTemplatesSample(TOUR.sample);
const NO_SCENE: Scene = {};

/** 手順の `scene` の文字列を `,` で割る */
function sceneList(value: Scene[string] | undefined): readonly string[] {
  return typeof value === "string" && value !== "" ? value.split(",").map((item) => item.trim()) : [];
}

/** 1 件の本文の答え。`error` があれば本文は無い */
interface Detail {
  readonly detail?: TemplateDetail;
  readonly error: string;
}

/** 編集の状態。始めるのを頼んでから答えが来るまでは `opened` が無い */
interface Editing {
  readonly session: number;
  readonly name: string;
  readonly opened?: EditOpened;
}

export function App({ initial }: { readonly initial: TemplatesData }): JSX.Element {
  const [data, setData] = useState<TemplatesData>(initial);
  const [busy, setBusy] = useState(false);
  /** 畳んだテンプレートのキー（`layer/name`）。行は最初から開いて図を見せるので、畳んだものだけを覚える */
  const [foldedTemplates, setFoldedTemplates] = useState<ReadonlySet<string>>(() => new Set());
  /** 開いている下書きの名前 */
  const [openDrafts, setOpenDrafts] = useState<ReadonlySet<string>>(() => new Set());
  /** 本文の答え（キーは `layer/name`） */
  const [details, setDetails] = useState<Readonly<Record<string, Detail>>>({});
  /** 図の中身の答え（キーは `layer/name`）。読めなければ `error` */
  const [previews, setPreviews] = useState<Readonly<Record<string, { readonly check?: TemplateCheck; readonly error: string }>>>({});
  /** 図の中身を頼んだ行（頼み直さないため）。読み直したら空にする */
  const asked = useRef<Set<string>>(new Set());
  /** 編集中のテンプレート。あるあいだは一覧の代わりに編集欄を出す */
  const [editing, setEditing] = useState<Editing | undefined>(undefined);
  /** 編集を始められなかった理由 */
  const [editError, setEditError] = useState("");
  /** 新しいテンプレートの名前の欄 */
  const [newName, setNewName] = useState("");
  const sessions = useRef(0);

  /** 案内の間の様子（開く行・開く下書き・統合結果）。実際の開閉は動かさないので、閉じたあとに戻すものは無い */
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

  useHostMessages<ToTemplates>((message) => {
    if (message.type === "data" && message.data !== undefined) {
      setData(message.data);
      setBusy(false);
      setDetails({});
      setPreviews({});
      asked.current = new Set();
    } else if (message.type === "tour") {
      requestTour();
    } else if (message.type === "editOpened") {
      // 最後に頼んだ編集の答えだけ受ける（頼み直した後に前の答えが届いても使わない）
      if (message.session !== sessions.current) {
        return;
      }
      if (message.opened) {
        const opened = message.opened as EditOpened;
        setEditing((now) => (now === undefined ? now : { ...now, opened }));
      } else {
        setEditError(String(message.error ?? "編集を始められませんでした"));
        setEditing(undefined);
      }
    } else if (message.type === "preview" && message.name !== undefined && message.layer !== undefined) {
      const key = `${message.layer}/${message.name}`;
      setPreviews((now) => ({ ...now, [key]: { check: (message.check as TemplateCheck | null) ?? undefined, error: String(message.error ?? "") } }));
    } else if (message.type === "detail" && message.name !== undefined && message.layer !== undefined) {
      const key = `${message.layer}/${message.name}`;
      setDetails((now) => ({ ...now, [key]: { detail: message.template ?? undefined, error: String(message.error ?? "") } }));
    }
  }, () => post({ type: "ready" }));

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
        <p className="empty">テンプレート管理画面を読み込めませんでした。原因を直してから「更新」を押してください。別の設定を選べば、このタブの中身がその設定に変わります。</p>
        <TargetSelect target={data.target} targets={data.targets} onSwitch={(kind, name) => post({ type: "switchTarget", kind, name })} />
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" onClick={() => post({ type: "refresh" })}>
          更新
        </button>
      </>
    );
  }

  const touring = tour.touring;
  /** 案内の間は見本を描く。実際の中身（`data.page`）は案内の間は使わない */
  const page: TemplatesPage = touring ? SAMPLE.page : data.page;
  const courses = coursesOf(TOUR, setScene);
  /** 案内の間の様子から作る、見本の開閉 */
  const sceneOpenTemplates = new Set(sceneList(scene.open).map((name) => `self/${name}`));
  const sceneOpenDrafts = new Set(sceneList(scene.draft));
  const sceneSum = typeof scene.sum === "string" && scene.sum !== "" ? scene.sum : undefined;
  const shownBusy = touring ? false : busy;
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
    setFoldedTemplates((now) => {
      const next = new Set(now);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  /** 図の中身を頼む（1 行につき 1 度。読み直すまで頼み直さない） */
  const askPreview = (entry: TemplateEntry): void => {
    const key = `${entry.layer}/${entry.name}`;
    if (tour.touring || asked.current.has(key)) {
      return;
    }
    asked.current.add(key);
    post({ type: "preview", name: entry.name, layer: entry.layer });
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
    act({ type: "refresh" });
  };

  /** 編集を始める。答え（`editOpened`）が届くまでは「読み込み中」を出す */
  const startEdit = (message: { readonly type: "editOpen"; readonly name: string; readonly layer: string } | { readonly type: "editNew"; readonly name: string }): void => {
    if (tour.touring) {
      return;
    }
    sessions.current += 1;
    setEditError("");
    setEditing({ session: sessions.current, name: message.name });
    post(message.type === "editOpen" ? { ...message, session: sessions.current } : { ...message, session: sessions.current });
  };

  const templateRow = (entry: TemplateEntry): JSX.Element => {
    const key = `${entry.layer}/${entry.name}`;
    const shown = details[key];
    const sampleText = touring && scene.body === true ? SAMPLE.texts[entry.name] : undefined;
    const sampleDetail: TemplateDetail | undefined = sampleText === undefined ? undefined : { ...entry, text: sampleText };
    return (
      <Template
        key={key}
        entry={entry}
        open={touring ? sceneOpenTemplates.has(key) : !foldedTemplates.has(key)}
        preview={touring ? undefined : previews[key]?.check}
        previewError={touring ? "" : (previews[key]?.error ?? "")}
        onPreview={touring || !entry.valid ? undefined : () => askPreview(entry)}
        onToggle={() => toggleTemplate(key)}
        detail={touring ? sampleDetail : shown?.detail}
        detailError={touring ? "" : (shown?.error ?? "")}
        onShow={() => act({ type: "showTemplate", name: entry.name, layer: entry.layer })}
        tour={key === firstKey}
        onEdit={!touring && entry.writable && entry.valid && layer?.writable === true ? () => startEdit({ type: "editOpen", name: entry.name, layer: entry.layer }) : undefined}
      />
    );
  };

  return (
    <>
      {page.notices.map((notice, index) => (
        <div key={index} className="banner warn">
          {notice}
        </div>
      ))}
      {touring ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
      <ScreenHeader courses={courses} onPick={tour.start}>
        <div className="summary" data-tour="target">
          <TargetSelect target={touring ? undefined : page.target} targets={touring ? undefined : page.targets} disabled={shownBusy || editing !== undefined} confirms={false} onSwitch={(kind, name) => act({ type: "switchTarget", kind, name })} />
          <span className="path" title={touring ? "" : page.root}>
            {touring ? `（見本）${layer?.path ?? ""}` : (layer?.path ?? "")}
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="reload" data-tour="refresh" title="ファイルを読み直します" disabled={shownBusy || editing !== undefined} onClick={refresh}>
            更新
          </button>
        </div>
      </ScreenHeader>
      {editing !== undefined && !touring ? (
        editing.opened === undefined ? (
          <p className="empty" id="edit-loading" data-role="edit-loading">
            {`テンプレート ${editing.name} を読み込み中…`}
          </p>
        ) : (
          <Edit key={editing.session} session={editing.session} opened={editing.opened} onClose={() => setEditing(undefined)} />
        )
      ) : (
        <>
        <p className="count" id="counts" data-tour="summary">
          <span>エラー {page.data.errors} 件</span>・<span>警告 {page.data.warns} 件</span>・<span>情報 {page.data.infos} 件</span>
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
              <Problems problems={layer.problems} />
              {editError !== "" && (
                <p className="status failed" role="alert" data-role="edit-error">
                  {editError}
                </p>
              )}
              {layer.writable && !touring && (
                <p className="tpl-new" data-role="new-template">
                  <label htmlFor="new-template-name" className="dim">
                    新しいテンプレートの名前
                  </label>{" "}
                  <input id="new-template-name" type="text" className="f-new-name" value={newName} placeholder="例: my-feature" onChange={(event) => setNewName(event.target.value.trim())} />{" "}
                  <button
                    type="button"
                    className="action small"
                    data-action="new-template"
                    disabled={!validTemplateName(newName) || templateNameTaken(page.data.layers, layer.name, newName) !== undefined}
                    title={newName === "" || validTemplateName(newName) ? "この層に新しいテンプレートを作ります。保存するまでファイルは作りません" : "英数字で始め、英数字と . _ - のみ、64 字まで"}
                    onClick={() => startEdit({ type: "editNew", name: newName })}
                  >
                    作る
                  </button>
                </p>
              )}
              {shownTemplates.length === 0 ? (
                <p className="empty">{"テンプレートがありません。共通は .ccnavi/config/common/templates/、プロジェクトは .ccnavi/config/project/templates/ の下に、<名前>.yml の名前で置きます。"}</p>
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
          <p className="hint">エージェントが作った効力のない下書きです。取り込むまでテンプレートとしては使われません。この画面では読むだけです。</p>
          <p className="handoff-link">
            <HandoffLink entry={page.handoff} disabled={touring} onOpen={() => act({ type: "openHandoff" })} />
          </p>
          {page.data.drafts.length === 0 ? (
            <p className="empty">{"下書きはありません。親チケットを閉じる前に、ccnavi --templates --draft <親> で作れます。"}</p>
          ) : (
            <ul className="list">
              {page.data.drafts.map((draft, index) => (
                <Draft
                  key={`${draft.tree}:${draft.path}:${draft.name}`}
                  draft={draft}
                  open={touring ? sceneOpenDrafts.has(draft.name) : openDrafts.has(draft.name)}
                  onToggle={() => toggleDraft(draft.name)}
                  tour={index === 0}
                />
              ))}
            </ul>
          )}
        </section>
        </>
      )}
      {tour.touring && <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} />}
    </>
  );
}
