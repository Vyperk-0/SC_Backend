import { useEffect, useState, useRef } from 'react';
import { Button, TextInput, Table } from 'flowbite-react';
import api from '../api/client';

export default function Historico() {
  const [disponibles, setDisponibles] = useState([]);
  const [cargandoLista, setCargandoLista] = useState(true);

  const [btSymbol, setBtSymbol] = useState('');
  const [btTimeframe, setBtTimeframe] = useState('');
  const [btEstado, setBtEstado] = useState('');
  const [btResultado, setBtResultado] = useState(null);
  const [btError, setBtError] = useState(false);
  const backtestRef = useRef(null);

  const [upSymbol, setUpSymbol] = useState('');
  const [upTimeframe, setUpTimeframe] = useState('');
  const [archivo, setArchivo] = useState(null);
  const [subiendo, setSubiendo] = useState(false);
  const [upEstado, setUpEstado] = useState('');
  const [upError, setUpError] = useState(false);

  async function cargarDisponibles() {
    setCargandoLista(true);
    try {
      const data = await api.historicoDisponibles();
      setDisponibles(data.disponibles || []);
    } catch (err) {
      console.error(err);
    }
    setCargandoLista(false);
  }

  useEffect(() => { cargarDisponibles(); }, []);

  function usarEnBacktest(symbol, timeframe) {
    setBtSymbol(symbol);
    setBtTimeframe(timeframe);
    backtestRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  async function correrBacktest() {
    if (!btSymbol || !btTimeframe) {
      setBtEstado('Completa simbolo y timeframe.');
      setBtError(true);
      return;
    }
    setBtEstado('Corriendo backtest...');
    setBtError(false);
    setBtResultado(null);
    try {
      const data = await api.backtest(btSymbol.toUpperCase(), btTimeframe.toUpperCase());
      setBtEstado(`${data.symbol} ${data.timeframe} - ${data.total_velas} velas analizadas`);
      setBtError(false);
      setBtResultado(data);
    } catch (err) {
      setBtEstado(err.message);
      setBtError(true);
    }
  }

  async function subirCSV() {
    if (!upSymbol || !upTimeframe || !archivo) {
      setUpEstado('Completa simbolo, timeframe, y elige un archivo.');
      setUpError(true);
      return;
    }
    setSubiendo(true);
    setUpEstado('Subiendo...');
    setUpError(false);
    try {
      const data = await api.subirHistoricoCSV(upSymbol.toUpperCase(), upTimeframe.toUpperCase(), archivo);
      setUpEstado(`${data.velas_guardadas} velas guardadas para ${data.symbol} ${data.timeframe}`);
      setUpError(false);
      setArchivo(null);
      cargarDisponibles();
    } catch (err) {
      setUpEstado(err.message);
      setUpError(true);
    }
    setSubiendo(false);
  }

  return (
    <div>
      <div className="mb-5">
        <h2 className="text-xl font-display font-semibold mb-1">Historico y backtest puntual</h2>
        <p className="text-text-muted text-sm">Pares y timeframes ya cargados en la base de datos. Toca una fila para correr su backtest abajo.</p>
      </div>

      {/* Tabla de disponibles */}
      <div className="bg-surface border border-border rounded-[10px] p-5 mb-4">
        <div className="flex justify-between items-center mb-3">
          <h3 className="font-semibold text-[0.95rem]">Tablas cargadas</h3>
          <Button size="xs" color="gray" onClick={cargarDisponibles}>Actualizar</Button>
        </div>

        {cargandoLista && <p className="text-text-muted text-sm text-center py-4">Cargando...</p>}
        {!cargandoLista && disponibles.length === 0 && (
          <p className="text-text-muted text-sm text-center py-4">Aun no hay historico cargado.</p>
        )}
        {!cargandoLista && disponibles.length > 0 && (
          <div className="overflow-x-auto">
            <Table>
              <Table.Head>
                <Table.HeadCell>Simbolo</Table.HeadCell>
                <Table.HeadCell>TF</Table.HeadCell>
                <Table.HeadCell>Velas</Table.HeadCell>
                <Table.HeadCell>Rango</Table.HeadCell>
              </Table.Head>
              <Table.Body>
                {disponibles.map((f) => (
                  <Table.Row
                    key={`${f.symbol}-${f.timeframe}`}
                    className="cursor-pointer hover:bg-surface-alt"
                    onClick={() => usarEnBacktest(f.symbol, f.timeframe)}
                  >
                    <Table.Cell className="font-mono">{f.symbol}</Table.Cell>
                    <Table.Cell className="font-mono">{f.timeframe}</Table.Cell>
                    <Table.Cell className="font-mono">{f.velas}</Table.Cell>
                    <Table.Cell className="font-mono text-xs">{f.desde} &rarr; {f.hasta}</Table.Cell>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table>
          </div>
        )}
      </div>

      {/* Backtest puntual */}
      <div ref={backtestRef} className="bg-surface border border-border rounded-[10px] p-5 mb-4">
        <h3 className="font-semibold text-[0.95rem] mb-3">Correr backtest puntual</h3>
        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block text-xs text-text-muted mb-1">Simbolo</label>
            <TextInput value={btSymbol} onChange={(e) => setBtSymbol(e.target.value)} placeholder="XAGUSD" />
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1">Timeframe</label>
            <TextInput value={btTimeframe} onChange={(e) => setBtTimeframe(e.target.value)} placeholder="W1" />
          </div>
        </div>
        <Button className="w-full" onClick={correrBacktest}>Correr backtest</Button>
        {btEstado && <p className={`mt-2 text-sm ${btError ? 'text-short' : 'text-long'}`}>{btEstado}</p>}

        {btResultado && (
          <div className="grid grid-cols-2 gap-3 mt-4">
            {['long_type1', 'short_type1'].map((k) => {
              const d = btResultado[k];
              const esLong = k === 'long_type1';
              return (
                <div key={k} className="bg-bg border border-border rounded-lg p-3">
                  <h4 className={`text-sm font-display mb-2 ${esLong ? 'text-long' : 'text-short'}`}>
                    {esLong ? 'LONG' : 'SHORT'} Type 1
                  </h4>
                  <div className="text-xs text-text-muted flex justify-between py-0.5"><span>Senales</span><b className="text-text font-mono">{d.total_senales}</b></div>
                  <div className="text-xs text-text-muted flex justify-between py-0.5"><span>% Acierto</span><b className="text-text font-mono">{d.pct_acierto}%</b></div>
                  <div className="text-xs text-text-muted flex justify-between py-0.5"><span>Ganados/Perdidos</span><b className="text-text font-mono">{d.ganados}/{d.perdidos}</b></div>
                  <div className="text-xs text-text-muted flex justify-between py-0.5"><span>Neto (pips)</span><b className="text-text font-mono">{d.resultado_neto_pips}</b></div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Subir CSV (fallback manual, ya que el EA de historico manda directo) */}
      <div className="bg-surface border border-border rounded-[10px] p-5">
        <h3 className="font-semibold text-[0.95rem] mb-3">Subir CSV manualmente (opcional)</h3>
        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block text-xs text-text-muted mb-1">Simbolo</label>
            <TextInput value={upSymbol} onChange={(e) => setUpSymbol(e.target.value)} placeholder="XAGUSD" />
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1">Timeframe</label>
            <TextInput value={upTimeframe} onChange={(e) => setUpTimeframe(e.target.value)} placeholder="W1" />
          </div>
        </div>
        <label className="block text-xs text-text-muted mb-1">Archivo CSV</label>
        <input
          type="file"
          accept=".csv"
          onChange={(e) => setArchivo(e.target.files[0] || null)}
          className="block w-full text-sm text-text-muted mb-3 border border-border rounded-lg cursor-pointer bg-bg
                     file:mr-3 file:py-2 file:px-3 file:rounded-l-lg file:border-0 file:bg-surface-alt file:text-text"
        />
        <Button className="w-full" onClick={subirCSV} disabled={subiendo}>
          {subiendo ? 'Subiendo...' : 'Subir'}
        </Button>
        {upEstado && <p className={`mt-2 text-sm ${upError ? 'text-short' : 'text-long'}`}>{upEstado}</p>}
      </div>
    </div>
  );
}
