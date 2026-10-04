/**
 * blob の控え。ボードを開くたびに読むのを tree だけにするため、blob を sha で引いて IndexedDB に控える。
 * sha が同じなら中身は同じなので、失効を持たない。
 * IndexedDB が使えなければ控えずに読む（毎回取る）。
 */
import type { BlobCache } from "../core/snapshot.js";

const DB = "ccnavi-approval";
const STORE = "blobs";

function open(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    try {
      const req = indexedDB.open(DB, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

export async function blobCache(): Promise<BlobCache> {
  const db = await open();
  if (db === null) {
    return { get: async () => undefined, put: async () => undefined };
  }
  const run = <T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>) =>
    new Promise<T | undefined>((resolve) => {
      try {
        const req = fn(db.transaction(STORE, mode).objectStore(STORE));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(undefined);
      } catch {
        resolve(undefined);
      }
    });
  return {
    get: async (key) => {
      const v = await run<unknown>("readonly", (s) => s.get(key));
      return typeof v === "string" ? v : undefined;
    },
    put: async (key, text) => {
      await run("readwrite", (s) => s.put(text, key));
    },
  };
}
