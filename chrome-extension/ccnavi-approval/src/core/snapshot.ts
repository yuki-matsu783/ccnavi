/**
 * リポジトリ 1 つのボードを組む。書く流れ（`write.ts`）も同じ読み方で、
 * 親子のチケット 1 組ぶんの判定の入力を組み直す（`readFamily`）。
 *
 * 流れ:
 *
 * 1. 統合先を決める（設定か、ホストのデフォルトブランチ）。設定したブランチが無ければ止める
 * 2. 統合先の `.claude/settings.json` を読み、置き場のパスを Python に出させる
 * 3. 統合先の `done/`・共通層・自身の層・互換のマーカーを読む。互換の比べは Python
 * 4. 直近 N 日とユーザの指定のブランチ（表示用）の置き場を読み、親子のチケットを Python に見分けさせる
 * 5. 親子のチケットごとに、参照の閉包の足りないブランチを読み足し、Python に承認待ちを出させる。
 *    判定の入力は統合先・`P`・閉包の `P_X` だけ。表示用のブランチは入れない
 * 6. 依頼済みでまだレビュー済みでないフェーズがあれば、MR のスレッドとレビューを読んで、Python の
 *    `confirm` に通るかを聞く（書かない）
 *
 * blob は sha で引き、キャッシュ（IndexedDB）にあれば読まない。判定はここでは出さない。
 */
import type { PathObject, RecentRef, TreeEntry, BlobText } from "./github.js";
import { BLOB_BATCH, LINK_MODE, type ApprovalCommit } from "./github.js";
import { py, type BoardResult, type Branch, type Compat, type Family, type Placement, type PyCall, type Snapshot, type Workspace } from "./py.js";
import { reviewPanels, type ReviewPanel } from "./reviewed.js";
import type { RepoConfig } from "./settings.js";

/** service worker へ頼む関数。`owner`・`repo` はここで前に付ける */
export type HostCall = (op: string, args: readonly unknown[]) => Promise<unknown>;

export interface BlobCache {
  get(key: string): Promise<string | null | undefined>;
  put(key: string, text: string): Promise<void>;
}

export interface Stats {
  rest: number;
  graphql: number;
  blobsFetched: number;
  blobsCached: number;
}

export interface Deps {
  readonly call: HostCall;
  readonly py: PyCall;
  readonly cache: BlobCache;
  readonly now: () => Date;
  /** 呼んだ回数を数える（service worker が返す数を足す） */
  readonly stats: Stats;
  /**
   * プロジェクトのリポジトリのワークスペース。登録したワークスペースのリポジトリと、そのホストへ頼む関数。
   * プロジェクトのリポジトリを読むときに要る（無ければ止める）
   */
  readonly workspace?: { readonly repo: RepoConfig; readonly call: HostCall };
}

export interface FamilyBoard {
  readonly family: Family;
  readonly result: BoardResult | null;
  readonly error: string;
  /** レビュー済みの候補のフェーズと、通るか */
  readonly reviews?: readonly ReviewPanel[];
}

export interface RepoBoard {
  readonly repo: RepoConfig;
  readonly integration: Snapshot["integration"] | null;
  readonly compat: Compat | null;
  readonly candidates: readonly string[];
  readonly missingExtras: readonly string[];
  readonly families: readonly FamilyBoard[];
  readonly error: string;
  readonly stats: Stats;
  /** 読んだブランチ（統合先と表示用のブランチ）。「始める」が開いた親子のチケットを見分けるのに使う */
  readonly seen?: Snapshot | null;
}

/** 読んでいる間にブランチの先頭が動いた。書く流れは読み直して再試行する */
export class MovedError extends Error {}

export class Reader {
  readonly branches: Record<string, Branch> = {};
  readonly absent: string[] = [];
  /** プロジェクトのリポジトリなら、その名前とワークスペースの統合先の中身 */
  project = "";
  workspace: Workspace | undefined = undefined;

  constructor(
    private readonly repo: RepoConfig,
    private readonly deps: Deps,
  ) {}

  call<T>(op: string, ...args: unknown[]): Promise<T> {
    return this.deps.call(op, [this.repo.owner, this.repo.repo, ...args]) as Promise<T>;
  }

  /** ブランチの先頭。無ければ null で、`absent` に残す */
  async head(name: string): Promise<string | null> {
    const sha = await this.call<string | null>("branchHead", name);
    if (sha === null && !this.absent.includes(name)) {
      this.absent.push(name);
    }
    return sha;
  }

