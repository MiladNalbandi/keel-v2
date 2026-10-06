-- A fixture schema for the Map's database diagram: ~25 tables, many foreign keys (engine + web tests, screenshots).

create table tenant (
    id          uuid primary key,
    slug        varchar(64)  not null unique,
    name        varchar(120) not null,
    created_at  timestamp with time zone not null default now()
);

create table app_user (
    id            uuid primary key,
    tenant_id     uuid not null references tenant (id) on delete cascade,
    email         varchar(254) not null,
    display_name  varchar(120),
    manager_id    uuid references app_user (id) on delete set null,
    created_at    timestamp with time zone not null default now(),
    constraint uq_app_user_email unique (tenant_id, email)
);

create table role (
    id    serial primary key,
    code  varchar(32) not null unique,
    label varchar(80) not null
);

create table user_role (
    user_id  uuid    not null references app_user (id) on delete cascade,
    role_id  integer not null references role (id),
    granted  timestamp with time zone not null default now(),
    primary key (user_id, role_id)
);

create table api_token (
    id         uuid primary key,
    user_id    uuid not null references app_user (id) on delete cascade,
    digest     char(64) not null unique,
    expires_at timestamp with time zone,
    revoked    boolean not null default false
);
create index ix_api_token_user on api_token (user_id);

create table address (
    id         bigserial primary key,
    user_id    uuid not null references app_user (id) on delete cascade,
    line1      varchar(200) not null,
    line2      varchar(200),
    city       varchar(100) not null,
    postcode   varchar(20)  not null,
    country    char(2)      not null
);
