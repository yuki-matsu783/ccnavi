/**
 * 図の編集（フロー編集・計画編集・テンプレート管理の編集欄）の上部の並び。
 * 「対象の名前 → 状態 → 元に戻す・やり直す → 補助の操作 → 主ボタン」の順をここ1か所で決め、3つの画面が同じ順で並ぶようにする。
 *
 * 中身（ボタンや名前）は呼ぶ側が作って渡す。何を押せるか・何を状態として出すかは画面ごとに違うので、ここは置く順と見た目だけを持つ。
 * 状態は`EditorState`で1つずつ渡し、出さない間も要素は残して`hidden`で隠す（`#dirty`のように、テストと案内が場所を指すため）。
 */
import type { JSX, ReactNode } from "react";

export interface EditorBarProps {
  /** 対象の名前（チケット・テンプレートの名前、ファイルのパスなど） */
  readonly name: ReactNode;
  /** 状態（未保存・閲覧のみ・提案あり・外で変わった など）。`EditorState`を並べる */
  readonly states?: ReactNode;
  /** 元に戻す・やり直す。持たない画面（計画編集）は渡さない */
  readonly history?: ReactNode;
  /** 補助の操作 */
  readonly aux?: ReactNode;
  /** 主ボタン（保存・この順序に決める）。1つだけ */
  readonly primary: ReactNode;
  /** 右の操作の並び全体を指す案内の印（`data-tour`） */
  readonly controlsTour?: string;
}

export function EditorBar({ name, states, history, aux, primary, controlsTour }: EditorBarProps): JSX.Element {
  return (
    <>
      <div className="summary editor-name">
        {name}
        {states !== undefined && <span className="editor-states">{states}</span>}
      </div>
      <div className="controls editor-controls" data-tour={controlsTour}>
        {history !== undefined && (
          <span className="editor-history" role="group" aria-label="元に戻す・やり直す">
            {history}
          </span>
        )}
        {aux}
        {primary}
      </div>
    </>
  );
}

export interface EditorStateProps {
  readonly shown: boolean;
  /** `dirty`は未保存（注意の色）、`warn`は保存できない・外で変わった、`info`はそれ以外 */
  readonly tone: "dirty" | "warn" | "info";
  readonly id?: string;
  readonly tour?: string;
  readonly role?: string;
  readonly title?: string;
  readonly children: ReactNode;
}

export function EditorState({ shown, tone, id, tour, role, title, children }: EditorStateProps): JSX.Element {
  return (
    <span id={id} data-tour={tour} data-role={role} title={title} className={`editor-state ${tone}${shown ? "" : " hidden"}`}>
      {children}
    </span>
  );
}
