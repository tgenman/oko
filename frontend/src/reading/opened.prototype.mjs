// Throwaway preview: real reader shell, synthetic material, no API or persistence.
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
const server = await createServer({
  configFile:false,
  root:fileURLToPath(new URL('../../',import.meta.url)),
  plugins:[{name:'opened-material-prototype',transformIndexHtml:html=>html.replace('/src/main.tsx','/src/reading/opened.prototype.tsx')}],
  server:{host:'127.0.0.1',port:5187,strictPort:true},
});
await server.listen();
console.log('Throwaway prototype: http://127.0.0.1:5187/?variant=A');
