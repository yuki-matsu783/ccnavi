// Package hostfixture は、ホストの応答の見本を返す GitLab・GitHub のスタブである。
//
// Chrome と手元の sh が同じ見本から同じ JSON を組むことを見るために使う。見本は
// `extensions/chrome/ccnavi-approval/test/fixtures/host/<gitlab|github>/<場面>/` にある。拡張の試験も同じ見本を同じ規則で返す。
// 規則は 2 つのスタブで揃える（規則の一覧は gitlab.go・github.go の Answer の冒頭）。
//
// sh の試験は PATH の先頭に curl（GitHub は gh も）のスタブを置く。Install が `#!/bin/sh` と `exec '<teststub>' gitlab-host curl "$@"` の
// 2 行の sh を置き、teststub の gitlab-host・github-host が GitlabCurl・GithubCurl・GithubGh を起こす。見本の置き場は、
// 子の環境の FAKE_FIXTURE_ROOT（リポジトリの根。Env が入れる）から引く。
//
//	hostfixture.Install(t, bin, hostfixture.GitLab)
//	env := hostfixture.Env(hostfixture.GitLab, "scene-name")   // run.Opts.Env に足す。FAKE_GITLAB_STATE・FAKE_GITLAB_LOG などは呼び手が足す
package hostfixture
