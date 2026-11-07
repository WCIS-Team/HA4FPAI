"""Bootstrap for the ZG330 resident worker: openDevice exactly once."""


def bootstrap():
    import sys
    for p in ('/root/icraft39', '/root/qwen2/modelzoo_utils'):
        if p not in sys.path:
            sys.path.insert(0, p)
    from pyrtutils.icraft_utils import openDevice
    return openDevice('zg330', '192.168.125.143', True, False)


def load_one(params, device):
    """Import the requested adapter and build its engine on the shared device.

    params must carry 'adapter_module' (absolute .py path). The adapter's
    load(params, device=<shared>) is preferred; legacy load(params) is accepted
    but opens its own device and must be the only session (not recommended in
    resident mode).
    """
    import importlib.util
    import sys
    path = params['adapter_module']
    here = __import__('os').path.dirname(os.path.abspath(path))
    if here not in sys.path:
        sys.path.insert(0, here)
    spec = importlib.util.spec_from_file_location('npuserv_adapter_' + str(abs(hash(path))), path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['npuserv_adapter_' + str(abs(hash(path)))] = mod
    spec.loader.exec_module(mod)
    try:
        return mod.load(params, device=device)
    except TypeError:
        return mod.load(params)
