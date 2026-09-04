"""
서비스 영향도 감지 백그라운드 잡.

주기적으로 (1) ftp_logs를 service_metrics 시간버킷으로 롤업하고,
(2) 장비별 baseline(최근 N일 median+MAD) 대비 이탈을 판정해 service_alerts에 적재한 뒤,
(3) 미발송 알림을 메일/웹훅으로 보낸다.

write_buffer와 동일하게 워커(프로세스)당 싱글턴 스레드로 동작한다.
Gunicorn 다중 워커 환경에서도 각 작업이 멱등(UPSERT / ON CONFLICT DO NOTHING)이라
중복 실행돼도 결과가 어긋나지 않는다.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config import settings
from app import alert_settings, disk_guard, notifier

log = logging.getLogger("soltrace.monitor")

_EPOCH = "2000-01-01 00:00:00+00"  # date_bin origin (버킷 경계 고정)

# CWD 550 뒤 이 시간 안에 같은 경로가 만들어졌으면 "이동 실패"가 아니라 존재 확인으로 본다.
# (업로드 클라이언트의 CWD → 550 → MKD → 업로드 흐름. 사람이 아니라 스크립트라 간격이 짧다.)
_CWD_PROBE_WINDOW = "10 minutes"

# 같은 흐름인데 로그 시각이 앞뒤로 몇 초 어긋나는 것을 흡수하는 여유.
# CWD 550 과 그 뒤 MKD 가 1초 차이로 뒤집혀 기록되면(세션이 여럿이거나 초 단위 절삭)
# "실패 직후 생성"이 성립하지 않아 정상 흐름이 실패로 남던 문제를 막는다.
_CWD_CLOCK_SKEW = "5 seconds"


def cwd_probe_sql(alias: str, since: str, until: str) -> str:
    """이 cwd_fail 이 '존재 확인'인가 — 실패 직후 그 경로(또는 그 하위)가 생성됐는지 보는 조건.

    폴더로 이동하다 실패한 게 아니라 업로드 전에 있는지 떠본 것이므로 실패가 아니다.
    하위 경로까지 보는 이유: `/a/b` CWD 실패 직후 `/a/b/11111` 이 만들어졌다면 그 사이에
    `/a/b` 가 생겼다는 뜻이라(하위를 만들려면 부모가 있어야 한다) 같은 '존재 확인' 흐름이다.
    LIKE 가 아니라 starts_with 를 쓰는 것은 경로에 흔한 `_`/`%` 가 와일드카드로 해석돼
    엉뚱한 건까지 실패에서 빠지는 것을 막기 위해서다.

    "직후"는 _CWD_CLOCK_SKEW(5초)만큼 앞도 인정한다. 같은 흐름이라도 기록 시각이 1~2초
    뒤집히는 일이 있어, 엄격히 이후만 보면 정상 흐름이 실패로 남는다.

    since/until 은 바깥 조회의 기간 SQL 식(예: ":since", "NOW()") — mkdir 쪽에도 같은
    기간을 걸어야 파티션 프루닝이 되고, 없으면 전체 월 파티션을 훑는다.
    idx_ftp_logs_mkdir_path(부분 인덱스)의 device_id 선두 컬럼을 탄다.
    """
    return f"""EXISTS (
                      SELECT 1 FROM ftp_logs mk
                      WHERE mk.action = 'mkdir'
                        AND mk.device_id = {alias}.device_id
                        AND (mk.file_path = {alias}.file_path
                             OR starts_with(mk.file_path, {alias}.file_path || '/'))
                        AND mk.log_time >= ({since}) - INTERVAL '{_CWD_CLOCK_SKEW}'
                        AND mk.log_time < ({until}) + INTERVAL '{_CWD_PROBE_WINDOW}'
                        AND mk.log_time >= {alias}.log_time - INTERVAL '{_CWD_CLOCK_SKEW}'
                        AND mk.log_time < {alias}.log_time + INTERVAL '{_CWD_PROBE_WINDOW}'
                  )"""


def cwd_real_fail_sql(alias: str, since: str, until: str) -> str:
    """진짜 디렉토리 이동 실패만 남기는 조건 (:cwd_ignore 바인딩 필요).

    두 가지를 뺀다.
      1. 설정의 제외 경로 — 원인이 밝혀져 더 볼 필요가 없는 경로.
      2. 존재 확인 — 실패 직후 같은 경로가 생성된 건. 폴더로 '이동하다 실패'한 게 아니라
         업로드 전에 있는지 떠본 것이므로 실패로 세지 않는다.

    집계·알림·화면이 모두 이 한 조건을 쓴다(기준이 갈라지면 숫자가 어긋난다).

    since/until 은 바깥 조회의 기간 SQL 식(예: ":since", "NOW()"). mkdir 쪽에도 같은
    기간을 걸어야 파티션 프루닝이 되고, 없으면 전체 월 파티션을 훑는다.
    2번 조회는 idx_ftp_logs_mkdir_path(부분 인덱스)를 탄다.
    """
    return f"""{cwd_not_ignored_sql(f'{alias}.file_path')}
                  AND NOT {cwd_probe_sql(alias, since, until)}"""


def cwd_not_ignored_sql(col: str = "file_path") -> str:
    """cwd_fail 집계에서 제외 경로를 거르는 SQL 조건 (:cwd_ignore 바인딩 필요).

    롤업(건수)·알림 경로 분석·대시보드 실패 건수가 모두 이 한 조건을 써야
    "제외 경로를 설정했는데 화면 숫자는 그대로" 같은 어긋남이 생기지 않는다.
    빈 목록이면 LIKE ANY(ARRAY[]) 가 false → NOT false = true 로 전부 집계된다.
    """
    return f"NOT (COALESCE({col},'') LIKE ANY(CAST(:cwd_ignore AS text[])))"


def _now():
    return datetime.now(timezone.utc)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2


def _percentile(xs, p):
    """하위 p(0~1) 분위값. 보간 없이 순위로 고르므로 결과가 항상 실제 관측값이다."""
    s = sorted(xs)
    if not s:
        return None
    return s[min(int(len(s) * p), len(s) - 1)]


def _mad(xs, med):
    # Median Absolute Deviation (이상치에 강건한 산포 척도)
    return _median([abs(x - med) for x in xs]) or 0.0


def _like_patterns(raw: str) -> list:
    """설정에 적힌 경로 패턴(줄 단위)을 SQL LIKE 패턴으로 바꾼다.

    사용자는 '*' 와일드카드만 알면 되도록, LIKE 메타문자(% _ \\)는 백슬래시로
    이스케이프하고(LIKE 의 기본 이스케이프 문자) '*' 만 '%' 로 바꾼다.
    """
    out = []
    for line in (raw or "").splitlines():
        pat = line.strip()
        if not pat:
            continue
        pat = pat.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        out.append(pat.replace("*", "%"))
    return out


class ServiceMonitor:
    def __init__(self, session_factory):
        self._sf = session_factory
        self._interval = settings.alert_rollup_interval_sec
        self._bucket = timedelta(minutes=settings.alert_bucket_minutes)
        self._running = False
        self._thread: threading.Thread = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        if not settings.alerts_enabled:
            log.info("ServiceMonitor disabled (alerts_enabled=false)")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="service-monitor")
        self._thread.start()
        log.info("ServiceMonitor started (interval=%ds bucket=%dm)",
                 self._interval, settings.alert_bucket_minutes)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self):
        # 기동 직후 한 번 돌고, 이후 주기 반복
        while self._running:
            try:
                self.run_once()
            except Exception as e:
                log.error("ServiceMonitor cycle error: %s", e)
            for _ in range(self._interval):
                if not self._running:
                    return
                time.sleep(1)

    def run_once(self):
        db = self._sf()
        try:
            cfg = alert_settings.load(db)   # 설정 페이지에서 바꾼 값을 매 주기 반영
            self._rollup(db, cfg)
            n = self._detect(db, cfg)
            if n:
                log.info("Detected %d new service alert(s)", n)
            self._resolve_episodes(db)      # 조용해진 이상은 닫고 복구로 본다
            self._notify(db)
            self._notify_recovery(db)
            self._guard_disk(db)
        finally:
            db.close()

    def _guard_disk(self, db):
        """디스크가 임계치를 넘으면 오래된 월 파티션을 정리한다 (설정에서 끌 수 있음).

        롤업 주기(기본 5분)마다 확인하고, 한 번에 한 파티션만 지운다.
        정리 실패가 알림·롤업을 멈추지 않도록 여기서 예외를 삼킨다.
        """
        try:
            disk_guard.enforce(db)
        except Exception as e:
            db.rollback()
            log.error("디스크 자동 정리 실패: %s", e)

    # ── (1) 롤업 ─────────────────────────────────────────────────────────────
    def _rollup(self, db, cfg):
        # 늦게 도착한 로그(write_buffer 지연 등)를 흡수하도록 트레일링 윈도우를 재집계.
        win_start = _now() - timedelta(hours=2)
        db.execute(text(f"""
            INSERT INTO service_metrics AS m
                (device_id, bucket, transfers, transfer_fails, bytes,
                 transfer_secs, xfers_big, bytes_big, secs_big,
                 login_attempts, login_fails, cwd_fails)
            SELECT
                device_id,
                date_bin(:iv, log_time, TIMESTAMPTZ '{_EPOCH}') AS bucket,
                COUNT(*) FILTER (WHERE action IN ('upload','download')),
                COUNT(*) FILTER (WHERE action IN ('upload','download') AND status='fail'),
                COALESCE(SUM(file_size) FILTER (WHERE action IN ('upload','download')), 0),
                COALESCE(SUM(transfer_time) FILTER (WHERE action IN ('upload','download')), 0),
                -- 전송 속도 판정용: 큰 파일 + 성공 전송만. 작은 파일은 고정 오버헤드가,
                -- 중단된 전송은 부분 바이트가 실효속도를 왜곡한다.
                COUNT(*) FILTER (
                    WHERE action IN ('upload','download') AND status='success' AND file_size >= :large),
                COALESCE(SUM(file_size) FILTER (
                    WHERE action IN ('upload','download') AND status='success' AND file_size >= :large), 0),
                COALESCE(SUM(transfer_time) FILTER (
                    WHERE action IN ('upload','download') AND status='success' AND file_size >= :large), 0),
                COUNT(*) FILTER (WHERE action='login'),
                COUNT(*) FILTER (WHERE action='login' AND status='fail'),
                -- 존재 확인(실패 직후 그 경로가 생성됨)과 제외 경로는 실패로 세지 않는다.
                COUNT(*) FILTER (WHERE action='cwd_fail' AND {cwd_real_fail_sql('ftp_logs', ':win_start', 'NOW()')})
            FROM ftp_logs
            WHERE log_time >= :win_start
            GROUP BY device_id, bucket
            ON CONFLICT (device_id, bucket) DO UPDATE SET
                transfers      = EXCLUDED.transfers,
                transfer_fails = EXCLUDED.transfer_fails,
                bytes          = EXCLUDED.bytes,
                transfer_secs  = EXCLUDED.transfer_secs,
                xfers_big      = EXCLUDED.xfers_big,
                bytes_big      = EXCLUDED.bytes_big,
                secs_big       = EXCLUDED.secs_big,
                login_attempts = EXCLUDED.login_attempts,
                login_fails    = EXCLUDED.login_fails,
                cwd_fails      = EXCLUDED.cwd_fails,
                updated_at     = NOW()
        """), {"iv": self._bucket, "win_start": win_start,
               "large": settings.alert_large_file_bytes,
               "cwd_ignore": _like_patterns(cfg["cwd_ignore_paths"])})
        db.commit()

    # ── (2) 이상 판정 ────────────────────────────────────────────────────────
    def _detect(self, db, cfg) -> int:
        now = _now()
        bucket_sec = self._bucket.total_seconds()
        # baseline: 최근 N일 중 "막 끝난" 1시간을 제외한 구간 → 직전 평소 패턴
        base_start = now - timedelta(days=settings.alert_baseline_days)
        base_end = now - timedelta(hours=1)
        # 후보: 최근에 완전히 닫힌 버킷들 (롤업 주기 + 버킷 2개 여유)
        cand_start = now - timedelta(seconds=self._interval + 2 * bucket_sec)
        cand_end = now - self._bucket  # 진행 중 버킷 제외

        rows = db.execute(text("""
            SELECT device_id, bucket, transfers, transfer_fails, bytes,
                   transfer_secs, xfers_big, bytes_big, secs_big,
                   login_attempts, login_fails, cwd_fails
            FROM service_metrics
            WHERE bucket >= :base_start
            ORDER BY device_id, bucket
        """), {"base_start": base_start}).fetchall()

        by_dev: dict[int, list] = {}
        for r in rows:
            by_dev.setdefault(r.device_id, []).append(r)

        alerts: list[dict] = []
        for device_id, drows in by_dev.items():
            baseline = [r for r in drows if base_start <= r.bucket < base_end]
            candidates = [r for r in drows if cand_start <= r.bucket <= cand_end]
            if not candidates:
                continue
            for cand in candidates:
                alerts.extend(self._eval_bucket(device_id, cand, baseline, cfg))

        if not alerts:
            return 0

        self._annotate_cwd_paths(db, alerts, cfg)

        inserted = 0
        # 버킷 순서대로 처리해야 에피소드의 시작/지속 판단이 시간순과 어긋나지 않는다
        for a in sorted(alerts, key=lambda x: (x["device_id"], x["metric"], x["bucket"])):
            if self._record_alert(db, a):
                inserted += 1
        db.commit()
        return inserted

    def _record_alert(self, db, a) -> bool:
        """알림 한 건을 적재하고 에피소드에 반영한다. 새로 적재됐으면 True.

        진행 중인 에피소드의 반복이면 그 자리에서 발송완료로 표시해 발송 큐에서 뺀다
        (화면에는 그대로 남는다).
        """
        row = db.execute(text("""
            INSERT INTO service_alerts
                (device_id, bucket, metric, severity, value, baseline,
                 threshold, sample_count, message)
            VALUES
                (:device_id, :bucket, :metric, :severity, :value, :baseline,
                 :threshold, :sample_count, :message)
            ON CONFLICT (device_id, bucket, metric) DO NOTHING
            RETURNING id
        """), a).fetchone()
        if row is None:
            return False    # 이미 있는 버킷(재롤업) — 에피소드 상태는 건드리지 않는다
        if not self._track_episode(db, a):
            db.execute(text("UPDATE service_alerts SET notified = TRUE WHERE id = :id"),
                       {"id": row.id})
        return True

    # ── (2-1) 에피소드(진행 중인 이상) 상태 ──────────────────────────────────
    # 마지막 이상 버킷 이후 이 버킷 수만큼 조용하면 에피소드를 닫고 복구로 본다.
    # (판정은 닫힌 버킷만 대상이라 최소 2버킷이 필요하고, 늦게 도착한 로그를 위해 1버킷 더 둔다)
    _RECOVERY_QUIET_BUCKETS = 3

    @staticmethod
    def _track_episode(db, a) -> bool:
        """이 알림을 에피소드에 반영하고, 발송 대상인지 돌려준다.

        발송하는 경우는 둘뿐이다 — 에피소드의 시작(직전이 정상이었거나 복구 뒤 재발),
        그리고 주의 → 심각 등급 상승. 나머지 반복은 화면에만 남긴다.
        같은 장애가 10분 버킷마다 새 메시지로 쏟아지던 문제를 여기서 막는다.
        """
        cur = db.execute(text("""
            SELECT severity, resolved_at FROM service_alert_episodes
            WHERE device_id = :device_id AND metric = :metric
            FOR UPDATE
        """), {"device_id": a["device_id"], "metric": a["metric"]}).fetchone()
        ongoing = cur is not None and cur.resolved_at is None
        escalated = ongoing and a["severity"] == "critical" and cur.severity != "critical"

        # 복구 뒤 재발이면 같은 행을 새 에피소드로 되살린다.
        # (ON CONFLICT DO NOTHING 으로 두면 한 번 닫힌 장비는 영영 다시 알리지 못한다)
        db.execute(text("""
            INSERT INTO service_alert_episodes
                (device_id, metric, started_at, first_bucket, last_bucket, severity, alert_count)
            VALUES (:device_id, :metric, NOW(), :bucket, :bucket, :severity, 1)
            ON CONFLICT (device_id, metric) DO UPDATE SET
                started_at  = CASE WHEN service_alert_episodes.resolved_at IS NOT NULL
                                   THEN NOW() ELSE service_alert_episodes.started_at END,
                first_bucket = CASE WHEN service_alert_episodes.resolved_at IS NOT NULL
                                    THEN EXCLUDED.first_bucket
                                    ELSE service_alert_episodes.first_bucket END,
                alert_count = CASE WHEN service_alert_episodes.resolved_at IS NOT NULL
                                   THEN 1 ELSE service_alert_episodes.alert_count + 1 END,
                notified    = CASE WHEN service_alert_episodes.resolved_at IS NOT NULL
                                   THEN FALSE ELSE service_alert_episodes.notified END,
                last_bucket = GREATEST(service_alert_episodes.last_bucket, EXCLUDED.last_bucket),
                severity    = CASE WHEN service_alert_episodes.resolved_at IS NOT NULL
                                        THEN EXCLUDED.severity
                                   WHEN EXCLUDED.severity = 'critical' THEN 'critical'
                                   ELSE service_alert_episodes.severity END,
                resolved_at = NULL,
                recovery_notified = FALSE
        """), {"device_id": a["device_id"], "metric": a["metric"],
               "bucket": a["bucket"], "severity": a["severity"]})
        return (not ongoing) or escalated

    def _resolve_episodes(self, db):
        """마지막 이상 버킷 이후 조용해진 에피소드를 닫는다(발송은 _notify_recovery 가 한다).

        닫기와 발송을 나눠 두어야 발송이 실패해도 다음 주기에 다시 시도한다.
        """
        cutoff = _now() - self._bucket * self._RECOVERY_QUIET_BUCKETS
        db.execute(text("""
            UPDATE service_alert_episodes
            SET resolved_at = NOW()
            WHERE resolved_at IS NULL AND last_bucket < :cutoff
        """), {"cutoff": cutoff})
        db.commit()

    # CWD 실패가 이 비율 이상 한 경로에 몰려 있으면 침입 탐색이 아니라 경로 설정 오류로 본다
    # (홈/업로드 경로 오타, 마운트 누락, 권한 등 — 매 세션 같은 경로에서 550 이 난다).
    _CWD_PATH_CONCENTRATION = 0.9

    def _annotate_cwd_paths(self, db, alerts: list, cfg) -> None:
        """CWD 실패 급증 알림에 실패가 몰린 경로를 덧붙인다.

        service_metrics 는 경로를 갖고 있지 않으므로 알림이 실제로 뜬 버킷에 한해
        ftp_logs 를 되짚는다(알림 건수만큼만 도는 조회).
        """
        for a in alerts:
            if a["metric"] != "cwd_fail_spike":
                continue
            row = db.execute(text(f"""
                SELECT fl.file_path AS file_path, COUNT(*)::int AS n
                FROM ftp_logs fl
                WHERE fl.device_id = :device_id AND fl.action = 'cwd_fail'
                  AND fl.log_time >= :bucket AND fl.log_time < :bucket_end
                  AND {cwd_real_fail_sql('fl', ':bucket', ':bucket_end')}
                GROUP BY fl.file_path
                ORDER BY n DESC
                LIMIT 1
            """), {"device_id": a["device_id"], "bucket": a["bucket"],
                   "bucket_end": a["bucket"] + self._bucket,
                   "cwd_ignore": _like_patterns(cfg["cwd_ignore_paths"])}).fetchone()
            if not row or not row.file_path:
                continue
            total = a["value"] or 0
            # 롤업 이후 도착한 로그(write_buffer 지연)로 n 이 집계보다 클 수 있어 1.0 로 자른다
            ratio = min(row.n / total, 1.0) if total else 0
            if ratio >= self._CWD_PATH_CONCENTRATION:
                a["message"] += (f" — {row.file_path} 한 경로에 {ratio*100:.0f}% 집중"
                                 f" (홈/업로드 경로 설정 확인)")
            else:
                a["message"] += f" — 최다 경로 {row.file_path} {row.n}건"

    # 큰 파일 평균 크기가 평소와 이 배수 이상 차이 나면 전송 속도 판정을 보류한다.
    # (같은 '큰 파일'이라도 5MB 묶음과 2GB 원본은 오버헤드 비중이 달라 비교가 성립하지 않음)
    _SIZE_MIX_TOLERANCE = 4.0

    # baseline 하위 백분위를 임계로 쓰려면 표본이 이만큼은 있어야 한다.
    # (버킷 몇 개짜리 분포에서 하위 5%는 그냥 최솟값이라 의미가 없다)
    _SLOW_PCT_MIN_BUCKETS = 20

    # 임계의 이 비율보다도 더 낮으면 '심각' — 임계 자체가 장비 분포에서 나오므로
    # 심각 여부도 고정 속도가 아니라 그 장비 기준으로 판단한다.
    _CRITICAL_RATIO = 0.5

    @classmethod
    def _eval_bucket(cls, device_id, cand, baseline, cfg) -> list[dict]:
        out = []
        k = cfg["mad_k"]

        # 전송 실패율 (높을수록 나쁨)
        if cand.transfers >= cfg["min_samples"]:
            value = cand.transfer_fails / cand.transfers
            base = [r.transfer_fails / r.transfers
                    for r in baseline if r.transfers >= cfg["min_samples"]]
            med = _median(base)
            thr = cfg["fail_rate_floor"]
            if med is not None:
                thr = max(med + k * _mad(base, med), cfg["fail_rate_floor"])
            if value > thr and value > 0:
                sev = "critical" if value >= max(2 * thr, 0.5) else "warning"
                out.append(cls._mk(device_id, cand, "fail_rate", sev, value, med, thr,
                                   cand.transfers,
                                   f"전송 실패율 {value*100:.1f}% (임계 {thr*100:.1f}%)"))

        # 전송 속도 throughput (낮을수록 나쁨) — baseline 필수
        # 큰 파일(alert_large_file_bytes 이상) 성공 전송만으로 판정한다. 작은 파일은
        # 연결/인증 오버헤드가 전송시간의 대부분이라, 소량 파일 대량 업로드가
        # 성능 저하로 오인되던 문제를 막는다.
        min_big = cfg["min_large_samples"]
        if cand.xfers_big >= min_big and cand.secs_big > 0:
            value = cand.bytes_big / cand.secs_big
            base_rows = [r for r in baseline if r.xfers_big >= min_big and r.secs_big > 0]
            speeds = [r.bytes_big / r.secs_big for r in base_rows]
            med = _median(speeds)
            # 보조 가드: 파일 크기 구성이 평소와 크게 다르면 비교 자체가 성립하지 않음
            base_avg = _median([r.bytes_big / r.xfers_big for r in base_rows])
            avg = cand.bytes_big / cand.xfers_big
            mixed = bool(base_avg) and not (
                base_avg / cls._SIZE_MIX_TOLERANCE <= avg <= base_avg * cls._SIZE_MIX_TOLERANCE
            )
            if med and med > 0 and not mixed:
                low = min(med - k * _mad(speeds, med), med * (1 - cfg["throughput_drop"]))
                # 업로드 사용자마다 회선 대역폭이 달라, 장비 합산 속도는 "그 시간에 누가
                # 올렸는가"만으로도 크게 흔들린다(느린 사용자 혼자 올리는 구간 = 평소의 일부).
                # 그런 구간이 평소에도 있으면 baseline 하위 분위가 낮게 잡히고, 임계도 같이
                # 내려간다 → 장비마다 임계를 손으로 정하지 않아도 자기 분포에 맞게 완화된다.
                if len(speeds) >= cls._SLOW_PCT_MIN_BUCKETS:
                    low = min(low, _percentile(speeds, cfg["throughput_slow_pct"]))
                if value < low:
                    sev = "critical" if value < low * cls._CRITICAL_RATIO else "warning"
                    out.append(cls._mk(device_id, cand, "throughput", sev, value, med, low,
                                       cand.xfers_big,
                                       f"전송 속도 {value/1048576:.2f}MB/s "
                                       f"(평소 {med/1048576:.2f}MB/s)"))

        # 로그인 실패율 (식별된 계정 한정, 높을수록 나쁨)
        if cand.login_attempts >= cfg["min_login_samples"]:
            value = cand.login_fails / cand.login_attempts
            base = [r.login_fails / r.login_attempts
                    for r in baseline if r.login_attempts >= cfg["min_login_samples"]]
            med = _median(base)
            thr = cfg["login_fail_rate_floor"]
            if med is not None:
                thr = max(med + k * _mad(base, med), cfg["login_fail_rate_floor"])
            if value > thr and value > 0:
                sev = "critical" if value >= max(2 * thr, 0.7) else "warning"
                out.append(cls._mk(device_id, cand, "login_fail_rate", sev, value, med, thr,
                                   cand.login_attempts,
                                   f"로그인 실패율 {value*100:.1f}% (임계 {thr*100:.1f}%)"))

        # CWD 실패 급증 (절대 건수 기준 — 오탐 방지를 위해 높은 하한 적용)
        if cand.cwd_fails >= cfg["min_cwd_samples"]:
            value = float(cand.cwd_fails)
            base = [float(r.cwd_fails) for r in baseline
                    if r.cwd_fails >= cfg["min_cwd_samples"]]
            med = _median(base)
            floor = float(cfg["cwd_fail_floor"])
            thr = floor
            if med is not None:
                thr = max(med + k * _mad(base, med), floor)
            if value > thr:
                sev = "critical" if value >= 2 * thr else "warning"
                out.append(cls._mk(device_id, cand, "cwd_fail_spike", sev, value,
                                   med, thr, int(value),
                                   f"CWD 실패 {int(value)}건 급증 (임계 {thr:.0f}건)"))
        return out

    @staticmethod
    def _mk(device_id, cand, metric, sev, value, baseline, threshold, n, msg):
        return {
            "device_id": device_id, "bucket": cand.bucket, "metric": metric,
            "severity": sev, "value": float(value),
            "baseline": float(baseline) if baseline is not None else None,
            "threshold": float(threshold) if threshold is not None else None,
            "sample_count": int(n), "message": msg,
        }

    # ── (3) 알림 발송 ────────────────────────────────────────────────────────
    def _notify(self, db):
        # FOR UPDATE SKIP LOCKED: 다중 워커가 동시에 실행될 때 이중 발송 방지
        rows = db.execute(text("""
            SELECT a.id, a.device_id, d.hostname, a.bucket, a.metric,
                   a.severity, a.value, a.baseline
            FROM service_alerts a JOIN devices d ON d.id = a.device_id
            WHERE a.notified = FALSE
            ORDER BY a.created_at
            LIMIT 100
            FOR UPDATE OF a SKIP LOCKED
        """)).fetchall()
        if not rows:
            return

        ids = [r.id for r in rows]
        if not notifier.channels_configured(db):
            # 채널 미설정 → UI에만 노출. 재시도 루프 방지 위해 발송완료로 표시.
            self._mark_notified(db, ids)
            return

        alerts = [{
            "device_id": r.device_id, "device_hostname": r.hostname,
            "bucket": r.bucket, "metric": r.metric, "severity": r.severity,
            "value": r.value, "baseline": r.baseline,
        } for r in rows]

        if notifier.dispatch(alerts, db):
            # 발송에 성공한 것만 에피소드에 기록한다 — 이상 알림이 나간 적 없는 에피소드는
            # 복구 알림도 보내지 않는다(채널 미설정 장비에 복구만 날아가는 것 방지).
            self._mark_notified(db, ids, mark_episodes=True)

    @staticmethod
    def _mark_notified(db, ids, mark_episodes: bool = False):
        db.execute(
            text("UPDATE service_alerts SET notified = TRUE WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        if mark_episodes:
            db.execute(text("""
                UPDATE service_alert_episodes e SET notified = TRUE
                FROM service_alerts a
                WHERE a.id = ANY(:ids) AND e.device_id = a.device_id AND e.metric = a.metric
                  AND e.resolved_at IS NULL
            """), {"ids": ids})
        db.commit()

    # ── (4) 복구 알림 ────────────────────────────────────────────────────────
    def _notify_recovery(self, db):
        """닫힌 에피소드의 복구를 알린다. 장애 알림을 보낸 건에 대해서만 보낸다."""
        rows = db.execute(text("""
            SELECT e.device_id, d.hostname, e.metric, e.severity,
                   e.first_bucket, e.last_bucket, e.alert_count
            FROM service_alert_episodes e JOIN devices d ON d.id = e.device_id
            WHERE e.resolved_at IS NOT NULL AND e.recovery_notified = FALSE
              AND e.notified = TRUE
            ORDER BY e.resolved_at
            LIMIT 100
            FOR UPDATE OF e SKIP LOCKED
        """)).fetchall()
        if not rows:
            return

        keys = [{"device_id": r.device_id, "metric": r.metric} for r in rows]
        if not notifier.channels_configured(db):
            self._mark_recovery_notified(db, keys)
            return

        items = [{
            "device_id": r.device_id, "device_hostname": r.hostname,
            "bucket": r.last_bucket, "metric": r.metric, "severity": r.severity,
            "value": None, "baseline": None, "recovered": True,
            # 이상이 이어진 시간은 버킷 기준으로 센다 — resolved_at 은 '조용해진 것을 확인한'
            # 시각이라 대기 시간(기본 30분)까지 얹혀 실제보다 길게 나온다.
            "duration_min": int((r.last_bucket - r.first_bucket + self._bucket)
                                .total_seconds() // 60),
            "alert_count": r.alert_count,
        } for r in rows]

        if notifier.dispatch(items, db):
            self._mark_recovery_notified(db, keys)

    @staticmethod
    def _mark_recovery_notified(db, keys):
        db.execute(text("""
            UPDATE service_alert_episodes SET recovery_notified = TRUE
            WHERE device_id = :device_id AND metric = :metric
        """), keys)
        db.commit()


# 프로세스(워커)당 싱글턴
_instance: ServiceMonitor = None


def init_monitor(session_factory) -> ServiceMonitor:
    global _instance
    _instance = ServiceMonitor(session_factory)
    _instance.start()
    return _instance


def shutdown_monitor():
    if _instance:
        _instance.stop()
