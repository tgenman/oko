export function element(tag, text = '') { const node = document.createElement(tag); node.textContent = text; return node; }
export function selectControl(options, value) {
  const select = document.createElement('select');
  for (const [id, title] of options) { const option = element('option', title); option.value = id; select.append(option); }
  select.value = String(value);
  return select;
}
export function labelled(title, input) { const label = element('label', title); label.append(input); return label; }
