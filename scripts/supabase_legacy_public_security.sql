-- Supabase public-schema hardening for the legacy tables.
-- Apply only to the explicitly approved Staging project first.
-- This script changes permissions/RLS only; it does not change business data.

BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.module_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operation_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operation_log_archives ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operation_log_archive_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_finance_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sh_junneng_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sh_junneng_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sh_junneng_close_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.strategy_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.strategy_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alert_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alert_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.calculated_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trading_days ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dv_week_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dv_data_points ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dv_import_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dv_change_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dv_integration_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dv_integrated_points ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    public.users,
    public.user_sessions,
    public.module_permissions,
    public.operation_logs,
    public.operation_log_archives,
    public.operation_log_archive_users,
    public.order_finance_progress,
    public.sh_junneng_trades,
    public.sh_junneng_positions,
    public.sh_junneng_close_trades,
    public.strategy_groups,
    public.strategy_positions,
    public.alert_settings,
    public.alert_history,
    public.calculated_data,
    public.daily_prices,
    public.trading_days,
    public.dv_week_keys,
    public.dv_data_points,
    public.dv_import_batches,
    public.dv_change_log,
    public.dv_integration_batches,
    public.dv_integrated_points
FROM anon, authenticated;

REVOKE ALL ON SEQUENCE
    public.users_id_seq,
    public.user_sessions_id_seq,
    public.module_permissions_id_seq,
    public.operation_logs_id_seq,
    public.operation_log_archives_id_seq,
    public.operation_log_archive_users_id_seq,
    public.order_finance_progress_id_seq,
    public.sh_junneng_trades_id_seq,
    public.sh_junneng_positions_id_seq,
    public.sh_junneng_close_trades_id_seq,
    public.strategy_groups_id_seq,
    public.strategy_positions_id_seq,
    public.alert_settings_id_seq,
    public.alert_history_id_seq,
    public.calculated_data_id_seq,
    public.daily_prices_id_seq,
    public.dv_week_keys_id_seq,
    public.dv_data_points_id_seq,
    public.dv_import_batches_id_seq,
    public.dv_change_log_id_seq,
    public.dv_integration_batches_id_seq,
    public.dv_integrated_points_id_seq
FROM anon, authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;

COMMIT;
