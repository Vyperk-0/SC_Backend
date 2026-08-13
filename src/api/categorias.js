// Mapa de simbolo -> categoria, usado por el buscador/filtro.
// Estatico por ahora (coincide con la lista configurada en los EAs);
// si el backend algun dia expone esto dinamicamente, se puede
// reemplazar por un fetch.

export const CATEGORIAS = {
  ADOBE: 'Shares US', ALCOA: 'Shares US', ALIBABA: 'Shares US', AMAZON: 'Shares US', AMD: 'Shares US',
  AMEX: 'Shares US', APPLE: 'Shares US', BOA: 'Shares US', BOEING: 'Shares US', BOOKING: 'Shares US',
  CHEVRON: 'Shares US', CISCO: 'Shares US', CITI: 'Shares US', COKE: 'Shares US', Coinbase: 'Shares US',
  DEVON: 'Shares US', DISNEY: 'Shares US', EBAY: 'Shares US', EXXON: 'Shares US', FORD: 'Shares US',
  GE: 'Shares US', GOOGLE: 'Shares US', GS: 'Shares US', HLT: 'Shares US', IBM: 'Shares US',
  ILMN: 'Shares US', INTEL: 'Shares US', JNJ: 'Shares US', JPMORGAN: 'Shares US', LAM: 'Shares US',
  MCARD: 'Shares US', MCDON: 'Shares US', META: 'Shares US', MICROCHIP: 'Shares US', MICRON: 'Shares US',
  MODERNA: 'Shares US', MSFT: 'Shares US', NIKE: 'Shares US', NVIDIA: 'Shares US', Netflix: 'Shares US',
  PAYPAL: 'Shares US', PEPSI: 'Shares US', PFIZER: 'Shares US', QCOM: 'Shares US', SALESFORCE: 'Shares US',
  STARBUCKS: 'Shares US', TEVA: 'Shares US', Tesla: 'Shares US', UBER: 'Shares US', VISA: 'Shares US',

  AUDCAD: 'Forex', AUDCHF: 'Forex', AUDJPY: 'Forex', AUDNZD: 'Forex', AUDSGD: 'Forex', AUDUSD: 'Forex',
  CADCHF: 'Forex', CADJPY: 'Forex', CHFJPY: 'Forex', CHFPLN: 'Forex', CHFSGD: 'Forex', EURAUD: 'Forex',
  EURCAD: 'Forex', EURCHF: 'Forex', EURDKK: 'Forex', EURGBP: 'Forex', EURHUF: 'Forex', EURJPY: 'Forex',
  EURNOK: 'Forex', EURNZD: 'Forex', EURPLN: 'Forex', EURSGD: 'Forex', EURUSD: 'Forex', EURZAR: 'Forex',
  GBPAUD: 'Forex', GBPCAD: 'Forex', GBPCHF: 'Forex', GBPJPY: 'Forex', GBPNZD: 'Forex', GBPPLN: 'Forex',
  GBPSGD: 'Forex', GBPUSD: 'Forex', GBPZAR: 'Forex', NZDCAD: 'Forex', NZDCHF: 'Forex', NZDJPY: 'Forex',
  NZDUSD: 'Forex', SGDJPY: 'Forex', USDAED: 'Forex', USDAEDr: 'Forex', USDCAD: 'Forex', USDCHF: 'Forex',
  USDCNH: 'Forex', USDCZK: 'Forex', USDDKK: 'Forex', USDGHS: 'Forex', USDHKD: 'Forex', USDHUF: 'Forex',
  USDIDR: 'Forex', USDJPY: 'Forex', USDKES: 'Forex', USDMXN: 'Forex', USDNGN: 'Forex', USDNOK: 'Forex',
  USDPLN: 'Forex', USDRUB: 'Forex', USDSEK: 'Forex', USDSGD: 'Forex', USDTHB: 'Forex', USDTRY: 'Forex',
  USDZAR: 'Forex', ZARJPY: 'Forex',

  XAUEUR: 'Oro', XAUUSD: 'Oro',
  XAGEUR: 'Silver', XAGUSD: 'Silver',

  '#ADAUSDr': 'Crypto', '#BNBEURr': 'Crypto', '#BNBJPYr': 'Crypto', '#BNBUSDr': 'Crypto',
  '#BTCEURr': 'Crypto', '#BTCJPYr': 'Crypto', '#BTCUSDr': 'Crypto', '#DOGEUSDr': 'Crypto',
  '#ETHUSDr': 'Crypto', '#LTCUSDr': 'Crypto', '#SOLUSDr': 'Crypto', '#TRXUSDr': 'Crypto',
  '#XRPEURr': 'Crypto', '#XRPUSDr': 'Crypto',
};

export function obtenerCategoria(symbol) {
  return CATEGORIAS[symbol] || 'Otros';
}

export const LISTA_CATEGORIAS = ['Shares US', 'Forex', 'Oro', 'Silver', 'Crypto'];
