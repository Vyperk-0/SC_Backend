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
def panel_unificado():
    """
    Panel unico: subir historico, ver que hay cargado, y correr el
    backtest - todo en la misma pagina, sin cambiar de URL. Accesible
    desde cualquier navegador (incluido el celular) apuntando a
    https://TU-URL.up.railway.app/upload
    """
    return """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UPS - Panel de historico y backtesting</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, sans-serif; background: #0f172a; color: #e2e8f0;
         margin: 0; padding: 1.5rem 1rem; }
  .contenedor { max-width: 640px; margin: 0 auto; display: flex; flex-direction: column; gap: 1.2rem; }
  .card { background: #1e293b; padding: 1.5rem; border-radius: 16px; }
  h1 { font-size: 1.3rem; margin: 0 0 1rem; }
  h2 { font-size: 1.05rem; margin: 0 0 1rem; color: #38bdf8; }
  label { display: block; margin: 0.8rem 0 0.3rem; font-size: 0.85rem; color: #94a3b8; }
  input[type=text] { width: 100%; padding: 0.6rem; border-radius: 8px; border: 1px solid #334155;
                      background: #0f172a; color: #e2e8f0; box-sizing: border-box; font-size: 1rem; }
  #drop { border: 2px dashed #475569; border-radius: 12px; padding: 1.5rem 1rem; text-align: center;
          margin-top: 0.8rem; cursor: pointer; transition: 0.2s; }
  #drop.hover { border-color: #38bdf8; background: #17263c; }
  #drop p { margin: 0; color: #94a3b8; font-size: 0.9rem; }
  #archivo { display: none; }
  button { width: 100%; margin-top: 1rem; padding: 0.7rem; border: none; border-radius: 8px;
           background: #38bdf8; color: #0f172a; font-weight: 600; cursor: pointer; font-size: 1rem; }
  button.secundario { background: #334155; color: #e2e8f0; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  #estado-upload, #estado-backtest { margin-top: 0.8rem; font-size: 0.85rem; text-align: center; }
  .ok { color: #4ade80; } .error { color: #f87171; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; font-size: 0.85rem; }
  th, td { text-align: left; padding: 0.5rem 0.4rem; border-bottom: 1px solid #334155; }
  th { color: #94a3b8; font-weight: 500; }
  tr.clickable { cursor: pointer; }
  tr.clickable:hover { background: #17263c; }
  .vacio { color: #64748b; font-size: 0.85rem; text-align: center; padding: 1rem 0; }
  .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; margin-top: 0.8rem; }
  .stat-box { background: #0f172a; border-radius: 10px; padding: 0.8rem; }
  .stat-box h3 { margin: 0 0 0.5rem; font-size: 0.9rem; }
  .stat-box .long h3 { color: #4ade80; }
  .stat-box .short h3 { color: #f87171; }
  .stat-row { display: flex; justify-content: space-between; font-size: 0.82rem; padding: 0.15rem 0; color: #cbd5e1; }
  .stat-row b { color: #e2e8f0; }
  .refresh { background: none; border: 1px solid #334155; color: #94a3b8; width: auto;
             padding: 0.35rem 0.7rem; font-size: 0.8rem; margin: 0; float: right; }
</style>
</head>
<body>
<div class="contenedor">

  <div class="card">
    <h1>UPS - Historico y Backtesting</h1>
    <p style="color:#94a3b8; font-size:0.85rem; margin:0;">
      Todo corre en Railway - no depende de tu PC ni de MT4 abierto.
    </p>
  </div>

  <!-- SUBIR CSV -->
  <div class="card">
    <h2>1. Subir historico (CSV)</h2>
    <label for="up-symbol">Simbolo</label>
    <input type="text" id="up-symbol" placeholder="XAGUSD">
    <label for="up-timeframe">Timeframe</label>
    <input type="text" id="up-timeframe" placeholder="W1">
    <label>Archivo CSV</label>
    <div id="drop">
      <p id="drop-texto">Arrastra el CSV aqui, o toca para elegirlo</p>
      <input type="file" id="archivo" accept=".csv">
    </div>
    <button id="btn-subir" disabled>Subir</button>
    <div id="estado-upload"></div>
  </div>

  <!-- DATOS DISPONIBLES -->
  <div class="card">
    <h2>2. Historico cargado <button class="refresh" onclick="cargarDisponibles()">↻ Actualizar</button></h2>
    <div id="tabla-disponibles"><p class="vacio">Cargando...</p></div>
  </div>

  <!-- BACKTEST -->
  <div class="card">
    <h2>3. Correr backtest</h2>
    <label for="bt-symbol">Simbolo</label>
    <input type="text" id="bt-symbol" placeholder="XAGUSD">
    <label for="bt-timeframe">Timeframe</label>
    <input type="text" id="bt-timeframe" placeholder="W1">
    <button id="btn-backtest">Correr backtest</button>
    <div id="estado-backtest"></div>
    <div id="resultado-backtest"></div>
  </div>

</div>

<script>
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
  dropTexto.textContent = "OK: " + archivo.name;
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
      estadoUpload.textContent = `OK: ${data.velas_guardadas} velas guardadas para ${data.symbol} ${data.timeframe}`;
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
                 <td>${f.desde} a ${f.hasta}</td></tr>`;
    }
    html += '</table><p style="color:#64748b; font-size:0.78rem; margin-top:0.5rem;">Toca una fila para usarla en el backtest de abajo.</p>';
    cont.innerHTML = html;
  } catch (err) {
    cont.innerHTML = '<p class="vacio error">Error cargando: ' + err + '</p>';
  }
}

function usarEnBacktest(symbol, timeframe) {
  document.getElementById('bt-symbol').value = symbol;
  document.getElementById('bt-timeframe').value = timeframe;
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
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
          <h3>LONG Type 1</h3>
          <div class="stat-row"><span>Senales</span><b>${l.total_senales}</b></div>
          <div class="stat-row"><span>% Acierto</span><b>${l.pct_acierto}%</b></div>
          <div class="stat-row"><span>Ganados/Perdidos</span><b>${l.ganados}/${l.perdidos}</b></div>
          <div class="stat-row"><span>Neto (pips)</span><b>${l.resultado_neto_pips}</b></div>
        </div>
        <div class="stat-box short">
          <h3>SHORT Type 1</h3>
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

// Cargar la lista de disponibles apenas abre la pagina
cargarDisponibles();
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
