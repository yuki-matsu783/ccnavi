/**
 * 吹き出しで画面の部品を順に指す案内（ツアー）。案内のある画面（プロジェクト管理画面のほか全部）が共通で使う。
 *
 * 出すのは画面ごとに初回だけ。 見たかどうかは拡張ホストが `globalState` に持つ（`src/tour.ts`）。
 * 画面が組み上がったとき、見ていなければ拡張ホストが `tour` を送り、画面はここを出す。閉じたら
 * （最後まで見ても、途中でやめても）`tourDone` を返し、次からは出ない。ヘッダ右上の「?」（`TourButton`）
 * からはいつでも出せる。
 *
 * 指す先は選択子で持つ。見つからない（定義が無くて行が無い、など）ときは、吹き出しを画面の中ほどに
 * 出して文だけを見せる。手順ごとの `before` は、指す先を出すための下ごしらえ（一覧と図の切り替え、
 * 行を開く）。下ごしらえで描き直しが起きるので、測るのは次の刻みまで待つ。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type JSX, type ReactNode } from "react";

import { ESCAPE_LAYER, useEscape } from "./useEscape.js";
import { INTRO_ID, type Scene, type TourDef } from "../core/tour-def.js";
import { placeBubble, type Rect } from "../core/tour-place.js";

export interface TourStep {
  /** 指す先。`document.querySelector` に渡す */
  readonly target: string;
  readonly title: string;
  readonly body: string;
  /** 本文の下に出す画像（画面の外の見た目）。指す先が空の手順だけが持つ（読み込みで吹き出しの高さが変わるため） */
  readonly image?: { readonly src: string; readonly alt: string };
  /** 指す先を出すための下ごしらえ */
  readonly before?: () => void;
}

/**
 * 案内 1 本。画面ごとに「基本の案内」（`INTRO_COURSE`。初回に自動で出る）と、使い方ごとの案内（ユースケース）を持つ。
 * 「?」を押すと一覧（`TourButton` のメニュー）が出て、選んだ 1 本だけが始まる。
 *
 * 案内の間、画面は今の設定ではなく案内用の見本（`core/tour-*.ts`）を描く。見本は画面の `useMemo` などで作り、
 * 手順の `before` が見本の上の様子（行を開く、点を選ぶ、タブを替える）を動かす。今の設定の編集中の内容には触れない。
 */
export interface TourCourse {
  /** 案内の鍵。画面の中で一意。テストと `data-course` に使う */
  readonly id: string;
  /** メニューに出す題 */
  readonly title: string;
  /** メニューに出す 1 行の説明（何ができるようになる案内か） */
  readonly summary: string;
  readonly steps: readonly TourStep[];
}

/** 基本の案内の鍵。初回に自動で出る案内で、メニューの先頭に出る */
export const INTRO_COURSE = INTRO_ID;

/** 指す先の印の選択子。`target` が空の手順は、どこにも一致しない選択子にして吹き出しを中ほどに出す */
export function targetSelector(target: string): string {
  return target === "" ? "[data-tour-none]" : `[data-tour="${target}"]`;
}

/**
 * 定義（YAML）から案内の一覧を作る。手順の下ごしらえは、その手順の `scene` を画面の `apply` に渡すこと。
 * 画面は `apply` で「見本の上の様子」（開く行・タブ・選ぶ点）を state に入れ、見本を描く。
 * `images` は手順の `image`（名前）から画像の URL を引く表（`tours/images.ts`）。表に無い名前の手順は画像なしで出る（テストが見つける）
 */
export function coursesOf(def: TourDef, apply: (scene: Scene) => void, images: Readonly<Record<string, string>> = {}): readonly TourCourse[] {
  return def.courses.map((course) => ({
    id: course.id,
    title: course.title,
    summary: course.summary,
    steps: course.steps.map((step) => ({
      target: targetSelector(step.target),
      title: step.title,
      body: step.body,
      image: step.image !== "" && images[step.image] !== undefined ? { src: images[step.image], alt: step.alt } : undefined,
      before: () => apply(step.scene),
    })),
  }));
}

/** 鍵で案内を引く。無ければ先頭（基本の案内） */
export function courseOf(courses: readonly TourCourse[], id: string | undefined): TourCourse {
  return courses.find((course) => course.id === id) ?? courses[0];
}

type Spot = Rect;

