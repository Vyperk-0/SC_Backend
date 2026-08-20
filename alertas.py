# -*- coding: utf-8 -*-
"""
Sistema de alertas configurables por usuario.

Cada cuenta puede armar sus propias reglas de alerta (que simbolos,
timeframes, direccion, que indicadores exigir, si requiere confirmacion
T2) y su propio numero de WhatsApp (via CallMeBot -- cada persona tiene
que activar su propio numero, ver /whatsapp/config).

El disparo real de las alertas se engancha desde main.py, en el mismo
punto donde ya se evaluan las senales para cada webhook del EA (funcion
evaluar_y_disparar_alertas, llamada por procesar_senal_para_alerta).
"""

import os
import time
from datetime import datetime, timezone
from typing import Optional, List

import requests
from psycopg2.extras import execute_values
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

import db
from usuarios import usuario_actual

router = APIRouter()

# =====================================================================
# CATEGORIAS (espejo exacto de src/api/categorias.js del frontend --
# si se agrega/saca un simbolo hay que actualizar los dos lados)
# =====================================================================

CATEGORIAS = {
    "ADOBE": "Shares US", "ALCOA": "Shares US", "ALIBABA": "Shares US", "AMAZON": "Shares US", "AMD": "Shares US",
    "AMEX": "Shares US", "APPLE": "Shares US", "BOA": "Shares US", "BOEING": "Shares US", "BOOKING": "Shares US",
    "CHEVRON": "Shares US", "CISCO": "Shares US", "CITI": "Shares US", "COKE": "Shares US", "Coinbase": "Shares US",
    "DEVON": "Shares US", "DISNEY": "Shares US", "EBAY": "Shares US", "EXXON": "Shares US", "FORD": "Shares US",
    "GE": "Shares US", "GOOGLE": "Shares US", "GS": "Shares US", "HLT": "Shares US", "IBM": "Shares US",
    "ILMN": "Shares US", "INTEL": "Shares US", "JNJ": "Shares US", "JPMORGAN": "Shares US", "LAM": "Shares US",
    "MCARD": "Shares US", "MCDON": "Shares US", "META": "Shares US", "MICROCHIP": "Shares US", "MICRON": "Shares US",
    "MODERNA": "Shares US", "MSFT": "Shares US", "NIKE": "Shares US", "NVIDIA": "Shares US", "Netflix": "Shares US",
    "PAYPAL": "Shares US", "PEPSI": "Shares US", "PFIZER": "Shares US", "QCOM": "Shares US", "SALESFORCE": "Shares US",
    "STARBUCKS": "Shares US", "TEVA": "Shares US", "Tesla": "Shares US", "UBER": "Shares US", "VISA": "Shares US",

    "AUDUSD": "Forex", "EURUSD": "Forex", "USDJPY": "Forex", "GBPUSD": "Forex",
    "USDCHF": "Forex", "USDCAD": "Forex", "EURGBP": "Forex",

    "XAUEUR": "Oro", "XAUUSD": "Oro",
    "XAGEUR": "Silver", "XAGUSD": "Silver",

    "#ADAUSDr": "Crypto", "#BNBUSDr": "Crypto", "#BTCUSDr": "Crypto", "#DOGEUSDr": "Crypto",
    "#ETHUSDr": "Crypto", "#LTCUSDr": "Crypto", "#SOLUSDr": "Crypto", "#TRXUSDr": "Crypto",
    "#XRPUSDr": "Crypto",
}

# Mapeo entre el codigo de indicador que usa el frontend y el numero
# de regla que usa ups_rules_engine (regla1=CS ... regla6=BBCloud).
INDICADOR_A_NUMERO_REGLA = {
    "CS": 1, "TT": 2, "TRVI": 3, "TR Wave": 4, "TSD": 5, "BB Cloud": 6,
}
NUMERO_REGLA_A_INDICADOR = {v: k for k, v in INDICADOR_A_NUMERO_REGLA.items()}


# =====================================================================
# TABLAS
# =====================================================================

