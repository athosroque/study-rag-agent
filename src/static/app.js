// Study RAG Agent - Interactive UI Logic & Real-Time SSE Stream

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  loadStats();
  loadMaterias();
  loadKnowledgeItems();
  initFormHandler();
  initSearchHandler();
  initModalHandlers();
});

// =====================================================================
// TABS NAVIGATION
// =====================================================================
function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

      btn.classList.add('active');
      const target = btn.getAttribute('data-tab');
      const panel = document.getElementById(target);
      if (panel) panel.classList.add('active');

      if (target === 'tab-explorer') loadKnowledgeItems();
      if (target === 'tab-curator') loadStats();
      if (target === 'tab-videos') loadVideos();
    });
  });
}

// =====================================================================
// STATS
// =====================================================================
async function loadStats() {
  try {
    const res = await fetch('/api/v1/stats');
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById('stat-total-topics').textContent = data.total_itens || 0;
    document.getElementById('stat-total-fragments').textContent = data.total_fragmentos || 0;
    document.getElementById('stat-total-videos').textContent = data.total_videos || 0;

    const totalCurado = (data.total_itens || 0) + (data.total_fragmentos || 0);
    document.getElementById('stat-total-curated').textContent = totalCurado;
  } catch (err) {
    console.error('Erro ao carregar estatísticas:', err);
  }
}

// =====================================================================
// FORM SUBMIT & SSE REAL-TIME STREAMING
// =====================================================================
function initFormHandler() {
  const form = document.getElementById('curate-form');
  const btnSubmit = document.getElementById('btn-submit');
  const timeline = document.getElementById('console-timeline');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const materia = document.getElementById('input-materia')?.value.trim();
    const tema = document.getElementById('input-tema').value.trim();
    const link = document.getElementById('input-link').value.trim();
    const titulo = document.getElementById('input-titulo').value.trim();
    const transcricao = document.getElementById('input-transcricao').value.trim();

    if (!tema) return;

    btnSubmit.disabled = true;
    btnSubmit.innerHTML = '<span>⚡ Processando no LangGraph...</span>';

    // Clear timeline and show initial step
    timeline.innerHTML = '';
    appendTimelineStep({
      node: 'start',
      title: 'Iniciando Pipeline LangGraph',
      badge: 'INIT',
      content: `Disparando curador de conhecimento para o tema: <strong>${escapeHtml(tema)}</strong>${materia ? ` (Matéria: <em>${escapeHtml(materia)}</em>)` : ''}.`
    });

    const params = new URLSearchParams({ tema });
    if (materia) params.append('materia', materia);
    if (link) params.append('link', link);
    if (titulo) params.append('titulo', titulo);
    if (transcricao) params.append('transcricao', transcricao);

    const eventSource = new EventSource(`/api/v1/process/stream?${params.toString()}`);

    eventSource.addEventListener('start', (event) => {
      const data = JSON.parse(event.data);
      console.log('Stream iniciado:', data);
    });

    eventSource.addEventListener('node_update', (event) => {
      const payload = JSON.parse(event.data);
      renderNodeUpdate(payload);
    });

    eventSource.addEventListener('complete', (event) => {
      const payload = JSON.parse(event.data);
      appendTimelineStep({
        node: 'db_writer',
        title: '🏁 Curadoria Finalizada com Sucesso',
        badge: 'COMPLETO',
        content: payload.message || 'Dados persistidos e reconciliados no PostgreSQL + pgvector.'
      });

      eventSource.close();
      btnSubmit.disabled = false;
      btnSubmit.innerHTML = '<span>🚀 Executar Curadoria RAG</span>';
      loadStats();
    });

    eventSource.addEventListener('error', (event) => {
      console.error('Erro SSE:', event);
      appendTimelineStep({
        node: 'error',
        title: '⚠️ Erro de Execução',
        badge: 'FALHA',
        content: 'Ocorreu uma instabilidade na execução do fluxo.'
      });
      eventSource.close();
      btnSubmit.disabled = false;
      btnSubmit.innerHTML = '<span>🚀 Executar Curadoria RAG</span>';
    });
  });
}

