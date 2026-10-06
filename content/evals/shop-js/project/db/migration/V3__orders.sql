CREATE TABLE orders (
  id           INTEGER PRIMARY KEY,
  customer_id  INTEGER NOT NULL REFERENCES customers(id),
  total_cents  INTEGER NOT NULL,
  status       TEXT NOT NULL DEFAULT 'new',
  placed_at    TEXT NOT NULL
);
CREATE TABLE order_lines (
  order_id     INTEGER NOT NULL REFERENCES orders(id),
  product_id   INTEGER NOT NULL REFERENCES products(id),
  quantity     INTEGER NOT NULL,
  price_cents  INTEGER NOT NULL,
  PRIMARY KEY (order_id, product_id)
);
CREATE TABLE payments (
  id          INTEGER PRIMARY KEY,
  order_id    INTEGER NOT NULL REFERENCES orders(id),
  amount_cents INTEGER NOT NULL,
  paid_at     TEXT
);
CREATE INDEX orders_customer ON orders(customer_id);
