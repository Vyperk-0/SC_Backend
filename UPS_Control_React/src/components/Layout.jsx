import { NavLink, Outlet } from 'react-router-dom';

const navItems = [
  { to: '/', num: '01', label: 'Resultados' },
  { to: '/historico', num: '02', label: 'Historico' },
  { to: '/en-vivo', num: '03', label: 'En vivo', pulse: true },
];

export default function Layout() {
  return (
    <div className="flex min-h-screen bg-bg text-text font-sans">
      {/* Sidebar */}
      <aside className="w-[220px] shrink-0 bg-surface border-r border-border p-6 flex flex-col gap-1 md:flex md:flex-col md:w-[220px]
                         max-md:w-full max-md:flex-row max-md:overflow-x-auto max-md:p-4 max-md:border-r-0 max-md:border-b">
        <div className="font-display font-bold text-lg mb-0.5 ml-1 max-md:hidden">
          UPS <span className="text-accent">Control</span>
        </div>
        <div className="text-[0.7rem] text-text-muted uppercase tracking-wider mb-6 ml-1 max-md:hidden">
          Sistema de senales
        </div>

        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2.5 rounded-[10px] text-[0.88rem] font-medium border transition-colors shrink-0
               ${isActive
                 ? 'bg-surface-alt text-text border-border'
                 : 'text-text-muted border-transparent hover:bg-surface-alt hover:text-text'}`
            }
          >
            {({ isActive }) => (
              <>
                <span className={`font-mono text-[0.78rem] ${isActive ? 'text-accent' : 'text-accent-dim'}`}>
                  {item.num}
                </span>
                {item.label}
                {item.pulse && (
                  <span className="ml-auto w-[7px] h-[7px] rounded-full bg-long pulse-dot" />
                )}
              </>
            )}
          </NavLink>
        ))}
      </aside>

      {/* Contenido principal */}
      <main className="flex-1 max-w-[1600px] mx-auto w-full p-8 px-10 max-md:p-4">
        <Outlet />
      </main>
    </div>
  );
}
