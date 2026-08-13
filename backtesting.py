# -*- coding: utf-8 -*-
"""
Backtesting del sistema UPS sobre datos historicos ya exportados desde
MT4 (ver UPS_Historico_Export.mq4).

Que hace, en resumen:
1. Lee el CSV con precio + los 6 indicadores, vela por vela.
2. Para cada vela, evalua las reglas Type 1 (Long y Short) usando el
   MISMO motor de reglas que corre en produccion (ups_rules_engine.py)
   - no hay duplicacion de logica, es literalmente el mismo codigo.
3. Cuando encuentra una senal completa, simula la entrada en la
   apertura de la vela siguiente (Regla 7 del manual), calcula SL/TP
   (Reglas 8-9), y camina hacia adelante en el historico para ver que
   se toco primero.
4. Al final, calcula estadisticas agregadas: numero de senales,
   % de acierto, pips promedio ganados/perdidos.

Este modulo NO usa IA - es analisis estadistico puro con pandas/Python,
tal como se definio en el diseno del proyecto.
"""

import csv
from dataclasses import dataclass
from typing import Optional, List

from ups_rules_engine import (
    LecturaIndicadores,
    evaluar_type1_long,
    evaluar_type1_short,
)


# =====================================================================
# CARGA DEL CSV EXPORTADO DESDE MT4
# =====================================================================

def _parse_float(valor: str) -> Optional[float]:
    """Convierte un campo del CSV a float, o None si esta vacio."""
    valor = valor.strip()
    if valor == "":
        return None
    return float(valor)


