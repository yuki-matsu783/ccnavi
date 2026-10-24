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
 *   diagrams 手順が引く時系列の図（`name: { actors, steps }`）。「誰が誰に何をするか」を矢印の並びで見せる。
 *            矢印は、実際に入れる値や出る結果（`value`。等幅で出す）を持てる。値を変えると結果が変わる例は、図を分けて並べる
 *
 * 手順（`steps`）は、指す先（`target`）・題・本文・その手順の画面の様子（`scene`）を持つ。画面の外（Claude Code・GitLab）の
 * 見た目を見せたい手順は、画像（`image` と `alt`）も持てる。
 *
 *   target  `data-tour="…"` の値。空なら吹き出しを画面の中ほどに出して文だけを見せる
 *   scene   その手順で画面がとる様子（開く行・開くタブ・選ぶ点など）。鍵と値は画面ごとに決める。手順ごとに全部を書き直す
 *           （前の手順の様子を引き継がない）ので、「戻る」でも同じ様子になる。値は文字列・数・真偽だけ
 *   image   本文の下に出す画像の名前。`webview/tours/images/<名前>.png` に置き、`webview/tours/images.ts` に登録する。
 *           画像は読み込みを待つ間に吹き出しの高さが変わるので、指す先は空にして中ほどに出す
 *   alt     画像の代替文（`image` があるとき必須）
 *   diagram 本文の下に出す図の名前（`diagrams` の鍵）。`image` とは同時に書かない
 *   focus   図の中で光らせる矢印の id（, で区切る）。ほかの矢印は薄く出す。書かなければ全部を同じ濃さで出す
 */

export type SceneValue = string | number | boolean;
export type Scene = Readonly<Record<string, SceneValue>>;

export interface TourStepDef {
  readonly target: string;
  readonly title: string;
  readonly body: string;
  readonly scene: Scene;
  /** 画像の名前。無ければ空 */
  readonly image: string;
  /** 画像の代替文。画像が無ければ空 */
  readonly alt: string;
  /** 図の名前（`TourDef.diagrams` の鍵）。無ければ空 */
  readonly diagram: string;
  /** 図の中で光らせる矢印の id */
  readonly focus: readonly string[];
}

/** 図の矢印 1 本。`from` と `to` が同じなら、その登場者の列に出す覚え書き（自分の中の動き） */
export interface DiagramStepDef {
  readonly id: string;
  readonly from: string;
  readonly to: string;
  readonly text: string;
  /** 矢印に添える具体的な値（ルールの書き方・実行するコマンド・答えなど）。無ければ空。複数行も書ける */
  readonly value: string;
}

/** 時系列の図。登場者を左から並べ、矢印を上から下へ時間順に並べる */
export interface DiagramDef {
  readonly actors: readonly string[];
  readonly steps: readonly DiagramStepDef[];
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
  readonly diagrams: Readonly<Record<string, DiagramDef>>;
}

/** 基本の案内の鍵。初回に自動で出る案内で、一覧の先頭に出る */
export const INTRO_ID = "intro";

/** 見本の目印。画面は見本を出している間、この文を帯で出す */
export const SAMPLE_NOTE = "案内のための見本を表示しています。実際のチケット・プロジェクト・設定ではなく、案内を閉じると消えます。";

const ID = /^[a-z][a-z0-9-]*$/;
/** 指す先（`data-tour` の値）。選択子に埋めるので、引用符や括弧を入れない */
const TARGET = /^[a-z][a-z0-9-]*$/;

const COURSE_KEYS = ["id", "title", "summary", "steps"];
const STEP_KEYS = ["target", "title", "body", "scene", "image", "alt", "diagram", "focus"];
const ROOT_KEYS = ["screen", "sample", "courses", "diagrams"];
const DIAGRAM_KEYS = ["actors", "steps"];
const DIAGRAM_STEP_KEYS = ["id", "from", "to", "text", "value"];
/** 図の登場者の数。列が狭くなりすぎない範囲 */
const MAX_ACTORS = 6;

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

