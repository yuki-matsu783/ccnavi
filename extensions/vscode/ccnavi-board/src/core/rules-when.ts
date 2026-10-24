/**
 * ルールの組み合わせ条件（`when:`）の形と、その組み合わせ条件に対する操作。読み書き（`rules-doc.ts`）と画面（`When.tsx`）で分け合う。
 *
 * `when:` はノードを入れ子にした式で、各ノードはキーをちょうど 1 つ持つ。
 *
 *   個別条件        `{glob: '…'}` / `{regex: '…'}`
 *   演算子    `{all: [ノード, …]}` / `{any: [ノード, …]}`（1 個以上）
 *   否定      `{not: ノード}`
 *
 * 上限は深さ 8、ノード数 64（実行ファイルの `--lint` と同じ数え方: 最上位のノードの深さを 1、ノードは個別条件も数える）。
 *
 * ここは組み合わせ条件の形と編集だけを持ち、ヒットする・ヒットしないには触れない（判定は実行ファイルの仕事）。
 * 画面がこのファイルを読むので、`yaml` も node も VS Code の API も入れない。
 */

export const COND_LEAVES = ["glob", "regex"] as const;
export const COND_GROUPS = ["all", "any"] as const;
export type CondLeafOp = (typeof COND_LEAVES)[number];
export type CondGroupOp = (typeof COND_GROUPS)[number];
export type CondOp = CondLeafOp | CondGroupOp | "not";

/** 深さの上限（最上位のノードが 1） */
export const COND_MAX_DEPTH = 8;
/** ノード数の上限（個別条件も数える） */
export const COND_MAX_NODES = 64;

export type CondNode =
  | { readonly op: CondLeafOp; readonly pattern: string }
  | { readonly op: CondGroupOp; readonly children: readonly CondNode[] }
  /** `child` が null なのは、画面で中を消した途中（保存前の `--lint` と画面の注意が止める） */
  | { readonly op: "not"; readonly child: CondNode | null }
  /**
   * 組み合わせ条件として読めなかったノード。`raw` は読んだ値そのもの（JSON にできる形）で、書き戻しはこれをそのまま書く。
   * 画面は `text` と `reason` を見せ、消すか置き換えるまで注意を出す
   */
  | { readonly op: "invalid"; readonly text: string; readonly reason: string; readonly raw: unknown };

/** ノードの場所。最上位のノードは `[]`、子は親の中の順番を足していく（`not` の中は 0） */
export type CondPath = readonly number[];

export function pathKey(path: CondPath): string {
  return path.length === 0 ? "r" : `r.${path.join(".")}`;
}

export function pathOfKey(key: string): CondPath | undefined {
  if (key === "r") {
    return [];
  }
  if (!key.startsWith("r.")) {
    return undefined;
  }
  const parts = key.slice(2).split(".").map(Number);
  return parts.every((n) => Number.isInteger(n) && n >= 0) ? parts : undefined;
}

export function leaf(op: CondLeafOp, pattern = ""): CondNode {
  return { op, pattern };
}

export function group(op: CondGroupOp, children: readonly CondNode[] = []): CondNode {
  return { op, children };
}

export function not(child: CondNode | null): CondNode {
  return { op: "not", child };
}

/** 子の並び。個別条件と読めなかったノードは子を持たない。`not` の中が空なら空の並び */
export function childrenOf(node: CondNode): readonly CondNode[] {
  switch (node.op) {
    case "all":
    case "any":
      return node.children;
    case "not":
      return node.child === null ? [] : [node.child];
    default:
      return [];
  }
}

export function nodeAt(root: CondNode, path: CondPath): CondNode | undefined {
  let node: CondNode | undefined = root;
  for (const index of path) {
    if (node === undefined) {
      return undefined;
    }
    node = childrenOf(node)[index];
  }
  return node;
}

/** `path` のノードを `fn` の結果に替えた組み合わせ条件。`fn` が null を返すと親から外す（最上位のノードは外せないので元のまま） */
function updateAt(root: CondNode, path: CondPath, fn: (node: CondNode) => CondNode | null): CondNode {
  if (path.length === 0) {
    return fn(root) ?? root;
  }
  const [head, ...rest] = path;
  switch (root.op) {
    case "all":
    case "any": {
      const target = root.children[head];
      if (target === undefined) {
        return root;
      }
      const next = rest.length === 0 ? fn(target) : updateAt(target, rest, fn);
      const children = root.children.slice();
      if (next === null) {
        children.splice(head, 1);
      } else {
        children[head] = next;
      }
      return { op: root.op, children };
    }
    case "not": {
      if (head !== 0 || root.child === null) {
        return root;
      }
      const next = rest.length === 0 ? fn(root.child) : updateAt(root.child, rest, fn);
      return { op: "not", child: next };
    }
    default:
      return root;
  }
}