def cargar_historico(ruta_csv: str) -> List[dict]:
    """Lee el CSV exportado y devuelve una lista de dicts, uno por vela,
    en el mismo orden cronologico del archivo (mas viejo -> mas nuevo)."""
    filas = []
    with open(ruta_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            filas.append({
                "time": row["time"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "cs_magenta": _parse_float(row["cs_magenta"]),
                "cs_blanca": _parse_float(row["cs_blanca"]),
                "tt_darkgreen": _parse_float(row["tt_darkgreen"]),
                "tt_maroon": _parse_float(row["tt_maroon"]),
                "tt_lime": _parse_float(row["tt_lime"]),
                "tt_red": _parse_float(row["tt_red"]),
                "trvi_valor": _parse_float(row["trvi_valor"]),
                "trwave_darkgreen": _parse_float(row["trwave_darkgreen"]),
                "trwave_maroon": _parse_float(row["trwave_maroon"]),
                "trwave_lime": _parse_float(row["trwave_lime"]),
                "trwave_red": _parse_float(row["trwave_red"]),
                "tsd_aqua": _parse_float(row["tsd_aqua"]),
                "tsd_yellow": _parse_float(row["tsd_yellow"]),
                "bb_inferior": _parse_float(row["bb_inferior"]),
                "bb_superior": _parse_float(row["bb_superior"]),
            })
    return filas


def fila_a_lectura(fila: dict, fila_prev: dict) -> LecturaIndicadores:
    """Convierte una fila del CSV (+ la anterior, para cs_blanca_prev)
    en un LecturaIndicadores, listo para el motor de reglas."""
    return LecturaIndicadores(
        symbol=fila["symbol"],
        timeframe=fila["timeframe"],
        precio=fila["close"],
        cs_magenta=fila["cs_magenta"],
        cs_blanca=fila["cs_blanca"],
        cs_blanca_prev=fila_prev["cs_blanca"],
        tt_darkgreen=fila["tt_darkgreen"],
        tt_maroon=fila["tt_maroon"],
        tt_lime=fila["tt_lime"],
        tt_red=fila["tt_red"],
        trvi_valor=fila["trvi_valor"],
        trwave_darkgreen=fila["trwave_darkgreen"],
        trwave_maroon=fila["trwave_maroon"],
        trwave_lime=fila["trwave_lime"],
        trwave_red=fila["trwave_red"],
        tsd_aqua=fila["tsd_aqua"],
        tsd_yellow=fila["tsd_yellow"],
        bb_inferior=fila["bb_inferior"],
        bb_superior=fila["bb_superior"],
    )


# =====================================================================
# SIMULACION DE SALIDA (que se toca primero: TP o SL)
# =====================================================================

@dataclass
class ResultadoTrade:
    fecha_senal: str
    direccion: str
    entry: float
    sl: float
    tp: float
    resultado: str          # "TP", "SL", o "sin_resolver"
    pips: Optional[float]
    velas_hasta_resolver: Optional[int]


def _simular_salida(filas: List[dict], desde_idx: int, entry: float,
                     sl: float, tp: float, direccion: str,
                     max_velas: int = 50) -> tuple:
    """
    Camina hacia adelante en el historico desde 'desde_idx' buscando
    cual nivel se toca primero. Si en una misma vela se tocan AMBOS
    (SL y TP), se asume conservadoramente que el SL se toco primero
    (peor caso) - limitacion conocida de trabajar solo con OHLC, sin
    datos intra-vela.
    """
    limite = min(desde_idx + max_velas, len(filas))
    for j in range(desde_idx, limite):
        high = filas[j]["high"]
        low = filas[j]["low"]

        if direccion == "long":
            toco_tp = high >= tp
            toco_sl = low <= sl
        else:
            toco_tp = low <= tp
            toco_sl = high >= sl

        if toco_tp and toco_sl:
            pips = -(entry - sl) if direccion == "long" else -(sl - entry)
            return "SL", pips, j - desde_idx + 1
        elif toco_tp:
            pips = (tp - entry) if direccion == "long" else (entry - tp)
            return "TP", pips, j - desde_idx + 1
        elif toco_sl:
            pips = -(entry - sl) if direccion == "long" else -(sl - entry)
            return "SL", pips, j - desde_idx + 1

    return "sin_resolver", None, None


# =====================================================================
# BACKTESTING PRINCIPAL
# =====================================================================

def correr_backtest_sobre_filas(filas: List[dict], umbrales: dict = None,
                                  max_velas_espera: int = 50) -> dict:
    """
    Version reutilizable: recibe la lista de filas ya cargada (sin
    importar si vino de un CSV local o de la base de datos en
    Railway), y corre el backtest sobre ella.
    """
    trades_long: List[ResultadoTrade] = []
    trades_short: List[ResultadoTrade] = []

    for i in range(1, len(filas) - 1):
        lectura = fila_a_lectura(filas[i], filas[i - 1])

        eval_long = evaluar_type1_long(lectura, umbrales)
        if eval_long.senal_completa and lectura.tsd_aqua is not None:
            entry = filas[i + 1]["open"]
            sl = lectura.tsd_aqua
            riesgo = entry - sl
            if riesgo > 0:
                tp = entry + riesgo
                resultado, pips, velas = _simular_salida(
                    filas, i + 1, entry, sl, tp, "long", max_velas_espera)
                trades_long.append(ResultadoTrade(
                    filas[i]["time"], "long", entry, sl, tp, resultado, pips, velas))

        eval_short = evaluar_type1_short(lectura, umbrales)
        if eval_short.senal_completa and lectura.tsd_yellow is not None:
            entry = filas[i + 1]["open"]
            sl = lectura.tsd_yellow
            riesgo = sl - entry
            if riesgo > 0:
                tp = entry - riesgo
                resultado, pips, velas = _simular_salida(
                    filas, i + 1, entry, sl, tp, "short", max_velas_espera)
                trades_short.append(ResultadoTrade(
                    filas[i]["time"], "short", entry, sl, tp, resultado, pips, velas))

    return {
        "symbol": filas[0]["symbol"] if filas else "?",
        "timeframe": filas[0]["timeframe"] if filas else "?",
        "total_velas": len(filas),
        "trades_long": trades_long,
        "trades_short": trades_short,
    }


def correr_backtest(ruta_csv: str, umbrales: dict = None,
                     max_velas_espera: int = 50) -> dict:
    """Version para uso local/CLI: lee un CSV y corre el backtest."""
    filas = cargar_historico(ruta_csv)
    return correr_backtest_sobre_filas(filas, umbrales, max_velas_espera)


def calcular_estadisticas(trades: List[ResultadoTrade]) -> dict:
    resueltos = [t for t in trades if t.resultado in ("TP", "SL")]
    ganados = [t for t in resueltos if t.resultado == "TP"]
    perdidos = [t for t in resueltos if t.resultado == "SL"]
    sin_resolver = [t for t in trades if t.resultado == "sin_resolver"]

    total_resueltos = len(resueltos)
    pct_acierto = (len(ganados) / total_resueltos * 100) if total_resueltos > 0 else 0.0

    pips_ganados = [t.pips for t in ganados]
    pips_perdidos = [t.pips for t in perdidos]

    return {
        "total_senales": len(trades),
        "resueltos": total_resueltos,
        "sin_resolver": len(sin_resolver),
        "ganados": len(ganados),
        "perdidos": len(perdidos),
        "pct_acierto": round(pct_acierto, 1),
        "ganancia_promedio_pips": round(sum(pips_ganados) / len(pips_ganados), 4) if pips_ganados else 0,
        "perdida_promedio_pips": round(sum(pips_perdidos) / len(pips_perdidos), 4) if pips_perdidos else 0,
        "resultado_neto_pips": round(sum(pips_ganados) + sum(pips_perdidos), 4),
    }


def imprimir_reporte(resultado_backtest: dict):
    symbol = resultado_backtest["symbol"]
    tf = resultado_backtest["timeframe"]
    print("=" * 70)
    print(f"BACKTEST: {symbol} {tf} - {resultado_backtest['total_velas']} velas analizadas")
    print("=" * 70)

    for direccion, trades in [("LONG", resultado_backtest["trades_long"]),
                               ("SHORT", resultado_backtest["trades_short"])]:
        stats = calcular_estadisticas(trades)
        print(f"\n--- {direccion} Type 1 ---")
        print(f"  Senales totales:        {stats['total_senales']}")
        print(f"  Resueltas (TP o SL):    {stats['resueltos']}")
        print(f"  Sin resolver (abiertas):{stats['sin_resolver']}")
        print(f"  Ganadas (TP):           {stats['ganados']}")
        print(f"  Perdidas (SL):          {stats['perdidos']}")
        print(f"  % de acierto:           {stats['pct_acierto']}%")
        print(f"  Ganancia promedio:      {stats['ganancia_promedio_pips']}")
        print(f"  Perdida promedio:       {stats['perdida_promedio_pips']}")
        print(f"  Resultado neto:         {stats['resultado_neto_pips']}")


if __name__ == "__main__":
    import sys
    ruta = sys.argv[1] if len(sys.argv) > 1 else "UPS_Historico_XAGUSD_W1.csv"
    resultado = correr_backtest(ruta)
    imprimir_reporte(resultado)
