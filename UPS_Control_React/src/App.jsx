import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Resultados from './pages/Resultados';
import Historico from './pages/Historico';
import EnVivo from './pages/EnVivo';
import DetalleActivo from './pages/DetalleActivo';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Resultados />} />
          <Route path="/historico" element={<Historico />} />
          <Route path="/en-vivo" element={<EnVivo />} />
          <Route path="/activo/:symbol" element={<DetalleActivo />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
