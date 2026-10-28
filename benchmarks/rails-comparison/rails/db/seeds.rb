# This file should ensure the existence of records required to run the application in every environment (production,
# development, test). The code here should be idempotent so that it can be executed at any point in every environment.
# The data can then be loaded with the bin/rails db:seed command (or created alongside the database with db:setup).
#
# Example:
#
#   ["Action", "Comedy", "Drama", "Horror"].each do |genre_name|
#     MovieGenre.find_or_create_by!(name: genre_name)
#   end
require "digest"

dataset = Integer(ENV.fetch("BENCH_DATASET", "1000"))
bearer = ENV.fetch("BENCH_BEARER")
alice = Digest::SHA256.hexdigest("rails-bench-alice")
bob = Digest::SHA256.hexdigest("rails-bench-bob")
main_workspace = Digest::SHA256.hexdigest("rails-bench-main-workspace")
denied_workspace = Digest::SHA256.hexdigest("rails-bench-denied-workspace")

ApplicationRecord.transaction do
  [ BenchIssue, BenchMembership, BenchWorkspace, BenchCredential, BenchRate, BenchUser ].each(&:delete_all)
  BenchUser.insert_all!([
    { id: alice, disabled: false, epoch: 0 },
    { id: bob, disabled: false, epoch: 0 }
  ])
  BenchWorkspace.insert_all!([
    { id: main_workspace, owner_id: alice, label: "Main benchmark workspace" },
    { id: denied_workspace, owner_id: bob, label: "Denied benchmark workspace" }
  ])
  BenchCredential.create!(
    id: Digest::SHA256.hexdigest("rails-bench-credential"),
    user_id: alice,
    digest: Digest::SHA256.hexdigest(bearer),
    operations: %w[page read create update],
    scopes: { workspace: [ main_workspace ] },
    expires_at: 30.days.from_now,
    revoked: false
  )

  rows = Array.new(dataset) do |index|
    {
      id: Digest::SHA256.hexdigest("rails-bench-issue-#{index}"),
      owner_id: alice,
      workspace_id: main_workspace,
      title: format("Issue %08d", index),
      details: "Benchmark issue details #{index} — Unicode 雪",
      rank: index
    }
  end
  rows.each_slice(1000) { |slice| BenchIssue.insert_all!(slice) }
  BenchIssue.create!(
    id: Digest::SHA256.hexdigest("rails-bench-denied-issue"),
    owner_id: bob,
    workspace_id: denied_workspace,
    title: "Denied issue",
    details: "This issue belongs to another principal.",
    rank: 0
  )
end

puts({
  dataset: dataset,
  workspace: main_workspace,
  issue: Digest::SHA256.hexdigest("rails-bench-issue-0"),
  denied_issue: Digest::SHA256.hexdigest("rails-bench-denied-issue")
}.to_json)
