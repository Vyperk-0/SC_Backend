# UPS Monitor Agent

Backend del agente de trading para el sistema UPS (Ultimate Profit Solution).
Recibe datos de indicadores desde un EA en MT4 vía webhook, evalua las
reglas exactas del manual UPS de forma determinista (sin IA), y expone
el resultado para alertas y consultas del chat.

## Estructura

```
UPS_Monitor_Agent/
├── main.py                # Servidor FastAPI, endpoint /webhook/indicators
├── ups_rules_engine.py    # Motor de reglas UPS (Type 1/2, Long/Short)
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

## Probar el webhook

```bash
curl -X POST http://127.0.0.1:8000/webhook/indicators \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "TESLA",
    "timeframe": "W1",
    "precio": 327.58,
    "cs_magenta": 34.79,
    "cs_blanca": 22.20,
    "cs_blanca_prev": 29.07,
    "tt_maroon": 1.0,
    "trvi_valor": 35997.0,
    "trwave_maroon": -57.81,
    "tsd_yellow": 376.96,
    "bb_inferior": 352.63,
    "bb_superior": 417.94
  }'
```

## Deploy en Railway

1. Sube esta carpeta a un repositorio de GitHub.
2. En Railway: **New Project** → **Deploy from GitHub repo** → selecciona el repo.
3. Railway detecta `requirements.txt` y `Procfile` automaticamente.
4. Al terminar el deploy, Railway asigna una URL publica
   (ej. `ups-monitor-agent-production.up.railway.app`).
5. Esa URL + `/webhook/indicators` es el endpoint que el EA en MT4
   debe usar con `WebRequest()`.

## Estado del proyecto

- [x] Motor de reglas UPS (6 indicadores, Type 1/2, Long/Short)
- [x] Servidor FastAPI con webhook funcional
- [ ] EA en MT4 que manda los datos reales
- [ ] Backtesting historico
- [ ] RAG del manual PDF
- [ ] Alertas por WhatsApp (CallMeBot)
- [ ] Chat web conectado a Claude API

## Nota importante

Este backend NO usa IA para decidir senales - el motor de reglas es
100% determinista, basado en el mapeo de buffers confirmado
manualmente sobre los indicadores UPS reales en MT4. La IA (Claude)
se conecta en una fase posterior, unicamente para el chat
conversacional y las explicaciones - nunca para decidir si hay senal.
