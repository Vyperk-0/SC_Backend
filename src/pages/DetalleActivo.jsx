import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button } from 'flowbite-react';
import api from '../api/client';
import { colorActivo, DOT_COLORES, formatearTiempo } from '../api/utils';

function Dot({ clase }) {
  return (
    <span
      className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
      style={{ background: DOT_COLORES[clase] }}
    />
  );
}

function CajaIndicador({ nombre, valor, sub }) {
  return (
    <div className="bg-surface border border-border rounded-[10px] p-4">
      <div className="text-[0.72rem] uppercase tracking-wider text-text-muted mb-2">{nombre}</div>
      <div className="font-mono text-[1.15rem] font-semibold flex items-center gap-1.5">{valor}</div>
      {sub && <div className="font-mono text-[0.75rem] text-text-muted mt-1">{sub}</div>}
    </div>
  );
}

function GridIndicadores({ ind }) {
  const tt = colorActivo(ind.tt_darkgreen, ind.tt_maroon, ind.tt_lime, ind.tt_red);
  const tw = colorActivo(ind.trwave_darkgreen, ind.trwave_maroon, ind.trwave_lime, ind.trwave_red);
  const tsdValor = ind.tsd_aqua ?? ind.tsd_yellow;
  const tsdDot = ind.tsd_aqua != null ? 'aq' : ind.tsd_yellow != null ? 'ye' : 'off';
  const tsdLabel = ind.tsd_aqua != null ? 'Aqua' : ind.tsd_yellow != null ? 'Yellow' : 'Sin dato';

  return (
    <div className="grid gap-3 mb-5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
      <CajaIndicador nombre="CS" valor={`${ind.cs_blanca?.toFixed(2) ?? '-'} / ${ind.cs_magenta?.toFixed(2) ?? '-'}`} sub="Blanca / Magenta" />
      <CajaIndicador nombre="TT" valor={<><Dot clase={tt.clase} />{tt.label}</>} />
      <CajaIndicador nombre="TRVI" valor={ind.trvi_valor?.toFixed(2) ?? '-'} sub="Volatilidad" />
      <CajaIndicador nombre="TR Wave" valor={<><Dot clase={tw.clase} />{tw.label}</>} sub={tw.valor?.toFixed(4) ?? ''} />
      <CajaIndicador nombre="TSD" valor={<><Dot clase={tsdDot} />{tsdValor?.toFixed(5) ?? '-'}</>} sub={tsdLabel} />
      <CajaIndicador nombre="BB Cloud" valor={ind.bb_inferior?.toFixed(5) ?? '-'} sub={`Sup: ${ind.bb_superior?.toFixed(5) ?? '-'}`} />
    </div>
  );
}

function CajaReglas({ titulo, reglas, completa, niveles }) {
  if (!reglas) return null;
  const cumplidas = reglas.filter((r) => r.cumple).length;
  return (
    <div className={`bg-surface border rounded-[10px] p-4 ${completa ? 'border-long' : 'border-border'}`}>
      <div className="flex justify-between items-center mb-2.5 text-[0.8rem] font-semibold">
        <span>{titulo}</span>
        <span>{cumplidas}/{reglas.length}</span>
      </div>
      <div className="flex gap-1.5 flex-wrap">
        {reglas.map((r) => (
          <div
            key={r.numero}
            title={`${r.descripcion}: ${r.detalle}`}
            className={`w-[22px] h-[22px] rounded-md flex items-center justify-center text-[0.75rem] font-bold border cursor-help
              ${r.cumple
                ? 'bg-long/15 text-long border-long'
                : 'bg-bg text-text-muted border-border'}`}
          >
            {r.numero}
          </div>
        ))}
      </div>
      {niveles && !niveles.error && (
        <div className="mt-2.5 pt-2.5 border-t border-border font-mono text-[0.74rem] text-text-muted flex gap-4">
          <span>E {niveles.entry?.toFixed(5)}</span>
          <span>SL {niveles.sl?.toFixed(5)}</span>
          <span>TP {niveles.tp?.toFixed(5)}</span>
        </div>
      )}
    </div>
  );
}

export default function DetalleActivo() {
  const { symbol } = useParams();
  const navigate = useNavigate();
  const [datos, setDatos] = useState(null);
  const [tfActivo, setTfActivo] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDatos(null);
    setError(null);
    api.estadoVivoDetalle(symbol)
      .then((data) => {
        setDatos(data);
        setTfActivo(data.timeframes[0]?.timeframe);
      })
      .catch((err) => setError(err.message));
  }, [symbol]);

  const tf = datos?.timeframes.find((t) => t.timeframe === tfActivo);

  return (
    <div>
      <div className="flex items-center gap-4 mb-4">
        <Button size="xs" color="gray" onClick={() => navigate('/en-vivo')}>&larr; Volver</Button>
        <h2 className="font-mono text-2xl text-accent m-0">{symbol}</h2>
      </div>

      {error && <p className="text-short text-sm">{error}</p>}
      {!datos && !error && <p className="text-text-muted text-sm">Cargando...</p>}

      {datos && (
        <>
          <div className="flex gap-2 flex-wrap mb-5">
            {datos.timeframes.map((t) => (
              <button
                key={t.timeframe}
                onClick={() => setTfActivo(t.timeframe)}
                className={`px-3.5 py-1.5 rounded-full border font-mono text-[0.8rem] transition-colors
                  ${t.timeframe === tfActivo
                    ? 'bg-accent text-[#1A1200] border-accent font-bold'
                    : 'bg-surface text-text-muted border-border hover:border-accent-dim'}`}
              >
                {t.timeframe}
              </button>
            ))}
          </div>

          {tf && (
            <>
              <div className="flex justify-between items-baseline mb-5 pb-3.5 border-b border-border">
                <span className="font-mono text-[1.6rem] font-semibold">{tf.precio}</span>
                <span className="text-[0.76rem] text-text-muted">hace {formatearTiempo(tf.actualizado_hace_segundos)}</span>
              </div>

              <GridIndicadores ind={tf.indicadores} />

              <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
                <CajaReglas titulo="↑ Long T1" reglas={tf.long_t1_reglas} completa={tf.long_t1_completa} niveles={tf.niveles_long} />
                <CajaReglas titulo="↓ Short T1" reglas={tf.short_t1_reglas} completa={tf.short_t1_completa} niveles={tf.niveles_short} />
                <CajaReglas titulo="↑ Long T2" reglas={tf.long_t2_reglas} completa={tf.long_t2_completa} />
                <CajaReglas titulo="↓ Short T2" reglas={tf.short_t2_reglas} completa={tf.short_t2_completa} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
