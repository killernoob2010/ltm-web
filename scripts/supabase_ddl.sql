-- ============================================================
-- PostgreSQL DDL for Supabase
-- 转换自 SQLite, 按外键依赖顺序排列
-- 日期: 2026-06-15
-- ============================================================

-- 1. users
DROP TABLE IF EXISTS module_permissions;
DROP TABLE IF EXISTS user_sessions;
DROP TABLE IF EXISTS operation_logs;
DROP TABLE IF EXISTS sh_junneng_trades;
DROP TABLE IF EXISTS strategy_positions;
DROP TABLE IF EXISTS strategy_groups;
DROP TABLE IF EXISTS daily_prices;
DROP TABLE IF EXISTS calculated_data;
DROP TABLE IF EXISTS alert_history;
DROP TABLE IF EXISTS alert_settings;
DROP TABLE IF EXISTS trading_days;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    department TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '启用',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 2. module_permissions
CREATE TABLE module_permissions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    module_code TEXT NOT NULL,
    can_view INTEGER NOT NULL DEFAULT 1,
    can_edit INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, module_code),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 3. user_sessions
CREATE TABLE user_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token TEXT NOT NULL UNIQUE,
    login_time TEXT DEFAULT CURRENT_TIMESTAMP,
    last_activity TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT '活跃',
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 4. operation_logs
CREATE TABLE operation_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    module_code TEXT,
    entity_type TEXT,
    entity_id INTEGER,
    operation_type TEXT NOT NULL,
    description TEXT NOT NULL,
    before_data TEXT,
    after_data TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 5. alert_settings
