// THROWAWAY: synthetic admin UI only; never starts the Oko runtime.
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
const server=await createServer({configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),plugins:[{name:'admin-prototype',transformIndexHtml:html=>html.replace('/src/main.tsx','/src/status/admin.prototype.tsx')}],server:{host:'127.0.0.1',port:5191,strictPort:true}});
await server.listen();
console.log('Admin prototype: http://127.0.0.1:5191/admin?variant=A');
