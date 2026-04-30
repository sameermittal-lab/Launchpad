// Listings + evaluation pipeline

async function submitNewListing(){
  const input = document.getElementById('addListingInput').value.trim();
  if (!input) {
    alert('Paste a URL or job description first');
    return;
  }
  const btn = document.getElementById('addListingBtn');
  const statusEl = document.getElementById('addListingStatus');
  btn.disabled = true;
  btn.innerHTML = '\u{23F3} Running pipeline...';
  statusEl.innerHTML = '\u{1F4A1} Fetching and evaluating with AI. This takes 15-30s...';
  statusEl.style.color = 'var(--primary)';

  // Detect URL vs raw text
  const isUrl = /^https?:\/\//i.test(input);
  const payload = isUrl ? { url: input } : { jd_text: input };

  const tryCreate = async (forceAdd) => {
    try {
      const listing = await window.api.listings.create(payload, { forceAdd });
      closeModal();
      btn.disabled = false;
      btn.innerHTML = '\u{2728} Run Pipeline';
      document.getElementById('addListingInput').value = '';
      showPage('pipeline');
      setTimeout(() => openDetail(listing.id), 300);
    } catch (err) {
      if (err.status === 409 && !forceAdd) {
        // Try to parse the structured reason from the detail
        let detail = err.message;
        let reason = '';
        try {
          const m = err.message.match(/^\d+:\s*(.+)/);
          const body = m ? m[1] : err.message;
          const parsed = JSON.parse(body);
          if (parsed && typeof parsed === 'object') {
            reason = parsed.message || parsed.reason || '';
          }
        } catch {}
        const ok = confirm(
          `This listing's title doesn't pass your filter.\n\n` +
          `${reason || detail}\n\n` +
          `Add it anyway? (Your filter rules stay intact — this is a one-time override.)`
        );
        btn.disabled = false;
        btn.innerHTML = '\u{2728} Run Pipeline';
        if (ok) {
          btn.disabled = true;
          btn.innerHTML = '\u{23F3} Adding (override)...';
          statusEl.innerHTML = '\u{1F4A1} Adding with filter override...';
          await tryCreate(true);
        } else {
          statusEl.innerHTML = '\u{26A0} Not added — title blocked by your negative-keyword filter. Edit keywords in Settings if this was wrong.';
          statusEl.style.color = 'var(--orange, #b45309)';
        }
        return;
      }
      btn.disabled = false;
      btn.innerHTML = '\u{2728} Run Pipeline';
      statusEl.innerHTML = '\u{26A0} ' + err.message;
      statusEl.style.color = 'var(--red)';
    }
  };

  await tryCreate(false);
}

// Replace the mock openDetail with a version that fetches real data
async function openDetail(id) {
  const panel = document.getElementById('detailPanel');
  const overlay = document.getElementById('detailOverlay');
  overlay.classList.add('open');
  panel.classList.add('open');

  // Show loading state
  document.getElementById('dp-company').textContent = 'Loading...';
  document.getElementById('dp-title').textContent = '';
  document.getElementById('dp-location').textContent = '';

  try {
    const listing = await window.api.listings.get(id);
    renderDetailPanel(listing);
  } catch (err) {
    document.getElementById('dp-company').textContent = 'Error';
    document.getElementById('dp-title').textContent = err.message;
  }
}

