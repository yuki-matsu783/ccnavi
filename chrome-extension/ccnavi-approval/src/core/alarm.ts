/**
 * PAT の期限を比べる 1 日 1 回の alarm。切れる 7 日前から知らせる。
 *
 * service worker は止まっては起動し直すので、起動するたびに alarm を作り直すと周期が起動した時刻から
 * 数え直しになり、1 日 1 回にならない。在るときは作らない。
 */
export const ALARM = "pat-expiry";
export const PERIOD_MINUTES = 24 * 60;

export interface AlarmApi {
  get(name: string): Promise<{ name: string; periodInMinutes?: number } | undefined>;
  create(name: string, info: { periodInMinutes?: number; delayInMinutes?: number }): Promise<void>;
}

/** 1 日 1 回の alarm が無いときだけ作る。作ったら真 */
export async function ensureDailyAlarm(api: AlarmApi): Promise<boolean> {
  const have = await api.get(ALARM);
  if (have && have.periodInMinutes === PERIOD_MINUTES) return false;
  await api.create(ALARM, { periodInMinutes: PERIOD_MINUTES, delayInMinutes: 1 });
  return true;
}