/** 図の定義を読む。読めなければ、どこが悪いかを `diagrams.name.steps[1].from` の形で名指しする */
function parseDiagrams(raw: unknown): { readonly ok: true; readonly value: Record<string, DiagramDef> } | { readonly ok: false; readonly error: string } {
  if (raw === undefined) {
    return { ok: true, value: {} };
  }
  const map = object(raw);
  if (map === undefined) {
    return { ok: false, error: "diagrams がオブジェクトではない" };
  }
  const out: Record<string, DiagramDef> = {};
  for (const [name, rawDiagram] of Object.entries(map)) {
    const at = `diagrams.${name}`;
    if (!ID.test(name)) {
      return { ok: false, error: `${at}: 名前は小文字の英字で始まり、英小文字・数字・- だけにする` };
    }
    const diagram = object(rawDiagram);
    if (diagram === undefined) {
      return { ok: false, error: `${at} がオブジェクトではない` };
    }
    const unknown = unknownKey(diagram, DIAGRAM_KEYS);
    if (unknown !== undefined) {
      return { ok: false, error: `${at} に知らない鍵 ${unknown}（actors / steps だけ）` };
    }
    const actors = Array.isArray(diagram.actors) ? diagram.actors.map((actor) => text(actor)) : [];
    if (actors.length < 2 || actors.length > MAX_ACTORS || actors.some((actor) => actor === undefined) || new Set(actors).size !== actors.length) {
      return { ok: false, error: `${at}.actors は重ならない文字列を 2〜${MAX_ACTORS} 個` };
    }
    if (!Array.isArray(diagram.steps) || diagram.steps.length === 0) {
      return { ok: false, error: `${at}.steps が空` };
    }
    const names = actors as string[];
    const seen = new Set<string>();
    const steps: DiagramStepDef[] = [];
    for (const [si, rawStep] of diagram.steps.entries()) {
      const stepAt = `${at}.steps[${si}]`;
      const step = object(rawStep);
      if (step === undefined) {
        return { ok: false, error: `${stepAt} がオブジェクトではない` };
      }
      const stepUnknown = unknownKey(step, DIAGRAM_STEP_KEYS);
      if (stepUnknown !== undefined) {
        return { ok: false, error: `${stepAt} に知らない鍵 ${stepUnknown}（id / from / to / text / value だけ）` };
      }
      const id = text(step.id);
      const from = text(step.from);
      const to = step.to === undefined ? from : text(step.to);
      const label = text(step.text);
      if (id === undefined || !ID.test(id)) {
        return { ok: false, error: `${stepAt}.id は小文字の英字で始まり、英小文字・数字・- だけにする` };
      }
      if (seen.has(id)) {
        return { ok: false, error: `${stepAt}.id「${id}」が重なっている` };
      }
      seen.add(id);
      if (from === undefined || to === undefined || !names.includes(from) || !names.includes(to)) {
        return { ok: false, error: `${stepAt} の from と to は actors にある名前にする（to を書かなければ from の列の覚え書き）` };
      }
      if (label === undefined) {
        return { ok: false, error: `${stepAt}.text が必要です` };
      }
      const value = step.value === undefined ? "" : text(step.value);
      if (value === undefined) {
        return { ok: false, error: `${stepAt}.value は空でない文字列にする（書かなければ無し）` };
      }
      steps.push({ id, from, to, text: label, value });
    }
    out[name] = { actors: names, steps };
  }
  return { ok: true, value: out };
}

