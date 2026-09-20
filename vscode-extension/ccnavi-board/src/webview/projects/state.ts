/**
 * 画面が覚えておくもの。clone の欄に打ちかけた URL と名前、名前を人が触ったか。
 *
 * 置き場は Webview の state で、拡張が HTML を作り直しても（裏に回って作り直されても）残る。
 * 形は React にする前と同じにしてある。入れ替えたときに、打ちかけの URL が消えないように。
 */
import { getState, setState } from "../vscode.js";

export interface CloneState {
  readonly url: string;
  readonly name: string;
  /** 名前を人が打った。真なら URL から自動で埋め直さない */
  readonly nameTouched: boolean;
}

export const EMPTY: CloneState = { url: "", name: "", nameTouched: false };

/** 覚えていた値を読む。型が違うもの・知らないものは既定に倒す */
export function loadClone(): CloneState {
  const saved = (getState() ?? {}) as Partial<Record<keyof CloneState, unknown>>;
  return {
    url: typeof saved.url === "string" ? saved.url : EMPTY.url,
    name: typeof saved.name === "string" ? saved.name : EMPTY.name,
    nameTouched: saved.nameTouched === true,
  };
}

export function saveClone(state: CloneState): void {
  setState({ url: state.url, name: state.name, nameTouched: state.nameTouched });
}

/** URL の末尾から採る、`projects/<名前>` の既定の名前。拡張ホスト側の検査（checkName）はこの後 */
export function guessName(text: string): string {
  const trimmed = text.trim().replace(/\/+$/, "").replace(/\.git$/i, "");
  return trimmed.split(/[/:]/).pop() ?? "";
}
