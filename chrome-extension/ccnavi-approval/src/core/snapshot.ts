/**
 * リポジトリ 1 つの読み取り専用ボードを組む（ADR-0093 の 8.1・8.2）。
 *
 * 流れ:
 *
 * 1. 統合先を決める（設定か、ホストのデフォルトブランチ。D30）。設定したブランチが無ければ止める
 * 2. 統合先の `.claude/settings.json` を読み、置き場の綴りを Python に出させる
 * 3. 統合先の `done/`・共通層・自身の層・互換の印を読む。互換の比べは Python（7.3）
 * 4. 直近 N 日と利用者の指定のブランチ（表示用）の置き場を読み、家族を Python に見分けさせる
 * 5. 家族ごとに、参照の閉包（3.3 の 5）の足りないブランチを読み足し、Python に承認待ちを出させる。
 *    判定の入力は統合先・`P`・閉包の `P_X` だけ（D2）。表示用のブランチは入れない
 *
 * blob は sha で引き、控え（IndexedDB）にあれば読まない（8.2）。判定はここでは出さない。
 */
import type { PathObject, RecentRef, TreeEntry, BlobText } from "./github.js";
import { BLOB_BATCH } from "./github.js";
import { py, type BoardResult, type Branch, type Compat, type Family, type Placement, type PyCall, type Snapshot } from "./py.js";
import type { RepoConfig } from "./settings.js";

/** service worker へ頼む口。`owner`・`repo` はここで前に付ける */
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
}

export interface FamilyBoard {
  readonly family: Family;
  readonly result: BoardResult | null;
  readonly error: string;
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
}

class Reader {
  readonly branches: Record<string, Branch> = {};
  readonly absent: string[] = [];

  constructor(
    private readonly repo: RepoConfig,
    private readonly deps: Deps,
  ) {}

  call<T>(op: string, ...args: unknown[]): Promise<T> {
    return this.deps.call(op, [this.repo.owner, this.repo.repo, ...args]) as Promise<T>;
  }

  /** ブランチの先頭。無ければ null で、`absent` に控える */
  async head(name: string): Promise<string | null> {
    const sha = await this.call<string | null>("branchHead", name);
    if (sha === null && !this.absent.includes(name)) {
      this.absent.push(name);
    }
    return sha;
  }

  /** ブランチの上のパス（ディレクトリかファイル）を読んで、そのブランチの中身に足す */
  async read(name: string, head: string, paths: readonly string[]): Promise<Branch> {
    const branch = this.branches[name] ?? { head, files: {}, binary: [] };
    if (branch.head !== head) {
      throw new Error(`${name} の先頭が読んでいる間に動いた。読み直す`);
    }
    const objects = await this.call<Record<string, PathObject | null>>("pathObjects", head, paths);
    const wanted: { path: string; sha: string }[] = [];
    for (const p of paths) {
      const obj = objects[p];
      if (!obj) continue;
      if (obj.type === "blob") {
        wanted.push({ path: p, sha: obj.oid });
      } else {
        const entries = await this.call<TreeEntry[]>("tree", obj.oid);
        for (const e of entries) wanted.push({ path: `${p}/${e.path}`, sha: e.sha });
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
    const next = { head, files, binary };
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
    return { integration, branches: { ...this.branches }, absent: [...this.absent] };
  }
}

/** リポジトリ 1 つを読んで、家族ごとの承認待ちを組む。失敗は `error` に入れて返す */
export async function collectRepo(repo: RepoConfig, deps: Deps): Promise<RepoBoard> {
  const empty = { repo, integration: null, compat: null, candidates: [], missingExtras: [], families: [], stats: deps.stats };
  const reader = new Reader(repo, deps);
  try {
    // 1. 統合先
    const info = await reader.call<{ defaultBranch: string }>("repoInfo");
    const name = repo.integration || info.defaultBranch;
    const source = repo.integration ? "setting" : "default";
    const head = await reader.head(name);
    if (head === null) {
      return { ...empty, error: `統合先 ${name} がリモートに無い。設定を直す` };
    }
    const integration = { name, source, head } as const;

    // 2・3. 置き場の綴り、統合先の中身、互換の印
    const first = await reader.read(name, head, [".claude/settings.json"]);
    const settings = first.files[".claude/settings.json"] ?? null;
    const place: Placement = await py.placement(deps.py, settings);
    await reader.read(name, head, [...place.integration_paths, ...place.integration_files]);
    const compat = await py.compat(deps.py, reader.snapshot(integration));

    // 4. 表示用のブランチ（直近 N 日と利用者の指定）
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

    // 5. 家族ごとの承認待ち
    const boards: FamilyBoard[] = [];
    for (const family of families) {
      boards.push(await familyBoard(family, reader, integration, settings, place, deps));
    }
    return { repo, integration, compat, candidates, missingExtras, families: boards, error: "", stats: deps.stats };
  } catch (err) {
    return { ...empty, error: (err as Error).message ?? String(err) };
  }
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
    // 閉包の足りない家族を読み足す。無いブランチは `absent` に入り、Python が決める
    for (let round = 0; round < 32; round += 1) {
      const closure = await py.closure(deps.py, settings, reader.snapshot(integration), family.name);
      if (closure.over_limit || closure.need.length === 0) break;
      for (const n of closure.need) {
        const sha = await reader.head(n);
        if (sha !== null) await reader.read(n, sha, place.branch_paths);
      }
    }
    // 判定の入力は統合先・P・閉包だけ（D2）。表示用のブランチを混ぜないよう、ここで絞る
    const all = reader.snapshot(integration);
    const closure = await py.closure(deps.py, settings, all, family.name);
    const keep = new Set([integration.name, ...closure.families]);
    const branches: Record<string, Branch> = {};
    for (const [k, v] of Object.entries(all.branches)) if (keep.has(k)) branches[k] = v;
    const input: Snapshot = { integration, branches, absent: all.absent.filter((a) => keep.has(a)) };
    const result = await py.board(deps.py, settings, input, family.name);
    return { family, result, error: "" };
  } catch (err) {
    return { family, result: null, error: (err as Error).message ?? String(err) };
  }
}
