/**
 * テンプレートの編集欄。説明・使う場面・計画のステップと線（`PlanEdit.tsx`）・子のフロー（`FlowEdit.tsx`）を直し、保存する。
 *
 * 編集中の中身（`EditTemplate`）はこの欄が持つ。直すたびに（少し待って）実行ファイルに読ませ（`editCheck`）、返ってきた
 * 苦情と図の中身をそのまま描く。読めるか・計画の順序・フローの形は実行ファイルの答えで、ここは判定しない。
 * 保存（`editSave`）は拡張ホストが、読ませ直してerrorが無く、読み返した中身が書こうとした中身と同じで、開いたときから
 * ファイルが外で変わっていないときだけ書く。断られたら理由を出して、編集は続けられる。
 *
 * 作業中のチケットがある間は編集はできるが保存できない（錠は拡張ホストが取り直して知らせる。`editLock`）。
 * 元に戻す・やり直すは、ボタンとCtrl+Z / Ctrl+Shift+Z / Ctrl+Y（欄の中は欄に任せる）で受ける。
 * 未保存のまま閉じようとすると、破棄してよいかを聞く。名前は変えられない（改名は持たない。ファイルを直接操作する）。
 */
import { useEffect, useMemo, useRef, useState, type JSX } from "react";

import { canRedo, canUndo, emptyHistory, record, redo, seal, undo, type FlowHistory } from "../../core/flow-history.js";
import { templateFlow, type FlowDoc } from "../../core/flow-doc.js";
import type { Lock } from "../../core/lock.js";
import { blankTemplate, fromData, removeFlow, setFlow, setMeta, toData, untouched, type EditTemplate } from "../../core/template-edit.js";
import type { EditOpened, TemplateCheck, ToTemplates } from "../../core/templates-view.js";
import { FlowEdit } from "./FlowEdit.js";
import { PlanEdit } from "./PlanEdit.js";
import { post } from "./post.js";
import { useSaveKey } from "../useSaveKey.js";
import { editSaveBlocker, saveTitle } from "../../core/save-gate.js";
import { Problems } from "./Problems.js";
import { Code } from "../shared/Code.js";
import { layerLabel } from "./text.js";
import { historyKey } from "../shared/keys.js";

/** 直して読ませるまでの待ち（ミリ秒）。打つたびに実行ファイルを呼ばない */
const CHECK_DELAY_MS = 300;

export interface EditProps {
  /** 編集の番号。拡張ホストの答えを自分のものか見分ける */
  readonly session: number;
  readonly opened: EditOpened;
  readonly onClose: () => void;
}

interface Status {
  readonly text: string;
  readonly error: boolean;
}

/** 全体計画の最初のステップにする型。`implement`があればそれ、無ければ作業の型の最初 */
function firstType(check: TemplateCheck): string {
  const work = check.types.filter((t) => t.kind === "work");
  return (work.find((t) => t.id === "implement") ?? work[0])?.id ?? "implement";
}

function without(set: ReadonlySet<number>, index: number): ReadonlySet<number> {
  const next = new Set(set);
  next.delete(index);
  return next;
}

function keyOf(template: EditTemplate): string {
  return JSON.stringify(toData(template));
}

/** 読ませた中身のステップのid。図の番号を、いまの中身のステップへ引き直すのに使う（`PlanEdit.tsx`） */
interface ShownIds {
  readonly plan: readonly number[];
  readonly feedback: readonly number[];
}

function idsOf(template: EditTemplate): ShownIds {
  return { plan: template.plan.map((item) => item.id), feedback: template.feedback.map((item) => item.id) };
}

