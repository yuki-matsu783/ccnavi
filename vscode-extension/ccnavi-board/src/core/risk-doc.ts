/**
 * リスクの配点（risk.yml）の読み書き。コメントを残したまま書き戻す。
 *
 * risk.yml は先頭に使い方の説明、項目の前後に理由や例のコメントを持つ。素直に読んで
 * dump し直すとそれが全部消えるので、rules-doc と同じく `yaml` の Document を保ち、
 * 変えるところだけを差し替える。項目の入れ替えは元のノードをそのまま別の並びへ移す。
 *
 * ここは配点の意味（何点になるか）には触れない。数えるのは実行ファイル（risk.py）の仕事で、
 * 書式の検証も `--lint --risk <一時ファイル>` に聞く。画面の欄は文字のまま持ち、整数で
 * なければそのまま書いて lint に言わせる。
 */
import { isMap, isSeq, parseDocument, Scalar, YAMLMap, YAMLSeq, type Document } from "yaml";

/** 当て方。1 件につき 1 つ。ccnavi の risk.KINDS と同じ並び */
export const KINDS = ["lines_over", "files_over", "deleted_over", "glob", "script", "judge"] as const;
export type FactorKind = (typeof KINDS)[number];

/** 段階の名前は固定。閾値だけ動かす。LOW は閾値を持たない */
export const LEVEL_NAMES = ["medium", "high", "critical"] as const;
export type LevelName = (typeof LEVEL_NAMES)[number];

/** 実行ファイルが読む版（risk.VERSION） */
export const RISK_VERSION = 1;

/** 組み込みの配点（risk.builtin と同じ値）。ファイルが無いときに画面が見せ、作るときに書き出す */
export const BUILTIN_LEVELS: Readonly<Record<LevelName, number>> = { medium: 20, high: 40, critical: 70 };

export const BUILTIN_RISK_TEXT = `# 実績で測るリスクの配点。子を閉じるときに、その子の差分（base_sha..HEAD）で数える。
#
# 計画のときに「軽い」と思った作業が大きな変更になっていたら、宣言に関わらずレビューを
# 要る扱いにするためのもの。段階の名前は LOW / MEDIUM / HIGH / CRITICAL で固定。
# HIGH 以上でフェーズのゲートが閉じる。閾値は levels で動かす。
#
# 項目は 3 系統。1 件につき当て方を 1 つだけ書く。
#   定量（組み込み）: lines_over / files_over / deleted_over / glob（当たるごとに加点。max で上限）
#   定量（スクリプト）: script: <.claude/ccnavi/ か .claude/scripts/ の下>。cwd は子の作業ツリー、
#                      CCNAVI_BASE_SHA / CCNAVI_HEAD / CCNAVI_TICKET / CCNAVI_PARENT を受け取り、
#                      標準出力に整数か {"points": N, "message": "..."} を出す。失敗は重い側（points を加点）
#   定性（サブエージェント）: judge: <問い>。親がサブエージェントに判断させ、
#                      'sh .claude/scripts/ccnavi-ticket.sh judge <子> <項目> yes|no --reason <根拠>' で記録する。
#                      判定が揃うまで子は閉じられない。子の HEAD が動けば取り直し
#
# このファイルが無ければ組み込み（下の定量 4 項目と同じ値）。壊れていれば組み込みに落ち、--lint が言う。
version: 1
levels:
  medium: 20
  high: 40
  critical: 70
factors:
  - id: big-diff
    points: 25
    lines_over: 300
    message: 行数が多い
  - id: many-files
    points: 15
    files_over: 10
    message: ファイルが多い
  - id: ci
    points: 35
    glob: ".github/**"
    max: 35
    message: CI やエージェント定義に触った
  - id: deletes
    points: 20
    deleted_over: 3
    message: 消したファイルが多い
`;

/** 画面で編集する項目 1 件。`origin` は読み込んだときの位置で、新しい項目は null */
export interface FactorForm {
  readonly origin: number | null;
  readonly id: string;
  /** 加点。整数のはずだが欄の文字のまま持つ。整数でなければそのまま書いて lint が言う */
  readonly points: string;
  readonly kind: FactorKind;
  /** 当て方の値。lines_over 等なら閾値、glob ならパターン、script ならパス、judge なら問い */
  readonly value: string;
  /** glob の上限。空なら青天井（欄を書かない） */
  readonly max: string;
  readonly message: string;
}

export interface RiskForm {
  /** 閾値。空ならその段階は組み込みの値（欄を書かない） */
  readonly levels: Readonly<Record<LevelName, string>>;
  readonly factors: readonly FactorForm[];
}

export interface RiskModel {
  readonly version: number | null;
  readonly form: RiskForm;
  /** 読み込み時の苦情。形が読めなかった場所。あっても他は出す */
  readonly problems: readonly string[];
}

export interface RiskDocument {
  readonly model: RiskModel;
  /** 編集した内容で書き戻す。元のノードを使い回してコメントを残す */
  readonly apply: (form: RiskForm) => string;
}

