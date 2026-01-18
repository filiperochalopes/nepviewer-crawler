import os
import sqlite3
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Tuple

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.requests import Request


TIMEZONE = ZoneInfo("America/Bahia")
SQLITE_PATH = os.environ.get("SQLITE_PATH", "nepviewer.db")

app = FastAPI(title="NepViewer Power Dashboard", version="1.0.0")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


def db_connect():
    return sqlite3.connect(SQLITE_PATH)


def parse_ts(ts_local: str) -> datetime:
    # ts_local foi salvo via datetime.now(ZoneInfo("America/Bahia")).isoformat(timespec="seconds")
    # Ex: 2026-01-16T08:01:02-03:00
    return datetime.fromisoformat(ts_local)


def load_rows_between(start_dt: datetime, end_dt: datetime) -> List[Tuple[datetime, float]]:
    conn = db_connect()
    cur = conn.cursor()
    # Seleciona tudo e filtra em Python para evitar depender do parsing de timezone do SQLite.
    cur.execute("SELECT ts_local, power_w FROM nep_power ORDER BY ts_local ASC")
    rows = []
    for ts_local, power_w in cur.fetchall():
        try:
            dt = parse_ts(ts_local)
        except Exception:
            continue
        if start_dt <= dt < end_dt:
            rows.append((dt, float(power_w)))
    conn.close()
    return rows


def month_bounds(ym: str) -> Tuple[datetime, datetime]:
    # ym: "YYYY-MM"
    y, m = ym.split("-")
    y = int(y); m = int(m)
    start = datetime(y, m, 1, 0, 0, 0, tzinfo=TIMEZONE)
    if m == 12:
        end = datetime(y + 1, 1, 1, 0, 0, 0, tzinfo=TIMEZONE)
    else:
        end = datetime(y, m + 1, 1, 0, 0, 0, tzinfo=TIMEZONE)
    return start, end


def day_bounds(ymd: str) -> Tuple[datetime, datetime]:
    # ymd: "YYYY-MM-DD"
    y, m, d = ymd.split("-")
    y = int(y); m = int(m); d = int(d)
    start = datetime(y, m, d, 0, 0, 0, tzinfo=TIMEZONE)
    end = start + timedelta(days=1)
    return start, end


def aggregate_daily(rows: List[Tuple[datetime, float]]) -> Dict[str, float]:
    # agrupa por dia (YYYY-MM-DD) usando média
    buckets: Dict[str, List[float]] = {}
    for dt, val in rows:
        key = dt.date().isoformat()
        buckets.setdefault(key, []).append(val)
    return {k: (sum(v) / max(len(v), 1)) for k, v in sorted(buckets.items())}


def aggregate_hourly(rows: List[Tuple[datetime, float]]) -> Dict[str, float]:
    # agrupa por hora (YYYY-MM-DD HH:00) usando média
    buckets: Dict[str, List[float]] = {}
    for dt, val in rows:
        key = dt.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:00")
        buckets.setdefault(key, []).append(val)
    return {k: (sum(v) / max(len(v), 1)) for k, v in sorted(buckets.items())}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # defaults: mês atual e dia atual no fuso America/Bahia
    now = datetime.now(TIMEZONE)
    default_month = now.strftime("%Y-%m")
    default_day = now.strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "default_month": default_month,
            "default_day": default_day,
        },
    )


@app.get("/api/series")
def api_series(
    mode: str = Query("month", pattern="^(month|day)$"),
    month: str = Query(None, description="YYYY-MM"),
    day: str = Query(None, description="YYYY-MM-DD"),
) -> Dict[str, Any]:
    """
    mode=month -> retorna série diária (média por dia) do mês selecionado
    mode=day   -> retorna série horária (média por hora) do dia selecionado
    """
    now = datetime.now(TIMEZONE)

    if mode == "month":
        ym = month or now.strftime("%Y-%m")
        start, end = month_bounds(ym)
        rows = load_rows_between(start, end)
        agg = aggregate_daily(rows)
        labels = list(agg.keys())
        values = [round(agg[k], 2) for k in labels]
        title = f"Potência(W) - Média diária ({ym})"
        return {"mode": mode, "title": title, "labels": labels, "values": values}

    # mode == "day"
    ymd = day or now.strftime("%Y-%m-%d")
    start, end = day_bounds(ymd)
    rows = load_rows_between(start, end)
    agg = aggregate_hourly(rows)
    labels = list(agg.keys())
    values = [round(agg[k], 2) for k in labels]
    title = f"Potência(W) - Média horária ({ymd})"
    return {"mode": mode, "title": title, "labels": labels, "values": values}
