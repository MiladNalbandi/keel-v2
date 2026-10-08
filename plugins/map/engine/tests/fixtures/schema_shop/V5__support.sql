create table review (
    id          bigserial primary key,
    product_id  bigint not null references product (id) on delete cascade,
    user_id     uuid   not null references app_user (id),
    rating      smallint not null check (rating between 1 and 5),
    body        text,
    created_at  timestamp with time zone not null default now()
);

create table support_ticket (
    id          bigserial primary key,
    tenant_id   uuid not null references tenant (id),
    opened_by   uuid not null references app_user (id),
    assignee_id uuid references app_user (id),
    order_id    bigint references customer_order (id),
    subject     varchar(200) not null,
    status      varchar(16) not null default 'open'
);

create table audit_log (
    id         bigserial primary key,
    at         timestamp with time zone not null default now(),
    actor      varchar(254),
    action     varchar(64) not null,
    payload    jsonb
);

create table feature_flag (
    key        varchar(64) primary key,
    enabled    boolean not null default false,
    note       text
);

alter table product add column weight_grams integer;
alter table app_user rename column display_name to full_name;

create view order_totals as
  select o.id, o.user_id, o.total_cents, count(l.line_no) as lines
  from customer_order o join order_line l on l.order_id = o.id
  group by o.id, o.user_id, o.total_cents;
