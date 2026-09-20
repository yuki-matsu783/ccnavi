/**
 * カードの行末のメニュー（「開く ▾」「git ▾」）。`<details>` 1 つで、開いているのは画面ぜんたいで 1 つだけ。
 *
 * 開閉は App が持つ（どれが開いているか）。人が summary を押すと `<details>` が自分で開くので、
 * その `toggle` を受けて App に伝え、App は他のメニューを閉じる。外を押したときと Esc は App が拾う。
 */
import type { JSX, ReactNode } from "react";

/** カード 1 枚が持つメニューの種類 */
export const MENU_KINDS = ["open", "git"] as const;
export type MenuKind = (typeof MENU_KINDS)[number];

/**
 * 画面の中で一意なメニューの名前。
 *
 * **組み立ても読み取りも、この関数と `MENU_KINDS` を通す。** プロジェクトの名前は置き場の
 * ディレクトリ名そのままで、画面の clone の欄が通す綴り（英数字と `. _ -`）とは限らない。
 * 人が `projects/app:staging/` を置けば `:` が名前に入る。前方一致で持ち主を探すと、
 * `app` が `app:staging` のメニューを自分のものだと言い出す
 */
export function menuId(name: string, kind: MenuKind): string {
  return `${name}:${kind}`;
}

export interface MenuProps {
  /** 画面の中で一意な名前。App が「いま開いているのはどれか」をこれで持つ */
  readonly id: string;
  readonly label: string;
  readonly open: boolean;
  readonly onOpen: (id: string | undefined) => void;
  readonly children: ReactNode;
}

export function Menu({ id, label, open, onOpen, children }: MenuProps): JSX.Element {
  return (
    <details
      className="menu"
      open={open}
      onToggle={(event) => {
        const nowOpen = (event.currentTarget as HTMLDetailsElement).open;
        // 閉じたことを伝えるのは、自分が開いていた側のときだけ。別のメニューを開いたときに
        // React がこちらを閉じ、その toggle で「閉じた」を送り返すと、開いたばかりのほうが消える
        if (nowOpen) {
          onOpen(id);
        } else if (open) {
          onOpen(undefined);
        }
      }}
    >
      <summary className="action">{label}</summary>
      <div className="menu-items">{children}</div>
    </details>
  );
}
