Rails.application.routes.draw do
  get "up" => "rails/health#show", as: :rails_health_check
  post "api/v1/page" => "issues#page"
  post "api/v1/read" => "issues#read"
  post "api/v1/create" => "issues#create"
  post "api/v1/update" => "issues#update"
end
