// DOM -> markdown for one assistant turn. innerText loses code fences and
// picks up the Copy/Edit button labels that live inside code-block headers.
(root) => {
  const SKIP = new Set(['BUTTON', 'SVG', 'SCRIPT', 'STYLE', 'PATH']);

  function inline(node) {
    if (node.nodeType === 3) return node.textContent;
    if (node.nodeType !== 1 || SKIP.has(node.tagName)) return '';
    const kids = () => Array.from(node.childNodes).map(inline).join('');
    switch (node.tagName) {
      case 'CODE': return '`' + node.textContent + '`';
      case 'STRONG': case 'B': return '**' + kids() + '**';
      case 'EM': case 'I': return '*' + kids() + '*';
      case 'DEL': return '~~' + kids() + '~~';
      case 'A': return '[' + kids() + '](' + (node.getAttribute('href') || '') + ')';
      case 'BR': return '\n';
      default: return kids();
    }
  }

  function list(node, ordered, depth) {
    const pad = '  '.repeat(depth);
    return Array.from(node.children)
      .filter((li) => li.tagName === 'LI')
      .map((li, i) => {
        const nested = Array.from(li.children).filter((c) => c.tagName === 'UL' || c.tagName === 'OL');
        const own = Array.from(li.childNodes).filter((c) => !nested.includes(c)).map(inline).join('').trim();
        const head = pad + (ordered ? i + 1 + '. ' : '- ') + own;
        const rest = nested.map((n) => list(n, n.tagName === 'OL', depth + 1)).join('\n');
        return rest ? head + '\n' + rest : head;
      })
      .join('\n');
  }

  function table(node) {
    const rows = Array.from(node.querySelectorAll('tr')).map((tr) =>
      Array.from(tr.children).map((td) => inline(td).trim()));
    if (!rows.length) return '';
    const out = ['| ' + rows[0].join(' | ') + ' |', '| ' + rows[0].map(() => '---').join(' | ') + ' |'];
    for (const r of rows.slice(1)) out.push('| ' + r.join(' | ') + ' |');
    return out.join('\n');
  }

  function block(node) {
    if (node.nodeType === 3) return node.textContent.trim();
    if (node.nodeType !== 1 || SKIP.has(node.tagName)) return '';
    switch (node.tagName) {
      case 'PRE': {
        const code = node.querySelector('code');
        const cls = (code && code.className) || '';
        // As of 2026-09-10 ChatGPT renders no language-* class and no header
        // label, so fences come back bare. Content is intact; only the syntax
        // hint is lost. Phase 5 (SSE interception) recovers the real markdown.
        const lang = (cls.match(/language-([\w+-]+)/) || [, ''])[1];
        return '```' + lang + '\n' + (code ? code.textContent : node.textContent).replace(/\n$/, '') + '\n```';
      }
      case 'UL': return list(node, false, 0);
      case 'OL': return list(node, true, 0);
      case 'TABLE': return table(node);
      case 'HR': return '---';
      case 'BLOCKQUOTE':
        return inline(node).trim().split('\n').map((l) => '> ' + l).join('\n');
      case 'H1': case 'H2': case 'H3': case 'H4': case 'H5': case 'H6':
        return '#'.repeat(+node.tagName[1]) + ' ' + inline(node).trim();
      case 'P': return inline(node).trim();
      default:
        // container: recurse, else treat as a paragraph
        if (Array.from(node.children).some((c) => /^(P|PRE|UL|OL|TABLE|H[1-6]|BLOCKQUOTE|HR|DIV)$/.test(c.tagName))) {
          return Array.from(node.childNodes).map(block).filter(Boolean).join('\n\n');
        }
        return inline(node).trim();
    }
  }

  return block(root).replace(/\n{3,}/g, '\n\n').trim();
}
