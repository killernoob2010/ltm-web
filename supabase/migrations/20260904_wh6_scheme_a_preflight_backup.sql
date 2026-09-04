-- Immutable-in-place logical snapshot for the WH6 scheme A Staging migration.
-- Target: LTM WEB STAGING only. Keep this schema until Staging acceptance and
-- rollback review are complete; it is not part of any client-facing API.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_namespace WHERE nspname = 'wh6_scheme_a_backup_20260904'
  ) THEN
    RAISE EXCEPTION 'WH6 preflight backup schema already exists; refusing to overwrite it';
  END IF;
END $$;

CREATE SCHEMA wh6_scheme_a_backup_20260904;
REVOKE ALL ON SCHEMA wh6_scheme_a_backup_20260904 FROM PUBLIC, anon, authenticated;

CREATE TABLE wh6_scheme_a_backup_20260904.trading_accounts AS
SELECT * FROM public.trading_accounts
WHERE account_code = 'hongyuan_futures';

CREATE TABLE wh6_scheme_a_backup_20260904.trading_import_batches AS
SELECT b.*
FROM public.trading_import_batches b
JOIN public.trading_accounts a ON a.id = b.account_id
WHERE a.account_code = 'hongyuan_futures';

CREATE TABLE wh6_scheme_a_backup_20260904.trading_collector_pairing_codes AS
SELECT p.*
FROM public.trading_collector_pairing_codes p
JOIN public.trading_accounts a ON a.id = p.account_id
WHERE a.account_code = 'hongyuan_futures';

CREATE TABLE wh6_scheme_a_backup_20260904.trading_collector_devices AS
SELECT d.*
FROM public.trading_collector_devices d
JOIN public.trading_accounts a ON a.id = d.account_id
WHERE a.account_code = 'hongyuan_futures';

CREATE TABLE wh6_scheme_a_backup_20260904.trading_intraday_fill_observations AS
SELECT o.*
FROM public.trading_intraday_fill_observations o
JOIN public.trading_accounts a ON a.id = o.account_id
WHERE a.account_code = 'hongyuan_futures';

CREATE TABLE wh6_scheme_a_backup_20260904.trading_intraday_fills AS
SELECT f.*
FROM public.trading_intraday_fills f
JOIN public.trading_accounts a ON a.id = f.account_id
WHERE a.account_code = 'hongyuan_futures';

CREATE TABLE wh6_scheme_a_backup_20260904.trading_fact_identities AS
SELECT i.*
FROM public.trading_fact_identities i
JOIN public.trading_accounts a ON a.id = i.account_id
WHERE a.account_code = 'hongyuan_futures';

CREATE TABLE wh6_scheme_a_backup_20260904.trading_trade_facts AS
SELECT tf.*
FROM public.trading_trade_facts tf
JOIN wh6_scheme_a_backup_20260904.trading_fact_identities i ON i.id = tf.identity_id;

CREATE TABLE wh6_scheme_a_backup_20260904.trading_source_rows AS
SELECT sr.*
FROM public.trading_source_rows sr
JOIN wh6_scheme_a_backup_20260904.trading_import_batches b ON b.id = sr.batch_id;

CREATE TABLE wh6_scheme_a_backup_20260904.manifest (
  table_name text PRIMARY KEY,
  captured_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  row_count bigint NOT NULL,
  content_md5 text NOT NULL
);

INSERT INTO wh6_scheme_a_backup_20260904.manifest (table_name, row_count, content_md5)
SELECT 'trading_accounts', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_accounts t
UNION ALL
SELECT 'trading_import_batches', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_import_batches t
UNION ALL
SELECT 'trading_collector_pairing_codes', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_collector_pairing_codes t
UNION ALL
SELECT 'trading_collector_devices', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_collector_devices t
UNION ALL
SELECT 'trading_intraday_fill_observations', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_intraday_fill_observations t
UNION ALL
SELECT 'trading_intraday_fills', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_intraday_fills t
UNION ALL
SELECT 'trading_fact_identities', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_fact_identities t
UNION ALL
SELECT 'trading_trade_facts', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_trade_facts t
UNION ALL
SELECT 'trading_source_rows', count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\n' ORDER BY t.id), ''))
FROM wh6_scheme_a_backup_20260904.trading_source_rows t;

ALTER TABLE wh6_scheme_a_backup_20260904.trading_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.trading_import_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.trading_collector_pairing_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.trading_collector_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.trading_intraday_fill_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.trading_intraday_fills ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.trading_fact_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.trading_trade_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.trading_source_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE wh6_scheme_a_backup_20260904.manifest ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA wh6_scheme_a_backup_20260904 FROM PUBLIC, anon, authenticated;