function renderDetailPanel(l) {
  window._currentListing = l;
  document.getElementById('dp-company').textContent = l.company || '(unknown)';
  document.getElementById('dp-title').textContent = l.role_title || '';
  const loc = [l.location, l.job_type].filter(Boolean).join(' \u00b7 ');
  document.getElementById('dp-location').textContent = loc || '';
  document.getElementById('dp-score').textContent = l.score != null ? l.score.toFixed(1) : '-';
  document.getElementById('dp-grade').textContent = l.grade ? 'Grade ' + l.grade : 'Unevaluated';

  // Sub-scores grid — 8 dimensions with tooltips for rationale
  const ss = l.sub_scores || {};
  const rationales = l.dimension_rationales || {};
  const scoreGrid = document.querySelector('#detailPanel .score-grid');
  if (scoreGrid) {
    scoreGrid.innerHTML = dimensionGridHTML(ss, rationales);
    scoreGrid.style.gridTemplateColumns = 'repeat(4, 1fr)';
  }

  // AI summary + qualitative breakdown
  const summarySection = document.querySelectorAll('#detailPanel .dp-section')[1];
  if (summarySection) {
    summarySection.innerHTML = renderSummarySection(l);
  }

  // Tailored Assets section
  const assetsSection = document.querySelectorAll('#detailPanel .dp-section')[2];
  if (assetsSection) {
    assetsSection.innerHTML = renderAssetsSection(l);
  }

  // Interview Prep section — replace mock HTML with real profile STAR stories
  const prepSection = document.querySelectorAll('#detailPanel .dp-section')[3];
  if (prepSection) {
    prepSection.innerHTML = renderInterviewPrepSection(l);
    loadInterviewPrepForPanel().catch(() => {});
  }

  // Company Research section — replace mock Anthropic with real data
  const aboutSection = document.querySelectorAll('#detailPanel .dp-section')[4];
  if (aboutSection) {
    aboutSection.innerHTML = renderCompanyResearchSection(l);
    loadCompanyResearchForPanel(l).catch(() => {});
  }

  // Action row
  const actions = document.querySelector('#detailPanel .dp-actions');
  if (actions) {
    actions.innerHTML = renderDetailActions(l);
  }
}

// All 8 evaluation dimensions, in display order
const EVAL_DIMS = [
  { key: 'role_match', label: 'Role Match' },
  { key: 'seniority_match', label: 'Seniority' },
  { key: 'skills', label: 'Skills' },
  { key: 'comp', label: 'Comp' },
  { key: 'growth', label: 'Growth' },
  { key: 's_curve', label: 'S-Curve' },
  { key: 'culture', label: 'Culture' },
  { key: 'location', label: 'Location' },
];

function dimensionGridHTML(subScores, rationales) {
  return EVAL_DIMS.map(d => {
    const val = subScores[d.key];
    const rationale = rationales[d.key] || '';
    const color = val == null ? 'var(--text3)' : val >= 4 ? 'var(--green)' : val >= 3.5 ? '#a16207' : 'var(--red)';
    const tooltip = rationale ? `title="${escapeHtml(rationale)}"` : '';
    return `<div class="score-item" ${tooltip} style="cursor:${rationale ? 'help' : 'default'}">
      <div class="score-item-label">${d.label}</div>
      <div class="score-item-value" style="color:${color}">${val != null ? val.toFixed(1) : '-'}</div>
    </div>`;
  }).join('');
}

