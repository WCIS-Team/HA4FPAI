$ErrorActionPreference='Continue'
$ProgressPreference='SilentlyContinue'
$env:UV_HTTP_TIMEOUT='120'
$env:PIP_EXISTS_ACTION='i'
$root='D:\yzd\WCIS\MOD\qwen3'
$uv='D:\yzd\asr\onnx\tools\uv.exe'
New-Item -ItemType Directory -Force -Path $root | Out-Null
Set-Location $root
"STEP venv"
& $uv venv --python '3.12' "$root\venv" 2>&1 | ForEach-Object { "V " + $_ }
$py = "$root\venv\Scripts\python.exe"
& $py -V 2>&1 | ForEach-Object { "VER " + $_ }
"STEP install torch cpu + transformers + onnx + ort"
& $uv pip install --python $py --index-url https://pypi.org/simple/ --extra-index-url https://download.pytorch.org/whl/cpu torch torchaudio transformers sentencepiece protobuf onnx onnxruntime "numpy<2" accelerate safetensors huggingface_hub modelscope 2>&1 | Select-Object -Last 30 | ForEach-Object { "I " + $_ }
"STEP versions"
& $py -c "import torch,transformers,onnx,onnxruntime,numpy;print('torch',torch.__version__,'cuda',torch.version.cuda);print('tf',transformers.__version__);print('onnx',onnx.__version__);print('ort',onnxruntime.__version__);print('np',numpy.__version__)" 2>&1 | ForEach-Object { "VER2 " + $_ }
"STEP genai"
& $uv pip install --python $py --index-url https://pypi.org/simple/ onnxruntime-genai 2>&1 | Select-Object -Last 25 | ForEach-Object { "G " + $_ }
& $py -c "import onnxruntime_genai as g;print('genai',g.__version__)" 2>&1 | ForEach-Object { "GV " + $_ }
"STEP genai qwen mentions"
& $py -c "import inspect,re,onnxruntime_genai.models.convert_hf_to_onnx as c;src=inspect.getsource(c);print('LEN',len(src));print('QWEN',sorted(set(re.findall(r'[Qq]wen[ A-Za-z0-9_]{0,20}',src))))" 2>&1 | ForEach-Object { "GL " + $_ }
"STEP convert help"
& $py -m onnxruntime_genai.models.convert_hf_to_onnx --help 2>&1 | ForEach-Object { "GH " + $_ }
"DONE_SETUP"
