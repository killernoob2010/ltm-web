"""
数据可视化管理 — 卡粉 MVP。
Excel 解析、业务周计算、表需计算、导入预检/确认、数据查询与编辑、图表数据。
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from . import db

router = APIRouter()

# ── 常量 ──────────────────────────────────────────────────────────────
PRODUCT = "卡粉"
DV_PRODUCTS = ["卡粉", "纽曼粉", "麦克粉", "PB粉", "金布巴粉", "超特粉", "混合粉", "杨迪粉", "罗伊山粉", "巴西粗粉", "乌克兰精粉", "俄罗斯精粉", "IOC6"]
SHIPMENT_SHEET = "发运"
INVENTORY_SHEET = "卡粉库存"
SHIPMENT_DATE_COL = 2   # B 列
SHIPMENT_VAL_COL = 51   # AY 列
INVENTORY_DATE_COL = 1  # A 列
INVENTORY_VAL_COL = 2   # B 列

# ── 权限（自包含，避免循环导入 main.py）───────────────────────────────


async def dv_current_user(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def dv_require_edit(module_code: str, user: dict):
    if user["role"] == "管理员":
        return
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            "SELECT can_edit FROM module_permissions WHERE user_id = ? AND module_code = ?",
            (user["id"], module_code),
        ).fetchone()
    if not row or not row["can_edit"]:
        raise HTTPException(status_code=403, detail="没有编辑权限")


# ── 工具函数 ──────────────────────────────────────────────────────────


def _to_date(val: Any) -> Optional[date]:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None


def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


# ── 业务周计算 ────────────────────────────────────────────────────────
# 规则：周一为起点；1 月 1 日所在周为第 1 周


def compute_business_week(d: date) -> Dict[str, Any]:
    """计算业务周：周一起始，1月1日所在周为 W01。

    修正跨年规则：若该周包含下一年的 1 月 1 日，则归属下一年 W01。
    例：2024-12-30 → 2025 W01；2022-12-26 → 2023 W01。
    """
    weekday = d.weekday()  # 0=Mon … 6=Sun
    week_start = d - timedelta(days=weekday)
    week_end = week_start + timedelta(days=6)

    jan1 = date(d.year, 1, 1)
    jan1_weekday = jan1.weekday()
    jan1_week_start = jan1 - timedelta(days=jan1_weekday)

    if week_start < jan1_week_start:
        prev_jan1 = date(d.year - 1, 1, 1)
        prev_jan1_weekday = prev_jan1.weekday()
        prev_jan1_week_start = prev_jan1 - timedelta(days=prev_jan1_weekday)
        week_no = ((week_start - prev_jan1_week_start).days // 7) + 1
        result = {
            "year": d.year - 1,
            "week_no": week_no,
            "week_start_date": week_start.isoformat(),
            "week_end_date": week_end.isoformat(),
        }
    else:
        week_no = ((week_start - jan1_week_start).days // 7) + 1
        result = {
            "year": d.year,
            "week_no": week_no,
            "week_start_date": week_start.isoformat(),
            "week_end_date": week_end.isoformat(),
        }

    # 跨年修正：若该周包含下一年的 1 月 1 日，则归属下一年 W01
    next_jan1 = date(d.year + 1, 1, 1)
    if week_end >= next_jan1:
        result["year"] = d.year + 1
        result["week_no"] = 1

    return result

# ── Excel 解析 ────────────────────────────────────────────────────────


def parse_excel(file_path: str) -> Dict[str, List[Dict]]:
    """读取发运 sheet 和卡粉库存 sheet，返回 {shipment: [...], inventory: [...]}。"""
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True)
    result: Dict[str, List[Dict]] = {"shipment": [], "inventory": []}

    if SHIPMENT_SHEET in wb.sheetnames:
        ws = wb[SHIPMENT_SHEET]
        for row_idx in range(2, ws.max_row + 1):
            d = _to_date(ws.cell(row=row_idx, column=SHIPMENT_DATE_COL).value)
            v = _to_float(ws.cell(row=row_idx, column=SHIPMENT_VAL_COL).value)
            if d:
                result["shipment"].append({"date": d.isoformat(), "value": v})

    if INVENTORY_SHEET in wb.sheetnames:
        ws = wb[INVENTORY_SHEET]
        for row_idx in range(2, ws.max_row + 1):
            d = _to_date(ws.cell(row=row_idx, column=INVENTORY_DATE_COL).value)
            v = _to_float(ws.cell(row=row_idx, column=INVENTORY_VAL_COL).value)
            if d:
                result["inventory"].append({"date": d.isoformat(), "value": v})

    wb.close()
    return result


# ── 周匹配合并 ────────────────────────────────────────────────────────
# 同周且日期差 ≤ 4 天 → 共享 week_key


def _upsert_week_key(cur, year: int, week_no: int, week_start_date: str,
                     week_end_date: str, shipment_date: Optional[str],
                     inventory_date: Optional[str], display_date: str) -> int:
    sd = shipment_date or ""
    id_ = inventory_date or ""
    row = db._exec(
        cur,
        """SELECT id FROM dv_week_keys
           WHERE year = ? AND week_no = ? AND shipment_date = ? AND inventory_date = ?""",
        (year, week_no, sd, id_),
    ).fetchone()
    if row:
        return row["id"]
    return db._last_insert_id(
        cur,
        """INSERT INTO dv_week_keys
           (year, week_no, week_start_date, week_end_date, shipment_date, inventory_date, display_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (year, week_no, week_start_date, week_end_date, sd, id_, display_date),
    )


