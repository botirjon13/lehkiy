CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,      -- name in latin
  qty INTEGER NOT NULL DEFAULT 0,
  cost_price BIGINT NOT NULL,    -- optovik narxi (so'm)
  cost_price_usd NUMERIC(12,2), -- optovik narxi (USD)
  usd_rate NUMERIC(12,2); ---- optovik narxi
  suggest_price BIGINT,    -- taxminiy sotish narxi (so'm)
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customers (
  id SERIAL PRIMARY KEY,
  name TEXT,
  phone TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sales (
  id SERIAL PRIMARY KEY,
  customer_id INTEGER REFERENCES customers(id),
  total_amount BIGINT NOT NULL,
  payment_type TEXT, -- 'naqd' or 'qarz'
  seller_phone TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sale_items (
  id SERIAL PRIMARY KEY,
  sale_id INTEGER REFERENCES sales(id),
  product_id INTEGER REFERENCES products(id),
  name TEXT,
  qty INTEGER,
  price BIGINT, -- price per item at sale time
  total BIGINT
);

CREATE TABLE IF NOT EXISTS debts (
  id SERIAL PRIMARY KEY,
  customer_id INTEGER REFERENCES customers(id),
  sale_id INTEGER REFERENCES sales(id),
  amount BIGINT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_carts (
  user_id BIGINT PRIMARY KEY,
  data JSONB, -- {items: [{product_id, name, qty, price}], customer_id:..., temp:...}
  updated_at TIMESTAMP DEFAULT now()
);

ALTER TABLE products
ADD COLUMN cost_price_usd NUMERIC(12,2);