/**
 * 指す先の枠。`display: contents` の要素（設定 3 画面の `.field` など。共通の list.css）は自分の箱を持たず、
 * `getBoundingClientRect` が 0 を返すので、光る枠が出ない。その場合は子の枠を合わせたものを返す。
 * 子が 1 つも箱を持たないとき（畳まれて見えないなど）は 0 のまま返す
 */
export function boxOf(el: Element): Rect {
  const rect = el.getBoundingClientRect();
  if (typeof getComputedStyle !== "function" || getComputedStyle(el).display !== "contents") {
    return { top: rect.top, left: rect.left, width: rect.width, height: rect.height };
  }
  let top = Infinity;
  let left = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  for (const child of Array.from(el.children)) {
    const box = boxOf(child);
    if (box.width === 0 && box.height === 0) {
      continue;
    }
    top = Math.min(top, box.top);
    left = Math.min(left, box.left);
    right = Math.max(right, box.left + box.width);
    bottom = Math.max(bottom, box.top + box.height);
  }
  return top === Infinity ? { top: rect.top, left: rect.left, width: 0, height: 0 } : { top, left, width: right - left, height: bottom - top };
}

/** 吹き出しの幅。狭い画面では画面の幅に合わせる（CSS の max-width） */
const BUBBLE_WIDTH = 340;
/** 画像のある手順の吹き出しの幅。画像の中の字が読める広さにする */
const IMAGE_BUBBLE_WIDTH = 480;

/**
 * ツールバー（`<header className="toolbar">`）。左に画面ごとの中身（`children`）、右端に「?」（`TourButton`）を置く。
 * 「?」がツールバーの最後の子であることは変えない
 */
export function ScreenHeader({ courses, onPick, children }: { readonly courses: readonly TourCourse[]; readonly onPick: (course: string) => void; readonly children: ReactNode }): JSX.Element {
  return (
    <header className="toolbar">
      {children}
      <TourButton courses={courses} onPick={onPick} />
    </header>
  );
}

/**
 * 案内を選び直す「?」。案内のある画面は、どれも `ScreenHeader` の最後の子として置く。`Tour.css` がヘッダの右上の同じ位置に
 * 小さく固定する（画面ごとにボタンの列へ混ぜると、置き場所がばらつき、ボードでは操作の邪魔になった）。
 *
 * 押すと案内の一覧（基本の案内と、使い方ごとの案内）が下に開き、選んだ 1 本が始まる。案内が 1 本だけの画面は、一覧を挟まずに始める。
 * 一覧はボタンの前の兄弟に置く（ボタンがツールバーの最後の子であることは変えない）。外を押す・Esc で閉じる
 */
