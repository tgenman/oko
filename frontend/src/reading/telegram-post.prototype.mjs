// THROWAWAY: synthetic reader, no API or persisted data.
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
const server=await createServer({configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),plugins:[{name:'telegram-post-prototype',transformIndexHtml:html=>html.replace('/src/main.tsx','/src/reading/telegram-post.prototype.tsx')}],server:{host:'127.0.0.1',port:5188,strictPort:true}});
await server.listen();
console.log('http://127.0.0.1:5188/?variant=A&case=post');
