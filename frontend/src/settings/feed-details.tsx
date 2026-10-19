import {useEffect,useRef,useState} from 'react';
import type {SubscriptionFeed,FeedActivity,FeedPatchRequest} from '../api/generated/types.gen';
import type {MenusOwner} from './owner';
import {FeedAvatar,SourceIcon} from '../reading/tree';
import {accessLabel} from './telegram';
import './feed-details.css';

const dateFields=[['created','Created'],['modified','Modified'],['updated','Last update'],['marked_read','Marked read']] as const;
const draftKeys=['detail-title','detail-folder'];
export function FeedDetails({owner,feed,activity}:{owner:MenusOwner;feed:SubscriptionFeed;activity?:FeedActivity|null}){
 const path='feed/'+feed.id,draft=owner.draft(path),urls=feed.original_urls??[];
 const title=String(draft['detail-title']??feed.title),folder=String(draft['detail-folder']??feed.folder_id);
 const patch:FeedPatchRequest={...(title.trim()!==feed.title?{title:title.trim()}:{}),...(folder!==feed.folder_id?{folder_id:folder}:{})};
 const dirty=Object.keys(patch).length>0,busy=owner.saving.has(path+'/feed');
 const [copyStatus,setCopyStatus]=useState('');const mounted=useRef(true);const titleInput=useRef<HTMLInputElement>(null);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const reset=()=>{titleInput.current?.setCustomValidity('');for(const key of draftKeys){owner.edit(path,key,key==='detail-title'?feed.title:feed.folder_id);delete draft[key];}owner.changed();};
 const copy=async(url:string)=>{const action=owner.operations.begin('copy-link');setCopyStatus('');try{await navigator.clipboard.writeText(url);action.commit(()=>{if(mounted.current)setCopyStatus('Copied');});}catch{action.commit(()=>{if(mounted.current)setCopyStatus('Could not copy. Select and copy the link manually.');});}};
 const cancel=()=>{reset();owner.close('feeds');(document.getElementById('feeds-dialog')as HTMLDialogElement).close();};
 const loading=owner.settingsLoads.has(path),failed=owner.settingsFailures.has(path);
 const kind=feed.kind==='telegram'?'Telegram':feed.kind==='youtube'?'YouTube':feed.kind==='demo'?'Test source':feed.kind==='rss'?'RSS':'Unresolved source';
 const connection=feed.kind==='telegram'?accessLabel(feed.access):feed.transport==='rss'?'RSS':feed.transport==='youtube'?'YouTube':'No collector connection';
 return <div className="feed-details" aria-label="Feed details">
 <div className="feed-details-heading">{'avatar_url'in feed&&feed.avatar_url&&owner.fetchMedia?<FeedAvatar url={feed.avatar_url} fetchMedia={owner.fetchMedia} kind={feed.kind}/>:<SourceIcon kind={['telegram','youtube'].includes(feed.kind)?feed.kind:'rss'}/>}<div><h2 tabIndex={-1}>{feed.title}</h2><p>{kind===connection?kind:kind+' · '+connection}</p></div></div>
 <div className="feed-details-layout"><form onSubmit={event=>{event.preventDefault();const input=event.currentTarget.querySelector<HTMLInputElement>('#feed-title')!;input.setCustomValidity([...title.trim()].length<1||[...title.trim()].length>200?'Enter a title of 1–200 characters.':'');if(dirty&&event.currentTarget.reportValidity())void owner.saveFeed(feed,patch,draftKeys);}}>
 <h3>Feed details</h3><div className="feed-details-fields">
 <label htmlFor="feed-title">Title</label><input id="feed-title" ref={titleInput} required value={title} onChange={event=>{event.currentTarget.setCustomValidity('');owner.edit(path,'detail-title',event.target.value);}} onBlur={event=>event.currentTarget.setCustomValidity([...title.trim()].length<1||[...title.trim()].length>200?'Enter a title of 1–200 characters.':'')} />
 <div className="feed-details-links">{urls.length?urls.map((url,index)=><div key={index}><label htmlFor={'feed-link-'+index}>{index===0?'Link':index===1?'Additional links':'Additional link '+(index+1)}</label><div className="feed-details-link"><input id={'feed-link-'+index} readOnly value={url}/><button type="button" aria-label={index===0?'Copy link':'Copy link '+(index+1)} title="Copy link" onClick={()=>void copy(url)}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg></button></div></div>):<><span>Link</span><p>Not available</p></>}</div>
 <label htmlFor="feed-folder">Folder</label><select id="feed-folder" value={folder} onChange={event=>owner.edit(path,'detail-folder',event.target.value)}>{!owner.subscriptions.folders.some(f=>f.id===folder)&&<option value={folder}>Folder unavailable</option>}{owner.subscriptions.folders.map(f=><option key={f.id} value={f.id}>{f.title}</option>)}</select>
 </div><p className="feed-copy-status" role="status">{copyStatus}</p>
 <h3>Description</h3><p className="feed-description">{activity?.description||'Not available'}</p>
 <div className="feed-details-actions"><button type="button" disabled={!dirty||busy} onClick={reset}>Reset</button><span/><button type="button" onClick={cancel}>Cancel</button><button type="submit" className="feed-details-save" disabled={!dirty||busy}>{busy?'Saving…':'Save'}</button></div>
 </form><aside className="feed-activity" aria-label="Activity"><h3>Activity</h3><div className="feed-unread"><strong data-unavailable={failed||!activity}>{failed?'Unavailable':activity?activity.unread:loading?'Loading…':'Unavailable'}</strong><span title="Includes hidden, loading and unavailable publications not viewed in Oko">Unread</span></div><dl>{dateFields.map(([key,label])=><div key={key}><dt>{label}</dt><dd>{activity?.[key]?<time dateTime={activity[key]}>{new Date(activity[key]).toLocaleString()}</time>:<span title="No recorded date">—</span>}</dd></div>)}</dl>{failed&&<button type="button" disabled={loading} onClick={()=>void owner.loadSettings()}>Retry</button>}</aside></div>
 </div>;
}
