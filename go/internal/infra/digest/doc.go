// Package digest は 16 進の短い名前を作る。中身から決まる要約（Hex・HexBytes・HexOf・New と HexSum）と、毎回変わる
// 乱数（RandomHex）の 2 つで、記録・バックアップのキーと名前に使う。要約は sha1・sha256 の標準の 16 進と同じ字を返す
// ので、同じ中身からは別の機械でも同じキーが出る。ダイジェストの符号に使う base64 の読み（URLSafeB64Decode）もここに置く。
//
// 一意な名前に使う乱数は RandomHex(UUIDBytes) で作る。
package digest
