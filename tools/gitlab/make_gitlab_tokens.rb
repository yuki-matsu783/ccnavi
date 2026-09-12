# 実物の GitLab（Docker）で ccnavi を試すための personal access token を、ブラウザを開かずに作る。
# root（エージェント役、GITLAB_TOKEN）と ccnavi-reviewer（人間役）の 2 人分。
#
#   docker exec -i gitlab gitlab-rails runner - < tools/gitlab/make_gitlab_tokens.rb
#
# 出力は KEY=VALUE の 4 行。probe_gitlab.py はこれを環境変数で受ける。
# GitLab 18 は組織（organization）と、よくある語を含まないパスワードを求める。
require 'securerandom'

def ensure_token(user, name, value)
  user.personal_access_tokens.active.where(name: name).find_each(&:revoke!)
  attrs = { name: name, scopes: [:api, :write_repository, :read_repository], expires_at: 7.days.from_now }
  attrs[:organization] = user.organization if user.respond_to?(:organization) && user.organization
  t = user.personal_access_tokens.build(attrs)
  t.set_token(value)
  t.save!
  value
end

root = User.find_by_username('root')
raise 'root が無い' unless root
org = nil
if defined?(Organizations::Organization)
  org = (Organizations::Organization.default_organization rescue nil) || Organizations::Organization.first
end

reviewer = User.find_by_username('ccnavi-reviewer')
unless reviewer
  params = {
    username: 'ccnavi-reviewer', email: 'ccnavi-reviewer@example.com', name: 'ccnavi reviewer',
    password: SecureRandom.alphanumeric(24) + 'Q9!', skip_confirmation: true
  }
  params[:organization_id] = org.id if org
  res = Users::CreateService.new(root, params).execute
  reviewer = res.respond_to?(:payload) ? res.payload[:user] : res
  reviewer = User.find_by_username('ccnavi-reviewer') unless reviewer.is_a?(User) && reviewer.persisted?
  raise "reviewer を作れない: #{res.respond_to?(:message) ? res.message : res.inspect}" unless reviewer
end
reviewer.confirm if reviewer.respond_to?(:confirm) && !reviewer.confirmed?
reviewer.update_columns(password_expires_at: nil) if reviewer.respond_to?(:password_expires_at)

puts "GITLAB_TOKEN=#{ensure_token(root, 'ccnavi-probe-root', 'glpat-ccnavi-root-' + SecureRandom.hex(8))}"
puts "CCNAVI_PROBE_REVIEWER_TOKEN=#{ensure_token(reviewer, 'ccnavi-probe-reviewer', 'glpat-ccnavi-rev-' + SecureRandom.hex(8))}"
puts "CCNAVI_PROBE_REVIEWER_ID=#{reviewer.id}"
puts "CCNAVI_PROBE_ROOT_ID=#{root.id}"
