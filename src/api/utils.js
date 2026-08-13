export function formatearTiempo(segundos) {
  if (segundos < 90) return `${segundos}s`;
  const min = Math.round(segundos / 60);
  if (min < 90) return `${min}min`;
  const horas = Math.round(min / 60);
  return `${horas}h`;
}

// Determina cual de los 4 colores de TT/TRWave esta activo
// (el primero que tenga un valor real, no null y no 0).
export function colorActivo(dg, mn, li, rd) {
  if (dg !== null && dg !== undefined && dg !== 0) return { clase: 'dg', label: 'Dark Green', valor: dg };
  if (mn !== null && mn !== undefined && mn !== 0) return { clase: 'mn', label: 'Maroon', valor: mn };
  if (li !== null && li !== undefined && li !== 0) return { clase: 'li', label: 'Light Green', valor: li };
  if (rd !== null && rd !== undefined && rd !== 0) return { clase: 'rd', label: 'Light Red', valor: rd };
  return { clase: 'off', label: 'Sin datos', valor: null };
}

export const DOT_COLORES = {
  dg: '#1a7a3a', mn: '#7a1a1a', li: '#3ddc84', rd: '#ff5c5c',
  aq: '#38bdf8', ye: '#eab308', off: '#232B42',
};
