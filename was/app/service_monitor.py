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
from app import notifier

log = logging.getLogger("soltrace.monitor")

_EPOCH = "2000-01-01 00:00:00+00"  # date_bin origin (버킷 경계 고정)


def _now():
    return datetime.now(timezone.utc)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2


def _mad(xs, med):
    # Median Absolute Deviation (이상치에 강건한 산포 척도)
    return _median([abs(x - med) for x in xs]) or 0.0


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
            self._rollup(db)
            n = self._detect(db)
            if n:
                log.info("Detected %d new service alert(s)", n)
            self._notify(db)
        finally:
            db.close()

    # ── (1) 롤업 ─────────────────────────────────────────────────────────────
    def _rollup(self, db):
        # 늦게 도착한 로그(write_buffer 지연 등)를 흡수하도록 트레일링 윈도우를 재집계.
        win_start = _now() - timedelta(hours=2)
        db.execute(text(f"""
            INSERT INTO service_metrics AS m
                (device_id, bucket, transfers, transfer_fails, bytes,
                 transfer_secs, login_attempts, login_fails, cwd_fails)
            SELECT
                device_id,
                date_bin(:iv, log_time, TIMESTAMPTZ '{_EPOCH}') AS bucket,
                COUNT(*) FILTER (WHERE action IN ('upload','download')),
                COUNT(*) FILTER (WHERE action IN ('upload','download') AND status='fail'),
                COALESCE(SUM(file_size) FILTER (WHERE action IN ('upload','download')), 0),
                COALESCE(SUM(transfer_time) FILTER (WHERE action IN ('upload','download')), 0),
                COUNT(*) FILTER (WHERE action='login'),
                COUNT(*) FILTER (WHERE action='login' AND status='fail'),
                COUNT(*) FILTER (WHERE action='cwd_fail')
            FROM ftp_logs
            WHERE log_time >= :win_start
            GROUP BY device_id, bucket
            ON CONFLICT (device_id, bucket) DO UPDATE SET
                transfers      = EXCLUDED.transfers,
                transfer_fails = EXCLUDED.transfer_fails,
                bytes          = EXCLUDED.bytes,
                transfer_secs  = EXCLUDED.transfer_secs,
                login_attempts = EXCLUDED.login_attempts,
                login_fails    = EXCLUDED.login_fails,
                cwd_fails      = EXCLUDED.cwd_fails,
                updated_at     = NOW()
        """), {"iv": self._bucket, "win_start": win_start})
        db.commit()

    # ── (2) 이상 판정 ────────────────────────────────────────────────────────
    def _detect(self, db) -> int:
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
                   transfer_secs, login_attempts, login_fails, cwd_fails
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
                alerts.extend(self._eval_bucket(device_id, cand, baseline))

        if not alerts:
            return 0

        inserted = 0
        for a in alerts:
            res = db.execute(text("""
                INSERT INTO service_alerts
                    (device_id, bucket, metric, severity, value, baseline,
                     threshold, sample_count, message)
                VALUES
                    (:device_id, :bucket, :metric, :severity, :value, :baseline,
                     :threshold, :sample_count, :message)
                ON CONFLICT (device_id, bucket, metric) DO NOTHING
            """), a)
            inserted += res.rowcount or 0
        db.commit()
        return inserted

    def _eval_bucket(self, device_id, cand, baseline) -> list[dict]:
        out = []
        k = settings.alert_mad_k

        # 전송 실패율 (높을수록 나쁨)
        if cand.transfers >= settings.alert_min_samples:
            value = cand.transfer_fails / cand.transfers
            base = [r.transfer_fails / r.transfers
                    for r in baseline if r.transfers >= settings.alert_min_samples]
            med = _median(base)
            thr = settings.alert_fail_rate_floor
            if med is not None:
                thr = max(med + k * _mad(base, med), settings.alert_fail_rate_floor)
            if value > thr and value > 0:
                sev = "critical" if value >= max(2 * thr, 0.5) else "warning"
                out.append(self._mk(device_id, cand, "fail_rate", sev, value, med, thr,
                                     cand.transfers,
                                     f"전송 실패율 {value*100:.1f}% (임계 {thr*100:.1f}%)"))

        # 전송 속도 throughput (낮을수록 나쁨) — baseline 필수
        if cand.transfers >= settings.alert_min_samples and cand.transfer_secs > 0:
            value = cand.bytes / cand.transfer_secs
            base = [r.bytes / r.transfer_secs for r in baseline
                    if r.transfers >= settings.alert_min_samples and r.transfer_secs > 0]
            med = _median(base)
            if med and med > 0:
                low = min(med - k * _mad(base, med), med * (1 - settings.alert_throughput_drop))
                if value < low:
                    sev = "critical" if value < med * 0.25 else "warning"
                    out.append(self._mk(device_id, cand, "throughput", sev, value, med, low,
                                        cand.transfers,
                                        f"전송 속도 {value/1048576:.2f}MB/s "
                                        f"(평소 {med/1048576:.2f}MB/s)"))

        # 로그인 실패율 (식별된 계정 한정, 높을수록 나쁨)
        if cand.login_attempts >= settings.alert_min_login_samples:
            value = cand.login_fails / cand.login_attempts
            base = [r.login_fails / r.login_attempts
                    for r in baseline if r.login_attempts >= settings.alert_min_login_samples]
            med = _median(base)
            thr = settings.alert_login_fail_rate_floor
            if med is not None:
                thr = max(med + k * _mad(base, med), settings.alert_login_fail_rate_floor)
            if value > thr and value > 0:
                sev = "critical" if value >= max(2 * thr, 0.7) else "warning"
                out.append(self._mk(device_id, cand, "login_fail_rate", sev, value, med, thr,
                                     cand.login_attempts,
                                     f"로그인 실패율 {value*100:.1f}% (임계 {thr*100:.1f}%)"))

        # CWD 실패 급증 (절대 건수 기준 — 오탐 방지를 위해 높은 하한 적용)
        if cand.cwd_fails >= settings.alert_min_cwd_samples:
            value = float(cand.cwd_fails)
            base = [float(r.cwd_fails) for r in baseline
                    if r.cwd_fails >= settings.alert_min_cwd_samples]
            med = _median(base)
            floor = float(settings.alert_cwd_fail_floor)
            thr = floor
            if med is not None:
                thr = max(med + k * _mad(base, med), floor)
            if value > thr:
                sev = "critical" if value >= 2 * thr else "warning"
                out.append(self._mk(device_id, cand, "cwd_fail_spike", sev, value,
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
        rows = db.execute(text("""
            SELECT a.id, a.device_id, d.hostname, a.bucket, a.metric,
                   a.severity, a.value, a.baseline
            FROM service_alerts a JOIN devices d ON d.id = a.device_id
            WHERE a.notified = FALSE
            ORDER BY a.created_at
            LIMIT 100
        """)).fetchall()
        if not rows:
            return

        ids = [r.id for r in rows]
        if not notifier.channels_configured():
            # 채널 미설정 → UI에만 노출. 재시도 루프 방지 위해 발송완료로 표시.
            self._mark_notified(db, ids)
            return

        alerts = [{
            "device_id": r.device_id, "device_hostname": r.hostname,
            "bucket": r.bucket, "metric": r.metric, "severity": r.severity,
            "value": r.value, "baseline": r.baseline,
        } for r in rows]

        if notifier.dispatch(alerts):
            self._mark_notified(db, ids)

    @staticmethod
    def _mark_notified(db, ids):
        db.execute(
            text("UPDATE service_alerts SET notified = TRUE WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
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