def match_and_merge_weeks(parsed: Dict[str, List[Dict]], conn) -> Dict[str, Any]:
    shipments = parsed["shipment"]
    inventories = parsed["inventory"]

    for row in shipments:
        row["_bw"] = compute_business_week(date.fromisoformat(row["date"]))
    for row in inventories:
        row["_bw"] = compute_business_week(date.fromisoformat(row["date"]))

    week_key_map: Dict[str, int] = {}
    week_keys: List[Dict] = []
    pairs: List[Dict] = []
    cur = conn.cursor()

    used_shipment: set = set()
    used_inventory: set = set()

    for si, s_row in enumerate(shipments):
        if si in used_shipment:
            continue
        s_date = date.fromisoformat(s_row["date"])
        s_bw = s_row["_bw"]
        for ii, i_row in enumerate(inventories):
            if ii in used_inventory:
                continue
            i_date = date.fromisoformat(i_row["date"])
            i_bw = i_row["_bw"]
            if s_bw["year"] == i_bw["year"] and s_bw["week_no"] == i_bw["week_no"]:
                if abs((s_date - i_date).days) <= 4:
                    used_shipment.add(si)
                    used_inventory.add(ii)
                    key_str = f"{s_bw['year']}_{s_bw['week_no']}_{s_row['date']}_{i_row['date']}"
                    wk_id = week_key_map.get(key_str)
                    if wk_id is None:
                        wk_id = _upsert_week_key(
                            cur, s_bw["year"], s_bw["week_no"],
                            s_bw["week_start_date"], s_bw["week_end_date"],
                            s_row["date"], i_row["date"], s_row["date"],
                        )
                        week_key_map[key_str] = wk_id
                        week_keys.append({
                            "id": wk_id, "year": s_bw["year"], "week_no": s_bw["week_no"],
                            "display_date": s_row["date"], "shipment_date": s_row["date"],
                            "inventory_date": i_row["date"],
                        })
                    pairs.append({"shipment_row": s_row, "inventory_row": i_row, "week_key_id": wk_id})
                    break

    for si, s_row in enumerate(shipments):
        if si in used_shipment:
            continue
        s_bw = s_row["_bw"]
        key_str = f"{s_bw['year']}_{s_bw['week_no']}_{s_row['date']}_none"
        wk_id = week_key_map.get(key_str)
        if wk_id is None:
            wk_id = _upsert_week_key(
                cur, s_bw["year"], s_bw["week_no"],
                s_bw["week_start_date"], s_bw["week_end_date"],
                s_row["date"], None, s_row["date"],
            )
            week_key_map[key_str] = wk_id
            week_keys.append({
                "id": wk_id, "year": s_bw["year"], "week_no": s_bw["week_no"],
                "display_date": s_row["date"], "shipment_date": s_row["date"],
                "inventory_date": None,
            })
        pairs.append({"shipment_row": s_row, "inventory_row": None, "week_key_id": wk_id})

    for ii, i_row in enumerate(inventories):
        if ii in used_inventory:
            continue
        i_bw = i_row["_bw"]
        key_str = f"{i_bw['year']}_{i_bw['week_no']}_none_{i_row['date']}"
        wk_id = week_key_map.get(key_str)
        if wk_id is None:
            wk_id = _upsert_week_key(
                cur, i_bw["year"], i_bw["week_no"],
                i_bw["week_start_date"], i_bw["week_end_date"],
                None, i_row["date"], i_row["date"],
            )
            week_key_map[key_str] = wk_id
            week_keys.append({
                "id": wk_id, "year": i_bw["year"], "week_no": i_bw["week_no"],
                "display_date": i_row["date"], "shipment_date": None,
                "inventory_date": i_row["date"],
            })
        pairs.append({"shipment_row": None, "inventory_row": i_row, "week_key_id": wk_id})

    return {"week_keys": week_keys, "pairs": pairs}


