"""
Capturas de pantalla (grafico + indicadores) por simbolo/timeframe -- BAJO DEMANDA.

El EA no manda capturas de todo todo el tiempo (serian ~380 combinaciones
simbolo x timeframe cada minuto, inviable). En cambio:

  1. El frontend, cuando el usuario esta mirando un simbolo+timeframe,
     llama POST /captura/solicitar. Si ya hay una imagen fresca (<55s)
     no hace nada; si no, la deja anotada en una cola en memoria.
  2. El EA, cada pocos segundos (via OnTick), llama GET /captura/pendientes,
     que devuelve texto plano "SIMBOLO|TIMEFRAME" (una linea por pedido).
  3. El EA abre el chart puntual pedido, aplica la plantilla con los
     indicadores, saca la captura, la sube por POST /captura, y ese
     pedido se cae solo de la cola.

Guarda UNA sola imagen por combinacion (symbol, timeframe) -- se
sobrescribe en cada subida, nunca se acumula historial.

Requiere un Volume persistente en Railway montado en /data (o la ruta
que definas en CAPTURAS_DIR) para que las capturas sobrevivan a un
redeploy.

NOTA: la cola de pendientes vive en memoria del proceso. Si tu backend
corre con mas de 1 worker de Uvicorn/Gunicorn esto no funciona bien
(cada worker tendria su propia cola). Si en algun momento escalan a
multiples workers, esto habria que moverlo a Postgres.
"""

import os
import time
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, PlainTextResponse

router = APIRouter()

CAPTURAS_DIR = Path(os.getenv("CAPTURAS_DIR", "/data/capturas"))
CAPTURAS_DIR.mkdir(parents=True, exist_ok=True)

EXTENSION = "gif"  # ChartScreenShot en MT4 clasico siempre genera GIF

# Umbral: si ya hay una imagen mas nueva que esto, no se vuelve a pedir
FRESCURA_SEGUNDOS = 4

# Si un pedido queda sin atender mas de esto (EA offline, etc.) se descarta
EXPIRACION_PEDIDO_SEGUNDOS = 180

# Cola en memoria: clave "SYMBOL_TF" -> timestamp del pedido
_pendientes: dict[str, float] = {}
_pendientes_lock = Lock()


def _clave(symbol: str, timeframe: str) -> str:
    return f"{symbol}_{timeframe}"


def _nombre_archivo(symbol: str, timeframe: str) -> str:
    safe_symbol = symbol.replace("/", "_").replace("\\", "_")
    return f"{safe_symbol}_{timeframe}.{EXTENSION}"


def _ruta_captura(symbol: str, timeframe: str) -> Path:
    return CAPTURAS_DIR / _nombre_archivo(symbol, timeframe)


def _imagen_fresca(symbol: str, timeframe: str) -> bool:
    ruta = _ruta_captura(symbol, timeframe)
    if not ruta.exists():
        return False
    return (time.time() - ruta.stat().st_mtime) < FRESCURA_SEGUNDOS


# ---------------------------------------------------------------------
# Usado por el FRONTEND
# ---------------------------------------------------------------------

@router.post("/captura/solicitar")
async def solicitar_captura(symbol: str, timeframe: str):
    """El frontend llama esto cuando el usuario esta viendo ese symbol+tf."""
    if _imagen_fresca(symbol, timeframe):
        return {"ok": True, "ya_actualizada": True}

    with _pendientes_lock:
        _pendientes[_clave(symbol, timeframe)] = time.time()

    return {"ok": True, "ya_actualizada": False, "en_cola": True}


@router.get("/captura")
async def obtener_captura(symbol: str, timeframe: str):
    """El frontend pide la imagen actual de ese symbol+timeframe."""
    ruta = _ruta_captura(symbol, timeframe)
    if not ruta.exists():
        raise HTTPException(404, "Todavia no hay captura para este par/timeframe")

    return Response(
        content=ruta.read_bytes(),
        media_type="image/gif",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/captura/meta")
async def meta_capturas(symbol: str):
    """Timestamp (epoch) de la ultima captura de cada timeframe de un simbolo."""
    safe_symbol = symbol.replace("/", "_").replace("\\", "_")
    resultado = {}
    for archivo in CAPTURAS_DIR.glob(f"{safe_symbol}_*.{EXTENSION}"):
        timeframe = archivo.stem[len(safe_symbol) + 1:]
        resultado[timeframe] = int(archivo.stat().st_mtime)
    return resultado


# ---------------------------------------------------------------------
# Usado por el EA
# ---------------------------------------------------------------------

@router.get("/captura/pendientes", response_class=PlainTextResponse)
async def listar_pendientes():
    """
    Texto plano, una linea por pedido: SIMBOLO|TIMEFRAME
    (formato simple a proposito para que sea trivial de parsear en MQL4).
    Purga pedidos viejos que nadie atendio.
    """
    ahora = time.time()
    lineas = []
    with _pendientes_lock:
        vencidos = [
            clave for clave, ts in _pendientes.items()
            if (ahora - ts) > EXPIRACION_PEDIDO_SEGUNDOS
        ]
        for clave in vencidos:
            del _pendientes[clave]

        for clave in _pendientes:
            symbol, timeframe = clave.rsplit("_", 1)
            lineas.append(f"{symbol}|{timeframe}")

    return "\n".join(lineas)


@router.post("/captura")
async def subir_captura(symbol: str, timeframe: str, request: Request):
    """El EA sube el binario del GIF ya capturado como body crudo."""
    cuerpo = await request.body()
    if not cuerpo:
        raise HTTPException(400, "Cuerpo vacio")

    ruta = _ruta_captura(symbol, timeframe)
    ruta.write_bytes(cuerpo)

    with _pendientes_lock:
        _pendientes.pop(_clave(symbol, timeframe), None)

    return {"ok": True, "symbol": symbol, "timeframe": timeframe, "bytes": len(cuerpo)}
