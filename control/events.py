# -*- coding: utf-8 -*-
"""事件 envelope 与实例级 producer 序号（规划 §时间与跨库关联）。

producer_id 是**生产者实例 UUID**（每进程启动新生成，可加 component_id 溯源）；
producer_seq 只在实例内递增，重启产生新 producer_id，因此 (producer_id, seq)
天然不撞号。事件只追加，不作为第二业务主库。
"""
import json
import threading

from . import ids
from .errors import ConflictError


class EventLog(object):
    def __init__(self, store, component_id='ha4fpai-control'):
        self.store = store
        self.component_id = component_id
        self.producer_id = ids.new_id()
        self._seq = 0
        self._lock = threading.Lock()

    def next_seq(self):
        with self._lock:
            self._seq += 1
            return self._seq

    def append(self, type_, payload, request_id=None, session_id=None, person_id=None,
               device_id=None, recording_id=None, media_id=None,
               time_quality='local-wall+monotonic'):
        seq = self.next_seq()
        event_id = ids.new_id()
        now = ids.now_iso()
        try:
            with self.store.tx() as conn:
                conn.execute(
                    'INSERT INTO events(event_id, producer_id, component_id, producer_seq,'
                    ' type, request_id, session_id, person_id, device_id, recording_id,'
                    ' media_id, boot_id, wall_time, monotonic_ns, time_quality,'
                    ' payload_json, created_at)'
                    ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (event_id, self.producer_id, self.component_id, seq, type_,
                     request_id, session_id, person_id, device_id, recording_id,
                     media_id, ids.boot_id(), now, ids.monotonic_ns(), time_quality,
                     json.dumps(payload or {}, ensure_ascii=False), now))
        except Exception as exc:
            # (producer_id, seq) 唯一；实例内正常不会撞，撞了是程序错误，如实报
            raise ConflictError('event_seq_conflict', '事件序号冲突: %s' % exc)
        return {'event_id': event_id, 'producer_id': self.producer_id,
                'producer_seq': seq, 'type': type_, 'wall_time': now}

    def list_after(self, after_seq=0, limit=100):
        limit = max(1, min(int(limit), 500))
        rows = self.store.query(
            'SELECT * FROM events WHERE producer_seq > ? ORDER BY producer_seq LIMIT ?',
            (int(after_seq), limit))
        for row in rows:
            row['payload'] = json.loads(row.pop('payload_json'))
        next_cursor = rows[-1]['producer_seq'] if rows else int(after_seq)
        return {'events': rows, 'next_cursor': next_cursor}