/** 子を足す。演算子なら末尾へ、`not` は中が空のときだけ入れる。ほかは何もしない */
export function addChild(root: CondNode, path: CondPath, child: CondNode): CondNode {
  return updateAt(root, path, (node) => {
    if (node.op === "all" || node.op === "any") {
      return { op: node.op, children: [...node.children, child] };
    }
    if (node.op === "not" && node.child === null) {
      return { op: "not", child };
    }
    return node;
  });
}

/** ノードを消す。演算子の子なら並びから外し、`not` の中なら中を空にする。最上位のノードは消せない */
export function removeAt(root: CondNode, path: CondPath): CondNode {
  if (path.length === 0) {
    return root;
  }
  return updateAt(root, path, () => null);
}

/** 種別を替える。all ⇄ any と glob ⇄ regex だけ（子と文字は持ち越す） */
export function changeOp(root: CondNode, path: CondPath, op: CondGroupOp | CondLeafOp): CondNode {
  return updateAt(root, path, (node) => {
    if ((node.op === "all" || node.op === "any") && (op === "all" || op === "any")) {
      return { op, children: node.children };
    }
    if ((node.op === "glob" || node.op === "regex") && (op === "glob" || op === "regex")) {
      return { op, pattern: node.pattern };
    }
    return node;
  });
}

export function setPattern(root: CondNode, path: CondPath, pattern: string): CondNode {
  return updateAt(root, path, (node) => (node.op === "glob" || node.op === "regex" ? { op: node.op, pattern } : node));
}

/** ノードを `not` で包む */
export function wrapNot(root: CondNode, path: CondPath): CondNode {
  return updateAt(root, path, (node) => not(node));
}

/** ノードを all / any で包む（最上位のノードが個別条件や not のときに、組み合わせへ広げる入口） */
export function wrapGroup(root: CondNode, path: CondPath, op: CondGroupOp): CondNode {
  return updateAt(root, path, (node) => group(op, [node]));
}

/** `not` を外して中身を残す。中が空の `not` は外せない（外すと何も残らない） */
export function unwrapNot(root: CondNode, path: CondPath): CondNode {
  return updateAt(root, path, (node) => (node.op === "not" && node.child !== null ? node.child : node));
}

/** ノードを別のものに置き換える（読めなかったノードを書き直すときに使う） */
export function replaceAt(root: CondNode, path: CondPath, next: CondNode): CondNode {
  return updateAt(root, path, () => next);
}

/**
 * glob / regex 1 つ（と `except:`）のルールを、同じ意味の組み合わせ条件にする。
 * 除くものがあれば `all: [本体, not: {any: 除くもの}]`、無ければ `all: [本体]`（あとから子を足せるように演算子で包む）。
 */
export function treeOfPattern(kind: CondLeafOp, pattern: string, excepts: readonly CondNode[] = []): CondNode {
  const body = leaf(kind, pattern);
  if (excepts.length === 0) {
    return group("all", [body]);
  }
  return group("all", [body, not(group("any", excepts))]);
}

/** 2 つの組み合わせ条件が同じか。書き戻しで「変えていない組み合わせ条件」に触らないために使う */
export function sameCond(a: CondNode | null, b: CondNode | null): boolean {
  if (a === null || b === null) {
    return a === b;
  }
  if (a.op !== b.op) {
    return false;
  }
  switch (a.op) {
    case "glob":
    case "regex":
      return a.pattern === (b as typeof a).pattern;
    case "all":
    case "any": {
      const other = (b as typeof a).children;
      return a.children.length === other.length && a.children.every((child, i) => sameCond(child, other[i]));
    }
    case "not":
      return sameCond(a.child, (b as typeof a).child);
    case "invalid":
      return JSON.stringify(a.raw) === JSON.stringify((b as typeof a).raw);
  }
}

/** 書式の誤り 1 件。`path` はどのノードか（組み合わせ条件ぜんたいの上限は最上位のノード） */
export interface CondProblem {
  readonly path: CondPath;
  readonly message: string;
}

/** 組み合わせ条件の深さ（最上位のノードが 1）とノード数 */
export function condSize(root: CondNode): { readonly depth: number; readonly nodes: number } {
  let nodes = 0;
  const walk = (node: CondNode): number => {
    nodes += 1;
    const depths = childrenOf(node).map(walk);
    return 1 + (depths.length === 0 ? 0 : Math.max(...depths));
  };
  const depth = walk(root);
  return { depth, nodes };
}

