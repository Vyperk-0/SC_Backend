# -*- coding: utf-8 -*-
"""
Sistema de cuentas: login con email o username, JWT, y favoritos
POR USUARIO (reemplaza el localStorage que usaba el frontend antes,
que era por-dispositivo y no se sincronizaba entre PC y celular).

No hay registro publico. Las cuentas se crean SOLO por vos (el admin)
llamando a POST /auth/usuarios con el header X-Admin-Secret, que tiene
que coincidir con la variable de entorno ADMIN_SECRET que vos definis
en Railway. Sin ese header correcto, el endpoint rechaza la creacion
-- asi nadie mas puede crearse una cuenta sola.

Variables de entorno necesarias en Railway (ademas de las que ya
tenias):
  - JWT_SECRET_KEY: cualquier string largo y aleatorio (ej. generado
    con `openssl rand -hex 32`). Si no la configuras, se usa un valor
    por defecto INSEGURO -- sirve para probar local pero no para
    produccion real.
  - ADMIN_SECRET: otro string aleatorio, el que vos vas a mandar en el
    header X-Admin-Secret cuando quieras crear una cuenta nueva.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

import db

router = APIRouter()

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "cambiar-esta-clave-en-produccion-insegura")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")
ALGORITMO = "HS256"
DIAS_EXPIRACION = 30  # "Recordarme" queda tildado por defecto en el login, asi que el token dura bastante


# =====================================================================
# TABLAS (se crean solas la primera vez que se usan, igual que en db.py)
# =====================================================================

def _crear_tablas(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            creado_en TIMESTAMP NOT NULL DEFAULT now()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS favoritos (
            user_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            PRIMARY KEY (user_id, symbol)
        );
    """)


# =====================================================================
# PASSWORD HASHING (bcrypt -- nunca se guarda la contrasena en texto plano)
# =====================================================================

def _hashear_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verificar_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# =====================================================================
# JWT
# =====================================================================

def _crear_token(usuario_id: int, username: str) -> str:
    expira = datetime.now(timezone.utc) + timedelta(days=DIAS_EXPIRACION)
    payload = {"sub": str(usuario_id), "username": username, "exp": expira}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITMO)


def _decodificar_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITMO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Sesion expirada, volve a iniciar sesion")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Token invalido")


def usuario_actual(authorization: Optional[str] = Header(None)) -> dict:
    """
    Dependency de FastAPI: valida el header 'Authorization: Bearer <token>'.
    Cualquier endpoint que necesite saber quien esta logueado usa esto
    como parametro (ej. `usuario: dict = Depends(usuario_actual)`).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Falta el token de autenticacion")
    token = authorization[len("Bearer "):]
    payload = _decodificar_token(token)
    return {"id": int(payload["sub"]), "username": payload["username"]}


# =====================================================================
# ENDPOINTS DE AUTENTICACION
# =====================================================================

class LoginBody(BaseModel):
    identificador: str  # email O username, el login acepta cualquiera de los dos
    password: str


@router.post("/auth/login")
def login(body: LoginBody):
    identificador = body.identificador.strip().lower()

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "SELECT id, email, username, password_hash FROM usuarios "
                "WHERE email = %s OR username = %s;",
                (identificador, identificador),
            )
            fila = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    if not fila or not _verificar_password(body.password, fila[3]):
        raise HTTPException(401, "Correo/usuario o contrasena incorrectos")

    usuario_id, email, username, _ = fila
    token = _crear_token(usuario_id, username)
    return {"token": token, "usuario": {"id": usuario_id, "email": email, "username": username}}


@router.get("/auth/me")
def me(usuario: dict = Depends(usuario_actual)):
    """El frontend llama esto al cargar la app para saber si el token
    guardado sigue siendo valido y traer los datos actuales del usuario."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, username FROM usuarios WHERE id = %s;", (usuario["id"],))
            fila = cur.fetchone()
    finally:
        conn.close()
    if not fila:
        raise HTTPException(401, "Usuario no encontrado")
    return {"id": fila[0], "email": fila[1], "username": fila[2]}


class CrearUsuarioBody(BaseModel):
    email: str
    username: str
    password: str


@router.post("/auth/usuarios")
def crear_usuario(body: CrearUsuarioBody, x_admin_secret: Optional[str] = Header(None)):
    """
    Crea una cuenta nueva. NO es publico: requiere el header
    X-Admin-Secret con el mismo valor que la variable de entorno
    ADMIN_SECRET. Es la forma en la que vos (el admin) creas cuentas
    para otras personas -- nadie mas puede crearse una sola.
    """
    if not ADMIN_SECRET:
        raise HTTPException(500, "ADMIN_SECRET no esta configurada en el servidor")
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(403, "No autorizado para crear cuentas")

    email_limpio = body.email.strip().lower()
    username_limpio = re.sub(r"[^a-zA-Z0-9_\.]", "", body.username).lower()
    if not username_limpio:
        raise HTTPException(400, "Username invalido (solo letras, numeros, punto y guion bajo)")
    if len(body.password) < 6:
        raise HTTPException(400, "La contrasena tiene que tener al menos 6 caracteres")

    password_hash = _hashear_password(body.password)

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            try:
                cur.execute(
                    "INSERT INTO usuarios (email, username, password_hash) "
                    "VALUES (%s, %s, %s) RETURNING id;",
                    (email_limpio, username_limpio, password_hash),
                )
            except Exception:
                conn.rollback()
                raise HTTPException(409, "Ya existe una cuenta con ese email o username")
            usuario_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    return {"id": usuario_id, "email": email_limpio, "username": username_limpio}


# =====================================================================
# FAVORITOS (por usuario -- reemplaza el localStorage del frontend)
# =====================================================================

@router.get("/favoritos")
def obtener_favoritos(usuario: dict = Depends(usuario_actual)):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "SELECT symbol FROM favoritos WHERE user_id = %s ORDER BY symbol;",
                (usuario["id"],),
            )
            filas = cur.fetchall()
        conn.commit()
    finally:
        conn.close()
    return {"favoritos": [f[0] for f in filas]}


class FavoritoBody(BaseModel):
    symbol: str


@router.post("/favoritos")
def alternar_favorito(body: FavoritoBody, usuario: dict = Depends(usuario_actual)):
    """Agrega o quita el simbolo de favoritos (toggle) y devuelve la
    lista completa actualizada, para que el frontend no tenga que
    llevar la cuenta el mismo de si quedo agregado o quitado."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "SELECT 1 FROM favoritos WHERE user_id = %s AND symbol = %s;",
                (usuario["id"], body.symbol),
            )
            ya_esta = cur.fetchone() is not None

            if ya_esta:
                cur.execute(
                    "DELETE FROM favoritos WHERE user_id = %s AND symbol = %s;",
                    (usuario["id"], body.symbol),
                )
            else:
                cur.execute(
                    "INSERT INTO favoritos (user_id, symbol) VALUES (%s, %s);",
                    (usuario["id"], body.symbol),
                )

            cur.execute(
                "SELECT symbol FROM favoritos WHERE user_id = %s ORDER BY symbol;",
                (usuario["id"],),
            )
            filas = cur.fetchall()
        conn.commit()
    finally:
        conn.close()

    return {"favoritos": [f[0] for f in filas]}
