# -*- coding: utf-8 -*-
"""
Conexion a Postgres (base de datos de Railway) donde vive el
historico de precios + indicadores. Reemplaza el uso de CSV locales
para que el backtesting no dependa de la PC del usuario - todo el
historico vive en la nube, junto con el resto del backend.

Railway provee la variable de entorno DATABASE_URL automaticamente
al agregar un servicio de Postgres al proyecto.
"""

import os
import psycopg2
from psycopg2.extras import execute_values
from typing import List, Optional

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL no esta configurada. Agrega un servicio de "
            "Postgres al proyecto de Railway (New -> Database -> "
            "PostgreSQL) - la variable se agrega automaticamente."
        )
    return psycopg2.connect(DATABASE_URL)


def inicializar_tabla():
    """Crea la tabla de historico si no existe. Se llama al arrancar el backend."""
    sql = """
    CREATE TABLE IF NOT EXISTS historico (
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        time TIMESTAMP NOT NULL,
        open DOUBLE PRECISION,
        high DOUBLE PRECISION,
        low DOUBLE PRECISION,
        close DOUBLE PRECISION,
        cs_magenta DOUBLE PRECISION,
        cs_blanca DOUBLE PRECISION,
        tt_darkgreen DOUBLE PRECISION,
        tt_maroon DOUBLE PRECISION,
        tt_lime DOUBLE PRECISION,
        tt_red DOUBLE PRECISION,
        trvi_valor DOUBLE PRECISION,
        trwave_darkgreen DOUBLE PRECISION,
        trwave_maroon DOUBLE PRECISION,
        trwave_lime DOUBLE PRECISION,
        trwave_red DOUBLE PRECISION,
        tsd_aqua DOUBLE PRECISION,
        tsd_yellow DOUBLE PRECISION,
        bb_inferior DOUBLE PRECISION,
        bb_superior DOUBLE PRECISION,
        PRIMARY KEY (symbol, timeframe, time)
    );
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def guardar_historico(symbol: str, timeframe: str, filas: List[dict]) -> int:
    """
    Inserta (o actualiza si ya existia esa vela exacta) las filas de
    historico. Devuelve cuantas filas se procesaron.
    """
    if not filas:
        return 0

    columnas = [
        "symbol", "timeframe", "time", "open", "high", "low", "close",
        "cs_magenta", "cs_blanca",
        "tt_darkgreen", "tt_maroon", "tt_lime", "tt_red",
        "trvi_valor",
        "trwave_darkgreen", "trwave_maroon", "trwave_lime", "trwave_red",
        "tsd_aqua", "tsd_yellow",
        "bb_inferior", "bb_superior",
    ]

    valores = []
    for f in filas:
        valores.append((
            symbol, timeframe, f["time"], f["open"], f["high"], f["low"], f["close"],
            f["cs_magenta"], f["cs_blanca"],
            f["tt_darkgreen"], f["tt_maroon"], f["tt_lime"], f["tt_red"],
            f["trvi_valor"],
            f["trwave_darkgreen"], f["trwave_maroon"], f["trwave_lime"], f["trwave_red"],
            f["tsd_aqua"], f["tsd_yellow"],
            f["bb_inferior"], f["bb_superior"],
        ))

    sql = f"""
    INSERT INTO historico ({", ".join(columnas)})
    VALUES %s
    ON CONFLICT (symbol, timeframe, time) DO UPDATE SET
        open = EXCLUDED.open, high = EXCLUDED.high,
        low = EXCLUDED.low, close = EXCLUDED.close,
        cs_magenta = EXCLUDED.cs_magenta, cs_blanca = EXCLUDED.cs_blanca,
        tt_darkgreen = EXCLUDED.tt_darkgreen, tt_maroon = EXCLUDED.tt_maroon,
        tt_lime = EXCLUDED.tt_lime, tt_red = EXCLUDED.tt_red,
        trvi_valor = EXCLUDED.trvi_valor,
        trwave_darkgreen = EXCLUDED.trwave_darkgreen, trwave_maroon = EXCLUDED.trwave_maroon,
        trwave_lime = EXCLUDED.trwave_lime, trwave_red = EXCLUDED.trwave_red,
        tsd_aqua = EXCLUDED.tsd_aqua, tsd_yellow = EXCLUDED.tsd_yellow,
        bb_inferior = EXCLUDED.bb_inferior, bb_superior = EXCLUDED.bb_superior;
    """

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, sql, valores)
        conn.commit()
    finally:
        conn.close()

    return len(filas)


def obtener_historico(symbol: str, timeframe: str) -> List[dict]:
    """Devuelve el historico guardado para symbol/timeframe, ordenado
    cronologicamente (mas viejo -> mas nuevo), listo para el backtest."""
    sql = """
    SELECT time, symbol, timeframe, open, high, low, close,
           cs_magenta, cs_blanca,
           tt_darkgreen, tt_maroon, tt_lime, tt_red,
           trvi_valor,
           trwave_darkgreen, trwave_maroon, trwave_lime, trwave_red,
           tsd_aqua, tsd_yellow,
           bb_inferior, bb_superior
    FROM historico
    WHERE symbol = %s AND timeframe = %s
    ORDER BY time ASC;
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe))
            columnas = [desc[0] for desc in cur.description]
            filas = [dict(zip(columnas, row)) for row in cur.fetchall()]
    finally:
        conn.close()

    for f in filas:
        f["time"] = f["time"].strftime("%Y.%m.%d %H:%M")

    return filas


def listar_pares_disponibles() -> List[dict]:
    """Devuelve la lista de (symbol, timeframe) que ya tienen historico
    cargado, con la cantidad de velas de cada uno."""
    sql = """
    SELECT symbol, timeframe, COUNT(*) as velas,
           MIN(time) as desde, MAX(time) as hasta
    FROM historico
    GROUP BY symbol, timeframe
    ORDER BY symbol, timeframe;
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            resultado = [
                {"symbol": r[0], "timeframe": r[1], "velas": r[2],
                 "desde": r[3].strftime("%Y-%m-%d"), "hasta": r[4].strftime("%Y-%m-%d")}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()
    return resultado
