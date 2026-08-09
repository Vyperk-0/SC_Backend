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

from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, List
import logging
import io
import csv

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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ups-backend")

app = FastAPI(
    title="UPS Trading Backend",
    description="Motor de reglas UPS + webhook para el EA de MT4",
    version="0.1.0",
)


@app.on_event("startup")
def inicializar():
    try:
        db.inicializar_tabla()
        logger.info("Tabla de historico verificada/creada en Postgres.")
    except Exception as e:
        logger.warning(f"No se pudo inicializar la base de datos: {e}. "
                        f"Los endpoints de historico/backtesting no funcionaran "
                        f"hasta que agregues un servicio Postgres en Railway.")


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


@app.get("/upload", response_class=HTMLResponse)
def pagina_upload():
    """
    Pagina simple para subir el CSV de historico arrastrando y
    soltando el archivo, sin necesitar terminal ni curl. Accesible
    desde cualquier navegador (incluido el celular) apuntando a
    https://TU-URL.up.railway.app/upload
    """
    return """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UPS - Subir historico</title>
<style>
  body { font-family: -apple-system, sans-serif; background: #0f172a; color: #e2e8f0;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
  .card { background: #1e293b; padding: 2rem; border-radius: 16px; width: 90%; max-width: 480px; }
  h1 { font-size: 1.3rem; margin-bottom: 1.5rem; }
  label { display: block; margin: 0.8rem 0 0.3rem; font-size: 0.9rem; color: #94a3b8; }
  input[type=text] { width: 100%; padding: 0.6rem; border-radius: 8px; border: 1px solid #334155;
                      background: #0f172a; color: #e2e8f0; box-sizing: border-box; }
  #drop { border: 2px dashed #475569; border-radius: 12px; padding: 2rem 1rem; text-align: center;
          margin-top: 1rem; cursor: pointer; transition: 0.2s; }
  #drop.hover { border-color: #38bdf8; background: #17263c; }
  #drop p { margin: 0; color: #94a3b8; }
  #archivo { display: none; }
  button { width: 100%; margin-top: 1.2rem; padding: 0.7rem; border: none; border-radius: 8px;
           background: #38bdf8; color: #0f172a; font-weight: 600; cursor: pointer; font-size: 1rem; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  #estado { margin-top: 1rem; font-size: 0.9rem; text-align: center; }
  .ok { color: #4ade80; } .error { color: #f87171; }
</style>
</head>
<body>
<div class="card">
  <h1>📊 Subir histórico UPS</h1>

  <label for="symbol">Símbolo (ej. XAGUSD)</label>
  <input type="text" id="symbol" placeholder="XAGUSD">

  <label for="timeframe">Timeframe (ej. W1)</label>
  <input type="text" id="timeframe" placeholder="W1">

  <label>Archivo CSV</label>
  <div id="drop">
    <p id="drop-texto">Arrastra el CSV aquí, o toca para elegirlo</p>
    <input type="file" id="archivo" accept=".csv">
  </div>

  <button id="btn-subir" disabled>Subir</button>
  <div id="estado"></div>
</div>

<script>
const drop = document.getElementById('drop');
const input = document.getElementById('archivo');
const btn = document.getElementById('btn-subir');
const estado = document.getElementById('estado');
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
  dropTexto.textContent = "✅ " + archivo.name;
  btn.disabled = false;
}

btn.addEventListener('click', async () => {
  const symbol = document.getElementById('symbol').value.trim().toUpperCase();
  const timeframe = document.getElementById('timeframe').value.trim().toUpperCase();

  if (!symbol || !timeframe || !archivoSeleccionado) {
    estado.textContent = "Completa símbolo, timeframe, y elige un archivo.";
    estado.className = "error";
    return;
  }

  btn.disabled = true;
  estado.textContent = "Subiendo...";
  estado.className = "";

  const formData = new FormData();
  formData.append("archivo", archivoSeleccionado);

  try {
    const resp = await fetch(`/historico/upload?symbol=${symbol}&timeframe=${timeframe}`, {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();

    if (resp.ok) {
      estado.textContent = `✅ ${data.velas_guardadas} velas guardadas para ${data.symbol} ${data.timeframe}`;
      estado.className = "ok";
      dropTexto.textContent = "Arrastra el CSV aquí, o toca para elegirlo";
      archivoSeleccionado = null;
    } else {
      estado.textContent = "❌ Error: " + (data.detail || "desconocido");
      estado.className = "error";
    }
  } catch (err) {
    estado.textContent = "❌ Error de conexion: " + err;
    estado.className = "error";
  }

  btn.disabled = false;
});
</script>
</body>
</html>
"""


@app.get("/pares-vigilados")
def obtener_pares_vigilados():
    return {"pares": PARES_VIGILADOS}


# =====================================================================
# HISTORICO Y BACKTESTING (viven en Railway, no dependen de la PC)
# =====================================================================

def _parse_float_csv(valor: str) -> Optional[float]:
    valor = (valor or "").strip()
    return None if valor == "" else float(valor)


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
