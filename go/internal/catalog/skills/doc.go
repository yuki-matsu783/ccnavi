// Package skills は、プロジェクトのスキルの目録（名前・説明・場所）を組み、モデルへ渡す。
//
// スキルの手順書はプロジェクトの git で育てるので、プロジェクトの `skills/<名前>/SKILL.md` に置く。ccnavi の
// ディレクトリの外にあるので、中は組み込みの保護の対象にならない。Claude Code は `skills/` をスキルとして読まない。
// そこで ccnavi は目録だけを渡し、本文はエージェントが必要なときに開く。渡すのは、cwd がそのプロジェクトのツリー
// （元のリポジトリか、そこから切ったワークツリー）の中にあるときだけ。読むのは元のリポジトリの版。
//
// SKILL.md の中身を誰が書いたか ccnavi には分からない。そこで、データとして区切りで囲み、1 行にまとめて切る
// （flow.Line）。区切りの文は flow.FencePhrases に並べてあり、名前や説明の中に出たら置き換わる。
//
// Python からの対応: projskills。notice → Notice、index・entries・project_of・_front は非公開。
package skills
