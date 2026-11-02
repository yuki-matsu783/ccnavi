/** 画面に出す最終更新の時刻。`2026-10-29T09:00:00+0900`を`2026-10-29 09:00`にする。読めない形はそのまま出す */
export function formatGeneratedAt(raw: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw);
  return m === null ? raw : `${m[1]} ${m[2]}`;
}
