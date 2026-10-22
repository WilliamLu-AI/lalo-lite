import DOMPurify from 'dompurify';

export function renderMeetingDocument(reader, blocks, document) {
  const template = window.document.createElement('template');
  template.innerHTML = DOMPurify.sanitize(document?.html || '', {
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'object', 'embed',
      'audio', 'video', 'svg', 'math', 'meta', 'link', 'title', 'base'],
    FORBID_ATTR: ['style', 'srcset', 'href', 'id', 'name'],
  });
  for (const element of template.content.querySelectorAll('[src]')) {
    const src = element.getAttribute('src');
    if (element.tagName !== 'IMG' || src.length > 220000 ||
        !/^data:image\/(png|jpeg|webp);base64,[a-zA-Z0-9+/=]+$/.test(src)) element.removeAttribute('src');
  }
  const mapped = new Map();
  const ids = new Set(blocks.map(block => block.id));
  for (const element of template.content.querySelectorAll('[data-block]')) {
    const id = element.dataset.block;
    if (!ids.has(id)) {element.removeAttribute('data-block'); continue;}
    if (mapped.has(id)) throw new Error('Duplicate shared document block');
    mapped.set(id, element);
  }
  reader.replaceChildren();
  for (const block of blocks) {
    if (window.document.getElementById(block.id)) throw new Error('Shared document block conflicts with page controls');
    let element = mapped.get(block.id);
    if (!element) {
      element = window.document.createElement(block.heading ? 'h2' : 'p');
      element.textContent = block.text;
      template.content.append(element);
    }
    element.id = block.id;
    element.dataset.block = block.id;
    element.tabIndex = 0;
  }
  for (const element of template.content.querySelectorAll('.active')) element.classList.remove('active');
  reader.append(template.content);
}
