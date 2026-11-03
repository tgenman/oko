export function AdminShell(){return <div id="admin-page" hidden aria-labelledby="admin-title">
  <div className="admin-heading"><div><p className="admin-eyebrow">OKO CONTROLS</p><h1 id="admin-title" tabIndex={-1}>Admin</h1><p>Source collection, processing, and operational status.</p></div><span id="admin-power" role="status">Connecting…</span></div>
  <div className="admin-command"><div id="admin-controls" className="reader-toolbar"><button id="collect" type="button">Refresh Sources</button><button id="stop" type="button">Stop</button><button id="status-toggle" type="button" aria-controls="status-panel" aria-expanded="false">Problems</button></div><p>Oko keeps running after you close the browser.</p></div>
  <div className="admin-progress"><div id="progress" role="status"/></div>
  <div className="admin-grid"><div className="admin-card"><h2>Needs attention</h2><div id="admin-attention"/><button id="admin-problems" type="button">Open Problems →</button></div><div className="admin-card"><h2>In progress</h2><div id="admin-work"/></div></div>
</div>;}
