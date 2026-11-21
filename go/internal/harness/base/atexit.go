package base

// AtExit は Main が終わりに消す置き場を足す。テスト 1 本の寿命を超えて使い回す置き場（treecache の写しなど）が使う。
func AtExit(path string) { atExit(path) }
