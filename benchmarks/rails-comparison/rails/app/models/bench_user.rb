class BenchUser < ApplicationRecord
  self.table_name = "bench_users"
  has_many :bench_credentials, foreign_key: :user_id, inverse_of: :bench_user
end
