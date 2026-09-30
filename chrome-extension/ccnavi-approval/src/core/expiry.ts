/**
 * PAT の期限の知らせ（ADR-0093 の 5.5・D25）。
 *
 * 期限の正はホストの PAT の期限。GitHub は応答ヘッダ `github-authentication-token-expiration` で
 * 返すので、service worker がホストを呼ぶたびに読んで控える。読めなければ、登録のときに利用者が
 * 入れた日付を使う。どちらも無ければ「期限不明」と出し続ける。
 * 切れる 7 日前から、service worker が 1 日 1 回比べてバッジに出し、ボードは帯で出す。
 */

/** 作成画面で選ぶ期限の既定（日） */
export const DEFAULT_DAYS = 90;
/** 何日前から知らせるか */
export const WARN_DAYS = 7;

export interface TokenMeta {
  /** ホストの応答から読んだ期限（ISO）。読めなければ空 */
  readonly host?: string;
  /** 登録のときに利用者が入れた期限（YYYY-MM-DD）。空なら入れていない */
  readonly manual?: string;
}

export type Level = "ok" | "soon" | "expired" | "unknown";

export interface Notice {
  readonly level: Level;
  /** 期限（ISO）。不明なら空 */
  readonly expiresAt: string;
  /** どこから決めたか */
  readonly source: "host" | "manual" | "none";
  /** 残りの日数（切り捨て。切れていれば負）。不明なら null */
  readonly daysLeft: number | null;
  readonly text: string;
}

const DAY = 24 * 3600 * 1000;

/**
 * 利用者が入れた日付（YYYY-MM-DD）を読む。読めなければ空。
 * 期限はその日の終わり（UTC の 23:59:59）とみなす。GitHub の作成画面の期限も日付で、その日のうちは使える
 */
export function parseManual(value: unknown): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return "";
  const t = Date.parse(`${value}T23:59:59Z`);
  return Number.isNaN(t) ? "" : value;
}

export function expiryNotice(host: string, meta: TokenMeta | undefined, now: Date): Notice {
  const fromHost = meta?.host && !Number.isNaN(Date.parse(meta.host)) ? meta.host : "";
  const manual = parseManual(meta?.manual);
  const expiresAt = fromHost || (manual ? new Date(Date.parse(`${manual}T23:59:59Z`)).toISOString() : "");
  const source = fromHost ? "host" : manual ? "manual" : "none";
  if (!expiresAt) {
    return {
      level: "unknown",
      expiresAt: "",
      source,
      daysLeft: null,
      text: `${host} の PAT の期限が分からない（ホストの応答から読めず、登録のときにも入れていない）。設定画面で期限を入れる`,
    };
  }
  // 残りはミリ秒で比べる（切り捨てた日数で比べると 7 日と数時間前から知らせてしまう。レビューの 7）。
  // 見せる日数は切り上げ（残り 2 日と 1 時間は「あと 3 日」）
  const ms = Date.parse(expiresAt) - now.getTime();
  const left = ms > 0 ? Math.ceil(ms / DAY) : Math.floor(ms / DAY);
  const day = expiresAt.slice(0, 10);
  if (ms <= 0) {
    return { level: "expired", expiresAt, source, daysLeft: left, text: `${host} の PAT は期限（${day}）が切れている。作り直して設定画面で差し替える` };
  }
  if (ms <= WARN_DAYS * DAY) {
    return { level: "soon", expiresAt, source, daysLeft: left, text: `${host} の PAT はあと ${left} 日で切れる（${day}）。作り直して設定画面で差し替える` };
  }
  return { level: "ok", expiresAt, source, daysLeft: left, text: `${host} の PAT の期限は ${day}` };
}

/** バッジに出す字（4 字まで）。知らせることが無ければ空 */
export function badgeText(notices: readonly Notice[]): string {
  if (notices.some((n) => n.level === "expired")) return "PAT!";
  const soon = notices.filter((n) => n.level === "soon").map((n) => n.daysLeft ?? 0);
  if (soon.length > 0) return `${Math.max(0, Math.min(...soon))}d`;
  return "";
}
