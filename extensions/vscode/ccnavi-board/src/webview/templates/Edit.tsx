/**
 * テンプレートの編集欄。説明・使う場面・計画のステップと線（`PlanEdit.tsx`）・子のフロー（`FlowEdit.tsx`）を直し、保存する。
 *
 * 編集中の中身（`EditTemplate`）はこの欄が持つ。直すたびに（少し待って）実行ファイルに読ませ（`editCheck`）、返ってきた
 * 苦情と図の中身をそのまま描く。読めるか・計画の順序・フローの形は実行ファイルの答えで、ここは判定しない。
 * 保存（`editSave`）は拡張ホストが、読ませ直して error が無く、読み返した中身が書こうとした中身と同じで、開いたときから
 * ファイルが外で変わっていないときだけ書く。断られたら理由を出して、編集は続けられる。
 *
 * 作業中のチケットがある間は編集はできるが保存できない（錠は拡張ホストが取り直して知らせる。`editLock`）。
 * 未保存のまま閉じようとすると、破棄してよいかを聞く。名前は変えられない（改名は持たない。ファイルを直接操作する）。
 */
import { useEffect, useMemo, useRef, useState, type JSX } from "react";

import { canRedo, canUndo, emptyHistory, record, redo, seal, undo, type FlowHistory } from "../../core/flow-history.js";
import { templateFlow, type FlowDoc } from "../../core/flow-doc.js";
import type { Lock } from "../../core/lock.js";
import { blankTemplate, fromData, removeFlow, setFlow, setMeta, toData, type EditTemplate } from "../../core/template-edit.js";
import type { EditOpened, TemplateCheck, ToTemplates } from "../../core/templates-view.js";
import { FlowEdit } from "./FlowEdit.js";
import { PlanEdit } from "./PlanEdit.js";
import { post } from "./post.js";
import { Problems } from "./Problems.js";
import { layerLabel } from "./text.js";

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

/** 全体計画の最初のステップにする型。`implement` があればそれ、無ければ作業の型の最初 */
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

export function Edit({ session, opened, onClose }: EditProps): JSX.Element {
  const start = useMemo((): EditTemplate | undefined => {
    const read = fromData(opened.data);
    if (read === undefined) {
      return undefined;
    }
    return opened.created ? blankTemplate(read.name, firstType(opened.check)) : read;
    // 開いたときの中身だけから作る（以降の編集は state が持つ）
  }, []);
  const [tpl, setTpl] = useState<EditTemplate | undefined>(start);
  const [history, setHistory] = useState<FlowHistory<EditTemplate>>(() => emptyHistory<EditTemplate>());
  /** 実行ファイルに読ませた答えと、読ませた中身のキー */
  const [checked, setChecked] = useState<{ readonly key: string; readonly check: TemplateCheck }>(() => ({
    key: keyOf(fromData(opened.data) ?? blankTemplate(opened.name, "implement")),
    check: opened.check,
  }));
  const [checkError, setCheckError] = useState("");
  const [lock, setLock] = useState<Lock>(opened.lock);
  const [created, setCreated] = useState(opened.created);
  const [baseSha, setBaseSha] = useState(opened.sha);
  const [savedKey, setSavedKey] = useState(() => (opened.created ? "" : keyOf(start ?? blankTemplate(opened.name, "implement"))));
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  /** 畳んだフローのステップの番号。フローは図を最初から見せるので、畳んだものだけを覚える */
  const [folded, setFolded] = useState<ReadonlySet<number>>(() => new Set());
  const [confirmClose, setConfirmClose] = useState(false);
  const seq = useRef(0);
  const asked = useRef<{ readonly seq: number; readonly key: string }>({ seq: 0, key: "" });
  const sent = useRef("");

  const key = tpl === undefined ? "" : keyOf(tpl);
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
          setChecked({ key: asked.current.key, check: message.check as TemplateCheck });
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
      asked.current = { seq: seq.current, key };
      post({ type: "editCheck", session, seq: seq.current, data: toData(tpl) });
    }, CHECK_DELAY_MS);
    return () => clearTimeout(timer);
  }, [tpl, key, fresh, session]);

  /** 閉じる。拡張ホストには、錠を取り直すのをやめてよいと知らせる */
  const finish = (): void => {
    post({ type: "editClose", session });
    onClose();
  };

  if (tpl === undefined) {
    return (
      <section className="tpl-edit" data-role="edit">
        <p className="status failed">読み込んだ中身を編集の形に直せませんでした。ファイルを直接直してください。</p>
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
  /** 計画の編集。フローのキーが振り直ったときは、畳んだフローがどのステップのものか分からなくなるので畳みを戻す */
  const updatePlan = (next: EditTemplate, typing?: string): void => {
    if (next.flows !== tpl.flows) {
      setFolded(new Set());
    }
    update(next, typing);
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

  const save = (): void => {
    setBusy(true);
    setStatus(undefined);
    sent.current = key;
    post({ type: "editSave", session, name: tpl.name, layer: opened.layer, expect: baseSha, data: toData(tpl) });
  };
  const close = (): void => {
    if (dirty && !confirmClose) {
      setConfirmClose(true);
      return;
    }
    finish();
  };

  const planOf = (part: "plan" | "feedback") => checked.check.plans.find((p) => p.part === part);
  const typeTitle = (type: string): string => checked.check.types.find((t) => t.id === type)?.title ?? type;

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
          <button type="button" className="action primary" data-action="save-edit" disabled={busy || !dirty || lock.locked} title={lock.locked ? lock.reason : undefined} onClick={save}>
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
          {status.text}
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
      <Problems problems={checked.check.problems} />
      <PlanEdit part="plan" template={tpl} plan={planOf("plan")} fresh={fresh} types={checked.check.types} onChange={updatePlan} />
      <PlanEdit part="feedback" template={tpl} plan={planOf("feedback")} fresh={fresh} types={checked.check.types} onChange={updatePlan} />
      <section className="tpl-edit-flows" data-role="flows">
        <h3>子のフロー</h3>
        <p className="hint">全体計画のステップごとに、子のフローを持てます。テンプレートを使うと、子のフローの下書きにこの文がコピーされます。</p>
        <ul className="list">
          {tpl.plan.map((item, i) => {
            const index = i + 1;
            const doc = tpl.flows[String(index)];
            const open = doc !== undefined && !folded.has(index);
            return (
              <li key={index} className={`row${open ? " open" : ""}`} data-index={index}>
                <div className="row-head">
                  <span className="mono">{index}</span> <span className="mono">{item.type}</span>
                  <span className="dim">{doc === undefined ? "フローなし" : `フロー: ${typeof doc.name === "string" && doc.name !== "" ? doc.name : "（名前なし）"}`}</span>
                  {doc === undefined ? (
                    <button
                      type="button"
                      className="action small"
                      data-action="add-flow"
                      onClick={() => {
                        update(setFlow(tpl, index, templateFlow(`${tpl.name}-${index}`, typeTitle(item.type))));
                        setFolded((f) => without(f, index));
                      }}
                    >
                      フローを足す
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
