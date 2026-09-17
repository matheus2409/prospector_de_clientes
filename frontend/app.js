(() => {
  const API_KEY_STORAGE = 'prospector_api_key';
  let apiKey = localStorage.getItem(API_KEY_STORAGE) || '';

  let currentJobId = null;
  let currentDedupeMode = false;
  let currentLeads = [];
  let eventSource = null;
  let statusInfo = {};
  let sortState = { field: null, dir: 1 };

  const WA_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3C7 3 3 6.6 3 11c0 2.2 1 4.2 2.7 5.7L5 21l4.4-1.5c.8.2 1.7.3 2.6.3 5 0 9-3.6 9-8s-4-8.5-9-8.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const TRASH_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const EMPTY_ICON = '<svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" stroke-width="1.6"/><path d="M15.5 15.5 21 21" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

  const STATUS_LABELS = { novo: 'Novo', contatado: 'Contatado', interessado: 'Interessado', sem_interesse: 'Sem interesse', fechado: 'Fechado' };
  const STATUS_ORDER = { novo: 0, contatado: 1, interessado: 2, fechado: 3, sem_interesse: 4 };
  const FREQ_LABELS = { 1: 'Diariamente', 7: 'Semanalmente', 30: 'Mensalmente' };
  const freqLabel = (days) => FREQ_LABELS[days] || `A cada ${days} dia${days === 1 ? '' : 's'}`;

  const el = {
    statusChips: document.getElementById('statusChips'),
    searchForm: document.getElementById('searchForm'),
    query: document.getElementById('query'),
    location: document.getElementById('location'),
    maxResults: document.getElementById('maxResults'),
    startBtn: document.getElementById('startBtn'),
    cancelBtn: document.getElementById('cancelBtn'),
    resumeBtn: document.getElementById('resumeBtn'),
    scheduleFrequency: document.getElementById('scheduleFrequency'),
    scheduleBtn: document.getElementById('scheduleBtn'),
    schedulesToggle: document.getElementById('schedulesToggle'),
    schedulesBody: document.getElementById('schedulesBody'),
    schedulesList: document.getElementById('schedulesList'),
    schedulesCount: document.getElementById('schedulesCount'),
    jobSelect: document.getElementById('jobSelect'),
    logDot: document.getElementById('logDot'),
    logTitle: document.getElementById('logTitle'),
    logBody: document.getElementById('logBody'),
    resultsCount: document.getElementById('resultsCount'),
    statsBar: document.getElementById('statsBar'),
    onlyNoWebsite: document.getElementById('onlyNoWebsite'),
    statusFilter: document.getElementById('statusFilter'),
    leadsBody: document.getElementById('leadsBody'),
    exportCsv: document.getElementById('exportCsv'),
    exportXlsx: document.getElementById('exportXlsx'),
    exportSheets: document.getElementById('exportSheets'),
    resendWebhook: document.getElementById('resendWebhook'),
    toast: document.getElementById('toast'),
  };

  function authHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    if (apiKey) headers['X-API-Key'] = apiKey;
    return headers;
  }

  // Wrapper de fetch: injeta a API key (se configurada) e, se o servidor
  // responder 401, pede a chave uma vez e tenta de novo.
  async function api(path, options = {}) {
    const resp = await fetch(path, {
      ...options,
      headers: authHeaders(options.headers),
    });
    if (resp.status === 401) {
      const entered = prompt('Essa instância está protegida por API Key.\nDigite o valor de API_KEY do seu .env:');
      if (entered) {
        apiKey = entered.trim();
        localStorage.setItem(API_KEY_STORAGE, apiKey);
        return api(path, options);
      }
      throw new Error('Não autorizado (API key ausente ou incorreta)');
    }
    return resp;
  }

  async function apiJson(path, options = {}) {
    const resp = await api(path, options);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `Falha na requisição (HTTP ${resp.status})`);
    return data;
  }

  function showToast(message, isError = false) {
    el.toast.textContent = message;
    el.toast.classList.toggle('error', isError);
    el.toast.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => { el.toast.hidden = true; }, 4000);
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }
  function escapeAttr(str) {
    return String(str || '').replace(/"/g, '&quot;');
  }

  // Só deixa a URL virar link clicável se for http(s) — proteção contra um
  // valor de "website" malformado/malicioso (ex: "javascript:...") virar um
  // link executável na tabela.
  function isSafeUrl(url) {
    if (!url) return false;
    try {
      const parsed = new URL(url, window.location.href);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch {
      return false;
    }
  }

  // Telefone brasileiro -> link de "clique pra conversar" do WhatsApp.
  // Heurística por quantidade de dígitos (evita confundir DDD 55 com o
  // código do país 55, que um corte por prefixo erraria):
  //   10 ou 11 dígitos -> número local sem código do país, adiciona 55
  //   12 ou 13 dígitos -> já parece ter o código do país, usa como está
  //   qualquer outro tamanho -> formato não reconhecido, não gera link
  function toWhatsAppLink(phone) {
    if (!phone) return null;
    const digits = String(phone).replace(/\D/g, '');
    if (digits.length === 10 || digits.length === 11) return `https://wa.me/55${digits}`;
    if (digits.length === 12 || digits.length === 13) return `https://wa.me/${digits}`;
    return null;
  }

  function logLine(message, kind = 'info') {
    const line = document.createElement('div');
    line.className = `log-line ${kind}`;
    const time = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    line.textContent = `[${time}] ${message}`;
    el.logBody.appendChild(line);
    el.logBody.scrollTop = el.logBody.scrollHeight;
  }

  function setLogStatus(status) {
    el.logDot.className = 'log-dot';
    const map = {
      pending: ['', 'Aguardando início'],
      running: ['active', 'Varredura em andamento...'],
      completed: ['done', 'Varredura concluída'],
      partial: ['partial', 'Pausada após várias falhas seguidas — dá pra retomar'],
      blocked: ['blocked', 'Bloqueio/captcha detectado — dá pra retomar mais tarde'],
      failed: ['failed', 'Varredura falhou'],
      cancelled: ['failed', 'Varredura cancelada — dá pra retomar'],
    };
    const [cls, title] = map[status] || map.pending;
    if (cls) el.logDot.classList.add(cls);
    el.logTitle.textContent = title;
  }

  function fmtRating(lead) {
    if (lead.rating == null) return '—';
    const stars = `${Number(lead.rating).toFixed(1)} ★`;
    return lead.review_count ? `${stars} (${lead.review_count})` : stars;
  }

  function statusSelect(lead) {
    const current = lead.status || 'novo';
    const opts = Object.entries(STATUS_LABELS)
      .map(([val, label]) => `<option value="${val}" ${current === val ? 'selected' : ''}>${label}</option>`)
      .join('');
    return `<select class="row-status status-${current}" data-action="set-status" data-lead-id="${lead.id ?? ''}" aria-label="Status do lead">${opts}</select>`;
  }

  function leadRow(lead) {
    const tr = document.createElement('tr');
    tr.className = 'lead-row' + (lead.website ? '' : ' no-website');
    const safeWebsite = lead.website && isSafeUrl(lead.website) ? lead.website : null;
    const waLink = toWhatsAppLink(lead.phone);
    const dupBadge = lead.duplicate_count > 1
      ? `<span class="dup-badge" title="Visto em ${lead.duplicate_count} buscas diferentes">×${lead.duplicate_count}</span>`
      : '';
    tr.innerHTML = `
      <td><span class="marker-dot ${lead.website ? 'has-website' : 'no-website'}" title="${lead.website ? '' : 'Sem site — lead quente'}"></span></td>
      <td>${escapeHtml(lead.name) || '—'}${dupBadge}</td>
      <td class="muted">${escapeHtml(lead.category) || '—'}</td>
      <td>${lead.phone ? escapeHtml(lead.phone) : '<span class="muted">—</span>'}${waLink ? `<a class="row-action wa-link" href="${escapeAttr(waLink)}" target="_blank" rel="noopener" title="Chamar no WhatsApp" aria-label="Chamar no WhatsApp">${WA_ICON}</a>` : ''}</td>
      <td>${safeWebsite ? `<a href="${escapeAttr(safeWebsite)}" target="_blank" rel="noopener">visitar</a>` : '<span class="muted">—</span>'}</td>
      <td class="muted">${escapeHtml(lead.address) || '—'}</td>
      <td class="muted">${fmtRating(lead)}</td>
      <td>${statusSelect(lead)}</td>
      <td class="col-actions">${lead.id != null ? `<button type="button" class="row-action delete-lead" data-action="delete-lead" data-lead-id="${lead.id}" title="Excluir lead" aria-label="Excluir lead">${TRASH_ICON}</button>` : ''}</td>
    `;
    return tr;
  }

  function sortLeads(list) {
    if (!sortState.field) return list;
    const { field, dir } = sortState;
    return list.slice().sort((a, b) => {
      let av, bv;
      if (field === 'rating') {
        av = a.rating == null ? -1 : a.rating;
        bv = b.rating == null ? -1 : b.rating;
      } else if (field === 'status') {
        av = STATUS_ORDER[a.status] ?? 0;
        bv = STATUS_ORDER[b.status] ?? 0;
      } else {
        av = (a[field] || '').toString().toLowerCase();
        bv = (b[field] || '').toString().toLowerCase();
      }
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }

  function updateSortIndicators() {
    document.querySelectorAll('#leadsTable th.sortable').forEach(th => {
      const indicator = th.querySelector('.sort-indicator');
      const active = th.dataset.sort === sortState.field;
      th.classList.toggle('sort-active', active);
      if (indicator) indicator.textContent = active ? (sortState.dir === 1 ? ' ▲' : ' ▼') : '';
    });
  }

  function updateStatsBar() {
    if (!el.statsBar) return;
    if (currentLeads.length === 0) {
      el.statsBar.hidden = true;
      el.statsBar.innerHTML = '';
      return;
    }
    const total = currentLeads.length;
    const noWebsite = currentLeads.filter(l => !l.website).length;
    const pct = total ? Math.round((noWebsite / total) * 100) : 0;
    const rated = currentLeads.filter(l => l.rating != null);
    const avgRating = rated.length ? (rated.reduce((s, l) => s + Number(l.rating), 0) / rated.length) : null;
    const contacted = currentLeads.filter(l => l.status && l.status !== 'novo').length;

    el.statsBar.hidden = false;
    el.statsBar.innerHTML = `
      <span class="stat"><strong>${total}</strong> lead${total === 1 ? '' : 's'}${currentDedupeMode ? ' únicos' : ' no total'}</span>
      <span class="stat highlight"><strong>${noWebsite}</strong> sem site (${pct}%)</span>
      ${avgRating != null ? `<span class="stat"><strong>${avgRating.toFixed(1)} ★</strong> média</span>` : ''}
      <span class="stat"><strong>${contacted}</strong> já trabalhado${contacted === 1 ? '' : 's'}</span>
    `;
  }

  function renderLeads() {
    const onlyNoSite = el.onlyNoWebsite.checked;
    const statusFilterVal = el.statusFilter.value;
    let visible = currentLeads;
    if (onlyNoSite) visible = visible.filter(l => !l.website);
    if (statusFilterVal) visible = visible.filter(l => (l.status || 'novo') === statusFilterVal);
    visible = sortLeads(visible);

    el.leadsBody.innerHTML = '';
    if (visible.length === 0) {
      const tr = document.createElement('tr');
      tr.className = 'empty-row';
      const msg = currentLeads.length === 0
        ? 'Nenhuma varredura ainda. Preencha o nicho e a localização acima e clique em "Iniciar Varredura".'
        : 'Nenhum lead corresponde a esse filtro.';
      tr.innerHTML = `<td colspan="9"><div class="empty-state">${EMPTY_ICON}<p>${msg}</p></div></td>`;
      el.leadsBody.appendChild(tr);
    } else {
      visible.forEach(l => el.leadsBody.appendChild(leadRow(l)));
    }

    const filtered = onlyNoSite || statusFilterVal;
    el.resultsCount.textContent = `${visible.length} lead${visible.length === 1 ? '' : 's'}` +
      (filtered && currentLeads.length !== visible.length ? ` de ${currentLeads.length}` : '');
    updateStatsBar();

    const hasLeads = currentLeads.length > 0;
    el.exportCsv.disabled = !hasLeads;
    el.exportXlsx.disabled = !hasLeads;
    el.exportSheets.disabled = !hasLeads || !statusInfo.google_sheets_configured;
    el.resendWebhook.disabled = !hasLeads || !statusInfo.n8n_webhook_configured || !currentJobId;
  }

  function addLeadLive(lead) {
    currentLeads.push(lead);
    renderLeads();
    const rows = el.leadsBody.querySelectorAll('tr.lead-row');
    const last = rows[rows.length - 1];
    if (last) {
      last.classList.add('lead-new');
      setTimeout(() => last.classList.remove('lead-new'), 1700);
    }
  }

  async function loadStatus() {
    const resp = await fetch('/api/status');
    statusInfo = await resp.json();
    const backendLabel = statusInfo.active_backend === 'places_api' ? 'Google Places API' : 'Scraper (Playwright)';
    el.statusChips.innerHTML = `
      <span class="chip backend-chip" title="Motor de busca ativo — configurável via GOOGLE_PLACES_API_KEY no .env">${backendLabel}</span>
      <span class="chip ${statusInfo.n8n_webhook_configured ? 'on' : 'off'}">n8n ${statusInfo.n8n_webhook_configured ? '✓' : '—'}</span>
      <span class="chip ${statusInfo.google_sheets_configured ? 'on' : 'off'}">sheets ${statusInfo.google_sheets_configured ? '✓' : '—'}</span>
      <span class="chip ${statusInfo.api_key_protected ? 'on' : 'off'}">api key ${statusInfo.api_key_protected ? '✓' : '—'}</span>
    `;
    // Mantém o campo "máx. resultados" alinhado ao teto real configurado no
    // .env (MAX_RESULTS_HARD_CAP) — sem isso, um teto customizado acima de
    // 200 ficava travado pela validação nativa do <input max="200"> do HTML.
    if (statusInfo.max_results_hard_cap) {
      el.maxResults.max = String(statusInfo.max_results_hard_cap);
      el.maxResults.title = `Máximo permitido nesta instância: ${statusInfo.max_results_hard_cap}`;
    }
  }

  async function loadJobHistory(selectId) {
    const resp = await api('/api/jobs');
    const jobs = await resp.json();
    const dedupeOption = jobs.length > 0
      ? '<option value="__all__">📊 Todas as buscas (sem duplicatas)</option>'
      : '';
    el.jobSelect.innerHTML = '<option value="">— nenhuma selecionada —</option>' + dedupeOption +
      jobs.map(j => {
        const date = new Date(j.created_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
        const info = j.status === 'completed' ? `${j.total_found} leads` : j.status;
        return `<option value="${j.id}">${escapeHtml(j.query)} · ${escapeHtml(j.location)} — ${date} (${info})</option>`;
      }).join('');
    if (selectId) el.jobSelect.value = selectId;
    return jobs;
  }

  async function updateResumeVisibility(jobId) {
    if (!jobId) { el.resumeBtn.hidden = true; return; }
    try {
      const resp = await api(`/api/jobs/${jobId}/resumable`);
      const data = await resp.json();
      el.resumeBtn.hidden = !data.resumable;
      el.resumeBtn.textContent = data.resumable ? `Retomar (${data.pending_count} pendente${data.pending_count === 1 ? '' : 's'})` : 'Retomar';
    } catch {
      el.resumeBtn.hidden = true;
    }
  }

  function resetForNewJob() {
    currentLeads = [];
    currentDedupeMode = false;
    el.logBody.innerHTML = '';
    sortState = { field: null, dir: 1 };
    updateSortIndicators();
    renderLeads();
  }

  function startStream(jobId) {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(`/api/jobs/${jobId}/stream`);

    eventSource.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'log') {
        const kind = /erro|falhou|cancelad/i.test(msg.message) ? 'error' : 'info';
        logLine(msg.message, kind);
      } else if (msg.type === 'lead') {
        addLeadLive(msg.lead);
      } else if (msg.type === 'status') {
        setLogStatus(msg.status);
      } else if (msg.type === 'done') {
        eventSource.close();
        eventSource = null;
        el.startBtn.disabled = false;
        el.cancelBtn.hidden = true;
        loadJobHistory(jobId);
        updateResumeVisibility(jobId);
      }
    };

    eventSource.onerror = () => {
      logLine('Conexão de log interrompida (verifique se o servidor ainda está rodando).', 'error');
    };
  }

  // Carrega um job (do histórico, reconectando a uma varredura ainda em
  // andamento, ou a visão agregada "__all__" sem duplicatas) e ajusta a UI
  // de acordo.
  async function selectJob(jobId, { silent = false } = {}) {
    if (eventSource) { eventSource.close(); eventSource = null; }
    el.logBody.innerHTML = '';
    sortState = { field: null, dir: 1 };
    updateSortIndicators();

    if (jobId === '__all__') {
      currentJobId = null;
      currentDedupeMode = true;
      const resp = await api('/api/leads?dedupe=true');
      currentLeads = await resp.json();
      renderLeads();
      setLogStatus('completed');
      logLine(`Visualizando todas as buscas sem duplicatas — ${currentLeads.length} estabelecimento(s) único(s).`);
      el.startBtn.disabled = false;
      el.cancelBtn.hidden = true;
      el.resumeBtn.hidden = true;
      return null;
    }

    currentDedupeMode = false;
    const [leadsResp, jobResp] = await Promise.all([
      api(`/api/leads?job_id=${jobId}`),
      api(`/api/jobs/${jobId}`),
    ]);
    currentLeads = await leadsResp.json();
    currentJobId = jobId;
    renderLeads();
    const job = await jobResp.json();
    setLogStatus(job.status);
    logLine(silent
      ? `Varredura em andamento reconectada: "${job.query}" em "${job.location}".`
      : `Varredura carregada: "${job.query}" em "${job.location}" — ${job.total_found} leads.`);

    if (job.status === 'running') {
      el.startBtn.disabled = true;
      el.cancelBtn.hidden = false;
      el.resumeBtn.hidden = true;
      startStream(jobId);
    } else {
      el.startBtn.disabled = false;
      el.cancelBtn.hidden = true;
      await updateResumeVisibility(jobId);
    }
    return job;
  }

  function parseFilenameFromDisposition(value) {
    if (!value) return null;
    const match = /filename\*?=(?:UTF-8''|")?([^";]+)"?/i.exec(value);
    return match ? decodeURIComponent(match[1]) : null;
  }

  async function downloadExport(path, fallbackFilename) {
    const params = new URLSearchParams();
    if (currentJobId) params.set('job_id', currentJobId);
    if (el.onlyNoWebsite.checked) params.set('only_no_website', 'true');
    if (currentDedupeMode) params.set('dedupe', 'true');
    const resp = await api(`${path}?${params.toString()}`);
    if (!resp.ok) throw new Error('Falha ao gerar o arquivo de exportação');
    const filename = parseFilenameFromDisposition(resp.headers.get('Content-Disposition')) || fallbackFilename;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // ---------- Agendamentos ----------
  async function loadSchedules() {
    const schedules = await apiJson('/api/schedules');
    renderSchedules(schedules);
    return schedules;
  }

  function renderSchedules(schedules) {
    el.schedulesCount.hidden = schedules.length === 0;
    el.schedulesCount.textContent = String(schedules.length);
    if (schedules.length === 0) {
      el.schedulesList.innerHTML = '<p class="muted">Nenhuma busca agendada ainda. Preencha o nicho/localização acima, escolha uma frequência e clique em "+ Agendar".</p>';
      return;
    }
    el.schedulesList.innerHTML = schedules.map(s => {
      const next = new Date(s.next_run_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      return `
        <div class="schedule-row-item ${s.active ? '' : 'inactive'}">
          <div class="schedule-info">
            <strong>${escapeHtml(s.query)}</strong> em ${escapeHtml(s.location)}
            <span class="muted">· ${freqLabel(s.frequency_days)} · ${s.active ? `próxima: ${next}` : 'pausada'}</span>
          </div>
          <div class="schedule-actions">
            <button type="button" class="btn-ghost btn-tiny" data-action="toggle-schedule" data-id="${s.id}" data-active="${s.active}">${s.active ? 'Pausar' : 'Retomar'}</button>
            <button type="button" class="btn-ghost btn-tiny danger" data-action="delete-schedule" data-id="${s.id}">Remover</button>
          </div>
        </div>`;
    }).join('');
  }

  el.searchForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const query = el.query.value.trim();
    const location = el.location.value.trim();
    const maxResults = parseInt(el.maxResults.value, 10) || 60;

    el.startBtn.disabled = true;
    el.cancelBtn.hidden = false;
    el.resumeBtn.hidden = true;
    resetForNewJob();
    setLogStatus('pending');
    logLine(`Iniciando varredura: "${query}" em "${location}" (até ${maxResults} resultados)`);

    try {
      const resp = await api('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, location, max_results: maxResults }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Falha ao criar a varredura');

      currentJobId = data.job_id;
      setLogStatus('running');
      startStream(currentJobId);
    } catch (err) {
      logLine(`Erro: ${err.message}`, 'error');
      setLogStatus('failed');
      el.startBtn.disabled = false;
      el.cancelBtn.hidden = true;
    }
  });

  el.cancelBtn.addEventListener('click', async () => {
    if (!currentJobId) return;
    try {
      await api(`/api/jobs/${currentJobId}/cancel`, { method: 'POST' });
      logLine('Cancelamento solicitado...');
    } catch (err) {
      showToast(err.message, true);
    }
  });

  el.jobSelect.addEventListener('change', async () => {
    const jobId = el.jobSelect.value;
    if (!jobId) return;
    try {
      await selectJob(jobId);
    } catch (err) {
      showToast(err.message, true);
    }
  });

  el.resumeBtn.addEventListener('click', async () => {
    if (!currentJobId) return;
    el.resumeBtn.disabled = true;
    try {
      await api(`/api/jobs/${currentJobId}/resume`, { method: 'POST' });
      el.startBtn.disabled = true;
      el.cancelBtn.hidden = false;
      el.resumeBtn.hidden = true;
      setLogStatus('running');
      logLine('Retomando varredura a partir dos itens pendentes...');
      startStream(currentJobId);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      el.resumeBtn.disabled = false;
    }
  });

  el.onlyNoWebsite.addEventListener('change', renderLeads);
  el.statusFilter.addEventListener('change', renderLeads);

  // Ordenação por coluna (Nome, Categoria, Avaliação, Status).
  document.querySelectorAll('#leadsTable th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const field = th.dataset.sort;
      if (sortState.field === field) {
        sortState.dir *= -1;
      } else {
        sortState.field = field;
        sortState.dir = field === 'rating' ? -1 : 1; // avaliação: maior nota primeiro
      }
      updateSortIndicators();
      renderLeads();
    });
  });

  // Excluir lead e mudar status (delegados no tbody, já que as linhas são
  // recriadas a cada render).
  el.leadsBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action="delete-lead"]');
    if (!btn) return;
    const leadId = btn.dataset.leadId;
    if (!leadId || !confirm('Excluir este lead da lista?')) return;
    btn.disabled = true;
    try {
      const resp = await api(`/api/leads/${leadId}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error('Falha ao excluir o lead');
      currentLeads = currentLeads.filter(l => String(l.id) !== String(leadId));
      renderLeads();
    } catch (err) {
      showToast(err.message, true);
      btn.disabled = false;
    }
  });

  el.leadsBody.addEventListener('change', async (e) => {
    const select = e.target.closest('[data-action="set-status"]');
    if (!select) return;
    const leadId = select.dataset.leadId;
    if (!leadId) return;
    const newStatus = select.value;
    select.disabled = true;
    try {
      await apiJson(`/api/leads/${leadId}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      const lead = currentLeads.find(l => String(l.id) === String(leadId));
      if (lead) lead.status = newStatus;
      renderLeads();
    } catch (err) {
      showToast(err.message, true);
      select.disabled = false;
    }
  });

  // ---------- Agendamentos: criar, pausar/retomar, remover ----------
  el.schedulesToggle.addEventListener('click', async () => {
    const willOpen = el.schedulesBody.hidden;
    el.schedulesBody.hidden = !willOpen;
    el.schedulesToggle.classList.toggle('open', willOpen);
    if (willOpen) {
      try { await loadSchedules(); } catch (err) { showToast(err.message, true); }
    }
  });

  el.scheduleBtn.addEventListener('click', async () => {
    const freq = el.scheduleFrequency.value;
    if (!freq) { showToast('Escolha uma frequência de repetição pra agendar.', true); return; }
    const query = el.query.value.trim();
    const location = el.location.value.trim();
    if (!query || !location) { showToast('Preencha nicho e localização antes de agendar.', true); return; }

    el.scheduleBtn.disabled = true;
    try {
      await apiJson('/api/schedules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query, location,
          max_results: parseInt(el.maxResults.value, 10) || 60,
          frequency_days: parseInt(freq, 10),
        }),
      });
      showToast(`Agendado: "${query}" em "${location}" — ${freqLabel(parseInt(freq, 10)).toLowerCase()}.`);
      el.schedulesBody.hidden = false;
      el.schedulesToggle.classList.add('open');
      await loadSchedules();
    } catch (err) {
      showToast(err.message, true);
    } finally {
      el.scheduleBtn.disabled = false;
    }
  });

  el.schedulesList.addEventListener('click', async (e) => {
    const toggleBtn = e.target.closest('[data-action="toggle-schedule"]');
    const deleteBtn = e.target.closest('[data-action="delete-schedule"]');
    if (toggleBtn) {
      const willBeActive = toggleBtn.dataset.active !== 'true';
      toggleBtn.disabled = true;
      try {
        await apiJson(`/api/schedules/${toggleBtn.dataset.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ active: willBeActive }),
        });
        await loadSchedules();
      } catch (err) {
        showToast(err.message, true);
        toggleBtn.disabled = false;
      }
    } else if (deleteBtn) {
      if (!confirm('Remover esse agendamento? Ele para de repetir, mas as buscas já feitas continuam salvas.')) return;
      deleteBtn.disabled = true;
      try {
        await apiJson(`/api/schedules/${deleteBtn.dataset.id}`, { method: 'DELETE' });
        await loadSchedules();
      } catch (err) {
        showToast(err.message, true);
        deleteBtn.disabled = false;
      }
    }
  });

  el.exportCsv.addEventListener('click', async () => {
    try { await downloadExport('/api/export/csv', 'leads.csv'); }
    catch (err) { showToast(err.message, true); }
  });

  el.exportXlsx.addEventListener('click', async () => {
    try { await downloadExport('/api/export/xlsx', 'leads.xlsx'); }
    catch (err) { showToast(err.message, true); }
  });

  el.exportSheets.addEventListener('click', async () => {
    try {
      const data = await apiJson('/api/export/sheets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: currentJobId,
          only_no_website: el.onlyNoWebsite.checked,
          dedupe: currentDedupeMode,
        }),
      });
      showToast('Enviado pro Google Sheets!');
      window.open(data.url, '_blank', 'noopener');
    } catch (err) {
      showToast(err.message, true);
    }
  });

  el.resendWebhook.addEventListener('click', async () => {
    try {
      await apiJson(`/api/webhook/resend/${currentJobId}`, { method: 'POST' });
      showToast('Webhook reenviado pro n8n!');
    } catch (err) {
      showToast(err.message, true);
    }
  });

  (async function init() {
    try {
      await loadStatus();
      const jobs = await loadJobHistory();
      loadSchedules().catch(() => {});
      // Se alguma varredura ainda estiver "running" (ex: a página foi
      // recarregada no meio de uma busca), reconecta ao vivo nela em vez de
      // deixar a UI parada sem mostrar nada acontecendo.
      const active = jobs.find(j => j.status === 'running');
      if (active) {
        el.jobSelect.value = active.id;
        await selectJob(active.id, { silent: true });
      }
    } catch (err) {
      logLine(`Erro ao carregar estado inicial: ${err.message}`, 'error');
    }
    renderLeads();
  })();
})();
