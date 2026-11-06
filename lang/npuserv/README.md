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

**Verification status (2026-11-06).** The server, the protocol and the three model adapters
are verified on a PC: `tests/` runs 93 checks green against a mock and against three stub
engines. **On 113 the server has been started (`/root/npuserv`, 11-04 11:45 — its
`server`/`adapter_lib`/`wire` cpython-38 bytecode is on the board), and since 2026-11-06
14:25 CST one real 件 has gone all the way through it: Qwen2-0.5B** — `/load` 137.4 s
(`apply_s` 137.0), `/generate {"text":"你是谁？","max_new":24}` 17 tokens in 6.3 s
(`我是阿里云开发的超大规模语言模型，我叫通义千问。`), `/unload` returning `MemAvailable`
to 662,800 kB (台账 §卅九). Also on 2026-11-06 (~19:50) **ERNIE-4.5-0.3B** generated through
npuserv (5 prompts, correct zh→en translation, deterministic repeat); **Qwen3-0.6B** loads
and emits tokens but generation is degenerate — see its model README. Board-side pieces
measured elsewhere stay narrower: `e41_tok.py` loads and
encodes on the board's Python 3.8.10, `import e41_chatd` succeeds there — neither has been
driven through `npuserv`. Board numbers quoted below come from 台账 rows, cited by number.
Task #24 (does the 512-slot ERNIE 件 swallow a full 512-slot prompt) is still open for the
same reason.

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
| `GET /models` | — | every registry entry with `adapter_exists`, `artifacts_present` (real on-disk checks) and `compiled_with` (declaration, not a check) |
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

- **ERNIE-4.5-0.3B** (`llm/models/ERNIE-4.5-0.3B-PT4icraft430tai/board/npuserv_adapter.py`) is glue
  over `e41_chatd.py`, the module validated on the board (padfix, `POS_OFFSET`, the 511-slot
  policy gate, narrow KV readback, `MAX_NEW_CAP=192`, stop `{2,100272}`). Params map onto the
  env that file reads at import, so a bare `/load` gives the 现役 configuration. It takes
  `text` as well as `ids`: tokenization runs on the board through `board/e41_tok.py` (pure
  stdlib + a 3.3 MB `e41_tok.pack`, no `tokenizers`), verified against `tokenizers` 0.23.3
  over 219,085 comparisons on the PC and loaded/encoded on the board — but never through a
  real generate. `max_slots` raises the *policy* gate only — `E41.step` still stops at the
  compiled window — which is task #24's entry point.
