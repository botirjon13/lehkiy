@@ -28,25 +28,34 @@ 
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
ADD COLUMN IF NOT EXISTS usd_rate NUMERIC(12,2);

CREATE TABLE IF NOT EXISTS web_users (
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'seller',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT now()
);