function renderSummarySection(l) {
  const hasSummary = !!l.ai_summary;
  const legacy = l.evaluation_version == null || l.evaluation_version < 2;
  const legacyBanner = legacy && l.score != null
    ? `<div style="background:var(--bg2);border-radius:var(--radius-sm);padding:10px 12px;margin-bottom:10px;font-size:11px;color:var(--text2);font-weight:500">
         \u{2139}\uFE0F This listing was scored with the older v1 evaluator (no seniority or s-curve dimensions). <button class="btn btn-ghost btn-sm" style="margin-left:6px" onclick="evaluateCurrentListing(${l.id}, true)">Re-evaluate</button>
       </div>`
    : '';
  const takeIf = Array.isArray(l.take_it_if) && l.take_it_if.length
    ? `<div style="margin-top:10px">
         <div style="font-size:11px;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">\u{2713} Take it if</div>
         <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.6;color:var(--text);font-weight:500">
           ${l.take_it_if.map(x => `<li>${escapeHtml(x)}</li>`).join('')}
         </ul>
       </div>` : '';
  const comps = Array.isArray(l.compromises) && l.compromises.length
    ? `<div style="margin-top:10px">
         <div style="font-size:11px;font-weight:700;color:#b45309;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">\u{26A0} Compromises</div>
         <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.6;color:var(--text);font-weight:500">
           ${l.compromises.map(x => `<li>${escapeHtml(x)}</li>`).join('')}
         </ul>
       </div>` : '';
  const blockers = Array.isArray(l.blockers) && l.blockers.length
    ? `<div style="margin-top:10px">
         <div style="font-size:11px;font-weight:700;color:var(--red);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">\u{26D4} Blockers</div>
         <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.6;color:var(--text);font-weight:500">
           ${l.blockers.map(x => `<li>${escapeHtml(x)}</li>`).join('')}
         </ul>
       </div>` : '';
  const citations = Array.isArray(l.citations) && l.citations.length
    ? `<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
         <div style="font-size:10px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Web sources (${l.citations.length})</div>
         <div style="font-size:11px;line-height:1.6">
           ${l.citations.slice(0, 5).map((c, i) => c.url ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener" style="color:var(--primary);font-weight:500;text-decoration:none">[${i+1}] ${escapeHtml(c.title || c.url)}</a>` : '').filter(Boolean).join(' \u00b7 ')}
         </div>
       </div>` : '';
  return `
    <div class="ai-summary">
      <div class="ai-summary-label">\u{2728} AI Summary</div>
      ${legacyBanner}
      <div class="ai-summary-text">${hasSummary ? escapeHtml(l.ai_summary) : 'No summary available yet. Click Evaluate to run the AI.'}</div>
      ${takeIf}
      ${comps}
      ${blockers}
      ${citations}
    </div>`;
}

function renderInterviewPrepSection(l) {
  return `
    <div class="dp-section-title">\u{1F3A4} Interview Prep</div>
    <div id="dpInterviewPrepBody" style="font-size:13px;color:var(--text2);line-height:1.7;font-weight:500">
      <div class="empty-state" style="padding:16px 10px">
        <div style="font-size:12px;color:var(--text3);font-weight:500">\u{23F3} Loading your STAR stories...</div>
      </div>
    </div>
    <div style="margin-top:10px;padding:10px 12px;background:var(--primary-soft);border-radius:var(--radius-sm);font-size:11px;color:var(--primary);font-weight:600;line-height:1.5">
      \u{1F4A1} These are your <strong>general</strong> STAR stories from your resume. Per-listing matching is on the roadmap \u2014 for now, eyeball the JD and pick the 2\u20133 most relevant ones. <a onclick="showPage('interview')" style="cursor:pointer;text-decoration:underline;font-weight:700">Open full interview prep \u2192</a>
    </div>`;
}

async function loadInterviewPrepForPanel() {
  const body = document.getElementById('dpInterviewPrepBody');
  if (!body) return;
  try {
    const data = await window.api.interview.getStories();
    const stories = (data && (data.stories || data)) || [];
    if (!Array.isArray(stories) || !stories.length) {
      body.innerHTML = `<div style="padding:10px;background:var(--bg2);border-radius:var(--radius-sm);color:var(--text3);font-size:12px;font-weight:500">No STAR stories yet. <a onclick="showPage('interview')" style="cursor:pointer;color:var(--primary);font-weight:700;text-decoration:underline">Generate them</a> from your resume.</div>`;
      return;
    }
    body.innerHTML = stories.slice(0, 3).map((s, i) => {
      const title = s.title || s.name || s.headline || 'Untitled';
      const preview = s.summary || s.situation || s.result || '';
      return `<div style="margin-bottom:10px;padding:10px;background:var(--bg2);border-radius:var(--radius-sm)">
        <strong style="color:var(--text)">\u{1F31F} STAR Story ${i+1}:</strong> ${escapeHtml(title)}${preview ? ' \u2014 ' + escapeHtml(preview.slice(0, 160)) : ''}
      </div>`;
    }).join('');
  } catch (err) {
    body.innerHTML = `<div style="padding:10px;background:var(--bg2);border-radius:var(--radius-sm);color:var(--text3);font-size:12px;font-weight:500">Couldn't load stories: ${escapeHtml(err.message)}</div>`;
  }
}

