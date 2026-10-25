(() => {
  const payloadNode = document.getElementById('album-data');
  if (!payloadNode) return;
  const payload = JSON.parse(payloadNode.textContent || '{}');
  const config = payload.config || {};
  const photos = Array.isArray(payload.photos) ? payload.photos : [];
  const root = document.getElementById('album-root');
  if (!root) return;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));

  document.documentElement.dataset.theme = config.theme || 'dark';
  document.documentElement.style.setProperty('--columns', String(config.columns || 3));
  document.body.dataset.template = config.template || 'grid';
  document.title = config.title || 'Album photo';

  const cover = photos.find(photo => Number(photo.id) === Number(config.cover_id)) || photos[0] || null;

  function header(options = {}) {
    const compact = options.compact === true;
    const coverStyle = !compact && cover ? ` style="background-image:url('${esc(cover.src)}')"` : '';
    const subtitle = config.subtitle ? `<p class="album-subtitle">${esc(config.subtitle)}</p>` : '';
    const intro = config.intro ? `<div class="album-intro">${esc(config.intro)}</div>` : '';
    const author = config.author ? `<span>${esc(config.author)}</span>` : '';
    return `
      <header class="album-header ${!compact && cover ? 'has-cover' : ''}"${coverStyle}>
        <div class="album-header-inner">
          <p class="eyebrow">Album photo</p>
          <h1>${esc(config.title || 'Mon album')}</h1>
          ${subtitle}
          ${intro}
          <div class="album-stats"><span>${photos.length} photo${photos.length > 1 ? 's' : ''}</span>${author}</div>
        </div>
      </header>`;
  }

  function tags(photo) {
    if (!Array.isArray(photo.tags) || !photo.tags.length) return '';
    return `<div class="album-item-tags">${photo.tags.map(tag => esc(tag)).join(' · ')}</div>`;
  }

  function caption(photo) {
    if (!config.show_captions && !config.show_metadata) return '';
    return `<figcaption>
      ${config.show_captions ? `<div class="album-item-title">${esc(photo.title)}</div>` : ''}
      ${config.show_metadata && photo.meta ? `<div class="album-item-meta">${esc(photo.meta)}</div>` : ''}
      ${config.show_metadata ? tags(photo) : ''}
    </figcaption>`;
  }

  function item(photo, index) {
    return `<figure class="album-item" data-index="${index}" tabindex="0" role="button" aria-label="Agrandir ${esc(photo.title)}">
      <div class="album-image"><img src="${esc(photo.src)}" alt="${esc(photo.title)}" loading="lazy"></div>
      ${caption(photo)}
    </figure>`;
  }

  function footer() {
    return `<footer class="album-footer"><span>${esc(config.title || 'Album photo')}</span><span>${new Date().getFullYear()}</span></footer>`;
  }

  function empty() {
    root.innerHTML = `<div class="album-empty"><strong>Aucune photo</strong><p>Cet album ne contient pas encore de photos.</p></div>`;
  }

  // ------------------------------------------------------------------ //
  // Lightbox (grille, mosaïque et livre)
  // ------------------------------------------------------------------ //
  let lightbox = null;
  let lightboxIndex = 0;

  function technicalMarkup(photo) {
    const fields = Array.isArray(photo.technical) ? photo.technical : [];
    return fields.length
      ? fields.map(field => `<div class="slide-tech-row"><dt>${esc(field.label)}</dt><dd>${esc(field.value)}</dd></div>`).join('')
      : '<div class="slide-tech-empty">Aucune information technique pour cette photo.</div>';
  }

  function ensureLightbox() {
    if (lightbox) return lightbox;
    lightbox = document.createElement('div');
    lightbox.className = 'lightbox';
    lightbox.hidden = true;
    lightbox.innerHTML = `
      <button class="lightbox-button lightbox-close" type="button" aria-label="Fermer">×</button>
      <button class="lightbox-button lightbox-prev" type="button" aria-label="Photo précédente">‹</button>
      <button class="lightbox-button lightbox-next" type="button" aria-label="Photo suivante">›</button>
      <button class="lightbox-tech-toggle" type="button" aria-expanded="false" aria-controls="lightbox-tech-panel">Infos techniques</button>
      <aside class="lightbox-tech-panel" id="lightbox-tech-panel" hidden>
        <div class="slide-tech-head">
          <strong>Infos techniques</strong>
          <button class="lightbox-tech-close" type="button" aria-label="Fermer les infos techniques">×</button>
        </div>
        <dl class="slide-tech-list lightbox-tech-list"></dl>
      </aside>
      <figure class="lightbox-figure">
        <img alt="">
        <figcaption class="lightbox-caption"></figcaption>
      </figure>`;
    document.body.appendChild(lightbox);
    const techToggle = lightbox.querySelector('.lightbox-tech-toggle');
    const techPanel = lightbox.querySelector('.lightbox-tech-panel');
    const setTechOpen = open => {
      techPanel.hidden = !open;
      techToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      techToggle.textContent = open ? 'Masquer les infos' : 'Infos techniques';
    };
    lightbox.querySelector('.lightbox-close').addEventListener('click', closeLightbox);
    lightbox.querySelector('.lightbox-prev').addEventListener('click', () => moveLightbox(-1));
    lightbox.querySelector('.lightbox-next').addEventListener('click', () => moveLightbox(1));
    lightbox.querySelector('.lightbox-tech-close').addEventListener('click', () => setTechOpen(false));
    techToggle.addEventListener('click', () => setTechOpen(techPanel.hidden));
    lightbox.addEventListener('click', event => {
      if (event.target === lightbox) closeLightbox();
    });
    document.addEventListener('keydown', event => {
      if (lightbox.hidden) return;
      if (event.key === 'Escape') {
        if (!techPanel.hidden) setTechOpen(false);
        else closeLightbox();
      }
      if (event.key === 'ArrowLeft') moveLightbox(-1);
      if (event.key === 'ArrowRight') moveLightbox(1);
    });
    return lightbox;
  }

  function renderLightbox() {
    if (!lightbox || !photos.length) return;
    const photo = photos[lightboxIndex];
    lightbox.querySelector('img').src = photo.src;
    lightbox.querySelector('img').alt = photo.title || '';
    lightbox.querySelector('.lightbox-caption').innerHTML =
      `<strong>${esc(photo.title)}</strong>${photo.meta ? `<small>${esc(photo.meta)}</small>` : ''}`;
    lightbox.querySelector('.lightbox-tech-list').innerHTML = technicalMarkup(photo);
  }

  function openLightbox(index) {
    if (!photos.length) return;
    ensureLightbox();
    lightboxIndex = (index + photos.length) % photos.length;
    lightbox.hidden = false;
    document.body.style.overflow = 'hidden';
    renderLightbox();
  }

  function closeLightbox() {
    if (!lightbox) return;
    lightbox.hidden = true;
    document.body.style.overflow = '';
  }

  function moveLightbox(delta) {
    if (!lightbox || lightbox.hidden) return;
    lightboxIndex = (lightboxIndex + delta + photos.length) % photos.length;
    renderLightbox();
  }

  function bindLightbox() {
    root.querySelectorAll('.album-item').forEach(element => {
      element.addEventListener('click', () => openLightbox(Number(element.dataset.index)));
      element.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openLightbox(Number(element.dataset.index));
        }
      });
    });
  }

  // ------------------------------------------------------------------ //
  // Grille et mosaïque
  // ------------------------------------------------------------------ //
  function renderGrid() {
    if (!photos.length) return empty();
    root.innerHTML = `${header()}<main class="album-main"><div class="album-grid ${config.template === 'masonry' ? 'masonry' : ''}">${photos.map(item).join('')}</div></main>${footer()}`;
    bindLightbox();
  }

  // ------------------------------------------------------------------ //
  // Diaporama
  // ------------------------------------------------------------------ //
  function renderSlideshow() {
    if (!photos.length) return empty();
    root.innerHTML = `
      <div class="slideshow-header">
        <p class="eyebrow">Album photo · diaporama</p>
        <h1>${esc(config.title || 'Mon album')}</h1>
        ${config.subtitle ? `<p>${esc(config.subtitle)}</p>` : ''}
        ${config.intro ? `<div class="album-intro">${esc(config.intro)}</div>` : ''}
      </div>
      <main class="slideshow">
        <div class="slide-stage">
          <img id="slide-image" alt="">
          <button class="slide-nav slide-prev" type="button" aria-label="Photo précédente">‹</button>
          <button class="slide-nav slide-next" type="button" aria-label="Photo suivante">›</button>
          <aside class="slide-tech-panel" id="slide-tech-panel" hidden>
            <div class="slide-tech-head">
              <strong>Infos techniques</strong>
              <button id="slide-tech-close" type="button" aria-label="Fermer les infos techniques">×</button>
            </div>
            <dl class="slide-tech-list" id="slide-tech-list"></dl>
          </aside>
          <div class="slide-caption" id="slide-caption"></div>
        </div>
        <div class="slide-toolbar">
          <span id="slide-counter"></span>
          <div class="slide-toolbar-actions">
            <span class="slide-hint">Utilisez les flèches du clavier</span>
            <button class="slide-tech-toggle" id="slide-tech-toggle" type="button" aria-expanded="false" aria-controls="slide-tech-panel">Infos techniques</button>
          </div>
        </div>
        <div class="slide-thumbs" id="slide-thumbs">${photos.map((photo, index) => `<button class="slide-thumb" type="button" data-index="${index}" aria-label="Voir ${esc(photo.title)}"><img src="${esc(photo.src)}" alt="" loading="lazy"></button>`).join('')}</div>
      </main>`;
    const image = root.querySelector('#slide-image');
    const caption = root.querySelector('#slide-caption');
    const counter = root.querySelector('#slide-counter');
    const thumbs = [...root.querySelectorAll('.slide-thumb')];
    const techPanel = root.querySelector('#slide-tech-panel');
    const techToggle = root.querySelector('#slide-tech-toggle');
    const techClose = root.querySelector('#slide-tech-close');
    const techList = root.querySelector('#slide-tech-list');
    let index = 0;

    const setTechOpen = open => {
      techPanel.hidden = !open;
      techToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      techToggle.textContent = open ? 'Masquer les infos' : 'Infos techniques';
    };

    const renderTechnical = photo => {
      techList.innerHTML = technicalMarkup(photo);
    };

    const show = next => {
      index = (next + photos.length) % photos.length;
      const photo = photos[index];
      image.src = photo.src;
      image.alt = photo.title || '';
      caption.innerHTML = `${config.show_captions ? `<strong>${esc(photo.title)}</strong>` : ''}${config.show_metadata && photo.meta ? `<span>${esc(photo.meta)}</span>` : ''}`;
      counter.textContent = `${index + 1} / ${photos.length}`;
      renderTechnical(photo);
      thumbs.forEach((thumb, thumbIndex) => thumb.classList.toggle('active', thumbIndex === index));
    };

    root.querySelector('.slide-prev').addEventListener('click', () => show(index - 1));
    root.querySelector('.slide-next').addEventListener('click', () => show(index + 1));
    techToggle.addEventListener('click', () => setTechOpen(techPanel.hidden));
    techClose.addEventListener('click', () => setTechOpen(false));
    thumbs.forEach(thumb => thumb.addEventListener('click', () => show(Number(thumb.dataset.index))));
    document.addEventListener('keydown', event => {
      if (document.body.dataset.template !== 'slideshow') return;
      if (event.key === 'ArrowLeft') show(index - 1);
      if (event.key === 'ArrowRight') show(index + 1);
      if (event.key === 'Escape' && !techPanel.hidden) setTechOpen(false);
    });
    show(0);
  }

  // ------------------------------------------------------------------ //
  // Livre
  // ------------------------------------------------------------------ //
  function bookPage(photo, index) {
    if (!photo) return `<div class="book-page book-page-empty">—</div>`;
    return `<div class="book-page"><figure class="book-photo" data-index="${index}" tabindex="0" role="button">
      <img src="${esc(photo.src)}" alt="${esc(photo.title)}" loading="lazy">
      <figcaption>${config.show_captions ? `<strong>${esc(photo.title)}</strong>` : ''}${config.show_metadata && photo.meta ? esc(photo.meta) : ''}</figcaption>
    </figure></div>`;
  }

  function renderBook() {
    if (!photos.length) return empty();
    root.innerHTML = `${header()}<main class="book">
      <div class="book-spread" id="book-spread"></div>
      <div class="book-toolbar">
        <span id="book-counter"></span>
        <div class="book-nav">
          <button id="book-prev" type="button" aria-label="Page précédente">‹</button>
          <button id="book-next" type="button" aria-label="Page suivante">›</button>
        </div>
      </div>
    </main>${footer()}`;
    const spread = root.querySelector('#book-spread');
    const counter = root.querySelector('#book-counter');
    let page = 0;
    const pageCount = Math.ceil(photos.length / 2);

    const show = next => {
      page = (next + pageCount) % pageCount;
      const first = page * 2;
      spread.innerHTML = bookPage(photos[first], first) + bookPage(photos[first + 1], first + 1);
      counter.textContent = `Pages ${first + 1}–${Math.min(first + 2, photos.length)} / ${photos.length}`;
      spread.querySelectorAll('.book-photo').forEach(figure => {
        figure.addEventListener('click', () => openLightbox(Number(figure.dataset.index)));
        figure.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openLightbox(Number(figure.dataset.index));
          }
        });
      });
    };

    root.querySelector('#book-prev').addEventListener('click', () => show(page - 1));
    root.querySelector('#book-next').addEventListener('click', () => show(page + 1));
    document.addEventListener('keydown', event => {
      if (document.body.dataset.template !== 'book') return;
      if (event.key === 'ArrowLeft') show(page - 1);
      if (event.key === 'ArrowRight') show(page + 1);
    });
    show(0);
  }

  switch (config.template) {
    case 'slideshow':
      renderSlideshow();
      break;
    case 'book':
      renderBook();
      break;
    case 'masonry':
      renderGrid();
      break;
    default:
      renderGrid();
  }
})();