function renderNodeUpdate(payload) {
  const node = payload.node;
  const data = payload.data || {};

  if (node === 'video_search') {
    const video = data.video_encontrado || {};
    appendTimelineStep({
      node: 'video_search',
      title: '1. Busca e Recepção de Aula',
      badge: 'BUSCA',
      content: `Vídeo: <strong>${escapeHtml(video.titulo || 'Aula')}</strong><br><small style="color:var(--text-muted)">${escapeHtml(video.link || '')}</small>`
    });
  } else if (node === 'validate_video') {
    const valido = data.video_valido;
    const msg = data.db_status || (valido ? 'Vídeo inédito no banco.' : 'Vídeo já processado.');
    appendTimelineStep({
      node: 'validate_video',
      title: '2. Validação de Duplicidade no Postgres',
      badge: valido ? 'INÉDITO' : 'DUPLICADO',
      content: msg
    });
  } else if (node === 'extraction_agent') {
    const items = data.extracted_items || [];
    let itemsHtml = `<p>Extraídos <strong>${items.length}</strong> itens pedagógicos (Tier Fast):</p><ul style="padding-left:18px; margin-top:6px;">`;
    items.forEach(it => {
      itemsHtml += `<li><strong>[${escapeHtml(it.categoria)}]</strong> ${escapeHtml(it.topico)}: ${escapeHtml(it.conteudo.slice(0, 100))}...</li>`;
    });
    itemsHtml += '</ul>';
    appendTimelineStep({
      node: 'extraction_agent',
      title: '3. Extração Pedagógica (LLM Tier Fast)',
      badge: `${items.length} ITENS`,
      content: itemsHtml
    });
  } else if (node === 'retrieve_similar_items') {
    const context = data.relevant_existing_items || [];
    let ctxHtml = `<p>pgvector recuperou <strong>${context.length}</strong> itens históricos relevantes:</p>`;
    if (context.length === 0) {
      ctxHtml += '<small style="color:var(--color-novo)">Base histórica vazia para este tema específico.</small>';
    } else {
      ctxHtml += '<ul style="padding-left:18px; margin-top:6px;">';
      context.forEach(ctx => {
        ctxHtml += `<li>ID #${ctx.id} - <strong>${escapeHtml(ctx.topico)}</strong> (${ctx.fragmentos ? ctx.fragmentos.length : 0} fragmentos existentes)</li>`;
      });
      ctxHtml += '</ul>';
    }
    appendTimelineStep({
      node: 'retrieve_similar_items',
      title: '4. Busca Semântica pgvector (RAG Nativo)',
      badge: `${context.length} HISTÓRICOS`,
      content: ctxHtml
    });
  } else if (node === 'reconciliation_agent') {
    const decisions = data.reconciliation_decisions || [];
    let decHtml = '<div class="decisions-container">';
    decisions.forEach(d => {
      const badgeClass = d.acao;
      const refText = d.item_id_referencia ? ` -> Enriquecendo ID #${d.item_id_referencia}` : '';
      decHtml += `
        <div class="decision-item">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <span class="decision-badge ${badgeClass}">${escapeHtml(d.acao)}${refText}</span>
            <small style="color:var(--text-muted)">${escapeHtml(d.item ? d.item.topico : '')}</small>
          </div>
          <div style="font-size:12px; color:var(--text-secondary);">${escapeHtml(d.justificativa)}</div>
          ${d.conteudo_incremental ? `<div style="font-size:12px; color:var(--color-fragmento); margin-top:4px;"><strong>Incremental:</strong> ${escapeHtml(d.conteudo_incremental)}</div>` : ''}
        </div>
      `;
    });
    decHtml += '</div>';

    appendTimelineStep({
      node: 'reconciliation_agent',
      title: '5. Curadoria e Reconciliação (Tier Mid)',
      badge: `${decisions.length} DECISÕES`,
      content: decHtml
    });
  } else if (node === 'db_writer') {
    const stats = data.summary_stats || {};
    appendTimelineStep({
      node: 'db_writer',
      title: '6. Upsert Inteligente no PostgreSQL',
      badge: 'PERSISTIDO',
      content: `
        <div style="display:flex; gap:16px; margin-top:6px;">
          <span style="color:var(--color-novo)">➕ ${stats.criados || 0} Novos</span>
          <span style="color:var(--color-fragmento)">🧩 ${stats.fragmentos || 0} Fragmentos</span>
          <span style="color:var(--color-descartar)">🗑️ ${stats.descartados || 0} Descartados</span>
        </div>
      `
    });
  }
}

