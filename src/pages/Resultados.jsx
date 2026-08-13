import { useState, useMemo } from 'react';
import { Button } from 'flowbite-react';
import api from '../api/client';
import { obtenerCategoria } from '../api/categorias';
import FiltroActivos from '../components/FiltroActivos';

function BloqueResultado({ r }) {
  const colorLong = r.long_neto > 0 ? 'text-long font-bold' : 'text-text-muted';
  const colorShort = r.short_neto > 0 ? 'text-short font-bold' : 'text-text-muted';
  return (
    <div className="mb-2.5 last:mb-0">
      <div className="flex justify-between items-baseline text-[0.72rem] text-text-muted mb-1 border-b border-border pb-1">
        <span className="text-text font-semibold font-mono">{r.timeframe}</span>
        <span>{r.velas} velas</span>
      </div>
      <div className="flex items-center gap-1.5 text-[0.74rem] font-mono">
        <span className="text-text-muted w-3.5">L</span>
        <span className={colorLong}>{r.long_pct}% ({r.long_senales})</span>
        <span className="text-text-muted">neto {r.long_neto}</span>
      </div>
      <div className="flex items-center gap-1.5 text-[0.74rem] font-mono">
        <span className="text-text-muted w-3.5">S</span>
        <span className={colorShort}>{r.short_pct}% ({r.short_senales})</span>
        <span className="text-text-muted">neto {r.short_neto}</span>
      </div>
    </div>
  );
}

export default function Resultados() {
  const [resultados, setResultados] = useState([]);
  const [cargando, setCargando] = useState(false);
  const [yaCargado, setYaCargado] = useState(false);
  const [busqueda, setBusqueda] = useState('');
  const [categoria, setCategoria] = useState('');

  async function cargar() {
    setCargando(true);
    try {
      const data = await api.backtestResumen();
      setResultados(data.resultados || []);
      setYaCargado(true);
    } catch (err) {
      console.error(err);
    }
    setCargando(false);
  }

  const grupos = useMemo(() => {
    const g = {};
    for (const r of resultados) {
      if (!g[r.symbol]) g[r.symbol] = [];
      g[r.symbol].push(r);
    }
    return g;
  }, [resultados]);

  const simbolosFiltrados = useMemo(() => {
    return Object.keys(grupos)
      .filter((s) => s.toUpperCase().includes(busqueda.toUpperCase()))
      .filter((s) => categoria === '' || obtenerCategoria(s) === categoria)
      .sort();
  }, [grupos, busqueda, categoria]);

  return (
    <div>
      <div className="flex justify-between items-start mb-3 flex-wrap gap-2">
        <div>
          <h2 className="text-xl font-display font-semibold mb-1">Resultados del backtest</h2>
          <p className="text-text-muted text-sm">Rendimiento historico de cada activo/timeframe ya cargado en Postgres.</p>
        </div>
        <Button size="xs" color="gray" onClick={cargar} disabled={cargando}>
          {cargando ? 'Calculando...' : 'Cargar resultados'}
        </Button>
      </div>

      <div className="flex flex-wrap gap-3.5 mt-3 pt-3 border-t border-border text-[0.72rem] text-text-muted mb-4">
        <span><span className="text-long font-bold">↑</span> Long</span>
        <span><span className="text-short font-bold">↓</span> Short</span>
        <span><b className="text-text">%</b> = % de acierto</span>
        <span><b className="text-text">Neto</b> = pips acumulados (positivo = ganancia)</span>
      </div>

      {yaCargado && (
        <FiltroActivos busqueda={busqueda} setBusqueda={setBusqueda} categoria={categoria} setCategoria={setCategoria} />
      )}

      {!yaCargado && !cargando && (
        <p className="text-text-muted text-sm text-center py-6">
          Toca "Cargar resultados" para calcular (puede tardar segun cuantos activos tengas cargados).
        </p>
      )}
      {cargando && <p className="text-text-muted text-sm text-center py-6">Corriendo backtest sobre todo el historico cargado...</p>}
      {yaCargado && !cargando && simbolosFiltrados.length === 0 && (
        <p className="text-text-muted text-sm text-center py-6">
          {resultados.length === 0 ? 'Aun no hay historico cargado.' : 'Ningun activo coincide con el filtro.'}
        </p>
      )}

      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))' }}>
        {simbolosFiltrados.map((symbol) => (
          <div key={symbol} className="bg-surface border border-border rounded-lg p-3.5">
            <div className="font-display font-semibold text-[0.88rem] text-accent mb-2.5">{symbol}</div>
            {grupos[symbol].map((r) => (
              <BloqueResultado key={r.timeframe} r={r} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
