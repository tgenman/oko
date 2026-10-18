// THROWAWAY DEV ENTRY. Reader route /, fake data only.
import {defineConfig} from 'vite';
import {resolve} from 'node:path';
export default defineConfig({root:resolve(import.meta.dirname,'../..'),plugins:[{name:'news-menu-prototype',transformIndexHtml:html=>html.replace('/src/main.tsx','/src/reading/news-menu.prototype.tsx')}],server:{host:'127.0.0.1',port:5189,strictPort:true}});
