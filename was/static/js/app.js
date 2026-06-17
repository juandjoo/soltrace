const loginModal = new bootstrap.Modal(document.getElementById('loginModal'), {keyboard:false});

let currentSettingsTab = 'telco';

function nav(page) {
  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  document.querySelectorAll('#topbar .nav-link').forEach(el => el.classList.remove('active'));
  const link = document.querySelector(`#topbar [onclick="nav('${page}')"]`);
  if (link) link.classList.add('active');
  if (page === 'dashboard') { dashQuick(7); }
  else if (typeof _dashTimer !== 'undefined' && _dashTimer) toggleDashAutoRefresh();
  if (page === 'logs') initLogsPage();
  if (page === 'settings') settingsTab(currentSettingsTab);
}

function settingsTab(tab) {
  currentSettingsTab = tab;
  document.querySelectorAll('#page-settings .settings-pane').forEach(el => el.classList.add('d-none'));
  document.getElementById('settings-' + tab).classList.remove('d-none');
  document.querySelectorAll('.settings-vnav .nav-link').forEach(el => el.classList.remove('active'));
  const link = document.querySelector(`.settings-vnav [onclick="settingsTab('${tab}')"]`);
  if (link) link.classList.add('active');
  if (tab === 'telco') loadTelcos();
  if (tab === 'update') loadVersion();
  if (tab === 'devices') loadDevices();
  if (tab === 'groups') loadGroups();
  if (tab === 'notify') loadNotify();
  if (tab === 'security') loadSecurity();
}

function initApp() {
  nav('dashboard');
}

document.getElementById('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  const username = document.getElementById('loginUser').value.trim();
  const pwd = document.getElementById('loginPwd').value;
  const errEl = document.getElementById('loginError');
  const ipBlockedEl = document.getElementById('loginIpBlocked');
  const btn = document.getElementById('loginBtn');
  errEl.classList.add('d-none');
  ipBlockedEl.classList.add('d-none');
  btn.disabled = true;
  try {
    const res = await fetch(API + '/auth/login', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({username, password: pwd})
    });
    if (res.status === 403) {
      const data = await res.json().catch(() => ({}));
      const ip = data?.detail?.client_ip || '';
      document.getElementById('blockedIpDisplay').textContent = ip;
      ipBlockedEl.classList.remove('d-none');
      return;
    }
    if (!res.ok) throw new Error('아이디 또는 비밀번호가 올바르지 않습니다.');
    const data = await res.json();
    token = data.access_token;
    localStorage.setItem('soltrace_token', token);
    startSessionTimers(token);
    loginModal.hide();
    document.getElementById('appLayout').classList.remove('app-hidden');
    initApp();
  } catch(err) {
    errEl.textContent = err.message;
    errEl.classList.remove('d-none');
  } finally { btn.disabled = false; }
});

['logUserFilter', 'logStartTime', 'logEndTime'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', e => {
    if (e.key === 'Enter') searchLogs(1);
  });
});

(function init() {
  if (token) {
    startSessionTimers(token);
    document.getElementById('appLayout').classList.remove('app-hidden');
    initApp();
  } else {
    loginModal.show();
  }
})();
