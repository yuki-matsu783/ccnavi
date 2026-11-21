// Package hoststub は、ホスト（GitHub / GitLab）のスタブを PATH の先頭に置く（tests/host_stub.py の写し）。
//
// write-and-push・ticket の sh・受入シナリオの setup.host が使う。置くのは次の 4 本。
//   - git: `git remote get-url origin` だけホストの形の URL（OriginURL）を答え、あとは本物の git へ渡す（自分の置き場は PATH から外す）。
//     origin が本物のホストのときは、bare と本物の URL を渡すと `-c url.<bare>.insteadOf=<本物の URL>` で bare へ向く
//     （対象のリポジトリだけに効かせるのは TargetCommon）
//   - gh・glab: 失敗する（`exit 1`）
//   - curl: routes.json で答える。`#!/bin/sh` と `exec '<teststub>' curl "$@"` の 2 本（teststub の curl）。Build の curl を false にすると置かない
//     （fakegitlab の模擬に届くように）
//
// トークンを渡さなければ、ホストの API は「確かめられなかった」になり、git のリモートの確認は通る。本物のネットワークには出ない。
//
//	stub := hoststub.Build(t, base.TempDir(t), true)
//	stub.WriteRoutes(t, hoststub.Routes{"GET https://gitlab.com/api/v4/user": {Status: 200, Body: map[string]any{"id": 1}}})
//	env := stub.Env(hoststub.EnvOpts{Token: "t"})   // run.Opts.Env に渡す差分
package hoststub
