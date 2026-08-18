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
from fastapi import APIRouter, HTTPException, Header, Depends, Request
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
    # Columnas agregadas despues de que la tabla ya existia -- ADD COLUMN
    # IF NOT EXISTS es seguro llamarlo repetido, no rompe nada si ya esta.
    cur.execute("""
        ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS es_admin BOOLEAN NOT NULL DEFAULT FALSE;
    """)
    cur.execute("""
        ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE;
    """)
    cur.execute("""
        ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ultima_ip TEXT;
    """)
    # Migracion de arranque: si todavia no hay NINGUN admin y hay
    # exactamente 1 usuario (tu cuenta, la primera que creaste con el
    # ADMIN_SECRET), lo promovemos a admin solo. Asi no hace falta que
    # corras ningun UPDATE a mano -- se resuelve solo la primera vez
    # que el backend arranca con este codigo nuevo.
    cur.execute("SELECT COUNT(*) FROM usuarios WHERE es_admin = TRUE;")
    if cur.fetchone()[0] == 0:
        cur.execute("SELECT COUNT(*) FROM usuarios;")
        if cur.fetchone()[0] == 1:
            cur.execute("UPDATE usuarios SET es_admin = TRUE;")

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
    Dependency de FastAPI: valida el header 'Authorization: Bearer <token>'
    Y ADEMAS chequea contra la base que la cuenta siga activa.

    Ojo con esto: es intencional que consulte la base en CADA request
    (no solo confia en lo que dice el JWT), aunque sea una consulta
    extra por pedido. Si no lo hicieramos asi, desactivar una cuenta
    desde /admin no cortaria el acceso hasta que su sesion expire sola
    (hasta 30 dias) -- alguien ya logueado seguiria usando el panel
    con normalidad. Consultando la base en cada pedido, el corte de
    acceso es inmediato.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Falta el token de autenticacion")
    token = authorization[len("Bearer "):]
    payload = _decodificar_token(token)
    usuario_id = int(payload["sub"])

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute("SELECT activo FROM usuarios WHERE id = %s;", (usuario_id,))
            fila = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    if not fila:
        raise HTTPException(401, "Usuario no encontrado")
    if not fila[0]:
        raise HTTPException(403, "Esta cuenta fue desactivada")

    return {"id": usuario_id, "username": payload["username"]}


def admin_actual(usuario: dict = Depends(usuario_actual)) -> dict:
    """
    Dependency que ademas de validar el token, chequea que la cuenta
    tenga es_admin = TRUE. Se usa en todos los endpoints /admin/*.
    """
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT es_admin FROM usuarios WHERE id = %s;", (usuario["id"],))
            fila = cur.fetchone()
    finally:
        conn.close()
    if not fila or not fila[0]:
        raise HTTPException(403, "No tenes permisos de administrador")
    return usuario


# =====================================================================
# ENDPOINTS DE AUTENTICACION
# =====================================================================

class LoginBody(BaseModel):
    identificador: str  # email O username, el login acepta cualquiera de los dos
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request):
    identificador = body.identificador.strip().lower()
    ip_cliente = request.client.host if request.client else None

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "SELECT id, email, username, password_hash, es_admin, activo FROM usuarios "
                "WHERE email = %s OR username = %s;",
                (identificador, identificador),
            )
            fila = cur.fetchone()

            password_ok = fila is not None and _verificar_password(body.password, fila[3])

            if password_ok:
                # Solo actualizamos la ultima IP en un login exitoso
                # (no en intentos fallidos, para no pisar el dato con
                # intentos de gente que no logra entrar).
                cur.execute(
                    "UPDATE usuarios SET ultima_ip = %s WHERE id = %s;",
                    (ip_cliente, fila[0]),
                )
        conn.commit()
    finally:
        conn.close()

    if not password_ok:
        raise HTTPException(401, "Correo/usuario o contrasena incorrectos")

    usuario_id, email, username, _, es_admin, activo = fila
    if not activo:
        raise HTTPException(403, "Esta cuenta fue desactivada")

    token = _crear_token(usuario_id, username)
    return {
        "token": token,
        "usuario": {"id": usuario_id, "email": email, "username": username, "es_admin": es_admin},
    }


