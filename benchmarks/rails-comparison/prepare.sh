#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
rails_dir="${script_dir}/rails"
# Ruby is pinned independently from Rails so native gems are reproducible.
# Update this digest deliberately after reviewing the official image update.
ruby_image="docker.io/library/ruby@sha256:b95324944d8326fc2c0908461a39ea055f5b493e85c364a7e6b4fb7b04be890f"

if [[ "$(ruby -e 'print RUBY_VERSION')" != "4.0.7" ]]; then
  echo "The benchmark requires host Ruby 4.0.7 so Rails runs directly beside Diamond." >&2
  exit 1
fi

podman run --rm \
  -e BUNDLE_APP_CONFIG=/workspace/.bundle \
  -e BUNDLE_PATH=/workspace/vendor/bundle \
  -v "${rails_dir}:/workspace:Z" \
  -w /workspace \
  "${ruby_image}" \
  bundle install --jobs 4 --retry 2

(
  cd "${rails_dir}"
  bundle config set --local path vendor/bundle
  bundle check
  bundle exec rails --version
  bundle exec puma --version
)
