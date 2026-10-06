CREATE TABLE customers (
  id     INTEGER PRIMARY KEY,
  email  TEXT NOT NULL UNIQUE,
  name   TEXT NOT NULL
);
CREATE TABLE carts (
  id           INTEGER PRIMARY KEY,
  customer_id  INTEGER NOT NULL REFERENCES customers(id),
  created_at   TEXT NOT NULL
);
CREATE TABLE cart_items (
  cart_id     INTEGER NOT NULL REFERENCES carts(id),
  product_id  INTEGER NOT NULL REFERENCES products(id),
  quantity    INTEGER NOT NULL,
  PRIMARY KEY (cart_id, product_id)
);
