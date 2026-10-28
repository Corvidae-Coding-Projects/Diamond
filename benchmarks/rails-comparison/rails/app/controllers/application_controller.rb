require "digest"

class ApplicationController < ActionController::API
  before_action :charge_benchmark_rate!
  before_action :authenticate_benchmark_credential!

  private

  def charge_benchmark_rate!
    token = request.authorization.to_s.delete_prefix("Bearer ")
    limits = [ 10_000_000, 10_000_000, 10_000_000 ]
    buckets = [
      "api:global",
      "api:peer:#{Digest::SHA256.hexdigest(request.remote_ip)}",
      "api:subject:#{Digest::SHA256.hexdigest(token)}"
    ]
    allowed = BenchRate.transaction(requires_new: true) do
      connection = BenchRate.connection
      connection.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('DiamondRailsBench:identity-rate',0))")
      connection.execute("DELETE FROM rails_bench.bench_rates WHERE expires_at <= pg_catalog.clock_timestamp()")
      buckets.zip(limits).all? do |bucket, limit|
        quoted = connection.quote(bucket)
        result = connection.select_value(<<~SQL.squish)
          INSERT INTO rails_bench.bench_rates(bucket, attempts, expires_at)
          VALUES (#{quoted}, 1, pg_catalog.clock_timestamp() + interval '1 minute')
          ON CONFLICT(bucket) DO UPDATE SET attempts = rails_bench.bench_rates.attempts + 1
          RETURNING attempts
        SQL
        Integer(result) <= limit
      end
    end
    return if allowed

    head :too_many_requests
  end

  def authenticate_benchmark_credential!
    bearer = request.authorization.to_s.delete_prefix("Bearer ")
    digest = Digest::SHA256.hexdigest(bearer)
    @credential = BenchCredential.includes(:bench_user).find_by(digest: digest)
    valid = @credential && !@credential.revoked && @credential.expires_at.future?
    valid &&= !@credential.bench_user.disabled
    return if valid

    head :unauthorized
  end

  def arguments
    value = request.request_parameters["arguments"]
    return value if value.is_a?(Array)

    render json: { error: "invalid arguments" }, status: :bad_request
    nil
  end

  def authorize_workspace!(workspace_id, operation)
    operations = @credential.operations
    scopes = @credential.scopes.fetch("workspace", [])
    permitted = operations.include?(operation) && scopes.include?(workspace_id)
    permitted &&= BenchWorkspace.where(id: workspace_id, owner_id: @credential.user_id)
      .or(BenchWorkspace.where(id: workspace_id).where(
        id: BenchMembership.where(user_id: @credential.user_id).select(:workspace_id)
      )).exists?
    head :forbidden unless permitted
    permitted
  end
end
