/**
 * 設定画面で入れるもの（`chrome.storage.local` の `repos`）。PAT はここに持たない（別の鍵で
 * service worker だけが読む）。
 *
 * - 統合先の名前（D30）: リポジトリごと。空ならホストのデフォルトブランチ
 * - 直近 N 日（表示用。D2）: 既定 3 日。ここに入ったブランチは提案を見つけるのに使うだけで、
 *   判定の入力（統合先・`P`・閉包の `P_X`）は変えない
 * - 利用者が指定したブランチ（表示用。D2）
 * - プロジェクト名（段階 5。3.3 の 7・10.3 の 1）: このリポジトリが手元で `projects/<名前>` に clone される
 *   プロジェクトなら、その名前。空ならワークスペース自身。プロジェクトのリポジトリは、判定に要るワークスペースの
 *   統合先（共通層・設定・互換の印）を読むために、登録したワークスペースのリポジトリ（`workspace`）を名指しする
 */
import { checkBranch, checkName } from "./github.js";
import { checkNamespace } from "./gitlab.js";
import type { Host } from "./hosts.js";

export interface RepoConfig {
  readonly host: string;
  readonly owner: string;
  readonly repo: string;
  /** 空ならホストのデフォルトブランチ */
  readonly integration: string;
  readonly recentDays: number;
  readonly extraBranches: readonly string[];
  /** プロジェクト名（`projects/<名前>` の名前）。空ならワークスペース自身（段階 5） */
  readonly project: string;
  /** プロジェクトのリポジトリのワークスペース（`repoKey` の形）。ワークスペース自身なら空 */
  readonly workspace: string;
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
    throw new Error(`ホストを選んでください（${hosts.map((h) => h.id).join(" / ")}）`);
  }
  // GitLab の owner は入れ子のグループ（`group/sub`）もある
  const owner = host.kind === "gitlab" ? checkNamespace(String(raw.owner ?? "").trim()) : checkName(String(raw.owner ?? "").trim(), "owner");
  const repo = checkName(String(raw.repo ?? "").trim(), "リポジトリ名");
  const integ = String(raw.integration ?? "").trim();
  const integration = integ === "" ? "" : checkBranch(integ);
  const days = Number(raw.recentDays ?? DEFAULT_RECENT_DAYS);
  if (!Number.isInteger(days) || days < 0 || days > MAX_RECENT_DAYS) {
    throw new Error(`直近の日数は 0〜${MAX_RECENT_DAYS} の整数で入れてください`);
  }
  const extras = Array.isArray(raw.extraBranches)
    ? raw.extraBranches
    : String(raw.extraBranches ?? "")
        .split(/[\s,]+/)
        .filter((s) => s !== "");
  const extraBranches = [...new Set(extras.map((b) => checkBranch(String(b))))];
  const project = String(raw.project ?? "").trim();
  if (project !== "" && (!PROJECT.test(project) || RESERVED_LAYER.has(project.toLowerCase()))) {
    throw new Error("プロジェクト名は projects/ の下の名前（英数字と . _ -。common・self は使えない）");
  }
  const workspace = project === "" ? "" : String(raw.workspace ?? "").trim();
  if (project !== "" && workspace === "") {
    throw new Error("プロジェクトのリポジトリには、ワークスペースのリポジトリ（<ホスト>/<owner>/<リポジトリ>）を選んでください");
  }
  return { host: host.id, owner, repo, integration, recentDays: days, extraBranches, project, workspace };
}

/** プロジェクト名の形（識別子と同じ。`ticket._ID`）と、層の名札に予約した名前（`settings.is_reserved_layer_name`） */
const PROJECT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const RESERVED_LAYER = new Set(["common", "self"]);

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
