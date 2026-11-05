"""Assertions over the public publication, media and Job contract."""


def body_text(publication):
    documents=[publication['content'],*(article['content'] for article in publication['linked_publications'])]
    return '\n\n'.join(block['text'] for document in documents for block in document.get('blocks',[])
                       if block.get('type')=='text' and block.get('role')=='body')


def subtitle_text(client,publication):
    identifiers={block['media_id'] for block in publication['content'].get('blocks',[])
                 if block.get('type')=='media' and block.get('role')=='subtitles'}
    file=next((item for item in publication['media'] if item['id'] in identifiers and item['url']),None)
    return client.get(file['url']).text if file else ''


def loading(publication):
    return any(job['operation'] in ('fetch','download_media') and
               (job['status']=='running' or job['status']=='pending' and not job['retry_at'])
               for job in publication['jobs'])


def reasons(publication,operation=None):
    return [job['reason_message'] for job in publication['jobs']
            if job['reason_message'] and (operation is None or job['operation']==operation)]


def reason(publication,operation):
    return next(iter(reasons(publication,operation)),None)


def summary(publication):
    value=publication['summarized_content']
    return value if 'points' in value else None


def filter_result(publication):
    value=publication['filtered_content']
    return value if 'hidden' in value else None


def filter_state(publication):
    job=next((job for job in publication['jobs'] if job['operation']=='filter' and job['object_id']==publication['id']),None)
    if job and job['status']=='running':return 'processing'
    if job and job['reason_message']:return 'error'
    if filter_result(publication) is not None:return 'ready'
    return 'pending' if job and job['status'] in ('pending','blocked') else 'disabled'


def collected(publication):
    documents=[publication['content'],*(article['content'] for article in publication['linked_publications'])]
    if any('blocks' not in document for document in documents):return False
    if publication['status']=='pending_collection' or any(article['status']=='pending_collection' for article in publication['linked_publications']):return False
    if any(file['status']!='ready' or not file['url'] for file in publication['media']):return False
    if any(job['operation'] in ('fetch','download_media') and job['reason_message'] for job in publication['jobs']):return False
    return not any(block.get('type')=='external_media' and (block.get('kind')=='web' or block.get('reason')=='public_preview_unavailable') for document in documents for block in document['blocks'])


def captions_state(publication):
    identifiers={block['media_id'] for block in publication['content'].get('blocks',[])
                 if block.get('type')=='media' and block.get('role')=='subtitles'}
    media=next((item for item in publication['media'] if item['id'] in identifiers),None)
    if media is None:return 'not_requested'
    if media['status']=='ready':return 'available'
    job=next((item for item in publication['jobs'] if item['operation']=='download_media' and item['object_id']==media['id']),None)
    if job is None or job['status']=='cancelled':return 'not_requested'
    if job['status']=='running':return 'running'
    if job['status']=='done' and job['reason_code']=='subtitles_absent':return 'unavailable'
    if job['status'] in ('failed','unavailable') or job['reason_message'] and not job['retry_at']:return 'error'
    return 'pending'