  /** ブランチの上のパス（ディレクトリかファイル）を読んで、そのブランチの中身に足す */
  async read(name: string, head: string, paths: readonly string[]): Promise<Branch> {
    const branch = this.branches[name] ?? { head, files: {}, binary: [], links: [] };
    if (branch.head !== head) {
      throw new MovedError(`${name} の先頭が読んでいる間に動いた。読み直す`);
    }
    const objects = await this.call<Record<string, PathObject | null>>("pathObjects", head, paths);
    const wanted: { path: string; sha: string }[] = [];
    const links = [...(branch.links ?? [])];
    for (const p of paths) {
      const obj = objects[p];
      if (!obj) continue;
      if (obj.type === "link") {
        // シンボリックリンク（GitLab が mode で見分けたもの）は読まない。Python が「決まらない」にする
        if (!links.includes(p)) links.push(p);
      } else if (obj.type === "blob") {
        wanted.push({ path: p, sha: obj.oid });
      } else {
        // GitLab は tree の sha でなく、コミットとパスで引く（GitHub は後ろの 2 つを使わない）
        const entries = await this.call<TreeEntry[]>("tree", obj.oid, head, p);
        for (const e of entries) {
          // シンボリックリンクは中身（指す先のパス）をファイルとして読まない。Python が「決まらない」にする
          if (e.mode === LINK_MODE) {
            if (!links.includes(`${p}/${e.path}`)) links.push(`${p}/${e.path}`);
          } else {
            wanted.push({ path: `${p}/${e.path}`, sha: e.sha });
          }
        }
      }
    }
    const texts = await this.blobs(wanted.map((w) => w.sha));
    const files = { ...branch.files };
    const binary = [...branch.binary];
    for (const w of wanted) {
      const t = texts.get(w.sha);
      if (typeof t === "string") files[w.path] = t;
      else if (!binary.includes(w.path)) binary.push(w.path);
    }
    const next = { head, files, binary, links };
    this.branches[name] = next;
    return next;
  }

  private async blobs(shas: readonly string[]): Promise<Map<string, string | null>> {
    const out = new Map<string, string | null>();
    const missing: string[] = [];
    for (const sha of new Set(shas)) {
      const hit = await this.deps.cache.get(this.cacheKey(sha));
      if (hit !== undefined) {
        out.set(sha, hit);
        this.deps.stats.blobsCached += 1;
      } else {
        missing.push(sha);
      }
    }
    for (let i = 0; i < missing.length; i += BLOB_BATCH) {
      const part = missing.slice(i, i + BLOB_BATCH);
      const got = await this.call<Record<string, BlobText>>("blobs", part);
      for (const sha of part) {
        const b = got[sha];
        const text = b && !b.binary ? b.text : null;
        out.set(sha, text);
        this.deps.stats.blobsFetched += 1;
        if (text !== null) await this.deps.cache.put(this.cacheKey(sha), text);
      }
    }
    return out;
  }

  private cacheKey(sha: string): string {
    return `${this.repo.host}/${this.repo.owner}/${this.repo.repo}/${sha}`;
  }

  snapshot(integration: Snapshot["integration"]): Snapshot {
    const base = { integration, branches: { ...this.branches }, absent: [...this.absent] };
    return this.project && this.workspace ? { ...base, project: this.project, workspace: this.workspace } : base;
  }
}

export interface IntegrationRead {
  readonly integration: Snapshot["integration"];
  readonly settings: string | null;
  readonly place: Placement;
  readonly compat: Compat;
}

/** 統合先の名前と先頭（設定か、ホストのデフォルトブランチ）。無ければ投げる */
async function integrationOf(repo: RepoConfig, reader: Reader, what: string): Promise<Snapshot["integration"]> {
  const info = await reader.call<{ defaultBranch: string }>("repoInfo");
  const name = repo.integration || info.defaultBranch;
  const source = repo.integration ? "setting" : "default";
  const head = await reader.head(name);
  if (head === null) {
    throw new Error(`${what}統合先 ${name} がリモートに無い。設定を直してください`);
  }
  return { name, source, head } as const;
}

/** 1〜3: 統合先を決め、置き場のパス・統合先の中身・互換のマーカーを読む。統合先が無ければ投げる */
export async function readIntegration(repo: RepoConfig, reader: Reader, deps: Deps): Promise<IntegrationRead> {
  if (repo.project) return await readProjectIntegration(repo, reader, deps);
  const integration = await integrationOf(repo, reader, "");
  const { name, head } = integration;
  const first = await reader.read(name, head, [".claude/settings.json"]);
  const settings = first.files[".claude/settings.json"] ?? null;
  const place: Placement = await py.placement(deps.py, settings);
  await reader.read(name, head, [...place.integration_paths, ...place.integration_files]);
  const compat = await py.compat(deps.py, reader.snapshot(integration));
  return { integration, settings, place, compat };
}

