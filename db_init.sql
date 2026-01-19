-- =====================================================
-- DB INIT FINAL (BOT + WEB, SAFE MIGRATION)
-- =====================================================

-- =========================
-- CUSTOMERS
-- =========================
CREATE TABLE IF NOT EXISTS customers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT,
  created_at TIMESTAMP DEFAULT now()
);

ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS name TEXT;

ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS phone TEXT;

ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now();


-- =========================
-- PRODUCTS
-- =========================
CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  qty INTEGER NOT NULL DEFAULT 0,
  cost_price BIGINT NOT NULL DEFAULT 0,
  cost_price_usd NUMERIC(12
