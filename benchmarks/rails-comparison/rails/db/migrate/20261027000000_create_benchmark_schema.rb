class CreateBenchmarkSchema < ActiveRecord::Migration[8.1]
  def change
    create_table :bench_users, id: false do |table|
      table.string :id, limit: 64, null: false, primary_key: true
      table.boolean :disabled, null: false, default: false
      table.bigint :epoch, null: false, default: 0
    end

    create_table :bench_credentials, id: false do |table|
      table.string :id, limit: 64, null: false, primary_key: true
      table.string :user_id, limit: 64, null: false
      table.string :digest, limit: 64, null: false
      table.jsonb :operations, null: false
      table.jsonb :scopes, null: false
      table.datetime :expires_at, null: false
      table.boolean :revoked, null: false, default: false
    end
    add_index :bench_credentials, :digest, unique: true
    add_foreign_key :bench_credentials, :bench_users, column: :user_id, primary_key: :id

    create_table :bench_rates, id: false do |table|
      table.string :bucket, null: false, primary_key: true
      table.bigint :attempts, null: false
      table.datetime :expires_at, null: false
    end

    create_table :bench_workspaces, id: false do |table|
      table.string :id, limit: 64, null: false, primary_key: true
      table.string :owner_id, limit: 64, null: false
      table.string :label, limit: 80, null: false
    end
    add_foreign_key :bench_workspaces, :bench_users, column: :owner_id, primary_key: :id

    create_table :bench_memberships, id: false do |table|
      table.string :id, limit: 64, null: false, primary_key: true
      table.string :workspace_id, limit: 64, null: false
      table.string :user_id, limit: 64, null: false
      table.string :role, limit: 16, null: false
    end
    add_index :bench_memberships, [ :workspace_id, :user_id ], unique: true
    add_foreign_key :bench_memberships, :bench_workspaces, column: :workspace_id, primary_key: :id
    add_foreign_key :bench_memberships, :bench_users, column: :user_id, primary_key: :id

    create_table :bench_issues, id: false do |table|
      table.string :id, limit: 64, null: false, primary_key: true
      table.string :owner_id, limit: 64, null: false
      table.string :workspace_id, limit: 64, null: false
      table.string :title, limit: 120, null: false
      table.text :details, null: false
      table.bigint :rank, null: false
    end
    add_index :bench_issues, [ :workspace_id, :rank, :id ]
    add_foreign_key :bench_issues, :bench_workspaces, column: :workspace_id, primary_key: :id
    add_foreign_key :bench_issues, :bench_users, column: :owner_id, primary_key: :id
  end
end
