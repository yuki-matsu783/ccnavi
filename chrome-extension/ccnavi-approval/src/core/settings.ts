/**
 * 設定画面で入れるもの（`chrome.storage.local` の `repos`）。PAT はここに持たない（別の鍵で
 * service worker だけが読む）。
 *
 * - 統合先の名前（D30）: リポジトリごと。空ならホストのデフォルトブランチ
 * - 直近 N 日（表示用。D2）: 既定 3 日。ここに入ったブランチは提案を見つけるのに使うだけで、
 *   判定の入力（統合先・`P`・閉包の `P_X`）は変えない
 * - 利用者が指定したブランチ（表示用。D2）
 */
import { checkBranch, checkName } from "./github.js";
import type { Host } from "./hosts.js";

export interface RepoConfig {
  readonly host: string;
  readonly owner: string;
  readonly repo: string;
  /** 空ならホストのデフォルトブランチ */
  readonly integration: string;
  readonly recentDays: number;
  readonly extraBranches: readonly string[];
}

export const DEFAULT_RECENT_DAYS = 3;
export const MAX_RECENT_DAYS = 90;

export function repoKey(r: Pick<RepoConfig, "host" | "owner" | "repo">): string {
  return `${r.host}/${r.owner}/${r.repo}`;
}

/** 入れた値を確かめて揃える。読めなければ理由を投げる */
export function normalizeRepo(raw: Record<string, unknown>, hosts: readonly Host[]): RepoConfig {
  const host = hosts.find((h) => h.id === raw.host);
  if (!host) {
    throw new Error(`ホストを選ぶ（${hosts.map((h) => h.id).join(" / ")}）`);
  }
  const owner = checkName(String(raw.owner ?? "").trim(), "owner");
  const repo = checkName(String(raw.repo ?? "").trim(), "リポジトリ名");
  const integ = String(raw.integration ?? "").trim();
  const integration = integ === "" ? "" : checkBranch(integ);
  const days = Number(raw.recentDays ?? DEFAULT_RECENT_DAYS);
  if (!Number.isInteger(days) || days < 0 || days > MAX_RECENT_DAYS) {
    throw new Error(`直近の日数は 0〜${MAX_RECENT_DAYS} の整数`);
  }
  const extras = Array.isArray(raw.extraBranches)
    ? raw.extraBranches
    : String(raw.extraBranches ?? "")
        .split(/[\s,]+/)
        .filter((s) => s !== "");
  const extraBranches = [...new Set(extras.map((b) => checkBranch(String(b))))];
  return { host: host.id, owner, repo, integration, recentDays: days, extraBranches };
}

/** 保存された並びを読む。読めない行は捨てる（画面で直させる） */
export function readRepos(value: unknown, hosts: readonly Host[]): RepoConfig[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const out: RepoConfig[] = [];
  for (const raw of value) {
    try {
      const r = normalizeRepo(raw as Record<string, unknown>, hosts);
      if (!out.some((o) => repoKey(o) === repoKey(r))) {
        out.push(r);
      }
    } catch {
      // 読めない行は落とす
    }
  }
  return out;
}
