# UPS Monitor Agent

Backend del agente de trading para el sistema UPS (Ultimate Profit Solution).
Recibe datos de indicadores desde un EA en MT4 vía webhook, evalua las
reglas exactas del manual UPS de forma determinista (sin IA), guarda
historico en Postgres, y corre backtesting - todo en Railway, sin
depender de la PC del usuario.

## Estructura

```
UPS_Monitor_Agent/
├── main.py                # Servidor FastAPI: webhook, historico, backtest, pagina de upload
├── ups_rules_engine.py    # Motor de reglas UPS (Type 1/2, Long/Short)
├── backtesting.py         # Logica de backtesting (reutilizable: CSV local o Postgres)
├── db.py                  # Conexion y operaciones con Postgres (historico)
├── requirements.txt       # Dependencias Python
└── Procfile                # Comando de arranque para Railway
```

## Correr localmente

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Servidor disponible en `http://127.0.0.1:8000`.
Documentación interactiva automática en `http://127.0.0.1:8000/docs`.

## Deploy en Railway

1. Sube esta carpeta a un repositorio de GitHub.
2. En Railway: **New Project** → **Deploy from GitHub repo** → selecciona el repo.
3. Agrega Postgres: dentro del proyecto → **New** → **Database** → **Add PostgreSQL**
   (Railway conecta `DATABASE_URL` automaticamente, no hace falta configurar nada mas).
4. Railway detecta `requirements.txt` y `Procfile` automaticamente y despliega.
5. Genera un dominio publico en **Settings → Networking → Generate Domain** si no se asigna solo.

## Subir historico (sin usar la PC despues de este paso)

**Opcion A - pagina web (recomendado, sin terminal):**
Abre https://upsmonitoralert-production.up.railway.app/upload en cualquier
navegador, arrastra el CSV exportado (ver `UPS_Historico_Export.mq4`),
completa simbolo/timeframe, y sube.

**Opcion B - curl:**
```bash
curl -X POST "https://upsmonitoralert-production.up.railway.app/historico/upload?symbol=XAGUSD&timeframe=W1" \
     -F "archivo=@UPS_Historico_XAGUSD_W1.csv"
```

## Backtesting (corre en Railway, no en la PC)

```bash
curl "https://upsmonitoralert-production.up.railway.app/backtest?symbol=XAGUSD&timeframe=W1"
```

Devuelve estadisticas de Long y Short Type 1: numero de senales,
% de acierto, ganancia/perdida promedio, resultado neto en pips.

Ver que pares/timeframes ya tienen historico cargado:
```bash
curl "https://upsmonitoralert-production.up.railway.app/historico/disponibles"
```

## Estado del proyecto

- [x] Motor de reglas UPS (6 indicadores, Type 1/2, Long/Short)
- [x] Servidor FastAPI con webhook funcional
- [x] EA en MT4 mandando datos reales cada minuto
- [x] Historico + backtesting corriendo 100% en Railway (Postgres)
- [ ] Backtesting en mas pares/timeframes, y Type 2
- [ ] Calibrar umbral de volatilidad TRVI por simbolo con datos reales
- [ ] RAG del manual PDF
- [ ] Alertas por WhatsApp (CallMeBot)
- [ ] Chat web conectado a Claude API

## Nota importante

Este backend NO usa IA para decidir senales - el motor de reglas es
100% determinista, basado en el mapeo de buffers confirmado
manualmente sobre los indicadores UPS reales en MT4. La IA (Claude)
se conecta en una fase posterior, unicamente para el chat
conversacional y las explicaciones - nunca para decidir si hay senal.

Todo el sistema (motor de reglas, historico, backtesting, y mas
adelante el chat) corre en Railway 24/7 - la PC del usuario y el
Virtual Hosting de MT4 son las unicas piezas fuera de Railway, y
ninguna de las dos necesita estar prendida para consultar resultados
o correr un backtest.

