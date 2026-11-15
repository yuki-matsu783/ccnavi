package start

import "ccnavi/internal/infra/reply"

// outcome は、ccnavi-start.sh の段取りから呼ばれる動詞の答え。形は reply にある。
type outcome = reply.Lines

// 終了コード（ccnavi-start.sh の契約）。
const (
	exitUnmet = 1 // 前提の未充足
	exitUsage = 2 // 引数か環境の誤り
	exitAsk   = 3 // 候補が複数などで、何も作らずにユーザに聞く
)
