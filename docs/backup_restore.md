# 备份与恢复说明

本项目继续使用 Render + Supabase。生产或 staging 执行任何 DB migration 前，先完成一次备份，并优先在 staging 数据库验证恢复。

## 备份

脚本读取 `DATABASE_URL`，不会打印连接串、密码或 token。

```bash
DATABASE_URL="postgres://..." python3 scripts/backup_database.py --mode all --output-dir backups
```

模式：

- `full`：生成 `pg_dump --format=custom` 完整 dump。
- `schema`：生成 schema-only SQL。
- `csv`：导出核心业务表 CSV。
- `all`：同时执行以上三类备份。

## 恢复到临时数据库

不要直接恢复到生产库。先创建临时 Supabase/Staging 数据库，然后执行：

```bash
createdb ltm_restore_check
pg_restore --no-owner --dbname ltm_restore_check backups/<timestamp>/full.dump
```

schema-only 检查：

```bash
psql "$TEMP_DATABASE_URL" -f backups/<timestamp>/schema.sql
```

## 核心表行数校验

恢复后对比核心表行数：

```sql
SELECT 'users' AS table_name, COUNT(*) FROM users
UNION ALL SELECT 'module_permissions', COUNT(*) FROM module_permissions
UNION ALL SELECT 'operation_logs', COUNT(*) FROM operation_logs
UNION ALL SELECT 'alert_settings', COUNT(*) FROM alert_settings
UNION ALL SELECT 'calculated_data', COUNT(*) FROM calculated_data
UNION ALL SELECT 'dv_integrated_points', COUNT(*) FROM dv_integrated_points
UNION ALL SELECT 'order_finance_progress', COUNT(*) FROM order_finance_progress
UNION ALL SELECT 'sh_junneng_positions', COUNT(*) FROM sh_junneng_positions
UNION ALL SELECT 'sh_junneng_close_trades', COUNT(*) FROM sh_junneng_close_trades;
```

## Render 配置说明

`render.yaml` 不管理套餐 plan。当前服务套餐以 Render Dashboard 为准，避免 Blueprint 同步时把 Standard 服务降回 Free。