function appendTimelineStep({ node, title, badge, content }) {
  const timeline = document.getElementById('console-timeline');
  const stepEl = document.createElement('div');
  stepEl.className = `timeline-step ${node} active`;

  stepEl.innerHTML = `
    <div class="step-header">
      <div class="step-name">${title}</div>
      <span class="step-badge">${badge}</span>
    </div>
    <div class="step-content">${content}</div>
  `;

  timeline.appendChild(stepEl);
  stepEl.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

// =====================================================================
// KNOWLEDGE EXPLORER
// =====================================================================
let currentCategoryFilter = '';
let currentSearchTerm = '';
let currentMateriaFilter = '';

async function loadMaterias() {
  try {
    const res = await fetch('/api/v1/materias');
    if (!res.ok) return;
    const materias = await res.json();

    const selectExplorer = document.getElementById('select-materia-filter');
    const selectSemantic = document.getElementById('semantic-materia-filter');

    const buildOptions = () => {
      let html = '<option value="">📚 Todas as Matérias</option>';
      materias.forEach(m => {
        html += `<option value="${m.id}">${escapeHtml(m.nome)} (${m.qtd_topicos || 0})</option>`;
      });
      return html;
    };

    if (selectExplorer) {
      selectExplorer.innerHTML = buildOptions();
      selectExplorer.value = currentMateriaFilter;
      selectExplorer.onchange = (e) => {
        currentMateriaFilter = e.target.value;
        loadKnowledgeItems();
      };
    }

    if (selectSemantic) {
      selectSemantic.innerHTML = buildOptions();
    }
  } catch (err) {
    console.error('Erro ao carregar matérias:', err);
  }
}

async function loadKnowledgeItems() {
  const container = document.getElementById('knowledge-grid');
  container.innerHTML = '<div style="color:var(--text-muted); padding:20px;">Carregando base de conhecimento...</div>';

  const params = new URLSearchParams();
  if (currentCategoryFilter) params.append('categoria', currentCategoryFilter);
  if (currentSearchTerm) params.append('search', currentSearchTerm);
  if (currentMateriaFilter) params.append('materia_id', currentMateriaFilter);

  try {
    const res = await fetch(`/api/v1/items?${params.toString()}`);
    if (!res.ok) throw new Error('Falha ao listar tópicos');
    const items = await res.json();

    if (items.length === 0) {
      container.innerHTML = `
        <div style="grid-column: 1/-1; text-align:center; padding:60px; color:var(--text-muted);">
          Nenhum tópico encontrado com os filtros selecionados.<br>
          <small>Execute uma curadoria na aba "Curadoria ao Vivo" para popular o banco.</small>
        </div>
      `;
      return;
    }

    container.innerHTML = '';
    items.forEach(item => {
      const card = renderTopicCard(item);
      container.appendChild(card);
    });
  } catch (err) {
    container.innerHTML = `<div style="color:var(--color-descartar); padding:20px;">Erro ao carregar tópicos: ${err.message}</div>`;
  }
}

function renderTopicCard(item) {
  const card = document.createElement('div');
  card.className = 'topic-card';

  // Consolida subitens de filhos e fragmentos, eliminando duplicatas por conteúdo
  const allSubItems = [];
  const seenContents = new Set();

  const addCandidate = (sub) => {
    const rawContent = (sub.conteudo_incremental || sub.conteudo || '').trim();
    if (!rawContent || seenContents.has(rawContent.toLowerCase())) return;
    seenContents.add(rawContent.toLowerCase());
    allSubItems.push({
      categoria: sub.categoria || 'teoria',
      topico: sub.topico || '',
      conteudo: rawContent,
      detalhes_resposta: sub.detalhes_resposta || null,
      justificativa: sub.justificativa || null,
      data: sub.adicionado_em || sub.criado_em || null,
      link_do_video: sub.link_do_video || null
    });
  };

  (item.filhos || []).forEach(addCandidate);
  (item.fragmentos || []).forEach(addCandidate);

  const sacadas = allSubItems.filter(s => s.categoria === 'sacada');
  const pegadinhas = allSubItems.filter(s => s.categoria === 'pegadinha');
  const questoes = allSubItems.filter(s => s.categoria === 'questao');
  const teorias = allSubItems.filter(s => s.categoria === 'teoria');
  const totalSubitens = allSubItems.length;

  let badgesHtml = '';
  if (totalSubitens > 0) {
    badgesHtml = `
      <div class="topic-subcounters">
        ${questoes.length > 0 ? `<span class="counter-badge questao">❓ ${questoes.length} Quest${questoes.length > 1 ? 'ões' : 'ão'}</span>` : ''}
        ${pegadinhas.length > 0 ? `<span class="counter-badge pegadinha">⚠️ ${pegadinhas.length} Pegadinha${pegadinhas.length > 1 ? 's' : ''}</span>` : ''}
        ${sacadas.length > 0 ? `<span class="counter-badge sacada">💡 ${sacadas.length} Macete${sacadas.length > 1 ? 's' : ''}</span>` : ''}
        ${teorias.length > 0 ? `<span class="counter-badge teoria">📖 ${teorias.length} Conceito${teorias.length > 1 ? 's' : ''}</span>` : ''}
      </div>
    `;
  }

  let sectionsHtml = '';
  if (totalSubitens > 0) {
    sectionsHtml = `
      <div class="curated-archive-wrapper">
        <button class="btn-toggle-archive" onclick="toggleArchiveSections(this)">
          <span class="toggle-text">🧩 Explorar Acervo Completo (${totalSubitens} itens vinculados)</span>
          <span class="chevron-icon">▾</span>
        </button>
        <div class="curated-archive-content" style="display:none;">
          ${sacadas.length > 0 ? `
            <div class="curated-category-group sacada">
              <div class="category-group-header">
                <span class="group-icon">💡</span>
                <span class="group-title">Macetes e Sacadas de Memorização</span>
                <span class="group-count">${sacadas.length}</span>
              </div>
              <div class="category-group-items">
                ${sacadas.map(s => `
                  <div class="curated-subitem sacada">
                    ${s.topico ? `<div class="subitem-topic">${escapeHtml(s.topico)}</div>` : ''}
                    <div class="subitem-content">${escapeHtml(s.conteudo)}</div>
                    ${s.justificativa ? `<div class="subitem-note"><em>💡 Dica:</em> ${escapeHtml(s.justificativa)}</div>` : ''}
                    <div class="subitem-footer">
                      <span>${s.data ? new Date(s.data).toLocaleDateString('pt-BR') : ''}</span>
                      ${s.link_do_video ? `<a href="${escapeHtml(s.link_do_video)}" target="_blank" rel="noopener noreferrer" class="link-origin">Vídeo Origem ↗</a>` : ''}
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          ${pegadinhas.length > 0 ? `
            <div class="curated-category-group pegadinha">
              <div class="category-group-header">
                <span class="group-icon">⚠️</span>
                <span class="group-title">Pegadinhas de Prova & Armadilhas</span>
                <span class="group-count">${pegadinhas.length}</span>
              </div>
              <div class="category-group-items">
                ${pegadinhas.map(p => `
                  <div class="curated-subitem pegadinha">
                    ${p.topico ? `<div class="subitem-topic">${escapeHtml(p.topico)}</div>` : ''}
                    <div class="subitem-content">${escapeHtml(p.conteudo)}</div>
                    ${p.detalhes_resposta ? `<div class="subitem-explanation"><strong>Atenção de Banca:</strong> ${escapeHtml(p.detalhes_resposta)}</div>` : ''}
                    <div class="subitem-footer">
                      <span>${p.data ? new Date(p.data).toLocaleDateString('pt-BR') : ''}</span>
                      ${p.link_do_video ? `<a href="${escapeHtml(p.link_do_video)}" target="_blank" rel="noopener noreferrer" class="link-origin">Vídeo Origem ↗</a>` : ''}
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          ${questoes.length > 0 ? `
            <div class="curated-category-group questao">
              <div class="category-group-header">
                <span class="group-icon">❓</span>
                <span class="group-title">Questões de Fixação e Avaliação</span>
                <span class="group-count">${questoes.length}</span>
              </div>
              <div class="category-group-items">
                ${questoes.map((q, idx) => `
                  <div class="curated-subitem questao">
                    <div class="questao-header">
                      <span class="questao-num">Questão #${idx + 1}</span>
                      ${q.topico ? `<span class="questao-sub">${escapeHtml(q.topico)}</span>` : ''}
                    </div>
                    <div class="subitem-content" style="font-weight:500; margin:6px 0;">${escapeHtml(q.conteudo)}</div>
                    ${q.detalhes_resposta ? `
                      <details class="questao-gabarito-details">
                        <summary class="gabarito-toggle">Ver Gabarito e Comentário ▾</summary>
                        <div class="gabarito-box">
                          ${escapeHtml(q.detalhes_resposta)}
                        </div>
                      </details>
                    ` : ''}
                    <div class="subitem-footer">
                      <span>${q.data ? new Date(q.data).toLocaleDateString('pt-BR') : ''}</span>
                      ${q.link_do_video ? `<a href="${escapeHtml(q.link_do_video)}" target="_blank" rel="noopener noreferrer" class="link-origin">Vídeo Origem ↗</a>` : ''}
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          ${teorias.length > 0 ? `
            <div class="curated-category-group teoria">
              <div class="category-group-header">
                <span class="group-icon">📖</span>
                <span class="group-title">Conceitos e Aprofundamento Teórico</span>
                <span class="group-count">${teorias.length}</span>
              </div>
              <div class="category-group-items">
                ${teorias.map(t => `
                  <div class="curated-subitem teoria">
                    ${t.topico ? `<div class="subitem-topic">${escapeHtml(t.topico)}</div>` : ''}
                    <div class="subitem-content">${escapeHtml(t.conteudo)}</div>
                    ${t.detalhes_resposta ? `<div class="subitem-note">${escapeHtml(t.detalhes_resposta)}</div>` : ''}
                    <div class="subitem-footer">
                      <span>${t.data ? new Date(t.data).toLocaleDateString('pt-BR') : ''}</span>
                      ${t.link_do_video ? `<a href="${escapeHtml(t.link_do_video)}" target="_blank" rel="noopener noreferrer" class="link-origin">Vídeo Origem ↗</a>` : ''}
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }

  card.innerHTML = `
    <div class="topic-top">
      <div>
        <div class="topic-title">#${item.id} — ${escapeHtml(item.topico)}</div>
        ${item.materia_nome ? `<div class="materia-badge">📚 ${escapeHtml(item.materia_nome)}</div>` : ''}
      </div>
      <span class="category-tag ${escapeHtml(item.categoria)}">${escapeHtml(item.categoria)}</span>
    </div>
    ${badgesHtml}
    <div class="topic-body" style="margin-top:8px;">
      ${escapeHtml(item.conteudo)}
    </div>
    ${item.detalhes_resposta ? `
      <div class="topic-answer">
        <strong>Explicação / Resposta:</strong><br>${escapeHtml(item.detalhes_resposta)}
      </div>
    ` : ''}
    ${sectionsHtml}
    <div style="font-size:11px; color:var(--text-muted); display:flex; justify-content:space-between; margin-top:12px; padding-top:8px; border-top:1px solid var(--border-subtle);">
      <span>Revisões: ${item.qtd_revisoes || 1}</span>
      <span>${item.data_ultima_revisao ? new Date(item.data_ultima_revisao).toLocaleDateString('pt-BR') : ''}</span>
    </div>
  `;

  return card;
}

window.toggleArchiveSections = function(buttonEl) {
  const content = buttonEl.nextElementSibling;
  if (!content) return;
  const isHidden = content.style.display === 'none';
  content.style.display = isHidden ? 'flex' : 'none';
  const chevron = buttonEl.querySelector('.chevron-icon');
  if (chevron) chevron.textContent = isHidden ? '▴' : '▾';
  const textEl = buttonEl.querySelector('.toggle-text');
  if (textEl) {
    if (isHidden) {
      textEl.textContent = textEl.textContent.replace('Explorar', 'Recolher');
    } else {
      textEl.textContent = textEl.textContent.replace('Recolher', 'Explorar');
    }
  }
};

// Category pills
document.querySelectorAll('.pill-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCategoryFilter = btn.getAttribute('data-cat') || '';
    loadKnowledgeItems();
  });
});

// Search input with debounce
let debounceTimer;
const searchInput = document.getElementById('search-input');
if (searchInput) {
  searchInput.addEventListener('input', (e) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      currentSearchTerm = e.target.value.trim();
      loadKnowledgeItems();
    }, 350);
  });
}

// =====================================================================
// SEMANTIC SEARCH TAB
// =====================================================================
function initSearchHandler() {
  const btn = document.getElementById('btn-semantic-search');
  const input = document.getElementById('semantic-query');
  const resultsContainer = document.getElementById('semantic-results');

  if (!btn || !input) return;

  btn.addEventListener('click', async () => {
    const q = input.value.trim();
    if (!q) return;

    btn.disabled = true;
    btn.textContent = 'Buscando vetores...';
    resultsContainer.innerHTML = '<div style="color:var(--text-muted); padding:20px;">Calculando similaridade vetorial via pgvector...</div>';

    const materiaSelect = document.getElementById('semantic-materia-filter');
    const materiaId = materiaSelect && materiaSelect.value ? parseInt(materiaSelect.value, 10) : null;

    try {
      const searchBody = { query: q, limit: 6 };
      if (materiaId) searchBody.materia_id = materiaId;

      const res = await fetch('/api/v1/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(searchBody)
      });

      if (!res.ok) throw new Error('Falha na busca vetorial');
      const data = await res.json();

      if (data.length === 0) {
        resultsContainer.innerHTML = '<div style="padding:40px; text-align:center; color:var(--text-muted);">Nenhum resultado semântico encontrado.</div>';
        return;
      }

      resultsContainer.innerHTML = '';
      data.forEach(item => {
        const card = renderTopicCard(item);
        const simBadge = document.createElement('div');
        simBadge.style.cssText = 'font-size:12px; color:var(--accent-cyan); font-weight:600; margin-bottom:8px;';
        simBadge.textContent = `🎯 Score de Similaridade: ${((item.similarity || 0) * 100).toFixed(1)}%`;
        card.prepend(simBadge);
        resultsContainer.appendChild(card);
      });
    } catch (err) {
      resultsContainer.innerHTML = `<div style="color:var(--color-descartar); padding:20px;">Erro: ${err.message}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Buscar com pgvector';
    }
  });
}

// =====================================================================
// HISTÓRICO DE VÍDEOS & TRANSCRIÇÕES
// =====================================================================
async function loadVideos() {
  const grid = document.getElementById('videos-grid');
  if (!grid) return;
  grid.innerHTML = '<div style="padding:40px; text-align:center; color:var(--text-muted); grid-column:1/-1;">Carregando histórico de aulas...</div>';

  try {
    const res = await fetch('/api/v1/videos');
    if (!res.ok) throw new Error('Falha ao carregar aulas processadas.');
    const videos = await res.json();

    if (videos.length === 0) {
      grid.innerHTML = '<div style="padding:40px; text-align:center; color:var(--text-muted); grid-column:1/-1;">Nenhuma aula processada ainda. Execute uma curadoria na primeira aba!</div>';
      return;
    }

    grid.innerHTML = '';
    videos.forEach(v => {
      grid.appendChild(renderVideoCard(v));
    });
  } catch (err) {
    grid.innerHTML = `<div style="color:var(--color-descartar); padding:20px; grid-column:1/-1;">Erro: ${escapeHtml(err.message)}</div>`;
  }
}

function renderVideoCard(video) {
  const card = document.createElement('div');
  card.className = 'topic-card';

  const dataFmt = video.data_processamento
    ? new Date(video.data_processamento).toLocaleString('pt-BR')
    : 'Data desconhecida';

  const charsFmt = (video.tamanho_transcricao || 0).toLocaleString('pt-BR');

  card.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px;">
      <span class="topic-cat-badge categoria-teoria">${escapeHtml(video.tema_busca || 'Geral')}</span>
      <small style="color:var(--text-muted); font-size:11px;">${dataFmt}</small>
    </div>
    <h3 class="topic-title" style="margin-bottom:8px;">${escapeHtml(video.titulo || video.tema_busca || 'Aula')}</h3>
    <div style="font-size:12px; color:var(--accent-cyan); margin-bottom:12px; word-break:break-all;">
      <a href="${escapeHtml(video.link_do_video)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent-cyan); text-decoration:none;">🔗 ${escapeHtml(video.link_do_video)}</a>
    </div>
    <div class="topic-body" style="font-size:13px; color:var(--text-secondary); margin-bottom:16px;">
      ${escapeHtml(video.preview_transcricao || 'Sem prévia de transcrição...')}...
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border-subtle); padding-top:12px;">
      <span style="font-size:12px; color:var(--text-muted);">📝 ${charsFmt} caracteres</span>
      <button class="pill-btn btn-view-transcript" style="cursor:pointer;" data-link="${escapeHtml(video.link_do_video)}">
        📜 Ler Transcrição
      </button>
    </div>
  `;

  const btnView = card.querySelector('.btn-view-transcript');
  btnView.addEventListener('click', () => {
    openTranscriptModal(video.link_do_video);
  });

  return card;
}

async function openTranscriptModal(link) {
  const modal = document.getElementById('transcript-modal');
  const titleEl = document.getElementById('modal-video-title');
  const temaEl = document.getElementById('modal-video-tema');
  const charsEl = document.getElementById('modal-video-chars');
  const linkContainer = document.getElementById('modal-video-link-container');
  const textPre = document.getElementById('modal-transcript-text');

  modal.style.display = 'flex';
  titleEl.textContent = 'Carregando transcrição...';
  textPre.textContent = 'Buscando transcrição integral no PostgreSQL...';
  temaEl.textContent = '';
  charsEl.textContent = '';
  linkContainer.innerHTML = '';

  try {
    const res = await fetch(`/api/v1/videos/detail?link=${encodeURIComponent(link)}`);
    if (!res.ok) throw new Error('Não foi possível carregar a transcrição do vídeo.');
    const video = await res.json();

    titleEl.textContent = video.titulo || video.tema_busca || 'Aula';
    temaEl.textContent = video.tema_busca || 'Geral';
    const charCount = (video.transcricao_completa || '').length;
    charsEl.textContent = `${charCount.toLocaleString('pt-BR')} caracteres gravados`;
    linkContainer.innerHTML = `<a href="${escapeHtml(video.link_do_video)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent-cyan); text-decoration:none;">🔗 ${escapeHtml(video.link_do_video)}</a>`;
    textPre.textContent = video.transcricao_completa || 'Transcrição vazia.';
  } catch (err) {
    titleEl.textContent = 'Erro ao carregar';
    textPre.textContent = `Erro: ${err.message}`;
  }
}

function initModalHandlers() {
  const modal = document.getElementById('transcript-modal');
  const btnClose1 = document.getElementById('btn-close-modal');
  const btnClose2 = document.getElementById('btn-close-modal-footer');
  const btnCopy = document.getElementById('btn-copy-transcript');
  const textPre = document.getElementById('modal-transcript-text');

  const closeModal = () => {
    if (modal) modal.style.display = 'none';
  };

  if (btnClose1) btnClose1.addEventListener('click', closeModal);
  if (btnClose2) btnClose2.addEventListener('click', closeModal);
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeModal();
    });
  }

  if (btnCopy) {
    btnCopy.addEventListener('click', async () => {
      const text = textPre.textContent || '';
      try {
        await navigator.clipboard.writeText(text);
        const originalText = btnCopy.textContent;
        btnCopy.textContent = '✅ Copiado!';
        setTimeout(() => { btnCopy.textContent = originalText; }, 2000);
      } catch (e) {
        console.error('Falha ao copiar:', e);
      }
    });
  }
}

function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
