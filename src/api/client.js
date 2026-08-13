// Cliente centralizado para hablar con el backend UPS en Railway.
// La URL base se configura via variable de entorno VITE_API_URL
// (definida en .env), asi el mismo build funciona apuntando a
// distintos backends (local, staging, produccion) sin tocar codigo.

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

async function request(path, options = {}) {
  const resp = await fetch(`${API_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.detail || `Error ${resp.status}`);
  }
  return data;
}

export const api = {
  estadoVivo: () => request('/estado-vivo'),
  estadoVivoDetalle: (symbol) => request(`/estado-vivo/detalle?symbol=${encodeURIComponent(symbol)}`),

  historicoDisponibles: () => request('/historico/disponibles'),
  backtest: (symbol, timeframe) =>
    request(`/backtest?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  backtestResumen: () => request('/backtest/resumen'),

  subirHistoricoCSV: async (symbol, timeframe, archivo) => {
    const formData = new FormData();
    formData.append('archivo', archivo);
    const resp = await fetch(
      `${API_URL}/historico/upload?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`,
      { method: 'POST', body: formData }
    );
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `Error ${resp.status}`);
    return data;
  },

  paresVigilados: () => request('/pares-vigilados'),
};

export default api;