/**
 * 書式の誤りを挙げる。読めなかったノード、空の演算子、中が空の `not`、空の個別条件、深さとノード数の上限。
 * 止めるのは保存前の `--lint`。画面はこれを見せ、どのノードかを図で示す。
 */
export function condProblems(root: CondNode): CondProblem[] {
  const problems: CondProblem[] = [];
  const walk = (node: CondNode, path: CondPath): void => {
    switch (node.op) {
      case "invalid":
        problems.push({ path, message: `${node.reason}（${node.text}）` });
        return;
      case "glob":
      case "regex":
        if (node.pattern === "") {
          problems.push({ path, message: `${node.op} が空です` });
        }
        return;
      case "all":
      case "any":
        if (node.children.length === 0) {
          problems.push({ path, message: `${node.op} の中が空です（1 個以上）` });
        }
        node.children.forEach((child, i) => walk(child, [...path, i]));
        return;
      case "not":
        if (node.child === null) {
          problems.push({ path, message: "not の中が空です" });
          return;
        }
        walk(node.child, [...path, 0]);
        return;
    }
  };
  walk(root, []);
  const { depth, nodes } = condSize(root);
  if (depth > COND_MAX_DEPTH) {
    problems.push({ path: [], message: `深さが ${depth} あります（上限 ${COND_MAX_DEPTH}）` });
  }
  if (nodes > COND_MAX_NODES) {
    problems.push({ path: [], message: `ノードが ${nodes} 個あります（上限 ${COND_MAX_NODES}）` });
  }
  return problems;
}

