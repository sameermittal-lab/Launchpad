// LaunchPad App - connects to backend API
let CURRENT_PROFILE = null;
let PROFILES = [];

function scoreColor(s){return s>=4?'var(--green)':s>=3.5?'#a16207':'var(--red)'}
function scoreClass(s){return s>=4?'score-high':s>=3.5?'score-mid':'score-low'}
function typeClass(t){return t==='Remote'?'t-remote':t==='Hybrid'?'t-hybrid':'t-onsite'}
function statusClass(s){return{new:'sb-new',evaluated:'sb-eval',applied:'sb-applied',interview:'sb-interview',rejected:'sb-rejected',offer:'sb-offer'}[s]||'sb-new'}

function loginAs(profile){
  CURRENT_PROFILE = profile;
  document.getElementById('loginScreen').style.display='none';
  document.getElementById('sidebar').style.display='flex';
  document.getElementById('main').style.display='flex';
  const initials = profile.name.split(' ').map(n=>n[0]).join('').slice(0,2).toUpperCase();
  document.getElementById('currentProfileAvatar').textContent = initials;
  document.getElementById('currentProfileName').textContent = profile.name;
  const roleEl = document.querySelector('.profile-role');
  if (roleEl) roleEl.textContent = profile.role_title || 'User';
  updateSidebarInfo();
  showPage('dashboard');
  // Check if this is a first-time user and show the setup wizard
  checkAndShowWizard();
}

async function updateSidebarInfo(){
  // Cost tracker
  try {
    const u = await window.api.usage.summary();
    const tracker = document.getElementById('costTracker');
    if (tracker) {
      tracker.querySelector('.cost-value').textContent = '$' + u.month_cost_usd.toFixed(2);
      tracker.querySelector('.cost-detail').textContent =
        `${u.month_calls} AI call${u.month_calls === 1 ? '' : 's'} this month`;
    }
  } catch {}
  // Scheduler
  try {
    const s = await window.api.scheduler.status();
    const dot = document.getElementById('schedDot');
    const text = document.getElementById('schedText');
    if (dot && text) {
      if (s.running) {
        dot.style.background = 'var(--green)';
        dot.style.animation = 'pulse 2s ease-in-out infinite';
        const next = s.jobs.map(j => new Date(j.next_run_time)).filter(d => d).sort((a,b) => a-b)[0];
        text.textContent = next ? `Next scan: ${relTime(next)}` : 'Scheduler active';
      } else {
        dot.style.background = 'var(--red)';
        text.textContent = 'Scheduler offline';
      }
    }
  } catch {}
  // Nav badges: unprocessed Gmail + active follow-ups
  updateNavBadges();
}

async function updateNavBadges(){
  // Pipeline: count new + evaluated (active listings needing attention)
  try {
    const stats = await window.api.listings.stats();
    const active = (stats.new || 0) + (stats.evaluated || 0);
    setNavBadge('pipeline', active, 'default');
  } catch {
    setNavBadge('pipeline', 0, 'default');
  }
  // Gmail unread/unprocessed badge
  try {
    const st = await window.api.gmail.status();
    setNavBadge('gmail', st.unprocessed_count || 0, 'warn');
  } catch {
    setNavBadge('gmail', 0, 'warn');
  }
  // Active reminders badge
  try {
    const rs = await window.api.reminders.list(false);
    setNavBadge('reminders', Array.isArray(rs) ? rs.length : 0, 'info');
  } catch {
    setNavBadge('reminders', 0, 'info');
  }
}

function setNavBadge(page, count, flavor){
  const item = document.querySelector(`.nav-item[data-page="${page}"]`);
  if (!item) return;
  let badge = item.querySelector('.ni-badge');
  if (!count || count <= 0) {
    if (badge) badge.remove();
    return;
  }
  if (!badge) {
    badge = document.createElement('span');
    badge.className = flavor && flavor !== 'default' ? `ni-badge ${flavor}` : 'ni-badge';
    item.appendChild(badge);
  } else {
    badge.className = flavor && flavor !== 'default' ? `ni-badge ${flavor}` : 'ni-badge';
  }
  badge.textContent = count > 99 ? '99+' : String(count);
}

function relTime(future){
  const mins = Math.max(0, Math.round((future.getTime() - Date.now()) / 60000));
  if (mins < 1) return 'any moment';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.round(hrs/24)}d`;
}

async function loadProfilesForLogin(){
  const container = document.getElementById('loginProfiles');
  try {
    PROFILES = await window.api.profiles.list();
    if (PROFILES.length === 0) {
      container.innerHTML = `
        <div style="text-align:center;padding:30px 20px">
          <div style="font-size:36px;margin-bottom:10px">\u{1F680}</div>
          <div style="font-size:15px;font-weight:700;margin-bottom:4px">No profiles yet</div>
          <div style="font-size:12px;color:var(--text2);font-weight:500;margin-bottom:14px">Create your first profile to get started</div>
        </div>`;
      return;
    }
    container.innerHTML = PROFILES.map(p => {
      const initials = p.name.split(' ').map(n=>n[0]).join('').slice(0,2).toUpperCase();
      const gradient = p.id % 2 === 0 
        ? 'background:linear-gradient(135deg,#ec4899,#f97316)' 
        : '';
      return `
        <div class="login-profile" onclick='selectProfile(${p.id})'>
          <div class="login-profile-avatar" style="${gradient}">${initials}</div>
          <div class="login-profile-info">
            <div class="login-profile-name">${p.name}</div>
            <div class="login-profile-meta">${p.role_title || 'User'} &middot; ${p.listing_count} listing${p.listing_count===1?'':'s'}${p.has_pin?' &middot; \u{1F510} PIN':''}</div>
          </div>
          <div class="chevron">&rsaquo;</div>
        </div>`;
    }).join('');
  } catch (err) {
    container.innerHTML = `<div style="text-align:center;padding:20px;color:var(--red);font-size:13px">Error loading profiles: ${err.message}</div>`;
  }
}

function selectProfile(profileId){
  const profile = PROFILES.find(p => p.id === profileId);
  if (!profile) return;
  if (profile.has_pin) {
    const pin = prompt('Enter PIN for ' + profile.name + ':');
    if (pin === null) return;
    doLogin(profile, pin);
  } else {
    doLogin(profile, null);
  }
}

async function doLogin(profile, pin){
  try {
    const user = await window.api.auth.login(profile.id, pin);
    // Fetch full settings to have profile_data available everywhere
    const settings = await window.api.settings.get();
    CURRENT_PROFILE = { ...profile, ...user, settings };
    loginAs(CURRENT_PROFILE);
  } catch (err) {
    if (err.status === 401) {
      alert('Incorrect PIN');
    } else {
      alert('Login failed: ' + err.message);
    }
  }
}

function showCreateProfile(){
  document.getElementById('createProfileModal').classList.add('open');
  setTimeout(() => document.getElementById('cp-name').focus(), 100);
}

function closeCreateProfile(){
  document.getElementById('createProfileModal').classList.remove('open');
}

async function createProfile(){
  const name = document.getElementById('cp-name').value.trim();
  if (!name) {
    alert('Name is required');
    return;
  }
  const data = {
    name,
    role_title: document.getElementById('cp-role').value.trim() || null,
    email: document.getElementById('cp-email').value.trim() || null,
    phone: document.getElementById('cp-phone').value.trim() || null,
    location: document.getElementById('cp-location').value.trim() || null,
    linkedin: document.getElementById('cp-linkedin').value.trim() || null,
    pin: document.getElementById('cp-pin').value.trim() || null,
  };
  try {
    const profile = await window.api.profiles.create(data);
    closeCreateProfile();
    // Auto-login to the new profile
    await doLogin(profile, data.pin);
  } catch (err) {
    alert('Failed to create profile: ' + err.message);
  }
}

async function showProfileSwitcher(){
  if(!confirm('Switch profile? This will log you out.')) return;
  try {
    await window.api.auth.logout();
  } catch {}
  CURRENT_PROFILE = null;
  document.getElementById('loginScreen').style.display='flex';
  document.getElementById('sidebar').style.display='none';
  document.getElementById('main').style.display='none';
  await loadProfilesForLogin();
}

function toggleSidebar(){
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const isOpen = sidebar.classList.contains('open');
  sidebar.classList.toggle('open', !isOpen);
  if (backdrop) backdrop.classList.toggle('open', !isOpen);
}

// Close sidebar when navigating on mobile
function closeSidebarIfMobile() {
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebarBackdrop');
    if (sidebar) sidebar.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
  }
}

function showPage(page){
  // Stop any page-scoped timers from the previous page
  if (typeof _stopSuggCountdown === 'function') _stopSuggCountdown();
  closeSidebarIfMobile();

  document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active',n.dataset.page===page));
  const c=document.getElementById('content');
  const r={dashboard:renderDashboard,pipeline:renderPipeline,listings:renderListings,passed:renderPassed,history:renderHistory,scanner:renderScanner,gmail:renderGmail,reminders:renderReminders,resume:renderResume,cover:renderCover,interview:renderInterview,companies:renderCompanies,negotiation:renderNegotiation,settings:renderSettings,'resume-editor':renderResumeEditor,'cover-editor':renderCoverLetterEditor};
  (r[page]||renderDashboard)(c);
  window.scrollTo(0,0);
  // close mobile menu + clear stale search UI
  document.getElementById('sidebar').classList.remove('mobile-open');
  hideSearchDropdown();
  // refresh network URL if present
  if (page === 'dashboard') loadNetworkInfo();
}

async function loadNetworkInfo(){
  const el = document.getElementById('networkUrl');
  if (!el) return;
  try {
    const info = await window.api.network();
    el.textContent = info.url;
    window._qrDataUrl = info.qr_data_url;
  } catch (err) {
    el.textContent = 'http://localhost:5000';
  }
}

function showNetworkQR(){
  if (!window._qrDataUrl) {
    alert('QR code not available yet');
    return;
  }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="modal" style="text-align:center">
      <div class="modal-head" style="justify-content:center;border-bottom:none">
        <div class="modal-title">\u{1F4F1} Scan to open on another device</div>
      </div>
      <div class="modal-body">
        <img src="${window._qrDataUrl}" style="width:240px;height:240px;border-radius:12px">
        <div style="margin-top:14px;font-size:12px;color:var(--text2);font-weight:500">Anyone on your local network can scan this.</div>
      </div>
      <div class="modal-foot" style="justify-content:center">
        <button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove()">Got it</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

function openDetail(id){
  // Overridden in js/listings.js to fetch real data. Kept as a no-op stub
  // so the call site remains safe if listings.js fails to load.
}
function closeDetail(){
  document.getElementById('detailOverlay').classList.remove('open');
  document.getElementById('detailPanel').classList.remove('open');
}
function showAddModal(){document.getElementById('addModal').classList.add('open')}
function closeModal(){document.getElementById('addModal').classList.remove('open')}
function runScan(){alert('\u{1F50D} Scanning via Greenhouse, Ashby, Lever APIs...\n\nThis runs in the background and new listings will appear in your pipeline.')}

async function renderDashboard(c){
  const firstName = (CURRENT_PROFILE && CURRENT_PROFILE.name) ? CURRENT_PROFILE.name.split(' ')[0] : 'there';

  c.innerHTML = `
  <div id="keyWarning"></div>
  <div class="network-banner" id="networkBanner">
    <div class="network-icon">\u{1F310}</div>
    <div class="network-content">
      <div class="network-title">LaunchPad is running on your local network</div>
      <div class="network-url" id="networkUrl">Loading...</div>
    </div>
    <button class="btn btn-ghost btn-sm" onclick="showNetworkQR()">\u{1F4F1} QR Code</button>
  </div>

  <div class="page-header">
    <div>
      <div class="page-title">Welcome back, ${escapeHtml(firstName)} <span style="display:inline-block;animation:wave 2s ease-in-out infinite;transform-origin:70% 70%">\u{1F44B}</span></div>
      <div class="page-subtitle">Here's your job search snapshot</div>
    </div>
  </div>

  <div id="dashStats" class="stats-row">
    <div class="empty-state" style="grid-column:1/-1"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading stats...</div></div>
  </div>
  <div id="dashBody"></div>`;

  loadNetworkInfo();

  try {
    const [stats, topMatches, settings] = await Promise.all([
      window.api.listings.stats(),
      window.api.listings.list({
        min_score: 4,
        limit: 5,
        order_by: '-score',
        exclude_status: 'passed,applied,interview,offer,rejected',
      }),
      window.api.settings.get(),
    ]);
    renderDashStats(stats);
    renderDashBody(stats, topMatches);

    if (!settings.has_llm_api_key) {
      document.getElementById('keyWarning').innerHTML = `
        <div style="background:linear-gradient(135deg,#fef3c7,#fed7aa);border:1px solid #fcd34d;border-radius:var(--radius);padding:14px 18px;margin-bottom:20px;display:flex;align-items:center;gap:12px">
          <div style="font-size:22px">\u{1F511}</div>
          <div style="flex:1">
            <div style="font-size:13px;font-weight:700;color:#78350f">Add your LLM API key to get started</div>
            <div style="font-size:12px;color:#92400e;font-weight:500">Evaluations, resume tailoring, and Gmail scanning all need one. Takes 30 seconds.</div>
          </div>
          <button class="btn btn-primary btn-sm" onclick="showPage('settings')">Add API Key</button>
        </div>`;
    }
  } catch (err) {
    document.getElementById('dashBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-title">Error loading dashboard</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function renderDashStats(stats){
  const avg = stats.avg_score != null ? stats.avg_score.toFixed(1) : '-';
  const scoreColor_ = stats.avg_score == null ? 'var(--text3)' : stats.avg_score >= 4 ? 'var(--green)' : stats.avg_score >= 3.5 ? '#a16207' : 'var(--red)';
  document.getElementById('dashStats').innerHTML = `
    <div class="stat-card stat-new" onclick="showPage('listings')">
      <div class="stat-icon-wrap"><div class="stat-icon si-new">\u{2728}</div></div>
      <div class="stat-label">New</div>
      <div class="stat-value" style="color:var(--blue)">${stats.new}</div>
    </div>
    <div class="stat-card stat-eval" onclick="showPage('listings')">
      <div class="stat-icon-wrap"><div class="stat-icon si-eval">\u{1F52E}</div></div>
      <div class="stat-label">Evaluated</div>
      <div class="stat-value" style="color:var(--purple)">${stats.evaluated}</div>
    </div>
    <div class="stat-card stat-app" onclick="showPage('listings')">
      <div class="stat-icon-wrap"><div class="stat-icon si-app">\u{1F680}</div></div>
      <div class="stat-label">Applied</div>
      <div class="stat-value" style="color:var(--green)">${stats.applied}</div>
    </div>
    <div class="stat-card stat-int" onclick="showPage('listings')">
      <div class="stat-icon-wrap"><div class="stat-icon si-int">\u{1F3AF}</div></div>
      <div class="stat-label">Interviews</div>
      <div class="stat-value" style="color:var(--orange)">${stats.interview}</div>
    </div>
    <div class="stat-card stat-score">
      <div class="stat-icon-wrap"><div class="stat-icon si-score">\u{2B50}</div></div>
      <div class="stat-label">Avg Score</div>
      <div class="stat-value" style="color:${scoreColor_}">${avg}</div>
    </div>`;
}

function renderDashBody(stats, topMatches){
  const body = document.getElementById('dashBody');
  if (stats.total === 0) {
    body.innerHTML = `
      <div style="background:linear-gradient(135deg,#eef0ff 0%,#fce7f3 100%);border-radius:var(--radius);padding:40px;text-align:center;border:1px solid var(--border)">
        <div style="font-size:48px;margin-bottom:12px">\u{1F680}</div>
        <div style="font-size:20px;font-weight:800;margin-bottom:6px">Ready to launch?</div>
        <div style="font-size:14px;color:var(--text2);font-weight:500;margin-bottom:20px">Add your first job listing to get started. Paste a URL or the JD text.</div>
        <button class="btn btn-primary" onclick="showAddModal()" style="font-size:14px;padding:12px 24px">\u{2728} Add First Listing</button>
      </div>`;
    return;
  }

  body.innerHTML = `
    <h3 style="font-size:16px;font-weight:700;margin-bottom:14px;display:flex;align-items:center;gap:8px">\u{1F525} Top Matches</h3>
    ${topMatches.length === 0
      ? `<div class="empty-state" style="padding:24px"><div class="es-desc">No matches scoring 4.0+ yet</div></div>`
      : topMatches.map(j => jobCardHTML(j)).join('')}`;
}

function jobCardHTML(j){
  const score = j.score != null ? j.score.toFixed(1) : '-';
  const scoreCls = j.score == null ? 'score-mid' : j.score >= 4 ? 'score-high' : j.score >= 3.5 ? 'score-mid' : 'score-low';
  const jobType = j.job_type || '';
  const salaryTag = j.salary_range ? `<span class="tag t-neutral">${escapeHtml(j.salary_range)}</span>` : '';
  return `
    <div class="jcard" onclick="openDetail(${j.id})" style="margin-bottom:10px">
      <div class="jcard-head">
        <div>
          <div class="jcard-co">${escapeHtml(j.company)}</div>
          <div class="jcard-title">${escapeHtml(j.role_title)}</div>
        </div>
        <div class="jcard-score-pill ${scoreCls}">${score}</div>
      </div>
      <div class="jcard-meta">
        ${jobType ? `<span class="tag ${typeClass(jobType)}">${escapeHtml(jobType)}</span>` : ''}
        ${salaryTag}
        ${j.archetype ? `<span class="tag t-neutral">${escapeHtml(j.archetype)}</span>` : ''}
      </div>
    </div>`;
}

async function renderPipeline(c){
  c.innerHTML = `
    <div class="page-header">
      <div>
        <div class="page-title">Pipeline Board \u{1F4CB}</div>
        <div class="page-subtitle">Your listings organized by status</div>
      </div>
      <div class="page-actions">
        <span id="pipelineBulkActions"></span>
        <button class="btn btn-primary" onclick="showAddModal()">\u{2795} Add Listing</button>
      </div>
    </div>
    <div id="pipelineBoard"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading pipeline...</div></div></div>`;

  try {
    const all = await window.api.listings.list({ limit: 500 });
    const byStatus = {new: [], evaluated: [], applied: [], interview: [], offer: [], rejected: [], passed: []};
    for (const j of all) {
      if (byStatus[j.status]) byStatus[j.status].push(j);
    }
    // Bulk-eval button if there are unevaluated listings — adaptive based on smart filter
    const bulk = document.getElementById('pipelineBulkActions');
    if (bulk && byStatus.new.length > 0) {
      renderBatchEvalBanner(bulk, 'pipeline');
    }
    const cols = [
      {title: 'New', status: 'new', dot: 'dot-new', jobs: byStatus.new},
      {title: 'Evaluated', status: 'evaluated', dot: 'dot-eval', jobs: byStatus.evaluated},
      {title: 'Applied', status: 'applied', dot: 'dot-app', jobs: byStatus.applied},
      {title: 'Interview', status: 'interview', dot: 'dot-int', jobs: byStatus.interview},
      {title: 'Offer', status: 'offer', dot: 'dot-final', jobs: byStatus.offer},
      {title: 'Rejected by company', status: 'rejected', dot: 'dot-final', jobs: byStatus.rejected},
    ];
    document.getElementById('pipelineBoard').innerHTML = `
      <div class="pipeline-board">
        ${cols.map(col => `
          <div class="pcol">
            <div class="pcol-header">
              <div class="pcol-title"><span class="pcol-dot ${col.dot}"></span>${col.title}</div>
              <span class="pcol-count">${col.jobs.length}</span>
            </div>
            <div class="pcol-body">
              ${col.jobs.length === 0
                ? '<div class="empty-state" style="padding:30px 10px"><div class="es-icon">\u{1F4CB}</div><div class="es-desc">No listings</div></div>'
                : col.jobs.map(j => pipelineCardHTML(j)).join('')}
            </div>
          </div>`).join('')}
      </div>`;
    if (window._searchQuery) applySearchFilterOnListings();
  } catch (err) {
    document.getElementById('pipelineBoard').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-title">Error loading pipeline</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function pipelineCardHTML(j){
  const score = j.score != null ? j.score.toFixed(1) : '-';
  const scoreCls = j.score == null ? 'score-mid' : j.score >= 4 ? 'score-high' : j.score >= 3.5 ? 'score-mid' : 'score-low';
  const date = new Date(j.created_at).toLocaleDateString();
  const needsEval = j.score == null && j.status === 'new';
  const canReval = j.score != null;
  const running = isEvaluating(j.id) || j.evaluation_in_progress;
  // Unevaluated → prominent "Evaluate" call-to-action
  // Evaluated → quieter "Re-evaluate" ghost button so users can rerun anytime
  // Running → disabled waiting-state
  let inlineBtn = '';
  if (running) {
    inlineBtn = `<button class="btn btn-ghost btn-sm" style="padding:3px 8px;font-size:11px;margin-top:6px;width:100%;justify-content:center;opacity:0.75" disabled>\u{23F3} ${needsEval ? 'Evaluating' : 'Re-evaluating'}...</button>`;
  } else if (needsEval) {
    inlineBtn = `<button class="btn btn-ghost btn-sm" style="padding:3px 8px;font-size:11px;margin-top:6px;width:100%;justify-content:center"
          onclick="event.stopPropagation(); evaluateFromCard(${j.id}, this, false)">\u{2728} Evaluate</button>`;
  } else if (canReval) {
    inlineBtn = `<button class="btn btn-ghost btn-sm" style="padding:3px 8px;font-size:11px;margin-top:6px;width:100%;justify-content:center;opacity:0.75"
          onclick="event.stopPropagation(); evaluateFromCard(${j.id}, this, true)" title="Re-run evaluation with latest prompt + fresh web search">\u{1F504} Re-evaluate</button>`;
  }
  return `
    <div class="jcard" onclick="openDetail(${j.id})">
      <div class="jcard-head">
        <div>
          <div class="jcard-co">${escapeHtml(j.company)}</div>
          <div class="jcard-title">${escapeHtml(j.role_title)}</div>
        </div>
        <div class="jcard-score-pill ${scoreCls}">${score}</div>
      </div>
      <div class="jcard-meta">
        ${j.job_type ? `<span class="tag ${typeClass(j.job_type)}">${escapeHtml(j.job_type)}</span>` : ''}
      </div>
      <div class="jcard-bottom">
        <span class="jcard-date">${date}</span>
        <span class="jcard-src">${escapeHtml(j.source)}</span>
      </div>
      ${inlineBtn}
    </div>`;
}

async function evaluateFromCard(id, btn, isRerun){
  if (isEvaluating(id)) {
    return;  // Already running — ignore double-click
  }
  markEvaluatingStart(id);
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = isRerun ? '\u{23F3} Re-evaluating...' : '\u{23F3} Evaluating...';
  try {
    await window.api.listings.evaluate(id);
    // Refresh pipeline to move card between columns
    renderPipeline(document.getElementById('content'));
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    const msg = err && err.status === 409
      ? 'Another evaluation is already running for this listing.'
      : ((isRerun ? 'Re-evaluation' : 'Evaluation') + ' failed: ' + err.message);
    alert(msg);
    btn.disabled = false;
    btn.innerHTML = original;
  } finally {
    markEvaluatingEnd(id);
  }
}

async function evaluateFromRow(id, btn, isRerun){
  if (isEvaluating(id)) return;
  markEvaluatingStart(id);
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '\u{23F3}';
  try {
    await window.api.listings.evaluate(id);
    // Refresh the listings table
    renderListings(document.getElementById('content'));
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    const msg = err && err.status === 409
      ? 'Another evaluation is already running for this listing.'
      : ((isRerun ? 'Re-evaluation' : 'Evaluation') + ' failed: ' + err.message);
    alert(msg);
    btn.disabled = false;
    btn.innerHTML = original;
  } finally {
    markEvaluatingEnd(id);
  }
}

async function evaluateAllNew(){
  const all = await window.api.listings.list({ status: 'new', limit: 500 });
  if (!all.length) { alert('No new listings to evaluate.'); return; }
  if (!confirm(`Evaluate ${all.length} listing${all.length===1?'':'s'}?\n\nEach takes ~15-30s. Total estimate: ~${Math.ceil(all.length * 20 / 60)} min.\n\nThis will consume LLM credits.`)) return;
  const btn = document.getElementById('evalAllBtn');
  let done = 0, failed = 0, skipped = 0;
  for (const l of all) {
    if (isEvaluating(l.id)) { skipped++; continue; }
    if (btn) btn.innerHTML = `\u{23F3} Evaluating ${done+1}/${all.length}...`;
    markEvaluatingStart(l.id);
    try {
      await window.api.listings.evaluate(l.id);
      done++;
    } catch (err) {
      if (err && err.status === 409) {
        skipped++;
      } else {
        console.error('eval failed for', l.id, err);
        failed++;
      }
    } finally {
      markEvaluatingEnd(l.id);
    }
  }
  alert(`Bulk evaluation done.\n\n\u2713 Evaluated: ${done}\n\u2717 Failed: ${failed}${skipped ? `\n\u23E9 Skipped (already running): ${skipped}` : ''}`);
  renderPipeline(document.getElementById('content'));
  if (typeof updateNavBadges === 'function') updateNavBadges();
}

async function renderListings(c){
  // Register sort reloader so header clicks re-render with the new sort
  registerTableSortReload('all_listings', () => renderListings(c));
  c.innerHTML = `
    <div class="page-header">
      <div>
        <div class="page-title">All Listings \u{1F50D}</div>
        <div class="page-subtitle">Everything in your pipeline</div>
      </div>
      <div class="page-actions">
        <span id="listingsBulkActions"></span>
        <button class="btn btn-ghost" onclick="cleanupByFilter()" title="Retroactively apply your title filter to existing listings">\u{1F9F9} Apply filter</button>
        <button class="btn btn-primary" onclick="showAddModal()">\u{2795} Add</button>
      </div>
    </div>
    <div id="batchEvalBannerListings"></div>
    <div id="listingsTable"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const all = await window.api.listings.list({ limit: 500 });
    // Render the adaptive batch eval banner above the table
    const bannerEl = document.getElementById('batchEvalBannerListings');
    if (bannerEl) renderBatchEvalBanner(bannerEl, 'listings');
    if (all.length === 0) {
      document.getElementById('listingsTable').innerHTML = `
        <div class="empty-state" style="padding:60px 20px">
          <div class="es-icon">\u{1F4CB}</div>
          <div class="es-title">No listings yet</div>
          <div class="es-desc">Paste a job URL to get started.</div>
          <button class="btn btn-primary" onclick="showAddModal()" style="margin-top:14px">\u{2728} Add First Listing</button>
        </div>`;
      return;
    }
    document.getElementById('listingsTable').innerHTML = `
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            ${sortableHeader('all_listings', 'company', 'Company', 'string')}
            ${sortableHeader('all_listings', 'role_title', 'Role', 'string')}
            ${sortableHeader('all_listings', 'location', 'Location', 'string')}
            ${sortableHeader('all_listings', 'score', 'Score', 'number')}
            ${sortableHeader('all_listings', 'status', 'Status', 'string')}
            ${sortableHeader('all_listings', 'source', 'Source', 'string')}
            ${sortableHeader('all_listings', 'created_at', 'Date', 'date')}
            <th></th>
          </tr></thead>
          <tbody>
            ${applyTableSort(all, 'all_listings', 'created_at', 'desc', 'date').map(j => {
              const rowEvalBtn = j.score == null
                ? `<button class="btn btn-ghost btn-sm" title="Evaluate with AI" onclick="event.stopPropagation();evaluateFromRow(${j.id}, this, false)">\u{2728}</button>`
                : `<button class="btn btn-ghost btn-sm" title="Re-evaluate with AI" onclick="event.stopPropagation();evaluateFromRow(${j.id}, this, true)">\u{1F504}</button>`;
              return `<tr onclick="openDetail(${j.id})" style="cursor:pointer">
              <td style="font-weight:700;color:var(--primary)">${escapeHtml(j.company)}</td>
              <td>${escapeHtml(j.role_title)}</td>
              <td>${j.job_type ? `<span class="tag ${typeClass(j.job_type)}" style="margin-right:4px">${escapeHtml(j.job_type)}</span>` : ''}${escapeHtml(j.location || '')}</td>
              <td><span style="font-weight:800;color:${j.score==null?'var(--text3)':j.score>=4?'var(--green)':j.score>=3.5?'#a16207':'var(--red)'};font-size:15px">${j.score != null ? j.score.toFixed(1) : '-'}</span></td>
              <td><span class="sb ${statusClass(j.status)}">${j.status}</span></td>
              <td style="color:var(--text3);font-size:12px;font-weight:500">${escapeHtml(j.source)}</td>
              <td style="color:var(--text3);font-size:12px;font-weight:500">${new Date(j.created_at).toLocaleDateString()}</td>
              <td style="white-space:nowrap">
                ${rowEvalBtn}
                <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();openDetail(${j.id})">View</button>
              </td>
            </tr>`;}).join('')}
          </tbody>
        </table>
      </div>`;
    if (window._searchQuery) applySearchFilterOnListings();
  } catch (err) {
    document.getElementById('listingsTable').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-title">Error loading listings</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

async function renderHistory(c){
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Application History \u{1F4DC}</div>
      <div class="page-subtitle">Complete timeline of actions on your pipeline</div>
    </div>
  </div>
  <div id="historyBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const events = await window.api.history.list(200);
    const body = document.getElementById('historyBody');
    if (events.length === 0) {
      body.innerHTML = `<div class="empty-state" style="padding:60px 20px"><div class="es-icon">\u{1F4DC}</div><div class="es-title">No history yet</div><div class="es-desc">Every evaluation, status change, and submission will show up here.</div></div>`;
      return;
    }
    body.innerHTML = `<div class="tbl-wrap"><div style="padding:20px 24px">${events.map(e => historyItemHTML(e)).join('')}</div></div>`;
  } catch (err) {
    document.getElementById('historyBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function historyItemHTML(e){
  const colors = {
    evaluation: 'var(--purple)', status_change: 'var(--blue)', submission: 'var(--green)',
    resume_generated: 'var(--orange)', cover_letter_generated: 'var(--pink)',
    email_received: 'var(--teal)',
  };
  const color = colors[e.event_type] || 'var(--text3)';
  const emoji = {
    evaluation: '\u{1F52E}', status_change: '\u{1F504}', submission: '\u{1F680}',
    resume_generated: '\u{1F4C4}', cover_letter_generated: '\u{270D}',
    email_received: '\u{1F4E7}',
  }[e.event_type] || '\u{1F4CC}';

  let detail = '';
  const d = e.event_data || {};
  if (e.event_type === 'evaluation') {
    detail = `Score: <strong>${d.score || '?'}</strong> (Grade ${d.grade || '?'}) \u00b7 Cost: $${(d.cost_usd || 0).toFixed(4)}`;
  } else if (e.event_type === 'status_change') {
    detail = `<strong>${d.from || '?'}</strong> \u2192 <strong>${d.to || '?'}</strong>`;
  } else if (e.event_type === 'resume_generated') {
    const cov = d.keyword_coverage ? Math.round(d.keyword_coverage * 100) + '% keyword coverage' : 'Resume tailored';
    detail = cov + ` \u00b7 Cost: $${(d.cost_usd || 0).toFixed(4)}`;
  } else if (e.event_type === 'cover_letter_generated') {
    detail = `Tone: ${d.tone || 'default'} \u00b7 Cost: $${(d.cost_usd || 0).toFixed(4)}`;
  }

  const title = e.listing_company
    ? `${e.event_type.replace(/_/g, ' ')}: <span style="color:var(--primary)">${escapeHtml(e.listing_company)}</span> \u00b7 ${escapeHtml(e.listing_role || '')}`
    : e.event_type.replace(/_/g, ' ');

  return `
    <div class="history-item" ${e.listing_id ? `onclick="openDetail(${e.listing_id})" style="cursor:pointer"` : ''}>
      <div class="history-dot" style="background:${color};margin-top:6px">${emoji.startsWith('\\u') ? '' : emoji}</div>
      <div class="history-content">
        <div class="history-title" style="text-transform:capitalize">${title}</div>
        <div class="history-meta">${new Date(e.created_at).toLocaleString()}${detail ? ' &middot; ' + detail : ''}</div>
      </div>
    </div>`;
}

async function renderScanner(c){
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Portal Scanner \u{1F4E1}</div>
      <div class="page-subtitle">Auto-discover new listings from company career pages. Keywords are tuned in <a onclick="showPage('settings')" style="cursor:pointer;color:var(--primary);font-weight:600">Settings</a>.</div>
    </div>
    <div class="page-actions">
      <button class="btn btn-ghost" onclick="showAiMonitorHelp()" title="What is AI Company Monitor?">\u{2728} AI Monitor</button>
      <button class="btn btn-primary" onclick="scanNow()">\u{25B6} Scan All Now</button>
    </div>
  </div>
  <div id="scannerBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const companies = await window.api.scanner.listCompanies();
    renderScannerBody(companies);
  } catch (err) {
    document.getElementById('scannerBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function renderScannerBody(companies){
  // Register sort reloader so clicking a column header re-renders with the new sort
  registerTableSortReload('scanner_companies', () => renderScannerBody(companies));
  const body = document.getElementById('scannerBody');
  if (companies.length === 0){
    body.innerHTML = `
      <div style="background:linear-gradient(135deg,#eef0ff 0%,#fce7f3 100%);border-radius:var(--radius);padding:40px;text-align:center;border:1px solid var(--border)">
        <div style="font-size:48px;margin-bottom:12px">\u{1F4E1}</div>
        <div style="font-size:20px;font-weight:800;margin-bottom:6px">Start tracking companies</div>
        <div style="font-size:14px;color:var(--text2);font-weight:500;margin-bottom:20px">Load our curated list of 20+ AI companies, or add your own.</div>
        <div style="display:flex;gap:10px;justify-content:center">
          <button class="btn btn-primary" onclick="loadDefaultCompanies()">\u{1F680} Load 20+ AI Companies</button>
          <button class="btn btn-ghost" onclick="showAddCompanyModal()">\u{2795} Add Custom</button>
        </div>
      </div>`;
    return;
  }
  const enabled = companies.filter(c => c.enabled).length;
  const atsMonitored = companies.filter(c => c.enabled && (c.platform !== 'custom') && c.api_url).length;
  const aiMonitored = companies.filter(c => c.ai_monitor_enabled).length;
  const totalAts = companies.reduce((sum, c) => sum + (c.last_job_count || 0), 0);
  const totalAi = companies.reduce((sum, c) => sum + (c.last_ai_monitor_count || 0), 0);
  const totalJobs = totalAts + totalAi;
  body.innerHTML = `
    <div class="stats-row" style="grid-template-columns:repeat(3,1fr)">
      <div class="stat-card stat-new">
        <div class="stat-icon-wrap"><div class="stat-icon si-new">\u{1F3E2}</div></div>
        <div class="stat-label">Companies Tracked</div>
        <div class="stat-value">${companies.length}</div>
        <div class="stat-change">${atsMonitored} ATS \u00b7 ${aiMonitored} AI</div>
      </div>
      <div class="stat-card stat-eval">
        <div class="stat-icon-wrap"><div class="stat-icon si-eval">\u{1F4CB}</div></div>
        <div class="stat-label">Jobs Found (Last Scan)</div>
        <div class="stat-value">${totalJobs}</div>
        <div class="stat-change">ATS: ${totalAts} \u00b7 AI: ${totalAi}</div>
      </div>
      <div class="stat-card stat-app">
        <div class="stat-icon-wrap"><div class="stat-icon si-app">\u{2795}</div></div>
        <div class="stat-label">Add Company</div>
        <div class="stat-value" style="font-size:14px">
          <button class="btn btn-ghost btn-sm" onclick="showAddCompanyModal()">\u{2795} Add Custom</button>
        </div>
      </div>
    </div>
    <div id="companySuggestionsStrip"></div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            ${sortableHeader('scanner_companies', 'name', 'Company', 'string')}
            ${sortableHeader('scanner_companies', 'last_job_count', 'Jobs Found', 'number')}
            ${sortableHeader('scanner_companies', 'last_ai_monitor_at', 'AI Monitor \u{2728}', 'date')}
            ${sortableHeader('scanner_companies', 'last_scanned_at', 'ATS Platform', 'date')}
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${applyTableSort(companies, 'scanner_companies', 'name', 'asc', 'string').map(co => {
            const isCustom = (co.platform === 'custom') || !co.api_url;
            const atsCount = co.last_job_count || 0;
            const aiCount = co.last_ai_monitor_count || 0;
            const totalCount = atsCount + aiCount;
            const aiOn = !!co.ai_monitor_enabled;
            const aiLast = co.last_ai_monitor_at ? new Date(co.last_ai_monitor_at).toLocaleString() : 'Never';
            const atsOn = !!co.enabled && !isCustom;
            const atsLast = co.last_scanned_at ? new Date(co.last_scanned_at).toLocaleString() : 'Never';
            const platformLabel = isCustom
              ? `<span class="tag" style="background:var(--orange-soft);color:var(--orange)" title="Custom career page \u2014 no public ATS API. Use AI Monitor instead.">Custom</span>`
              : `<span class="tag t-neutral">${escapeHtml(co.platform || 'custom')}</span>`;

            return `<tr>
              <td>
                <div style="font-weight:700">${escapeHtml(co.name)}</div>
                <div style="font-size:11px;color:var(--text3);font-weight:500;margin-top:2px">
                  <a href="${escapeHtml(co.careers_url)}" target="_blank" style="color:var(--text3)">${escapeHtml(co.careers_url.length > 55 ? co.careers_url.slice(0, 55) + '\u2026' : co.careers_url)}</a>
                </div>
              </td>

              <td>
                <div style="font-size:18px;font-weight:800;line-height:1.1">${totalCount}</div>
                <div style="font-size:10px;color:var(--text3);font-weight:600;margin-top:3px;letter-spacing:.02em">
                  ATS: ${atsCount} \u00b7 AI: ${aiCount}
                </div>
              </td>

              <td>
                <div style="display:flex;align-items:center;gap:6px">
                  <button class="btn btn-sm ${aiOn ? 'btn-primary' : 'btn-ghost'}" onclick="toggleAiMonitor(${co.id}, ${!aiOn})" title="${aiOn ? 'Disable AI Monitor' : 'Enable AI Monitor'}">
                    ${aiOn ? 'On' : 'Off'}
                  </button>
                  ${aiOn ? `<button class="btn btn-ghost btn-sm" onclick="openAiMonitorModal(${co.id})" title="View query plan and run history">Details</button>` : ''}
                </div>
                <div style="font-size:10px;color:var(--text3);font-weight:500;margin-top:4px" title="Last AI Monitor scan">${aiLast}</div>
              </td>

              <td>
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                  <button class="btn btn-sm ${atsOn ? 'btn-primary' : 'btn-ghost'}" ${isCustom ? 'disabled title="No public ATS API for this company"' : `onclick="toggleCompany(${co.id}, ${!co.enabled})"`}>${atsOn ? 'On' : 'Off'}</button>
                  ${platformLabel}
                </div>
                <div style="font-size:10px;color:var(--text3);font-weight:500;margin-top:4px" title="Last ATS scan">${isCustom ? 'N/A' : atsLast}</div>
              </td>

              <td style="white-space:nowrap">
                <button class="btn btn-ghost btn-sm" onclick="scanSingleCompany(${co.id})" title="Run ATS scan now" ${isCustom ? 'disabled' : ''}>\u{1F50D}</button>
                <button class="btn btn-danger btn-sm" onclick="deleteCompany(${co.id}, '${escapeHtml(co.name).replace(/'/g, '&#39;')}')">\u{2715}</button>
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
    ${companies.some(c => (c.platform === 'custom') || !c.api_url) ? `
      <div style="margin-top:14px;padding:14px 18px;background:linear-gradient(135deg,#fef3c7,#fed7aa);border:1px solid #fcd34d;border-radius:var(--radius);font-size:12px;color:#7c2d12;font-weight:500;line-height:1.6;display:flex;gap:14px;align-items:flex-start">
        <div style="font-size:24px">\u{1F4A1}</div>
        <div style="flex:1">
          <div style="font-weight:700;font-size:13px;margin-bottom:4px;color:#78350f">Custom-platform companies won't auto-scan</div>
          <div>Companies like Amazon, Google, Microsoft, Apple, and Meta don't expose public APIs. Use these workarounds:</div>
          <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
            <button class="btn btn-ghost btn-sm" onclick="showAddModal()">\u{2795} Add Listing by URL</button>
            <button class="btn btn-primary btn-sm" onclick="showLinkedInGuide(${JSON.stringify(companies.filter(c => (c.platform === 'custom') || !c.api_url).map(c => c.name)).replace(/"/g, '&quot;')})">\u{1F4BC} LinkedIn + Gmail Setup</button>
          </div>
        </div>
      </div>` : ''}`;
  // Kick off the suggestions panel load; non-blocking
  loadCompanySuggestions();
}

async function loadCompanySuggestions(){
  const host = document.getElementById('companySuggestionsStrip');
  if (!host) return;
  try {
    const data = await window.api.scanner.getSuggestions();
    renderCompanySuggestions(data);
  } catch (err) {
    host.innerHTML = '';
  }
}

function renderCompanySuggestions(data){
  const host = document.getElementById('companySuggestionsStrip');
  if (!host) return;
  const items = data.suggestions || [];
  const collapsed = localStorage.getItem('launchpad.suggestions.collapsed') === '1';
  const cd = data.cooldown_remaining_seconds || 0;
  // Fix a target time so the countdown is stable across re-renders within the
  // same cooldown window. Persist it per-profile session so switching tabs
  // doesn't reset the clock.
  if (cd > 0) {
    window._suggCooldownEndMs = Date.now() + cd * 1000;
  } else {
    window._suggCooldownEndMs = null;
  }
  const cdText = cd > 0 ? ` \u2022 next refresh in <span id="suggCooldownClock">${_fmtCountdown(cd)}</span>` : '';
  const refreshedTxt = data.refreshed_at
    ? new Date(data.refreshed_at).toLocaleString()
    : 'never';

  if (items.length === 0 && !data.refreshed_at) {
    host.innerHTML = `
      <div style="margin:12px 0;padding:12px 16px;background:linear-gradient(135deg,var(--primary-soft),#fce7f3);border-radius:var(--radius);display:flex;align-items:center;gap:12px;font-size:13px;color:var(--text2);font-weight:500">
        <span>\u{2728} <strong style="color:var(--primary)">Get AI-curated company suggestions</strong> \u2014 half adjacent to what you track, half net-new discoveries.</span>
        <button class="btn btn-primary btn-sm" style="margin-left:auto" onclick="refreshCompanySuggestions()">\u{2728} Generate now</button>
      </div>`;
    _stopSuggCountdown();
    return;
  }

  if (items.length === 0) {
    host.innerHTML = `
      <div style="margin:12px 0;padding:10px 14px;background:var(--bg2);border-radius:var(--radius-sm);display:flex;align-items:center;gap:10px;font-size:12px;color:var(--text3);font-weight:500">
        <span>\u{2728} No suggestions right now \u2014 you've either added or dismissed them all. Last refreshed ${refreshedTxt}${cdText}</span>
        ${cd === 0 ? `<button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="refreshCompanySuggestions()">\u{21BB} Refresh</button>` : ''}
      </div>`;
    _startSuggCountdown();
    return;
  }

  const countAdjacent = items.filter(s => s.source === 'adjacent').length;
  const countDiscovery = items.length - countAdjacent;
  const chipsHTML = items.map(s => companySuggestionChipHTML(s)).join('');

  const refreshBtnTitle = cd > 0 ? `Cooldown \u2014 next refresh in ${_fmtCountdown(cd)}` : 'Refresh suggestions now';

  host.innerHTML = `
    <div id="suggestionsStripInner" style="margin:12px 0;background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow-sm)">
      <div style="padding:10px 14px;display:flex;align-items:center;gap:10px;justify-content:space-between;cursor:pointer;border-bottom:${collapsed ? 'none' : '1px solid var(--border)'}" onclick="toggleSuggestionsCollapse()">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <div style="font-size:14px;font-weight:700">\u{2728} Suggested companies</div>
          <span class="tag t-neutral">${items.length} total</span>
          <span class="tag" style="background:var(--primary-soft);color:var(--primary)">${countAdjacent} adjacent</span>
          <span class="tag" style="background:#fce7f3;color:#be185d">${countDiscovery} discovery</span>
          <span style="font-size:11px;color:var(--text3);font-weight:500">Updated ${refreshedTxt}${cdText}</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px">
          <button id="suggRefreshBtn" class="btn btn-ghost btn-sm" onclick="event.stopPropagation(); refreshCompanySuggestions()" ${cd > 0 ? 'disabled' : ''} title="${refreshBtnTitle}">
            \u{21BB} <span id="suggRefreshLabel">${cd > 0 ? _fmtCountdown(cd) : 'Refresh'}</span>
          </button>
          <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation(); toggleSuggestionsCollapse()" style="padding:4px 8px">${collapsed ? '\u25BE' : '\u25B4'}</button>
        </div>
      </div>
      <div id="suggestionsGrid" style="display:${collapsed ? 'none' : 'grid'};grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px;padding:14px">
        ${chipsHTML}
      </div>
    </div>`;

  _startSuggCountdown();
}

// ---- cooldown countdown helpers ---------------------------------------------

function _fmtCountdown(secs){
  secs = Math.max(0, Math.floor(secs));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function _stopSuggCountdown(){
  if (window._suggCountdownTimer) {
    clearInterval(window._suggCountdownTimer);
    window._suggCountdownTimer = null;
  }
}

function _startSuggCountdown(){
  _stopSuggCountdown();
  if (!window._suggCooldownEndMs) return;
  const tick = () => {
    const endMs = window._suggCooldownEndMs;
    if (!endMs) { _stopSuggCountdown(); return; }
    const remaining = Math.max(0, Math.round((endMs - Date.now()) / 1000));
    const clockEl = document.getElementById('suggCooldownClock');
    const labelEl = document.getElementById('suggRefreshLabel');
    const btn = document.getElementById('suggRefreshBtn');

    // If the strip was torn down while we were still counting, stop quietly.
    if (!clockEl && !labelEl && !btn) { _stopSuggCountdown(); return; }

    const txt = _fmtCountdown(remaining);
    if (clockEl) clockEl.textContent = txt;
    if (labelEl) labelEl.textContent = remaining > 0 ? txt : 'Refresh';
    if (btn) {
      if (remaining > 0) {
        btn.disabled = true;
        btn.title = `Cooldown \u2014 next refresh in ${txt}`;
      } else {
        btn.disabled = false;
        btn.title = 'Refresh suggestions now';
      }
    }
    if (remaining <= 0) {
      window._suggCooldownEndMs = null;
      _stopSuggCountdown();
      // Re-render the strip once more to remove the inline countdown text
      // from the header and restore the plain Refresh button label.
      loadCompanySuggestions();
    }
  };
  tick();
  window._suggCountdownTimer = setInterval(tick, 1000);
}

function companySuggestionChipHTML(s){
  const isDiscovery = s.source === 'discovery';
  const badgeColor = isDiscovery
    ? 'background:#fce7f3;color:#be185d'
    : 'background:var(--primary-soft);color:var(--primary)';
  const platformBadge = s.platform_guess
    ? `<span class="tag t-neutral" style="font-size:10px">${escapeHtml(s.platform_guess)}</span>`
    : '';
  const url = s.careers_url || '#';
  return `
    <div class="sugg-chip" data-id="${s.id}" style="border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px;background:var(--bg1);display:flex;flex-direction:column;gap:8px;transition:opacity .2s">
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <span class="tag" style="font-size:10px;${badgeColor}">${isDiscovery ? 'Discovery' : 'Adjacent'}</span>
        ${platformBadge}
      </div>
      <div style="font-weight:700;font-size:14px">${escapeHtml(s.name)}</div>
      ${s.why_relevant ? `<div style="font-size:12px;color:var(--text2);line-height:1.5">${escapeHtml(s.why_relevant)}</div>` : ''}
      ${s.careers_url ? `<a href="${escapeHtml(url)}" target="_blank" style="font-size:11px;color:var(--text3);word-break:break-all">${escapeHtml(s.careers_url.length > 50 ? s.careers_url.slice(0, 50) + '\u2026' : s.careers_url)}</a>` : ''}
      <div style="display:flex;gap:6px;margin-top:auto">
        <button class="btn btn-primary btn-sm" style="flex:1" onclick="addCompanySuggestion(${s.id}, this)">\u{2795} Add</button>
        <button class="btn btn-ghost btn-sm" onclick="dismissCompanySuggestion(${s.id}, this)" title="Don't suggest this company again">\u{2715}</button>
      </div>
    </div>`;
}

function toggleSuggestionsCollapse(){
  const grid = document.getElementById('suggestionsGrid');
  if (!grid) return;
  const isCollapsed = grid.style.display === 'none';
  localStorage.setItem('launchpad.suggestions.collapsed', isCollapsed ? '0' : '1');
  loadCompanySuggestions();
}

async function refreshCompanySuggestions(){
  const host = document.getElementById('companySuggestionsStrip');
  if (host) {
    host.innerHTML = `
      <div style="margin:12px 0;padding:14px 18px;background:var(--bg2);border-radius:var(--radius-sm);font-size:13px;color:var(--text2);font-weight:500;display:flex;align-items:center;gap:10px">
        <span>\u{23F3}</span>
        <span>Generating company suggestions \u2014 this usually takes 15-30s...</span>
      </div>`;
  }
  try {
    const data = await window.api.scanner.refreshSuggestions();
    renderCompanySuggestions(data);
  } catch (err) {
    if (host) host.innerHTML = `<div style="margin:12px 0;padding:10px 14px;background:var(--red-soft,#fee2e2);color:var(--red);border-radius:var(--radius-sm);font-size:12px">Refresh failed: ${escapeHtml(err.message)}</div>`;
  }
}

async function addCompanySuggestion(suggestionId, btn){
  const chip = btn.closest('.sugg-chip');
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '\u{23F3}';
  try {
    await window.api.scanner.addSuggestion(suggestionId);
    btn.innerHTML = '\u2705 Added';
    if (chip) {
      chip.style.opacity = '0.4';
      setTimeout(() => {
        chip.remove();
        loadCompanySuggestions();
        renderScanner(document.getElementById('content'));
      }, 600);
    }
  } catch (err) {
    btn.disabled = false;
    btn.innerHTML = original;
    alert('Failed: ' + err.message);
  }
}

async function dismissCompanySuggestion(suggestionId, btn){
  const chip = btn.closest('.sugg-chip');
  btn.disabled = true;
  try {
    await window.api.scanner.dismissSuggestion(suggestionId);
    if (chip) {
      chip.style.opacity = '0.3';
      setTimeout(() => {
        chip.remove();
        loadCompanySuggestions();
      }, 350);
    }
  } catch (err) {
    btn.disabled = false;
    alert('Failed: ' + err.message);
  }
}

async function loadDefaultCompanies(){
  try {
    const result = await window.api.scanner.loadDefaults();
    alert(`Added ${result.added} companies to your tracked list.`);
    renderScanner(document.getElementById('content'));
  } catch (err) {
    alert('Failed: ' + err.message);
  }
}

function showLinkedInGuide(companyList = []){
  const companies = Array.isArray(companyList) ? companyList : [];
  const sampleSearch = companies.length > 0
    ? companies.slice(0, 3).map(c => `"${c}"`).join(' OR ')
    : '"OpenAI" OR "Anthropic" OR "Amazon"';

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="modal" style="width:640px;max-width:95vw;max-height:90vh;overflow-y:auto">
      <div class="modal-head">
        <div class="modal-title">\u{1F4BC} Capture any company's jobs via LinkedIn + Gmail</div>
        <button class="dp-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="background:linear-gradient(135deg,#eef0ff,#fce7f3);border-radius:var(--radius-sm);padding:14px;margin-bottom:16px;font-size:13px;color:var(--text);font-weight:500;line-height:1.6">
          <strong>\u{1F4A1} The idea:</strong> LinkedIn sees jobs from every company (including ones our scanner can't reach). Set up LinkedIn Job Alerts \u2192 LinkedIn emails you matches \u2192 LaunchPad's Gmail sync auto-extracts those into your pipeline.
        </div>

        <div style="font-size:13px;font-weight:700;margin-bottom:10px;text-transform:uppercase;letter-spacing:0.5px;color:var(--text2)">Setup (3 minutes)</div>

        ${[
          {
            title: 'Open LinkedIn Jobs',
            body: `Go to <a href="https://www.linkedin.com/jobs/" target="_blank" style="color:var(--primary);font-weight:700">linkedin.com/jobs</a>`,
          },
          {
            title: 'Search for roles at specific companies',
            body: `In the search box, type your target roles. Include specific companies you want with OR syntax:<div style="background:var(--bg2);padding:8px 12px;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:12px;margin-top:8px;font-weight:500;word-break:break-all">Senior AI Engineer (${escapeHtml(sampleSearch)})</div>`,
          },
          {
            title: 'Apply location + experience filters',
            body: `Set your preferred location (Remote, US, etc.) and experience level. This makes the alerts more relevant.`,
          },
          {
            title: 'Click "Set alert" (the bell icon)',
            body: `Near the top of the results. Choose <strong>Email</strong> as the delivery method and <strong>Daily</strong> frequency. Name the alert something like "AI leadership roles".`,
          },
          {
            title: 'Create a separate alert per target archetype',
            body: `Set up 2-5 alerts for different role flavors (e.g., "VP Product AI", "Head of Applied AI", "Senior Director Product"). Each becomes a daily email \u2192 each one gets auto-ingested.`,
          },
          {
            title: 'Connect Gmail in LaunchPad',
            body: `Go to <a onclick="this.closest('.modal-overlay').remove(); showPage('gmail');" style="color:var(--primary);font-weight:700;cursor:pointer">Gmail Feed</a> and complete the one-time OAuth setup.`,
          },
          {
            title: 'Let it run',
            body: `When new LinkedIn alert emails arrive, LaunchPad classifies them and auto-extracts the (company, role, URL) tuples into your pipeline. Zero manual scraping, fully ToS-compliant.`,
          },
        ].map((s, i) => `
          <div style="padding:14px;border-radius:var(--radius-sm);background:var(--bg2);margin-bottom:8px;display:flex;gap:14px;align-items:flex-start">
            <div style="background:linear-gradient(135deg,#6366f1,#ec4899);color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:12px;flex-shrink:0">${i+1}</div>
            <div style="flex:1">
              <div style="font-size:13px;font-weight:700;margin-bottom:4px">${s.title}</div>
              <div style="font-size:12px;color:var(--text2);line-height:1.6">${s.body}</div>
            </div>
          </div>
        `).join('')}

        <div style="background:var(--green-soft);border-left:3px solid var(--green);padding:12px 14px;border-radius:6px;margin-top:16px;font-size:12px;color:var(--text);font-weight:500;line-height:1.6">
          <strong style="color:var(--green)">\u{2728} Why this works:</strong> LinkedIn has every company's listings (including ones that block scraping). Their own alert emails are completely allowed. You never scrape LinkedIn directly - you just read your own inbox.
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Close</button>
        <button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove(); showPage('gmail')">\u{1F4E7} Go to Gmail Setup</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function scanNow(){
  if (!confirm('Scan all enabled companies now? This will fetch the latest listings and add new matches to your pipeline.')) return;
  const btn = event.target;
  btn.disabled = true;
  const originalText = btn.innerHTML;
  btn.innerHTML = '\u{23F3} Scanning...';
  try {
    const result = await window.api.scanner.scanNow(false);
    showScanResultsModal(result);
    renderScanner(document.getElementById('content'));
  } catch (err) {
    alert('Scan failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
}

function showScanResultsModal(r){
  const noApiErrors = (r.errors || []).filter(e => /no ats api/i.test(e.error));
  const otherErrors = (r.errors || []).filter(e => !/no ats api/i.test(e.error));

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="modal" style="width:560px;max-width:95vw">
      <div class="modal-head">
        <div class="modal-title">\u{2728} Scan Complete</div>
        <button class="dp-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px">
          <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:800;color:var(--green)">${r.new_listings}</div>
            <div style="font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:0.5px">New Listings</div>
          </div>
          <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:800">${r.total_jobs_found}</div>
            <div style="font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:0.5px">Jobs Found</div>
          </div>
          <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:800;color:var(--text3)">${r.filtered_out}</div>
            <div style="font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:0.5px">Filtered Out</div>
          </div>
          <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:800;color:var(--text3)">${r.duplicates}</div>
            <div style="font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:0.5px">Duplicates</div>
          </div>
        </div>

        ${noApiErrors.length ? `
        <div style="background:var(--orange-soft);border:1px solid var(--orange);border-radius:var(--radius-sm);padding:14px;margin-bottom:12px">
          <div style="font-size:13px;font-weight:700;color:#7c2d12;margin-bottom:6px">
            \u{26A0} ${noApiErrors.length} compan${noApiErrors.length === 1 ? 'y' : 'ies'} couldn't be scanned (custom career page)
          </div>
          <div style="font-size:12px;color:#7c2d12;font-weight:500;margin-bottom:10px">
            ${noApiErrors.map(e => escapeHtml(e.company)).join(', ')}
          </div>
          <div style="font-size:12px;color:#7c2d12;font-weight:500;line-height:1.6">
            <strong>\u{1F4A1} Two workarounds:</strong>
            <div style="margin-top:8px;padding-left:4px">
              <div style="margin-bottom:8px">
                <strong>1. Paste URLs one at a time</strong> - when you find a role on their site, copy the URL and use the <strong>\u{2795} Add Listing</strong> button. Works on any page.
              </div>
              <div>
                <strong>2. Let Gmail + LinkedIn do the work</strong> - set up LinkedIn Job Alerts for these companies. LaunchPad's Gmail sync will pick up the emails and auto-extract listings.
                <a onclick="this.closest('.modal-overlay').remove(); showLinkedInGuide(${JSON.stringify(noApiErrors.map(e => e.company)).replace(/"/g, '&quot;')})" style="color:var(--primary);font-weight:700;cursor:pointer;text-decoration:underline;margin-left:4px">Show me how \u2192</a>
              </div>
            </div>
          </div>
        </div>` : ''}

        ${otherErrors.length ? `
        <div style="background:var(--red-soft);border:1px solid var(--red);border-radius:var(--radius-sm);padding:12px;margin-bottom:12px">
          <div style="font-size:13px;font-weight:700;color:var(--red);margin-bottom:6px">\u{2715} ${otherErrors.length} error${otherErrors.length === 1 ? '' : 's'}:</div>
          <div style="font-size:11px;color:var(--red);font-weight:500;max-height:120px;overflow-y:auto">
            ${otherErrors.map(e => `<div style="margin-bottom:4px"><strong>${escapeHtml(e.company)}:</strong> ${escapeHtml(e.error.slice(0, 200))}</div>`).join('')}
          </div>
        </div>` : ''}

        ${r.new_listings > 0 ? `
        <div style="font-size:12px;color:var(--text2);font-weight:500;text-align:center">
          Go to <strong>Pipeline Board</strong> to see your new listings \u{1F680}
        </div>` : ''}
      </div>
      <div class="modal-foot">
        ${r.new_listings > 0 ? `<button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove(); showPage('pipeline')">View Pipeline</button>` : ''}
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function scanSingleCompany(id){
  try {
    const result = await window.api.scanner.scanCompany(id);
    alert(`Found ${result.jobs_found} open roles at ${result.company}.\n\nRun "Scan All Now" to add matching jobs to your pipeline.`);
    renderScanner(document.getElementById('content'));
  } catch (err) {
    alert('Scan failed: ' + err.message);
  }
}

async function toggleCompany(id, enable){
  try {
    await window.api.scanner.updateCompany(id, { enabled: enable });
    renderScanner(document.getElementById('content'));
  } catch (err) {
    alert('Failed: ' + err.message);
  }
}

async function deleteCompany(id, name){
  if (!confirm(`Remove "${name}" from your tracked companies?`)) return;
  try {
    await window.api.scanner.deleteCompany(id);
    renderScanner(document.getElementById('content'));
  } catch (err) {
    alert('Failed: ' + err.message);
  }
}

function showAddCompanyModal(){
  const name = prompt('Company name:');
  if (!name) return;
  const url = prompt(`Careers URL for ${name}:\n\n(e.g., https://jobs.ashbyhq.com/company-slug)`);
  if (!url) return;
  window.api.scanner.createCompany({ name: name.trim(), careers_url: url.trim(), enabled: true })
    .then(() => {
      alert('Company added!');
      renderScanner(document.getElementById('content'));
    })
    .catch(err => alert('Failed: ' + err.message));
}

// ============================================================================
// AI Company Monitor — per-company query plans + web-search scans
// ============================================================================

// Shared "Track this company" quick-add used by the listing detail panel and the
// Companies research page. Given a company name + optional hint URL, resolves
// the careers URL (derive first, LLM web search fallback) and creates a
// TrackedCompany row. Optionally enables AI Monitor in the same call.
async function trackCompany({ name, hintUrl = null, enableAiMonitor = false, btn = null }) {
  if (!name || !name.trim()) {
    alert('Missing company name.');
    return null;
  }
  const original = btn ? btn.innerHTML : null;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = enableAiMonitor
      ? '\u{23F3} Setting up AI Monitor...'
      : '\u{23F3} Finding careers page...';
  }
  try {
    const res = await window.api.scanner.trackByName(name.trim(), hintUrl, enableAiMonitor);
    if (btn) {
      if (enableAiMonitor) {
        btn.innerHTML = '\u{2705} Tracked \u00b7 \u{2728} AI On';
      } else {
        btn.innerHTML = '\u{2705} Tracked';
      }
      btn.classList.remove('btn-primary');
      btn.classList.add('btn-ghost');
    }
    // If AI bootstrap ran, pop the run detail modal so the user sees what was found
    if (enableAiMonitor && res.ai_monitor_bootstrap_run_id) {
      setTimeout(() => openAiRunDetail(res.ai_monitor_bootstrap_run_id), 500);
    }
    return res;
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = original;
    }
    alert('Could not track company: ' + err.message);
    return null;
  }
}

