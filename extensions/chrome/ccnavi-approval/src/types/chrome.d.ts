// 使う分だけの chrome の型。@types/chrome を足さずに済ませる。
declare namespace chrome {
  namespace runtime {
    const id: string;
    function getURL(path: string): string;
    function sendMessage(message: unknown): Promise<unknown>;
    function openOptionsPage(): Promise<void>;
    interface MessageSender {
      id?: string;
      url?: string;
    }
    const onMessage: {
      addListener(cb: (message: unknown, sender: MessageSender, sendResponse: (r: unknown) => void) => boolean | void): void;
    };
  }
  namespace storage {
    interface Area {
      get(keys: string | string[] | null): Promise<Record<string, unknown>>;
      set(items: Record<string, unknown>): Promise<void>;
      remove(keys: string | string[]): Promise<void>;
    }
    const local: Area;
  }
  namespace tabs {
    function create(props: { url: string }): Promise<unknown>;
  }
  namespace permissions {
    /** 設定画面のボタンから呼ぶ（ユーザの操作が要る）。許可されたら true */
    function request(permissions: { origins: string[] }): Promise<boolean>;
    /** 外した通信先の許可を外す。許可が無ければ false */
    function remove(permissions: { origins: string[] }): Promise<boolean>;
  }
  namespace action {
    const onClicked: { addListener(cb: () => void): void };
    function setBadgeText(details: { text: string }): Promise<void>;
    function setBadgeBackgroundColor(details: { color: string }): Promise<void>;
    function setTitle(details: { title: string }): Promise<void>;
  }
  namespace alarms {
    interface Alarm {
      name: string;
      periodInMinutes?: number;
    }
    function get(name: string): Promise<Alarm | undefined>;
    function create(name: string, info: { periodInMinutes?: number; delayInMinutes?: number }): Promise<void>;
    const onAlarm: { addListener(cb: (alarm: Alarm) => void): void };
  }
}

// ビルドが埋め込む通信先（scripts/build.js の define）。
declare const __CCNAVI_HOSTS__: import("../core/hosts.js").Host[];
declare const __CCNAVI_VERSION__: string;
/** 同梱の ccnavi の互換の版（`src/ccnavi/entry/version.py` の COMPAT。ビルドが埋め込む） */
declare const __CCNAVI_COMPAT__: number;
