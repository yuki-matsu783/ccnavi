/**
 * 計画編集タブの本体。帯・ツールバー・計画ごとの図・理由の一覧・保存の確かめ。
 *
 * 見せる中身は拡張ホストが渡す（`WorkflowData`）。画面が持つのは、いま見せている実行ファイルの答え（`answer`）・
 * 答えを待っているか・点の置き場所・直前の一言・外で変わった知らせだけ。
 *
 * **画面は判定しない。番号も振り直さない。** 線を足す・消すと、元の番号での直接の先行（`--order` の形。
 * `core/workflow-view.ts` の `editedOrder`）を組んで拡張ホストに頼み（`order`）、答え（`answer`）が届いたら
 * その答えを描く。答えを待つ間は線を変えさせない（頼みと答えがずれないように）。断られた線（`refused`）と、
 * 確かめられなかったときは、線を引く前の答えのまま理由を出す。引けない線（循環・子が承認された番号へ入る線）は、
 * 図の部品が引いている最中に断る（`core/plan-graph.ts` の `canConnect`）。
 *
 * **未保存は答えから決める**（`isDirty`）。案に戻すは、往復せずに開いたときの答えへ戻す。未保存の間は、
 * 線のコピーを拡張ホストに覚えさせる（未保存のまま閉じられたときに戻すため）。
 *
 * **置き場所は動かさない。** 番号が振り直っても点に表示する番号だけが変わる。置き場所は振り直す前の番号で覚え
 * （`layoutOf`）、「並べ直す」で今の線から組み直す。
 *
 * **案内**（`Tour.tsx`）は図が出てから始める。案内の間は今の計画ではなく、案内用の見本（`tours/workflow.yml` の `sample:`。実行ファイルの
 * 答えの形）を描く。見本は名前を付けた答えの束で、手順の `scene` が描く答えを選ぶ。画面の state（実際の答え・未保存の線・置き場所・
 * 保存の確かめ）と拡張ホストへの送信（線の頼み・保存・再読み込み）には触れない。案内の間は、線も保存も効かない。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";

import { layoutOf, planGraphOf, type PlanLayout } from "../../core/plan-graph.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { parseWorkflowSample } from "../../core/tour-workflow.js";
import type { PlanOrder, PlanOrderAnswer } from "../../core/planordermodel.js";
import {
  changedOrder,
  CHECKING_TEXT,
  editedOrder,
  goneReasonOf,
  isDirty,
  planDiff,
  refusedText,
  renumbered,
  saveBlocker,
  type WorkflowData,
  type WorkflowPage,
} from "../../core/workflow-view.js";
import { ESCAPE_LAYER, useEscape } from "../useEscape.js";
import { useHostMessages } from "../useHostMessages.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import { PlanGraph } from "../shared/PlanGraph.js";
import workflowTour from "../tours/workflow.yml";
import { post } from "./post.js";
import { Review } from "./Review.js";
import { useGraphMarks } from "./tour-marks.js";

/** 未保存の線のコピーを拡張ホストへ送るまでの間（線を変えるたびに送らない） */
const DRAFT_MS = 300;

interface Status {
  readonly text: string;
  readonly error: boolean;
}

function pageOf(data: WorkflowData): WorkflowPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

/** 答えの計画ごとの置き場所 */
function layoutsOf(answer: PlanOrderAnswer | undefined): Readonly<Record<string, PlanLayout>> {
  const out: Record<string, PlanLayout> = {};
  for (const plan of answer?.plans ?? []) {
    out[plan.part] = layoutOf(planGraphOf(plan));
  }
  return out;
}

/** 計画の欄の言葉 */
function partName(part: string): string {
  return part === "feedback" ? "フィードバック計画" : "全体計画";
}

/**
 * 案内（`tours/workflow.yml`）と、案内の間だけ描く見本。今の計画は使わず、画面が持つ実際の答え・未保存の線・置き場所にも触れない。
 * 読み込み時に形を確かめる（壊れていればテストが先に落とす）。
 */