def _crear_tablas(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_config (
            user_id INTEGER PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
            numero TEXT NOT NULL,
            apikey TEXT NOT NULL,
            actualizado_en TIMESTAMP NOT NULL DEFAULT now()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alertas_config (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            nombre TEXT,
            simbolos JSONB NOT NULL DEFAULT '["TODO"]',
            timeframes JSONB NOT NULL DEFAULT '["TODOS"]',
            direcciones JSONB NOT NULL DEFAULT '["long", "short"]',
            indicadores JSONB NOT NULL DEFAULT '["TODOS"]',
            requiere_t2 BOOLEAN NOT NULL DEFAULT FALSE,
            activa BOOLEAN NOT NULL DEFAULT TRUE,
            plantilla_mensaje TEXT NOT NULL DEFAULT '',
            creado_en TIMESTAMP NOT NULL DEFAULT now(),
            actualizado_en TIMESTAMP NOT NULL DEFAULT now()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alertas_historial (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            alerta_id INTEGER REFERENCES alertas_config(id) ON DELETE SET NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direccion TEXT NOT NULL,
            reglas_confirmadas INTEGER NOT NULL,
            t2_confirmado BOOLEAN NOT NULL DEFAULT FALSE,
            mensaje TEXT NOT NULL,
            estado TEXT NOT NULL,
            enviado_en TIMESTAMP NOT NULL DEFAULT now()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_historial_user ON alertas_historial(user_id, enviado_en DESC);")

    # Estado anterior de cada (alerta, simbolo, timeframe, direccion):
    # si la ultima vez que se reviso esta combinacion estaba cumpliendo
    # o no. Asi solo se dispara UNA vez por transicion "paso a cumplir"
    # -- no en cada webhook mientras se mantiene cumpliendo. Persistido
    # en Postgres (a diferencia de ESTADO_VIVO en main.py) para que un
    # redeploy de Railway no dispare alertas "repetidas" para senales
    # que ya estaban activas antes del redeploy.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alertas_estado_anterior (
            alerta_id INTEGER NOT NULL REFERENCES alertas_config(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direccion TEXT NOT NULL,
            cumplia BOOLEAN NOT NULL,
            actualizado_en TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (alerta_id, symbol, timeframe, direccion)
        );
    """)


PLANTILLA_DEFAULT = (
    "🔔 {{simbolo}} · {{direccion}} · {{timeframe}}\n"
    "Reglas 1-6: {{reglas}}/6 confirmadas\n"
    "{{t2}}"
)

DEFAULT_TIMEOUT_HTTP = 10


# =====================================================================
# WHATSAPP: configuracion + prueba
# =====================================================================

class WhatsappConfigBody(BaseModel):
    numero: str
    apikey: str


@router.get("/whatsapp/config")
def obtener_whatsapp_config(usuario: dict = Depends(usuario_actual)):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "SELECT numero, apikey, actualizado_en FROM whatsapp_config WHERE user_id = %s;",
                (usuario["id"],),
            )
            fila = cur.fetchone()
    finally:
        conn.close()

    if not fila:
        return {"configurado": False}

    numero, apikey, actualizado_en = fila
    # Nunca devolvemos la apikey completa de vuelta al frontend, solo
    # confirmamos que existe -- no hace falta mostrarla de nuevo, y asi
    # evitamos que quede circulando en respuestas de red innecesariamente.
    return {
        "configurado": True,
        "numero": numero,
        "apikey_parcial": apikey[:3] + "•" * max(0, len(apikey) - 3),
        "actualizado_en": actualizado_en.isoformat(),
    }


@router.put("/whatsapp/config")
def guardar_whatsapp_config(body: WhatsappConfigBody, usuario: dict = Depends(usuario_actual)):
    numero = body.numero.strip()
    apikey = body.apikey.strip()
    if not numero or not apikey:
        raise HTTPException(400, "Numero y apikey son obligatorios")

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                """
                INSERT INTO whatsapp_config (user_id, numero, apikey, actualizado_en)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (user_id) DO UPDATE
                SET numero = EXCLUDED.numero, apikey = EXCLUDED.apikey, actualizado_en = now();
                """,
                (usuario["id"], numero, apikey),
            )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True}


def _enviar_whatsapp_crudo(numero: str, apikey: str, mensaje: str) -> bool:
    """Llama a la API de CallMeBot. Devuelve True si salio bien."""
    try:
        resp = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": numero, "text": mensaje, "apikey": apikey},
            timeout=DEFAULT_TIMEOUT_HTTP,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


@router.post("/whatsapp/probar")
def probar_whatsapp(usuario: dict = Depends(usuario_actual)):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute("SELECT numero, apikey FROM whatsapp_config WHERE user_id = %s;", (usuario["id"],))
            fila = cur.fetchone()
    finally:
        conn.close()

    if not fila:
        raise HTTPException(400, "Todavia no configuraste tu numero de WhatsApp")

    numero, apikey = fila
    ok = _enviar_whatsapp_crudo(numero, apikey, "✅ Prueba de UPS Control -- si ves esto, tu WhatsApp quedo bien configurado.")
    if not ok:
        raise HTTPException(502, "No se pudo mandar el mensaje de prueba. Revisa el numero/apikey.")
    return {"ok": True}


# =====================================================================
# REGLAS DE ALERTA (CRUD)
# =====================================================================

class AlertaBody(BaseModel):
    nombre: Optional[str] = None
    simbolos: List[str] = ["TODO"]
    timeframes: List[str] = ["TODOS"]
    direcciones: List[str] = ["long", "short"]
    indicadores: List[str] = ["TODOS"]
    requiere_t2: bool = False
    activa: bool = True
    plantilla_mensaje: Optional[str] = None


def _fila_a_dict(fila) -> dict:
    (id_, nombre, simbolos, timeframes, direcciones, indicadores,
     requiere_t2, activa, plantilla, creado_en, actualizado_en) = fila
    return {
        "id": id_, "nombre": nombre, "simbolos": simbolos, "timeframes": timeframes,
        "direcciones": direcciones, "indicadores": indicadores, "requiere_t2": requiere_t2,
        "activa": activa, "plantilla_mensaje": plantilla,
        "creado_en": creado_en.isoformat(), "actualizado_en": actualizado_en.isoformat(),
    }


@router.get("/alertas")
def listar_alertas(usuario: dict = Depends(usuario_actual)):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                """
                SELECT id, nombre, simbolos, timeframes, direcciones, indicadores,
                       requiere_t2, activa, plantilla_mensaje, creado_en, actualizado_en
                FROM alertas_config WHERE user_id = %s ORDER BY id DESC;
                """,
                (usuario["id"],),
            )
            filas = cur.fetchall()
    finally:
        conn.close()

    return {"alertas": [_fila_a_dict(f) for f in filas]}


@router.post("/alertas")
def crear_alerta(body: AlertaBody, usuario: dict = Depends(usuario_actual)):
    import json as _json

    plantilla = body.plantilla_mensaje.strip() if body.plantilla_mensaje else PLANTILLA_DEFAULT

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                """
                INSERT INTO alertas_config
                    (user_id, nombre, simbolos, timeframes, direcciones, indicadores,
                     requiere_t2, activa, plantilla_mensaje)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, nombre, simbolos, timeframes, direcciones, indicadores,
                          requiere_t2, activa, plantilla_mensaje, creado_en, actualizado_en;
                """,
                (
                    usuario["id"], body.nombre, _json.dumps(body.simbolos), _json.dumps(body.timeframes),
                    _json.dumps(body.direcciones), _json.dumps(body.indicadores),
                    body.requiere_t2, body.activa, plantilla,
                ),
            )
            fila = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return _fila_a_dict(fila)


@router.put("/alertas/{alerta_id}")
def editar_alerta(alerta_id: int, body: AlertaBody, usuario: dict = Depends(usuario_actual)):
    import json as _json

    plantilla = body.plantilla_mensaje.strip() if body.plantilla_mensaje else PLANTILLA_DEFAULT

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                """
                UPDATE alertas_config SET
                    nombre = %s, simbolos = %s, timeframes = %s, direcciones = %s,
                    indicadores = %s, requiere_t2 = %s, activa = %s, plantilla_mensaje = %s,
                    actualizado_en = now()
                WHERE id = %s AND user_id = %s
                RETURNING id, nombre, simbolos, timeframes, direcciones, indicadores,
                          requiere_t2, activa, plantilla_mensaje, creado_en, actualizado_en;
                """,
                (
                    body.nombre, _json.dumps(body.simbolos), _json.dumps(body.timeframes),
                    _json.dumps(body.direcciones), _json.dumps(body.indicadores),
                    body.requiere_t2, body.activa, plantilla, alerta_id, usuario["id"],
                ),
            )
            fila = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    if not fila:
        raise HTTPException(404, "Alerta no encontrada")
    return _fila_a_dict(fila)


class ActivaAlertaBody(BaseModel):
    activa: bool


@router.patch("/alertas/{alerta_id}/activa")
def cambiar_activa_alerta(alerta_id: int, body: ActivaAlertaBody, usuario: dict = Depends(usuario_actual)):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "UPDATE alertas_config SET activa = %s, actualizado_en = now() WHERE id = %s AND user_id = %s;",
                (body.activa, alerta_id, usuario["id"]),
            )
            actualizado = cur.rowcount > 0
        conn.commit()
    finally:
        conn.close()

    if not actualizado:
        raise HTTPException(404, "Alerta no encontrada")
    return {"ok": True, "activa": body.activa}


@router.delete("/alertas/{alerta_id}")
def eliminar_alerta(alerta_id: int, usuario: dict = Depends(usuario_actual)):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute("DELETE FROM alertas_config WHERE id = %s AND user_id = %s;", (alerta_id, usuario["id"]))
            eliminado = cur.rowcount > 0
        conn.commit()
    finally:
        conn.close()

    if not eliminado:
        raise HTTPException(404, "Alerta no encontrada")
    return {"ok": True}


# =====================================================================
# HISTORIAL (paginado, 20 por pagina)
# =====================================================================

POR_PAGINA_HISTORIAL = 20


@router.get("/alertas/historial")
def historial_alertas(pagina: int = Query(1, ge=1), usuario: dict = Depends(usuario_actual)):
    offset = (pagina - 1) * POR_PAGINA_HISTORIAL

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute("SELECT COUNT(*) FROM alertas_historial WHERE user_id = %s;", (usuario["id"],))
            total = cur.fetchone()[0]

            cur.execute(
                """
                SELECT id, symbol, timeframe, direccion, reglas_confirmadas, t2_confirmado,
                       mensaje, estado, enviado_en
                FROM alertas_historial WHERE user_id = %s
                ORDER BY enviado_en DESC LIMIT %s OFFSET %s;
                """,
                (usuario["id"], POR_PAGINA_HISTORIAL, offset),
            )
            filas = cur.fetchall()
    finally:
        conn.close()

    items = [
        {
            "id": f[0], "symbol": f[1], "timeframe": f[2], "direccion": f[3],
            "reglas_confirmadas": f[4], "t2_confirmado": f[5], "mensaje": f[6],
            "estado": f[7], "enviado_en": f[8].isoformat(),
        }
        for f in filas
    ]

    return {
        "items": items, "total": total, "pagina": pagina,
        "por_pagina": POR_PAGINA_HISTORIAL,
        "total_paginas": max(1, (total + POR_PAGINA_HISTORIAL - 1) // POR_PAGINA_HISTORIAL),
    }


# =====================================================================
# MOTOR DE DISPARO -- llamado desde main.py por cada evaluacion
# =====================================================================

# El estado anterior (que reemplazaba a este dict) ahora se guarda
# en la tabla alertas_estado_anterior -- ver evaluar_y_disparar_alertas.


def _simbolo_coincide(symbol: str, simbolos_regla: List[str]) -> bool:
    if not simbolos_regla or "TODO" in simbolos_regla:
        return True
    if symbol in simbolos_regla:
        return True
    categoria = CATEGORIAS.get(symbol)
    return categoria is not None and categoria in simbolos_regla


def _timeframe_coincide(timeframe: str, timeframes_regla: List[str]) -> bool:
    return not timeframes_regla or "TODOS" in timeframes_regla or timeframe in timeframes_regla


def _indicadores_requeridos(indicadores_regla: List[str]) -> List[int]:
    if not indicadores_regla or "TODOS" in indicadores_regla:
        return [1, 2, 3, 4, 5, 6]
    return [INDICADOR_A_NUMERO_REGLA[i] for i in indicadores_regla if i in INDICADOR_A_NUMERO_REGLA]


def _armar_mensaje(plantilla: str, symbol: str, direccion_texto: str, timeframe: str,
                    reglas_confirmadas: int, t2_confirmado: bool) -> str:
    reemplazos = {
        "{{simbolo}}": symbol,
        "{{direccion}}": direccion_texto,
        "{{timeframe}}": timeframe,
        "{{reglas}}": str(reglas_confirmadas),
        "{{t2}}": "✅ TF superior tambien confirma (T2)" if t2_confirmado else "",
        "{{hora}}": datetime.now().strftime("%H:%M"),
    }
    texto = plantilla or PLANTILLA_DEFAULT
    for variable, valor in reemplazos.items():
        texto = texto.replace(variable, valor)
    return texto


def evaluar_y_disparar_alertas(evaluacion, niveles: dict):
    """
    Punto de enganche desde main.py (procesar_senal_para_alerta).
    Revisa todas las reglas de alerta ACTIVAS de TODOS los usuarios y
    dispara por WhatsApp las que coincidan con esta evaluacion puntual.

    'evaluacion' es un ResultadoEvaluacion de ups_rules_engine (puede
    ser Type1 o Type2 -- Type2 solo se evalua cuando el EA mando
    tf_superior, y sus reglas incluyen la #7 de confirmacion superior
    ademas de las 1-6 normales).
    """
    symbol = evaluacion.symbol
    timeframe = evaluacion.timeframe
    direccion = evaluacion.direccion.value  # 'long' o 'short'
    es_type2 = evaluacion.tipo.value == "type_2"

    reglas_1_a_6 = {r.numero: r.cumple for r in evaluacion.reglas if r.numero <= 6}
    regla7_cumple = next((r.cumple for r in evaluacion.reglas if r.numero == 7), False)

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            # Traemos, junto con cada regla, el numero de WhatsApp del
            # dueño (si lo configuro) -- sin eso no hay a donde mandar.
            cur.execute(
                """
                SELECT a.id, a.user_id, a.nombre, a.simbolos, a.timeframes, a.direcciones,
                       a.indicadores, a.requiere_t2, a.plantilla_mensaje,
                       w.numero, w.apikey
                FROM alertas_config a
                LEFT JOIN whatsapp_config w ON w.user_id = a.user_id
                WHERE a.activa = TRUE;
                """
            )
            reglas_activas = cur.fetchall()

            # Estado anterior persistido: una sola consulta para TODAS
            # las reglas activas, filtrado a esta combinacion puntual
            # (symbol, timeframe, direccion) -- evita N consultas
            # individuales dentro del loop de abajo.
            ids_reglas = [fila[0] for fila in reglas_activas]
            estado_anterior = {}
            if ids_reglas:
                cur.execute(
                    """
                    SELECT alerta_id, cumplia FROM alertas_estado_anterior
                    WHERE symbol = %s AND timeframe = %s AND direccion = %s
                          AND alerta_id = ANY(%s);
                    """,
                    (symbol, timeframe, direccion, ids_reglas),
                )
                estado_anterior = dict(cur.fetchall())
        conn.commit()
    finally:
        conn.close()

    # Se acumulan los cambios de estado para escribirlos todos juntos
    # al final, en un solo UPSERT por lote (en vez de uno por regla).
    cambios_estado = []

    for fila in reglas_activas:
        (alerta_id, user_id, nombre, simbolos_regla, timeframes_regla, direcciones_regla,
         indicadores_regla, requiere_t2, plantilla, numero_whatsapp, apikey_whatsapp) = fila

        # Una regla con requiere_t2 solo se evalua contra evaluaciones
        # Type2 (que ya incluyen la confirmacion del TF superior); una
        # regla sin requiere_t2 solo contra Type1. Mezclarlas no tiene
        # sentido: evitamos, por ejemplo, que una regla T1 se dispare
        # dos veces (una por el T1 y otra por el T2 del mismo cierre).
        if requiere_t2 != es_type2:
            continue

        if direccion not in direcciones_regla:
            continue
        if not _simbolo_coincide(symbol, simbolos_regla):
            continue
        if not _timeframe_coincide(timeframe, timeframes_regla):
            continue

        requeridos = _indicadores_requeridos(indicadores_regla)
        cumple_indicadores = all(reglas_1_a_6.get(n, False) for n in requeridos)
        cumple_t2 = (regla7_cumple if requiere_t2 else True)
        cumple_todo = cumple_indicadores and cumple_t2

        estaba_cumpliendo = estado_anterior.get(alerta_id, False)
        if cumple_todo != estaba_cumpliendo:
            cambios_estado.append((alerta_id, symbol, timeframe, direccion, cumple_todo))

        if not cumple_todo or estaba_cumpliendo:
            # O no cumple ahora, o ya cumplia la vuelta anterior (no
            # es una transicion nueva) -- no se manda de nuevo.
            continue

        reglas_confirmadas = sum(1 for n in [1, 2, 3, 4, 5, 6] if reglas_1_a_6.get(n, False))
        direccion_texto = "Long" if direccion == "long" else "Short"
        mensaje = _armar_mensaje(plantilla, symbol, direccion_texto, timeframe, reglas_confirmadas, cumple_t2 and requiere_t2)

        estado = "fallo"
        if numero_whatsapp and apikey_whatsapp:
            enviado_ok = _enviar_whatsapp_crudo(numero_whatsapp, apikey_whatsapp, mensaje)
            estado = "enviada" if enviado_ok else "fallo"
        # Si el usuario nunca configuro su WhatsApp, igual dejamos el
        # registro en el historial (como "fallo"), para que se entere
        # de que la alerta hubiera disparado si tuviera el numero cargado.

        conn2 = db.get_connection()
        try:
            with conn2.cursor() as cur2:
                cur2.execute(
                    """
                    INSERT INTO alertas_historial
                        (user_id, alerta_id, symbol, timeframe, direccion,
                         reglas_confirmadas, t2_confirmado, mensaje, estado)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (user_id, alerta_id, symbol, timeframe, direccion_texto,
                     reglas_confirmadas, bool(cumple_t2 and requiere_t2), mensaje, estado),
                )
            conn2.commit()
        finally:
            conn2.close()

    # Guardar todos los cambios de estado detectados en este ciclo,
    # de una sola vez.
    if cambios_estado:
        conn3 = db.get_connection()
        try:
            with conn3.cursor() as cur3:
                execute_values(
                    cur3,
                    """
                    INSERT INTO alertas_estado_anterior (alerta_id, symbol, timeframe, direccion, cumplia, actualizado_en)
                    VALUES %s
                    ON CONFLICT (alerta_id, symbol, timeframe, direccion) DO UPDATE
                    SET cumplia = EXCLUDED.cumplia, actualizado_en = now();
                    """,
                    [(aid, s, tf, d, c, datetime.now(timezone.utc)) for (aid, s, tf, d, c) in cambios_estado],
                )
            conn3.commit()
        finally:
            conn3.close()
