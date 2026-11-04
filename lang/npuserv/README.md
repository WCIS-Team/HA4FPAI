# npuserv — one NPU slot, one HTTP API, any model

A supervisor that keeps the board running, puts **one** compiled 件 on the ZG330 when asked,
takes it back down when asked, and answers questions over HTTP in between. It is written so
that neither the server nor the caller ever has to touch the board's power or another
session's process to change model.

## Not LLM-only

It lives at `lang/npuserv/` — a top-level peer of `lang/asr`, `lang/kws`, `lang/tts` in the
scheduling model — rather than `lang/llm/`, because the mechanics are not about tokens. It
stays **server-managed**: the server owns its lifetime, its port (18080) and the supervisor;
the `npuserv` client lives in `lang/server/backends/llm.py` (see
[../server/README.md](../server/README.md)). The server never interprets a request body:
[server.py:362](server.py#L362) copies it whole and the worker hands it to
`engine.generate(req, emit)` ([adapter_lib.py:113](adapter_lib.py#L113)). Across `server.py`,
`adapter_lib.py` and `wire.py` there are exactly four lines that name a token-ish key — the
`max_new_tokens` → `max_new` alias ([server.py:364](server.py#L364)) and the pair that strips
`ids` out of `/status` and keeps only its length
([server.py:389](server.py#L389)-[390](server.py#L390)) — and none of them decides anything.
What the registry points at is `<kind>/<model>/board/npuserv_adapter.py`, resolved from the
registry's `"root"` key, so an ASR／TTS／detection 件 registers the same way a decoder does.
A single-shot model (one forward, one answer) fits the contract as it stands: return one end
object, emit nothing while it runs, and the `tok` event name simply never appears.

**Unverified, and worth saying plainly**: what has been driven through this server so far is
`mock` and the two LLM adapters under stub engines. No non-LLM 件 has ever been registered,
so "any model" is a statement about the contract, not a measurement.

**Verification status (2026-11-04).** The server, the protocol and both model adapters are
verified on a PC only: `tests/` runs 71 checks green against a mock and against two
stub engines. **Nothing here has been run on 113** — the board is not back, and its absence
is also why task #24 (does the 512-slot ERNIE 件 swallow a full 512-slot prompt) is still
open. Board numbers quoted below come from 台账 rows, cited by number.

## Why a worker process per model

The ZG330 session belongs to the process that ran `apply()`, and the board's single 256 MiB
CMA pool is returned only when that process exits (台账 §册三). So "放下来" cannot mean
"tell the engine to let go" — it means **the child dies**. Consequences the API makes
explicit:

| Rule | Where it shows up |
|---|---|
| One model resident at a time | `/load` while loaded → `409`; pass `evict:true` to replace it |
| `apply` is 50–132 s, not instant | `/load` waits (default `timeout:420`), or `wait:false` returns `state:"loading"` and you poll `/status` |
| A worker that dies frees the slot | `/status` goes back to `idle`, the exit reason and rc land in `history` |
| A model bug must not eat the session | `generate()` raising answers `500`, the worker stays up, and `/ping` still works |
| Logs must survive an SSH drop | handshake / params / stdout are **files** under `state/`, never pipes |

The server itself never opens a device. Model-specific steps are not in `npuserv/` either —
each 件's quirks live in that model's own directory as `npuserv_adapter.py`.

## Run it

```sh
python3 lang/npuserv/server.py           # 127.0.0.1:18080, models.json beside it
python3 lang/npuserv/server.py --port 18081 --state-dir /tmp/npuserv --python python3
```

`GET /health` answers without any model loaded, so it is the thing to poll after startup.

On a PC, load the fixture instead of a 件 (`mock` replays ids and sleeps, no icraft):

```sh
curl -s localhost:18080/models | python -m json.tool
curl -s -XPOST localhost:18080/load -d '{"model":"mock","params":{"load_ms":2000}}'
curl -s -XPOST localhost:18080/generate -d '{"model":"mock","ids":[1,2,3],"max_new":4}'
curl -s -XPOST localhost:18080/unload -d '{"model":"mock"}'
```

## API

| Route | Body | Answers |
|---|---|---|
| `GET /health` (or `/`) | — | `{ok:true, proto, state}` — `state` is the whole `/status` object, so it answers with no model loaded |
| `GET /models` | — | every registry entry with `adapter_exists` and `artifacts_present` (real on-disk checks) |
| `GET /status` | — | `state` (idle/loading/ready), `pid`, `apply_s`, `info`, `requests`, `last_result` + `last_ids_len` (the token ids are not repeated here), `last_error`, `history`, and `/proc/meminfo` readings (`CmaFree` / `MemAvailable` / `MemFree`, `{}` on Windows) |
| `GET /ping` | — | `alive` — one round trip into the worker |
| `POST /load` | `{model, params, wait:true, timeout:420, evict:false}` | `loaded`, `pid`, `apply_s`, `loading_s`, `log`, `info`; `404` unknown id or missing adapter, `409` a model is already resident, `500` the worker's own error |
| `POST /unload` | `{model}` (optional; naming a different model → `409`) | `unloaded`, `state`, `mem_before`, `mem_after`, `log` |
| `POST /generate` | per model, plus `stream:false`, `wait_s:10`, `timeout:900` | the engine's end object + `events` + `job`; `stream:true` answers chunked `application/x-ndjson` of the same events as they happen |

Event shapes are the ones the ERNIE daemon already spoke (台账 §廿六):
`{"pre":i,"n":N}` while the prompt is walked, `{"tok":t,"i":k,"sec":dt}` per generated
token, then one end object with `ids` / `reason` / `prompt` / `gen` / `sec`.

## Adapter contract (what a model directory has to provide)

`npuserv_adapter.py` is run as a process, not imported. It calls `adapter_lib.run(load)`
where `load(params)` returns an engine:

```python
engine.info      # dict, shown verbatim by /status and /load
engine.apply_s   # seconds spent in apply(), for the handshake
engine.n_steps   # cumulative step counter; /ping reads it (optional)
engine.generate(req, emit) -> dict   # emit() per event; the return is the end object
engine.close()   # optional
```

The server passes params as a JSON file (`NPUSERV_PARAMS`); the worker writes its port and
status into a handshake file (`NPUSERV_HANDSHAKE`) **before** `apply()` starts and again
after, so a 132 s apply and a crash inside it are distinguishable. `NPUSERV_IDLE` lets a
worker exit on its own after that many seconds with no client attached; the server sets `0`
(forever) for model workers, so only the caller takes it down.

Model-specific things stay on the model side:

- **ERNIE-4.5-0.3B** (`llm/ERNIE-4.5-0.3B-PT4icraft430tai/board/npuserv_adapter.py`) is glue
  over `e41_chatd.py`, the module validated on the board (padfix, `POS_OFFSET`, the 511-slot
  policy gate, narrow KV readback, `MAX_NEW_CAP=192`, stop `{2,100272}`). Params map onto the
  env that file reads at import, so a bare `/load` gives the 现役 configuration. It refuses
  `text`: this 件 is ids-in/ids-out, see `capabilities.text_in` in `models.json` and 台账
  #220. `max_slots` raises the *policy* gate only — `E41.step` still stops at the compiled
  window — which is task #24's entry point.
- **Qwen3-0.6B** (`llm/Qwen3-0.6B/board/npuserv_adapter.py`) wraps `patch/q3_infer_board.py`
  and reads the KV window off the graph rather than the config. Two things it must feed are
  non-negotiable: `token_dynamic_fm` is **3 fp32 = 12 B** (the ZG graph declares `[1,3]`; the
  vendor script feeds 8 B and the board rejects it — 台账 #196), and position 0 is not
  expressible (`op_id 4525`). The vendored `run_prompt` and `run_decode` disagree about the
  position fed per slot, so `pos_rule` picks an arm: `"vendor"` (default, reproduces both
  loops exactly — the sequence the Qwen2 chain that spoke coherent Chinese used) or
  `"uniform"` (one rule everywhere). Neither arm has run on a board: this 件 is at `apply
  75.1 s`, zero tokens (台账 #212), and the driver itself has never been on the board
  (#208–#211: that push carried json + raw + tokenizer only).

### Where the per-model difference is written down

Four places, and they are deliberately not the same kind of record:

| 记的是什么 | 在哪 |
|---|---|
| 机器读的差分：能力门（`ids_in`／`text_in`／`text_out`）、`defaults`、`env`、件在板上的绝对路径 | `models.json` 那一条 entry，运行时由 `GET /models` 原样回答（含 `adapter_exists`／`artifacts_present`） |
| 写代码时的差分：那颗件的非 negotiable 喂法、位置口径、为什么拒 text | 各自 `board/npuserv_adapter.py` 的文件头 |
| 人读的差分：上面两条 bullets | 本节 |
| **实测**差分：每步耗时、KV 上行字节、内存与 CMA 谷底、窗口裁剪对照 | **不在这里**——正本＝[[LLM上板要求与候选模型对照]] §6.6 与 [[板端我方代码与产物台账]] §21.1（三颗横表）、窄读与 KV 常驻两篇实测页；规矩是一个事实只写一处，所以这张表只指路、不抄数 |

颗与颗在 API 层的差分只有一条：`/load` 的 `params` 命名空间不同（ERNIE 认 `pos_offset`／
`padfix`／`max_slots`／`max_new_cap`，Qwen3 认 `pos_rule`／`driver_dir`／`config_path`），
端点、事件、槽位规则两边完全一样。

## Board layout

The registry carries `"root": ".."`, so `adapter` paths resolve against `lang/` — the tree
on 113 has to mirror the repo:

```
<base>/npuserv/{server.py,adapter_lib.py,wire.py,models.json,tests/}
<base>/llm/ERNIE-4.5-0.3B-PT4icraft430tai/board/{e41_chatd.py,npuserv_adapter.py}
<base>/llm/Qwen3-0.6B/board/npuserv_adapter.py
<base>/llm/Qwen3-0.6B/patch/q3_infer_board.py
```

`models.json` points the 件 at their board paths (`/mnt/data/ernie/jr_path/...` for ERNIE,
`/root/qwen3/...` for Qwen3 — those roots are where each session left them, not a choice
made here). Run `python3 server.py` from `<base>/npuserv`.

Before a first load on the board, read the gate rather than assume it: `CmaTotal` is
262,144 kB of which 128 MiB is taken at boot by `udmabuf0`, so a loaded model gets ≈130 MB
of CMA and **must not share a window with the live video chain** (台账 §册三, [[NPU调用链]]
§7.4). The API is enough to check that gate: `GET /status` carries the `/proc/meminfo`
readings at all times, and `POST /unload` returns `mem_before` / `mem_after` around the kill.

## Tests

```sh
python -X utf8 lang/npuserv/tests/lifecycle_check.py       # 37 checks, mock engine, no NPU
python -X utf8 lang/npuserv/tests/adapter_stub_check.py    # 12 checks, real ERNIE adapter + stub e41_chatd
python -X utf8 lang/npuserv/tests/q3_adapter_stub_check.py # 22 checks, real Qwen3 adapter + stub driver
```

The two stub runs exist because the adapters' whole job is translation, and translation is
testable without a board: the fakes expose the real module's attribute names, record what
they were called with, and the checks assert positions, cache contiguity, the 12 B tdf, the
event stream and the failure paths. They do not cover any arithmetic — that stays 台账 读数.

Written tests caught two real defects on 2026-11-04: `adapter_lib._select` unpacked three
values from a call that returns one (every connect to a ready worker reset the socket), and
`adapter_lib` overwrote `engine.n_steps` unconditionally (a model owning a read-only counter
could not load).
