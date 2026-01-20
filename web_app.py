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


def aggregate_daily_energy(rows: List[Tuple[datetime, float]]) -> Tuple[Dict[str, float], float]:
    # agrupa por dia (YYYY-MM-DD) calculando energia em kWh (integral trapezoidal)
    # Retorna: (dicionario_dia_valor, total_periodo)
    
    if not rows:
        return {}, 0.0

    # Garante ordenação
    rows.sort(key=lambda x: x[0])
    
    daily_energy: Dict[str, float] = {}
    total_energy = 0.0
    
    # Processa intervalos entre pontos consecutivos
    for i in range(1, len(rows)):
        dt_curr, p_curr = rows[i]
        dt_prev, p_prev = rows[i-1]
        
        # Diferença de tempo em horas
        delta_seconds = (dt_curr - dt_prev).total_seconds()
        
        # Se o gap for muito grande (ex: > 1 hora), ignoramos a integração nesse intervalo
        # assumindo que o sistema estava desligado ou sem coleta.
        if delta_seconds > 3600:
            continue
            
        delta_hours = delta_seconds / 3600.0
        
        # Regra do trapézio: área = (b1 + b2) * h / 2
        # Potência média no intervalo (W) * tempo (h) = Wh
        # / 1000 -> kWh
        avg_power = (p_curr + p_prev) / 2.0
        energy_kwh = (avg_power * delta_hours) / 1000.0
        
        # Atribui ao dia do ponto final (dt_curr)
        day_key = dt_curr.date().isoformat()
        
        daily_energy[day_key] = daily_energy.get(day_key, 0.0) + energy_kwh
        total_energy += energy_kwh

    # Formata arredondando
    result = {k: float(f"{v:.2f}") for k, v in sorted(daily_energy.items())}
    total_period = float(f"{total_energy:.2f}")
    
    return result, total_period


def aggregate_20min_full_day(rows: List[Tuple[datetime, float]], day_start: datetime) -> Tuple[List[str], List[Any]]:
    """
    Agrupa por média a cada 20 minutos e retorna as 72 posições do dia (00:00 ... 23:40).
    labels -> lista de HH:MM, values -> médias ou None quando não houve ponto no bucket.
    """
    buckets: Dict[str, List[float]] = {}

    for dt, val in rows:
        # Encaixa o ponto no bucket de 20 minutos mais próximo para baixo
        bucket_dt = dt.replace(minute=(dt.minute // 20) * 20, second=0, microsecond=0)
        key = bucket_dt.strftime("%H:%M")
        buckets.setdefault(key, []).append(val)

    labels: List[str] = []
    values: List[Any] = []

    t = day_start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = t + timedelta(days=1)

    while t < end:
        label = t.strftime("%H:%M")
        labels.append(label)
        bucket_vals = buckets.get(label)
        values.append(round(sum(bucket_vals) / len(bucket_vals), 2) if bucket_vals else None)
        t += timedelta(minutes=20)

    return labels, values


def load_recent_bucketed(limit: int = 10, bucket_minutes: int = 10) -> List[Dict[str, Any]]:
    conn = db_connect()
    cur = conn.cursor()
    max_rows = max(2000, limit * bucket_minutes * 20)
    cur.execute("SELECT ts_local, power_w FROM nep_power ORDER BY ts_local DESC LIMIT ?", (max_rows,))
    rows = cur.fetchall()
    conn.close()

    buckets: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for ts_local, power_w in rows:
        try:
            dt = parse_ts(ts_local)
        except Exception:
            continue
        bucket_dt = dt.replace(minute=(dt.minute // bucket_minutes) * bucket_minutes, second=0, microsecond=0)
        key = bucket_dt.isoformat(timespec="minutes")
        if key not in buckets:
            if len(order) >= limit:
                break
            buckets[key] = {"dt": bucket_dt, "sum": 0.0, "count": 0}
            order.append(key)
        buckets[key]["sum"] += float(power_w)
        buckets[key]["count"] += 1

    result: List[Dict[str, Any]] = []
    for key in order:
        bucket = buckets[key]
        avg = bucket["sum"] / bucket["count"] if bucket["count"] else 0.0
        result.append({
            "label": bucket["dt"].strftime("%Y-%m-%d %H:%M"),
            "value": float(f"{avg:.2f}"),
        })

    return result


def load_recent_raw(limit: int = 10) -> List[Dict[str, Any]]:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT ts_local, power_w FROM nep_power ORDER BY ts_local DESC LIMIT ?", (limit * 5,))
    rows = cur.fetchall()
    conn.close()

    result: List[Dict[str, Any]] = []
    for ts_local, power_w in rows:
        try:
            dt = parse_ts(ts_local)
        except Exception:
            continue
        if power_w is None:
            continue
        try:
            val = float(power_w)
        except Exception:
            continue
        result.append({
            "label": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "value": float(f"{val:.2f}"),
        })
        if len(result) >= limit:
            break

    return result


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
    mode: str = Query("day", pattern="^(month|day)$"),
    month: str = Query(None, description="YYYY-MM"),
    day: str = Query(None, description="YYYY-MM-DD"),
) -> Dict[str, Any]:
    """
    mode=month -> retorna série diária (total kWh por dia) do mês selecionado
    mode=day   -> retorna série de 20min (média W) do dia selecionado
    """
    now = datetime.now(TIMEZONE)
    recent_rows = load_recent_bucketed(limit=10, bucket_minutes=10)
    recent_raw = load_recent_raw(limit=10)

    if mode == "month":
        ym = month or now.strftime("%Y-%m")
        start, end = month_bounds(ym)
        rows = load_rows_between(start, end)
        
        # Agrega energia diária
        agg, total_kwh = aggregate_daily_energy(rows)
        
        labels = list(agg.keys())
        values = list(agg.values())
        
        title = f"Energia (kWh) - Total diário ({ym})"
        return {
            "mode": mode,
            "title": title,
            "labels": labels,
            "values": values,
            "stat_label": "Total",
            "stat_value": total_kwh,
            "stat_unit": "kWh",
            "recent_rows": recent_rows,
            "recent_raw": recent_raw,
        }

    # mode == "day"
    ymd = day or now.strftime("%Y-%m-%d")
    start, end = day_bounds(ymd)
    rows = load_rows_between(start, end)
    
    # Agrega média a cada 20 min e preenche todos os 72 pontos do dia
    labels, values = aggregate_20min_full_day(rows, start)
    
    # Calcula PICO do dia (máximo valor registrado nos dados brutos)
    max_power = 0.0
    if rows:
        max_power = max(r[1] for r in rows)
    
    title = f"Potência (W) - Média 20min ({ymd})"
    return {
        "mode": mode,
        "title": title,
        "labels": labels,
        "values": values,
        "stat_label": "Pico",
        "stat_value": float(f"{max_power:.2f}"),
        "stat_unit": "W",
        "recent_rows": recent_rows,
        "recent_raw": recent_raw,
    }
