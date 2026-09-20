/**
 * カードの行末のメニュー（「開く ▾」「git ▾」）。`<details>` 1 つで、開いているのは画面ぜんたいで 1 つだけ。
 *
 * 開閉は App が持つ（どれが開いているか）。人が summary を押すと `<details>` が自分で開くので、
 * その `toggle` を受けて App に伝え、App は他のメニューを閉じる。外を押したときと Esc は App が拾う。
 */
import type { JSX, ReactNode } from "react";

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
