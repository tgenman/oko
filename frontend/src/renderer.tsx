import {Component, type ErrorInfo, type ReactNode} from 'react';
import {createPortal, flushSync} from 'react-dom';
import {createRoot, type Root} from 'react-dom/client';

let root: Root | undefined;
let shell: ReactNode = null;
const surfaces = new Map<HTMLElement, ReactNode>();
let reportRenderError: (error:Error, source:string) => void = () => {};

class SurfaceBoundary extends Component<{children:ReactNode; name:string}, {failed:boolean}> {
  state = {failed:false};
  static getDerivedStateFromError() {return {failed:true};}
  componentDidCatch(error:Error, _info:ErrorInfo) {reportRenderError(error,'Показ интерфейса: '+this.props.name);}
  render() {return this.state.failed ? <p role="alert">Не удалось показать интерфейс Око. Подробности в диагностике.</p> : this.props.children;}
}

function render() {
  if (!root) {
    let host = document.getElementById('react-root');
    if (!host) {host=document.createElement('div');host.id='react-root';document.body.append(host);}
    root = createRoot(host);
  }
  flushSync(() => root!.render(<>{shell}{[...surfaces].map(([host,node]) => createPortal(<SurfaceBoundary name={host.id}>{node}</SurfaceBoundary>,host,host.id))}</>));
}

// Composition only: each surface keeps its own React state and resources.
// Portals let untouched owners retain exclusive containers during migration.
export function mountSurface(host:HTMLElement, node:ReactNode) {
  // Claim this container once; portals do not clear a legacy placeholder.
  if (!surfaces.has(host)) host.replaceChildren();
  surfaces.set(host,node); render();
  return () => {surfaces.delete(host);render();};
}

export function initializeReaderShell(node:ReactNode) {shell=node;render();}
export function setRenderReporter(reporter:typeof reportRenderError) {reportRenderError=reporter;}
