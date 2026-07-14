"""Read-only management and display APIs for iron-ore spot-futures basis data."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from . import db
from .data_visualization import dv_current_user, dv_require_view


router = APIRouter()

PORT_ORDER = [
    "日照港", "青岛港", "岚山港", "连云港", "江阴港", "太仓港", "京唐港", "曹妃甸港",
]
PRODUCT_ORDER = [
    "卡拉拉精粉", "卡拉加斯粉", "乌克兰精粉", "昆巴粉", "BRBF", "纽曼粉", "PB粉",
    "IOC6", "麦克粉", "罗伊山粉", "金布巴粉", "SP10粉", "FMG混合粉", "杨迪粉", "超特粉",
]


def _csv_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _ordered(values: list[Any], preferred: list[Any]) -> list[Any]:
    available = set(values)
    output = [item for item in preferred if item in available]
    output.extend(sorted(available - set(output)))
    return output


def _filters_for_permission(resource: str, user: dict) -> dict[str, Any]:
    dv_require_view(resource, user)
    with db.connect() as conn:
        cur = conn.cursor()
        years = [
            int(row["business_year"])
            for row in db._exec(
                cur,
                "SELECT DISTINCT business_year FROM iron_ore_basis_results ORDER BY business_year",
            ).fetchall()
        ]
        products = [
            row["product"]
            for row in db._exec(
                cur,
                "SELECT DISTINCT product FROM iron_ore_basis_results",
            ).fetchall()
        ]
        ports = [
            row["port"]
            for row in db._exec(
                cur,
                "SELECT DISTINCT port FROM iron_ore_basis_results",
            ).fetchall()
        ]
        latest_row = db._exec(
            cur,
            "SELECT MAX(business_date) AS latest_data_date FROM iron_ore_basis_results",
        ).fetchone()
    return {
        "years": years,
        "products": _ordered(products, PRODUCT_ORDER),
        "ports": _ordered(ports, PORT_ORDER),
        "latest_data_date": latest_row["latest_data_date"],
    }


@router.get("/iron-ore-basis/management/filters")
async def management_filters(user=Depends(dv_current_user)):
    return _filters_for_permission("data_visualization.data", user)


@router.get("/iron-ore-basis/display/filters")
async def display_filters(user=Depends(dv_current_user)):
    filters = _filters_for_permission("data_visualization.display", user)
    return {
        "years": filters["years"],
        "products": filters["products"],
        "ports": filters["ports"],
        "latest_data_date": filters["latest_data_date"],
    }


def _where_filters(
    years: list[str],
    products: list[str],
    ports: list[str],
) -> tuple[str, list[Any], bool]:
    if "__EMPTY__" in years or "__EMPTY__" in products or "__EMPTY__" in ports:
        return " AND 1 = 0", [], True
    clauses = []
    params: list[Any] = []
    if years:
        parsed_years = []
        for item in years:
            try:
                parsed_years.append(int(item))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"年份无效: {item}") from exc
        clauses.append(f"business_year IN ({','.join('?' for _ in parsed_years)})")
        params.extend(parsed_years)
    if products:
        clauses.append(f"product IN ({','.join('?' for _ in products)})")
        params.extend(products)
    if ports:
        clauses.append(f"port IN ({','.join('?' for _ in ports)})")
        params.extend(ports)
    return (" AND " + " AND ".join(clauses) if clauses else ""), params, False


@router.get("/iron-ore-basis/management/rows")
async def management_rows(
    years: str = "",
    products: str = "",
    ports: str = "",
    limit: int = 50,
    offset: int = 0,
    user=Depends(dv_current_user),
):
    dv_require_view("data_visualization.data", user)
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    where_sql, params, _ = _where_filters(
        _csv_values(years), _csv_values(products), _csv_values(ports)
    )
    with db.connect() as conn:
        cur = conn.cursor()
        total = db._exec(
            cur,
            "SELECT COUNT(*) AS c FROM iron_ore_basis_results WHERE 1 = 1" + where_sql,
            tuple(params),
        ).fetchone()["c"]
        rows = db._exec(
            cur,
            """SELECT business_date, week_label, business_year, port, product,
                      wet_spot_price, quality_adjustment, brand_adjustment,
                      futures_close, basis, data_status
               FROM iron_ore_basis_results
               WHERE 1 = 1"""
            + where_sql
            + " ORDER BY business_date DESC, port, product LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        ).fetchall()
    return {
        "data": [dict(row) for row in rows],
        "pagination": {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < total,
        },
    }


@router.get("/iron-ore-basis/display/chart")
async def display_chart(
    port: str = "日照港",
    years: str = "",
    products: str = "",
    user=Depends(dv_current_user),
):
    dv_require_view("data_visualization.display", user)
    if port not in PORT_ORDER:
        raise HTTPException(status_code=400, detail="港口无效")
    where_sql, params, empty = _where_filters(
        _csv_values(years), _csv_values(products), [port]
    )
    if empty:
        return {"port": port, "series": {}}
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            """SELECT business_date, business_year, product, basis
               FROM iron_ore_basis_results
               WHERE data_status = '有效'"""
            + where_sql
            + " ORDER BY product, business_year, business_date",
            tuple(params),
        ).fetchall()
    series: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        series[row["product"]][str(row["business_year"])].append(
            {"date": row["business_date"], "value": float(row["basis"])}
        )
    return {
        "port": port,
        "series": {
            product: dict(years_map)
            for product, years_map in series.items()
        },
    }


def _optimal_warrant_for_year(business_year: int, user: dict) -> Optional[dict[str, Any]]:
    dv_require_view("data_visualization.display", user)
    with db.connect() as conn:
        cur = conn.cursor()
        latest = db._exec(
            cur,
            """SELECT MAX(business_date) AS latest_date
               FROM iron_ore_basis_results
               WHERE business_year = ? AND data_status = '有效'""",
            (business_year,),
        ).fetchone()["latest_date"]
        if not latest:
            return None
        row = db._exec(
            cur,
            """SELECT business_date AS data_as_of, product, port, wet_spot_price,
                      quality_adjustment, brand_adjustment, standardized_spot_price,
                      futures_series, futures_close, basis
               FROM iron_ore_basis_results
               WHERE business_year = ? AND business_date = ? AND data_status = '有效'
               ORDER BY basis ASC, standardized_spot_price ASC, wet_spot_price ASC,
                        port ASC, product ASC
               LIMIT 1""",
            (business_year, latest),
        ).fetchone()
    return dict(row) if row else None


@router.get("/iron-ore-basis/display/optimal-warrant")
async def optimal_warrant(user=Depends(dv_current_user)):
    return _optimal_warrant_for_year(date.today().year, user)
