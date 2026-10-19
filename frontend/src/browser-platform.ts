// These capabilities must also work on the local HTTP origin http://oko.
export function randomId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte=>byte.toString(16).padStart(2,'0')).join('');
}

export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {await navigator.clipboard.writeText(text);return;}
  const previous = document.activeElement;
  const input = document.createElement('textarea');
  input.value = text;
  input.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
  document.body.append(input);
  try {
    input.select();
    if (!document.execCommand('copy')) throw new Error('Clipboard access denied by browser');
  } finally {
    input.remove();
    if(previous instanceof HTMLElement)previous.focus({preventScroll:true});
  }
}
