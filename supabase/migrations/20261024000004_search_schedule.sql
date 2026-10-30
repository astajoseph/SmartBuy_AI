-- When seller discovery last ran a web search for each product (Brave budget: products take turns).
set search_path to smartbuy, public;

alter table products add column last_web_search_at timestamptz;
