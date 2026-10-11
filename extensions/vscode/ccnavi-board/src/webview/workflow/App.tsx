/**
 * ワークフロー編集タブの本体。帯・ツールバー・計画ごとの図・理由の一覧・保存の確かめ。
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
 * 線の写しを拡張ホストに覚えさせる（未保存のまま閉じられたときに戻すため）。
 *
 * **置き場所は動かさない。** 番号が振り直っても点の名札の番号だけが変わる。置き場所は振り直す前の番号で覚え
 * （`layoutOf`）、「並べ直す」で今の線から組み直す。
 *
 * **初回の案内**（`Tour.tsx`）は図が出てから始める。図を出せない答え（計画が空）では、案内の間だけ見本の計画
 * （`core/tour-sample.ts` の `sampleWorkflow`。実行ファイルの答えの実例）を読むだけで出す。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";

import { layoutOf, planGraphOf, type PlanLayout } from "../../core/plan-graph.js";
import { SAMPLE_NOTE, sampleWorkflow } from "../../core/tour-sample.js";
import type { PlanOrder, PlanOrderAnswer } from "../../core/planordermodel.js";
import { changedOrder, editedOrder, isDirty, planDiff, renumbered, saveBlocker, type WorkflowData, type WorkflowPage } from "../../core/workflow-view.js";
import { applyAppearance } from "../appearance.js";
import { Tour, TourButton, useTour, type TourStep } from "../Tour.js";
import { PlanGraph } from "../shared/PlanGraph.js";
import { post } from "./post.js";
import { Review } from "./Review.js";

/** 未保存の線の写しを拡張ホストへ送るまでの間（線を変えるたびに送らない） */
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

