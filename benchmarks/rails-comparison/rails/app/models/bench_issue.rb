class BenchIssue < ApplicationRecord
  self.table_name = "bench_issues"

  validates :title, length: { in: 1..120 }
  validates :details, length: { maximum: 4000 }
end
