"""Bounded listing snapshots supplied by the client's home-search web view."""
import re
from urllib.parse import urlsplit, urlunsplit


def web_listing(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('Choose a home from the app.')
    link = value.get('url')
    address = value.get('address')
    if not isinstance(link, str) or len(link) > 2048:
        raise ValueError('Choose a home from the app.')
    try:
        url = urlsplit(link)
        valid = (url.scheme == 'https' and url.hostname in ('zillow.com', 'www.zillow.com')
                 and not url.username and not url.password and url.port in (None, 443)
                 and re.fullmatch(r'/homedetails/[^/]+/[1-9][0-9]*_zpid/?', url.path))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Choose a home from the app.')
    if (not isinstance(address, str) or not address.strip() or len(address) > 300
            or any(ord(c) < 32 for c in address)):
        raise ValueError('Add the home address.')
    result = dict(url=urlunsplit(('https', 'www.zillow.com', url.path.rstrip('/') + '/', '', '')),
                  address=address.strip())
    price = value.get('price')
    if price is not None:
        if (not isinstance(price, str) or len(price) > 32 or not re.fullmatch(
                r'\$(?:[1-9][0-9]{0,2}(?:,[0-9]{3}){1,3}|[1-9][0-9]{0,11})(?:\.[0-9]{2})?\+?(?:/mo)?', price)):
            raise ValueError('Refresh the home price before sending.')
        result['price'] = price
    photo = value.get('photo_url')
    if photo is not None:
        if not isinstance(photo, str) or len(photo) > 2048:
            raise ValueError('Refresh the home photo before sending.')
        try:
            image = urlsplit(photo)
            valid = (image.scheme == 'https' and image.hostname == 'photos.zillowstatic.com'
                     and not image.username and not image.password and image.port in (None, 443)
                     and not image.query and not image.fragment and re.fullmatch(
                         r'/fp/[a-fA-F0-9]{32}-[a-zA-Z0-9_]+\.(jpg|jpeg|webp|png)', image.path))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('Refresh the home photo before sending.')
        result['photo_url'] = urlunsplit(('https', 'photos.zillowstatic.com', image.path, '', ''))
    return result
