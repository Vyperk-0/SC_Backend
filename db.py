# -*- coding: utf-8 -*-
"""
Conexion a Postgres (base de datos de Railway) donde vive el
historico de precios + indicadores.

DISENO: cada combinacion simbolo + timeframe vive en su PROPIA tabla
(ej. historico_XAGUSD_W1, historico_EURUSD_H1), en vez de una sola
tabla compartida con columna "symbol". Las tablas se crean
automaticamente la primera vez que se sube historico de ese
simbolo/timeframe - no hace falta crearlas a mano.

Railway provee la variable de entorno DATABASE_URL automaticamente
al agregar un servicio de Postgres al proyecto.
"""

import os
import re
import psycopg2
import psycopg2.errors
from psycopg2 import sql
from psycopg2.extras import execute_values
from typing import List, Optional

DATABASE_URL = os.environ.get("DATABASE_URL")

COLUMNAS = [
    "time", "open", "high", "low", "close",
    "cs_magenta", "cs_blanca",
    "tt_darkgreen", "tt_maroon", "tt_lime", "tt_red",
    "trvi_valor",
    "trwave_darkgreen", "trwave_maroon", "trwave_lime", "trwave_red",
    "tsd_aqua", "tsd_yellow",
    "bb_inferior", "bb_superior",
]


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL no esta configurada. Agrega un servicio de "
            "Postgres al proyecto de Railway (New -> Database -> "
            "PostgreSQL) y conecta la variable al servicio web."
        )
    return psycopg2.connect(DATABASE_URL)


def _nombre_tabla(symbol: str, timeframe: str) -> str:
    """
    Genera el nombre de tabla para este simbolo/timeframe, ej.
    historico_XAGUSD_W1. Se sanitiza a solo letras/numeros para
    evitar inyeccion SQL, ya que estos valores vienen de parametros
    de la URL (query params del usuario).
    """
    symbol_limpio = re.sub(r"[^A-Za-z0-9]", "", symbol).upper()
    tf_limpio = re.sub(r"[^A-Za-z0-9]", "", timeframe).upper()
    if not symbol_limpio or not tf_limpio:
        raise ValueError("symbol/timeframe invalido (debe tener letras/numeros)")
    return f"{symbol_limpio}_{tf_limpio}"


def verificar_conexion():
    """Chequeo simple de que la base de datos responde. Se llama al arrancar el backend."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
    finally:
        conn.close()


def guardar_historico(symbol: str, timeframe: str, filas: List[dict]) -> int:
    """
    Crea (si no existe) la tabla propia de este simbolo/timeframe, e
    inserta/actualiza las filas dadas.
    """
    if not filas:
        return 0

    tabla = _nombre_tabla(symbol, timeframe)

    crear_sql = sql.SQL("""
        CREATE TABLE IF NOT EXISTS {tabla} (
            time TIMESTAMP PRIMARY KEY,
            open DOUBLE PRECISION, high DOUBLE PRECISION,
            low DOUBLE PRECISION, close DOUBLE PRECISION,
            cs_magenta DOUBLE PRECISION, cs_blanca DOUBLE PRECISION,
            tt_darkgreen DOUBLE PRECISION, tt_maroon DOUBLE PRECISION,
            tt_lime DOUBLE PRECISION, tt_red DOUBLE PRECISION,
            trvi_valor DOUBLE PRECISION,
            trwave_darkgreen DOUBLE PRECISION, trwave_maroon DOUBLE PRECISION,
            trwave_lime DOUBLE PRECISION, trwave_red DOUBLE PRECISION,
            tsd_aqua DOUBLE PRECISION, tsd_yellow DOUBLE PRECISION,
            bb_inferior DOUBLE PRECISION, bb_superior DOUBLE PRECISION
        );
    """).format(tabla=sql.Identifier(tabla))

    insert_sql = sql.SQL("""
        INSERT INTO {tabla} ({columnas})
        VALUES %s
        ON CONFLICT (time) DO UPDATE SET
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
    """).format(
        tabla=sql.Identifier(tabla),
        columnas=sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNAS),
    )

    valores = [tuple(f[c] for c in COLUMNAS) for f in filas]

    symbol_limpio = re.sub(r"[^A-Za-z0-9]", "", symbol).upper()
    tf_limpio = re.sub(r"[^A-Za-z0-9]", "", timeframe).upper()

    registro_crear_sql = """
        CREATE TABLE IF NOT EXISTS historico_registro (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            tabla TEXT NOT NULL,
            PRIMARY KEY (symbol, timeframe)
        );
    """
    registro_insert_sql = """
        INSERT INTO historico_registro (symbol, timeframe, tabla)
        VALUES (%s, %s, %s)
        ON CONFLICT (symbol, timeframe) DO NOTHING;
    """

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(crear_sql)
            execute_values(cur, insert_sql.as_string(conn), valores)
            cur.execute(registro_crear_sql)
            cur.execute(registro_insert_sql, (symbol_limpio, tf_limpio, tabla))
        conn.commit()
    finally:
        conn.close()

    return len(filas)


def obtener_historico(symbol: str, timeframe: str) -> List[dict]:
    """Devuelve el historico de la tabla propia de este simbolo/timeframe,
    ordenado cronologicamente. Lista vacia si la tabla aun no existe."""
    tabla = _nombre_tabla(symbol, timeframe)

    query = sql.SQL("""
        SELECT {columnas} FROM {tabla} ORDER BY time ASC;
    """).format(
        columnas=sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNAS),
        tabla=sql.Identifier(tabla),
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(query)
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return []
            columnas_resultado = [desc[0] for desc in cur.description]
            filas = [dict(zip(columnas_resultado, row)) for row in cur.fetchall()]
    finally:
        conn.close()

    symbol_limpio = re.sub(r"[^A-Za-z0-9]", "", symbol).upper()
    tf_limpio = re.sub(r"[^A-Za-z0-9]", "", timeframe).upper()
    for f in filas:
        f["symbol"] = symbol_limpio
        f["timeframe"] = tf_limpio
        f["time"] = f["time"].strftime("%Y.%m.%d %H:%M")

    return filas


def listar_pares_disponibles() -> List[dict]:
    """Devuelve un resumen (simbolo, timeframe, velas, rango de fechas)
    de cada par/timeframe registrado en historico_registro."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, timeframe, tabla FROM historico_registro
                ORDER BY symbol, timeframe;
            """)
            registros = cur.fetchall()

            resultado = []
            for symbol, timeframe, tabla in registros:
                cur.execute(sql.SQL(
                    "SELECT COUNT(*), MIN(time), MAX(time) FROM {t};"
                ).format(t=sql.Identifier(tabla)))
                count, desde, hasta = cur.fetchone()

                resultado.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "velas": count,
                    "desde": desde.strftime("%Y-%m-%d") if desde else None,
                    "hasta": hasta.strftime("%Y-%m-%d") if hasta else None,
                })
    finally:
        conn.close()

    return resultado
