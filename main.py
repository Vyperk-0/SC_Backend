# -*- coding: utf-8 -*-
"""
Servidor backend para Railway.

Recibe los datos de indicadores que manda el EA (vía WebRequest desde
MT4) por HTTP, los evalua con el motor de reglas UPS, y devuelve el
resultado. Este es el primer eslabon del backend - las alertas por
WhatsApp y el chat con RAG se conectan aqui mismo mas adelante, sin
cambiar esta estructura base.

Correr localmente para probar:
    pip install -r requirements.txt
    uvicorn main:app --reload

Endpoint principal:
    POST /webhook/indicators
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
import logging

from ups_rules_engine import (
    LecturaIndicadores,
    evaluar_type1_long,
    evaluar_type1_short,
    evaluar_type2_long,
    evaluar_type2_short,
    calcular_sl_tp_long,
    calcular_sl_tp_short,
    ResultadoEvaluacion,
    TIMEFRAME_SUPERIOR,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ups-backend")

app = FastAPI(
    title="UPS Trading Backend",
    description="Motor de reglas UPS + webhook para el EA de MT4",
    version="0.1.0",
)


# =====================================================================
# ESQUEMA DE DATOS QUE MANDA EL EA (JSON del webhook)
# =====================================================================

class IndicadoresPayload(BaseModel):
    """
    Estructura del JSON que el EA en MT4 debe mandar por WebRequest().
    Los nombres coinciden 1:1 con los campos de LecturaIndicadores,
    usando el mapeo de buffers ya confirmado.
    """
    symbol: str
    timeframe: str
    precio: float

    cs_magenta: float
    cs_blanca: float
    cs_blanca_prev: float

    tt_darkgreen: Optional[float] = None
    tt_maroon: Optional[float] = None
    tt_lime: Optional[float] = None
    tt_red: Optional[float] = None

    trvi_valor: float

    trwave_darkgreen: Optional[float] = None
    trwave_maroon: Optional[float] = None
    trwave_lime: Optional[float] = None
    trwave_red: Optional[float] = None

    tsd_aqua: Optional[float] = None
    tsd_yellow: Optional[float] = None

    bb_inferior: float
    bb_superior: float

    # Opcional: si el EA tambien manda la lectura del timeframe
    # superior en el mismo request, se puede evaluar Type 2 de una vez.
    tf_superior: Optional["IndicadoresPayload"] = None


class ReglaOut(BaseModel):
    numero: int
    descripcion: str
    cumple: bool
    detalle: str


class EvaluacionOut(BaseModel):
    symbol: str
    timeframe: str
    direccion: str
    tipo: str
    reglas_cumplidas: int
    total_reglas: int
    senal_completa: bool
    reglas: List[ReglaOut]
    niveles: Optional[dict] = None


class WebhookResponse(BaseModel):
    symbol: str
    timeframe: str
    long_type1: EvaluacionOut
    short_type1: EvaluacionOut
    long_type2: Optional[EvaluacionOut] = None
    short_type2: Optional[EvaluacionOut] = None


# =====================================================================
# CONVERSION payload -> LecturaIndicadores
# =====================================================================

def payload_a_lectura(p: IndicadoresPayload) -> LecturaIndicadores:
    return LecturaIndicadores(
        symbol=p.symbol,
        timeframe=p.timeframe,
        precio=p.precio,
        cs_magenta=p.cs_magenta,
        cs_blanca=p.cs_blanca,
        cs_blanca_prev=p.cs_blanca_prev,
        tt_darkgreen=p.tt_darkgreen,
        tt_maroon=p.tt_maroon,
        tt_lime=p.tt_lime,
        tt_red=p.tt_red,
        trvi_valor=p.trvi_valor,
        trwave_darkgreen=p.trwave_darkgreen,
        trwave_maroon=p.trwave_maroon,
        trwave_lime=p.trwave_lime,
        trwave_red=p.trwave_red,
        tsd_aqua=p.tsd_aqua,
        tsd_yellow=p.tsd_yellow,
        bb_inferior=p.bb_inferior,
        bb_superior=p.bb_superior,
    )


def evaluacion_a_out(r: ResultadoEvaluacion, niveles: dict = None) -> EvaluacionOut:
    return EvaluacionOut(
        symbol=r.symbol,
        timeframe=r.timeframe,
        direccion=r.direccion.value,
        tipo=r.tipo.value,
        reglas_cumplidas=r.reglas_cumplidas,
        total_reglas=r.total_reglas,
        senal_completa=r.senal_completa,
        reglas=[ReglaOut(numero=x.numero, descripcion=x.descripcion,
                          cumple=x.cumple, detalle=x.detalle) for x in r.reglas],
        niveles=niveles,
    )


# =====================================================================
# LUGAR DONDE SE CONECTAN LAS ALERTAS (fase siguiente del roadmap)
# =====================================================================

# Lista de pares vigilados para alertas automaticas. Se define despues
# por el usuario; por ahora vacia = no manda alertas todavia.
PARES_VIGILADOS: List[str] = []


def procesar_senal_para_alerta(evaluacion: ResultadoEvaluacion, niveles: dict):
    """
    Punto de enganche para las alertas de WhatsApp (CallMeBot).
    Se implementa en la siguiente fase - por ahora solo loguea.
    """
    if evaluacion.symbol not in PARES_VIGILADOS:
        return
    if not evaluacion.senal_completa:
        return
    logger.info(f"[ALERTA PENDIENTE DE CONECTAR] {evaluacion.resumen()} niveles={niveles}")
    # TODO (fase 5 del roadmap): llamar aca a alertas/whatsapp.py


# =====================================================================
# ENDPOINTS
# =====================================================================

@app.get("/")
def health_check():
    return {"status": "ok", "service": "UPS Trading Backend"}


@app.post("/webhook/indicators", response_model=WebhookResponse)
def recibir_indicadores(payload: IndicadoresPayload):
    """
    Endpoint principal: el EA en MT4 manda aqui los valores de los
    6 indicadores UPS (via WebRequest) en cada vela nueva.
    """
    try:
        lectura = payload_a_lectura(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Payload invalido: {e}")

    # Evaluacion Type 1 (siempre se puede calcular con un solo timeframe)
    eval_long_1 = evaluar_type1_long(lectura)
    eval_short_1 = evaluar_type1_short(lectura)

    niveles_long = calcular_sl_tp_long(lectura) if eval_long_1.senal_completa else None
    niveles_short = calcular_sl_tp_short(lectura) if eval_short_1.senal_completa else None

    procesar_senal_para_alerta(eval_long_1, niveles_long)
    procesar_senal_para_alerta(eval_short_1, niveles_short)

    respuesta = WebhookResponse(
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        long_type1=evaluacion_a_out(eval_long_1, niveles_long),
        short_type1=evaluacion_a_out(eval_short_1, niveles_short),
    )

    # Evaluacion Type 2, solo si el EA mando tambien el timeframe superior
    if payload.tf_superior is not None:
        lectura_superior = payload_a_lectura(payload.tf_superior)

        esperado = TIMEFRAME_SUPERIOR.get(payload.timeframe)
        if esperado and payload.tf_superior.timeframe != esperado:
            logger.warning(
                f"tf_superior recibido ({payload.tf_superior.timeframe}) no coincide "
                f"con el esperado por el manual ({esperado}) para {payload.timeframe}"
            )

        eval_long_2 = evaluar_type2_long(lectura, lectura_superior)
        eval_short_2 = evaluar_type2_short(lectura, lectura_superior)

        niveles_long_2 = calcular_sl_tp_long(lectura) if eval_long_2.senal_completa else None
        niveles_short_2 = calcular_sl_tp_short(lectura) if eval_short_2.senal_completa else None

        procesar_senal_para_alerta(eval_long_2, niveles_long_2)
        procesar_senal_para_alerta(eval_short_2, niveles_short_2)

        respuesta.long_type2 = evaluacion_a_out(eval_long_2, niveles_long_2)
        respuesta.short_type2 = evaluacion_a_out(eval_short_2, niveles_short_2)

    return respuesta


@app.get("/pares-vigilados")
def obtener_pares_vigilados():
    return {"pares": PARES_VIGILADOS}