export function TourButton({ courses, onPick }: { readonly courses: readonly TourCourse[]; readonly onPick: (course: string) => void }): JSX.Element {
  const [open, setOpen] = useState(false);
  const holder = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const many = courses.length > 1;

  useEffect(() => {
    if (!open) {
      return;
    }
    const onDown = (event: Event): void => {
      const target = event.target as HTMLElement | null;
      if (target === null || (target.closest(".tour-menu") === null && target.closest(".tour-button") === null)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  // Esc は一覧を閉じて「?」へ焦点を戻す
  useEscape(open, ESCAPE_LAYER.tourMenu, () => {
    setOpen(false);
    button.current?.focus();
  });

  // 開いたら先頭の案内へ焦点を移す（キーボードで選べる）
  useEffect(() => {
    if (open) {
      holder.current?.querySelector<HTMLButtonElement>("button")?.focus();
    }
  }, [open]);

  const click = (): void => {
    if (!many) {
      onPick(courses[0]?.id ?? INTRO_COURSE);
      return;
    }
    setOpen((now) => !now);
  };

  return (
    <>
      {open && (
        <div
          ref={holder}
          className="tour-menu"
          role="group"
          aria-label="案内の一覧"
          // 一覧と「?」の外へ焦点が出たら閉じる（Tab で抜けたとき開いたまま残さない）
          onBlur={(event) => {
            const next = event.relatedTarget as Node | null;
            if (next !== null && !event.currentTarget.contains(next) && next !== button.current) {
              setOpen(false);
            }
          }}
        >
          <p className="tour-menu-head">見たい案内を選んでください</p>
          {courses.map((course) => (
            <button
              key={course.id}
              type="button"
              className="tour-menu-item"
              data-action="tour-course"
              data-course={course.id}
              onClick={() => {
                // 案内を閉じたとき焦点を戻す先は、始める時点で焦点のある要素。消えるメニューの項目ではなく「?」にしておく
                button.current?.focus();
                setOpen(false);
                onPick(course.id);
              }}
            >
              <span className="tour-menu-title">{course.title}</span>
              <span className="tour-menu-summary">{course.summary}</span>
            </button>
          ))}
        </div>
      )}
      <button
        ref={button}
        type="button"
        className="tour-button"
        data-action="tour"
        data-tour="tour"
        title={many ? "案内を選ぶ" : "この画面の案内を見る"}
        aria-label={many ? "案内を選ぶ" : "この画面の案内を見る"}
        aria-haspopup={many ? "true" : undefined}
        aria-expanded={many ? open : undefined}
        onClick={click}
      >
        ?
      </button>
    </>
  );
}

/**
 * 案内を出すかどうかの持ち物。画面の `App` が 1 つ持つ。
 *
 * - `request`: 拡張ホストの `tour`（初回）を受けたときに呼ぶ。基本の案内（`INTRO_COURSE`）を、指す先が出る（`ready`）まで待って始める。
 *   読み込み中やエラーの画面には指す先が無い
 * - `start`: 「?」のメニューで案内を選んだとき。指す先が出ていればすぐ始め、出ていなければ（ボードの承認のオーバーレイの
 *   最中など）`request` と同じく出るまで待つ。押した経路だけ待たないと、オーバーレイの上に案内が被さる
 * - `end`: 吹き出しを閉じたとき。`onEnd` を呼ぶ（画面はそこで様子を戻し、`tourDone` を返す）
 * - `course`: いま出している案内の鍵（出していなければ undefined）。画面は鍵で見本の様子を替える
 *
 * `onStart` は始める直前に呼ぶ（案内の鍵つき）。案内が画面の様子（タブなど）を動かすなら、ここで今の様子を覚えておく。
 * `tourDone` は基本の案内に限らず、どの案内を閉じても返す（初回の自動の案内を出さない印は、どの案内を見ても付く）。
 */
export function useTour(ready: boolean, hooks: { readonly onStart?: (course: string) => void; readonly onEnd: () => void }): {
  readonly touring: boolean;
  readonly course: string | undefined;
  readonly request: () => void;
  readonly start: (course?: string) => void;
  readonly end: () => void;
} {
  const [course, setCourse] = useState<string | undefined>(undefined);
  const [pending, setPending] = useState<string | undefined>(undefined);
  // 受け取る側は描くたびに作り直さないので、呼ぶ先はいまのものを ref へ入れておく
  const latest = useRef(hooks);
  latest.current = hooks;
  const courseRef = useRef(course);
  courseRef.current = course;

  const readyRef = useRef(ready);
  readyRef.current = ready;

  const begin = useCallback((id: string): void => {
    if (courseRef.current !== undefined) {
      return;
    }
    latest.current.onStart?.(id);
    courseRef.current = id;
    setCourse(id);
  }, []);

  useEffect(() => {
    if (pending !== undefined && ready) {
      setPending(undefined);
      begin(pending);
    }
  }, [pending, ready, begin]);

  // 案内の最中に指す先が消えた（読み直せずエラーになった、別の対象へ切り替わって読み込み中になった、
  // 承認のオーバーレイが出た）。吹き出しは描かれなくなるので、ここで閉じたことにする。閉じずに残すと、
  // 中身が戻ったときにユーザが始めていない案内が 1 番目の手順から出直す
  useEffect(() => {
    if (course !== undefined && !ready) {
      courseRef.current = undefined;
      setCourse(undefined);
      latest.current.onEnd();
    }
  }, [course, ready]);

  const request = useCallback((): void => setPending(INTRO_COURSE), []);
  const start = useCallback(
    (id: string = INTRO_COURSE): void => {
      if (readyRef.current) {
        begin(id);
      } else {
        setPending(id);
      }
    },
    [begin],
  );
  const end = useCallback((): void => {
    courseRef.current = undefined;
    setCourse(undefined);
    latest.current.onEnd();
  }, []);
  return { touring: course !== undefined, course, request, start, end };
}

export function Tour({ steps, onClose }: { readonly steps: readonly TourStep[]; readonly onClose: () => void }): JSX.Element {
  const [index, setIndex] = useState(0);
  const [spot, setSpot] = useState<Spot | undefined>(undefined);
  const [place, setPlace] = useState<{ top: number; left: number } | undefined>(undefined);
  const [measured, setMeasured] = useState(false);
  const bubble = useRef<HTMLDivElement>(null);
  const next = useRef<HTMLButtonElement>(null);
  const step = steps[index];
  // 手順の中身は呼ぶ側が描くたびに作り直すので、下ごしらえは手順の番号が変わったときだけ走らせる
  const current = useRef(step);
  current.current = step;

  // 手順が変わったら下ごしらえをして、次の刻みで指す先を測る。画面の大きさが変わったときと、
  // 背後の文書が動いたとき（覆いの上のホイール、帯が出てずれた）にも測り直す
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
      let el: Element | null = null;
      try {
        el = document.querySelector(step.target);
      } catch {
        // 選択子として読めない指す先（定義の誤り）は無いものとして、文だけを中ほどに出す
      }
      if (el === null) {
        setSpot(undefined);
      } else {
        // 手順に入って最初の 1 回だけ、指す先を見える場所へ動かす（測り直しのたびに動かすと、ユーザのスクロールを邪魔する）
        if (!scrolled) {
          scrolled = true;
          // 画面より高い要素（長い一覧）は頭を見せる。中ほどに置くと、何を指しているのかが見えない
          const tall = boxOf(el).height > window.innerHeight * 0.6;
          // 箱を持たない要素（display: contents）は scrollIntoView が何もしないので、箱を持つ最初の子孫を動かす
          const scroller = getComputedStyle(el).display === "contents" ? Array.from(el.querySelectorAll("*")).find((child) => getComputedStyle(child).display !== "contents") ?? el : el;
          scroller.scrollIntoView?.({ block: tall ? "start" : "center" });
        }
        setSpot(boxOf(el));
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

  // 焦点は「次へ」に置く（Enter で進める）。閉じたら、案内の前に焦点があった場所へ戻す。
  // 戻す先は最初に描くときに覚える。 effect で覚えると、先に走る「次へ」への移動のあとを読んでしまい、
  // 閉じたときに消えた「次へ」へ戻そうとして焦点が body に移ってしまう
  const [focusBefore] = useState(() => document.activeElement as HTMLElement | null);
  useEffect(() => {
    next.current?.focus();
  }, [index, measured]);
  useEffect(() => () => focusBefore?.focus?.(), [focusBefore]);

  // Esc でやめる。→ で次へ、← で前へ（端の手順ではどちらも何もしない。→ で最後の手順を閉じると、読み終える前に消えてしまう）。
  // Tab は吹き出しのボタンの中だけを巡る（覆いの背後の画面の「保存」などへ焦点を移さない）
  const count = steps.length;
  useEscape(true, ESCAPE_LAYER.tour, onClose);
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
        // 既定の動き（覆いの背後の画面の横スクロール）を止める
        event.preventDefault();
        event.stopPropagation();
        const step = event.key === "ArrowRight" ? 1 : -1;
        setIndex((at) => Math.min(count - 1, Math.max(0, at + step)));
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
  }, [count]);

  const last = index === steps.length - 1;
  const width = Math.min(step.image === undefined ? BUBBLE_WIDTH : IMAGE_BUBBLE_WIDTH, window.innerWidth - 24);

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
        <button type="button" className="tour-close" data-action="tour-skip" title="案内を閉じる" aria-label="案内を閉じる" onClick={onClose}>
          ×
        </button>
        <p className="tour-count">
          {index + 1} / {steps.length}
        </p>
        <h3 id="tour-title">{step.title}</h3>
        <p className="tour-body" id="tour-body">
          {step.body}
        </p>
        {step.image !== undefined && <img className="tour-image" src={step.image.src} alt={step.image.alt} />}
        <div className="tour-buttons">
          {index > 0 && (
            <button type="button" className="action small" data-action="tour-back" onClick={() => setIndex(index - 1)}>
              ← 前へ
            </button>
          )}
          <button ref={next} type="button" className="action small primary" data-action="tour-next" onClick={() => (last ? onClose() : setIndex(index + 1))}>
            {last ? "完了" : "次へ →"}
          </button>
        </div>
      </div>
    </div>
  );
}
