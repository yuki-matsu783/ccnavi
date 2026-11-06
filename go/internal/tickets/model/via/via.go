package via

import "sync"

// Route は起動を動かした経路。
type Route string

const (
	// CLI はエージェントが sh（ccnavi-ticket.sh / ccnavi-review.sh）から打ったサブコマンド。
	CLI Route = "cli"
	// Terminal はユーザがターミナルで打った判断（--agree / --reviewed / --close-early）。
	Terminal Route = "terminal"
	// Board は VS Code のボードから押した判断（--agree --yes / --reviewed --yes）。
	Board Route = "board"
	// Hook は hook（フェーズの終わりの告知が置くマーカー）。
	Hook Route = "hook"
	// Chrome は Chrome 拡張から押した判断（承認・レビュー済みなど）。
	Chrome Route = "chrome"
)

var (
	mu      sync.Mutex
	current = CLI
	actor   string
)

// Session は 1 回の起動の間、経路とアカウントを覚える。返した関数で前の値へ戻す
// （テストは同じプロセスで何度も起動するので、前の起動の値を次へ引き継がない）。
func Session(r Route, who string) (restore func()) {
	mu.Lock()
	beforeRoute, beforeActor := current, actor
	current, actor = r, who
	mu.Unlock()
	return func() {
		mu.Lock()
		current, actor = beforeRoute, beforeActor
		mu.Unlock()
	}
}

// SetRoute はいまの起動の経路を差し替える（経路が後から分かったとき）。
func SetRoute(r Route) {
	mu.Lock()
	current = r
	mu.Unlock()
}

// SetActor はいまの起動のアカウントを足す（decide が使う）。空なら何もしない。
func SetActor(who string) {
	if who == "" {
		return
	}
	mu.Lock()
	actor = who
	mu.Unlock()
}

// Current はいまの経路。
func Current() Route {
	mu.Lock()
	defer mu.Unlock()
	return current
}

// Actor はいまのアカウント。
func Actor() string {
	mu.Lock()
	defer mu.Unlock()
	return actor
}
