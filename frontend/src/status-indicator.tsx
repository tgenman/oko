import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';

type Indicator = {stopped: boolean; online: boolean | null; count: number};

function StatusIndicator({stopped, online, count}: Indicator) {
  return <>{stopped ? 'Око остановлено' : !online ? 'Нет связи с Око' : count ? `Проблемы · ${count}` : 'Состояние'}</>;
}

// Until the whole panel is migrated, React owns only this button's contents.
export function createStatusIndicator(host: HTMLElement) {
  const root = createRoot(host);
  return (state: Indicator) => flushSync(() => root.render(<StatusIndicator {...state}/>));
}