function excerpt(text: string, max: number): string {
  const one = text.replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max)}…` : one;
}

/**
 * 組み合わせ条件を 1 行に縮める（畳んだ行に出す）。最上位の演算子は中身を並べ、その下の演算子は子の数だけ言う。
 * `not` は段に数えない。例: `all(regex ^{root}[\/], not(any(2)))`
 */
export function condSummary(root: CondNode): string {
  const walk = (node: CondNode, level: number): string => {
    switch (node.op) {
      case "glob":
      case "regex":
        return node.pattern === "" ? `${node.op} （空）` : `${node.op} ${excerpt(node.pattern, 24)}`;
      case "not":
        return `not(${node.child === null ? "" : walk(node.child, level)})`;
      case "all":
      case "any":
        return level >= 1 ? `${node.op}(${node.children.length})` : `${node.op}(${node.children.map((child) => walk(child, level + 1)).join(", ")})`;
      case "invalid":
        return "（読み込めない）";
    }
  };
  return walk(root, 0);
}

/** 組み合わせ条件の中の個別条件の文字（絞り込みが適用する） */
export function condPatterns(root: CondNode): string[] {
  if (root.op === "glob" || root.op === "regex") {
    return [root.pattern];
  }
  return childrenOf(root).flatMap(condPatterns);
}

// ---- 図に置く形（左から右へ伸びる図）

/** 図の点 1 つ。`empty` は中が空の `not` の穴（押すと中に入れるものを選べる） */
export interface CondPlace {
  readonly key: string;
  readonly path: CondPath;
  readonly op: CondOp | "invalid" | "empty";
  readonly label: string;
  readonly x: number;
  readonly y: number;
}

export interface CondLayout {
  readonly places: readonly CondPlace[];
  readonly edges: readonly { readonly id: string; readonly from: string; readonly to: string }[];
}

/** 段の間隔（横）と、個別条件 1 つの高さ（縦） */
export const COND_COLUMN = 210;
export const COND_ROW = 54;

function labelOf(node: CondNode): string {
  switch (node.op) {
    case "glob":
    case "regex":
      return node.pattern;
    case "all":
      return "すべてにヒットする";
    case "any":
      return "どれかにヒットする";
    case "not":
      return "ヒットしない";
    case "invalid":
      return node.text;
  }
}

/**
 * 組み合わせ条件を置く。最上位のノードを左に置き、段ごとに右へ。個別条件は上から順に 1 行ずつ、演算子は子の真ん中の高さに置く。
 * 置き場所は組み合わせ条件の形だけで決まる（ドラッグで動かした位置は持たない。組み合わせ条件を直すたびに置き直す）。
 */
export function layoutCond(root: CondNode): CondLayout {
  const places: CondPlace[] = [];
  const edges: { id: string; from: string; to: string }[] = [];
  let row = 0;
  const walk = (node: CondNode | null, path: CondPath, depth: number): number => {
    const key = pathKey(path);
    if (node === null) {
      const y = row * COND_ROW;
      row += 1;
      places.push({ key, path, op: "empty", label: "（空）", x: depth * COND_COLUMN, y });
      return y;
    }
    const kids: (CondNode | null)[] = node.op === "not" ? [node.child] : [...childrenOf(node)];
    let y: number;
    if (kids.length === 0) {
      y = row * COND_ROW;
      row += 1;
    } else {
      const ys = kids.map((child, i) => {
        const childPath = [...path, i];
        edges.push({ id: `${key}>${pathKey(childPath)}`, from: key, to: pathKey(childPath) });
        return walk(child, childPath, depth + 1);
      });
      y = (ys[0] + ys[ys.length - 1]) / 2;
    }
    places.push({ key, path, op: node.op, label: labelOf(node), x: depth * COND_COLUMN, y });
    return y;
  };
  walk(root, [], 0);
  // 最上位のノードから順に並べ直す（点の順が組み合わせ条件の順になる。テストと読み上げで追いやすい）。
  // 文字で比べると `r.10` が `r.2` より前に来るので、場所の数の並びで比べる
  places.sort((a, b) => comparePaths(a.path, b.path));
  return { places, edges };
}

/** 場所を組み合わせ条件の順（最上位のノードから、子は番号の順）に比べる */
export function comparePaths(a: CondPath, b: CondPath): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    if (a[i] !== b[i]) {
      return a[i] - b[i];
    }
  }
  return a.length - b.length;
}

/**
 * 「組み合わせ条件に変換」できない理由。`glob` と `regex` を両方書いたルールは、変換すると片方が
 * 黙って消えるので押させない。except の形は exceptBlocker が見る。押せるなら undefined
 */
export function toWhenBlocker(rule: { readonly bothPatterns?: boolean; readonly exceptNodes?: readonly CondNode[] }): string | undefined {
  if (rule.bothPatterns === true) {
    return "glob と regex を両方指定しています。ルールファイルでどちらか 1 つにしてから変換してください";
  }
  return exceptBlocker(rule.exceptNodes);
}

/**
 * 「組み合わせ条件に変換」できない理由。`except:` の各要素が glob / regex 1 つの個別条件でなければ、組み合わせ条件の中へ
 * そのまま移せない（読めない要素を組み合わせ条件に入れると、元の誤りの形が変わって見つけにくくなる）。
 * 移せるなら undefined
 */
export function exceptBlocker(excepts: readonly CondNode[] | undefined): string | undefined {
  if (excepts === undefined) {
    return undefined;
  }
  if (excepts.length === 0) {
    return "except が空のリストです。ルールファイルで直してから変換してください";
  }
  for (const [i, node] of excepts.entries()) {
    if (node.op === "invalid") {
      return `except の ${i + 1} 件目が読み込めません（${node.reason}）。ルールファイルで直してから変換してください`;
    }
    if (node.op !== "glob" && node.op !== "regex") {
      return `except の ${i + 1} 件目が glob / regex 1 つのマップではありません。ルールファイルで直してから変換してください`;
    }
    if (node.pattern === "") {
      return `except の ${i + 1} 件目の ${node.op} が空です。ルールファイルで直してから変換してください`;
    }
  }
  return undefined;
}

/** 画面が送ってきた組み合わせ条件を、形だけ確かめて受け取る。形が違えば undefined */
export function asCond(raw: unknown, depth = 0): CondNode | undefined {
  if (typeof raw !== "object" || raw === null || depth > 200) {
    return undefined;
  }
  const r = raw as Record<string, unknown>;
  switch (r.op) {
    case "glob":
    case "regex":
      return typeof r.pattern === "string" ? { op: r.op, pattern: r.pattern } : undefined;
    case "all":
    case "any": {
      if (!Array.isArray(r.children)) {
        return undefined;
      }
      const children: CondNode[] = [];
      for (const item of r.children) {
        const child = asCond(item, depth + 1);
        if (child === undefined) {
          return undefined;
        }
        children.push(child);
      }
      return { op: r.op, children };
    }
    case "not": {
      if (r.child === null) {
        return { op: "not", child: null };
      }
      const child = asCond(r.child, depth + 1);
      return child === undefined ? undefined : { op: "not", child };
    }
    case "invalid":
      return typeof r.text === "string" && typeof r.reason === "string" ? { op: "invalid", text: r.text, reason: r.reason, raw: r.raw ?? null } : undefined;
    default:
      return undefined;
  }
}