/**
 * プロジェクトのリポジトリ: 置き場のパス・共通層・互換のマーカーはワークスペースの統合先から、
 * 閉じたもの（`done/`）とプロジェクトの層はプロジェクトの統合先から読む。プロジェクトの層の計算は Python
 */
async function readProjectIntegration(repo: RepoConfig, reader: Reader, deps: Deps): Promise<IntegrationRead> {
  const ws = deps.workspace;
  if (!ws || ws.repo.project) {
    throw new Error(`プロジェクト ${repo.project} のワークスペースのリポジトリ（${repo.workspace || "未設定"}）が設定画面に登録されていない`);
  }
  const wsReader = new Reader(ws.repo, { ...deps, call: ws.call, workspace: undefined });
  const wsInteg = await integrationOf(ws.repo, wsReader, "ワークスペースの");
  const first = await wsReader.read(wsInteg.name, wsInteg.head, [".claude/settings.json"]);
  const settings = first.files[".claude/settings.json"] ?? null;
  const place: Placement = await py.placement(deps.py, settings);
  const wsBranch = await wsReader.read(wsInteg.name, wsInteg.head, [...place.workspace_paths, ...place.workspace_files]);
  reader.project = repo.project;
  reader.workspace = { integration: wsInteg, files: wsBranch.files, binary: wsBranch.binary, links: wsBranch.links ?? [] };
  const integration = await integrationOf(repo, reader, "");
  await reader.read(integration.name, integration.head, place.project_paths);
  const compat = await py.compat(deps.py, reader.snapshot(integration));
  return { integration, settings, place, compat };
}

/** リポジトリ 1 つを読んで、親子のチケットごとの承認待ちを組む。失敗は `error` に入れて返す */
export async function collectRepo(repo: RepoConfig, deps: Deps): Promise<RepoBoard> {
  const empty = { repo, integration: null, compat: null, candidates: [], missingExtras: [], families: [], stats: deps.stats };
  const reader = new Reader(repo, deps);
  let integration: Snapshot["integration"] | null = null;
  try {
    // 1〜3. 統合先、置き場のパス、統合先の中身、互換のマーカー
    const base = await readIntegration(repo, reader, deps);
    integration = base.integration;
    const { settings, place, compat } = base;
    const name = integration.name;

    // 4. 表示用のブランチ（直近 N 日とユーザの指定）
    const since = new Date(deps.now().getTime() - repo.recentDays * 24 * 3600 * 1000);
    const recent = repo.recentDays > 0 ? await reader.call<RecentRef[]>("recentRefs", since.toISOString()) : [];
    const heads = new Map<string, string>();
    for (const r of recent) heads.set(r.name, r.head);
    const missingExtras: string[] = [];
    for (const b of repo.extraBranches) {
      if (heads.has(b)) continue;
      const sha = await reader.head(b);
      if (sha === null) missingExtras.push(b);
      else heads.set(b, sha);
    }
    heads.delete(name);
    const candidates = [...heads.keys()].sort();
    for (const c of candidates) {
      await reader.read(c, heads.get(c) as string, place.branch_paths);
    }
    const families = await py.families(deps.py, settings, reader.snapshot(integration), candidates);

    // 5. 親子のチケットごとの承認待ち
    const boards: FamilyBoard[] = [];
    for (const family of families) {
      boards.push(await familyBoard(family, reader, integration, settings, place, deps));
    }
    return { repo, integration, compat, candidates, missingExtras, families: boards, error: "", stats: deps.stats, seen: reader.snapshot(integration) };
  } catch (err) {
    return { ...empty, integration, error: (err as Error).message ?? String(err) };
  }
}

