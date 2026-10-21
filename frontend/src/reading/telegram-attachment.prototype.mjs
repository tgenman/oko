// THROWAWAY: runs the existing reader route with static public-post content.
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
const server=await createServer({configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),plugins:[{name:'telegram-attachment-prototype',transformIndexHtml:html=>html.replace('/src/main.tsx','/src/reading/telegram-attachment.prototype.tsx')}],server:{host:'127.0.0.1',port:5188,strictPort:true}});
await server.listen();
console.log('Telegram attachment prototype: http://127.0.0.1:5188/?variant=A');
