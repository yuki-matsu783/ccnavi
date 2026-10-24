/**
 * 案内（ツアー）の定義の形。画面ごとの YAML（`src/webview/tours/<画面>.yml`）を読んだ値を確かめて、画面が使う形にする。
 *
 * YAML は esbuild のプラグインが JSON にしてバンドルに入れる（`scripts/bundle-webview.js`）ので、実行時に YAML を解析しない（案内のために解析器を足さない。ボードとフローのバンドルには、フローの読み書き（`flow-doc.ts`）の都合で別に入っている）。
 * ここは形の検査だけで、DOM にも VS Code の API にも触れない。テストは YAML を直に読んで同じ検査を通す。
 *
 * 1 本の YAML に入るもの:
 *
 *   screen   画面の名前（ファイル名と同じ）
 *   sample   案内の間だけ画面に出す見本。形は画面ごとに決める
 *   courses  案内の一覧。先頭は基本の案内（`intro`。初回に自動で出る）、あとは使い方ごとの案内（ユースケース）
 *
 * 手順（`steps`）は、指す先（`target`）・題・本文・その手順の画面の様子（`scene`）を持つ。
 *
 *   target  `data-tour="…"` の値。空なら吹き出しを画面の中ほどに出して文だけを見せる
 *   scene   その手順で画面がとる様子（開く行・開くタブ・選ぶ点など）。鍵と値は画面ごとに決める。手順ごとに全部を書き直す
 *           （前の手順の様子を引き継がない）ので、「戻る」でも同じ様子になる。値は文字列・数・真偽だけ
 */

export type SceneValue = string | number | boolean;
export type Scene = Readonly<Record<string, SceneValue>>;

export interface TourStepDef {
  readonly target: string;
  readonly title: string;
  readonly body: string;
  readonly scene: Scene;
}

export interface TourCourseDef {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly steps: readonly TourStepDef[];
}

export interface TourDef {
  readonly screen: string;
  readonly sample: unknown;
  readonly courses: readonly TourCourseDef[];
}

/** 基本の案内の鍵。初回に自動で出る案内で、一覧の先頭に出る */
export const INTRO_ID = "intro";

/** 見本の目印。画面は見本を出している間、この文を帯で出す */
export const SAMPLE_NOTE = "案内のための見本を表示しています。実際のチケット・プロジェクト・設定ではなく、案内を閉じると消えます。";

const ID = /^[a-z][a-z0-9-]*$/;
/** 指す先（`data-tour` の値）。選択子に埋めるので、引用符や括弧を入れない */
const TARGET = /^[a-z][a-z0-9-]*$/;

const COURSE_KEYS = ["id", "title", "summary", "steps"];
const STEP_KEYS = ["target", "title", "body", "scene"];
const ROOT_KEYS = ["screen", "sample", "courses"];

/** 知らない鍵（綴り違い）を名指しする。黙って捨てると、空の指す先や空の様子に化ける */
function unknownKey(map: Record<string, unknown>, known: readonly string[]): string | undefined {
  return Object.keys(map).find((key) => !known.includes(key));
}

export type TourParsed = { readonly ok: true; readonly value: TourDef } | { readonly ok: false; readonly error: string };

function object(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

/** 読めなければ、どこが悪いかを `courses[2].steps[1].title` の形で名指しする */
export function parseTour(raw: unknown): TourParsed {
  const root = object(raw);
  if (root === undefined) {
    return { ok: false, error: "案内の定義がオブジェクトではない" };
  }
  const rootUnknown = unknownKey(root, ROOT_KEYS);
  if (rootUnknown !== undefined) {
    return { ok: false, error: `未知の鍵 ${rootUnknown}（screen / sample / courses だけ）` };
  }
  const screen = text(root.screen);
  if (screen === undefined) {
    return { ok: false, error: "screen がありません" };
  }
  if (!Array.isArray(root.courses) || root.courses.length === 0) {
    return { ok: false, error: "courses が空" };
  }
  const courses: TourCourseDef[] = [];
  const seen = new Set<string>();
  for (const [ci, rawCourse] of root.courses.entries()) {
    const at = `courses[${ci}]`;
    const course = object(rawCourse);
    if (course === undefined) {
      return { ok: false, error: `${at} がオブジェクトではない` };
    }
    const courseUnknown = unknownKey(course, COURSE_KEYS);
    if (courseUnknown !== undefined) {
      return { ok: false, error: `${at} に未知の鍵 ${courseUnknown}` };
    }
    const id = text(course.id);
    if (id === undefined || !ID.test(id)) {
      return { ok: false, error: `${at}.id は小文字の英字で始まり、英小文字・数字・- だけにする` };
    }
    if (seen.has(id)) {
      return { ok: false, error: `${at}.id「${id}」が重複している` };
    }
    seen.add(id);
    if (ci === 0 && id !== INTRO_ID) {
      return { ok: false, error: `${at}.id は先頭の案内として「${INTRO_ID}」にする` };
    }
    const title = text(course.title);
    const summary = text(course.summary);
    if (title === undefined || summary === undefined) {
      return { ok: false, error: `${at} の title と summary が必要です` };
    }
    if (!Array.isArray(course.steps) || course.steps.length === 0) {
      return { ok: false, error: `${at}.steps が空` };
    }
    const steps: TourStepDef[] = [];
    for (const [si, rawStep] of course.steps.entries()) {
      const stepAt = `${at}.steps[${si}]`;
      const step = object(rawStep);
      if (step === undefined) {
        return { ok: false, error: `${stepAt} がオブジェクトではない` };
      }
      const stepUnknown = unknownKey(step, STEP_KEYS);
      if (stepUnknown !== undefined) {
        return { ok: false, error: `${stepAt} に未知の鍵 ${stepUnknown}` };
      }
      const stepTitle = text(step.title);
      const body = text(step.body);
      if (stepTitle === undefined || body === undefined) {
        return { ok: false, error: `${stepAt} の title と body が必要です` };
      }
      if (step.target !== undefined && step.target !== "" && (typeof step.target !== "string" || !TARGET.test(step.target))) {
        return { ok: false, error: `${stepAt}.target は小文字の英字で始まり、英小文字・数字・- だけにする（空なら中ほどに出す）` };
      }
      const scene: Record<string, SceneValue> = {};
      if (step.scene !== undefined) {
        const given = object(step.scene);
        if (given === undefined) {
          return { ok: false, error: `${stepAt}.scene はオブジェクトにする` };
        }
        for (const [key, value] of Object.entries(given)) {
          if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") {
            return { ok: false, error: `${stepAt}.scene.${key} は文字列・数・真偽のどれかにする` };
          }
          scene[key] = value;
        }
      }
      steps.push({ target: (step.target as string | undefined) ?? "", title: stepTitle, body, scene });
    }
    courses.push({ id, title, summary, steps });
  }
  return { ok: true, value: { screen, sample: root.sample, courses } };
}

/** 画面が読むときの形。定義が壊れていたらここで落ちる（テストが先に見つける） */
export function tourOf(raw: unknown): TourDef {
  const parsed = parseTour(raw);
  if (!parsed.ok) {
    throw new Error(`案内の定義が読み込めない: ${parsed.error}`);
  }
  return parsed.value;
}
