import {createOperations} from './ui-operations.js';
import {createSettingsEditor} from './settings-editor.js';
import {createModelSettings} from './model-settings.js';
import {createYouTubeSettings} from './youtube-settings.js';
import {createFeedConnection} from './feed-connection.js';
import {createTelegramConnection} from './telegram-connection.js';
import {createOpmlImport} from './opml-import.js';
import {reportError} from './debug.js';
import {createFolderPicker} from './controls.js';
import {createSettingsNavigation} from './settings-navigation.js';

export function createMenus({api, onChanged}) {
  const byId = id => document.getElementById(id);
  const operations = createOperations();
  let active = null, opener = null, stopped = false;
  const rssFolder = createFolderPicker('rss', 'Без папки');
  const telegramFolder = createFolderPicker('telegram', 'Telegram');
  const editor = createSettingsEditor({api, onChanged, onFolders: folders => {rssFolder.refresh(folders); telegramFolder.refresh(folders);},
    onNavigate: (page, trigger) => navigation.show(page, trigger), onRendered: () => navigation.refresh(),
    onDetail: () => {byId('feeds-layout').classList.add('show-detail'); navigation.activate(byId('feeds-dialog'));}});
  const models = createModelSettings({api});
  const youtube = createYouTubeSettings({api});
  const navigation = createSettingsNavigation({
    onLeave() {editor.viewChanged(); models.deactivate(); youtube.deactivate();},
    onEnter(page) {
      void editor.refreshView();
      if (['models','llamacpp','codex'].includes(page)) void models.open();
      if (page === 'youtube') void youtube.open();
    },
  });
  for (const button of document.querySelectorAll('[data-settings-target]')) button.onclick = () => navigation.show(button.dataset.settingsTarget, button);
  const context = () => operations.capture();
  async function selected(feed, action) {
    onChanged();
    await editor.refresh();
    await action.commit(() => open('feeds', null, 'feed/' + feed.id));
  }
  const rss = createFeedConnection({api, context, onSelected: selected, folderPicker: rssFolder});
  const telegram = createTelegramConnection({api, context, onConnected: selected, folderPicker: telegramFolder});
  const opml = createOpmlImport({api, context, onImported: async action => {
    onChanged(); await editor.refresh();
    await action.commit(async () => {
      byId('feeds-layout').classList.remove('show-detail');
      await open('feeds', null, null);
    });
  }});
  function leave() {
    operations.invalidate(); editor.deactivate(); models.deactivate(); youtube.deactivate();
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
    if (name !== 'add') navigation.activate(byId(name + '-dialog'));
    const action = operations.begin('open');
    try {
      if (name === 'add') {
        await editor.refresh();
        action.commit(() => {
          if (!byId('telegram-connection').hidden && !byId('telegram-account').value) telegram.loadAccounts();
        });
      }
      else await editor.open(name === 'settings', preferred);
      if (name === 'settings') await action.commit(() => youtube.open());
      action.commit(navigation.refresh);
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
  byId('feeds-back').onclick = () => {editor.viewChanged(); navigation.activate(byId('feeds-dialog')); byId('feeds-layout').classList.remove('show-detail'); editor.focusOwner();};
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
    status(state) {editor.status(state.initial_histories || []); youtube.status(state.youtube);},
    sources: editor.sources,
    openFeed: (id, trigger) => open('feeds', trigger, 'feed/' + id),
    async openStatus(target, trigger) {
      if (target.kind === 'feed-settings') {await open('feeds', trigger, 'feed/' + target.id); if (target.page) navigation.show(target.page, trigger); return;}
      if (target.kind === 'add') {await open('add',trigger);byId('add-'+target.page+'-tab')?.click();return;}
      await open(target.scope && target.scope !== 'oko' ? 'feeds' : 'settings', trigger, target.scope && target.scope !== 'oko' ? target.scope+'/'+target.id : undefined);
      navigation.show(target.page || 'home', trigger);
    },
    stop() {
      stopped = true; leave(); editor.stop(); models.stop(); youtube.stop(); rss.stop(); telegram.stop(); opml.stop();
      for (const name of ['add', 'feeds', 'settings']) {byId(name + '-dialog').close(); byId(name + '-open').disabled = true;}
    },
  };
}