export function readRisk(text: string): RiskDocument {
  const doc = parseDocument(text, { keepSourceTokens: false });
  const problems: string[] = [];
  for (const e of doc.errors) {
    problems.push(`YAML として読めない: ${e.message}`);
  }
  const version = doc.get("version");
  if (version === undefined || version === null) {
    problems.push(`version が無い。保存すると version: ${RISK_VERSION} を先頭に足す`);
  } else if (version !== RISK_VERSION) {
    problems.push(`version ${String(version)} は実行ファイルが読めない（読むのは ${RISK_VERSION}）。組み込みの配点に落ちる`);
  }

  const levels = { medium: "", high: "", critical: "" } as Record<LevelName, string>;
  const rawLevels = doc.get("levels", true);
  if (rawLevels !== undefined && rawLevels !== null) {
    if (!isMap(rawLevels)) {
      problems.push("levels が対応表ではない。閾値は組み込みの値として出す");
    } else {
      for (const name of LEVEL_NAMES) {
        levels[name] = scalarText(rawLevels, name);
      }
    }
  }

  const factors: FactorForm[] = [];
  const rawFactors = doc.get("factors", true);
  if (rawFactors !== undefined && rawFactors !== null) {
    if (!isSeq(rawFactors)) {
      problems.push("factors が並びではない。項目は画面に出さない");
    } else {
      rawFactors.items.forEach((item, index) => {
        if (!isMap(item)) {
          problems.push(`factors の ${index + 1} 件目が対応表ではない。画面に出さない`);
          return;
        }
        const present = KINDS.filter((k) => item.has(k));
        if (present.length > 1) {
          problems.push(
            `factors の ${index + 1} 件目に当て方が ${present.length} 個ある（${present.join(", ")}）。画面は ${present[0]} だけを出し、保存すると他は消える`,
          );
        }
        factors.push(formOf(index, item, present[0] ?? "lines_over"));
      });
    }
  }

  return {
    model: {
      version: typeof version === "number" ? version : null,
      form: { levels, factors },
      problems,
    },
    // 書き出すたびに読み直す。元のノードに値を書き込むので、同じ文書を使い回すと
    // 2 回目の `origin` が 1 回目の並び替えの後を指してしまう。
    apply: (edited) => applyTo(parseDocument(text), edited),
  };
}

function formOf(index: number, map: YAMLMap, kind: FactorKind): FactorForm {
  return {
    origin: index,
    id: scalarText(map, "id"),
    points: scalarText(map, "points"),
    kind,
    value: scalarText(map, kind),
    max: scalarText(map, "max"),
    message: scalarText(map, "message"),
  };
}

function scalarText(map: YAMLMap, key: string): string {
  const value = map.get(key);
  if (value === undefined || value === null) {
    return "";
  }
  return typeof value === "string" ? value : String(value);
}

function applyTo(doc: Document, edited: RiskForm): string {
  // 空か、最上位が対応表でないファイルは、中身を捨てて対応表から始める（読み込みの苦情で言ってある）。
  const top: YAMLMap = isMap(doc.contents) ? doc.contents : new YAMLMap();
  if (doc.contents !== top) {
    doc.contents = top;
  }

  // version。無ければ先頭に足す。違う版はそのまま（lint が言う）。
  if (!top.has("version")) {
    top.items.unshift(doc.createPair("version", RISK_VERSION));
  }

  // levels。空の欄は書かない（組み込みの値に落ちる）。
  const rawLevels: unknown = top.get("levels", true);
  let levelsNode: YAMLMap | undefined;
  if (isMap(rawLevels)) {
    levelsNode = rawLevels;
  } else if (LEVEL_NAMES.some((name) => edited.levels[name] !== "")) {
    levelsNode = new YAMLMap();
    insertAfter(doc, top, "levels", levelsNode, "version");
  }
  if (levelsNode !== undefined) {
    for (const name of LEVEL_NAMES) {
      const text = edited.levels[name];
      if (text === "") {
        if (levelsNode.has(name)) {
          levelsNode.delete(name);
        }
        continue;
      }
      setValue(doc, levelsNode, name, numberish(text), Scalar.PLAIN);
    }
  }

  // factors。元のノードを先に全部拾っておき、編集した並びへ移す。
  const existing = top.get("factors", true);
  const originals: YAMLMap[] = isSeq(existing) ? existing.items.filter(isMap) : [];
  if (isSeq(existing)) {
    adoptLeadingComment(existing, originals[0]);
  }
  const nodes = edited.factors.map((form) => {
    const original = form.origin === null ? undefined : originals[form.origin];
    const node = original ?? (doc.createNode({}) as YAMLMap);
    writeFactor(doc, node, form);
    return node;
  });
  if (isSeq(existing)) {
    // 並びの前後のコメントは並びのノードに付いているので、並びは残して中身だけ替える。
    existing.items = nodes;
    existing.flow = nodes.length === 0;
  } else if (nodes.length > 0) {
    const seq = new YAMLSeq();
    seq.items = nodes;
    top.set("factors", seq);
  }
  return doc.toString({ lineWidth: 0 });
}

