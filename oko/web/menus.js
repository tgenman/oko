import {createOperations} from './ui-operations.js';
import {createSettingsEditor} from './settings-editor.js';
import {createModelSettings} from './model-settings.js';
import {createFeedConnection} from './feed-connection.js';
import {createTelegramConnection} from './telegram-connection.js';
import {createOpmlImport} from './opml-import.js';
import {reportError} from './debug.js';

export function createMenus({api, onChanged}) {
  const byId = id => document.getElementById(id);
  const operations = createOperations();
  let active = null, opener = null, stopped = false;
  const editor = createSettingsEditor({api, onChanged,
    onDetail: () => byId('feeds-layout').classList.add('show-detail')});
  const models = createModelSettings({api});
  const context = () => operations.capture();
  async function selected(feed, action) {
    onChanged();
    await editor.refresh();
    await action.commit(() => open('feeds', null, 'feed/' + feed.id));
  }
  const rss = createFeedConnection({api, context, onSelected: selected});
  const telegram = createTelegramConnection({api, context, onConnected: selected});
  const opml = createOpmlImport({api, context, onImported: async action => {
    onChanged(); await editor.refresh();
    await action.commit(async () => {
      byId('feeds-layout').classList.remove('show-detail');
      await open('feeds', null, null);
    });
  }});
  function leave() {
    operations.invalidate(); editor.deactivate(); models.deactivate();
  }
  function close(name) {
    if (active !== name) return;
    leave(); active = null;
    if (opener?.isConnected) opener.focus({preventScroll:true});
    opener = null;
  }
  async function open(name, trigger, preferred) {
    if (stopped) return;
    if (!active) opener = trigger || document.activeElement;
    const previous = active;
    leave(); active = name;
    if (previous && previous !== name) byId(previous + '-dialog').close();
    if (!byId(name + '-dialog').open) byId(name + '-dialog').showModal();
    const action = operations.begin('open');
    try {
      if (name === 'add') {
        await editor.refresh();
        action.commit(() => {
          if (!byId('telegram-connection').hidden && !byId('telegram-account').value) telegram.loadAccounts();
        });
      }
      else await editor.open(name === 'settings', preferred);
      if (name === 'settings') await action.commit(() => models.open());
    } catch(error) {
      reportError(error, 'Меню Око');
      action.commit(() => {byId(name === 'add' ? 'add-message' : 'settings-message').textContent = error.message;});
    }
  }
  for (const name of ['add', 'feeds', 'settings']) {
    byId(name + '-open').onclick = event => open(name, event.currentTarget);
    byId(name + '-close').onclick = () => {close(name); byId(name + '-dialog').close();};
    byId(name + '-dialog').addEventListener('cancel', () => close(name));
    byId(name + '-dialog').addEventListener('close', () => {
      // close events are queued; a rapid reopen must retain its new lifecycle.
      if (!byId(name + '-dialog').open) close(name);
    });
  }
  byId('feeds-add').onclick = event => open('add', event.currentTarget);
  byId('feeds-back').onclick = () => byId('feeds-layout').classList.remove('show-detail');
  for (const name of ['rss', 'telegram', 'opml']) {
    byId('add-' + name + '-tab').onclick = () => {
      operations.invalidate();
      for (const [other, target] of [['rss', 'rss-connection'], ['telegram', 'telegram-connection'], ['opml', 'opml-panel']]) {
        byId(target).hidden = other !== name;
        byId('add-' + other + '-tab').setAttribute('aria-selected', String(other === name));
      }
      if (name === 'telegram' && !byId('telegram-account').value) telegram.loadAccounts();
    };
  }
  return {
    status: editor.status,
    sources: editor.sources,
    openFeed: (id, trigger) => open('feeds', trigger, 'feed/' + id),
    stop() {
      stopped = true; leave(); editor.stop(); models.stop(); rss.stop(); telegram.stop(); opml.stop();
      for (const name of ['add', 'feeds', 'settings']) {byId(name + '-dialog').close(); byId(name + '-open').disabled = true;}
    },
  };
}
