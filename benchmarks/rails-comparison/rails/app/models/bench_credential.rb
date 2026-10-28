class BenchCredential < ApplicationRecord
  self.table_name = "bench_credentials"
  belongs_to :bench_user, foreign_key: :user_id, inverse_of: :bench_credentials
end
