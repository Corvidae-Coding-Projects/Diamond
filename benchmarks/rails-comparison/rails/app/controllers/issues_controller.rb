class IssuesController < ApplicationController
  def page
    values = arguments
    return unless values

    workspace_id, after = values
    return unless authorize_workspace!(workspace_id, "page")

    relation = BenchIssue.where(workspace_id: workspace_id).order(:rank, :id)
    unless after == ""
      cursor = decode_cursor(after)
      return render json: { error: "invalid_input" }, status: :bad_request unless cursor

      relation = relation.where("(rank, id) > (?, ?)", cursor.fetch("key"), cursor.fetch("id"))
    end
    rows = relation.limit(20).to_a
    cursor = rows.length == 20 ? encode_cursor(rows.last) : ""
    render json: {
      api_version: 1,
      value: {
        cards: rows.map { |row| issue_wire(row) },
        after: cursor,
        total: BenchIssue.where(workspace_id: workspace_id).count.to_s
      }
    }
  end

  def read
    values = arguments
    return unless values

    issue = BenchIssue.find_by(id: values.first)
    return head :not_found unless issue
    return unless authorize_workspace!(issue.workspace_id, "read")

    render json: { api_version: 1, value: "#{issue.title}\n#{issue.details}" }
  end

  def create
    values = arguments
    return unless values

    workspace_id, title, details, rank = values
    return unless authorize_workspace!(workspace_id, "create")

    issue = BenchIssue.create!(
      id: SecureRandom.hex(32),
      workspace_id: workspace_id,
      owner_id: @credential.user_id,
      title: title,
      details: details,
      rank: rank
    )
    render json: { api_version: 1, value: issue.id }
  end

  def update
    values = arguments
    return unless values

    issue = BenchIssue.find_by(id: values.first)
    return head :not_found unless issue
    return unless authorize_workspace!(issue.workspace_id, "update")

    issue.update!(title: values.fetch(1), details: values.fetch(2))
    render json: { api_version: 1, value: "#{issue.title}\n#{issue.details}" }
  end

  private

  def issue_wire(issue)
    {
      id: { tag: "issue", value: issue.id },
      title: issue.title,
      details: issue.details,
      rank: issue.rank.to_s
    }
  end

  def encode_cursor(issue)
    JSON.generate(
      version: 1,
      resource: "Issues",
      order: "rank",
      descending: false,
      key: issue.rank.to_s,
      id: issue.id
    ).unpack1("H*")
  end

  def decode_cursor(value)
    return unless value.is_a?(String) && value.length <= 16_384
    return unless value.match?(/\A(?:[0-9a-f]{2})+\z/)

    cursor = JSON.parse([ value ].pack("H*"))
    return unless cursor == {
      "version" => 1,
      "resource" => "Issues",
      "order" => "rank",
      "descending" => false,
      "key" => cursor["key"],
      "id" => cursor["id"]
    }
    return unless cursor["key"].is_a?(String) && cursor["key"].match?(/\A(?:0|[1-9][0-9]*)\z/)
    return unless cursor["id"].is_a?(String) && cursor["id"].match?(/\A[0-9a-f]{64}\z/)

    cursor
  rescue JSON::ParserError, ArgumentError, TypeError
    nil
  end
end