/** 閉包の足りない親子のチケットを読み足し、判定の入力を統合先・P・閉包だけに絞る */
async function closureInput(
  family: string,
  reader: Reader,
  integration: Snapshot["integration"],
  settings: string | null,
  place: Placement,
  deps: Deps,
): Promise<Snapshot> {
  // 閉包の足りない親子のチケットを読み足す。無いブランチは `absent` に入り、Python が決める
  for (let round = 0; round < 32; round += 1) {
    const closure = await py.closure(deps.py, settings, reader.snapshot(integration), family);
    if (closure.over_limit || closure.need.length === 0) break;
    for (const n of closure.need) {
      const sha = await reader.head(n);
      if (sha !== null) await reader.read(n, sha, place.branch_paths);
    }
  }
  // 判定の入力は統合先・P・閉包だけ。表示用のブランチを混ぜないよう、ここで絞る
  const all = reader.snapshot(integration);
  const closure = await py.closure(deps.py, settings, all, family);
  const keep = new Set([integration.name, ...closure.families]);
  const branches: Record<string, Branch> = {};
  for (const [k, v] of Object.entries(all.branches)) if (keep.has(k)) branches[k] = v;
  return { ...all, integration, branches, absent: all.absent.filter((a) => keep.has(a)) };
}

async function familyBoard(
  family: Family,
  reader: Reader,
  integration: Snapshot["integration"],
  settings: string | null,
  place: Placement,
  deps: Deps,
): Promise<FamilyBoard> {
  try {
    const input = await closureInput(family.name, reader, integration, settings, place, deps);
    const board = await py.board(deps.py, settings, input, family.name);
    const result = await withdrawableHere(board, reader, place, family.name);
    const reviews = await reviewPanels(
      board.reviewable ?? [],
      deps.py,
      (op, args) => reader.call(op, ...args),
      { settings, snapshot: input, family: family.name },
      deps.now(),
    );
    return { family, result, error: "", reviews };
  } catch (err) {
    return { family, result: null, error: (err as Error).message ?? String(err) };
  }
}

/**
 * 取り下げを出すのは、承認コミットを引けて、その親で提案を読めるときだけにする（取り下げを出せる場合を狭める条件）。
 * Python が理由を返さなかった承認済みのチケットでも、引けなければ理由を足す。
 */
async function withdrawableHere(board: BoardResult, reader: Reader, place: Placement, family: string): Promise<BoardResult> {
  const head = reader.branches[family]?.head;
  if (!board.withdrawable || !head) return board;
  const out = [];
  for (const w of board.withdrawable) {
    if (w.problems.length > 0) {
      out.push(w);
      continue;
    }
    const prior = await findPrior((op, args) => reader.call(op, ...args), place, head, w.ticket);
    out.push(prior === null ? { ...w, problems: ["承認コミット（親のブランチの first-parent の履歴で、この承認済みチケットを足したコミット）が見つからないか、そのコミットの親に提案が無い"] } : w);
  }
  return { ...board, withdrawable: out };
}

type Ask = (op: string, args: readonly unknown[]) => Promise<unknown>;

/** 承認コミットの親にあった提案の本文。引けなければ null */
export async function findPrior(ask: Ask, place: Placement, head: string, ident: string): Promise<string | null> {
  const doing = `${place.approved}/doing/${ident}.md`;
  const todo = `${place.tickets}/todo/${ident}.md`;
  const found = (await ask("approvalCommit", [head, doing])) as ApprovalCommit | null;
  if (found === null) return null;
  const objs = (await ask("pathObjects", [found.parent, [todo]])) as Record<string, PathObject | null>;
  const obj = objs[todo];
  if (!obj || obj.type !== "blob") return null;
  const texts = (await ask("blobs", [[obj.oid]])) as Record<string, BlobText>;
  const blob = texts[obj.oid];
  return blob && !blob.binary && typeof blob.text === "string" ? blob.text : null;
}

export interface FamilyRead extends IntegrationRead {
  /** 判定の入力（統合先・P・閉包の P_X） */
  readonly input: Snapshot;
  /** 読んだときの P の先頭。書く条件（`expectedHeadOid`）にする */
  readonly head: string;
}

/**
 * 書く流れのために、親子のチケット 1 組ぶんを新しく読み直す（毎回 Snapshot を組み直す）。
 * `at` を渡すと、親のブランチをその先頭で読む（GitLab の事後確認で、自分の書き込みの直前の状態を読み直す）
 */
export async function readFamily(repo: RepoConfig, family: string, deps: Deps, at?: string): Promise<FamilyRead> {
  const reader = new Reader(repo, deps);
  const base = await readIntegration(repo, reader, deps);
  const head = at ?? (await reader.head(family));
  if (head === null) {
    throw new Error(`親のブランチ ${family} がホストに無い`);
  }
  await reader.read(family, head, base.place.branch_paths);
  const input = await closureInput(family, reader, base.integration, base.settings, base.place, deps);
  return { ...base, input, head };
}
