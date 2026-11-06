package turn

import (
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
)

// RecordBusy はメインエージェントの Stop で、動かしている作業（バックグラウンドのコマンド・予約した繰り返し）が
// 残っていたかを書く。残っていれば印を置き、残っていなければ消す。
func RecordBusy(state, session string, busy bool) {
	if state == "" || session == "" {
		return
	}
	file := path(state, BusyPrefix, session)
	if busy {
		fsio.WriteJSON(file, pyjson.NewObject("busy", true))
		return
	}
	fsio.Remove(file)
}

// Busy は前のターンの終わりに、動かしている作業が残っていたか。在るのに読めない印は残っているものとして読む
// （覚えられないまま変更を受け入れて保護を外すより、受け入れない側が安全）。
func Busy(state, session string) bool {
	if state == "" || session == "" {
		return false
	}
	file := path(state, BusyPrefix, session)
	if !ospath.DiskExists(file) {
		return false
	}
	data, err := fsio.ReadJSON(file)
	if err != nil {
		return true
	}
	obj, ok := data.(*pyjson.Object)
	if !ok {
		return true
	}
	value, ok := obj.Value("busy").(bool)
	return !ok || value
}