CREATE TABLE alert_settings (
    id SERIAL PRIMARY KEY,
    info_type TEXT NOT NULL,
    contract_year TEXT NOT NULL DEFAULT '2026',
    contract_month TEXT NOT NULL,
    alert_value DOUBLE PRECISION NOT NULL,
    direction TEXT NOT NULL DEFAULT 'above',
    status TEXT NOT NULL DEFAULT 'enabled',
    creator TEXT,
    reminder_users TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 6. alert_history
CREATE TABLE alert_history (
    id SERIAL PRIMARY KEY,
    alert_id INTEGER,
    alert_time TEXT DEFAULT CURRENT_TIMESTAMP,
    current_value DOUBLE PRECISION,
    alert_value DOUBLE PRECISION,
    direction TEXT,
    status TEXT NOT NULL DEFAULT 'unread',
    FOREIGN KEY (alert_id) REFERENCES alert_settings(id)
);

-- 7. calculated_data
CREATE TABLE calculated_data (
    id SERIAL PRIMARY KEY,
    info_type TEXT NOT NULL,
    year INTEGER NOT NULL,
    month TEXT NOT NULL,
    calc_date TEXT NOT NULL,
    t_1_value DOUBLE PRECISION,
    t_2_value DOUBLE PRECISION,
    mean_value DOUBLE PRECISION,
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    std_value DOUBLE PRECISION,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(info_type, year, month, calc_date)
);

-- 8. daily_prices
CREATE TABLE daily_prices (
    id SERIAL PRIMARY KEY,
    info_type TEXT NOT NULL,
    contract_code TEXT NOT NULL,
    calc_date TEXT NOT NULL,
    close_price DOUBLE PRECISION,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(info_type, contract_code, calc_date)
);

-- 9. trading_days
CREATE TABLE trading_days (
    date TEXT PRIMARY KEY
);

-- 10. strategy_groups
CREATE TABLE strategy_groups (
    id SERIAL PRIMARY KEY,
    group_name TEXT NOT NULL UNIQUE,
    created_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 11. strategy_positions
CREATE TABLE strategy_positions (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL,
    variety TEXT NOT NULL,
    variety_name TEXT,
    direction TEXT NOT NULL,
    open_price DOUBLE PRECISION NOT NULL,
    quantity INTEGER NOT NULL,
    multiplier INTEGER DEFAULT 100,
    contract TEXT DEFAULT '',
    current_price DOUBLE PRECISION,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES strategy_groups(id)
);

CREATE TABLE IF NOT EXISTS iron_ore_basis_results (
    id SERIAL PRIMARY KEY,
    business_key TEXT NOT NULL UNIQUE,
    business_date TEXT NOT NULL,
    business_week INTEGER NOT NULL,
    week_label TEXT NOT NULL,
    business_year INTEGER NOT NULL,
    port TEXT NOT NULL,
    product TEXT NOT NULL,
    wet_spot_price DOUBLE PRECISION NOT NULL,
    quality_adjustment DOUBLE PRECISION NOT NULL,
    brand_adjustment DOUBLE PRECISION NOT NULL,
    standardized_spot_price DOUBLE PRECISION NOT NULL,
    futures_series TEXT NOT NULL DEFAULT 'I0',
    futures_close DOUBLE PRECISION NOT NULL,
    basis DOUBLE PRECISION NOT NULL,
    data_status TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    parameter_version TEXT NOT NULL,
    source_workbook_name TEXT NOT NULL,
    source_workbook_sha256 TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(business_date, port, product, rule_version, parameter_version)
);

CREATE TABLE IF NOT EXISTS iron_ore_basis_details (
    id SERIAL PRIMARY KEY,
    result_id INTEGER NOT NULL UNIQUE REFERENCES iron_ore_basis_results(id) ON DELETE CASCADE,
    business_key TEXT NOT NULL UNIQUE,
    business_date TEXT NOT NULL,
    week_label TEXT NOT NULL,
    business_year INTEGER NOT NULL,
    port TEXT NOT NULL,
    product TEXT NOT NULL,
    ebc_indicator_code TEXT,
    ebc_indicator_name TEXT,
    ebc_price_fe DOUBLE PRECISION,
    wet_spot_price DOUBLE PRECISION NOT NULL,
    parameter_year INTEGER NOT NULL,
    parameter_type TEXT NOT NULL,
    fe DOUBLE PRECISION NOT NULL,
    sio2 DOUBLE PRECISION NOT NULL,
    al2o3 DOUBLE PRECISION NOT NULL,
    phosphorus DOUBLE PRECISION NOT NULL,
    sulfur DOUBLE PRECISION NOT NULL,
    h2o DOUBLE PRECISION NOT NULL,
    sulfur_defaulted INTEGER NOT NULL DEFAULT 0,
    price_proxy_indicator TEXT,
    price_parameter_spec_diff INTEGER NOT NULL DEFAULT 0,
    fe_adjustment_x DOUBLE PRECISION NOT NULL,
    brand_adjustment DOUBLE PRECISION NOT NULL,
    futures_series TEXT NOT NULL,
    futures_close DOUBLE PRECISION NOT NULL,
    fe_adjustment DOUBLE PRECISION NOT NULL,
    sio2_adjustment DOUBLE PRECISION NOT NULL,
    al2o3_adjustment DOUBLE PRECISION NOT NULL,
    phosphorus_adjustment DOUBLE PRECISION NOT NULL,
    sulfur_adjustment DOUBLE PRECISION NOT NULL,
    quality_adjustment DOUBLE PRECISION NOT NULL,
    dry_spot_price DOUBLE PRECISION NOT NULL,
    standardized_spot_price DOUBLE PRECISION NOT NULL,
    basis DOUBLE PRECISION NOT NULL,
    data_status TEXT NOT NULL,
    note TEXT,
    rule_version TEXT NOT NULL,
    parameter_source TEXT NOT NULL,
    parameter_version TEXT NOT NULL,
    ebc_original_port TEXT,
    source_workbook_name TEXT NOT NULL,
    source_workbook_sha256 TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_results_query
ON iron_ore_basis_results(business_year, port, product, business_date);
CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_results_optimal
ON iron_ore_basis_results(business_year, data_status, business_date, basis);
CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_details_result
ON iron_ore_basis_details(result_id);

ALTER TABLE iron_ore_basis_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE iron_ore_basis_details ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE iron_ore_basis_results, iron_ore_basis_details FROM anon, authenticated;
REVOKE ALL ON SEQUENCE iron_ore_basis_results_id_seq, iron_ore_basis_details_id_seq FROM anon, authenticated;

-- 12. sh_junneng_trades
CREATE TABLE sh_junneng_trades (
    id SERIAL PRIMARY KEY,
    contract_month TEXT NOT NULL,
    direction TEXT NOT NULL,
    open_price DOUBLE PRECISION NOT NULL,
    close_price DOUBLE PRECISION,
    current_price DOUBLE PRECISION,
    trade_quantity INTEGER NOT NULL,
    hold_quantity INTEGER NOT NULL,
    open_fee DOUBLE PRECISION NOT NULL DEFAULT 0,
    close_fee DOUBLE PRECISION NOT NULL DEFAULT 0,
    profit DOUBLE PRECISION,
    open_date TEXT NOT NULL,
    close_date TEXT,
    status TEXT NOT NULL DEFAULT '未平仓',
    is_closed INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX idx_calculated_data_lookup ON calculated_data(info_type, year, month, calc_date);
CREATE INDEX idx_daily_prices_lookup ON daily_prices(info_type, contract_code, calc_date);
CREATE INDEX idx_sh_junneng_contract ON sh_junneng_trades(contract_month);
CREATE INDEX idx_sh_junneng_open_date ON sh_junneng_trades(open_date);
CREATE INDEX idx_sh_junneng_close_date ON sh_junneng_trades(close_date);
CREATE INDEX idx_sh_junneng_status ON sh_junneng_trades(status);

-- ============================================================
-- 重置序列（SERIAL 列默认从 1 开始，数据迁移后会调整）
-- ============================================================
