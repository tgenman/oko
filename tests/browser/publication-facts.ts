import type {PublicationResponse} from '../../frontend/src/api/generated/types.gen';

export function text(publication:PublicationResponse):string {
 return [publication.content,...publication.linked_publications.map(value=>value.content)]
   .flatMap(document=>document.blocks||[]).filter(block=>block.type==='text'&&block.role==='body')
   .map(block=>block.text).join('\n\n');
}
export function loading(publication:PublicationResponse):boolean {
 return publication.jobs.some(job=>(job.operation==='fetch'||job.operation==='download_media')&&
   (job.status==='running'||job.status==='pending'&&!job.retry_at));
}
