/** 画面に出す最終更新の行。`2026-10-29T09:00:00+0900`を`最終更新: 2026-10-29 09:00`にする。読めない形は時刻の位置にそのまま出す */
export function lastUpdatedText(raw: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw);
  return `最終更新: ${m === null ? raw : `${m[1]} ${m[2]}`}`;
}