@router.get("/auth/me")
def me(usuario: dict = Depends(usuario_actual)):
    """El frontend llama esto al cargar la app para saber si el token
    guardado sigue siendo valido y traer los datos actuales del usuario."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, username, es_admin FROM usuarios WHERE id = %s;", (usuario["id"],))
            fila = cur.fetchone()
    finally:
        conn.close()
    if not fila:
        raise HTTPException(401, "Usuario no encontrado")
    return {"id": fila[0], "email": fila[1], "username": fila[2], "es_admin": fila[3]}


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


# =====================================================================
# PANEL DE ADMINISTRACION (solo cuentas con es_admin = TRUE)
# =====================================================================
# Estos endpoints reemplazan el flujo manual de POST /auth/usuarios con
# el header X-Admin-Secret -- una vez que ya tenes UNA cuenta admin
# (la primera, promovida sola en _crear_tablas), podes crear el resto
# de las cuentas desde la pantalla /admin del panel, sin volver a tocar
# la consola del navegador. El endpoint viejo con X-Admin-Secret se deja
# como esta, por si alguna vez hace falta crear una cuenta sin tener
# ya una sesion admin activa (ej. recuperacion de emergencia).

@router.get("/admin/usuarios")
def listar_usuarios(admin: dict = Depends(admin_actual)):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "SELECT id, email, username, es_admin, activo, ultima_ip, creado_en "
                "FROM usuarios ORDER BY id;"
            )
            filas = cur.fetchall()
    finally:
        conn.close()
    return {
        "usuarios": [
            {
                "id": f[0], "email": f[1], "username": f[2],
                "es_admin": f[3], "activo": f[4], "ultima_ip": f[5],
                "creado_en": f[6].isoformat(),
            }
            for f in filas
        ]
    }


class CrearUsuarioAdminBody(BaseModel):
    email: str
    username: str
    password: str
    es_admin: bool = False


@router.post("/admin/usuarios")
def crear_usuario_admin(body: CrearUsuarioAdminBody, admin: dict = Depends(admin_actual)):
    """Version del alta de cuentas para usar DESDE el panel, protegida
    por sesion de admin en vez de por el header X-Admin-Secret."""
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
                    "INSERT INTO usuarios (email, username, password_hash, es_admin) "
                    "VALUES (%s, %s, %s, %s) RETURNING id, creado_en;",
                    (email_limpio, username_limpio, password_hash, body.es_admin),
                )
            except Exception:
                conn.rollback()
                raise HTTPException(409, "Ya existe una cuenta con ese email o username")
            usuario_id, creado_en = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return {
        "id": usuario_id, "email": email_limpio, "username": username_limpio,
        "es_admin": body.es_admin, "creado_en": creado_en.isoformat(),
    }


@router.delete("/admin/usuarios/{usuario_id}")
def eliminar_usuario(usuario_id: int, admin: dict = Depends(admin_actual)):
    if usuario_id == admin["id"]:
        raise HTTPException(400, "No podes eliminar tu propia cuenta mientras estas logueado con ella")

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute("DELETE FROM usuarios WHERE id = %s;", (usuario_id,))
            eliminado = cur.rowcount > 0
        conn.commit()
    finally:
        conn.close()

    if not eliminado:
        raise HTTPException(404, "Usuario no encontrado")
    return {"ok": True}


class ActivoBody(BaseModel):
    activo: bool


@router.patch("/admin/usuarios/{usuario_id}/activo")
def cambiar_activo(usuario_id: int, body: ActivoBody, admin: dict = Depends(admin_actual)):
    """
    Activa o desactiva una cuenta. Una cuenta desactivada no puede
    loguearse de nuevo, Y ADEMAS pierde el acceso YA si ya estaba
    logueada -- usuario_actual() chequea 'activo' en cada request, no
    solo al momento del login.
    """
    if usuario_id == admin["id"] and not body.activo:
        raise HTTPException(400, "No te podes desactivar a vos mismo mientras estas logueado con esta cuenta")

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "UPDATE usuarios SET activo = %s WHERE id = %s;",
                (body.activo, usuario_id),
            )
            actualizado = cur.rowcount > 0
        conn.commit()
    finally:
        conn.close()

    if not actualizado:
        raise HTTPException(404, "Usuario no encontrado")
    return {"ok": True, "activo": body.activo}


class ResetPasswordBody(BaseModel):
    password: str


@router.post("/admin/usuarios/{usuario_id}/resetear-password")
def resetear_password(usuario_id: int, body: ResetPasswordBody, admin: dict = Depends(admin_actual)):
    """
    Le pone una contrasena nueva a una cuenta, sin necesitar la vieja
    -- es lo que usas si alguien se olvido la suya (no hay flujo de
    'olvide mi contrasena' por email todavia, esta es la alternativa
    manual mientras tanto).
    """
    if len(body.password) < 6:
        raise HTTPException(400, "La contrasena tiene que tener al menos 6 caracteres")

    password_hash = _hashear_password(body.password)

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            _crear_tablas(cur)
            cur.execute(
                "UPDATE usuarios SET password_hash = %s WHERE id = %s;",
                (password_hash, usuario_id),
            )
            actualizado = cur.rowcount > 0
        conn.commit()
    finally:
        conn.close()

    if not actualizado:
        raise HTTPException(404, "Usuario no encontrado")
    return {"ok": True}
