import { LISTA_CATEGORIAS } from '../api/categorias';

export default function FiltroActivos({ busqueda, setBusqueda, categoria, setCategoria }) {
  return (
    <div className="flex gap-2.5 flex-wrap mb-4">
      <input
        type="text"
        value={busqueda}
        onChange={(e) => setBusqueda(e.target.value)}
        placeholder="Buscar simbolo..."
        className="flex-1 min-w-[180px] px-3 py-2.5 rounded-lg border border-border bg-bg text-text
                   font-mono text-sm focus:outline-none focus:border-accent-dim"
      />
      <select
        value={categoria}
        onChange={(e) => setCategoria(e.target.value)}
        className="px-3 py-2.5 rounded-lg border border-border bg-bg text-text text-sm cursor-pointer
                   focus:outline-none focus:border-accent-dim"
      >
        <option value="">Todas las categorias</option>
        {LISTA_CATEGORIAS.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>
    </div>
  );
}
