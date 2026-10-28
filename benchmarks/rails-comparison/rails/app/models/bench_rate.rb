class BenchRate < ApplicationRecord
  self.table_name = "bench_rates"
  self.primary_key = :bucket
end
