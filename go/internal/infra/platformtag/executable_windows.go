//go:build windows

package platformtag

// executable は実行できるか。Windows では実行の権限ビットが無いので、あれば実行できるとみなす。
func executable(string) bool { return true }