// Render a small split-button dropdown for "Track this company"
// Use: document.getElementById(hostId).innerHTML = trackCompanyButtonHTML({...})
// Then clicks dispatch to the functions defined below.
function trackCompanyButtonHTML({ id, name, hintUrl = '', alreadyTracked = false, aiOn = false }) {
  if (alreadyTracked) {
    const label = aiOn
      ? '\u{2705} Tracked \u00b7 \u{2728} AI On'
      : '\u{2705} Tracked';
    return `<button class="btn btn-ghost btn-sm" disabled title="This company is already being tracked">${label}</button>`;
  }
  const safeName = escapeHtml(name).replace(/"/g, '&quot;');
  const safeHint = escapeHtml(hintUrl || '').replace(/"/g, '&quot;');
  return `
    <div class="track-split-btn" data-id="${id}" style="display:inline-flex;border-radius:var(--radius-sm);overflow:hidden;border:1px solid var(--primary)">
      <button class="btn btn-primary btn-sm" style="border-radius:0;border:none" onclick="trackCompanyFromBtn(this, '${safeName}', '${safeHint}', false)">\u{2795} Track company</button>
      <button class="btn btn-primary btn-sm" style="border-radius:0;border:none;border-left:1px solid rgba(255,255,255,0.3);padding:4px 8px" title="More options" onclick="toggleTrackMenu(this)">\u25BE</button>
      <div class="track-menu" style="position:absolute;display:none;background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius-sm);padding:4px;margin-top:30px;box-shadow:var(--shadow-sm);z-index:100;min-width:220px">
        <button class="btn btn-ghost btn-sm" style="width:100%;justify-content:flex-start;text-align:left;border-radius:4px" onclick="trackCompanyFromBtn(this, '${safeName}', '${safeHint}', false)">\u{2795} Track (ATS scanner only)</button>
        <button class="btn btn-ghost btn-sm" style="width:100%;justify-content:flex-start;text-align:left;border-radius:4px;margin-top:2px" onclick="trackCompanyFromBtn(this, '${safeName}', '${safeHint}', true)">\u{2728} Track + enable AI Monitor</button>
      </div>
    </div>`;
}

function toggleTrackMenu(chevronBtn) {
  const wrap = chevronBtn.closest('.track-split-btn');
  if (!wrap) return;
  const menu = wrap.querySelector('.track-menu');
  if (!menu) return;
  const open = menu.style.display !== 'none';
  // Close all other menus first
  document.querySelectorAll('.track-menu').forEach(m => m.style.display = 'none');
  menu.style.display = open ? 'none' : 'block';
  if (!open) {
    // Close on outside click
    const closer = (e) => {
      if (!wrap.contains(e.target)) {
        menu.style.display = 'none';
        document.removeEventListener('click', closer);
      }
    };
    setTimeout(() => document.addEventListener('click', closer), 0);
  }
}

// Click handler that reads name + hintUrl from button dataset and calls trackCompany().
// Used by both the inline "Track company" button and the menu items in the split dropdown.
async function trackCompanyFromBtn(btn, name, hintUrl, enableAiMonitor) {
  // Close any open track menu so the feedback state is clearly visible
  const wrap = btn.closest('.track-split-btn');
  if (wrap) {
    const menu = wrap.querySelector('.track-menu');
    if (menu) menu.style.display = 'none';
    // Disable the whole split button during the call
    wrap.style.opacity = '0.6';
  }
  const res = await trackCompany({ name, hintUrl: hintUrl || null, enableAiMonitor, btn });
  if (res && wrap) {
    // Replace the whole split button with the "Tracked" state
    const aiOn = !!(res.company && res.company.ai_monitor_enabled);
    const label = aiOn ? '\u{2705} Tracked \u00b7 \u{2728} AI On' : '\u{2705} Tracked';
    wrap.outerHTML = `<button class="btn btn-ghost btn-sm" disabled>${label}</button>`;
  } else if (wrap) {
    wrap.style.opacity = '';
  }
}

function showAiMonitorHelp(){
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="modal" style="width:560px;max-width:95vw">
      <div class="modal-head">
        <div class="modal-title">\u{2728} AI Company Monitor</div>
        <button class="dp-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <p style="font-size:13px;color:var(--text2);line-height:1.6;margin:0 0 14px">
          Many companies (Amazon, Microsoft, OpenAI, Anthropic, Nvidia, Apple) don't publish a Greenhouse/Ashby/Lever API. For those, the regular scanner can't auto-discover roles.
        </p>
        <p style="font-size:13px;color:var(--text1);font-weight:600;line-height:1.6;margin:0 0 10px">How AI Monitor fills that gap:</p>
        <ol style="font-size:13px;color:var(--text2);line-height:1.7;margin:0 0 14px;padding-left:22px">
          <li>Reads your resume + target roles + pass history</li>
          <li>Asks your LLM to generate a <strong>3-5 query plan</strong> tuned to each company, grounded in web search (so level mapping like "Director at Amazon \u2248 Staff PM at a scaleup" stays current)</li>
          <li>Runs those queries via the LLM's web search, dedupes URLs, brings everything back to LaunchPad</li>
          <li>Your existing title filter runs next — dropped listings stay visible with "Add anyway" so nothing is hidden outside LaunchPad</li>
          <li>Kept listings flow into the Pipeline and auto-evaluate like any other</li>
        </ol>
        <p style="font-size:12px;color:var(--text3);line-height:1.6;margin:0">
          Cost: one planner call per company per month, plus one search batch per scheduled scan. Typical: ~$0.05\u2013$0.20 per company per month depending on your provider.
        </p>
      </div>
      <div class="modal-foot">
        <button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove()">Got it</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function toggleAiMonitor(companyId, enable){
  // If we're disabling, just flip and move on
  if (!enable) {
    try {
      await window.api.scanner.updateCompany(companyId, { ai_monitor_enabled: false });
      renderScanner(document.getElementById('content'));
    } catch (err) {
      alert('Failed: ' + err.message);
    }
    return;
  }

  // Enabling: flip the flag, then bootstrap (generate plan + first scan).
  // Show a progress modal so the user knows something's happening.
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.innerHTML = `
    <div class="modal" style="width:480px;max-width:95vw;text-align:center">
      <div class="modal-head"><div class="modal-title">\u{2728} Setting up AI Monitor</div></div>
      <div class="modal-body" id="aiBootstrapBody">
        <div style="padding:24px 12px">
          <div style="font-size:40px;margin-bottom:14px">\u{23F3}</div>
          <div id="aiBootstrapPhase" style="font-size:14px;font-weight:600;margin-bottom:6px">Step 1 of 2 \u00b7 Generating query plan</div>
          <div style="font-size:12px;color:var(--text3)">The AI is researching the company's careers site and your resume to build 3-5 tailored searches. ~15-30 seconds.</div>
        </div>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  // Rotate the phase text after ~15s so the UI doesn't feel stuck
  const phaseTimer = setTimeout(() => {
    const el = document.getElementById('aiBootstrapPhase');
    if (el) el.textContent = 'Step 2 of 2 \u00b7 Running first scan';
  }, 15000);

  try {
    await window.api.scanner.updateCompany(companyId, { ai_monitor_enabled: true });
    const run = await window.api.scanner.aiBootstrap(companyId);
    clearTimeout(phaseTimer);
    overlay.remove();
    renderScanner(document.getElementById('content'));
    if (run.error) {
      alert('AI Monitor enabled, but the first scan errored: ' + run.error + '\nYou can retry from the Details modal.');
    } else {
      // Pop the run detail modal so they see what was found
      openAiRunDetail(run.id);
    }
  } catch (err) {
    clearTimeout(phaseTimer);
    overlay.remove();
    alert('Setup failed: ' + err.message + '\nAI Monitor flag was toggled but the first scan did not run. Open Details to retry.');
    renderScanner(document.getElementById('content'));
  }
}

async function openAiMonitorModal(companyId){
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="modal" style="width:780px;max-width:97vw;max-height:90vh;overflow-y:auto">
      <div class="modal-head">
        <div class="modal-title">\u{2728} AI Monitor Details</div>
        <button class="dp-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body" id="aiMonitorBody" data-company-id="${companyId}">
        <div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading query plan...</div></div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  renderAiMonitorBody(companyId);
}

async function renderAiMonitorBody(companyId){
  const body = document.getElementById('aiMonitorBody');
  if (!body || Number(body.dataset.companyId) !== companyId) return;
  body.innerHTML = `<div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div>`;
  let plan, runs;
  try {
    const [p, all] = await Promise.all([
      window.api.scanner.getQueryPlan(companyId),
      window.api.scanner.listAiRuns(20),
    ]);
    plan = p;
    runs = (all || []).filter(r => r.tracked_company_id === companyId).slice(0, 10);
  } catch (err) {
    body.innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
    return;
  }

  const hasPlan = plan.has_plan && (plan.queries || []).length > 0;
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:16px">
      <div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">
          <div style="font-size:15px;font-weight:700">${escapeHtml(plan.company_name)}</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn btn-ghost btn-sm" onclick="regenerateQueryPlan(${companyId})">\u{21BB} Regenerate plan</button>
            <button class="btn btn-primary btn-sm" onclick="runAiScanForCompany(${companyId})">\u{25B6} Scan now</button>
          </div>
        </div>
        <div style="font-size:12px;color:var(--text3);font-weight:500">${plan.generated_at ? 'Plan generated ' + new Date(plan.generated_at).toLocaleString() : 'No plan yet'}${plan.strategy ? ' \u00b7 strategy: ' + escapeHtml(plan.strategy) : ''}${plan.careers_site ? ' \u00b7 site: ' + escapeHtml(plan.careers_site) : ''}</div>
        ${plan.level_mapping_notes ? `<div style="margin-top:8px;padding:10px 12px;background:var(--bg2);border-radius:var(--radius-sm);font-size:12px;color:var(--text2);line-height:1.55"><strong style="color:var(--text1)">Level mapping:</strong> ${escapeHtml(plan.level_mapping_notes)}</div>` : ''}
      </div>

      <div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div style="font-size:12px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.04em">Query plan (${(plan.queries || []).length})</div>
          <div style="display:flex;gap:6px">
            <button class="btn btn-ghost btn-sm" id="aiAddQueryBtn" onclick="aiAddQueryRow(${companyId})" ${(plan.queries || []).length >= 5 ? 'disabled' : ''} title="${(plan.queries || []).length >= 5 ? 'Maximum 5 queries per plan' : 'Add a new query'}">\u{2795} Add query</button>
            <button class="btn btn-primary btn-sm" id="aiSaveQueriesBtn" style="display:none" onclick="aiSaveQueries(${companyId})">\u{1F4BE} Save changes</button>
          </div>
        </div>
        ${hasPlan ? `
          <div id="aiPlanRows" style="display:flex;flex-direction:column;gap:8px">
            ${plan.queries.map((q, i) => aiPlanRowHTML(q, i, companyId)).join('')}
          </div>
        ` : `<div class="empty-state" style="padding:16px"><div style="font-size:13px;color:var(--text3);font-weight:500">No plan yet. Click "Regenerate plan" to create one.</div></div>`}
      </div>

      <div>
        <div style="font-size:12px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px">Recent scans</div>
        ${runs.length === 0 ? `<div class="empty-state" style="padding:16px"><div style="font-size:13px;color:var(--text3);font-weight:500">No scans yet. Click "Scan now" to run the first one.</div></div>` : `
          <div style="display:flex;flex-direction:column;gap:6px">
            ${runs.map(r => `
              <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg1)">
                <div style="font-size:12px;color:var(--text2);font-weight:500">
                  ${new Date(r.started_at).toLocaleString()}
                  <span style="color:var(--text3)"> \u00b7 ${r.trigger}</span>
                  ${r.error ? `<span style="color:var(--red)"> \u00b7 error: ${escapeHtml(r.error.slice(0,60))}</span>` : ''}
                </div>
                <div style="display:flex;gap:8px;align-items:center;font-size:12px;font-weight:600">
                  <span style="color:var(--green)">${r.kept_count} kept</span>
                  <span style="color:var(--text3)">${r.filtered_count} filtered</span>
                  <span style="color:var(--text3)">${r.deduped_count} dupes</span>
                  <button class="btn btn-ghost btn-sm" onclick="openAiRunDetail(${r.id})">Details</button>
                </div>
              </div>
            `).join('')}
          </div>
        `}
      </div>
    </div>`;
}

async function regenerateQueryPlan(companyId){
  const btn = event && event.target;
  if (btn) { btn.disabled = true; btn.innerHTML = '\u{23F3} Regenerating...'; }
  try {
    await window.api.scanner.regenerateQueryPlan(companyId);
    await renderAiMonitorBody(companyId);
  } catch (err) {
    alert('Failed: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{21BB} Regenerate plan'; }
  }
}

async function runAiScanForCompany(companyId){
  const btn = event && event.target;
  if (btn) { btn.disabled = true; btn.innerHTML = '\u{23F3} Scanning...'; }
  try {
    const run = await window.api.scanner.runAiScanForCompany(companyId);
    if (run.error) { alert('Scan error: ' + run.error); }
    await renderAiMonitorBody(companyId);
    // Surface detail immediately so user sees results
    openAiRunDetail(run.id);
  } catch (err) {
    alert('Scan failed: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{25B6} Scan now'; }
  }
}

async function openAiRunDetail(runId){
  let run;
  try {
    run = await window.api.scanner.getAiRun(runId);
  } catch (err) {
    alert('Failed: ' + err.message);
    return;
  }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  const mkRow = (row, extra = '') => `
    <div style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg1);display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;font-size:13px">${escapeHtml(row.role_title || '(untitled)')}</div>
        <div style="font-size:11px;color:var(--text3);margin-top:2px">${escapeHtml(row.company || '')}${row.location ? ' \u00b7 ' + escapeHtml(row.location) : ''}</div>
        <a href="${escapeHtml(row.url || '#')}" target="_blank" style="font-size:11px;color:var(--primary);word-break:break-all">${escapeHtml((row.url || '').slice(0, 100))}</a>
        ${extra}
      </div>
    </div>`;
  overlay.innerHTML = `
    <div class="modal" style="width:820px;max-width:97vw;max-height:92vh;overflow-y:auto">
      <div class="modal-head">
        <div class="modal-title">${escapeHtml(run.company_name)} \u00b7 run #${run.id}</div>
        <button class="dp-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px">
          <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:10px;text-align:center">
            <div style="font-size:20px;font-weight:800">${run.total_found}</div>
            <div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase">Found</div>
          </div>
          <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:10px;text-align:center">
            <div style="font-size:20px;font-weight:800;color:var(--green)">${run.kept_count}</div>
            <div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase">Kept</div>
          </div>
          <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:10px;text-align:center">
            <div style="font-size:20px;font-weight:800;color:var(--text3)">${run.filtered_count}</div>
            <div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase">Filtered</div>
          </div>
          <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:10px;text-align:center">
            <div style="font-size:20px;font-weight:800;color:var(--text3)">${run.deduped_count}</div>
            <div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase">Already had</div>
          </div>
        </div>

        <div style="font-size:12px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.04em;margin:10px 0 6px">Kept \u2014 added to pipeline</div>
        ${run.kept_listings && run.kept_listings.length ? `
          <div style="display:flex;flex-direction:column;gap:6px">
            ${run.kept_listings.map(r => mkRow(r, r.promoted_from_filter_reason ? `<div style="font-size:10px;color:var(--orange);margin-top:4px">\u2705 Promoted from filter (reason was: ${escapeHtml(r.promoted_from_filter_reason)})</div>` : '')).join('')}
          </div>` : `<div style="padding:12px;color:var(--text3);font-size:12px">None.</div>`}

        <div style="font-size:12px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.04em;margin:14px 0 6px">Filtered by your title keywords</div>
        ${run.filtered_listings && run.filtered_listings.length ? `
          <div style="display:flex;flex-direction:column;gap:6px">
            ${run.filtered_listings.map((r, i) => `
              <div style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg1);display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
                <div style="flex:1;min-width:0">
                  <div style="font-weight:600;font-size:13px">${escapeHtml(r.role_title || '(untitled)')}</div>
                  <div style="font-size:11px;color:var(--text3);margin-top:2px">${escapeHtml(r.company || '')}${r.location ? ' \u00b7 ' + escapeHtml(r.location) : ''}</div>
                  <a href="${escapeHtml(r.url || '#')}" target="_blank" style="font-size:11px;color:var(--primary);word-break:break-all">${escapeHtml((r.url || '').slice(0, 100))}</a>
                  <div style="font-size:10px;color:var(--orange);margin-top:4px">Reason: ${escapeHtml(r.reason || 'unknown')}</div>
                </div>
                <button class="btn btn-primary btn-sm" onclick="promoteFiltered(${run.id}, ${i}, this)">Add anyway</button>
              </div>
            `).join('')}
          </div>` : `<div style="padding:12px;color:var(--text3);font-size:12px">None. Every returned role passed your keywords.</div>`}

        ${run.deduped_listings && run.deduped_listings.length ? `
          <div style="font-size:12px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.04em;margin:14px 0 6px">Already in your pipeline (${run.deduped_listings.length})</div>
          <div style="display:flex;flex-direction:column;gap:6px">
            ${run.deduped_listings.map(r => mkRow(r)).join('')}
          </div>` : ''}
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function promoteFiltered(runId, index, btn){
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '\u{23F3}';
  try {
    await window.api.scanner.promoteFiltered(runId, index);
    btn.innerHTML = '\u2705 Added';
    btn.classList.remove('btn-primary');
    btn.classList.add('btn-ghost');
  } catch (err) {
    btn.disabled = false;
    btn.innerHTML = original;
    alert('Failed: ' + err.message);
  }
}

// --- Query plan inline editor --------------------------------------------------

function aiPlanRowHTML(q, index, companyId){
  // Two visual states rendered from the same data. We toggle between them.
  const safeQ = escapeHtml(q.q || '');
  const safeR = escapeHtml(q.rationale || '');
  const rawQ = (q.q || '').replace(/"/g, '&quot;');
  const rawR = (q.rationale || '').replace(/"/g, '&quot;');
  return `
    <div class="ai-plan-row" data-index="${index}" data-q="${rawQ}" data-rationale="${rawR}" style="border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 12px;background:var(--bg1)">
      <div class="ai-plan-view">
        <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">
          <div style="flex:1;min-width:0">
            <div style="font-family:'SF Mono',Menlo,monospace;font-size:12px;word-break:break-all;color:var(--text1)">${safeQ}</div>
            ${safeR ? `<div style="font-size:11px;color:var(--text3);margin-top:4px">${safeR}</div>` : ''}
          </div>
          <div style="display:flex;gap:4px">
            <button class="btn btn-ghost btn-sm" onclick="aiStartEditRow(this)" title="Edit this query">\u270F\uFE0F</button>
            <button class="btn btn-ghost btn-sm" onclick="aiRemoveRow(this, ${companyId})" title="Remove this query">\u{1F5D1}</button>
          </div>
        </div>
      </div>
      <div class="ai-plan-edit" style="display:none">
        <label style="font-size:10px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.04em">Query (must include a site: operator)</label>
        <textarea class="fi ai-plan-q" style="font-family:'SF Mono',Menlo,monospace;font-size:12px;margin-top:4px;min-height:60px">${safeQ}</textarea>
        <label style="font-size:10px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.04em;display:block;margin-top:8px">Rationale (optional)</label>
        <textarea class="fi ai-plan-rationale" style="font-size:12px;margin-top:4px;min-height:48px">${safeR}</textarea>
        <div style="display:flex;gap:6px;margin-top:8px;justify-content:flex-end">
          <button class="btn btn-ghost btn-sm" onclick="aiCancelEditRow(this)">Cancel</button>
          <button class="btn btn-primary btn-sm" onclick="aiCommitEditRow(this, ${companyId})">Apply</button>
        </div>
      </div>
    </div>`;
}

function aiMarkDirty(){
  const btn = document.getElementById('aiSaveQueriesBtn');
  if (btn) btn.style.display = 'inline-block';
}

function aiStartEditRow(anchor){
  const row = anchor.closest('.ai-plan-row');
  row.querySelector('.ai-plan-view').style.display = 'none';
  row.querySelector('.ai-plan-edit').style.display = 'block';
  row.querySelector('.ai-plan-q').focus();
}

function aiCancelEditRow(anchor){
  const row = anchor.closest('.ai-plan-row');
  // Restore textareas from row's data attributes in case user typed anything
  row.querySelector('.ai-plan-q').value = row.dataset.q || '';
  row.querySelector('.ai-plan-rationale').value = row.dataset.rationale || '';
  row.querySelector('.ai-plan-view').style.display = '';
  row.querySelector('.ai-plan-edit').style.display = 'none';
}

function aiCommitEditRow(anchor, companyId){
  const row = anchor.closest('.ai-plan-row');
  const qVal = row.querySelector('.ai-plan-q').value.trim();
  const rVal = row.querySelector('.ai-plan-rationale').value.trim();
  if (!qVal) { alert('Query text cannot be empty'); return; }
  if (!/site:/i.test(qVal)) { alert('Query must include a "site:" operator'); return; }
  row.dataset.q = qVal.replace(/"/g, '&quot;');
  row.dataset.rationale = rVal.replace(/"/g, '&quot;');
  const view = row.querySelector('.ai-plan-view');
  view.querySelector(`div[style*="JetBrains Mono"], div[style*="SF Mono"]`).textContent = qVal;
  // Rationale line is the optional second div — rebuild the whole view for simplicity
  const flexCol = view.querySelector('div[style*="flex:1"]');
  if (flexCol) {
    flexCol.innerHTML = `
      <div style="font-family:'SF Mono',Menlo,monospace;font-size:12px;word-break:break-all;color:var(--text1)">${escapeHtml(qVal)}</div>
      ${rVal ? `<div style="font-size:11px;color:var(--text3);margin-top:4px">${escapeHtml(rVal)}</div>` : ''}`;
  }
  view.style.display = '';
  row.querySelector('.ai-plan-edit').style.display = 'none';
  aiMarkDirty();
}

function aiRemoveRow(anchor, companyId){
  const container = document.getElementById('aiPlanRows');
  const rows = container.querySelectorAll('.ai-plan-row');
  if (rows.length <= 1) {
    alert('A plan must keep at least one query. Use "Regenerate plan" if you want to wipe and start fresh.');
    return;
  }
  const row = anchor.closest('.ai-plan-row');
  row.remove();
  aiMarkDirty();
  // Re-enable Add button if we drop below 5
  const addBtn = document.getElementById('aiAddQueryBtn');
  if (addBtn) addBtn.disabled = container.querySelectorAll('.ai-plan-row').length >= 5;
}

function aiAddQueryRow(companyId){
  const container = document.getElementById('aiPlanRows');
  if (!container) return;
  const rows = container.querySelectorAll('.ai-plan-row');
  if (rows.length >= 5) return;
  const idx = rows.length;
  const wrapper = document.createElement('div');
  wrapper.innerHTML = aiPlanRowHTML({ q: '', rationale: '' }, idx, companyId).trim();
  const newRow = wrapper.firstElementChild;
  container.appendChild(newRow);
  // Open directly in edit mode
  newRow.querySelector('.ai-plan-view').style.display = 'none';
  newRow.querySelector('.ai-plan-edit').style.display = 'block';
  newRow.querySelector('.ai-plan-q').focus();
  aiMarkDirty();
  const addBtn = document.getElementById('aiAddQueryBtn');
  if (addBtn) addBtn.disabled = container.querySelectorAll('.ai-plan-row').length >= 5;
}

async function aiSaveQueries(companyId){
  const container = document.getElementById('aiPlanRows');
  if (!container) return;
  const rows = Array.from(container.querySelectorAll('.ai-plan-row'));
  const queries = [];
  for (const r of rows) {
    // Prefer committed (dataset) value; fall back to textarea if user hasn't clicked Apply
    const q = (r.dataset.q || (r.querySelector('.ai-plan-q')?.value || '')).replace(/&quot;/g, '"').trim();
    const rationale = (r.dataset.rationale || (r.querySelector('.ai-plan-rationale')?.value || '')).replace(/&quot;/g, '"').trim();
    if (!q) continue;
    if (!/site:/i.test(q)) { alert('All queries must include a "site:" operator. Fix:\n' + q); return; }
    queries.push({ q, rationale });
  }
  if (queries.length === 0) { alert('Need at least one query'); return; }
  const btn = document.getElementById('aiSaveQueriesBtn');
  if (btn) { btn.disabled = true; btn.textContent = '\u{23F3} Saving...'; }
  try {
    await window.api.scanner.editQueryPlan(companyId, { queries });
    await renderAiMonitorBody(companyId);
  } catch (err) {
    alert('Save failed: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{1F4BE} Save changes'; }
  }
}
async function editTitleFilter(){
  try {
    const current = await window.api.scanner.getTitleFilter();
    const positive = prompt(
      'POSITIVE keywords (comma-separated).\nA job title must match at least one of these.\n\nCurrent:',
      (current.positive || []).join(', ')
    );
    if (positive === null) return;
    const negative = prompt(
      'NEGATIVE keywords (comma-separated).\nA title matching any of these is skipped.\n\nCurrent:',
      (current.negative || []).join(', ')
    );
    if (negative === null) return;

    await window.api.scanner.updateTitleFilter({
      positive: positive.split(',').map(s => s.trim()).filter(Boolean),
      negative: negative.split(',').map(s => s.trim()).filter(Boolean),
    });
    alert('Filter updated!');
  } catch (err) {
    alert('Failed: ' + err.message);
  }
}

async function renderGmail(c){
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Gmail Feed \u{1F4E7}</div>
      <div class="page-subtitle">Scan your inbox for job alerts, recruiter messages, and application updates</div>
    </div>
    <div class="page-actions" id="gmailActions"></div>
  </div>
  <div id="gmailBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const st = await window.api.gmail.status();
    renderGmailBody(st);
  } catch (err) {
    document.getElementById('gmailBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function renderGmailBody(st){
  const body = document.getElementById('gmailBody');
  const actions = document.getElementById('gmailActions');

  if (!st.has_client_credentials){
    actions.innerHTML = '';
    body.innerHTML = `
      <div style="max-width:780px;margin:0 auto">
        <div style="background:linear-gradient(135deg,#eef0ff 0%,#fce7f3 100%);border-radius:var(--radius);padding:32px;text-align:center;border:1px solid var(--border);margin-bottom:20px">
          <div style="font-size:48px;margin-bottom:12px">\u{1F511}</div>
          <div style="font-size:22px;font-weight:800;margin-bottom:6px">Gmail Setup Required</div>
          <div style="font-size:14px;color:var(--text2);font-weight:500;margin-bottom:20px;line-height:1.6">To scan Gmail you'll need to create a free Google Cloud project and upload the OAuth credentials file. One-time setup, ~5 minutes.</div>
          <button class="btn btn-primary" onclick="showGmailSetupGuide()">\u{1F4D6} Show Setup Instructions</button>
        </div>
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px;text-align:center">
          <div style="font-size:14px;font-weight:700;margin-bottom:8px">Already have your credentials.json?</div>
          <div style="font-size:12px;color:var(--text2);font-weight:500;margin-bottom:14px">Drop it here to connect your first Gmail account.</div>
          <div class="upload-zone" style="padding:24px" onclick="document.getElementById('gmailCredsInput').click()">
            <div class="uz-icon" style="font-size:28px">\u{1F4C1}</div>
            <div class="uz-title" style="font-size:14px">Click to upload credentials.json</div>
          </div>
          <input type="file" id="gmailCredsInput" accept=".json,application/json" style="display:none" onchange="handleGmailCredentialsUpload(this.files[0])">
          <div id="gmailCredsStatus" style="margin-top:10px"></div>
        </div>
      </div>`;
    return;
  }

  const accountsCount = (st.accounts || []).length;
  const canAddMore = accountsCount < st.max_accounts;

  actions.innerHTML = `
    ${canAddMore ? `<button class="btn btn-ghost" onclick="connectGmail()">\u{2795} Connect Account</button>` : ''}
    ${accountsCount > 0 ? `<button class="btn btn-ghost" onclick="reExtractAllZero()" title="Retry extraction on emails that returned 0 listings previously">\u{1F504} Re-extract empties</button>` : ''}
    ${accountsCount > 0 ? `<button class="btn btn-primary" onclick="syncGmail()">\u{1F504} Sync All</button>` : ''}
  `;

  body.innerHTML = `
    <div style="background:linear-gradient(135deg,#dbeafe,#ccfbf1);border:1px solid var(--blue);border-radius:var(--radius);padding:14px 18px;margin-bottom:14px;display:flex;align-items:center;gap:14px">
      <div style="font-size:22px">\u{1F4E7}</div>
      <div style="flex:1">
        <div style="font-size:13px;font-weight:700">${accountsCount} of ${st.max_accounts} Gmail account${accountsCount === 1 ? '' : 's'} connected</div>
        <div style="font-size:12px;color:var(--text2);font-weight:500;margin-top:2px">${(st.accounts || []).map(a => escapeHtml(a.email)).join(' \u00b7 ') || 'None yet'}</div>
      </div>
      <button class="btn btn-ghost btn-sm" onclick="manageGmailAccounts()">\u{2699} Manage</button>
    </div>

    ${accountsCount > 0 ? gmailExtractionStatsHTML(st.accounts) : ''}

    <div id="gmailMessages">${accountsCount === 0 ? `<div class="empty-state" style="padding:40px 20px"><div class="es-icon">\u{1F4E8}</div><div class="es-title">No accounts connected yet</div><div class="es-desc">Click "Connect Account" above to authorize your first Gmail inbox.</div></div>` : `<div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading messages...</div></div>`}</div>`;

  if (accountsCount > 0) {
    loadGmailMessages();
  }
}

async function loadGmailMessages(){
  try {
    const [msgs, pending] = await Promise.all([
      window.api.gmail.listMessages(),
      window.api.gmail.pendingCount().catch(() => ({ count: 0 })),
    ]);
    const container = document.getElementById('gmailMessages');
    if (!msgs.length) {
      container.innerHTML = `<div class="empty-state" style="padding:40px 20px"><div class="es-icon">\u{1F4EC}</div><div class="es-title">No messages yet</div><div class="es-desc">Click "Sync All" to fetch your inbox.</div></div>`;
      return;
    }

    const byCategory = {};
    for (const m of msgs) {
      byCategory[m.category] = (byCategory[m.category] || 0) + 1;
    }

    const pendingBanner = pending.count > 0
      ? `<div style="background:linear-gradient(135deg,#fef3c7,#fed7aa);border:1px solid #fcd34d;border-radius:var(--radius);padding:12px 16px;margin-bottom:14px;display:flex;align-items:center;gap:12px">
          <div style="font-size:22px">\u{1F3AF}</div>
          <div style="flex:1">
            <div style="font-size:13px;font-weight:700;color:#78350f">${pending.count} unprocessed job-alert email${pending.count===1?'':'s'}</div>
            <div style="font-size:11px;color:#92400e;font-weight:500">LinkedIn/recruiter emails not yet scanned for listings. About $0.002 and 2-3s each.</div>
          </div>
          <button class="btn btn-primary btn-sm" id="extractPendingBtn" onclick="extractAllPendingEmails()">\u{2728} Extract all</button>
        </div>`
      : '';

    container.innerHTML = `
      ${pendingBanner}
      <div class="tabs">
        <div class="tab active" onclick="filterGmailCategory(event, null)">All (${msgs.length})</div>
        ${byCategory.linkedin_alert ? `<div class="tab" onclick="filterGmailCategory(event, 'linkedin_alert')">\u{1F4BC} LinkedIn (${byCategory.linkedin_alert})</div>` : ''}
        ${byCategory.recruiter ? `<div class="tab" onclick="filterGmailCategory(event, 'recruiter')">\u{1F464} Recruiters (${byCategory.recruiter})</div>` : ''}
        ${byCategory.app_update ? `<div class="tab" onclick="filterGmailCategory(event, 'app_update')">\u{2705} Updates (${byCategory.app_update})</div>` : ''}
        ${byCategory.offer ? `<div class="tab" onclick="filterGmailCategory(event, 'offer')">\u{1F4B0} Offers (${byCategory.offer})</div>` : ''}
        ${byCategory.rejection ? `<div class="tab" onclick="filterGmailCategory(event, 'rejection')">\u{274C} Rejections (${byCategory.rejection})</div>` : ''}
        ${byCategory.other ? `<div class="tab" onclick="filterGmailCategory(event, 'other')">\u{1F4E8} Other (${byCategory.other})</div>` : ''}
      </div>
      <div id="gmailList">${gmailMessagesHTML(msgs)}</div>`;
    window._allGmailMessages = msgs;
  } catch (err) {
    document.getElementById('gmailMessages').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function gmailMessagesHTML(msgs){
  if (!msgs.length) return `<div class="empty-state"><div class="es-desc">Nothing here</div></div>`;
  return msgs.map(m => gmailMessageCardHTML(m)).join('');
}

const EXPANDABLE_CATEGORIES = new Set(['linkedin_alert', 'recruiter', 'app_update', 'offer', 'rejection']);

function gmailMessageCardHTML(m){
  const iconCls = {linkedin_alert:'gi-li',recruiter:'gi-rec',app_update:'gi-alert',rejection:'gi-reject',offer:'gi-alert',other:'gi-alert'}[m.category] || 'gi-alert';
  const iconEmoji = {linkedin_alert:'\u{1F4BC}',recruiter:'\u{1F464}',app_update:'\u{2705}',rejection:'\u{274C}',offer:'\u{1F4B0}',other:'\u{1F4E8}'}[m.category] || '\u{1F4E8}';
  const when = timeAgo(new Date(m.received_at));
  const account = m.gmail_account_email ? m.gmail_account_email.split('@')[0] : '';
  const isExtractable = (m.category === 'linkedin_alert' || m.category === 'recruiter');
  const extractedCount = (m.extracted_listings || []).length;
  const filteredCount = (m.filtered_listings || []).length;
  const canExpand = EXPANDABLE_CATEGORIES.has(m.category);

  // Summary line — prefer AI summary, fall back to snippet for unextracted.
  // But: pre-reconcile emails may have a stale LLM-authored summary that says
  // "N relevant; skipped X" when our actual extraction was zero. If we detect
  // that pattern and extraction_meta is missing, suppress the stale summary
  // and nudge the user to re-extract.
  const stalePattern = /\b\d+\s+(relevant|matching|mismatched|skipped)\b/i;
  const isStaleSummary = m.ai_summary
    && !m.extraction_meta
    && stalePattern.test(m.ai_summary);
  const summaryLine = isStaleSummary
    ? `<div style="font-size:11px;color:var(--text3);font-weight:600;margin-top:4px;padding:6px 10px;background:var(--bg2);border-radius:var(--radius-sm);font-style:italic">\u{2139}\uFE0F Older extraction summary may be out of date. <a onclick="event.stopPropagation(); reExtractEmail(${m.id}, this)" style="cursor:pointer;color:var(--primary);font-weight:700;font-style:normal">Re-extract \u2192</a></div>`
    : m.ai_summary
      ? `<div style="font-size:12px;color:var(--text);font-weight:600;margin-top:4px;line-height:1.5;padding:8px 10px;background:var(--primary-soft);border-radius:var(--radius-sm);border-left:3px solid var(--primary);overflow-wrap:anywhere">\u{1F4A1} ${escapeHtml(m.ai_summary)}</div>`
      : m.snippet
        ? `<div style="font-size:11px;color:var(--text3);font-weight:500;margin-top:4px;line-height:1.4;max-height:32px;overflow:hidden">${escapeHtml(m.snippet.slice(0,200))}</div>`
        : '';

  // Meta line showing LLM vs actual counts (transparency)
  const meta = m.extraction_meta;
  const metaParts = [];
  if (meta) {
    if (typeof meta.llm_claimed === 'number') metaParts.push(`LLM found ${meta.llm_claimed}`);
    if (typeof meta.kept === 'number') metaParts.push(`kept ${meta.kept}`);
    if (meta.filtered_by_policy) metaParts.push(`${meta.filtered_by_policy} filtered by your rules`);
    if (meta.no_url) metaParts.push(`${meta.no_url} had no URL`);
    if (meta.dupes) metaParts.push(`${meta.dupes} already in pipeline`);
  }
  const metaLine = metaParts.length
    ? `<div style="font-size:10px;color:var(--text3);font-weight:600;margin-top:4px">${metaParts.join(' \u00b7 ')}</div>`
    : '';

  const listingsInfo = extractedCount
    ? `<div style="font-size:11px;color:var(--green);font-weight:700;margin-top:4px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <span>\u{2728} Extracted ${extractedCount} listing${extractedCount === 1 ? '' : 's'}${filteredCount ? ` \u00b7 ${filteredCount} filtered` : ''}</span>
        <button class="btn btn-ghost btn-sm" style="padding:2px 8px;font-size:10px" onclick="event.stopPropagation(); showPage('pipeline')">View in pipeline \u2192</button>
        ${canExpand ? `<span style="font-size:10px;color:var(--text3);font-weight:600">\u00b7 click to see details</span>` : ''}
      </div>`
    : m.processed && isExtractable && filteredCount > 0
      ? `<div style="font-size:11px;color:#b45309;font-weight:700;margin-top:4px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span>\u{26A0} All ${filteredCount} listing${filteredCount === 1 ? '' : 's'} filtered out by your rules</span>
          <span style="font-size:10px;color:var(--text3);font-weight:600">\u00b7 click to see why</span>
          <button class="btn btn-ghost btn-sm" style="padding:2px 8px;font-size:10px" onclick="event.stopPropagation(); reExtractEmail(${m.id}, this)" title="Re-run after changing your keyword filter">\u{1F504} Re-extract</button>
        </div>`
    : m.processed && isExtractable
      ? `<div style="font-size:11px;color:var(--text3);font-weight:600;margin-top:4px;display:flex;align-items:center;gap:6px">
          <span>\u{2139}\uFE0F No job listings found in this email</span>
          <button class="btn btn-ghost btn-sm" style="padding:2px 8px;font-size:10px" onclick="event.stopPropagation(); reExtractEmail(${m.id}, this)">\u{1F504} Re-extract</button>
        </div>`
      : '';
  const extractBtn = isExtractable && !m.processed
    ? `<button class="btn btn-ghost btn-sm" style="margin-top:6px;padding:4px 10px;font-size:11px" onclick="event.stopPropagation(); extractOneEmail(${m.id}, this)">\u{1F3AF} Extract listings</button>`
    : '';

  const chevron = canExpand
    ? `<div class="gmail-chevron" style="color:var(--text3);font-size:14px;transition:transform .15s">\u25B8</div>`
    : '';

  return `
    <div class="gmail-item gmail-item-wrap" data-email-id="${m.id}" data-category="${m.category}" data-expandable="${canExpand ? '1' : '0'}" style="min-width:0">
      <div class="gmail-row" ${canExpand ? `onclick="toggleGmailExpand(${m.id})"` : ''} style="cursor:${canExpand ? 'pointer' : 'default'};display:flex;align-items:flex-start;gap:12px;width:100%;min-width:0">
        <div class="gmail-icon ${iconCls}">${iconEmoji}</div>
        <div class="gmail-content" style="flex:1;min-width:0;overflow-wrap:anywhere;word-break:break-word">
          <div class="gmail-from"><span class="gmail-tag">${escapeHtml(account)}</span>${escapeHtml(m.from_name || m.from_email || 'Unknown')}</div>
          <div class="gmail-subject">${escapeHtml(m.subject || '(no subject)')}</div>
          ${summaryLine}
          ${metaLine}
          ${listingsInfo}
          ${extractBtn}
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;flex-shrink:0">
          <div class="gmail-time">${when}</div>
          ${chevron}
        </div>
      </div>
      <div class="gmail-expanded" id="gmailExp-${m.id}" style="display:none;margin-top:10px;padding-top:10px;border-top:1px dashed var(--border);max-width:100%;overflow:hidden"></div>
    </div>`;
}

async function toggleGmailExpand(emailId) {
  const wrap = document.querySelector(`.gmail-item[data-email-id="${emailId}"]`);
  if (!wrap) return;
  const exp = document.getElementById(`gmailExp-${emailId}`);
  const chev = wrap.querySelector('.gmail-chevron');
  const isOpen = exp.style.display === 'block';
  if (isOpen) {
    exp.style.display = 'none';
    if (chev) chev.style.transform = 'rotate(0deg)';
    return;
  }
  exp.style.display = 'block';
  if (chev) chev.style.transform = 'rotate(90deg)';
  if (exp.dataset.loaded === '1') return;
  exp.innerHTML = `<div style="padding:12px;color:var(--text3);font-size:12px;font-weight:500">\u{23F3} Loading email details...</div>`;
  try {
    const detail = await window.api.gmail.getMessageDetail(emailId);
    exp.innerHTML = gmailExpandedHTML(detail);
    exp.dataset.loaded = '1';
  } catch (err) {
    exp.innerHTML = `<div style="padding:12px;color:var(--red);font-size:12px;font-weight:600">Couldn't load: ${escapeHtml(err.message)}</div>`;
  }
}

function gmailExpandedHTML(d) {
  const extracted = d.extracted_listings || [];
  const filtered = d.filtered_listings || [];
  const meta = d.extraction_meta || {};

  const extractedSection = extracted.length
    ? `<div style="margin-bottom:12px">
         <div style="font-size:11px;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">\u{2728} Extracted into pipeline (${extracted.length})</div>
         <div style="display:flex;flex-direction:column;gap:4px">
           ${extracted.map(x => `
             <div style="display:flex;gap:8px;align-items:center;padding:6px 10px;background:var(--green-soft,#dcfce7);border-radius:var(--radius-sm);min-width:0">
               <div style="flex:1;min-width:0;overflow-wrap:anywhere">
                 <div style="font-size:12px;font-weight:700;color:var(--text)">${escapeHtml(x.role_title)}</div>
                 <div style="font-size:11px;color:var(--text2);font-weight:500">${escapeHtml(x.company)}${x.location ? ' \u00b7 ' + escapeHtml(x.location) : ''}</div>
               </div>
               ${x.url ? `<a href="${escapeHtml(x.url)}" target="_blank" rel="noopener" style="font-size:11px;color:var(--primary);font-weight:600;text-decoration:none;flex-shrink:0" onclick="event.stopPropagation()">Open JD \u2197</a>` : ''}
             </div>`).join('')}
         </div>
       </div>`
    : '';

  const filteredSection = filtered.length
    ? `<div style="margin-bottom:12px">
         <div style="font-size:11px;font-weight:700;color:#b45309;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">\u{26A0} Filtered out (${filtered.length})</div>
         <div style="display:flex;flex-direction:column;gap:4px">
           ${filtered.map((x, i) => `
             <div style="display:flex;gap:8px;align-items:center;padding:6px 10px;background:var(--bg2);border-radius:var(--radius-sm);opacity:.95;min-width:0;flex-wrap:wrap">
               <div style="flex:1;min-width:180px;overflow-wrap:anywhere">
                 <div style="font-size:12px;font-weight:600;color:var(--text2)">${escapeHtml(x.role_title || '')}</div>
                 <div style="font-size:11px;color:var(--text3);font-weight:500">${escapeHtml(x.company || '')} \u00b7 <span style="color:var(--red)">${escapeHtml(x.reason || 'filtered')}</span></div>
               </div>
               ${x.url ? `<button class="btn btn-ghost btn-sm" style="padding:2px 8px;font-size:10px;flex-shrink:0" onclick="event.stopPropagation(); addFilteredToPipeline(${d.id}, ${i}, this)" title="Add this listing to your pipeline even though it matched your filter">\u{2795} Add anyway</button>` : ''}
               ${x.url ? `<a href="${escapeHtml(x.url)}" target="_blank" rel="noopener" style="font-size:11px;color:var(--text3);font-weight:600;text-decoration:none;flex-shrink:0" onclick="event.stopPropagation()">Open \u2197</a>` : ''}
             </div>`).join('')}
         </div>
         <div style="font-size:10px;color:var(--text3);font-weight:500;margin-top:6px">Adjust your title filter in <a onclick="event.stopPropagation();showPage('settings')" style="cursor:pointer;color:var(--primary);font-weight:600">Settings \u2192 Portal Scanner Filter</a> if this filter is too strict.</div>
       </div>`
    : '';

  const metaSection = Object.keys(meta).length
    ? `<div style="margin-bottom:10px;padding:8px 10px;background:var(--bg2);border-radius:var(--radius-sm);font-size:11px;color:var(--text2);font-weight:600">
         \u{1F50D} LLM proposed ${meta.llm_claimed ?? '?'} listings \u00b7 kept ${meta.kept ?? 0} \u00b7 filtered by your rules ${meta.filtered_by_policy ?? 0} \u00b7 missing URL ${meta.no_url ?? 0} \u00b7 dupes ${meta.dupes ?? 0}
       </div>`
    : '';

  const body = d.body_text || '';
  const bodySection = body
    ? `<div style="min-width:0">
         <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">Full email</div>
         <div style="background:var(--bg2);border-radius:var(--radius-sm);padding:12px;font-size:12px;line-height:1.5;color:var(--text);white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;max-height:520px;overflow-y:auto;font-family:ui-sans-serif,system-ui,-apple-system,sans-serif">${renderGmailBodyWithLinks(body)}</div>
       </div>`
    : '<div style="color:var(--text3);font-size:12px;font-weight:500">Email body not available.</div>';

  return `${metaSection}${extractedSection}${filteredSection}${bodySection}`;
}

function renderGmailBodyWithLinks(text) {
  // Replace raw URLs with clickable anchor tags BEFORE HTML-escaping the rest
  // of the text. For each URL we show a shortened display (host + truncated path)
  // so long tracking URLs don't visually bloat the panel, but the full URL
  // lives in the href so clicks still work.
  const urlRe = /https?:\/\/[^\s<>"']+[^\s<>"'().,;:!?]/g;
  let out = '';
  let lastIndex = 0;
  let m;
  while ((m = urlRe.exec(text)) !== null) {
    out += escapeHtml(text.slice(lastIndex, m.index));
    const full = m[0];
    let display = full;
    try {
      const u = new URL(full);
      const path = u.pathname.length > 40 ? u.pathname.slice(0, 37) + '\u2026' : u.pathname;
      display = u.host + path;
      if (u.search && u.search.length > 20) display += '?\u2026';
    } catch {
      /* keep full URL as display on parse error */
    }
    const safeHref = escapeHtml(full);
    const safeDisplay = escapeHtml(display);
    out += `<a href="${safeHref}" target="_blank" rel="noopener" style="color:var(--primary);font-weight:600" onclick="event.stopPropagation()" title="${safeHref}">${safeDisplay}</a>`;
    lastIndex = m.index + full.length;
  }
  out += escapeHtml(text.slice(lastIndex));
  return out;
}

async function extractOneEmail(emailId, btn){
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '\u{23F3} Extracting...';
  try {
    const r = await window.api.gmail.extractFromEmail(emailId);
    const count = (r.extracted || []).length;
    if (count === 0) {
      btn.innerHTML = '\u{2139}\uFE0F No listings found';
      setTimeout(() => {
        // refresh to update the processed flag so this button disappears
        loadGmailMessages();
        updateNavBadges();
      }, 1800);
      return;
    }
    // Show success briefly, then refresh
    btn.innerHTML = `\u{2713} Added ${r.new_listings_created} of ${count}`;
    btn.style.background = 'var(--green)';
    btn.style.color = 'white';
    setTimeout(() => {
      loadGmailMessages();
      if (typeof updateNavBadges === 'function') updateNavBadges();
    }, 1500);
  } catch (err) {
    alert('Extraction failed: ' + err.message);
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

async function extractAllPendingEmails(){
  const btn = document.getElementById('extractPendingBtn');
  const original = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '\u{23F3} Processing...';
  }
  try {
    const r = await window.api.gmail.extractPending();
    const msg = `Processed ${r.processed_emails} email${r.processed_emails===1?'':'s'}.\n` +
                `Extracted ${r.total_extracted} listing${r.total_extracted===1?'':'s'}.\n` +
                `${r.total_new_listings} new listing${r.total_new_listings===1?'':'s'} added to your pipeline.` +
                (r.errors && r.errors.length ? `\n\n\u26A0 ${r.errors.length} email${r.errors.length===1?'':'s'} failed.` : '');
    alert(msg);
    loadGmailMessages();
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    alert('Extraction sweep failed: ' + err.message);
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }
}

function filterGmailCategory(evt, cat){
  document.querySelectorAll('.gmail-item').forEach(el => el.style.display = '');
  document.querySelectorAll('#gmailMessages .tab').forEach(t => t.classList.remove('active'));
  evt.currentTarget.classList.add('active');
  if (!cat) return;
  const msgs = (window._allGmailMessages || []).filter(m => m.category === cat);
  document.getElementById('gmailList').innerHTML = gmailMessagesHTML(msgs);
}

function timeAgo(date){
  const secs = Math.floor((Date.now() - date.getTime()) / 1000);
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

async function handleGmailCredentialsUpload(file){
  if (!file) return;
  const statusEl = document.getElementById('gmailCredsStatus');
  statusEl.innerHTML = `<div style="color:var(--primary);font-size:12px;font-weight:600">\u{23F3} Uploading...</div>`;
  try {
    await window.api.gmail.uploadCredentials(file);
    renderGmail(document.getElementById('content'));
  } catch (err) {
    statusEl.innerHTML = `<div style="color:var(--red);font-size:12px;font-weight:600">\u{26A0} ${escapeHtml(err.message)}</div>`;
  }
}

async function connectGmail(){
  try {
    const { auth_url } = await window.api.gmail.connect();
    const popup = window.open(auth_url, 'gmail-oauth', 'width=600,height=700');
    if (!popup) {
      // Popup blocked - just redirect
      window.location.href = auth_url;
      return;
    }
    // Listen for completion message
    window.addEventListener('message', async function onMessage(e){
      if (e.data && e.data.type === 'gmail_connected') {
        window.removeEventListener('message', onMessage);
        await new Promise(r => setTimeout(r, 500));
        renderGmail(document.getElementById('content'));
      }
    });
  } catch (err) {
    alert('Connect failed: ' + err.message);
  }
}

async function syncGmail(){
  const btn = document.querySelector('#gmailActions .btn-primary');
  if (btn) { btn.disabled = true; btn.innerHTML = '\u{23F3} Syncing (30-60s)...'; }
  try {
    const result = await window.api.gmail.syncNow();
    const totals = result.results.reduce((acc, r) => {
      acc.new += r.new; acc.extracted += r.listings_extracted;
      if (r.error) acc.errors.push(`${r.account_email}: ${r.error}`);
      return acc;
    }, {new: 0, extracted: 0, errors: []});
    let msg = `Sync complete!\n\nNew messages: ${totals.new}\nListings extracted: ${totals.extracted}`;
    if (totals.errors.length) msg += `\n\nErrors:\n${totals.errors.join('\n')}`;
    alert(msg);
    renderGmail(document.getElementById('content'));
  } catch (err) {
    alert('Sync failed: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{1F504} Sync All'; }
  }
}

async function manageGmailAccounts(){
  const st = await window.api.gmail.status();
  const list = (st.accounts || []).map(a => `${a.email}${a.last_synced_at ? ' (last synced ' + new Date(a.last_synced_at).toLocaleString() + ')' : ' (never synced)'}`).join('\n');
  const removeEmail = prompt(`Connected accounts:\n\n${list}\n\nType an email to disconnect it, or leave blank to cancel:`);
  if (!removeEmail) return;
  const account = (st.accounts || []).find(a => a.email === removeEmail.trim());
  if (!account) {
    alert('Email not found');
    return;
  }
  if (!confirm(`Disconnect ${account.email}?`)) return;
  try {
    await window.api.gmail.disconnectAccount(account.id);
    renderGmail(document.getElementById('content'));
  } catch (err) {
    alert('Failed: ' + err.message);
  }
}

function showGmailSetupGuide(){
  const body = document.getElementById('gmailBody');
  body.innerHTML = `
  <div style="max-width:780px;margin:0 auto">
    <button class="btn btn-ghost btn-sm" onclick="renderGmail(document.getElementById('content'))" style="margin-bottom:16px">\u{2190} Back</button>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:32px">
      <div style="font-size:22px;font-weight:800;margin-bottom:6px">Gmail Setup - One-Time, ~5 minutes</div>
      <div style="font-size:13px;color:var(--text2);font-weight:500;margin-bottom:24px">You'll create a free Google Cloud project so LaunchPad can connect to your Gmail.</div>

      <div style="counter-reset:step">${[
        {title: 'Create a Google Cloud project', body: `<a href="https://console.cloud.google.com/projectcreate" target="_blank" style="color:var(--primary);font-weight:600">Open Google Cloud Console \u2192</a><div style="margin-top:6px">Name it anything (e.g. "launchpad-gmail"). Click Create.</div>`},
        {title: 'Enable the Gmail API', body: `In your new project, search for "Gmail API" in the top search bar. Click it, then click <strong>Enable</strong>.`},
        {title: 'Configure the OAuth consent screen', body: `Go to <strong>APIs & Services</strong> \u2192 <strong>OAuth consent screen</strong>. Choose <strong>External</strong>, fill in app name and your email, and save. Add your own Gmail address as a "Test user" on the next page.`},
        {title: 'Create OAuth client credentials', body: `Go to <strong>APIs & Services</strong> \u2192 <strong>Credentials</strong>. Click <strong>+ CREATE CREDENTIALS</strong> \u2192 <strong>OAuth client ID</strong>. Choose <strong>Web application</strong>. Under "Authorized redirect URIs", add:<div style="background:var(--bg2);padding:8px 12px;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:12px;margin-top:6px;font-weight:500" id="redirect-uri-display">(loading...)</div>Click Create.`},
        {title: 'Download and upload credentials.json', body: `Click the download icon on your new OAuth client to get the JSON file. Come back here and upload it below.`},
      ].map((s, i) => `
        <div style="padding:16px;border-radius:var(--radius-sm);background:var(--bg2);margin-bottom:10px;display:flex;gap:14px;align-items:flex-start">
          <div style="background:linear-gradient(135deg,#6366f1,#ec4899);color:#fff;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;flex-shrink:0">${i+1}</div>
          <div>
            <div style="font-size:14px;font-weight:700;margin-bottom:6px">${s.title}</div>
            <div style="font-size:13px;color:var(--text2);line-height:1.7">${s.body}</div>
          </div>
        </div>
      `).join('')}</div>

      <div class="upload-zone" style="margin-top:20px;padding:24px" onclick="document.getElementById('gmailCredsInput2').click()">
        <div class="uz-icon" style="font-size:28px">\u{1F4C1}</div>
        <div class="uz-title" style="font-size:14px">Upload credentials.json</div>
      </div>
      <input type="file" id="gmailCredsInput2" accept=".json,application/json" style="display:none" onchange="handleGmailCredentialsUpload(this.files[0])">
      <div id="gmailCredsStatus" style="margin-top:10px"></div>
    </div>
  </div>`;

  // Show the actual redirect URI
  const redirectEl = document.getElementById('redirect-uri-display');
  if (redirectEl) {
    redirectEl.textContent = `${window.location.protocol}//${window.location.host}/api/gmail/callback`;
  }
}

async function renderReminders(c){
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Follow-ups &amp; Reminders \u{1F514}</div>
      <div class="page-subtitle">Stay on top of your applications</div>
    </div>
    <div class="page-actions">
      <button class="btn btn-primary" onclick="refreshReminders()">\u{1F504} Generate Reminders</button>
    </div>
  </div>
  <div id="remindersBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const reminders = await window.api.reminders.list();
    renderRemindersBody(reminders);
  } catch (err) {
    document.getElementById('remindersBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function renderRemindersBody(reminders){
  const body = document.getElementById('remindersBody');
  if (reminders.length === 0){
    body.innerHTML = `
      <div class="empty-state" style="padding:60px 20px">
        <div class="es-icon">\u{1F389}</div>
        <div class="es-title">All caught up!</div>
        <div class="es-desc">No pending follow-ups right now. Click "Generate Reminders" to check for new ones.</div>
      </div>`;
    return;
  }
  const byType = {followup_7d: [], interview: [], offer_deadline: []};
  for (const r of reminders) {
    (byType[r.reminder_type] || (byType[r.reminder_type] = [])).push(r);
  }
  let html = '';
  if (byType.followup_7d && byType.followup_7d.length){
    html += `<h3 style="font-size:14px;font-weight:700;margin-bottom:12px;color:var(--text2);text-transform:uppercase;letter-spacing:1px">\u{23F0} Action Needed - Follow Up</h3>`;
    html += byType.followup_7d.map(r => reminderCardHTML(r, 'followup')).join('');
  }
  if (byType.interview && byType.interview.length){
    html += `<h3 style="font-size:14px;font-weight:700;margin:20px 0 12px;color:var(--text2);text-transform:uppercase;letter-spacing:1px">\u{1F3AF} Interviews</h3>`;
    html += byType.interview.map(r => reminderCardHTML(r, 'interview')).join('');
  }
  if (byType.offer_deadline && byType.offer_deadline.length){
    html += `<h3 style="font-size:14px;font-weight:700;margin:20px 0 12px;color:var(--text2);text-transform:uppercase;letter-spacing:1px">\u{1F4B0} Offers</h3>`;
    html += byType.offer_deadline.map(r => reminderCardHTML(r, 'offer')).join('');
  }
  body.innerHTML = html;
}

function reminderCardHTML(r, flavor){
  const styles = {
    followup: {bg: 'linear-gradient(135deg,#fed7aa 0%,#fef3c7 100%)', border: '#fcd34d', titleColor: '#78350f', descColor: '#92400e', icon: '\u{23F0}'},
    interview: {bg: 'linear-gradient(135deg,#dbeafe 0%,#d1fae5 100%)', border: 'var(--blue)', titleColor: '#1e40af', descColor: '#1e3a8a', icon: '\u{1F3AF}'},
    offer: {bg: 'linear-gradient(135deg,#f3e8ff 0%,#fce7f3 100%)', border: 'var(--purple)', titleColor: '#6b21a8', descColor: '#701a75', icon: '\u{1F4B0}'},
  }[flavor];
  const action = flavor === 'offer' ? `<button class="btn btn-pink btn-sm" onclick="showPage('negotiation')">\u{1F4B0} Negotiate</button>`
    : flavor === 'interview' ? `<button class="btn btn-ghost btn-sm" onclick="showPage('interview')">\u{1F3A4} Prep</button>`
    : `<button class="btn btn-primary btn-sm" onclick="openDetail(${r.listing_id})">View</button>`;
  return `
    <div class="reminder-card" style="background:${styles.bg};border-color:${styles.border}">
      <div class="reminder-icon">${styles.icon}</div>
      <div class="reminder-content">
        <div class="reminder-title" style="color:${styles.titleColor}">${escapeHtml(r.title)}</div>
        <div class="reminder-desc" style="color:${styles.descColor}">${escapeHtml(r.description || '')}</div>
      </div>
      <div style="display:flex;gap:6px">
        ${action}
        <button class="btn btn-ghost btn-sm" onclick="dismissReminder(${r.id})" title="Dismiss">\u{2715}</button>
      </div>
    </div>`;
}

async function refreshReminders(){
  try {
    await window.api.reminders.regenerate();
    renderReminders(document.getElementById('content'));
  } catch (err) {
    alert('Failed: ' + err.message);
  }
}

async function dismissReminder(id){
  try {
    await window.api.reminders.dismiss(id);
    renderReminders(document.getElementById('content'));
  } catch (err) {
    alert('Failed: ' + err.message);
  }
}

async function renderResume(c){
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Resume Builder \u{1F4C4}</div>
      <div class="page-subtitle">Your base resume becomes the source of truth for every tailored version</div>
    </div>
  </div>
  <div id="resumeBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const [cv, generated, settings] = await Promise.all([
      window.api.resumes.getCv(),
      window.api.resumes.listGenerated(),
      window.api.settings.get(),
    ]);
    renderResumeBody(cv, generated, settings);
  } catch (err) {
    document.getElementById('resumeBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-title">Error</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function renderResumeBody(cv, generated, settings){
  const body = document.getElementById('resumeBody');
  const hasKey = settings && settings.has_llm_api_key;

  // Key-missing banner, appears when PDF upload would fail
  const keyBanner = !hasKey ? `
    <div style="background:linear-gradient(135deg,#fef3c7,#fed7aa);border:1px solid #fcd34d;border-radius:var(--radius);padding:14px 18px;margin-bottom:16px;display:flex;align-items:center;gap:12px">
      <div style="font-size:22px">\u{1F511}</div>
      <div style="flex:1">
        <div style="font-size:13px;font-weight:700;color:#78350f">LLM API key needed</div>
        <div style="font-size:12px;color:#92400e;font-weight:500">You need an LLM API key to convert PDF to markdown. Head to Settings to add one.</div>
      </div>
      <button class="btn btn-primary btn-sm" onclick="showPage('settings')">Go to Settings</button>
    </div>` : '';

  if (!cv.has_cv) {
    body.innerHTML = `
      ${keyBanner}
      <div class="upload-zone" onclick="${hasKey ? "document.getElementById('resumePdfInput').click()" : "alert('Add your LLM API key in Settings first.')"}">
        <div class="uz-icon">\u{1F4C4} \u{2192} \u{1F4DD}</div>
        <div class="uz-title">Upload Your PDF Resume</div>
        <div class="uz-desc">We'll extract the text and convert it to clean markdown with AI. Takes 20-40s.</div>
      </div>
      <input type="file" id="resumePdfInput" accept=".pdf" style="display:none" onchange="handleResumeUpload(this.files[0])">
      <div id="uploadStatus" style="margin-top:14px"></div>
      <div style="margin-top:24px;padding:20px;background:var(--bg2);border-radius:var(--radius);border:1px solid var(--border)">
        <div style="font-size:13px;font-weight:700;margin-bottom:8px">Or write cv.md from scratch</div>
        <div style="font-size:12px;color:var(--text2);margin-bottom:12px">Prefer markdown? Start with a blank template - no API key needed.</div>
        <button class="btn btn-ghost" onclick="startBlankCv()">\u{270F} Create Blank cv.md</button>
      </div>`;
    return;
  }
  const updated = cv.updated_at ? new Date(cv.updated_at).toLocaleString() : '';
  body.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <div>
            <div style="font-size:14px;font-weight:700">Your Base Resume (cv.md)</div>
            <div style="font-size:11px;color:var(--text3);font-weight:500">Last updated: ${updated}</div>
          </div>
          <div style="display:flex;gap:6px">
            <button class="btn btn-ghost btn-sm" onclick="runSmartSetup()" title="Re-run smart setup based on current resume">\u{1F9E0} Re-Analyze</button>
            <button class="btn btn-ghost btn-sm" onclick="reuploadPdf()">\u{1F4E4} Replace from PDF</button>
            <button class="btn btn-primary btn-sm" onclick="saveCvFromEditor()">\u{1F4BE} Save</button>
          </div>
        </div>
        <textarea id="cvEditor" style="width:100%;min-height:520px;padding:16px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.5;color:var(--text);resize:vertical;outline:none" spellcheck="false"></textarea>
        <input type="file" id="resumePdfInput" accept=".pdf" style="display:none" onchange="handleResumeUpload(this.files[0])">
        <div id="uploadStatus" style="margin-top:10px"></div>
      </div>
      <div>
        <div style="font-size:14px;font-weight:700;margin-bottom:10px">Tailored Versions (${generated.length})</div>
        <div id="generatedList">${renderGeneratedList(generated)}</div>
      </div>
    </div>`;
  document.getElementById('cvEditor').value = cv.markdown;
}

function renderGeneratedList(generated){
  if (generated.length === 0) {
    return `<div class="empty-state" style="padding:30px"><div class="es-icon">\u{1F4C4}</div><div class="es-desc">No tailored resumes yet.<br>Add a listing and they'll be auto-generated.</div></div>`;
  }
  return generated.map(r => {
    const date = new Date(r.created_at).toLocaleDateString();
    return `<div class="asset-card" style="margin-bottom:8px">
      <div class="asset-icon">\u{1F4C4}</div>
      <div class="asset-info">
        <div class="asset-name">${escapeHtml(r.filename)}</div>
        <div class="asset-meta">${date} \u00b7 ${r.size_kb} KB</div>
      </div>
      <div class="asset-actions">
        <a class="btn btn-primary btn-sm" href="${window.api.resumes.downloadResumeUrl(r.filename)}" download target="_blank">\u{2B07}</a>
      </div>
    </div>`;
  }).join('');
}

async function handleResumeUpload(file){
  if (!file) return;
  const statusEl = document.getElementById('uploadStatus');
  statusEl.innerHTML = `<div style="background:var(--primary-soft);border-radius:var(--radius-sm);padding:12px;font-size:13px;color:var(--primary);font-weight:600">\u{23F3} Extracting text and converting to markdown with AI (20-40s)...</div>`;
  try {
    const result = await window.api.resumes.uploadPdf(file);
    statusEl.innerHTML = '';
    showMarkdownReview(result.markdown);
  } catch (err) {
    let msg = err.message;
    let hint = '';
    if (msg.includes('401') || msg.toLowerCase().includes('authentication') || msg.toLowerCase().includes('api_key')) {
      msg = 'Your LLM API key is invalid or missing.';
      hint = '<br><br><button class="btn btn-primary btn-sm" onclick="showPage(\'settings\')">Go to Settings</button>';
    } else if (msg.includes('400') && msg.includes('No LLM API key')) {
      msg = 'No LLM API key configured.';
      hint = '<br><br><button class="btn btn-primary btn-sm" onclick="showPage(\'settings\')">Go to Settings</button>';
    }
    statusEl.innerHTML = `<div style="background:var(--red-soft);border:1px solid var(--red);border-radius:var(--radius-sm);padding:14px;font-size:13px;color:var(--red);font-weight:600">\u{26A0} ${escapeHtml(msg)}${hint}</div>`;
  }
}

function showMarkdownReview(markdown){
  const body = document.getElementById('resumeBody');
  body.innerHTML = `
    <div style="background:var(--green-soft);border:1px solid var(--green);border-radius:var(--radius-sm);padding:12px 14px;margin-bottom:16px;display:flex;align-items:center;gap:10px">
      <div style="font-size:18px">\u{2728}</div>
      <div style="flex:1">
        <div style="font-size:13px;font-weight:700;color:var(--green)">Ready for review</div>
        <div style="font-size:12px;color:var(--text2);font-weight:500">Edit if needed, then save as your base resume.</div>
      </div>
    </div>
    <div style="margin-bottom:10px;display:flex;gap:8px;justify-content:flex-end">
      <button class="btn btn-ghost" onclick="renderResume(document.getElementById('content'))">Cancel</button>
      <button class="btn btn-primary" onclick="saveCvFromEditor()">\u{1F4BE} Save as Base Resume</button>
    </div>
    <textarea id="cvEditor" style="width:100%;min-height:560px;padding:16px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.5;color:var(--text);resize:vertical;outline:none" spellcheck="false"></textarea>`;
  document.getElementById('cvEditor').value = markdown;
}

function startBlankCv(){
  const template = `# Your Full Name\n\nEmail: your@email.com\nPhone: +1-555-555-5555\nLocation: City, State\nLinkedIn: linkedin.com/in/you\n\n## Professional Summary\n\n2-4 sentences about who you are and what you do.\n\n## Core Competencies\n\n- Competency 1\n- Competency 2\n\n## Work Experience\n\n### Company Name - Role Title\n**Start Date - End Date**\n\n- Achievement bullet 1\n- Achievement bullet 2\n\n## Education\n\n### Degree, Institution\nYear\n\n## Skills\n\n**Technical:** Your tech stack\n`;
  showMarkdownReview(template);
}

function reuploadPdf(){
  document.getElementById('resumePdfInput').click();
}

async function saveCvFromEditor(){
  const markdown = document.getElementById('cvEditor').value;
  if (!markdown.trim()) { alert('Resume is empty'); return; }
  try {
    await window.api.resumes.saveCv(markdown);
    // After saving, offer smart profile setup
    showSmartSetupPrompt(markdown);
  } catch (err) {
    alert('Save failed: ' + err.message);
  }
}

function showSmartSetupPrompt(markdown){
  const c = document.getElementById('content');
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Resume Saved \u{2728}</div>
      <div class="page-subtitle">Let's tune your job search based on your experience</div>
    </div>
  </div>
  <div style="background:linear-gradient(135deg,#eef0ff 0%,#fce7f3 100%);border-radius:var(--radius);padding:32px;text-align:center;border:1px solid var(--border);max-width:720px;margin:0 auto">
    <div style="font-size:56px;margin-bottom:14px">\u{1F9E0}</div>
    <div style="font-size:22px;font-weight:800;margin-bottom:8px">Smart Profile Setup</div>
    <div style="font-size:14px;color:var(--text2);font-weight:500;margin-bottom:24px;line-height:1.6">
      Your resume is ready. Want us to analyze it and suggest personalized settings?<br>
      We'll recommend:
    </div>
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;text-align:left;margin-bottom:24px;max-width:480px;margin-left:auto;margin-right:auto">
      <div style="font-size:13px;font-weight:500;padding:8px 12px;background:var(--surface);border-radius:var(--radius-sm)">\u{1F3AF} Target roles &amp; level</div>
      <div style="font-size:13px;font-weight:500;padding:8px 12px;background:var(--surface);border-radius:var(--radius-sm)">\u{1F50D} Scanner keywords</div>
      <div style="font-size:13px;font-weight:500;padding:8px 12px;background:var(--surface);border-radius:var(--radius-sm)">\u{1F4B0} Target salary range</div>
      <div style="font-size:13px;font-weight:500;padding:8px 12px;background:var(--surface);border-radius:var(--radius-sm)">\u{2696} Scoring weights</div>
    </div>
    <div style="display:flex;gap:10px;justify-content:center">
      <button class="btn btn-ghost" onclick="skipSmartSetup()">Skip for now</button>
      <button class="btn btn-primary" onclick="runSmartSetup()" id="smartSetupBtn">\u{2728} Analyze My Resume (10-15s)</button>
    </div>
  </div>`;
}

function skipSmartSetup(){
  renderResume(document.getElementById('content'));
}

async function runSmartSetup(){
  const btn = document.getElementById('smartSetupBtn');
  btn.disabled = true;
  btn.innerHTML = '\u{23F3} Analyzing your resume...';
  try {
    const analysis = await window.api.resumes.analyze();
    showSmartSetupReview(analysis);
  } catch (err) {
    alert('Analysis failed: ' + err.message);
    btn.disabled = false;
    btn.innerHTML = '\u{2728} Analyze My Resume (10-15s)';
  }
}

function showSmartSetupReview(a){
  window._pendingSuggestions = a;
  const c = document.getElementById('content');
  const chipList = (items) => (items || []).map(i => `<span class="tag t-neutral" style="font-size:11px;padding:4px 10px">${escapeHtml(i)}</span>`).join(' ');

  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Your Personalized Setup \u{1F9E0}</div>
      <div class="page-subtitle">Review the recommendations. Uncheck anything you don't want.</div>
    </div>
  </div>

  ${a.reasoning ? `
  <div class="ai-summary" style="margin-bottom:20px">
    <div class="ai-summary-label">\u{1F4A1} AI Analysis</div>
    <div class="ai-summary-text">${escapeHtml(a.reasoning)}</div>
  </div>` : ''}

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px">
    <div class="scard" style="padding:16px">
      <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px">Career Stage</div>
      <div style="font-size:16px;font-weight:700">${escapeHtml(a.career_stage || '-')}</div>
    </div>
    <div class="scard" style="padding:16px">
      <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px">Primary Domain</div>
      <div style="font-size:16px;font-weight:700">${escapeHtml(a.primary_domain || '-')}</div>
    </div>
  </div>

  <div class="scard" style="margin-bottom:12px">
    <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
      <input type="checkbox" id="sc-target-roles" checked style="width:18px;height:18px">
      <div>
        <div style="font-size:14px;font-weight:700">\u{1F3AF} Target Roles</div>
        <div style="font-size:12px;color:var(--text2);font-weight:500">What you want to be considered for</div>
      </div>
    </label>
    <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${chipList(a.target_roles)}</div>
  </div>

  <div class="scard" style="margin-bottom:12px">
    <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
      <input type="checkbox" id="sc-positive" checked style="width:18px;height:18px">
      <div>
        <div style="font-size:14px;font-weight:700">\u{2705} Scanner Positive Keywords</div>
        <div style="font-size:12px;color:var(--text2);font-weight:500">A scanned job title must match one of these to make it in</div>
      </div>
    </label>
    <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${chipList(a.title_positive_keywords)}</div>
  </div>

  <div class="scard" style="margin-bottom:12px">
    <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
      <input type="checkbox" id="sc-negative" checked style="width:18px;height:18px">
      <div>
        <div style="font-size:14px;font-weight:700">\u{274C} Scanner Negative Keywords</div>
        <div style="font-size:12px;color:var(--text2);font-weight:500">Matching titles get filtered out</div>
      </div>
    </label>
    <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${chipList(a.title_negative_keywords)}</div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:12px">
    <div class="scard">
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
        <input type="checkbox" id="sc-salary" checked style="width:18px;height:18px">
        <div>
          <div style="font-size:14px;font-weight:700">\u{1F4B0} Target Salary</div>
          <div style="font-size:13px;color:var(--text2);font-weight:600;margin-top:4px">${escapeHtml(a.target_salary_range || 'Not specified')}</div>
        </div>
      </label>
    </div>
    <div class="scard">
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
        <input type="checkbox" id="sc-weights" checked style="width:18px;height:18px">
        <div>
          <div style="font-size:14px;font-weight:700">\u{2696} Scoring Weights</div>
          <div style="font-size:11px;color:var(--text2);font-weight:500;margin-top:4px">${Object.entries(a.scoring_weights || {}).map(([k,v]) => `${k}: ${(v*100).toFixed(0)}%`).join(' \u00b7 ')}</div>
        </div>
      </label>
    </div>
  </div>

  <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:20px">
    <button class="btn btn-ghost" onclick="renderResume(document.getElementById('content'))">Skip All</button>
    <button class="btn btn-primary" onclick="applySmartSetup()" id="applyBtn">\u{2713} Apply Selected</button>
  </div>`;
}

async function applySmartSetup(){
  const a = window._pendingSuggestions;
  if (!a) return;
  const btn = document.getElementById('applyBtn');
  btn.disabled = true;
  btn.innerHTML = '\u{23F3} Applying...';
  const payload = {};
  if (document.getElementById('sc-target-roles').checked) payload.target_roles = a.target_roles;
  if (document.getElementById('sc-positive').checked) payload.title_positive_keywords = a.title_positive_keywords;
  if (document.getElementById('sc-negative').checked) payload.title_negative_keywords = a.title_negative_keywords;
  if (document.getElementById('sc-salary').checked) payload.target_salary_range = a.target_salary_range;
  if (document.getElementById('sc-weights').checked) payload.scoring_weights = a.scoring_weights;

  try {
    const result = await window.api.resumes.applySuggestions(payload);
    const appliedList = result.applied.length ? result.applied.join(', ') : 'nothing (all unchecked)';
    const ok = document.getElementById('content');
    ok.innerHTML = `
      <div style="max-width:560px;margin:80px auto;text-align:center;background:var(--surface);border-radius:var(--radius);padding:40px;border:1px solid var(--border);box-shadow:var(--shadow-lg)">
        <div style="font-size:56px;margin-bottom:14px">\u{2728}</div>
        <div style="font-size:22px;font-weight:800;margin-bottom:8px">You're all set, ${escapeHtml((CURRENT_PROFILE.name || '').split(' ')[0])}!</div>
        <div style="font-size:14px;color:var(--text2);font-weight:500;margin-bottom:24px">Applied: ${escapeHtml(appliedList)}</div>
        <div style="display:flex;gap:10px;justify-content:center">
          <button class="btn btn-ghost" onclick="showPage('settings')">Review in Settings</button>
          <button class="btn btn-primary" onclick="showPage('dashboard')">\u{1F3E0} Go to Dashboard</button>
        </div>
      </div>`;
    delete window._pendingSuggestions;
  } catch (err) {
    alert('Failed to apply: ' + err.message);
    btn.disabled = false;
    btn.innerHTML = '\u{2713} Apply Selected';
  }
}

async function renderCover(c){
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Cover Letters \u{270D}</div>
      <div class="page-subtitle">AI-generated cover letters tailored to each role</div>
    </div>
  </div>
  <div id="coverLetterBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;
  try {
    const letters = await window.api.resumes.listCoverLetters();
    const body = document.getElementById('coverLetterBody');
    if (letters.length === 0) {
      body.innerHTML = `<div class="empty-state" style="padding:60px 20px"><div class="es-icon">\u{270D}</div><div class="es-title">No cover letters yet</div><div class="es-desc">Cover letters are auto-generated per listing. Add a job listing to get started.</div></div>`;
      return;
    }
    body.innerHTML = `
      <div style="font-size:14px;font-weight:700;margin-bottom:14px">Generated Letters (${letters.length})</div>
      ${letters.map(l => {
        const date = new Date(l.created_at).toLocaleDateString();
        return `<div class="asset-card" style="margin-bottom:8px">
          <div class="asset-icon" style="background:linear-gradient(135deg,#fce7f3,#fed7aa)">\u{270D}</div>
          <div class="asset-info">
            <div class="asset-name">${escapeHtml(l.filename)}</div>
            <div class="asset-meta">${date} \u00b7 ${l.size_kb} KB</div>
          </div>
          <div class="asset-actions">
            <a class="btn btn-primary btn-sm" href="${window.api.resumes.downloadCoverLetterUrl(l.filename)}" download target="_blank">\u{2B07}</a>
          </div>
        </div>`;
      }).join('')}`;
  } catch (err) {
    document.getElementById('coverLetterBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

async function renderInterview(c){
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Interview Prep \u{1F3A4}</div>
      <div class="page-subtitle">AI-generated STAR stories from your resume</div>
    </div>
    <div class="page-actions" id="interviewActions"></div>
  </div>
  <div id="interviewBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const data = await window.api.interview.getStories();
    renderInterviewBody(data);
  } catch (err) {
    document.getElementById('interviewBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function renderInterviewBody(data){
  const body = document.getElementById('interviewBody');
  const actions = document.getElementById('interviewActions');
  actions.innerHTML = data.has_cache
    ? `<button class="btn btn-ghost" onclick="regenerateStories()">\u{1F504} Regenerate</button>`
    : '';

  if (!data.has_cache || !data.stories || data.stories.length === 0){
    body.innerHTML = `
      <div style="background:linear-gradient(135deg,#eef0ff 0%,#fce7f3 100%);border-radius:var(--radius);padding:40px;text-align:center;border:1px solid var(--border);max-width:640px;margin:0 auto">
        <div style="font-size:48px;margin-bottom:12px">\u{2B50}</div>
        <div style="font-size:20px;font-weight:800;margin-bottom:6px">Generate Your STAR Stories</div>
        <div style="font-size:14px;color:var(--text2);font-weight:500;margin-bottom:20px;line-height:1.6">AI reads your resume and creates 5-8 polished STAR stories with Situation / Task / Action / Result / Reflection that you can use in any behavioral interview.</div>
        <button class="btn btn-primary" onclick="regenerateStories()">\u{2728} Generate Stories (20-30s)</button>
      </div>`;
    return;
  }

  body.innerHTML = `
    <div class="tabs">
      <div class="tab active">\u{2B50} STAR Stories (${data.stories.length})</div>
    </div>
    ${data.stories.map(s => starCardHTML(s)).join('')}`;
}

function starCardHTML(s){
  const tags = (s.tags || []).map(t => `<span class="tag t-remote">${escapeHtml(t)}</span>`).join(' ');
  return `
    <div class="star-card">
      <div class="star-head">
        <div class="star-title">${escapeHtml(s.title || '')}</div>
        <div class="star-tags">${tags}</div>
      </div>
      <div class="star-grid">
        <div class="star-item"><div class="star-item-label si-s">\u{1F535} Situation</div><div class="star-item-text">${escapeHtml(s.situation || '')}</div></div>
        <div class="star-item"><div class="star-item-label si-t">\u{1F7E2} Task</div><div class="star-item-text">${escapeHtml(s.task || '')}</div></div>
        <div class="star-item"><div class="star-item-label si-a">\u{1F7E0} Action</div><div class="star-item-text">${escapeHtml(s.action || '')}</div></div>
        <div class="star-item"><div class="star-item-label si-r">\u{1F7E3} Result</div><div class="star-item-text">${escapeHtml(s.result || '')}</div></div>
      </div>
      ${s.reflection ? `<div class="star-reflection"><strong>\u{1F4A1} Reflection:</strong> ${escapeHtml(s.reflection)}</div>` : ''}
    </div>`;
}

async function regenerateStories(){
  const body = document.getElementById('interviewBody');
  body.innerHTML = `<div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Generating STAR stories from your resume...<br>This takes 20-30 seconds.</div></div>`;
  try {
    const data = await window.api.interview.generate(true);
    renderInterviewBody({stories: data.stories, has_cache: true});
  } catch (err) {
    body.innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

async function renderCompanies(c){
  registerTableSortReload('companies_research', () => renderCompanies(c));
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Company Research \u{1F3E2}</div>
      <div class="page-subtitle">AI-cached intelligence on companies in your pipeline</div>
    </div>
    <div class="page-actions">
      ${companiesSortSelectorHTML()}
      <button class="btn btn-primary" onclick="showResearchCompanyModal()">\u{2728} Research Company</button>
    </div>
  </div>
  <div id="companiesBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const companies = await window.api.companies.list();
    renderCompaniesBody(companies);
  } catch (err) {
    document.getElementById('companiesBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function companiesSortSelectorHTML() {
  const st = getSortState('companies_research') || { field: 'refreshed_at', dir: 'desc' };
  const opts = [
    { field: 'name', dir: 'asc', label: 'Name (A\u2192Z)', dataType: 'string' },
    { field: 'name', dir: 'desc', label: 'Name (Z\u2192A)', dataType: 'string' },
    { field: 'refreshed_at', dir: 'desc', label: 'Recently refreshed', dataType: 'date' },
    { field: 'refreshed_at', dir: 'asc', label: 'Oldest refresh', dataType: 'date' },
  ];
  const selected = `${st.field}|${st.dir}`;
  return `<select class="fi" style="width:auto;font-size:12px;padding:6px 10px;font-weight:600" onchange="selectCompaniesSort(this.value)">
    ${opts.map(o => {
      const val = `${o.field}|${o.dir}|${o.dataType}`;
      const sel = `${o.field}|${o.dir}` === selected ? 'selected' : '';
      return `<option value="${val}" ${sel}>\u2195 ${o.label}</option>`;
    }).join('')}
  </select>`;
}

function selectCompaniesSort(val) {
  const [field, dir, dataType] = val.split('|');
  saveSortState('companies_research', { field, dir, dataType });
  const reload = (window._tableSortReloaders || {})['companies_research'];
  if (reload) reload();
}

function renderCompaniesBody(companies){
  const body = document.getElementById('companiesBody');
  if (companies.length === 0){
    body.innerHTML = `
      <div class="empty-state" style="padding:60px 20px">
        <div class="es-icon">\u{1F3E2}</div>
        <div class="es-title">No companies researched yet</div>
        <div class="es-desc">Click "Research Company" to get an AI-generated brief on any company.<br>Saves money by caching research you can reuse across listings.</div>
        <button class="btn btn-primary" style="margin-top:14px" onclick="showResearchCompanyModal()">\u{2728} Research First Company</button>
      </div>`;
    return;
  }
  const sorted = applyTableSort(companies, 'companies_research', 'refreshed_at', 'desc', 'date');
  body.innerHTML = sorted.map(c => companyCardHTML(c)).join('');
  // Populate Track-this-company buttons asynchronously (one lookup per visible card)
  populateCompaniesTrackButtons(sorted).catch(() => {});
}

async function populateCompaniesTrackButtons(companies) {
  let tracked = [];
  try {
    tracked = await window.api.scanner.listCompanies();
  } catch (err) {
    return; // Silent — tracking is optional, research still works
  }
  const trackedByName = new Map(
    (tracked || []).map(t => [(t.name || '').toLowerCase(), t])
  );
  for (const c of companies) {
    const host = document.getElementById(`cp-track-${c.id}`);
    if (!host) continue;
    const match = trackedByName.get((c.name || '').toLowerCase());
    if (match) {
      const aiOn = !!match.ai_monitor_enabled;
      const label = aiOn ? '\u{2705} Tracked \u00b7 \u{2728} AI On' : '\u{2705} Tracked';
      host.innerHTML = `<button class="btn btn-ghost btn-sm" disabled title="Being tracked by the scanner">${label}</button>`;
    } else {
      // Use the cached careers_url as hint if available (saves an LLM call)
      const hintUrl = c.careers_url || (c.research_data && c.research_data.careers_url) || '';
      host.innerHTML = trackCompanyButtonHTML({
        id: `cp-${c.id}`,
        name: c.name,
        hintUrl,
        alreadyTracked: false,
      });
    }
  }
}

function companyCardHTML(c){
  const initials = c.name.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  const data = c.research_data || {};
  const highlights = Array.isArray(data.recent_highlights) ? data.recent_highlights : [];
  const risks = Array.isArray(data.concerns_or_risks) ? data.concerns_or_risks : [];
  const sources = Array.isArray(data.sources) ? data.sources : [];
  const asOf = data.as_of_date || new Date(c.refreshed_at).toLocaleDateString();
  const confidence = data.confidence || null;
  const confColor = confidence === 'high' ? 'var(--green)' : confidence === 'medium' ? '#a16207' : confidence === 'low' ? 'var(--red)' : 'var(--text3)';

  return `
    <div class="company-card" style="display:block;padding:24px">
      <div style="display:flex;gap:16px;align-items:flex-start;margin-bottom:16px">
        <div class="company-logo">${initials}</div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;justify-content:space-between;align-items:start;gap:12px">
            <div>
              <div class="company-name">${escapeHtml(c.name)}</div>
              <div class="company-desc" style="margin-top:4px">${escapeHtml(c.description || 'No description')}</div>
              <div style="margin-top:6px;display:flex;gap:10px;align-items:center;font-size:11px;color:var(--text3);font-weight:600">
                <span>\u{1F50E} Researched: ${escapeHtml(asOf)}</span>
                ${confidence ? `<span>&middot;</span><span>Confidence: <span style="color:${confColor};text-transform:uppercase;font-weight:700">${escapeHtml(confidence)}</span></span>` : ''}
                ${sources.length ? `<span>&middot;</span><span>${sources.length} source${sources.length === 1 ? '' : 's'}</span>` : ''}
              </div>
            </div>
            <div style="display:flex;gap:6px;flex-shrink:0">
              <span id="cp-track-${c.id}"></span>
              <button class="btn btn-ghost btn-sm" onclick="refreshCompany(${c.id})">\u{1F504} Refresh</button>
              <button class="btn btn-danger btn-sm" onclick="deleteCompanyResearch(${c.id}, '${escapeHtml(c.name).replace(/'/g, '&#39;')}')">\u{2715}</button>
            </div>
          </div>
          <div class="company-metrics">
            <div class="company-metric"><div class="company-metric-label">Valuation</div><div class="company-metric-value" style="color:var(--green)">${escapeHtml(c.valuation || 'Unknown')}</div></div>
            <div class="company-metric"><div class="company-metric-label">Employees</div><div class="company-metric-value">${escapeHtml(c.employee_count || 'Unknown')}</div></div>
            ${c.glassdoor_rating ? `<div class="company-metric"><div class="company-metric-label">Glassdoor</div><div class="company-metric-value" style="color:var(--yellow)">\u{2B50} ${c.glassdoor_rating.toFixed(1)}</div></div>` : ''}
            ${c.tech_stack ? `<div class="company-metric"><div class="company-metric-label">Stack</div><div class="company-metric-value" style="font-size:12px">${escapeHtml(c.tech_stack.slice(0,50))}</div></div>` : ''}
          </div>
        </div>
      </div>

      ${highlights.length ? `
        <div style="background:var(--green-soft);border-radius:var(--radius-sm);padding:12px 14px;margin-bottom:8px">
          <div style="font-size:11px;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">\u{1F525} Recent Highlights</div>
          <ul style="margin:0;padding-left:18px;font-size:12px;color:var(--text);font-weight:500;line-height:1.6">
            ${highlights.map(h => `<li>${escapeHtml(h)}</li>`).join('')}
          </ul>
        </div>` : ''}

      ${data.culture_signals ? `
        <div style="background:var(--blue-soft);border-radius:var(--radius-sm);padding:12px 14px;margin-bottom:8px">
          <div style="font-size:11px;font-weight:700;color:var(--blue);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">\u{1F3E1} Culture</div>
          <div style="font-size:12px;color:var(--text);font-weight:500;line-height:1.6">${escapeHtml(data.culture_signals)}</div>
        </div>` : ''}

      ${risks.length ? `
        <div style="background:var(--red-soft);border-radius:var(--radius-sm);padding:12px 14px;margin-bottom:8px">
          <div style="font-size:11px;font-weight:700;color:var(--red);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">\u{26A0} Concerns</div>
          <ul style="margin:0;padding-left:18px;font-size:12px;color:var(--text);font-weight:500;line-height:1.6">
            ${risks.map(r => `<li>${escapeHtml(r)}</li>`).join('')}
          </ul>
        </div>` : ''}

      ${data.leadership ? `
        <div style="font-size:12px;color:var(--text2);font-weight:500;margin-top:8px">
          <strong style="color:var(--text)">Leadership:</strong> ${escapeHtml(data.leadership)}
        </div>` : ''}

      ${data.good_fit_for ? `
        <div style="font-size:12px;color:var(--text2);font-weight:500;font-style:italic;margin-top:8px">
          <strong>Good fit for:</strong> ${escapeHtml(data.good_fit_for)}
        </div>` : ''}

      ${sources.length ? `
        <div style="margin-top:14px;padding-top:12px;border-top:1px dashed var(--border)">
          <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">\u{1F4CE} Sources (${sources.length})</div>
          <div style="display:flex;flex-wrap:wrap;gap:6px">
            ${sources.slice(0,8).map((s,i) => `
              <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener" style="font-size:11px;padding:4px 10px;background:var(--bg2);border:1px solid var(--border);border-radius:12px;color:var(--primary);font-weight:600;text-decoration:none">
                [${i+1}] ${escapeHtml((s.title || s.url).slice(0, 60))}
              </a>
            `).join('')}
          </div>
        </div>` : ''}
    </div>`;
}

function showResearchCompanyModal(){
  const name = prompt('Company name:');
  if (!name) return;
  const url = prompt(`Careers URL for ${name} (optional):`);
  doResearchCompany(name.trim(), (url || '').trim() || null);
}

async function doResearchCompany(name, careers_url){
  const body = document.getElementById('companiesBody');
  body.innerHTML = `
    <div style="max-width:480px;margin:40px auto;text-align:center;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:32px">
      <div style="font-size:44px;margin-bottom:10px">\u{1F50E}</div>
      <div style="font-size:16px;font-weight:700;margin-bottom:6px">Researching ${escapeHtml(name)}</div>
      <div style="font-size:13px;color:var(--text2);font-weight:500">Searching the web for funding, leadership, recent news, Glassdoor...<br>15-30 seconds.</div>
      <div style="margin-top:20px;height:3px;background:var(--bg2);border-radius:3px;overflow:hidden">
        <div style="height:100%;width:40%;background:linear-gradient(90deg,transparent,var(--primary),transparent);animation:progress-slide 1.5s ease-in-out infinite"></div>
      </div>
    </div>`;
  try {
    await window.api.companies.research(name, careers_url);
    renderCompanies(document.getElementById('content'));
  } catch (err) {
    alert('Research failed: ' + err.message);
    renderCompanies(document.getElementById('content'));
  }
}

async function refreshCompany(id){
  // Find the specific card and overlay it with a loading indicator
  const card = document.querySelector(`.company-card button[onclick*="refreshCompany(${id})"]`)?.closest('.company-card');
  if (card) {
    card.style.position = 'relative';
    const overlay = document.createElement('div');
    overlay.className = 'refresh-overlay';
    overlay.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.85);border-radius:var(--radius);z-index:10;flex-direction:column;gap:10px;backdrop-filter:blur(2px)';
    overlay.innerHTML = `
      <div style="font-size:28px">\u{1F50E}</div>
      <div style="font-size:14px;font-weight:700;color:var(--primary)">Refreshing with web search\u2026</div>
      <div style="font-size:11px;color:var(--text2);font-weight:500">15-30 seconds</div>
      <div style="width:180px;height:3px;background:var(--bg2);border-radius:3px;overflow:hidden;margin-top:4px">
        <div style="height:100%;width:40%;background:linear-gradient(90deg,transparent,var(--primary),transparent);animation:progress-slide 1.5s ease-in-out infinite"></div>
      </div>`;
    card.appendChild(overlay);
  }
  try {
    await window.api.companies.refresh(id);
    renderCompanies(document.getElementById('content'));
  } catch (err) {
    alert('Refresh failed: ' + err.message);
    renderCompanies(document.getElementById('content'));
  }
}

async function deleteCompanyResearch(id, name){
  if (!confirm(`Remove research for ${name}?`)) return;
  try {
    await window.api.companies.delete(id);
    renderCompanies(document.getElementById('content'));
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

async function renderNegotiation(c){
  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Negotiation Helper \u{1F4B0}</div>
      <div class="page-subtitle">AI-generated counter-offer scripts for offer-stage listings</div>
    </div>
  </div>
  <div id="negotiationBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;

  try {
    const offers = await window.api.listings.list({ status: 'offer', limit: 50 });
    renderNegotiationBody(offers);
  } catch (err) {
    document.getElementById('negotiationBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

function renderNegotiationBody(offers){
  const body = document.getElementById('negotiationBody');
  if (offers.length === 0){
    body.innerHTML = `
      <div class="empty-state" style="padding:60px 20px">
        <div class="es-icon">\u{1F389}</div>
        <div class="es-title">No offers yet</div>
        <div class="es-desc">When you mark a listing as "Offer" status, it will appear here with a counter-offer generator.</div>
      </div>`;
    return;
  }
  body.innerHTML = `
    <div style="font-size:14px;font-weight:700;margin-bottom:14px;text-transform:uppercase;letter-spacing:0.8px;color:var(--text2)">Active Offers (${offers.length})</div>
    ${offers.map(o => `
      <div class="offer-card">
        <div class="offer-header">
          <div>
            <div class="offer-title">\u{1F389} ${escapeHtml(o.company)} - ${escapeHtml(o.role_title)}</div>
            <div style="font-size:13px;color:#78350f;font-weight:500;margin-top:4px">${escapeHtml(o.location || '')} \u00b7 ${escapeHtml(o.job_type || '')}</div>
          </div>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="btn btn-primary" onclick="openNegotiationModal(${o.id}, '${escapeHtml(o.company).replace(/'/g, '&#39;')}')">\u{1F4B0} Generate Counter-Offer</button>
        </div>
      </div>
    `).join('')}`;
}

function openNegotiationModal(listingId, companyName){
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="modal" style="width:580px;max-width:95vw">
      <div class="modal-head">
        <div class="modal-title">\u{1F4B0} Counter-Offer for ${escapeHtml(companyName)}</div>
        <button class="dp-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div class="fg"><label class="fl">Base Salary Offered *</label><input class="fi" id="neg-base" placeholder="e.g., $175,000"></div>
        <div class="fg"><label class="fl">Equity Offered</label><input class="fi" id="neg-equity" placeholder="e.g., 0.1% / $200K RSUs over 4 years"></div>
        <div class="fg"><label class="fl">Other (bonus, signing)</label><input class="fi" id="neg-other" placeholder="e.g., $30K signing bonus"></div>
        <div class="fg"><label class="fl">Response Deadline</label><input class="fi" id="neg-deadline" placeholder="e.g., 2026-05-03"></div>
        <div class="fg"><label class="fl">Competing Offers (optional)</label><input class="fi" id="neg-competing" placeholder="e.g., Company X offered $200K base + $80K RSUs"></div>
        <div class="fg"><label class="fl">Notes (optional)</label><textarea class="fi" id="neg-notes" rows="2" placeholder="Anything else relevant"></textarea></div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn btn-primary" onclick="generateCounterOffer(${listingId})" id="neg-gen-btn">\u{2728} Generate Counter-Offer</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('neg-base').focus(), 100);
}

async function generateCounterOffer(listingId){
  const base = document.getElementById('neg-base').value.trim();
  if (!base) { alert('Base salary is required'); return; }
  const btn = document.getElementById('neg-gen-btn');
  btn.disabled = true; btn.innerHTML = '\u{23F3} Generating (20-30s)...';
  const offer = {
    base_salary: base,
    equity: document.getElementById('neg-equity').value.trim() || null,
    other: document.getElementById('neg-other').value.trim() || null,
    deadline: document.getElementById('neg-deadline').value.trim() || null,
    competing_offers: document.getElementById('neg-competing').value.trim() || null,
    notes: document.getElementById('neg-notes').value.trim() || null,
  };
  try {
    const result = await window.api.negotiation.counter(listingId, offer);
    // Replace modal contents with result
    const modalBody = document.querySelector('.modal-overlay.open .modal-body');
    const modalFoot = document.querySelector('.modal-overlay.open .modal-foot');
    modalBody.innerHTML = renderCounterResult(result);
    modalFoot.innerHTML = `
      <button class="btn btn-ghost" onclick="copyCounterEmail()">\u{1F4CB} Copy Email</button>
      <button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove()">Done</button>`;
    window._lastCounterOffer = result;
  } catch (err) {
    btn.disabled = false; btn.innerHTML = '\u{2728} Generate Counter-Offer';
    alert('Failed: ' + err.message);
  }
}

function renderCounterResult(r){
  const rec = r.recommended_counter || {};
  const talkingPoints = Array.isArray(r.talking_points) ? r.talking_points : [];
  return `
    <div class="ai-summary" style="margin-bottom:16px">
      <div class="ai-summary-label">\u{1F4A1} AI Analysis</div>
      <div class="ai-summary-text">${escapeHtml(r.analysis || '')}</div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px">
      <div class="score-item">
        <div class="score-item-label">Counter Base</div>
        <div class="score-item-value" style="color:var(--green);font-size:16px">${escapeHtml(rec.base_ask || '?')}</div>
      </div>
      <div class="score-item">
        <div class="score-item-label">Walk-Away Point</div>
        <div class="score-item-value" style="color:var(--red);font-size:16px">${escapeHtml(r.walk_away_point || '?')}</div>
      </div>
    </div>

    ${rec.rationale ? `<div style="font-size:12px;color:var(--text2);font-weight:500;margin-bottom:16px;font-style:italic">${escapeHtml(rec.rationale)}</div>` : ''}

    <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Email Subject</div>
    <div style="background:var(--bg2);padding:10px 14px;border-radius:var(--radius-sm);font-size:13px;font-weight:600;margin-bottom:12px" id="counter-subject">${escapeHtml(r.email_subject || '')}</div>

    <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Email Body</div>
    <textarea style="width:100%;min-height:220px;padding:12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius-sm);font-family:'Plus Jakarta Sans',sans-serif;font-size:13px;line-height:1.6;color:var(--text);resize:vertical" id="counter-body">${escapeHtml(r.email_body || '')}</textarea>

    ${talkingPoints.length ? `
      <div style="margin-top:16px">
        <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:8px">\u{1F4AC} If They Push Back</div>
        <ul style="margin:0;padding-left:20px;font-size:12px;color:var(--text);font-weight:500;line-height:1.7">
          ${talkingPoints.map(p => `<li>${escapeHtml(p)}</li>`).join('')}
        </ul>
      </div>` : ''}`;
}

function copyCounterEmail(){
  const subject = document.getElementById('counter-subject')?.textContent || '';
  const body = document.getElementById('counter-body')?.value || '';
  const text = `Subject: ${subject}\n\n${body}`;
  navigator.clipboard.writeText(text).then(() => {
    alert('Email copied to clipboard');
  }).catch(() => {
    alert('Copy failed. Select the text manually.');
  });
}

async function renderSettings(c){
  c.innerHTML = `
    <div class="page-header">
      <div>
        <div class="page-title">Settings \u{2699}</div>
        <div class="page-subtitle">Loading...</div>
      </div>
    </div>
    <div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Fetching your settings...</div></div>`;

  let s, models, titleFilter;
  try {
    [s, models, titleFilter] = await Promise.all([
      window.api.settings.get(),
      window.api.settings.models(),
      window.api.scanner.getTitleFilter(),
    ]);
  } catch (err) {
    c.innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-title">Failed to load settings</div><div class="es-desc">${err.message}</div></div>`;
    return;
  }

  const pd = s.profile_data || {};
  const targetRoles = Array.isArray(pd.target_roles) ? pd.target_roles.join(', ') : (pd.target_roles || '');
  const modelOpts = (provider) => (models[provider] || []).map(m =>
    `<option value="${m.id}" ${m.id===s.llm_model?'selected':''}>${m.name} - ${m.description}</option>`
  ).join('');

  c.innerHTML = `
  <div class="page-header">
    <div>
      <div class="page-title">Settings \u{2699}</div>
      <div class="page-subtitle">Configure your LaunchPad instance</div>
    </div>
    <div class="page-actions">
      <span id="saveStatus" style="font-size:12px;color:var(--text3);font-weight:500"></span>
      <button class="btn btn-primary" onclick="saveSettings()">\u{1F4BE} Save All</button>
    </div>
  </div>
  <div class="settings-grid">

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-llm">\u{1F916}</div>
        <div><div class="scard-title">LLM Provider</div><div class="scard-desc">Choose which AI powers evaluations and resume tailoring</div></div>
      </div>
      <div class="fg">
        <label class="fl">Provider</label>
        <select class="fi" id="set-llm-provider" onchange="onProviderChange()">
          <option value="anthropic" ${s.llm_provider==='anthropic'?'selected':''}>Anthropic (Claude)</option>
          <option value="openai" ${s.llm_provider==='openai'?'selected':''}>OpenAI (GPT)</option>
          <option value="gemini" ${s.llm_provider==='gemini'?'selected':''}>Google (Gemini)</option>
        </select>
      </div>
      <div class="fg">
        <label class="fl">API Key</label>
        <input class="fi" id="set-llm-key" type="password" placeholder="${s.has_llm_api_key?'Saved (enter new to change)':'Paste your API key'}">
      </div>
      <div class="fg">
        <label class="fl">Model</label>
        <select class="fi" id="set-llm-model">${modelOpts(s.llm_provider)}</select>
      </div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn btn-ghost btn-sm" onclick="testLLMConnection()">\u{26A1} Test Connection</button>
        <div id="testResult" style="flex:1;display:flex;align-items:center;font-size:12px;font-weight:600"></div>
      </div>
    </div>

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-profile">\u{1F464}</div>
        <div><div class="scard-title">Your Profile</div><div class="scard-desc">Personal details used across all evaluations</div></div>
      </div>
      <div class="fg"><label class="fl">Full Name</label><input class="fi" id="set-name" value="${escapeHtml(s.name || '')}"></div>
      <div class="fg"><label class="fl">Role Title</label><input class="fi" id="set-role" value="${escapeHtml(s.role_title || '')}"></div>
      <div class="fg"><label class="fl">Email</label><input class="fi" id="set-email" value="${escapeHtml(pd.email || '')}"></div>
      <div class="fg"><label class="fl">Phone</label><input class="fi" id="set-phone" value="${escapeHtml(pd.phone || '')}"></div>
      <div class="fg"><label class="fl">LinkedIn</label><input class="fi" id="set-linkedin" value="${escapeHtml(pd.linkedin || '')}"></div>
      <div class="fg"><label class="fl">Location</label><input class="fi" id="set-location" value="${escapeHtml(pd.location || '')}"></div>
      <div class="fg"><label class="fl">Target Roles (comma-separated)</label><input class="fi" id="set-target-roles" value="${escapeHtml(targetRoles)}"></div>
      <div class="fg"><label class="fl">Target Salary</label><input class="fi" id="set-target-salary" value="${escapeHtml(pd.target_salary || '')}"></div>
    </div>

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-submit">\u{1F680}</div>
        <div><div class="scard-title">Application Submission</div><div class="scard-desc">Auto-submission behavior and safety rules</div></div>
      </div>
      <div class="toggle-row">
        <div class="toggle-info"><div class="toggle-label">Require approval before submit</div><div class="toggle-sub">Always show review panel before submitting</div></div>
        <button class="toggle ${s.require_approval?'on':''}" id="set-require-approval" onclick="this.classList.toggle('on')"></button>
      </div>
      <div class="toggle-row">
        <div class="toggle-info"><div class="toggle-label">Auto-evaluate new listings</div><div class="toggle-sub">Run AI evaluation automatically when a listing is added</div></div>
        <button class="toggle ${s.auto_evaluate?'on':''}" id="set-auto-evaluate" onclick="this.classList.toggle('on')"></button>
      </div>
      <div class="toggle-row">
        <div class="toggle-info"><div class="toggle-label">Auto-generate resume &amp; cover letter</div><div class="toggle-sub">After evaluation, tailor assets automatically</div></div>
        <button class="toggle ${s.auto_generate_assets?'on':''}" id="set-auto-generate" onclick="this.classList.toggle('on')"></button>
      </div>
      <div class="toggle-row">
        <div class="toggle-info"><div class="toggle-label">Web-grounded evaluations</div><div class="toggle-sub">Use LLM web search for current funding, headcount, news. Slightly higher cost and latency, better quality.</div></div>
        <button class="toggle ${s.web_grounded_eval?'on':''}" id="set-web-grounded" onclick="this.classList.toggle('on')"></button>
      </div>
      <div class="fg" style="margin-top:10px">
        <label class="fl">Min Score to Submit</label>
        <select class="fi" id="set-min-score">
          <option value="4.5" ${s.min_submit_score===4.5?'selected':''}>4.5+ (Grade A+ only)</option>
          <option value="4.0" ${s.min_submit_score===4.0?'selected':''}>4.0+ (Grade A only, recommended)</option>
          <option value="3.5" ${s.min_submit_score===3.5?'selected':''}>3.5+ (Grade B+ and above)</option>
          <option value="3.0" ${s.min_submit_score===3.0?'selected':''}>3.0+ (Grade B and above)</option>
          <option value="0" ${s.min_submit_score===0?'selected':''}>No minimum</option>
        </select>
      </div>
    </div>

    <div class="scard" style="grid-column: span 2">
      <div class="scard-header">
        <div class="scard-icon sci-score">\u{2696}\uFE0F</div>
        <div>
          <div class="scard-title">Scoring Weights</div>
          <div class="scard-desc">How each dimension contributes to the overall score. Must sum to 100%. Changes apply to future evaluations \u2014 existing listings keep their current scores until re-evaluated.</div>
        </div>
      </div>
      <div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap">
        <button class="btn btn-ghost btn-sm" onclick="applyWeightPreset('equal')">\u{2696}\uFE0F Equal (12.5% each)</button>
        <button class="btn btn-ghost btn-sm" onclick="applyWeightPreset('startup')">\u{1F680} Startup focus</button>
        <button class="btn btn-ghost btn-sm" onclick="applyWeightPreset('comp')">\u{1F4B0} Comp maximizer</button>
        <button class="btn btn-ghost btn-sm" onclick="applyWeightPreset('lateral')">\u{2194}\uFE0F Lateral move</button>
      </div>
      <div id="weightSliders"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
        <div style="font-size:12px;color:var(--text2);font-weight:600">
          Total: <span id="weightTotal" style="font-weight:800">100.0%</span>
        </div>
        <div style="font-size:11px;color:var(--text3);font-weight:500">
          Weights auto-normalize on save.
        </div>
      </div>
    </div>

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-resume">\u{1F4C4}</div>
        <div><div class="scard-title">Resume &amp; Cover Letter</div><div class="scard-desc">Generation preferences</div></div>
      </div>
      <div class="fg"><label class="fl">Output Format</label>
        <select class="fi" id="set-resume-format">
          <option value="pdf" ${s.resume_format==='pdf'?'selected':''}>PDF (ATS-optimized)</option>
          <option value="docx" ${s.resume_format==='docx'?'selected':''}>DOCX</option>
          <option value="both" ${s.resume_format==='both'?'selected':''}>Both</option>
        </select>
      </div>
      <div class="fg"><label class="fl">Paper Size</label>
        <select class="fi" id="set-paper-size">
          <option value="letter" ${s.paper_size==='letter'?'selected':''}>US Letter</option>
          <option value="a4" ${s.paper_size==='a4'?'selected':''}>A4</option>
        </select>
      </div>
      <div class="fg"><label class="fl">Cover Letter Tone</label>
        <select class="fi" id="set-cl-tone">
          <option value="warm" ${s.cover_letter_tone==='warm'?'selected':''}>Warm and genuine</option>
          <option value="formal" ${s.cover_letter_tone==='formal'?'selected':''}>Formal and direct</option>
          <option value="enthusiastic" ${s.cover_letter_tone==='enthusiastic'?'selected':''}>Enthusiastic</option>
          <option value="concise" ${s.cover_letter_tone==='concise'?'selected':''}>Concise</option>
        </select>
      </div>
    </div>

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-scan">\u{1F4E1}</div>
        <div><div class="scard-title">Portal Scanner Filter</div><div class="scard-desc">Title keywords used to pre-filter scanned jobs. Tune these or use Smart Setup from Resume Builder.</div></div>
      </div>
      <div class="toggle-row"><div class="toggle-info"><div class="toggle-label">Auto-scan on startup</div></div><button class="toggle on" onclick="this.classList.toggle('on')"></button></div>
      <div class="fg"><label class="fl">Scan Interval <span style="font-weight:500;color:var(--text3);text-transform:none;letter-spacing:0">(ATS scanner \u2014 Greenhouse, Ashby, Lever)</span></label><select class="fi" id="set-scan-interval">
        <option value="6" ${s.scan_interval_hours===6?'selected':''}>Every 6 hours</option>
        <option value="12" ${s.scan_interval_hours===12?'selected':''}>Every 12 hours</option>
        <option value="24" ${s.scan_interval_hours===24?'selected':''}>Daily</option>
        <option value="168" ${s.scan_interval_hours===168?'selected':''}>Weekly</option>
      </select></div>
      <div class="fg"><label class="fl">AI Monitor Interval <span style="font-weight:500;color:var(--text3);text-transform:none;letter-spacing:0">(web-search scans \u2014 costs ~$0.05-$0.20/company per run)</span></label><select class="fi" id="set-ai-monitor-interval">
        <option value="6" ${s.ai_monitor_interval_hours===6?'selected':''}>Every 6 hours</option>
        <option value="12" ${s.ai_monitor_interval_hours===12?'selected':''}>Every 12 hours</option>
        <option value="24" ${(s.ai_monitor_interval_hours===24 || !s.ai_monitor_interval_hours)?'selected':''}>Daily (recommended)</option>
        <option value="48" ${s.ai_monitor_interval_hours===48?'selected':''}>Every 2 days</option>
        <option value="168" ${s.ai_monitor_interval_hours===168?'selected':''}>Weekly</option>
      </select></div>
      <div class="fg">
        <label class="fl">Positive Keywords <span style="font-weight:500;color:var(--text3);text-transform:none;letter-spacing:0">(title must match one)</span></label>
        <div id="sf-positive-tags" class="rp-tags" style="margin-bottom:8px;min-height:10px"></div>
        <input class="fi" id="sf-positive-input" placeholder="Type a keyword and press Enter">
      </div>
      <div class="fg">
        <label class="fl">Negative Keywords <span style="font-weight:500;color:var(--text3);text-transform:none;letter-spacing:0">(title matching any gets skipped)</span></label>
        <div id="sf-negative-tags" class="rp-tags" style="margin-bottom:8px;min-height:10px"></div>
        <input class="fi" id="sf-negative-input" placeholder="Type a keyword and press Enter">
      </div>
      <div class="toggle-row">
        <div class="toggle-info">
          <div class="toggle-label">Smart title filter (beta)</div>
          <div class="toggle-desc">Adds a tiny LLM pass that classifies titles yes/no/maybe. Drops obvious mismatches (e.g. "HTML Designer" matching "ML") and catches synonyms ("Applied AI PM" without literal "AI" keyword). ~$0.001 per title.</div>
        </div>
        <button class="toggle ${s.smart_title_filter_enabled?'on':''}" id="set-smart-title-filter" onclick="this.classList.toggle('on')"></button>
      </div>
      <div style="background:var(--primary-soft);border-radius:var(--radius-sm);padding:10px 12px;font-size:11px;color:var(--primary);font-weight:600;display:flex;align-items:center;gap:8px">
        <span>\u{1F4A1} Let AI generate these from your resume?</span>
        <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="showPage('resume')">\u{1F9E0} Smart Setup</button>
      </div>
      <div style="margin-top:10px;text-align:right">
        <button class="btn btn-ghost btn-sm" onclick="resetScannerDefaults()">\u{21A9} Reset to defaults</button>
      </div>
    </div>

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-scan">\u{1F50D}</div>
        <div>
          <div class="scard-title">Google Search (AI Monitor)</div>
          <div class="scard-desc">When configured, the AI Company Monitor uses Google's fresh search index instead of the LLM's built-in web search (which often returns stale/filled positions). Free tier: 100 queries/day. ~5 min one-time setup.</div>
        </div>
      </div>
      <details style="margin-bottom:12px;background:var(--bg2);border-radius:var(--radius-sm);padding:10px 14px">
        <summary style="cursor:pointer;font-size:12px;font-weight:600;color:var(--primary)">Setup instructions (click to expand)</summary>
        <div style="font-size:12px;line-height:1.7;color:var(--text2);margin-top:8px">
          <strong>Step 1 — Create a Google Cloud API Key</strong>
          <ol style="margin:4px 0 10px 18px;padding:0">
            <li>Go to <a href="https://console.cloud.google.com/apis/credentials" target="_blank" style="color:var(--primary)">console.cloud.google.com/apis/credentials</a></li>
            <li>Create a project if you don't have one (name it anything, e.g. "LaunchPad")</li>
            <li>Click <strong>+ Create Credentials \u{2192} API Key</strong> \u{2192} copy the key</li>
            <li>Then go to <a href="https://console.cloud.google.com/apis/library/customsearch.googleapis.com" target="_blank" style="color:var(--primary)">API Library \u{2192} Custom Search API</a> \u{2192} click <strong>Enable</strong></li>
          </ol>
          <strong>Step 2 — Create a Programmable Search Engine</strong>
          <ol style="margin:4px 0 10px 18px;padding:0">
            <li>Go to <a href="https://programmablesearchengine.google.com/controlpanel/create" target="_blank" style="color:var(--primary)">programmablesearchengine.google.com</a> \u{2192} Create</li>
            <li>Name it anything (e.g. "LaunchPad Job Search")</li>
            <li>In "Sites to search", type <code style="background:var(--bg1);padding:1px 5px;border-radius:4px">www.google.com</code> and click Add (this is just a placeholder to pass form validation)</li>
            <li>Complete the reCAPTCHA \u{2192} click <strong>Create</strong></li>
            <li>After creation, go into the engine's settings and toggle <strong>"Search the entire web"</strong> ON (this overrides the placeholder site)</li>
            <li>Copy the <strong>Search engine ID</strong> (looks like <code style="background:var(--bg1);padding:1px 5px;border-radius:4px">a1b2c3d4e5f6g7h8i</code>)</li>
          </ol>
          <strong>Step 3 — Paste both values below and Save</strong>
          <div style="margin-top:6px;color:var(--text3)">
            <strong>Cost:</strong> 100 queries/day free (no credit card needed). Each AI Monitor scan uses ~4 queries, so ~25 company scans/day at zero cost. Beyond 100/day: $5 per 1,000 queries.
          </div>
        </div>
      </details>
      <div class="fg">
        <label class="fl">Google API Key</label>
        <input class="fi" id="set-google-search-key" type="password" placeholder="${s.has_google_search_key ? '\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022} (saved)' : 'Paste your Google API key'}" autocomplete="off">
      </div>
      <div class="fg">
        <label class="fl">Search Engine ID (cx)</label>
        <input class="fi" id="set-google-search-cx" value="${escapeHtml(s.google_search_cx || '')}" placeholder="e.g. a1b2c3d4e5f6g7h8i">
      </div>
      ${s.has_google_search_key && s.google_search_cx
        ? '<div style="font-size:11px;color:var(--success);font-weight:600;margin-top:4px">\u{2705} Google Search configured — AI Monitor will use fresh results</div>'
        : s.has_google_search_key
          ? '<div style="font-size:11px;color:var(--warning);font-weight:600;margin-top:4px">\u{26A0}\u{FE0F} API key saved but Search Engine ID is missing — add the cx value above</div>'
          : '<div style="font-size:11px;color:var(--text3);font-weight:500;margin-top:4px">\u{26A0}\u{FE0F} Not configured — AI Monitor falls back to LLM web search (may return stale results)</div>'
      }
    </div>

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-scan">\u{1F4EE}</div>
        <div>
          <div class="scard-title">Trusted Job-Alert Senders</div>
          <div class="scard-desc">Emails from these senders are fast-pathed as job alerts — LaunchPad skips the LLM classifier and goes straight to listing extraction. Add your own alert sources (Indeed, Wellfound, Built In, etc.). Other emails still go through the regular classifier.</div>
        </div>
      </div>
      <div class="fg">
        <label class="fl">Sender emails and domains
          <span style="font-weight:500;color:var(--text3);text-transform:none;letter-spacing:0">— use <code style="background:var(--bg2);padding:1px 5px;border-radius:4px">@domain.com</code> to match any sender at that domain</span>
        </label>
        <div id="sf-senders-tags" class="rp-tags" style="margin-bottom:8px;min-height:10px"></div>
        <input class="fi" id="sf-senders-input" placeholder="e.g. alert@indeed.com or @wellfound.com">
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:space-between;align-items:center">
        <button class="btn btn-ghost btn-sm" onclick="addAlertSenderPresets()">\u{2B50} Add recommended presets</button>
        <button class="btn btn-primary btn-sm" onclick="saveAlertSenders()" id="sf-senders-save" style="display:none">\u{1F4BE} Save</button>
      </div>
      <div style="font-size:11px;color:var(--text3);font-weight:500;margin-top:8px;line-height:1.5">
        Presets: Indeed, LinkedIn, ZipRecruiter, Glassdoor. Remove any you don't want. Changes apply to future Gmail syncs.
      </div>
    </div>

    <div class="scard" style="grid-column: span 2">
      <div class="scard-header">
        <div class="scard-icon sci-submit">\u{1F3AF}</div>
        <div>
          <div class="scard-title">Pass History &amp; Calibration</div>
          <div class="scard-desc">Once you pass on enough roles, your reasons start teaching the AI what to score lower for you. Review, delete, or exclude individual passes here.</div>
        </div>
      </div>
      <div id="passCalibrationCard">
        <div class="empty-state" style="padding:16px"><div style="font-size:12px;color:var(--text3);font-weight:500">\u{23F3} Loading pass history...</div></div>
      </div>
    </div>

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-export">\u{1F4BE}</div>
        <div><div class="scard-title">Data &amp; Backup</div><div class="scard-desc">Export your LaunchPad data, or restore from a previous backup. Secrets (API keys, OAuth tokens) are never included.</div></div>
      </div>
      <button class="btn btn-primary" style="width:100%;justify-content:center;margin-bottom:10px" onclick="exportLaunchpadBackup()">\u{2B07} Download Full Backup (.zip)</button>
      <label class="btn btn-ghost" style="width:100%;justify-content:center;cursor:pointer;margin-bottom:6px">
        \u{2B06} Restore from Backup
        <input type="file" accept=".zip" style="display:none" onchange="importLaunchpadBackup(this.files[0])">
      </label>
      <div style="font-size:11px;color:var(--text3);font-weight:500;line-height:1.5">
        Restore overwrites everything. After restore you'll need to restart the server and re-enter LLM keys + reconnect Gmail accounts.
      </div>
    </div>

    <div class="scard">
      <div class="scard-header">
        <div class="scard-icon sci-export">\u{1F4E6}</div>
        <div><div class="scard-title">Danger Zone</div><div class="scard-desc">Destructive actions</div></div>
      </div>
      <button class="btn btn-danger" style="width:100%;justify-content:center" onclick="deleteThisProfile()">\u{1F5D1} Delete This Profile</button>
      <div style="font-size:11px;color:var(--text3);font-weight:500;margin-top:8px;text-align:center">This deletes all your listings, resumes, and connected accounts.</div>
    </div>

  </div>`;

  // Initialize tag inputs for scanner title filter
  window._titleFilter = {
    positive: [...(titleFilter.positive || [])],
    negative: [...(titleFilter.negative || [])],
  };
  renderFilterTags();
  ['positive', 'negative'].forEach(kind => {
    const input = document.getElementById(`sf-${kind}-input`);
    if (!input) return;
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        const val = input.value.trim().replace(/,$/, '');
        if (val && !window._titleFilter[kind].includes(val)) {
          window._titleFilter[kind].push(val);
          input.value = '';
          renderFilterTags();
          saveTitleFilter();
        }
      }
    });
  });

  // Initialize tag input for trusted job-alert senders.
  window._alertSenders = Array.isArray(s.job_alert_senders)
    ? [...s.job_alert_senders]
    : [];
  window._alertSendersOriginal = [...window._alertSenders];
  renderAlertSenderTags();
  (() => {
    const input = document.getElementById('sf-senders-input');
    if (!input) return;
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        const val = input.value.trim().replace(/,$/, '').toLowerCase();
        if (!val) return;
        if (!val.includes('@')) { toast && toast('Enter a full email or a domain starting with @'); return; }
        if (window._alertSenders.includes(val)) { input.value = ''; return; }
        window._alertSenders.push(val);
        input.value = '';
        renderAlertSenderTags();
        markAlertSendersDirty();
      }
    });
  })();

  // Initialize scoring weight sliders
  window._scoringWeights = normalizeWeights(s.scoring_weights || {});
  renderWeightSliders();

  // Initialize pass calibration card
  loadPassCalibrationCard(s);
}

function renderFilterTags(){
  const f = window._titleFilter || { positive: [], negative: [] };
  for (const kind of ['positive', 'negative']) {
    const el = document.getElementById(`sf-${kind}-tags`);
    if (!el) continue;
    if ((f[kind] || []).length === 0) {
      el.innerHTML = `<span style="font-size:12px;color:var(--text3);font-weight:500">(none - add keywords below)</span>`;
      continue;
    }
    el.innerHTML = f[kind].map((k, i) => `
      <span class="rp-tag" style="display:inline-flex;align-items:center;gap:4px;padding:4px 4px 4px 10px;font-size:12px">
        ${escapeHtml(k)}
        <button style="background:rgba(99,102,241,0.2);border:none;color:var(--primary);width:18px;height:18px;border-radius:50%;cursor:pointer;font-size:14px;line-height:1;padding:0" onclick="removeFilterTag('${kind}', ${i})">\u00d7</button>
      </span>
    `).join(' ');
  }
}

function removeFilterTag(kind, index){
  if (!window._titleFilter) return;
  window._titleFilter[kind].splice(index, 1);
  renderFilterTags();
  saveTitleFilter();
}

let _titleFilterSaveTimer = null;
function saveTitleFilter(){
  clearTimeout(_titleFilterSaveTimer);
  _titleFilterSaveTimer = setTimeout(async () => {
    try {
      await window.api.scanner.updateTitleFilter({
        positive: window._titleFilter.positive,
        negative: window._titleFilter.negative,
      });
    } catch (err) {
      console.error('Failed to save title filter:', err);
    }
  }, 500);
}

// ---------------------------------------------------------------------------
// Trusted job-alert senders (Settings → Trusted Job-Alert Senders)
// ---------------------------------------------------------------------------
const ALERT_SENDER_PRESETS = [
  '@indeed.com',
  'jobs-listings@linkedin.com',
  'jobs-noreply@linkedin.com',
  '@ziprecruiter.com',
  '@glassdoor.com',
  '@wellfound.com',
  '@builtin.com',
];

function renderAlertSenderTags(){
  const el = document.getElementById('sf-senders-tags');
  if (!el) return;
  const senders = window._alertSenders || [];
  if (senders.length === 0) {
    el.innerHTML = `<span style="font-size:12px;color:var(--text3);font-weight:500">(none — add senders below or use presets)</span>`;
    return;
  }
  el.innerHTML = senders.map((s, i) => `
    <span class="rp-tag" style="display:inline-flex;align-items:center;gap:4px;padding:4px 4px 4px 10px;font-size:12px">
      ${escapeHtml(s)}
      <button style="background:rgba(99,102,241,0.2);border:none;color:var(--primary);width:18px;height:18px;border-radius:50%;cursor:pointer;font-size:14px;line-height:1;padding:0" onclick="removeAlertSender(${i})">\u00d7</button>
    </span>
  `).join(' ');
}

function removeAlertSender(index){
  if (!window._alertSenders) return;
  window._alertSenders.splice(index, 1);
  renderAlertSenderTags();
  markAlertSendersDirty();
}

function addAlertSenderPresets(){
  window._alertSenders = window._alertSenders || [];
  let added = 0;
  for (const p of ALERT_SENDER_PRESETS) {
    if (!window._alertSenders.includes(p)) {
      window._alertSenders.push(p);
      added++;
    }
  }
  renderAlertSenderTags();
  if (added > 0) {
    markAlertSendersDirty();
  } else {
    toast && toast('All presets are already in your list');
  }
}

function markAlertSendersDirty(){
  const btn = document.getElementById('sf-senders-save');
  if (!btn) return;
  const cur = (window._alertSenders || []).slice().sort().join('|');
  const orig = (window._alertSendersOriginal || []).slice().sort().join('|');
  btn.style.display = cur === orig ? 'none' : 'inline-block';
}

async function saveAlertSenders(){
  const btn = document.getElementById('sf-senders-save');
  if (btn) { btn.disabled = true; btn.textContent = '\u{23F3} Saving...'; }
  try {
    const senders = (window._alertSenders || []).slice();
    await window.api.settings.update({ job_alert_senders: senders });
    window._alertSendersOriginal = [...senders];
    toast && toast('Trusted senders saved');
    if (btn) btn.style.display = 'none';
  } catch (err) {
    console.error('Failed to save alert senders:', err);
    toast && toast('Failed to save: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '\u{1F4BE} Save'; }
  }
}


function escapeHtml(str){
  if(!str)return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ============================================================================
// Reusable sort helper for data tables.
// ============================================================================
//
// getSortState(tableKey) -> { field, dir } with sensible defaults applied by
// the caller when saved state is missing.
//
// sortTable(tableKey, field, dataType) — called from column header onclick.
// Flips direction if already active, else sets ascending for the new field,
// persists to localStorage, then invokes the registered reload function.
//
// sortRows(rows, field, dir, dataType) — deterministic in-place sort helper
// handling null/undefined gracefully. dataType: 'string' | 'number' | 'date'.
//
// sortableHeader(tableKey, field, label, dataType) — emits a <th> with the
// current-sort arrow and onclick handler wired up.

window._tableSortReloaders = window._tableSortReloaders || {};

function registerTableSortReload(tableKey, fn) {
  window._tableSortReloaders[tableKey] = fn;
}

function getSortState(tableKey) {
  try {
    const raw = localStorage.getItem(`launchpad.sort.${tableKey}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.field && (parsed.dir === 'asc' || parsed.dir === 'desc')) {
      return parsed;
    }
  } catch {}
  return null;
}

function saveSortState(tableKey, state) {
  try {
    localStorage.setItem(`launchpad.sort.${tableKey}`, JSON.stringify(state));
  } catch {}
}

function sortTable(tableKey, field, dataType) {
  const prev = getSortState(tableKey);
  const dir = (prev && prev.field === field && prev.dir === 'asc') ? 'desc' : 'asc';
  saveSortState(tableKey, { field, dir, dataType });
  const reload = (window._tableSortReloaders || {})[tableKey];
  if (typeof reload === 'function') {
    try { reload(); } catch (err) { console.error('sort reload failed', err); }
  }
}

function sortRows(rows, field, dir, dataType) {
  if (!Array.isArray(rows) || !field) return rows;
  const mul = dir === 'desc' ? -1 : 1;
  const getter = (o) => (o == null ? null : o[field]);
  const isNully = (v) => v == null || v === '';
  return [...rows].sort((a, b) => {
    const av = getter(a);
    const bv = getter(b);
    if (isNully(av) && isNully(bv)) return 0;
    if (isNully(av)) return 1;  // nulls always at end regardless of dir
    if (isNully(bv)) return -1;
    if (dataType === 'number') {
      return (Number(av) - Number(bv)) * mul;
    }
    if (dataType === 'date') {
      return (new Date(av).getTime() - new Date(bv).getTime()) * mul;
    }
    return String(av).localeCompare(String(bv), undefined, { sensitivity: 'base' }) * mul;
  });
}

function sortableHeader(tableKey, field, label, dataType) {
  const st = getSortState(tableKey);
  const active = st && st.field === field;
  const arrow = !active ? '\u2195'  // up-down arrows
    : st.dir === 'asc' ? '\u25B4'    // up triangle
    : '\u25BE';                      // down triangle
  const color = active ? 'var(--primary)' : 'var(--text3)';
  return `<th style="cursor:pointer;user-select:none" onclick="sortTable('${tableKey}','${field}','${dataType}')" title="Sort by ${escapeHtml(label)}">
    ${escapeHtml(label)} <span style="color:${color};font-size:11px;margin-left:4px">${arrow}</span>
  </th>`;
}

function applyTableSort(rows, tableKey, defaultField, defaultDir, defaultDataType) {
  const st = getSortState(tableKey) || { field: defaultField, dir: defaultDir, dataType: defaultDataType };
  return sortRows(rows, st.field, st.dir, st.dataType || defaultDataType);
}


// Global search across listings - works from any page
let _searchTimer = null;
function handleGlobalSearch(query){
  window._searchQuery = query.trim().toLowerCase();
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(async () => {
    const activeItem = document.querySelector('.nav-item.active');
    const page = activeItem ? activeItem.dataset.page : 'dashboard';

    if (!window._searchQuery) {
      // Cleared - re-render the current page
      hideSearchDropdown();
      if (page === 'pipeline' || page === 'listings') showPage(page);
      return;
    }

    // On pipeline/listings pages, filter in-place
    if (page === 'pipeline' || page === 'listings') {
      applySearchFilterOnListings();
      hideSearchDropdown();
      return;
    }

    // Otherwise show a floating quick-results dropdown
    try {
      const [listings, companies] = await Promise.all([
        window.api.listings.list({ limit: 500 }),
        window.api.scanner.listCompanies().catch(() => []),
      ]);
      const matches = filterListingsBySearch(listings, window._searchQuery).slice(0, 6);
      const companyMatches = filterCompaniesBySearch(companies, window._searchQuery, listings).slice(0, 3);
      showSearchDropdown(matches, companyMatches);
    } catch (err) {
      console.error(err);
    }
  }, 200);
}

function filterListingsBySearch(listings, q){
  if (!q) return listings;
  const low = q.toLowerCase();
  return listings.filter(j =>
    (j.company || '').toLowerCase().includes(low) ||
    (j.role_title || '').toLowerCase().includes(low) ||
    (j.location || '').toLowerCase().includes(low) ||
    (j.archetype || '').toLowerCase().includes(low)
  );
}

function filterCompaniesBySearch(companies, q, listings){
  if (!q) return [];
  const low = q.toLowerCase();
  return companies.filter(c => (c.name || '').toLowerCase().includes(low));
}

function applySearchFilterOnListings(){
  const q = window._searchQuery || '';
  // Walk visible jcards and toggle display
  const cards = document.querySelectorAll('.jcard');
  let shown = 0, total = cards.length;
  cards.forEach(card => {
    const txt = card.textContent.toLowerCase();
    if (!q || txt.includes(q)) {
      card.style.display = '';
      shown++;
    } else {
      card.style.display = 'none';
    }
  });
  // Also handle table rows
  const rows = document.querySelectorAll('.tbl-wrap tbody tr');
  rows.forEach(row => {
    const txt = row.textContent.toLowerCase();
    if (!q || txt.includes(q)) {
      row.style.display = '';
    } else {
      row.style.display = 'none';
    }
  });
}

function showSearchDropdown(matches, companyMatches = []){
  hideSearchDropdown();
  const input = document.getElementById('globalSearch');
  if (!input) return;
  const rect = input.getBoundingClientRect();
  const d = document.createElement('div');
  d.id = 'searchDropdown';
  d.style.cssText = `position:fixed;top:${rect.bottom+6}px;left:${rect.left}px;width:${rect.width}px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);box-shadow:var(--shadow-lg);z-index:100;max-height:500px;overflow-y:auto`;

  let html = '';

  // Company actions section
  if (companyMatches.length > 0) {
    html += `<div style="padding:6px 14px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text3);background:var(--bg2)">Scan a tracked company</div>`;
    html += companyMatches.map(c => `
      <div class="sdrop-item" onclick="scanFromSearch(${c.id}, '${escapeHtml(c.name).replace(/'/g, "&#39;")}')" style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px" onmouseover="this.style.background='var(--bg2)'" onmouseout="this.style.background=''">
        <div style="width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#eef0ff,#fce7f3);display:flex;align-items:center;justify-content:center;font-size:14px">\u{1F50D}</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:700">${escapeHtml(c.name)}</div>
          <div style="font-size:11px;color:var(--text3);font-weight:500">${c.last_job_count || 0} jobs last scan \u00b7 Click to scan now</div>
        </div>
        <div style="font-size:16px;color:var(--primary);font-weight:700">\u{25B6}</div>
      </div>`).join('');
  }

  // Listings section
  if (matches.length > 0) {
    html += `<div style="padding:6px 14px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text3);background:var(--bg2)">Listings in your pipeline</div>`;
    html += matches.map(j => {
      const score = j.score != null ? j.score.toFixed(1) : '-';
      const scoreColor = j.score == null ? 'var(--text3)' : j.score >= 4 ? 'var(--green)' : j.score >= 3.5 ? '#a16207' : 'var(--red)';
      return `
        <div class="sdrop-item" onclick="openDetail(${j.id}); hideSearchDropdown(); document.getElementById('globalSearch').value=''" style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px" onmouseover="this.style.background='var(--bg2)'" onmouseout="this.style.background=''">
          <div style="flex:1;min-width:0">
            <div style="font-size:11px;font-weight:700;color:var(--primary);text-transform:uppercase;letter-spacing:0.5px">${escapeHtml(j.company)}</div>
            <div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(j.role_title)}</div>
            <div style="font-size:11px;color:var(--text3);font-weight:500">${escapeHtml(j.status)} \u00b7 ${escapeHtml(j.location || '')}</div>
          </div>
          <div style="font-size:14px;font-weight:800;color:${scoreColor}">${score}</div>
        </div>`;
    }).join('');
  }

  if (!html) {
    html = `<div style="padding:16px;font-size:13px;color:var(--text3);text-align:center">
      <div style="margin-bottom:10px">No matches in your pipeline or tracked companies</div>
      <button class="btn btn-primary btn-sm" onclick="addCompanyFromSearch()">\u{2795} Add "${escapeHtml(window._searchQuery)}" as tracked company</button>
    </div>`;
  }

  d.innerHTML = html;
  document.body.appendChild(d);

  setTimeout(() => {
    document.addEventListener('click', hideSearchDropdownOnClickOutside, { once: true });
  }, 50);
}

async function scanFromSearch(companyId, companyName){
  hideSearchDropdown();
  const input = document.getElementById('globalSearch');
  if (input) input.value = '';
  window._searchQuery = '';

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.innerHTML = `
    <div class="modal" style="text-align:center">
      <div class="modal-body">
        <div style="font-size:44px;margin-bottom:10px">\u{1F50D}</div>
        <div style="font-size:16px;font-weight:700;margin-bottom:6px">Scanning ${escapeHtml(companyName)}...</div>
        <div style="font-size:12px;color:var(--text2);font-weight:500" id="scanResultMsg">Fetching open roles from their career page.</div>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  try {
    const result = await window.api.scanner.scanCompany(companyId);
    overlay.querySelector('#scanResultMsg').textContent =
      `Found ${result.jobs_found} open roles. Click "Scan All Now" on the Scanner page to add matching ones to your pipeline.`;
    const modal = overlay.querySelector('.modal-body');
    modal.insertAdjacentHTML('beforeend', `
      <div style="margin-top:16px;display:flex;gap:8px;justify-content:center">
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Close</button>
        <button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove(); showPage('scanner')">Go to Scanner</button>
      </div>`);
  } catch (err) {
    const msg = err.message || String(err);
    const isNoApi = msg.toLowerCase().includes('no ats api');
    const modal = overlay.querySelector('.modal-body');
    if (isNoApi) {
      modal.innerHTML = `
        <div style="font-size:44px;margin-bottom:10px">\u{1F6E0}</div>
        <div style="font-size:16px;font-weight:700;margin-bottom:6px">${escapeHtml(companyName)} uses a custom career page</div>
        <div style="font-size:13px;color:var(--text2);font-weight:500;line-height:1.6;text-align:left;background:var(--bg2);padding:14px;border-radius:var(--radius-sm);margin:10px 0">
          We support Greenhouse, Ashby, and Lever ATS APIs automatically. Companies with custom systems (Amazon, Google, Microsoft, Apple, Meta) need a different approach.
          <br><br>
          <strong>\u{1F4A1} Your options:</strong>
          <div style="margin-top:8px;padding-left:4px">
            <div style="margin-bottom:8px"><strong>1. Paste URLs manually</strong> - Copy a job URL from ${escapeHtml(companyName)}'s career page and use <strong>\u{2795} Add Listing</strong>. Works on any site.</div>
            <div><strong>2. Let LinkedIn feed you jobs</strong> - Set up a LinkedIn Job Alert for ${escapeHtml(companyName)}. LaunchPad's Gmail sync auto-extracts listings from those emails.</div>
          </div>
        </div>
        <div style="display:flex;gap:8px;justify-content:center;margin-top:14px;flex-wrap:wrap">
          <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Close</button>
          <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove(); showLinkedInGuide(['${escapeHtml(companyName).replace(/'/g, '\\\'')}'])">\u{1F4D6} LinkedIn Setup</button>
          <button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove(); showAddModal()">\u{2795} Add Listing Manually</button>
        </div>`;
    } else {
      overlay.querySelector('#scanResultMsg').textContent = 'Failed: ' + msg;
      overlay.querySelector('#scanResultMsg').style.color = 'var(--red)';
      overlay.querySelector('.modal-body').insertAdjacentHTML('beforeend',
        `<div style="margin-top:14px"><button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove()">Close</button></div>`);
    }
  }
}

async function addCompanyFromSearch(){
  const name = window._searchQuery;
  if (!name) return;
  hideSearchDropdown();
  const url = prompt(`Careers URL for "${name}":\n\n(e.g., https://jobs.ashbyhq.com/${name.toLowerCase()})`);
  if (!url) return;
  try {
    await window.api.scanner.createCompany({ name: name, careers_url: url.trim(), enabled: true });
    alert('Added! Go to Scanner to run a scan.');
    document.getElementById('globalSearch').value = '';
    window._searchQuery = '';
    showPage('scanner');
  } catch (err) {
    alert('Failed: ' + err.message);
  }
}

function hideSearchDropdownOnClickOutside(e){
  const d = document.getElementById('searchDropdown');
  const input = document.getElementById('globalSearch');
  if (d && !d.contains(e.target) && e.target !== input) {
    hideSearchDropdown();
  } else if (d) {
    document.addEventListener('click', hideSearchDropdownOnClickOutside, { once: true });
  }
}

function hideSearchDropdown(){
  const d = document.getElementById('searchDropdown');
  if (d) d.remove();
}

async function onProviderChange(){
  const providerEl = document.getElementById('set-llm-provider');
  const modelEl = document.getElementById('set-llm-model');
  try {
    const models = await window.api.settings.models();
    const list = models[providerEl.value] || [];
    modelEl.innerHTML = list.map(m => `<option value="${m.id}">${m.name} - ${m.description}</option>`).join('');
  } catch (err) {
    console.error(err);
  }
}

async function testLLMConnection(){
  const provider = document.getElementById('set-llm-provider').value;
  const api_key = document.getElementById('set-llm-key').value;
  const model = document.getElementById('set-llm-model').value;
  const resultEl = document.getElementById('testResult');
  resultEl.innerHTML = '<span style="color:var(--text3)">\u{23F3} Testing...</span>';
  try {
    const r = await window.api.settings.testLLM(provider, api_key, model);
    if (r.success) {
      resultEl.innerHTML = `<span style="color:var(--green)">\u{2713} Working (${r.latency_ms}ms)</span>`;
    } else {
      resultEl.innerHTML = `<span style="color:var(--red)">\u{2715} ${r.error}</span>`;
    }
  } catch (err) {
    resultEl.innerHTML = `<span style="color:var(--red)">\u{2715} ${err.message}</span>`;
  }
}

async function saveSettings(){
  const statusEl = document.getElementById('saveStatus');
  statusEl.textContent = 'Saving...';
  const targetRolesStr = document.getElementById('set-target-roles').value;
  const targetRoles = targetRolesStr.split(',').map(s => s.trim()).filter(Boolean);
  const payload = {
    name: document.getElementById('set-name').value.trim(),
    role_title: document.getElementById('set-role').value.trim(),
    profile_data: {
      email: document.getElementById('set-email').value.trim(),
      phone: document.getElementById('set-phone').value.trim(),
      linkedin: document.getElementById('set-linkedin').value.trim(),
      location: document.getElementById('set-location').value.trim(),
      target_roles: targetRoles,
      target_salary: document.getElementById('set-target-salary').value.trim(),
    },
    llm_provider: document.getElementById('set-llm-provider').value,
    llm_model: document.getElementById('set-llm-model').value,
    require_approval: document.getElementById('set-require-approval').classList.contains('on'),
    auto_evaluate: document.getElementById('set-auto-evaluate').classList.contains('on'),
    auto_generate_assets: document.getElementById('set-auto-generate').classList.contains('on'),
    min_submit_score: parseFloat(document.getElementById('set-min-score').value),
    resume_format: document.getElementById('set-resume-format').value,
    paper_size: document.getElementById('set-paper-size').value,
    cover_letter_tone: document.getElementById('set-cl-tone').value,
    scan_interval_hours: parseInt(document.getElementById('set-scan-interval').value),
    ai_monitor_interval_hours: parseInt(document.getElementById('set-ai-monitor-interval')?.value || 24),
    smart_title_filter_enabled: document.getElementById('set-smart-title-filter')?.classList.contains('on') ?? false,
    web_grounded_eval: document.getElementById('set-web-grounded')?.classList.contains('on') ?? true,
    scoring_weights: window._scoringWeights || undefined,
    pass_calibration_preference: document.getElementById('set-pass-calibration-pref')?.value || undefined,
    pass_history_threshold: (() => {
      const v = parseInt(document.getElementById('set-pass-threshold')?.value || '');
      return Number.isFinite(v) && v >= 3 && v <= 100 ? v : undefined;
    })(),
  };
  const newKey = document.getElementById('set-llm-key').value;
  if (newKey) payload.llm_api_key = newKey;

  // Google Search credentials
  const gKey = document.getElementById('set-google-search-key')?.value;
  if (gKey) payload.google_search_api_key = gKey;
  const gCx = document.getElementById('set-google-search-cx')?.value?.trim();
  if (gCx !== undefined) payload.google_search_cx = gCx || null;

  try {
    const updated = await window.api.settings.update(payload);
    CURRENT_PROFILE.settings = updated;
    CURRENT_PROFILE.name = updated.name;
    document.getElementById('currentProfileName').textContent = updated.name;
    const initials = updated.name.split(' ').map(n=>n[0]).join('').slice(0,2).toUpperCase();
    document.getElementById('currentProfileAvatar').textContent = initials;
    statusEl.textContent = '\u2713 Saved';
    statusEl.style.color = 'var(--green)';
    setTimeout(() => { statusEl.textContent = ''; }, 2000);
  } catch (err) {
    statusEl.textContent = '\u2715 ' + err.message;
    statusEl.style.color = 'var(--red)';
  }
}

async function deleteThisProfile(){
  if (!confirm('Delete this profile and ALL its data? This cannot be undone.')) return;
  if (!confirm('Are you absolutely sure?')) return;
  try {
    await window.api.profiles.delete(CURRENT_PROFILE.id);
    await window.api.auth.logout();
    location.reload();
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

// Event handlers
document.addEventListener('click',(e)=>{
  const n=e.target.closest('.nav-item[data-page]');
  if(n){showPage(n.dataset.page)}
});

// Load profiles on page load (or restore existing session)
document.addEventListener('DOMContentLoaded', async () => {
  // Check if already logged in
  try {
    const user = await window.api.auth.me();
    const settings = await window.api.settings.get();
    CURRENT_PROFILE = { ...user, settings };
    loginAs(CURRENT_PROFILE);
  } catch {
    // Not logged in - show login screen
    loadProfilesForLogin();
  }
  // Refresh sidebar info every 60s if logged in
  setInterval(() => {
    if (CURRENT_PROFILE) updateSidebarInfo();
  }, 60000);
});

// ============================================================================
// Full-page resume & cover letter markdown editors
// Two-column: left = raw markdown textarea, right = inline PDF preview.
// Toolbar at top for intensity/tone, Revert, Save, Regenerate, Back.
// ============================================================================

async function renderResumeEditor(c){
  const id = window._editorListingId;
  if (!id) {
    c.innerHTML = `<div style="padding:40px;text-align:center">
      <div style="font-size:16px;color:var(--text2);margin-bottom:12px">No listing selected.</div>
      <button class="btn btn-primary" onclick="showPage('pipeline')">Go to Pipeline</button>
    </div>`;
    return;
  }

  c.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text2)">\u{23F3} Loading editor...</div>`;

  let listing, resumeData;
  try {
    [listing, resumeData] = await Promise.all([
      window.api.listings.get(id),
      window.api.listings.getTailoredResume(id),
    ]);
  } catch (err) {
    c.innerHTML = `<div style="padding:40px;text-align:center">
      <div style="font-size:16px;color:var(--red);margin-bottom:12px">\u{26A0} Failed to load: ${escapeHtml(err.message)}</div>
      <button class="btn btn-primary" onclick="showPage('pipeline')">Back to Pipeline</button>
    </div>`;
    return;
  }

  if (!resumeData.markdown) {
    c.innerHTML = `<div style="padding:40px;max-width:640px;margin:40px auto;background:var(--bg1);border-radius:var(--radius);text-align:center">
      <div style="font-size:48px;margin-bottom:16px">\u{1F4C4}</div>
      <div style="font-size:20px;font-weight:700;margin-bottom:10px">No tailored resume yet</div>
      <div style="font-size:14px;color:var(--text2);margin-bottom:20px">This listing doesn't have a tailored resume. Generate one first to enable editing.</div>
      <div style="display:flex;gap:10px;justify-content:center">
        <button class="btn btn-ghost" onclick="showPage('pipeline')">Back to Pipeline</button>
        <button class="btn btn-primary" onclick="regenerateResumeFromEditor('resume')">\u{1F4C4} Generate Now</button>
      </div>
    </div>`;
    return;
  }

  const intensity = resumeData.intensity || 'medium';
  const pdfFilename = resumeData.pdf_path ? resumeData.pdf_path.split('/').pop() : null;
  const pdfUrl = pdfFilename ? `/api/resumes/generated/${encodeURIComponent(pdfFilename)}?inline=1&t=${Date.now()}` : null;
  const hasOriginal = !!resumeData.markdown_original && resumeData.markdown_original !== resumeData.markdown;
  const chatOn = isChatVisible('resume');
  const bodyGrid = chatOn ? '320px 1fr 1fr' : '1fr 1fr';

  c.innerHTML = `
    <div class="editor-shell" style="display:flex;flex-direction:column;height:calc(100vh - 120px);min-height:520px;gap:14px">
      <div class="editor-header" style="background:var(--bg1);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow-sm);display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between">
        <div style="flex:1;min-width:260px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
            <button class="btn btn-ghost btn-sm" onclick="showPage('pipeline')" title="Back">\u{2190}</button>
            <div style="font-size:18px;font-weight:700">\u{270F}\u{FE0F} Resume Editor</div>
          </div>
          <div style="font-size:12px;color:var(--text2);font-weight:500;padding-left:4px">
            ${escapeHtml(listing.company || '')} \u00b7 ${escapeHtml(listing.role_title || '')}
          </div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
          <div class="intensity-pills" style="display:flex;gap:4px;background:var(--bg2);border-radius:var(--radius-sm);padding:3px">
            ${['light','medium','heavy'].map(t => `
              <button class="intensity-pill ${t===intensity?'active':''}"
                      data-intensity="${t}"
                      onclick="changeTailoringIntensity('${t}')"
                      style="padding:6px 12px;border-radius:6px;border:none;cursor:pointer;font-size:12px;font-weight:600;background:${t===intensity?'var(--primary)':'transparent'};color:${t===intensity?'white':'var(--text2)'}">
                ${t[0].toUpperCase()+t.slice(1)}
              </button>`).join('')}
          </div>
          <button class="btn ${chatOn ? 'btn-primary' : 'btn-ghost'} btn-sm" onclick="chatToggle('resume')" title="Toggle the conversational AI editing panel">\u{1F4AC} Chat</button>
          <button class="btn btn-ghost btn-sm" onclick="regenerateResumeFromEditor()" id="editRegenBtn" title="Run LLM tailoring again">\u{1F504} Regenerate</button>
          ${hasOriginal ? `<button class="btn btn-ghost btn-sm" onclick="revertResumeEdits()" title="Restore original">\u{23EA} Revert</button>` : ''}
          ${pdfUrl ? `<a class="btn btn-ghost btn-sm" href="${pdfUrl}" target="_blank" title="Open PDF in new tab">\u{2197} Open PDF</a>` : ''}
          ${pdfFilename ? `<a class="btn btn-ghost btn-sm" href="/api/resumes/generated/${encodeURIComponent(pdfFilename)}" download title="Download">\u{2B07} Download</a>` : ''}
          <button class="btn btn-primary btn-sm" onclick="saveResumeEdits()" id="saveResumeBtn">\u{1F4BE} Save</button>
        </div>
      </div>

      <div class="editor-body" style="flex:1;display:grid;grid-template-columns:${bodyGrid};gap:14px;min-height:0">
        ${chatOn ? `<div id="chatPanel-resume" style="background:var(--bg1);border-radius:var(--radius);box-shadow:var(--shadow-sm);overflow:hidden;min-height:0"></div>` : ''}
        <div style="background:var(--bg1);border-radius:var(--radius);display:flex;flex-direction:column;overflow:hidden;box-shadow:var(--shadow-sm)">
          <div style="padding:10px 14px;border-bottom:1px solid var(--border);font-size:12px;font-weight:700;color:var(--text2);letter-spacing:.04em;text-transform:uppercase;display:flex;justify-content:space-between;align-items:center">
            <span>Markdown source</span>
            <span id="resumeDirtyBadge" style="font-size:11px;color:var(--text3);font-weight:500"></span>
          </div>
          <textarea id="resumeMdEditor"
                    style="flex:1;border:none;outline:none;padding:16px;font-family:'SF Mono',Menlo,Consolas,monospace;font-size:13px;line-height:1.55;resize:none;background:var(--bg1);color:var(--text1)"
                    spellcheck="false"
                    oninput="markResumeDirty()">${escapeHtml(resumeData.markdown)}</textarea>
        </div>
        <div style="background:var(--bg2);border-radius:var(--radius);display:flex;flex-direction:column;overflow:hidden;box-shadow:var(--shadow-sm)">
          <div style="padding:10px 14px;border-bottom:1px solid var(--border);font-size:12px;font-weight:700;color:var(--text2);letter-spacing:.04em;text-transform:uppercase">
            PDF preview
          </div>
          <div style="flex:1;position:relative;min-height:0" id="resumePdfWrap">
            ${pdfUrl
              ? `<embed id="resumePdfEmbed" src="${pdfUrl}" type="application/pdf" style="width:100%;height:100%;border:none;background:#525659">`
              : `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text3);font-size:13px">No PDF rendered yet. Save to render.</div>`
            }
          </div>
        </div>
      </div>
    </div>`;

  // Store originals so we can detect dirty state
  window._editorState = {
    kind: 'resume',
    listingId: id,
    originalMarkdown: resumeData.markdown,
    pdfFilename: pdfFilename,
    intensity: intensity,
  };

  // If chat is on, hydrate + render the panel.
  if (chatOn) {
    await loadChatFor(id);
    renderChatPanel(id, 'chatPanel-resume', 'resume');
  }
}

async function renderCoverLetterEditor(c){
  const id = window._editorListingId;
  if (!id) {
    c.innerHTML = `<div style="padding:40px;text-align:center">
      <div style="font-size:16px;color:var(--text2);margin-bottom:12px">No listing selected.</div>
      <button class="btn btn-primary" onclick="showPage('pipeline')">Go to Pipeline</button>
    </div>`;
    return;
  }

  c.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text2)">\u{23F3} Loading editor...</div>`;

  let listing, coverData;
  try {
    [listing, coverData] = await Promise.all([
      window.api.listings.get(id),
      window.api.listings.getCoverLetterMd(id),
    ]);
  } catch (err) {
    c.innerHTML = `<div style="padding:40px;text-align:center">
      <div style="font-size:16px;color:var(--red);margin-bottom:12px">\u{26A0} Failed to load: ${escapeHtml(err.message)}</div>
      <button class="btn btn-primary" onclick="showPage('pipeline')">Back to Pipeline</button>
    </div>`;
    return;
  }

  if (!coverData.markdown) {
    c.innerHTML = `<div style="padding:40px;max-width:640px;margin:40px auto;background:var(--bg1);border-radius:var(--radius);text-align:center">
      <div style="font-size:48px;margin-bottom:16px">\u{270D}</div>
      <div style="font-size:20px;font-weight:700;margin-bottom:10px">No cover letter yet</div>
      <div style="font-size:14px;color:var(--text2);margin-bottom:20px">This listing doesn't have a cover letter. Generate one first to enable editing.</div>
      <div style="display:flex;gap:10px;justify-content:center">
        <button class="btn btn-ghost" onclick="showPage('pipeline')">Back to Pipeline</button>
        <button class="btn btn-pink" onclick="regenerateCoverFromEditor()">\u{270D} Generate Now</button>
      </div>
    </div>`;
    return;
  }

  const currentTone = coverData.tone_override || coverData.profile_default_tone || 'warm';
  const pdfFilename = coverData.pdf_path ? coverData.pdf_path.split('/').pop() : null;
  const pdfUrl = pdfFilename ? `/api/resumes/cover-letters/${encodeURIComponent(pdfFilename)}?inline=1&t=${Date.now()}` : null;
  const hasOriginal = !!coverData.markdown_original && coverData.markdown_original !== coverData.markdown;
  const chatOn = isChatVisible('cover');
  const bodyGrid = chatOn ? '320px 1fr 1fr' : '1fr 1fr';

  const tones = [
    { value: 'warm', label: 'Warm and genuine' },
    { value: 'formal', label: 'Formal and direct' },
    { value: 'enthusiastic', label: 'Enthusiastic' },
    { value: 'concise', label: 'Concise' },
  ];

  c.innerHTML = `
    <div class="editor-shell" style="display:flex;flex-direction:column;height:calc(100vh - 120px);min-height:520px;gap:14px">
      <div class="editor-header" style="background:var(--bg1);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow-sm);display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between">
        <div style="flex:1;min-width:260px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
            <button class="btn btn-ghost btn-sm" onclick="showPage('pipeline')" title="Back">\u{2190}</button>
            <div style="font-size:18px;font-weight:700">\u{270F}\u{FE0F} Cover Letter Editor</div>
          </div>
          <div style="font-size:12px;color:var(--text2);font-weight:500;padding-left:4px">
            ${escapeHtml(listing.company || '')} \u00b7 ${escapeHtml(listing.role_title || '')}
          </div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
          <select id="coverToneSelect" class="fi" style="padding:7px 10px;font-size:12px;font-weight:600;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg2)" onchange="changeCoverTone(this.value)">
            ${tones.map(t => `<option value="${t.value}" ${t.value===currentTone?'selected':''}>${t.label}</option>`).join('')}
          </select>
          <button class="btn ${chatOn ? 'btn-primary' : 'btn-ghost'} btn-sm" onclick="chatToggle('cover')" title="Toggle the conversational AI editing panel">\u{1F4AC} Chat</button>
          <button class="btn btn-ghost btn-sm" onclick="regenerateCoverFromEditor()" id="editCoverRegenBtn" title="Run LLM again">\u{1F504} Regenerate</button>
          ${hasOriginal ? `<button class="btn btn-ghost btn-sm" onclick="revertCoverEdits()" title="Restore original">\u{23EA} Revert</button>` : ''}
          ${pdfUrl ? `<a class="btn btn-ghost btn-sm" href="${pdfUrl}" target="_blank" title="Open PDF in new tab">\u{2197} Open PDF</a>` : ''}
          ${pdfFilename ? `<a class="btn btn-ghost btn-sm" href="/api/resumes/cover-letters/${encodeURIComponent(pdfFilename)}" download title="Download">\u{2B07} Download</a>` : ''}
          <button class="btn btn-primary btn-sm" onclick="saveCoverEdits()" id="saveCoverBtn">\u{1F4BE} Save</button>
        </div>
      </div>

      <div class="editor-body" style="flex:1;display:grid;grid-template-columns:${bodyGrid};gap:14px;min-height:0">
        ${chatOn ? `<div id="chatPanel-cover" style="background:var(--bg1);border-radius:var(--radius);box-shadow:var(--shadow-sm);overflow:hidden;min-height:0"></div>` : ''}
        <div style="background:var(--bg1);border-radius:var(--radius);display:flex;flex-direction:column;overflow:hidden;box-shadow:var(--shadow-sm)">
          <div style="padding:10px 14px;border-bottom:1px solid var(--border);font-size:12px;font-weight:700;color:var(--text2);letter-spacing:.04em;text-transform:uppercase;display:flex;justify-content:space-between;align-items:center">
            <span>Markdown source</span>
            <span id="coverDirtyBadge" style="font-size:11px;color:var(--text3);font-weight:500"></span>
          </div>
          <textarea id="coverMdEditor"
                    style="flex:1;border:none;outline:none;padding:16px;font-family:'SF Mono',Menlo,Consolas,monospace;font-size:13px;line-height:1.55;resize:none;background:var(--bg1);color:var(--text1)"
                    spellcheck="false"
                    oninput="markCoverDirty()">${escapeHtml(coverData.markdown)}</textarea>
        </div>
        <div style="background:var(--bg2);border-radius:var(--radius);display:flex;flex-direction:column;overflow:hidden;box-shadow:var(--shadow-sm)">
          <div style="padding:10px 14px;border-bottom:1px solid var(--border);font-size:12px;font-weight:700;color:var(--text2);letter-spacing:.04em;text-transform:uppercase">
            PDF preview
          </div>
          <div style="flex:1;position:relative;min-height:0" id="coverPdfWrap">
            ${pdfUrl
              ? `<embed id="coverPdfEmbed" src="${pdfUrl}" type="application/pdf" style="width:100%;height:100%;border:none;background:#525659">`
              : `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text3);font-size:13px">No PDF rendered yet. Save to render.</div>`
            }
          </div>
        </div>
      </div>
    </div>`;

  window._editorState = {
    kind: 'cover',
    listingId: id,
    originalMarkdown: coverData.markdown,
    pdfFilename: pdfFilename,
    tone: currentTone,
  };

  // If chat is on, hydrate + render the panel.
  if (chatOn) {
    await loadChatFor(id);
    renderChatPanel(id, 'chatPanel-cover', 'cover_letter');
  }
}

// --- Resume editor actions ---
function markResumeDirty(){
  const badge = document.getElementById('resumeDirtyBadge');
  if (!badge) return;
  const ed = document.getElementById('resumeMdEditor');
  const st = window._editorState || {};
  badge.textContent = (ed && ed.value !== st.originalMarkdown) ? 'Unsaved changes' : '';
  badge.style.color = 'var(--orange, #b45309)';
}

async function saveResumeEdits(){
  const ed = document.getElementById('resumeMdEditor');
  const btn = document.getElementById('saveResumeBtn');
  if (!ed) return;
  const md = ed.value;
  if (!md.trim()) { alert('Markdown cannot be empty'); return; }
  const st = window._editorState;
  btn.disabled = true; btn.innerHTML = '\u{23F3} Saving...';
  try {
    const updated = await window.api.listings.saveTailoredResume(st.listingId, md);
    st.originalMarkdown = updated.markdown;
    st.pdfFilename = updated.pdf_path ? updated.pdf_path.split('/').pop() : st.pdfFilename;
    refreshResumePdfPreview();
    const badge = document.getElementById('resumeDirtyBadge');
    if (badge) { badge.textContent = 'Saved \u2713'; badge.style.color = 'var(--green)'; }
  } catch (err) {
    alert('Save failed: ' + err.message);
  } finally {
    btn.disabled = false; btn.innerHTML = '\u{1F4BE} Save';
  }
}

async function revertResumeEdits(){
  if (!confirm('Discard your edits and restore the original AI-tailored resume?')) return;
  const st = window._editorState;
  try {
    const updated = await window.api.listings.revertTailoredResume(st.listingId);
    const ed = document.getElementById('resumeMdEditor');
    if (ed) ed.value = updated.markdown;
    st.originalMarkdown = updated.markdown;
    st.pdfFilename = updated.pdf_path ? updated.pdf_path.split('/').pop() : st.pdfFilename;
    refreshResumePdfPreview();
    markResumeDirty();
  } catch (err) {
    alert('Revert failed: ' + err.message);
  }
}

async function changeTailoringIntensity(newIntensity){
  const st = window._editorState;
  if (!st || st.kind !== 'resume') return;
  if (st.intensity === newIntensity) return;
  const ed = document.getElementById('resumeMdEditor');
  const hasDirty = ed && ed.value !== st.originalMarkdown;
  const msg = hasDirty
    ? `Regenerate at "${newIntensity}" intensity? Your unsaved edits will be lost.`
    : `Regenerate this resume with "${newIntensity}" intensity? (Takes 30-50s)`;
  if (!confirm(msg)) return;
  await regenerateResumeFromEditor(newIntensity);
}

async function regenerateResumeFromEditor(intensityOverride){
  const st = window._editorState || { listingId: window._editorListingId };
  const intensity = intensityOverride || (st && st.intensity) || null;
  const btn = document.getElementById('editRegenBtn');
  if (btn) { btn.disabled = true; btn.innerHTML = '\u{23F3} Tailoring...'; }
  try {
    await window.api.listings.tailorListing(st.listingId, intensity);
    // Re-render entire editor with fresh data
    await renderResumeEditor(document.getElementById('content'));
  } catch (err) {
    alert('Regeneration failed: ' + err.message);
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{1F504} Regenerate'; }
  }
}

function refreshResumePdfPreview(){
  const st = window._editorState;
  if (!st || !st.pdfFilename) return;
  const embed = document.getElementById('resumePdfEmbed');
  const wrap = document.getElementById('resumePdfWrap');
  const newUrl = `/api/resumes/generated/${encodeURIComponent(st.pdfFilename)}?inline=1&t=${Date.now()}`;
  if (embed) {
    embed.src = newUrl;
  } else if (wrap) {
    wrap.innerHTML = `<embed id="resumePdfEmbed" src="${newUrl}" type="application/pdf" style="width:100%;height:100%;border:none;background:#525659">`;
  }
}

// --- Cover letter editor actions ---
function markCoverDirty(){
  const badge = document.getElementById('coverDirtyBadge');
  if (!badge) return;
  const ed = document.getElementById('coverMdEditor');
  const st = window._editorState || {};
  badge.textContent = (ed && ed.value !== st.originalMarkdown) ? 'Unsaved changes' : '';
  badge.style.color = 'var(--orange, #b45309)';
}

async function saveCoverEdits(){
  const ed = document.getElementById('coverMdEditor');
  const btn = document.getElementById('saveCoverBtn');
  if (!ed) return;
  const md = ed.value;
  if (!md.trim()) { alert('Markdown cannot be empty'); return; }
  const st = window._editorState;
  btn.disabled = true; btn.innerHTML = '\u{23F3} Saving...';
  try {
    const updated = await window.api.listings.saveCoverLetterMd(st.listingId, md);
    st.originalMarkdown = updated.markdown;
    st.pdfFilename = updated.pdf_path ? updated.pdf_path.split('/').pop() : st.pdfFilename;
    refreshCoverPdfPreview();
    const badge = document.getElementById('coverDirtyBadge');
    if (badge) { badge.textContent = 'Saved \u2713'; badge.style.color = 'var(--green)'; }
  } catch (err) {
    alert('Save failed: ' + err.message);
  } finally {
    btn.disabled = false; btn.innerHTML = '\u{1F4BE} Save';
  }
}

async function revertCoverEdits(){
  if (!confirm('Discard your edits and restore the original AI-generated cover letter?')) return;
  const st = window._editorState;
  try {
    const updated = await window.api.listings.revertCoverLetterMd(st.listingId);
    const ed = document.getElementById('coverMdEditor');
    if (ed) ed.value = updated.markdown;
    st.originalMarkdown = updated.markdown;
    st.pdfFilename = updated.pdf_path ? updated.pdf_path.split('/').pop() : st.pdfFilename;
    refreshCoverPdfPreview();
    markCoverDirty();
  } catch (err) {
    alert('Revert failed: ' + err.message);
  }
}

async function changeCoverTone(newTone){
  const st = window._editorState;
  if (!st || st.kind !== 'cover') return;
  if (st.tone === newTone) return;
  const ed = document.getElementById('coverMdEditor');
  const hasDirty = ed && ed.value !== st.originalMarkdown;
  const msg = hasDirty
    ? `Regenerate with tone "${newTone}"? Your unsaved edits will be lost.`
    : `Regenerate this cover letter with tone "${newTone}"? (Takes 20-30s)`;
  if (!confirm(msg)) {
    // revert select back
    const sel = document.getElementById('coverToneSelect');
    if (sel) sel.value = st.tone;
    return;
  }
  await regenerateCoverFromEditor(newTone);
}

async function regenerateCoverFromEditor(toneOverride){
  const st = window._editorState || { listingId: window._editorListingId };
  const tone = toneOverride || (st && st.tone) || null;
  const btn = document.getElementById('editCoverRegenBtn');
  if (btn) { btn.disabled = true; btn.innerHTML = '\u{23F3} Writing...'; }
  try {
    await window.api.listings.coverLetterListing(st.listingId, tone);
    await renderCoverLetterEditor(document.getElementById('content'));
  } catch (err) {
    alert('Regeneration failed: ' + err.message);
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{1F504} Regenerate'; }
  }
}

function refreshCoverPdfPreview(){
  const st = window._editorState;
  if (!st || !st.pdfFilename) return;
  const embed = document.getElementById('coverPdfEmbed');
  const wrap = document.getElementById('coverPdfWrap');
  const newUrl = `/api/resumes/cover-letters/${encodeURIComponent(st.pdfFilename)}?inline=1&t=${Date.now()}`;
  if (embed) {
    embed.src = newUrl;
  } else if (wrap) {
    wrap.innerHTML = `<embed id="coverPdfEmbed" src="${newUrl}" type="application/pdf" style="width:100%;height:100%;border:none;background:#525659">`;
  }
}

// ============================================================================
// Backup / Restore / Reset scanner defaults
// ============================================================================
function exportLaunchpadBackup(){
  // Stream the download via a hidden anchor to carry session cookies
  const a = document.createElement('a');
  a.href = window.api.backup.exportUrl();
  a.download = '';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 500);
}

async function importLaunchpadBackup(file){
  if (!file) return;
  if (!confirm(`Restore from "${file.name}"?\n\nThis will OVERWRITE your current database and all user files. A pre-restore backup copy will be kept on disk so you can roll back manually.\n\nAfter restore you must restart the server and re-enter your LLM key and reconnect Gmail.`)) return;
  try {
    const r = await window.api.backup.import(file);
    alert('Restore complete.\n\n' + (r.message || 'Please restart the server now.'));
  } catch (err) {
    alert('Restore failed: ' + err.message);
  }
}

async function resetScannerDefaults(){
  if (!confirm('Reset scanner title filter and tracked companies to factory defaults?\n\nYour current filter keywords and any custom companies you added will be replaced.')) return;
  try {
    const r = await window.api.backup.resetScannerDefaults();
    alert(`Reset complete.\nCompanies loaded: ${r.companies_loaded}`);
    // refresh settings page to pick up new keywords
    renderSettings(document.getElementById('content'));
  } catch (err) {
    alert('Reset failed: ' + err.message);
  }
}

// ============================================================================
// Scoring weights UI
// ============================================================================
const WEIGHT_DIMS = [
  { key: 'role_match', label: 'Role Match', desc: 'Function and domain fit' },
  { key: 'seniority_match', label: 'Seniority', desc: 'Level vs company size' },
  { key: 'skills', label: 'Skills', desc: 'Technical overlap' },
  { key: 'comp', label: 'Comp', desc: 'Salary vs target' },
  { key: 'growth', label: 'Growth', desc: 'Scope trajectory' },
  { key: 's_curve', label: 'S-Curve', desc: 'Company inflection potential' },
  { key: 'culture', label: 'Culture', desc: 'Work style and values' },
  { key: 'location', label: 'Location', desc: 'Geographic fit' },
];

const WEIGHT_PRESETS = {
  equal: { role_match: .125, seniority_match: .125, skills: .125, comp: .125, growth: .125, s_curve: .125, culture: .125, location: .125 },
  startup: { role_match: .10, seniority_match: .10, skills: .10, comp: .05, growth: .20, s_curve: .25, culture: .15, location: .05 },
  comp: { role_match: .10, seniority_match: .10, skills: .15, comp: .30, growth: .10, s_curve: .05, culture: .10, location: .10 },
  lateral: { role_match: .15, seniority_match: .25, skills: .15, comp: .15, growth: .10, s_curve: .05, culture: .10, location: .05 },
};

function normalizeWeights(w) {
  const out = {};
  let total = 0;
  for (const d of WEIGHT_DIMS) {
    const v = Number(w[d.key]) || 0;
    out[d.key] = Math.max(0, v);
    total += out[d.key];
  }
  if (total <= 0) {
    // Fallback to equal
    for (const d of WEIGHT_DIMS) out[d.key] = 1 / WEIGHT_DIMS.length;
  } else {
    for (const d of WEIGHT_DIMS) out[d.key] = out[d.key] / total;
  }
  return out;
}

function renderWeightSliders() {
  const container = document.getElementById('weightSliders');
  if (!container) return;
  const w = window._scoringWeights || {};
  container.innerHTML = WEIGHT_DIMS.map(d => {
    const pct = Math.round((w[d.key] || 0) * 1000) / 10;
    return `
      <div style="display:grid;grid-template-columns:140px 1fr 60px;gap:10px;align-items:center;padding:6px 0">
        <div>
          <div style="font-size:13px;font-weight:700;color:var(--text)">${escapeHtml(d.label)}</div>
          <div style="font-size:10px;color:var(--text3);font-weight:500">${escapeHtml(d.desc)}</div>
        </div>
        <input type="range" min="0" max="50" step="0.5" value="${pct}"
               data-weight-key="${d.key}"
               oninput="onWeightSlider('${d.key}', this.value)"
               style="width:100%;accent-color:var(--primary)">
        <div style="font-size:13px;font-weight:800;color:var(--primary);text-align:right" id="wpct-${d.key}">${pct.toFixed(1)}%</div>
      </div>`;
  }).join('');
  updateWeightTotalDisplay();
}

function onWeightSlider(key, rawVal) {
  const pct = Number(rawVal) || 0;
  window._scoringWeights[key] = pct / 100;
  const label = document.getElementById(`wpct-${key}`);
  if (label) label.textContent = pct.toFixed(1) + '%';
  updateWeightTotalDisplay();
}

function updateWeightTotalDisplay() {
  const totalEl = document.getElementById('weightTotal');
  if (!totalEl) return;
  const total = WEIGHT_DIMS.reduce((s, d) => s + (window._scoringWeights[d.key] || 0), 0);
  const pct = total * 100;
  totalEl.textContent = pct.toFixed(1) + '%';
  if (Math.abs(pct - 100) < 0.5) {
    totalEl.style.color = 'var(--green)';
  } else if (pct <= 0) {
    totalEl.style.color = 'var(--red)';
  } else {
    totalEl.style.color = 'var(--orange, #b45309)';
  }
}

function applyWeightPreset(name) {
  const preset = WEIGHT_PRESETS[name];
  if (!preset) return;
  window._scoringWeights = { ...preset };
  renderWeightSliders();
}

// ============================================================================
// "Passed" page — listings the candidate declined to pursue
// ============================================================================
const PASS_REASON_LABELS_PAGE = {
  level_mismatch: 'Wrong seniority',
  comp_too_low: 'Comp too low',
  stage_mismatch: 'Stage mismatch',
  domain_mismatch: 'Wrong function',
  location: 'Location',
  culture_fit: 'Culture / leadership',
  scope_too_narrow: 'Scope too narrow',
  founder_market_fit: 'Founder fit',
  timing: 'Timing',
  other: 'Other',
};

async function renderPassed(c) {
  registerTableSortReload('passed_listings', () => renderPassed(c));
  c.innerHTML = `
    <div class="page-header">
      <div>
        <div class="page-title">Passed \u{1F6AB}</div>
        <div class="page-subtitle">Roles you chose not to pursue, with reasons. Used to calibrate AI scoring once you hit 15 passes.</div>
      </div>
    </div>
    <div id="passedBody"><div class="empty-state"><div class="es-icon">\u{23F3}</div><div class="es-desc">Loading...</div></div></div>`;
  try {
    const data = await window.api.listings.listPassed();
    const body = document.getElementById('passedBody');
    if (!data.items.length) {
      body.innerHTML = `
        <div class="empty-state" style="padding:60px 20px">
          <div class="es-icon">\u{1F6AB}</div>
          <div class="es-title">No passes yet</div>
          <div class="es-desc">When you decide a role isn't for you, click "Pass" on its detail panel to add it here.</div>
        </div>`;
      return;
    }
    const threshold = data.threshold || 15;
    const active = data.calibration_active;
    const progressPct = Math.min(100, Math.round((data.total / threshold) * 100));
    const progress = active
      ? `<div style="background:var(--green-soft,#dcfce7);border:1px solid var(--green);border-radius:var(--radius);padding:12px 16px;margin-bottom:18px;display:flex;gap:10px;align-items:center">
          <div style="font-size:20px">\u{1F3AF}</div>
          <div style="flex:1">
            <div style="font-size:13px;font-weight:700;color:var(--green)">Calibration is active</div>
            <div style="font-size:11px;color:var(--text2);font-weight:500">Your ${data.total} pass decisions are now informing AI evaluations. Turn this off in Settings if you want to reset.</div>
          </div>
        </div>`
      : `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;margin-bottom:18px">
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:6px">
            <div style="font-size:20px">\u{1F4CA}</div>
            <div style="flex:1;font-size:13px;font-weight:700">Calibration progress: ${data.total} / ${threshold}</div>
            <div style="font-size:11px;color:var(--text3);font-weight:600">${threshold - data.total} to go</div>
          </div>
          <div style="height:6px;background:var(--bg);border-radius:3px;overflow:hidden">
            <div style="width:${progressPct}%;height:100%;background:linear-gradient(90deg,var(--primary),var(--pink, #ec4899));transition:width .3s"></div>
          </div>
          <div style="font-size:11px;color:var(--text3);font-weight:500;margin-top:8px">Once you cross ${threshold} passes, your pass history starts calibrating AI evaluations automatically.</div>
        </div>`;

    const reasonChips = Object.entries(data.reason_counts)
      .sort((a, b) => b[1] - a[1])
      .map(([r, n]) => `<span class="tag" style="margin-right:6px;background:var(--bg2)">${escapeHtml(PASS_REASON_LABELS_PAGE[r] || r)}: <strong>${n}</strong></span>`)
      .join('');

    const sortedItems = applyTableSort(data.items, 'passed_listings', 'passed_at', 'desc', 'date');
    const rowsHtml = sortedItems.map(p => {
      const scoreCol = p.score != null ? p.score.toFixed(1) : '\u2014';
      const scoreClr = p.score == null ? 'var(--text3)' : p.score >= 4 ? 'var(--green)' : p.score >= 3.5 ? '#a16207' : 'var(--red)';
      const when = p.passed_at ? new Date(p.passed_at).toLocaleDateString() : '';
      return `<tr onclick="openDetail(${p.listing_id})" style="cursor:pointer">
        <td style="font-weight:700;color:var(--primary)">${escapeHtml(p.company)}</td>
        <td>${escapeHtml(p.role_title)}</td>
        <td style="font-weight:800;color:${scoreClr}">${scoreCol}</td>
        <td><span class="tag" style="background:var(--bg2)">${escapeHtml(PASS_REASON_LABELS_PAGE[p.pass_reason] || p.pass_reason)}</span></td>
        <td style="font-size:12px;color:var(--text2);font-weight:500;max-width:280px">${escapeHtml((p.pass_note || '').slice(0,120))}</td>
        <td style="font-size:11px;color:var(--text3);font-weight:500">${escapeHtml(when)}</td>
        <td style="white-space:nowrap">
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:11px;color:var(--text2);font-weight:600;cursor:pointer" onclick="event.stopPropagation()">
            <input type="checkbox" ${p.use_for_calibration ? 'checked' : ''} onchange="togglePassCalibration(${p.listing_id}, this.checked)">
            calibrate
          </label>
          <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();reconsiderFromRow(${p.listing_id})" title="Reconsider">\u{21A9}\uFE0F</button>
        </td>
      </tr>`;
    }).join('');

    body.innerHTML = `
      ${progress}
      <div style="margin-bottom:14px">${reasonChips}</div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            ${sortableHeader('passed_listings', 'company', 'Company', 'string')}
            ${sortableHeader('passed_listings', 'role_title', 'Role', 'string')}
            ${sortableHeader('passed_listings', 'score', 'Score', 'number')}
            ${sortableHeader('passed_listings', 'pass_reason', 'Reason', 'string')}
            <th>Note</th>
            ${sortableHeader('passed_listings', 'passed_at', 'Date', 'date')}
            <th></th>
          </tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>`;
  } catch (err) {
    document.getElementById('passedBody').innerHTML = `<div class="empty-state"><div class="es-icon">\u{26A0}</div><div class="es-desc">${escapeHtml(err.message)}</div></div>`;
  }
}

async function togglePassCalibration(listingId, useForCalibration) {
  try {
    await window.api.listings.togglePassCalibration(listingId, useForCalibration);
  } catch (err) {
    alert('Update failed: ' + err.message);
    renderPassed(document.getElementById('content'));
  }
}

async function reconsiderFromRow(listingId) {
  if (!confirm('Bring this listing back into your active pipeline?')) return;
  try {
    await window.api.listings.reconsider(listingId);
    renderPassed(document.getElementById('content'));
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    alert('Reconsider failed: ' + err.message);
  }
}

// ============================================================================
// Pass-history calibration card in Settings
// ============================================================================
async function loadPassCalibrationCard(settings) {
  const container = document.getElementById('passCalibrationCard');
  if (!container) return;
  try {
    const data = await window.api.listings.listPassed();
    const threshold = data.threshold || 15;
    const progressPct = Math.min(100, Math.round((data.total / threshold) * 100));
    const pref = (settings && settings.pass_calibration_preference) || 'auto';

    const progress = data.calibration_active
      ? `<div style="background:var(--green-soft,#dcfce7);border:1px solid var(--green);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:14px;display:flex;gap:10px;align-items:center">
          <div style="font-size:18px">\u{1F3AF}</div>
          <div style="flex:1">
            <div style="font-size:12px;font-weight:700;color:var(--green)">Calibration is active</div>
            <div style="font-size:11px;color:var(--text2);font-weight:500">${data.total} pass decisions are informing your evaluations.</div>
          </div>
        </div>`
      : `<div style="background:var(--bg2);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:14px">
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:6px">
            <div style="font-size:12px;font-weight:700">Progress: ${data.total} / ${threshold}</div>
            <div style="font-size:11px;color:var(--text3);font-weight:600;margin-left:auto">${Math.max(0, threshold - data.total)} to go</div>
          </div>
          <div style="height:5px;background:var(--bg);border-radius:3px;overflow:hidden">
            <div style="width:${progressPct}%;height:100%;background:linear-gradient(90deg,var(--primary),var(--pink, #ec4899))"></div>
          </div>
        </div>`;

    const prefControl = `
      <div class="fg">
        <label class="fl">Calibration preference</label>
        <select class="fi" id="set-pass-calibration-pref">
          <option value="auto" ${pref==='auto'?'selected':''}>Auto (turn on at ${threshold} passes)</option>
          <option value="on" ${pref==='on'?'selected':''}>Always on</option>
          <option value="off" ${pref==='off'?'selected':''}>Always off</option>
        </select>
      </div>
      <div class="fg">
        <label class="fl">Threshold (when auto-enables)</label>
        <input class="fi" type="number" id="set-pass-threshold" min="3" max="100" value="${threshold}">
      </div>`;

    const chips = Object.entries(data.reason_counts)
      .sort((a, b) => b[1] - a[1])
      .map(([r, n]) => `<span class="tag" style="margin-right:6px;background:var(--bg2)">${escapeHtml(PASS_REASON_LABELS_PAGE[r] || r)}: <strong>${n}</strong></span>`)
      .join('');

    const rowsHtml = data.items.length
      ? data.items.map(p => {
          const score = p.score != null ? p.score.toFixed(1) : '\u2014';
          const when = p.passed_at ? new Date(p.passed_at).toLocaleDateString() : '';
          return `
            <tr>
              <td style="font-weight:600">${escapeHtml(p.company)}</td>
              <td style="font-size:12px">${escapeHtml(p.role_title)}</td>
              <td style="font-size:12px;color:var(--text3);font-weight:600">${score}</td>
              <td style="font-size:11px"><span class="tag" style="background:var(--bg2)">${escapeHtml(PASS_REASON_LABELS_PAGE[p.pass_reason] || p.pass_reason)}</span></td>
              <td style="font-size:11px;color:var(--text3)">${escapeHtml(when)}</td>
              <td style="white-space:nowrap">
                <label style="display:inline-flex;align-items:center;gap:3px;font-size:10px;color:var(--text2);font-weight:600;cursor:pointer">
                  <input type="checkbox" ${p.use_for_calibration ? 'checked' : ''} onchange="togglePassCalibration(${p.listing_id}, this.checked)">
                  use
                </label>
                <button class="btn btn-ghost btn-sm" onclick="deletePassFromSettings(${p.listing_id})" title="Remove this pass from history">\u{1F5D1}</button>
              </td>
            </tr>`;
        }).join('')
      : '<tr><td colspan="6" style="text-align:center;color:var(--text3);font-size:12px;padding:20px">No passes yet</td></tr>';

    const actionsRow = data.items.length
      ? `<div style="margin-top:12px;text-align:right">
          <button class="btn btn-danger btn-sm" onclick="clearAllPassesFromSettings()">\u{21BB} Clear all pass history</button>
        </div>`
      : '';

    container.innerHTML = `
      ${progress}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        ${prefControl}
      </div>
      ${chips ? `<div style="margin:8px 0 12px">${chips}</div>` : ''}
      <div style="max-height:280px;overflow-y:auto;border:1px solid var(--border);border-radius:var(--radius-sm)">
        <table style="width:100%">
          <thead>
            <tr style="background:var(--bg2)">
              <th style="text-align:left;padding:6px 10px;font-size:11px;color:var(--text3);font-weight:700;text-transform:uppercase">Company</th>
              <th style="text-align:left;padding:6px 10px;font-size:11px;color:var(--text3);font-weight:700;text-transform:uppercase">Role</th>
              <th style="text-align:left;padding:6px 10px;font-size:11px;color:var(--text3);font-weight:700;text-transform:uppercase">Score</th>
              <th style="text-align:left;padding:6px 10px;font-size:11px;color:var(--text3);font-weight:700;text-transform:uppercase">Reason</th>
              <th style="text-align:left;padding:6px 10px;font-size:11px;color:var(--text3);font-weight:700;text-transform:uppercase">Date</th>
              <th></th>
            </tr>
          </thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
      ${actionsRow}`;
  } catch (err) {
    container.innerHTML = `<div style="padding:12px;color:var(--red);font-size:12px;font-weight:600">Couldn't load pass history: ${escapeHtml(err.message)}</div>`;
  }
}

async function deletePassFromSettings(listingId) {
  if (!confirm('Remove this pass from your history? The listing will return to the evaluated column.')) return;
  try {
    await window.api.listings.reconsider(listingId);
    const s = await window.api.settings.get();
    loadPassCalibrationCard(s);
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

async function clearAllPassesFromSettings() {
  if (!confirm('Clear ALL pass history? This removes every pass record — the listings themselves stay in your pipeline as "evaluated".')) return;
  if (!confirm('Are you sure? This can\'t be undone.')) return;
  try {
    const r = await window.api.listings.clearAllPasses();
    alert(`Cleared ${r.cleared} pass record${r.cleared===1?'':'s'}.`);
    const s = await window.api.settings.get();
    loadPassCalibrationCard(s);
  } catch (err) {
    alert('Clear failed: ' + err.message);
  }
}

function gmailExtractionStatsHTML(accounts) {
  if (!accounts || !accounts.length) return '';
  const rows = accounts.map(a => {
    const lifetime = a.lifetime_listings_extracted || 0;
    const lastCount = a.last_extraction_count || 0;
    const lastAt = a.last_extraction_at ? timeAgo(new Date(a.last_extraction_at)) : 'never';
    const lastSync = a.last_synced_at ? timeAgo(new Date(a.last_synced_at)) : 'never';
    return `
      <div style="display:flex;align-items:center;gap:14px;padding:8px 14px;border-top:1px solid var(--border)">
        <div style="font-size:13px;font-weight:700;flex:1">${escapeHtml(a.email)}</div>
        <div style="font-size:11px;color:var(--text2);font-weight:600">\u{1F504} synced ${lastSync}</div>
        <div style="font-size:11px;color:var(--text2);font-weight:600">\u{2728} last extraction: ${lastAt}${lastCount ? ` (${lastCount})` : ''}</div>
        <div style="font-size:13px;font-weight:800;color:var(--green)">${lifetime}</div>
        <div style="font-size:10px;color:var(--text3);font-weight:700;text-transform:uppercase;letter-spacing:.04em">listings all-time</div>
      </div>`;
  }).join('');
  return `
    <div style="background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:14px;overflow:hidden">
      <div style="padding:10px 14px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--text3);background:var(--bg2)">Extraction stats</div>
      ${rows}
    </div>`;
}

async function reExtractEmail(emailId, btn) {
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '\u{23F3} Re-extracting...';
  try {
    const r = await window.api.gmail.extractFromEmail(emailId, true);
    const count = (r.extracted || []).length;
    // Invalidate the expanded-view cache for this email
    const exp = document.getElementById(`gmailExp-${emailId}`);
    if (exp) exp.dataset.loaded = '';
    if (count === 0) {
      btn.innerHTML = '\u{2139}\uFE0F Still none';
      setTimeout(() => { btn.disabled = false; btn.innerHTML = original; }, 2200);
      return;
    }
    btn.innerHTML = `\u{2713} Added ${r.new_listings_created} of ${count}`;
    btn.style.background = 'var(--green)';
    btn.style.color = 'white';
    setTimeout(() => {
      loadGmailMessages();
      if (typeof updateNavBadges === 'function') updateNavBadges();
    }, 1500);
  } catch (err) {
    alert('Re-extraction failed: ' + err.message);
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

async function reExtractAllZero() {
  if (!confirm(
    'Re-run extraction on every LinkedIn / recruiter email that returned zero listings previously?\n\n' +
    'Useful after parser improvements (e.g., the URL-preservation fix). Uses LLM credits — typically $0.02 per email.'
  )) return;
  try {
    const r = await window.api.gmail.extractPending(true);
    const msg = `Reprocessed ${r.processed_emails} email${r.processed_emails===1?'':'s'}.\n` +
                `Extracted ${r.total_extracted} listing${r.total_extracted===1?'':'s'}.\n` +
                `${r.total_new_listings} new listing${r.total_new_listings===1?'':'s'} added.` +
                (r.errors && r.errors.length ? `\n\n\u26A0 ${r.errors.length} failed.` : '');
    alert(msg);
    loadGmailMessages();
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    alert('Sweep failed: ' + err.message);
  }
}

// ============================================================================
// Retroactive title-filter cleanup
// ============================================================================
async function cleanupByFilter() {
  let preview;
  try {
    preview = await window.api.listings.filterSweepPreview();
  } catch (err) {
    alert('Could not preview filter: ' + err.message);
    return;
  }
  if (!preview.would_fail || preview.would_fail.length === 0) {
    alert(`All ${preview.passed_count} active listings pass your current filter. Nothing to clean up.`);
    return;
  }
  // Show preview in a modal so the user can review before committing
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  const rows = preview.would_fail.slice(0, 50).map(x => `
    <tr>
      <td style="font-weight:600">${escapeHtml(x.company)}</td>
      <td style="font-size:12px">${escapeHtml(x.role_title)}</td>
      <td><span class="sb sb-new">${escapeHtml(x.status)}</span></td>
      <td style="font-size:11px;color:var(--red);font-weight:600">${escapeHtml(x.reason)}</td>
    </tr>`).join('');
  overlay.innerHTML = `
    <div class="modal" style="width:720px;max-width:95vw">
      <div class="modal-head">
        <div class="modal-title">\u{1F9F9} Apply title filter to pipeline</div>
        <button class="dp-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="font-size:13px;color:var(--text2);font-weight:500;margin-bottom:12px;line-height:1.55">
          ${preview.would_fail.length} of ${preview.total_scanned} active listings would fail your current positive / negative keyword filter.
          They'll be moved to <strong>Passed</strong> with reason <code>domain_mismatch</code> and a note about which keyword matched.
          You can reconsider any of them later from the Passed page.
        </div>
        <div class="tbl-wrap" style="max-height:360px;overflow:auto">
          <table>
            <thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Why it fails</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        ${preview.would_fail.length > 50 ? `<div style="font-size:11px;color:var(--text3);margin-top:8px">+ ${preview.would_fail.length - 50} more not shown</div>` : ''}
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn btn-danger" onclick="confirmCleanup(this)">\u{1F9F9} Pass ${preview.would_fail.length} listing${preview.would_fail.length===1?'':'s'}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function confirmCleanup(btn) {
  btn.disabled = true;
  btn.innerHTML = '\u{23F3} Applying...';
  try {
    const r = await window.api.listings.filterSweepApply();
    btn.closest('.modal-overlay').remove();
    alert(`Moved ${r.passed_now} listing${r.passed_now===1?'':'s'} to Passed.\n\nYou can reconsider them any time from the Passed page.`);
    renderListings(document.getElementById('content'));
    if (typeof updateNavBadges === 'function') updateNavBadges();
  } catch (err) {
    alert('Cleanup failed: ' + err.message);
    btn.disabled = false;
    btn.innerHTML = '\u{1F9F9} Apply filter';
  }
}

async function evaluateAllNewFromAllListings() {
  // Reuse the same bulk-eval handler from pipeline — works across any page
  if (typeof evaluateAllNew === 'function') {
    await evaluateAllNew();
    // Refresh the table view after
    renderListings(document.getElementById('content'));
  }
}

async function addFilteredToPipeline(emailId, filteredIdx, btn) {
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '\u{23F3} Adding...';
  try {
    const r = await window.api.gmail.promoteFiltered(emailId, filteredIdx);
    btn.innerHTML = '\u{2713} Added';
    btn.style.background = 'var(--green)';
    btn.style.color = 'white';
    // Invalidate the expanded view cache for this email + refresh the feed
    const exp = document.getElementById(`gmailExp-${emailId}`);
    if (exp) exp.dataset.loaded = '';
    setTimeout(() => {
      loadGmailMessages();
      // Re-open the expanded view for continuity
      setTimeout(() => toggleGmailExpand(emailId), 400);
      if (typeof updateNavBadges === 'function') updateNavBadges();
    }, 700);
  } catch (err) {
    alert('Add failed: ' + err.message);
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

// ============================================================================
// Per-listing evaluation lock (client side) — keeps multiple entry points
// (pipeline card, listings row, detail panel) from double-firing /evaluate.
// Authoritative lock is server-side via listing.evaluation_in_progress.
// ============================================================================
window._evaluatingIds = window._evaluatingIds || new Set();

function isEvaluating(listingId) {
  return window._evaluatingIds && window._evaluatingIds.has(Number(listingId));
}

function markEvaluatingStart(listingId) {
  if (!window._evaluatingIds) window._evaluatingIds = new Set();
  window._evaluatingIds.add(Number(listingId));
}

function markEvaluatingEnd(listingId) {
  if (!window._evaluatingIds) return;
  window._evaluatingIds.delete(Number(listingId));
}


// ============================================================================
// Resume / Cover-Letter chat editor (shared thread per listing — Option C)
// ============================================================================
// All chat state lives in window._chatState keyed by listing id, so the panel
// rehydrates correctly when the user flips between resume and cover letter
// editors for the same listing without losing pending proposals.
window._chatState = window._chatState || {};

function chatStateFor(listingId) {
  const key = String(listingId);
  if (!window._chatState[key]) {
    window._chatState[key] = {
      history: [],            // full history from server
      loading: false,
      pendingSend: false,
      visible: false,         // user has toggled the panel on
      scope: 'resume',        // default scope for new turns; updated to current editor
      onboardingDismissed: false,
    };
  }
  return window._chatState[key];
}

async function loadChatFor(listingId) {
  const st = chatStateFor(listingId);
  st.loading = true;
  try {
    const r = await window.api.listings.getChat(listingId);
    st.history = r.history || [];
    st.onboardingDismissed = !!r.onboarding_dismissed;
    st.editLogSize = r.edit_log_size || 0;
  } catch (err) {
    console.warn('Chat load failed:', err);
    st.history = [];
  } finally {
    st.loading = false;
  }
}

function renderChatPanel(listingId, containerId, defaultScope) {
  const st = chatStateFor(listingId);
  st.scope = defaultScope || st.scope;
  const el = document.getElementById(containerId);
  if (!el) return;

  const onboardingBanner = !st.onboardingDismissed
    ? `<div id="chatOnboarding" style="margin:10px;padding:10px 12px;background:var(--primary-soft);border-left:3px solid var(--primary);border-radius:var(--radius-sm);font-size:11px;line-height:1.5;color:var(--text);font-weight:500">
        <div style="font-weight:700;margin-bottom:4px">\u{1F4AC} Chat when intent beats editing</div>
        Use chat for intent-level changes like <em>"tighten the Amazon bullets"</em> or <em>"make it sound more founder-ish"</em>. For simple word swaps (<em>"developed" \u2192 "built"</em>), just edit the markdown directly \u2014 it's faster, free, and instant.
        <br><br>History is saved per-listing, so this conversation picks up where you left off whenever you reopen this resume.
        <div style="margin-top:8px;text-align:right">
          <button class="btn btn-primary btn-sm" onclick="dismissChatOnboarding()">Got it</button>
        </div>
      </div>`
    : '';

  const messagesHtml = st.history.length === 0
    ? `<div style="padding:30px 14px;text-align:center;color:var(--text3);font-size:12px;font-weight:500">
        <div style="font-size:32px;margin-bottom:8px">\u{1F4AC}</div>
        Ask for intent-level changes. Try "tighten the summary to 2 lines" or "emphasize the P&L story in the cover letter."
      </div>`
    : st.history.map((turn, idx) => chatTurnHTML(listingId, idx, turn)).join('');

  el.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:var(--bg1);border-radius:var(--radius);overflow:hidden">
      <div style="display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--bg2)">
        <span style="font-size:12px;font-weight:700;flex:1">\u{1F4AC} Chat</span>
        ${st.editLogSize > 0
          ? `<button class="btn btn-ghost btn-sm" style="padding:2px 8px;font-size:10px" onclick="chatUndoLast(${listingId})" title="Undo the most recent applied edit">\u{21A9}\uFE0F Undo</button>`
          : ''}
        ${st.history.length
          ? `<button class="btn btn-ghost btn-sm" style="padding:2px 8px;font-size:10px" onclick="chatClearHistory(${listingId})" title="Clear the conversation (does not touch applied edits)">\u{1F9F9} Clear</button>`
          : ''}
      </div>
      ${onboardingBanner}
      <div id="chatMessages-${listingId}" style="flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:10px">
        ${messagesHtml}
      </div>
      <div style="border-top:1px solid var(--border);padding:10px;background:var(--bg2)">
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          <label style="font-size:10px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.04em">Scope:</label>
          <select id="chatScope-${listingId}" class="fi" style="padding:4px 8px;font-size:11px;font-weight:600;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg1)">
            <option value="resume" ${st.scope === 'resume' ? 'selected' : ''}>Resume only</option>
            <option value="cover_letter" ${st.scope === 'cover_letter' ? 'selected' : ''}>Cover letter only</option>
            <option value="both" ${st.scope === 'both' ? 'selected' : ''}>Both docs (strategy)</option>
          </select>
        </div>
        <textarea id="chatInput-${listingId}" rows="2" placeholder="Ask for intent-level changes like 'tighten the summary'. For a word swap, just edit the markdown directly."
          style="width:100%;padding:8px 10px;font-size:12px;border-radius:var(--radius-sm);border:1px solid var(--border);resize:vertical;font-family:inherit;background:var(--bg1);color:var(--text)"
          onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();chatSubmit(${listingId})}"></textarea>
        <div id="chatNudge-${listingId}" style="margin-top:6px"></div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
          <div style="font-size:10px;color:var(--text3);font-weight:500">Enter = send, Shift+Enter = newline</div>
          <button class="btn btn-primary btn-sm" onclick="chatSubmit(${listingId})" id="chatSendBtn-${listingId}">\u{27A4} Send</button>
        </div>
      </div>
    </div>`;
  // Scroll to newest
  const msgs = document.getElementById(`chatMessages-${listingId}`);
  if (msgs) msgs.scrollTop = msgs.scrollHeight;
  // Attach word-swap detection on input
  const input = document.getElementById(`chatInput-${listingId}`);
  if (input) input.addEventListener('input', () => chatCheckWordSwap(listingId));
  // Scope selector persists to state
  const sel = document.getElementById(`chatScope-${listingId}`);
  if (sel) sel.addEventListener('change', (e) => { chatStateFor(listingId).scope = e.target.value; });
}

function chatTurnHTML(listingId, idx, turn) {
  if (turn.role === 'system') {
    return `<div style="padding:6px 10px;background:var(--bg2);border-radius:var(--radius-sm);font-size:11px;color:var(--text3);font-weight:500;font-style:italic;text-align:center">${escapeHtml(turn.content || '')}</div>`;
  }
  const isUser = turn.role === 'user';
  const scopeLabel = { resume: 'resume', cover_letter: 'cover letter', both: 'both docs' }[turn.scope] || turn.scope;
  const bubble = isUser
    ? `<div style="align-self:flex-end;max-width:88%;padding:8px 12px;background:var(--primary);color:white;border-radius:12px 12px 2px 12px;font-size:12px;line-height:1.5;word-wrap:break-word;overflow-wrap:anywhere">
        ${escapeHtml(turn.content)}
        <div style="font-size:10px;color:rgba(255,255,255,0.7);margin-top:3px">${escapeHtml(scopeLabel)}</div>
       </div>`
    : `<div style="align-self:flex-start;max-width:92%;padding:8px 12px;background:var(--bg2);color:var(--text);border-radius:12px 12px 12px 2px;font-size:12px;line-height:1.5;word-wrap:break-word;overflow-wrap:anywhere">
        ${escapeHtml(turn.content)}
       </div>`;
  const edits = (turn.proposed_edits || []);
  const editsHtml = edits.length
    ? `<div style="align-self:stretch;display:flex;flex-direction:column;gap:6px;margin-top:2px">
        ${edits.map(e => chatEditCardHTML(listingId, idx, e)).join('')}
       </div>`
    : '';
  return `<div style="display:flex;flex-direction:column;gap:4px">${bubble}${editsHtml}</div>`;
}

function chatEditCardHTML(listingId, turnIdx, edit) {
  const applied = !!edit.applied_at;
  const rejected = !!edit.rejected_at;
  const applicable = edit.applicable !== false;
  const targetLabel = edit.target === 'resume' ? 'Resume' : 'Cover letter';
  const status = applied
    ? `<span style="font-size:10px;font-weight:700;color:var(--green)">\u{2713} Applied</span>`
    : rejected
      ? `<span style="font-size:10px;font-weight:700;color:var(--text3)">\u{2715} Rejected</span>`
      : applicable
        ? ''
        : `<span style="font-size:10px;font-weight:700;color:var(--red)" title="The original text no longer matches the current markdown">\u{26A0} Stale</span>`;
  const actions = (applied || rejected)
    ? ''
    : `<div style="display:flex;gap:6px;margin-top:6px">
        <button class="btn btn-primary btn-sm" style="padding:3px 10px;font-size:11px" ${applicable ? '' : 'disabled'} onclick="chatApplyEdit(${listingId}, ${turnIdx}, '${edit.id}')">\u{2713} Apply</button>
        <button class="btn btn-ghost btn-sm" style="padding:3px 10px;font-size:11px" onclick="chatRejectEdit(${listingId}, ${turnIdx}, '${edit.id}')">\u{2715} Reject</button>
       </div>`;
  return `
    <div style="border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 12px;background:var(--bg1);font-size:11px;line-height:1.5">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:6px">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--primary)">\u{1F4DD} ${escapeHtml(targetLabel)}${edit.section ? ' \u00b7 ' + escapeHtml(edit.section) : ''}</div>
        ${status}
      </div>
      ${edit.rationale ? `<div style="font-size:11px;color:var(--text2);font-weight:500;margin-bottom:6px">${escapeHtml(edit.rationale)}</div>` : ''}
      <div style="display:flex;flex-direction:column;gap:4px">
        <div style="padding:6px 8px;background:#fee2e2;border-radius:4px;color:#991b1b;font-size:11px;line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere">\u2212 ${escapeHtml(edit.before)}</div>
        <div style="padding:6px 8px;background:#dcfce7;border-radius:4px;color:#166534;font-size:11px;line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere">\u002B ${escapeHtml(edit.after)}</div>
      </div>
      ${actions}
    </div>`;
}

// Word-swap pattern nudge — client-side regex. Fires only while the user is
// typing; stays non-blocking.
const WORD_SWAP_PATTERNS = [
  /\b(change|replace|swap|rename)\s+['"]?\w+['"]?\s+(to|with|for)\s+['"]?\w+['"]?/i,
  /['"]?\w+['"]?\s*(?:->|→|instead of)\s*['"]?\w+['"]?/i,
];

function chatCheckWordSwap(listingId) {
  const input = document.getElementById(`chatInput-${listingId}`);
  const nudge = document.getElementById(`chatNudge-${listingId}`);
  if (!input || !nudge) return;
  const v = (input.value || '').trim();
  const looksLikeSwap = v.length > 0 && v.length < 120 && WORD_SWAP_PATTERNS.some(p => p.test(v));
  if (!looksLikeSwap) { nudge.innerHTML = ''; return; }
  nudge.innerHTML = `
    <div style="padding:8px 10px;background:#fef3c7;border-left:3px solid #fcd34d;border-radius:var(--radius-sm);font-size:11px;line-height:1.5;color:#78350f;font-weight:500;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span>\u{1F914} That looks like a simple word swap \u2014 try the markdown editor instead (faster, free).</span>
      <button class="btn btn-ghost btn-sm" style="padding:2px 8px;font-size:10px;background:var(--bg1)" onclick="focusMarkdownEditor()">Edit markdown</button>
    </div>`;
}

function focusMarkdownEditor() {
  // Prefer resume editor textarea if present, else cover letter.
  const r = document.getElementById('resumeMdEditor');
  const c = document.getElementById('coverMdEditor');
  const ta = r || c;
  if (ta) { ta.focus(); ta.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
}

async function chatSubmit(listingId) {
  const input = document.getElementById(`chatInput-${listingId}`);
  const btn = document.getElementById(`chatSendBtn-${listingId}`);
  const sel = document.getElementById(`chatScope-${listingId}`);
  if (!input) return;
  const msg = (input.value || '').trim();
  if (!msg) return;
  const st = chatStateFor(listingId);
  if (st.pendingSend) return;
  st.pendingSend = true;
  const scope = sel ? sel.value : st.scope;
  st.scope = scope;

  // Optimistically append the user message locally so the UI feels responsive.
  st.history.push({ role: 'user', scope, content: msg, timestamp: new Date().toISOString(), _optimistic: true });
  renderChatPanelForCurrent(listingId);
  input.value = '';
  if (btn) { btn.disabled = true; btn.innerHTML = '\u{23F3} Thinking\u2026'; }

  try {
    const r = await window.api.listings.chatTurn(listingId, msg, scope);
    // Reload authoritative history (server appends both turns)
    await loadChatFor(listingId);
    renderChatPanelForCurrent(listingId);
  } catch (err) {
    alert('Chat failed: ' + err.message);
    // Remove optimistic user turn so we don't leave a phantom entry
    st.history = (st.history || []).filter(t => !t._optimistic);
    renderChatPanelForCurrent(listingId);
  } finally {
    st.pendingSend = false;
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{27A4} Send'; }
  }
}

function renderChatPanelForCurrent(listingId) {
  // Re-render to whichever container is currently open (resume or cover editor).
  const resumePanel = document.getElementById('chatPanel-resume');
  const coverPanel = document.getElementById('chatPanel-cover');
  if (resumePanel) renderChatPanel(listingId, 'chatPanel-resume', 'resume');
  if (coverPanel) renderChatPanel(listingId, 'chatPanel-cover', 'cover_letter');
}

async function chatApplyEdit(listingId, turnIdx, editId) {
  try {
    const r = await window.api.listings.chatApply(listingId, turnIdx, editId);
    await loadChatFor(listingId);
    renderChatPanelForCurrent(listingId);
    // If the edit touched the document the user is currently viewing, reload
    // the markdown + refresh the PDF preview.
    if (r.target === 'resume') {
      await reloadResumeEditorAfterChat();
    } else if (r.target === 'cover_letter') {
      await reloadCoverLetterEditorAfterChat();
    }
  } catch (err) {
    alert('Apply failed: ' + err.message);
  }
}

async function chatRejectEdit(listingId, turnIdx, editId) {
  try {
    await window.api.listings.chatReject(listingId, turnIdx, editId);
    await loadChatFor(listingId);
    renderChatPanelForCurrent(listingId);
  } catch (err) {
    alert('Reject failed: ' + err.message);
  }
}

async function chatUndoLast(listingId) {
  if (!confirm('Undo the most recent applied chat edit?')) return;
  try {
    const r = await window.api.listings.chatUndo(listingId);
    await loadChatFor(listingId);
    renderChatPanelForCurrent(listingId);
    if (r.target === 'resume') await reloadResumeEditorAfterChat();
    else if (r.target === 'cover_letter') await reloadCoverLetterEditorAfterChat();
  } catch (err) {
    alert('Undo failed: ' + err.message);
  }
}

async function chatClearHistory(listingId) {
  if (!confirm('Clear this conversation? Applied edits stay in the document; only the chat thread is wiped.')) return;
  try {
    await window.api.listings.chatClear(listingId);
    await loadChatFor(listingId);
    renderChatPanelForCurrent(listingId);
  } catch (err) {
    alert('Clear failed: ' + err.message);
  }
}

async function dismissChatOnboarding() {
  try {
    await window.api.settings.update({ chat_onboarding_dismissed: true });
    // Propagate locally to all chat states
    Object.values(window._chatState || {}).forEach(s => { s.onboardingDismissed = true; });
    // Re-render current panel
    const listingId = window._editorListingId;
    if (listingId) renderChatPanelForCurrent(listingId);
  } catch (err) {
    console.warn('Failed to persist onboarding dismissal:', err);
    const el = document.getElementById('chatOnboarding');
    if (el) el.style.display = 'none';
  }
}

// After a chat edit mutates the resume, re-pull the markdown + refresh the PDF.
async function reloadResumeEditorAfterChat() {
  if (typeof window._editorState === 'undefined' || !window._editorState || window._editorState.kind !== 'resume') return;
  try {
    const fresh = await window.api.listings.getTailoredResume(window._editorState.listingId);
    const ed = document.getElementById('resumeMdEditor');
    if (ed) ed.value = fresh.markdown || '';
    window._editorState.originalMarkdown = fresh.markdown || '';
    window._editorState.pdfFilename = fresh.pdf_path ? fresh.pdf_path.split('/').pop() : window._editorState.pdfFilename;
    if (typeof refreshResumePdfPreview === 'function') refreshResumePdfPreview();
  } catch (err) {
    console.warn('Could not refresh resume editor after chat edit:', err);
  }
}

async function reloadCoverLetterEditorAfterChat() {
  if (typeof window._editorState === 'undefined' || !window._editorState || window._editorState.kind !== 'cover') return;
  try {
    const fresh = await window.api.listings.getCoverLetterMd(window._editorState.listingId);
    const ed = document.getElementById('coverMdEditor');
    if (ed) ed.value = fresh.markdown || '';
    window._editorState.originalMarkdown = fresh.markdown || '';
    window._editorState.pdfFilename = fresh.pdf_path ? fresh.pdf_path.split('/').pop() : window._editorState.pdfFilename;
    if (typeof refreshCoverPdfPreview === 'function') refreshCoverPdfPreview();
  } catch (err) {
    console.warn('Could not refresh cover letter editor after chat edit:', err);
  }
}

// Toggle on/off — remembered per profile in localStorage.
function chatToggle(editorKind) {
  const key = `launchpad.chat.${editorKind}.visible`;
  const currentlyOn = localStorage.getItem(key) === '1';
  localStorage.setItem(key, currentlyOn ? '0' : '1');
  // Reload the editor page so layout re-flows with / without the panel.
  if (editorKind === 'resume' && typeof renderResumeEditor === 'function') {
    renderResumeEditor(document.getElementById('content'));
  } else if (editorKind === 'cover' && typeof renderCoverLetterEditor === 'function') {
    renderCoverLetterEditor(document.getElementById('content'));
  }
}

function isChatVisible(editorKind) {
  return localStorage.getItem(`launchpad.chat.${editorKind}.visible`) === '1';
}

// ========================= Batch Evaluation Banner =========================
// Adaptive UI that changes based on whether smart title filter is enabled.
// Smart OFF: [Evaluate all (N)] [Top 10 by keyword] [Skip]
// Smart ON:  [Evaluate all (N)] [Confident matches only (C of N)] [Skip]
//            + inline "M ambiguous titles skipped — [Evaluate them anyway]"

async function renderBatchEvalBanner(container, context) {
  try {
    const cohort = await window.api.listings.batchEvaluateCohort();
    if (cohort.unevaluated_total === 0) {
      container.innerHTML = '';
      return;
    }
    const est = `~$${(cohort.unevaluated_total * 0.03).toFixed(2)} estimated`;
    const n = cohort.unevaluated_total;

    if (cohort.smart_filter_enabled) {
      // Smart ON layout
      const confidentN = cohort.confident;
      const maybeN = cohort.maybe;
      const noVerdictN = cohort.no_verdict;
      let btns = `<button class="btn btn-primary btn-sm" onclick="runBatchEval('all','${context}')" title="${est}">\u{2728} Evaluate all (${n})</button>`;
      if (confidentN > 0 && confidentN < n) {
        btns += ` <button class="btn btn-ghost btn-sm" onclick="runBatchEval('confident','${context}')" title="Only titles the smart filter marked 'yes'">\u{2705} Confident matches only (${confidentN} of ${n})</button>`;
      }
      btns += ` <button class="btn btn-ghost btn-sm" onclick="this.closest('.batch-eval-banner')?.remove()">Skip</button>`;
      let maybeLine = '';
      if (maybeN > 0) {
        maybeLine = `<div style="margin-top:6px;font-size:11px;color:var(--text-muted)">${maybeN} ambiguous title${maybeN===1?'':'s'} skipped \u2014 <a href="#" onclick="event.preventDefault();runBatchEval('maybe_only','${context}')">Evaluate them anyway</a></div>`;
      }
      container.innerHTML = `
        <div class="batch-eval-banner" style="background:var(--primary-soft);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:12px;display:flex;flex-wrap:wrap;align-items:center;gap:8px">
          <span style="font-size:13px;font-weight:600;color:var(--primary)">${n} new listing${n===1?'':'s'} waiting for evaluation</span>
          <span style="font-size:11px;color:var(--text-muted)">${est}</span>
          <span style="flex:1"></span>
          ${btns}
          ${maybeLine}
        </div>`;
    } else {
      // Smart OFF layout
      let btns = `<button class="btn btn-primary btn-sm" onclick="runBatchEval('all','${context}')" title="${est}">\u{2728} Evaluate all (${n})</button>`;
      btns += ` <button class="btn btn-ghost btn-sm" onclick="runBatchEval('keyword_top','${context}')" title="Top 10 by keyword overlap with your target roles">\u{1F3AF} Top 10 by keyword</button>`;
      btns += ` <button class="btn btn-ghost btn-sm" onclick="this.closest('.batch-eval-banner')?.remove()">Skip</button>`;
      container.innerHTML = `
        <div class="batch-eval-banner" style="background:var(--primary-soft);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:12px;display:flex;flex-wrap:wrap;align-items:center;gap:8px">
          <span style="font-size:13px;font-weight:600;color:var(--primary)">${n} new listing${n===1?'':'s'} waiting for evaluation</span>
          <span style="font-size:11px;color:var(--text-muted)">${est}</span>
          <span style="flex:1"></span>
          ${btns}
        </div>`;
    }
  } catch (err) {
    console.warn('Batch eval banner failed:', err);
    container.innerHTML = '';
  }
}

async function runBatchEval(mode, context) {
  const banner = document.querySelector('.batch-eval-banner');
  if (banner) {
    banner.innerHTML = `<span style="font-size:13px;color:var(--primary)">\u{23F3} Running batch evaluation (${mode})...</span>`;
  }
  try {
    const result = await window.api.listings.batchEvaluate(mode);
    const msg = `Batch evaluation complete.\n\n\u2713 Evaluated: ${result.evaluated}\n\u2717 Failed: ${result.failed}${result.skipped ? `\n\u23E9 Skipped: ${result.skipped}` : ''}`;
    alert(msg);
  } catch (err) {
    alert('Batch evaluation failed: ' + err.message);
  }
  // Refresh the page that triggered this
  const c = document.getElementById('content');
  if (context === 'pipeline' && typeof renderPipeline === 'function') {
    renderPipeline(c);
  } else if (context === 'listings' && typeof renderListings === 'function') {
    renderListings(c);
  }
  if (typeof updateNavBadges === 'function') updateNavBadges();
}

// ========================= Welcome Wizard + Feature Tour =========================

// --- Welcome Wizard (first-time setup) ---
// Shows on first login when profile has no LLM key configured.
// Steps: Welcome → Upload Resume → LLM Key → Get Started

async function checkAndShowWizard() {
  if (!CURRENT_PROFILE) return;
  const settings = CURRENT_PROFILE.settings || await window.api.settings.get();
  // Show wizard if no LLM key is set (first-time user)
  const wizardDismissed = localStorage.getItem(`launchpad.wizard.dismissed.${CURRENT_PROFILE.id}`);
  if (!settings.has_llm_api_key && !wizardDismissed) {
    showWelcomeWizard();
  }
}

function showWelcomeWizard() {
  let step = 0;
  const steps = [
    { title: 'Welcome to LaunchPad! \u{1F680}', body: `
      <div style="text-align:center;padding:20px 0">
        <div style="font-size:48px;margin-bottom:16px">\u{1F680}</div>
        <div style="font-size:16px;font-weight:600;margin-bottom:8px">Your AI-powered job search command center</div>
        <div style="font-size:13px;color:var(--text2);line-height:1.7;max-width:360px;margin:0 auto">
          LaunchPad helps you discover, evaluate, and track job opportunities using AI.
          Let's get you set up in 3 quick steps.
        </div>
      </div>` },
    { title: 'Step 1: Upload Your Resume \u{1F4C4}', body: `
      <div style="padding:10px 0">
        <div style="font-size:13px;color:var(--text2);margin-bottom:16px;line-height:1.6">
          Upload your resume PDF so LaunchPad can tailor it for each job and evaluate how well you match.
          You can skip this and do it later from the Resume page.
        </div>
        <div class="upload-zone" onclick="document.getElementById('wizardResumeInput').click()" style="margin:0">
          <div class="uz-icon">\u{1F4C4}</div>
          <div class="uz-title">Drop your resume PDF here</div>
          <div class="uz-desc">or click to browse</div>
        </div>
        <input type="file" id="wizardResumeInput" accept=".pdf" style="display:none" onchange="wizardUploadResume(this)">
        <div id="wizardResumeStatus" style="margin-top:10px;font-size:12px;text-align:center"></div>
      </div>` },
    { title: 'Step 2: Connect Your LLM \u{1F9E0}', body: `
      <div style="padding:10px 0">
        <div style="font-size:13px;color:var(--text2);margin-bottom:16px;line-height:1.6">
          LaunchPad uses an LLM (like OpenAI or Anthropic) to evaluate jobs, tailor resumes, and generate cover letters.
          Paste your API key below. Your key is encrypted and never leaves your machine.
        </div>
        <div class="fg">
          <label class="fl">LLM Provider</label>
          <select class="fi" id="wizardProvider">
            <option value="openai">OpenAI</option>
            <option value="anthropic" selected>Anthropic (Claude)</option>
            <option value="google">Google (Gemini)</option>
          </select>
        </div>
        <div class="fg">
          <label class="fl">API Key</label>
          <input class="fi" id="wizardApiKey" type="password" placeholder="sk-... or your API key">
        </div>
        <div id="wizardKeyStatus" style="font-size:12px;margin-top:4px"></div>
      </div>` },
    { title: 'You\'re All Set! \u{1F389}', body: `
      <div style="text-align:center;padding:20px 0">
        <div style="font-size:48px;margin-bottom:16px">\u{1F389}</div>
        <div style="font-size:16px;font-weight:600;margin-bottom:12px">LaunchPad is ready to go</div>
        <div style="font-size:13px;color:var(--text2);line-height:1.7;max-width:360px;margin:0 auto">
          <strong>Next steps:</strong><br>
          \u{2022} Paste a job URL to add your first listing<br>
          \u{2022} Set up the Portal Scanner to auto-discover jobs<br>
          \u{2022} Connect Gmail to extract listings from job alerts<br>
          \u{2022} Click <strong>"Take a Tour"</strong> anytime from the sidebar
        </div>
      </div>` },
  ];

  function render() {
    const s = steps[step];
    const isFirst = step === 0;
    const isLast = step === steps.length - 1;
    const overlay = document.getElementById('wizardOverlay') || (() => {
      const el = document.createElement('div');
      el.id = 'wizardOverlay';
      el.className = 'modal-overlay open';
      el.style.cssText = 'display:flex;z-index:300';
      document.body.appendChild(el);
      return el;
    })();
    overlay.innerHTML = `
      <div class="modal" style="width:480px;max-width:92vw">
        <div class="modal-head">
          <div class="modal-title">${s.title}</div>
          <span style="font-size:11px;color:var(--text3)">${step + 1} of ${steps.length}</span>
        </div>
        <div class="modal-body">${s.body}</div>
        <div class="modal-foot" style="justify-content:space-between">
          <div>
            ${!isFirst ? `<button class="btn btn-ghost" onclick="wizardNav(-1)">\u{2190} Back</button>` : ''}
          </div>
          <div style="display:flex;gap:8px">
            ${!isLast ? `<button class="btn btn-ghost" onclick="dismissWizard()">Skip setup</button>` : ''}
            ${isLast
              ? `<button class="btn btn-primary" onclick="dismissWizard()">\u{1F680} Let's go!</button>`
              : `<button class="btn btn-primary" onclick="wizardNav(1)">Next \u{2192}</button>`}
          </div>
        </div>
      </div>`;
  }

  window.wizardNav = async function(dir) {
    // Before advancing from step 2 (LLM key), save the key
    if (step === 2 && dir > 0) {
      const provider = document.getElementById('wizardProvider')?.value;
      const key = document.getElementById('wizardApiKey')?.value?.trim();
      if (key) {
        const status = document.getElementById('wizardKeyStatus');
        if (status) status.innerHTML = '<span style="color:var(--primary)">\u{23F3} Saving...</span>';
        try {
          await window.api.settings.update({ llm_provider: provider, llm_api_key: key });
          if (status) status.innerHTML = '<span style="color:var(--green)">\u{2705} Saved!</span>';
        } catch (err) {
          if (status) status.innerHTML = `<span style="color:var(--red)">\u{274C} ${escapeHtml(err.message)}</span>`;
          return; // Don't advance on error
        }
      }
    }
    step = Math.max(0, Math.min(steps.length - 1, step + dir));
    render();
  };

  window.dismissWizard = function() {
    localStorage.setItem(`launchpad.wizard.dismissed.${CURRENT_PROFILE.id}`, '1');
    const overlay = document.getElementById('wizardOverlay');
    if (overlay) overlay.remove();
  };

  window.wizardUploadResume = async function(input) {
    const file = input.files?.[0];
    if (!file) return;
    const status = document.getElementById('wizardResumeStatus');
    if (status) status.innerHTML = '<span style="color:var(--primary)">\u{23F3} Uploading...</span>';
    try {
      await window.api.resumes.uploadPdf(file);
      if (status) status.innerHTML = '<span style="color:var(--green)">\u{2705} Resume uploaded!</span>';
    } catch (err) {
      if (status) status.innerHTML = `<span style="color:var(--red)">\u{274C} ${escapeHtml(err.message)}</span>`;
    }
  };

  render();
}


// --- Feature Tour (navigating walkthrough) ---
// Navigates to each page and highlights specific elements with tooltips.
// Goes in-depth on settings, scanner, and key features.

const TOUR_STEPS = [
  // Dashboard
  { page: 'dashboard', target: '.stats-row', title: 'Dashboard Overview', text: 'Your command center. These cards show your pipeline at a glance — new listings, evaluated, applied, interviews, and your average match score.', position: 'bottom' },

  // Pipeline
  { page: 'pipeline', target: '.pipeline-board', title: 'Pipeline Board', text: 'Kanban-style board. Listings flow left to right: New → Evaluated → Applied → Interview → Offer. Click any card to see details, evaluate, tailor your resume, or generate a cover letter.', position: 'bottom', delay: 300 },

  // All Listings
  { page: 'listings', target: '#listingsTable', title: 'All Listings', text: 'Every listing in one sortable table. Click column headers to sort. Use the "Apply filter" button to retroactively clean up listings that no longer match your title keywords.', position: 'bottom', delay: 300 },

  // Scanner
  { page: 'scanner', target: '#scannerCompanies', title: 'Portal Scanner — Companies', text: 'Add companies you want to track. LaunchPad auto-detects their ATS platform (Greenhouse, Ashby, Lever, Workday) and scans for new jobs on a schedule.', position: 'bottom', delay: 300 },
  { page: 'scanner', target: null, title: 'AI Company Monitor', text: 'For companies without a standard ATS, the AI Monitor uses web search (Google or LLM) to discover listings. It generates a custom query plan per company and runs it on a schedule. Toggle it per-company in the table.', position: 'center' },

  // Gmail
  { page: 'gmail', target: null, title: 'Gmail Integration', text: 'Connect your Gmail to auto-extract job listings from Indeed, LinkedIn, Glassdoor, and recruiter emails. LaunchPad classifies each email and pulls out structured listings with company, role, URL, and location.', position: 'center', delay: 300 },

  // Resume
  { page: 'resume', target: null, title: 'Resume Management', text: 'Upload your base resume PDF. LaunchPad parses it and uses it to tailor role-specific versions for each listing. You can also analyze your resume for improvement suggestions.', position: 'center', delay: 300 },

  // Companies
  { page: 'companies', target: null, title: 'Company Research', text: 'AI-powered research on every company in your pipeline. Culture, growth stage, tech stack, compensation data, and recent news — all gathered via web search and cached for 30 days.', position: 'center', delay: 300 },

  // Interview Prep
  { page: 'interview', target: null, title: 'Interview Prep', text: 'AI generates STAR stories from your resume — Situation, Task, Action, Result — tailored to common behavioral interview questions. Great for prep before calls.', position: 'center', delay: 300 },

  // Settings — LLM
  { page: 'settings', target: null, title: 'Settings — LLM Provider', text: 'Choose your LLM (OpenAI, Anthropic, or Google Gemini) and paste your API key. This powers all AI features: evaluation, resume tailoring, cover letters, company research, and the smart title filter.', position: 'center', delay: 400, scrollTo: '.scard:nth-child(1)' },

  // Settings — Scoring
  { page: 'settings', target: null, title: 'Settings — Scoring Weights', text: 'Customize how listings are scored across 8 dimensions: Role Match, Seniority, Skills, Compensation, Growth, S-Curve, Culture, and Location. Use presets (Balanced, Growth, Comp, Culture) or fine-tune each slider.', position: 'center', scrollTo: '.scard:nth-child(3)' },

  // Settings — Scanner
  { page: 'settings', target: null, title: 'Settings — Title Filter & Smart Filter', text: 'Positive keywords (must match at least one) and negative keywords (auto-reject). The Smart Title Filter adds an LLM pass that catches synonyms and drops obvious mismatches — costs ~$0.001 per title.', position: 'center', scrollTo: '#set-smart-title-filter' },

  // Settings — Google Search
  { page: 'settings', target: null, title: 'Settings — Google Search', text: 'Optional: connect Google Custom Search for the AI Monitor. Uses Google\'s fresh index instead of the LLM\'s built-in search (which can return stale/filled positions). Free tier: 100 queries/day.', position: 'center', scrollTo: '#set-google-search-key' },

  // Settings — Trusted Senders
  { page: 'settings', target: null, title: 'Settings — Trusted Senders', text: 'Emails from these senders skip the LLM classifier and go straight to listing extraction. Add your job alert sources (Indeed, LinkedIn, Glassdoor, etc.) to speed up Gmail processing.', position: 'center', scrollTo: '#sf-senders-tags' },

  // Topbar
  { page: null, target: '.topbar-search input', title: 'Global Search', text: 'Search across all listings by company, role, or keyword. Works on every page — just start typing.', position: 'bottom' },

  // Done
  { page: null, target: null, title: 'Tour Complete! \u{1F389}', text: 'You\'ve seen the key features. Start by adding a listing (paste a job URL) or set up the Portal Scanner to auto-discover jobs. You can restart this tour anytime from the sidebar.', position: 'center' },
];

let _tourStep = 0;
let _tourOverlay = null;

function startTour() {
  _tourStep = 0;
  closeSidebarIfMobile();
  _advanceTourStep();
}

async function _advanceTourStep() {
  // Clean up previous
  if (_tourOverlay) _tourOverlay.remove();
  document.querySelectorAll('.tour-highlight').forEach(el => el.classList.remove('tour-highlight'));

  if (_tourStep >= TOUR_STEPS.length) {
    endTour();
    return;
  }

  const step = TOUR_STEPS[_tourStep];

  // Navigate to the page if specified and not already there
  if (step.page) {
    const currentPage = document.querySelector('.nav-item.active')?.dataset?.page;
    if (currentPage !== step.page) {
      showPage(step.page);
    }
  }

  // Wait for page to render
  const delay = step.delay || 200;
  await new Promise(r => setTimeout(r, delay));

  // Scroll to a specific element if requested
  if (step.scrollTo) {
    const scrollTarget = document.querySelector(step.scrollTo);
    if (scrollTarget) {
      scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'center' });
      await new Promise(r => setTimeout(r, 400));
    }
  }

  // Find and highlight target
  const target = step.target ? document.querySelector(step.target) : null;
  if (target) {
    target.classList.add('tour-highlight');
    target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    await new Promise(r => setTimeout(r, 200));
  }

  // Create tooltip
  _tourOverlay = document.createElement('div');
  _tourOverlay.id = 'tourTooltip';
  _tourOverlay.style.cssText = 'position:fixed;z-index:400;pointer-events:auto';

  if (target && step.position !== 'center') {
    // Position relative to target
    const rect = target.getBoundingClientRect();
    let top, left;
    if (step.position === 'right') {
      top = rect.top + rect.height / 2 - 60;
      left = rect.right + 12;
    } else if (step.position === 'left') {
      top = rect.top + rect.height / 2 - 60;
      left = rect.left - 300;
    } else {
      // bottom
      top = rect.bottom + 12;
      left = rect.left + rect.width / 2 - 150;
    }
    top = Math.max(8, Math.min(window.innerHeight - 220, top));
    left = Math.max(8, Math.min(window.innerWidth - 320, left));
    _tourOverlay.style.top = top + 'px';
    _tourOverlay.style.left = left + 'px';
  } else {
    // Center on screen
    _tourOverlay.style.top = '50%';
    _tourOverlay.style.left = '50%';
    _tourOverlay.style.transform = 'translate(-50%, -50%)';
  }

  const isLast = _tourStep >= TOUR_STEPS.length - 1;
  _tourOverlay.innerHTML = `
    <div style="background:var(--surface);border:1.5px solid var(--primary);border-radius:var(--radius-sm);padding:18px;width:320px;box-shadow:var(--shadow-xl)">
      <div style="font-size:15px;font-weight:700;margin-bottom:8px">${step.title}</div>
      <div style="font-size:12px;color:var(--text2);line-height:1.7;margin-bottom:14px">${step.text}</div>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:11px;color:var(--text3)">${_tourStep + 1} of ${TOUR_STEPS.length}</span>
        <div style="display:flex;gap:6px">
          ${_tourStep > 0 ? '<button class="btn btn-ghost btn-sm" onclick="prevTourStep()">\u{2190} Back</button>' : ''}
          <button class="btn btn-ghost btn-sm" onclick="endTour()">End</button>
          <button class="btn btn-primary btn-sm" onclick="nextTourStep()">${isLast ? 'Done \u{2705}' : 'Next \u{2192}'}</button>
        </div>
      </div>
    </div>`;

  document.body.appendChild(_tourOverlay);
}

function nextTourStep() {
  _tourStep++;
  _advanceTourStep();
}

function prevTourStep() {
  _tourStep = Math.max(0, _tourStep - 1);
  _advanceTourStep();
}

function endTour() {
  if (_tourOverlay) { _tourOverlay.remove(); _tourOverlay = null; }
  document.querySelectorAll('.tour-highlight').forEach(el => el.classList.remove('tour-highlight'));
  _tourStep = 0;
}
