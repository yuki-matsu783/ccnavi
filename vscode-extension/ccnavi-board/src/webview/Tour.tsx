/**
 * 吹き出しで画面の部品を順に指す案内（ツアー）。5 つの画面が共通で使う。
 *
 * **出すのは画面ごとに初回だけ。** 見たかどうかは拡張ホストが `globalState` に持つ（`src/tour.ts`）。
 * 画面が組み上がったとき、見ていなければ拡張ホストが `tour` を送り、画面はここを出す。閉じたら
 * （最後まで見ても、途中でやめても）`tourDone` を返し、次からは出ない。ツールバーの「？ 案内」
 * （フェーズ管理画面はヘルプの「案内をもう一度見る」）からはいつでも出せる。
 *
 * 指す先は選択子で持つ。見つからない（種類が無くて行が無い、など）ときは、吹き出しを画面の中ほどに
 * 出して文だけを見せる。段ごとの `before` は、指す先を出すための下ごしらえ（一覧と図の切り替え、
 * 行を開く）。下ごしらえで描き直しが起きるので、測るのは次の刻みまで待つ。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type JSX } from "react";

import { placeBubble, type Rect } from "../core/tour-place.js";

export interface TourStep {
  /** 指す先。`document.querySelector` に渡す */
  readonly target: string;
  readonly title: string;
  readonly body: string;
  /** 指す先を出すための下ごしらえ */
  readonly before?: () => void;
}

type Spot = Rect;

/** 吹き出しの幅。狭い画面では画面の幅に合わせる（CSS の max-width） */
const BUBBLE_WIDTH = 340;

/**
 * 案内を出すかどうかの持ち物。画面の `App` が 1 つ持つ。
 *
 * - `request`: 拡張ホストの `tour`（初回）を受けたときに呼ぶ。**指す先が出る（`ready`）まで待って始める。**
 *   読み込み中やエラーの画面には指す先が無い
 * - `start`: 「？ 案内」を押したとき。すぐ始める
 * - `end`: 吹き出しを閉じたとき。`onEnd` を呼ぶ（画面はそこで様子を戻し、`tourDone` を返す）
 *
 * `onStart` は始める直前に呼ぶ。案内が画面の様子（タブなど）を動かすなら、ここで控えを取る。
 */
export function useTour(ready: boolean, hooks: { readonly onStart?: () => void; readonly onEnd: () => void }): {
  readonly touring: boolean;
  readonly request: () => void;
  readonly start: () => void;
  readonly end: () => void;
} {
  const [touring, setTouring] = useState(false);
  const [pending, setPending] = useState(false);
  // 受け口は描くたびに作り直さないので、呼ぶ先はいまのものを写しておく
  const latest = useRef(hooks);
  latest.current = hooks;
  const touringRef = useRef(touring);
  touringRef.current = touring;

  const start = useCallback((): void => {
    if (touringRef.current) {
      return;
    }
    latest.current.onStart?.();
    touringRef.current = true;
    setTouring(true);
  }, []);

  useEffect(() => {
    if (pending && ready) {
      setPending(false);
      start();
    }
  }, [pending, ready, start]);

  const request = useCallback((): void => setPending(true), []);
  const end = useCallback((): void => {
    touringRef.current = false;
    setTouring(false);
    latest.current.onEnd();
  }, []);
  return { touring, request, start, end };
}

