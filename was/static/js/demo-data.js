/* SolTrace 데모 모드 — 백엔드 없이 UI 를 그대로 보여주기 위한 API 스텁.
 *
 * 실제 화면(index.html + 실제 css/js)을 그대로 쓰고 네트워크 응답만 가짜로 채운다.
 * 따라서 UI 를 고쳐도 데모가 따로 낡지 않는다.
 *
 * 로드 조건: utils.js 보다 **먼저** 실행되어야 한다 (token 을 미리 심고 fetch 를 가로챈다).
 * 사용처:
 *   - scripts/build_demo.py 가 만드는 단일 파일 샘플(samples/soltrace-demo.html)
 *   - 서버 실행 중에는 /demo 경로로 접속 (index.html 이 쿼리/경로를 보고 이 파일을 로드)
 */
(function () {
  'use strict';

  const HOUR = 3600 * 1000;
  const DAY = 24 * HOUR;
  const now = Date.now();

  // 결정적 난수 — 새로고침해도 같은 화면이 나오도록
  let _seed = 20260903;
  function rnd() {
    _seed = (_seed * 1103515245 + 12345) & 0x7fffffff;
    return _seed / 0x7fffffff;
  }
  const pick = arr => arr[Math.floor(rnd() * arr.length)];
  const between = (a, b) => a + Math.floor(rnd() * (b - a + 1));

  // ── 로그인 상태 위조: role=admin 인 서명 없는 토큰 (데모 전용, 서버 검증 없음) ──
  function b64url(obj) {
    return btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  const demoToken = [
    b64url({ alg: 'none', typ: 'JWT' }),
    b64url({ sub: 'admin', role: 'admin', exp: Math.floor((now + HOUR) / 1000) }),
    'demo',
  ].join('.');
  localStorage.setItem('soltrace_token', demoToken);

  // ── 기준 데이터 ────────────────────────────────────────────────────────────
  const TELCOS = [{ id: 1, name: 'SKT' }, { id: 2, name: 'KT' }, { id: 3, name: 'LG U+' }];

  const GROUPS = [
    { id: 1, name: 'VOD-서울', telco: 'SKT',   customer: 'ACME',    application: 'VOD 배포',    upload_domains: 'vod.acme.co.kr',   description: '서울 IDC VOD 원본 수집', device_count: 2 },
    { id: 2, name: 'VOD-부산', telco: 'SKT',   customer: 'ACME',    application: 'VOD 배포',    upload_domains: 'vod-bs.acme.co.kr', description: '부산 IDC 이중화',        device_count: 1 },
    { id: 3, name: 'CDN-원본', telco: 'KT',    customer: 'BLUEWAVE', application: 'CDN Origin', upload_domains: 'origin.bluewave.kr', description: 'CDN 원본 서버',         device_count: 1 },
    { id: 4, name: '백업-야간', telco: 'LG U+', customer: 'BLUEWAVE', application: '백업',       upload_domains: '',                  description: '야간 배치 백업',         device_count: 1 },
  ].map(g => ({ ...g, created_at: new Date(now - 120 * DAY).toISOString() }));

  const GROUP_BRIEF = GROUPS.map(g => ({ id: g.id, name: g.name, telco: g.telco }));

  const DEVICES = [
    { id: 1, hostname: 'ftp-seoul-01', ip_address: '10.20.1.11', status: 'confirmed', daemon_status: 'running',  cpu: 12.4, mem: 88.2,  disk: 412.5, buf: 0,     fail: 0, groups: [0] },
    { id: 2, hostname: 'ftp-seoul-02', ip_address: '10.20.1.12', status: 'confirmed', daemon_status: 'running',  cpu: 31.7, mem: 104.6, disk: 233.1, buf: 0,     fail: 0, groups: [0] },
    { id: 3, hostname: 'ftp-busan-01', ip_address: '10.30.2.21', status: 'confirmed', daemon_status: 'degraded', cpu: 68.9, mem: 151.3, disk: 41.8,  buf: 12400, fail: 3, groups: [1], err: 'WAS 응답 지연 — 로컬 버퍼 사용 중' },
    { id: 4, hostname: 'ftp-origin-01', ip_address: '10.40.3.31', status: 'confirmed', daemon_status: 'running', cpu: 22.1, mem: 96.0,  disk: 890.2, buf: 0,     fail: 0, groups: [2] },
    { id: 5, hostname: 'ftp-backup-01', ip_address: '10.40.3.32', status: 'pending',   daemon_status: 'unknown', cpu: null, mem: null,  disk: null,  buf: 0,     fail: 0, groups: [] },
  ].map(d => ({
    id: d.id,
    hostname: d.hostname,
    ip_address: d.ip_address,
    device_key: 'demo-key-' + String(d.id).padStart(4, '0'),
    status: d.status,
    os_info: 'Rocky Linux 8.9 (Green Obsidian)',
    kernel_version: '4.18.0-513.el8.x86_64',
    proftpd_version: '1.3.6',
    daemon_version: '1.0.0',
    last_heartbeat: new Date(now - (d.daemon_status === 'unknown' ? 6 * HOUR : between(5, 90) * 1000)).toISOString(),
    daemon_status: d.daemon_status,
    last_send_time: new Date(now - between(10, 120) * 1000).toISOString(),
    buffer_lines: d.buf,
    queue_size: d.buf ? 320 : 0,
    consecutive_failures: d.fail,
    error_message: d.err || null,
    cpu_percent: d.cpu,
    mem_mb: d.mem,
    disk_free_gb: d.disk,
    daemon_uptime: d.daemon_status === 'unknown' ? null : between(3600, 720000),
    update_requested: false,
    created_at: new Date(now - 100 * DAY).toISOString(),
    groups: d.groups.map(i => GROUP_BRIEF[i]),
  }));

  const ACTIVE_DEVICES = DEVICES.filter(d => d.status === 'confirmed');
  const USERS = ['vod_ingest', 'cdn_sync', 'batch_night', 'media_ops', 'partner_a', 'partner_b'];
  const PATHS = [
    '/upload/vod/2026/09/ep_{n}.mp4', '/upload/vod/2026/09/ep_{n}.ts',
    '/origin/live/ch{n}/segment_{n}.m4s', '/backup/db/dump_{n}.sql.gz',
    '/upload/thumb/{n}.jpg', '/partner/drop/{n}.zip',
  ];

  // ── 로그 (총 12,483건 중 페이지 단위로 잘라 응답) ──────────────────────────
  const LOG_TOTAL = 12483;
  function makeLog(i) {
    const dev = ACTIVE_DEVICES[i % ACTIVE_DEVICES.length];
    const action = pick(['upload', 'upload', 'download', 'download', 'delete', 'login', 'logout', 'mkdir', 'cwd_fail']);
    const failed = rnd() < 0.06;
    const isTransfer = action === 'upload' || action === 'download';
    const size = isTransfer ? between(120 * 1024, 4 * 1024 * 1024 * 1024) : 0;
    return {
      id: LOG_TOTAL - i,
      device_id: dev.id,
      device_hostname: dev.hostname,
      device_ip: dev.ip_address,
      log_time: new Date(now - i * 137 * 1000 - between(0, 60) * 1000).toISOString(),
      client_ip: `192.168.${between(1, 40)}.${between(2, 250)}`,
      username: action === 'logout' ? pick(USERS) : pick(USERS),
      action,
      file_path: isTransfer || action === 'delete' || action === 'mkdir'
        ? pick(PATHS).replace(/\{n\}/g, () => String(between(1, 9999)))
        : (action === 'cwd_fail' ? '/upload/vod/2026/08' : null),
      file_size: size,
      transfer_time: isTransfer ? Number((size / (1024 * 1024) / between(8, 90) + 0.2).toFixed(2)) : 0,
      transfer_type: isTransfer ? 'b' : null,
      status: action === 'cwd_fail' ? 'fail' : (failed ? 'fail' : 'success'),
    };
  }

  // ── 대시보드 ───────────────────────────────────────────────────────────────
  function dashboard() {
    const timeseries = [];
    for (let d = 6; d >= 0; d--) {
      const day = new Date(now - d * DAY);
      const up = between(900, 2600), down = between(1400, 3800);
      timeseries.push({
        date: `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`,
        uploads: up, downloads: down, deletes: between(20, 160),
        bytes_in: up * between(40, 120) * 1024 * 1024,
        bytes_out: down * between(30, 90) * 1024 * 1024,
      });
    }
    const sum = k => timeseries.reduce((a, b) => a + b[k], 0);
    return {
      stats: {
        total_uploads: sum('uploads'), total_downloads: sum('downloads'), total_deletes: sum('deletes'),
        total_bytes_in: sum('bytes_in'), total_bytes_out: sum('bytes_out'),
        active_devices: ACTIVE_DEVICES.length, active_users: USERS.length, period_days: 7,
      },
      timeseries,
      top_users: USERS.map(u => ({ label: u, count: between(300, 3000), bytes: between(20, 900) * 1024 * 1024 * 1024 }))
        .sort((a, b) => b.count - a.count),
      top_devices: ACTIVE_DEVICES.map(d => ({ label: d.hostname, count: between(800, 4200), bytes: between(50, 1200) * 1024 * 1024 * 1024 }))
        .sort((a, b) => b.count - a.count),
      top_groups: GROUPS.map(g => ({ label: g.name, customer: g.customer, count: between(500, 3500), bytes: between(40, 1000) * 1024 * 1024 * 1024 }))
        .sort((a, b) => b.bytes - a.bytes),
      by_action: { upload: sum('uploads'), download: sum('downloads'), delete: sum('deletes'), login: between(400, 900), logout: between(380, 880), mkdir: between(30, 120), cwd_fail: between(10, 90) },
    };
  }

  function hourlyPoints(scale) {
    const pts = [];
    for (let h = 23; h >= 0; h--) {
      const b = new Date(now - h * HOUR);
      b.setUTCMinutes(0, 0, 0);
      // 업무시간(UTC+9 기준 낮)에 트래픽이 몰리는 모양
      const kst = (b.getUTCHours() + 9) % 24;
      const busy = kst >= 9 && kst <= 21 ? 1 : 0.25;
      const up = Math.round(between(20, 160) * busy * scale);
      const down = Math.round(between(40, 260) * busy * scale);
      const del = Math.round(between(0, 12) * busy * scale);
      pts.push({
        bucket: b.toISOString().replace(/\.\d+Z$/, 'Z'),
        uploads: up, downloads: down, deletes: del,
        bytes_in: up * between(30, 90) * 1024 * 1024,
        bytes_out: down * between(20, 70) * 1024 * 1024,
        bytes_del: del * between(10, 60) * 1024 * 1024,
      });
    }
    return pts;
  }

  function serviceHealth() {
    const trend = [];
    for (let i = 23; i >= 0; i--) {
      const b = new Date(now - i * HOUR);
      b.setUTCMinutes(0, 0, 0);
      const spike = i >= 4 && i <= 6;   // 몇 시간 전 장애 구간
      trend.push({
        bucket: b.toISOString(),
        fail_rate: spike ? 0.18 + rnd() * 0.09 : rnd() * 0.03,
        throughput_mb: spike ? 12 + rnd() * 6 : 48 + rnd() * 22,
        login_fail_rate: spike ? 0.34 + rnd() * 0.1 : rnd() * 0.05,
        cwd_fails: spike ? between(30, 80) : between(0, 6),
      });
    }
    const alerts = [
      { metric: 'fail_rate',       severity: 'critical', value: 0.241, baseline: 0.021, dev: 2, msg: '전송 실패율 24.1% (기준 2.1%)' },
      { metric: 'throughput',      severity: 'warning',  value: 13.4,  baseline: 51.2,  dev: 2, msg: '실효 전송속도 13.4MB/s (기준 51.2MB/s)' },
      { metric: 'login_fail_rate', severity: 'warning',  value: 0.382, baseline: 0.042, dev: 3, msg: '로그인 실패율 38.2% (기준 4.2%)' },
      { metric: 'cwd_fail_spike',  severity: 'warning',  value: 64,    baseline: 3,     dev: 1, msg: 'CWD 실패 64건 (기준 3건)' },
    ].map((a, i) => {
      const d = ACTIVE_DEVICES[a.dev];
      const t = new Date(now - (5 - i * 0.3) * HOUR);
      return {
        id: 900 + i, device_id: d.id, hostname: d.hostname,
        bucket: t.toISOString(), metric: a.metric, severity: a.severity,
        value: a.value, baseline: a.baseline, message: a.msg, created_at: t.toISOString(),
      };
    });
    const statuses = { 1: 'ok', 2: 'ok', 3: 'critical', 4: 'warning' };
    return {
      devices: ACTIVE_DEVICES.map(d => ({
        device_id: d.id, hostname: d.hostname,
        status: statuses[d.id] || 'ok',
        last_bucket: new Date(now - 10 * 60 * 1000).toISOString(),
        fail_rate: d.id === 3 ? 0.241 : rnd() * 0.02,
        throughput_mb: d.id === 3 ? 13.4 : 40 + rnd() * 25,
        login_fail_rate: d.id === 4 ? 0.382 : rnd() * 0.04,
        open_alerts: d.id === 3 ? 2 : (d.id === 4 ? 2 : 0),
      })),
      alerts,
      trend,
      fail_totals: { transfer_fails: 812, login_fails: 143, cwd_fails: 291, cwd_fails_ignored: 640 },
    };
  }

  // CWD 실패 원인 분석 — 대부분이 잘못 설정된 업로드 경로 두 곳에 몰린 모양
  function cwdFails() {
    const paths = [
      { file_path: '/upload/vod/2026/08', count: 412, users: 3,  ips: 4,  ignored: false },
      { file_path: '/data/incoming/tmp',  count: 386, users: 2,  ips: 2,  ignored: true  },
      { file_path: '/backup/daily',       count: 254, users: 1,  ips: 1,  ignored: true  },
      { file_path: '/home/ftpuser/vod',   count: 121, users: 5,  ips: 9,  ignored: false },
      { file_path: '/upload/thumb',       count: 44,  users: 6,  ips: 11, ignored: false },
      { file_path: '/etc',                count: 9,   users: 2,  ips: 2,  ignored: false },
    ].map((p, i) => ({ ...p, last_seen: new Date(now - (i + 1) * 900 * 1000).toISOString() }));
    const listed = paths.reduce((a, p) => a + p.count, 0);
    const probes = 1840;   // 존재 확인으로 걸러진 건 (목록에는 오지 않는다)
    return {
      total: listed + probes,
      ignored: paths.filter(p => p.ignored).reduce((a, p) => a + p.count, 0),
      probes,
      distinct_paths: 37,
      top_share: (paths[0].count + paths[3].count + paths[4].count)
                 / paths.filter(p => !p.ignored).reduce((a, p) => a + p.count, 0),
      paths,
      users: [
        { username: 'vod_batch', count: 401, paths: 6 },
        { username: 'cdn_sync',  count: 118, paths: 9 },
        { username: 'ops_user',  count: 44,  paths: 12 },
        { username: null,        count: 23,  paths: 5 },
      ],
      ignore_patterns: ['/data/incoming/tmp', '/backup/daily'],
    };
  }

  function storage() {
    const partitions = [];
    let total = 0;
    for (let m = 0; m < 9; m++) {
      const d = new Date(now - m * 30 * DAY);
      const rows = m === 0 ? between(900000, 1400000) : between(2400000, 4200000);
      const table = rows * 210;
      const index = Math.round(table * 0.78);
      total += table + index;
      partitions.push({
        name: `ftp_logs_${d.getFullYear()}_${String(d.getMonth() + 1).padStart(2, '0')}`,
        rows_est: rows, table_bytes: table, index_bytes: index, total_bytes: table + index,
      });
    }
    partitions.push({ name: 'ftp_logs_default', rows_est: 0, table_bytes: 8192, index_bytes: 16384, total_bytes: 24576 });
    return {
      db_bytes: total + 380 * 1024 * 1024,
      ftp_logs_bytes: total + 24576,
      partitions,
      default_rows: 0,
      default_months: [],
      retention_months: 36,
    };
  }

  const ACCOUNTS = [
    { id: 1, username: 'acme_view',  customer: 'ACME',     allowed_ips: ['203.0.113.0/24'], is_active: true },
    { id: 2, username: 'bluewave',   customer: 'BLUEWAVE', allowed_ips: [],                 is_active: true },
    { id: 3, username: 'partner_ro', customer: 'ACME',     allowed_ips: ['198.51.100.7'],   is_active: false },
  ].map(u => ({ ...u, role: 'customer', created_at: new Date(now - 30 * DAY).toISOString() }));

  // ── 라우팅 ─────────────────────────────────────────────────────────────────
  const ROUTES = [
    [/^\/auth\/refresh$/, () => ({ access_token: demoToken, token_type: 'bearer', role: 'admin', customer: null })],
    [/^\/telcos$/, () => TELCOS],
    [/^\/groups$/, () => GROUPS],
    [/^\/devices/, () => DEVICES],
    [/^\/logs\/count/, () => ({ total: LOG_TOTAL })],
    [/^\/logs\?/, (path) => {
      const q = new URLSearchParams(path.split('?')[1] || '');
      const page = parseInt(q.get('page') || '1', 10);
      const size = parseInt(q.get('size') || '50', 10);
      const start = (page - 1) * size;
      const items = [];
      for (let i = start; i < Math.min(start + size, LOG_TOTAL); i++) items.push(makeLog(i));
      return { total: null, page, size, items };
    }],
    [/^\/dashboard\/users-hourly/, () => USERS.slice(0, 5).map(u => ({ username: u, data: hourlyPoints(0.6 + rnd()) }))],
    [/^\/dashboard\/hourly/, () => GROUPS.map(g => ({ group_id: g.id, name: g.name, telco: g.telco, data: hourlyPoints(0.5 + rnd()) }))],
    [/^\/dashboard\/cwd-fails/, cwdFails],
    [/^\/dashboard\/service-health/, serviceHealth],
    [/^\/dashboard/, dashboard],
    [/^\/settings\/changelog/, () => ([
      { date: '2026-09-03', items: [
        { text: '사용자별 업로드양·삭제량 차트 + 왼쪽 탭 정리', commit: 'demo123' },
        { text: '로그 조회 화면 수정 — 검색 시 깜빡임, 작업 열 침범', commit: 'b820825' },
        { text: 'CWD 존재 확인은 실패로 세지 않는다', commit: 'f5c3b70' },
      ]},
      { date: '2026-06-30', items: [
        { text: '고객사 계정 + groups.customer 기준 데이터 격리 추가', commit: '7f649da' },
      ]},
    ])],
    [/^\/settings\/version$/, () => ({
      branch: 'main', commit: 'demo123', commit_date: '2026-09-03 10:20',
      subject: '대량 데이터 보관 개선 + 죽은 코드/중복 정리',
      behind: 0, update_available: false, checked: false, error: null,
    })],
    [/^\/settings\/storage$/, storage],
    [/^\/settings\/security$/, () => ({ username: 'admin', allowed_ips: ['10.0.0.0/8', '203.0.113.0/24'], my_ip: '203.0.113.42' })],
    [/^\/settings\/alerts$/, () => ({
      mad_k: 4.0, fail_rate_floor: 0.05, login_fail_rate_floor: 0.3, throughput_drop: 0.5,
      cwd_fail_floor: 20, min_samples: 20, min_login_samples: 10, min_cwd_samples: 5,
      min_large_samples: 5, cwd_ignore_paths: '/data/incoming/tmp\n/backup/daily',
      large_file_bytes: 4 * 1024 * 1024, bucket_minutes: 10, baseline_days: 7,
    })],
    [/^\/settings\/notify\/mute$/, () => ({ muted: false })],
    [/^\/settings\/notify$/, () => ({ webhook_url: 'https://hooks.example.com/soltrace', hms_url: '' })],
    [/^\/users$/, () => ACCOUNTS],
    [/^\/api-keys$/, () => [
      { id: 1, user_id: null, username: 'admin', role: 'admin', customer: null,
        label: '사내 대시보드 연동', key_prefix: 'slt_7Kd2mQ4a', is_active: true,
        expires_at: null, last_used_at: new Date(now - 12 * 60 * 1000).toISOString(),
        created_at: new Date(now - 20 * DAY).toISOString() },
      { id: 2, user_id: 1, username: 'acme_view', role: 'customer', customer: 'ACME',
        label: 'ACME 월간 리포트', key_prefix: 'slt_Xb91TpLs', is_active: true,
        expires_at: new Date(now + 90 * DAY).toISOString(),
        last_used_at: new Date(now - 3 * HOUR).toISOString(),
        created_at: new Date(now - 8 * DAY).toISOString() },
      { id: 3, user_id: 3, username: 'partner_ro', role: 'customer', customer: 'ACME',
        label: '', key_prefix: 'slt_Qm38vZc1', is_active: false,
        expires_at: null, last_used_at: null,
        created_at: new Date(now - 40 * DAY).toISOString() },
    ]],
  ];

  const DEMO_READONLY = '데모 모드에서는 변경할 수 없습니다.';

  const _realFetch = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    const u = String(url);
    if (!u.startsWith('/api/v1')) return _realFetch(url, opts);

    const path = u.slice('/api/v1'.length);
    const method = ((opts && opts.method) || 'GET').toUpperCase();
    const json = (body, status = 200) => Promise.resolve(new Response(
      status === 204 ? null : JSON.stringify(body),
      { status, headers: { 'Content-Type': 'application/json' } }
    ));

    // 내보내기: 실제 파일이 받아지도록 작은 CSV 를 돌려준다
    if (path.startsWith('/logs/export')) {
      const rows = [['id', 'device', 'log_time', 'client_ip', 'username', 'action', 'file_path', 'file_size', 'transfer_time', 'status']];
      for (let i = 0; i < 200; i++) {
        const l = makeLog(i);
        rows.push([l.id, l.device_hostname, l.log_time, l.client_ip, l.username, l.action, l.file_path || '', l.file_size, l.transfer_time, l.status]);
      }
      const csv = rows.map(r => r.join(',')).join('\n');
      return Promise.resolve(new Response(csv, { status: 200, headers: { 'Content-Type': 'text/csv' } }));
    }

    if (method !== 'GET') {
      // 로그인은 통과시켜 화면 진입만 가능하게, 나머지 변경 요청은 안내 후 차단
      if (path === '/auth/login') return json({ access_token: demoToken, token_type: 'bearer', role: 'admin', customer: null });
      if (path === '/auth/refresh') return json({ access_token: demoToken, token_type: 'bearer', role: 'admin', customer: null });
      return json({ detail: DEMO_READONLY }, 403);
    }

    for (const [re, handler] of ROUTES) {
      if (re.test(path)) return json(handler(path));
    }
    return json({ detail: `데모 데이터 없음: ${path}` }, 404);
  };

  // 데모 안내 배너
  document.addEventListener('DOMContentLoaded', function () {
    const bar = document.createElement('div');
    bar.className = 'alert alert-info d-flex align-items-center gap-2 mb-0 rounded-0 py-2 small';
    bar.innerHTML = '<i class="bi bi-info-circle-fill"></i>'
      + '<span><b>데모 모드</b> — 모든 수치는 예시 데이터입니다. 조회·차트·탭 이동은 동작하며, 저장/삭제 등 변경 기능은 막혀 있습니다.</span>';
    document.body.prepend(bar);
  });
})();