function renderCompanyResearchSection(l) {
  const company = l.company || '';
  const safeCompany = escapeHtml(company);
  const safeCompanyAttr = safeCompany.replace(/"/g, '&quot;');
  const safeHint = escapeHtml(l.url || '').replace(/"/g, '&quot;');
  return `
    <div class="dp-section-title" style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
      <span>\u{1F3E2} About ${safeCompany}</span>
      <span id="dpTrackCompanyHost"></span>
    </div>
    <div id="dpCompanyResearchBody" style="font-size:13px;color:var(--text2);line-height:1.7;font-weight:500">
      <div class="empty-state" style="padding:16px 10px">
        <div style="font-size:12px;color:var(--text3);font-weight:500">\u{23F3} Loading cached research...</div>
      </div>
    </div>`;
}

async function loadTrackCompanyButton(l) {
  const host = document.getElementById('dpTrackCompanyHost');
  if (!host || !l.company) return;
  host.innerHTML = '<span style="font-size:11px;color:var(--text3);font-weight:500">\u{23F3}</span>';
  try {
    const all = await window.api.scanner.listCompanies();
    const match = (all || []).find(c => (c.name || '').toLowerCase() === l.company.toLowerCase());
    if (match) {
      const aiOn = !!match.ai_monitor_enabled;
      const label = aiOn ? '\u{2705} Tracked \u00b7 \u{2728} AI On' : '\u{2705} Tracked';
      host.innerHTML = `<button class="btn btn-ghost btn-sm" disabled title="This company is already being tracked" style="font-size:11px">${label}</button>`;
    } else {
      host.innerHTML = trackCompanyButtonHTML({
        id: `dp-${l.id}`,
        name: l.company,
        hintUrl: l.url || '',
        alreadyTracked: false,
      });
    }
  } catch (err) {
    // Silent — scanner might not be accessible; don't block detail panel
    host.innerHTML = '';
  }
}

async function loadCompanyResearchForPanel(l) {
  const body = document.getElementById('dpCompanyResearchBody');
  if (!body || !l.company) return;

  // Populate the Track-this-company button in the section header (fire & forget).
  loadTrackCompanyButton(l).catch(() => {});

  try {
    const all = await window.api.companies.list();
    const match = (all || []).find(c => (c.name || '').toLowerCase() === l.company.toLowerCase());
    if (!match) {
      body.innerHTML = `
        <div style="padding:14px;background:var(--bg2);border-radius:var(--radius-sm);text-align:center">
          <div style="font-size:12px;color:var(--text3);font-weight:500;margin-bottom:8px">No research on ${escapeHtml(l.company)} yet.</div>
          <button class="btn btn-primary btn-sm" onclick="researchCompanyFromPanel('${encodeURIComponent(l.company)}', '${encodeURIComponent(l.url || '')}')">\u{1F50D} Research with web search</button>
        </div>`;
      return;
    }
    const content = match.research_markdown || match.summary || match.description || '(empty research)';
    body.innerHTML = `
      <div style="padding:10px 12px;background:var(--bg2);border-radius:var(--radius-sm);white-space:pre-wrap;font-size:13px;line-height:1.55;color:var(--text);max-height:260px;overflow-y:auto">${escapeHtml(content)}</div>
      ${match.updated_at ? `<div style="font-size:10px;color:var(--text3);font-weight:500;margin-top:6px;text-align:right">Last updated: ${new Date(match.updated_at).toLocaleDateString()} \u00b7 <a onclick="refreshCompanyFromPanel(${match.id})" style="cursor:pointer;color:var(--primary);font-weight:600;text-decoration:underline">Refresh</a></div>` : ''}`;
  } catch (err) {
    body.innerHTML = `<div style="padding:10px;background:var(--bg2);border-radius:var(--radius-sm);color:var(--text3);font-size:12px;font-weight:500">Couldn't load research: ${escapeHtml(err.message)}</div>`;
  }
}

async function researchCompanyFromPanel(nameEnc, urlEnc) {
  const name = decodeURIComponent(nameEnc);
  const url = decodeURIComponent(urlEnc);
  const body = document.getElementById('dpCompanyResearchBody');
  if (body) body.innerHTML = `<div style="padding:14px;text-align:center;color:var(--text2);font-size:12px;font-weight:500">\u{23F3} Researching ${escapeHtml(name)} with web search (~10-20s)...</div>`;
  try {
    await window.api.companies.research(name, url || null, false);
    if (window._currentListing) {
      const fresh = await window.api.listings.get(window._currentListing.id);
      renderDetailPanel(fresh);
    }
  } catch (err) {
    if (body) body.innerHTML = `<div style="padding:10px;background:var(--bg2);border-radius:var(--radius-sm);color:var(--red);font-size:12px;font-weight:600">Research failed: ${escapeHtml(err.message)}</div>`;
  }
}

async function refreshCompanyFromPanel(companyId) {
  const body = document.getElementById('dpCompanyResearchBody');
  if (body) body.innerHTML = `<div style="padding:14px;text-align:center;color:var(--text2);font-size:12px;font-weight:500">\u{23F3} Refreshing...</div>`;
  try {
    await window.api.companies.refresh(companyId);
    if (window._currentListing) loadCompanyResearchForPanel(window._currentListing);
  } catch (err) {
    if (body) body.innerHTML = `<div style="padding:10px;background:var(--bg2);border-radius:var(--radius-sm);color:var(--red);font-size:12px;font-weight:600">Refresh failed: ${escapeHtml(err.message)}</div>`;
  }
}

function renderDetailActions(l) {
  const isUnevaluated = l.score == null || l.status === 'new';
  const openUrlBtn = l.url
    ? `<a class="btn btn-ghost" href="${l.url}" target="_blank" rel="noopener" title="Open original JD">\u{1F517}</a>`
    : '';
  const running = isEvaluating(l.id) || l.evaluation_in_progress;
  const reevalBtn = running
    ? `<button class="btn btn-ghost" disabled title="Evaluation in progress">\u{23F3}</button>`
    : `<button class="btn btn-ghost" onclick="evaluateCurrentListing(${l.id}, true)" title="Re-evaluate with AI">\u{1F504}</button>`;
  const passBtn = (l.status !== 'passed' && l.status !== 'applied' && l.status !== 'interview' && l.status !== 'offer')
    ? `<button class="btn btn-ghost" onclick="openPassModal(${l.id})" title="Pass on this role" style="color:var(--red)">\u{1F6AB}</button>`
    : '';
  const deleteBtn = `<button class="btn btn-danger" onclick="deleteCurrentListing(${l.id})" title="Delete listing">\u{2715}</button>`;

  if (l.status === 'passed') {
    return `
      <button class="btn btn-primary" style="flex:1" onclick="reconsiderListing(${l.id})">\u{21A9}\uFE0F Reconsider</button>
      ${reevalBtn}
      ${openUrlBtn}
      ${deleteBtn}`;
  }

  if (isUnevaluated) {
    const primary = running
      ? `<button class="btn btn-primary" style="flex:1" disabled id="dpEvaluateBtn">\u{23F3} Evaluating (30-60s)...</button>`
      : `<button class="btn btn-primary" style="flex:1" onclick="evaluateCurrentListing(${l.id}, false)" id="dpEvaluateBtn">\u{2728} Evaluate with AI</button>`;
    return `
      ${primary}
      ${openUrlBtn}
      ${passBtn}
      ${deleteBtn}`;
  }

  // Already evaluated — show status-based primary action
  if (l.status === 'applied' || l.status === 'interview' || l.status === 'offer') {
    const markLabel = l.status === 'applied' ? 'Mark Interview' : l.status === 'interview' ? 'Mark Offer' : 'Mark Rejected';
    const nextStatus = l.status === 'applied' ? 'interview' : l.status === 'interview' ? 'offer' : 'rejected';
    return `
      <button class="btn btn-primary" style="flex:1" onclick="changeListingStatus(${l.id}, '${nextStatus}')">\u{2713} ${markLabel}</button>
      ${reevalBtn}
      ${openUrlBtn}
      ${deleteBtn}`;
  }

  // Evaluated or rejected - allow marking applied AND re-evaluating AND passing
  const primaryBtn = l.status === 'rejected'
    ? (running
        ? `<button class="btn btn-ghost" style="flex:1" disabled id="dpEvaluateBtn">\u{23F3} Re-evaluating...</button>`
        : `<button class="btn btn-ghost" style="flex:1" onclick="evaluateCurrentListing(${l.id}, true)" id="dpEvaluateBtn">\u{1F504} Re-evaluate</button>`)
    : `<button class="btn btn-success" style="flex:1" onclick="changeListingStatus(${l.id}, 'applied')">\u{2713} Mark Applied</button>`;
  return `
    ${primaryBtn}
    ${l.status !== 'rejected' ? reevalBtn : ''}
    ${passBtn}
    ${openUrlBtn}
    ${deleteBtn}`;
}

const PASS_REASON_LABELS = {
  level_mismatch: 'Wrong seniority level',
  comp_too_low: 'Comp below threshold',
  stage_mismatch: 'Company too early / too late',
  domain_mismatch: 'Wrong function or industry',
  location: 'Not geographically workable',
  culture_fit: 'Culture / leadership red flags',
  scope_too_narrow: 'Scope too narrow',
  founder_market_fit: 'Don\'t trust the founder / team',
  timing: 'Not now, maybe later',
  other: 'Other (specify in note)',
};

function openPassModal(listingId) {
  const existing = document.getElementById('passModal');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.id = 'passModal';
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="modal" style="width:500px;max-width:95vw">
      <div class="modal-head">
        <div class="modal-title">\u{1F6AB} Pass on this role</div>
        <button class="dp-close" onclick="document.getElementById('passModal').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="font-size:12px;color:var(--text2);font-weight:500;margin-bottom:12px;line-height:1.5">
          Pick a reason — once you've passed on 15+ roles, these reasons start teaching the AI what to score lower for you.
        </div>
        <div class="fg">
          <label class="fl">Reason</label>
          <select class="fi" id="passReason">
            ${Object.entries(PASS_REASON_LABELS).map(([k, v]) => `<option value="${k}">${escapeHtml(v)}</option>`).join('')}
          </select>
        </div>
        <div class="fg">
          <label class="fl">Note (optional)</label>
          <textarea class="fi" id="passNote" rows="3" placeholder="Specifics the AI should remember, e.g., 'IC role, I want to stay as manager-of-managers'"></textarea>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" onclick="document.getElementById('passModal').remove()">Cancel</button>
        <button class="btn btn-danger" onclick="submitPass(${listingId})">\u{1F6AB} Pass</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('passReason').focus(), 100);
}

async function submitPass(listingId) {
  const reason = document.getElementById('passReason').value;
  const note = document.getElementById('passNote').value.trim();
  try {
    const updated = await window.api.listings.pass(listingId, reason, note);
    document.getElementById('passModal').remove();
    renderDetailPanel(updated);
    if (typeof renderPipeline === 'function' && document.getElementById('pipelineBoard')) {
      renderPipeline(document.getElementById('content'));
    }
    if (typeof renderListings === 'function' && document.querySelector('.tbl-wrap')) {
      renderListings(document.getElementById('content'));
    }
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    alert('Pass failed: ' + err.message);
  }
}

async function reconsiderListing(listingId) {
  if (!confirm('Bring this listing back into your active pipeline?')) return;
  try {
    const updated = await window.api.listings.reconsider(listingId);
    renderDetailPanel(updated);
    if (typeof renderPipeline === 'function' && document.getElementById('pipelineBoard')) {
      renderPipeline(document.getElementById('content'));
    }
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    alert('Reconsider failed: ' + err.message);
  }
}

async function evaluateCurrentListing(id, isRerun) {
  // Guard — if already running, bail with a polite nudge
  if (typeof isEvaluating === 'function' && isEvaluating(id)) {
    alert('Evaluation already in progress for this listing. Give it a few more seconds.');
    return;
  }
  if (typeof markEvaluatingStart === 'function') markEvaluatingStart(id);
  const btn = document.getElementById('dpEvaluateBtn');
  const originalHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = isRerun
      ? '\u{23F3} Re-evaluating (30-60s)...'
      : '\u{23F3} Evaluating (30-60s)...';
  }
  try {
    const updated = await window.api.listings.evaluate(id);
    renderDetailPanel(updated);
    if (typeof renderPipeline === 'function' && document.getElementById('pipelineBoard')) {
      renderPipeline(document.getElementById('content'));
    }
    if (typeof renderListings === 'function' && document.querySelector('.tbl-wrap')) {
      renderListings(document.getElementById('content'));
    }
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    // 409 = already-in-progress from another entry point — be gentle
    const msg = err && err.status === 409
      ? 'Another evaluation is already running for this listing. Wait a moment and refresh.'
      : ((isRerun ? 'Re-evaluation' : 'Evaluation') + ' failed: ' + err.message);
    alert(msg);
    if (btn) { btn.disabled = false; btn.innerHTML = originalHtml; }
  } finally {
    if (typeof markEvaluatingEnd === 'function') markEvaluatingEnd(id);
  }
}

