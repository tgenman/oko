"""Synthetic Telegram read fixture for storage tests."""

def setup(store):
    with store.db:
        store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls,auto_read,read_history) VALUES ('feed','feed','Feed','default','telegram','telegram','connected','[]',1,0)")
        store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
        store.subscriptions.telegram.put_binding('feed',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
        store.db.execute("INSERT INTO telegram_read_state(feed,since,initial,confirmed,waiting,error) VALUES ('feed',1,0,0,'',NULL)")