- **Qwen3-0.6B** (`llm/models/Qwen3-0.6B/board/npuserv_adapter.py`) wraps `patch/q3_infer_board.py`
  and reads the KV window off the graph rather than the config. Two things it must feed are
  non-negotiable: `token_dynamic_fm` is **3 fp32 = 12 B** (the ZG graph declares `[1,3]`; the
  vendor script feeds 8 B and the board rejects it — 台账 #196), and position 0 is not
  expressible (`op_id 4525`). The vendored `run_prompt` and `run_decode` disagree about the
  position fed per slot, so `pos_rule` picks an arm: `"vendor"` (default, reproduces both
  loops exactly — the sequence the Qwen2 chain that spoke coherent Chinese used) or
  `"uniform"` (one rule everywhere). **Both arms now have run on a board (2026-11-06): the 件
  loads (apply ≈171 s) and emits tokens, but generation is degenerate — a constant token,
  identical under both `pos_rule` arms.** Root cause localised to the decode-step KV feedback
  (not `pos_rule`, not the tokenizer); shelved for the Qwen model line. Details and evidence:
  [`llm/models/Qwen3-0.6B/README.md`](../llm/models/Qwen3-0.6B/README.md)「板上首次运行与退化问题」.
- **Qwen2-0.5B** (`llm/models/Qwen2_0.5B_zg3.36.1_axi_demo/board/npuserv_adapter.py`) drives the **vendor script
  itself** (`qwen2_infer_decode_only_axi_pad3.py` at the model root = vendor script + three dependency
  drops + `token_dynamic_fm` widened to 12 B, 台账 #196) instead of re-implementing its
  loops: prefill goes through `process_prompt_character_by_character` (tdf `[1, i+1, 0]`,
  entry written at out`[i+1]` into slot `i`), decode through `run_decode` (tdf
  `[1, prompt_len+step-1, 0]`, cache handed back un-shifted). No `pos_rule` arm here —
  the vendored sequence is the one that answered on 113. This is the only 件 of the three
  with a **measured** text path: 132.8 s apply, 20 tokens in 6.9 s, `bye` releasing the
  NPU (台账 §卅八 第四节). The adapter itself had not been through npuserv before
  2026-11-06; its first `/load` is the wiring proof, and `tests/q2_adapter_stub_check.py`
  covers the shape of the calls on a PC.

### Where the per-model difference is written down

Four places, and they are deliberately not the same kind of record:

| 记的是什么 | 在哪 |
|---|---|
| 机器读的差分：能力门（`ids_in`／`text_in`／`text_out`）、`defaults`、`env`、`compiled_with`（编这颗件的 iCraft 版本）、件在板上的绝对路径 | `models.json` 那一条 entry，运行时由 `GET /models` 原样回答（含 `adapter_exists`／`artifacts_present`） |
| 写代码时的差分：那颗件的非 negotiable 喂法、位置口径、为什么拒 text | 各自 `board/npuserv_adapter.py` 的文件头 |
| 人读的差分：上面两条 bullets | 本节 |
| **实测**差分：每步耗时、KV 上行字节、内存与 CMA 谷底、窗口裁剪对照 | **不在这里**——正本＝[[LLM上板要求与候选模型对照]] §6.6 与 [[板端我方代码与产物台账]] §21.1（三颗横表）、窄读与 KV 常驻两篇实测页；规矩是一个事实只写一处，所以这张表只指路、不抄数 |

颗与颗在 API 层的差分只有一条：`/load` 的 `params` 命名空间不同（ERNIE 认 `pos_offset`／
`padfix`／`max_slots`／`max_new_cap`，Qwen3 认 `pos_rule`／`driver_dir`／`config_path`），
端点、事件、槽位规则两边完全一样。

**API 面看不见的两条差分**（"换个新件"真正会咬人的地方，registry 只能声明、不能判定）：

1. **`text_in` 背后是两套依赖。** ernie 自带板侧纯 stdlib 分词（`e41_tok.py`＋`pack`，板上
   load/encode 实测过）；qwen3 的 text 路径赌的是板端 `tokenizers` 能不能读那颗
   `tokenizer.json`（从未执行，#210 vs #220）。同一个 `true`，成本完全不同。
2. **`compiled_with` 是声明，不是检查。** 板的 runtime 必须对得上（厂商预编译那颗在
   3.36.1／3.39.0 runtime 上都 `symbol lookup error`），而 npuserv 里没有任何一行代码比过它——
   版本不匹配的表现是 worker 起不来、`/load` 回 500，归因读不出来。板上现场：`pip3 list`
   有 `icraft 3.39.0`，`import icraft.xrt`／`icraft.zg330backend` 都过（11-05 现场复核）。

## Board layout

The registry carries `"root": ".."`, so `adapter` paths resolve against `lang/` — the tree
on 113 has to mirror the repo:

```
<base>/npuserv/{server.py,adapter_lib.py,wire.py,models.json,tests/}
<base>/llm/models/ERNIE-4.5-0.3B-PT4icraft430tai/board/{e41_chatd.py,e41_tok.py,e41_tok.pack,npuserv_adapter.py}
<base>/llm/models/Qwen3-0.6B/board/npuserv_adapter.py
<base>/llm/models/Qwen3-0.6B/patch/q3_infer_board.py
<base>/llm/models/Qwen2_0.5B_zg3.36.1_axi_demo/board/npuserv_adapter.py
<base>/llm/models/Qwen2_0.5B_zg3.36.1_axi_demo/qwen2_infer_decode_only_axi_pad3.py   (driver; board copy sits at <base>/qwen2)
```

`adapter` 是**相对** `root` 的，所以这一条在 PC 和板上都得成立（PC 那边落在 submodule checkout
上）；`artifacts` 和 `env` 里的路径是**板上绝对路径**，只有 server 跑在 113 时才有意义。
两种约定混在同一份文件里，这就是为什么 `tests/lifecycle_check.py` 现在逐条 assert
`adapter_exists`——`llm → llm/models` 那次子模块搬家把两条真件的路径都指空了，而行为测试
71 项照旧全绿（只有 `mock` 那条真解析过），`/load` 会 404。

板上现状**不满足**这个形状（11-05 现场只读复核）：`/root/npuserv/` 是 `<base>`=/root，
但 ERNIE 落在 `/root/ernie/board/`（json＋raw 在 `/root/ernie/jr_path/decode/`），Qwen3 落在
`/root/llm/Qwen3-0.6B/board/`——没有 `models/` 这一层。满足契约的最小动作是两条软链，不动任何
人的文件：

```sh
mkdir -p /root/llm/models
ln -s /root/ernie           /root/llm/models/ERNIE-4.5-0.3B-PT4icraft430tai
ln -s /root/llm/Qwen3-0.6B  /root/llm/models/Qwen3-0.6B
ln -s /root/qwen2           /root/llm/models/Qwen2_0.5B_zg3.36.1_axi_demo
```

Qwen2 多一步：板上它的件摆在 `/root/qwen2/` 顶上（daemon、驱动、`jr_path/`、`config/`、
`modelzoo_utils/` 全在同一层），契约要的是 `…/Qwen2_0.5B_zg3.36.1_axi_demo/board/npuserv_adapter.py`，所以
adapter 落在 `/root/qwen2/board/`，其余不动（目录名对齐线上仓，2026-11-06 改名）。

**这两条我没跑**（`113.txt` 是 `test==on`，那条会话 06:50 还在写相机 DMA 的现场）。另外板上那份
`/root/npuserv/models.json`（11-04 11:45）与仓里这份是**两个副本**、已经漂了（板的 artifacts 指
`/root/ernie/…`，仓里这份现在也指那里了，但 adapter 两条还是旧的）——上板前要么把仓里这份推过去
覆盖，要么就地改板上那份，别留两份各说各话。

`models.json` points the 件 at their board paths (`/root/ernie/jr_path/...` for ERNIE,
`/root/qwen3/...` for Qwen3 — those roots are where each session left them, not a choice
made here). Run `python3 server.py` from `<base>/npuserv`.

Before a first load on the board, read the gate rather than assume it: `CmaTotal` is
262,144 kB of which 128 MiB is taken at boot by `udmabuf0`, so a loaded model gets ≈130 MB
of CMA and **must not share a window with the live video chain** (台账 §册三, [[NPU调用链]]
§7.4). The API is enough to check that gate: `GET /status` carries the `/proc/meminfo`
readings at all times, and `POST /unload` returns `mem_before` / `mem_after` around the kill.

## Tests

```sh
python -X utf8 lang/npuserv/tests/lifecycle_check.py       # 40 checks, mock engine, no NPU
python -X utf8 lang/npuserv/tests/adapter_stub_check.py    # 12 checks, real ERNIE adapter + stub e41_chatd
python -X utf8 lang/npuserv/tests/q3_adapter_stub_check.py # 22 checks, real Qwen3 adapter + stub driver
python -X utf8 lang/npuserv/tests/q2_adapter_stub_check.py # 19 checks, real Qwen2 adapter + stub driver
```

`lifecycle_check.py` 里有两条现在是**布局契约**的守门人（2026-11-06 加）：每一条 entry 的
`adapter` 都必须在当前这台机上解析得到、每条都得带 `compiled_with`。它们是在
`llm → llm/models` 搬家把两条真件指空之后补的——当时 71 项行为检查全绿，因为它们只驱动 `mock`。

The two stub runs exist because the adapters' whole job is translation, and translation is
testable without a board: the fakes expose the real module's attribute names, record what
they were called with, and the checks assert positions, cache contiguity, the 12 B tdf, the
event stream and the failure paths. They do not cover any arithmetic — that stays 台账 读数.

Written tests caught two real defects on 2026-11-04: `adapter_lib._select` unpacked three
values from a call that returns one (every connect to a ready worker reset the socket), and
`adapter_lib` overwrote `engine.n_steps` unconditionally (a model owning a read-only counter
could not load).
