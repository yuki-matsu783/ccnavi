package fault

import "errors"

// Values は文面に差し込む値（名前 → 値）。
type Values = map[string]string

// Detail は読み手に見せる理由の材料。エラー型が埋め込む。
// ID は文面ファイルの識別子（`ERROR_` で始まる）で、空なら What を出す。
// What は文面を引けないとき・ログに残すときの 1 行で、Error() の文でもある。
type Detail struct {
	ID     string
	Values Values
	What   string
}

// New は Detail を作る。識別子の字が文面ファイルにあるかは、リポジトリのテストが点検する。
func New(id string, values Values, what string) Detail {
	return Detail{ID: id, Values: values, What: what}
}

// Plain は文面ファイルを引かない Detail（読み手に届かないエラー用）。
func Plain(what string) Detail { return Detail{What: what} }

func (d Detail) Error() string { return d.What }

// Fault は Carrier を満たすための取り出し口。
func (d Detail) Fault() Detail { return d }

// Carrier は Detail を運ぶエラー。
type Carrier interface{ Fault() Detail }

// Of は err の連なりから、最初の Detail を取り出す。
func Of(err error) (Detail, bool) {
	var c Carrier
	if errors.As(err, &c) {
		return c.Fault(), true
	}
	return Detail{}, false
}
