# UPS Control (React + Vite + Tailwind + Flowbite)

Reescritura del panel de control del sistema UPS en React, usando
Vite como bundler, Tailwind CSS v4 para estilos, y Flowbite React
para componentes de UI (botones, tablas, inputs).

## Estructura

```
src/
├── api/
│   ├── client.js        # Cliente centralizado que habla con el backend (Railway)
│   ├── categorias.js     # Mapa de simbolo -> categoria (Shares US, Forex, Oro, Silver, Crypto)
│   └── utils.js          # Funciones compartidas (formateo de tiempo, colores de indicador)
├── components/
│   ├── Layout.jsx         # Sidebar de navegacion + contenedor principal
│   └── FiltroActivos.jsx  # Buscador + filtro por categoria (reutilizado en 2 paginas)
├── pages/
│   ├── Resultados.jsx     # Resumen de backtest de todos los activos
│   ├── Historico.jsx      # Lista de historico cargado + backtest puntual + subida CSV
│   ├── EnVivo.jsx         # Estado en tiempo real, agrupado por activo
│   └── DetalleActivo.jsx  # Pagina dedicada: indicadores + reglas de un activo (ruta /activo/:symbol)
├── App.jsx                # Rutas (React Router)
└── main.jsx                # Punto de entrada
```

## Correr en desarrollo

```bash
npm install
cp .env.example .env    # ajusta VITE_API_URL si hace falta
npm run dev
```

Abre `http://localhost:5173`.

## Build de produccion

```bash
npm run build
```

Genera la carpeta `dist/` con los archivos estaticos listos para
hostear en cualquier lado (Vercel, Netlify, o el mismo Railway via
`StaticFiles`).

## Variable de entorno

`VITE_API_URL` — URL base de tu backend en Railway (sin `/` al final).
Definida en `.env` (no se sube a git, ver `.env.example`).

## Deploy recomendado: Vercel

1. Sube esta carpeta a un repo de GitHub (separado del repo del backend).
2. En Vercel: **New Project** → importa el repo.
3. Vercel detecta Vite automaticamente. Agrega la variable de entorno
   `VITE_API_URL` en la configuracion del proyecto (Settings → Environment Variables).
4. Deploy. Listo — el frontend queda en su propio dominio (ej. `ups-control.vercel.app`),
   hablando con el backend en Railway via HTTP (CORS ya esta habilitado en el backend).

## Importante: el backend (Railway) no cambia

Este frontend consume los mismos endpoints que ya existian en el
panel HTML anterior (`/estado-vivo`, `/backtest`, `/historico/*`,
etc.) - no hizo falta tocar el motor de reglas, el EA, ni la base de
datos. Es una migracion solo de la capa visual.

## Flowbite

Se usan componentes de `flowbite-react` (Button, TextInput, Table)
sobre Tailwind CSS v4. La paleta de colores personalizada (fondo
oscuro, acento ambar) esta definida en `src/index.css` via `@theme`,
y Tailwind la expone como clases utilitarias normales
(`bg-surface`, `text-accent`, etc.) en todo el proyecto.
