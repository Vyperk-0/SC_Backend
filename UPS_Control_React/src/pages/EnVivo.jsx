import { useEffect, useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from 'flowbite-react';
import api from '../api/client';
import { obtenerCategoria } from '../api/categorias';
import { formatearTiempo } from '../api/utils';
import FiltroActivos from '../components/FiltroActivos';

function Flecha({ direccion, valor, completa }) {
  const icono = direccion === 'long' ? '↑' : '↓';
  const color = completa
    ? (direccion === 'long' ? 'text-long font-bold' : 'text-short font-bold')
    : 'text-text-muted';
  return <span className={`font-mono text-[0.74rem] ${color}`}>{icono} {valor}</span>;
}

function BloqueTimeframe({ p }) {
  const antiguo = p.actualizado_hace_segundos > 180;
  return (
    <div className={`mb-2.5 last:mb-0 ${antiguo ? 'opacity-40' : ''}`}>
      <div className="flex justify-between items-baseline text-[0.72rem] text-text-muted mb-1 border-b border-border pb-1">
        <span className="text-text font-semibold font-mono">{p.timeframe}</span>
        <span>{formatearTiempo(p.actualizado_hace_segundos)}</span>
      </div>
      <div className="flex items-center gap-1.5 mb-0.5">
        <span className="text-text-muted text-[0.68rem] w-4.5 shrink-0">T1</span>
        <Flecha direccion="long" valor={p.long_t1} completa={p.long_t1_completa} />
        <Flecha direccion="short" valor={p.short_t1} completa={p.short_t1_completa} />
      </div>
      {p.long_t2 && (
        <div className="flex items-center gap-1.5">
          <span className="text-text-muted text-[0.68rem] w-4.5 shrink-0">T2</span>
          <Flecha direccion="long" valor={p.long_t2} completa={p.long_t2_completa} />
          <Flecha direccion="short" valor={p.short_t2} completa={p.short_t2_completa} />
        </div>
      )}
    </div>
  );
}

export default function EnVivo() {
  const [pares, setPares] = useState([]);
  const [cargando, setCargando] = useState(true);
  const [busqueda, setBusqueda] = useState('');
  const [categoria, setCategoria] = useState('');
  const navigate = useNavigate();

  async function cargar() {
    try {
      const data = await api.estadoVivo();
      setPares(data.pares || []);
    } catch (err) {
      console.error(err);
    }
    setCargando(false);
  }

  useEffect(() => {
    cargar();
    const id = setInterval(cargar, 20000);
    return () => clearInterval(id);
  }, []);

  const grupos = useMemo(() => {
    const g = {};
    for (const p of pares) {
      if (!g[p.symbol]) g[p.symbol] = [];
      g[p.symbol].push(p);
    }
    return g;
  }, [pares]);

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
          <h2 className="text-xl font-display font-semibold mb-1">Estado en vivo</h2>
          <p className="text-text-muted text-sm">Cumplimiento de reglas ahora mismo. Se actualiza sola cada 20s.</p>
        </div>
        <Button size="xs" color="gray" onClick={cargar}>Actualizar</Button>
      </div>

      <div className="flex flex-wrap gap-3.5 mt-3 pt-3 border-t border-border text-[0.72rem] text-text-muted mb-4">
        <span><span className="text-long font-bold">↑</span> Long</span>
        <span><span className="text-short font-bold">↓</span> Short</span>
        <span><b className="text-text">T1</b> = Type 1</span>
        <span><b className="text-text">T2</b> = Type 2 (confirmado en TF superior)</span>
      </div>

      <FiltroActivos busqueda={busqueda} setBusqueda={setBusqueda} categoria={categoria} setCategoria={setCategoria} />

      {cargando && <p className="text-text-muted text-sm text-center py-6">Cargando...</p>}
      {!cargando && simbolosFiltrados.length === 0 && (
        <p className="text-text-muted text-sm text-center py-6">
          {pares.length === 0 ? 'Aun no llego ningun dato del EA.' : 'Ningun activo coincide con el filtro.'}
        </p>
      )}

      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))' }}>
        {simbolosFiltrados.map((symbol) => (
          <div key={symbol} className="bg-surface border border-border rounded-lg p-3.5">
            <div
              className="font-display font-semibold text-[0.88rem] text-accent mb-2.5 cursor-pointer hover:brightness-125"
              onClick={() => navigate(`/activo/${encodeURIComponent(symbol)}`)}
            >
              {symbol}
            </div>
            {grupos[symbol].map((p) => (
              <BloqueTimeframe key={p.timeframe} p={p} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
