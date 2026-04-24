"""
Numpy 2.x ↔ 1.x 호환성 shim.

numpy 2.x에서 저장(pickle)된 모델을 numpy 1.x로 로드할 때
'No module named numpy._core.numeric' 에러 방지.

numpy 2.x는 내부 모듈 경로가 numpy._core.* 로 변경됐지만,
numpy 1.x는 numpy.core.* 를 사용함.
stable-baselines3 PPO 모델 등이 numpy 2.x 환경에서 pickle됐다면
numpy 1.x에서 로드할 때 경로 불일치로 실패함.

이 shim은 sys.modules에 numpy._core 경로를 numpy.core로 alias하여
pickle 로드 시 자동으로 올바른 모듈을 찾도록 함.

사용: main.py 최상단에서 import만 하면 됨.
"""
import sys
import numpy


def _install_numpy_compat_shim():
    """numpy 2.x pickle 경로를 numpy 1.x로 alias."""
    if hasattr(numpy, "_core"):
        return  # 이미 numpy 2.x이므로 shim 불필요

    # numpy 1.x에서 numpy._core.* 를 numpy.core.* 로 매핑
    mapping = {
        "numpy._core":             numpy.core,
        "numpy._core.numeric":     numpy.core.numeric,
        "numpy._core.multiarray":  numpy.core.multiarray,
        "numpy._core._multiarray_umath": numpy.core._multiarray_umath,
        "numpy._core.umath":       numpy.core.umath,
    }

    for new_path, mod in mapping.items():
        sys.modules.setdefault(new_path, mod)


_install_numpy_compat_shim()
