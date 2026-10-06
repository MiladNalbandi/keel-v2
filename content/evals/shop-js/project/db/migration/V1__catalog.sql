CREATE TABLE categories (
  id    INTEGER PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);
CREATE TABLE products (
  id           INTEGER PRIMARY KEY,
  category_id  INTEGER NOT NULL REFERENCES categories(id),
  sku          TEXT NOT NULL UNIQUE,
  name         TEXT NOT NULL,
  price_cents  INTEGER NOT NULL,
  stock        INTEGER NOT NULL DEFAULT 0
);
