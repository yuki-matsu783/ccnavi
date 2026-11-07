package judge

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
)

// GuardOutcomes は ccnavi 自身の設定ファイルを、バックアップと突き合わせる 1 回。step は selfguard の Before・After・
// Accept・AtStart のどれか。行ったことを record.Guarded に残し、結果を返す。対象とモードの掛け方をイベントごとに
// 書き直さないよう、どのイベントもここを通す。
func GuardOutcomes(mode config.Mode, conf *config.Settings, root string, in hookio.Input, record *audit.Record,
	step selfguard.Step) []selfguard.Outcome {
	setting := config.EffectiveSetting(mode, config.Mode(conf.GuardCoreFiles))
	targets := selfguard.Targets(root, conf.Rules, conf.Bin, ruleload.LayerFiles(conf, root), conf.Projects)
	outcomes := step(setting, conf.State, in.SessionID, root, targets)
	if len(outcomes) == 0 {
		return nil
	}
	record.Guarded = make([]string, len(outcomes))
	for i, o := range outcomes {
		record.Guarded[i] = o.Target.Key + ":" + string(o.Action)
	}
	return outcomes
}

// GuardSettingFiles は GuardOutcomes の結果を、モデルに知らせる文にして返す（何もしなければ空）。
func GuardSettingFiles(mode config.Mode, conf *config.Settings, root string, in hookio.Input, record *audit.Record,
	step selfguard.Step) string {
	return selfguard.Report(root, GuardOutcomes(mode, conf, root, in, record, step))
}
