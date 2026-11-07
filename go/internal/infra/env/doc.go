// Package env は ccnavi が読む環境変数の名前を 1 か所に集める。
//
// 名前はここの定数だけで書き、コードに文字列で直書きしない。値の読み方（既定値・範囲・単位）は、
// その値を使う責務のパッケージが持つ（設定も時間の上限も infra/config）。
// git に渡す GIT_* は git の起動の決まりなので infra/git が持つ。ccnavi の外が決める名前（GITHUB_TOKEN・
// GITLAB_TOKEN・PATH・HOME など）もここに置く。
//
// Python からの対応: review_host.GITHUB_TOKEN・GITLAB_TOKEN → GitHubToken・GitLabToken、shutil.which が読む PATH →
// Path、os.path.expanduser が読む HOME・USERPROFILE・HOMEDRIVE・HOMEPATH・USERNAME → Home・UserProfile・HomeDrive・
// HomePath・UserName、shutil.get_terminal_size が読む COLUMNS → Columns。ccnavi の変数の頭 `CCNAVI_` → Prefix。
package env
