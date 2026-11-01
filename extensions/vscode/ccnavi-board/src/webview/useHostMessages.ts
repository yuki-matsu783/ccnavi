import { useEffect, useRef } from "react";
import { applyAppearance } from "./appearance.js";

/**
 * 拡張ホストから届くメッセージの受信。各画面で同じ形だった受信ループをここに寄せる。
 *
 * - `appearance`はここで受けてbodyのクラスを切り替える（画面のhandlerには渡さない）
 * - 受信の登録が済んだら`ready`を1回だけ呼ぶ（送る関数は画面ごとに違うので、呼ぶ側が渡す）。非表示になって破棄された画面は、再表示されると
 *   拡張ホストが入れてあるHTMLから作り直される。そのHTMLは少し古いことがあるので、いまの中身をもらい直す
 * - handlerは毎回の描画で差し替わってよい。登録は1回だけなので、`ready`も1回だけ呼ぶ
 */
export function useHostMessages<T extends { type?: string }>(handler: (message: Partial<T> & Record<string, unknown>) => void, ready: () => void): void {
  const latest = useRef(handler);
  latest.current = handler;
  const readyRef = useRef(ready);
  readyRef.current = ready;
  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<T> & Record<string, unknown>;
      if (message.type === "appearance") {
        applyAppearance(message.value, message.zoom);
        return;
      }
      latest.current(message);
    };
    window.addEventListener("message", onMessage);
    readyRef.current();
    return () => window.removeEventListener("message", onMessage);
  }, []);
}
