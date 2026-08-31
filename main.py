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

import os
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
import logging
import io
import csv
from datetime import datetime, timezone

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
from backtesting import correr_backtest_sobre_filas, calcular_estadisticas
import db
from capturas import router as capturas_router
from usuarios import router as usuarios_router
from alertas import router as alertas_router, evaluar_y_disparar_alertas

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ups-backend")

app = FastAPI(
    title="UPS Trading Backend",
    description="Motor de reglas UPS + webhook para el EA de MT4",
    version="0.1.0",
)

# CORS: el frontend React se hostea en un dominio separado (Railway),
# asi que el navegador necesita permiso explicito para llamar a este
# backend desde ahi. Restringido al dominio real (via variable de
# entorno FRONTEND_ORIGINS, separados por coma si hay mas de uno -- ej.
# produccion + un dominio de preview) en vez de "*", ya que ahora hay
# cuentas de usuario reales y datos personales de por medio.
_frontend_origins_env = os.environ.get("FRONTEND_ORIGINS", "").strip()
if _frontend_origins_env:
    FRONTEND_ORIGINS = [o.strip() for o in _frontend_origins_env.split(",") if o.strip()]
else:
    # Sin configurar: se deja abierto para no romper el desarrollo
    # local/pruebas, pero bien avisado -- en produccion real hay que
    # configurar FRONTEND_ORIGINS en Railway con la URL exacta del
    # frontend (ej. https://ups-control-production.up.railway.app).
    FRONTEND_ORIGINS = ["*"]
    logger.warning(
        "FRONTEND_ORIGINS no configurada: CORS esta abierto a cualquier origen (*). "
        "Configura esta variable en Railway con la URL real de tu frontend para "
        "restringir el acceso, ahora que hay cuentas de usuario reales."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(capturas_router)
app.include_router(usuarios_router)
app.include_router(alertas_router)


@app.on_event("startup")
def inicializar():
    try:
        db.verificar_conexion()
        logger.info("Conexion a Postgres verificada. Las tablas se crean "
                    "automaticamente por simbolo/timeframe al subir historico.")
    except Exception as e:
        logger.warning(f"No se pudo conectar a la base de datos: {e}. "
                        f"Los endpoints de historico/backtesting no funcionaran "
                        f"hasta que agregues/conectes un servicio Postgres en Railway.")


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

    cs_magenta: Optional[float] = None
    cs_blanca: Optional[float] = None
    cs_blanca_prev: Optional[float] = None

    tt_darkgreen: Optional[float] = None
    tt_maroon: Optional[float] = None
    tt_lime: Optional[float] = None
    tt_red: Optional[float] = None

    trvi_valor: Optional[float] = None

    trwave_darkgreen: Optional[float] = None
    trwave_maroon: Optional[float] = None
    trwave_lime: Optional[float] = None
    trwave_red: Optional[float] = None

    tsd_aqua: Optional[float] = None
    tsd_yellow: Optional[float] = None

    bb_inferior: Optional[float] = None
    bb_superior: Optional[float] = None

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

# Estado en vivo: guarda el ULTIMO resultado calculado para cada
# combinacion (symbol, timeframe), asi el panel puede mostrarlo sin
# tener que esperar a un nuevo request del EA. Vive en memoria (se
# reinicia si Railway redeploya el servicio) - el EA lo vuelve a
# llenar solo en el siguiente ciclo (60s), asi que no es un problema
# real en la practica.
ESTADO_VIVO: dict = {}

# Lista fija de simbolos que se muestran en el panel. Tiene que
# coincidir con el input ParesAMonitorear del EA -- si agregas o
# sacas un simbolo del EA, hay que actualizar esta lista tambien
# (a mano, aca). No se basa en tiempo/actividad reciente: lo que no
# esta en esta lista simplemente no se muestra nunca, y lo que SI
# esta se muestra siempre (aunque el EA este momentaneamente caido),
# asi no hay parpadeos raros por reinicios o cortes breves.
SIMBOLOS_ACTIVOS = {
    "ADOBE", "ALCOA", "ALIBABA", "AMAZON", "AMD", "AMEX", "APPLE", "BOA",
    "BOEING", "BOOKING", "CHEVRON", "CISCO", "CITI", "COKE", "Coinbase",
    "DEVON", "DISNEY", "EBAY", "EXXON", "FORD", "GE", "GOOGLE", "GS",
    "HLT", "IBM", "ILMN", "INTEL", "JNJ", "JPMORGAN", "LAM", "MCARD",
    "MCDON", "META", "MICROCHIP", "MICRON", "MODERNA", "MSFT", "NIKE",
    "NVIDIA", "Netflix", "PAYPAL", "PEPSI", "PFIZER", "QCOM",
    "SALESFORCE", "STARBUCKS", "TEVA", "Tesla", "UBER", "VISA",
    "AUDUSD", "EURUSD", "USDJPY", "GBPUSD", "USDCHF", "USDCAD",
    "EURGBP", "XAUEUR", "XAUUSD", "XAGEUR", "XAGUSD",
    "#ADAUSDr", "#BNBUSDr", "#BTCUSDr", "#DOGEUSDr", "#ETHUSDr",
    "#LTCUSDr", "#SOLUSDr", "#TRXUSDr", "#XRPUSDr",
}


def procesar_senal_para_alerta(evaluacion: ResultadoEvaluacion, niveles: dict):
    """
    Punto de enganche de las alertas de WhatsApp configurables por
    usuario (ver alertas.py). Se llama en CADA evaluacion (no solo
    cuando esta completa) porque una regla de alerta puede pedir un
    subconjunto de indicadores, no necesariamente los 6 -- el filtrado
    real pasa adentro de evaluar_y_disparar_alertas().
    """
    evaluar_y_disparar_alertas(evaluacion, niveles)


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

    # Guardamos el estado ANTERIOR (si existia) antes de tocar nada, para
    # poder comparar despues y detectar transiciones de "no completa" a
    # "completa" -- eso es lo unico que se registra en el historial diario,
    # no cada llamada del EA mientras la senal se mantiene completa.
    clave = (payload.symbol, payload.timeframe)
    estado_previo = ESTADO_VIVO.get(clave)

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

    # Guardar el resultado en el estado en vivo, para que el panel
    # pueda mostrarlo sin depender de leer los Logs de Railway.
    # Se guarda el desglose COMPLETO de reglas (no solo el resumen)
    # para poder abrir el detalle de cada activo en el panel.
    def reglas_a_dict(reglas):
        return [{"numero": r.numero, "descripcion": r.descripcion,
                  "cumple": r.cumple, "detalle": r.detalle} for r in reglas]

    clave = (payload.symbol, payload.timeframe)
    ESTADO_VIVO[clave] = {
        "symbol": payload.symbol,
        "timeframe": payload.timeframe,
        "precio": payload.precio,
        "long_t1": f"{eval_long_1.reglas_cumplidas}/{eval_long_1.total_reglas}",
        "short_t1": f"{eval_short_1.reglas_cumplidas}/{eval_short_1.total_reglas}",
        "long_t1_completa": eval_long_1.senal_completa,
        "short_t1_completa": eval_short_1.senal_completa,
        "long_t1_reglas": reglas_a_dict(eval_long_1.reglas),
        "short_t1_reglas": reglas_a_dict(eval_short_1.reglas),
        "long_t2": (f"{respuesta.long_type2.reglas_cumplidas}/{respuesta.long_type2.total_reglas}"
                    if respuesta.long_type2 else None),
        "short_t2": (f"{respuesta.short_type2.reglas_cumplidas}/{respuesta.short_type2.total_reglas}"
                     if respuesta.short_type2 else None),
        "long_t2_completa": respuesta.long_type2.senal_completa if respuesta.long_type2 else False,
        "short_t2_completa": respuesta.short_type2.senal_completa if respuesta.short_type2 else False,
        "long_t2_reglas": reglas_a_dict(eval_long_2.reglas) if payload.tf_superior else None,
        "short_t2_reglas": reglas_a_dict(eval_short_2.reglas) if payload.tf_superior else None,
        "niveles_long": niveles_long if eval_long_1.senal_completa else None,
        "niveles_short": niveles_short if eval_short_1.senal_completa else None,
        # Valores crudos de cada indicador, para la pagina de detalle
        # (mostrar el indicador en si, no solo el resultado de la regla).
        "indicadores": {
            "cs_magenta": lectura.cs_magenta, "cs_blanca": lectura.cs_blanca,
            "tt_darkgreen": lectura.tt_darkgreen, "tt_maroon": lectura.tt_maroon,
            "tt_lime": lectura.tt_lime, "tt_red": lectura.tt_red,
            "trvi_valor": lectura.trvi_valor,
            "trwave_darkgreen": lectura.trwave_darkgreen, "trwave_maroon": lectura.trwave_maroon,
            "trwave_lime": lectura.trwave_lime, "trwave_red": lectura.trwave_red,
            "tsd_aqua": lectura.tsd_aqua, "tsd_yellow": lectura.tsd_yellow,
            "bb_inferior": lectura.bb_inferior, "bb_superior": lectura.bb_superior,
        },
        "actualizado": datetime.now(timezone.utc),
    }

    # Si alguna senal paso de "no completa" a "completa" en esta vuelta,
    # se registra UN evento en la base para el historial diario. Se
    # compara contra estado_previo (capturado al principio, antes de
    # tocar nada) para no contar de nuevo una senal que ya estaba
    # completa en el webhook anterior.
    def _estaba_completa_antes(campo):
        return bool(estado_previo and estado_previo.get(campo))

    eventos_nuevos = []
    if eval_long_1.senal_completa and not _estaba_completa_antes("long_t1_completa"):
        eventos_nuevos.append((payload.symbol, payload.timeframe, "T1", "long"))
    if eval_short_1.senal_completa and not _estaba_completa_antes("short_t1_completa"):
        eventos_nuevos.append((payload.symbol, payload.timeframe, "T1", "short"))
    if payload.tf_superior:
        if respuesta.long_type2 and respuesta.long_type2.senal_completa and not _estaba_completa_antes("long_t2_completa"):
            eventos_nuevos.append((payload.symbol, payload.timeframe, "T2", "long"))
        if respuesta.short_type2 and respuesta.short_type2.senal_completa and not _estaba_completa_antes("short_t2_completa"):
            eventos_nuevos.append((payload.symbol, payload.timeframe, "T2", "short"))

    for symbol_ev, tf_ev, tipo_ev, direccion_ev in eventos_nuevos:
        try:
            db.registrar_evento_senal(symbol_ev, tf_ev, tipo_ev, direccion_ev)
        except Exception as e:
            # Nunca romper el webhook por un problema al guardar el
            # historial diario -- el EA depende de que esto responda 200.
            logger.warning(f"No se pudo registrar evento de senal ({symbol_ev} {tf_ev} {tipo_ev} {direccion_ev}): {e}")

    return respuesta


@app.get("/upload", response_class=HTMLResponse)
def panel_unificado():
    """
    Panel de control del sistema UPS: cargar historico, ver que hay
    disponible, correr backtest, y monitorear el estado en vivo -
    todo en un solo lugar, con navegacion por pestanas. Accesible
    desde cualquier navegador (incluido el celular) apuntando a
    https://TU-URL.up.railway.app/upload
    """
    return """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UPS Control</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0A0E1A;
    --surface: #12182B;
    --surface-alt: #1A2238;
    --border: #232B42;
    --accent: #E8A33D;
    --accent-dim: #8A6526;
    --text: #E6E9F0;
    --text-muted: #7C8599;
    --long: #3DDC84;
    --short: #FF5C5C;
    --radius: 10px;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: 'Inter', sans-serif; min-height: 100vh;
  }
  h1, h2, h3, .display { font-family: 'Space Grotesk', sans-serif; }
  .mono { font-family: 'JetBrains Mono', monospace; }

  .layout { display: flex; min-height: 100vh; }

  /* ---------- SIDEBAR ---------- */
  .sidebar {
    width: 220px; flex-shrink: 0; background: var(--surface);
    border-right: 1px solid var(--border); padding: 1.5rem 1rem;
    display: flex; flex-direction: column; gap: 0.3rem;
  }
  .brand {
    font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 1.15rem;
    letter-spacing: 0.02em; margin: 0 0 0.2rem 0.4rem;
  }
  .brand span { color: var(--accent); }
  .brand-sub {
    font-size: 0.7rem; color: var(--text-muted); margin: 0 0 1.5rem 0.4rem;
    text-transform: uppercase; letter-spacing: 0.08em;
  }
  .nav-item {
    display: flex; align-items: center; gap: 0.6rem; padding: 0.65rem 0.7rem;
    border-radius: var(--radius); cursor: pointer; color: var(--text-muted);
    font-size: 0.88rem; font-weight: 500; transition: 0.15s; border: 1px solid transparent;
  }
  .nav-item:hover { background: var(--surface-alt); color: var(--text); }
  .nav-item.activo { background: var(--surface-alt); color: var(--text); border-color: var(--border); }
  .nav-item .num { font-family: 'JetBrains Mono', monospace; color: var(--accent-dim); font-size: 0.78rem; }
  .nav-item.activo .num { color: var(--accent); }

  .pulse {
    width: 7px; height: 7px; border-radius: 50%; background: var(--long);
    margin-left: auto; box-shadow: 0 0 0 0 rgba(61,220,132,0.6);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(61,220,132,0.55); }
    70%  { box-shadow: 0 0 0 7px rgba(61,220,132,0); }
    100% { box-shadow: 0 0 0 0 rgba(61,220,132,0); }
  }

  /* ---------- MAIN ---------- */
  .main { flex: 1; padding: 2rem 2.5rem; max-width: 1600px; margin: 0 auto; width: 100%; }
  .panel { display: none; }
  .panel.activo { display: block; animation: fade 0.25s ease; }
  @keyframes fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

  .panel-header { margin-bottom: 1.5rem; }
  .panel-header h2 { font-size: 1.4rem; margin: 0 0 0.3rem; }
  .panel-header p { color: var(--text-muted); font-size: 0.87rem; margin: 0; }

  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 1.3rem 1.4rem; margin-bottom: 1rem;
  }
  .card h3 { font-size: 0.95rem; margin: 0 0 0.9rem; color: var(--text); font-weight: 600; }

  label { display: block; margin: 0.7rem 0 0.35rem; font-size: 0.78rem; color: var(--text-muted); }
  input[type=text] {
    width: 100%; padding: 0.6rem 0.7rem; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-family: 'JetBrains Mono', monospace;
    font-size: 0.92rem;
  }
  input[type=text]:focus { outline: none; border-color: var(--accent-dim); }

  #drop {
    border: 1.5px dashed var(--border); border-radius: var(--radius); padding: 1.8rem 1rem;
    text-align: center; margin-top: 0.8rem; cursor: pointer; transition: 0.2s;
  }
  #drop.hover { border-color: var(--accent); background: rgba(232,163,61,0.05); }
  #drop p { margin: 0; color: var(--text-muted); font-size: 0.85rem; }

  button {
    padding: 0.65rem 1.1rem; border: none; border-radius: 8px;
    background: var(--accent); color: #1A1200; font-weight: 600; cursor: pointer;
    font-size: 0.88rem; font-family: 'Inter', sans-serif; transition: 0.15s;
  }
  button:hover { filter: brightness(1.08); }
  button:disabled { background: var(--surface-alt); color: var(--text-muted); cursor: not-allowed; }
  button.ancho { width: 100%; margin-top: 1rem; }
  button.ghost {
    background: transparent; border: 1px solid var(--border); color: var(--text-muted);
    padding: 0.35rem 0.7rem; font-size: 0.76rem; margin: 0;
  }

  #estado-upload, #estado-backtest { margin-top: 0.7rem; font-size: 0.82rem; }
  .ok { color: var(--long); } .error { color: var(--short); }

  table { width: 100%; border-collapse: collapse; font-size: 0.83rem; }
  th, td { text-align: left; padding: 0.55rem 0.5rem; border-bottom: 1px solid var(--border); }
  th { color: var(--text-muted); font-weight: 500; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }
  td { font-family: 'JetBrains Mono', monospace; }
  tr.clickable { cursor: pointer; }
  tr.clickable:hover td { background: var(--surface-alt); }
  .vacio { color: var(--text-muted); font-size: 0.85rem; text-align: center; padding: 1.5rem 0; }

  .section-title-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.9rem; }

  .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem; margin-top: 1rem; }
  .stat-box { background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem; }
  .stat-box h4 { margin: 0 0 0.7rem; font-size: 0.82rem; font-family: 'Space Grotesk', sans-serif; }
  .stat-box.long h4 { color: var(--long); } .stat-box.short h4 { color: var(--short); }
  .stat-row { display: flex; justify-content: space-between; font-size: 0.8rem; padding: 0.2rem 0; color: var(--text-muted); }
  .stat-row b { color: var(--text); font-family: 'JetBrains Mono', monospace; }

  .badge-ok { color: var(--long); font-weight: 700; }
  .badge-off { color: var(--text-muted); }

  .leyenda {
    display: flex; flex-wrap: wrap; gap: 0.9rem; margin-top: 0.8rem;
    padding-top: 0.8rem; border-top: 1px solid var(--border);
    font-size: 0.72rem; color: var(--text-muted);
  }
  .leyenda b { color: var(--text); }
  .badge-short { color: var(--short); font-weight: 700; }

  .filtros {
    display: flex; gap: 0.6rem; margin: 1rem 0 1rem; flex-wrap: wrap;
  }
  .filtros input[type=text] { flex: 1; min-width: 180px; margin: 0; }
  .filtros select {
    padding: 0.6rem 0.7rem; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif;
    font-size: 0.85rem; cursor: pointer;
  }
  .filtros select:focus { outline: none; border-color: var(--accent-dim); }

  #tabla-vivo, #tabla-resumen {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 0.8rem;
  }
  .grupo-activo {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 0.75rem 0.85rem;
  }
  .grupo-activo-titulo {
    font-family: 'Space Grotesk', sans-serif; font-weight: 600; font-size: 0.88rem;
    color: var(--accent); margin-bottom: 0.55rem; letter-spacing: 0.02em;
  }
  .tf-block { margin-bottom: 0.6rem; }
  .tf-block:last-child { margin-bottom: 0; }
  .tf-header {
    display: flex; justify-content: space-between; align-items: baseline;
    font-size: 0.72rem; color: var(--text-muted); margin-bottom: 0.3rem;
    border-bottom: 1px solid var(--border); padding-bottom: 0.2rem;
  }
  .tf-header .tf-nombre { color: var(--text); font-weight: 600; font-family: 'JetBrains Mono', monospace; }
  .tipo-row {
    display: flex; align-items: center; gap: 0.4rem; font-size: 0.74rem;
    font-family: 'JetBrains Mono', monospace; margin-bottom: 0.15rem;
  }
  .tipo-row:last-child { margin-bottom: 0; }
  .tipo-tag { color: var(--text-muted); font-family: 'Inter', sans-serif; width: 18px; flex-shrink: 0; font-size: 0.68rem; }
  .flecha-long { color: var(--text-muted); }
  .flecha-long.completa { color: var(--long); font-weight: 700; }
  .flecha-short { color: var(--text-muted); }
  .flecha-short.completa { color: var(--short); font-weight: 700; }

  .clickable-titulo { cursor: pointer; }
  .clickable-titulo:hover { color: var(--accent); }

  /* ---------- PAGINA DE DETALLE POR ACTIVO ---------- */
  #panel-detalle { }
  .detalle-topbar {
    display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem;
  }
  .detalle-topbar h2 {
    margin: 0; font-size: 1.4rem; font-family: 'JetBrains Mono', monospace; color: var(--accent);
  }

  .tf-pills { display: flex; gap: 0.5rem; margin-bottom: 1.3rem; flex-wrap: wrap; }
  .tf-pill {
    padding: 0.4rem 0.9rem; border-radius: 999px; border: 1px solid var(--border);
    background: var(--surface); color: var(--text-muted); font-size: 0.8rem;
    font-family: 'JetBrains Mono', monospace; cursor: pointer; transition: 0.15s;
  }
  .tf-pill:hover { border-color: var(--accent-dim); }
  .tf-pill.activo { background: var(--accent); color: #1A1200; border-color: var(--accent); font-weight: 700; }

  .detalle-precio-row {
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 1.2rem; padding-bottom: 0.9rem; border-bottom: 1px solid var(--border);
  }
  .detalle-precio-valor { font-size: 1.6rem; font-family: 'JetBrains Mono', monospace; font-weight: 600; }
  .detalle-actualizado { font-size: 0.76rem; color: var(--text-muted); }

  /* Grid de cajas, una por indicador */
  .indicadores-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 0.8rem; margin-bottom: 1.3rem;
  }
  .indicador-box {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 0.9rem 1rem;
  }
  .indicador-box .ind-nombre {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--text-muted); margin-bottom: 0.5rem;
  }
  .indicador-box .ind-valor-principal {
    font-family: 'JetBrains Mono', monospace; font-size: 1.15rem; font-weight: 600;
    display: flex; align-items: center; gap: 0.4rem;
  }
  .indicador-box .ind-sub {
    font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--text-muted); margin-top: 0.3rem;
  }
  .ind-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
  .ind-dot.dg { background: #1a7a3a; } .ind-dot.mn { background: #7a1a1a; }
  .ind-dot.li { background: #3ddc84; } .ind-dot.rd { background: #ff5c5c; }
  .ind-dot.aq { background: #38bdf8; } .ind-dot.ye { background: #eab308; }
  .ind-dot.off { background: var(--border); }

  /* Resumen compacto de reglas: solo iconos, sin texto largo */
  .reglas-resumen-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.8rem;
  }
  .reglas-box {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.9rem 1rem;
  }
  .reglas-box.completa { border-color: var(--long); }
  .reglas-box-header {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.6rem;
    font-size: 0.8rem; font-weight: 600;
  }
  .reglas-iconos { display: flex; gap: 0.35rem; }
  .regla-icono {
    width: 22px; height: 22px; border-radius: 6px; display: flex; align-items: center;
    justify-content: center; font-size: 0.75rem; font-weight: 700;
    background: var(--bg); color: var(--text-muted); border: 1px solid var(--border);
  }
  .regla-icono.ok { background: rgba(61,220,132,0.15); color: var(--long); border-color: var(--long); }
  .reglas-box-niveles {
    margin-top: 0.6rem; padding-top: 0.6rem; border-top: 1px solid var(--border);
    font-family: 'JetBrains Mono', monospace; font-size: 0.74rem; color: var(--text-muted);
    display: flex; gap: 1rem;
  }

  .mobile-tabs { display: none; }

  /* Pantallas medianas/tablet: sidebar mas angosto, tarjetas se acomodan solas via el grid */
  @media (max-width: 1100px) and (min-width: 781px) {
    .sidebar { width: 180px; padding: 1.2rem 0.7rem; }
    .main { padding: 1.6rem 1.4rem; }
  }

  @media (max-width: 780px) {
    .layout { flex-direction: column; }
    .sidebar {
      width: 100%; flex-direction: row; overflow-x: auto; padding: 1rem;
      border-right: none; border-bottom: 1px solid var(--border);
    }
    .brand, .brand-sub { display: none; }
    .nav-item { flex-shrink: 0; }
    .main { padding: 1.3rem 1rem; max-width: 100%; }
    .stats-grid { grid-template-columns: 1fr; }
    #tabla-vivo, #tabla-resumen { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0.5rem; }
  }
</style>
</head>
<body>

<div class="layout">

  <div class="sidebar">
    <div class="brand">UPS <span>Control</span></div>
    <div class="brand-sub">Sistema de senales</div>

    <div class="nav-item activo" data-tab="cargar" onclick="cambiarTab('cargar')">
      <span class="num">01</span> Resultados
    </div>
    <div class="nav-item" data-tab="datos" onclick="cambiarTab('datos')">
      <span class="num">02</span> Historico
    </div>
    <div class="nav-item" data-tab="vivo" onclick="cambiarTab('vivo')">
      <span class="num">03</span> En vivo <span class="pulse"></span>
    </div>
  </div>

  <div class="main">

    <!-- PANEL 1: RESULTADOS DEL BACKTEST (agrupado por activo, estilo En Vivo) -->
    <div class="panel activo" id="panel-cargar">
      <div class="panel-header">
        <div class="section-title-row">
          <div>
            <h2 style="margin:0 0 0.3rem">Resultados del backtest</h2>
            <p style="margin:0">Rendimiento historico de cada activo/timeframe ya cargado en Postgres.</p>
          </div>
          <button class="ghost" id="btn-cargar-resumen" onclick="cargarResumenBacktest()">Cargar resultados</button>
        </div>
        <div class="leyenda">
          <span><span class="badge-ok">&uarr;</span> Long</span>
          <span><span class="badge-short">&darr;</span> Short</span>
          <span><b>%</b> = % de acierto</span>
          <span><b>Neto</b> = pips acumulados (positivo = ganancia)</span>
        </div>
      </div>
      <div class="filtros">
        <input type="text" id="buscar-resumen" placeholder="Buscar simbolo..." oninput="aplicarFiltro('tabla-resumen','buscar-resumen','categoria-resumen')">
        <select id="categoria-resumen" onchange="aplicarFiltro('tabla-resumen','buscar-resumen','categoria-resumen')">
          <option value="">Todas las categorias</option>
          <option value="Shares US">Shares US</option>
          <option value="Forex">Forex</option>
          <option value="Oro">Oro</option>
          <option value="Silver">Silver</option>
          <option value="Crypto">Crypto</option>
        </select>
      </div>
      <div id="tabla-resumen"><p class="vacio">Toca "Cargar resultados" para calcular (puede tardar segun cuantos activos tengas cargados).</p></div>
    </div>

    <!-- PANEL 2b (oculto, ya no se usa como pestana - subida disponible via API si hace falta) -->
    <div class="panel" id="panel-subir-legacy" style="display:none">
      <div class="card">
        <label for="up-symbol">Simbolo</label>
        <input type="text" id="up-symbol" placeholder="XAGUSD">
        <label for="up-timeframe">Timeframe</label>
        <input type="text" id="up-timeframe" placeholder="W1">
        <label>Archivo CSV</label>
        <div id="drop">
          <p id="drop-texto">Arrastra el CSV aqui, o toca para elegirlo</p>
          <input type="file" id="archivo" accept=".csv" style="display:none">
        </div>
        <button class="ancho" id="btn-subir" disabled>Subir</button>
        <div id="estado-upload"></div>
      </div>
    </div>

    <!-- PANEL 2: DATOS DISPONIBLES -->
    <div class="panel" id="panel-datos">
      <div class="panel-header">
        <h2>Historico y backtest puntual</h2>
        <p>Pares y timeframes ya cargados en la base de datos. Toca una fila para correr su backtest abajo.</p>
      </div>
      <div class="card">
        <div class="section-title-row">
          <h3 style="margin:0">Tablas cargadas</h3>
          <button class="ghost" onclick="cargarDisponibles()">Actualizar</button>
        </div>
        <div id="tabla-disponibles"><p class="vacio">Cargando...</p></div>
      </div>
      <div class="card">
        <h3>Correr backtest puntual</h3>
        <label for="bt-symbol">Simbolo</label>
        <input type="text" id="bt-symbol" placeholder="XAGUSD">
        <label for="bt-timeframe">Timeframe</label>
        <input type="text" id="bt-timeframe" placeholder="W1">
        <button class="ancho" id="btn-backtest">Correr backtest</button>
        <div id="estado-backtest"></div>
        <div id="resultado-backtest"></div>
      </div>
    </div>

    <!-- PANEL 3: EN VIVO -->
    <div class="panel" id="panel-vivo">
      <div class="panel-header">
        <div class="section-title-row">
          <div>
            <h2 style="margin:0 0 0.3rem">Estado en vivo</h2>
            <p style="margin:0">Cumplimiento de reglas ahora mismo. Se actualiza sola cada 20s.</p>
          </div>
          <button class="ghost" onclick="cargarEstadoVivo()">Actualizar</button>
        </div>
        <div class="leyenda">
          <span><span class="badge-ok">&uarr;</span> Long</span>
          <span><span class="badge-short">&darr;</span> Short</span>
          <span><b>T1</b> = Type 1</span>
          <span><b>T2</b> = Type 2 (confirmado en TF superior)</span>
          <span><span class="badge-ok">&check;</span> = todas las reglas cumplidas</span>
        </div>
      </div>
      <div class="filtros">
        <input type="text" id="buscar-vivo" placeholder="Buscar simbolo..." oninput="aplicarFiltro('tabla-vivo','buscar-vivo','categoria-vivo')">
        <select id="categoria-vivo" onchange="aplicarFiltro('tabla-vivo','buscar-vivo','categoria-vivo')">
          <option value="">Todas las categorias</option>
          <option value="Shares US">Shares US</option>
          <option value="Forex">Forex</option>
          <option value="Oro">Oro</option>
          <option value="Silver">Silver</option>
          <option value="Crypto">Crypto</option>
        </select>
      </div>
      <div id="tabla-vivo"><p class="vacio">Cargando...</p></div>
    </div>

    <!-- PANEL DE DETALLE (pagina dedicada por activo, no es una pestana del sidebar) -->
    <div class="panel" id="panel-detalle">
      <div class="detalle-topbar">
        <button class="ghost" onclick="volverDeDetalle()">&larr; Volver</button>
        <h2 id="detalle-titulo">-</h2>
      </div>
      <div class="tf-pills" id="detalle-tf-pills"></div>
      <div id="detalle-contenido"><p class="vacio">Cargando...</p></div>
    </div>

  </div>
</div>

<script>
// ---------- MAPA DE CATEGORIAS (para buscador y filtros) ----------
const CATEGORIAS = {
  "ADOBE":"Shares US","ALCOA":"Shares US","ALIBABA":"Shares US","AMAZON":"Shares US","AMD":"Shares US",
  "AMEX":"Shares US","APPLE":"Shares US","BOA":"Shares US","BOEING":"Shares US","BOOKING":"Shares US",
  "CHEVRON":"Shares US","CISCO":"Shares US","CITI":"Shares US","COKE":"Shares US","Coinbase":"Shares US",
  "DEVON":"Shares US","DISNEY":"Shares US","EBAY":"Shares US","EXXON":"Shares US","FORD":"Shares US",
  "GE":"Shares US","GOOGLE":"Shares US","GS":"Shares US","HLT":"Shares US","IBM":"Shares US",
  "ILMN":"Shares US","INTEL":"Shares US","JNJ":"Shares US","JPMORGAN":"Shares US","LAM":"Shares US",
  "MCARD":"Shares US","MCDON":"Shares US","META":"Shares US","MICROCHIP":"Shares US","MICRON":"Shares US",
  "MODERNA":"Shares US","MSFT":"Shares US","NIKE":"Shares US","NVIDIA":"Shares US","Netflix":"Shares US",
  "PAYPAL":"Shares US","PEPSI":"Shares US","PFIZER":"Shares US","QCOM":"Shares US","SALESFORCE":"Shares US",
  "STARBUCKS":"Shares US","TEVA":"Shares US","Tesla":"Shares US","UBER":"Shares US","VISA":"Shares US",
  "AUDCAD":"Forex","AUDCHF":"Forex","AUDJPY":"Forex","AUDNZD":"Forex","AUDSGD":"Forex","AUDUSD":"Forex",
  "CADCHF":"Forex","CADJPY":"Forex","CHFJPY":"Forex","CHFPLN":"Forex","CHFSGD":"Forex","EURAUD":"Forex",
  "EURCAD":"Forex","EURCHF":"Forex","EURDKK":"Forex","EURGBP":"Forex","EURHUF":"Forex","EURJPY":"Forex",
  "EURNOK":"Forex","EURNZD":"Forex","EURPLN":"Forex","EURSGD":"Forex","EURUSD":"Forex","EURZAR":"Forex",
  "GBPAUD":"Forex","GBPCAD":"Forex","GBPCHF":"Forex","GBPJPY":"Forex","GBPNZD":"Forex","GBPPLN":"Forex",
  "GBPSGD":"Forex","GBPUSD":"Forex","GBPZAR":"Forex","NZDCAD":"Forex","NZDCHF":"Forex","NZDJPY":"Forex",
  "NZDUSD":"Forex","SGDJPY":"Forex","USDAED":"Forex","USDAEDr":"Forex","USDCAD":"Forex","USDCHF":"Forex",
  "USDCNH":"Forex","USDCZK":"Forex","USDDKK":"Forex","USDGHS":"Forex","USDHKD":"Forex","USDHUF":"Forex",
  "USDIDR":"Forex","USDJPY":"Forex","USDKES":"Forex","USDMXN":"Forex","USDNGN":"Forex","USDNOK":"Forex",
  "USDPLN":"Forex","USDRUB":"Forex","USDSEK":"Forex","USDSGD":"Forex","USDTHB":"Forex","USDTRY":"Forex",
  "USDZAR":"Forex","ZARJPY":"Forex",
  "XAUEUR":"Oro","XAUUSD":"Oro",
  "XAGEUR":"Silver","XAGUSD":"Silver",
  "#ADAUSDr":"Crypto","#BNBEURr":"Crypto","#BNBJPYr":"Crypto","#BNBUSDr":"Crypto","#BTCEURr":"Crypto",
  "#BTCJPYr":"Crypto","#BTCUSDr":"Crypto","#DOGEUSDr":"Crypto","#ETHUSDr":"Crypto","#LTCUSDr":"Crypto",
  "#SOLUSDr":"Crypto","#TRXUSDr":"Crypto","#XRPEURr":"Crypto","#XRPUSDr":"Crypto"
};

function obtenerCategoria(symbol) {
  return CATEGORIAS[symbol] || "Otros";
}

function aplicarFiltro(containerId, buscarId, categoriaId) {
  const texto = document.getElementById(buscarId).value.trim().toUpperCase();
  const categoria = document.getElementById(categoriaId).value;
  const tarjetas = document.querySelectorAll(`#${containerId} .grupo-activo`);
  let visibles = 0;

  tarjetas.forEach(t => {
    const symbol = (t.dataset.symbol || '').toUpperCase();
    const cat = t.dataset.categoria || '';
    const coincideTexto = texto === '' || symbol.includes(texto);
    const coincideCategoria = categoria === '' || cat === categoria;
    const mostrar = coincideTexto && coincideCategoria;
    t.style.display = mostrar ? '' : 'none';
    if (mostrar) visibles++;
  });

  const msgId = containerId + '-sin-resultados';
  let msg = document.getElementById(msgId);
  if (visibles === 0 && tarjetas.length > 0) {
    if (!msg) {
      msg = document.createElement('p');
      msg.id = msgId;
      msg.className = 'vacio';
      msg.textContent = 'Ningun activo coincide con el filtro.';
      document.getElementById(containerId).appendChild(msg);
    }
  } else if (msg) {
    msg.remove();
  }
}

// ---------- NAVEGACION DE PESTANAS ----------
function cambiarTab(tab) {
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('activo'));
  document.querySelectorAll('.panel').forEach(el => el.classList.remove('activo'));
  document.querySelector(`.nav-item[data-tab="${tab}"]`).classList.add('activo');
  document.getElementById(`panel-${tab}`).classList.add('activo');
}

// ---------- SUBIR CSV ----------
const drop = document.getElementById('drop');
const input = document.getElementById('archivo');
const btnSubir = document.getElementById('btn-subir');
const estadoUpload = document.getElementById('estado-upload');
const dropTexto = document.getElementById('drop-texto');
let archivoSeleccionado = null;

drop.addEventListener('click', () => input.click());
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('hover'); });
drop.addEventListener('dragleave', () => drop.classList.remove('hover'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('hover');
  if (e.dataTransfer.files.length) seleccionarArchivo(e.dataTransfer.files[0]);
});
input.addEventListener('change', () => {
  if (input.files.length) seleccionarArchivo(input.files[0]);
});

function seleccionarArchivo(archivo) {
  archivoSeleccionado = archivo;
  dropTexto.textContent = archivo.name;
  btnSubir.disabled = false;
}

btnSubir.addEventListener('click', async () => {
  const symbol = document.getElementById('up-symbol').value.trim().toUpperCase();
  const timeframe = document.getElementById('up-timeframe').value.trim().toUpperCase();

  if (!symbol || !timeframe || !archivoSeleccionado) {
    estadoUpload.textContent = "Completa simbolo, timeframe, y elige un archivo.";
    estadoUpload.className = "error";
    return;
  }

  btnSubir.disabled = true;
  estadoUpload.textContent = "Subiendo...";
  estadoUpload.className = "";

  const formData = new FormData();
  formData.append("archivo", archivoSeleccionado);

  try {
    const resp = await fetch(`/historico/upload?symbol=${symbol}&timeframe=${timeframe}`, {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();

    if (resp.ok) {
      estadoUpload.textContent = `${data.velas_guardadas} velas guardadas para ${data.symbol} ${data.timeframe}`;
      estadoUpload.className = "ok";
      dropTexto.textContent = "Arrastra el CSV aqui, o toca para elegirlo";
      archivoSeleccionado = null;
      cargarDisponibles();
    } else {
      estadoUpload.textContent = "Error: " + (data.detail || "desconocido");
      estadoUpload.className = "error";
    }
  } catch (err) {
    estadoUpload.textContent = "Error de conexion: " + err;
    estadoUpload.className = "error";
  }

  btnSubir.disabled = false;
});

// ---------- DATOS DISPONIBLES ----------
async function cargarDisponibles() {
  const cont = document.getElementById('tabla-disponibles');
  cont.innerHTML = '<p class="vacio">Cargando...</p>';
  try {
    const resp = await fetch('/historico/disponibles');
    const data = await resp.json();
    const filas = data.disponibles || [];

    if (filas.length === 0) {
      cont.innerHTML = '<p class="vacio">Aun no hay historico cargado.</p>';
      return;
    }

    let html = '<table><tr><th>Simbolo</th><th>TF</th><th>Velas</th><th>Rango</th></tr>';
    for (const f of filas) {
      html += `<tr class="clickable" onclick="usarEnBacktest('${f.symbol}','${f.timeframe}')">
                 <td>${f.symbol}</td><td>${f.timeframe}</td><td>${f.velas}</td>
                 <td>${f.desde} &rarr; ${f.hasta}</td></tr>`;
    }
    html += '</table>';
    cont.innerHTML = html;
  } catch (err) {
    cont.innerHTML = '<p class="vacio error">Error cargando: ' + err + '</p>';
  }
}

function usarEnBacktest(symbol, timeframe) {
  document.getElementById('bt-symbol').value = symbol;
  document.getElementById('bt-timeframe').value = timeframe;
  document.getElementById('btn-backtest').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ---------- BACKTEST ----------
document.getElementById('btn-backtest').addEventListener('click', async () => {
  const symbol = document.getElementById('bt-symbol').value.trim().toUpperCase();
  const timeframe = document.getElementById('bt-timeframe').value.trim().toUpperCase();
  const estado = document.getElementById('estado-backtest');
  const resultado = document.getElementById('resultado-backtest');

  if (!symbol || !timeframe) {
    estado.textContent = "Completa simbolo y timeframe.";
    estado.className = "error";
    return;
  }

  estado.textContent = "Corriendo backtest...";
  estado.className = "";
  resultado.innerHTML = "";

  try {
    const resp = await fetch(`/backtest?symbol=${symbol}&timeframe=${timeframe}`);
    const data = await resp.json();

    if (!resp.ok) {
      estado.textContent = "Error: " + (data.detail || "desconocido");
      estado.className = "error";
      return;
    }

    estado.textContent = `${data.symbol} ${data.timeframe} - ${data.total_velas} velas analizadas`;
    estado.className = "ok";

    const l = data.long_type1, s = data.short_type1;
    resultado.innerHTML = `
      <div class="stats-grid">
        <div class="stat-box long">
          <h4>LONG Type 1</h4>
          <div class="stat-row"><span>Senales</span><b>${l.total_senales}</b></div>
          <div class="stat-row"><span>% Acierto</span><b>${l.pct_acierto}%</b></div>
          <div class="stat-row"><span>Ganados/Perdidos</span><b>${l.ganados}/${l.perdidos}</b></div>
          <div class="stat-row"><span>Neto (pips)</span><b>${l.resultado_neto_pips}</b></div>
        </div>
        <div class="stat-box short">
          <h4>SHORT Type 1</h4>
          <div class="stat-row"><span>Senales</span><b>${s.total_senales}</b></div>
          <div class="stat-row"><span>% Acierto</span><b>${s.pct_acierto}%</b></div>
          <div class="stat-row"><span>Ganados/Perdidos</span><b>${s.ganados}/${s.perdidos}</b></div>
          <div class="stat-row"><span>Neto (pips)</span><b>${s.resultado_neto_pips}</b></div>
        </div>
      </div>`;
  } catch (err) {
    estado.textContent = "Error de conexion: " + err;
    estado.className = "error";
  }
});

// ---------- ESTADO EN VIVO ----------
function formatearTiempo(segundos) {
  if (segundos < 90) return segundos + "s";
  const min = Math.round(segundos / 60);
  if (min < 90) return min + "min";
  const horas = Math.round(min / 60);
  return horas + "h";
}

function flecha(direccion, valor, completa) {
  const icono = direccion === 'long' ? '&uarr;' : '&darr;';
  const clase = direccion === 'long' ? 'flecha-long' : 'flecha-short';
  return `<span class="${clase} ${completa ? 'completa' : ''}">${icono} ${valor}</span>`;
}

// ---------- RESULTADOS DEL BACKTEST (agrupado por activo) ----------
function bloqueResultado(r) {
  const colorLong = r.long_neto > 0 ? 'flecha-long completa' : 'flecha-long';
  const colorShort = r.short_neto > 0 ? 'flecha-short completa' : 'flecha-short';
  return `<div class="tf-block">
            <div class="tf-header">
              <span class="tf-nombre">${r.timeframe}</span>
              <span>${r.velas} velas</span>
            </div>
            <div class="tipo-row">
              <span class="tipo-tag">L</span>
              <span class="${colorLong}">${r.long_pct}% (${r.long_senales})</span>
              <span style="color:var(--text-muted)">neto ${r.long_neto}</span>
            </div>
            <div class="tipo-row">
              <span class="tipo-tag">S</span>
              <span class="${colorShort}">${r.short_pct}% (${r.short_senales})</span>
              <span style="color:var(--text-muted)">neto ${r.short_neto}</span>
            </div>
          </div>`;
}

async function cargarResumenBacktest() {
  const cont = document.getElementById('tabla-resumen');
  const btn = document.getElementById('btn-cargar-resumen');
  btn.disabled = true;
  btn.textContent = "Calculando...";
  cont.innerHTML = '<p class="vacio">Corriendo backtest sobre todo el historico cargado, puede tardar...</p>';

  try {
    const resp = await fetch('/backtest/resumen');
    const data = await resp.json();
    const resultados = data.resultados || [];

    if (resultados.length === 0) {
      cont.innerHTML = '<p class="vacio">Aun no hay historico cargado para calcular resultados.</p>';
      btn.disabled = false;
      btn.textContent = "Cargar resultados";
      return;
    }

    const grupos = {};
    for (const r of resultados) {
      if (!grupos[r.symbol]) grupos[r.symbol] = [];
      grupos[r.symbol].push(r);
    }

    let html = '';
    for (const symbol of Object.keys(grupos).sort()) {
      html += `<div class="grupo-activo" data-symbol="${symbol}" data-categoria="${obtenerCategoria(symbol)}">
                 <div class="grupo-activo-titulo">${symbol}</div>`;
      for (const r of grupos[symbol]) {
        html += bloqueResultado(r);
      }
      html += '</div>';
    }
    cont.innerHTML = html;
    aplicarFiltro('tabla-resumen', 'buscar-resumen', 'categoria-resumen');
  } catch (err) {
    cont.innerHTML = '<p class="vacio error">Error cargando: ' + err + '</p>';
  }

  btn.disabled = false;
  btn.textContent = "Cargar resultados";
}

// ---------- PAGINA DE DETALLE POR ACTIVO ----------
let _detalleData = null;

function colorActivo(dg, mn, li, rd) {
  if (dg !== null && dg !== 0) return { clase: 'dg', label: 'Dark Green', valor: dg };
  if (mn !== null && mn !== 0) return { clase: 'mn', label: 'Maroon', valor: mn };
  if (li !== null && li !== 0) return { clase: 'li', label: 'Light Green', valor: li };
  if (rd !== null && rd !== 0) return { clase: 'rd', label: 'Light Red', valor: rd };
  return { clase: 'off', label: 'Sin datos', valor: null };
}

function cajaIndicador(nombre, valorHtml, subTexto) {
  return `<div class="indicador-box">
            <div class="ind-nombre">${nombre}</div>
            <div class="ind-valor-principal">${valorHtml}</div>
            ${subTexto ? `<div class="ind-sub">${subTexto}</div>` : ''}
          </div>`;
}

function renderIndicadores(ind) {
  const cs = `${ind.cs_blanca?.toFixed(2) ?? '-'} / ${ind.cs_magenta?.toFixed(2) ?? '-'}`;
  const tt = colorActivo(ind.tt_darkgreen, ind.tt_maroon, ind.tt_lime, ind.tt_red);
  const tw = colorActivo(ind.trwave_darkgreen, ind.trwave_maroon, ind.trwave_lime, ind.trwave_red);
  const tsdValor = ind.tsd_aqua ?? ind.tsd_yellow;
  const tsdDot = ind.tsd_aqua != null ? 'aq' : (ind.tsd_yellow != null ? 'ye' : 'off');
  const tsdLabel = ind.tsd_aqua != null ? 'Aqua' : (ind.tsd_yellow != null ? 'Yellow' : 'Sin dato');

  let html = '<div class="indicadores-grid">';
  html += cajaIndicador('CS', cs, 'Blanca / Magenta');
  html += cajaIndicador('TT', `<span class="ind-dot ${tt.clase}"></span>${tt.label}`, '');
  html += cajaIndicador('TRVI', ind.trvi_valor?.toFixed(2) ?? '-', 'Volatilidad');
  html += cajaIndicador('TR Wave', `<span class="ind-dot ${tw.clase}"></span>${tw.label}`, tw.valor?.toFixed(4) ?? '');
  html += cajaIndicador('TSD', `<span class="ind-dot ${tsdDot}"></span>${tsdValor?.toFixed(5) ?? '-'}`, tsdLabel);
  html += cajaIndicador('BB Cloud', ind.bb_inferior?.toFixed(5) ?? '-', `Sup: ${ind.bb_superior?.toFixed(5) ?? '-'}`);
  html += '</div>';
  return html;
}

function cajaReglas(titulo, reglas, completa, niveles) {
  if (!reglas) return '';
  const cumplidas = reglas.filter(r => r.cumple).length;
  let html = `<div class="reglas-box ${completa ? 'completa' : ''}">
                <div class="reglas-box-header"><span>${titulo}</span><span>${cumplidas}/${reglas.length}</span></div>
                <div class="reglas-iconos">`;
  for (const r of reglas) {
    html += `<div class="regla-icono ${r.cumple ? 'ok' : ''}" title="${r.descripcion}: ${r.detalle}">${r.numero}</div>`;
  }
  html += '</div>';
  if (niveles && !niveles.error) {
    html += `<div class="reglas-box-niveles">
                <span>E ${niveles.entry?.toFixed(5)}</span>
                <span>SL ${niveles.sl?.toFixed(5)}</span>
                <span>TP ${niveles.tp?.toFixed(5)}</span>
              </div>`;
  }
  html += '</div>';
  return html;
}

function renderDetalleTF(tf) {
  const cont = document.getElementById('detalle-contenido');
  let html = `<div class="detalle-precio-row">
                <span class="detalle-precio-valor">${tf.precio}</span>
                <span class="detalle-actualizado">hace ${formatearTiempo(tf.actualizado_hace_segundos)}</span>
              </div>`;
  html += renderIndicadores(tf.indicadores);
  html += '<div class="reglas-resumen-grid">';
  html += cajaReglas('&uarr; Long T1', tf.long_t1_reglas, tf.long_t1_completa, tf.niveles_long);
  html += cajaReglas('&darr; Short T1', tf.short_t1_reglas, tf.short_t1_completa, tf.niveles_short);
  html += cajaReglas('&uarr; Long T2', tf.long_t2_reglas, tf.long_t2_completa, null);
  html += cajaReglas('&darr; Short T2', tf.short_t2_reglas, tf.short_t2_completa, null);
  html += '</div>';
  cont.innerHTML = html;
}

function seleccionarTFDetalle(tfName) {
  document.querySelectorAll('#detalle-tf-pills .tf-pill').forEach(p => {
    p.classList.toggle('activo', p.dataset.tf === tfName);
  });
  const tf = _detalleData.timeframes.find(t => t.timeframe === tfName);
  if (tf) renderDetalleTF(tf);
}

function mostrarPanelDetalle() {
  document.querySelectorAll('.panel').forEach(el => el.classList.remove('activo'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('activo'));
  document.getElementById('panel-detalle').classList.add('activo');
}

function volverDeDetalle() {
  cambiarTab('vivo');
}

async function abrirDetalleVivo(symbol) {
  document.getElementById('detalle-titulo').textContent = symbol;
  document.getElementById('detalle-contenido').innerHTML = '<p class="vacio">Cargando...</p>';
  document.getElementById('detalle-tf-pills').innerHTML = '';
  mostrarPanelDetalle();

  try {
    const resp = await fetch(`/estado-vivo/detalle?symbol=${encodeURIComponent(symbol)}`);
    const data = await resp.json();

    if (!resp.ok) {
      document.getElementById('detalle-contenido').innerHTML = '<p class="vacio error">' + (data.detail || 'Error') + '</p>';
      return;
    }

    _detalleData = data;
    document.getElementById('detalle-tf-pills').innerHTML = data.timeframes.map((tf, i) =>
      `<span class="tf-pill ${i === 0 ? 'activo' : ''}" data-tf="${tf.timeframe}" onclick="seleccionarTFDetalle('${tf.timeframe}')">${tf.timeframe}</span>`
    ).join('');
    renderDetalleTF(data.timeframes[0]);
  } catch (err) {
    document.getElementById('detalle-contenido').innerHTML = '<p class="vacio error">Error: ' + err + '</p>';
  }
}

function bloqueTimeframe(p) {
  const antiguo = p.actualizado_hace_segundos > 180;
  let html = `<div class="tf-block" style="${antiguo ? 'opacity:0.4' : ''}">
                <div class="tf-header">
                  <span class="tf-nombre">${p.timeframe}</span>
                  <span>${formatearTiempo(p.actualizado_hace_segundos)}</span>
                </div>
                <div class="tipo-row">
                  <span class="tipo-tag">T1</span>
                  ${flecha('long', p.long_t1, p.long_t1_completa)}
                  ${flecha('short', p.short_t1, p.short_t1_completa)}
                </div>`;
  if (p.long_t2) {
    html += `<div class="tipo-row">
                <span class="tipo-tag">T2</span>
                ${flecha('long', p.long_t2, p.long_t2_completa)}
                ${flecha('short', p.short_t2, p.short_t2_completa)}
              </div>`;
  }
  html += '</div>';
  return html;
}

async function cargarEstadoVivo() {
  const cont = document.getElementById('tabla-vivo');
  try {
    const resp = await fetch('/estado-vivo');
    const data = await resp.json();
    const pares = data.pares || [];

    if (pares.length === 0) {
      cont.innerHTML = '<p class="vacio">Aun no llego ningun dato del EA.</p>';
      return;
    }

    // Agrupar por simbolo, asi cada activo queda visualmente separado
    // con sus propios timeframes debajo, evitando confundir lecturas
    // de distintos timeframes entre si.
    const grupos = {};
    for (const p of pares) {
      if (!grupos[p.symbol]) grupos[p.symbol] = [];
      grupos[p.symbol].push(p);
    }

    let html = '';
    for (const symbol of Object.keys(grupos).sort()) {
      html += `<div class="grupo-activo" data-symbol="${symbol}" data-categoria="${obtenerCategoria(symbol)}">
                 <div class="grupo-activo-titulo clickable-titulo" onclick="abrirDetalleVivo('${symbol}')">${symbol}</div>`;
      for (const p of grupos[symbol]) {
        html += bloqueTimeframe(p);
      }
      html += '</div>';
    }
    cont.innerHTML = html;
    aplicarFiltro('tabla-vivo', 'buscar-vivo', 'categoria-vivo');
  } catch (err) {
    cont.innerHTML = '<p class="vacio error">Error cargando: ' + err + '</p>';
  }
}

cargarDisponibles();
cargarEstadoVivo();
setInterval(cargarEstadoVivo, 20000);
</script>
</body>
</html>
"""


@app.get("/pares-vigilados")
def obtener_pares_vigilados():
    return {"pares": PARES_VIGILADOS}


def _indicadores_activos(indicadores: dict) -> List[str]:
    """
    Resume el diccionario crudo de valores de indicadores (el mismo que
    usa la pagina de Detalle de Activo) en una lista simple de que
    REGLAS estan mostrando algo ahora mismo -- para poder filtrar en
    'Senales' por CS/TT/TRVI/TR Wave/TSD/BB Cloud sin tener que pedir
    el detalle completo de cada simbolo, uno por uno.
    """
    def hay_valor(*claves):
        return any(indicadores.get(c) is not None for c in claves)

    activos = []
    if hay_valor("cs_magenta", "cs_blanca"):
        activos.append("CS")
    if hay_valor("tt_darkgreen", "tt_maroon", "tt_lime", "tt_red"):
        activos.append("TT")
    if hay_valor("trvi_valor"):
        activos.append("TRVI")
    if hay_valor("trwave_darkgreen", "trwave_maroon", "trwave_lime", "trwave_red"):
        activos.append("TR Wave")
    if hay_valor("tsd_aqua", "tsd_yellow"):
        activos.append("TSD")
    if hay_valor("bb_inferior", "bb_superior"):
        activos.append("BB Cloud")
    return activos


@app.get("/estado-vivo")
def estado_vivo():
    """
    Devuelve el ultimo resultado calculado para cada par/timeframe que
    el EA haya mandado, junto con hace cuanto se actualizo (para saber
    si el dato esta fresco o si el EA dejo de mandar datos).
    """
    ahora = datetime.now(timezone.utc)
    resultado = []

    # Mismo criterio de orden que en /estado-vivo/detalle: por duracion
    # real del timeframe (H1->H4->D1->W1->MN), no alfabetico.
    orden_timeframes = {"H1": 0, "H4": 1, "D1": 2, "W1": 3, "MN": 4}

    items_ordenados = sorted(
        ESTADO_VIVO.items(),
        key=lambda item: (item[0][0], orden_timeframes.get(item[0][1], 99)),
    )

    for (symbol, timeframe), datos in items_ordenados:
        if symbol not in SIMBOLOS_ACTIVOS:
            continue  # ya no esta en la lista vigilada -- no se muestra nunca
        segundos = (ahora - datos["actualizado"]).total_seconds()
        resultado.append({
            "symbol": datos["symbol"],
            "timeframe": datos["timeframe"],
            "precio": datos["precio"],
            "long_t1": datos["long_t1"],
            "short_t1": datos["short_t1"],
            "long_t1_completa": datos["long_t1_completa"],
            "short_t1_completa": datos["short_t1_completa"],
            "long_t2": datos["long_t2"],
            "short_t2": datos["short_t2"],
            "long_t2_completa": datos["long_t2_completa"],
            "short_t2_completa": datos["short_t2_completa"],
            "indicadores_activos": _indicadores_activos(datos.get("indicadores") or {}),
            "actualizado_hace_segundos": round(segundos),
        })
    return {"pares": resultado}


@app.get("/estado-vivo/detalle")
def estado_vivo_detalle(symbol: str = Query(...)):
    """
    Devuelve el desglose COMPLETO (regla por regla, con su detalle en
    texto) de todos los timeframes de un simbolo, para el panel de
    detalle al hacer clic en un activo en 'En vivo'.
    """
    ahora = datetime.now(timezone.utc)
    resultado = []

    # Orden por duracion real (H1 -> H4 -> D1 -> W1 -> MN), NO alfabetico.
    # sorted() sobre el nombre del timeframe los deja como D1,H1,H4,MN,W1
    # (orden de texto), que es justamente el desorden que se veia en el
    # panel -- esta tabla fuerza el orden logico correcto.
    orden_timeframes = {"H1": 0, "H4": 1, "D1": 2, "W1": 3, "MN": 4}

    items_del_simbolo = [
        (tf, datos) for (s, tf), datos in ESTADO_VIVO.items() if s == symbol
    ]
    items_del_simbolo.sort(key=lambda item: orden_timeframes.get(item[0], 99))

    for timeframe, datos in items_del_simbolo:
        segundos = (ahora - datos["actualizado"]).total_seconds()
        resultado.append({
            "timeframe": datos["timeframe"],
            "precio": datos["precio"],
            "actualizado_hace_segundos": round(segundos),
            "long_t1_completa": datos["long_t1_completa"],
            "short_t1_completa": datos["short_t1_completa"],
            "long_t1_reglas": datos["long_t1_reglas"],
            "short_t1_reglas": datos["short_t1_reglas"],
            "long_t2_completa": datos["long_t2_completa"],
            "short_t2_completa": datos["short_t2_completa"],
            "long_t2_reglas": datos["long_t2_reglas"],
            "short_t2_reglas": datos["short_t2_reglas"],
            "niveles_long": datos["niveles_long"],
            "niveles_short": datos["niveles_short"],
            "indicadores": datos["indicadores"],
        })

    if not resultado:
        raise HTTPException(status_code=404, detail=f"No hay datos en vivo para {symbol}")

    return {"symbol": symbol, "timeframes": resultado}


class HistoricoBarra(BaseModel):
    """Una vela de historico, tal como la manda el script de MT4 vía JSON."""
    time: str  # formato ISO: "YYYY-MM-DDTHH:MM"
    open: float
    high: float
    low: float
    close: float
    cs_magenta: Optional[float] = None
    cs_blanca: Optional[float] = None
    tt_darkgreen: Optional[float] = None
    tt_maroon: Optional[float] = None
    tt_lime: Optional[float] = None
    tt_red: Optional[float] = None
    trvi_valor: Optional[float] = None
    trwave_darkgreen: Optional[float] = None
    trwave_maroon: Optional[float] = None
    trwave_lime: Optional[float] = None
    trwave_red: Optional[float] = None
    tsd_aqua: Optional[float] = None
    tsd_yellow: Optional[float] = None
    bb_inferior: Optional[float] = None
    bb_superior: Optional[float] = None


class HistoricoLotePayload(BaseModel):
    symbol: str
    timeframe: str
    filas: List[HistoricoBarra]


# =====================================================================
# HISTORICO Y BACKTESTING (viven en Railway, no dependen de la PC)
# =====================================================================

def _parse_float_csv(valor: str) -> Optional[float]:
    valor = (valor or "").strip()
    return None if valor == "" else float(valor)


@app.post("/historico/upload-json")
def subir_historico_json(payload: HistoricoLotePayload):
    """
    Recibe un LOTE de velas directo en JSON (sin pasar por CSV) y las
    guarda en Postgres. Pensado para que UPS_Historico_Export.mq4
    mande los datos directo por WebRequest, en varios lotes, sin que
    el usuario tenga que exportar/arrastrar ningun archivo.
    """
    filas = [f.dict() for f in payload.filas]

    try:
        total = db.guardar_historico(payload.symbol, payload.timeframe, filas)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando en Postgres: {e}")

    return {"symbol": payload.symbol, "timeframe": payload.timeframe, "velas_guardadas": total}


@app.post("/historico/upload")
async def subir_historico(symbol: str, timeframe: str, archivo: UploadFile = File(...)):
    """
    Sube el CSV exportado por UPS_Historico_Export.mq4 y lo guarda en
    Postgres. Se sube UNA VEZ (o cada vez que quieras actualizar el
    historico) - despues de esto, el backtesting corre 100% en
    Railway, sin depender de que el CSV siga en tu PC.

    Ejemplo de uso (desde tu terminal, una sola vez):
        curl -X POST "https://TU-URL.up.railway.app/historico/upload?symbol=XAGUSD&timeframe=W1" \\
             -F "archivo=@UPS_Historico_XAGUSD_W1.csv"
    """
    contenido = (await archivo.read()).decode("utf-8")
    lector = csv.DictReader(io.StringIO(contenido))

    filas = []
    for row in lector:
        filas.append({
            "time": row["time"].replace(".", "-").replace(" ", "T"),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "cs_magenta": _parse_float_csv(row["cs_magenta"]),
            "cs_blanca": _parse_float_csv(row["cs_blanca"]),
            "tt_darkgreen": _parse_float_csv(row["tt_darkgreen"]),
            "tt_maroon": _parse_float_csv(row["tt_maroon"]),
            "tt_lime": _parse_float_csv(row["tt_lime"]),
            "tt_red": _parse_float_csv(row["tt_red"]),
            "trvi_valor": _parse_float_csv(row["trvi_valor"]),
            "trwave_darkgreen": _parse_float_csv(row["trwave_darkgreen"]),
            "trwave_maroon": _parse_float_csv(row["trwave_maroon"]),
            "trwave_lime": _parse_float_csv(row["trwave_lime"]),
            "trwave_red": _parse_float_csv(row["trwave_red"]),
            "tsd_aqua": _parse_float_csv(row["tsd_aqua"]),
            "tsd_yellow": _parse_float_csv(row["tsd_yellow"]),
            "bb_inferior": _parse_float_csv(row["bb_inferior"]),
            "bb_superior": _parse_float_csv(row["bb_superior"]),
        })

    try:
        total = db.guardar_historico(symbol, timeframe, filas)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando en Postgres: {e}")

    return {"symbol": symbol, "timeframe": timeframe, "velas_guardadas": total}


@app.get("/historico/disponibles")
def historico_disponibles():
    """Lista que pares/timeframes ya tienen historico cargado en Railway."""
    try:
        return {"disponibles": db.listar_pares_disponibles()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/historico-diario")
def historico_diario():
    """
    Conteo real de senales completadas por dia, para la semana actual
    (lunes a domingo). Usado por el grafico semanal del Dashboard --
    antes esto eran barras decorativas sin datos, ahora es el conteo
    real de eventos guardados por el webhook cada vez que una senal
    pasa de "no completa" a "completa".
    """
    try:
        return {"semana": db.obtener_historico_semana()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/senales-activadas")
def senales_activadas():
    """
    Para cada symbol+timeframe+direccion, el momento real (fecha y hora)
    en que se activo la senal por ultima vez -- no "hace cuanto llego
    el ultimo aviso del EA" (eso se resetea aunque la senal no haya
    cambiado en nada), sino desde cuando esta activa de verdad.
    """
    try:
        return {"activaciones": db.obtener_activaciones_recientes()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/backtest/resumen")
def backtest_resumen(max_velas_espera: int = Query(50)):
    """
    Corre el backtest sobre TODAS las combinaciones simbolo/timeframe
    que ya tengan historico cargado, y devuelve un resumen compacto
    de cada una (agrupable por simbolo en el panel). Puede tardar
    segun cuantas combinaciones haya cargadas.
    """
    try:
        disponibles = db.listar_pares_disponibles()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    resultado = []
    for item in disponibles:
        symbol = item["symbol"]
        timeframe = item["timeframe"]
        try:
            filas = db.obtener_historico(symbol, timeframe)
            if not filas:
                continue
            r = correr_backtest_sobre_filas(filas, max_velas_espera=max_velas_espera)
            stats_long = calcular_estadisticas(r["trades_long"])
            stats_short = calcular_estadisticas(r["trades_short"])
            resultado.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "velas": item["velas"],
                "long_pct": stats_long["pct_acierto"],
                "long_neto": stats_long["resultado_neto_pips"],
                "long_senales": stats_long["total_senales"],
                "short_pct": stats_short["pct_acierto"],
                "short_neto": stats_short["resultado_neto_pips"],
                "short_senales": stats_short["total_senales"],
            })
        except Exception as e:
            logger.warning(f"Error en backtest resumen para {symbol} {timeframe}: {e}")
            continue

    return {"resultados": resultado}


@app.get("/backtest")
def correr_backtest_endpoint(
    symbol: str = Query(...),
    timeframe: str = Query(...),
    max_velas_espera: int = Query(50, description="Maximo de velas a esperar por una salida (TP/SL)"),
):
    """
    Corre el backtest sobre el historico ya guardado en Postgres, y
    devuelve las estadisticas. Todo esto corre en Railway - no hace
    falta tener nada abierto en tu PC para pedir este resultado.
    """
    try:
        filas = db.obtener_historico(symbol, timeframe)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not filas:
        raise HTTPException(
            status_code=404,
            detail=f"No hay historico cargado para {symbol} {timeframe}. "
                   f"Sube el CSV primero via /historico/upload."
        )

    resultado = correr_backtest_sobre_filas(filas, max_velas_espera=max_velas_espera)

    return {
        "symbol": resultado["symbol"],
        "timeframe": resultado["timeframe"],
        "total_velas": resultado["total_velas"],
        "long_type1": calcular_estadisticas(resultado["trades_long"]),
        "short_type1": calcular_estadisticas(resultado["trades_short"]),
    }