const TOUR = tourOf(workflowTour);
const SAMPLE = parseWorkflowSample(TOUR.sample);
const NO_SCENE: Scene = {};

/** 見本の答えごとの点の置き場所。`base`（その答えを引く前の答え）の置き場所のままにする（実際の画面と同じ）。`tidy` は今の線から組み直した置き場所 */
const SAMPLE_LAYOUTS: Readonly<Record<string, { readonly kept: Readonly<Record<string, PlanLayout>>; readonly tidy: Readonly<Record<string, PlanLayout>> }>> = Object.fromEntries(
  Object.entries(SAMPLE.answers).map(([name, sample]) => [name, { kept: layoutsOf(SAMPLE.answers[sample.base].answer), tidy: layoutsOf(sample.answer) }]),
);

/** 手順の `scene` が選ぶ答えの名前。無い・知らない名前は、開いたときの答え */
function sampleName(value: Scene[string] | undefined, fallback = "open"): string {
  return typeof value === "string" && value in SAMPLE.answers ? value : fallback;
}

/** 案内の間、図の部品へ何も頼まない（線を引く・消すは効かない） */
function noop(): void {
  // 案内の間は線を変えない
}

export function App({ initial }: { readonly initial: WorkflowData }): JSX.Element {
  const [data, setData] = useState<WorkflowData>(initial);
  const [answer, setAnswer] = useState<PlanOrderAnswer | undefined>(() => pageOf(initial)?.answer);
  const [layouts, setLayouts] = useState(() => layoutsOf(pageOf(initial)?.answer));
  const [fit, setFit] = useState(0);
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [changed, setChanged] = useState<PlanOrderAnswer | undefined>(undefined);
  const [showDiff, setShowDiff] = useState(false);
  const [gone, setGone] = useState<string | undefined>(undefined);
  const [review, setReview] = useState(false);
  const seq = useRef(0);
  /**
   * 案内の間は見本の答えを描き、手順ごとの様子（描く答え・開く確かめ・選ぶステップなど）は `scene` に入れる。
   * 実際の答え・未保存の線・置き場所・確かめは動かさないので、閉じたあとに戻すものは無い
   */
  const [scene, setScene] = useState<Scene>(NO_SCENE);
  // 初回の案内は図が出てから始める。保存の確かめの最中は、その下を指しても見えないので待つ
  const tour = useTour(data.kind === "page" && answer !== undefined && !review, {
    onStart: () => setScene(NO_SCENE),
    onEnd: () => {
      setScene(NO_SCENE);
      post({ type: "tourDone" });
    },
  });
  const requestTour = tour.request;
  const graphs = useRef<HTMLDivElement>(null);
  const touring = tour.touring;
  const shownName = sampleName(scene.answer);
  const picked = typeof scene.pick === "number" ? scene.pick : undefined;
  // 見本の図は、描く答え・並べ直した図か・選んだステップが変わるたびに作り直す（選んだステップや置き場所を前の手順から持ち越さない）
  const sceneKey = `${shownName}|${scene.tidy === true ? "tidy" : "kept"}|${picked ?? ""}`;
  useGraphMarks(graphs, touring, picked, sceneKey);

  /** 線を変えた先行で答えを頼む。前の頼みへの答えは、届いても破棄する */
  const ask = useCallback((order: PlanOrder) => {
    seq.current += 1;
    setPending(true);
    setStatus({ text: CHECKING_TEXT, error: false });
    post({ type: "order", seq: seq.current, order });
  }, []);

  useHostMessages((message) => {
    if (message.type === "data" && message.data !== undefined) {
      const next = message.data as WorkflowData;
      const page = pageOf(next);
      seq.current += 1;
      setData(next);
      setAnswer(page?.answer);
      setLayouts(layoutsOf(page?.answer));
      setFit((n) => n + 1);
      setPending(false);
      setBusy(false);
      setStatus(undefined);
      setChanged(undefined);
      setShowDiff(false);
      setGone(undefined);
      setReview(false);
      if (page?.draft !== undefined) {
        ask(page.draft);
      }
    } else if (message.type === "answer") {
      if (message.seq !== seq.current) {
        return;
      }
      setPending(false);
      const got = message.answer as PlanOrderAnswer | undefined;
      if (got === undefined) {
        setStatus({ text: `順序を確認できませんでした。線は引く前のままです: ${String(message.error ?? "")}`, error: true });
      } else if (got.refused.length > 0) {
        setStatus({ text: refusedText(got.refused), error: true });
      } else {
        setAnswer(got);
        setStatus(undefined);
      }
    } else if (message.type === "failed") {
      setBusy(false);
      setStatus({ text: String(message.message ?? ""), error: true });
    } else if (message.type === "cancelled") {
      setBusy(false);
      setStatus(undefined);
    } else if (message.type === "changed" && message.answer !== undefined) {
      setBusy(false);
      setChanged(message.answer as PlanOrderAnswer);
      setShowDiff(true);
    } else if (message.type === "gone") {
      setBusy(false);
      setPending(false);
      seq.current += 1;
      setReview(false);
      setGone(String(message.reason ?? ""));
    } else if (message.type === "tour") {
      requestTour();
    }
  }, () => post({ type: "ready" }));

  // 最初の中身に戻す線があれば、開いてすぐその線で答えを頼む
  useEffect(() => {
    const draft = pageOf(initial)?.draft;
    if (draft !== undefined) {
      ask(draft);
    }
    // 最初の 1 回だけ（`ask` は変わらない）。中身が入れ替わったときの頼み直しは、"data" の受信側が行う
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirty = useMemo(() => answer !== undefined && isDirty(answer), [answer]);

  // 拡張ホストへは、答えの未保存と、線を変えて答えを待っているか（`pending`）を送る。待つ間もボードの承認を
  // 止めるため（答えが届くまで未保存に数えないと、その間に承認が通る）。タイトルの「●」と「未保存」は答えから決める
  const sentDirty = useRef("false/false");
  useEffect(() => {
    const key = `${dirty}/${pending}`;
    if (sentDirty.current !== key) {
      sentDirty.current = key;
      post({ type: "dirty", dirty, checking: pending });
    }
  }, [dirty, pending]);

  // 未保存の線のコピーを、止まってから拡張ホストに覚えさせる。未保存でなくなったら消させる
  const sentDraft = useRef(false);
  useEffect(() => {
    if (!dirty || answer === undefined) {
      if (sentDraft.current) {
        sentDraft.current = false;
        post({ type: "draft", order: null });
      }
      return;
    }
    const timer = setTimeout(() => {
      sentDraft.current = true;
      post({ type: "draft", order: changedOrder(answer) });
    }, DRAFT_MS);
    return () => clearTimeout(timer);
  }, [answer, dirty]);

  useEscape(review, ESCAPE_LAYER.overlay, () => setReview(false));

  if (data.kind === "loading") {
    return (
      <p className="empty" id="ccnavi-loading">
        {data.text}
      </p>
    );
  }
  if (data.kind === "error" || answer === undefined) {
    return (
      <>
        <p className="empty">計画編集タブを読み込めませんでした。原因を直してから「再読み込み」を押してください。</p>
        <pre className="load-error">{data.kind === "error" ? data.error : ""}</pre>
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          再読み込み
        </button>
      </>
    );
  }
  const page = data.page;

  // 案内の間は、見本の答えと、手順の様子（`scene`）が描くものを使う。実際の答え・待ち・帯・確かめは使わない
  const sample = SAMPLE.answers[shownName];
  const sampleBase = SAMPLE.answers[sample.base].answer;
  const shownAnswer = touring ? sample.answer : answer;
  const shownTicket = touring ? SAMPLE.ticket : page.ticket;
  const shownPending = touring ? scene.checking === true : pending;
  const shownBusy = touring ? false : busy;
  const shownGone = touring ? (scene.gone === true ? goneReasonOf(SAMPLE.ticket, false, { proposal: null, approved: true }) : undefined) : gone;
  const shownChanged = touring ? SAMPLE.answers[sampleName(scene.changed, "")]?.answer : changed;
  const shownDiff = touring ? scene.diff !== false : showDiff;
  const shownReview = touring ? scene.review === true : review;
  const shownDirty = touring ? isDirty(sample.answer) : dirty;
  const sceneRefused = touring && typeof scene.refused === "string" ? SAMPLE.answers[sampleName(scene.refused)]?.answer.refused : undefined;
  const shownStatus: Status | undefined = touring
    ? sceneRefused !== undefined
      ? { text: refusedText(sceneRefused), error: true }
      : scene.checking === true
        ? { text: CHECKING_TEXT, error: false }
        : undefined
    : status;
  const shownLayouts = touring ? SAMPLE_LAYOUTS[shownName][scene.tidy === true ? "tidy" : "kept"] : layouts;
  const editable = !shownPending && !shownBusy && shownGone === undefined && !shownReview;
  const blocker = saveBlocker(shownAnswer, { pending: shownPending, changed: shownChanged !== undefined, gone: shownGone !== undefined });
  const moved = renumbered(shownAnswer);
  const notes = [
    ...shownAnswer.revision_problems.map((text) => ({ text, kind: "revision" })),
    ...shownAnswer.child_problems.map((text) => ({ text, kind: "child" })),
    ...shownAnswer.mismatched.map((m) => ({ text: m.text, kind: "mismatched" })),
  ];

  // 案内の間は、画面の state にも拡張ホストにも何も頼まない（押せるように見える部品も、ここで止める）
  const edit = (part: string, add: boolean) => (from: number, to: number): void => {
    if (touring || !editable) {
      return;
    }
    const order = editedOrder(answer, { part, from, to, add });
    if (order !== undefined) {
      ask(order);
    }
  };

  const revert = (): void => {
    if (touring) {
      return;
    }
    seq.current += 1;
    setPending(false);
    setAnswer(page.answer);
    setStatus({ text: "提案に記載されている順序に戻しました", error: false });
  };

  const relayout = (): void => {
    if (touring) {
      return;
    }
    setLayouts(layoutsOf(answer));
    setFit((n) => n + 1);
  };

  const reload = (): void => {
    if (touring) {
      return;
    }
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

  const confirmSave = (): void => {
    if (touring) {
      return;
    }
    setReview(false);
    setBusy(true);
    setStatus({ text: "提案を変更しています…", error: false });
    post({ type: "save", order: changedOrder(answer), expect: page.answer.source_sha });
  };

  const rows = shownChanged === undefined ? [] : planDiff((touring ? sampleBase : page.answer).plans, shownChanged.plans);
  const courses = coursesOf(TOUR, setScene);

  return (
    <>
      {shownGone !== undefined && (
        <div id="gone" className="lock" role="status" data-tour="gone">
          {shownGone}
        </div>
      )}
      {shownChanged !== undefined && shownGone === undefined && (
        <div id="changed" className="banner warn" role="status" data-tour="changed">
          <span className="workflow-banner-text">
            {shownDiff
              ? "提案が外で変わりました（エージェントが変更した可能性があります）。下の差分を見て、取り込むか編集を続けるかを選んでください。"
              : "提案が外で変わったため、保存できません。「取り込む」で新しい提案から始めてください（いまの編集は破棄します）。"}
          </span>
          <button
            type="button"
            className="action"
            data-action="adopt"
            data-tour="adopt"
            disabled={shownBusy}
            onClick={() => {
              if (!touring) {
                post({ type: "adopt" });
              }
            }}
          >
            {shownDirty ? "取り込む（編集を破棄する）" : "取り込む"}
          </button>
          {shownDiff && (
            <button
              type="button"
              className="action"
              data-action="keep-editing"
              onClick={() => {
                if (!touring) {
                  setShowDiff(false);
                }
              }}
            >
              編集を続ける（保存はできない）
            </button>
          )}
        </div>
      )}
      {shownChanged !== undefined && shownDiff && shownGone === undefined && (
        <section id="changed-diff" data-tour="changed-diff">
          {rows.length === 0 ? (
            <p className="dim small">計画の順序は同じです。子の提案か、親の提案のほかの欄が変わりました。</p>
          ) : (
            <table className="workflow-diff">
              <thead>
                <tr>
                  <th>計画</th>
                  <th>番号</th>
                  <th>開いたとき</th>
                  <th>いま</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.part}-${row.number}`} data-number={row.number}>
                    <td>{partName(row.part)}</td>
                    <td>{row.number}</td>
                    <td>{row.before}</td>
                    <td>{row.after}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
      <ScreenHeader courses={courses} onPick={tour.start}>
        <div className="summary" data-tour="summary">
          <span className="workflow-ticket">
            <strong>{shownTicket}</strong> の作業・レビュー計画の順序
          </span>
          <span id="dirty" data-tour="dirty" className={shownDirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls" data-tour="controls">
          <button type="button" className="action" data-action="relayout" data-tour="relayout" title="今の線で点の置き場所を並べ直します（番号は変わりません）" onClick={relayout}>
            並べ直す
          </button>
          <button
            type="button"
            className="action"
            data-action="revert"
            data-tour="revert"
            disabled={!shownDirty || shownBusy || shownGone !== undefined}
            title="提案に記載されている先行と番号に戻します"
            onClick={revert}
          >
            案に戻す
          </button>
          <button type="button" className="action" data-action="reload" data-tour="reload" disabled={shownBusy} onClick={reload}>
            再読み込み
          </button>
          <button
            type="button"
            className="action primary"
            id="save"
            data-action="save"
            data-tour="save"
            disabled={shownBusy || blocker !== ""}
            title={blocker === "" ? "提案の計画と、番号の変わるステップを指す子の提案を変更します（承認はしません）" : blocker}
            onClick={() => {
              if (!touring) {
                setReview(true);
              }
            }}
          >
            この順序に決める
          </button>
        </div>
      </ScreenHeader>
      {touring ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
      <p className="dim small">
        ステップの右の点から別のステップの左の点へドラッグすると、線の先のステップに、元のステップが先行します（逆向きでも引けます）。線の真ん中の × で先行を外せます。線を変えるたびに ccnavi が番号を振り直します。循環になる線と、子が承認された番号へ入る線は引けません。
      </p>
      {moved.length > 0 && (
        <p id="workflow-renumbered" className="small" data-tour="renumbered">
          番号が変わるステップ: {moved.map((item) => `${item.title} ${item.from} → ${item.number}`).join("、")}
        </p>
      )}
      {notes.length > 0 && (
        <ul className="problems" id="workflow-problems" data-tour="problems">
          {notes.map((note, index) => (
            <li key={index} data-kind={note.kind}>
              {note.text}
            </li>
          ))}
        </ul>
      )}
      <div className="workflow-graphs" data-tour="graphs" ref={graphs}>
        {shownAnswer.plans.length === 0 ? (
          <p className="empty">この親の提案には計画（plan:）がありません。</p>
        ) : touring ? (
          shownAnswer.plans.map((plan) => (
            <PlanGraph key={`${sceneKey}|${plan.part}`} plan={plan} detail editable={editable} onConnect={noop} onDisconnect={noop} layout={shownLayouts[plan.part]} fitKey={0} />
          ))
        ) : (
          shownAnswer.plans.map((plan) => (
            <PlanGraph
              key={plan.part}
              plan={plan}
              detail
              editable={editable}
              onConnect={edit(plan.part, true)}
              onDisconnect={edit(plan.part, false)}
              layout={shownLayouts[plan.part]}
              fitKey={fit}
            />
          ))
        )}
      </div>
      {shownReview && <Review ticket={shownTicket} answer={shownAnswer} focus={!touring} onConfirm={confirmSave} onCancel={() => (touring ? undefined : setReview(false))} />}
      <footer className={shownStatus?.error === true ? "foot error" : "foot"} data-tour="status">
        <span id="status">{shownStatus?.text ?? ""}</span>
      </footer>
      {touring ? <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} /> : null}
    </>
  );
}
