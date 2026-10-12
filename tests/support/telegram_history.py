from tests.support.telegram import Telegram

class Archive(Telegram):
    def __init__(self):
        self.rows = [dict(id=n, date=1759350000+n, text=str(n), group=88 if n in (4,5) else None, media=None) for n in range(1,7)]
    async def history_page(self, account, peer, upper, before, since, **scope):
        rows = [m for m in self.rows if (not upper or m['id']<=upper) and (not before or m['id']<before)]
        raw = sorted(rows,key=lambda m:m['id'],reverse=True)[:2]
        expanded = list(raw)
        for m in raw:
            if m['group']:
                expanded += [r for r in self.rows if r['group']==m['group'] and r not in expanded]
        return dict(messages=expanded, upper=upper or max([0]+[m['id'] for m in self.rows]), next_before=min([m['id'] for m in raw],default=before), more=len(rows)>len(raw))
    async def page(self,account,peer,after,since,**scope):
        return dict(messages=[m for m in self.rows if m['id']>after],more=False,next_after=max([after]+[m['id'] for m in self.rows]))
    async def messages(self,account,peer,ids,**scope):
        return [m for m in self.rows if m['id'] in ids]
