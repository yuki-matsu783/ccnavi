// Package gitlabdocker は、Docker の本物の GitLab CE を起動・停止・確認する。
// 手で走らせる道具で、自動テストは本物を立てない（docker の呼び出しと GitLab への HTTP を差し込み口から差し替えて見る）。
//
//		gitlabmanual up [--pull] [--rotate]   起動して、応答するまで待つ（Tool.Up）
//		gitlabmanual status                   状態・版・トークンの有効性（Tool.Status）
//		gitlabmanual down [--purge]           止めて消す（Tool.Down）
//
//	  - 版は Image の 1 か所で固定する（ダイジェスト指定）。上げるときはここを書き換える。
//	  - ポートは環境変数 CCNAVI_GITLAB_PORT（既定 8929）の 1 か所で決める。compose の external_url と公開ポート、probe・契約の録りの
//	    既定の接続先（Base）は、この値から作る。
//	  - up はイメージが無ければ取得せず止まる（--pull のときだけ取る）。トークンは有効なら作り直さない（--rotate のときだけ）。
//	    0600 のファイル（StateDir の tokens.env）に書き、端末には出さない。
//	  - root のパスワードは、コンテナもボリュームも無いときだけ作る（ボリュームが残っていれば GitLab が初回の値を持っているので作らない）。
//	  - 立て方（external_url と公開ポートの一致・127.0.0.1 へのバインド・shm_size・restart no）は tests/manual/gitlab/compose.yml。
//
// 受入シナリオ（scenario/host.Docker）には、接続先として Base と TokensPath を渡す。
package gitlabdocker
