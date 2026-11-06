// Package digest は 16 進の短い名前を作る。中身から決まる要約（Hex・HexOf）と、毎回変わる乱数（RandomHex）の 2 つで、
// 記録・バックアップのキーと名前に使う。要約は Python の hashlib と同じ字を返す（記録のキーが Python の版と揃う）。
//
// Python からの対応: hashlib.sha1(text.encode()).hexdigest()[:n] → Hex(SHA1, text, n)、sha256 → Hex(SHA256, …)、
// ファイルを塊で読む sha256 の hexdigest → HexOf(SHA256, r)、secrets.token_hex(n) → RandomHex(n)、
// uuid.uuid4().hex（一意な名前として使うもの）→ RandomHex(UUIDBytes)。
package digest