/** 読めなければ、どこが悪いかを `courses[2].steps[1].title` の形で名指しする */
export function parseTour(raw: unknown): TourParsed {
  const root = object(raw);
  if (root === undefined) {
    return { ok: false, error: "案内の定義がオブジェクトではない" };
  }
  const rootUnknown = unknownKey(root, ROOT_KEYS);
  if (rootUnknown !== undefined) {
    return { ok: false, error: `知らない鍵 ${rootUnknown}（screen / sample / courses / diagrams だけ）` };
  }
  const screen = text(root.screen);
  if (screen === undefined) {
    return { ok: false, error: "screen がありません" };
  }
  if (!Array.isArray(root.courses) || root.courses.length === 0) {
    return { ok: false, error: "courses が空" };
  }
  const diagrams = parseDiagrams(root.diagrams);
  if (!diagrams.ok) {
    return { ok: false, error: diagrams.error };
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
      return { ok: false, error: `${at} に知らない鍵 ${courseUnknown}` };
    }
    const id = text(course.id);
    if (id === undefined || !ID.test(id)) {
      return { ok: false, error: `${at}.id は小文字の英字で始まり、英小文字・数字・- だけにする` };
    }
    if (seen.has(id)) {
      return { ok: false, error: `${at}.id「${id}」が重なっている` };
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
        return { ok: false, error: `${stepAt} に知らない鍵 ${stepUnknown}` };
      }
      const stepTitle = text(step.title);
      const body = text(step.body);
      if (stepTitle === undefined || body === undefined) {
        return { ok: false, error: `${stepAt} の title と body が必要です` };
      }
      if (step.target !== undefined && step.target !== "" && (typeof step.target !== "string" || !TARGET.test(step.target))) {
        return { ok: false, error: `${stepAt}.target は小文字の英字で始まり、英小文字・数字・- だけにする（空なら中ほどに出す）` };
      }
      const image = step.image === undefined ? "" : text(step.image);
      if (image === undefined || (image !== "" && !ID.test(image))) {
        return { ok: false, error: `${stepAt}.image は小文字の英字で始まり、英小文字・数字・- だけにする（画像の名前）` };
      }
      const alt = step.alt === undefined ? "" : text(step.alt);
      if (alt === undefined || (image !== "" && alt === "") || (image === "" && alt !== "")) {
        return { ok: false, error: `${stepAt} は image と alt を両方書くか、両方書かない` };
      }
      if (image !== "" && step.target !== undefined && step.target !== "") {
        return { ok: false, error: `${stepAt} は image があるので target は空にする（画像の読み込みで吹き出しの高さが変わる）` };
      }
      const diagram = step.diagram === undefined ? "" : text(step.diagram);
      if (diagram === undefined || (diagram !== "" && diagrams.value[diagram] === undefined)) {
        return { ok: false, error: `${stepAt}.diagram は diagrams にある名前にする` };
      }
      if (diagram !== "" && image !== "") {
        return { ok: false, error: `${stepAt} は image と diagram を同時に書かない` };
      }
      const focusText = step.focus === undefined ? "" : text(step.focus);
      const focus = focusText === undefined ? undefined : focusText === "" ? [] : focusText.split(",").map((id) => id.trim());
      if (focus === undefined || (focus.length > 0 && diagram === "")) {
        return { ok: false, error: `${stepAt}.focus は diagram と一緒に、矢印の id を , で区切って書く` };
      }
      const known = diagram === "" ? [] : diagrams.value[diagram].steps.map((entry) => entry.id);
      const missing = focus.find((id) => !known.includes(id));
      if (missing !== undefined) {
        return { ok: false, error: `${stepAt}.focus「${missing}」は diagrams.${diagram} の矢印にない` };
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
      steps.push({ target: (step.target as string | undefined) ?? "", title: stepTitle, body, scene, image, alt, diagram, focus });
    }
    courses.push({ id, title, summary, steps });
  }
  return { ok: true, value: { screen, sample: root.sample, courses, diagrams: diagrams.value } };
}

/** 画面が読むときの形。定義が壊れていたらここで落ちる（テストが先に見つける） */
export function tourOf(raw: unknown): TourDef {
  const parsed = parseTour(raw);
  if (!parsed.ok) {
    throw new Error(`案内の定義が読み込めない: ${parsed.error}`);
  }
  return parsed.value;
}