export function Edit({ session, opened, onClose }: EditProps): JSX.Element {
  const start = useMemo((): EditTemplate | undefined => {
    const read = fromData(opened.data);
    if (read === undefined) {
      return undefined;
    }
    return opened.created ? blankTemplate(read.name, firstType(opened.check)) : read;
    // 開いたときの中身だけから作る（以降の編集はstateが持つ）
  }, []);
  const [tpl, setTpl] = useState<EditTemplate | undefined>(start);
  const [history, setHistory] = useState<FlowHistory<EditTemplate>>(() => emptyHistory<EditTemplate>());
  /** 実行ファイルに読ませた答えと、読ませた中身のキーとステップのid */
  const [checked, setChecked] = useState<{ readonly key: string; readonly ids: ShownIds; readonly check: TemplateCheck }>(() => {
    const read = fromData(opened.data) ?? blankTemplate(opened.name, "implement");
    return { key: keyOf(read), ids: idsOf(read), check: opened.check };
  });
  const [checkError, setCheckError] = useState("");
  const [lock, setLock] = useState<Lock>(opened.lock);
  const [created, setCreated] = useState(opened.created);
  const [baseSha, setBaseSha] = useState(opened.sha);
  const [savedKey, setSavedKey] = useState(() => (opened.created ? "" : keyOf(start ?? blankTemplate(opened.name, "implement"))));
  /** 作った直後の空の雛形にerrorの苦情を出さないための印。一度触ったら、空に戻しても出す */
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  /** 畳んだフローのステップの番号。フローは図を最初から見せるので、畳んだものだけを覚える */
  const [folded, setFolded] = useState<ReadonlySet<number>>(() => new Set());
  const [confirmClose, setConfirmClose] = useState(false);
  /** 選んだステップのid（全体計画とフィードバック計画で1つ）。図の点・表の行・子のフローの行で目立たせる */
  const [picked, setPicked] = useState<number | undefined>(undefined);
  /** 子のフローへ移るときの行き先（全体計画の添字）。描き直したあとに画面を送る */
  const [scrollTo, setScrollTo] = useState<{ readonly index: number } | undefined>(undefined);
  useEffect(() => {
    if (scrollTo === undefined) {
      return;
    }
    const row = document.querySelector(`section[data-role="flows"] li[data-index="${scrollTo.index}"]`);
    (row as { scrollIntoView?: (options?: unknown) => void } | null)?.scrollIntoView?.({ block: "start" });
    setScrollTo(undefined);
  }, [scrollTo]);
  const seq = useRef(0);
  const asked = useRef<{ readonly seq: number; readonly key: string; readonly ids: ShownIds }>({ seq: 0, key: "", ids: { plan: [], feedback: [] } });
  const sent = useRef("");

  const key = tpl === undefined ? "" : keyOf(tpl);
  useEffect(() => {
    if (start !== undefined && tpl !== undefined && !touched && !untouched(tpl, blankTemplate(start.name, firstType(opened.check)))) {
      setTouched(true);
    }
  }, [tpl, touched]);
  const dirty = created || key !== savedKey;
  const fresh = checked.key === key;

  // 拡張ホストの答え（読ませた結果・保存の結果）。自分の番号のものだけ受ける
  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToTemplates> & Record<string, unknown>;
      if (message.session !== session) {
        return;
      }
      if (message.type === "editChecked") {
        // 最後に頼んだ答えだけ描く（古い答えが後から届いても描き換えない）
        if (message.seq !== asked.current.seq) {
          return;
        }
        if (message.check) {
          setChecked({ key: asked.current.key, ids: asked.current.ids, check: message.check as TemplateCheck });
          setCheckError("");
        } else {
          setCheckError(String(message.error ?? "実行ファイルで検査できませんでした"));
        }
      } else if (message.type === "editLock") {
        setLock(message.lock as Lock);
      } else if (message.type === "editSaved") {
        setBusy(false);
        if (typeof message.error === "string" && message.error !== "") {
          setStatus({ text: message.error, error: true });
          return;
        }
        setBaseSha(String(message.sha ?? ""));
        setCreated(false);
        setSavedKey(sent.current);
        setStatus({ text: "保存しました", error: false });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [session]);

  // 直したら、少し待ってから実行ファイルに読ませる
  useEffect(() => {
    if (tpl === undefined || fresh) {
      return;
    }
    const timer = setTimeout(() => {
      seq.current += 1;
      asked.current = { seq: seq.current, key, ids: idsOf(tpl) };
      post({ type: "editCheck", session, seq: seq.current, data: toData(tpl) });
    }, CHECK_DELAY_MS);
    return () => clearTimeout(timer);
  }, [tpl, key, fresh, session]);

  // キー入力を受け取る側は1度だけ張り、中身は描くたびに最新へ差し替える
  const onKey = useRef<(event: KeyboardEvent) => void>(() => undefined);
  useEffect(() => {
    const listener = (event: KeyboardEvent): void => onKey.current(event);
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);

  /** 閉じる。拡張ホストには、錠を取り直すのをやめてよいと知らせる */
  const finish = (): void => {
    post({ type: "editClose", session });
    onClose();
  };

  const saveBlocker = editSaveBlocker({ busy, dirty, lock });
  /** 保存。中身（`tpl`）が無いときは下の分岐で編集欄を表示しないので、Ctrl+Sもここで止める */
  const saveNow = useRef<() => void>(() => undefined);
  // 早く返す分岐より前に置く（フックの順番を変えない）。閉じる確認が出ている間は、その答えを待つ
  useSaveKey(tpl !== undefined && !confirmClose, () => ({ blocker: saveBlocker, busy, save: () => saveNow.current(), blocked: (reason) => setStatus({ text: reason, error: false }) }));

  if (tpl === undefined) {
    onKey.current = () => undefined;
    return (
      <section className="tpl-edit" data-role="edit">
        <p className="status failed">読み込んだ中身を編集用の形に変換できませんでした。ファイルを直接編集してください。</p>
        <button type="button" className="action" data-action="close-edit" onClick={finish}>
          閉じる
        </button>
      </section>
    );
  }

  const update = (next: EditTemplate, typing?: string): void => {
    if (next === tpl) {
      return;
    }
    setHistory((h) => record(h, tpl, typing === undefined ? {} : { key: typing, now: Date.now() }));
    setTpl(next);
    setStatus(undefined);
  };
  /**
   * 計画の編集。フローのキーが振り直ったときは、畳んだフローがどのステップのものか分からなくなるので畳みを戻す。
   * ステップと一緒に子のフローが消えたときは、そう知らせる（表の削除ボタンのtitleでも先に言う）
   */
  const updatePlan = (next: EditTemplate, typing?: string): void => {
    if (next.flows !== tpl.flows) {
      setFolded(new Set());
    }
    update(next, typing);
    const gone = tpl.plan.map((item, i) => ({ item, index: i + 1 })).filter(({ item, index }) => tpl.flows[String(index)] !== undefined && !next.plan.some((n) => n.id === item.id));
    if (gone.length > 0) {
      setStatus({ text: `${gone.map((g) => g.index).join(", ")}番のステップと一緒に、子のフローを消しました。戻すときは「元に戻す」を押してください`, error: false });
    }
  };
  const travel = (moved: { readonly history: FlowHistory<EditTemplate>; readonly doc: EditTemplate } | undefined): void => {
    if (moved === undefined) {
      return;
    }
    setHistory(moved.history);
    setTpl(moved.doc);
    setFolded(new Set());
    setStatus(undefined);
  };

  onKey.current = (event: KeyboardEvent): void => {
    const moved = busy ? undefined : historyKey(event);
    if (moved === "undo" && canUndo(history)) {
      event.preventDefault();
      travel(undo(history, tpl));
    } else if (moved === "redo" && canRedo(history)) {
      event.preventDefault();
      travel(redo(history, tpl));
    }
  };

  const save = (): void => {
    setTouched(true);
    setBusy(true);
    setStatus(undefined);
    sent.current = key;
    post({ type: "editSave", session, name: tpl.name, layer: opened.layer, expect: baseSha, data: toData(tpl) });
  };
  saveNow.current = save;
  const close = (): void => {
    if (dirty && !confirmClose) {
      setConfirmClose(true);
      return;
    }
    finish();
  };

  const planOf = (part: "plan" | "feedback") => checked.check.plans.find((p) => p.part === part);
  /** 型の定義のタイトル。無ければ型の名前 */
  const typeTitle = (type: string): string => {
    const title = checked.check.types.find((t) => t.id === type)?.title ?? "";
    return title === "" ? type : title;
  };
  /** 全体計画の`index`番のステップに子のフロー（雛形）を足して開く */
  const addFlow = (index: number): void => {
    const item = tpl.plan[index - 1];
    if (item === undefined || tpl.flows[String(index)] !== undefined) {
      return;
    }
    update(setFlow(tpl, index, templateFlow(`${tpl.name}-${index}`, typeTitle(item.type))));
    setFolded((f) => without(f, index));
  };
  /** 全体計画の`index`番のステップの子のフローを開き、画面をそこまで送る（送るのは描き直したあと） */
  const goFlow = (index: number): void => {
    setFolded((f) => without(f, index));
    setScrollTo({ index });
  };
  const stepProps = { picked, onPick: setPicked, onChange: updatePlan, onAddFlow: addFlow, onGoFlow: goFlow };

  return (
    <section className="tpl-edit" data-role="edit" data-name={tpl.name} data-layer={opened.layer}>
      <div className="tpl-edit-head">
        <h2>
          テンプレートを編集 <span className="mono">{tpl.name}</span> <span className="tag">{layerLabel(opened.layer)}</span>{" "}
          {created && <span className="tag">新規（まだ保存していません）</span>}
          {dirty && !created && <span className="tag warn" data-role="dirty">未保存</span>}
        </h2>
        <div className="controls">
          <button type="button" className="action" data-action="undo-edit" disabled={busy || !canUndo(history)} title="元に戻す" onClick={() => travel(undo(history, tpl))}>
            元に戻す
          </button>
          <button type="button" className="action" data-action="redo-edit" disabled={busy || !canRedo(history)} title="やり直す" onClick={() => travel(redo(history, tpl))}>
            やり直す
          </button>
          <button type="button" className="action primary" data-action="save-edit" disabled={saveBlocker !== ""} title={saveTitle(saveBlocker)} onClick={save}>
            保存
          </button>
          <button type="button" className="action" data-action="close-edit" disabled={busy} onClick={close}>
            閉じる
          </button>
        </div>
      </div>
      {lock.locked && (
        <div className="banner warn" role="status" data-role="edit-lock">
          {lock.reason}（編集はできますが、保存は作業が終わってからです）
        </div>
      )}
      {confirmClose && (
        <div className="banner warn" role="alert" data-role="confirm-close">
          保存していない変更があります。破棄して閉じますか？{" "}
          <button type="button" className="action small" data-action="discard-edit" onClick={finish}>
            破棄して閉じる
          </button>{" "}
          <button type="button" className="action small" data-action="keep-edit" onClick={() => setConfirmClose(false)}>
            編集を続ける
          </button>
        </div>
      )}
      {status !== undefined && (
        <p className={status.error ? "status failed" : "status"} role="status" data-role="edit-status">
          <Code text={status.text} />
        </p>
      )}
      {checkError !== "" && (
        <p className="status failed" data-role="check-error">
          実行ファイルで検査できません: {checkError}
        </p>
      )}
      <label className="field">
        <span className="dim">説明（一覧に出す文）</span>
        <textarea className="f-description" rows={2} value={tpl.description} onChange={(event) => update(setMeta(tpl, { description: event.target.value }), "meta:description")} onBlur={() => setHistory(seal)} />
      </label>
      <label className="field">
        <span className="dim">使う場面（when。選ぶ手がかり。省略できます）</span>
        <input type="text" className="f-when" value={tpl.when} onChange={(event) => update(setMeta(tpl, { when: event.target.value }), "meta:when")} onBlur={() => setHistory(seal)} />
      </label>
      <Problems problems={created && !touched ? checked.check.problems.filter((p) => p.severity !== "error") : checked.check.problems} />
      <PlanEdit part="plan" template={tpl} plan={planOf("plan")} fresh={fresh} shownIds={checked.ids.plan} types={checked.check.types} {...stepProps} />
      <PlanEdit part="feedback" template={tpl} plan={planOf("feedback")} fresh={fresh} shownIds={checked.ids.feedback} types={checked.check.types} {...stepProps} />
      <section className="tpl-edit-flows" data-role="flows">
        <h3>子のフロー</h3>
        <p className="hint">全体計画のステップごとに、子のフローを持てます。テンプレートを使うと、ここで書いた子のフローが子の下書きにコピーされます。</p>
        <ul className="list">
          {tpl.plan.map((item, i) => {
            const index = i + 1;
            const doc = tpl.flows[String(index)];
            const open = doc !== undefined && !folded.has(index);
            return (
              <li key={index} className={`row${open ? " open" : ""}`} data-index={index} data-picked={item.id === picked ? "1" : "0"}>
                <div className="row-head">
                  <span className="tpl-flow-heading">{`${index}:${typeTitle(item.type)} のフロー`}</span>
                  <span className="dim">{doc === undefined ? "フローなし" : `フロー: ${typeof doc.name === "string" && doc.name !== "" ? doc.name : "（名前なし）"}`}</span>
                  {doc === undefined ? (
                    <button type="button" className="action small" data-action="add-flow" onClick={() => addFlow(index)}>
                      フローを追加
                    </button>
                  ) : (
                    <>
                      <button type="button" className="action small" data-action="toggle-flow" aria-expanded={open} onClick={() => setFolded((f) => (open ? new Set(f).add(index) : without(f, index)))}>
                        {open ? "畳む" : "図を開く"}
                      </button>
                      <button
                        type="button"
                        className="action small"
                        data-action="remove-flow"
                        onClick={() => {
                          update(removeFlow(tpl, index));
                          setFolded((f) => without(f, index));
                        }}
                      >
                        フローを外す
                      </button>
                    </>
                  )}
                </div>
                {open && doc !== undefined && (
                  <div className="row-body">
                    <FlowEdit key={index} doc={doc as FlowDoc} onChange={(next, typing) => update(setFlow(tpl, index, next), typing === undefined ? undefined : `flow${index}:${typing}`)} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </section>
  );
}