export function Tour({ steps, onClose }: { readonly steps: readonly TourStep[]; readonly onClose: () => void }): JSX.Element {
  const [index, setIndex] = useState(0);
  const [spot, setSpot] = useState<Spot | undefined>(undefined);
  const [place, setPlace] = useState<{ top: number; left: number } | undefined>(undefined);
  const [measured, setMeasured] = useState(false);
  const bubble = useRef<HTMLDivElement>(null);
  const next = useRef<HTMLButtonElement>(null);
  const step = steps[index];
  // 段の中身は呼ぶ側が描くたびに作り直すので、下ごしらえは段の番号が変わったときだけ走らせる
  const current = useRef(step);
  current.current = step;

  // 段が変わったら下ごしらえをして、次の刻みで指す先を測る。画面の大きさが変わったときと、
  // 裏の文書が動いたとき（覆いの上のホイール、帯が出てずれた）にも測り直す
  useLayoutEffect(() => {
    const step = current.current;
    setMeasured(false);
    step.before?.();
    let cancelled = false;
    let scrolled = false;
    const measure = (): void => {
      if (cancelled) {
        return;
      }
      const el = document.querySelector(step.target);
      if (el === null) {
        setSpot(undefined);
      } else {
        // 段に入って最初の 1 回だけ、指す先を見える場所へ動かす（測り直しのたびに動かすと、人のスクロールと取り合う）
        if (!scrolled) {
          scrolled = true;
          // 画面より高い要素（長い一覧）は頭を見せる。中ほどに寄せると、何を指しているのかが見えない
          const tall = el.getBoundingClientRect().height > window.innerHeight * 0.6;
          el.scrollIntoView?.({ block: tall ? "start" : "center" });
        }
        const rect = el.getBoundingClientRect();
        setSpot({ top: rect.top, left: rect.left, width: rect.width, height: rect.height });
      }
      setMeasured(true);
    };
    const timer = setTimeout(measure, 0);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [index]);

  // 吹き出しの実寸が分かってから置き場所を決める
  useLayoutEffect(() => {
    const el = bubble.current;
    if (spot === undefined || el === null) {
      setPlace(undefined);
      return;
    }
    setPlace(placeBubble(spot, { width: el.offsetWidth, height: el.offsetHeight }, { width: window.innerWidth, height: window.innerHeight }));
  }, [spot, index]);

  // 焦点は「次へ」に置く（Enter で進める）。閉じたら、案内の前に焦点があった場所へ戻す
  useEffect(() => {
    next.current?.focus();
  }, [index, measured]);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    return () => before?.focus?.();
  }, []);

  // Esc でやめる。Tab は吹き出しのボタンの中だけを巡る（裏の画面の「保存」などへ焦点を逃がさない）
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || bubble.current === null) {
        return;
      }
      const buttons = Array.from(bubble.current.querySelectorAll<HTMLButtonElement>("button"));
      if (buttons.length === 0) {
        return;
      }
      const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const to = event.shiftKey ? (at <= 0 ? buttons.length - 1 : at - 1) : at < 0 || at === buttons.length - 1 ? 0 : at + 1;
      event.preventDefault();
      buttons[to].focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const last = index === steps.length - 1;
  const width = Math.min(BUBBLE_WIDTH, window.innerWidth - 24);

  return (
    <div className="tour" data-step={index}>
      {spot !== undefined && (
        <div className="tour-spot" style={{ top: spot.top - 4, left: spot.left - 4, width: spot.width + 8, height: spot.height + 8 }} />
      )}
      {spot === undefined && <div className="tour-shade" />}
      <div
        ref={bubble}
        className={spot === undefined ? "tour-bubble center" : "tour-bubble"}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        aria-describedby="tour-body"
        tabIndex={-1}
        style={{ ...(place ?? {}), width, visibility: measured && (spot === undefined || place !== undefined) ? "visible" : "hidden" }}
      >
        <p className="tour-count">
          {index + 1} / {steps.length}
        </p>
        <h3 id="tour-title">{step.title}</h3>
        <p className="tour-body" id="tour-body">
          {step.body}
        </p>
        <div className="tour-buttons">
          {!last && (
            <button type="button" className="action small" data-action="tour-skip" onClick={onClose}>
              スキップ
            </button>
          )}
          {index > 0 && (
            <button type="button" className="action small" data-action="tour-back" onClick={() => setIndex(index - 1)}>
              戻る
            </button>
          )}
          <button ref={next} type="button" className="action small primary" data-action="tour-next" onClick={() => (last ? onClose() : setIndex(index + 1))}>
            {last ? "完了" : "次へ"}
          </button>
        </div>
      </div>
    </div>
  );
}
