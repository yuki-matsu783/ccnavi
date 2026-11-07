//go:build windows

package platformtag

// executable は os.access(path, X_OK)。Windows では在れば実行できるとみなす（Python と同じ）。
func executable(string) bool { return true }