/**
 * 並びの先頭の項目の前にあるコメントは、読み込みでは並びのほうに付く。
 * そのままだと先頭の項目を移したときにコメントが置き去りになるので、項目に付け直す。
 */
function adoptLeadingComment(seq: YAMLSeq, first: YAMLMap | undefined): void {
  if (first === undefined || !seq.commentBefore) {
    return;
  }
  if (!first.commentBefore) {
    first.commentBefore = seq.commentBefore;
    seq.commentBefore = null;
  }
}

/** 欄を順に書く。変わっていない欄は触らず、元の書き方（引用符）を残す */
function writeFactor(doc: Document, node: YAMLMap, form: FactorForm): void {
  setValue(doc, node, "id", form.id, Scalar.PLAIN);
  setValue(doc, node, "points", numberish(form.points), Scalar.PLAIN, "id");
  // 当て方は 1 つ。他の当て方の欄が残っていれば消す（残すと lint が「1 つを書く」と止める）。
  for (const other of KINDS) {
    if (other !== form.kind && node.has(other)) {
      node.delete(other);
    }
  }
  if (form.kind === "lines_over" || form.kind === "files_over" || form.kind === "deleted_over") {
    setValue(doc, node, form.kind, numberish(form.value), Scalar.PLAIN, "points");
  } else if (form.kind === "glob") {
    // glob は引用符で囲む。`*` で始まる値を裸で書くと YAML が別名として読む。
    setValue(doc, node, form.kind, form.value, Scalar.QUOTE_DOUBLE, "points");
  } else {
    setValue(doc, node, form.kind, form.value, Scalar.PLAIN, "points");
  }
  // max は glob の上限。空なら欄ごと消す（青天井）。
  if (form.max === "") {
    if (node.has("max")) {
      node.delete("max");
    }
  } else {
    setValue(doc, node, "max", numberish(form.max), Scalar.PLAIN, form.kind);
  }
  // message は空なら欄ごと消す（実行ファイルは id を文面に使う）。
  if (form.message === "") {
    if (node.has("message")) {
      node.delete("message");
    }
  } else {
    setValue(doc, node, "message", form.message, Scalar.PLAIN);
  }
}

/** 整数に読める文字は数として書き、それ以外は文字のまま書く（lint が「整数ではない」と言う） */
function numberish(text: string): number | string {
  const trimmed = text.trim();
  return /^-?\d+$/.test(trimmed) ? Number(trimmed) : text;
}

function setValue(
  doc: Document,
  node: YAMLMap,
  key: string,
  value: number | string,
  style: Scalar.Type,
  after?: string,
): void {
  const current = node.get(key, true);
  if (current instanceof Scalar) {
    // 書き方（引用符）は元のまま。値が変わっても書き方まで変えない。
    if (current.value !== value) {
      current.value = value;
    }
    return;
  }
  const scalar = doc.createNode(value) as Scalar;
  scalar.type = style;
  insertAfter(doc, node, key, scalar, after);
}

function insertAfter(doc: Document, node: YAMLMap, key: string, value: unknown, after?: string): void {
  const pair = doc.createPair(key, value);
  const at = after === undefined ? -1 : node.items.findIndex((p) => String((p.key as Scalar).value) === after);
  if (at >= 0) {
    node.items.splice(at + 1, 0, pair);
  } else {
    node.items.push(pair);
  }
}

/** 画面が送ってきた内容を、形だけ確かめて受け取る */
export function asRiskForm(raw: unknown): RiskForm | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }
  const r = raw as Record<string, unknown>;
  if (typeof r.levels !== "object" || r.levels === null || !Array.isArray(r.factors)) {
    return undefined;
  }
  const rawLevels = r.levels as Record<string, unknown>;
  const levels = {} as Record<LevelName, string>;
  for (const name of LEVEL_NAMES) {
    levels[name] = text(rawLevels[name]);
  }
  const factors: FactorForm[] = [];
  for (const item of r.factors) {
    const form = asFactor(item);
    if (form === undefined) {
      return undefined;
    }
    factors.push(form);
  }
  return { levels, factors };
}

function asFactor(raw: unknown): FactorForm | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }
  const r = raw as Record<string, unknown>;
  let origin: number | null = null;
  if (r.origin !== null && r.origin !== undefined) {
    if (typeof r.origin !== "number" || !Number.isInteger(r.origin) || r.origin < 0) {
      return undefined;
    }
    origin = r.origin;
  }
  if (typeof r.kind !== "string" || !(KINDS as readonly string[]).includes(r.kind)) {
    return undefined;
  }
  return {
    origin,
    id: text(r.id),
    points: text(r.points),
    kind: r.kind as FactorKind,
    value: text(r.value),
    max: text(r.max),
    message: text(r.message),
  };
}

/** 欄は文字で持つ。数で来ても文字にする（JSON を経ても同じ形にするため） */
function text(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return typeof value === "number" ? String(value) : "";
}
