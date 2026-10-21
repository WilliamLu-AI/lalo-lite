"""Validated inline document images and deterministic ordinal references."""
import base64,io,re
from PIL import Image
from .contracts import ProviderError

def validate_image(value):
    if not value:return ''
    if not isinstance(value,str) or len(value)>220000:raise ValueError('Image exceeds 160 KB limit')
    match=re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)',value)
    if not match:raise ValueError('Only inline PNG, JPEG and WebP images are supported')
    try:
        raw=base64.b64decode(match[2],validate=True)
        with Image.open(io.BytesIO(raw)) as im:
            if im.format.lower()!=match[1] or im.width*im.height>12_000_000:raise ValueError('Invalid image dimensions or format')
            im.verify()
    except Exception as exc:raise ValueError('Invalid image') from exc
    return value

def image_reference(text, blocks, current, *, sequential=False):
    images=[b for b in blocks if b.get('imageIndex')]
    number_pattern=r'([0-9零一二两三四五六七八九十百]+)'
    match=(re.search(r'第\s*'+number_pattern+r'\s*(?:张|幅)?\s*(?:图片|插图|图)',text)
           or re.search(r'(?:图片|插图|图)\s*(?:第\s*)?'+number_pattern,text))
    if match:
        token=match[1];digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits['两']=2
        if token.isdigit():number=int(token)
        else:
            number=0;pending=0
            for c in token:
                if c in ('十','百'):
                    number+=(pending or 1)*({'十':10,'百':100}[c]);pending=0
                else:pending=digits.get(c,0)
            number+=pending
        if not 1<=number<=len(images):raise ProviderError('没有找到这张图片，请确认图片编号')
        return images[number-1]
    # Only explicit references to the visible picture (or sequential reading)
    # may pin the answer to it. Other requests must let the model choose a target.
    if sequential or re.search(r'(?:这|那|当前|眼前)(?:一)?(?:张|幅)?(?:图片|插图|图)',text):
        return next((b for b in images if b['id']==current),None)
    return None
