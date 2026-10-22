"""Synthetic public Telegram markup for reader HTTP checks."""
def page(posts, more=None):
    continuation = f'<a class="tme_messages_more js-messages_more" href="/s/fixture?before={more}">More</a>' if more else ''
    return '<div class="tgme_channel_info_header_title">Fixture</div><div class="tgme_channel_history">'+continuation+''.join(
        f'<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="fixture/{identity}"><div class="tgme_widget_message_text">{text}</div><a class="tgme_widget_message_date" href="https://t.me/fixture/{identity}"><time datetime="2026-10-15T12:00:00+00:00"></time></a></div></div>'
        for identity,text in posts)+'</div>'



AUDIO = '<a class="tgme_widget_message_document_wrap" href="https://t.me/fixture/11"><div class="tgme_widget_message_document_icon audio"></div><div class="tgme_widget_message_document_title">A recorded lesson</div><div class="tgme_widget_message_document_extra">Fixture author</div></a>'

VIDEO = '<a class="tgme_widget_message_video_player not_supported" href="https://t.me/fixture/11"><i class="tgme_widget_message_video_thumb" style="background-image:url(\'https://cdn.example/poster.jpg\')"></i><time class="message_video_duration">0:58</time><div class="message_media_not_supported_label">Media is too big</div></a>'


def external_page(posts, before=0):
    """Media widgets are siblings of own text in Telegram's public HTML."""
    from bs4 import BeautifulSoup
    document=BeautifulSoup(page([(identity,text) for identity,text in posts if not before or identity<before]),'html.parser')
    for text in document.select('.tgme_widget_message_text'):
        for media in reversed(text.select('.tgme_widget_message_video_player, .tgme_widget_message_document_wrap')):
            text.insert_after(media.extract())
    return str(document)


def mark_post_unavailable(html, identity):
    from bs4 import BeautifulSoup
    document = BeautifulSoup(html, 'html.parser')
    wrapper = document.select_one('[data-post="'+identity+'"]')
    if wrapper:
        wrapper['class'].append('text_not_supported_wrap')
        wrapper.append(BeautifulSoup('<div class="message_media_not_supported"><div class="message_media_not_supported_label">Please open Telegram to view this post</div><a class="message_media_view_in_telegram" href="https://t.me/'+identity+'">VIEW IN TELEGRAM</a></div>', 'html.parser'))
    return str(document)

# Structure checked against public embed lexkravetski/861 on 2026-10-22;
# URL/identity are synthetic so tests never contact the real channel.
DOCUMENT = '<a class="tgme_widget_message_document_wrap" href="https://t.me/fixture/13"><div class="tgme_widget_message_document_icon accent_bg"></div><div class="tgme_widget_message_document"><div class="tgme_widget_message_document_title accent_color">Бесы.txt</div><div class="tgme_widget_message_document_extra">36.2 KB</div></div></a>'