/** タブの案内。画面の様子は動かさないので、閉じても戻すものは無い */
const TOUR_STEPS: readonly TourStep[] = [
  {
    target: ".workflow-graphs",
    title: "図",
    body:
      "点は親の計画の項で、線はどの項がどの項を待つか（after）です。項の右の点から別の項の左の点へドラッグすると、線の先の項が元の項を待ちます（逆向きでも引けます）。線の真ん中の × で待たなくなります。" +
      "線を変えるたびに ccnavi が番号を振り直します。循環になる線と、子が承認された番号へ入る線は引けません。最後の項が待っていない項は注意の色で出ます。",
  },
  {
    target: ".toolbar .controls",
    title: "直す手段",
    body: "「並べ直す」は今の線で点の置き場所を組み直します（番号は変わりません）。「案に戻す」は提案に書いてある順序に戻し、「再読込」は提案を読み直します。",
  },
  {
    target: "#save",
    title: "この順序に決める",
    body:
      "番号の変わる項と、付け替える子の提案を一覧で確かめてから、提案の計画（plan: / feedback:）と子の提案を書き換えます。承認はしません。書き換えた提案はコミットされません。" +
      "エージェントが提案を書き直していたら、何も書かずに差分を出します。",
  },
  {
    target: ".toolbar .summary",
    title: "承認との関係",
    body:
      "未保存の変更があるあいだは、チケット管理画面でこの親を含む一覧を承認できません。保存したら、承認はチケット管理画面から開き直してください。" +
      "保存したあとにエージェントが提案を書き換えると、承認の画面に注意が出ます。",
  },
  {
    target: '[data-action="tour"]',
    title: "案内",
    body: "この案内は、ヘッダ右上の ? からもう一度見られます。",
  },
];

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
  // 初回の案内は図が出てから始める。保存の確かめの最中は、その下を指しても見えないので待つ
  const tour = useTour(data.kind === "page" && answer !== undefined && !review, { onEnd: () => post({ type: "tourDone" }) });
  const requestTour = tour.request;

  /** 線を変えた先行で答えを頼む。前の頼みへの答えは、届いても捨てる */
  const ask = useCallback((order: PlanOrder) => {
    seq.current += 1;
    setPending(true);
    setStatus({ text: "線を変えた順序を ccnavi に確かめています…", error: false });
    post({ type: "order", seq: seq.current, order });
  }, []);

  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as { type?: unknown; [key: string]: unknown };
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
          setStatus({ text: `順序を確かめられませんでした。線は引く前のままです: ${String(message.error ?? "")}`, error: true });
        } else if (got.refused.length > 0) {
          setStatus({ text: `この線は引けません。線は引く前のままです: ${got.refused.map((r) => r.text).join("、")}`, error: true });
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
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    post({ type: "ready" });
    // 最初の中身に戻す線があれば、開いてすぐその線で答えを頼む
    const draft = pageOf(initial)?.draft;
    if (draft !== undefined) {
      ask(draft);
    }
    return () => window.removeEventListener("message", onMessage);
    // 最初の 1 回だけ（`ask` と `requestTour` は変わらない）。中身が入れ替わったときの頼み直しは "data" の受けがする
  }, [ask, requestTour]);

  const dirty = useMemo(() => answer !== undefined && isDirty(answer), [answer]);

  // 拡張ホストへは、答えの未保存と、線を変えて答えを待っているか（`pending`）を送る。待つ間もボードの承認を
  // 止めるため（答えが届くまで未保存に数えないと、その間に承認が通る）。題の「●」と「未保存」は答えから決める
  const sentDirty = useRef("false/false");
  useEffect(() => {
    const key = `${dirty}/${pending}`;
    if (sentDirty.current !== key) {
      sentDirty.current = key;
      post({ type: "dirty", dirty, checking: pending });
    }
  }, [dirty, pending]);

  // 未保存の線の写しを、止まってから拡張ホストに覚えさせる。未保存でなくなったら消させる
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

  useEffect(() => {
    if (!review) {
      return;
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        setReview(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [review]);

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
        <p className="empty">ワークフロー編集タブを読み込めませんでした。原因を直してから「再読込」を押してください。</p>
        <pre className="load-error">{data.kind === "error" ? data.error : ""}</pre>
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          再読込
        </button>
      </>
    );
  }
  const page = data.page;
  const editable = !pending && !busy && gone === undefined && !review;
  const blocker = saveBlocker(answer, { pending, changed: changed !== undefined, gone: gone !== undefined });
  const moved = renumbered(answer);
  const notes = [
    ...answer.revision_problems.map((text) => ({ text, kind: "revision" })),
    ...answer.child_problems.map((text) => ({ text, kind: "child" })),
    ...answer.mismatched.map((m) => ({ text: m.text, kind: "mismatched" })),
  ];

  const edit = (part: string, add: boolean) => (from: number, to: number): void => {
    if (!editable) {
      return;
    }
    const order = editedOrder(answer, { part, from, to, add });
    if (order !== undefined) {
      ask(order);
    }
  };

  const revert = (): void => {
    seq.current += 1;
    setPending(false);
    setAnswer(page.answer);
    setStatus({ text: "提案に書いてある順序に戻しました", error: false });
  };

  const relayout = (): void => {
    setLayouts(layoutsOf(answer));
    setFit((n) => n + 1);
  };

  const reload = (): void => {
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

  const confirmSave = (): void => {
    setReview(false);
    setBusy(true);
    setStatus({ text: "提案を書き換えています…", error: false });
    post({ type: "save", order: changedOrder(answer), expect: page.answer.source_sha });
  };

  const rows = changed === undefined ? [] : planDiff(page.answer.plans, changed.plans);
  // 案内の間、図を出せない答え（計画が空）なら見本の計画を読むだけで出す。見本は描くだけで、線の頼みにも保存にも使わない
  const sample = tour.touring && answer.plans.length === 0 ? sampleWorkflow() : undefined;

  return (
    <>
      {gone !== undefined && (
        <div id="gone" className="lock" role="status">
          {gone}
        </div>
      )}
      {changed !== undefined && gone === undefined && (
        <div id="changed" className="banner warn" role="status">
          <span className="workflow-banner-text">
            {showDiff
              ? "提案が外で変わりました（エージェントが書き換えた可能性があります）。下の差分を見て、取り込むか編集を続けるかを選んでください。"
              : "提案が外で変わったため、保存できません。「取り込む」で新しい提案から始めてください（いまの編集は捨てます）。"}
          </span>
          <button type="button" className="action" data-action="adopt" disabled={busy} onClick={() => post({ type: "adopt" })}>
            {dirty ? "取り込む（編集を捨てる）" : "取り込む"}
          </button>
          {showDiff && (
            <button type="button" className="action" data-action="keep-editing" onClick={() => setShowDiff(false)}>
              編集を続ける（保存はできない）
            </button>
          )}
        </div>
      )}
      {changed !== undefined && showDiff && gone === undefined && (
        <section id="changed-diff">
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
      <header className="toolbar">
        <div className="summary">
          <span className="workflow-ticket">
            <strong>{page.ticket}</strong> のワークフロー（計画の順序）
          </span>
          <span id="dirty" className={dirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="relayout" title="今の線で点の置き場所を組み直します（番号は変わりません）" onClick={relayout}>
            並べ直す
          </button>
          <button type="button" className="action" data-action="revert" disabled={!dirty || busy || gone !== undefined} title="提案に書いてある先行と番号に戻します" onClick={revert}>
            案に戻す
          </button>
          <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
            再読込
          </button>
          <button
            type="button"
            className="action primary"
            id="save"
            data-action="save"
            disabled={busy || blocker !== ""}
            title={blocker === "" ? "提案の計画と、番号の変わる項を指す子の提案を書き換えます（承認はしません）" : blocker}
            onClick={() => setReview(true)}
          >
            この順序に決める
          </button>
        </div>
        <TourButton onClick={tour.start} />
      </header>
      {sample !== undefined ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
      <p className="dim small">
        項の右の点から別の項の左の点へドラッグすると、線の先の項が元の項を待ちます（逆向きでも引けます）。線の真ん中の × で待たなくなります。線を変えるたびに ccnavi が番号を振り直します。循環になる線と、子が承認された番号へ入る線は引けません。
      </p>
      {moved.length > 0 && (
        <p id="workflow-renumbered" className="small">
          番号が変わる項: {moved.map((item) => `${item.title} ${item.from} → ${item.number}`).join("、")}
        </p>
      )}
      {notes.length > 0 && (
        <ul className="problems" id="workflow-problems">
          {notes.map((note, index) => (
            <li key={index} data-kind={note.kind}>
              {note.text}
            </li>
          ))}
        </ul>
      )}
      <div className="workflow-graphs">
        {sample !== undefined ? (
          sample.plans.map((plan) => <PlanGraph key={plan.part} plan={plan} detail />)
        ) : answer.plans.length === 0 ? (
          <p className="empty">この親の提案には計画（plan:）がありません。</p>
        ) : (
          answer.plans.map((plan) => (
            <PlanGraph
              key={plan.part}
              plan={plan}
              detail
              editable={editable}
              onConnect={edit(plan.part, true)}
              onDisconnect={edit(plan.part, false)}
              layout={layouts[plan.part]}
              fitKey={fit}
            />
          ))
        )}
      </div>
      {review && <Review ticket={page.ticket} answer={answer} onConfirm={confirmSave} onCancel={() => setReview(false)} />}
      <footer className={status?.error === true ? "foot error" : "foot"}>
        <span id="status">{status?.text ?? ""}</span>
      </footer>
      {tour.touring ? <Tour steps={TOUR_STEPS} onClose={tour.end} /> : null}
    </>
  );
}
