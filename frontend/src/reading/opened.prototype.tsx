/// <reference types="vite/client" />
// Three variants on the existing / reader route: ?variant=A/B/C.
// Question: how should collapsed history sit below the first-class current text?
// THROWAWAY. Synthetic content only; no API, accounts, processing or persistence.
import {useEffect,useState} from 'react';
import {ReaderShell} from '../shell';
import {initializeReaderShell,mountSurface} from '../renderer';
import './opened.prototype.css';

const title='AI-агент Tolan, которым пользуются в основном женщины, поднял $30 млн';
const names={A:'Отдельные версии',B:'Общая история',C:'Шкала версий'};
type Variant=keyof typeof names;
const link='https://www.upstartsmedia.com/p/tolan-raises-30m-for-ai-companion-you-trust';
const versions=[{date:'20 октября, 18:42',text:'Предыдущая версия: компания привлекла $30 млн. Основатели Tolan — Квинтен Фармер, Аджай Мехта и Эван Голдшмидт.'},{date:'20 октября, 17:10',text:'Первая версия: стартап Tolan привлёк инвестиции на развитие AI-агента для повседневных задач.'}];
function Body(){return <div className="prototype-body">
  <p>Сегодня новостью про AI-агента сложно удивить. Их какое-то бесконечное количество. Но я сегодня прям погрузился <a href={link} target="_blank" rel="noopener noreferrer">в историю от Upstarts Media про стартап Tolan</a>, которые делает одноименного агента и поднял $30 млн. Он специально уходят от вайба Кремниевой долины и делают агента для самых простых задач. А 70% аудитории у них женщины.</p>
  <p>Основатели Tolan — Квинтен Фармер (CEO), Аджай Мехта и Эван Голдшмидт. Фармер до этого делал необанк Even, который продал Walmart в 2022-м. А потом с партнерами стал экспериментировать с AI.</p>
  <p>Изначально их проект назывался Portola. И он был чуть про другое. Они экспериментировали с AI-агентом, куда можно наговорить идей, он бы их проанализировал и выдал варианты развития. При этом агент был с долгой памятью. Он мог запоминать, о чем с ним разговаривал пользователь в течение нескольких месяцев, и поддерживать беседу на базе этих знаний.</p>
  <p>А потом это трансформировалось в AI-агента для простых задач и для обычных людей.</p>
</div>;}
function HistoryText({text}:{text:string}){return <><p>{text}</p><p><a href={link} target="_blank" rel="noopener noreferrer">История от Upstarts Media</a></p></>;}
function VariantA(){return <><Body/><section className="prototype-history history-a" aria-label="Предыдущие версии"><h2>Предыдущие версии <span>2</span></h2>{versions.map(v=><details key={v.date}><summary>{v.date}</summary><HistoryText text={v.text}/></details>)}</section></>;}
function VariantB(){return <><Body/><section className="prototype-history history-b" aria-label="Предыдущие версии"><details><summary>Предыдущие версии <span>2 версии</span></summary>{versions.map(v=><section key={v.date}><h3>{v.date}</h3><HistoryText text={v.text}/></section>)}</details></section></>;}
function VariantC(){return <><Body/><section className="prototype-history history-c" aria-label="Предыдущие версии"><h2>История публикации</h2><div className="timeline-current">● <strong>Актуальная версия</strong><span>20 октября, 19:18 · текст выше</span></div>{versions.map(v=><details key={v.date}><summary>{v.date}<span>Предыдущая версия</span></summary><HistoryText text={v.text}/></details>)}</section></>;}
function Preview(){
  const read=():Variant=>{const value=new URLSearchParams(location.search).get('variant');return value==='B'||value==='C'?value:'A';};
  const [variant,setVariant]=useState<Variant>(read);
  function cycle(delta:number){const keys:Variant[]=['A','B','C'];const next=keys[(keys.indexOf(variant)+delta+3)%3]!;const url=new URL(location.href);url.searchParams.set('variant',next);history.replaceState(null,'',url);setVariant(next);}
  useEffect(()=>{const key=(event:KeyboardEvent)=>{if((event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable]'))||event.altKey||event.metaKey||event.ctrlKey)return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();cycle(event.key==='ArrowRight'?1:-1);}};const pop=()=>setVariant(read());window.addEventListener('keydown',key);window.addEventListener('popstate',pop);return()=>{window.removeEventListener('keydown',key);window.removeEventListener('popstate',pop);};},[variant]);
  return <><div className="prototype-material"><p className="meta">Fully saved · Updated</p><h1>{title}</h1><p className="prototype-date">20 октября 2026, 19:18</p><div key={variant}>{variant==='A'?<VariantA/>:variant==='B'?<VariantB/>:<VariantC/>}</div></div><aside className="prototype-switcher" aria-label="Варианты прототипа"><div><button aria-label="Предыдущий вариант" onClick={()=>cycle(-1)}>←</button><strong>{variant} · {names[variant]}</strong><button aria-label="Следующий вариант" onClick={()=>cycle(1)}>→</button></div><small>ПРОТОТИП · синтетические данные · актуальная версия открыта · 2 прежних свёрнуты · ссылки активны</small></aside></>;
}
// This entry is selected only by the separate dev server; production uses main.tsx.
if(import.meta.env.DEV){
  initializeReaderShell(<ReaderShell/>);
  for(const button of document.querySelectorAll<HTMLButtonElement>('button'))button.disabled=true;
  document.getElementById('progress')!.textContent='Прототип · без подключения к источникам';
  document.getElementById('reading-nav')!.innerHTML='<h2>Чтение</h2><p class="prototype-folder">▾ Технологии <span>3</span></p><p>　Upstarts Media</p><p>　AI и продукты</p><p>▸ Дизайн</p>';
  document.getElementById('material-list')!.innerHTML='<h2>Технологии</h2><div class="prototype-card selected"><small>AI и продукты · 20 октября</small><h3></h3><span>Updated</span></div><div class="prototype-card"><small>Upstarts Media</small><h3>Как меняются привычки общения с AI</h3></div><div class="prototype-card"><small>AI и продукты</small><h3>Инструменты для повседневных задач</h3></div>';
  document.querySelector('.prototype-card h3')!.textContent=title;
  mountSurface(document.getElementById('material')!,<Preview/>);
}
