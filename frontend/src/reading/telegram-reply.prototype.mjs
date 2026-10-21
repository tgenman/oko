// THROWAWAY: existing reader route, static snapshot of one user-specified post.
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
const server=await createServer({configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),plugins:[{name:'telegram-reply-prototype',transformIndexHtml:html=>html.replace('/src/main.tsx','/src/reading/telegram-reply.prototype.tsx')}],server:{host:'127.0.0.1',port:5190,strictPort:true}});
await server.listen();
console.log('Telegram reply prototype: http://127.0.0.1:5190/?variant=A');
