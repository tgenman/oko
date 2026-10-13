export function element(tag, text = '') { const node = document.createElement(tag); node.textContent = text; return node; }
export function selectControl(options, value) {
  const select = document.createElement('select');
  for (const [id, title] of options) { const option = element('option', title); option.value = id; select.append(option); }
  select.value = String(value);
  return select;
}
export function labelled(title, input) { const label = element('label', title); label.append(input); return label; }


export function createFolderPicker(prefix, defaultTitle) {
  const select = document.getElementById(prefix + '-folder');
  const input = document.getElementById(prefix + '-folder-new');
  const label = document.getElementById(prefix + '-folder-new-label');
  let disabled = select.disabled;
  let onChanged = () => {};
  function sync() {
    label.hidden = select.value !== '';
    input.disabled = disabled || label.hidden;
    select.disabled = disabled;
  }
  select.addEventListener('change', () => {sync(); onChanged();});
  input.addEventListener('input', () => {input.setCustomValidity(''); onChanged();});
  function refresh(folders) {
    const previous = select.options.length ? select.value : defaultTitle;
    const titles = [...new Set([defaultTitle, ...folders.map(folder => folder.title)])];
    select.replaceChildren(...titles.map(title => {
      const option = element('option', title); option.value = title; option.defaultSelected = title === defaultTitle; return option;
    }));
    const create = element('option', 'Создать новую…'); create.value = ''; select.append(create);
    if (previous === '' || titles.includes(previous)) select.value = previous;
    else {select.value = ''; input.value = previous;}
    sync(); onChanged();
  }
  refresh([]);
  return {
    refresh,
    value: () => (select.value || input.value).trim(),
    valid() {
      if (select.value !== '') return true;
      input.setCustomValidity(input.value.trim() ? '' : 'Введите название папки.');
      return input.reportValidity();
    },
    setDisabled(value) {disabled = value; sync();},
    reset() {select.value = defaultTitle; input.value = ''; input.setCustomValidity(''); sync(); onChanged();},
    onChange(callback) {onChanged = callback;},
  };
}
