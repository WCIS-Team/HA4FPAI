#!/usr/bin/env python3
"""Board -> MQTT adapter (no change to any board service).

The board keeps owning the camera, the SDK, the DMA and the recorder. This
process only *reads* the board's existing HTTP endpoints and republishes them as
a proper MQTT device:

  * device identity   : identifiers / manufacturer / model / sw_version
  * MQTT discovery    : retained homeassistant/<component>/board113_<obj>/config
  * availability      : LWT (broker publishes "offline" if this process dies)
  * state             : retained, QoS1
  * event             : an MQTT event entity fires when a new recording appears

Because it speaks MQTT with its own client certificate, HA sees a normal MQTT
device with a device page, not a REST-polled sensor.

Read-only contract: every board call is a GET. Nothing here POSTs to 8093, so the
recorder mode can never be changed by this adapter.
"""
import argparse
import json
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

import paho.mqtt.client as mqtt

NODE = 'board113'
DEVICE = {
    'identifiers': ['wcis-board-113'],
    'name': 'WCIS Board 113',
    'manufacturer': 'WCIS',
    'model': 'FMQL30TAI676H (SDI -> PL -> PS -> VPU)',
    'sw_version': 'vision-20261103',
}
AVAILABILITY = 'ha-small/%s/availability' % NODE
EVENT_TOPIC = 'ha-small/%s/event' % NODE

SENSORS = {
    'capture_state': dict(name='Capture state', value_json='state',
                          url='/api/status', port=8092, icon='mdi:cctv'),
    'captured_frames': dict(name='Captured frames', value_json='captured',
                            url='/api/status', port=8092, icon='mdi:counter',
                            unit='frames'),
    'frame_age': dict(name='Frame age', value_json='frame_age_s',
                      url='/api/status', port=8092, icon='mdi:timer-sand', unit='s'),
    'recorder_mode': dict(name='Recorder mode', value_json='mode',
                          url='/api/status', port=8093, icon='mdi:record-rec'),
    'recorder_state': dict(name='Recorder state', value_json='state',
                           url='/api/status', port=8093, icon='mdi:state-machine'),
    'source_ready': dict(name='Recorder source ready', value_json='source_ready',
                         url='/api/status', port=8093, icon='mdi:check-network'),
    'recordings': dict(name='Recordings', value_json=None,
                       url='/api/recordings', port=8093, icon='mdi:filmstrip',
                       unit='files'),
}


def get_json(host, port, path, timeout=8.0):
    with urllib.request.urlopen('http://%s:%d%s' % (host, port, path), timeout=timeout) as r:
        return json.load(r)


class Adapter:
    def __init__(self, args):
        self.args = args
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id='board113-adapter')
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.will_set(AVAILABILITY, 'offline', qos=1, retain=True)
        self.connected = False
        self.last_recording_id = None
        self.published = 0
        self.events = 0
        self.errors = []

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = reason_code == 0
        print('mqtt connected (%s)' % reason_code, flush=True)
        if self.connected:
            client.publish(AVAILABILITY, 'online', qos=1, retain=True)
            self.publish_discovery()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        print('mqtt disconnected (%s)' % reason_code, flush=True)

    def _config_topic(self, obj):
        return 'homeassistant/sensor/%s/%s/config' % (NODE, obj)

    def publish_discovery(self):
        for obj, meta in SENSORS.items():
            payload = {
                'name': meta['name'],
                'unique_id': '%s_%s' % (NODE, obj),
                'state_topic': 'ha-small/%s/%s' % (NODE, obj),
                'availability_topic': AVAILABILITY,
                'payload_available': 'online',
                'payload_not_available': 'offline',
                'device': DEVICE,
                'entity_category': 'diagnostic',
            }
            if meta.get('icon'):
                payload['icon'] = meta['icon']
            if meta.get('unit'):
                payload['unit_of_measurement'] = meta['unit']
            self.client.publish(self._config_topic(obj), json.dumps(payload), qos=1, retain=True)
        # Event entity for new recordings.
        self.client.publish('homeassistant/event/%s/recording/config' % NODE, json.dumps({
            'name': 'New recording',
            'unique_id': '%s_recording_event' % NODE,
            'state_topic': EVENT_TOPIC,
            'event_types': ['recording_complete'],
            'device': DEVICE,
        }), qos=1, retain=True)

    def connect(self):
        self.client.tls_set(ca_certs=self.args.ca,
                            certfile=self.args.cert, keyfile=self.args.key,
                            cert_reqs=ssl.CERT_REQUIRED)
        self.client.connect(self.args.broker, self.args.port, keepalive=20)
        self.client.loop_start()
        deadline = time.time() + 15
        while time.time() < deadline and not self.connected:
            time.sleep(0.3)
        return self.connected

    def poll_once(self):
        host = self.args.board
        for obj, meta in SENSORS.items():
            try:
                data = get_json(host, meta['port'], meta['url'])
            except (urllib.error.URLError, OSError, ValueError) as exc:
                self.errors.append('%s: %s' % (obj, str(exc)[:60]))
                continue
            if obj == 'recordings':
                value = len(data.get('recordings') or [])
                recs = data.get('recordings') or []
                newest = recs[0].get('id') if recs else None
                if newest and newest != self.last_recording_id:
                    if self.last_recording_id is not None:
                        self.client.publish(EVENT_TOPIC, json.dumps(
                            {'event_type': 'recording_complete', 'id': newest}), qos=1)
                        self.events += 1
                    self.last_recording_id = newest
            else:
                value = data.get(meta['value_json'])
            self.client.publish('ha-small/%s/%s' % (NODE, obj), str(value), qos=1, retain=True)
            self.published += 1

    def run(self):
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *a: stop.set())
        signal.signal(signal.SIGINT, lambda *a: stop.set())
        while not stop.is_set():
            if self.connected:
                self.poll_once()
            time.sleep(self.args.interval)
        try:
            self.client.publish(AVAILABILITY, 'offline', qos=1, retain=True).wait_for_publish(3)
        except Exception:  # noqa: BLE001
            pass
        self.client.loop_stop()
        self.client.disconnect()
        print('adapter stopped: published=%d events=%d errors=%d'
              % (self.published, self.events, len(self.errors)), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--broker', default='10.88.0.67')
    p.add_argument('--port', type=int, default=8883)
    p.add_argument('--board', default='192.168.1.113')
    p.add_argument('--ca', default='/tmp/mtls/ca.crt')
    p.add_argument('--cert', default='/tmp/mtls/client_adapter.crt')
    p.add_argument('--key', default='/tmp/mtls/client_adapter.key')
    p.add_argument('--interval', type=float, default=10.0)
    args = p.parse_args()

    adapter = Adapter(args)
    if not adapter.connect():
        print('could not connect to the broker over mTLS', file=sys.stderr)
        return 2
    adapter.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