# ── 表需重算 ──────────────────────────────────────────────────────────
# 表需(t) = 发运(t-2) + 库存(t-1) - 库存(t)


def recalc_apparent_demand(conn) -> None:
    cur = conn.cursor()
    weeks = db._exec(
        cur,
        """SELECT wk.id, wk.year, wk.week_no, wk.display_date
           FROM dv_week_keys wk ORDER BY wk.display_date""",
    ).fetchall()
    if not weeks:
        return

    week_id_to_idx = {wk["id"]: i for i, wk in enumerate(weeks)}

    all_points = db._exec(
        cur,
        """SELECT id, week_key_id, metric_type, display_value, is_manual_override
           FROM dv_data_points
           WHERE product = ? AND metric_type IN ('shipment','inventory','apparent_demand')""",
        (PRODUCT,),
    ).fetchall()

    point_map: Dict[str, Dict[int, dict]] = {"shipment": {}, "inventory": {}, "apparent_demand": {}}
    for pt in all_points:
        point_map[pt["metric_type"]][pt["week_key_id"]] = dict(pt)

    for t_idx, wk in enumerate(weeks):
        wid = wk["id"]
        ad_pt = point_map["apparent_demand"].get(wid)
        if ad_pt and ad_pt["is_manual_override"]:
            continue

        shipment_val = 0.0
        inv_prev = 0.0
        inv_t = 0.0

        if t_idx >= 2:
            sp = point_map["shipment"].get(weeks[t_idx - 2]["id"])
            if sp and sp["display_value"] is not None:
                shipment_val = float(sp["display_value"])
        if t_idx >= 1:
            ip = point_map["inventory"].get(weeks[t_idx - 1]["id"])
            if ip and ip["display_value"] is not None:
                inv_prev = float(ip["display_value"])
        ip_t = point_map["inventory"].get(wid)
        if ip_t and ip_t["display_value"] is not None:
            inv_t = float(ip_t["display_value"])

        demand = shipment_val + inv_prev - inv_t

        is_missing = (
            (t_idx >= 2 and point_map["shipment"].get(weeks[t_idx - 2]["id"]) is None)
            or (t_idx >= 1 and point_map["inventory"].get(weeks[t_idx - 1]["id"]) is None)
            or point_map["inventory"].get(wid) is None
        )

        if ad_pt is None:
            db._exec(
                cur,
                """INSERT INTO dv_data_points
                   (week_key_id, product, metric_type, imported_value, calculated_value,
                    manual_value, display_value, is_manual_override, is_missing_filled,
                    source, created_at)
                   VALUES (?, ?, 'apparent_demand', NULL, ?, NULL, ?, 0, ?, '自动计算', CURRENT_TIMESTAMP)""",
                (wid, PRODUCT, demand, demand, 1 if is_missing else 0),
            )
        else:
            old_val = ad_pt["display_value"]
            db._exec(
                cur,
                """UPDATE dv_data_points
                   SET calculated_value = ?, display_value = ?, is_missing_filled = ?,
                       source = '自动计算', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (demand, demand, 1 if is_missing else 0, ad_pt["id"]),
            )
            if old_val != demand:
                db._exec(
                    cur,
                    """INSERT INTO dv_change_log
                       (data_point_id, old_value, new_value, operation_type, note, created_at)
                       VALUES (?, ?, ?, 'recalculation', '表需自动重算', CURRENT_TIMESTAMP)""",
                    (ad_pt["id"], old_val, demand),
                )


# ══════════════════════════════════════════════════════════════════════
# API 端点
# ══════════════════════════════════════════════════════════════════════

class ImportRequest(BaseModel):
    file_data: str  # base64 encoded
    file_name: str
    overwrite_manual_ids: List[int] = []

class ManualEditRequest(BaseModel):
    data_point_id: int
    new_value: float


def _now_expr() -> str:
    """返回 cross-db 的当前时间表达式。"""
    return "CURRENT_TIMESTAMP"



# ── GET /api/data-visualization/years ──────────────────────────────────

@router.get("/data-visualization/years")
async def get_years(user=Depends(dv_current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            "SELECT DISTINCT year FROM dv_week_keys ORDER BY year"
        ).fetchall()
    return {"years": [r["year"] for r in rows]}


# ── POST /api/data-visualization/import/preview ───────────────────────

@router.post("/data-visualization/import/preview")
async def import_preview(
    payload: ImportRequest,
    user=Depends(dv_current_user),
):
    dv_require_edit("data_visualization_data", user)

    import base64
    file_bytes = base64.b64decode(payload.file_data)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        parsed = parse_excel(tmp_path)
    finally:
        os.unlink(tmp_path)

    shipments = parsed["shipment"]
    inventories = parsed["inventory"]
    all_dates = sorted(set([r["date"] for r in shipments] + [r["date"] for r in inventories]))

    null_count = sum(1 for r in shipments if r["value"] is None) + sum(
        1 for r in inventories if r["value"] is None
    )

    with db.connect() as conn:
        merged = match_and_merge_weeks(parsed, conn)
        cur = conn.cursor()

        manual_protected = []
        history_changes = []
        for pair in merged["pairs"]:
            wk_id = pair["week_key_id"]
            for metric_type, row_key in [("shipment", "shipment_row"), ("inventory", "inventory_row")]:
                row = pair.get(row_key)
                if row is None or row["value"] is None:
                    continue
                existing = db._exec(
                    cur,
                    """SELECT id, display_value, is_manual_override
                       FROM dv_data_points
                       WHERE week_key_id = ? AND product = ? AND metric_type = ?""",
                    (wk_id, PRODUCT, metric_type),
                ).fetchone()
                if existing:
                    if existing["is_manual_override"]:
                        manual_protected.append({
                            "data_point_id": existing["id"],
                            "metric_type": metric_type,
                            "week_key_id": wk_id,
                            "date": row["date"],
                            "current_value": existing["display_value"],
                            "new_value": row["value"],
                        })
                    elif existing["display_value"] != row["value"]:
                        history_changes.append({
                            "metric_type": metric_type,
                            "week_key_id": wk_id,
                            "date": row["date"],
                            "current_value": existing["display_value"],
                            "new_value": row["value"],
                            "is_manual_protected": False,
                        })

    insert_count = sum(
        1 for p in merged["pairs"]
        if (p.get("shipment_row") and p["shipment_row"]["value"] is not None)
        or (p.get("inventory_row") and p["inventory_row"]["value"] is not None)
    )

    return {
        "file_name": payload.file_name,
        "metric_types": "inventory,shipment",
        "date_start": all_dates[0] if all_dates else "",
        "date_end": all_dates[-1] if all_dates else "",
        "total_rows": len(shipments) + len(inventories),
        "insert_count": insert_count,
        "overwrite_count": len(history_changes),
        "null_count": null_count,
        "error_count": 0,
        "manual_protected_count": len(manual_protected),
        "anomalies": [],
        "history_changes": history_changes,
        "manual_protected": manual_protected,
    }


# ── POST /api/data-visualization/import/commit ────────────────────────

@router.post("/data-visualization/import/commit")
async def import_commit(
    payload: ImportRequest,
    user=Depends(dv_current_user),
):
    dv_require_edit("data_visualization_data", user)

    import base64
    file_bytes = base64.b64decode(payload.file_data)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        parsed = parse_excel(tmp_path)
    finally:
        os.unlink(tmp_path)

    overwrite_ids = set(payload.overwrite_manual_ids)
    stats = {"insert_count": 0, "overwrite_count": 0, "error_count": 0, "manual_protected_count": 0}
    user_name = user["name"]

    with db.connect() as conn:
        cur = conn.cursor()
        merged = match_and_merge_weeks(parsed, conn)

        all_dates = sorted(
            set([r["date"] for r in parsed["shipment"]] + [r["date"] for r in parsed["inventory"]])
        )
        batch_id = db._last_insert_id(
            cur,
            """INSERT INTO dv_import_batches
               (file_name, metric_types, date_start, date_end, status, created_by)
               VALUES (?, 'inventory,shipment', ?, ?, 'processing', ?)""",
            (payload.file_name, all_dates[0] if all_dates else "",
             all_dates[-1] if all_dates else "", user_name),
        )

        for pair in merged["pairs"]:
            wk_id = pair["week_key_id"]
            for metric_type, row_key in [("shipment", "shipment_row"), ("inventory", "inventory_row")]:
                row = pair.get(row_key)
                if row is None or row["value"] is None:
                    continue
                value = row["value"]

                existing = db._exec(
                    cur,
                    """SELECT id, display_value, is_manual_override
                       FROM dv_data_points
                       WHERE week_key_id = ? AND product = ? AND metric_type = ?""",
                    (wk_id, PRODUCT, metric_type),
                ).fetchone()

                if existing:
                    dp_id = existing["id"]
                    if existing["is_manual_override"]:
                        if dp_id in overwrite_ids:
                            db._exec(
                                cur,
                                """UPDATE dv_data_points
                                   SET imported_value = ?, display_value = ?, is_manual_override = 0,
                                       manual_value = NULL, source = 'Excel导入覆盖人工修正',
                                       source_batch_id = ?, updated_by = ?
                                   WHERE id = ?""",
                                (value, value, batch_id, user_name, dp_id),
                            )
                            stats["overwrite_count"] += 1
                        else:
                            stats["manual_protected_count"] += 1
                            continue
                    else:
                        old_val = existing["display_value"]
                        db._exec(
                            cur,
                            """UPDATE dv_data_points
                               SET imported_value = ?, display_value = ?, source = '导入',
                                   source_batch_id = ?, updated_by = ?
                               WHERE id = ?""",
                            (value, value, batch_id, user_name, dp_id),
                        )
                        if old_val != value:
                            stats["overwrite_count"] += 1
                else:
                    db._last_insert_id(
                        cur,
                        """INSERT INTO dv_data_points
                           (week_key_id, product, metric_type, imported_value, display_value,
                            is_manual_override, is_missing_filled, source, source_batch_id, created_by)
                           VALUES (?, ?, ?, ?, ?, 0, 0, '导入', ?, ?)""",
                        (wk_id, PRODUCT, metric_type, value, value, batch_id, user_name),
                    )
                    stats["insert_count"] += 1

        db._exec(
            cur,
            """UPDATE dv_import_batches
               SET insert_count = ?, overwrite_count = ?, manual_protected_count = ?,
                   status = 'completed'
               WHERE id = ?""",
            (stats["insert_count"], stats["overwrite_count"], stats["manual_protected_count"], batch_id),
        )

        recalc_apparent_demand(conn)

    return {"ok": True, "batch_id": batch_id, "stats": stats}
# ── GET /api/data-visualization/table ─────────────────────────────────

@router.get("/data-visualization/table")
async def get_table(
    metric: str = Query(..., regex="^(inventory|shipment|apparent_demand)$"),
    years: str = "",
    user=Depends(dv_current_user),
):
    year_list: List[int] = []
    if years:
        for part in years.split(","):
            part = part.strip()
            if part:
                try:
                    year_list.append(int(part))
                except ValueError:
                    pass

    with db.connect() as conn:
        cur = conn.cursor()

        base_sql = """SELECT wk.display_date, wk.year, wk.week_no, dp.id, dp.product,
                        dp.display_value, dp.is_manual_override, dp.is_missing_filled,
                        dp.source, dp.updated_by, dp.updated_at
                 FROM dv_data_points dp
                 JOIN dv_week_keys wk ON wk.id = dp.week_key_id
                 WHERE dp.metric_type = ?"""

        params: List[Any] = [metric]
        if year_list:
            placeholders = ",".join("?" for _ in year_list)
            base_sql += f" AND wk.year IN ({placeholders})"
            params.extend(year_list)

        base_sql += " ORDER BY wk.display_date, dp.product"
        rows = db._exec(cur, base_sql, tuple(params)).fetchall()

    products = list(DV_PRODUCTS)
    week_map: Dict[str, Dict] = {}
    for r in rows:
        key = r["display_date"]
        if key not in week_map:
            week_map[key] = {
                "date": r["display_date"],
                "week": f"{r['year']} W{r['week_no']:02d}",
            }
            for p in products:
                week_map[key][p] = {"id": None, "value": None, "is_manual_override": False, "is_missing_filled": False, "source": None, "updated_by": None, "updated_at": None}
        week_map[key][r["product"]] = {
            "id": r["id"],
            "value": r["display_value"],
            "is_manual_override": bool(r["is_manual_override"]),
            "is_missing_filled": bool(r["is_missing_filled"]),
            "source": r["source"],
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"],
        }

    return {
        "metric": metric,
        "products": products,
        "data": list(week_map.values()),
    }


@router.put("/data-visualization/value")
async def update_value(
    payload: ManualEditRequest,
    user=Depends(dv_current_user),
):
    dv_require_edit("data_visualization_data", user)

    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(
            cur,
            """SELECT id, display_value, metric_type
               FROM dv_data_points WHERE id = ?""",
            (payload.data_point_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="数据点不存在")

        old_val = existing["display_value"]
        metric_type = existing["metric_type"]
        user_name = user["name"]

        db._exec(
            cur,
            """UPDATE dv_data_points
               SET manual_value = ?, display_value = ?, is_manual_override = 1,
                   source = '手工修改', updated_by = ?
               WHERE id = ?""",
            (payload.new_value, payload.new_value, user_name, payload.data_point_id),
        )

        if metric_type in ("shipment", "inventory"):
            recalc_apparent_demand(conn)

    return {"ok": True, "data_point_id": payload.data_point_id, "new_value": payload.new_value}


# ── GET /api/data-visualization/chart ─────────────────────────────────

@router.get("/data-visualization/chart")
async def get_chart(
    metric: str = Query(..., regex="^(inventory|shipment|apparent_demand)$"),
    years: str = "",
    products: str = "",
    user=Depends(dv_current_user),
):
    year_list: List[int] = []
    if years:
        for part in years.split(","):
            part = part.strip()
            if part:
                try:
                    year_list.append(int(part))
                except ValueError:
                    pass

    product_list: List[str] = []
    if products:
        product_list = [p.strip() for p in products.split(",") if p.strip()]

    with db.connect() as conn:
        cur = conn.cursor()

        params: List[Any] = [metric]
        sql = """SELECT wk.year, wk.week_no, wk.display_date, dp.product,
                        dp.display_value, dp.is_manual_override, dp.is_missing_filled
                 FROM dv_data_points dp
                 JOIN dv_week_keys wk ON wk.id = dp.week_key_id
                 WHERE dp.metric_type = ?"""

        if product_list:
            placeholders_p = ",".join("?" for _ in product_list)
            sql += f" AND dp.product IN ({placeholders_p})"
            params.extend(product_list)

        if year_list:
            placeholders_y = ",".join("?" for _ in year_list)
            sql += f" AND wk.year IN ({placeholders_y})"
            params.extend(year_list)

        sql += " ORDER BY dp.product, wk.year, wk.week_no"
        rows = db._exec(cur, sql, tuple(params)).fetchall()

    by_product_year: Dict[str, Dict[int, List[Dict]]] = {}
    for r in rows:
        prod = r["product"]
        yr = r["year"]
        if prod not in by_product_year:
            by_product_year[prod] = {}
        if yr not in by_product_year[prod]:
            by_product_year[prod][yr] = []
        by_product_year[prod][yr].append({
            "week_no": r["week_no"],
            "display_date": r["display_date"],
            "value": r["display_value"],
            "is_manual_override": bool(r["is_manual_override"]),
            "is_missing_filled": bool(r["is_missing_filled"]),
        })

    # Sort by year within each product
    result: Dict[str, Dict[str, List[Dict]]] = {}
    for prod, by_year in by_product_year.items():
        result[prod] = {str(k): v for k, v in sorted(by_year.items())}

    return {"metric": metric, "series": result}


# ── GET /api/data-visualization/import-batches ────────────────────────

@router.get("/data-visualization/import-batches")
async def get_import_batches(user=Depends(dv_current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            """SELECT id, file_name, metric_types, date_start, date_end,
                      insert_count, overwrite_count, error_count, manual_protected_count,
                      status, created_by, created_at
        FROM dv_import_batches ORDER BY created_at DESC LIMIT 50""",
        ).fetchall()
    return {"batches": [_row_to_dict(r) for r in rows]}


# ── Seed test data ─────────────────────────────────────────────────

def seed_dv_data():
    """如果 DV 表为空，插入 2023-2026 共 4 年测试数据（仅卡粉）。
    
    使用 compute_business_week() 正确计算跨年周所属年份。
    """
    with db.connect() as conn:
        cur = conn.cursor()
        existing_count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_week_keys").fetchone()["c"]
        if existing_count > 0:
            old_seed = db._exec(
                cur, "SELECT COUNT(*) AS c FROM dv_data_points WHERE source = '种子数据'"
            ).fetchone()["c"]
            if old_seed > 0:
                print("[seed_dv_data] 检测到旧种子数据，清除后重新生成...")
                db._exec(cur, "DELETE FROM dv_change_log")
                db._exec(cur, "DELETE FROM dv_data_points")
                db._exec(cur, "DELETE FROM dv_week_keys")
                db._exec(cur, "DELETE FROM dv_import_batches")
            else:
                return

        import random
        random.seed(42)

        week_ids: List[int] = []
        years_to_seed = [2023, 2024, 2025, 2026]

        for yr in years_to_seed:
            jan1 = date(yr, 1, 1)
            w01_start = jan1 - timedelta(days=jan1.weekday())

            for wn in range(1, 53):
                ws = w01_start + timedelta(weeks=wn - 1)
                we = ws + timedelta(days=6)

                bw = compute_business_week(ws)
                correct_year = bw["year"]
                correct_wn = bw["week_no"]

                db._exec(
                    cur,
                    """INSERT INTO dv_week_keys
                       (year, week_no, week_start_date, week_end_date, display_date)
                       VALUES (?, ?, ?, ?, ?)""",
                    (correct_year, correct_wn, ws.isoformat(), we.isoformat(), ws.isoformat()),
                )
                wk = db._exec(
                    cur,
                    "SELECT id FROM dv_week_keys WHERE year=? AND week_no=? AND week_start_date=?",
                    (correct_year, correct_wn, ws.isoformat()),
                ).fetchone()
                week_ids.append(wk["id"])

        total_weeks = len(week_ids)
        print(f"[seed_dv_data] 已插入 {len(years_to_seed)} 年 x 52 周 = {total_weeks} 个业务周")

        base = 1200
        inv = [float(base + i * 5 + random.randint(-80, 80)) for i in range(total_weeks)]
        ship = [float(500 + random.randint(-60, 60)) for _ in range(total_weeks)]
        ad = [0.0] * total_weeks
        for t in range(2, total_weeks):
            ad[t] = float(ship[t - 2] + inv[t - 1] - inv[t])
        ad[0] = 0.0
        ad[1] = 0.0

        for i, wk_id in enumerate(week_ids):
            for metric, values in [("inventory", inv), ("shipment", ship), ("apparent_demand", ad)]:
                val = values[i]
                db._exec(
                    cur,
                    """INSERT INTO dv_data_points
                       (week_key_id, product, metric_type, imported_value, calculated_value,
                        display_value, source, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (wk_id, PRODUCT, metric, val, val, val, "种子数据", "system"),
                )

        print(f"[seed_dv_data] 已完成：{total_weeks} 周 x 3 指标 = {total_weeks * 3} 条测试数据")
