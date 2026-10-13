import {element} from './controls.js';

// The active menu owns the page stack; field drafts remain with their editor.
export function createSettingsNavigation({onLeave, onEnter}) {
  let dialog = null, page = 'home', stack = [];
  const buttons = new Map();
  for (const name of ['settings', 'feeds']) {
    const owner = document.getElementById(name + '-dialog');
    const button = element('button', '‹ Назад'); button.className = 'settings-back'; button.hidden = true;
    button.onclick = () => {
      const previous = stack.pop();
      if (!previous) return;
      const leaving = page; page = previous.page;
      onLeave(leaving, page); refresh(); onEnter(page);
      const target = previous.trigger?.isConnected ? previous.trigger : [...dialog.querySelectorAll('[data-settings-target]')].find(node => node.dataset.settingsTarget === previous.target && !node.closest('[hidden]'));
      if (target && !target.closest('[hidden]')) target.focus({preventScroll:true});
      else focusHeading();
    };
    owner.querySelector('.dialog-header').prepend(button); buttons.set(owner, button);
  }
  function refresh() {
    if (!dialog) return;
    for (const node of dialog.querySelectorAll('[data-settings-page]')) node.hidden = node.dataset.settingsPage !== page;
    buttons.get(dialog).hidden = stack.length === 0;
    dialog.dataset.settingsView = page;
  }
  function show(next, trigger) {
    if (!dialog || next === page) return;
    const leaving = page; stack.push({page, trigger, target:trigger?.dataset.settingsTarget}); page = next;
    onLeave(leaving, page); refresh(); onEnter(page);
    dialog.scrollTop = 0;
    focusHeading();
  }
  function focusHeading() {
    const heading = [...dialog.querySelectorAll('[data-settings-page] h2')].find(n => !n.closest('[hidden]'));
    if (heading) {heading.tabIndex = -1; heading.focus({preventScroll:true});}
  }
  return {
    show, refresh,
    activate(owner) {dialog = owner; page = 'home'; stack = []; refresh();},
  };
}
