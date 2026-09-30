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
  namespace action {
    const onClicked: { addListener(cb: () => void): void };
    function setBadgeText(details: { text: string }): Promise<void>;
    function setBadgeBackgroundColor(details: { color: string }): Promise<void>;
    function setTitle(details: { title: string }): Promise<void>;
  }
  namespace alarms {
    interface Alarm {
      name: string;
    }
    function create(name: string, info: { periodInMinutes?: number; delayInMinutes?: number }): Promise<void>;
    const onAlarm: { addListener(cb: (alarm: Alarm) => void): void };
  }
}

// ビルドが焼き込む通信先（scripts/build.js の define）。
declare const __CCNAVI_HOSTS__: import("../core/hosts.js").Host[];
declare const __CCNAVI_VERSION__: string;
