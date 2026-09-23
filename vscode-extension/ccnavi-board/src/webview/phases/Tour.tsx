/**
 * 吹き出しで画面の部品を順に指す案内（ツアー）。
 *
 * **出すのは画面ごとに初回だけ。** 見たかどうかは拡張ホストが `globalState` に持つ（`src/tour.ts`）。
 * 画面が組み上がったとき、見ていなければ拡張ホストが `tour` を送り、画面はここを出す。閉じたら
 * （最後まで見ても、途中でやめても）`tourDone` を返し、次からは出ない。ヘルプの「案内をもう一度見る」
 * からはいつでも出せる。
 *
 * 指す先は選択子で持つ。見つからない（種類が無くて行が無い、など）ときは、吹き出しを画面の中ほどに
 * 出して文だけを見せる。段ごとの `before` は、指す先を出すための下ごしらえ（一覧と図の切り替え、
 * 行を開く）。下ごしらえで描き直しが起きるので、測るのは次の刻みまで待つ。
 */
import { useEffect, useLayoutEffect, useRef, useState, type JSX } from "react";

export interface TourStep {
  /** 指す先。`document.querySelector` に渡す */
  readonly target: string;
  readonly title: string;
  readonly body: string;
  /** 指す先を出すための下ごしらえ */
  readonly before?: () => void;
}

interface Spot {
  readonly top: number;
  readonly left: number;
  readonly width: number;
  readonly height: number;
}

/** 吹き出しの幅。狭い画面では画面の幅に合わせる（CSS の max-width） */
const BUBBLE_WIDTH = 340;
/** 指す先と吹き出しの間 */
const GAP = 10;

export function Tour({ steps, onClose }: { readonly steps: readonly TourStep[]; readonly onClose: () => void }): JSX.Element {
  const [index, setIndex] = useState(0);
  const [spot, setSpot] = useState<Spot | undefined>(undefined);
  const [measured, setMeasured] = useState(false);
  const bubble = useRef<HTMLDivElement>(null);
  const step = steps[index];
  // 段の中身は呼ぶ側が描くたびに作り直すので、下ごしらえは段の番号が変わったときだけ走らせる
  const current = useRef(step);
  current.current = step;

  // 段が変わったら下ごしらえをして、次の刻みで指す先を測る
  useLayoutEffect(() => {
    const step = current.current;
    setMeasured(false);
    step.before?.();
    let cancelled = false;
    const measure = (): void => {
      if (cancelled) {
        return;
      }
      const el = document.querySelector(step.target);
      if (el === null) {
        setSpot(undefined);
      } else {
        el.scrollIntoView?.({ block: "nearest" });
        const rect = el.getBoundingClientRect();
        setSpot({ top: rect.top, left: rect.left, width: rect.width, height: rect.height });
      }
      setMeasured(true);
    };
    const timer = setTimeout(measure, 0);
    window.addEventListener("resize", measure);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      window.removeEventListener("resize", measure);
    };
  }, [index]);

  // 吹き出しに焦点を移す（キーボードで「次へ」を押せるように）。Esc でやめる
  useEffect(() => {
    bubble.current?.focus();
  }, [index, measured]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const last = index === steps.length - 1;
  const width = Math.min(BUBBLE_WIDTH, window.innerWidth - 24);
  let style: { top: number; left: number } | undefined;
  if (spot !== undefined) {
    const below = spot.top + spot.height + GAP;
    // 下に 160px 取れなければ上に出す
    const top = below + 160 < window.innerHeight || spot.top < 170 ? below : Math.max(8, spot.top - GAP - 150);
    const left = Math.min(Math.max(8, spot.left), Math.max(8, window.innerWidth - width - 8));
    style = { top, left };
  }

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
        tabIndex={-1}
        style={{ ...(style ?? {}), width, visibility: measured ? "visible" : "hidden" }}
      >
        <p className="tour-count">
          {index + 1} / {steps.length}
        </p>
        <h3 id="tour-title">{step.title}</h3>
        <p className="tour-body">{step.body}</p>
        <div className="tour-buttons">
          <button type="button" className="action small" data-action="tour-skip" onClick={onClose}>
            {last ? "閉じる" : "スキップ"}
          </button>
          {index > 0 && (
            <button type="button" className="action small" data-action="tour-back" onClick={() => setIndex(index - 1)}>
              戻る
            </button>
          )}
          <button type="button" className="action small primary" data-action="tour-next" onClick={() => (last ? onClose() : setIndex(index + 1))}>
            {last ? "終わる" : "次へ"}
          </button>
        </div>
      </div>
    </div>
  );
}
