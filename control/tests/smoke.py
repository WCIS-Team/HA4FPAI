# -*- coding: utf-8 -*-
"""control 骨架自检：纯标准库、临时目录、不依赖板端模型/网络服务。

跑法（仓库根）：
    python -B -X utf8 control/tests/smoke.py
输出 PASS/FAIL 计数；全绿退出码 0。板上 py3.8 语法兼容按构造保证，
板端实跑另按实施路线验收（本机 3.13 通过不代表板端结论）。
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import commands, ids, outbox, persons, samples, tasks  # noqa: E402
from control import voiceprint_import  # noqa: E402
from control.actions import build_registry, interpret, DIRECT_PHRASES  # noqa: E402
from control.adapters import klar as klar_adapter  # noqa: E402
from control.adapters import video  # noqa: E402
from control.errors import (ConflictError, DomainError, ForbiddenError,  # noqa: E402
                            ConfirmationRequired, VideoDomainNotConnected)
from control.events import EventLog  # noqa: E402
from control.service import App, Handler  # noqa: E402
from control.settings import Settings  # noqa: E402
from control.store import Store  # noqa: E402

RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


def raises(fn, exc_type, name):
    try:
        fn()
        check(False, name + '（未抛出）')
    except exc_type:
        check(True, name)
    except Exception as exc:  # noqa: BLE001
        check(False, name + '（抛了别的: %r）' % exc)


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-control-smoke-')
    try:
        run_all(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('smoke: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


def run_all(tmp):
    db = os.path.join(tmp, 'business.sqlite3')
    store = Store(db)
    events = EventLog(store)
    settings = Settings(store)
    registry = build_registry(store, events, settings)

    # ---- store / meta ----
    check(store.meta('store_id'), 'store_id 已生成')
    check(store.integrity() == 'ok', 'integrity_check ok')
    check(store.restore_epoch() == 1, '初始 restore_epoch=1')
    check(store.bump_restore_epoch() == 2, 'restore_epoch 递增')
    check(store.restore_epoch() == 2, 'restore_epoch 持久')

    # ---- persons：同名不合并 ----
    alice1 = persons.create(store, events, '张三')
    alice2 = persons.create(store, events, '张三')
    check(alice1['person_id'] != alice2['person_id'], '同名人员是两个 person_id')
    raises(lambda: persons.create(store, events, ''), ConflictError, '空姓名拒绝')
    raises(lambda: persons.create(store, events, 'x' * 41), ConflictError, '超长姓名拒绝')
    updated = persons.update(store, events, alice1['person_id'], display_name='张三丰',
                             expected_revision=1)
    check(updated['display_name'] == '张三丰' and updated['revision'] == 2, '改名后 person_id 不变')
    raises(lambda: persons.update(store, events, alice1['person_id'],
                                  display_name='再改', expected_revision=1),
           ConflictError, '旧 expected_revision 冲突 409')

    # ---- models / samples ----
    samples.register_model(store, events, 'campplus-zh-common', 'voice', 192,
                           model_sha256='a' * 64)
    raises(lambda: samples.register_model(store, events, 'campplus-zh-common', 'voice', 512,
                                          model_sha256='a' * 64),
           ConflictError, '同模型异指纹拒绝')
    vec = [0.01] * 192
    norm = sum(v * v for v in vec) ** 0.5
    vec = [v / norm for v in vec]
    sample = samples.enroll(store, events, 'voice', alice1['person_id'],
                            'campplus-zh-common', vec, require_unit_norm=True)
    check(sample['dim'] == 192 and sample['dtype'] == 'float32-le', '声纹样本入库（fp32-le）')
    blob = bytes(sample['blob'])
    decoded = samples.decode_vector(blob, 'float32-le', 192)
    check(all(abs(a - b) < 1e-6 for a, b in zip(decoded, vec)), '向量编码 round-trip')
    raises(lambda: samples.enroll(store, events, 'voice', alice1['person_id'],
                                  'campplus-zh-common', [0.1] * 191),
           ConflictError, '维度不符拒绝')
    raises(lambda: samples.enroll(store, events, 'voice', alice1['person_id'],
                                  'unknown-model', vec), DomainError, '未登记模型拒绝')
    raises(lambda: samples.enroll(store, events, 'voice', alice2['person_id'],
                                  'campplus-zh-common', [float('nan')] + [0.0] * 191),
           ConflictError, 'NaN 拒绝')
    # fp64 无损
    samples.register_model(store, events, 'face-mock', 'face', 4, dtype='float64-le')
    v64 = samples.enroll(store, events, 'face', alice1['person_id'], 'face-mock',
                         [0.1, 0.2, 0.3, 0.4], dtype='float64-le')
    check(samples.decode_vector(bytes(v64['blob']), 'float64-le', 4) == [0.1, 0.2, 0.3, 0.4],
          'fp64 无损 round-trip')

    # ---- settings：revision 冲突 + 防乱序 ----
    revs = settings.update({'audio.output.volume': 30})
    check(revs['audio.output.volume'] == 1, '设置首写 revision=1')
    raises(lambda: settings.update({'audio.output.volume': 40},
                                   expected_revisions={'audio.output.volume': 0}),
           ConflictError, '设置过期修订 409')
    revs = settings.update({'audio.output.volume': 40},
                           expected_revisions={'audio.output.volume': 1})
    check(revs['audio.output.volume'] == 2, '设置 revision 递增')
    entry = settings.get('recording.mode')
    check(entry['applied'] is False, 'recorder 域 desired≠applied（视频域未接，如实报告）')
    fb = settings.apply_feedback('recorder', 2, 1)
    check(fb['accepted'] == ['recording.mode'], 'recorder 回执按当前 epoch 接受')
    fb = settings.apply_feedback('recorder', 2, 2)
    check(fb.get('accepted') == ['recording.mode'], '更新版本回执接受')
    fb = settings.apply_feedback('recorder', 2, 1)
    check(fb.get('superseded') == ['recording.mode'], '严格旧版本回执标 superseded')
    fb = settings.apply_feedback('recorder', 2, 2)
    check(fb.get('accepted') == ['recording.mode'], '同版本重放幂等接受（不覆盖变化）')
    raises(lambda: settings.update({'voice.match.threshold': 'high'}),
           ConflictError, '设置类型错误拒绝')

    # ---- voiceprint_import：两种格式只读预览 ----
    old_path = os.path.join(tmp, 'voiceprints.json')
    with open(old_path, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'dim': 4, 'speakers': {'张三': [0.5] * 4, '李四': [0.1] * 4}}, f)
    preview_old = voiceprint_import.preview(old_path, source='boards')
    check(preview_old['format'] == 'boards-voiceprints-v1' and preview_old['people_count'] == 2,
          'boards 格式识别（单向量）')
    new_path = os.path.join(tmp, 'library.json')
    with open(new_path, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'dim': 4, 'updated': '2026-11-04T00:00:00+00:00',
                   'speakers': {'张三': {'vectors': [[0.2] * 4, [0.3] * 4], 'updated': 'x'}}}, f)
    preview_new = voiceprint_import.preview(new_path, source='lang')
    check(preview_new['format'] == 'lang-vertify-library-v1'
          and preview_new['sample_count'] == 2, 'lang 格式识别（多样本）')
    cmp = voiceprint_import.compare_previews([preview_old, preview_new])
    check(any(c['name'] == '张三' for c in cmp['name_conflicts']), '同名跨源列为待核对冲突')
    bad_path = os.path.join(tmp, 'bad.json')
    with open(bad_path, 'w', encoding='utf-8') as f:
        f.write('{not json')
    raises(lambda: voiceprint_import.preview(bad_path), ConflictError, '坏 JSON 报错不吞')

    # ---- commands：幂等 / 确认 / 视频留空 / unknown ----
    r1 = commands.create(store, registry, events, 'system.time',
                         idempotency_key='k-time-1')
    check(r1['command']['status'] == 'succeeded', 'system.time 成功')
    r2 = commands.create(store, registry, events, 'system.time',
                         idempotency_key='k-time-1')
    check(r2['replayed'] and r2['command']['command_id'] == r1['command']['command_id'],
          '同键同参幂等复用')
    raises(lambda: commands.create(store, registry, events, 'system.time',
                                   params={'x': 1}, idempotency_key='k-time-1'),
           ConflictError, '同键异参 409')
    raises(lambda: commands.create(store, registry, events, 'no.such'),
           DomainError, '未登记动作 404')
    raises(lambda: commands.create(store, registry, events, 'person.create', params={}),
           DomainError, '缺必填参数拒绝')
    rv = commands.create(store, registry, events, 'camera.recognize')
    check(rv['command']['status'] == 'failed'
          and rv['command']['code'] == 'video_domain_not_connected',
          '视频域留空桩返回明确错误码（不假成功）')
    raises(lambda: commands.create(store, registry, events, 'person.delete',
                                   params={'person_id': alice2['person_id']}),
           ConfirmationRequired, '危险动作无确认拒绝')
    conf = commands.create_confirmation(store, registry, 'person.delete',
                                        {'person_id': alice2['person_id']})
    wrong = commands.create_confirmation(store, registry, 'person.delete',
                                         {'person_id': 'other'})
    raises(lambda: commands.create(store, registry, events, 'person.delete',
                                   params={'person_id': alice2['person_id']},
                                   confirmation_id=wrong['confirmation_id']),
           ForbiddenError, '确认与参数不匹配拒绝')
    rd = commands.create(store, registry, events, 'person.delete',
                         params={'person_id': alice2['person_id']},
                         confirmation_id=conf['confirmation_id'])
    check(rd['command']['status'] == 'succeeded', '确认后删除成功')
    raises(lambda: commands.create(store, registry, events, 'person.delete',
                                   params={'person_id': alice1['person_id']},
                                   confirmation_id=conf['confirmation_id']),
           ForbiddenError, '确认一次性消费')
    bob = persons.create(store, events, '合并来源李四')
    merged = persons.merge(store, events, bob['person_id'], alice1['person_id'])
    check(persons.get(store, bob['person_id'])['status'] == 'merged', '合并后来源为 merged')
    check(persons.get(store, alice1['person_id'])['status'] == 'active', '合并目标保持 active')
    # unknown：accepted 态命令回执丢失 → 显式置 unknown；终态不可再改
    with store.tx() as conn:
        now = ids.now_iso()
        conn.execute(
            "INSERT INTO commands(command_id, request_id, idempotency_key, action,"
            " params_json, params_digest, status, created_at, updated_at)"
            " VALUES(?,?, 'k-unk-manual', 'system.time', '{}', 'digest-x', 'accepted', ?, ?)",
            (ids.new_id(), ids.new_id(), now, now))
    row = store.query_one("SELECT * FROM commands WHERE idempotency_key='k-unk-manual'")
    uk = commands.fail_unknown(store, events, row['command_id'], '模拟回执丢失')
    check(uk['status'] == 'unknown', 'accepted → unknown 迁移')
    raises(lambda: commands.fail_unknown(store, events, row['command_id']),
           ConflictError, '终态不可再改 unknown')

    # ---- 事件 ----
    feed = events.list_after(0, 500)
    check(len(feed['events']) > 0 and feed['next_cursor'] >= len(feed['events']),
          '事件游标读取')
    types = [e['type'] for e in feed['events']]
    check('person.created' in types and 'command.succeeded' in types, '事件类型齐全')

    # ---- 导出 ----
    exp1 = outbox.export_registry(store, os.path.join(tmp, 'exports'))
    check(exp1['manifest']['export_schema'] == 'klar-registry-v1', '导出 manifest')
    current = outbox.read_current(os.path.join(tmp, 'exports'))
    check(current and current['snapshot'] == exp1['manifest']['snapshot'], 'current 指针')
    outbox.deliver(store, 'klar-engine', 'klar-registry-v1', current['source_revision'])
    raises(lambda: outbox.deliver(store, 'klar-engine', 'klar-registry-v1',
                                  current['source_revision'] - 1),
           DomainError, '消费游标倒退拒绝')

    # ---- tasks：幂等尝试 + 破坏性任务需确认 ----
    tk = tasks.create(store, registry, 'system.time', {}, run_at=ids.now_iso())
    run1 = tasks.run_due(store, registry, events, commands)
    run2 = tasks.run_due(store, registry, events, commands)
    check(len(run1) == 1 and run1[0]['state'] == 'succeeded', '到期任务执行')
    check(len(run2) == 0, '重跑不重复执行（occurrence 唯一）')
    tdel = tasks.create(store, registry, 'person.delete',
                        {'person_id': alice1['person_id']}, run_at=ids.now_iso())
    run3 = tasks.run_due(store, registry, events, commands)
    check(len(run3) == 1 and run3[0]['state'] == 'needs_confirmation',
          '破坏性任务只生成待确认，不自动执行')

    # ---- klar 适配 / interpret ----
    check(klar_adapter.match_direct('现在几点', DIRECT_PHRASES)['action'] == 'system.time',
          'direct 整句命中')
    check(klar_adapter.match_direct('不要开始录制', DIRECT_PHRASES) is None,
          '否定句不命中开始录制')
    check(klar_adapter.match_direct('怎么开始录制', DIRECT_PHRASES) is None,
          '询问句不命中')
    check(klar_adapter.match_direct('你说开始录制了吗', DIRECT_PHRASES) is None,
          '引述句不命中')
    out = interpret(store, registry, '不要开始录制', klar_post=lambda *a, **k: (_ for _ in ()).throw(
        OSError('refused')))
    check(out['source'] == 'llm_handoff' and not out['proposals'],
          '否定句不执行设备动作')
    out = interpret(store, registry, '小克小克')
    check(out['source'] == 'direct_reply' and out['reply'] == '我在。', '固定应答')
    out = interpret(store, registry, '打开客厅的灯',
                    klar_post=lambda url, payload, timeout: (0, None))
    check(out['source'] == 'llm_handoff', 'klar 不可用 → 交 LLM，不假执行')

    # ---- 视频适配器状态 ----
    check(video.status()['connected'] is False, '视频域状态：未接')
    raises(lambda: video.recognize(), VideoDomainNotConnected, 'video.recognize 留空桩')

    # ---- HTTP 面 ----
    http_checks(tmp)


def http_checks(tmp):
    app = App(os.path.join(tmp, 'http.sqlite3'), os.path.join(tmp, 'exports-http'),
              token='test-token')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.app = app
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:%d' % port
    try:
        status, obj = request(base, 'GET', '/healthz')
        check(status == 200 and obj.get('ok'), 'GET /healthz')
        status, obj = request(base, 'GET', '/v1/capabilities')
        video_actions = [a for a in obj['actions'] if a['capability'] == 'video']
        check(status == 200 and video_actions
              and all(not a['connected'] for a in video_actions),
              '能力清单：视频域如实标未接')
        status, _ = request(base, 'GET', '/v1/persons')
        check(status == 401, '无令牌 401')
        status, obj = request(base, 'POST', '/v1/commands',
                              body={'action': 'system.time'}, token='test-token')
        check(status == 200 and obj['command']['status'] == 'succeeded', '带令牌执行命令')
        status, obj = request(base, 'POST', '/v1/persons',
                              body={'display_name': '网页建的人'}, token='test-token')
        pid = obj['person']['person_id']
        check(status == 200 and pid, 'POST 建人')
        status, _ = request(base, 'POST', '/v1/persons',
                            body={'display_name': 'x'}, token='test-token',
                            origin='http://evil.example')
        check(status == 403, '跨源变更 403')
        status, obj = request(base, 'POST', '/v1/commands',
                              body={'action': 'recording.set_mode',
                                    'params': {'mode': 'manual'}}, token='test-token')
        check(status == 200 and obj['command']['status'] == 'failed'
              and obj['command']['code'] == 'video_domain_not_connected',
              'HTTP 层同样拿到留空错误码')
        status, obj = request(base, 'POST', '/v1/interpret',
                              body={'text': '不要开始录制'}, token='test-token')
        check(status == 200 and not obj['proposals'], 'HTTP interpret 否定句不产生提案')
    finally:
        server.shutdown()
        server.server_close()


def request(base, method, path, body=None, token=None, origin=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    if origin:
        req.add_header('Origin', origin)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode('utf-8'))
        except ValueError:
            return exc.code, {}


if __name__ == '__main__':
    sys.exit(main())
