// Throwaway dev configuration: keep the existing / route and the real app shell/styles.
import {defineConfig} from 'vite';
import {resolve} from 'node:path';
export default defineConfig({root:resolve(import.meta.dirname,'../..'),plugins:[{name:'read-feeds-prototype',transformIndexHtml:html=>html.replace('/src/main.tsx','/src/reading/read-feeds.prototype.tsx')}],server:{host:'127.0.0.1',port:5187,strictPort:true}});
