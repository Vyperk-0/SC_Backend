# -*- coding: utf-8 -*-
"""
Motor de reglas del sistema UPS (Ultimate Profit Solution).

Este modulo NO usa IA ni aprendizaje automatico. Traduce, de forma
determinista, las reglas exactas descritas en el manual UPS a codigo
Python, usando el mapeo de buffers confirmado manualmente sobre MT4
(ver tabla de mapeo al final de este archivo).

Filosofia de diseno:
- Cada regla del manual es una funcion pura que devuelve True/False
  + una explicacion en texto (para que el chat pueda citarla).
- El motor NUNCA "interpreta" ni "adivina" - solo compara numeros.
- Se reporta tanto el resultado final (senal completa si/no) como el
  cumplimiento parcial (cuantas de las N reglas se cumplieron), para
  poder responder preguntas tipo "que tan cerca esta este par de dar
  senal".
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


# =====================================================================
# MAPEO DE BUFFERS (confirmado manualmente, ver conversacion de mapeo)
# =====================================================================
#
# UPS_CS       -> buffer 0 = linea magenta, buffer 1 = linea blanca
# UPS_TT       -> buffer 0 = DarkGreen, 1 = Maroon, 2 = Lime, 3 = Red
#                 (cada buffer trae un valor cuando ese color esta
#                  activo; el resto quedan EMPTY_VALUE)
# UPS_TRVI     -> buffer 0 = volatilidad numerica (pips/puntos).
#                 El color verde/rojo NO es legible via iCustom()
#                 (esta calculado internamente en el indicador
#                  encriptado), asi que usamos un umbral propio,
#                  configurable y calibrable con backtesting.
# UPS_TRWave   -> mismo patron que UPS_TT: 0=DarkGreen, 1=Maroon,
#                 2=Lime, 3=Red
# UPS_TSD      -> buffer 0 = punto Aqua (nivel de precio),
#                 buffer 1 = punto Yellow (nivel de precio)
# UPS_BBCloud  -> buffer 0 = banda inferior, buffer 1 = banda
#                 superior (buffers 2 y 3 son duplicados visuales,
#                 se ignoran)
# UPS_TMRInfo  -> pendiente / no usado en las reglas de entrada
#
# =====================================================================


class Direccion(Enum):
    LONG = "long"
    SHORT = "short"


class TipoEntrada(Enum):
    TYPE_1 = "type_1"  # solo timeframe actual
    TYPE_2 = "type_2"  # + confirmacion en timeframe superior


# Tabla de timeframes relacionados, tal como la da el manual (para Type 2)
TIMEFRAME_SUPERIOR = {
    "M1": "M15",
    "M5": "M30",
    "M15": "H1",
    "M30": "H4",
    "H1": "H4",
    "H4": "D1",
    "D1": "W1",
    "W1": "MN",
}


@dataclass
class LecturaIndicadores:
    """
    Snapshot de los valores de los 6 indicadores UPS relevantes para
    las reglas, en un instante dado (una vela) de un par/timeframe.

    Los campos "prev_*" son necesarios para evaluar condiciones de
    "cruzando" o "subiendo/bajando" (comparacion contra la vela
    anterior), tal como pide el manual.
    """
    symbol: str
    timeframe: str
    precio: float

    # UPS_CS (None = el indicador aun no termino de calcular en este ciclo)
    cs_magenta: Optional[float]
    cs_blanca: Optional[float]
    cs_blanca_prev: Optional[float]  # vela anterior, para detectar "going up/down"

    # UPS_TT  (None = ese color no esta activo en esta vela)
    tt_darkgreen: Optional[float]
    tt_maroon: Optional[float]
    tt_lime: Optional[float]
    tt_red: Optional[float]

    # UPS_TRVI
    trvi_valor: Optional[float]  # volatilidad numerica cruda

    # UPS_TRWave
    trwave_darkgreen: Optional[float]
    trwave_maroon: Optional[float]
    trwave_lime: Optional[float]
    trwave_red: Optional[float]

    # UPS_TSD  (None = no hay punto formado en esta vela)
    tsd_aqua: Optional[float]
    tsd_yellow: Optional[float]

    # UPS_BBCloud (None = el indicador aun no termino de calcular)
    bb_inferior: Optional[float]
    bb_superior: Optional[float]


@dataclass
class ResultadoRegla:
    """Resultado de evaluar UNA regla puntual del manual."""
    numero: int
    descripcion: str
    cumple: bool
    detalle: str  # explicacion legible, citando los valores reales


@dataclass
class ResultadoEvaluacion:
    """Resultado completo de evaluar todas las reglas de un setup."""
    symbol: str
    timeframe: str
    direccion: Direccion
    tipo: TipoEntrada
    reglas: List[ResultadoRegla] = field(default_factory=list)

    @property
    def reglas_cumplidas(self) -> int:
        return sum(1 for r in self.reglas if r.cumple)

    @property
    def total_reglas(self) -> int:
        return len(self.reglas)

    @property
    def senal_completa(self) -> bool:
        """True solo si TODAS las reglas se cumplen (senal real)."""
        return self.total_reglas > 0 and self.reglas_cumplidas == self.total_reglas

    def resumen(self) -> str:
        estado = "SENAL COMPLETA" if self.senal_completa else "incompleta"
        return (f"{self.symbol} {self.timeframe} {self.direccion.value} "
                f"{self.tipo.value}: {self.reglas_cumplidas}/{self.total_reglas} "
                f"reglas cumplidas ({estado})")


# =====================================================================
# UMBRAL DE VOLATILIDAD (Regla 3) - configurable por simbolo
# =====================================================================
#
# Como el color verde/rojo de TRVI no es legible directamente, cada
# simbolo necesita su propio umbral de "volatilidad suficiente",
# calibrado idealmente con el backtesting (ver modulo de historico).
# Estos son valores de partida razonables, NO definitivos.
UMBRAL_VOLATILIDAD_DEFAULT = {
    # symbol: minimo de volatilidad (mismas unidades que trvi_valor)
    "EURUSD": 30,
    "GBPUSD": 40,
    "XAGUSD": 200,
    # Agregar mas simbolos segun se vayan calibrando con el historico
}


def volatilidad_suficiente(lectura: LecturaIndicadores,
                            umbrales: dict = None) -> bool:
    umbrales = umbrales or UMBRAL_VOLATILIDAD_DEFAULT
    umbral = umbrales.get(lectura.symbol, 0)  # sin umbral definido = no filtra
    return lectura.trvi_valor >= umbral


# =====================================================================
# REGLAS INDIVIDUALES - LONG
# =====================================================================

def regla1_cs_long(lectura: LecturaIndicadores) -> ResultadoRegla:
    """CS: blanca por encima (o cruzando por encima) de magenta, subiendo."""
    if lectura.cs_blanca is None or lectura.cs_magenta is None or lectura.cs_blanca_prev is None:
        return ResultadoRegla(1, "CS: blanca > magenta y subiendo", False,
                               "dato no disponible (indicador CS aun calculando)")
    arriba = lectura.cs_blanca > lectura.cs_magenta
    subiendo = lectura.cs_blanca > lectura.cs_blanca_prev
    cumple = arriba and subiendo
    detalle = (f"blanca={lectura.cs_blanca:.2f} magenta={lectura.cs_magenta:.2f} "
               f"(prev blanca={lectura.cs_blanca_prev:.2f}) -> "
               f"{'arriba y subiendo' if cumple else 'no cumple posicion/direccion'}")
    return ResultadoRegla(1, "CS: blanca > magenta y subiendo", cumple, detalle)


def regla2_tt_long(lectura: LecturaIndicadores) -> ResultadoRegla:
    """TT: coloreado verde (claro u oscuro)."""
    cumple = lectura.tt_darkgreen is not None or lectura.tt_lime is not None
    tono = "Dark Green" if lectura.tt_darkgreen is not None else (
        "Light Green" if lectura.tt_lime is not None else "no verde")
    return ResultadoRegla(2, "TT: verde (claro u oscuro)", cumple, f"tono actual: {tono}")


def regla3_trvi(lectura: LecturaIndicadores, umbrales: dict = None) -> ResultadoRegla:
    """TRVI: volatilidad suficiente (umbral propio, ver nota arriba)."""
    if lectura.trvi_valor is None:
        return ResultadoRegla(3, "TRVI: volatilidad suficiente", False,
                               "dato no disponible (indicador TRVI aun calculando)")
    cumple = volatilidad_suficiente(lectura, umbrales)
    return ResultadoRegla(3, "TRVI: volatilidad suficiente", cumple,
                           f"volatilidad={lectura.trvi_valor:.2f}")


def regla4_trwave_long(lectura: LecturaIndicadores) -> ResultadoRegla:
    """TR Wave: verde (claro u oscuro) y por encima del nivel Zero."""
    verde_activo = lectura.trwave_darkgreen is not None or lectura.trwave_lime is not None
    valor = lectura.trwave_darkgreen or lectura.trwave_lime or 0
    cumple = verde_activo and valor > 0
    return ResultadoRegla(4, "TR Wave: verde y sobre nivel Zero", cumple,
                           f"verde_activo={verde_activo} valor={valor:.2f}")


def regla5_tsd_long(lectura: LecturaIndicadores) -> ResultadoRegla:
    """TSD: punto Aqua formado debajo del precio."""
    cumple = lectura.tsd_aqua is not None and lectura.tsd_aqua < lectura.precio
    detalle = (f"aqua={lectura.tsd_aqua} precio={lectura.precio:.2f}"
               if lectura.tsd_aqua is not None else "no hay punto Aqua en esta vela")
    return ResultadoRegla(5, "TSD: punto Aqua debajo del precio", cumple, detalle)


def regla6_bbcloud_long(lectura: LecturaIndicadores) -> ResultadoRegla:
    """Precio cruzando o por encima de la banda inferior de BB Cloud."""
    if lectura.bb_inferior is None:
        return ResultadoRegla(6, "Precio >= banda inferior BB Cloud", False,
                               "dato no disponible (indicador BBCloud aun calculando)")
    cumple = lectura.precio >= lectura.bb_inferior
    return ResultadoRegla(6, "Precio >= banda inferior BB Cloud", cumple,
                           f"precio={lectura.precio:.2f} banda_inferior={lectura.bb_inferior:.2f}")


# =====================================================================
# REGLAS INDIVIDUALES - SHORT (espejo exacto de las de Long)
# =====================================================================

def regla1_cs_short(lectura: LecturaIndicadores) -> ResultadoRegla:
    if lectura.cs_blanca is None or lectura.cs_magenta is None or lectura.cs_blanca_prev is None:
        return ResultadoRegla(1, "CS: blanca < magenta y bajando", False,
                               "dato no disponible (indicador CS aun calculando)")
    abajo = lectura.cs_blanca < lectura.cs_magenta
    bajando = lectura.cs_blanca < lectura.cs_blanca_prev
    cumple = abajo and bajando
    detalle = (f"blanca={lectura.cs_blanca:.2f} magenta={lectura.cs_magenta:.2f} "
               f"(prev blanca={lectura.cs_blanca_prev:.2f}) -> "
               f"{'abajo y bajando' if cumple else 'no cumple posicion/direccion'}")
    return ResultadoRegla(1, "CS: blanca < magenta y bajando", cumple, detalle)


def regla2_tt_short(lectura: LecturaIndicadores) -> ResultadoRegla:
    cumple = lectura.tt_maroon is not None or lectura.tt_red is not None
    tono = "Dark Red" if lectura.tt_maroon is not None else (
        "Light Red" if lectura.tt_red is not None else "no rojo")
    return ResultadoRegla(2, "TT: rojo (claro u oscuro)", cumple, f"tono actual: {tono}")


def regla4_trwave_short(lectura: LecturaIndicadores) -> ResultadoRegla:
    rojo_activo = lectura.trwave_maroon is not None or lectura.trwave_red is not None
    valor = lectura.trwave_maroon or lectura.trwave_red or 0
    cumple = rojo_activo and valor < 0
    return ResultadoRegla(4, "TR Wave: rojo y bajo nivel Zero", cumple,
                           f"rojo_activo={rojo_activo} valor={valor:.2f}")


def regla5_tsd_short(lectura: LecturaIndicadores) -> ResultadoRegla:
    cumple = lectura.tsd_yellow is not None and lectura.tsd_yellow > lectura.precio
    detalle = (f"yellow={lectura.tsd_yellow} precio={lectura.precio:.2f}"
               if lectura.tsd_yellow is not None else "no hay punto Yellow en esta vela")
    return ResultadoRegla(5, "TSD: punto Yellow arriba del precio", cumple, detalle)


def regla6_bbcloud_short(lectura: LecturaIndicadores) -> ResultadoRegla:
    if lectura.bb_superior is None:
        return ResultadoRegla(6, "Precio <= banda superior BB Cloud", False,
                               "dato no disponible (indicador BBCloud aun calculando)")
    cumple = lectura.precio <= lectura.bb_superior
    return ResultadoRegla(6, "Precio <= banda superior BB Cloud", cumple,
                           f"precio={lectura.precio:.2f} banda_superior={lectura.bb_superior:.2f}")


# =====================================================================
# EVALUACION COMPLETA (Type 1 y Type 2, Long y Short)
# =====================================================================

def evaluar_type1_long(lectura: LecturaIndicadores, umbrales: dict = None) -> ResultadoEvaluacion:
    resultado = ResultadoEvaluacion(lectura.symbol, lectura.timeframe,
                                     Direccion.LONG, TipoEntrada.TYPE_1)
    resultado.reglas = [
        regla1_cs_long(lectura),
        regla2_tt_long(lectura),
        regla3_trvi(lectura, umbrales),
        regla4_trwave_long(lectura),
        regla5_tsd_long(lectura),
        regla6_bbcloud_long(lectura),
    ]
    return resultado


def evaluar_type1_short(lectura: LecturaIndicadores, umbrales: dict = None) -> ResultadoEvaluacion:
    resultado = ResultadoEvaluacion(lectura.symbol, lectura.timeframe,
                                     Direccion.SHORT, TipoEntrada.TYPE_1)
    resultado.reglas = [
        regla1_cs_short(lectura),
        regla2_tt_short(lectura),
        regla3_trvi(lectura, umbrales),
        regla4_trwave_short(lectura),
        regla5_tsd_short(lectura),
        regla6_bbcloud_short(lectura),
    ]
    return resultado


def evaluar_type2_long(lectura_actual: LecturaIndicadores,
                        lectura_tf_superior: LecturaIndicadores,
                        umbrales: dict = None) -> ResultadoEvaluacion:
    """
    Type 2: mismas 6 reglas del timeframe actual, MAS la confirmacion
    de que las mismas condiciones (1-6) tambien se cumplen en el
    timeframe superior relacionado (ver TIMEFRAME_SUPERIOR).
    """
    resultado = evaluar_type1_long(lectura_actual, umbrales)
    resultado.tipo = TipoEntrada.TYPE_2

    confirmacion_superior = evaluar_type1_long(lectura_tf_superior, umbrales)
    cumple_confirmacion = confirmacion_superior.senal_completa
    resultado.reglas.append(ResultadoRegla(
        7, f"Confirmacion en timeframe superior ({lectura_tf_superior.timeframe})",
        cumple_confirmacion,
        confirmacion_superior.resumen()
    ))
    return resultado


def evaluar_type2_short(lectura_actual: LecturaIndicadores,
                         lectura_tf_superior: LecturaIndicadores,
                         umbrales: dict = None) -> ResultadoEvaluacion:
    resultado = evaluar_type1_short(lectura_actual, umbrales)
    resultado.tipo = TipoEntrada.TYPE_2

    confirmacion_superior = evaluar_type1_short(lectura_tf_superior, umbrales)
    cumple_confirmacion = confirmacion_superior.senal_completa
    resultado.reglas.append(ResultadoRegla(
        7, f"Confirmacion en timeframe superior ({lectura_tf_superior.timeframe})",
        cumple_confirmacion,
        confirmacion_superior.resumen()
    ))
    return resultado


# =====================================================================
# STOP LOSS / TAKE PROFIT (Reglas 8-9 del manual)
# =====================================================================

def calcular_sl_tp_long(lectura: LecturaIndicadores, pips_por_unidad: float = 1.0,
                         usar_trvi_para_tp: bool = False) -> dict:
    """
    SL: unos pips debajo del primer punto Aqua TSD.
    TP: igual al SL (1:1) o TRVI(pips) / 10, segun se configure.

    'pips_por_unidad' convierte la diferencia de precio a pips segun
    el simbolo (ajustar segun el activo: forex vs metales vs acciones).
    """
    if lectura.tsd_aqua is None:
        return {"error": "no hay punto Aqua para calcular SL"}

    entry = lectura.precio
    sl = lectura.tsd_aqua  # "unos pips debajo" -> se resta un buffer extra afuera de aqui
    riesgo_pips = (entry - sl) * pips_por_unidad

    if usar_trvi_para_tp:
        tp_pips = lectura.trvi_valor / 10
    else:
        tp_pips = riesgo_pips  # 1:1

    tp = entry + (tp_pips / pips_por_unidad)

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "riesgo_pips": riesgo_pips,
        "recompensa_pips": tp_pips,
    }


def calcular_sl_tp_short(lectura: LecturaIndicadores, pips_por_unidad: float = 1.0,
                          usar_trvi_para_tp: bool = False) -> dict:
    if lectura.tsd_yellow is None:
        return {"error": "no hay punto Yellow para calcular SL"}

    entry = lectura.precio
    sl = lectura.tsd_yellow
    riesgo_pips = (sl - entry) * pips_por_unidad

    if usar_trvi_para_tp:
        tp_pips = lectura.trvi_valor / 10
    else:
        tp_pips = riesgo_pips

    tp = entry - (tp_pips / pips_por_unidad)

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "riesgo_pips": riesgo_pips,
        "recompensa_pips": tp_pips,
    }


# =====================================================================
# EJEMPLO DE USO (con datos parecidos a los reales de Tesla que vimos)
# =====================================================================

if __name__ == "__main__":
    # Snapshot inspirado en los datos reales que confirmamos en Tesla
    # Weekly (mercado bajista: Dark Red activo en TT/TRWave, Yellow
    # activo en TSD)
    lectura_tesla = LecturaIndicadores(
        symbol="TESLA",
        timeframe="W1",
        precio=327.58,

        cs_magenta=34.79,
        cs_blanca=22.20,
        cs_blanca_prev=29.07,  # bajando

        tt_darkgreen=None,
        tt_maroon=1.0,      # Dark Red activo
        tt_lime=None,
        tt_red=None,

        trvi_valor=35997.0,

        trwave_darkgreen=None,
        trwave_maroon=-57.81,  # Dark Red activo, bajo Zero
        trwave_lime=None,
        trwave_red=None,

        tsd_aqua=None,
        tsd_yellow=376.96,   # arriba del precio actual (327.58)

        bb_inferior=352.63,
        bb_superior=417.94,
    )

    print("=" * 70)
    print("EJEMPLO: evaluando Short Type 1 sobre los datos reales de Tesla")
    print("=" * 70)

    resultado = evaluar_type1_short(lectura_tesla, umbrales={"TESLA": 1000})

    for r in resultado.reglas:
        estado = "CUMPLE" if r.cumple else "no cumple"
        print(f"  Regla {r.numero} [{estado}] {r.descripcion}")
        print(f"      -> {r.detalle}")

    print()
    print(resultado.resumen())

    if resultado.senal_completa:
        sl_tp = calcular_sl_tp_short(lectura_tesla)
        print(f"\nNiveles sugeridos: {sl_tp}")
