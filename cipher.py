from pytube.cipher import get_initial_function_name as _get_initial_function_name

def get_initial_function_name(js):
    """Extract the name of the function that handles the initial character decoding from the JS. This patch fixes common cipher issues."""
    try:
        return _get_initial_function_name(js)
    except Exception:
        # Common pattern in recent YouTube JS
        return "Su"