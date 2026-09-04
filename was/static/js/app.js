const loginModal = new bootstrap.Modal(document.getElementById('loginModal'), {keyboard:false});

let currentSettingsTab = 'telco';

// 관리자 전용 페이지 — 고객 계정은 서버에서도 막히지만 UI 에서도 진입을 막는다.
const ADMIN_PAGES = ['settings', 'changelog', 'devices', 'groups'];

function nav(page) {
  if (ADMIN_PAGES.includes(page) && getRole() !== 'admin') page = 'dashboard';
  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  document.querySelectorAll('#sidenav .nav-link').forEach(el => el.classList.remove('active'));
  const link = document.querySelector(`#sidenav [onclick="nav('${page}')"]`);
  if (link) link.classList.add('active');
  if (page === 'dashboard') {
    initDashLayout();
    if (_dashExactStart || document.getElementById('dashStart').value) loadAll();
    else dashLast24();
  }
  else if (typeof _dashTimer !== 'undefined' && _dashTimer) toggleDashAutoRefresh();
  if (page === 'logs') initLogsPage();
  if (page === 'devices') loadDevices();
  if (page === 'groups') loadGroups();
  if (page === 'apiguide') initApiGuide();
  if (page === 'changelog') initChangelogPage();
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
  if (tab === 'storage') loadStorage();
  if (tab === 'notify') loadNotify();
  if (tab === 'users') loadUsers();
  if (tab === 'security') loadSecurity();
  if (tab === 'admins') loadAdmins();
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
      const code = data?.detail?.code;
      // 잠금·비활성은 IP 차단과 원인이 다르므로 안내도 달라야 한다
      if (code === 'ACCOUNT_LOCKED' || code === 'ACCOUNT_DISABLED') {
        errEl.textContent = data.detail.message || '로그인할 수 없는 계정입니다.';
        errEl.classList.remove('d-none');
        return;
      }
      document.getElementById('blockedIpDisplay').textContent = data?.detail?.client_ip || '';
      ipBlockedEl.classList.remove('d-none');
      return;
    }
    if (!res.ok) {
      // 남은 시도 횟수 등 서버가 준 문구를 그대로 보여준다
      const data = await res.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' && data.detail !== 'Incorrect credentials'
        ? data.detail : '아이디 또는 비밀번호가 올바르지 않습니다.');
    }
    const data = await res.json();
    token = data.access_token;
    localStorage.setItem('soltrace_token', token);
    startSessionTimers(token);
    applyRoleUI();
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
    applyRoleUI();
    document.getElementById('appLayout').classList.remove('app-hidden');
    initApp();
  } else {
    loginModal.show();
  }
})();
