"""Synthetic public Telegram markup for reader HTTP checks."""
def page(posts, more=None):
    continuation = f'<a class="tme_messages_more js-messages_more" href="/s/fixture?before={more}">More</a>' if more else ''
    return '<div class="tgme_channel_info_header_title">Fixture</div><div class="tgme_channel_history">'+continuation+''.join(
        f'<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="fixture/{identity}"><div class="tgme_widget_message_text">{text}</div><a class="tgme_widget_message_date" href="https://t.me/fixture/{identity}"><time datetime="2026-10-15T12:00:00+00:00"></time></a></div></div>'
        for identity,text in posts)+'</div>'


