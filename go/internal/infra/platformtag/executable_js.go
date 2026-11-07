//go:build js

package platformtag

import "os"

// executable は os.access(path, X_OK)。js（Chrome 拡張の WASM）には access(2) が無いので、権限ビットを見る。
func executable(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().Perm()&0o111 != 0
}