async function changeListingStatus(id, status) {
  try {
    const updated = await window.api.listings.update(id, { status });
    renderDetailPanel(updated);
    if (typeof renderPipeline === 'function' && document.getElementById('pipelineBoard')) {
      renderPipeline(document.getElementById('content'));
    }
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    alert('Status change failed: ' + err.message);
  }
}

async function deleteCurrentListing(id) {
  if (!confirm('Delete this listing? This cannot be undone.')) return;
  try {
    await window.api.listings.delete(id);
    closeDetail();
    if (typeof renderPipeline === 'function' && document.getElementById('pipelineBoard')) {
      renderPipeline(document.getElementById('content'));
    }
    if (typeof renderListings === 'function' && document.querySelector('.tbl-wrap')) {
      renderListings(document.getElementById('content'));
    }
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

function renderAssetsSection(l) {
  const resumeFilename = l.tailored_resume_path ? l.tailored_resume_path.split('/').pop() : null;
  const coverFilename = l.cover_letter_path ? l.cover_letter_path.split('/').pop() : null;
  const hasResume = !!resumeFilename;
  const hasCover = !!coverFilename;

  const resumeCard = hasResume
    ? `<div class="asset-card" style="margin-bottom:8px">
        <div class="asset-icon">\u{1F4C4}</div>
        <div class="asset-info">
          <div class="asset-name">${escapeHtml(resumeFilename)}</div>
          <div class="asset-meta">Tailored resume${l.keyword_coverage != null ? ' \u00b7 ' + Math.round(l.keyword_coverage * 100) + '% keyword match' : ''}${l.tailoring_intensity ? ' \u00b7 ' + l.tailoring_intensity : ''}</div>
        </div>
        <div class="asset-actions">
          <a class="btn btn-primary btn-sm" href="/api/resumes/generated/${encodeURIComponent(resumeFilename)}" download target="_blank" title="Download">\u{2B07}</a>
          <button class="btn btn-ghost btn-sm" onclick="openResumeEditor(${l.id})" title="Edit markdown">\u{270F}\u{FE0F}</button>
          <button class="btn btn-ghost btn-sm" onclick="regenerateResume(${l.id})" title="Regenerate">\u{1F504}</button>
        </div>
      </div>`
    : `<button class="btn btn-primary" style="width:100%;justify-content:center;margin-bottom:8px" onclick="regenerateResume(${l.id})" id="genResumeBtn-${l.id}">\u{1F4C4} Generate Tailored Resume</button>`;

  const coverCard = hasCover
    ? `<div class="asset-card" style="margin-bottom:8px">
        <div class="asset-icon" style="background:linear-gradient(135deg,#fce7f3,#fed7aa)">\u{270D}</div>
        <div class="asset-info">
          <div class="asset-name">${escapeHtml(coverFilename)}</div>
          <div class="asset-meta">Cover letter${l.cover_letter_tone_override ? ' \u00b7 ' + l.cover_letter_tone_override : ''}</div>
        </div>
        <div class="asset-actions">
          <a class="btn btn-primary btn-sm" href="/api/resumes/cover-letters/${encodeURIComponent(coverFilename)}" download target="_blank" title="Download">\u{2B07}</a>
          <button class="btn btn-ghost btn-sm" onclick="openCoverLetterEditor(${l.id})" title="Edit markdown">\u{270F}\u{FE0F}</button>
          <button class="btn btn-ghost btn-sm" onclick="regenerateCoverLetter(${l.id})" title="Regenerate">\u{1F504}</button>
        </div>
      </div>`
    : `<button class="btn btn-pink" style="width:100%;justify-content:center;margin-bottom:8px" onclick="regenerateCoverLetter(${l.id})" id="genCoverBtn-${l.id}">\u{270D} Generate Cover Letter</button>`;

  // Always offer base resume as a fallback
  const baseResumeCard = `
    <div class="asset-card" style="background:var(--bg2)">
      <div class="asset-icon" style="background:linear-gradient(135deg,#dbeafe,#ccfbf1)">\u{1F4D1}</div>
      <div class="asset-info">
        <div class="asset-name">Your Base Resume</div>
        <div class="asset-meta">Unmodified, as uploaded</div>
      </div>
      <div class="asset-actions">
        <a class="btn btn-ghost btn-sm" href="/api/resumes/base-pdf" download target="_blank">\u{2B07}</a>
      </div>
    </div>`;

  return `
    <div class="dp-section-title">\u{1F4C4} Tailored Assets</div>
    ${resumeCard}
    ${coverCard}
    ${baseResumeCard}`;
}

async function regenerateResume(listingId){
  const btnId = `genResumeBtn-${listingId}`;
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = true; btn.innerHTML = '\u{23F3} Tailoring (30-50s)...'; }
  try {
    const updated = await window.api.resumes.tailorListing(listingId);
    renderDetailPanel(updated);
  } catch (err) {
    alert('Resume generation failed: ' + err.message);
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{1F4C4} Generate Tailored Resume'; }
  }
}

async function regenerateCoverLetter(listingId){
  const btnId = `genCoverBtn-${listingId}`;
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = true; btn.innerHTML = '\u{23F3} Writing (20-30s)...'; }
  try {
    const updated = await window.api.resumes.coverLetterListing(listingId);
    renderDetailPanel(updated);
  } catch (err) {
    alert('Cover letter generation failed: ' + err.message);
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{270D} Generate Cover Letter'; }
  }
}

// Override the runScan and showAddModal from app.js
function showAddModal(){
  document.getElementById('addModal').classList.add('open');
  setTimeout(() => document.getElementById('addListingInput').focus(), 100);
}
function closeModal(){
  document.getElementById('addModal').classList.remove('open');
}

// ============================================================================
// Full-page editors for tailored resume and cover letter markdown
// ============================================================================
function openResumeEditor(listingId){
  window._editorListingId = listingId;
  // close the detail panel if open
  document.getElementById('detailOverlay').classList.remove('open');
  document.getElementById('detailPanel').classList.remove('open');
  showPage('resume-editor');
}

function openCoverLetterEditor(listingId){
  window._editorListingId = listingId;
  document.getElementById('detailOverlay').classList.remove('open');
  document.getElementById('detailPanel').classList.remove('open');
  showPage('cover-editor');
}
