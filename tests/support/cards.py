"""Read complete synthetic test collections through the paginated HTTP interface."""
from urllib.parse import parse_qsl, urlsplit


def read_all_cards(client, url='/api/v3/cards', **kwargs):
    parsed=urlsplit(url)
    params=dict(parse_qsl(parsed.query))
    params.update(kwargs.pop('params', {}))
    params.setdefault('limit',200)
    result=[]
    while True:
        response=client.get(parsed.path, params=params, **kwargs)
        assert response.status_code == 200, response.text
        page=response.json()
        result.extend(page['items'])
        cursor=page['next_cursor']
        if cursor is None:
            return result
        params['cursor']=cursor


def read_publications_for_cards(client, url='/api/v3/cards', **kwargs):
    """Open selected publications when a test needs text or processing results."""
    materials=[]
    for card in read_all_cards(client,url,**kwargs):
        response=client.get('/api/v3/publication/'+card['id'])
        assert response.status_code == 200, response.text
        materials.append(response.json())
    return materials
