create schema billing;

create table billing.invoice (
    id          bigserial primary key,
    order_id    bigint not null unique references customer_order (id),
    number      varchar(32) not null unique,
    issued_at   date not null,
    total_cents integer not null
);

create table billing.payment (
    id          bigserial primary key,
    invoice_id  bigint not null references billing.invoice (id),
    provider    varchar(32) not null,
    amount_cents integer not null,
    paid_at     timestamp with time zone
);

create table billing.refund (
    id          bigserial primary key,
    payment_id  bigint not null references billing.payment (id),
    reason      text,
    amount_cents integer not null
);
